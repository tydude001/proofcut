"""How far a long operation has got, for whoever is listening.

A reporter is installed around a call — the MCP server's `_tool()` wrapper
turns each report into a `notifications/progress`, and a web UI job into a bus
event — and anything underneath calls `report()` without knowing who, or
whether anyone, is there. A context variable rather than a callback argument
on every op, because the sources are three calls deep (`ops.export` →
`picture.render` → melt's frame counter) and the ops between them have nothing
to say about it.

Why it is worth having: a stdio MCP call with no reply **and no progress** is
aborted by Claude Code after 30 minutes, which a long melt render reaches while
still writing. A progress notification resets that clock. docs/plans/MCP.md
§ Step 6.

`run()` is the one streaming subprocess runner, so a new source of progress
parses lines rather than re-deriving pipe handling. A worker script shipped in
this package reports by printing `MARKER current total` on stderr — restated
in each worker, since they run under another interpreter and import nothing
from proofcut.
"""

from __future__ import annotations

import codecs
import contextlib
import contextvars
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any

Reporter = Callable[[float, float | None, str | None], None]

_REPORTER: ContextVar[Reporter | None] = ContextVar("proofcut_progress", default=None)

#: Reports closer together than this are dropped, except one that finishes —
#: melt prints a frame counter many times a second, and every report is a
#: message on the wire.
MIN_INTERVAL = 1.0

#: What a shipped worker prints on stderr after each item: `MARKER i n`.
MARKER = "proofcut-progress"
_MARKER_LINE = re.compile(rf"^{MARKER} (\d+) (\d+)\s*$")

#: When the last report went out; None until one has, so the first always goes.
_last: ContextVar[float | None] = ContextVar("proofcut_progress_last", default=None)

_CANCEL: ContextVar[threading.Event | None] = ContextVar("proofcut_cancel", default=None)

#: How often a cancellable `run()` looks at its event while the child runs.
CANCEL_POLL = 0.2


class Cancelled(Exception):
    """The job this ran under was stopped, and its subprocess killed."""


@contextlib.contextmanager
def reporting(reporter: Reporter | None) -> Iterator[None]:
    """Install `reporter` for everything called inside the block."""
    previous = _REPORTER.set(reporter)
    last = _last.set(None)
    try:
        yield
    finally:
        _last.reset(last)
        _REPORTER.reset(previous)


def active() -> bool:
    return _REPORTER.get() is not None


@contextlib.contextmanager
def cancellable(event: threading.Event | None) -> Iterator[None]:
    """Let setting `event` kill whatever `run()` has running inside the block."""
    previous = _CANCEL.set(event)
    try:
        yield
    finally:
        _CANCEL.reset(previous)


def cancel_armed() -> bool:
    """Whether a Stop could reach a subprocess here — so the caller must go
    through `run()` rather than a plain `subprocess.run`."""
    return _CANCEL.get() is not None


def streamed() -> bool:
    """Whether a subprocess should go through `run()` at all: someone is
    listening, or someone may stop it. Otherwise it is the plain call."""
    return active() or cancel_armed()


def _kill(proc: subprocess.Popen[bytes], group: bool) -> None:
    """Kill the child, and with `group` everything it started: melt runs under
    `systemd-run` → `nice` → `flatpak run`, and killing only the first leaves
    the encode running."""
    if group:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def report(current: float, total: float | None = None, message: str | None = None) -> None:
    """Say how far along the current operation is. A no-op with nobody listening.

    A reporter that raises is dropped for the rest of the call rather than
    failing the operation it was only describing.
    """
    reporter = _REPORTER.get()
    if reporter is None:
        return
    now = time.monotonic()
    finished = total is not None and current >= total
    last = _last.get()
    if not finished and last is not None and now - last < MIN_INTERVAL:
        return
    _last.set(now)
    try:
        reporter(float(current), None if total is None else float(total), message)
    except Exception:  # noqa: BLE001 — a listener gone away must not fail the work
        _REPORTER.set(None)


def worker_line(line: str, message: str) -> None:
    """Report a shipped worker's `MARKER i n` line; ignore anything else."""
    match = _MARKER_LINE.match(line.strip())
    if match:
        report(int(match.group(1)), int(match.group(2)), message)


def run_worker(command: Sequence[str], message: str) -> subprocess.CompletedProcess[str]:
    """Run a shipped worker, reporting its `MARKER` lines under `message`."""
    report(0, None, message)
    return run(command, on_stderr=lambda line: worker_line(line, message))


def _pump(stream: Any, sink: list[str], on_line: Callable[[str], None] | None) -> None:
    """Read a binary pipe as it arrives, keep it all, and hand over each line.

    `read1` returns whatever is ready rather than blocking for a full buffer,
    and a carriage return ends a line as well as a newline: melt redraws its
    frame counter in place.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    while True:
        chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(4096)
        if not chunk:
            break
        text = decoder.decode(chunk)
        sink.append(text)
        if on_line is None:
            continue
        pending += text
        *lines, pending = re.split(r"[\r\n]", pending)
        for line in lines:
            if line:
                on_line(line)
    tail = decoder.decode(b"", final=True)
    sink.append(tail)
    if on_line is not None and (pending + tail).strip():
        on_line(pending + tail)


def run(
    command: Sequence[str],
    *,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    stdin: Any = None,
    cwd: str | Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run(capture_output=True, text=True)`, streamed.

    Returns the same `CompletedProcess` with both streams as text, so a caller
    swapping this in changes nothing downstream. Each stream is read on its own
    thread, so neither can fill its pipe and stall the child. On `timeout` the
    child is killed and `subprocess.TimeoutExpired` raised, as `run` does.
    `FileNotFoundError` propagates unchanged.

    Under `cancellable`, the child gets its own process group (POSIX) so a
    Stop kills all of it, and `Cancelled` is raised — before spawning, if the
    event is already set.
    """
    cancel = _CANCEL.get()
    if cancel is not None and cancel.is_set():
        raise Cancelled(f"stopped before running {command[0]}")
    group = cancel is not None and os.name == "posix"
    proc = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=stdin,
        env=env,
        cwd=cwd,
        start_new_session=group,
    )
    out: list[str] = []
    err: list[str] = []
    # The reporter lives in this thread's context; the readers need it too.
    readers = [
        threading.Thread(
            target=contextvars.copy_context().run, args=(_pump, proc.stdout, out, on_stdout), daemon=True
        ),
        threading.Thread(
            target=contextvars.copy_context().run, args=(_pump, proc.stderr, err, on_stderr), daemon=True
        ),
    ]
    for reader in readers:
        reader.start()
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        wait = remaining if cancel is None else min(CANCEL_POLL, remaining if remaining is not None else CANCEL_POLL)
        try:
            returncode = proc.wait(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            stopped = cancel is not None and cancel.is_set()
            if not stopped and (deadline is None or time.monotonic() < deadline):
                continue
            _kill(proc, group)
            proc.wait()
            # A grandchild can still hold a pipe open; the partial output is a
            # courtesy, not worth hanging the kill on.
            for reader in readers:
                reader.join(timeout=5)
            if stopped:
                raise Cancelled(f"stopped {command[0]}") from None
            raise subprocess.TimeoutExpired(command, timeout or 0, _text(out), _text(err)) from None
    for reader in readers:
        reader.join()
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, list(command), _text(out), _text(err))
    return subprocess.CompletedProcess(list(command), returncode, _text(out), _text(err))


def _text(chunks: list[str]) -> str:
    """Joined, with the newline translation `text=True` would have done."""
    return "".join(chunks).replace("\r\n", "\n")
