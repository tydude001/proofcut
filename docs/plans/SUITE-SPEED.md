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
- **The box has 20 cores**, and a plain `pytest` runs on one.
- **`pytest-xdist` was not installed when this was written**; it joined the
  dev group once step 2 had measured it.
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
     cannot run the melt tests anyway. (Measured 2026-09-24; see the
     decision below.)

## Also worth a look, after step 1

- **A fast default and a slow marker.** For example, `-m "not slow"` for
  the edit loop, with the full run before every commit. This only helps if
  step 1 shows the slow tests are a small set.
- **The headless run needs `QT_QPA_PLATFORM` handed to the stdio server**
  (the `get_default_environment` plugin, CLAUDE.md). If that plugin is kept,
  it belongs in `tests/conftest.py` rather than a scratch file, so an
  unattended run renders without it being remembered. That is a separate
  small change, and worth making in any case.

## Steps 1 and 2, measured — 2026-09-17

Logs and junit files: `~/proofcut-work/spikes/suite-speed/`. Every run used
`QT_QPA_PLATFORM=offscreen`, which `tests/conftest.py` now passes through to
the stdio server, so all the melt tests ran.

**Step 1, serial: 2408 passed in 13:09**, at 131% CPU.
- **The time is spread thin.** The slowest 10 tests are 7% of it, the
  slowest 100 are 32%, and it takes 250 to reach half. The median test takes
  2 ms. No small set of slow tests is worth cutting, so a "slow" marker would
  not buy much.
- **Two files are 61% of the time.** `test_server_stdio` is 39% (310 s, 237
  tests), and most of that is starting a server process for each test.
  `test_webui_http` is 21% (169 s). Next is `test_ops_reframe_sheet` at 8%.

**Step 2, xdist** (`uv run --with pytest-xdist`, three runs each; the dev
group is unchanged):

| workers | wall times | failures |
|---|---|---|
| `-n 4` | 3:38, 3:37, 3:28 | none |
| `-n 8` | 1:59, 2:22, 2:22 | none |
| `-n auto` (20) | 1:50, 1:50, 1:46 | 1, in the third run |

In the other eight runs, the set of passing and failing tests matched the
serial run exactly.

**The one failure names a shared resource: the flatpak launcher.** melt
printed nothing, and its stderr said `error: Extension
org.freedesktop.Platform.GL.default has invalid merge-dirs`. That is
`flatpak run` failing before melt starts.
- **It reproduces.** Running only the 54 melt tests at `-n 20` failed in 3
  of 5 rounds (4 failures in all), each with the same message. It hit
  `export` and `check_frames` alike.
- **Plain launches don't trigger it.** 120 concurrent
  `flatpak run … melt -version` calls (5 rounds of 4, 5 rounds of 20) never
  failed. So it needs a launch to overlap other instances of the app that
  are doing real work.
- **It is a known flatpak startup race.** The same message is reported for a
  Discord flatpak starting at login, with healthy extension metadata
  (hyperlapse122/dotfiles#379).
- **This is a product defect as well as a test one.** Two renders at once
  through the Kdenlive flatpak can fail this way: a window export while an
  agent runs `check_frames`, say. The failure is reported as "melt printed
  no timeline" or "melt rendered nothing".

## Decisions for Tyler

Taken 2026-09-17: Tyler accepted all three, and all three are built
(HISTORY.md § The suite in two minutes, and the flatpak launch race).

0. **The flatpak launch race** (actionable now). Recommended: **fix it in
   `picture.py`.** Retry a melt call once when stderr carries flatpak's own
   `invalid merge-dirs` line. The failure happens before melt reads
   anything, so a retry repeats no work. Then re-run the 20-worker melt
   stress as the check. The alternative, a test-only fix, is an
   `xdist_group("melt")` that serialises the melt tests. That hides the race
   from the suite and leaves it in the product.

1. **Add `pytest-xdist` as a dev dependency, once 0 is fixed**
   (recommended). Step 2 showed 13:09 → 1:50 at `-n auto`, about 7x, and no
   failure other than 0. The alternative is to keep the suite serial and cut
   only the slowest tests, but step 1 found no small set worth cutting.
2. **Keep CI serial** (recommended) until it is measured on the runners.
