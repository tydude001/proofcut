"""Reading melt's answer, and the frame arithmetic the answer is compared to.

Both halves are pure — a string in, a number out — so the cases that matter are
cheap to state exactly. Whether melt is reachable, and whether the count it
gives back matches a real export, is checked over stdio in test_server_stdio.py.

The MLT document below is **verbatim** `melt <project> -consumer xml` output,
captured 2026-08-07 from Kdenlive 26.04.3 / MLT 7.40 reading an auto-editor
`--export kdenlive` project of a 12.0s, 360-frame, 30fps source. Rendering that
same project produced 361 frames, so `length` is the field that predicts a
render and 361 is the number this file is entitled to expect.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from proofcut import autoeditor, media, picture
from proofcut.timeline import Edit, Segment

MELT_XML = """<?xml version="1.0"?>
<mlt LC_NUMERIC="C" version="7.40.0" title="out.kdenlive">
  <profile description="automatic" width="320" height="240" progressive="1" \
sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="4" \
display_aspect_den="3" frame_rate_num="30" frame_rate_den="1" colorspace="709"/>
  <producer id="tractor2" in="0" out="360">
    <property name="length">361</property>
    <property name="eof">pause</property>
    <property name="resource">proj/out.kdenlive</property>
    <property name="mlt_service">xml</property>
    <property name="kdenlive:projectTractor">1</property>
    <property name="xml">was here</property>
    <property name="seekable">1</property>
  </producer>
  <playlist id="playlist0">
    <entry producer="tractor2" in="0" out="360"/>
  </playlist>
  <tractor id="tractor0" title="out.kdenlive" in="0" out="360">
    <track producer="playlist0"/>
  </tractor>
</mlt>
"""


def test_the_wrapping_producers_length_is_the_frame_count() -> None:
    assert picture.parse_melt_xml(MELT_XML) == 361


def test_a_media_producers_length_is_not_mistaken_for_the_timelines() -> None:
    """A source clip carries a `length` too, and it is a different number.

    It also comes first in the document, so taking the first `length` seen —
    which is what a line-oriented read of this output does — reports the length
    of whatever media happens to be declared earliest instead of the timeline's.
    """
    document = MELT_XML.replace(
        '  <playlist id="playlist0">',
        """  <producer id="producer0" in="0" out="200">
    <property name="length">99999</property>
    <property name="mlt_service">avformat</property>
  </producer>
  <playlist id="playlist0">""",
    )

    assert picture.parse_melt_xml(document) == 361


def test_a_document_with_no_wrapping_producer_falls_back_to_the_tractor() -> None:
    """MLT's `out` is frame-inclusive, so the fallback has to add the one back."""
    document = """<?xml version="1.0"?>
<mlt LC_NUMERIC="C" version="7.40.0">
  <playlist id="playlist0"/>
  <tractor id="tractor0" in="0" out="360">
    <track producer="playlist0"/>
  </tractor>
</mlt>
"""
    assert picture.parse_melt_xml(document) == 361


def test_an_unreadable_or_countless_document_raises_rather_than_guesses() -> None:
    with pytest.raises(picture.PictureError, match="readable MLT document"):
        picture.parse_melt_xml("melt: could not open the project\n")

    with pytest.raises(picture.PictureError, match="no frame count"):
        picture.parse_melt_xml('<mlt version="7.40.0"><playlist id="p"/></mlt>')


def test_a_melt_that_prints_no_document_is_named_and_quoted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Six Windows renders failed with only "syntax error: line 1, column 0",
    which says neither what answered to `melt` nor what it said. The refusal
    carries both, repr'd, so a BOM or a banner is visible in a CI log."""
    project = tmp_path / "p.mlt"
    project.write_text("<mlt/>")
    monkeypatch.setattr(picture, "melt_command", lambda: [r"C:\somewhere\melt"])
    monkeypatch.setattr(picture, "display_env", dict)
    monkeypatch.setattr(
        picture.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "\ufeffNot melt 1.0 usage: melt FILE", ""
        ),
    )
    with pytest.raises(picture.PictureError) as caught:
        picture.project_frames(project)
    message = str(caught.value)
    assert "readable MLT document" in message
    assert r"C:\somewhere\melt" in message
    assert "\\ufeffNot melt 1.0" in message


_RACED = "error: Extension org.freedesktop.Platform.GL.default has invalid merge-dirs\n"


def test_a_melt_launch_that_lost_flatpaks_race_is_tried_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Twenty concurrent renders through the Kdenlive flatpak failed in flatpak's
    launcher before melt ran, and read as "melt printed no timeline". The call
    is repeated, because nothing was read or written."""
    project = tmp_path / "p.mlt"
    project.write_text("<mlt/>")
    monkeypatch.setattr(picture, "melt_command", lambda: ["melt"])
    monkeypatch.setattr(picture, "display_env", dict)
    monkeypatch.setattr(picture, "LAUNCH_RETRY_PAUSE", 0)
    answers = [("", _RACED), ("", _RACED), (MELT_XML, "")]
    calls: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        out, err = answers.pop(0)
        return subprocess.CompletedProcess(command, 0, out, err)

    monkeypatch.setattr(picture.subprocess, "run", fake)
    assert picture.project_frames(project) == 361
    assert len(calls) == 3


def test_a_melt_failure_of_its_own_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the launcher's own line is a race. Anything melt says about the
    project is its answer, and running it again would only say it twice."""
    project = tmp_path / "p.mlt"
    project.write_text("<mlt/>")
    monkeypatch.setattr(picture, "melt_command", lambda: ["melt"])
    monkeypatch.setattr(picture, "display_env", dict)
    monkeypatch.setattr(picture, "LAUNCH_RETRY_PAUSE", 0)
    calls: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "Failed to load project\n")

    monkeypatch.setattr(picture.subprocess, "run", fake)
    with pytest.raises(picture.PictureError, match="printed no timeline"):
        picture.project_frames(project)
    assert len(calls) == 1


def test_a_launch_that_keeps_losing_the_race_is_reported_not_looped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The retries are bounded, and the launcher's line reaches the refusal."""
    project = tmp_path / "p.mlt"
    project.write_text("<mlt/>")
    monkeypatch.setattr(picture, "melt_command", lambda: ["melt"])
    monkeypatch.setattr(picture, "display_env", dict)
    monkeypatch.setattr(picture, "LAUNCH_RETRY_PAUSE", 0)
    calls: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", _RACED)

    monkeypatch.setattr(picture.subprocess, "run", fake)
    with pytest.raises(picture.PictureError, match="invalid merge-dirs"):
        picture.project_frames(project)
    assert len(calls) == 1 + picture.LAUNCH_RETRIES


def test_a_render_that_lost_the_launch_race_is_rendered(melt: _FakeMelt, tmp_path: Path) -> None:
    """The render path retries too: the raced call writes no file."""
    project = tmp_path / "p.mlt"
    project.write_text("<mlt/>")
    picture.LAUNCH_RETRY_PAUSE, pause = 0, picture.LAUNCH_RETRY_PAUSE
    raced = {"left": 1}
    real = melt.__call__

    def racing(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if raced["left"]:
            raced["left"] -= 1
            return subprocess.CompletedProcess(command, 0, "", _RACED)
        return real(command, **kwargs)

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(picture.subprocess, "run", racing)
            result = picture.render(project, tmp_path / "out.mp4", expect_frames=150)
    finally:
        picture.LAUNCH_RETRY_PAUSE = pause
    assert result["agrees"] is True
    assert (tmp_path / "out.mp4").read_bytes() == b"a render, honestly"


def test_a_wayland_socket_travels_with_the_directory_it_lives_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`WAYLAND_DISPLAY` is a socket name, and Qt resolves it under
    `XDG_RUNTIME_DIR`. Naming one without the other is how melt aborts printing
    nothing under a scrubbed environment — which is the environment the MCP
    stdio transport hands its server (measured 2026-08-08)."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "wayland-0").touch()
    (tmp_path / "wayland-0.lock").touch()

    env = picture.display_env()

    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["XDG_RUNTIME_DIR"] == str(tmp_path)


def test_an_inherited_display_is_left_alone_but_still_kept_whole(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A session that already names a display is not second-guessed — the pair
    is only ever completed, never replaced."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-9")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "wayland-9").touch()
    (tmp_path / "wayland-0").touch()

    env = picture.display_env()

    assert env["WAYLAND_DISPLAY"] == "wayland-9"
    assert env["XDG_RUNTIME_DIR"] == str(tmp_path)


def test_melt_command_prefers_an_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROOFCUT_MELT is a command, not a path — the flatpak form is four words."""
    monkeypatch.setenv("PROOFCUT_MELT", "flatpak run --command=melt org.kde.kdenlive")

    assert picture.melt_command() == [
        "flatpak",
        "run",
        "--command=melt",
        "org.kde.kdenlive",
    ]


# -- the number melt is compared against ---------------------------------


def test_frame_total_counts_the_grid_the_export_lays_down() -> None:
    edit = Edit([Segment("vo", 0.0, 2.0), Segment("vo", 3.4, 7.0), Segment("vo", 8.4, 12.0)])

    assert autoeditor.frame_layout(edit, 30.0) == [(0, 60), (102, 108), (252, 108)]
    assert autoeditor.frame_total(edit, 30.0) == 276


def test_quantising_each_edge_is_not_the_same_as_quantising_the_total() -> None:
    """Why the count comes from `frame_layout` and never from the duration.

    Two segments of 0.017s each are half a frame apiece; the export cannot lay
    down half a frame, so each becomes one and the timeline is two frames long.
    Rounding the 0.034s total instead says one. The exported timeline is the
    honest answer, and only the per-segment path knows it.
    """
    edit = Edit([Segment("vo", 0.0, 0.017), Segment("vo", 1.0, 1.017)])

    assert autoeditor.frame_total(edit, 30.0) == 2
    assert round(edit.duration * 30.0) == 1


def test_a_segment_shorter_than_a_frame_still_gets_one() -> None:
    """`max(1, ...)`: a segment that rounds to zero frames would vanish silently."""
    edit = Edit([Segment("vo", 0.0, 0.001)])

    assert autoeditor.frame_total(edit, 30.0) == 1


# -- reading a render directly --------------------------------------------

BLACKDETECT_STDERR = """\
frame=  360 fps=0.0 q=-0.0 Lsize=N/A time=00:00:12.00 bitrate=N/A speed=45.2x
[Parsed_blackdetect_0 @ 0x55d1234] black_start:0 black_end:0.5 black_duration:0.5
some other banter ffmpeg prints in between
[Parsed_blackdetect_0 @ 0x55d1234] black_start:11.967 black_end:12 black_duration:0.033
"""


def test_parse_blackdetect_reads_every_run() -> None:
    assert picture.parse_blackdetect(BLACKDETECT_STDERR) == [
        {"start": 0.0, "end": 0.5, "duration": 0.5},
        {"start": 11.967, "end": 12.0, "duration": 0.033},
    ]


def test_parse_blackdetect_empty_stderr_returns_no_runs() -> None:
    assert picture.parse_blackdetect("frame=1 fps=0.0 q=-0.0 Lsize=N/A\n") == []


SIGNALSTATS_STDOUT = """\
[Parsed_metadata_1 @ 0x55d1234] frame:0    pts:0       pts_time:0
[Parsed_metadata_1 @ 0x55d1234] lavfi.signalstats.YMIN=9
[Parsed_metadata_1 @ 0x55d1234] lavfi.signalstats.YAVG=123.532
[Parsed_metadata_1 @ 0x55d1234] lavfi.signalstats.YMAX=240
[Parsed_metadata_1 @ 0x55d1234] lavfi.signalstats.YDIF=0.983398
"""


def test_parse_signalstats_reads_every_lavfi_key() -> None:
    stats = picture.parse_signalstats(SIGNALSTATS_STDOUT)
    assert stats["YMIN"] == 9.0
    assert stats["YAVG"] == pytest.approx(123.532)
    assert stats["YMAX"] == 240.0
    assert stats["YDIF"] == pytest.approx(0.983398)
    assert isinstance(stats["YMIN"], float)


# -- rendering, and what the render is checked against -------------------


def _measured(**overrides: object) -> dict[str, object]:
    """A 5s 1080p render at 30fps, as `render()` measures one off disk."""
    return {
        "width": 1920,
        "height": 1080,
        "frames": 150,
        "duration": 5.0,
        "has_video": True,
        "has_audio": True,
    } | overrides


def test_a_degraded_resolution_is_a_problem_however_melt_exited() -> None:
    """720x576 is auto-editor's multi-source downgrade signature, and the whole
    reason the pixels are checked rather than the status."""
    problems = picture.render_problems(
        _measured(width=720, height=576), expect_resolution=(1920, 1080)
    )

    assert len(problems) == 1
    assert "720x576" in problems[0]


def test_a_frame_count_that_padded_the_render_is_a_problem() -> None:
    problems = picture.render_problems(_measured(frames=181), expect_frames=150)

    assert len(problems) == 1
    assert "+31" in problems[0]


def test_duration_is_not_compared_when_there_are_frames_to_count() -> None:
    """An mp4's duration is the longest of its streams, and an AAC stream
    routinely outruns the video by a frame of padding. Comparing it on a video
    render would fail correct ones — the frame count is the exact check."""
    assert picture.render_problems(
        _measured(duration=5.07), expect_frames=150, expect_duration=5.0
    ) == []


def test_an_audio_only_render_falls_back_to_its_duration() -> None:
    """The ordinary shape for a VO project with two clips and no picture: there
    are no frames to count, so the container duration is the only length."""
    audio_only = _measured(frames=None, has_video=False, width=None, height=None, duration=5.6)

    problems = picture.render_problems(
        audio_only, expect_frames=150, expect_resolution=(1920, 1080), expect_duration=5.0
    )

    assert len(problems) == 1
    assert "no video stream" in problems[0]
    # And inside the tolerance it is not a problem — AAC pads to 1024 samples.
    assert picture.render_problems(audio_only | {"duration": 5.02}, expect_duration=5.0) == []


class _FakeMelt:
    """A melt that writes the file it was asked for and reports nothing else.

    The render path is a subprocess, a probe and a copy; this replaces the
    subprocess so the other two can be asserted on. What a real melt does with
    a real document is measured against a real render instead — no fake can
    establish that.
    """

    def __init__(self) -> None:
        self.command: list[str] = []
        self.kwargs: dict[str, object] = {}

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.command = command
        self.kwargs = kwargs
        target = next(a for a in command if a.startswith("avformat:")).removeprefix("avformat:")
        Path(target).write_bytes(b"a render, honestly")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def melt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeMelt:
    fake = _FakeMelt()
    monkeypatch.setenv("PROOFCUT_MELT", "melt")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(picture.subprocess, "run", fake)
    monkeypatch.setattr(picture.media, "probe", lambda p: _PROBE)
    monkeypatch.setattr(
        picture.media,
        "count_frames",
        lambda p: {"frames": 150, "container_frames": 150, "duration": 5.0, "has_video": True},
    )
    return fake


_PROBE = media.MediaInfo(
    duration=5.0,
    has_video=True,
    has_audio=True,
    fps=30.0,
    width=1920,
    height=1080,
    sample_rate=48000,
    channels=2,
    video_codec="h264",
    audio_codec="aac",
    vfr=False,
)


def test_the_consumer_gets_the_codec_and_nothing_else(
    melt: _FakeMelt, tmp_path: Path
) -> None:
    """Restating the project profile on the consumer is what the 14.6 GB that
    froze the machine correlated with, and no single one of the four extra
    properties reproduces it alone (HISTORY.md § 4) — so the assertion is that
    none of them is there at all."""
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    picture.render(project, tmp_path / "out.mp4", expect_frames=150)

    assert melt.command[-4:] == list(picture.RENDER_ARGS)
    assert not [a for a in melt.command if a.split("=")[0] in {"width", "height", "progressive", "ab"}]


def test_the_render_never_hands_melt_the_console_as_stdin(
    melt: _FakeMelt, tmp_path: Path
) -> None:
    """melt with its output captured and a console on stdin writes the whole
    file and then never exits — a first real Windows PC sat on it for minutes
    while CI's console-less runner rendered in 4 s. Nothing on Linux or CI
    reproduces the hang, so the call's own argument is what is held."""
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    picture.render(project, tmp_path / "out.mp4", expect_frames=150)

    assert melt.kwargs.get("stdin") is subprocess.DEVNULL


def test_the_render_is_staged_under_the_scratch_root_then_copied(
    melt: _FakeMelt, tmp_path: Path
) -> None:
    """Staged because the flatpak cannot see the host's `/tmp`, and because a
    render that dies halfway would otherwise leave a half-muxed file at the
    destination that looks finished."""
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")
    output = tmp_path / "renders" / "out.mp4"

    result = picture.render(project, output, expect_frames=150, expect_resolution=(1920, 1080))

    staged = next(a for a in melt.command if a.startswith("avformat:")).removeprefix("avformat:")
    assert Path(staged).is_relative_to(tmp_path / "scratch")
    assert output.is_file()
    assert result["agrees"] is True
    assert result["frames"] == 150
    assert not (tmp_path / "scratch").exists() or not list((tmp_path / "scratch").iterdir())


def test_a_render_that_disagrees_is_not_copied_into_place(
    melt: _FakeMelt, tmp_path: Path
) -> None:
    """The staged file is kept and named: the evidence for what melt did is the
    file it wrote."""
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")
    output = tmp_path / "out.mp4"

    with pytest.raises(picture.PictureError, match="disagrees with the timeline"):
        picture.render(project, output, expect_frames=99)

    assert not output.exists()
    staged = next(a for a in melt.command if a.startswith("avformat:")).removeprefix("avformat:")
    assert Path(staged).is_file()


def test_a_headless_qt_platform_counts_as_a_display(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`QT_QPA_PLATFORM=offscreen` draws a `qimage` producer with no socket at
    all (measured 2026-08-23, red in → red out), so a background job with no
    session renders under it rather than being refused for the socket it
    does not need."""
    monkeypatch.setattr(picture, "display_env", lambda: {"QT_QPA_PLATFORM": "offscreen"})
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    try:
        picture.render(project, tmp_path / "out.mp4")
    except picture.PictureError as exc:
        assert "no display" not in str(exc)
    assert picture.qt_is_headless({"QT_QPA_PLATFORM": "offscreen"})
    assert picture.qt_is_headless({"QT_QPA_PLATFORM": "minimal:tty"})
    assert not picture.qt_is_headless({"QT_QPA_PLATFORM": "wayland"})
    assert not picture.qt_is_headless({})


def test_a_render_with_no_display_refuses_rather_than_dropping_the_picture_lane(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a display every `qimage` producer and the `qtblend` transition
    refuse to load, the picture lane vanishes and melt still exits 0
    (HISTORY.md § 4) — so this is a refusal, not a warning."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(picture, "display_env", dict)
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    with pytest.raises(picture.PictureError, match="no display"):
        picture.render(project, tmp_path / "out.mp4")


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_a_native_qt_platform_neither_asks_for_a_uid_nor_refuses(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """Qt draws through cocoa on macOS and its `windows` plugin on Windows, so
    the Linux display dance does not apply there — and Windows has no
    `os.getuid` at all, which made every render and `proofcut doctor` raise
    `AttributeError` before anything ran (docs/plans/PORTABILITY.md step 1).
    Removing `getuid` is what proves the path never reaches for it."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delattr(os, "getuid", raising=False)
    for name in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "QT_QPA_PLATFORM"):
        monkeypatch.delenv(name, raising=False)

    assert picture.display_env() == dict(os.environ)

    # `shutil.which` branches on `sys.platform` itself and reaches for
    # `_winapi` under a faked win32, which Linux has not got. Neither OS has
    # `systemd-run`, so None is the answer a real `which` gives on both.
    monkeypatch.setattr(picture.shutil, "which", lambda name: None)
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")
    result = picture.render(project, tmp_path / "out.mp4", expect_frames=150)
    assert result["agrees"] is True
    assert (tmp_path / "out.mp4").exists()


def test_a_render_melt_did_not_write_is_a_failure_whatever_it_exited(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        picture.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", "melt: eh"),
    )
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    with pytest.raises(picture.PictureError, match="rendered nothing"):
        picture.render(project, tmp_path / "out.mp4")


def _unreadable(p: Path) -> media.MediaInfo:
    raise media.MediaError(f"ffprobe failed on {p}: moov atom not found")


def test_an_unreadable_render_carries_melts_own_exit_and_output(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shotcut's Mac melt has left a file with no moov atom, and ffprobe's
    complaint was all the log held — melt's exit and stderr must survive."""

    def killed(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        melt(command, **kwargs)
        return subprocess.CompletedProcess(command, -11, "", "Current Frame: 288\nmelt: bye")

    monkeypatch.setattr(picture.subprocess, "run", killed)
    monkeypatch.setattr(picture.media, "probe", _unreadable)
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    with pytest.raises(picture.PictureError) as caught:
        picture.render(project, tmp_path / "out.mp4")
    message = str(caught.value)
    assert "killed by SIGSEGV" in message
    assert "melt: bye" in message
    assert "moov atom not found" in message
    assert not (tmp_path / "out.mp4").exists()
    staged = next((tmp_path / "scratch").glob("render-*/out.mp4"))
    assert str(staged) in message


class _CrashingMelt(_FakeMelt):
    """melt that dies of `signals` in turn, each after writing a torn file,
    and then renders — the Mac melt's `cache_object_close` segfault."""

    def __init__(self, signals: list[int]) -> None:
        super().__init__()
        self.signals = signals
        self.calls = 0

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        completed = super().__call__(command, **kwargs)
        self.calls += 1
        if self.calls <= len(self.signals):
            return subprocess.CompletedProcess(command, -self.signals[self.calls - 1], "", "")
        return completed


def _crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, signals: list[int]
) -> tuple[_CrashingMelt, Path]:
    fake = _CrashingMelt(signals)
    monkeypatch.setattr(picture.subprocess, "run", fake)
    # A torn file is one the probe cannot read; the fake's last run is whole.
    monkeypatch.setattr(
        picture.media,
        "probe",
        lambda p: _unreadable(p) if fake.calls <= len(signals) else _PROBE,
    )
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")
    return fake, project


def test_a_crashed_melt_is_run_again_and_says_so(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, project = _crashing(monkeypatch, tmp_path, [11, 11])

    result = picture.render(project, tmp_path / "out.mp4", expect_frames=150)

    assert fake.calls == 3
    assert result["crash_retries"] == 2
    assert any("melt crashed 2 time(s) (killed by SIGSEGV" in n for n in result["notes"])
    assert (tmp_path / "out.mp4").exists()


def test_a_melt_that_keeps_crashing_fails_after_the_retries(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, project = _crashing(monkeypatch, tmp_path, [11] * (picture.CRASH_RETRIES + 1))

    with pytest.raises(picture.PictureError, match="after 2 crashed run"):
        picture.render(project, tmp_path / "out.mp4")
    assert fake.calls == picture.CRASH_RETRIES + 1


def test_a_render_the_memory_cap_killed_is_not_run_again(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGKILL is the cap's; running that render again only hits it again."""
    fake, project = _crashing(monkeypatch, tmp_path, [9])

    # Windows' `signal` has no SIGKILL, so there it is named by number.
    with pytest.raises(picture.PictureError, match=r"killed by (SIGKILL|signal 9)"):
        picture.render(project, tmp_path / "out.mp4")
    assert fake.calls == 1


def test_an_unreadable_render_names_a_plain_exit_too(
    melt: _FakeMelt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture.media, "probe", _unreadable)
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    with pytest.raises(picture.PictureError, match=r"\(exit 0\)"):
        picture.render(project, tmp_path / "out.mp4")


def test_blackdetect_missing_target_raises() -> None:
    with pytest.raises(picture.PictureError, match="no such file"):
        picture.blackdetect("/no/such/render.mp4")


def test_extract_frame_missing_target_raises() -> None:
    with pytest.raises(picture.PictureError, match="no such file"):
        picture.extract_frame("/no/such/render.mp4", 1.0, "/tmp/whatever.png")


# -- the staging sweep ----------------------------------------------------


def _staged(root: Path, name: str, *, age_days: float) -> Path:
    """A staging directory of a given age, with the document melt was given."""
    made = root / name
    made.mkdir(parents=True)
    (made / "timeline.mlt").write_text("<mlt/>", encoding="utf-8")
    when = time.time() - age_days * 86400
    os.utime(made, (when, when))
    return made


def test_sweep_scratch_drops_only_what_is_old(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "proofcut-render"
    monkeypatch.setattr(picture, "RENDER_SCRATCH", root)
    old = _staged(root, "timeline-abcd1234", age_days=30)
    recent = _staged(root, "render-0zx9_qq1", age_days=1)

    swept = picture.sweep_scratch()

    assert swept == [old]
    assert not old.exists()
    assert recent.exists(), "a directory inside the retention is evidence, not litter"


def test_sweep_scratch_never_touches_a_named_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard that matters: the sweep runs unattended inside someone's render.

    `kf-manual` and `kf-mini` are real directories a person put in the scratch
    root by hand (CLAUDE.md § The keyframed move). Age alone would take them.
    """
    root = tmp_path / "proofcut-render"
    monkeypatch.setattr(picture, "RENDER_SCRATCH", root)
    kept = [
        _staged(root, "kf-manual", age_days=400),
        _staged(root, "kf-mini", age_days=400),
        _staged(root, "timeline-notmkdtemp", age_days=400),
        _staged(root, "render-ABCD1234", age_days=400),
    ]

    assert picture.sweep_scratch() == []
    assert all(d.exists() for d in kept)


def test_sweep_scratch_is_quiet_with_no_scratch_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "never-made")
    assert picture.sweep_scratch() == []


def test_scratch_sweeps_as_it_makes_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "proofcut-render"
    monkeypatch.setattr(picture, "RENDER_SCRATCH", root)
    root.mkdir()
    old = _staged(root, "timeline-abcd1234", age_days=30)

    made = picture.scratch("timeline-")

    assert made.is_dir()
    assert not old.exists()


def test_sweep_scratch_does_not_follow_a_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`Path.is_dir()` follows symlinks — the same trap the `--root` picker had."""
    root = tmp_path / "proofcut-render"
    root.mkdir()
    outside = _staged(tmp_path / "elsewhere", "real", age_days=400)
    link = root / "timeline-abcd1234"
    link.symlink_to(outside, target_is_directory=True)
    # The link itself aged too, so an `lstat`-based sweep would be tempted
    # as well — where the OS lets a link's own mtime be set at all, which
    # Windows does not (`utime: follow_symlinks unavailable`). The sweep ages
    # by `stat()`, the target's 400 days, so the guard is exercised either way.
    if os.utime in os.supports_follow_symlinks:
        os.utime(link, (time.time() - 400 * 86400, time.time() - 400 * 86400), follow_symlinks=False)
    monkeypatch.setattr(picture, "RENDER_SCRATCH", root)

    assert picture.sweep_scratch() == []
    assert link.is_symlink()
    assert (outside / "timeline.mlt").exists(), "the sweep read through the link"
