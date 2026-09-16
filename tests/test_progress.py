"""Progress: the reporter, the streaming runner, and the two sources parsed off a pipe.

The MCP half — a report arriving as `notifications/progress` before the reply —
is `test_server_stdio.py`'s, over the real transport. This file holds the
pieces under it, with real child processes rather than a patched
`subprocess.run`, because what is under test is reading a pipe while the
child is still writing to it. docs/plans/MCP.md § Step 6.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from stubs import write_stub

from proofcut import media, picture, progress

Report = tuple[float, float | None, str | None]


def _collect() -> tuple[list[Report], progress.Reporter]:
    got: list[Report] = []
    return got, lambda current, total, message: got.append((current, total, message))


def test_nobody_listening_is_a_no_op() -> None:
    assert not progress.active()
    progress.report(1, 2, "ignored")


def test_reports_are_throttled_but_a_finish_always_gets_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 0.1, 0.2, 5.0, 5.1])
    monkeypatch.setattr(progress.time, "monotonic", lambda: next(clock))
    got, reporter = _collect()
    with progress.reporting(reporter):
        assert progress.active()
        progress.report(0, 10, "a")  # 0.0 — the first always goes
        progress.report(1, 10, "b")  # 0.1 — too soon
        progress.report(10, 10, "c")  # 0.2 — finished, goes anyway
        progress.report(2, 10, "d")  # 5.0 — long enough
        progress.report(3, 10, "e")  # 5.1 — too soon
    assert [message for _, _, message in got] == ["a", "c", "d"]
    assert not progress.active()


def test_a_listener_that_fails_is_dropped_and_the_work_goes_on() -> None:
    calls = []

    def broken(*args: object) -> None:
        calls.append(args)
        raise RuntimeError("client went away")

    with progress.reporting(broken):
        progress.report(10, 10)
        progress.report(20, 20)
    assert len(calls) == 1


def test_run_streams_both_pipes_and_returns_what_subprocess_run_would() -> None:
    """Lines arrive while the child is still running — the second is printed
    only after the first has been read — and a carriage return ends a line,
    as melt's redrawn counter needs. The returned streams fold `\\r\\n` as
    `text=True` does and keep a lone `\\r`. The child writes bytes, since a
    text stdout on Windows would turn its `\\r\\n` into `\\r\\r\\n` first."""
    child = (
        "import sys, time\n"
        "sys.stdout.buffer.write(b'out one\\r\\nout two\\n'); sys.stdout.buffer.flush()\n"
        "sys.stderr.write('tick 1\\rtick 2\\r'); sys.stderr.flush()\n"
        "time.sleep(0.3)\n"
        "sys.stderr.write('tail'); sys.exit(3)\n"
    )
    out_lines: list[str] = []
    err_lines: list[str] = []
    completed = progress.run(
        [sys.executable, "-c", child], on_stdout=out_lines.append, on_stderr=err_lines.append
    )
    assert completed.returncode == 3
    assert completed.stdout == "out one\nout two\n"
    assert completed.stderr.startswith("tick 1\rtick 2\r")
    assert out_lines == ["out one", "out two"]
    assert err_lines == ["tick 1", "tick 2", "tail"]


def test_run_kills_a_child_past_its_timeout() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        progress.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)


def test_run_with_check_raises_as_subprocess_run_does() -> None:
    with pytest.raises(subprocess.CalledProcessError) as raised:
        progress.run([sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(2)"], check=True)
    assert raised.value.returncode == 2
    assert raised.value.stderr == "bad"


def _alive(pid: int) -> bool:
    """Running, as opposed to gone or a zombie waiting on whoever reaps it."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads /proc to see the grandchild")
def test_a_stop_kills_the_child_and_everything_it_started(tmp_path: Path) -> None:
    """melt runs under `systemd-run` → `nice` → `flatpak run`, so killing the
    direct child alone leaves the encode running. The kill takes the group."""
    pid_file = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )
    stop = threading.Event()
    started = time.monotonic()

    def stop_once_spawned() -> None:
        while not pid_file.exists() or not pid_file.read_text():
            time.sleep(0.02)
        stop.set()

    threading.Thread(target=stop_once_spawned, daemon=True).start()
    with progress.cancellable(stop), pytest.raises(progress.Cancelled):
        progress.run([sys.executable, "-c", child])

    assert time.monotonic() - started < 10
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild)


def test_a_job_already_stopped_spawns_nothing(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    stop = threading.Event()
    stop.set()
    with progress.cancellable(stop), pytest.raises(progress.Cancelled):
        progress.run([sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    assert not marker.exists()


def test_a_workers_marker_lines_are_the_only_ones_reported() -> None:
    got, reporter = _collect()
    child = (
        "import sys\n"
        "for line in ('loading weights', 'proofcut-progress 1 2', 'noise 3 4', 'proofcut-progress 2 2'):\n"
        "    sys.stderr.write(line + '\\n')\n"
    )
    with progress.reporting(reporter):
        completed = progress.run_worker([sys.executable, "-c", child], "describing footage")
    assert completed.returncode == 0
    assert (2.0, 2.0, "describing footage") in got
    assert all(message == "describing footage" for _, _, message in got)
    assert {current for current, _, _ in got} <= {0.0, 1.0, 2.0}


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


@pytest.fixture
def stub_melt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A melt that redraws its frame counter the way `-progress` makes the real
    one do, records its argv, and writes the file it was asked for."""
    argv = tmp_path / "argv.txt"
    stub = write_stub(
        tmp_path / "fake-melt",
        "import sys, time\n"
        "from pathlib import Path\n"
        f"Path({str(argv)!r}).write_text('\\n'.join(sys.argv[1:]))\n"
        "target = next(a for a in sys.argv if a.startswith('avformat:'))[len('avformat:'):]\n"
        "for frame in (0, 60, 149):\n"
        "    sys.stderr.write(f'Current Frame:    {frame}, percentage:   {frame * 100 // 150}\\r')\n"
        "    sys.stderr.flush()\n"
        "    time.sleep(0.2)\n"
        "Path(target).write_bytes(b'a render')\n",
    )
    monkeypatch.setattr(sys, "platform", "linux")
    # Quoted: the platform is pinned to linux, so the value is split POSIX-style,
    # which eats a Windows path's backslashes.
    monkeypatch.setenv("PROOFCUT_MELT", shlex.quote(str(stub)))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(picture.shutil, "which", lambda name: None)
    monkeypatch.setattr(picture.media, "probe", lambda p: _PROBE)
    monkeypatch.setattr(
        picture.media,
        "count_frames",
        lambda p: {"frames": 150, "container_frames": 150, "duration": 5.0, "has_video": True},
    )
    monkeypatch.setattr(progress, "MIN_INTERVAL", 0.0)
    return argv


def test_a_render_reports_melts_frame_counter(stub_melt: Path, tmp_path: Path) -> None:
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")
    got, reporter = _collect()

    with progress.reporting(reporter):
        result = picture.render(project, tmp_path / "out.mp4", expect_frames=150)

    assert result["agrees"] is True
    assert "-progress" in stub_melt.read_text(encoding="utf-8").splitlines()
    assert (60.0, 150.0, "rendering out.mp4") in got
    assert (149.0, 150.0, "rendering out.mp4") in got
    assert got[-1] == (150.0, 150.0, "rendered out.mp4")


def test_a_render_nobody_watches_asks_melt_for_no_counter(stub_melt: Path, tmp_path: Path) -> None:
    """The unwatched render is the command it always was — `-progress` is only
    added when someone will read it."""
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    picture.render(project, tmp_path / "out.mp4", expect_frames=150)

    assert "-progress" not in stub_melt.read_text(encoding="utf-8").splitlines()


def test_a_stopped_render_leaves_no_staging_directory(stub_melt: Path, tmp_path: Path) -> None:
    """Nobody listens, and the render still goes through `progress.run` — a
    Stop is enough reason. A failed render keeps its staging directory to be
    looked at; a stopped one has nothing to show."""
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()

    with progress.cancellable(stop), pytest.raises(progress.Cancelled):
        picture.render(project, tmp_path / "out.mp4", expect_frames=150)

    assert not (tmp_path / "out.mp4").exists()
    scratch = tmp_path / "scratch"
    assert not scratch.exists() or not any(scratch.iterdir())
