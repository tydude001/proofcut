"""The `proofcut mcp` server — MCP tools over stdio, or over HTTP.

Every tool here is a thin wrapper over `proofcut.ops`, and every one has a
matching `proofcut` CLI subcommand (CLAUDE.md). Tool bodies stay trivial on
purpose: logic that lives here is logic the CLI cannot reach and the stdio
tests cannot isolate.

Note the SDK is v2 — `MCPServer` from `mcp.server`. There is no `FastMCP` and
no `mcp.server.fastmcp` module, whatever your priors say.

stdio is the default transport and every existing client spawns the server
that way; HTTP is opt-in (`proofcut mcp --transport http`, docs/plans/DAYDREAM.md § MCP
over HTTP) for the day something needs to drive an already-running project
from outside. Its guard mirrors `webui.py`'s discipline exactly — see
`_LoopbackGuard` and `_serve_http` below — because an HTTP MCP server carries
the same edit-mutating tools stdio does, reachable from anywhere that can
route to the port.
"""

from __future__ import annotations

import atexit
import base64
import functools
import inspect
import os
import re
import signal
import socket
import sys
import threading
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, TypeVar, get_args, get_origin, get_type_hints

import anyio.from_thread
import anyio.lowlevel
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import Icon, ToolAnnotations
from pydantic import Field
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from proofcut import __version__, asr, briefs, energy, ops, progress, projectlock, webui
from proofcut.project import MANIFEST_NAME, ProjectError, refusing_path_too_long

#: The one text about proofcut a client loads before it decides which tool to
#: search for — Claude Code defers every tool definition and keeps only names
#: and this. So it is a map rather than a manual: what proofcut is, which tool
#: family each phase of a film lives in, and the invariants an agent breaks
#: silently without. The client truncates it past 2 KB and says nothing, so a
#: test holds it under `INSTRUCTIONS_CAP` on the wire. docs/plans/MCP.md § Step 2.
INSTRUCTIONS = (
    "proofcut edits video from a project directory on disk, addressed by the "
    "words spoken in it. Everything is local; nothing is uploaded, and it never "
    "generates footage.\n\n"
    "Start any project with timeline_status, finish_report and list_media.\n\n"
    "Phases, and the tools to search for in each:\n"
    "- footage in: init, import_media, list_media, footage_sheet, synopsis, events\n"
    "- transcript: transcribe or attach_transcript; get_transcript with search=; "
    "hear (what the source audio says between two times)\n"
    "- cut: seed_timeline, then cut_by_transcript / cut_by_time, restore, locate, retime_add\n"
    "- picture: cue_add (b-roll under a line), broll_brief, shot_sheet, canvas, "
    "reframe, reframe_sheet, inset_add (a clip inside the recording), follow "
    "(a second recording after the first, dissolved)\n"
    "- sound: music (the bed), hold_add, vo_extend, vo_synth, sound_add (one-shots at events)\n"
    "- cards and ends: card_templates, card_new, overlay_add (type over the film), head, tail\n"
    "- finish: add_captions, caption_style, lexicon_add (caption spelling), caption_span_add (captions off or a big word), export "
    "(render with export_format=null)\n"
    "- checks: check_frames, verify, film_check, finish_check; changes (what the last edits did)\n\n"
    "Rules nothing will warn you about:\n"
    "- Word indices address the ORIGINAL recording and never renumber, so a range "
    "stays valid across cuts. Prefer phrase= over a hand-typed index; "
    "resolve_phrase shows a resolution without writing.\n"
    "- Mutating tools take plan=; use it before a write you are unsure of. Every "
    "mutation is snapshotted, and undo rolls one back.\n"
    "- Sheets return the image in the reply; look at them before choosing footage "
    "or approving framing.\n"
    "- export does not burn captions, and a render existing is not a render being "
    "right: run check_frames and verify after every export.\n"
    "- When verify hears words the timeline does not have, whisper hid a retake "
    "inside one word's duration: the transcript looks right and the audio is not. "
    "hear that source span, then cut by time.\n"
)

#: Where Claude Code silently cuts both `instructions` and a tool description.
#: Bytes, measured on the wire — an em-dash is three.
INSTRUCTIONS_CAP = 2048
DESCRIPTION_CAP = 2048

#: What Claude Code will put in context from one tool reply: 25,000 tokens,
#: past which it writes the reply to a file and hands the model a path — which
#: the agent panel, with no `Read`, cannot open. Held in bytes of the reply's
#: pretty-printed text at a conservative three bytes a token, because that text
#: is what the SDK sends. A reply that grows with the film has a default window
#: sized well under it (the defaults below, measured on the 336s film: a
#: transcript word is ~100 bytes, a timeline word ~320, a caption cue ~1,170).
#: docs/plans/MCP.md § Step 5.
REPLY_CAP_BYTES = 25_000 * 3
TRANSCRIPT_WORDS = 300
VIEW_WORDS = 100
CAPTION_CUES = 30

#: `timeline_view`'s lanes (segments, seams, shots) are not windowed — each is
#: the whole edit or it is wrong — and on the film they are ~60 KB of text
#: before a single word. `_meta["anthropic/maxResultSizeChars"]` raises the
#: client's threshold for that one tool rather than trimming what it must
#: return; 500,000 is the client's own ceiling.
MAX_RESULT_META = "anthropic/maxResultSizeChars"
VIEW_RESULT_CHARS = 200_000

#: What `initialize` says about the server, beside `name` and `version`. The
#: same strings `server.json` gives the registry, restated rather than read,
#: because `server.json` is not in the wheel — `tests/test_server_stdio.py`
#: reads both back and holds them together, `test_version.py`'s discipline.
#: docs/plans/MCP.md § Step 9.
TITLE = "proofcut"
DESCRIPTION = (
    "Local-first AI video editor: recordings to a finished film, cut by transcript, "
    "then verified"
)
WEBSITE_URL = "https://github.com/tydude001/proofcut/blob/main/docs/DEMO.md"


def _icons() -> list[Icon]:
    """The web UI's favicon as the server's icon, inline — a client showing a
    server list has no reason to be able to reach anything else. The SVG's
    comment is for whoever edits the file and is stripped from the wire."""
    svg = (resources.files("proofcut") / "web" / "favicon.svg").read_text(encoding="utf-8")
    svg = re.sub(r"\s*<!--.*?-->", "", svg, flags=re.DOTALL)
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return [Icon(src=f"data:image/svg+xml;base64,{data}", mime_type="image/svg+xml", sizes=["any"])]


mcp: MCPServer = MCPServer(
    name="proofcut",
    title=TITLE,
    description=DESCRIPTION,
    website_url=WEBSITE_URL,
    icons=_icons(),
    version=__version__,
    instructions=INSTRUCTIONS,
)


#: The project this server is bound to, or None when it is unbound. Set once
#: by `serve(root=...)`, which `proofcut mcp` calls with its `-C` directory — and
#: only when `-C` was actually typed, because a globally-configured
#: `proofcut mcp` has no project and must keep reaching any of them.
_BOUND_ROOT: Path | None = None

#: How `_BOUND_ROOT` was set: `"-C"`, `"cwd"`, or None while unbound. `ping`
#: and `doctor` report it, because a server that bound itself to where it was
#: started refuses every other project, and whoever meets that refusal needs
#: to see why.
_BOUND_BY: str | None = None

#: Bind address and port `proofcut mcp --transport http` uses when neither flag
#: is given. Loopback, matching `webui.DEFAULT_HOST` (127.0.0.1): an HTTP MCP
#: server carries the same edit-mutating tools stdio does, so it gets
#: `webui.py`'s discipline (CLAUDE.md) rather than a looser default of its
#: own. The port is one past `webui.DEFAULT_PORT` for the same reason that
#: one isn't 8000/8080 — don't collide with `proofcut web` running on the same
#: project, or with whatever else a dev box already has up.
DEFAULT_HTTP_HOST = webui.DEFAULT_HOST
DEFAULT_HTTP_PORT = webui.DEFAULT_PORT + 1

#: Bind-side "any interface" addresses. These are never a client-presented
#: identity — no real client dials `0.0.0.0` or `::`, so no real `Host:`
#: header ever names one. Widening the guard's allow-list with the literal
#: `--host` string is honest for a specific address (a client that reaches
#: the server by that address naturally sends it in `Host:`) but not for a
#: wildcard bind: it would add a string only an attacker who read the
#: server's own startup banner would ever send, while doing nothing for the
#: real remote clients the wildcard bind exists to admit — they show up with
#: whatever address they actually dialed, never `0.0.0.0`/`::`. See
#: `_build_http_server`.
#: One copy, in `webui.py` beside `_LOOPBACK_NAMES` — the same refusal is
#: made by `webui.remote_policy` for `proofcut web --allow-remote`, and two
#: lists of what counts as a wildcard bind is one list that goes stale.
_WILDCARD_HOSTS = webui._WILDCARD_HOSTS


def _host_name(host_header: str) -> str:
    """Normalize a `Host:` header value to a bare, lowercased name.

    The same parse `webui.Handler._host_allowed` does — strip a trailing
    `:port`, unwrap a bracketed IPv6 literal — reimplemented rather than
    called, because this one runs against Starlette's `Headers` instead of
    `BaseHTTPRequestHandler`'s. Both guards can now be widened by an explicit
    `--allow-remote`; the difference that remains is that widening `webui`'s
    also mints a token, because that server can rewrite a whole project while
    this one is reached by a client that already had to be configured.
    """
    host = (host_header or "").strip()
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if name.startswith("[") and "]" in name:
        name = name[: name.index("]") + 1]
    return name.lower()


class _LoopbackGuard:
    """ASGI middleware: refuses any HTTP request whose Host header isn't allowed.

    `webui.py` solved exactly this problem (`Handler._host_allowed`,
    answered with `HTTPStatus.FORBIDDEN`), and CLAUDE.md is explicit that
    binding loopback is not enough by itself — a hostile page's cross-origin
    fetch, or a DNS-rebinding attempt, reaches a loopback-bound socket just
    fine, and only the Host header tells it apart from a real local client.
    The MCP HTTP surface carries the same mutating tools stdio does (every
    `@_tool()` in this module), so it gets the same two-layer guard: loopback
    bind by default (`_serve_http`) plus this middleware, implemented as real
    ASGI middleware wrapping the SDK's own Starlette app rather than left as
    a comment saying the guard belongs somewhere.

    A pure ASGI callable rather than Starlette's `BaseHTTPMiddleware`: it has
    to run in front of the SDK's own routing, including the streamable-HTTP
    session manager's `lifespan`-scoped startup, and must pass any scope type
    that isn't `"http"` straight through untouched rather than adapt it into
    a request/response pair that doesn't exist for it.
    """

    def __init__(self, app: ASGIApp, allowed_names: frozenset[str]) -> None:
        self._app = app
        self._allowed_names = allowed_names

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if _host_name(headers.get("host", "")) not in self._allowed_names:
            response = PlainTextResponse(
                "this server answers loopback requests only", status_code=403
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)

F = TypeVar("F", bound=Callable[..., Any])


_BOUND_REASON = {
    "-C": "It was started as `proofcut -C DIR mcp`. ",
    "cwd": (
        "It was started inside that project, so it bound to it; start it from "
        "outside any project to reach several. "
    ),
}


def _confine(path: str | None) -> str | None:
    """Resolve a tool's project-selector argument against the bound project.

    **Only a project selector is confined, and that is a deliberate
    boundary**: `path` is the selector, so leaving it free is what lets an
    agent panel opened on one project mutate another. The file arguments are
    not selectors and are left alone — `import_media`'s `source` reads footage
    that lives on the NAS, and `export`/`add_captions` write where the user
    asked. Confining either would break the ordinary workflow while buying
    nothing, since neither can touch a second project's state.

    `reel`'s `dest` is the one argument that is a *second* selector rather
    than a file: it names a whole project, and an unconfined one would let a
    bound panel write a project anywhere on disk. It is confined by being
    named at the registration site (`@_tool("path", "dest")`) rather than by
    a line in the body, for the reason the decorator exists.

    A relative path resolves against the bound root rather than the process
    cwd. For the agent panel the two are the same directory (`webui.py` sets
    `cwd` on the Popen), but a bound server means "this project", and that
    reading should not depend on where the client happened to be standing.
    Both sides are `resolve()`d, so `..` and a symlink out are refused rather
    than followed.

    **An absent `path` defaults to the bound project.** Every tool but
    `fonts`/`pack_show` (see `projectless=True` below) takes `path` as a
    required argument in its own signature, which under `-C` is ceremony
    with exactly one accepted value — an agent measured on the real trial
    called it on every one of 29 tool calls, having been told it never would
    need to (TRIAL.md § `path` is a required argument). `path=None` (the
    decorator's own default now) resolves to `str(root)` when bound. Unbound
    servers are unaffected in the way that matters: a caller that omits
    `path` still gets refused, now with a message naming the reason instead
    of a bare schema-validation error, since the schema itself can no longer
    make `path` required only sometimes — `test_binding_does_not_change_the_
    advertised_tool_schema` holds one schema for both states.
    """
    root = _BOUND_ROOT
    if path is None:
        if root is not None:
            return str(root)
        raise ProjectError(
            "this server is not bound to a project (no `-C` at startup, and not "
            "started inside one), so `path` is required."
        )
    if root is None:
        return path
    # `~` is expanded before the containment test, because `resolve()` alone
    # reads `~/proj` as a directory literally named `~` under the root and
    # refuses it as outside the project — a brief written with `~/…` paths
    # (so the agent pane shows no username) had every `path` refused by name.
    candidate = Path(path).expanduser()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ProjectError(
            f"this server is bound to {root} and {path!r} resolves outside it "
            f"({resolved}). {_BOUND_REASON.get(_BOUND_BY, '')}Every tool addresses "
            "that project; pass a path at or under it."
        )
    return str(resolved)


#: What each tool does to the project, as the MCP spec's hints. Nothing in
#: proofcut reads these; a client does (deciding what needs a prompt), and so do
#: the directories that grade a server's tools (Glama's "what does it do to the
#: world"). **The table is the whole contract, and `_tool()` refuses a tool
#: missing from it**, so a new tool cannot register unclassified.
#:
#: The four shapes, and the rules they were assigned by:
#:
#: - READ changes no project state. A tool that writes only a regenerable
#:   cache (`cache/thumbs`, `cache/sheets`, `cache/frames`) is still READ,
#:   because nothing a later op reads back as authored state moved.
#: - ADD only creates, and refuses rather than replaces: `init` refuses an
#:   existing project, `cue_add` a word that already has a cue, `unspoken_add`
#:   a word already marked, which is also why a repeat is idempotent: the
#:   second call refuses. **`destructive_hint=False` is claimed only where
#:   that refusal was read in the op**; an op not checked gets the spec's own
#:   default, destructive. A tool with `apply` or `plan` is classified by what
#:   it does when told to write, never by its default.
#: - SET replaces a value, and a repeat with the same arguments changes
#:   nothing further (`caption_style`, `canvas`, an `export` to one `output`).
#:   A sheet with an `out` path is SET, not READ: `out` writes a file where it
#:   is told.
#: - EDIT replaces or removes, and a repeat is not a no-op (`cut_by_time`
#:   moves under its own cut; `undo` rolls back one more; a synth re-rolls).
#: - Every tool is closed-world: proofcut runs local binaries over local files
#:   and calls no service.
_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_ADD = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_SET = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
_EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)

_ANNOTATIONS: dict[str, ToolAnnotations] = {
    **dict.fromkeys(
        [
            "ping", "doctor", "list_media", "hear", "get_transcript", "resolve_phrase",
            "transcript_checks", "describe_ls", "card_templates", "card_safe_zones",
            "pack_show", "pack_status", "cue_ls", "assets", "unspoken_ls", "build_shots",
            "locate", "timeline_status", "timeline_view", "changes", "properties", "finish_report",
            "caption_view", "lexicon_ls", "caption_span_ls", "hold_ls", "hold_check", "overlay_ls", "sound_ls", "retime_ls", "inset_ls", "finish_check", "reframe_coverage",
            "continuity_check", "continuity_ls", "thumbnail", "contact_sheet",
            "broll_brief", "verify", "check_frames", "check_black", "spot_frames",
            "speech_overlap", "review_list",
        ],
        _READ,
    ),
    **dict.fromkeys(
        [
            "init", "import_media", "cue_add", "unspoken_add", "continuity_accept",
            # Inserts a record into the stack; never replaces one.
            "overlay_add",
            # Appends a span; an overlap is decided by order, never merged.
            "caption_span_add",
            # Appends a record; never replaces one.
            "sound_add",
            # Appends a stretch; one overlapping another is refused, never merged.
            "retime_add",
            # Inserts a record into the stack; never replaces one.
            "inset_add",
            # `apply` writes a label only where none is set, and never over one.
            "attribute_speakers",
            # `apply` never writes over an existing override (CLAUDE.md).
            "reframe_detect",
            # A preview-only cache, skipped when current; never the manifest.
            "proxy_transcode",
        ],
        _ADD,
    ),
    **dict.fromkeys(
        [
            "migrate_project", "clip_role", "attach_transcript", "describe", "fonts",
            "card_new", "card_render", "card_reauthor", "pack_apply", "pack_activate",
            "pack_apply_captions", "cue_reresolve", "seed_timeline", "restore", "export",
            "add_captions", "caption_style", "canvas",
            # Replaces the entry at its key, or adds one.
            "lexicon_add", "head", "tail", "music", "reframe",
            "reframe_sheet", "shot_sheet", "footage_sheet", "synopsis", "events", "film_check",
            "import_edit", "review_verdict",
            # Replaces the dissolve at its join, or clears it.
            "dissolve",
            # Rewrites the generated WAVs with the same bytes; imports only
            # the clip ids not yet registered.
            "sound_generate",
            # Replaces the entry at its address.
            "hold_under",
            # Moved here from EDIT on 2026-09-15: it always re-reads the clip's
            # *original* media and rewrites one derived copy plus one key on the
            # clip record, so a repeat at the same `db` lands the same bytes and
            # the same record — SET's own definition, and what the tool's own
            # docstring ("repeated calls never compound gain") already said
            # while the hint beside it declared the opposite.
            "attenuate_noises",
        ],
        _SET,
    ),
    **dict.fromkeys(
        [
            "clip_rm", "transcribe", "cue_rm", "unspoken_rm", "unspoken_detect",
            "cut_by_transcript", "cut_by_time", "undo", "vo_extend", "vo_synth",
            "hold_add", "hold_rm", "hold_under_rm", "reel", "continuity_reject",
            "overlay_rm", "sound_rm", "retime_rm", "inset_rm",
            "review_add",
            # Refuses a key it lacks, so a repeat is not a no-op.
            "lexicon_rm",
            # Positions renumber, so a repeat removes the next span.
            "caption_span_rm",
            # Splices a second recording in; the timeline grows, as vo_extend's does.
            "follow",
        ],
        _EDIT,
    ),
}


#: What each argument means, hung on the advertised JSON schema as the
#: parameter's own `description`. A table beside `_ANNOTATIONS` rather than 386
#: `Annotated[...]` blocks inline, for the reason that one is a table: a
#: signature is read to see the shape of a call, and a paragraph per argument
#: buried in it hides the shape. `_tool()` applies these, and **refuses a tool
#: with an argument missing from here** the same way it refuses one missing a
#: hint row — coverage is a contract, not a housekeeping task that decays.
#:
#: Why it is worth the words: the schema alone says `string | null`, and an
#: agent picks arguments from what the definition tells it. Measured — 92
#: tools, 475 arguments, not one description — by `uvx tdqs lint` against the
#: real server, which is the offline half of the score Glama publishes.
#: HISTORY.md § The tool definitions were graded, and `path` was the gap.
#:
#: `_COMMON_PARAMS` holds only the names whose meaning is *identical*
#: everywhere they appear. `clip_id` is deliberately not among them — in a cue
#: it is the transcript the index addresses, and in `thumbnail` it is the
#: footage — and neither is `phrase`, which binds its first word on one tool
#: and its last on another. A per-tool entry always wins.
_COMMON_PARAMS: dict[str, str] = {
    "plan": (
        "Resolve the whole call and report what it would do, writing nothing. "
        "Prefer it over doing the thing and undoing it."
    ),
    "after": (
        "A forward cursor over a phrase's matches: any match at or before this "
        "word index is skipped. -1, the default, means from the start."
    ),
    "occurrence": (
        "Disambiguate a phrase by count when it matches more than once, "
        "**1-based** in transcript order among the matches after `after`: 1 is "
        "the first, 2 the second. Unset, an ambiguous phrase is refused — "
        "listing every candidate's range and text — rather than guessed at."
    ),
    "confirm_suspect": (
        "Go ahead even though a boundary word claims a suspect duration. Read "
        "the echoed words first — a suspect duration usually means whisper hid "
        "a retake inside that word, so the edge is not where it reads."
    ),
    "out": (
        "Write the image to this path as well, replacing whatever file is "
        "there. Unset, it goes to the project's own sheet cache and only the "
        "bytes come back."
    ),
    "page": "Which page of rows to draw, from 1. Unset, the first.",
    "per_page": (
        "Rows per page. `null` draws the whole project in one montage, which "
        "returns a path rather than readable bytes — for a person to open, not "
        "for an agent to read."
    ),
}

_PARAM_DOCS: dict[str, dict[str, str]] = {
    "undo": {
        "steps": (
            "How many mutations to roll back, from 1 (the last one) up to the undo "
            "depth; the same count `changes` takes. All or nothing: a number past the "
            "depth is refused, not walked as far as it goes."
        ),
        "plan": (
            "Roll nothing back; return `changes`' account of what `steps` would undo. "
            "There is no redo, so read it before a `steps` above 1."
        ),
    },
    "changes": {
        "steps": (
            "How many mutations back to compare against, from 1 (the last one — what "
            "a single undo would roll back) up to the undo depth. The reply covers "
            "everything since that point, not only the oldest step."
        ),
    },
    "init": {
        "name": (
            "A name for the project, recorded in the manifest. Unset, the directory's "
            "own name is used."
        ),
    },
    "import_media": {
        "source": (
            "The media file to register. A file argument rather than a project "
            "selector, so it is deliberately left unconfined — footage usually lives "
            "outside the project."
        ),
        "clip_id": (
            "The id every later tool addresses this clip by. Unset, one is derived from "
            "the filename. Keep it short: it becomes part of cache paths, and a stock "
            "Windows measures those against 248 characters."
        ),
        "copy": (
            "Copy the media into the project instead of referencing it where it sits. "
            "Off by default — a reference costs no disk, and it is also the fallback "
            "where symlinks are rejected."
        ),
        "mix": (
            "Sum a container's audio streams into one track, for two mics on one "
            "performance. It writes a derived copy every later op reads without knowing "
            "it."
        ),
        "audio_stream": (
            "Keep one of a container's audio streams and drop the rest, numbered from 0 "
            "in ffmpeg's own audio ordering — not the container's absolute stream "
            "index, which is a different number once there is video."
        ),
        "sheet": (
            "Draw a contact sheet of the clip's first ten seconds onto the returned "
            "record. On by default, because a first look that has to be asked for is "
            "one nobody takes."
        ),
    },
    "list_media": {
        "source_dir": (
            "The directory to list. It names where footage lives rather than which "
            "project, so it is deliberately not confined to the bound project."
        ),
        "recursive": "Walk subdirectories too. On by default.",
    },
    "clip_role": {
        "clip_id": "The clip to read or set.",
        "role": (
            "`voiceover` or `footage`. Omit it and `reset` to read what is stored. It "
            "is the assets pane's grouping and nothing else: neither "
            "transcribe/describe nor any render path reads it."
        ),
        "reset": "Clear the role back to undeclared.",
    },
    "clip_rm": {
        "clip_id": "The clip to un-register. Its media on disk is never touched.",
    },
    "attach_transcript": {
        "clip_id": (
            "The clip this transcript belongs to. Its words become `(clip_id, "
            "word_index)`, which is how every cue, mark and caption addresses them "
            "afterwards."
        ),
        "transcript_path": (
            "The whisper JSON to ingest. It has to carry word-level timings — proofcut "
            "addresses words, not segments."
        ),
    },
    "transcribe": {
        "clip_id": "The clip whose own media whisper transcribes.",
        "model": (
            "The whisper model to run, e.g. `small.en`. Larger is slower, and there is "
            "no timeout."
        ),
        "language": (
            "Force a language code, e.g. `en`. Unset, whisper detects it, which it gets "
            "wrong on short or noisy clips."
        ),
    },
    "hear": {
        "clip_id": "The clip whose source audio to listen to.",
        "start": (
            "Where to start listening, in that clip's own **source** seconds — never "
            "timeline seconds and never a word index."
        ),
        "end": (
            "Where to stop, in the same source seconds. Past the end of the clip it is "
            "refused rather than clamped."
        ),
        "model": "The whisper model for this windowed pass.",
        "language": "Force a language code, e.g. `en`. Unset, whisper detects it.",
        "window": "Length of each window, in seconds.",
        "overlap": (
            "How far each window overlaps the one before it, in seconds. The overlap is "
            "what stops a word straddling a boundary from being lost between two "
            "windows."
        ),
    },
    "get_transcript": {
        "clip_id": "The clip to read.",
        "first": "First word index to return, inclusive.",
        "last": "Last word index to return, inclusive.",
        "limit": (
            "Most words to return in one call, counted from `first`. The reply's "
            "`next_first` says where to continue. Bounded by default because a whole "
            "transcript can be past what a client will put in context."
        ),
        "search": (
            "Return each match as a word range ready to hand to `cut_by_transcript`, "
            "instead of the whole transcript. Prefer it: a transcript is a lot of words "
            "to read to find two."
        ),
    },
    "resolve_phrase": {
        "clip_id": "The transcript to resolve against.",
        "phrase": "The words to find, as they were spoken.",
        "fuzzy": (
            "Fall back to a fuzzy match when nothing matches exactly. A fuzzy hit sets "
            "`ratio` and is never reported as an exact one; `false` refuses instead."
        ),
    },
    "transcript_checks": {
        "clip_id": "One clip to re-check. Omit it for every clip that has a transcript.",
    },
    "describe_ls": {
        "clip_id": "List only this clip's windows.",
        "contains": (
            "Keep only windows whose text holds every whitespace-separated term, "
            "case-insensitively — so `\"kitchen knife\"` matches \"a knife on the "
            "kitchen counter\"."
        ),
    },
    "attribute_speakers": {
        "clip_id": (
            "The co-hosted clip: one container, one mic per speaker, one transcript "
            "already attached."
        ),
        "streams": (
            "Which audio streams the speakers are on, as ffmpeg audio ordinals (`[0, "
            "1]`). Unset, the container's readable audio streams in order."
        ),
        "labels": (
            "What to call each stream, in the same order — one per stream. Unset, "
            "`speaker1`, `speaker2`."
        ),
        "margin_db": (
            "How much louder one mic has to be to be believed, in dB. It reports a "
            "default and is not a threshold to trust: on words spoken over each other "
            "the rule is at chance, and anything under this margin comes back in "
            "`ambiguous_spans` to go and listen to."
        ),
        "apply": (
            "Write the labels onto the words. Off by default — it reports first, and "
            "applying keeps any label already on a word this refuses to call."
        ),
        "limit": (
            "How many ambiguous spans to return; the reply also says how many there are "
            "in total."
        ),
    },
    "describe": {
        "clip_id": (
            "One clip to describe. Omit it for every video clip not described yet; "
            "audio-only clips are refused, since their words are what `transcribe` "
            "indexes."
        ),
        "window": (
            "Seconds of footage per description. Do not widen it to save time: a single "
            "pass over a whole clip describes six frames as six people, fluently, with "
            "nothing saying it is wrong."
        ),
        "force": (
            "Describe clips that already have descriptions, replacing them. Without it "
            "they are skipped."
        ),
    },
    "fonts": {
        "install": (
            "Copy the vendored face where this OS's font system looks (fontconfig, "
            "CoreText or DirectWrite). Off by default, because it writes into the home "
            "directory."
        ),
    },
    "card_new": {
        "name": (
            "The `<name>` in `card:<name>` — the key a cue points at. The SVG and the "
            "PNG are both written under `assets/cards/`."
        ),
        "template": (
            "Which template to fill; `card_templates` lists them with their slots. A "
            "per-aspect variant file is resolved from the canvas, never named here."
        ),
        "slots": (
            "The template's slots filled in, as text. A newline is a line break where "
            "the template takes several lines; ratings are numbers out of five, to the "
            "nearest half."
        ),
        "width": (
            "Render width. **Leave it unset unless you mean something other than this "
            "film** — it defaults to the project's canvas, which is what stops a card "
            "pillarboxing inside the frame it was made for. Given at all, `height` must "
            "be too."
        ),
        "height": "Render height, given together with `width` or not at all.",
        "overwrite": (
            "Redraw a card of this name that already exists. Refused without it, since "
            "a cue may already point at that card."
        ),
    },
    "card_render": {
        "name": (
            "The card to rasterise: `assets/cards/<name>.svg` becomes the PNG that "
            "`card:<name>` resolves to."
        ),
        "width": (
            "Render width. The document is *drawn* at this scale rather than resampled, "
            "so text stays sharp, and it fits rather than distorts. Given together with "
            "`height` or not at all; omitted, the document renders at its own declared "
            "size."
        ),
        "height": "Render height, given together with `width` or not at all.",
    },
    "card_reauthor": {
        "name": (
            "One card to redraw, whatever its canvas. Omit it to sweep every recorded "
            "card the canvas has left behind, plus any whose files have gone missing."
        ),
    },
    "card_safe_zones": {
        "card": (
            "The card to measure, read from its already-rendered PNG rather than from "
            "the recorded slots — so the ink measured is the ink on disk."
        ),
        "platform": (
            "Whose reserved band to measure against: one of proofcut's own zones "
            "(`tiktok-organic`, `tiktok-ads`, `reels`, `shorts`, `worst-case`) or one "
            "an applied pack's active variant declares. `pack_show` lists both."
        ),
    },
    "pack_apply": {
        "pack_path": (
            "The pack file to load. An external file, never confined to the project — a "
            "pack usually lives in a separate branding repo — and nothing after this "
            "call depends on it staying reachable."
        ),
        "variant": (
            "Which resolved variant to activate. Every declared variant is snapshotted "
            "regardless, so `pack_activate` can switch later with no file re-read."
        ),
        "allow_fallback": (
            "Accept a declared CSS fallback for a font family that does not actually "
            "draw on this machine, recording which was used. Without it, a family that "
            "does not draw refuses the whole call."
        ),
        "install_fonts": (
            "Vendor the pack's own `fonts/` directory, if it ships one. Off by default, "
            "since it writes into `$HOME`."
        ),
    },
    "pack_activate": {
        "variant": (
            "Which already-snapshotted variant to switch to. No file is re-read, and an "
            "unknown name is refused by listing the ones that are available."
        ),
    },
    "pack_apply_captions": {
        "preset": (
            "Which caption preset of the active variant to apply, through the ordinary "
            "`caption_style` call. This is the only thing that restyles captions from a "
            "pack; `pack_apply` never does it on its own."
        ),
    },
    "pack_show": {
        "pack_path": (
            "Read and resolve this pack file fresh, needing no project. With `path` as "
            "well, it compares what the file says now against what the project is still "
            "running."
        ),
        "variant": "Report one variant rather than all of them.",
    },
    "cue_add": {
        "clip_id": (
            "The transcript the cue is addressed against — the VO on a voiceover "
            "project, not the footage being shown. `asset` is what gets seen."
        ),
        "word_index": (
            "The word the picture starts on, in `clip_id`'s transcript. Give this or "
            "`phrase`, not both."
        ),
        "asset": (
            "What to show from that word onward: a registered clip id, or "
            "`card:<name>` for a card. An opaque key here, resolved by `build_shots` "
            "rather than checked against disk now."
        ),
        "phrase": (
            "Address the cue by what is said instead of by index. It binds to the "
            "phrase's **first** word — \"from this word onward\"."
        ),
        "src_start": (
            "Where inside `asset` the shot reads from, in that asset's own source "
            "seconds — the number `describe_ls` reports for a window. An in-point and "
            "never a range: unpinned, the shot reads from wherever the per-asset "
            "cursor had got to, which is right for re-using a clip and wrong for "
            "showing the thing you searched for. A card takes none."
        ),
        "event": (
            "Start the picture on this event of clip_id instead of a word: `name`, or "
            "`name#k` when the name repeats — a screen recording's logged moments, "
            "for a clip with no transcript. Not with word_index or phrase."
        ),
    },
    "cue_rm": {
        "clip_id": "The transcript the cue was addressed against.",
        "word_index": "The word the cue sits on. Give this or `phrase`.",
        "phrase": (
            "Address it by wording instead; it resolves to its first word, the way "
            "`cue_add` placed it."
        ),
        "event": "The event the cue sits on, spelled as `cue_add` was given it.",
    },
    "cue_ls": {
        "clip_id": "List one clip's cues. Omit it for the whole table.",
    },
    "cue_reresolve": {
        "clip_id": "Re-resolve one clip's entries. Omit it for every clip.",
        "apply": (
            "Rewrite `word_index` wherever the stored phrase still resolves to "
            "exactly one match. Off by default: it reports first, and anything "
            "ambiguous or unresolved is reported and left untouched either way."
        ),
    },
    "unspoken_add": {
        "clip_id": "The transcript holding the word.",
        "word_index": "The word to mark. Give this or `phrase`.",
        "phrase": (
            "Address it by wording instead — but unlike `cue_add`, a phrase matching "
            "more than one word is refused rather than bound to an edge: a mark "
            "addresses exactly one word."
        ),
    },
    "unspoken_rm": {
        "clip_id": "The transcript holding the marked word.",
        "word_index": "The marked word. Give this or `phrase`.",
        "phrase": "Address it by wording instead; it has to resolve to exactly one word.",
    },
    "unspoken_detect": {
        "render": (
            "The rendered file to judge against — the witness. A word is proposed "
            "only where the timeline holds more of it over a span than the render's "
            "own transcription heard."
        ),
        "clip_id": "Limit the scan to one transcript.",
        "transcript_path": (
            "An existing transcription of `render`, which is what `verify` leaves in "
            "`cache/verify/`. It is never found automatically: a re-render under the "
            "same filename would otherwise be judged against the previous render's "
            "audio."
        ),
        "model": (
            "The whisper model to transcribe the render with, when no "
            "`transcript_path` is given."
        ),
        "language": "Force a language code for that transcription.",
        "pad": "Widen the window each candidate is counted in, in seconds.",
        "apply": (
            "Mark the proposals. Off by default, like `reframe_detect`: a wrong mark "
            "deletes a real word from every check proofcut has, so read the echoes "
            "first."
        ),
    },
    "build_shots": {
        "fps": (
            "The frame grid to project onto. Unset, the project's timebase — which on "
            "an audio-only project is milliseconds rather than frames. Pass the rate "
            "`export` will use to see the frames the export actually cuts at."
        ),
    },
    "seed_timeline": {
        "clip_id": "The clip to lay down as the timeline.",
        "remove_silences": (
            "Silence-cut the clip on the way in, through auto-editor. On by default; "
            "false lays the whole clip down untouched."
        ),
        "threshold": "auto-editor's audio loudness threshold, 0–1. Lower keeps quieter material.",
        "margin": (
            "How much to leave either side of kept audio, in auto-editor's own "
            "notation (e.g. `0.2s`), so an edge lands in the silence rather than on "
            "the breath."
        ),
        "edit_expr": (
            "auto-editor's edit language, passed straight through — e.g. `(or "
            "audio:0.03 motion:0.06)`. It replaces the threshold-based rule."
        ),
    },
    "cut_by_transcript": {
        "clip_id": "The transcript the word ranges address.",
        "cut": (
            "Inclusive word ranges to remove, e.g. `[[30, 45], [120, 131]]`. Pass "
            "exactly one of `cut` or `keep`."
        ),
        "keep": (
            "Inclusive word ranges to keep, everything else going. Pass exactly one "
            "of `cut` or `keep`."
        ),
        "pad": (
            "Widen each range on both sides, in seconds, so the cut lands in the "
            "silence between words rather than on them. `pad_reach` names any "
            "neighbour the padding eats."
        ),
        "through_pause": (
            "Extend each cut's trailing edge through the pause after its last word, "
            "wherever that gap was wide enough to draw a `[N.Ns]` marker — so cutting "
            "a phrase also takes the dead air after it. A no-op when the gap is "
            "short."
        ),
    },
    "cut_by_time": {
        "spans": (
            "Half-open `[start, end)` spans in the seconds **an export plays at** — "
            "what a person reports off a watch, not source time and not word indices. "
            "Every span resolves against the current timeline before any is applied, "
            "so a list of notes from one watch stays valid together; overlapping "
            "spans are refused rather than double-applied."
        ),
        "pad": "Widen only the outer edges of each requested span, in seconds.",
    },
    "restore": {
        "clip_id": "The clip whose cut material to bring back.",
        "ranges": (
            "Inclusive word ranges, the shape `cut_by_transcript` takes. Only the "
            "part the edit says is actually absent comes back; material still present "
            "is left alone."
        ),
        "pad": (
            "Pass the same `pad` the original cut used to bring its padding sliver "
            "back, not only the words."
        ),
    },
    "locate": {
        "clip_id": "The transcript, or the recording, the address belongs to.",
        "first": (
            "First word index, inclusive. Address it one way per call: "
            "`first`/`last`, `source_start`/`source_end`, or `phrase`."
        ),
        "last": "Last word index, inclusive. Defaults to `first`.",
        "source_start": (
            "Seconds into the original recording. Omit `source_end` to locate an "
            "instant."
        ),
        "source_end": "End of the source interval, in the recording's own seconds.",
        "phrase": (
            "Locate by wording. A phrase is naturally a range, so it resolves "
            "straight to first and last with no edge to pick."
        ),
        "event": (
            "Locate a named instant from `events`, as `name` or `name#k` (k counts "
            "that name's events from 0). Echoed with its neighbours."
        ),
    },
    "timeline_view": {
        "clip_id": (
            "The transcript whose words' fate to report. A clip that is registered "
            "but not on the edit still answers — read `off_timeline`, or every word "
            "reads `present: false` and looks cut."
        ),
        "first": "Index into the `words` list to start the window at. 0 by default.",
        "limit": (
            "Most entries of `words` to return; `words_next` says where to continue. "
            "The segments, seams and shots are always whole."
        ),
    },
    "properties": {
        "clip_id": "Add this clip's assets entry, framing windows and cue table to the report.",
        "word_index": (
            "With `clip_id`, add the cue at that word — or, when there is none, the "
            "word plus three either side. It needs `clip_id`."
        ),
    },
    "finish_report": {
        "framing": (
            "Add stale-framing numbers. Off by default because it decodes placed "
            "footage for a scene-cut scan (5.7s wall, 46s of CPU on the film, "
            "uncached, every call). Off, `framing` is null, which means *not "
            "measured* rather than nothing stale."
        ),
        "holds": (
            "Add the per-hold seam and transcription report against the last render. "
            "Off by default for the same reason: it decodes and transcribes render "
            "spans. Null when not asked for, and also null when nothing has rendered "
            "here yet."
        ),
        "continuity": (
            "Add the continuity finding counts by kind and how many are accepted. Off "
            "by default — its stub half pays the same scene-cut decode `framing` "
            "does."
        ),
    },
    "export": {
        "output": (
            "Where to write the project file or the render. A file argument, not a "
            "project selector: it writes where you say."
        ),
        "export_format": (
            "`kdenlive` (the default) writes an MLT project Kdenlive opens and melt "
            "renders. Pass null to render media instead. Other auto-editor targets — "
            "shotcut, premiere, resolve, final-cut-pro — pass straight through."
        ),
        "fps": (
            "The NLE timeline's frame rate, defaulting to the picture's own (30 for "
            "an audio-only project). It sets the render's rate too wherever proofcut "
            "owns the profile, and is ignored when auto-editor renders a "
            "single-source timeline."
        ),
        "preset": (
            "A named quality bundle — `youtube`, `web`, `tiktok-reels`, or `custom` "
            "(which needs `resolution`) — meaningful only with `export_format=null`, "
            "since an NLE project file has no bitrate. `tiktok-reels` also **checks** "
            "that the project renders 9:16 and refuses otherwise; it never sets the "
            "shape. Use `canvas` for that."
        ),
        "resolution": (
            "`[width, height]`. It **letterboxes** the existing frame on the "
            "single-source render path rather than cropping or reframing it, and is "
            "refused outright on a melt (multi-source) project."
        ),
        "loudness": (
            "Master the render to this many LUFS integrated: one gain and a true-peak "
            "limiter, measured before and after, and refused — leaving the render as "
            "it was — if the result misses by more than 1 LU. Render only."
        ),
        "true_peak": "The dBTP ceiling the loudness pass limits under. -1.0 by default.",
    },
    "add_captions": {
        "output": (
            "Where to write the `.ass` sidecar — always, burning or not. Never a video "
            "path: a media suffix is refused. The burned video goes to `burn_output`."
        ),
        "clip_id": "Caption one transcript's words rather than every clip's.",
        "preset": (
            "Override the project's base look for this one file — `clean`, `karaoke` "
            "or `boxed`. Nothing here is written back to the project."
        ),
        "max_words": "Most words in one caption cue.",
        "max_gap": "Start a new cue when the silence between two words exceeds this many seconds.",
        "max_duration": "Longest a single cue stays on screen, in seconds.",
        "hold": "How long a cue lingers after its last word, in seconds.",
        "burn": (
            "Also burn the captions into this video with ffmpeg; the sidecar is still "
            "written to `output`. It must be a render of **this** timeline — against any other "
            "video the timings will not line up. `export --render` does not burn "
            "captions, and nothing else reports a render that was made without them."
        ),
        "burn_output": (
            "Where the burned video goes, when `burn` is set. Unset, it is derived from "
            "`burn`'s own name in the project's renders folder."
        ),
    },
    "caption_view": {
        "clip_id": "Show one transcript's captions rather than every clip's.",
        "first": "Index of the first cue to return. 0 by default.",
        "limit": "Most cues to return; `cues_next` says where to continue.",
    },
    "card_templates": {
        "name": (
            "One template to return in full. The others come back as name and "
            "description only. Unset, every template in full."
        ),
    },
    "caption_span_add": {
        "clip_id": (
            "The clip whose words or events address the span — the transcript the "
            "word indices index, or the recording the events belong to."
        ),
        "word_index": "The word the span starts on. One of word_index, phrase or event.",
        "phrase": "Start on this phrase's FIRST word, resolved against clip_id's transcript.",
        "event": "Start on this event of clip_id: `name`, or `name#k` when the name repeats.",
        "until_word_index": "End as this word ends. One of until_word_index, until_phrase, until_event or seconds.",
        "until_phrase": "End as this phrase's LAST word ends.",
        "until_event": "End on this event of clip_id.",
        "seconds": "End this long after the start.",
        "off": "Draw no captions over the span. Give this or `style`.",
        "style": (
            "caption_style fields (any but `preset`) that differ over the span, on top of "
            "the project's look: {\"max_words\": 1, \"size\": 150, \"position\": \"middle\"} "
            "draws each word alone and large. Give this or `off`."
        ),
    },
    "caption_span_rm": {
        "position": "The span to remove, by its position in caption_span_ls.",
    },
    "lexicon_add": {
        "heard": (
            "The words as whisper spelled them, one or several (`rough`, `pup bnb`). "
            "Matched as whole words, case and edge punctuation aside, at every "
            "occurrence; carry a neighbouring word to narrow it to one place."
        ),
        "canonical": (
            "What to print instead (`hear`) or what the voice model is given "
            "(`say`). Written as it should appear: `PupBnB` keeps its capitals."
        ),
        "kind": (
            "`hear` (default): a caption correction, also folded out of "
            "`vo_synth`'s WER. `say`: a respelling `vo_synth` gives the voice model."
        ),
    },
    "lexicon_rm": {
        "heard": "The entry's key, matched the way `lexicon_add` matches it.",
        "kind": "`hear` (default) or `say`: which table the entry is in.",
    },
    "caption_style": {
        "preset": (
            "The base look: `clean`, `karaoke` (per-word highlight), `reveal` (words "
            "land mid-frame, fading in as spoken) or `boxed`. "
            "Everything else overrides one of its fields, and only the overrides are "
            "stored."
        ),
        "font": (
            "Family name to draw with. Whether it actually draws is a different "
            "question from whether it is installed — `fonts` measures a render, and "
            "libass substitutes silently at exit 0."
        ),
        "size": "Type size, against the project's canvas as the reference frame.",
        "text": (
            "The word's own colour: `#rrggbb`, `#rrggbbaa`, a name, or an ASS `&H…` "
            "value. It comes back resolved, because ASS quotes colours backwards and "
            "alpha-inverted."
        ),
        "highlight": "What a word turns as it is spoken. It only shows with `karaoke` on.",
        "outline_colour": "Colour of the outline around the type.",
        "box_colour": "Colour of the box behind the type, when `box` is on.",
        "bold": "Draw bold.",
        "box": (
            "Draw an opaque box behind the words. It buys legibility over light "
            "footage — white captions over the film's own light cards measure 1.10:1 "
            "without one — and costs clean edges, since libass draws one box per "
            "override block."
        ),
        "outline_width": (
            "Outline thickness. With no box this is what holds the words apart from "
            "the picture."
        ),
        "shadow": "Drop-shadow distance.",
        "position": "Where captions sit, named: `bottom`, `top`, `top-right` and so on.",
        "margin": "Distance from the frame edge, in canvas pixels.",
        "karaoke": (
            "Fill each word as it is spoken. The fill is left-to-right within a line "
            "rather than a per-word step, which is what the grouping fields below "
            "shape."
        ),
        "reveal": (
            "How each word arrives as it is spoken: `fade`, `blur` (blurs and fades "
            "in; the outline returns at the end), or `none`. The line is laid out "
            "whole from the start, so nothing moves."
        ),
        "reveal_ms": "How long a word's reveal takes, in milliseconds. Default 150. Needs a reveal.",
        "reveal_blur": (
            "How blurred a word starts under `reveal=blur` (ASS `\\blur`; a gaussian of "
            "0.85 x this in canvas pixels). Default 6."
        ),
        "max_words": (
            "Most words in one caption cue. Grouping is part of the look, which is "
            "why it is stored with it."
        ),
        "max_gap": "Start a new cue when the silence between two words exceeds this many seconds.",
        "max_duration": "Longest a single cue stays on screen, in seconds.",
        "hold": "How long a cue lingers after its last word, in seconds.",
        "reset": (
            "Drop every override first. `reset` together with `preset` starts clean "
            "from that preset."
        ),
    },
    "canvas": {
        "size": (
            "`WIDTHxHEIGHT`, e.g. `1080x1920` for a vertical reel. Both edges must be "
            "even. Omit it to read what is in force plus the footage-derived shape it "
            "would fall back to."
        ),
        "reset": "Drop the override and return to the footage-derived shape.",
    },
    "head": {
        "asset": (
            "The footage the cold open plays, as a registered clip id — never "
            "`card:name`. A cold open is real footage with real dialogue by "
            "definition, and `verify` accounts for its words rather than forbidding "
            "them."
        ),
        "src_start": (
            "Where inside that asset the cold open reads from, in source seconds. 0.0 "
            "on a first set."
        ),
        "seconds": (
            "How long the cold open runs. Setting `asset` or `seconds` for the first "
            "time needs both together; either alone afterwards updates just that "
            "field."
        ),
        "fade_in": (
            "Seconds of fade at the head. Unlike `tail`'s fade this is drawn, and it "
            "is the whole reason the feature exists — a hard butt-join between room "
            "tone and digital silence is exactly the seam a missing fade produces."
        ),
        "fade_out": "Seconds of fade where the cold open hands over to the film.",
        "gain_db": (
            "A flat level shift for the cold open, in dB, distinct from the fades. "
            "0.0 is unity."
        ),
        "reset": "Drop the cold open entirely.",
    },
    "tail": {
        "asset": (
            "The end card or bumper, as `card:name` — never a clip id. `verify` diffs "
            "the render's own transcription against the timeline's words, and a card "
            "behind silence adds none of its own, which a media clip would."
        ),
        "seconds": (
            "The tail's **whole** length, card included — not a hold with `fade` "
            "added on top of it."
        ),
        "fade": (
            "Seconds the card dissolves in over the film's last frames, opaque on the "
            "tail's first frame — overlapping the film, never added to `seconds`. 0 "
            "cuts to the card hard."
        ),
        "reset": (
            "Drop the tail entirely. Note a derivation inherits none of it anyway and "
            "reports `tail_dropped`."
        ),
    },
    "music": {
        "after": (
            "A forward cursor over the matches of `phrase_start`/`phrase_end`: any "
            "match at or before this word index is skipped. -1, the default, means "
            "from the start."
        ),
        "occurrence": (
            "Disambiguate `phrase_start`/`phrase_end` by count, **1-based**. Unset, "
            "an ambiguous phrase is refused rather than guessed at."
        ),
        "asset": (
            "The bed's own music, as a registered clip id — never `card:name`, since "
            "a held frame has no sound. It plays from its own head; shorter than its "
            "span pads with real silence, longer is trimmed."
        ),
        "clip_id": "The clip whose words (or events) the bed addresses — the VO, not the music.",
        "word_index_start": (
            "Where the bed comes in, as a word of `clip_id`. The bed stores words and "
            "never a length, so a cut before either boundary moves it automatically."
        ),
        "word_index_end": (
            "Where the bed goes out. Unset means *to the end of the edit*, so a tail "
            "holds over silence unless `over_tail`."
        ),
        "phrase_start": (
            "Set the in-point by wording instead; it binds the phrase's first word. "
            "The resolved phrase is stored beside the index, so `cue_reresolve` can "
            "re-derive it after a re-record."
        ),
        "phrase_end": (
            "Set the out-point by wording instead; it binds the phrase's last word. "
            "Each boundary is independent — one can be a phrase and the other an "
            "index."
        ),
        "fade_in": (
            "Seconds of fade at the bed's start. The fades ride the bed's own entry, "
            "so a fade-out ends where the music audibly ends."
        ),
        "fade_out": (
            "Seconds of fade at the bed's end. A fade pair the bed cannot hold "
            "refuses at build time rather than being clamped."
        ),
        "clear_end": "Drop the end word, returning the bed to running to the end of the edit.",
        "src_in": "Where inside the bed's own asset it starts, in seconds.",
        "crossfade": (
            "Seconds two pieces overlap by. A crossfade edge is equal-power rather "
            "than the straight dB line an ordinary fade draws — two straight fades "
            "crossing sum to a hole."
        ),
        "rotate": (
            "Further assets to play in turn as each one runs out, overlapping by "
            "`crossfade`. `[]` clears them."
        ),
        "passages": (
            "Replace the list of passages after the bed's own asset: each `{asset, "
            "word_index_start | phrase_start | event, src_in?, crossfade?, rotate?}`. "
            "`[]` clears them."
        ),
        "under": (
            "Level the whole bed this many LU below the voice, measured. It is a "
            "fixed offset; `duck` is the moving one."
        ),
        "clear_under": "Return every asset to its own level.",
        "loudness": (
            "Level the whole bed to this many LUFS, measured — for a film with no "
            "voice for `under` to sit below, such as a screen recording. Setting it "
            "drops `under`, and `under` drops it. The launch clip's approved bed "
            "reads -23.3."
        ),
        "clear_loudness": "Drop the `loudness` level.",
        "over_tail": (
            "True runs a bed with no end boundary on under the tail's card, so the "
            "card is not silent and `fade_out` ends with it. False ends it with the "
            "edit, the default."
        ),
        "duck": (
            "Pull the bed this many dB down while the voice is speaking and let it "
            "back up in the pauses. It is keyed off the timeline's own audio at "
            "export rather than the transcript's word timings, which were measured "
            "against a bed recovered from a real render and beaten: 2.72 dB off for "
            "the audio gate against a word-span duck's 3.39. It also hears audible "
            "insets and sounds placed with `ducks`."
        ),
        "clear_duck": "Return the bed to one level, with no ducking.",
        "event": (
            "Start the bed on this event of clip_id instead of a word: `name`, or "
            "`name#k` when the name repeats. Replaces a start word."
        ),
        "until_event": "End the bed on this event of clip_id. Replaces an end word; `clear_end` drops it.",
        "reset": "Drop the bed entirely.",
    },
    "vo_extend": {
        "clip_id": "The track the gap opens in — the VO.",
        "word_index": (
            "The last word **before** the gap; the hold opens immediately after that "
            "word's own end. It has to be on the timeline: an index naming cut "
            "material is refused rather than guessed at."
        ),
        "seconds": "How long the hold runs. An editorial call this makes no attempt to derive.",
        "phrase": (
            "Address it by wording instead. A phrase binds to its **last** word here, "
            "which is this tool's own meaning: the last word before the gap."
        ),
    },
    "vo_synth": {
        "text": (
            "What the voice says. It is respelled first through the project's "
            "`lexicon.json` `say` folds, if one exists — the fix for a mispronounced "
            "name."
        ),
        "voice": (
            "A directory holding `ref.wav` + `ref.txt`, the ≈19s reference the clone "
            "is zero-shot from. Unset, `$PROOFCUT_TTS_VOICE`. There is no built-in "
            "voice, and none ships in the repo: a voice is somebody's recorded "
            "speech."
        ),
        "candidates": (
            "How many seeds to render and rank. Seed moves a render more than the "
            "reference does, which is why this ranks rather than renders once."
        ),
        "seed": (
            "First seed of the range; seeds `seed .. seed+candidates-1` render in one "
            "process. A new range renders only what the cache lacks."
        ),
        "max_seconds": (
            "Length cap per render. One that hits it is reported `capped` and never "
            "wins while an uncapped one exists — a 21s reference once ran every "
            "render to 655s."
        ),
        "clip_id": (
            "With `word_index`, the track to splice the winner into. Omitted, nothing "
            "is spliced and the renders are just ranked."
        ),
        "word_index": (
            "The word to splice the winner in right after, through `vo_extend`'s own "
            "mechanism — so the same one-way consequences follow (melt routing, "
            "`restore` refusing across the seam)."
        ),
        "readback": (
            "Transcribe the winner with whisper and report `heard`/`wer`. On by "
            "default: a clone that sounds right and says the wrong words is the "
            "failure nothing else sees. The numbers are a report, never a gate."
        ),
        "lexicon": (
            "A `{\"say\": {…}, \"hear\": {…}}` file: `say` respells what the model is "
            "given, `hear` folds whisper's spelling back to the script's before the "
            "WER is scored. Defaults to the project's own `lexicon.json` if it has "
            "one."
        ),
        "flat_floor": (
            "Below this much voiced pitch movement (semitones) a render starts paying "
            "the flatness penalty. Likeness alone keeps the flattest read, because "
            "sims in one pool differ by thousandths while spread differs by "
            "semitones."
        ),
        "flat_weight": (
            "How much likeness to subtract per semitone of flatness under the floor. "
            "0 restores likeness-only ranking."
        ),
    },
    "hold_add": {
        "after": (
            "A forward cursor over the matches of "
            "`gap_phrase`/`cue_phrase`/`asset_phrase`: any match at or before this "
            "word index is skipped. -1, the default, means from the start."
        ),
        "occurrence": (
            "Disambiguate `gap_phrase`/`cue_phrase`/`asset_phrase` by count, "
            "**1-based**. Unset, an ambiguous phrase is refused rather than guessed "
            "at."
        ),
        "clip_id": "The VO track the gap opens in.",
        "gap_word_index": (
            "The word the gap opens right after. With `clip_id` it is the hold's "
            "address, and a second `hold_add` at the same address is refused."
        ),
        "cue_word_index": "The word the picture cue for `asset` is placed on.",
        "asset": "The film clip whose own audio plays in the gap, and whose picture the cue pins.",
        "word_index_first": (
            "First word of the line to play, in **`asset`'s own** transcript — not "
            "the VO's."
        ),
        "word_index_last": (
            "Last word of that line. With `word_index_first` it is one-way once "
            "spliced: resizing means `hold_rm` then `hold_add`, or `undo`."
        ),
        "gap_phrase": (
            "Address the gap by wording; it binds its **last** word, since the gap "
            "opens right after it."
        ),
        "cue_phrase": "Address the cue by wording; it binds its **first** word.",
        "asset_phrase": (
            "The line to play, resolved against `asset`'s **own** transcript, binding "
            "its first and last words together. One phrase is the source of truth for "
            "both ends; hand-typed indices drift the moment a transcript changes "
            "under them."
        ),
        "head_margin": (
            "Seconds kept before the line, so it does not start on the word. "
            "Re-settable on an already-spliced hold."
        ),
        "tail_margin": "Seconds kept after the line. Re-settable.",
        "under": "How far below the VO the held audio sits, in LU. Re-settable.",
        "fade_in": "Seconds of fade as the held audio comes in. Re-settable.",
        "fade_out": "Seconds of fade as it goes out. Re-settable.",
    },
    "hold_rm": {
        "clip_id": "The VO track the hold was spliced into.",
        "gap_word_index": (
            "The hold's address, with `clip_id`. The record and its owned cue go; the "
            "spliced silence stays, since there is no clean un-splice — only `undo`."
        ),
    },
    "hold_under": {
        "after": (
            "A forward cursor over the matches of `phrase_start`/`phrase_end`: any "
            "match at or before this word index is skipped. -1, the default, means "
            "from the start."
        ),
        "occurrence": (
            "Disambiguate `phrase_start`/`phrase_end` by count, **1-based**. Unset, "
            "an ambiguous phrase is refused rather than guessed at."
        ),
        "clip_id": "The VO track whose words the span is measured in.",
        "asset": (
            "The film clip whose audio plays under the voice. It has to be on screen "
            "across the span — the audio reads from wherever the shot showing it has "
            "got to — so cue it first."
        ),
        "word_index_start": (
            "First VO word of the span. With `clip_id` it is the entry's address; a "
            "second call at the same address replaces it."
        ),
        "word_index_end": "Last VO word of the span.",
        "phrase_start": "Set the span's start by wording instead.",
        "phrase_end": "Set the span's end by wording instead.",
        "under": "How far below the VO the film audio sits, in LU. 13 by default.",
        "fade_in": "Seconds of fade as the film audio comes in.",
        "fade_out": "Seconds of fade as it goes out.",
    },
    "hold_under_rm": {
        "clip_id": "The VO track the entry was addressed against.",
        "word_index_start": "The span's first VO word — the entry's address, with `clip_id`.",
    },
    "hold_check": {
        "render": (
            "The rendered file to listen to. Each hold's span is resolved live "
            "against the current edit and transcribed off this file."
        ),
    },
    "finish_check": {
        "final": (
            "The delivered file to check — one an external mix pass produced, not a "
            "proofcut render. Every position reported is in this file's own absolute "
            "seconds."
        ),
        "holds": (
            "The holds to expect in `final`, resolved and offset the same way the "
            "stored ones are. Defaults to the project's own; pass a list (an empty "
            "one included) to check against a different set."
        ),
        "prepend_seconds": (
            "How much runs before the timeline's first frame in `final` — a cold open "
            "concatenated on outside proofcut. Defaults to the project's stored head "
            "length."
        ),
        "fps": (
            "The frame grid the timeline's arithmetic is counted on. Defaults to the "
            "rate `export` would have picked."
        ),
        "duration_tolerance": (
            "How far `final`'s duration may sit from the timeline's own arithmetic "
            "before it is a fault, in seconds."
        ),
        "pix_th": "blackdetect's pixel threshold: how dark a pixel counts as black.",
        "black_min_duration": "Shortest black run to report, in seconds.",
        "windowed_model": (
            "The whisper model for the windowed transcription of `final`. A "
            "deliberately small one is the default, since the windowed pass runs over "
            "twice the audio."
        ),
        "window": "Length of each transcription window, in seconds.",
        "overlap": "How far each window overlaps the one before, in seconds.",
        "recheck_pad": (
            "How much to pad a dropped run when re-cutting it for its own "
            "transcription — the pass that separates a real miss from a false one at "
            "a window stitch."
        ),
        "language": "Force a language code for the transcription.",
        "clip_id": "Diff against one transcript's expected words rather than all of them.",
        "transcript_path": (
            "An existing transcription of `final`, to diff again without "
            "re-transcribing."
        ),
    },
    "reel": {
        "start": (
            "Where the reel begins, in the seconds **an export plays at** — the same "
            "numbers `cut_by_time` takes, read off a watch."
        ),
        "end": (
            "Where it ends, in those same render seconds. `start`/`end` name the span "
            "to **keep**, the opposite direction from every other tool here."
        ),
        "canvas": (
            "The shape to set on the derived project only, e.g. `1080x1920`. Setting "
            "it on the film instead is what deriving exists to avoid — a canvas is "
            "project state and would stay."
        ),
        "name": "A name for the derived project. Unset, it is derived from `dest`.",
    },
    "reframe": {
        "clip_id": (
            "The clip to read or frame. Omit it to read the crops in force for every "
            "clip, including how much of each is kept."
        ),
        "rect": (
            "`X,Y,W,H` in that clip's **own source pixels** — the region kept. An "
            "override is a floor rather than a frame: a rect that is not the canvas's "
            "shape is grown to it, so nothing named is pushed off screen, and the "
            "reply gives both `asked` and the `crop` it became."
        ),
        "pane": (
            "A second rect making this window a **stacked split**: `rect` on top, "
            "`pane` below, each about twice the width one 9:16 window gets. For the "
            "shot one window cannot frame. Both are grown to the full source height — "
            "nothing masks a pane, so a shorter crop scales into the other half at "
            "exit 0."
        ),
        "src_start": (
            "Frame a **shot** rather than a clip: seconds into that clip's own "
            "source, the rect holding from there until the next window. Because the "
            "address is the source's own clock, a clip used seven times picks up the "
            "right window at each placement. Omitted, it is the window from the head "
            "of the file."
        ),
        "ease": (
            "The curve of this window's slide: linear, ease (slow at both ends), "
            "ease-in or ease-out. Implies `interp`. The slide runs across the whole "
            "previous window, so to move between two moments put a window holding "
            "the old rect at the first."
        ),
        "event": (
            "Start this window at a named instant from `events` (`name` or "
            "`name#k`) instead of `src_start`. The seconds it resolves to are "
            "stored; the listing says `event_moved` if the event later moves."
        ),
        "interp": (
            "Slide into this window from whatever governed before it instead of "
            "stepping to it. It needs `src_start` past 0 — there is nothing before "
            "the head of the source to slide from — and cannot be combined with "
            "`pane`."
        ),
        "fill": (
            "`blur` draws this window **blur-filled**: the whole source contained in "
            "the frame, over a blurred, darkened copy of the same moment covering the "
            "canvas. For a shot every crop loses something from and no split divides. "
            "Takes no `rect`, `pane` or `interp`; set `src_start` for one shot."
        ),
        "reset": (
            "With `clip_id`, drop that clip's overrides; with `src_start` as well, "
            "only the window there. Alone, drop every override."
        ),
    },
    "reframe_detect": {
        "clip_id": "Propose windows for one clip. Omit it for every placed clip.",
        "threshold": (
            "How strong a scene change has to be to count as a camera cut, 0–1. 0.15 "
            "is pinned by judging detections on real footage: every candidate from "
            "0.141 to 0.244 was a real cut, and the first non-cut is 0.137."
        ),
        "frames": (
            "How many moments to sample inside each window before centring it on the "
            "faces found there."
        ),
        "apply": (
            "Write the proposals through `reframe`. Off by default — the opposite of "
            "`cut --plan` — because the pass runs 24% of a window's width out on "
            "average. Call `reframe_sheet` and look first. It never writes over a "
            "window that is already an override."
        ),
        "split": (
            "Offer a stacked split where every sampled frame holds two or three faces "
            "one window cannot hold. On by default; `false` turns the offer off."
        ),
    },
    "reframe_coverage": {
        "clip_id": "Walk one clip's placements. Omit it for the whole project.",
        "threshold": (
            "How strong a scene change has to be to **demand** a window. Boundaries "
            "are scored against every detected cut rather than only these, since a "
            "cut too weak to demand a window still explains one."
        ),
    },
    "continuity_check": {
        "gap": (
            "How much timeline may pass before re-showing an asset reads as a replay "
            "rather than a rewind, in seconds."
        ),
        "min_shot": (
            "Shortest a shot may run before it is reported as a short shot, in "
            "seconds. Stills are excluded."
        ),
        "stub_tolerance": (
            "How close a shot edge has to sit to its footage's own internal cut to be "
            "called a stub, in seconds."
        ),
        "stubs": (
            "Look for stubs. On by default, and it costs a scene-cut decode per "
            "distinct asset placed — `false` skips that."
        ),
        "scene_threshold": (
            "The scene-cut threshold for the stub scan. It defaults to the pinned "
            "0.15, but darker footage from a different film has needed 0.12."
        ),
    },
    "continuity_accept": {
        "clip_id": "The cue's own addressing transcript, as `continuity_check` reports it.",
        "word_index": (
            "The cue's word. With `clip_id` and `kind` it is the finding's address — "
            "one shot can carry more than one finding."
        ),
        "kind": "Which finding to acknowledge: `rewind`, `replay`, `short_shot` or `stub`.",
    },
    "continuity_reject": {
        "clip_id": "The cue's own addressing transcript.",
        "word_index": (
            "The cue's word, with `clip_id` and `kind` the address the "
            "acknowledgement was stored under."
        ),
        "kind": "Which finding to un-acknowledge: `rewind`, `replay`, `short_shot` or `stub`.",
    },
    "reframe_sheet": {
        "moments": (
            "Fractions of each window to draw tiles at, e.g. `[0.1, 0.5, 0.9]`. A "
            "tile is evidence about one instant while a rect is a claim about a "
            "stretch, so where the subject moves these decide what the sheet can see. "
            "Refused alongside `extremes`."
        ),
        "extremes": (
            "Draw the subject's own leftmost and rightmost moments, worst first, "
            "instead of fixed fractions — the rect does not move inside a stretch, so "
            "that is where a static window is worst. Off by default: it costs the "
            "face detector and about half a second a probe. Read `worst_offset` "
            "beside `multi_face`, never after it."
        ),
    },
    "footage_sheet": {
        "clip_id": (
            "The registered clip to browse. This sheet reads the clip's **own "
            "source**, so it needs no edit, no cues and no transcript."
        ),
        "mode": (
            "Which instants to draw: `auto` (the default) uses the clip's described "
            "windows if it has any and the interval otherwise, and never scans; "
            "`interval` draws every `interval` seconds; `describe` draws one tile per "
            "described window, beside its text; `scenes` draws one per detected cut. "
            "Scenes is opt-in because its yield is "
            "uncorrelated with anything the caller knows — 0 cuts on a 29s b-roll "
            "loop, 17 in 60s of gameplay — and it decodes the whole clip."
        ),
        "interval": (
            "Seconds between tiles when drawing by interval (`interval`, or `auto` on a "
            "clip with no descriptions). It is `describe`'s own window "
            "length, so a tile lines up with a description."
        ),
    },
    "thumbnail": {
        "clip_id": "The clip to pull a frame from.",
        "at": (
            "Source seconds to pull the frame at. It snaps to a multiple of "
            "`interval` first, so a repeated ask for a nearby instant is a cache hit."
        ),
        "interval": "The grid `at` snaps to, in seconds.",
    },
    "contact_sheet": {
        "clip_id": "The clip whose head to look at.",
        "seconds": (
            "How much of the head to cover, in seconds. Ten by default — long enough "
            "to catch credits, black or a slate before anything is cued to the clip."
        ),
        "interval": "Seconds between tiles.",
    },
    "synopsis": {
        "clip_id": (
            "The clip to read or write. Omit it to list every clip's synopsis and "
            "which are missing one."
        ),
        "text": (
            "What this footage **is** — the work, the scene, the people. A different "
            "fact from a `describe` window, which says what is in front of the "
            "camera. Write it yourself: nothing generates one, because a model "
            "reading the pixels measurably cannot."
        ),
        "clear": "Remove this clip's synopsis.",
    },
    "events": {
        "clip_id": (
            "The clip whose events to read or write. Omit it to count every clip's."
        ),
        "source": (
            "A recorder's event file to import: a JSON object of name → seconds (or → "
            "a list of seconds), or a bare list of seconds with `name`. Replaces this "
            "clip's events of the names the file brings; other names are kept."
        ),
        "name": (
            "With `at`, the event to add. With a bare-list `source`, what those "
            "times are. One token, no '#'."
        ),
        "origin": (
            "A key in the imported object holding the recording's zero, subtracted "
            "from every time — a recorder's clock is usually the wall clock."
        ),
        "offset": "Seconds subtracted from every imported time, after `origin`.",
        "at": "Seconds into the recording for the one event being added.",
        "event": (
            "Resolve one address — `name`, or `name#k` when the name repeats — and "
            "echo it with its neighbours, writing nothing."
        ),
        "clear": "Remove every event on this clip.",
    },
    "overlay_add": {
        "card": (
            "The overlay card to place: one made by card_new from an overlay template "
            "(`lowerthird`, `scrim`). An ordinary card is opaque and is refused."
        ),
        "clip_id": (
            "The clip whose words or events address the span — the transcript the "
            "word indices index, or the recording the events belong to."
        ),
        "word_index": "The word the overlay starts on. One of word_index, phrase or event.",
        "phrase": "Start on this phrase's FIRST word, resolved against clip_id's transcript.",
        "event": "Start on this event of clip_id: `name`, or `name#k` when the name repeats.",
        "until_word_index": (
            "End as this word ends. One of until_word_index, until_phrase, until_event or seconds."
        ),
        "until_phrase": "End as this phrase's LAST word ends.",
        "until_event": "End on this event of clip_id.",
        "seconds": (
            "End this long after the start. A length, so a cut inside the span does not shorten it."
        ),
        "enter": "How it appears: `rise` (moves up while fading in), `fade`, or `none` (a cut). Default rise.",
        "enter_seconds": "How long the entrance takes. Default 0.45.",
        "enter_ease": "The entrance's curve: linear, ease, ease-in or ease-out. Default ease-out.",
        "leave": "How it goes: `fade`, `rise` (moves down while fading out), or `none`. Default fade.",
        "leave_seconds": "How long the exit takes. Default 0.3.",
        "leave_ease": "The exit's curve: linear, ease, ease-in or ease-out. Default ease-in.",
        "position": (
            "Where in the stack it goes: 0 is the bottom, omitted is the top. A later "
            "overlay draws over an earlier one it overlaps, so a scrim goes before its type."
        ),
    },
    "overlay_rm": {
        "position": "The overlay to remove, by its position in overlay_ls (0 is the bottom).",
    },
    "retime_add": {
        "clip_id": (
            "The clip whose words or events address the span — the transcript the "
            "word indices index, or the recording the events belong to."
        ),
        "seconds": (
            "How long the span plays for in the render. Shorter than the span speeds it "
            "up (a 31 s wait in 1.0), longer slows it down."
        ),
        "word_index": "The word the stretch starts on. One of word_index, phrase or event.",
        "phrase": "Start on this phrase's FIRST word, resolved against clip_id's transcript.",
        "event": "Start on this event of clip_id: `name`, or `name#k` when the name repeats.",
        "until_word_index": "End as this word ends. One of until_word_index, until_phrase or until_event.",
        "until_phrase": "End as this phrase's LAST word ends.",
        "until_event": "End on this event of clip_id.",
    },
    "inset_add": {
        "clip_id": (
            "The recording the inset is drawn into — its camera (reframe windows) is what "
            "the inset follows, and its words or events address the span."
        ),
        "asset": "The clip to draw, by clip_id: the render an agent made, in a launch clip. Needs picture.",
        "rect": (
            "[x0, y0, x1, y1] in the recording's OWN pixels: where the recording shows the thing "
            "the inset replaces (a preview pane). Must be the asset's shape, within 1%."
        ),
        "word_index": "The word the inset starts on. One of word_index, phrase or event.",
        "phrase": "Start on this phrase's FIRST word, resolved against clip_id's transcript.",
        "event": (
            "Start on this event of clip_id: `name`, or `name#k` when the name repeats. Usually "
            "the moment the recording's own preview starts playing, which locks the two."
        ),
        "until_word_index": (
            "End as this word ends. At most one of until_word_index, until_phrase, until_event "
            "or seconds; none runs to the asset's end."
        ),
        "until_phrase": "End as this phrase's LAST word ends.",
        "until_event": "End on this event of clip_id.",
        "seconds": "End this long after the start.",
        "src_in": "Seconds into the asset the inset starts from. Default 0.",
        "enter": "How it appears: `fade` or `none` (a cut). Default fade.",
        "enter_seconds": "How long the fade in takes. Default 0.4.",
        "enter_ease": "The fade's curve: linear, ease, ease-in or ease-out. Default ease.",
        "leave": "How it goes: `fade` or `none`. Default fade.",
        "leave_seconds": "How long the fade out takes. Default 0.4.",
        "leave_ease": "The fade's curve: linear, ease, ease-in or ease-out. Default ease.",
        "dim": "Darken the recording around the inset, 0 (none, the default) to 1 (black); 0.55 reads well.",
        "gain_db": (
            "The asset's own audio level in dB. Default 0. The music bed goes out under it, or dips "
            "under it when the bed has a duck."
        ),
        "mute": "Play none of the asset's audio (and leave the bed alone).",
        "position": "Where in the stack it goes: 0 is the bottom, omitted is the top.",
        "level": (
            "'speech' measures the span the inset plays, once, and records the gain_db that brings "
            "its speech to -18 dBFS RMS, the launch clip's film level. Not with gain_db."
        ),
    },
    "inset_rm": {
        "position": "The inset to remove, by its position in inset_ls.",
    },
    "follow": {
        "clip_id": "The incoming recording, a registered clip with picture.",
        "after": "The clip on the timeline it follows — the first recording.",
        "at_event": (
            "Splice in after this event of `after` (name or name#k). Omitted: after the last of "
            "`after` the timeline plays."
        ),
        "src_start": "Seconds into clip_id where it starts. Default its head. Not with from_event.",
        "src_end": "Seconds into clip_id where it ends. Default its end. Not with until_event.",
        "from_event": "Start at this event of clip_id instead of src_start.",
        "until_event": "End at this event of clip_id instead of src_end.",
        "dissolve": (
            "Seconds of crossfade into it; 0, the default, is a cut. Drawn from clip_id's own "
            "frames before its in-point, so it needs that much of the file before the start, and "
            "the film is no longer for it."
        ),
        "ease": "The crossfade's curve: linear (the default), ease, ease-in or ease-out.",
    },
    "dissolve": {
        "clip_id": "The incoming clip of a join `follow` made.",
        "src_start": "Where clip_id starts at that join, in its own seconds (follow's reply says).",
        "seconds": "Seconds of crossfade; 0 clears it back to a cut.",
        "ease": "The crossfade's curve: linear (the default), ease, ease-in or ease-out.",
    },
    "retime_rm": {
        "position": "The stretch to remove, by its position in retime_ls; that span plays at 1x again.",
    },
    "sound_add": {
        "assets": (
            "The imported clips to play, by clip_id. With several, each hit draws one, so a "
            "typed run does not repeat one sample. sound_generate registers a ready set (sfx-*)."
        ),
        "clip_id": (
            "The clip whose words or events say where — the transcript the word index "
            "indexes, or the recording the events belong to."
        ),
        "word_index": "Play as this word starts. One of word_index, phrase, event or every.",
        "phrase": "Play as this phrase's FIRST word starts, resolved against clip_id's transcript.",
        "event": "Play at this event of clip_id: `name`, or `name#k` when the name repeats.",
        "every": (
            "Play at every event of clip_id with this name (e.g. every keystroke). Events a cut "
            "removed are skipped and counted."
        ),
        "gain_db": "The level in dB; 0 plays the file as it is. The generated set peaks at -3 dBFS.",
        "jitter_db": "Vary each hit's level by up to this many dB either way. Default 0.",
        "min_gap": (
            "With every: drop a hit closer than this many seconds to the last one kept. Default 0.045."
        ),
        "src_in": (
            "Seconds into each asset where the sound starts. With src_out, plays one line of a long "
            "take, and the 30 s cap is on the trimmed part."
        ),
        "src_out": "Seconds into each asset where the sound stops. Unset plays to the file's end.",
        "ducks": (
            "The music bed's duck hears this sound as it hears the voice: set it for a narrator take or "
            "a line of dialogue placed as a sound, never for clicks. Only matters when the bed has a duck."
        ),
    },
    "sound_rm": {
        "position": "The sound record to remove, by its position in sound_ls.",
    },
    "broll_brief": {
        "fps": (
            "The frame grid the shot positions are projected on. Defaults to the rate "
            "`export` would use."
        ),
    },
    "verify": {
        "render": (
            "The finished render to transcribe and diff against the timeline. The "
            "expected words include a sound's or an inset's own when its clip has a "
            "transcript (`placed_audio`) — a narrator take placed as a sound is the "
            "film's voice. A voice sound (`ducks`) with no transcript is listed in "
            "`voice_sounds_untranscribed` and not checked: transcribe it first."
        ),
        "clip_id": "Diff against one transcript's expected words rather than all of them.",
        "transcript_path": (
            "An existing transcription of `render` — what a previous run cached and "
            "reported as `heard_transcript`. Pass it back to re-diff without spending "
            "the minutes again."
        ),
        "model": "The whisper model for the single-pass transcription.",
        "language": "Force a language code for it.",
        "windowed": (
            "Transcribe in short overlapping windows instead of one pass. **A clean "
            "single-pass result is not proof** — one pass collapses an immediate "
            "repeat the same way the source transcript did, and three surviving "
            "retakes passed a correct single-pass run on a real video. It costs a run "
            "over twice the audio and a smaller model."
        ),
        "window": "Length of each window in the windowed pass, in seconds.",
        "overlap": "How far each window overlaps the one before, in seconds.",
    },
    "check_frames": {
        "target": (
            "An NLE project (`.kdenlive`/`.mlt`/`.xml`) or a finished render. Omit it "
            "to just report `expected_frames`, the total the timeline lays down. Run "
            "it on the **exported project before rendering** — that is where it is "
            "worth the most."
        ),
        "fps": (
            "The rate the export ran at. It has to match, or the two sides are "
            "counting on different grids; it defaults to the rate `export` would have "
            "picked."
        ),
    },
    "film_check": {
        "reference": (
            "The delivered file this project is supposed to be. It is remembered, so "
            "a later call with no argument re-asks the same question against the same "
            "file."
        ),
        "reset": "Drop the stored reference.",
    },
    "import_edit": {
        "document": (
            "The `.kdenlive` or `.mlt` playlist somebody already trimmed by hand. "
            "Every clip it references has to be registered already; ones that do not "
            "match a registered clip by resolved path are named rather than imported "
            "behind your back."
        ),
        "clip_id": (
            "The registered clip to attribute a single-source document to, when its "
            "media sits at a path this project does not know."
        ),
    },
    "check_black": {
        "target": (
            "The render to scan. Required — unlike `check_frames` there is no cheap "
            "no-target mode, since there is nothing to detect black in without a "
            "render."
        ),
        "fps": "The rate the timeline's own frame arithmetic is counted on.",
        "pix_th": "How dark a pixel counts as black, 0–1.",
        "min_duration": "Shortest black run to report, in seconds.",
    },
    "spot_frames": {
        "target": "The render to pull frames from.",
        "count": (
            "How many evenly-spaced frames to pull. They come back ranked "
            "darkest-first, with a montage of them as an image."
        ),
        "times": "Explicit seconds to sample as well as the evenly-spaced ones.",
        "fps": (
            "The rate used to map a frame back to the clip and word it lands near — "
            "refused rather than guessed when the render's duration no longer matches "
            "the timeline."
        ),
    },
    "speech_overlap": {
        "clip_id": (
            "The clip whose placement is being proposed. It need not be on the "
            "timeline yet, and usually is not."
        ),
        "at": "Where the clip would sit on the timeline, in seconds.",
        "clip_in": (
            "Where inside the clip the proposed placement starts, in its own source "
            "seconds. Unset, its head."
        ),
        "clip_out": "Where it ends, in the clip's own source seconds. Unset, its end.",
        "vo_clip_id": (
            "Which transcript is the VO. Unset, the project's own. The VO always "
            "needs a transcript; the placed clip does not."
        ),
        "max_gap": (
            "How short a silence may be and still be swallowed into one speech run, "
            "in seconds — a 0.05s gap is not a usable seam."
        ),
        "min_seam": "How wide a gap has to be to be reported as a `clean_seam`, in seconds.",
        "cap": (
            "How far a word's claimed duration is trusted, as a multiple of the "
            "median. Whisper inflates the word after a collapsed retake until it "
            "covers the second take, so believing the claim masks exactly the hole "
            "being looked for — 3x is the same multiple a suspect duration is "
            "flagged at."
        ),
        "clip_evidence": (
            "`auto` (the default) uses the clip's transcript if it has one and its "
            "energy envelope otherwise, saying which in the result. `transcript` "
            "refuses a clip with none; `energy` forces the envelope even on a clip "
            "that has one — sound rather than speech, which counts a sting or a swell "
            "too."
        ),
    },
    "attenuate_noises": {
        "clip_id": (
            "The clip to scan. It always reads that clip's **original** media, never "
            "a previous attenuated copy, so repeated calls never compound gain."
        ),
        "db": "How far to pull each qualifying event down, in dB. Negative is quieter.",
        "max_event_seconds": (
            "Longest an event may run and still qualify automatically. Anything "
            "longer is reported as `disqualified` and never written."
        ),
        "max_gap_seconds": (
            "How wide the word-map gap around an event may be. A wide gap "
            "disqualifies even a very short event — that is the false-positive class "
            "this exists to prevent, speech sitting in a hole the transcript never "
            "wrote down."
        ),
        "pad": "Seconds added either side of each event before it is pulled down.",
    },
    "proxy_transcode": {
        "clip_id": (
            "The clip the preview cannot decode. A clip that already plays is "
            "refused, and so is one with no decodable streams — that is a broken "
            "file, not a codec problem."
        ),
        "force": (
            "Rebuild a proxy that is already current. It touches nothing authored: a "
            "proxy is a preview artefact the manifest never records, so no render can "
            "reach one. It overrides neither refusal."
        ),
    },
    "review_add": {
        "name": (
            "What to call this item in the served round. Re-using a name replaces "
            "that entry while its verdict stays attached."
        ),
        "source": (
            "The file to point at — never copied. A render already lives in "
            "`renders/`, a sheet in the sheet directory."
        ),
        "kind": "One of `render`, `sheet`, `ab`, `control`.",
        "baseline": (
            "Required for `kind=\"control\"`: the name of the already-registered item "
            "this one claims to be identical to. Both files' sha256 must match or the "
            "call is refused — nothing is labelled a control here unless it is "
            "byte-identical to what it claims."
        ),
        "about": (
            "One plain line the served page prints under the name, saying what this "
            "item is (how it was made, what differs) — a reviewer judges nothing they "
            "cannot tell apart."
        ),
    },
    "review_verdict": {
        "name": "The registered item being answered. An unregistered name is refused.",
        "verdict": (
            "The answer, as free text rather than an enum — past rounds answered "
            "yes/no, loop/hold, or a specific choice by name, and a fixed vocabulary "
            "would misfit whichever question the next round asks."
        ),
        "note": "Anything to record beside the verdict.",
    },
}

def _has_description(annotation: Any) -> bool:
    """Does this annotation already carry a `Field(description=...)`?

    `ProjectPath` is one, so `path` is documented before the table is
    consulted and must not be asked for twice.
    """
    if get_origin(annotation) is not Annotated:
        return False
    return any(getattr(meta, "description", None) for meta in get_args(annotation)[1:])


def _context_param(fn: Callable[..., Any]) -> str | None:
    """The argument the SDK fills with its `Context`, which is not advertised
    and so needs no description. The SDK's own finder, so the two agree."""
    from mcp.server.mcpserver.utilities.context_injection import find_context_parameter

    return find_context_parameter(fn)


def _reporter(ctx: Context) -> progress.Reporter:
    """Turn `progress.report` calls into `notifications/progress` on `ctx`.

    The tool body runs on an anyio worker thread and a report can come from a
    subprocess reader thread under it, so the loop token is captured here, on
    the worker, and handed to `from_thread.run` from wherever the report is
    made. The spec wants each value higher than the last, and a tool with
    phases (a synth, then its whisper readback) restarts from 0, so a phase
    that goes backwards is stacked on top of what was already sent.
    """
    # Asked of the loop itself: `from_thread.current_token()` wants a running
    # loop in *this* thread, which a worker thread has not got.
    token = anyio.from_thread.run_sync(anyio.lowlevel.current_token)
    state = {"base": 0.0, "raw": 0.0, "sent": -1.0}

    def send(current: float, total: float | None, message: str | None) -> None:
        if current < state["raw"]:
            state["base"] = max(state["sent"], 0.0)
        state["raw"] = current
        value = state["base"] + current
        if value <= state["sent"]:
            return
        state["sent"] = value
        scaled_total = None if total is None else state["base"] + total
        anyio.from_thread.run(ctx.report_progress, value, scaled_total, message, token=token)

    return send


def _describe_params(fn: Callable[..., Any]) -> None:
    """Hang `_PARAM_DOCS`'s text on the function's own annotations.

    On `fn` rather than on the wrapper deliberately: `functools.wraps` sets
    `__wrapped__` and `inspect.signature` follows it, so the advertised schema
    is always the undecorated function's — a description attached to the
    wrapper would be invisible in `tools/list`, which is the one place it has
    to appear.

    Refuses an argument with no entry, and an entry naming no argument. The
    second half is what catches a rename: the table would otherwise keep
    describing a parameter that no longer exists while the one that replaced
    it advertised nothing.
    """
    documented = {**_COMMON_PARAMS, **_PARAM_DOCS.get(fn.__name__, {})}
    hints = get_type_hints(fn, include_extras=True)
    parameters = [
        name
        for name, parameter in inspect.signature(fn).parameters.items()
        if parameter.kind is not inspect.Parameter.VAR_KEYWORD
    ]

    unknown = set(_PARAM_DOCS.get(fn.__name__, {})) - set(parameters)
    if unknown:
        raise RuntimeError(
            f"server._PARAM_DOCS[{fn.__name__!r}] describes {sorted(unknown)}, which "
            f"{fn.__name__} does not take — renamed, or a typo"
        )

    for name in parameters:
        annotation = hints.get(name)
        if annotation is None or _has_description(annotation) or name == _context_param(fn):
            continue
        text = documented.get(name)
        if text is None:
            raise RuntimeError(
                f"tool {fn.__name__!r} advertises {name!r} with no description — add one "
                "to server._PARAM_DOCS (or to _COMMON_PARAMS if it means the same "
                "thing on every tool that takes it)"
            )
        fn.__annotations__[name] = Annotated[annotation, Field(description=text)]


#: The `_meta` key that makes Claude Code load a tool's definition upfront
#: rather than defer it behind `ToolSearch`. Every tool so marked is context on
#: every turn of every session, plugin users included, so none is — until a
#: trial shows an agent searching for the same tool on every brief.
#: docs/plans/MCP.md § Step 4.
ALWAYS_LOAD_META = "anthropic/alwaysLoad"


def _tool(
    *selectors: str,
    projectless: bool = False,
    always_load: bool = False,
    max_result_chars: int | None = None,
) -> Callable[[F], F]:
    """Register a tool, routing its project-selector arguments through `_confine`.

    A decorator rather than a line in each body because the confinement has
    to hold for *every* tool — one body that forgot it would be the whole
    hole again — and because tool bodies stay trivial (see this module's
    docstring). `functools.wraps` sets `__wrapped__`, which the SDK's
    `inspect.signature(fn, eval_str=True)` follows, so the advertised schema
    is the undecorated function's and nothing about the tool surface changes.

    Defaults to `path`, which is every tool but one, so `@_tool()` keeps its
    meaning. Naming more than one is for a tool that addresses a *second*
    project — `reel`'s `dest` — and an unlisted selector is silently
    unconfined, exactly the way a tool registered with `mcp.tool()` is, so
    the list belongs beside the registration where it can be read.

    `projectless=True` is `fonts`/`pack_show`'s escape from the default-to-
    bound-project rule below: their own docstrings document `path=None` as
    "no project, proofcut's default" rather than "which project", so an omitted
    `path` there stays `None` — not confined, not defaulted — in every bind
    state, exactly as it always has.

    `always_load=True` sets `ALWAYS_LOAD_META`; read that constant's comment
    before reaching for it. `max_result_chars` sets `MAX_RESULT_META`, for a
    reply that cannot be windowed and is legitimately large.
    """
    names = selectors or ("path",)

    def decorator(fn: F) -> F:
        if fn.__name__ not in _ANNOTATIONS:
            raise RuntimeError(
                f"tool {fn.__name__!r} has no entry in server._ANNOTATIONS — classify it "
                "(read, add, set or edit) before registering it"
            )
        _describe_params(fn)
        meta: dict[str, Any] = {}
        if always_load:
            meta[ALWAYS_LOAD_META] = True
        if max_result_chars is not None:
            meta[MAX_RESULT_META] = max_result_chars
        register = mcp.tool(annotations=_ANNOTATIONS[fn.__name__], meta=meta or None)
        signature = inspect.signature(fn)
        present = [name for name in names if name in signature.parameters]
        context_param = _context_param(fn)
        if not present and context_param is None:
            return register(fn)
        writes = _ANNOTATIONS[fn.__name__] is not _READ

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(*args, **kwargs)
            # A caller that omits a selector now matters: `path` has a
            # default (`None`) so it can be left out, and the whole point is
            # to resolve that absence against the bound project rather than
            # let the tool body see `None`.
            bound.apply_defaults()
            for name in present:
                if projectless and bound.arguments[name] is None:
                    continue
                bound.arguments[name] = _confine(bound.arguments[name])
            # A long tool takes the SDK's `Context` only so its reports can
            # reach the client; the body never sees it, and nothing under the
            # body needs to know a client exists (`progress`). A progress
            # message is also what keeps a client from aborting a call that
            # has been silent for 30 minutes. docs/plans/MCP.md § Step 6.
            ctx = bound.arguments.get(context_param) if context_param else None
            reporter = _reporter(ctx) if ctx is not None else None
            # A project folder too deep for a stock Windows arrives as the
            # CLI's one line rather than `[WinError 206]` and a filename —
            # every tool that addresses a project passes through here, and
            # the few that address none (`ping`, `fonts`' default) write
            # nothing under one.
            with refusing_path_too_long(), progress.reporting(reporter):
                roots = _lock_roots(bound, present) if writes and _LOCKING else []
                notices = [_hold(root) for root in roots if _is_project(root)]
                if reporter is not None:
                    progress.report(0, None, fn.__name__)
                result = fn(*bound.args, **bound.kwargs)
                # `init`, and `reel`'s `dest`, make the project they address:
                # there was nothing to hold until the body ran.
                notices += [_hold(root) for root in roots if root not in projectlock.held_roots() and _is_project(root)]
                notice = next((n for n in notices if n), None)
                if notice is not None and isinstance(result, dict):
                    result = {**result, "lock_notice": notice}
                return result

        return register(wrapper)

    return decorator


#: Whether tools take the project lock. Only `serve()` turns it on: the lock
#: is held by the MCP server process, which is what an agent session is
#: (docs/plans/PROJECT-LOCK.md § What the lock guards), and a process that
#: merely imports this module — a test, `briefs` — is not one.
_LOCKING = False


def _lock_roots(bound: inspect.BoundArguments, present: list[str]) -> list[Path]:
    return [Path(bound.arguments[name]).resolve() for name in present if bound.arguments[name]]


def _is_project(root: Path) -> bool:
    return (root / MANIFEST_NAME).is_file()


def _hold(root: Path) -> str | None:
    return projectlock.ensure_held(root)


#: What `path` means, stated once and attached to the parameter itself rather
#: than repeated in 89 docstrings. It rides the advertised JSON schema as the
#: parameter's `description`, which is where a client — and a directory
#: grading this server's tools — looks for what an argument means: the schema
#: alone says only `string | null`, and "the optional `path`" being the one
#: unexplained argument is what Glama's tool-definition score reported, tool
#: after tool, on 2026-09-15 (docs/plans/LAUNCH.md § Step 4).
#:
#: The text is the two bind states `_confine` actually implements, because an
#: agent meets both: bound, it is ceremony with one accepted value; unbound,
#: it is the whole address.
ProjectPath = Annotated[
    str | None,
    Field(
        description=(
            "The project directory to act on. Omit it — the usual case — when this "
            "server is bound to a project (started as `proofcut -C DIR mcp`, or "
            "inside a project; `ping` says which): it then resolves to that one "
            "bound project, a relative path resolves against it, and a path "
            "outside it is refused by name. Unbound, `path` is the whole address "
            "and omitting it refuses rather than guessing."
        )
    ),
]

@_tool()
def ping() -> dict[str, Any]:
    """Check that the proofcut MCP server is alive, and report its version.

    `project` is the project every tool addresses when `path` is omitted, and
    `bound_by` says how it was chosen: `-C` at startup, or `cwd` because the
    server was started inside a project. Both are null when it is unbound.
    """
    return {
        "status": "ok",
        "server": "proofcut",
        "version": __version__,
        **_binding(),
    }


def _binding() -> dict[str, Any]:
    return {
        "project": None if _BOUND_ROOT is None else str(_BOUND_ROOT),
        "bound_by": _BOUND_BY,
    }


@_tool()
def doctor() -> dict[str, Any]:
    """Probe every external binary proofcut depends on, and name each one's trap.

    Takes no project — it answers the question asked before there is one.
    Report-only: nothing is installed and nothing is written. `ok` reads the
    required section alone; the four optional entries each gate one feature
    (cards, `describe`, `reframe_detect`, `vo_synth`) and everything else
    works without them.

    Every failing entry carries the fix, not just the ✗ — where melt actually
    lives, why PyPI's auto-editor is the wrong program, what to set on a box
    with no display.

    `server` says which project this server is bound to and how (`ping`'s own
    answer), since that decides which `path` a call may name.
    """
    return {**ops.doctor(), "server": _binding()}


@_tool()
def init(path: ProjectPath = None,
    *, name: str | None = None) -> dict[str, Any]:
    """Create a proofcut project directory at `path`.

    Writes `proofcut.json` and the empty `assets/`, `cache/`, `media/` and
    `renders/` directories, and nothing else — no media, no timeline. Refuses
    a directory that already holds a project rather than resetting it, so it
    is safe to call when unsure. Next is `import_media`, then a transcript,
    then `seed_timeline`.
    """
    return ops.init(path, name=name)


@_tool()
def migrate_project(path: ProjectPath = None,
    *, plan: bool = False) -> dict[str, Any]:
    """Bring an older project manifest forward to the current schema version.

    Every other tool refuses a project written by an older proofcut rather than
    guessing at a layout it does not recognise; this is what clears that. It
    is forward-only, and it copies the manifest into `cache/history/` before
    writing. `plan=True` reports the version and the steps without writing,
    which is how to ask what a project is before deciding to change it. A
    project whose manifest is still `lucid.json` (written before the rename)
    is renamed to `proofcut.json` first, reported as the first step.
    """
    return ops.migrate(path, plan=plan)


@_tool()
def import_media(
    path: ProjectPath = None,
    *,
    source: str,
    clip_id: str | None = None,
    copy: bool = False,
    mix: bool = False,
    audio_stream: int | None = None,
    sheet: bool = True,
) -> dict[str, Any]:
    """Register a media file with the project, probing it with ffprobe.

    Links the media by default rather than copying it. Returns the clip record,
    including the `clip_id` every other tool takes, and `audio_streams` — how
    many the container holds, since every other audio field on the record
    describes only the first.

    A container with more than one audio stream is refused rather than
    registered as if the first were the recording: whisper picks a stream of
    its own and MLT picks again at render, so the others would be missing
    from the film with every check clean. `mix=True` sums them into one track
    (two mics of one performance); `audio_stream=k` keeps one, numbered from
    0 in ffmpeg's own audio ordering. Either writes a derived copy under
    `cache/mixed/` that every later op reads without knowing it.

    A chapter list or the data/text track it rides on — a movie rip's own
    inherited from its parent film — is stripped unconditionally, with no
    flag to opt out: there is no legitimate choice to offer, unlike the
    audio-stream one. `strip`/`stripped` on the record say so when it
    happened; `duration` is corrected from the real video/audio streams
    either way it was detected.

    `sheet=True` by default: a `contact_sheet` of the clip's first ten
    seconds rides along on the returned record — cached frames from
    `thumbnail()`, so the clip's own opening (credits, black, a slate) is
    seen before it is cued to a shot rather than discovered after. Pass
    `sheet=False` to skip it. `--no-sheet` on the CLI.
    """
    return ops.import_media(
        path, source, clip_id=clip_id, copy=copy, mix=mix, audio_stream=audio_stream, sheet=sheet
    )


@_tool()
def list_media(
    path: ProjectPath = None, *, source_dir: str, recursive: bool = True
) -> dict[str, Any]:
    """List media files under `source_dir` that `import_media` could register.

    What hands an unattended agent source paths on a real job, since an
    agent confined to proofcut's tools (the agent panel's `--tools
    ToolSearch`) has no directory listing of its own (HISTORY.md § The
    seventh queue item, decided and built).
    A filename filter, not a probe — `import_media` is still what decides a
    file is actually usable. Each entry's `already_imported` is checked
    against this project's own registered clips, so a repeated call does not
    keep re-suggesting footage already on the asset list. `source_dir` names
    wherever the footage lives and is not confined to the project.
    """
    return ops.list_media(path, source_dir, recursive=recursive)


@_tool()
def clip_role(
    path: ProjectPath = None,
    *, clip_id: str, role: str | None = None, reset: bool = False
) -> dict[str, Any]:
    """Read or set a clip's import role — voiceover vs footage.

    Called with no `role` and no `reset` it just reports what is stored;
    `role` must be `"voiceover"` or `"footage"` (`ops.CLIP_ROLES`); `reset`
    clears it back to undeclared.

    **This changes nothing about how `transcribe`/`attach_transcript` or
    `describe` treat the clip.** Both already gate on their own evidence — a
    transcript file, `has_video` — and neither reads this field, so an
    undeclared clip is exactly as eligible for both as it always was. It is
    the assets pane's grouping, purely, and setting one is not a schema bump
    for that reason: an additive optional field on an existing clip record.
    """
    return ops.clip_role(path, clip_id, role, reset=reset)


@_tool()
def clip_rm(path: ProjectPath = None, *, clip_id: str) -> dict[str, Any]:
    """Un-register a clip `import_media` added, when nothing depends on it yet.

    Refused, naming every reason, if the clip is on the timeline, cued,
    held, the music bed's own clip, marked unspoken, transcribed or
    described — clear those first (`cue_rm`/`hold_rm`/`unspoken_rm`/`music
    reset=True`, or `proofcut undo`) or use `undo` back to before the import
    instead. The media on disk is never touched either way.
    """
    return ops.clip_rm(path, clip_id)


@_tool()
def attach_transcript(path: ProjectPath = None,
    *, clip_id: str, transcript_path: str) -> dict[str, Any]:
    """Ingest an existing word-timed whisper JSON as this clip's transcript.

    Checks the transcript against itself for `near_duplicates` — adjacent
    runs of words that sound like the same line said twice. That is a
    retake `verify` can never catch once both takes are cut into the edit,
    since nothing then disagrees with the timeline. A hit is not a verdict:
    a deliberate callback line looks the same as a swallowed retake here.

    Also reports `suspect_durations`, `overlaps` and `repeats`. An `overlaps`
    seam is a retake splice whisper read straight across, interleaving both
    takes and inventing words nobody said — check it before drawing anything
    derived from this transcript. `repeats` is a back-to-back duplicated
    phrase, the shape a retake makes when it survives as distinct words
    rather than as a seam — a different subset of retakes than `overlaps`
    finds, not a smaller one. Use transcript_checks to see all four again
    later.
    """
    return ops.attach_transcript(path, clip_id, transcript_path)


@_tool()
def transcribe(
    path: ProjectPath = None,
    *,
    clip_id: str,
    model: str = "turbo",
    language: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Transcribe a clip's own media with whisper, and attach the result.

    attach_transcript's ASR-driven sibling: use that when the recording
    already has a transcript, this when it needs one made. Takes minutes on a
    long recording — there is no timeout, so let it run. Reports
    `near_duplicates`, `suspect_durations`, `overlaps` and `repeats` the same
    way attach_transcript does.

    **It replaces whatever transcript the clip already had**, and it is the
    one mutation `undo` cannot reach: a transcript is its own file, so this
    writes neither the manifest nor the timeline and nothing is snapshotted.
    There is no cache either — a second call spends the same minutes again.
    `get_transcript` first if a transcript might already be there.
    """
    return ops.transcribe(path, clip_id, model=model, language=language)


@_tool()
def hear(
    path: ProjectPath = None,
    *,
    clip_id: str,
    start: float,
    end: float,
    model: str = asr.WINDOWED_MODEL,
    language: str | None = None,
    window: float = asr.WINDOW,
    overlap: float = asr.OVERLAP,
) -> dict[str, Any]:
    """What does `clip_id`'s source audio actually say between `start` and `end`?

    Use this when the transcript and the audio might disagree — a word with a
    suspect duration, a hole with no words in it, a stretch that reads clean
    but sounds wrong. It runs the same short-overlapping-window pass
    `verify(windowed=True)` runs, over the clip's own media across the span
    (source seconds), and comes back with `heard_words`/`heard_text` beside
    the attached transcript's own words over that span (`transcript_words`).
    No need to seed, export and verify to hear your source material.

    **Reports, never attaches** — nothing is written and no word index moves.
    Where the two disagree, `cut_by_time` addresses what the transcript has
    no word for. `heard_words` can be empty: silence is a real answer. One
    whisper run over the span; `end` past the clip is refused.
    """
    return ops.hear(
        path,
        clip_id,
        start=start,
        end=end,
        model=model,
        language=language,
        window=window,
        overlap=overlap,
    )


@_tool()
def get_transcript(
    path: ProjectPath = None,
    *,
    clip_id: str,
    first: int | None = None,
    last: int | None = None,
    search: str | None = None,
    limit: int = TRANSCRIPT_WORDS,
) -> dict[str, Any]:
    """Read a clip's transcript.

    With `search`, returns each match as a word range ready to hand to
    cut_by_transcript — prefer this to reading the whole transcript. With
    `first`/`last`, returns that window of words. Indices are inclusive.

    At most `limit` words come back per call. `total_words` is the whole
    transcript, `last_word` where this reply stopped, and `next_first` — only
    present when words were left out — is the `first` to ask for next.
    """
    return ops.get_transcript(
        path, clip_id, first=first, last=last, search=search, limit=limit
    )


@_tool()
def resolve_phrase(
    path: ProjectPath = None,
    *,
    clip_id: str,
    phrase: str,
    after: int = -1,
    occurrence: int | None = None,
    fuzzy: bool = True,
) -> dict[str, Any]:
    """Resolve a phrase to a word range against `clip_id`'s transcript.

    What `phrase=` on cue_add/cue_rm/unspoken_add/unspoken_rm/vo_extend/music/
    locate calls internally, exposed on its own so a resolution — including
    its full ambiguity list — can be inspected without attempting a write.
    Companion to `get_transcript` with `search=`, which lists every match
    with no cursor/occurrence/fuzzy; this picks exactly one, or explains why
    it can't.

    `after` skips matches at or before that word index (forward cursor, -1
    means from the start). More than one exact match with no `occurrence`
    given fails with every candidate's word range and text — pass
    `occurrence` (1-based) to pick one, or narrow the phrase. Zero exact
    matches falls back to a fuzzy match (`fuzzy=False` to refuse instead) —
    `ratio` is set only on a fuzzy hit, never disguised as exact. Read-only.
    """
    return ops.resolve_phrase(
        path, clip_id, phrase, after=after, occurrence=occurrence, fuzzy=fuzzy
    )


@_tool()
def transcript_checks(path: ProjectPath = None,
    *, clip_id: str | None = None) -> dict[str, Any]:
    """Re-check an already-attached transcript against itself.

    Returns the same four findings attach_transcript does —
    `near_duplicates`, `suspect_durations`, `overlaps`, `repeats` — for a
    transcript attached earlier, whose findings were reported once and are
    otherwise gone. Omit `clip_id` for every clip that has a transcript.

    Read `overlaps` before anything derived from this transcript is drawn on
    screen. A seam there is whisper reading across a retake splice and
    interleaving both takes, which **invents words nobody said** — and they
    read as ordinary English, so a human proofread finds some and is blind to
    the rest. `repeats` catches the other shape a retake takes: one that
    survived transcription as distinct, cleanly-timed duplicated words rather
    than as an interleaved seam. Reads only; it never writes.
    """
    return ops.transcript_checks(path, clip_id)


@_tool()
def attribute_speakers(
    path: ProjectPath = None,
    *,
    clip_id: str,
    streams: list[int] | None = None,
    labels: list[str] | None = None,
    margin_db: float = ops.spk.MARGIN_DB,
    apply: bool = False,
    limit: int = ops.AMBIGUOUS_SPANS,
) -> dict[str, Any]:
    """Label each word with the mic that was loudest while it was spoken.

    For a co-hosted recording captured on one mic per speaker. It is one pass
    over the transcript that is already attached — **never a second ASR
    run**, and transcribing each mic separately is measured and dead: half of
    each mic's own transcript is the other person, at every isolation tried.
    Transcribe once, from the mix or either mic, then call this.

    `streams` are ffmpeg audio ordinals into the registered container (`0`,
    `1`), and `labels` names them in the same order — one label per stream,
    defaulting to `speaker1`, `speaker2`. The speaker lands on the *word*: it
    is a label and never an address, so every cue, description, mark, music
    anchor and caption still resolves through `(clip_id, word_index)` and
    nothing else moves.

    **It reports; it does not decide below the floor.** `apply` is off by
    default. The rule is ~99% correct per word on clear speech and at
    **chance** on words spoken over each other, and `margin_db` is what
    half-knows the difference — anything under it comes back in
    `ambiguous_spans` to go and listen to, with the three words either side.
    Read `unmeasurable` separately from `ambiguous`: it means the mics ran
    out before the transcript did, which is a different recording problem.
    Applying keeps any label already on a word this refuses to call.
    """
    return ops.attribute_speakers(
        path,
        clip_id,
        streams=streams,
        labels=labels,
        margin_db=margin_db,
        apply=apply,
        limit=limit,
    )


@_tool()
def describe(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    window: float = 10.0,
    force: bool = False,
    plan: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Describe footage in fixed windows, so b-roll can be found by what is in it.

    A description is `(clip_id, src_start, src_end, text)` in **source**
    seconds, which is why cutting the edit can never invalidate one. Omit
    `clip_id` to describe every video clip that has not been described yet;
    name one to do just that clip. Audio-only clips are refused — their words
    are what `transcribe` indexes.

    **This is a job, not a request.** Cost is about three seconds per window
    regardless of how much footage the window spans, so a project's footage
    is minutes of GPU time. Run it with `plan=True` first: that resolves the
    whole work list and the estimate, and reports whether this machine can
    run the model at all, without loading anything.

    Already-described clips are skipped unless `force`. Do not widen `window`
    to save time without a reason — a single pass over a whole clip describes
    six frames as six people, fluently and with nothing saying it is wrong.

    Read `errors` and `truncated` in the result. A truncated description
    stops mid-fact and reads exactly like a complete one, and a window is
    never evidence of a *continuous shot*: the model narrates across a cut
    inside one as though it were a single take.

    The descriptions are written into the project, and `force` replaces the
    ones a clip already has; without it an already-described clip is skipped,
    so a repeat costs nothing and changes nothing.
    """
    return ops.describe(path, clip_id, window=window, force=force, plan=plan)


@_tool()
def describe_ls(
    path: ProjectPath = None,
    *, clip_id: str | None = None, contains: str | None = None
) -> dict[str, Any]:
    """Read the footage descriptions, to find b-roll by what is in it.

    **This is the search.** There is no ranking and no similarity score to
    ask for — you read the descriptions and pick, which is why the prompt
    behind them asks for concrete nouns. Each entry is `(clip_id, src_start,
    src_end, text)` in **source** seconds, so what you pick stays valid
    however the edit is cut.

    `contains` filters: whitespace-separated terms, case-insensitive, and
    every term must appear — `"kitchen knife"` matches "a knife on the
    kitchen counter". Reach for it before reading everything on a large
    project; `words` says how much text came back.

    Two things not to over-read. A window is evidence of what is *visible in
    a span*, never of a continuous shot — the model narrates across a cut
    inside one as though it were a single take. And an entry with
    `truncated` true stopped mid-fact and reads exactly like a complete
    description.

    A clip listed under `clips` with `windows: 0` has not been described yet;
    `describe` is what indexes it.
    """
    return ops.describe_ls(path, clip_id, contains=contains)


@_tool()
def card_templates(name: str | None = None) -> dict[str, Any]:
    """The card templates proofcut ships, and the slots each one takes.

    Read this before card_new: each slot says what it is for, whether it is
    required, and what it defaults to. The palette and font stacks are slots
    too, so a card can be restyled without authoring an SVG by hand.

    Call it with no `name` to choose one, then with `name` to read only that
    template's slots — the whole table is long.
    """
    return ops.card_templates(name)


@_tool(projectless=True)
def fonts(
    path: Annotated[
        str | None,
        Field(
            description=(
                "A project directory, or nothing. Omitting it means *no project* "
                "here — never the bound one — and reports proofcut's own default "
                "caption face; with a project, it reports the face that project's "
                "caption style would burn."
            )
        ),
    ] = None,
    install: bool = False,
) -> dict[str, Any]:
    """Will the caption font actually draw on this machine?

    Reports two answers side by side and does not merge them: `fontconfig`
    says whether the family is present, `render` burns the family and an
    impossible family and compares the pixels. Identical pixels mean the name
    is substituting whatever fontconfig claims — the only way to settle which
    face drew is to measure a render.

    `path` is optional: with a project, this checks the font that project's
    caption style would burn; without one, proofcut's default. `install` copies
    the vendored face where this OS's font system looks (fontconfig, CoreText
    or DirectWrite) and is off by default, because it writes into the home
    directory.
    """
    return ops.fonts(path, install=install)


@_tool()
def card_new(
    path: ProjectPath = None,
    *,
    name: str,
    template: str,
    slots: dict[str, Any],
    width: int | None = None,
    height: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Make a card from a template: fill its slots, write the SVG, render it.

    `name` is the `<name>` in `card:<name>` — the key a cue points at. Both
    the SVG source and the PNG are written under the project's
    `assets/cards/`, so the card can be re-edited later and re-rendered with
    card_render rather than redrawn.

    A slot value is text. A newline inside one is a line break wherever the
    template accepts multiple lines; nothing wraps automatically, because a
    guessed wrap overflows the frame without saying so. Ratings are numbers
    out of five, to the nearest half.

    **Leave `width`/`height` unset unless you mean something other than this
    film.** They default to the project's own canvas, which is what stops a
    card from pillarboxing inside the frame it was made for; naming a size
    that is not the project's is how a card loses a quarter of its width to
    black bar. Given at all, both must be.

    Refused if a card of this name exists, unless `overwrite` — a cue may
    already point at it. Read `font_warnings` in the result: a template
    naming a face this machine lacks still renders, in a substitute, with
    nothing else to say so.
    """
    return ops.card_new(
        path, name, template, slots, width=width, height=height, overwrite=overwrite
    )


@_tool()
def card_render(
    path: ProjectPath = None,
    *, name: str, width: int | None = None, height: int | None = None
) -> dict[str, Any]:
    """Rasterise `assets/cards/<name>.svg` into the PNG `card:<name>` shows.

    Author the SVG under the project's `assets/cards/`, then render it here;
    both files are kept, so a card can be re-edited rather than redrawn. The
    PNG is what a `card:` cue resolves to, so a card is not usable until this
    has run.

    `width`/`height` are given together or not at all and set the *render*
    size — the document is drawn at that scale rather than rasterised and
    resampled — and they fit rather than distort, so a size at a different
    aspect from the document's comes back smaller on one axis. Omitted, the
    document renders at its own declared size.

    Every call reports the fonts the document names and what fontconfig will
    actually draw. Read `font_warnings`: a card naming a face this machine
    lacks renders pixel-identically to one naming a face it has, so nothing
    downstream can catch the substitution.
    """
    return ops.card_render(path, name, width=width, height=height)


@_tool()
def card_reauthor(path: ProjectPath = None,
    *, name: str | None = None, plan: bool = False) -> dict[str, Any]:
    """Draw recorded cards again at the shape this project renders at now.

    Reach for this after `canvas` — a card is the only thing in a project
    whose shape a canvas change cannot fix on its own, because the aspect is
    baked into the SVG it was drawn from. Re-rendering the old SVG at the new
    size would pillarbox the card inside the frame; this fills the template
    again at the new canvas, from what `card_new` recorded.

    With no `name` it sweeps every recorded card the canvas has left behind,
    plus any whose files have gone missing. Named, it redraws that one
    whatever its canvas.

    Read `unrecorded` in the result. Those are cards with files on disk and
    no record of what made them — nothing can re-author one, and the way to
    fix it is card_new with `overwrite`, which records it on the way past.
    `plan` reports what would change and writes nothing.
    """
    return ops.card_reauthor(path, name, plan=plan)


@_tool()
def card_safe_zones(path: ProjectPath = None,
    *, card: str, platform: str) -> dict[str, Any]:
    """Measure a rendered card's ink in and around a platform's reserved band.

    **Report only** — nothing here blocks a render, and there is no default
    floor: `SCENE_THRESHOLD`'s own history is that a threshold gets pinned by
    looking at real output, not picked cold, and this check has had exactly
    one look so far. `platform` is one of proofcut's own zones (`tiktok-organic`,
    `tiktok-ads`, `reels`, `shorts`, `worst-case`) or one an applied pack's
    active variant declares — `pack_show` lists both.

    Reads `card` from its already-rendered PNG, never from the manifest's
    recorded slots alone, so the ink it measures is the ink actually on disk.
    Refuses a card with no PNG yet (card_new/card_render it first) or a
    platform neither source declares.
    """
    return ops.card_safe_zones(path, card, platform)


@_tool()
def pack_apply(
    path: ProjectPath = None,
    *,
    pack_path: str,
    variant: str = "default",
    allow_fallback: bool = False,
    install_fonts: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Load a channel preset pack, resolve and snapshot every variant, activate one.

    `pack_path` is an external file — never confined to the project, the same
    way `import_media`'s `source` is not — because a pack typically lives in
    a separate branding repo. **Every declared variant is resolved and
    hashed, not only the one `variant` activates**, so `pack_activate` can
    switch between them later with no file re-read; nothing after this call
    ever depends on `pack_path` staying reachable.

    For every font role, `fonts.probe` asks whether the declared family
    actually draws *on this machine* — a family that does not refuses the whole
    call unless `allow_fallback` (then its declared CSS fallback is used and
    recorded, never silent); one that draws but is vendored nowhere proofcut
    knows about is recorded `font_provenance: "unvendored"` rather than
    refused, since the render here is genuinely correct today. `install_fonts`
    vendors the pack's own `fonts/` directory if it ships one — off by
    default, since it writes into `$HOME`.

    Writes nothing to caption styling or to any card already on disk; a card
    picks up the new style only when `card_new`/`card_reauthor` next draws
    it, and captions only via `pack_apply_captions`. `plan` resolves and
    probes without writing.
    """
    return ops.pack_apply(
        path,
        pack_path,
        variant=variant,
        allow_fallback=allow_fallback,
        install_fonts=install_fonts,
        plan=plan,
    )


@_tool()
def pack_activate(path: ProjectPath = None,
    *, variant: str, plan: bool = False) -> dict[str, Any]:
    """Switch the active pack variant to one already snapshotted by pack_apply.

    No file re-read — refuses an unknown variant by name, naming the ones
    that are actually available, rather than trying to load it here.
    """
    return ops.pack_activate(path, variant, plan=plan)


@_tool()
def pack_apply_captions(path: ProjectPath = None,
    *, preset: str, plan: bool = False) -> dict[str, Any]:
    """Apply the active pack variant's caption preset, through caption_style.

    **Concrete resolved fields, never a live pointer**: this reads the
    preset's already-resolved dict off the snapshot and hands it to the
    ordinary caption_style call, so a later pack swap can never silently
    overwrite a project's caption look out from under it. Separate from
    pack_apply on purpose — applying a pack never restyles captions on its
    own, only this does.
    """
    return ops.pack_apply_captions(path, preset, plan=plan)


@_tool(projectless=True)
def pack_show(
    pack_path: str | None = None,
    path: Annotated[
        str | None,
        Field(
            description=(
                "A project directory, or nothing. Omitting it means *no project* "
                "here — never the bound one — so `pack_path` alone reads the file "
                "fresh; given, it reports what that project has applied."
            )
        ),
    ] = None,
    variant: str | None = None,
) -> dict[str, Any]:
    """What a pack declares — from its file, a project's snapshot, or both.

    `pack_path` alone reads and resolves the file fresh, needing no project
    (`card_templates`'s own shape). `path` alone reports what a project
    actually has applied, from its stored snapshot — never the file again.
    Both together compares "what the file says now" against "what the
    project is still running."
    """
    return ops.pack_show(pack_path, path=path, variant=variant)


@_tool()
def pack_status(path: ProjectPath = None) -> dict[str, Any]:
    """Active pack variant, and which cards/captions have drifted from it.

    A card is `stale` when its own recorded pack_hash no longer matches the
    active variant's current hash — not wrong, since card_new only pre-merges
    a pack's style and a per-call slot still wins, but worth a `card_reauthor`
    to catch up. `caption_preset_stale` is the same question for whatever
    pack_apply_captions last wrote.
    """
    return ops.pack_status(path)


@_tool()
def cue_add(
    path: ProjectPath = None,
    *,
    clip_id: str,
    word_index: int | None = None,
    asset: str | None = None,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    src_start: float | None = None,
    event: str | None = None,
) -> dict[str, Any]:
    """Add a picture cue: from `word_index` of `clip_id` onward, show `asset`.

    Or from an `event` of `clip_id`, for a recording with no words.

    Source-addressed like a word range — `asset` is an opaque key or path,
    not checked against disk here; `build_shots` resolves it, the same way
    assemble_scream.py's CUES table did by hand. Refused if a cue already
    sits at that exact word; cue_rm it first to replace it. Echoes the
    resolved word plus three either side, the same convention every
    word-indexed tool follows.

    Addressed by `word_index` **or** `phrase` (exactly one) — a phrase binds
    to its **first** word ("from this word onward"). `after`/`occurrence`
    disambiguate a phrase matching more than once; a resolved phrase is
    stored alongside the word index, additive metadata `cue_reresolve` can
    re-derive after a re-record.

    `src_start` pins **where inside `asset` the shot reads from**: seconds in
    that asset's own source time, which is exactly the number `describe_ls`
    reports for a window. This is how a moment you found with `describe` gets
    placed — without it the shot reads from wherever the per-asset cursor
    had got to, which is right for re-using a clip and wrong for showing the
    thing you searched for.

    It is an in-point and never a range: the out-point stays derived from the
    next cue through the edit, so a later cut still renumbers the shot
    correctly. The cost is a refusal instead of a rewind — if the shot's
    length runs past the end of the asset from that in-point, `build_shots`
    and the picture lane report it rather than quietly showing the asset's
    opening seconds instead. Shorten the shot with another cue, or pin
    earlier. A card takes no `src_start`; a held frame has no playhead.
    """
    return ops.cue_add(
        path,
        clip_id,
        word_index,
        asset,
        phrase=phrase,
        after=after,
        occurrence=occurrence,
        src_start=src_start,
        event=event,
    )


@_tool()
def cue_rm(
    path: ProjectPath = None,
    *,
    clip_id: str,
    word_index: int | None = None,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    event: str | None = None,
) -> dict[str, Any]:
    """Remove one picture cue, addressed the way `cue_add` placed it.

    Give `word_index`, or `phrase` to resolve against `clip_id`'s transcript
    (its first word, `cue_add`'s own binding). Refuses, listing every cue,
    when none sits at that word — `cue_ls` shows the table first. Shots
    re-project from the cues that remain; no other cue moves. Replacing a cue's asset is `cue_rm` then `cue_add`, since `cue_add`
    refuses an occupied word. `undo` puts it back.
    """
    return ops.cue_rm(path, clip_id, word_index, phrase=phrase, after=after, occurrence=occurrence, event=event)


@_tool()
def cue_ls(path: ProjectPath = None,
    *, clip_id: str | None = None) -> dict[str, Any]:
    """List the picture cue table, each entry echoed with its resolved word.

    Read-only. Omit `clip_id` to see every clip's cues. Ordered by
    `(clip_id, word_index)`, not by resolved timeline position — that needs
    the edit's surviving ranges, which is `build_shots`'s job.
    """
    return ops.cue_ls(path, clip_id=clip_id)


@_tool()
def cue_reresolve(
    path: ProjectPath = None,
    *, clip_id: str | None = None, apply: bool = False
) -> dict[str, Any]:
    """Re-resolve every phrase-addressed cue, unspoken mark and music-bed
    boundary against the current transcript, and report what moved.

    A re-record replaces a clip's transcript wholesale, and every stored
    `word_index` on that clip potentially now addresses the wrong word —
    already true today of a plain word-index entry, and this does not close
    that gap for one. What it closes it for is an entry that also carries the
    `phrase` it was placed with: re-resolving says where that same wording
    landed now, without hand re-indexing a whole cue table.

    `apply=False` (default): report only, nothing is written — the same
    posture as `reframe_detect`/`unspoken_detect`. `apply=True` rewrites
    `word_index` in place for every entry whose phrase still resolves to
    exactly one match; anything ambiguous or unresolved is reported and left
    untouched, never guessed. An entry with no stored phrase is reported as
    `"action": "unchanged (no phrase to re-resolve)"`, not silently skipped.
    """
    return ops.cue_reresolve(path, clip_id=clip_id, apply=apply)


@_tool()
def assets(path: ProjectPath = None) -> dict[str, Any]:
    """Every asset a cue can point at — clip or card — for an assets pane.

    The cue vocabulary is `clip_id` or `card:name`, so this lists both: each
    clip with its probe metadata, transcript/description presence, `role`
    and `media.playability` verdict; each card with what it was made from,
    whether its files exist, and whether it has a re-author record. Every
    entry carries `cues`, how many cues reference it — "is this used" is
    the question an assets pane exists to answer. Read-only.
    """
    return ops.assets(path)


@_tool()
def unspoken_add(
    path: ProjectPath = None,
    *,
    clip_id: str,
    word_index: int | None = None,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Mark a word the transcript holds and the recording never said.

    Whisper transcribes straight *across* a retake splice and emits words from
    both takes interleaved, so words appear in the index that nobody said.
    They are in the transcript and nowhere else — not the audio, not the
    render — so captions draw them and `verify` expects them.

    This writes a mark beside the transcript and never touches the transcript
    itself: word indices must not renumber, or every cue pointing at one would
    move. Captions, `caption_view` and `verify` all stop expecting the word;
    no audio, timing or shot changes, because the seconds around it are the
    take that was kept. Echoes the word it resolved to, plus three either side.

    Addressed by `word_index` **or** `phrase` — but unlike cue_add, a phrase
    resolving to more than one word is refused rather than bound to an edge:
    unspoken addresses exactly one word, and picking a side of a wider match
    would silently mark the wrong one half the time. Narrow the phrase, or
    pass `occurrence=` if it is disambiguation rather than width.

    Prefer `unspoken_detect` to find them: it is evidence rather than reading,
    and reading for sense provably misses the grammatical ones.
    """
    return ops.unspoken_add(
        path, clip_id, word_index, phrase=phrase, after=after, occurrence=occurrence
    )


@_tool()
def unspoken_rm(
    path: ProjectPath = None,
    *,
    clip_id: str,
    word_index: int | None = None,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Unmark a word `unspoken_add` marked, putting it back into captions and `verify`.

    Address it by `word_index`, or by a `phrase` resolving to exactly one
    word. Refuses a word that is not marked. The transcript file is never
    edited either way — a mark is a manifest entry — so no cue or caption
    renumbers. `unspoken_ls` lists the marks; `undo` restores one.
    """
    return ops.unspoken_rm(
        path, clip_id, word_index, phrase=phrase, after=after, occurrence=occurrence
    )


@_tool()
def unspoken_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every word marked never-spoken, with what the transcript says now.

    Read-only. `stale` is a mark whose recorded text and current text
    disagree — the transcript was re-attached under it. A stale mark is never
    applied, so re-transcribing surfaces as a list to re-check rather than as
    words disappearing from a caption file.
    """
    return ops.unspoken_ls(path)


@_tool()
def unspoken_detect(
    path: ProjectPath = None,
    *,
    render: str,
    clip_id: str | None = None,
    transcript_path: str | None = None,
    model: str | None = None,
    language: str | None = None,
    pad: float = ops.UNSPOKEN_PAD,
    apply: bool = False,
) -> dict[str, Any]:
    """Propose the words a render's own transcription says were never spoken.

    Candidates come from two mechanisms and one witness decides both. A
    **seam** is where whisper read across a splice and invented a word; a
    **fragment** is where a cut left a sliver of a real one, which draws as a
    whole word on screen and is inaudible. The witness is the render: the
    candidate's word is counted in the timeline over a short window and in the
    render's own transcription over the same seconds, and it is proposed only
    where the timeline has more of them than the render heard. Counted rather
    than looked up because the inventions are function words — asking whether
    the render says "the" near here answers yes off the real one beside it.

    `apply=False` by default, like `reframe_detect`: this changes what a
    caption says, and a wrong mark deletes a real word from every check proofcut
    has. Read the echoes first.

    `transcript_path` takes an existing transcription of the render, which is
    what `verify` leaves in `cache/verify/`. Pass it explicitly — it is never
    found automatically, because a re-render under the same filename would
    otherwise be judged against the previous render's audio.
    """
    return ops.unspoken_detect(
        path,
        render,
        clip_id=clip_id,
        transcript_path=transcript_path,
        model=model,
        language=language,
        pad=pad,
        apply=apply,
    )


@_tool()
def build_shots(path: ProjectPath = None,
    *, fps: float | None = None) -> dict[str, Any]:
    """Project the cue table into contiguous shots over the current edit.

    Maps each cue's word through the edit's surviving ranges to a timeline
    frame, resolves its `asset` to a checked path (`card:name` under
    `assets/cards/`, else a registered video clip_id), and runs each shot to
    the next cue — the last to the edit's own frame total. Refuses if a
    cue's word was cut from the edit; fix it with cue_rm/cue_add first.

    `fps` picks the frame grid; it defaults to the project's timebase, which
    for an audio-only project is milliseconds rather than frames. Pass the
    rate `export` will use to see the frames the export actually cuts at.
    """
    return ops.build_shots(path, fps=fps)


@_tool()
def seed_timeline(
    path: ProjectPath = None,
    *,
    clip_id: str,
    remove_silences: bool = True,
    threshold: float = 0.04,
    margin: str | None = None,
    edit_expr: str | None = None,
) -> dict[str, Any]:
    """Lay a clip down as the timeline, silence-cut by auto-editor by default.

    `edit_expr` passes auto-editor's edit language straight through, e.g.
    "(or audio:0.03 motion:0.06)".

    Writes `project.otio` and **replaces any timeline already there** — every
    cut made since the last seed included. It seeds a project rather than
    re-cutting one, and a re-seed with the same arguments lands the same
    timeline. The old one is snapshotted first, so `undo` puts it back.
    """
    return ops.seed_timeline(
        path,
        clip_id,
        remove_silences=remove_silences,
        threshold=threshold,
        margin=margin,
        edit_expr=edit_expr,
    )


@_tool()
def cut_by_transcript(
    path: ProjectPath = None,
    *,
    clip_id: str,
    cut: Sequence[Sequence[int]] | None = None,
    keep: Sequence[Sequence[int]] | None = None,
    pad: float = 0.0,
    confirm_suspect: bool = False,
    through_pause: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Cut or keep inclusive word ranges, e.g. cut=[[30, 45], [120, 131]].

    Pass exactly one of `cut` or `keep`. `pad` widens each range on both sides
    in seconds, to land the cut in the silence between words. The timeline is
    snapshotted first, so this is undoable.

    Every range echoes back the words it resolved to, plus the few words either
    side of it — an index one past the intended phrase reads fine on its own
    and is only visibly wrong next to its neighbours. `pad_reach` names any
    neighbour the padding eats, since padding is in seconds and the echoed text
    is not.

    `through_pause=True` (cut only) extends each range's trailing edge through
    the pause after its last word, whenever that gap is wide enough to have
    drawn a `[N.Ns]` marker in the transcript pane — so cutting a phrase also
    removes the dead air after it instead of leaving it playing. A no-op when
    the trailing gap is too short to have drawn a marker.

    `plan=True` returns that whole payload — including what the timeline would
    become — without writing anything. Prefer it over cutting and undoing.

    Refused if a range's first or last word claims a suspect duration (see
    `attach_transcript`/`transcribe`'s `suspect_durations`) — that word's
    `end`/`start` is what the cut boundary resolves to, and it is usually
    hiding a retake rather than ending where it claims. Check the word, then
    retry with `confirm_suspect=True` if the boundary is actually fine. Under
    `plan=True` these are reported as `suspect_boundaries` instead of refused.
    """
    return ops.cut_by_transcript(
        path,
        clip_id,
        cut=cut,
        keep=keep,
        pad=pad,
        confirm_suspect=confirm_suspect,
        through_pause=through_pause,
        plan=plan,
    )


@_tool()
def cut_by_time(
    path: ProjectPath = None,
    *,
    spans: Sequence[Sequence[float]],
    pad: float = 0.0,
    confirm_suspect: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Cut spans of RENDER/TIMELINE time — what a human reports watching an export.

    Each span is [start, end) in the seconds the current export plays at
    (what timeline_status/verify describe), not source time and not word
    indices. proofcut converts each span to the source interval(s) it plays —
    the inverse of the mapping captions and playback use — and cuts those
    through the same Edit.remove path cut_by_transcript uses. The render
    timestamp is never stored: the conversion happens once, here, at call
    time.

    All spans resolve against the CURRENT timeline before any is applied, so a
    list of notes from one watch stays valid together even though a real cut
    would shift every later timestamp. Overlapping spans are refused rather
    than silently double-applied.

    Every piece echoes the source interval it produced (more than one when the
    span crosses an earlier cut or a clip boundary) and the words it overlaps
    there, plus three neighbours either side — the human check that the
    timestamp actually hit the intended flub. `pad` widens only the OUTER
    edges of each requested span. `plan=True` resolves and reports without
    writing, identically to cut_by_transcript.

    Refused the same way cut_by_transcript is if a span overlaps a word with a
    suspect duration; `confirm_suspect=True` or `plan=True` behave the same.

    **A second call is not the same call.** These are render timestamps, and
    this cut moves everything after it, so the same numbers name different
    material next time — take them off a fresh watch rather than reusing a
    list across two calls. That is also why one call takes every span at
    once.
    """
    return ops.cut_by_time(path, spans=spans, pad=pad, confirm_suspect=confirm_suspect, plan=plan)


@_tool()
def restore(
    path: ProjectPath = None,
    *,
    clip_id: str,
    ranges: Sequence[Sequence[int]],
    pad: float = 0.0,
    plan: bool = False,
) -> dict[str, Any]:
    """Un-cut whichever part of these inclusive word ranges is not currently in the timeline.

    Same range shape as cut_by_transcript's cut=/keep=. Each range resolves to
    source time exactly like a cut does; only the part Edit.gaps says is
    actually absent comes back — material still present in the request is left
    alone, a request spanning two separate cuts restores both as separate
    pieces, a request only touching part of one cut restores only that part.
    Restoring only ever brings back material the source recording already has
    (bounded by the clip's own registered duration), so the timeline stays a
    subset of the source throughout — this is not vo_extend (PLAN.md parks that
    separately), which would add material the source never had.

    pad matches cut_by_transcript's own pad: pass the same value used on the
    original cut to bring back its padding sliver, not just the words.

    Unlike a cut, there is no suspect-duration refusal — a boundary that looks
    like it swallowed a retake is exactly the kind of thing restore exists to
    bring back, not a mistake to guard against.

    Refused if clip_id has no surviving segment anywhere in the edit (nothing
    left of it to splice the range next to — undo or re-seed instead), or if
    its segments are not contiguous in the edit (an interleaved multi-source
    timeline, which restore does not support yet).

    plan=True resolves and reports without writing, identically to
    cut_by_transcript.
    """
    return ops.restore(path, clip_id, ranges, pad=pad, plan=plan)


@_tool()
def locate(
    path: ProjectPath = None,
    *,
    clip_id: str,
    first: int | None = None,
    last: int | None = None,
    source_start: float | None = None,
    source_end: float | None = None,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    event: str | None = None,
) -> dict[str, Any]:
    """Where does a SOURCE word or SOURCE time play in the current render?

    cut_by_time's read-only mirror, and the tool to reach for before quoting
    any timestamp to a human: word indices and transcript times address the
    original recording, so they are NOT render times and every accumulated cut
    moves them further apart.

    **Two clocks, and this reports the Edit's.** `timeline_start`/
    `timeline_end` are 0 = the Edit's own first frame, unchanged whether or
    not a `head` (a cold open) is configured. `head_seconds` rides along
    (0.0 with none) so a caller that needs the *actual* render time — this
    tool's own stated purpose — can add it: render time = Edit time +
    `head_seconds`.

    Address it one way per call — `first`/`last` are inclusive word indices
    (`last` defaults to `first`), `source_start`/`source_end` are seconds into
    the recording (omit `source_end` to locate an instant), or `phrase` — a
    phrase naturally *is* a range, so it resolves straight to `first`/`last`
    with no edge to pick (`after`/`occurrence` disambiguate a phrase matching
    more than once), or `event` — a named instant from `events`.

    Read `present` first. False means the material is not in the render, and
    `beyond_source` distinguishes "you cut it" from "the recording never went
    that far". A partially-cut range is normal: `placements` lists each
    surviving piece in playback order with the source coordinates saying which
    part of the phrase it is, `covered` how much survives, and `contiguous`
    whether the survivors still play back-to-back. Word mode (and phrase
    mode, which resolves into it) echoes the resolved words plus three either
    side; time mode echoes the words the interval overlaps, or its nearest
    neighbours if it landed in silence. Read-only: nothing is written.
    """
    return ops.locate(
        path,
        clip_id,
        first=first,
        last=last,
        source_start=source_start,
        source_end=source_end,
        phrase=phrase,
        after=after,
        occurrence=occurrence,
        event=event,
    )


@_tool()
def timeline_status(path: ProjectPath = None) -> dict[str, Any]:
    """Report the current timeline: duration, segment count, undo depth.

    `head`/`tail` echo the cold open / finishing pass set with the `head`/
    `tail` tools, or null for either with none. `expected_frames`/
    `expected_duration` are what `export` would lay down — `timeline_duration`
    alone stays the `Edit`'s own length even with a head or a tail configured,
    since the `Edit` never grows to describe either bookend.

    **This is the tool to call first, to see what state a project is in** —
    a fresh or un-seeded project answers `seeded: false` with the clip list
    rather than refusing (TRIAL.md § `timeline_status` is the first call an
    agent makes and it refuses on a fresh project).
    """
    return ops.status(path)


@_tool(max_result_chars=VIEW_RESULT_CHARS)
def timeline_view(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    first: int = 0,
    limit: int = VIEW_WORDS,
) -> dict[str, Any]:
    """The whole edit at once: segments, cut seams, and every word's fate.

    timeline_status counts things; this says what they are. Each segment
    carries both coordinate systems (source in, timeline out), each seam is
    named by the surviving words either side of it rather than by the second
    it currently sits at, and each word reports whether it survived, how much
    of it did, and where it now plays.

    Survival is an overlap test, so a word a cut split reports present with
    `partial` set — that is normal on whisper timings, not a defect. Words
    with a suspect duration carry the same flag attach_transcript reported.

    This is locate asked once for the whole clip instead of once per range,
    and it is what the `proofcut web` view draws. Read-only.

    `shots` is the picture lane the cue table projects — null when there are no
    cues, and null with a `shots_error` message when the plan refuses (a cue
    that was cut, or a shot longer than the asset it points at). The refusal is
    reported here rather than raised, because this is the view a person uses to
    find the cue to fix. `shots_rate` is the frame grid it was quantised on,
    which is export's rate and not `timebase`.

    `segments`/`shots`/`seams` stay Edit-relative even with a `head`
    configured — see `head`'s own docstring for the two-clock rule.
    `head_seconds` is the offset a render-time reader needs (0.0 with none);
    `head` is the stored config plus its resolved frame count.

    `words` is a window of `limit` from `first` (`words_total`, `words_next`);
    the lanes are always whole. `get_transcript` with `search=` finds a word
    faster than paging here.
    """
    return ops.timeline_view(path, clip_id=clip_id, first=first, limit=limit)


@_tool()
def properties(
    path: ProjectPath = None,
    *, clip_id: str | None = None, word_index: int | None = None
) -> dict[str, Any]:
    """Project/clip/cue detail for a properties inspector, composed only.

    No arguments: `status`, `canvas` and `caption_style`'s own reports.
    `clip_id`: adds that clip's `assets` entry, its `reframe` window table,
    and its whole `cue_ls`. Both `clip_id` and `word_index`: adds `cue` (the
    matching entry from that `cue_ls`, or null if the word carries none) and,
    only when `cue` is null, `context` — the word plus three either side,
    the same echo every word-indexed tool gives (a cue's own entry already
    carries this, so it is not duplicated). `word_index` needs `clip_id`.
    """
    return ops.properties(path, clip_id=clip_id, word_index=word_index)


@_tool()
def finish_report(
    path: ProjectPath = None,
    *, framing: bool = False, holds: bool = False, continuity: bool = False
) -> dict[str, Any]:
    """Duration/canvas/caption/picture/marks/seams report for Finish mode,
    composed only — the truth strip's own numbers.

    `duration`: edit seconds, tail seconds, and their sum. `canvas`: the
    stored or footage-fallback canvas, plus each export preset's own
    ok/refusal-message. `captions`: whether a style is configured, its
    resolved font, and whether the last render actually burned it in
    ("yes"/"no"/"unknown" — unknown when no render log exists). `picture`:
    cue count, pinned count, and the picture plan's own refusal message when
    it has one. `marks`: unspoken marks applied vs. still stale. `seams`:
    the transcript's own overlap count. `unused_clips`: registered clips on
    no lane, cued nowhere, held nowhere, not the music bed — a clip
    imported and forgotten (TRIAL.md § Registered-and-not-on-the-timeline
    has no report of its own), clearable with `clip_rm` or by cueing it.
    `flags`: the rolled-up warnings behind all of the above, each one naming
    the mode that fixes it.

    `framing` adds `reframe_coverage`'s stale-framing numbers and their two
    flags, and is off by default because it decodes placed footage for a
    scene-cut scan — 5.7s wall and 46s of CPU on the film, uncached, every
    call. Off, `framing` is `None`, which means "not measured" rather than
    "nothing stale".

    `holds` adds `hold_check`'s own per-hold seam/transcription report
    against the last render — off by default for the same reason `framing`
    is: it decodes and transcribes render spans. `None` when not asked for,
    and also `None` when asked for but nothing has rendered here yet.

    `continuity` adds `continuity_check`'s finding count by kind (rewind,
    replay, short_shot, stub) and how many are currently accepted — also off
    by default, its `stubs=True` half paying the identical scene-cut decode
    `framing` does. `None` when not asked for.
    """
    return ops.finish_report(path, framing=framing, holds=holds, continuity=continuity)


@_tool()
def undo(path: ProjectPath = None, steps: int = 1, plan: bool = False) -> dict[str, Any]:
    """Roll the project back `steps` mutations (default one) — the timeline, the manifest, or both.

    Mutating tools snapshot first (`migrate_project` keeps its own backup
    instead), so this undoes cuts, cues, framing, the music bed, caption style
    and the rest alike. The reply says what came back: `timeline_restored`,
    `manifest_restored`, and `timeline_removed` when undoing a `seed_timeline`
    leaves no timeline at all. Undoing an import un-registers the clip but
    leaves its media on disk; transcripts are not snapshotted and stay. There
    is no redo, so an out-of-range `steps` is refused before anything is
    restored, and `plan` shows what it would undo — `changes`' answer for the
    same `steps` — first. `status` gives `undo_depth`.
    """
    return ops.undo(path, steps=steps, plan=plan)


@_tool()
def changes(path: ProjectPath = None, steps: int = 1) -> dict[str, Any]:
    """What the last `steps` mutations did — what `undo` that many times would roll back.

    Read-only. Compares the snapshot every mutation already leaves against the
    live project. `timeline.removed` and `timeline.added` are source spans per
    clip with the words they carry and where they played, so a cut reads as the
    words it took out rather than as every later segment moving; a pure
    reorder is `reordered`; spans under 50 ms (a frame's edge moving) are only
    counted, in `removed_slivers`/`added_slivers`. `manifest.keys` lists each changed manifest key:
    records `added`/`removed`, and `changed` field by field where a record has a
    name (a cue by its word, a framing window by its in-point, a clip by its
    id); a word-addressed record echoes its word in brackets with three either
    side. Lists past 40 entries are cut, with exact `_count`s beside them.
    `unchanged: true` means the snapshot and the project agree. Words come from
    the transcripts as they stand now.
    """
    return ops.changes(path, steps=steps)


@_tool()
def export(
    path: ProjectPath = None,
    *,
    output: str,
    export_format: str | None = "kdenlive",
    fps: float | None = None,
    preset: str | None = None,
    resolution: Sequence[int] | None = None,
    loudness: float | None = None,
    true_peak: float = -1.0,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Export the timeline as an NLE project, or render it.

    The default writes an MLT project Kdenlive opens; `export_format=null`
    renders media. The writer is chosen **from the project**, never from an
    argument: a single-source timeline goes through auto-editor, and a
    multi-source one — a cue table, a second clip, a canvas, a bed, a tail —
    is written as MLT by proofcut and rendered by melt, because auto-editor
    renders a second source at 720x576 while exiting 0. The reply names the
    writer, and a melt render reports resolution and frame count measured off
    the finished file.

    `preset` bundles quality for a render; `tiktok-reels` **checks** 9:16 and
    never sets the shape — use `canvas` first. `loudness` masters to a LUFS
    target and refuses, leaving the render as it was, if it misses by more
    than 1 LU.

    Captions are not burned by this — `add_captions` is its own step. Then
    check the file against the timeline with `check_frames` and `verify`;
    a render that exists is not a render that is right.
    """
    return ops.export(
        path,
        output,
        export_format=export_format,
        fps=fps,
        preset=preset,
        resolution=tuple(resolution) if resolution is not None else None,
        loudness=loudness,
        true_peak=true_peak,
    )


@_tool()
def add_captions(
    path: ProjectPath = None,
    *,
    output: str,
    clip_id: str | None = None,
    preset: str | None = None,
    max_words: int | None = None,
    max_gap: float | None = None,
    max_duration: float | None = None,
    hold: float | None = None,
    burn: str | None = None,
    burn_output: str | None = None,
) -> dict[str, Any]:
    """Write word-timed ASS captions for the current timeline to `output`.

    Timings follow the *timeline*, not the original recording, so captions stay
    correct after cuts; words that were cut are omitted and counted as
    `words_cut`.

    The look comes from the project — set it with caption_style, see it with
    caption_view. The arguments here override it for this one file and are not
    written back, so regenerating after a cut is styled the project's way
    again. Leave them unset unless you specifically want a one-off.

    The sidecar .ass is always written to `output` — Kdenlive loads it and it
    stays restylable. Pass `burn` (a render of THIS timeline) to burn the
    captions into a video as well, written to `burn_output`; against any other
    video the timings will not line up.
    """
    return ops.add_captions(
        path,
        output,
        clip_id=clip_id,
        preset=preset,
        max_words=max_words,
        max_gap=max_gap,
        max_duration=max_duration,
        hold=hold,
        burn=burn,
        burn_output=burn_output,
    )


@_tool()
def caption_view(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    first: int = 0,
    limit: int = CAPTION_CUES,
) -> dict[str, Any]:
    """The captions this timeline would produce, and the style in force.

    add_captions without writing a file: the same cues, in timeline seconds,
    already grouped by the project's own break rules — so this is how to check
    a restyle, or read back what a caption actually says at some moment,
    before committing a file to it.

    Reports rather than refuses: a project with no transcript, or one whose
    every word has been cut, comes back with an empty `cues` and a
    `cues_error` saying which. Read-only.

    `cues` is a window of `limit` from `first`; `cues_total` is how many the
    film has and `cues_next`, when present, where to continue. Use `locate` to
    find the cue at a moment rather than paging to it.
    """
    return ops.caption_view(path, clip_id=clip_id, first=first, limit=limit)


@_tool()
def caption_span_add(
    path: ProjectPath = None,
    *,
    clip_id: str,
    word_index: int | None = None,
    phrase: str | None = None,
    event: str | None = None,
    until_word_index: int | None = None,
    until_phrase: str | None = None,
    until_event: str | None = None,
    seconds: float | None = None,
    after: int = -1,
    occurrence: int | None = None,
    off: bool = False,
    style: dict[str, Any] | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Captions off over a stretch of the film, or a different look there.

    `off=True` draws none (over an end card or logo). `style` changes
    caption_style fields over the span only; `{"max_words": 1, "size": 150,
    "position": "middle"}` over one word draws it alone and large, a beat
    inside ordinary lines. Addressed like overlay_add: start at a word,
    phrase or event; end at a word, phrase, event or length. A line never
    crosses a span's edge; later spans win where two overlap. caption_view
    draws the result; add_captions writes it.
    """
    return ops.caption_span_add(
        path, clip_id, word_index, phrase=phrase, event=event, until_word_index=until_word_index,
        until_phrase=until_phrase, until_event=until_event, seconds=seconds, after=after,
        occurrence=occurrence, off=off, style=style, plan=plan,
    )


@_tool()
def caption_span_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every caption span, later ones winning, with where each plays now. Read-only."""
    return ops.caption_span_ls(path)


@_tool()
def caption_span_rm(path: ProjectPath = None, *, position: int, plan: bool = False) -> dict[str, Any]:
    """Remove the caption span at `position`, as caption_span_ls numbers it."""
    return ops.caption_span_rm(path, position, plan=plan)


@_tool()
def lexicon_ls(path: ProjectPath = None) -> dict[str, Any]:
    """The project's standing corrections: `hear` (captions) and `say` (vo_synth).

    Read-only. `hear` maps whisper's spelling to what captions print; `say`
    maps a scripted word to what the voice model is given. `caption_view`'s
    `corrected` shows where the `hear` rules fired.
    """
    return ops.lexicon_ls(path)


@_tool()
def lexicon_add(
    path: ProjectPath = None,
    *,
    heard: str,
    canonical: str,
    kind: str = "hear",
    plan: bool = False,
) -> dict[str, Any]:
    """Keep a standing spelling correction: captions print `canonical` wherever whisper wrote `heard`.

    The fix for a caption that shows whisper's spelling ("rough" for "ruff",
    "Pup BNB" for a brand). Whole words, one or several: a multi-word key
    merges its words into one caption word. Every occurrence, so narrow one
    by including a neighbouring word. Display only; the transcript and
    `verify` are untouched. The reply counts the caption words it changes now.

    **Undo does not revert it**: the lexicon is a preference kept beside the
    project, not an edit. Remove an entry with `lexicon_rm`. `kind="say"` is
    `vo_synth`'s pronunciation respelling instead.
    """
    return ops.lexicon_add(path, heard, canonical, kind=kind, plan=plan)


@_tool()
def lexicon_rm(
    path: ProjectPath = None,
    *,
    heard: str,
    kind: str = "hear",
    plan: bool = False,
) -> dict[str, Any]:
    """Remove one lexicon entry; refuses a key the lexicon does not hold.

    The caption goes back to whisper's spelling. `lexicon_ls` lists the entries.
    """
    return ops.lexicon_rm(path, heard, kind=kind, plan=plan)


@_tool()
def caption_style(
    path: ProjectPath = None,
    *,
    preset: str | None = None,
    font: str | None = None,
    size: int | None = None,
    text: str | None = None,
    highlight: str | None = None,
    outline_colour: str | None = None,
    box_colour: str | None = None,
    bold: bool | None = None,
    box: bool | None = None,
    outline_width: float | None = None,
    shadow: float | None = None,
    position: str | None = None,
    margin: int | None = None,
    karaoke: bool | None = None,
    reveal: str | None = None,
    reveal_ms: int | None = None,
    reveal_blur: float | None = None,
    max_words: int | None = None,
    max_gap: float | None = None,
    max_duration: float | None = None,
    hold: float | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the caption look this project keeps.

    The style is project state and the captions are derived from it, so a
    restyle survives every later cut: regenerating re-reads this. Call it with
    no arguments to read the current look and learn the field names; any
    argument sets that field and leaves the others alone. `reset` drops every
    override first — `reset` plus `preset` starts clean from a preset.

    `preset` is the base look ("clean", "karaoke" for per-word highlight,
    "reveal" for words landing mid-frame and fading in as spoken, or "boxed");
    everything else overrides one of its fields, and only the overrides are
    stored. `reveal` ("fade", "blur" or "none") is how each word arrives.

    Colours take "#rrggbb", "#rrggbbaa", a name ("yellow", "white", "red", …)
    or an ASS "&H…" value. `text` is the word's colour and `highlight` what it
    turns as it is spoken, which only shows with karaoke on. `position` is
    named: "bottom", "top", "top-right", and so on. Both come back resolved,
    because ASS quotes colours backwards and alpha-inverted.

    `plan` validates and resolves without writing. Use caption_view to see the
    result on the actual timeline.
    """
    return ops.caption_style(
        path,
        preset=preset,
        font=font,
        size=size,
        text=text,
        highlight=highlight,
        outline_colour=outline_colour,
        box_colour=box_colour,
        bold=bold,
        box=box,
        outline_width=outline_width,
        shadow=shadow,
        position=position,
        margin=margin,
        karaoke=karaoke,
        reveal=reveal,
        reveal_ms=reveal_ms,
        reveal_blur=reveal_blur,
        max_words=max_words,
        max_gap=max_gap,
        max_duration=max_duration,
        hold=hold,
        reset=reset,
        plan=plan,
    )


@_tool()
def canvas(
    path: ProjectPath = None,
    *,
    size: str | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the shape this project renders at.

    The canvas is project state and every frame size derives from it — the
    MLT profile and the captions' reference canvas both read it, so a project
    cannot quote caption sizes against one shape and render another. Call it
    with no `size` to read what is in force plus the footage-derived shape it
    would fall back to; `reset` drops the override and returns to that shape.

    `size` is "WIDTHxHEIGHT", e.g. "1080x1920" for a vertical reel. Both
    edges must be even.

    Setting one has a routing consequence, reported as `routes_through`: an
    overridden project renders through the MLT writer whatever its source
    count, because auto-editor cannot be handed a canvas it will honour.
    An override that changes the *aspect* crops to fill rather than
    pillarboxing, so `cropped` names every clip that loses footage to it —
    use `reframe` to see or change which part of each one is kept.
    """
    return ops.canvas(path, size=size, reset=reset, plan=plan)


@_tool()
def head(
    path: ProjectPath = None,
    *,
    asset: str | None = None,
    src_start: float | None = None,
    seconds: float | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    gain_db: float | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the cold open this project plays before its first frame.

    `tail`'s mirror at the other end of the film — the same read/partial-
    update/reset/plan shape — but its asset rule runs the other way:
    `asset` must be a registered clip_id, never `card:name`. A cold open is
    real footage with real dialogue by definition; `tail` forbids that
    because `verify` would gain a permanent disagreement it can never
    resolve, and a head is taught to account for its own words instead
    (`verify`'s `head_words_trimmed`) rather than being restricted to
    silence. Call it with no arguments to read what is in force.

    Setting `asset` or `seconds` for the first time needs both together;
    either alone after that updates just that field, `tail`'s partial-update
    shape. `src_start` defaults to 0.0 on a first set. `fade_in`/`fade_out`
    default to 0.0 and — unlike `tail`'s `fade` — are drawn from day one,
    the whole reason this feature exists (a hard butt-join between room tone
    and digital silence is exactly the seam a missing fade produces).
    `gain_db` defaults to 0.0, a flat non-fading level shift distinct from
    the fades.

    Joins the cues' picture lane, or with none the timeline's own track; a
    sound-only film with no cues is refused. `reset` drops the
    head entirely. `plan` resolves and validates
    without writing.
    """
    return ops.head(
        path,
        asset=asset,
        src_start=src_start,
        seconds=seconds,
        fade_in=fade_in,
        fade_out=fade_out,
        gain_db=gain_db,
        reset=reset,
        plan=plan,
    )


@_tool()
def tail(
    path: ProjectPath = None,
    *,
    asset: str | None = None,
    seconds: float | None = None,
    fade: float | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the finishing pass this project plays after its last frame.

    An end card or a bumper, applied by `export` itself rather than glued on
    afterward with ffmpeg — the fix for a defect that has already shipped: a
    finishing pass applied downstream of `export` is dropped by every
    derivation at exit 0, silently, because nothing in the project ever knew
    it existed (HISTORY.md § The bumper the teaser never had, § The end card).
    Call it with no arguments to read what is in force.

    `asset` must be `card:name`, never a clip_id — `verify` diffs a render's
    own transcription against the timeline's words, and silence adds none of
    its own, which is exactly what a card behind it guarantees and a media
    clip would not. `seconds` is the tail's *whole* length, card included, not
    a hold with `fade` added on top of it (the known trap: `xfade` finishes
    exactly at the length it is given). `fade` dissolves the card in over
    the film's last frames; 0 cuts to it hard. A music bed's `over_tail`
    keeps the music playing under the card.

    Setting `asset` or `seconds` for the first time needs both together;
    either alone after that updates just that field, the same partial-update
    shape `caption_style` has. `reset` drops the tail entirely.

    The card follows the cues' picture lane, or with none the timeline's own
    track, so a screen recording takes a tail as it is. A
    sound-only film with no cues is refused by `export`, by name. `plan` resolves and validates without writing.
    """
    return ops.tail(path, asset=asset, seconds=seconds, fade=fade, reset=reset, plan=plan)


@_tool()
def music(
    path: ProjectPath = None,
    *,
    asset: str | None = None,
    clip_id: str | None = None,
    word_index_start: int | None = None,
    word_index_end: int | None = None,
    phrase_start: str | None = None,
    phrase_end: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    clear_end: bool = False,
    src_in: float | None = None,
    crossfade: float | None = None,
    rotate: list[str] | None = None,
    passages: list[dict[str, Any]] | None = None,
    under: float | None = None,
    clear_under: bool = False,
    loudness: float | None = None,
    clear_loudness: bool = False,
    duck: float | None = None,
    clear_duck: bool = False,
    over_tail: bool | None = None,
    event: str | None = None,
    until_event: str | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the A2 music bed this project mixes under its edit.

    Call with no arguments to read what is in force. The bed stores word
    indices and an asset, never a length: it starts where `word_index_start`
    of `clip_id` (the VO transcript) lands on the timeline and runs to where
    `word_index_end` ends — or to the end of the edit — so a cut before either
    boundary moves both. Duration is derived at build time. On a recording
    with no words, `event`/`until_event` (and a passage's `event`) address
    its logged events instead.

    The first set needs `asset`, `clip_id` and a start (`word_index_start` or
    `phrase_start`) together; after that each field updates on its own. A
    field set by phrase stores the phrase beside the index it resolved to, so
    `cue_reresolve` can re-derive it; set by plain index, the stored phrase is
    cleared. Both boundaries are echoed with their resolved words and
    neighbours — check them.

    Beyond one asset from its head: `passages` (more pieces, each from its own
    word), `rotate` (assets in turn), `crossfade`, `src_in`; `under` levels the
    bed below the voice, or `loudness` to a LUFS where there is no voice;
    `duck` dips it while the voice speaks, keyed off the
    edit's own audio, audible insets and `ducks` sounds at export;
    `over_tail` plays it on under the end card. `export`'s `music` field says what the render
    carried. `clear_*` and `reset` undo each; `plan` validates without writing.
    """
    return ops.music(
        path,
        asset=asset,
        clip_id=clip_id,
        word_index_start=word_index_start,
        word_index_end=word_index_end,
        phrase_start=phrase_start,
        phrase_end=phrase_end,
        after=after,
        occurrence=occurrence,
        fade_in=fade_in,
        fade_out=fade_out,
        clear_end=clear_end,
        src_in=src_in,
        crossfade=crossfade,
        rotate=rotate,
        passages=passages,
        under=under,
        clear_under=clear_under,
        loudness=loudness,
        clear_loudness=clear_loudness,
        duck=duck,
        clear_duck=clear_duck,
        over_tail=over_tail,
        event=event,
        until_event=until_event,
        reset=reset,
        plan=plan,
    )


@_tool()
def vo_extend(
    path: ProjectPath = None,
    *,
    clip_id: str,
    word_index: int | None = None,
    seconds: float | None = None,
    plan: bool = False,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Open a gap in `clip_id`'s track for material the recording never had.

    The one tool allowed to grow the edit rather than cut it: a real hold in
    the VO, e.g. to let a line the footage carries play under it. Not the
    end card (`tail`), and not `restore`, which only brings back cut source.

    Addressed by `word_index` **or** `phrase` — the last word *before* the
    gap, which must be on the timeline — for `seconds`. The stretch is a real
    silent WAV, registered like any clip; a second call at the same `seconds`
    reuses it.

    **Read `covered_by`.** `build_shots` runs each shot to the next cue, so
    whatever picture was playing freezes across the hold by default, with
    `shots_error`, `verify` and `check_frames` all staying clean. It names
    every shot the gap now overlaps (`[]` with no cue table at all).

    Two consequences are permanent once a hold lands: `restore` refuses across
    the seam, and export always goes through the MLT writer. `plan=True`
    reports `covered_by` without writing; its `hold_clip_id` is a placeholder.
    """
    return ops.vo_extend(
        path, clip_id, word_index, seconds, plan=plan, phrase=phrase, after=after, occurrence=occurrence
    )


@_tool()
def vo_synth(
    path: ProjectPath = None,
    *,
    text: str,
    voice: str | None = None,
    candidates: int = 3,
    seed: int = 0,
    max_seconds: float = 20.0,
    clip_id: str | None = None,
    word_index: int | None = None,
    readback: bool = True,
    plan: bool = False,
    lexicon: str | None = None,
    flat_floor: float = 4.5,
    flat_weight: float = 0.002,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Say `text` in a cloned voice — render several seeds, rank them, read the winner back.

    Zero-shot Qwen3-TTS from a ≈19s reference clip (`voice`); there is no
    built-in voice. Seeds `seed .. seed+candidates-1` render in one process,
    each with `sim` (speaker-embedding likeness to the reference — a real take
    ≈0.99, a 3-semitone shift ≈0.96) and `spread` (voiced pitch movement).
    `chosen` is the best `sim` less a flatness penalty, since likeness alone
    keeps the flattest read. A render that hit `max_seconds` is `capped` and
    never wins while an uncapped one exists.

    The winner is read back through whisper and `heard`/`wer` reported — a
    clone that sounds right and says the wrong words is the failure nothing
    else sees. A report, never a gate.

    Renders are cached under `cache/synth/`, so a repeat spends no GPU. **The
    splice is not cached**: with `clip_id` + `word_index` the winner is
    registered and spliced in after that word through `vo_extend`'s mechanism
    (melt routing, `restore` refusing across the seam, a `covered_by`
    report), and calling again splices a second time — check the timeline or
    `undo` rather than re-calling. `plan=True` reports the ranking and splice
    preview from cached renders only, and says `rendered: False` rather than
    spending the GPU.
    """
    return ops.vo_synth(
        path,
        text,
        voice=voice,
        candidates=candidates,
        seed=seed,
        max_seconds=max_seconds,
        clip_id=clip_id,
        word_index=word_index,
        readback=readback,
        plan=plan,
        lexicon=lexicon,
        flat_floor=flat_floor,
        flat_weight=flat_weight,
    )


@_tool()
def hold_add(
    path: ProjectPath = None,
    *,
    clip_id: str,
    gap_word_index: int | None = None,
    cue_word_index: int | None = None,
    asset: str | None = None,
    word_index_first: int | None = None,
    word_index_last: int | None = None,
    gap_phrase: str | None = None,
    cue_phrase: str | None = None,
    asset_phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    head_margin: float | None = None,
    tail_margin: float | None = None,
    under: float | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Splice a hold into `clip_id` after `gap_word_index`: a real gap opens
    in the VO (`vo_extend`'s own mechanism, reused) and a picture cue pins
    `asset`'s own in-point, snapped to whole words with margin and
    **refused, never clamped**, when it cannot fit.

    Addressed by `(clip_id, gap_word_index)`, unique — a second `hold_add` at
    the same address is refused. `gap_word_index`/`cue_word_index`/
    `word_index_first`+`word_index_last` each also accept a phrase
    alternative: `gap_phrase` binds its **last** word (the gap opens right
    after it), `cue_phrase` binds its **first**, and `asset_phrase` resolves
    against `asset`'s own transcript and binds its first and last words to
    `word_index_first`/`word_index_last` together.

    Everything else is resolved live: `elapsed` (how long the VO plays
    between the cue and the gap), `src_start` (deterministically —
    `phrase_start - elapsed - head_margin`), and `hold_length` (the phrase's
    own span plus both margins). Refused, with the measured numbers, when
    there is no room or the asset runs out.

    Mix-only fields (`head_margin`/`tail_margin`/`under`/`fade_in`/
    `fade_out`) are re-settable on an already-spliced hold by calling again
    with the same address and no change to `word_index_first`/
    `word_index_last` — those two are one-way once spliced (`hold_rm` then
    `hold_add` again, or `proofcut undo`, are the only ways to resize one).

    `plan=True` resolves and reports without writing anything.
    """
    return ops.hold_add(
        path,
        clip_id,
        gap_word_index,
        cue_word_index,
        asset,
        word_index_first,
        word_index_last,
        gap_phrase=gap_phrase,
        cue_phrase=cue_phrase,
        asset_phrase=asset_phrase,
        after=after,
        occurrence=occurrence,
        head_margin=head_margin,
        tail_margin=tail_margin,
        under=under,
        fade_in=fade_in,
        fade_out=fade_out,
        plan=plan,
    )


@_tool()
def hold_rm(path: ProjectPath = None,
    *, clip_id: str, gap_word_index: int) -> dict[str, Any]:
    """Drop a hold's record and its owned cue — the spliced silence stays.

    `vo_extend`'s own irreversibility, inherited: there is no clean
    "un-splice", only `proofcut undo`. After this the gap reverts to being an
    ordinary manufactured silence, a coherent pre-existing state rather than
    a broken one.
    """
    return ops.hold_rm(path, clip_id, gap_word_index)


@_tool()
def hold_under(
    path: ProjectPath = None,
    *,
    clip_id: str,
    asset: str,
    word_index_start: int | None = None,
    word_index_end: int | None = None,
    phrase_start: str | None = None,
    phrase_end: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    under: float | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Play a film clip's own audio *under* a span of the VO, `under` LU below
    it (default 13) — no gap, unlike `hold_add`. The span is VO words
    (`word_index_start`/`word_index_end`, or `phrase_start`/`phrase_end`), and
    the audio reads from wherever the shot showing `asset` has got to at the
    span's first word, so `asset` must be on screen there — cue it first. A
    second call at the same `(clip_id, word_index_start)` replaces the entry;
    the music bed goes out across it. `plan` resolves without writing. Both
    boundary words are echoed with neighbours — check them.
    """
    return ops.hold_under(
        path,
        clip_id,
        asset,
        word_index_start=word_index_start,
        word_index_end=word_index_end,
        phrase_start=phrase_start,
        phrase_end=phrase_end,
        after=after,
        occurrence=occurrence,
        under=under,
        fade_in=fade_in,
        fade_out=fade_out,
        plan=plan,
    )


@_tool()
def hold_under_rm(path: ProjectPath = None, *, clip_id: str, word_index_start: int) -> dict[str, Any]:
    """Drop the film audio under the VO addressed by `(clip_id, word_index_start)`.

    The inverse of `hold_under`: that span plays the VO alone again, and the
    music bed — which a hold gates out — comes back across it. Refused when
    no entry sits at that address, so a second call says so rather than
    doing nothing quietly. The audio was a manifest entry, not a splice, so
    no word moves and `undo` restores it.
    """
    return ops.hold_under_rm(path, clip_id, word_index_start)


@_tool()
def hold_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every stored hold plus its live-resolved plan.

    A hold that cannot currently resolve is reported inline (`hold_error`),
    never raised. Each item also carries `cue_drift` — a check between the
    hold's own owned cue and what it would compute fresh right now, since
    nothing stops a plain `cue_rm`/`cue_add` on that exact word from an
    unrelated caller.
    """
    return ops.hold_ls(path)


@_tool()
def hold_check(path: ProjectPath = None,
    *, render: str) -> dict[str, Any]:
    """Transcribe each hold's own span off `render` and check its seams.

    For each stored hold: the required phrase, transcribed off the render at
    the hold's live-resolved span, plus the level right at each edge against
    the quiet floor just after it — "still loud" (a word cut off) or a
    "noise-floor cliff" (a hard drop with nowhere graceful to land).
    **Report, never refuse** — a post-hoc listening check on a render that
    already exists, `verify`'s and `film_check`'s own stance.
    """
    return ops.hold_check(path, render)


@_tool()
def finish_check(
    path: ProjectPath = None,
    *,
    final: str,
    holds: list[dict[str, Any]] | None = None,
    prepend_seconds: float | None = None,
    fps: float | None = None,
    duration_tolerance: float = 0.5,
    pix_th: float = 0.10,
    black_min_duration: float = 0.0,
    windowed_model: str | None = None,
    window: float = asr.WINDOW,
    overlap: float = asr.OVERLAP,
    recheck_pad: float = asr.WINDOW,
    language: str | None = None,
    clip_id: str | None = None,
    transcript_path: str | None = None,
) -> dict[str, Any]:
    """Check a **delivered** file against this project's timeline —
    `verify`/`check_frames`/`check_black`/`film_check` for a file an
    external mix pass produced, not one of proofcut's own renders.

    `final` carries a cold open and/or holds concatenated on outside proofcut,
    so every position this reports is in `final`'s own absolute seconds.
    `prepend_seconds` defaults to this project's stored head length; `holds`
    defaults to its stored holds, resolved live and offset the same way —
    pass either explicitly (an empty `holds` list included) to check a file
    against a different set than what is currently stored.

    Eight checks, none individually fatal to the others: stream/chapter/
    duration agreement against the timeline's own arithmetic; loudness
    (report only); blackdetect, with a run explained only when it falls
    inside the prepend or a hold's own span; each hold's own span
    transcribed and its seam levels measured; a windowed transcription of
    `final` diffed against the timeline's expected words, with every heard
    word inside the prepend or a hold filtered out first; every dropped run
    re-cut and re-transcribed on its own to catch a windowed-pass false miss
    at a window stitch (`boundary_misses`, recovered — a run that still
    cannot be found stays in `missing`, a real fault); and a self-repeat
    scan over the same filtered transcript. `faults`/`ok` aggregate all of
    it, and every run is logged (`finishlog`) so `proofcut review serve` can
    show a WARN badge keyed to the file's own sha256.
    """
    return ops.finish_check(
        path,
        final,
        holds=holds,
        prepend_seconds=prepend_seconds,
        fps=fps,
        duration_tolerance=duration_tolerance,
        pix_th=pix_th,
        black_min_duration=black_min_duration,
        windowed_model=windowed_model,
        window=window,
        overlap=overlap,
        recheck_pad=recheck_pad,
        language=language,
        clip_id=clip_id,
        transcript_path=transcript_path,
    )


@_tool("path", "dest")
def reel(
    path: ProjectPath = None,
    *,
    dest: Annotated[
        str,
        Field(
            description=(
                "Where the derived project is created. It is a project selector "
                "too, not a file, so a bound server confines it to the same tree "
                "as `path` rather than letting a reel be written anywhere on disk."
            )
        ),
    ],
    start: float,
    end: float,
    canvas: str | None = None,
    name: str | None = None,
    confirm_suspect: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Derive a new project at `dest` holding `[start, end)` of this timeline.

    `start`/`end` are render seconds naming the span to **keep** — the
    opposite direction from every other tool; the head and tail are cut
    through `cut_by_time`. Reach for this before setting a vertical `canvas`
    on a film: the canvas is project state, so pass the reel's shape here and
    it lands on the copy only.

    Media is linked, not copied. Descriptions and reframes carry over. Cues
    carry over only where the reel keeps their word — read `cues_dropped`
    **head-first**, since one pruned just outside the kept span opens the reel
    on no picture. Survivors are pinned to the film's in-points
    (`cues_pinned`, or `pins_error`). Cards are re-authored at the new canvas
    (`cards_unrecorded` names any that cannot be); `over_platform_cap` says if
    it still runs long for a vertical feed.

    Nothing after the film is inherited: `tail_dropped` and `music_dropped`
    name what the film had. An edge on a suspect-duration word — likely a
    hidden retake — refuses unless `confirm_suspect`; read `suspect_edges`.
    `plan=True` creates nothing.
    """
    return ops.reel(
        path,
        dest,
        start=start,
        end=end,
        canvas=canvas,
        name=name,
        confirm_suspect=confirm_suspect,
        plan=plan,
    )


@_tool()
def reframe(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    rect: str | None = None,
    pane: str | None = None,
    src_start: float | None = None,
    interp: bool = False,
    fill: str | None = None,
    reset: bool = False,
    plan: bool = False,
    ease: str | None = None,
    event: str | None = None,
) -> dict[str, Any]:
    """Read or set which part of each clip survives into the frame.

    What makes a swapped canvas fill the frame instead of pillarboxing it. The
    default is a centre crop, which is **wrong whenever the subject is not
    centred**. Call with no `clip_id` to read the crops in force for every
    clip; `clips[].windows` is each clip's whole series.

    A `rect` is a floor rather than a frame: grown to the canvas's shape, never
    shrunk into it, stored as asked and refit whenever the canvas moves; the
    reply gives both `asked` and the `crop` it became. `src_start` makes it a
    per-**shot** window, addressed on the source's own clock, so every
    placement of the clip picks it up. `pane` makes that window a stacked
    split for a shot one crop cannot hold; `interp` slides into it rather than
    stepping, and `ease` names that slide's curve; `event` addresses the window
    by a named instant instead of seconds; `fill="blur"` draws it whole over a blurred copy of itself
    instead of cropping, for a shot every crop loses something from.

    `reset` drops overrides (one clip, one window, or all); `plan` resolves
    without writing. Nothing here analyses the picture — `reframe_detect`
    proposes crops and writes through this tool. Judge a window on
    `reframe_sheet`, never on a watch: a wrong one reads as framing in motion.
    """
    return ops.reframe(
        path,
        clip_id,
        rect=rect,
        pane=pane,
        src_start=src_start,
        interp=interp,
        fill=fill,
        reset=reset,
        plan=plan,
        ease=ease,
        event=event,
    )


@_tool()
def reframe_detect(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    threshold: float = ops.SCENE_THRESHOLD,
    frames: int = ops.DETECT_FRAMES,
    apply: bool = False,
    split: bool = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Propose a framing window per camera shot, from where the faces are.

    Every placement is split at its camera cuts, each window sampled at a few
    moments and centred on the faces found. Against fifteen hand-framed,
    approved windows it beats the centre crop on every measure (0.755 mean
    overlap against 0.568).

    **It proposes; it does not frame.** `apply` is off by default: the pass is
    still about a quarter of a window's width out on average, and a wrong
    window reads as framing in motion. Look at `reframe_sheet` before
    applying. Applying writes through `reframe` and never over an existing
    override.

    **A window with no face is `refused`, never guessed at** — expect about
    one in seven — and nothing is written for it, so read `falls_back_to`: at
    a clip's head that is the centre crop, anywhere else the **previous
    shot's** framing. Nothing here chooses the subject either.

    A window one crop cannot hold comes back as a stacked split (`rect` and
    `pane`). Read `subjects` (per frame), not `faces`, which sums detections
    across samples and calls one face three. Needs `PROOFCUT_FACE`.
    """
    return ops.reframe_detect(
        path, clip_id=clip_id, threshold=threshold, frames=frames, apply=apply, split=split
    )


@_tool()
def reframe_coverage(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    threshold: float = ops.SCENE_THRESHOLD,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Which placed seconds are framed by a window chosen for an earlier shot.

    **The question `reframe_detect` cannot answer**: that one is about a
    proposal, this is about the project on disk. A refused proposal writes
    nothing, so a stretch can sit under a rect chosen for a shot that ended
    long before — 13.6s of one clip across four camera setups on the film,
    with the manifest, `status` and `reframe_sheet` all clean.

    Every placement is walked against its source's scene cuts. A cut with no
    window boundary within a frame of it opens a stale stretch. Read
    `stale_seconds` — an **override** held across a cut, which looks
    deliberate — not `default_seconds` (the centre crop, only the default
    doing what it always did). Each stretch carries `timeline_start`; the fix
    is `reframe_sheet` to look, then `reframe_detect` on the clip.

    **`steps` is the mirror, and the one a viewer notices**: a window boundary
    with no cut, where the frame slides sideways mid-take and reads as an edit
    that is not there. Each carries `shift` and `nearest_cut`.

    Needs no face detector, reads and never writes — but it decodes placed
    footage, so it is seconds, not free.
    """
    return ops.reframe_coverage(path, clip_id=clip_id, threshold=threshold)


@_tool()
def continuity_check(
    path: ProjectPath = None,
    *,
    gap: float = ops.CONTINUITY_GAP,
    min_shot: float = ops.CONTINUITY_MIN_SHOT,
    stub_tolerance: float = ops.CONTINUITY_STUB_TOLERANCE,
    stubs: bool = True,
    scene_threshold: float = ops.SCENE_THRESHOLD,
) -> dict[str, Any]:
    """Rewinds, replays, short shots, and film-internal-cut stubs — reports,
    never decides.

    **Rewind**: a shot lands behind where its own asset last left off, with
    under `gap` seconds of timeline since. **Replay**: an earlier shot's
    source range is re-shown, `gap` seconds or more later — reported, never
    refused, because a deliberate narrative rhyme and a mistake look
    identical from the cue table alone. **short_shot**: under `min_shot`
    seconds (stills excluded). **stub**: a shot ends or begins right where
    its own footage has a real internal cut — likely a fragment rather than
    the shot itself.

    The stored cold open (`head`) is walked as a pseudo-shot before the
    first real one, so a body shot that rewinds into the head's own footage
    is caught the same way a body-to-body rewind is. Overrun is never a
    finding: `mlt.plan_picture` already refuses it structurally, so nothing
    reaches this walk having overrun its asset.

    `stubs=True` costs a scene-cut decode per distinct asset placed —
    `stubs=False` skips it. `scene_threshold` defaults to the pinned 0.15 but
    is caller-settable: darker footage from a different film has needed 0.12.

    Findings already acknowledged by `continuity_accept` are dropped unless
    the shot moved under the mark, in which case they are kept and marked
    `accepted_stale: True` rather than silently re-suppressed.
    """
    return ops.continuity_check(
        path,
        gap=gap,
        min_shot=min_shot,
        stub_tolerance=stub_tolerance,
        stubs=stubs,
        scene_threshold=scene_threshold,
    )


@_tool()
def continuity_accept(path: ProjectPath = None,
    *, clip_id: str, word_index: int, kind: str) -> dict[str, Any]:
    """Acknowledge one continuity finding once — a deliberate rhyme, never
    re-reported every run.

    Addressed the way a cue is (`clip_id`, `word_index`), plus `kind`, since
    one shot can carry more than one finding. Stores a fingerprint of the
    finding's own numbers; a later run whose recomputed fingerprint disagrees
    means the shot moved under the mark, and the finding is reported again
    rather than trusted blindly. Refuses when no finding of `kind` currently
    sits at that cue — `continuity_check` first, then accept what it found.
    """
    return ops.continuity_accept(path, clip_id, word_index, kind)


@_tool()
def continuity_reject(path: ProjectPath = None,
    *, clip_id: str, word_index: int, kind: str) -> dict[str, Any]:
    """Unmark a continuity finding, putting it back into `continuity_check`.

    The inverse of `continuity_accept`: the acknowledgement is dropped from
    the manifest, so every later run reports that finding again instead of
    passing over it. Addressed exactly as it was accepted (`clip_id`,
    `word_index`, `kind`), and refused when no accepted finding of that kind
    sits there — so a second call says so rather than quietly doing nothing.
    Nothing on the timeline moves either way; an acknowledgement is a
    manifest entry, and `undo` puts it back.
    """
    return ops.continuity_reject(path, clip_id, word_index, kind)


@_tool()
def continuity_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every accepted continuity finding, with whether it is still live and
    whether it still matches what was accepted (`stale`).

    A finding that has disappeared entirely — the shot was re-cued away, or
    the issue was fixed — reports `still_found: False` rather than `stale`,
    since there is nothing live left to disagree with the mark.
    """
    return ops.continuity_ls(path)


# `-> Any` for the reason spelled out above `shot_sheet` below: a concrete
# return annotation makes the SDK validate an `Image` against a JSON output
# schema, and the tool then answers `is_error` from a perfectly correct body.
@_tool()
def reframe_sheet(
    path: ProjectPath = None,
    *,
    out: str | None = None,
    moments: list[float] | None = None,
    extremes: bool = False,
    page: int = 0,
    per_page: int | None = ops.REFRAME_SHEET_PER_PAGE,
) -> Any:
    """Draw every placement's framing window on its own source frames.

    **A framing decision is unreviewable without this.** The hand-framed
    teaser had 2 of its 15 windows wrong and neither was visible in motion —
    a badly-placed window reads as framing. Drawn on the whole source frame,
    what the window leaves out sits right beside it.

    Every placement the render shows is walked window by window, the window in
    force drawn in red and labelled with its rect. **A row is a window shown,
    not a placement**: each placement is split at the boundaries it crosses,
    so a window covering a small slice of a long placement still gets a row.
    `window` on a row is the source address `reframe --src-start` takes;
    `windows` is how many the whole placement crosses. Stills come back under
    `skipped` — a card is re-authored, never cropped.

    **A tile is evidence about an instant, not an approval of the span.** A
    static rect over a moving subject has a best moment and a sample can land
    on it; `extremes` draws where the subject is leftmost, median and
    rightmost instead, worst first, with `worst_offset` on the row to sort by.

    **A page of rows comes back as an image**, six windows by default, at a
    width vision reads verbatim; `row` keeps its project-wide number on every
    page. `per_page: null` is the whole project as a PNG path, for a person.
    """
    report = ops.reframe_sheet(
        path, out=out, moments=moments, extremes=extremes, page=page, per_page=per_page
    )
    if not report.get("sheet") or per_page is None:
        return report
    return [report, Image(path=report["sheet"])]


# `-> Any` is load-bearing and is not laziness. The SDK builds an output
# schema from a concrete return annotation and then validates the return
# against it — and an `Image` is not JSON, so `-> list[Any]` comes back as
# `is_error: true` with "Unable to serialize unknown type", while the tool
# body is perfectly correct. `-> list[ContentBlock]` fails the same way, with
# 13 validation errors. Measured over a real stdio server, 2026-08-24: `Any`
# and no annotation at all are the two that work, and `Any` is the one that
# matches this file. The trap is that the obvious build — annotate it like
# every other tool here — produces a tool that looks written and never
# returns a picture.
@_tool()
def shot_sheet(
    path: ProjectPath = None,
    *,
    page: int = 0,
    per_page: int = ops.SHOT_SHEET_PER_PAGE,
    out: str | None = None,
) -> Any:
    """Look at the picture track — one labelled tile per shot, as an image.

    **This is the tool to call to see what the film looks like** — the picture
    track of the edit as it stands. Its neighbours answer different questions
    with the same kind of picture: `footage_sheet` browses one registered
    clip's own material, `contact_sheet` looks at a clip's first ten seconds,
    and `reframe_sheet` reviews framing windows a page at a time. All four
    hand the bytes back, because an agent confined to proofcut's tools (the
    agent panel's `--tools ToolSearch`) can open no path at all.

    One tile per shot, at the exact source second that shot reads from, four
    across and about two dozen a page — the measured ceiling before vision
    downscales the sheet and takes the labels with it. Each tile is labelled
    `asset t=<timeline second>s src=<source second>s`, and `page` walks a
    longer film.

    `asset` on a row is the *footage*; `clip_id` is the transcript the cue is
    addressed against, which on a voiceover project is the VO and not
    anything you can see. Read `asset`.

    Drawn from the same projection `export` renders, so a plan that refuses
    comes back as `shots_error` with no sheet rather than a picture of a film
    that will not render.

    **What you see here is a hypothesis, not a check.** Nothing downstream
    reads a verdict formed off this sheet — confirm one with an op that
    measures (`check_frames`, `verify`, `black`, `reframe_coverage`).

    `out` is the one thing here that writes where you say: the montage lands
    at that path, **replacing whatever file is there**. Without it a page is
    written into the project's own sheet cache, which nothing reads back as
    authored state.
    """
    report = ops.shot_sheet(path, page=page, per_page=per_page, out=out)
    if not report.get("sheet"):
        return report
    return [report, Image(path=report["sheet"])]


# `-> Any` for the reason spelled out above `shot_sheet`: a concrete return
# annotation makes the SDK validate an `Image` against a JSON output schema
# and the tool answers `is_error` from a correct body.
@_tool()
def footage_sheet(
    path: ProjectPath = None,
    *,
    clip_id: str,
    mode: str = "auto",
    interval: float = ops.FOOTAGE_SHEET_INTERVAL,
    page: int = 0,
    per_page: int = ops.SHOT_SHEET_PER_PAGE,
    out: str | None = None,
    ctx: Context | None = None,
) -> Any:
    """Look at a clip's own footage — one labelled tile per moment, as an image.

    **The tool to see what is *in* some footage**, as opposed to
    `shot_sheet`, which shows an existing edit's picture track. It needs no
    edit, cues or transcript, so it is the first look at b-roll, recordings
    and gameplay — material `describe` can search by text but cannot show.
    The bytes come back in the reply.

    `mode` picks the instants: `auto` (described windows if the clip has any,
    else the interval), `interval`, `describe` (each tile beside its window's
    sentence), or `scenes` (one per detected cut — opt-in, since a continuous
    take has none and a scan decodes the whole clip). `page` walks a long
    recording. A tile with nothing in it is marked `[blank]` on the picture,
    so a black square is never mistaken for a frame that failed to extract.

    **What you see is a hypothesis, not a check** — and this sheet is read to
    *choose* footage. `synopsis` is where a person says what a clip is; a tile
    shows what the camera saw, which is a different fact.
    """
    report = ops.footage_sheet(
        path, clip_id, mode=mode, interval=interval, page=page, per_page=per_page, out=out
    )
    if not report.get("sheet"):
        return report
    return [report, Image(path=report["sheet"])]


@_tool()
def thumbnail(
    path: ProjectPath = None,
    *, clip_id: str, at: float, interval: float = ops.THUMB_INTERVAL
) -> dict[str, Any]:
    """One filmstrip frame for `clip_id`, at the source time nearest `at`.

    `at` snaps to a multiple of `interval` before anything is extracted, and
    the frame is cached under `cache/thumbs/` keyed by the clip's media size
    and mtime — a repeated ask for a nearby instant is a cache hit. The
    result is a path, not the image bytes; `proofcut web` serves those over
    `/api/thumb/<clip_id>?at=`. It never enters the manifest, so nothing
    that renders can reach it (the same wall the preview proxy has).
    """
    return ops.thumbnail(path, clip_id, at, interval=interval)


# `-> Any` for the reason spelled out above `shot_sheet`.
@_tool()
def contact_sheet(
    path: ProjectPath = None,
    *,
    clip_id: str,
    seconds: float = ops.FIRST_LOOK_SECONDS,
    interval: float = ops.FIRST_LOOK_INTERVAL,
) -> Any:
    """Look at a clip's own head — the first look, as an image.

    **The sheet to call before cueing anything to a clip you have not seen.**
    Two shots of the film were cued to a clip's own head and got 4.5s of
    "BASED ON THE NOVEL BY THOMAS HARRIS" over black, because nobody had
    looked at its first seconds. Ten seconds at 1.5s spacing by default, each
    tile labelled with the source second it is.

    The frames come from `thumbnail()`'s cache — no new cache location, no new
    manifest key, no new web route — and the montage of them comes back here
    as bytes, since an agent confined to proofcut's tools (the agent panel's
    `--tools ToolSearch`) cannot open a path. `import_media` makes the frames for every clip it registers,
    so this is usually a cache hit; call it to *see* them, to look further
    than ten seconds, or to redraw after a re-import.

    An audio-only clip returns `frames: []` and no sheet, not a refusal — the
    same "nothing to look at is not a failure" as `check_frames`. A box
    without `magick` returns the frames and a `sheet_error`.
    """
    report = ops.contact_sheet(path, clip_id, seconds=seconds, interval=interval)
    if not report.get("sheet"):
        return report
    return [report, Image(path=report["sheet"])]


@_tool()
def synopsis(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    text: str | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Read, set or clear what a clip *is* — the corpus b-roll gets chosen from.

    No `clip_id` lists every clip's synopsis and which are missing one;
    `clip_id` alone reads one; `text` writes; `clear` removes.

    A synopsis is a different fact from a `describe` window. A description
    says what is in front of the camera — rooms, clothing, lighting. A
    synopsis says what the footage is: the work, the scene, the people, and
    whatever else decides whether it belongs under a sentence. It is meant to
    carry what no camera can see, because that is where the signal turned out
    to be — measured on real footage, the vision index chose the same clip a
    human did 2 times in 25, and this catalogue read by something that knows
    the material chose it 13.

    Write these yourself. Nothing generates them: a model looking at the
    pixels cannot, and guessing a title from a filename would produce
    confident wrong placements rather than an obviously empty catalogue.
    """
    return ops.synopsis(path, clip_id, text, clear=clear)


@_tool()
def events(
    path: ProjectPath = None,
    *,
    clip_id: str | None = None,
    source: str | None = None,
    name: str | None = None,
    origin: str | None = None,
    offset: float = 0.0,
    at: float | None = None,
    event: str | None = None,
    clear: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Named instants in a recording — the anchors a screen recording has instead of words.

    An event is `(name, seconds into the clip's own recording)`: `sent`,
    `typing_started`, a keystroke. No `clip_id` counts every clip's; `clip_id`
    alone lists one clip's, each with the `address` other tools take (`name`,
    or `name#k` when the name repeats, k from 0); `event` resolves one address
    and echoes three neighbours either side.

    `source` imports a recorder's JSON and REPLACES the clip's events of the
    names it brings (other names are kept), so a repeat import is a no-op and a
    marks file and a keystroke file combine. A recorder usually logs wall-clock stamps: pass
    `origin` naming the key that holds the recording's start. A set with any
    event outside the clip is refused whole, because a wrong clock moves every
    event by the same amount. `name` + `at` adds one event by hand.

    Events index the source, so no cut invalidates one; `locate` with `event=`
    says where one plays now, and `present: false` means it was cut.
    """
    return ops.events(
        path,
        clip_id,
        source=source,
        name=name,
        origin=origin,
        offset=offset,
        at=at,
        event=event,
        clear=clear,
        plan=plan,
    )


@_tool()
def overlay_add(
    path: ProjectPath = None,
    *,
    card: str,
    clip_id: str,
    word_index: int | None = None,
    phrase: str | None = None,
    event: str | None = None,
    until_word_index: int | None = None,
    until_phrase: str | None = None,
    until_event: str | None = None,
    seconds: float | None = None,
    after: int = -1,
    occurrence: int | None = None,
    enter: str | None = None,
    enter_seconds: float | None = None,
    enter_ease: str | None = None,
    leave: str | None = None,
    leave_seconds: float | None = None,
    leave_ease: str | None = None,
    position: int | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Draw a transparent card over the film — a lower third, or the scrim under one.

    Make the card first with card_new from `lowerthird` (a headline and an
    optional footnote, bottom left) or `scrim` (a dark gradient for type to sit
    on). The span starts at a word, phrase or event of `clip_id` and ends at a
    word, phrase, event or length; it is resolved through the timeline on
    every build and never stored as seconds, so cuts move it, and a cut
    through its start word makes export refuse until it is moved.

    The stack is list order: a later overlay draws over an earlier one it
    overlaps. Put the scrim first (or `position=0`), then the lowerthird. A
    staggered footnote is its own lowerthird with an empty headline, starting
    later. The reply echoes the words or event each end resolved to and
    where it plays; `plan=true` writes nothing. Any overlay routes export
    through the MLT writer, and export's reply lists the overlays it drew.
    """
    return ops.overlay_add(
        path,
        card,
        clip_id,
        word_index,
        phrase=phrase,
        event=event,
        until_word_index=until_word_index,
        until_phrase=until_phrase,
        until_event=until_event,
        seconds=seconds,
        after=after,
        occurrence=occurrence,
        enter=enter,
        enter_seconds=enter_seconds,
        enter_ease=enter_ease,
        leave=leave,
        leave_seconds=leave_seconds,
        leave_ease=leave_ease,
        position=position,
        plan=plan,
    )


@_tool()
def overlay_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every overlay, bottom of the stack first, with where each plays now.

    Each carries its `position` (as overlay_rm takes it), its lane, and its
    timeline span. A stack that cannot resolve — a cut removed a start word —
    comes back as `overlays_error` beside the stored records, so the one to
    move or remove can still be found.
    """
    return ops.overlay_ls(path)


@_tool()
def overlay_rm(path: ProjectPath = None, *, position: int, plan: bool = False) -> dict[str, Any]:
    """Take one overlay off the film, by its position in overlay_ls.

    The card stays under assets/cards/, so it can be placed again. Positions
    above the removed one move down by one.
    """
    return ops.overlay_rm(path, position, plan=plan)


@_tool()
def inset_add(
    path: ProjectPath = None,
    *,
    clip_id: str,
    asset: str,
    rect: list[int],
    word_index: int | None = None,
    phrase: str | None = None,
    event: str | None = None,
    until_word_index: int | None = None,
    until_phrase: str | None = None,
    until_event: str | None = None,
    seconds: float | None = None,
    src_in: float = 0.0,
    after: int = -1,
    occurrence: int | None = None,
    enter: str | None = None,
    enter_seconds: float | None = None,
    enter_ease: str | None = None,
    leave: str | None = None,
    leave_seconds: float | None = None,
    leave_ease: str | None = None,
    dim: float = 0.0,
    gain_db: float | None = None,
    mute: bool = False,
    position: int | None = None,
    level: str | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Draw a clip into a rectangle of the recording, following its camera — a render inside the app's preview.

    `rect` is where the recording shows what the inset replaces, in the
    recording's own pixels, and must be the clip's shape. The inset moves and
    zooms with the recording's reframe windows, so a push into the preview
    fills the frame with the clip itself, at full sharpness. The span starts
    at a word, phrase or event of `clip_id` and ends at one, at a length, or
    at the clip's end; it plays at 1x and must lie inside one continuous, 1x
    stretch of the recording (no cut, no retimed span under it).

    It fades in and out by default, can dim the recording around it, and plays
    its own audio with the music bed out underneath (dipped, with a duck)
    unless `mute`; `level="speech"` measures it and sets `gain_db`. The reply
    echoes where it plays and `dest`, where its rect lands in the canvas;
    `plan=true` writes nothing. Any inset routes export through the MLT
    writer, and export's reply gives each inset's rect at its first and last
    frame.
    """
    return ops.inset_add(
        path,
        clip_id,
        asset,
        rect,
        word_index,
        phrase=phrase,
        event=event,
        until_word_index=until_word_index,
        until_phrase=until_phrase,
        until_event=until_event,
        seconds=seconds,
        src_in=src_in,
        after=after,
        occurrence=occurrence,
        enter=enter,
        enter_seconds=enter_seconds,
        enter_ease=enter_ease,
        leave=leave,
        leave_seconds=leave_seconds,
        leave_ease=leave_ease,
        dim=dim,
        gain_db=gain_db,
        mute=mute,
        position=position,
        level=level,
        plan=plan,
    )


@_tool()
def inset_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every inset, bottom of the stack first, with where each plays and where its rect lands.

    Each carries its `position` (as inset_rm takes it). Insets that cannot
    resolve — a cut removed a start word — come back as `insets_error` beside
    the stored records, so the one to move or remove can still be found.
    """
    return ops.inset_ls(path)


@_tool()
def inset_rm(path: ProjectPath = None, *, position: int, plan: bool = False) -> dict[str, Any]:
    """Take one inset off the recording, by its position in inset_ls.

    The clip stays registered. Positions above the removed one move down by one.
    """
    return ops.inset_rm(path, position, plan=plan)


@_tool()
def follow(
    path: ProjectPath = None,
    *,
    clip_id: str,
    after: str,
    at_event: str | None = None,
    src_start: float | None = None,
    src_end: float | None = None,
    from_event: str | None = None,
    until_event: str | None = None,
    dissolve: float = 0.0,
    ease: str = "linear",
    plan: bool = False,
) -> dict[str, Any]:
    """Put a second recording on the timeline after the first, cut or dissolved.

    The window's recording, then the terminal's: `clip_id` plays its span
    spliced in after `after`, and everything after moves later. Words, events,
    retime stretches and reframe windows of either clip keep their meaning, so
    the incoming clip is retimed and framed like any other. `dissolve` seconds
    crossfade into it from its own frames before the in-point; the film is no
    longer for it. The reply gives the join and the dissolve; `plan=true`
    writes nothing, and `undo` takes the splice back.
    """
    return ops.follow(
        path, clip_id, after, at_event=at_event, src_start=src_start, src_end=src_end,
        from_event=from_event, until_event=until_event, dissolve=dissolve, ease=ease, plan=plan,
    )  # fmt: skip


@_tool()
def dissolve(
    path: ProjectPath = None,
    *,
    clip_id: str,
    src_start: float,
    seconds: float,
    ease: str = "linear",
    plan: bool = False,
) -> dict[str, Any]:
    """Set, change or clear the crossfade at a join `follow` made.

    Addressed by the incoming clip and where it starts there, never a timeline
    second, so a cut elsewhere moves it. `seconds=0` makes the join a cut
    again. Checked against the timeline before writing; `plan=true` writes
    nothing.
    """
    return ops.dissolve_set(path, clip_id, src_start, seconds, ease=ease, plan=plan)


@_tool()
def retime_add(
    path: ProjectPath = None,
    *,
    clip_id: str,
    seconds: float,
    word_index: int | None = None,
    phrase: str | None = None,
    event: str | None = None,
    until_word_index: int | None = None,
    until_phrase: str | None = None,
    until_event: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Play a span of the film faster or slower: "from event sent to event words in 1.0 s".

    The span starts at a word, phrase or event of `clip_id` and ends at one;
    it is resolved through the timeline on every build, never stored as
    seconds. Everything outside every stretch plays at 1x, with the speed
    eased in and out over half a second either side. The film's own audio is
    muted wherever it plays off speed; music, sounds, overlays and captions
    keep 1x and land where the retime puts their moment. Stretches may not
    overlap.

    The reply gives the span in Edit seconds and render seconds, its speed,
    and the render's new length; `plan=true` writes nothing. Any retime routes
    export through the MLT writer. A film-audio hold cannot share a project
    with a retime yet, and the window previews at 1x.
    """
    return ops.retime_add(
        path,
        clip_id,
        seconds,
        word_index,
        phrase=phrase,
        event=event,
        until_word_index=until_word_index,
        until_phrase=until_phrase,
        until_event=until_event,
        after=after,
        occurrence=occurrence,
        plan=plan,
    )


@_tool()
def retime_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every retime stretch, in the order added, with where each plays now.

    Each carries its `position` (as retime_rm takes it), its Edit and render
    spans and its speed, beside the render's length. A retime that cannot
    resolve — a cut removed an end word — comes back as `retime_error` beside
    the stored records.
    """
    return ops.retime_ls(path)


@_tool()
def retime_rm(path: ProjectPath = None, *, position: int, plan: bool = False) -> dict[str, Any]:
    """Take one stretch away, by its position in retime_ls; that span plays at 1x again."""
    return ops.retime_rm(path, position, plan=plan)


@_tool()
def sound_add(
    path: ProjectPath = None,
    *,
    assets: list[str],
    clip_id: str,
    word_index: int | None = None,
    phrase: str | None = None,
    event: str | None = None,
    every: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    gain_db: float = 0.0,
    jitter_db: float = 0.0,
    min_gap: float | None = None,
    src_in: float | None = None,
    src_out: float | None = None,
    ducks: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Play a one-shot sound at a word, an event, or every event of one name.

    Import the sound first (import_media), or call sound_generate for a ready
    UI set: eight key ticks, send, land, strike. `every="key"` puts a tick on
    each keystroke event; pass all eight keys as `assets` so the run does not
    repeat one sample, and a `jitter_db` of 2-3 to vary them. Hits land to the
    millisecond, between frames, and are resolved through the timeline on
    every build: a cut moves them, an `every` hit a cut removed is skipped,
    and a single hit whose word or event is cut makes export refuse until it
    is moved. The picks and jitter repeat on every build. `src_in`/`src_out`
    trim what plays. The music's duck hears a sound only with `ducks`. The reply counts the hits and echoes the first few; `plan=true`
    writes nothing. Export's reply lists the sounds it placed.
    """
    return ops.sound_add(
        path,
        assets,
        clip_id,
        word_index,
        phrase=phrase,
        after=after,
        occurrence=occurrence,
        event=event,
        every=every,
        gain_db=gain_db,
        jitter_db=jitter_db,
        min_gap=min_gap,
        src_in=src_in,
        src_out=src_out,
        ducks=ducks,
        plan=plan,
    )


@_tool()
def sound_ls(path: ProjectPath = None) -> dict[str, Any]:
    """Every sound record, with how many hits each places now.

    Each carries its `position` (as sound_rm takes it), `hit_count`, how many
    occurrences were `skipped` (cut) and `thinned` (too close), and its first
    hits. Records that cannot resolve come back as `sounds_error` beside the
    stored records.
    """
    return ops.sound_ls(path)


@_tool()
def sound_rm(path: ProjectPath = None, *, position: int, plan: bool = False) -> dict[str, Any]:
    """Take one sound record off the film, by its position in sound_ls.

    The clip stays registered. Positions above the removed one move down.
    """
    return ops.sound_rm(path, position, plan=plan)


@_tool()
def sound_generate(path: ProjectPath = None) -> dict[str, Any]:
    """Write proofcut's generated UI sounds into the project and import them.

    Registers `sfx-key_0` … `sfx-key_7` (soft key ticks), `sfx-send` (a click),
    `sfx-land` (a thump and chime, for a result arriving) and `sfx-strike` (a
    falling swipe), synthesised, under assets/sounds/. Ids already registered
    are left alone. Use them with sound_add.
    """
    return ops.sound_generate(path)


@_tool()
def broll_brief(path: ProjectPath = None,
    *, fps: float | None = None) -> dict[str, Any]:
    """The whole b-roll question as data: what there is, and what it goes under.

    Returns the footage catalogue with each clip's `synopsis` and duration,
    then every shot position on the timeline with the narration that plays
    over it, how long it is held, and what is currently there. `card: true`
    positions are shown for rhythm and are not choices.

    This is the half proofcut can do. Choosing is the other half, and it belongs
    to you: read the brief, decide which clip goes under which sentence, and
    write the answers back with `cue_add`, where the picture plan checks each
    one. Ranking the catalogue by text similarity was measured and does not
    work — the sentence that earns a clip routinely shares no word with any
    description of it.

    `missing_synopsis` is the thing to fix first. A clip with no synopsis is
    invisible to any reasoning about the catalogue, so it will simply never
    be chosen.
    """
    return ops.broll_brief(path, fps=fps)


@_tool()
def verify(
    path: ProjectPath = None,
    *,
    render: str,
    clip_id: str | None = None,
    transcript_path: str | None = None,
    model: str | None = None,
    language: str | None = None,
    windowed: bool = False,
    # Bound to the module constants rather than restated: a stale literal here
    # is a tool whose schema advertises a default the CLI no longer uses.
    window: float = asr.WINDOW,
    overlap: float = asr.OVERLAP,
) -> dict[str, Any]:
    """Transcribe a finished render and diff it against what the timeline says.

    Run this after rendering, before calling an edit done. It transcribes the
    render with whisper and compares that word sequence to the one the timeline
    should play, which is the only check that catches a retake still in the
    picture: whisper collapses an immediate repeat into a single utterance, so a
    doubled phrase can be invisible in the source transcript and still be in the
    render.

    Read `repeated` first — an entry there is a phrase the render plays more
    times than the timeline expects, i.e. a surviving retake, with the heard word
    index to look at. `dropped` is the opposite: words the timeline expects that
    the render never says, usually a cut that reached too far.

    **A clean single-pass result is not proof.** This check has a known blind
    spot: the render's transcript is itself one whisper pass, which collapses a
    repeat the same way the source transcript did — three retakes survived a
    correct run of it on a real video. Set `windowed=True` to transcribe in
    short overlapping windows instead, which is what found them. It costs one
    whisper run over 2x the audio and uses a deliberately smaller model, so
    run the default first and escalate to it before calling an edit finished.

    `loud_gaps` comes back either way and trusts no transcript: it measures the
    render's own energy and reports holes in the heard word map that hold sound
    anyway. An entry is a place to *listen*, not a verdict — a music bed or an
    attenuated noise can produce one. Read `speech_db`/`threshold_db` beside it.

    `similarity` around 0.97 is normal on a *clean* render — whisper spells its
    own output differently on a second pass ("whodunit" / "who done it", "4" /
    "four"). Treat it as triage; `diff` is the artifact. Transcription takes
    minutes on a long render, and the result is cached under
    cache/verify/ and reported as `heard_transcript` — pass it back as
    `transcript_path` to re-diff without re-transcribing.
    """
    return ops.verify(
        path,
        render,
        clip_id=clip_id,
        transcript_path=transcript_path,
        model=model,
        language=language,
        windowed=windowed,
        window=window,
        overlap=overlap,
    )


@_tool()
def check_frames(path: ProjectPath = None,
    *, target: str | None = None, fps: float | None = None) -> dict[str, Any]:
    """Check an export's frame count against what the timeline says it should be.

    The picture-side counterpart to `verify`, which covers only the audio. Run
    this on the exported NLE project **before** rendering — that is where it is
    worth the most, because the count settles whether the cut positions are
    right for the price of reading a document rather than encoding one.

    `target` is an NLE project (.kdenlive/.mlt/.xml, put to `melt -consumer
    xml`) or a finished render (counted with ffprobe). Omit it to just report
    `expected_frames`, the total the timeline lays down.

    Read `agrees` first, then `delta` — how many frames the target has that the
    timeline does not. A non-zero delta on an NLE project means the render will
    not be the length the edit is, and `notes` says so when the cause is one
    proofcut already knows about. `agrees` is null, not false, for an audio-only
    render: it has no frames, so nothing was checked.

    `fps` must match the rate the export ran at or the two sides are counting on
    different grids; it defaults to the rate `export` would have picked.
    """
    return ops.check_frames(path, target, fps=fps)


@_tool()
def film_check(
    path: ProjectPath = None,
    *,
    reference: str | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Compare this project against the export it is supposed to be.

    check_frames answers whether an export agrees with *this project's own*
    arithmetic; it cannot catch this project being the wrong film to begin
    with — a project can pass every check it has and still be seeded from a
    stale stage of an outside edit (HISTORY.md § The VO the project was
    holding: 73 segments/410.963s sat in a project whose shipped film was 63
    segments/336.269s, with the render, verify, the cue table and the shot
    plan all agreeing with the wrong one). This checks the project's
    `timeline_duration` against a reference file's own ffprobe duration —
    cheap, no frame counting, no melt. Segment count has nothing on the
    reference side to compare against once a film is encoded, so `segments`
    is reported alone and the notes say why.

    `reference` is remembered: passing it stores it on the project
    (additive, no schema bump), so a later call with no argument re-asks the
    same question against the same file. `reset` drops the stored reference;
    `plan` resolves without writing. With no reference given or stored, this
    reports the project's own numbers and says there is nothing to compare
    them against, rather than raising.
    """
    return ops.film_check(path, reference, reset=reset, plan=plan)


@_tool()
def import_edit(
    path: ProjectPath = None,
    *,
    document: str,
    clip_id: str | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Lay a cut made in Kdenlive down as this project's timeline.

    The supported way to bring an outside edit in. `seed_timeline` lays a clip
    down and lets auto-editor find the cuts; this takes a `.kdenlive` (or
    `.mlt`) playlist somebody already trimmed by hand and reads its surviving
    ranges into the timeline. It replaces the whole timeline, and the previous
    one is snapshotted first, so it is undoable like any other mutation —
    which is the part the hand-rolled version of this never had (HISTORY.md
    § The VO the project was holding: 63 ranges were parsed out of a
    `.kdenlive` and written straight to `Edit`, bypassing `cut` and its
    history).

    Every clip the document references has to be **registered already** — the
    resources are matched against registered clips by resolved path, and any
    that do not match are named rather than imported behind your back. Pass
    `clip_id` for a single-source document whose media sits at a path this
    project does not know.

    Ranges that overrun a clip's registered duration are clamped and reported
    in `overshot`, never silently dropped: auto-editor's own exports overshoot
    the tail by one frame, so a clean `overshot` is worth reading rather than
    assuming. `plan` resolves and checks without writing.

    Refused by name rather than half-read: a `<blank>` in the playlist (real
    runtime an `Edit` has nowhere to put), and two playlists carrying
    different cuts (a multi-track picture edit, which proofcut's one linked A/V
    track has no shape for).
    """
    return ops.import_edit(path, document, clip_id=clip_id, plan=plan)


@_tool()
def check_black(
    path: ProjectPath = None,
    *,
    target: str,
    fps: float | None = None,
    pix_th: float = 0.10,
    min_duration: float | None = None,
) -> dict[str, Any]:
    """Scan a render for black stretches, and say whether each is the known
    kdenlive-export tail frame (picture.KNOWN_TAIL_FRAME) or a genuine defect.

    `target` is required — unlike check_frames, there is no cheap no-target
    mode; there is nothing to detect black in without a render. A run is
    only ever explained when it sits at the tail *and* the frame delta
    against the timeline matches the known defect exactly; a black run
    inside the declared picture is always reported as a real defect.
    """
    return ops.check_black(path, target, fps=fps, pix_th=pix_th, min_duration=min_duration)


# `-> Any` for the reason spelled out above `shot_sheet`.
@_tool()
def spot_frames(
    path: ProjectPath = None,
    *,
    target: str,
    count: int = 6,
    times: Sequence[float] | None = None,
    fps: float | None = None,
) -> Any:
    """Pull `count` evenly-spaced frames (plus any explicit `times`) from a
    render as PNGs with signalstats luma, ranked darkest-first.

    When `target`'s own probed duration still matches the current timeline
    within a frame (`mapping_trusted`), each frame also reports which
    clip/word it lands near via `Edit.source_at` — refused, not guessed,
    when the render looks stale.

    Like `shot_sheet`/`footage_sheet`/`contact_sheet`, the reply also carries
    a montage of the sampled frames as an image — `frames[].png` is a path,
    and an agent confined to proofcut's tools (the agent panel's `--tools
    ToolSearch`) has no `Read` to open one (TRIAL.md § `spot_frames` hands
    back paths the agent cannot open).
    """
    report = ops.spot_frames(path, target, count=count, times=times, fps=fps)
    if not report.get("sheet"):
        return report
    return [report, Image(path=report["sheet"])]


@_tool()
def speech_overlap(
    path: ProjectPath = None,
    *,
    clip_id: str,
    at: float = 0.0,
    clip_in: float | None = None,
    clip_out: float | None = None,
    vo_clip_id: str | None = None,
    max_gap: float = 0.3,
    min_seam: float = 0.5,
    cap: float = energy.CAP,
    clip_evidence: str = "auto",
) -> dict[str, Any]:
    """Does a proposed placement of `clip_id` overlap the VO's speech?

    The prerequisite check behind "can this clip speak here?" — answer it
    before designing any ducking. `at`/`clip_in`/`clip_out` describe where
    `clip_id` would sit on the timeline (defaults: unplaced at 0, its whole
    duration) — the clip need not be on the timeline yet, and usually isn't,
    since the current model is single-track. VO's own words map through the
    existing edit (`Edit.timeline_span`); `clip_id`'s map by offsetting into
    the proposed window instead. Both sides are trimmed with
    `energy.believable` first — an inflated word duration can hide a real
    seam — then merged into speech runs with `max_gap` tolerance, since a
    0.05s gap is not a usable seam.

    Read `overlaps` first: any entry means placing `clip_id` there would step
    on VO speech, not empty air — this caught exactly that on Billy/Stu,
    where the clip's speech nearly fully covered a VO thesis line with no
    clean seam to duck into. `clean_seams` (>= `min_seam` wide) are the
    windows where `clip_id` could speak without touching the VO. Read-only —
    nothing is written, and there is no `plan=`.

    `clip_id` need not have a transcript. Without one the clip side is its
    energy envelope — runs of sound, reported as sound rather than speech (a
    sting or a swell counts too) — and `clip_evidence` in the result says
    "energy" so the reading is not mistaken for a word-level one. Pass
    `clip_evidence="transcript"` to refuse instead, or `"energy"` to force the
    envelope on a clip that has a transcript. The VO always needs its
    transcript.
    """
    return ops.speech_overlap(
        path,
        clip_id,
        at=at,
        clip_in=clip_in,
        clip_out=clip_out,
        vo_clip_id=vo_clip_id,
        max_gap=max_gap,
        min_seam=min_seam,
        cap=cap,
        clip_evidence=clip_evidence,
    )


@_tool()
def attenuate_noises(
    path: ProjectPath = None,
    *,
    clip_id: str,
    db: float = -12.0,
    max_event_seconds: float = 1.5,
    max_gap_seconds: float = 2.0,
    pad: float = 0.05,
    confirm_suspect: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Pull down short loud non-speech events instead of cutting them out.

    An event only qualifies automatically when it is both short
    (max_event_seconds) and sitting in a word-map gap narrow enough to prove
    the map is dense around it (max_gap_seconds) — a wide gap disqualifies
    even a very short event, which is the false-positive class this exists
    to prevent (speech sitting in a hole the transcript never wrote down).
    Qualifying events are pulled down `db` via one ffmpeg pass, never cut,
    and written as a new derived copy that `media_path()` picks up
    automatically everywhere downstream; the original is always what a
    re-run reads from, so repeated calls never compound gain.

    Unlike cut_by_transcript/cut_by_time, nothing here ever raises on what
    the scan finds — this is an automatic multi-candidate scan, not a
    handful of explicit ranges, so withholding is done per event rather than
    refusing the whole call. `suspect_neighbours` (a bounding word itself
    has a suspect duration — withheld unless confirm_suspect=True or
    plan=True) and `disqualified` (too long, or too wide a gap — never
    written, no override) are always reported in full, not only under
    plan=True.
    """
    return ops.attenuate_noises(
        path,
        clip_id,
        db=db,
        max_event_seconds=max_event_seconds,
        max_gap_seconds=max_gap_seconds,
        pad=pad,
        confirm_suspect=confirm_suspect,
        plan=plan,
    )


@_tool()
def proxy_transcode(path: ProjectPath = None,
    *, clip_id: str, force: bool = False) -> dict[str, Any]:
    """Make footage the preview cannot decode playable in the window.

    The other half of what the viewer already reports: an unplayable clip
    names its reason (hev1, 10-bit, an unopenable container, an undecodable
    audio track) and shows black. This transcodes a downscaled h264/aac/mp4
    stand-in into the project's cache so it plays. One ffmpeg pass; a long
    clip is minutes.

    The result is a *preview* artefact and cannot reach a render: nothing
    records it in the manifest, so `media_path()` — what export, verify and
    check_frames all resolve through — has no way to see it. That containment
    is structural, not a convention to be careful about.

    Skips the work when a current proxy already exists (keyed by the source's
    size and mtime), so calling it on every unplayable clip in a project is
    cheap after the first pass. Refuses a clip that already plays, and refuses
    a file with no decodable streams — that is a broken file, not a codec
    problem, and it is the one refusal a transcode cannot close. `force`
    rebuilds a current proxy but does not override either refusal.
    """
    return ops.proxy_transcode(path, clip_id, force=force)


@_tool()
def review_add(
    path: ProjectPath = None,
    *,
    name: str,
    source: str,
    kind: str,
    baseline: str | None = None,
    about: str | None = None,
) -> dict[str, Any]:
    """Register a rendered file, sheet or A/B member for `proofcut review serve`.

    Never copies `source` — a render already lives in `renders/`, a sheet in
    `reframe_sheet`'s own directory — this just points `name` at it, so a
    served round has something to stream and a verdict has something to
    attach to.

    Registering a `name` that already exists **replaces** that entry, and the
    verdict recorded against it stays — so re-pointing a name at a different
    file leaves yesterday's answer attached to today's bytes. Register the new
    file under a new name unless replacing it is what you mean.

    `kind` is one of `render`, `sheet`, `ab`, `control`. **A `control`
    requires `baseline`, the name of an already-registered item, and the two
    files' sha256 must match — a mismatch refuses the call.** This is the
    rule the round that went wrong exists to enforce (HISTORY.md § The
    bumper the teaser never had): a page once served three cuts, one
    mislabelled "control" when it was a different, later render. Nothing is
    labelled a control here unless it is byte-identical to what it claims.
    """
    return ops.review_add(path, name, source, kind=kind, baseline=baseline, about=about)


@_tool()
def review_verdict(path: ProjectPath = None,
    *, name: str, verdict: str, note: str | None = None) -> dict[str, Any]:
    """Record a verdict against a review item registered by `review_add`.

    `verdict` is a free string, not an enum — past review rounds answered
    yes/no, "loop"/"hold", or a specific choice by name, and a fixed
    vocabulary would misfit whichever question the next round is actually
    asking.

    Refuses a `name` `review_add` has not registered. Calling it again for
    the same item **replaces** that item's answer rather than appending one,
    so a round holds one verdict per item, with the time it was recorded.
    """
    return ops.review_verdict(path, name, verdict, note=note)


@_tool()
def review_list(path: ProjectPath = None) -> dict[str, Any]:
    """List every item registered for this project's review round, and its verdict.

    Read-only. Returns `items` (each `review_add` registration: name, kind,
    project-relative path, sha256 and any control baseline) and `verdicts`
    (keyed by item name: the verdict, its note and when it was recorded). Items are registered with
    `review_add`, judged with `review_verdict`, and served to a phone by
    `proofcut review serve`.
    """
    return ops.review_list(path)


# ---------------------------------------------------------------- prompts
#
# The briefs the trials measured, shipped as prompts (docs/plans/MCP.md § Step
# 7): `/mcp__proofcut__film` in Claude Code, `prompts/get` anywhere else. The
# text is `proofcut.briefs`, which `scripts/agent_trial.py` composes from too.
# Every argument is a string because MCP prompt arguments are, and every one
# but the material is optional. A bound server names its own project, so the
# brief does not have to ask.

_MEDIA = Annotated[str, Field(description="The folder holding the recordings and footage to work from.")]
_OUTPUT = Annotated[
    str | None,
    Field(description="Where to write the finished file. Unset, the project's renders/ folder."),
]
_LENGTH = Annotated[
    str | None, Field(description="How long the result should run, e.g. `90s` or `3 minutes`.")
]
_PROJECT = Annotated[
    str | None,
    Field(
        description=(
            "The proofcut project directory. Unset, the one this server is bound to, "
            "or a new one beside the material."
        )
    ),
]


def _prompt_project(project: str | None) -> str | None:
    return project or (str(_BOUND_ROOT) if _BOUND_ROOT is not None else None)


@mcp.prompt(name="cut", title="Cut a video")
def _cut_prompt(
    media: _MEDIA, output: _OUTPUT = None, length: _LENGTH = None, project: _PROJECT = None
) -> str:
    """Cut recordings into a finished video: retakes out, b-roll under the lines it
    belongs to, captions burned in, rendered and checked against the timeline."""
    return briefs.cut(media, output=output, length=length, project=_prompt_project(project))


@mcp.prompt(name="film", title="Make a film")
def _film_prompt(
    media: _MEDIA,
    output: _OUTPUT = None,
    length: _LENGTH = None,
    end_card: Annotated[
        str | None, Field(description="What the end card after the last line reads.")
    ] = None,
    loudness: Annotated[
        str | None,
        Field(description="The master's level in LUFS integrated. Unset, -16."),
    ] = None,
    project: _PROJECT = None,
) -> str:
    """Make a finished film ready to upload: the cut, b-roll, music under the
    narration, an end card, captions, a loudness master, and the checks."""
    return briefs.film(
        media,
        output=output,
        length=length,
        end_card=end_card,
        loudness=loudness,
        project=_prompt_project(project),
    )


@mcp.prompt(name="review", title="Review a finished film")
def _review_prompt(
    render: Annotated[
        str | None,
        Field(description="The rendered file to check. Unset, the project's latest render."),
    ] = None,
    project: _PROJECT = None,
) -> str:
    """Check a finished project before it is uploaded, changing nothing: whether the
    render is the timeline, every word is heard, and the picture, captions, music
    and level are what the project says."""
    return briefs.review(render=render, project=_prompt_project(project))


def _trim_schema(node: Any) -> None:
    """Drop what pydantic generates and a schema reader does not need.

    Two shapes, 13% of the advertised input schema between them: a `title`
    restating each argument's own name, and `anyOf: [{type: X}, {type:
    null}]` for every optional one, which `type: [X, "null"]` says in a
    third of the bytes. Only the advertised dict changes — a call is still
    validated by the tool's own pydantic model, which is why
    `tests/test_server_stdio.py` checks the two schemas accept the same
    arguments rather than trusting the rewrite. docs/plans/MCP.md § Step 11.
    """
    if isinstance(node, list):
        for item in node:
            _trim_schema(item)
        return
    if not isinstance(node, dict):
        return
    if isinstance(node.get("title"), str):
        del node["title"]
    options = node.get("anyOf")
    if isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
        (other,) = [option for option in options if option != {"type": "null"}]
        if isinstance(other.get("type"), str) and not set(other) & (set(node) - {"anyOf"}):
            del node["anyOf"]
            node.update(other)
            node["type"] = [other["type"], "null"]
    for key in ("properties", "$defs"):
        for child in (node.get(key) or {}).values():
            _trim_schema(child)
    for key in ("items", "anyOf", "additionalProperties"):
        _trim_schema(node.get(key))


for _registered in mcp._tool_manager.list_tools():
    _trim_schema(_registered.parameters)


def serve(
    root: str | Path | None = None,
    *,
    transport: str = "stdio",
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    allow_remote: bool = False,
    allow_remote_hosts: Sequence[str] | None = None,
) -> None:
    """Run the server. Blocks until the client disconnects (stdio) or interrupted (http).

    `root` binds every tool's `path` to one project (`_confine`). It is
    checked here rather than on first use because a bad root would otherwise
    surface as a refusal on every call, blaming the argument the client sent
    instead of the directory the server was started with. Existence is all
    that is checked: `init` under a bound root is legitimate, so requiring
    the root to already be a proofcut project would refuse a real workflow.

    With no `root`, a server started **inside a project** — a directory holding
    `proofcut.json` — binds to it, exactly as `-C .` would. Claude Code spawns a
    stdio server in the directory it was launched from, so a plugin user
    working in a project stops passing `path` on every call; started anywhere
    else, the server is the unbound one it always was. A `lucid.json`-only
    directory is not a project until `migrate`, so it does not bind.
    docs/plans/MCP.md § Step 8.

    `transport` is `"stdio"` (the default — every existing client spawns the
    server this way, so changing the default would break them silently) or
    `"http"`. `host`, `port`, `allow_remote` and `allow_remote_hosts` are
    ignored for stdio.
    """
    global _LOCKING
    _LOCKING = True
    projectlock.configure(command=f"proofcut mcp ({transport})")
    atexit.register(projectlock.release_all)
    if sys.platform != "win32" and threading.current_thread() is threading.main_thread():
        # Python's default SIGTERM handler skips `atexit`, so the lock is
        # released here and the signal then re-delivered to its default
        # action. Never `sys.exit` from the handler: a stdio server's loop
        # waits on a thread blocked reading stdin, and a SystemExit left it
        # hung rather than dead — measured.
        signal.signal(signal.SIGTERM, _release_and_terminate)
    try:
        _serve(root, transport=transport, host=host, port=port, allow_remote=allow_remote,
               allow_remote_hosts=allow_remote_hosts)
    finally:
        projectlock.release_all()


def _release_and_terminate(signum: int, _frame: Any) -> None:
    projectlock.release_all()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _serve(
    root: str | Path | None,
    *,
    transport: str,
    host: str,
    port: int,
    allow_remote: bool,
    allow_remote_hosts: Sequence[str] | None,
) -> None:
    global _BOUND_ROOT, _BOUND_BY
    if root is not None:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise ProjectError(f"cannot bind the MCP server to {root!r}: not a directory")
        _BOUND_ROOT, _BOUND_BY = resolved, "-C"
    elif (Path.cwd() / MANIFEST_NAME).is_file():
        _BOUND_ROOT, _BOUND_BY = Path.cwd().resolve(), "cwd"

    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport != "http":
        raise ProjectError(f"unknown MCP transport {transport!r}: use 'stdio' or 'http'")
    _serve_http(
        host=host, port=port, allow_remote=allow_remote, allow_remote_hosts=allow_remote_hosts
    )


def _serve_http(
    *,
    host: str,
    port: int,
    allow_remote: bool,
    allow_remote_hosts: Sequence[str] | None = None,
) -> None:
    """Run the MCP server over streamable HTTP until interrupted.

    `host` must name loopback unless `allow_remote` is set — the opt-in
    `--host`/`--allow-remote` split asks for, because the MCP tools read and
    write whatever project this server is bound to (or any project, when
    unbound), so a non-loopback bind reaches anyone who can route to the
    port. Passing `allow_remote` says what it gives up: `_LoopbackGuard`'s
    allow-list widens from loopback names to loopback names *plus* the bound
    host, never to "accept anything" — a guard that admits any Host header
    is no guard at all.

    A wildcard bind (`host` in `_WILDCARD_HOSTS`) cannot widen that way: the
    literal string `"0.0.0.0"`/`"::"` is never what a real client's `Host:`
    header carries, only what an attacker who read the startup banner would
    send, so adding it would open the guard to a spoofed header while doing
    nothing for genuine remote clients — they arrive naming the address they
    actually dialed. `allow_remote_hosts` is required in that case: the
    operator names the address(es) clients will present (their LAN IP, a
    Tailscale hostname, ...), and only those are added.
    """

    if not allow_remote and host.lower() not in webui._LOOPBACK_NAMES:
        raise ProjectError(
            f"refusing to bind {host!r}: the MCP tools can read and write "
            "project files, so a non-loopback bind reaches anyone who can "
            "route to this port. Pass allow_remote=True (CLI: --allow-remote) "
            "once you mean that — it also widens the Host-header guard to "
            "accept this host, not just loopback."
        )
    if allow_remote and host.lower() in _WILDCARD_HOSTS and not allow_remote_hosts:
        raise ProjectError(
            f"refusing to bind {host!r} with allow_remote=True and no "
            "allow_remote_hosts: a wildcard bind has no single client-facing "
            "identity, so there is nothing honest to add to the Host-header "
            "guard's allow-list — a real client sends whatever address it "
            "dialed, never the wildcard itself. Pass allow_remote_hosts "
            "(CLI: --allow-remote-host, repeatable) naming the address(es) "
            "clients will actually present."
        )

    server, sock = _build_http_server(
        host=host, port=port, allow_remote=allow_remote, allow_remote_hosts=allow_remote_hosts
    )
    bound_port = sock.getsockname()[1]
    # Flushed: this is the one line a client needs to find the server, and a
    # piped stdout would otherwise hold it in the buffer (mirrors webui.serve).
    print(f"proofcut mcp: http://{host}:{bound_port}/mcp", flush=True)
    print("Ctrl-C to stop.", flush=True)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        print()


def _build_http_server(
    *,
    host: str,
    port: int,
    allow_remote: bool,
    allow_remote_hosts: Sequence[str] | None = None,
) -> tuple[Any, socket.socket]:
    """Build (but do not run) the uvicorn server and its bound socket.

    Split from `_serve_http` so a test can start it on a thread and discover
    the real port (`port=0` picks a free one) the same way
    `tests/test_webui_http.py` does for `webui.make_server` — binding the
    socket here, synchronously, is what makes the port available before
    `server.run()` starts blocking.
    """
    import uvicorn

    allowed_names = set(webui._LOOPBACK_NAMES)
    if allow_remote:
        # A specific address is honest to echo back: a client that reaches
        # the server *by* that address naturally sends it in `Host:`. A
        # wildcard bind is not — see `_serve_http` and `_WILDCARD_HOSTS` —
        # so it contributes nothing here, only `allow_remote_hosts` does.
        if host.lower() not in _WILDCARD_HOSTS:
            allowed_names.add(host.lower())
        allowed_names.update(name.lower() for name in (allow_remote_hosts or ()))

    app = mcp.streamable_http_app(host=host)
    guarded = _LoopbackGuard(app, frozenset(allowed_names))

    config = uvicorn.Config(guarded, host=host, port=port, log_level="warning")
    sock = config.bind_socket()
    # `bind_socket()` only binds — `listen()` normally happens inside
    # uvicorn's own async startup, after `server.run()` is called, which is
    # too late for a caller that wants to print (or hand back) a URL that is
    # actually connectable the moment it returns. Calling `listen()` here is
    # safe to repeat: POSIX allows re-listening on a bound socket, which is
    # all asyncio's own server startup does to it next.
    sock.listen(config.backlog)
    server = uvicorn.Server(config)
    return server, sock
