"""The preview/timeline web UI — a third client, never a third implementation.

proofcut's checks all answer a *machine's* question: `verify` diffs the render's
words, `frames`/`black`/`spots` read counts and pixels. None of them let a
person see an edit before committing to a render. This does — and because it
plays the *source* through the edit rather than a render of it, seeing an edit
costs no render at all.

Two constraints hold it in place, both from HISTORY.md § The preview/timeline web UI:

* **No privileged path.** Every mutation here goes through the same `ops`
  functions the CLI and the MCP server call. A window is the most tempting
  thing to break the parity convention with, so nothing below computes an
  edit — it posts to `ops.cut_by_transcript` / `ops.cut_by_time` / `ops.undo`
  and draws whatever comes back. The agent panel does not change this: it is
  a *client* of the same MCP tools, reaching the timeline through no path the
  page's own buttons don't already use.
* **Local, in-package.** `http.server` from the standard library, static
  assets shipped beside this file, no build step and no second stack. The one
  thing hand-rolled rather than inherited is HTTP Range, because a browser
  will not seek in a `<video>` without it.

It binds loopback, and by default nothing else. Two further guards matter
because this server can mutate a project and read media, and any page you
happen to be browsing can issue requests to `localhost`:

* the `Host` header must name loopback, which is what stops a DNS-rebinding
  page from reaching it under its own name;
* every mutating request must be `application/json`, which an HTML form
  cannot send — so a cross-origin attempt becomes a preflight, and no CORS
  headers are ever served to satisfy one.

**Off the machine, that trade is made explicitly and never by default**
(`remote_policy`). CLAUDE.md's rule was that this server is not widened off
loopback the way `reviewserver.py` is reachable off it — and the reason was
never "loopback is sacred", it was that loopback+Host is this server's whole
credential and dropping it would leave a project-rewriting server behind no
credential at all. So `--allow-remote` does not drop a guard, it *replaces*
one: the Host allow-list widens from loopback to loopback plus the names the
operator says clients will present (never to "anything"), and a **token**
stands in for what loopback was buying, `reviewserver.py`'s model. Every
request must carry it. The `application/json` rule on mutations is untouched,
and so is the default: with no `--allow-remote`, nothing below changes at all.

The token reaches the page's own JavaScript through a cookie rather than
through a rewritten `fetch`: a request presenting `?t=` is handed
`Set-Cookie: proofcut_token=…; HttpOnly; SameSite=Strict`, so the fetches, media
ranges and `EventSource` that `web/` already issues carry it with no line of
`web/` changed — and `SameSite=Strict` means no other site can make the
browser spend it, which is the CSRF half loopback+Host used to cover.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from proofcut import captions, media, ops, progress, renderlog
from proofcut.asr import ASRError
from proofcut.autoeditor import AutoEditorError
from proofcut.energy import EnergyError
from proofcut.faces import FaceError
from proofcut.media import MediaError
from proofcut.mlt import MLTError
from proofcut.picture import PictureError
from proofcut.project import (
    CACHE_DIR,
    LEGACY_MANIFEST_NAME,
    MANIFEST_NAME,
    SCHEMA_VERSION,
    TIMELINE_NAME,
    LegacyManifestError,
    Project,
    ProjectError,
    path_too_long,
    refusing_path_too_long,
)
from proofcut.timeline import TimelineError
from proofcut.transcript import TranscriptError
from proofcut.verify import VerifyError

#: The same family the CLI flattens into a one-line message. Anything outside
#: it is a bug and keeps its traceback rather than being reported as a 400.
EXPECTED = (
    ProjectError,
    MediaError,
    TranscriptError,
    TimelineError,
    AutoEditorError,
    captions.CaptionError,
    ASRError,
    VerifyError,
    PictureError,
    EnergyError,
    # `plan_picture`'s refusals — a shot longer than its asset. `timeline_view`
    # reports that one rather than raising it (the picture lane draws the
    # message), but `export` still raises it, and it is a 400 like the rest.
    MLTError,
    # `reframe_detect`/`reframe_sheet(extremes=True)`'s refusal when no
    # interpreter has a face detector — without this here, a missing
    # `PROOFCUT_FACE` on this box turns into an unhandled exception inside
    # `ReframeDetectJob._run`'s `try/except EXPECTED`: no error event is
    # published, `_finish()` never runs, and the view spins forever waiting
    # on an SSE event that will never arrive (Studio Step 03 contract § A).
    FaceError,
    # A Stop killing a job's subprocess — raised only under
    # `progress.cancellable`, which only a stoppable job installs.
    progress.Cancelled,
)

STATIC_DIR = Path(__file__).parent / "web"

DEFAULT_HOST = "127.0.0.1"
#: Deliberately not 8000/8080. Those collide with whatever else is being
#: served on a dev box, and a collision here looks like proofcut showing someone
#: else's page.
DEFAULT_PORT = 8710

#: Hostnames a request may claim to have reached. A browser sends whatever the
#: user typed, so an attacker-controlled name resolving to 127.0.0.1 arrives
#: here looking local unless the header itself is checked.
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

#: Addresses that are a *bind* instruction and never a client's identity. A
#: real client sends the address it dialed; only someone who read the startup
#: banner sends `Host: 0.0.0.0`. `server.py` imports this rather than keeping
#: a second copy — it makes the same refusal for `proofcut mcp --transport http`.
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})

#: Where a request that presented `?t=` gets the token parked, so that the
#: page's own JS — which knows nothing about a token — carries it on every
#: subsequent fetch/range/SSE request. `HttpOnly` keeps it out of `document`,
#: `SameSite=Strict` keeps any other origin from spending it.
TOKEN_COOKIE = "proofcut_token"

#: Names an exact `tailscale` binary, for the same reason `PROOFCUT_WHISPER`
#: does: the one on PATH is not always the one meant, and a silent fallback
#: to loopback would look like `--tailscale` working right up until a phone
#: tried it.
PROOFCUT_TAILSCALE_ENV = "PROOFCUT_TAILSCALE"


def _tailscale_installs() -> list[Path]:
    """Where the Tailscale app puts its CLI on this OS, searched after PATH.

    Neither the macOS app nor the Windows installer puts `tailscale` on PATH
    by default. Leads from docs/plans/PORTABILITY.md step 2, unmeasured here.
    """
    if sys.platform == "darwin":
        return [Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")]
    if sys.platform == "win32":
        program_files = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        return [program_files / "Tailscale" / "tailscale.exe"]
    return []


def _find_tailscale() -> str | None:
    """`$PROOFCUT_TAILSCALE`, then PATH, then the OS's own install location."""
    found = os.environ.get(PROOFCUT_TAILSCALE_ENV) or shutil.which("tailscale")
    if found:
        return found
    return next((str(p) for p in _tailscale_installs() if p.is_file()), None)


def _host_forms(name: str) -> set[str]:
    """Every spelling of `name` a browser might put in `Host:`.

    One entry per name is right for a hostname and wrong for an IPv6 literal,
    which a browser always brackets — `Host: [fd7a:…]:8710`. The bare form is
    what `tailscale status` and a `--host` flag carry, the bracketed form is
    what actually arrives, and storing only one of them refuses every real
    v6 client at exit 0. `_LOOPBACK_NAMES` has always carried both `::1` and
    `[::1]` for exactly this reason; this is that fact applied to a name
    nobody typed by hand.
    """
    lowered = name.strip().lower()
    if not lowered:
        return set()
    if ":" in lowered and not lowered.startswith("["):
        return {lowered, f"[{lowered}]"}
    return {lowered}


def remote_policy(
    *,
    host: str,
    allow_remote: bool = False,
    allow_remote_hosts: Sequence[str] | None = None,
    token: str | None = None,
) -> tuple[str | None, frozenset[str]]:
    """Resolve `(token, allowed_hosts)` for a bind, or refuse the combination.

    The default — `allow_remote=False`, loopback `host`, no token — returns
    `(None, _LOOPBACK_NAMES)`, which is byte-for-byte the behaviour this
    server has always had: no token guard, loopback Host only.

    `allow_remote` is the opt-in, and it mirrors `server._serve_http`'s split
    exactly rather than inventing a second vocabulary for the same decision:
    the Host allow-list widens to include the bound host, a wildcard bind
    contributes nothing to it (see `_WILDCARD_HOSTS`) and so must be told
    what clients will present, and a token is minted if the caller did not
    supply one. A token with no `allow_remote` is allowed and means what it
    says — a loopback server that also wants a credential.
    """
    names = set(_LOOPBACK_NAMES)
    if not allow_remote:
        if host.lower() not in _LOOPBACK_NAMES:
            raise ProjectError(
                f"refusing to bind {host!r}: this server can rewrite the whole "
                "project, and loopback + the Host header is the only credential "
                "it has by default. Pass allow_remote=True (CLI: --allow-remote) "
                "once you mean that — it widens the Host guard to accept this "
                "host and requires an access token on every request, rather "
                "than leaving the server open to anyone who can route to it."
            )
        return (token, frozenset(names))
    if host.lower() in _WILDCARD_HOSTS and not allow_remote_hosts:
        raise ProjectError(
            f"refusing to bind {host!r} with allow_remote=True and no "
            "allow_remote_hosts: a wildcard bind has no single client-facing "
            "identity, so there is nothing honest to add to the Host guard's "
            "allow-list — a real client sends whatever address it dialed, "
            "never the wildcard itself. Pass allow_remote_hosts (CLI: "
            "--allow-remote-host, repeatable) naming the address(es) clients "
            "will actually present, or use --tailscale, which reads them off "
            "this node."
        )
    if host.lower() not in _WILDCARD_HOSTS:
        names.update(_host_forms(host))
    # A MagicDNS name arrives from `tailscale status` fully qualified, with
    # the root dot a browser never sends.
    for name in allow_remote_hosts or ():
        names.update(_host_forms(name.rstrip(".")))
    return (token or secrets.token_urlsafe(24), frozenset(names))


def tailscale_identity(binary: str | None = None) -> tuple[str, list[str]]:
    """This node's tailnet address, and every Host value a client may present.

    Two values because they are two different answers: the address to *bind*
    (the 100.x one — binding it rather than a wildcard is what keeps the
    socket off the LAN entirely, so the Host guard and the token are the
    second and third lines rather than the first) and the set of names a
    client may arrive under, which includes the MagicDNS name because a phone
    typing `homebase` and a phone dialing `100.x` are one session to the
    operator and two different `Host:` headers here.

    Refuses rather than falling back: a `--tailscale` that quietly bound
    loopback would look like the feature working until something off the
    machine tried it.
    """
    resolved = binary or _find_tailscale()
    if not resolved:
        where = "".join(f" or at {p}" for p in _tailscale_installs())
        raise ProjectError(
            f"--tailscale: no `tailscale` binary found on PATH{where}. Install it, or "
            f"set {PROOFCUT_TAILSCALE_ENV} to the one to use, or pass --host with "
            "--allow-remote --allow-remote-host yourself."
        )
    try:
        proc = subprocess.run(
            [resolved, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectError(f"--tailscale: could not run {resolved!r}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise ProjectError(
            f"--tailscale: `{resolved} status --json` failed"
            + (f": {detail[0]}" if detail else "")
        )
    try:
        status = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProjectError(f"--tailscale: could not read `{resolved} status --json`: {exc}") from exc
    state = status.get("BackendState")
    if state and state != "Running":
        raise ProjectError(
            f"--tailscale: tailscale is not up (BackendState {state!r}). "
            "Run `tailscale up` first."
        )
    ips = [str(ip) for ip in (status.get("TailscaleIPs") or []) if ip]
    if not ips:
        raise ProjectError("--tailscale: this node has no tailnet address yet.")
    # IPv4 first: it is what a phone dials and what reads back as a bindable
    # host with no bracket rules attached.
    bind = next((ip for ip in ips if ":" not in ip), ips[0])
    names = list(ips)
    self_node = status.get("Self") or {}
    for key in ("DNSName", "HostName"):
        value = str(self_node.get(key) or "").strip().rstrip(".")
        if value:
            names.append(value)
    return bind, names

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    # The three type voices, vendored beside the stylesheet rather than
    # fetched from a CDN — which the `default-src 'self'` CSP would refuse
    # anyway (docs/plans/DAYDREAM.md § Typography). Flat names, because `_send_static`
    # serves a filename and never a path.
    ".woff2": "font/woff2",
}

#: Read size for streaming media. Large enough that a long range is not a
#: million syscalls, small enough that an aborted seek stops promptly.
_CHUNK = 256 * 1024

#: How often the SSE handler checks whether the revision moved (PLAN.md §
#: Tier 3 is the goal, lines 1794-1798). Simple beats exact: this one poll
#: loop is what lets an agent edit, the page's own edit, and a `proofcut cut` in
#: a terminal all raise the same event instead of three code paths.
_REVISION_POLL_SECONDS = 0.5

#: `claude` by default; overridable so tests never spawn the real thing.
AGENT_BIN_ENV = "PROOFCUT_AGENT_BIN"

#: The agent's tool allowlist — the security boundary, not a convenience
#: default (PLAN.md § The agent panel, in mechanism). `--permission-mode
#: manual` is the belt to this allowlist's braces: there is no TTY on a
#: subprocess, so anything falling outside the allowlist fails closed rather
#: than prompting. Do not widen this without writing the decision down there.
#:
#: `--allowedTools`/`--disallowedTools`/`--permission-mode manual` alone do
#: **not** bound the built-in tool set — verified against the installed
#: claude 2.1.226: a built-in tool named in neither list (`Glob`, in the
#: reproduction) runs with no permission gate at all, because those flags
#: govern *permission prompts*, and a tool outside the allowlist without a
#: matching disallow entry is simply never asked about. `--tools ""` is the
#: actual boundary for the built-in set — it disables all of it, leaving only
#: the MCP tools `--strict-mcp-config` exposes, which is what makes "the
#: agent gets proofcut's MCP tools and nothing else" true. `_AGENT_DISALLOWED_TOOLS`
#: stays as defense in depth, not because it does the job on its own.
_AGENT_ALLOWED_TOOLS = "mcp__proofcut__*"
#: The built-ins `--tools` leaves on: `ToolSearch` alone. `""` stripped it with
#: the rest, and it is what defers MCP tool definitions — without it every one
#: of proofcut's ~92 definitions loaded on every turn (92,266 turn-1 tokens
#: against 9,359 for a `ping`, $1.87 against $0.19). It only answers "which
#: tool"; it reads no file and runs nothing, so the confinement above holds.
#: Named so `scripts/agent_trial.py` moves with the panel. docs/plans/MCP.md
#: § Step 1.
_AGENT_TOOLS = "ToolSearch"
_AGENT_DISALLOWED_TOOLS = ("Bash", "Write", "Edit", "WebFetch", "WebSearch")


class WebUIError(Exception):
    """Raised when a request cannot be served for a reason worth reporting."""


class RenderBusyError(WebUIError):
    """A second render was requested while one was already running.

    Its own type rather than a plain `WebUIError` so the handler can answer
    409 (a real conflict with server state) instead of 400 (a bad request) —
    the identical request would succeed once the first job finishes.
    """


class ProxyBusyError(WebUIError):
    """A second proxy transcode was requested while one was already running.

    Same 409-not-400 reasoning as `RenderBusyError`, and deliberately its own
    type rather than a shared one: a busy proxy must not be reported as a busy
    render, since the two jobs have separate slots and the message is what
    tells a person which to wait for.
    """


class ReframeSheetBusyError(WebUIError):
    """A second sheet generation was requested while one was already running.

    Its own type, same 409-not-400 reasoning as `RenderBusyError`/
    `ProxyBusyError` — a busy sheet job must not be reported as a busy detect
    job or a busy render, since each has its own slot.
    """


class ReframeDetectBusyError(WebUIError):
    """A second detect pass was requested while one was already running."""


class ImportBusyError(WebUIError):
    """A second import was requested while one was already running.

    Its own type, same 409-not-400 reasoning as the other `*BusyError`s — a
    busy import must not be reported as a busy transcribe or a busy render,
    since each of these jobs holds its own slot.
    """


class TranscribeBusyError(WebUIError):
    """A second transcription was requested while one was already running."""


class SeedBusyError(WebUIError):
    """A second seed was requested while one was already running.

    Its own type, the `ImportBusyError` reasoning: every job here holds its
    own slot, and a busy seed must not be reported as a busy transcription.
    """


def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    """Read and parse a JSON request body, enforcing the content type.

    The content type is a guard, not a formality: `application/json` is not a
    type an HTML form can produce, so requiring it is what makes a
    cross-origin POST take the preflight path this server never answers.
    """
    ctype = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise WebUIError("request body must be application/json")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        raise WebUIError("bad Content-Length") from None
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebUIError(f"body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WebUIError("body must be a JSON object")
    return payload


#: How far beneath `--root` the scan looks for a project. Real dogfood
#: layouts put one at depth 1 (`~/proofcut-work/archive/spikes/dogfood/scream-vo` is itself the
#: project, two path segments under a `~/` root) and at depth 2
#: (`~/proofcut-work/archive/spikes/teaser/proj`, an explicit `proj` subdirectory) — this covers
#: both without wandering into an unrelated deep tree. A directory a scan
#: finds a project in is never descended into further: its own `history/`
#: backups are named `proofcut-vN.json` or `lucid-vN.json`, never a manifest name (`Project.migrate`),
#: so there is no false positive to worry about, but stopping there also
#: keeps a big `--root` cheap to scan on every picker load.
_SCAN_MAX_DEPTH = 3

#: Directories a scan never opens, whether or not they hold a project one
#: level down — dev-tooling trees that can be enormous and are never where a
#: project lives.
_SCAN_SKIP_NAMES = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})


def scan_projects(root: Path) -> list[dict[str, Any]]:
    """Find proofcut projects under `root` (docs/plans/DAYDREAM.md § Multi-project).

    A project is a directory holding `proofcut.json` (`Project.MANIFEST_NAME`)
    directly — not a directory containing one somewhere inside it, which
    would also match every project's own `history/` backups' *parent*.
    Never opens a manifest at the wrong schema and never migrates one
    (CLAUDE.md: `Project.open` refuses an old manifest and a read must never
    rewrite a project someone only looked at) — each entry says what was
    found instead:

    * `status: "ok"` — opened cleanly; `name`, `timeline_duration`,
      `segments` and `clips` are `ops.status`'s own cheap read, the same one
      the workspace's top bar uses.
    * `status: "needs_migration"` — a manifest at a schema `Project.open`
      refuses; `schema_version` names what was found. Listed, not skipped
      and not opened — `proofcut -C <path> migrate` is the way forward, and the
      picker says so without taking it. A directory holding only a
      pre-rename `lucid.json` is one of these too, with `manifest:
      "lucid.json"` (docs/plans/RENAME.md, decision 1).
    * `status: "unreadable"` — any other `ProjectError` (bad JSON, a
      manifest that is not a JSON object, or a read that raced the
      directory listing) — `error` carries `Project.open`'s own message.
    * `status: "error"` — the manifest itself read fine at the current
      schema, but `ops.status` (the same cheap read the `ok` case uses)
      raised one of `EXPECTED` — an un-seeded project (`proofcut init` with no
      `proofcut seed` yet) is the ordinary way to hit this, not a corrupt
      project. Listed with `error` carrying the message, same as
      `unreadable` — one bad project must never take the whole `/api/projects`
      listing down with it. `seeded` says which kind of `error` this is
      without anyone matching a sentence: false means there is no
      `project.otio`, which is the ordinary un-seeded case and the one the
      picker can offer to finish.

    A directory with neither `proofcut.json` nor `lucid.json` is not a project and is not in
    the returned list — that is the "skip a non-project directory" case, and
    it produces no entry rather than a fifth kind of failure.
    """
    root = Path(root)
    found: list[dict[str, Any]] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > _SCAN_MAX_DEPTH:
            return
        try:
            # `is_symlink()` excludes a symlinked directory, not just a
            # symlinked file — a symlink placed under `--root` can point
            # anywhere on disk, and `is_dir()` alone follows it. `/api/open`
            # confines by `.resolve()` at bind time regardless, but the scan
            # must not *report* metadata (name, segment/clip counts) for a
            # directory `--root` was never scanned to include.
            children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in _SCAN_SKIP_NAMES:
                continue
            if (child / MANIFEST_NAME).is_file() or (child / LEGACY_MANIFEST_NAME).is_file():
                found.append(_scan_one(child))
                continue  # never descend into a project's own subdirectories
            walk(child, depth + 1)

    walk(root, 1)
    found.sort(key=lambda entry: entry["path"])
    return found


def _scan_one(path: Path) -> dict[str, Any]:
    """Classify one directory already known to hold `proofcut.json` or `lucid.json`."""
    entry: dict[str, Any] = {"path": str(path), "name": path.name}
    # Resume line (Studio Step 04 § D): a plain, best-effort file read that
    # rides this scan rather than adding a second one — no `Project.open`,
    # no binding. Works for every status below, `needs_migration` included,
    # because `session.json` carries no schema version of its own.
    session_file = path / CACHE_DIR / "session.json"
    if session_file.is_file():
        try:
            with session_file.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                entry["session"] = data
        except (json.JSONDecodeError, OSError):
            pass  # no resume line for this project; the rest of the entry stands
    project = Project(path)
    try:
        # A pre-rename `lucid.json` alone is `needs_migration` — the filename
        # step is a migration like any schema step, and skipping the directory
        # would hide a real project from the picker. Both manifests at once is
        # `Project.open`'s refusal, so it lands in `unreadable` with that
        # message rather than in a fifth status.
        try:
            project.check_manifest_name()
            legacy = False
        except LegacyManifestError:
            legacy = True
        manifest = project.read_legacy_manifest() if legacy else project.read_manifest()
    except (ProjectError, OSError) as exc:
        entry["status"] = "unreadable"
        entry["error"] = str(exc)
        return entry
    found_version = manifest.get("schema_version")
    if legacy or found_version != SCHEMA_VERSION:
        entry["status"] = "needs_migration"
        entry["schema_version"] = found_version
        if legacy:
            # Which file was found, as data: a v4 `lucid.json` needs the
            # rename and nothing else, and `schema_version` alone reads current.
            entry["manifest"] = LEGACY_MANIFEST_NAME
        return entry
    try:
        info = ops.status(path)
    except EXPECTED as exc:
        # A current-schema project whose manifest reads fine can still fail
        # here — a corrupt head/tail/pack record, say. One bad project must
        # not take the whole listing down (webui.py's own `_route_picker`
        # would otherwise turn this into a 400 for everyone under `--root`,
        # not just the broken one). No timeline seeded yet is no longer one
        # of these: `ops.status` reports `seeded: false` instead of raising
        # (TRIAL.md § `timeline_status` is the first call an agent makes and
        # it refuses on a fresh project), so landing here means a real
        # error, and the picker offers no "finish setting up" affordance for
        # it. No fifth status — the four outcomes this scan reports are
        # unchanged (docs/plans/POLISH.md § Step 06).
        entry["status"] = "error"
        entry["error"] = str(exc)
        entry["seeded"] = (path / TIMELINE_NAME).is_file()
        return entry
    entry["status"] = "ok"
    entry["seeded"] = info["seeded"]
    entry["clips"] = len(info["clips"])
    if info["seeded"]:
        entry["timeline_duration"] = info["timeline_duration"]
        entry["segments"] = info["segments"]
    return entry


def _ranges(spec: str, size: int) -> tuple[int, int] | None:
    """Parse a single-range `Range: bytes=` header against a known size.

    Only the first range of a multi-range request is honoured — serving
    `multipart/byteranges` would be real work and no browser needs it for
    media playback. Returns None when the header is unusable, which means
    "serve the whole thing" rather than an error.
    """
    if not spec.lower().startswith("bytes="):
        return None
    first, sep, last = spec[6:].split(",")[0].strip().partition("-")
    if not sep:
        return None
    try:
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        elif last:
            # Suffix form, `bytes=-500`: the final N bytes.
            start = max(0, size - int(last))
            end = size - 1
        else:
            return None
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        raise WebUIError(f"range {spec!r} does not fit a {size}-byte file")
    return start, min(end, size - 1)


def _stream_file(handler: BaseHTTPRequestHandler, source: Path, *, head_only: bool) -> None:
    """Byte-range streaming, shared by every route that hands over a file.

    Takes `handler` rather than being a method, the `_json_body` shape —
    `reviewserver.py` streams renders and sheets over LAN under a different
    guard (a token, not loopback+Host) and reuses this rather than
    reimplementing Range parsing a second time, which is the one piece of
    HTTP this package hand-rolls in the first place (module docstring).
    """
    size = source.stat().st_size
    ctype = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    header = handler.headers.get("Range")
    window = _ranges(header, size) if header else None

    if window is None:
        start, end, status = 0, size - 1, HTTPStatus.OK
    else:
        start, end = window
        status = HTTPStatus.PARTIAL_CONTENT

    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(length))
    handler.send_header("Accept-Ranges", "bytes")
    if status == HTTPStatus.PARTIAL_CONTENT:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    if head_only:
        return

    with source.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(_CHUNK, remaining))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except ConnectionError:
                # A seek aborts the in-flight range. Routine, not an error.
                # `ConnectionError`, not its two Linux children: Windows
                # reports the same abort as ConnectionAbortedError
                # (WinError 10053), which printed a traceback per seek on
                # the first person-run PC. HISTORY.md § The editor on
                # Windows, looked at.
                return
            remaining -= len(chunk)


def _revision(project_root: Path) -> list[float | int]:
    """`project.otio` mtime, the manifest's, and undo depth (PLAN.md § View
    invalidation).

    Not a single counter: a mutation changes the otio mtime, an undo changes
    the depth without necessarily changing that mtime to something
    new-looking (a restore overwrites the file, but a second undo back to a
    state that was never re-saved can otherwise look unchanged). Comparing
    the tuple catches both.

    **The manifest is in here because two things the view draws live in it and
    not in the timeline at all** — the cue table behind the V2 lane, and the
    caption style the preview overlay draws. `cue_add` and `caption_style`
    write the manifest and never touch `project.otio`, so a revision that
    watched the timeline alone left an open window showing the old picture
    lane until something unrelated moved the edit.
    """
    project = Project.open(project_root)
    timeline = project.timeline_path
    mtime = timeline.stat().st_mtime if timeline.exists() else 0.0
    manifest = project.manifest_path
    return [mtime, manifest.stat().st_mtime if manifest.exists() else 0.0, len(project.snapshots())]


def _session_get(root: str) -> dict[str, Any]:
    """`GET /api/session` — read back `cache/session.json`, or `{}`.

    Best-effort by design (Studio Step 04 contract § B): a missing or
    corrupt session file restores nothing rather than failing the page
    load, since it holds nothing but UI convenience state — playhead, zoom,
    scroll, pane-expand, mode, selection — and every key is optional on
    both read and write.
    """
    project = Project.open(root)
    path = project.session_path
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _session_set(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/session` — replace `cache/session.json` wholesale.

    Deliberately does not touch `project.otio` or the manifest: `_revision()`
    (above) only stats `project.timeline_path`, `project.manifest_path`, and
    counts `project.snapshots()`, and this writes to none of the three — so
    no `project-changed` event fires from this call, the same fact
    `_agent_thumb`'s own docstring states about `Project.thumbs_path`.

    Full-object replace, last-write-wins: the client always POSTs its whole
    current snapshot, never a partial patch, so there is nothing to merge
    here. Atomic write, `Project.write_manifest`'s own tmp+`.replace()`
    pattern (project.py:439-445), aimed at `session_path` instead of
    `manifest_path` — cache, no schema version, disposable by design.
    """
    project = Project.open(root)
    path = project.session_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
    return {"saved": True}


def _ensure_poster(project: Project, view: dict[str, Any]) -> None:
    """Best-effort — a poster failure must never break `/api/view`.

    Written once, on the workspace's first real data fetch (whether reached
    via `-C` or via `/api/open`) — no auto-refresh. `view["shots"]` is
    `timeline_view`'s own picture-lane projection (already through
    `mlt.plan_picture`), and its first shot's `asset` is the footage
    actually on screen — CLAUDE.md: a shot's *addressing* clip (`clip_id`)
    is not its footage, and reaching in by `clip_id` is the exact bug the
    first filmstrip draft had.

    `ops.thumbnail` is used unmodified: it resolves the source through
    `media.media_path()`, never `media.preview_path()`, and caches its own
    frame under `cache/thumbs/<clip_id>/<ms>.jpg`. This function does a
    plain byte copy of that cached frame into the stable `cache/poster.jpg`
    name the picker's `_send_poster` serves — not a symlink, not a reused
    path — so the thumbnail cache can be pruned independently later without
    breaking the poster.
    """
    if project.poster_path.is_file():
        return  # written once; no auto-refresh
    shots = view.get("shots") or []
    if not shots:
        return  # audio-only or unedited project: no poster, acceptable
    first = shots[0]
    asset_clip_id = first.get("asset")
    if not asset_clip_id:
        return
    at = first.get("src_start") or 0.0
    resolved = ops.thumbnail(str(project.root), asset_clip_id, at)
    project.poster_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(resolved["path"]), project.poster_path)


class EventBus:
    """A lock and a set of per-subscriber queues — nothing cleverer than that.

    `project-changed` is polled straight off `_revision` inside the SSE
    handler; `agent` and `render` events are *pushed* here by other server
    code (the agent subprocess reader and the render job, respectively) and
    fan out to every open `/api/events` connection. One bus per server,
    because one server serves one project.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[tuple[str, dict[str, Any]]]] = set()

    def subscribe(self) -> queue.Queue[tuple[str, dict[str, Any]]]:
        q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[tuple[str, dict[str, Any]]]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put((event, data))


def _agent_bin() -> str:
    """The `claude` the agent pane spawns, resolved the way `doctor._agent` finds it.

    Through `shutil.which`, because Popen does not: on Windows an npm install
    is `claude.cmd`, and CreateProcess searches PATH for `.exe` alone — so a
    bare name that doctor's `which` found would still fail to spawn, and doctor
    would be ✓ over a pane that cannot start. Unresolved, the name goes through
    as given and the spawn's own error names it.
    """
    name = os.environ.get(AGENT_BIN_ENV, "claude")
    return shutil.which(name) or name


class AgentSession:
    """One `claude -p` subprocess per server, spawned lazily on first prompt.

    PLAN.md § The agent panel, in mechanism: `--strict-mcp-config` confines it
    to a generated config holding *only* proofcut's MCP server (never the user's
    own Gmail/Drive/Calendar servers), the allowlist above is the sole path it
    has into the project, and `--permission-mode manual` fails anything
    outside that allowlist closed rather than prompting a TTY that isn't
    there. There is no `--cwd` flag — the working directory is set on the
    Popen instead.
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._mcp_config_path: Path | None = None
        #: The model baked into the live subprocess's own argv, `None` for
        #: "let `claude` choose its own default" — set from `send()`'s own
        #: `model` argument, never read anywhere else, because `_spawn()` is
        #: the only thing that needs it.
        self._model: str | None = None
        #: Set only by `close()`'s kill branch, and only when it actually
        #: kills a live proc — never unconditionally, or it would wrongly
        #: swallow a *genuine* future crash report after an earlier no-op
        #: reset. Consumed once, by `_pump_stdout`'s silent-exit branch, so a
        #: deliberate `reset()` (item 4, docs/plans/DAYDREAM.md § Agent panel) does not
        #: also surface as a synthetic `error_no_output` result event.
        self._suppress_next_exit_report = False

    def _mcp_config(self) -> Path:
        """Write the generated one-server MCP config lazily, once.

        `proofcut -C <project> mcp` is the invocation that actually binds the
        server to this project — `-C` is a global flag that argparse only
        accepts *before* the subcommand (`proofcut mcp -C <project>` does not
        parse), so it is spelled out here as `command`/`args` rather than as
        a single shell string.

        **The command is this interpreter, never the name `proofcut`.** A bare
        name is a PATH lookup performed by `claude`, not by the process that
        knows where proofcut is, and it is absent from PATH for every launch
        that does not go through an activated venv — `.venv/bin/proofcut web`,
        a desktop entry, anything `proofcut open` is wired to. Measured on this
        box: with `"command": "lucid"` the harness's own `system`/`init`
        event reports `mcp_servers: [{"name": "lucid", "status": "failed"}]`
        and **`tools: []`**, and the panel then answers the prompt in prose
        with no way to touch the project, while the pane's banner goes on
        saying it reaches the timeline through lucid's tools. Same probe with
        this interpreter: `"status": "connected"`, 68 tools. `sys.executable`
        plus `-m proofcut.cli` is the same resolution `_vlm_worker`/`_face_worker`
        already use — run the interpreter you are, not a name you hope is on
        someone's PATH.
        """
        if self._mcp_config_path is None:
            config = {
                "mcpServers": {
                    "proofcut": {
                        "command": sys.executable,
                        "args": ["-m", "proofcut.cli", "-C", str(self.project_root), "mcp"],
                    }
                }
            }
            fd, name = tempfile.mkstemp(prefix="proofcut-mcp-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(config, fh)
            self._mcp_config_path = Path(name)
        return self._mcp_config_path

    def _spawn(self) -> subprocess.Popen[str]:
        argv = [
            _agent_bin(),
            "-p",
            "--verbose",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--mcp-config",
            str(self._mcp_config()),
            "--strict-mcp-config",
            "--tools",
            _AGENT_TOOLS,
            "--allowedTools",
            _AGENT_ALLOWED_TOOLS,
            "--disallowedTools",
            *_AGENT_DISALLOWED_TOOLS,
            "--permission-mode",
            "manual",
        ]
        if self._model:
            # Absent means `claude`'s own default, exactly what every prompt
            # before this flag existed got — never validated against a fixed
            # list here, the same reasoning as `ops.py`'s framing/canvas
            # overrides: `claude`'s own accepted model names change out from
            # under any list proofcut would keep, so a wrong value surfaces as
            # `claude`'s own refusal (an exit with nothing on stdout, caught
            # by `_pump_stdout`'s silent-exit report below) rather than
            # proofcut's guess getting in the way of the one closer to the truth.
            argv += ["--model", self._model]
        proc = subprocess.Popen(
            argv,
            cwd=str(self.project_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        stderr_tail: list[str] = []
        stderr_thread = threading.Thread(
            target=self._pump_stderr, args=(proc, stderr_tail), daemon=True
        )
        stderr_thread.start()
        threading.Thread(
            target=self._pump_stdout,
            args=(proc, stderr_tail, stderr_thread),
            daemon=True,
        ).start()
        return proc

    @staticmethod
    def _pump_stderr(proc: subprocess.Popen[str], tail: list[str]) -> None:
        """Keep the last ~200 lines of stderr, for `_pump_stdout`'s silent-exit report.

        Read continuously rather than at the end: `claude` writing enough to
        fill the pipe buffer while nobody drains it would otherwise deadlock
        the subprocess against its own stderr.
        """
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line)
            del tail[:-200]

    def _pump_stdout(
        self,
        proc: subprocess.Popen[str],
        stderr_tail: list[str],
        stderr_thread: threading.Thread,
    ) -> None:
        """Parse one `stream-json` line at a time and publish it as-is.

        The page styles the event; this only has to pass the parsed JSON
        through, unchanged, the same way `ops.timeline_view` hands the page
        numbers rather than a rendering of them.

        If the process exits without ever emitting a `result` event — a CLI
        flag mismatch that errors and exits 0 before printing anything is the
        reproduction that found this — the page's composer has nothing to
        clear `busy` on and hangs forever with no visible failure. A
        synthetic `result` event with a non-"success" subtype covers that:
        `agent.js`'s `handleResult` already renders any such subtype and
        clears `busy` regardless of what it says.
        """
        assert proc.stdout is not None
        saw_result = False
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("type") == "result":
                saw_result = True
            self.bus.publish("agent", payload)
        if saw_result:
            return
        proc.wait()
        # stderr is drained by its own thread; without this join a fast exit
        # can report a truncated tail while that thread is still mid-read.
        stderr_thread.join(timeout=2)
        with self._lock:
            suppressed, self._suppress_next_exit_report = self._suppress_next_exit_report, False
        if suppressed:
            # A deliberate `reset()` (item 4) killed this proc on purpose —
            # the exit is expected, not a silent failure, so it gets no
            # synthetic result event. The client clears `busy` itself instead
            # of waiting on this stream (see `_handle_agent_new_task`).
            return
        detail = "".join(stderr_tail).strip()[-2000:] or (
            f"the agent process exited (code {proc.returncode}) without producing a response"
        )
        self.bus.publish(
            "agent",
            {"type": "result", "subtype": "error_no_output", "result": detail},
        )

    def send(self, prompt: str, model: str | None = None) -> None:
        """Write one user turn to the live subprocess, spawning it if needed.

        `--model` bakes into `claude -p`'s argv at spawn — there is no way to
        hot-swap a running turn's model — so `model` is compared against
        `self._model`, the value already baked into any live subprocess. A
        difference kills it exactly the way `reset()`/"New Task" does (same
        `_suppress_next_exit_report` dance, so the kill does not also surface
        as a synthetic `error_no_output`), and the respawn below picks up the
        new value. The ordinary path — same model as last turn, or the first
        turn of a fresh session — touches nothing extra.
        """
        stale_proc: subprocess.Popen[str] | None = None
        with self._lock:
            if self._proc is not None and self._proc.poll() is None and model != self._model:
                stale_proc, self._proc = self._proc, None
                self._suppress_next_exit_report = True
            self._model = model
        if stale_proc is not None:
            stale_proc.kill()
            stale_proc.wait(timeout=5)
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = self._spawn()
            proc = self._proc
        message = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
        }
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def stop(self) -> None:
        """Interrupt the running turn.

        `claude`'s `stream-json` input protocol takes a `control_request` of
        subtype `interrupt` on stdin (verified against the installed 2.1.226
        binary's own control-plane strings, not recalled). That is tried
        first; if the pipe is already gone the process is killed instead and
        the next prompt lazily respawns it — the documented fallback this
        stage was asked to fall back to.
        """
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        control = {
            "type": "control_request",
            "request_id": str(uuid.uuid4()),
            "request": {"subtype": "interrupt"},
        }
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(control) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            proc.kill()
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def close(self) -> None:
        """Server shutdown: kill the subprocess and remove the generated config.

        Nothing else reaps either one — the subprocess otherwise outlives the
        server it belonged to, and every session that prompted the agent
        would leave one `proofcut-mcp-*.json` behind in `$TMPDIR`.

        Also the mechanism `reset()` (item 4) reuses for a live mid-session
        "Start New Task": when a proc is actually killed here, the
        `_suppress_next_exit_report` flag is set first so `_pump_stdout`'s
        silent-exit safety net — written for a genuinely broken subprocess —
        does not also fire for this deliberate kill.
        """
        with self._lock:
            proc, self._proc = self._proc, None
            config, self._mcp_config_path = self._mcp_config_path, None
        if proc is not None and proc.poll() is None:
            with self._lock:
                self._suppress_next_exit_report = True
            proc.kill()
            proc.wait(timeout=5)
        if config is not None:
            try:
                config.unlink(missing_ok=True)
            except OSError:
                pass

    def reset(self) -> None:
        """`POST /api/agent/new-task`: kill the running subprocess so the next
        prompt spawns fresh with no conversation history.

        Same mechanics as `close()` — the alias exists so a live per-request
        reset does not read as server shutdown, which is `close()`'s only
        caller today. `send()` already respawns lazily
        (`if self._proc is None or self._proc.poll() is not None`), so "fresh
        subprocess turn" needs nothing beyond making the current one gone.
        """
        self.close()


def _run_checks(project_root: Path, output: Path, has_video: bool) -> dict[str, Any]:
    """What the checks that already exist say about this render.

    PLAN.md § Finishing — the render happens in the window: `verify`,
    `check_frames`, `check_black` and `spot_frames` already answer whether a
    render says what the timeline says, and the completion card is where they
    belong rather than a separate command a person has to remember to run.

    `verify` is the audio-side check (its own docstring: "deliberately covers
    only audio") and always applies. The picture-side three are *skipped*
    outright for an audio-only render, not called to report null fields —
    cheaper than a wasted ffprobe/ffmpeg pass, and the payload says why rather
    than silently omitting them.

    Any one check failing to run (no whisper on `PATH`, no video stream to
    probe, a render too odd to scan) is caught and reported `skipped` next to
    its reason, so one check's absence never costs the render its completion
    event or the checks that did run.
    """
    checks: dict[str, Any] = {}
    try:
        checks["verify"] = ops.verify(str(project_root), str(output))
    except EXPECTED as exc:
        checks["verify"] = {"skipped": True, "reason": str(exc)}

    picture_checks: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
        ("check_frames", lambda: ops.check_frames(str(project_root), str(output))),
        ("check_black", lambda: ops.check_black(str(project_root), str(output))),
        ("spot_frames", lambda: ops.spot_frames(str(project_root), str(output))),
    )
    for name, call in picture_checks:
        if not has_video:
            checks[name] = {
                "skipped": True,
                "reason": "this render has no video stream — the picture-side checks do not apply",
            }
            continue
        try:
            checks[name] = call()
        except EXPECTED as exc:
            checks[name] = {"skipped": True, "reason": str(exc)}
    return checks


def _job_progress(bus: EventBus, topic: str, job_id: str, **extra: Any) -> progress.Reporter:
    """A job's `progress` reporter: each report becomes a `progress` event on
    the job's own topic, beside `running`/`done`/`error`, so a pane can draw
    how far along it is where it used to draw only that it was running. The
    same reports the MCP server sends as `notifications/progress`.
    docs/plans/MCP.md § Step 6."""

    def publish(current: float, total: float | None, message: str | None) -> None:
        bus.publish(
            topic,
            {
                "job_id": job_id,
                "status": "progress",
                "progress": current,
                "total": total,
                "message": message,
                **extra,
            },
        )

    return publish


class RenderJob:
    """One render at a time per server (PLAN.md § Finishing, docs/plans/STUDIO.md § Step 01).

    Runs the finishing pipeline — `export` → optional `burn` (add_captions)
    → `check_frames` → `verify`, the last two derived from `_run_checks`
    rather than called a second time — in a worker thread, publishing a
    `"stage"` event on the same `render` bus topic after each stage attempt,
    and appending the whole run to `renderlog` exactly once, on every exit
    path (success, error, or cancelled).

    `stop()` kills the encode and deletes whatever the partial output
    currently is, per PLAN.md's explicit "not left". The pipeline runs under
    `progress.cancellable`, so the melt, auto-editor and ffmpeg calls under
    `ops.export` and `ops.add_captions` go through `progress.run`, which kills
    the child's process group when the event is set and raises
    `progress.Cancelled`. Until 2026-09-16 nothing here held that handle, and
    Stop took effect only when melt finished. The output is still deleted on
    both ends — on the thread that called `stop()`, and again when the
    background call returns — since a stage that is not a subprocess runs on.

    Only `export` and `burn` can fail or be cancelled — `check_frames` and
    `verify` are read off `_run_checks`'s own return value, which already
    turns a per-check `EXPECTED` failure into `{"skipped": True, "reason":
    ...}` rather than raising, so those two stages are always `"done"` or
    `"skipped"`, never `"error"`/`"cancelled"`.

    `_running` is a plain flag guarded by the lock, not `Thread.is_alive()`:
    a `Thread` object is not alive until `.start()` actually runs, so gating
    "busy" on liveness would let a second `/api/render` slip through in the
    window between constructing the thread and starting it.
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._running = False
        self._cancel: threading.Event | None = None
        self._output: Path | None = None

    def _output_path(self, job_id: str) -> Path:
        """`renders/web-<job_id><suffix>`.

        The suffix follows the primary clip's own media — the same clip
        `ops.export` treats as primary (`edit.segments[0]`) — so the
        container this picks matches the one auto-editor is about to write.
        **Except on a layered timeline**, which melt renders as picture over
        the VO: a `.wav` primary would then name a container that cannot hold
        the video the render is for. `layered` is `ops.timeline_view`'s answer,
        not a second reading of the manifest here — the UI never decides
        (CLAUDE.md).

        Everything here can raise before any job state is touched, which is
        what makes an empty timeline a 400 rather than a job that starts only
        to immediately error.
        """
        project = Project.open(self.project_root)
        view = ops.timeline_view(str(self.project_root))
        segments = view.get("segments") or []
        if not segments:
            raise WebUIError("the timeline is empty — nothing to render")
        clip = media.get_clip(project, segments[0]["clip_id"])
        suffix = ".mp4" if view.get("layered") else (media.media_path(project, clip).suffix or ".mp4")
        return project.render_dir / f"web-{job_id}{suffix}"

    def start(
        self,
        preset: str | None,
        resolution: tuple[int, int] | None = None,
        burn: bool | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        output = self._output_path(job_id)
        cancel = threading.Event()
        with self._lock:
            if self._running:
                raise RenderBusyError("a render is already running")
            self._running = True
            self._cancel = cancel
            self._output = output
        threading.Thread(
            target=self._run,
            args=(job_id, output, cancel, preset, resolution, burn),
            daemon=True,
        ).start()
        return job_id

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            cancel = self._cancel
            output = self._output
        assert cancel is not None
        cancel.set()
        self._delete(output)

    def _finish(self) -> None:
        with self._lock:
            self._running = False

    @staticmethod
    def _delete(output: Path | None) -> None:
        if output is None:
            return
        try:
            if output.exists():
                output.unlink()
        except OSError:
            pass

    def _report_error(self, exc: Exception, job_id: str) -> None:
        # Reached only for EXPECTED exceptions (CLAUDE.md's family) — anything
        # outside it is a bug and is left to propagate and keep its traceback
        # (the same rule the HTTP handlers follow), rather than being caught
        # here and flattened into a fake completion event.
        self.bus.publish("render", {"job_id": job_id, "status": "error", "error": str(exc)})

    def _run(
        self,
        job_id: str,
        output: Path,
        cancel: threading.Event,
        preset: str | None,
        resolution: tuple[int, int] | None,
        burn: bool | None,
    ) -> None:
        self.bus.publish(
            "render",
            {
                "job_id": job_id,
                "status": "running",
                "preset": preset,
                "resolution": list(resolution) if resolution else None,
            },
        )
        # The finally is a backstop for non-EXPECTED exceptions only: a bug
        # still propagates with its traceback (the HTTP handlers' rule), but
        # it must not leave `_running` latched — that would turn one bug into
        # a permanent 409 for every render until the server restarts.
        try:
            with progress.cancellable(cancel):
                self._run_inner(job_id, output, cancel, preset, resolution, burn)
        finally:
            self._finish()

    def _run_inner(
        self,
        job_id: str,
        output: Path,
        cancel: threading.Event,
        preset: str | None,
        resolution: tuple[int, int] | None,
        burn: bool | None,
    ) -> None:
        project = Project.open(self.project_root)
        # Read once, before `export` runs — this is "what the project claims
        # its own finished length is" at the moment the render was asked for,
        # the same number `check_frames`/`verify` measure a render against.
        expected_duration = ops.status(str(self.project_root))["expected_duration"]
        stages_log: dict[str, dict[str, Any]] = {}

        def publish_stage(stage: str, outcome: str, detail: dict[str, Any] | None = None) -> None:
            stages_log[stage] = {"outcome": outcome, "detail": detail}
            self.bus.publish(
                "render",
                {
                    "job_id": job_id,
                    "status": "stage",
                    "stage": stage,
                    "outcome": outcome,
                    "detail": detail,
                },
            )

        def append_run(final_output: Path) -> None:
            renderlog.append(
                project,
                output=str(final_output),
                preset=preset,
                expected_duration=expected_duration,
                stages=stages_log,
            )

        # -- export ------------------------------------------------------
        # Only passed on when actually set, so a plain `/api/render {}` call
        # still hits `ops.export(path, output, export_format=None)` exactly
        # as it did before preset/resolution existed — the shape a stubbed
        # `ops.export` in the test suite still expects.
        export_kwargs: dict[str, Any] = {}
        if preset is not None:
            export_kwargs["preset"] = preset
        if resolution is not None:
            export_kwargs["resolution"] = resolution
        try:
            # `log=False` on both calls here: this pipeline appends the whole
            # run itself, at every exit path, and the two ops learned to log
            # for the clients that have no pipeline (renderlog.py § two
            # writers). Letting them also log would leave `renderlog.last`
            # reading a prefix of this run — an export with no burn stage —
            # instead of the run.
            with refusing_path_too_long(), progress.reporting(
                _job_progress(self.bus, "render", job_id)
            ):
                ops.export(
                    str(self.project_root), str(output), export_format=None, log=False,
                    **export_kwargs,
                )
        except EXPECTED as exc:
            self._finish()
            if cancel.is_set():
                self._delete(output)
                publish_stage("export", "cancelled")
                append_run(output)
                self.bus.publish("render", {"job_id": job_id, "status": "cancelled"})
            else:
                publish_stage("export", "error", {"error": str(exc)})
                append_run(output)
                self._report_error(exc, job_id)
            return

        if cancel.is_set():
            self._finish()
            self._delete(output)
            publish_stage("export", "cancelled")
            append_run(output)
            self.bus.publish("render", {"job_id": job_id, "status": "cancelled"})
            return

        publish_stage("export", "done")

        # -- burn ----------------------------------------------------------
        # `burn is None` means "apply the project's own default": on when a
        # caption style is configured, off otherwise — docs/plans/STUDIO.md's rule.
        # `burn is True`/`burn is False` overrides it explicitly either way.
        should_burn = (
            ops.CAPTION_STYLE_KEY in project.read_manifest() if burn is None else burn
        )
        final_output = output
        if should_burn:
            try:
                # `burn` names the video to burn onto — the file `export` just
                # wrote — never the untrimmed source (CLAUDE.md: burning onto
                # the source lines captions up against audio that has moved).
                with refusing_path_too_long():
                    result = ops.add_captions(
                        str(self.project_root), str(output.with_suffix(".ass")),
                        burn=str(output), log=False,
                    )
            except EXPECTED as exc:
                self._finish()
                if cancel.is_set():
                    publish_stage("burn", "cancelled")
                    append_run(output)
                    self.bus.publish("render", {"job_id": job_id, "status": "cancelled"})
                else:
                    publish_stage("burn", "error", {"error": str(exc)})
                    append_run(output)
                    self._report_error(exc, job_id)
                return
            final_output = Path(result["burned"])
            if cancel.is_set():
                self._finish()
                self._delete(final_output)
                publish_stage("burn", "cancelled")
                append_run(final_output)
                self.bus.publish("render", {"job_id": job_id, "status": "cancelled"})
                return
            publish_stage("burn", "done")
        else:
            publish_stage("burn", "skipped", {"reason": "burn not requested"})

        try:
            # (a) auto-editor's/melt's exit code does not mean success — this
            # probes the actual file that landed on disk, the same way every
            # other check here reads a render rather than trusting a
            # subprocess's own report of itself.
            info = media.probe(final_output)
        except EXPECTED as exc:
            self._finish()
            append_run(final_output)
            self._report_error(exc, job_id)
            return

        # -- check_frames / verify, derived from `_run_checks` --------------
        # (b) the existing checks, run here rather than left for a person to
        # remember — see `_run_checks`. Neither can fail this pipeline:
        # `_run_checks` already turns a per-check `EXPECTED` failure into a
        # `skipped` entry rather than raising.
        checks = _run_checks(self.project_root, final_output, info.has_video)

        check_frames_result = checks.get("check_frames", {})
        if check_frames_result.get("skipped"):
            publish_stage(
                "check_frames", "skipped", {"reason": check_frames_result.get("reason")}
            )
        else:
            publish_stage("check_frames", "done", {"agrees": check_frames_result.get("agrees")})

        verify_result = checks.get("verify", {})
        if verify_result.get("skipped"):
            publish_stage("verify", "skipped", {"reason": verify_result.get("reason")})
        else:
            publish_stage("verify", "done", {"similarity": verify_result.get("similarity")})

        self._finish()
        append_run(final_output)
        self.bus.publish(
            "render",
            {
                "job_id": job_id,
                "status": "done",
                "output": str(final_output),
                "width": info.width,
                "height": info.height,
                "duration": info.duration,
                "has_video": info.has_video,
                "has_audio": info.has_audio,
                "checks": checks,
            },
        )


class ProxyJob:
    """One proxy transcode at a time per server (PLAN.md § The preview proxy).

    `RenderJob`'s pattern, deliberately: the lock, the plain `_running` flag
    rather than `Thread.is_alive()`, everything that can raise computed
    *before* any job state is touched so a bad asset is a 400 rather than a
    job that starts only to immediately fail, `_finish()` in a `finally` so a
    bug cannot latch the slot into a permanent 409, and completion published
    on the same bus the SSE handler already serves. There is no GET-by-job-id
    route in this codebase and this adds none.

    **The single slot is a decision the design note left open, taken here and
    worth stating.** A proxy is keyed by *asset*, not by project, so unlike a
    render there is a real case for concurrency: a timeline can show several
    unplayable shots, and one slot means the second one clicked gets a 409
    while the first encodes. It is taken anyway because nothing measures the
    alternative — settling it needs a project carrying more than one
    unplayable asset, which this box does not have — and because a wrong
    single slot costs a retry while a wrong parallel one costs N concurrent
    x264 encodes on a box that is also running melt. Revisit with real
    footage, not with reasoning.

    There is no `/api/proxy/stop`. Cancelling is safe by construction rather
    than by handling: `ops.proxy_transcode` writes the sidecar key only after
    ffmpeg returns, so an interrupted job leaves an unkeyed file that
    `proxy_is_current` reads as no proxy at all.
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._running = False

    def start(self, clip_id: str, *, force: bool = False) -> str:
        job_id = uuid.uuid4().hex
        # Resolve before claiming the slot: an unknown clip_id, missing media,
        # an already-playable file and a streamless one all raise here, on the
        # request thread, where they become a 400.
        project = Project.open(self.project_root)
        media.get_clip(project, clip_id)
        with self._lock:
            if self._running:
                raise ProxyBusyError("a proxy transcode is already running")
            self._running = True
        threading.Thread(target=self._run, args=(job_id, clip_id, force), daemon=True).start()
        return job_id

    def _finish(self) -> None:
        with self._lock:
            self._running = False

    def _run(self, job_id: str, clip_id: str, force: bool) -> None:
        self.bus.publish("proxy", {"job_id": job_id, "status": "running", "clip_id": clip_id})
        try:
            try:
                with refusing_path_too_long():
                    result = ops.proxy_transcode(str(self.project_root), clip_id, force=force)
            except EXPECTED as exc:
                # Same rule as `RenderJob._report_error`: only proofcut's own
                # refusals are flattened into an event. Anything else is a bug
                # and keeps its traceback.
                self.bus.publish(
                    "proxy",
                    {"job_id": job_id, "status": "error", "clip_id": clip_id, "error": str(exc)},
                )
                return
            self.bus.publish("proxy", {"job_id": job_id, "status": "done", **result})
        finally:
            self._finish()


class ReframeSheetJob:
    """One sheet generation at a time per server (docs/plans/STUDIO.md § Step 03, Frame mode).

    `ProxyJob`'s exact shape: the lock, the plain `_running` flag, everything
    that can raise (`Project.open`) resolved on the request thread before the
    slot is claimed, a dedicated `*BusyError` for a distinct 409, `_finish()`
    in a `finally`, and completion published on the same bus the SSE handler
    already serves.

    `extremes` is accepted for parity with `ops.reframe_sheet`'s own
    signature, but `frame.js` never sends `extremes: true` in this step — a
    later step's draggable/extreme-probe review can turn it on without this
    job changing shape.
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._running = False

    def start(
        self,
        *,
        moments: list[float] | None = None,
        extremes: bool = False,
        out: str | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        # A bad/unopenable project is caught here, on the request thread,
        # before the slot is touched — the `ProxyJob` precedent.
        Project.open(self.project_root)
        with self._lock:
            if self._running:
                raise ReframeSheetBusyError("a reframe sheet is already generating")
            self._running = True
        threading.Thread(
            target=self._run, args=(job_id, moments, extremes, out), daemon=True
        ).start()
        return job_id

    def _finish(self) -> None:
        with self._lock:
            self._running = False

    def _run(
        self, job_id: str, moments: list[float] | None, extremes: bool, out: str | None
    ) -> None:
        self.bus.publish("reframe-sheet", {"job_id": job_id, "status": "running"})
        try:
            try:
                with refusing_path_too_long(), progress.reporting(
                    _job_progress(self.bus, "reframe-sheet", job_id)
                ):
                    result = ops.reframe_sheet(
                        str(self.project_root), out=out, moments=moments, extremes=extremes
                    )
            except EXPECTED as exc:
                self.bus.publish(
                    "reframe-sheet", {"job_id": job_id, "status": "error", "error": str(exc)}
                )
                return
            self.bus.publish("reframe-sheet", {"job_id": job_id, "status": "done", **result})
        finally:
            self._finish()


class ReframeDetectJob:
    """One detect pass at a time per server (docs/plans/STUDIO.md § Step 03, Frame mode).

    `ProxyJob`'s exact shape. **`apply` is not a parameter of `.start()` at
    all** — it is hard-coded `False` in the call to `ops.reframe_detect`,
    enforced here at the job layer (and again at the HTTP layer, in
    `_handle_reframe_detect_start`, which refuses even a hand-crafted
    request naming the key) — the one flag docs/plans/STUDIO.md is explicit about:
    "`apply` stays off — it proposes, the sheet judges."

    This job always needs `PROOFCUT_FACE` — `ops.reframe_detect` raises
    `FaceError` unconditionally, before the scene scan, when no interpreter
    is available — which is why `FaceError` is in `EXPECTED` above: without
    it, a missing detector would propagate out of `_run`'s worker thread
    with no handler, `_finish()` would never run, and the slot would latch
    busy forever while the view spins on an SSE event that never arrives.
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._running = False

    def start(
        self,
        *,
        clip_id: str | None = None,
        threshold: float | None = None,
        frames: int | None = None,
        split: bool = True,
    ) -> str:
        job_id = uuid.uuid4().hex
        Project.open(self.project_root)
        with self._lock:
            if self._running:
                raise ReframeDetectBusyError("a reframe detect pass is already running")
            self._running = True
        threading.Thread(
            target=self._run,
            args=(job_id, clip_id, threshold, frames, split),
            daemon=True,
        ).start()
        return job_id

    def _finish(self) -> None:
        with self._lock:
            self._running = False

    def _run(
        self,
        job_id: str,
        clip_id: str | None,
        threshold: float | None,
        frames: int | None,
        split: bool,
    ) -> None:
        self.bus.publish("reframe-detect", {"job_id": job_id, "status": "running"})
        try:
            try:
                with refusing_path_too_long(), progress.reporting(
                    _job_progress(self.bus, "reframe-detect", job_id)
                ):
                    result = ops.reframe_detect(
                        str(self.project_root),
                        clip_id=clip_id,
                        threshold=ops.SCENE_THRESHOLD if threshold is None else threshold,
                        frames=ops.DETECT_FRAMES if frames is None else frames,
                        apply=False,
                        split=split,
                    )
            except EXPECTED as exc:
                self.bus.publish(
                    "reframe-detect", {"job_id": job_id, "status": "error", "error": str(exc)}
                )
                return
            self.bus.publish("reframe-detect", {"job_id": job_id, "status": "done", **result})
        finally:
            self._finish()


class ImportJob:
    """One import at a time per server (docs/plans/STUDIO.md § Cross-cutting: footage in
    becomes a window operation, not a CLI-only step).

    `ProxyJob`'s exact shape: the lock, the plain `_running` flag, everything
    that can raise resolved on the request thread before the slot is
    claimed, a dedicated `*BusyError` for a distinct 409, `_finish()` in a
    `finally`, and completion published on the same bus the SSE handler
    already serves.

    `source` is a **server-side absolute path the client types into the
    window** — not an upload, not a directory browser (decision taken
    before this was built). Import has to be able to reach footage on the
    NAS, so there is no confinement to add beyond the guard every other
    webui mutation already carries (loopback + Host + `application/json`).
    This adds no new caller to `media.preview_path()` and no new route that
    serves arbitrary files — the path only ever flows *into*
    `ops.import_media`, never back out.

    Registering media is a job rather than a plain `_POST_ROUTES` mutation
    (`attach_transcript`'s shape) because `_place`'s symlink is ordinarily
    instant but `copy=True` can be a real file copy off the NAS, and a
    request that long is a dead window (`RenderJob`'s reasoning).
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._running = False

    def start(
        self,
        source: str,
        *,
        clip_id: str | None = None,
        copy: bool = False,
        mix: bool = False,
        audio_stream: int | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        # Resolve before claiming the slot: a bad project and a bad source
        # path both raise here, on the request thread, where they become a
        # 400 rather than a job that starts only to fail immediately.
        # `media.import_media`'s own error for a bad path routes through
        # `probe`'s ffprobe call, and while it is not wrong (ffprobe says
        # "Is a directory" / "Permission denied" plainly enough), it arrives
        # wrapped inside "ffprobe failed on <path>: <stderr>" and depends on
        # ffprobe's own phrasing. A directory is the mistake a person
        # actually makes typing a path by hand — pointing at a folder of
        # clips instead of one clip in it — so it gets a plain refusal here
        # instead, alongside the other two ways a hand-typed path is wrong.
        Project.open(self.project_root)
        resolved = Path(source).expanduser()
        if not resolved.exists():
            raise WebUIError(f"no such file: {resolved}")
        if resolved.is_dir():
            raise WebUIError(
                f"{resolved} is a directory, not a media file — name one clip inside it"
            )
        if not os.access(resolved, os.R_OK):
            raise WebUIError(f"cannot read {resolved} — check file permissions")
        with self._lock:
            if self._running:
                raise ImportBusyError("an import is already running")
            self._running = True
        threading.Thread(
            target=self._run, args=(job_id, source, clip_id, copy, mix, audio_stream), daemon=True
        ).start()
        return job_id

    def _finish(self) -> None:
        with self._lock:
            self._running = False

    def _run(
        self,
        job_id: str,
        source: str,
        clip_id: str | None,
        copy: bool,
        mix: bool = False,
        audio_stream: int | None = None,
    ) -> None:
        self.bus.publish("import", {"job_id": job_id, "status": "running", "source": source})
        try:
            try:
                with refusing_path_too_long():
                    result = ops.import_media(
                        str(self.project_root),
                        source,
                        clip_id=clip_id,
                        copy=copy,
                        mix=mix,
                        audio_stream=audio_stream,
                    )
            except media.MultiAudioError as exc:
                # The one refusal a client can *act* on rather than only
                # print: the window offers to sum the mics and re-post.
                # `streams` rides the event so the pane never has to
                # string-match the sentence to know which refusal this is.
                self.bus.publish(
                    "import",
                    {
                        "job_id": job_id,
                        "status": "error",
                        "error": str(exc),
                        "audio_streams": exc.streams,
                        "source": source,
                    },
                )
                return
            except (*EXPECTED, OSError) as exc:
                # Same rule as `ProxyJob._run`: proofcut's own refusals (an
                # already-registered clip_id, a source ffprobe cannot read)
                # are flattened into an event. `OSError` joins them here
                # specifically because `copy=True` routes through
                # `media._place`'s `shutil.copy2`, which has no try/except of
                # its own (unlike the symlink branch beside it) — a disk-full,
                # permission, or dropped-NAS-connection failure mid-copy would
                # otherwise escape both `except` clauses, skip the `error`
                # event, and leave the slot released but the window's import
                # form latched busy forever (`_handle_import_start`'s only
                # reload signal is this job's own event). Anything else is
                # still a bug and keeps its traceback.
                self.bus.publish("import", {"job_id": job_id, "status": "error", "error": str(exc)})
                return
            self.bus.publish("import", {"job_id": job_id, "status": "done", **result})
        finally:
            self._finish()


class TranscribeJob:
    """One transcription at a time per server (docs/plans/STUDIO.md § Cross-cutting).

    `ProxyJob`'s exact shape. Wraps `ops.transcribe`, the ASR-driven sibling
    of `attach_transcript` — this is a job because whisper on real footage is
    minutes of work; `attach_transcript` stays a plain `_POST_ROUTES`
    mutation (below) because parsing a transcript file that already has word
    timings is instant.

    **`_revision()` never sees this job's own write, and that is not a bug
    to fix here.** `ops.transcribe` writes a transcript file
    (`project.transcript_path(clip_id)`) and touches neither `project.otio`
    nor the manifest, and `_revision()` above stats only those two files
    plus undo depth — so finishing a transcription fires no `project-changed`
    event. This job's own `done` event on the `"transcribe"` bus topic is the
    client's *only* reload signal; a panel that reloads on `project-changed`
    and ignores its own job's `done` event will wait forever.

    **`ASRError` has to be in `EXPECTED`, or a missing whisper binary would
    leave this slot latched busy with no event ever published** — the same
    `FaceError` precedent `ReframeDetectJob` documents above. It already is:
    `proofcut.asr.ASRError` is imported and listed in the tuple at the top of
    this file (for `verify --windowed`'s sake, predating this job) — this
    note is for whoever next reorders that tuple and assumes it is only
    about `verify`.
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._running = False

    def start(
        self, clip_id: str, *, model: str | None = None, language: str | None = None
    ) -> str:
        job_id = uuid.uuid4().hex
        # Resolve before claiming the slot: an unknown clip_id raises here,
        # on the request thread, where it becomes a 400 — the `ProxyJob`
        # precedent (`media.get_clip` there, the same call here).
        project = Project.open(self.project_root)
        media.get_clip(project, clip_id)
        with self._lock:
            if self._running:
                raise TranscribeBusyError("a transcription is already running")
            self._running = True
        threading.Thread(
            target=self._run, args=(job_id, clip_id, model, language), daemon=True
        ).start()
        return job_id

    def _finish(self) -> None:
        with self._lock:
            self._running = False

    def _run(self, job_id: str, clip_id: str, model: str | None, language: str | None) -> None:
        self.bus.publish(
            "transcribe", {"job_id": job_id, "status": "running", "clip_id": clip_id}
        )
        try:
            try:
                # `model` only passed through when set, so `asr.DEFAULT_MODEL`
                # stays the single place the default is spelled out — naming
                # it again here would give it a second definition to drift.
                kwargs: dict[str, Any] = {"language": language}
                if model is not None:
                    kwargs["model"] = model
                with refusing_path_too_long(), progress.reporting(
                    _job_progress(self.bus, "transcribe", job_id, clip_id=clip_id)
                ):
                    result = ops.transcribe(str(self.project_root), clip_id, **kwargs)
            except EXPECTED as exc:
                self.bus.publish(
                    "transcribe",
                    {"job_id": job_id, "status": "error", "clip_id": clip_id, "error": str(exc)},
                )
                return
            self.bus.publish("transcribe", {"job_id": job_id, "status": "done", **result})
        finally:
            self._finish()


class SeedJob:
    """Laying a clip down as the timeline — one at a time per server.

    `TranscribeJob`'s exact shape, and a job for the same reason: seeding
    runs auto-editor's silence pass over the whole recording, which is real
    seconds of work on a real voiceover, and a request that long is a dead
    window (`RenderJob`'s reasoning).

    **This is the one existing op that had no window route at all**, which
    made a freshly created project a dead end in the picker: `ops.status`
    refuses a project with no timeline, so the scan classifies it `error`
    and nothing in the page could take it forward (docs/plans/POLISH.md § Step 06).

    Unlike transcribe, this one *does* move `project.otio` and the manifest,
    so `_revision()` sees it and `project-changed` fires on its own. The
    `done` event is still what carries the op's own return value — segment
    count, source and timeline duration, whether silences were removed — and
    a client that reloads the page instead of reading that event throws the
    completion report away (CLAUDE.md § Import and transcribe became window
    operations).
    """

    def __init__(self, project_root: Path, bus: EventBus) -> None:
        self.project_root = project_root
        self.bus = bus
        self._lock = threading.Lock()
        self._running = False

    def start(self, clip_id: str, *, remove_silences: bool = True) -> str:
        job_id = uuid.uuid4().hex
        # Resolve before claiming the slot, `TranscribeJob.start`'s
        # precedent: an unknown clip_id becomes a 400 on the request thread
        # rather than a job that starts only to fail immediately.
        project = Project.open(self.project_root)
        media.get_clip(project, clip_id)
        with self._lock:
            if self._running:
                raise SeedBusyError("a seed is already running")
            self._running = True
        threading.Thread(target=self._run, args=(job_id, clip_id, remove_silences), daemon=True).start()
        return job_id

    def _finish(self) -> None:
        with self._lock:
            self._running = False

    def _run(self, job_id: str, clip_id: str, remove_silences: bool) -> None:
        self.bus.publish("seed", {"job_id": job_id, "status": "running", "clip_id": clip_id})
        try:
            try:
                with refusing_path_too_long():
                    result = ops.seed_timeline(
                        str(self.project_root), clip_id, remove_silences=remove_silences
                    )
            except EXPECTED as exc:
                # `AutoEditorError` is in `EXPECTED` for `ImportJob`'s own
                # reason: a missing or stale auto-editor binary must arrive
                # as an event, or this slot latches busy with nothing ever
                # published and the window's setup card waits forever.
                self.bus.publish(
                    "seed",
                    {"job_id": job_id, "status": "error", "clip_id": clip_id, "error": str(exc)},
                )
                return
            self.bus.publish("seed", {"job_id": job_id, "status": "done", **result})
        finally:
            self._finish()


class Handler(BaseHTTPRequestHandler):
    """One request. `project_root` and `verbose` are set by `make_server`.

    `root_dir` is the picker's own flag: `None` (its default, unchanged for
    every `-C` server `make_server` builds) means this class has a fixed
    `project_root` and behaves exactly as it always has. Set (by
    `make_picker_server`) it means `project_root` does not exist *yet* —
    `self.server.bound_root` is `None` until `POST /api/open` picks one, and
    every route below is reachable only after that (`_route_picker`,
    `_handle_open`). Once open, `project_root` is set the same way `-C`
    always set it and this instance is, for the rest of the process, an
    ordinary single-project handler."""

    project_root: Path
    root_dir: Path | None = None
    verbose: bool = False
    #: `None` — the default, and what every server built without
    #: `--allow-remote` gets — means no token guard at all: loopback + Host
    #: is the whole credential, exactly as it has always been. A string means
    #: every request must present it, as `?t=` or as the cookie a `?t=`
    #: request is handed back. Set by `make_server`/`make_picker_server` out
    #: of `remote_policy`.
    token: str | None = None
    #: Host values this server answers to. Widened past loopback only by
    #: `remote_policy`, and never to "anything" — a guard that accepts any
    #: Host header is not a guard.
    allowed_hosts: frozenset[str] = _LOOPBACK_NAMES
    #: Per-request: set by `_refusal` when the token arrived as `?t=`, read
    #: by `_send`, which is what hands the cookie back.
    _issue_cookie: bool = False
    server_version = "proofcut"
    sys_version = ""
    #: Keep-alive, so seeking a video does not reopen a connection per range.
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        # Media playback issues a request per range; logging every one buries
        # the startup line the user actually needs. Off unless asked for.
        if self.verbose:
            super().log_message(fmt, *args)

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if name.startswith("[") and "]" in name:
            name = name[: name.index("]") + 1]
        return name.lower() in self.allowed_hosts

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        try:
            morsel = SimpleCookie(raw).get(TOKEN_COOKIE)
        except Exception:  # noqa: BLE001 — a malformed Cookie header is a 403, never a 500
            return ""
        return morsel.value if morsel is not None else ""

    def _refusal(self, query: str = "") -> str | None:
        """`None` if this request may proceed, else the message to 403 with.

        Both guards in one place because they are one decision — "may this
        request reach the project" — and because the token half must never
        be reachable without the Host half having run first.
        """
        # Reset first, not only on the success path: this handler instance is
        # reused for every request on a keep-alive connection, so a `?t=`
        # request would otherwise leave the flag set and stamp the cookie
        # onto the 403 of a later request that presented nothing.
        self._issue_cookie = False
        if not self._host_allowed():
            if self.allowed_hosts == _LOOPBACK_NAMES:
                return "this server answers loopback requests only"
            return (
                "this server answers requests naming "
                + ", ".join(sorted(self.allowed_hosts))
                + " only"
            )
        if self.token is None:
            return None
        from_query = (parse_qs(query).get("t") or [""])[0]
        # Constant-time: this token is the only thing standing between the
        # tailnet and a server that can rewrite the edit (`reviewserver.py`
        # makes the same comparison for a smaller blast radius).
        if not hmac.compare_digest(from_query or self._cookie_token(), self.token):
            return (
                "this server needs the access token it printed at startup — "
                "open the ?t=... URL it gave you"
            )
        self._issue_cookie = bool(from_query)
        return None

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This page is only ever served to a person sitting at this machine,
        # and it embeds no third-party anything. Say so, so a stray injection
        # has nowhere to phone home to. `img-src` admits `data:` for one
        # reason: a tool result's image arrives inline over this server's own
        # event stream, and the agent pane draws it as a data URL — under
        # `default-src 'self'` alone every such picture was a broken-image
        # icon, silently, in the third recorded run. A data URL loads
        # nothing from anywhere, so nothing here gains a place to phone.
        self.send_header(
            "Content-Security-Policy", "default-src 'self'; media-src 'self'; img-src 'self' data:"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        # No `?t=` token leaves this origin in a Referer header. Cheap, and
        # the only way the credential could walk out of a page that embeds
        # no third-party anything.
        self.send_header("Referrer-Policy", "no-referrer")
        if self._issue_cookie and self.token is not None:
            # `Secure` is deliberately absent: this is plain HTTP over a
            # WireGuard tunnel, and a `Secure` cookie would simply never be
            # stored, which reads as "the token does not work".
            self.send_header(
                "Set-Cookie",
                f"{TOKEN_COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict",
            )
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _fail(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    # -- routing ---------------------------------------------------------

    def _answering_path_too_long(self, handle: Callable[[], None]) -> None:
        """Run one request, answering Windows' too-long-path `OSError` as a 400.

        Every route already answers proofcut's own refusals as a 400 and lets
        anything else keep its traceback; this is the one `OSError` that is a
        person's folder being too deep rather than a bug, and catching it
        here reaches every route without a clause in each. It cannot reach a
        response already under way, and needs not to: an op raises it before
        a handler sends anything.
        """
        try:
            handle()
        except OSError as exc:
            message = path_too_long(exc)
            if message is None:
                raise
            self._fail(HTTPStatus.BAD_REQUEST, message)

    def do_GET(self) -> None:
        self._answering_path_too_long(lambda: self._route(head_only=False))

    def do_HEAD(self) -> None:
        # Browsers probe media with HEAD before ranging into it.
        self._answering_path_too_long(lambda: self._route(head_only=True))

    def do_POST(self) -> None:
        self._answering_path_too_long(self._post)

    def _post(self) -> None:
        url = urlparse(self.path)
        refusal = self._refusal(url.query)
        if refusal is not None:
            self._fail(HTTPStatus.FORBIDDEN, refusal)
            return
        if self.root_dir is not None:
            # Picker mode. `/api/open` is reachable whether or not a project
            # is bound yet — `_handle_open` is what makes a second call on
            # the same project a no-op and a call naming a *different* one a
            # 409, rather than either being "no such endpoint". Every other
            # route below needs `self.server.agent`/`render_job`/`proxy_job`
            # or a fixed `project_root`, none of which exist before the
            # first successful open.
            if url.path == "/api/open":
                self._handle_open()
                return
            # Create is picker-only for the same reason open is: under `-C`
            # the project already exists. Reachable before a bind, because
            # making the first project is exactly what someone with an empty
            # root has to do before there is anything to open.
            if url.path == "/api/create":
                self._handle_create()
                return
            if not self._project_bound():
                self._fail(HTTPStatus.NOT_FOUND, "no project open yet — pick one at /")
                return
        if url.path == "/api/agent":
            self._handle_agent_prompt()
            return
        if url.path == "/api/agent/stop":
            self._handle_agent_stop()
            return
        if url.path == "/api/agent/new-task":
            self._handle_agent_new_task()
            return
        if url.path == "/api/render":
            self._handle_render_start()
            return
        if url.path == "/api/render/stop":
            self._handle_render_stop()
            return
        if url.path == "/api/proxy":
            self._handle_proxy_start()
            return
        if url.path == "/api/reframe/sheet":
            self._handle_reframe_sheet_start()
            return
        if url.path == "/api/reframe/detect":
            self._handle_reframe_detect_start()
            return
        if url.path == "/api/import":
            self._handle_import_start()
            return
        if url.path == "/api/transcribe":
            self._handle_transcribe_start()
            return
        if url.path == "/api/seed":
            self._handle_seed_start()
            return
        route = _POST_ROUTES.get(url.path)
        if route is None:
            self._fail(HTTPStatus.NOT_FOUND, f"no such endpoint: {url.path}")
            return
        try:
            payload = _json_body(self)
            self._send_json(route(str(self.project_root), payload))
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        except EXPECTED as exc:
            # A refused cut is the normal case here, not a server fault: the
            # suspect-duration guard rejects a boundary precisely so a person
            # reads the message and picks a different word.
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))

    def _project_bound(self) -> bool:
        """Picker mode only: has `POST /api/open` picked a project yet?

        Always `False` on a plain `-C` server, but never checked there —
        `root_dir is None` short-circuits every caller before this runs, so
        a single-project server never even looks at `self.server.bound_root`
        (which does not exist on it).
        """
        return getattr(self.server, "bound_root", None) is not None

    def _route(self, *, head_only: bool) -> None:
        url = urlparse(self.path)
        refusal = self._refusal(url.query)
        if refusal is not None:
            self._fail(HTTPStatus.FORBIDDEN, refusal)
            return
        path = url.path
        if self.root_dir is not None and not self._project_bound():
            self._route_picker(path, url.query, head_only=head_only)
            return
        try:
            if path in ("/", "/index.html"):
                self._send_static("index.html")
            elif path.startswith("/static/"):
                self._send_static(path[len("/static/") :])
            elif path == "/api/view":
                query = parse_qs(url.query)
                clip_id = (query.get("clip_id") or [None])[0]
                view = ops.timeline_view(str(self.project_root), clip_id=clip_id)
                # Best-effort, on the workspace's first real data fetch —
                # never lets a poster failure turn a page load into a 400
                # (Studio Step 04 contract § C).
                try:
                    _ensure_poster(Project.open(self.project_root), view)
                except Exception:  # noqa: BLE001, S110 — a poster is best-effort, never fatal
                    pass
                self._send_json(view)
            elif path == "/api/session":
                self._send_json(_session_get(str(self.project_root)))
            elif path == "/api/captions":
                query = parse_qs(url.query)
                clip_id = (query.get("clip_id") or [None])[0]
                self._send_json(ops.caption_view(str(self.project_root), clip_id=clip_id))
            elif path == "/api/assets":
                self._send_json(ops.assets(str(self.project_root)))
            elif path == "/api/properties":
                query = parse_qs(url.query)
                clip_id = (query.get("clip_id") or [None])[0]
                raw_word = (query.get("word_index") or [None])[0]
                word_index = self._int_query(raw_word, "word_index")
                self._send_json(
                    ops.properties(str(self.project_root), clip_id=clip_id, word_index=word_index)
                )
            elif path == "/api/finish":
                # `?framing=1` opts into the scene-cut scan. Off by default
                # and deliberately so: the truth strip re-reads this route on
                # every `project-changed` event, and the framing section
                # costs 5.7s wall / 46s CPU on the film, uncached — every cut
                # would have paid it for a number nothing on screen asked to
                # change. Frame mode asks for it when it opens.
                query = parse_qs(url.query)
                want_framing = (query.get("framing") or ["0"])[0] not in ("", "0", "false")
                self._send_json(
                    ops.finish_report(str(self.project_root), framing=want_framing)
                )
            elif path == "/api/output":
                self._send_last_render(head_only=head_only)
            elif path == "/api/reframe/coverage":
                query = parse_qs(url.query)
                clip_id = (query.get("clip_id") or [None])[0]
                raw_threshold = (query.get("threshold") or [None])[0]
                threshold = self._float_query(raw_threshold, "threshold")
                self._send_json(
                    ops.reframe_coverage(
                        str(self.project_root),
                        clip_id=clip_id,
                        threshold=ops.SCENE_THRESHOLD if threshold is None else threshold,
                    )
                )
            elif path.startswith("/api/reframe/tile/"):
                name = unquote(path[len("/api/reframe/tile/") :])
                self._send_reframe_tile(name, head_only=head_only)
            elif path == "/api/events":
                self._send_events()
            elif path.startswith("/api/waveform/"):
                clip_id = unquote(path[len("/api/waveform/") :])
                if not clip_id:
                    raise WebUIError("clip id is required")
                self._send_json(ops.waveform(str(self.project_root), clip_id))
            elif path.startswith("/api/thumb/"):
                clip_id = unquote(path[len("/api/thumb/") :])
                if not clip_id:
                    raise WebUIError("clip id is required")
                self._send_thumb(clip_id, url.query, head_only=head_only)
            elif path.startswith("/api/media/"):
                self._send_media(unquote(path[len("/api/media/") :]), head_only=head_only)
            elif path.startswith("/api/asset/"):
                self._send_asset(unquote(path[len("/api/asset/") :]), head_only=head_only)
            elif path.startswith("/api/preview/"):
                asset = unquote(path[len("/api/preview/") :])
                if not asset:
                    raise WebUIError("asset key is required")
                self._send_json(ops.preview_source(str(self.project_root), asset))
            else:
                self._fail(HTTPStatus.NOT_FOUND, f"no such endpoint: {path}")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))

    # -- handlers --------------------------------------------------------

    def _send_static(self, name: str) -> None:
        # No traversal: a static name is one flat filename out of a directory
        # shipped inside the package, never a path.
        if "/" in name or "\\" in name or name.startswith("."):
            self._fail(HTTPStatus.NOT_FOUND, f"no such asset: {name}")
            return
        target = STATIC_DIR / name
        if not target.is_file() or target.suffix not in _STATIC_TYPES:
            self._fail(HTTPStatus.NOT_FOUND, f"no such asset: {name}")
            return
        self._send(HTTPStatus.OK, target.read_bytes(), _STATIC_TYPES[target.suffix])

    def _route_picker(self, path: str, query: str, *, head_only: bool) -> None:
        """GET routing while `root_dir` is set and no project is open yet.

        Four routes — the picker page, its own static assets, the scan
        itself, and a project's poster image — because everything else on a
        normal server needs a bound `project_root` or the singletons
        `/api/open` has not built yet. `POST /api/open` is handled in
        `do_POST`, not here; this method only ever answers GET/HEAD.
        """
        try:
            if path in ("/", "/index.html"):
                self._send_static("picker.html")
            elif path.startswith("/static/"):
                self._send_static(path[len("/static/") :])
            elif path == "/api/projects":
                assert self.root_dir is not None
                self._send_json({"root": str(self.root_dir), "projects": scan_projects(self.root_dir)})
            elif path == "/api/poster":
                params = parse_qs(query)
                raw_path = (params.get("path") or [None])[0]
                if not raw_path:
                    raise WebUIError("'path' is required")
                self._send_poster(raw_path, head_only=head_only)
            else:
                self._fail(HTTPStatus.NOT_FOUND, "no project open yet — pick one at /")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))

    def _send_poster(self, raw_path: str, *, head_only: bool) -> None:
        """`GET /api/poster?path=<project>` — the Home gallery's still image.

        Picker-only, modeled directly on `_send_reframe_tile` (Studio Step
        03's own confinement precedent — CLAUDE.md names it as the model to
        copy). The picker never binds and never thumbnails: this only ever
        reads a file a *bound* session already wrote via `_ensure_poster`.

        The symlink refusal is inherited, not re-implemented: `raw_path` is
        matched against `scan_projects`'s own output, and `scan_projects`'s
        `is_symlink()` filter (webui.py, `walk()`) already excludes a
        symlinked directory from that list — a poster request naming a path
        outside the scan, symlinked or not, simply has no matching entry and
        404s here rather than needing a second symlink check. The
        resolved-parent check below is belt-and-suspenders on top of that,
        catching a symlink placed *inside* a scanned project's own `cache/`
        pointing elsewhere on disk — the one thing matching against the scan
        alone would not catch, same reasoning as `_send_reframe_tile`'s own
        third layer.
        """
        assert self.root_dir is not None
        entries = scan_projects(self.root_dir)
        match = next((e for e in entries if e["path"] == raw_path), None)
        if match is None:
            raise WebUIError(f"{raw_path!r} is not a project under {self.root_dir}")
        project = Project(Path(match["path"]))
        poster = project.poster_path
        if poster.resolve().parent != (project.root / CACHE_DIR).resolve():
            raise WebUIError(f"{raw_path!r} has no poster")
        if not poster.is_file():
            raise WebUIError(f"no poster for {raw_path!r} yet")
        self._stream_file(poster, head_only=head_only)

    def _send_media(self, clip_id: str, *, head_only: bool) -> None:
        """Stream a clip's media, honouring Range so the browser can seek.

        Resolution goes through `media.preview_path`, which is `media_path`
        — the attenuated copy when one exists, so the preview is of the audio
        that will actually be exported (CLAUDE.md) — *unless* a current proxy
        exists, in which case it is that. This is one of the two preview-side
        callers allowed to resolve that way; nothing that renders is among
        them (PLAN.md § The preview proxy transcode).
        """
        project = Project.open(self.project_root)
        clip = media.get_clip(project, clip_id)
        source = media.preview_path(project, clip)
        if not source.is_file():
            raise WebUIError(f"{clip_id}'s media is missing from disk: {source}")
        self._stream_file(source, head_only=head_only)

    def _send_asset(self, asset: str, *, head_only: bool) -> None:
        """Stream one picture asset — a cue's `card:name` or clip_id — to the viewer.

        Separate from `_send_media` because the two resolve differently and only
        one of them is addressed by clip_id: a card is not a clip and never will
        be. Both end in the same `_stream_file`, so Range behaves identically —
        which matters more here than for the transport, since the picture layer
        seeks constantly and every seek aborts an in-flight range.

        Resolution and the traversal check are `ops.preview_source`'s, not a
        second copy: the untrusted-string case is exactly the one that must have
        one implementation.
        """
        if not asset:
            raise WebUIError("asset key is required")
        resolved = ops.preview_source(str(self.project_root), asset)
        self._stream_file(Path(resolved["path"]), head_only=head_only)

    def _send_thumb(self, clip_id: str, query: str, *, head_only: bool) -> None:
        """`GET /api/thumb/<clip_id>?at=<seconds>` — one filmstrip frame.

        `ops.thumbnail` does the caching and the containment (it writes
        under `cache/thumbs/`, never the manifest, and is never resolved by
        `media.media_path`/`preview_path` — CLAUDE.md's split); this is only
        the fourth caller into `_stream_file`, so a thumbnail seeks and
        Ranges exactly like every other picture asset the viewer draws.
        """
        params = parse_qs(query)
        at = self._float_query((params.get("at") or [None])[0], "at", required=True)
        interval_raw = (params.get("interval") or [None])[0]
        parsed_interval = self._float_query(interval_raw, "interval")
        interval = ops.THUMB_INTERVAL if parsed_interval is None else parsed_interval
        resolved = ops.thumbnail(str(self.project_root), clip_id, at, interval=interval)
        self._stream_file(Path(resolved["path"]), head_only=head_only)

    def _send_reframe_tile(self, name: str, *, head_only: bool) -> None:
        """`GET /api/reframe/tile/<name>` — one PNG frame out of `cache/sheets`.

        The security-relevant route in Studio Step 03 (contract § B). `name`
        is confined to `project.sheet_dir` exactly the way `ops.thumbnail`'s
        cache convention is confined (CLAUDE.md's named precedent) — never
        through `media.preview_path()`, which gains no new caller here.

        Three layers, each catching something the others do not:

        1. `Path(name).name` strips any directory component — this alone
           refuses `../../etc/passwd` (becomes `passwd`, which then simply
           fails the `is_file()` check below) and an absolute path
           (`Path("/etc/passwd").name == "passwd"`, same outcome).
        2. A belt-and-suspenders character blacklist, `_send_asset`'s
           `card:` style, catching anything step 1's silent stripping might
           otherwise let through unnoticed.
        3. A resolved-parent check — the symlink defence: a symlink *placed
           inside* `cache/sheets` pointing outside it has a bare name with
           no `/` or `..` in it at all, so steps 1-2 alone would pass it.
        """
        if not name:
            raise WebUIError("tile name is required")
        if ".." in name or "\\" in name:
            raise WebUIError(f"{name!r} does not name a sheet tile")
        if name != Path(name).name:
            raise WebUIError(f"{name!r} does not name a sheet tile")
        project = Project.open(self.project_root)
        target = project.sheet_dir / name
        if target.resolve().parent != project.sheet_dir.resolve():
            raise WebUIError(f"{name!r} does not name a sheet tile")
        if not target.is_file():
            raise WebUIError(f"no such tile: {name}")
        self._stream_file(target, head_only=head_only)

    def _send_last_render(self, *, head_only: bool) -> None:
        """`GET /api/output` — stream the file the last pipeline run produced.

        The window rebuilds the picture live from the cue table and never
        reads `renders/`, so Export wrote a file the page could not open,
        name, or say anything about (PLAN.md § Should the workspace play its
        own output?). This is the narrow answer: the *one* file the render
        log's last run recorded, resolved through `renderlog.last` — never a
        listing of `renders/`, and never a path the client gets to name.

        **Not a third caller of `media.preview_path()`.** A render is not a
        preview and must never be resolvable as one, which is the whole point
        of that split (CLAUDE.md § The preview proxy) — this resolves through
        the render log and streams the file directly, the way
        `_send_reframe_tile` resolves through `cache/sheets`.

        The confinement is not ceremony even though proofcut writes the log
        itself: a loopback server that streams whatever absolute path a JSON
        file happens to name is a file server, and one hand-edited line
        should not turn this into one. So the resolved path has to sit inside
        the project directory, and `exists` is re-checked here rather than
        trusted from the report — a render deleted after its run leaves a log
        line that still names it.
        """
        project = Project.open(self.project_root)
        run = renderlog.last(project)
        if run is None:
            raise WebUIError(
                "nothing has been rendered from this project yet — Finish → Render writes one"
            )
        named = str(run.get("output") or "")
        if not named:
            raise WebUIError("the last render recorded no output path")
        target = Path(named)
        root = project.root.resolve()
        try:
            resolved = target.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            raise WebUIError(f"the last render's output is not inside this project: {named}") from None
        if not resolved.is_file():
            raise WebUIError(f"the last render's output is no longer on disk: {named}")
        self._stream_file(resolved, head_only=head_only)

    @staticmethod
    def _int_query(raw: str | None, name: str) -> int | None:
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            raise WebUIError(f"{name!r} must be an integer, not {raw!r}") from None

    @staticmethod
    def _float_query(raw: str | None, name: str, *, required: bool = False) -> float | None:
        if raw is None:
            if required:
                raise WebUIError(f"{name!r} is required")
            return None
        try:
            return float(raw)
        except ValueError:
            raise WebUIError(f"{name!r} must be a number, not {raw!r}") from None

    def _stream_file(self, source: Path, *, head_only: bool) -> None:
        """Byte-range streaming, shared by every route that hands over a file."""
        _stream_file(self, source, head_only=head_only)

    def _send_events(self) -> None:
        """`GET /api/events` — a long-lived `text/event-stream`.

        One event type is polled (`project-changed`, off `_revision`); two
        are pushed (`agent`, `render`) through `self.server.bus`, which other
        server code publishes onto — the agent's stdout reader and the render
        job. No `Content-Length`: the response ends only when the client
        disconnects, which is also the only way this method returns.
        """
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        bus: EventBus = self.server.bus  # type: ignore[attr-defined]
        subscription = bus.subscribe()
        try:
            last = _revision(self.project_root)
            self._write_sse("project-changed", {"revision": last})
            while True:
                try:
                    event, data = subscription.get(timeout=_REVISION_POLL_SECONDS)
                    self._write_sse(event, data)
                except queue.Empty:
                    pass
                current = _revision(self.project_root)
                if current != last:
                    last = current
                    self._write_sse("project-changed", {"revision": last})
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The browser navigated away or closed the tab. Routine, not an
            # error — the same treatment `_send_media` gives an aborted seek.
            return
        finally:
            bus.unsubscribe(subscription)

    def _write_sse(self, event: str, data: dict[str, Any]) -> None:
        chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    def _handle_agent_prompt(self) -> None:
        """`POST /api/agent {"prompt": ..., "model": optional}` — 202, the
        work happens on the stream.

        The reply is an acknowledgement, not a result: what the agent does
        arrives as `agent` events on `/api/events`, the same feed a person's
        own cut lands in (PLAN.md § Where the cut controls go). `model` rides
        the same call rather than a side-channel setter — `AgentSession.send`
        is what decides whether it actually changed anything.
        """
        try:
            payload = _json_body(self)
            prompt = payload.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise WebUIError("'prompt' is required")
            model = payload.get("model")
            if model is not None and (not isinstance(model, str) or not model.strip()):
                raise WebUIError("'model' must be a non-empty string or null")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        agent: AgentSession = self.server.agent  # type: ignore[attr-defined]
        try:
            agent.send(prompt, model=model)
        except OSError as exc:
            # A spawn that never happened (`claude` not on PATH, a dead
            # PROOFCUT_AGENT_BIN) puts nothing on `/api/events` to clear the
            # composer — so the failure has to come back on this request,
            # as JSON, not as a connection reset with a server-side traceback.
            self._fail(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"could not start the agent process: {exc}",
            )
            return
        self._send_json({"accepted": True}, HTTPStatus.ACCEPTED)

    def _handle_agent_stop(self) -> None:
        try:
            _json_body(self)
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        agent: AgentSession = self.server.agent  # type: ignore[attr-defined]
        agent.stop()
        self._send_json({"stopped": True})

    def _handle_agent_new_task(self) -> None:
        """`POST /api/agent/new-task {}` — kill the live subprocess so the
        next prompt starts a fresh conversation (docs/plans/DAYDREAM.md § Agent panel,
        item 4: "needs only the affordance" — `AgentSession.reset()` and
        `send()`'s existing lazy respawn already do the rest).

        Same shape as `_handle_agent_stop`: a plain 200 acknowledgement, no
        `self.server`-free `_POST_ROUTES` entry because this needs the live
        `AgentSession` off `self.server`.
        """
        try:
            _json_body(self)
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        agent: AgentSession = self.server.agent  # type: ignore[attr-defined]
        agent.reset()
        self._send_json({"reset": True})

    def _handle_render_start(self) -> None:
        """`POST /api/render {"preset": optional, "resolution": optional,
        "burn": optional}` — 202, work happens on the stream.

        Like `/api/agent`, the reply only acknowledges; progress arrives as
        `render` events on `/api/events` — `"running"`, one `"stage"` event
        per pipeline stage attempted (`export`/`burn`/`check_frames`/
        `verify`), then `"done"`/`"error"`/`"cancelled"`. All three fields
        are threaded straight through to the render pipeline — this handler
        only checks their *shape* (a string; a 2-element list of ints; a
        bool or null), never whether the combination is valid. An invalid
        combination (an unknown preset name, `resolution` on a layered
        project, `preset`/`resolution` with an NLE `export_format` — this
        endpoint never asks for one, so that specific combination cannot
        happen here) still raises inside the pipeline, on the render worker
        thread, and surfaces as the existing `error` render event — the
        window draws and plays, it does not decide.

        `burn`: `null` applies the project's own default (on when a caption
        style is configured, off otherwise); `true`/`false` overrides it.
        """
        try:
            payload = _json_body(self)
            preset = payload.get("preset")
            if preset is not None and not isinstance(preset, str):
                raise WebUIError("'preset' must be a string")
            resolution = payload.get("resolution")
            if resolution is not None:
                if (
                    not isinstance(resolution, list)
                    or len(resolution) != 2
                    or not all(isinstance(n, int) and not isinstance(n, bool) for n in resolution)
                ):
                    raise WebUIError("'resolution' must be a two-element list of integers")
                resolution = (resolution[0], resolution[1])
            burn = payload.get("burn")
            if burn is not None and not isinstance(burn, bool):
                raise WebUIError("'burn' must be true, false, or null")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: RenderJob = self.server.render_job  # type: ignore[attr-defined]
        try:
            job_id = job.start(preset, resolution, burn)
        except RenderBusyError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _handle_render_stop(self) -> None:
        """`POST /api/render/stop {}` — cancel; the partial output is deleted.

        A no-op 200 when nothing is running, the same shape `/api/agent/stop`
        already answers with — the client does not have to know whether a
        render was in flight to ask it to stop.
        """
        try:
            _json_body(self)
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: RenderJob = self.server.render_job  # type: ignore[attr-defined]
        job.stop()
        self._send_json({"stopped": True})

    def _handle_proxy_start(self) -> None:
        """`POST /api/proxy {"clip_id": ..., "force": optional}` — 202.

        Like `/api/render`, the reply only acknowledges; `running` → `done`/
        `error` arrive as `proxy` events on `/api/events`. The transcode is a
        job rather than a request because it is minutes of ffmpeg on a long
        clip, and a request that long is a dead window.

        This handler checks shape only. Whether the clip is *worth* proxying —
        already playable, or streamless and so unfixable — is
        `ops.proxy_transcode`'s judgement, reached through `ProxyJob.start`
        before the slot is claimed, so it still surfaces as a 400 here rather
        than as an event nobody asked for. The window draws and plays, it does
        not decide (CLAUDE.md).
        """
        try:
            payload = _json_body(self)
            clip_id = payload.get("clip_id")
            if not isinstance(clip_id, str) or not clip_id:
                raise WebUIError("'clip_id' is required")
            force = payload.get("force", False)
            if not isinstance(force, bool):
                raise WebUIError("'force' must be a boolean")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: ProxyJob = self.server.proxy_job  # type: ignore[attr-defined]
        try:
            job_id = job.start(clip_id, force=force)
        except ProxyBusyError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _handle_reframe_sheet_start(self) -> None:
        """`POST /api/reframe/sheet {"moments": [...] | null, "extremes": bool
        | null, "out": str | null}` — 202, work happens on the stream.

        Shape check only, `_handle_proxy_start`'s pattern. `frame.js` never
        sends `extremes: true` in this step, but the endpoint honours it if
        a body ever does — `extremes` is only cost/availability-gated, not
        the "never write" rail `apply` is on the detect endpoint.
        """
        try:
            payload = _json_body(self)
            moments = payload.get("moments")
            if moments is not None:
                if not isinstance(moments, list) or not all(
                    isinstance(n, int | float) and not isinstance(n, bool) for n in moments
                ):
                    raise WebUIError("'moments' must be a list of numbers")
                moments = [float(n) for n in moments]
            extremes = payload.get("extremes", False)
            if not isinstance(extremes, bool):
                raise WebUIError("'extremes' must be a boolean")
            out = payload.get("out")
            if out is not None and not isinstance(out, str):
                raise WebUIError("'out' must be a string")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: ReframeSheetJob = self.server.reframe_sheet_job  # type: ignore[attr-defined]
        try:
            job_id = job.start(moments=moments, extremes=extremes, out=out)
        except ReframeSheetBusyError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _handle_reframe_detect_start(self) -> None:
        """`POST /api/reframe/detect {"clip_id": str | null, "threshold":
        number | null, "frames": int | null, "split": bool | null}` — 202.

        **`apply` is not read from the body at all.** If the body includes
        `"apply": true` this refuses with a 400 before the job even starts —
        the frontend has no control that could set it, and this refuses even
        a hand-crafted request that tries, the second of the two independent
        places (`ReframeDetectJob.start` hard-codes `apply=False`) enforcing
        "reframe-detect never writes; approve a proposal through
        /api/reframe instead."
        """
        try:
            payload = _json_body(self)
            if "apply" in payload:
                raise WebUIError(
                    "'apply' is not accepted here — reframe-detect never writes; "
                    "approve a proposal through /api/reframe instead"
                )
            clip_id = payload.get("clip_id")
            if clip_id is not None and not isinstance(clip_id, str):
                raise WebUIError("'clip_id' must be a string")
            threshold = payload.get("threshold")
            if threshold is not None and not isinstance(threshold, int | float):
                raise WebUIError("'threshold' must be a number")
            frames = payload.get("frames")
            if frames is not None and (
                not isinstance(frames, int) or isinstance(frames, bool)
            ):
                raise WebUIError("'frames' must be an integer")
            split = payload.get("split", True)
            if not isinstance(split, bool):
                raise WebUIError("'split' must be a boolean")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: ReframeDetectJob = self.server.reframe_detect_job  # type: ignore[attr-defined]
        try:
            job_id = job.start(
                clip_id=clip_id,
                threshold=None if threshold is None else float(threshold),
                frames=frames,
                split=split,
            )
        except ReframeDetectBusyError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _handle_import_start(self) -> None:
        """`POST /api/import {"source": str, "clip_id": str | null, "copy":
        bool | null, "mix": bool | null, "audio_stream": int | null}` — 202,
        work happens on the stream.

        Like `/api/proxy`, the reply only acknowledges; `running` → `done`/
        `error` arrive as `import` events on `/api/events`. `source` is a
        server-side absolute path the client types into the window — not an
        upload, not a directory browser — because import has to be able to
        reach footage on the NAS; the guard here is the same loopback + Host
        + `application/json` every mutation on this server already carries,
        not a new one.

        This handler checks shape only. Whether `source` names a real,
        readable file is `ImportJob.start`'s judgement, resolved before the
        slot is claimed so it still surfaces as a 400 here rather than an
        event nobody asked for; whether `clip_id` collides with one already
        registered is `ops.import_media`'s, reached on the job's own worker
        thread and reported as an `import` error event. The window draws and
        plays, it does not decide (CLAUDE.md).
        """
        try:
            payload = _json_body(self)
            source = payload.get("source")
            if not isinstance(source, str) or not source:
                raise WebUIError("'source' is required")
            clip_id = payload.get("clip_id")
            if clip_id is not None and (not isinstance(clip_id, str) or not clip_id):
                raise WebUIError("'clip_id' must be a non-empty string")
            copy = payload.get("copy")
            if copy is None:
                copy = False
            elif not isinstance(copy, bool):
                raise WebUIError("'copy' must be a boolean")
            mix = payload.get("mix")
            if mix is None:
                mix = False
            elif not isinstance(mix, bool):
                raise WebUIError("'mix' must be a boolean")
            audio_stream = payload.get("audio_stream")
            # `isinstance(True, int)` is True in Python, and a stray `true`
            # here would read as stream 1 — the second mic — silently.
            if audio_stream is not None and (
                isinstance(audio_stream, bool) or not isinstance(audio_stream, int)
            ):
                raise WebUIError("'audio_stream' must be an integer")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: ImportJob = self.server.import_job  # type: ignore[attr-defined]
        try:
            job_id = job.start(
                source, clip_id=clip_id, copy=copy, mix=mix, audio_stream=audio_stream
            )
        except ImportBusyError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _handle_transcribe_start(self) -> None:
        """`POST /api/transcribe {"clip_id": str, "model": str | null,
        "language": str | null}` — 202, work happens on the stream.

        Like `/api/proxy`, the reply only acknowledges; `running` → `done`/
        `error` arrive as `transcribe` events on `/api/events` — and per
        `TranscribeJob`'s docstring, that `done` event is the *only* reload
        signal a completed transcription fires. A panel watching
        `project-changed` instead will never reload.

        Shape only, here: `model`/`language` absent (or `null`) leave
        `ops.transcribe`'s own defaults in force — `asr.DEFAULT_MODEL` stays
        defined in exactly one place because this handler never repeats it.
        """
        try:
            payload = _json_body(self)
            clip_id = payload.get("clip_id")
            if not isinstance(clip_id, str) or not clip_id:
                raise WebUIError("'clip_id' is required")
            model = payload.get("model")
            if model is not None and not isinstance(model, str):
                raise WebUIError("'model' must be a string")
            language = payload.get("language")
            if language is not None and not isinstance(language, str):
                raise WebUIError("'language' must be a string")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: TranscribeJob = self.server.transcribe_job  # type: ignore[attr-defined]
        try:
            job_id = job.start(clip_id, model=model, language=language)
        except TranscribeBusyError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _handle_seed_start(self) -> None:
        """`POST /api/seed {"clip_id": str, "remove_silences": bool | null}`
        — 202, work happens on the stream.

        `/api/transcribe`'s exact shape, and a job for the same reason: the
        silence pass runs auto-editor over the whole recording. Shape only
        here; an unknown `clip_id` is `SeedJob.start`'s judgement (resolved
        before the slot is claimed, so it is a 400 rather than an event) and
        auto-editor's own refusals arrive as `seed` error events.

        `remove_silences` defaults to **true**, matching `ops.seed_timeline`
        and `proofcut seed` rather than picking a second default here — one
        place spells it out or the two drift.
        """
        try:
            payload = _json_body(self)
            clip_id = payload.get("clip_id")
            if not isinstance(clip_id, str) or not clip_id:
                raise WebUIError("'clip_id' is required")
            remove_silences = payload.get("remove_silences")
            if remove_silences is None:
                remove_silences = True
            elif not isinstance(remove_silences, bool):
                raise WebUIError("'remove_silences' must be a boolean")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        job: SeedJob = self.server.seed_job  # type: ignore[attr-defined]
        try:
            job_id = job.start(clip_id, remove_silences=remove_silences)
        except SeedBusyError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except EXPECTED as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _handle_create(self) -> None:
        """`POST /api/create {"name": "..."}` — make a project under `--root`.

        **Picker mode only.** Under `-C` the project already exists and there
        is nothing to create, so this route is simply not reachable there
        (`do_POST` dispatches it inside the `root_dir is not None` branch) —
        absent rather than refused, the same way `/api/open` is absent on a
        `-C` server.

        A *name*, never a path. That is the whole containment: the new
        directory is `root_dir / name` and `name` may not contain a
        separator, a `..`, a leading dot, or a NUL, so there is no traversal
        to resolve away. The resolve-and-compare check `_handle_open` uses
        runs anyway, belt-and-braces, because a name check and a path check
        answer different questions and this repo has been caught by exactly
        one of the two before.

        **A symlink is refused before anything is written.** `Path.is_dir()`
        follows symlinks (CLAUDE.md § The multi-project picker), so a
        pre-existing symlink at the target name would pass an `is_dir()`
        test and `Project.create` would write a manifest through it, outside
        the root. `is_symlink()` is checked first and on its own.

        Creating does not open: the reply carries the new project's path and
        the client posts `/api/open` with it, so there is exactly one place
        that binds this process to a project.
        """
        try:
            payload = _json_body(self)
            raw = payload.get("name")
            if not isinstance(raw, str) or not raw.strip():
                raise WebUIError("'name' is required")
            name = raw.strip()
            if name != Path(name).name or name in (".", ".."):
                raise WebUIError(
                    f"{name!r} must be a plain directory name, not a path — "
                    "a project is created directly under the scanned root"
                )
            if name.startswith(".") or "\x00" in name:
                raise WebUIError(f"{name!r} is not a usable directory name")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return

        assert self.root_dir is not None  # only dispatched in picker mode
        root_dir = self.root_dir
        target = root_dir / name
        if target.is_symlink():
            self._fail(
                HTTPStatus.FORBIDDEN,
                f"{target} is a symlink; a project is created as a real directory "
                "under the scanned root, never through a link out of it",
            )
            return
        # The name check above already makes traversal impossible; this is the
        # second, independent answer — `_handle_open`'s own comparison, run
        # against a path nobody has written to yet.
        resolved = target.resolve()
        if root_dir not in resolved.parents:
            self._fail(
                HTTPStatus.FORBIDDEN,
                f"this server was started with --root {root_dir} and {name!r} resolves "
                f"outside it ({resolved}); pass a plain name",
            )
            return
        try:
            created = ops.init(resolved, name=name)
        except EXPECTED as exc:
            # An existing project at that name is the ordinary mistake —
            # `Project.create` refuses rather than overwriting, and that
            # refusal is the message to show.
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except OSError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, f"could not create {resolved}: {exc}")
            return
        self._send_json({"created": True, "path": created["project"], "name": name})

    def _handle_open(self) -> None:
        """`POST /api/open {"path": "..."}` — the picker's one mutation.

        Binds this process to one project, permanently: the same work
        `make_server` already does for `-C` at process start
        (`_bind_singletons`), just deferred to the moment a person picks one
        instead of decided in advance. There is no unbind and no switch —
        once this returns 200, `project_root` is set on the handler class
        and `self.server.bus`/`agent`/`render_job`/`proxy_job` exist, and
        every request after this one (from any tab, any connection) is an
        ordinary single-project request against that project for the rest
        of the process's life. Wanting a second project open at the same
        time still means a second process, exactly as `-C` always required
        — that is what keeps two projects from ever sharing one
        `AgentSession` or `RenderJob` (CLAUDE.md: cross-wiring those is a
        data-corruption bug, not a UI bug).

        The path is confined to `root_dir` the same way `server.py`'s
        `_confine` confines an MCP tool's project selector — resolved, and
        refused if it lands outside the scanned root rather than followed —
        because a `--root` a user passed must not become a way to open a
        directory it never scanned. And it goes through `Project.open`
        itself, so an old-schema or unreadable project (already visible to
        `/api/projects` as `needs_migration`/`unreadable`) is refused here
        exactly as it always refuses `-C`, not skipped and not migrated.
        """
        try:
            payload = _json_body(self)
            raw = payload.get("path")
            if not isinstance(raw, str) or not raw:
                raise WebUIError("'path' is required")
        except WebUIError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return

        assert self.root_dir is not None  # only reachable in picker mode
        root_dir = self.root_dir
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root_dir / candidate
        resolved = candidate.resolve()
        if resolved != root_dir and root_dir not in resolved.parents:
            self._fail(
                HTTPStatus.FORBIDDEN,
                f"this server was started with --root {root_dir} and {raw!r} resolves "
                f"outside it ({resolved}); pass a path at or under the scanned root",
            )
            return

        try:
            project = Project.open(resolved)
        except ProjectError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return

        lock: threading.Lock = self.server.open_lock  # type: ignore[attr-defined]
        with lock:
            bound = getattr(self.server, "bound_root", None)
            if bound is not None:
                if bound != project.root:
                    self._fail(HTTPStatus.CONFLICT, f"this server already opened {bound}")
                    return
                # Idempotent: a second click on the same project (or a second
                # tab that raced the first) is not an error.
                self._send_json({"opened": True, "root": str(project.root)})
                return
            type(self).project_root = project.root
            _bind_singletons(self.server, project.root)  # type: ignore[arg-type]
            self.server.bound_root = project.root  # type: ignore[attr-defined]
        self._send_json({"opened": True, "root": str(project.root)})


# -- the mutating endpoints ----------------------------------------------
#
# Each one is a shape check and a call. No endpoint computes an edit; the
# payload the browser draws is the op's own return value, which is what keeps
# the view honest about `plan` in particular — the panel shows the numbers the
# real call produced, because it *is* the real call with the write skipped.


def _ranges_arg(payload: dict[str, Any], key: str) -> list[list[int]]:
    raw = payload.get(key)
    if not isinstance(raw, list) or not raw:
        raise WebUIError(f"{key!r} must be a non-empty list of [first, last] word ranges")
    out = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 2:
            raise WebUIError(f"{item!r} is not a [first, last] word range")
        try:
            out.append([int(item[0]), int(item[1])])
        except (TypeError, ValueError):
            raise WebUIError(f"{item!r} is not a [first, last] word range") from None
    return out


def _spans_arg(payload: dict[str, Any]) -> list[list[float]]:
    raw = payload.get("spans")
    if not isinstance(raw, list) or not raw:
        raise WebUIError("'spans' must be a non-empty list of [start, end] timeline spans")
    out = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 2:
            raise WebUIError(f"{item!r} is not a [start, end] span")
        try:
            out.append([float(item[0]), float(item[1])])
        except (TypeError, ValueError):
            raise WebUIError(f"{item!r} is not a [start, end] span") from None
    return out


def _clip_arg(payload: dict[str, Any]) -> str:
    clip_id = payload.get("clip_id")
    if not isinstance(clip_id, str) or not clip_id:
        raise WebUIError("'clip_id' is required")
    return clip_id


def _float_arg(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(payload.get(key, default))
    except (TypeError, ValueError):
        raise WebUIError(f"{key!r} must be a number") from None


def _cut_words(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode", "cut")
    if mode not in ("cut", "keep"):
        raise WebUIError("'mode' must be 'cut' or 'keep'")
    ranges = _ranges_arg(payload, "ranges")
    return ops.cut_by_transcript(
        root,
        _clip_arg(payload),
        cut=ranges if mode == "cut" else None,
        keep=ranges if mode == "keep" else None,
        pad=_float_arg(payload, "pad"),
        confirm_suspect=bool(payload.get("confirm_suspect")),
        through_pause=bool(payload.get("through_pause")),
        plan=bool(payload.get("plan")),
    )


def _cut_at(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    return ops.cut_by_time(
        root,
        spans=_spans_arg(payload),
        pad=_float_arg(payload, "pad"),
        confirm_suspect=bool(payload.get("confirm_suspect")),
        plan=bool(payload.get("plan")),
    )


def _restore(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    ranges = _ranges_arg(payload, "ranges")
    return ops.restore(
        root,
        _clip_arg(payload),
        ranges,
        pad=_float_arg(payload, "pad"),
        plan=bool(payload.get("plan")),
    )


def _undo(root: str, _payload: dict[str, Any]) -> dict[str, Any]:
    return ops.undo(root)


def _cue_add(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/cue` — the timeline drag gesture's landing point.

    A fourth caller into `ops.cue_add`, alongside the CLI and MCP tool,
    matching every other route in this table (CLAUDE.md: the web UI draws
    and plays, it never decides — every mutation posts to the same `ops`
    function the CLI and MCP call).
    """
    asset = payload.get("asset")
    if not isinstance(asset, str) or not asset:
        raise WebUIError("'asset' is required")
    word_index = payload.get("word_index")
    try:
        word_index = int(word_index)
    except (TypeError, ValueError):
        raise WebUIError("'word_index' must be an integer") from None
    src_start = payload.get("src_start")
    return ops.cue_add(
        root,
        _clip_arg(payload),
        word_index,
        asset,
        src_start=None if src_start is None else _float_arg(payload, "src_start"),
    )


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    """`None` when the key is absent, an int when it is there.

    Distinct from `_float_arg`'s defaulting on purpose: every field of
    `ops.music` is a partial update, where absent means "leave this one
    alone" and a default would silently rewrite it.
    """
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise WebUIError(f"{key!r} must be an integer") from None


def _music(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/music` — the A2 lane's own mutation.

    A fourth caller into `ops.music`, alongside the CLI and MCP tool, and
    what makes the A2 lane a thing the window can *set* rather than only
    draw: the lane shipped reading `timeline_view`'s `music` projection with
    the CLI owning every way to create, move, fade or clear the bed under it
    (HISTORY.md § The A2 music lane, built; § The A2 fades and the lane,
    drawn).

    Nothing here decides. The merge rule (either field alone updates its own
    once a bed exists), the card refusal, the `word_index_end` ordering test
    and the registered-asset check are all `ops.music`'s, and its refusals
    surface as this route's 400 — the same "don't re-implement the op's own
    validation" discipline `_cue_add` and `_reframe` already follow. A bed
    that no longer *resolves* is not this route's business either: that
    arrives on the next `/api/view` as `music_error` for the lane to draw.

    Every field is optional because `ops.music` is a partial update — a
    panel changing only the fades sends only the fades — so `_clip_arg` is
    deliberately not used here. `plan` is not read from the payload at all,
    `_cue_add`'s precedent: the panel echoes the words it resolved against
    from the same `state.words` the drag resolved against, and the server's
    own echo arrives on the bus with the applied result. And `clip_id` is
    the *addressing* transcript
    (the cue's own clip, which is what a word index is an index into), never
    the music asset: `asset` is the footage that plays, the same split a
    shot dict makes between `clip_id` and `asset` (CLAUDE.md).
    """
    asset = payload.get("asset")
    if asset is not None and (not isinstance(asset, str) or not asset):
        raise WebUIError("'asset' must be a non-empty string")
    clip_id = payload.get("clip_id")
    if clip_id is not None and (not isinstance(clip_id, str) or not clip_id):
        raise WebUIError("'clip_id' must be a non-empty string")
    return ops.music(
        root,
        asset=asset,
        clip_id=clip_id,
        word_index_start=_optional_int(payload, "word_index_start"),
        word_index_end=_optional_int(payload, "word_index_end"),
        fade_in=None if payload.get("fade_in") is None else _float_arg(payload, "fade_in"),
        fade_out=None if payload.get("fade_out") is None else _float_arg(payload, "fade_out"),
        clear_end=bool(payload.get("clear_end")),
        reset=bool(payload.get("reset")),
    )


def _clip_role(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/clip-role` — the assets pane's role toggle.

    A fourth caller into `ops.clip_role`, alongside the CLI and MCP tool,
    matching every other route in this table (CLAUDE.md: the web UI draws
    and plays, it never decides).
    """
    clip_id = payload.get("clip_id")
    if not isinstance(clip_id, str) or not clip_id:
        raise WebUIError("'clip_id' is required")
    role = payload.get("role")
    if role is not None and not isinstance(role, str):
        raise WebUIError("'role' must be a string")
    return ops.clip_role(root, clip_id, role, reset=bool(payload.get("reset")))


def _attach_transcript(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/transcript/attach {"clip_id": str, "path": str}` —
    `/api/transcribe`'s non-ASR sibling.

    A plain `_POST_ROUTES` mutation rather than a job, unlike `/api/import`
    and `/api/transcribe`: `ops.attach_transcript` parses a transcript file
    that already has word timings, which is instant, so there is no
    long-running work to report progress on (`TranscribeJob`'s docstring
    spells out why *that* one is a job). A fifth caller into
    `ops.attach_transcript`, alongside the CLI and MCP tool, matching every
    other route in this table (CLAUDE.md: the web UI draws and plays, it
    never decides).

    `path` is a server-side path the client types into the window, the same
    "reach what's on the NAS, not an upload" shape `/api/import`'s `source`
    is — a transcript made outside proofcut (the Scream VO's, transcribed
    before lucid existed) lives on disk, not in the browser.
    """
    clip_id = _clip_arg(payload)
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        raise WebUIError("'path' is required")
    return ops.attach_transcript(root, clip_id, path)


def _agent_thumb(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/agent/thumbs` — append one rating to `Project.thumbs_path`.

    docs/plans/DAYDREAM.md § Agent panel, item 2: "useful only if something reads it;
    build the log, defer any use." So this is telemetry, not an `ops`
    mutation — no CLI subcommand, matching the existing precedent that
    `/api/agent`, `/api/agent/stop`, `/api/render` and `/api/render/stop`
    also have no CLI equivalents (CLAUDE.md's parity rule is scoped to MCP
    tools, and this route touches no MCP tool either).

    `session_id` + `turn_id` are the pair that let a later reader tell WHICH
    turn was rated: `session_id` is constant across every turn of one
    subprocess (the stream-json `result` event's own field, verified live
    against the installed `claude` binary), `turn_id` — that event's `uuid`
    — is unique per turn. `prompt` is optional, echoed for convenience; it is
    not part of the identifying pair.

    Deliberately does not touch `project.otio`: `_revision()` only stats
    `project.timeline_path` and counts `project.snapshots()`, and this writes
    to neither, so no `project-changed` event fires from this call.
    """
    rating = payload.get("rating")
    if rating not in ("up", "down"):
        raise WebUIError("'rating' must be 'up' or 'down'")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise WebUIError("'session_id' is required")
    turn_id = payload.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        raise WebUIError("'turn_id' is required")
    prompt = payload.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise WebUIError("'prompt' must be a string")

    project = Project.open(root)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "turn_id": turn_id,
        "rating": rating,
        "prompt": prompt,
    }
    with project.thumbs_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return {"recorded": True, **record}


def _reframe(root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/reframe` — the Re-frame panel's landing point.

    A fifth caller into `ops.reframe`, alongside the CLI, the MCP tool, and
    (read-only) `timeline_view`/`reframe_sheet`, matching every other route
    in this table (CLAUDE.md: the web UI draws and plays, it never decides).

    `clip_id` is always required on this route — the read-only "report on
    every clip" use of `ops.reframe(clip_id=None)` has no caller from the
    Frame view; `frame.js` always targets one row's asset. `pane` without
    `rect` is not re-checked here — `ops.reframe` refuses that combination
    itself, and the refusal surfaces as this route's 400, the same
    "don't re-implement the op's own validation" discipline `_cue_add`
    already follows for its own args. `interp`, `reset` and `plan` are not
    read from the payload at all in this step (Studio Step 03 contract § B) —
    nudge and direct-rect-entry produce only `rect`/`pane`/`src_start`.
    """
    clip_id = _clip_arg(payload)
    rect = payload.get("rect")
    if rect is not None and not isinstance(rect, str):
        raise WebUIError("'rect' must be a string")
    pane = payload.get("pane")
    if pane is not None and not isinstance(pane, str):
        raise WebUIError("'pane' must be a string")
    src_start = payload.get("src_start")
    return ops.reframe(
        root,
        clip_id,
        rect=rect,
        pane=pane,
        src_start=None if src_start is None else _float_arg(payload, "src_start"),
        interp=False,
        reset=False,
        plan=False,
    )


#: `plan` is a field on the request rather than a separate endpoint, because
#: it is one flag on one op — giving preview its own URL would invite the two
#: paths to drift, which is the whole thing `plan=True` exists to prevent.
#: `/api/agent`, `/api/agent/stop`, `/api/agent/new-task`, `/api/render`,
#: `/api/render/stop`, `/api/proxy`, `/api/reframe/sheet`,
#: `/api/reframe/detect`, `/api/import` and `/api/transcribe` are all handled
#: directly in `do_POST` instead of living here, because each needs
#: `self.server` (the bus, and its own job or session object) rather than
#: just the project root a plain `ops` call takes. `/api/agent/thumbs` is the
#: one `/api/agent*` route that lives here rather than in `do_POST`: it only
#: ever needs the project root, the same as every other route in this table.
#: `/api/transcript/attach` is `/api/transcribe`'s non-ASR sibling and lives
#: here rather than beside it, on the same reasoning: parsing an
#: already-timed transcript file is instant, so it needs no job, no
#: `self.server`, and no progress event of its own.
_POST_ROUTES: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "/api/cut": _cut_words,
    "/api/cut-at": _cut_at,
    "/api/restore": _restore,
    "/api/undo": _undo,
    "/api/cue": _cue_add,
    "/api/music": _music,
    "/api/clip-role": _clip_role,
    "/api/agent/thumbs": _agent_thumb,
    "/api/reframe": _reframe,
    "/api/transcript/attach": _attach_transcript,
    "/api/session": _session_set,
}


# -- lifecycle -----------------------------------------------------------


def _bind_singletons(server: ThreadingHTTPServer, project_root: Path) -> None:
    """One bus, one agent session, and one job slot each for render, proxy,
    reframe-sheet, reframe-detect, import, transcribe and seed — the
    per-project state a `Handler` reaches through `self.server`.

    Called exactly once per server: at construction for a plain `-C` server
    (`make_server`), or once from `Handler._handle_open` on a picker
    server's first successful `POST /api/open`. Never both, and never twice
    — a picker server starts with none of these attributes set at all
    (`make_picker_server`), so a route reached before `/api/open` fails
    loudly (`AttributeError` in tests, refused by `do_POST`/`_route` in
    production) rather than reading a stale project's job.

    This is the whole answer to docs/plans/DAYDREAM.md § Multi-project's "a second
    project would need a second everything here": it does not get one.
    `--root` lets a process defer *which* project these belong to, but only
    ever binds one — a second project open at once still means a second
    process. Every job here holds its *own* slot for the reason render and
    proxy always have: different work on different files, and sharing a slot
    would make one job refuse while an unrelated one ran — an import of a
    fresh clip must not 409 because someone is mid-transcription of a clip
    already on the timeline, and a render must not wait behind either.
    """
    server.bus = EventBus()  # type: ignore[attr-defined]
    server.agent = AgentSession(project_root, server.bus)  # type: ignore[attr-defined]
    server.render_job = RenderJob(project_root, server.bus)  # type: ignore[attr-defined]
    server.proxy_job = ProxyJob(project_root, server.bus)  # type: ignore[attr-defined]
    server.reframe_sheet_job = ReframeSheetJob(project_root, server.bus)  # type: ignore[attr-defined]
    server.reframe_detect_job = ReframeDetectJob(project_root, server.bus)  # type: ignore[attr-defined]
    server.import_job = ImportJob(project_root, server.bus)  # type: ignore[attr-defined]
    server.transcribe_job = TranscribeJob(project_root, server.bus)  # type: ignore[attr-defined]
    server.seed_job = SeedJob(project_root, server.bus)  # type: ignore[attr-defined]


def make_server(
    path: Path | str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    verbose: bool = False,
    token: str | None = None,
    allowed_hosts: frozenset[str] | Sequence[str] = _LOOPBACK_NAMES,
) -> ThreadingHTTPServer:
    """Build a server for one project. Opens it first, so a bad path fails now.

    Threading matters for two reasons now, not one: media streams for as long
    as playback lasts, and `/api/events` holds a connection open for as long
    as the tab is — either one would leave every other request queued behind
    it on a single-threaded server.
    """
    project = Project.open(path)

    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "project_root": project.root,
            "verbose": verbose,
            "token": token,
            "allowed_hosts": frozenset(allowed_hosts),
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    _bind_singletons(server, project.root)
    return server


def make_picker_server(
    root: Path | str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    verbose: bool = False,
    token: str | None = None,
    allowed_hosts: frozenset[str] | Sequence[str] = _LOOPBACK_NAMES,
) -> ThreadingHTTPServer:
    """Build a server over a `--root` scan (docs/plans/DAYDREAM.md § Multi-project).

    Opens nothing: a scan can find a project at an old schema or with a
    broken manifest (`scan_projects`), and `Project.open` must never migrate
    one on a read (CLAUDE.md), so nothing here may call it before a person
    picks. The server starts with `bound_root = None` and no `bus`/`agent`/
    `render_job`/`proxy_job` at all; `Handler._route_picker` and
    `Handler._handle_open` are the only routes reachable until `POST
    /api/open` succeeds, at which point `_bind_singletons` runs (the same
    call `make_server` makes at construction, just later) and this server is
    an ordinary single-project server for the rest of its life — see
    `_bind_singletons`'s docstring for why that is the whole design.
    """
    root_dir = Path(root).expanduser().resolve()
    handler = type(
        "RootHandler",
        (Handler,),
        {
            "root_dir": root_dir,
            "verbose": verbose,
            "token": token,
            "allowed_hosts": frozenset(allowed_hosts),
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.bound_root = None  # type: ignore[attr-defined]
    #: Guards the check-then-bind in `Handler._handle_open` — two requests
    #: racing to open two *different* projects on the same fresh server must
    #: not both win, or the second `_bind_singletons` call would silently
    #: replace the first project's agent/render/proxy jobs out from under
    #: whoever was about to use them.
    server.open_lock = threading.Lock()  # type: ignore[attr-defined]
    return server


#: Printed under the URL whenever a token is in force. The URL *is* the
#: credential, so say so once rather than let it read as a cache-buster.
_TOKEN_NOTE = (
    "  ^ that ?t= is the access token — the whole URL is the credential. "
    "Anyone on the tailnet who has it can edit this project."
)


def _client_url(
    host: str, port: int, allow_remote_hosts: Sequence[str] | None, token: str | None
) -> str:
    """The URL to hand a person, which is not always the bind address.

    A wildcard bind has no client-facing identity of its own (see
    `_WILDCARD_HOSTS`), so the first name the operator said clients would
    present is the honest thing to print — printing `http://0.0.0.0:8710/`
    hands out an address nothing can dial.
    """
    name = host
    if name.lower() in _WILDCARD_HOSTS:
        name = next(iter(allow_remote_hosts or ()), "localhost").rstrip(".")
    if ":" in name and not name.startswith("["):
        name = f"[{name}]"
    url = f"http://{name}:{port}/"
    return f"{url}?t={token}" if token else url


def serve(
    path: Path | str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    verbose: bool = False,
    open_browser: bool = False,
    allow_remote: bool = False,
    allow_remote_hosts: Sequence[str] | None = None,
    token: str | None = None,
) -> None:
    """Run the UI until interrupted. `port=0` picks a free one.

    `allow_remote`/`allow_remote_hosts`/`token` go straight to
    `remote_policy`, which refuses before a socket is bound — see this
    module's docstring for what the opt-in trades away and what replaces it.
    """
    token, allowed = remote_policy(
        host=host,
        allow_remote=allow_remote,
        allow_remote_hosts=allow_remote_hosts,
        token=token,
    )
    server = make_server(
        path, host=host, port=port, verbose=verbose, token=token, allowed_hosts=allowed
    )
    bound = server.server_address[1]
    url = _client_url(host, bound, allow_remote_hosts, token)
    # Flushed: this is the one line the user needs, and a piped stdout would
    # otherwise hold it in the buffer until the server exits.
    print(f"proofcut web: {url}  (project: {Project.open(path).root})", flush=True)
    if token is not None:
        print(_TOKEN_NOTE, flush=True)
    print("Ctrl-C to stop.", flush=True)

    if open_browser:
        import webbrowser

        threading.Timer(0.3, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.shutdown()
        server.server_close()
        server.agent.close()  # type: ignore[attr-defined]


def serve_root(
    root: Path | str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    verbose: bool = False,
    open_browser: bool = False,
    allow_remote: bool = False,
    allow_remote_hosts: Sequence[str] | None = None,
    token: str | None = None,
) -> None:
    """Run the picker until interrupted. `port=0` picks a free one.

    Once `POST /api/open` binds a project, this is functionally `serve`
    running on a server that happened to start life unbound — same loop,
    same shutdown. `open_browser` opens the picker, not a project: nothing
    is open yet at startup by construction.
    """
    token, allowed = remote_policy(
        host=host,
        allow_remote=allow_remote,
        allow_remote_hosts=allow_remote_hosts,
        token=token,
    )
    server = make_picker_server(
        root, host=host, port=port, verbose=verbose, token=token, allowed_hosts=allowed
    )
    bound = server.server_address[1]
    url = _client_url(host, bound, allow_remote_hosts, token)
    root_dir = server.RequestHandlerClass.root_dir  # type: ignore[attr-defined]
    print(f"proofcut web: {url}  (projects under: {root_dir})", flush=True)
    if token is not None:
        print(_TOKEN_NOTE, flush=True)
    print("Ctrl-C to stop.", flush=True)

    if open_browser:
        import webbrowser

        threading.Timer(0.3, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.shutdown()
        server.server_close()
        agent = getattr(server, "agent", None)
        if agent is not None:
            agent.close()


#: Checked in order by `_resolve_app_browser`; a chromium-family browser is
#: required because `--app=<url>` (a chromeless window, no tabs/toolbar) is
#: a Chromium flag with no Firefox/Safari equivalent — the whole reason
#: `proofcut open` prefers one over the default browser. Verified live on this
#: box: nothing here is on PATH (only reachable through the flatpak tier
#: below), but the tuple is what should be probed, portably.
_APP_BROWSER_BINS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "google-chrome-unstable",
    "chrome",
    "brave-browser",
    "brave",
    "vivaldi",
    "microsoft-edge",
    "microsoft-edge-stable",
)

#: Same chromium-family constraint, one flatpak app ID per vendor.
#: `com.google.Chrome` is confirmed installed on this box (flathub, system)
#: and is what actually fires `proofcut open` here today, since nothing above
#: is on PATH.
_APP_BROWSER_FLATPAKS = (
    "com.google.Chrome",
    "com.brave.Browser",
    "com.microsoft.Edge",
    "org.chromium.Chromium",
    "com.vivaldi.Vivaldi",
)

#: Env var naming an exact browser command to launch `proofcut open`'s window
#: with. Checked first and taken literally — no existence check — because an
#: operator who set it wrong would rather see the failure than have it
#: silently ignored.
PROOFCUT_BROWSER_ENV = "PROOFCUT_BROWSER"


def _app_browser_installs() -> list[Path]:
    """Where a chromium-family browser installs itself on macOS and Windows.

    Neither OS puts one on PATH, so `_APP_BROWSER_BINS` finds nothing there.
    Same vendor order as that tuple, so a box with Chrome and Edge both — every
    Windows box has Edge — opens the same browser it would on Linux. Leads from
    docs/plans/PORTABILITY.md step 2, unmeasured here.
    """
    if sys.platform == "darwin":
        return [
            Path(apps) / f"{name}.app" / "Contents" / "MacOS" / name
            for name in ("Chromium", "Google Chrome", "Brave Browser", "Vivaldi", "Microsoft Edge")
            for apps in ("/Applications", Path.home() / "Applications")
        ]
    if sys.platform == "win32":
        roots = [
            Path(value)
            for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
            if (value := os.environ.get(key))
        ]
        return [
            root / relative
            for relative in (
                Path("Chromium", "Application", "chrome.exe"),
                Path("Google", "Chrome", "Application", "chrome.exe"),
                Path("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                Path("Vivaldi", "Application", "vivaldi.exe"),
                Path("Microsoft", "Edge", "Application", "msedge.exe"),
            )
            for root in roots
        ]
    return []


def _resolve_app_browser() -> list[str] | None:
    """The command to launch a chromeless `--app=<url>` window with, or None.

    Checked in order, stopping at the first hit: `$PROOFCUT_BROWSER` (taken
    literally, unconditionally); a chromium-family binary on PATH
    (`_APP_BROWSER_BINS`); on macOS and Windows, a chromium-family browser in
    its install location (`_app_browser_installs`); a chromium-family flatpak,
    only if `flatpak` itself is on PATH (`_APP_BROWSER_FLATPAKS`, probed with
    `flatpak info`).

    Deliberately does not add a fourth tier for Playwright's cached
    Chromium under `~/.cache/ms-playwright/` — it exists on this box only as
    a test fixture, and `chrome-headless-shell` specifically cannot open a
    window at all, so launching either as the user's app surface would be
    silently wrong (Studio Step 04 contract § A).
    """
    override = os.environ.get(PROOFCUT_BROWSER_ENV)
    if override:
        return [override]

    for name in _APP_BROWSER_BINS:
        resolved = shutil.which(name)
        if resolved:
            return [resolved]

    for install in _app_browser_installs():
        if install.is_file():
            return [str(install)]

    if shutil.which("flatpak"):
        for app_id in _APP_BROWSER_FLATPAKS:
            try:
                probe = subprocess.run(
                    ["flatpak", "info", app_id], capture_output=True, check=False
                )
            except OSError:
                continue
            if probe.returncode == 0:
                return ["flatpak", "run", app_id]

    return None


def _launch_app(url: str) -> None:
    """Open `url` in a chromeless app window, falling back to a normal tab.

    The tab goes through `xdg-open` where there is one — Linux, unchanged —
    and the stdlib `webbrowser` module on macOS and Windows, which is what
    `xdg-open` is there (`open`, `os.startfile`).

    Best-effort only: a vanished binary, a permission error, or nothing
    found at all must never crash `proofcut open` — the URL was already
    printed by the caller before this runs, which is what satisfies "print
    the URL either way."
    """
    cmd = _resolve_app_browser()
    if cmd is not None:
        try:
            subprocess.Popen(
                [*cmd, f"--app={url}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass
        return

    xdg_open = shutil.which("xdg-open")
    if xdg_open:
        try:
            subprocess.Popen(
                [xdg_open, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass
        return
    # Not on Linux, where a box with no `xdg-open` is a box `webbrowser` would
    # answer with a console browser (w3m, lynx) seizing this terminal.
    if sys.platform.startswith("linux"):
        return

    import webbrowser

    try:
        webbrowser.open(url)
    except (webbrowser.Error, OSError):
        pass


def open_studio(path: Path | str | None = None, *, root: Path | str | None = None) -> None:
    """`proofcut open` — an ephemeral-port server plus a chromeless browser window.

    Studio Step 04 contract § A. Composes the existing `make_server`/
    `make_picker_server` rather than adding a mode to `serve`/`serve_root`,
    so neither function's signature or existing callers/tests are touched.
    Port is hardcoded `0` — always ephemeral, never configurable, per
    docs/plans/STUDIO.md's own wording; there is no `--host`/`--port` here the way
    `proofcut web` has them.

    `-C` (`path`) opens straight into that project; `--root` opens Home.
    Mutual refusal between the two lives in `cli._cmd_open`, matching where
    `_cmd_web` already refuses `-C`+`--root` together.
    """
    if root is not None:
        server = make_picker_server(root, host=DEFAULT_HOST, port=0)
        bound = server.server_address[1]
        url = f"http://{DEFAULT_HOST}:{bound}/"
        root_dir = server.RequestHandlerClass.root_dir  # type: ignore[attr-defined]
        print(f"proofcut open: {url}  (projects under: {root_dir})", flush=True)
    else:
        server = make_server(path if path is not None else ".", host=DEFAULT_HOST, port=0)
        bound = server.server_address[1]
        url = f"http://{DEFAULT_HOST}:{bound}/"
        print(f"proofcut open: {url}  (project: {Project.open(path if path is not None else '.').root})", flush=True)
    print("Ctrl-C to stop.", flush=True)

    # 0.3s, same delay `serve`'s own `open_browser` path already uses — the
    # socket is listening (bound above) before anything dials it, and the
    # print above already happened, so the URL is on screen even if
    # `_launch_app` finds nothing.
    threading.Timer(0.3, _launch_app, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.shutdown()
        server.server_close()
        agent = getattr(server, "agent", None)
        if agent is not None:
            agent.close()
