# proofcut — the portability plan: macOS and Windows

Provenance: a survey on 2026-09-10 answering "what would it take to make
lucid run on Windows and Mac", made by grepping `src/lucid` for every
platform-shaped assumption and reading each one. proofcut has never been run
on either (README.md § Requirements says so), and every render-side trap in
CLAUDE.md was measured on one Fedora box. The finding that frames this
plan: **the Python side is nearly portable already and one line crashes on
Windows; the cost is not code but re-measuring melt, libass and ImageMagick
on each OS, because nothing measured here transfers.**

This is the one document for that work. Status lives **only** in the wiki's
Open items table (`~/projects/wiki/README.md`, `lucid-portability`); this
file never carries a status header. When a step ships: HISTORY.md gets a
named section, the step here gains a one-line "Shipped — see HISTORY.md §
<name>" pointer, and the wiki row updates.

## How to work this plan

- **Steps 1–3 are code and can ship from this box.** Steps 4–6 need a
  machine of the OS in question and cannot be marked done from Linux under
  any reading of "verified". Step 5's code halves (5a–5c) are written here,
  and each is verified by a GitHub Windows run, never by the Linux suite.
- **Every step starts with its "Verify first" list.** The line references
  below were read on 2026-09-10 and code moves; a claim here is a lead,
  never a fact to build on unread.
- Repo conventions bind throughout (CLAUDE.md § Conventions). The ones this
  plan leans on: `doctor` judges melt by its `-version` banner and *runs*
  whisper; an absent optional capability is "unavailable", never a failure;
  every resolver takes a `PROOFCUT_*` override first; `ruff check` is the gate
  and `ruff format` is forbidden; nothing about a render is settled by an
  exit code.
- **Order: macOS first, Windows second.** macOS is Unix-like, symlinks work,
  Homebrew has ffmpeg and ImageMagick, Shotcut bundles melt, and it is where
  the video creators proofcut should earn from mostly are. The Windows pass is
  cheaper once the macOS measurements exist to compare against.

## What already works in lucid's favour

Read these before assuming a port is a rewrite. Each was checked 2026-09-10.

- **No POSIX-only imports anywhere in `src/proofcut`** — no `fcntl`, `signal`,
  `select`, `pwd`, `resource`, `termios`. Subprocesses die by `Popen.kill()`
  (`webui.py` § AgentSession), which works on Windows. The only
  `start_new_session=True` is the browser launch, harmless off Linux.
- **Every heavy tool is a subprocess resolved by `shutil.which` or a
  `PROOFCUT_*` override, never an import**: ffmpeg/ffprobe (bare name on
  PATH), whisper (`asr.whisper_binary`), auto-editor (`autoeditor.binary`),
  melt (`picture.melt_command`), magick (`graphics` § `PROOFCUT_MAGICK`),
  `claude` (`webui._agent_bin`), and the three interpreter-behind-an-env
  workers (`PROOFCUT_VLM`/`PROOFCUT_FACE`/`PROOFCUT_TTS`).
- **Paths are `pathlib` with `expanduser` throughout.** No `/tmp` literal in
  code that runs; the render scratch is `Path.home() / "proofcut-render"`
  (`picture.RENDER_SCRATCH`), which is a valid home-relative path on all
  three.
- **The symlink fallback already handles a filesystem that refuses one.**
  `media._place` catches any `OSError` from `symlink_to` and records
  `"link": "reference"` — so Windows without Developer Mode behaves exactly
  like the NAS case, with no new branch. Same shape in `ops.reel`'s link.
- **Runtime deps are `mcp` and OpenTimelineIO only**, and OTIO 0.18.1 ships
  cp313 wheels for macOS (arm64 and x86_64) and Windows. Verify the wheel
  list on PyPI before relying on this; the cp314 gap in `pyproject.toml`'s
  comment is the kind of thing that changes.
  *Correction, 2026-09-10: the x86_64 half is wrong — every macOS wheel of
  0.18.1 is arm64, so an Intel Mac builds OTIO from source (CMake and a C++
  compiler). HISTORY.md § The Linux-shaped resolvers, widened.*
- **The MCP stdio server, the stdlib HTTP web UI, its `EventSource` bus,
  Range streaming (`webui._stream_file`) and the mtime-based
  `_revision`/`ProjectConflictError` watches are all platform-neutral.**
- **The face worker is CPU already** (`faces.PROVIDERS` is
  `CPUExecutionProvider`), so `reframe_detect` needs no GPU on any OS.

## Step 1 — the two things that break outright

Shipped — see HISTORY.md § The Windows crash and the display gate.

Both are in `picture.py` and both are Linux display-server concepts applied
unconditionally.

- **`display_env` calls `os.getuid()`** (`picture.py:164`, the
  `/run/user/<uid>` default for `XDG_RUNTIME_DIR`). `os.getuid` does not
  exist on Windows, so every render and `proofcut doctor` (`doctor.py:476`)
  raises `AttributeError` before anything runs.
- **The render gate refuses without a display** (`picture.py:485`): a render
  proceeds only under `WAYLAND_DISPLAY`, `DISPLAY` or
  `QT_QPA_PLATFORM=offscreen`. Qt on macOS (`cocoa`) and Windows (`windows`)
  needs none of these, so on both OSes the gate refuses a render that would
  have worked.

Verify first: `picture.display_env`, `picture.qt_is_headless`,
`picture.render` (the gate), `doctor` § display. Read HISTORY.md § Rendering
through `melt` for why the gate exists at all — the failure it catches (melt
aborting silently with a socket name but no runtime dir) is real *on Linux*.

Build: gate the whole display dance on `sys.platform == "linux"`.
`display_env()` returns `os.environ` unchanged elsewhere; the render gate
and doctor's display probe report "not applicable on this platform" rather
than pass or fail. A unit test monkeypatches `sys.platform` to `"darwin"`
and `"win32"` and asserts neither path touches `os.getuid` or refuses.

## Step 2 — the Linux-shaped resolvers, widened

Shipped — see HISTORY.md § The Linux-shaped resolvers, widened.

None of these crash; each silently narrows what a non-Linux box can find.
All keep their `PROOFCUT_*` override as the first branch.

- **melt** (`picture.melt_command`, `picture.py:125–137`): PATH, then the
  Kdenlive flatpak. Add the bundle locations: Shotcut and Kdenlive both ship
  `melt` on macOS (`/Applications/Shotcut.app/Contents/MacOS/melt`,
  `/Applications/kdenlive.app/Contents/MacOS/melt`) and Windows
  (`C:\Program Files\Shotcut\melt.exe`, the Kdenlive install's `bin\`).
  Probe with the same `-version` banner rule doctor holds to. **Whether
  those bundles carry the `qtblend`, `qimage`, `affine` and `avformat`
  modules proofcut's documents use is unmeasured and is step 4's first
  question.** The `_TMP_HINT` / `_invisible_to_flatpak` message
  (`picture.py:270–285`) is Linux-only text and should only fire there.
- **auto-editor** (`autoeditor.binary`, `autoeditor.py:55–67`): PATH, then
  `~/.local/bin`. The error text names `auto-editor-linux-x86_64`; upstream
  ships `-macos-arm64`/`-macos-x86_64`/`-windows-x86_64` builds, so name the
  one for `sys.platform`. `shutil.which` finds `.exe` on Windows by itself.
- **`proofcut open`'s browser** (`webui._resolve_app_browser`,
  `webui.py:3670–3745`): Linux chromium binary names, flatpak IDs, then
  `xdg-open`. Add the macOS app bundles (`/Applications/Google
  Chrome.app/Contents/MacOS/Google Chrome` and siblings, or `open -a`) and
  the Windows install paths, and fall back to the stdlib `webbrowser`
  module rather than `xdg-open` — it is what `xdg-open` is on every OS. The
  `--app=` chromeless window stays the preference where a chromium is
  found; the fallback is a normal tab.
- **The melt memory cap** (`picture.py:499–567`) uses `systemd-run --user
  --scope` and already runs uncapped with a warning when it is absent. Leave
  it; make the warning say the cap is Linux-only rather than "not available
  here".
- **Tailscale** (`webui.tailscale_identity`, `PROOFCUT_TAILSCALE`): the CLI is
  at `/Applications/Tailscale.app/Contents/MacOS/Tailscale` on macOS and
  `C:\Program Files\Tailscale\tailscale.exe` on Windows, neither on PATH by
  default. Add both to the probe; `--tailscale` refuses rather than falls
  back already, which is right.
- **`proofcut fonts --install`** (`fonts.user_font_dir`, `fonts.py:60–72`)
  writes to `$XDG_DATA_HOME/fonts` — correct only for fontconfig. The user
  font directory is `~/Library/Fonts` on macOS and
  `%LOCALAPPDATA%\Microsoft\Windows\Fonts` on Windows (per-user, no admin;
  Windows additionally registers fonts in the registry, and a file copied
  there without the `HKCU\...\Fonts` entry is *not* installed — check this
  before claiming the copy worked). `captions.font_match` already answers
  `available: null` when `fc-match` is missing, which is the right degrade;
  doctor's font probe should say "fontconfig is not this platform's font
  system" rather than ✗.

Verify first: each function named above, and `tests/test_doctor.py` for
how doctor's report shape is asserted, so a new "not applicable" value does
not read as a regression.

## Step 3 — the GPU workers and the platform docs

Shipped — see HISTORY.md § The GPU workers take a device, and CI runs on
three OSes. Its README paragraphs wait on step 4, as below.

- **`_tts_worker.py:44` and `_vlm_worker.py:88,103` hardcode `cuda`.** On a
  Mac there is no CUDA; on Windows a CUDA torch works as it does here. Take
  the device from the job dict (`"cuda"` default, `"mps"` or `"cpu"`
  allowed) so `vo_synth` and `describe` are *reachable* on a Mac. Whether
  Qwen3-TTS and Qwen2.5-VL under `bitsandbytes` actually run on MPS is a
  separate question — bitsandbytes has no MPS backend as of this writing —
  so the honest first build is: the Mac reports both as "unavailable: no
  CUDA", the way doctor already reports an absent optional capability, and
  the device knob exists for whoever measures MPS later.
- **Whisper on a Mac is CPU-only** (openai-whisper's MPS support is partial
  and slow enough to be a footnote). Document it; nothing to build.
- **README.md § Requirements** names `proofcut setup`'s three OSes and the
  per-tool notes each needs; what step 4 adds is one paragraph per OS naming
  the rest of the install route (Homebrew `ffmpeg imagemagick`, Shotcut for melt, the auto-editor
  release binary, `uv tool install openai-whisper`; winget or the upstream
  installers on Windows). **The ImageMagick RSVG delegate is the one to
  check by hand on both**: `magick -list format | grep RSVG`. Homebrew's
  `imagemagick` links librsvg; the Windows installer bundles it; neither is
  guaranteed across versions.
- **CI** (`.github/workflows/ci.yml`) runs on `ubuntu-24.04` only. Add
  `macos-latest` and `windows-latest` to the suite job once step 1 lands,
  with ffmpeg installed (`brew install ffmpeg`; `choco install ffmpeg`).
  The melt, whisper, auto-editor and magick tests already skip when the
  binary is absent, so the matrix covers the same ground the Linux runner
  does — everything but renders and transcription — on three OSes. A
  Windows runner is also where the path-handling class below gets caught.
  **First run 2026-09-10: macOS 3 failed, Windows 62 failed + 465 errors** —
  and "the same ground" was wrong for Windows, whose image ships ImageMagick
  and answered `@needs_melt` with something nobody installed. What it found
  and what was fixed: HISTORY.md § The first run on macOS and Windows.

## Step 4 — measure melt on macOS (needs a Mac)

This is the step that costs, and none of it can be done from here — though
GitHub's macOS runner can now reach Shotcut's melt, since
`.github/workflows/mac-demo.yml` installs it, so the first two items can be
asked there before a Mac is (HISTORY.md § The Mac test in CI). Each
item repeats a measurement CLAUDE.md records for Linux, because the
mechanism underneath it is different on macOS.

- **Module inventory.** `melt -query producers`, `-query filters`,
  `-query transitions` from the Shotcut and Kdenlive bundles, checked for
  every module `mlt.py` emits. A missing module renders *something* at exit
  0 — that is the whole melt lesson — so this is a readback, not a query
  alone.
- **A layered render read back against the timeline**, the way
  `picture.render` already does (frame count from `mlt.declared_frames`,
  duration from ffprobe), on a project with a cue, a card and a music bed.
- **Font resolution in libass is CoreText on macOS, not fontconfig.** Every
  finding in CLAUDE.md § the font-name bullets (the Nerd Font symbol face
  first pick, fontconfig weight 200 for Bold, `fc-match` treating a `-24`
  tail as a size) is void; a *different* substitution behaviour replaces it
  and has to be found the same way — `ffmpeg -v verbose` and the
  `fontselect` lines, then the burn's pixels. Start with the vendored
  caption face installed and not installed.
- **The card renderer**: `magick` with RSVG on a `receipt` and a portrait
  variant, compared pixel-wise against a Linux render of the same slots.
  The font-provenance half (`fonts.probe`'s `drew`) is measured there too.
- **Paths**: a project path with a space in it, since `/Users/<name>/My
  Movie/` is the normal case on macOS and every MLT `resource` attribute
  and every `magick` argument carries one.
- **The seven `@needs_melt` tests in `tests/test_server_stdio.py`** run
  green with no `QT_QPA_PLATFORM` set — that is step 1's gate proven on a
  real Qt platform.

## Step 5 — Windows: the three bugs, a test kit, and a stranger's run

Written 2026-09-13, after GitHub run 34778624429 answered the last two open
questions (HISTORY.md § The Windows run that answered). The shape is the Mac's
(LAUNCH.md § Step 2, HISTORY.md § The Mac test in CI): fix what CI found, give
the demo a kit a person can run, prove the kit on a runner, then ask for a
report. **No Windows box is reachable from here, so a Windows claim is settled
only by a CI log or a tester's report**, and every CI reading needs Tyler to
sync the GitHub mirror. So batch the code fixes (5a) and the kit (5b) into
one sync where possible.

**5a — the three bugs.** Each gets a test that fails against the old code.

1. **`melt_command` takes a PATH `melt` that is not MLT.** WiX's `melt.EXE`
   on Windows; freeze's `melt` on Fedora. Hold every PATH and bundle
   candidate to doctor's banner rule and skip one that fails it. Move
   `doctor._MELT_BANNER` into `picture` so there is one copy, and cache the
   verdict per `(path, mtime)`, because `melt_command` runs several times per
   render. `PROOFCUT_MELT` and the flatpak are not probed. When only impostors
   were found, the refusal names each by path. The suite's `needs_melt` gate
   then skips on the Windows runner, which has no MLT. Test with
   `tests/stubs.py`'s `write_stub`: an impostor `melt` ahead of a real-banner
   one picks the second, and an impostor alone refuses by name.
2. **A registered font GDI has not loaded.** After the HKCU write,
   `_register_windows` calls `gdi32.AddFontResourceW` on each face through
   `ctypes` and broadcasts `WM_FONTCHANGE` (`SendMessageTimeoutW`,
   `HWND_BROADCAST`, `SMTO_ABORTIFHUNG`). It reports how many loaded, beside
   `registered`, never folded into it. **Unverified:** that a load from the
   `fonts --install` process is still visible to a later ffmpeg in the same
   session. The docs say the load lasts for the session, not the process. CI
   runs `fonts --install` and the probe as separate steps, so the next
   Windows log answers it: the three font tests go green or they do not. If
   they do not, the fallback is libass's own font directory (ffmpeg's `ass`
   filter `fontsdir=`, relative to the burn's cwd, never a drive letter),
   and it changes what the Linux burn measured, so it is a decision rather
   than a patch.
3. **`PROOFCUT_MELT` and `PROOFCUT_MAGICK` go through POSIX `shlex.split`.** Where
   `os.name == "nt"`, an override naming an existing file is taken whole as
   one argv element. Anything else splits with `posix=False` and strips one
   layer of surrounding quotes. Grep for every other `PROOFCUT_*` that is split
   rather than taken as a path before calling this done.

Then read the in-flight and next Windows logs for anything else. Doctor's
new text-filter row on Chocolatey's ffmpeg is the one reading nobody has
seen.

**Read off the in-flight log** (run 34779525442, lucid `a5ec829`): the same 9
failures as run 34778624429 and nothing new among them, 6 on WiX's
`melt.EXE` and 3 on Outfit, with 2016 passed.

- **Doctor's ffmpeg row is ✓ on Chocolatey's ffmpeg**, which is gyan.dev's
  `9.0.1-essentials_build`, so the text-filter check found `drawtext` and
  `ass` there. That is the build 5b downloads.
- **Doctor's caption-font fix loops.** It tells the runner to run `lucid fonts
  --install`, which had run one step earlier and reported `registered: true`.
  If 5a.2's GDI load does not turn the three font tests green, that fix line
  is the next thing to change: it should name GDI, not the install.
- **The Windows suite takes 23 min** (1392 s of pytest), against 13 on
  Ubuntu. At 2x billing while the repo is private, that is ~50 billed
  minutes per push, before windows-demo's own run.

**What the first sync answered** (HISTORY.md § The first windows-demo run):
5a.1 skips WiX's melt on the runner. 5a.2's GDI load did not make Outfit
draw, so the fallback above was taken, Windows only. It had to stage static
instances rather than the variable file, because libass names a face in its
font directory by name ID 1, `Outfit Thin`. Shotcut's melt names itself
`melt.exe` in its banner, which doctor had never accepted. The ci.yml Windows
leg is `continue-on-error`.

**What the flip's sync answered** (HISTORY.md § The sync the repo went
public on, lucid `a382e6c`): **every Windows reading is green.** ci.yml's
Windows leg passed 2048 with 0 failed, so the static Outfit instances in
libass's font directory are the fix, and that leg's `continue-on-error`
comes off. windows-demo ran the kit to a checked render, 286 of 286 frames
and `verify` 0.971 through Shotcut's `melt.exe`. mac-demo stayed green after
5a.1. That settles 5a–5c, and the only Windows claim it leaves unsettled is
a person's run (Step 6).

**The next ci run found a flake** (run 34793271514, `8eae34c`, docs and
ci.yml only): Windows failed
`test_undo_refuses_when_the_manifest_moved_past_the_snapshot_it_read`,
2047 passed. `restore()` could only have skipped the refusal if the second
writer's `write_manifest` left the same `st_mtime_ns` as the first's, so
`_manifest_stamp` (mtime alone) cannot see two writes inside one Windows
file-time tick. The test is right and the stamp is the defect; the fix is a
stamp that changes on every write. **Chosen and built the same evening: a
sha256 of the manifest's bytes**, read back off disk after every write. A
new test pins the mtime back to what the stale instance saw and the refusal
still fires; against the old stamp that test does not raise. HISTORY.md
§ The stamp that was a clock.

**5b — `scripts/windows_trial.ps1`, the Mac kit's twin.** Same contract as
`mac_trial.sh`: it asks before starting, records everything it adds,
`-Uninstall` removes exactly that, it runs DEMO.md's commands verbatim and
stops at the first failure, and it zips a report with the home folder
replaced by `~`. Its own shape, recommended:

- **Portable downloads into one folder under `%LOCALAPPDATA%`, not winget.**
  winget is not on every Windows 10, is not guaranteed on the runner, and
  its uninstall is per-package. A folder makes the runner and a person's PC
  run the same code, needs no admin, and uninstalls by deleting it. The
  pieces are uv's `install.ps1` (or its zip), a gyan.dev ffmpeg build
  (confirm libx264, drawtext and `ass` with doctor's own row), auto-editor's
  `-windows-x86_64` release binary, the espeak-ng MSI unpacked with
  `msiexec /a` (no install and no admin; verify that it runs from the
  unpacked tree), Shotcut's portable zip for `melt.exe`, and whisper by
  `uv tool install`. Pin each URL and SHA-256 in the script.
- Portable Shotcut sits outside `melt_bundles()`, so the kit sets
  `PROOFCUT_MELT`, which is the reason 5a.3 comes first.
- PowerShell 5.1 syntax, since that is what a stock Windows has. Write for
  `-ExecutionPolicy Bypass -File`, the README's one-line invocation.
- `--pack` stays on the bash kit's side unless a friend needs one. The
  public README route is a clone.
- Syntax-check it here with a PowerShell tarball under `~/proofcut-work/`
  (`pwsh` is not installed on this box). That is a parse, not a run.

**5c — `.github/workflows/windows-demo.yml`.** mac-demo.yml's twin: it runs the
kit on `windows-latest` with no prompts, then `scripts/mac_trial_check.py`,
which is OS-neutral already and is renamed `trial_check.py` in the same
commit, with mac-demo.yml updated. It uploads the zip. Its path triggers
include the kit. It is green when the check passes, never on the kit's exit
code. **Then decide the ci.yml Windows job.** If 5a leaves it green there is
nothing to decide. If not, recommend `continue-on-error` on that matrix leg
only, so a Windows finding stops failing the run while the log still shows
it.

**5d — README.md § Help wanted, and the issue form.** Once windows-demo is
green, add a Windows paragraph beside the Mac one: a clone plus one
`powershell -ExecutionPolicy Bypass -File proofcut\scripts\windows_trial.ps1`
line, what it installs, that `-Uninstall` reverses it, and a link to
`.github/ISSUE_TEMPLATE/windows-test.yml`. That form is `mac-test.yml`'s
twin, with Windows 10/11 and x64/ARM64 in place of the chip. Launch step 9's
label-and-pin applies to a `windows-test` label too — **issue #2, pinned
2026-09-13**, the Mac issue's twin, from
`~/proofcut-work/spikes/launch-release/WINDOWS-ISSUE.md`; it says the kit's downloads
are x64 builds, so an ARM64 PC runs them under emulation, untried. Requirements' Windows
sentence says no person has run it until a person's report arrives (Step 6),
windows-demo green or not.

**5e — measure what the demo does not reach.** Everything in step 4 again,
plus the class of defects only Windows has. **`scripts/windows_probe.ps1`
asks the path, drive, case and caption classes below** — the demo's edit
re-run in a folder shaped for each, every render read back, beside a plain
control — on a person's PC after the kit, and on windows-demo's runner
(HISTORY.md § The editor on Windows, looked at). Its first answers are
candidates until judged:

- **Drive letters and backslashes inside MLT XML.** MLT accepts forward
  slashes on Windows; whether `mlt.py`'s `str(Path)` output (backslashes)
  is read correctly by melt, and whether `C:` survives melt's own
  `resource` parsing, is unmeasured. The caption burn sidesteps the
  best-known ffmpeg filter trap already — `captions.py:874` passes
  `-vf ass=proofcut.ass` as a relative name against a working directory, so
  no drive-letter colon ever enters a filter string — confirm that cwd
  handling holds rather than re-deriving it. **`media.scene_cuts` did not
  dodge it** — its `metadata=print:file=` carried an absolute temp path and
  every scan refused on the first Windows CI run; it takes the caption
  burn's cwd route now, and it was the only other filter string with a path.
- **Symlinks refuse without Developer Mode**, and `_place` already handles
  that; confirm the `reference` route resolves through `media_path()` on a
  project on a different drive from its footage.
- **`MAX_PATH` (260 chars).** `cache/sheets/`, `cache/thumbs/<clip_id>/`
  and the render scratch nest deeply under a project path; either opt the
  process into long paths or measure the deepest path proofcut writes.
  *Measured 2026-09-14:* with `LongPathsEnabled` 0 a 235-character root died
  at `init` on the 248-character directory limit; with it on, 266-character
  paths rendered cleanly. `Project.create` now refuses a root past 148 while
  it is off — HISTORY.md § A long project path on Windows.
- **Case-insensitive filesystem vs the confinement checks.** `server._confine`
  and `webui`'s root checks compare `resolve()`d paths (`server.py:171`);
  `Path.resolve()` on Windows returns on-disk case for existing paths, so
  two spellings of one directory should compare equal — test it rather than
  trust it, since a confinement check is a guard and a wrong one is silent.
- **libass uses DirectWrite** — the third font-resolution system, measured
  the same way as step 4's.
- **The `claude` binary is `claude.cmd` under npm on Windows**, and
  `subprocess.Popen` does not resolve `.cmd` without `shell=True` or
  `shutil.which` first. `webui._agent_bin` returns the bare name; route it
  through `shutil.which` and let `PROOFCUT_AGENT_BIN` override, or the agent
  pane fails the way CLAUDE.md § the agent panel had no tools describes —
  silently. **Done 2026-09-11**, unmeasured on a real `claude.cmd`. The half
  it leaves: **killing a `.cmd` kills `cmd.exe` and not the `node` under
  it** — TerminateProcess does not take children — so "New Task" and a
  model change would leave the old agent running, editing, holding its
  pipes. Claude Code's native installer is a `claude.exe` and does not have
  the problem; an npm install does. Measure it on the Windows box before
  building a tree kill (`taskkill /T`, or a Job object) for it.

## Step 6 — say so

README.md § Requirements drops "Linux only" for each OS as its step-4/5
measurements land, and not before. `proofcut doctor` on each OS is the
evidence: paste its output into the HISTORY.md section. A platform whose
render has not been read back is still unsupported, whatever runs.

## What this plan deliberately does not do

- **No installer, no packaged app.** `uv sync` and the tool list is the
  route on every OS; a `.dmg` or `.msi` is a different project. (`proofcut
  setup` installs the external tools on Linux, Windows and an Intel Mac, and
  packages nothing; docs/plans/INSTALL.md.)
- **No transcoding to dodge a platform codec gap.** The lean is to not
  transcode (CLAUDE.md § `vfr`); a platform ffmpeg missing an encoder is
  reported by doctor, not worked around.
- **No fontconfig on macOS or Windows.** Both have a native font system
  libass already uses; installing fontconfig to keep `fc-match` answering
  would measure the wrong resolver, which is the disagreement CLAUDE.md
  already records for Linux.
