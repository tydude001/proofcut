"""One agent session per project: the project lock.

docs/plans/PROJECT-LOCK.md is the design and the reasons; this module is it,
and knows nothing about MCP. `server._tool` calls `ensure_held` before every
tool that is not read-only, so the lock is held by the process that writes —
the MCP server — never by whatever started it.

The lock is `<project>/cache/agent.lock/`, a directory written in full under
a staging name and moved into place with `os.rename`. **A rename onto an
existing non-empty directory fails on every OS and share proofcut runs on**,
so the move is an exclusive create, and no reader ever sees a lock without
its `owner.json`. Everything else follows from three rules:

- **A clock prompts a check and never decides one.** Staleness is a dead pid
  or a different boot on this machine; a heartbeat *counter* that stops
  advancing while watched is the only other evidence, and it is gathered only
  when the heartbeat file's age already looks wrong.
- **Lazy acquisition breaks a lock only on certain same-machine evidence.** A
  holder on another machine always needs `proofcut unlock`, which is never an
  MCP tool: an agent able to break another agent's lock is the problem again.
- **Every race the lock does not prevent loses cleanly.** A holder checks its
  fencing token before every write, so a lock broken or deleted under it is a
  refusal, never a silent second writer; `Project._manifest_stamp` stays as the
  per-write layer beneath it.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from proofcut.project import CACHE_DIR, ProjectError

LOCK_NAME = "agent.lock"
OWNER_NAME = "owner.json"
HEARTBEAT_NAME = "heartbeat"
#: The token of the last session that released this project, so a session
#: taking it back after an idle release can tell whether anyone wrote between.
LAST_NAME = "agent.lock.last"

#: How often a holder advances its heartbeat counter.
HEARTBEAT_SECONDS = 15.0
#: How long the counter is watched before it is called dead.
OBSERVE_SECONDS = 3 * HEARTBEAT_SECONDS
#: A heartbeat file older than this by the local clock *prompts* the watch.
SUSPECT_SECONDS = 120.0
#: A holder with no mutating call for this long releases its own lock
#: (Tyler, 2026-09-22: yes, and ten minutes).
IDLE_SECONDS = 600.0
#: Staging and broken directories older than this are swept whatever they say.
LITTER_SECONDS = 86400.0

_DO_NOT_DELETE = (
    "Do not delete the lock — stop and tell the user; they can run "
    "`proofcut unlock` if that session is dead."
)


class ProjectLockedError(ProjectError):
    """Another live session holds the project. A `ProjectError`, so every
    existing handler treats it as a refusal; deliberately not a
    `ProjectConflictError`, because "refused before reading" and "stale
    write" are different facts."""


class LockLostError(ProjectError):
    """This session held the project and no longer does — the lock was broken
    or deleted under it. Raised once, instead of writing."""


# -- machine identity and liveness --------------------------------------------


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text().strip() or None
    except OSError:
        return None


def machine() -> dict[str, str | None]:
    """What makes a pid meaningful: the host, the boot (Linux), and the pid
    namespace (Linux) — a pid from another container means nothing here."""
    pid_ns = None
    with contextlib.suppress(OSError):
        pid_ns = os.readlink("/proc/self/ns/pid")
    return {
        "host": socket.gethostname(),
        "boot_id": _read_text("/proc/sys/kernel/random/boot_id"),
        "pid_ns": pid_ns,
    }


def pid_alive(pid: int) -> bool:
    """Whether `pid` names a running process on this machine.

    **Never `os.kill` on Windows**: there, any signal but the two console
    events calls `TerminateProcess`, so the probe kills what it asks about —
    the defect in `agent_trial.py`'s first `hold_lock`.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _win_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # Access denied is a process that exists and is not ours to query.
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


# -- the files ----------------------------------------------------------------


def lock_dir(root: str | Path) -> Path:
    return Path(root) / CACHE_DIR / LOCK_NAME


def _read_owner(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / OWNER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_owner(root: str | Path) -> dict[str, Any] | None:
    """The holder's `owner.json`, or None when nothing holds the project."""
    return _read_owner(lock_dir(root))


def _read_counter(directory: Path) -> int | None:
    try:
        return int((directory / HEARTBEAT_NAME).read_text().strip())
    except (OSError, ValueError):
        return None


def _write_counter(directory: Path, value: int, token: str) -> None:
    staged = directory / f"{HEARTBEAT_NAME}.{token}.tmp"
    staged.write_text(str(value))
    os.replace(staged, directory / HEARTBEAT_NAME)


def _heartbeat_age(directory: Path) -> float | None:
    try:
        return time.time() - (directory / HEARTBEAT_NAME).stat().st_mtime
    except OSError:
        return None


def _same_machine(owner: dict[str, Any], me: dict[str, str | None]) -> bool:
    return all(owner.get(key) == me[key] for key in ("host", "boot_id", "pid_ns"))


# -- judging a holder ---------------------------------------------------------


def _observe_dead(directory: Path, observe_seconds: float) -> bool:
    """Watch the counter for `observe_seconds`: unchanged is dead. The only
    staleness evidence that needs no pid and trusts nobody's clock — and it
    also catches a reused pid, which reads alive while nothing counts."""
    before = _read_counter(directory)
    time.sleep(observe_seconds)
    return _read_counter(directory) == before


def judge(
    owner: dict[str, Any],
    directory: Path,
    *,
    observe_seconds: float | None = None,
    suspect_seconds: float | None = None,
) -> str:
    """`"dead"`, `"live"` or `"foreign"` (another machine; never auto-broken).

    Rules in order, PROJECT-LOCK.md § Expiry: same host with a different boot
    is dead; same machine with a dead pid is dead; same machine and alive is
    live unless its heartbeat file looks old *and* its counter then does not
    move while watched.
    """
    observe = OBSERVE_SECONDS if observe_seconds is None else observe_seconds
    suspect = SUSPECT_SECONDS if suspect_seconds is None else suspect_seconds
    me = machine()
    if owner.get("host") == me["host"] and owner.get("boot_id") != me["boot_id"]:
        return "dead"
    if not _same_machine(owner, me):
        return "foreign"
    pid = owner.get("pid")
    if not isinstance(pid, int) or not pid_alive(pid):
        return "dead"
    age = _heartbeat_age(directory)
    if age is not None and age > suspect and _observe_dead(directory, observe):
        return "dead"
    return "live"


def _describe(owner: dict[str, Any]) -> str:
    return (
        f"pid {owner.get('pid')}, `{owner.get('command') or 'proofcut'}`, "
        f"host {owner.get('host')}, since {owner.get('started')}"
    )


def refusal(owner: dict[str, Any]) -> str:
    return f"Another proofcut session holds this project ({_describe(owner)}). {_DO_NOT_DELETE}"


# -- taking, breaking and sweeping --------------------------------------------


def _new_owner(token: str, command: str) -> dict[str, Any]:
    return {
        "token": token,
        "pid": os.getpid(),
        **machine(),
        "started": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": command,
    }


def _stage(cache: Path, owner: dict[str, Any]) -> Path:
    staged = cache / f"{LOCK_NAME}.{owner['token']}.new"
    staged.mkdir(parents=True)
    (staged / OWNER_NAME).write_text(json.dumps(owner, indent=2), encoding="utf-8")
    _write_counter(staged, 0, owner["token"])
    return staged


def _break(root: Path, judged_token: Any) -> bool:
    """Move the lock aside, never delete it: of two racing breakers exactly
    one wins the rename. The winner re-reads what it moved, and if a fresh
    owner took the lock in between it puts that one back — and if even that
    loses a race, the displaced owner's fencing check refuses its next write
    rather than letting it write blind. CutPilot's `rmSync` is this race
    with no answer."""
    cache = root / CACHE_DIR
    aside = cache / f"{LOCK_NAME}.broken.{uuid.uuid4().hex}"
    try:
        os.rename(lock_dir(root), aside)
    except OSError:
        return False
    moved = _read_owner(aside)
    if moved is None or moved.get("token") != judged_token:
        with contextlib.suppress(OSError):
            os.rename(aside, lock_dir(root))
        return False
    shutil.rmtree(aside, ignore_errors=True)
    return True


def sweep(root: str | Path) -> list[str]:
    """Remove staging and broken directories nobody can be using: their owner's
    pid is dead on this machine, or they are older than a day. Litter never
    blocks anything — only the exact name `agent.lock` is the lock."""
    cache = Path(root) / CACHE_DIR
    removed: list[str] = []
    try:
        entries = list(cache.iterdir())
    except OSError:
        return removed
    me = machine()
    for entry in entries:
        name = entry.name
        if name == LOCK_NAME or not name.startswith(LOCK_NAME + ".") or not entry.is_dir():
            continue
        owner = _read_owner(entry)
        try:
            old = time.time() - entry.stat().st_mtime > LITTER_SECONDS
        except OSError:
            continue
        dead = (
            owner is not None
            and _same_machine(owner, me)
            and isinstance(owner.get("pid"), int)
            and not pid_alive(owner["pid"])
        )
        if old or dead or name.startswith(f"{LOCK_NAME}.released."):
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(name)
    return removed


def acquire(
    root: str | Path,
    *,
    command: str,
    observe_seconds: float | None = None,
    suspect_seconds: float | None = None,
) -> str:
    """Take the lock and return its token, or raise `ProjectLockedError`.

    A holder that is dead by same-machine evidence is broken on the way; one
    on another machine never is. Not idempotent on its own — `ensure_held` is
    the per-process entry point that remembers what it holds.
    """
    root = Path(root)
    cache = root / CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    sweep(root)
    owner = _new_owner(uuid.uuid4().hex, command)
    staged = _stage(cache, owner)
    try:
        for _attempt in range(20):
            try:
                os.rename(staged, lock_dir(root))
                return owner["token"]
            except OSError:
                pass
            held = read_owner(root)
            if held is None:
                # Mid-release or mid-break: the name is about to be free.
                time.sleep(0.05)
                continue
            verdict = judge(
                held,
                lock_dir(root),
                observe_seconds=observe_seconds,
                suspect_seconds=suspect_seconds,
            )
            if verdict != "dead":
                raise ProjectLockedError(refusal(held))
            _break(root, held.get("token"))
        raise ProjectLockedError(
            "could not take the project lock after repeated races; another session is "
            f"starting in this project. {_DO_NOT_DELETE}"
        )
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def release(root: str | Path, token: str) -> bool:
    """Give the lock up, only if it is still ours. Moved aside first, so no
    reader ever sees a lock directory whose owner file is already gone."""
    root = Path(root)
    held = read_owner(root)
    if held is None or held.get("token") != token:
        return False
    aside = root / CACHE_DIR / f"{LOCK_NAME}.released.{token}"
    try:
        os.rename(lock_dir(root), aside)
    except OSError:
        return False
    shutil.rmtree(aside, ignore_errors=True)
    with contextlib.suppress(OSError):
        (root / CACHE_DIR / LAST_NAME).write_text(token)
    return True


# -- the per-process lease ----------------------------------------------------


@dataclass
class _Lease:
    root: Path
    token: str
    last_write: float
    counter: int = 0


@dataclass
class _State:
    command: str = "proofcut mcp"
    clock: Any = time.monotonic
    idle_seconds: float = IDLE_SECONDS
    leases: dict[Path, _Lease] = field(default_factory=dict)
    #: The token this process last released per project, for the notice.
    released: dict[Path, str] = field(default_factory=dict)
    thread: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)


_state = _State()
_mutex = threading.RLock()


def configure(*, command: str) -> None:
    """Name what holds the lock, for the refusal another session reads."""
    _state.command = command


def ensure_held(root: str | Path, *, heartbeat: bool = True) -> str | None:
    """Hold `root` for this process before a write, or raise.

    Takes the lock if this process does not hold it; if it does, checks the
    fencing token first and raises `LockLostError` (once, dropping the lease)
    when the lock is no longer ours. Returns a notice when this session is
    taking the project back after an idle release and another session wrote
    in between — the caller hands it to the agent, which must re-read.
    """
    root = Path(root).resolve()
    with _mutex:
        lease = _state.leases.get(root)
        if lease is not None:
            held = read_owner(root)
            if held is None or held.get("token") != lease.token:
                del _state.leases[root]
                holder = f" to {_describe(held)}" if held else ""
                raise LockLostError(
                    f"this session lost the project lock{holder} — someone broke or "
                    "removed it. Nothing was written. Re-read the project before "
                    "changing it again."
                )
            lease.last_write = _state.clock()
            return None
        token = acquire(root, command=_state.command)
        _state.leases[root] = _Lease(root, token, _state.clock())
        notice = None
        previous = _state.released.pop(root, None)
        if previous is not None:
            last = _read_text(str(root / CACHE_DIR / LAST_NAME))
            if last != previous:
                notice = (
                    "another session edited this project while this one was idle — "
                    "re-read before continuing"
                )
        if heartbeat:
            _start_heartbeat()
        return notice


def held_roots() -> list[Path]:
    with _mutex:
        return list(_state.leases)


def tick() -> None:
    """One heartbeat round: advance every held counter, and release a lease
    idle past `IDLE_SECONDS` by this process's own monotonic clock. A lease
    whose lock is no longer ours is left for `ensure_held` to report."""
    with _mutex:
        now = _state.clock()
        for root, lease in list(_state.leases.items()):
            held = read_owner(root)
            if held is None or held.get("token") != lease.token:
                continue
            if now - lease.last_write >= _state.idle_seconds:
                release(root, lease.token)
                del _state.leases[root]
                _state.released[root] = lease.token
                continue
            lease.counter += 1
            with contextlib.suppress(OSError):
                _write_counter(lock_dir(root), lease.counter, lease.token)


def _beat() -> None:
    while not _state.stop.wait(HEARTBEAT_SECONDS):
        with contextlib.suppress(Exception):
            tick()


def _start_heartbeat() -> None:
    if _state.thread is not None and _state.thread.is_alive():
        return
    _state.stop.clear()
    _state.thread = threading.Thread(target=_beat, name="proofcut-lock-heartbeat", daemon=True)
    _state.thread.start()


def release_all() -> None:
    """Give up every lock this process holds. `atexit` and the server's own
    exit path call it; a lock that is no longer ours is left alone."""
    with _mutex:
        for root, lease in list(_state.leases.items()):
            with contextlib.suppress(OSError):
                release(root, lease.token)
            del _state.leases[root]
        _state.stop.set()


# -- what a person sees: the holder, the CLI's warning, `unlock` ---------------


def holder(root: str | Path) -> dict[str, Any] | None:
    """Who holds `root`, for a person: the owner record plus `stale` — true
    only on certain same-machine evidence (dead pid, different boot), the one
    case Studio's button and lazy acquisition may break — and `mine`. No
    heartbeat watch: this is read on a refresh, and must not wait."""
    directory = lock_dir(root)
    owner = _read_owner(directory)
    if owner is None:
        return None
    verdict = judge(owner, directory, suspect_seconds=float("inf"))
    with _mutex:
        mine = any(lease.token == owner.get("token") for lease in _state.leases.values())
    return {
        "pid": owner.get("pid"),
        "host": owner.get("host"),
        "command": owner.get("command"),
        "started": owner.get("started"),
        "stale": verdict == "dead",
        "foreign": verdict == "foreign",
        "mine": mine,
    }


def break_stale(root: str | Path) -> dict[str, Any]:
    """Break the lock only if it is stale by same-machine evidence — what
    Studio's button does (Tyler, 2026-09-22: a stale lock only)."""
    root = Path(root)
    owner = read_owner(root)
    if owner is None:
        return {"held": False, "broken": False}
    if judge(owner, lock_dir(root), suspect_seconds=float("inf")) != "dead":
        raise ProjectLockedError(
            f"the session holding this project is not stale ({_describe(owner)}); "
            "only a dead session's lock can be cleared here. `proofcut unlock --force` "
            "breaks a live one from a terminal."
        )
    return {"held": True, "broken": _break(root, owner.get("token")), "holder": owner}


def unlock(
    root: str | Path, *, force: bool = False, observe_seconds: float | None = None
) -> dict[str, Any]:
    """`proofcut unlock`: break a dead session's lock, and a live one only
    under `force`. A holder on another machine is judged by watching its
    heartbeat, since no pid there means anything here."""
    root = Path(root)
    swept = sweep(root)
    directory = lock_dir(root)
    owner = _read_owner(directory)
    if owner is None:
        return {"held": False, "broken": False, "swept": swept}
    verdict = judge(owner, directory, suspect_seconds=float("inf"))
    heartbeat = None
    if verdict == "foreign":
        observe = OBSERVE_SECONDS if observe_seconds is None else observe_seconds
        heartbeat = "stopped" if _observe_dead(directory, observe) else "advancing"
        verdict = "dead" if heartbeat == "stopped" else "live"
    if verdict == "live" and not force:
        raise ProjectLockedError(
            f"a live session holds this project ({_describe(owner)}"
            + (f", heartbeat {heartbeat}" if heartbeat else "")
            + "). Stop it, or pass --force to break the lock anyway; it will then "
            "refuse its next write."
        )
    broken = _break(root, owner.get("token"))
    return {
        "held": True,
        "broken": broken,
        "stale": verdict == "dead",
        "forced": verdict == "live",
        "heartbeat": heartbeat,
        "holder": owner,
        "swept": swept,
    }
