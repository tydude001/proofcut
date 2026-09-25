"""`seed` of several recordings, and `split`: DAYDREAM.md § Splitting a footage
dump, designed. A dump is seeded as one timeline, cleaned once, and cut into
shorts, each its own project beside the dump."""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut.project import Project, ProjectError

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
needs_auto_editor = pytest.mark.skipif(
    shutil.which("auto-editor") is None and not (Path.home() / ".local/bin/auto-editor").exists(),
    reason="auto-editor is not installed",
)

#: Four tone bursts, two words to a burst: a recording with pauses to cut.
BURSTS = [(0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 11.0)]


def _encode_recording(root: Path, name: str, colour: str) -> tuple[Path, Path, Path]:
    """A 12s video of one flat colour over the four bursts, and its transcript
    — the real ffmpeg (and wave-module) encode, run once per session by
    `recording_cache` rather than once per test."""
    audio = root / f"{name}.wav"
    rate = 22050
    with wave.open(str(audio), "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * 12.0)):
            t = i / rate
            loud = any(a <= t < b for a, b in BURSTS)
            frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * t)) if loud else 0)
        out.writeframes(bytes(frames))
    video = root / f"{name}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=160x120:r=30:d=12",
            "-i", str(audio),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(video),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    words = [
        {"word": f"{name}{burst}{n}", "start": start + n, "end": start + n + 0.9}
        for burst, (start, _) in enumerate(BURSTS)
        for n in range(2)
    ]
    transcript = root / f"{name}.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return video, audio, transcript


@pytest.fixture(scope="session")
def recording_cache(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path, Path]]:
    """Recordings `a` (blue) and `b` (red), encoded once for the whole
    session: every `_dump` wants the same bytes, so re-running ffmpeg on the
    same colour and burst pattern for each of ~15 tests bought nothing but
    the wait."""
    root = tmp_path_factory.mktemp("recordings")
    return {
        name: _encode_recording(root, name, colour)
        for name, colour in (("a", "blue"), ("b", "red"))
    }


def _recording(
    root: Path, name: str, cache: dict[str, tuple[Path, Path, Path]]
) -> tuple[Path, Path]:
    """Copy the session's `name` recording into `root` — a project mutates
    its own media, and media can be probed by mtime, so each test needs its
    own files rather than the cached ones."""
    video, audio, transcript = cache[name]
    shutil.copy(video, root / video.name)
    shutil.copy(audio, root / audio.name)
    dest_transcript = root / transcript.name
    shutil.copy(transcript, dest_transcript)
    return root / video.name, dest_transcript


def _dump(
    tmp_path: Path,
    recording_cache: dict[str, tuple[Path, Path, Path]],
    *,
    silences: bool = False,
) -> Project:
    """`dump/` holding recordings `a` (blue) and `b` (red), seeded end to end."""
    root = tmp_path / "dump"
    ops.init(root)
    for name in ("a", "b"):
        video, transcript = _recording(tmp_path, name, recording_cache)
        ops.import_media(root, video, clip_id=name)
        ops.attach_transcript(root, name, transcript)
    ops.seed_timeline(root, ["a", "b"], remove_silences=silences)
    return Project.open(root)


def _edge(clip_id: str, word: int) -> dict[str, object]:
    return {"clip_id": clip_id, "word_index": word}


# -- seed ----------------------------------------------------------------


@needs_ffmpeg
def test_a_seed_lays_several_recordings_end_to_end_in_order(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    segments = tl.read(project.timeline_path).segments
    assert [s.clip_id for s in segments] == ["a", "b"]
    assert tl.read(project.timeline_path).duration == pytest.approx(24.0, abs=0.1)


@needs_ffmpeg
@needs_auto_editor
def test_a_seed_of_several_silence_cuts_every_recording(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    """Finding 1: `follow` spliced the second recording in whole, pauses and
    all. A seed of several runs the silence pass over each."""
    project = _dump(tmp_path, recording_cache, silences=True)
    edit = tl.read(project.timeline_path)
    for clip_id in ("a", "b"):
        kept = sum(s.end - s.start for s in edit.segments if s.clip_id == clip_id)
        assert kept < 10.5, f"{clip_id} kept {kept}s of 12, so its pauses survived"
    assert edit.segments[0].clip_id == "a" and edit.segments[-1].clip_id == "b"


@needs_ffmpeg
def test_a_seed_of_several_reports_each_recording(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    result = ops.seed_timeline(project.root, ["b", "a"], remove_silences=False)
    assert result["clip_id"] == ["b", "a"]
    assert [c["clip_id"] for c in result["clips"]] == ["b", "a"]
    assert [s.clip_id for s in tl.read(project.timeline_path).segments] == ["b", "a"]


@needs_ffmpeg
def test_a_seed_of_several_refuses_a_repeat_and_a_recording_with_no_picture(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    with pytest.raises(ProjectError, match="more than once"):
        ops.seed_timeline(project.root, ["a", "a"], remove_silences=False)
    ops.import_media(project.root, tmp_path / "a.wav", clip_id="vo")
    with pytest.raises(ProjectError, match="'vo' has no picture"):
        ops.seed_timeline(project.root, ["a", "vo"], remove_silences=False)
    # One recording with no picture is still an ordinary seed.
    assert ops.seed_timeline(project.root, "vo", remove_silences=False)["clip_id"] == "vo"


# -- split ---------------------------------------------------------------


@needs_ffmpeg
def test_split_makes_each_short_beside_the_dump_with_only_its_recordings(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    result = ops.split(
        project.root,
        [
            {"name": "one", "from": _edge("a", 0), "to": _edge("a", 7)},
            {"name": "two", "from": _edge("b", 2), "to": _edge("b", 5)},
        ],
    )
    one, two = (Project.open(tmp_path / name) for name in ("one", "two"))
    assert [s["project"] for s in result["shorts"]] == [str(one.root), str(two.root)]
    assert result["shorts"][0]["duration"] == pytest.approx(10.9, abs=0.05)
    # b's words 2..5 are 3.0 to 7.9 of its source.
    assert result["shorts"][1]["duration"] == pytest.approx(4.9, abs=0.05)

    assert result["shorts"][0]["clips_dropped"] == ["b"]
    assert [c["clip_id"] for c in one.read_manifest()["clips"]] == ["a"]
    assert not one.transcript_path("b").exists()
    assert not (one.root / "media" / "b.mp4").is_symlink()
    assert [c["clip_id"] for c in two.read_manifest()["clips"]] == ["b"]
    assert two.read_manifest()["derived_from"]["short"] == "two"
    # The only state behind a short is the whole dump, which names recordings
    # it no longer has: a short begins where it was split.
    assert one.snapshots() == []


@needs_ffmpeg
def test_a_short_across_the_join_keeps_both_recordings(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    result = ops.split(
        project.root, [{"name": "join", "from": _edge("a", 6), "to": _edge("b", 1)}]
    )
    short = result["shorts"][0]
    assert short["clips_dropped"] == []
    # a's word 6 starts at 9.0; b's word 1 ends at 1.9 into b, which is 12 + 1.9.
    assert short["start"] == pytest.approx(9.0)
    assert short["end"] == pytest.approx(13.9)
    assert [s.clip_id for s in tl.read(Path(short["project"]) / "project.otio").segments] == [
        "a",
        "b",
    ]


@needs_ffmpeg
def test_split_echoes_each_edge_and_its_neighbours(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    planned = ops.split(
        project.root,
        [{"name": "s", "from": {"clip_id": "a", "phrase": "a20"}, "to": _edge("a", 5)}],
        plan=True,
    )
    edge = planned["shorts"][0]["from"]
    assert (edge["index"], edge["text"]) == (4, "a20")
    assert [w["text"] for w in edge["context_before"]] == ["a01", "a10", "a11"]
    assert [w["text"] for w in edge["context_after"]] == ["a21", "a30", "a31"]


@needs_ffmpeg
def test_split_reports_overlaps_and_unassigned_material_and_refuses_neither(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    planned = ops.split(
        project.root,
        [
            {"name": "x", "from": _edge("a", 0), "to": _edge("a", 3)},
            {"name": "y", "from": _edge("a", 2), "to": _edge("a", 5)},
        ],
        plan=True,
    )
    assert planned["overlaps"] == [{"shorts": ["x", "y"], "span": [3.0, 4.9]}]
    assert planned["unassigned"] == [[7.9, 24.0]]
    assert [s["clips_dropped"] for s in planned["shorts"]] == [["b"], ["b"]]
    assert not (tmp_path / "x").exists(), "a plan creates nothing"


@needs_ffmpeg
def test_split_takes_reel_seconds_too(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    result = ops.split(project.root, [{"name": "watch", "start": 13.0, "end": 17.0}])
    assert result["shorts"][0]["duration"] == pytest.approx(4.0, abs=0.05)
    assert result["shorts"][0]["clips_dropped"] == ["a"]


@needs_ffmpeg
def test_split_refuses_a_word_the_cleaning_removed(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    ops.cut_by_transcript(project.root, "a", cut=[[2, 3]])
    with pytest.raises(ProjectError, match="word 2 .* is not on the timeline"):
        ops.split(project.root, [{"name": "s", "from": _edge("a", 2), "to": _edge("a", 5)}])
    assert not (tmp_path / "s").exists()


@needs_ffmpeg
@pytest.mark.parametrize(
    ("short", "message"),
    [
        ({"name": "../up", "start": 0.0, "end": 2.0}, "is a path"),
        ({"name": "a/b", "start": 0.0, "end": 2.0}, "is a path"),
        ({"name": "s", "from": _edge("a", 0)}, "one pair, both halves"),
        ({"name": "s", "from": _edge("a", 0), "to": _edge("a", 1), "start": 0.0}, "one pair"),
        ({"name": "s", "from": _edge("a", 5), "to": _edge("a", 2)}, "not after"),
        ({"name": "s", "start": 0.0, "end": 2.0, "canvas": "9:16"}, "does not take canvas"),
    ],
)
def test_split_refuses_a_malformed_short(
    tmp_path: Path,
    short: dict[str, object],
    message: str,
    recording_cache: dict[str, tuple[Path, Path, Path]],
) -> None:
    project = _dump(tmp_path, recording_cache)
    with pytest.raises(ProjectError, match=message):
        ops.split(project.root, [short])


@needs_ffmpeg
def test_split_refuses_an_existing_directory_and_a_repeated_name(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    (tmp_path / "taken").mkdir()
    with pytest.raises(ProjectError, match="already exists"):
        ops.split(project.root, [{"name": "taken", "start": 0.0, "end": 2.0}])
    with pytest.raises(ProjectError, match="two shorts are named 'twice'"):
        ops.split(
            project.root,
            [{"name": "twice", "start": 0.0, "end": 2.0}, {"name": "twice", "start": 3.0, "end": 5.0}],
        )


@needs_ffmpeg
def test_a_split_that_fails_part_way_leaves_no_short_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_cache: dict[str, tuple[Path, Path, Path]],
) -> None:
    project = _dump(tmp_path, recording_cache)
    real = ops.reel
    calls: list[str] = []

    def failing(*args: object, **kwargs: object) -> dict[str, object]:
        if not kwargs.get("plan"):
            calls.append(str(args[1]))
            if len(calls) == 2:
                raise ProjectError("the second short failed")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ops, "reel", failing)
    with pytest.raises(ProjectError, match="second short failed"):
        ops.split(
            project.root,
            [{"name": "first", "start": 0.0, "end": 2.0}, {"name": "second", "start": 3.0, "end": 5.0}],
        )
    assert not (tmp_path / "first").exists()
    assert not (tmp_path / "second").exists()


@needs_ffmpeg
def test_split_puts_shorts_in_into_when_asked(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    project = _dump(tmp_path, recording_cache)
    elsewhere = tmp_path / "shorts"
    elsewhere.mkdir()
    result = ops.split(project.root, [{"name": "s", "start": 0.0, "end": 2.0}], into=elsewhere)
    assert result["shorts"][0]["project"] == str(elsewhere / "s")


@needs_ffmpeg
def test_split_drops_a_dropped_recordings_rows_and_keeps_a_cued_one(
    tmp_path: Path, recording_cache: dict[str, tuple[Path, Path, Path]]
) -> None:
    """Design 6: a recording is in a short when it is on the timeline or a kept
    cue names it; anything keyed by a dropped one's `clip_id` goes with it."""
    project = _dump(tmp_path, recording_cache)
    manifest = project.read_manifest()
    manifest["reframe"] = [{"clip_id": "b", "rect": [0, 0, 90, 120]}]
    manifest["unspoken"] = [{"clip_id": "b", "word_index": 1}]
    project.write_manifest(manifest)
    result = ops.split(project.root, [{"name": "s", "from": _edge("a", 0), "to": _edge("a", 3)}])
    short = Project.open(result["shorts"][0]["project"]).read_manifest()
    assert short.get("reframe") == [] and short.get("unspoken") == []

    ops.cue_add(project.root, "a", 1, "b")
    result = ops.split(project.root, [{"name": "cued", "from": _edge("a", 0), "to": _edge("a", 3)}])
    assert result["shorts"][0]["clips_dropped"] == []
