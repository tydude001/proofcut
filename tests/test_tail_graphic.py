"""A tail that plays an animated graphic — docs/plans/ENDCARD.md.

`tail asset=graphic:<name>` plays the graphic's intro from the tail's first
frame, then its hold to the tail's last, on the lane a card tail uses. Pinned
here without a browser, on a faked capture: the pieces and their lengths on
both lanes, the fade's first frame, and every refusal — a capture missing,
stale, transparent or showing a video, and a tail shorter than the intro.
What melt draws from a real capture is read back off a render in
`test_server_stdio.py` (`test_an_endcard_tail_eases_its_lines_in_on_a_real_render`).
"""

from __future__ import annotations

import json
import shutil
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest

from proofcut import motion, ops
from proofcut import timeline as tl
from proofcut.project import Project, ProjectError

EXPORT_FPS = 30.0

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def _png(alpha: int) -> bytes:
    """A 1x1 RGBA PNG, grey at `alpha`."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(bytes([0, 128, 128, 128, alpha]))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A film clip cut to 5 s on the Edit's own track, as a screen recording is."""
    project = Project.create(tmp_path / "proj")
    film = tmp_path / "film.mp4"
    film.write_bytes(b"not really a video")
    manifest = project.read_manifest()
    manifest["clips"] = [
        {"clip_id": "film", "source": str(film), "duration": 10.0, "has_video": True, "has_audio": True},
        {"clip_id": "vo", "source": str(tmp_path / "vo.wav"), "duration": 6.0, "has_video": False, "has_audio": True},
    ]
    manifest["timebase"] = 1000.0
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("film", 0.0, 2.0), tl.Segment("film", 4.0, 7.0)])
    tl.write(tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0), project.timeline_path)
    return project


def _graphic(project: Project, name: str = "end", *, intro: float = 1.0, outro: float = 0.0, loop: float | None = None) -> None:
    ops.graphic_new(project.root, name, html="<html></html>", capture=False, intro=intro, outro=outro, loop=loop)


def _capture(
    project: Project, name: str = "end", *, alpha: int = 255, video: bool = False, stale: bool = False
) -> Path:
    """What `motion.capture` leaves, without a browser, at the spec's phases."""
    width, height, rate = ops._graphic_target(project)
    spec = motion.read_spec(project.graphics_dir / name)
    layout = motion.phase_frames(spec, rate)
    frames = project.graphic_frames_dir / name
    for phase in motion.PHASES:
        (frames / phase).mkdir(parents=True, exist_ok=True)
        for k in range(layout[phase]):
            (frames / phase / f"f{k:04d}.png").write_bytes(_png(alpha))
    record = {
        "stamp": "stale" if stale else motion.stamp(project.graphics_dir / name, width, height, rate),
        "canvas": [width, height], "fps": rate, "hold_at": layout["hold_at"],
        **{phase: layout[phase] for phase in motion.PHASES}, "loop": layout["loop"],
    }  # fmt: skip
    if video:
        record["video"] = {"videos": 1, "ends": None}
    (frames / motion.CAPTURE_NAME).write_text(json.dumps(record))
    return frames


def _build(project: Project) -> dict:
    return ops._build_mlt(project, ops._load_edit(project), fps=EXPORT_FPS)


def _entries(document: ET.Element, playlist: str) -> list[tuple[str, int]]:
    """Each entry's resource and length on `playlist`."""
    out = []
    for entry in document.find(f"*[@id='{playlist}']").findall("entry"):
        node = document.find(f"*[@id='{entry.get('producer')}']")
        out.append((node.find("property[@name='resource']").text, int(entry.get("out")) - int(entry.get("in")) + 1))
    return out


# -- tail() --------------------------------------------------------------------


def test_a_graphic_tail_is_stored_and_reports_its_capture(project: Project) -> None:
    _graphic(project, outro=0.4)

    result = ops.tail(project.root, asset="graphic:end", seconds=3.0)

    assert result["tail"] == {"asset": "graphic:end", "seconds": 3.0, "fade": 0.0}
    assert result["asset_exists"] is True
    assert result["graphic"]["capture"] == "missing"
    assert "outro" in result["graphic"]["outro_ignored"], "an outro a tail will not play is said"


@needs_ffmpeg
def test_a_current_capture_reports_whether_it_is_opaque(project: Project) -> None:
    _graphic(project)
    _capture(project, alpha=200)

    result = ops.tail(project.root, asset="graphic:end", seconds=3.0)

    assert result["graphic"]["capture"] == "current"
    assert result["graphic"]["opaque"] is False


def test_a_tail_shorter_than_the_graphics_intro_is_refused(project: Project) -> None:
    _graphic(project, intro=1.7)

    with pytest.raises(ProjectError, match="intro takes 1.7s"):
        ops.tail(project.root, asset="graphic:end", seconds=1.0)


def test_a_graphic_that_does_not_exist_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="no graphic 'nope'"):
        ops.tail(project.root, asset="graphic:nope", seconds=2.0)


# -- export ----------------------------------------------------------------------


@needs_ffmpeg
def test_a_graphic_tail_plays_its_intro_then_holds_on_the_edits_track(project: Project) -> None:
    _graphic(project, intro=1.0, outro=0.4)
    frames = _capture(project)
    ops.tail(project.root, asset="graphic:end", seconds=3.0)

    built = _build(project)

    film, tail = round(5.0 * EXPORT_FPS), round(3.0 * EXPORT_FPS)
    assert built["frames"] == film + tail
    assert _entries(built["document"], "playlist0")[-2:] == [
        (str(frames / "intro" / "f%04d.png"), 30),
        (str(frames / "hold" / "f0000.png"), tail - 30),
    ], "the intro once, then the still hold to the end; the outro never plays"
    assert built["tail"]["graphic"] == {
        "name": "end", "intro_frames": 30, "hold_frames": tail - 30, "loop": False,
        "stamp": built["tail"]["graphic"]["stamp"], "outro_ignored": 12,
    }  # fmt: skip


@needs_ffmpeg
def test_a_graphic_tail_goes_on_the_picture_lane_with_cues(project: Project, tmp_path: Path) -> None:
    from proofcut import transcript as tx

    manifest = project.read_manifest()
    edit = tl.Edit([tl.Segment("vo", 0.0, 6.0)])
    tl.write(tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0), project.timeline_path)
    tx.save(
        tx.Transcript(clip_id="vo", words=(tx.Word(index=0, text="one", start=0.2, end=0.5),)),
        project.transcript_path("vo"),
    )
    ops.cue_add(project.root, "vo", 0, "film")
    _graphic(project, intro=1.0)
    frames = _capture(project)
    ops.tail(project.root, asset="graphic:end", seconds=2.0)

    built = _build(project)

    assert _entries(built["document"], "playlist2")[-2:] == [
        (str(frames / "intro" / "f%04d.png"), 30),
        (str(frames / "hold" / "f0000.png"), 30),
    ]
    assert built["frames"] == round(6.0 * EXPORT_FPS) + 60


@needs_ffmpeg
def test_a_looping_hold_loops_through_the_tail(project: Project) -> None:
    _graphic(project, intro=1.0, loop=0.5)
    frames = _capture(project)
    ops.tail(project.root, asset="graphic:end", seconds=3.0)

    entries = _entries(_build(project)["document"], "playlist0")

    assert entries[-1] == (str(frames / "hold" / "f%04d.png"), 60)


@needs_ffmpeg
def test_the_fade_dissolves_in_from_the_intros_first_frame(project: Project) -> None:
    _graphic(project, intro=1.0)
    frames = _capture(project)
    ops.tail(project.root, asset="graphic:end", seconds=2.0, fade=0.5)

    built = _build(project)

    node = built["document"].find("*[@id='tchain0']")
    assert node.find("property[@name='resource']").text == str(frames / "intro" / "f0000.png")
    assert built["tail"]["fade_frames"] == 15


def test_a_tail_graphic_never_captured_is_refused_at_export(project: Project) -> None:
    _graphic(project)
    ops.tail(project.root, asset="graphic:end", seconds=2.0)

    with pytest.raises(ProjectError, match="never been captured.*graphic_capture"):
        _build(project)


def test_a_stale_tail_graphic_is_refused_at_export(project: Project) -> None:
    _graphic(project)
    _capture(project, stale=True)
    ops.tail(project.root, asset="graphic:end", seconds=2.0)

    with pytest.raises(ProjectError, match="changed since it was captured"):
        _build(project)


@needs_ffmpeg
def test_a_transparent_tail_graphic_is_refused_at_export(project: Project) -> None:
    _graphic(project)
    _capture(project, alpha=0)
    ops.tail(project.root, asset="graphic:end", seconds=2.0)

    with pytest.raises(ProjectError, match="transparent pixels"):
        _build(project)


def test_a_tail_graphic_showing_a_video_is_refused_at_export(project: Project) -> None:
    _graphic(project)
    _capture(project, video=True)
    ops.tail(project.root, asset="graphic:end", seconds=2.0)

    with pytest.raises(ProjectError, match="shows a video"):
        _build(project)


# -- the opacity read ------------------------------------------------------------


@needs_ffmpeg
def test_opacity_is_measured_once_and_kept_in_the_capture_record(project: Project) -> None:
    _graphic(project)
    frames = _capture(project)

    assert motion.is_opaque(frames) is True
    assert motion.read_capture(frames)["opaque"] is True
    # A frame turned transparent after the measurement is not re-read: the
    # record is the answer until a recapture replaces it.
    (frames / "hold" / "f0000.png").write_bytes(_png(0))
    assert motion.is_opaque(frames) is True


@needs_ffmpeg
def test_one_transparent_intro_frame_is_enough_to_be_not_opaque(project: Project) -> None:
    _graphic(project)
    frames = _capture(project)
    (frames / "intro" / "f0017.png").write_bytes(_png(254))

    assert motion.is_opaque(frames) is False
