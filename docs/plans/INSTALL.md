# proofcut — the install plan: a bare machine to a checked render

Provenance: PRIOR-ART.md § FableCut, read names install as "the gap most
likely to cost proofcut a stranger". FableCut is `node server.js`; proofcut is
uv plus four programs from four places. Tyler asked on 2026-09-16 to plan
closing it. Sources: HISTORY.md § A stranger's install, on a clean Ubuntu,
§ A stranger's install, on a clean Fedora, § The one-line doctor, and
§ The plugin, installed the way a stranger installs it; the two trial kits
(`scripts/mac_trial.sh`, `scripts/windows_trial.ps1`); and the measurements
below, made the same day in `~/proofcut-work/spikes/install-paths/`.

Two facts frame the plan. **The Mac and Windows kits are already installers,
dressed up as tests.** Linux is the platform proofcut is built on, and it is
the only one with no script. What stops a stranger there has already been
measured twice in clean containers: four programs from four sources, an X
server on a headless box, and Fedora's `ffmpeg-free` and `melt` name traps.
**A portable Shotcut's melt removes the X server and the name trap**, measured
below.

This is the one document for that work. Status lives **only** in the wiki's
Open items table (`~/projects/wiki/README.md`, `proofcut-install`), and this
file never carries a status header. When a step ships, HISTORY.md gets a named
section, the step here gains a one-line "Shipped — see HISTORY.md § <name>"
pointer, and the wiki row updates.

## The gate this plan works under

Two plans already say not to build this:

- LAUNCH.md § What this plan deliberately does not do: "**No container or
  install script before step 2 says where the stranger stopped.** Measure the
  blocker, then beat it."
- PORTABILITY.md § What this plan deliberately does not do: "No installer, no
  packaged app."

This plan does not overturn either of them on its own authority; that is
decision 1 below. The case for Linux going first is that the measurement
LAUNCH.md's gate asks for already exists there: two clean-container runs
recorded each place a stranger stopped, and each place was fixed in doctor's
advice. Nothing like that exists for a Mac. The Mac kit already does the
install for a tester, so a Mac installer waits for the Mac report, as
LAUNCH.md says. PORTABILITY.md's rule is about `.dmg`/`.msi` packaging, and
nothing here packages proofcut.

## How to work this plan

- **Step 1 needs no decision and ships from this box.** Steps 2–3 wait on
  decisions 1–2. Step 4 is Tyler's hand. Step 5 waits on a person's Mac
  report.
- **Every step starts with its "Verify first" list.** The numbers below were
  read on 2026-09-16, and upstream releases move under them.
- **An installer installs only what `proofcut doctor` reports missing.** A kit
  can install everything, because its tester signed up for that and its
  `--uninstall` puts the machine back. A real installer that replaces a
  working system ffmpeg or melt breaks something the user already had. So
  doctor's report is the installer's input, and nothing without a ✗ is
  touched.
- **Installs on someone else's machine reverse exactly.** Every file and link
  the installer adds is recorded, and `--uninstall` removes those and nothing
  else. That is the kits' contract, carried over.
- **Judge an install by a checked render, never by exit codes.** Done means
  DEMO.md reaches `verify` and `check_frames` agrees, in a clean container of
  each distribution, the way the two HISTORY.md runs did.

## What installing takes today

| Piece | Linux | macOS | Windows | Size |
|---|---|---|---|---|
| uv + Python 3.13 + proofcut | uv installer; `uvx --from git+…` (needs git too) | same | same | ~230 MB |
| ffmpeg with libx264, drawtext, libass | distro package; Fedora's default `ffmpeg-free` has no libx264 (RPM Fusion swap) | `ffmpeg-full`, keg-only, first on PATH by hand | gyan.dev essentials (kit) | — |
| whisper | `uv tool install openai-whisper --torch-backend cpu` | same, **but see below** | same (kit) | 1.9 GB CPU, 5.5 GB CUDA |
| auto-editor 31+ | release binary (PyPI is a stale 29.x) | Homebrew or release | release (kit) | 26–46 MB |
| melt | `apt install melt` / `dnf install mlt` (Fedora's `melt` is freeze), or the Kdenlive flatpak; **plus `xvfb-run` on a headless box** for both distro builds | Shotcut.app (kit) | Shotcut portable zip (kit) | — |

whisper is the biggest single cost on every OS, and nothing in this plan
reduces it. See § What this plan deliberately does not do.

## What was measured for this plan — 2026-09-16

1. **Shotcut 26.8.1's portable Linux tarball** is 148 MB
   (`shotcut-linux-x86_64-26.8.1.txz`, sha256 `c4befab2…a7a844`, matching the
   release's `sha256sums.txt`) and unpacks to 578 MB in 4 s. `Shotcut.app/melt`
   is a wrapper script that sets the library and MLT paths and then execs
   `bin/melt`. It reports `melt 7.41.0`. The tarball also ships `ffmpeg` and
   `ffprobe` (n8.1.2) and `whisper-cli` (whisper.cpp 1.8.3).
2. **Its melt draws with no display server.** proofcut's own
   `picture.qt_draws` reads True under `QT_QPA_PLATFORM=offscreen` with
   `DISPLAY` and `WAYLAND_DISPLAY` unset. Doctor's melt and Display rows are
   both ✓. In a clean `ubuntu:24.04` container with no X server, the
   `_QT_PROBE` frame came back red at x=8 and black at x=48, meaning Qt drew.
   That container had only 14 runtime library packages added (`libasound2t64
   libgl1 libegl1 libopengl0 libx11-xcb1 libcairo2 libgbm1 libva-drm2
   libva-x11-2 libwayland-client0 libwayland-cursor0 libwayland-egl1
   libfontconfig1 libglib2.0-0t64`). **Control:** the same container with
   `apt install melt` (7.22) printed "The MLT Qt module requires a X11
   environment" and came back red at both points. That matches the Ubuntu run
   in HISTORY.md. A container with no packages added fails at load on
   `libasound.so.2`. `ldd` names 20 missing desktop libraries in both
   `ubuntu:24.04` and `fedora:latest`. A desktop has them; a server image
   does not.
3. **Its renders pass the suite's real-render tests.** All 14 `@needs_melt`
   tests in `tests/test_server_stdio.py` passed on this box with
   `PROOFCUT_MELT` pointed at the wrapper and no display. They cover the cued
   project through melt, the blur-fill, `tiktok-reels`, holds, the ducked bed,
   the crossfaded passages, the master and the six-channel hold. Logs:
   `stdio-shotcut.log` and `stdio-shotcut-2.log`. This box has the desktop
   libraries, so the same render in a clean container is step 2's check.
4. **Its ffmpeg is not usable.** It has libx264, but no `drawtext`, `ass` or
   `subtitles` filter, so doctor's ffmpeg row would be ✗. **Shotcut supplies
   melt and nothing else.**
5. **BtbN's static `ffmpeg-n8.1-latest-linux64-gpl-8.1`** is a 149 MB tarball
   whose `bin/` is 492 MB (ffmpeg, ffprobe and ffplay). It has libx264,
   `drawtext`, `ass` and `subtitles`. A libx264 encode ran in clean
   `ubuntu:24.04` and `fedora:latest` containers with nothing installed.
   `drawtext` needs a fontconfig configuration: on this box, `font=sans`
   drew; in a bare container, with no `/etc/fonts`, it found no font. A
   desktop has one.
6. **Whisper cannot install on an Intel Mac under Python 3.13.** torch's
   macOS x86_64 wheels end at 2.2.2, which goes up to cp312, and 2.14.0's
   macOS wheels are arm64 only. `uv pip compile` of `openai-whisper` for
   `x86_64-apple-darwin` fails at 3.13 and resolves torch 2.2.2 at 3.12.
   Apple Silicon resolves torch 2.11.0 on both. Both kits pin `--python
   3.12`, from 775e6b8, which gives no reason; this is the likely one.
   **Doctor's whisper fix omits the pin**, so an Intel Mac following doctor
   hits a resolver error. Resolver evidence only: no Intel Mac ran it.
7. **ffmpeg must be on PATH.** proofcut names it bare across 11
   `src/proofcut` modules, with no override, and openai-whisper's own `audio.py` runs
   `"ffmpeg"` from PATH. So an installed ffmpeg goes on PATH, and a
   `PROOFCUT_FFMPEG` override would not reach whisper anyway.
8. **`proofcut` is unclaimed on PyPI** (the JSON API answers 404). The
   one-line doctor builds from the git URL, so a stranger needs git as well
   as uv.

## Step 1 — doctor's advice, corrected

Shipped — see HISTORY.md § `proofcut setup`, built.

No gate. Each change is a line of advice with a test that fails against the
old text.

- **The whisper fix pins `--python 3.12`**, which is what both kits already
  run: `uv tool install --python 3.12 openai-whisper`, plus `--torch-backend
  cpu` with no NVIDIA GPU. Also the README's Requirements row.
- **A headless Linux box whose melt does not draw is offered Shotcut's
  tarball beside `xvfb-run`.** Doctor's Display row and the render refusal
  already name xvfb. The tarball is the route that needs no X server
  (measurement 2). Name it with the `PROOFCUT_MELT=…/Shotcut.app/melt` line
  it needs.

Verify first: `doctor._whisper_entry`, `picture.melt_search`, the Display row
in `doctor.py`, and `tests/test_doctor.py`, for how fix text is asserted.
Confirm that the wrapper also works through a symlink (it resolves itself
with `readlink -f`), since step 2 may link it rather than set a variable.

## Step 2 — a Linux installer (decisions 1–3)

Shipped — see HISTORY.md § `proofcut setup`, built.

The Windows kit's shape, applied to Linux, for use rather than for a test.

- **One folder**, `~/.local/share/proofcut/deps`, holding a pinned
  download of each piece doctor reported missing. Each URL and SHA-256 is
  pinned in the source, as the Windows kit pins them. The pieces are uv's
  installer if uv is missing, BtbN's ffmpeg if the ffmpeg row is ✗,
  auto-editor's `-linux-x86_64` or `-linux-aarch64` release binary if its row
  is ✗, and Shotcut's tarball if the melt row or the Display row is ✗. Then
  whisper by `uv tool install --python 3.12 openai-whisper`, with
  `--torch-backend cpu` unless `nvidia-smi` answers.
- **Only ffmpeg and ffprobe go on PATH, as symlinks in `~/.local/bin`**, the
  directory uv's own installer already puts on PATH (measurement 7). A link
  is never written over a file that is already there. It may shadow a system
  ffmpeg doctor crossed, such as Fedora's `ffmpeg-free`, which is the point,
  and removing the link brings that one back. If the name still resolves
  elsewhere after linking, setup says to put `~/.local/bin` first.
- **melt and auto-editor get no link. Their resolvers search setup's folder
  ahead of PATH** (`deps.py`). As built, a link could not work: setup
  installs a melt when a distro melt that draws nothing is already on PATH,
  and on Fedora that one is `/usr/bin/mlt-melt`, a name `melt_command`
  searches before `melt`. A PATH-first search would find the failing one
  again. The order is safe because setup installs one only when the PATH one
  failed doctor.
- **`--uninstall` removes the folder, the recorded links, the whisper tool,
  any Python uv downloaded for it, and every directory setup created and
  left empty**, and nothing else. uv's download cache is uv's, for `uv cache
  clean`.
- **Before it downloads anything, it asks and prints the total**, whisper's
  1.9 GB included.
- **No sudo, ever.** It never installs a distribution package. The 14 desktop
  libraries in measurement 2 are the one thing it cannot supply, plus
  `libgomp.so.1`, which auto-editor's binary links and a bare Ubuntu lacks.
  The first clean-container run found that one only when `seed` died, with
  doctor calling auto-editor ✓. On a server image setup names the missing
  libraries from `ldd`, keeps nothing of that piece, and stops there rather
  than guessing a package manager.

**Done when:** in clean `ubuntu:24.04` and `fedora:latest` containers, given
the desktop libraries and nothing else, the installer followed by DEMO.md
reaches `verify` with `check_frames` at delta 0 and a layered render
cropping correctly. That is the HISTORY.md runs repeated, with no xvfb and no
RPM Fusion. After `--uninstall`, a file listing of `$HOME` matches the one
taken before the install.

Verify first: BtbN's `latest` tag moves daily, so pin a dated release tag,
never `latest`. **Its dated daily builds are deleted after a few weeks,
while its month-end builds are kept back to 2024**, so the pin is
`autobuild-2026-08-31-13-27`. Shotcut is GPLv3; setup downloads it from
Shotcut's own release and never mirrors it. auto-editor's Linux binary is
dynamically linked against glibc. The HISTORY.md Ubuntu run used it, so it
runs on glibc 2.39 at least.

## Step 3 — where the installer lives (decision 2)

Shipped — see HISTORY.md § `proofcut setup`, built.

Either `scripts/install.sh`, or `proofcut setup`, a CLI subcommand that reads
doctor's report in-process. **Recommended: `proofcut setup`.** The one-line
doctor already runs with no clone (`uvx --from git+… proofcut doctor`), so
`uvx --from git+… proofcut setup` is the same line with one word changed, and
it reads the same `doctor` report rather than parsing one. It is
**CLI-only, never an MCP tool.** An agent must not download 2 GB and add to
PATH unasked, and the parity rule (CLAUDE.md § Conventions) asks every tool
for a CLI twin, not every subcommand for a tool.

What it costs: pinned URLs inside the wheel go stale per upstream release. So
a test holds the table's shape, and the pins are bumped on purpose, like the
version literal.

## Step 4 — the PyPI name (Tyler's hand)

**Shipped — 0.29.0 published 2026-09-16; see HISTORY.md § `proofcut setup`,
built.** A release is `uv build && uv publish` from the tagged tree, run by
Tyler; the version is still read from `pyproject.toml`, so no seventh
literal was added.

`uvx proofcut doctor` drops git from the prerequisites. Publishing is a
public-surface action, so it is Tyler's to run. It adds a seventh place the
version is bumped (`tests/test_version.py`) and one more step per release.
**Recommended: publish once step 3 has shipped**, so the first PyPI README
says `uvx proofcut setup`. Claim the name sooner only if a squatter shows up.

## Step 5 — macOS and Windows (after a person's Mac report)

`proofcut setup` grows the other two OSes out of the kits' install halves.
On Windows that is the portable folder under `%LOCALAPPDATA%`. On a Mac it is
Homebrew's `ffmpeg-full` and Shotcut.app. Each kit then calls `proofcut setup`
and keeps only its demo and its report, so there is one install code path.
LAUNCH.md's gate holds here: build it when a report says where a person
stopped.

## Decisions for Tyler

**Taken 2026-09-16, all four as recommended.**

1. **Build Linux ahead of LAUNCH.md's gate?** Recommended: **yes.** The gate
   asks for a measurement of where strangers stop, and for Linux the two
   clean-container runs are that measurement. Mac and Windows stay gated.
   LAUNCH.md's line would gain "except Linux — INSTALL.md".
2. **A script or `proofcut setup`?** Recommended: **`proofcut setup`**, for
   the reasons in step 3.
3. **Shotcut as the Linux melt?** Recommended: **only where doctor's melt or
   Display row is ✗.** A distribution melt that draws stays. On a desktop
   that is most of them, and Shotcut is 578 MB against a package.
4. **PyPI.** Recommended: **after step 3**, as above.

## What this plan deliberately does not do

- **No container image.** The workspace opens a window, the agent pane runs
  the user's own `claude`, and the footage is on the user's disk. A container
  is a different machine from all three.
- **No distribution packages and no sudo.** The folder is what makes the
  uninstall exact.
- **No replacing a working system tool.** doctor's ✓ means the tool is left
  alone.
- **No smaller transcriber.** Shotcut's `whisper-cli` and faster-whisper
  would each cut the 1.9 GB. But every cut is addressed by openai-whisper's
  word timings, and the hallucination rules in `asr.clean` were measured on
  its output. Swapping it is a measured decision of its own, not an install
  detail.
- **No `.dmg`, `.msi` or AppImage of proofcut** (PORTABILITY.md).
