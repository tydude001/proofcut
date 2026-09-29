"""A render's document names no numeric locale, and melt reads numbers as C.

HISTORY.md § The Mac melt crash, found: a root `LC_NUMERIC` gives every
service a locale, and MLT then swaps the process locale with `setlocale`
around each number it prints, which a render thread on macOS reads freed.
What a real melt does with the stripped document was measured against a real
render (the same frames and audio as the original); these pin the plumbing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from proofcut import media, picture

_DOC = (
    b"<?xml version='1.0' encoding='utf-8'?>\n"
    b'<mlt LC_NUMERIC="C" version="7.22.0" producer="main_bin">\n'
    b'  <producer id="p"><property name="LC_NUMERIC">kept</property></producer>\n'
    b"</mlt>\n"
)

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


class _ReadingMelt:
    """A melt that keeps the document and environment it was handed."""

    def __init__(self) -> None:
        self.document = b""
        self.document_path: Path | None = None
        self.env: dict[str, str] = {}

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        target = next(a for a in command if a.startswith("avformat:"))
        self.document_path = Path(command[command.index(target) - 2])
        self.document = self.document_path.read_bytes()
        self.env = dict(kwargs["env"])  # type: ignore[arg-type]
        Path(target.removeprefix("avformat:")).write_bytes(b"a render")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_the_copy_drops_only_the_roots_locale(tmp_path: Path) -> None:
    original = tmp_path / "timeline.mlt"
    original.write_bytes(_DOC)

    copy = picture.render_document(original)

    assert copy.parent == tmp_path and copy.suffix == ".mlt" and copy != original
    assert copy.read_bytes() == _DOC.replace(b' LC_NUMERIC="C"', b"", 1)
    assert original.read_bytes() == _DOC


def test_a_document_with_no_locale_is_rendered_as_it_is(tmp_path: Path) -> None:
    original = tmp_path / "timeline.mlt"
    original.write_bytes(b'<mlt version="7.22.0"><producer id="p"/></mlt>')

    assert picture.render_document(original) == original
    assert list(tmp_path.iterdir()) == [original]


def test_all_of_the_users_locale_survives_but_its_numbers() -> None:
    env = picture.numeric_c_env({"LC_ALL": "de_DE.UTF-8", "LANG": "en_US.UTF-8", "PATH": "/bin"})

    assert "LC_ALL" not in env
    assert env["LC_NUMERIC"] == "C"
    assert env["LC_CTYPE"] == env["LC_TIME"] == "de_DE.UTF-8"
    assert env["LANG"] == "en_US.UTF-8" and env["PATH"] == "/bin"
    assert picture.numeric_c_env({"LANG": "de_DE.UTF-8"}) == {
        "LANG": "de_DE.UTF-8",
        "LC_NUMERIC": "C",
    }


def test_render_hands_melt_the_copy_and_a_c_numeric_env_then_drops_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _ReadingMelt()
    monkeypatch.setenv("PROOFCUT_MELT", "melt")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(picture.subprocess, "run", fake)
    monkeypatch.setattr(picture.media, "probe", lambda p: _PROBE)
    monkeypatch.setattr(
        picture.media,
        "count_frames",
        lambda p: {"frames": 150, "container_frames": 150, "duration": 5.0, "has_video": True},
    )
    project = tmp_path / "proj"
    project.mkdir()
    original = project / "timeline.mlt"
    original.write_bytes(_DOC)

    picture.render(original, tmp_path / "out.mp4", max_memory=None)

    assert fake.document_path is not None and fake.document_path.parent == project
    assert b' LC_NUMERIC="C"' not in fake.document
    assert b'<property name="LC_NUMERIC">kept</property>' in fake.document
    assert fake.env["LC_NUMERIC"] == "C" and "LC_ALL" not in fake.env
    assert list(project.iterdir()) == [original]
