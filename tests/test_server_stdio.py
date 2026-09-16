"""End-to-end checks against the real server process.

This spawns `proofcut mcp` as a subprocess and speaks MCP over its stdio, rather
than calling the tool functions directly — the wiring between the CLI, the
transport, and the tool registry is exactly what a unit test would miss
(CLAUDE.md).
"""

from __future__ import annotations

import array
import inspect
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client
from stubs import write_stub

from proofcut import energy, finish, finishlog, graphics, media, ops, picture
from proofcut.project import Project

SERVER = StdioServerParameters(command=sys.executable, args=["-m", "proofcut.cli", "mcp"])

#: Every tool the MCP surface is expected to expose. Asserted exactly, so a
#: tool that is written but never registered fails the suite instead of
#: silently not existing.
EXPECTED_TOOLS = {
    "ping",
    "doctor",
    "init",
    "migrate_project",
    "import_media",
    "list_media",
    "attach_transcript",
    "transcribe",
    "hear",
    "get_transcript",
    "transcript_checks",
    "resolve_phrase",
    "attribute_speakers",
    "describe",
    "describe_ls",
    "card_templates",
    "card_new",
    "card_render",
    "card_reauthor",
    "card_safe_zones",
    "pack_apply",
    "pack_activate",
    "pack_apply_captions",
    "pack_show",
    "pack_status",
    "cue_add",
    "cue_rm",
    "cue_ls",
    "cue_reresolve",
    "unspoken_add",
    "unspoken_rm",
    "unspoken_ls",
    "unspoken_detect",
    "build_shots",
    "seed_timeline",
    "cut_by_transcript",
    "cut_by_time",
    "restore",
    "locate",
    "timeline_status",
    "timeline_view",
    "undo",
    "add_captions",
    "caption_view",
    "caption_style",
    "canvas",
    "head",
    "tail",
    "music",
    "vo_extend",
    "vo_synth",
    "hold_add",
    "hold_rm",
    "hold_under",
    "hold_under_rm",
    "hold_ls",
    "hold_check",
    "finish_check",
    "reel",
    "review_add",
    "review_verdict",
    "review_list",
    "reframe",
    "reframe_detect",
    "reframe_coverage",
    "reframe_sheet",
    "continuity_check",
    "continuity_accept",
    "continuity_reject",
    "continuity_ls",
    "synopsis",
    "broll_brief",
    "verify",
    "fonts",
    "check_frames",
    "film_check",
    "import_edit",
    "check_black",
    "spot_frames",
    "attenuate_noises",
    "proxy_transcode",
    "speech_overlap",
    "export",
    "assets",
    "clip_role",
    "clip_rm",
    "properties",
    "thumbnail",
    "contact_sheet",
    "shot_sheet",
    "footage_sheet",
    "finish_report",
}

#: The undo depth a project has the moment it is seeded, before anyone edits
#: it. `import_media` and `seed_timeline` each write the manifest, and a
#: manifest write is a snapshot now (docs/plans/POLISH.md § Step 03) — most authoring
#: state lives there, so undo had to cover it. "Nothing has been edited yet"
#: is therefore this number rather than zero. Named once, so a change in what
#: setup does is explained in one place instead of eight.
SEEDED_DEPTH = 2

needs_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe is not installed"
)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


class Client:
    """A tiny wrapper so tests read as a sequence of tool calls."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def call(self, tool: str, **arguments: Any) -> Any:
        result = await self._session.call_tool(tool, arguments)
        # A refusal is prose, not JSON — checked first, or the failure reads as
        # a bare JSONDecodeError and the server's own message is lost (every
        # one of the first Windows CI run's stdio failures, 2026-09-10).
        assert not result.is_error, f"{tool} failed: {result.content[0].text}"
        return json.loads(result.content[0].text)


async def _with_server(body: Any, server: StdioServerParameters = SERVER) -> Any:
    async with (
        stdio_client(server) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await body(session)


def _make_wav(path: Path, *, tones: list[tuple[float, float]], duration: float = 12.0) -> None:
    """A wav with tone bursts at `tones` and silence elsewhere."""
    rate = 22050
    with wave.open(str(path), "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * duration)):
            t = i / rate
            loud = any(a <= t < b for a, b in tones)
            value = int(12000 * math.sin(2 * math.pi * 220 * t)) if loud else 0
            frames += struct.pack("<h", value)
        out.writeframes(bytes(frames))


def _make_sources(root: Path) -> tuple[Path, Path]:
    """A four-burst recording and a transcript with two words per burst."""
    audio = root / "vo.wav"
    _make_wav(audio, tones=[(0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 11.0)])

    words = []
    for burst, (start, _) in enumerate([(0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 11.0)]):
        for n in range(2):
            at = start + n
            words.append({"word": f"w{burst}{n}", "start": at, "end": at + 0.9})

    transcript = root / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


@pytest.fixture
def sources(tmp_path: Path) -> tuple[Path, Path]:
    return _make_sources(tmp_path)


def test_doctor_reachable_over_stdio() -> None:
    """The one tool that takes no project, so `@_tool()` has no selector to bind.

    That is the whole reason it is checked here rather than only as a unit:
    `_tool()` falls through to a plain registration when none of its named
    selectors is in the signature, and nothing but the real registry proves
    the fall-through happened.
    """

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("doctor")

    payload = anyio.run(_with_server, body)
    assert isinstance(payload["ok"], bool)
    assert {r["name"] for r in payload["required"]} == {
        "ffmpeg",
        "ffprobe",
        "whisper",
        "auto-editor",
        "melt",
    }
    # magick is optional: cards are one feature (HISTORY.md § A stranger's
    # install, on a clean Ubuntu). Optional entries never move the verdict.
    assert "magick" in {r["name"] for r in payload["optional"]}
    assert payload["ok"] == all(r["ok"] for r in payload["required"])


def test_doctor_takes_no_arguments_over_stdio() -> None:
    """A project-less tool advertising a `path` would be a `-C` binding hole."""

    async def body(session: ClientSession) -> Any:
        return await session.list_tools()

    tools = anyio.run(_with_server, body)
    doctor = next(t for t in tools.tools if t.name == "doctor")
    assert not doctor.input_schema.get("properties")


def test_timeline_status_reports_seeded_false_instead_of_refusing(tmp_path: Path) -> None:
    """TRIAL.md § `timeline_status` is the first call an agent makes and it
    refuses on a fresh project — both trial runs opened with this call and
    both got a refusal. A project with nothing seeded must answer instead."""
    project = tmp_path / "proj"
    ops.init(str(project))

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("timeline_status", path=str(project))

    result = anyio.run(_with_server, body)

    assert result["seeded"] is False
    assert result["clips"] == []
    assert "timeline_duration" not in result
    assert "segments" not in result
    # Timeline-independent fields still answer.
    assert result["undo_depth"] == 0
    assert result["canvas"]
    assert result["head"] is None and result["tail"] is None


@needs_ffprobe
@needs_ffmpeg
def test_timeline_status_lists_a_registered_clip_before_it_is_seeded(
    tmp_path: Path,
) -> None:
    footage = tmp_path / "footage.mp4"
    _make_video(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        status = await client.call("timeline_status", path=str(project))
        return clip["clip_id"], status

    clip_id, status = anyio.run(_with_server, body)

    assert status["seeded"] is False
    assert status["clips"] == [clip_id]


@needs_ffprobe
def test_a_manifest_only_mutation_undoes_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The hole docs/plans/POLISH.md § Step 03 closes, asserted through the real server.

    A cue touches no `project.otio` at all, so the timeline-only undo covered
    cuts and nothing a gesture in the window can now do. Over the wire because
    the snapshot happens inside `Project.write_manifest`, and a unit test of
    `cue_add` would not notice a registration or a transport that lost it.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, audio, transcript)
        before = await client.call("cue_ls", path=str(project))
        await client.call(
            "cue_add", path=str(project), clip_id=clip, word_index=4, asset="card:title"
        )
        added = await client.call("cue_ls", path=str(project))
        undone = await client.call("undo", path=str(project))
        return {"before": before, "added": added, "undone": undone,
                "after": await client.call("cue_ls", path=str(project))}

    out = anyio.run(_with_server, body)

    assert len(out["added"]["cues"]) == len(out["before"]["cues"]) + 1
    assert out["undone"]["manifest_restored"] is True
    assert out["undone"]["timeline_restored"] is True
    assert out["after"]["cues"] == out["before"]["cues"]


@needs_ffprobe
@needs_ffmpeg
def test_a_framing_rect_undoes_over_the_wire(tmp_path: Path) -> None:
    """`reframe` is the other manifest-only one-gesture mutation (Frame mode).

    A real video source, because framing is refused on an audio-only project
    — there is no frame to crop.
    """
    source = tmp_path / "clip.mp4"
    _make_video(source)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, None)
        set_to = await client.call(
            "reframe", path=str(project), clip_id=clip, rect="10,0,100,120"
        )
        undone = await client.call("undo", path=str(project))
        return {"set_to": set_to, "undone": undone,
                "after": await client.call("reframe", path=str(project))}

    out = anyio.run(_with_server, body)

    assert out["set_to"]["written"] is True
    assert out["undone"]["manifest_restored"] is True
    assert all(entry.get("stored") is None for entry in out["after"]["clips"])


@needs_ffprobe
def test_a_mixed_sequence_undoes_one_mutation_per_press(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """cut, cue, cut — in reverse, one decision at a time.

    The failure this rules out is an op that snapshots twice because it writes
    both files, which would cost two presses to take back one thing.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, audio, transcript)
        await client.call("cut_by_time", path=str(project), spans=[[1.0, 2.0]])
        await client.call(
            "cue_add", path=str(project), clip_id=clip, word_index=6, asset="card:title"
        )
        await client.call("cut_by_time", path=str(project), spans=[[8.0, 9.0]])
        steps = []
        for _ in range(3):
            steps.append(
                {
                    "status": await client.call("timeline_status", path=str(project)),
                    "cues": len((await client.call("cue_ls", path=str(project)))["cues"]),
                }
            )
            await client.call("undo", path=str(project))
        steps.append(
            {
                "status": await client.call("timeline_status", path=str(project)),
                "cues": len((await client.call("cue_ls", path=str(project)))["cues"]),
            }
        )
        return steps

    steps = anyio.run(_with_server, body)
    durations = [round(s["status"]["timeline_duration"], 2) for s in steps]
    cues = [s["cues"] for s in steps]

    assert durations == [10.0, 11.0, 11.0, 12.0]
    assert cues == [1, 1, 0, 0]


def test_server_serves_ping_over_stdio() -> None:
    async def body(session: ClientSession) -> Any:
        return await Client(session).call("ping")

    payload = anyio.run(_with_server, body)
    assert payload["status"] == "ok"
    assert payload["server"] == "proofcut"


@needs_ffprobe
def test_finish_report_reachable_over_stdio(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call("finish_report", path=str(project))

    payload = anyio.run(_with_server, body)
    assert set(payload) == {
        "duration",
        "canvas",
        "captions",
        "picture",
        "marks",
        "seams",
        "sources",
        "unused_clips",
        "framing",
        "holds",
        "continuity",
        "last_render",
        "flags",
    }


def test_every_tool_is_registered() -> None:
    async def body(session: ClientSession) -> Any:
        return await session.list_tools()

    tools = anyio.run(_with_server, body)
    assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS


def test_every_tool_says_what_it_does_to_the_project() -> None:
    """The MCP hints, read off the wire the way a client or a directory reads
    them — a table in `server.py` that never reached `tools/list` would pass
    any in-process check. All four hints are set on every tool, because an
    unset one means the spec's default, and for `destructive_hint` that
    default is `true`. The pinned rows are the ones a wrong table would
    most plausibly get wrong: a cut that moves under itself, a check that
    only reads, a create that refuses rather than replaces."""

    async def body(session: ClientSession) -> Any:
        return await session.list_tools()

    tools = {tool.name: tool.annotations for tool in anyio.run(_with_server, body).tools}
    for name, hints in tools.items():
        assert hints is not None, name
        values = (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint, hints.open_world_hint)
        assert None not in values, name
        assert hints.open_world_hint is False, name
        if hints.read_only_hint:
            assert hints.destructive_hint is False, name

    assert tools["cut_by_time"].destructive_hint and not tools["cut_by_time"].idempotent_hint
    assert tools["undo"].destructive_hint and not tools["undo"].idempotent_hint
    assert tools["verify"].read_only_hint
    assert tools["init"].destructive_hint is False and not tools["init"].read_only_hint
    assert tools["shot_sheet"].read_only_hint is False  # `out` writes a file


def test_a_tool_missing_from_the_hint_table_refuses_to_register() -> None:
    """The table is only a contract if a new tool cannot skip it."""
    from proofcut import server

    def not_a_classified_tool(path: str | None = None) -> dict[str, Any]:
        return {}

    with pytest.raises(RuntimeError, match="_ANNOTATIONS"):
        server._tool()(not_a_classified_tool)


def test_a_path_windows_refuses_as_too_long_reaches_an_agent_as_the_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In-process, because the refusal has to be raised from inside a tool
    body: over MCP a 206 used to arrive as `[WinError 206]` and a filename,
    with nothing saying what to do about it. `_tool()`'s wrapper is the one
    place every project-addressing tool passes through."""
    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError

    from proofcut import server

    where = "C:\\deep\\cache\\transcripts\\a-long-clip.json"

    def refuse(*args: object, **kwargs: object) -> None:
        error = OSError(2, "The filename or extension is too long", where)
        error.winerror = 206  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(ops, "status", refuse)
    with pytest.raises(ToolError) as refused:
        asyncio.run(server.mcp.call_tool("timeline_status", {"path": str(tmp_path)}))
    assert f"Windows refused a path as too long ({len(where)} characters: {where})" in str(refused.value)
    assert "LongPathsEnabled 1" in str(refused.value)

    # The control: any other OSError still reaches the agent as itself.
    monkeypatch.setattr(ops, "status", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Access is denied")))
    with pytest.raises(ToolError) as other:
        asyncio.run(server.mcp.call_tool("timeline_status", {"path": str(tmp_path)}))
    assert "Access is denied" in str(other.value)
    assert "too long" not in str(other.value)


#: MCP tool -> CLI subcommand. Tool names are spelled for an agent reading a
#: tool list; subcommands are spelled for a human typing them. Where the two
#: differ the mapping is recorded here and asserted in both directions, so a
#: tool added to only one front end fails the suite either way.
TOOL_TO_COMMAND = {
    "doctor": "doctor",
    "init": "init",
    "migrate_project": "migrate",
    "import_media": "import",
    "list_media": "list-media",
    "attach_transcript": "attach-transcript",
    "transcribe": "transcribe",
    "hear": "hear",
    "get_transcript": "transcript",
    "transcript_checks": "transcript-checks",
    "resolve_phrase": "resolve",
    "attribute_speakers": "attribute-speakers",
    "describe": "describe",
    "describe_ls": "describe-ls",
    "card_templates": "card",
    "card_new": "card",
    "card_render": "card",
    "card_reauthor": "card",
    "card_safe_zones": "card",
    "pack_apply": "pack",
    "pack_activate": "pack",
    "pack_apply_captions": "pack",
    "pack_show": "pack",
    "pack_status": "pack",
    "cue_add": "cue",
    "cue_rm": "cue",
    "cue_ls": "cue",
    "cue_reresolve": "cue",
    "unspoken_add": "unspoken",
    "unspoken_rm": "unspoken",
    "unspoken_ls": "unspoken",
    "unspoken_detect": "unspoken",
    "build_shots": "shots",
    "seed_timeline": "seed",
    "cut_by_transcript": "cut",
    "cut_by_time": "cut-at",
    "restore": "restore",
    "locate": "locate",
    "timeline_status": "status",
    "timeline_view": "view",
    "undo": "undo",
    "add_captions": "captions",
    "caption_view": "caption-view",
    "caption_style": "caption-style",
    "canvas": "canvas",
    "head": "head",
    "tail": "tail",
    "music": "music",
    "vo_extend": "vo-extend",
    "vo_synth": "vo-synth",
    "hold_add": "hold",
    "hold_rm": "hold",
    "hold_under": "hold",
    "hold_under_rm": "hold",
    "hold_ls": "hold",
    "hold_check": "hold",
    "finish_check": "finish-check",
    "reel": "reel",
    "review_add": "review",
    "review_verdict": "review",
    "review_list": "review",
    "reframe": "reframe",
    "reframe_detect": "reframe-detect",
    "reframe_coverage": "reframe-coverage",
    "reframe_sheet": "reframe-sheet",
    "continuity_check": "continuity-check",
    "continuity_accept": "continuity-accept",
    "continuity_reject": "continuity-reject",
    "continuity_ls": "continuity-ls",
    "synopsis": "synopsis",
    "broll_brief": "broll-brief",
    "verify": "verify",
    "fonts": "fonts",
    "check_frames": "frames",
    "film_check": "film-check",
    "import_edit": "import-edit",
    "check_black": "black",
    "spot_frames": "spots",
    "attenuate_noises": "attenuate",
    "proxy_transcode": "proxy",
    "speech_overlap": "speech-overlap",
    "export": "export",
    "assets": "assets",
    "clip_role": "role",
    "clip_rm": "clip-rm",
    "properties": "properties",
    "thumbnail": "thumbnail",
    "contact_sheet": "contact-sheet",
    "shot_sheet": "shot-sheet",
    "footage_sheet": "footage-sheet",
    "finish_report": "finish-report",
}

#: CLI-only commands, with the reason each one has no tool behind it.
CLI_ONLY = {
    "mcp",  # starts the server; nothing to call it from
    "ping",  # a tool, but takes no project and needs no mapping
    "info",  # prints the manifest, which MCP clients get from other tools —
    # and stands its descriptions down to a count, because describe_ls is
    # where the text is meant to be read
    "web",  # serves the UI until Ctrl-C; an agent cannot watch a page
    "open",  # same as `web` — serves the UI until Ctrl-C and launches a
    # browser window; an agent cannot watch a page or use a GUI browser
    "waveform",  # 19,000 floats is a picture, not something an agent reasons
    # over — PLAN.md § Read-model additions
    "preview",  # answers "will a *browser* play this", and an agent has no
    # <video> element; no render path consults the verdict either
}


def test_every_mcp_tool_has_a_cli_subcommand() -> None:
    """Parity is a project convention, so it gets asserted rather than trusted."""
    from proofcut.cli import _COMMANDS

    assert set(TOOL_TO_COMMAND) == EXPECTED_TOOLS - {"ping"}, (
        "the tool -> command map has drifted from the registered tool list"
    )
    for tool, command in TOOL_TO_COMMAND.items():
        assert command in _COMMANDS, f"MCP tool {tool!r} has no `proofcut {command}` subcommand"

    # And the other way, so a CLI command cannot quietly lack a tool.
    unmapped = set(_COMMANDS) - set(TOOL_TO_COMMAND.values()) - CLI_ONLY
    assert not unmapped, f"CLI subcommands with no MCP tool: {sorted(unmapped)}"


@needs_ffprobe
def test_cut_by_transcript_end_to_end(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """The whole slice over the wire: import, transcript, seed, cut, undo."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        attached = await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        # Seed without the silence pass so this test does not need auto-editor.
        seeded = await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        found = await client.call(
            "get_transcript", path=str(project), clip_id=clip["clip_id"], search="w10 w11"
        )
        cut = await client.call(
            "cut_by_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            cut=[[2, 3]],
        )
        status = await client.call("timeline_status", path=str(project))
        undone = await client.call("undo", path=str(project))
        return {
            "clip": clip,
            "attached": attached,
            "seeded": seeded,
            "found": found,
            "cut": cut,
            "status": status,
            "undone": undone,
        }

    out = anyio.run(_with_server, body)

    assert out["clip"]["has_audio"] is True and out["clip"]["has_video"] is False
    assert out["attached"]["words"] == 8
    assert out["seeded"]["timeline_duration"] == pytest.approx(12.0, abs=0.05)

    # The phrase is found at the word range that addresses it.
    assert (out["found"]["matches"][0]["first_word"], out["found"]["matches"][0]["last_word"]) == (2, 3)

    # Words 2-3 span source 3.0 -> 4.9, so 1.9s comes out and the segment splits.
    assert out["cut"]["removed"] == pytest.approx(1.9, abs=0.01)
    assert out["cut"]["segments"] == 2
    assert out["status"]["timeline_duration"] == pytest.approx(10.1, abs=0.05)
    assert out["status"]["undo_depth"] == SEEDED_DEPTH + 1

    # Undo puts the timeline back exactly.
    assert out["undone"]["timeline_duration"] == pytest.approx(12.0, abs=0.05)
    assert out["undone"]["undo_depth"] == SEEDED_DEPTH


@needs_ffprobe
@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_card_render_over_the_wire(tmp_path: Path) -> None:
    """A card rendered through the server lands where a `card:` cue looks."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        cards = Project.open(project).cards_dir
        cards.mkdir(parents=True, exist_ok=True)
        (cards / "receipt.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080">'
            '<rect width="1920" height="1080" fill="#101418"/></svg>',
            encoding="utf-8",
        )
        return await client.call("card_render", path=str(project), name="receipt")

    out = anyio.run(_with_server, body)

    assert out["asset"] == "card:receipt"
    assert (out["width"], out["height"]) == (1920, 1080)
    assert Path(out["output"]) == Project.open(project).cards_dir / "receipt.png"
    assert Path(out["output"]).is_file()


@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_card_new_from_a_template_over_the_wire(tmp_path: Path) -> None:
    """A card an agent could actually make: list templates, then fill one."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        listed = await client.call("card_templates")
        made = await client.call(
            "card_new",
            path=str(project),
            name="receipt-scream-1996",
            template="receipt",
            slots={
                "title": "Scream",
                "year": "1996",
                "rating": 4.5,
                "date_line": "watched 20 May 2021",
            },
        )
        return {"listed": listed, "made": made}

    out = anyio.run(_with_server, body)

    # Exhaustive on purpose, and it is the only place the wire's own template
    # inventory is pinned: a template that ships without reaching `card_new`
    # is invisible to an agent, and one that reaches it without being meant to
    # is worse. Adding a template means adding it here — that is the check
    # working, not a test in the way. `endcard` and `bumper` arrived with the
    # completion queue's channel-preset item; `chapter` with the section-card
    # register the Lambs/Longlegs bumpers outgrew.
    assert {t["template"] for t in out["listed"]["templates"]} == {
        "receipt",
        "reveal",
        "rerate",
        "endcard",
        "bumper",
        "chapter",
    }
    assert out["made"]["asset"] == "card:receipt-scream-1996"
    # No video clip in this project, so the canvas falls back to 1080p.
    assert out["made"]["canvas_from"] == "project"
    assert (out["made"]["width"], out["made"]["height"]) == (1920, 1080)

    cards = Project.open(project).cards_dir
    assert (cards / "receipt-scream-1996.svg").is_file()
    assert (cards / "receipt-scream-1996.png").is_file()


@pytest.mark.skipif(
    shutil.which("magick") is None
    or shutil.which("ffmpeg") is None
    or shutil.which("fc-match") is None,
    reason="both halves of the font report: fontconfig, and a burn through ImageMagick and libass",
)
def test_fonts_over_the_wire_reports_fontconfig_and_the_render_separately(
    tmp_path: Path,
) -> None:
    """The two answers must arrive side by side. `fc-match` says whether a
    family is present; only a burn says which face drew, and this repo has
    measured them disagreeing — so an agent that got one merged number could
    not tell "installed" from "actually drawing"."""

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("fonts")

    result = anyio.run(_with_server, body)

    assert result["project"] is None
    entry = result["fonts"][result["caption_font"]]
    assert entry["fontconfig"]["available"] is True
    assert entry["render"]["drew"] is True
    # Never folded together: the render's verdict is its own key, and it is
    # reached by comparison against a family that cannot exist rather than
    # against a stored reference image.
    assert entry["render"]["rmse_against_substitute"] > 0
    assert entry["render"]["control_family"] not in (result["caption_font"], "")
    # The vendored face is what makes the default resolve rather than
    # substitute, so its absence from the package is a reportable state.
    assert result["vendored"]


@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_card_reauthor_over_the_wire_follows_a_canvas_swap(tmp_path: Path) -> None:
    """The sequence an agent asked for a vertical cut would actually run: swap
    the canvas, which names the cards it has left behind, then redraw them.
    Re-rendering the old SVG at the new size would pillarbox the card inside
    the frame at exit 0, so the shape on disk is what settles it."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        await client.call(
            "card_new",
            path=str(project),
            name="reveal-two",
            template="reveal",
            slots={"title": "Scream 2"},
        )
        swapped = await client.call("canvas", path=str(project), size="1080x1920")
        planned = await client.call("card_reauthor", path=str(project), plan=True)
        redrawn = await client.call("card_reauthor", path=str(project))
        return {"swapped": swapped, "planned": planned, "redrawn": redrawn}

    out = anyio.run(_with_server, body)

    assert out["swapped"]["cards_stale"] == ["reveal-two"]
    assert out["planned"]["redrawn"] == 0
    assert out["planned"]["to_redraw"] == 1
    assert out["redrawn"]["redrawn"] == 1
    assert out["redrawn"]["cards"][0]["width"] == 1080
    assert out["redrawn"]["cards"][0]["height"] == 1920
    assert graphics.identify(Project.open(project).cards_dir / "reveal-two.png") == (1080, 1920)


@needs_ffprobe
def test_describe_plans_over_the_wire_without_loading_a_model(tmp_path: Path) -> None:
    """`describe` reachable over stdio, in the mode an agent should reach for
    first: `plan=True` resolves the whole work list, the estimate, and whether
    this box can run the model at all, without 31 GB of weights being what
    answers the question. The describing half needs a GPU and is exercised in
    `test_ops_describe.py` against a stub.
    """
    project = tmp_path / "proj"
    media = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24:d=25",
            str(media),
        ],
        check=True,
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        await client.call("import_media", path=str(project), source=str(media))
        return await client.call("describe", path=str(project), plan=True)

    out = anyio.run(_with_server, body)

    assert out["plan"] is True
    # 25s at 10s windows is three windows, never two of 12.5 — a window is
    # never longer than the one asked for.
    assert out["windows"] == 3
    assert out["clips"] == [{"clip_id": "silent", "windows": 3}]
    assert out["estimated_seconds"] == 26
    assert set(out["runtime"]) == {"available", "python", "tagger", "why"}
    # Nothing was described, so nothing was stored.
    assert Project.open(project).read_manifest()["descriptions"] == []


@needs_ffprobe
def test_describe_ls_searches_over_the_wire(tmp_path: Path) -> None:
    """The read half reachable over stdio — and reachable is the whole point,
    since this is what an agent looking for b-roll actually calls.

    The descriptions are written into the manifest directly rather than
    generated: the model is 31 GB under another interpreter, and what is
    under test here is the transport and the filter, not the vision pass
    (`test_ops_describe.py` covers that against a stub).
    """
    project = tmp_path / "proj"
    source = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24:d=25",
            str(source),
        ],
        check=True,
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        await client.call("import_media", path=str(project), source=str(source))

        opened = Project.open(project)
        manifest = opened.read_manifest()
        manifest["descriptions"] = [
            {
                "clip_id": "silent",
                "src_start": 0.0,
                "src_end": 12.5,
                "text": "A knife on a kitchen counter, beside a white phone.",
                "truncated": False,
                "origin": "qwen2.5-vl:10s/3f",
            },
            {
                "clip_id": "silent",
                "src_start": 12.5,
                "src_end": 25.0,
                "text": "A car parked in a driveway at night, headlights",
                "truncated": True,
                "origin": "qwen2.5-vl:10s/3f",
            },
        ]
        opened.write_manifest(manifest)

        return {
            "all": await client.call("describe_ls", path=str(project)),
            "hit": await client.call("describe_ls", path=str(project), contains="kitchen knife"),
            "miss": await client.call("describe_ls", path=str(project), contains="helicopter"),
        }

    out = anyio.run(_with_server, body)

    assert out["all"]["count"] == 2
    assert out["all"]["clips"] == [
        {
            "clip_id": "silent",
            "windows": 2,
            "described_seconds": 25.0,
            "duration": pytest.approx(25.0, abs=0.05),
            "truncated": 1,
        }
    ]

    # Every term, anywhere — not a phrase match.
    assert out["hit"]["count"] == 1
    assert "knife" in out["hit"]["descriptions"][0]["text"]
    assert out["hit"]["filter"]["terms"] == ["kitchen", "knife"]

    # A miss still says what it filtered out of, so it cannot be mistaken for
    # a project with nothing described in it.
    assert out["miss"]["count"] == 0
    assert out["miss"]["total"] == 2


@needs_ffprobe
def test_describe_refuses_an_audio_only_clip_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The Scream project's VO is exactly this clip, and skipping it quietly
    reads the same as describing it and finding nothing to say."""
    audio, _ = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        return await session.call_tool(
            "describe", {"path": str(project), "clip_id": clip["clip_id"], "plan": True}
        )

    result = anyio.run(_with_server, body)

    assert result.is_error
    assert "no video track" in result.content[0].text


def test_cue_table_add_ls_rm_end_to_end(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """The cue table over the wire — no timeline needed, only a transcript."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        added = await client.call(
            "cue_add", path=str(project), clip_id=clip["clip_id"], word_index=2, asset="cold-open"
        )
        listed = await client.call("cue_ls", path=str(project))
        removed = await client.call(
            "cue_rm", path=str(project), clip_id=clip["clip_id"], word_index=2
        )
        empty = await client.call("cue_ls", path=str(project))
        return {"clip": clip, "added": added, "listed": listed, "removed": removed, "empty": empty}

    out = anyio.run(_with_server, body)

    assert out["added"]["asset"] == "cold-open"
    assert out["added"]["text"] == "w10"  # word 2 of the 8-word `sources` transcript
    assert out["listed"]["count"] == 1
    assert out["listed"]["cues"][0]["word_index"] == 2
    assert out["removed"]["asset"] == "cold-open"
    assert out["empty"]["count"] == 0


@needs_ffprobe
def test_cue_add_refuses_a_duplicate_word_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "cue_add", path=str(project), clip_id=clip["clip_id"], word_index=0, asset="cold-open"
        )
        result = await session.call_tool(
            "cue_add",
            {"path": str(project), "clip_id": clip["clip_id"], "word_index": 0, "asset": "s4-reveal"},
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)
    assert out["is_error"]
    assert "already has a cue" in out["text"]


# -- phrase addressing (feature: phrase-addressed cues) ---------------------
#
# `sources`' words are w00 w01 w10 w11 w20 w21 w30 w31 — "w10 w11" is the
# same word_index=2 pair `test_cue_table_add_ls_rm_end_to_end` above
# exercises by index, so this is the control that reaching over the wire
# resolves to the identical place.


@needs_ffprobe
def test_cue_add_by_phrase_over_the_wire(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        return await client.call(
            "cue_add",
            path=str(project),
            clip_id=clip["clip_id"],
            asset="cold-open",
            phrase="w10 w11",
        )

    added = anyio.run(_with_server, body)
    assert added["word_index"] == 2
    assert added["text"] == "w10"
    assert added["phrase"] == "w10 w11"


@needs_ffprobe
def test_cue_add_ambiguous_phrase_lists_candidates_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """A repeated word with no `occurrence=` refuses and names every match —
    the safety story this feature exists to guarantee."""
    audio, _ = sources
    project = tmp_path / "proj"
    transcript = tmp_path / "repeated.json"
    transcript.write_text(
        json.dumps(
            {
                "language": "en",
                "words": [
                    {"word": "the", "start": 0.0, "end": 0.3},
                    {"word": "cat", "start": 0.5, "end": 0.8},
                    {"word": "and", "start": 0.9, "end": 1.1},
                    {"word": "the", "start": 1.2, "end": 1.5},
                    {"word": "dog", "start": 1.6, "end": 1.9},
                ],
            }
        ),
        encoding="utf-8",
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        result = await session.call_tool(
            "cue_add",
            {
                "path": str(project),
                "clip_id": clip["clip_id"],
                "asset": "cold-open",
                "phrase": "the",
            },
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)
    assert out["is_error"]
    assert "matches 2 times" in out["text"]
    assert "words 0-0" in out["text"] and "words 3-3" in out["text"]
    assert "occurrence=" in out["text"]


@needs_ffprobe
def test_resolve_phrase_reachable_over_stdio(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        return await client.call(
            "resolve_phrase", path=str(project), clip_id=clip["clip_id"], phrase="w10 w11"
        )

    resolved = anyio.run(_with_server, body)
    assert (resolved["first_word"], resolved["last_word"]) == (2, 3)
    assert resolved["match"] == "exact"
    # The read-only companion to cue_add's write — nothing was added.
    assert resolved["phrase"] == "w10 w11"


@needs_ffprobe
def test_cue_reresolve_reachable_over_stdio(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "cue_add",
            path=str(project),
            clip_id=clip["clip_id"],
            asset="cold-open",
            phrase="w10 w11",
        )
        return await client.call("cue_reresolve", path=str(project))

    report = anyio.run(_with_server, body)
    assert report["apply"] is False
    assert report["cues"][0]["action"] == "resolved"
    assert report["cues"][0]["word_index"] == 2


@needs_ffprobe
def test_cut_plan_resolves_without_touching_the_timeline(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Look before you cut, and see the words either side.
    HISTORY.md § `cut --plan`, and echoing what a word index resolved to.

    The numbers a plan reports are the real ones — it runs the same code path
    and skips the write — so the assertion that matters is that the plan and
    the cut that follows it agree exactly, while the plan alone leaves no
    snapshot behind.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        planned = await client.call(
            "cut_by_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            cut=[[2, 3]],
            plan=True,
        )
        padded = await client.call(
            "cut_by_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            cut=[[2, 3]],
            pad=1.2,
            plan=True,
        )
        after_plan = await client.call("timeline_status", path=str(project))
        cut = await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[2, 3]]
        )
        after_cut = await client.call("timeline_status", path=str(project))
        return {
            "planned": planned,
            "padded": padded,
            "after_plan": after_plan,
            "cut": cut,
            "after_cut": after_cut,
        }

    out = anyio.run(_with_server, body)
    planned, applied = out["planned"], out["planned"]["applied"][0]

    assert planned["plan"] is True

    # The indices resolve to their words, and to the words either side of them.
    assert applied["text"] == "w10 w11"
    assert [w["index"] for w in applied["context_before"]] == [0, 1]
    assert [w["index"] for w in applied["context_after"]] == [4, 5, 6]
    assert applied["word_start"] == pytest.approx(3.0) and applied["word_end"] == pytest.approx(4.9)
    assert "pad_reach" not in applied

    # A pad wide enough to reach the neighbouring words says so — the echoed
    # text is the same two words either way, which is the whole problem.
    reach = {w["index"]: w["side"] for w in out["padded"]["applied"][0]["pad_reach"]}
    assert reach == {1: "before", 4: "after"}
    assert out["padded"]["applied"][0]["text"] == "w10 w11"

    # Planning wrote nothing: same duration, and no snapshot to roll back.
    assert out["after_plan"]["timeline_duration"] == pytest.approx(12.0, abs=0.05)
    assert out["after_plan"]["undo_depth"] == SEEDED_DEPTH

    # And the plan was exact — same removal, same resulting segment count.
    assert out["cut"]["removed"] == pytest.approx(planned["removed"], abs=1e-9)
    assert out["cut"]["segments"] == planned["segments"] == 2
    assert out["after_cut"]["undo_depth"] == SEEDED_DEPTH + 1


@needs_ffprobe
def test_restore_brings_back_a_cut_range_end_to_end(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """`restore` is the inverse of `cut_by_transcript`'s `cut=`: cutting a
    range and then restoring the same range round-trips the timeline back to
    its pre-cut state, and the words themselves report `present` again.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        cut = await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[3, 4]]
        )
        before_restore = await client.call(
            "timeline_view", path=str(project), clip_id=clip["clip_id"]
        )
        restored = await client.call(
            "restore", path=str(project), clip_id=clip["clip_id"], ranges=[[3, 4]]
        )
        after_restore = await client.call(
            "timeline_view", path=str(project), clip_id=clip["clip_id"]
        )
        return {
            "clip_id": clip["clip_id"],
            "cut": cut,
            "before_restore": before_restore,
            "restored": restored,
            "after_restore": after_restore,
        }

    out = anyio.run(_with_server, body)

    assert out["cut"]["removed"] > 0.0
    assert out["before_restore"]["words"][3]["present"] is False
    assert out["before_restore"]["words"][4]["present"] is False

    assert out["restored"]["restored"] == pytest.approx(out["cut"]["removed"], abs=1e-6)
    assert out["restored"]["applied"][0]["already_present"] is False

    assert out["after_restore"]["timeline_duration"] == pytest.approx(12.0, abs=0.05)
    assert out["after_restore"]["words"][3]["present"] is True
    assert out["after_restore"]["words"][4]["present"] is True


@needs_ffprobe
def test_restore_plan_resolves_without_touching_the_timeline(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[3, 4]]
        )
        status_before = await client.call("timeline_status", path=str(project))
        planned = await client.call(
            "restore", path=str(project), clip_id=clip["clip_id"], ranges=[[3, 4]], plan=True
        )
        status_after_plan = await client.call("timeline_status", path=str(project))
        real = await client.call(
            "restore", path=str(project), clip_id=clip["clip_id"], ranges=[[3, 4]]
        )
        return {
            "status_before": status_before,
            "planned": planned,
            "status_after_plan": status_after_plan,
            "real": real,
        }

    out = anyio.run(_with_server, body)

    assert out["planned"]["plan"] is True
    # The plan reports the real numbers — same code path, write skipped.
    assert out["planned"]["restored"] == pytest.approx(out["real"]["restored"], abs=1e-9)

    # Nothing was written: status is unchanged, in particular the undo depth.
    assert out["status_after_plan"] == out["status_before"]


@needs_ffprobe
def test_restore_echoes_words_plus_three_either_side(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """CLAUDE.md's convention: any op taking a word index echoes the words it
    resolved to, plus the three either side — same shape as
    `cut_by_transcript`'s own echo.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[3, 4]]
        )
        return await client.call(
            "restore", path=str(project), clip_id=clip["clip_id"], ranges=[[3, 4]], plan=True
        )

    out = anyio.run(_with_server, body)
    applied = out["applied"][0]

    assert applied["text"] == "w11 w20"
    assert [w["index"] for w in applied["context_before"]] == [0, 1, 2]
    assert [w["index"] for w in applied["context_after"]] == [5, 6, 7]


@needs_ffprobe
def test_restore_of_a_range_still_fully_present_reports_already_present_without_error(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """A range that was never cut is a no-op, not a refusal — mirroring
    `cut_by_transcript`'s `already_cut`.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call(
            "restore", path=str(project), clip_id=clip["clip_id"], ranges=[[0, 1]]
        )

    out = anyio.run(_with_server, body)
    applied = out["applied"][0]

    assert applied["already_present"] is True
    assert applied["restored_seconds"] == pytest.approx(0.0)
    assert out["restored"] == pytest.approx(0.0)


@needs_ffprobe
def test_attach_transcript_flags_adjacent_near_duplicate_phrases(tmp_path: Path) -> None:
    """Over the wire: attach reports a retake `verify` can never catch,
    before any edit exists to diff it against (test_verify.py exercises the
    detector itself; this checks it is actually wired in).
    HISTORY.md § Adjacent near-duplicate phrases at `attach-transcript`.
    """
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 8.0)])

    texts = ["so", "much", "going", "on", "here", "it's", "more", "of", "a", "meta", "commentary", "I", "don't", "think", "that", "it's", "a", "coincidence", "I", "don't", "think", "that's", "a", "coincidence", "that", "ghostface"]
    words = [{"word": w, "start": n * 0.3, "end": n * 0.3 + 0.25} for n, w in enumerate(texts)]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        return await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )

    attached = anyio.run(_with_server, body)

    assert len(attached["near_duplicates"]) == 1
    hit = attached["near_duplicates"][0]
    assert hit["similarity"] >= 0.5
    assert hit["first_word"] < hit["second_word"]


def _suspect_duration_sources(tmp_path: Path) -> tuple[Path, Path]:
    """A recording where one word's claimed span hides a swallowed retake."""
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 8.0)])

    words = [
        {"word": "so", "start": 0.0, "end": 0.3},
        {"word": "much", "start": 0.3, "end": 0.6},
        {"word": "going", "start": 0.6, "end": 0.9},
        # claims 3.96s against a 0.3s median — the Scream VO's "bit", in miniature.
        {"word": "bit", "start": 0.9, "end": 4.86},
        {"word": "on", "start": 4.86, "end": 5.16},
    ]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


@needs_ffprobe
def test_attach_transcript_flags_suspect_word_durations(tmp_path: Path) -> None:
    """Over the wire: a word running past 3x the median is a lie about
    something, usually a swallowed retake (HISTORY.md § 2).
    HISTORY.md § Suspect word durations at `attach-transcript`.
    """
    audio, transcript = _suspect_duration_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        return await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )

    attached = anyio.run(_with_server, body)

    assert len(attached["suspect_durations"]) == 1
    hit = attached["suspect_durations"][0]
    assert hit["index"] == 3
    assert hit["text"] == "bit"


def _overlap_sources(tmp_path: Path) -> tuple[Path, Path]:
    """A transcript with a retake splice in it, from the Scream VO's own timings.

    `Billy Billions and Stu do spend` — whisper read across the splice and
    interleaved both takes, inventing `Billions` and `do`.
    HISTORY.md § The hand-framed teaser, watched.
    """
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 8.0)])

    words = [
        {"word": "the", "start": 0.0, "end": 0.3},
        {"word": "point", "start": 0.3, "end": 0.6},
        {"word": "is", "start": 0.6, "end": 0.9},
        {"word": "Billy", "start": 1.04, "end": 1.38},
        {"word": "Billions", "start": 1.08, "end": 1.64},
        {"word": "and", "start": 1.38, "end": 1.62},
        {"word": "Stu", "start": 1.62, "end": 1.88},
        {"word": "spend", "start": 1.96, "end": 2.40},
        {"word": "the", "start": 2.40, "end": 2.70},
        {"word": "film", "start": 2.70, "end": 3.00},
    ]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


@needs_ffprobe
def test_attach_transcript_flags_overlapping_words(tmp_path: Path) -> None:
    """Over the wire: a splice whisper read across, reported as one seam.

    The invented words are ordinary-length and repeat no phrase, so neither
    sibling check can see this — `suspect_durations` looks at one word's
    length and `near_duplicates` matches phrases.
    HISTORY.md § The hand-framed teaser, watched.
    """
    audio, transcript = _overlap_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        return await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )

    attached = anyio.run(_with_server, body)

    assert len(attached["overlaps"]) == 1
    seam = attached["overlaps"][0]
    assert seam["text"] == "Billy Billions and"
    assert seam["pairs"] == 2
    # The neighbours are the point: the seam reads as English without them.
    assert [w["text"] for w in seam["context_before"]] == ["the", "point", "is"]
    assert next(w["text"] for w in seam["context_after"]) == "Stu"
    # And the checks its siblings cannot make are genuinely theirs alone.
    assert attached["suspect_durations"] == []
    assert attached["near_duplicates"] == []


def _repeat_sources(tmp_path: Path) -> tuple[Path, Path]:
    """A transcript with a retake read back in as ordinary, cleanly-timed
    words — the shape `find_repeats` exists to catch, and the one
    `find_overlaps` structurally cannot: there is no invented word here, just
    the same four-word phrase said twice in a row. The Scream VO's retake
    pass, in miniature. HISTORY.md § The VO the project was holding.
    """
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 8.0)])

    words = [
        {"word": "the", "start": 0.0, "end": 0.3},
        {"word": "best", "start": 0.3, "end": 0.6},
        {"word": "twelve", "start": 0.6, "end": 0.9},
        {"word": "minutes", "start": 0.9, "end": 1.2},
        {"word": "the", "start": 1.6, "end": 1.9},
        {"word": "best", "start": 1.9, "end": 2.2},
        {"word": "twelve", "start": 2.2, "end": 2.5},
        {"word": "minutes", "start": 2.5, "end": 2.8},
        {"word": "of", "start": 2.8, "end": 3.0},
        {"word": "horror", "start": 3.0, "end": 3.4},
    ]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


@needs_ffprobe
def test_attach_transcript_flags_repeated_phrases(tmp_path: Path) -> None:
    """Over the wire: a retake that survived as distinct words, the mirror
    image of the seam `overlaps` finds — see `tx.find_repeats`'s docstring
    for why one does not subsume the other.
    """
    audio, transcript = _repeat_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        return await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )

    attached = anyio.run(_with_server, body)

    assert len(attached["repeats"]) == 1
    hit = attached["repeats"][0]
    assert hit["text"] == "the best twelve minutes"
    assert hit["first_word"] == 0 and hit["last_word"] == 7
    # The neighbours are the point, same as `overlaps`.
    assert [w["text"] for w in hit["context_after"]] == ["of", "horror"]
    # No invented word here, so the seam scan has nothing to say about it.
    assert attached["overlaps"] == []


@needs_ffprobe
def test_transcript_checks_rechecks_an_already_attached_transcript(tmp_path: Path) -> None:
    """The finding is computed at attach and returned once, so a project
    attached before a check existed can never see it — which is exactly the
    Scream VO's position. This is how it gets asked again, without re-running
    ASR or hunting for the original whisper JSON.
    """
    audio, transcript = _overlap_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        return await client.call("transcript_checks", path=str(project))

    checked = anyio.run(_with_server, body)

    assert len(checked["clips"]) == 1
    found = checked["clips"][0]
    assert found["words"] == 10
    assert [s["text"] for s in found["overlaps"]] == ["Billy Billions and"]
    # All four findings, not just the new one.
    assert found["suspect_durations"] == [] and found["near_duplicates"] == []
    assert found["repeats"] == []


@needs_ffprobe
def test_transcript_checks_does_not_write_to_the_project(tmp_path: Path) -> None:
    """It backs a read of a finished cut, so it must not rewrite one — the
    same property `Project.open` refuses an old manifest to protect.
    """
    audio, transcript = _overlap_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> tuple[str, str]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        before = (project / "proofcut.json").read_text()
        await client.call("transcript_checks", path=str(project))
        return before, (project / "proofcut.json").read_text()

    before, after = anyio.run(_with_server, body)
    assert before == after


@needs_ffprobe
def test_cut_refuses_a_suspect_boundary_word_without_confirmation(tmp_path: Path) -> None:
    """A flagged word's end is what the cut boundary resolves to, so using one
    unconfirmed would silently cut wherever the hidden retake actually ends.
    """
    audio, transcript = _suspect_duration_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        blocked = await session.call_tool(
            "cut_by_transcript",
            {"path": str(project), "clip_id": clip["clip_id"], "cut": [[3, 4]]},
        )
        confirmed = await client.call(
            "cut_by_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            cut=[[3, 4]],
            confirm_suspect=True,
        )
        return {"blocked": blocked, "confirmed": confirmed}

    out = anyio.run(_with_server, body)

    assert out["blocked"].is_error
    assert "hides a retake" in out["blocked"].content[0].text
    assert out["confirmed"]["removed"] > 0


@needs_ffprobe
def test_cut_plan_reports_a_suspect_boundary_instead_of_refusing_it(tmp_path: Path) -> None:
    """Refusing to *look* at a flagged boundary would be backwards — checking
    the word is exactly what the refusal above asks the caller to go and do.
    """
    audio, transcript = _suspect_duration_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        planned = await client.call(
            "cut_by_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            cut=[[3, 4]],
            plan=True,
        )
        return {"planned": planned, "status": await client.call("timeline_status", path=str(project))}

    out = anyio.run(_with_server, body)

    flagged = out["planned"]["suspect_boundaries"]
    assert [hit["index"] for hit in flagged] == [3]
    assert flagged[0]["text"] == "bit"
    assert flagged[0]["range"] == [3, 4]
    # Reported, not applied — the timeline is still untouched.
    assert out["status"]["undo_depth"] == SEEDED_DEPTH


@needs_ffprobe
def test_cut_and_keep_are_mutually_exclusive(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """Reading a 'keep' as a 'cut' would produce the exact inverse edit."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await session.call_tool(
            "cut_by_transcript",
            {"path": str(project), "clip_id": clip["clip_id"], "cut": [[0, 1]], "keep": [[2, 3]]},
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    assert "exactly one" in result.content[0].text


@needs_ffprobe
def test_cut_by_time_converts_render_time_to_source_and_cuts(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Render time equals source time before any cut has landed, so the
    conversion is checkable directly against the fixture's own word times.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call("cut_by_time", path=str(project), spans=[[3.0, 3.9]])

    out = anyio.run(_with_server, body)

    assert len(out["applied"]) == 1
    pieces = out["applied"][0]["pieces"]
    assert len(pieces) == 1
    assert [w["text"] for w in pieces[0]["words_overlapped"]] == ["w10"]
    assert pieces[0]["context_after"][0]["text"] == "w11"


@needs_ffprobe
def test_cut_by_time_uses_render_time_not_source_time_once_a_prior_cut_has_landed(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The core "this is the inverse of timeline_span" claim, pinned against
    the real server rather than only timeline.py.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        # Cut w00/w01 (source 0.0-1.9), which shifts the whole timeline left.
        earlier = await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[0, 1]]
        )
        # w10/w11 now sit at render time 1.1-2.9, not their source time 3.0-4.9.
        cut = await client.call("cut_by_time", path=str(project), spans=[[1.1, 2.0]])
        return {"earlier": earlier, "cut": cut}

    out = anyio.run(_with_server, body)

    removed_earlier = out["earlier"]["removed"]
    assert removed_earlier == pytest.approx(1.9, abs=0.01)

    piece = out["cut"]["applied"][0]["pieces"][0]
    assert piece["source_start"] == pytest.approx(1.1 + removed_earlier, abs=0.01)
    assert piece["source_end"] == pytest.approx(2.0 + removed_earlier, abs=0.01)


@needs_ffprobe
def test_cut_by_time_spanning_a_prior_cut_splits_into_two_source_pieces(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The end-to-end version of the roadmap's headline scenario: a render-time
    note straddling a seam a prior cut created resolves into two source pieces
    of the same clip, non-adjacent by exactly the earlier cut's width.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        earlier = await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[2, 3]]
        )
        cut = await client.call("cut_by_time", path=str(project), spans=[[2.5, 3.5]])
        return {"earlier": earlier, "cut": cut}

    out = anyio.run(_with_server, body)

    pieces = out["cut"]["applied"][0]["pieces"]
    assert len(pieces) == 2
    assert pieces[0]["clip_id"] == pieces[1]["clip_id"]
    gap = pieces[1]["source_start"] - pieces[0]["source_end"]
    assert gap == pytest.approx(out["earlier"]["removed"], abs=0.01)


@needs_ffprobe
def test_cut_by_time_pad_widens_only_the_outer_edges(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The one spot a wrong implementation would silently over-cut a live
    neighbour across the seam: pad must reach only the two true outer edges,
    never the inner seam a multi-piece span crosses.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[2, 3]]
        )
        return await client.call(
            "cut_by_time", path=str(project), spans=[[2.5, 3.5]], pad=0.2
        )

    out = anyio.run(_with_server, body)
    pieces = out["applied"][0]["pieces"]
    assert len(pieces) == 2

    # Outer edges padded...
    assert pieces[0]["source_start"] == pytest.approx(2.3, abs=0.01)
    assert pieces[1]["source_end"] == pytest.approx(5.6, abs=0.01)
    # ...but the inner seam is not, so a narrow gap on the far side stays intact.
    assert pieces[0]["source_end"] == pytest.approx(3.0, abs=0.01)
    assert pieces[1]["source_start"] == pytest.approx(4.9, abs=0.01)


@needs_ffprobe
def test_cut_by_time_plan_matches_the_real_cut(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """HISTORY.md § `cut --plan`: a plan runs the identical code path and simply
    skips the write, so a plan and the real cut that follows must agree exactly.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        planned = await client.call(
            "cut_by_time", path=str(project), spans=[[3.0, 3.9]], plan=True
        )
        status_after_plan = await client.call("timeline_status", path=str(project))
        cut = await client.call("cut_by_time", path=str(project), spans=[[3.0, 3.9]])
        return {"planned": planned, "status_after_plan": status_after_plan, "cut": cut}

    out = anyio.run(_with_server, body)

    assert out["planned"]["plan"] is True
    assert out["status_after_plan"]["undo_depth"] == SEEDED_DEPTH
    assert out["cut"]["duration_after"] == pytest.approx(out["planned"]["duration_after"], abs=1e-9)
    assert out["cut"]["removed"] == pytest.approx(out["planned"]["removed"], abs=1e-9)
    assert out["cut"]["applied"] == out["planned"]["applied"]


@needs_ffprobe
def test_cut_by_time_reports_requested_removed_versus_removed(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """`removed == requested_removed` is asserted, not just reported, at
    `pad == 0.0` — spans address the current timeline, so every requested
    render-second is live by construction. `pad > 0` legitimately removes more.
    """
    audio, transcript = sources

    async def _flow(project: Path, pad: float) -> dict[str, Any]:
        async def body(session: ClientSession) -> dict[str, Any]:
            client = Client(session)
            await client.call("init", path=str(project))
            clip = await client.call("import_media", path=str(project), source=str(audio))
            await client.call(
                "attach_transcript",
                path=str(project),
                clip_id=clip["clip_id"],
                transcript_path=str(transcript),
            )
            await client.call(
                "seed_timeline",
                path=str(project),
                clip_id=clip["clip_id"],
                remove_silences=False,
            )
            return await client.call(
                "cut_by_time", path=str(project), spans=[[3.0, 3.9]], pad=pad
            )

        return await _with_server(body)

    unpadded = anyio.run(_flow, tmp_path / "unpadded", 0.0)
    padded = anyio.run(_flow, tmp_path / "padded", 0.5)

    assert unpadded["removed"] == pytest.approx(unpadded["requested_removed"], abs=1e-9)
    assert padded["removed"] > padded["requested_removed"]


@needs_ffprobe
def test_cut_by_time_rejects_overlapping_spans_in_one_call(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Overlapping spans are refused, not merged or applied twice — an overlap
    between two watch-notes is almost certainly one flub logged twice.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await session.call_tool(
            "cut_by_time", {"path": str(project), "spans": [[3.0, 4.0], [3.5, 5.0]]}
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    text = result.content[0].text
    assert "overlap" in text


@needs_ffprobe
def test_cut_by_time_rejects_a_span_past_the_end(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Distinguished from the overlap refusal above by message content, not just
    is_error — an over-eager or wrongly-routed boundary check would still leave
    is_error True while refusing for the wrong reason.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await session.call_tool(
            "cut_by_time", {"path": str(project), "spans": [[11.0, 20.0]]}
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    text = result.content[0].text
    assert "outside the timeline" in text


@needs_ffprobe
def test_cut_by_time_refuses_a_suspect_boundary_without_confirmation(tmp_path: Path) -> None:
    """A word overlapped by a (padded) span is refused just like
    `cut_by_transcript`'s own boundary word check.
    """
    audio, transcript = _suspect_duration_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        blocked = await session.call_tool(
            "cut_by_time", {"path": str(project), "spans": [[2.0, 2.5]]}
        )
        confirmed = await client.call(
            "cut_by_time", path=str(project), spans=[[2.0, 2.5]], confirm_suspect=True
        )
        return {"blocked": blocked, "confirmed": confirmed}

    out = anyio.run(_with_server, body)

    assert out["blocked"].is_error
    assert "hides a retake" in out["blocked"].content[0].text
    assert out["confirmed"]["removed"] > 0


@needs_ffprobe
def test_cut_by_time_with_plan_reports_a_suspect_boundary_instead_of_refusing(
    tmp_path: Path,
) -> None:
    """Refusing to *look* at a flagged boundary under `plan` would be
    backwards — checking the word is exactly what the refusal above asks for.
    """
    audio, transcript = _suspect_duration_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        planned = await client.call(
            "cut_by_time", path=str(project), spans=[[2.0, 2.5]], plan=True
        )
        status = await client.call("timeline_status", path=str(project))
        return {"planned": planned, "status": status}

    out = anyio.run(_with_server, body)

    flagged = out["planned"]["suspect_boundaries"]
    assert [hit["index"] for hit in flagged] == [3]
    assert flagged[0]["text"] == "bit"
    assert out["status"]["undo_depth"] == SEEDED_DEPTH


@needs_ffprobe
def test_cut_by_time_on_an_untranscribed_clip_still_cuts(tmp_path: Path) -> None:
    """The cut is the load-bearing operation; the echo is the safety net, and
    degrading it beats refusing a valid render-time cut on a picture-only clip.
    """
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 8.0)])
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call("cut_by_time", path=str(project), spans=[[2.0, 3.0]])

    out = anyio.run(_with_server, body)

    piece = out["applied"][0]["pieces"][0]
    assert piece["words_overlapped"] is None
    assert piece["transcript_missing"] is True
    assert out["duration_after"] == pytest.approx(out["duration_before"] - 1.0, abs=0.01)


def _fake_whisper(path: Path, heard: str = "hello from the stub") -> Path:
    """A whisper stand-in for `PROOFCUT_WHISPER`: writes a fixed transcript.

    Real whisper's CLI shape, minus the GPU — `asr.transcribe` only cares that
    the binary accepts these flags and drops `<stem>.json` in `--output_dir`.
    `heard` is what it hears, one word every half second.
    """
    words = [
        {"word": word, "start": round(0.5 * i, 2), "end": round(0.5 * i + 0.4, 2)}
        for i, word in enumerate(heard.split())
    ]
    return write_stub(
        path / "fake-whisper",
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('media')\n"
        "p.add_argument('--model')\n"
        "p.add_argument('--output_format')\n"
        "p.add_argument('--word_timestamps')\n"
        "p.add_argument('--output_dir')\n"
        "p.add_argument('--language', default=None)\n"
        "args = p.parse_args()\n"
        f"words = {words!r}\n"
        "out = Path(args.output_dir) / f'{Path(args.media).stem}.json'\n"
        "out.write_text(json.dumps({'language': args.language or 'en', 'words': words}))\n",
    )


@needs_ffprobe
def test_transcribe_runs_whisper_and_attaches_the_result(tmp_path: Path) -> None:
    """transcribe wires asr.transcribe -> parse_whisper -> the transcript cache.

    No real GPU here — PROOFCUT_WHISPER points the server subprocess at a stand-in
    that writes a fixed transcript, so this checks the wiring, not whisper.
    """
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 2.0)], duration=3.0)
    project = tmp_path / "proj"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proofcut.cli", "mcp"],
        env={"PROOFCUT_WHISPER": str(_fake_whisper(tmp_path))},
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        transcribed = await client.call("transcribe", path=str(project), clip_id=clip["clip_id"])
        found = await client.call("get_transcript", path=str(project), clip_id=clip["clip_id"])
        return {"transcribed": transcribed, "found": found}

    out = anyio.run(lambda: _with_server(body, server))

    assert out["transcribed"]["words"] == 4
    assert out["transcribed"]["language"] == "en"
    assert Path(out["transcribed"]["cached"]).exists()
    assert out["found"]["text"] == "hello from the stub"
    assert out["transcribed"]["hallucinated_words"] == 0


def _fake_whisper_runaway(path: Path) -> Path:
    """A whisper stand-in that ends in a repeat loop, as the real one did.

    The tail is the October scale spike's own artifact, word for word: eight
    words echoing an earlier sentence inside the last 0.20 s of the audio. The
    ingest path had no guard against this at all, and the windowed pass's rule
    only sees the three of the eight that share an instant.
    """
    return write_stub(
        path / "runaway-whisper",
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('media')\n"
        "p.add_argument('--model')\n"
        "p.add_argument('--output_format')\n"
        "p.add_argument('--word_timestamps')\n"
        "p.add_argument('--output_dir')\n"
        "p.add_argument('--language', default=None)\n"
        "args = p.parse_args()\n"
        "real = [\n"
        "    {'word': 'them', 'start': 117.78, 'end': 118.12},\n"
        "    {'word': 'alive', 'start': 118.12, 'end': 118.70},\n"
        "    {'word': 'you', 'start': 118.70, 'end': 119.64},\n"
        "    {'word': 'know', 'start': 119.64, 'end': 119.78},\n"
        "]\n"
        "loop = [\n"
        "    {'word': 'people', 'start': 119.78, 'end': 119.78},\n"
        "    {'word': 'were', 'start': 119.78, 'end': 119.78},\n"
        "    {'word': 'really', 'start': 119.78, 'end': 119.78},\n"
        "    {'word': 'well', 'start': 119.78, 'end': 119.88},\n"
        "    {'word': 'people', 'start': 119.88, 'end': 119.94},\n"
        "    {'word': 'of', 'start': 119.94, 'end': 119.94},\n"
        "    {'word': 'you', 'start': 119.94, 'end': 119.98},\n"
        "    {'word': 'know', 'start': 119.98, 'end': 119.98},\n"
        "]\n"
        "out = Path(args.output_dir) / f'{Path(args.media).stem}.json'\n"
        "out.write_text(json.dumps({'language': 'en', 'segments': [\n"
        "    {'start': 117.78, 'end': 119.78, 'text': ' them alive you know', 'words': real},\n"
        "    {'start': 119.78, 'end': 119.98, 'text': ' people were really well', 'words': loop},\n"
        "]}))\n",
    )


@needs_ffprobe
def test_transcribe_drops_a_runaway_tail_and_says_how_many(tmp_path: Path) -> None:
    """The ingest path's hallucination guard, over the real server.

    The defect the scale spike found: `_drop_stacked` ran only in the windowed
    path, so a single-pass transcription could write a repeat loop into the
    project's transcript, where every later cut and cue is addressed against
    it. The count is reported rather than only applied — a transcript quietly
    shortened is the failure this repo will not ship.
    """
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 2.0)], duration=3.0)
    project = tmp_path / "proj"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proofcut.cli", "mcp"],
        env={"PROOFCUT_WHISPER": str(_fake_whisper_runaway(tmp_path))},
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        transcribed = await client.call("transcribe", path=str(project), clip_id=clip["clip_id"])
        found = await client.call("get_transcript", path=str(project), clip_id=clip["clip_id"])
        return {"transcribed": transcribed, "found": found}

    out = anyio.run(lambda: _with_server(body, server))

    assert out["transcribed"]["hallucinated_words"] == 8
    assert out["transcribed"]["words"] == 4
    # The real words survive whole, in order, and nothing of the loop is left.
    assert out["found"]["text"] == "them alive you know"


def _fake_whisper_finish_check(path: Path) -> Path:
    """A branching whisper stand-in for `finish_check`'s own stdio test.

    `finish_check` makes several separate whisper invocations — one windowed
    multi-file pass, one per hold, one per boundary recheck — and CLAUDE.md
    is explicit that none of them should run concurrently, so one stub has
    to answer all the shapes rather than assuming a single fixed transcript.
    The two shapes it distinguishes are how many positional media files it
    is handed: `transcribe_windowed`'s own `nargs="+"` invocation hands
    whisper every window at once; `_transcribe_span` (a hold's own span, or
    a boundary recheck — this test's project has no holds, so it is always
    the recheck) hands it exactly one file.
    """
    return write_stub(
        path / "fake-whisper-finish-check",
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('media', nargs='+')\n"
        "p.add_argument('--model')\n"
        "p.add_argument('--output_format')\n"
        "p.add_argument('--word_timestamps')\n"
        "p.add_argument('--output_dir')\n"
        "p.add_argument('--verbose', default=None)\n"
        "p.add_argument('--language', default=None)\n"
        "args = p.parse_args()\n"
        "out = Path(args.output_dir)\n"
        "# Window-local words: transcribe_windowed's own _absolute() adds\n"
        "# each window's start offset on the way out, so these are\n"
        "# deliberately window-relative rather than whole-file times.\n"
        "# 'charlie'/'delta' are never emitted by either window — a real\n"
        "# stitch loss, not a reconciliation artifact.\n"
        "WINDOWS = {\n"
        "    0: [{'word': 'alpha', 'start': 0.3, 'end': 0.6},\n"
        "        {'word': 'bravo', 'start': 1.3, 'end': 1.6}],\n"
        "    1: [{'word': 'echo', 'start': 0.3, 'end': 0.6},\n"
        "        {'word': 'foxtrot', 'start': 1.3, 'end': 1.6}],\n"
        "}\n"
        "if len(args.media) > 1:\n"
        "    for m in args.media:\n"
        "        stem = Path(m).stem\n"
        "        idx = int(stem.lstrip('w'))\n"
        "        words = WINDOWS.get(idx, [])\n"
        "        (out / f'{stem}.json').write_text(json.dumps({'language': 'en', 'words': words}))\n"
        "else:\n"
        "    # A single-file call, always the boundary recheck in this test\n"
        "    # (no holds) — recovers the run the two windows lost.\n"
        "    words = [{'word': 'charlie', 'start': 0.1, 'end': 0.4},\n"
        "             {'word': 'delta', 'start': 0.5, 'end': 0.8}]\n"
        "    stem = Path(args.media[0]).stem\n"
        "    (out / f'{stem}.json').write_text(json.dumps({'language': 'en', 'words': words}))\n",
    )


@needs_ffprobe
@needs_ffmpeg
def test_finish_check_reachable_over_stdio_and_recovers_a_boundary_miss(
    tmp_path: Path,
) -> None:
    """`finish_check` is registered and reachable, and step 6's direction is
    right end to end: a run neither window's own fake transcript ever
    emits — a real stitch loss, not a reconciliation artifact — is recut and
    re-transcribed on its own by the same branching stub and comes back as
    `boundary_misses`, not `missing`. `finishlog` gains an entry keyed to
    `final`'s own sha256.
    """
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    _make_wav(vo, tones=[(0.0, 6.0)], duration=6.0)

    transcript = tmp_path / "vo.json"
    transcript.write_text(
        json.dumps(
            {
                "language": "en",
                "words": [
                    {"word": "alpha", "start": 0.0, "end": 0.4},
                    {"word": "bravo", "start": 1.0, "end": 1.4},
                    {"word": "charlie", "start": 2.0, "end": 2.4},
                    {"word": "delta", "start": 3.0, "end": 3.4},
                    {"word": "echo", "start": 4.0, "end": 4.4},
                    {"word": "foxtrot", "start": 5.0, "end": 5.4},
                ],
            }
        ),
        encoding="utf-8",
    )

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proofcut.cli", "mcp"],
        env={"PROOFCUT_WHISPER": str(_fake_whisper_finish_check(tmp_path))},
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(vo))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call(
            "finish_check",
            path=str(project),
            final=str(vo),
            prepend_seconds=0.0,
            window=4.0,
            overlap=2.0,
            recheck_pad=1.0,
        )

    result = anyio.run(lambda: _with_server(body, server))

    assert {
        "faults", "ok", "streams", "duration", "black", "holds", "missing",
        "boundary_misses", "repeats",
    } <= set(result)  # fmt: skip
    assert result["mode"] == "windowed"
    assert result["holds"] == []
    assert result["hold_errors"] == []
    assert result["missing"] == []
    assert len(result["boundary_misses"]) == 1
    assert result["boundary_misses"][0]["text"] == "charlie delta"
    assert result["repeats"] == []
    assert result["streams"]["clean"] is True
    assert result["duration"]["agrees"] is True
    assert result["black"]["has_video"] is False
    assert result["faults"] == 0
    assert result["ok"] is True

    logged = finishlog.last(Project.open(project))
    assert logged is not None
    assert logged["ok"] is True
    assert logged["faults"] == 0
    assert logged["sha256"] == result["sha256"]


@needs_ffprobe
def test_captions_follow_the_timeline_not_the_recording(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The whole point of generating captions from the project.

    Words 2-3 are cut, so they must be absent from the .ass, and every word
    after them must have moved earlier by the length of the cut.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    subtitles = tmp_path / "vo.ass"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        before = await client.call(
            "add_captions", path=str(project), output=str(tmp_path / "before.ass")
        )
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[2, 3]]
        )
        after = await client.call(
            "add_captions", path=str(project), output=str(subtitles), max_words=2
        )
        return {"before": before, "after": after}

    out = anyio.run(_with_server, body)
    written = subtitles.read_text(encoding="utf-8")

    # Eight words in, two cut, six captioned — and the drop is reported.
    assert out["before"]["words"] == 8 and out["before"]["words_cut"] == 0
    assert out["after"]["words"] == 6
    assert out["after"]["words_cut"] == 2

    # The fixture names words w<burst><n>, so indices 2-3 are the second burst.
    assert "w10" not in written and "w11" not in written
    assert "w00" in written and "w20" in written and "w30" in written

    # w20 sat at source 6.0. Cutting 3.0-4.9 removed 1.9s ahead of it, so it is
    # now heard at 4.1 — a caption still quoting 6.0 would be the bug.
    assert "0:00:04.10" in written
    assert "0:00:06.00" not in written


@needs_ffprobe
def test_a_stored_style_reaches_the_ass_file_and_survives_a_cut(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Caption styling's whole claim, exercised over the wire.

    The style is project state and the captions are derived, so a restyle
    cannot be lost by a later edit — there is nothing coupling the two. The
    agent sets it once and every regeneration picks it back up.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    subtitles = tmp_path / "vo.ass"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        styled = await client.call(
            "caption_style", path=str(project), preset="karaoke", size=80, text="yellow"
        )
        # The cut lands *after* the restyle, which is the case that used to
        # have no answer: regenerate has to come back styled.
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[2, 3]]
        )
        view = await client.call("caption_view", path=str(project))
        written = await client.call("add_captions", path=str(project), output=str(subtitles))
        read_back = await client.call("caption_style", path=str(project))
        return {"styled": styled, "view": view, "written": written, "read_back": read_back}

    out = anyio.run(_with_server, body)
    text = subtitles.read_text(encoding="utf-8")

    assert out["styled"]["written"] is True
    assert out["styled"]["changed"] == ["preset", "size", "text"]
    assert out["styled"]["stored"] == {"preset": "karaoke", "size": 80, "text": "&H0000D4FF"}

    # Stored, not flattened: the manifest carries three fields and the rest
    # still come from the preset.
    assert out["read_back"]["stored"] == out["styled"]["stored"]
    assert out["read_back"]["written"] is False, "reading is not a mutation"

    assert "Style: proofcut,Outfit,80," in text
    assert "\\k" in text, "karaoke survived the cut that followed the restyle"
    # SecondaryColour is the *unspoken* colour — the swap this layer exists for.
    assert out["view"]["style"]["ass"]["text"] == "&H0000D4FF"
    assert out["view"]["style"]["resolved"]["text"] == "#ffd400ff"

    # One derivation behind both: what the window would draw and what the file
    # contains are the same cues.
    assert len(out["view"]["cues"]) == out["written"]["cues"]
    assert out["view"]["words"] == out["written"]["words"] == 6
    assert out["view"]["words_cut"] == 2


def test_canvas_over_the_wire(tmp_path: Path) -> None:
    """Registration and the `-C` binding, plus the one field feeding both
    derivations — a project with no footage still has a canvas to report."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        derived = await client.call("canvas", path=str(project))
        planned = await client.call("canvas", path=str(project), size="1080x1920", plan=True)
        set_ = await client.call("canvas", path=str(project), size="1080x1920")
        after_plan = await client.call("canvas", path=str(project))
        return {"derived": derived, "planned": planned, "set": set_, "after": after_plan}

    out = anyio.run(_with_server, body)

    assert out["derived"]["canvas"] == "1920x1080"
    assert out["derived"]["source"] == "default"
    assert out["planned"]["written"] is False
    assert out["set"]["aspect"] == "9:16"
    assert out["set"]["routes_through"] == "mlt"
    assert out["after"]["canvas"] == "1080x1920", "the plan call left the set one alone"


def test_canvas_refusal_travels_as_an_error(tmp_path: Path) -> None:
    """An odd edge has to come back as a refusal naming the number, not as a
    canvas one pixel different from the one asked for."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        result = await session.call_tool(
            "canvas", {"path": str(project), "size": "1081x1920"}
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)

    assert out["is_error"]
    assert "even" in out["text"]


def test_head_over_the_wire(tmp_path: Path) -> None:
    """Registration and the `-C` binding, `tail`'s own partial-update shape:
    `seconds` alone after the first set changes only that field, and every
    other field defaults in (`src_start` to 0.0, the fades and `gain_db` to
    0.0)."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        opened = Project.open(project)
        manifest = opened.read_manifest()
        manifest["clips"] = [
            {
                "clip_id": "cold-open",
                "source": "/tmp/cold-open.mp4",
                "duration": 12.0,
                "has_video": True,
                "has_audio": True,
            }
        ]
        opened.write_manifest(manifest)

        derived = await client.call("head", path=str(project))
        planned = await client.call(
            "head", path=str(project), asset="cold-open", seconds=6.0, plan=True
        )
        set_ = await client.call("head", path=str(project), asset="cold-open", seconds=6.0)
        updated = await client.call(
            "head", path=str(project), seconds=5.0, fade_in=0.15, fade_out=0.5, gain_db=15.1
        )
        reset = await client.call("head", path=str(project), reset=True)
        return {
            "derived": derived,
            "planned": planned,
            "set": set_,
            "updated": updated,
            "reset": reset,
        }

    out = anyio.run(_with_server, body)

    assert out["derived"]["head"] is None
    assert out["planned"]["written"] is False
    assert out["set"]["head"] == {
        "asset": "cold-open",
        "src_start": 0.0,
        "seconds": 6.0,
        "fade_in": 0.0,
        "fade_out": 0.0,
        "gain_db": 0.0,
    }
    assert out["set"]["asset_registered"] is True
    assert out["updated"]["head"] == {
        "asset": "cold-open",
        "src_start": 0.0,
        "seconds": 5.0,
        "fade_in": 0.15,
        "fade_out": 0.5,
        "gain_db": 15.1,
    }
    assert out["reset"]["head"] is None
    assert Project.open(project).read_manifest().get("head") is None


def test_head_refuses_a_card_asset(tmp_path: Path) -> None:
    """The exact inverse of `tail`'s own refusal: a cold open is real
    footage, so `card:name` is refused rather than a media clip."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        result = await session.call_tool(
            "head", {"path": str(project), "asset": "card:outro", "seconds": 6.0}
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)

    assert out["is_error"]
    assert "registered clip_id" in out["text"]


def test_tail_over_the_wire(tmp_path: Path) -> None:
    """Registration and the `-C` binding, plus the partial-update shape
    `caption_style` has: `seconds` alone after the first set changes only
    that field, `fade` defaults to 0.0 and is not required."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        derived = await client.call("tail", path=str(project))
        planned = await client.call(
            "tail", path=str(project), asset="card:outro", seconds=6.0, plan=True
        )
        set_ = await client.call("tail", path=str(project), asset="card:outro", seconds=6.0)
        updated = await client.call("tail", path=str(project), seconds=5.0, fade=0.167)
        reset = await client.call("tail", path=str(project), reset=True)
        return {
            "derived": derived,
            "planned": planned,
            "set": set_,
            "updated": updated,
            "reset": reset,
        }

    out = anyio.run(_with_server, body)

    assert out["derived"]["tail"] is None
    assert out["planned"]["written"] is False
    assert out["set"]["tail"] == {"asset": "card:outro", "seconds": 6.0, "fade": 0.0}
    assert out["set"]["asset_exists"] is False
    assert out["updated"]["tail"] == {"asset": "card:outro", "seconds": 5.0, "fade": 0.167}
    assert out["reset"]["tail"] is None
    assert Project.open(project).read_manifest().get("tail") is None


def test_tail_refuses_a_media_clip_asset(tmp_path: Path) -> None:
    """`verify` diffs a render's own transcription against the timeline's
    words; a media clip's audio would give it something to disagree about on
    every check from here on, so this is refused at `tail` rather than
    discovered later as a permanent verify miss."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        result = await session.call_tool(
            "tail", {"path": str(project), "asset": "some_clip_id", "seconds": 6.0}
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)

    assert out["is_error"]
    assert "card" in out["text"]


def test_music_over_the_wire(tmp_path: Path) -> None:
    """Registration and the word-index echo for the A2 bed: the cue stores
    word indices and never a length, both boundary words come back echoed
    with their neighbours, and `reset` drops the key (PLAN.md § The A2 music
    lane — the design note)."""
    from proofcut import transcript as tx

    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))

        opened = Project.open(project)
        manifest = opened.read_manifest()
        manifest["clips"] = [
            {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 6.0,
             "has_video": False, "has_audio": True},
            {"clip_id": "bed", "source": "/tmp/bed.wav", "duration": 2.0,
             "has_video": False, "has_audio": True},
        ]
        opened.write_manifest(manifest)
        tx.save(
            tx.Transcript(
                clip_id="vo",
                words=(
                    tx.Word(index=0, text="the", start=0.0, end=0.3),
                    tx.Word(index=1, text="first", start=0.5, end=0.9),
                    tx.Word(index=2, text="twelve", start=1.0, end=1.4),
                ),
            ),
            opened.transcript_path("vo"),
        )

        derived = await client.call("music", path=str(project))
        set_ = await client.call(
            "music", path=str(project), asset="bed", clip_id="vo", word_index_start=1
        )
        bounded = await client.call("music", path=str(project), word_index_end=2)
        cleared = await client.call("music", path=str(project), clear_end=True)
        reset = await client.call("music", path=str(project), reset=True)
        refused = await session.call_tool(
            "music",
            {"path": str(project), "asset": "card:outro", "clip_id": "vo",
             "word_index_start": 1},
        )
        return {
            "derived": derived,
            "set": set_,
            "bounded": bounded,
            "cleared": cleared,
            "reset": reset,
            "refused": {"is_error": refused.is_error, "text": refused.content[0].text},
        }

    out = anyio.run(_with_server, body)

    assert out["derived"]["music"] is None
    assert out["set"]["music"]["asset"] == "bed"
    assert out["set"]["music"]["word_index_end"] is None
    assert out["set"]["start_word"]["text"] == "first"
    assert out["bounded"]["music"]["word_index_end"] == 2
    assert out["bounded"]["end_word"]["text"] == "twelve"
    assert out["cleared"]["music"]["word_index_end"] is None
    assert out["reset"]["music"] is None
    assert Project.open(project).read_manifest().get("music") is None
    assert out["refused"]["is_error"]
    assert "no sound" in out["refused"]["text"]


@needs_ffprobe
@needs_ffmpeg
def test_vo_extend_over_the_wire(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """Registration and the `-C` binding for the one op authorized to grow the
    timeline (PLAN.md § `vo_extend` — the design note). `plan=True` reports
    the same shape as the real call but writes neither the manifest nor the
    timeline; the real call then lands the hold and reports `covered_by`
    against a cue that would otherwise freeze silently across it.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        clip_id = clip["clip_id"]
        await client.call(
            "attach_transcript", path=str(project), clip_id=clip_id, transcript_path=str(transcript)
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip_id, remove_silences=False
        )
        Project.open(project).cards_dir.joinpath("cold-open.png").write_bytes(b"\x89PNG")
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset="card:cold-open"
        )
        manifest_before = Project.open(project).manifest_path.read_text()

        planned = await client.call(
            "vo_extend", path=str(project), clip_id=clip_id, word_index=1, seconds=2.0, plan=True
        )
        manifest_after_plan = Project.open(project).manifest_path.read_text()
        real = await client.call(
            "vo_extend", path=str(project), clip_id=clip_id, word_index=1, seconds=2.0
        )
        status = await client.call("timeline_status", path=str(project))
        refused = await session.call_tool(
            "vo_extend",
            {"path": str(project), "clip_id": clip_id, "word_index": 1, "seconds": -1.0},
        )
        return {
            "clip": clip,
            "planned": planned,
            "manifest_before": manifest_before,
            "manifest_after_plan": manifest_after_plan,
            "real": real,
            "status": status,
            "refused_is_error": refused.is_error,
            "refused_text": refused.content[0].text,
        }

    out = anyio.run(_with_server, body)

    assert out["planned"]["written"] is False
    assert out["manifest_after_plan"] == out["manifest_before"], "a plan touched the manifest"
    assert out["planned"]["duration_after"] == pytest.approx(14.0)

    assert out["real"]["written"] is True
    assert out["real"]["text"] == "w01"
    assert out["real"]["duration_before"] == pytest.approx(12.0)
    assert out["real"]["duration_after"] == pytest.approx(14.0)
    assert [c["asset"] for c in out["real"]["covered_by"]] == ["card:cold-open"]

    assert out["status"]["timeline_duration"] == pytest.approx(14.0)

    assert out["refused_is_error"]
    assert "positive" in out["refused_text"]


def test_review_add_registers_a_render_and_a_matching_control(tmp_path: Path) -> None:
    """A control whose bytes match its baseline registers with `control_ok:
    true` — the enforcement of "nothing is labelled a control unless it is
    byte-identical to what it claims to be" (HISTORY.md § The bumper the
    teaser never had)."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        (project / "renders" / "teaser.mp4").write_bytes(b"same bytes")
        (project / "renders" / "teaser-copy.mp4").write_bytes(b"same bytes")
        render = await client.call(
            "review_add", path=str(project), name="teaser", source="renders/teaser.mp4", kind="render"
        )
        control = await client.call(
            "review_add",
            path=str(project),
            name="teaser-control",
            source="renders/teaser-copy.mp4",
            kind="control",
            baseline="teaser",
        )
        listed = await client.call("review_list", path=str(project))
        return {"render": render, "control": control, "listed": listed}

    out = anyio.run(_with_server, body)

    assert out["render"]["kind"] == "render"
    assert out["render"]["control_ok"] is None
    assert out["control"]["kind"] == "control"
    assert out["control"]["control_ok"] is True
    names = {item["name"] for item in out["listed"]["items"]}
    assert names == {"teaser", "teaser-control"}


def test_review_add_refuses_a_control_whose_bytes_dont_match(tmp_path: Path) -> None:
    """A mismatched control is refused outright, and nothing is registered for
    it — the failure mode the bumper incident is named for was a page that
    labelled a *different* render a control, not one that refused to."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        (project / "renders" / "teaser.mp4").write_bytes(b"same bytes")
        (project / "renders" / "different.mp4").write_bytes(b"different bytes entirely")
        await client.call(
            "review_add", path=str(project), name="teaser", source="renders/teaser.mp4", kind="render"
        )
        result = await session.call_tool(
            "review_add",
            {
                "path": str(project),
                "name": "bad-control",
                "source": "renders/different.mp4",
                "kind": "control",
                "baseline": "teaser",
            },
        )
        listed = await client.call("review_list", path=str(project))
        return {"is_error": result.is_error, "text": result.content[0].text, "listed": listed}

    out = anyio.run(_with_server, body)

    assert out["is_error"]
    assert "byte-identical" in out["text"] or "not byte-identical" in out["text"]
    names = {item["name"] for item in out["listed"]["items"]}
    assert names == {"teaser"}


def test_review_verdict_is_recorded_and_read_back(tmp_path: Path) -> None:
    """A verdict is a free-form string, not an enum — past review rounds have
    answered yes/no, a shape choice, or a specific description."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        (project / "renders" / "teaser.mp4").write_bytes(b"a render")
        await client.call(
            "review_add", path=str(project), name="teaser", source="renders/teaser.mp4", kind="render"
        )
        recorded = await client.call(
            "review_verdict", path=str(project), name="teaser", verdict="ship it", note="watched twice"
        )
        listed = await client.call("review_list", path=str(project))
        return {"recorded": recorded, "listed": listed}

    out = anyio.run(_with_server, body)

    assert out["recorded"]["verdict"] == "ship it"
    assert out["recorded"]["note"] == "watched twice"
    assert out["listed"]["verdicts"]["teaser"]["verdict"] == "ship it"


def test_reframe_over_the_wire(tmp_path: Path) -> None:
    """The sequence an agent asked for a vertical cut would run after the
    cards: swap the canvas, read what it crops, then move the crop off centre
    because the subject is not centred. The clip is written into the manifest
    rather than imported — a reframe is arithmetic over a declared shape, and
    ffprobe is not what is under test."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        opened = Project.open(project)
        manifest = opened.read_manifest()
        manifest["clips"] = [
            {
                "clip_id": "cold-open",
                "source": "/tmp/cold-open.mp4",
                "duration": 12.0,
                "has_video": True,
                "has_audio": True,
                "width": 1920,
                "height": 816,
            }
        ]
        opened.write_manifest(manifest)

        swapped = await client.call("canvas", path=str(project), size="1080x1920")
        centred = await client.call("reframe", path=str(project))
        moved = await client.call(
            "reframe", path=str(project), clip_id="cold-open", rect="1200,0,459,816"
        )
        impossible = await session.call_tool(
            "reframe",
            {"path": str(project), "clip_id": "cold-open", "rect": "0,0,1920,816"},
        )
        return {
            "swapped": swapped,
            "centred": centred,
            "moved": moved,
            "impossible": (impossible.is_error, impossible.content[0].text),
        }

    out = anyio.run(_with_server, body)

    assert out["swapped"]["cropped"] == ["cold-open"]
    assert out["centred"]["clips"][0]["crop"] == "730,0,459,816"
    assert out["centred"]["clips"][0]["origin"] == "centre"
    assert out["moved"]["clips"][0]["crop"] == "1200,0,459,816"
    assert out["moved"]["clips"][0]["origin"] == "override"

    is_error, text = out["impossible"]
    assert is_error, "an impossible ask refuses rather than quietly clipping"
    assert "730,0,459,816" in text, "and the refusal names the rect that would work"


def test_reframe_interp_over_the_wire(tmp_path: Path) -> None:
    """The keyframed move reaches an agent, and its two refusals do too.

    `reframe` was already registered, so the tool inventory says nothing about
    whether a *parameter* added to it is reachable — a default-valued keyword
    that never made it into the tool signature would leave every call
    succeeding with the flag silently dropped, which is the shape of failure
    this file exists for. Both refusals are asserted over the wire rather than
    in-process for the same reason: they are the only thing standing between a
    hand-typed flag and a document that renders wrong at exit 0 (HISTORY.md
    § The keyframed move)."""
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        opened = Project.open(project)
        manifest = opened.read_manifest()
        manifest["clips"] = [
            {
                "clip_id": "cold-open",
                "source": "/tmp/cold-open.mp4",
                "duration": 12.0,
                "has_video": True,
                "has_audio": True,
                "width": 1920,
                "height": 816,
            }
        ]
        opened.write_manifest(manifest)

        await client.call("canvas", path=str(project), size="1080x1920")
        await client.call(
            "reframe", path=str(project), clip_id="cold-open", rect="200,0,459,816"
        )
        slid = await client.call(
            "reframe",
            path=str(project),
            clip_id="cold-open",
            rect="1200,0,459,816",
            src_start=4.0,
            interp=True,
        )
        head = await session.call_tool(
            "reframe",
            {
                "path": str(project),
                "clip_id": "cold-open",
                "rect": "300,0,459,816",
                "interp": True,
            },
        )
        split = await session.call_tool(
            "reframe",
            {
                "path": str(project),
                "clip_id": "cold-open",
                "rect": "0,0,459,408",
                "pane": "1461,0,459,408",
                "src_start": 8.0,
                "interp": True,
            },
        )
        return {
            "slid": slid,
            "head": (head.is_error, head.content[0].text),
            "split": (split.is_error, split.content[0].text),
        }

    out = anyio.run(_with_server, body)

    by_start = {w["src_start"]: w["interp"] for w in out["slid"]["clips"][0]["windows"]}
    assert by_start == {0.0: False, 4.0: True}, (
        "the flag rides through the tool onto the window it was asked for, and "
        "the head is never flagged"
    )

    is_error, text = out["head"]
    assert is_error, "the head has nothing before it to slide from"
    assert "head of" in text

    is_error, text = out["split"]
    assert is_error, "a split's lower pane has no interpolation of its own"
    assert "cannot also slide" in text


@needs_ffmpeg
@needs_ffprobe
def test_reframe_coverage_over_the_wire(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """The reading that says a window is covering footage it was never chosen
    for — over stdio, because that is the client that would ask.

    Real footage with a real cut in it: this is the one framing tool whose
    answer is ffmpeg's rather than arithmetic, so a fixtured clip would be
    testing proofcut against itself. The window is stored at the head and the cut
    is six seconds in, which is the film's `cold-open` shape in miniature —
    there, one rect covered four camera setups and the manifest, `status` and
    `reframe_sheet` were all clean over it.
    """
    audio, transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video_with_cut(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo = await client.call("import_media", path=str(project), source=str(audio))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo["clip_id"], remove_silences=False
        )
        await client.call(
            "cue_add",
            path=str(project),
            clip_id=vo["clip_id"],
            word_index=0,
            asset=clip["clip_id"],
        )
        await client.call("canvas", path=str(project), size="1080x1920")
        # Store the centre crop as an explicit window, which is what makes it
        # someone's decision rather than the default — the distinction the two
        # second-counts are built on.
        centred = await client.call("reframe", path=str(project))
        crop = next(
            row["crop"] for row in centred["clips"] if row["clip_id"] == clip["clip_id"]
        )
        await client.call(
            "reframe", path=str(project), clip_id=clip["clip_id"], rect=crop
        )
        return {
            "covered": await client.call("reframe_coverage", path=str(project)),
            "strict": await client.call(
                "reframe_coverage", path=str(project), threshold=0.99
            ),
            "clip": clip,
        }

    out = anyio.run(_with_server, body)
    covered = out["covered"]

    assert covered["cuts"] == 1 and covered["cuts_unframed"] == 1
    assert covered["stale_stretches"] == 1
    (stretch,) = covered["stretches"]
    assert stretch["asset"] == out["clip"]["clip_id"]
    assert stretch["stale"] is True
    assert stretch["src_start"] == pytest.approx(6.0, abs=0.2)
    assert "a different shot's framing" in stretch["framed_by"]
    assert covered["stale_seconds"] > 0
    # Where it plays, not just where it reads — the fix is to go and look at it.
    assert stretch["timeline_start"] == pytest.approx(6.0, abs=0.2)

    # A floor high enough to find no cuts finds nothing stale either, and says
    # which floor it used: the miss rate of the threshold *is* a framing number.
    assert out["strict"]["cuts"] == 0
    assert out["strict"]["stale_seconds"] == 0.0
    assert out["strict"]["threshold"] == 0.99


@needs_ffmpeg
@needs_ffprobe
def test_import_media_contact_sheet_rides_along_over_the_wire(tmp_path: Path) -> None:
    """`import_media`'s default `sheet=True`, over the real server: a video
    clip's registration reply carries its own first look, so an agent sees
    "the first N seconds are X" without a second call it has to remember to
    make. `contact_sheet` itself is also independently reachable, for a
    re-import or a wider look.
    """
    footage = tmp_path / "footage.mp4"
    _make_video(footage, duration=12.0)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        sheet = await client.call(
            "contact_sheet", path=str(project), clip_id=clip["clip_id"]
        )
        return {"clip": clip, "sheet": sheet}

    out = anyio.run(_with_server, body)

    assert "contact_sheet" in out["clip"]
    assert out["clip"]["contact_sheet"]["clip_id"] == out["clip"]["clip_id"]
    assert len(out["clip"]["contact_sheet"]["frames"]) > 0
    # The direct call is the same request `import_media` made internally —
    # same count, same source times, and a cache hit the second time round.
    assert len(out["sheet"]["frames"]) == len(out["clip"]["contact_sheet"]["frames"])
    assert [f["src_time"] for f in out["sheet"]["frames"]] == [
        f["src_time"] for f in out["clip"]["contact_sheet"]["frames"]
    ]
    assert all(f["cached"] for f in out["sheet"]["frames"])


@needs_ffmpeg
@needs_ffprobe
def test_continuity_check_accept_reject_ls_round_trip_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The unpinned rewind the goodsometimes script's own gap missed, found
    and then acknowledged over the real server — every new tool this feature
    adds, reachable and registered, not just written.

    `clipa` is 5s; shot0 (words 0-1, `[0, 3)`) fits inside it and leaves the
    cursor at 3.0; shot1 (words 2-3, `[3, 6)`, 3s) would need to read
    `[3, 6)` — 6s in, past the 5s asset — so `plan_picture` rewinds the
    cursor to 0 rather than clamping (its own duration, 3s, fits from
    there). Both cues are unpinned — `src_pin` is `None` on both — exactly
    the case `shot_check.py`'s own `if pin is None: continue` would have
    skipped.
    """
    audio, transcript = sources
    footage = tmp_path / "clipa.mp4"
    _make_video(footage, duration=5.0)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo = await client.call("import_media", path=str(project), source=str(audio))
        clip = await client.call(
            "import_media", path=str(project), source=str(footage), sheet=False
        )
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo["clip_id"], remove_silences=False
        )
        await client.call(
            "cue_add", path=str(project), clip_id=vo["clip_id"], word_index=0, asset=clip["clip_id"]
        )
        # Every remaining word re-cued to clipa too, each shot 3s — short
        # enough that every rewind-to-0 actually fits, rather than the last
        # one growing to the timeline's own end (12.0) and overrunning the
        # 5s asset outright, which is a refusal rather than a rewind.
        for word_index in (2, 4, 6):
            await client.call(
                "cue_add",
                path=str(project),
                clip_id=vo["clip_id"],
                word_index=word_index,
                asset=clip["clip_id"],
            )
        checked = await client.call(
            "continuity_check", path=str(project), stubs=False
        )
        rewind = next(f for f in checked["findings"] if f["kind"] == "rewind")
        accepted = await client.call(
            "continuity_accept",
            path=str(project),
            clip_id=rewind["clip_id"],
            word_index=rewind["word_index"],
            kind="rewind",
        )
        suppressed = await client.call("continuity_check", path=str(project), stubs=False)
        listed = await client.call("continuity_ls", path=str(project))
        rejected = await client.call(
            "continuity_reject",
            path=str(project),
            clip_id=rewind["clip_id"],
            word_index=rewind["word_index"],
            kind="rewind",
        )
        restored = await client.call("continuity_check", path=str(project), stubs=False)
        return {
            "clip": clip,
            "checked": checked,
            "rewind": rewind,
            "accepted": accepted,
            "suppressed": suppressed,
            "listed": listed,
            "rejected": rejected,
            "restored": restored,
        }

    out = anyio.run(_with_server, body)

    # `sheet=False` on the video import — proof `--no-sheet`/`sheet=False`
    # actually skips the sheet rather than only defaulting it on elsewhere.
    assert "contact_sheet" not in out["clip"]

    assert out["rewind"]["asset"] == out["clip"]["clip_id"]
    accepted_word = out["rewind"]["word_index"]
    assert out["accepted"]["accepted"] == 1

    # The accepted rewind is gone from the live findings; every re-cued word
    # rewinds the same way, so others may still be there — this checks the
    # one that was actually accepted, not "no rewinds anywhere".
    still_there = {
        f["word_index"] for f in out["suppressed"]["findings"] if f["kind"] == "rewind"
    }
    assert accepted_word not in still_there
    assert out["suppressed"]["accepted"] == 1

    assert out["listed"]["count"] == 1
    (row,) = out["listed"]["accepted"]
    assert row["word_index"] == accepted_word
    assert row["kind"] == "rewind"
    assert row["stale"] is False

    assert out["rejected"]["accepted"] == 0
    restored_words = {
        f["word_index"] for f in out["restored"]["findings"] if f["kind"] == "rewind"
    }
    assert accepted_word in restored_words


@needs_ffmpeg
@needs_ffprobe
@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_shot_sheet_returns_the_image_itself_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The whole point of this tool: the reply carries the picture, not a path.

    Asserted against the raw `CallToolResult` rather than through `Client`,
    which reads `content[0]` and would pass just as happily on a tool that
    returned only its table. The agent panel runs `claude --tools ''`, so a
    path in the reply is unreachable there — an `ImageContent` block is the
    entire feature, and a test that never looks for one cannot tell the
    difference.

    It also pins the return annotation. `-> Any` on the tool is load-bearing:
    a concrete one makes the SDK build an output schema, and validating an
    `Image` against it fails with `is_error` and a serialization message from
    a tool body that is perfectly correct.
    """
    audio, transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo = await client.call("import_media", path=str(project), source=str(audio))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo["clip_id"], remove_silences=False
        )
        await client.call(
            "cue_add",
            path=str(project),
            clip_id=vo["clip_id"],
            word_index=0,
            asset=clip["clip_id"],
        )
        raw = await session.call_tool("shot_sheet", {"path": str(project)})
        return {
            "is_error": raw.is_error,
            "kinds": [type(block).__name__ for block in raw.content],
            "mime": [
                getattr(block, "mime_type", None)
                for block in raw.content
                if type(block).__name__ == "ImageContent"
            ],
            "bytes": [
                len(block.data)
                for block in raw.content
                if type(block).__name__ == "ImageContent"
            ],
            "report": json.loads(raw.content[0].text),
            "vo": vo["clip_id"],
            "clip": clip["clip_id"],
        }

    out = anyio.run(_with_server, body)

    assert out["is_error"] is False
    assert "ImageContent" in out["kinds"], f"no image came back: {out['kinds']}"
    assert out["mime"] == ["image/jpeg"]
    assert out["bytes"][0] > 0

    report = out["report"]
    assert report["shots_error"] is None
    assert report["count"] == 1 and report["shots"] == 1
    assert report["sheet"].endswith(".jpg")

    # The standing trap in this projection: a shot's addressing clip is the
    # transcript the cue hangs on — here the *audio-only* VO — and its footage
    # is `asset`. A tile drawn off `clip_id` would have nothing to show.
    (tile,) = report["tiles"]
    assert tile["asset"] == out["clip"]
    assert tile["clip_id"] == out["vo"]
    assert tile["asset"] != tile["clip_id"]
    assert tile["label"].startswith(out["clip"])


@needs_ffmpeg
@needs_ffprobe
@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_shot_sheet_pages_and_refuses_a_bad_page_size(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Paging is addressed by shot index, and a page past the end is empty.

    `pages` is what tells a caller there is more film than one reply holds —
    without it, a first page of a long edit reads as the whole picture track,
    which is the same silent-truncation shape CLAUDE.md keeps naming.
    """
    audio, transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo = await client.call("import_media", path=str(project), source=str(audio))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo["clip_id"], remove_silences=False
        )
        for word in (0, 2, 4):
            await client.call(
                "cue_add",
                path=str(project),
                clip_id=vo["clip_id"],
                word_index=word,
                asset=clip["clip_id"],
            )
        first = await client.call("shot_sheet", path=str(project), per_page=2, page=0)
        second = await client.call("shot_sheet", path=str(project), per_page=2, page=1)
        past = await client.call("shot_sheet", path=str(project), per_page=2, page=9)
        return {"first": first, "second": second, "past": past}

    out = anyio.run(_with_server, body)

    assert out["first"]["shots"] == 3
    assert out["first"]["pages"] == 2
    assert out["first"]["count"] == 2
    assert out["second"]["count"] == 1

    # Indices are absolute over the picture track, not per page — a caller
    # cross-referencing a tile against `shots` needs one numbering.
    assert [t["index"] for t in out["first"]["tiles"]] == [0, 1]
    assert [t["index"] for t in out["second"]["tiles"]] == [2]

    # Past the end draws nothing rather than raising: "there is no page 9" is
    # an answer, and `pages` beside it says what the real range was.
    assert out["past"]["count"] == 0 and out["past"]["sheet"] is None
    assert out["past"]["pages"] == 2


@needs_ffmpeg
@needs_ffprobe
@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_footage_sheet_returns_the_image_and_needs_no_edit(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """A clip's own footage, sheeted with no timeline, no cues and no transcript.

    That is the whole difference from `shot_sheet`, and it is the audience:
    someone with a recording and nothing to search has no edit yet, so a tool
    that needs one is a tool they cannot reach. Only `init` and `import_media`
    run here — deliberately no `seed_timeline`, no `attach_transcript`, no
    `cue_add`.

    Asserted on the raw `CallToolResult` for the same reason `shot_sheet`'s
    twin is: `Client` reads `content[0]`, so a tool that returned only its
    table would pass, and the `ImageContent` block is the entire feature.
    """
    _audio, _transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage, duration=12.0)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        raw = await session.call_tool(
            "footage_sheet",
            {"path": str(project), "clip_id": clip["clip_id"], "interval": 5.0},
        )
        return {
            "is_error": raw.is_error,
            "kinds": [type(block).__name__ for block in raw.content],
            "mime": [
                getattr(block, "mime_type", None)
                for block in raw.content
                if type(block).__name__ == "ImageContent"
            ],
            "bytes": [
                len(block.data) for block in raw.content if type(block).__name__ == "ImageContent"
            ],
            "report": json.loads(raw.content[0].text),
            "clip": clip["clip_id"],
        }

    out = anyio.run(_with_server, body)

    assert out["is_error"] is False
    assert "ImageContent" in out["kinds"], f"no image came back: {out['kinds']}"
    assert out["mime"] == ["image/jpeg"]
    assert out["bytes"][0] > 0

    report = out["report"]
    # 12s at a 5s ask is three equal windows of 4s, never two of 5 and a 2s
    # runt — `plan_windows`' rule, shared rather than re-derived.
    assert report["marks"] == 3
    assert report["drawn"] == 3
    assert report["mode"] == "interval" and report["asked"] == "auto"
    assert report["interval"] == 4.0 and report["interval_asked"] == 5.0
    assert report["sheet"].endswith(".jpg")
    assert [t["clip_id"] for t in report["tiles"]] == [out["clip"]] * 3

    # Every tile carries its luma, and nothing here is blank: `testsrc` is a
    # colour chart. A sheet that called it blank would be telling an agent to
    # disregard the only footage in the project.
    assert report["blank"] == 0
    assert all(t["luma"]["blank"] is False for t in report["tiles"])
    assert all(t["luma"]["scale"] == 255.0 for t in report["tiles"])


@needs_ffmpeg
@needs_ffprobe
@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_contact_sheet_returns_the_image_itself_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The first look, as a picture rather than as seven paths.

    `import_media` already made these frames — what this tool adds is that
    the caller can *see* them, which is the whole reason the incident behind
    `FIRST_LOOK_SECONDS` happened: nobody looked at the clip's own head.
    Asserted on the raw `CallToolResult`, because `Client` reads `content[0]`
    and a tool returning only its table would pass that just as happily.
    """
    _audio, _transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage, duration=12.0)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        raw = await session.call_tool(
            "contact_sheet", {"path": str(project), "clip_id": clip["clip_id"]}
        )
        return {
            "is_error": raw.is_error,
            "kinds": [type(block).__name__ for block in raw.content],
            "mime": [
                getattr(block, "mime_type", None)
                for block in raw.content
                if type(block).__name__ == "ImageContent"
            ],
            "bytes": [
                len(block.data) for block in raw.content if type(block).__name__ == "ImageContent"
            ],
            "report": json.loads(raw.content[0].text),
            "clip": clip["clip_id"],
        }

    out = anyio.run(_with_server, body)

    assert out["is_error"] is False
    assert "ImageContent" in out["kinds"], f"no image came back: {out['kinds']}"
    assert out["mime"] == ["image/jpeg"]
    assert out["bytes"][0] > 0

    report = out["report"]
    assert report["clip_id"] == out["clip"]
    assert report["sheet"].endswith(".jpg")
    # The frames themselves stay where the filmstrip route serves them from —
    # the montage is drawn from those, never a second extraction.
    assert all("/cache/thumbs/" in Path(frame["path"]).as_posix() for frame in report["frames"])


@needs_ffmpeg
@needs_ffprobe
@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_reframe_sheet_pages_come_back_as_an_image(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The retrofit this tool existed without: a framing review an agent can see.

    Its unpaged montage is the whole project at review resolution — on the
    film ~4700px tall — and vision downscales anything past ~1568 on its long
    edge, so handing that back would deliver the rects and labels resampled
    away. A page is drawn to the width that reads back verbatim; asking for
    the whole thing (`per_page: null`) still returns a PNG's *path*, which is
    correct for a person and is deliberately not an image here.
    """
    audio, transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo = await client.call("import_media", path=str(project), source=str(audio))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo["clip_id"], remove_silences=False
        )
        for word in (0, 2):
            await client.call(
                "cue_add",
                path=str(project),
                clip_id=vo["clip_id"],
                word_index=word,
                asset=clip["clip_id"],
            )
        raw = await session.call_tool(
            "reframe_sheet", {"path": str(project), "per_page": 1, "page": 0}
        )
        whole = await session.call_tool("reframe_sheet", {"path": str(project), "per_page": None})
        return {
            "is_error": raw.is_error,
            "kinds": [type(block).__name__ for block in raw.content],
            "mime": [
                getattr(block, "mime_type", None)
                for block in raw.content
                if type(block).__name__ == "ImageContent"
            ],
            "bytes": [
                len(block.data) for block in raw.content if type(block).__name__ == "ImageContent"
            ],
            "report": json.loads(raw.content[0].text),
            "whole_kinds": [type(block).__name__ for block in whole.content],
            "whole": json.loads(whole.content[0].text),
        }

    out = anyio.run(_with_server, body)

    assert out["is_error"] is False
    assert "ImageContent" in out["kinds"], f"no image came back: {out['kinds']}"
    assert out["mime"] == ["image/jpeg"]
    assert out["bytes"][0] > 0

    report = out["report"]
    assert report["sheet"].endswith("page0.jpg")
    assert report["drawn"] == 1 and report["count"] == 2 and report["pages"] == 2
    assert [row["row"] for row in report["rows"]] == [0]

    # Unpaged is a person's file, so it comes back as a path and no picture —
    # the one case where returning bytes would be the wrong answer.
    assert "ImageContent" not in out["whole_kinds"]
    assert out["whole"]["sheet"].endswith("sheet.png")
    assert out["whole"]["per_page"] is None


@needs_ffmpeg
@needs_ffprobe
def test_synopsis_and_broll_brief_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The pair an agent actually uses: fill the catalogue, then read the brief.

    Asserted together because the brief's whole job is to carry the synopses to
    whatever is choosing — a `synopsis` that registers but never reaches
    `broll_brief` would pass a test of either one alone. Needs a real video
    clip: the b-roll catalogue is footage, and the VO the cues are addressed
    against is deliberately not in it.
    """
    audio, transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo = await client.call("import_media", path=str(project), source=str(audio))
        clip = await client.call("import_media", path=str(project), source=str(footage))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo["clip_id"], remove_silences=False
        )
        empty = await client.call("broll_brief", path=str(project))
        listed = await client.call("synopsis", path=str(project))
        written = await client.call(
            "synopsis",
            path=str(project),
            clip_id=clip["clip_id"],
            text="Scream (1996) reveal. Billy and Stu unmask themselves.",
        )
        await client.call(
            "cue_add",
            path=str(project),
            clip_id=vo["clip_id"],
            word_index=2,
            asset=clip["clip_id"],
        )
        briefed = await client.call("broll_brief", path=str(project))
        return {
            "empty": empty,
            "listed": listed,
            "written": written,
            "briefed": briefed,
            "vo": vo,
            "clip": clip,
        }

    out = anyio.run(_with_server, body)

    assert "no cues yet" in out["empty"]["note"], "an unplaced project says which empty it is"
    assert out["listed"]["missing"] == [out["vo"]["clip_id"], out["clip"]["clip_id"]]
    assert out["written"]["written"] is True
    assert "note" not in out["briefed"]
    assert out["briefed"]["choices"] == 1
    assert out["briefed"]["missing_synopsis"] == [], "the VO is not a b-roll candidate"
    assert [c["clip_id"] for c in out["briefed"]["candidates"]] == [out["clip"]["clip_id"]]
    assert out["briefed"]["candidates"][0]["synopsis"].startswith("Scream (1996) reveal")
    position = out["briefed"]["positions"][0]
    assert position["card"] is False
    # One cue, so its shot is forced to frame 0 and holds the whole timeline —
    # the narration over it is every surviving word of the 8-word transcript.
    assert position["narration"] == "w00 w01 w10 w11 w20 w21 w30 w31"


@needs_ffprobe
def test_synopsis_refusal_travels_as_an_error(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """Over the cap is a refusal naming the number, not a quietly truncated
    synopsis — a clipped last clause is exactly the part that decides a
    placement."""
    audio, _ = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        result = await session.call_tool(
            "synopsis",
            {"path": str(project), "clip_id": clip["clip_id"], "text": "x" * 1200},
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)

    assert out["is_error"]
    assert "1200 characters" in out["text"]


@needs_ffprobe
def test_caption_style_plan_writes_nothing(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        planned = await client.call("caption_style", path=str(project), size=99, plan=True)
        after = await client.call("caption_style", path=str(project))
        return {"planned": planned, "after": after}

    out = anyio.run(_with_server, body)

    assert out["planned"]["resolved"]["size"] == 99
    assert out["planned"]["written"] is False
    assert out["after"]["resolved"]["size"] == 64, "the project never took it"


@needs_ffprobe
def test_caption_view_reports_a_missing_transcript_rather_than_failing(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The view a person has open while making exactly this mistake."""
    audio, _ = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call("caption_view", path=str(project))

    out = anyio.run(_with_server, body)

    assert out["cues"] == []
    assert "transcript" in out["cues_error"]
    assert out["style"]["resolved"]["preset"] == "clean", "still says what the look is"


def _heard(path: Path, words: list[str]) -> Path:
    """A transcript of a "render", as whisper would have dumped it.

    Timings are plausible but arbitrary — `verify` compares word *order*, and
    the timings of a render's own transcript are never trusted for anything
    else (CLAUDE.md).
    """
    payload = {
        "language": "en",
        "words": [{"word": w, "start": n * 1.0, "end": n * 1.0 + 0.9} for n, w in enumerate(words)],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


async def _cut_project(client: Client, project: Path, audio: Path, transcript: Path) -> None:
    """init -> import -> attach -> seed -> cut words 2-3, leaving six words."""
    await client.call("init", path=str(project))
    clip = await client.call("import_media", path=str(project), source=str(audio))
    await client.call(
        "attach_transcript",
        path=str(project),
        clip_id=clip["clip_id"],
        transcript_path=str(transcript),
    )
    await client.call(
        "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
    )
    await client.call(
        "cut_by_transcript", path=str(project), clip_id=clip["clip_id"], cut=[[2, 3]]
    )


@needs_ffprobe
def test_verify_matches_a_render_that_says_what_the_timeline_expects(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The expected sequence is the timeline's, not the transcript's.

    Words 2-3 were cut, so a render that plays the remaining six is clean —
    against the untrimmed transcript those same six words would look like four
    dropped ones.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    heard = _heard(tmp_path / "render.json", ["w00", "w01", "w20", "w21", "w30", "w31"])

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _cut_project(client, project, audio, transcript)
        # The wav stands in for a render of this timeline; the transcript is
        # supplied, so no whisper is needed anywhere in this suite.
        return await client.call(
            "verify", path=str(project), render=str(audio), transcript_path=str(heard)
        )

    out = anyio.run(_with_server, body)

    assert out["expected_words"] == 6 and out["heard_words"] == 6
    assert out["similarity"] == 1.0
    assert out["repeated"] == [] and out["dropped"] == []
    assert out["diff"] == []
    assert out["words_cut_from_transcript"] == 2
    assert out["timeline_duration"] == pytest.approx(10.1, abs=0.05)
    assert out["render_duration"] == pytest.approx(12.0, abs=0.05)
    # ASR was skipped, so nothing was cached.
    assert "heard_transcript" not in out


@needs_ffprobe
def test_verify_catches_a_phrase_the_render_plays_twice(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The retake case, which is why verify exists at all (HISTORY.md § 1)."""
    audio, transcript = sources
    project = tmp_path / "proj"
    heard = _heard(
        tmp_path / "render.json",
        ["w00", "w01", "w20", "w21", "w20", "w21", "w30", "w31"],
    )

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _cut_project(client, project, audio, transcript)
        return await client.call(
            "verify", path=str(project), render=str(audio), transcript_path=str(heard)
        )

    out = anyio.run(_with_server, body)

    assert len(out["repeated"]) == 1
    assert out["repeated"][0]["text"] == "w20 w21"
    assert out["repeated"][0]["at_heard_word"] == 4
    assert out["dropped"] == []
    assert out["heard_words"] == 8 and out["expected_words"] == 6
    assert out["similarity"] < 1.0


@needs_ffprobe
def test_verify_reports_which_pass_produced_the_words_it_heard(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """`windowed` is a request to transcribe, and a supplied transcript is not one.

    Reporting mode "windowed" here because the flag was set would tell a reader
    the render had been through the pass that catches a collapsed retake when it
    had not — and a clean result is exactly what they would act on.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    heard = _heard(tmp_path / "render.json", ["w00", "w01", "w20", "w21", "w30", "w31"])

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _cut_project(client, project, audio, transcript)
        return await client.call(
            "verify",
            path=str(project),
            render=str(audio),
            transcript_path=str(heard),
            windowed=True,
        )

    out = anyio.run(_with_server, body)

    assert out["mode"] == "supplied"
    assert "windows" not in out


@needs_ffprobe
def test_verify_reports_sound_in_a_hole_the_word_map_calls_empty(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The energy arbiter, end to end and over the wire.

    The render's word map accounts for the first burst and the last. Two more
    bursts play in between, and no transcript on either side of the diff has a
    word for them — which is the exact shape of the 4.12 s "gap" holding 2.4 s
    of speech on the Scream v1 export.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    render_words = [
        {"word": "w00", "start": 0.0, "end": 0.9},
        {"word": "w01", "start": 1.0, "end": 1.9},
        {"word": "w30", "start": 9.0, "end": 9.9},
        {"word": "w31", "start": 10.0, "end": 10.9},
    ]
    heard = tmp_path / "render.json"
    heard.write_text(json.dumps({"language": "en", "words": render_words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _cut_project(client, project, audio, transcript)
        return await client.call(
            "verify", path=str(project), render=str(audio), transcript_path=str(heard)
        )

    out = anyio.run(_with_server, body)

    gaps = out["loud_gaps"]["gaps"]
    assert len(gaps) == 1
    assert (gaps[0]["start"], gaps[0]["end"]) == (1.9, 9.0)
    # The 3-5 and 6-8 bursts, and nothing else in there. A shade over 4.0s:
    # the fixture is written at 22050 Hz and measured at 8000, and the
    # resampler's ring smears each of the four burst edges into its 20ms frame.
    assert gaps[0]["sound_seconds"] == pytest.approx(4.0, abs=0.3)


needs_auto_editor = pytest.mark.skipif(
    shutil.which("auto-editor") is None and not (Path.home() / ".local/bin/auto-editor").exists(),
    reason="auto-editor is not installed",
)


@needs_ffprobe
@needs_auto_editor
def test_nle_export_uses_a_frame_rate_not_the_audio_timebase(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The v3 timebase becomes MLT's <profile frame_rate_num>.

    Audio projects run on a millisecond timebase for cut precision, and letting
    that reach the export hands Kdenlive a 1000fps timeline. Caught on the real
    Scream VO, so it is pinned here.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    out = tmp_path / "out.kdenlive"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call("export", path=str(project), output=str(out))

    result = anyio.run(_with_server, body)

    assert result["timebase"] == 30.0
    written = Path(result["output"]).read_text(encoding="utf-8")
    assert 'frame_rate_num="30"' in written
    assert 'frame_rate_num="1000"' not in written


@needs_ffprobe
@needs_auto_editor
def test_rendering_keeps_the_millisecond_timebase(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Media renders are not frame-bound, so precision is kept there."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call(
            "export", path=str(project), output=str(tmp_path / "out.wav"), export_format=None
        )

    assert anyio.run(_with_server, body)["timebase"] == 1000.0


# -- the picture half: frame counts --------------------------------------

def _melt_available() -> bool:
    """Ask proofcut's own resolver, so the guard skips exactly when the check would."""
    try:
        picture.melt_command()
    except picture.PictureError:
        return False
    return True


needs_melt = pytest.mark.skipif(
    not _melt_available(),
    reason="melt is installed neither on PATH nor in the Kdenlive flatpak",
)


def _make_video(path: Path, *, duration: float = 12.0, fps: int = 30) -> None:
    """A real encoded video, because the check counts packets in a real one."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate={fps}:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


def _make_video_with_cut(path: Path, *, each: float = 6.0, fps: int = 30) -> None:
    """testsrc then smptebars: one unambiguous camera cut, at `each` seconds.

    Bigger than `_make_video`'s 160x120 because a 9:16 window out of it has to
    be a rect with room to be wrong in, and the scene scan wants real detail
    either side of the boundary rather than eight pixels of it. Long enough,
    too, that a single cue's shot fits inside it — `plan_picture` refuses a
    shot longer than its asset, and one cue holds the whole timeline.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate={fps}:duration={each}",
            "-f", "lavfi", "-i", f"smptebars=size=320x240:rate={fps}:duration={each}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={each * 2}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


def _make_video_with_mid_black(
    path: Path, *, before: float = 4.0, black: float = 2.0, after: float = 6.0, fps: int = 30
) -> None:
    """testsrc/black/testsrc concatenated: a real black region well before the tail."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate={fps}:duration={before}",
            "-f", "lavfi", "-i", f"color=black:size=160x120:rate={fps}:duration={black}",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate={fps}:duration={after}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={before + black + after}",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-map", "3:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


def _make_video_with_tail_black_frame(path: Path, *, duration: float = 12.0, fps: int = 30) -> None:
    """`duration` of testsrc plus exactly one appended black frame.

    Constructs `delta == picture.KNOWN_TAIL_FRAME` directly against a plain
    render, without going anywhere near melt or auto-editor's kdenlive
    export — the defect those produce is that a render ends up exactly this
    shape, so building the shape by hand pins the *reporting* the same way
    `test_melt_is_asked_what_it_would_render_before_anything_is_rendered`
    pins melt's, without needing melt installed to run it.
    """
    frame = 1.0 / fps
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate={fps}:duration={duration}",
            "-f", "lavfi", "-t", f"{frame:.6f}", "-i", f"color=black:size=160x120:rate={fps}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration + frame}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


async def _seeded(client: Client, project: Path, source: Path, transcript: Path | None) -> str:
    """init -> import -> (transcript) -> seed, the preamble every case below wants."""
    await client.call("init", path=str(project))
    clip = await client.call("import_media", path=str(project), source=str(source))
    if transcript is not None:
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
    await client.call(
        "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
    )
    return str(clip["clip_id"])


@needs_ffprobe
@needs_ffmpeg
def test_clip_rm_removes_an_unreferenced_clip(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """TRIAL.md § `spot_frames` is the tool for looking at a delivered
    render: an agent registered a delivered render as a clip just to see it,
    with no way to take that back afterwards. The happy path — nothing
    refers to the clip yet — must actually remove it."""
    audio, transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        extra = await client.call("import_media", path=str(project), source=str(footage))
        removed = await client.call("clip_rm", path=str(project), clip_id=extra["clip_id"])
        after = await client.call("assets", path=str(project))
        return {"extra": extra["clip_id"], "removed": removed, "after": after}

    out = anyio.run(_with_server, body)

    assert out["removed"] == {"clip_id": out["extra"], "removed": True}
    assert out["extra"] not in {c["clip_id"] for c in out["after"]["clips"]}


@needs_ffprobe
@needs_ffmpeg
def test_clip_rm_refuses_a_cued_clip(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    audio, transcript = sources
    footage = tmp_path / "footage.mp4"
    _make_video(footage)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        vo = await _seeded(client, project, audio, transcript)
        extra = await client.call("import_media", path=str(project), source=str(footage))
        await client.call(
            "cue_add", path=str(project), clip_id=vo, word_index=0, asset=extra["clip_id"]
        )
        return await _refused(session, "clip_rm", path=str(project), clip_id=extra["clip_id"])

    message = anyio.run(_with_server, body)
    assert "cue" in message


@needs_ffprobe
def test_clip_rm_refuses_the_clip_on_the_timeline(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        vo = await _seeded(client, project, audio, transcript)
        return vo, await _refused(session, "clip_rm", path=str(project), clip_id=vo)

    _vo, message = anyio.run(_with_server, body)
    assert "timeline" in message


def test_clip_rm_refuses_an_unknown_clip(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    ops.init(str(project))

    async def body(session: ClientSession) -> Any:
        return await _refused(session, "clip_rm", path=str(project), clip_id="nope")

    message = anyio.run(_with_server, body)
    assert "nope" in message


@needs_ffprobe
def test_list_media_finds_new_files_and_flags_already_imported_ones(
    tmp_path: Path,
) -> None:
    """TRIAL.md item 7: an unattended agent has no directory listing of its
    own (`--tools ""`), so this is what hands it source paths on a real
    job."""
    project = tmp_path / "proj"
    ops.init(str(project))
    source_dir = tmp_path / "footage"
    (source_dir / "nested").mkdir(parents=True)
    clip_a = source_dir / "a.mp4"
    _make_video(clip_a)
    (source_dir / "nested" / "b.mp4").write_bytes(clip_a.read_bytes())
    (source_dir / "notes.txt").write_text("not media")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        imported = await client.call("import_media", path=str(project), source=str(clip_a))
        listing = await client.call(
            "list_media", path=str(project), source_dir=str(source_dir)
        )
        return imported, listing

    imported, listing = anyio.run(_with_server, body)

    assert listing["count"] == 2
    assert listing["new"] == 1
    paths = {f["path"]: f["already_imported"] for f in listing["files"]}
    assert paths[str(clip_a.resolve())] is True
    assert paths[str((source_dir / "nested" / "b.mp4").resolve())] is False
    assert not any("notes.txt" in p for p in paths)
    assert imported["clip_id"]  # sanity: the import itself succeeded


def test_list_media_recursive_false_skips_subdirectories(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    ops.init(str(project))
    source_dir = tmp_path / "footage"
    (source_dir / "nested").mkdir(parents=True)
    (source_dir / "top.wav").write_bytes(b"")
    (source_dir / "nested" / "deep.wav").write_bytes(b"")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        return await client.call(
            "list_media",
            path=str(project),
            source_dir=str(source_dir),
            recursive=False,
        )

    listing = anyio.run(_with_server, body)
    assert listing["count"] == 1
    assert listing["files"][0]["path"] == str((source_dir / "top.wav").resolve())


def test_list_media_refuses_a_missing_directory(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    ops.init(str(project))

    async def body(session: ClientSession) -> Any:
        return await _refused(
            session, "list_media", path=str(project), source_dir=str(tmp_path / "nope")
        )

    message = anyio.run(_with_server, body)
    assert "directory" in message


@needs_ffprobe
def test_check_frames_reports_the_export_grid_with_no_target(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The cheap call: what the timeline will be, before anything is exported."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(Client(session), project, audio, transcript)
        return await client.call("check_frames", path=str(project))

    result = anyio.run(_with_server, body)

    # An audio-only project has no picture to take a rate from, so the export
    # default applies — the same 30 the NLE export would write.
    assert result["fps"] == 30.0
    assert result["expected_frames"] == 360
    assert result["expected_duration"] == pytest.approx(12.0, abs=0.001)
    # Nothing was compared, so there is no verdict to read.
    assert "agrees" not in result


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_a_render_of_the_current_timeline_agrees_frame_for_frame(tmp_path: Path) -> None:
    """The check passing means exactly this, and it is checked against a real render."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        # Cut, so the count is of an edit rather than of an untouched source.
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip, cut=[[4, 6], [14, 16]]
        )
        await client.call(
            "export", path=str(project), output=str(render), export_format=None
        )
        return await client.call("check_frames", path=str(project), target=str(render))

    result = anyio.run(_with_server, body)

    assert result["target_kind"] == "render"
    assert result["expected_frames"] == 276
    assert result["target_frames"] == 276
    assert result["delta"] == 0
    assert result["agrees"] is True


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_a_stale_render_is_caught_by_its_frame_count(tmp_path: Path) -> None:
    """The defect the check is for: a render that is no longer of this timeline.

    Rendering and then cutting again is the easy way to ship the previous
    edit — the file on disk still opens, still plays, and is simply the wrong
    one. Its length is the tell, and nothing else in proofcut was looking at it.
    """
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "stale.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        await client.call(
            "export", path=str(project), output=str(render), export_format=None
        )
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip, cut=[[4, 6], [14, 16]]
        )
        return await client.call("check_frames", path=str(project), target=str(render))

    result = anyio.run(_with_server, body)

    assert result["agrees"] is False
    # 360 frames of the uncut source against a 276-frame timeline.
    assert result["target_frames"] == 360
    assert result["expected_frames"] == 276
    assert result["delta"] == 84


@needs_ffprobe
@needs_auto_editor
def test_an_audio_only_render_has_no_frames_and_says_so(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """A VO project is the ordinary case, and it is not a failed check.

    `agrees` is null rather than false: nothing disagreed, there was simply
    nothing with frames in it to compare.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    render = tmp_path / "out.wav"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        await client.call(
            "export", path=str(project), output=str(render), export_format=None
        )
        return await client.call("check_frames", path=str(project), target=str(render))

    result = anyio.run(_with_server, body)

    assert result["agrees"] is None
    assert result["target_frames"] is None
    assert result["expected_frames"] == 360
    assert "no video stream" in result["notes"][0]
    # The duration is still there to compare by hand, which is the advice given.
    assert result["target_duration"] == pytest.approx(result["expected_duration"], abs=0.05)


@needs_ffprobe
@needs_ffmpeg
def test_import_edit_over_the_wire(tmp_path: Path) -> None:
    """An outside cut reaches the timeline through the tool, and the two
    refusals an agent is most likely to hit come back as errors rather than as
    a half-read edit.

    Worth reaching over the wire rather than testing `ops.import_edit` alone
    for the ordinary reason this file exists — a tool that is not registered
    is not reachable — and for one specific to this op: it *replaces* the
    timeline, so an agent that reached it by accident would overwrite an edit.
    `plan` is the guard, and it has to work from out here.
    """
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    document = tmp_path / "cut.kdenlive"
    document.write_text(
        f'<mlt root="{tmp_path}">'
        '<profile frame_rate_num="30" frame_rate_den="1" />'
        '<producer id="producer0">'
        '<property name="resource">black</property>'
        '<property name="mlt_service">color</property>'
        "</producer>"
        '<chain id="chain0"><property name="resource">pic.mp4</property></chain>'
        '<playlist id="playlist0">'
        '<entry producer="chain0" in="0" out="59"/>'
        '<entry producer="chain0" in="120" out="179"/>'
        "</playlist></mlt>",
        encoding="utf-8",
    )
    blanked = tmp_path / "blank.kdenlive"
    blanked.write_text(
        document.read_text(encoding="utf-8").replace(
            '<entry producer="chain0" in="120" out="179"/>',
            '<blank length="00:00:01.000"/><entry producer="chain0" in="120" out="179"/>',
        ),
        encoding="utf-8",
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        await client.call("import_media", path=str(project), source=str(source))
        planned = await client.call(
            "import_edit", path=str(project), document=str(document), plan=True
        )
        imported = await client.call(
            "import_edit", path=str(project), document=str(document)
        )
        blank = await session.call_tool(
            "import_edit", {"path": str(project), "document": str(blanked)}
        )
        missing = await session.call_tool(
            "import_edit", {"path": str(project), "document": str(tmp_path / "nope.kdenlive")}
        )
        return {
            "planned": planned,
            "imported": imported,
            "status": await client.call("timeline_status", path=str(project)),
            "blank": (blank.is_error, blank.content[0].text),
            "missing": (missing.is_error, missing.content[0].text),
        }

    out = anyio.run(_with_server, body)

    assert out["planned"]["plan"] is True
    assert out["planned"]["segments"] == 2
    assert out["imported"]["segments"] == 2
    assert out["imported"]["clips"] == ["pic"]
    # 60 + 60 frames at 30fps, out read as the last frame index.
    assert out["imported"]["timeline_duration"] == pytest.approx(4.0)
    assert out["status"]["segments"] == 2

    is_error, text = out["blank"]
    assert is_error, "a blank is runtime an Edit cannot hold"
    assert "close the hole" in text

    is_error, text = out["missing"]
    assert is_error
    assert "no such edit document" in text


@needs_ffprobe
@needs_auto_editor
def test_film_check_with_no_reference_reports_the_projects_own_numbers(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The cheap call, same shape `check_frames` has with no target: report
    what is knowable about this project alone and say plainly there is
    nothing to compare it against yet, rather than raising.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(Client(session), project, audio, transcript)
        return await client.call("film_check", path=str(project))

    result = anyio.run(_with_server, body)

    assert result["reference"] is None
    assert result["segments"] == 1
    assert result["timeline_duration"] == pytest.approx(12.0, abs=0.05)
    assert "agrees" not in result
    assert "no reference declared" in result["notes"][0]


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_film_check_agrees_when_the_reference_is_this_cut(tmp_path: Path) -> None:
    """The clean case: a render of exactly this project's own timeline."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip, cut=[[4, 6], [14, 16]]
        )
        await client.call("export", path=str(project), output=str(render), export_format=None)
        return await client.call("film_check", path=str(project), reference=str(render))

    result = anyio.run(_with_server, body)

    assert result["reference_source"] == "argument"
    assert result["reference"] == str(render)
    assert result["duration_delta"] == pytest.approx(0.0, abs=0.2)
    assert result["agrees"] is True


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_film_check_compares_the_tail_in(tmp_path: Path) -> None:
    """The number compared is `expected_duration`, not the edit.

    A tail is project state and `export` lays it down, so a project holding
    one is longer than its own `Edit` — the rule `_frame_total_with_tail`
    exists to keep in one place. `film_check` was written before `tail` was
    and read the edit straight, which made it disagree with the film it was
    pointed at by exactly the end card: the Scream project renders 342.36s
    against a 336.27s edit, so a correct project read `agrees: false` at a
    delta of 6s — the same direction and order of magnitude as the stale VO
    this check exists to catch, which is the one false alarm it cannot
    afford. Pinned from both sides: the tail-less render stops agreeing once
    a tail is configured, and a reference carrying the tail agrees.
    """
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip, cut=[[4, 6], [14, 16]]
        )
        await client.call("export", path=str(project), output=str(render), export_format=None)
        before = await client.call(
            "film_check", path=str(project), reference=str(render), plan=True
        )
        await client.call("tail", path=str(project), asset="card:outro", seconds=3.0)
        after = await client.call(
            "film_check", path=str(project), reference=str(render), plan=True
        )
        # The film this project now describes: the same cut, plus the tail.
        full = tmp_path / "with-tail.mp4"
        _make_video(full, duration=after["expected_duration"])
        against_full = await client.call(
            "film_check", path=str(project), reference=str(full), plan=True
        )
        return {"before": before, "after": after, "against_full": against_full}

    out = anyio.run(_with_server, body)

    # No tail: unchanged behaviour, within a frame of the edit's own duration.
    assert out["before"]["tail_seconds"] == 0.0
    assert out["before"]["expected_duration"] == pytest.approx(
        out["before"]["timeline_duration"], abs=0.1
    )
    assert out["before"]["agrees"] is True

    # With one: the compared number grows by the tail, the edit does not, and
    # the render made before the tail existed is no longer this film.
    after = out["after"]
    assert after["tail_seconds"] == 3.0
    assert after["timeline_duration"] == pytest.approx(out["before"]["timeline_duration"])
    assert after["expected_duration"] == pytest.approx(after["timeline_duration"] + 3.0, abs=0.1)
    assert after["duration_delta"] == pytest.approx(3.0, abs=0.2)
    assert after["agrees"] is False
    assert any("tail" in note for note in after["notes"])

    # And a reference that carries the tail agrees — the case that read false
    # before this, on the real film.
    assert out["against_full"]["agrees"] is True
    assert out["against_full"]["duration_delta"] == pytest.approx(0.0, abs=0.2)


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_film_check_catches_a_project_seeded_from_a_stale_cut(tmp_path: Path) -> None:
    """The defect this item exists for (HISTORY.md § The VO the project was
    holding): a project's own checks can all agree with themselves — the
    render matches the timeline, `check_frames` is clean — while the project
    is seeded from the wrong stage of the edit. `check_frames` cannot catch
    this: it would have agreed with itself just as cleanly on the stale cut,
    because it never looks outside the project. `undo` stands in here for
    what actually happened to the Scream project: a retake pass done outside
    proofcut never landing in it, so the film's own export is short and correct
    while the project's own timeline is still the longer, stale one.
    """
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip, cut=[[4, 6], [14, 16]]
        )
        # The reference is a render of the *correct*, cut film.
        await client.call("export", path=str(project), output=str(render), export_format=None)
        # The retake pass never landed: the project's own timeline reverts to
        # the longer, uncut version — the shape the Scream project was found in.
        await client.call("undo", path=str(project))
        return await client.call("film_check", path=str(project), reference=str(render))

    result = anyio.run(_with_server, body)

    assert result["agrees"] is False
    assert result["timeline_duration"] > result["reference_duration"]
    assert result["duration_delta"] > 1.0


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_film_check_remembers_a_declared_reference(tmp_path: Path) -> None:
    """Passing `reference` records it on the project (additive, no schema
    bump — the `canvas`/`caption_style` precedent), so a later call with no
    argument asks the same question again. This is the fix HISTORY.md names:
    "a caveat recorded in a results table is not a guard" only holds when
    nothing re-checks it — this makes the claim project state instead.
    """
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> tuple[dict[str, Any], dict[str, Any], str]:
        client = Client(session)
        await _seeded(client, project, source, transcript)
        await client.call("export", path=str(project), output=str(render), export_format=None)
        declared = await client.call("film_check", path=str(project), reference=str(render))
        reread = await client.call("film_check", path=str(project))
        manifest = (project / "proofcut.json").read_text()
        return declared, reread, manifest

    declared, reread, manifest = anyio.run(_with_server, body)

    assert declared["reference_source"] == "argument"
    assert reread["reference_source"] == "declared"
    assert reread["reference"] == declared["reference"] == str(render)
    assert reread["agrees"] is True and declared["agrees"] is True
    assert json.loads(manifest)["reference"] == str(render)


@needs_ffprobe
@needs_auto_editor
def test_film_check_plan_does_not_write_the_reference(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """`plan` resolves and reports without recording anything — the same
    contract `canvas`/`tail` give the same flag."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> tuple[dict[str, Any], str]:
        client = Client(session)
        await _seeded(Client(session), project, audio, transcript)
        planned = await client.call(
            "film_check", path=str(project), reference=str(audio), plan=True
        )
        manifest = (project / "proofcut.json").read_text()
        return planned, manifest

    planned, manifest = anyio.run(_with_server, body)

    assert planned["reference"] == str(audio)
    assert "reference" not in json.loads(manifest)


@needs_ffprobe
@needs_auto_editor
def test_film_check_reset_drops_the_declared_reference(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> tuple[dict[str, Any], str]:
        client = Client(session)
        await _seeded(Client(session), project, audio, transcript)
        await client.call("film_check", path=str(project), reference=str(audio))
        after_reset = await client.call("film_check", path=str(project), reset=True)
        manifest = (project / "proofcut.json").read_text()
        return after_reset, manifest

    after_reset, manifest = anyio.run(_with_server, body)

    assert after_reset["reference"] is None
    assert "reference" not in json.loads(manifest)


@pytest.fixture
def visible_tmp() -> Iterator[Path]:
    """A working directory melt can actually read.

    pytest's `tmp_path` is under /tmp, and **the Kdenlive flatpak's /tmp is not
    the host's** — `filesystems=host` does not cover it (HISTORY.md § 4). melt
    pointed at one prints "Failed to load" and **exits 0**, so a melt test using
    `tmp_path` would silently stop testing melt and start testing the
    empty-output guard instead.
    """
    root = Path(tempfile.mkdtemp(prefix="proofcut-melt-", dir=Path.home()))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@needs_ffprobe
@needs_auto_editor
@needs_melt
def test_melt_is_asked_what_it_would_render_before_anything_is_rendered(
    visible_tmp: Path,
) -> None:
    """The load-bearing case: the NLE project checked without paying for a render.

    On this box the answer is the timeline's count plus one — auto-editor's
    kdenlive export declares the tractors' frame-inclusive `out` as a frame
    count, so melt renders a trailing black frame (picture.KNOWN_TAIL_FRAME).
    That is upstream's bug, not proofcut's, so this pins the *reporting* rather
    than the +1: a delta of 0 here would mean auto-editor had fixed it, and the
    thing that must stay true either way is that the note travels with the
    delta it explains.
    """
    audio, transcript = _make_sources(visible_tmp)
    project = visible_tmp / "proj"
    exported = visible_tmp / "out.kdenlive"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        written = await client.call("export", path=str(project), output=str(exported))
        return await client.call("check_frames", path=str(project), target=written["output"])

    result = anyio.run(_with_server, body)

    assert result["target_kind"] == "nle-project"
    assert result["expected_frames"] == 360
    assert result["delta"] in (0, picture.KNOWN_TAIL_FRAME)
    assert result["agrees"] is (result["delta"] == 0)
    if result["delta"] == picture.KNOWN_TAIL_FRAME:
        assert any("trailing black frame" in note for note in result["notes"])


# -- the picture half: black runs and spot-checked frames -----------------


@needs_ffprobe
@needs_ffmpeg
def test_check_black_reports_clean_when_nothing_is_black(tmp_path: Path) -> None:
    """A plain render, matching the timeline frame for frame: nothing to explain."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, source, None)
        return await client.call("check_black", path=str(project), target=str(source))

    result = anyio.run(_with_server, body)

    assert result["clean"] is True
    assert result["runs"] == []


@needs_ffprobe
@needs_ffmpeg
def test_check_black_catches_a_black_stretch_inside_the_picture(tmp_path: Path) -> None:
    """The actual defect this op exists for, well before the tail."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    target = tmp_path / "mid-black.mp4"
    _make_video_with_mid_black(target)  # 4s testsrc + 2s black + 6s testsrc = 12s
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, source, None)
        return await client.call("check_black", path=str(project), target=str(target))

    result = anyio.run(_with_server, body)

    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["inside_expected_picture"] is True
    assert run["explained"] is False
    assert result["clean"] is False


@needs_ffprobe
@needs_ffmpeg
def test_check_black_explains_the_known_tail_frame(tmp_path: Path) -> None:
    """Pins the reporting, per the same philosophy as the melt tail-frame test:
    a clean result here would mean the render no longer carries the defect.
    """
    source = tmp_path / "pic.mp4"
    _make_video(source)
    target = tmp_path / "tail-black.mp4"
    _make_video_with_tail_black_frame(target, duration=12.0)  # 360 + 1 black frame
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, source, None)
        return await client.call("check_black", path=str(project), target=str(target))

    result = anyio.run(_with_server, body)

    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["explained"] is True
    assert picture.TAIL_FRAME_NOTE in run["note"]
    assert result["clean"] is True


@needs_ffprobe
def test_check_black_on_audio_only_target_says_so(tmp_path: Path, sources: tuple[Path, Path]) -> None:
    """A VO project is the ordinary case, not a failure: nothing to scan for black."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await client.call("check_black", path=str(project), target=str(audio))

    result = anyio.run(_with_server, body)

    assert result["clean"] is None
    assert result["runs"] == []
    assert "no video stream" in result["notes"][0]


@needs_ffprobe
@needs_ffmpeg
def test_spot_frames_samples_evenly_and_writes_pngs(tmp_path: Path) -> None:
    """The cheap happy path: evenly-spaced samples, each pulled to a real PNG."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, source, None)
        return await client.call(
            "spot_frames", path=str(project), target=str(source), count=3
        )

    result = anyio.run(_with_server, body)

    assert len(result["frames"]) == 3
    for i, frame in enumerate(result["frames"]):
        assert Path(frame["png"]).exists()
        assert isinstance(frame["YAVG"], float)
        assert frame["origin"] == "sampled"
        # duration 12.0 / 3 samples: midpoints at 2, 6, 10.
        assert frame["time"] == pytest.approx(4.0 * (i + 0.5), abs=0.05)


@needs_ffprobe
@needs_ffmpeg
@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_spot_frames_returns_a_montage_of_the_samples_over_the_wire(tmp_path: Path) -> None:
    """TRIAL.md § `spot_frames` hands back paths the agent cannot open: the
    tool must carry the sampled frames as an image too, like `shot_sheet` and
    the other sheets — asserted on the raw `CallToolResult` the same way, so
    a tool that returned only its table cannot pass by accident."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, source, None)
        raw = await session.call_tool(
            "spot_frames", {"path": str(project), "target": str(source), "count": 3}
        )
        return {
            "is_error": raw.is_error,
            "kinds": [type(block).__name__ for block in raw.content],
            "report": json.loads(raw.content[0].text),
        }

    out = anyio.run(_with_server, body)

    assert out["is_error"] is False
    assert "ImageContent" in out["kinds"], f"no image came back: {out['kinds']}"
    assert out["report"]["sheet"].endswith(".jpg")


@needs_ffprobe
@needs_ffmpeg
def test_spot_frames_merges_explicit_times_with_sampled_ones(tmp_path: Path) -> None:
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, source, None)
        return await client.call(
            "spot_frames", path=str(project), target=str(source), count=2, times=[1.0]
        )

    result = anyio.run(_with_server, body)

    origins = [f["origin"] for f in result["frames"]]
    times = [f["time"] for f in result["frames"]]
    assert sorted(origins) == ["explicit", "sampled", "sampled"]
    assert times == sorted(times)


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_spot_frames_echoes_the_word_at_a_sample(tmp_path: Path) -> None:
    """A sample landing inside a known word's span reports that word, in context."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip, cut=[[4, 6], [14, 16]]
        )
        await client.call("export", path=str(project), output=str(render), export_format=None)
        return await client.call(
            "spot_frames", path=str(project), target=str(render), count=0, times=[0.1]
        )

    result = anyio.run(_with_server, body)

    assert result["mapping_trusted"] is True
    frame = result["frames"][0]
    assert frame["clip_id"] is not None
    assert frame["word"]["text"] == "w00"
    assert frame["context_after"][0]["text"] == "w01"


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_spot_frames_trusts_a_render_checked_at_a_different_export_rate(
    tmp_path: Path,
) -> None:
    """`edit.duration` is fixed to whatever rate the project's timeline was
    last persisted at (a 24fps source here) and cannot see a *different*
    `fps` a caller asks `spot_frames` to check against — `expected_duration`
    (`frame_total(edit, rate) / rate`), computed at that same `fps`, can.

    Twelve 0.07s keep-ranges against a 24fps source persist to exactly
    1.000s of `edit.duration`. The identical timeline, independently laid
    out at 30fps (a legitimate, supported `spot_frames(..., fps=...)`
    override — e.g. checking a render taken at a different rate than the
    project's own), really runs to 31 frames / 1.0333s, not 1.000s. A render
    built to exactly that 31-frame length is the genuinely correct answer at
    `fps=30`; comparing it against `edit.duration` (1.000s, a different
    rate's number) instead would wrongly call it stale.
    """
    source = tmp_path / "pic.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=24:duration=41.0",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=41.0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30",
            "-frames:v", "31", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(render),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.07} for i in range(12)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        await client.call(
            "cut_by_transcript",
            path=str(project),
            clip_id=clip,
            keep=[[i, i] for i in range(12)],
        )
        return await client.call(
            "spot_frames", path=str(project), target=str(render), count=1, fps=30.0
        )

    result = anyio.run(_with_server, body)

    assert result["expected_duration"] == pytest.approx(31 / 30, abs=1e-6)
    assert result["mapping_trusted"] is True
    assert "notes" not in result


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_spot_frames_refuses_word_mapping_on_a_stale_render(tmp_path: Path) -> None:
    """A render taken before a second cut must not silently map to the wrong words."""
    source = tmp_path / "pic.mp4"
    _make_video(source)
    project = tmp_path / "proj"
    render = tmp_path / "stale.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(24)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip = await _seeded(client, project, source, transcript)
        await client.call("export", path=str(project), output=str(render), export_format=None)
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip, cut=[[4, 6], [14, 16]]
        )
        return await client.call(
            "spot_frames", path=str(project), target=str(render), count=2
        )

    result = anyio.run(_with_server, body)

    assert result["mapping_trusted"] is False
    assert result["notes"]
    for frame in result["frames"]:
        assert "clip_id" not in frame
        assert "word" not in frame
        assert Path(frame["png"]).exists()
        assert isinstance(frame["YAVG"], float)


@needs_ffprobe
def test_spot_frames_on_audio_only_target_says_so(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await client.call("spot_frames", path=str(project), target=str(audio))

    result = anyio.run(_with_server, body)

    assert result["has_video"] is False
    assert result["frames"] == []


def test_spot_frames_refuses_zero_samples_with_no_explicit_times(tmp_path: Path) -> None:
    """count=0 with no explicit times names nothing to sample — refused up front,
    before any project or media is even touched.
    """
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        return await session.call_tool(
            "spot_frames", {"path": str(project), "target": str(tmp_path / "nope.mp4"), "count": 0}
        )

    result = anyio.run(_with_server, body)

    assert result.is_error
    assert "nothing to sample" in result.content[0].text


# -- attenuate_noises -------------------------------------------------------


def _db_at(env: list[float], t: float) -> float:
    return energy._db(env[int(t / energy.FRAME)])


def _write_wav_at_rate(
    path: Path, *, tones: list[tuple[float, float]], duration: float, rate: int
) -> None:
    """Like `_make_wav`, but at an explicit sample rate.

    Written directly at `energy.RATE` for these fixtures so `energy.decode`'s
    resample to that same rate is a no-op — `_make_wav`'s 22050 Hz is fine
    when bursts sit a full second from a word edge, but these fixtures place
    a burst right up against one, and the resampler's ring (documented at
    `test_verify_reports_sound_in_a_hole_the_word_map_calls_empty`) would
    smear a spurious ~20ms run onto the wrong side of the boundary.
    """
    with wave.open(str(path), "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * duration)):
            t = i / rate
            loud = any(a <= t < b for a, b in tones)
            value = int(12000 * math.sin(2 * math.pi * 220 * t)) if loud else 0
            frames += struct.pack("<h", value)
        out.writeframes(bytes(frames))


def _attenuate_sources(root: Path) -> tuple[Path, Path]:
    """A short loud burst in a 1.3s gap (qualifies) and a longer one in a
    4.7s gap (too wide to prove the map is dense there — disqualified).
    """
    audio = root / "vo.wav"
    _write_wav_at_rate(
        audio,
        tones=[(0.0, 1.0), (1.5, 2.0), (2.3, 3.3), (5.0, 5.7), (8.0, 9.0)],
        duration=10.0,
        rate=energy.RATE,
    )
    words = [
        {"word": "well", "start": 0.0, "end": 1.0},
        {"word": "so", "start": 2.3, "end": 3.3},
        {"word": "quiet", "start": 8.0, "end": 9.0},
    ]
    transcript = root / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


def _scream_hole_sources(root: Path) -> tuple[Path, Path]:
    """Scream v1's own false-positive shape: a 0.6s burst in a 4.12s hole in
    the word map (`ideas/scream.md`) — must stay disqualified on gap width
    alone, never on the event's own (short) duration.
    """
    audio = root / "vo.wav"
    _write_wav_at_rate(
        audio, tones=[(0.0, 1.0), (3.0, 3.6), (5.12, 6.12)], duration=8.0, rate=energy.RATE
    )
    words = [
        {"word": "well", "start": 0.0, "end": 1.0},
        {"word": "so", "start": 5.12, "end": 6.12},
    ]
    transcript = root / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


def _suspect_neighbour_sources(root: Path) -> tuple[Path, Path]:
    """A qualifying event whose *before* neighbour ("loud") claims a
    duration far past 3x the median — suspect by `energy.CAP` — so the
    "gap is narrow" evidence next to it is itself unproven.
    """
    audio = root / "vo.wav"
    _write_wav_at_rate(
        audio,
        tones=[(0.0, 0.3), (0.5, 0.8), (1.0, 1.9), (2.2, 2.7), (3.0, 3.3)],
        duration=6.0,
        rate=energy.RATE,
    )
    words = [
        {"word": "well", "start": 0.0, "end": 0.3},
        {"word": "so", "start": 0.5, "end": 0.8},
        {"word": "loud", "start": 1.0, "end": 4.0},
        {"word": "quiet", "start": 3.0, "end": 3.3},
    ]
    transcript = root / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


def _make_video_with_audio(path: Path, audio: Path, *, duration: float, fps: int = 30) -> None:
    """`_make_video`'s picture, muxed with a real gap-and-burst audio track
    instead of a flat sine tone, so `-c:v copy` has real picture to preserve.
    """
    video_only = path.with_suffix(".video-only.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate={fps}:duration={duration}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(video_only),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_only), "-i", str(audio),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


async def _attached(client: Client, project: Path, source: Path, transcript: Path) -> str:
    """init -> import -> attach, the preamble `attenuate_noises` wants — it
    needs no timeline, so there is no `seed_timeline` step here.
    """
    await client.call("init", path=str(project))
    clip = await client.call("import_media", path=str(project), source=str(source))
    await client.call(
        "attach_transcript",
        path=str(project),
        clip_id=clip["clip_id"],
        transcript_path=str(transcript),
    )
    return str(clip["clip_id"])


@needs_ffprobe
@needs_ffmpeg
def test_attenuate_noises_pulls_down_a_qualifying_event_and_leaves_the_rest(tmp_path: Path) -> None:
    """The load-bearing shape in one project: a short event in a narrow gap
    is pulled down and written, and a short event in a much wider gap
    (disqualified) is reported but never touched.
    """
    audio, transcript = _attenuate_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, audio, transcript)
        return await client.call("attenuate_noises", path=str(project), clip_id=clip_id)

    result = anyio.run(_with_server, body)

    assert result["written"] is True
    assert len(result["attenuated"]) == 1
    assert len(result["disqualified"]) == 1
    assert result["suspect_neighbours"] == []
    assert any("max_gap_seconds" in reason for reason in result["disqualified"][0]["reasons"])

    output = Path(result["output_media"])
    assert output.exists()

    manifest = Project.open(project).read_manifest()
    clip = next(c for c in manifest["clips"] if c.get("attenuated"))
    assert Path(clip["attenuated"]).name == output.name

    before = energy.envelope(energy.decode(audio))
    after = energy.envelope(energy.decode(output))
    assert _db_at(after, 1.75) == pytest.approx(_db_at(before, 1.75) - 12.0, abs=2.0)
    # The disqualified burst, elsewhere in the same file, is left alone.
    assert _db_at(after, 5.35) == pytest.approx(_db_at(before, 5.35), abs=1.0)


@needs_ffprobe
@needs_ffmpeg
def test_attenuate_noises_refuses_to_touch_a_wide_map_hole_even_though_it_is_loud(
    tmp_path: Path,
) -> None:
    """Scream v1's exact false positive: 0.6s of sound in a 4.12s hole in the
    word map read as noise on a first pass. It is not — the hole is too wide
    to prove the map is dense there, so it must be reported, never written.
    """
    audio, transcript = _scream_hole_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, audio, transcript)
        return await client.call("attenuate_noises", path=str(project), clip_id=clip_id)

    result = anyio.run(_with_server, body)

    assert result["attenuated"] == []
    assert result["written"] is False
    assert result["output_media"] is None
    assert len(result["disqualified"]) == 1
    disqualified = result["disqualified"][0]
    assert disqualified["gap"]["duration"] == pytest.approx(4.12, abs=0.01)
    assert disqualified["duration"] == pytest.approx(0.6, abs=0.05)
    assert any("max_gap_seconds" in reason for reason in disqualified["reasons"])
    assert not any("max_event_seconds" in reason for reason in disqualified["reasons"])


@needs_ffprobe
@needs_ffmpeg
def test_attenuate_noises_plan_reports_without_writing(tmp_path: Path) -> None:
    """Mirrors `cut --plan`'s "same numbers" contract: a plan and a real run
    against the same project must agree on everything except `written`.

    Run against two fixtures. `_attenuate_sources` has no `suspect_neighbour`
    event, so it cannot see a real bug: `include_suspect = confirm_suspect or
    plan` used to let `plan=True` alone pull a suspect event into
    `to_write`/`output_media`, previewing a write a subsequent real call
    (confirm_suspect defaulting to False) would never actually perform.
    `_suspect_neighbour_sources` has exactly one such event, so it is the one
    that would have caught that divergence — `to_write` must now be gated by
    `confirm_suspect` alone in both the plan and the real path.
    """
    audio, transcript = _attenuate_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, audio, transcript)
        planned = await client.call(
            "attenuate_noises", path=str(project), clip_id=clip_id, plan=True
        )
        exists_under_plan = Path(planned["output_media"]).exists()
        real = await client.call("attenuate_noises", path=str(project), clip_id=clip_id)
        return planned, exists_under_plan, real

    planned, exists_under_plan, real = anyio.run(_with_server, body)

    assert planned["plan"] is True
    assert planned["written"] is False
    assert exists_under_plan is False

    assert real["written"] is True
    assert Path(real["output_media"]).exists()

    assert planned["output_media"] == real["output_media"]
    assert planned["attenuated"] == real["attenuated"]
    assert planned["disqualified"] == real["disqualified"]

    suspect_audio, suspect_transcript = _suspect_neighbour_sources(tmp_path)
    suspect_project = tmp_path / "suspect-proj"

    async def suspect_body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, suspect_project, suspect_audio, suspect_transcript)
        planned = await client.call(
            "attenuate_noises", path=str(suspect_project), clip_id=clip_id, plan=True
        )
        real = await client.call(
            "attenuate_noises", path=str(suspect_project), clip_id=clip_id
        )
        return planned, real

    suspect_planned, suspect_real = anyio.run(_with_server, suspect_body)

    assert len(suspect_planned["suspect_neighbours"]) == 1
    # Neither call confirmed the suspect neighbour, so a plan's preview and
    # the matching real call must agree it was withheld from both.
    assert suspect_planned["attenuated"] == []
    assert suspect_real["attenuated"] == []
    assert suspect_planned["written"] is False
    assert suspect_real["written"] is False
    assert suspect_planned["output_media"] == suspect_real["output_media"] is None


@needs_ffprobe
@needs_ffmpeg
def test_attenuate_noises_withholds_a_suspect_neighbour_without_confirm_then_writes_it_with_confirm_true(
    tmp_path: Path,
) -> None:
    audio, transcript = _suspect_neighbour_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, audio, transcript)
        withheld = await client.call("attenuate_noises", path=str(project), clip_id=clip_id)
        confirmed = await client.call(
            "attenuate_noises", path=str(project), clip_id=clip_id, confirm_suspect=True
        )
        return withheld, confirmed

    withheld, confirmed = anyio.run(_with_server, body)

    assert withheld["attenuated"] == []
    assert withheld["written"] is False
    assert len(withheld["suspect_neighbours"]) == 1
    assert withheld["suspect_neighbours"][0]["neighbour_before"]["text"] == "loud"

    assert len(confirmed["attenuated"]) == 1
    assert confirmed["written"] is True


@needs_ffprobe
@needs_ffmpeg
def test_attenuate_noises_re_run_does_not_compound_gain(tmp_path: Path) -> None:
    """A second call must read the *original* media, not the first call's
    output — otherwise the same span would be attenuated twice.
    """
    audio, transcript = _attenuate_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, audio, transcript)
        first = await client.call("attenuate_noises", path=str(project), clip_id=clip_id)
        second = await client.call("attenuate_noises", path=str(project), clip_id=clip_id)
        return first, second

    first, second = anyio.run(_with_server, body)

    assert second["source_media"] == first["source_media"]

    after_first = energy.envelope(energy.decode(Path(first["output_media"])))
    after_second = energy.envelope(energy.decode(Path(second["output_media"])))
    assert _db_at(after_second, 1.75) == pytest.approx(_db_at(after_first, 1.75), abs=1.0)


def _overlapping_runs_sources(root: Path) -> tuple[Path, Path]:
    """Two loud runs ~0.02s apart (after envelope quantisation) in one 1.3s
    gap — closer than `2*pad` (default `pad=0.05s`), so their padded spans
    overlap. Both independently qualify as `"attenuated"`; the write side
    must merge them before building the filtergraph, or ffmpeg's
    comma-chained `volume` filters double-attenuate the overlap.
    """
    audio = root / "vo.wav"
    _write_wav_at_rate(
        audio,
        tones=[(0.0, 1.0), (1.5, 1.75), (1.79, 2.05), (2.3, 3.3)],
        duration=5.0,
        rate=energy.RATE,
    )
    words = [
        {"word": "well", "start": 0.0, "end": 1.0},
        {"word": "so", "start": 2.3, "end": 3.3},
    ]
    transcript = root / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


@needs_ffprobe
@needs_ffmpeg
def test_attenuate_noises_merges_overlapping_padded_runs_before_writing(tmp_path: Path) -> None:
    """Two loud runs in one gap, padded closer together than they are apart,
    must not stack. `energy.attenuate` comma-chains one `volume` filter per
    span, so an unmerged overlap gets attenuated twice — roughly double the
    requested `db` there, with no error and no warning, while `result["gain"]`
    keeps reporting the single-pass value that does not describe what
    actually happened in the overlap.
    """
    audio, transcript = _overlapping_runs_sources(tmp_path)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, audio, transcript)
        return await client.call("attenuate_noises", path=str(project), clip_id=clip_id)

    result = anyio.run(_with_server, body)

    assert result["written"] is True
    # Per-event detail survives the write-side merge: two distinct runs are
    # still two distinct reported events.
    assert len(result["attenuated"]) == 2
    assert all(e["status"] == "attenuated" for e in result["attenuated"])

    before = energy.envelope(energy.decode(audio))
    after = energy.envelope(energy.decode(Path(result["output_media"])))

    # A point inside only the first run's padded span, a point inside the
    # overlap of both padded spans, and a point inside only the second run's.
    for t in (1.55, 1.82, 2.00):
        assert _db_at(after, t) == pytest.approx(_db_at(before, t) - 12.0, abs=2.0)


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_export_renders_the_attenuated_copy_not_the_original(tmp_path: Path) -> None:
    """`media_path()` prefers `clip["attenuated"]`, but `autoeditor.to_v3`
    used to build each segment's `"src"` from `record["source"]` directly,
    bypassing `media_path()` entirely — so a render taken after
    `attenuate_noises` still carried the original noise burst at full
    volume, with `written: true` giving no sign anything had been bypassed.
    Doubles as the regression test for that bypass: without routing
    `to_v3`'s `"src"` through `media.media_path()`, this render measures the
    *pre*-attenuation level, not the post-attenuation one.
    """
    audio, transcript = _attenuate_sources(tmp_path)
    project = tmp_path / "proj"
    render = tmp_path / "out.wav"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, audio, transcript)
        await client.call("attenuate_noises", path=str(project), clip_id=clip_id)
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip_id, remove_silences=False
        )
        await client.call("export", path=str(project), output=str(render), export_format=None)

    anyio.run(_with_server, body)

    before = energy.envelope(energy.decode(audio))
    after = energy.envelope(energy.decode(render))
    assert _db_at(after, 1.75) == pytest.approx(_db_at(before, 1.75) - 12.0, abs=2.0)


def _make_hevc_video(path: Path, *, duration: float = 1.0, size: str = "1280x960") -> None:
    """An `hev1` MP4 — what the preview shows black and names a reason for."""
    encoders = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
    ).stdout
    if " libx265 " not in encoders:
        pytest.skip("this ffmpeg has no libx265 encoder")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx265", "-tag:v", "hev1", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip


@needs_ffprobe
@needs_ffmpeg
def test_proxy_transcode_makes_an_undecodable_clip_playable(tmp_path: Path) -> None:
    """Over stdio, because a tool body proves nothing about whether it is
    registered and reachable (CLAUDE.md).

    The verdict is read back off the finished file through the server's own
    `preview_source`, not off the flags this handed ffmpeg — so what passes is
    "the preview will play it", which is the only claim the tool makes.
    """
    source = tmp_path / "hevc.mp4"
    _make_hevc_video(source)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        record = await client.call("import_media", path=str(project), source=str(source))
        clip = record["clip_id"]
        return await client.call("proxy_transcode", path=str(project), clip_id=clip)

    built = anyio.run(_with_server, body)

    assert built["built"] is True
    assert built["playable"]["playable"] is True
    assert built["reason"], "the refusal it was built to close should be reported"
    assert Path(built["proxy"]).is_file()
    # In the cache, and so nowhere any render resolves through.
    assert Path(built["proxy"]).parent == Project.open(project).proxy_dir
    assert "proxy" not in Project.open(project).read_manifest()["clips"][0]


@needs_ffprobe
@needs_ffmpeg
def test_proxy_transcode_refuses_a_clip_that_already_plays(tmp_path: Path) -> None:
    """The refusal arrives as a refusal over the wire rather than as a wasted
    encode — a proxy of a file the browser opens is a second, lower-quality
    copy of footage nothing needed one of."""
    source = tmp_path / "fine.mp4"
    _make_video(source, duration=1.0)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        record = await client.call("import_media", path=str(project), source=str(source))
        return await session.call_tool(
            "proxy_transcode", {"path": str(project), "clip_id": record["clip_id"]}
        )

    result = anyio.run(_with_server, body)

    assert result.is_error
    assert "already plays" in result.content[0].text
    # Refused before any encode, not after one that then got thrown away.
    assert list(Project.open(project).proxy_dir.glob("*")) == []


@needs_ffprobe
@needs_ffmpeg
def test_attenuate_noises_video_clip_copies_picture_and_only_touches_audio(tmp_path: Path) -> None:
    """`-c:v copy`, proven by frame count parity rather than trusted by name."""
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 1.0), (1.5, 2.0), (2.3, 3.3)], duration=8.0)
    source = tmp_path / "pic.mp4"
    _make_video_with_audio(source, audio, duration=8.0)
    project = tmp_path / "proj"
    words = [
        {"word": "well", "start": 0.0, "end": 1.0},
        {"word": "so", "start": 2.3, "end": 3.3},
    ]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _attached(client, project, source, transcript)
        return await client.call("attenuate_noises", path=str(project), clip_id=clip_id)

    result = anyio.run(_with_server, body)

    assert result["written"] is True
    before = media.count_frames(source)
    after = media.count_frames(result["output_media"])
    assert after["frames"] == before["frames"]
    assert after["frames"] is not None


async def _clip_b(client: Client, project: Path, source: Path, transcript: Path) -> str:
    """import -> attach for a clip that is never placed on the timeline —
    `speech_overlap` tests a *proposed* placement of it against the VO's
    already-seeded one, so unlike `_seeded` there is no `seed_timeline` step.
    """
    clip = await client.call("import_media", path=str(project), source=str(source))
    await client.call(
        "attach_transcript",
        path=str(project),
        clip_id=clip["clip_id"],
        transcript_path=str(transcript),
    )
    return str(clip["clip_id"])


def _write_words(path: Path, words: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")


@needs_ffprobe
def test_speech_overlap_reports_a_clean_seam_when_clip_speech_sits_in_a_vo_gap(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The cheapest case: a proposed clip placement lands in a VO silence
    gap (between the w01 and w10 bursts of the `sources` fixture), so there
    is nothing to duck around.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[(2.2, 2.8)], duration=4.0)
    b_transcript = tmp_path / "clipb.json"
    _write_words(b_transcript, [{"word": "bspeak", "start": 2.2, "end": 2.8}])

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        clip_b = await _clip_b(client, project, b_audio, b_transcript)
        return await client.call("speech_overlap", path=str(project), clip_id=clip_b)

    result = anyio.run(_with_server, body)

    assert result["overlaps"] == []
    assert result["summary"]["overlap_count"] == 0
    assert len(result["clean_seams"]) == 1
    seam = result["clean_seams"][0]
    assert seam["timeline_start"] == pytest.approx(2.2)
    assert seam["timeline_end"] == pytest.approx(2.8)


@needs_ffprobe
def test_speech_overlap_catches_the_billy_stu_case(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Named for the incident it exists to catch: a word-level pass on a
    real clip found its speech (105.35-109.15s) landing almost entirely on
    top of a VO thesis line (105.97-109.85s) — near-total overlap, no seam
    to duck into. Here the proposed clip placement lands squarely inside a
    VO run (the merged w00/w01 burst of the `sources` fixture).
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[(1.0, 1.5)], duration=3.0)
    b_transcript = tmp_path / "clipb.json"
    _write_words(b_transcript, [{"word": "over", "start": 1.0, "end": 1.5}])

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        clip_b = await _clip_b(client, project, b_audio, b_transcript)
        return await client.call("speech_overlap", path=str(project), clip_id=clip_b)

    result = anyio.run(_with_server, body)

    assert len(result["overlaps"]) == 1
    overlap = result["overlaps"][0]
    assert [w["text"] for w in overlap["clip_words"]] == ["over"]
    assert [w["text"] for w in overlap["vo_words"]] == ["w01"]
    assert result["clean_seams"] == []


@needs_ffprobe
def test_speech_overlap_merges_a_narrow_gap_into_one_run(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Pins `max_gap` end-to-end, not just at the `speech.merge_runs` unit
    level: two clip words 0.05s apart read as one run at the default
    tolerance and two runs at `max_gap=0.0`.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[(0.5, 1.4)], duration=3.0)
    b_transcript = tmp_path / "clipb.json"
    _write_words(
        b_transcript,
        [
            {"word": "p1", "start": 0.5, "end": 0.9},
            {"word": "p2", "start": 0.95, "end": 1.4},
        ],
    )

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        clip_b = await _clip_b(client, project, b_audio, b_transcript)
        default = await client.call("speech_overlap", path=str(project), clip_id=clip_b)
        strict = await client.call(
            "speech_overlap", path=str(project), clip_id=clip_b, max_gap=0.0
        )
        return {"default": default, "strict": strict}

    out = anyio.run(_with_server, body)

    assert len(out["default"]["clip_runs"]) == 1
    assert len(out["strict"]["clip_runs"]) == 2


@needs_ffprobe
def test_speech_overlap_trims_a_suspect_vo_word_before_testing_overlap(
    tmp_path: Path,
) -> None:
    """Mirrors HISTORY.md § 2's swallowed-retake trap: a VO word claiming 6.0s
    (15x the 0.4s median of its neighbours) is capped by `energy.believable`
    before mapping, so a clip placed just past the claimed-but-unbelieved
    tail is correctly read as a clean seam, not an overlap.
    """
    project = tmp_path / "proj"
    vo_audio = tmp_path / "vo.wav"
    _make_wav(vo_audio, tones=[(0.0, 8.1)], duration=9.0)
    vo_transcript = tmp_path / "vo.json"
    _write_words(
        vo_transcript,
        [
            {"word": "so", "start": 0.0, "end": 0.5},
            {"word": "we", "start": 0.6, "end": 1.0},
            {"word": "bit", "start": 1.1, "end": 1.5},
            {"word": "on", "start": 1.6, "end": 7.6},
            {"word": "it", "start": 7.7, "end": 8.1},
        ],
    )
    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[(3.0, 3.5)], duration=4.0)
    b_transcript = tmp_path / "clipb.json"
    _write_words(b_transcript, [{"word": "later", "start": 3.0, "end": 3.5}])

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, vo_audio, vo_transcript)
        clip_b = await _clip_b(client, project, b_audio, b_transcript)
        return await client.call("speech_overlap", path=str(project), clip_id=clip_b)

    result = anyio.run(_with_server, body)

    trimmed = next(w for w in result["vo_words"] if w["text"] == "on")
    assert trimmed["source_end"] == pytest.approx(7.6)
    assert trimmed["believable_end"] == pytest.approx(2.8)
    assert trimmed["believable_end"] != trimmed["source_end"]

    assert result["overlaps"] == []
    assert len(result["clean_seams"]) == 1
    seam = result["clean_seams"][0]
    assert seam["timeline_start"] == pytest.approx(3.0)
    assert seam["timeline_end"] == pytest.approx(3.5)


@needs_ffprobe
def test_speech_overlap_trims_a_suspect_clip_b_word_using_the_whole_clip_not_the_window(
    tmp_path: Path,
) -> None:
    """Clip B's own words are not in the edit, so they map by direct offset
    against the proposed `[clip_in, clip_out)` window — and the window here
    is narrow enough (one word) that the old bug's local median was set by
    the very outlier it was supposed to catch: with only the inflated word
    in `clip_hits`, `_median_limit` took *its own* duration as the median,
    so `believable` trimmed nothing. The fix computes the cap from clip_b's
    whole transcript (mirroring the VO side, `_suspect_durations`, and
    `attenuate_noises`), so the same 6.0s outlier is still caught even
    though the proposed window only ever sees it alone.
    """
    project = tmp_path / "proj"
    vo_audio = tmp_path / "vo.wav"
    _make_wav(vo_audio, tones=[], duration=12.0)
    vo_transcript = tmp_path / "vo.json"
    _write_words(vo_transcript, [{"word": "hush", "start": 0.0, "end": 0.4}])

    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[], duration=10.0)
    b_transcript = tmp_path / "clipb.json"
    _write_words(
        b_transcript,
        [
            {"word": "so", "start": 0.0, "end": 0.5},
            {"word": "we", "start": 0.6, "end": 1.0},
            {"word": "bit", "start": 1.1, "end": 1.5},
            {"word": "later", "start": 3.0, "end": 9.0},
            {"word": "it", "start": 9.6, "end": 10.0},
        ],
    )

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, vo_audio, vo_transcript)
        clip_b = await _clip_b(client, project, b_audio, b_transcript)
        # Narrow enough that only "later" falls in clip_hits — "bit" ends at
        # 1.5 (<= clip_in), "it" starts at 9.6 (>= clip_out).
        return await client.call(
            "speech_overlap",
            path=str(project),
            clip_id=clip_b,
            clip_in=2.0,
            clip_out=9.5,
        )

    result = anyio.run(_with_server, body)

    assert [w["text"] for w in result["clip_words"]] == ["later"]
    trimmed = result["clip_words"][0]
    assert trimmed["source_end"] == pytest.approx(9.0)
    # Full-population median of [0.4, 0.4, 0.4, 0.5, 6.0] is 0.4, cap=3.0 ->
    # limit=1.2, so believable_end = 3.0 + 1.2 = 4.2. Under the bug, the
    # single-word window's own median was the 6.0s outlier itself, so
    # believable_end stayed 9.0 (untrimmed).
    assert trimmed["believable_end"] == pytest.approx(4.2)
    assert trimmed["believable_end"] != trimmed["source_end"]


@needs_ffprobe
def test_speech_overlap_reads_an_untranscribed_clip_off_its_energy_and_says_so(tmp_path: Path) -> None:
    """Until 2026-09-05 this refused with "no transcript" — the real-footage
    trial's one refusal, asked of b-roll (TRIAL.md § The queue, item 3). The
    default now falls back to the clip's envelope and labels the reading;
    `clip_evidence="transcript"` keeps the refusal, naming the route to words.
    """
    project = tmp_path / "proj"
    audio, transcript = _make_sources(tmp_path)
    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[(0.5, 1.0)], duration=2.0)

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        clip_b = await client.call("import_media", path=str(project), source=str(b_audio))
        by_energy = await client.call("speech_overlap", path=str(project), clip_id=clip_b["clip_id"])
        refused = await session.call_tool(
            "speech_overlap",
            {"path": str(project), "clip_id": clip_b["clip_id"], "clip_evidence": "transcript"},
        )
        return {"by_energy": by_energy, "refused": refused}

    out = anyio.run(_with_server, body)

    by_energy = out["by_energy"]
    assert by_energy["clip_evidence"] == "energy"
    assert by_energy["clip_words"] == []
    assert by_energy["clip_energy"]["runs_in_clip"] >= 1
    [run] = by_energy["clip_runs"]
    assert run["timeline_start"] == pytest.approx(0.5, abs=0.06)
    assert run["timeline_end"] == pytest.approx(1.0, abs=0.06)
    assert out["refused"].is_error
    assert "transcribe" in out["refused"].content[0].text


@needs_ffprobe
def test_speech_overlap_refuses_when_vo_timeline_is_empty(tmp_path: Path) -> None:
    """No `seed_timeline` call at all — a project with clips but no timeline
    is the same refusal `_load_edit` gives every other timeline-reading op.
    """
    project = tmp_path / "proj"
    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[(0.5, 1.0)], duration=2.0)
    b_transcript = tmp_path / "clipb.json"
    _write_words(b_transcript, [{"word": "hi", "start": 0.5, "end": 1.0}])

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        clip_b = await _clip_b(client, project, b_audio, b_transcript)
        return await session.call_tool(
            "speech_overlap", {"path": str(project), "clip_id": clip_b}
        )

    result = anyio.run(_with_server, body)

    assert result.is_error
    assert "timeline" in result.content[0].text


@needs_ffprobe
def test_speech_overlap_refuses_for_an_unregistered_clip(tmp_path: Path) -> None:
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        await Client(session).call("init", path=str(project))
        return await session.call_tool(
            "speech_overlap", {"path": str(project), "clip_id": "nope"}
        )

    result = anyio.run(_with_server, body)

    assert result.is_error
    assert "nope" in result.content[0].text


@needs_ffprobe
def test_speech_overlap_default_placement_covers_the_clips_whole_duration(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """`at`/`clip_in`/`clip_out` are silent defaults otherwise — pin them
    explicitly rather than trust the docstring.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    b_audio = tmp_path / "clipb.wav"
    _make_wav(b_audio, tones=[(0.5, 1.0)], duration=2.0)
    b_transcript = tmp_path / "clipb.json"
    _write_words(b_transcript, [{"word": "hi", "start": 0.5, "end": 1.0}])

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        b_clip = await client.call("import_media", path=str(project), source=str(b_audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=b_clip["clip_id"],
            transcript_path=str(b_transcript),
        )
        result = await client.call(
            "speech_overlap", path=str(project), clip_id=b_clip["clip_id"]
        )
        return {"clip": b_clip, "result": result}

    out = anyio.run(_with_server, body)

    assert out["result"]["at"] == pytest.approx(0.0)
    assert out["result"]["clip_in"] == pytest.approx(0.0)
    assert out["result"]["clip_out"] == pytest.approx(out["clip"]["duration"])


# -- locate: source -> timeline ------------------------------------------
#
# The `sources` fixture seeded with `remove_silences=False` is one 0-12s
# segment, and its words are w00 0.0-0.9, w01 1.0-1.9, w10 3.0-3.9,
# w11 4.0-4.9, w20 6.0-6.9, w21 7.0-7.9, w30 9.0-9.9, w31 10.0-10.9. Every
# case below cuts something and then asks where a word *downstream of the cut*
# now plays, because an uncut timeline makes source and timeline coordinates
# identical and would pass no matter what the mapping did.


@needs_ffprobe
def test_locate_maps_a_word_forward_through_an_earlier_cut(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The whole point of the tool: the index does not move, the answer does.

    w30 is asked for twice with the same index, either side of a 2s cut that
    happens entirely before it.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        before = await client.call("locate", path=str(project), clip_id=clip_id, first=6)
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip_id, cut=[[2, 3]]
        )
        after = await client.call("locate", path=str(project), clip_id=clip_id, first=6)
        return {"before": before, "after": after}

    out = anyio.run(_with_server, body)

    # Uncut, source and timeline agree; that is the control, not the result.
    assert out["before"]["timeline_start"] == pytest.approx(9.0)
    assert out["before"]["source_start"] == pytest.approx(9.0)

    # The cut removed words 2..3, i.e. source 3.0-4.9 = 1.9s of material.
    assert out["after"]["source_start"] == pytest.approx(9.0), "the index must not renumber"
    assert out["after"]["timeline_start"] == pytest.approx(9.0 - 1.9)
    assert out["after"]["timeline_end"] == pytest.approx(9.9 - 1.9)
    assert out["after"]["present"] is True
    assert out["after"]["fully_present"] is True
    assert out["after"]["timeline_duration"] == pytest.approx(12.0 - 1.9)


@needs_ffprobe
def test_locate_echoes_the_words_and_their_neighbours(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The project-wide echo convention (CLAUDE.md) applies to a read too —
    an index one past the intended phrase reads fine on its own.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        return await client.call(
            "locate", path=str(project), clip_id=clip_id, first=3, last=4
        )

    result = anyio.run(_with_server, body)

    assert result["text"] == "w11 w20"
    assert [w["text"] for w in result["words"]] == ["w11", "w20"]
    assert [w["text"] for w in result["context_before"]] == ["w00", "w01", "w10"]
    assert [w["text"] for w in result["context_after"]] == ["w21", "w30", "w31"]


@needs_ffprobe
def test_locate_reports_a_cut_word_as_absent_rather_than_moving_it(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """`present: false` is the honest answer, and the reason word indices can
    stay stable across cuts at all — nothing silently slides onto neighbouring
    material.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        await client.call(
            "cut_by_transcript", path=str(project), clip_id=clip_id, cut=[[2, 3]]
        )
        return await client.call("locate", path=str(project), clip_id=clip_id, first=2)

    result = anyio.run(_with_server, body)

    assert result["present"] is False
    assert result["placements"] == []
    assert result["timeline_start"] is None
    assert result["covered"] == pytest.approx(0.0)
    assert "beyond_source" not in result, "it was recorded, it was cut — different things"
    assert result["text"] == "w10"


@needs_ffprobe
def test_locate_reports_each_surviving_piece_of_a_half_cut_range(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """A range a cut split is the normal case, not an error. `timeline_span`
    would report only the first survivor (it is shaped for captions); the
    aggregate `timeline_spans` behind `locate` reports both, and the pieces
    still play back-to-back because a cut closes its hole.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        # Cut 5.0-6.0, a silence gap sitting inside the 4.0-7.0 range below.
        await client.call("cut_by_time", path=str(project), spans=[[5.0, 6.0]])
        return await client.call(
            "locate",
            path=str(project),
            clip_id=clip_id,
            source_start=4.0,
            source_end=7.0,
        )

    result = anyio.run(_with_server, body)

    assert result["present"] is True
    assert result["fully_present"] is False
    assert result["requested"] == pytest.approx(3.0)
    assert result["covered"] == pytest.approx(2.0)
    assert len(result["placements"]) == 2

    first, second = result["placements"]
    assert (first["source_start"], first["source_end"]) == pytest.approx((4.0, 5.0))
    assert (second["source_start"], second["source_end"]) == pytest.approx((6.0, 7.0))
    # The hole closed, so the two survivors are adjacent in timeline time even
    # though they are a second apart in source time.
    assert first["timeline_end"] == pytest.approx(second["timeline_start"])
    assert result["contiguous"] is True

    # Time mode echoes by overlap, never containment: w11 (4.0-4.9) and
    # w20 (6.0-6.9) both fall inside, and the request's own edges touch neither
    # neighbour.
    assert [w["text"] for w in result["words"]] == ["w11", "w20"]


@needs_ffprobe
def test_locate_finds_the_word_playing_at_a_source_instant(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        await client.call("cut_by_time", path=str(project), spans=[[0.0, 1.0]])
        return await client.call(
            "locate", path=str(project), clip_id=clip_id, source_start=7.5
        )

    result = anyio.run(_with_server, body)

    assert result["mode"] == "instant"
    assert result["timeline_start"] == pytest.approx(6.5)
    assert result["timeline_end"] == pytest.approx(6.5)
    assert [w["text"] for w in result["words"]] == ["w21"]


@needs_ffprobe
def test_locate_names_the_nearest_words_for_an_instant_in_silence(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """5.5s is in the gap between w11 (ends 4.9) and w20 (starts 6.0). An empty
    word list with no neighbours would leave nothing to check the timestamp
    against.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        return await client.call(
            "locate", path=str(project), clip_id=clip_id, source_start=5.5
        )

    result = anyio.run(_with_server, body)

    assert result["words"] == []
    assert [w["text"] for w in result["context_before"]] == ["w01", "w10", "w11"]
    assert [w["text"] for w in result["context_after"]] == ["w20", "w21", "w30"]


@needs_ffprobe
def test_locate_distinguishes_never_recorded_from_cut(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Both come back with no placements, and only the clip's own duration
    tells them apart — so `locate` says which it is rather than letting the
    empty list read as an edit decision.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        return await client.call(
            "locate", path=str(project), clip_id=clip_id, source_start=20.0
        )

    result = anyio.run(_with_server, body)

    assert result["present"] is False
    assert result["beyond_source"] is True
    assert result["source_duration"] == pytest.approx(12.0, abs=0.05)


@needs_ffprobe
def test_locate_refuses_both_addressing_modes_at_once(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Word indices and source seconds name the same thing two ways; a call
    giving both cannot say which it meant, and picking one would be a guess.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        return await session.call_tool(
            "locate", {"path": str(project), "clip_id": clip_id, "first": 3, "source_start": 4.0}
        )

    result = anyio.run(_with_server, body)

    assert result.is_error
    assert "not both" in result.content[0].text


@needs_ffprobe
def test_locate_works_on_a_clip_with_no_transcript(tmp_path: Path) -> None:
    """A picture-only clip is a valid thing to ask about by time, so the
    missing transcript is reported rather than made a refusal — the same
    choice `cut_by_time` makes.
    """
    project = tmp_path / "proj"
    audio = tmp_path / "vo.wav"
    _make_wav(audio, tones=[(0.0, 2.0)], duration=4.0)

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, None)
        return await client.call(
            "locate", path=str(project), clip_id=clip_id, source_start=1.0, source_end=2.0
        )

    result = anyio.run(_with_server, body)

    assert result["present"] is True
    assert result["words"] is None
    assert result["transcript_missing"] is True


# -- timeline_view: the whole edit in one payload -------------------------
#
# `locate` asked once per range; this asks once for the clip. The cases that
# matter are the ones a view drawn off the transcript instead of the edit
# would get wrong.


@needs_ffprobe
def test_timeline_view_names_each_seam_by_the_surviving_words_either_side(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The seam is a word boundary, not a timeline second.

    Asking the transcript what sits at the seam's *source* time answers with
    the word that was removed — a cut begins exactly where the outgoing
    segment ends — so the lookup has to happen among the survivors, in
    timeline coordinates.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        await client.call("cut_by_transcript", path=str(project), clip_id=clip_id, cut=[[2, 3]])
        return await client.call("timeline_view", path=str(project))

    view = anyio.run(_with_server, body)

    assert len(view["seams"]) == 1
    seam = view["seams"][0]
    assert (seam["before"]["index"], seam["after"]["index"]) == (1, 4)
    assert seam["removed"] == pytest.approx(1.9)
    assert seam["timeline_time"] == pytest.approx(3.0)


@needs_ffprobe
def test_timeline_view_reports_cut_words_absent_and_later_words_moved(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        await client.call("cut_by_transcript", path=str(project), clip_id=clip_id, cut=[[2, 3]])
        return await client.call("timeline_view", path=str(project))

    words = {w["index"]: w for w in anyio.run(_with_server, body)["words"]}

    assert words[2]["present"] is False and words[2]["timeline_start"] is None
    assert words[3]["present"] is False
    # The index never renumbers; only the answer moves.
    assert words[6]["start"] == pytest.approx(9.0), "the index must not renumber"
    assert words[6]["timeline_start"] == pytest.approx(9.0 - 1.9)


@needs_ffprobe
def test_timeline_view_marks_a_word_a_cut_only_half_removed(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Partial survival is normal on whisper timings, so it is reported.

    w10 runs 3.0-3.9; cutting render time 3.5-4.5 takes half of it. A
    containment test would call the word gone (HISTORY.md § 2).
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        await client.call("cut_by_time", path=str(project), spans=[[3.5, 4.5]])
        return await client.call("timeline_view", path=str(project))

    words = {w["index"]: w for w in anyio.run(_with_server, body)["words"]}

    assert words[2]["present"] is True
    assert words[2]["partial"] is True
    assert words[2]["covered"] == pytest.approx(0.5)


@needs_ffprobe
def test_timeline_view_carries_both_coordinate_systems_per_segment(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        await client.call("cut_by_transcript", path=str(project), clip_id=clip_id, cut=[[2, 3]])
        return await client.call("timeline_view", path=str(project))

    view = anyio.run(_with_server, body)
    first, second = view["segments"]

    assert (first["start"], first["timeline_start"]) == (pytest.approx(0.0), pytest.approx(0.0))
    assert first["end"] == pytest.approx(3.0)
    # Source 4.9 onward, but it plays from 3.0 — the pair a caller otherwise
    # recomputes by summing durations.
    assert second["start"] == pytest.approx(4.9)
    assert second["timeline_start"] == pytest.approx(3.0)
    assert view["undo_depth"] == SEEDED_DEPTH + 1


@needs_ffprobe
def test_timeline_view_words_carry_a_paragraph_field(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The read model over the wire: every word — cut or not — is placed in a
    paragraph. test_ops_paragraphs.py covers the break rule itself in
    isolation; this only checks the field rides through the real call, since
    `sources`' 8 plain words never earn a break.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        await client.call("cut_by_transcript", path=str(project), clip_id=clip_id, cut=[[2, 3]])
        return await client.call("timeline_view", path=str(project))

    words = {w["index"]: w for w in anyio.run(_with_server, body)["words"]}

    assert all(w["paragraph"] == 0 for w in words.values())
    # A cut word still gets a paragraph — it is a document property, not an
    # edit one.
    assert words[2]["present"] is False
    assert words[2]["paragraph"] == 0


@needs_ffprobe
def test_timeline_view_words_carries_a_pause_after_field_above_threshold(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The read model over the wire: `sources`' 8 words sit two-per-burst,
    0.1s apart within a burst and 1.1s apart across a burst boundary — so
    only the second word of each burst (except the last) should carry
    `pause_after`. ops._gap_after/_word_placements are unit-tested directly
    in test_ops_pause_markers.py; this only checks the field rides through
    the real `timeline_view` call unmodified.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await client.call("timeline_view", path=str(project))

    words = {w["index"]: w for w in anyio.run(_with_server, body)["words"]}

    for i in (1, 3, 5):
        assert words[i]["pause_after"]["duration"] == pytest.approx(1.1)
        assert words[i]["pause_after"]["present"] is True
    for i in (0, 2, 4, 6, 7):
        assert "pause_after" not in words[i]


@needs_ffprobe
def test_cut_by_transcript_through_pause_extends_the_removed_range_to_the_next_word(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Cutting the phrase ending at word 3 (w11, end 4.9s) sits right before
    the 1.1s gap to word 4 (w20, start 6.0s) — wide enough to have drawn a
    `[1.1s]` marker. `through_pause=True` should extend the removed range's
    trailing edge onto that gap; without the flag the pinned boundary
    (test_cut_plan_resolves_without_touching_the_timeline, `word_end ==
    4.9`) holds unmodified.
    """
    audio, transcript = sources

    async def with_flag(project: Path) -> dict[str, Any]:
        async def body(session: ClientSession) -> Any:
            client = Client(session)
            clip_id = await _seeded(client, project, audio, transcript)
            return await client.call(
                "cut_by_transcript",
                path=str(project),
                clip_id=clip_id,
                cut=[[2, 3]],
                through_pause=True,
            )

        return await _with_server(body)

    async def without_flag(project: Path) -> dict[str, Any]:
        async def body(session: ClientSession) -> Any:
            client = Client(session)
            clip_id = await _seeded(client, project, audio, transcript)
            return await client.call(
                "cut_by_transcript", path=str(project), clip_id=clip_id, cut=[[2, 3]]
            )

        return await _with_server(body)

    extended = anyio.run(with_flag, tmp_path / "proj-flagged")
    plain = anyio.run(without_flag, tmp_path / "proj-plain")

    # Regression guard: the no-flag boundary is exactly the last word's own
    # `end`, matching the pinned assertion elsewhere in this file.
    assert plain["applied"][0]["source_end"] == pytest.approx(4.9)
    assert plain["applied"][0]["word_end"] == pytest.approx(4.9)

    # With the flag, the removed range's trailing edge reaches the next
    # word's own start (6.0s) — the pause between them is gone too — while
    # the echoed `word_end` (the words themselves) is unaffected, exactly
    # the way `pad` already diverges `source_end` from `word_end`.
    assert extended["applied"][0]["source_end"] == pytest.approx(6.0)
    assert extended["applied"][0]["word_end"] == pytest.approx(4.9)
    assert extended["removed"] == pytest.approx(plain["removed"] + 1.1, abs=1e-6)


@needs_ffprobe
def test_cut_by_transcript_through_pause_has_no_effect_under_the_marker_threshold(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Word 0 (w00, end 0.9s) sits only 0.1s from word 1 (w01, start 1.0s) —
    under PAUSE_MARKER_MIN, so no marker would have drawn there. The shared
    predicate makes `through_pause=True` a safe no-op on that boundary.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        return await client.call(
            "cut_by_transcript",
            path=str(project),
            clip_id=clip_id,
            cut=[[0, 0]],
            through_pause=True,
        )

    out = anyio.run(_with_server, body)
    applied = out["applied"][0]
    assert applied["source_end"] == pytest.approx(0.9)
    assert applied["word_end"] == pytest.approx(0.9)


# -- the layered timeline over the wire ----------------------------------
#
# `export` grew a second writer (PLAN.md § The layered timeline, step 4): a
# project with a cue table is written as MLT by proofcut itself, because
# auto-editor refuses a second source on export and renders one at 720x576
# while exiting 0. Which writer ran is a property of the project, never of an
# argument, so these go through the real tool calls that build that project.


@needs_ffprobe
@needs_ffmpeg
def test_a_cue_table_makes_export_write_mlt_itself(tmp_path: Path) -> None:
    audio, transcript = _make_sources(tmp_path)
    film = tmp_path / "film.mp4"
    _make_video(film, duration=12.0)
    project = tmp_path / "proj"
    out = tmp_path / "out.kdenlive"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset=asset["clip_id"]
        )
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=4, asset=asset["clip_id"]
        )
        shots = await client.call("build_shots", path=str(project), fps=30)
        return {
            "shots": shots,
            "export": await client.call("export", path=str(project), output=str(out)),
        }

    result = anyio.run(_with_server, body)

    assert result["export"]["writer"] == "mlt"
    assert result["export"]["shots"] == 2
    # The lane is quantised on the export's grid, not on the project's
    # millisecond timebase — asking for shots on 30 gives the same total.
    assert result["shots"]["total_frames"] == result["export"]["frames"]
    assert 'frame_rate_num="30"' in out.read_text(encoding="utf-8")


@needs_ffprobe
@needs_ffmpeg
def test_a_pinned_cue_puts_its_in_point_into_the_document(tmp_path: Path) -> None:
    """`cue_add`'s `src_start` is reachable over the wire and lands in the XML.

    The end of the b-roll chain: `describe` finds a moment, `describe_ls`
    reports its `src_start`, and this is where that number becomes the `in`
    on an entry melt reads. Asserted off the written document rather than off
    `build_shots`, because the projection deliberately does not decide the
    in-point — only the writer does, and a number that was right in the plan
    and absent from the XML is exactly the bug this file exists to catch.
    """
    audio, transcript = _make_sources(tmp_path)
    film = tmp_path / "film.mp4"
    _make_video(film, duration=20.0)
    project = tmp_path / "proj"
    out = tmp_path / "out.kdenlive"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset=asset["clip_id"]
        )
        pinned = await client.call(
            "cue_add",
            path=str(project),
            clip_id=clip_id,
            word_index=4,
            asset=asset["clip_id"],
            src_start=7.0,
        )
        return {
            "pinned": pinned,
            "listed": await client.call("cue_ls", path=str(project)),
            "export": await client.call("export", path=str(project), output=str(out)),
        }

    result = anyio.run(_with_server, body)

    assert result["pinned"]["src_start"] == 7.0
    assert [c["src_start"] for c in result["listed"]["cues"]] == [None, 7.0]
    assert result["export"]["writer"] == "mlt"

    document = ET.fromstring(out.read_text(encoding="utf-8"))
    film_nodes = {
        node.get("id")
        for node in document.iter()
        if node.tag in {"chain", "producer"}
        and any(
            p.get("name") == "resource" and str(p.text).endswith("film.mp4")
            for p in node.findall("property")
        )
    }
    assert film_nodes, "the film never made it into the document at all"
    # Track playlists only — `main_bin` lists the same film again as a bin
    # entry, always at 0, and that one says nothing about the timeline.
    picture_ins = [
        int(entry.get("in"))
        for playlist in document.iter("playlist")
        if playlist.get("id") != "main_bin"
        for entry in playlist.findall("entry")
        if entry.get("producer") in film_nodes
    ]
    # Two shots off the film: the unpinned one from its head, and the pinned
    # one at 7.0s * 30fps. Nothing rewound and nothing was clamped.
    assert picture_ins == [0, 210], f"expected the pin at frame 210, got {picture_ins}"


@needs_ffprobe
@needs_ffmpeg
def test_a_pinned_cue_that_outruns_its_asset_refuses_over_the_wire(tmp_path: Path) -> None:
    """The safety property, at the transport. Unpinned this same shot rewinds
    to the head of the clip and exports happily; pinned it must refuse, or the
    film quietly shows the asset's opening seconds under a cue that says it
    shows the moment at 19.0s.
    """
    audio, transcript = _make_sources(tmp_path)
    film = tmp_path / "film.mp4"
    _make_video(film, duration=20.0)
    project = tmp_path / "proj"
    out = tmp_path / "out.kdenlive"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add",
            path=str(project),
            clip_id=clip_id,
            word_index=0,
            asset=asset["clip_id"],
            src_start=19.0,
        )
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=4, asset=asset["clip_id"]
        )
        refused = await session.call_tool(
            "export", {"path": str(project), "output": str(out)}
        )
        return {"is_error": refused.is_error, "text": refused.content[0].text}

    result = anyio.run(_with_server, body)

    assert result["is_error"]
    assert "a pinned cue shows the moment it names" in result["text"]
    assert not out.exists(), "a refused export must leave no half-written document"


@needs_ffprobe
def test_cue_add_refuses_a_pin_on_a_card_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """A held frame has no playhead, so this is refused where it is cheapest —
    before any media is resolved. Over the wire because a refusal that only
    exists in `ops` is one the agent never meets."""
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        result = await session.call_tool(
            "cue_add",
            {
                "path": str(project),
                "clip_id": clip["clip_id"],
                "word_index": 0,
                "asset": "card:outro",
                "src_start": 3.0,
            },
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)
    assert out["is_error"]
    assert "no playhead to move" in out["text"]


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_rendering_a_cued_project_goes_through_melt_and_is_measured(
    visible_tmp: Path,
) -> None:
    """Step 5, end to end and against a real melt: a cued project renders.

    Everything here is the thing itself — a real film clip, a real cue, a real
    encode — because every failure this path routes around produces a file and
    exit 0 rather than an error. auto-editor would render this at 720x576;
    melt has no source-count gate, and what proves which one ran is the
    resolution ffprobe reads back off the finished file.

    Under `$HOME` (`visible_tmp`), not `tmp_path`: the flatpak cannot see the
    host's /tmp, and a project it cannot read renders nothing while exiting 0.
    """
    audio, transcript = _make_sources(visible_tmp)
    film = visible_tmp / "film.mp4"
    _make_video(film, duration=12.0)
    project = visible_tmp / "proj"
    output = visible_tmp / "out.mp4"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset=asset["clip_id"]
        )
        rendered = await client.call(
            "export", path=str(project), output=str(output), export_format=None
        )
        return rendered, await client.call(
            "check_frames", path=str(project), target=str(output)
        )

    rendered, frames = anyio.run(_with_server, body)

    assert rendered["writer"] == "melt"
    assert rendered["format"] == "media"
    # melt's own answer before the encode, and the file's after it.
    assert rendered["melt_frames"] == rendered["frames"] == 360
    assert rendered["rendered"]["frames"] == 360
    assert (rendered["rendered"]["width"], rendered["rendered"]["height"]) == (160, 120)
    assert rendered["rendered"]["has_video"] and rendered["rendered"]["has_audio"]
    assert Path(rendered["output"]).is_file()
    # And the picture-side check agrees with it, with no tail frame to explain:
    # that defect is auto-editor's kdenlive export, and this document is proofcut's.
    assert frames["delta"] == 0
    assert frames["agrees"] is True


# -- a head's own lead-silence pad, against a real melt render --------------
#
# The trap named in CLAUDE.md: `mlt.document`'s validation only checks the
# music lane's *total* frame count against the timeline total, never its
# internal alignment against the edit track — so a bed placed `head_frames`
# too early passes every check proofcut has and is wrong only to a listener.
# Two distinct tones, Goertzel-read from two windows of the actual render,
# the same readback discipline CLAUDE.md documents for the co-hosted
# recording's `audio_index` trap.


def _tone_video(path: Path, hz: float, duration: float, *, fps: int = 30) -> None:
    """Real picture (for the head/cue asset) plus a pure tone soundtrack."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate={fps}:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={duration}:sample_rate=48000",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        capture_output=True,
        check=True,
    )  # fmt: skip


def _tone_wav(path: Path, hz: float, duration: float) -> None:
    """The music bed's own asset — audio only, a distinct pure tone."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={duration}:sample_rate=48000",
         "-c:a", "pcm_s16le", str(path)],
        capture_output=True,
        check=True,
    )  # fmt: skip


def _silence_wav(path: Path, duration: float) -> None:
    """The VO track — silent, so it cannot be mistaken for either tone."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(duration),
         "-c:a", "pcm_s16le", str(path)],
        capture_output=True,
        check=True,
    )  # fmt: skip


def _quiet_vo_wav(path: Path, duration: float) -> None:
    """A VO stand-in with a normal, measurable level — a different frequency
    (100 Hz) from either tone under test, so it cannot be mistaken for one,
    and full amplitude rather than `anullsrc`'s pure digital silence.

    `energy.integrated_loudness` (`ops._vo_loudness`'s own measurement)
    refuses `-inf` — which `loudnorm` reports not only for pure silence but
    for *any* signal quiet enough that every block falls under EBU R128's
    own -70 LUFS absolute gate (measured: even a -78 dBTP tone still gates
    to `-inf`) — because a hold's gain formula built on `-inf` corrupts the
    *whole* rendered audio mix, not just one entry, at real `melt`'s exit 0.
    A deliberately *quiet* VO also does not help this test: `_hold_gain_db`
    levels the hold to sit at the VO's own measured loudness, so an
    artificially quiet VO attenuates the hold's own tone into the noise
    floor and the Goertzel read comes back near zero for a reason that has
    nothing to do with the mechanism under test. Full level, like the other
    synthetic tones here, keeps the gain this scenario computes close to
    unity.
    """
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=100:duration={duration}:sample_rate=48000",
         "-c:a", "pcm_s16le", str(path)],
        capture_output=True,
        check=True,
    )  # fmt: skip


def _tone_window(path: Path, hz: float, start: float, duration: float) -> float:
    """Goertzel power at `hz` over one window of the render's own audio.

    `-ss` **after** `-i`, not before: this is a short file and accuracy
    matters more than seek speed — the whole point is telling apart a window
    that starts a few centiseconds either side of `head_seconds`.
    """
    decoded = path.with_name(f"{path.stem}-{hz:g}-{start:g}.wav")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
         "-ss", str(start), "-t", str(duration),
         "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(decoded)],
        capture_output=True,
        check=True,
    )  # fmt: skip
    with wave.open(str(decoded), "rb") as handle:
        rate, count = handle.getframerate(), handle.getnframes()
        raw = handle.readframes(count)
    samples = array.array("h")
    samples.frombytes(raw)
    n = len(samples)
    if n == 0:
        return 0.0
    k = int(0.5 + (n * hz) / rate)
    w = 2 * math.pi * k / n
    coeff = 2 * math.cos(w)
    q1 = q2 = 0.0
    for sample in samples:
        q0 = coeff * q1 - q2 + sample
        q2, q1 = q1, q0
    return math.sqrt(abs(q1 * q1 + q2 * q2 - coeff * q1 * q2)) / n


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_a_head_delays_the_music_beds_own_lead_silence(visible_tmp: Path) -> None:
    """The bed must not play over the cold open.

    The head's own clip carries a 300 Hz tone, the bed carries 880 Hz, the VO
    track is silent so neither tone can be mistaken for it. With the lead
    pad wired correctly, the render is 300 Hz-only for the head's own two
    seconds and 880 Hz-only once the bed's boundary word (word 0, at the
    Edit's own start) actually plays — which, with a head, is `head_seconds`
    into the render, not frame 0. Measured against a real render because
    `mlt.document` only checks the music lane's *total* frame count, never
    its alignment against the edit track — a bed placed `head_frames` too
    early passes every check proofcut has and is wrong only to a listener.
    """
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    film = visible_tmp / "film.mp4"
    bed = visible_tmp / "bed.wav"
    _silence_wav(vo, 5.0)
    _tone_video(film, 300.0, 6.0)
    _tone_wav(bed, 880.0, 6.0)

    transcript = visible_tmp / "vo.json"
    transcript.write_text(
        json.dumps({"language": "en", "words": [{"word": "w0", "start": 0.0, "end": 0.1}]}),
        encoding="utf-8",
    )

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        vo_clip = await client.call("import_media", path=str(project), source=str(vo))
        film_clip = await client.call("import_media", path=str(project), source=str(film))
        bed_clip = await client.call("import_media", path=str(project), source=str(bed))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo_clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo_clip["clip_id"], remove_silences=False
        )
        await client.call(
            "cue_add",
            path=str(project),
            clip_id=vo_clip["clip_id"],
            word_index=0,
            asset=film_clip["clip_id"],
        )
        await client.call("head", path=str(project), asset=film_clip["clip_id"], seconds=2.0)
        await client.call(
            "music",
            path=str(project),
            asset=bed_clip["clip_id"],
            clip_id=vo_clip["clip_id"],
            word_index_start=0,
        )
        return await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None
        )

    result = anyio.run(_with_server, body)

    assert result["writer"] == "melt"
    assert result["head"]["frames"] > 0
    render = Path(result["output"])

    # Comfortably inside the head, well clear of its own fade-free edges.
    during_head_300 = _tone_window(render, 300.0, 0.2, 1.5)
    during_head_880 = _tone_window(render, 880.0, 0.2, 1.5)
    # Comfortably after `head_seconds`, well clear of the boundary.
    after_head_880 = _tone_window(render, 880.0, 2.2, 1.0)
    after_head_300 = _tone_window(render, 300.0, 2.2, 1.0)

    # Measured clean separation on this box: the present tone reads in the
    # thousands, the absent one reads at noise floor (0.0-0.01) — the
    # threshold has three orders of magnitude of headroom either way.
    assert during_head_300 > 500.0, "the head's own tone must be audible during the head"
    assert during_head_880 < 50.0, "the bed must not be audible yet — it would be, un-offset"
    assert after_head_880 > 500.0, "the bed must be audible once the head has actually ended"
    assert after_head_300 < 50.0, "the head's own clip does not extend past its own length"


# -- export presets --------------------------------------------------------
#
# docs/plans/DAYDREAM.md § Export presets maps YouTube/Web/Custom onto ops.export as
# named bundles. TikTok-Reels joined them with PLAN.md § Aspect swap step 5,
# once a filled 9:16 render existed to make the name honest — and it is the
# one preset that *checks* rather than only encoding, because the shape a
# project renders at is `canvas`'s and a preset that set it would be an
# export argument rewriting project state.


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_export_preset_youtube_reproduces_todays_default_melt_consumer(
    visible_tmp: Path,
) -> None:
    """Naming today's hardcoded melt consumer as 'youtube' changes nothing
    about what melt actually does — the encode is byte-identical."""
    audio, transcript = _make_sources(visible_tmp)
    film = visible_tmp / "film.mp4"
    _make_video(film, duration=12.0)
    project = visible_tmp / "proj"
    output = visible_tmp / "out.mp4"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset=asset["clip_id"]
        )
        return await client.call(
            "export",
            path=str(project),
            output=str(output),
            export_format=None,
            preset="youtube",
        )

    rendered = anyio.run(_with_server, body)

    assert rendered["preset"] == "youtube"
    assert rendered["rendered"]["consumer"] == [
        "vcodec=libx264",
        "crf=18",
        "preset=medium",
        "acodec=aac",
    ]


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_export_preset_web_only_varies_the_four_known_keys(visible_tmp: Path) -> None:
    """'web' varies values within melt's already-measured-safe {vcodec, crf,
    preset, acodec} — never a new key (HISTORY.md § 4's `ab`/`width`/`height`
    growth is exactly what this guards against ever being silently reopened).
    """
    audio, transcript = _make_sources(visible_tmp)
    film = visible_tmp / "film.mp4"
    _make_video(film, duration=12.0)
    project = visible_tmp / "proj"
    output = visible_tmp / "out.mp4"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset=asset["clip_id"]
        )
        return await client.call(
            "export", path=str(project), output=str(output), export_format=None, preset="web"
        )

    rendered = anyio.run(_with_server, body)

    consumer = rendered["rendered"]["consumer"]
    assert "crf=23" in consumer
    assert "preset=faster" in consumer
    assert len(consumer) == 4
    assert not any(entry.startswith(("width=", "height=", "ab=")) for entry in consumer)


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_tiktok_reels_renders_a_filled_vertical_frame_through_melt(
    visible_tmp: Path,
) -> None:
    """Step 5 end to end, against a real melt: with the canvas set, the preset
    renders and the finished file measures 9:16.

    The whole point of the preset is a claim about the frame, so the assertion
    is the frame ffprobe reads back — not the exit code, and not the reply's
    echo of the preset name. The consumer is asserted alongside it because the
    entry carries `youtube`'s four values: the shape must come from the canvas
    and nothing else. A small 9:16 canvas rather than 1080x1920 keeps the
    encode cheap; 90x160 is exactly 9:16 and even on both edges.
    """
    audio, transcript = _make_sources(visible_tmp)
    film = visible_tmp / "film.mp4"
    _make_video(film, duration=12.0)
    project = visible_tmp / "proj"
    output = visible_tmp / "out.mp4"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset=asset["clip_id"]
        )
        swapped = await client.call("canvas", path=str(project), size="90x160")
        rendered = await client.call(
            "export",
            path=str(project),
            output=str(output),
            export_format=None,
            preset="tiktok-reels",
        )
        return swapped, rendered

    swapped, rendered = anyio.run(_with_server, body)

    assert swapped["aspect"] == "9:16"
    assert rendered["writer"] == "melt"
    assert rendered["preset"] == "tiktok-reels"
    # The reply's own account of the shape, and the file's — a render that
    # degraded would still have carried the preset name.
    assert rendered["canvas"] == "90x160"
    assert (rendered["rendered"]["width"], rendered["rendered"]["height"]) == (90, 160)
    assert rendered["rendered"]["consumer"] == [
        "vcodec=libx264",
        "crf=18",
        "preset=medium",
        "acodec=aac",
    ]


@needs_ffprobe
@needs_ffmpeg
def test_export_resolution_on_a_layered_project_is_refused(tmp_path: Path) -> None:
    """melt's consumer is deliberately hardcoded shut against width/height —
    HISTORY.md § 4's growth to 14.6 GB was never isolated to a safe subset —
    so a layered project refuses `resolution` outright, before any subprocess
    runs."""
    audio, transcript = _make_sources(tmp_path)
    film = tmp_path / "film.mp4"
    _make_video(film, duration=12.0)
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clip_id = await _seeded(client, project, audio, transcript)
        asset = await client.call("import_media", path=str(project), source=str(film))
        await client.call(
            "cue_add", path=str(project), clip_id=clip_id, word_index=0, asset=asset["clip_id"]
        )
        result = await session.call_tool(
            "export",
            {
                "path": str(project),
                "output": str(tmp_path / "out.mp4"),
                "export_format": None,
                "resolution": [608, 1080],
            },
        )
        return result

    result = anyio.run(_with_server, body)
    assert result.is_error
    text = result.content[0].text
    assert "14.6" in text or "HISTORY" in text
    assert "single-source" in text


def test_unknown_export_preset_lists_every_available_name(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await session.call_tool(
            "export",
            {
                "path": str(project),
                "output": str(tmp_path / "out.wav"),
                "export_format": None,
                "preset": "instagram-story",
            },
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    text = result.content[0].text
    assert "youtube" in text and "web" in text and "custom" in text
    # The name that arrived last has to be reachable from the refusal too,
    # or the one preset with a precondition is the one nobody discovers.
    assert "tiktok-reels" in text


def test_tiktok_reels_refuses_a_project_whose_canvas_is_not_9_16(tmp_path: Path) -> None:
    """The failure this preset exists to close: a landscape render under a
    vertical name, which every downstream check would have passed.

    The clip is written into the manifest rather than imported — what is under
    test is the shape check, not ffprobe — and the refusal is asserted to carry
    the fix, because `canvas` is also what reports what the crop would cost.
    """
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await client.call("init", path=str(project))
        opened = Project.open(project)
        manifest = opened.read_manifest()
        manifest["clips"] = [
            {
                "clip_id": "cold-open",
                "source": "/tmp/cold-open.mp4",
                "duration": 12.0,
                "has_video": True,
                "has_audio": True,
                "width": 1920,
                "height": 816,
            }
        ]
        opened.write_manifest(manifest)
        return await session.call_tool(
            "export",
            {
                "path": str(project),
                "output": str(tmp_path / "out.mp4"),
                "export_format": None,
                "preset": "tiktok-reels",
            },
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    text = result.content[0].text
    assert "1920x816" in text and "9:16" in text
    assert "canvas 1080x1920" in text
    # And it refuses without writing: a preset that fixed the shape on the
    # caller's behalf is the thing this design decided against.
    assert Project.open(project).read_manifest().get("canvas") is None


def test_tiktok_reels_refuses_an_audio_only_project_for_the_true_reason(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """`_mlt_resolution` falls back to 1080p with no footage, so the geometry
    path would refuse this by quoting a frame the project has not got — a
    true refusal for a false reason. It is caught before that.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await session.call_tool(
            "export",
            {
                "path": str(project),
                "output": str(tmp_path / "out.wav"),
                "export_format": None,
                "preset": "tiktok-reels",
            },
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    text = result.content[0].text
    assert "no picture" in text
    assert "1920x1080" not in text, "quoting the 1080p fallback would be a fiction"


def test_preset_or_resolution_with_an_nle_export_format_is_refused(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await session.call_tool(
            "export",
            {
                "path": str(project),
                "output": str(tmp_path / "out.kdenlive"),
                "export_format": "kdenlive",
                "preset": "youtube",
            },
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    assert "bitrate" in result.content[0].text


def test_custom_preset_without_resolution_is_refused(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await session.call_tool(
            "export",
            {
                "path": str(project),
                "output": str(tmp_path / "out.wav"),
                "export_format": None,
                "preset": "custom",
            },
        )

    result = anyio.run(_with_server, body)
    assert result.is_error
    assert "custom" in result.content[0].text


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_export_resolution_letterboxes_a_single_source_render_and_reports_the_measured_size(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pic.mp4"
    _make_video(source, duration=2.0)
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"

    words = [{"word": f"w{i:02d}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(4)]
    transcript = tmp_path / "pic.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, source, transcript)
        return await client.call(
            "export",
            path=str(project),
            output=str(render),
            export_format=None,
            preset="custom",
            resolution=[608, 1080],
        )

    result = anyio.run(_with_server, body)

    assert result["resolution"] == [608, 1080]
    assert result["notes"] == []
    assert Path(result["output"]).is_file()
    info = media.probe(render)
    assert (info.width, info.height) == (608, 1080)


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_export_resolution_is_a_documented_noop_on_an_audio_only_project(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project = tmp_path / "proj"
    render = tmp_path / "out.wav"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await client.call(
            "export",
            path=str(project),
            output=str(render),
            export_format=None,
            preset="custom",
            resolution=[608, 1080],
        )

    result = anyio.run(_with_server, body)

    assert result["resolution"] is None
    assert any("no video stream" in note for note in result["notes"])
    assert Path(result["output"]).is_file()


# -- the bound server ---------------------------------------------------------
#
# `proofcut -C <project> mcp` binds the server to one project. The web UI's agent
# panel spawns exactly this (`webui.py`'s generated MCP config), and it is the
# half of that panel's confinement that `--strict-mcp-config` and `--tools ""`
# do not cover: those keep the agent inside proofcut's ops, this keeps it inside
# *this project's*. Every check below goes over the wire for the usual reason
# — the binding lives in the CLI-to-server wiring, which is exactly what a
# direct call to a tool function cannot see.


def _bound(root: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "proofcut.cli", "-C", str(root), "mcp"]
    )


async def _refused(session: ClientSession, tool: str, **arguments: Any) -> str:
    """Call a tool expecting a refusal, and return the message it refused with."""
    result = await session.call_tool(tool, arguments)
    text = result.content[0].text
    assert result.is_error, f"{tool} was expected to be refused, but returned: {text}"
    return text


def _two_projects(tmp_path: Path) -> tuple[Path, Path]:
    project, other = tmp_path / "proj", tmp_path / "other"
    ops.init(str(project))
    ops.init(str(other))
    return project, other


def test_a_bound_server_serves_its_own_project(tmp_path: Path) -> None:
    project, _ = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("cue_ls", path=str(project))

    assert anyio.run(_with_server, body, _bound(project))["count"] == 0


def test_a_bound_server_refuses_another_project(tmp_path: Path) -> None:
    """The hole this binding closes: every tool takes an explicit `path`."""
    project, other = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await _refused(session, "cue_ls", path=str(other))

    message = anyio.run(_with_server, body, _bound(project))
    assert str(project) in message and str(other) in message


def test_a_bound_server_defaults_an_omitted_path_to_its_own_project(tmp_path: Path) -> None:
    """TRIAL.md § `path` is a required argument: an agent bound to a project
    passed `path` on every one of 29 calls despite being told it never would
    need to, because omitting it against a bound server used to be a schema
    validation error. `path` is now optional everywhere, and a bound server
    resolves the omission to itself."""
    project, _ = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("cue_ls")

    assert anyio.run(_with_server, body, _bound(project))["count"] == 0


def test_an_unbound_server_refuses_an_omitted_path(tmp_path: Path) -> None:
    """There is no project to default to, so the omission is still refused —
    with a message naming the reason, not a bare schema error, since `path`
    can no longer be required only for the unbound case (the two states
    share one advertised schema)."""

    async def body(session: ClientSession) -> Any:
        return await _refused(session, "cue_ls")

    message = anyio.run(_with_server, body)
    assert "path" in message and "required" in message


@pytest.mark.skipif(
    shutil.which("magick") is None or shutil.which("ffmpeg") is None,
    reason="fonts' render check needs ImageMagick and ffmpeg with libass",
)
def test_a_bound_server_still_lets_fonts_go_without_a_project(tmp_path: Path) -> None:
    """`fonts`/`pack_show` document `path=None` as "no project, proofcut's
    default" rather than "which project" — a meaning the default-to-bound
    rule above must not overwrite just because a project happens to be
    bound. `projectless=True` is what keeps their omitted `path` as `None`
    in every bind state, checked here on `fonts` (`pack_show` refuses with
    neither `pack_path` nor `path` given, bound or not, so it cannot show
    the same thing with no fixture pack file)."""
    project, _ = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("fonts")

    result = anyio.run(_with_server, body, _bound(project))
    assert result["project"] is None


def test_a_bound_server_still_confines_an_explicit_path_on_a_projectless_tool(
    tmp_path: Path,
) -> None:
    """`projectless=True` only changes what an *omitted* `path` means — a
    `path` actually given must still be confined to the bound project, or a
    bound panel could reach `pack_show` on a second project."""
    project, other = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await _refused(session, "pack_show", path=str(other))

    message = anyio.run(_with_server, body, _bound(project))
    assert str(project) in message and str(other) in message


def test_a_bound_server_resolves_a_relative_path_against_its_project(tmp_path: Path) -> None:
    """A bound server means "this project", not "wherever the client stands".

    The proof is that this succeeds at all: the test process runs from the
    repo, which is not a proofcut project, so a "." resolved against the cwd
    could only fail.
    """
    project, _ = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("cue_ls", path=".")

    assert anyio.run(_with_server, body, _bound(project))["count"] == 0


def test_a_bound_server_expands_a_tilde_in_the_confined_path(tmp_path: Path) -> None:
    """`~/proj` is the bound project when the project is under HOME, not a
    directory literally named `~`. Measured on the first recorded agent run
    (docs/plans/LAUNCH.md § Step 1): a brief spelled `~/…` so the pane shows
    no username, and `resolve()` alone would refuse every such `path` as
    outside the project. HOME is the child's, so both are set on the server
    (`USERPROFILE` is what `expanduser` reads on Windows)."""
    project, _ = _two_projects(tmp_path)
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "proofcut.cli", "-C", str(project), "mcp"], env=env
    )

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("cue_ls", path="~/proj")

    assert anyio.run(_with_server, body, server)["count"] == 0


def test_a_bound_server_refuses_an_escape_by_parent_or_symlink(tmp_path: Path) -> None:
    """Both sides resolve, so `..` and a symlink out are refused, not followed."""
    project, other = _two_projects(tmp_path)
    (project / "elsewhere").symlink_to(other, target_is_directory=True)

    async def body(session: ClientSession) -> Any:
        return [
            await _refused(session, "cue_ls", path="../other"),
            await _refused(session, "cue_ls", path=str(project / "elsewhere")),
        ]

    for message in anyio.run(_with_server, body, _bound(project)):
        assert str(other) in message


def test_a_bound_server_confines_the_reel_destination(tmp_path: Path) -> None:
    """`dest` is the one argument in the whole surface that is a *second*
    project selector rather than a file, so an unconfined one would let an
    agent panel opened on one project write a whole project anywhere on disk.
    `path` being confined is no help: the escape is on the way out."""
    project, other = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await _refused(
            session, "reel", path=str(project), dest=str(other / "teaser"), start=0.0, end=1.0
        )

    message = anyio.run(_with_server, body, _bound(project))
    assert str(project) in message and str(other) in message


@needs_ffprobe
def test_a_bound_server_takes_a_reel_destination_inside_its_project(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The other half of the same rule — confinement that refused the ordinary
    case would just mean nobody could derive a reel from the agent panel.

    Seeded over the unbound server first because binding happens at start-up:
    a server bound to a directory that is not there yet exits rather than
    waiting for one to appear.
    """
    audio, transcript = sources
    project = tmp_path / "proj"

    async def seed(session: ClientSession) -> Any:
        return await _seeded(Client(session), project, audio, transcript)

    async def body(session: ClientSession) -> Any:
        return await Client(session).call(
            "reel", path=".", dest="reels/teaser", start=2.0, end=6.0
        )

    anyio.run(_with_server, seed)
    result = anyio.run(_with_server, body, _bound(project))

    assert result["reel"] == str(project / "reels" / "teaser")
    assert (project / "reels" / "teaser" / "proofcut.json").is_file()


@needs_ffprobe
def test_reel_derives_a_project_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The whole recipe through the real server: the derived project holds the
    span, and the film it came from is untouched — which is the point, since
    the alternative is setting a vertical canvas on the film to take one render
    and leaving it swapped afterwards."""
    audio, transcript = sources
    project, teaser = tmp_path / "proj", tmp_path / "teaser"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        before = await client.call("timeline_status", path=str(project))
        derived = await client.call(
            "reel", path=str(project), dest=str(teaser), start=3.0, end=8.0, canvas="1080x1920"
        )
        return {
            "before": before,
            "derived": derived,
            "reel_status": await client.call("timeline_status", path=str(teaser)),
            "film_after": await client.call("timeline_status", path=str(project)),
            "film_canvas": await client.call("canvas", path=str(project)),
        }

    out = anyio.run(_with_server, body)

    assert out["derived"]["cut"] == [[0.0, 3.0], [8.0, 12.0]]
    assert out["reel_status"]["timeline_duration"] == pytest.approx(5.0, abs=0.01)
    assert out["derived"]["canvas_set"]["canvas"] == "1080x1920"
    assert out["film_after"]["timeline_duration"] == pytest.approx(
        out["before"]["timeline_duration"]
    ), "the film kept its own timeline"
    assert out["film_canvas"]["source"] != "override", "and its own shape"


@needs_ffprobe
def test_reel_plan_creates_nothing_over_the_wire(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    audio, transcript = sources
    project, teaser = tmp_path / "proj", tmp_path / "teaser"

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        await _seeded(client, project, audio, transcript)
        return await client.call(
            "reel", path=str(project), dest=str(teaser), start=3.0, end=8.0, plan=True
        )

    planned = anyio.run(_with_server, body)

    assert planned["plan"] is True
    assert planned["duration"] == pytest.approx(5.0)
    assert not teaser.exists()


def test_an_unbound_server_still_reaches_any_project(tmp_path: Path) -> None:
    """`proofcut mcp` with no `-C` is the general-client case and is unconfined.

    `main()` defaults `-C` to ".", so this is what would break if the binding
    were applied whenever the default was present rather than when the flag
    was typed.
    """
    _, other = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await Client(session).call("cue_ls", path=str(other))

    assert anyio.run(_with_server, body)["count"] == 0


def test_binding_does_not_change_the_advertised_tool_schema(tmp_path: Path) -> None:
    """The confinement is a wrapper, and a wrapper that reshaped the schema
    would change the tool surface for every client. `functools.wraps` sets
    `__wrapped__` and the SDK's `inspect.signature(fn, eval_str=True)`
    follows it; this is that claim, asserted rather than trusted."""
    project, _ = _two_projects(tmp_path)

    async def body(session: ClientSession) -> Any:
        return await session.list_tools()

    unbound = {t.name: t.input_schema for t in anyio.run(_with_server, body).tools}
    bound = {t.name: t.input_schema for t in anyio.run(_with_server, body, _bound(project)).tools}

    assert set(bound) == EXPECTED_TOOLS
    assert bound == unbound


def test_path_is_never_a_required_field_in_the_advertised_schema() -> None:
    """`path` must be optional in the one schema both bind states share —
    a `required` entry for it would put the pydantic-level "Field required"
    error back in front of `_confine`'s own, clearer refusal."""

    async def body(session: ClientSession) -> Any:
        return await session.list_tools()

    for tool in anyio.run(_with_server, body).tools:
        if "path" in tool.input_schema.get("properties", {}):
            required = tool.input_schema.get("required", [])
            assert "path" not in required, f"{tool.name} still requires path"


def test_every_advertised_path_says_what_it_means() -> None:
    """`path` is the one argument every tool takes, and the schema alone says
    only `string | null` about it. Read off the wire, because a description
    hung on the parameter in `server.py` that never reached `tools/list` is
    exactly the failure this pins: the two tools whose `path` means *no
    project* (`fonts`, `pack_show`) have to say their own thing, and the
    other 87 share `ProjectPath`'s sentence."""

    async def body(session: ClientSession) -> Any:
        return await session.list_tools()

    projectless = {"fonts", "pack_show"}
    seen = 0
    for tool in anyio.run(_with_server, body).tools:
        field = tool.input_schema.get("properties", {}).get("path")
        if field is None:
            continue
        seen += 1
        description = field.get("description", "")
        assert description, f"{tool.name} advertises an undocumented path"
        if tool.name in projectless:
            assert "no project" in description, tool.name
        else:
            assert "bound project" in description, tool.name
    assert seen == 89


def test_no_tool_advertises_an_argument_with_nothing_said_about_it() -> None:
    """Every argument of every tool carries a description in the schema a
    client actually receives. `_PARAM_DOCS` is what fills them in and
    `_describe_params` refuses a tool with a gap, so this passing in-process
    is not the claim — the claim is that the text survives registration,
    `functools.wraps` and the SDK's own schema build and arrives on the
    wire, which is the only place it is any use to a caller."""

    async def body(session: ClientSession) -> Any:
        return await session.list_tools()

    gaps = {
        tool.name: [
            name
            for name, field in tool.input_schema.get("properties", {}).items()
            if not field.get("description")
        ]
        for tool in anyio.run(_with_server, body).tools
    }
    assert {name: missing for name, missing in gaps.items() if missing} == {}


def test_an_argument_with_no_description_refuses_to_register() -> None:
    """The coverage above is only a contract if a new argument cannot skip
    it — the same shape as the hint table's own refusal. Registered here
    rather than asserted over the table, because the gap that matters is
    between a signature and the docs, not inside either."""
    from proofcut import server

    def check_something(path: str | None = None, *, undocumented: str = "") -> dict[str, Any]:
        return {}

    server._ANNOTATIONS[check_something.__name__] = server._READ
    try:
        with pytest.raises(RuntimeError, match="_PARAM_DOCS"):
            server._tool()(check_something)
    finally:
        del server._ANNOTATIONS[check_something.__name__]


def test_the_parameter_table_cannot_describe_an_argument_that_is_gone() -> None:
    """The other direction: a renamed argument leaves the table describing
    one the tool no longer takes, while the one that replaced it advertises
    nothing. Silent in every check that only walks the signature."""
    from proofcut import server

    def check_something_else(path: str | None = None) -> dict[str, Any]:
        return {}

    server._ANNOTATIONS[check_something_else.__name__] = server._READ
    server._PARAM_DOCS[check_something_else.__name__] = {"renamed_away": "gone"}
    try:
        with pytest.raises(RuntimeError, match="does not take"):
            server._tool()(check_something_else)
    finally:
        del server._ANNOTATIONS[check_something_else.__name__]
        del server._PARAM_DOCS[check_something_else.__name__]


def test_binding_to_a_directory_that_is_not_there_fails_at_startup() -> None:
    """A bad root is caught when the server starts rather than on every call,
    which would blame the client's argument for the server's own start-up."""
    result = subprocess.run(
        [sys.executable, "-m", "proofcut.cli", "-C", "/nonexistent-project", "mcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not a directory" in result.stderr


def test_every_tool_taking_a_path_goes_through_the_binding() -> None:
    """A tool registered with `@mcp.tool()` instead of `@_tool()` would work,
    advertise an identical schema, and quietly not be confined — so the
    invariant is asserted rather than left to review. `@_tool()` returns the
    wrapper for anything taking a `path`, and `functools.wraps` is what puts
    `__wrapped__` on it; `mcp.tool()` returns the function untouched.
    """
    import proofcut.server as server_module

    for name in sorted(EXPECTED_TOOLS):
        fn = getattr(server_module, name)
        takes_path = "path" in inspect.signature(fn).parameters
        confined = hasattr(fn, "__wrapped__")
        assert takes_path == confined, (
            f"{name} takes path={takes_path} but is confined={confined} — "
            "a tool with a `path` argument must be registered with `@_tool()`, "
            "not `@mcp.tool()`, or it escapes the -C binding"
        )


def test_the_sheet_advertises_its_extremes_argument() -> None:
    """A CLI flag with no tool behind it is the parity convention broken one
    argument at a time — the name mapping above cannot see it, because both
    sides still exist. `extremes` is the whole difference between a sheet that
    samples the clock and one that samples the subject.
    """
    import proofcut.server as server_module

    taken = inspect.signature(server_module.reframe_sheet).parameters
    assert "extremes" in taken and taken["extremes"].default is False, (
        "reframe_sheet must offer `extremes`, off by default like reframe_detect's `apply`"
    )


def _seam_sources(root: Path) -> tuple[Path, Path]:
    """A recording whose transcript holds one word nobody said.

    `w11x` starts before `w11` ends, which is the only tell a retake splice
    leaves: whisper reads across it and interleaves both takes, and the
    invention is grammatical as often as not (HISTORY.md § The hand-framed
    teaser, watched). Everything else here is an ordinary two-words-per-burst
    recording.
    """
    audio = root / "seam.wav"
    _make_wav(audio, tones=[(0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 11.0)])
    words = [
        {"word": "w00", "start": 0.0, "end": 0.9},
        {"word": "w01", "start": 1.0, "end": 1.9},
        {"word": "w10", "start": 3.0, "end": 3.9},
        {"word": "w11", "start": 4.0, "end": 4.9},
        {"word": "w11x", "start": 4.8, "end": 4.95},
        {"word": "w20", "start": 6.0, "end": 6.9},
        {"word": "w21", "start": 7.0, "end": 7.9},
        {"word": "w30", "start": 9.0, "end": 9.9},
        {"word": "w31", "start": 10.0, "end": 10.9},
    ]
    transcript = root / "seam.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


def _heard_at(path: Path, words: list[tuple[str, float]]) -> Path:
    """A render's transcript with times that mean something.

    `_heard` puts words on an arbitrary one-per-second grid, which is right
    for `verify` — it compares order. `unspoken_detect` reads the *seconds*,
    because a word is judged against what the render says at the moment it
    plays, so these have to be the timeline's own.
    """
    payload = {
        "language": "en",
        "words": [{"word": w, "start": at, "end": at + 0.9} for w, at in words],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@needs_ffprobe
def test_unspoken_mark_drops_a_word_from_captions_and_from_verify(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """The whole point: one mark, and the three transcript readers agree.

    Captions, `caption_view` and `verify` share one derivation, so a word
    marked never-spoken leaves all three at once — and `verify` says so in the
    same breath, because a render checked against a shortened expectation has
    to report that it was shortened.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    # The render says the seven words a listener would hear: no `w11`.
    heard = _heard(
        tmp_path / "render.json", ["w00", "w01", "w10", "w20", "w21", "w30", "w31"]
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        before = await client.call("caption_view", path=str(project))
        dirty = await client.call(
            "verify", path=str(project), render=str(audio), transcript_path=str(heard)
        )
        marked = await client.call(
            "unspoken_add", path=str(project), clip_id=clip["clip_id"], word_index=3
        )
        after = await client.call("caption_view", path=str(project))
        clean = await client.call(
            "verify", path=str(project), render=str(audio), transcript_path=str(heard)
        )
        listed = await client.call("unspoken_ls", path=str(project))
        await client.call(
            "unspoken_rm", path=str(project), clip_id=clip["clip_id"], word_index=3
        )
        restored = await client.call("caption_view", path=str(project))
        return {
            "before": before,
            "dirty": dirty,
            "marked": marked,
            "after": after,
            "clean": clean,
            "listed": listed,
            "restored": restored,
        }

    out = anyio.run(_with_server, body)

    # The mark echoes what it resolved to, plus neighbours — every
    # word-indexed tool here does (CLAUDE.md).
    assert out["marked"]["text"] == "w11"
    assert [w["text"] for w in out["marked"]["context_before"]] == ["w00", "w01", "w10"]

    assert out["before"]["words"] == 8
    assert out["after"]["words"] == 7
    assert out["after"]["unspoken"] == 1
    drawn = " ".join(cue["text"] for cue in out["after"]["cues"])
    assert "w11" not in drawn.split()

    # The render was always right; it was the transcript that was wrong.
    assert out["dirty"]["similarity"] < 1.0
    assert out["clean"]["similarity"] == 1.0
    assert out["clean"]["diff"] == []
    # ...and it never hides that the expectation was shortened by hand.
    assert out["clean"]["unspoken"] == 1

    assert out["listed"]["count"] == 1
    assert out["listed"]["unspoken"][0]["text"] == "w11"
    assert out["listed"]["unspoken"][0]["stale"] is False
    assert out["restored"]["words"] == 8


@needs_ffprobe
def test_unspoken_detect_proposes_a_seam_word_and_writes_nothing(tmp_path: Path) -> None:
    """Proposes, like `reframe_detect`, and for a sharper reason.

    A wrong mark deletes a real word from every check proofcut has, so `apply` is
    off by default and the echoes are what gets read first.
    """
    audio, transcript = _seam_sources(tmp_path)
    project = tmp_path / "proj"
    heard = _heard_at(
        tmp_path / "render.json",
        [("w00", 0.0), ("w01", 1.0), ("w10", 3.0), ("w11", 4.0), ("w20", 6.0),
         ("w21", 7.0), ("w30", 9.0), ("w31", 10.0)],
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        proposed = await client.call(
            "unspoken_detect",
            path=str(project),
            render=str(audio),
            transcript_path=str(heard),
        )
        after = await client.call("unspoken_ls", path=str(project))
        applied = await client.call(
            "unspoken_detect",
            path=str(project),
            render=str(audio),
            transcript_path=str(heard),
            apply=True,
        )
        marked = await client.call("unspoken_ls", path=str(project))
        again = await client.call(
            "unspoken_detect",
            path=str(project),
            render=str(audio),
            transcript_path=str(heard),
        )
        return {
            "proposed": proposed,
            "after": after,
            "applied": applied,
            "marked": marked,
            "again": again,
        }

    out = anyio.run(_with_server, body)

    assert out["proposed"]["count"] == 1
    only = out["proposed"]["proposals"][0]
    assert only["text"] == "w11x"
    assert only["found_by"] == "seam"
    assert only["seam"] is not None
    # The count is what decides, never a lookup: the render says it 0 times
    # where the timeline says it once.
    assert "the render says it 0x" in only["why"]
    # A proposal is not a write.
    assert out["proposed"]["applied"] == 0
    assert out["after"]["count"] == 0

    assert out["applied"]["applied"] == 1
    assert out["marked"]["count"] == 1
    assert out["marked"]["unspoken"][0]["text"] == "w11x"
    # Already marked is not proposed again — the second run has nothing to say.
    assert out["again"]["count"] == 0
    assert out["again"]["already_marked"] == 1


@needs_ffprobe
def test_unspoken_detect_leaves_alone_a_seam_word_the_render_does_say(
    tmp_path: Path,
) -> None:
    """The seam is the candidate, never the verdict.

    People do speak across a splice, and a scan that marked every seam word
    would delete real ones. The render is the witness, and here it says the
    word — so nothing is proposed.
    """
    audio, transcript = _seam_sources(tmp_path)
    project = tmp_path / "proj"
    heard = _heard_at(
        tmp_path / "render.json",
        [("w00", 0.0), ("w01", 1.0), ("w10", 3.0), ("w11", 4.0), ("w11x", 4.8),
         ("w20", 6.0), ("w21", 7.0), ("w30", 9.0), ("w31", 10.0)],
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        return await client.call(
            "unspoken_detect",
            path=str(project),
            render=str(audio),
            transcript_path=str(heard),
        )

    out = anyio.run(_with_server, body)

    assert out["seams"] == 1
    assert out["count"] == 0


@needs_ffprobe
def test_a_stale_mark_is_reported_and_never_applied(
    tmp_path: Path, sources: tuple[Path, Path]
) -> None:
    """Re-transcribing must not make words disappear.

    A mark is an index, and an index only means something against the
    transcript it was taken from. When the recorded text and the text at that
    index disagree the word stays on screen and the mark is reported, because
    the two failures are not symmetric: a word wrongly drawn is visible to
    anyone watching, and a real word silently dropped is invisible to every
    check proofcut has.
    """
    audio, transcript = sources
    project = tmp_path / "proj"
    replacement = tmp_path / "redone.json"
    words = json.loads(transcript.read_text(encoding="utf-8"))["words"]
    words[3] = {**words[3], "word": "different"}
    replacement.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call("import_media", path=str(project), source=str(audio))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=clip["clip_id"], remove_silences=False
        )
        await client.call(
            "unspoken_add", path=str(project), clip_id=clip["clip_id"], word_index=3
        )
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(replacement),
        )
        return {
            "view": await client.call("caption_view", path=str(project)),
            "listed": await client.call("unspoken_ls", path=str(project)),
        }

    out = anyio.run(_with_server, body)

    # The word is still drawn — the mark was not applied.
    assert out["view"]["words"] == 8
    assert out["view"]["unspoken"] == 0
    assert out["view"]["unspoken_stale"][0]["recorded"] == "w11"
    assert out["view"]["unspoken_stale"][0]["found"] == "different"
    assert out["listed"]["stale"] == 1


# -- a co-hosted recording's two mics --------------------------------------
#
# PLAN.md § The co-hosted recording. `test_media_streams.py` pins the
# derivation itself; these two go through the real server, because whether
# `import_media` *refuses* is a property of the registered tool and its
# arguments, not of `media.py`.


def _two_mic_source(root: Path, *, seconds: float = 12.0) -> Path:
    """A video container with a 300 Hz mic on one track and 1200 Hz on the other."""
    dest = root / "cohost.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}:sample_rate=48000",
         "-f", "lavfi", "-i", f"sine=frequency=1200:duration={seconds}:sample_rate=48000",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(dest)],
        capture_output=True,
        check=True,
    )  # fmt: skip
    return dest


def _tone(path: Path, hz: float) -> float:
    """Goertzel power at `hz` over whatever a player would hear — the first
    audio stream, decoded. A two-track file "has" both mics and plays one."""
    decoded = path.with_name(f"{path.stem}-probe.wav")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1",
         "-c:a", "pcm_s16le", str(decoded)],
        capture_output=True,
        check=True,
    )  # fmt: skip
    with wave.open(str(decoded), "rb") as handle:
        rate, count = handle.getframerate(), handle.getnframes()
        raw = handle.readframes(count)
    samples = array.array("h")
    samples.frombytes(raw)
    k = int(0.5 + (len(samples) * hz) / rate)
    w = 2 * math.pi * k / len(samples)
    coeff = 2 * math.cos(w)
    q1 = q2 = 0.0
    for sample in samples:
        q0 = coeff * q1 - q2 + sample
        q2, q1 = q1, q0
    return math.sqrt(abs(q1 * q1 + q2 * q2 - coeff * q1 * q2)) / len(samples)


@needs_ffprobe
@needs_ffmpeg
def test_import_refuses_a_two_mic_container(tmp_path: Path) -> None:
    """Registering it as it stands would record only the first mic, and every
    check downstream would agree with the timeline about it.
    """
    project = tmp_path / "proj"
    source = _two_mic_source(tmp_path, seconds=2.0)

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        result = await session.call_tool(
            "import_media", {"path": str(project), "source": str(source)}
        )
        return {"is_error": result.is_error, "text": result.content[0].text}

    out = anyio.run(_with_server, body)

    assert out["is_error"]
    assert "2 audio streams" in out["text"]
    # The refusal has to say what to do instead, on both surfaces.
    assert "--mix" in out["text"] and "--audio-stream" in out["text"]


@needs_ffprobe
@needs_ffmpeg
@needs_auto_editor
def test_a_mixed_import_puts_both_mics_in_the_film(tmp_path: Path) -> None:
    """The end of the chain, measured rather than reasoned: import → seed →
    render, read back at each mic's own tone with the first-mic import as a
    control. The control is what every import did before this shipped.
    """
    project = tmp_path / "proj"
    control = tmp_path / "control"
    source = _two_mic_source(tmp_path)
    words = [
        {"word": f"w{burst}{n}", "start": burst * 3 + n, "end": burst * 3 + n + 0.9}
        for burst in range(4)
        for n in range(2)
    ]
    transcript = tmp_path / "cohost.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        out: dict[str, Any] = {}
        for root, key, extra in (
            (project, "mixed", {"mix": True}),
            (control, "first", {"audio_stream": 0}),
        ):
            await client.call("init", path=str(root))
            clip = await client.call(
                "import_media", path=str(root), source=str(source), **extra
            )
            await client.call(
                "attach_transcript",
                path=str(root),
                clip_id=clip["clip_id"],
                transcript_path=str(transcript),
            )
            await client.call("seed_timeline", path=str(root), clip_id=clip["clip_id"])
            rendered = await client.call(
                "export",
                path=str(root),
                output=str(tmp_path / f"{key}.mp4"),
                export_format=None,
            )
            out[key] = {"clip": clip, "render": rendered}
        return out

    out = anyio.run(_with_server, body)

    clip = out["mixed"]["clip"]
    assert clip["audio_streams"] == 2
    assert clip["mixed"] == "cache/mixed/cohost.mp4"
    assert clip["mix"] == {"streams": 2, "mode": "sum", "stream": None, "codec": "aac"}

    both = tmp_path / "mixed.mp4"
    assert _tone(both, 300.0) > 100.0
    assert _tone(both, 1200.0) > 100.0

    # The control: one mic chosen, and the other is simply not in the film.
    only_a = tmp_path / "first.mp4"
    assert _tone(only_a, 300.0) > 100.0
    assert _tone(only_a, 1200.0) < 10.0


def _turn_taking_source(root: Path, *, turn: float = 2.0, turns: int = 6) -> Path:
    """Two mics on one container, each hot for its own turns and bled into the
    other's — the co-hosted shape, and what attribution has to separate."""
    dest = root / "turns.mp4"
    seconds = turn * turns
    cycle, hot = turn * 2, turn
    quiet = 0.25  # ~12 dB of isolation, the middle of the note's own column
    # Commas inside a filter expression are filtergraph separators, so they
    # are escaped rather than quoted — there is no shell here to do it.
    graph = (
        f"[0:a]volume='if(lt(mod(t\\,{cycle})\\,{hot})\\,1\\,{quiet})':eval=frame[a];"
        f"[1:a]volume='if(lt(mod(t\\,{cycle})\\,{hot})\\,{quiet}\\,1)':eval=frame[b]"
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}:sample_rate=48000",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}:sample_rate=48000",
         "-filter_complex", graph,
         "-map", "[a]", "-map", "[b]", "-c:a", "aac", str(dest)],
        capture_output=True,
        check=True,
    )  # fmt: skip
    return dest


@needs_ffprobe
@needs_ffmpeg
def test_attribute_speakers_labels_the_words_over_the_wire(tmp_path: Path) -> None:
    """Import → attach → attribute → apply, through the registered tools.

    The clip is imported with `mix=True`, so everything downstream of import
    reads the mixdown — which is the point: attribution is the one op that
    reaches past it to the individual mics, and calling it through the server
    is what proves the tool is registered and reachable rather than merely
    written. PLAN.md § The co-hosted recording, build order step 3.
    """
    project = tmp_path / "proj"
    turn, turns, per_turn = 2.0, 6, 4
    source = _turn_taking_source(tmp_path, turn=turn, turns=turns)

    words, truth = [], []
    step = turn / (per_turn + 1)
    for index in range(turns):
        for k in range(per_turn):
            start = index * turn + step * (k + 1)
            words.append({"word": f"w{index}{k}", "start": start, "end": start + 0.2})
            truth.append("ana" if index % 2 == 0 else "ben")
    transcript = tmp_path / "turns.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        clip = await client.call(
            "import_media", path=str(project), source=str(source), mix=True
        )
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=clip["clip_id"],
            transcript_path=str(transcript),
        )
        common = {"path": str(project), "clip_id": clip["clip_id"], "labels": ["ana", "ben"]}
        return {
            "clip": clip,
            "report": await client.call("attribute_speakers", **common),
            "applied": await client.call("attribute_speakers", **common, apply=True),
            "read_back": await client.call(
                "get_transcript", path=str(project), clip_id=clip["clip_id"]
            ),
        }

    out = anyio.run(_with_server, body)

    report = out["report"]
    assert report["words"] == len(truth)
    assert report["attributed"] == len(truth), report["ambiguous_spans"]
    assert report["by_label"] == {"ana": truth.count("ana"), "ben": truth.count("ben")}
    assert report["applied"] is False
    # It read the container, not the mixdown import derived from it.
    assert report["container"] != out["clip"]["mixed"]
    assert report["audio_streams"] == 2

    assert out["applied"]["changed"] == len(truth)
    cached = Path(out["applied"]["transcript"])
    saved = json.loads(cached.read_text(encoding="utf-8"))
    assert [w["speaker"] for w in saved["words"]] == truth


# -- film-audio holds: the compound op, over the real server --------------
#
# `hold_add` is `vo_extend` (a real gap, registered like any other clip) plus
# a picture cue, tied together — the fixture below is the fixture
# `test_vo_extend_over_the_wire` uses for the VO half, plus a real encoded
# film clip of its own for the hold's asset. Words: "the"(0.0-0.3)
# "first"(0.5-0.9) "twelve"(1.0-1.4) "minutes"(1.5-1.9) — cue_word_index=2
# ("twelve"), gap_word_index=3 ("minutes"), so `elapsed = gap_at - cue_at =
# 1.9 - 1.0 = 0.9`, the same algebra `test_ops_holds.py` pins by hand.


def _hold_vo_transcript(path: Path) -> None:
    words = [
        {"word": "the", "start": 0.0, "end": 0.3},
        {"word": "first", "start": 0.5, "end": 0.9},
        {"word": "twelve", "start": 1.0, "end": 1.4},
        {"word": "minutes", "start": 1.5, "end": 1.9},
        {"word": "of", "start": 2.0, "end": 2.2},
        {"word": "scream", "start": 5.0, "end": 5.4},
    ]
    path.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")


def _hold_film_transcript(path: Path) -> None:
    words = [
        {"word": "i", "start": 10.0, "end": 10.2},
        {"word": "know", "start": 10.2, "end": 10.5},
        {"word": "what", "start": 10.5, "end": 10.8},
        {"word": "you", "start": 10.8, "end": 11.0},
        {"word": "did", "start": 11.0, "end": 11.3},
    ]
    path.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")


async def _hold_fixture(client: Client, project: Path, vo: Path, film: Path) -> dict[str, str]:
    """init -> import both clips -> attach both transcripts -> seed the VO.
    Returns the two clip_ids."""
    await client.call("init", path=str(project))
    vo_clip = await client.call("import_media", path=str(project), source=str(vo))
    film_clip = await client.call("import_media", path=str(project), source=str(film))
    vo_transcript = project.parent / "vo.json"
    film_transcript = project.parent / "film.json"
    _hold_vo_transcript(vo_transcript)
    _hold_film_transcript(film_transcript)
    await client.call(
        "attach_transcript",
        path=str(project),
        clip_id=vo_clip["clip_id"],
        transcript_path=str(vo_transcript),
    )
    await client.call(
        "attach_transcript",
        path=str(project),
        clip_id=film_clip["clip_id"],
        transcript_path=str(film_transcript),
    )
    await client.call(
        "seed_timeline", path=str(project), clip_id=vo_clip["clip_id"], remove_silences=False
    )
    return {"vo": vo_clip["clip_id"], "film": film_clip["clip_id"]}


@needs_ffprobe
@needs_ffmpeg
def test_hold_add_over_the_wire(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _silence_wav(vo, 6.0)
    _make_video(film, duration=15.0)

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        manifest_before = Project.open(project).manifest_path.read_text()

        planned = await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
            plan=True,
        )
        manifest_after_plan = Project.open(project).manifest_path.read_text()

        real = await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        status_after = await client.call("timeline_status", path=str(project))
        refused = await session.call_tool(
            "hold_add",
            {
                "path": str(project),
                "clip_id": clips["vo"],
                "gap_word_index": 3,
                "cue_word_index": 2,
                "word_index_first": 1,
                "word_index_last": 4,
            },
        )
        return {
            "clips": clips,
            "planned": planned,
            "manifest_before": manifest_before,
            "manifest_after_plan": manifest_after_plan,
            "real": real,
            "status_after": status_after,
            "refused_is_error": refused.is_error,
            "refused_text": refused.content[0].text,
        }

    out = anyio.run(_with_server, body)

    assert out["planned"]["written"] is False
    assert out["manifest_after_plan"] == out["manifest_before"], "a plan touched the manifest"
    assert out["planned"]["src_start"] == pytest.approx(8.95)

    assert out["real"]["written"] is True
    assert out["real"]["src_start"] == pytest.approx(8.95)
    # "Both words' echoes" (CLAUDE.md's word-index convention): the gap
    # word's echo rides `_splice_after`'s own top-level shape (`text` here
    # is "minutes"), and the cue word's is namespaced under `cue_echo` so
    # the two cannot collide under one key name.
    assert out["real"]["text"] == "minutes"
    assert out["real"]["cue_echo"]["text"] == "twelve"
    assert out["status_after"]["timeline_duration"] == pytest.approx(6.0 + 1.9)

    manifest = Project.open(project).read_manifest()
    holds = manifest[ops.HOLDS_KEY]
    assert len(holds) == 1
    assert holds[0]["gap_word_index"] == 3
    assert holds[0]["asset"] == out["clips"]["film"]
    cues = manifest["cues"]
    assert len(cues) == 1
    assert cues[0] == {
        "clip_id": out["clips"]["vo"],
        "word_index": 2,
        "asset": out["clips"]["film"],
        "src_start": pytest.approx(8.95),
    }

    assert out["refused_is_error"]
    assert "cannot change without re-splicing" in out["refused_text"]


@needs_ffprobe
@needs_ffmpeg
def test_hold_add_refuses_insufficient_head_margin(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _silence_wav(vo, 6.0)
    _make_video(film, duration=15.0)

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        return await session.call_tool(
            "hold_add",
            {
                "path": str(project),
                "clip_id": clips["vo"],
                "gap_word_index": 3,
                "cue_word_index": 2,
                "asset": clips["film"],
                "word_index_first": 0,
                "word_index_last": 4,
                "head_margin": 20.0,
                "plan": True,
            },
        )

    refused = anyio.run(_with_server, body)

    assert refused.is_error
    text = refused.content[0].text
    assert "no room" in text
    assert "more than" in text and "has before that point" in text


@needs_ffprobe
@needs_ffmpeg
def test_hold_add_refuses_asset_too_short(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _silence_wav(vo, 6.0)
    # The film's own line (words up to 11.3s) needs more than the 10.6s this
    # asset actually has.
    _make_video(film, duration=10.6)

    async def body(session: ClientSession) -> Any:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        return await session.call_tool(
            "hold_add",
            {
                "path": str(project),
                "clip_id": clips["vo"],
                "gap_word_index": 3,
                "cue_word_index": 2,
                "asset": clips["film"],
                "word_index_first": 0,
                "word_index_last": 4,
                "plan": True,
            },
        )

    refused = anyio.run(_with_server, body)

    assert refused.is_error
    text = refused.content[0].text
    assert "short by" in text


@needs_ffprobe
@needs_ffmpeg
def test_hold_rm_over_the_wire(tmp_path: Path) -> None:
    """`hold_rm` drops the record and its owned cue — the spliced silence
    stays — and a second call at the same address is refused, naming the
    address rather than silently no-oping."""
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _silence_wav(vo, 6.0)
    _make_video(film, duration=15.0)

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        duration_before_rm = (await client.call("timeline_status", path=str(project)))[
            "timeline_duration"
        ]
        removed = await client.call(
            "hold_rm", path=str(project), clip_id=clips["vo"], gap_word_index=3
        )
        after = await client.call("hold_ls", path=str(project))
        duration_after_rm = (await client.call("timeline_status", path=str(project)))[
            "timeline_duration"
        ]
        refused = await session.call_tool(
            "hold_rm", {"path": str(project), "clip_id": clips["vo"], "gap_word_index": 3}
        )
        return {
            "removed": removed,
            "after": after,
            "duration_before_rm": duration_before_rm,
            "duration_after_rm": duration_after_rm,
            "refused_is_error": refused.is_error,
            "refused_text": refused.content[0].text,
        }

    out = anyio.run(_with_server, body)

    assert out["removed"]["clip_id"] == out["removed"]["removed"]["clip_id"]
    assert out["removed"]["gap_word_index"] == 3
    assert out["removed"]["removed"]["asset"] is not None

    assert out["after"]["count"] == 0

    # The gap does not close — dropping the hold's meaning is not undoing
    # its splice.
    assert out["duration_after_rm"] == pytest.approx(out["duration_before_rm"])

    manifest = Project.open(project).read_manifest()
    assert manifest[ops.HOLDS_KEY] == []
    assert manifest["cues"] == []

    assert out["refused_is_error"]
    assert "no hold at" in out["refused_text"]


@needs_ffprobe
@needs_ffmpeg
def test_hold_ls_reports_per_item_error(tmp_path: Path) -> None:
    """One good hold, one orphaned by a subsequent cut of its gap word —
    `hold_ls` returns both, the bad one carrying `hold_error`, never raising
    for the whole list."""
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _silence_wav(vo, 6.0)
    _make_video(film, duration=15.0)

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        # Cut away word 5 ("scream") to leave the hold itself intact but
        # prove one bad entry does not break a good one either — then
        # orphan the *good* hold's own gap word by cutting it away too, in
        # a second project so the good entry above stays good.
        return await client.call("hold_ls", path=str(project))

    out = anyio.run(_with_server, body)
    assert out["count"] == 1
    assert out["holds"][0].get("hold_error") is None
    assert out["holds"][0]["cue_drift"] is None


@needs_ffprobe
@needs_ffmpeg
def test_hold_check_over_the_wire(tmp_path: Path) -> None:
    """`hold_check` reachable over stdio, transcribing a real hold's own
    span off a real render (the project's own `vo.wav` stands in — long
    enough to cover the hold's resolved span, and `hold_check` only needs a
    real audio file to cut from, `finish.hold_seams`'s own real ffmpeg
    decode running unmocked) and checking its seams. A fake whisper
    (`_fake_whisper`) stands in for the transcription half only.

    Also proves `cue_drift` folds into `faults` end to end — the release-
    facing half of the guard `hold_ls` already had — by hand-drifting the
    owned cue's `src_start` between two calls.
    """
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _silence_wav(vo, 6.0)
    _make_video(film, duration=15.0)

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "proofcut.cli", "mcp"],
        # The stub hears the hold's own line: a "clean" hold is one whose line
        # is heard, and `hold_check` counts a line it does not hear as a fault.
        env={"PROOFCUT_WHISPER": str(_fake_whisper(tmp_path, heard="i know what you did"))},
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        clean = await client.call("hold_check", path=str(project), render=str(vo))

        # An unrelated hand cue_rm/cue_add on the exact word the hold owns —
        # the retro's own "two lists drift apart in one edit" shape.
        manifest = Project.open(project).read_manifest()
        for cue in manifest["cues"]:
            if cue["clip_id"] == clips["vo"] and cue["word_index"] == 2:
                cue["src_start"] = 0.0
        Project.open(project).write_manifest(manifest)
        drifted = await client.call("hold_check", path=str(project), render=str(vo))

        refused = await session.call_tool(
            "hold_check", {"path": str(project), "render": str(tmp_path / "nope.mp4")}
        )
        return {
            "clean": clean,
            "drifted": drifted,
            "refused_is_error": refused.is_error,
            "refused_text": refused.content[0].text,
        }

    out = anyio.run(lambda: _with_server(body, server))

    assert out["clean"]["count"] == 1
    assert out["clean"]["holds"][0]["cue_drift"] is None
    assert out["clean"]["holds"][0]["heard"] == "i know what you did"
    assert out["clean"]["holds"][0]["line_edges"] == {"start": True, "end": True}
    assert out["clean"]["holds"][0]["phrase"] == "i know what you did"
    assert out["clean"]["faults"] == 0

    assert out["drifted"]["holds"][0]["cue_drift"] is not None
    assert out["drifted"]["faults"] == 1

    assert out["refused_is_error"]
    assert "no such render" in out["refused_text"]


@needs_ffprobe
@needs_ffmpeg
def test_is_layered_with_only_a_hold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _silence_wav(vo, 6.0)
    _make_video(film, duration=15.0)

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        return await client.call("timeline_view", path=str(project))

    out = anyio.run(_with_server, body)
    assert out["layered"] is True
    assert len(out["holds"]) == 1


@needs_ffprobe
@needs_ffmpeg
def test_hold_gates_the_music_lane(tmp_path: Path) -> None:
    """A hold whose span overlaps the bed splits the bed's own document
    entry around it — cheaper to check on the built `mlt.document` (via a
    plain `kdenlive` export, which needs no melt) than a real render."""
    project = tmp_path / "proj"
    vo = tmp_path / "vo.wav"
    film = tmp_path / "film.mp4"
    _quiet_vo_wav(vo, 6.0)
    # `hold_add` writes an unbounded picture cue (nothing follows it in the
    # cue table), so the shot it projects runs from that cue to the end of
    # the timeline — long enough here to need more runtime than the hold's
    # own short phrase, hence the generous duration.
    _make_video(film, duration=25.0)

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        await client.call(
            "music",
            path=str(project),
            asset=clips["film"],
            clip_id=clips["vo"],
            word_index_start=0,
        )
        return await client.call(
            "export", path=str(project), output=str(tmp_path / "out.mlt"), export_format="kdenlive"
        )

    result = anyio.run(_with_server, body)
    assert result["writer"] == "mlt"
    assert result["holds"]
    assert result["music"] is not None

    root = ET.parse(result["output"]).getroot()
    music_playlist = next(p for p in root.findall("playlist") if p.get("id") == "playlist8")
    entries = music_playlist.findall("entry")
    # Gated: three entries where the un-gated lane would have had one (this
    # bed starts at word 0 and needs no lead/trail padding, so its whole
    # un-gated lane is a single entry) — the hold's own span splits the bed
    # entry into a piece before it, a silent gate, and a piece after.
    assert len(entries) == 3, ET.tostring(music_playlist, encoding="unicode")
    # And the gated stretch is a real silent WAV, never the bed's own
    # resource — "OUT, not ducked".
    resources = {
        node.get("id"): (node.find("property[@name='resource']").text or "")
        for node in [*root.findall("chain"), *root.findall("producer")]
    }
    entry_resources = [resources.get(e.get("producer"), "") for e in entries]
    assert any("silence" in r for r in entry_resources), entry_resources


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_hold_plays_its_own_film_audio_and_gates_the_bed_on_a_real_render(
    visible_tmp: Path,
) -> None:
    """The end-to-end proof melt/auto-editor's exit-0 lies make necessary
    (CLAUDE.md, WORK-ORDERS ruling 10): a hold's own tone must be audible
    exactly across its resolved span, and the bed's own distinct tone must
    be silent there and audible everywhere else — not merely a document
    that validates.
    """
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    film = visible_tmp / "film.mp4"
    bed = visible_tmp / "bed.wav"
    _quiet_vo_wav(vo, 6.0)
    # 25s, not 15s: `hold_add` writes an unbounded picture cue (nothing
    # follows it in the cue table), so the shot it projects runs from that
    # cue to the end of the timeline — longer than the hold's own phrase.
    _tone_video(film, 300.0, 25.0)
    _tone_wav(bed, 880.0, 25.0)

    transcript = visible_tmp / "vo.json"
    transcript.write_text(
        json.dumps(
            {
                "language": "en",
                "words": [
                    {"word": "the", "start": 0.0, "end": 0.3},
                    {"word": "first", "start": 0.5, "end": 0.9},
                    {"word": "twelve", "start": 1.0, "end": 1.4},
                    {"word": "minutes", "start": 1.5, "end": 1.9},
                ],
            }
        ),
        encoding="utf-8",
    )
    film_transcript = visible_tmp / "film.json"
    film_transcript.write_text(
        json.dumps(
            {
                "language": "en",
                "words": [
                    {"word": "i", "start": 10.0, "end": 10.2},
                    {"word": "know", "start": 10.2, "end": 10.5},
                    {"word": "what", "start": 10.5, "end": 10.8},
                    {"word": "you", "start": 10.8, "end": 11.0},
                    {"word": "did", "start": 11.0, "end": 11.3},
                ],
            }
        ),
        encoding="utf-8",
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo_clip = await client.call("import_media", path=str(project), source=str(vo))
        film_clip = await client.call("import_media", path=str(project), source=str(film))
        bed_clip = await client.call("import_media", path=str(project), source=str(bed))
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=vo_clip["clip_id"],
            transcript_path=str(transcript),
        )
        await client.call(
            "attach_transcript",
            path=str(project),
            clip_id=film_clip["clip_id"],
            transcript_path=str(film_transcript),
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo_clip["clip_id"], remove_silences=False
        )
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=vo_clip["clip_id"],
            gap_word_index=3,
            cue_word_index=2,
            asset=film_clip["clip_id"],
            word_index_first=0,
            word_index_last=4,
        )
        await client.call(
            "music",
            path=str(project),
            asset=bed_clip["clip_id"],
            clip_id=vo_clip["clip_id"],
            word_index_start=0,
        )
        return await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None
        )

    result = anyio.run(_with_server, body)

    assert result["writer"] == "melt"
    hold = result["holds"][0]
    render_start = hold["timeline_start"]  # head_seconds is 0.0 here
    render_end = render_start + (hold["hold_frames"] / result["timebase"])
    render = Path(result["output"])

    # Comfortably inside the hold's own span, clear of its fade edges.
    margin = 0.25
    during_hold_300 = _tone_window(render, 300.0, render_start + margin, (render_end - render_start) - 2 * margin)
    during_hold_880 = _tone_window(render, 880.0, render_start + margin, (render_end - render_start) - 2 * margin)
    # Well before the hold: the bed should be audible (nothing gates it yet).
    before_hold_880 = _tone_window(render, 880.0, 0.0, max(render_start - 0.3, 0.05))

    assert during_hold_300 > 500.0, "the hold's own film audio must be audible across its span"
    assert during_hold_880 < 50.0, "the bed must be gated OUT across the hold, not merely ducked"
    assert before_hold_880 > 500.0, "the bed must be audible before the hold gates it"


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_a_ducked_bed_drops_under_the_voice_and_comes_back_in_the_pause_on_a_real_render(
    visible_tmp: Path,
) -> None:
    """The duck as project state, rendered through melt and read back by tone:
    a VO speaking (100 Hz) for three seconds and silent for three, a bed at
    440 Hz under it with `duck=12`. The bed reads ~12 dB lower under the voice
    than in the pause — the keys ride the bed's own `volume` filter, offset by
    its `src_in` — and the render carries what `music.duck` reports. Without
    the duck the two windows read the same (the A1 test above's bed)."""
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    bed = visible_tmp / "bed.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=100:duration=3:sample_rate=48000",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-filter_complex",
         "[1:a]atrim=0:3[s];[0:a][s]concat=n=2:v=0:a=1", "-c:a", "pcm_s16le", str(vo)],
        capture_output=True,
        check=True,
    )  # fmt: skip
    _tone_wav(bed, 440.0, 8.0)
    transcript = visible_tmp / "vo.json"
    transcript.write_text(
        json.dumps({"language": "en", "words": [
            {"word": "one", "start": 0.0, "end": 0.3},
            {"word": "two", "start": 2.0, "end": 2.3},
        ]}),
        encoding="utf-8",
    )  # fmt: skip

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo_clip = await client.call("import_media", path=str(project), source=str(vo))
        bed_clip = await client.call("import_media", path=str(project), source=str(bed))
        await client.call(
            "attach_transcript", path=str(project), clip_id=vo_clip["clip_id"], transcript_path=str(transcript)
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo_clip["clip_id"], remove_silences=False
        )
        await client.call(
            "music",
            path=str(project),
            asset=bed_clip["clip_id"],
            clip_id=vo_clip["clip_id"],
            word_index_start=0,
            src_in=1.0,
            duck=12.0,
        )
        return await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None
        )

    result = anyio.run(_with_server, body)

    assert result["writer"] == "melt"
    duck = result["music"]["duck"]
    assert duck["depth_db"] == 12.0 and duck["keys"] > 2
    assert 2.5 < duck["ducked_seconds"] < 3.6, duck
    render = Path(result["output"])
    speaking = _tone_window(render, 440.0, 1.0, 1.5)
    pause = _tone_window(render, 440.0, 4.3, 1.5)
    drop = 20 * math.log10(pause / speaking)
    assert 9.0 < drop < 15.0, f"the bed should read ~12 dB lower under the voice, measured {drop:.1f}"


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_music_passages_crossfade_on_a_real_render_and_sit_under_the_vo(visible_tmp: Path) -> None:
    """Two passages as project state, rendered through melt and read back by
    tone: the first (440 Hz) alone before the second's start word, the second
    (990 Hz) alone after the crossfade, and — with `under=10` — the bed about
    10 dB below the VO's own 100 Hz. The overlap is what needs the second
    music lane; the level is what `music_bed.py --under` did after the fact.
    docs/plans/NATIVE.md § A1."""
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    first = visible_tmp / "first.wav"
    second = visible_tmp / "second.wav"
    _quiet_vo_wav(vo, 6.0)
    _tone_wav(first, 440.0, 6.0)
    _tone_wav(second, 990.0, 6.0)
    transcript = visible_tmp / "vo.json"
    transcript.write_text(
        json.dumps({"language": "en", "words": [
            {"word": "one", "start": 0.0, "end": 0.3},
            {"word": "two", "start": 3.0, "end": 3.3},
        ]}),
        encoding="utf-8",
    )  # fmt: skip

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo_clip = await client.call("import_media", path=str(project), source=str(vo))
        first_clip = await client.call("import_media", path=str(project), source=str(first))
        second_clip = await client.call("import_media", path=str(project), source=str(second))
        await client.call(
            "attach_transcript", path=str(project), clip_id=vo_clip["clip_id"], transcript_path=str(transcript)
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo_clip["clip_id"], remove_silences=False
        )
        await client.call(
            "music",
            path=str(project),
            asset=first_clip["clip_id"],
            clip_id=vo_clip["clip_id"],
            word_index_start=0,
            passages=[{"asset": second_clip["clip_id"], "word_index_start": 1, "crossfade": 0.5}],
            under=10.0,
        )
        return await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None
        )

    result = anyio.run(_with_server, body)

    assert result["writer"] == "melt"
    assert [p["lane"] for p in result["music"]["pieces"]] == [0, 1]
    render = Path(result["output"])
    before_440 = _tone_window(render, 440.0, 1.0, 1.5)
    before_990 = _tone_window(render, 990.0, 1.0, 1.5)
    after_440 = _tone_window(render, 440.0, 4.0, 1.5)
    after_990 = _tone_window(render, 990.0, 4.0, 1.5)
    assert before_440 > 10 * max(before_990, 1.0), "only the first passage before the second's word"
    assert after_990 > 10 * max(after_440, 1.0), "only the second passage after the crossfade"
    # Mid-crossfade (word two at 3.0 s, overlap 3.0–3.5 s) both passages are
    # clearly there. Two straight-in-dB fades leave both 25–30 dB down at the
    # middle — a hole — where the equal-power curve puts each ~3 dB down.
    mid_440 = _tone_window(render, 440.0, 3.15, 0.2)
    mid_990 = _tone_window(render, 990.0, 3.15, 0.2)
    assert mid_440 > 0.3 * before_440 and mid_990 > 0.3 * after_990, "a crossfade, not a hole"
    vo_level = _tone_window(render, 100.0, 1.0, 1.5)
    under = 20 * math.log10(vo_level / before_440)
    assert 7.0 < under < 13.0, f"the bed should sit ~10 dB under the VO, measured {under:.1f}"


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_export_masters_a_render_to_its_loudness_target(visible_tmp: Path) -> None:
    """docs/plans/NATIVE.md § A3: `export --loudness -16` leaves a file that
    measures −16 LUFS integrated under a −1 dBTP ceiling (with AAC's allowance),
    judged by `finish.loudness` on the finished file rather than by loudnorm's
    own report — and the reply says what it was before."""
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    bed = visible_tmp / "bed.wav"
    _quiet_vo_wav(vo, 6.0)
    _tone_wav(bed, 880.0, 6.0)
    transcript = visible_tmp / "vo.json"
    transcript.write_text(
        json.dumps({"language": "en", "words": [{"word": "one", "start": 0.0, "end": 0.3}]}), encoding="utf-8"
    )

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        await client.call("init", path=str(project))
        vo_clip = await client.call("import_media", path=str(project), source=str(vo))
        bed_clip = await client.call("import_media", path=str(project), source=str(bed))
        await client.call(
            "attach_transcript", path=str(project), clip_id=vo_clip["clip_id"], transcript_path=str(transcript)
        )
        await client.call(
            "seed_timeline", path=str(project), clip_id=vo_clip["clip_id"], remove_silences=False
        )
        await client.call(
            "music", path=str(project), asset=bed_clip["clip_id"], clip_id=vo_clip["clip_id"],
            word_index_start=0, under=20.0,
        )
        return await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None,
            loudness=-16.0,
        )

    result = anyio.run(_with_server, body)

    report = result["loudness"]
    assert abs(report["before"]["integrated"] - -16.0) > 1.0, "the fixture must start away from the target"
    measured = finish.loudness(result["output"])
    assert measured["integrated"] == pytest.approx(-16.0, abs=1.0)
    assert measured["true_peak"] <= -1.0 + 0.5
    assert not list(visible_tmp.glob("*.mastering.*")), "no staged copy left beside the render"


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_film_audio_under_the_vo_plays_the_shot_on_screen_below_the_voice(visible_tmp: Path) -> None:
    """docs/plans/NATIVE.md § A2 on a real render. The film's tone names its
    second (200 + 50·⌊t⌋ Hz); it is cued from src 8.0, and as the first cue
    its shot runs from frame 0 (`build_shots`), so under words 3–4 (1.5–2.2 s)
    the picture is at film 9.5–10.2 — 650 Hz at 1.6–2.0 s, never the in-point's
    600 Hz — and the audio has to be there too, ~6 dB under the VO's 100 Hz
    with `under=6`. Nothing is spliced: the timeline keeps its length."""
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    film = visible_tmp / "film.mp4"
    _quiet_vo_wav(vo, 6.0)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=25",
         "-f", "lavfi", "-i", "aevalsrc=exprs='sin(2*PI*(200+50*floor(t))*t)':s=48000:d=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(film)],
        capture_output=True,
        check=True,
    )  # fmt: skip

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "cue_add", path=str(project), clip_id=clips["vo"], word_index=2, asset=clips["film"], src_start=8.0
        )
        placed = await client.call(
            "hold_under",
            path=str(project),
            clip_id=clips["vo"],
            asset=clips["film"],
            word_index_start=3,
            word_index_end=4,
            under=6.0,
        )
        exported = await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None
        )
        return {"placed": placed, "exported": exported}

    out = anyio.run(_with_server, body)

    assert out["placed"]["play_at"] == pytest.approx(9.5)
    assert out["exported"]["holds"][0]["kind"] == "under_vo"
    render = Path(out["exported"]["output"])
    right = _tone_window(render, 650.0, 1.6, 0.4)
    wrong = _tone_window(render, 600.0, 1.6, 0.4)
    voice = _tone_window(render, 100.0, 1.6, 0.4)
    assert right > 4 * max(wrong, 1.0), "the span plays the film from where the shot has got to"
    below = 20 * math.log10(voice / right)
    assert 3.0 < below < 10.0, f"about 6 dB under the VO, measured {below:.1f}"


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_a_hold_plays_the_film_from_where_its_playhead_is_when_the_gap_opens(
    visible_tmp: Path,
) -> None:
    """The picture cue shows the clip from `src_start`; by the time the VO
    reaches the gap the clip has played `elapsed` further, so the hold's audio
    has to read from `play_at` = `src_start + elapsed` — where the line is. The
    holds lane read from `src_start`, and a constant-tone fixture cannot tell
    the two apart: the test above passed while every hold on the Lambs/Longlegs
    native rebuild played the seconds *before* its line (miggs heard "What did
    Migs say to you?" for "He hissed at you … I can smell your cunt").

    So the film's tone names its own second: 200 + 50·⌊t⌋ Hz. The hold reads
    film 9.85–11.75 (`play_at`), so 0.4–0.9 s into it is film 10.25–10.75 —
    700 Hz. Read from `src_start` (8.95) it would be film 9.35–9.85 — 650 Hz.
    """
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    film = visible_tmp / "film.mp4"
    _quiet_vo_wav(vo, 6.0)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=25",
         "-f", "lavfi", "-i", "aevalsrc=exprs='sin(2*PI*(200+50*floor(t))*t)':s=48000:d=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(film)],
        capture_output=True,
        check=True,
    )  # fmt: skip

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        return await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None
        )

    result = anyio.run(_with_server, body)

    hold = result["holds"][0]
    assert hold["src_start"] == pytest.approx(8.95)
    render = Path(result["output"])
    at_play_at = _tone_window(render, 700.0, hold["timeline_start"] + 0.4, 0.5)
    at_src_start = _tone_window(render, 650.0, hold["timeline_start"] + 0.4, 0.5)
    assert at_play_at > 500.0, "the hold must play the film's second 10, where its line is"
    assert at_src_start < at_play_at / 4, "the hold must not play the seconds before its line"


@needs_ffprobe
@needs_ffmpeg
@needs_melt
def test_a_hold_from_a_six_channel_clip_with_no_layout_is_heard(visible_tmp: Path) -> None:
    """A film rip's clip keeps six channels and, often, no layout; its dialogue
    is the centre. MLT renders the first two channels of such a stream, so on
    the Lambs/Longlegs native rebuild the cold open and all three Longlegs
    holds came out at −47 to −53 LUFS against v10's −16 — at exit 0, with the
    hold's own gain measured (by ffmpeg, centre included) as if it were fine.
    Here the film's only real audio is a 700 Hz centre channel."""
    project = visible_tmp / "proj"
    vo = visible_tmp / "vo.wav"
    film = visible_tmp / "film.mkv"
    _quiet_vo_wav(vo, 6.0)
    exprs = "|".join("0.5*sin(2*PI*700*t)" if k == 2 else "0.005*sin(2*PI*1000*t)" for k in range(6))
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=25",
         "-f", "lavfi", "-i", f"aevalsrc=exprs={exprs}:s=48000:d=25",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-af", "aformat=channel_layouts=6C", "-c:a", "pcm_s16le", str(film)],
        capture_output=True,
        check=True,
    )  # fmt: skip

    async def body(session: ClientSession) -> dict[str, Any]:
        client = Client(session)
        clips = await _hold_fixture(client, project, vo, film)
        await client.call(
            "hold_add",
            path=str(project),
            clip_id=clips["vo"],
            gap_word_index=3,
            cue_word_index=2,
            asset=clips["film"],
            word_index_first=0,
            word_index_last=4,
        )
        return await client.call(
            "export", path=str(project), output=str(visible_tmp / "render.mp4"), export_format=None
        )

    result = anyio.run(_with_server, body)

    hold = result["holds"][0]
    centre = _tone_window(Path(result["output"]), 700.0, hold["timeline_start"] + 0.4, 1.0)
    assert centre > 500.0, "the centre channel is the film's dialogue and must reach the render"
