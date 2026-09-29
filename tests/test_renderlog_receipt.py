"""The render log's receipt half: which bytes a run wrote, and with what.

`sources` (test_renderlog_writers.py) says which edit a render read. These pin
the rest — `output_sha256` and `tools` — without rendering: the output is a
stub file and the tools are stub binaries on PATH.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from stubs import write_stub

from proofcut import __version__, picture, renderlog
from proofcut.project import Project


@pytest.fixture(autouse=True)
def _fresh_probes() -> None:
    renderlog._melt.cache_clear()
    renderlog._ffmpeg.cache_clear()
    yield
    renderlog._melt.cache_clear()
    renderlog._ffmpeg.cache_clear()


def _append(project: Project, output: Path) -> dict:
    renderlog.append(project, output=str(output), preset=None, expected_duration=1.0, stages={})
    run = renderlog.last(project)
    assert run is not None
    return run


def test_a_line_names_the_bytes_it_wrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderlog, "tools", dict)
    project = Project.create(tmp_path / "proj")
    out = tmp_path / "film.mp4"
    out.write_bytes(b"a render")

    assert _append(project, out)["output_sha256"] == hashlib.sha256(b"a render").hexdigest()
    assert _append(project, tmp_path / "gone.mp4")["output_sha256"] is None


def test_the_tools_are_named_by_their_own_banners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir / "ffmpeg", "print('ffmpeg version 9.9-test Copyright (c) the FFmpeg developers')\n")
    melt = write_stub(bin_dir / "melt", "print('melt 7.99.0')\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(picture, "melt_command", lambda: [str(melt)])

    assert renderlog.tools() == {"proofcut": __version__, "melt": "melt 7.99.0", "ffmpeg": "9.9-test"}


def test_a_missing_tool_is_none_and_the_line_is_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_melt() -> list[str]:
        raise picture.PictureError("melt not found")

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(picture, "melt_command", no_melt)
    project = Project.create(tmp_path / "proj")

    assert _append(project, tmp_path / "film.mp4")["tools"] == {
        "proofcut": __version__,
        "melt": None,
        "ffmpeg": None,
    }
