"""`proofcut web` on a port something else already holds.

The failure this guards: an older `proofcut web` kept answering on the port,
the new one died with `OSError: Address already in use` in its own log only,
and a backgrounded launch still read as a success (wiki troubleshooting.md,
2026-09-20). It must instead say which port, and exit non-zero.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from proofcut import webui
from proofcut.cli import main
from proofcut.project import Project


@pytest.fixture
def held_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        yield sock.getsockname()[1]


def test_make_server_names_a_held_port(tmp_path: Path, held_port: int) -> None:
    project = Project.create(tmp_path / "proj")
    with pytest.raises(webui.PortInUseError, match=f"port {held_port}"):
        webui.make_server(project.root, host="127.0.0.1", port=held_port)


def test_make_picker_server_names_a_held_port(tmp_path: Path, held_port: int) -> None:
    with pytest.raises(webui.PortInUseError, match=f"port {held_port}"):
        webui.make_picker_server(tmp_path, host="127.0.0.1", port=held_port)


def test_cli_web_exits_1_with_one_line(
    tmp_path: Path, held_port: int, capsys: pytest.CaptureFixture[str]
) -> None:
    project = Project.create(tmp_path / "proj")
    rc = main(["-C", str(project.root), "web", "--host", "127.0.0.1", "--port", str(held_port)])
    err = capsys.readouterr().err
    assert rc == 1
    assert f"port {held_port}" in err
    assert "Traceback" not in err
