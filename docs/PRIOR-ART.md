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

Re-run this survey before any major scope change. Last re-checked in full
**2026-09-16** — § The re-check before Show HN; the counts above it stay as
they were read. Seven more repos, two of them re-reads, were added
**2026-09-20** — § Seven repos a new stargazer had starred. OpenCut, the one
of those seven marked "to watch", was then read in full the same day, both the
rewrite and the classic app — § OpenCut, read in full. What is worth
harvesting from classic's UI, and the three measured reasons its tree cannot be
vendored into a Python wheel, are § What is worth taking, and what a copy would
cost.

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
and real MCP with typed skills. What it does **not** cover (re-checked
2026-09-16, still true; it now checks renders structurally — § The re-check
before Show HN):

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
Apple Silicon macOS Tahoe only, 11 commits (macOS arm64 and Windows x64 by
2026-09-16, with no default transcription provider — § The re-check before
Show HN)

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
(By 2026-09-16 they read and write OTIO-schema JSON at the edges, still not
as their model — § The re-check before Show HN.)

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
| [chaoz23/otio-diff](https://github.com/chaoz23/otio-diff) | 1 | Python | Structural diff between two OTIO timelines — added/removed/retimed/moved clips. CLI + MCP. **Directly useful to proofcut** as the "what did the agent just change?" primitive. Built natively 2026-09-16 over the undo history (HISTORY.md § `changes`: what the last edits did) |
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
So `verify` and `check_frames` stay proofcut's, stated as before — narrowed
four days later, when OpenChatCut's `verify_export` turned up (§ The re-check
before Show HN).

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
  The cheap half of the recovery is built (2026-09-20): a corrupt-manifest
  refusal names the newest readable snapshot to copy back
  (`Project._recovery_hint`), and restores nothing unasked.
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
  `export_publish_package` for manual upload (vidcut). The gapless preview was
  considered 2026-09-20 and is not queued: `player.js` sets `pictureVideo.src`
  per shot, so a cut to another asset stalls the window and never the render.
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
either is asked for. **`batch` was measured the same day and is not worth
it yet**: 12.8% of the trial turns could have been folded together, most
of what a batch would fold is already parallel calls, and each trial reads
a `plan` echo before applying it — HISTORY.md § Stop reaches the render,
and `batch` was measured.

## The re-check before Show HN — 2026-09-16

Every section above, re-read against current source, plus a new-entrant
sweep searched the way a user would type it (GitHub search, the MCP
registry, awesome-mcp-servers, Glama, HN and Product Hunt). Clones were
shallow and read, never run. Heads that day: auto-editor `1647365`, kinocut
`e593881`, OpenChatCut `8411023`, video-use `9575612`, open-edit `b470ebc`,
FableCut `21ec62f`, oh-my-cassette `4bebe25` (`main`), Diffusion Studio
`b312417`, splicedeck `1d6b31b`. Daydream and Cardboard have no source and
were read from their sites.

**The launch claim needs narrowing.** OpenChatCut now ships
`verify_export` (`assessExportQuality`, `src/export/quality.ts`). It probes
the rendered file for duration against the timeline (tolerance max(0.25 s,
2 frames)), resolution, fps (±0.5), missing streams, black and frozen spans,
long silences and clipping peaks, and it draws a contact sheet around edit
points. So "checks its own render" is no longer something only proofcut
does. What is still
proofcut's alone: **transcribing the render and diffing its words against
the cut** (`verify`), which nothing found does, and an **exact** frame count
against the timeline (`check_frames`), where OpenChatCut's check is a
duration tolerance. No match turned up in `src/export/` or
`server/plugins/export-qa.ts` for `transcribe`, searched both through the
output filter and around it. docs/plans/LAUNCH.md's title rests on the old
claim.

The other half of that check — **frozen and silent spans** — is one proofcut
lacks, and OpenChatCut and CutPilot both have. Measured 2026-09-20 on two
films: 13 of 14 frozen spans were cards and one was footage that is still on
purpose, so the finding is a span nothing explains, not a list
(docs/plans/RENDER-CHECKS.md).

What changed in the sections above:

| Project | Change | Evidence |
|---|---|---|
| OpenChatCut | ~140 MCP tools, not ~24, with a `toolExposure=progressive` mode and a bearer token by default; v0.2.14; an "offline edit session" runs reversible edits without the editor tab, **still inside the Electron process**, so still no CLI; still custom JSON, FCPXML only | `server/external-agent/offline-*.ts`, `src/agent/external-tool-shape.ts` |
| Daydream | four tiers now: Creator $20–25/mo, Pro $40–50/mo (was $16–19), unlimited MCP calls on every paid tier; homepage footer says "macOS and Windows" while `/download` still says Mac and the docs name no OS; MCP URL, no tool list, the "never uploaded" line and the three NLE exports are unchanged | daydreamvideo.com `/`, `/download`, `/pricing`; docs `connect-mcp.md`, `exporting.md` |
| auto-editor | 31.6.0 on GitHub, PyPI still 29.3.1. The multi-source paywall is **two gates**: a render degrades to 720x576 with a warning (`src/render/format.nim:145-155`), and an NLE export with more than one source refuses outright (`src/conductor.nim:496-501`). Still no MCP, no project state, `--edit word:` still a filter | |
| kinocut | repo renamed `KyaniteLabs/kinocut` (old URL redirects); 201 MCP / 173 CLI at tip (196/167 published, 1.15.1). **It now reads and writes OTIO-schema JSON** (`kinocut/multipliers/otio_io.py`, tools `video_otio_export`/`video_otio_import`), hand-rolled without the `opentimelineio` library, with its own IR carried in `metadata.kinocut_ir` — a bridge at the edges, not its model. QC is still signal-level (`watching/metrics.py`, `vision_qc.py`); its ASR is for dub consistency | |
| open-edit | **Windows x64 as well as macOS arm64** (`SETUP.md:21`); Tahoe is what CI tests, not a gate. **No default transcription provider** now, and local WhisperX is listed first (README:47-48) | |
| video-use | local whisper is now *rejected by name*: "Use hosted Scribe" (`SKILL.md:312-313`). Still no MCP | |
| OpenTimelineIO | still 0.18.1, still no `editAlgorithm` bindings, still the only package here with no cp314 wheel (ctranslate2 4.8.2, onnxruntime 1.30.0 and av 18.1.0 all have one) | |
| FableCut, oh-my-cassette | 7 commits since `6ed70b0`, still v1.7.0, still 8 tools and no headless export; Cassette still needs an account | |

The rest of the survey was re-checked and still holds: clipwright,
open-post-production, otio-diff, the stateless servers, rescript, CutScript,
OpenCut-AI, openshorts and davinci-resolve-mcp. Their stars moved, but none
added MCP or a render check, and none was archived.

**New since the survey:**

- **[diffusionstudio/editor](https://github.com/diffusionstudio/editor)**
  · 2,795★ · MPL-2.0 · TypeScript · created 2026-07-07. The nearest
  substantial neighbour. A browser canvas editor whose project is a
  folder of JSX, with a real `dapi` CLI and an MCP server. It was covered on
  HN and elsewhere in 2026-08. Its `check` tool (`packages/dapi/src/tools/check.ts`) is
  structural and says so: "without rendering … a scheduled clip can still
  render black … confirm suspicious spans visually." Its transcription
  (`media-transcribe.ts`) is for reading and captions. Edits address nodes
  by id, never by word.
- **DaVinci Resolve 21.1** (2026-09-08, Studio only) ships a native MCP
  server with 88 tools, read from press coverage, not source. It is a GUI
  with an MCP front end, and no coverage mentions a render check. A major
  NLE now has agent hooks out of the box.
- **Cardboard** (YC W26, closed, browser-rendered, [Launch
  HN](https://news.ycombinator.com/item?id=47170174)): a natural-language
  timeline editor with NLE XML export. It has no source to read, so it is
  Daydream's case again.
- **[ihuzaifashoukat/splicedeck](https://github.com/ihuzaifashoukat/splicedeck)**
  · 3★ · Apache-2.0 · Python. The closest in *shape*: nine verbs generated
  from one table for both CLI and MCP, local, with a hash-chained ledger.
  Its `verify` gates the plan before `deliver` encodes
  (`splicedeck/surface/verbs.py`); it is not a check of the render. By its
  own README, cutting by speech needs a binary it cannot obtain yet.
- Named and not read further: nanzhi84/Rushes (24★, local, GUI-only,
  versioned timeline, README-only read), FireRed-OpenStoryline (3.4k★,
  CLI+MCP, needs a cloud LLM key), burningion/video-editing-mcp (288★,
  2024, a client for a cloud service), krusemediallc/video-editor-agent (a
  skill pack whose QA is "pixels and dB, not intentions").
- Too new or thin to judge; look again next sweep: codeaashu/Rescript (11★,
  unrelated to wassgha's), RychagovSergey/intelligent-video-editor (1★, local
  VLM plus MCP), sstani-bgv/ai-montage (0★, three days old),
  awaismirza/yusaf-cut (2★, Mac GUI), danielbaldwin47/resolve-mcp (1★, pitched
  against Resolve's native server). A farm of near-identical 0-commit repos
  (`ai-capcut-pro`, `cupcat-video-editor` and five more) was skipped.
- Not editors: guimatheus92/mcp-video-analyzer (transcripts and OCR from
  URLs), and every `search=video` hit in the MCP registry (generation,
  download and marketing tools).

Stars that day, for the next re-check to diff against: OpenChatCut 1,865
(~915 commits), video-use 24,975, openshorts 4,749, FireRed-OpenStoryline
3,417, davinci-resolve-mcp 2,862, Diffusion Studio 2,795, OpenTimelineIO
1,982, rescript 892, FableCut 669, open-edit 658, burningion 288, CutScript
249, OpenCut-AI 222, oh-my-cassette 155, kinocut 151, video-audio-mcp 86,
auto-editor 5,228.

How the sweep searched, so the next one can repeat it: `gh search repos` for
"ai video editor agent", "video editing mcp", "edit video by transcript",
"claude code video editor", "text-based video editing", "video agent mcp",
"headless video editor agent", "whisper video editor agent" and "video render
verify", by stars and by recency (`--created ">2026-07-01"`);
`registry.modelcontextprotocol.io/v0/servers?search=video`;
awesome-mcp-servers' Multimedia section; Glama's `video editing` search; and
web searches for Show HN and Product Hunt launches. **A filtered `grep`
dropped two of kinocut's 201 `@mcp.tool(` lines** and counted 199, so every
count and every absence above was re-run around the shell's output filter.

## Seven repos a new stargazer had starred — 2026-09-20

A new star on the public repo led to that account's own starred list, which
held two competitors the survey had and five it lacked. Each was read from a
shallow clone, source before README, nothing run. The same pass re-read the
two already covered against § The re-check before Show HN's recorded heads.
Heads that day: OpenMontage `08e2151`, OpenCut `400f097` (the rewrite) and
opencut-classic `cf5e79e`, sentrysearch `acd5a00`, B-Roll-Finder `c1f7ff6`,
VoiceStudio `7c9e7a4`, voice-pro `7231384`, video-use `9575612`, Diffusion
Studio `57c3983`.

**No launch claim moves.** The narrowed one from 2026-09-16 — transcribing
the render and diffing its words against the cut, and an exact frame count —
holds against all seven. The nearest is OpenMontage's `final_review`, and it
never runs a transcription: `_compare_transcript_to_script` diffs a transcript
*file the caller supplies* against the script
(`tools/video/video_compose.py:2183-2200`, `2640-2649`), which is why its own
docstring says "Only runs when caller provides both". Its duration check
flags only past 25% of target and it samples four frames, "black" meaning a
PNG under 2000 bytes.

| Repo | Category | What matters against proofcut |
|---|---|---|
| [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) · 60.3k★ · AGPL-3.0 · Python + Remotion · created 2026-03-29 | Generator and orchestrator | 121 `BaseTool` classes, **no MCP server and no CLI** — an agent reads `AGENT_GUIDE.md` and calls a Python registry. Mostly provider tools (video, image, TTS, music, avatar). Four of its 13 pipelines cut the user's own footage (`talking-head`, `clip-factory`, `podcast-repurpose`, `screen-demo`), by silence removal or an agent's reading of a transcript; **no word-addressed edit, no retake handling.** State is per-stage JSON artifacts validated against schemas (`lib/checkpoint.py`), not a timeline, and there is no undo. Local faster-whisper by default |
| [OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) · 89.9k★ · MIT | Human CapCut clone, mid-rewrite | The repo the stars sit on is a **skeleton**: `README.md:11-22` says it is being rewritten, the editor route is "Coming soon", and MCP and headless mode are roadmap bullets. The working product is [opencut-app/opencut-classic](https://github.com/opencut-app/opencut-classic) (252★), a browser editor with local in-browser Whisper whose captions are spread evenly across a segment rather than aligned per word. No agent surface in either, no transcript cutting, no render check. **The one to watch**, because the roadmap names MCP and 90k stars is distribution none of the agent-facing editors has. Read in full later that day: § OpenCut, read in full |
| [ssrajadh/sentrysearch](https://github.com/ssrajadh/sentrysearch) · 4.5k★ · Apache-2.0 · Python | Footage retrieval | 30 s chunks, 5 s overlap, embedded *as video* (Gemini Embedding 2 by default; Qwen3-VL-Embedding locally, ~18 GB VRAM) into ChromaDB; a text or image query matches footage directly and the top hit is trimmed with ffmpeg. CLI only, no MCP, no timeline. **No retrieval evaluation anywhere** |
| [erfsalehi/B-Roll-Finder](https://github.com/erfsalehi/B-Roll-Finder) · 4★ · **no licence file** · Python | Cloud b-roll sourcing | Voiceover to shot list to stock and YouTube candidates to a Premiere XML. Almost all cloud (Groq, OpenRouter, Pexels, Gemini). Its library index embeds the *query text that fetched a clip*, not what the footage shows — a text-similarity index over words. No evaluation, no agent surface |
| [debpalash/VoiceStudio](https://github.com/debpalash/VoiceStudio) · 33.3k★ · AGPL-3.0 · Python | TTS and dubbing app | ~17 engines behind subprocess sidecars (proofcut's own pattern). **"646 languages" is the row count of one model's name-to-ID table** (`omnivoice/utils/lang_map.py`); its own docs say other engines differ. Dubbing never touches a timeline. **Default model's weights are CC-BY-NC**, so the default install is not commercial (`LICENSE-NOTICE.md:46`). 7-tool MCP, dubbing not among them. No take ranking and no runtime speaker-similarity check |
| [abus-aikorea/voice-pro](https://github.com/abus-aikorea/voice-pro) · 12.8k★ · GPL-3.0 (README says LGPL; unresolved) · Python/Gradio | Dubbing convenience app | Gradio only, no CLI or MCP. Dubbing is a sequential cursor, so one overrunning line pushes every later line late; no output check, no ranking. Different category |

**Re-checked, unchanged.**

- **video-use** — head still `9575612`, 0 commits since 2026-09-16, 25,156★.
  Every claim in § browser-use/video-use holds. **New detail, present at the
  same head:** `SKILL.md:91-99` has a self-eval step — a filmstrip at each cut
  boundary and the first and last 2 s, and `ffprobe` of the output's duration
  against the edit list, capped at 3 passes. It is a prompt instruction, not
  code, and it neither detects black or frozen frames nor transcribes the
  output.
- **diffusionstudio/editor** — 2,964★, MPL-2.0, 18 tools
  (`packages/dapi/src/catalog.ts:32-51`). **The head § The re-check before
  Show HN recorded, `b312417`, is in neither the repo's history nor its refs**
  — force-pushed away or mistyped — so the diff ran from v0.205.1
  (`5529819`, 2026-09-14): 16 commits, UI and chat refactors, `packages/dapi`
  untouched. Transcription is a cloud call (`media-transcribe.ts:28`;
  `whisper.ts` is only a caption-format decoder, so a grep for "whisper" there
  reads as local ASR and is not). `capture` draws contact sheets of the
  *composition*, not the output file (`capture.ts:14`), and edits still
  address nodes by id.

**Worth taking, as evidence and not as a decision** (PLAN.md decides):

- **Footage embeddings against `describe`'s text.** sentrysearch is the only
  thing here that skips the lexical step proofcut measured at 2 of 25 human
  picks — and it brings no number of its own. **Tested 2026-09-20, and it
  failed**: shortlist-of-3 at 10 of 25 against a bar of 15, top-1 at 1, no
  better than chance (docs/plans/FOOTAGE-EMBED.md; HISTORY.md § Embedding the
  footage did not pick the b-roll). Its cheap parts stand
  alone: a model-free still-chunk skip (JPEG sizes of three frames at a 0.98
  ratio, `chunker.py:204-296`), which could skip `describe` windows on static
  footage, and an image as the query, which a `footage_sheet` tile could be.
  The skip is held (2026-09-20) until a static clip costs enough windows to matter.
- **B-Roll-Finder's learned trims.** Re-importing the user's edited XML records
  their in and out points per clip (`clip_library.py:490-512`) — human-pick
  signal, the thing the 25-pick measurement used. Its VLM prompt that may
  answer "none" (`prompts/visual_verify.txt:19-33`) is the honest half of a
  reviewing pass. Its whole-timeline "executive producer" pass is the kind
  proofcut measured making picks worse (13 → 10), with no evidence here either
  way. Held (2026-09-20) for a design note first: it is a measurement problem,
  and the bar is 15 of 25 against embeddings' 10.
- **VoiceStudio's dub timing, for `vo_synth` — declined 2026-09-20**, because
  `vo_synth` splices in and lets the edit grow, so there is no slot to fit
  (HISTORY.md § Embedding the footage did not pick the b-roll). A duration predictor
  calibrated from the same voice's own characters-per-second, run *before* the
  GPU is spent (`duration_planner.py`); an overrun split between audio
  speed-up and picture slow-down under hard caps (`fit_planner.py`); a
  per-line WER drift score against the target, opt-in and never fatal
  (`dub_qc.py`) — proofcut's own report-not-gate stance, arrived at
  separately.
- **OpenCut's integer ticks** (120,000 per second, dividing 24/25/30/60
  exactly, `rust/crates/time/src/media_time.rs:10`) and its even-spread
  captions as the failure `verify` is built to catch.

**Not confirmed.** OpenCut's and opencut-classic's ages and commit counts
(both clones were shallow); OpenMontage's real commit count (API only, ~449)
and whether its `final_review` is enforced or advisory; the F5-TTS weights'
non-commercial licence, which rests on VoiceStudio's own competitive notes
and not on voice-pro's source; sentrysearch's 0.41 default confidence
threshold, whose derivation the read did not find; and every cost or latency
figure the READMEs quote.

## OpenCut, read in full — 2026-09-20

Asked the same day § Seven repos a new stargazer had starred marked OpenCut
"the one to watch": is it a better proofcut, and is it about to do everything
proofcut does. Two readers, one per repo, source before README, every grep that
mattered re-run around the shell's output filter; **nothing was installed, built
or run**, so every "works" below is what the code says. Heads: the rewrite
(`main`) `400f097`, last commit 2026-08-01, repo pushed 2026-08-10; classic
`cf5e79e`, 2026-05-17. Clones are kept at
`~/proofcut-work/spikes/opencut-read/{new,classic,classic-deploy}`.

**No launch claim moves, and the "it is about to overshadow proofcut" reading
is not supported.** The rewrite is an empty scaffold; the classic app is a real
editor for a person's hands with no agent surface at all.

**Three names for two codebases, and the READMEs disagree about which is live.**
`OpenCut-app/OpenCut` (`main`, ~90k★, ~97 contributors) is the rewrite.
`opencut-app/opencut-classic` (~253★, issues disabled) calls itself "Legacy…
archived"; it was created 2026-05-16 as a copy with its history, and the same
code is `main`'s `deploy` branch. `main`'s README says classic is "the one to
reach for today" and that opencut.app still runs it, and new.opencut.app is the
rewrite. **Not verified that opencut.app serves this commit**: a fetch returned
only a landing page with a "Try early beta" link.

### The rewrite (`main`) — a scaffold

127 files, about 3.1k lines of hand-written code beside ~6.8k of vendored
shadcn components. One person effectively owns it, and its README says outside
contributions are not accepted "while the architecture is being designed".

| Piece | What is in the tree |
|---|---|
| `apps/web` | TanStack Start on Cloudflare Workers. `/` renders "hello world!"; `/editor` renders "Coming soon." A fetch of new.opencut.app returned only a page title and `/editor` a 404 |
| `apps/api` | Elysia on a Worker, 15 lines: `GET /`, `GET /health`, `POST /echo`. No auth, database, storage or queue |
| `apps/desktop` | GPUI window with four panels that only draw their names (Browser, Preview, Inspector, Timeline) and a UI-primitive kit; its README: "Right now this is just a window that opens" |
| `rust/`, `docs/`, `packages/` | **Not on `main`.** `Cargo.toml` lists only `apps/desktop`, `crates/*` commented out |

**Every item in its README's "What's coming" is README-only**: an Editor API,
plugins, an MCP server, headless mode, a scripting tab, a Rust core. Searched
for keyframe, transcri, whisper, ffmpeg, wasm, webgpu, webcodecs, indexeddb,
opfs, undo, export, plugin, mcp and headless across `apps/`: no hits outside
unrelated identifiers and the changelog. Its `changelog/` describes the
*classic* app's masks, graph editor and stickers and reads as rewrite status
if taken at face value. Tests: 4 Rust unit tests, none in the web package.
CI runs `moon ci` on three OSes.

**The Rust core is the classic app's, on the `deploy` branch**, and a
project-structure section copied from classic's README describes it as though
it were the rewrite's. A session here said so wrongly, mid-survey, and had to
correct it: read that structure section against `main`'s tree.

### Classic — a real browser editor, no agent surface

Browser-only and local-first. About 91k lines of TypeScript (Next.js 16, React
19) and ~4.8k of Rust (`compositor`, `masks`, `time`, `gpu`, `effects`, `wasm`,
`bridge`), migrating business logic into Rust per its `AGENTS.md`.

- **Editing.** Tracks of video, text, audio, graphic and effect; elements of
  video, image, audio, text, sticker, graphic and effect. Split, trim, move,
  duplicate, group move/resize, copy/paste, ripple, snapping (10px), undo/redo
  as a command stack, multiple scenes and bookmarks. **Keyframes** on
  transform, opacity, volume and text colours (linear, hold, bezier, with a
  graph editor). **Masks**: about nine shapes plus a freeform path, feathered
  on the GPU. **17 blend modes.** Text with Google Fonts. Constant-rate speed
  0.01–5x with pitch kept by SoundTouch — no ramps, no reverse. Audio
  waveforms, volume −60 to +20 dB (keyframable), a master limiter; **no audio
  fades found, by grep only**. Canvas presets (16:9, 9:16, 1:1, 4:3, custom),
  24/25/30/60/120 fps, platform safe-zone guides, .srt and .ass import.
- **Thin or stubbed.** The effect registry holds **one** effect, Gaussian blur.
  Transitions and the adjustment tab are "coming soon" panels, freeze frame is
  a disabled button, the stickers "logos" provider returns nothing, and the
  songs library answers 501.
- **Transcription** runs in the browser (transformers.js, Whisper ONNX: tiny,
  small by default, medium, large-v3-turbo; 9 languages plus auto), over the
  **whole timeline's mixed audio** in 30 s chunks. **Timestamps are
  segment-level only, and `buildCaptionChunks` spreads each segment's words in
  groups of three evenly across it** — the failure `verify` is built to catch,
  confirmed in source. One user issue calls the transcript inaccurate.
- **Export is all client-side.** A Rust/WASM wgpu compositor (WebGPU, WebGL2
  fallback) feeds WebCodecs through mediabunny: MP4 (H.264 with AAC, Opus if
  AAC is unsupported) or WebM (VP9 with Opus); four quality presets and no
  numeric bitrate; resolution is the canvas, with no export-time picker; no
  ProRes, GIF or image sequence. **No ffmpeg.wasm**, so no GPL codec build. The
  mixed audio is one in-memory buffer and the muxed file another, with no
  length cap in code — users report out-of-memory crashes (#628, #656). No
  server-side or headless render.
- **Storage.** Project JSON in IndexedDB, media in OPFS, a typed schema at
  version 31 with 30 migration files, 20 of them tested. **No project-file
  save/load, no FCPXML, OTIO or EDL, and no SRT export** (#719 asks for a
  project export).
- **Backend, all of it.** Four routes: auth (better-auth, but `signIn`,
  `signUp` and `useSession` are never called from the UI; a schema comment says
  "we don't have any auth flows currently"), a feedback box, health, and a
  Freesound search proxy holding the key. Postgres holds only auth and
  feedback rows; Redis only rate limits. **Projects and media never leave the
  browser.** The fal.ai sponsor is a logo entry, not an integration. What
  does leave: feedback text, sound-search queries, Whisper weights fetched
  from Hugging Face on first use, Google Fonts, a Databuddy error-tracking
  script and Vercel's bot check. A first read that took the database for a
  project store was wrong.
- **Self-hosting is not zero-config.** `env/web.ts` zod-parses about eight
  required variables at import; Compose supplies Postgres and Redis and
  placeholders satisfy the Freesound and blog keys, at the cost of the sound
  library and the blog. The editor itself needs no cloud.
- **Agent surface: none.** No MCP, plugin or scripting hook anywhere in the
  classic tree; its "actions" registry is keybinding triggers only. #778 asks
  for a headless render SDK, so it does not exist.
- **Maturity.** 1,567 commits, ~95 contributors, one of whom made two thirds.
  By month: 406 and 632 in 2025-06 and -07, a lull through 2025-12 (2 in
  December), then a Rust-migration surge, 86 / 108 / 130 in 2026-02 to -04, and
  21 in May before the archive. 30 web test files (20 are storage migrations)
  and 11 Rust tests; **CI's test step is `echo "No tests implemented yet"`
  with `continue-on-error`**, so none of them run there. Over 300 issues on the
  main repo, mixing both eras: the most-commented are a Chinese translation,
  the rewrite's tracking issue, out-of-memory, text that cannot be dragged, and
  `db:migrate` failing; by title keyword, UI/timeline/text bugs ~60,
  memory/crash/lag ~27, setup/DB/Docker ~21, export ~20.
- **Licences.** MIT. Dependencies worth a look before borrowing anything:
  `soundtouchjs` LGPL-2.1, `mediabunny` MPL-2.0. Whisper weights carry their
  own licences, **not checked**.

### Against proofcut

- **OpenCut's classic is ahead** on what a person does with a mouse — masks, a
  keyframe graph editor, blend modes, a GPU compositor, a browser tab with no
  install — and on reach. That is FableCut's gap over proofcut again
  (§ FableCut, read), and again not one proofcut is chasing.
- **proofcut is ahead on everything an agent needs**: word-addressed edits,
  a headless render, a check of the output, NLE export, and captions timed to
  the words rather than the segment. The classic app has none of the five,
  and a **hand-editor with an MCP layer added is still that editor**, which is
  why even a shipped rewrite would compete with `proofcut web` before it
  competed with the pipeline.
- **The real risks are the two the survey already names**: distribution (90k
  stars is reach no agent-facing editor has) and "good enough" beating "exact"
  for someone who never needed a word diff. Neither is answered by code.
- **Convergent, not borrowed**: platform safe-zone guides (`graphics.SAFE_ZONES`
  is proofcut's report-only version), and a versioned, tested schema migration
  chain.

**Not queued.** Its keyframe graph editor, masks and blend modes are
hand-editing breadth, in the class of § Glama's related servers' "breadth
nobody here has asked for". Its in-memory export is the failure a `melt`
render to disk does not have.

### What is worth taking, and what a copy would cost

Asked the same day, after the read: evaluate classic's web UI and copy it into
proofcut if it is good. **The design is worth harvesting; the tree is not
vendorable**, and the licence is the one thing that does not block it.

**MIT into Shield is the permitted direction** — classic is MIT, so a file
lifted with its copyright notice kept ships under proofcut's own terms with no
relicensing. The reverse is what is closed. Two dependencies in its export path
are copyleft and travel with anything borrowed from it (`soundtouchjs`
LGPL-2.1, `mediabunny` MPL-2.0), and the Whisper weights' licences are still
unchecked.

Three measured costs of a wholesale copy:

- **They are two different kinds of software.** `src/proofcut/web/` is **15,181
  lines** of vanilla ES modules, CSS and HTML — no `package.json`, no
  `node_modules`, no build step, ten native imports in `app.js`. Classic's
  `apps/web` is Next.js 16 and React 19 on bun, with Radix UI, drizzle over
  Postgres, Upstash Redis, better-auth, OpenNext-on-Cloudflare, `opencut-wasm`
  and motion behind it — about 91k lines of TypeScript. Taking the UI means
  taking the stack.
- **proofcut ships as a wheel, and `web/` is static files inside it.** A Next
  build wants a node toolchain at install time, which no Python wheel carries —
  against an install story (`proofcut setup`, docs/plans/INSTALL.md) built this
  month to remove exactly that kind of step.
- **It is the implementation, and proofcut's must not be.** Project JSON in
  IndexedDB, media in OPFS, undo as a client-side command stack, a schema at
  version 31: there is no server-side truth for it to be a client of. Copying
  that UI copies a state model that *decides*, against CLAUDE.md's rule that the
  web UI is a third client and never a third implementation — and the browser
  and the CLI would stop agreeing about what the film is.

**Worth harvesting as design**, reimplemented in proofcut's own JS against
`ops`, never vendored:

- **10px timeline snapping**, read against `snapTolerance()` and the rule that a
  hit target smaller than the tolerance resolves to nothing.
- **A curve editor for a window's `interp`.** This is the one item that refines
  the **Not queued** paragraph above rather than agreeing with it: OpenCut's
  linear/hold/bezier graph over transform, opacity, volume and text colour *is*
  hand-editing breadth, but proofcut already stores eased curves
  (§ Eased slides and event-addressed windows) and has no way to draw one. Re-ask
  it as "a graph for the easings already in the manifest", never as their feature.
- **Multiple scenes and bookmarks** — no equivalent here.
- **Drawn platform safe-zone guides**, which is the drawn half of the data
  `graphics.SAFE_ZONES` already holds report-only. **Built 2026-09-20**, and
  the one item here that needed no verdict on how OpenCut feels in the hand,
  because the geometry was already proofcut's (HISTORY.md § The safe-zone
  guide, drawn).
- **Its media browser and inspector layout**, against the rail's three tabs.

**Not worth taking**, beyond the Not queued list: its export is in-memory and
OOMs on real files (#628, #656), which is the failure a `melt` render to disk
does not have; and its captions spread each segment's words evenly across it,
which is the defect `verify` is built to catch. The workspace redesign and the
look pass are both recent and approved, so **harvest mechanics, not chrome**.

**A live check the source read did not make** (GitHub API, 2026-09-20): `main`'s
last commit is **2026-08-01** and the repo was last pushed 2026-08-10 — fifty
days still — at 89,999 stars and 378 open issues. Its README declines outside
contributions "while the architecture is being designed", which answers the
collaboration question without anyone having to ask it.

**Not confirmed.** Whether any of these interactions is good in the hand:
nothing was run for this either, and the cheap evaluation is opencut.app in a
browser rather than a self-host, which wants about eight environment variables,
Postgres and Redis.

**Re-check triggers for the next sweep**, so it looks for something rather than
re-reading everything: `/editor` on `main` stops being a stub; a `rust/` or
`crates/` directory appears on `main`; the README's MCP, headless or Editor API
bullets acquire code; new.opencut.app serves more than a title; the two READMEs
stop disagreeing about which app is live. Stars that day: `OpenCut-app/OpenCut`
89,994, opencut-classic 253.

**Not confirmed.** That opencut.app serves the classic commit; the running
behaviour of either app; classic's export at any length or resolution (the
memory limits are issue reports); the audio-fade absence beyond a grep; the
Whisper weights' licences; issue counts, which are title-keyword matches over a
repo that mixes both eras; and the rewrite-era commit count, since only the 30
most recent commits were listed.

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
