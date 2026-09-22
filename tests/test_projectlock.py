"""The project lock, docs/plans/PROJECT-LOCK.md § Test plan.

Timing is injected — the observation window, the idle clock — so nothing
waits more than a fraction of a second, and every assertion is about the
property (one holder, a refusal, a release) rather than a timing margin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from proofcut import ops, projectlock
from proofcut.projectlock import LockLostError, ProjectLockedError


@pytest.fixture(autouse=True)
def _clean_state():
    projectlock.release_all()
    state = projectlock._state
    state.released.clear()
    state.clock = time.monotonic
    state.idle_seconds = projectlock.IDLE_SECONDS
    yield
    projectlock.release_all()
    state.clock = time.monotonic
    state.idle_seconds = projectlock.IDLE_SECONDS


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    ops.init(str(root))
    return root.resolve()


def _owner(root: Path, **fields: object) -> dict:
    """Plant a lock by hand, as another session would have left it."""
    owner = {
        "token": "planted",
        "pid": os.getpid(),
        **projectlock.machine(),
        "started": "2026-09-22T00:00:00",
        "command": "planted",
        **fields,
    }
    directory = projectlock.lock_dir(root)
    directory.mkdir(parents=True)
    (directory / projectlock.OWNER_NAME).write_text(json.dumps(owner))
    (directory / projectlock.HEARTBEAT_NAME).write_text("0")
    return owner


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


# The holder prints its verdict, then its own pid: on Windows a venv's
# python.exe is a launcher that runs the interpreter as a child, so
# Popen.pid is not the process that takes the lock.
_HOLDER = textwrap.dedent(
    """
    import os
    import sys
    from proofcut import projectlock
    try:
        projectlock.ensure_held(sys.argv[1], heartbeat=False)
    except projectlock.ProjectLockedError:
        print("refused", flush=True)
    else:
        print("held", flush=True)
    print(os.getpid(), flush=True)
    sys.stdin.read()
    projectlock.release_all()
    """
)


def _spawn_holder(root: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


def test_a_directory_cannot_be_renamed_onto_a_populated_one(tmp_path: Path) -> None:
    """The primitive the whole design rests on, on every OS CI runs."""
    first, second = tmp_path / "a", tmp_path / "b"
    for directory in (first, second):
        directory.mkdir()
        (directory / "owner.json").write_text(directory.name)
    with pytest.raises(OSError):
        os.rename(first, second)
    assert (second / "owner.json").read_text() == "b"


def test_acquire_then_release_leaves_nothing(project: Path) -> None:
    projectlock.ensure_held(project, heartbeat=False)
    assert projectlock.read_owner(project)["pid"] == os.getpid()
    projectlock.ensure_held(project, heartbeat=False)  # idempotent in one process
    projectlock.release_all()
    assert not projectlock.lock_dir(project).exists()
    assert [p.name for p in (project / "cache").iterdir() if p.name.startswith("agent.lock.")] in (
        [],
        [projectlock.LAST_NAME],
    )


def test_a_second_process_is_refused_by_name(project: Path) -> None:
    holder = _spawn_holder(project)
    try:
        assert holder.stdout.readline().strip() == "held"
        holder_pid = int(holder.stdout.readline())
        with pytest.raises(ProjectLockedError) as refused:
            projectlock.ensure_held(project, heartbeat=False)
        message = str(refused.value)
        assert f"pid {holder_pid}" in message
        assert "Do not delete the lock" in message
    finally:
        holder.communicate("")
    # The holder exited cleanly and released: the project is free again.
    projectlock.ensure_held(project, heartbeat=False)


def test_racing_processes_leave_exactly_one_holder(project: Path) -> None:
    racers = [_spawn_holder(project) for _ in range(6)]
    try:
        answers = [racer.stdout.readline().strip() for racer in racers]
    finally:
        for racer in racers:
            racer.communicate("")
    assert sorted(answers) == ["held"] + ["refused"] * 5


def test_a_dead_pid_is_reclaimed_at_once(project: Path) -> None:
    _owner(project, pid=_dead_pid())
    projectlock.ensure_held(project, heartbeat=False)
    assert projectlock.read_owner(project)["pid"] == os.getpid()


def test_a_holder_from_another_boot_is_reclaimed_at_once(project: Path) -> None:
    _owner(project, boot_id="a-boot-that-is-not-this-one")
    projectlock.ensure_held(project, heartbeat=False)
    assert projectlock.read_owner(project)["pid"] == os.getpid()


def test_another_machine_is_never_broken_by_acquisition(project: Path) -> None:
    _owner(project, host="elsewhere", pid=_dead_pid())
    os.utime(projectlock.lock_dir(project) / projectlock.HEARTBEAT_NAME, (0, 0))
    with pytest.raises(ProjectLockedError):
        projectlock.acquire(project, command="test", observe_seconds=0.05)
    assert projectlock.read_owner(project)["token"] == "planted"


def test_unlock_breaks_another_machine_only_when_its_heartbeat_stopped(project: Path) -> None:
    _owner(project, host="elsewhere")
    out = projectlock.unlock(project, observe_seconds=0.05)
    assert out["broken"] is True and out["heartbeat"] == "stopped"
    assert not projectlock.lock_dir(project).exists()


def test_unlock_refuses_a_live_holder_without_force(project: Path) -> None:
    holder = _spawn_holder(project)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(ProjectLockedError, match="--force"):
            projectlock.unlock(project)
        forced = projectlock.unlock(project, force=True)
        assert forced["broken"] is True and forced["forced"] is True
    finally:
        holder.communicate("")


def test_a_live_pid_whose_heartbeat_froze_is_stale_by_observation(project: Path) -> None:
    """A reused pid reads alive; nothing is advancing its counter."""
    _owner(project)  # this process's own, live pid — under a token it never took
    os.utime(projectlock.lock_dir(project) / projectlock.HEARTBEAT_NAME, (0, 0))
    projectlock.acquire(project, command="test", observe_seconds=0.05)
    assert projectlock.read_owner(project)["token"] != "planted"


def test_a_live_holder_with_a_fresh_heartbeat_is_never_watched(project: Path) -> None:
    _owner(project)
    started = time.monotonic()
    with pytest.raises(ProjectLockedError):
        projectlock.acquire(project, command="test", observe_seconds=5)
    assert time.monotonic() - started < 2


def test_of_two_breakers_one_wins_and_a_fresh_owner_is_put_back(project: Path) -> None:
    _owner(project, pid=_dead_pid())
    assert projectlock._break(project, "planted") is True
    assert projectlock._break(project, "planted") is False
    fresh = _owner(project, token="fresh")
    # A breaker that judged the old owner and then found a fresh one moved.
    assert projectlock._break(project, "planted") is False
    assert projectlock.read_owner(project)["token"] == fresh["token"]


def test_a_holder_whose_lock_was_removed_refuses_once(project: Path) -> None:
    projectlock.ensure_held(project, heartbeat=False)
    import shutil

    shutil.rmtree(projectlock.lock_dir(project))
    with pytest.raises(LockLostError, match="Nothing was written"):
        projectlock.ensure_held(project, heartbeat=False)
    projectlock.ensure_held(project, heartbeat=False)
    assert projectlock.read_owner(project)["pid"] == os.getpid()


def test_idle_release_and_the_notice_on_taking_it_back(project: Path) -> None:
    now = [0.0]
    projectlock._state.clock = lambda: now[0]
    projectlock.ensure_held(project, heartbeat=False)
    now[0] = projectlock.IDLE_SECONDS - 1
    projectlock.tick()
    assert projectlock.lock_dir(project).exists()
    now[0] = 2 * projectlock.IDLE_SECONDS
    projectlock.tick()
    assert not projectlock.lock_dir(project).exists()
    # Nobody else wrote: no notice.
    assert projectlock.ensure_held(project, heartbeat=False) is None

    now[0] = 4 * projectlock.IDLE_SECONDS
    projectlock.tick()
    other = projectlock.acquire(project, command="another session")
    projectlock.release(project, other)
    notice = projectlock.ensure_held(project, heartbeat=False)
    assert notice is not None and "re-read" in notice


def test_the_liveness_probe_never_kills_what_it_asks_about() -> None:
    """The regression test for `os.kill` on Windows, which terminates."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert projectlock.pid_alive(child.pid) is True
        time.sleep(0.2)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()
    assert projectlock.pid_alive(child.pid) is False


def test_litter_from_dead_sessions_is_swept_and_the_lock_is_not(project: Path) -> None:
    live = _owner(project)
    litter = project / "cache" / "agent.lock.deadbeef.new"
    litter.mkdir()
    (litter / projectlock.OWNER_NAME).write_text(
        json.dumps({**live, "token": "deadbeef", "pid": _dead_pid()})
    )
    assert projectlock.sweep(project) == ["agent.lock.deadbeef.new"]
    assert projectlock.read_owner(project)["token"] == "planted"


def test_holder_reports_stale_only_on_same_machine_evidence(project: Path) -> None:
    _owner(project, pid=_dead_pid())
    assert projectlock.holder(project)["stale"] is True
    assert projectlock.break_stale(project)["broken"] is True
    _owner(project, host="elsewhere")
    held = projectlock.holder(project)
    assert held["stale"] is False and held["foreign"] is True
    with pytest.raises(ProjectLockedError):
        projectlock.break_stale(project)


def test_the_server_wrapper_locks_writers_and_never_readers(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every write goes through the lock and no read does — checked against
    the registry, so a new tool is covered the day it is classified."""
    from proofcut import server

    monkeypatch.setattr(server, "_LOCKING", True)
    server.timeline_status(path=str(project))
    assert not projectlock.lock_dir(project).exists()
    server.migrate_project(path=str(project), plan=True)
    assert projectlock.read_owner(project)["pid"] == os.getpid()

    unwrapped = [
        tool.name
        for tool in server.mcp._tool_manager.list_tools()
        if server._ANNOTATIONS[tool.name] is not server._READ
        and "path" in tool.parameters.get("properties", {})
        and not hasattr(tool.fn, "__wrapped__")
    ]
    assert unwrapped == []


def test_init_takes_the_lock_after_it_makes_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from proofcut import server

    monkeypatch.setattr(server, "_LOCKING", True)
    root = (tmp_path / "fresh").resolve()
    server.init(path=str(root))
    assert projectlock.read_owner(root)["pid"] == os.getpid()


def test_the_cli_warns_when_it_changes_a_held_project(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from proofcut import cli

    holder = _spawn_holder(project)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert cli.main(["-C", str(project), "status"]) == 0
        assert "warning" not in capsys.readouterr().err
        cli.main(["-C", str(project), "canvas", "1080x1920"])
        assert "an agent session holds this project" in capsys.readouterr().err
    finally:
        holder.communicate("")


def test_the_trial_refuses_to_prepare_a_project_an_agent_still_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TRIAL.md § 4, caught by the product: a killed harness's `claude` edits
    on through its MCP server, whose pid is alive — so `prepare` must not
    delete the project out from under it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import agent_trial

    work = tmp_path / "work"
    project = work / "proj"
    ops.init(str(project))
    source = tmp_path / "footage"
    source.mkdir()
    monkeypatch.setattr(agent_trial, "check_source", lambda _source: None)
    holder = _spawn_holder(project.resolve())
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(agent_trial.TrialError, match="still holds"):
            agent_trial.prepare(work, fresh=True, source=source)
        assert (project / "proofcut.json").exists()
    finally:
        holder.communicate("")
    agent_trial.prepare(work, fresh=True, source=source)
