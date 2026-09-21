# proofcut

<!-- mcp-name: io.github.tydude001/proofcut -->

**An AI video editor that proves its cuts.** Your recordings in, a finished,
mastered film out: cut by transcript, with b-roll, cards, music and captions,
and every step an agent can call. proofcut renders on your own machine, then
transcribes the render and checks that it says what the edit says.

https://github.com/user-attachments/assets/4153d180-3d7c-4c70-af5f-54d63d0a8bd5

Above: an agent cutting a demo video, unattended. The two runs it was cut
from, uncut (silent: the recorder took frames only, and the voice is in the
film the agent cut): [the workspace](https://github.com/tydude001/proofcut/releases/download/v0.23.0/proofcut-v0.23.0-uncut-workspace-run.mp4)
(2:26) and [Claude Code with the proofcut plugin](https://github.com/tydude001/proofcut/releases/download/v0.23.0/proofcut-v0.23.0-uncut-claude-code-run.mp4)
(3:09).

## Why it exists

Most of the work in a narrated video — an essay, a tutorial, a screencast, a
talk — is bookkeeping. Find the retakes and cut them clean. Put the right
footage under each line. Level the music under the voice, caption it, end on
a card, render it, master it. An agent can do that bookkeeping now, given an
editor it can drive.

What an agent cannot do on its own is know that the file it rendered is the
film it meant. ffmpeg, melt and auto-editor all exit 0 on some failures, so a
render can drop a line, keep a retake, add a frame of black or carry no
captions and still report success — and every check that reads the project
rather than the file agrees with it. The usual way to find out is to watch
the whole thing.

proofcut is an editor built around that gap. You, or an agent, edit a
timeline addressed by the words in it. proofcut renders it on your machine,
then transcribes the render, counts its frames, and says where the file and
the edit disagree.

## Run, not staged

[TRIAL.md](https://github.com/tydude001/proofcut/blob/main/docs/TRIAL.md)
scores three unattended runs, each handed a goal and no steps, and each
passed every one of its checks:

- **The demo cut**, the one above.
- **Real footage.** 96 seconds of narration with its fluffed takes left in,
  plus four clips of film footage. The agent cut it to 45 seconds, chose
  footage by what each line was about, burned captions, and checked its own
  render: all 123 expected words heard back.
- **A whole film.** The demo material plus a score, briefed as a finished
  film ready to upload. In 192 seconds and $2.27 the agent cut it, laid the
  music under the voice, ended on a card, mastered it to −16 LUFS, and
  checked it. The score, the level and the end card were each measured in
  the delivered file, not taken from the project.

## What it can do

One project, and proofcut's own commands from the first import to the
delivered file. No NLE finishes the film, and nothing else touches the render.
Each stage is one command, and each row links the section of the
[manual](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md) that
walks it.

| Stage | Command |
|---|---|
| [Bring in the voiceover and footage, and transcribe](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#the-core-loop) | `import`, `transcribe` |
| [Cut retakes and asides by naming their words](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#word-indices-and-locate) | `cut vo 111:114` |
| [Hang b-roll and cards off the lines they belong to](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#cards-cues-and-tails) | `cue add`, `card new` |
| [Open cold on a scene](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#a-cold-open), [end on a card](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#cards-cues-and-tails) | `head`, `tail` |
| [Play the footage's own lines in a gap, or under the narration](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#holds-the-films-own-lines) | `hold add`, `hold under` |
| [Score it: placed passages, crossfaded, levelled under the voice](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#the-music-bed) | `music` |
| [Pull breaths down without cutting them](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#attenuation) | `attenuate` |
| [Render](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#renders-presets-undo-migration) and [master to a loudness target](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#the-master) | `export --render --loudness -16` |
| [Burn in captions](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#captions) | `captions --burn` |
| [Check the render says what the edit says](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#verifying-a-render) | `verify`, [`frames`](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#frames-film-check-black-spots), [`hold check`](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#holds-the-films-own-lines) |

Two video essays of five to six minutes, first finished in Kdenlive, have
been rebuilt with these commands alone and measured against the delivered
files. One came out the same length to the frame. The other matched all 63
of its voiceover ranges to the millisecond, with the voice aligned to the
sample. Both master at the original's −16 LUFS. The measurements are in
[HISTORY.md](https://github.com/tydude001/proofcut/blob/main/docs/HISTORY.md).

Beyond those stages:

- **Cuts stay addressable.** A word's index never renumbers, so
  `cut vo 111:114` names the same words however many cuts came before it,
  and a cue hung off a phrase stays addressed through every cut around it.
- **An agent that can look.** `shot-sheet` draws the whole picture track as
  one labelled grid, and `footage-sheet` browses a clip you haven't cut yet.
  Both return the image itself over MCP, not a path the agent can't open.
- **B-roll by description.** `describe` writes what is on screen in each
  ~10-second window of footage, so an agent can choose a clip by what a line
  is about.
- **Transcript self-checks.** Retake seams, invented words, swallowed repeats
  and suspect durations are reported when a transcript is attached, and
  `unspoken` lets the render itself testify to words nobody said.
- **Reframing for another aspect.** Per-shot crop windows, face-aware
  proposals (`reframe-detect`), a review sheet, and stacked splits for two
  speakers. Cards are redrawn at the new frame size, never stretched.
- **Screen recordings.** Named instants (`events`) that a crop, a sound or a
  speed change can hang off, a clip inset into the recording, and a retime
  for the slow parts.
- **Built for agents.** 109 MCP tools with typed inputs, structured returns
  and read/write annotations, so Claude Code, Codex or your own agent can
  drive it and a permission layer can tell a look from a change.
- **No lock-in.** The timeline is OpenTimelineIO, the manifest is JSON, and
  the render is ffmpeg's and MLT's. Export a `.kdenlive` or OTIO file and
  finish anywhere.

![Frame mode: a shot list beside the selected shot's windows — each crop
drawn as a rect on three of the source's own frames, over a filmstrip of the
whole shot with the sampled instants ticked on it, the window's rect quoted
in source pixels, Approve/Re-frame beside it, and coverage chips for stale
framing and unexplained steps](https://raw.githubusercontent.com/tydude001/proofcut/main/docs/img/frame-mode.png)

## How people use it

**Tell an agent what film you want.** In Claude Code, install the plugin and
describe the film: which recording is the voice, what the b-roll is, where
the music goes, what it ends on. The agent imports, transcribes, cuts the
retakes, hangs footage off the lines it belongs to, scores, masters and
checks the render, using proofcut's tools and nothing else. It can look at
the picture track as a labelled grid while it works, so it sees what it
placed rather than a filename. Every MCP client runs the same server; the
plugin is the one that needs no setup.

```
/plugin marketplace add tydude001/proofcut
/plugin install proofcut@proofcut
```

**Cut from a shell, by naming words.** Every tool is also a `proofcut`
subcommand printing JSON, so a cut is a script you can read, re-run and
diff. `--plan` prints what a range says before anything changes, and `undo`
walks it back.

```sh
uv run proofcut -C myproject cut vo 111:114 --plan
```

**Edit by hand in the workspace.** Strike words in the transcript, drag-trim
and razor on the timeline, review every crop in place, and export from
Finish. It plays the source through the edit, so seeing a cut costs no
render, and the truth strip warns while you edit if the film would ship
wrong. The agent sits in a side rail of the same window.

```sh
uv run proofcut -C myproject open
```

**Finish elsewhere, and still prove the file.** Rough-cut here, export a
`.kdenlive` or OpenTimelineIO file, finish in Kdenlive, Resolve or Premiere,
and bring the trim back with `import-edit`. Then point `verify` at the
delivered file, and a retake left in is caught before anyone watches.

```sh
uv run proofcut -C myproject verify final.mp4
```

**Cut a vertical teaser from the film.** `reel` copies a span of the finished
film into a second project at another canvas, so the film itself is never
reshaped to take one render. It names every picture the teaser will not
have and pins the ones it keeps to the frames the film showed, so the
teaser shows what the film showed. Frame mode then reviews each crop on
the source's own frames.

```sh
uv run proofcut -C myproject reel ../teaser 1:32+44 --canvas 1080x1920
```

The first three are walked in [§ Try it](#try-it); the reel and the
round-trip are in the
[manual](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md#the-reel--a-derived-vertical-cut).

![The proofcut workspace on the demo project: the transcript with a retake struck
through, the preview drawing the shot under the playhead with its captions, the
side rail on its agent tab reporting a finished render against the timeline,
and the layered timeline below — picture, waveform and captions as three
projections of one edit](https://raw.githubusercontent.com/tydude001/proofcut/main/docs/img/edit-mode.png)

## What it holds to

- **The file is the evidence.** A check reads the render, never the project.
  What `verify`, `frames` and `hold check` report is measured off the
  delivered file, because a project can say captions are burned, or a line
  is in, about a file that has neither.
- **Your recordings, cut, not invented.** proofcut makes no footage and
  writes no script. It takes what you recorded to a film, in an order you or
  your agent chose. It is not a text-to-video generator.
- **Your machine, no meter.** Commercial AI editors are apps around a
  metered cloud service. proofcut transcribes, edits and renders locally:
  no account, no per-minute billing, and no cloud service of its own. The
  only thing that talks to a model provider is the agent you choose to run,
  and your footage stays where it is.
- **Nothing an agent does is out of your reach.** The MCP server, the command
  line and the workspace call the same operations, and a test holds every
  tool to a matching command printing JSON. Changes take `--plan` to show
  what they would do first, anything addressed by word echoes the words it
  resolved to, and `undo --steps N` walks back an agent's whole turn.
- **Say what is not measured.** The design record is public — the plans,
  and a dated history of what shipped and what the evidence said, failures
  included. Where only a CI runner has done something, the docs say a runner
  did it, not a person; where a check is a model's reading of a picture, it
  is reported and never gates.

## Try it

Nothing below installs anything until you say so, and every step prints what
it would do first. [§ What it puts on your machine](#what-it-puts-on-your-machine)
is the whole footprint and how to reverse it.

**Check your machine**, before cloning anything. `proofcut doctor` probes every
tool proofcut uses and prints the fix for anything missing
([§ Requirements](#requirements) has the list). It only looks — it installs
nothing and writes nothing. With [uv](https://docs.astral.sh/uv/) installed:

```sh
uvx proofcut doctor
```

That first run downloads Python 3.13 if uv has none, plus proofcut's
dependencies — about 230 MB, all of it inside uv's own cache, which
`uv cache clean` empties.

**Then read what an install would do.** On Linux, Windows and a Mac of either
kind,
`proofcut setup --plan` prints every piece it would fetch, its size, and which
doctor row asked for it, then stops without touching anything:

```sh
uvx proofcut setup --plan
```

```
proofcut setup — installs into /home/you/.local/share/proofcut/deps

Will install
  ffmpeg — n8.1.2 (BtbN autobuild-2026-08-31-13-27), about 126 MB
      because: ffmpeg is not on PATH. ffprobe is not on PATH.
  whisper — openai-whisper (uv tool, Python 3.12, cpu torch), about 1.9 GB
      because: whisper is not on PATH.
  auto-editor — 31.6.0, about 46 MB
      because: auto-editor is not on PATH.
  melt — Shotcut 26.8.1 (melt 7.41.0), about 155 MB
      because: melt is not on PATH.
  total: about 2.2 GB
```

That is a bare machine. Yours will be shorter, because setup installs nothing
doctor passed — a working ffmpeg or melt of your own is never touched. With an
NVIDIA GPU the whisper row is CUDA torch and the total is about 5.8 GB.

Drop `--plan` to go ahead. It reprints the plan, asks once, and installs for
you alone, with no sudo or administrator rights; `proofcut setup --uninstall`
removes exactly what it added, and nothing you already had.

```sh
uvx proofcut setup
```

The demo and your own recordings run from a checkout:

```sh
git clone https://github.com/tydude001/proofcut && cd proofcut
uv sync
```

### The two-minute demo

No footage needed. [docs/DEMO.md](https://github.com/tydude001/proofcut/blob/main/docs/DEMO.md)
makes a voiceover with a real retake, b-roll and a score, then walks a whole
small film: cut the retake by naming its words, hang b-roll off a phrase, lay
the score under the voice, render and master it, end on a card, and check the
render against the timeline.

```sh
uv run python scripts/make_demo.py ~/proofcut-demo
```

### In Claude Code

The plugin registers proofcut's MCP server, so every tool is available with
no setup of your own:

```
/plugin marketplace add tydude001/proofcut
/plugin install proofcut@proofcut
```

The first start downloads about 175 MB of Python dependencies, and Claude
Code gives a server 30 seconds to connect. On a slow connection, start that
first session as `MCP_TIMEOUT=300000 claude`, or reconnect proofcut in `/mcp`
once the download has finished.

Any other MCP client runs the same server, from a checkout or with no
checkout at all:

```sh
uv run --project /path/to/proofcut proofcut mcp
uvx proofcut mcp
```

### On your own recording

```sh
uv run proofcut init myproject
uv run proofcut -C myproject import VO.wav --clip-id vo
uv run proofcut -C myproject transcribe vo                  # whisper, word-timed
uv run proofcut -C myproject seed vo                        # auto-editor strips silences
uv run proofcut -C myproject transcript vo --search "here's the thing"
uv run proofcut -C myproject cut vo 111:114 --plan          # what do those indices say?
uv run proofcut -C myproject cut vo 111:114 --pad 0.1       # inclusive word range
uv run proofcut -C myproject export final.mp4 --render      # or a .kdenlive to finish in an NLE
uv run proofcut -C myproject verify final.mp4               # did the render say what you edited?
```

To watch the edit instead, open the workspace:

```sh
uv run proofcut -C myproject open        # server plus an app window
uv run proofcut -C myproject web --open  # the same page in a browser tab
```

proofcut is 0.x software. A project from an older version is refused rather
than guessed at, and `proofcut migrate` brings it forward.

## What it puts on your machine

proofcut is a local tool that needs real media binaries, so `proofcut setup`
does download a few hundred megabytes. Here is all of it, where it goes, and
what takes it away. Sizes are a Linux x86_64 bare machine; `proofcut setup
--plan` prints yours.

| What | Where | Size | Removed by |
|---|---|---|---|
| ffmpeg, ffprobe | setup's folder, with symlinks in `~/.local/bin` | 126 MB | `proofcut setup --uninstall` |
| auto-editor | setup's folder | 46 MB | `proofcut setup --uninstall` |
| melt (Shotcut's portable build) | setup's folder | 155 MB | `proofcut setup --uninstall` |
| whisper | a uv tool, plus the Python 3.12 uv fetches for it | 1.9 GB, or 5.5 GB with an NVIDIA GPU | `proofcut setup --uninstall` |
| proofcut and its Python dependencies | uv's cache | 230 MB | `uv cache clean`, `uv tool uninstall proofcut` |
| the demo's media | the directory you name it | under 2 MB | delete that directory |

"Setup's folder" is one directory: `~/.local/share/proofcut/deps`
(`$XDG_DATA_HOME` if you set it), or `%LOCALAPPDATA%\proofcut\deps` on
Windows. It holds everything except whisper, which is a uv tool because that is
how whisper ships.

Five rules it holds to, each one enforced by a test rather than promised here:

- **`--plan` writes nothing at all**, and its exit code reports only what is
  missing (`test_setup_plan_writes_nothing_and_exits_by_what_is_missing`), so
  reading the plan can never turn into performing it.
- **Doctor's report is the input.** A piece is installed only when its row is
  ✗. A tool you already have is never replaced, and never upgraded behind your
  back.
- **Every download is pinned by URL and SHA-256**, bumped by hand, never
  resolved from a `latest` tag. A hash that does not match leaves nothing
  behind.
- **No sudo, no administrator rights, no distribution packages**, and nothing
  is ever written over an existing file.
- **Every link, file, folder and uv tool is recorded**, and `--uninstall`
  removes exactly those — the Python uv fetched for whisper included.
  `tests/test_install.py` installs the lot into a fake home, uninstalls, and
  asserts the home's listing is what it was before
  (`test_install_then_uninstall_leaves_the_home_as_it_was`), so "removes
  exactly what it added" is checked on every run of the suite, not just meant.
  A link you repointed yourself is left alone, because it is yours now.

To see a removal before it happens:

```sh
proofcut setup --uninstall --plan
```

Two things setup deliberately does not do: it is a command you type and never
an MCP tool, so an agent cannot start a 2 GB download or change your PATH; and
it touches nothing outside your own user account.

## Help wanted: a Mac or a Windows run

GitHub's macOS and Windows runners take the demo to a checked render, but a
runner never reads the instructions. On a Mac, one person has taken the demo
to a checked render, on an Intel Mac (i7-8850H, macOS 15.7.9); on Apple
silicon only the runner has. On Windows, the author's own Windows 11 laptop has, twice, and
nobody else's PC; Windows 10 and ARM64 PCs have not been tried at all. If you
have one of these machines and half an hour, one script installs what
proofcut needs, makes a short test video, has proofcut cut, score, master and
check it, and puts a report on your Desktop. It asks before it starts,
records what it added, and removes exactly that on request, nothing you
already had. A run that stops at the first step is just as useful, because
where it stops is the finding.

On a Mac:

```sh
git clone https://github.com/tydude001/proofcut
bash proofcut/scripts/mac_trial.sh
```

On Apple silicon it installs `uv`, `ffmpeg-full`, `espeak-ng` and
`auto-editor` with Homebrew (and Homebrew itself if you have none), plus the
Shotcut app for its renderer and whisper. Homebrew no longer installs on
Intel Macs, so on one the script downloads the same tools into
`~/proofcut-mac-trial` instead, and needs only Apple's Command Line Tools.
`bash proofcut/scripts/mac_trial.sh --uninstall` removes what it added.
Then [file the report](https://github.com/tydude001/proofcut/issues/new?template=mac-test.yml).

On Windows, from PowerShell:

```powershell
git clone https://github.com/tydude001/proofcut
powershell -ExecutionPolicy Bypass -File proofcut\scripts\windows_trial.ps1
```

It downloads `uv`, `ffmpeg`, `auto-editor`, `espeak-ng`, Shotcut's renderer
and whisper into one folder under `%LOCALAPPDATA%`. Nothing is installed
system-wide and it needs no administrator rights. The same command with
`-Uninstall` deletes that folder. It puts `proofcut-windows-report.zip` on
your Desktop with your home folder's name taken out; [file the report](https://github.com/tydude001/proofcut/issues/new?template=windows-test.yml).

## Requirements

proofcut is developed on Linux (a Fedora-based desktop). On macOS and Windows
the test suite passes on CI; where each OS stands by hand is
[§ Help wanted](#help-wanted-a-mac-or-a-windows-run) above, and in detail
[docs/plans/PORTABILITY.md](https://github.com/tydude001/proofcut/blob/main/docs/plans/PORTABILITY.md).

Every hard part of an editor already exists as mature open source, and
proofcut is the layer that lets an agent drive those tools and check what
they produced. Run `uv run proofcut doctor` to check everything below at
once. On Linux, Windows and a Mac of either kind, `uv run proofcut setup` installs
any of the last four that doctor marks ✗
([§ What it puts on your machine](#what-it-puts-on-your-machine), and
`--plan` to read it first): a static ffmpeg, whisper,
auto-editor's release binary and Shotcut's melt — on Linux the portable
build, which renders with no display at all
([docs/plans/INSTALL.md](https://github.com/tydude001/proofcut/blob/main/docs/plans/INSTALL.md)).

| You need | For | Notes |
|---|---|---|
| **Python 3.13** and [uv](https://docs.astral.sh/uv/) | everything | `uv sync` installs the Python side. The only runtime dependencies are `mcp` and OpenTimelineIO, which holds the timeline and exports it to other editors. |
| **ffmpeg / ffprobe** built with `libx264`, freetype and libass | cutting, concatenating, captions, rendering | Fedora's default `ffmpeg-free` has no `libx264`: use RPM Fusion's `ffmpeg`. On a Mac, Homebrew's `ffmpeg` lacks freetype and libass: install `ffmpeg-full` and put `$(brew --prefix ffmpeg-full)/bin` first on `PATH` (it is keg-only). |
| **[auto-editor](https://github.com/WyattBlue/auto-editor) 31+** | silence and bad-take removal, single-source renders | Install the upstream binary. The PyPI package is a stale 29.x. |
| **whisper** | word-timed transcription (30+ languages), render verification | Any `openai-whisper` install. `uv tool install --python 3.12 openai-whisper` is the short route (3.12 because torch's Intel-Mac builds stop there, and on an Intel Mac also `--with 'numpy<2'`, which that last torch needs); add `--torch-backend cpu` without an NVIDIA GPU (1.9 GB instead of 5.5 GB). Found via `PROOFCUT_WHISPER`, then `PATH`. The CPU build transcribed the demo's 19-second voiceover in 33 seconds. |
| **MLT (`melt`)** | layered renders (b-roll, cards, music) | Your distribution's MLT package (`mlt` on Fedora, whose `melt` package is an unrelated compression tool), or Kdenlive, whose flatpak copy is found automatically. `PROOFCUT_MELT` overrides both. |

Optional. Each unlocks one feature, `proofcut doctor` reports whether it is
available, and everything else works without it:

| Optional | Unlocks | Notes |
|---|---|---|
| **ImageMagick 7** (`magick`) | title and end cards, rendered from SVG templates | ImageMagick 6's `convert` is not used, so distributions that still ship 6 (Ubuntu 24.04) need ImageMagick's own build. |
| **[Claude Code](https://docs.claude.com/en/docs/claude-code)** (`claude`, logged in) | the agent pane in the workspace | `proofcut mcp` works with any MCP client; only the pane runs `claude` itself. |
| **`PROOFCUT_VLM`** | `describe` (b-roll search by what's on screen) | The python of a venv with torch, transformers, bitsandbytes and Pillow, on a CUDA GPU. The Qwen2.5-VL model downloads on first use. |
| **`PROOFCUT_FACE`** | `reframe-detect` (face-aware crops) | The python of a venv with insightface, onnxruntime and opencv-python. |
| **`PROOFCUT_TTS`**, **`PROOFCUT_TTS_MODEL`**, **`PROOFCUT_TTS_VOICE`** | `vo-synth` (a line in a cloned voice) | A python with qwen-tts and a CUDA torch, a local Qwen3-TTS snapshot, and a directory holding a reference clip of the voice. There is no default voice, on purpose. |

## Working on proofcut

Whether you're a person or a coding agent, start with
[CLAUDE.md](https://github.com/tydude001/proofcut/blob/main/CLAUDE.md). It holds the rules and the traps this repo has
already hit, and Claude Code loads it automatically.
[CONTRIBUTING.md](https://github.com/tydude001/proofcut/blob/main/CONTRIBUTING.md) is the short version a pull request is
checked against, and [SECURITY.md](https://github.com/tydude001/proofcut/blob/main/SECURITY.md) says how to report a
vulnerability.

Where things live:

| Path | What it is |
|---|---|
| `src/proofcut/ops.py` | Every operation. The MCP tools, the CLI and the web UI all call these. |
| `src/proofcut/server.py` | The MCP server. Register tools with `@_tool()`, never `@mcp.tool()`. |
| `src/proofcut/cli.py` | The `proofcut` command: one subcommand per tool, printing JSON. |
| `src/proofcut/webui.py`, `src/proofcut/web/` | The workspace. It posts to `ops` and renders what comes back; it never decides anything itself. |
| `src/proofcut/project.py`, `timeline.py` | The project manifest (`proofcut.json`) and the OTIO timeline. |
| `tests/` | `test_server_stdio.py` drives a real `proofcut mcp` subprocess; `test_webui_http.py` a real socket. |
| `scripts/` | The demo maker, the Mac and Windows trial kits, screenshot capture. |
| `docs/` | The manual, the demo, and the design record (below). |

Run the checks:

```sh
uv sync
uv run ruff check .      # never `ruff format`; see CONTRIBUTING.md
uv run pytest
```

The suite talks to a real `proofcut mcp` subprocess, so it is slower than a
pure unit suite. Tests that need whisper, auto-editor, melt or ImageMagick
skip when the tool is missing. Tests that render through `melt` also need a
display: on a headless machine use `QT_QPA_PLATFORM=offscreen` or `xvfb-run
-a` (`proofcut doctor` tells you which your MLT needs). Without one they fail
with "no display for MLT's Qt module to open", which is the environment, not a
regression.

The documentation:

- [docs/MANUAL.md](https://github.com/tydude001/proofcut/blob/main/docs/MANUAL.md): every command, with the reasoning.
- [docs/DEMO.md](https://github.com/tydude001/proofcut/blob/main/docs/DEMO.md): the whole loop in two minutes.

proofcut's reasoning is part of what it ships, so the design record is public:

- [PLAN.md](https://github.com/tydude001/proofcut/blob/main/docs/PLAN.md): architecture, stack decisions, open questions.
- [HISTORY.md](https://github.com/tydude001/proofcut/blob/main/docs/HISTORY.md): the dated record of what shipped and what the
  evidence said.
- [PRIOR-ART.md](https://github.com/tydude001/proofcut/blob/main/docs/PRIOR-ART.md): what else exists in this space, and what
  proofcut does that they don't.
- [NEXT.md](https://github.com/tydude001/proofcut/blob/main/docs/NEXT.md): the directions after the queues closed, ranked.
- [TRIAL.md](https://github.com/tydude001/proofcut/blob/main/docs/TRIAL.md): an agent cutting a video end to end, unattended
  and scored.
- [docs/plans/](https://github.com/tydude001/proofcut/tree/main/docs/plans): the plans, in progress and finished. A step
  that landed says "Shipped" and names its HISTORY.md section.

## License

[PolyForm Shield 1.0.0](https://github.com/tydude001/proofcut/blob/main/LICENSE). proofcut is source-available, not open
source: you can read, run, change and redistribute it for any purpose except
building a product that competes with it. Cutting your own videos, running it
for clients, building on it and forking it to fix a bug are all fine. For a
commercial licence, ask.

The bundled typefaces are not proofcut's to relicense. The caption face in
`src/proofcut/fonts/` and the three browser faces in `src/proofcut/web/` are
OFL-1.1, each with its licence text beside it and its source in that
directory's `FONTS.md`.

## Say thanks

If proofcut cut a video for you, you can [buy me a coffee on Ko-fi](https://ko-fi.com/tydude001).
