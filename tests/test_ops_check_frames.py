"""`check_frames` counts a render against the rate it was laid on.

COMPETITORS.md § FilmCraft, run against `verify`: a VO project defaulted to
24 fps, and FilmCraft's 1000 fps render of its own exact cut read 11,717
frames off. A render that declares a constant rate is counted against that
rate, with a note; an explicit `fps` still wins.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from proofcut import media, ops

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="the fixtures need ffmpeg and ffprobe",
)


def _video(path: Path, rate: int, seconds: float = 2.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate={rate}:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    ops.init(root)
    ops.import_media(root, _video(tmp_path / "src.mp4", 30), clip_id="src")
    ops.seed_timeline(root, "src", remove_silences=False)
    return root


@needs_ffmpeg
def test_a_render_at_another_rate_is_counted_against_its_own(seeded: Path, tmp_path: Path) -> None:
    render = _video(tmp_path / "at60.mp4", 60)

    result = ops.check_frames(seeded, render)

    assert result["fps"] == 60.0
    assert result["fps_source"] == "render"
    assert result["expected_frames"] == 120
    assert result["agrees"] is True
    assert "runs at 60 fps, not the 30 fps" in result["notes"][0]


@needs_ffmpeg
def test_an_explicit_fps_still_wins(seeded: Path, tmp_path: Path) -> None:
    render = _video(tmp_path / "at60.mp4", 60)

    result = ops.check_frames(seeded, render, fps=30)

    assert result["fps_source"] == "argument"
    assert result["delta"] == 60
    assert "notes" not in result


@needs_ffmpeg
def test_a_render_at_the_export_rate_carries_no_note(seeded: Path, tmp_path: Path) -> None:
    result = ops.check_frames(seeded, _video(tmp_path / "at30.mp4", 30))

    assert result["fps_source"] == "export"
    assert result["agrees"] is True
    assert "notes" not in result


@needs_ffmpeg
def test_a_variable_rate_render_keeps_the_export_rate_and_says_so(
    seeded: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    render = _video(tmp_path / "at30.mp4", 30)
    real = media.count_frames

    def variable(path: Any) -> dict[str, Any]:
        return {**real(path), "fps": None}

    monkeypatch.setattr(media, "count_frames", variable)
    result = ops.check_frames(seeded, render)

    assert result["fps"] == 30.0
    assert result["fps_source"] == "export"
    assert "frame rate is variable" in result["notes"][0]


def test_count_frames_reads_a_rate_only_when_it_is_constant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clip = tmp_path / "x.mp4"
    clip.write_bytes(b"")

    def probe(rates: tuple[str, str]) -> Any:
        def fake(_path: Any, *_args: Any) -> dict[str, Any]:
            return {
                "streams": [{"nb_read_packets": "10", "r_frame_rate": rates[0], "avg_frame_rate": rates[1]}],
                "format": {"duration": "1.0"},
            }
        return fake

    monkeypatch.setattr(media, "_ffprobe", probe(("30000/1001", "30000/1001")))
    assert media.count_frames(clip)["fps"] == pytest.approx(29.97, abs=0.001)
    monkeypatch.setattr(media, "_ffprobe", probe(("30/1", "28763/1000")))
    assert media.count_frames(clip)["fps"] is None
    monkeypatch.setattr(media, "_ffprobe", probe(("0/0", "0/0")))
    assert media.count_frames(clip)["fps"] is None
