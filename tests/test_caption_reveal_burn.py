"""The caption reveal, judged by the burned picture rather than the `.ass` text.

A test on the subtitle string proves the string (PLAN.md § Per-word caption
animation, the build rule). This burns a two-word line through the same
`captions.burn` route over a flat mid-grey frame — mid-grey, because black on
black reads as "no effect" (finding 6) — and reads the pixels back where
each word sits, before, during and after the second word's reveal.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from proofcut import captions
from proofcut.captions import CueWord


def _has_libass() -> bool:
    """Homebrew's plain `ffmpeg`, which CI's macOS job installs, has no `ass`
    filter — so asking only whether ffmpeg is on PATH ran these there and
    failed them at the burn. Same listing test as doctor's `TEXT_FILTERS`."""
    if shutil.which("ffmpeg") is None:
        return False
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False).stdout
    return re.search(r"^[ \t]*\S+[ \t]+ass[ \t]", listing, re.MULTILINE) is not None


pytestmark = pytest.mark.skipif(not _has_libass(), reason="needs ffmpeg with libass")

W, H = 640, 360
GREY = 128


def _grey_clip(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s={W}x{H}:r=50:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-qp", "0", str(path)],
        check=True,
    )
    return path


def _frame(video: Path, t: float) -> list[bytes]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout
    return [raw[y * W : (y + 1) * W] for y in range(H)]


def _ink(rows: list[bytes], left: bool) -> tuple[int, int]:
    """(pixels off the grey, sharpest horizontal step) in one half of the frame."""
    xs = range(W // 2) if left else range(W // 2, W)
    count = 0
    sharpest = 0
    for row in rows:
        for x in xs:
            if abs(row[x] - GREY) > 12:
                count += 1
            if x + 1 in xs:
                sharpest = max(sharpest, abs(row[x + 1] - row[x]))
    return count, sharpest


def _burn(tmp_path: Path, stored: dict) -> Path:
    style = captions.resolve({"preset": "reveal", "size": 120, **stored})
    cue = captions.group([CueWord("MMM", 0.0, 0.4), CueWord("MMM", 1.0, 1.4)], max_words=2, hold=1.0)
    ass = tmp_path / "c.ass"
    ass.write_text(captions.to_ass(cue, style=style.ass, resolution=captions.canvas(W, H)), encoding="utf-8")
    return captions.burn(_grey_clip(tmp_path / "grey.mp4"), ass, tmp_path / "out.mp4")


def test_a_fade_reveals_each_word_from_its_own_start(tmp_path: Path) -> None:
    out = _burn(tmp_path, {"reveal_ms": 400})
    before, during, after = (_frame(out, t) for t in (0.8, 1.2, 1.7))
    first_before, _ = _ink(before, left=True)
    second_before, _ = _ink(before, left=False)
    second_during, _ = _ink(during, left=False)
    second_after, _ = _ink(after, left=False)
    # The first word is fully in; the second has not begun, though the line
    # is laid out whole — it holds its place invisibly.
    assert first_before > 1000
    assert second_before == 0
    # Halfway through its fade the second word is drawn, and fainter than
    # when it has arrived: fewer pixels clear the threshold.
    assert 0 < second_during < second_after
    # Arrived, it matches the first word it is a copy of.
    assert abs(second_after - _ink(after, left=True)[0]) < 0.05 * second_after


def test_a_blur_reveal_starts_soft_and_ends_sharp(tmp_path: Path) -> None:
    out = _burn(tmp_path, {"reveal": "blur", "reveal_ms": 400, "reveal_blur": 8})
    during, after = (_frame(out, t) for t in (1.15, 1.7))
    _, soft = _ink(during, left=False)
    _, sharp = _ink(after, left=False)
    # libass blurs a glyph's fill only while it has no outline (measured,
    # P2a), which is why the reveal draws one only at its end.
    assert soft < sharp / 3
    assert abs(sharp - _ink(after, left=True)[1]) <= 8
