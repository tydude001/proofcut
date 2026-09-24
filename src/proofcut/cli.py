"""The `proofcut` CLI.

Every MCP tool is also reachable here, so the same operations can be scripted
or debugged without an agent in the loop. Both front ends call `proofcut.ops`;
neither holds logic of its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from proofcut import (
    __version__,
    asr,
    captions,
    describe,
    energy,
    ops,
    projectlock,
    reviewserver,
    tts,
    webui,
)
from proofcut.asr import ASRError
from proofcut.autoeditor import AutoEditorError
from proofcut.describe import DescribeError
from proofcut.energy import EnergyError
from proofcut.finish import FinishError
from proofcut.graphics import GraphicsError
from proofcut.media import MediaError
from proofcut.mlt import EASINGS, OVERLAY_MOTIONS, MLTError
from proofcut.pack import PackError
from proofcut.picture import PictureError
from proofcut.project import MANIFEST_NAME, TIMELINE_NAME, ProjectError, path_too_long
from proofcut.timeline import TimelineError
from proofcut.transcript import TranscriptError
from proofcut.verify import VerifyError


def _word_range(value: str) -> list[int]:
    """Parse a `FIRST:LAST` or `FIRST` word range from the command line."""
    first, _, last = value.partition(":")
    try:
        lo = int(first)
        hi = int(last) if last else lo
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a word range — use FIRST:LAST, e.g. 30:45"
        ) from None
    return [lo, hi]


def _parse_timecode(value: str) -> float:
    """Parse a colon-separated timecode, parts optional from the right.

    `"4.4"` -> 4.4, `"0:40.4"` -> 40.4, `"1:00:40.4"` -> 3640.4 — the same
    surface `vo_trim.parse_tc` uses.
    """
    parts = value.split(":")
    if not 1 <= len(parts) <= 3:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a timecode — use [[H:]M:]S, e.g. 0:40.4"
        )
    try:
        seconds = 0.0
        for part in (float(p) for p in parts):
            seconds = seconds * 60 + part
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a timecode — use [[H:]M:]S, e.g. 0:40.4"
        ) from None
    return seconds


def _time_span(value: str) -> list[float]:
    """Parse a render-time span: `START+DURATION` or `START-END`.

    `+DURATION` is primary — it's how a watch-note is phrased ("cut 0:40.4
    for 4.4s"), a start and a length rather than two timestamps someone has
    to compute. `-END` is kept for a note phrased as two timestamps, matching
    `vo_trim`'s own surface.
    """
    if "+" in value:
        start_str, _, duration_str = value.partition("+")
        start = _parse_timecode(start_str)
        try:
            duration = float(duration_str)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{value!r} is not START+DURATION — duration must be seconds"
            ) from None
        return [start, start + duration]
    if "-" in value:
        start_str, _, end_str = value.partition("-")
        return [_parse_timecode(start_str), _parse_timecode(end_str)]
    raise argparse.ArgumentTypeError(
        f"{value!r} is not a span — use START-END or START+DURATION, e.g. 0:40.4+4.4"
    )


def _resolution(value: str) -> tuple[int, int]:
    """Parse a `WIDTHxHEIGHT` export resolution, e.g. `1080x1920`."""
    width_str, _, height_str = value.partition("x")
    try:
        width, height = int(width_str), int(height_str)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a resolution — use WIDTHxHEIGHT, e.g. 1920x1080"
        ) from None
    return (width, height)


def _parse_hold(value: str) -> dict[str, Any]:
    """Parse `finish-check --hold`'s `NAME,START,LENGTH[,ducked]` spec.

    Comma-delimited, not colon — colon is already `_parse_timecode`'s own
    `[[H:]M:]S` separator, and a hold spec needs to stay unambiguous if a
    `START`/`LENGTH` is ever given as a timecode. `--rect X,Y,W,H`'s own
    convention, one field over. `START`/`LENGTH` are `final`'s own absolute
    seconds, plain floats — a hold spec is comma-delimited precisely so it
    never collides with a timecode's own colons.
    """
    parts = value.split(",")
    if len(parts) not in (3, 4):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a hold — use NAME,START,LENGTH[,ducked], "
            "e.g. miggs,42.0,1.9"
        )
    name, start_str, length_str, *rest = parts
    try:
        start, length = float(start_str), float(length_str)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r}'s START and LENGTH must be numbers, in final's own "
            "absolute seconds"
        ) from None
    ducked = bool(rest) and rest[0].strip().lower() in ("ducked", "true", "1", "yes")
    return {"name": name, "start": start, "length": length, "ducked": ducked}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proofcut",
        description="Local-first AI video editing: an MCP server over ffmpeg, whisper, and OTIO.",
    )
    parser.add_argument("--version", action="version", version=f"proofcut {__version__}")
    # Global and git-style, before the subcommand: `proofcut -C myproject cut ...`.
    # Defining it per-subparser instead would make the two positions clobber
    # each other on the shared dest.
    # `default=None`, resolved to "." in `main`, so `init` can tell an
    # explicit `-C .` from no `-C` at all.
    parser.add_argument("-C", "--project", help="project directory (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_mcp = sub.add_parser("mcp", help="run the MCP server, over stdio (default) or HTTP")
    p_mcp.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio (default — every existing client spawns the server this way) or http",
    )
    # `default=None` rather than `webui.DEFAULT_HOST`/`server.DEFAULT_HTTP_PORT`
    # directly: `proofcut.server` imports the MCP SDK and is only ever imported
    # lazily (inside `_cmd_mcp`), and resolving the real default here would
    # force that import on every `proofcut` invocation, not just `mcp`.
    p_mcp.add_argument(
        "--host",
        default=None,
        help="bind address for --transport http (default: loopback, like `proofcut web`)",
    )
    p_mcp.add_argument(
        "--port",
        type=int,
        default=None,
        help="port for --transport http (default: one past `proofcut web`'s; 0 picks a free one)",
    )
    p_mcp.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "allow --host to bind off loopback for --transport http; the "
            "Host-header guard still runs, widened to also accept that host "
            "rather than turned off"
        ),
    )
    p_mcp.add_argument(
        "--allow-remote-host",
        action="append",
        default=None,
        dest="allow_remote_hosts",
        metavar="NAME",
        help=(
            "a Host value a remote client will actually present (repeatable); "
            "required with --allow-remote when --host is a wildcard address "
            "(0.0.0.0, ::) since that address is never what a real client sends"
        ),
    )
    sub.add_parser("ping", help="print the same payload the MCP ping tool returns")

    p_brief = sub.add_parser(
        "brief", help="print one of the briefs the MCP server ships as a prompt"
    )
    p_brief.add_argument("brief", choices=["cut", "film", "review"])
    p_brief.add_argument("media", nargs="?", help="the material folder (cut, film)")
    p_brief.add_argument("--output", help="where the finished file goes")
    p_brief.add_argument("--length", help="how long the result should run, e.g. 90s")
    p_brief.add_argument("--end-card", help="what the film's end card reads (film)")
    p_brief.add_argument("--loudness", help="the film's master level in LUFS (film)")
    p_brief.add_argument("--render", help="the rendered file to check (review)")

    p_doctor = sub.add_parser(
        "doctor", help="check every external dependency proofcut needs, and say how to fix each"
    )
    # The one subcommand that prints prose by default. Every other one emits
    # JSON because its caller is a script or an agent; doctor's caller is a
    # person who has just cloned this and wants to know what is missing, and
    # the sentence after a ✗ is the whole point of the command. `--json` is
    # the same dict the MCP tool returns, for the scripted case.
    p_doctor.add_argument(
        "--json", action="store_true", help="emit the report as JSON instead of prose"
    )

    p_setup = sub.add_parser(
        "setup",
        help="install, for this user, what `proofcut doctor` reports missing (Linux, Windows, Intel Mac; no sudo)",
    )
    # Prose, like doctor, and for the same reason: its caller is a person. It
    # is CLI-only on purpose — an agent must not start a 2 GB download or
    # change PATH unasked (docs/plans/INSTALL.md § Step 3).
    p_setup.add_argument("--plan", action="store_true", help="say what would be installed, and stop")
    p_setup.add_argument("--yes", action="store_true", help="install without asking")
    p_setup.add_argument(
        "--uninstall", action="store_true", help="remove everything setup installed, and nothing else"
    )
    p_setup.add_argument("--json", action="store_true", help="emit JSON instead of prose")

    p_unlock = sub.add_parser(
        "unlock",
        help="clear the project lock a dead agent session left behind (-C DIR)",
    )
    # CLI-only on purpose, like setup: an agent able to break another agent's
    # lock is two writers again (docs/plans/PROJECT-LOCK.md § Breaking a stale lock).
    p_unlock.add_argument(
        "--force", action="store_true", help="break the lock even though its session is alive"
    )

    p_init = sub.add_parser("init", help="create a project directory")
    # `default=None`, not `"."`, so the handler can tell "not given" from
    # "given as `.`" and refuse the ambiguous both-were-given call.
    p_init.add_argument(
        "path",
        nargs="?",
        help="where to create it (default: the -C directory, or .)",
    )
    p_init.add_argument("--name", help="project name (default: the directory name)")

    p_info = sub.add_parser("info", help="show a project's manifest")
    p_info.add_argument(
        "--raw",
        action="store_true",
        help="print the manifest verbatim, descriptions and all",
    )

    p_migrate = sub.add_parser(
        "migrate",
        help="bring an older project manifest forward to the current schema "
        "(and rename a pre-rename lucid.json to proofcut.json)",
    )
    p_migrate.add_argument(
        "--plan", action="store_true", help="report the steps and the version, writing nothing"
    )

    p_import = sub.add_parser("import", help="register a media file with the project")
    p_import.add_argument("source", help="path to the media file")
    p_import.add_argument("--clip-id", help="override the generated clip id")
    p_import.add_argument(
        "--copy", action="store_true", help="copy the media in rather than linking it"
    )
    p_import.add_argument(
        "--mix",
        action="store_true",
        help="sum a multi-stream container's audio into the one track proofcut edits",
    )
    p_import.add_argument(
        "--audio-stream",
        type=int,
        help="keep one audio stream of a multi-stream container (0 is the first)",
    )
    p_import.add_argument(
        "--no-sheet",
        dest="sheet",
        action="store_false",
        help="skip the first-look contact sheet this makes by default",
    )

    p_list_media = sub.add_parser(
        "list-media", help="list media files under a directory that `import` could register"
    )
    p_list_media.add_argument("source_dir", help="directory to scan")
    p_list_media.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="only look in source_dir itself, not its subdirectories",
    )

    p_role = sub.add_parser(
        "role", help="read or set a clip's import role — voiceover vs footage"
    )
    p_role.add_argument("clip_id")
    p_role.add_argument(
        "role", nargs="?", choices=sorted(ops.CLIP_ROLES), help="omit to read what is stored"
    )
    p_role.add_argument(
        "--reset", action="store_true", help="clear it back to undeclared"
    )

    p_clip_rm = sub.add_parser(
        "clip-rm", help="un-register a clip, refusing if anything depends on it yet"
    )
    p_clip_rm.add_argument("clip_id")

    p_attach = sub.add_parser("attach-transcript", help="ingest a word-timed whisper JSON")
    p_attach.add_argument("clip_id")
    p_attach.add_argument("transcript", help="path to the whisper JSON")

    p_transcribe = sub.add_parser("transcribe", help="transcribe a clip's media with whisper")
    p_transcribe.add_argument("clip_id")
    p_transcribe.add_argument(
        "--model", default=asr.DEFAULT_MODEL, help=f"whisper model ({asr.DEFAULT_MODEL})"
    )
    p_transcribe.add_argument("--language", help="force a language instead of detecting one")

    p_hear = sub.add_parser(
        "hear",
        help="what the source audio actually says across a span — a windowed reading, never attached",
    )
    p_hear.add_argument("clip_id")
    p_hear.add_argument("--from", dest="start", type=_parse_timecode, required=True, help="span start, source time")
    p_hear.add_argument("--to", dest="end", type=_parse_timecode, required=True, help="span end, source time")
    p_hear.add_argument(
        "--model", default=asr.WINDOWED_MODEL, help=f"whisper model ({asr.WINDOWED_MODEL}, the windowed pass's own)"
    )
    p_hear.add_argument("--language", help="force a language instead of detecting one per window")
    p_hear.add_argument("--window", type=float, default=asr.WINDOW, help=f"window length in seconds ({asr.WINDOW})")
    p_hear.add_argument("--overlap", type=float, default=asr.OVERLAP, help=f"window overlap in seconds ({asr.OVERLAP})")

    p_tx = sub.add_parser("transcript", help="read a clip's transcript")
    p_tx.add_argument("clip_id")
    p_tx.add_argument("--first", type=int, help="first word index (inclusive)")
    p_tx.add_argument("--last", type=int, help="last word index (inclusive)")
    p_tx.add_argument("--search", help="locate a phrase; returns word ranges")
    p_tx.add_argument("--limit", type=int, help="return at most this many words (default: all)")

    p_tx_checks = sub.add_parser(
        "transcript-checks",
        help="re-run the attach-time transcript checks over an attached transcript",
    )
    p_tx_checks.add_argument(
        "clip_id", nargs="?", help="one clip; omitted, every clip with a transcript"
    )

    p_resolve = sub.add_parser(
        "resolve",
        help="resolve a phrase to a word range against a clip's transcript — what every "
        "--phrase flag calls internally, exposed on its own",
    )
    p_resolve.add_argument("clip_id")
    p_resolve.add_argument("phrase")
    p_resolve.add_argument(
        "--after", type=int, default=-1, help="only match forward of this word index"
    )
    p_resolve.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )
    p_resolve.add_argument(
        "--no-fuzzy", dest="fuzzy", action="store_false", default=True,
        help="refuse rather than falling back to a fuzzy match when nothing matches exactly",
    )

    p_attribute = sub.add_parser(
        "attribute-speakers",
        help="label each word with the mic that was loudest while it was spoken",
    )
    p_attribute.add_argument("clip_id")
    p_attribute.add_argument(
        "--stream",
        type=int,
        action="append",
        dest="streams",
        metavar="K",
        help="an audio stream to read, by ffmpeg's audio ordinal (-map 0:a:K), "
        "repeatable. Omitted, every stream the container holds",
    )
    p_attribute.add_argument(
        "--label",
        action="append",
        dest="labels",
        help="the name for the preceding --stream, repeatable and in the same "
        "order (default speaker1, speaker2, ...)",
    )
    p_attribute.add_argument(
        "--margin-db",
        type=float,
        default=ops.spk.MARGIN_DB,
        metavar="DB",
        help=f"dB the loudest mic must lead by before the word is called "
        f"(default {ops.spk.MARGIN_DB:g}; under it the word is reported, never guessed)",
    )
    p_attribute.add_argument(
        "--limit",
        type=int,
        default=ops.AMBIGUOUS_SPANS,
        help=f"ambiguous spans listed (default {ops.AMBIGUOUS_SPANS}; the total is "
        "always reported)",
    )
    p_attribute.add_argument(
        "--apply",
        action="store_true",
        help="write the labels into the transcript, keeping any existing label "
        "on a word this refuses to call",
    )

    p_describe = sub.add_parser(
        "describe", help="describe a clip's footage in windows, for b-roll search"
    )
    p_describe.add_argument(
        "clip_id", nargs="?", help="one clip; omitted, every video clip not yet described"
    )
    p_describe.add_argument(
        "--window",
        type=float,
        default=describe.WINDOW,
        help=f"seconds of footage per description ({describe.WINDOW:g})",
    )
    p_describe.add_argument(
        "--force", action="store_true", help="describe again, replacing what is stored"
    )
    p_describe.add_argument(
        "--plan",
        action="store_true",
        help="resolve the work list and the estimate without loading a model",
    )

    p_describe_ls = sub.add_parser(
        "describe-ls", help="read the footage descriptions — this is the b-roll search"
    )
    p_describe_ls.add_argument(
        "clip_id", nargs="?", help="only this clip's descriptions (default: every clip)"
    )
    p_describe_ls.add_argument(
        "--contains",
        help="keep descriptions containing every one of these terms, case-insensitively",
    )

    p_card = sub.add_parser("card", help="generate the card assets a picture cue points at")
    card_sub = p_card.add_subparsers(dest="card_command", required=True)

    p_card_templates = card_sub.add_parser(
        "templates", help="the card templates proofcut ships, and their slots"
    )
    p_card_templates.add_argument(
        "name", nargs="?", help="one template's slots in full; the rest by name only"
    )

    p_card_new = card_sub.add_parser(
        "new", help="fill a template's slots and land both the SVG and its PNG"
    )
    p_card_new.add_argument("name", help="the <name> in card:<name>, without an extension")
    p_card_new.add_argument("--template", required=True, help="see `proofcut card templates`")
    p_card_new.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="SLOT=VALUE",
        dest="slots",
        help="fill one slot; repeat for each. A literal \\n in VALUE is a line break",
    )
    p_card_new.add_argument(
        "--width", type=int, help="canvas width (default: the project's own)"
    )
    p_card_new.add_argument(
        "--height", type=int, help="canvas height (with --width)"
    )
    p_card_new.add_argument(
        "--overwrite", action="store_true", help="replace a card of this name if one exists"
    )

    p_card_render = card_sub.add_parser(
        "render", help="rasterise assets/cards/<name>.svg to the PNG card:<name> resolves to"
    )
    p_card_render.add_argument("name", help="the <name> in card:<name>, without an extension")
    p_card_render.add_argument(
        "--width", type=int, help="render width in pixels (with --height; fits, never distorts)"
    )
    p_card_render.add_argument(
        "--height", type=int, help="render height in pixels (with --width)"
    )

    p_card_reauthor = card_sub.add_parser(
        "reauthor", help="draw recorded cards again at the project's canvas"
    )
    p_card_reauthor.add_argument(
        "name",
        nargs="?",
        help="one card; omit to redraw every recorded card the canvas has left behind",
    )
    p_card_reauthor.add_argument(
        "--plan", action="store_true", help="report what would be redrawn, writing nothing"
    )

    p_card_safe_zones = card_sub.add_parser(
        "safe-zones",
        help="measure a rendered card's ink in and around a platform's reserved band",
    )
    p_card_safe_zones.add_argument("name", help="the <name> in card:<name>, without an extension")
    p_card_safe_zones.add_argument(
        "--platform",
        required=True,
        help="a zone in `proofcut pack show`'s safe_zones, or one of proofcut's own "
        "(tiktok-organic, tiktok-ads, reels, shorts, worst-case)",
    )

    p_graphic = sub.add_parser("graphic", help="animated graphics: a web page captured as intro, hold and outro")
    graphic_sub = p_graphic.add_subparsers(dest="graphic_command", required=True)
    p_graphic_templates = graphic_sub.add_parser("templates", help="the animated templates proofcut ships, and their slots")
    p_graphic_templates.add_argument("name", nargs="?", help="one template")
    phases = (
        ("--intro", "seconds of the page that play once from the start of the span"),
        ("--loop", "seconds after the intro to repeat through the hold (a hold that moves)"),
        ("--outro", "seconds of the page, from the hold on, that end the span"),
    )
    p_graphic_new = graphic_sub.add_parser("new", help="make a graphic from a template or a page of HTML, and capture it")
    p_graphic_new.add_argument("name", help="the name overlay add graphic:NAME places")
    source = p_graphic_new.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", help="see `proofcut graphic templates`")
    source.add_argument("--html", type=Path, help="a page written by hand, read from this file")
    p_graphic_new.add_argument(
        "--set", action="append", default=[], metavar="SLOT=VALUE", dest="slots", help="fill one slot; repeat for each"
    )
    for flag, text in phases:
        p_graphic_new.add_argument(flag, type=float, help=text)
    p_graphic_new.add_argument("--replace", action="store_true", help="replace a graphic of this name")
    p_graphic_new.add_argument("--no-capture", action="store_true", help="write the page, draw nothing")
    p_graphic_new.add_argument("--pages", type=int, default=2, help="browser pages capturing side by side (1-8)")
    p_graphic_edit = graphic_sub.add_parser("edit", help="refill slots, replace the page, or move the phases; recapture")
    p_graphic_edit.add_argument("name")
    p_graphic_edit.add_argument(
        "--set", action="append", default=[], metavar="SLOT=VALUE", dest="slots", help="change one slot; repeat"
    )
    p_graphic_edit.add_argument("--html", type=Path, help="a whole new page, read from this file")
    for flag, text in phases:
        p_graphic_edit.add_argument(flag, type=float, help=text)
    p_graphic_edit.add_argument("--no-loop", action="store_true", help="make the hold still again")
    p_graphic_edit.add_argument("--no-capture", action="store_true", help="leave the capture stale")
    p_graphic_edit.add_argument("--pages", type=int, default=2, help="browser pages capturing side by side (1-8)")
    p_graphic_capture = graphic_sub.add_parser("capture", help="capture a graphic, or every missing or stale one")
    p_graphic_capture.add_argument("name", nargs="?")
    p_graphic_capture.add_argument("--force", action="store_true", help="recapture a current one too")
    p_graphic_capture.add_argument("--pages", type=int, default=2, help="browser pages capturing side by side (1-8)")
    graphic_sub.add_parser("ls", help="every graphic, its capture state and the overlays placing it")
    p_graphic_sheet = graphic_sub.add_parser("sheet", help="one labelled tile per phase boundary")
    p_graphic_sheet.add_argument("name")
    p_graphic_save = graphic_sub.add_parser("save", help="copy a graphic into this machine's library")
    p_graphic_save.add_argument("name")
    p_graphic_save.add_argument("--as", dest="as_name", help="its name in the library")
    p_graphic_save.add_argument("--replace", action="store_true", help="replace a library graphic of that name")
    graphic_sub.add_parser("library", help="every graphic saved to this machine's library")
    p_graphic_load = graphic_sub.add_parser("load", help="copy a library graphic into this project and capture it")
    p_graphic_load.add_argument("saved", help="the library graphic")
    p_graphic_load.add_argument("--name", help="its name in this project")
    p_graphic_load.add_argument("--replace", action="store_true", help="replace a project graphic of that name")
    p_graphic_load.add_argument("--no-capture", action="store_true", help="copy only")
    p_graphic_load.add_argument("--pages", type=int, default=2, help="browser pages capturing side by side (1-8)")

    p_look = sub.add_parser("caption-look", help="caption looks saved to this machine's library, across projects")
    look_sub = p_look.add_subparsers(dest="look_command", required=True)
    p_look_save = look_sub.add_parser("save", help="save this project's caption look")
    p_look_save.add_argument("name")
    p_look_save.add_argument("--replace", action="store_true", help="replace a saved look of this name")
    look_sub.add_parser("library", help="every saved caption look")
    p_look_load = look_sub.add_parser("load", help="make a saved look this project's")
    p_look_load.add_argument("name")
    p_look_load.add_argument("--plan", action="store_true", help="report the look without writing it")

    p_image = sub.add_parser("image", help="still images: added once, shown full frame (image:NAME) or as stickers")
    image_sub = p_image.add_subparsers(dest="image_command", required=True)
    p_image_add = image_sub.add_parser("add", help="add a still, upright and in a format everything reads")
    p_image_add.add_argument("source", help="PNG, JPEG, WebP, HEIC, GIF, AVIF, TIFF or BMP")
    p_image_add.add_argument("--name", help="what to call it (default: from the filename)")
    p_image_add.add_argument("--replace", action="store_true", help="replace an image of this name")
    image_sub.add_parser("ls", help="every still, where it came from, and what places it")
    p_image_rm = image_sub.add_parser("rm", help="remove a still nothing places")
    p_image_rm.add_argument("name")

    p_pack = sub.add_parser("pack", help="load, activate and inspect a channel preset pack")
    pack_sub = p_pack.add_subparsers(dest="pack_command", required=True)

    p_pack_apply = pack_sub.add_parser(
        "apply", help="load a pack file, resolve and snapshot every variant, activate one"
    )
    p_pack_apply.add_argument("pack_path", help="path to the pack's JSON file")
    p_pack_apply.add_argument(
        "--variant", default="default", help="which declared variant to activate (default)"
    )
    p_pack_apply.add_argument(
        "--allow-fallback",
        action="store_true",
        help="use a font role's own declared fallback stack when its primary "
        "family does not draw on this machine, instead of refusing",
    )
    p_pack_apply.add_argument(
        "--install-fonts",
        action="store_true",
        help="vendor the pack's own fonts/ directory, if it ships one (writes into $HOME)",
    )
    p_pack_apply.add_argument(
        "--plan", action="store_true", help="resolve and probe without writing"
    )

    p_pack_activate = pack_sub.add_parser(
        "activate", help="switch the active variant to one already snapshotted by pack apply"
    )
    p_pack_activate.add_argument("variant")
    p_pack_activate.add_argument(
        "--plan", action="store_true", help="report without writing"
    )

    p_pack_captions = pack_sub.add_parser(
        "captions", help="apply the active variant's caption preset via caption-style"
    )
    p_pack_captions.add_argument("preset")
    p_pack_captions.add_argument(
        "--plan", action="store_true", help="resolve without writing"
    )

    p_pack_show = pack_sub.add_parser(
        "show", help="what a pack declares — from its file, a project's snapshot, or both"
    )
    p_pack_show.add_argument(
        "--pack-path", help="read and resolve this pack file fresh (no project needed)"
    )
    p_pack_show.add_argument(
        "--variant", help="which snapshotted variant to show (default: the active one)"
    )

    pack_sub.add_parser("status", help="active variant, and which cards/captions have gone stale")

    p_cue = sub.add_parser("cue", help="manage the picture cue table (word_index -> asset)")
    cue_sub = p_cue.add_subparsers(dest="cue_command", required=True)

    p_cue_add = cue_sub.add_parser("add", help="add a cue: from this word onward, show asset")
    p_cue_add.add_argument("clip_id")
    p_cue_add.add_argument(
        "word_index", type=int, nargs="?", help="inclusive word index (omit and use --phrase instead)"
    )
    p_cue_add.add_argument(
        "asset", help="card:name, or a registered video clip_id — `proofcut shots` resolves it"
    )
    p_cue_add.add_argument(
        "--phrase", help="resolve against clip_id's transcript instead of a word index"
    )
    p_cue_add.add_argument(
        "--after", type=int, default=-1, help="only match --phrase forward of this word index"
    )
    p_cue_add.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )
    p_cue_add.add_argument(
        "--src-start",
        type=float,
        help="pin the in-point: seconds into the asset's own source time, as "
        "`proofcut describe ls` reports it. Omitted, the shot reads from wherever "
        "the per-asset cursor is. In-point only — the out-point stays derived",
    )
    p_cue_add.add_argument(
        "--event", help="start on this event of clip_id (name or name#k) instead of a word — for a recording"
    )

    p_cue_rm = cue_sub.add_parser("rm", help="remove a cue")
    p_cue_rm.add_argument("clip_id")
    p_cue_rm.add_argument(
        "word_index", type=int, nargs="?", help="inclusive word index (omit and use --phrase instead)"
    )
    p_cue_rm.add_argument(
        "--phrase", help="resolve against clip_id's transcript instead of a word index"
    )
    p_cue_rm.add_argument(
        "--after", type=int, default=-1, help="only match --phrase forward of this word index"
    )
    p_cue_rm.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )
    p_cue_rm.add_argument("--event", help="the event the cue sits on, as it was added")

    p_cue_ls = cue_sub.add_parser("ls", help="list the cue table")
    p_cue_ls.add_argument("--clip-id", help="only this clip's cues (default: every clip)")

    p_cue_reresolve = cue_sub.add_parser(
        "reresolve",
        help="re-resolve every phrase-addressed cue/mark/music-bed boundary against the "
        "current transcript and report what moved",
    )
    p_cue_reresolve.add_argument("--clip-id", help="only this clip (default: every clip)")
    p_cue_reresolve.add_argument(
        "--apply",
        action="store_true",
        help="rewrite word_index for every entry whose phrase still resolves unambiguously. "
        "Off by default: report only",
    )

    p_lexicon = sub.add_parser(
        "lexicon",
        help="the project's standing corrections: caption spellings (hear) and "
        "vo-synth respellings (say)",
    )
    lexicon_sub = p_lexicon.add_subparsers(dest="lexicon_command", required=True)
    lexicon_sub.add_parser("ls", help="list lexicon.json's entries")
    p_lexicon_add = lexicon_sub.add_parser(
        "add",
        help="captions print CANONICAL wherever whisper wrote HEARD (whole words, every "
        "occurrence; undo does not revert it)",
    )
    p_lexicon_add.add_argument("heard", help="the words as whisper spelled them")
    p_lexicon_add.add_argument("canonical", help="what to print (or, with --kind say, to say) instead")
    p_lexicon_add.add_argument("--kind", choices=("hear", "say"), default="hear")
    p_lexicon_add.add_argument("--plan", action="store_true", help="report, write nothing")
    p_lexicon_rm = lexicon_sub.add_parser("rm", help="remove one entry")
    p_lexicon_rm.add_argument("heard")
    p_lexicon_rm.add_argument("--kind", choices=("hear", "say"), default="hear")
    p_lexicon_rm.add_argument("--plan", action="store_true", help="report, write nothing")

    p_unspoken = sub.add_parser(
        "unspoken",
        help="manage the words the transcript holds and the recording never said",
    )
    unspoken_sub = p_unspoken.add_subparsers(dest="unspoken_command", required=True)

    p_unspoken_add = unspoken_sub.add_parser(
        "add", help="mark a word as never spoken: captions and verify stop expecting it"
    )
    p_unspoken_add.add_argument("clip_id")
    p_unspoken_add.add_argument(
        "word_index", type=int, nargs="?", help="inclusive word index (omit and use --phrase instead)"
    )
    p_unspoken_add.add_argument(
        "--phrase",
        help="resolve against clip_id's transcript instead of a word index — must resolve "
        "to exactly one word",
    )
    p_unspoken_add.add_argument(
        "--after", type=int, default=-1, help="only match --phrase forward of this word index"
    )
    p_unspoken_add.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )

    p_unspoken_rm = unspoken_sub.add_parser("rm", help="unmark a word")
    p_unspoken_rm.add_argument("clip_id")
    p_unspoken_rm.add_argument(
        "word_index", type=int, nargs="?", help="inclusive word index (omit and use --phrase instead)"
    )
    p_unspoken_rm.add_argument(
        "--phrase",
        help="resolve against clip_id's transcript instead of a word index — must resolve "
        "to exactly one word",
    )
    p_unspoken_rm.add_argument(
        "--after", type=int, default=-1, help="only match --phrase forward of this word index"
    )
    p_unspoken_rm.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )

    unspoken_sub.add_parser("ls", help="list every marked word, and which marks have gone stale")

    p_unspoken_detect = unspoken_sub.add_parser(
        "detect",
        help="propose the words a render's own transcription says were never spoken",
    )
    p_unspoken_detect.add_argument("render", help="a finished render of this timeline")
    p_unspoken_detect.add_argument("--clip-id", help="only this clip (default: every transcript)")
    p_unspoken_detect.add_argument(
        "--transcript",
        dest="transcript_path",
        help="an existing transcription of the render — `verify` leaves one in "
        "cache/verify/. Passed explicitly rather than found, so a re-render under "
        "the same name is never judged against the previous render's audio",
    )
    p_unspoken_detect.add_argument("--model", help="whisper model (default: the verify default)")
    p_unspoken_detect.add_argument("--language")
    p_unspoken_detect.add_argument(
        "--pad",
        type=float,
        default=ops.UNSPOKEN_PAD,
        help="seconds either side of a candidate to read the render's own words "
        f"(default: {ops.UNSPOKEN_PAD})",
    )
    p_unspoken_detect.add_argument(
        "--apply",
        action="store_true",
        help="write the proposals as marks. Off by default: this changes what a "
        "caption says, and a wrong mark deletes a real word from every check",
    )

    p_synopsis = sub.add_parser(
        "synopsis", help="read, set or clear what a clip is — the corpus a b-roll picker needs"
    )
    p_synopsis.add_argument(
        "clip_id", nargs="?", help="omit to list every clip's synopsis and which are missing one"
    )
    p_synopsis.add_argument(
        "text",
        nargs="?",
        help="a sentence or three naming the work, the scene and the people. It is "
        "allowed to carry what a camera cannot see — who wrote it, what the twist "
        "means — because that is what decides the placement",
    )
    p_synopsis.add_argument("--clear", action="store_true", help="remove this clip's synopsis")

    p_events = sub.add_parser(
        "events",
        help="named instants in a recording (sent, typing_started, a keystroke) — "
        "list, import a recorder's JSON, add one, or clear",
    )
    p_events.add_argument("clip_id", nargs="?", help="omit to count every clip's events")
    p_events.add_argument(
        "--import",
        dest="source",
        metavar="FILE",
        help="a recorder's JSON (name → seconds, or a bare list with --name); "
        "replaces this clip's events of the names it brings",
    )
    p_events.add_argument(
        "--origin",
        help="the key in the imported file holding the recording's start, "
        "subtracted from every time",
    )
    p_events.add_argument(
        "--offset", type=float, default=0.0, help="seconds subtracted after --origin"
    )
    p_events.add_argument("--name", help="the event to --add, or what a bare list's times are")
    p_events.add_argument(
        "--add",
        dest="at",
        type=_parse_timecode,
        metavar="TIMECODE",
        help="add one event, named by --name, at this source instant",
    )
    p_events.add_argument(
        "--resolve",
        dest="event",
        metavar="NAME[#K]",
        help="resolve one address and echo its neighbours",
    )
    p_events.add_argument("--clear", action="store_true", help="remove every event on this clip")
    p_events.add_argument("--plan", action="store_true", help="validate without writing")

    p_broll = sub.add_parser(
        "broll-brief",
        help="the whole b-roll question as data: the catalogue, and every position "
        "with the narration over it",
    )
    p_broll.add_argument(
        "--fps", type=float, help="frame grid to answer on (default: the export's rate)"
    )

    p_shots = sub.add_parser(
        "shots", help="project the cue table into contiguous shots over the current edit"
    )
    p_shots.add_argument(
        "--fps",
        type=float,
        help="answer on this frame grid (default: the project timebase, "
        "which for audio-only projects is milliseconds — pass the export's "
        "rate to see the frames the export will actually cut at)",
    )

    p_seed = sub.add_parser("seed", help="lay a clip down as the timeline")
    p_seed.add_argument("clip_id")
    p_seed.add_argument(
        "--keep-silences", action="store_true", help="do not run auto-editor's silence pass"
    )
    p_seed.add_argument("--threshold", type=float, default=0.04, help="audio threshold (0.04)")
    p_seed.add_argument("--margin", help="auto-editor --margin, e.g. 0.2s")
    p_seed.add_argument("--edit", dest="edit_expr", help="auto-editor --edit expression")

    p_cut = sub.add_parser("cut", help="cut or keep word ranges")
    p_cut.add_argument("clip_id")
    p_cut.add_argument(
        "ranges", nargs="+", type=_word_range, metavar="FIRST:LAST", help="inclusive word ranges"
    )
    p_cut.add_argument(
        "--keep",
        action="store_true",
        help="keep these ranges and drop the rest of the clip (default is to cut them)",
    )
    p_cut.add_argument(
        "--pad", type=float, default=0.0, help="widen each range by N seconds on both sides"
    )
    p_cut.add_argument(
        "--confirm-suspect",
        action="store_true",
        help="allow a boundary word flagged with a suspect duration (see `transcript`)",
    )
    p_cut.add_argument(
        "--through-pause",
        action="store_true",
        help="extend each cut range's trailing edge through the pause after its last "
        "word, when the gap clears the marker threshold (cut mode only)",
    )
    p_cut.add_argument(
        "--plan",
        action="store_true",
        help="show what these ranges resolve to and what the edit would become, "
        "without touching the timeline",
    )

    p_cut_at = sub.add_parser(
        "cut-at", help="cut render/timeline-time spans from watching an export"
    )
    p_cut_at.add_argument(
        "spans", nargs="+", type=_time_span, metavar="START-END|START+DURATION"
    )
    p_cut_at.add_argument(
        "--pad", type=float, default=0.0, help="widen each span's outer edges by N seconds"
    )
    p_cut_at.add_argument(
        "--confirm-suspect",
        action="store_true",
        help="allow an overlapped word flagged with a suspect duration (see `transcript`)",
    )
    p_cut_at.add_argument(
        "--plan",
        action="store_true",
        help="show what these spans resolve to and what the edit would become, "
        "without touching the timeline",
    )

    p_restore = sub.add_parser("restore", help="un-cut word ranges that are currently absent")
    p_restore.add_argument("clip_id")
    p_restore.add_argument(
        "ranges",
        nargs="+",
        type=_word_range,
        metavar="FIRST:LAST",
        help="inclusive word ranges to restore",
    )
    p_restore.add_argument(
        "--pad",
        type=float,
        default=0.0,
        help="match the pad used on the original cut, to bring the padding sliver back too",
    )
    p_restore.add_argument(
        "--plan",
        action="store_true",
        help="show what would be restored without touching the timeline",
    )

    p_locate = sub.add_parser(
        "locate", help="where a source word or source time plays in the current render"
    )
    p_locate.add_argument("clip_id")
    # Mutually exclusive because they are two ways of naming one thing, and a
    # call giving both cannot say which it meant — `ops.locate` refuses the
    # same combination, this just refuses it earlier and with usage text.
    p_where = p_locate.add_mutually_exclusive_group(required=True)
    p_where.add_argument(
        "--words", type=_word_range, metavar="FIRST[:LAST]", help="inclusive word indices"
    )
    p_where.add_argument(
        "--at", type=_parse_timecode, metavar="TIMECODE", help="a source instant, [[H:]M:]S"
    )
    p_where.add_argument(
        "--span",
        type=_time_span,
        metavar="START-END|START+DURATION",
        help="a source interval, in the seconds of the original recording",
    )
    p_where.add_argument(
        "--phrase", help="resolve against clip_id's transcript — a phrase naturally is a range"
    )
    p_where.add_argument(
        "--event", metavar="NAME[#K]", help="a named instant from `events` (k counts from 0)"
    )
    p_locate.add_argument(
        "--after", type=int, default=-1, help="only match --phrase forward of this word index"
    )
    p_locate.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )

    sub.add_parser("status", help="show the current timeline")

    p_view = sub.add_parser(
        "view", help="the whole edit as one payload: segments, seams, every word's fate"
    )
    p_view.add_argument(
        "--clip-id", help="which clip's words to report (default: the one the timeline opens with)"
    )
    p_view.add_argument("--first", type=int, help="first word of the words list to report")
    p_view.add_argument("--limit", type=int, help="report at most this many words (default: all)")

    sub.add_parser("assets", help="every clip and card a cue can point at, with usage counts")

    p_properties = sub.add_parser(
        "properties", help="project/clip/cue detail for a properties inspector"
    )
    p_properties.add_argument("--clip-id", help="narrow to one clip")
    p_properties.add_argument(
        "--word-index", type=int, help="narrow to one cue on --clip-id's own words"
    )

    p_finish_report = sub.add_parser(
        "finish-report",
        help="assembled duration/canvas/caption/picture/marks/seams report for Finish mode",
    )
    p_finish_report.add_argument(
        "--framing",
        action="store_true",
        help=(
            "also measure stale framing (reframe_coverage) — off by default because it "
            "decodes placed footage for a scene-cut scan; ~5.7s on the film"
        ),
    )
    p_finish_report.add_argument(
        "--holds",
        action="store_true",
        help=(
            "also run hold_check against the last render — off by default because it "
            "decodes and transcribes render spans"
        ),
    )
    p_finish_report.add_argument(
        "--continuity",
        action="store_true",
        help=(
            "also run continuity_check (rewind/replay/short-shot/stub) — off by default "
            "because its stub scan decodes placed footage the same way --framing does"
        ),
    )

    p_waveform = sub.add_parser(
        "waveform", help="RMS envelope for the timeline's waveform lane (cached)"
    )
    p_waveform.add_argument(
        "--clip-id", help="which clip's media to measure (default: the one the timeline opens with)"
    )

    p_thumb = sub.add_parser(
        "thumbnail", help="one filmstrip frame for a clip, at a source time (cached)"
    )
    p_thumb.add_argument("clip_id")
    p_thumb.add_argument("at", type=float, help="seconds into the clip's own source")
    p_thumb.add_argument(
        "--interval",
        type=float,
        default=ops.THUMB_INTERVAL,
        help=f"grid `at` snaps to, in seconds ({ops.THUMB_INTERVAL})",
    )

    p_sheet_cmd = sub.add_parser(
        "contact-sheet",
        help="a clip's own head, as labelled frames plus one montage of them — "
        "the look before anything is cued to footage nobody has seen",
    )
    p_sheet_cmd.add_argument("clip_id")
    p_sheet_cmd.add_argument(
        "--seconds",
        type=float,
        default=ops.FIRST_LOOK_SECONDS,
        help=f"how far into the clip to look (default {ops.FIRST_LOOK_SECONDS})",
    )
    p_sheet_cmd.add_argument(
        "--interval",
        type=float,
        default=ops.FIRST_LOOK_INTERVAL,
        help=f"spacing between frames (default {ops.FIRST_LOOK_INTERVAL})",
    )
    p_sheet_cmd.add_argument(
        "--no-montage",
        action="store_true",
        help="frames only, no combined sheet — what `import` itself asks for, "
        "since the frames are already served by the window's own thumb route",
    )

    p_shot_sheet = sub.add_parser(
        "shot-sheet",
        help="one labelled tile per shot of the picture track, as a montage — what the "
        "agent looks at (MCP returns the image itself; here you get its path)",
    )
    p_shot_sheet.add_argument(
        "--page", type=int, default=0, help="which page of tiles, counted from 0"
    )
    p_shot_sheet.add_argument(
        "--per-page",
        type=int,
        default=ops.SHOT_SHEET_PER_PAGE,
        help=f"tiles per page (default {ops.SHOT_SHEET_PER_PAGE}; past ~24 the sheet "
        "is downscaled by vision and the labels go with it)",
    )
    p_shot_sheet.add_argument(
        "--out", help="where to write the montage (default cache/sheets/shots/page<N>.jpg)"
    )

    p_footage_sheet = sub.add_parser(
        "footage-sheet",
        help="one labelled tile per moment of a clip's own footage — a browse of "
        "material with nothing to search, not a look at an edit",
    )
    p_footage_sheet.add_argument("clip_id", help="which registered clip to look at")
    p_footage_sheet.add_argument(
        "--mode",
        choices=ops.FOOTAGE_SHEET_MODES,
        default="auto",
        help="which instants to draw (default auto: describe windows if the clip "
        "has any, else the interval — never scenes, which decodes the whole clip)",
    )
    p_footage_sheet.add_argument(
        "--interval",
        type=float,
        default=ops.FOOTAGE_SHEET_INTERVAL,
        help=f"seconds between tiles in interval mode (default "
        f"{ops.FOOTAGE_SHEET_INTERVAL}, which is describe's own window)",
    )
    p_footage_sheet.add_argument(
        "--page", type=int, default=0, help="which page of tiles, counted from 0"
    )
    p_footage_sheet.add_argument(
        "--per-page",
        type=int,
        default=ops.SHOT_SHEET_PER_PAGE,
        help=f"tiles per page (default {ops.SHOT_SHEET_PER_PAGE})",
    )
    p_footage_sheet.add_argument(
        "--out",
        help="where to write the montage (default cache/sheets/footage/<clip>/page<N>.jpg)",
    )

    p_preview = sub.add_parser(
        "preview", help="resolve one preview asset and say whether a browser will play it"
    )
    p_preview.add_argument("asset", help="a cue's asset key: card:<name>, or a clip_id")

    p_web = sub.add_parser("web", help="serve the preview/timeline UI on localhost")
    # `default=None`, not the loopback literal, so `--tailscale` can supply
    # the bind address without having to guess whether a `127.0.0.1` on
    # `args.host` was typed or defaulted (`p_mcp --host`'s own precedent).
    p_web.add_argument(
        "--host",
        default=None,
        help=f"bind address (default: {webui.DEFAULT_HOST}; --tailscale supplies this node's tailnet address)",
    )
    p_web.add_argument(
        "--port", type=int, default=webui.DEFAULT_PORT, help=f"port ({webui.DEFAULT_PORT}); 0 picks a free one"
    )
    p_web.add_argument("--open", action="store_true", help="open a browser at it")
    # Off-machine access, opt-in and never inferred. `--allow-remote` mirrors
    # `proofcut mcp --transport http`'s flag exactly rather than inventing a
    # second name for the same decision; the difference is that this server
    # can rewrite a project, so widening it also puts a token on every
    # request (webui.remote_policy, and the module docstring above it).
    p_web.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "allow --host to bind off loopback; the Host-header guard still runs, "
            "widened to accept that host, and every request must then carry an "
            "access token (printed as ?t=... in the startup URL)"
        ),
    )
    p_web.add_argument(
        "--allow-remote-host",
        action="append",
        default=None,
        dest="allow_remote_hosts",
        metavar="NAME",
        help=(
            "a Host value a remote client will actually present (repeatable); "
            "required with --allow-remote when --host is a wildcard address "
            "(0.0.0.0, ::) since that address is never what a real client sends"
        ),
    )
    p_web.add_argument(
        "--tailscale",
        action="store_true",
        help=(
            "serve on this node's tailnet address: implies --allow-remote and "
            "fills in --host and --allow-remote-host from `tailscale status` "
            "(the 100.x address and the MagicDNS name)"
        ),
    )
    p_web.add_argument(
        "--token",
        help="use this access token instead of minting one (remote serving only)",
    )
    p_web.add_argument("--verbose", action="store_true", help="log every request, media ranges included")
    # Serves a picker over a scan instead of one fixed project (docs/plans/DAYDREAM.md §
    # Multi-project). Mutually exclusive with -C in practice, checked in
    # `_cmd_web` rather than here because -C is a *global* flag shared with
    # every other subcommand and always carries a value by the time a
    # subcommand runs (`main`'s own `args.project_given`).
    p_web.add_argument(
        "--root",
        help="serve a picker over every proofcut project found under this directory, "
        "instead of one project (default: none — serve -C's project, as always)",
    )

    # `open` is `web` with the discovery removed: the port is *always*
    # ephemeral (no --host/--port — that is the point) and it launches a
    # browser window itself rather than taking `--open` (Studio Step 04
    # contract § A). `-C` is the existing global flag; no new flag for it.
    p_open = sub.add_parser(
        "open", help="start the webui on an ephemeral port and launch a browser window"
    )
    p_open.add_argument(
        "--root",
        help="open Home over every proofcut project found under this directory, "
        "instead of one project (default: none — opens -C's project directly)",
    )

    p_undo = sub.add_parser("undo", help="roll back the last mutation (or --steps N of them)")
    p_undo.add_argument(
        "--steps", type=int, default=1,
        help="roll back this many mutations (default: 1); refused, whole, past the undo depth",
    )
    p_undo.add_argument(
        "--plan", action="store_true",
        help="roll nothing back; say what --steps would undo, as `changes` does",
    )

    p_changes = sub.add_parser(
        "changes", help="what the last mutations did — what undo would roll back, in words"
    )
    p_changes.add_argument(
        "--steps", type=int, default=1,
        help="compare against this many mutations back (default: 1, the last one)",
    )

    p_cap = sub.add_parser("captions", help="write word-timed ASS captions for the timeline")
    p_cap.add_argument("output", help="where to write the .ass subtitle file")
    p_cap.add_argument(
        "--clip-id", help="caption only this clip (default: every clip with a transcript)"
    )
    # Every one of these defaults to None rather than to a number, and that is
    # load-bearing: the defaults live on the *project* now (`caption-style`),
    # so a flag that defaulted to 7 here would silently overrule a stored 4.
    p_cap.add_argument(
        "--preset", choices=sorted(captions.PRESETS), help="override the project's base preset, for this file only"
    )
    p_cap.add_argument("--max-words", type=int, help="words per caption line (project's, else 7)")
    p_cap.add_argument(
        "--max-gap", type=float, help="silence that starts a new line (project's, else 0.7s)"
    )
    p_cap.add_argument(
        "--max-duration", type=float, help="longest a line stays up (project's, else 6.0s)"
    )
    p_cap.add_argument(
        "--hold", type=float, help="linger after the last word (project's, else 0.3s)"
    )
    p_cap.add_argument(
        "--burn", help="burn the captions into this video — must be a render of this timeline"
    )
    p_cap.add_argument(
        "--burn-output", help="captioned video path (default: renders/<name>-captioned.<ext>)"
    )

    p_capview = sub.add_parser(
        "caption-view", help="the captions this timeline would produce, and the style in force"
    )
    p_capview.add_argument(
        "--clip-id", help="caption only this clip (default: every clip with a transcript)"
    )
    p_capview.add_argument("--first", type=int, help="first cue to report")
    p_capview.add_argument("--limit", type=int, help="report at most this many cues (default: all)")

    p_capstyle = sub.add_parser(
        "caption-style", help="read or change the caption look this project keeps"
    )
    p_capstyle.add_argument(
        "--preset", choices=sorted(captions.PRESETS), help="the base look everything else overrides"
    )
    p_capstyle.add_argument("--font", help="font family, as libass will look it up")
    p_capstyle.add_argument(
        "--size", type=int, help=f"point size against a {captions.REFERENCE_HEIGHT}-line canvas"
    )
    p_capstyle.add_argument(
        "--text", metavar="COLOUR", help="the words' colour — #rrggbb[aa], a name, or ASS &H…"
    )
    p_capstyle.add_argument(
        "--highlight", metavar="COLOUR", help="what a word turns as it is spoken (karaoke only)"
    )
    p_capstyle.add_argument("--outline-colour", metavar="COLOUR", help="the outline's colour")
    p_capstyle.add_argument("--box-colour", metavar="COLOUR", help="the box/shadow colour")
    p_capstyle.add_argument("--outline-width", type=float, help="outline thickness")
    p_capstyle.add_argument("--shadow", type=float, help="drop-shadow depth")
    p_capstyle.add_argument(
        "--position", choices=sorted(captions.ALIGNMENTS), help="where on the frame the line sits"
    )
    p_capstyle.add_argument("--margin", type=int, help="distance from that edge")
    # Three-state, and it has to be: `store_true` would default to False, and
    # False is a *setting* here — it would turn karaoke off on every unrelated
    # `caption-style --size 72`. BooleanOptionalAction with default=None gives
    # --karaoke / --no-karaoke / say nothing.
    for flag, helptext in (
        ("bold", "draw the text bold"),
        ("box", "draw an opaque box behind the text instead of an outline"),
        ("karaoke", "highlight each word as it is spoken"),
    ):
        p_capstyle.add_argument(
            f"--{flag}", action=argparse.BooleanOptionalAction, default=None, help=helptext
        )
    p_capstyle.add_argument(
        "--reveal", choices=captions.REVEALS, help="how each word arrives: fade, blur, or none"
    )
    p_capstyle.add_argument("--reveal-ms", type=int, help="length of each word's reveal (default 150)")
    p_capstyle.add_argument("--reveal-blur", type=float, help="how blurred a word starts (reveal blur; default 6)")
    p_capstyle.add_argument("--max-words", type=int, help="words per caption line")
    p_capstyle.add_argument("--max-gap", type=float, help="silence that starts a new line")
    p_capstyle.add_argument("--max-duration", type=float, help="longest a line stays up")
    p_capstyle.add_argument("--hold", type=float, help="linger after the last word")
    p_capstyle.add_argument(
        "--reset", action="store_true", help="drop every override before applying these"
    )
    p_capstyle.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the manifest"
    )

    p_fonts = sub.add_parser(
        "fonts", help="will the caption font actually draw here, measured by rendering"
    )
    p_fonts.add_argument(
        "--install",
        action="store_true",
        help="copy the vendored face where this OS's font system looks (writes into $HOME)",
    )

    p_canvas = sub.add_parser("canvas", help="read or change the shape this project renders at")
    p_canvas.add_argument(
        "size",
        nargs="?",
        metavar="WIDTHxHEIGHT",
        help="e.g. 1080x1920. Omit to read what is in force and what it derives from",
    )
    p_canvas.add_argument(
        "--reset", action="store_true", help="drop the override and go back to the footage's shape"
    )
    p_canvas.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the manifest"
    )

    p_head = sub.add_parser(
        "head", help="read or change the cold open this project plays before its first frame"
    )
    p_head.add_argument(
        "--asset", metavar="CLIP_ID", help="the clip to open on — a registered clip_id, never a card"
    )
    p_head.add_argument(
        "--src-start", type=float, help="seconds into the asset where the head begins (default 0.0)"
    )
    p_head.add_argument("--seconds", type=float, help="the head's whole length")
    p_head.add_argument(
        "--fade-in", type=float, help="drawn from day one, unlike tail's fade (default 0.0)"
    )
    p_head.add_argument(
        "--fade-out", type=float, help="drawn from day one, unlike tail's fade (default 0.0)"
    )
    p_head.add_argument(
        "--gain-db", type=float, help="a flat, non-fading level shift for the head's own clip"
    )
    p_head.add_argument("--reset", action="store_true", help="drop the head entirely")
    p_head.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the manifest"
    )

    p_tail = sub.add_parser(
        "tail", help="read or change the finishing pass this project plays after its last frame"
    )
    p_tail.add_argument(
        "--asset", metavar="card:NAME", help="the card to hold — must be card:name, never a clip"
    )
    p_tail.add_argument(
        "--seconds", type=float, help="the tail's whole length, card included"
    )
    p_tail.add_argument(
        "--fade",
        type=float,
        help="seconds the card dissolves in over the film's end — overlapping the film, never added to `seconds`",
    )
    p_tail.add_argument("--reset", action="store_true", help="drop the tail entirely")
    p_tail.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the manifest"
    )

    p_music = sub.add_parser(
        "music", help="read or change the A2 music bed this project mixes under its edit"
    )
    p_music.add_argument(
        "--asset", metavar="CLIP_ID", help="the music clip — a registered clip_id, never a card"
    )
    p_music.add_argument(
        "--clip-id", help="the transcript track the word indices address"
    )
    p_music.add_argument(
        "--start-word", type=int, metavar="N", help="word index the bed starts on"
    )
    p_music.add_argument(
        "--end-word",
        type=int,
        metavar="N",
        help="word index the bed runs through — omit for a single pass to the end of the timeline",
    )
    p_music.add_argument(
        "--phrase-start",
        help="resolve --clip-id's transcript for the start word instead of --start-word",
    )
    p_music.add_argument(
        "--phrase-end",
        help="resolve --clip-id's transcript for the end word instead of --end-word",
    )
    p_music.add_argument(
        "--event", help="start the bed on this event of --clip-id (name or name#k) instead of a word"
    )
    p_music.add_argument(
        "--until-event", help="end the bed on this event of --clip-id instead of a word"
    )
    p_music.add_argument(
        "--after", type=int, default=-1, help="only match a --phrase-* forward of this word index"
    )
    p_music.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )
    p_music.add_argument(
        "--fade-in", type=float, help="seconds of fade drawn over the bed's audible start"
    )
    p_music.add_argument(
        "--fade-out", type=float, help="seconds of fade ending where the music audibly ends"
    )
    p_music.add_argument(
        "--clear-end",
        action="store_true",
        help="drop the end word back to running to the end of the timeline",
    )
    p_music.add_argument("--src-in", type=float, help="seconds into the bed's own asset where it starts")
    p_music.add_argument(
        "--crossfade", type=float, help="seconds a rotation's assets overlap, and a passage's default"
    )
    p_music.add_argument(
        "--rotate",
        action="append",
        metavar="CLIP_ID",
        help="an asset played in turn when the bed's own runs out — repeatable; --clear-rotate empties it",
    )
    p_music.add_argument("--clear-rotate", action="store_true", help="drop the bed's rotation")
    p_music.add_argument(
        "--passage",
        action="append",
        metavar="ASSET,START[,SRC_IN[,CROSSFADE]]",
        help="a later passage: its asset, its start (a word index, event:NAME, or a phrase), and optionally "
        "its in-point and the crossfade into it — repeatable, and replaces every passage; "
        "--clear-passages empties them",
    )
    p_music.add_argument("--clear-passages", action="store_true", help="drop every later passage")
    p_music.add_argument("--under", type=float, help="level the bed this many LU below the VO, measured")
    p_music.add_argument("--clear-under", action="store_true", help="play every asset at its own level again")
    p_music.add_argument(
        "--loudness", type=float, metavar="LUFS", help="level the bed to this loudness, measured — for a film with no VO"
    )
    p_music.add_argument("--clear-loudness", action="store_true", help="drop the loudness level")
    p_music.add_argument(
        "--over-tail",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="run a bed that goes to the end on under the tail's card, fading out with it",
    )
    p_music.add_argument(
        "--duck", type=float, metavar="DB", help="pull the bed this many dB down while the voice is speaking"
    )
    p_music.add_argument("--clear-duck", action="store_true", help="play the bed at one level again")
    p_music.add_argument("--reset", action="store_true", help="drop the music bed entirely")
    p_music.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the manifest"
    )

    p_vo_extend = sub.add_parser(
        "vo-extend",
        help="open a gap in a clip's track for material the recording never had",
    )
    p_vo_extend.add_argument("clip_id")
    p_vo_extend.add_argument(
        "word_index",
        type=int,
        nargs="?",
        help="the last word before the gap — the hold opens right after it "
        "(omit and use --phrase instead)",
    )
    p_vo_extend.add_argument("seconds", type=float, help="the hold's length")
    p_vo_extend.add_argument(
        "--phrase",
        help="resolve against clip_id's transcript instead of a word index — binds to the "
        "phrase's last word, the same meaning as word_index",
    )
    p_vo_extend.add_argument(
        "--after", type=int, default=-1, help="only match --phrase forward of this word index"
    )
    p_vo_extend.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )
    p_vo_extend.add_argument(
        "--plan", action="store_true", help="resolve and report covered_by without writing"
    )

    p_vo_synth = sub.add_parser(
        "vo-synth",
        help="say a line in a cloned voice: render several seeds, rank by likeness, read back",
    )
    p_vo_synth.add_argument("text", help="the words to say")
    p_vo_synth.add_argument(
        "--voice", help="a directory holding ref.wav + ref.txt (default: $PROOFCUT_TTS_VOICE; there is no built-in voice)"
    )
    p_vo_synth.add_argument(
        "--candidates", type=int, default=ops.SYNTH_CANDIDATES, help="how many seeds to render and rank"
    )
    p_vo_synth.add_argument("--seed", type=int, default=0, help="the first seed of the range")
    p_vo_synth.add_argument(
        "--max-seconds", type=float, default=ops.SYNTH_MAX_SECONDS, help="the hard length cap on a render"
    )
    p_vo_synth.add_argument(
        "--after",
        nargs=2,
        metavar=("CLIP_ID", "WORD_INDEX"),
        help="splice the winner into this clip's track right after this word",
    )
    p_vo_synth.add_argument(
        "--no-readback", action="store_true", help="skip the whisper readback of the winner"
    )
    p_vo_synth.add_argument(
        "--plan", action="store_true", help="resolve and report (and rank, if cached) without rendering or writing"
    )
    p_vo_synth.add_argument(
        "--lexicon",
        help='a JSON file of {"say": {written: respelling}, "hear": {variant: canonical}} '
        "(default: the project's lexicon.json, if present)",
    )
    p_vo_synth.add_argument(
        "--flat-floor",
        type=float,
        default=ops.SYNTH_FLAT_FLOOR,
        help="semitones of pitch movement below which the flatness penalty starts",
    )
    p_vo_synth.add_argument(
        "--flat-weight",
        type=float,
        default=ops.SYNTH_FLAT_WEIGHT,
        help="likeness docked per semitone under the floor (0 restores likeness-only ranking)",
    )

    p_overlay = sub.add_parser(
        "overlay", help="transparent cards drawn over the film (lowerthird, scrim)"
    )
    overlay_sub = p_overlay.add_subparsers(dest="overlay_command", required=True)
    p_overlay_add = overlay_sub.add_parser(
        "add", help="place an overlay card from a word or event to a word, event or length"
    )
    p_overlay_add.add_argument(
        "card",
        help="a card made from an overlay template (card new … lowerthird), graphic:NAME (graphic new) "
        "or image:NAME (image add), which places the still as a sticker",
    )
    p_overlay_add.add_argument("clip_id", help="the clip whose words or events address the span")
    p_overlay_add.add_argument("word_index", type=int, nargs="?", help="the word it starts on")
    p_overlay_add.add_argument("--phrase", help="start on this phrase's first word")
    p_overlay_add.add_argument("--event", help="start on this event (name or name#k)")
    p_overlay_add.add_argument("--until-word", type=int, dest="until_word_index", help="end with this word")
    p_overlay_add.add_argument("--until-phrase", help="end with this phrase's last word")
    p_overlay_add.add_argument("--until-event", help="end on this event")
    p_overlay_add.add_argument("--for", type=float, dest="seconds", help="end this many seconds after the start")
    p_overlay_add.add_argument("--after", type=int, default=-1, help="only match a phrase forward of this word index")
    p_overlay_add.add_argument("--occurrence", type=int, help="pick the Nth phrase match")
    motions = ", ".join(OVERLAY_MOTIONS)
    eases = ", ".join(EASINGS)
    p_overlay_add.add_argument(
        "--enter", choices=OVERLAY_MOTIONS, help=f"{motions} (default {ops.OVERLAY_ENTER[0]}; none for a graphic)"
    )
    p_overlay_add.add_argument("--enter-seconds", type=float, help=f"default {ops.OVERLAY_ENTER[1]}")
    p_overlay_add.add_argument("--enter-ease", choices=tuple(EASINGS), help=f"{eases} (default {ops.OVERLAY_ENTER[2]})")
    p_overlay_add.add_argument(
        "--leave", choices=OVERLAY_MOTIONS, help=f"{motions} (default {ops.OVERLAY_LEAVE[0]}; none for a graphic)"
    )
    p_overlay_add.add_argument("--leave-seconds", type=float, help=f"default {ops.OVERLAY_LEAVE[1]}")
    p_overlay_add.add_argument("--leave-ease", choices=tuple(EASINGS), help=f"{eases} (default {ops.OVERLAY_LEAVE[2]})")
    p_overlay_add.add_argument(
        "--position", type=int, help="where in the stack (0 = bottom; default the top) — a scrim goes under its type"
    )
    p_overlay_add.add_argument("--x", type=float, help="an image's centre across the frame, 0 to 1 (default 0.5)")
    p_overlay_add.add_argument("--y", type=float, help="an image's centre down the frame, 0 to 1 (default 0.5)")
    p_overlay_add.add_argument("--width", type=float, help="an image's width as a fraction of the frame's (default 0.3)")
    p_overlay_add.add_argument("--rotate", type=float, help="degrees to turn an image, clockwise")
    p_overlay_add.add_argument("--style", choices=("plain", "photo"), help="photo: a white border and a soft shadow")
    p_overlay_add.add_argument("--plan", action="store_true", help="resolve and report without writing")
    overlay_sub.add_parser("ls", help="list overlays, bottom of the stack first, with where each plays")
    p_cspan = sub.add_parser(
        "caption-span", help="captions off, or a different look, over a stretch of the film"
    )
    cspan_sub = p_cspan.add_subparsers(dest="caption_span_command", required=True)
    p_cspan_add = cspan_sub.add_parser(
        "add", help="from a word or event to a word, event or length: --off, or --style JSON"
    )
    p_cspan_add.add_argument("clip_id", help="the clip whose words or events address the span")
    p_cspan_add.add_argument("word_index", type=int, nargs="?", help="the word it starts on")
    p_cspan_add.add_argument("--phrase", help="start on this phrase's first word")
    p_cspan_add.add_argument("--event", help="start on this event (name or name#k)")
    p_cspan_add.add_argument("--until-word", type=int, dest="until_word_index", help="end with this word")
    p_cspan_add.add_argument("--until-phrase", help="end with this phrase's last word")
    p_cspan_add.add_argument("--until-event", help="end on this event")
    p_cspan_add.add_argument("--for", type=float, dest="seconds", help="end this many seconds after the start")
    p_cspan_add.add_argument("--after", type=int, default=-1, help="only match a phrase forward of this word index")
    p_cspan_add.add_argument("--occurrence", type=int, help="pick the Nth phrase match")
    look = p_cspan_add.add_mutually_exclusive_group(required=True)
    look.add_argument("--off", action="store_true", help="draw no captions over the span")
    look.add_argument(
        "--style",
        type=json.loads,
        help='caption-style fields over the span, as JSON — \'{"max_words": 1, "size": 150, "position": "middle"}\' '
        "draws each word alone and large",
    )
    p_cspan_add.add_argument("--plan", action="store_true", help="resolve and report without writing")
    cspan_sub.add_parser("ls", help="list caption spans, later ones winning, with where each plays")
    p_cspan_rm = cspan_sub.add_parser("rm", help="remove a caption span")
    p_cspan_rm.add_argument("position", type=int, help="its position in `caption-span ls`")
    p_cspan_rm.add_argument("--plan", action="store_true", help="report without writing")

    p_follow = sub.add_parser("follow", help="put a second recording on the timeline after the first, cut or dissolved")
    p_follow.add_argument("clip_id", help="the incoming recording")
    p_follow.add_argument("after", help="the clip on the timeline it follows")
    p_follow.add_argument("--at-event", help="splice in after this event of AFTER (default: after the last of it)")
    p_follow.add_argument("--src-start", type=float, help="seconds into CLIP_ID where it starts (default its head)")
    p_follow.add_argument("--src-end", type=float, help="seconds into CLIP_ID where it ends (default its end)")
    p_follow.add_argument("--from-event", help="start at this event of CLIP_ID")
    p_follow.add_argument("--until-event", help="end at this event of CLIP_ID")
    p_follow.add_argument("--dissolve", type=float, default=0.0, help="seconds of crossfade into it (default 0, a cut)")
    p_follow.add_argument("--ease", choices=tuple(EASINGS), default="linear", help="the crossfade's curve")
    p_follow.add_argument("--plan", action="store_true", help="resolve and report without writing")

    p_dissolve = sub.add_parser("dissolve", help="set, change or clear the crossfade at a join follow made")
    p_dissolve.add_argument("clip_id", help="the incoming clip of the join")
    p_dissolve.add_argument("src_start", type=float, help="where it starts at that join, in its own seconds")
    p_dissolve.add_argument("seconds", type=float, help="seconds of crossfade; 0 makes the join a cut again")
    p_dissolve.add_argument("--ease", choices=tuple(EASINGS), default="linear", help="the crossfade's curve")
    p_dissolve.add_argument("--plan", action="store_true", help="resolve and report without writing")

    p_inset = sub.add_parser("inset", help="a clip drawn into a rectangle of the recording, following its camera")
    inset_sub = p_inset.add_subparsers(dest="inset_command", required=True)
    p_inset_add = inset_sub.add_parser(
        "add", help="draw ASSET into RECT of CLIP_ID from a word or event, to a word, event, length or its end"
    )
    p_inset_add.add_argument("clip_id", help="the recording it is drawn into, whose words or events address it")
    p_inset_add.add_argument("asset", help="the clip to draw (the render, in a launch clip)")
    p_inset_add.add_argument(
        "rect", help="X0,Y0,X1,Y1 in the recording's own pixels — where it shows what the inset replaces"
    )
    p_inset_add.add_argument("word_index", type=int, nargs="?", help="the word it starts on")
    p_inset_add.add_argument("--phrase", help="start on this phrase's first word")
    p_inset_add.add_argument("--event", help="start on this event (name or name#k)")
    p_inset_add.add_argument("--until-word", type=int, dest="until_word_index", help="end with this word")
    p_inset_add.add_argument("--until-phrase", help="end with this phrase's last word")
    p_inset_add.add_argument("--until-event", help="end on this event")
    p_inset_add.add_argument("--for", type=float, dest="seconds", help="end this many seconds after the start")
    p_inset_add.add_argument("--src-in", type=float, default=0.0, help="seconds into ASSET it starts from (default 0)")
    p_inset_add.add_argument("--after", type=int, default=-1, help="only match a phrase forward of this word index")
    p_inset_add.add_argument("--occurrence", type=int, help="pick the Nth phrase match")
    fade_eases = ", ".join(EASINGS)
    for side in ("enter", "leave"):
        p_inset_add.add_argument(f"--{side}", choices=("fade", "none"), help=f"fade or none (default {ops.INSET_FADE[0]})")
        p_inset_add.add_argument(f"--{side}-seconds", type=float, help=f"default {ops.INSET_FADE[1]}")
        p_inset_add.add_argument(
            f"--{side}-ease", choices=tuple(EASINGS), help=f"{fade_eases} (default {ops.INSET_FADE[2]})"
        )
    p_inset_add.add_argument("--dim", type=float, default=0.0, help="darken the recording around it, 0 to 1")
    p_inset_add.add_argument("--gain-db", type=float, help="its own audio's level (default 0)")
    p_inset_add.add_argument(
        "--level", choices=("speech",), help="measure it once and level its speech to -18 dBFS RMS"
    )
    p_inset_add.add_argument("--mute", action="store_true", help="play none of its audio")
    p_inset_add.add_argument("--position", type=int, help="where in the stack (0 = bottom; default the top)")
    p_inset_add.add_argument("--plan", action="store_true", help="resolve and report without writing")
    inset_sub.add_parser("ls", help="list insets with where each plays and where its rect lands")
    p_inset_rm = inset_sub.add_parser("rm", help="take an inset off the recording (the clip stays)")
    p_inset_rm.add_argument("position", type=int, help="its position, as `inset ls` numbers it")
    p_inset_rm.add_argument("--plan", action="store_true", help="report without writing")
    p_retime = sub.add_parser("retime", help="play spans of the film faster or slower")
    retime_sub = p_retime.add_subparsers(dest="retime_command", required=True)
    p_retime_add = retime_sub.add_parser(
        "add", help="play a span from a word or event to another in SECONDS"
    )
    p_retime_add.add_argument("clip_id", help="the clip whose words or events address the span")
    p_retime_add.add_argument("seconds", type=float, help="how long the span plays for in the render")
    p_retime_add.add_argument("word_index", type=int, nargs="?", help="the word it starts on")
    p_retime_add.add_argument("--phrase", help="start on this phrase's first word")
    p_retime_add.add_argument("--event", help="start on this event (name or name#k)")
    p_retime_add.add_argument("--until-word", type=int, dest="until_word_index", help="end with this word")
    p_retime_add.add_argument("--until-phrase", help="end with this phrase's last word")
    p_retime_add.add_argument("--until-event", help="end on this event")
    p_retime_add.add_argument("--after", type=int, default=-1, help="only match a phrase forward of this word index")
    p_retime_add.add_argument("--occurrence", type=int, help="pick the Nth phrase match")
    p_retime_add.add_argument("--plan", action="store_true", help="resolve and report without writing")
    retime_sub.add_parser("ls", help="list stretches with their Edit and render spans")
    p_retime_rm = retime_sub.add_parser("rm", help="take a stretch away; its span plays at 1x")
    p_retime_rm.add_argument("position", type=int, help="its position, as `retime ls` numbers it")
    p_retime_rm.add_argument("--plan", action="store_true", help="report without writing")
    p_overlay_rm = overlay_sub.add_parser("rm", help="take an overlay off the film (the card stays)")
    p_overlay_rm.add_argument("position", type=int, help="its position, as `overlay ls` numbers it")
    p_overlay_rm.add_argument("--plan", action="store_true", help="report without writing")

    p_sound = sub.add_parser("sound", help="one-shot sounds at words and events (keystrokes, send, land)")
    sound_sub = p_sound.add_subparsers(dest="sound_command", required=True)
    p_sound_add = sound_sub.add_parser(
        "add", help="place a sound at a word, an event, or every event of one name"
    )
    p_sound_add.add_argument("clip_id", help="the clip whose words or events say where")
    p_sound_add.add_argument("word_index", type=int, nargs="?", help="the word it plays at")
    p_sound_add.add_argument(
        "--asset", action="append", required=True, dest="assets",
        help="an imported clip to play; repeat it and each hit draws one",
    )  # fmt: skip
    p_sound_add.add_argument("--phrase", help="play at this phrase's first word")
    p_sound_add.add_argument("--event", help="play at this event (name or name#k)")
    p_sound_add.add_argument("--every", help="play at every event of this name")
    p_sound_add.add_argument("--after", type=int, default=-1, help="only match a phrase forward of this word index")
    p_sound_add.add_argument("--occurrence", type=int, help="pick the Nth phrase match")
    p_sound_add.add_argument("--gain", type=float, default=0.0, dest="gain_db", help="level in dB (0 is the file's own)")
    p_sound_add.add_argument("--jitter", type=float, default=0.0, dest="jitter_db", help="vary each hit by up to this many dB")
    p_sound_add.add_argument(
        "--min-gap", type=float,
        help=f"with --every, drop a hit closer than this to the last (default {ops.SOUND_MIN_GAP}s)",
    )  # fmt: skip
    p_sound_add.add_argument("--src-in", type=float, help="seconds into the asset where the sound starts")
    p_sound_add.add_argument("--src-out", type=float, help="seconds into the asset where the sound stops")
    p_sound_add.add_argument(
        "--ducks", action="store_true", help="the music bed's duck hears this sound, as it hears the voice"
    )
    p_sound_add.add_argument("--plan", action="store_true", help="resolve and report without writing")
    sound_sub.add_parser("ls", help="list sound records with how many hits each places")
    p_sound_rm = sound_sub.add_parser("rm", help="take a sound record off the film (the clip stays)")
    p_sound_rm.add_argument("position", type=int, help="its position, as `sound ls` numbers it")
    p_sound_rm.add_argument("--plan", action="store_true", help="report without writing")
    sound_sub.add_parser(
        "generate", help="write proofcut's generated UI sounds into the project and import them (sfx-*)"
    )

    p_hold = sub.add_parser(
        "hold", help="film-audio holds — a clean span of a clip's own audio spliced into the VO"
    )
    hold_sub = p_hold.add_subparsers(dest="hold_command", required=True)

    p_hold_add = hold_sub.add_parser(
        "add", help="splice a hold: after this VO word, play the film clip's own audio"
    )
    p_hold_add.add_argument("clip_id")
    p_hold_add.add_argument(
        "gap_word_index", type=int, nargs="?", help="last VO word before the gap (omit and use --gap-phrase)"
    )
    p_hold_add.add_argument(
        "cue_word_index", type=int, nargs="?", help="VO word the picture cue for asset starts on (omit and use --cue-phrase)"
    )
    p_hold_add.add_argument("--asset", help="registered clip_id of the film clip (must already be transcribed)")
    p_hold_add.add_argument(
        "--word-index-first", type=int, help="first word of asset's own transcript that must be heard clean"
    )
    p_hold_add.add_argument(
        "--word-index-last", type=int, help="last word of asset's own transcript that must be heard clean"
    )
    p_hold_add.add_argument("--gap-phrase", help="resolve clip_id's transcript for the gap word — binds its last word")
    p_hold_add.add_argument("--cue-phrase", help="resolve clip_id's transcript for the cue word — binds its first word")
    p_hold_add.add_argument(
        "--asset-phrase",
        help="resolve --asset's own transcript for word_index_first/word_index_last together",
    )
    p_hold_add.add_argument(
        "--after", type=int, default=-1, help="only match a --*-phrase forward of this word index"
    )
    p_hold_add.add_argument(
        "--occurrence", type=int, help="pick the Nth match rather than refusing on ambiguity"
    )
    p_hold_add.add_argument(
        "--head-margin", type=float, help=f"seconds before the first word (default {ops.HOLD_HEAD_MARGIN})"
    )
    p_hold_add.add_argument(
        "--tail-margin", type=float, help=f"seconds after the last word (default {ops.HOLD_TAIL_MARGIN})"
    )
    p_hold_add.add_argument(
        "--under", type=float, help=f"LU below the VO (default {ops.HOLD_UNDER})"
    )
    p_hold_add.add_argument("--fade-in", type=float, help=f"seconds (default {ops.HOLD_FADE_IN})")
    p_hold_add.add_argument("--fade-out", type=float, help=f"seconds (default {ops.HOLD_FADE_OUT})")
    p_hold_add.add_argument("--plan", action="store_true", help="resolve and report without writing")

    p_hold_rm = hold_sub.add_parser("rm", help="drop a hold's record and cue (the spliced silence stays)")
    p_hold_rm.add_argument("clip_id")
    p_hold_rm.add_argument("gap_word_index", type=int)

    hold_sub.add_parser("ls", help="list holds with their live-resolved plan")

    p_hold_under = hold_sub.add_parser(
        "under", help="play a film clip's own audio under a span of the VO, levelled below it"
    )
    p_hold_under.add_argument("clip_id", help="the VO clip whose words the span addresses")
    p_hold_under.add_argument("asset", help="the film clip on screen across the span")
    p_hold_under.add_argument("--start-word", type=int, help="first VO word of the span")
    p_hold_under.add_argument("--end-word", type=int, help="last VO word of the span")
    p_hold_under.add_argument("--phrase-start", help="resolve the span's first word by phrase")
    p_hold_under.add_argument("--phrase-end", help="resolve the span's last word by phrase, after the start")
    p_hold_under.add_argument("--after", type=int, default=-1, help="only match a phrase forward of this word")
    p_hold_under.add_argument("--occurrence", type=int, help="pick the Nth match rather than refusing")
    p_hold_under.add_argument("--under", type=float, help="LU below the VO (default 13)")
    p_hold_under.add_argument("--fade-in", type=float, help="seconds (default 0.1)")
    p_hold_under.add_argument("--fade-out", type=float, help="seconds (default 0.3)")
    p_hold_under.add_argument("--plan", action="store_true", help="resolve and report without writing")

    p_hold_under_rm = hold_sub.add_parser("under-rm", help="drop film audio under the VO")
    p_hold_under_rm.add_argument("clip_id")
    p_hold_under_rm.add_argument("word_index_start", type=int)

    p_hold_check = hold_sub.add_parser(
        "check", help="transcribe each hold's own span off a render and check its seams"
    )
    p_hold_check.add_argument("render")

    p_reel = sub.add_parser(
        "reel", help="derive a new project holding one span of this one's timeline"
    )
    p_reel.add_argument("dest", help="where to put the derived project (must not exist yet)")
    # The same surface `cut-at` takes, because it is the same kind of number —
    # seconds an export played at, read off a watch. What differs is the
    # direction: this one names what to *keep*.
    p_reel.add_argument(
        "keep",
        type=_time_span,
        metavar="START-END|START+DURATION",
        help="the span to keep, in the seconds the current export plays at",
    )
    p_reel.add_argument(
        "--canvas",
        metavar="WIDTHxHEIGHT",
        help="reshape the derived project only, e.g. 1080x1920. The film is left alone",
    )
    p_reel.add_argument("--name", help="project name (default: the destination directory's)")
    p_reel.add_argument(
        "--confirm-suspect",
        action="store_true",
        help="allow a kept edge that lands on a word with a suspect duration "
        "(the reel's own two edges — not everything being cut away)",
    )
    p_reel.add_argument(
        "--plan",
        action="store_true",
        help="resolve the spans and the clips it would link, and create nothing",
    )

    p_review = sub.add_parser("review", help="serve a review round: named renders, sheets, A/B pairs")
    review_sub = p_review.add_subparsers(dest="review_command", required=True)

    p_review_add = review_sub.add_parser(
        "add", help="register a rendered file, sheet or A/B member for review"
    )
    p_review_add.add_argument("name", help="how this item is addressed and displayed")
    p_review_add.add_argument("source", help="the file, relative to the project or absolute")
    p_review_add.add_argument(
        "--kind", required=True, choices=list(ops.REVIEW_KINDS), help="what this item is"
    )
    p_review_add.add_argument(
        "--baseline",
        help="the already-registered item this claims to be byte-identical to "
        "(required, and checked, for --kind control)",
    )
    p_review_add.add_argument(
        "--about", help="one plain line the page prints under the name: what this item is"
    )

    p_review_verdict = review_sub.add_parser(
        "verdict", help="record a verdict against a registered review item"
    )
    p_review_verdict.add_argument("name")
    p_review_verdict.add_argument("verdict", help="free-form — yes/no, a choice, a description")
    p_review_verdict.add_argument("--note", help="anything else worth keeping beside the verdict")

    review_sub.add_parser("list", help="list every registered review item and its verdict")

    p_review_serve = review_sub.add_parser(
        "serve", help="serve the review round over HTTP, with a token in the URL"
    )
    p_review_serve.add_argument(
        "--host",
        default=reviewserver.DEFAULT_HOST,
        help=f"bind address ({reviewserver.DEFAULT_HOST}); pass a Tailscale/LAN "
        "address to reach this from a phone",
    )
    p_review_serve.add_argument(
        "--port",
        type=int,
        default=reviewserver.DEFAULT_PORT,
        help=f"port ({reviewserver.DEFAULT_PORT}); 0 picks a free one",
    )
    p_review_serve.add_argument(
        "--token", help="use this token instead of minting a random one"
    )
    p_review_serve.add_argument(
        "--question",
        help="what the reviewer is asked, printed at the top of the page "
        f"(an A/B round defaults to {reviewserver.DEFAULT_AB_QUESTION!r})",
    )
    p_review_serve.add_argument("--verbose", action="store_true", help="log every request")

    p_reframe = sub.add_parser(
        "reframe", help="read or set which part of each clip survives into the frame"
    )
    p_reframe.add_argument(
        "clip_id", nargs="?", help="the clip to crop. Omit to read every clip's crop"
    )
    p_reframe.add_argument(
        "--rect",
        metavar="X,Y,W,H",
        help="the region to keep, in the clip's own source pixels. Grown to the "
        "canvas's shape if it is not already, so everything named stays on screen",
    )
    p_reframe.add_argument(
        "--pane",
        metavar="X,Y,W,H",
        help="draw this window as a stacked split: --rect on top, this below, "
        "each pane getting twice the width one 9:16 crop gets. For the "
        "two-hander one window cannot frame",
    )
    p_reframe.add_argument(
        "--at",
        type=float,
        metavar="SECONDS",
        help="seconds into this clip's own source that the rect applies from, "
        "until the next window. Omit for the window from the head of the file",
    )
    p_reframe.add_argument(
        "--fill",
        choices=["blur"],
        help="draw this window whole, over a blurred copy of itself, instead of "
        "cropping it. Takes no --rect or --pane",
    )
    p_reframe.add_argument(
        "--interp",
        action="store_true",
        help="slide into this window from whatever governed before it, instead "
        "of stepping to it. Needs --at after 0 (nothing before the head of the "
        "source to slide from) and cannot be combined with --pane",
    )
    p_reframe.add_argument(
        "--ease",
        choices=list(EASINGS),
        help="the curve of this window's slide; implies --interp",
    )
    p_reframe.add_argument(
        "--event",
        metavar="NAME[#K]",
        help="start this window at a named instant from `events`, instead of --at",
    )
    p_reframe.add_argument(
        "--reset",
        action="store_true",
        help="drop this clip's overrides, every one of them with no clip_id, or "
        "just the window named by --at or --event",
    )
    p_reframe.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the manifest"
    )

    p_detect = sub.add_parser(
        "reframe-detect",
        help="propose a framing window per camera shot, from where the faces are",
    )
    p_detect.add_argument(
        "clip_id", nargs="?", help="only this clip's placements. Omit for every one"
    )
    p_detect.add_argument(
        "--threshold",
        type=float,
        default=ops.SCENE_THRESHOLD,
        metavar="SCORE",
        help=f"scene score above which a change of picture is a cut "
        f"(default {ops.SCENE_THRESHOLD}, picked by the framing control)",
    )
    p_detect.add_argument(
        "--frames",
        type=int,
        default=ops.DETECT_FRAMES,
        help=f"frames sampled per window (default {ops.DETECT_FRAMES})",
    )
    p_detect.add_argument(
        "--apply",
        action="store_true",
        help="write the proposals through `reframe`, leaving any window that is "
        "already framed by hand alone. Off by default: look at `reframe-sheet` first",
    )
    p_detect.add_argument(
        "--no-split",
        dest="split",
        action="store_false",
        help="never offer a stacked split, however many subjects a window holds. "
        "On by default, and rare: 3 of the film's 59 windows",
    )

    p_coverage = sub.add_parser(
        "reframe-coverage",
        help="which placed seconds are framed by a window chosen for an earlier shot",
    )
    p_coverage.add_argument(
        "clip_id", nargs="?", help="only this clip's placements. Omit for every one"
    )
    p_coverage.add_argument(
        "--threshold",
        type=float,
        default=ops.SCENE_THRESHOLD,
        metavar="SCORE",
        help=f"scene score above which a change of picture is a cut "
        f"(default {ops.SCENE_THRESHOLD}, picked by the framing control)",
    )

    p_continuity_check = sub.add_parser(
        "continuity-check",
        help="rewinds, replays, short shots, and film-internal-cut stubs — reports, never decides",
    )
    p_continuity_check.add_argument(
        "--gap",
        type=float,
        default=ops.CONTINUITY_GAP,
        help=f"timeline seconds that make a re-use a rhyme rather than a rewind "
        f"(default {ops.CONTINUITY_GAP})",
    )
    p_continuity_check.add_argument(
        "--min-shot",
        type=float,
        default=ops.CONTINUITY_MIN_SHOT,
        help=f"shots shorter than this are flagged (default {ops.CONTINUITY_MIN_SHOT}s)",
    )
    p_continuity_check.add_argument(
        "--stub-tolerance",
        type=float,
        default=ops.CONTINUITY_STUB_TOLERANCE,
        help=f"how close a real internal cut has to sit to a shot's own edge to "
        f"flag it as a fragment (default {ops.CONTINUITY_STUB_TOLERANCE}s)",
    )
    p_continuity_check.add_argument(
        "--no-stubs",
        dest="stubs",
        action="store_false",
        help="skip the film-internal-cut-stub scan — it costs a scene-cut decode "
        "per distinct asset placed. On by default",
    )
    p_continuity_check.add_argument(
        "--scene-threshold",
        type=float,
        default=ops.SCENE_THRESHOLD,
        metavar="SCORE",
        help=f"scene score above which a change of picture is a cut "
        f"(default {ops.SCENE_THRESHOLD}; darker footage from a different film "
        "has needed 0.12)",
    )

    p_continuity_accept = sub.add_parser(
        "continuity-accept",
        help="acknowledge one continuity finding once — a deliberate rhyme, never re-reported",
    )
    p_continuity_accept.add_argument("clip_id")
    p_continuity_accept.add_argument("word_index", type=int)
    p_continuity_accept.add_argument("kind", choices=["rewind", "replay", "short_shot", "stub"])

    p_continuity_reject = sub.add_parser(
        "continuity-reject", help="unmark a continuity finding"
    )
    p_continuity_reject.add_argument("clip_id")
    p_continuity_reject.add_argument("word_index", type=int)
    p_continuity_reject.add_argument("kind", choices=["rewind", "replay", "short_shot", "stub"])

    sub.add_parser("continuity-ls", help="every accepted continuity finding, and whether it is stale")

    p_sheet = sub.add_parser(
        "reframe-sheet",
        help="draw every placement's framing window on its own source frames, for review",
    )
    p_sheet.add_argument("--out", help="where to write the montage (default cache/sheets/sheet.png)")
    p_sheet.add_argument(
        "--moments",
        help="comma-separated fractions of each placement to sample (default 0.15,0.5,0.85)",
    )
    p_sheet.add_argument(
        "--extremes",
        action="store_true",
        help="draw each window where the subject is leftmost, median and rightmost "
        "instead of at fixed fractions — the worst moment is one of the ends. Needs "
        "the face detector and minutes of decoding",
    )
    p_sheet.add_argument(
        "--page", type=int, default=0, help="which page of rows, counted from 0"
    )
    p_sheet.add_argument(
        "--per-page",
        type=int,
        default=None,
        help="windows per page (default: all of them, in one PNG for a person to "
        f"open — the MCP tool pages at {ops.REFRAME_SHEET_PER_PAGE} instead, because "
        "it hands the bytes to something that reads a downscaled label as no label). "
        "A page is also cheaper: only its own frames are extracted and probed",
    )

    p_verify = sub.add_parser(
        "verify", help="transcribe a render and diff it against the timeline"
    )
    p_verify.add_argument("render", help="the finished render to check")
    p_verify.add_argument(
        "--clip-id",
        "--clip",
        dest="clip_id",
        help="verify against only this clip (default: every clip with a transcript)",
    )
    p_verify.add_argument(
        "--transcript",
        dest="transcript_path",
        help="use this transcript of the render instead of running whisper",
    )
    p_verify.add_argument(
        "--model",
        help=f"whisper model (default: {asr.DEFAULT_MODEL}, or "
        f"{asr.WINDOWED_MODEL} with --windowed)",
    )
    p_verify.add_argument("--language", help="force a language instead of detecting one")
    p_verify.add_argument(
        "--windowed",
        action="store_true",
        help="transcribe in short overlapping windows — catches a retake a single "
        "pass collapses, at 2x the audio to transcribe",
    )
    p_verify.add_argument(
        "--window", type=float, default=asr.WINDOW, help=f"window length ({asr.WINDOW}s)"
    )
    p_verify.add_argument(
        "--overlap", type=float, default=asr.OVERLAP, help=f"window overlap ({asr.OVERLAP}s)"
    )

    p_frames = sub.add_parser(
        "frames", help="count the timeline's frames, and check an export against it"
    )
    p_frames.add_argument(
        "target",
        nargs="?",
        help="an NLE project to ask melt about, or a render to count with ffprobe "
        "(default: just report the timeline's own total)",
    )
    p_frames.add_argument(
        "--fps",
        type=float,
        help="the rate the export used (default: the picture's, else 30)",
    )

    p_film_check = sub.add_parser(
        "film-check",
        help="compare this project's own numbers against a declared reference export",
    )
    p_film_check.add_argument(
        "reference",
        nargs="?",
        help="a render to compare against, checked with ffprobe alone (default: whatever "
        "was declared before, if anything). Passing one records it on the project.",
    )
    p_film_check.add_argument(
        "--reset", action="store_true", help="drop the declared reference"
    )
    p_film_check.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the manifest"
    )

    p_finish_check = sub.add_parser(
        "finish-check",
        help="check a delivered file (a mix pass outside proofcut) against this project's timeline",
    )
    p_finish_check.add_argument("final", help="the delivered file to check")
    p_finish_check.add_argument(
        "--hold",
        dest="holds",
        action="append",
        type=_parse_hold,
        metavar="NAME,START,LENGTH[,ducked]",
        help="a hold's own span in final's absolute seconds — repeatable. "
        "Default: this project's stored holds, resolved live",
    )
    p_finish_check.add_argument(
        "--prepend-seconds",
        type=float,
        help="length of a cold open/bumper glued on before the timeline's own "
        "first frame (default: this project's stored head length)",
    )
    p_finish_check.add_argument(
        "--fps", type=float, help="the rate the export used (default: the picture's, else 30)"
    )
    p_finish_check.add_argument(
        "--duration-tolerance",
        type=float,
        default=0.5,
        help="how far final's total duration may drift from expected before it "
        "is a fault (0.5s)",
    )
    p_finish_check.add_argument(
        "--pix-th", type=float, default=0.10, help="ffmpeg blackdetect pix_th (0.10)"
    )
    p_finish_check.add_argument(
        "--black-min-duration",
        type=float,
        default=0.0,
        help="shortest black run blackdetect reports (0.0s)",
    )
    p_finish_check.add_argument(
        "--windowed-model",
        help=f"whisper model for every ASR call this makes (default: {asr.WINDOWED_MODEL})",
    )
    p_finish_check.add_argument(
        "--window", type=float, default=asr.WINDOW, help=f"window length ({asr.WINDOW}s)"
    )
    p_finish_check.add_argument(
        "--overlap", type=float, default=asr.OVERLAP, help=f"window overlap ({asr.OVERLAP}s)"
    )
    p_finish_check.add_argument(
        "--recheck-pad",
        type=float,
        default=asr.WINDOW,
        help=f"seconds either side of a dropped run's own neighbours to re-cut "
        f"before re-transcribing it ({asr.WINDOW}s)",
    )
    p_finish_check.add_argument("--language", help="force a language instead of detecting one")
    p_finish_check.add_argument(
        "--clip-id", "--clip", dest="clip_id", help="check against only this clip's words"
    )
    p_finish_check.add_argument(
        "--transcript",
        dest="transcript_path",
        help="use this transcript of final instead of running whisper's windowed pass",
    )

    p_import_edit = sub.add_parser(
        "import-edit",
        help="lay a cut made in Kdenlive down as this project's timeline",
    )
    p_import_edit.add_argument("document", help="a .kdenlive (or .mlt) playlist to read")
    p_import_edit.add_argument(
        "--clip-id",
        help="the registered clip a single-source document maps onto, for when the "
        "document names the media at a path this project does not know",
    )
    p_import_edit.add_argument(
        "--plan", action="store_true", help="resolve and check without writing the timeline"
    )

    p_black = sub.add_parser(
        "black", help="scan a render for black stretches and explain the known ones"
    )
    p_black.add_argument("target", help="the render to scan")
    p_black.add_argument(
        "--fps", type=float, help="the rate the export used (default: the picture's, else 30)"
    )
    p_black.add_argument(
        "--pix-th", type=float, default=0.10, help="ffmpeg blackdetect pix_th (0.10)"
    )
    p_black.add_argument(
        "--min-duration",
        type=float,
        help="shortest run to count, in seconds (default: 0 — see check_black's docstring "
        "for why a positive default would hide the known tail-frame case)",
    )

    p_spots = sub.add_parser(
        "spots", help="pull sample frames from a render, with darkest-first luma stats"
    )
    p_spots.add_argument("target", help="the render to sample")
    p_spots.add_argument("--count", type=int, default=6, help="evenly-spaced samples (6)")
    p_spots.add_argument(
        "--at",
        dest="times",
        type=float,
        action="append",
        metavar="SECONDS",
        help="an explicit sample time; repeatable",
    )
    p_spots.add_argument(
        "--fps", type=float, help="the rate the export used (default: the picture's, else 30)"
    )

    p_atten = sub.add_parser(
        "attenuate", help="pull down short loud non-speech events in narrow word-map gaps"
    )
    p_atten.add_argument("clip_id")
    p_atten.add_argument("--db", type=float, default=-12.0, help="gain reduction in dB (-12.0)")
    p_atten.add_argument(
        "--max-event-seconds",
        type=float,
        default=1.5,
        help="longest event duration that still qualifies (1.5s)",
    )
    p_atten.add_argument(
        "--max-gap-seconds",
        type=float,
        default=2.0,
        help="widest gap that still proves the word map is dense (2.0s)",
    )
    p_atten.add_argument(
        "--pad", type=float, default=0.05, help="widen each attenuated span by N seconds (0.05)"
    )
    p_atten.add_argument(
        "--confirm-suspect",
        action="store_true",
        help="also attenuate events whose bounding word has a suspect duration",
    )
    p_atten.add_argument(
        "--plan",
        action="store_true",
        help="show what would be attenuated without writing anything",
    )

    p_proxy = sub.add_parser(
        "proxy",
        help="build a browser-playable preview stand-in for footage a <video> cannot decode",
    )
    p_proxy.add_argument("clip_id")
    p_proxy.add_argument(
        "--force",
        action="store_true",
        help="rebuild even when a current proxy exists (for a changed PROXY_HEIGHT/CRF)",
    )

    p_speech = sub.add_parser(
        "speech-overlap",
        help="does a proposed clip placement overlap the VO's speech, once both are mapped through the edit?",
    )
    p_speech.add_argument("clip_id")
    p_speech.add_argument(
        "--at", type=_parse_timecode, default=0.0, help="proposed placement start on the timeline (0.0)"
    )
    p_speech.add_argument(
        "--in",
        dest="clip_in",
        type=_parse_timecode,
        help="clip start, source time (default: 0.0)",
    )
    p_speech.add_argument(
        "--out",
        dest="clip_out",
        type=_parse_timecode,
        help="clip end, source time (default: the clip's own duration)",
    )
    p_speech.add_argument(
        "--vo-clip",
        dest="vo_clip_id",
        help="the VO clip_id on the timeline (default: the sole clip on it)",
    )
    p_speech.add_argument(
        "--max-gap",
        type=float,
        default=0.3,
        help="gap tolerance for merging speech into runs (0.3s)",
    )
    p_speech.add_argument(
        "--min-seam",
        type=float,
        default=0.5,
        help="narrowest clean seam worth reporting (0.5s)",
    )
    p_speech.add_argument(
        "--evidence",
        dest="clip_evidence",
        choices=("auto", "transcript", "energy"),
        default="auto",
        help="the clip side's evidence: its transcript, its energy envelope, or whichever exists (auto)",
    )
    p_speech.add_argument(
        "--cap",
        type=float,
        default=energy.CAP,
        help=f"energy.believable's median-multiple cap ({energy.CAP})",
    )

    p_export = sub.add_parser(
        "export",
        help="export or render the timeline (multi-source projects are written as MLT "
        "by proofcut and rendered by melt; everything else goes through auto-editor)",
    )
    p_export.add_argument("output", help="output path")
    p_export.add_argument(
        "--format",
        dest="export_format",
        default="kdenlive",
        help="auto-editor export target (default: kdenlive)",
    )
    p_export.add_argument(
        "--render",
        action="store_true",
        help="render media instead of exporting an NLE project (melt on a "
        "multi-source timeline, auto-editor otherwise)",
    )
    p_export.add_argument(
        "--fps",
        type=float,
        help="frame rate for the NLE timeline, and for a multi-source render "
        "(default: the picture's, else 30)",
    )
    p_export.add_argument(
        "--preset",
        # Read off ops.EXPORT_PRESETS rather than restated here, so a preset
        # added there cannot silently go unreachable from the CLI. 'custom'
        # is not in that dict (ops._resolve_preset handles it specially) so
        # it is added back explicitly.
        choices=[*sorted(ops.EXPORT_PRESETS), "custom"],
        help="a named quality bundle (--render only; an NLE export has no bitrate). "
        "'custom' requires --resolution. 'tiktok-reels' checks that the project's "
        "canvas is 9:16 and refuses otherwise — it never sets the shape, because "
        "that is `proofcut canvas`'s job",
    )
    p_export.add_argument(
        "--resolution",
        type=_resolution,
        metavar="WIDTHxHEIGHT",
        help="single-source render only: letterboxes the existing frame to this "
        "size — does not crop or reframe it. Refused on a multi-source (melt) "
        "project. To crop to fill instead, set the shape with `proofcut canvas`",
    )
    p_export.add_argument(
        "--loudness",
        type=float,
        metavar="LUFS",
        help="--render only: master the render to this integrated loudness (e.g. -16), "
        "measured before and after",
    )
    p_export.add_argument(
        "--true-peak", type=float, default=-1.0, metavar="DBTP", help="the ceiling --loudness holds (default -1)"
    )

    return parser


def _emit(payload: object) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    """Create a project, from `-C` or the positional path — never both.

    Every other subcommand *finds* a project through `-C`, so `-C` reading as
    "the project directory" is the habit the CLI teaches. `init` used to
    ignore it entirely and read only its positional, which meant
    `proofcut -C myproj init` created a project in the current directory and
    reported success — the wrong directory, silently. Both spellings now work
    and giving two different answers is an error rather than a coin flip.
    """
    if args.project_given and args.path is not None:
        raise ProjectError(
            f"init was given two directories: -C {args.project!r} and {args.path!r}. "
            "Pass one — they name where the project goes, and there is no "
            "sensible way to pick between them."
        )
    return _emit(ops.init(args.path if args.path is not None else args.project, name=args.name))


def _cmd_info(args: argparse.Namespace) -> int:
    return _emit(ops.info(args.project, raw=args.raw))


def _cmd_migrate(args: argparse.Namespace) -> int:
    return _emit(ops.migrate(args.project, plan=args.plan))


def _cmd_import(args: argparse.Namespace) -> int:
    return _emit(
        ops.import_media(
            args.project,
            args.source,
            clip_id=args.clip_id,
            copy=args.copy,
            mix=args.mix,
            audio_stream=args.audio_stream,
            sheet=args.sheet,
        )
    )


def _cmd_role(args: argparse.Namespace) -> int:
    return _emit(ops.clip_role(args.project, args.clip_id, args.role, reset=args.reset))


def _cmd_clip_rm(args: argparse.Namespace) -> int:
    return _emit(ops.clip_rm(args.project, args.clip_id))


def _cmd_list_media(args: argparse.Namespace) -> int:
    return _emit(ops.list_media(args.project, args.source_dir, recursive=args.recursive))


def _cmd_attach_transcript(args: argparse.Namespace) -> int:
    return _emit(ops.attach_transcript(args.project, args.clip_id, args.transcript))


def _cmd_transcribe(args: argparse.Namespace) -> int:
    return _emit(
        ops.transcribe(args.project, args.clip_id, model=args.model, language=args.language)
    )


def _cmd_hear(args: argparse.Namespace) -> int:
    return _emit(
        ops.hear(
            args.project,
            args.clip_id,
            start=args.start,
            end=args.end,
            model=args.model,
            language=args.language,
            window=args.window,
            overlap=args.overlap,
        )
    )


def _cmd_transcript(args: argparse.Namespace) -> int:
    return _emit(
        ops.get_transcript(
            args.project,
            args.clip_id,
            first=args.first,
            last=args.last,
            search=args.search,
            limit=args.limit,
        )
    )


def _cmd_transcript_checks(args: argparse.Namespace) -> int:
    return _emit(ops.transcript_checks(args.project, args.clip_id))


def _cmd_attribute_speakers(args: argparse.Namespace) -> int:
    return _emit(
        ops.attribute_speakers(
            args.project,
            args.clip_id,
            streams=args.streams,
            labels=args.labels,
            margin_db=args.margin_db,
            apply=args.apply,
            limit=args.limit,
        )
    )


def _cmd_describe_ls(args: argparse.Namespace) -> int:
    return _emit(ops.describe_ls(args.project, args.clip_id, contains=args.contains))


def _cmd_describe(args: argparse.Namespace) -> int:
    return _emit(
        ops.describe(
            args.project,
            args.clip_id,
            window=args.window,
            force=args.force,
            plan=args.plan,
        )
    )


def _slot_assignments(pairs: list[str]) -> dict[str, str]:
    """`SLOT=VALUE` pairs into a slot dict, `\\n` in VALUE meaning a line break.

    The escape is here rather than in `graphics` because it is a shell
    problem: a real newline inside `--set quote=...` is awkward to type and
    trivial to lose to word splitting, while the op and the MCP tool both
    take the string with its newlines already in it.
    """
    slots: dict[str, str] = {}
    for pair in pairs:
        slot, sep, value = pair.partition("=")
        if not sep or not slot.strip():
            raise ProjectError(f"--set takes SLOT=VALUE, not {pair!r}")
        slots[slot.strip()] = value.replace("\\n", "\n")
    return slots


def _cmd_card(args: argparse.Namespace) -> int:
    if args.card_command == "templates":
        return _emit(ops.card_templates(args.name))
    if args.card_command == "new":
        return _emit(
            ops.card_new(
                args.project,
                args.name,
                args.template,
                _slot_assignments(args.slots),
                width=args.width,
                height=args.height,
                overwrite=args.overwrite,
            )
        )
    if args.card_command == "reauthor":
        return _emit(ops.card_reauthor(args.project, args.name, plan=args.plan))
    if args.card_command == "safe-zones":
        return _emit(ops.card_safe_zones(args.project, args.name, args.platform))
    return _emit(ops.card_render(args.project, args.name, width=args.width, height=args.height))


def _cmd_caption_look(args: argparse.Namespace) -> int:
    if args.look_command == "save":
        return _emit(ops.caption_style_save(args.project, args.name, replace=args.replace))
    if args.look_command == "library":
        return _emit(ops.caption_style_library())
    return _emit(ops.caption_style_load(args.project, args.name, plan=args.plan))


def _cmd_image(args: argparse.Namespace) -> int:
    if args.image_command == "add":
        return _emit(ops.image_add(args.project, args.source, name=args.name, replace=args.replace))
    if args.image_command == "ls":
        return _emit(ops.image_ls(args.project))
    return _emit(ops.image_rm(args.project, args.name))


def _cmd_graphic(args: argparse.Namespace) -> int:
    command = args.graphic_command
    if command == "templates":
        return _emit(ops.graphic_templates(args.name))
    if command == "library":
        return _emit(ops.graphic_library())
    if command == "new":
        return _emit(
            ops.graphic_new(
                args.project, args.name, template=args.template, slots=_slot_assignments(args.slots) or None,
                html=args.html.read_text() if args.html else None, intro=args.intro, loop=args.loop,
                outro=args.outro, replace=args.replace, capture=not args.no_capture, pages=args.pages,
            )
        )  # fmt: skip
    if command == "edit":
        return _emit(
            ops.graphic_edit(
                args.project, args.name, slots=_slot_assignments(args.slots) or None,
                html=args.html.read_text() if args.html else None, intro=args.intro, loop=args.loop,
                no_loop=args.no_loop, outro=args.outro, capture=not args.no_capture, pages=args.pages,
            )
        )  # fmt: skip
    if command == "capture":
        return _emit(ops.graphic_capture(args.project, args.name, force=args.force, pages=args.pages))
    if command == "ls":
        return _emit(ops.graphic_ls(args.project))
    if command == "sheet":
        return _emit(ops.graphic_sheet(args.project, args.name))
    if command == "save":
        return _emit(ops.graphic_save(args.project, args.name, as_name=args.as_name, replace=args.replace))
    return _emit(
        ops.graphic_load(
            args.project, args.saved, name=args.name, replace=args.replace, capture=not args.no_capture, pages=args.pages
        )
    )


def _cmd_pack(args: argparse.Namespace) -> int:
    if args.pack_command == "apply":
        return _emit(
            ops.pack_apply(
                args.project,
                args.pack_path,
                variant=args.variant,
                allow_fallback=args.allow_fallback,
                install_fonts=args.install_fonts,
                plan=args.plan,
            )
        )
    if args.pack_command == "activate":
        return _emit(ops.pack_activate(args.project, args.variant, plan=args.plan))
    if args.pack_command == "captions":
        return _emit(ops.pack_apply_captions(args.project, args.preset, plan=args.plan))
    if args.pack_command == "show":
        # `pack_path` alone needs no project (`fonts`'s own `project_given`
        # shape) — asked with neither, ops.pack_show raises the message
        # naming what it needs.
        return _emit(
            ops.pack_show(
                args.pack_path,
                path=args.project if (args.project_given or not args.pack_path) else None,
                variant=args.variant,
            )
        )
    return _emit(ops.pack_status(args.project))


def _cmd_cue(args: argparse.Namespace) -> int:
    if args.cue_command == "add":
        return _emit(
            ops.cue_add(
                args.project,
                args.clip_id,
                args.word_index,
                args.asset,
                phrase=args.phrase,
                after=args.after,
                occurrence=args.occurrence,
                src_start=args.src_start,
                event=args.event,
            )
        )
    if args.cue_command == "rm":
        return _emit(
            ops.cue_rm(
                args.project,
                args.clip_id,
                args.word_index,
                phrase=args.phrase,
                after=args.after,
                occurrence=args.occurrence,
                event=args.event,
            )
        )
    if args.cue_command == "reresolve":
        return _emit(
            ops.cue_reresolve(args.project, clip_id=args.clip_id, apply=args.apply)
        )
    return _emit(ops.cue_ls(args.project, clip_id=args.clip_id))


def _cmd_lexicon(args: argparse.Namespace) -> int:
    if args.lexicon_command == "add":
        return _emit(ops.lexicon_add(args.project, args.heard, args.canonical, kind=args.kind, plan=args.plan))
    if args.lexicon_command == "rm":
        return _emit(ops.lexicon_rm(args.project, args.heard, kind=args.kind, plan=args.plan))
    return _emit(ops.lexicon_ls(args.project))


def _cmd_unspoken(args: argparse.Namespace) -> int:
    if args.unspoken_command == "add":
        return _emit(
            ops.unspoken_add(
                args.project,
                args.clip_id,
                args.word_index,
                phrase=args.phrase,
                after=args.after,
                occurrence=args.occurrence,
            )
        )
    if args.unspoken_command == "rm":
        return _emit(
            ops.unspoken_rm(
                args.project,
                args.clip_id,
                args.word_index,
                phrase=args.phrase,
                after=args.after,
                occurrence=args.occurrence,
            )
        )
    if args.unspoken_command == "detect":
        return _emit(
            ops.unspoken_detect(
                args.project,
                args.render,
                clip_id=args.clip_id,
                transcript_path=args.transcript_path,
                model=args.model,
                language=args.language,
                pad=args.pad,
                apply=args.apply,
            )
        )
    return _emit(ops.unspoken_ls(args.project))


def _cmd_shots(args: argparse.Namespace) -> int:
    return _emit(ops.build_shots(args.project, fps=args.fps))


def _cmd_seed(args: argparse.Namespace) -> int:
    return _emit(
        ops.seed_timeline(
            args.project,
            args.clip_id,
            remove_silences=not args.keep_silences,
            threshold=args.threshold,
            margin=args.margin,
            edit_expr=args.edit_expr,
        )
    )


def _cmd_cut(args: argparse.Namespace) -> int:
    ranges = args.ranges
    return _emit(
        ops.cut_by_transcript(
            args.project,
            args.clip_id,
            cut=None if args.keep else ranges,
            keep=ranges if args.keep else None,
            pad=args.pad,
            confirm_suspect=args.confirm_suspect,
            through_pause=args.through_pause,
            plan=args.plan,
        )
    )


def _cmd_cut_at(args: argparse.Namespace) -> int:
    return _emit(
        ops.cut_by_time(
            args.project,
            spans=args.spans,
            pad=args.pad,
            confirm_suspect=args.confirm_suspect,
            plan=args.plan,
        )
    )


def _cmd_restore(args: argparse.Namespace) -> int:
    return _emit(
        ops.restore(args.project, args.clip_id, args.ranges, pad=args.pad, plan=args.plan)
    )


def _cmd_locate(args: argparse.Namespace) -> int:
    first = last = None
    source_start = source_end = None
    phrase = None
    if args.words is not None:
        first, last = args.words
    elif args.span is not None:
        source_start, source_end = args.span
    elif args.phrase is not None:
        phrase = args.phrase
    elif args.event is None:
        source_start = args.at
    return _emit(
        ops.locate(
            args.project,
            args.clip_id,
            first=first,
            last=last,
            source_start=source_start,
            source_end=source_end,
            phrase=phrase,
            after=args.after,
            occurrence=args.occurrence,
            event=args.event,
        )
    )


def _cmd_resolve(args: argparse.Namespace) -> int:
    return _emit(
        ops.resolve_phrase(
            args.project,
            args.clip_id,
            args.phrase,
            after=args.after,
            occurrence=args.occurrence,
            fuzzy=args.fuzzy,
        )
    )


def _cmd_status(args: argparse.Namespace) -> int:
    return _emit(ops.status(args.project))


def _cmd_view(args: argparse.Namespace) -> int:
    return _emit(
        ops.timeline_view(args.project, clip_id=args.clip_id, first=args.first, limit=args.limit)
    )


def _cmd_assets(args: argparse.Namespace) -> int:
    return _emit(ops.assets(args.project))


def _cmd_properties(args: argparse.Namespace) -> int:
    return _emit(
        ops.properties(args.project, clip_id=args.clip_id, word_index=args.word_index)
    )


def _cmd_finish_report(args: argparse.Namespace) -> int:
    return _emit(
        ops.finish_report(
            args.project, framing=args.framing, holds=args.holds, continuity=args.continuity
        )
    )


def _cmd_waveform(args: argparse.Namespace) -> int:
    return _emit(ops.waveform(args.project, clip_id=args.clip_id))


def _cmd_thumbnail(args: argparse.Namespace) -> int:
    return _emit(ops.thumbnail(args.project, args.clip_id, args.at, interval=args.interval))


def _cmd_contact_sheet(args: argparse.Namespace) -> int:
    return _emit(
        ops.contact_sheet(
            args.project,
            args.clip_id,
            seconds=args.seconds,
            interval=args.interval,
            montage=not args.no_montage,
        )
    )


def _cmd_shot_sheet(args: argparse.Namespace) -> int:
    return _emit(
        ops.shot_sheet(args.project, page=args.page, per_page=args.per_page, out=args.out)
    )


def _cmd_footage_sheet(args: argparse.Namespace) -> int:
    return _emit(
        ops.footage_sheet(
            args.project,
            args.clip_id,
            mode=args.mode,
            interval=args.interval,
            page=args.page,
            per_page=args.per_page,
            out=args.out,
        )
    )


def _cmd_preview(args: argparse.Namespace) -> int:
    return _emit(ops.preview_source(args.project, args.asset))


def _cmd_web(args: argparse.Namespace) -> int:
    # Blocks until Ctrl-C. Unlike every other subcommand this one prints no
    # JSON — its output is the page.
    host = args.host
    allow_remote = args.allow_remote
    allow_remote_hosts = list(args.allow_remote_hosts or [])
    if args.tailscale:
        # Bind the tailnet address itself rather than a wildcard: the socket
        # is then not on the LAN at all, so the Host guard and the token are
        # the second and third lines of defence rather than the first and
        # only. An explicit --host still wins — someone binding 0.0.0.0 on
        # purpose gets the tailnet names in the allow-list and nothing else
        # taken out of their hands.
        bind, names = webui.tailscale_identity()
        host = host or bind
        allow_remote = True
        allow_remote_hosts += [name for name in names if name not in allow_remote_hosts]
    host = host if host is not None else webui.DEFAULT_HOST
    if args.root is not None:
        if args.project_given:
            raise ProjectError(
                f"web was given two ways to pick a project: -C {args.project!r} and "
                f"--root {args.root!r}. Pass one — --root serves a picker over every "
                "project found under it, -C serves exactly one, and there is no "
                "sensible way to pick between them."
            )
        webui.serve_root(
            args.root,
            host=host,
            port=args.port,
            verbose=args.verbose,
            open_browser=args.open,
            allow_remote=allow_remote,
            allow_remote_hosts=allow_remote_hosts or None,
            token=args.token,
        )
        return 0
    webui.serve(
        args.project,
        host=host,
        port=args.port,
        verbose=args.verbose,
        open_browser=args.open,
        allow_remote=allow_remote,
        allow_remote_hosts=allow_remote_hosts or None,
        token=args.token,
    )
    return 0


def _cmd_open(args: argparse.Namespace) -> int:
    # Blocks until Ctrl-C, same as `_cmd_web` — `open_studio` prints its own
    # URL line and then serves forever; there is no JSON to emit.
    if args.root is not None:
        if args.project_given:
            raise ProjectError(
                f"open was given two ways to pick a project: -C {args.project!r} and "
                f"--root {args.root!r}. Pass one — --root opens Home over every project "
                "found under it, -C opens straight into one project, and there is no "
                "sensible way to pick between them."
            )
        webui.open_studio(root=args.root)
        return 0
    webui.open_studio(args.project)
    return 0


def _cmd_undo(args: argparse.Namespace) -> int:
    return _emit(ops.undo(args.project, steps=args.steps, plan=args.plan))


def _cmd_changes(args: argparse.Namespace) -> int:
    return _emit(ops.changes(args.project, steps=args.steps))


def _cmd_captions(args: argparse.Namespace) -> int:
    return _emit(
        ops.add_captions(
            args.project,
            args.output,
            clip_id=args.clip_id,
            preset=args.preset,
            max_words=args.max_words,
            max_gap=args.max_gap,
            max_duration=args.max_duration,
            hold=args.hold,
            burn=args.burn,
            burn_output=args.burn_output,
        )
    )


def _cmd_caption_view(args: argparse.Namespace) -> int:
    return _emit(
        ops.caption_view(args.project, clip_id=args.clip_id, first=args.first, limit=args.limit)
    )


def _cmd_caption_style(args: argparse.Namespace) -> int:
    return _emit(
        ops.caption_style(
            args.project,
            preset=args.preset,
            font=args.font,
            size=args.size,
            text=args.text,
            highlight=args.highlight,
            outline_colour=args.outline_colour,
            box_colour=args.box_colour,
            bold=args.bold,
            box=args.box,
            outline_width=args.outline_width,
            shadow=args.shadow,
            position=args.position,
            margin=args.margin,
            karaoke=args.karaoke,
            reveal=args.reveal,
            reveal_ms=args.reveal_ms,
            reveal_blur=args.reveal_blur,
            max_words=args.max_words,
            max_gap=args.max_gap,
            max_duration=args.max_duration,
            hold=args.hold,
            reset=args.reset,
            plan=args.plan,
        )
    )


def _cmd_fonts(args: argparse.Namespace) -> int:
    # `project_given` rather than `project`, because `-C` is resolved to "."
    # for every other subcommand and a font is not project state: asked from a
    # directory that happens not to be a project, this should report proofcut's
    # own default rather than refuse. Naming a project is how you ask the
    # narrower question, and typing `-C` is the only evidence of that intent.
    return _emit(ops.fonts(args.project if args.project_given else None, install=args.install))


def _cmd_canvas(args: argparse.Namespace) -> int:
    # The raw string goes through: `ops._parse_canvas` owns every refusal, so
    # the CLI and the MCP tool cannot disagree about what a canvas may be.
    return _emit(ops.canvas(args.project, size=args.size, reset=args.reset, plan=args.plan))


def _cmd_head(args: argparse.Namespace) -> int:
    return _emit(
        ops.head(
            args.project,
            asset=args.asset,
            src_start=args.src_start,
            seconds=args.seconds,
            fade_in=args.fade_in,
            fade_out=args.fade_out,
            gain_db=args.gain_db,
            reset=args.reset,
            plan=args.plan,
        )
    )


def _cmd_tail(args: argparse.Namespace) -> int:
    return _emit(
        ops.tail(
            args.project,
            asset=args.asset,
            seconds=args.seconds,
            fade=args.fade,
            reset=args.reset,
            plan=args.plan,
        )
    )


def _cmd_music(args: argparse.Namespace) -> int:
    return _emit(
        ops.music(
            args.project,
            asset=args.asset,
            clip_id=args.clip_id,
            word_index_start=args.start_word,
            word_index_end=args.end_word,
            phrase_start=args.phrase_start,
            phrase_end=args.phrase_end,
            after=args.after,
            occurrence=args.occurrence,
            fade_in=args.fade_in,
            fade_out=args.fade_out,
            clear_end=args.clear_end,
            src_in=args.src_in,
            crossfade=args.crossfade,
            rotate=[] if args.clear_rotate else args.rotate,
            passages=[] if args.clear_passages else _passages(args.passage),
            under=args.under,
            clear_under=args.clear_under,
            loudness=args.loudness,
            clear_loudness=args.clear_loudness,
            duck=args.duck,
            clear_duck=args.clear_duck,
            over_tail=args.over_tail,
            event=args.event,
            until_event=args.until_event,
            reset=args.reset,
            plan=args.plan,
        )
    )


def _passages(specs: list[str] | None) -> list[dict[str, Any]] | None:
    """`ASSET,START[,SRC_IN[,CROSSFADE]]` — START is a word index when it is an
    integer, an event when it is `event:NAME`, and a phrase otherwise, which is why a phrase with a comma in it
    goes through MCP's `passages` instead."""
    if specs is None:
        return None
    out: list[dict[str, Any]] = []
    for spec in specs:
        parts = [part.strip() for part in spec.split(",")]
        if len(parts) < 2 or len(parts) > 4 or not parts[0] or not parts[1]:
            raise SystemExit(f"--passage wants ASSET,START[,SRC_IN[,CROSSFADE]], not {spec!r}")
        passage: dict[str, Any] = {"asset": parts[0]}
        if parts[1].startswith("event:"):
            passage["event"] = parts[1].removeprefix("event:")
        elif parts[1].lstrip("-").isdigit():
            passage["word_index_start"] = int(parts[1])
        else:
            passage["phrase_start"] = parts[1]
        if len(parts) > 2 and parts[2]:
            passage["src_in"] = float(parts[2])
        if len(parts) > 3 and parts[3]:
            passage["crossfade"] = float(parts[3])
        out.append(passage)
    return out


def _cmd_vo_extend(args: argparse.Namespace) -> int:
    return _emit(
        ops.vo_extend(
            args.project,
            args.clip_id,
            args.word_index,
            args.seconds,
            plan=args.plan,
            phrase=args.phrase,
            after=args.after,
            occurrence=args.occurrence,
        )
    )


def _cmd_vo_synth(args: argparse.Namespace) -> int:
    clip_id, word_index = (None, None) if args.after is None else (args.after[0], int(args.after[1]))
    return _emit(
        ops.vo_synth(
            args.project,
            args.text,
            voice=args.voice,
            candidates=args.candidates,
            seed=args.seed,
            max_seconds=args.max_seconds,
            clip_id=clip_id,
            word_index=word_index,
            readback=not args.no_readback,
            plan=args.plan,
            lexicon=args.lexicon,
            flat_floor=args.flat_floor,
            flat_weight=args.flat_weight,
        )
    )


def _cmd_sound(args: argparse.Namespace) -> int:
    if args.sound_command == "add":
        return _emit(
            ops.sound_add(
                args.project,
                args.assets,
                args.clip_id,
                args.word_index,
                phrase=args.phrase,
                after=args.after,
                occurrence=args.occurrence,
                event=args.event,
                every=args.every,
                gain_db=args.gain_db,
                jitter_db=args.jitter_db,
                min_gap=args.min_gap,
                src_in=args.src_in,
                src_out=args.src_out,
                ducks=args.ducks,
                plan=args.plan,
            )
        )
    if args.sound_command == "ls":
        return _emit(ops.sound_ls(args.project))
    if args.sound_command == "generate":
        return _emit(ops.sound_generate(args.project))
    return _emit(ops.sound_rm(args.project, args.position, plan=args.plan))


def _cmd_overlay(args: argparse.Namespace) -> int:
    if args.overlay_command == "add":
        return _emit(
            ops.overlay_add(
                args.project,
                None if args.card.startswith(("graphic:", "image:")) else args.card,
                args.clip_id,
                args.word_index,
                graphic=args.card.removeprefix("graphic:") if args.card.startswith("graphic:") else None,
                image=args.card.removeprefix("image:") if args.card.startswith("image:") else None,
                x=args.x,
                y=args.y,
                width=args.width,
                rotate=args.rotate,
                style=args.style,
                phrase=args.phrase,
                event=args.event,
                until_word_index=args.until_word_index,
                until_phrase=args.until_phrase,
                until_event=args.until_event,
                seconds=args.seconds,
                after=args.after,
                occurrence=args.occurrence,
                enter=args.enter,
                enter_seconds=args.enter_seconds,
                enter_ease=args.enter_ease,
                leave=args.leave,
                leave_seconds=args.leave_seconds,
                leave_ease=args.leave_ease,
                position=args.position,
                plan=args.plan,
            )
        )
    if args.overlay_command == "ls":
        return _emit(ops.overlay_ls(args.project))
    return _emit(ops.overlay_rm(args.project, args.position, plan=args.plan))


def _cmd_caption_span(args: argparse.Namespace) -> int:
    if args.caption_span_command == "add":
        return _emit(
            ops.caption_span_add(
                args.project,
                args.clip_id,
                args.word_index,
                phrase=args.phrase,
                event=args.event,
                until_word_index=args.until_word_index,
                until_phrase=args.until_phrase,
                until_event=args.until_event,
                seconds=args.seconds,
                after=args.after,
                occurrence=args.occurrence,
                off=args.off,
                style=args.style,
                plan=args.plan,
            )
        )
    if args.caption_span_command == "ls":
        return _emit(ops.caption_span_ls(args.project))
    return _emit(ops.caption_span_rm(args.project, args.position, plan=args.plan))


def _cmd_follow(args: argparse.Namespace) -> int:
    return _emit(
        ops.follow(
            args.project, args.clip_id, args.after, at_event=args.at_event, src_start=args.src_start,
            src_end=args.src_end, from_event=args.from_event, until_event=args.until_event,
            dissolve=args.dissolve, ease=args.ease, plan=args.plan,
        )
    )


def _cmd_dissolve(args: argparse.Namespace) -> int:
    return _emit(ops.dissolve_set(args.project, args.clip_id, args.src_start, args.seconds, ease=args.ease, plan=args.plan))


def _cmd_inset(args: argparse.Namespace) -> int:
    if args.inset_command == "add":
        try:
            rect = [int(value) for value in args.rect.split(",")]
        except ValueError:
            raise ProjectError(f"rect is X0,Y0,X1,Y1 in whole pixels, not {args.rect!r}") from None
        return _emit(
            ops.inset_add(
                args.project,
                args.clip_id,
                args.asset,
                rect,
                args.word_index,
                phrase=args.phrase,
                event=args.event,
                until_word_index=args.until_word_index,
                until_phrase=args.until_phrase,
                until_event=args.until_event,
                seconds=args.seconds,
                src_in=args.src_in,
                after=args.after,
                occurrence=args.occurrence,
                enter=args.enter,
                enter_seconds=args.enter_seconds,
                enter_ease=args.enter_ease,
                leave=args.leave,
                leave_seconds=args.leave_seconds,
                leave_ease=args.leave_ease,
                dim=args.dim,
                gain_db=args.gain_db,
                mute=args.mute,
                position=args.position,
                level=args.level,
                plan=args.plan,
            )
        )
    if args.inset_command == "ls":
        return _emit(ops.inset_ls(args.project))
    return _emit(ops.inset_rm(args.project, args.position, plan=args.plan))


def _cmd_retime(args: argparse.Namespace) -> int:
    if args.retime_command == "add":
        return _emit(
            ops.retime_add(
                args.project,
                args.clip_id,
                args.seconds,
                args.word_index,
                phrase=args.phrase,
                event=args.event,
                until_word_index=args.until_word_index,
                until_phrase=args.until_phrase,
                until_event=args.until_event,
                after=args.after,
                occurrence=args.occurrence,
                plan=args.plan,
            )
        )
    if args.retime_command == "ls":
        return _emit(ops.retime_ls(args.project))
    return _emit(ops.retime_rm(args.project, args.position, plan=args.plan))


def _cmd_hold(args: argparse.Namespace) -> int:
    if args.hold_command == "add":
        return _emit(
            ops.hold_add(
                args.project,
                args.clip_id,
                args.gap_word_index,
                args.cue_word_index,
                args.asset,
                args.word_index_first,
                args.word_index_last,
                gap_phrase=args.gap_phrase,
                cue_phrase=args.cue_phrase,
                asset_phrase=args.asset_phrase,
                after=args.after,
                occurrence=args.occurrence,
                head_margin=args.head_margin,
                tail_margin=args.tail_margin,
                under=args.under,
                fade_in=args.fade_in,
                fade_out=args.fade_out,
                plan=args.plan,
            )
        )
    if args.hold_command == "rm":
        return _emit(ops.hold_rm(args.project, args.clip_id, args.gap_word_index))
    if args.hold_command == "ls":
        return _emit(ops.hold_ls(args.project))
    if args.hold_command == "under":
        return _emit(
            ops.hold_under(
                args.project,
                args.clip_id,
                args.asset,
                word_index_start=args.start_word,
                word_index_end=args.end_word,
                phrase_start=args.phrase_start,
                phrase_end=args.phrase_end,
                after=args.after,
                occurrence=args.occurrence,
                under=args.under,
                fade_in=args.fade_in,
                fade_out=args.fade_out,
                plan=args.plan,
            )
        )
    if args.hold_command == "under-rm":
        return _emit(ops.hold_under_rm(args.project, args.clip_id, args.word_index_start))
    # "check"
    return _emit(ops.hold_check(args.project, args.render))


def _cmd_reel(args: argparse.Namespace) -> int:
    start, end = args.keep
    return _emit(
        ops.reel(
            args.project,
            args.dest,
            start=start,
            end=end,
            canvas=args.canvas,
            name=args.name,
            confirm_suspect=args.confirm_suspect,
            plan=args.plan,
        )
    )


def _cmd_review(args: argparse.Namespace) -> int:
    if args.review_command == "add":
        return _emit(
            ops.review_add(
                args.project, args.name, args.source, kind=args.kind, baseline=args.baseline,
                about=args.about,
            )
        )
    if args.review_command == "verdict":
        return _emit(ops.review_verdict(args.project, args.name, args.verdict, note=args.note))
    if args.review_command == "list":
        return _emit(ops.review_list(args.project))
    # "serve" — blocks until Ctrl-C, like `web`; prints the URL rather than JSON.
    reviewserver.serve(
        args.project,
        host=args.host,
        port=args.port,
        token=args.token,
        question=args.question,
        verbose=args.verbose,
    )
    return 0


def _cmd_reframe(args: argparse.Namespace) -> int:
    # Same rule as `canvas` above: the raw `X,Y,W,H` goes through, because
    # `ops._parse_rect` and `ops._fit_rect_to_canvas` own every refusal.
    return _emit(
        ops.reframe(
            args.project,
            args.clip_id,
            rect=args.rect,
            pane=args.pane,
            src_start=args.at,
            interp=args.interp,
            fill=args.fill,
            reset=args.reset,
            plan=args.plan,
            ease=args.ease,
            event=args.event,
        )
    )


def _cmd_reframe_detect(args: argparse.Namespace) -> int:
    return _emit(
        ops.reframe_detect(
            args.project,
            clip_id=args.clip_id,
            split=args.split,
            threshold=args.threshold,
            frames=args.frames,
            apply=args.apply,
        )
    )


def _cmd_reframe_coverage(args: argparse.Namespace) -> int:
    return _emit(
        ops.reframe_coverage(args.project, clip_id=args.clip_id, threshold=args.threshold)
    )


def _cmd_continuity_check(args: argparse.Namespace) -> int:
    return _emit(
        ops.continuity_check(
            args.project,
            gap=args.gap,
            min_shot=args.min_shot,
            stub_tolerance=args.stub_tolerance,
            stubs=args.stubs,
            scene_threshold=args.scene_threshold,
        )
    )


def _cmd_continuity_accept(args: argparse.Namespace) -> int:
    return _emit(ops.continuity_accept(args.project, args.clip_id, args.word_index, args.kind))


def _cmd_continuity_reject(args: argparse.Namespace) -> int:
    return _emit(ops.continuity_reject(args.project, args.clip_id, args.word_index, args.kind))


def _cmd_continuity_ls(args: argparse.Namespace) -> int:
    return _emit(ops.continuity_ls(args.project))


def _cmd_reframe_sheet(args: argparse.Namespace) -> int:
    moments = [float(part) for part in args.moments.split(",")] if args.moments else None
    return _emit(
        ops.reframe_sheet(
            args.project,
            out=args.out,
            moments=moments,
            extremes=args.extremes,
            page=args.page,
            per_page=args.per_page,
        )
    )


def _cmd_synopsis(args: argparse.Namespace) -> int:
    return _emit(ops.synopsis(args.project, args.clip_id, args.text, clear=args.clear))


def _cmd_events(args: argparse.Namespace) -> int:
    return _emit(
        ops.events(
            args.project,
            args.clip_id,
            source=args.source,
            name=args.name,
            origin=args.origin,
            offset=args.offset,
            at=args.at,
            event=args.event,
            clear=args.clear,
            plan=args.plan,
        )
    )


def _cmd_broll_brief(args: argparse.Namespace) -> int:
    return _emit(ops.broll_brief(args.project, fps=args.fps))


def _cmd_verify(args: argparse.Namespace) -> int:
    return _emit(
        ops.verify(
            args.project,
            args.render,
            clip_id=args.clip_id,
            transcript_path=args.transcript_path,
            model=args.model,
            language=args.language,
            windowed=args.windowed,
            window=args.window,
            overlap=args.overlap,
        )
    )


def _cmd_frames(args: argparse.Namespace) -> int:
    return _emit(ops.check_frames(args.project, args.target, fps=args.fps))


def _cmd_film_check(args: argparse.Namespace) -> int:
    return _emit(
        ops.film_check(args.project, args.reference, reset=args.reset, plan=args.plan)
    )


def _cmd_finish_check(args: argparse.Namespace) -> int:
    return _emit(
        ops.finish_check(
            args.project,
            args.final,
            holds=args.holds,
            prepend_seconds=args.prepend_seconds,
            fps=args.fps,
            duration_tolerance=args.duration_tolerance,
            pix_th=args.pix_th,
            black_min_duration=args.black_min_duration,
            windowed_model=args.windowed_model,
            window=args.window,
            overlap=args.overlap,
            recheck_pad=args.recheck_pad,
            language=args.language,
            clip_id=args.clip_id,
            transcript_path=args.transcript_path,
        )
    )


def _cmd_import_edit(args: argparse.Namespace) -> int:
    return _emit(
        ops.import_edit(args.project, args.document, clip_id=args.clip_id, plan=args.plan)
    )


def _cmd_black(args: argparse.Namespace) -> int:
    return _emit(
        ops.check_black(
            args.project,
            args.target,
            fps=args.fps,
            pix_th=args.pix_th,
            min_duration=args.min_duration,
        )
    )


def _cmd_spots(args: argparse.Namespace) -> int:
    return _emit(
        ops.spot_frames(args.project, args.target, count=args.count, times=args.times, fps=args.fps)
    )


def _cmd_attenuate(args: argparse.Namespace) -> int:
    return _emit(
        ops.attenuate_noises(
            args.project,
            args.clip_id,
            db=args.db,
            max_event_seconds=args.max_event_seconds,
            max_gap_seconds=args.max_gap_seconds,
            pad=args.pad,
            confirm_suspect=args.confirm_suspect,
            plan=args.plan,
        )
    )


def _cmd_proxy(args: argparse.Namespace) -> int:
    return _emit(ops.proxy_transcode(args.project, args.clip_id, force=args.force))


def _cmd_speech_overlap(args: argparse.Namespace) -> int:
    return _emit(
        ops.speech_overlap(
            args.project,
            args.clip_id,
            at=args.at,
            clip_in=args.clip_in,
            clip_out=args.clip_out,
            vo_clip_id=args.vo_clip_id,
            max_gap=args.max_gap,
            min_seam=args.min_seam,
            cap=args.cap,
            clip_evidence=args.clip_evidence,
        )
    )


def _cmd_export(args: argparse.Namespace) -> int:
    fmt = None if args.render else args.export_format
    return _emit(
        ops.export(
            args.project,
            args.output,
            export_format=fmt,
            fps=args.fps,
            preset=args.preset,
            resolution=args.resolution,
            loudness=args.loudness,
            true_peak=args.true_peak,
        )
    )


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Print the dependency report, and exit non-zero when something required is missing.

    The exit code is the one thing here that is not report-only: `proofcut
    doctor` is what a setup script or a CI step would gate on, and a command
    that always exits 0 cannot be gated on. Optional capabilities never move
    it — they gate a feature, not the install.
    """
    from proofcut import doctor as doc

    payload = ops.doctor()
    if args.json:
        _emit(payload)
    else:
        print(doc.render(payload))
    return 0 if payload["ok"] else 1


def _confirm(question: str, args: argparse.Namespace) -> bool:
    """`--yes`, or a person at a terminal saying y. Never a silent yes from a pipe."""
    if args.yes:
        return True
    if not sys.stdin.isatty():
        raise ProjectError(f"{question} There is no terminal to answer on: pass --yes.")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _cmd_setup(args: argparse.Namespace) -> int:
    """Install what doctor reports missing, or remove what setup installed.

    Exit 0 when doctor reports everything required afterwards (or, for
    `--plan`, already), 1 otherwise — the gate doctor's own exit code is.
    """
    from proofcut import install

    if args.uninstall:
        steps = install.uninstall_plan()
        if not steps["recorded"]:
            print(f"Nothing to remove: setup has installed nothing here ({steps['root']}).")
            return 0
        if args.plan:
            _emit(steps)
            return 0
        print("Will remove:")
        for name, entry in steps["pieces"].items():
            parts = [entry["dir"], *entry["links"]]
            if entry["uv_tool"]:
                parts.append(f"uv tool {entry['uv_tool']}")
            print(f"  {name}: " + ", ".join(p for p in parts if p))
        print(f"  and {steps['root']}")
        if not _confirm("Remove them?", args):
            return 1
        result = install.uninstall()
        if args.json:
            _emit(result)
        else:
            print("\n".join(["Removed:", *(f"  {item}" for item in result["removed"])]))
        return 0

    try:
        steps = install.plan()
    except install.InstallError as exc:
        raise ProjectError(str(exc)) from exc
    if args.json and args.plan:
        _emit(steps)
    else:
        print(install.render_plan(steps))
    if args.plan or not steps["pieces"]:
        return 0 if not steps["pieces"] and not steps["unavailable"] else 1
    if not _confirm(f"Download and install about {install._mb(steps['bytes'])}?", args):
        return 1
    result = install.install(steps, say=lambda line: print(line, flush=True))
    if args.json:
        _emit(result)
    else:
        print(install.render_result(result))
    return 0 if result["ok"] else 1


def _cmd_brief(args: argparse.Namespace) -> int:
    """The prompt text itself, not JSON: it is for pasting into an agent.

    `-C` names the project when typed, the prompts' own `project` argument.
    """
    from pathlib import Path

    from proofcut import briefs

    project = str(Path(args.project).resolve()) if args.project_given else None
    if args.brief == "review":
        text = briefs.review(render=args.render, project=project)
    elif not args.media:
        raise ProjectError(f"`proofcut brief {args.brief}` needs the material folder")
    elif args.brief == "cut":
        text = briefs.cut(args.media, output=args.output, length=args.length, project=project)
    else:
        text = briefs.film(
            args.media,
            output=args.output,
            length=args.length,
            end_card=args.end_card,
            loudness=args.loudness,
            project=project,
        )
    print(text, end="")
    return 0


def _cmd_ping(_args: argparse.Namespace) -> int:
    from proofcut.server import ping

    return _emit(ping())


def _cmd_unlock(args: argparse.Namespace) -> int:
    return _emit(projectlock.unlock(Path(args.project).resolve(), force=args.force))


def _cmd_mcp(args: argparse.Namespace) -> int:
    from proofcut.server import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, serve

    # `-C` binds the server to one project, and is honoured only when it was
    # actually typed: `main()` defaults it to ".", so binding unconditionally
    # would pin a globally-configured `proofcut mcp` to whatever directory its
    # client happened to launch from. Unbound is the general-client default;
    # bound is what the web UI's agent panel spawns (`webui.py`'s generated
    # MCP config), and what confines that agent to the project it was opened
    # on rather than to proofcut's ops in general. Untyped, `serve` still binds
    # when it is started inside a project (docs/plans/MCP.md § Step 8) — that
    # is a directory that says it is one, not whatever the client stood in.
    serve(
        root=args.project if args.project_given else None,
        transport=args.transport,
        host=args.host if args.host is not None else DEFAULT_HTTP_HOST,
        port=args.port if args.port is not None else DEFAULT_HTTP_PORT,
        allow_remote=args.allow_remote,
        allow_remote_hosts=args.allow_remote_hosts,
    )
    return 0


_COMMANDS = {
    "doctor": _cmd_doctor,
    "setup": _cmd_setup,
    "init": _cmd_init,
    "info": _cmd_info,
    "migrate": _cmd_migrate,
    "import": _cmd_import,
    "role": _cmd_role,
    "clip-rm": _cmd_clip_rm,
    "list-media": _cmd_list_media,
    "attach-transcript": _cmd_attach_transcript,
    "transcribe": _cmd_transcribe,
    "hear": _cmd_hear,
    "transcript": _cmd_transcript,
    "transcript-checks": _cmd_transcript_checks,
    "resolve": _cmd_resolve,
    "attribute-speakers": _cmd_attribute_speakers,
    "describe": _cmd_describe,
    "describe-ls": _cmd_describe_ls,
    "card": _cmd_card,
    "graphic": _cmd_graphic,
    "image": _cmd_image,
    "caption-look": _cmd_caption_look,
    "pack": _cmd_pack,
    "cue": _cmd_cue,
    "unspoken": _cmd_unspoken,
    "lexicon": _cmd_lexicon,
    "caption-span": _cmd_caption_span,
    "shots": _cmd_shots,
    "seed": _cmd_seed,
    "cut": _cmd_cut,
    "cut-at": _cmd_cut_at,
    "restore": _cmd_restore,
    "locate": _cmd_locate,
    "status": _cmd_status,
    "view": _cmd_view,
    "assets": _cmd_assets,
    "properties": _cmd_properties,
    "finish-report": _cmd_finish_report,
    "waveform": _cmd_waveform,
    "thumbnail": _cmd_thumbnail,
    "contact-sheet": _cmd_contact_sheet,
    "shot-sheet": _cmd_shot_sheet,
    "footage-sheet": _cmd_footage_sheet,
    "preview": _cmd_preview,
    "web": _cmd_web,
    "open": _cmd_open,
    "undo": _cmd_undo,
    "changes": _cmd_changes,
    "captions": _cmd_captions,
    "caption-view": _cmd_caption_view,
    "caption-style": _cmd_caption_style,
    "canvas": _cmd_canvas,
    "fonts": _cmd_fonts,
    "head": _cmd_head,
    "tail": _cmd_tail,
    "music": _cmd_music,
    "vo-extend": _cmd_vo_extend,
    "vo-synth": _cmd_vo_synth,
    "overlay": _cmd_overlay,
    "retime": _cmd_retime,
    "inset": _cmd_inset,
    "follow": _cmd_follow,
    "dissolve": _cmd_dissolve,
    "sound": _cmd_sound,
    "hold": _cmd_hold,
    "reel": _cmd_reel,
    "review": _cmd_review,
    "reframe": _cmd_reframe,
    "reframe-detect": _cmd_reframe_detect,
    "reframe-coverage": _cmd_reframe_coverage,
    "continuity-check": _cmd_continuity_check,
    "continuity-accept": _cmd_continuity_accept,
    "continuity-reject": _cmd_continuity_reject,
    "continuity-ls": _cmd_continuity_ls,
    "reframe-sheet": _cmd_reframe_sheet,
    "synopsis": _cmd_synopsis,
    "events": _cmd_events,
    "broll-brief": _cmd_broll_brief,
    "verify": _cmd_verify,
    "frames": _cmd_frames,
    "film-check": _cmd_film_check,
    "finish-check": _cmd_finish_check,
    "import-edit": _cmd_import_edit,
    "black": _cmd_black,
    "spots": _cmd_spots,
    "attenuate": _cmd_attenuate,
    "proxy": _cmd_proxy,
    "speech-overlap": _cmd_speech_overlap,
    "export": _cmd_export,
    "ping": _cmd_ping,
    "brief": _cmd_brief,
    "mcp": _cmd_mcp,
    "unlock": _cmd_unlock,
}

#: Commands the lock warning skips: the server that takes the lock, the
#: window whose own edits a person makes knowingly, and `unlock` itself.
_NO_LOCK_WARNING = frozenset({"mcp", "web", "unlock", "review", "setup", "doctor"})


def _project_bytes(root: Path) -> tuple[bytes | None, ...]:
    out: list[bytes | None] = []
    for name in (MANIFEST_NAME, TIMELINE_NAME):
        try:
            out.append((root / name).read_bytes())
        except OSError:
            out.append(None)
    return tuple(out)


def _warn_if_held(args: argparse.Namespace) -> Callable[[], None]:
    """Warn, and proceed, when a one-shot command changes a project another
    live session holds (Tyler, 2026-09-22). Judged by the project's own bytes
    before and after, so a read never warns and no list of mutating commands
    has to be kept in step with the parser."""
    if args.command in _NO_LOCK_WARNING:
        return lambda: None
    root = Path(args.project).resolve()
    held = projectlock.holder(root) if projectlock.lock_dir(root).is_dir() else None
    if held is None or held["stale"] or held["mine"]:
        return lambda: None
    before = _project_bytes(root)

    def after() -> None:
        if _project_bytes(root) != before:
            print(
                f"proofcut: warning: an agent session holds this project (pid "
                f"{held['pid']}, {held['command']}, since {held['started']}); this "
                "command changed it underneath that session.",
                file=sys.stderr,
            )

    return after

#: Every failure proofcut raises deliberately. Anything else is a bug and should
#: keep its traceback rather than be flattened into a one-line message.
_EXPECTED = (
    ProjectError,
    MediaError,
    TranscriptError,
    TimelineError,
    AutoEditorError,
    captions.CaptionError,
    ASRError,
    # The vision model is missing, or failed on a clip — a message naming
    # which interpreter was looked for, not a traceback.
    DescribeError,
    # Same shape for the voice synthesiser: no interpreter, an incomplete
    # voice, or a worker that died.
    tts.TTSError,
    VerifyError,
    PictureError,
    EnergyError,
    GraphicsError,
    # `plan_picture` refuses a shot longer than the asset it points at, and
    # that refusal fired for real on the Scream assembly (HISTORY.md
    # § Rendering through `melt`) — it is a message to read, not a traceback.
    MLTError,
    # A pack file that does not resolve — an unknown top-level key, a font
    # role with no fallback stack, and the rest of `pack.py`'s own refusals.
    PackError,
    # `finish.loudness`/`hold_seams` cannot decode or measure `final` — a
    # message naming the file, not a traceback.
    FinishError,
)


def _utf8_output() -> None:
    """Write UTF-8 to any standard stream whose own encoding cannot carry ✓.

    Windows encodes a *piped* stdout as the ANSI code page (cp1252), which has
    no byte for ✓ or ✗, so `proofcut doctor > out.txt` — and CI's doctor step, and
    the paste LAUNCH.md step 2 asks a tester for — died with
    `UnicodeEncodeError` before printing a line. Replacing the glyphs would
    lose the one thing a paste is read for; UTF-8 is what a CI log, a file and
    a paste all decode. A console, and every stream already able to encode the
    marks, is left exactly as it was.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None) or "ascii"
        try:
            "✓✗–".encode(encoding)
        except (UnicodeEncodeError, LookupError):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    args = _build_parser().parse_args(argv)
    # `-C` defaults here rather than in argparse because `init` is the one
    # subcommand whose directory is an *argument* rather than a lookup, so it
    # alone needs to know whether `-C` was actually typed.
    args.project_given = args.project is not None
    if args.project is None:
        args.project = "."
    try:
        warn = _warn_if_held(args)
        try:
            return _COMMANDS[args.command](args)
        finally:
            warn()
    except _EXPECTED as exc:
        print(f"proofcut: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # Windows' 206 is a person's folder being too deep, not a bug to trace
        # — `Project.create` refuses the common case up front, and this is the
        # one line for a clip id or render name that spends the headroom later.
        message = path_too_long(exc)
        if message is None:
            raise
        print(f"proofcut: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
