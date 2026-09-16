# Prior art — the agent-driven video editing landscape

Survey conducted **2026-08-06**, in two sweeps the same day: the first missed
the conversational-editor field entirely (see "Corrections"), and a second
sweep prompted by the stop-or-continue question found OpenChatCut, video-use,
and open-edit. A third pass on **2026-08-07** covered Daydream, the closed-source
product closest to proofcut's own shape, which neither sweep had checked
because it has no GitHub repo. Star counts, versions, and wheel matrices are
snapshots from those dates and go stale; the *conclusions* they support live
in [PLAN.md](PLAN.md), which is authoritative for decisions. This file is the
evidence, not the decision.

Re-run this survey before any major scope change.

## Why this survey happened

The plan was written from priors about who else was in this space. Two of those
priors turned out to be wrong (see "Corrections to earlier assumptions"), and
one project — auto-editor — turned out to occupy far more of the MVP tool
surface than the plan assumed.

## auto-editor — the project that matters most

[WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) · 4.7k★ ·
**Unlicense (public domain)** · pushed daily · Nim

Originally treated in the plan as a silence-removal library. It is now
effectively a headless NLE, and it ships an agent interface.

What it has:

| Capability | Detail |
|---|---|
| Transcription | `auto-editor whisper FILE MODEL` — whisper.cpp, NVIDIA Parakeet, or Apple Speech backends. `--format text\|srt\|json`, `--split-words` for per-word cues, `:mic` for live capture. **Not usable out of the box:** MODEL must already be on disk, there is no `download` subcommand, and a bare `base.en` fails with "Could not load whisper model" (run 2026-08-07) |
| Transcript cutting | `--edit word:VALUE`, `--edit "subtitle:pattern=REGEX,ignore-case=#t"`, composable with `or`/`and`/`not`/`xor` |
| Silence/motion cutting | `--edit audio:threshold=0.04`, `--edit motion:...`, `blackdetect`; labels 0–255 with per-label actions (`--edit:N` / `--when:N`) |
| Timeline format | `.v1`/`.v2`/`.v3` — **both exported and imported/rendered** |
| NLE export | `premiere` (fcp7 xml), `resolve` (fcpxml), `resolve-fcp7`, `final-cut-pro`, `shotcut` (.mlt), `kdenlive`, `clip-sequence`. All but `resolve-fcp7`/`clip-sequence` **run-verified on 31.4.2, 2026-08-07**; `kdenlive` emits a real MLT playlist — cuts as separate entries on linked video/audio chains, resources resolving — which `melt` rendered |
| OTIO export | **Undocumented.** `src/exports/otio.nim`, selected by `--export premiere-otio` or an `.otio` output extension. Premiere-flavored (`PremierePro_OTIO` metadata), one-way |
| Dry run | `--preview` prints what would be cut and exits without rendering |
| Agent interface | `skills/auto-editor{,-transcribe,-export,-effects}` in-repo; `npx skills add WyattBlue/auto-editor` |

### The v3 timeline format

Flat, LLM-legible JSON — essentially a flattened OTIO track with different field
names. This is what makes auto-editor usable as proofcut's render backend:

```json
{"version":"3","timebase":"30/1","background":"#000","resolution":[1280,720],
 "samplerate":48000,"layout":"stereo","langs":["eng","eng"],
 "v":[[{"src":"example.mp4","start":0,"dur":26,"offset":0,"stream":0}]],
 "a":[[{"src":"example.mp4","start":0,"dur":26,"offset":0,"stream":0}]]}
```

- `v` / `a` are `Clip[][]` — layers, compositing bottom-to-top, `v[0]` the base.
  Empty layers are rejected.
- Clip fields: `src`, `start` (timeline position), `dur`, `offset` (start point
  in the source), `stream`, optional `effects`.
- All times are in **timebase units**, timebase is a rational (`"30000/1001"`).
- `auto-editor timeline.v3 -o render.mp4` renders it.

Renderer internals worth reading (public domain, portable without attribution):
`src/render/{video,audio,h264,hevc,smart,partialplan,subtitle,format}.nim`.

### What auto-editor cannot do — lucid's remaining differentiator

1. **Addressable ranges.** Its transcript editing is a *global declarative
   filter* — "keep every section whose subtitle matches this regex." There is no
   way to say "cut words 30–45" or "keep take 2 of that sentence, drop take 1."
   Filter, not edit. This is the single most important gap, because addressable
   ranges are exactly what an agent needs for iterative work.
2. **Persistent project state.** Each invocation is source → output. No
   accumulating edit, no refinement across turns, no undo.
3. **MCP proper.** Skills driving a CLI are not typed tools with schemas and
   structured returns.
4. **OTIO as native source of truth.** Its OTIO export is Premiere-flavored,
   one-way, and undocumented.

## OpenChatCut — the project that decides lucid's fate

[0xsline/OpenChatCut](https://github.com/0xsline/OpenChatCut) · 854★ ·
**AGPL-3.0** · TypeScript (Electron 43 / React 19 / Remotion) · active, 338
commits · v0.1.9 (2026-08-06) ships macOS dmg (arm64+x64), Windows exe, and a
**Linux x86_64 AppImage**

Missed by the first sweep. A local-first conversational editor whose pitch is
proofcut's pitch: agents (built-in via Vercel AI SDK, or external via MCP) editing
a real multi-track timeline, with word-level transcription, text-based cuts,
speaker labels, linked captions, and undo/redo. Projects persist to
`~/.openchatcut` as JSON.

Its MCP integration is the notable part: the desktop app serves an HTTP MCP
endpoint (`localhost:5199/api/external-mcp/mcp`) exposing ~24 skills, with a
`begin_edit_session` / `review_edit_session` proposal workflow — agent edits
land atomically as a single undo step, optionally gated on in-app human
approval. That proposal-review shape is arguably *better* for human-in-the-loop
than raw tool calls.

Against proofcut's three differentiators, it plausibly covers all three:
addressable word-level cuts (not just filters), persistent projects with undo,
and real MCP with typed skills. What it does **not** cover:

1. **Headless.** The MCP endpoint is served by the Electron app; a GUI process
   has to be running. No CLI. Docs are macOS-flavored; the Linux AppImage is
   unverified on this box.
2. **OTIO.** Custom JSON timeline rendered by Remotion + ffmpeg. It does export
   FCPXML 1.10 (`src/export/fcpxml.ts`, plus a Resolve-flavoured `fcp_xml_resolve`
   variant that adds `colorSpace` to `<format>`), and transcript-deleted words
   are emitted as separate `asset-clip`s so the cuts survive — but that is cuts,
   clip placement and lanes only. Transitions, effects, volume and titles do not
   cross, and motion graphics become a `<gap>` unless pre-rendered to ProRes.
   Captions leave separately as SRT. No OTIO.
3. **Thin dependency graph.** Electron 43 + Node 24 + Remotion versus a Python
   package and three subprocesses.

Whether that remainder justifies proofcut is not answerable by reading — it is
answerable by installing the AppImage and running the addressable-edit test
("cut words 30–45; keep take 2, drop take 1", iteratively, via Claude Code
over its MCP endpoint) on a real recording. That trial is the go/no-go gate in
[PLAN.md](PLAN.md).

## Daydream — the most prominent competitor, and had never been checked

[daydreamvideo.com](https://www.daydreamvideo.com) · closed-source, no public repo (confirmed —
searched GitHub for the org; the only `daydream*` orgs that exist belong to unrelated products,
including a same-named but unrelated real-time video-diffusion tool at daydream.live) · macOS
desktop app, Apple Silicon (page title literally reads "Download Daydream — AI Video Editor for
Mac"; no Windows or Linux build found on the download page, docs, or FAQ) · by Pushie, Inc.

Neither sweep covered it, because neither searched outside GitHub — the README named Daydream as
proofcut's foil from the first commit without the claim ever being checked. Verified directly against
daydreamvideo.com and docs.daydreamvideo.com, 2026-08-07.

**It is a full desktop NLE, not a chat front end that hands off to finish elsewhere.** The editor
shows a real multi-track timeline (V1/V2 video, A1/A2 audio, CC captions), an asset panel,
properties/templates panels, frame-accurate scrubbing, and direct transcript-based trimming.
Watermark-free MP4 rendering happens inside the app; NLE export is an *additional* option, not the
only way to finish a project. This falsifies README.md's claim that proofcut does not build a desktop
editor "same as Daydream does" — Daydream finishes its own timelines.

MCP: local HTTP server, `http://127.0.0.1:7433/mcp`, no auth documented.
`claude mcp add daydream --transport http http://127.0.0.1:7433/mcp --scope user`. Docs describe
capabilities in prose only — import/transcribe, transcript-based cutting, b-roll search and
placement, captions, motion graphics, export — with no enumerated tool list. Unlike OpenChatCut's
~24 named skills, Daydream's actual MCP schema is unverified: there's no source to grep, and no
Linux build to install and connect to directly.

Export: separate XML for Premiere, XML for Resolve, and FCPXML for Final Cut. References the
original footage rather than bundling it — "you may need to relink the media in your editor," per
docs. No documentation of what survives the export (transitions, effects, motion graphics, titles)
versus what both OpenChatCut and auto-editor document losing on their own NLE exports. No OTIO.

Pricing is the only hint at the processing architecture, and it cuts against the marketing copy:

| Plan | Price | Processing (b-roll search) | Transcription | MCP calls/mo |
|---|---|---|---|---|
| Free | $0 | 1 hr/mo | 1 hr/mo | 100 |
| Pro | $16/mo annual, $19/mo monthly | 20 hr/mo | 10 hr/mo | 1M |
| Business | custom | extended | extended | extended |

Hour-metered transcription and search sit awkwardly next to "your footage stays on your device and
is never uploaded or stored in the cloud" (docs, verbatim). Either the AI work runs locally and the
hour caps are a pure subscription gate, or "never uploaded" describes only the raw footage and not
what's derived from it (transcripts, embeddings) sent out for inference. Docs don't say which, and
closed source means it isn't independently checkable the way auto-editor's or OpenChatCut's claims
were.

**Against proofcut's differentiators:** headless — no, it's a GUI app fronting a local MCP server,
same shape as OpenChatCut, not proofcut's CLI-first model. OTIO-native — no, undocumented per-target
XML/FCPXML export only, no evidence of any timeline IR underneath. Thin dependency graph —
unconfirmed but unlikely, given the product surface (motion graphics, b-roll search, multi-format
export) matches OpenChatCut's Electron/Remotion scale more than auto-editor's. Addressable ranges
and persistent project state — plausible from "edit the transcript to cut" but unverified, and
unlike OpenChatCut there is no way to put it through the addressable-edit trial on this box: no
Linux build exists.

Not a substitute for the OpenChatCut go/no-go trial in [PLAN.md](PLAN.md) — it's the more prominent
competitor by mindshare but the least inspectable one.
Whatever confidence the OpenChatCut trial buys by actually running the software, Daydream can't
offer, because it can't be run here at all.

### The full-site pass — 2026-08-08

Re-surveyed in full for the parity decision — every page, the three CSS bundles, all sixteen
homepage videos frame-sampled through ffmpeg, the docs via their Mintlify markdown mirror. The
observed product anatomy, the workflows, and the design system live in
[docs/plans/DAYDREAM.md](plans/DAYDREAM.md) (capture method and its one limit in
that file's § How this was captured). What the pass adds as *competitor* evidence: the
homepage retitled to "AI Video Editor for Claude Code & Codex" ("for Mac" survives only
on `/download`), still macOS-only, so the Linux asymmetry stands; the in-app chat is
Claude Code/Codex itself as a subprocess on the user's own sign-in — the mechanism
proofcut's agent panel chose independently a day earlier (PLAN.md § The agent panel, in
mechanism); and the MCP tool schema remains unpublished — workflow prose only, no
tool list, unchanged from the entry above.

## NLE handoff on Linux has a ceiling

"OTIO-native NLE handoff" is only worth something if a Linux NLE can receive it.
Checked 2026-08-06:

- **DaVinci Resolve** imports FCPXML, but free Resolve on Linux decodes no
  H.264/H.265 and no AAC at all — a licensing restriction, not a bug, and Studio
  buys back only the video half. An FCPXML pointing at camera MP4s therefore
  opens as a timeline of offline clips. A working handoff means transcoding to
  DNxHR/ProRes + PCM first and referencing the transcodes. Install on Bazzite:
  `ujust install-resolve`, or [davincibox](https://github.com/zelikos/davincibox).
- **Kdenlive** has no FCPXML import. The way in is
  [KDE/kdenlive-opentimelineio](https://github.com/KDE/kdenlive-opentimelineio)
  plus `otio-fcpx-xml-adapter` — two lossy hops.
- **Shotcut / Olive / Blender VSE** take MLT or nothing.

So the handoff formats that actually land on this box are MLT (`kdenlive`,
`shotcut`), both of which auto-editor already emits, with FCPXML useful only for
an already-transcoded Resolve project. That is a further argument for OTIO as
proofcut's *internal* source of truth rather than as the pitch.

## browser-use/video-use — the distribution threat, not a substitute

[browser-use/video-use](https://github.com/browser-use/video-use) · 19.9k★ /
2.5k forks · MIT · Python · young (18 commits; stars ride the browser-use
org's distribution)

Agent-driven editing via coding agents (Claude Code, Codex): filler-word
removal, grading, subtitle burn-in, fades. Fails proofcut's core constraint
outright — transcription is **ElevenLabs Scribe cloud API only**, no local
option — and has no MCP and no real timeline model (text-first: word-level
transcript packed into ~12KB markdown, ffmpeg execution underneath).

Two ideas worth stealing regardless:

- **Decision-point composites.** Instead of frame-dumping, it renders PNG
  composites (filmstrip + waveform + labels) only where the agent must make a
  call. Convergent with proofcut's contact-sheet preview lean — the first sweep's
  "nobody else in the space does it" was wrong.
- **`project.md` session memory** persisting editorial decisions across
  sessions — a cheaper cousin of kinocut's receipts.

## veedstudio/open-edit — open-core, not open

[veedstudio/open-edit](https://github.com/veedstudio/open-edit) · 169★ ·
Apache-2.0 editor over **PolyForm Shield** renderer binaries · TypeScript ·
Apple Silicon macOS Tahoe only, 11 commits

VEED's agent-driven caption/motion-graphics pipeline. Default transcription
uploads audio to VEED (WhisperX local fallback exists); rendering is their
closed-source binary. Same open-core-with-hosted-tier shape as openshorts.
Not a proofcut substitute — wrong platform, wrong openness — but it belongs in
the field map: incumbents are now releasing agent-facing editors.

## kinocut — the cautionary tale

[KyaniteLabs/kinocut](https://github.com/KyaniteLabs/mcp-video) · 101★ ·
Apache-2.0 · Python · pushed daily

Closest neighbour by intent: local-first, MCP + CLI, "guardrailed video editing
for AI agents." **161 MCP tools and 140 CLI commands** at v1.11.1.

The tool count is the lesson. A surface that large degrades agent tool selection
and consumes context before any work begins. Their ROADMAP is correspondingly
dense with governance apparatus — policy engines, release gates, human-only
acceptance items, "agents must not invent closed." That is what an agent-built
project looks like after a year of unchecked accretion.

Two things they got right, arrived at independently:

- **Durable edit projects** — content-addressed store, async render/resume,
  ordered events, workflow receipt lineage (`kinocut/projectstore/`). Two
  projects converging separately on persistent project state is decent evidence
  it is load-bearing rather than gold-plating.
- **Word-timed ASS captions.** Their choice, shipped. Alongside disfluency cuts
  and project-recipe export/replay.

They do **not** use OTIO (3 incidental code hits). They built a JSON workflow
engine over stateless ffmpeg instead — i.e. reinvented a weaker timeline model.

Worth stealing: the **Video Receipt** idea — per-operation JSON provenance with
input/output hashes, ffmpeg version, and a resume cursor.

## The OTIO + MCP niche is empty — but the broader thesis is not

Searched GitHub for anyone occupying proofcut's exact thesis. The *literal* niche
— OTIO as native source of truth behind MCP — remains unoccupied (OpenChatCut
uses a custom JSON timeline). But the first sweep's stronger reading, that
nobody offers addressable ranges + persistent state + MCP together, was
falsified by OpenChatCut above. What survives as differentiator is the
narrower combination: headless, CLI-parity, OTIO-native, thin-stack. The
micro-repos found by the first sweep:

| Repo | ★ | Language | Notes |
|---|---|---|---|
| [satoh-y-0323/clipwright](https://github.com/satoh-y-0323/clipwright) | 2 | Python | Closest match — MCP server *suite* wrapping FFmpeg/OTIO, split per domain (`clipwright-stabilize`, `clipwright-transcribe`, …). Careful work; runtime depends only on ffprobe, uses whisper.cpp + ggml models |
| [alexrienzie/open-post-production](https://github.com/alexrienzie/open-post-production) | 2 | Python | Transcribe/search/cut at documentary scale, local hardware |
| [chaoz23/otio-diff](https://github.com/chaoz23/otio-diff) | 1 | Python | Structural diff between two OTIO timelines — added/removed/retimed/moved clips. CLI + MCP. **Directly useful to proofcut** as the "what did the agent just change?" primitive |
| [plokdalberb-byte/cutible](https://github.com/plokdalberb-byte/cutible) | 0 | Python | Created and abandoned the same day (2026-06-22); ignore |

The OTIO rendering thesis is also still unproven. **Nobody has demonstrated
OTIO→ffmpeg rendering inside an agent loop.** That is the argument for spiking
render before building on top of the assumption — if the project continues
past the OpenChatCut trial gate.

## Two neighbours the survey did not have — 2026-09-12

Found while drafting the launch listings, in awesome-mcp-servers' Multimedia
Process section. Read from each README and the GitHub API that day. Neither was
run.

- **[ronak-create/FableCut](https://github.com/ronak-create/FableCut)** · 667★ ·
  MIT · JavaScript · created 2026-07-06. A Premiere-style NLE in the browser
  whose whole timeline is one `project.json`. An agent edits that document over
  MCP or REST, and the open UI hot-reloads it over SSE, so a person watches the
  agent's cut land. That is `proofcut web`'s agent pane from the other end: no
  transcript addressing, no OTIO, render in the browser. It is already in the
  official registry, the awesome list and Glama, which is the listing path proofcut
  is about to take. Read from source the same day, below: § FableCut, read —
  and why lucid does not merge with it.
- **[Cassette-Editor/oh-my-cassette](https://github.com/Cassette-Editor/oh-my-cassette)**
  · 132★ · MIT · Python. A Claude Code, Codex and OpenCode plugin plus MCP server.
  It returns a timeline digest and a contact sheet every turn, and renders
  nothing until the plan is approved. It needs a Cassette account, so the edit
  is not local. It quotes its own session cost ($4 on Opus 5), the way TRIAL.md
  quotes proofcut's.

kinocut moved from 101★ to 146★ since the entry above and now pitches "quality
gates". **Read from source the same day (`faaecc2`), the gate scores signal
levels**: brightness, contrast, saturation, colour balance, motion and
loudness, against fixed ranges. Its receipts are sha256 provenance. The one
output-against-plan check found is silence removal's duration, within 0.15s.
No path was found that transcribes a render against intended words or counts
frames against a timeline, so that pair (`verify`, `check_frames`) stays
proofcut's, stated narrowly. Grep and reading, not a run:
`~/proofcut-work/spikes/launch-listings/LISTINGS.md` § kinocut's gate, read.

## FableCut, read — and why lucid does not merge with it

Asked 2026-09-12: is FableCut the same thing, is it better, should proofcut merge
into it. Read from source at `6ed70b0` (v1.7.0, pushed 2026-09-11; 667★, 67
forks, 7 contributors). Grep and reading, not a run.

**Same neighbourhood, a different product.** FableCut is a human's NLE with an
agent as co-editor; proofcut is an agent's editing toolkit with a window a human
watches through.

| | FableCut | proofcut |
|---|---|---|
| Agent surface | 8 MCP tools, mostly get/patch/set of `project.json` — clips placed in seconds | 93 `@_tool()` tools, each with a CLI twin, ranges addressed by transcript word |
| Speech | None in the editor. `examples/auto-captions/` turns someone else's STT word timestamps into karaoke text clips — captions, never addressing | whisper at import; cut, cue, caption and verify all resolve through words |
| Render | Browser compositor → server ffmpeg (`/api/export/begin`/`frame`/`audio`/`end`); its own CLAUDE.md: "the user previews/exports from the UI". No MCP export tool | Headless — auto-editor single-source, `melt` multi-source |
| Checking the output | None found against the plan | `verify` transcribes the render, `check_frames` counts it against the timeline |
| Stack | Node ≥18, zero npm deps, one 8,345-line vanilla `app.js` | Python ops layer plus whisper, the Kdenlive flatpak's melt, auto-editor |
| Licence | MIT | PolyForm Shield |

**Where it is ahead, measured rather than conceded:**

- **Distribution.** HN front page, the official registry, awesome-mcp-servers,
  Glama, a Discord, five README translations — the whole of
  docs/plans/LAUNCH.md, already done.
- **Hand editing.** Keyframes, transitions, marquee multi-select, on-monitor
  move/resize/rotate, multi-channel audio stems, in/out work area. proofcut's
  window has direct-manipulation gestures; it is not a Premiere.
- **Install.** `node server.js` against proofcut's three external binaries. Of
  everything here, this is the gap most likely to cost proofcut a stranger.

**Where proofcut is ahead:** an unattended agent can cut, render and check its own
cut. FableCut's agent cannot render without a browser tab open, and nothing
compares what it rendered to what was meant. Word-addressed editing,
framing detection, the timeline-derived captions, cards and the TTS splice have
no counterpart, and its "any process that writes JSON edits the video" design
points away from them rather than toward them.

**Why not merge.** Three reasons, any one sufficient:

1. **Nothing ports.** A merge is proofcut's ideas rewritten into a single JS file
   on a different runtime; none of `ops.py`, whisper or the melt writer
   crosses over.
2. **The licence.** Code contributed there is MIT, which undoes the PolyForm
   Shield choice (HISTORY.md § The licence, chosen, and wiki `decisions.md`).
3. **The designs are opposites.** "The project file is the interface" lets
   any writer place anything; proofcut routes every mutation through one `ops`
   function and verifies the render. Blending them keeps neither guarantee.

**What to take instead:** its install story and its listing path, both named
in LAUNCH.md. Interop is the one bridge that makes sense — proofcut writing a
FableCut `project.json` so a word-cut film can be hand-finished there, the way
`import_edit` already reads a `.kdenlive` — and it is **not queued**: build it
when someone asks, not on speculation.

## Glama's related servers — 2026-09-16

The six Glama lists beside proofcut's own entry
(`glama.ai/mcp/servers/tydude001/proofcut/related-servers`). Each was
shallow-cloned and read, README against source; none was installed or run.
Tool counts are registrations counted in source; commit counts are the GitHub
API's. Heads that day: vidcut `4558d35`, CutPilot `2114ca2`,
ffmpeg-mcp-video-editor `c65cd58`, mcpCut `573e443`, NeuroCut `c88f266`,
Unflick `ddc3a3d` (pushed that day, so the read may predate it).

| Project | What it is | Tools | Commits | Tests / CI |
|---|---|---|---|---|
| [mao-data/vidcut](https://github.com/mao-data/vidcut) · 1★ · AGPL-3.0 · TS | short-form timeline editor; the browser watches the agent's edits over a WebSocket | 39 | 408 in 6 weeks | 109 test files, no CI runs them |
| [Hellotravisss/cutpilot](https://github.com/Hellotravisss/cutpilot) · 0★ · no licence · JS | macOS-only editor engine; ffmpeg, optional Remotion | 219 | 12 | CI runs 13 of 70 test files |
| [AbyAbyss/ffmpeg-mcp-video-editor](https://github.com/AbyAbyss/ffmpeg-mcp-video-editor) · 0★ · MIT · Python | typed ffmpeg tools over a SQLite job queue; one whole JSON timeline per render, no project state | 38 (Glama says 32, and 38★) | 16 | unit only; rendering tests excluded from CI |
| [musyta-labs/mcpCut](https://github.com/musyta-labs/mcpCut) · 0★ · MIT · Python | multi-user editor; immutable project versions plus an operation journal; MLT XML → `melt` | 44 | 5, one day | none; CI is lint + a smoke script |
| [vibeDN/NeuroCut](https://github.com/vibeDN/NeuroCut) · 0★ · MIT · Python | N-track editor held only in server memory; MLT XML → `melt` | 30 | 4, one day, AI-written | none, no CI |
| [zhitongblog/unflick](https://github.com/zhitongblog/unflick) · 0★ · MIT · Rust | a libmpv **player**, not an editor; "clip" is one `-c copy` extraction | 101 | 135 | real CI on four OSes |

**None addresses an edit by transcript word** — every one is element id plus
seconds. vidcut's `shared/src/types.ts` documents the failure this avoids:
reordering clips moved overlay anchors to the wrong clip, fixed by requiring
ids to be reused rather than by tracking content.

**None checks a render against the intent.** mcpCut is the closest: it knows
melt exits 0 on failure (cites MLT #547) and `_verify_mlt_output` raises on a
short or missing file — a duration floor, not a content check. NeuroCut checks
only that the file exists and is non-empty; CutPilot's `visual-qa-engine.mjs`
runs `blackdetect`/`freezedetect`; vidcut's `preview-vs-export.mjs` compares
ink boxes between preview and render, which is geometry, not words or frames.
So `verify` and `check_frames` stay proofcut's, stated as before.

**Both melt-based servers keep the first-audio-stream trap unguarded**:
mcpCut's `_probe_clip_audio` asks only whether an audio stream exists, and
NeuroCut's `probe.py` keeps the first. NeuroCut does get `out = nframes - 1`
right.

**What they have that proofcut does not:**

- *Blur-fill* for an aspect mismatch (vidcut) — a blurred copy behind the
  frame rather than a crop or bars; the usual vertical-video treatment.
- A `batch` tool bundling several ops into one call (NeuroCut) — fewer agent
  round trips, the cost docs/plans/MCP.md measured.
- SSRF-guarded URL import (mcpCut, `app/net/egress.py`) — proofcut imports
  only local paths.
- A pre-export readiness audit (CutPilot, `director-acceptance-engine.mjs`) —
  near `finish_report` already.
- A job stamped with a hash of the tool schema it was queued under
  (ffmpeg-mcp-video-editor, `tools/registry.py`), so a stale worker refuses.
- A project store with a lock directory that expires, a revision counter for
  optimistic concurrency and `.bak` recovery on a corrupt read (CutPilot,
  `project-store.mjs`) — the same ground as `Project._manifest_stamp`,
  with recovery added.
- Tool arguments checked against the real function signature (mcpCut,
  `app/mcp/argspec.py`), the goal `_PARAM_DOCS` serves from the other side.
- Checksum-verified ffmpeg downloaded on first run, cross-platform from day
  one, a job queue with cancellation shared between MCP and its UI, and
  one-call MediaPipe `track_and_crop` (ffmpeg-mcp-video-editor). Its
  `captions.py` passes text to `drawtext` via `textfile=` with
  `expansion=none` and tests an adversarial string — irrelevant to proofcut's
  ASS path, but the right way if `drawtext` ever appears.
- Text-anchored seek (`search_transcript`/`seek_to_text`, Unflick) — the
  player's version of `locate`.
- Fonts fetched from Google Fonts on demand, and a render shared through a
  tokenized Cloudflare quick-tunnel link (NeuroCut, `run.sh`).
- A gapless two-`<video>` preview, word-highlight captions rasterized with
  Pillow and shared byte-for-byte between preview and export, and an
  `export_publish_package` for manual upload (vidcut).
- Breadth nobody here has asked for: free N-track placement, crossfades and
  arbitrary filter passthrough (NeuroCut); Remotion/JSX motion graphics,
  multicam sync, CapCut handoff and genre "director" presets (CutPilot); a
  cross-project asset library and a review-and-chat loop (vidcut).

**Claims the code does not back:** CutPilot's "review-first natural-language
edits" are a keyword matcher to fixed magnitudes (±15% speed, ±3 dB) unless
an LLM key is configured; mcpCut's "every export writes an `.mlt` sidecar"
holds only under the default `RENDER_ENGINE=mlt`. Glama's "32 tools" and 38★
for ffmpeg-mcp-video-editor disagree with its source (38) and GitHub (0★).
mcpCut pins `mcp>=1.28.1,<2.0`, so its `FastMCP` is real there — the v1 SDK,
not the v2 proofcut is on.

None is queued. Blur-fill and `batch` are the two worth a design note if
either is asked for.

## Stateless-ffmpeg MCP servers

Useful only as reference for tool naming and parameter conventions. None carries
edit state; each invocation is a one-shot ffmpeg call.

- [misbahsy/video-audio-mcp](https://github.com/misbahsy/video-audio-mcp) — 83★, MIT, Python, last pushed 2025-05
- [chandler767/mcp-video-editor](https://github.com/chandler767/mcp-video-editor) — 5★, Go, no license, last pushed 2026-02
- [hyepartners-gmail/vibevideo-mcp](https://github.com/hyepartners-gmail/vibevideo-mcp) — agentic editing plus a front-end editor

## Transcript-based editors (the UX prior art)

None of these are agent-driven; they are humans editing text to cut video. They
establish what the interaction *should* feel like.

- [wassgha/rescript](https://github.com/wassgha/rescript) — 630★, TypeScript,
  browser. transformers.js running `whisper-*_timestamped` (WebGPU, WASM
  fallback) in a worker; `pyannote-segmentation-3.0` ONNX for speakers; deletions
  become cut ranges; export via multi-threaded ffmpeg.wasm.
  **Notable:** they shipped drag-to-adjust word edges — direct field evidence
  that ASR word alignment alone is not accurate enough for clean cuts.
- [DataAnts-AI/CutScript](https://github.com/DataAnts-AI/CutScript) — 184★, MIT,
  TypeScript, local-first Descript-alike. Last pushed 2026-03
- [Ekaanth/OpenCut-AI](https://github.com/Ekaanth/OpenCut-AI) — Whisper
  word-level timestamps, one-click filler-word removal, silence detection
- [OpenScript](https://tryopenscript.vercel.app/) — same category

## Adjacent

- [mutonby/openshorts](https://github.com/mutonby/openshorts) — 2.9k★, MIT with
  a commercial-license exception on `cloud/`. Opus Clip alternative: long video →
  9:16 shorts, moment detection, face tracking, dubbing. Has an MCP server. Open
  core with a hosted tier — a different business shape, not a direct competitor.
  Relevant only for caption burn-in technique.
- [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)
  — 2k★, MIT. The other end of the finishing handoff. Note Resolve's scripting
  API deliberately withholds colour wheel/curve access.
- [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) —
  1.9k★, Apache-2.0, C++ with Python bindings.
- [Agent-Driven-Editing-2026](https://github.com/12georgiadis/open-source-cinema/blob/master/Agent-Driven-Editing-2026.md)
  — landscape writeup; independently lands on "OTIO is JSON, so an LLM can read
  and generate timelines directly" as the key insight.

## Corrections to earlier assumptions

Recorded so they are not re-derived, and so the reasoning that depended on them
can be found.

### The first sweep missed the conversational-editor field

The first sweep searched GitHub for proofcut's *architecture* (OTIO, MCP, ffmpeg
wrappers) and found micro-repos. A second sweep the same day searched the
*product space* ("edit by transcript", "AI video editor agent") and immediately
surfaced OpenChatCut (854★), browser-use/video-use (19.9k★), and
veedstudio/open-edit — including the one project that plausibly covers proofcut's
differentiators. Lesson for the next re-survey: search what a user would type,
not what the implementation contains. This also falsified the first sweep's
claim that decision-point frame composites were unique to proofcut's preview idea
(video-use ships them).

### "No FCPXML export" was a search failure

The second sweep read OpenChatCut's README and releases and concluded it had no
NLE export. The README names FCPXML in three places and the serializer is 387
lines of `src/export/fcpxml.ts`. Grep the repo for format names; a feature list
read at skim depth is not evidence of absence.

### auto-editor is no longer Python

`ae.nimble` at the repo root — it is Nim. PyPI is frozen at **29.3.1**; GitHub
ships **31.4.2** (2026-07-31). `pip install auto-editor` silently installs a
stale, diverged version. auto-editor must be treated as a subprocess dependency
with a version floor, like ffmpeg.

This falsified the plan's stack-decision rationale ("faster-whisper,
OpenTimelineIO, and auto-editor are all Python" → now 2 of 3).

### OTIO's editing algorithms are C++ only

`overwrite`, `insert`, `trim`, `slice`, `slip`, `slide`, `ripple`, `roll`,
`fill`, `remove` all exist in `src/opentimelineio/algo/editAlgorithm.{h,cpp}`
with C++ tests (`tests/test_editAlgorithm.cpp`). A repo-wide search for
`editAlgorithm` returns only C++ sources and CMakeLists — **there are no Python
bindings.**

Verified against the installed package, not just repo source — `dir()` on
`opentimelineio.algorithms` from OpenTimelineIO 0.18.1 on CPython 3.13.14 gives
exactly:

```
filter, filtered_composition, filtered_with_sequence_context, flatten_stack,
stack_algo, timeline_algo, timeline_trimmed_to_range, top_clip_at_time,
track_algo, track_trimmed_to_range, track_with_expanded_transitions
```

No `overwrite`, `insert`, `trim`, `slice`, `slip`, `slide`, `ripple`, `roll`,
`fill`, or `remove`.

Consequence: `cut_by_transcript` hand-rolls track surgery over Track/Clip/Gap
and `source_range`. Tractable for single-track cut-and-lift, but it is real work
and not a library call.

### The Python 3.12 pin's revisit condition was already met

Wheel availability as of 2026-08-06:

| Package | Version | CPython wheels |
|---|---|---|
| OpenTimelineIO | 0.18.1 | cp39–**cp313** (no cp314) |
| ctranslate2 | 4.8.1 | cp310–**cp314** (incl. free-threaded `cp314t`) |
| onnxruntime | 1.28.0 | cp311–**cp314** |
| av (PyAV) | 18.0.0 | `cp311-abi3` → covers 3.12/3.13/3.14 |
| tokenizers | 0.23.1 | `cp310-abi3` → covers 3.10+ |

OpenTimelineIO is the sole blocker on 3.14. Everything supports 3.13 — confirmed
by actually installing `opentimelineio` 0.18.1 and `faster-whisper` 1.2.1 (with
ctranslate2, onnxruntime, av, tokenizers) on CPython 3.13.14 and importing them,
rather than by reading PyPI metadata alone.

### "Daydream hands off finishing work, same as lucid would" was never checked

README.md pitched proofcut against Daydream from the first commit, and both survey sweeps skipped it
because neither searched outside GitHub — Daydream has no repo. Fetching daydreamvideo.com and its
docs directly (2026-08-07) shows a full NLE-style timeline editor with in-app watermark-free
rendering; NLE export to Premiere/Resolve/Final Cut is an optional extra, not the finishing path.
The README's "same as Daydream does" clause was false and was cut the same day (2c7d119); the
README no longer pitches proofcut against Daydream at all. See the Daydream section above.

## Convergent signals worth noting

- **Transcription backend.** Both auto-editor and clipwright chose whisper.cpp
  binaries over faster-whisper. Real signal, but proofcut stays with faster-whisper:
  in-process and pip-installable matters more here than raw throughput, and it
  keeps the dependency graph free of a second hand-managed binary.

  **Overturned 2026-08-07, when ASR was actually built.** proofcut shells out to
  an openai-whisper binary (`asr.py`), which is the same call auto-editor and
  clipwright made and against the reasoning above. Two things decided it. The
  box already had a working openai-whisper install and no faster-whisper one,
  so "pip-installable" bought nothing that wasn't already paid for. And
  in-process is a *cost* here, not a benefit: importing it drags torch and a
  GPU context into `proofcut status`, which never touches audio. The convergent
  signal was right and the counter-argument was theoretical.
- **Caption format.** kinocut ships word-timed ASS. Word-level highlighting is
  what burned-in captions are actually for, and SRT + ffmpeg `force_style`
  structurally cannot do it.
- **Persistent project state.** kinocut built it; auto-editor's lack of it is its
  clearest structural limit. Both point the same direction.
