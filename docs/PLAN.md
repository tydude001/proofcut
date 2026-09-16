# proofcut — planning

The living plan: architecture, standing decisions, open questions, and the
order of work. Decisions that get made move out of "Open questions" into
"Decisions", with the reasoning that settled them.

The other layers are elsewhere and this file cites rather than restates them:
competitor and dependency research in [PRIOR-ART.md](PRIOR-ART.md); the dated
record of what shipped and what the evidence said in [HISTORY.md](HISTORY.md);
the Daydream parity program in [docs/plans/DAYDREAM.md](plans/DAYDREAM.md); status
in the wiki's Open items table.

## Tier 1 MVP — headless MCP server

One binary/package, `proofcut`, usable two ways:

- `proofcut mcp` — MCP server (stdio) that Claude Code or any MCP client connects to
- `proofcut <subcommand>` — same operations as a plain CLI for scripting/debugging

### Project model

A *project* is a directory: source media, a `project.otio` timeline, a
transcript cache, and rendered outputs. Everything on disk, everything
inspectable, nothing uploaded. The OTIO file is the single source of truth
the MCP tools mutate; renders are derived from it.

The concrete layout is implemented in `src/proofcut/project.py`, whose docstring
is the reference for it. `proofcut.json` carries a `schema_version`; a project
written by a newer proofcut is refused rather than silently misread.

### What lucid is, stated narrowly

Most of the MVP tool surface exists elsewhere already — see
[PRIOR-ART.md](PRIOR-ART.md), particularly auto-editor. Three things were
believed not to, and they were the original reason this project exists.
Everything else is plumbing to make them usable:

1. **Addressable ranges.** Every shipping transcript editor treats speech as a
   global declarative *filter* — "keep every section matching this regex."
   proofcut addresses a specific range: "cut words 30–45", "keep take 2 of that
   sentence, drop take 1." Filter versus edit. This is what an agent needs to
   work iteratively.
2. **Persistent project state.** The competition is source → output per
   invocation. proofcut accumulates an edit across turns, which is what makes
   refinement — and undo — possible.
3. **MCP with a real timeline underneath.** Typed tools with structured returns,
   over OTIO as the native source of truth rather than a one-way export.

Scope discipline follows from this: if a capability is available by shelling out
to an existing tool, shell out. Reimplementation is only justified where one of
the three above requires it.

**The second survey sweep weakened this thesis.** OpenChatCut (see
[PRIOR-ART.md](PRIOR-ART.md)) plausibly covers all three: word-level text-based
cuts, persistent projects with undo, and a real MCP endpoint with a
proposal/review workflow. What it does not cover is the *form factor*: it is an
Electron desktop app whose MCP endpoint requires the GUI process, with a custom
JSON timeline, cuts-only FCPXML as its whole NLE handoff, and no CLI. proofcut's surviving thesis, if it
has one, is the narrower combination **headless + CLI parity + OTIO-native NLE
handoff + thin Python stack** — and whether that justifies the project is
decided by the trial gate in the milestones, not by argument.

**The third pass, 2026-08-07, did not weaken it further.** Daydream is the
most prominent competitor by mindshare, and neither sweep had checked it — it has no GitHub repo, so a
GitHub-shaped search skipped it silently. Checked directly, it clears none of
the narrowed thesis: a closed macOS GUI app fronting a local MCP server (not
headless, no CLI), undocumented per-target XML/FCPXML export with no evidence
of a timeline IR underneath (not OTIO-native), and a product surface —
b-roll search, motion graphics, multi-format export — implying OpenChatCut's
dependency scale rather than a Python package and three subprocesses. It is
also a full desktop NLE that renders in-app, which falsified the README's
claim that it hands finishing work off the way proofcut does. Evidence in
[PRIOR-ART.md](PRIOR-ART.md) § Daydream.

What it cannot do is stand in for a trial. There is no Linux build, so unlike
OpenChatCut it cannot be run on this box at all — the differentiators above are
checked against its docs and pricing page, not against the software.

### How much of the MVP is left once the box is inventoried — 2026-08-07

Measured, not argued. Everything below was verified installed and working on
this machine the same day the fourth trial criterion was added:

| MVP tool | Already covered by | Left for proofcut |
|---|---|---|
| `transcribe`, `get_transcript` | openai-whisper + goodsometimes `scripts/clipcut.py` (in use; it verified the Scream reveals) | packaging. **`transcribe` built 2026-08-07** — `asr.py` already existed for `verify`; the tool was a thin wrapper over it |
| `remove_silences` | auto-editor 31.4.2 | nothing — the plan already said shell out |
| `render` | auto-editor v3 / `melt` | mapping layer, per the render decision |
| `export_otio` | `auto-editor --export kdenlive` lands natively in the only NLE here | **near zero** — this was already "lower urgency than it looks"; the Linux NLE ceiling has now collapsed it. **Superseded by the render spike — see § The handoff clause is not dead, below** |
| `add_captions` | — | word-timed ASS, genuinely absent. **Built 2026-08-07** — see HISTORY.md § Captions came out of the timeline, not the transcript |
| `cut_by_transcript` | — | **the differentiator, and the only one** |

So the surviving thesis is thinner than "headless + CLI parity + OTIO-native
handoff + thin stack." The handoff clause is dead — auto-editor already does it,
better, to Kdenlive. What is actually left is **addressable ranges over an
accumulating edit**, and nothing else.

> **The handoff clause is not dead — corrected by the render spike, 2026-08-07.**
> The row above reasons that auto-editor already exports Kdenlive, so proofcut's
> handoff adds nothing. That is only true of auto-editor's *own* filter-based
> edit. The spike showed the same v3 timeline proofcut builds for `render` also
> takes `--export kdenlive`, so an **arbitrary addressable edit** reaches
> Kdenlive through the mapping layer `render` needed anyway. One mapping, two
> exits, no extra work. The handoff clause of the thesis survives, and it is
> free rather than earned. Evidence in HISTORY.md § First milestones, the render spike.

**And for the workload that prompted this, there may be a cheaper shape than
either.** The essay VO is a *scripted* read: 848 known words. The edit is not
"discover the good take interactively" — it is "align the recording against a
script you already have, pick the best rendition of each sentence, assemble in
script order." That is alignment over word timings whisper already emits, not a
filter (auto-editor) and not interactive addressable editing (OpenChatCut).
Neither tool does it; it is also not obviously a whole project. Size it against
a real VO before assuming it needs proofcut's architecture underneath.

### MVP tool surface

| Tool | Backed by | Notes |
|---|---|---|
| `import_media` | ffprobe | register clips, probe codecs/fps/duration |
| `transcribe` | openai-whisper subprocess | word-level timestamps, cached per clip. Written as faster-whisper; it is openai-whisper shelled out through `asr.py`, because that is the install that exists on this box and ASR is not worth importing torch into every `proofcut status` for. Built 2026-08-07 — `verify` needed the module first, and the tool itself was then a thin wrapper over it |
| `attach_transcript` | cache | ingest a word-timed JSON the recording already has. Not in the original surface; added once the first real subject turned out to have been transcribed before proofcut existed |
| `get_transcript` | cache | agent reads text + timings to plan cuts. Takes a `search` phrase as well as a window — locating a retake in 929 words should not mean reading 929 words |
| `cut_by_transcript` | OTIO, hand-rolled | cut/keep ranges as words or times. OTIO's edit algorithms are C++ only — no Python bindings — so this is track surgery over Track/Clip/Gap and `source_range`, not a library call. Echoes the words each index resolved to and takes `plan=True` to resolve without writing — HISTORY.md § `cut --plan` |
| `restore` | OTIO, hand-rolled | un-cut a specific word range. Not in the original surface, and not the mirror it looks like: `Edit` never stored what it removed, so the removed ranges are *derived* — `Edit.gaps` against the clip's registered duration. Bounded by those gaps, so the timeline stays a subset of the source and the subtractive invariant holds; that is what separates it from the still-parked `vo_extend`. Built 2026-08-08 — HISTORY.md § The head of the parity queue |
| `remove_silences` | auto-editor subprocess | do not reimplement; auto-editor's `--edit` language (`"(or audio:0.03 motion:0.06)"`, labels, `--margin`) is richer than thresholds-as-parameters |
| `add_captions` | ffmpeg + ASS | word-timed, styled via a small preset set; sidecar `.ass` by default, burn-in opt-in. Built 2026-08-07 — HISTORY.md § Captions came out of the timeline, not the transcript |
| `render` | OTIO → auto-editor v3 | a mapping layer, not a renderer — see the render decision below |
| `verify` | openai-whisper + difflib + ffmpeg | not in the original surface. Transcribe the finished render and diff it against the words the timeline should play — the only check that catches a retake the transcript never contained. Added after the dogfood found two of them in a shipped render. Built 2026-08-07 — HISTORY.md § `verify` checks the render, because the transcript cannot. `--windowed` (a second reading in short overlapping windows, `asr.py`) and `loud_gaps` (the energy envelope, `energy.py` — the only check that answers to no transcript) followed the same day — HISTORY.md § `verify --windowed`, and what the Scream exports actually said |
| `export_otio` | OTIO adapters | Lower urgency than it looks: auto-editor already exports six NLE formats via subprocess, and on Linux the ones that actually land are MLT (kdenlive/shotcut) — free Resolve decodes no H.264/AAC, so FCPXML only pays off for pre-transcoded footage. See PRIOR-ART.md |

Deliberately absent from MVP: motion graphics (tier 1.5, Motion Canvas),
b-roll generation, any GUI.

### Stack decision

**Python.** OpenTimelineIO and faster-whisper are both Python, and OTIO is the
load-bearing one — it is the source of truth every tool mutates, and rewriting
or FFI-wrapping it is the cost that would dominate any other choice. The MCP SDK
is solid. ffmpeg and auto-editor are subprocesses either way — auto-editor is
Nim, so it was never a Python dependency to weigh.

This rests on OTIO alone, which is narrower than it looks. Revisit if
performance actually hurts, or if OTIO stops being the source of truth.

**And it now rests on OTIO literally alone.** auto-editor turned out to be Nim
(PRIOR-ART.md), and ASR turned out to be a subprocess too once it was built —
so of the three Python dependencies the paragraph above reasons from, one is
left. The conclusion survives because it was always the load-bearing one, but
"the stack is Python" is no longer a reason for anything.

## Decisions

- **Timeline addressing: both transcript ranges and clip/segment IDs.**
  Transcript-only is simpler, but it has no way to name footage with no
  speech — b-roll, music beds, screen recordings with no narration — and
  those are ordinary material, not edge cases. OTIO already gives every
  segment an identity, so IDs are nearly free now and expensive later:
  retrofitting them means changing the signature of every tool that was
  shaped around transcript ranges. Word ranges stay the ergonomic path for
  dialogue; IDs are the fallback that always works.
- **Python 3.13, not 3.14.** OpenTimelineIO is the sole holdout: 0.18.1 ships
  cp39–cp313 wheels and no cp314. Every other dependency already covers 3.14
  (ctranslate2 4.8.1 through cp314 including free-threaded, onnxruntime 1.28.0
  through cp314, PyAV and tokenizers via abi3 wheels). Pinned in
  `pyproject.toml`; `uv` fetches 3.13. Wheel matrix in
  [PRIOR-ART.md](PRIOR-ART.md). Revisit when OTIO publishes cp314 — and check
  the matrix rather than assuming, since this pin was stale against its own
  revisit condition once already.
- **`render` is a mapping layer, not a renderer.** There is no first-party
  OTIO→ffmpeg renderer in the ecosystem, and nobody has demonstrated one inside
  an agent loop. Rather than write one, map OTIO onto auto-editor's `v3`
  timeline JSON and shell out to `auto-editor timeline.v3 -o out.mp4`. v3 is a
  flattened OTIO track under different field names, so the mapping is small, and
  it buys auto-editor's renderer and its `--preview` dry run for free.
  auto-editor is public domain, so porting its render logic later is
  unencumbered if the mapping leaks. Hand-rolled OTIO→ffmpeg stays the fallback.
  **Verified 2026-08-07** (milestone 3), with one addition the decision did not
  anticipate: the same mapping also exports to Kdenlive, so it is the NLE
  handoff too. Its header fields are templated out of `auto-editor --edit none`
  rather than reconstructed from ffprobe — `layout` and friends are not worth
  guessing, and templating costs a probe instead of an audio analysis.
- **Captions are word-timed ASS.** SRT + ffmpeg `force_style` structurally
  cannot do word-level highlighting, which is the thing burned-in captions are
  actually for. ASS is miserable to generate by hand; generate it from the
  cached word timings instead, which is where they already live. kinocut reached
  the same conclusion independently.
- **Snapshot the timeline on every mutation.** `project.otio` is a single source
  of truth being mutated in place by a non-deterministic agent, so undo is not a
  tier-2 feature. Copy to `cache/history/<n>.otio` before each write. Ten lines
  now, a schema migration later.

## Open questions

- **How does a lucid project know it is the film? Opened 2026-08-10.** The
  Scream project sat at the *silence-cut* stage of an edit whose retake pass had
  been done in Kdenlive — 73 segments and 411s against the shipped film's 63 and
  336s — and every check proofcut has agreed with itself the whole time: the render
  matched the timeline, `verify` had nothing to report, all 38 shots planned. It
  was not broken, it was the wrong cut, and 72s of retakes reached a review.
  Three gaps, in the order they bite: **no repeat-finder in a transcript**
  (`verify --windowed` finds one in a *render*; `goodsometimes/scripts/vo_windows.py
  --repeats` finds one in audio and lives outside proofcut), **no way to bring an
  outside edit in** (63 ranges were parsed from the `.kdenlive` playlist and
  written straight to `Edit`, bypassing `cut`), and **nothing that compares a
  project against what it is meant to be**. HISTORY.md § The VO the project was
  holding.
  - **All three gaps are closed as of 2026-08-13, and closing the second one
    answered the question the other two only asked around.** The repeat-finder
    is `transcript.find_repeats`, the comparison is `film_check`, and the way in
    is `import_edit` (HISTORY.md § The film check, and the repeat that was never
    lucid's to see; § The import that was one frame short, sixty-three times).
    Building the supported import measured the hand-rolled one: the
    `.kdenlive` declares **338.367s** in three places and the shipped project
    holds **336.269s**, because `out` is the last frame *index* and the
    hand-parse read it as exclusive — one frame off the end of each of the 63
    ranges. The film is not being re-imported over it; what changed is that
    nothing had ever compared a document's own declared length against what its
    entries sum to, and now `import_edit` does.
    - **A closed gap is not a fixed project, and the comparison had a blind
      spot of its own.** The film's project went on holding the stale cut for
      five more days, and `film_check` pointed at it compared the `Edit`
      alone while `export` lays down edit + tail — so on the *restored*
      project it read `agrees: false` by exactly the 6s end card, the same
      direction as the 74.7s it was built to catch. Both fixed 2026-08-18.
      HISTORY.md § The film's project, restored.
- **Should the workspace play its own output? Opened 2026-08-17.** `proofcut web`
  rebuilds the picture live from the cue table and never reads `renders/` — the
  Export button writes a file the UI then cannot open. So "let me watch the cut
  we just exported" has no answer inside the workspace; it is `proofcut review
  serve` (built for it, and reachable off the machine) or a video player. The
  split is defensible — a workspace shows the project, a review tool shows the
  artifact — but it is undocumented and surprised Tyler, and the failure mode is
  bad: when the live rebuild refuses, the page draws nothing and looks broken
  rather than saying the export is elsewhere.
  - **The narrow half is answered and built, 2026-08-18; the general half
    stays open deliberately.** Walking the reshape end to end turned this from
    a worry into a demonstration — the pipeline finished, the report said
    *Render complete*, and the page could not open the file, name it, or say
    where it was, because `webui.py` had no route under `renders/` at all.
    What shipped is the one file the flow itself recorded:
    `finish_report`'s new `last_render`, `GET /api/output` streaming it
    through the existing `_stream_file`, and a Watch button in Finish. It is
    **not** a listing of `renders/`, not a path the client may name, and not
    a third caller of `media.preview_path()` — a render is not a preview.
    HISTORY.md § The window plays its own render.
  - **Answered 2026-08-18: it stays split, and this question is closed.**
    Whether the workspace should reach *any* render — an earlier one, a
    reference, an A/B against the file `film_check` compares against — was
    taken on the recommendation: it does not. A workspace shows the project,
    a review tool shows the artifact, and `proofcut review serve` is built for
    the artifact and reachable off the machine, which the workspace
    deliberately is not. The one exception is the file the window itself just
    made, reached through `renderlog.last` rather than through a path the
    client names — and the reason that exception cannot grow quietly is the
    same reason it was drawn narrowly: proofcut writing the log itself is the
    whole argument for not letting one hand-edited line turn a loopback
    server into a file server. Widening it is a new decision, taken here and
    not by an increment.
- **Does OpenChatCut make proofcut redundant? Answered 2026-08-07: no.** The gate
  required all four criteria — runs acceptably on Linux **and** MCP handles
  iterative addressable edits on a real recording **and** Electron-as-MCP-host
  is tolerable in an agent-CLI workflow **and** the output reaches the
  finishing NLE. Criterion 4 failed outright (FCPXML-only handoff) and 2 is
  cloud-locked (AssemblyAI-only ASR); per-criterion evidence in milestone 2.
  proofcut continues with the narrowed thesis above.
- **Fourth trial criterion, added 2026-08-07: does the edit get *out*?** The
  first three criteria were written assuming finishing happened elsewhere. It
  can't. Premiere does not run on Linux and the goodsometimes back catalog's
  last `.prproj` is 2025-10-30 — **the only NLE on this box is Kdenlive, and
  Kdenlive cannot import FCPXML.** OpenChatCut's sole handoff is FCPXML, so it
  is all-or-nothing: either the video is finished inside a v0.1.9 Electron app
  using Remotion, or nothing leaves. For a video essay that needs quote cards,
  chapter titles and lower-thirds, that is a real bet and it belongs in the gate.
  Contrast measured the same day: `auto-editor --export kdenlive` writes a native
  MLT project that Kdenlive opens and `melt` renders. See goodsometimes
  `pipeline.md` § The NLE changed for the verified path.
- **Word-timestamp accuracy.** whisper word timings drift on long recordings.
  Forced alignment (WhisperX-style) is the obvious fix, but probably the wrong
  one: cut points want to land in the *silence between* words, so snapping the
  cut to the nearest audio-energy minimum within a window is cheaper and more
  robust than better ASR. Evidence that alignment alone is insufficient:
  rescript, a shipping transcript editor, added drag-to-adjust word edges.
  Decide after measuring on real footage.

  **First measurement, 2026-08-07 — cheaper than feared on this material.** At
  the six retake boundaries in the Scream VO the silence between the abandoned
  take and the restart ran 0.34s–2.48s, so a flat `pad` of 0.1s put every cut
  well inside the gap, and re-transcribing the render confirmed no word was
  clipped at either edge. That is *not* a general answer: restart pauses are
  the easiest case there is, and a cut mid-sentence has no such margin. Energy
  minimum snapping stays the plan for tight cuts; it is just not urgent.

  **Narrowed once that video was rendered: this covers drift, not infidelity.**
  It sampled the retakes proofcut knew about. Two it did not know about were
  missing from the transcript altogether — whisper had folded them into the
  duration of the following word — and no amount of edge-snapping finds a take
  the transcript never recorded. HISTORY.md § 2.
- **Variable frame rate footage. The probe half is answered 2026-08-24; the
  normalising half is still open.** Phone/screen recordings are often VFR and
  break naive cut math. The lean held: do *not* transcode on import — it is
  slow and lossy, and cut-and-concat operates in the time domain where VFR is
  mostly fine. `media.probe` records `vfr` on the clip's manifest entry,
  `import`'s own return carries it, `assets`/`properties` echo it, and
  `finish_report`'s `sources` names the affected clips — **informational,
  never a flag**, because nothing in the window clears it and a permanent flag
  is a count that can never reach zero.
  The signal (`r_frame_rate` vs `avg_frame_rate`, 1% tolerance) was measured
  rather than trusted: it fires at 42% on a frames-dropped file — a screen
  recorder's own shape — and produced **zero false positives over twelve real
  files on this box**, including the film's 23.976 footage at 1e-6. The
  tightest true-CFR margin measured is 0.33%, so the tolerance clears real
  material by about 3× rather than 100×. Packet-timing deltas agree (a CFR
  file has at most two, one tick apart; the variable one had nine), which is
  the fallback that turned out not to be needed. HISTORY.md § The VFR probe.
  **What is still open is normalising at NLE export**, where frame-exactness
  actually matters and where a recorded `vfr` is what a future step would key
  off; nothing has been built for it, deliberately.
- **Preview delivery in tier 1. Answered 2026-08-24: `shot_sheet`, and this
  question is closed.** The consumer is an agent and an agent cannot watch an
  MP4; what it can do is receive an image in a tool result, so the picture
  track goes back as one labelled grid. Both claims it turned on were measured
  before anything was built (§ The agent contact sheet), and the build found
  the third: every other sheet here returns a *path*, which is unreachable
  under `--tools ""`. HISTORY.md § The shot sheet. Source footage for a project
  with no dialogue to address followed as `footage_sheet` (HISTORY.md § The
  footage sheet), and the other two sheets were retrofitted 2026-08-25 —
  where the finding was that returning the bytes is only half of it, since a
  sheet drawn for a person arrives downscaled past its own labels (HISTORY.md
  § The two sheets an agent could not see). Note the neighbouring
  questions this does *not* answer: an MP4 for the human, and the web preview
  (tier 2, built — HISTORY.md § The preview/timeline web UI), which is for a
  person and never made an agent able to look.
- **Does the OTIO→v3 mapping hold? Answered 2026-08-08: yes, wider than proofcut
  uses.** Single-track cut-and-concat maps in both directions on real material
  (milestones 3–5), and v3 turns out to express the three things this bullet
  used to list as unverified: multi-layer composites (`v`/`a` are lists of
  tracks), speed (`effects: ["speed:2.0"]`) and transitions (a top-level
  `transitions` key). So none of them can force an architecture change at the
  v3 boundary. `timeline.py` still cannot express them — one track, A/V linked,
  no gaps — but that is now proofcut's choice rather than the format's limit, and
  the cost of changing it is measured in HISTORY.md § The multi-track costing spike. What
  v3 has no field for is per-entry gain.
- **Laying clips and graphics over the VO is unmodelled, and that is the next
  real decision.** The Scream video needs five film clips and nine cards on top
  of the trimmed VO. Right now that is Kdenlive's job and proofcut's output is an
  audio bed to build on, which is a defensible split. But it is the difference
  between "proofcut trims your VO" and "proofcut edits your video", and multi-track is
  the whole cost. Decide against the *next* video, not this one — this one has a
  working path.

  **The decision is cheaper now.** `goodsometimes/scripts/assemble_scream.py`
  shipped that video: a cue table of `(source_word_index, asset)` mapped through
  the surviving ranges. It is a worked reference for what proofcut would absorb,
  including the MLT details that cost the most time. HISTORY.md § 3.

  **And it has a concrete test case, whose prerequisite now exists.** Beat 3's
  Billy/Stu line wants VO ducked under a clip's own audio, which breaks the
  assumption underneath the split above — that clips are silent and proofcut owns
  the only audio track. `speech_overlap` is the overlap test that call needed
  either way. What is left is not the model — HISTORY.md § The multi-track costing spike
  prices that, and it is cheap — but getting a multi-*source* timeline out of
  auto-editor at all. Sequence: § Direction and order.

  **Decided 2026-08-08: proofcut edits your video.** This bullet is answered and
  is kept for the reasoning, not as an open question. The multi-source timeline
  does not come out of auto-editor at all — it comes out of `melt`, which has
  no source-count gate and was already this repo's stated multi-track renderer
  (HISTORY.md § 4). Design, build order and what stays blocked:
  § The layered timeline — the gate is decided, and `melt` renders it.

## Non-goals (write them down so they stay dead)

- Cloud anything. No accounts, no metering, no upload.
- Competing with Resolve/Premiere on finishing. Export to them instead.
- A plugin system before there are two users.

## Direction and order

Sequence and rationale — formerly ROADMAP.md, folded in 2026-08-08. Status
lives in the wiki's Open items table; the account of what shipped is
[HISTORY.md](HISTORY.md). **Cite items by name, never by number**: numbers
renumber on every ship, and four things once cited "item 1" meaning four
different items. Anything built gets a named HISTORY.md section; cite that.

**Ordering answers to measured defects, not to competitors** — with one
deliberate exception on the record: Daydream parity, a goal by owner's
decision (Tyler, 2026-08-08) rather than by measurement. What survives of the
rule is the order *inside* the queue — it still ranks by what the layered
timeline unblocks — and the constraints that keep the window honest bind
every parity item.

### The property everything below defends

**Word indices address the source and never renumber.** When two late retakes
shifted every downstream cut by 4.4 s, the whole 37-shot plan recomputed from
two `lucid cut` commands, because no cue was written in timeline seconds
(HISTORY.md § The core thesis held, and it paid off late). The music bed
proved the converse: cues carrying explicit lengths tuned to the old runtime
were invalidated wholesale by a ~12 s append — **the property is about every
cue in a project, not just the shot plan.** Any item below that would trade
it away is wrong regardless of what it buys. The corollaries are conventions
in [CLAUDE.md](../CLAUDE.md): trust word *order*, never word *durations*;
survival is an *overlap* test; anything emitting times for playback maps
through `Edit.timeline_span`.

### Recently — what shipped, and what each one corrected

**What is in flight is the wiki's Open items table, never this file** (root
`CLAUDE.md` § Knowledge stores). What follows is the standing record of what
each shipped item turned out to be, which is a different question.

The tier-3 workspace shipped 2026-08-08 (§ Tier 3 is the goal). Of its two
remainders one is closed — the agent's MCP server binds to its `-C` project
as of 2026-08-09 (HISTORY.md § Binding the agent's MCP server to its
project) — leaving the video preview proxy, whose showing-the-shot half
shipped the same day (HISTORY.md § The preview picture layer), so what is
open there is the transcode. The verified
bar for any UI item: run against the real Scream VO, in a real browser (wiki
`tooling.md` § Headless browser) — and for anything showing *video*, not by
screenshot, for the reason that page now records.

The look/feel pass — the parity queue's head — shipped 2026-08-08 too
(HISTORY.md § The look pass), and the six small items it left behind shipped
the same day: model label, per-turn thumbs, `@`-mentions, inline pause
markers, `restore`, export presets. HISTORY.md § The head of the parity queue.

**Caption styling, the next ranked item, shipped 2026-08-09** — the style is
project state and the captions are derived from it, so a restyle survives
every later edit (HISTORY.md § Caption styling). Two measurements out of it
carry past captions. ASS `\k` is a left-to-right *fill*, not a per-word step,
and the preview was corrected to match the file rather than the other way
round. And **`DejaVu Sans` is not installed on this box** — `fc-match`
answers `Noto Sans`, so every caption proofcut has burned here was drawn in a
substitute, silently, and the preset comment claiming otherwise was wrong.
The substitution is now reported on every call; **whether proofcut's default
should name a font this machine actually has is an open call**, deliberately
not taken, because changing the table would silently restyle every existing
project. It is not only a caption question: librsvg substitutes as silently as
libass and at exit 0, so a card template names a fallback *stack* ending in a
generic rather than a face — routing around the call rather than taking it.

**Motion graphics + templates shipped 2026-08-09 — all three steps, stopping
where its costed note said stop** (§ Motion graphics and templates; HISTORY.md
§ The card renderer, § Card templates). The note's finding was that the item
needed no new timeline mechanism — the Scream cards already are motion graphics
minus the motion — so what shipped is the generator it was missing: a renderer,
`card new` over three templates, and a canvas that defaults to the project's
own. That last closes the note's finding 4 for new cards, and only it could
have: `-size` *fits*, so no resize on the way in would have done it.

What the note costed and refused to build is *animation* — an animated card
carries a length, a shot's length is derived from the edit, and a cue carrying
a length is the failure § The property everything below defends exists to
prevent. It gets its own note, after a watch of a card-heavy cut.

**B-roll by description shipped 2026-08-09, all three build steps**
(§ B-roll by description; HISTORY.md § `describe`, § `describe_ls`, § The
pinned cue). The note inverted the item's own framing and was right to: the
indexing half everyone assumed was the cloud-shaped risk is local and resident,
while the *placement* half docs/plans/DAYDREAM.md counted as already-coming did not
exist — a cue could not name a moment inside its asset, because `mlt.plan_picture`
picks that by a consumption cursor. So footage is described, searched by
reading, and now placed by a cue carrying an in-point that refuses rather than
rewinds.

Four things the build corrected or added, each outliving the step that found it:

- **Windows round up, never to nearest** — round-to-nearest silently *widens*,
  and widening is the one direction that fails.
- **~3.5s a window and ~97 words a description**, against the note's 2.6–3.3s
  and ~60, on two independent runs. The read-them-all ceiling is ~600 windows,
  not ~1000.
- **`src_pin` and `src_start` are separate keys** — the cue's ask against the
  planner's answer. One key meaning both reads as correct in every test that
  has a pin in it.
- **A pin advances the per-asset cursor**, so an unpinned re-use after one
  carries on rather than replaying what was just shown.

The pinned refusal was settled by rendering colour-coded b-roll through melt
and sampling the pixels, because the rewind it prevents produces a file and
exit 0. **What is left is not a build**: the note's step 4 is a watch of a real
b-roll cut, before ranking of any kind.

The one named thing it blocked has since cleared: a **`tiktok-reels` preset**
waited on a real reframe rather than a pillarbox of the 16:9 frame — shipping a
platform's name over a quiet letterbox being the correct-pixels-wrong-video
failure the trap below exists to prevent. The aspect-swap item delivered the
reframe and the preset shipped 2026-08-10 (§ Next, item 5).

### Done — the layered timeline

The gate was decided — **proofcut edits your video** — and all six steps shipped
2026-08-08. Design, evidence, and what stays blocked: § The layered timeline —
the gate is decided, and `melt` renders it; the account of each step, and of
the three constraints the ordering owned, is HISTORY.md.

The outside date is real: the **October Horror Bracket, part 1 due Oct 1**,
format decisions wanted mid-September. The Billy/Stu duck stays blocked by
the recording, not the model — 74–85% overlap, no seam to duck into
(HISTORY.md § `speech_overlap`) — and the layered timeline ships without it.

### Next — the Daydream parity queue

The whole parity plan — observed product, design system, per-feature notes,
non-imports — is [docs/plans/DAYDREAM.md](plans/DAYDREAM.md). Ranking, governed by
the layered timeline being the enabler and the look pass being gated on nothing:

1. **The look/feel pass** — shipped 2026-08-08 (HISTORY.md § The look pass),
   and so are the six small items that were to ride it and didn't
   (HISTORY.md § The head of the parity queue). This rung is done.
2. **Caption styling** — shipped 2026-08-09 (HISTORY.md § Caption styling).
   What it left was per-word *animation*, **costed and then declined on a
   watch, both 2026-08-10** (§ Per-word caption animation — the design note;
   HISTORY.md § The caption animation nobody wanted). The costing overturned
   the item's premise — it is not an export cost and not a per-word Dialogue
   event, it is two style fields over the one event `to_ass` already writes —
   and then all four treatments were rendered on the real film and Tyler
   chose the `\k` fill proofcut already writes. **This rung is done, and proofcut
   diverges from Daydream here by choice** (docs/plans/DAYDREAM.md § What parity
   does not import).
3. **Motion graphics + templates** — shipped 2026-08-09, all three steps
   (§ Motion graphics and templates; HISTORY.md § The card renderer, § Card
   templates). This rung is done.
4. **b-roll by description** — **all three build steps shipped 2026-08-09**
   (§ B-roll by description; HISTORY.md § `describe`, § `describe_ls`, § The
   pinned cue). Footage is indexed, searched, and now *placed*: a cue carries
   an in-point and the shot shows the moment it names or `export` refuses.
   The hour-metering worry resolved against Daydream rather than for it —
   describing locally is minutes, and the part that actually needed building
   was the cue that could name a moment. **The watch that step 4 held for
   happened 2026-08-10, and cost the item its premise**: what chooses a clip
   is not the vision index but a per-clip `synopsis`, and proofcut does not
   choose at all (§ B-roll by description step 5; HISTORY.md § Choosing the
   b-roll). The index's remaining use is the in-point, which nothing pins yet.
5. **The long tail** — aspect swap, import roles + assets pane,
   multi-project picker, HTTP MCP transport, properties pane. **Aspect swap
   is no longer only a parity nicety** — it is what a `tiktok-reels` export
   preset is waiting on, and the preset is the first thing anyone reaching
   for a vertical export will ask for. **Costed 2026-08-09** (§ Aspect swap —
   the design note), and the cost moved: not both render paths but one, since
   an override routes through the MLT writer that already reframes — plus the
   cards, which are the only project state a swap cannot re-derive. **Steps 1
   and 2 shipped 2026-08-10** — the canvas field with its routing, and the
   card record that makes a card re-derivable (HISTORY.md § The canvas field,
   § The card record). Both corrected the note: the bump the first refused
   landed on the second, and the cards the second was meant to rescue turned
   out never to have been proofcut's. **Step 3, the MLT reframe, shipped
   2026-08-10**: a swapped canvas now crops to fill, per-clip and overridable,
   verified against a real melt render (HISTORY.md § The MLT reframe).
   **Step 4, the viewer's frame, shipped 2026-08-10** — the preview is shaped
   like the render and crops where it crops, which closes both the
   disagreement step 3 opened and the older finding 6 (HISTORY.md § The
   viewer's frame). It corrected the note too: the note said "contain" there
   and contain would have drawn black bars the render does not have.
   **`tiktok-reels` shipped 2026-08-10** (HISTORY.md § `tiktok-reels`), and
   the stop-and-watch that was to end this item ran instead of ending it:
   **the centre-crop default is refused, and so is letterboxing the footage
   back in.** What the item was waiting on is per-*shot* framing, costed
   2026-08-10 — § Per-shot framing — the design note, which moved the answer
   off the cue and off a new render node both. **Its steps 1–4 shipped the
   same day** (HISTORY.md § Per-shot framing): a window is
   `(clip_id, src_start, rect)` in source seconds, it renders as keyframes on
   the producer's own clock, the preview places per shot, and
   `reframe_sheet` is how any of it gets reviewed. **What is left is the
   framing itself** — 25 placements on the film, against the 15 hand numbers
   as a control — and then the detector. docs/plans/DAYDREAM.md § Aspect swap
   carries the detector numbers. **The control is ported and rendered, 2026-08-10**
   (HISTORY.md § The framing control): the approved numbers are source-
   addressed project state, they reproduce the approved framing through a real
   `melt` render, and they are a test. **All 25 placements are framed as of
   2026-08-11** — 55 windows over 9 clips, the detector's 39 alongside the
   hand 16 — and **all 39 were reviewed on 2026-08-11, which cleared the
   detector and indicted its coverage**: every window is right for the shot it
   was placed on, and about a third of the film's placed footage is framed by a
   window placed for a *different* one. What is left is the two mechanisms
   behind that, neither of them the placement rule. HISTORY.md § The thirty-nine
   windows, reviewed. **The instrument's own share of it is closed as of
   2026-08-12** — a sheet row is a window shown rather than a placement, so the
   14 windows of 55 no round fraction ever landed in are drawn, and
   `reframe_coverage` now answers the mirror question (`steps`: a boundary the
   picture does not justify) as well as its original one. **Its two findings
   were judged the same day and both were already known** — one a defect the
   vertical never had re-applied, one the 410px follow in the shipped teaser
   that wants the parked keyframed *move*. **Sampling where the subject is
   extreme shipped 2026-08-12** as `reframe-sheet --extremes`: 284px of error
   against the default's 186 on the window a watch had already indicted, never
   a *better* moment than the default draws (the fractions are in its probe
   grid, which the measurement forced), and one new finding on the shipped
   teaser — `s4-reveal`'s window at 11.053 frames the back of someone's head
   and clips Sidney, who is speaking, which a watch decides. HISTORY.md § The
   sheet samples where the subject is. **The last of it, the
   `SCENE_THRESHOLD` re-pin, shipped 2026-08-12** — judged by looking at the
   frames either side of every candidate rather than by agreeing with the hand
   table, which is what showed 0.20 discarding 21 real cuts; the film's stale
   share going 6.3% → 28.0% is the reporting starting rather than a regression,
   and `tests/test_scene_threshold.py` pins the number from both sides. `steps`
   said nothing about it either way (every boundary scored 0.002–0.013 at the
   frame itself). **So this item is closed.** HISTORY.md § The three gaps,
   closed; § The scene threshold, re-pinned.

**What step 6 left is closed, and it was two items rather than one.** The
picture lane is previewed as of 2026-08-09: clicking a shot shows it, from the
position inside its asset `mlt.plan_picture` assigned, so a clip used three
times previews from three different places (HISTORY.md § The preview picture
layer). What that needed was a second element in `#viewer`, not a transcode —
every piece of the Scream footage is already `avc1`/`yuv420p`.

The wiki row's other half, **a playable proxy for footage a browser cannot
decode**, shipped 2026-08-11 — with the containment that makes it safe being
that `media_path()` has no branch for a proxy, so `export` cannot reach one.
HISTORY.md § The preview proxy.

**What follows the parity queue is § The completion queue — what the Scream
video left — 2026-08-12**, drawn from a full review of the production once the
essay and teaser were declared done. Its status rows live in the wiki, as ever.

### Parked — deliberately, with the reasoning

- **`vo_extend`, the mirror of `cut_by_time`** — **no longer parked: it is
  queued, and it has a design note.** § `vo_extend` — the design note —
  2026-08-13 is where its shape is settled and § The completion queue is
  where its order is; what is recorded *here* is only why it sat parked for
  so long. It is the one item touching `Edit`'s subtractive invariant, it is
  the same operation as inserting a hold to unblock Billy/Stu without a
  re-record, and the case for it is editorial — decide on a watch.
  Constraints it inherits: HISTORY.md § `cut_by_time`, at *the `vo_extend`
  mirror*.
  - **Its subject is material the source never had, and that is narrower than
    "runtime after the last word."** Tail time for an end card is not this
    item: append real silence to the recording, raise the clip's registered
    duration, and `restore` walks it onto the timeline already bounded by
    `Edit.gaps` — the invariant never bends. What that costs instead is a cue
    the *cue table cannot express*, because cues resolve through the
    transcript and silence has no word. HISTORY.md § The end card.
    - **The teaser is the harder case and the one that has already cost
      something.** It ends on live VO rather than a sign-off, so there is no
      silence to append to and the tail must be manufactured as well as
      addressed. And because the bumper goes on downstream of `export`, every
      re-derivation drops it at exit 0 with nothing reporting the loss — which
      is what makes this a shipping cost rather than a modelling one.
      HISTORY.md § The bumper the teaser never had.
- **Energy-snapping cut edges** — measured non-urgent on Scream-like
  material; the failures that looked like drift were transcript infidelity,
  addressed instead by the near-duplicate and suspect-duration checks
  (HISTORY.md § 2, revising the Word-timestamp accuracy finding). Revisit if
  a video demands mid-sentence cuts.
- **Everything one video couldn't establish** — one speaker, audio-only, no
  camera footage, no VFR, no speed changes (HISTORY.md § What this does not
  establish). The October bracket is where several get their first real test;
  expect this section to reshuffle then.
  - **The first of them is costed, 2026-08-18: § The co-hosted recording —
    the design note.** It moved the item's premise twice. The scale spike's
    two-stream framing is not what this event records — both prior runs
    published one mixed track and OBS on this box was `RecTracks=1` when the
    note was written (it has been `RecTracks=3` since 2026-08-18 — § The
    format decision, which is the deliverable to the event) — and a two-mic
    recording turns out **not** to need the new `Edit` primitive the spike
    named, because the mics are one performance cut together and the
    only per-word fact is a label. What it does need is a decision taken
    before part 1 is recorded, which is why the note exists in August.

**What separates tier 2 from tier 3 is finishing, not mutation** — the
definition stands, and proofcut crossed it 2026-08-08: Export renders a
watermark-free MP4 in the window, with the render checks reported on the
completion card. Non-goals stay dead in § Non-goals.

## Tier 3 is the goal — the Daydream-shaped workspace — 2026-08-08

**The decision: proofcut grows a workspace, not a shell.** HISTORY.md § The preview/timeline
web UI shipped and was used, and the verdict on use was that it is a correct
*instrument* and a poor *editor*. the roadmap had already left tier 3
"revisitable — a question behind the web UI"; this is the answer, taken
deliberately rather than arrived at by drift.

What reopened it was not the handoff argument the closing note anticipated. It
was simpler: the page is unpleasant to work in, and every reason it is
unpleasant is a reason inside the window.

### What using it actually exposed

Measured against the real Scream VO at 67 segments, not against a mock:

* **The picture is not the centre.** `#viewer` is capped at 34vh above two
  strips, and on an audio-only clip it is `display:none` outright — so the
  largest thing on screen is a transcript and the smallest is the thing being
  edited.
* **The transcript is one 929-word paragraph.** No breaks, no timestamps, no
  scroll to the playing word. It is addressable and unreadable at once.
* **67 segments in a 34px strip is a barcode.** No ruler, no zoom, no track
  header, no clip name, no waveform. The strip proves the edit exists; it does
  not let you work on it.
* **The inspector is empty almost always.** "Nothing selected · Nothing run
  yet" is the resting state of 380px of a 1440px window.
* **There is no agent in the window at all** — which is the category
  difference, not a craft one. proofcut's agent lives in another application.

### The line that moves, and the one that does not

§ Direction and order's line — "what separates tier 2 from tier 3 is finishing, not mutation" —
stays true and stays the definition. **What changes is that proofcut now intends
to cross it**, in this order: the workspace first, finishing second. A window
good enough to edit in is worth building before the render path can finish a
video inside it, because the window is what makes the render path's gaps
visible.

**Non-goals do not move.** No cloud, no accounts, no metering, no upload. The
agent panel below is what makes that non-trivial to keep, and it is why the
panel is shaped the way it is.

### The agent panel, and why it does not become a fourth implementation

CLAUDE.md's convention — *the web UI draws and it plays, it never decides* —
survives this intact, and it constrains the design rather than yielding to it.

The panel hosts a **local `claude` subprocess** (2.1.226 on this box) run as
`claude -p --input-format stream-json --output-format stream-json
--mcp-config`, with proofcut's own `proofcut mcp` attached. So:

* the agent reaches the timeline **only** through the MCP tools, which are the
  same `ops` functions the CLI and the page's own buttons call. There is no
  privileged path, and the panel adds no new one — it adds a *client of the
  existing one*;
* it rides Claude Code's existing auth. No API key, no key storage, no request
  leaving for an endpoint proofcut chose. § Non-goals holds;
* `stream-json` is already the progress list the panel needs to draw. Daydream
  renders "Reading transcript → Editing transcript → Done!"; that is a tool-use
  stream with a stylesheet on it.

The page still never computes an edit. It now hosts something that asks for
one, and draws the answer — which is the same relationship it already has to
its own Preview button.

### The trap this section exists to write down

**Do not draw tracks the export cannot produce.** The UI can grow a V2 lane and
a ducked A2 in an afternoon; `export` cannot follow it. HISTORY.md § The multi-track
costing spike measured the wall: auto-editor 31.x gates multi-*source*
timelines behind a paid key and **degrades the render to 720x576 with a warning
and exit 0** rather than failing. A workspace that draws a b-roll lane over
that produces a beautiful window and a silently wrong file.

So the timeline is built as a real NLE timeline **over the single-track `Edit`
that exists** — ruler, zoom, lanes, named clip blocks, waveform — and widens
when the September decision gate widens the model, not before. Lanes drawn
today are *projections of one track*, and the code says so where it draws
them.

### Not a desktop app, and the reasoning is on file

Tier 3 named "a full desktop editor". What is being built is the editor, not
the packaging, and those were never the same decision.

Everything that makes Daydream feel like Daydream is inside the window. The
native shell buys a title bar and costs a second stack — which § Non-goals
already refuses — and this repo has *measured* the specific friction: the
OpenChatCut trial (HISTORY.md § First milestones) found an Electron GUI that must be
running before its MCP tools register, a confirmation card per tool per
session, and a transport that goes stale after an import. Adopting that shape
to gain chrome would be paying the trial's own findings forward.

If the finished page still reads as a browser tab, wrapping *that same page* in
a Chrome `--app` window or Tauri is a day's work against a week's. The shell is
deferred because it is cheap and reversible, not because it is unwanted.

**And the asymmetry that makes any of this worth doing: Daydream has no Linux
build.** PRIOR-ART.md § Daydream — no Windows or Linux build on the download
page, docs or FAQ. On this box the competitor cannot run at all.

### The design — panes, endpoints, and what each one is not allowed to do

Written before building, because three of the decisions below are expensive to
reverse once the page exists.

#### Layout

One CSS grid, three columns over a full-width timeline. No framework, no build
step — that constraint is inherited from § Non-goals and is not revisited.

```
┌──────────────────────────────────────────────────────────┐
│ proofcut / <project>                 [Export] [Undo (n)] │  top bar
├────────────┬─────────────────────────┬───────────────────┤
│ transcript │        preview          │      agent        │
│  ~26rem    │        flex: 1          │      ~24rem       │
│            │   transport under it    │  feed + composer  │
├────────────┴─────────────────────────┴───────────────────┤
│ ruler · zoom                                             │  timeline
│ V1 ▓▓▓▓│▓▓▓▓▓▓│▓▓▓▓▓▓▓▓                                  │  ≥22vh
│ A1 ╫╫╫╫│╫╫╫╫╫╫│╫╫╫╫╫╫╫╫                                  │
│ CC ──────────────────────────                            │
└──────────────────────────────────────────────────────────┘
```

The one thing this fixes that is not cosmetic: **the picture becomes the
largest element on screen**, and an audio-only clip gets a drawn level display
rather than `display:none`. The current page's worst behaviour is that on the
only real project in the repo it renders no viewer at all.

#### Files, and why they split

`app.js` is 744 lines today and this roughly triples it. ES modules, served
flat out of `/static/` — which the existing static handler already permits
(flat names, `.js` allowed) and which the existing CSP (`default-src 'self'`)
already satisfies. No bundler is introduced.

| module | holds |
|---|---|
| `app.js` | entry, the one `view` state, wiring |
| `api.js` | fetch + SSE, the JSON content-type header |
| `transcript.js` | the document pane and selection |
| `timeline.js` | ruler, lanes, clip blocks, waveform canvas, zoom |
| `player.js` | the seam-jumping playback loop, transport |
| `agent.js` | the feed, the composer, the tool-progress list |
| `dom.js` | `el`/`$`, time formatting |

`player.js` inherits the existing seam loop unchanged. It is the one piece of
the current page that is genuinely good and it is not being rewritten for
tidiness — it is the thing that makes seeing an edit cost no render.

#### Read-model additions

Two, and both are ops rather than server-side computation, for the reason
`timeline_view` already is one: a view that computed its own answers would be
a second implementation.

**`paragraph`, a new field on each word in `timeline_view`.** The transcript is
one 929-word block today because nothing tells the page where to break. The
rule is deliberately **word-order-driven, not duration-driven**: break after a
sentence-ending word once the paragraph holds ≥40 words. A silence of ≥0.75 s
after a sentence end may break earlier, once the paragraph holds ≥15 words —
but that arm is opportunistic and the word count is the guarantee. This is the
CLAUDE.md rule about durations applied to a cosmetic feature: whisper inflates
a duration to swallow a retake, which can only ever *suppress* a gap break,
never invent one. A suppressed break is an ugly paragraph; an invented one
would be a lie about where a sentence ended.

**`ops.waveform(path, clip_id)` — CLI `proofcut waveform`, no MCP tool.** RMS per
20 ms frame from `energy.decode` + `energy.envelope`, normalised to bytes,
cached under a new `cache/waveform/` keyed by the media's size and mtime. The
cost is why it is cached and not computed per request: `envelope` is a Python
loop, and 385 s at 8 kHz is ~3 M multiply-adds — roughly a second here, and
linear, so an hour of footage is ~15 s. It is deliberately **not** an MCP
tool: the convention binds MCP tools to have CLI subcommands, not the reverse,
and 19 000 floats is a picture, not something an agent should reason over —
`loud_gaps` and `unaccounted_sound` already answer the numeric questions.

The waveform is drawn **through the edit**: each timeline segment maps to a
source range, and the lane draws that slice of the source envelope. So no
timeline-space envelope is ever computed, and a cut needs no recompute.

#### The timeline

* **Zoom is pixels-per-second**, one number, fit-to-window by default. The
  current strip has no zoom, which is why 67 segments render as a barcode.
* **Track headers are a sticky left column**; lanes scroll horizontally
  together in one container.
* **Clip blocks are DOM, the waveform is `<canvas>`.** Blocks need hover, title
  and hit-testing and there are tens of them; the envelope is thousands of
  points and needs none of that. 67 blocks does not warrant virtualisation —
  the threshold to revisit is ~2000.
* **Lanes are projections of one `Edit`.** V1 only when the clip
  `has_video`, A1 always, CC only when captions exist. **No lane that `export`
  cannot produce** — § The trap this section exists to write down, above.
  Written when that also ruled out V2; V2 became producible at step 5 and was
  drawn at step 6, which is the rule working rather than an exception to it.
  There is still no A2.

#### The agent panel, in mechanism

```
POST /api/agent      {prompt, model: optional} → 202, work happens on the stream
POST /api/agent/stop                            → interrupt the running turn
GET  /api/events                                → SSE: agent deltas, tool calls, project-changed
```

One subprocess per server, spawned lazily:

Flags below verified against the installed `claude` 2.1.226 rather than
recalled — `--allowedTools`, `--disallowedTools` and `--strict-mcp-config` all
exist, and `--permission-mode` takes `manual` among others. There is **no
`--cwd` flag**; the working directory is set on the spawn.

```
claude -p --verbose --input-format stream-json --output-format stream-json
       --mcp-config <generated: one server, proofcut -C <project> mcp>
       --strict-mcp-config
       --tools ''
       --allowedTools 'mcp__proofcut__*'
       --disallowedTools Bash Write Edit WebFetch WebSearch
       --permission-mode manual
       [--model <model>]  # only when the composer's #agent-model-select asked for one
# cwd=<project root>, set on the Popen, not by a flag
```

`--model` bakes in at spawn — no live hot-swap — so a request naming a
different model than the live subprocess's own kills and respawns it, the
same suppressed-exit mechanics as "New Task". HISTORY.md § The agent panel
got a model selector.

`--verbose` is not optional either, and not for logging: 2.1.226 refuses
`--print --output-format=stream-json` without it — errors and **exits 0**
with nothing on stdout, which the SSE-consuming page has no way to see as
failure (a submitted prompt just sits "busy" forever). Verified by direct
reproduction, not recalled.

**The tool allowlist is the security boundary, and it is the whole design —
but `--allowedTools`/`--disallowedTools`/`--permission-mode manual` alone do
not enforce it against built-in tools.** Verified against 2.1.226: those three
flags govern *permission prompts*; a built-in tool named in neither list (the
reproduction used `Glob`) simply never triggers one and runs. `--tools ''`
is what actually disables the built-in set, leaving only the MCP tools
`--strict-mcp-config` exposes — that is the flag that makes "the agent gets
proofcut's MCP tools and nothing else" true, not the allow/disallow lists on
their own. Today a bypass of the `Host`/content-type guards costs you a
mangled edit that `Undo` reverses. An agent panel without this bound would
make the same bypass cost arbitrary code execution as the user, because
Claude Code has Bash. So the agent gets proofcut's MCP tools **and nothing
else**, which bounds a fully hijacked agent to operations the undo stack
already reverses. `--permission-mode manual` remains the belt to the
allowlist's braces for the MCP tools themselves: there is no TTY on a
subprocess, so an MCP tool call outside `--allowedTools` cannot be approved
and fails closed rather than running.

**This was chosen against the two looser options, not defaulted into**
(2026-08-08). Read-only project file access via `--add-dir` was rejected
because it widens what an injected transcript can pull into a tool result for
no capability proofcut's own tools do not already expose; Bash was rejected
because it converts a guard bypass into arbitrary code execution, which is the
single thing the allowlist exists to prevent. If a future need argues for
widening this, it is a decision that gets written here, not a flag someone
adds to make a debugging session easier.

**`--strict-mcp-config` is not optional, and it is the trap worth writing
down.** Without it the spawned agent inherits *the user's own* MCP servers —
on this box that is Gmail, Google Drive and Calendar. A video editor's agent
panel silently holding a mail client is exactly the kind of privilege nobody
audits later. The flag confines it to the one generated config.

The subprocess runs with the project as its working directory and its MCP
server is spawned as `proofcut -C <project> mcp` (`-C` is a global flag and
must precede the subcommand — `proofcut mcp -C` does not parse), **and as of
2026-08-09 that binding is real.** It was not for a day: `_cmd_mcp` ignored
`-C` entirely while every tool took its own explicit `path`, so confinement
to proofcut's ops held and confinement to *this project's* ops did not.
`serve(root=)` now pins the server, and each tool's `path` resolves against
that root or is refused. The account, and the boundary the fix deliberately
stops at — `path` is confined because it is the project *selector*, while
`import_media`'s `source` and `export`'s `output` are not — is HISTORY.md
§ Binding the agent's MCP server to its project.

**Prompt injection is in scope and is bounded the same way.** The transcript is
attacker-influenced content whenever the footage is not yours, and the agent
reads it. An injected instruction cannot reach outside the allowlist; that is
the property the allowlist exists to buy, and it is the reason not to relax it
for convenience later.

**View invalidation is uniform.** The server tracks a revision — `project.otio`
mtime plus undo depth — and the SSE stream emits `project-changed` when it
moves. The page reloads `/api/view` on that event, which covers an agent edit,
the page's own edit, and a `proofcut cut` run in a terminal beside it, without
three code paths.

#### Where the cut controls go

The right pane is the agent now, so the inspector cannot stay there. Selecting
words raises a **floating toolbar anchored to the selection** — Preview, Cut,
Keep only, and a small popover for pad and the suspect-boundary confirmation.

The op's *result* renders into the agent feed as a system entry. That is the
substantive choice: **one feed for everything that happened to the edit**,
whether a person or the agent caused it, in order. The alternative — a separate
result panel — reproduces the current page's emptiest region and splits the
history of an editing session across two places.

The echo rules do not move. A word range still shows the three words either
side (CLAUDE.md), and Preview is still `plan=True` on the same op rather than
its own endpoint.

#### Finishing — the render happens in the window

**Decided 2026-08-08: Export renders a watermark-free MP4 in the window**, and
the MLT/Kdenlive handoff stays as an additional way out rather than the only
one. This is the sentence that actually crosses the tier line. § Direction and order's
"what separates tier 2 from tier 3 is finishing, not mutation" has been the
definition since the tiers were written; this is proofcut choosing to cross it,
with the definition left standing so the crossing stays legible.

`ops.export` already does both — `export_format=None` renders, `"kdenlive"`
writes MLT — so no new render path is written. What is new is that a render is
long enough that a blocking HTTP request is the wrong shape:

```
POST /api/render   {preset}   → 202 {job_id}
GET  /api/events              → job progress and completion on the same stream
POST /api/render/stop         → cancel; the partial output is deleted, not left
```

One job at a time per server, output under `renders/`. Three things the job
model has to get right, all of them already-measured traps rather than
guesses:

* **auto-editor's exit code does not mean success** (HISTORY.md § The multi-track costing
  spike). The job reads the output's actual dimensions before reporting
  success, and says what it got rather than that it finished.
* **A finished render is checkable, and the checks exist.** `verify`,
  `check_frames`, `check_black` and `spot_frames` already answer whether the
  render says what the timeline says. The window is the first place those have
  somewhere useful to appear — the completion card is where they belong, not a
  separate command a person has to remember.
* **The proxy must not be what gets rendered.** The preview proxy below and
  the attenuated copy both resolve through `media.media_path()`; export reads
  through the same function. That ordering is load-bearing and gets a test,
  because the failure is a finished, delivered file at preview quality.

#### What this design still does not answer

* **Multi-project.** *Answered 2026-08-17 — built, and still one project per
  process.* `proofcut web --root DIR` serves a picker over `webui.scan_projects`;
  `POST /api/open` is a one-way bind, the first project picked becomes the
  process's project for the rest of its life, so two projects at once still
  means two processes exactly as `-C` always required. HISTORY.md § The
  multi-project picker, built; docs/plans/DAYDREAM.md § Multi-project.
* **Video, and the codec wall under it.** *Answered in part, 2026-08-09 — the
  picture is verified against real footage now (HISTORY.md § The preview
  picture layer), and the wall turned out not to stand in front of this
  project.* All ten Scream clips are H.264 High / `avc1` / `yuv420p`, so the
  browser plays them from `/api/asset/` byte-for-byte and the letterbox is
  `object-fit: contain`. What survives of this bullet is everything below it:
  the wall is real for *other* footage, and proofcut now names which wall it hit
  rather than showing black.

  The wall found while planning, and it is a *container and profile* problem
  rather than a Linux one. ffprobe on a representative NAS source reports
  `codec_name=hevc`, `codec_tag_string=hev1`, `profile=Main 10`,
  `pix_fmt=yuv420p10le` — which is precisely the combination wiki `home.md`
  already records as un-playable in a browser (H.264, or H.265 tagged `hvc1`;
  `hev1` does not play). That rule was written for the Vault browser and holds
  here for the same reason.

  So `/api/media/` streaming the source byte-for-byte — what makes the current
  page's "no render" claim true for audio — **does not carry over to picture**.
  The preview would show nothing and blame the file.

  This does not break the design; it adds a step the design must not skip. The
  fix is a cached **proxy transcode** into a new `cache/proxy/`, resolved the
  way `media.media_path()` already prefers an attenuated copy. Note before
  building it that homebase already runs an encoder service for exactly this
  conversion (wiki `homebase.md`, port 8765) — worth checking whether proofcut
  should call it rather than grow its own ffmpeg path. Two consequences to
  keep honest either way: seeing a *video* edit costs one proxy pass, not
  zero, and the proxy must never reach `export`, which reads through
  `media_path()` — so the resolution order is load-bearing and gets a test.

## The layered timeline — the gate is decided, and `melt` renders it — 2026-08-08

The decision gate (§ Direction and order) asked whether proofcut trims your VO or
edits your video. **It edits your video.** This section is the decision, the
measurement that forced it, and the build order — written to be picked up cold
in a later session.

### The gate was mis-framed, and the correction is one measurement

HISTORY.md § The multi-track costing spike priced the model as cheap and the export as the
wall: auto-editor 31.x gates any timeline naming two distinct `src` files down
to 720x576 with **exit 0**. Every number in that section is correct and stands.

What it did not do is connect back to HISTORY.md § 4, which had
already concluded — from the video that shipped — that **rendering a multi-track
project needs `melt`, not auto-editor**. So the gate's "four options, none free"
framing quietly assumed auto-editor was the only renderer, when this repo's own
dogfood notes had already said it was the wrong one.

Measured today, closing that loop, against the real assembly rather than a
fixture:

```sh
melt "…/Every Scream Sequel Falls Apart At The REVEAL - assembly v3.kdenlive" \
     in=0 out=90 -consumer avformat:spike.mp4 vcodec=libx264 acodec=aac
```

| | result |
|---|---|
| distinct source files in that project | **23** — `VO.wav`, 9 film clips, 13 cards |
| structure | 4 tractors, 5 playlists |
| render | **1920x1080**, 91 frames, h264 + aac stereo |
| licence gate encountered | **none** |

`melt` has no source-count gate, is already installed (inside the Kdenlive
flatpak, `filesystems=host` already granted), and is already resolved by
`picture.melt_command()`. Two of the spike's own choices were the documented
traps being obeyed rather than luck: it passed `WAYLAND_DISPLAY`, and it passed
**the codec and nothing else** on the consumer. Both are HISTORY.md § 4; do not
re-derive them, and read `goodsometimes/scripts/render.py` before writing the
render call — it handles all three traps plus a `systemd-run` memory cap.

**So the wall is auto-editor's, not this box's.** Options 1 (buy a key) and 2
(fork the Nim source, re-patch every release, and assert un-gatedness forever by
probing render *resolution* because the exit code lies) both existed to buy back
something `melt` does for free. They are dropped.

### What this does to "lucid never writes MLT"

That rule (HISTORY.md § `cut_by_time`, at *the `vo_extend` mirror*) is narrower than its
summary. It says proofcut **regenerates** a timeline through `auto-editor --export
kdenlive` rather than **mutating** MLT in place, so it never owns MLT's two
sharp edges: `<blank>` silently adding runtime every downstream cue is blind to,
and four declared-length spots (both tractors' `out`, the sequence track's
`out`, `producer0`'s length) that must be swept in step.

The rule pays for itself only while auto-editor writes the XML. On the
multi-source path it writes nothing — the kdenlive exporter **refuses, exit 2**.
So the XML is not being re-adopted from something that would otherwise produce
it; it is being written because nothing else will.

**The rule is therefore narrowed, not abandoned:**

- **Single-source `export` is unchanged.** It still shells out to auto-editor,
  render and `--export kdenlive` both. Nothing about today's behaviour moves.
- **The multi-source path generates MLT from the `Edit` plus the cue table**,
  every time, from scratch. It **still never mutates** an existing project — the
  distinction the original rule actually cared about survives intact.
- Generating means proofcut now owns both sharp edges above. They are already
  written down in this file precisely so this day would not be a surprise, and
  the declared-length sweep gets an assertion rather than a comment.

Built as `src/proofcut/mlt.py` (step 4, below): the sweep is `declared_frames()`,
read back off the finished document rather than tracked while building it.

### The design: Design B, unchanged

HISTORY.md § The multi-track costing spike costed two designs, recommended B, and
validated it against the real 37-cue table. Nothing found since argues against
it; that section keeps the numbers.

`Edit` stays **single-track and subtractive**. The picture is a *derived
projection* recomputed on every build, so there is nothing positioned to go
stale, and § The property everything below defends is preserved by
construction rather than by care. Untouched: all five addressing methods
(`timeline_time`, `timeline_span`, `timeline_spans`, `source_at`,
`source_spans`), `remove`, `keep_only`, `from_otio`, `to_otio`, captions,
verify, cut, locate.

Design A — widening `Edit` to N positioned tracks — stays rejected. Rippling the
VO would invalidate every stored position on the picture track, which is the
property traded away for the thing it was defending against.

### Build order

Each step is shippable and verifiable on its own. Parity is not optional:
every op gets an MCP tool **and** a `proofcut` subcommand (CLAUDE.md § Conventions).

1. **The cue table.** `(clip_id, word_index, asset)` in the manifest,
   source-addressed, nothing in timeline coordinates. Ops `cue_add`, `cue_rm`,
   `cue_ls`; CLI `proofcut cue add|rm|ls`; MCP to match. Bump `schema_version` and
   keep the reader tolerant of manifests without the key. **Built
   2026-08-08** — HISTORY.md § The cue table, step 1 of the layered timeline.
2. **The shot projection.** `build_shots` minus all XML — map each cue's word
   through the surviving ranges, each shot running to the next cue. ~150–200
   lines. `assemble_scream.py` is the worked reference; take its arithmetic,
   not its structure. **Built 2026-08-08** — HISTORY.md § The shot
   projection, step 2 of the layered timeline. Also resolves `asset` to a
   checked path (`card:name` under a new `assets/cards/`, else a registered
   video clip_id), per the division `cue_add`'s own docstring already
   committed to at step 1.
3. **Refuse to build when a cue lands in a cut range.** This fired correctly
   twice on Scream, both times catching a stale cue after a recut. It is the
   safety property of the whole feature and it is not optional. Test it
   first. **Shipped inside step 2, not after it** — the frame arithmetic has
   nothing to return for a cut word, so the refusal could not be deferred.
   What step 2 got wrong on the first pass and step 3's own tests exist to
   guard: it used `Edit.timeline_time` (containment of the word's *start*
   instant) rather than `Edit.timeline_span` (overlap across the whole
   word), and disagreed with `assemble_scream.py`'s own arithmetic on the
   real Scream VO at word 115 — a swallowed false start whose survival
   depends on its tail, not its start. Fixed before shipping; HISTORY.md
   § The shot projection has the numbers.
4. **The MLT writer**, multi-source path only. Owns the `<blank>` and
   declared-length constraints named above. Every emitted length asserted
   against the `Edit`'s own frame total from `autoeditor.frame_layout` — never
   from a duration (CLAUDE.md). **Built 2026-08-08** — HISTORY.md § The MLT
   writer, step 4 of the layered timeline. It also took the per-clip playback
   cursor step 2 deferred to it, and made `export` refuse to *render* a
   multi-source timeline rather than let auto-editor degrade it — the refusal
   step 5 then replaced with the render itself.
5. **Render through `melt`**, HISTORY.md § 4's three traps handled, exit code
   trusted for nothing. Assert the output's **resolution and frame count**, not
   its status. The measurements to beat are already on file: the step-4 spike
   rendered its own document at 1920x1080 and exactly the declared frame
   count, by hand. **Built 2026-08-08** — HISTORY.md § Rendering through
   `melt`, step 5 of the layered timeline. It also found the fourth trap the
   first three imply and none of them states: `WAYLAND_DISPLAY` without
   `XDG_RUNTIME_DIR` is not a display, and that is the pair a scrubbed
   environment — the MCP stdio transport's — hands the renderer.
6. **The picture lane in the web UI.** This becomes legal for the first time
   here and not before: the timeline may not draw a lane `export` cannot
   produce, so V2 lands in the same change that makes `export` able to produce
   it — never earlier. CLAUDE.md § Conventions, and § Tier 3 is the goal.
   **Built 2026-08-08** — HISTORY.md § The picture lane. What the rule turned
   out to demand: **not a lane drawn from `build_shots`.** The projection
   accepts a shot longer than its asset and the MLT writer refuses it, so the
   lane draws `timeline_view`'s `shots` — the projection already through
   `mlt.plan_picture` — and a refusal from either arrives as a message rather
   than an exception.

Seed the cue table from `assemble_scream.py`'s existing 37 cues, so the first
layered timeline proofcut builds is **this video**, checkable against a file that
has already been watched — rather than an empty project that can only be
checked against itself. **Done at step 5, and it moved which project that
means.** The 37 cues address `VO/VO2-windowed.json` — the re-recorded VO,
re-found by phrase — while `Project/lucid-vo` holds the *v1* VO and a
929-word transcript, so pasting the table onto that project would have put
every cue on the wrong word. The seeded project is a new one on VO2.wav plus
the windowed transcript, where all 37 land and the render is real:
HISTORY.md § Rendering through `melt` has the numbers, and § The shot
projection and § The MLT writer have the two before it.

### What stays blocked, and it is not lucid

**The Billy/Stu duck.** It was rejected on measurement, not taste: the clip's
line sits at 105.35–109.15 s against the VO's own thesis sentence at
105.97–109.85 s, and `speech_overlap` re-measured 74–85% overlap with only
sub-second clean seams (HISTORY.md § `speech_overlap`). There is no seam to duck into. That
is a property of **the v1 recording**, and no amount of multi-track fixes it.

Two ways out, and the choice is editorial rather than technical:

- **The re-record**, which was always the plan and opens a real pause.
- **Insert a hold** — open a gap in the VO and let the film's line play in it.
  proofcut cannot do this today: `Edit` only ever removes, so a non-subtractive
  operation is new work, and it is the same work as HISTORY.md § The `vo_extend` mirror is
  a deliberate non-goal, for now. That section's stated reason for parking —
  that it only matters the day proofcut owns MLT generation — **expires with this
  decision.** Re-cost it against a watch, not in the abstract, and note it is
  the one item here that touches `Edit`'s subtractive invariant.

Neither blocks steps 1–6. The layered timeline ships without the duck.

## The Daydream parity map — copy the features and the look — 2026-08-08

The parity direction (Tyler, 2026-08-08: lucid copies Daydream's full feature
set and look/feel), the observed product, the design-system spec, the
feature-by-feature map against shipped code, and the build order are one
document: [docs/plans/DAYDREAM.md](plans/DAYDREAM.md). The constraints that bind
every parity item are proofcut's own and live where they always did: § Non-goals, the
web-UI conventions (no lane `export` cannot produce — CLAUDE.md), and § The
property everything below defends.

## Motion graphics and templates — the design note — 2026-08-09

The costed note docs/plans/DAYDREAM.md § Motion graphics + templates and § Next ask for
before any build. **The finding that shapes it: motion graphics need no new
timeline mechanism at all.** The Scream assembly's 13 cards already are motion
graphics minus the motion — PNGs in `assets/cards/`, cued by `(clip_id,
word_index, asset)`, resolved by `ops._resolve_asset`, projected by
`build_shots`, held by `mlt.plan_picture` as an `is_image` `Entry`, composited
by melt as a `qimage` producer. What is missing is a *generator* for the
asset, and one decision about animation that turns out to be about § The
property everything below defends rather than about rendering.

### Measured on this box, 2026-08-09

Five measurements, because a design note that guesses at the toolchain is how
the caption default came to name a font this machine does not have.

1. **There is a real SVG rasteriser here, and the obvious probes miss it** —
   `magick` links librsvg, so `magick in.svg out.png` renders text faithfully.
   Which binaries are absent, which delegate row to look for, and the numbers
   behind "faithfully" are a box fact, not a proofcut one: wiki `tooling.md`
   § Rasterising SVG. **What it means for this note is only that the generator
   has a renderer to shell out to and needs no new dependency.**

2. **That renderer substitutes a missing font silently — the caption trap, on a
   second renderer.** Same card, one naming an installed font and one naming a
   missing one: pixel-identical output, both exit 0 (measurement in the wiki
   section above). The consequence here is that `captions.font_match` is
   already the right answer and gets reused rather than reinvented — report the
   substitution, do not prevent it. Without that, cards join captions in being
   drawn in a typeface nobody picked.

3. **The production render preserves a card's colour, and the alarm that says
   otherwise is a testing artifact.** A one-frame `melt … -consumer avformat`
   probe with default args lifted blacks — `(16,20,24)` → `(30,34,37)`, the
   signature of a limited/full-range mismatch — and that is the probe's fault,
   not melt's. Against the real render: `receipt-scream-1996.png`'s paper is
   `(250,245,236)` in the source and `(250,243,236)` in `~/proofcut-work/projects/scream-v2/out.mp4`.
   Two levels on one channel is h.264 chroma rounding. **No colour management
   is needed**; the false alarm is recorded because a one-frame probe is the
   obvious way to check and it will be rediscovered.

4. **Cards pillarbox, and it costs a quarter of the frame.** Now a measurement
   rather than the wiki row's prediction. `ops._mlt_resolution` takes the
   canvas from the first video clip — the film's **1920x816** crop — and the
   1920x1080 cards fit to it by height. In `out.mp4` the card occupies
   x ∈ [232, 1686]: **465 px of 1920, 24% of the width, is black bar.**

5. **melt will read an SVG directly, and taking that shortcut would split the
   rasteriser in two** — it goes through Qt, not librsvg (wiki `tooling.md`
   § Rasterising SVG). A card previewed through one renderer and rendered
   through another can disagree with no error on either side, which is this
   repo's recurring failure shape. So proofcut rasterises to PNG itself and never
   hands melt an SVG — which the current code already does by accident, since
   `_resolve_asset` and `preview_source` both hardcode `<name>.png`.

### The design

**Asset format: SVG in, PNG out, both kept.** `assets/cards/<name>.svg` is the
source the agent authors and re-edits; `assets/cards/<name>.png` is the
rasterisation the cue resolves to. Both, because the cue table and the
preview `<img>` want a raster (and finding 5 says keep it that way), while a
card you cannot re-edit is a card you have to redraw from scratch to change a
year. No schema bump — `assets/cards/` is a directory, not a manifest field,
the precedent `CARDS_DIR` set at step 2 of the layered timeline.

**The generator is a new `graphics.py`**, shelling `magick` the way `asr`
shells whisper and `picture` shells melt — same reasoning, an external
renderer with a resolution order and no Python API worth binding. It returns
the font-substitution report for every `font-family` in the document, reusing
`captions.font_match` rather than growing a second one.

**Templates are SVG files with `{{slot}}` placeholders and a slot manifest**,
filled by XML-escaped string substitution — not a template engine. The starter
set is the three the Scream assembly actually used, not a speculative library:
`receipt` (title/year/stars/date/quote), `reveal` (title/year), `rerate`
(before/after). Their docs' "iterate one graphic at a time" is prompt guidance
and free to adopt.

**Cards generate at the project's canvas size, not at 1920x1080.** This is
what finding 4 actually says: the question was never "should cards
pillarbox", it is that a card was authored at a different aspect from the film
it sits in. `_mlt_resolution` already computes the canvas; `graphics` takes it
and the templates are authored in relative units. Existing cards keep working
— they still scale to fit — and new ones stop discarding 24% of the frame.

### Animation is a length problem, not a rendering one

This is the part that costs, and the cost is not where it looks.

`mlt.plan_picture` refuses a shot longer than its asset. A still has no length
to run out of: `IMAGE_LENGTH_SECONDS` claims four hours and `eof=continue`
holds the last frame, so a card fits any shot. **An animated card is a video
clip, and a video clip has a length.** But a shot's length is *derived* — it
runs from its cue to the next cue, through the current edit. An animated asset
baked to N seconds is a cue carrying an explicit length, which is precisely the
music-bed failure § The property everything below defends records as the
converse proof: cues carrying lengths tuned to the old runtime were
invalidated wholesale by a ~12 s append, while the 37-shot plan recomputed for
free from two `lucid cut` commands.

So the ordering is forced, and it is not a preference:

- **Static cards are the whole of the first build.** They inherit the property.
- **Animation, when costed, must be length-agnostic.** Intro-then-hold
  (animate in, then hold the last frame indefinitely) and loop are the two
  shapes that are; `eof=continue` already gives the hold for free on a
  `qimage` producer and would need it stated explicitly on an `avformat` one.
  **A fixed-length animation is the one to refuse**, and the refusal belongs at
  *authoring* time — `plan_picture`'s existing refusal fires at export, which
  is far too late to be told the graphic is the wrong length.
- Two mechanisms, when it comes: a melt `<filter>` on the entry (affine
  keyframes), which **`mlt.py` cannot express at all today** — `_playlist`
  writes bare entries and there is no filter support anywhere in the module —
  or an ffmpeg-rendered clip from an SVG frame sequence, which needs **zero**
  `mlt.py` change and reuses the existing non-image path. The second is
  cheaper and gets tried first.

### Build order, and where it stops

1. `graphics.py` — `render_svg`, with the font-substitution report. CLI
   `proofcut card render`, MCP parity, per CLAUDE.md § Conventions.
   **Shipped 2026-08-09** — HISTORY.md § The card renderer, which records what
   the four measurements behind it decided and one they changed: `-size` fits
   rather than distorts, so step 3 below is the only thing that can close
   finding 4.
2. Templates and card creation: fill a template's slots, rasterise, land both
   files. CLI and MCP parity again. **Shipped 2026-08-09** — `proofcut card new`
   and `card templates`, the three templates read off the real Scream cards,
   and the escaping split that a string template lives or dies on.
   HISTORY.md § Card templates.
3. Canvas-size defaulting, closing finding 4 for new cards. **Shipped
   2026-08-09**, in the same change — and the stdio suite is what caught the
   MCP tool still defaulting to 1920x1080 while the op defaulted to the
   project.
4. **Stop.** Animation gets its own note, after a watch of a real card-heavy
   cut — the same discipline the layered timeline used.

**The watch that step 4 stopped for happened, and it refused the cards at
9:16** — not for the reason it was filed under. § The vertical card layout —
the design note (2026-08-11) carries the four findings: the type is the same
size on screen at both aspects, the fit constraint that shapes the 16:9
templates is absent at portrait, an enlarged title walks into the one
unmeasured slot, and the footer sits in the platform UI band. Animation is
still parked behind it.

No properties pane and no styling UI: the agent authors and the window
renders, which is the parity target docs/plans/DAYDREAM.md states. Two things stay
deliberately unanswered — whether the existing Scream cards get regenerated at
1920x816 (Tyler's call on a watch, and the wiki row already carries it), and
per-word caption animation, which shares "one Dialogue event per word" with
nothing here and neither blocks this nor is blocked by it.

## B-roll by description — the design note — 2026-08-09

The costed note docs/plans/DAYDREAM.md § B-roll by description and § Next ask for before
any build. **The finding that shapes it inverts the item's own framing.**
docs/plans/DAYDREAM.md says lucid "has the placement substrate coming (cues) and one
primitive (`spot_frames`)" and lacks indexing and search, with the indexing
half being the part that must not be copied blind because Daydream's metering
implies cloud inference.

Measured, both halves come out the other way round. **The indexing half is
nearly free and entirely local** — the model is already on this box, the
pipeline that drives it is already written in a sibling repo, and a project's
whole footage library describes in about six minutes. **The placement
substrate is the part that does not exist.** A cue addresses
`(clip_id, word_index, asset)`; where inside the asset a shot reads is decided
by `mlt.plan_picture`'s per-asset cursor, in consumption order. Search's whole
output is *a moment* — "cold-open at 312s" — and there is no way to say it.
The missing piece is not an index. It is an in-point on a cue, and it is small.

### Measured on this box, 2026-08-09

Six measurements, on the real Scream footage (`~/proofcut-work/projects/scream-v2/proj/media`,
nine clips, 14s to 730s), because the last two notes both found the toolchain
was not what it was assumed to be.

1. **The describe model is resident and the loader is already written.**
   `Qwen2.5-VL-7B-Instruct` is 31 GB in the HF cache; `vaultmedia`'s
   `tagger_core.load_qwen` loads it 4-bit (nf4, bf16 compute) in **14.9s**, and
   `run_vlm` is a generic frames+prompt pass. Nothing needs downloading and no
   inference code needs writing. What proofcut must **not** reuse is that repo's
   prompt and vocabulary — both are specific to that repo's own library —
   only the loader and the generation pass. That the model is cached and that the tagging venv
   exists are box/cross-repo facts and belong in the wiki, not here; this note
   cites them.

2. **Cost is per window, not per second of footage.** Sampling plus one
   description is **~2.6–3.3s per window**, near enough constant regardless of
   how much footage the window spans:

   | clip | span | windows | total | per minute of footage |
   |---|---|---|---|---|
   | `s1996-billy-stu` | 30s | 3 × 10s | 7.9s | 15.7s |
   | `s3-reveal` | 55s | 5 × 10s | 16.5s | 18.0s |
   | `s2022-reveal` | 160s | 8 × 20s | 22.9s | 8.6s |

   So window *length* is the only cost knob, and the Scream project's ~1300s of
   video is **~6 minutes at 10s windows, ~3 at 20s**. That makes `describe` a
   *job*, not a request — the same shape as the still-open preview transcode,
   and it should reuse `/api/render`'s background pattern rather than invent a
   second one.

3. **VRAM is the ceiling, and this box's always-on MoE server sets it.** The
   RTX 5070 has 11.5 GiB usable and `llama-server` holds **3.5 GiB
   permanently**. Six frames at 420x360 peaks at **6024 MiB and fits**; twelve
   frames **OOMs** — which is what vaultmedia's own `num_frames_for` rule asks
   for on a 730s clip, so the 730s cold open is exactly the clip that failed.
   proofcut therefore **cannot scale frames with clip length**; it holds
   frames-per-call fixed and scales window *count*. Finding 2 reaches the same
   place from the cost side.

4. **A whole-clip description is not merely vague, it is wrong — and windows
   fix it.** One 6-frame pass over the 30s Billy/Stu clip described **six men**
   where there are two: it read six frames as six people, in fluent prose, with
   no signal that anything was off. The same clip in 10s windows of 3 frames
   reads correctly and concretely — kitchen, white cabinets, blood, a knife.
   The whole-clip pass on `s3-reveal` did the softer version of the same thing,
   collapsing three distinct locations into "a person… another person… a third
   person" and discarding *when* each was. Windowed output is specific enough to
   search on: "a kitchen with blue tiled walls and a white range hood featuring
   circular vents".
   - **Two residual error classes, recorded so they are not rediscovered as
     bugs.** The model still narrates *across* a cut inside a window as though
     it were one take ("the camera remains stationary as the figure turns to
     face away"), so a window is not evidence of a continuous shot. And at
     `max_new_tokens=120` two windows **truncated mid-sentence** — an index
     entry that ends mid-fact, which reads as a complete description. Whatever
     limit ships has to be checked against, not assumed.

5. **Scene detection cannot choose the windows, and it fails by producing
   output.** `select='gt(scene,T)'` is cheap — 730s scanned in 11.1s, ~66×
   realtime — but `T` is not portable across footage. At the *same* threshold
   0.3, the 160s clip yields **23** cuts and the 730s cold open yields **5**.
   Five windows over 730s is a 2.4-minute window described from three frames:
   precisely the blur finding 4 rejects, arrived at silently and looking like
   success. Sweeping the cold open confirms the knob is a cliff rather than a
   dial — **0.1 → 118 cuts, 0.2 → 50, 0.3 → 5**. So **fixed windows are the
   default**, and scene detection is only ever allowed to *subdivide* one.

6. **The homebase encoder service is not relevant compute.** docs/plans/DAYDREAM.md put
   port 8765 on the checklist as possibly-relevant. It is vaultmedia's ffmpeg
   transcoder — zero inference, no model of any kind. Struck from this item;
   it is, however, relevant to a *different* open one, the preview proxy
   transcode.

### The design

**A description indexes the source, which is why an edit cannot invalidate
it.** The unit is `(clip_id, src_start, src_end, text)` in **source** seconds.
The b-roll asset is not the thing being cut, so its own times never renumber —
the same reason word indices are safe, and not in tension with § The property
everything below defends, which is about cues carrying *timeline* lengths.

**They live in the manifest, and that is a schema bump.** v2 → v3, with a
`_MIGRATIONS[2]` entry keyed by the version it migrates *from*, exactly as the
caption/cue work established — the migration mechanism exists so a bump is a
step rather than a widening of `Project.open`. Rejected alternatives: a sidecar
directory on the `assets/cards/` precedent (a card is a file a cue names by
convention; a description has no natural filename and is per-clip metadata
`info` should report), and `cache/` (disposable, and these cost GPU minutes).

**The runtime is a subprocess, resolved the way whisper is.** `asr.transcribe()`
shells the binary via `PROOFCUT_WHISPER` → PATH → a sibling venv specifically so
proofcut never imports a heavy model runtime; `describe.py` gets the same shape —
`PROOFCUT_VLM` → the sibling tagging venv → a refusal that names what is missing.
proofcut's own venv gains no torch. It also keeps the sibling repo's prompts out of
proofcut: proofcut passes its own.

**Search is the agent reading the descriptions, and that is right up to a
measured ceiling.** ~130 windows for this project at ~60 words each is roughly
**10k tokens** — the agent is already in the window and can simply read them.
No embedding runtime, no vector store, no similarity threshold to tune. The
ceiling is real and worth writing down: at ~1000 windows (≈5.5 hours of footage
at 20s) it is ~80k tokens and stops being reasonable. Embeddings become the
right answer only when a **cross-project** library exists, which proofcut still
does not have — multi-project (built, HISTORY.md § The multi-project picker,
built) is a picker over `proofcut web --root`, one project per process still, not
an index. So `describe_ls` returns the
table, optionally filtered by clip, and the agent picks. Keyword filtering is a
convenience on top, not a subsystem.

**The one thing that must be built rather than reused: a cue that can name a
moment.** `cue_add` gains an optional `src_start`, and four constraints come
with it:

- **In-point only.** The out-point stays derived from the next cue, through the
  edit. A cue carrying both ends is the music-bed failure § The property
  everything below defends records.
- **A pinned cue refuses rather than rewinds.** `plan_picture` today rewinds a
  cursor that would overrun its asset, which is right for an unpinned re-use —
  clamping would read as a frozen frame, a render bug. For a *pinned* cue,
  rewinding silently shows footage the search did not find: correct pixels,
  wrong video. It refuses, and the refusal arrives as `shots_error` for the
  lane to draw, never as an exception — the picture lane's existing contract.
- **Same schema bump as the descriptions**, one migration for both.
- **No new plumbing for the window.** `timeline_view`'s shots already carry
  `src_start` (`ops.py:632`), and both `player.js` and `timeline.js` already
  read it, because the preview picture layer needed exactly this field.

### What this note refuses to build

- **No embeddings, no vector store, no CLIP.** Measured unnecessary at project
  scale; the trigger to revisit is a cross-project library, not a hunch.
- **No "watch on import".** It would make every import a six-minute job and
  describe footage nobody uses. `describe` is explicit, like `transcribe`.
- **No automatic placement.** The agent places, through `cue_add`, after a human
  or the agent has read the description. A pass that picks *and* places puts a
  wrong shot into a finished film with nothing on screen saying so.
- **No re-describe hook on edit.** Descriptions index the source; cutting the VO
  cannot invalidate them. Stated explicitly so nobody adds the invalidation.

### Build order — step 1 shipped 2026-08-09

1. `describe` — the subprocess, fixed windows, manifest storage, the schema
   bump, and skip-if-already-described. **Shipped**, with the corrections
   measured on the way in: HISTORY.md § `describe`.
2. `describe_ls` — the table. **Shipped 2026-08-09**, CLI and MCP, and it took
   `info` with it. The web half of the parity this line asked for was *not*
   built, deliberately: nothing in the window places a cue, so a descriptions
   pane would be a view of a decision the UI cannot take. HISTORY.md
   § `describe_ls`.
3. `cue_add --src-start`, `plan_picture`'s pinned-entry refusal, and the stdio
   and HTTP suites that prove both are reachable rather than merely written.
   **Shipped 2026-08-09**, verified by a real melt render rather than by the
   suites alone: HISTORY.md § The pinned cue. What it added is a second field
   name — the cue's ask is `src_pin` and the planner's answer is `src_start`,
   because carrying both under one key would have read correctly in every test
   that had a pin in it.
4. **Stop.** Watch a cut that actually uses b-roll before adding ranking of any
   kind — the same discipline the layered timeline and the card note used.
   **The cut got made on 2026-08-10, and the stop earned itself.** Ranking was
   the wrong next question: the index it would rank has no separation in it —
   0.201 word overlap between two windows of the same clip against 0.161
   between two *different films*, and `killer`, `unmask`, `stab` and `costume`
   appear in none of the 139 descriptions. The placement half held on real
   footage; the describing half returns rooms and clothing because
   `describe.PROMPT` asks for rooms and clothing. HISTORY.md § The b-roll cut,
   on real footage.
5. **The prompt was the wrong suspect too, and the answer is `synopsis` —
   shipped 2026-08-10.** Rewriting `describe.PROMPT` to ask for events was next
   until it was costed against the alternative it was competing with: the clips'
   own *filenames* already name the event (`scream3-reveal-roman-brother`) and
   score 3 of 25. The information is not missing from the index; the connection
   is not lexical. So the corpus moved up a level — one sentence per clip saying
   what the footage *is*, allowed to carry what no camera can see — and the
   choosing moved out of proofcut, to whatever is reading the brief. 2/25 → 14/25,
   with the whole loop driven off `broll_brief`'s own output. **A second
   reviewing pass was tried and scored worse (13 → 10), so there is not one.**
   HISTORY.md § Choosing the b-roll.

## Aspect swap — the design note — 2026-08-09

The small note docs/plans/DAYDREAM.md § Aspect swap and § Next ask for before any build.
**The finding that shapes it inverts which half is hard.** docs/plans/DAYDREAM.md
records the multi-source side as the blocker — "the melt path cannot take a resolution
at all until HISTORY.md § 4's memory-growth combination is isolated — so a real
9:16 needs an answer on the multi-source side, not just a flag on the other
one" — and `ops.export` and `picture.render` both carry the same reading.

Measured, it comes out the other way round. **The melt path renders a 9:16
frame today, with its consumer untouched, and reframes with one filter.** The
resolution on that path is a `<profile>` attribute, and `mlt.document` has
taken a `resolution=` argument since the MLT writer shipped; the consumer was
never the knob. **The single-source path is the one with no answer** — `-res`
letterboxes and auto-editor has no reframe flag to teach. So the item is not
"add a resolution to both writers". It is: give the project a canvas, route
anything that overrides it through the writer that can already do the work,
and decide what part of the frame survives the crop.

### Measured on this box, 2026-08-09

Rendered against the real Scream footage (`~/proofcut-work/projects/scream-v2/proj/media`,
`cold-open.mp4`, 1920x816), through `picture.render` — not through a
hand-run melt, so the display env, the `$HOME` staging and the memory cap are
the ones proofcut actually uses.

1. **A 9:16 profile renders, and `RENDER_ARGS` is untouched.** `mlt.document(
   resolution=(1080, 1920))` over a 1920x816 source produced a **1080x1920**
   file, memory cap 6G, no growth, against a 1920x816 baseline from the same
   script. The consumer stayed the four measured-safe keys throughout.
   HISTORY.md § 4's finding is about *restating the profile on the consumer*;
   declaring it in the `<profile>` is a different mechanism and always was.
   `ops.export`'s refusal of a caller-supplied `resolution` on the melt path
   is still correct — widening the consumer remains unmeasured — but the
   inference docs/plans/DAYDREAM.md drew from it, that 9:16 is therefore unreachable
   there, is not.

2. **A swapped profile pillarboxes, and the geometry is exact.** Sampled
   pixels rather than exit codes: the content band is **459 of 1920 rows**,
   x 236..845 — the source scaled by 1080/1920 = 0.5625 (419→236, 1503→845),
   to the pixel. **76% of the frame is black bar.** So melt at a swapped
   profile produces precisely what auto-editor's `-res` produces (docs/plans/DAYDREAM.md
   § Export presets, measured 320x240 → 608x1080). **Both paths can already
   make 9:16 pixels and neither reframes** — resolution was never the missing
   piece on either one.
   - **The first sample said 104 rows**, which reads as something worse than a
     pillarbox. It was a dark scene, not geometry. A brightness-thresholded
     bounding box across five frames is what settled it — the same reason the
     picture layer is verified by canvas readback rather than by eye
     (HISTORY.md § The preview picture layer).

3. **A real crop-to-fill reframe renders today, and it is one filter.** A
   `qtblend` filter carrying `rect="-1719 0 4518 1920 1"` (fill by height,
   centre the overflow) hung on the source producer fills the frame: content
   spans y **0..1919** and x **83..1079**, against the pillarbox's 459-row
   band. No new mechanism, no new dependency, no consumer change — `mlt.py`
   already writes `qtblend` for compositing. docs/plans/DAYDREAM.md's "mechanically
   modest after the MLT writer exists" is right, about the path it called hard.
   - **The filter has to go on every node, not every resource.** The probe
     matched **two** nodes for one file, because `mlt.py` writes one node per
     distinct resource *per role* — a file used by both the edit and the
     picture lane would otherwise be reframed on one track and letterboxed on
     the other, in the same frame.

4. **The project holds two independent derivations of its canvas, and no place
   to override either.** `ops._mlt_resolution` (first video clip, else 1080p)
   and `ops._caption_canvas` (first video clip → `captions.canvas`) walk
   `clips` separately for the same fact. An override must reach both: quoting
   captions against a 16:9 reference over a 9:16 render stretches the glyphs,
   which is what `captions.canvas`'s own docstring exists to prevent.

5. **An aspect swap orphans every card already made, and nothing on disk can
   re-author one.** `card_new` writes `assets/cards/<name>.svg` at the canvas
   and rasterises it; the template name and slot values come back in the reply
   and are **persisted nowhere**. The SVG on disk has the old aspect baked into
   its viewBox, and `-size` *fits* rather than distorts, so re-rendering it at
   a new canvas pillarboxes the card inside the frame. It is not a one-off — it
   recurs on every aspect change. **Cards are the only project state that is
   rasterised rather than derived**; captions survive a swap because they come
   off `caption_style` every time.
   - **The half of this finding about the *existing* cards was wrong, and step
     2 found out by building it.** It read `card_new`'s reply and inferred the
     cards in the Scream project came from it. They did not — they are twelve
     PNGs with no SVG, drawn by a `goodsometimes` script before `card_new`
     existed — the thirteenth of the assembly's set is the outro card, which is
     a tail in that script and not in proofcut's cue table at all. So the record
     cannot recover them, and this is not the wiki's "regenerate the 13 cards"
     item after all. HISTORY.md § The card record.

6. **The preview holds two ideas of the frame, and they agree today only by
   accident.** `player.js`'s `captionBox()` contain-fits the *project's*
   caption canvas into `#viewer`, while `#viewer video` is `max-width`/
   `max-height: 100%` in a flex-centred black box — so the picture's letterbox
   is the *media's* own aspect. The two match today because the canvas is
   derived from the media. An aspect override is exactly what separates them:
   a 16:9 source in a 9:16 project would draw full-width video with the
   captions boxed to a 9:16 sub-rectangle inside it.
   - **Closed by step 4**, and it turned out to be the smaller half of what
     the preview owed: there is one rectangle now, and it is #frame.

### The design

**A reframe indexes the source, so no edit can invalidate one.** The unit is
`(clip_id, rect)` in **source pixels** — geometry, never a length — the same
rule as a footage description, and for the same reason. Nothing here carries a
timeline duration, so it is not in tension with § The property everything below
defends.

**The project canvas is one field, and it feeds both derivations.** A `canvas`
in the manifest (`WIDTHxHEIGHT`, absent meaning "derive as today"), read by
`_mlt_resolution` and `_caption_canvas` before either walks `clips`.

**It takes no schema bump, and the first draft of this note was wrong to say it
did.** The precedent is written at `CAPTION_STYLE_KEY`: an additive optional
key whose absence means what every older manifest already meant needs no
version, and bumping for one "would make `Project.open` refuse every existing
project to gain nothing". v2 and v3 both bumped for *list* keys that other ops
`setdefault` — there the number is what makes the key true rather than
incidentally survivable. A canvas is the `caption_style` shape, not the
`descriptions` shape. **Where the bump does land is step 2**: persisted card
records are a list, and old cards on disk lack them.

**An aspect override routes through the MLT writer, whatever the source
count.** `export` picks its writer from the project and never from an argument,
precisely so a project that would render wrong cannot be argued onto the wrong
road; "this project overrides its canvas" is another way of asking
`_is_layered`'s question, and it gets folded in there rather than given a flag.
The single-source path is not taught to reframe — it cannot be.

**The default crop is centre, and it is reported rather than assumed.** A
centre crop of a 16:9 frame is wrong whenever the subject is not centred, which
in this footage is often. So the reply names the crop it used per clip and a
clip can override it. What `export` must never do is choose a crop by analysis
and say nothing: that is correct-pixels-wrong-video with a plausible file to
back it up.

**Cards become re-derivable, which `card_new` owes anyway.** Persist
`(template, slots, canvas)` alongside the card — the v4 bump — so a swap
can re-author every card at the new canvas, and so the 13 existing Scream cards
regenerate by command rather than by hand.

**The viewer's frame becomes the project canvas**, with the media contained
inside *that* — one more `contain`, in the layer that currently has none, so
the picture and the caption layer letterbox against the same rectangle.

> **Corrected by step 4, which built it.** `contain` was written before step 3
> decided the render *crops to fill*, and it only answers finding 6. Contained
> media in a canvas frame draws the whole 16:9 clip pillarboxed inside a 9:16
> box — still showing footage the export drops, now with black bars the render
> does not have. The build places media at the writer's own `dest_rect`
> instead, so the preview crops exactly where the render crops. A still keeps
> the `contain`, because that is what MLT does to one. HISTORY.md § The
> viewer's frame.

### What this note refuses to build

- **No consumer widening.** Whether `width`/`height` on the melt consumer is
  memory-safe stays unanswered *and stays unnecessary* — finding 1 makes it
  irrelevant to this item rather than a prerequisite for it. The refusal in
  `ops.export` stays exactly as it is.
- **No smart reframe** — no face tracking, no saliency crop. That is a model,
  and a wrong one reframes a film with nothing on screen saying so.
- **No animated crop.** A pan/scale over time is a crop carrying a length, and
  a cue carrying a length is the failure § The property everything below
  defends exists to prevent. It belongs with animation, after its own watch.
- **No in-place swap that leaves old cards behind.** A vertical render with 13
  pillarboxed 16:9 cards in it is the failure this item is meant to close, not
  a partial win. Step 2 gates step 3 for that reason.

### Build order — nothing built yet

1. **The canvas field** — **shipped 2026-08-10.** Manifest key, both
   derivations reading it, `status` reporting it, `canvas` op with CLI and MCP
   parity. **The routing came with it rather than waiting for step 3**, which
   the note originally had backwards: an override that `_is_layered` did not
   know about would send a single-source project to auto-editor, which takes
   the export and ignores the canvas — a 16:9 file at exit 0, the exact
   silent-wrong-output this item exists to close. So an overridden project
   routes through the MLT writer from the day the field exists, and the reply
   says so (`routes_through`). Until step 3 a swapped aspect pillarboxes, and
   the reply says that too (`fills_frame`).
2. **Card re-derivation** — **shipped 2026-08-10.** `card_new` records
   `(template, slots, canvas)`, `card_reauthor` fills the template again at
   the project canvas, and `canvas` names the cards a swap has left behind.
   The v4 bump lands here, as step 1 said it would. **What it does not do is
   close the wiki's 13-card item, and this step's premise was wrong about
   that**: the twelve cards in the real project are PNGs with no SVG, drawn by
   a `goodsometimes` script before `card_new` existed, so there is nothing to
   persist and nothing to re-render — and their receipts carry three ink
   levels that proofcut's one-fill `quote` slot cannot express. Closing that item
   needs an emphasis-capable quote slot first, **costed 2026-08-10 in § The
   emphasis-capable quote slot**, which found a second loss this step missed:
   the script wraps by measurement, so the slot needs a flow as well as an
   ink. HISTORY.md § The card record.
3. **The MLT reframe** — **shipped 2026-08-10.** Per-clip crop rects
   (`reframe`, with CLI and MCP parity) and the `qtblend` filter applied per
   node *per role*; the routing landed in step 1. Verified the way the pinned
   cue was, and the method mattered: the render fills the frame *and* matches
   ffmpeg's own crop of the source to 0.34/255, while the bbox check that
   settled finding 2 is uninformative on a dark frame and nearly reproduced
   its own trap. **The note under-specified one decision and the build had to
   make it**: an override is a rect of arbitrary shape, and growing it to the
   canvas rather than shrinking it into the canvas is what keeps the subject
   whole. HISTORY.md § The MLT reframe.
4. **The viewer's project-canvas frame** — **shipped 2026-08-10.** `#frame`
   is the canvas, every layer draws inside it, and media is *placed* at
   `timeline_view`'s new `reframe[clip].dest` — the writer's own rect — rather
   than fitted to its own aspect, so the preview crops where the render crops.
   `captionBox()` collapsed to the frame, which closes finding 6 as well.
   **The note said "contain" here and the build had to correct it**: contain
   would have closed finding 6 and left step 3's disagreement open, drawing
   black bars the render does not have. Verified in a real browser on two
   copies of the Scream project, geometry from `getBoundingClientRect()` and
   pixels against both hypotheses; it also turned up a shipped bug the picture
   layer's own readback could not see. HISTORY.md § The viewer's frame.
5. **`tiktok-reels`** — **shipped 2026-08-10.** One `EXPORT_PRESETS` entry
   carrying `youtube`'s four encode values, plus `PRESET_ASPECT`; the refusal
   text in `_resolve_preset` came out with it, and CLI, MCP and the window's
   preset menu all carry the name. **The note said "plus the canvas" and left
   open how the two halves meet, so the build decided it: the preset
   *checks* the project's canvas and refuses, and never sets it.** A preset
   that reshaped a project would be an export argument rewriting project
   state — the same class of failure as picking the writer from an argument.
   Verified by a real melt render measured at 90x160 and by the refusal
   against the real Scream project, which wrote nothing. HISTORY.md
   § `tiktok-reels`.
6. **Stop.** Watch a vertical cut before ranking anything further — the same
   discipline the layered timeline, the card note and the b-roll note all
   used. **This is where the item now sits, and it is the only thing left in
   it.** Three of the five steps above corrected their own premise by being
   built; nothing here has yet been looked at as a film.

## The emphasis-capable quote slot — the design note — 2026-08-10

The note § Aspect swap step 2 said it was not writing, and the one thing
standing between the twelve unrecorded Scream cards and being re-authored
through proofcut at all (HISTORY.md § The card record). It is nominally a
template change. **It is not, and the finding that shapes it is that the
costing named one loss where there are two — and the second one is a rule
this repo already wrote down.**

HISTORY.md § The card record costs it as emphasis alone: the script's receipts
"carry three ink levels inside one paragraph — `dim`, `key`, and an amber `em`
on the fragment the VO quotes — and lucid's `receipt` has one `quote` slot of
kind `lines`, drawn in a single fill." True. But `make_scream_cards.py:63` is
`wrap_runs`, a greedy word-wrap that **measures** each word against a body
width, and the emphasis runs cross the line breaks it produces. proofcut's
`_lines_markup` refuses to wrap on principle — "a wrap computed from a
character count is a wrap that overflows the frame silently on the first line
of wide glyphs" (`graphics.py:456`). So the slot needs emphasis *and* a wrap,
and the wrap is the half that argues with a standing rule.

**The rule survives the measurement, and its reasoning is what tells you how
to build the wrap.** It indicts character counts, which deserve it; it does
not indict measurement, and this box can measure through the exact renderer
that draws the card.

### Measured on this box, 2026-08-10

Through `magick`'s RSVG coder — librsvg 2.62.0, ImageMagick 7.1.2-13 — which
is the path `graphics.render_svg` already uses, at Zilla Slab 40px, the
script's own body size.

1. **Per-run emphasis renders through librsvg exactly, and reproduces all
   three of the script's ink levels to the byte.** One `<text>` carrying three
   `<tspan>`s with their own `font-weight`, `fill` and `fill-opacity`, sampled
   off the raster rather than eyeballed: `dim` → `155,151,145` (ink `#1a1714`
   at 0.42 over paper `#faf5ec` predicts 156), `em` → `232,161,60` (amber
   `#e8a13c`, exact), `key` → `26,23,20` (ink, exact). **There is no
   mechanism to invent here** — the emphasis half is a markup shape and a
   vocabulary, nothing more.

2. **A wrap measured through RSVG is accurate to ±0.8%; a character count is
   wrong by −34.6% to +83.4%.** Against the ink width of the whole line
   rendered, on three real Scream quote lines and two adversaries:

   | line | measured | character count |
   |------|----------|-----------------|
   | "might be the perfect horror slasher." | +0.8% | +16.3% |
   | "The satire is great. I know I am late…" | +0.3% | +19.8% |
   | "Falls apart a bit in the second half." | +0.7% | +25.0% |
   | `WWW MMM WWW MMM WWW MMM` | +0.7% | **−34.6%** |
   | `illillillill iiii llll iiii llll` | −0.3% | +83.4% |

   **The `WWW MMM` row is the rule's own case, and it confirms it**: the
   character count *underestimates* by a third on wide glyphs, which is the
   silent overflow `_lines_markup`'s docstring exists to prevent. Note the
   direction — a character count is not merely imprecise, it is unsafe in the
   one direction that produces a wrong card at exit 0.

3. **Measure candidate lines, not words — it is exact, and it costs the same
   number of renders.** The obvious cheap build measures each word once and
   sums with a space advance; that is the +0.3%/+0.8% column above, and it
   drifts to −0.3% on `illill…` because side bearings accumulate. But greedy
   wrap tests one *prefix* per word either way, so rendering the actual
   candidate line is O(words) too — and it is ground truth rather than a sum,
   it captures kerning, and it can see the mixed faces a styled line actually
   contains, which the per-word shortcut cannot without tracking run styles
   itself. The shortcut is more code and less accurate.

4. **100ms a render, so ~1.5s for a 15-word quote and ~48s for all twelve
   cards.** That is fine for `card_new` and it is a fact about *where* this
   lives: measurement belongs at authoring time and must never sit in a
   request path. `card_reauthor` at a new canvas re-flows, so a canvas swap
   over a card-heavy project pays it too — worth reporting, not worth
   avoiding.

5. **librsvg resolves CSS weights correctly, and `fc-match` disagrees with
   it.** `font-family="Zilla Slab" font-weight="600"` renders SemiBold (918px
   ink on a reference string) and `700` renders Bold (931px), which is right.
   `fc-match 'Zilla Slab:weight=600'` answers `ZillaSlab-Bold.ttf` — because
   fontconfig's weight scale is not CSS's. This nearly became a finding about
   losing SemiBold in the re-author; it is instead a finding about the
   reporter, and a live one rather than a future one. **`font_report` reports
   per `font-family` declaration and never reads `font-weight`, so its `drawn`
   field is already wrong on a shipped template**: `receipt.svg`'s title is
   `font-weight="700"`, librsvg draws Bold, and `fc-match 'Zilla Slab'` — the
   family alone, which is what the reporter asks — answers SemiBold. It is a
   wrong *report*, not a wrong render, and it only bites a family with more
   than one weight installed. Emphasis makes weight load-bearing for the first
   time, so the reporter needs the declaration's weight before step 1 lands;
   filed as its own remainder rather than folded into this note's steps.

6. **The typeface is not a loss, which the costing did not say either way.**
   proofcut's `FONTS` defaults are Noto Serif / Lato, not Zilla Slab — but they
   are ordinary overridable slots, so a re-author passes the script's own
   stack and gets the script's own faces, both of which this box has
   (`~/.local/share/fonts/ZillaSlab-{SemiBold,Bold}.ttf`). Nothing needs
   building for it; it needs writing down, because the twelve will look wrong
   in a way that has nothing to do with this item if nobody passes it.

### The two decisions the measurements do not make

- **The run vocabulary.** Three named levels (`key` as the default, `dim`,
  `em`) is what the source data has, and semantic names beat inline style
  because the `em` fragment is *the bit the VO quotes* — that is meaning, not
  ink. What the note recommends and does not consider settled is spelling it
  as lightweight inline markers rather than JSON runs, because the value
  arrives from a CLI argument and an MCP string, where JSON is hostile. A
  marker syntax owes an escape for a literal marker, and templates escaping
  every user value while inserting only proofcut's own markup raw is the rule
  that syntax has to survive.
- **What a quote that does not fit does.** The script's `wrap_runs` returns a
  final baseline and `receipt()` throws it away, so the original could overrun
  its own footer and nothing would say so. A flowed slot has a box; the slot
  **refuses** when the flow exceeds it, and names the width and the overflow.
  Growing the card instead is the tempting build and it is wrong for the same
  reason a cue cannot carry a length: the canvas is the project's.

### Build order

0. **The font report's weight** — finding 5's remainder, **shipped
   2026-08-10** ahead of step 1 as it asked to be. It was owed for a
   stronger reason than the finding states: `em` is a *weight* change, not
   only a colour, so emphasis is exactly what a family-only reporter cannot
   see. Three faults, all unsafe-direction, and the mapping is validated
   against renders rather than against `fc-match`. HISTORY.md § The
   emphasis-capable quote slot.
1. **The `runs` slot kind** — **shipped 2026-08-10.** Markers, three levels,
   the `[[` escape, per-run `<tspan>`s; all three inks reproduce finding 1's
   sampled values at L1 0. **The note under-specified one thing and it was a
   silent one**: per-run `<tspan>`s collapse the whitespace between them, so
   `the [em]perfect[/em] horror` draws as `theperfecthorror` at exit 0.
   `xml:space="preserve"`, once per line.
2. **Measured flow** — **shipped 2026-08-10**, with the character-count
   control built and run rather than cited: `WWW MMM` overflows a 1640 box
   by 1054 units where the measurement fits. Two decisions the note left
   open: the scratch canvas is the whole cost of a measurement *and* clips
   silently when it is too small (a clipped line measures narrower, which
   overflows the card), and the box is derived from the canvas rather than
   declared, because the bottom margin moves with the aspect.
3. **Re-author the twelve** — **ten of twelve, 2026-08-10, and the two
   refusals are the finding.** The receipt's header is fixed in template
   units while the canvas is 264 units shorter, so the quote box is 3 lines
   at 2.35:1 against 7 at 16:9, and the two longest reviews flow to 4 and 6.
   Correct rather than broken — before step 2 they overran the card in
   silence — but **it does not close the wiki's card row**, and what to do
   instead is editorial: shorten the verbatim reviews, split them across two
   cards, scale the receipt's header with the canvas, or keep those two at
   16:9 and accept the pillarbox. Done on a copy; the real project's twelve
   are untouched.
4. **Stop.** The cards exist so that the aspect swap's own step 6 — the watch
   of a vertical cut — has graphics in it. Card *animation* is still parked
   behind that watch and this note does not touch it. **This is where the
   item now sits**, alongside the editorial call step 3 raised.

## Per-word caption animation — the design note — 2026-08-10

The last named gap on docs/plans/DAYDREAM.md § Captions: lucid generates and styles
timeline-mapped captions, but the highlight is `\k`'s left-to-right fill and
nothing moves a glyph. The row costs it as two things sharing one construction
question — *"both want one Dialogue event per word rather than one per line,
and both are export questions to cost before building"* — and
`captions.py`'s `Preset` docstring says the same in code: *"a genuine
one-word-at-a-time highlight is a different construction (one Dialogue event
per word) and is not what `to_ass` writes."*

**That construction claim is wrong, and it is wrong in the expensive
direction.** One Dialogue event per word is the build that would force proofcut
to own text layout — libass decides where a word sits, and an event carrying
one word has no way to be told where the *other* words put it. Measured
instead: both the single-word highlight and the per-word animation are
per-word `\t` blocks **inside one Dialogue event per line**, which is the
shape `to_ass` already writes. The real cost is somewhere else entirely, and
it is a metrics question.

### Measured on this box, 2026-08-10

libass through ffmpeg 8.1.2 (`--enable-libass`), burned over a flat frame at
1920×1080 and read back per pixel, then reproduced on the real film
(`~/proofcut-work/projects/final-cut/out.mp4`, 1920×816, the caption canvas being the 2541×1080
PlayRes `to_ass` writes). Probes kept at `~/proofcut-work/archive/spikes/caption-anim/`.

1. **A single-word highlight is one event, and it is exact.** Per word, a
   block of `{\c<base>\t(on,on+1,\c<hi>)\t(off,off+1,\c<base>)}`. Sampled at
   seven times across a seven-word line, **exactly one word is in the
   highlight colour at every sample and it advances**, against the `\k`
   control on the same words accumulating 1 → 2 → 3 → 4 → 5 → 6 → 7. On the
   real film's first cue the same two constructions read 1,1,1,1,1 and
   2,3,4,6,7. The `\k` finding of HISTORY.md § Caption styling is reproduced
   exactly; what is new is that stepping out of it costs one tag, not a
   rebuild.

2. **Per-word animation is also one event.** `\t` inside a mid-line override
   block animates only the run that follows it, so each word carries its own
   pop. A 130% scale over 120ms on word 3 of 7 rendered at 42px→50px of ink
   height and returned. **No event splitting, no `\pos`, no layout of our
   own.**

3. **What one event per word actually gives you is a different feature.**
   Built to check: with no `\pos` — which proofcut cannot compute — every event
   centres itself, so the seven words drew as a single blob at x≈890–1030 at
   every sample. That is **one word alone in the middle of the frame**, which
   is a real caption style and is not "a line with the current word lit". The
   claim on file did not name a harder build of this feature; it named a
   different feature.

4. **The cost is metrics: a scale pop reflows the whole line.** The animatable
   tags split cleanly, measured as the drift of the line's outermost ink
   columns — which belong to the first and last words, neither of them the
   animated one:

   | tag | animates | line drift | |
   |-----|----------|-----------|---|
   | `\fscx`+`\fscy` | yes | **20 px** | reflows |
   | `\fs` | yes | **20 px** | reflows |
   | `\fsp` | yes | **18 px** | reflows |
   | `\fscy` alone | yes | 0 px | metric-neutral |
   | `\frz` | yes | 0 px | metric-neutral |
   | `\bord` · `\shad` · `\blur` · `\be` | yes | 0 px | metric-neutral |
   | `\alpha` · `\c` | yes | 0 px | metric-neutral |

   On the real film the same pop moves both edges of the line 13 px, seven
   times a line, while `\fscy` alone moves them **0 px across every frame of
   the pop**. So a *proper* scale pop and a line that holds still are
   mutually exclusive, and no amount of building changes that — it is what
   text layout is. `\fsp` compensation was tried and is not a way out: at the
   compensation that holds the width, neighbouring words merge.

5. **The trap, and it is the `\k` disagreement again.** The preview overlay
   must draw what libass will, and **CSS's natural pop is metric-neutral
   where ASS's is not** — `transform: scale()` does not affect layout, so the
   obvious browser implementation shows a still line with one word growing
   while the render shows the whole line breathing. Right in the window,
   wrong in the file, no error on either side. Whatever ships, the browser
   half animates the property that *reflows* (`font-size`) if the ASS half
   reflows, and the agreement is settled by reading back a burned frame
   against the page, never by looking at the page.

6. **An artifact worth writing down, because this repo has had it twice
   before.** The first pass of finding 4 reported `\bord`, `\shad` and
   `\blur` as *not animating at all* — zero pixels changed. They animate
   fine; the outline and shadow colours were black and the probe background
   was black. Same shape as the brightness-bbox misreads in CLAUDE.md: a
   black subject reads exactly like an absent one. The fix was a coloured
   outline, and the lesson is that "no effect" and "no contrast" need
   separating before either is believed.

7. **It is not an export cost.** No new dependency, no filter, no second
   pass — the same `-vf ass=` burn. The `.ass` grows about 4× (1.7 KB → 6.6 KB
   over eight cues; ~150 KB for the film's 178), which is nothing, and the
   sidecar stays a plain ASS file Kdenlive opens.

### The design

Two fields on the existing `caption_style` object, both additive and optional,
so **no `SCHEMA_VERSION` bump** — the standing rule in CLAUDE.md, and neither
is a list key another op would `setdefault`.

- **`emphasis`**: `none` | `fill` | `word`. This absorbs the `karaoke`
  boolean rather than sitting beside it, because two knobs for one thing is
  two answers — `karaoke: true` resolves to `fill` and `false` to `none`, the
  alias stays accepted and stays readable in every manifest already on disk,
  and `describe()` reports `emphasis` resolved. `fill` remains what `\k`
  writes, so no existing project changes appearance.
- **`animate`**: `none` | `pop`, plus `animate_ms` and `animate_amount`. Only
  meaningful with `emphasis` set, and refused with `emphasis: none` rather
  than silently ignored, per the standing rule on style fields.

**One derivation, as before.** `Cue.karaoke_spans()` already owns the rule
that a word's highlight begins where the previous one ended, and `as_dict`
already exports `highlight_start`/`highlight_end` — so the `word` mode needs
no new server field at all, and the pop needs only the two style numbers the
overlay already receives resolved. `_dialogue_text` grows a branch per mode
and stays the only place a cue becomes ASS. `\t` times are milliseconds
**relative to the Dialogue line's own start**, i.e. `cue.start`, which is the
one arithmetic detail that is easy to get wrong and silent when wrong.

### The build

1. **`emphasis`, with `word` writing per-word `\t` colour steps.** Style
   field, alias, echo, CLI and MCP parity. The test burns and counts
   highlighted blobs, because a test that asserts on the `.ass` text proves
   the string and not the picture.
2. **The overlay draws `word`**, calibrated against a burned frame the way
   HISTORY.md § Caption styling calibrated the `\k` fill — readback, not
   screenshot.
3. **`animate`**, whichever pop the watch picks, with finding 5's constraint
   binding the browser half.

### The watch happened, and the answer was `fill` — 2026-08-10

**Tyler watched the four treatments and picked 1, the `\k` fill proofcut already
writes. So the item closes having built nothing, and the design above is a
record of a road not taken rather than a plan.** The four are kept at
`~/proofcut-work/archive/spikes/caption-anim/` (`fill`, `word`, `word` + reflowing scale pop, `word`
+ metric-neutral vertical pop), served by `serve.py` on :8791.

**This is a deliberate divergence from Daydream, not an unbuilt row**, and
docs/plans/DAYDREAM.md § Captions now says so. Daydream lights one word at a time and
moves it; proofcut sweeps and does not. The measurement is what makes that a
choice rather than a limitation — findings 1 and 2 say either could ship for
about a day's work, so nobody needs to re-derive the cost to reopen it.

**What would reopen it, stated so the next reader does not re-run the
probes:** a watch that wants the *current word* legible late in a line. That
is the fill's one real cost — by the last word of a seven-word cue every word
is in the highlight colour, so the signal is "how far through the line are
you", not "which word is this". Nothing else about the fill is in question,
and no measurement here is stale: the tag table in finding 4 is a fact about
libass, not about this project.

**What did survive the item.** Finding 5's trap is now on record before
anything could trip it, and it applies to any future caption motion whatever
its shape. Finding 6 is a third instance of a failure this repo keeps having
and is cited from CLAUDE.md's brightness-bbox rule. And `captions.py`'s
comment no longer claims a single-word highlight needs one Dialogue event per
word — the claim was false whether or not proofcut ever ships one.

## The preview proxy transcode — the design note — 2026-08-10

The last open piece of the tier-3 workspace, and the one the wiki row already
shaped: *"a job, not a request, so it wants `/api/render`'s pattern, a cache
key and an eviction rule."* All three of those hold. What does not hold is the
sentence this document wrote above it, in § Tier 3 is the goal's codec-wall
bullet, and it is worth correcting first because it names the one build that
would reintroduce the bug the proxy exists beside.

### The premise this note overturns

That bullet says the fix is *"a cached proxy transcode into a new
`cache/proxy/`, **resolved the way `media.media_path()` already prefers an
attenuated copy**"*. Copying that resolution is exactly what must not happen.

`media_path()` prefers the attenuated copy by reading a **manifest key** —
`clip.get("attenuated") or clip.get("media")` (`media.py:342-352`) — and that
is what makes attenuation transparent to `export`, `verify`, `check_frames`
and every other downstream op *for free*. A `proxy` key folded into the same
chain inherits the identical reach, and the thing it would reach is the
render: a delivered file at preview quality. `tests/test_server_stdio.py`'s
`test_export_renders_the_attenuated_copy_not_the_original` is the historical
regression for this shape of bug pointing the other way — `autoeditor.to_v3`
once built `"src"` from `record["source"]` and skipped `media_path()`
entirely, so a render taken after `attenuate_noises` carried the noise at full
volume with `written: true` giving no sign.

So the rule is structural rather than procedural: **the proxy never enters the
manifest, and `media_path()` gains no branch.** A new
`media.preview_path(project, clip)` tries `cache/proxy/` first and falls back
to `media_path()`; its only callers are `webui._send_media`, `_send_asset` and
`ops.preview_source` (the swap is one line, `ops.py:1868`). `export` cannot
reach the proxy because it never calls the new function — not because a
resolution order was written carefully. That is the difference worth having.

The pin is two tests: the cheap one asserts `media_path()`'s return is
unchanged by the presence of a `proxy` key on the clip dict at all; the real
one attaches a genuinely unplayable `hev1` source, produces a proxy, renders,
and reads the *output's* codec back — still HEVC, never the proxy's H.264.

### What a transcode actually fixes, measured against `playability()`

`media.playability()` (`media.py:190-249`) refuses in four classes, and the
proxy closes three of them in one ffmpeg pass:

| Refusal | Example | Closed by a transcode? |
|---|---|---|
| Container not browser-openable | `.mkv` | **Yes** — remux to `.mp4` |
| No streams at all | empty/corrupt file | **No** — not a codec problem |
| Video codec outside `{h264, vp8, vp9, av1, theora}` | `hevc`/`hev1` | **Yes** — `libx264` |
| Pixel format outside `{yuv420p, yuvj420p}` | High 10 / `yuv420p10le` | **Yes** — `-pix_fmt yuv420p` |
| Audio codec outside the playable set | `ac3` | **Yes** — `aac` |

Only "no streams" is unfixable, and it should stay a refusal rather than
become a failed job. `playability()` itself does not change: it stays
reported-never-enforced and consulted only from the preview side.
`tests/test_media_playability.py`'s `_encode()` helper already builds a
fixture for every fixable class from a one-second `lavfi` source, so the
proxy's test suite needs no new binary assets.

### Homebase's encoder is struck, and this is the second row to strike it

§ Tier 3's bullet says *"homebase already runs an encoder service for exactly
this conversion (port 8765) — worth checking whether proofcut should call it."*
Checked, live, on this box: it is up, and it is the wrong tool twice over.

1. **It has no per-file API.** `vaultmedia/encoder_common.py` exposes exactly
   three routes — `/` (dashboard), `/state` (JSON progress), and
   `/cmd/<command>` (`pause`/`resume`/`hardstop`). It is a directory-walking
   batch daemon over a fixed `TheVaultData` root. Nothing can hand it an
   arbitrary path and get one file back.
2. **Its output would still not play.** `encoder.py`'s `video_args()` always
   encodes `libx265`/`hevc_nvenc` tagged `-tag:v hvc1` — its whole purpose is
   HEVC-for-Apple. proofcut's own `_PLAYABLE_VIDEO` excludes `hevc` under every
   tag, deliberately (`media.py:176-179`: an `hvc1`-tagged HEVC plays on iOS
   and not in the Chromium/Firefox `proofcut web` actually runs against). Its
   output would fail `playability()` and show the same black.

docs/plans/DAYDREAM.md § B-roll by description already struck this service off that
item's checklist for having no inference in it, and redirected it here. It is
now struck here too, for a different reason, and the note is: **grow our
own.** One `subprocess.run(["ffmpeg", ...])` in the `energy.attenuate` idiom.
`h264_nvenc` is available (RTX 5070) if latency ever matters, unmeasured.

### The cache key is the house one; the eviction rule is genuinely new

**Key: the resolved source's `st_size` + `st_mtime_ns`**, verbatim from
`_cached_waveform` (`ops.py:1745-1763`) — "cheap to check (no re-read of the
media) and exactly what `attenuate_noises` or a re-import changes when they
replace a clip's audio." A content hash is correct across an mtime-preserving
copy and costs a full read of a multi-hundred-MB file; no cache in this repo
uses one, and the disagreement is narrow enough that the convention has
already made this call once. Key off **`media_path()`'s result**, not
`clip["source"]`, so a proxy of the attenuated copy is a different entry from
a proxy of the raw original.

**Eviction has no precedent here and that is the finding.** Every existing
`cache/` subdirectory — `waveform/`, `attenuated/`, `verify/`, `frames/` — is
either one-entry-per-clip-overwritten-on-invalidation or hand-cleaned, and
none of them has an eviction rule. They get away with it because a waveform is
a small JSON. A proxy is a full-resolution H.264 re-encode, so a project with
many unplayable clips grows `cache/proxy/` without bound and nothing in the
current convention says what deletes one. **This is new ground, not a pattern
to copy** — which is precisely why the wiki row asked for the rule by name.

**Corrected at build time, 2026-08-11: there is no eviction rule, because the
premise above is wrong.** "A proxy is a full-resolution H.264 re-encode" was
assumed, not decided — and a preview proxy has no reason to be full
resolution. Measured on the Scream cut's own vertical render (1080x1920,
5.28 Mbps, real film footage), one minute transcoded:

| | size | encode |
|---|---|---|
| full resolution, CRF 23 | 24.2 MB/min | 5.5 s/min |
| 720-tall, CRF 26 | **3.2 MB/min** | 1.9 s/min |

7.6x smaller and 2.9x faster, which puts the film's whole 1324s of footage at
~70 MB — smaller than `cache/frames/` is already allowed to get. One entry per
clip, invalidated by the key above, is then the *existing* convention rather
than new ground, and it needs no policy on top.

The downscale is geometrically free, and that had to be checked rather than
assumed: `player.js`'s `place()` positions the element by `timeline_view`'s
`dest` rect in **canvas** coordinates with `objectFit: fill`, and never reads
`videoWidth`/`videoHeight` — so a uniform downscale draws in exactly the same
place. Nothing else measures a proxy's pixels; the picture-layer readback
calibration runs against the film's own footage, which is `avc1`/`yuv420p` and
never proxied.

**The size claim is about real footage and does not hold on a synthetic
fixture.** In the live check the 4s `testsrc` proxy came out *larger* than its
source (112,748 vs 95,699 bytes), because libx265 compresses a test pattern
pathologically well. That is a fact about testsrc, not about the ratio — but
it is why the measurement above was taken on a real render, and why no test
asserts a size reduction.

### The job shape, and the one decision it leaves open

`RenderJob` (`webui.py:555-743`) is the pattern, and it transfers almost
verbatim: `_lock`/`_running`/`_cancel`, an output path computed *before* any
state is touched so a bad request is a 400 rather than a job that starts to
immediately fail, `_finish()` in a `finally`, and completion pushed as an
event on the SSE bus rather than polled — **there is no GET-by-job-id route in
this codebase at all.** `POST /api/proxy {"asset": ...}` → `202 {"job_id"}`,
with `"running"` → `"done"`/`"error"`/`"cancelled"` on `/api/events`. The op
itself is `ops.proxy_transcode`, with a CLI subcommand and an `@_tool()`
registration, per the standing parity convention.

**Open, and deliberately not decided here: whether a proxy job is
one-at-a-time per server like a render.** `RenderJob`'s single slot is
justified by "one render at a time per server" being an acceptable product
constraint. A proxy is keyed by *asset*, and a timeline can show several
unplayable shots, so copying the constraint means the second unplayable shot
clicked returns 409 while the first encodes. That may be right; it is not
measured, and the thing that would settle it is a real project carrying more
than one unplayable asset, which this box does not yet have.

**Taken 2026-08-11: one slot, and its own slot.** Still unmeasured, and taken
on asymmetry rather than on evidence — a wrong single slot costs a retry, a
wrong parallel one costs N concurrent x264 encodes on a box also running melt.
It is a *separate* slot from the render's, though: sharing one would make an
export refuse while a preview transcoded, which is not a conflict anyone asked
for. Revisit with real footage. There is also no `/api/proxy/stop`, because
cancelling is safe by construction rather than by handling — the sidecar key
is written only after ffmpeg returns, so an interrupted job leaves an unkeyed
file that reads as no proxy at all.

### Cost

Roughly: `project.py` +10 (a `PROXY_DIR` constant and a `proxy_path`, mirroring
`waveform_dir`/`waveform_path` exactly), `media.py` +50-80 (`preview_path` and
the transcode primitive; **`media_path()` itself changes by zero lines, which
is the point**), `ops.py` +60-100, `webui.py` +150-250 against `RenderJob`'s
own ~190, `cli.py`/`server.py` +30-50. Tests are the largest single piece, as
they were for the picture layer.

**Built, 2026-08-11** — HISTORY.md § The preview proxy. The structural rule
held exactly as written and `media_path()` did change by zero lines; what
moved was the eviction premise above, and the cost, which came in at the low
end because the job needed no cancellation path.

## Three uncosted parity items, costed — 2026-08-10

The wiki's parity row carried *"**Uncosted:** reel selection, music, a default
font"* with no note behind any of them. Costed here together because two of
the three turn out to be smaller than their names, and the third is larger.

### Reel selection — the mechanism exists; only the choosing is open

**The name is ambiguous and both readings matter, so both are costed.** The
row's own provenance is the 2026-08-10 log entry: *"Aspect swap approved,
driver a promo reel per video; reel selection is a separate uncosted
question."* So the primary reading is **which sixty seconds of a six-minute
film becomes the vertical reel** — not a gesture.

**Under that reading there is nothing to build, and that is the finding.**
`cut_by_time(spans=…)` already takes spans in *timeline* seconds — "what a
human reports watching an export" — converts them through `Edit.source_spans`
and cuts through the same `Edit.remove` path `cut_by_transcript` uses. So a
reel is: copy the project, `cut_by_time` the head and the tail, `canvas`, and
`export --preset tiktok-reels`. Every step ships today. **That is the dumb
control any reel feature has to beat**, and it should be built as a test
before anything cleverer is designed. **What it produces is not yet watchable,
and that is a framing problem rather than a reel one** — the centre crop
mis-frames a subject in most of the seconds that hold one (docs/plans/DAYDREAM.md
§ Aspect swap), so a reel built on it would be correct-pixels-wrong-video.

What is genuinely absent is one level up: **a reel is a derived project, and
proofcut has no project-derivation op.** The canvas is project state and the cuts
are destructive, so the copy is mandatory and is currently a `cp -a` done by
hand. `proofcut reel <start> <end>` — copy, two cuts, canvas, refit, re-author
cards — is the real shape, and it is small.

**Built, 2026-08-10** — HISTORY.md § `lucid reel`, the project-derivation op.
It was small, and two of its three real findings were only reachable by
deriving a reel of the actual film: the suspect-duration guard fires on
everything a reel removes rather than on the edges it keeps, and a reel
orphans nearly every cue, which `build_shots` refuses a whole projection on.
The second made the derived project pass every check and render nothing. The
44s control renders at 1080x1920 and is mis-framed exactly as § Aspect swap
measured, which is the argument for the framing item below rather than a fault
of this one.

The *selection* itself is the `synopsis` precedent, not a new algorithm.
§ Choosing the b-roll measured that lexical matching does not choose footage
and that proofcut should not choose at all — one line per clip handed to whatever
is reading, which writes back through `cue_add`. A reel wants the same:
narration plus synopsis handed out, spans written back through `cut_by_time`.
**Do not build a ranker.** That is the measurement that already exists.

**The second reading** is Daydream's timeline gesture — *"right-click-drag on
the timeline selects a range"* (docs/plans/DAYDREAM.md:102-104), the third b-roll entry
point beside the agent prompt and the transcript selection. That is UI-only
*if* one premise holds: that a timeline range translates to the word-index /
`src_start` pair `cue_add` needs. `timeline.js:344-361` already draws a
selection box, but it is driven by **word indices** and its `'selection'`
event is unwired and commented as speculative; there is no pointer handler on
the canvas at all. The premise breaks on a drag that starts or ends **inside a
gap a prior cut left**, where no surviving word sits under the cursor — and
"put b-roll over this silence" is a *likely* drag, not an edge case. If most
useful drags land on gaps, the build is gap-anchored placement, which is a
different and currently unbuilt address space. Cheap to settle against the
Scream project with `Edit.gaps`; settle it before writing any UI.

**Settled 2026-08-13, and the premise as written cannot happen.** A cut
ripple-closes, so its removed span has **zero width on the timeline** — the
film's 155.17s across 64 ranges is nowhere a cursor can go, and 16,813 sampled
instants all mapped back through `source_spans`. The real referent is a
*surviving pause*, and `cue_add` is in-point only, so only a drag's **start**
needs an address: **85–87% of sampled drags are word-anchorable**, and the
residual 14% is inter-word silence with a median of 0.54s. Gap-anchored
placement is not needed; the third entry point is UI work on the address space
that already exists, plus one snap rule. HISTORY.md § The gap that was never on
the timeline.

### Music — the headline was that it had nowhere to live

Daydream places music as short accent blocks on its own **A2** lane
(docs/plans/DAYDREAM.md:92-104), imported under a distinct role. **proofcut has one
now — the A2 lane shipped 2026-08-17** (§ The A2 music lane — the design note;
HISTORY.md § The A2 music lane, built), so read what follows as the costing
that got there rather than as the state of the code: the two absence claims
below — no third lane role, `grep -rn 'A2' src/lucid/` returning nothing — were
true when this was written and are not now. At the time it had no such lane and
could not grow one cheaply, for a reason already on the record.

`Edit`'s own docstring: *"One track. A/V are linked… there is no way to cut
picture without sound yet."* `mlt.document()` accepts exactly two lane roles,
`audio` and `picture`, and its length invariant only checks `picture` against
`audio`'s frame count. There is no third role, and `grep -rn 'A2' src/lucid/`
returns nothing.

**The trap is the length model, and this repo has already been burned by it.**
`cue_add` is in-point only, deliberately: a cue carrying its own length is the
failure § The property everything below defends exists to prevent. The
goodsometimes Scream assembly is the worked example — a music bed whose
lengths were tuned to one runtime was invalidated wholesale by a ~12s VO
append, while the word-indexed shot plan recomputed for free. **Music is the
one asset that genuinely wants a length** (a bed under a passage), which is
precisely the shape the rule forbids. Any design that reintroduces
explicit-length placement repeats a failure already written against.

So the first step is not a lane. It is to measure whether a bed can be
expressed length-agnostically — loop or hold, fading against the last
surviving segment rather than a baked timecode — the way § Motion graphics
already solved it for animated cards. **That premise is the one most likely to
be wrong, and it is not cheap to settle by inspection**: music is not
indifferent to when it ends, and a bed that loops past a natural beat is a
worse defect than a picture held one frame long. It needs a toy bed rendered
over the Scream cut and *listened to*. Until that watch happens, music risks
being costed as a rendering problem when its whole cost is in the length
model.

**The watch happened, 2026-08-13, and it answered a different question than
this note asks.** The looped bed lost to the control, and the control is not a
bed at all — it is *two cues placed against the film's structure*. So the
length model was never what was being heard, and "loop or hold" is the wrong
axis: what beat the machine was **arrangement**, which is placement, which is
the shape `cue_add` already has. Nothing above is wrong; it is aimed at a cost
that is not the binding one. HISTORY.md § The three served answers.

**And the item that answer deferred was raised and built, 2026-08-17.** The
closure above said music would come back as cue *placement* "when a video wants
it"; Lambs/Longlegs did, and what shipped is `proofcut music` — one bed asset over
one word span with fades, addressed by word index with the duration derived
live, never stored. Its design and the two render traps behind it are § The A2
music lane — the design note. **This item is closed.**

### A default font — the premise is dead on measurement

The row reads as "port Daydream's caption/template fonts" —
docs/plans/DAYDREAM.md:196-209 names Inter Tight and Poppins, deferred there as
*"irrelevant until the graphics work"*, a clause that expired when cards
and caption styling shipped.

**Measured on this box, 2026-08-10: none of them are installed, and neither is
proofcut's own default.** `fc-match "DejaVu Sans"` → Noto Sans; `fc-match "Inter
Tight"` → Noto Sans; `fc-match "Poppins"` → Noto Sans; `fc-list` counts zero
for both of Daydream's faces. All three caption `PRESETS` name `DejaVu Sans`.
So copying Daydream's choice moves the silent substitution from one absent name
to another absent name. **The item as named builds nothing.**

> **Superseded in two places, 2026-08-13, by burning rather than asking.**
> The `PRESETS` table now names `Outfit`, settled in the approvals round. And
> the sentence this paragraph used to carry — *"every one of them draws as Noto
> Sans"* — is **measurably false**: a burn naming `DejaVu Sans` is
> pixel-identical to a burn naming a family that cannot exist, and both differ
> from a burn naming the literal string `Noto Sans`. The old default drew as
> the *absent-name substitute*, which is a third thing, and there are two
> distinct flavours of wrong here that do not look like each other. This is the
> § The emphasis-capable quote slot rule biting the paragraph that states it:
> the claim above was `fc-match`'s answer written down as the render's.
> HISTORY.md § The caption default resolved by coincidence.

The real question is already on the record and explicitly not taken
(§ Direction and order): *whether proofcut's default should name a font this
machine actually has, because changing the table would silently restyle every
existing project.* That is the item, and it is smaller and different: make the
default resolve-safe rather than aspirational. Two shapes — vendor the faces
into fontconfig's path (note the web UI's `woff2` files do **not** put a face
where `fc-match`, libass or librsvg can see it; those are separate delivery
mechanisms), or name what resolves on the rendering machine, computed rather
than hardcoded.

Cards already dodge this and captions cannot: `graphics.py:464-466` uses CSS
fallback *stacks* ending in a generic, which is the SVG-native survival trick.
libass's `\fn` takes exactly one family name, so captions have no equivalent.
Only captions are exposed.

Whatever is chosen, it is not a schema bump — no new key — but it *is* a
behaviour change to every prior project's output, and nothing is stored
per-project until overridden. And it is settled by measuring a render's
pixels, never by `fc-match`, per § The emphasis-capable quote slot: `fc-match`
answers "is the family present", not "which face drew".

**Both shapes shipped, 2026-08-13, and the second one was the item.** The face
is vendored into the package (`src/proofcut/fonts/`, with `fonts.install()`
putting it where fontconfig looks), and `fonts.probe()` is the render check —
it burns the family and an impossible family and compares the pixels, so it
needs no stored reference and works for a family proofcut does not ship. What the
build found is that the vendoring had already half-happened by accident: the
face was on this box because a sibling repo's tooling fetched it, months before
captions named it. HISTORY.md § The caption default resolved by coincidence.

## Per-shot framing — the design note — 2026-08-10

The item the vertical line is waiting on, and the wiki row states its gap as
*"per-shot framing has nowhere to live (`reframe` is per clip)."* **That is
true and it is the smaller half.** Two measurements taken for this note move
the answer somewhere else entirely: the address space is not the cue, and the
render needs no new node.

The item is justified by a cut that can be distributed. § Three uncosted
parity items settled that a teaser is the only vertical output that reaches
the feed — the film is 5:36 against a 3:00 cap — and HISTORY.md § `lucid reel`
delivered the derivation, which renders at 1080x1920 with `check_frames`
delta 0 and is mis-framed in exactly the way § Aspect swap measured. The
centre crop leaves a subject **outside the frame entirely in 59.8%** of the
seconds that hold one. Framing is the only thing left between the reel and a
watchable one.

### Finding 1 — a cue cannot carry it: cues and camera cuts are unrelated clocks

A cue is addressed by a **word in the narration**. A camera cut is a fact
about the **footage**. Nothing aligns them, and the gap is not marginal.

The 44s teaser reel carries **4 cues**. The hand-framed teaser that was
watched and approved needed **15 windows** (`~/proofcut-work/projects/final-cut/render.py`,
`CROP`). Scene detection over the source range each of those 4 placements
actually reads — `_picture_plan`'s `src_start`, never `build_shots` alone —
finds camera cuts *inside* 2 of the 4.

Over the whole film, 25 footage placements, counted at five thresholds:

| scene threshold | cuts inside placements | windows to choose |
|---|---|---|
| 0.10 | 65 | 90 |
| 0.15 | 54 | 79 |
| 0.20 | 34 | 59 |
| 0.30 | 13 | 38 |
| 0.40 | 4 | 29 |

**The count is a tuning choice, not a fact** — 3.2× across an ordinary band,
and § Aspect swap's own "64 distinct camera shots" sits at about 0.18 rather
than being independent of one. Any detector build inherits this parameter, and
it changes the answer by more than a factor of three; that is a thing to state
in its output, not to pick quietly.

**What is threshold-robust is the direction, and that is what the design turns
on: at every threshold in the band the film needs more windows than it has
placements** — 16% more at the most conservative setting, 3.6× at the loosest.
So a number hung on the cue cannot reach the shots that need one, at any
setting. Subdividing a placement by adding cues is not the way out either: it
would quantise a camera cut to the nearest VO word and write editorially
meaningless cues whose only purpose is to carry a crop.

### Finding 2 — framing is source-addressed, and it is the `describe` shape

Once the cue is out, the home is the one every other durable fact in this repo
already uses. A footage description is `(clip_id, src_start, src_end, text)` in
**source** seconds so that no edit can invalidate one (CLAUDE.md). A reframe
rect is "geometry in source pixels, never a length" for the same reason. A
camera cut is a source fact at a fixed source timestamp, forever.

So framing is **`(clip_id, src_start, rect)`** — the window that applies from
that point in the source onward. Every property this repo defends falls out
rather than being engineered:

- **No cut can invalidate one**, so `reel` derives without pruning framing the
  way it must prune cues (HISTORY.md § `lucid reel`).
- **A clip used seven times gets seven correct windows for free**, because each
  placement reads a different source range and picks up whatever entries lie in
  it. `cold-open` is used 7 times; nothing has to be said seven times.
- **It carries no length**, so it cannot repeat the music-bed failure that
  § The property everything below defends exists to prevent.

It is additive and **optional** on the existing `reframe` entries — `src_start`
absent means "from 0 onward", which is exactly what every stored rect means
today. By CLAUDE.md's own rule that is **not a schema bump**: absent-means-what-
every-older-manifest-meant, and a bump would make `open` refuse every project
on disk to gain nothing. Today's per-clip reframe becomes the degenerate
one-entry case of the general thing.

**The existing rect format already expresses the hand-framing address space,
and is strictly wider than it.** `render.py` fixed `W, H = 459, 816` — the
full-height window, which on a 1920x816 source is the largest 9:16 rect there
is — so its table is scalars: one `x` per shot, no zoom. A stored rect is not
constrained that way. `_fit_rect_to_canvas` grows an ask to the canvas *aspect*
but not to the source's full height, so a smaller ask stays smaller and scales
up further: the format carries `x`, `y` and zoom where the hand table carried
only `x`. Nothing new is needed to hold the hand numbers, and the extra freedom
costs nothing until something asks for it.

### Finding 3 — the render needs no new node, and this was measured

The obvious blocker is that `mlt.document` writes **one node per distinct
resource per role** (`if entry.resource in picture_nodes: continue`), so a clip
used seven times has one node and one `qtblend` filter hung on it. Per-shot
framing looks like it forces one node per entry, which would collide with
`reframed_nodes`, the `wants_reframe` invariant, and the byte-identical
property.

It does not. `qtblend`'s `rect` is `type: rect, animation: yes` — keyframable —
and **its keyframes run on the producer's own source frames**, which is the
clock source-addressed framing needs and the one MLT would have rendered either
way at exit 0. Measured rather than assumed, and refuted from both directions
(`~/proofcut-work/archive/spikes/framing-probe/probe.py`): one clip read from `src_in=300` for 90
frames, a discrete `|=` step between two known windows, scored by pixel readback
against ffmpeg's own crop at the source timestamp the document claims —

- step keyframed at frame **310** → the window changes at **output frame 10**,
  which is `310 − 300`. A timeline clock would have shown no step at all.
- step keyframed at frame **20** → the new window is up from **output frame 0**;
  both keys were consumed before the read began. A timeline clock would have
  stepped at output frame 20.

Separation was an order of magnitude (440–1160 RMSE for the winner against
10,300–14,900 for the loser), so the readback is an answer rather than a
number — § Aspect swap's rule about the wrong hypothesis, applied.

So one node per resource per role **stays**, and it carries an animated rect
holding every framing decision for that file. The writer's change is that
`Reframe` widens from one rect to a source-keyed series, and `is_identity`,
`reframed_nodes` and `wants_reframe` widen with it. A clip with no framing
entries still emits exactly what it emits today, which is what keeps an
unswapped project's document byte-identical.

### What to build, in order — steps 1–4 shipped 2026-08-10

1. **The store and the op.** `src_start` on a `reframe` entry, `_stored_reframes`
   returning a series per clip, and `reframe` taking and echoing one. No schema
   bump. Refuse two entries at one `(clip_id, src_start)` the way `cue_add`
   refuses two cues at one word. **Shipped**, and it added one refusal the note
   did not have: a window past the clip's own duration, which would never come
   into force and would read as framing already dealt with.
2. **The writer.** `Reframe` as a series; the animated `rect` property; the
   three invariants widened. Verified against a real `melt` render, because the
   failure it prevents produces a file and exit 0. **Shipped and verified that
   way** — end to end through `export --render` on a deliberately *cut* edit,
   so source time and timeline time differ by five seconds and the step can
   only land in the right place on the source clock. HISTORY.md § Per-shot
   framing.
3. **The contact sheet.** `~/proofcut-work/projects/final-cut/audit.py` is the prototype: every
   shot at three moments, the window drawn in red **on the source frame**.
   § The hand-framed teaser found 2 of 15 hand numbers wrong and **neither was
   visible in motion** — they read as framing, because nothing in the frame
   says otherwise. This is a build item beside the framing, not after it; the
   output of any framing decision is unreviewable without one. **Shipped** as
   `reframe_sheet`, and it earned the "beside, not after" on its first run:
   two hand-picked windows on the real film, both plainly wrong on the sheet
   and both invisible in motion.
4. **The preview.** `timeline_view`'s `reframe[clip].dest` is one rect per clip
   and becomes one per shot — the picture layer already reads `src_start` off
   the shot (CLAUDE.md), so it has the key it needs. **Shipped**, and checked
   on the served page rather than reasoned about. Its one limitation, stated
   rather than discovered: a shot is placed by the window at its `src_start`,
   so a boundary *inside* a placement previews as the first of the two while
   the render steps mid-shot correctly. `reframe_sheet`'s `windows` count is
   how such a placement announces itself.
5. **Then, and only then, the detector** — judged on whether it beats the 15
   hand numbers, which is the control that already exists and was watched and
   approved. Not before: § Three uncosted parity items' finding about `reel`
   applies unchanged — build the dumb control first, and build it as a test.
   **The control is built, 2026-08-10** (HISTORY.md § The framing control):
   `tests/test_framing_control.py` holds the fifteen numbers re-addressed as
   `(clip_id, src_start, rect)` and the metric a detector is scored on, and
   the port was verified end to end through a real `melt` render — 14 of 14
   shots closer to the approved framing than to the centre crop. The bar it
   has to beat is now a number rather than a memory: **the centre crop covers
   0.568 of the approved window on average, is 199px off its centre, and on
   one shot shares no pixel with it at all.**
   **The detector is built, 2026-08-11** — § The auto-framing detector, and
   HISTORY.md § The auto-framing detector, built. `reframe_detect` clears the
   bar at the sampling it actually ships (0.750 / 114.0, never the lost subject
   the centre crop has), the threshold this item flagged as a 3.2× tuning risk
   is pinned — at 0.20 by the control in 2026-08-11, and **at 0.15 from
   2026-08-12**, when judging the detections rather than matching them against
   the hand table found 21 real cuts the old floor was discarding (§ The scene
   threshold, re-pinned in HISTORY.md) — and the ceiling turns out not to be
   detection: an oracle allowed only to pick *which* face reaches 0.863. So the
   pass proposes and `reframe_sheet` disposes, which is why `apply` is off by
   default. **Step 5 is closed; what is left is looking at the sheet.**

### Refused, with the reasoning

**Keyframed moves are not in step 1.** `render.py` used one eased move in
fifteen shots, and the two shots that *looked* like they wanted one wanted a
different static x instead — "the window was not moving too little, it was
parked in the wrong place." One in fifteen, with a 2-in-15 false-positive rate
against it, does not justify the mechanism up front. When it is built, note
that `render.py` wrote its keyframes in **absolute timeline seconds**, which a
single upstream cut invalidates wholesale; in source frames the same move is
edit-invariant, and MLT's default `=` interpolates for free. The mechanism is
already paid for by step 2 — it is the *authoring* that waits for evidence.

**The stacked two-pane split was out of scope**, both reasons expired on
2026-08-11, and it shipped the same day — § The stacked split for the
measurements, HISTORY.md § The stacked split, built for what went in. It is
*not* a new render path (a second node, no new service), and it stopped being
unreachable when Tyler reviewed the 39 proposed windows and named the
two-handers. What was right here is that it had to wait for single-window
framing to be insufficient against something real, which is exactly how it got
unblocked.

## The auto-framing detector — the design note — 2026-08-11

§ Per-shot framing, step 5 gates this on one thing: *"judged on whether it
beats the 15 hand numbers, which is the control that already exists and was
watched and approved."* That control is now `tests/test_framing_control.py`
and the bar is a number — **0.568 mean overlap, 199.4px displacement, one
approved subject entirely outside the frame** (HISTORY.md § The framing
control). Everything below is measured against it. The probes are
`~/proofcut-work/projects/framing-detect/`.

The headline: **a face detector beats the bar comfortably, the naive signal
loses to it, and the ceiling is not detection.**

### Finding 1 — the threshold this note was told to worry about is pinned by the control

§ Per-shot framing measured the scene threshold as a 3.2× swing across an
ordinary band and concluded it "changes the answer by more than a factor of
three; that is a thing to state in its output, not to pick quietly." Scored
against the approved boundaries rather than counted in the abstract, it stops
being a free parameter:

| threshold | cuts found | approved boundaries hit | recall | precision |
|---|---|---|---|---|
| 0.05 | 27 | 12 / 15 | 0.80 | 0.44 |
| 0.10 | 21 | 12 / 15 | 0.80 | 0.57 |
| 0.15 | 19 | 12 / 15 | 0.80 | 0.63 |
| **0.20** | **16** | **12 / 15** | **0.80** | **0.75** |
| 0.25 | 12 | 8 / 15 | 0.53 | 0.67 |
| 0.30 | 7 | 5 / 15 | 0.33 | 0.71 |
| 0.40 | 1 | 1 / 15 | 0.07 | 1.00 |

> **Superseded 2026-08-12 — the floor is 0.15.** The table below is kept
> because the *way* it is wrong is the finding. Its precision column is the
> share of detections that matched an approved boundary, so a real camera cut
> in a shot nobody had chosen to frame counted against the floor: it was
> measuring the hand table's coverage — fifteen windows over three of the
> film's nine clips — and not whether a detection was a cut. Judged the other
> way, on the frames either side of every candidate the film shows across all
> nine clips, **all 31 candidates from 0.141 to 0.244 are real cuts and the
> first non-cut is at 0.137**; the 21 detections between 0.15 and 0.20 that
> this table called imprecise are every one of them a cut. § The scene
> threshold, re-pinned in HISTORY.md, and `tests/test_scene_threshold.py`.

Recall is **flat** from 0.05 to 0.20 and precision climbs monotonically across
the same span, then recall collapses. **0.20 is the setting**, and the control
picked it rather than a preference. Everything below 0.20 buys false positives
and no boundaries.

**And the three "misses" are not detector failures**, which is the finding that
matters:

- `s1996-billy-stu` 0.5012 — no visual event at all. Luma is flat at 75–77
  across the whole first second, so nothing was there to detect; the human
  simply began the window twelve frames in. As a *boundary* it is the head of
  the placement.
- `s4-reveal` 3.6703 — **is** placement 10's own `src_start`. The edit supplies
  it for free.
- `s4-reveal` 7.3428 — the midpoint of the single hand-eased move, carried in
  the control as one discrete step. By construction there is no cut there.

So: **no camera cut in the control is missed.** Twelve of twelve are found at
0.20, and the remaining three come from the edit or from the mechanism
§ Per-shot framing deliberately refused. The boundary half of a detector is
essentially free.

### Finding 2 — faces clear the bar, and the obvious alternative is worse than doing nothing

Every rule scored over the same 16 windows, aggregating per-frame answers by
median, with the control's own metric:

| rule | overlap | displacement | lost | worst |
|---|---|---|---|---|
| centre crop — **the bar** | 0.568 | 199.4 | **1** | 0.000 |
| luma centroid | 0.551 | 203.6 | 0 | 0.098 |
| largest face | 0.749 | 114.2 | 0 | 0.407 |
| **area-weighted faces** | **0.755** | **111.6** | **0** | **0.480** |
| mean face | 0.750 | 113.6 | 0 | 0.480 |

Two things fall out, and the second is the one that saves work.

**The luma centroid is worse than the centre crop.** CLAUDE.md's standing
warning is that "a brightness bbox answers 'where is the bright part', never
'where is the frame'," recorded after it misread the same render twice. Here it
has a number: as a framing signal it is *below* not asking at all. It was
measured precisely because a saliency-flavoured rule is what anyone would reach
for first, and § Three uncosted parity items' rule is to build the dumb control
rather than remember it.

**The three face rules are within 0.006 of each other.** Largest face, mean
face and area-weighted faces are the same answer. So **the aggregation rule is
not a lever and must not be tuned** — the signal is doing all the work, and
time spent on the rule is time spent on noise.

The metric a watch would notice is `lost`: the centre crop puts one approved
subject **entirely outside** the vertical frame, and no face rule ever does.
That column, not the mean, is what the centre crop was refused for.

### Finding 3 — the ceiling is selection, not detection

An oracle allowed to pick *which* detected face to frame on, and permitted
nothing else — no new signal, no rule, no look-room:

| | overlap | displacement | lost | worst |
|---|---|---|---|---|
| centre crop | 0.568 | 199.4 | 1 | 0.000 |
| area-weighted faces | 0.755 | 111.6 | 0 | 0.480 |
| **oracle: best available face** | **0.863** | **62.1** | **0** | **0.584** |

Of the centre crop's 199px error, **faces remove 88px with no judgement at
all, knowing which face removes another 50, and 62px survive both.**

That middle term is this repo's own b-roll finding arriving in a new place. A
description does not choose the clip — `synopsis` does, and proofcut does not
choose at all (CLAUDE.md; HISTORY.md § Choosing the b-roll). **A face detector
does not choose the subject.** In a two-hander every face is a true positive
and only one of them is the shot, and no property of the boxes says which.

### Finding 4 — what the last 62px is, looked at rather than reasoned about

The four windows where even the best available face is wrong, each drawn on
its own source frame with the approved window in red and every detection in
yellow (`~/proofcut-work/projects/framing-detect/look.png`) — because § Per-shot framing step 3
is that a framing decision is unreviewable in motion:

- **`s4-reveal` 11.053 — the human framed a two-shot.** One face, and the
  approved window holds both it and the over-the-shoulder figure a
  face-centred window cuts. The subject of the shot is the conversation.
- **`s4-reveal` 8.800 and `s1996-billy-stu` 0.501 — look-room.** One face
  each, every detection agreeing within 20px and 168px respectively, and the
  human still put the face off-centre rather than in the middle of the window.
  Not a different subject; a different composition.
- **`s1996-randy` 0.834 — zero faces, and the subject is a screen.** A woman
  on a CRT playing inside the shot. RetinaFace sees nothing, the rule falls
  back to the centre crop, and it is 170px wrong. A screen-within-a-frame is a
  hole no face detector closes.

**No look-room rule is fitted to these.** Sixteen windows are the only approved
ground truth in existence; a rule tuned on them would score well on them and
mean nothing, and this repo has a standing rule against exactly that shape of
result. A composition rule needs a *second* approved set before it is anything.

### Finding 5 — what the pass would actually produce, on the film

Run over the 18 placements still on the centre crop, at threshold 0.20, with a
three-frame face probe per window:

- **35 windows**, against 18 placements — 1.9×, which is § Per-shot framing's
  threshold-robust claim (*"at every threshold the film needs more windows than
  it has placements"*) arriving on real data rather than in a table.
- **A face is found in 28 of the 35 (80%).** Seven windows arrive with no
  signal at all, and `s2022-reveal` [6.34, 16.77] is a whole placement with
  none.

So the pass serves four windows in five and **must name the fifth rather than
quietly centre-cropping it** — a silent fallback is indistinguishable in the
output from a framing decision, which is the failure mode every item in this
section exists to prevent.

### What to build

1. **`reframe_detect`, plan-only.** It proposes and never writes, the shape
   `cut --plan` established. Per window it echoes the boundary and **where the
   boundary came from** (cut score, placement head), the proposed rect, the
   face count behind it, and the threshold — § Per-shot framing's "state it in
   its output, not pick quietly", now that the control says which number to
   state.
2. **Windows with no signal are named, never guessed.** They come back as
   refusals with their reason, the way a card with no record is reported rather
   than reconstructed. The centre crop is not a fallback; it is the thing being
   replaced.
3. **It writes through `ops.reframe`.** Same function the CLI, MCP and web UI
   already call — the detector is a fourth client, never a fourth
   implementation.
4. **It is scored as a test**, extending `tests/test_framing_control.py`: the
   detector must beat 0.568/199.4 on the control and must never return
   `lost > 0`. The control existed before the detector did, which is the whole
   reason it is worth anything.
5. **Review on `reframe_sheet`, then approve.** The detector proposes, the
   sheet disposes, and nothing reaches a render unlooked-at. 2 of 15 hand
   numbers were wrong and neither was visible in motion; a detector's numbers
   get no more trust than a human's.

**The subprocess shape is the third instance of a pattern this repo already
has.** whisper is a binary (`asr`), the vision model is an interpreter
(`describe`, `_vlm_worker.py`), and proofcut's venv holds neither torch nor
onnxruntime and should not start now. insightface, onnxruntime and the
`buffalo_l` RetinaFace weights are all resident on this box in genstack's venv.
So: an interpreter named by an env var, a worker shipped in the package and
never imported, and a refusal naming both when it does not resolve.

**One open question, deliberately not taken here.** `describe` resolves
`PROOFCUT_VLM` to vaultmedia's `.venv-tag`; the face detector needs genstack's
`.venv`, which is a *different* interpreter. Whether that is a second variable
(`PROOFCUT_FACE`) or one "vision sidecar" resolver with two capabilities is a real
call, and it wants the second consumer to exist before it is answered. A second
variable is the smaller, more reversible move.

### Refused, with the reasoning

- **No look-room or composition rule**, per finding 4 — it would be fitted to
  the only ground truth that could test it.
- **No second signal for the screen-within-a-frame case.** One in sixteen, and
  it is precisely the case where a human looking at the sheet is cheap and
  correct.
- **No auto-apply, at any score.** The pass beats the bar on every column and
  is still 111px out on a 459px window — 24% of its width. That is a first
  pass to be reviewed, not a framing.
- **Keyframed moves stay refused**, unchanged. Shot 9 put a number on the
  mechanism (HISTORY.md § The framing control) and the call is Tyler's; the
  detector proposes discrete windows either way.
  - **Authored moves shipped 2026-08-13** and this line still holds as
    written: `reframe --interp` lets a *person* ask for a move, and
    `reframe_detect` still proposes discrete windows only. What the build
    settled is the one thing the mechanism had not been asked: MLT
    interpolates the segment **leaving** a keyframe, so the flag names the
    window a move arrives at and the writer flags its predecessor — measured
    on the teaser's own `s4-reveal` follow, 30 frames of 1067 differing and
    exactly the 30 between the two keys. HISTORY.md § The keyframed move.

### Built, 2026-08-11 — and the one thing the note had wrong

`faces.py`, `_face_worker.py`, `media.scene_cuts` and `ops.reframe_detect`,
with the CLI and MCP tools beside them. All five build items landed as written.
HISTORY.md § The auto-framing detector, built has the account; two things
belong here because they change what the note claims.

**The gate is scored at the sampling that ships, and it is a different number.**
Finding 2's 0.755 / 111.6 was measured on the probe's dense sampling — up to
sixteen frames a window. `reframe_detect` takes three. Rescored on three:
**0.750 / 114.0, `lost` still 0, worst still 0.480.** The conclusion is
unchanged and the reason is worth keeping: like the aggregation rule, the
sampling is not a lever either. `tests/test_framing_control.py` gates on the
three-frame number, because a gate measured at a density the code does not use
is a gate on something that does not ship.

**Two things the note did not anticipate, and the film found both rather than
the tests.** The second is that **a refused window is not a centre-cropped
one**: nothing is written for it, so whatever is in force carries over, which
anywhere but a clip's head is *the previous shot's framing* — 4 of the film's 8
refusals. That is worse than the default rather than equal to it, and the
refusal message claimed the opposite until the review page was built and looked
at. `falls_back_to` now names it. The first:

**"Is this window already framed?" is a frame, not an epsilon.** ffmpeg
reports a cut at 0.834167; the manifest holds 0.8342, because a hand window was
addressed through a timeline offset while the scan reads raw presentation times.
33µs apart, the same cut. An exact-match test called **fifteen of the sixteen**
approved windows unframed, and `--apply` would have written a duplicate beside
each one. Two boundaries inside one *source frame* are one window, which is not
a tolerance for slop but the resolution the render has — a reframe is emitted as
keyframes numbered in the producer's own source frames. With the frame in place
14 of 16 are recognised, and the two that are not are exactly the two finding 1
predicted: the window a human began twelve frames into a placement with no
visual event, and the midpoint of the eased move, where by construction there is
no cut.

## The stacked split — the mechanism, measured — 2026-08-11

§ Per-shot framing calls the stacked two-pane split *"a genuinely new render
path"* and puts it out of scope on a gate: *"unreachable until single-window
framing exists to be insufficient against."* **Both halves of that moved on
2026-08-11.** The gate opened — Tyler reviewed the 39 proposed windows and
named the two-handers, the car scene by name — and the render path turns out
not to be new. The probes are `~/proofcut-work/archive/spikes/split-probe/`.

### Finding 1 — it is a second node, not a new service

The spike that measured the split built it in an ffmpeg filtergraph
(`split`/`crop`/`pad`/`overlay`) and concluded a build would need *"a stacked
render path in `mlt.py`"*. It needs no such thing. Two `chain` nodes of one
resource, a `qtblend` filter on each with a different `rect`, and the
compositing transition the tractor already carries — every piece is machinery
the writer has written since the aspect swap. No new service, no crop filter,
no mask, and no `<blank>`.

This is § Per-shot framing's own finding 3 arriving a second time: that note
also expected a new node and found the rect keyframable instead. **The
mechanism keeps being cheaper than the treatment sounds**, because `qtblend`'s
rect is a destination and the profile does the clipping.

### Finding 2 — the panes are disjoint by construction, not by luck

A pane window spans the **full source height** — that is what makes it a pane
rather than a crop — so its width is fixed at `src_h * pane_w / pane_h`, and
the scale that fills the pane's width is `pane_w / that`. The scaled source
height is then `src_h * pane_h / src_h`, which is `pane_h` exactly, for **any
source shape**. The top pane cannot reach the bottom half however wide the
footage is; the horizontal overflow is clipped by the profile as usual.

Worth stating because the obvious defensive build — crop each pane first, or
mask it — is buying a guarantee the geometry already gives. The one shape that
fails is a source *taller* than 8:9, where the pane window is wider than the
frame; that refuses and falls back to one window.

### Finding 3 — the pane node hides on the rect it already animates

A clip carries solo windows and split ones, and the second node exists for the
whole clip either way, so it has to draw nothing during the solo stretches.
`mlt.py` emits no `<blank>`, ever, so the answer has to live in the rect
animation per-shot framing already writes. Both candidates were rendered —
**opacity 0 on the keyframe, and a rect parked below the profile** — on a clip
running solo → split → solo, and they produce **byte-identical frames**.
Opacity is what to build: an off-canvas rect is a real geometry that happens
to miss, and the next person to read it cannot tell that from a bug.

### What the render was checked against

A picture that looks right is not evidence, so the split was diffed against
ffmpeg's own two-pane crop of the same source frame **and** against the
treatment it replaces:

| melt's split, against | mean \|diff\| | max |
|---|---|---|
| ffmpeg's two-pane crop | **9.4** | 33 |
| the solo centre crop it replaces | 68.0 | — |

9.4 at a max of 33 is Qt's scaler against lanczos, spread smoothly. A geometry
error of one pixel spikes the max at every edge — which is what the
neighbouring source frames do (max 145), so the low max is also what identifies
the frame.

### Finding 4 — how often it fires, measured on the film's own windows

**The spike's 24.8%-of-shot-seconds was the wrong pass and was nearly
inherited.** It came from outside proofcut, on 64 shots found by its own cut
detection, before `reframe_detect` existed. Re-run over the 59 windows the
detector actually proposes — same footage, same timestamps, boxes kept this
time — the answer is a *quarter* of it, and the rule is what moves it:

| what counts as a split | windows | of 245.7s | share |
|---|---|---|---|
| median frame holds 2+ faces | 13 | 43.0s | 17.5% |
| …that one crop cannot hold | 12 | 42.0s | 17.1% |
| …and is not a crowd | 10 | 36.4s | 14.8% |
| **every sampled frame agrees** | 5 | 15.6s | **6.3%** |
| …and at least 1s long — what shipped | 4 | 15.1s | **6.2%** |

**`reframe_detect`'s `faces` field is detections summed over the sampled
frames, and reading it as a subject count is wrong twice**: one face reads as
3, and `s1996-randy`'s 33 is eleven people in a room watching a television.
The per-frame median is now reported as `subjects` beside it.

The row that matters is the fourth. On 4 of the 10 candidates one sampled
frame of three disagrees — a figure crossing frame, a face turning away — and
a split whose panes are wrong for a third of its length is worse than the
single window it replaces, because a stacked frame framing nobody is
unmistakably deliberate. Requiring all three frames is not a tightening of the
rule, it *is* the rule. Two more refusals fall out of the same table: a crowd
(2 windows, 9 and 11 subjects) gets no split because two panes would frame two
people at random, and a pair that **does** fit one window is not a split — 1
of the 13 multi-subject windows is that case, at a 401px span against a 450px
crop, and one window is the better picture whenever it is possible.

The minimum duration is nearly free and worth having: 3 of the 10 candidates
are under 1.2s and they are 1.7s of the 36.4s, so excluding them costs almost
no coverage and removes every window where the second pane would flash for
under thirty frames.

### What is still not measured

Which cluster goes in the **top** pane. The spike ordered them left-to-right
in the source and the build kept that; whether it reads correctly against a
cut is an editorial question and a watch, not a rule.

And whether the four are *right*. They are a proposal — `reframe_sheet` draws
both rects now, dashed for the lower one, which is where that gets settled.
`s2022-reveal`'s panes overlap heavily (344 and 711, 904 wide apiece), which
is the case where a split is legal and may still be the worse picture.

## The vertical card layout — the design note — 2026-08-11

The last unbuilt piece of § Motion graphics and templates, and the one thing
between the vertical cut and a card-heavy watch. docs/plans/DAYDREAM.md § Motion
graphics states the gap as *"one line of `graphics.py`: `view_height = round(
TEMPLATE_WIDTH * height / width)` keeps the design grid 1920 wide always, so a
1080-wide canvas renders every template at 0.5625 while the frame grows 1.78x
taller"*, and concludes that a vertical layout is a per-aspect template
variant rather than a scale factor.

**The conclusion is right and the reason given for it is wrong.** The
arithmetic checks out — a receipt title is 11.30% of frame height at 1920x1080
and 3.57% at 1080x1920, its body 4.26% against 1.35% — but the frame is not
what a viewer measures type against, and the control nobody ran says so.

### Finding 1 — the type is not smaller on screen; it is pixel-identical

The same card, rendered at the film's canvas and at the vertical one, ink
extent measured off both rasters with `magick`:

| canvas | ink box | on a 1080-wide phone |
|---|---|---|
| 1920x816 | 1359x432 at (141,151) | x0.5625 → **765x243** |
| 1080x1920 | 765x243 at (79,85) | x1 → **765x243** |

A 16:9 card in a portrait feed is letterboxed to 1080 wide, so it renders its
122u title at the same 68.6px the 9:16 card does. The swap changes the type's
on-screen size by nothing at all.

What it changes is the field around it. **Ink ends at 17.1% of the vertical
frame against 71.4% of the landscape one** — 83% of the card is empty paper.
That is what read as small on the watch, and it is a composition fact, not a
scale one. The distinction is not pedantry: it decides what gets built. A
scale fix (anchor the grid to the frame's short side, or divide the type by
the aspect) makes the type bigger and leaves the stack in the top sixth, which
is the same card with a louder title.

So: a variant, as DAYDREAM concluded — but the variant's job is to **compose
into the tall frame**, and any enlargement is an editorial choice made on top
of that rather than the fix itself.

### Finding 2 — fit is not the constraint at portrait; it is the opposite of 16:9

The 16:9 blocker is that two of twelve cards refuse — a 2.35:1 receipt holds
three lines of quote where a 16:9 one holds seven. Portrait has the reverse
problem, measured through `flow_runs`' own coder against the twelve real slot
tables in `~/lucid-vertical/proj`, in the portrait grid's 3413 units:

| quote size | worst card (`receipt-scream4-2011`) | share of frame height |
|---|---|---|
| 46u (today) | 6 lines, 348u | 10.2% |
| 64u | 8 lines, 648u | 19.0% |
| 80u | 10 lines, 1010u | 29.6% |
| 96u | 11 lines, 1331u | 39.0% |

**All twelve fit at every size in that band**, including the two that refuse
at 16:9. The portrait variant is free to roughly double its type and still
have room; the constraint that shapes the 16:9 templates simply is not present
here. That is the budget the layout gets to spend, and it is why the answer is
a composition rather than a compromise.

### Finding 3 — enlarging the type walks into an unmeasured slot

`quote` is a `runs` slot, wrapped and fit-checked since § The emphasis-capable
quote slot, and that is the only slot in any of the three templates that is
measured. `title`, `note`, `date_line` and `mark` are plain escaped
substitutions. Today that is latent: at 196u a `reveal` title fits the 1640u
body box with room. Measured at the sizes a portrait variant would want:

| reveal title | 196u | 300u | 360u |
|---|---|---|---|
| `Scream VI` | 861u | 1318u | 1582u |
| `Scream 2022` | 1096u | **1677u** | **1986u** |

Past the 1640u box it overruns the margin and **`magick` exits 0**, which is
the same silent overflow `flow=True` exists to close for the quote. So the
variant cannot simply set a bigger title: **the title has to become measured
first, or the build ships the failure the quote slot was built to prevent.**
This is the finding that adds a step, and it is one nothing in the item's
description predicted.

**The table above measures the slot, and step 2 found the box belongs to the
`<text>` element** — the reveal title carries a raised asterisk and the
receipt title the year, so every row is ~185–229 units short and the drawn
line at 300u is 1957 rather than 1677. Read HISTORY.md § The measured line for
the budget, not this table.

### Finding 4 — the footer lands where the platform UI is

`foot_y = view_height - 110` is a landscape number. At 1080x1920 it puts the
wordmark 62px from the bottom edge, inside the band
`goodsometimes/branding.md`'s Shorts row reserves — *"Top-third title zone (UI
covers bottom)"*. Portrait declares its own footer position, and whether the
mark stays bottom-right at all is a call rather than a derivation.

### The design

**A variant is a file, chosen from the canvas, never a template name.**
`card_new` records `(template, slots, canvas)` and `card_reauthor` fills the
template again at the project canvas — the record's entire job is to survive
an aspect swap. If portrait were a separate template name, re-authoring after
a swap would have to rewrite the recorded template, and the record would no
longer say what the card is. So `fill_template` resolves `receipt` +
`height > width` to `receipt.portrait.svg`, and every existing record keeps
meaning what it meant.

**The grid stays 1920 units wide.** x-coordinates, margins and body widths keep
one language across both variants, `render_svg` learns nothing new, and the
portrait file is the same coordinate system with a 3413u-tall viewBox that it
actually uses. What differs is the vertical stack and the sizes, which is what
a variant is for.

**Declared geometry becomes per-variant.** `TEMPLATES[name]["slots"][slot]`
carries `x`/`y`/`width`/`size`/`line_height`/`footer_slot` for the wrap
measurement, and those numbers are the landscape file's. A portrait file with
landscape declarations measures the wrap against the wrong box, which is
finding 3 arriving by a second route. The existing test that measures declared
against the file runs for both.

### Build order

1. **Variant resolution, with no variant files yet.** `fill_template` picks a
   file and a geometry table from the canvas; landscape resolves to today's
   file and today's numbers. **The step's test is that every existing card
   renders byte-identical**, which is what makes the mechanism safe to put
   under the twelve records before any of them move.

   **Shipped 2026-08-11.** 60 card/canvas pairs — the twelve real slot tables
   plus each template, at four canvases — hashed against the pre-change tree
   and identical, the two known 16:9 refusals included. `ops` needed no change,
   which is the sign the record survived the mechanism. One guard the note did
   not ask for and step 3 would have hit: a variant *file* the manifest does
   not declare is refused, because otherwise authoring one and forgetting the
   declaration leaves every portrait canvas quietly filling the landscape file.
   HISTORY.md § Variant resolution.
2. **Measure the title.** `title` and `note` fit-checked, refusing rather than
   overrunning, at both aspects. Ships a refusal on cards nobody has drawn yet
   and closes a hole that is live today at exit 0.

   **Shipped 2026-08-11**, over five slots rather than two — `date_line`,
   `year` and `mark` fail the same way and the marginal cost of each is a
   table entry, so the rule is the class: a placed text slot is measured or it
   is drawn inside one that is, and a test holds every template to it. The
   same 60 pairs hash identical, so nothing on disk moves. Two corrections
   from the measuring. **The box belongs to the `<text>` element, not the
   slot, and finding 3's table measured the slot** — the year is a flat 229
   units of the 1640 box and the reveal asterisk ~183, so `Scream 2022` at
   300u draws 1957 rather than 1677 and the overflow is 317 units, not 37.
   And **the tightest line on the twelve is the date line at 72% of its box**,
   not any title (worst 56%), so that is where step 3's enlargement gets
   refused first. HISTORY.md § The measured line.
3. **Author the three portrait files** — `receipt`, `reveal`, `rerate` — to
   the branding spec: title in the top third, content through the middle,
   bottom clear.

   **Shipped 2026-08-11**, and the mechanism needed nothing new — the grid
   stays 1920 wide and each file scales its own derived markup in a `<g
   transform>`. Two sizes came out **bound rather than chosen**: the
   comparison row is 1290 units at five-against-five so it tops out at 1.27x,
   and `date_line` caps at 46 while the title went to 176, which is step 2's
   own prediction arriving. HISTORY.md § The portrait cards.
4. **Re-author the twelve and watch them.** `card_reauthor` in
   `~/lucid-vertical/proj`, served as a sheet. The watch is what
   settles the type scale, not the measurement — finding 2 says the band
   80u–96u all fits, and which of it reads is Tyler's eye.

   **Built and watched 2026-08-11, on `:8798`.** What the re-author found is the
   step's real finding: **the sweep could not see the change and reported the
   project up to date**, because a variant shipping changes what a canvas
   draws without changing the canvas. A card record now carries the variant
   and the sweep compares it. Measured, the receipts' ink moved from ending
   15–25% down the frame to 30–60%, and the reveal footer out of the bottom
   20% band (96.9% → 78.5%), which is finding 4 closing.

### The two calls this note deferred — both settled 2026-08-11

**The bottom fifth is right.** 384px of 1920 clears every published overlay
(TikTok organic ~324, in-feed ads ~370, Reels ~320, Shorts ~300), and
`foot_margin` 740 puts the footer baseline at 78.4%, 31px above the line.

**The wordmark question was the wrong one.** The covered edge is the *right*
one: the action rail wants 180–300px below the halfway line against a 79px
margin, so `mark` at `x=1780` sat under the like button. Portrait draws it
bottom left. Both numbers live in `BASE_GEOMETRY`'s comment rather than in a
reviewer's head.

**And the mark that goes in that corner was one proofcut could not draw.** The
brand's is two-tone — an amber asterisk on ink letters — against a line slot
with one `fill` and the body face. Line slots now take the flowing slots' own
`[em]` vocabulary and `mark` declares `title_font`, so `G[em]*[/em]` is
authored in the brand's document and proofcut learns nothing about the brand.
HISTORY.md § The brand mark on a line slot has the three properties that kept
it additive.

### What the watch found that no measurement did

Every budget in this note passed and the reveal still read wrong: its year was
drawn in the footer, **the only ink in the 724px between the note and the
bottom margin** — a cluster and an orphan, where the receipt beside it is a
cluster and a margin. Both leave half the frame empty; only one puts something
in the middle of it.

So the counter-example to every measured guard this item added: **a layout is
judged on where the ink clusters, and a slot fitting its box says nothing
about that.** A portrait template gets watched before it is called done.
HISTORY.md § The orphaned year.

## Tail time — the design note — 2026-08-12

The wiki's first lucid row: the essay's end card and the teaser's bumper both
exist only as ffmpeg passes over finished renders, in no project. **The
reported loss is not that they are missing — it is that they cannot survive a
re-cut.** A bumper applied downstream of `export` is dropped by every
derivation at exit 0, and `status`, `verify` and `check_frames` are all silent
about it, because nothing in the project ever knew (HISTORY.md § The bumper the
teaser never had). Both proofs are built and both were watched, so the
editorial question PLAN.md § Parked wanted answered on a watch is answered: the
essay gets bumper A's register at 16:9, mark only, 6s; the teaser keeps B.

### The premise this item carries is wrong

Both HISTORY notes conclude the same thing: *"the honest shape is a cue
addressed by **source time** instead of word index"*, touching
`cue_add`/`cue_rm`/`cue_ls`/`_cue_echo`/`build_shots`, both clients, the cue
lane and their tests. Read against the code rather than reasoned about, that is
answering a question tail time does not ask.

**A cue addresses a moment inside the film. A tail is not inside the film.**
`build_shots` resolves every cue through `edit.timeline_span` — it needs the
cue's word to *survive into the timeline*, because a shot's start is a
timeline frame. To address six seconds of card that way, the timeline has to
already contain six seconds to hang the address on. That is why § The end card
had to append real silence to `media/vo.wav` and raise the clip's registered
duration first: **the cue change is not the mechanism, it is the second half of
one.** And the first half does not exist for the teaser at all, which ends on
live VO at −9.9 dB with no silence to append — so the harder of the two cases
needs material the source never had, which is `vo_extend`, which is parked for
reasons that have not moved.

So the cue route costs two mechanisms, and delivers one of the two cases.

### What the writer already accepts

`mlt.document` takes `audio` (the `Edit`) and `picture` (the cue lane), refuses
a picture lane that does not cover the audio track exactly, and knows how to
hold a still: `Entry(resource, 0, frames, is_image=True, has_video=True)` is
what a `card:` cue already becomes. A tail is **two ordinary entries** — the
card on the picture lane, and something on the audio track of the same length.
There is no silence producer today, but there does not need to be one: a silent
wav is a rendered asset exactly the way a card is a rendered png, so the tail
introduces **no new MLT concept at all**. The lane-covers-the-track invariant
that would otherwise refuse it is satisfied by construction.

### Three shapes, costed

| | what it touches | cases it covers | derivation inherits it |
|---|---|---|---|
| **A. cue by source time** | cue model, both clients, cue lane, tests, + appended silence per project, + `vo_extend` for the teaser | essay only, until `vo_extend` | yes |
| **B. project-level `tail`** | manifest (one optional key), `export`, `check_frames`, `timeline_status`, `reel` | both | yes |
| **C. leave it downstream** | nothing | both, by hand | **no — this is the reported defect** |

**B is the recommendation.** One optional manifest key —
`{"asset": "card:outro", "seconds": 6.0, "fade": 0.167}` — read by `export`
into two entries after the last frame. `Edit` never changes, so the subtractive
invariant is untouched and `vo_extend` stays parked and stays irrelevant. The
teaser and the essay become the same case. And the property PLAN.md § The
property everything below defends exists to protect is not in danger here,
which is the part worth stating plainly: **a tail carries a length, and that is
safe precisely because it is anchored to the end.** The music bed's lengths
were invalidated by an append because they were pinned to absolute positions in
a runtime; "after the last frame" moves with every cut by construction. A
length is only dangerous when something upstream of it can move.

### What B actually costs, stated rather than waved at

**The `Edit` stops describing the whole output.** Today `autoeditor.frame_total`
is the single answer to "how long is this", and four things read it —
`check_frames`, `timeline_status`, the web UI's lanes, and `export`'s own
document check. Adding frames outside it means either every caller learns about
the tail, or one helper answers "frames including tail" and every caller moves
to it. It has to be the second: a duration answered two ways is how a render
disagrees with its own timeline while both report clean, which is the failure
`check_frames` exists to catch and would now be able to cause.

> **The list of four was wrong, found on the build, 2026-08-13.** `status`
> did not read `frame_total` at all — it answered in seconds off `edit.duration`
> — and the web UI's picture lane is drawn from `build_shots`, which is the
> film-only projection and not a `frame_total` reader. So the count was two,
> not four. The conclusion survives intact and `status` now genuinely reads the
> helper; what the miscount cost is nothing, and what it shows is that the note
> enumerated readers by reasoning rather than by grep. **`build_shots` stays on
> plain `frame_total` deliberately**: a cue's word has to survive into the
> timeline and a tail is never inside it, so teaching the projection about a
> tail would answer a question the tail does not ask.

Three smaller ones, each a decision rather than work:

- **`verify` is unaffected and should stay that way.** It diffs the render's
  own transcription against the timeline's words; silence adds no words. A
  tail that ever carries *audio* breaks that, so the key takes a card and a
  duration and deliberately not a media clip.
- **`reel` must name what it does with a tail**, the way it already names
  pruned cues (`cues_dropped`). A teaser derived from a film should not
  silently inherit the film's end card — bumper B and card A are different
  register — but it must not silently drop one either, which is the exact
  defect this item is here to fix. Proposal: carry nothing, report `tail_dropped`.
- **The frame arithmetic has a known trap already measured**: `xfade` at
  `offset = DUR - FADE` finishes the transition at `DUR`, so the hold passed
  in is the time the card is alone, and adding the fade to it runs the tail
  long by exactly the fade (HISTORY.md § The bumper the teaser never had, four
  frames). Whatever B emits gets checked by frame readback against
  `frame_total + tail`, not by reading the filter graph.

### What this note does not settle

Two calls, both Tyler's, neither of which more building answers:

1. **Does the essay's project get a tail at all, or does the card stay a
   finishing pass?** The film is built and awaiting a watch; adding project
   state to it now means re-rendering it. The teaser is the case with the
   demonstrated loss.

   **Taken 2026-08-13: it gets a tail.** `card:outro` at 6s, and the film
   re-rendered from it. HISTORY.md § The essay's end card went into the
   project.
2. **Should a derivation inherit a tail** — never (report and drop, above), or
   by asset with a register check? Never is the conservative answer and the one
   that cannot be silently wrong.

   **Taken 2026-08-12: never.** A derivation carries nothing and reports
   `tail_dropped`, the way it already reports `cues_dropped` — the answer that
   cannot be silently wrong.

Both calls are now taken, so this note settles nothing further; what it costs
and what it refuses stand as written above.

## `vo_extend` — the design note — 2026-08-13

Queue item *`vo_extend`* is authorized and is the one item that bends `Edit`'s
subtractive invariant, so it gets a note before code. Read against the code
rather than reasoned about, the item is **narrower than its name, safer in the
place everyone expected trouble, and unsafe in a place nobody has mentioned.**

### It is one case, not two

The queue item names two cases: "the Billy/Stu hold and manufactured mid-film
silence". There is no second case. Every other mention of Billy/Stu — § What
stays blocked, and it is not lucid; HISTORY.md § `speech_overlap` — describes
exactly this: *open a gap in the VO and let the film's line play in it*. Silence
manufactured in the middle of the film **is** the Billy/Stu hold, and the note
plans one feature.

Concretely, from the measurement that blocked it: the clip's own line runs
105.35–109.15s against the VO's thesis sentence at 105.97–109.85s — 74–85% of
the clip's runtime overlapping VO speech, with the only clean seams 0.5–0.62s
wide, every one narrower than a single word. So the need is a real silence gap
of roughly the line's own length at roughly 106–110s. How long exactly is an
editorial call on a watch, not a number to derive from code.

### The name points at the case tail time already solved

The item is named for goodsometimes' `scripts/vo_extend.py`, and that script
**appends and prepends at the two outer edges of a playlist and has no
interior-insertion mode at all** — it refuses outright when the head would
overlap real audio. Its whole subject is head and tail time, which § Tail time,
shape B routes around entirely by putting the tail *downstream* of `Edit`.

So the namesake covers the case that no longer needs it, and there is **no
reference implementation for the case that remains, on either side of the
fence.** Tail time shipping changes `vo_extend`'s cost not at all; the two are
orthogonal by construction.

### The shape is forced, and it is forced physically

**A. Widen the VO clip's own segment past its registered duration.** This is the
shape that sounds cheap, and it is not merely disallowed — it is *unreadable*.
`_build_mlt` turns every segment into an `mlt.Entry` whose resource is a real
file, with `src_in`/`frames` read straight off `autoeditor.frame_layout`. A
segment naming seconds past the recording asks melt and ffmpeg to decode frames
the file does not contain, which is the silent-wrong-output class this repo
already knows from the kdenlive tail frame and auto-editor's 720x576 degrade.
`clips[].duration` is also written exactly once, by `media.import_media` from
ffprobe, and **nothing in this codebase has ever updated it after import.**

**B. Splice a real generated silence clip between two of the VO's segments.**
The manufactured material is its own registered clip, backed by a real file,
imported like any other asset. Shape A is not a cheaper B; it is a broken one.

### Where the read paths were expected to break, and don't

The premise this item has carried is that `Edit`'s addressing would need
rework. It does not. `timeline_time`, `timeline_span`, `timeline_spans`,
`source_at`, `covers`, `source_spans` and `gaps` are all clip-filtering,
offset-accumulating walks over `self.segments` — mechanically already tolerant
of more than one `clip_id` interleaved in the list. `_word_placements`,
`_seams`, `_reel_suspect_edges`, `cut_by_time`, `speech_overlap`,
`_caption_cues` and `frame_layout` inherit that and need no change. A silence
clip with no transcript is simply absent from the transcripts dict, not an
error.

**Word indices are safe under growth in a way they are not under a cut.** A cue
is `(clip_id, word_index)` resolved through the immutable transcript, and an
insertion adds no words to it — so no cue's address changes meaning, and none
is orphaned. Orphaning is strictly a consequence of shrinking. Every cue after
the insertion repositions for free, because `timeline_span` recomputes its
offset by walking every segment including the new one. This is precisely what
§ The property everything below defends is for, and it holds under growth as
cleanly as under cuts.

Three things do break, and only one of them is in the addressing:

1. **`restore` refuses across the hold.** It requires a clip's own segments to
   be one contiguous run and raises `TimelineError` otherwise — which is what a
   foreign `clip_id` between two VO segments produces, by construction. Its
   docstring already names this exact future. So `restore`, its CLI, its tool
   and its web button stop working on the VO for any range straddling the hold,
   the day this lands.
2. **Export switches to `melt` permanently and one-way.** `_is_layered` routes
   to the MLT writer whenever the timeline holds more than one `clip_id`, so a
   project that has ever been extended never returns to the auto-editor path,
   even for a render that would otherwise be single-source. That is a real
   architectural cost rather than a bug — the machinery exists and is built for
   this — but `vo_extend` rides on the layered timeline and cannot ship before
   it.
3. **`timeline_view`'s default clip is the first segment's.** A mid-film hold
   leaves the VO at segment 0 and is unaffected. A *head* insertion would not
   be, which is one more reason the outer edges belong to tail time.

### The unsafe place nobody has mentioned

**No cue can address the manufactured hold, because a cue resolves through a
word and the hold has none** — the same finding § The end card made for tail
silence, now true in the interior. But the interior version fails differently,
and worse.

`build_shots` runs every shot from its cue's frame to the next cue's. So
whichever picture was already playing simply **auto-extends across the hold**.
The projection does not refuse. It succeeds, renders, and shows stale picture
over the manufactured silence — and `shots_error`, `pins_error`, `verify` and
`check_frames` are all silent, because nothing was orphaned and nothing went
missing. A hold inserted to let the film's own line play would, by default,
play it under a frozen picture with every check clean.

That is a silent success, which is the failure shape this repo is least able to
see, and it is the reason this item needs the design note rather than just the
mechanism. **Whatever `vo_extend` becomes, it reports what picture covers the
span it opened** — the way `reel` names `cues_dropped` and a derivation reports
`tail_dropped`. The insertion is the easy half.

### The line that must not move

`vo_extend` is authorized to bend the subtractive invariant. It is **not**
authorized to touch § The property everything below defends. There is a third
shape nobody has proposed and somebody eventually will — splice the silence
into the underlying recording file — and it must be refused by name: it shifts
every subsequent word's absolute source timestamp, renumbering the address
space every cue in the project is written against. The rule: **the manufactured
stretch adds source alongside the recording and never moves a byte of its
interior.** That is also exactly what shape B does for free, which is the last
argument for it.

### What this note does not settle

1. **Is the hold worth it at all?** The case is editorial, decided on a watch.
   The two outs on record are unchanged: re-record the VO with a real pause, or
   build this. The film shipped around it.
2. **Does `restore` grow a two-clip contiguity rule, or refuse on an extended
   timeline?** Refusing is the answer that cannot be silently wrong, and it is
   also the answer that makes an extend one-way.

Not started. What this note changes about the item's cost: the addressing work
everyone expected is not there, and a reporting obligation nobody had costed
is.

## The A2 music lane — the design note — 2026-08-17

docs/plans/STUDIO.md § Step 05: the only step in the reshape that touches the model, so
it stops here rather than becoming code. Everything below was measured, not
reasoned about — a hand-built two-audio-track MLT document, rendered through
the real `melt` (resolved via `picture.melt_command()`/`display_env()`, staged
under `~/proofcut-work/spikes/a2-probe`, never `/tmp`), and verified by reading raw PCM back
out rather than trusting `ffmpeg -ss` — which, on this box, was caught
misplacing an output seek on a WAV (a region confirmed silent by direct sample
inspection read back as −30.9 dB through `-ss 2.5 -t 0.5 -af astats`; a
Goertzel filter over `wave`-module samples was used for every number below
instead). Full script and renders: `~/proofcut-work/spikes/a2-probe/{build_doc.py,
analyze.py, goertzel.py}`.

### Render: no new writer concept, and the two failure modes that could have made that false did not

`mlt.py`'s own docstring already named the shape: *"Unmuting a shot ... needs
a producer of its own and a second `mix` transition, since `audio_index` is a
producer property and not a per-entry one"* (`mlt.py:53–56`). A2 is exactly
that, generalized from "a shot's audio" to "a second track": one more
per-role node dict (`music_nodes`, same shape as `audio_nodes`/`picture_nodes`),
one more playlist pair, one more per-lane `tractor`, one more entry in the
sequence's `stack`, and one more `mix` transition — `a_track="0"` (the black
background — mix does not care that it carries no sound; every mix in the
document composites against it) — `b_track` set to the new track's index.
Built and rendered exactly this way (`case1_equal.mlt`): both tones survive at
their **exact source amplitudes** — a Goertzel probe at the render's voice
band reads −27.10 dBFS-equiv against a pure-voice control's −27.10; the music
band reads −33.12 against a pure-music control's −33.12; each track probed at
the *other's* frequency sits at noise floor (≈−50 to −53 dB). `sum="1"` mixing
is additive and lossless — nothing here needs a gain filter to avoid clipping
at these levels, and if it ever did, that is one more property on the node,
not a new service. `declared_frames()` on the built document agrees at every
site when A2's own tractor is built to span the timeline exactly, the same
discipline `tractor0`/`tractor1` already hold to.

**Two things that could have made "no new concept" false, tested rather than
assumed:**

1. **A shorter A2 than the timeline.** Built at 90 frames of music against a
   180-frame (voice) timeline (`case2_short.mlt`, `case3_marker.mlt` with a
   distinguishable silence marker to catch a hidden loop). Render duration
   stayed the full 180 frames, and the tail — the 90 frames past where music's
   own content ends — measured at **true digital silence** (Goertzel power
   `−323 dBFS-equiv`, i.e. exactly zero, not a decayed ring or a repeated
   loop). melt does not truncate the render to the short track, and it does
   not loop it to fill the gap — it pads with silence, correctly, at exit 0.
2. **A longer A2 than the timeline.** Built at 180 frames of music against a
   90-frame (voice) timeline (`case4_longer.mlt`). The render came back at
   exactly 90 frames (3.0s) with both tones present and correct for the whole
   render — the excess 90 frames of music were dropped, not appended. This is
   the finding worth being explicit about, because it looks like it should
   contradict the module docstring's own warning that *"melt renders to the
   longest declared length in the document."* It doesn't: that warning is
   about the four **top-level, mutually-independent** slots `document()`
   already controls and cross-checks (`producer0`'s `length`, each lane
   tractor's own `out`, the sequence tractor's `out`) — not about an arbitrary
   child track referenced inside a `<track producer="…">` of a shorter
   enclosing tractor. A nested track's own declared length is irrelevant to
   how long it plays; the enclosing tractor clips it. Both directions are safe
   at the render level.

**They are not safe at the write-time check level, unmodified**, which is the
one real finding here. `declared_frames()` as written scans *every* tractor's
`out` and `document()` refuses any that disagree with the timeline total. A2's
own tractor, honestly declaring its own (shorter or longer) length, would trip
that refusal even though the render it would have produced is provably
correct. Two ways to close that gap, and only one is free:

- **(a) Special-case A2's tractor in the check** — teach `declared_frames()`
  or its caller that one more site is allowed to disagree. This is a standing
  exception living forever next to a rule whose entire value is having no
  exceptions.
- **(b) Pad or trim A2 to the timeline's exact length by construction**, the
  same way the tail already appends a real silent-WAV entry rather than
  relying on melt's un-enforced blank-padding. A trailing silent `Entry`
  closes a short cue to the timeline's length; an over-long asset is trimmed
  by frame count before it is written. Every declared length keeps agreeing
  uniformly and `document()`'s existing check needs **zero** changes.

**(b), for the reason the tail already established: pad, never rely on an
un-checked melt behavior just because it happens to be safe today.** The
finding that melt pads safely is worth having measured — it means a padding
bug in the writer degrades to "the padding is redundant," not to a broken
render — but it is not a reason to skip padding.

**An offset (music starting partway in) is not a `<blank>`.** `mlt.py`'s own
docstring carves out exactly one deliberate `<blank>` — a split pane's overlay
track — and calls every other occurrence of one a trap: a lane silently
running short, invisible to every downstream cue position. A pre-roll is
instead a **real silent producer entry**, first in A2's playlist, ahead of the
music entry — the same shape `vo_extend`'s manufactured stretch and the tail's
own silent WAV already use. Built and rendered (`case5_offset.mlt`, 2s
silence + 3s music against a 6s timeline): silence measured exactly through
2.0s, tone measured exactly from 2.0s to 5.0s, silence again 5.0–6.0s (the
short-track padding from finding 1, composing cleanly with the offset). Frame-
accurate, no artifact at either seam, voice track undisturbed throughout.

### Addressing: word-index start, derived duration, argued against two alternatives

The cautionary prior is not abstract — it was measured. § The property
everything below defends: cues carrying explicit lengths tuned to a runtime
were invalidated wholesale by a ~12s append. HISTORY.md § The music bed,
measured against a dumb control ran the actual A/B this design note would
otherwise have to reason about from scratch: a machine-anchored wrap (never a
stored length) against a hand-typed one (a stored length, typed carefully, by
someone trying). **The hand-typed length undershot the real remainder by
0.341s and landed its splice on live material (−46.3 dB max at the seam);
the anchored version landed in genuine hush (−91.0 dB max).** That is the
failure shape a stored duration produces even when nobody is being careless
about it. A follow-up listen (HISTORY.md § The music bed: the loop lost, and
the reason is not length) rejected a *looping* arrangement in favor of a
single pass that plays out once — "hold," in that note's own word — which is
the shape recommended below, not a new one.

**Recommended mechanism:** a music cue is `(asset, word_index_start,
word_index_end | None)` — addressed into the transcript exactly like `cue_add`
already addresses picture, through `Edit.timeline_span`. **No field in it is a
timeline second or a frame count.** Duration is derived at build time, the
same way a picture shot's length is derived from one cue to the next in
`build_shots`, never stored: it runs from the resolved start to the resolved
end cue if one is given, or **to the end of the timeline** if not — the
"hold" HISTORY.md already settled on, made the default rather than a special
case. A cut anywhere before either boundary moves both automatically, because
both are word indices and word indices are what survives a cut for free
(§ The property everything below defends's own worked example: a 4.4s shift
recomputed a 37-shot plan from two `lucid cut` commands, no replanning).

**Argued against two alternatives, not one:**

1. **Store an explicit stop time or duration in seconds** (shape A of the
   cautionary prior, and the literal control arm HISTORY.md already measured
   losing). Refuted by measurement above, not by the general rule alone — a
   0.341s drift and a live-material splice are what "tuned carefully by hand"
   produces at this scale, and nothing about proofcut’s tooling improves that; the
   number moves because the runtime it was tuned to moves.
2. **Anchor to a word index but store the *derived* frame count as a cache**,
   refreshed by some hook on cut. This was seriously considered because
   `TAIL_KEY`'s own `seconds` field is a stored duration and it is safe — but
   it is safe for a specific, narrow reason the "Tail time" note states
   plainly: *"a length is only dangerous when something upstream of it can
   move,"* and a tail is anchored to **the end**, which nothing is ever
   upstream of. A2's start (and its optional end) sit **inside** the film,
   where an earlier cut is always upstream of them. A cached frame count next
   to a word-index cue is two facts that can disagree, and the only thing
   keeping them in step is a hook nobody has forgotten to call yet — the
   exact shape `caption_style`/`covered_by`/every other derived-and-reported
   field in this repo refuses to trust. Deriving it live, every time, off the
   same `Edit.timeline_span` every other cue already goes through, costs one
   more `build_shots`-shaped function and removes the hook entirely.

### Ducking: out of scope, and it is the recording's fault, not the model's

HISTORY.md § `speech_overlap`, the ducking prerequisite Billy/Stu never had:
the actual clip/VO pairing measured **74–85% overlap with only sub-second
clean seams** (the longest, 0.620s, "none of them a real insertion point").
There is no seam in that recording for a duck to open into — PLAN.md § What
stays blocked, and it is not lucid already names this as a property of *the
v1 recording*, fixed only by a re-record or by `vo_extend` opening a hold,
neither of which A2 changes. A2 gives proofcut a second audio track to mix a bed
into; it does not give a VO take a pause it does not have. Building a ducking
mechanism now would be solving a problem the model does not have and the
recording does — nothing here changes that math, so it stays out.

### The gate: A2 draws only when `export` can render it, restated against `_is_layered`'s own four triggers

The standing rule (`CLAUDE.md`, "Timeline lanes are projections of one
`Edit`... never draw a lane `export` cannot produce") exists because the
single-source path silently degrades: auto-editor 31.x renders a multi-`src`
timeline at 720×576 with **exit 0** rather than failing (CLAUDE.md; HISTORY.md
§ The multi-track costing spike). `_is_layered` (`ops.py:7254–7270`) is the
switch — currently four triggers, all routing to the MLT writer because
auto-editor has no export path for any of them: more than one `clip_id` on
the edit, a cue table, a `CANVAS_KEY` override, a `TAIL_KEY`. **A music cue is
a fifth**, on exactly the same footing as the fourth: `manifest.get(TAIL_KEY)`
becomes `manifest.get(TAIL_KEY) or manifest.get(MUSIC_KEY)` (or however the
cue table itself is namespaced), because auto-editor's export has no more
concept of a second audio track than it has of a card-and-silence tail.

**The failure this specifically guards against**, named rather than left
implicit: a project with a music cue recorded in the manifest but *not yet*
wired into `_is_layered`'s check would still be single-source-eligible by
every existing test, still export through auto-editor, and the render would
come back with no music in it — at exit 0, with `status`/`verify`/
`check_frames` all silent, because none of them know to look for a lane that
was never on the write path to begin with. That is indistinguishable from
success on every check this repo has except listening to the file, which is
exactly the shape `check_frames`/`verify`/the tail's own history exist to
prevent. So the web UI's A2 lane, when it is drawn, is gated the same way the
picture lane already is (CLAUDE.md: *"drawn as of step 6, and only because
step 5 made `export` able to render it"*) — it appears only once `_is_layered`
reports `True` **because of** the music cue specifically, never speculatively
ahead of the writer.

### What `Edit` is afterwards: beside it, on the `TAIL_KEY` precedent

**Recommend beside, not in.** `Edit` is one subtractive list of source
segments over one addressing space (`CLAUDE.md`: *"`Edit` never stored what it
removed"*), and every mutator on it — `remove`/`keep_only`/`restore`/`insert`
— answers a question about *what survived a cut*. A2 answers a different
question: what plays alongside whatever survived. Folding it into `Edit` would
mean either a second `clip_id` axis inside one segment (this repo's segment
model does not have parallel tracks, and `_is_layered`'s own existence is the
tell — two simultaneous `clip_id`s already force the MLT writer specifically
*because* `Edit` cannot represent them as one sequence) or a parallel list
inside the same dataclass that every one of `Edit`'s four mutators would now
have to know about and not accidentally corrupt — the exact `_SpanIndex`
staleness risk `CLAUDE.md` already flags for a much smaller change ("assigning
`segments` drops the cached index... a caller mutating the list in place is
the one way round the guard").

Project state, like `TAIL_KEY`, is the shape that already exists for
"something that isn't a cut but has to survive one, expressed and re-derived
against the current `Edit` rather than stored as part of it." Both are
additive-optional manifest keys, no schema bump, on the same precedent
`CANVAS_KEY`/`TAIL_KEY`/a window's `interp` already set. And `status`/
`check_frames` already stopped reading `edit.duration` alone once `TAIL_KEY`
needed a second answer to "how long is this" (`_frame_total_with_tail`) — a
music cue does not even add a second *duration* (the timeline's own length is
unchanged by an audio bed inside it), so it costs nothing there. What it does
cost, matching § What this note does not settle below, is one more thing
`reel` has to decide about on derivation, on the `tail_dropped`/`cues_dropped`
precedent — a survivable cost, not a new kind of one.

### What this note does not settle

1. **What `reel` does with a music cue that survives a prune vs one that
   does not.** `tail_dropped` and `cues_dropped` are the two existing
   precedents; a music cue anchored to a word index that gets cut is the same
   shape as any other orphaned cue (`CLAUDE.md`'s `build_shots` refusal
   rule) and should report the same way. Not designed here because it is a
   restatement of an existing pattern, not a new one — flagged so the build
   step doesn't skip it.
2. **Where the cue table lives** — the same `cues` manifest key `_is_layered`
   already checks, tagged by kind (`picture` vs `music`), or a second key.
   Either is additive-optional; picking one is an implementation detail of
   the build step, not a modeling question this note needs to answer.
3. **Fade in/out at the cue's own boundaries** (the tail already has
   `fade`). Likely the same shape, not measured here — out of scope for a
   note whose job was the addressing and render questions, not the full
   feature.
   - **Settled and built 2026-08-18, by measurement — and "likely the same
     shape" was wrong in the detail that renders**: a `volume` filter's
     `level` keyframes are dB, not gain factors, and entry-relative, so the
     fades ride the bed's own entry and end where the music audibly ends.
     HISTORY.md § The A2 fades and the lane, drawn.

**Approved and built 2026-08-18** — to the five-step order below, verified
against a real melt render read back by the probe's own Goertzel scripts;
the two calls the build had to settle beyond this note (the cue stores its
addressing `clip_id`; an unbounded bed ends where the `Edit` does) and the
`reel` handling it took early are in HISTORY.md § The A2 music lane, built.
The lane became **settable from the window** the same day — `POST /api/music`,
a drag or a click on A2, and the cue toolbar's own "Music bed" verb for the
first bed, which has no lane to drag on yet: HISTORY.md § The A2 lane became
settable.
What this note settles: the render side needs nothing new that
isn't already named in `mlt.py`'s own docstring, verified rather than assumed,
including the two directions (short/long) that could have made that false;
the addressing side is word-index-start-plus-optional-word-index-end with
derived duration, argued against both a stored-length shape (measured losing)
and a cached-derived-length shape (unsafe for the same reason a tail's cached
length is safe — proximity to something that can move); ducking stays blocked
on the recording; the gate is `_is_layered`'s existing fifth trigger; and A2
lives beside `Edit`, not in it.

### What would have to be true to start

The smallest set, in build order — each item is what the next one needs, not
an independent checklist:

1. **`MUSIC_KEY` on the manifest**, validated on every read the way
   `_stored_tail` validates `TAIL_KEY` — additive-optional, no schema bump,
   holding `{"asset", "word_index_start", "word_index_end": int | None,
   "fade_in", "fade_out"}`. Nothing downstream has anything to resolve until
   this exists.
2. **A resolver from that cue to a frame span**, the `build_shots`-shaped
   function this note argues for over a cached field — `word_index_start`/
   `word_index_end` through `Edit.timeline_span`, "no end" meaning "to the
   end of the timeline." This is the one piece with no direct precedent to
   copy verbatim (the tail computes a span from a fixed end; this computes
   one from two resolved cues), so it is where a build session should expect
   to spend its own measurement time.
3. **`mlt.py`: the second per-role node/playlist/tractor/`mix` shape**,
   built exactly as measured in `~/proofcut-work/spikes/a2-probe/build_doc.py` — including
   the pad/trim step this note recommends (§ Render, resolution (b)) so
   `declared_frames()` needs no special case. Depends on (2) to know what
   frame span to build the entry at.
4. **`_is_layered`'s fifth trigger**: `manifest.get(MUSIC_KEY)` added beside
   the existing four, so a project with a music cue routes to the MLT writer
   automatically rather than silently degrading through auto-editor. This is
   the gate — it must land in the same change as (3), never after it, or
   there is a window where a music cue exists but exports silently without it.
5. **`timeline_view`'s `music` projection**, echoing the resolved span (or a
   `music_error`) so the web UI's future A2 lane has something to gate on,
   on the picture lane's own precedent — the lane is drawn only once this
   exists and only because (3) made `export` able to render it.

Fade curves, `reel`'s handling of a pruned music cue, and where the cue table
itself lives (§ What this note does not settle, items 1–3) are none of them
blocking — each can land after (1)–(4) render a plain bed correctly.

## The co-hosted recording — the design note — 2026-08-18

§ Parked's *everything one video couldn't establish* lists one speaker as the
first thing the Scream video could not test, and § The completion queue's
October scale spike named the shape of the answer: *"the two streams are a new
`Edit` primitive, not a parameter"* (HISTORY.md § The scale spike, half-run).
The October Horror Bracket is where that gets tested — co-hosted with Natalie,
four or five parts, part 1 publishing Oct 1, with the live-vs-recorded call
wanted late August and the rest of the format mid-September
(`goodsometimes/ideas/october-horror-bracket-2026.md`). This note is written
now because **the only decision here that cannot be taken later is what the
recording is**, and it is taken by a setting in OBS before anyone presses
record.

Everything below was measured today against real material and a controlled
fixture, not reasoned from the spike's summary. Scripts, renders and result
JSON: `~/proofcut-work/archive/spikes/cohost-spike/{fixture,fixture2,fixture3,fixture4,fixture5,
goertzel}.py`.

### Verify first — the spike's four points, re-read at 0.9.0

All four hold, unchanged:

1. `media.probe()` still takes `next((s for s in streams if codec_type ==
   "audio"), None)` — the first audio stream, and no other is recorded
   anywhere (`media.py:93`).
2. `media.import_media()` still dedups on the resolved source path and
   **returns the existing record**, so importing one container twice to reach
   its second stream is a silent no-op (`media.py:354-355`).
3. `asr.transcribe` still hands the *container* to the whisper binary, which
   picks a stream by ffmpeg's own automatic selection (`asr.py:127`).
4. `Segment` addresses one `clip_id`, and `transcript.Word` is
   `(index, text, start, end)` with no speaker field.

Two more the spike did not name, and both matter to the design:

5. **A transcript is one file per clip** — `cache/transcripts/<clip_id>.json`,
   `Project.transcript_path(clip_id)`. There is no key space for a second one
   under the same clip.
6. **The single-pass ASR path never decodes**, so there is nowhere to put a
   `-map` today. Only the *windowed* path decodes (`asr._to_mono_wav`), and
   that call is `-i <container> -vn -ac 1` with no map either — ffmpeg picks.
   Stream selection is therefore a new decode step in `transcribe`, not an
   argument threaded through an existing one.

### The premise does not survive contact with the event

The spike measured a 58m08s two-stream capture and framed the problem as two
streams. **What this event has produced, every prior time, is one mixed
track**, and what this box would record today is one mixed track:

| evidence | reads |
|---|---|
| `2021/Ep 11 …/…1 of 5.mp4` — 2999.88s, h264 1280x720, **one** AAC stereo stream at 128 kbps; `2022/Ep 18 …mp4` — 2853.78s, the same one-stream shape | both prior runs published one mixed track per part, and it is a mix, not a mic |
| the `.mp3` beside the 2021 file — one stereo stream, **2643.39s** | the podcast cut is its own edit, ~6 min shorter than the video; two deliverables per part, not one file published twice |
| OBS 32.2.1 on this box: `UseAdvanced=false`, `RecTracks=1` in both `[SimpleOutput]` and `[AdvOut]`, `RecFormat2=hybrid_mp4` | a capture started right now writes **one** track — the mics are summed before anything proofcut could see them |

So the two-stream work the spike scoped is not what October produces by
default. It is what October produces **only if someone changes a setting
first**, and that is the decision this note exists to force.

### What lucid does with a real co-hosted mixed track, today

A 120s slice from the middle of Ep 11 (`slice_1200_120s.m4a`, two people
talking at conversational pace) through `asr.transcribe` — the real
`proofcut transcribe` path, turbo, GPU idle at 1406 MiB of 12227:

- **10.53s wall, 11.39× real-time**, 410 words, 23 segments, **3
  hallucinated words** reported by the guard. Extrapolated, a 50m part costs
  ~4.4 min and a 58m one ~5.1 min. **Scale is not the problem.**
- The transcript is correct and unusable for attribution. It reads: *"…that's
  it for scares and kills I only gave her two out of four because there's not
  really any amazing kills it's more like a fun movie I would say yeah um
  really the best part is the the werewolf scene…"* — the turn changes hands
  inside that run of words and nothing in the payload marks it. Every word
  indexes one `clip_id`, which is the show.

So a mixed-track bracket part is, to proofcut, a long single-clip VO. **Cutting,
cues, captions, export and every check work on it unchanged.** What is missing
is only speaker identity — and with it per-speaker captions, "cut Natalie's
tangent", and any per-speaker mix move.

**There is no local route to speaker identity on a mixed track.** The tag venv
has torch 2.11 and openai-whisper; there is no `pyannote`, `whisperx`, `nemo`,
`speechbrain` or `faster-whisper` in any of the nine venvs under `~/projects`, and pyannote's
diarization models are gated behind an accepted licence on Hugging Face.
Adding one is a new gated dependency with a model download, on the far side of
proofcut's "no cloud, no accounts" line only in spirit — it is local at inference
time, but it is not something this repo can ship and expect to work on a fresh
checkout. It is the wrong first move for an event eight weeks out.

### If two mics are recorded: what the fixture measured

Ground truth by construction. Two disjoint 120s stretches of the film's own VO
(`~/proofcut-work/projects/final-cut/proj/media/vo.wav` at 60s and 240s) cut into alternating
6s turns, then `mic_A = turns_A + g·turns_B` and its mirror, for a bleed `g`.
**Same voice on both mics is deliberate** — the rule under test is an energy
ratio between two streams, and one voice removes a gain/timbre confound the
rule never gets to see. What it also removes is realism, and that is this
fixture's honest limit (§ What this note does not answer).

**Finding 1 — per-stream transcripts do not separate speakers, at any
isolation.** Each mic's own transcript, scored against which turn each word
falls in:

| bleed | mic A words | of the other speaker | mic B words | of the other speaker |
|---|---|---|---|---|
| −6 dB | 269 | **135** | 269 | 134 |
| −12 dB | 268 | 134 | 267 | 133 |
| −18 dB | 266 | 133 | 266 | 130 |
| −24 dB | 259 | 126 | 258 | 128 |

Half of each mic's transcript is the other person, and the share barely moves
across 18 dB of isolation. Pushing further does not fix it — **it changes the
failure mode**: at −30 dB the count falls to 61 of 185, at −36 dB to 37 of 164
**with 145 words dropped by the hallucination guard**, at −48 dB 41 of 160.
An isolated mic is a track that is silent half the time, which is whisper's
own documented trigger (CLAUDE.md § Both passes hallucinate). Transcribing
each mic and diffing them is the obvious design and it is dead.

**Finding 2 — the energy ratio does separate, and cheaply.** For each word,
compare the two mics' RMS over that word's own span and take the louder:

| bleed | mic A correct | mic B correct |
|---|---|---|
| −6 dB | 266/269 (98.9%) | 266/269 |
| −12 dB | 263/268 | 261/267 |
| −18 dB | 261/266 | 262/266 |
| −24 dB | 254/259 | 256/258 |

It costs one decode of each mic and no second ASR pass. **Transcribe once —
either mic, or the mix — and attribute.**

**Finding 3 — simultaneous speech takes it to chance, which is the whole
risk.** The turns above never overlap and real co-hosts interrupt. Rebuilt at
−12 dB bleed with each turn running into the next:

| overlap per turn | share of runtime | words clear of overlap | correct | words inside overlap | correct |
|---|---|---|---|---|---|
| 1.0s | 15.8% | 210 | **208 (99.0%)** | 72 | **47 (65%)** |
| 3.0s | 47.5% | 128 | 127 (99.2%) | 153 | 77 (50%, chance) |

**Finding 4 — the rule half-knows when it is guessing.** Recording the
margin (how many dB louder the winner is) and refusing below a floor, on the
1.0s-overlap condition:

| margin | flags, of 72 overlapped | costs, of 211 clear | accuracy on what it keeps |
|---|---|---|---|
| 0.5 dB | 3 | 2 | 91.4% |
| 2 dB | 13 | 2 | 92.5% |
| 6 dB | **32** | **2** | **95.6%** |

The asymmetry is the useful part — 6 dB flags 44% of the overlapped words and
costs 1% of the clear ones — and the limit is equally clear: 40 overlapped
words survive the floor and about half of those are wrong. **The margin is a
report, not a fix.**

**Finding 5 (negative) — an envelope-only overlap detector does not work, and
it was built before being called unfit.** Frame-level "both mics hot at once",
each mic thresholded against its own speech level: recall 0.38–0.73 at
precision 0.25–0.88, fragmenting into 107–241 spans over 120s, because speech
has gaps inside a turn. Given its fair form — `speech.merge_runs` on each mic
then `speech.intersect_runs`, this repo's own treatment — the fragmentation
goes (25–55 spans) and the precision does not: at the 15.8%-overlap condition,
recall 0.86–0.90 buys precision 0.21–0.32, i.e. **81.6s of "simultaneous" in a
clip that holds 19.0s of it**. Recorded so nobody builds it a second time.

### The render trap, measured: a two-track container plays one mic, at exit 0

Built `twostream.mkv` — h264 at stream 0, a 300 Hz tone at stream 1, a 1200 Hz
tone at stream 2 — and rendered it through the real `melt`
(`picture.melt_command()`/`display_env()`), reading the result back with a
Goertzel probe rather than trusting a level meter:

| document | 300 Hz | 1200 Hz |
|---|---|---|
| no `audio_index` (what `mlt.py` writes for the Edit lane today) | 1448 | **0.0** |
| `audio_index=0` | 1448 | 0.0 |
| `audio_index=1` | 1448 | 0.0 |
| `audio_index=2` | 0.1 | **1448** |
| controls: `-map 0:a:0` / `-map 0:a:1` off the source | 1679 / 0.1 | 0.0 / 1638 |

Two things, and the second is a trap:

1. **A dual-track capture imported and rendered today loses the second mic
   entirely, silently, at exit 0.** `mlt.py` emits `audio_index` only as `-1`
   to *silence* a picture node (`mlt.py:1052,1080,1102`); the Edit lane's own
   producer carries no property, so MLT picks, and it picks the first.
   Half the conversation would be missing from the render with `verify`,
   `check_frames` and `film_check` all clean, because every one of them
   compares the render against the timeline and the timeline never knew.
2. **`audio_index` is the container's absolute stream index, not the audio
   ordinal.** With video at 0, the first mic is `1` and the second is `2`,
   while ffmpeg's own `-map 0:a:1` means the *second* audio. Two numbering
   systems for the same choice, agreeing exactly when the file is audio-only —
   which is every fixture anyone would write first.

### The design

The smallest thing that holds every invariant. **One clip, one word-index
space** — the speaker is an attribute of a word, never a second address.

1. **`Word.speaker: str | None = None`**, additive and optional.
   `parse_whisper` carries `entry.get("speaker")` through. Absent means what
   every transcript on disk already means, so no migration and **no schema
   bump** — the `CANVAS_KEY`/`TAIL_KEY`/`caption_style` precedent. Cues,
   descriptions, unspoken marks, the music bed and `captions.place` all
   address `(clip_id, word_index)` and are untouched.
2. **`clip["audio_streams"]`** recorded at import (additive, optional; absent
   means one, which is what every older record meant). `import_media`'s path
   dedup stays exactly as it is — the second stream is *not* reached by
   importing the file twice, which is the workaround the dedup blocks and
   should keep blocking.
3. **`asr.transcribe(..., stream=k)`** decodes with `-map 0:a:k` first and
   hands whisper a WAV, `_to_mono_wav`'s shape. Needed for the *attribution*
   decode even when the transcript comes from the mix.
4. **Attribution is one pass over an existing transcript**, not a second ASR
   run: `speakers.attribute(transcript, mics, labels, margin_db=6.0)` — pure,
   stdlib, `speech.py`'s tier — returning a new transcript with `speaker` set
   and a report carrying the per-word margin, the count left `None`, and the
   ambiguous spans. It **reports and never decides** below the floor, on
   `reframe_detect`'s precedent (`apply` off by default).
5. **Playback and render mix the mics at import, not in the writer.** A
   mixdown lands beside the source the way `attenuate_noises` already does —
   `media_path()` prefers `clip["attenuated"]` today, and a `mixed` key is the
   same move — so `Edit`, `export`, `verify` and the whole writer stay
   single-stream and the `audio_index` trap above is never in the render path
   at all. Two real lanes in the writer is the A2 mechanism and is already
   proven, but it buys nothing until per-speaker *gain* becomes an edit
   operation; that is the named trigger for revisiting, not a step now.
6. **Captions are out of scope for the first build.** With `speaker` on the
   word the look question opens (a prefix, a colour per speaker, a lower
   third), and `caption_style` is where it would live. It is a second note.

What this deliberately does **not** do: no second transcript per clip, no
speaker in any address, no `Edit` primitive. The spike's phrase "a new `Edit`
primitive" was right about the shape of a *simultaneous second source* and
wrong about what a two-mic recording needs — the mics are one performance, cut
together, and the only thing that is genuinely per-word is a label.

### The format decision, which is the deliverable to the event

**Recommendation, and the reasoning is the fixture above: record two tracks.**
In OBS, Advanced output, tracks 1 and 2 enabled with one mic on each, keeping
the mixed track for the stream itself. It costs one settings change and a test
recording; it buys 99% per-word speaker attribution on clear speech, with the
ambiguity reportable. On one mixed track proofcut can do nothing at all here
today, and the only route to it is a gated ML dependency.

**If the format stays one mixed track, nothing is lost that exists now** — a
bracket part is a long single-clip VO and every op works on it, at ~4.4 min of
transcription per part. What does not happen is per-speaker anything.

**The deadline is real and asymmetric.** A setting changed before part 1 is
free; a part recorded on one track can never be separated afterwards. Late
August, with the live-vs-recorded call, is when this has to be decided.

### What this note does not answer

- **The fixture is synthetic in the way that matters most.** One voice, an
  injected bleed at a chosen level, no room, no different mic gains, and turns
  that alternate on a metronome. The rule it validates is an energy ratio, and
  the numbers should be treated as an upper bound. **The cheapest thing that
  would settle it is a five-minute two-mic test recording** of two people
  actually talking over each other — before October, not during it.
- **There is no real dual-mic recording on this box to check against.** The
  scale spike's 58m08s two-stream capture has an effectively dead second
  stream (2.2 kbps), which is why this note built a fixture rather than
  measuring that file.
- **Whether whisper's word timings are good enough for attribution at the
  boundary.** Every number above attributes over the word span whisper
  reported, and this repo's standing rule is that those durations are not to
  be trusted (CLAUDE.md § Trust a transcript's word order, never its word
  durations). An inflated duration spans a turn change; the margin report is
  what would show it, and it has not been measured against a hand-marked turn
  list.
- **Two speakers on two separate *files*** — a remote guest recorded locally,
  the third shape — is not measured here. It is the same attribution problem
  plus a sync offset, and the offset is the part that has no answer yet.

### Build order, if this is approved

1. `Word.speaker` + `parse_whisper` + `clip["audio_streams"]` at import. No
   behaviour change; every existing transcript loads unchanged.
2. `asr.transcribe(stream=)`, its CLI flag and its MCP argument, with the
   decode step.
3. `speakers.attribute` as a pure module with the fixture above as its test,
   plus `ops.attribute_speakers` (`plan`-shaped: reports, applies on request)
   and its CLI/MCP pair.
4. The import mixdown for a multi-stream container, and the refusal that
   catches the trap: **importing a container with more than one audio stream
   says so**, rather than registering it as if the first mic were the
   recording.
5. Only then, and only against a real recording: captions per speaker.

Steps 1–2 are safe to ship in one session. Step 4 is the one that prevents a
silent wrong render and could reasonably go first if a two-track recording
exists before the rest is built.

**Steps 1 and 4 shipped 2026-08-18, ahead of the format decision and
deliberately not waiting on it** — step 1 changes no behaviour and step 4
closes a wrong render that exists today either way. HISTORY.md § The two mics
survive import. Three things the build moved:

- **A third way forward was needed, and the note has only two.** `--mix`
  sums, which is right for two mics of one performance and nonsense for a
  film rip with a commentary track — so `--audio-stream k` keeps one, as a
  stream copy rather than a re-encode. Without it the refusal would have
  been a new wall in front of ordinary footage. (Measured first: all ten
  clips of `~/proofcut-work/projects/final-cut/proj` are single-audio, so nothing here was
  behind that wall — but the wild case is a rip.)
- **The trap is not only MLT's, and the auto-editor half fails in a way that
  is harder to see.** Handed the container directly, auto-editor passes
  *both* tracks through: nothing is dropped, the output holds two audio
  streams, and everything that decodes it takes the first — so the film
  plays as mic A while a stream count answers "both are there". Read by
  Goertzel power at each mic's own tone, with the first-mic import as the
  control: 300 Hz 1024.9 / 1200 Hz 1017.8 mixed, against 2042.3 / **0.0**.
- **`original_media_path` keeps the mixdown**, which the note's item 5 did
  not say and which follows from it: the untouched original of a two-mic
  container is the mixdown, and reading the container there would attenuate
  mic A alone. The same reach reaches `reel`: `_reel_media`'s key tuple is
  `media_path()`'s preference chain, and a key added to one and not the
  other hands the derived project a path with nothing at it.
- **The window is a client of this op and had to be able to comply.** The
  note is silent on the assets pane, and a refusal reachable there with no
  way out of it is the one surface whose point is that the terminal is never
  required. Both offers are drawn only after a refusal — and the *message*
  turned out to be the thing spending the pane's height, at 194px against a
  129px list.

**Step 3 shipped 2026-08-18** — `speakers.py`, `ops.attribute_speakers`,
`proofcut attribute-speakers` and the MCP tool. HISTORY.md § Speaker
attribution, built. The line below used to say steps 2, 3 and 5 had nothing
to be built against, and **step 3 was the exception this note's own build
order already named**: its test is the fixture above, which exists. Three
things the build moved:

- **The signature takes spans, not a transcript.** `attribute(spans, mics,
  margin_db=)` keeps `speakers.py` from importing `transcript`, so the rule
  is pinnable with no `Word` in sight.
- **The report separates two refusals the note treated as one.**
  `ambiguous` (mics too close to call) against `unmeasurable` (the word is
  past the end of a mic) — the second means the transcript and the mics are
  not the same recording, and merging them hides that.
- **Reaching the mics needed a resolver, because every existing one prefers
  the mixdown** — `media_path()` and `original_media_path` both, correctly.
  `media.container_path` is the one that does not, and it has exactly one
  caller.

What is still owed on this note is otherwise unchanged: **the format decision
itself** (the OBS setting, before part 1 is recorded) and the five-minute
two-mic test recording, and then steps 2 and 5, which have nothing to be
built against until one exists. The recommendation was **taken 2026-08-18 —
record two tracks** — and the OBS half of it is set: `RecTracks=3` (the
mixer bitmask, tracks 1+2) in both `[SimpleOutput]` and `[AdvOut]`. What
that does **not** do is make the two tracks two mics. This box's scene
collection holds **no audio sources at all**, so until one mic is routed to
each track in Advanced Audio Properties, a two-track capture is two copies
of the same mix — which is the failure this whole note exists to catch, in
a new place.

**And what the fixture cannot answer still gates the number.** `MARGIN_DB`
ships as a reported default rather than a pinned threshold, because one
voice with an injected bleed on a metronome is an upper bound on every
accuracy figure here.

## `vo_synth` — the design note — 2026-08-21

**What it is for.** The editor has two holes a recording cannot fill from
itself: a word `unspoken` marks as never said cleanly (a retake seam, a
fragment), and a line the script needs that was never recorded — the case
`vo_extend` opens a *silent* hold for. Both want a sentence in the narrator's
voice, spliced where the transcript says it goes. `vo_synth` is that: text in,
a clip in the voice out, placed after a word.

**Why the voice is a reference clip and not a model.** Decided by measurement
rather than by the obvious plan (local-llm `notes/voice-clone-zero-shot.md`,
rounds 1–4): on the model's own speaker encoder, zero-shot with 19 s of VO is
the closest thing to Tyler anything rendered (0.989 against real takes' 0.993),
every fine-tune is further and gets further with training, the upstream
learning rate collapses outright, and the 2026-only control shows the drift is
the recipe's, not the data's. So a voice is `ref.wav` + `ref.txt` in a
directory, `PROOFCUT_TTS_VOICE` names the default, and nothing in proofcut loads a
checkpoint that is not the stock model. A better clone, if one arrives, is a
different worker behind the same `tts.synth` contract — seeds in, candidates
with `sim` out.

**Why it renders several.** Seed moved a render more than the reference did;
the op renders `candidates` seeds in one worker process and ranks by `sim`, the
one number measured to track "sounds like him". A capped render never wins. A
readback through whisper rides every real call and is reported beside the
winner rather than used to choose — the ranking is on likeness; "did it say
the words" is a second question with its own answer.

**Why the splice is `vo_extend`'s.** The hold and the voiced line are one
mechanism — a real file registered and `Edit.insert`ed after a word, with
`covered_by` computed over the mutated edit — so `_splice_after` is shared and
`vo_extend` calls it with a silence file. What `vo_synth` adds is the order:
`_splice_point` refuses a word that is not on the timeline before the GPU is
spent, not after.

**Open.** The listening verdict on the op's own output is Tyler's and is the
wiki's Open items row. The fine-tune route is parked with its untried levers
named in HISTORY.md § `vo_synth`, built; the `instruct` lever and
reference-in-context were measured and made likeness worse. A best-of-N that
also read back every candidate and preferred WER 0 among near-equal `sim` is
the obvious next knob and is deliberately not built until a real line needs
it.

## The agent contact sheet — the design note — 2026-08-24

docs/plans/POLISH.md § Step 07. **Built as `shot_sheet` — what it turned into, and the
three things this note got wrong, are HISTORY.md § The shot sheet.** Kept for
the measurements and the reasoning, both of which held.

**The question it answers** is the oldest one still open here (§ Open
questions, *Preview delivery in tier 1*): an agent can *listen* to what it
made — `verify` reads a render back through whisper and diffs it against the
timeline — and it cannot *look* at it. That is the missing half of the
self-check loop for the product's whole thesis. A person opens the window and
watches; an agent has no window and cannot watch an MP4.

Everything below was measured on 2026-08-24 against the real film
(`~/proofcut-work/spikes/ui-polish-check/proj`, the 5:36 Scream essay, 38 shots of which
25 are video) rather than reasoned about, because the two load-bearing claims —
that an image can reach the model at all, and that a label survives being one
tile of twenty-five — are both the kind this repo has been wrong about before.

### The channel works, and that was the thing to check first

The agent panel runs `claude` with `--tools ''`, so the agent **cannot Read a
file path**: proofcut's MCP tools are the entire surface it has. The sheet has to
travel inside the tool result as image content, and two separate things had to
be true for that.

**The SDK carries it.** `mcp` 2.0.0 has `ImageContent` in its `ContentBlock`
union and `mcp.server.mcpserver.utilities.types.Image` as the helper a tool
returns. Round-tripped over a real stdio server: a tool returning
`Image(path=...)` arrives client-side as `ImageContent`, `mime_type`
`image/png`, `is_error` false. No structural work needed.

**`claude -p` puts it in front of the model**, which is the half that could
not be read off the SDK and is the half the design depends on. Measured with a
throwaway one-tool MCP server under the panel's own flags
(`--strict-mcp-config --tools ""`): asked to call the tool and read the text in
the image, the model answered `LUCID 7` — the burnt-in string, in the right
colour on the right background, with an unprompted note that nothing else was
in frame. The channel is real.

### Frames: per-shot in-points, not ±0.5 s around each cut

The recorded lean was a sheet at ±0.5 s around each cut boundary. **Measurement
argues against it**, on this film:

- The boundaries an agent can enumerate are the **VO's** cuts — 62 of them.
  The picture does not change at a VO cut unless a cue happens to land there:
  both sides of the seam are usually the same asset, at nearly the same source
  second. A 124-tile sheet of near-duplicate pairs is the expensive way to
  learn nothing.
- `timeline_view`'s `shots` is already the projection *through*
  `mlt.plan_picture` (CLAUDE.md), and every entry carries exactly what a tile
  needs to be labelled with: `asset`, `asset_path`, `start` (the timeline
  second), `src_start` (where inside the asset it reads), `is_image`. Nothing
  has to be derived. 38 shots, 25 of them video — a fifth of the tile count,
  each showing a different picture.
- The proposal survives its own evidence. Reading a 12-tile sheet of exactly
  those in-points, the model volunteered two findings without being asked for
  any: that the `cold-open` tile at `t=0.0s` is a black title card rather than
  live action (a `check_black` finding, arrived at by looking), and that two
  clips appear twice with a non-zero `src` on the second instance, so they are
  cut into two pieces. Both are true.

The cue in-points are the same set for a cued project and a strict subset
otherwise, so `shots` is the more general address. **A cut-boundary mode stays
possible and is not the default**; the argument for adding one later is a
specific question a per-shot sheet cannot answer, and none has been named yet.

### Budget and paging: about twenty-four tiles, four across

The constraint is the model's own image handling, not the file: vision
downscales anything over ~1568 px on its long edge, and a downscaled sheet is
a downscaled *label*. Measured at two tile widths, four across:

| tiles | tile width | sheet | bytes | long edge |
|---|---|---|---|---|
| 25 | 320 | 1296×1096 | 198 KiB | native |
| 25 | 384 | 1552×1313 | 261 KiB | native |

The 384 sheet was read back exactly: asked for the labels of the **bottom
four** tiles alone, the model returned all four verbatim
(`s3-reveal t=265.6s src=30.5s`, …) and counted the distinct clip names across
the whole grid correctly (9). So a page is **~24 tiles at 384 px, four
across**, which is one page for this film's whole picture track and is the
number a `page` argument should default to. An hour-long edit is several
pages, addressed by shot index — deliberately not by "give me everything",
which is how a tool result becomes a megabyte.

### Composition: `magick montage`, label on its own band

Both routes were built and timed on twelve real frames:

- **`magick montage`** — 0.95 s for twelve, one subprocess per tile plus the
  montage. `-splice` puts the label on a black band **below** the frame, so it
  never covers picture, and `-geometry +2+2` gives the grid gutters.
- **`ffmpeg` `xstack`** — 0.74 s, one call. Faster, and worse in three ways
  that matter more than 0.2 s: the layout string has to be hand-built per tile
  count (`w0+w1_h0`-style offsets, one term per column and row), `drawtext`
  burns the label **over** the picture, and with no gutters two adjacent dark
  tiles run together — visible in the probe output.

`magick` is also already this repo's rasteriser, resolved through
`graphics.magick_command()` with its coder caveat recorded. Both share the
font-substitution risk libass has (CLAUDE.md § The font named in a style may
not be installed), and the mitigation is the same one the cards use: the label
is drawn at a size where a substitute is still legible, and legibility is
settled by reading a render, which is what the measurements above did.

### Cost, and containment

**0.295 s per frame** to extract on this film (1920×816 h264 — a seek plus one
frame through `picture.extract_frame`), so a cold 25-tile sheet is ~7.4 s of
extraction plus ~1.8 s of montage. Cached, a second call is the montage alone.

Containment is `ops.thumbnail`'s exactly, and it is the point: frames resolve
through `media.media_path()` and `picture.extract_frame`, cached under
`cache/`, keyed by `(asset, source second)` the way `cache/thumbs/` already is.
**No third caller of `media.preview_path()`, ever** — a proxy is downscaled and
never enters the manifest, and a contact sheet that reached one would be
showing the agent a preview encode and calling it the film.

### What this is not

A reading is an **opinion, not a check**. `reframe_sheet` is the precedent: it
draws the evidence and a person judges it; it decides nothing and nothing
downstream reads its verdict. The same holds here — the sheet is how an agent
forms a hypothesis it must then confirm with an op that measures
(`check_black`, `check_frames`, `verify`, `film_check`). Nothing in proofcut
should ever gate on what a model said it saw.

### Open, for the review this note stops for

- **Whether the sheet takes a span.** Per-shot over the whole film is the
  simple contract; `start`/`end` seconds would let an agent look at the part it
  just changed. Adding it later is additive; adding it now is a second address
  to keep correct.
- **What else rides the label.** Clip name, timeline second and source second
  were enough for every question asked in the probe. The shot index, the cue's
  own word, and a `pinned` marker are all candidates and all cost label width,
  which is the thing that was measured to be tight.
- **The CLI half.** Parity says `proofcut contact-sheet` exists; it writes a file
  for a person rather than returning an image, the same op with two
  deliveries. Whether the MCP tool *also* returns the path (so a human can open
  what the agent looked at) is a small decision with a real answer either way.
- **Whether an agent should be able to sheet a *render* rather than the
  project.** It is the honest version of "watch what you made" and it opens
  `renders/` to a second reader, which `GET /api/output` was deliberately kept
  narrow about (§ Open questions, *Should the workspace play its own output*).
  Parked here rather than decided.

## The footage sheet — the design note — 2026-08-24

**Built as `footage_sheet` — what it turned into, and the two claims
measurement overturned, are HISTORY.md § The footage sheet.** Kept for the
reasoning, which held, and for the addressing table, which did not survive
contact with real unedited footage and is corrected there. It is the sequel to
the note above (HISTORY.md § The shot sheet); read that one first, since the
channel, the tile size, the paging and the label format are settled there and
are not re-argued here.

**The question it answers** is the one proofcut's wordless-footage users have and
its own dogfood film does not. `describe` indexes what is *visible* in a clip in
10s windows and `describe-ls` searches that text, so someone with a GoPro dump,
event coverage or gameplay — no dialogue, no subtitles, nothing for the
transcript to address — can already find a moment. What they cannot do is
**look at it**. The find is a text match; the confirm is a path they open by
hand, and for an agent under `--tools ''` there is no hand.

That gap is measured, not supposed: **against 25 human picks, the description
index agreed 2 times and the clips' own filenames 3** (§ Choosing the b-roll).
The conclusion drawn then was that a better `describe` prompt is the wrong fix
and a reader should choose. `shot_sheet` now makes a reader able to *see*. So
the same conclusion points somewhere new: let the thing that chooses look at
the candidates, instead of choosing from prose about them.

### What is already built, and what is missing

`shot_sheet` sheets **the timeline** — one tile per shot of the picture track,
addressed through the cue table. Every part of it except the address
generalises. What a footage sheet needs is a different answer to *which
instants of which clip*, and nothing else: the same frame caching (now shared
as `_sheet_frame`), the same `_sheet_tile` band, the same `graphics.montage`,
the same `[report, Image]` return.

`contact_sheet` (the first-look filmstrip, HISTORY.md § Import strips a chapter
list) is not this and does not grow into it. Its `seconds` knob does open the
window past the default ten, but it samples a clip's *head* at a fixed spacing
— a first look, not a browse — and it returns **paths**, which is exactly the
wall this note exists to get past. Widening it into a browse would mean giving
it an address, a montage and an image return, at which point it is this note's
op wearing the other one's name.

### The addressing options, measured on real clips

Three candidates. Measured on the film's own footage, 2026-08-24, whole-clip
scans at `SCENE_THRESHOLD` 0.15:

| clip | duration | scene scan | cuts ≥0.15 | 10s windows |
|---|---|---|---|---|
| `s3-reveal` | 55.0s | 1.0s | 14 | 6 |
| `s2022-reveal` | 160.1s | 3.6s | 62 | 17 |
| `cold-open` | 730.1s | 14.2s | 85 | 74 |

**Scene cuts are the wrong default, and the table is why.** Cut count tracks
how *edited* the material is, not how long it is: `s2022-reveal` is 160s with a
cut every 2.6s, `cold-open` is 730s with one every 8.6s — a 3.3x density spread
inside one film. All of this footage is cut studio material. The audience this
note is for has the opposite: long continuous takes, where a scene scan returns
few tiles or none, and `media.scene_cuts`' own docstring already says an empty
list is a real answer meaning one continuous shot. **A default that degrades to
one tile on precisely the material it was built for is the wrong default.**

**Measured 2026-08-25 on real unedited footage, and the conclusion held while
the reasoning behind it did not.** Yield is not sparse on continuous material,
it is *uncorrelated with anything a caller knows* — 0 cuts on a 29s b-roll
loop, 17 in 60s of gameplay, 38 in 1070s of screen capture. The table there
supersedes this one: HISTORY.md § The footage sheet.

**Fixed interval is the robust default** — it needs no describe run, no scan
and no cue table, and it works identically on a continuous take and a trailer.
Its cost is that it is blind to content, which the prototype below shows.

**Describe windows are the *right* unit when they exist**, because then a tile
and a description share one address — `(clip_id, src_start, src_end)` — so a
`describe-ls` hit has a picture and the sheet has text. That is the pairing
this whole note is for, and it should be the mode chosen automatically when a
clip has descriptions rather than a flag someone remembers.

### The prototype, and the two defects it found

A 24-tile evenly-spaced sheet of `s2022-reveal` (160s, so 6.7s apart), built
from `shot_sheet`'s own helpers and **read back**: 265 KiB, entirely legible,
and the whole scene's arc readable at a glance. Two real defects, both visible
only because it was looked at rather than counted:

- **A dark tile is ambiguous, and the first reading of it was wrong.** The
  `src=123.4s` tile reads as nearly black, and the note first recorded that as
  a sampling defect — an instant that landed on nothing. Measuring it says
  otherwise: **YAVG 40.7 and YMAX 137**, against 60.3 and 165 four seconds
  earlier, so it is real underexposed content and the tile is correct. The
  neighbour at 124.5s measures the same, so it is a dark *passage*, not an
  unlucky instant. What is defective is the *reading* — this is the repo's
  standing trap arriving in a new place (§ The auto-framing detector: a black
  source reads exactly like a black bar), now with a vision model as the thing
  being fooled. The fix is not to reject the sample. It is that **darkness
  must arrive as a number rather than be left to the eye**, and
  `picture.extract_frame` already returns the signalstats this sheet throws
  away: a tile that says `dark` beside its label cannot be misread as an
  empty frame.
- **A fixed interval is content-blind in both directions.** The 90.0/96.7/103.4s
  tiles are three samples of one scene and setup — much the same information
  three times — on a clip whose 62 cuts mean there was plenty else to show.
  Even spacing spends tiles on stillness and skips past fast cutting.

Both point the same way: **the interval should be the fallback, and the
content-aware address preferred where one exists.** Neither is a reason to
sheet less; they are reasons the address matters — and the first is a reason
the *label* carries more than a timestamp.

### Cost, and what it is not

Extraction dominates and it is already measured: ~0.3s a frame, ~8s for a cold
24-tile page, ~1s warm. A scene scan adds the table's own column and is the
only new cost — 14.2s on a 730s clip, which is why it cannot be the default and
must never ride a path something calls on every change (`reframe_coverage`'s
own rule, and the `frame.js` breach of it).

Containment is unchanged and non-negotiable: frames resolve through
`media.media_path()`, cache under `cache/`, never the manifest. **No third
caller of `media.preview_path()`** — a proxy is downscaled, and a footage sheet
drawn off one would be showing the agent a preview encode and calling it the
footage.

And the standing rule holds hardest here, because this sheet's whole purpose is
to inform a *choice*: **a reading is an opinion, not a check.** A tile is how
something forms a hypothesis about what footage to use. Nothing may gate on it,
and `synopsis` remains the place a human says what a clip *is* — a sheet shows
what a camera saw, which is a different fact and not a replacement for it.

### What the review settled

- **Its own op, per the lean**, because the paging unit differs. Both sheets
  share the frame cache, the tile band and the montage; only the address is new.
- **The luma went into the *shared* helper, so both sheets carry it**, which was
  the open question's own argument. What it became is not a darkness number —
  the data has no dark/normal boundary to pin one to, and it does have a clean
  one for "there is nothing here". HISTORY.md § The footage sheet.
- **A describe-addressed tile carries its window's `text` in the reply and not
  on the picture.** The band holds a line, and a description truncated to fit is
  worse than one the caller reads whole. The cost the note worried about is
  real and is what `page` is for: 74 windows of prose for `cold-open` arrives
  24 at a time.

Still open: **whether a sheet may span clips.** "Show me every clip's 30s mark"
is a real browse gesture and is a different address again.

## The completion queue — what the Scream video left — 2026-08-12

Provenance: a full review of HISTORY.md, docs/plans/DAYDREAM.md, the design notes above,
the goodsometimes pipeline, and both decision files
(`~/proofcut-work/archive/spikes/approvals/decisions.json`, `~/proofcut-work/archive/spikes/watch/decisions.json`), made the
day the essay (v8) and its teaser were declared done. The finding that frames
everything: **the editing core is complete** — the film and the teaser ran end
to end through proofcut — and the queue is exactly the set of things the
production still did by hand, each of which cost this video real time or
nearly shipped a defect. Status lives in the wiki's Open items table, never
here; this section owns the order and the reasoning, cited by name.

The approvals round already answered **build** for three of these (the music
bed, the keyframed move, `vo_extend`), **vendor** for the caption font, and
**all** for the flash in-points — those are queue items now, not open calls.
The order was adopted 2026-08-12.

1. **Vendor the caption font** — taken ("vendor"): put the faces the presets
   name where fontconfig finds them, so the default resolves on the rendering
   box rather than aspirationally. Closes the open call § Direction and order
   has carried since caption styling, in the direction that restyles nothing.
   Settled, as ever, by measuring a render — never by `fc-match`.
   - **Shipped 2026-08-13** (HISTORY.md § The caption default resolved by
     coincidence): the faces ship in the package (`src/proofcut/fonts/`) rather
     than being installed on a box, and `fonts.probe()` settles which face
     libass actually drew by burning a family against an impossible one, which
     needs no stored reference render. **This item is closed.**
2. **Tail time, shape B** (§ Tail time — the design note). Call 2 is taken —
   a derivation never inherits a tail, it reports `tail_dropped`. The teaser is
   the demonstrated-loss case either way, and it is the reason this ranks
   second.
   - **Built and applied 2026-08-13, and call 1 is taken: the essay gets a
     tail.** `card:outro` at 6s, and the film re-rendered from it, so the
     project is now the thing that knows the card exists. **This item is
     closed.** HISTORY.md § Tail time, built; § The end card and the bumper
     became templates; § The essay's end card went into the project.
3. **Music, the throwaway bed** — the measurement before any design
   (§ Three uncosted parity items, costed): render one bed over the Scream cut
   expressed length-agnostically — loop or hold, fade anchored to the end —
   and *listen*. The length premise is the one most likely to be wrong and it
   cannot be settled by inspection. The numbers to port either way live in
   goodsometimes `music_bed.py`: bed 17 LU under VO, 9 dB sidechain duck,
   −16.1 LUFS / −1.2 dBTP, tame-don't-cut, the no-music twin export. The
   v7→v8 lost-invocation incident is why this belongs in the project at all.
   - **The listen happened 2026-08-13 and the loop lost, on an axis this item
     does not have.** The control is not a bed but an *arrangement* — two cues
     placed against the film's structure — so length handling was never what
     was being heard, and no length model turns one looped cue into two placed
     ones. **Nothing gets built on the loop, and the "hold" variant is beside
     the point.** If proofcut gets music it is cue *placement*, in the shape the
     cue table already has for picture — which is a new item to raise when a
     video wants it, not this one. **This item is closed.** HISTORY.md § The
     three served answers.
4. **The keyframed move** — authoring only; the mechanism is already paid for
   (§ Per-shot framing: the writer emits keyframes and `=` interpolates), and
   keyframes are in source frames, never timeline seconds. The test case is
   named: `s4-reveal`'s 410px follow at src 7.343 in the shipped teaser.
   Judged on `reframe_sheet`, then a watch. The flash in-points ride the same
   review round — the answer on record is "all", but only `vi-richie` at
   59.528 survives scoring, so the procedure taken 2026-08-12 is: nudge that
   one on its evidence and put the other four on a served sheet before
   touching them, because the score cannot tell a flash from a fade
   (HISTORY.md § The thirty-nine windows, reviewed).
   - **Which project the flash in-points are about is not the teaser, and that
     has to be settled before the sheet is built.** `flashes.json` scored 25
     placements over a timeline running to 325s — the *vertical cut*, deleted
     2026-08-12 and rebuildable only from `~/proofcut-work/archive/vertical/`. The
     shipped teaser places four cues over three clips in 44s and holds none of
     `vi-richie`, `cold-open` or `s4-overexposed` at all. The findings do carry
     to the **essay**, whose cue table has all five assets with `src_start`
     unpinned, so the in-points are `plan_picture`'s and the nudge has
     somewhere to land — but a sheet built "for the teaser" would be a sheet of
     shots the teaser does not contain.
   - **The mechanism shipped, both framing calls came back, and both are
     authored onto the shipped teaser** — the move at src 7.3428 (approved as
     intentional) and the wrong crop at src 11.0527 moved 780 → 1010, taking
     the sheet's own number 274 → 44. `teaser-v4-captioned.mp4` is the render.
     The A/B that carried the not-a-control caution, `~/proofcut-work/projects/kf-probe`, turned
     out to hold the *identical* window, so authoring was `--interp` alone.
     **What is left of this item is the flash in-points above, and nothing
     else.** HISTORY.md § The keyframed move; § The three served answers.
   - **The flash in-points are answered, 2026-08-13, and the count of five does
     not survive contact with the essay either.** The sheet was built against
     the essay as the note demanded, and its placements turn out to be the same
     25 at the same positions — so the vertical's findings land directly. The
     signal the score never had is a *source* one: a flash is an in-point on
     the wrong side of a camera cut, so `media.scene_cuts` on the asset answers
     it and a fade has no cut to find. **One placement of 25 has the fault** —
     `vi-richie-1`, cut at +0.124s — and it is the one the score already
     separated; two more hold a cut at offset 0.000, which is an in-point
     sitting *on* its cut and correct. The nudge is authored (pin 0.125, frame
     3 of 23.976) and rendered as `essay-flashfix.mp4`, byte-identical outside
     the two `vi-richie` shots. **What is left is a watch on the other 24**,
     served at `~/proofcut-work/archive/spikes/flash-review/` (8804) — the answer on record was "all",
     and the evidence says one. HISTORY.md § The flash in-points, answered
     against the essay.
   - **The watch came back, 2026-08-13, and the count is two of 25, not one.**
     23 of the 24 agreed with the source-cut signal — `s1996-billy-stu-3` did
     not. The signal itself had found the real cut (source 21.354s), just 0.058s
     past `CUT_REACH`'s 0.40s reach from that placement's in-point (20.896s) —
     a fixed search window, not a wrong test. Fixed the same way as
     `vi-richie-1` (pin the cue, `src_start=21.35`, lands on frame 512 exactly);
     re-rendered over the first fix, `agrees: true` at 8208 frames, and 30-point
     sampling shows a difference only inside this shot's own span. **This item
     is closed.** HISTORY.md § The flash in-points, watched — and a second one,
     caught by eye.
5. **`import-edit` and the film check** — the wrong-cut class (§ Open
   questions, *How does a lucid project know it is the film*). Three builds,
   smallest first: fold the repeat-finder (`vo_windows.py --repeats`, which
   lives outside proofcut) into `transcript-checks`; one op comparing a project
   against a declared reference export (duration, segment count — one line of
   output would have caught 72s of retakes before a review did); and a real
   import for a `.kdenlive` playlist, which is also most of what Elf needs
   (inherit a picture cut, re-attach new VO). Lands before the next essay
   starts, because analytics wants one or two before October.
   - **All three shipped 2026-08-13** (HISTORY.md § The film check, and the
     repeat that was never lucid's to see; § The import that was one frame
     short, sixty-three times). The import is the one that paid: read back,
     `out` is the last frame *index*, and reading it as exclusive lost a frame
     off the end of all 63 ranges. **This item is closed.**
6. **`proofcut review` — the review round as a feature.** Every version of this
   film moved on a served page, and the serving was rebuilt ad hoc at least
   four times with hand-written decision files. Serve named renders, sheets
   and A/B pairs over LAN with Range support; record verdicts into the
   project. One rule carried from the round that went wrong: nothing is
   labelled a control unless it is byte-identical to what it claims to be
   (HISTORY.md § The bumper the teaser never had).
   - **Shipped 2026-08-13** (HISTORY.md § `lucid review`, built): an additive
     `review` manifest key, `ops.review_add`/`review_verdict`/`review_list`
     (MCP tools and `proofcut review add/verdict/list`), and `proofcut review
     serve` — **token-gated rather than loopback+Host**, since this server is
     built to be reached off the machine. The control rule is enforced at
     registration (a byte mismatch refuses the call) rather than left as a
     convention to remember. **This item is closed.**
7. **`vo_extend`** — authorized, and it is the one item that bends `Edit`'s
   subtractive invariant, so it gets its own design note before code
   (§ Parked's constraints; HISTORY.md § `cut_by_time`). Not needed for tail
   time — shape B made the tail downstream of `Edit` — its cases are the
   Billy/Stu hold and manufactured mid-film silence.
   - **Built 2026-08-14** (HISTORY.md § `vo_extend`, built), exactly the shape
     the design note settled: `Edit.insert` splices a real generated-silence
     clip in via a new mutator rather than widening a clip_id past its
     registered duration, `restore`'s existing interleaved-segments check and
     `_is_layered`'s existing clip-count test both catch the two named
     consequences with no changes of their own, and word indices upstream of
     a hold are untouched. `ops.vo_extend`/CLI `vo-extend`/the MCP tool report
     `covered_by` — the reporting obligation the note flagged as the one
     unbudgeted piece, since `build_shots` would otherwise auto-extend stale
     picture across a hold with every other check staying clean. **This item
     is closed.** What it does not settle, because it was never in scope: the
     design note's own open question — whether the Billy/Stu hold is worth
     using on the actual film — stays editorial, decided on a watch.
8. **The channel preset pack** — templating, after tail time gives the assets
   a home. proofcut stays generic (`mark` is an empty slot, deliberately);
   goodsometimes ships a loadable pack: palette, faces, the mark as
   `G[em]*[/em]`, caption presets, platform safe zones, and the end card and
   bumper as card templates. The October palette swap becomes a preset
   variant instead of a rebrand.
   - **Its precondition is met**: tail time shipped and took the two cards with
     it — `endcard` and `bumper` are `graphics.TEMPLATES` entries as of
     2026-08-13, so neither spec lives in a proof script any more (HISTORY.md
     § The end card and the bumper became templates).
   - **Built 2026-08-16, and the line above it was wrong: what was left was not
     the pack, it was the loader.** The queue's own premise going in was that
     the pack was goodsometimes' content, loaded by proofcut — in fact proofcut had
     no extension point at all: `captions.PRESETS` and
     `graphics.TEMPLATES`/`PALETTE`/`FONTS` were closed literal dicts and
     nothing anywhere read a config file, so this was mostly proofcut-side work,
     not content waiting on goodsometimes. **This item is closed.** HISTORY.md
     § The channel preset pack, built.
9. **The October scale spike** — timed to land before the mid-September
   format decisions: one long two-speaker recording through
   transcribe → cut → render, to find where the pipeline groans (windowed
   transcription cost, melt RSS, cue-table size) while the format can still
   route around it. § Parked's *everything one video couldn't establish* is
   the list under test.
   - **Half of it ran 2026-08-13, and it moved its own premise** (HISTORY.md
     § The scale spike, half-run). The 58m08s recording exists and no proofcut doc
     knew it did. **Cue-table size is not the groan point**: `cue_add` is O(n²)
     over authoring and still sub-second at 450, while the spike read
     **`build_shots`** as the compounding one — `Edit.timeline_span` was an
     unindexed linear scan called once per cue, firing on every editing
     mutation through the web UI. (Timed rather than projected, that reading
     did not hold; see below.)
     Windowing is linear and holds. Two findings ride out of it as their own
     work: the missing hallucination guard on `asr.transcribe`, and that two
     streams are a new `Edit` primitive rather than a parameter.
   - **The transcribe half ran 2026-08-14, once the GPU that blocked it was
     idle** (HISTORY.md § The scale spike's GPU half, measured): 7.42×
     real-time on the same 120s slice, peak VRAM 6.47 GiB, 0 hallucinated
     words — a point-in-time number on an idle card, not a throughput promise
     against real contention. **Melt RSS is still one data point** — a repeat
     of the same project at the same duration and source count, so whether it
     tracks duration or source count is the one thing left unmeasured from
     this item.
   - **Both defects it named are fixed 2026-08-13, and both overturned their
     own row on the way** — which is the argument for keeping a spike's
     artifacts rather than its summary. The hallucination guard is not
     "run `_drop_stacked` on the ingest path too": on the spike's own saved
     transcript that rule drops three of the eight bad words and would have
     shipped as a fix. What separates the loop from real speech is a **count in
     a window** — 3 words per 0.25s across every real transcript on this box
     against the loop's 8 — never a rate, because whisper's durations put real
     speech at 50 w/s over three words. And **`build_shots` is not the groan
     point**: at the projected 400 cues over 2000 segments it costs 25 ms. The
     same method called once per *word* by `captions.place` costs 2.03 s on a
     silence-cut hour, which is what `Edit._SpanIndex` now answers in 3.9 ms.
     HISTORY.md § The ingest path's hallucination guard; § The scan the spike
     named was not the one that costs. **What is left of this item is its own
     unrun half** — the transcribe pass and the melt-RSS second data point.
   - **The melt half ran 2026-08-16, and the controlled cell it flagged as
     missing ran 2026-08-17 and flipped its verdict: RSS tracks the resident
     SOURCE COUNT (scaled by frame size), not density and not duration.**
     Six cells ruled duration out twice over but read "density" off the
     pattern; cell F held the source set constant while dropping density
     6.8× and the peak moved +0.4% — inside repeat noise. The comparison
     that made density look right was a canvas confound (the vertical cells
     render 32% more pixels per frame than the landscape ones). Per-source
     cost is ~30 MiB on these cells, resident once opened; timeline length
     is free. **This item is closed.** HISTORY.md § The melt RSS matrix;
     § The controlled density cell, and the answer flipped.
10. **The parity long tail** — the gap-drag measurement first (§ Three
    uncosted parity items: does a timeline drag usually land on a gap a cut
    left? settle against the Scream project with `Edit.gaps` before writing
    any UI), then the window learning to place a cue — the root of three
    separate deferrals — then the panes (assets/import roles; properties,
    whose gate cleared when graphics shipped), multi-project, HTTP transport,
    filmstrip thumbnails.
    - **The window learning to place a cue shipped 2026-08-13.** The
      settled premise (§ Three uncosted parity items: 85–87% of drags are
      word-anchorable, no gap-anchored address space needed) meant nothing
      in `Edit`'s addressing had to change — just a gesture and a route. A
      click-and-drag on any timeline lane resolves its *start* to a word
      index (`timeline.js`'s own client-side mirror of `ops._nearest_word`,
      over `state.words` it already had for drawing — no server round trip
      to place the highlight), and feeds the already-drawn but previously
      dead `drawSelectionHighlight`/`'selection'` plumbing for the first
      time. A small floating toolbar (the same `.selection-toolbar` CSS
      transcript.js's Cut/Restore bar uses) takes a free-text asset name —
      `clip_id` or `card:name`, typed by hand, since the assets pane this
      would otherwise drag from is the *next* item on this list and does
      not exist yet — and posts to a new `/api/cue`, a fourth caller into
      `ops.cue_add` alongside the CLI and MCP tool, matching every other
      mutating route in `webui.py`'s `_POST_ROUTES`. A plain click with no
      drag is left alone (`seekOnClick` still owns it; a capture-phase
      click listener swallows the one native `click` a real drag can still
      fire, so it never double-fires a seek). Backend covered by three new
      real-socket tests in `test_webui_http.py` (91/91 pass); the drag
      gesture itself is the one piece this session could not verify —
      `proofcut web` binds loopback only and no browser tool was available
      here, so it needs a real-browser pass before being called done for
      the UI half. **What is left of this item: that browser pass, then the
      panes, multi-project, HTTP transport, and filmstrip thumbnails.**
    - **All five are built, 2026-08-16/17, and the browser pass did not
      merely confirm the gesture — it found six defects.** Driven for real
      over CDP rather than through the three backend tests that only POST to
      `/api/cue`, the worst of the six was silent: a re-render on mousedown
      tore out the very DOM node the gesture had landed on, which **broke
      click-to-seek on every lane, for any clip with a transcript**, found
      only by A/B against a transcript-less clip where the handler never
      runs. The first fix passed every drag assertion in the harness and was
      dead in the hand — a dwell probe showed it only worked at dwell times no
      real click produces, the same lesson the assets pane's role toggle
      needed a second time on an unrelated race. Built alongside: the
      assets/properties/filmstrip panes, the multi-project picker, and
      `proofcut mcp --transport http`, whose own review caught an allow-list bug
      admitting the attacker it was meant to exclude, fixed before ship.
      **This item is closed.** HISTORY.md § The cue-drag browser pass, and six
      defects; § The dwell-timing lesson; § The assets, properties and
      filmstrip backend, and its panes; § The multi-project picker, built;
      § MCP over HTTP, built.

Corrected the same day, found by the same review: docs/plans/DAYDREAM.md's two stale
rows (b-roll's "nothing is built"; aspect swap's "none has been reviewed").
The goodsometimes side of the review — the teaser workflow absent from
`pipeline.md`, the unrecorded 3:00 Shorts cap, `branding.md`'s stale
end-screen row — belongs to that repo, not this queue.

## Blur-fill — the design note — 2026-09-16

PRIOR-ART.md § Glama's related servers found blur-fill in vidcut, and it is
the usual vertical-video treatment proofcut does not have. A shot whose
aspect does not match the canvas is drawn *contained*, and a blurred,
darkened copy of the same moment, scaled to *cover* the canvas, fills the
bars. proofcut offers two answers to a wide shot on a 9:16 canvas today, a
crop (§ Per-shot framing) and a stacked split (§ The stacked split). Blur-fill
is the third, for a shot where every crop loses something and nothing
divides into two panes: a wide establishing shot, a screen recording, a
group. The mechanism was measured before this note, in
`~/proofcut-work/spikes/blur-fill/` (`FINDINGS.md`: every number below,
with the command that produced it). Nothing is built.

### Finding 1 — it is a second node again, and the third time is not a surprise

The same result as the split: two nodes of one resource and the tractor's
existing `qtblend` transitions. The background node has a `qtblend` rect
scaled to cover (the larger of the two axis ratios, centred, overflowing the
profile, which clips it), plus `box_blur` and `brightness level=0.7`. The
foreground node has the contain rect, which is `fit_rect`'s. A 1080x1920
render of a 1920x1080 colour-coded source read back as follows. The centre
band matches ffmpeg's own contain scale of the same frame to RMSE 7.4, the
same compression floor the no-blur and letterbox controls read. The bands
are non-black, and their Laplacian energy is 0.30 of the sharp copy's. A
per-second colour marker read back correctly in the blurred band at two
different seconds, so the background is the same *moment*, not merely the
same clip. No new service, no mask, no `<blank>`.

### Finding 2 — the blur has to be a percentage, which rules out every avfilter blur

`box_blur`'s radius is a percentage of the image, and ffmpeg's `gblur` sigma
is pixels. Measured as the seam's transition width over frame width, at
1080x1920 and at 360x640: `box_blur` gives 6.20% and 6.39% (ratio 1.03),
`gblur` gives 12.69% and 38.61% (ratio 3.04). A pixel blur tuned on one
canvas is three times as strong on a third-size one, and it renders at
exit 0 either way. **Use `box_blur`**. The same rule binds the preview (step
3): CSS `filter: blur()` takes pixels, so its radius must be computed from
the drawn frame's width, never written as a constant.

### Finding 3 — the audio stays single, and `audio_index=-1` stays mandatory

An unmuted second node added nothing to the mix: −42.1 dB mean, identical to
the baseline. It added +6.0 dB only once a `mix` transition was wired to it.
So the writer's convention (picture nodes carry `audio_index=-1`, and no
`mix` targets a picture track) is what holds today. Keep both, because the
convention is the only guard against a later generic per-track loop that
mixes whatever declares audio.

### Finding 4 — the cost is the second decode

A 10 s 1080x1920 render took 7.3–7.5 s single-track, 12.0 s with the cover
node, and 12.4–13.6 s with blur and darken as well: about +60% for the
node and a further ~13% for the blur. It is paid only by a project using
it, because a project with no fill window writes no background node, and
its document stays byte-identical (the aspect swap's rule).

### The design

1. **Blur-fill is a mode of a framing window, not a project setting.** A
   `reframe` record may carry `fill: "blur"` instead of a `rect`, meaning
   "this shot, whole source, contained, over its own blur". It is additive
   and optional, so absence means today's window and there is no schema
   bump. `pane` and `fill` on one window refuse. The switch keys the
   background node's opacity at every window boundary (the pane's own
   mechanism, § The stacked split: a step not written is a value that
   carries on), and the picture node's discrete `qtblend` key takes the
   contain rect for a fill window.
2. **The background covers the whole source, never the shot's crop.** This
   is the fork the spike left open. A fill window has no crop, so there is
   nothing to follow, and the rect is a pure function of (source size,
   canvas). No keyframes of its own beyond the opacity switch, and no
   inheritance risk, because it is its own role (`bgchain`) under
   one-node-per-resource-per-role. Following a crop (a contained crop over
   its own blur) is expressible later as `rect` plus `fill` together,
   refused until someone asks.
3. **The preview draws it, or the lane is not drawn.** `timeline_view`'s
   shot entry gains `fill` and the writer's own two dest rects (contain and
   cover), scaled like `dest` is. `player.js` draws a second `<video>` of the
   same shot behind, with a blur radius computed from the frame's drawn
   width (Finding 2). Nothing in JS derives either rect.
4. **`reframe_sheet` labels a fill row and draws the contain rect**, so a
   fill window is judged where every window is judged.
   **`reframe_detect` does not propose fill yet.** Its subject evidence is
   faces, and "every crop loses someone" is a claim to pin against looked-at
   windows first, the way `SCENE_THRESHOLD` was pinned.
5. **`_is_layered` needs no new trigger.** A fill window exists only under a
   canvas override, which already routes to melt. A test holds that, rather
   than this sentence.

### Steps

1. The writer: `bgchain`, the opacity switch, and a real-melt readback test
   on a colour-coded source (centre band against ffmpeg's contain; a band
   pixel naming the same second). **Two traps from the spike**: tag the
   generated source's colour range (bt709/tv) or the readback reads a
   range mismatch as a picture error (RMSE 14.7 before, 7.4 after); and
   place the time marker inside the *cover* crop, since the spike's first
   marker sat entirely off-canvas.
2. The op: `reframe --fill blur` (and `plan`), on CLI and MCP, echoing the
   contain rect it will draw.
3. `timeline_view` and the preview layer, verified by canvas readback
   against ffmpeg (verify-live).
4. `reframe_sheet`'s fill row, and a Fill control beside Crop and Split in
   Frame mode.

### Decisions for Tyler

- **Per-window, not project-wide** (recommended). A project-wide "fill
  every mismatched shot" is one line on top of it later, if asked.
- **Whole-source background** (recommended), not following the crop.
- **Darkening fixed at 0.7, blur at 12%**, until a real render has been
  watched. A pack value would be tuning a number nobody has looked at.
