# proofcut — a faster test suite

Provenance: 2026-09-17, during B4. Tyler said "the test suite takes so long"
while the full run he was waiting on sat at 92%, and
asked for a plan to try another time. The only thing measured so far is the
suite's total time.
Status lives in the wiki's Open items table (`proofcut-suite-speed`).

## What is known

- **The full suite is 2408 tests in 13:05**, measured 2026-09-17 with the
  headless offscreen plugin, so all the melt tests ran.
  - It is run to a file in the background, because a piped `| tail` loses
    the verdict. Source must not change during a run.
  - Its cost is real work: the stdio tests start a server process each and
    render through melt, ffmpeg and magick. No per-test breakdown has been
    taken.
- **The box has 20 cores**, and pytest runs on one.
- **`pytest-xdist` is not installed.** The dev group is `pytest` and `ruff`
  only.
- **Isolation already looks good:**
  - Every test that touches `picture.RENDER_SCRATCH` patches it to
    `tmp_path`.
  - `visible_tmp` is a fresh `mkdtemp` under `$HOME` per test.
  - The HTTP servers bind `--port 0`.
  - These were read from the source, not proven under load.
- **Known shared state that parallel runs could collide on:**
  - A real render with no patch still stages in `~/proofcut-render/`. Its
    staging directories are uniquely named, but `sweep_scratch` deletes
    old ones by name. A sweep in one worker could hit another worker's
    directory only if that directory is older than
    `SCRATCH_RETENTION_DAYS`, so it is unlikely but unproven.
  - The GPU: memory `one-gpu-job-at-a-time` records a TTS job failing a
    whisper verify. If any test reaches a real model (`PROOFCUT_WHISPER`,
    `PROOFCUT_TTS`, `PROOFCUT_FACE`, `PROOFCUT_VLM` set in this box's
    environment), two workers could collide.
  - The Kdenlive flatpak: many concurrent `flatpak run` calls are untested.
  - The `systemd-run --user` memory cap: many scopes at once are untested.

## Steps

Each step is judged by the numbers it produces, and the order is the
cheapest first.

1. **Measure where the 13 minutes go.** Run
   `pytest --durations=40 -q` to a file, then sum by file and by marker
   (`needs_melt`, `needs_ffprobe`, the stdio server spawns).
   - If a few tests are most of the time, the fix may be those tests (a
     shared module-scoped fixture, a shorter render), not parallelism.
2. **Try xdist.** Add `pytest-xdist` to the dev group, then run
   `pytest -n 4`, then `-n 8`, then `-n auto`, three runs each. Record
   wall time and every failure.
   - **A failure that appears only under `-n` is a finding about shared
     state, never a flake to retry.** Name the shared resource before
     changing anything.
   - Never edit or weaken a test to make it parallel-safe. If one truly
     cannot share, mark it (step 3).
3. **Serialise only what must be.** If step 2 names GPU or flatpak
   contention, put those tests in one xdist group (`--dist loadgroup` and
   `@pytest.mark.xdist_group("gpu")`), so they run one at a time while the
   rest spread out.
4. **The control.** Run the suite serially once more, and compare the
   pass/fail sets. A parallel run that passes a test the serial run fails,
   or the reverse, is not faster, it is different.
5. **Write down the result.** The speed-up (against 13:05) and the command go in
   CLAUDE.md's one-line pointer and in HISTORY.md, and the memory
   `run-the-suite-to-a-file-not-a-pipe` gets the new duration.
   - CI (Linux, macOS, Windows) stays serial unless it is measured
     separately. GitHub's runners have 2–4 cores, and the Windows runner
     cannot run the melt tests anyway.

## Also worth a look, after step 1

- **A fast default and a slow marker.** For example, `-m "not slow"` for
  the edit loop, with the full run before every commit. This only helps if
  step 1 shows the slow tests are a small set.
- **The headless run needs `QT_QPA_PLATFORM` handed to the stdio server**
  (the `get_default_environment` plugin, CLAUDE.md). If that plugin is kept,
  it belongs in `tests/conftest.py` rather than a scratch file, so an
  unattended run renders without it being remembered. That is a separate
  small change, and worth making in any case.

## Decisions for Tyler

1. **Add `pytest-xdist` as a dev dependency** if step 2 shows a real gain
   with no new failures (recommended). The alternative is to keep the suite
   serial and cut only the slowest tests.
2. **Keep CI serial** (recommended) until it is measured on the runners.
