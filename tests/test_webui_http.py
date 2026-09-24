"""The web UI, exercised over a real socket.

The same discipline `test_server_stdio.py` applies to MCP: a handler function
called directly proves nothing about whether it is routed, whether Range works,
or whether the guards that make a localhost mutating server safe actually fire.
So these start the real `ThreadingHTTPServer` and speak HTTP to it.
"""

from __future__ import annotations

import base64
import http.client
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from stubs import write_stub

from proofcut import media, ops, progress, renderlog, webui
from proofcut import timeline as tl
from proofcut.faces import FaceError
from proofcut.project import Project, ProjectError

needs_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe is not installed"
)

pytestmark = needs_ffprobe


#: The undo depth a project has the moment it is seeded, before anyone edits
#: it. `import_media` and `seed_timeline` each write the manifest, and a
#: manifest write is a snapshot now (docs/plans/POLISH.md § Step 03) — most authoring
#: state lives there, so undo had to cover it. "Nothing has been edited yet"
#: is therefore this number rather than zero. Named once, so a change in what
#: setup does is explained in one place instead of eight.
SEEDED_DEPTH = 2


def _make_wav(path: Path, *, duration: float = 12.0) -> None:
    rate = 22050
    with wave.open(str(path), "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * duration)):
            value = int(12000 * math.sin(2 * math.pi * 220 * (i / rate)))
            frames += struct.pack("<h", value)
        out.writeframes(bytes(frames))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A one-clip project: 12s of audio, eight words, no silence pass.

    `remove_silences=False` on purpose — a seeded-with-silences timeline would
    drag auto-editor into a test about HTTP. The cuts these tests make are what
    produce the seams.
    """
    root = tmp_path / "proj"
    audio = tmp_path / "vo.wav"
    _make_wav(audio)

    words = [{"word": f"w{n}", "start": float(n), "end": n + 0.9} for n in range(8)]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    ops.init(root)
    imported = ops.import_media(root, audio, clip_id="vo")
    ops.attach_transcript(root, imported["clip_id"], transcript)
    ops.seed_timeline(root, "vo", remove_silences=False)
    return root


@pytest.fixture
def server(project: Path) -> Iterator[str]:
    """The real server on a free port, torn down after the test."""
    httpd = webui.make_server(project, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


def _get(url: str, **kwargs: Any) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, **kwargs)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _json(url: str, **kwargs: Any) -> tuple[int, Any]:
    status, _, body = _get(url, **kwargs)
    return status, json.loads(body)


def _post(url: str, payload: dict[str, Any], *, content_type: str = "application/json") -> tuple[int, Any]:
    return _json(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": content_type},
        method="POST",
    )


def _host_and_port(server: str) -> tuple[str, int]:
    parts = urlsplit(server)
    assert parts.hostname is not None
    assert parts.port is not None
    return parts.hostname, parts.port


def _sse_events(resp: http.client.HTTPResponse) -> Iterator[tuple[str, Any]]:
    """Yield `(event, data)` pairs off an open `/api/events` connection.

    Reads raw lines off the still-open socket rather than `resp.read()`,
    which would block until the connection closes — exactly what an SSE
    stream never does on its own.
    """
    event = "message"
    while True:
        raw = resp.readline()
        if not raw:
            return
        line = raw.decode("utf-8").rstrip("\n")
        if line == "":
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data = json.loads(line[len("data:") :].strip())
            yield event, data
            event = "message"


def _agent_stub_body(
    canned: dict[str, Any], *, argv_file: Path | None = None, pid_file: Path | None = None
) -> str:
    """The Python every fake `claude` below runs (`stubs.write_stub`).

    Records what it is asked to, echoes one stream-json line, then blocks
    reading stdin so the process stays alive the way the real subprocess
    would, rather than exiting and racing the reader thread. The argv lands by
    rename, so a test polling for the file never reads it half-written.
    """
    lines = ["import json, os, sys", "from pathlib import Path"]
    if pid_file is not None:
        lines.append(f"with open({str(pid_file)!r}, 'a', encoding='utf-8') as f: f.write(f'{{os.getpid()}}\\n')")
    if argv_file is not None:
        partial = f"{str(argv_file)!r} + '.partial'"
        lines.append(f"Path({partial}).write_text(''.join(a + '\\n' for a in sys.argv[1:]), encoding='utf-8')")
        lines.append(f"os.replace({partial}, {str(argv_file)!r})")
    lines.append(f"print(json.dumps({canned!r}), flush=True)")
    lines.append("sys.stdin.read()")
    return "\n".join(lines) + "\n"


def _write_agent_stub(path: Path, argv_file: Path, canned: dict[str, Any]) -> Path:
    """A fake `claude` binary: records its own argv, echoes one stream-json line."""
    return write_stub(path, _agent_stub_body(canned, argv_file=argv_file))


def _write_pid_recording_stub(path: Path, pid_file: Path, canned: dict[str, Any]) -> Path:
    """Same shape as `_write_agent_stub`, plus its own PID appended to
    `pid_file` on every invocation — proves a respawn is a genuinely new
    process rather than the same one reused.
    """
    return write_stub(path, _agent_stub_body(canned, pid_file=pid_file))


def _write_pid_and_argv_stub(
    path: Path, pid_file: Path, argv_file: Path, canned: dict[str, Any]
) -> Path:
    """`_write_pid_recording_stub` plus `_write_agent_stub`'s own argv
    capture, combined: a model change has to be proven on both axes at
    once — a genuinely new process (the PID) that actually got the new
    `--model` (the argv) rather than the old one respawned unchanged.
    """
    return write_stub(path, _agent_stub_body(canned, pid_file=pid_file, argv_file=argv_file))


# -- the read model -------------------------------------------------------


def test_view_reports_segments_and_no_seams_before_a_cut(server: str) -> None:
    status, payload = _json(f"{server}/api/view")
    assert status == 200
    assert payload["clip_id"] == "vo"
    assert len(payload["segments"]) == 1
    assert payload["seams"] == []
    assert payload["segments"][0]["timeline_start"] == pytest.approx(0.0)
    assert len(payload["words"]) == 8
    assert all(word["present"] for word in payload["words"])


def test_a_cut_names_its_seam_by_the_words_either_side(server: str) -> None:
    status, _ = _post(f"{server}/api/cut", {"clip_id": "vo", "ranges": [[3, 4]], "mode": "cut"})
    assert status == 200

    _, payload = _json(f"{server}/api/view")
    assert len(payload["seams"]) == 1
    seam = payload["seams"][0]
    # The property the whole project defends: the boundary is named by words,
    # not by the timeline second it currently happens to sit at.
    assert seam["before"]["index"] == 2
    assert seam["after"]["index"] == 5
    assert seam["removed"] == pytest.approx(1.9)  # words 3 and 4: 3.0 -> 4.9


def test_cut_words_report_present_false_and_no_timeline_position(server: str) -> None:
    _post(f"{server}/api/cut", {"clip_id": "vo", "ranges": [[3, 4]], "mode": "cut"})
    _, payload = _json(f"{server}/api/view")
    words = {w["index"]: w for w in payload["words"]}

    assert words[3]["present"] is False
    assert words[3]["timeline_start"] is None
    assert words[4]["present"] is False
    # And the words after the hole moved earlier, which is what a view drawn
    # off the transcript instead of the edit would get wrong.
    assert words[5]["present"] is True
    assert words[5]["timeline_start"] == pytest.approx(5.0 - 1.9)


def test_a_word_a_cut_only_half_removes_is_reported_partial(server: str) -> None:
    # Cut by time, straight through the middle of word 3 (3.0-3.9).
    status, _ = _post(f"{server}/api/cut-at", {"spans": [[3.5, 4.5]]})
    assert status == 200

    _, payload = _json(f"{server}/api/view")
    word = next(w for w in payload["words"] if w["index"] == 3)
    # Survival is an overlap test, never containment (CLAUDE.md) — half a word
    # left is present, and saying so is the point.
    assert word["present"] is True
    assert word["partial"] is True
    assert word["covered"] == pytest.approx(0.5)


def test_overlapping_cut_at_spans_refuse_as_400_not_500(server: str) -> None:
    # ops._reject_overlapping_spans raises timeline.TimelineError, a member
    # of webui.EXPECTED — this must surface as a readable 400, never the
    # traceback-keeping 500 branch reserved for a real bug.
    status, payload = _post(f"{server}/api/cut-at", {"spans": [[1.0, 3.0], [2.0, 4.0]]})
    assert status == 400
    assert "overlap" in payload["error"]

    # And nothing got through: an overlap refusal is refused as a whole call.
    _, view = _json(f"{server}/api/view")
    assert view["undo_depth"] == SEEDED_DEPTH


def test_cut_at_plan_true_returns_ops_cut_by_time_own_return_value(
    project: Path, server: str
) -> None:
    """The route is a thin dispatch (`webui._cut_at`) — its response body
    must be exactly `ops.cut_by_time`'s own dict, not a re-shaped echo of it
    (CLAUDE.md: the web UI draws and plays, it never decides)."""
    status, payload = _post(f"{server}/api/cut-at", {"spans": [[3.5, 4.5]], "plan": True})
    assert status == 200
    assert payload["plan"] is True

    # Nothing else touched the project between init and this call, so a
    # direct `ops` call against the same on-disk state must agree byte for
    # byte — the plan path never wrote (`plan=True` skips `_save_edit`).
    expected = ops.cut_by_time(project, spans=[[3.5, 4.5]], plan=True)
    assert payload == expected

    _, view = _json(f"{server}/api/view")
    assert view["undo_depth"] == SEEDED_DEPTH
    assert view["words"][3]["present"] is True  # nothing actually cut


def test_cut_at_plan_false_actually_mutates_the_timeline(server: str) -> None:
    _, before = _json(f"{server}/api/view")

    status, applied = _post(f"{server}/api/cut-at", {"spans": [[3.5, 4.5]]})
    assert status == 200
    assert "plan" not in applied
    assert applied["duration_after"] == pytest.approx(before["timeline_duration"] - applied["removed"])
    assert applied["removed"] > 0

    _, view = _json(f"{server}/api/view")
    assert view["timeline_duration"] == pytest.approx(applied["duration_after"])
    assert view["timeline_duration"] < before["timeline_duration"]
    assert view["undo_depth"] == SEEDED_DEPTH + 1
    word = next(w for w in view["words"] if w["index"] == 3)
    assert word["present"] is True
    assert word["partial"] is True


def test_cut_at_requires_json_content_type(server: str) -> None:
    for ctype in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data"):
        status, payload = _post(
            f"{server}/api/cut-at", {"spans": [[3.5, 4.5]]}, content_type=ctype
        )
        assert status == 400, ctype
        assert "application/json" in payload["error"]

    _, view = _json(f"{server}/api/view")
    assert view["undo_depth"] == SEEDED_DEPTH  # nothing got through


def test_cut_at_refuses_a_non_loopback_host(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/api/cut-at",
        data=json.dumps({"spans": [[3.5, 4.5]]}).encode(),
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403

    _, view = _json(f"{server}/api/view")
    assert view["undo_depth"] == SEEDED_DEPTH


def test_cut_at_404s_under_picker_root_before_a_project_is_open(
    project: Path, picker_server: str
) -> None:
    """§D.1's bind-order rule (already proven for `/api/finish`) holds for
    `/api/cut-at` too: it sits behind `_project_bound()`, so it 404s with
    the same 'no project open' message before `/api/open`, never a 200
    against an unbound `self.project_root`."""
    status, payload = _post(f"{picker_server}/api/cut-at", {"spans": [[3.5, 4.5]]})
    assert status == 404
    assert "no project open" in payload["error"]

    status, _ = _post(f"{picker_server}/api/open", {"path": str(project)})
    assert status == 200

    status, applied = _post(f"{picker_server}/api/cut-at", {"spans": [[3.5, 4.5]]})
    assert status == 200
    assert applied["removed"] > 0


def _suspect_duration_project(tmp_path: Path) -> Path:
    """A one-clip project whose word 3 claims 3.96s against a 0.3s median —
    the Scream VO's 'bit', in miniature (`_suspect_duration_sources`,
    `test_server_stdio.py`) — reused here so a plain `_make_wav` (no `tones`
    parameter in this file) is enough, since `_suspect_durations` scores
    only word timings, never audio content (`ops._suspect_durations`).
    """
    root = tmp_path / "proj"
    audio = tmp_path / "vo.wav"
    _make_wav(audio, duration=6.0)

    words = [
        {"word": "so", "start": 0.0, "end": 0.3},
        {"word": "much", "start": 0.3, "end": 0.6},
        {"word": "going", "start": 0.6, "end": 0.9},
        {"word": "bit", "start": 0.9, "end": 4.86},
        {"word": "on", "start": 4.86, "end": 5.16},
    ]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    ops.init(root)
    imported = ops.import_media(root, audio, clip_id="vo")
    ops.attach_transcript(root, imported["clip_id"], transcript)
    ops.seed_timeline(root, "vo", remove_silences=False)
    return root


def test_cut_at_confirm_suspect_round_trips_over_http(tmp_path: Path) -> None:
    root = _suspect_duration_project(tmp_path)
    httpd = webui.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        server = f"http://127.0.0.1:{httpd.server_address[1]}"

        # A span overlapping the flagged word is refused without confirmation.
        status, payload = _post(f"{server}/api/cut-at", {"spans": [[2.0, 2.5]]})
        assert status == 400
        assert "hides a retake" in payload["error"]

        # Under plan=True the boundary is reported instead of refused.
        status, planned = _post(
            f"{server}/api/cut-at", {"spans": [[2.0, 2.5]], "plan": True}
        )
        assert status == 200
        flagged = planned["suspect_boundaries"]
        assert [hit["index"] for hit in flagged] == [3]
        assert flagged[0]["text"] == "bit"

        # confirm_suspect=True is what actually lets it through.
        status, confirmed = _post(
            f"{server}/api/cut-at",
            {"spans": [[2.0, 2.5]], "confirm_suspect": True},
        )
        assert status == 200
        assert confirmed["removed"] > 0

        _, view = _json(f"{server}/api/view")
        assert view["undo_depth"] == SEEDED_DEPTH + 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


def test_cue_add_lands_in_the_manifest(project: Path, server: str) -> None:
    """`/api/cue` is the timeline drag gesture's landing point — a fourth
    caller into `ops.cue_add`, same as the CLI and MCP tool."""
    status, payload = _post(f"{server}/api/cue", {"clip_id": "vo", "word_index": 3, "asset": "card:title"})
    assert status == 200
    assert payload["clip_id"] == "vo"
    assert payload["asset"] == "card:title"

    cues = Project.open(project).read_manifest()["cues"]
    assert cues == [{"clip_id": "vo", "word_index": 3, "asset": "card:title"}]


def test_cue_add_refuses_a_duplicate_word(server: str) -> None:
    _post(f"{server}/api/cue", {"clip_id": "vo", "word_index": 3, "asset": "card:title"})
    status, payload = _post(f"{server}/api/cue", {"clip_id": "vo", "word_index": 3, "asset": "card:other"})
    assert status != 200
    assert "already has a cue" in payload["error"]


def test_cue_add_requires_asset(server: str) -> None:
    status, payload = _post(f"{server}/api/cue", {"clip_id": "vo", "word_index": 3})
    assert status != 200
    assert "asset" in payload["error"]


def _music_asset(project: Path, tmp_path: Path, clip_id: str = "bed") -> str:
    """A second registered clip for the A2 bed to play — music is a clip_id,
    never a card (a held frame has no sound to mix)."""
    audio = tmp_path / f"{clip_id}.wav"
    _make_wav(audio, duration=6.0)
    ops.import_media(project, audio, clip_id=clip_id)
    return clip_id


def test_music_sets_the_bed_and_the_lane_can_see_it(
    project: Path, server: str, tmp_path: Path
) -> None:
    """`/api/music` is the A2 lane's own mutation — a fourth caller into
    `ops.music`, and the reason the lane is something the window can set
    rather than only draw."""
    asset = _music_asset(project, tmp_path)
    status, payload = _post(
        f"{server}/api/music",
        {"asset": asset, "clip_id": "vo", "word_index_start": 2, "word_index_end": 5},
    )
    assert status == 200
    assert payload["written"] is True
    assert payload["music"]["asset"] == asset
    # The word echo the CLAUDE.md rule requires of anything taking an index.
    assert payload["start_word"]["text"] == "w2"
    assert payload["end_word"]["text"] == "w5"

    assert Project.open(project).read_manifest()["music"] == {
        "asset": asset,
        "clip_id": "vo",
        "word_index_start": 2,
        "word_index_end": 5,
        "fade_in": 0.0,
        "fade_out": 0.0,
    }

    # And the projection the A2 lane draws from now has a bed in it.
    _, view = _json(f"{server}/api/view")
    assert view["music"]["asset"] == asset
    assert view["music"]["to_end"] is False
    assert "music_error" not in view


def test_music_updates_one_field_without_rewriting_the_rest(
    project: Path, server: str, tmp_path: Path
) -> None:
    """Every field is a partial update — a panel changing only the fades
    sends only the fades, and the absent keys keep what they had."""
    asset = _music_asset(project, tmp_path)
    _post(
        f"{server}/api/music",
        {"asset": asset, "clip_id": "vo", "word_index_start": 2, "word_index_end": 5},
    )
    status, payload = _post(f"{server}/api/music", {"fade_in": 0.5, "fade_out": 1.0})
    assert status == 200
    assert payload["music"] == {
        "asset": asset,
        "clip_id": "vo",
        "word_index_start": 2,
        "word_index_end": 5,
        "fade_in": 0.5,
        "fade_out": 1.0,
    }


def test_music_clear_end_runs_the_bed_to_the_end_of_the_timeline(
    project: Path, server: str, tmp_path: Path
) -> None:
    asset = _music_asset(project, tmp_path)
    _post(
        f"{server}/api/music",
        {"asset": asset, "clip_id": "vo", "word_index_start": 2, "word_index_end": 5},
    )
    status, payload = _post(f"{server}/api/music", {"clear_end": True})
    assert status == 200
    assert payload["music"]["word_index_end"] is None

    _, view = _json(f"{server}/api/view")
    assert view["music"]["to_end"] is True


def test_music_reset_drops_the_bed_and_the_lane_with_it(
    project: Path, server: str, tmp_path: Path
) -> None:
    asset = _music_asset(project, tmp_path)
    _post(f"{server}/api/music", {"asset": asset, "clip_id": "vo", "word_index_start": 2})
    status, payload = _post(f"{server}/api/music", {"reset": True})
    assert status == 200
    assert payload["music"] is None
    assert "music" not in Project.open(project).read_manifest()

    _, view = _json(f"{server}/api/view")
    assert view["music"] is None  # no bed, no lane


def test_music_refuses_a_card_as_the_asset(server: str) -> None:
    """The op owns its own validation and its refusal is this route's 400 —
    a held frame has no sound to mix."""
    status, payload = _post(
        f"{server}/api/music",
        {"asset": "card:title", "clip_id": "vo", "word_index_start": 2},
    )
    assert status != 200
    assert "clip_id" in payload["error"]


def test_music_refuses_a_first_set_missing_its_start_word(
    project: Path, server: str, tmp_path: Path
) -> None:
    asset = _music_asset(project, tmp_path)
    status, payload = _post(f"{server}/api/music", {"asset": asset, "clip_id": "vo"})
    assert status != 200
    assert "word_index_start" in payload["error"]


def test_music_refuses_a_non_integer_word_index(server: str, project: Path, tmp_path: Path) -> None:
    asset = _music_asset(project, tmp_path)
    status, payload = _post(
        f"{server}/api/music",
        {"asset": asset, "clip_id": "vo", "word_index_start": "two"},
    )
    assert status != 200
    assert "word_index_start" in payload["error"]


def test_music_read_only_call_changes_nothing(project: Path, server: str, tmp_path: Path) -> None:
    """No fields is a read, `tail`'s shape — the panel opens on the bed in
    force without writing to find out what it is."""
    asset = _music_asset(project, tmp_path)
    _post(f"{server}/api/music", {"asset": asset, "clip_id": "vo", "word_index_start": 2})
    status, payload = _post(f"{server}/api/music", {})
    assert status == 200
    assert payload["written"] is False
    assert payload["music"]["asset"] == asset


def test_music_requires_json_content_type(server: str, project: Path, tmp_path: Path) -> None:
    """The mutation guard every route here carries: an HTML form cannot send
    `application/json`, so a cross-origin attempt becomes a preflight."""
    asset = _music_asset(project, tmp_path)
    status, _ = _post(
        f"{server}/api/music",
        {"asset": asset, "clip_id": "vo", "word_index_start": 2},
        content_type="text/plain",
    )
    assert status != 200
    assert Project.open(project).read_manifest().get("music") is None


def test_view_of_a_clip_without_a_transcript_still_draws_the_edit(
    project: Path, server: str, tmp_path: Path
) -> None:
    other = tmp_path / "b.wav"
    _make_wav(other, duration=3.0)
    ops.import_media(project, other, clip_id="b")

    _, payload = _json(f"{server}/api/view?clip_id=b")
    assert payload["words"] is None
    assert payload["transcript_missing"] is True
    assert payload["segments"]  # the timeline is still there to look at


def test_view_flags_a_clip_registered_but_not_on_the_timeline(
    project: Path, server: str, tmp_path: Path
) -> None:
    """`off_timeline` is the only thing that separates "never on the track"
    from "you cut all of it", and a front end may not re-derive it.

    Registering a clip does not put it in the edit — footage a cue points at
    never is. Asked for one, `timeline_view` reports rather than refuses, and
    hands back the *timeline's* segments under the clip_id it was asked for;
    without the flag the payload is indistinguishable from a clip whose every
    word was removed."""
    other = tmp_path / "b.wav"
    _make_wav(other, duration=3.0)
    ops.import_media(project, other, clip_id="b")

    _, off = _json(f"{server}/api/view?clip_id=b")
    assert off["clip_id"] == "b"
    assert off["off_timeline"] is True
    # The echoed clip_id is not the segments' clip_id — the exact trap the flag
    # exists to make legible rather than derivable.
    assert {s["clip_id"] for s in off["segments"]} == {"vo"}

    # And it is absent, not False, for the clip the edit actually contains —
    # same absent-means-the-ordinary-case shape as `transcript_missing`.
    _, on = _json(f"{server}/api/view?clip_id=vo")
    assert "off_timeline" not in on


def test_an_off_timeline_clip_with_a_transcript_reads_as_entirely_cut(
    project: Path, server: str, tmp_path: Path
) -> None:
    """The half that actually misleads: give the off-timeline clip a
    transcript and every word comes back `present: false`, which is precisely
    what a clip somebody cut in its entirety looks like. The words alone
    cannot tell the two apart — the flag beside them is the whole answer."""
    other = tmp_path / "b.wav"
    _make_wav(other, duration=3.0)
    ops.import_media(project, other, clip_id="b")
    words = [{"word": f"b{n}", "start": float(n) * 0.5, "end": n * 0.5 + 0.4} for n in range(4)]
    transcript = tmp_path / "b.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    ops.attach_transcript(project, "b", transcript)

    _, payload = _json(f"{server}/api/view?clip_id=b")
    assert payload["words"], "the clip has a transcript, so words are drawn"
    assert all(w["present"] is False for w in payload["words"])
    assert "transcript_missing" not in payload
    assert payload["off_timeline"] is True


def test_the_picture_lane_reaches_the_page_over_http(project: Path, server: str) -> None:
    """Step 6: what the V2 lane is drawn from has to survive the trip, and it
    is a planned shot list rather than a raw projection — `src_in` is the
    field only `mlt.plan_picture` can produce, and the lane's tooltip is the
    one place a re-used clip's cursor is visible."""
    _, before = _json(f"{server}/api/view")
    assert before["shots"] is None  # no cues, so no picture lane exists to draw

    # Cues are added by the CLI, MCP or the agent panel — the page draws the
    # lane, it does not author it — so this arrives from outside the server,
    # the same way the untranscribed clip above does.
    Project.open(project).cards_dir.joinpath("outro.png").write_bytes(b"\x89PNG")
    ops.cue_add(project, "vo", 2, "card:outro")

    _, payload = _json(f"{server}/api/view")
    assert payload["layered"] is True
    assert payload["shots_rate"] == 30.0
    assert len(payload["shots"]) == 1
    shot = payload["shots"][0]
    assert shot["asset"] == "card:outro"
    assert shot["is_image"] is True
    assert shot["src_in"] == 0
    # The first shot covers from the open, whatever word its cue names.
    assert shot["start"] == pytest.approx(0.0)
    assert shot["word_index"] == 2


def _make_broll(root: Path, *, duration: float) -> Path:
    """A real encoded clip for the picture lane to point at. Real, because
    `_resolve_asset` reads the registered duration off a probe and the pin is
    checked against it."""
    broll = root / "broll.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=30:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(broll),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return broll


def test_a_pinned_cue_reaches_the_lane_with_the_moment_it_names(
    project: Path, server: str
) -> None:
    """The b-roll placement, drawn. `src_pin` is what the cue asked for and
    `src_start` is where the planner says the shot reads — the page needs both,
    because the preview layer seeks to `src_start` and a viewer who cannot see
    that the two agree has no way to tell a placement from a re-use.
    """
    broll = _make_broll(project.parent, duration=8.0)
    ops.import_media(project, broll, clip_id="broll")
    Project.open(project).cards_dir.joinpath("outro.png").write_bytes(b"\x89PNG")
    ops.cue_add(project, "vo", 0, "broll", src_start=2.0)
    ops.cue_add(project, "vo", 4, "card:outro")

    status, payload = _json(f"{server}/api/view")

    assert status == 200
    shots = payload["shots"]
    assert shots[0]["asset"] == "broll"
    assert shots[0]["src_pin"] == 2.0
    assert shots[0]["src_start"] == pytest.approx(2.0)
    assert shots[0]["src_in"] == 60  # 2.0s on the 30fps export grid
    # A card takes no pin, and says so rather than reporting a number.
    assert shots[1]["src_pin"] is None


def test_the_frame_reaches_the_page_with_the_render_s_own_placement(
    project: Path, server: str
) -> None:
    """Step 4 of the aspect swap, over the wire. The page draws #frame at
    `canvas` and places media at `reframe[clip].dest`, so both have to survive
    the trip — and `dest` has to be the *whole source frame's* landing rect,
    which is wider than the canvas and starts left of it whenever the render
    crops. A page given only the crop would have to re-derive that itself.
    """
    broll = _make_broll(project.parent, duration=8.0)  # 160x120, so 4:3
    ops.import_media(project, broll, clip_id="broll")
    ops.canvas(project, size="1080x1920")

    status, payload = _json(f"{server}/api/view")

    assert status == 200
    assert payload["canvas"] == [1080, 1920]
    entry = payload["reframe"]["broll"]
    assert entry["source"] == [160, 120]
    assert entry["crops"] is True
    # 9:16 out of 4:3 keeps a 68-wide column of the 160 — centred, and in
    # source pixels, because a rect no edit can invalidate is the whole rule.
    assert entry["crop"] == [46, 0, 68, 120]
    x, y, w, h = entry["dest"]
    assert x < 0 and w > 1080, "the whole frame lands wider than the canvas"
    assert (y, h) == (0, 1920), "filled by height, which is the axis that fits"
    # The audio-only clip has no picture to place, and gets no entry at all.
    assert "vo" not in payload["reframe"]


def test_an_unswapped_project_still_reports_a_frame_to_draw(server: str) -> None:
    """The page has one code path, so the canvas is always answered — here it
    is the audio-only default rather than an absence the front end has to
    guess a shape for."""
    _, payload = _json(f"{server}/api/view")

    assert payload["canvas"] == [1920, 1080]
    assert payload["reframe"] == {}
    assert "reframe_error" not in payload


def test_a_pin_that_runs_off_its_asset_is_drawn_as_a_refusal_not_a_rewind(
    project: Path, server: str
) -> None:
    """The safety property this step adds, over the wire. Unpinned, the same
    shot is drawn from the head of the clip without complaint — so the test
    does both, because the pair is the design: the rewind is right for a
    re-use and would be a lie for a placement.
    """
    broll = _make_broll(project.parent, duration=8.0)
    ops.import_media(project, broll, clip_id="broll")
    Project.open(project).cards_dir.joinpath("outro.png").write_bytes(b"\x89PNG")
    ops.cue_add(project, "vo", 0, "broll", src_start=7.0)  # 4s shot, 1s left in the clip
    ops.cue_add(project, "vo", 4, "card:outro")

    status, payload = _json(f"{server}/api/view")

    assert status == 200
    assert payload["shots"] is None
    assert "a pinned cue shows the moment it names" in payload["shots_error"]
    # Every other lane still answers — the window is how the cue gets fixed.
    assert payload["words"] is not None
    assert payload["segments"]

    ops.cue_rm(project, "vo", 0)
    ops.cue_add(project, "vo", 0, "broll")

    _, redrawn = _json(f"{server}/api/view")
    assert redrawn.get("shots_error") is None
    assert redrawn["shots"][0]["src_in"] == 0


def test_a_cue_the_edit_cuts_away_is_drawn_as_a_refusal_not_a_500(
    project: Path, server: str
) -> None:
    """The safety property, over the wire. A stale cue must not take the view
    down — the window is how a person finds the cue to move, so the picture
    lane comes back empty with the reason attached and every other lane still
    answers."""
    Project.open(project).cards_dir.joinpath("outro.png").write_bytes(b"\x89PNG")
    ops.cue_add(project, "vo", 5, "card:outro")
    _post(f"{server}/api/cut", {"clip_id": "vo", "ranges": [[5, 5]], "mode": "cut"})

    status, payload = _json(f"{server}/api/view")

    assert status == 200
    assert payload["shots"] is None
    assert "was cut from the edit" in payload["shots_error"]
    assert payload["words"] is not None
    assert payload["seams"]


# -- plan, apply, undo ----------------------------------------------------


def test_plan_reports_the_real_numbers_and_writes_nothing(server: str) -> None:
    _, before = _json(f"{server}/api/view")

    status, plan = _post(
        f"{server}/api/cut",
        {"clip_id": "vo", "ranges": [[3, 4]], "mode": "cut", "plan": True},
    )
    assert status == 200
    assert plan["plan"] is True
    assert plan["removed"] == pytest.approx(1.9)
    # The echo is what makes an off-by-one visible, so it has to survive the
    # trip through HTTP rather than be a CLI-only nicety.
    assert plan["applied"][0]["text"] == "w3 w4"
    assert [w["index"] for w in plan["applied"][0]["context_before"]] == [0, 1, 2]

    _, after = _json(f"{server}/api/view")
    assert after["timeline_duration"] == pytest.approx(before["timeline_duration"])
    assert after["undo_depth"] == before["undo_depth"]


def test_apply_then_undo_returns_the_timeline_and_the_words(server: str) -> None:
    _, before = _json(f"{server}/api/view")

    _post(f"{server}/api/cut", {"clip_id": "vo", "ranges": [[3, 4]], "mode": "cut"})
    _, cut = _json(f"{server}/api/view")
    assert cut["undo_depth"] == SEEDED_DEPTH + 1
    assert cut["timeline_duration"] < before["timeline_duration"]

    status, undone = _post(f"{server}/api/undo", {})
    assert status == 200
    assert undone["undo_depth"] == SEEDED_DEPTH

    _, restored = _json(f"{server}/api/view")
    assert restored["timeline_duration"] == pytest.approx(before["timeline_duration"])
    assert all(word["present"] for word in restored["words"])


def test_keep_only_goes_through_the_same_endpoint(server: str) -> None:
    status, payload = _post(
        f"{server}/api/cut", {"clip_id": "vo", "ranges": [[2, 3]], "mode": "keep"}
    )
    assert status == 200
    assert payload["mode"] == "keep"

    _, view = _json(f"{server}/api/view")
    kept = [w["index"] for w in view["words"] if w["present"]]
    assert kept == [2, 3]


def test_a_refused_cut_comes_back_as_a_message_not_a_traceback(server: str) -> None:
    status, payload = _post(
        f"{server}/api/cut", {"clip_id": "vo", "ranges": [[99, 99]], "mode": "cut"}
    )
    assert status == 400
    assert "error" in payload
    assert "99" in payload["error"]


def test_bad_request_shapes_are_refused_before_reaching_ops(server: str) -> None:
    for body in (
        {"clip_id": "vo", "mode": "cut"},
        {"clip_id": "vo", "ranges": [], "mode": "cut"},
        {"clip_id": "vo", "ranges": [[1]], "mode": "cut"},
        {"ranges": [[1, 2]], "mode": "cut"},
        {"clip_id": "vo", "ranges": [[1, 2]], "mode": "sideways"},
    ):
        status, payload = _post(f"{server}/api/cut", body)
        assert status == 400, body
        assert "error" in payload


def test_restore_undoes_a_cut_over_http(server: str) -> None:
    _, before = _json(f"{server}/api/view")

    status, cut = _post(f"{server}/api/cut", {"clip_id": "vo", "ranges": [[3, 4]], "mode": "cut"})
    assert status == 200
    _, cut_view = _json(f"{server}/api/view")
    assert cut_view["timeline_duration"] < before["timeline_duration"]
    words = {w["index"]: w for w in cut_view["words"]}
    assert words[3]["present"] is False
    assert words[4]["present"] is False

    status, restored = _post(
        f"{server}/api/restore", {"clip_id": "vo", "ranges": [[3, 4]]}
    )
    assert status == 200
    assert restored["applied"][0]["already_present"] is False
    assert restored["restored"] == pytest.approx(cut["removed"], abs=1e-6)

    _, view = _json(f"{server}/api/view")
    assert view["timeline_duration"] == pytest.approx(before["timeline_duration"])
    words = {w["index"]: w for w in view["words"]}
    assert words[3]["present"] is True
    assert words[4]["present"] is True


def test_restore_plan_over_http_does_not_touch_the_timeline(server: str) -> None:
    _post(f"{server}/api/cut", {"clip_id": "vo", "ranges": [[3, 4]], "mode": "cut"})
    _, before_plan = _json(f"{server}/api/view")

    status, planned = _post(
        f"{server}/api/restore", {"clip_id": "vo", "ranges": [[3, 4]], "plan": True}
    )
    assert status == 200
    assert planned["plan"] is True

    _, after_plan = _json(f"{server}/api/view")
    assert after_plan["timeline_duration"] == pytest.approx(before_plan["timeline_duration"])
    assert after_plan["undo_depth"] == before_plan["undo_depth"]


def test_restore_of_a_clip_with_no_material_left_comes_back_as_a_message_not_a_traceback(
    server: str,
) -> None:
    # Cut-at the whole timeline away: no segment of 'vo' survives anywhere.
    _, before = _json(f"{server}/api/view")
    status, _ = _post(f"{server}/api/cut-at", {"spans": [[0.0, before["timeline_duration"]]]})
    assert status == 200
    _, emptied = _json(f"{server}/api/view")
    assert emptied["timeline_duration"] == pytest.approx(0.0)

    status, payload = _post(f"{server}/api/restore", {"clip_id": "vo", "ranges": [[0, 1]]})
    assert status == 400
    assert "error" in payload
    assert "vo" in payload["error"]


def test_api_cut_through_pause_removes_the_trailing_pause(tmp_path: Path) -> None:
    """The shared `project` fixture's words are all 0.1s apart — too tight
    for a marker (CLAUDE.md / docs/plans/DAYDREAM.md's 0.4s threshold) — so this builds
    its own project with one wide gap: words 3 and 4 sit 2.1s apart instead.

    A baseline cut of words 2-3 leaves that 2.1s of dead air playing before
    word 4; `through_pause=True` removes it too, which shows up as word 4
    playing immediately after word 1 instead of 2.1s later.
    """
    root = tmp_path / "proj"
    audio = tmp_path / "vo.wav"
    _make_wav(audio, duration=14.0)

    words = [
        {"word": f"w{n}", "start": n + (2.0 if n >= 4 else 0.0), "end": n + (2.0 if n >= 4 else 0.0) + 0.9}
        for n in range(8)
    ]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    ops.init(root)
    imported = ops.import_media(root, audio, clip_id="vo")
    ops.attach_transcript(root, imported["clip_id"], transcript)
    ops.seed_timeline(root, "vo", remove_silences=False)

    httpd = webui.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        server = f"http://127.0.0.1:{httpd.server_address[1]}"

        # Baseline: word 3's own end (3.9s) is where the removed range stops.
        status, baseline = _post(
            f"{server}/api/cut",
            {"clip_id": "vo", "ranges": [[2, 3]], "mode": "cut", "plan": True},
        )
        assert status == 200
        assert baseline["applied"][0]["source_end"] == pytest.approx(3.9)

        # With the flag: the range's trailing edge reaches word 4's own start
        # (6.0s) instead — the pause between them is swallowed by the cut.
        status, flagged = _post(
            f"{server}/api/cut",
            {
                "clip_id": "vo",
                "ranges": [[2, 3]],
                "mode": "cut",
                "through_pause": True,
                "plan": True,
            },
        )
        assert status == 200
        assert flagged["applied"][0]["source_end"] == pytest.approx(6.0)
        assert flagged["applied"][0]["word_end"] == pytest.approx(3.9)

        # Apply it for real and confirm the effect on where word 4 now plays:
        # immediately after word 1's segment (timeline second 2.0), not 2.1s
        # later the way the un-flagged baseline would have left it.
        status, _ = _post(
            f"{server}/api/cut",
            {"clip_id": "vo", "ranges": [[2, 3]], "mode": "cut", "through_pause": True},
        )
        assert status == 200

        _, view = _json(f"{server}/api/view")
        words_by_index = {w["index"]: w for w in view["words"]}
        assert words_by_index[4]["present"] is True
        assert words_by_index[4]["timeline_start"] == pytest.approx(2.0)
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


# -- media, and the Range support a browser needs to seek -----------------


def test_media_serves_the_whole_file_and_advertises_ranges(project: Path, server: str) -> None:
    status, headers, body = _get(f"{server}/api/media/vo")
    assert status == 200
    assert headers["Accept-Ranges"] == "bytes"
    assert len(body) == (project / "media" / "vo.wav").stat().st_size


def test_a_range_request_returns_206_and_exactly_those_bytes(project: Path, server: str) -> None:
    whole = (project / "media" / "vo.wav").read_bytes()

    status, headers, body = _get(f"{server}/api/media/vo", headers={"Range": "bytes=100-199"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes 100-199/{len(whole)}"
    assert body == whole[100:200]


def test_an_open_ended_range_runs_to_the_end_of_the_file(project: Path, server: str) -> None:
    whole = (project / "media" / "vo.wav").read_bytes()
    size = len(whole)

    status, headers, body = _get(f"{server}/api/media/vo", headers={"Range": f"bytes={size - 50}-"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes {size - 50}-{size - 1}/{size}"
    assert body == whole[-50:]


def test_a_suffix_range_returns_the_tail(project: Path, server: str) -> None:
    whole = (project / "media" / "vo.wav").read_bytes()
    status, _, body = _get(f"{server}/api/media/vo", headers={"Range": "bytes=-50"})
    assert status == 206
    assert body == whole[-50:]


def test_an_unsatisfiable_range_is_refused_rather_than_served_wrong(server: str) -> None:
    status, payload = _json(f"{server}/api/media/vo", headers={"Range": "bytes=999999999-"})
    assert status == 400
    assert "error" in payload


@pytest.mark.parametrize("abort", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_a_client_abandoning_a_stream_ends_it_quietly(tmp_path: Path, abort: type[OSError]) -> None:
    """A seek drops the in-flight range, and each OS names that differently:
    Linux says broken pipe or reset, Windows `ConnectionAbortedError`
    (WinError 10053), which escaped and printed a traceback per seek on a
    real Windows PC. No socket here reproduces the Windows name, so this
    calls `_stream_file` directly with a writer that raises it — the
    argument is held, not the behaviour; the laptop's run is the check."""
    source = tmp_path / "clip.bin"
    source.write_bytes(b"x" * 1024)

    class Writer:
        def write(self, chunk: bytes) -> int:
            raise abort()

    class Handler:
        headers = http.client.HTTPMessage()
        wfile = Writer()

        def send_response(self, status: object) -> None: ...
        def send_header(self, name: str, value: str) -> None: ...
        def end_headers(self) -> None: ...

    webui._stream_file(Handler(), source, head_only=False)  # type: ignore[arg-type]


def test_head_on_media_answers_without_a_body(server: str) -> None:
    status, headers, body = _get(f"{server}/api/media/vo", method="HEAD")
    assert status == 200
    assert int(headers["Content-Length"]) > 0
    assert body == b""


def test_an_unknown_clip_is_a_message_not_a_crash(server: str) -> None:
    status, payload = _json(f"{server}/api/media/nope")
    assert status == 400
    assert "nope" in payload["error"]


# -- waveform ---------------------------------------------------------------


def test_waveform_matches_the_ops_contract(project: Path, server: str) -> None:
    status, payload = _json(f"{server}/api/waveform/vo")
    assert status == 200
    assert payload["clip_id"] == "vo"
    assert payload["frame_ms"] == 20
    assert isinstance(payload["rms"], list)
    assert payload["rms"]
    assert all(isinstance(v, int) and 0 <= v <= 255 for v in payload["rms"])
    assert payload["duration_s"] == pytest.approx(12.0, abs=0.1)


def test_waveform_on_an_unknown_clip_is_a_message_not_a_crash(server: str) -> None:
    status, payload = _json(f"{server}/api/waveform/nope")
    assert status == 400
    assert "nope" in payload["error"]


# -- assets, properties, roles ----------------------------------------------
#
# The three backend items behind the assets pane and properties inspector
# (docs/plans/DAYDREAM.md § Import roles + assets pane, § Properties pane): a catalogue
# of everything a cue can point at, a composed detail view, and the role
# toggle each clip carries in the catalogue.


def test_assets_lists_the_seeded_clip(server: str) -> None:
    status, payload = _json(f"{server}/api/assets")
    assert status == 200
    assert {c["clip_id"] for c in payload["clips"]} == {"vo"}
    vo = payload["clips"][0]
    assert vo["has_audio"] is True
    assert vo["role"] is None
    assert vo["cues"] == 0


def test_clip_role_lands_in_the_manifest_and_reaches_assets(
    project: Path, server: str
) -> None:
    """`/api/clip-role` is a fourth caller into `ops.clip_role`, same as the
    CLI and MCP tool — the web UI never decides on its own (CLAUDE.md)."""
    status, payload = _post(f"{server}/api/clip-role", {"clip_id": "vo", "role": "voiceover"})
    assert status == 200
    assert payload["role"] == "voiceover"

    clips = Project.open(project).read_manifest()["clips"]
    assert next(c for c in clips if c["clip_id"] == "vo")["role"] == "voiceover"

    _, assets = _json(f"{server}/api/assets")
    assert assets["clips"][0]["role"] == "voiceover"


def test_clip_role_reset_clears_it(server: str) -> None:
    _post(f"{server}/api/clip-role", {"clip_id": "vo", "role": "footage"})
    status, payload = _post(f"{server}/api/clip-role", {"clip_id": "vo", "reset": True})
    assert status == 200
    assert payload["role"] is None


def test_clip_role_requires_clip_id(server: str) -> None:
    status, payload = _post(f"{server}/api/clip-role", {"role": "footage"})
    assert status == 400
    assert "clip_id" in payload["error"]


def test_properties_with_no_query_is_project_state(server: str) -> None:
    status, payload = _json(f"{server}/api/properties")
    assert status == 200
    assert set(payload) == {"status", "canvas", "caption_style"}


def test_properties_narrows_to_a_clip_and_word(server: str) -> None:
    status, payload = _json(f"{server}/api/properties?clip_id=vo&word_index=3")
    assert status == 200
    assert payload["clip"]["clip_id"] == "vo"
    assert payload["cue"] is None
    assert "context" in payload


def test_properties_word_index_without_clip_id_is_refused(server: str) -> None:
    status, payload = _json(f"{server}/api/properties?word_index=3")
    assert status == 400
    assert "clip_id" in payload["error"]


# -- finish mode: GET /api/finish --------------------------------------------
#
# docs/plans/STUDIO.md § Step 01 — `ops.finish_report` composed entirely from existing
# ops. This is the same discipline `test_properties_*` above applies to
# `/api/properties`: prove the route is actually reachable over a real
# socket and answers with the documented top-level shape, not that
# `ops.finish_report` itself is correct (that's `tests/test_ops_finish.py`).


def test_api_finish_reports_over_a_real_socket(server: str) -> None:
    status, payload = _json(f"{server}/api/finish")
    assert status == 200
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
    # Unasked-for, so `None` — the truth strip re-reads this route on every
    # `project-changed`, and the framing section decodes placed footage for a
    # scene-cut scan (5.7s wall / 46s CPU on the real film, uncached). `None`
    # means "not measured" and is deliberately distinct from a measured zero.
    assert payload["framing"] is None
    assert [f for f in payload["flags"]["items"] if f["kind"] == "framing"] == []
    assert set(payload["duration"]) == {"edit_seconds", "tail_seconds", "total_seconds"}
    assert set(payload["canvas"]) == {"canvas", "presets"}
    # The refusing preset card's short line and command ride the route too.
    for preset in payload["canvas"]["presets"].values():
        assert set(preset) == {"ok", "message", "needs", "fix"}
    assert set(payload["captions"]) == {"configured", "font", "burned"}
    assert payload["captions"]["configured"] is False
    assert payload["captions"]["burned"] == "unknown"  # no render log yet
    assert set(payload["picture"]) == {"cue_count", "pinned_count", "shots_error"}
    assert set(payload["marks"]) == {"applied", "stale"}
    assert set(payload["seams"]) == {"count"}
    assert set(payload["flags"]) == {"count", "items"}
    assert payload["flags"]["count"] == len(payload["flags"]["items"])


# -- filmstrip thumbnails -----------------------------------------------------
#
# docs/plans/DAYDREAM.md's filmstrip lane: a cached frame per source instant, addressed
# the way the timeline lane will address it — a clip and a source second.


@pytest.fixture
def video_clip(project: Path) -> str:
    """A second, video clip in the same project the `server` fixture opened."""
    path = project.parent / "vid.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    ops.import_media(project, path, clip_id="vid")
    return "vid"


def test_thumb_serves_a_real_jpeg(video_clip: str, server: str) -> None:
    status, headers, body = _get(f"{server}/api/thumb/{video_clip}?at=2.3")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert body[:3] == b"\xff\xd8\xff"


def test_thumb_requires_at(video_clip: str, server: str) -> None:
    status, payload = _json(f"{server}/api/thumb/{video_clip}")
    assert status == 400
    assert "at" in payload["error"]


def test_thumb_on_an_unknown_clip_is_a_message_not_a_crash(server: str) -> None:
    status, payload = _json(f"{server}/api/thumb/nope?at=1.0")
    assert status == 400
    assert "nope" in payload["error"]


def test_thumb_honours_an_interval(video_clip: str, server: str) -> None:
    """Two different grids for the same instant land in different cache
    buckets (`ops.thumbnail`'s own snapping), so both must independently
    succeed rather than one masking the other as a cache hit."""
    coarse_status, coarse_headers, _ = _get(
        f"{server}/api/thumb/{video_clip}?at=2.3&interval=2.0"
    )
    fine_status, fine_headers, _ = _get(f"{server}/api/thumb/{video_clip}?at=2.3&interval=0.5")
    assert coarse_status == fine_status == 200
    assert coarse_headers["Content-Type"] == fine_headers["Content-Type"] == "image/jpeg"


def test_thumb_rejects_an_explicit_zero_interval(video_clip: str, server: str) -> None:
    """`interval=0` used to fall through `... or ops.THUMB_INTERVAL` (0.0 is
    falsy) straight to the 1.0s default, silently skipping `ops.thumbnail`'s
    own refusal that every negative interval already hit."""
    status, payload = _json(f"{server}/api/thumb/{video_clip}?at=2.3&interval=0")
    assert status == 400
    assert "interval must be positive" in payload["error"]


# -- captions ---------------------------------------------------------------
#
# The second read model. It is a separate endpoint from /api/view because it is
# a different derivation of the same edit — placed, grouped and styled — and
# because it moves when the *manifest* moves rather than when the timeline
# does, which is also why `_revision` watches both.


def test_captions_reach_the_page_with_the_style_in_force(server: str) -> None:
    status, payload = _json(f"{server}/api/captions")

    assert status == 200
    assert payload["cues"], "eight words should produce at least one cue"
    assert payload["style"]["resolved"]["preset"] == "clean"
    assert payload["resolution"] == [1920, 1080]
    first = payload["cues"][0]
    assert first["start"] == 0.0
    assert {"start", "end", "text", "words"} <= set(first)


def test_a_caption_word_carries_the_span_its_highlight_runs_over(server: str) -> None:
    """The overlay must not light a word at its own start — a `\\k` duration
    covers the gap before it (captions.Cue.karaoke_spans). The server sends the
    spans so the browser cannot get this rule differently from the burn-in."""
    _, payload = _json(f"{server}/api/captions")
    words = payload["cues"][0]["words"]

    assert words[1]["start"] == 1.0
    assert words[1]["highlight_start"] == pytest.approx(0.9), "when w0 stopped"
    assert words[0]["highlight_start"] == 0.0


def test_the_style_a_restyle_stores_comes_back_on_the_next_fetch(
    project: Path, server: str
) -> None:
    ops.caption_style(project, preset="karaoke", size=80, text="yellow")

    _, payload = _json(f"{server}/api/captions")
    look = payload["style"]["resolved"]

    assert look["size"] == 80
    assert look["karaoke"] is True
    assert look["text"] == "#ffd400ff", "CSS, because the overlay is what draws it"
    assert look["highlight"] != look["text"], "or the word-highlight shows nothing"


def test_captions_follow_a_cut(server: str) -> None:
    """Timeline seconds, not source seconds — the clock the viewer has."""
    _, before = _json(f"{server}/api/captions")
    status, _ = _post(f"{server}/api/cut", {"clip_id": "vo", "ranges": [[0, 0]], "mode": "cut"})
    assert status == 200
    _, after = _json(f"{server}/api/captions")

    assert after["words_cut"] == 1
    assert after["cues"][0]["text"] != before["cues"][0]["text"]
    # w1 was heard at 1.0 and is now heard at 0.1 — the 0.9s w0 occupied is
    # gone from the timeline, so everything after it moved by that much.
    assert after["cues"][0]["start"] == pytest.approx(0.1)


def test_undoing_a_manifest_only_mutation_moves_the_revision(
    project: Path, server: str
) -> None:
    """A cue undo puts back a manifest and never touches `project.otio`, so an
    open window has to hear about it the same way a restyle does. `_revision`
    watches the manifest's mtime and the snapshot count, and this is the claim
    that both halves of the restore reach it (docs/plans/POLISH.md § Step 03)."""
    ops.cue_add(project, clip_id="vo", word_index=3, asset="card:title")
    _, before = _json(f"{server}/api/view")

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        _, first = next(events)

        status, undone = _post(f"{server}/api/undo", {})
        assert status == 200
        assert undone["manifest_restored"] is True

        for event, data in events:
            if event == "project-changed" and data["revision"] != first["revision"]:
                break
        else:
            pytest.fail("no project-changed event followed the undo")
    finally:
        conn.close()

    _, after = _json(f"{server}/api/view")
    assert after["undo_depth"] == before["undo_depth"] - 1
    assert ops.cue_ls(project)["cues"] == []


def test_a_restyle_moves_the_revision_so_an_open_window_repaints(
    project: Path, server: str
) -> None:
    """The style lives in the manifest and nothing about it touches
    project.otio, so a revision watching the timeline alone would leave the
    preview overlay drawing the old look until an unrelated edit moved it."""
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        _, first = next(events)

        ops.caption_style(project, size=80)

        for event, data in events:
            if event == "project-changed" and data["revision"] != first["revision"]:
                break
        else:
            pytest.fail("no project-changed event followed the restyle")
    finally:
        conn.close()


# -- the picture layer's assets -------------------------------------------
#
# What the viewer loads to show the shot under the playhead. A card is not a
# clip and is not reachable through `/api/media/`, so these are the routes that
# make V2 a picture rather than a plan of one.


def test_an_asset_route_serves_a_card_the_media_route_cannot_reach(
    project: Path, server: str
) -> None:
    """The reason there are two routes at all: `card:outro` is an asset key, not
    a clip_id, and `/api/media/` resolves clip_ids only."""
    card = Project.open(project).cards_dir / "outro.png"
    card.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    status, headers, body = _get(f"{server}/api/asset/card:outro")

    assert status == 200
    assert body == card.read_bytes()
    assert headers["Accept-Ranges"] == "bytes"
    assert _json(f"{server}/api/media/card:outro")[0] == 400


def test_an_asset_route_also_serves_a_clip(project: Path, server: str) -> None:
    """A picture cue's asset is a clip_id as often as it is a card, so the one
    route answers for both — and answers with the same bytes `/api/media/` does,
    because both go through `media.media_path` rather than guessing at a path."""
    status, _, body = _get(f"{server}/api/asset/vo")

    assert status == 200
    assert body == (project / "media" / "vo.wav").read_bytes()


def test_an_asset_honours_range_the_way_media_does(project: Path, server: str) -> None:
    """The picture layer seeks on every shot change, so Range is load-bearing
    here rather than incidental — and it is the same `_stream_file` either way."""
    whole = (project / "media" / "vo.wav").read_bytes()

    status, headers, body = _get(f"{server}/api/asset/vo", headers={"Range": "bytes=64-127"})

    assert status == 206
    assert headers["Content-Range"] == f"bytes 64-127/{len(whole)}"
    assert body == whole[64:128]


def test_a_graphic_frame_is_served_and_nothing_beside_it_is(project: Path, server: str) -> None:
    """`graphic:<name>/<phase>/<frame>` reaches one captured frame, and every
    part of the key is held to its shape before a path is built from it."""
    frame = Project.open(project).graphic_frames_dir / "hl" / "intro" / "f0007.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"\x89PNG\r\n\x1a\nframe")

    status, _, body = _get(f"{server}/api/asset/graphic:hl/intro/7?v=abc")
    assert status == 200 and body == frame.read_bytes()
    for bad in ("graphic:..%2Fhl/intro/7", "graphic:hl/..%2F..%2Fcards/7", "graphic:hl/intro/7%2F..", "graphic:hl/capture/0"):
        assert _get(f"{server}/api/asset/{bad}")[0] == 400, bad


@pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")
def test_an_image_pasted_into_the_prompt_is_added_and_named(project: Path, server: str, tmp_path: Path) -> None:
    """`POST /api/image` is `ops.image_add` behind the JSON guard: a taken name
    gets a number rather than a refusal, and a non-image is a 400."""
    png = tmp_path / "s.png"
    subprocess.run(["magick", "-size", "8x8", "xc:red", str(png)], check=True)
    data = base64.b64encode(png.read_bytes()).decode()
    first = _post(f"{server}/api/image", {"filename": "Sun Sticker.png", "data": data})
    second = _post(f"{server}/api/image", {"filename": "Sun Sticker.png", "data": data})
    assert first[0] == 200 and first[1]["asset"] == "image:sun-sticker"
    assert second[1]["asset"] == "image:sun-sticker-2"
    assert "pasted" in first[1]["image"]["source"]
    assert _post(f"{server}/api/image", {"filename": "notes.txt", "data": data})[0] == 400
    assert _post(f"{server}/api/image", {"filename": "a.png", "data": data}, content_type="text/plain")[0] == 400
    assert not list((Project.open(project).root / "cache" / "uploads").iterdir()), "the upload is not kept"


def test_a_card_name_cannot_climb_out_of_the_cards_directory(server: str) -> None:
    """The one place an asset key arrives from outside the project. A traversal
    is refused with a message rather than served, and the check lives in
    `ops.preview_source` so the route cannot have a second, weaker copy of it."""
    assert _get(f"{server}/api/asset/card:..%2F..%2F..%2Fetc%2Fpasswd")[0] == 400

    status, payload = _json(f"{server}/api/preview/card:..%2F..%2F..%2Fetc%2Fpasswd")
    assert status == 400
    assert "does not name a card" in payload["error"]


def test_an_unknown_asset_is_a_message_not_a_crash(server: str) -> None:
    status, payload = _json(f"{server}/api/asset/nope")
    assert status == 400
    assert "nope" in payload["error"]


def test_preview_names_the_kind_the_front_end_has_to_draw_with(
    project: Path, server: str
) -> None:
    """`kind` decides <img> versus <video>, and it is read off the resolved file
    rather than off the cue: `is_image` in a shot is a statement about the key."""
    Project.open(project).cards_dir.joinpath("outro.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    _, card = _json(f"{server}/api/preview/card:outro")
    assert card["kind"] == "image"
    assert card["playable"] is True
    assert Path(card["path"]).as_posix().endswith("assets/cards/outro.png")

    _, clip = _json(f"{server}/api/preview/vo")
    assert clip["kind"] == "audio"
    assert clip["playable"] is True
    assert clip["reason"] is None
    assert clip["audio_codec"] == "pcm_s16le"


def test_preview_is_what_turns_a_contentless_media_error_into_a_reason(
    project: Path, server: str
) -> None:
    """The point of the endpoint. A `<video>` that cannot decode its source fires
    one empty `error` event, so the front end asks here and gets words — and the
    file is still served on `/api/asset/`, because the browser is the authority
    and this list is a prediction."""
    # A .mkv registered as a clip: playable streams, container browsers refuse.
    mkv = project.parent / "clip.mkv"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=24:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mkv),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    ops.import_media(project, mkv, clip_id="broll")

    status, payload = _json(f"{server}/api/preview/broll")

    assert status == 200
    assert payload["playable"] is False
    assert ".mkv" in payload["reason"]
    assert _get(f"{server}/api/asset/broll")[0] == 200


# -- the guards -----------------------------------------------------------


def test_a_non_loopback_host_header_is_refused(server: str) -> None:
    # What a DNS-rebinding page looks like from here: the socket is loopback,
    # but the name the browser thinks it reached is not.
    status, payload = _json(f"{server}/api/view", headers={"Host": "evil.example.com"})
    assert status == 403
    assert "loopback" in payload["error"]


def test_a_mutating_request_must_be_json(server: str) -> None:
    # An HTML form can only send these three, which is exactly why requiring
    # application/json is what stops a cross-origin POST.
    for ctype in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data"):
        status, payload = _post(
            f"{server}/api/cut",
            {"clip_id": "vo", "ranges": [[0, 1]], "mode": "cut"},
            content_type=ctype,
        )
        assert status == 400, ctype
        assert "application/json" in payload["error"]

    _, view = _json(f"{server}/api/view")
    assert view["undo_depth"] == SEEDED_DEPTH  # nothing got through


def test_a_non_loopback_host_cannot_mutate_either(server: str) -> None:
    # A routed mutation from a good Host, for contrast with the refusal
    # below. It used to be chosen because there was nothing to undo; the
    # fixture's own import and seed are undoable now (docs/plans/POLISH.md § Step 03),
    # so what it demonstrates is a 200 rather than a 400 — the point of the
    # test is the 403 that follows.
    status, _ = _post(f"{server}/api/undo", {})
    assert status == 200

    request = urllib.request.Request(
        f"{server}/api/cut",
        data=b"{}",
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


# -- static assets --------------------------------------------------------


def test_the_page_and_its_assets_are_served_from_the_package(server: str) -> None:
    status, headers, body = _get(f"{server}/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b'src="/static/app.js"' in body
    # theme.js is loaded from <head> as a classic script, not a module: a
    # module is deferred and the page would paint in the wrong theme first.
    assert b'<script src="/static/theme.js"></script>' in body

    for asset, prefix in (
        ("app.js", "text/javascript"),
        ("theme.js", "text/javascript"),
        ("app.css", "text/css"),
    ):
        status, headers, body = _get(f"{server}/static/{asset}")
        assert status == 200, asset
        assert headers["Content-Type"].startswith(prefix)
        assert body


def test_the_vendored_fonts_are_served_and_the_css_asks_for_them(server: str) -> None:
    """The three type voices ship in the package, not from a CDN.

    A CDN would be refused by this server's own `default-src 'self'` CSP, so
    a missing woff2 does not fail loudly — the page just silently falls back
    to a system font and the look pass quietly undoes itself.
    """
    _, _, css = _get(f"{server}/static/app.css")
    wanted = {
        name.decode()
        for name in re.findall(rb'url\("/static/([^"]+\.woff2)"\)', css)
    }
    assert wanted, "app.css declares no vendored fonts"

    for name in sorted(wanted):
        status, headers, body = _get(f"{server}/static/{name}")
        assert status == 200, name
        assert headers["Content-Type"] == "font/woff2", name
        assert body[:4] == b"wOF2", name


def test_static_serving_refuses_a_path_rather_than_a_name(server: str) -> None:
    for name in ("..%2f..%2fpyproject.toml", "%2eenv", "nope.js", "index.html%00"):
        status, _ = _json(f"{server}/static/{name}")
        assert status == 404, name


def test_an_unknown_endpoint_is_a_404_with_a_message(server: str) -> None:
    status, payload = _json(f"{server}/api/nothing")
    assert status == 404
    assert "error" in payload


# -- /api/events, the SSE stream -------------------------------------------


def test_events_stream_is_text_event_stream_and_starts_with_the_revision(server: str) -> None:
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        assert resp.status == 200
        assert (resp.getheader("Content-Type") or "").startswith("text/event-stream")

        event, data = next(_sse_events(resp))
        assert event == "project-changed"
        assert "revision" in data
    finally:
        conn.close()


def test_events_stream_reports_project_changed_after_a_mutation(server: str) -> None:
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        _, first = next(events)
        first_revision = first["revision"]

        status, _ = _post(
            f"{server}/api/cut", {"clip_id": "vo", "ranges": [[3, 4]], "mode": "cut"}
        )
        assert status == 200

        for event, data in events:
            if event == "project-changed" and data["revision"] != first_revision:
                break
        else:
            pytest.fail("no project-changed event followed the mutation")
    finally:
        conn.close()


def test_events_endpoint_rejects_a_bad_host(server: str) -> None:
    status, payload = _json(f"{server}/api/events", headers={"Host": "evil.example.com"})
    assert status == 403
    assert "loopback" in payload["error"]


# -- the agent subprocess ---------------------------------------------------


def test_agent_endpoints_reject_a_bad_host(server: str) -> None:
    for path in ("/api/agent", "/api/agent/stop"):
        request = urllib.request.Request(
            f"{server}{path}",
            data=b"{}",
            headers={"Content-Type": "application/json", "Host": "evil.example.com"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code == 403, path


def test_agent_endpoints_require_json_content_type(server: str) -> None:
    for path in ("/api/agent", "/api/agent/stop"):
        status, payload = _post(f"{server}{path}", {"prompt": "hi"}, content_type="text/plain")
        assert status == 400, path
        assert "application/json" in payload["error"]

    # Missing entirely, not just wrong, is refused the same way.
    request = urllib.request.Request(
        f"{server}/api/agent", data=b"{}", method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 400


def test_agent_prompt_requires_a_prompt(server: str) -> None:
    status, payload = _post(f"{server}/api/agent", {})
    assert status == 400
    assert "prompt" in payload["error"]


def test_agent_prompt_rejects_a_non_string_model(server: str) -> None:
    status, payload = _post(f"{server}/api/agent", {"prompt": "hi", "model": 5})
    assert status == 400
    assert "model" in payload["error"]


def test_agent_prompt_rejects_an_empty_model(server: str) -> None:
    status, payload = _post(f"{server}/api/agent", {"prompt": "hi", "model": ""})
    assert status == 400
    assert "model" in payload["error"]


def test_agent_prompt_with_a_model_passes_it_through_to_the_spawned_argv(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`model` is a free string, never validated against a fixed list — the
    same `ops.py` reasoning as the canvas/framing overrides: `claude`'s own
    accepted model names change out from under any list proofcut would keep, so
    a wrong one is `claude`'s own refusal to report, not proofcut's to guess at.
    """
    argv_file = tmp_path / "argv.txt"
    stub = _write_agent_stub(tmp_path / "agent-stub", argv_file, {"type": "result", "subtype": "success"})
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    status, _ = _post(f"{server}/api/agent", {"prompt": "hi", "model": "claude-opus-5"})
    assert status == 202
    deadline = time.time() + 5
    while time.time() < deadline and not argv_file.exists():
        time.sleep(0.05)
    argv = argv_file.read_text(encoding="utf-8").splitlines()
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5"


def test_agent_prompt_without_a_model_omits_the_flag(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent means `claude`'s own default — the flag must not appear at
    all, not appear with an empty value, or every prompt before this field
    existed would render differently.
    """
    argv_file = tmp_path / "argv.txt"
    stub = _write_agent_stub(tmp_path / "agent-stub", argv_file, {"type": "result", "subtype": "success"})
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    status, _ = _post(f"{server}/api/agent", {"prompt": "hi"})
    assert status == 202
    deadline = time.time() + 5
    while time.time() < deadline and not argv_file.exists():
        time.sleep(0.05)
    argv = argv_file.read_text(encoding="utf-8").splitlines()
    assert "--model" not in argv


def test_agent_prompt_spawns_with_the_allowlist_and_streams_the_canned_event(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one end-to-end check: a stubbed `claude` records its own argv and
    echoes a canned `stream-json` line, which must come back out as an
    `agent` event on `/api/events` — proving the spawn, the allowlist, and
    the bus all actually connect rather than merely existing side by side.
    """
    argv_file = tmp_path / "argv.txt"
    canned = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    stub = _write_agent_stub(tmp_path / "agent-stub", argv_file, canned)
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/agent", {"prompt": "cut the retake"})
        assert status == 202
        assert payload["accepted"] is True

        found = None
        for event, data in events:
            if event == "agent":
                found = data
                break
        assert found == canned
    finally:
        conn.close()

    argv = argv_file.read_text(encoding="utf-8").splitlines()
    assert "--strict-mcp-config" in argv
    assert "mcp__proofcut__*" in argv
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "manual"
    for tool in ("Bash", "Write", "Edit", "WebFetch", "WebSearch"):
        assert tool in argv
    # --verbose: claude 2.1.226 refuses --print --output-format=stream-json
    # without it (errors and exits 0 with nothing on stdout) — see
    # PLAN.md § The agent panel, in mechanism.
    assert "--verbose" in argv
    # --tools: the allow/disallow lists alone do not gate built-in tools
    # absent from both — this is what actually confines the agent to proofcut's
    # MCP tools and nothing else. Same PLAN.md section. Its one survivor is
    # ToolSearch, which defers the MCP definitions (docs/plans/MCP.md § Step 1)
    # and reads no file — nothing else may ride along.
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == "ToolSearch"


def test_the_agents_mcp_config_spawns_this_interpreter_not_a_path_lookup(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`"command": "proofcut"` is a PATH lookup performed by `claude`, and PATH is
    not this process's.

    Every launch that skips an activated venv — `.venv/bin/proofcut web`, a
    desktop entry, whatever `proofcut open` is wired to — leaves the name
    unresolvable, and the failure is silent in the worst way: measured on this
    box, `claude`'s own `system`/`init` event came back
    `mcp_servers: [{"name": "lucid", "status": "failed"}]` with `tools: []`,
    and the panel answered the prompt in prose while its banner went on saying
    it reaches the timeline through lucid's tools. The property that cannot
    degrade that way is an absolute interpreter that exists on disk, so that is
    what this asserts — not the string that happens to be there today.
    """
    argv_file = tmp_path / "argv.txt"
    stub = _write_agent_stub(tmp_path / "agent-stub", argv_file, {"type": "result", "subtype": "success"})
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    status, _ = _post(f"{server}/api/agent", {"prompt": "hello"})
    assert status == 202
    deadline = time.time() + 5
    while time.time() < deadline and not argv_file.exists():
        time.sleep(0.05)
    argv = argv_file.read_text(encoding="utf-8").splitlines()

    config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    entry = config["mcpServers"]["proofcut"]
    command = Path(entry["command"])
    assert command.is_absolute(), f"{entry['command']!r} is a PATH lookup, not a resolved binary"
    assert command.exists(), f"{command} does not exist"
    assert entry["args"][:2] == ["-m", "proofcut.cli"]
    assert entry["args"][-1] == "mcp"
    assert "-C" in entry["args"]


def _write_silent_agent_stub(path: Path) -> Path:
    """A fake `claude` that reproduces the real bug this test guards against:
    it errors to stderr and exits 0 without ever writing a `stream-json` line
    to stdout — exactly what claude 2.1.226 does when `--verbose` is missing
    from a `--print --output-format=stream-json` invocation.
    """
    return write_stub(path, "import sys\nprint('Error: boom, no --verbose', file=sys.stderr)\n")


def test_a_silent_agent_exit_still_surfaces_as_a_result_event(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subprocess that exits without ever printing a `stream-json` line
    must not leave the page's composer hung on `busy` forever with nothing on
    `/api/events` to clear it — the failure mode a real `claude` upgrade
    silently produced. A synthetic `result` event with a non-"success"
    subtype is what `agent.js`'s `handleResult` needs to show an error and
    clear `busy`.
    """
    stub = _write_silent_agent_stub(tmp_path / "silent-agent-stub")
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/agent", {"prompt": "cut the retake"})
        assert status == 202
        assert payload["accepted"] is True

        found = None
        for event, data in events:
            if event == "agent":
                found = data
                break
        assert found is not None
        assert found["type"] == "result"
        assert found["subtype"] != "success"
        assert "boom" in found["result"]
    finally:
        conn.close()


def test_a_failed_agent_spawn_is_a_json_error_not_a_reset(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary that cannot spawn at all (`claude` missing from PATH, a dead
    PROOFCUT_AGENT_BIN) puts nothing on `/api/events` — so the composer's only
    way to hear about it is this request failing as JSON rather than the
    connection resetting on an uncaught server-side traceback.
    """
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(tmp_path / "no-such-binary"))
    status, payload = _post(f"{server}/api/agent", {"prompt": "cut the retake"})
    assert status == 500
    assert "could not start the agent" in payload["error"]


def test_agent_stop_with_no_subprocess_running_is_a_no_op(server: str) -> None:
    status, payload = _post(f"{server}/api/agent/stop", {})
    assert status == 200
    assert payload["stopped"] is True


# -- per-turn thumbs (agent panel cosmetics, item 2) -------------------------


def test_agent_thumbs_rejects_a_bad_host(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/api/agent/thumbs",
        data=b"{}",
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


def test_agent_thumbs_requires_json_content_type(server: str) -> None:
    status, payload = _post(
        f"{server}/api/agent/thumbs",
        {"rating": "up", "session_id": "s", "turn_id": "t"},
        content_type="text/plain",
    )
    assert status == 400
    assert "application/json" in payload["error"]


def test_agent_thumbs_rejects_a_bad_rating(server: str) -> None:
    status, payload = _post(
        f"{server}/api/agent/thumbs",
        {"rating": "sideways", "session_id": "s", "turn_id": "t"},
    )
    assert status == 400
    assert "rating" in payload["error"]


def test_agent_thumbs_requires_session_and_turn_id(server: str) -> None:
    status, payload = _post(f"{server}/api/agent/thumbs", {"rating": "up", "turn_id": "t"})
    assert status == 400
    assert "session_id" in payload["error"]

    status, payload = _post(f"{server}/api/agent/thumbs", {"rating": "up", "session_id": "s"})
    assert status == 400
    assert "turn_id" in payload["error"]


def test_agent_thumbs_appends_a_record_without_touching_the_timeline(
    project: Path, server: str
) -> None:
    _, before = _json(f"{server}/api/view")

    host, port = _host_and_port(server)
    # `getresponse()` on a Connection: close response (this stream has no
    # Content-Length) hands the socket to the response object and nulls
    # `conn.sock` — so the read timeout has to be set on the connection
    # before that happens, not on `conn.sock` afterwards. It applies to the
    # whole socket lifetime, including the earlier reads, which is fine —
    # they arrive immediately.
    conn = http.client.HTTPConnection(host, port, timeout=1.2)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, payload = _post(
            f"{server}/api/agent/thumbs",
            {
                "rating": "up",
                "session_id": "sess-1",
                "turn_id": "turn-1",
                "prompt": "cut the retake",
            },
        )
        assert status == 200
        assert payload["recorded"] is True
        assert payload["rating"] == "up"
        assert payload["session_id"] == "sess-1"
        assert payload["turn_id"] == "turn-1"
        assert payload["prompt"] == "cut the retake"

        # The SSE poll loop only writes when `_revision` moves — a thumbs
        # write touches neither `project.otio`'s mtime nor the snapshot
        # count, so nothing should arrive here within a couple of poll
        # cycles (_REVISION_POLL_SECONDS is 0.5).
        with pytest.raises(TimeoutError):
            next(events)
    finally:
        conn.close()

    lines = Project.open(project).thumbs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session_id"] == "sess-1"
    assert record["turn_id"] == "turn-1"
    assert record["rating"] == "up"
    assert record["prompt"] == "cut the retake"
    assert "ts" in record

    _, after = _json(f"{server}/api/view")
    assert after["undo_depth"] == before["undo_depth"]
    assert after["timeline_duration"] == before["timeline_duration"]


def test_agent_thumbs_appends_a_second_line_for_a_second_turn(project: Path, server: str) -> None:
    _post(
        f"{server}/api/agent/thumbs",
        {"rating": "up", "session_id": "sess-1", "turn_id": "turn-1"},
    )
    status, payload = _post(
        f"{server}/api/agent/thumbs",
        {"rating": "down", "session_id": "sess-1", "turn_id": "turn-2"},
    )
    assert status == 200
    assert payload["rating"] == "down"

    lines = Project.open(project).thumbs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    turn_ids = {json.loads(line)["turn_id"] for line in lines}
    assert turn_ids == {"turn-1", "turn-2"}


# -- Start New Task (agent panel cosmetics, item 4) --------------------------


def test_agent_new_task_rejects_a_bad_host_and_bad_content_type(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/api/agent/new-task",
        data=b"{}",
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403

    status, payload = _post(f"{server}/api/agent/new-task", {}, content_type="text/plain")
    assert status == 400
    assert "application/json" in payload["error"]


def test_new_task_with_no_subprocess_running_is_a_no_op(server: str) -> None:
    status, payload = _post(f"{server}/api/agent/new-task", {})
    assert status == 200
    assert payload["reset"] is True


def test_new_task_kills_the_running_subprocess_and_the_next_prompt_spawns_a_fresh_one(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "pids.txt"
    canned = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    stub = _write_pid_recording_stub(tmp_path / "agent-stub", pid_file, canned)
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, _ = _post(f"{server}/api/agent", {"prompt": "first"})
        assert status == 202
        event, data = next(events)
        assert event == "agent"
        assert data == canned

        pids_after_first = pid_file.read_text(encoding="utf-8").split()
        assert len(pids_after_first) == 1

        status, payload = _post(f"{server}/api/agent/new-task", {})
        assert status == 200
        assert payload["reset"] is True

        status, _ = _post(f"{server}/api/agent", {"prompt": "second"})
        assert status == 202
        event, data = next(events)
        assert event == "agent"
        assert data == canned

        pids_after_second = pid_file.read_text(encoding="utf-8").split()
        assert len(pids_after_second) == 2
        # The second spawn is a genuinely different process, not the killed
        # one reused.
        assert pids_after_second[0] != pids_after_second[1]
    finally:
        conn.close()


def test_new_task_mid_turn_does_not_leak_a_synthetic_error_event(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards `_suppress_next_exit_report` specifically: a reset kills a live
    subprocess before it ever emits a `result` event, which is exactly the
    shape `_pump_stdout`'s silent-exit safety net (written for a genuinely
    broken subprocess) would otherwise report as `error_no_output` — right
    after the deliberate reset that a person clicked New Task to get.
    """
    argv_file = tmp_path / "argv.txt"
    canned = {"type": "assistant", "message": {"content": [{"type": "text", "text": "working…"}]}}
    stub = _write_agent_stub(tmp_path / "agent-stub", argv_file, canned)
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    host, port = _host_and_port(server)
    # See the comment in test_agent_thumbs_appends_a_record_without_touching_the_timeline
    # on why the timeout is set on the connection, not on `conn.sock`.
    conn = http.client.HTTPConnection(host, port, timeout=1.2)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, _ = _post(f"{server}/api/agent", {"prompt": "cut the retake"})
        assert status == 202
        event, data = next(events)
        assert event == "agent"
        assert data == canned  # the turn is genuinely live and mid-flight

        status, payload = _post(f"{server}/api/agent/new-task", {})
        assert status == 200
        assert payload["reset"] is True

        # No error_no_output (or any) result event should follow the kill.
        with pytest.raises(TimeoutError):
            next(events)
    finally:
        conn.close()


def test_changing_the_model_mid_conversation_kills_the_subprocess_and_respawns(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--model` bakes into `claude -p`'s argv at spawn — there is no way to
    hot-swap a running turn's model — so `AgentSession.send`'s own
    stale-process check is what makes picking a different model actually
    take effect: kill the live subprocess and let the existing lazy-respawn
    machinery start a genuinely new one, the same proof
    `test_new_task_kills_the_running_subprocess_and_the_next_prompt_spawns_a_fresh_one`
    makes for an explicit New Task, reused here for an implicit one.
    """
    pid_file = tmp_path / "pids.txt"
    argv_file = tmp_path / "argv.txt"
    canned = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    stub = _write_pid_and_argv_stub(tmp_path / "agent-stub", pid_file, argv_file, canned)
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, _ = _post(f"{server}/api/agent", {"prompt": "first"})
        assert status == 202
        event, data = next(events)
        assert event == "agent"
        assert data == canned

        pids_after_first = pid_file.read_text(encoding="utf-8").split()
        assert len(pids_after_first) == 1
        argv = argv_file.read_text(encoding="utf-8").splitlines()
        assert "--model" not in argv  # first turn used the default

        status, _ = _post(f"{server}/api/agent", {"prompt": "second", "model": "claude-opus-5"})
        assert status == 202
        event, data = next(events)
        assert event == "agent"
        assert data == canned

        pids_after_second = pid_file.read_text(encoding="utf-8").split()
        assert len(pids_after_second) == 2
        assert pids_after_second[0] != pids_after_second[1]
        argv = argv_file.read_text(encoding="utf-8").splitlines()
        assert "--model" in argv
        assert argv[argv.index("--model") + 1] == "claude-opus-5"
    finally:
        conn.close()


def test_sending_the_same_model_again_reuses_the_live_subprocess(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary path — same model as last turn — must touch nothing
    extra: no kill, no respawn, the live subprocess just gets a second line
    on its stdin the way any other multi-turn conversation would.
    """
    pid_file = tmp_path / "pids.txt"
    canned = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    stub = _write_pid_recording_stub(tmp_path / "agent-stub", pid_file, canned)
    monkeypatch.setenv(webui.AGENT_BIN_ENV, str(stub))

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, _ = _post(f"{server}/api/agent", {"prompt": "first", "model": "claude-sonnet-5"})
        assert status == 202
        event, data = next(events)
        assert event == "agent"
        assert data == canned

        status, _ = _post(f"{server}/api/agent", {"prompt": "second", "model": "claude-sonnet-5"})
        assert status == 202

        # The stub blocks on stdin after its one canned line, so a respawn
        # (a second invocation) is the only way a second PID could appear —
        # poll briefly rather than a fixed sleep, since there is nothing else
        # to wait on.
        deadline = time.time() + 1.0
        while time.time() < deadline and len(pid_file.read_text(encoding="utf-8").split()) < 2:
            time.sleep(0.05)
        pids = pid_file.read_text(encoding="utf-8").split()
        assert len(pids) == 1, "same model must reuse the live subprocess, not respawn"
    finally:
        conn.close()


# -- render jobs -------------------------------------------------------------
#
# `ops.export` is monkeypatched to a stub that writes a tiny real WAV instead
# of shelling out to auto-editor (PLAN.md § Finishing; the task's own
# guidance for this test style) — these exercise the job model's plumbing,
# not a real render. `ops.verify` is separately stubbed where a completion
# payload's shape is asserted, so the test does not depend on whisper being
# on this machine's PATH; where it is *not* stubbed, the real absence of
# whisper (CLAUDE.md) drives the honest `verify: skipped` path for free.


def _fast_export_stub(
    path: str,
    output: str,
    *,
    export_format: str | None = None,
    fps: float | None = None,
    log: bool = True,
) -> dict[str, Any]:
    _make_wav(Path(output), duration=0.3)
    return {
        "output": output,
        "format": "media",
        "timebase": 1000.0,
        "segments": 1,
        "timeline_duration": 0.3,
    }


def _render_output_path(project: Path, job_id: str) -> Path:
    # RenderJob._output_path: `web-<job_id><suffix>` under renders/, where the
    # suffix follows the fixture's one clip, vo.wav.
    return project / "renders" / f"web-{job_id}.wav"


def _next_render_event(events: Iterator[tuple[str, Any]], job_id: str) -> dict[str, Any]:
    """The first terminal `render` event for `job_id` off an open stream.

    `"running"` and the per-stage `"stage"` events (export/burn/check_frames/
    verify, published as the pipeline progresses — see webui.RenderJob) are
    both non-terminal; only `"done"`/`"error"`/`"cancelled"` end a run.
    """
    for event, data in events:
        if event == "render" and data.get("job_id") == job_id and data.get("status") not in ("running", "stage"):
            return data
    raise AssertionError(f"no completion event arrived for render {job_id}")


def test_a_layered_project_renders_into_a_container_melt_can_mux(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suffix follows the primary clip — except on a layered timeline.

    That one is rendered by melt with `vcodec=libx264`, and the VO it lays
    picture over is a `.wav`: naming the output after it would ask melt to mux
    h264 into a wav. The fact that the timeline is layered comes from
    `ops.timeline_view`, not from a second reading of the manifest here —
    the window never decides (CLAUDE.md).
    """
    second = project.parent / "vo2.wav"
    _make_wav(second)
    ops.import_media(project, second, clip_id="vo2")
    view = ops.timeline_view(str(project))
    tl.write(
        tl.to_otio(
            tl.Edit([tl.Segment("vo", 0.0, 2.0), tl.Segment("vo2", 0.0, 2.0)]),
            {c["clip_id"]: c for c in Project.open(project).read_manifest()["clips"]},
            rate=view["timebase"],
            name="proj",
        ),
        Project.open(project).timeline_path,
    )
    assert ops.timeline_view(str(project))["layered"] is True
    seen: dict[str, str] = {}

    def _stub(path: str, output: str, **kwargs: Any) -> dict[str, Any]:
        seen["output"] = output
        _make_wav(Path(output), duration=0.3)
        return {"output": output, "format": "media", "writer": "melt"}

    monkeypatch.setattr(ops, "export", _stub)
    monkeypatch.setattr(ops, "verify", lambda *a, **k: {"agrees": True, "stub": True})
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {})
        assert status == 202
        assert _next_render_event(events, payload["job_id"])["status"] == "done"
    finally:
        conn.close()

    assert Path(seen["output"]).suffix == ".mp4"


def test_render_accepted_returns_a_job_id(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "export", _fast_export_stub)
    status, payload = _post(f"{server}/api/render", {})
    assert status == 202
    assert isinstance(payload["job_id"], str) and payload["job_id"]


def test_a_second_render_while_one_is_running_is_refused(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()

    def _stub(
        path: str,
        output: str,
        *,
        export_format: str | None = None,
        fps: float | None = None,
        # The pipeline passes `log=False`: it appends the whole run to
        # `renderlog` itself, so `export`'s own record would be a prefix of it.
        log: bool = True,
    ) -> dict[str, Any]:
        _make_wav(Path(output), duration=0.2)
        gate.wait(timeout=5)
        return {"output": output, "format": "media", "timebase": 1000.0}

    monkeypatch.setattr(ops, "export", _stub)
    try:
        status, _ = _post(f"{server}/api/render", {})
        assert status == 202

        status, payload = _post(f"{server}/api/render", {})
        assert status == 409
        assert "error" in payload
    finally:
        gate.set()


def test_render_stop_deletes_the_partial_output_and_reports_cancelled(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()

    def _stub(
        path: str,
        output: str,
        *,
        export_format: str | None = None,
        fps: float | None = None,
        # The pipeline passes `log=False`: it appends the whole run to
        # `renderlog` itself, so `export`'s own record would be a prefix of it.
        log: bool = True,
    ) -> dict[str, Any]:
        _make_wav(Path(output), duration=0.2)
        gate.wait(timeout=5)
        return {"output": output, "format": "media", "timebase": 1000.0}

    monkeypatch.setattr(ops, "export", _stub)

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {})
        assert status == 202
        job_id = payload["job_id"]
        output = _render_output_path(project, job_id)

        deadline = time.monotonic() + 5
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert output.exists(), "the stub never wrote its partial output"

        status, _ = _post(f"{server}/api/render/stop", {})
        assert status == 200

        # Deleted the instant `stop` answers, not merely once the background
        # export call eventually notices the cancellation (PLAN.md line 1832:
        # the partial output is deleted, not left).
        assert not output.exists()

        gate.set()
        found = _next_render_event(events, job_id)
        assert found["status"] == "cancelled"
        assert not output.exists()
    finally:
        gate.set()
        conn.close()


def test_render_stop_kills_the_encode_rather_than_waiting_for_it(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop reaches the subprocess under `export`: a 60 s child ends at once.
    Until 2026-09-16 it took effect only when melt finished on its own."""
    started = threading.Event()

    def _stub(path: str, output: str, **kwargs: Any) -> dict[str, Any]:
        _make_wav(Path(output), duration=0.2)
        started.set()
        progress.run([sys.executable, "-c", "import time; time.sleep(60)"])
        return {"output": output, "format": "media", "timebase": 1000.0}

    monkeypatch.setattr(ops, "export", _stub)

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=15)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {})
        assert status == 202
        job_id = payload["job_id"]
        assert started.wait(timeout=5)

        stopped_at = time.monotonic()
        status, _ = _post(f"{server}/api/render/stop", {})
        assert status == 200
        found = _next_render_event(events, job_id)
        assert found["status"] == "cancelled"
        assert time.monotonic() - stopped_at < 10
        assert not _render_output_path(project, job_id).exists()
    finally:
        conn.close()


def test_the_render_pipeline_logs_the_edit_its_export_read(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RenderJob` logs the whole run itself (`log=False` on the op), so the
    export's `source` stamp reaches the log only if the pipeline carries it —
    and without it the window's own renders could never say they were stale."""
    stamped: dict[str, Any] = {}

    def _stub(path: str, output: str, **kwargs: Any) -> dict[str, Any]:
        _make_wav(Path(output), duration=0.3)
        stamped.update(renderlog.stamp(Project.open(path)))
        return {"output": output, "format": "media", "source": dict(stamped)}

    monkeypatch.setattr(ops, "export", _stub)
    monkeypatch.setattr(ops, "verify", lambda *a, **k: {"agrees": True, "stub": True})
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {"burn": False})
        assert status == 202
        assert _next_render_event(events, payload["job_id"])["status"] == "done"
    finally:
        conn.close()

    run = renderlog.last(Project.open(project))
    assert run["sources"] == {"export": stamped}
    assert ops.finish_report(str(project))["last_render"]["current"] is True


def test_render_completion_event_carries_dimensions_and_check_results(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "export", _fast_export_stub)
    monkeypatch.setattr(ops, "verify", lambda *a, **k: {"agrees": True, "stub": True})

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {})
        assert status == 202
        job_id = payload["job_id"]

        found = _next_render_event(events, job_id)
        assert found["status"] == "done"
        assert Path(found["output"]).exists()
        # (a) honesty: these are ffprobe's own read of the file that landed on
        # disk, not auto-editor's exit code.
        assert found["has_video"] is False
        assert found["has_audio"] is True
        assert found["duration"] == pytest.approx(0.3, abs=0.05)

        # (b) the completion card is where the existing checks land.
        checks = found["checks"]
        assert checks["verify"] == {"agrees": True, "stub": True}
        for name in ("check_frames", "check_black", "spot_frames"):
            assert checks[name]["skipped"] is True
            assert "video" in checks[name]["reason"]
    finally:
        conn.close()


def _fast_export_stub_with_preset(
    path: str,
    output: str,
    *,
    export_format: str | None = None,
    fps: float | None = None,
    preset: str | None = None,
    resolution: tuple[int, int] | None = None,
    log: bool = True,
) -> dict[str, Any]:
    _make_wav(Path(output), duration=0.3)
    return {
        "output": output,
        "format": "media",
        "timebase": 1000.0,
        "segments": 1,
        "timeline_duration": 0.3,
        "preset": preset,
        "resolution": list(resolution) if resolution else None,
    }


def test_render_accepts_preset_and_resolution_and_echoes_them_on_the_running_event(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "export", _fast_export_stub_with_preset)
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {"preset": "web", "resolution": [608, 1080]})
        assert status == 202

        # the FIRST render event is 'running' — the one that carries the echo.
        for event, data in events:
            if event == "render" and data.get("job_id") == payload["job_id"]:
                assert data["status"] == "running"
                assert data["preset"] == "web"
                assert data["resolution"] == [608, 1080]
                break
        else:
            raise AssertionError("no render event arrived")
    finally:
        conn.close()


def test_render_resolution_must_be_a_two_element_int_list(server: str) -> None:
    for bad in ("1080x1920", [1080], [1080, 1920, 1], [1080.0, 1920.0]):
        status, payload = _post(f"{server}/api/render", {"resolution": bad})
        assert status == 400, bad
        assert "resolution" in payload["error"]


def test_render_with_an_invalid_preset_combination_reports_error_not_400(
    server: str,
) -> None:
    """A syntactically valid body ('custom' with no resolution) is a real
    caller mistake ops.export refuses — but only once the job is running, not
    at the HTTP layer, since this handler only checks shape."""
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {"preset": "custom"})
        assert status == 202
        job_id = payload["job_id"]

        found = _next_render_event(events, job_id)
        assert found["status"] == "error"
        assert "custom" in found["error"]
    finally:
        conn.close()


def test_render_endpoints_reject_a_bad_host(server: str) -> None:
    for path in ("/api/render", "/api/render/stop"):
        request = urllib.request.Request(
            f"{server}{path}",
            data=b"{}",
            headers={"Content-Type": "application/json", "Host": "evil.example.com"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code == 403, path


def test_render_endpoints_require_json_content_type(server: str) -> None:
    for path in ("/api/render", "/api/render/stop"):
        status, payload = _post(f"{server}{path}", {}, content_type="text/plain")
        assert status == 400, path
        assert "application/json" in payload["error"]


def test_render_stop_with_no_job_running_is_a_no_op(server: str) -> None:
    status, payload = _post(f"{server}/api/render/stop", {})
    assert status == 200
    assert payload["stopped"] is True


# -- render: the new `burn` request key ---------------------------------
#
# Contract §C.7/§D.2 — `burn` on `POST /api/render` is `null` (apply the
# project's own default) or a bool, and anything else is a 400, not a 500,
# same validation style `resolution`'s shape check already gets.


def _next_terminal_render_event(events: Iterator[tuple[str, Any]], job_id: str) -> dict[str, Any]:
    """The first `done`/`error`/`cancelled` `render` event for `job_id`.

    A local helper rather than reusing `_next_render_event` above: that one
    is defined as "first non-`running`" and the new `stage` events (one per
    pipeline stage, landing between `running` and the terminal event) now
    satisfy that condition without being terminal — see the reported finding.
    Written fresh here so this file's own new tests do not inherit that bug.
    """
    for event, data in events:
        if event == "render" and data.get("job_id") == job_id and data.get("status") in (
            "done",
            "error",
            "cancelled",
        ):
            return data
    raise AssertionError(f"no terminal event arrived for render {job_id}")


def test_render_start_rejects_a_non_bool_burn_value(server: str) -> None:
    for bad in ("yes", 1, 0, [], {}):
        status, payload = _post(f"{server}/api/render", {"burn": bad})
        assert status == 400, bad
        assert "burn" in payload["error"]
        # a 4xx body, never a raw 500/traceback
        assert "error" in payload and isinstance(payload["error"], str)


def test_render_start_accepts_explicit_burn_true_and_false(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "export", _fast_export_stub)
    monkeypatch.setattr(ops, "verify", lambda *a, **k: {"agrees": True, "stub": True})

    def _fast_add_captions_stub(path: str, output: str, **kwargs: Any) -> dict[str, Any]:
        burn = kwargs.get("burn")
        burned = Path(burn) if burn else Path(output)
        if not burned.exists():
            _make_wav(burned, duration=0.3)
        return {"burned": str(burned), "output": str(output)}

    monkeypatch.setattr(ops, "add_captions", _fast_add_captions_stub)

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/render", {"burn": False})
        assert status == 202
        found = _next_terminal_render_event(events, payload["job_id"])
        assert found["status"] == "done"

        status, payload = _post(f"{server}/api/render", {"burn": True})
        assert status == 202
        found = _next_terminal_render_event(events, payload["job_id"])
        assert found["status"] == "done"
    finally:
        conn.close()


def test_render_endpoints_still_require_json_content_type_with_a_burn_key(server: str) -> None:
    """The `application/json` guard (webui.py, `_json_body`) fires on the
    render-start route before the new `burn` key is ever looked at — a
    mutation POST missing the header still 400s, unchanged by this feature.
    """
    status, payload = _post(
        f"{server}/api/render", {"burn": True}, content_type="text/plain"
    )
    assert status == 400
    assert "application/json" in payload["error"]


# -- the proxy transcode job ---------------------------------------------
#
# PLAN.md § The preview proxy transcode. `ops.proxy_transcode` is stubbed the
# way `ops.export` is stubbed above: what these pin is the *route* — 202, the
# 409 on a busy slot, the completion event, and the refusals that must land
# before a job is ever claimed. The transcode's own behaviour is real-encode
# territory and lives in `tests/test_ops_proxy.py`.


def _next_proxy_event(events: Iterator[tuple[str, Any]], job_id: str) -> dict[str, Any]:
    """The first non-`running` `proxy` event for `job_id` off an open stream."""
    for event, data in events:
        if event == "proxy" and data.get("job_id") == job_id and data.get("status") != "running":
            return data
    raise AssertionError(f"no completion event arrived for proxy {job_id}")


def _clip_id(project: Path) -> str:
    return Project.open(project).read_manifest()["clips"][0]["clip_id"]


def test_proxy_accepted_returns_a_job_id_and_completes_on_the_stream(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip_id = _clip_id(project)
    monkeypatch.setattr(
        ops,
        "proxy_transcode",
        lambda path, clip, force=False: {"clip_id": clip, "proxy": "/x.mp4", "built": True},
    )
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/proxy", {"clip_id": clip_id})
        assert status == 202
        assert isinstance(payload["job_id"], str) and payload["job_id"]

        found = _next_proxy_event(events, payload["job_id"])
        assert found["status"] == "done"
        assert found["clip_id"] == clip_id
        assert found["built"] is True
    finally:
        conn.close()


def test_a_second_proxy_while_one_is_running_is_refused(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One slot per server, and 409 rather than 400: the identical request
    succeeds once the first finishes (`ProxyBusyError`)."""
    clip_id = _clip_id(project)
    gate = threading.Event()

    def _stub(path: str, clip: str, force: bool = False) -> dict[str, Any]:
        gate.wait(timeout=5)
        return {"clip_id": clip, "proxy": "/x.mp4", "built": True}

    monkeypatch.setattr(ops, "proxy_transcode", _stub)
    try:
        status, _ = _post(f"{server}/api/proxy", {"clip_id": clip_id})
        assert status == 202

        status, payload = _post(f"{server}/api/proxy", {"clip_id": clip_id})
        assert status == 409
        assert "already running" in payload["error"]
    finally:
        gate.set()


def test_a_render_and_a_proxy_hold_separate_slots(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different work on different files. Sharing one slot would make an export
    refuse while a preview transcoded, which is not a conflict anyone asked
    for."""
    clip_id = _clip_id(project)
    gate = threading.Event()

    def _stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        gate.wait(timeout=5)
        return {"clip_id": clip_id, "proxy": "/x.mp4", "built": True}

    monkeypatch.setattr(ops, "proxy_transcode", _stub)
    try:
        assert _post(f"{server}/api/proxy", {"clip_id": clip_id})[0] == 202

        monkeypatch.setattr(ops, "export", _fast_export_stub)
        assert _post(f"{server}/api/render", {})[0] == 202
    finally:
        gate.set()


def test_the_proxy_job_slot_is_released_after_a_refusal(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_finish()` in a `finally`. A refusal that latched the slot would turn
    one bad request into a permanent 409 until the server restarted."""
    clip_id = _clip_id(project)

    def _refuse(path: str, clip: str, force: bool = False) -> dict[str, Any]:
        raise ProjectError("nope")

    monkeypatch.setattr(ops, "proxy_transcode", _refuse)
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)

        status, payload = _post(f"{server}/api/proxy", {"clip_id": clip_id})
        assert status == 202
        found = _next_proxy_event(events, payload["job_id"])
        assert found["status"] == "error"
        assert found["error"] == "nope"
    finally:
        conn.close()

    assert _post(f"{server}/api/proxy", {"clip_id": clip_id})[0] == 202


def test_an_unknown_clip_is_a_400_before_a_job_starts(server: str) -> None:
    """Resolved on the request thread, so it is a bad request rather than an
    error event nobody was watching for."""
    status, payload = _post(f"{server}/api/proxy", {"clip_id": "no-such-clip"})
    assert status == 400
    assert "no-such-clip" in payload["error"]


def test_proxy_checks_the_shape_of_its_payload(server: str, project: Path) -> None:
    for payload in ({}, {"clip_id": ""}, {"clip_id": 3}):
        status, body = _post(f"{server}/api/proxy", payload)
        assert status == 400, payload
        assert "clip_id" in body["error"]

    status, body = _post(f"{server}/api/proxy", {"clip_id": _clip_id(project), "force": "yes"})
    assert status == 400
    assert "force" in body["error"]


def test_proxy_endpoint_rejects_a_bad_host(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/api/proxy",
        data=b"{}",
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


def test_proxy_endpoint_requires_json_content_type(server: str) -> None:
    status, payload = _post(f"{server}/api/proxy", {}, content_type="text/plain")
    assert status == 400
    assert "application/json" in payload["error"]


def test_render_on_an_empty_timeline_is_refused_before_a_job_starts(
    project: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `ops.undo` empties the seeded timeline back to nothing to import; simpler
    # to point the render at a project whose timeline was never seeded.
    empty = project.parent / "empty-proj"
    ops.init(empty)
    httpd = webui.make_server(empty, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _post(f"http://127.0.0.1:{httpd.server_address[1]}/api/render", {})
        assert status == 400
        assert "error" in payload
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


# -- footage in: /api/import, /api/transcribe, /api/transcript/attach ------
#
# docs/plans/STUDIO.md § Cross-cutting: "footage in" becomes a window operation.
# `ImportJob`/`TranscribeJob` are `ProxyJob`'s exact shape (one slot, request-
# thread validation before the slot is claimed, a dedicated `*BusyError`
# -> 409, completion published on the bus `/api/events` already serves), so
# these tests follow `test_proxy_accepted_returns_a_job_id_and_completes_on_
# the_stream` and its siblings above rather than inventing a new pattern.
# `/api/transcript/attach` is a plain `_POST_ROUTES` mutation instead (the
# `attach_transcript` op is instant), so it is tested the way `/api/music`
# is above: one HTTP round trip, no job, no stream.
#
# The success path of `/api/transcribe` is deliberately NOT exercised here —
# it would shell out to a real whisper subprocess and load a real model
# (CLAUDE.md: "Whisper is a subprocess ... it is not on PATH"). Only its
# refusal paths (resolved on the request thread, before a job starts) are
# covered; the ASR success path is left to the live browser pass.


def _next_event(events: Iterator[tuple[str, Any]], topic: str, job_id: str) -> dict[str, Any]:
    """The first non-`running` event on `topic` for `job_id` off an open
    stream — `_next_proxy_event`/`_next_render_event`'s shape, generalised
    since `import` and `transcribe` are two more bus topics of the same
    kind."""
    for event, data in events:
        if event == topic and data.get("job_id") == job_id and data.get("status") != "running":
            return data
    raise AssertionError(f"no completion event arrived for {topic} {job_id}")


def test_import_accepted_returns_a_job_id_and_the_clip_lands_in_the_manifest(
    project: Path, server: str, tmp_path: Path
) -> None:
    source = tmp_path / "b.wav"
    _make_wav(source, duration=3.0)
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/import", {"source": str(source), "clip_id": "b"})
        assert status == 202
        assert isinstance(payload["job_id"], str) and payload["job_id"]

        found = _next_event(events, "import", payload["job_id"])
        assert found["status"] == "done"
        assert found["clip_id"] == "b"
    finally:
        conn.close()

    clips = Project.open(project).read_manifest()["clips"]
    assert any(c["clip_id"] == "b" and c["source"] == str(source.resolve()) for c in clips)


def test_a_second_import_while_one_is_running_is_refused(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One slot per server, and 409 rather than 400 — `ImportBusyError`,
    `ProxyBusyError`'s sibling. The path checks that make a *bad* source a
    400 run before the slot is claimed either way, so a real, readable file
    is what actually exercises the slot itself."""
    source = tmp_path / "b.wav"
    _make_wav(source, duration=1.0)
    gate = threading.Event()

    def _stub(
        path: str,
        src: Any,
        *,
        clip_id: str | None = None,
        copy: bool = False,
        mix: bool = False,
        audio_stream: int | None = None,
    ) -> dict[str, Any]:
        gate.wait(timeout=5)
        return {"clip_id": clip_id or "b", "source": str(src)}

    monkeypatch.setattr(ops, "import_media", _stub)
    try:
        status, _ = _post(f"{server}/api/import", {"source": str(source), "clip_id": "b"})
        assert status == 202

        status, payload = _post(f"{server}/api/import", {"source": str(source), "clip_id": "c"})
        assert status == 409
        assert "already running" in payload["error"]
    finally:
        gate.set()


def test_import_forwards_the_audio_choice(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A two-mic container is refused unless the caller says what to do with
    it, and the window is a client of the same op — so the choice has to
    reach `ops.import_media` from here exactly as it does from the CLI.
    """
    source = tmp_path / "b.wav"
    _make_wav(source, duration=1.0)
    seen: dict[str, Any] = {}
    done = threading.Event()

    def _stub(path: str, src: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        done.set()
        return {"clip_id": "b", "source": str(src)}

    monkeypatch.setattr(ops, "import_media", _stub)
    status, _ = _post(f"{server}/api/import", {"source": str(source), "mix": True})
    assert status == 202
    assert done.wait(timeout=5)
    assert seen["mix"] is True
    assert seen["audio_stream"] is None

    # And stream 0 must arrive as 0, not as the None it shares a falsiness
    # with — "keep the first track" is the pane's other offer.
    seen.clear()
    done.clear()
    status, _ = _post(f"{server}/api/import", {"source": str(source), "audio_stream": 0})
    assert status == 202
    assert done.wait(timeout=5)
    assert seen["audio_stream"] == 0
    assert seen["mix"] is False


def test_import_rejects_a_boolean_audio_stream(server: str, tmp_path: Path) -> None:
    """`isinstance(True, int)` is True in Python, so a stray `true` would
    read as stream 1 — the *second* mic — and import the wrong person.
    """
    source = tmp_path / "b.wav"
    _make_wav(source, duration=1.0)

    status, payload = _post(
        f"{server}/api/import", {"source": str(source), "audio_stream": True}
    )

    assert status == 400
    assert "audio_stream" in payload["error"]


def test_a_multi_mic_refusal_reaches_the_window_as_a_count(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal the pane can act on rather than only print.

    `audio_streams` rides the error event so the offer button never has to
    string-match the sentence to know which refusal came back — the same
    reason `MultiAudioError` is a type rather than a message.
    """
    source = tmp_path / "b.wav"
    _make_wav(source, duration=1.0)

    def _stub(path: str, src: Any, **kwargs: Any) -> dict[str, Any]:
        raise media.MultiAudioError("two mics in there", streams=2)

    monkeypatch.setattr(ops, "import_media", _stub)
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)

        status, payload = _post(f"{server}/api/import", {"source": str(source)})
        assert status == 202
        found = _next_event(events, "import", payload["job_id"])
    finally:
        conn.close()

    assert found["status"] == "error"
    assert found["audio_streams"] == 2
    assert found["error"] == "two mics in there"
    # The source rides it too, so a re-post can be built without the form.
    assert found["source"] == str(source)


def test_import_of_a_missing_source_is_400_not_a_job(server: str, tmp_path: Path) -> None:
    """Resolved on the request thread — a bad path is a 400, never a job
    that starts only to fail (decision #2 in docs/plans/STUDIO.md's build order)."""
    missing = tmp_path / "nope.wav"
    status, payload = _post(f"{server}/api/import", {"source": str(missing)})
    assert status == 400
    assert "no such file" in payload["error"]
    assert str(missing) in payload["error"]


def test_import_of_a_directory_is_400_with_an_actionable_message(
    server: str, tmp_path: Path
) -> None:
    directory = tmp_path / "footage"
    directory.mkdir()
    status, payload = _post(f"{server}/api/import", {"source": str(directory)})
    assert status == 400
    assert "directory" in payload["error"]


def test_import_requires_json_content_type(server: str, tmp_path: Path) -> None:
    source = tmp_path / "b.wav"
    _make_wav(source, duration=1.0)
    status, payload = _post(
        f"{server}/api/import", {"source": str(source)}, content_type="text/plain"
    )
    assert status == 400
    assert "application/json" in payload["error"]


def test_import_rejects_a_non_loopback_host(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/api/import",
        data=b"{}",
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


def test_import_404s_under_picker_root_before_a_project_is_open(
    picker_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "b.wav"
    _make_wav(source, duration=1.0)
    status, payload = _post(f"{picker_server}/api/import", {"source": str(source)})
    assert status == 404
    assert "no project open" in payload["error"]


def test_transcribe_unknown_clip_is_a_400_before_a_job_starts(server: str) -> None:
    """`TranscribeJob.start` resolves the clip through `media.get_clip` on
    the request thread — the `ProxyJob` precedent — so an unknown clip_id is
    a bad request, not a job that spins up whisper only to error."""
    status, payload = _post(f"{server}/api/transcribe", {"clip_id": "no-such-clip"})
    assert status == 400
    assert "no-such-clip" in payload["error"]


def test_transcribe_checks_the_shape_of_its_payload(server: str, project: Path) -> None:
    for payload in ({}, {"clip_id": ""}, {"clip_id": 3}):
        status, body = _post(f"{server}/api/transcribe", payload)
        assert status == 400, payload
        assert "clip_id" in body["error"]

    status, body = _post(f"{server}/api/transcribe", {"clip_id": _clip_id(project), "model": 7})
    assert status == 400
    assert "model" in body["error"]

    status, body = _post(
        f"{server}/api/transcribe", {"clip_id": _clip_id(project), "language": 7}
    )
    assert status == 400
    assert "language" in body["error"]


def test_a_second_transcribe_while_one_is_running_is_refused(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`TranscribeBusyError`, `ProxyBusyError`'s sibling — same one-slot
    shape, proven the same way the proxy job's is proven above."""
    clip_id = _clip_id(project)
    gate = threading.Event()

    def _stub(path: str, clip: str, **kwargs: Any) -> dict[str, Any]:
        gate.wait(timeout=5)
        return {"clip_id": clip, "words": 0, "language": "en"}

    monkeypatch.setattr(ops, "transcribe", _stub)
    try:
        status, _ = _post(f"{server}/api/transcribe", {"clip_id": clip_id})
        assert status == 202

        status, payload = _post(f"{server}/api/transcribe", {"clip_id": clip_id})
        assert status == 409
        assert "already running" in payload["error"]
    finally:
        gate.set()


def test_a_transcription_job_says_how_far_it_has_got(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job hands `ops.transcribe` a reporter, and each report reaches the
    page as a `progress` event on the job's own topic, before `done` — the
    same reports the MCP server sends a client. docs/plans/MCP.md § Step 6."""
    from proofcut import progress

    clip_id = _clip_id(project)

    def _stub(path: str, clip: str, **kwargs: Any) -> dict[str, Any]:
        progress.report(5.0, 10.0, f"transcribing {clip}")
        return {"clip_id": clip, "words": 0, "language": "en"}

    monkeypatch.setattr(ops, "transcribe", _stub)
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)
        status, _ = _post(f"{server}/api/transcribe", {"clip_id": clip_id})
        assert status == 202
        seen = []
        for event, data in events:
            if event != "transcribe":
                continue
            seen.append(data["status"])
            if data["status"] == "progress":
                assert (data["progress"], data["total"], data["clip_id"]) == (5.0, 10.0, clip_id)
            if data["status"] in {"done", "error"}:
                break
    finally:
        conn.close()
    assert seen == ["running", "progress", "done"]


def test_transcript_attach_round_trips_over_http(
    project: Path, server: str, tmp_path: Path
) -> None:
    """`/api/transcript/attach`'s a plain `_POST_ROUTES` mutation — one round
    trip, the op's own return value echoed back, and the words it attached
    reachable off `/api/view` right afterward. Same transcript-fixture shape
    `test_an_off_timeline_clip_with_a_transcript_reads_as_entirely_cut` above
    already uses, reused rather than inventing a second one."""
    other = tmp_path / "b.wav"
    _make_wav(other, duration=3.0)
    ops.import_media(project, other, clip_id="b")
    words = [{"word": f"b{n}", "start": float(n) * 0.5, "end": n * 0.5 + 0.4} for n in range(4)]
    transcript = tmp_path / "b.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")

    status, payload = _post(
        f"{server}/api/transcript/attach", {"clip_id": "b", "path": str(transcript)}
    )
    assert status == 200
    assert payload["clip_id"] == "b"
    assert payload["words"] == len(words)
    assert payload["language"] == "en"
    # The four attach-time findings ops.attach_transcript reports.
    for key in ("near_duplicates", "suspect_durations", "overlaps", "repeats"):
        assert key in payload

    _, view = _json(f"{server}/api/view?clip_id=b")
    assert view["words"], "the attached transcript should now draw as words"
    assert [w["text"] for w in view["words"]] == [w["word"] for w in words]


def test_transcript_attach_requires_json_content_type(server: str, tmp_path: Path) -> None:
    transcript = tmp_path / "b.json"
    transcript.write_text(json.dumps({"language": "en", "words": []}), encoding="utf-8")
    status, payload = _post(
        f"{server}/api/transcript/attach",
        {"clip_id": "vo", "path": str(transcript)},
        content_type="text/plain",
    )
    assert status == 400
    assert "application/json" in payload["error"]


def test_transcript_attach_rejects_a_bad_host(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/api/transcript/attach",
        data=b"{}",
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


# -- multi-project: the picker over a --root scan --------------------------
#
# docs/plans/DAYDREAM.md § Multi-project. `webui.scan_projects` and the picker-mode
# routes it feeds (`Handler._route_picker`, `Handler._handle_open`) — see
# webui.py's own docstrings for the design (a picker binds this process to
# at most one project, permanently, deferring `make_server`'s own binding
# rather than adding a second one).


@pytest.fixture
def picker_server(tmp_path: Path) -> Iterator[str]:
    """A picker server over `tmp_path`, torn down after the test — nothing
    is open on it yet, unlike `server` above."""
    httpd = webui.make_picker_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        agent = getattr(httpd, "agent", None)
        if agent is not None:
            agent.close()
        thread.join(timeout=5)


def _write_manifest(directory: Path, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "proofcut.json").write_text(text, encoding="utf-8")


def test_scan_finds_a_project_and_skips_a_non_project_directory(
    project: Path, picker_server: str
) -> None:
    not_a_project = project.parent / "not-a-project"
    not_a_project.mkdir()
    (not_a_project / "readme.txt").write_text("nope", encoding="utf-8")

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entries = {entry["path"]: entry for entry in payload["projects"]}
    assert str(project) in entries
    entry = entries[str(project)]
    assert entry["status"] == "ok"
    assert entry["segments"] == 1  # the seeded, uncut timeline
    assert entry["clips"] == 1
    assert not any(p.endswith("not-a-project") for p in entries)


def test_an_old_schema_project_is_listed_as_needing_migration_not_skipped_or_opened(
    project: Path, picker_server: str
) -> None:
    old = project.parent / "old-schema"
    _write_manifest(old, json.dumps({"schema_version": 2, "clips": []}))

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entry = {entry["path"]: entry for entry in payload["projects"]}[str(old)]
    assert entry["status"] == "needs_migration"
    assert entry["schema_version"] == 2


def test_a_pre_rename_project_is_listed_as_needing_migration_not_skipped_or_opened(
    project: Path, picker_server: str
) -> None:
    """A directory holding only `lucid.json` (docs/plans/RENAME.md, decision 1)
    — at the *current* schema, so the schema check alone would call it fine
    and a scan that looked only for `proofcut.json` would not list it at all."""
    old = project.parent / "pre-rename"
    ops.init(old)
    (old / "proofcut.json").rename(old / "lucid.json")
    before = (old / "lucid.json").read_bytes()

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entry = {entry["path"]: entry for entry in payload["projects"]}[str(old)]
    assert entry["status"] == "needs_migration"
    assert entry["manifest"] == "lucid.json"
    assert entry["schema_version"] == 4
    # Listed, never renamed as a side effect of being looked at.
    assert (old / "lucid.json").read_bytes() == before
    assert not (old / "proofcut.json").exists()

    status, payload = _post(f"{picker_server}/api/open", {"path": str(old)})
    assert status == 400
    assert "proofcut migrate" in payload["error"]
    assert not (old / "proofcut.json").exists()


def test_a_directory_holding_both_manifests_is_listed_unreadable(
    project: Path, picker_server: str
) -> None:
    both = project.parent / "both-manifests"
    ops.init(both)
    (both / "lucid.json").write_bytes((both / "proofcut.json").read_bytes())

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entry = {entry["path"]: entry for entry in payload["projects"]}[str(both)]
    assert entry["status"] == "unreadable"
    assert "both" in entry["error"]


def test_an_unreadable_manifest_is_listed_not_skipped(
    project: Path, picker_server: str
) -> None:
    bad = project.parent / "bad-manifest"
    _write_manifest(bad, "{not json")

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entry = {entry["path"]: entry for entry in payload["projects"]}[str(bad)]
    assert entry["status"] == "unreadable"
    assert entry["error"]


def test_an_unseeded_project_is_listed_as_ok_and_unseeded(
    project: Path, picker_server: str
) -> None:
    """`ops.status` used to refuse a project with no timeline seeded yet,
    which put it in the `error` bucket here too; it now reports `seeded:
    false` instead of raising (TRIAL.md § `timeline_status` is the first
    call an agent makes and it refuses on a fresh project), so the scan
    reports it `ok` — a state to offer "finish setup" for, not a failure."""
    unseeded = project.parent / "unseeded"
    ops.init(unseeded)

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entries = {entry["path"]: entry for entry in payload["projects"]}
    assert entries[str(unseeded)]["status"] == "ok"
    # Which kind of `ok` this is, as data rather than as a sentence to
    # match: the picker offers to finish an un-seeded project and must never
    # offer that for a genuinely broken one (docs/plans/POLISH.md § Step 06).
    assert entries[str(unseeded)]["seeded"] is False
    assert entries[str(unseeded)]["clips"] == 0
    assert "timeline_duration" not in entries[str(unseeded)]
    # And the healthy project alongside it still lists normally.
    assert entries[str(project)]["status"] == "ok"
    assert entries[str(project)]["seeded"] is True


def test_a_project_with_a_corrupt_manifest_record_is_still_listed_as_an_error(
    project: Path, picker_server: str
) -> None:
    """`seeded: false` covers the ordinary no-timeline case now — this is
    what still lands in the `error` bucket: a current-schema project whose
    manifest carries a record `ops.status` cannot make sense of at all
    (`_stored_tail`'s own validation, read even before a timeline exists)."""
    broken = project.parent / "broken-tail"
    ops.init(broken)
    manifest_path = broken / "proofcut.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tail"] = {"asset": "card:not-a-real-asset"}  # missing required 'seconds'
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entry = {entry["path"]: entry for entry in payload["projects"]}[str(broken)]
    assert entry["status"] == "error"
    assert entry["error"]
    assert entry["seeded"] is False  # never got as far as `proofcut seed`


def test_scan_does_not_follow_a_symlink_outside_root(
    project: Path, picker_server: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A symlink under `--root` pointing at a project outside it must not
    have its metadata reported by the scan (webui.py's `walk`) — confinement
    at `/api/open` time doesn't help if the listing already leaked it."""
    outside = tmp_path_factory.mktemp("outside-root") / "proj"
    ops.init(outside)
    link = project.parent / "escape-link"
    link.symlink_to(outside)

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entries = {entry["path"] for entry in payload["projects"]}
    assert str(outside) not in entries
    assert str(link) not in entries


def test_the_picker_page_serves_before_a_project_is_open(picker_server: str) -> None:
    status, headers, body = _get(f"{picker_server}/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b'id="list"' in body
    assert b'src="/static/picker.js"' in body


def test_no_route_needs_a_project_before_one_is_open(picker_server: str) -> None:
    status, payload = _json(f"{picker_server}/api/view")
    assert status == 404
    assert "no project open" in payload["error"]

    status, payload = _post(f"{picker_server}/api/agent", {"prompt": "hi"})
    assert status == 404
    assert "no project open" in payload["error"]


def test_finish_before_open_404s_under_picker_root(picker_server: str) -> None:
    """§D.1's bind-order rule holds for the new route too: `_route_picker`
    only answers `/`, `/index.html`, `/static/*`, `/api/projects` before a
    project is bound, and 404s everything else — including `/api/finish` —
    with the same "no project open yet" message, never a 200 against an
    unbound `self.project_root`. No new code should have been needed for
    this (the new route sits behind the existing structural guard)."""
    status, payload = _json(f"{picker_server}/api/finish")
    assert status == 404
    assert "no project open" in payload["error"]


def test_open_binds_the_server_and_the_workspace_loads(
    project: Path, picker_server: str
) -> None:
    status, payload = _post(f"{picker_server}/api/open", {"path": str(project)})
    assert status == 200
    assert payload["opened"] is True
    assert payload["root"] == str(project.resolve())

    status, _, body = _get(f"{picker_server}/")
    assert status == 200
    assert b'id="workspace"' in body  # the ordinary shell now, not the picker

    status, view = _json(f"{picker_server}/api/view")
    assert status == 200
    assert view["clip_id"] == "vo"

    # A second click on the already-open project is a no-op, not an error.
    status, payload = _post(f"{picker_server}/api/open", {"path": str(project)})
    assert status == 200
    assert payload["opened"] is True


def test_open_refuses_a_second_different_project(
    project: Path, picker_server: str
) -> None:
    second = project.parent / "second-proj"
    ops.init(second)

    status, _ = _post(f"{picker_server}/api/open", {"path": str(project)})
    assert status == 200

    status, payload = _post(f"{picker_server}/api/open", {"path": str(second)})
    assert status == 409
    assert str(project.resolve()) in payload["error"]

    # And the first project is still the one actually bound.
    status, view = _json(f"{picker_server}/api/view")
    assert status == 200
    assert view["clip_id"] == "vo"


# -- the first run: create, then seed (docs/plans/POLISH.md § Step 06) ----------------


def test_create_makes_a_project_under_the_root_and_open_binds_it(
    picker_server: str, tmp_path: Path
) -> None:
    """The dead end this removes: an empty `--root` had no way in from the
    page at all, and a project created on the CLI still could not be opened
    from the picker because it has no timeline to draw."""
    status, payload = _post(f"{picker_server}/api/create", {"name": "fresh"})
    assert status == 200
    assert payload["created"] is True
    assert Path(payload["path"]) == (tmp_path / "fresh").resolve()
    assert (tmp_path / "fresh" / "proofcut.json").is_file()

    # Creating does not open — there is exactly one place that binds.
    status, payload = _post(f"{picker_server}/api/open", {"path": payload["path"]})
    assert status == 200
    assert payload["opened"] is True


def test_create_refuses_a_name_that_is_a_path(picker_server: str, tmp_path: Path) -> None:
    """A name, never a path: there is no traversal left to resolve away."""
    for bad in ("../escape", "a/b", "/absolute", ".."):
        status, payload = _post(f"{picker_server}/api/create", {"name": bad})
        assert status == 400, bad
        assert "name" in payload["error"], bad
    assert not (tmp_path.parent / "escape").exists()


def test_create_refuses_a_dotfile_name(picker_server: str, tmp_path: Path) -> None:
    """The scan skips anything beginning with a dot, so a project named that
    way would be created and then be invisible in the listing that made it."""
    status, _ = _post(f"{picker_server}/api/create", {"name": ".hidden"})
    assert status == 400
    assert not (tmp_path / ".hidden").exists()


def test_create_refuses_a_symlink_before_writing_anything(
    picker_server: str, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`Path.is_dir()` follows symlinks (CLAUDE.md), so a pre-existing link
    at the target name would pass a directory test and have a manifest
    written *through* it, outside the root."""
    outside = tmp_path_factory.mktemp("outside-create")
    link = tmp_path / "linked"
    link.symlink_to(outside)

    status, payload = _post(f"{picker_server}/api/create", {"name": "linked"})
    assert status == 403
    assert "symlink" in payload["error"]
    assert not (outside / "proofcut.json").exists()


def test_create_refuses_an_existing_project(picker_server: str, project: Path) -> None:
    """`Project.create` refuses rather than overwriting, and that refusal is
    what the page shows."""
    status, payload = _post(f"{picker_server}/api/create", {"name": project.name})
    assert status == 400
    assert "already exists" in payload["error"]


def test_create_is_absent_on_a_single_project_server(server: str) -> None:
    """Under `-C` the project already exists, so there is nothing to create —
    the route is not reachable rather than refused, `/api/open`'s own shape."""
    status, payload = _post(f"{server}/api/create", {"name": "fresh"})
    assert status == 404
    assert "no such endpoint" in payload["error"]


def test_create_needs_json_like_every_other_mutation(picker_server: str) -> None:
    status, payload = _post(
        f"{picker_server}/api/create", {"name": "fresh"}, content_type="text/plain"
    )
    assert status == 400
    assert "application/json" in payload["error"]


def test_create_refuses_a_non_loopback_host(picker_server: str) -> None:
    request = urllib.request.Request(
        f"{picker_server}/api/create",
        data=json.dumps({"name": "fresh"}).encode(),
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


def test_the_picker_still_lists_a_broken_sibling_after_a_create(
    picker_server: str, tmp_path: Path
) -> None:
    """One bad project must never take the listing down, and creating a new
    one alongside it must not change that."""
    _write_manifest(tmp_path / "bad-manifest", "{not json")

    status, _ = _post(f"{picker_server}/api/create", {"name": "fresh"})
    assert status == 200

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entries = {entry["path"]: entry for entry in payload["projects"]}
    assert entries[str(tmp_path / "bad-manifest")]["status"] == "unreadable"
    assert entries[str(tmp_path / "fresh")]["status"] == "ok"
    assert entries[str(tmp_path / "fresh")]["seeded"] is False


@needs_ffprobe
def test_seed_is_a_job_and_reports_on_the_stream(tmp_path: Path) -> None:
    """`ops.seed_timeline` was the one op with no window route at all, which
    is what made a freshly created project unreachable from the page. It is a
    job rather than a plain mutation because the silence pass runs
    auto-editor over the whole recording."""
    root = tmp_path / "proj"
    audio = tmp_path / "vo.wav"
    _make_wav(audio)
    words = [{"word": f"w{n}", "start": float(n), "end": n + 0.9} for n in range(8)]
    transcript = tmp_path / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    ops.init(root)
    ops.import_media(root, audio, clip_id="vo")
    ops.attach_transcript(root, "vo", transcript)

    httpd = webui.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    host, port = _host_and_port(base)
    conn = http.client.HTTPConnection(host, port, timeout=30)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the opening revision record

        status, payload = _post(f"{base}/api/seed", {"clip_id": "vo", "remove_silences": False})
        assert status == 202
        assert payload["job_id"]

        seen = []
        for name, data in events:
            if name != "seed":
                continue
            seen.append(data["status"])
            if data["status"] in ("done", "error"):
                assert data["status"] == "done", data.get("error")
                # The op's own return, which is the whole reason a client
                # reads the event instead of reloading the page.
                assert data["segments"] == 1
                assert data["timeline_duration"] == pytest.approx(12.0, abs=0.05)
                assert data["silences_removed"] is False
                break
        assert seen[0] == "running"

        status, view = _json(f"{base}/api/view")
        assert status == 200
        assert view["clip_id"] == "vo"
    finally:
        conn.close()
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


@needs_ffprobe
def test_seed_refuses_an_unknown_clip_as_a_400_not_an_event(server: str) -> None:
    """Resolved before the slot is claimed, `TranscribeJob.start`'s precedent
    — a bad clip_id is a bad request, not a job that starts and dies."""
    status, payload = _post(f"{server}/api/seed", {"clip_id": "nope"})
    assert status == 400
    assert "nope" in payload["error"]


def _refuse_as_too_long(where: str) -> Callable[..., Any]:
    def refuse(*args: object, **kwargs: object) -> None:
        error = OSError(2, "The filename or extension is too long", where)
        error.winerror = 206  # type: ignore[attr-defined]
        raise error

    return refuse


def test_a_path_windows_refuses_as_too_long_is_a_400_with_the_one_line(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every route answers proofcut's own refusals as a 400 and lets anything
    else keep its traceback — which, for a 206, meant a dropped connection and
    a window with nothing to say. GET and POST both, since they route apart."""
    where = "C:\\deep\\cache\\thumbs\\a-long-clip"
    monkeypatch.setattr(ops, "assets", _refuse_as_too_long(where))
    status, payload = _json(f"{server}/api/assets")
    assert status == 400
    assert payload["error"].startswith(f"Windows refused a path as too long ({len(where)} characters: {where})")

    monkeypatch.setattr(ops, "clip_role", _refuse_as_too_long(where))
    status, payload = _post(f"{server}/api/clip-role", {"clip_id": "vo", "role": "voiceover"})
    assert status == 400
    assert "LongPathsEnabled 1" in payload["error"]


def test_any_other_os_error_in_a_route_is_still_not_a_400(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: a bug keeps its traceback, so the request gets no answer."""
    monkeypatch.setattr(
        ops, "assets", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Access is denied"))
    )
    with pytest.raises((urllib.error.URLError, http.client.HTTPException, ConnectionError)):
        urllib.request.urlopen(f"{server}/api/assets", timeout=5)


@needs_ffprobe
def test_a_job_that_hits_a_path_too_long_publishes_the_one_line(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job flattens only proofcut's own refusals into its error event; a 206
    used to escape it, publish nothing, and leave the window waiting forever."""
    where = "C:\\deep\\cache\\history\\3.otio"
    monkeypatch.setattr(ops, "seed_timeline", _refuse_as_too_long(where))
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)
        status, payload = _post(f"{server}/api/seed", {"clip_id": "vo"})
        assert status == 202
        event = _next_event(events, "seed", payload["job_id"])
    finally:
        conn.close()
    assert event["status"] == "error"
    assert event["error"].startswith(f"Windows refused a path as too long ({len(where)} characters: {where})")


def test_seed_requires_a_clip_id(server: str) -> None:
    status, payload = _post(f"{server}/api/seed", {})
    assert status == 400
    assert "clip_id" in payload["error"]


def test_seed_requires_json(server: str) -> None:
    status, payload = _post(f"{server}/api/seed", {"clip_id": "vo"}, content_type="text/plain")
    assert status == 400
    assert "application/json" in payload["error"]


def test_open_refuses_a_path_outside_the_scanned_root(tmp_path: Path) -> None:
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    outside = tmp_path / "outside"
    ops.init(outside)

    httpd = webui.make_picker_server(root_dir, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        status, payload = _post(f"{base}/api/open", {"path": str(outside)})
        assert status == 403
        assert "outside" in payload["error"]

        # A traversal spelled relative to the root is refused the same way.
        status, payload = _post(f"{base}/api/open", {"path": "../outside"})
        assert status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_open_refuses_an_old_schema_project_rather_than_migrating_it(
    project: Path, picker_server: str
) -> None:
    old = project.parent / "old-schema-2"
    _write_manifest(old, json.dumps({"schema_version": 2, "clips": []}))

    status, payload = _post(f"{picker_server}/api/open", {"path": str(old)})
    assert status == 400
    assert "schema_version" in payload["error"]
    # Never migrated as a side effect of merely attempting to open it.
    assert json.loads((old / "proofcut.json").read_text())["schema_version"] == 2


def test_open_requires_loopback_and_json_like_every_other_mutation(
    picker_server: str,
) -> None:
    request = urllib.request.Request(
        f"{picker_server}/api/open",
        data=b'{"path": "x"}',
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403

    status, payload = _post(f"{picker_server}/api/open", {"path": "x"}, content_type="text/plain")
    assert status == 400
    assert "application/json" in payload["error"]


def test_a_plain_single_project_server_has_no_picker_routes(server: str) -> None:
    """The default `-C` path is unchanged: no `/api/open`, no picker page,
    every existing endpoint answers exactly as it always has."""
    status, _ = _post(f"{server}/api/open", {"path": "whatever"})
    assert status == 404  # not a route on a plain server

    status, _, body = _get(f"{server}/")
    assert status == 200
    assert b'id="workspace"' in body
    assert b'id="list"' not in body

    status, view = _json(f"{server}/api/view")
    assert status == 200
    assert view["clip_id"] == "vo"


# -- session state and the poster (Studio Step 04 §§ B, C) -----------------
#
# `cache/session.json` is cache, never manifest: disposable, no schema
# version, and deliberately invisible to `_revision` — the `_agent_thumb`
# precedent (CLAUDE.md, webui.py's own `_session_set` docstring). The
# poster route is picker-only, modeled on `_send_reframe_tile`'s own
# confinement (CLAUDE.md names it as the model to copy).


def test_session_round_trips(server: str) -> None:
    payload = {
        "playhead": 12.34,
        "zoom": 2.5,
        "scroll_left": 340,
        "pane_expand": "inspector",
        "mode": "frame",
        "selection": {"clip_id": "vo", "indices": [12, 13, 14]},
    }
    status, posted = _post(f"{server}/api/session", payload)
    assert status == 200
    assert posted["saved"] is True

    status, got = _json(f"{server}/api/session")
    assert status == 200
    assert got == payload


def test_session_get_is_empty_before_anything_is_saved(server: str) -> None:
    status, payload = _json(f"{server}/api/session")
    assert status == 200
    assert payload == {}


def test_session_get_recovers_from_a_corrupt_file_rather_than_failing_the_page(
    project: Path, server: str
) -> None:
    session_path = Project.open(project).session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{not json", encoding="utf-8")

    status, payload = _json(f"{server}/api/session")
    assert status == 200
    assert payload == {}


def test_session_post_does_not_bump_the_revision_or_fire_project_changed(
    project: Path, server: str
) -> None:
    """The load-bearing guarantee: a session write must be mechanically
    invisible to `_revision` (webui.py), which stats only `project.otio`,
    the manifest, and the snapshot count — none of which `_session_set`
    touches."""
    before = webui._revision(project)

    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=1.2)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        events = _sse_events(resp)
        next(events)  # the initial project-changed

        status, _ = _post(
            f"{server}/api/session",
            {"playhead": 1.0, "zoom": 1.0, "mode": "edit"},
        )
        assert status == 200

        # A poll cycle or two (`_REVISION_POLL_SECONDS` is 0.5) should pass
        # with nothing arriving — a session write fires no `project-changed`.
        with pytest.raises(TimeoutError):
            next(events)
    finally:
        conn.close()

    after = webui._revision(project)
    assert after == before


def test_session_post_requires_json_content_type(server: str) -> None:
    status, payload = _post(
        f"{server}/api/session", {"playhead": 1.0}, content_type="text/plain"
    )
    assert status == 400
    assert "application/json" in payload["error"]


def test_session_before_open_404s_under_picker_root(picker_server: str) -> None:
    """The same bind-order rule every other route already follows: neither
    verb of `/api/session` is reachable before `POST /api/open` binds a
    project — GET falls through `_route_picker`'s allowlist, POST falls
    through `do_POST`'s `_project_bound()` check."""
    status, payload = _json(f"{picker_server}/api/session")
    assert status == 404
    assert "no project open" in payload["error"]

    status, payload = _post(f"{picker_server}/api/session", {"playhead": 1.0})
    assert status == 404
    assert "no project open" in payload["error"]


def test_session_rejects_a_bad_host(server: str) -> None:
    request = urllib.request.Request(
        f"{server}/api/session",
        data=b'{"playhead": 1.0}',
        headers={"Content-Type": "application/json", "Host": "evil.example.com"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


def test_poster_route_refuses_a_path_outside_the_scanned_root(
    project: Path, picker_server: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("poster-outside") / "proj"
    ops.init(outside)
    poster = Project.open(outside).poster_path
    poster.parent.mkdir(parents=True, exist_ok=True)
    poster.write_bytes(b"\xff\xd8\xff fake jpeg")

    status, payload = _json(f"{picker_server}/api/poster?path={outside}")
    assert status == 400
    assert "not a project under" in payload["error"]


def test_poster_route_refuses_a_symlink_planted_inside_the_cache_dir(
    project: Path, picker_server: str
) -> None:
    """The layer matching against the scan alone cannot catch: the project
    directory itself is real and unsymlinked (so the scan reports it
    normally), but `cache/poster.jpg` inside it is a symlink pointing
    outside — the exact `_send_reframe_tile` third-layer precedent."""
    real_project = Project.open(project)
    secret = project.parent / "poster-secret.jpg"
    secret.write_bytes(b"\xff\xd8\xff not yours")
    real_project.poster_path.parent.mkdir(parents=True, exist_ok=True)
    real_project.poster_path.symlink_to(secret)

    status, payload = _json(f"{picker_server}/api/poster?path={project}")
    assert status == 400
    assert "poster" in payload["error"]


def test_poster_route_refuses_a_symlinked_project_directory(
    project: Path, picker_server: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A project directory reached only via a symlink under `--root` never
    appears in the scan at all (`scan_projects`'s own `is_symlink()`
    filter) — so a poster request naming it has no matching entry and 400s
    the same way an unrelated outside path does, never a served file."""
    outside = tmp_path_factory.mktemp("poster-link-target") / "proj"
    ops.init(outside)
    poster = Project.open(outside).poster_path
    poster.parent.mkdir(parents=True, exist_ok=True)
    poster.write_bytes(b"\xff\xd8\xff fake jpeg")
    link = project.parent / "poster-escape-link"
    link.symlink_to(outside)

    status, payload = _json(f"{picker_server}/api/poster?path={link}")
    assert status == 400
    assert "not a project under" in payload["error"]


def test_poster_route_serves_a_real_poster(project: Path, picker_server: str) -> None:
    poster = Project.open(project).poster_path
    poster.parent.mkdir(parents=True, exist_ok=True)
    jpeg_bytes = b"\xff\xd8\xff fake jpeg bytes"
    poster.write_bytes(jpeg_bytes)

    status, headers, body = _get(f"{picker_server}/api/poster?path={project}")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert body == jpeg_bytes


def test_poster_route_reports_no_poster_yet_rather_than_a_500(
    project: Path, picker_server: str
) -> None:
    status, payload = _json(f"{picker_server}/api/poster?path={project}")
    assert status == 400
    assert "no poster" in payload["error"]


def test_scan_reports_a_resume_line_only_when_a_session_file_exists(
    project: Path, picker_server: str
) -> None:
    """`_scan_one`'s best-effort `session` field (Studio Step 04 § D) rides
    the existing scan — no second fetch, no binding."""
    _, payload = _json(f"{picker_server}/api/projects")
    entries = {entry["path"]: entry for entry in payload["projects"]}
    assert "session" not in entries[str(project)]

    session_path = Project.open(project).session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps({"mode": "frame", "playhead": 3.0}), encoding="utf-8")

    _, payload = _json(f"{picker_server}/api/projects")
    entries = {entry["path"]: entry for entry in payload["projects"]}
    assert entries[str(project)]["session"] == {"mode": "frame", "playhead": 3.0}


def test_broken_project_with_a_stale_session_file_still_does_not_take_down_the_listing(
    project: Path, picker_server: str
) -> None:
    """Extends the existing one-bad-project resiliency test
    (`test_an_unseeded_project_is_listed_as_ok_and_unseeded`) to the new
    resume-line read: an un-seeded project can carry a `session.json` of its
    own — the scan must still list it (`ok`/`seeded: false`, with the
    session field alongside) and must still list every healthy project next
    to it."""
    unseeded = project.parent / "unseeded-with-session"
    ops.init(unseeded)
    session_path = Project.open(unseeded).session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps({"mode": "edit"}), encoding="utf-8")

    status, payload = _json(f"{picker_server}/api/projects")
    assert status == 200
    entries = {entry["path"]: entry for entry in payload["projects"]}
    assert entries[str(unseeded)]["status"] == "ok"
    assert entries[str(unseeded)]["seeded"] is False
    assert entries[str(unseeded)]["session"] == {"mode": "edit"}
    assert entries[str(project)]["status"] == "ok"


# -- the F1 guard: a dropped CSS rule, caught with no browser ----------------
#
# F1 was a `*/` closing #picture's own comment early, so CSS error recovery
# absorbed the following prose AND the `#picture` selector into one invalid
# rule and dropped the real declaration block whole — `getComputedStyle` came
# back `position: static` for an element the file plainly still has a rule
# for, and 116 existing tests noticed nothing, because none of them fetch
# app.css and look. These two guards do, without a browser: one structural
# (comments/braces balance), one selector-resolution (a named, load-bearing
# set of selectors each has to survive as its OWN clean selector, the way
# `cssRules.some(r => r.selectorText === "#picture")` asked it live).


def _stripped_css(css: str) -> str:
    """Comments removed the way a CSS tokenizer removes them: a comment
    token ends at the FIRST `*/` it finds, so this non-greedy regex is not a
    shortcut — it is the real rule, which is what makes it a faithful model
    of F1's failure rather than a looser approximation of it. An unterminated
    or early-closed comment leaves a stray `/*` or `*/` sitting in the
    output rather than being silently absorbed, and the assertions below
    read exactly that.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _rule_selectors(stripped_css: str) -> set[str]:
    """Every ruleset's own selector list in already-comment-stripped CSS,
    split into individual trimmed selectors — the text-level analogue of
    `[...document.styleSheets[0].cssRules]`.

    A stack-based brace walk, not a single regex: a selector has to be
    isolated to the text between its OWN opening `{` and the previous brace
    event at the same nesting depth, so a rule nested inside `@media` gets
    its own prelude rather than merging into the media query's. This is
    deliberately cruder than a real CSS parser — it does not validate
    selector *syntax* — but it draws the same boundary a browser's tokenizer
    draws around one prelude, which is exactly the boundary F1's bug
    crossed: the orphaned `#picture` selector was glued onto the end of a
    runaway prelude ("It fills #frame and centres nothing: ... #picture")
    and, measured against the real file, that walk does NOT put a clean
    "#picture" back into the returned set — a plain substring grep for
    "#picture {" would have missed this, since that literal text is still
    sitting in the file even though the browser never sees it as a rule.
    """
    preludes: list[str] = []
    stack: list[list[str]] = [[]]
    for ch in stripped_css:
        if ch == "{":
            preludes.append("".join(stack[-1]))
            stack[-1] = []
            stack.append([])
        elif ch == "}":
            if len(stack) > 1:
                stack.pop()
            stack[-1] = []
        else:
            stack[-1].append(ch)

    selectors: set[str] = set()
    for prelude in preludes:
        for piece in prelude.split(","):
            normalized = " ".join(piece.split())
            if normalized:
                selectors.add(normalized)
    return selectors


def test_app_css_has_no_orphaned_comment_and_balanced_braces(server: str) -> None:
    """F1's structural signature, read off the served file. The bug was one
    comment opener followed by TWO closers (the true close, plus two lines
    of prose ending in a second `*/`) — an imbalance a browser's CSS
    tokenizer does not recover from gracefully, it just quietly drops a
    rule. Verified against the file BEFORE the fix landed: app.css then read
    73 `/*` against 74 `*/`, exactly this bug — and against the same file
    after, where both counts agree at 87. This is the regression guard that
    keeps it that way.
    """
    _, _, body = _get(f"{server}/static/app.css")
    css = body.decode("utf-8")

    assert css.count("/*") == css.count("*/"), (
        "unbalanced CSS comment markers — a comment closed early or never "
        "closed, which is the shape that let error recovery eat a real rule "
        "(F1: #picture)"
    )

    stripped = _stripped_css(css)
    assert "*/" not in stripped, "a comment terminator survived comment-stripping"
    assert "/*" not in stripped, "a comment opener survived comment-stripping"
    assert stripped.count("{") == stripped.count("}"), (
        "braces do not balance outside comments"
    )


def test_app_css_load_bearing_selectors_resolve_to_a_rule(server: str) -> None:
    """The selector-resolution half of the F1 guard. Each entry below is
    depended on by name from JS or is the rule a CLAUDE.md-documented
    invariant leans on — losing any of them the way #picture was lost is a
    silent regression a real browser would show and this suite would not
    catch any other way. Kept as an explicit, commented list on purpose, so
    it reads as a contract rather than a lint:
    """
    _, _, body = _get(f"{server}/static/app.css")
    selectors = _rule_selectors(_stripped_css(body.decode("utf-8")))

    load_bearing = {
        # F1 itself — the picture layer's own black ground; without it a
        # letterboxed V2 shot shows whatever #frame's other children are
        # through the bars instead of black (app.css's own comment on it).
        "#picture": "the exact rule F1 dropped",
        # The project canvas every layer draws inside and is placed against,
        # never fitted to (CLAUDE.md: "media is placed in it, never fitted").
        "#frame": "the canvas #picture/#caption-layer/player.js's place() all draw against",
        # The preview pane's transport container — CLAUDE.md/HISTORY.md: never
        # display:none, because the level display has to draw from it.
        "#viewer": "the transport container #frame lives inside",
        # The burn-in preview overlay's own geometry (position/inset/overflow).
        "#caption-layer": "the caption preview layer's own positioning",
        # The grid whose column tracks F2/F3 resize — losing this collapses
        # the whole four-pane layout to plain block flow.
        "#workspace": "the pane grid F2/F3's tokens tune",
        # The one channel errors arrive on (F6) — an aria-live region that
        # never lays out correctly is not actually announcing anything useful.
        "#toast": "the sole error channel; F9's data-severity styling keys off it",
        # The shared pane-head/pane idiom all three panes reuse.
        ".pane": "the flex-column/scroll/border idiom shared by every pane",
        # The rail's tab panels — losing this rule does not hide anything,
        # it draws all three panels on top of each other, because the
        # companion `[hidden]` rule below has nothing to switch off.
        ".rail-panel": "the agent/assets/properties tab panels' own box",
        # A timeline lane's own row (CLAUDE.md: "never draw a lane export
        # cannot produce") — this is the rule that makes a lane look like one.
        ".lane": "one lane's row; timeline.js draws several of these per project",
        # One DOM block per timeline segment — timeline.js's most-rebuilt node.
        ".clip-block": "one block per timeline segment",
    }
    missing = {sel for sel in load_bearing if sel not in selectors}
    assert not missing, {sel: load_bearing[sel] for sel in missing}


def test_app_css_declares_the_pane_layout_tokens_and_the_rail_breakpoints(
    server: str,
) -> None:
    """F2/F3/F9's own literal contract text, pinned as regression guards
    against the served file rather than the source tree, matching this
    file's own idiom for the vendored-fonts check above. `--pane-preview-min:
    26rem` is F3's real-weight fix (the picture pane must not be the smallest
    thing on screen); `--rail-w: 46px` and both breakpoints are F2's collapsed
    rail; `#toast[hidden]{display:none}` is the required override noted in
    THE CONTRACT — without it the base `#toast{display:flex}` id rule
    outranks the browser's own `[hidden]` UA rule and the toast never hides.
    """
    _, _, body = _get(f"{server}/static/app.css")
    css = body.decode("utf-8")

    assert "--pane-preview-min: 26rem" in css
    assert "--rail-w: 46px" in css
    # Two breakpoints became one on 2026-08-24, and again the pair was not a
    # threshold — it was one collapse width per collapsible pane (1200px for
    # the inspector, 980px for the agent). With three tracks the grid demands
    # 15rem + 26rem + 17rem = 928px, so 1200px would collapse a rail on a
    # window with 272px to spare. 980px survives as the collapse width and
    # 928px is the second breakpoint the merged layout does need: the state
    # where the rail is EXPANDED under its own collapse width, which is the
    # state F2 originally shipped without measuring.
    assert "@media (max-width: 980px)" in css
    assert "@media (max-width: 928px)" in css
    # The same class of required override as #toast[hidden] below, and the
    # same trap: `.rail-panel` carries an author `display: flex`, which
    # outranks the browser's own `[hidden]` UA rule — so without this the
    # `hidden` app.js sets on two of the three panels does nothing at all and
    # all three draw stacked. It has cost this file both toolbars, the pad
    # popover and #picture already.
    assert re.search(r"\.rail-panel\[hidden\]\s*\{[^}]*display:\s*none", css), (
        ".rail-panel[hidden] must set display:none explicitly, or the base "
        ".rail-panel{display:flex} rule outranks the browser's own [hidden] "
        "UA rule and every tab panel draws at once"
    )
    assert re.search(r"#toast\[hidden\]\s*\{[^}]*display:\s*none", css), (
        "#toast[hidden] must set display:none explicitly, or the base "
        "#toast{display:flex} id rule (specificity 100) outranks the "
        "browser's own [hidden] UA rule (specificity 10) and the toast "
        "never actually hides"
    )


def test_index_html_carries_the_landmark_and_toast_aria_contract(server: str) -> None:
    """F6's HTTP-checkable half: the landmark, the one live region errors
    arrive on, and the aria-labels on the three controls the finding named
    by hand (`#play` had none, `#zoom` had none, `#clip` had none — while
    `#theme` already carried one, so the pattern was known and just not
    applied). A browser pass is still required for the keyboard-navigation
    half of F6/F7 (tabindex on words, arrow-key routing) — this only pins
    what a static fetch can see.
    """
    _, _, body = _get(f"{server}/")
    page = body.decode("utf-8")

    assert '<main id="workspace"' in page
    assert 'role="status"' in page
    assert 'aria-live="polite"' in page
    assert 'aria-label="play or pause the timeline"' in page
    assert 'aria-label="timeline zoom"' in page
    assert 'aria-label="active clip"' in page


def test_index_html_carries_the_new_contract_ids(server: str) -> None:
    """The remaining static markup THE CONTRACT names by id: F8's render
    chip, F7's shortcut sheet, F3's canvas-dimensions note, and F2's
    collapsed-pane rail (one per collapsible pane, so at least two)."""
    _, _, body = _get(f"{server}/")
    page = body.decode("utf-8")

    assert 'id="export-status"' in page
    assert 'id="shortcuts-sheet"' in page
    assert 'id="preview-canvas-note"' in page
    # This read `>= 2` until 2026-08-24, and the number was not a threshold —
    # it was "one rail per collapsible pane, and there are two panes". The
    # agent column and the assets/properties column became ONE pane with three
    # tab panels, so there is one collapsible pane and one rail. The assertion
    # is updated rather than deleted because what it was really pinning is
    # that the collapsed-pane affordance still EXISTS: F2's finding was a pane
    # drawing entirely past the right edge of an `overflow: hidden` body, with
    # no scrollbar and nothing in the console, and the rail is the only way
    # back to it.
    assert page.count("pane-rail") >= 1
    # The merge's own contract, so losing a panel is not silent. Each tab has
    # a panel and each panel is named by its tab; app.js's setRailTab moves
    # `hidden` between exactly these three.
    for which in ("agent", "assets", "properties"):
        assert f'id="rail-tab-{which}"' in page
        assert f'id="rail-{which}"' in page
    assert page.count('role="tab"') == 3
    # Exactly one selected in the static markup — the state app.js starts
    # from. Two would draw two panels at once on the first paint.
    assert page.count('aria-selected="true"') == 1


def test_dom_js_exports_the_shared_clamp_helper(server: str) -> None:
    """Pins the exact helper name THE CONTRACT gives F4's fix: one clamp,
    shared by transcript.js's selection toolbar and timeline.js's cue
    toolbar, rather than a second hand-rolled copy — which is how the first
    clamp fix (timeline.js's) failed to reach the sibling toolbar the first
    time this bug was found."""
    _, _, body = _get(f"{server}/static/dom.js")
    assert b"export function clampFloating" in body


def test_app_js_wires_the_new_bus_events_and_the_toast_dismiss(server: str) -> None:
    """F7's undo/help keyboard shortcuts and F9's manual dismiss button, all
    of which have to be wired in app.js for player.js's key handler and
    index.html's static markup to do anything at all."""
    _, _, body = _get(f"{server}/static/app.js")
    assert b"shortcut-undo" in body
    assert b"shortcut-help" in body
    assert b"toast-dismiss" in body


def test_a_load_failure_toast_and_a_planned_cut_are_retired_by_what_supersedes_them(server: str) -> None:
    """Two banners that outlived the state they reported, both measured on
    the first recorded agent run (docs/plans/LAUNCH.md § Step 1): the fresh
    project's "no timeline yet" error toast — timerless by design — sat over
    the CC lane for three minutes after the agent had seeded the timeline,
    and the transcript's Apply/Dismiss banner went on proposing a 13-word cut
    the agent had already applied. `load()` clears its own failure toast on
    the next success; an applied cut emits a null `agent-plan`, which is the
    banner's own clear path. The repo has no JS harness, so this holds the
    two source-level facts the way the toast-dismiss test above does."""
    _, _, app = _get(f"{server}/static/app.js")
    assert b"load.failed = true" in app and b"load.failed = false" in app
    # A fresh project's "no timeline yet" is not a failure: it draws an empty
    # state and raises no toast; a real load failure stays red until a load
    # succeeds.
    assert b"/no timeline yet/.test(err.message)" in app
    # And the pane balancer measures in layout pixels only: a bounding rect is
    # in the transformed frame, and the two disagree under any ancestor transform.
    _, _, player = _get(f"{server}/static/player.js")
    assert b"const workspaceH = workspace.offsetHeight;" in player
    assert b"workspace.getBoundingClientRect().height" not in player
    fresh_branch = app[app.index(b"/no timeline yet/.test(err.message)") :]
    fresh_branch = fresh_branch[: fresh_branch.index(b"return;")]
    assert b"toast(" not in fresh_branch
    assert b'toast({ message: err.message, severity: "error" });' in app
    # A fresh project draws an empty state and still fills the assets pane,
    # which needs no timeline — rather than panes frozen on "Loading…".
    assert b"transcript.unseeded();" in app
    assert b"assets.update(null);" in app
    _, _, transcript_js = _get(f"{server}/static/transcript.js")
    assert b"export function unseeded()" in transcript_js
    _, _, agent = _get(f"{server}/static/agent.js")
    assert b'ctx.emit("agent-plan", null)' in agent


def test_the_feed_follows_its_checklist_and_draws_a_tool_results_image(server: str) -> None:
    """Two more from the same recorded run: the checklist grew forty steps
    below the fold while the pane showed its first three, and the shot sheet
    the agent reviewed its own picture on — an image block in the tool
    result — was skipped as not part of the progress story. `addStep`
    scrolls; `resultImages` draws each base64 image under its step, and the
    stylesheet caps it at the pane's width."""
    _, _, agent = _get(f"{server}/static/agent.js")
    assert b"function resultImages" in agent
    assert b"agent-entry--image" in agent
    _, _, css = _get(f"{server}/static/app.css")
    assert re.search(rb"\.agent-entry--image img\s*\{[^}]*max-width:\s*100%", css)
    # The image is a data URL, and `default-src 'self'` alone refuses one:
    # every returned sheet drew as a broken-image icon in the third recorded
    # run, with nothing in the console to say why.
    _, headers, _ = _get(f"{server}/")
    assert "img-src 'self' data:" in headers["Content-Security-Policy"]


# -- Studio Step 03: the Frame view's backend ----------------------------
#
# `GET /api/reframe/coverage` (synchronous, cheap), `POST /api/reframe/sheet`
# and `POST /api/reframe/detect` (jobs, `ReframeSheetJob`/`ReframeDetectJob`),
# `GET /api/reframe/tile/<name>` (the confined cache-file route) and
# `POST /api/reframe` (the write). CLAUDE.md's vocabulary applies throughout:
# a sheet row is a window, `apply` never reaches `reframe_detect` from here.


def test_reframe_coverage_returns_ops_own_payload_and_forwards_query_params(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def _stub(path: str, *, clip_id: str | None = None, threshold: float = ops.SCENE_THRESHOLD) -> dict[str, Any]:
        seen["clip_id"] = clip_id
        seen["threshold"] = threshold
        return {
            "project": path,
            "canvas": "1920x1080",
            "threshold": threshold,
            "same_window_within": 0.04,
            "placements": 1,
            "placed_seconds": 5.0,
            "cuts": 0,
            "cuts_framed": 0,
            "cuts_unframed": 0,
            "stretches": [],
            "steps_seen": 0,
            "steps_cut": 0,
            "steps": [],
            "stale_seconds": 0.0,
            "stale_share": 0.0,
            "stale_stretches": 0,
            "default_seconds": 5.0,
            "skipped": [],
        }

    monkeypatch.setattr(ops, "reframe_coverage", _stub)

    status, payload = _json(f"{server}/api/reframe/coverage?clip_id=vo&threshold=0.2")
    assert status == 200
    assert seen == {"clip_id": "vo", "threshold": 0.2}
    assert payload["canvas"] == "1920x1080"
    assert payload["stale_seconds"] == 0.0

    # No query params at all: threshold defaults to ops.SCENE_THRESHOLD, not
    # None passed straight through to the op (which requires 0 < threshold <= 1).
    status, _ = _json(f"{server}/api/reframe/coverage")
    assert status == 200
    assert seen["clip_id"] is None
    assert seen["threshold"] == ops.SCENE_THRESHOLD


def test_reframe_coverage_404s_under_picker_root_before_a_project_is_open(
    project: Path, picker_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ops,
        "reframe_coverage",
        lambda path, *, clip_id=None, threshold=ops.SCENE_THRESHOLD: {"ok": True},
    )
    status, payload = _json(f"{picker_server}/api/reframe/coverage")
    assert status == 404
    assert "no project open" in payload["error"]

    status, _ = _post(f"{picker_server}/api/open", {"path": str(project)})
    assert status == 200

    status, payload = _json(f"{picker_server}/api/reframe/coverage")
    assert status == 200
    assert payload["ok"] is True


def test_reframe_writes_and_the_change_is_visible_on_read_back(
    video_clip: str, server: str
) -> None:
    """`_reframe` (webui.py) is a thin dispatch onto `ops.reframe` — the
    write lands in the manifest, and a second call with no `rect` (a pure
    read, `write=False`) reports the same crop back."""
    status, first = _post(f"{server}/api/reframe", {"clip_id": video_clip, "rect": "0,0,160,120"})
    assert status == 200
    assert first["written"] is True
    entry = next(c for c in first["clips"] if c["clip_id"] == video_clip)
    assert entry["origin"] == "override"
    assert entry["reframes"] is True
    assert entry["crop"] is not None

    status, second = _post(f"{server}/api/reframe", {"clip_id": video_clip})
    assert status == 200
    assert second["written"] is False
    entry2 = next(c for c in second["clips"] if c["clip_id"] == video_clip)
    assert entry2["origin"] == "override"
    assert entry2["crop"] == entry["crop"]


def test_reframe_requires_clip_id(server: str) -> None:
    status, payload = _post(f"{server}/api/reframe", {"rect": "0,0,160,120"})
    assert status == 400
    assert "clip_id" in payload["error"]


def test_reframe_pane_without_rect_surfaces_the_ops_refusal_as_a_400(
    video_clip: str, server: str
) -> None:
    """`_reframe` does not re-check the rect/pane combination itself —
    `ops.reframe`'s own refusal is what must surface here."""
    status, payload = _post(
        f"{server}/api/reframe", {"clip_id": video_clip, "pane": "0,0,10,10"}
    )
    assert status == 400
    assert "pane" in payload["error"]


def test_reframe_endpoint_requires_json_content_type(video_clip: str, server: str) -> None:
    status, payload = _post(
        f"{server}/api/reframe",
        {"clip_id": video_clip, "rect": "0,0,160,120"},
        content_type="text/plain",
    )
    assert status == 400
    assert "application/json" in payload["error"]


# -- the sheet and detect jobs --------------------------------------------


def _reframe_sheet_stub_result(path: str) -> dict[str, Any]:
    return {
        "project": path,
        "canvas": "1920x1080",
        "sheet": f"{path}/cache/sheets/sheet.png",
        "rows": [],
        "count": 0,
        "placements": 0,
        "extremes": False,
        "moments": list(ops.SHEET_MOMENTS),
        "probed": 0,
        "skipped": [],
    }


def _next_topic_event(
    events: Iterator[tuple[str, Any]], topic: str, job_id: str
) -> dict[str, Any]:
    """The first non-`running` event for `job_id` on `topic` off an open stream."""
    for event, data in events:
        if event == topic and data.get("job_id") == job_id and data.get("status") != "running":
            return data
    raise AssertionError(f"no completion event arrived for {topic} {job_id}")


def test_reframe_sheet_accepted_returns_a_job_id_and_completes_on_the_stream(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ops, "reframe_sheet", lambda path, *, out=None, moments=None, extremes=False: _reframe_sheet_stub_result(path)
    )
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)  # the initial project-changed

        status, payload = _post(f"{server}/api/reframe/sheet", {})
        assert status == 202
        assert isinstance(payload["job_id"], str) and payload["job_id"]

        found = _next_topic_event(events, "reframe-sheet", payload["job_id"])
        assert found["status"] == "done"
        assert found["count"] == 0
    finally:
        conn.close()


def test_a_second_reframe_sheet_while_one_is_running_is_refused(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()

    def _stub(path: str, *, out: str | None = None, moments: Any = None, extremes: bool = False) -> dict[str, Any]:
        gate.wait(timeout=5)
        return _reframe_sheet_stub_result(path)

    monkeypatch.setattr(ops, "reframe_sheet", _stub)
    try:
        status, _ = _post(f"{server}/api/reframe/sheet", {})
        assert status == 202

        status, payload = _post(f"{server}/api/reframe/sheet", {})
        assert status == 409
        assert "already generating" in payload["error"]
    finally:
        gate.set()


def test_reframe_sheet_endpoint_requires_json_content_type(server: str) -> None:
    status, payload = _post(f"{server}/api/reframe/sheet", {}, content_type="text/plain")
    assert status == 400
    assert "application/json" in payload["error"]


def _reframe_detect_stub_result(path: str) -> dict[str, Any]:
    return {
        "project": path,
        "canvas": "1920x1080",
        "threshold": ops.SCENE_THRESHOLD,
        "frames_per_window": ops.DETECT_FRAMES,
        "same_window_within": 0.04,
        "detector": {"available": True},
        "windows": [],
        "count": 0,
        "proposed": 0,
        "refused": 0,
        "splits": 0,
        "placements": 0,
        "applied": False,
        "written": 0,
        "skipped": [],
    }


def test_reframe_detect_accepted_returns_a_job_id_and_completes_on_the_stream(
    server: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def _stub(
        path: str,
        *,
        clip_id: str | None = None,
        threshold: float = ops.SCENE_THRESHOLD,
        frames: int = ops.DETECT_FRAMES,
        apply: bool = False,
        split: bool = True,
    ) -> dict[str, Any]:
        seen["apply"] = apply
        return _reframe_detect_stub_result(path)

    monkeypatch.setattr(ops, "reframe_detect", _stub)
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)

        status, payload = _post(f"{server}/api/reframe/detect", {})
        assert status == 202
        assert isinstance(payload["job_id"], str) and payload["job_id"]

        found = _next_topic_event(events, "reframe-detect", payload["job_id"])
        assert found["status"] == "done"
        # The one flag docs/plans/STUDIO.md is explicit about: `apply` never reaches the
        # op as True from this route, no matter what — this call sent no body
        # key for it at all, and the job still hard-codes it.
        assert seen["apply"] is False
    finally:
        conn.close()


def test_a_second_reframe_detect_while_one_is_running_is_refused(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()

    def _stub(
        path: str,
        *,
        clip_id: str | None = None,
        threshold: float = ops.SCENE_THRESHOLD,
        frames: int = ops.DETECT_FRAMES,
        apply: bool = False,
        split: bool = True,
    ) -> dict[str, Any]:
        gate.wait(timeout=5)
        return _reframe_detect_stub_result(path)

    monkeypatch.setattr(ops, "reframe_detect", _stub)
    try:
        status, _ = _post(f"{server}/api/reframe/detect", {})
        assert status == 202

        status, payload = _post(f"{server}/api/reframe/detect", {})
        assert status == 409
        assert "already running" in payload["error"]
    finally:
        gate.set()


def test_reframe_detect_refuses_an_apply_key_before_starting_a_job(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt and suspenders: even though `ReframeDetectJob.start` has no
    `apply` parameter at all, the endpoint refuses a hand-crafted request
    naming the key before a job is ever started — a 400, not a 202 that
    silently drops it."""
    called = False

    def _stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return _reframe_detect_stub_result(str(args[0]))

    monkeypatch.setattr(ops, "reframe_detect", _stub)

    status, payload = _post(f"{server}/api/reframe/detect", {"apply": True})
    assert status == 400
    assert "apply" in payload["error"]
    assert called is False


def test_reframe_detect_endpoint_requires_json_content_type(server: str) -> None:
    status, payload = _post(f"{server}/api/reframe/detect", {}, content_type="text/plain")
    assert status == 400
    assert "application/json" in payload["error"]


def test_reframe_detect_missing_face_detector_reports_as_an_error_event_and_frees_the_slot(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure docs/plans/STUDIO.md's contract names and forbids: `FaceError` must
    be in `webui.EXPECTED`, or this raises inside the worker thread with no
    handler — no error event, `_finish()` never runs, the slot latches busy
    forever. Pinned here rather than trusted from the source read."""
    message = (
        "no interpreter with a face detector. Looked at $PROOFCUT_FACE (unset), "
        "then a sibling venv. Set PROOFCUT_FACE to the python in a venv that has "
        "insightface and onnxruntime."
    )

    def _stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise FaceError(message)

    monkeypatch.setattr(ops, "reframe_detect", _stub)
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        events = _sse_events(conn.getresponse())
        next(events)

        status, payload = _post(f"{server}/api/reframe/detect", {})
        assert status == 202
        found = _next_topic_event(events, "reframe-detect", payload["job_id"])
        assert found["status"] == "error"
        assert found["error"] == message
    finally:
        conn.close()

    # The slot freed — a second call succeeds rather than 409ing forever.
    status, _ = _post(f"{server}/api/reframe/detect", {})
    assert status == 202


def test_reframe_job_starts_404_under_picker_root_before_a_project_is_open(
    project: Path, picker_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ops, "reframe_sheet", lambda path, *, out=None, moments=None, extremes=False: _reframe_sheet_stub_result(path)
    )
    status, payload = _post(f"{picker_server}/api/reframe/sheet", {})
    assert status == 404
    assert "no project open" in payload["error"]

    status, _ = _post(f"{picker_server}/api/open", {"path": str(project)})
    assert status == 200

    status, payload = _post(f"{picker_server}/api/reframe/sheet", {})
    assert status == 202


def test_reframe_endpoints_reject_a_bad_host(server: str) -> None:
    for path in ("/api/reframe", "/api/reframe/sheet", "/api/reframe/detect"):
        request = urllib.request.Request(
            f"{server}{path}",
            data=b"{}",
            headers={"Content-Type": "application/json", "Host": "evil.example.com"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code == 403, path

    status, _ = _json(f"{server}/api/reframe/coverage", headers={"Host": "evil.example.com"})
    assert status == 403


# -- the tile route's confinement -----------------------------------------
#
# `GET /api/reframe/tile/<name>` is the security-relevant route in the
# contract: `name` must never escape `project.sheet_dir`, whether by a `..`
# traversal, an absolute path, or a symlink placed inside the cache dir
# pointing outside it.


# -- /api/output: the file the last run made ---------------------------------
#
# The window plays the project, never `renders/` — which is what left Export
# writing something the page could not open (PLAN.md § Should the workspace
# play its own output?). These cover the narrow route that answers it: the
# render log's own last output, and nothing the client gets to name.


def _write_render_log(project: Path, output: Path, **extra: Any) -> None:
    log = Project.open(project).renders_log_path
    log.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": "2026-08-18T00:00:00+00:00",
        "output": str(output),
        "preset": None,
        "expected_duration": 1.0,
        "stages": {"export": {"outcome": "done", "detail": None}},
    }
    record.update(extra)
    log.write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_output_says_so_when_nothing_has_been_rendered(server: str) -> None:
    status, payload = _json(f"{server}/api/output")
    assert status == 400
    assert "rendered" in payload["error"]


def test_output_streams_the_last_render_with_range(project: Path, server: str) -> None:
    render = Project.open(project).render_dir
    render.mkdir(parents=True, exist_ok=True)
    made = render / "final.mp4"
    made.write_bytes(b"not really an mp4, but bytes are bytes")
    _write_render_log(project, made)

    status, headers, body = _get(f"{server}/api/output")
    assert status == 200
    assert body == made.read_bytes()
    assert headers["Accept-Ranges"] == "bytes"

    status, headers, body = _get(f"{server}/api/output", headers={"Range": "bytes=0-3"})
    assert status == 206
    assert body == b"not "
    assert headers["Content-Range"].endswith(f"/{made.stat().st_size}")


def test_output_refuses_a_log_line_naming_a_file_outside_the_project(
    project: Path, server: str
) -> None:
    """proofcut writes this log itself, which is exactly the argument for not
    trusting it: one hand-edited line should not turn a loopback server into
    a file server for the whole disk.
    """
    outside = project.parent / "secret.txt"
    outside.write_text("not part of any project", encoding="utf-8")
    _write_render_log(project, outside)

    status, payload = _json(f"{server}/api/output")
    assert status == 400
    assert "inside this project" in payload["error"]


def test_output_refuses_a_render_deleted_after_its_run(project: Path, server: str) -> None:
    render = Project.open(project).render_dir
    render.mkdir(parents=True, exist_ok=True)
    gone = render / "deleted.mp4"
    _write_render_log(project, gone)

    status, payload = _json(f"{server}/api/output")
    assert status == 400
    assert "no longer on disk" in payload["error"]


def test_finish_report_names_the_last_render_for_the_page(project: Path, server: str) -> None:
    render = Project.open(project).render_dir
    render.mkdir(parents=True, exist_ok=True)
    made = render / "final.mp4"
    made.write_bytes(b"bytes")
    _write_render_log(project, made)

    status, payload = _json(f"{server}/api/finish")
    assert status == 200
    assert payload["last_render"]["name"] == "final.mp4"
    assert payload["last_render"]["exists"] is True


def test_reframe_tile_serves_a_real_tile(project: Path, server: str) -> None:
    sheet_dir = Project.open(project).sheet_dir
    sheet_dir.mkdir(parents=True, exist_ok=True)
    tile = sheet_dir / "000-0-0.15.png"
    tile.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    status, headers, body = _get(f"{server}/api/reframe/tile/000-0-0.15.png")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body == tile.read_bytes()


def test_reframe_tile_refuses_an_empty_name(server: str) -> None:
    status, payload = _json(f"{server}/api/reframe/tile/")
    assert status == 400
    assert "required" in payload["error"]


def test_reframe_tile_refuses_a_traversal_name(project: Path, server: str) -> None:
    sheet_dir = Project.open(project).sheet_dir
    sheet_dir.mkdir(parents=True, exist_ok=True)
    secret = project.parent / "secret.txt"
    secret.write_text("outside the cache dir", encoding="utf-8")

    for traversal in ("..%2Fsecret.txt", "..%2F..%2Fsecret.txt", "..%2f..%2fetc%2fpasswd"):
        status, payload = _json(f"{server}/api/reframe/tile/{traversal}")
        assert status == 400, traversal
        assert "does not name a sheet tile" in payload["error"]

    # And nothing outside cache/sheets was ever read.
    assert secret.read_text(encoding="utf-8") == "outside the cache dir"


def test_reframe_tile_refuses_an_absolute_path(project: Path, server: str) -> None:
    secret = project.parent / "secret.txt"
    secret.write_text("outside the cache dir", encoding="utf-8")

    status, payload = _json(f"{server}/api/reframe/tile/%2Fetc%2Fpasswd")
    assert status == 400
    assert "does not name a sheet tile" in payload["error"]

    # An absolute path to a file that genuinely exists, just outside the
    # cache dir — `Path(name).name` strips it to a bare filename that fails
    # the `is_file()` check under `sheet_dir`, refused the same way.
    quoted = str(secret).replace("/", "%2F")
    status, payload = _json(f"{server}/api/reframe/tile/{quoted}")
    assert status == 400
    assert "does not name a sheet tile" in payload["error"]
    assert secret.read_text(encoding="utf-8") == "outside the cache dir"


def test_reframe_tile_refuses_a_symlink_escaping_the_cache_dir(
    project: Path, server: str
) -> None:
    """The layer the character/`Path.name` checks alone cannot catch: a bare
    filename with no `/` or `..` in it, sitting inside `cache/sheets`, whose
    target resolves outside it."""
    sheet_dir = Project.open(project).sheet_dir
    sheet_dir.mkdir(parents=True, exist_ok=True)
    secret = project.parent / "secret.txt"
    secret.write_text("outside the cache dir", encoding="utf-8")

    link = sheet_dir / "evil.png"
    link.symlink_to(secret)

    status, payload = _json(f"{server}/api/reframe/tile/evil.png")
    assert status == 400
    assert "does not name a sheet tile" in payload["error"]


def test_reframe_tile_refuses_an_unknown_name(project: Path, server: str) -> None:
    Project.open(project).sheet_dir.mkdir(parents=True, exist_ok=True)
    status, payload = _json(f"{server}/api/reframe/tile/nope.png")
    assert status == 400
    assert "no such tile" in payload["error"]


def test_finish_framing_is_opt_in_and_zero_is_not_none(server: str) -> None:
    """`?framing=1` measures; the bare route does not.

    The distinction the payload has to keep is "not measured" vs "measured
    and nothing stale" — a `None` that read as a zero would let the truth
    strip claim framing is clean on a project nobody ever scanned. This
    fixture is audio-only, so the measured answer really is all zeros, which
    is exactly the pair that would collapse if `framing` defaulted to `{}`.
    """
    _, unasked = _json(f"{server}/api/finish")
    assert unasked["framing"] is None

    _, asked = _json(f"{server}/api/finish?framing=1")
    assert asked["framing"] == {"stale_seconds": 0.0, "stale_stretches": 0, "steps": 0}
    assert [f for f in asked["flags"]["items"] if f["kind"] == "framing"] == []


# ---------------------------------------------------------------------------
# Off the machine: `--allow-remote`, and the token that replaces loopback+Host
# ---------------------------------------------------------------------------
#
# The default is the thing most worth pinning here — every test above this
# line is a test that the opt-in changed nothing — so these build a *second*
# server through the real `remote_policy` rather than hand-setting the
# handler's attributes, which would prove the guard runs and nothing about
# whether the policy that configures it agrees.

#: What `--tailscale` would have filled in: a 100.x bind address and the
#: MagicDNS name a phone typing the short name actually presents. Synthetic
#: values — no real node is named here, and nothing in `remote_policy` or
#: `tailscale_identity` reads these as anything but opaque host strings.
_TAILNET_HOST = "100.101.102.103"
_TAILNET_NAME = "proofcut-box.tailnet-example.ts.net"


@pytest.fixture
def remote(project: Path) -> Iterator[tuple[str, str]]:
    """A token-guarded server, configured exactly as `--allow-remote` would.

    Bound to loopback so the test can reach it, but carrying the allow-list
    and token `remote_policy` produces for a tailnet bind — the socket's own
    address is not what any of this guards on.
    """
    token, allowed = webui.remote_policy(
        host=_TAILNET_HOST, allow_remote=True, allow_remote_hosts=[_TAILNET_NAME + "."]
    )
    assert token is not None
    httpd = webui.make_server(project, port=0, token=token, allowed_hosts=allowed)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", token
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


def test_remote_policy_refuses_a_non_loopback_bind_by_default(tmp_path: Path) -> None:
    """The rule that has always held, now stated as a refusal rather than a doc.

    `webui.py` can rewrite a whole project, so binding it off loopback with
    nothing in place of loopback is the one shape this must never do quietly.
    """
    with pytest.raises(ProjectError) as exc:
        webui.remote_policy(host=_TAILNET_HOST)
    assert "allow_remote" in str(exc.value)


def test_remote_policy_leaves_the_default_untouched() -> None:
    assert webui.remote_policy(host="127.0.0.1") == (None, webui._LOOPBACK_NAMES)


def test_remote_policy_refuses_a_wildcard_bind_with_no_client_names() -> None:
    """`0.0.0.0` is a bind instruction, never a client identity.

    Adding it to the allow-list would admit an attacker echoing the startup
    banner while still refusing every real client, which arrives naming the
    address it dialed. `server.py` refuses the same shape for the same reason.
    """
    with pytest.raises(ProjectError) as exc:
        webui.remote_policy(host="0.0.0.0", allow_remote=True)
    assert "allow_remote_hosts" in str(exc.value)

    token, allowed = webui.remote_policy(
        host="0.0.0.0", allow_remote=True, allow_remote_hosts=[_TAILNET_HOST]
    )
    assert token
    assert "0.0.0.0" not in allowed
    assert _TAILNET_HOST in allowed


def test_remote_policy_widens_the_host_guard_and_mints_a_token() -> None:
    token, allowed = webui.remote_policy(
        host=_TAILNET_HOST, allow_remote=True, allow_remote_hosts=[_TAILNET_NAME + "."]
    )
    assert token and len(token) > 20
    # Loopback still answers — serving to the tailnet does not stop the
    # machine itself from being a client.
    assert webui._LOOPBACK_NAMES <= allowed
    assert _TAILNET_HOST in allowed
    # The root dot `tailscale status` reports is not what a browser sends.
    assert _TAILNET_NAME in allowed
    assert _TAILNET_NAME + "." not in allowed


def test_an_ipv6_client_name_is_stored_in_both_spellings() -> None:
    """A browser brackets an IPv6 literal in `Host:` and nothing else does.

    `tailscale status` reports `fd7a:…` bare, `--host` takes it bare, and the
    header arrives as `[fd7a:…]:8710`. Storing one spelling refuses every real
    v6 client while every test written against the bare form passes —
    `_LOOPBACK_NAMES` has carried both `::1` and `[::1]` from the start for
    this reason.
    """
    v6 = "fd7a:115c:a1e0::1"
    _token, allowed = webui.remote_policy(
        host=_TAILNET_HOST, allow_remote=True, allow_remote_hosts=[v6, _TAILNET_NAME]
    )
    assert v6 in allowed
    assert f"[{v6}]" in allowed
    # A hostname gets exactly one spelling — brackets are a v6 literal thing.
    assert f"[{_TAILNET_NAME}]" not in allowed


def test_an_ipv6_host_header_is_answered(project: Path) -> None:
    v6 = "fd7a:115c:a1e0::1"
    token, allowed = webui.remote_policy(
        host=_TAILNET_HOST, allow_remote=True, allow_remote_hosts=[v6]
    )
    httpd = webui.make_server(project, port=0, token=token, allowed_hosts=allowed)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        status, _payload = _json(
            f"{base}/api/view?t={token}", headers={"Host": f"[{v6}]:{httpd.server_address[1]}"}
        )
        assert status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


def test_remote_serving_refuses_a_request_with_no_token(remote: tuple[str, str]) -> None:
    server, _ = remote
    status, payload = _json(f"{server}/api/view")
    assert status == 403
    assert "token" in payload["error"]


def test_remote_serving_refuses_a_wrong_token(remote: tuple[str, str]) -> None:
    server, _ = remote
    status, _payload = _json(f"{server}/api/view?t=not-the-token")
    assert status == 403


def test_the_token_url_answers_and_hands_back_a_cookie(remote: tuple[str, str]) -> None:
    """The whole reason `web/` needed no change: `?t=` becomes a cookie.

    The page's own fetches, media ranges and `EventSource` know nothing about
    a token, so the credential has to travel the way the browser already
    sends things — and `SameSite=Strict` is what keeps any other origin from
    spending it.
    """
    server, token = remote
    status, headers, _body = _get(f"{server}/?t={token}")
    assert status == 200
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"{webui.TOKEN_COOKIE}={token};")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie


def test_the_cookie_alone_reaches_every_route(remote: tuple[str, str]) -> None:
    server, token = remote
    jar = {"Cookie": f"{webui.TOKEN_COOKIE}={token}"}
    for path in ("/api/view", "/api/assets", "/api/finish", "/static/app.css"):
        status, _headers, _body = _get(f"{server}{path}", headers=jar)
        assert status == 200, path


def test_a_mutation_needs_the_token_too(remote: tuple[str, str]) -> None:
    """The `application/json` rule is unchanged; the token is on top of it."""
    server, token = remote
    body = json.dumps({"spans": [[3.5, 4.5]]}).encode()
    headers = {"Content-Type": "application/json"}
    status, _payload = _json(f"{server}/api/cut-at", data=body, headers=headers, method="POST")
    assert status == 403

    status, _payload = _json(
        f"{server}/api/cut-at",
        data=body,
        headers={**headers, "Cookie": f"{webui.TOKEN_COOKIE}={token}"},
        method="POST",
    )
    assert status == 200


def test_a_disallowed_host_is_refused_even_with_a_good_token(remote: tuple[str, str]) -> None:
    """Host first, token second — a valid token must not buy a rebinding page in.

    The token is the credential a person copies to their phone; the Host
    allow-list is what keeps a page at some other name from using it if it
    ever leaks into one.
    """
    server, token = remote
    status, payload = _json(f"{server}/api/view?t={token}", headers={"Host": "evil.example.com"})
    assert status == 403
    assert "token" not in payload["error"]
    assert _TAILNET_NAME in payload["error"]


def test_the_tailnet_name_is_answered(remote: tuple[str, str]) -> None:
    """What a phone typing the MagicDNS name actually sends."""
    server, token = remote
    status, _payload = _json(f"{server}/api/view?t={token}", headers={"Host": _TAILNET_NAME})
    assert status == 200


def test_media_and_events_are_guarded_too(remote: tuple[str, str]) -> None:
    """Neither is routed through the JSON handlers, so neither is guarded by them."""
    server, _token = remote
    for path in ("/api/media/vo", "/api/events"):
        status, _headers, _body = _get(f"{server}{path}")
        assert status == 403, path


def test_the_default_server_still_asks_for_no_token(server: str) -> None:
    """The opt-in changed nothing for every existing caller.

    Stated as its own test rather than left to the rest of the file, because
    "the guard is off by default" is the property that would break silently.
    """
    status, _payload = _json(f"{server}/api/view")
    assert status == 200
    _status, headers, _body = _get(f"{server}/")
    assert "Set-Cookie" not in headers


def test_tailscale_identity_refuses_rather_than_falling_back(tmp_path: Path) -> None:
    """A `--tailscale` that quietly bound loopback would look like it worked."""
    down = write_stub(
        tmp_path / "tailscale-down", "print('{\"BackendState\":\"Stopped\",\"TailscaleIPs\":[]}')\n"
    )
    with pytest.raises(ProjectError) as exc:
        webui.tailscale_identity(str(down))
    assert "not up" in str(exc.value)

    with pytest.raises(ProjectError) as exc:
        webui.tailscale_identity(str(tmp_path / "no-such-binary"))
    assert "could not run" in str(exc.value)


def test_tailscale_identity_reads_the_bind_address_and_every_client_name(tmp_path: Path) -> None:
    """Two answers, not one: what to bind, and what a client may put in `Host:`."""
    payload = json.dumps(
        {
            "BackendState": "Running",
            "TailscaleIPs": [_TAILNET_HOST, "fd7a:115c:a1e0::1"],
            "Self": {"HostName": "proofcut-box", "DNSName": _TAILNET_NAME + "."},
        }
    )
    fake = write_stub(tmp_path / "tailscale", f"print({payload!r})\n")

    bind, names = webui.tailscale_identity(str(fake))
    # IPv4, because it is what a phone dials and what binds with no bracket
    # rules attached — the v6 address is a client name, not the bind address.
    assert bind == _TAILNET_HOST
    # The root dot `tailscale status` reports is stripped here as well as in
    # `remote_policy`: a browser never sends it, and a name that only matches
    # with it would refuse every real client.
    assert set(names) >= {_TAILNET_HOST, "fd7a:115c:a1e0::1", _TAILNET_NAME, "proofcut-box"}


def test_the_cookie_is_not_stamped_onto_a_later_request_on_the_same_connection(
    remote: tuple[str, str],
) -> None:
    """`protocol_version = "HTTP/1.1"` means one handler instance, many requests.

    A per-request flag living on the handler is per-*connection* unless it is
    reset, and the shape that hides it is exactly the shape a browser makes:
    the token URL first, everything else after, all down one socket.
    """
    server, token = remote
    host, port = _host_and_port(server)
    conn = http.client.HTTPConnection(host, port)
    try:
        conn.request("GET", f"/?t={token}")
        first = conn.getresponse()
        first.read()
        assert first.status == 200
        assert first.getheader("Set-Cookie") is not None

        # Same socket, same handler instance, no credential of its own.
        conn.request("GET", "/api/view")
        second = conn.getresponse()
        second.read()
        assert second.status == 403
        assert second.getheader("Set-Cookie") is None
    finally:
        conn.close()


def test_the_truth_strip_reads_the_lock_and_clears_only_a_dead_one(
    server: str, project: Path
) -> None:
    """docs/plans/PROJECT-LOCK.md, Tyler's third answer: Studio shows the
    holder and its button clears a stale lock only. The window never takes
    the lock itself — a read of the route leaves none behind."""
    import os
    import subprocess
    import sys

    from proofcut import projectlock

    status, free = _json(f"{server}/api/lock")
    assert status == 200 and free == {"held": False}
    assert not projectlock.lock_dir(project).exists()

    def plant(pid: int) -> None:
        directory = projectlock.lock_dir(project)
        directory.mkdir(parents=True)
        owner = {"token": "t", "pid": pid, **projectlock.machine(),
                 "started": "2026-09-22T00:00:00", "command": "proofcut mcp (stdio)"}
        (directory / "owner.json").write_text(json.dumps(owner))
        (directory / "heartbeat").write_text("0")

    plant(os.getpid())
    status, live = _json(f"{server}/api/lock")
    assert live["held"] is True and live["stale"] is False and live["pid"] == os.getpid()
    status, refused = _post(f"{server}/api/unlock", {})
    assert status == 400 and "not stale" in refused["error"]
    assert projectlock.lock_dir(project).exists()

    import shutil

    shutil.rmtree(projectlock.lock_dir(project))
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    plant(child.pid)
    assert _json(f"{server}/api/lock")[1]["stale"] is True
    status, cleared = _post(f"{server}/api/unlock", {})
    assert status == 200 and cleared["broken"] is True
    assert _json(f"{server}/api/lock")[1] == {"held": False}
