# One agent per project: the project lock

Written 2026-09-21, off PRIOR-ART.md § Glama's related servers (CutPilot's
`project-store.mjs` keeps a lock directory that expires) and TRIAL.md § 4.
Design only; nothing here is built. Status of the work lives in the wiki
(`README.md` Open items, `proofcut-project-lock`), not here.

## The gap

`Project._manifest_stamp` (`project.py`) is a sha256 of `proofcut.json`'s
on-disk bytes, taken on every read and re-taken after every write.
`write_manifest` and `restore` compare the file against it and raise
`ProjectConflictError` when another writer has moved it, so **the loser of a
write race now loses cleanly**. Three things it does not do:

- **It does not stop the second start.** Every op opens its own `Project`, so
  the stamp's window is one op long. Two agents alternating ops never trip it:
  each op re-reads, writes and succeeds, and each agent goes on planning from
  a picture of the project that the other agent has already changed. TRIAL.md
  § 4 is that failure. An agent found a cue table it had not written and said
  so in its own prose.
- **It covers the manifest only.** `timeline.write` (`timeline.py`) is atomic
  but has no stamp, and both writers share one temp name
  (`project.otio.tmp`, `proofcut.json.tmp`).
- **It has a check-then-replace window.** The digest is compared, then the
  temp file is written and moved over the manifest. A write can land between
  the two. The window is small, but it is real.

The one lock in the repo today is `agent_trial.py`'s `hold_lock`, which fixes
the instrument and not the product (TRIAL.md § 4). It has two defects worth
learning from:

1. **It records the harness's pid, and the harness is not the writer.**
   `run_agent` puts `claude` in its own session so that `killpg` reaches it,
   and that is exactly why `claude` outlives a killed harness. After a
   `kill -9` of the harness, the lock names a dead pid while the orphaned
   agent keeps editing, so the next run's `hold_lock` proceeds, which is the
   measured failure again. **The lock has to be held by the process that
   writes, which is the MCP server.** The MCP server is `claude`'s child and
   dies with it, not with the harness.
2. **`os.kill(pid, 0)` is a liveness probe only on POSIX.** On Windows,
   Python's `os.kill` with any signal other than `CTRL_C_EVENT` or
   `CTRL_BREAK_EVENT` calls `TerminateProcess`. The probe kills the holder.
   Nothing runs the harness on Windows today, but CI runs the suite there, so
   the product lock must not reuse this probe.

**CutPilot is prior art for the form, not the semantics.** Its
`withProjectLock` is a `mkdirSync` critical section held for the length of
one save (a 5 s acquire timeout, stale after 30 s by the directory's mtime),
with a revision counter on top. That is the same ground `_manifest_stamp`
already covers. It does nothing about a second session starting. Its
stale-break also has a race this design must avoid: two waiters both see the
lock as stale and both `rmSync` it, and the slower one deletes the lock the
faster one has just taken. Its staleness also compares a file mtime against
the local clock, which proofcut has already learned not to trust (CLAUDE.md,
"a clock is the wrong witness"; HISTORY.md § The stamp that was a clock).

## What the lock guards, and what it must not block

**It guards one thing: that at most one agent session writes a project at a
time.** "Agent session" means a proofcut MCP server process. That covers
Claude Code's plugin server (unbound, or bound by cwd), `proofcut -C DIR mcp`,
the HTTP transport, and the web panel's own `claude`, which `webui._spawn`
starts with `-C <root> mcp`. The failure measured in TRIAL.md § 4 is two such
sessions, and a session is the only writer that plans across many calls from
an old read.

**It must not block:**

- **Any read.** A tool whose `_ANNOTATIONS` entry is `_READ` (`ping`,
  `doctor`, `timeline_view`, `changes`, `verify`, `check_frames`, and the rest
  of that list in `server.py`) neither takes nor checks the lock. The same goes
  for the CLI's read commands and the picker's scan. An agent reviewing a
  project another agent is editing is a legitimate workflow.
- **A server that never writes.** Acquisition is lazy: it happens at the first
  non-`_READ` tool call for a given project, never at server start. A Claude
  Code session that has the plugin loaded in a project but only reads it holds
  nothing. An unbound server reaches many projects, so it holds a lock per
  project it has written.
- **`proofcut web` as a viewer, and a person's own edits in it.** The web
  process does not take the lock. A person in Studio sees another writer's
  edits through `_revision`'s live refresh, and the stamp already makes a
  collision between a person's edit and an agent's edit a clean refusal. The
  only part of the web UI that takes the lock is its panel agent, through its
  MCP child, like any other session.
- **One-shot CLI mutations** (`proofcut cut`, `undo`, …). A person typing a
  command beside an agent knows the agent is there. The stamp covers the
  collision. Whether the CLI should *warn* when a live lock is held is an open
  question below.

**Mutating tools called with `plan=True` do acquire.** They write nothing, but
an agent planning in a project that someone else holds is planning from a
stale read, which is the failure itself. The rule stays one line: acquire
unless the tool is `_READ`.

## Where it hooks in

There is one choke point. `server._tool` already wraps every tool so that its
project selectors pass through `_confine`, and `_ANNOTATIONS[fn.__name__]`
classifies every tool (a missing entry is a `KeyError` at import). The wrapper
gains one step: for a non-`_READ` tool, resolve the confined project root and
call `ensure_held(root)`. A tool with two selectors, such as `reel`'s `dest`,
ensures the lock for each root. `init` creates the directory first and takes
the lock after `Project.create`.

`ensure_held` lives in a new small module, `projectlock.py`, next to
`project.py`, and knows nothing about MCP. It does three things:

- **Take the lock if this process does not hold it.** It raises
  `ProjectLockedError(ProjectError)` if a live holder has it. Because the error
  is a `ProjectError` subclass, every existing `EXPECTED` handler already
  treats it as a refusal rather than a crash. It is not a subclass of
  `ProjectConflictError`, because "refused before reading" and "stale write"
  are different facts.
- **Check the fencing token if this process already holds the lock.** It reads
  `owner.json` and compares its token with the process's own token. A mismatch
  means someone broke the lock, and the process refuses with "this session lost
  the project lock to …" instead of writing. That is one small file read per
  mutating call.
- **Stamp `last_write`** for the idle release (§ Expiry).

`agent_trial.py`'s `prepare` deletes and re-initialises the project. It must
also refuse while the project lock is live, by importing the same check
instead of restating it. Its own `hold_lock` can stay for harness-against-
harness, but its liveness probe should switch to the one below.

## Lock form

**A directory, created complete and moved into place with a rename:**
`<project>/cache/agent.lock/`.

1. Write `cache/agent.lock.<token>.new/owner.json` in full.
2. `os.rename` that directory to `cache/agent.lock`.

**A directory rename onto an existing non-empty directory fails** on POSIX
(`EEXIST`/`ENOTEMPTY`), on Windows (`FileExistsError`) and on NFS and SMB,
where the server performs the rename atomically. The lock directory is never
empty, because it is populated before the move, so the rename is an exclusive
create. **No reader ever sees a lock without an owner.** That removes the
window plain `mkdir` then write leaves open, where a lock exists but is empty
and every reader must guess whether it is being created or was abandoned.
`O_EXCL` on a file has the same empty window and has a history of not being
exclusive over NFSv2. `os.link` is exclusive with full contents, but FAT,
exFAT and some SMB shares do not support it.

Why under `cache/`: it is already the project's "disposable, not authoring
state" directory (`session.json`, `poster.jpg`). A copy of a project that
carries a lock along is harmless, because a copied lock is judged like any
other: its pid is dead, or it belongs to another host. Deleting `cache/` wholesale
is safe, because the fencing check turns it into "lost the lock" at the
holder's next write.

**`owner.json`:**

| field | for |
|---|---|
| `token` | a random uuid, which is the fencing identity. It is never a pid, because a pid is reused. |
| `pid` | liveness on the same host |
| `host` | `socket.gethostname()` |
| `boot_id` | Linux `/proc/sys/kernel/random/boot_id`, else absent. It differs across a reboot, and a holder from before a reboot is dead. |
| `pid_ns` | Linux `readlink /proc/self/ns/pid`, else absent. A pid from another container's namespace means nothing here. |
| `started` | ISO wall time, **for people only; never compared** |
| `command` | what holds it, e.g. `proofcut -C … mcp (stdio)` or `web panel`, for the refusal message |

**`heartbeat`** is a separate file in the lock directory, holding an integer
counter. A daemon thread in the holder increments it every
`HEARTBEAT_SECONDS` (15), writing a temp file in the directory and replacing
the heartbeat file with it. It is a **counter, not a timestamp**. What matters
is whether it changes, and that can be seen without trusting anyone's clock.

## Expiry: when is a lock stale

This is the hard part, so here are the rules in order. **A clock may prompt a
check. It never decides one.**

1. **Same machine (`host`, `boot_id` and `pid_ns` all match ours), pid dead**
   → stale at once. Liveness uses `os.kill(pid, 0)` on POSIX, and
   `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` plus `GetExitCodeProcess`
   `== STILL_ACTIVE` through `ctypes` on Windows. **Never `os.kill` there**
   (§ The gap). There is no `psutil`, and adding a dependency for one probe is
   not worth it.
2. **Same host name, `boot_id` differs** → stale at once, because the holder
   died with the boot.
3. **Otherwise** (the pid is alive, or it is on another machine, or its
   liveness cannot be known) → **live**, unless the heartbeat is judged dead.
   The heartbeat is judged by observation: read the counter, wait
   `3 × HEARTBEAT_SECONDS`, then read it again. Unchanged means dead. That
   check runs only when something already looks wrong:
   - the heartbeat file's mtime, compared with the local clock, is older than
     `2 min`. That is a clock comparison, and it is allowed because all it does
     is trigger the observation.
   - or the owner is on another host and the caller explicitly asked to break
     it (§ Breaking a stale lock).

   So an ordinary refusal is immediate. The ~45 s wait only happens on the path
   that is already about to take a lock from someone.

The observation also covers **pid reuse**. A recycled pid reads as alive, but
nothing is advancing its counter.

**Cross-host (a project on a NAS share opened from two machines):** pid
liveness is not available, so the heartbeat observation is the only evidence.
Rename atomicity holds on NFS and SMB. Two caveats remain:

- An NFS client caches attributes (`actimeo`), so a counter can look frozen
  for a few seconds. The `3×` window is sized well past the default 3 s to 60 s
  attribute cache for data files, but `noac` and long `actimeo` mounts vary,
  and **cross-host is best-effort by design**.
- A partitioned holder keeps believing it holds the lock after the other side
  has broken it. The fencing check catches that at its next write, but only if
  it can reach the share again. If it cannot, it cannot write either.

**Idle release is separate from liveness.** A live, idle holder is not stale.
It is simply not writing. A web panel agent that made one edit and sits open
for an afternoon would otherwise block every terminal agent in that project
until the browser tab closes. So the holder releases its own lock after
`IDLE_MINUTES` (proposed 10) with no mutating call. It measures that with its
own `time.monotonic()`, which is one process's own clock and immune to wall
clock jumps. It takes the lock again lazily at its next mutating call. If
another session wrote in between (the release records the last holder's token
in `cache/agent.lock.last`), the first mutating result after re-acquiring
carries a notice: *"another session edited this project while this one was
idle — re-read before continuing"*.

## Breaking a stale lock

**By hand, never by an agent.** `proofcut unlock [-C DIR]` prints the holder
(`command`, pid, host, started, heartbeat state), then:

- If the holder is stale by rule 1 or 2, it breaks the lock and says so.
- If the holder is on the same machine and its pid is alive, it refuses and
  names the pid. `--force` breaks it anyway, for a hung holder, and the holder
  then gets "lost the lock" at its next write.
- If the holder is on another host, it runs the heartbeat observation. If the
  heartbeat is dead, it breaks the lock. If it is advancing, it refuses unless
  `--force` is given.

**The break is a rename, never a delete.** It moves `cache/agent.lock` to
`cache/agent.lock.broken.<our token>`. Exactly one of two racing breakers wins
the rename, and the loser gets `ENOENT`. The winner then re-reads the moved
`owner.json`, and if its token is not the one it judged stale (a fresh owner
took the lock in between), it tries to rename it back. If even that loses a
race, the fencing check makes the displaced owner refuse. It does not write
blind. This is the race CutPilot's `rmSync` has.

**Lazy acquisition breaks automatically only under rules 1 and 2**, where
same-machine evidence is certain. A cross-host stale lock always needs
`unlock`, because a wrong automatic break across hosts is the one outcome that
brings back two writers.

**`unlock` is not an MCP tool.** An agent that can break another agent's lock
recreates the problem. The refusal text says so plainly, because a terminal
Claude Code agent that has shell access will otherwise "fix" the refusal with
`rm -rf`. The text is: *"Another proofcut session holds this project (pid N,
`command`, host H, since T). Do not delete the lock — stop and tell the user;
they can run `proofcut unlock` if that session is dead."* The panel agent's
`--tools` confinement already keeps it off the shell. The fallback of
deleting `cache/agent.lock/` by hand goes into MANUAL.md with `unlock`. It is
safe for the same fencing reason.

## How it fits with `_manifest_stamp`

**They are two layers, and neither replaces the other.** The lock stops the
second *start*. The stamp stays exactly as it is, as the per-write guard for
everything the lock deliberately does not cover: a person's edits in the web
UI, CLI one-shots, the residual races in lock breaking, and the cross-host
best-effort gap. The design principle: **every race the lock does not prevent
must lose cleanly, never silently.** The fencing check and the stamp are the
two refusals that make that true.

When a `ProjectConflictError` fires and a lock is held by someone else, its
message appends the holder (*"… another writer touched this project in
between; it is held by pid N (`command`)"*). The stamp can then say *who*,
which it cannot today. The manifest-only coverage and the check-then-replace
window stay as they are. With one agent per project, closing them is a
separate question and not part of this work.

## Crash cleanup

- **Normal exit** (a stdio MCP server exits when its client closes stdin; the
  web panel's `agent.close()`): release in `finally` and in `atexit`, and only
  if `owner.json`'s token is still ours. Never delete someone else's lock.
- **SIGTERM:** Python's default handler skips `atexit`, so the server installs
  a SIGTERM handler that raises `SystemExit`. It does this on POSIX only.
  Windows has no SIGTERM delivery to hook here.
- **SIGKILL, power loss, OOM:** the lock stays on disk. It is reclaimed
  automatically by rule 1 (dead pid) or rule 2 (reboot) at the next
  acquisition on the same machine. From another machine it takes `unlock`.
- **Litter:** a crash between steps 1 and 2 of taking the lock leaves
  `agent.lock.<token>.new/`, and a break leaves `agent.lock.broken.*/`.
  Acquisition sweeps any of those whose `owner.json` names a pid that is dead
  on this machine, or that is older than a day. Litter can never block
  anything, because only the exact name `agent.lock` is the lock.
- **The trial's orphan** (`kill -9` of the harness): the orphaned `claude`'s
  MCP server holds the lock and keeps its heartbeat going, so the next
  `prepare` refuses. That is the TRIAL.md § 4 failure, now caught by the
  product.

## Test plan

Timing is injected (`HEARTBEAT_SECONDS`, `IDLE_MINUTES` and the observation
window are parameters, and the monotonic clock is a fake), so nothing sleeps
more than about a second. Tests measure the property itself, never a timing
margin (CLAUDE.md's cross-OS CI rule).

**The primitive, on all three CI OSes:** renaming a populated directory onto
an existing populated directory fails. The whole design rests on this, so it
gets its own test, not an incidental one.

**Unit (`projectlock.py`):**
- Acquire, then release, and the lock directory is gone. Acquiring twice in
  one process is idempotent.
- A second *process* is refused with `ProjectLockedError`, and the message
  names pid and command and contains "do not delete".
- N processes race to acquire (`multiprocessing`) → exactly one holds the
  lock.
- A dead-pid owner (spawn a child, record its pid, reap it) is reclaimed at
  once. A mismatched `boot_id` is reclaimed at once.
- A foreign-host owner with an advancing heartbeat is live. With a frozen
  heartbeat it is stale after the observation. It is never broken
  automatically at lazy acquisition, only by `unlock`.
- A live pid whose heartbeat is frozen (pid reuse) is stale by observation.
- Two breakers race → at most one owner afterwards, and a displaced owner's
  next `ensure_held` raises "lost the lock".
- Fencing: delete `cache/agent.lock` under a holder → its next mutating call
  refuses, and it does not re-acquire silently.
- Idle release after the fake clock passes `IDLE_MINUTES`, and the notice on
  re-acquiring after another session wrote.
- Windows: the liveness probe does not terminate a live child. This is the
  regression test for `os.kill`, and it asserts the child is still running
  afterwards.
- Litter sweep: `.new`/`.broken` directories with a dead pid are removed, and
  the live lock is untouched.

**Server (the registry sweep, a test that grows with the tool list):**
- For every `_READ` tool: calling it on a project leaves no `cache/agent.lock`.
- For every non-`_READ` tool: its registration goes through the lock step
  (checked from the registry, not by calling all ~65 writers).
- A bound server's second process gets the refusal as an `is_error` result
  with the message intact.

**Integration:**
- `agent_trial.py prepare` refuses while a live lock exists.
- The web panel agent's first write, while a terminal session holds the lock,
  shows the refusal in the pane. It must not be swallowed by `_pump_stdout`.
- `proofcut unlock`: its refuse, break and `--force` paths.

**Live, per the repo's verify-live rule:** two real `claude` sessions on the
demo project (the web panel plus a terminal Claude Code with the plugin). The
second one's first edit is refused, and it tells the user rather than
deleting the lock. `kill -9` the first `claude`, and the second one's next
edit succeeds without `unlock`. Then rerun the TRIAL.md § 4 sequence (kill the
harness mid-run, start another) and see `prepare` refuse.

## Recommendation

Build it as designed, in this order:

1. Take the lock with a rename of a populated directory into
   `cache/agent.lock/`.
2. Acquire it lazily in `server._tool` for every non-`_READ` tool.
3. Hold it as a session lease with a counter heartbeat, a fencing token
   checked before every write, and idle release after 10 minutes.
4. Judge staleness automatically only from certain same-machine evidence
   (dead pid, different boot).
5. Break anything else only through `proofcut unlock`, which is never an MCP
   tool.

Keep `_manifest_stamp` unchanged as the layer every remaining race loses
against. Fix `agent_trial.py`'s probe and make `prepare` honour the lock in
the same change. The work is one new module of about 200 lines plus one step
in `_tool`, and none of it touches the manifest schema.

## Open questions for Tyler

1. **Idle release: yes, and is 10 minutes right?** Without it, an open web
   panel that made one edit blocks every other agent in that project until
   the tab closes. With it, an agent that thinks for more than 10 minutes
   between edits can lose the lock. It gets it back if nobody else took it,
   and gets a notice if someone did.
2. **Should one-shot CLI mutations warn (stderr, and proceed) when another
   session holds the lock, or stay silent?** This design proposes a warning.
   Refusing would get in the way of a person working next to their own agent.
3. **Should Studio show the holder** ("an agent is editing: pid N, since T")
   in the truth strip, and offer an unlock button for a *stale* lock only?
   This is a read of `owner.json`, and it is optional to the lock itself.
4. **Is a project on a NAS share opened from two machines a real workflow?**
   If not, the cross-host path can stay "always needs `unlock`" and the
   heartbeat observation is only a backstop against pid reuse. If it is, it
   should get a live test against the NAS mount.

**Answered by Tyler 2026-09-22: all four as proposed.** Idle release after 10
minutes. CLI mutations warn and proceed. Studio shows the holder and offers an
unlock button for a stale lock only. Two machines on one NAS project is not a
real workflow, so the cross-host path always needs `unlock`. Build per
§ Recommendation.
