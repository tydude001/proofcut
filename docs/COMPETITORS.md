# Competitors: the agent-driven video editing landscape

Who else builds this, read from their source and re-read as they move. This
file is the evidence, not the decision: the conclusions it supports live in
[PLAN.md](PLAN.md), which is authoritative. Star counts, versions and heads
are snapshots dated where they appear, and go stale. Re-run the survey before
any major scope change; § The passes records how each sweep searched, so the
next one can repeat it.

Named `PRIOR-ART.md` until 2026-09-28, when it was renamed for what it had
become. The survey that began it was prior art in the planning sense, and
auto-editor, a dependency as much as a rival, still reads that way. Every
citation moved with it except docs/plans/RENAME.md's, whose counts were
measured under the old name, and commits before the rename, which keep it.
Restructured the same day from a log of passes into one entry per project,
and every entry that rested on a README was read from source
(§ Every entry from source).

## How to read an entry

Each entry opens with a **Source read** line: the date, the commit read, and
how deep. "Source read" means a clone read source before README, with claims
cited to file and line and every count and absence re-run around the shell's
output filter. Nothing in this file was installed or run unless the entry
says so (auto-editor, and OpenCut classic driven in a browser). A closed
product says **Source read: none possible** and names what it was read from
instead; its claims cannot be checked the way the others' were.

Entries are grouped by what they are, nearest competitors first. Where a
section was cited from elsewhere in the repo by a heading, the heading's
opening words are kept so the citation still resolves.

## Where proofcut stands (2026-09-28)

What proofcut does, and who comes nearest to each. The launch claims are the
first two rows.

| proofcut | Nearest found | How near |
|---|---|---|
| `verify` transcribes the whole render and diffs its words against the cut | video-editor-agent; resolve-mcp; clipwright | video-editor-agent checks each seam with the *source's* word timings and transcribes short windows of the render around suspect seams, arguing the whole render is too wobbly to trust. resolve-mcp's `virtual_transcript` reads the cut's words *before* the render. clipwright recommends transcribing the render, for captions. Nobody diffs a whole render's words against the cut |
| `check_frames` counts the render's frames exactly against its timeline | hyperframes; davinci-resolve-mcp | hyperframes rejects a render more than one frame short, and passes when the probe has no count. davinci-resolve-mcp compares two renders' frame counts, and a timeline's against a supplied reference. OpenChatCut, Rushes, cutible, ai-montage and video-editor-agent check duration within a tolerance |
| Edits addressed by transcript word index | palmier-pro; the transcript editors | palmier-pro's `remove_words`, over MCP, alive only with its macOS GUI open. rescript, CutScript, OpenScript, yusaf-cut and ai-montage do it for a person's hands. OpenChatCut's `delete_text` takes a phrase and does not cut video |
| Headless, with a CLI twin for every MCP tool | OpenChatCut's `occ`; clipwright; cutible; kinocut; splicedeck | `occ` edits and renders headless, but cannot transcribe or check its render without the app. The others are headless and have none of word addressing, persistent undo and a render check together |
| OTIO as the source of truth | clipwright | Its project state is an OTIO file behind 25 MCP tools; no undo, no word addressing, no render check |
| Frozen and silent spans in a render | OpenChatCut, CutPilot, Rushes, cutible, video-editor-agent, davinci-resolve-mcp | **proofcut lacks this** (docs/plans/RENDER-CHECKS.md). Rushes and video-editor-agent filter findings through what the plan declared intentional |

The risks that are not code: distribution (OpenCut's 90k stars, video-use's
27k, FableCut's listings) and "good enough" beating "exact" for someone who
never needed a word diff.

## At a glance

Stars on 2026-09-28 from the GitHub API. "Read" is the latest source read;
a dash means no source exists.

| Entry | What it is | Licence | ★ | Read |
|---|---|---|---|---|
| § OpenChatCut | Electron NLE, MCP + `occ` CLI | AGPL-3.0 | 2,040 | 2026-09-28 `bb2a4f7` |
| § Daydream | macOS NLE, local MCP | closed | – | – (site, 2026-09-16) |
| § FableCut | browser NLE, `project.json` over MCP | MIT | 695 | 2026-09-12 `6ed70b0` (head 2026-09-16) |
| § kinocut | MCP + CLI, 201 tools | Apache-2.0 | 175 | 2026-09-16 `e593881` |
| § Diffusion Studio | browser canvas, `dapi` CLI + MCP | MPL-2.0 | 3,129 | 2026-09-20 `57c3983` |
| § palmier-pro | macOS editor, MCP while GUI open | GPL-3.0 snapshot | 14,482 | 2026-09-28 `eeafde2` |
| § video-use | coding-agent skill, cloud ASR | MIT | 27,506 | 2026-09-16 `9575612` |
| § open-edit | CLI + skill over a closed renderer | Apache-2.0 + Shield binary | 966 | 2026-09-28 `dd7913b` |
| § oh-my-cassette | MCP client of a hosted agent | MIT | 156 | 2026-09-28 `4bebe25` |
| § splicedeck | nine verbs, CLI + MCP | Apache-2.0 | 3 | 2026-09-16 `1d6b31b` (unchanged 09-28) |
| § Rushes | conversational editor, internal agent | none | 24 | 2026-09-28 `08b05f5` |
| § video-editor-agent | Claude Code skill pack + QA | none | 19 | 2026-09-28 `493535e` |
| § cutible | montage engine, MCP + CLI | MIT | 1 | 2026-09-28 `054bdff` |
| § OpenMontage | generator, Python registry | AGPL-3.0 | 61,742 | 2026-09-20 `08e2151` |
| § FireRed-OpenStoryline | montage node pipeline over MCP | Apache-2.0 | 3,452 | 2026-09-28 `c9e9452` |
| Cardboard, Resolve 21.1 (§ Closed products) | NL editor; NLE with MCP | closed | – | – (site, press) |
| § auto-editor | headless NLE, render backend | Unlicense | 5,385 | 2026-09-16 `1647365` |
| clipwright (§ The OTIO + MCP niche) | OTIO-state MCP suite | MIT | 2 | 2026-09-28 `7665374` |
| open-post-production (same) | documentary retrieval blueprint | MIT | 4 | 2026-09-28 `c0723c6` |
| otio-diff (same) | timeline diff, CLI + MCP | Apache-2.0 | 2 | 2026-09-28 `d9c023b` |
| vidcut, CutPilot, ffmpeg-mcp-video-editor, mcpCut, NeuroCut, Unflick (§ Glama's related servers) | small MCP editors, one player | mixed | 0–1 | 2026-09-16 |
| video-audio-mcp, mcp-video-editor, vibevideo-mcp (§ Thin ffmpeg servers) | ffmpeg wrappers | mixed | 5–217 | 2026-09-28 |
| § intelligent-video-editor | VLM-described library, MCP | GPL-3.0 | 1 | 2026-09-28 `0fc7a83` |
| § OpenCut | human browser editor, rewrite pending | MIT | 90,869 | 2026-09-24 `e668010`; classic 2026-09-20 `cf5e79e`, driven 09-26 |
| § rescript | transcript editor | PolyForm NC | 921 | 2026-09-28 `4d6f295` |
| CutScript, OpenCut-AI, OpenScript, codeaashu/Rescript, ai-montage, yusaf-cut (§ Transcript-based editors) | transcript editors | mixed | 0–260 | 2026-09-28 |
| § hyperframes | HTML-to-video renderer | Apache-2.0 | 56,154 (10-03) | 2026-10-03 `ce08f204` |
| § Remotion | React-to-video framework | custom (company licence past 3 staff) | 61,661 (10-03) | 2026-10-03 `e385a83` |
| sentrysearch, B-Roll-Finder (§ Footage retrieval) | footage search | Apache-2.0; none | 4,525; 4 | 2026-09-20 |
| VoiceStudio, voice-pro (§ Voice) | TTS and dubbing | AGPL-3.0; GPL-3.0 | 44,377; 12,955 | 2026-09-20 |
| § openshorts | shorts pipeline, MCP | MIT + commercial `cloud/` | 5,772 | 2026-09-28 `29c54fa` |
| davinci-resolve-mcp, resolve-mcp (§ DaVinci Resolve drivers) | Resolve over MCP | MIT; none | 3,221; 1 | 2026-09-28 |
| mcp-video-analyzer, video-editing-mcp (§ Analysis and cloud clients) | analysis; cloud client | MIT; none | 79; 290 | 2026-09-28 |
| OpenTimelineIO | the timeline library | Apache-2.0 | 1,991 | installed package, 2026-08-06 |

## Agent-facing editors

Products whose pitch is proofcut's pitch: an agent edits a real timeline. The
nearest come first.

### OpenChatCut

[0xsline/OpenChatCut](https://github.com/0xsline/OpenChatCut) · AGPL-3.0 ·
TypeScript (Electron / React / Remotion) · 2,040★ on 2026-09-28 (854★ and
338 commits on 2026-08-06, when v0.1.9 shipped a macOS dmg for arm64 and x64,
a Windows exe and a **Linux x86_64 AppImage**, unverified on this box)

**Source read** 2026-09-28 at `bb2a4f7` (v0.2.15), about 204k lines of
non-test TypeScript and 81k of `*.verify.ts`. Earlier reads: 2026-08-06
(README and releases, then `src/export/fcpxml.ts`), 2026-09-16 at `8411023`.
Nothing installed or run.

Missed by the first sweep. A local-first conversational editor: agents,
built in or external over MCP, edit a multi-track timeline with word-level
transcripts, speaker labels, linked captions and undo. Projects persist to
`~/.openchatcut` as JSON.

- **MCP.** Streamable HTTP at `/api/external-mcp/mcp`, served by the app's
  own Node server (`server/plugins/external-agent.ts:141`), with a bearer
  token kept in `~/.openchatcut/mcp-token` (`server/mcp-token.ts:20-30`). No
  stdio transport. The generated catalog
  (`assets/agent/openchatcut-tool-schemas.json`) holds 122 edit and 23
  read-only tools; five control and six session tools bring it to about 140
  (it was ~24 skills on 2026-08-06, at `localhost:5199/api/external-mcp/mcp`,
and v0.2.14 on 2026-09-16). A `toolExposure=progressive` mode
  exists but is **opt-in**: `requestedMcpToolExposure` returns `'full'`
  unless a header or query asks otherwise (`mcp-tool-exposure.ts:49-57`). The
  2026-09-16 entry read it as the default.
- **Proposal workflow.** `begin_edit_session` / `review_edit_session` /
  `discard_edit_session` commit a draft atomically against a revision check,
  landing as one undo step, optionally gated on approval in the app
  (`cli/session.ts:1-12`, `65-101`). An editor that has the project open
  holds an ownership lock that blocks headless writes. A draft whose client
  disconnected is kept as a recoverable session (v0.2.15). That shape is
  arguably better for human-in-the-loop than raw tool calls, and
  docs/plans/GROUPED-UNDO.md was written off it.
- **A command line now exists: `occ`** (`package.json` bin; `cli/main.ts:1-3`:
  "Runs against the local project library with no app, no browser and no MCP
  session in the loop"). First committed 2026-09-17, announced in v0.2.15's
  changelog. It lists and creates projects, moves, trims, splits, removes and
  duplicates clips, calls the agent's headless tools, applies `edit --ops`,
  **renders** (`occ render --out cut.mp4`, through `renderTimeline` and
  Remotion, `cli/commands/render.ts:14`, `104`) and exports a JianYing draft.
  Writes need `--apply` and leave a restorable version. On 2026-09-16 an
  "offline edit session" already ran reversible edits without the editor tab,
  but inside the Electron process (`server/external-agent/offline-*.ts`,
  `src/agent/external-tool-shape.ts`); `occ` is what took it out of the app.
  **This retires the
  "no CLI, not headless" difference every earlier read recorded.** What still
  needs the browser: `transcribe_track`, `submit_export`, `verify_export`,
  generation and the undo tools. They are outside the offline whitelist
  (`src/agent/external-tool-policy.ts:41-92`), so a headless run can edit and
  render an already-transcribed project but cannot transcribe or check its
  render. Whether `occ render` needs a display server (Remotion drives its own
  Chrome) is not confirmed.
- **Speech.** Word-level. Local whisper.cpp in the desktop process
  (`desktop/native-asr-service.ts`) or transformers.js Whisper in a browser
  worker (`src/transcript/local-asr.worker.ts`); cloud AssemblyAI is the
  default (`server/plugins/transcription.ts:62`), with six other providers
  (`transcription-providers.ts:47-77`).
- **Addressing.** Clips and tracks by id, times in frames. `delete_text` takes
  a phrase `query`, not word indices, and its own description says that on a
  VIDEO clip "deleting its words cuts NOTHING"; it re-times audio clips only
  (`src/agent/tools/schemas/transcript-tools.ts:116-119`). Word indices appear
  in `manage_transcript fix` and in `set_play_order`, a word-index reorder
  (`:120-160`). **So the 2026-08-06 reading that it covers addressable
  word-level cuts does not hold for video**: to cut picture an agent uses
  `split_item` / `edit_item` in frames.
- **Interchange.** FCPXML 1.10 out, with a Resolve variant (`fcp_xml_resolve`) that adds
  `colorSpace` to `<format>` (`src/export/fcpxml.ts`); transcript-deleted words
  leave as separate `asset-clip`s, so the cuts survive, but transitions,
  effects, volume and titles do not cross, and motion graphics become a
  `<gap>` unless pre-rendered to ProRes. Captions leave as SRT. JianYing/CapCut
  drafts out. FCPXML and CMX3600 EDL in (`schemas/timeline-import-tools.ts:6`).
  **No OTIO** (a word-boundary grep for `otio|opentimelineio` over `.ts`,
  `.tsx` and `.md`, around the output filter, is empty).
- **Render check.** `verify_export` posts to `/api/export-qa`
  (`src/agent/tools/export-qa-tools.ts:60-90`); `assessExportQuality`
  (`src/export/quality.ts:257-347`) checks duration within max(0.25 s,
  2 frames), resolution, fps (warning at ±0.5), missing streams, black and
  frozen spans, silences of 3 s or more and peaks at or above −0.1 dBFS, using
  `blackdetect`, `freezedetect`, `silencedetect` and `volumedetect`
  (`server/plugins/export-qa.ts:135-144`), and draws a contact sheet around
  each cut (`quality.ts:350`). It does not transcribe the render and has no
  exact frame count.
- **Stack.** Electron + Node + Remotion, against a Python package and three
  subprocesses.

**Worth taking:** the draft-then-commit session with a revision check and an
ownership lock (proofcut's lock is docs/plans/PROJECT-LOCK.md); the boot set
plus `ToolSearch` progressive exposure (`mcp-tool-exposure.ts:37-47`), which
docs/plans/MCP.md reached separately; the before/after sheet at each cut.

**Against proofcut, 2026-09-28.** The differences left are that proofcut
transcribes and verifies headless (`occ` cannot), cuts picture by word
index, checks the render's words and exact frame count, and keeps OTIO as its
source of truth. Whether that remainder justifies proofcut was the go/no-go
question in [PLAN.md](PLAN.md), answerable only by running both on a real
recording; that trial ("cut words 30–45; keep take 2, drop take 1",
iteratively, over its MCP endpoint) has not been run here.

### Daydream

[daydreamvideo.com](https://www.daydreamvideo.com) · closed source · by
Pushie, Inc.

**Source read: none possible.** No public repo (confirmed: the only
`daydream*` GitHub orgs belong to unrelated products, including a same-named
real-time video-diffusion tool at daydream.live). Read from the site and
docs.daydreamvideo.com on 2026-08-07, in full on 2026-08-08, and again
2026-09-16. The observed product anatomy, workflows and design system live in
[docs/plans/DAYDREAM.md](plans/DAYDREAM.md) (capture method and its one limit
in that file's § How this was captured); this entry keeps only the competitor
evidence.

Neither 2026-08-06 sweep covered it, because neither searched outside GitHub.
The README named Daydream as proofcut's foil from the first commit without the
claim ever being checked.

- **A full desktop NLE, not a chat front end that hands off.** A multi-track
  timeline (V1/V2 video, A1/A2 audio, CC captions), an asset panel,
  properties and templates panels, frame-accurate scrubbing and direct
  transcript trimming. Watermark-free MP4 rendering happens in the app; NLE
  export is an extra. This falsified README.md's claim that proofcut does not
  build a desktop editor "same as Daydream does".
- **Platform.** macOS, Apple Silicon. The 2026-08-07 page title read
  "Download Daydream — AI Video Editor for Mac"; by 2026-08-08 the homepage
  read "AI Video Editor for Claude Code & Codex" and "for Mac" survived only
  on `/download`. On 2026-09-16 the homepage footer said "macOS and Windows"
  while `/download` still said Mac and the docs named no OS. No Linux build,
  so it cannot run on this box at all.
- **MCP.** Local HTTP, `http://127.0.0.1:7433/mcp`, no auth documented;
  `claude mcp add daydream --transport http http://127.0.0.1:7433/mcp --scope
  user`. Capabilities in prose only (import and transcribe, transcript cuts,
  b-roll search and placement, captions, motion graphics, export); the tool
  schema is unpublished, unchanged on every read. The in-app chat is Claude
  Code or Codex itself as a subprocess on the user's own sign-in, the
  mechanism proofcut's agent panel chose independently a day earlier (PLAN.md
  § The agent panel, in mechanism).
- **Export.** Separate XML for Premiere, XML for Resolve and FCPXML for Final
  Cut, referencing the original footage ("you may need to relink the media in
  your editor"). What survives the export is undocumented. No OTIO.
- **Pricing against privacy.** On 2026-08-07:

  | Plan | Price | Processing (b-roll search) | Transcription | MCP calls/mo |
  |---|---|---|---|---|
  | Free | $0 | 1 hr/mo | 1 hr/mo | 100 |
  | Pro | $16/mo annual, $19/mo monthly | 20 hr/mo | 10 hr/mo | 1M |
  | Business | custom | extended | extended | extended |

  By 2026-09-16, four tiers: Creator $20–25/mo, Pro $40–50/mo, unlimited MCP
  calls on every paid tier. Hour-metered transcription and search sit
  awkwardly beside "your footage stays on your device and is never uploaded or
  stored in the cloud" (docs, verbatim). Either the AI work is local and the
  caps are a subscription gate, or "never uploaded" covers only the raw
  footage and not transcripts or embeddings sent out for inference. The docs
  do not say which, and closed source means it cannot be checked. Read
  2026-09-16 from `/`, `/download` and `/pricing`, and the docs'
  `connect-mcp.md` and `exporting.md`: the MCP URL, the missing tool list,
  the "never uploaded" line and the three NLE exports were unchanged.

**Against proofcut.** Headless: no, a GUI fronting a local MCP server, the
same shape as OpenChatCut was. OTIO: no. Thin stack: unconfirmed, but the
surface matches OpenChatCut's scale more than auto-editor's. Addressable
ranges and persistent state: plausible from "edit the transcript to cut" and
unverifiable. The most prominent competitor by mindshare and the least
inspectable one: whatever confidence the OpenChatCut trial buys by running
the software, Daydream cannot offer.

### FableCut

[ronak-create/FableCut](https://github.com/ronak-create/FableCut) · MIT ·
JavaScript · created 2026-07-06 · 695★ on 2026-09-28

**Source read** 2026-09-12 at `6ed70b0` (v1.7.0, pushed 2026-09-11; 667★, 67
forks, 7 contributors), grep and reading, not a run. Re-checked 2026-09-16 at
`21ec62f`: 7 commits since, still v1.7.0, 8 tools and no headless export.

Found 2026-09-12 in awesome-mcp-servers' Multimedia Process section while
drafting the launch listings. A Premiere-style NLE in the browser whose whole
timeline is one `project.json`. An agent edits that document over MCP or
REST, and the open UI hot-reloads it over SSE, so a person watches the agent's
cut land. That is `proofcut web`'s agent pane from the other end: no
transcript addressing, no OTIO, render in the browser. It was already in the
official registry, the awesome list and Glama, the listing path proofcut was
about to take.

#### FableCut, read, and why proofcut does not merge with it

Asked 2026-09-12: is FableCut the same thing, is it better, should proofcut
merge into it.

**Same neighbourhood, a different product.** FableCut is a human's NLE with an
agent as co-editor; proofcut is an agent's editing toolkit with a window a
human watches through.

| | FableCut | proofcut |
|---|---|---|
| Agent surface | 8 MCP tools, mostly get/patch/set of `project.json`; clips placed in seconds | 93 `@_tool()` tools, each with a CLI twin, ranges addressed by transcript word |
| Speech | None in the editor. `examples/auto-captions/` turns someone else's STT word timestamps into karaoke text clips: captions, never addressing | whisper at import; cut, cue, caption and verify all resolve through words |
| Render | Browser compositor → server ffmpeg (`/api/export/begin`/`frame`/`audio`/`end`); its own CLAUDE.md: "the user previews/exports from the UI". No MCP export tool | Headless: auto-editor single-source, `melt` multi-source |
| Checking the output | None found against the plan | `verify` transcribes the render, `check_frames` counts it against the timeline |
| Stack | Node ≥18, zero npm deps, one 8,345-line vanilla `app.js` | Python ops layer plus whisper, the Kdenlive flatpak's melt, auto-editor |
| Licence | MIT | PolyForm Shield |

**Where it is ahead, measured rather than conceded:**

- **Distribution.** HN front page, the official registry, awesome-mcp-servers,
  Glama, a Discord, five README translations: the whole of
  docs/plans/LAUNCH.md, already done.
- **Hand editing.** Keyframes, transitions, marquee multi-select, on-monitor
  move/resize/rotate, multi-channel audio stems, in/out work area. proofcut's
  window has direct-manipulation gestures; it is not a Premiere.
- **Install.** `node server.js` against proofcut's three external binaries. Of
  everything here, this is the gap most likely to cost proofcut a stranger
  (docs/plans/INSTALL.md was written off this line).

**Where proofcut is ahead:** an unattended agent can cut, render and check its
own cut. FableCut's agent cannot render without a browser tab open, and
nothing compares what it rendered to what was meant. Word-addressed editing,
framing detection, the timeline-derived captions, cards and the TTS splice
have no counterpart, and its "any process that writes JSON edits the video"
design points away from them rather than toward them.

**Why not merge.** Three reasons, any one sufficient:

1. **Nothing ports.** A merge is proofcut's ideas rewritten into a single JS
   file on a different runtime; none of `ops.py`, whisper or the melt writer
   crosses over.
2. **The licence.** Code contributed there is MIT, which undoes the PolyForm
   Shield choice (HISTORY.md § The licence, chosen, and wiki `decisions.md`).
3. **The designs are opposites.** "The project file is the interface" lets any
   writer place anything; proofcut routes every mutation through one `ops`
   function and verifies the render. Blending them keeps neither guarantee.

**What to take instead:** its install story and its listing path, both named
in LAUNCH.md. Interop is the one bridge that makes sense (proofcut writing a
FableCut `project.json` so a word-cut film can be hand-finished there, the way
`import_edit` already reads a `.kdenlive`), and it is **not queued**: build it
when someone asks, not on speculation.

### kinocut, the cautionary tale

[KyaniteLabs/kinocut](https://github.com/KyaniteLabs/kinocut) (was
`KyaniteLabs/mcp-video`; the old URL redirects) · Apache-2.0 · Python ·
175★ on 2026-09-28 (101★ on 2026-08-06, 146★ on 2026-09-12, when it began
pitching "quality gates")

**Source read** 2026-08-06 (`kinocut/projectstore/`), 2026-09-12 at `faaecc2`
(the quality gate), 2026-09-16 at `e593881`. Grep and reading, not a run.

The closest neighbour by intent: local-first, MCP + CLI, "guardrailed video
editing for AI agents". **161 MCP tools and 140 CLI commands** at v1.11.1 on
2026-08-06; 201 MCP / 173 CLI at tip on 2026-09-16 (196/167 published,
1.15.1).

The tool count is the lesson. A surface that large degrades agent tool
selection and consumes context before any work begins. Its ROADMAP is dense
with governance apparatus (policy engines, release gates, human-only
acceptance items, "agents must not invent closed"): what an agent-built
project looks like after a year of unchecked accretion.

Two things it got right, arrived at independently:

- **Durable edit projects.** Content-addressed store, async render and
  resume, ordered events, workflow receipt lineage (`kinocut/projectstore/`).
  Two projects converging separately on persistent project state is decent
  evidence it is load-bearing rather than gold-plating.
- **Word-timed ASS captions**, shipped, alongside disfluency cuts and
  project-recipe export/replay.

**OTIO at the edges, not as the model.** On 2026-08-06 it had 3 incidental
OTIO hits and a JSON workflow engine over stateless ffmpeg, a weaker
timeline model. By 2026-09-16 it read and wrote OTIO-schema JSON
(`kinocut/multipliers/otio_io.py`, tools `video_otio_export` /
`video_otio_import`), hand-rolled without the `opentimelineio` library, its own
IR carried in `metadata.kinocut_ir`.

**Its quality gate scores signal levels** (read 2026-09-12): brightness,
contrast, saturation, colour balance, motion and loudness against fixed
ranges (`watching/metrics.py`, `vision_qc.py`). Its receipts are sha256
provenance. The one output-against-plan check is silence removal's duration,
within 0.15 s. Its ASR is for dub consistency. No path transcribes a render
against intended words or counts frames against a timeline
(`~/proofcut-work/spikes/launch-listings/LISTINGS.md` § kinocut's gate, read).
A filtered `grep` once dropped two of its 201 `@mcp.tool(` lines and counted
199; every count here was re-run around the filter.

**Worth taking: the Video Receipt**, per-operation JSON provenance with input
and output hashes, the ffmpeg version and a resume cursor. **Its input half
was built 2026-09-21**: each render-log line stamps the edit it read, and
`finish_report` says whether the last render is still the project's film
(HISTORY.md § A render knows which edit it was made from). The output hash
and tool versions were built 2026-09-28 (`renderlog.output_digest`,
`renderlog.tools`); the resume cursor is not taken, because a render here is
one melt run.

### Diffusion Studio

[diffusionstudio/editor](https://github.com/diffusionstudio/editor) · MPL-2.0
· TypeScript · created 2026-07-07 · 3,129★ on 2026-09-28 (2,964★ on
2026-09-20)

**Source read** 2026-09-16 (recorded head `b312417`, which on 2026-09-20 was
in neither the history nor the refs: force-pushed away or mistyped), and
2026-09-20 at `57c3983`, diffed from v0.205.1 (`5529819`, 2026-09-14): 16
commits, UI and chat refactors, `packages/dapi` untouched.

The nearest substantial neighbour on 2026-09-16, covered on HN in 2026-08. A
browser canvas editor whose project is a folder of JSX, with a real `dapi` CLI
and an MCP server of 18 tools (`packages/dapi/src/catalog.ts:32-51`).

- Its `check` tool (`packages/dapi/src/tools/check.ts`) is structural and says
  so: "without rendering … a scheduled clip can still render black … confirm
  suspicious spans visually."
- `capture` draws contact sheets of the *composition*, not the output file
  (`capture.ts:14`).
- Transcription is a cloud call (`media-transcribe.ts:28`), for reading and
  captions; `whisper.ts` is only a caption-format decoder, so a grep for
  "whisper" there reads as local ASR and is not.
- Edits address nodes by id, never by word.

### palmier-pro

[palmier-io/palmier-pro](https://github.com/palmier-io/palmier-pro) · GPL-3.0
snapshot, proprietary binaries · Swift · created 2026-04-07 · 14,482★ on
2026-09-28

**Source read** 2026-09-28 at `eeafde2`, a depth-50 clone, source before
README, nothing run.

A native macOS editor with an in-app agent. **The public source is frozen**:
the README and `BINARY_LICENSE.md` say source through tag `last-gpl-source`
(v0.7.6) is GPLv3 and every later binary is proprietary with source
unpublished; `eba39db` (2026-08-28) "Retire public source development" removed
CI and CONTRIBUTING, and every commit since publishes an appcast. What was
read is roughly v0.8 against a shipped v0.10.1. macOS 26 on Apple Silicon
only.

- **MCP.** HTTP on `127.0.0.1:19789/mcp`, alive only while the GUI is open, 51
  tools. No CLI, no headless mode. The in-app agent is Anthropic or OpenAI
  with hardcoded endpoints, so no local model.
- **It cuts by word.** `remove_words` takes indices or exact matches, over
  on-device Apple `SpeechTranscriber` or a paid hosted model. Retakes are a
  prompt instruction, with no detector. The project is a JSON of tracks and
  clips in integer frames.
- **Its check is the agent reading its own edit model back:**
  `get_transcript` maps source words through the edit list ("Deleted ranges
  are gone by construction", `Agent/Tools/ToolDefinitions.swift:771`), and
  `capture_frame` draws from the preview compositor, not the exported file.
- Export is AVFoundation, with FCPXML and Premiere XML out and nothing in; no
  OTIO. Cloud generation, credits and an account through Palmier's backend.

**Worth taking:**

- **The stale-index refusal** (`ToolExecutor+Words.swift:29-40`):
  `remove_words` refuses if the timeline changed since the agent's last
  `get_transcript`. proofcut's answer to the same hazard is the echo (§ Word
  indices echo their neighbours, in TRAPS.md); the refusal is the stricter
  form. **Checked 2026-09-28 and not built: the hazard is palmier's, not
  proofcut's.** Its indices are positions in the current timeline, so every
  removal renumbers the words after it. proofcut's name a word of the source
  transcript, which no cut renumbers: cutting words 1–2 then 6–7 and cutting
  them in the other order gave the same edit, and the second call echoed the
  words it took. Only a replaced transcript renumbers, and the echo shows it.
- **Mutation diffs** (`ToolExecutor+MutationDelta.swift:3-30`): every edit
  replies with the clips it changed, shifted and removed, capped at 30.
  **Already proofcut's as `changes`** (checked 2026-09-28): the spans each
  mutation removed and added with their words, records changed field by
  field, lists capped at 40, one call after the edit and named in the
  server's instructions. Folding it into every edit reply would only grow
  the replies docs/plans/MCP.md bounded.
- **The frame overlay** (`InspectFrameOverlay.swift`): the frame number and a
  0 to 1 grid burned into every frame it hands the agent, for placing things
  by coordinate.

**Not confirmed.** What v0.8.1 to v0.10.1 added over the frozen source,
output checks included; the model behind its hosted transcription; its full
`ffmpeg` use and its SigLIP2 search ranking.

### video-use

[browser-use/video-use](https://github.com/browser-use/video-use) · MIT ·
Python · 27,506★ on 2026-09-28 (19.9k★ and 18 commits on 2026-08-06, 25,156★
on 2026-09-20; stars ride the browser-use org's distribution)

**Source read** 2026-09-16 at `9575612`; re-checked 2026-09-20, same head.

The distribution threat, not a substitute. Agent-driven editing through coding
agents (Claude Code, Codex): filler-word removal, grading, subtitle burn-in,
fades. It fails proofcut's core constraint outright: transcription is
**ElevenLabs Scribe only**, and by 2026-09-16 local whisper was rejected by
name ("Use hosted Scribe", `SKILL.md:312-313`). No MCP and no timeline model:
a word-level transcript packed into ~12KB of markdown, ffmpeg underneath.

- **Self-eval is a prompt, not code.** `SKILL.md:91-99` asks for a filmstrip
  at each cut boundary and the first and last 2 s, and an `ffprobe` of the
  output's duration against the edit list, capped at 3 passes. It detects no
  black or frozen frames and transcribes nothing.
- **Decision-point composites.** Instead of frame-dumping, it renders PNG
  composites (filmstrip + waveform + labels) only where the agent must make a
  call. Convergent with proofcut's contact sheets; the first sweep's "nobody
  else in the space does it" was wrong.
- **`project.md` session memory** persisting editorial decisions across
  sessions, a cheaper cousin of kinocut's receipts.

### open-edit

[veedstudio/open-edit](https://github.com/veedstudio/open-edit) · Apache-2.0
CLI over a **PolyForm Shield** renderer binary · TypeScript · 966★ on
2026-09-28 (169★ on 2026-08-06)

**Source read** 2026-09-28 at `dd7913b` (2026-09-22), 23 commits, about 17.4k
lines in `cli/src`. The 2026-08-06 and 2026-09-16 entries rested on the
README, `SETUP.md` and the API: on 2026-08-06, Apple Silicon macOS Tahoe
only with 11 commits and uploads to VEED by default (a local WhisperX
fallback); on 2026-09-16, Windows x64 as well (`SETUP.md:21`), Tahoe what CI
tests rather than a gate, and local WhisperX listed first (`README:47-48`).

VEED's agent-driven caption and motion-graphics pipeline. **It is a CLI
(`openedit`, `@veedstudio/openedit-cli`) driven by a Claude skill, not an MCP
server** (a repo-wide grep for `modelcontextprotocol|McpServer|registerTool`,
around the filter, is empty); AGENTS.md's MCP mentions mean other servers.
The renderer is `veed-engine-cli`, a prebuilt closed binary under PolyForm
Shield (`NOTICE:9-17`); only the CLI and the recipes are Apache-2.0. macOS
arm64 and Windows x64 only (`cli/src/platform.ts:8-15`), and it needs a real
desktop session (`gates.ts:6`). The same open-core-with-hosted-tier shape as
openshorts.

- **Speech.** The README says there is no default provider; the code defaults
  to local WhisperX (`values.provider ?? 'whisperx'`,
  `cli/src/commands/transcribe.ts:292`). `--provider veed` is hosted, needs a
  login and spends credits.
- **Edit.** A JSON EDL of `{source, start, end}` in source seconds, snapped to
  the frame grid (`cli/src/edl.ts:9-30`); `apply-edl` re-encodes the kept
  ranges with audio crossfades in one ffmpeg graph; `retime-transcript` moves
  word timings onto the cut rather than re-transcribing
  (`retime-transcript.ts:85-103`). No undo.
- **Check.** `check-delivery` (`cli/src/commands/check-delivery.ts:1-40`)
  checks container facts, loudness, and the picture against the source at
  sampled instants, a 48x48 grey-frame diff searching ±3 frames for an
  `offsetFrames`. Nothing transcribes the render.

**Worth taking:** the sampled picture-against-source sync search, a cheap
frame-offset check; frame-grid snapping with a half-frame trim edge
(`apply-edl.ts:38-46`).

### oh-my-cassette

[Cassette-Editor/oh-my-cassette](https://github.com/Cassette-Editor/oh-my-cassette)
· MIT · Python · 156★ on 2026-09-28

**Source read** 2026-09-28 at `4bebe25` (2026-09-02): `core/` 14.7k lines,
`mcp_plugin/` 2.5k. The 2026-09-12 and 2026-09-16 entries rested on the README.

A local stdio MCP server (16 `@mcp.tool`, `mcp_plugin/server.py:941`) with
Claude Code, Codex, OpenCode and Hermes plugin manifests: **a thin client to a
hosted Cassette agent.** Creative edits go through `cassette_run_job`, a
LangGraph run on Cassette's servers (`core/api_transport.py:1-30`); the
render is server-side too (`api_transport.py:11-14`). No transcription code in
the repo. A direct no-LLM lane (`cassette_edit`, by clip and command) sits
behind `CASSETTE_DIRECT_EDIT=1` (`core/tools.py:1665-1691`), with an
`expected_version` refusal and undo by history cursor (`:1717-1755`). It
quotes its own session cost ($4 on Opus 5), the way TRIAL.md quotes
proofcut's.

**Claims the code does not back.** "Renders nothing until the plan is
approved" is a tri-state `export` flag plus a skill convention; the one code
gate, `review_required`, the skill tells the agent to resolve in the same turn
(`SKILL.md:146-152`). "A digest and a contact sheet every turn": the digest is
on request and the sheet is opt-in (`contact_sheet=true`). Its `_export_qc`
(`api_transport.py:1725-1752`) is "Container facts only".

### splicedeck

[ihuzaifashoukat/splicedeck](https://github.com/ihuzaifashoukat/splicedeck) ·
Apache-2.0 · Python · 3★

**Source read** 2026-09-16 at `1d6b31b`; the head was unchanged on 2026-09-28 (last push 2026-08-18, 3 commits), so it was not re-read.

The closest in *shape*: nine verbs generated from one table for both CLI and
MCP, local, with a hash-chained ledger. Its `verify` gates the plan before
`deliver` encodes (`splicedeck/surface/verbs.py`); it is not a check of the
render. By its own README, cutting by speech needs a binary it cannot obtain
yet.

### Rushes

[nanzhi84/Rushes](https://github.com/nanzhi84/Rushes) · no licence · Go ·
24★

**Source read** 2026-09-28 at `08b05f5` (2026-08-09), about 49.6k lines of
non-test Go and 15k of web TypeScript. Named 2026-09-16 from its README only.

A local-first conversational editor: a web GUI, an internal Eino ReAct agent
and a SQLite event store. **No MCP and no CLI** (the only "mcp" hit is
`NumCPU`, `storage/db.go:102`); about 23 `addTool` registrations
(`go/internal/tools/registry.go:457`). Transcription is cloud DashScope FunASR
with word timings (`providers/dashscope_asr.go:22`, `195-206`). A frame-level
`timeline.Document`, versioned, one atomic op per call making a new
`timeline_id`, rewind through `parent_version` (`reducer/rewind.go:26`,
`reducer.go:1235`); addressed by asset, shot and source frame range, not by
word. Renders through ffmpeg `-filter_complex` (`media/render.go:144`). No
interchange.

**Its render check is the most complete signal-level one found.**
`InspectVideo` (`media/render.go:1046`) checks resolution, duration within
0.25 s, a decode pass, then `blackdetect`, `freezedetect`, `silencedetect`
and `ebur128` (`:1154-1171`), and **drops black and frozen spans the plan
declared intentional** (`filterExpectedSignalIssues`, `:1195`). A stop gate
re-runs it at most three times a turn
(`agent/automatic_preview_qa.go:18`). It does not transcribe the render or
count frames exactly.

**Worth taking:** the declared-intent filter, which is the answer to
docs/plans/RENDER-CHECKS.md's finding that 13 of 14 frozen spans were cards
(the finding is a span nothing explains). Also ASR-side retake detection
(`agentexec/speech_inspect.go:616`, `746`, `870`, `1044`), Chinese-oriented.

### video-editor-agent

[krusemediallc/video-editor-agent](https://github.com/krusemediallc/video-editor-agent)
· no licence · Python and TypeScript · 19★

**Source read** 2026-09-28 at `493535e` (2026-09-24), about 12.6k lines.
Named 2026-09-16 from its README ("pixels and dB, not intentions").

A Claude Code skill pack: 19 skills, `tools/editor/editor.py` and a
`tools/video-qa` CLI. No MCP server. The edit is hand-authored HTML rendered
by hyperframes (`branded-ad-edit/SKILL.md:138`), with a CapCut draft out. A
stage ledger (ingest, style, edit, sound, qa, review) with SHA-256 artifact
hashes and downstream invalidation (`project.py:114`, `160`).

**Its QA is the nearest thing found to `verify`, and it argues the other
way.**

- Layer 1 (`video-qa/src/layer1-technical.ts`): duration within 0.15 s
  (`:38`, `:124`), black, freeze, silence, LUFS and true peak, cross-checked
  against `manifest.intentional`.
- Layer 2 (`layer2-transcript.ts:1-20`) checks each cut seam with the
  **source's** word timings against the manifest's cut provenance, and
  transcribes only short windows around suspect seams
  (`transcribe.ts:213-235`). It never re-transcribes the whole render,
  because, its comment says, the render hallucinates at jump cuts. That
  comment cites "SESSION_LOG 2026-08-14", which is not in `SESSION_LOG.md`;
  the nearest entry (`:368-384`, 2026-09-02) is about whisper smearing onsets
  on the source and hallucinating on very short isolated clips, which is
  different evidence.
- Layer 3 sends a proxy of the render to Gemini under a JSON schema
  (`layer3-semantic.ts:1-30`).
- No exact frame count.

**Worth taking:** the silence cross-check that stops a whisper timing error
reading as dead air (`layer2-transcript.ts:74-90`) and the butt-splice click
detector at the join (`:104`). Its whole-render objection is a claim to test
against `verify`'s own results, not a finding: proofcut's trials have run
`verify` on every render.

### cutible

[plokdalberb-byte/cutible](https://github.com/plokdalberb-byte/cutible) · MIT ·
Python · 1★

**Source read** 2026-09-28 at `054bdff`. The 2026-08-06 entry said "created
and abandoned the same day (2026-06-22); ignore". The first half is true (5
commits, all 2026-06-22, three of them CI fixes); the second undersold it.

An "agent-native montage engine" of about 9.5k lines of Python plus a
TypeScript SDK, written in one afternoon. A hand-rolled JSON-RPC MCP over
stdio with **37 tools**, not the README's 35 (`cutible/mcp_server.py:250-307`),
one session per process, plus a CLI, REST and SDK. faster-whisper or
openai-whisper with word timestamps (`ingest/audio_transcribe.py:60-102`). A
Pydantic timeline edited by verbs that address clips and tracks by id; every
verb returns a `Diff`, and errors a `VerbError` with a hint (`verbs.py:29`,
`43`). Undo is in-memory checkpoints (`verbs.py:98-108`). ffmpeg
`filter_complex` render plus a Remotion compiler; OTIO in and out
(`otio_bridge/`).

Its `run_qc` (`qc.py`) runs automatically after every render
(`mcp_server.py:66-76`): streams, duration within 0.3 s, `blackdetect` and
`ebur128` loudness. **Its VLM review silently returns mock results with no
API key** (`perception/vlm_review.py:112-119`, `173`), so a green review can
be fake: the failure proofcut's checks are built not to have.

### OpenMontage

[calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) · AGPL-3.0 ·
Python + Remotion · created 2026-03-29 · 61,742★ on 2026-09-28

**Source read** 2026-09-20 at `08e2151`.

A generator and orchestrator: 121 `BaseTool` classes, **no MCP server and no
CLI**; an agent reads `AGENT_GUIDE.md` and calls a Python registry. Mostly
provider tools (video, image, TTS, music, avatar). Four of its 13 pipelines
cut the user's own footage (`talking-head`, `clip-factory`,
`podcast-repurpose`, `screen-demo`), by silence removal or an agent's reading
of a transcript; **no word-addressed edit, no retake handling.** State is
per-stage JSON artifacts validated against schemas (`lib/checkpoint.py`), not
a timeline, and there is no undo. Local faster-whisper by default.

Its `final_review` never runs a transcription: `_compare_transcript_to_script`
diffs a transcript *file the caller supplies* against the script
(`tools/video/video_compose.py:2183-2200`, `2640-2649`), which is why its
docstring says "Only runs when caller provides both". Its duration check flags
only past 25% of target, and it samples four frames, "black" meaning a PNG
under 2000 bytes.

**Not confirmed.** Its real commit count (API only, ~449) and whether
`final_review` is enforced or advisory.

### FireRed-OpenStoryline

[FireRedTeam/FireRed-OpenStoryline](https://github.com/FireRedTeam/FireRed-OpenStoryline)
· Apache-2.0 · Python · 3,452★

**Source read** 2026-09-28 at `c9e9452` (2026-07-31), about 12.7k lines in
`src/`. Named 2026-09-16 as "CLI+MCP, needs a cloud LLM key".

An agent that builds short montages from a node pipeline. A FastMCP server on
streamable HTTP, port 8001 (`config.toml:41-50`, `mcp/server.py:38-56`), 19
tools generated from 18 nodes plus `read_node_history`; `cli.py` is a chat
REPL. **The server holds no key: its nodes borrow the client's model through
MCP sampling** (`mcp/sampling_requester.py:39`); only the bundled client
(`agent.py`, ChatOpenAI) needs one. Local funasr `paraformer-zh`, Chinese,
sentence and token timestamps (`asr_node.py:31-37`, `132`). The rough cut is
an LLM choosing sentences (`speech_rough_cut.py:1-80`), no word addressing,
no undo; moviepy render (`render_video.py:17-30`); no interchange, no render
check.

**Worth taking:** MCP sampling, a tool server using the host's model instead
of holding a key, is the route docs/plans/LOCAL.md's director question has
not considered.

### Closed products read from their sites

**Source read: none possible** for either. (burningion/video-editing-mcp,
first named in 2026-09-16 as a 2024 client for a cloud service, has source
and an entry: § Analysis and cloud clients.)

- **Cardboard** (YC W26, closed, browser-rendered, [Launch
  HN](https://news.ycombinator.com/item?id=47170174)), found 2026-09-16: a
  natural-language timeline editor with NLE XML export. Daydream's case again.
- **DaVinci Resolve 21.1** (2026-09-08, Studio only) ships a native MCP server
  with 88 tools, read 2026-09-16 from press coverage. A GUI with an MCP front
  end; no coverage mentions a render check. A major NLE now has agent hooks
  out of the box.

## The render backend and the handoff

### auto-editor, the project that matters most

[WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) ·
**Unlicense (public domain)** · Nim · 5,385★ on 2026-09-28 (4.7k★ on
2026-08-06)

**Source read** 2026-08-06, run-verified on 31.4.2 on 2026-08-07, and read
again 2026-09-16 at `1647365` (31.6.0). A dependency as much as a rival: it
is proofcut's single-source render path.

Originally treated in the plan as a silence-removal library. It is
effectively a headless NLE, and it ships an agent interface.

| Capability | Detail |
|---|---|
| Transcription | `auto-editor whisper FILE MODEL`: whisper.cpp, NVIDIA Parakeet, or Apple Speech backends. `--format text\|srt\|json`, `--split-words` for per-word cues, `:mic` for live capture. **Not usable out of the box:** MODEL must already be on disk, there is no `download` subcommand, and a bare `base.en` fails with "Could not load whisper model" (run 2026-08-07) |
| Transcript cutting | `--edit word:VALUE`, `--edit "subtitle:pattern=REGEX,ignore-case=#t"`, composable with `or`/`and`/`not`/`xor` |
| Silence/motion cutting | `--edit audio:threshold=0.04`, `--edit motion:...`, `blackdetect`; labels 0–255 with per-label actions (`--edit:N` / `--when:N`) |
| Timeline format | `.v1`/`.v2`/`.v3`, **both exported and imported/rendered** |
| NLE export | `premiere` (fcp7 xml), `resolve` (fcpxml), `resolve-fcp7`, `final-cut-pro`, `shotcut` (.mlt), `kdenlive`, `clip-sequence`. All but `resolve-fcp7`/`clip-sequence` **run-verified on 31.4.2, 2026-08-07**; `kdenlive` emits a real MLT playlist (cuts as separate entries on linked video/audio chains, resources resolving) which `melt` rendered |
| OTIO export | **Undocumented.** `src/exports/otio.nim`, selected by `--export premiere-otio` or an `.otio` output extension. Premiere-flavored (`PremierePro_OTIO` metadata), one-way |
| Dry run | `--preview` prints what would be cut and exits without rendering |
| Agent interface | `skills/auto-editor{,-transcribe,-export,-effects}` in-repo; `npx skills add WyattBlue/auto-editor` |

**The multi-source paywall is two gates** (2026-09-16): a render with more
than one source degrades to 720x576 with a warning
(`src/render/format.nim:145-155`), and an NLE export with more than one source
refuses outright (`src/conductor.nim:496-501`). GitHub ships 31.6.0 while
PyPI is still 29.3.1 (§ auto-editor is no longer Python, below).

#### The v3 timeline format

Flat, LLM-legible JSON: essentially a flattened OTIO track with different
field names. This is what makes auto-editor usable as proofcut's render
backend:

```json
{"version":"3","timebase":"30/1","background":"#000","resolution":[1280,720],
 "samplerate":48000,"layout":"stereo","langs":["eng","eng"],
 "v":[[{"src":"example.mp4","start":0,"dur":26,"offset":0,"stream":0}]],
 "a":[[{"src":"example.mp4","start":0,"dur":26,"offset":0,"stream":0}]]}
```

- `v` / `a` are `Clip[][]`: layers, compositing bottom-to-top, `v[0]` the
  base. Empty layers are rejected.
- Clip fields: `src`, `start` (timeline position), `dur`, `offset` (start
  point in the source), `stream`, optional `effects`.
- All times are in **timebase units**; timebase is a rational
  (`"30000/1001"`).
- `auto-editor timeline.v3 -o render.mp4` renders it.

Renderer internals worth reading (public domain, portable without
attribution): `src/render/{video,audio,h264,hevc,smart,partialplan,subtitle,format}.nim`.

#### What auto-editor cannot do

Unchanged on 2026-09-16: still no MCP, no project state, and `--edit word:`
still a filter.

1. **Addressable ranges.** Its transcript editing is a *global declarative
   filter*: "keep every section whose subtitle matches this regex." There is
   no way to say "cut words 30–45" or "keep take 2 of that sentence, drop take
   1." Filter, not edit. This is the most important gap, because addressable
   ranges are exactly what an agent needs for iterative work.
2. **Persistent project state.** Each invocation is source → output. No
   accumulating edit, no refinement across turns, no undo.
3. **MCP proper.** Skills driving a CLI are not typed tools with schemas and
   structured returns.
4. **OTIO as native source of truth.** Its OTIO export is Premiere-flavored,
   one-way, and undocumented.

### NLE handoff on Linux has a ceiling

"OTIO-native NLE handoff" is only worth something if a Linux NLE can receive
it. Checked 2026-08-06:

- **DaVinci Resolve** imports FCPXML, but free Resolve on Linux decodes no
  H.264/H.265 and no AAC at all (a licensing restriction, not a bug), and
  Studio buys back only the video half. An FCPXML pointing at camera MP4s
  therefore opens as a timeline of offline clips. A working handoff means
  transcoding to DNxHR/ProRes + PCM first and referencing the transcodes.
  Install on Bazzite: `ujust install-resolve`, or
  [davincibox](https://github.com/zelikos/davincibox).
- **Kdenlive** has no FCPXML import. The way in is
  [KDE/kdenlive-opentimelineio](https://github.com/KDE/kdenlive-opentimelineio)
  plus `otio-fcpx-xml-adapter`: two lossy hops.
- **Shotcut / Olive / Blender VSE** take MLT or nothing.

So the handoff formats that land on this box are MLT (`kdenlive`, `shotcut`),
both of which auto-editor already emits, with FCPXML useful only for an
already-transcoded Resolve project. That is a further argument for OTIO as
proofcut's *internal* source of truth rather than as the pitch.

## Small MCP servers

Low stars, often one author, some written in a day. Read because each one is
a design someone chose, and a few hold an idea worth taking.

### The OTIO + MCP niche is occupied, by one two-star repo

The 2026-08-06 survey searched GitHub for proofcut's exact thesis and found
the *literal* niche, OTIO as native source of truth behind MCP, unoccupied.
**The 2026-09-28 source read of clipwright falsifies that**: its state is an
OTIO file and its tools are MCP. What it lacks is word addressing, undo and a
render check, so the combination that survives as proofcut's is headless,
CLI-parity, OTIO-native, word-addressed and render-verified. The first
sweep's stronger reading, that nobody offered addressable ranges +
persistent state + MCP together, had already fallen to OpenChatCut.

| Repo | ★ | Source read | What the source shows |
|---|---|---|---|
| [satoh-y-0323/clipwright](https://github.com/satoh-y-0323/clipwright) · MIT · Python | 2 | 2026-09-28 at `7665374` | 19 pip packages plus a core (the per-domain split the 2026-08-06 entry noted: `clipwright-stabilize`, `clipwright-transcribe`, …), about 31.9k lines of source and 137k of tests; 25 FastMCP stdio tools, a CLI per package, headless. **The project state is an OTIO file**; `write_timeline` takes an `operations` list (`src/clipwright/server.py:715`) over time ranges. No undo or history (grep outside `tests/`, around the filter, is empty); every output is a new file. whisper.cpp (`whisper-cli`, `clipwright-transcribe/transcribe.py:64`), word-level on request. ffmpeg renders the whole timeline in one pass (`render.py:416`, `1225`); EDL and FCPXML out through OTIO adapters. **The 2026-08-06 "runtime depends only on ffprobe" was wrong**: ffmpeg is resolved at runtime in render, silence, scene, transcribe, loudness, noise, colour, frames and stabilize (`render.py:416`, `transcribe.py:435`), and stabilize needs a libvidstab build. Its render check is that the file exists, plus its size (`render.py:1388`); it recommends transcribing the render, for captions (`clipwright-render/server.py:92`). **Worth taking:** its response envelope (`ok`, `summary`, `data`, `artifacts`, `warnings`, `error{code, message, hint}`, CONVENTIONS.md M2) |
| [alexrienzie/open-post-production](https://github.com/alexrienzie/open-post-production) · MIT (code; media all rights reserved) · Python | 4 | 2026-09-28 at `c0723c6` | A blueprint, not an installable app (its README says so): about 58.9k lines of catalog, SQLite/FTS5/FAISS retrieval and scripts that edit Premiere xmeml. No MCP (`premiere mcp/` is "notes, not code"), a query CLI only. **Not local in practice**: transcription drives MacWhisper.app through its `mw` CLI and database (`dataset/_scripts/extraction/transcribe_all.py:6-30`), labelling goes to Gemini or Vertex, and 18 files hardcode macOS paths. The NLE renders; the direct-ffmpeg path is archived and `editor/project.py:62` points at a file not in the tree. OTIO, AAF and FCPXML interchange has no code. **Worth taking:** `eval_cut_boundaries` (`editor/sidecar_cut_eval.py:46`) flags in and out points that land mid-word, 0.12 s threshold, on the plan rather than the render |
| [chaoz23/otio-diff](https://github.com/chaoz23/otio-diff) · Apache-2.0 · Python | 2 | 2026-09-28 at `d9c023b` | About 800 lines. A structural diff of two timelines: added, removed, retimed, moved, shifted. A CLI with diff(1) exit codes (`otio_diff.py:427-456`) and one FastMCP tool, `diff_timelines` (`mcp_server.py:24`). Clips match on `(media_url, src_start)`, falling back to `(name, src_start)` (`clip_key`, `:153`), so a head trim reads as a removal plus an addition. Reads `.otio`, `.edl`, `.fcpxml` and `.aaf` through adapters, first timeline only. **Directly useful** as the "what did the agent just change?" primitive: built natively 2026-09-16 over the undo history (HISTORY.md § `changes`: what the last edits did) |

cutible, which the first sweep listed here as abandoned, is a 9.5k-line
engine and has its own entry above (§ cutible).

The OTIO rendering thesis was also unproven on 2026-08-06: nobody had
demonstrated OTIO → ffmpeg rendering inside an agent loop, which was the
argument for spiking render before building on the assumption. clipwright
does it now, one ffmpeg pass per timeline.

### Glama's related servers

The six Glama lists beside proofcut's own entry
(`glama.ai/mcp/servers/tydude001/proofcut/related-servers`), read 2026-09-16.

**Source read** 2026-09-16: each shallow-cloned and read, README against
source; none installed or run. Tool counts are registrations counted in
source; commit counts are the GitHub API's. Heads: vidcut `4558d35`, CutPilot
`2114ca2`, ffmpeg-mcp-video-editor `c65cd58`, mcpCut `573e443`, NeuroCut
`c88f266`, Unflick `ddc3a3d` (pushed that day, so the read may predate it).

| Project | What it is | Tools | Commits | Tests / CI |
|---|---|---|---|---|
| [mao-data/vidcut](https://github.com/mao-data/vidcut) · 1★ · AGPL-3.0 · TS | short-form timeline editor; the browser watches the agent's edits over a WebSocket | 39 | 408 in 6 weeks | 109 test files, no CI runs them |
| [Hellotravisss/cutpilot](https://github.com/Hellotravisss/cutpilot) · 0★ · no licence · JS | macOS-only editor engine; ffmpeg, optional Remotion | 219 | 12 | CI runs 13 of 70 test files |
| [AbyAbyss/ffmpeg-mcp-video-editor](https://github.com/AbyAbyss/ffmpeg-mcp-video-editor) · 0★ · MIT · Python | typed ffmpeg tools over a SQLite job queue; one whole JSON timeline per render, no project state | 38 (Glama says 32, and 38★) | 16 | unit only; rendering tests excluded from CI |
| [musyta-labs/mcpCut](https://github.com/musyta-labs/mcpCut) · 0★ · MIT · Python | multi-user editor; immutable project versions plus an operation journal; MLT XML → `melt` | 44 | 5, one day | none; CI is lint + a smoke script |
| [vibeDN/NeuroCut](https://github.com/vibeDN/NeuroCut) · 0★ · MIT · Python | N-track editor held only in server memory; MLT XML → `melt` | 30 | 4, one day, AI-written | none, no CI |
| [zhitongblog/unflick](https://github.com/zhitongblog/unflick) · 0★ · MIT · Rust | a libmpv **player**, not an editor; "clip" is one `-c copy` extraction | 101 | 135 | real CI on four OSes |

**None addresses an edit by transcript word**: every one is element id plus
seconds. vidcut's `shared/src/types.ts` documents the failure this avoids:
reordering clips moved overlay anchors to the wrong clip, fixed by requiring
ids to be reused rather than by tracking content.

**None checks a render against the intent.** mcpCut is the closest: it knows
melt exits 0 on failure (cites MLT #547) and `_verify_mlt_output` raises on a
short or missing file, a duration floor, not a content check. NeuroCut checks
only that the file exists and is non-empty; CutPilot's
`visual-qa-engine.mjs` runs `blackdetect`/`freezedetect`; vidcut's
`preview-vs-export.mjs` compares ink boxes between preview and render, which
is geometry, not words or frames.

**Both melt-based servers keep the first-audio-stream trap unguarded**:
mcpCut's `_probe_clip_audio` asks only whether an audio stream exists, and
NeuroCut's `probe.py` keeps the first. NeuroCut does get `out = nframes - 1`
right.

**What they have that proofcut does not:**

- *Blur-fill* for an aspect mismatch (vidcut): a blurred copy behind the
  frame rather than a crop or bars; the usual vertical-video treatment.
  Built since, as a reframe window's `fill: "blur"` (PLAN.md).
- A `batch` tool bundling several ops into one call (NeuroCut): fewer agent
  round trips, the cost docs/plans/MCP.md measured.
- SSRF-guarded URL import (mcpCut, `app/net/egress.py`); proofcut imports
  only local paths.
- A pre-export readiness audit (CutPilot, `director-acceptance-engine.mjs`),
  near `finish_report` already.
- A job stamped with a hash of the tool schema it was queued under
  (ffmpeg-mcp-video-editor, `tools/registry.py`), so a stale worker refuses.
- A project store with a lock directory that expires, a revision counter for
  optimistic concurrency and `.bak` recovery on a corrupt read (CutPilot,
  `project-store.mjs`): the same ground as `Project._manifest_stamp`, with
  recovery added. The cheap half of the recovery is built (2026-09-20): a
  corrupt-manifest refusal names the newest readable snapshot to copy back
  (`Project._recovery_hint`), and restores nothing unasked. The lock became
  docs/plans/PROJECT-LOCK.md.
- Tool arguments checked against the real function signature (mcpCut,
  `app/mcp/argspec.py`), the goal `_PARAM_DOCS` serves from the other side.
- Checksum-verified ffmpeg downloaded on first run, cross-platform from day
  one, a job queue with cancellation shared between MCP and its UI, and
  one-call MediaPipe `track_and_crop` (ffmpeg-mcp-video-editor). Its
  `captions.py` passes text to `drawtext` via `textfile=` with
  `expansion=none` and tests an adversarial string: irrelevant to proofcut's
  ASS path, but the right way if `drawtext` ever appears.
- Text-anchored seek (`search_transcript`/`seek_to_text`, Unflick), the
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
mcpCut pins `mcp>=1.28.1,<2.0`, so its `FastMCP` is real there: the v1 SDK,
not the v2 proofcut is on.

None is queued. Blur-fill and `batch` were the two worth a design note if
either was asked for. **`batch` was measured the same day and is not worth
it yet**: 12.8% of the trial turns could have been folded together, most of
what a batch would fold is already parallel calls, and each trial reads a
`plan` echo before applying it (HISTORY.md § Stop reaches the render, and
`batch` was measured).

### Thin ffmpeg servers

Listed 2026-08-06 as "stateless-ffmpeg MCP servers", useful only as
reference for tool naming, from their READMEs. **Source read** 2026-09-28:
one is stateless, one keeps a timeline, and one is not an MCP server.

| Repo | Source read | What the source shows |
|---|---|---|
| [misbahsy/video-audio-mcp](https://github.com/misbahsy/video-audio-mcp) · 87★ · MIT · Python (83★ on 2026-08-06) | `905549b` (2025-05-24, the last push) | Stateless, as listed: 27 FastMCP stdio tools (the README says "30+", `README:369`) over ffmpeg-python, each taking an input and output path and returning a status string. No speech; `add_subtitles` burns an SRT the caller supplies (`server.py:486-562`). `remove_silence` is `silencedetect` then a `select`/`aselect` filter (`:1103-1207`), the crude form of auto-editor's cut. No render check |
| [chandler767/mcp-video-editor](https://github.com/chandler767/mcp-video-editor) · 5★ · no licence · Go | `31964f1` (2026-02-14) | **Not stateless**: an operation-log timeline in `.mcp-video-timelines/` (`pkg/timeline/manager.go:20-30`) whose undo and redo move an index and hand back an earlier output file (`:173-197`). 79 `addTool` registrations (the README says 60), stdio, headless, plus a separate Wails desktop app. Cloud OpenAI `whisper-1` with word granularity (`pkg/transcript/operations.go:235-243`); script matching is by substring, keeps merged within 0.5 s and removals padded 0.1 s (`:350-417`). **Multi-take is a stub**: every take scores a hard-coded 75.0 (`pkg/multitake/manager.go:237-260`), the same take wins every section (`:263-294`), and `AssembleFinal` writes no file (`:296-310`), against the README's "automatically select best takes / assemble final video" (`README:88-89`) |
| [hyepartners-gmail/vibevideo-mcp](https://github.com/hyepartners-gmail/vibevideo-mcp) · 217★ · no licence · Python + Node | `077f4ac` (2025-06-05) | **Not an MCP server by protocol**: no MCP SDK anywhere; its "MCP" is `ffmpeg-frontend/src/mcp/*.js`, ffmpeg command-string generators behind Express REST routes (`server.js:53-141`), with an Ollama function-calling loop (`ollamarun.py`). Flask's `/run` executes a caller-supplied command after a `startswith(('ffmpeg','ffprobe'))` check (`main.py:578-648`). `/render` runs `melt` on an `.otio`, with a comment that it needs an adapter (`:699-711`). No speech, state or check; three servers must be running |

### intelligent-video-editor

[RychagovSergey/intelligent-video-editor](https://github.com/RychagovSergey/intelligent-video-editor)
· GPL-3.0 · Python + TS · 1★

**Source read** 2026-09-28 at `0fc7a83` (2026-09-20), 14 commits since
2026-09-07, about 13.8k lines. Named 2026-09-16 as "local VLM plus MCP".

A FastAPI + SQLite backend under a React/Tauri app; an agent edits a timeline
over a media library a local Ollama VLM has described
(`app/analysis/ollama_client.py:73-74`). A stdio MCP server of 22 tools
(`backend/mcp_server.py:35-55`, `146-150`, plus 20 `_spec(` entries in
`app/agent/tools.py`), headless but sharing the desktop app's database. **No
speech at all**: a grep for whisper, transcri, speech, subtitle, srt and asr
over the backend, around the filter, is empty. Clip ids and seconds; undo is
an in-memory `deque` per project (`app/timeline/store.py:21-22`), lost on
restart and not exposed over MCP. ffmpeg render with a final
`loudnorm=I=-14:TP=-1.5:LRA=11` (`compile.py:34`). No render check; its own
`STATUS.md:38` says analysis quality is "judged by eye". Beat-aware inputs
(`get_audio_bpm`, `get_audio_events`, `tools.py:351-426`) are the one thing of
note.

## Editors for a person's hands

None of these is agent-driven. They are humans editing a timeline or a
transcript, and they set what the interaction should feel like.

### OpenCut

[OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) (the rewrite) ·
MIT · 90,869★ on 2026-09-28 · and
[opencut-app/opencut-classic](https://github.com/opencut-app/opencut-classic)
(the working app) · MIT · 261★, marked archived on GitHub by 2026-09-28

**Source read** 2026-09-20 in full (rewrite `400f097`, classic `cf5e79e`), by
two readers, source before README, every grep that mattered re-run around the
filter; re-checked 2026-09-24 at `e668010`; classic driven in a headless
browser 2026-09-26. Clones kept at
`~/proofcut-work/spikes/opencut-read/{new,classic,classic-deploy}`.

Found 2026-09-20 on a stargazer's starred list and marked "the one to watch",
because the rewrite's roadmap names MCP and 90k stars is distribution none of
the agent-facing editors has. **The "it is about to overshadow proofcut"
reading is not supported**: the rewrite is an empty scaffold, and classic is
a real editor for a person's hands with no agent surface at all.

**Re-check triggers for the next sweep**, so it looks for something rather
than re-reading everything: `/editor` on `main` stops being a stub;
`crates/media` gains Rust (a `build.rs` or a `src/`); the README's MCP,
headless or Editor API bullets acquire code; `ffmpeg.json` gains a macOS entry
or is repointed at OpenCut's own release (which would mean `pin.ts` landed);
new.opencut.app serves an editor; the two READMEs stop disagreeing about which
app is live.

#### OpenCut, read in full (2026-09-20)

Asked the same day it was marked "to watch": is it a better proofcut, and is
it about to do everything proofcut does. Nothing was installed, built or run,
so every "works" below is what the code says. Heads: the rewrite (`main`)
`400f097`, last commit 2026-08-01, repo pushed 2026-08-10; classic `cf5e79e`,
2026-05-17.

**Three names for two codebases, and the READMEs disagree about which is
live.** `OpenCut-app/OpenCut` (`main`, ~90k★, ~97 contributors) is the
rewrite. `opencut-app/opencut-classic` (~253★ that day, 252★ by the first count, issues disabled) calls
itself "Legacy… archived"; it was created 2026-05-16 as a copy with its
history, and the same code is `main`'s `deploy` branch. `main`'s README says
classic is "the one to reach for today" and that opencut.app still runs it,
and new.opencut.app is the rewrite (`README.md:11-22` says it is being
rewritten). Whether opencut.app served this commit was not verified that day:
a fetch returned only a landing page with a "Try early beta" link.

**The rewrite (`main`) is a scaffold.** 127 files, about 3.1k lines of
hand-written code beside ~6.8k of vendored shadcn components. One person
effectively owns it, and its README says outside contributions are not
accepted "while the architecture is being designed".

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
if taken at face value. Tests: 4 Rust unit tests, none in the web package. CI
runs `moon ci` on three OSes.

**The Rust core is the classic app's, on the `deploy` branch**, and a
project-structure section copied from classic's README describes it as though
it were the rewrite's. A session here said so wrongly, mid-survey, and had to
correct it: read that structure section against `main`'s tree.

**Classic is a real browser editor with no agent surface.** Browser-only and
local-first. About 91k lines of TypeScript (Next.js 16, React 19) and ~4.8k of
Rust (`compositor`, `masks`, `time`, `gpu`, `effects`, `wasm`, `bridge`),
migrating business logic into Rust per its `AGENTS.md`.

- **Editing.** Tracks of video, text, audio, graphic and effect; elements of
  video, image, audio, text, sticker, graphic and effect. Split, trim, move,
  duplicate, group move/resize, copy/paste, ripple, snapping (10px), undo/redo
  as a command stack, multiple scenes and bookmarks. **Keyframes** on
  transform, opacity, volume and text colours (linear, hold, bezier, with a
  graph editor). **Masks**: about nine shapes plus a freeform path, feathered
  on the GPU. **17 blend modes.** Text with Google Fonts. Constant-rate speed
  0.01–5x with pitch kept by SoundTouch; no ramps, no reverse. Audio
  waveforms, volume −60 to +20 dB (keyframable), a master limiter; **no audio
  fades found, by grep only**. Canvas presets (16:9, 9:16, 1:1, 4:3, custom),
  24/25/30/60/120 fps, platform safe-zone guides, .srt and .ass import.
- **Thin or stubbed.** The effect registry holds **one** effect, Gaussian
  blur. Transitions and the adjustment tab are "coming soon" panels, freeze
  frame is a disabled button, the stickers "logos" provider returns nothing,
  and the songs library answers 501.
- **Transcription** runs in the browser (transformers.js, Whisper ONNX: tiny,
  small by default, medium, large-v3-turbo; 9 languages plus auto), over the
  **whole timeline's mixed audio** in 30 s chunks. **Timestamps are
  segment-level only, and `buildCaptionChunks` spreads each segment's words in
  groups of three evenly across it**: the failure `verify` is built to catch,
  confirmed in source. One user issue calls the transcript inaccurate.
- **Export is all client-side.** A Rust/WASM wgpu compositor (WebGPU, WebGL2
  fallback) feeds WebCodecs through mediabunny: MP4 (H.264 with AAC, Opus if
  AAC is unsupported) or WebM (VP9 with Opus); four quality presets and no
  numeric bitrate; resolution is the canvas, with no export-time picker; no
  ProRes, GIF or image sequence. **No ffmpeg.wasm**, so no GPL codec build.
  The mixed audio is one in-memory buffer and the muxed file another, with no
  length cap in code; users report out-of-memory crashes (#628, #656). No
  server-side or headless render.
- **Storage.** Project JSON in IndexedDB, media in OPFS, a typed schema at
  version 31 with 30 migration files, 20 of them tested. **No project-file
  save/load, no FCPXML, OTIO or EDL, and no SRT export** (#719 asks for a
  project export).
- **Backend, all of it.** Four routes: auth (better-auth, but `signIn`,
  `signUp` and `useSession` are never called from the UI; a schema comment
  says "we don't have any auth flows currently"), a feedback box, health, and
  a Freesound search proxy holding the key. Postgres holds only auth and
  feedback rows; Redis only rate limits. **Projects and media never leave the
  browser.** The fal.ai sponsor is a logo entry, not an integration. What does
  leave: feedback text, sound-search queries, Whisper weights fetched from
  Hugging Face on first use, Google Fonts, a Databuddy error-tracking script
  and Vercel's bot check. A first read that took the database for a project
  store was wrong.
- **Self-hosting is not zero-config.** `env/web.ts` zod-parses about eight
  required variables at import; Compose supplies Postgres and Redis and
  placeholders satisfy the Freesound and blog keys, at the cost of the sound
  library and the blog. The editor itself needs no cloud.
- **Agent surface: none.** No MCP, plugin or scripting hook anywhere in the
  classic tree; its "actions" registry is keybinding triggers only. #778 asks
  for a headless render SDK, so it does not exist.
- **Maturity.** 1,567 commits, ~95 contributors, one of whom made two thirds.
  By month: 406 and 632 in 2025-06 and -07, a lull through 2025-12 (2 in
  December), then a Rust-migration surge, 86 / 108 / 130 in 2026-02 to -04,
  and 21 in May before the archive. 30 web test files (20 are storage
  migrations) and 11 Rust tests; **CI's test step is `echo "No tests
  implemented yet"` with `continue-on-error`**, so none of them run there.
  Over 300 issues on the main repo, mixing both eras: the most-commented are a
  Chinese translation, the rewrite's tracking issue, out-of-memory, text that
  cannot be dragged, and `db:migrate` failing; by title keyword, UI/timeline/
  text bugs ~60, memory/crash/lag ~27, setup/DB/Docker ~21, export ~20.
- **Licences.** MIT. Dependencies worth a look before borrowing anything:
  `soundtouchjs` LGPL-2.1, `mediabunny` MPL-2.0. Whisper weights carry their
  own licences, **not checked**.

**Against proofcut.**

- **Classic is ahead** on what a person does with a mouse (masks, a keyframe
  graph editor, blend modes, a GPU compositor, a browser tab with no install)
  and on reach. That is FableCut's gap over proofcut again (§ FableCut, read),
  and again not one proofcut is chasing.
- **proofcut is ahead on everything an agent needs**: word-addressed edits, a
  headless render, a check of the output, NLE export, and captions timed to
  the words rather than the segment. Classic has none of the five, and a
  **hand-editor with an MCP layer added is still that editor**, which is why
  even a shipped rewrite would compete with `proofcut web` before it competed
  with the pipeline.
- **The real risks are the two the survey already names**: distribution (90k
  stars is reach no agent-facing editor has) and "good enough" beating
  "exact" for someone who never needed a word diff. Neither is answered by
  code.
- **Convergent, not borrowed**: platform safe-zone guides
  (`graphics.SAFE_ZONES` is proofcut's report-only version), and a versioned,
  tested schema migration chain.

**Not queued.** Its keyframe graph editor, masks and blend modes are
hand-editing breadth, in the class of § Glama's related servers' "breadth
nobody here has asked for". Its in-memory export is the failure a `melt`
render to disk does not have.

**A live check the source read did not make** (GitHub API, 2026-09-20):
`main`'s last commit was **2026-08-01** and the repo was last pushed
2026-08-10, fifty days still, at 89,999 stars and 378 open issues. Its README
declines outside contributions "while the architecture is being designed",
which answers the collaboration question without anyone having to ask it.
Stars that day: `OpenCut-app/OpenCut` 89,994, opencut-classic 253.

**Not confirmed.** The running behaviour of either app; classic's export at
any length or resolution (the memory limits are issue reports); the
audio-fade absence beyond a grep; the Whisper weights' licences; issue
counts, which are title-keyword matches over a repo that mixes both eras; the
rewrite-era commit count, since only the 30 most recent commits were listed;
and the ages and commit counts of both, since both clones were shallow. That
opencut.app serves the classic commit was settled 2026-09-26 (§ OpenCut
classic, driven).

#### What is worth taking, and what a copy would cost

Asked 2026-09-20, after the read: evaluate classic's web UI and copy it into
proofcut if it is good. **The design is worth harvesting; the tree is not
vendorable**, and the licence is the one thing that does not block it.

**MIT into Shield is the permitted direction.** Classic is MIT, so a file
lifted with its copyright notice kept ships under proofcut's own terms with no
relicensing. The reverse is what is closed. Two dependencies in its export
path are copyleft and travel with anything borrowed from it (`soundtouchjs`
LGPL-2.1, `mediabunny` MPL-2.0), and the Whisper weights' licences are still
unchecked.

Three measured costs of a wholesale copy:

- **They are two different kinds of software.** `src/proofcut/web/` is
  **15,181 lines** of vanilla ES modules, CSS and HTML: no `package.json`, no
  `node_modules`, no build step, ten native imports in `app.js`. Classic's
  `apps/web` is Next.js 16 and React 19 on bun, with Radix UI, drizzle over
  Postgres, Upstash Redis, better-auth, OpenNext-on-Cloudflare, `opencut-wasm`
  and motion behind it, about 91k lines of TypeScript. Taking the UI means
  taking the stack.
- **proofcut ships as a wheel, and `web/` is static files inside it.** A Next
  build wants a node toolchain at install time, which no Python wheel carries,
  against an install story (`proofcut setup`, docs/plans/INSTALL.md) built
  this month to remove exactly that kind of step.
- **It is the implementation, and proofcut's must not be.** Project JSON in
  IndexedDB, media in OPFS, undo as a client-side command stack, a schema at
  version 31: there is no server-side truth for it to be a client of. Copying
  that UI copies a state model that *decides*, against CLAUDE.md's rule that
  the web UI is a third client and never a third implementation, and the
  browser and the CLI would stop agreeing about what the film is.

**Worth harvesting as design**, reimplemented in proofcut's own JS against
`ops`, never vendored:

- **10px timeline snapping**, read against `snapTolerance()` and the rule that
  a hit target smaller than the tolerance resolves to nothing.
- **A curve editor for a window's `interp`.** This is the one item that
  refines the **Not queued** paragraph above rather than agreeing with it:
  OpenCut's linear/hold/bezier graph over transform, opacity, volume and text
  colour *is* hand-editing breadth, but proofcut already stores eased curves
  (§ Eased slides and event-addressed windows) and has no way to draw one.
  Re-ask it as "a graph for the easings already in the manifest", never as
  their feature.
- **Multiple scenes and bookmarks**: no equivalent here. Bookmarks were
  built 2026-09-26 (§ OpenCut classic, driven).
- **Drawn platform safe-zone guides**, the drawn half of the data
  `graphics.SAFE_ZONES` already holds report-only. **Built 2026-09-20**, and
  the one item here that needed no verdict on how OpenCut feels in the hand,
  because the geometry was already proofcut's (HISTORY.md § The safe-zone
  guide, drawn).
- **Its media browser and inspector layout**, against the rail's three tabs.
- **Integer ticks** (120,000 per second, dividing 24/25/30/60 exactly,
  `rust/crates/time/src/media_time.rs:10`, on the `deploy` branch), noted
  2026-09-20 beside its even-spread captions as the failure `verify` is built
  to catch.

**Not worth taking**, beyond the Not queued list: its export is in-memory and
OOMs on real files (#628, #656); and its captions spread each segment's words
evenly across it, the defect `verify` is built to catch. The workspace
redesign and the look pass are both recent and approved, so **harvest
mechanics, not chrome**.

#### OpenCut's first commit in seven weeks (2026-09-24)

Checked because `main` moved: `e668010` (2026-09-24), "ci(media): add the
workflow that builds FFmpeg for every platform". The commit before it was
`400f097` on 2026-08-01. **One re-check trigger fired, on plumbing alone: a
`crates/` directory is on `main`, and it holds no Rust.** Its whole content is
`crates/media/setup/ffmpeg.json` and `setup.ts`. The `build.rs` that
`setup.ts` says will find the build, and the `pin.ts` that the workflow and
release notes tell you to run, are not in the tree.

- **A pinned FFmpeg for a `media` crate to decode with, not to shell out
  to.** Shared LGPL-2.1 builds of 8.1.3 (a licence-clean library to link from
  an MIT app), unpacked into `.cache/media/`. `setup.ts` downloads the
  prebuilt for the host and checks its SHA-256, or builds the pinned source
  tarball with `--from-source`. It cross-compiles Windows from Linux with
  llvm-mingw.
- **A hand-run workflow (`media-deps.yml`, `workflow_dispatch`) that builds
  all six OS/arch pairs from source and publishes them as a prerelease.** The
  first run is `ffmpeg-8.1.3-1`, published the same day: the "new release" the
  repo showed that day. It is FFmpeg archives and a checksum file, and the
  app's latest release is still v0.3.0 (2026-04-15). Linux is built on Ubuntu
  22.04, so the libraries need glibc 2.35 or newer.
- **As committed, `ffmpeg.json` still points four platforms at a BtbN *daily*
  build (`autobuild-2026-09-23-14-55`) and has no macOS entry.**
  `install.py`'s header records that BtbN deletes its dated dailies after a
  few weeks, which is why proofcut pins a month-end build. The self-built
  release looks like OpenCut's answer to the same problem: once `pin.ts`
  runs, its pins point at files OpenCut hosts. proofcut has no need to follow
  while BtbN keeps its month-end builds. Self-hosting is the fallback if that
  stops.

**The other triggers had not fired.** `apps/web/src/routes/editor.tsx` is 238
bytes. The desktop panels are placeholders: `timeline.rs` is 502 bytes and
`preview.rs` 479, and `apps/desktop/README.md` says "Very early. Right now
this is just a window that opens." The README still lists the Editor API, MCP
server and headless mode as "what's coming", with no code behind them, and it
has not changed since 2026-07-23. It still declines outside contributions.
new.opencut.app now serves a real HTML shell titled "OpenCut rewrite —
beta.opencut.app" with one route, and `/editor` is still a 404.
beta.opencut.app does not resolve. opencut-classic has not moved since
`cf5e79e` (2026-05-17), so the two READMEs disagree just as before. The
feature branches `desktop`, `frames` and `dev` last moved in March and April.

**Reading.** The rewrite has chosen its media layer (FFmpeg linked
in-process, behind a Rust crate) and has nothing yet that edits, plays,
exports or takes an agent's call. Nothing in the tree dates a release, but the
distance from here to a working editor is months of work, not weeks. Stars
that day: `OpenCut-app/OpenCut` 90,596 (+602 since 2026-09-20), 276 open
issues and 102 open PRs; opencut-classic 260.

**Not confirmed.** That the self-built release is meant to replace the BtbN
pins: inferred from the workflow's comment and the release notes, because
`pin.ts` is absent. Also whether `crates/media` will do the export as well as
the decode; only "decodes with" is written anywhere.

#### OpenCut classic, driven (2026-09-26)

The cheap evaluation the read in full asked for: opencut.app in a headless
Chrome over CDP (proofcut's `verify-live` driver, extended in
`~/proofcut-work/spikes/opencut-hands/`), with a generated test clip, no
login. **A scripted pass, not a hand one**: it settles what is there and how
it is reached, never how it feels.

**opencut.app serves classic, v0.3.0** (its changelog banner, dated
2026-04-15). No commit hash is shown. It needs WebGL: under `--disable-gpu`
the editor throws "GPU context not initialized" and shows an error boundary.
Media drags are native HTML5 drag-and-drop, so a synthetic mouse drag moves
nothing.

- **Snapping: not settled.** The magnet is on by default, and at the default
  zoom (~325 px/s) drops at raw gaps of 0 to 40 px landed on a frame ladder
  ~10 px apart with no extra pull to the neighbour's edge. A gap of 0 did not
  land flush. Either snapping does not act on that path or a CDP drop skips
  the preview's snap math; the pass could not tell which. The source's 10 px
  `snapTolerance()` is unverified live.
- **The curve graph exists and was not reached.** A keyframe diamond per
  transform property, then the clip's context menu "Expand keyframes" for a
  lane, then the graph button, which stays disabled until the earlier of two
  keyframes is selected ("Select a keyframe that has an outgoing segment").
  The ~10 px diamond defeated scripted clicks, so the easing picker itself is
  unmeasured.
- **Bookmarks work**: the toolbar button drops a tick on the ruler at the
  playhead, and clicking it returned the playhead to the same frame.
  **Scenes**: one "Main scene" by default, and no add-scene control was
  found.
- **Layout**: at 1440x900, an icon rail (Media, Text, Stickers, Effects,
  Transitions, Captions, Adjustment, Settings), an assets panel, the preview,
  and an inspector tabbed Transform/Audio/Speed/Blending/Masks/Effects. At
  390x844 nothing reflows: the desktop panes clip and scroll inside
  themselves. The freeze-frame button is disabled.

**Reading.** Bookmarks are the one candidate this pass settles: cheap, and
proven to work. **Built the same day** (HISTORY.md § Bookmarks on the ruler).
The layout is worth reading for the desktop inspector's tabs only, since
proofcut's rail already handles a phone and OpenCut does not. Snapping and the
curve graph still need the ten minutes by hand; if snapping is built, its
tolerance is measured on proofcut's own timeline, never copied.

**Not confirmed.** Whether snapping pulls at all under a real mouse; the
easing picker's curves; how to add a scene; export. The screenshots were
deleted once these notes were checked, per DAYDREAM.md § Copyright, the DMCA,
and this work.

### Transcript-based editors

Delete a word, and the video loses it: the Descript model, open-sourced
several times over. **Every one was source-read 2026-09-28; none has an agent
surface, and none checks its render.** Most were listed 2026-08-06 from
READMEs.

#### rescript

[wassgha/rescript](https://github.com/wassgha/rescript) · **PolyForm
Noncommercial 1.0.0** (`LICENSE:5`; the API reports NOASSERTION) ·
TypeScript · 921★

**Source read** 2026-09-28 at `4d6f295` (2026-09-14), 127 commits, about 25.6k
lines. The 2026-08-06 entry was README-level.

Browser and Electron (Next.js). It had 630★ on 2026-08-06, when the entry
read it as "`whisper-*_timestamped` in a worker". Transcription runs locally in a worker:
`onnx-community/whisper-base_timestamped` or `whisper-small_timestamped`
(`lib/models.ts:188-197`) or NVIDIA Parakeet, on WebGPU with a WASM fallback
when the device fails (`workers/transcription.worker.ts:229`, `357-372`);
speakers from `pyannote-segmentation-3.0` ONNX (`lib/diarize.ts:4`). **Word
bounds are corrected by CTC forced alignment** (`lib/forcedAlign.ts`,
`lib/align.ts`). Words carry a `deleted` flag and cuts come from deleted-word
bounds, merged across gaps under 0.35 s (`lib/edits.ts:9`, `25-40`); a drag
moves a word's edge (`clampWordBounds` `:283`, `applyWordBounds` `:331`).
IndexedDB, and an undo stack of 100 (`lib/store.ts:129`, `247-259`). Export is
ffmpeg.wasm `trim`/`atrim` + `concat`, re-encoded (`lib/ffmpeg.ts:444-518`),
and **single-threaded**: `exportCoreKind()` always returns `"st"`
(`:70-77`), the multi-threaded core serving only audio extraction (`:371`),
so the 2026-08-06 "multi-threaded ffmpeg.wasm export" was wrong. Interchange
out: Resolve and Premiere XML, FCPXML, AAF, Reaper `.rpp` and Samplitude EDL
(`lib/serializeTimeline.ts:24-30`, `273`, `358`). No OTIO.

**The drag handles are field evidence that ASR word alignment alone is not
accurate enough for clean cuts.** **Measured against proofcut 2026-09-21,
and it does not carry over by ear.** On VO2.wav, a cut placed exactly on
whisper's word edge has sound on both sides 84% of the time where the words
touch, and 43% of the time even across a pause of 0.3 s or more; the middle of
a pause reads 12% (the negative control), and Tyler's own by-ear Kdenlive cut
of the same file 3%. The sound is real: past a removed word's reported end it
stays at speech level for 175 to 425 ms, and whisper run on that span alone
heard "being", a word the cut was meant to take. The two agent trials never
met it, since one cut by `cut_by_time` seconds and the other with `pad: 0.1`.
Two blind A/B rounds on 10 phrase cuts each, served by `proofcut review
serve`: snapping each edge to the *quietest* point between the words lost
(Tyler "maybe" preferred the exact edges), because it moved edges up to 1.4 s,
shortened each join's pause by 0.4 to 1.8 s and clipped two kept words;
snapping to the *nearest* quiet point, which removes only that tail, was
"can't tell". So neither a snap nor a drag handle is queued. What this does
not cover: one voice, one listener on a phone, cuts at pauses only (a
mid-phrase cut, where 40% of touching word pairs have no quiet point at all,
was not listened to). The probes, both keys and both verdicts are in
`~/proofcut-work/spikes/cut-edges/`, and round 1's first page, whose clip
edges were themselves cut mid-word, is kept there marked invalid.

**Worth taking:** its forced alignment is the other answer to the problem
TRAPS.md's "trust a transcript's word order, never its word durations"
answers by distrust; comparing the two on VO2.wav is the cheap next step if
cut edges come back. Its AAF, Reaper and Samplitude writers are an
interchange reference.

#### The others

| Repo | Source read | What the source shows |
|---|---|---|
| [DataAnts-AI/CutScript](https://github.com/DataAnts-AI/CutScript) · 260★ (184★ on 2026-08-06) · MIT · Python + TS | `e5c47e3` (2026-03-06, the last push) | A Descript-alike: Electron over a FastAPI backend, about 4.7k lines. Local whisperx with alignment, openai-whisper as fallback (`backend/services/transcription.py:21-55`, `104-135`); diarization needs an HF token. `deletedRanges` over `wordIndices` with 100-step undo (`frontend/src/store/editorStore.ts:125-147`, `230`). ffmpeg stream-copy concat with a re-encode fallback (`backend/services/video_editor.py:24-80`); ASS burn or SRT. **"One-click filler removal" is an LLM call** (Ollama, OpenAI or Claude) returning word indices (`services/ai_provider.py:106-159`) |
| [Ekaanth/OpenCut-AI](https://github.com/Ekaanth/OpenCut-AI) · 239★ · MIT · TS + Python | `eb8aef9` (2026-09-23) | **A fork of OpenCut classic** plus seven FastAPI services; most of its ~114k lines of TypeScript are inherited. faster-whisper with `word_timestamps=True` (`services/whisper-service/app.py:188`); fillers are a word list over a confidence threshold (`services/ai-backend/app/routes/analyze.py:77-113`); silence is `silencedetect` (`silence_service.py:1-47`); `/api/llm/command` turns language into editor-action JSON (`routes/command.py:14-40`). Edits address element ids and seconds, not words |
| [OpenScript](https://tryopenscript.vercel.app/) = [preston176/openscript](https://github.com/preston176/openscript) · 20★ · MIT · TS | `98d011a` (2026-06-09) | tryopenscript.vercel.app links this repo (two unrelated repos share the name). **Another OpenCut fork**, with a ~1.9k-line transcript panel (`apps/web/src/transcript-editor/`). transformers.js with `return_timestamps: "word"` (`services/transcription/worker.ts:61-63`); a delete is a split, delete and ripple on the tracks (`transcript-editor/plan.ts:22-40`); **one undo command snapshots the tracks and the transcript together** (`transcript-edit-command.ts:16-25`); fillers a fixed list (`filler-words.ts:3-25`) |
| [codeaashu/Rescript](https://github.com/codeaashu/Rescript) · 11★ · MIT · TS | `ff258de` (2026-08-30), a depth-1 clone (depth 50 failed twice) | Unrelated to wassgha's despite the name, though the same design: whisper-base_timestamped in a worker (`workers/transcription.worker.ts:298`), a `deleted` flag, cuts merged under 0.35 s (`lib/edits.ts:4-38`), in-memory undo and no persistence (`lib/store.ts:187-206`), ffmpeg.wasm export (`lib/ffmpeg.ts:110-135`). "Frame-accurate MP4" is a trim in seconds; "nothing leaves your device" beside Google Analytics in `layout.tsx:70`, which its README admits |
| [sstani-bgv/ai-montage](https://github.com/sstani-bgv/ai-montage) · 0★ · MIT · Python + TS + Swift | `ea6a726` (2026-09-14), 15 commits over three days | A local server and React UI with a macOS shell; REST only (`backend/app.py:381-757`). Cloud Groq `whisper-large-v3` with word timestamps (`backend/media.py:894-911`) and a hallucination filter (`hallucinations.py:1-14`); its key lookup reads `~/.claude` paths (`media.py:853-870`). `PUT /edits` refuses on a stale revision and forbids changing word ids (`app.py:571-595`). **After a render it ffprobes the result and refuses to save it** if the duration misses the kept ranges' sum by more than 1/fps + 25 ms (`media.py:570-635`), writing with a no-overwrite `os.link` (`:585-590`). Duration only |
| [awaismirza/yusaf-cut](https://github.com/awaismirza/yusaf-cut) · 2★ · AGPL-3.0 · Rust + TS | `1b3a5e8` (2026-07-07), 119 commits | A Tauri transcript editor and recorder for Apple Silicon. whisper.cpp with DTW token timestamps (`src-tauri/src/transcribe.rs:1-60`). An immutable-word EDL whose source timecodes never change (`src/lib/edl.ts:1-13`), `.scribe` bundles with named snapshots. **Smart cut**: stream-copy between keyframes, re-encode only each cut's head and tail, then concat (`smart_cut.rs:59-150`, `plan_segment` `:125`). No render check, which is exactly where a stream-copy splice needs one |

## Adjacent

Not video editors for agents, but each sits beside one of proofcut's parts:
the graphics renderer, footage retrieval, voice, shorts, the finishing NLE.

### hyperframes

[heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) ·
Apache-2.0 · TypeScript · created 2026-03-10 · 53,937★ on 2026-09-28,
56,154★ on 2026-10-03

**Source read** 2026-09-28 at `ea48936`, a depth-50 clone, source before
README, nothing run. Found beside palmier-pro on a stargazer's starred list.
**Re-read** 2026-10-03 at `ce08f204`, 210 commits later, every claim and
citation below checked against the new head (§ Re-read at `ce08f204`).

An HTML-to-video renderer for agents. **It generates and never cuts the
user's footage**; its `talking-head-recut` skill plays the clip in full and
lays graphics over it. A composition is HTML with `data-start`/
`data-duration` and one paused GSAP timeline the page registers. 45 CLI
commands (46 at `ce08f204`) and 21 agent skills shipped as Claude Code, Codex, Cursor and Gemini
plugins; the only MCP is 12 in-browser WebMCP tools on the open Studio page.
Local by default (whisper-cpp or Parakeet, Kokoro TTS); HeyGen's cloud render
and a few providers are opt-in keys. **It overlaps proofcut's animated
graphics**, not its editing: chrome-headless-shell driven frame by frame
through `HeadlessExperimental.beginFrame`, with a page-side virtual clock, and
audio mixed by ffmpeg from the page's `<audio>`/`<video>` timing rather than
captured. No OTIO or NLE interchange. 42 commits on 2026-09-28 alone.

**The nearest frame check found**: `ArtifactTransaction.validate` ffprobes the
output and rejects a short frame count, but only past one frame short, and it
passes silently when the probe returns no count
(`packages/producer/src/services/render/artifactTransaction.ts:230-235`); GIF
and PNG sequences are skipped. proofcut's count is exact.

**Worth taking:**

- **The page-side clock**
  (`packages/producer/src/services/fileServer.ts:234-388`) freezes `Date`,
  `performance.now` and `requestAnimationFrame` and advances them with the
  seek, and can seed `Math.random` per virtual millisecond. `browser.py`'s
  seek reaches CSS animations and WAAPI and leaves anything else to the page's
  own `proofcutSeek`; a page an agent writes that animates from rAF or `Date`
  without that hook renders its first frame every time. **Built 2026-09-28**
  as `browser.CLOCK`, without the `Math.random` seed.
- **A liveness probe on the capture path** (`browserManager.ts:330-434`,
  `730-746`): one real frame at startup, and a fallback that strips the
  BeginFrame-only flags, because leaving them on makes screenshots blank. The
  shape applies to `DETERMINISTIC_FLAGS` even without BeginFrame: a capture
  that comes back empty should fail loudly at the first frame. **Built
  2026-09-28 as "every frame", not "the first"**: an intro may open empty on
  purpose, so `motion.capture` refuses only a capture where nothing drew.
- **Hold dedup** (`packages/engine/src/services/frameCapture.ts:2783`,
  `armStaticDedup` at `3411-3494`): skip re-capturing a frame predicted
  static, verified against sampled anchor frames (24 by default), and on by
  default since this entry was first written (opt out with
  `HF_STATIC_DEDUP=false`). The first read cited `1058-1080`, which was
  wrong at `ea48936` too: that span gates a fast capture path. A graphic's
  hold phase is the case, and a hold is already its own piece in proofcut.
- **`<video>` in a page is never played**: ffmpeg extracts its frames and a
  pre-capture hook swaps them in as images (`videoFrameInjector.ts`), because
  a browser's own decode is not frame-accurate. proofcut has no video inside a
  graphic yet; this is the route if one is wanted.

- **`hyperframes check --json`** (`commands/check.ts`,
  `utils/checkPipeline.ts`, `checkTypes.ts:39`): one envelope of findings
  graded error, warning or info, with `--strict`. It gates the composition's
  source on sampled frames (lint, runtime errors, contrast, layout, caption
  zones) and never probes the output file, so it is a shape for proofcut's
  check output to compare against, not a rival check.

**Not confirmed.** Which optional keys gate which features (the env flags are
`HF_*`, `HYPERFRAMES_*` and `PRODUCER_*`; provider keys are opt-in; not
enumerated).

#### Re-read at `ce08f204` (2026-10-03)

Cloned with `--shallow-since=2026-09-20`, which reaches `ea48936`; nothing
run. **Nothing new touches proofcut's ground**: still no MCP server but the
Studio page's WebMCP (still 12 tools, `useStudioAgentTools.ts:145-264`), no
transcript-addressed editing, no check of the rendered file, and no OTIO,
FCPXML or EDL (a word-bounded grep over packages, skills and docs finds
none). The frame-count floor is unchanged (`artifactTransaction.ts:225-240`:
silent on a missing count, throws only past one frame short), as are the
clock (`fileServer.ts:234`, the `Math.random` seed opt-in at `393`) and the
liveness probe (`packages/engine/src/services/browserManager.ts:330`, the
flag-stripping fallback near `730`).

- **Counts corrected**: 46 CLI commands (45 at `ea48936`); 21 shipped skills
  under `skills/` at both heads, not 18. One of them is
  `remotion-to-hyperframes` (§ Remotion).
- **"Never cuts the user's footage" holds for the agent path.**
  `talking-head-recut` still plays the clip whole. Studio, the GUI, has a
  razor split for a person's hands (`App.tsx:453,556`), and WebMCP has no
  split tool.
- **Studio audio landed** (#4814 to #4821): a carve that dips a music bed at
  the voice's bands (`core/src/audioCarve.ts:1-12`), loudness normalising and
  a duck with a limiter report. It is docs/plans/NATIVE.md's ground, but
  Studio-side, and no loudness measurement of a render was found
  (`loudnorm`/`LUFS` appear only in tests).
- **Settled from the old Not confirmed list.** Cadence: 589 commits in the
  14 days from 2026-09-20, 20 to 86 a day, weekends included (2026-09-28 was
  86, not the 42 the shallow clone showed). Fonts are embedded:
  `deterministicFonts.ts` inlines generated font data as data URIs, and
  `authoredGoogleFonts.ts` fetches named Google families, so a render is
  hermetic for the families it names. No doc was found promising an output
  check the code does not make.

### Remotion

[remotion-dev/remotion](https://github.com/remotion-dev/remotion) · custom
licence (`LICENSE.md`; the API says `NOASSERTION`) · TypeScript · created
2020-06-23 · 61,661★ on 2026-10-03

**Source read** 2026-10-03 at `e385a83`, a depth-50 clone, source before
README, nothing run; with its agent skills mirror
[remotion-dev/skills](https://github.com/remotion-dev/skills) (4,825★) at
`0b5db9d`. About 220k lines of TypeScript under `packages/`; Lambda and the
other serverless renderers, the Rust compositor's internals, Studio,
templates and the effect packages were skipped. Asked for by name; it was
until now cited only as OpenChatCut's and OpenMontage's renderer.

A video is a React component rendered frame by frame in headless Chrome and
stitched by ffmpeg. **It generates and never edits footage**: a clip is JSX
with `trimBefore`, and nothing addresses a cut by transcript. The agent
surface is skills (12 `skills/remotion-*` directories, mirrored into Claude
Code, Codex and Kimi plugin repos) over the ordinary CLI. Its MCP server is
one tool, `remotion-documentation`, which searches the docs at
`mcp.remotion.dev` (`packages/mcp/src/index.ts:14`); there are no project or
edit tools. Local render needs no display (Chrome Headless Shell by default,
`renderer/src/options/chrome-mode.tsx:4`); Lambda, Cloud Run and Vercel are
opt-in.

**The licence is not open source** (`LICENSE.md:18-23,43`): free for an
individual, a company of up to three employees, a non-profit, or evaluation;
past that, a paid Company License. It forbids copying or modifying its code
"for the purpose of selling, renting, licensing, relicensing, or
sublicensing your own derivate of Remotion" (`:31`). For proofcut that means
ideas only, never code, and a dependency on it would hand every larger user
a licence bill. The skills and `agent-plugin` are MIT.

**Rendering, against `browser.py`.** Capture is `Page.captureScreenshot`
with a clip (`renderer/src/screenshot-task.ts:66-69`), not BeginFrame, and
the flags (`open-browser.ts:197-232`) are anti-throttling and sRGB only; it
sets none of the compositor flags `browser.DETERMINISTIC_FLAGS` pins,
because it never captures an arbitrary page. Determinism is a protocol:
`seekToFrame` calls the page's `remotion_setFrame`, waits for
`remotion_renderReady`, then `document.fonts.ready`
(`seek-to-frame.ts:180-214`), and `delayRender` holds a frame until the
page releases it (`core/src/delay-render.ts:67-195`). There is no frozen
clock; `useCurrentFrame` is the clock. Audio is never captured from the
page: per-frame asset records are mixed by ffmpeg filters
(`stringify-ffmpeg-filter.ts`, an `atempo` chain at
`assets/calculate-atempo.ts:5-17`). `<OffthreadVideo>` asks a local server
(`offthread-video-server.ts:142`) for the exact frame from a Rust ffmpeg
compositor, the same idea as hyperframes' injector.

**Speed is a constant, not a ramp.** `playbackRate?: number` on a video
(`core/src/video/props.ts:51`); the only ramp the docs show is volume
(`offthreadvideo.mdx:127-137`), and no time-remap API was found. Easing is
real: `interpolate` with per-segment easing (`core/src/interpolate.ts:6-30`),
`Easing` (`core/src/easing.ts:38-140`) and `spring`. This corrects
docs/plans/NATIVE.md (§ Corrections, "Remotion does a real ramp").

**No output check.** Nothing compares a render's frame count or duration
with its composition; ffprobe appears only for audio channels and the ffmpeg
call itself.

**Speech and interchange.** `@remotion/install-whisper-cpp` runs local
whisper.cpp with JSON output and optional DTW token timestamps
(`transcribe.ts:172-175,188`) into `Caption {startMs, timestampMs,
confidence}` (`captions/src/caption.ts:3-6`), for captions. OTIO export is a
skill that has the agent *recreate* the timeline, and the docs say plainly
it is "not possible to deterministically export a Remotion project"
(`docs/docs/export-opentimeline.mdx:12-15`).

**Worth taking:**

- **A readiness handshake** for `motion.py`: the page says when a frame is
  drawn (`remotion_renderReady`, `delayRender`) and the capture also waits on
  `document.fonts.ready`. **Built 2026-10-03** as
  `window.proofcutWaitFor(promise, label)`, a promise rather than a handle
  to release, plus a second font check after the last frame (HISTORY.md
  § The readiness handshake, built).
- **Compositor-served exact frames** for video inside a graphic, with
  hyperframes' injector the other route. **Built 2026-10-03** as frames
  ffmpeg decodes and the page's request handler serves, painted as the
  `<video>` element's own background (HISTORY.md § Video inside a graphic,
  built).
- Its whisper-to-`Caption` shape is no improvement on proofcut's word
  transcript, and its OTIO skill is the opposite of proofcut's OTIO-as-state.

**Not confirmed.** The compositor's frame accuracy beyond the code path;
whether any test asserts a render's frame count; whether `<Html5Video>`
accepts a varying rate.

### Footage retrieval: sentrysearch and B-Roll-Finder

**Source read** 2026-09-20: sentrysearch at `acd5a00`, B-Roll-Finder at
`c1f7ff6`, shallow clones, source before README, nothing run.

- **[ssrajadh/sentrysearch](https://github.com/ssrajadh/sentrysearch)** ·
  4,525★ · Apache-2.0 · Python. 30 s chunks with 5 s overlap, embedded *as
  video* (Gemini Embedding 2 by default; Qwen3-VL-Embedding locally, ~18 GB
  VRAM) into ChromaDB; a text or image query matches footage directly and the
  top hit is trimmed with ffmpeg. CLI only, no MCP, no timeline. **No
  retrieval evaluation anywhere.** It is the only thing here that skips the
  lexical step proofcut measured at 2 of 25 human picks, and it brings no
  number of its own. **Tested 2026-09-20, and it failed**: shortlist-of-3 at
  10 of 25 against a bar of 15, top-1 at 1, no better than chance
  (docs/plans/FOOTAGE-EMBED.md; HISTORY.md § Embedding the footage did not
  pick the b-roll). Its cheap parts stand alone: a model-free still-chunk skip
  (JPEG sizes of three frames at a 0.98 ratio, `chunker.py:204-296`), which
  could skip `describe` windows on static footage, and an image as the query,
  which a `footage_sheet` tile could be. The skip is held (2026-09-20) until a
  static clip costs enough windows to matter. Its 0.41 default confidence
  threshold's derivation was not found.
- **[erfsalehi/B-Roll-Finder](https://github.com/erfsalehi/B-Roll-Finder)** ·
  4★ · **no licence file** · Python. Voiceover to shot list to stock and
  YouTube candidates to a Premiere XML. Almost all cloud (Groq, OpenRouter,
  Pexels, Gemini). Its library index embeds the *query text that fetched a
  clip*, not what the footage shows: a text-similarity index over words. No
  evaluation, no agent surface. **Its learned trims**: re-importing the user's
  edited XML records their in and out points per clip
  (`clip_library.py:490-512`), human-pick signal, the thing the 25-pick
  measurement used. Its VLM prompt that may answer "none"
  (`prompts/visual_verify.txt:19-33`) is the honest half of a reviewing pass.
  Its whole-timeline "executive producer" pass is the kind proofcut measured
  making picks worse (13 → 10), with no evidence here either way. **Checked
  2026-09-20 and not built** (docs/plans/PICKS-PRIOR.md): the 13-of-25 floor
  it would be held to is in-sample (held out, the learned prior scores 7),
  and there is one film's worth of human picks to test on.

### Voice: VoiceStudio and voice-pro

**Source read** 2026-09-20: VoiceStudio at `7c9e7a4`, voice-pro at
`7231384`.

- **[debpalash/VoiceStudio](https://github.com/debpalash/VoiceStudio)** ·
  44,377★ on 2026-09-28 (33.3k on 2026-09-20) · AGPL-3.0 · Python. A TTS and
  dubbing app: ~17 engines behind subprocess sidecars (proofcut's own
  pattern). **"646 languages" is the row count of one model's name-to-ID
  table** (`omnivoice/utils/lang_map.py`); its own docs say other engines
  differ. Dubbing never touches a timeline. **The default model's weights are
  CC-BY-NC**, so the default install is not commercial
  (`LICENSE-NOTICE.md:46`). A 7-tool MCP, dubbing not among them. No take
  ranking and no runtime speaker-similarity check. **Its dub timing, for
  `vo_synth`, was declined 2026-09-20**, because `vo_synth` splices in and
  lets the edit grow, so there is no slot to fit (HISTORY.md § Embedding the
  footage did not pick the b-roll): a duration predictor calibrated from the
  same voice's own characters-per-second, run *before* the GPU is spent
  (`duration_planner.py`); an overrun split between audio speed-up and
  picture slow-down under hard caps (`fit_planner.py`); a per-line WER drift
  score against the target, opt-in and never fatal (`dub_qc.py`), proofcut's
  own report-not-gate stance, arrived at separately.
- **[abus-aikorea/voice-pro](https://github.com/abus-aikorea/voice-pro)** ·
  12,955★ · GPL-3.0 (the README says LGPL; unresolved) · Python/Gradio.
  Gradio only, no CLI or MCP. Dubbing is a sequential cursor, so one
  overrunning line pushes every later line late; no output check, no ranking.
  A different category.

**Not confirmed.** The F5-TTS weights' non-commercial licence, which rests on
VoiceStudio's own competitive notes and not on voice-pro's source.

### openshorts

[mutonby/openshorts](https://github.com/mutonby/openshorts) · MIT with a
commercial licence on `cloud/` · Python + JS · 5,772★ on 2026-09-28

**Source read** 2026-09-28 at `29c54fa`, about 67k lines. The 2026-08-06
entry was README-level.

An Opus Clip alternative: a YouTube link or upload to 9:16 shorts, with
faster-whisper or Parakeet (`transcribe_backends.py:161-176`), Gemini picking
moments and layout, MediaPipe/YOLOv8 face tracking and ElevenLabs dubbing.
The carve-out is real: `cloud/` (billing, autopilot, keys) is source-available
and may not be offered as a hosted service (`README:486-488`). An 8-tool MCP
over HTTP at `/mcp` plus a stdio wrapper (`mcp_server.py:58-300`), each a thin
call into its own web app, so a server or Docker must be running. A per-clip
EDL (`recut.py:1-40`, up to 12 segments and 180 s), no undo. No render check.

**Its caption burn-in, compared with proofcut's `caption_style`**
(`subtitles.py`):

- Word timestamps grouped into blocks of 16 characters or 1.4 s
  (`_collect_word_blocks`, `:151-200`, `:239-253`), after merging
  continuation fragments with no leading space onto the previous word
  (`merge_continuation_words`, `:37-60`).
- `generate_ass` (`:303-435`) writes one Dialogue event per word, back to
  back, each ending where the next word starts, so nothing flickers. The
  active word takes inline overrides: `\c` plus an optional `pop`
  (`\fscx90\fscy90\t(0,110,\fscx108\fscy108)`), `glow` or `box`, then `{\r}` back to the
  dimmed style.
- **Dimming scales the colour rather than alpha, because alpha looked muddy
  in libass** (`:284-300`). proofcut's reveal ends each alpha channel at the
  style's own value (TRAPS.md § Captions), a different answer to the same
  rendering; worth one side-by-side.
- `SAFE_MARGIN_V = 43` keeps captions clear of the TikTok/Reels UI
  (`:229`); on split-screen scenes each event gets `{\an5}` so the text sits
  on the seam (`:355-364`).
- The burn is `ass=filename=…:fontsdir='fonts/'` (`:551-557`) with neutral
  file names, because a filter path cannot hold an apostrophe (`:91-110`).

### DaVinci Resolve drivers

The other end of the finishing handoff. Both need Resolve Studio running.

- **[samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)**
  · 3,221★ · MIT · Python + Node. **Source read** 2026-09-28 at `89da04b`
  (2026-09-26), about 124k lines under `src/` (`server.py` alone ~33k) and 30k
  of Node in `resolve-advanced/`. **37 compound tools, each taking an
  `action`, stand in for 389 granular ones** (426 `@mcp.tool` in all;
  `README:8`), a context-cost answer beside docs/plans/MCP.md's. stdio by
  default, SSE and streamable HTTP behind a bearer token
  (`server.py:33220-33228`). Every destructive op archives the timeline to a
  bin first (`:421`), behind a confirm token (`:1837`). Whisper or
  mlx-whisper with word timestamps (`media_analysis.py:4232-4300`). EDL,
  FCPXML and DRT out (`:6157-6176`). **Its render QC is real** and
  report-only ("never auto-clear", `:723-750`):
  `resolve-advanced/server/deliverable-qc.mjs` checks a render against a spec
  with ffprobe, loudness by `ebur128`, blanking, and `re_delivery_diff`, which
  compares two renders' frame counts (`:247-262`; the count is `nb_frames`, or
  duration × fps when that is missing, `ffprobe-media.mjs:42-44`).
  `conform_completeness` checks the *timeline's* frame count against a
  reference the caller supplies (`:216-240`), not a render. `qc-frame.mjs`
  runs a per-cut SSIM against a reference render. Nothing transcribes the
  render. **The "deliberately withholds colour wheel/curve access" of
  2026-08-06 is half right**: its own truth table says primary grade values
  cannot be read or written by script (`src/utils/api_truth.py:1443-1452`),
  and nothing says Blackmagic chose that.
- **[danielbaldwin47/resolve-mcp](https://github.com/danielbaldwin47/resolve-mcp)**
  · 1★ · no licence · Python. **Source read** 2026-09-28 at `4d99305`
  (2026-09-15), 257 commits, about 34k lines. Pitched against Resolve's native
  server. 43 FastMCP stdio tools (the README says 40), a `run_python` escape
  hatch. faster-whisper large-v3 with word timestamps and confidence,
  preferred over Resolve's own transcript (`analysis/whisper.py:1-40`). A
  declarative cut file of half-open source ranges with `alternates[]`; each
  build is a new `<name> vN` timeline, never an overwrite. **Its
  `virtual_transcript` (`analysis/virtual.py:72`) reads the words the cut will
  contain before building**, warning on half-cut words, repeated runs of two
  or more words (two takes kept), low-confidence words and uncovered seams,
  under the rule "Nothing here is a judgement" (`:15`): a pre-render twin of
  `verify`. Its repeated-run half was built 2026-09-28 as `transcript_checks`'
  `cut`: on the approved Scream cut it named 20 candidates, nearly all the
  script's own repetition, so it stays out of `finish_report`. `_verify` (`resolve/build.py:769-790`) reads the built timeline
  back shot by shot and deletes the build on a mismatch. Renders through
  Resolve's queue and returns only size, codec and format (`deliver.py:271`).

### Analysis and cloud clients

- **[guimatheus92/mcp-video-analyzer](https://github.com/guimatheus92/mcp-video-analyzer)**
  · 79★ · MIT · TypeScript. **Source read** 2026-09-28 at `541df52`. Not an
  editor: transcripts, frames, OCR and metadata from a URL or file, 8 fastmcp
  tools (`src/server.ts:64-71`) and a CLI. Transcription falls through HF JS
  whisper, the `whisper` CLI and OpenAI (`audio-transcriber.ts:120-175`) and
  is **segment-level only**: the parser keeps `segment.start` rounded to whole
  seconds (`:289-306`) even though `--word_timestamps` is passed (`:281`). A
  mute-track gate runs before whisper to avoid hallucinated text
  (`:120-135`).
- **[burningion/video-editing-mcp](https://github.com/burningion/video-editing-mcp)**
  · 290★ · no licence · Python. **Source read** 2026-09-28 at `fbe06dc`
  (2025-10-08). A thin client for the hosted Video Jungle service: 10 tools,
  or 11 with `LOAD_PHOTOS_DB` (`server.py:365-827`, `866-1311`; the README
  lists 8), and `VJ_API_KEY` is required at startup (`:44-56`). Analysis,
  search and render happen on the vendor's servers. `edit-locally` writes an
  `.otio` for Resolve (`generate_opentimeline.py:111-197`), a small example of
  an edit spec turned into OTIO.

### OpenTimelineIO, and one writeup

- [AcademySoftwareFoundation/OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)
  · 1,991★ (1.9k★ on 2026-08-06) · Apache-2.0 · C++ with Python bindings. A dependency, read from
  source and the installed package: § OTIO's editing algorithms are C++ only
  and § The Python 3.12 pin's revisit condition, below. On 2026-09-16 it was
  still 0.18.1, still without `editAlgorithm` bindings, and still the only
  package here with no cp314 wheel (ctranslate2 4.8.2, onnxruntime 1.30.0 and
  av 18.1.0 all had one).
- [Agent-Driven-Editing-2026](https://github.com/12georgiadis/open-source-cinema/blob/master/Agent-Driven-Editing-2026.md)
  (the repo now resolves to `ismael-joffroy-chandoutis/open-source-cinema`): a
  landscape writeup, not code, so there is no source to read. It lands
  independently on "OTIO is JSON, so an LLM can read and generate timelines
  directly" as the key insight.

## The passes

The dated record of each sweep: how it searched, what it read, and what it
moved. The findings live in the entries above; a pass keeps only its method,
its heads and its verdict, so the next sweep can repeat it.

### The first survey (2026-08-06 and 2026-08-07)

The plan was written from priors about who else was in this space. Two of
those priors turned out to be wrong (§ Corrections to earlier assumptions),
and one project, auto-editor, turned out to occupy far more of the MVP tool
surface than the plan assumed.

Two sweeps on 2026-08-06. The first searched GitHub for proofcut's
*architecture* (OTIO, MCP, ffmpeg wrappers) and found the micro-repos; the
second, prompted by the stop-or-continue question, searched the *product
space* and found OpenChatCut, video-use and open-edit. A third pass on
2026-08-07 covered Daydream, which neither sweep had checked because it has no
GitHub repo, and a full-site pass followed on 2026-08-08. Most entries outside
the headline projects were read from READMEs: the gap the 2026-09-28 pass
closed.

### The re-check before Show HN (2026-09-16)

Every section, re-read against current source, plus a new-entrant sweep
searched the way a user would type it (GitHub search, the MCP registry,
awesome-mcp-servers, Glama, HN and Product Hunt). Clones were shallow and
read, never run. Heads that day: auto-editor `1647365`, kinocut `e593881`,
OpenChatCut `8411023`, video-use `9575612`, open-edit `b470ebc`, FableCut
`21ec62f`, oh-my-cassette `4bebe25` (`main`), Diffusion Studio `b312417`,
splicedeck `1d6b31b`. Daydream and Cardboard have no source and were read
from their sites.

**The launch claim needed narrowing.** OpenChatCut then shipped
`verify_export` (§ OpenChatCut): duration against the timeline within a
tolerance, resolution, fps, missing streams, black and frozen spans, long
silences, clipping peaks and a contact sheet around edit points. So "checks
its own render" was no longer something only proofcut did. What stayed
proofcut's alone: **transcribing the render and diffing its words against the
cut** (`verify`), which nothing found did, and an **exact** frame count
against the timeline (`check_frames`), where OpenChatCut's check is a
duration tolerance. No match turned up in `src/export/` or
`server/plugins/export-qa.ts` for `transcribe`, searched both through the
output filter and around it. docs/plans/LAUNCH.md's title rested on the old
claim.

The other half of that check, **frozen and silent spans**, is one proofcut
lacks, and OpenChatCut and CutPilot both had. Measured 2026-09-20 on two
films: 13 of 14 frozen spans were cards and one was footage that is still on
purpose, so the finding is a span nothing explains, not a list
(docs/plans/RENDER-CHECKS.md). Rushes and video-editor-agent, read
2026-09-28, both filter their frozen and black findings through what the plan
declared intentional (§ Rushes, § video-editor-agent).

What changed that day is folded into each entry: OpenChatCut, Daydream,
auto-editor, kinocut, open-edit, video-use, OpenTimelineIO, FableCut and
oh-my-cassette. The rest of the survey was re-checked and still held: clipwright, open-post-production, otio-diff, the
stateless servers, rescript, CutScript, OpenCut-AI, openshorts and
davinci-resolve-mcp. Their stars moved, but none added MCP or a render check,
and none was archived. (The 2026-09-28 source read revised several of them.)

New that day, each with its own entry now: Diffusion Studio, DaVinci Resolve
21.1, Cardboard and splicedeck. Named and not read further: Rushes,
FireRed-OpenStoryline, burningion/video-editing-mcp and
krusemediallc/video-editor-agent; too new or thin to judge:
codeaashu/Rescript, RychagovSergey/intelligent-video-editor,
sstani-bgv/ai-montage, awaismirza/yusaf-cut and danielbaldwin47/resolve-mcp.
All of those were source-read 2026-09-28. A farm of near-identical 0-commit
repos (`ai-capcut-pro`, `cupcat-video-editor` and five more) was skipped, and
still is. Not editors: guimatheus92/mcp-video-analyzer (now read) and every
`search=video` hit in the MCP registry (generation, download and marketing
tools).

Stars that day, for the next re-check to diff against: OpenChatCut 1,865
(~915 commits), video-use 24,975, openshorts 4,749, FireRed-OpenStoryline
3,417, davinci-resolve-mcp 2,862, Diffusion Studio 2,795, OpenTimelineIO
1,982, rescript 892, FableCut 669, open-edit 658, burningion 288, CutScript
249, OpenCut-AI 222, oh-my-cassette 155, kinocut 151, video-audio-mcp 86,
auto-editor 5,228.

**How the sweep searched, so the next one can repeat it:** `gh search repos`
for "ai video editor agent", "video editing mcp", "edit video by transcript",
"claude code video editor", "text-based video editing", "video agent mcp",
"headless video editor agent", "whisper video editor agent" and "video render
verify", by stars and by recency (`--created ">2026-07-01"`);
`registry.modelcontextprotocol.io/v0/servers?search=video`;
awesome-mcp-servers' Multimedia section; Glama's `video editing` search; and
web searches for Show HN and Product Hunt launches. **A filtered `grep`
dropped two of kinocut's 201 `@mcp.tool(` lines** and counted 199, so every
count and every absence was re-run around the shell's output filter.

### Seven repos a new stargazer had starred (2026-09-20)

A new star on the public repo led to that account's own starred list, which
held two competitors the survey had and five it lacked. Each was read from a
shallow clone, source before README, nothing run. The same pass re-read the
two already covered against the 2026-09-16 heads. Heads that day: OpenMontage
`08e2151`, OpenCut `400f097` (the rewrite) and opencut-classic `cf5e79e`,
sentrysearch `acd5a00`, B-Roll-Finder `c1f7ff6`, VoiceStudio `7c9e7a4`,
voice-pro `7231384`, video-use `9575612`, Diffusion Studio `57c3983`.

**No launch claim moved.** The narrowed one from 2026-09-16 held against all
seven. The nearest was OpenMontage's `final_review`, which never runs a
transcription (§ OpenMontage).

The seven, each with an entry: OpenMontage (generator and orchestrator),
OpenCut (a human CapCut clone mid-rewrite, read in full the same day),
sentrysearch (footage retrieval), B-Roll-Finder (cloud b-roll sourcing),
VoiceStudio (TTS and dubbing), voice-pro (a dubbing convenience app).
Re-checked: video-use and Diffusion Studio.

**Worth taking, as evidence and not as a decision** (PLAN.md decides), each
followed up where its entry says: footage embeddings against `describe`'s
text (sentrysearch; tested and failed, docs/plans/FOOTAGE-EMBED.md);
B-Roll-Finder's learned trims (checked and not built,
docs/plans/PICKS-PRIOR.md); VoiceStudio's dub timing (declined); OpenCut's
integer ticks and its even-spread captions.

**Not confirmed** that day, beyond what each entry lists: every cost or
latency figure the READMEs quote.

### palmier-pro and hyperframes, read (2026-09-28)

Two neighbours a stargazer's own starred list held, neither surveyed before:
one account starred palmier-pro and proofcut on the same day. Each was read
from a shallow clone (depth 50), source before README, nothing run. Heads:
palmier-pro `eeafde2`, hyperframes `ea48936`.

**No launch claim moved.** Neither transcribes its render. palmier-pro's
check is the agent reading its own edit model back; hyperframes has half of
the other claim, a frame-count floor one frame loose (§ palmier-pro,
§ hyperframes).

### Remotion, and hyperframes re-read (2026-10-03)

Asked for both by name. Remotion was read for the first time (§ Remotion) and
hyperframes re-read 210 commits on (§ hyperframes), each by a reader on the
2026-09-28 brief (`~/proofcut-work/spikes/competitors-source/BRIEF.md`),
nothing run; the findings that move a claim were re-run by hand: Remotion's
licence lines, its constant `playbackRate`, its OTIO page and its one MCP
tool; hyperframes' dedup citation and skill count.

**No launch claim moved.** Neither transcribes its render; Remotion checks no
frame count, and hyperframes' floor is still one frame loose. **One claim
corrected**: Remotion has no speed ramp (§ Corrections). hyperframes'
first read misplaced its hold-dedup citation and undercounted its skills;
both are fixed in the entry.

### Every entry from source (2026-09-28)

Asked the same day: give every entry a real source review, not a README one.
Six readers took the 26 repositories whose entries rested on a README, the
API or a skim (and checked splicedeck's head, unchanged), each a depth-50
clone (one depth-1) under
`~/proofcut-work/spikes/competitors-source/`, source before README, every
claim cited to a file and line, every count and absence re-run around the
shell's output filter, nothing installed or run. The two findings that move
a claim were re-read by hand: OpenChatCut's `occ` (`cli/main.ts`, the v0.2.15
changelog) and davinci-resolve-mcp's frame checks
(`deliverable-qc.mjs:216-262`). Three products have no source to read and say
so: Daydream, Cardboard and DaVinci Resolve 21.1.

**What moved:**

- **OpenChatCut is headless now, in part.** Its `occ` CLI edits and renders
  with no app open. It cannot transcribe or run `verify_export` headless, and
  its word-based cut does not cut picture (§ OpenChatCut). "Headless" alone
  is no longer a difference; headless *transcribe, cut by word, render and
  check* still is.
- **The OTIO + MCP niche is occupied**, by clipwright (§ The OTIO + MCP
  niche).
- **No launch claim moves.** Nothing transcribes a whole render and diffs its
  words against the cut. The nearest are video-editor-agent, which
  transcribes short windows of the render around suspect seams, and
  resolve-mcp's `virtual_transcript`, which reads the cut's words before the
  render. Nothing counts a render's frames exactly against its own timeline:
  hyperframes rejects a render more than one frame short, davinci-resolve-mcp
  compares two renders' frame counts, and OpenChatCut, Rushes, cutible,
  sstani-bgv/ai-montage and video-editor-agent check duration within a
  tolerance.
- **Claims corrected**, each in its entry: clipwright needs ffmpeg at run
  time; cutible is a 9.5k-line engine, not an empty repo; open-post-production
  has no OTIO code and is not local in practice; chandler767's server keeps a
  timeline, and vibevideo-mcp is not an MCP server; rescript's export is
  single-threaded and its licence is noncommercial; open-edit has no MCP
  server and does default to WhisperX; OpenChatCut's progressive tool
  exposure is opt-in; oh-my-cassette's render gate is a convention; the
  Resolve colour limit is real and not shown to be deliberate.
- **Newly worth a look**, each in its entry: the declared-intent filter on
  black and frozen findings (Rushes, video-editor-agent), for
  docs/plans/RENDER-CHECKS.md; MCP sampling so a server borrows the host's
  model (FireRed-OpenStoryline), for docs/plans/LOCAL.md; compound tools with
  an `action` argument (davinci-resolve-mcp), for docs/plans/MCP.md;
  `virtual_transcript`'s repeated-run warning as a pre-render retake check
  (resolve-mcp); forced alignment of word edges (rescript); smart-cut
  stream copy (yusaf-cut).

## Corrections to earlier assumptions

Recorded so they are not re-derived, and so the reasoning that depended on
them can be found.

### The first sweep missed the conversational-editor field

The first sweep searched GitHub for proofcut's *architecture* (OTIO, MCP,
ffmpeg wrappers) and found micro-repos. A second sweep the same day searched
the *product space* ("edit by transcript", "AI video editor agent") and
immediately surfaced OpenChatCut (854★), browser-use/video-use (19.9k★), and
veedstudio/open-edit, including the one project that plausibly covered
proofcut's differentiators. Lesson for the next re-survey: search what a user
would type, not what the implementation contains. This also falsified the
first sweep's claim that decision-point frame composites were unique to
proofcut's preview idea (video-use ships them).

### "Remotion does a real ramp" was never read

docs/plans/NATIVE.md (2026-09-14) says "only MLT and Remotion do a real
ramp". Read from source on 2026-10-03, Remotion's video speed is one
constant `playbackRate` per element (`core/src/video/props.ts:51`) and no
time-remap was found; a ramp means chopping the clip or computing the source
frame by hand. Among the tools surveyed, MLT is the only real ramp.

### "No FCPXML export" was a search failure

The second sweep read OpenChatCut's README and releases and concluded it had
no NLE export. The README names FCPXML in three places and the serializer is
387 lines of `src/export/fcpxml.ts`. Grep the repo for format names; a
feature list read at skim depth is not evidence of absence.

### A README read is not a source read

The 2026-09-28 pass read from source 26 repositories whose entries had rested
on a README, and nine of those entries were wrong in a way the README could
not show: a runtime dependency, a licence, an agent surface, a default, a tool
count, a stub behind a feature list, a stateful server listed as stateless,
and an "MCP server" with no MCP in it. The same lesson as the FCPXML miss,
at scale: an entry states which read it rests on (§ How to read an entry).

### auto-editor is no longer Python

`ae.nimble` at the repo root: it is Nim. PyPI is frozen at **29.3.1**; GitHub
shipped **31.4.2** (2026-07-31) and **31.6.0** by 2026-09-16. `pip install
auto-editor` silently installs a stale, diverged version. auto-editor must be
treated as a subprocess dependency with a version floor, like ffmpeg.

This falsified the plan's stack-decision rationale ("faster-whisper,
OpenTimelineIO, and auto-editor are all Python" → now 2 of 3).

### OTIO's editing algorithms are C++ only

`overwrite`, `insert`, `trim`, `slice`, `slip`, `slide`, `ripple`, `roll`,
`fill`, `remove` all exist in `src/opentimelineio/algo/editAlgorithm.{h,cpp}`
with C++ tests (`tests/test_editAlgorithm.cpp`). A repo-wide search for
`editAlgorithm` returns only C++ sources and CMakeLists: **there are no
Python bindings.**

Verified against the installed package, not just repo source: `dir()` on
`opentimelineio.algorithms` from OpenTimelineIO 0.18.1 on CPython 3.13.14
gives exactly:

```
filter, filtered_composition, filtered_with_sequence_context, flatten_stack,
stack_algo, timeline_algo, timeline_trimmed_to_range, top_clip_at_time,
track_algo, track_trimmed_to_range, track_with_expanded_transitions
```

No `overwrite`, `insert`, `trim`, `slice`, `slip`, `slide`, `ripple`, `roll`,
`fill`, or `remove`.

Consequence: `cut_by_transcript` hand-rolls track surgery over
Track/Clip/Gap and `source_range`. Tractable for single-track cut-and-lift,
but it is real work and not a library call.

### The Python 3.12 pin's revisit condition was already met

Wheel availability as of 2026-08-06:

| Package | Version | CPython wheels |
|---|---|---|
| OpenTimelineIO | 0.18.1 | cp39–**cp313** (no cp314) |
| ctranslate2 | 4.8.1 | cp310–**cp314** (incl. free-threaded `cp314t`) |
| onnxruntime | 1.28.0 | cp311–**cp314** |
| av (PyAV) | 18.0.0 | `cp311-abi3` → covers 3.12/3.13/3.14 |
| tokenizers | 0.23.1 | `cp310-abi3` → covers 3.10+ |

OpenTimelineIO is the sole blocker on 3.14. Everything supports 3.13,
confirmed by actually installing `opentimelineio` 0.18.1 and `faster-whisper`
1.2.1 (with ctranslate2, onnxruntime, av, tokenizers) on CPython 3.13.14 and
importing them, rather than by reading PyPI metadata alone.

### "Daydream hands off finishing work, same as lucid would" was never checked

README.md pitched proofcut against Daydream from the first commit, and both
survey sweeps skipped it because neither searched outside GitHub: Daydream
has no repo. Fetching daydreamvideo.com and its docs directly (2026-08-07)
showed a full NLE-style timeline editor with in-app watermark-free rendering;
NLE export to Premiere/Resolve/Final Cut is an optional extra, not the
finishing path. The README's "same as Daydream does" clause was false and was
cut the same day (2c7d119); the README no longer pitches proofcut against
Daydream at all. See § Daydream.

## Convergent signals worth noting

- **Transcription backend.** Both auto-editor and clipwright chose
  whisper.cpp binaries over faster-whisper. Real signal, but proofcut stayed
  with faster-whisper: in-process and pip-installable mattered more here than
  raw throughput, and it kept the dependency graph free of a second
  hand-managed binary.

  **Overturned 2026-08-07, when ASR was actually built.** proofcut shells out
  to an openai-whisper binary (`asr.py`), which is the same call auto-editor
  and clipwright made and against the reasoning above. Two things decided it.
  The box already had a working openai-whisper install and no faster-whisper
  one, so "pip-installable" bought nothing that wasn't already paid for. And
  in-process is a *cost* here, not a benefit: importing it drags torch and a
  GPU context into `proofcut status`, which never touches audio. The
  convergent signal was right and the counter-argument was theoretical.
- **Caption format.** kinocut ships word-timed ASS, and so does openshorts,
  one event per word. Word-level highlighting is what burned-in captions are
  actually for, and SRT + ffmpeg `force_style` structurally cannot do it.
- **Persistent project state.** kinocut built it; auto-editor's lack of it is
  its clearest structural limit. Both point the same direction.
- **Refuse on a stale view.** palmier-pro's `remove_words`, oh-my-cassette's
  `expected_version`, sstani-bgv/ai-montage's `PUT /edits` and OpenChatCut's
  sessions all refuse an edit made against a timeline that has since changed.
  proofcut echoes the words it resolved instead (TRAPS.md § Word indices echo
  their neighbours) and stamps the manifest (`_manifest_stamp`). The signal
  does not transfer to word cuts: proofcut's indices address the source and
  no cut renumbers them (§ palmier-pro).
- **Filter the render check through intent.** Rushes and video-editor-agent
  both drop black and frozen spans the plan declared, arrived at separately
  from docs/plans/RENDER-CHECKS.md's "a span nothing explains".
