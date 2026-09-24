# lucid × Daydream — the parity plan

This document was written when the project was called lucid, and says so
throughout; nothing in it was rewritten for the rename (HISTORY.md § The rename).

**The direction, set by Tyler 2026-08-08: lucid copies Daydream — the full
feature set and the look/feel.** This is the one document for that work: what
Daydream actually is (observed, not recalled), the design system to copy, the
feature-by-feature map against lucid's shipped code, the detailed design notes
per feature, and the build order. PLAN.md and PRIOR-ART.md point
here rather than restating any of it.

Three lucid constraints do not move, and where a Daydream behaviour conflicts,
the constraint wins and the divergence is recorded in § What parity does not
import:

1. **No cloud, no accounts, no metering, no upload** (PLAN.md § Non-goals).
2. **The web UI draws and plays; it never decides** — every mutation goes
   through the same `ops` functions the CLI and MCP call, and **no lane is
   drawn that `export` cannot produce** (CLAUDE.md § Conventions).
3. **Cues address the source by word index, never the timeline** (PLAN.md
   § The property everything below defends).

## How this was captured, and the one limit

Captured 2026-08-08 without a browser: homepage HTML plus every subpage
(`/claude`, `/codex`, `/mcp`, `/download`, `/wall-of-love`), the three CSS
bundles (design tokens read from source), all sixteen homepage videos
downloaded and frame-sampled through ffmpeg, `hero.png` (a full-resolution
screenshot of the real editor), and the complete docs site via its Mintlify
markdown mirror (`docs.daydreamvideo.com/llms.txt` + per-page `.md`). The
limit: scroll choreography was reconstructed from DOM structure, `@keyframes`
and the videos, not watched live — close enough to copy from, not
pixel-testimony. The MCP tool schema remains unpublished anywhere on the site;
everything below about tools is workflow-level, from their docs' prose.

Older evidence — what Daydream is as a *competitor* (macOS-only, closed
source, pricing-vs-privacy tension) — stays in PRIOR-ART.md § Daydream.

---

## Daydream, observed

A macOS desktop editor (Pushie, Inc.) whose in-app chat **is Claude Code or
Codex run as a subprocess**, riding the user's existing Claude/ChatGPT
sign-in — no API key. The homepage title is "AI Video Editor for Claude Code
& Codex"; the product is positioned entirely around agents driving a real
timeline the user can still touch by hand. No Windows or Linux build exists,
which is the asymmetry that makes copying it worthwhile: on this box the
competitor cannot run at all.

### The editor, pane by pane

From `hero.png` (a real editor screenshot) and the four live HTML mocks on
the homepage. This is the anatomy the workspace copies.

**Top bar.** macOS traffic lights · `Projects / <project-name>` breadcrumb
(Projects is a link — there is a project list) · right side: `Agents` button,
aspect-ratio control showing `16:9`, `Export` button with download icon,
dark-mode toggle (moon), logo mark.

**Left pane — four tabs: `Editor · Assets · Templates · Properties`.**

* *Editor* is the transcript-as-document: project title as a heading, then
  the voiceover as flowing prose in soft highlighted blocks (rose-tinted
  background), word-level — each word is a span. Inline pause markers render
  between words as `[2.4s]` (docs show `[...0.4s]`). A **Show cuts** toggle
  reveals removed text with strikethrough; selecting struck text and pressing
  backspace (or a revert button) restores it. Selecting live text and
  pressing delete (or a scissors icon) cuts it, and the timeline updates.
  Below the script: `+ Add Video` and `+ Add Voiceover` buttons.
* *Assets* is import, **by role**: "Voiceover / Talking Head" (transcribed,
  becomes the script) vs "Footage / Images / Music" (watched and indexed for
  b-roll search — "the AI watches your video clips"). Multi-select import.
* *Templates* is a gallery of handcrafted motion-graphic templates; add one
  to the timeline, then customise by prompt or by hand.
* *Properties* edits the selected clip: text, font, colours, and transform —
  position, scale, rotation, opacity, crop.

**Centre — the preview.** The video, largest element on screen. Under it a
seek bar, `current / total` timecode, transport (jump-to-start · prev · play
· next · jump-to-end), volume slider. Captions render on the video with the
active word highlighted.

**Right pane — the agent.** Chat feed: the user's prompt in a bordered
rounded bubble; the agent's tool activity as a **checklist of green-check
lines** (observed labels: Reading timeline · Reading transcript · Checking
available footage · Surveying video content · Analyzing specific clips ·
Adding clips · Creating custom animation · Updating animation); then the
result as plain prose; **thumbs up/down** under each turn. Composer at the
bottom: placeholder "Ask AI to edit your video...", a `Start New Task`
affordance, `@` to mention assets, and muted hints — `Enter to send ·
Sonnet 4.6` (the model, named) and `⌘L to toggle` (panel show/hide).

**Bottom — the timeline, full width.** Toolbar: drag handle, undo/redo,
pointer tool, scissors/razor, and what read as snap and link toggles; on the
right a zoom `− slider +`. Large current timecode at left of the ruler; ruler
ticks every 10 s. Tracks, top to bottom, each with a header holding an icon,
**lock** and **visibility** toggles: `CC` (captions) · `V2` (graphics —
lavender blocks labelled "Two Line Title", "Image…") · `V1` (video — clips
carry **filmstrip thumbnails** and their source filename, e.g.
`lighthouse.mp4`, `sunset_DSCF0332.mov`) · `A1` (voiceover audio, e.g.
`hawaii_voiceover.wav`) · `A2` (short accent blocks — music/sfx). Clips are
pastel colour-coded (rose / mint / lavender / powder-blue). A thin blue
playhead runs the full height. **Right-click-drag on the timeline selects a
range** — used to place b-roll over that duration. Horizontal scrollbar
along the bottom.

### The workflows, as their docs specify them

1. **Connect an agent.** First launch shows a welcome screen with a status
   card per agent (Claude Code / Codex). `▷ Log in` opens the browser;
   Claude may ask to paste a login code back into the app. Card flips to
   "Connected — signed in as … · plan". Auto-setup installs the CLI in the
   background; on restrictive networks the fallback is installing the CLI
   yourself, and Daydream picks it up. Work/SSO accounts supported. States
   handled: Connected / Not connected / setup-failed (Retry) / status-check
   failed (refresh).
2. **Import and transcribe.** Import by role (above). In Editor, `Transcribe
   Video` / `Transcribe Voiceover` (multi-select allowed) transcribes and
   places the result on the timeline. 30+ languages.
3. **Cut by transcript.** Agent prompts their docs give as canonical: "Cut
   the filler words and long pauses", "Remove the second take of the intro
   and keep the best one", "Remove the section where I was talking about…".
   Or by hand: select text → delete/scissors; pauses are cuttable inline
   markers; Show cuts to review; backspace/revert on struck text restores.
4. **B-roll by description.** Only footage imported under the footage role is
   searchable ("only footage you import here is searchable; your voiceover is
   not used as b-roll"). Three entry points: ask the agent ("Add b-roll of a
   city skyline over the intro"); select transcript text → `Add B-roll` →
   describe; right-click-drag a timeline range → describe, clip fills that
   duration.
5. **Motion graphics.** Prompt the agent ("Create a title card that says
   'Chapter One'", "Make a lower-third with my name and role", "Fade in this
   logo in the bottom-left corner") — it designs the graphic and places it on
   the timeline. Graphics can embed the user's own imported images/video,
   full-frame or overlay. Their docs advise iterating on **one graphic at a
   time**. Or start from a Template and tweak. Manual editing via Properties.
6. **Captions.** Generated from the transcript; a Captions button toggles
   them; default style highlights each word as spoken. **Regenerate Captions
   after a transcript edit preserves custom styling.** Agent restyling is
   open-ended: "single-word captions", "like movie subtitles", "move to the
   top", "add my image of Mario so it jumps on the active word".
7. **Export.** `Export to Video` → presets **YouTube / TikTok-Reels / Web /
   Custom** (custom = resolution + quality), watermark-free. Or hand off:
   XML for Premiere, XML for Resolve, FCPXML for Final Cut — files reference
   original footage rather than bundling it ("you may need to relink").
8. **MCP.** Local HTTP server at `http://127.0.0.1:7433/mcp`, running only
   while the app runs. Auto-registers into Claude Code/Codex on each app
   start, toggleable per agent (Agent Settings → MCP Integrations in
   Terminal). Manual: `claude mcp add daydream --transport http
   http://127.0.0.1:7433/mcp --scope user`. Any HTTP-capable MCP client can
   drive it; external agents get the same toolkit as the built-in chat, and
   the open window stays in sync while they edit.

### The business shell (observed so it can be explicitly not copied)

Free: 1 hr/mo transcription, 1 hr/mo "video processing for search", 100 MCP
tool calls/mo, full editing, unlimited watermark-free exports. Pro $16/mo
annual ($19 monthly): 10 hr / 20 hr / 1M. The metering sits against their own
"your footage stays on your device and is never uploaded" — either the caps
are a pure subscription gate on local work, or derived data (transcripts,
embeddings) leaves for inference. Closed source; not checkable. **The b-roll
metering is a design signal lucid must not copy blind**: hour-metered "video
processing" smells like cloud inference, and lucid's search has to run on
this box instead.

---

## The design system, specified — copy the system, not the assets

The name, logo, and copy text are not copied. Every typeface involved is
open (Geist: OFL; Source Serif 4: OFL; JetBrains Mono: OFL; Inter Tight,
Poppins, PT Mono: OFL), so the system is reproducible by vendoring files, not
borrowing theirs.

### Tokens

Warm paper, from their shipped CSS (`--landing-*`, HSL triplets verbatim):

| token | light | dark |
|---|---|---|
| background | `48 27% 98%` | `48 6% 7%` |
| panel stripe / bg-2 | `48 20% 95%` · `41 19% 93%` | `48 5% 11%` |
| text | `48 14% 7%` | `48 27% 97%` |
| text-2 | `42 5% 27%` | `42 9% 80%` |
| text-muted | `42 4% 51%` | `42 4% 55%` |
| border (hairline) | `42 12% 83%` | `42 6% 18%` |
| border-2 | `42 14% 91%` | `42 6% 14%` |

Radius `0.5rem` everywhere. The app chrome layer under those is stock
shadcn/Tailwind zinc (`240 x% x%` neutrals) — the *warmth lives in the
surfaces, not the widgets*. Accent colours are functional, not brand: one
blue for selection/playhead, green checks, red destructive.

**The warmth is the look** — and it is what `src/lucid/web/app.css` now
carries, in place of the cool slate it shipped with.

### Typography — three voices, six families observed

Roles, which is what gets copied:

* **UI and body:** Geist Sans (fallback: `ui-sans-serif, system-ui`).
* **The accent voice:** an *italic serif* for exactly one emphasized word or
  phrase per heading — Source Serif 4 ("polished *video*.", "Questions,
  *answered*.", "collaborating"). This single move carries most of the brand.
* **Numeric/mono:** JetBrains Mono (they also load PT Mono) for timecodes,
  word indices, keyboard hints, and the editorial numbering — capability
  cards `01`–`07`, FAQ items `Q.01`–`Q.07`, step numbers `0 1 / 0 2 / 0 3`
  on the SEO pages.
* In-app graphics additionally load Inter Tight and Poppins — template/
  caption fonts, not chrome; irrelevant until the graphics work.

Vendor the three main families as woff2 under `/static/` — the web UI has no
build step and a `default-src 'self'` CSP, both of which vendored files
satisfy; no CDN.

### Component language

* Buttons: pill or 0.5rem-rounded, near-black fill with white text for the
  one primary action; everything else quiet outline/ghost on paper.
* Cards and panels: hairline borders, generous whitespace, very low-contrast
  section stripes; subtle backdrop blur on the sticky nav.
* Numbered labels in mono ahead of section titles (the editorial voice).
* Agent feed: prompt in a bordered rounded bubble; tool progress as
  checklist lines with green checks; result as plain prose; muted mono
  keyboard hints in the composer.
* Timeline: pastel clip blocks (rose / mint / lavender / powder-blue at low
  saturation), filmstrip thumbnails on video clips, clip filename labels,
  sticky track headers with lock/visibility, thin ruler, single accent-blue
  playhead.

### Motion

In-app motion is restrained to the point of austerity: a blinking cursor
(`steps(2)` at 1 s), a 0.5 s linear spinner, accordion open/close — that is
essentially the observed inventory (`hero-anim-blink`, `hero-anim-spin`,
`accordion-*`, `wall-rise`/`wall-scroll` for the testimonial wall). The
spectacle lives on the marketing page as scroll-driven choreography of mock
app panes. **Do not import marketing motion into the tool** — in the app, the
moving element is the edit itself.

### The marketing pattern worth stealing, the day lucid wants a page

The homepage hero is not a video: it is a **live HTML replica of the editor
performing an edit as you watch** — words type in, the agent checklist ticks,
the playhead moves, the duration drops 06:10 → 04:52 as cuts land. Three more
section-sized mocks repeat the trick (a terminal wired to the timeline, the
Show-cuts sequence, a motion-graphics prompt becoming the finished graphic),
then numbered capability cards, an examples wall, testimonials, pricing, FAQ.
lucid's workspace is already HTML: **a scripted demo mode of the real page
beats a mock** — the replica would be the product, not a copy of it.

---

## The parity map

"Built" is grounded in shipped code (`src/lucid/web/`, `ops.py`), not in the
sections that planned it. Order of work is § Build order at the end; this map
is the inventory with design detail per row. Cite rows by feature name.

### Workspace shell — built, and rethemed

Four panes over a full-width timeline: built (PLAN.md § Tier 3 is the
goal). The retheme shipped 2026-08-08 — HISTORY.md § The look pass has the
account and the measurements. What landed: the warm token set in light **and**
dark, the three families vendored as woff2, the three voices applied, the
pastel lanes, and the top bar in Daydream's order.

The shell became responsive on 2026-08-17: the preview carries real grid
weight and is the widest pane at every width, the agent and inspector panes
collapse to a 46px rail rather than off a hidden edge, and the top bar wraps
instead of dropping Export. HISTORY.md § The web UI review.

**Corrected 2026-08-24: it is three panes, not four.** The agent and
inspector columns became one rail with three tab panels, because two panes
stacked in one column were clipping the asset list at 1400px and four columns
left the preview third widest — which is what the sentence above was arguing
for and had only half won. One rail, one collapse breakpoint. HISTORY.md
§ The workspace redesign.

One thing the plan above got wrong, worth keeping written down: it expected
"a media query plus a toggle". That shape stores the palette twice. What was
built is `light-dark()` per token declared once, with `color-scheme` as the
whole switch — see CLAUDE.md, which also carries the trap that costs you a
black-on-black canvas if you read a colour token from JS.

### Agent panel — built, cosmetics included

The mechanism is identical by convergence: a local `claude` subprocess with
tool-use streamed to a checklist (PLAN.md § The agent panel, in mechanism —
the security flags there are settled and do not reopen here). The four
remaining cosmetics shipped 2026-08-08 (HISTORY.md § The head of the parity
queue), none of them adding a tool, widening the allowlist, or giving the
subprocess a new path to the project:

* **Model label** in the composer — the stream-json `init` message's model
  field, which the panel was already receiving and dropping. No endpoint.
* **Per-turn thumbs** — one JSON line appended to `cache/agent_thumbs.jsonl`,
  carrying session and turn ids so a later reader can tell *which* turn was
  rated. Nothing reads it yet, as planned. It obeys the `Host` and
  content-type guards like every POST, and deliberately does **not** bump the
  revision or fire `project-changed` — it never touches `project.otio`.
* **`@`-mentions of assets** — completion over `view.clips`, which the view
  already ships, so no endpoint and no new capability.
* `Start New Task` — the one with a real edge rather than only an affordance:
  killing the subprocess mid-turn trips the stdout pump's silent-exit branch,
  so a deliberate reset announced itself as a crash until a suppress-once
  flag was added.

### Transcript document — built, both gaps closed

Paragraphs, show-cuts with strikethrough, selection → floating toolbar
shipped with the workspace; the two gaps below closed 2026-08-08
(HISTORY.md § The head of the parity queue).

* **Inline pause markers.** `[1.2s]` between word spans, from `ops._gap_after`
  — the one gap computation the `paragraph` field's silence arm now calls too,
  because two of them is how they drift. The CLAUDE.md duration rule is what
  makes the field safe: whisper inflates a word's duration to swallow a
  retake, which makes a gap measured to the next word's `start` **under**-report,
  never over-report, so a bad transcript can suppress a marker and cannot
  invent one. Markers are selectable with the text — a `.pause` node resolves
  to the word it trails and never becomes a bound of its own, and when it is
  the selection's *trailing* edge the cut extends through the pause
  (`--through-pause`). That last clause is where the bug was: the flag was
  first computed from whichever node the gesture moved, so extending a
  selection leftward silently cancelled it.
* **Restore one cut.** Shipped as `restore`, with CLI + MCP parity and the
  standard `plan=True` echo, anchored on the struck text show-cuts already
  renders. **This section's premise was wrong and the correction is worth
  keeping:** it said "`Edit` stores its removed ranges". It does not — `Edit`
  is surviving segments and nothing else — so the ranges are derived by
  `Edit.gaps` against the clip's registered duration, which also covers the
  head/tail case a stored table would have missed.

### Timeline — V1/V2/A1/CC and filmstrips built; one small item left

Shipped: lanes as projections, waveform canvas, zoom, ruler, the pastel
palette from the look pass, and filmstrip thumbnails. Left, with real (small)
machinery — plus clip *filename* labels, held back on purpose because a
block labelled by `clip_id` is the name every other surface in lucid uses:

* **Filmstrip thumbnails**, on V1 segment blocks and V2 non-still shot
  blocks — **built 2026-08-17**, drawn through `ops.thumbnail`
  (`picture.extract_frame`, cached per clip instant). HISTORY.md § The
  assets, properties and filmstrip backend, and its panes.
* **Snap and link toggles, lock/visibility per lane**: deferred until there
  is more than one *real* track to lock or link — meaningful post-layered
  timeline, decorative before it.

The CC lane was rebuilt on 2026-08-09: it drew one block per timeline
*segment*, which is not the shape a cue is, so it showed caption lines the
`.ass` would never contain — V2's rule failing quietly. It now draws
`/api/captions` (HISTORY.md § Caption styling).

V2 arrived through that gate and no other way — the picture lane, step 6 of
the layered timeline, drawn only once `export` could render it. A2 and
anything else that widens the view is held to the same rule, and the rule is
absolute: the export degrades silently rather than failing, so a lane drawn
early produces a beautiful window and a wrong file (PLAN.md § The layered
timeline).

### B-roll by description — the biggest new subsystem, design before build

Daydream's flow to copy: imported footage is indexed ("watched"), then
searched by natural-language description, placed by the agent or from a
transcript selection / timeline range. lucid has the placement substrate
coming (cues) and one primitive (`spot_frames` samples frames); it has **no
indexing and no search**, and Daydream's own metering suggests theirs is
cloud inference — the one part that must not be copied blind.

**The design is costed, in lucid `PLAN.md` § B-roll by description**
(2026-08-09), **and it is built** — steps 1–3 shipped 2026-08-09, and the
watch on 2026-08-10 overturned the index's role: what chooses a clip is a
per-clip `synopsis`, not the vision index, and lucid does not choose at all
(HISTORY.md § `describe`, § The pinned cue, § Choosing the b-roll). This
paragraph's "nothing is built" stood stale for three days; corrected
2026-08-12. The note picks the agent-reads-the-descriptions
option — measured sufficient at project scale, ~10k tokens for this project's
footage — over embeddings, and it describes on this box in about six minutes
for a whole project. Two corrections it makes to *this* row, both measured:

- **"lucid has the placement substrate coming (cues)" is true only for
  whole-clip b-roll.** A cue names an asset, never a moment inside one —
  `mlt.plan_picture` assigns that from a consumption cursor. Since search's
  entire output is a moment, the item's real build is an in-point on a cue,
  which must refuse rather than rewind when it would overrun.
- **The homebase encoder service (port 8765) is struck from the checklist.**
  It is an ffmpeg transcoder with no inference in it. It is relevant to the
  preview proxy transcode instead.

Also settled: "watching at import" is refused (it would make every import a
six-minute job over footage nobody uses), and describing is a *job* on
`/api/render`'s pattern rather than a request. Still strictly after the picture
lane — placing b-roll the export can't render is the standing trap.

### Motion graphics + templates — after the picture lane, design first

The worked prior is already in the repo's history: the Scream assembly's 13
cards are pre-rendered stills placed as cues and rendered by `melt`. Motion
graphics generalise exactly that mechanism: **the agent authors an asset, it
lands as a cue on the picture track, `melt` composites it** — full-frame or
overlay. Templates are a starter library of those assets with editable
text/colour slots — the same slots the Properties pane (§ Properties pane,
built) already inspects on a static card, which is why that pane's gate was
graphics, not motion graphics specifically. Their docs' advice ("iterate one
graphic at a time") is a prompt-guidance line, free to adopt.

**The design is settled, in lucid `PLAN.md` § Motion graphics and templates**
(2026-08-09) — asset format, generator, canvas, and the measurements behind
each. What changes *this map* is that the row ships in two pieces rather than
one: static cards need no new timeline mechanism at all, while animation
carries a length that a shot's derived length cannot be trusted to match, so
it is deferred to its own note rather than being the second half of this
build.

**One gap remains and it is not a template one.** The `quote` slot carries
emphasis and a measured wrap as of 2026-08-10, and ten of the twelve Scream
cards re-author through lucid at the project's own canvas (HISTORY.md § The
emphasis-capable quote slot). The two that refuse are a *content* fit, not a
missing feature: a 2.35:1 receipt holds three lines of quote where a 16:9 one
holds seven. So a card-heavy watch is blocked on an editorial call rather
than on a build.

**That refusal is aspect-specific, measured 2026-08-10: at 9:16 all twelve
author, zero refusals** — the frame got taller and the long reviews fit
(HISTORY.md § Step 6 of the aspect swap, watched). So the editorial call is a
16:9-only one and gates no vertical work. What the same render found instead
is a *composition* gap the wrap measurement could not see: the `receipt`
template stacks from the top, so at 1080x1920 its content sits in the top
quarter and two-thirds is empty. **A template that fits is not a template that
composes**, and only the fit was ever measured.

**Refused on a watch 2026-08-10.** The cause was filed as one line of
`graphics.py` — `view_height = round(TEMPLATE_WIDTH * height / width)` keeps
the design grid 1920 wide always, so a 1080-wide canvas renders every template
at 0.5625 — and type is indeed ~3.2x smaller *against the frame* at 9:16 than
at 16:9: a receipt title falls 11.3% → 3.57% of frame height, its body 4.26% →
**1.35%**. **The arithmetic held and the diagnosis did not** (PLAN.md § The
vertical card layout, finding 1). A 16:9 card is letterboxed to 1080 wide in a
portrait feed, so it draws its title at the same 68.6px the 9:16 card does —
ink measured off both rasters is **765x243 either way, pixel-identical on
screen**. What the swap changes is the field around the type: ink ends at
17.1% of the vertical frame against 71.4% of the landscape one. The cards read
as small because they are top-heavy, not instead of it.

So a vertical layout is a per-aspect template variant rather than a scale
factor — the conclusion survives its reason — and its job is to *compose* into
the tall frame, with any enlargement an editorial choice on top.
`goodsometimes/branding.md`'s Shorts row is the only spec: 1080x1920, title in
the top third, bottom clear for platform UI — which is the bottom fifth, and
the right edge below the halfway line too. **Built and watched 2026-08-11**;
the design note, its four findings and the watch's own correction are PLAN.md
§ The vertical card layout.

### Captions — built, and per-word animation declined on a watch

lucid generates timeline-mapped captions (HISTORY.md § Captions came out of the
timeline), and styles them as of 2026-08-09 (HISTORY.md § Caption styling).
The design this row specified is what shipped: a caption-style object in the
project, separate from caption *content*, which is derived — so
regenerate-preserving-style is true by construction rather than by care.
`caption_style` writes it, `caption_view` shows the result, `add_captions`
reads it, and the window draws it in the viewer and on the CC lane.

**Per-word animation was the gap, and it is now a decision instead.** Daydream
lights one word at a time and pops it; lucid sweeps a `\k` fill and moves no
glyph. Costed 2026-08-10 — **PLAN.md § Per-word caption animation** owns the
design and the measurements — and **Tyler watched all four treatments on the
real film and chose the fill lucid already writes.** So this row is complete
by choice, and the divergence below is deliberate. Nothing was built.

**The claim the row used to carry was false, and false in the expensive
direction** — that a single-word highlight and the animation both "want one
Dialogue event per word rather than one per line". Measured: both are per-word
`\t` blocks inside the one event per line that `to_ass` already writes, and
one event *per word* is not a harder build of this feature but a different one
(it draws a single word centred in the frame, because libass owns layout and
lucid cannot supply a `\pos`). That matters after the decision, not before it:
it means reopening the row costs about a day, not a text-layout engine.

**The one real cost of the fill, so a future watch knows what to look for:**
by the last word of a seven-word cue every word is in the highlight colour, so
it reads as progress through the line rather than as *which word is this*.

Also absent by choice: a styling UI. The agent restyles and the window renders
it, which is the parity target.

### Aspect swap 16:9 ↔ 9:16 — after the layered timeline, and now load-bearing

Touches the model (a project canvas property), both render paths, the cards
already rasterised at the old canvas, and the preview letterbox. **Costed
2026-08-09 — PLAN.md § Aspect swap — the design note**, which owns the design,
the measurements and the build order; this row is the pointer. **The preview
letterbox closed 2026-08-10** — the viewer's frame is the project canvas and
media is placed at the render's own rect, so the page crops where the render
crops (HISTORY.md § The viewer's frame). **The preset below shipped the same
day** (HISTORY.md § `tiktok-reels`). **The vertical cut was rendered and read
back 2026-08-10** (HISTORY.md § Step 6 of the aspect swap, watched): 1080x1920,
8064 frames, `agrees: true`, all twelve cards re-authored, no bars anywhere.

**What it found is that the centre-crop default is not usable** — of six
frames sampled across the film one is composed correctly, the title card's
"SCREAM" crops to "REA", and two faces are cut. That is the question the step
existed to ask, now answered by measurement rather than assumed.

**The fix this row then recorded — "nine editorial choices and a watch, not a
build" — is false, and the audit that disproves it is cheap: draw the 9:16
window on all 25 footage shots.** Most fail, and the failures are not
per-*clip*: `s2022-reveal` wants a left crop at 92s (Amber frame-left) and a
right one at 180s (Richie centre-right), from one clip. **5 of 9 clips
contradict themselves this way**, so no single rect per clip can frame the
film and `reframe`'s existing shape cannot express the answer. Per-*shot*
framing is a real build.

Scope makes it unavoidable rather than unlucky: the sources are 1920x816, so a
9:16 crop **keeps 23.9% of the picture width**. Measured over all 25 footage
placements — **64 camera shots, 245.6s** — the shipped centre crop leaves a
subject *outside the frame entirely* in **59.8% of the seconds that hold one**,
and clips one at the edge in a further 23.2%.

**Letterboxing is ruled out, by Tyler on a watch 2026-08-10: a landscape
picture parked in a tall frame reads as repurposed however the ground is
painted.** So the answer is full-bleed and it is two mechanisms, not one mode:
**per-shot tracked framing** covers 62.2% of the footage, and where the
subjects' spread exceeds one 9:16 window — 24.8% — the frame **splits into two
stacked panes**, each cropping 918 source pixels against the solo window's 459.
Above three subjects a split frames nobody and it reverts to one window. 11.6%
has no detectable face and stays a guess. A working spike measured all of this
outside lucid (HISTORY.md § The vertical cut, made native). **The build it
implies was costed and then built 2026-08-10, and is none of the three things
the spike guessed**: framing is source-addressed rather than on the cue, the
render needs no new node, and the detector comes last — behind the 15 hand
numbers it has to beat. The mechanism ships — store, writer, contact sheet,
preview — and those 15 numbers are now source-addressed project state, a test,
and verified through a real render (HISTORY.md § The framing control). The
detector that frames the rest was costed against those numbers and built
2026-08-11 (PLAN.md § The auto-framing detector), and **all 25 placements are
now framed** — 55 windows over 9 clips, of which 39 are the detector's and
**all 39 were reviewed one by one on 2026-08-11** — which cleared the
placement rule and indicted its *coverage*: every window is right for the shot
it was placed on, and about a third of the film's placed footage was framed by
a window placed for a different one. The instrument's own share of that closed
2026-08-12 (`reframe_coverage`, the row-per-window sheet, `--extremes`); this
row's "none has been reviewed" claim stood stale for a day and was corrected
2026-08-12. HISTORY.md § The thirty-nine windows, reviewed; § The three gaps,
closed.
**The stacked split shipped 2026-08-11** — a second node with a second
`qtblend` rect, not the new render path this page assumed — and it fires on 4
of the film's 59 windows, 6.2% of the picture-seconds against the 24.8% the
spike claimed. Tyler approved all four on a watch, three-face window included:
the sheet's own recommendation against that one was drawn from a single sampled
frame where the three converge. HISTORY.md § The stacked split, built,
§ Keeping the split the sheet argued against; PLAN.md § The stacked split.

**It stopped being only a parity nicety on 2026-08-08:** it is what a
`tiktok-reels` export preset is waiting on (§ Export presets), the first thing
anyone wanting a vertical export will reach for.

**The claim this row used to carry was false, and backwards** — that "the melt
path cannot take a resolution at all" until HISTORY.md § 4's memory-growth
combination is isolated, so a real 9:16 "needs an answer on the multi-source
side". Measured: melt renders 9:16 today with its consumer untouched (the knob
is the `<profile>`, never the consumer) and reframes with one `qtblend` filter.
The **single-source** path is the one with no answer — `-res` letterboxes and
auto-editor has no reframe flag to teach. What stays genuinely unmeasured is
the melt *consumer*, which this item never needs to touch.

### Export presets — built, all four

Shipped 2026-08-08 (HISTORY.md § The head of the parity queue) as `youtube`,
`web` and `custom`, on `ops.export`, the CLI and the window's Export flow;
`tiktok-reels` joined them 2026-08-10 (HISTORY.md § `tiktok-reels`).

**"Cheap, anytime" was optimistic in two ways, and both are worth keeping on
the record.** First, the sentence this row used to carry — that the presets
"map onto `ops.export`'s existing arguments" — was false: `export` took
`output`, `export_format` and `fps`, and had no resolution or quality argument
to bundle. Second, the bundles are deliberately narrow. They vary only the
four consumer keys `picture.RENDER_ARGS` already hardcodes, because HISTORY.md
§ 4 measured a melt consumer reaching 14.6 GB with `width`/`height`/`ab` in the
combination and nobody has since isolated which addition caused it. A preset
that widens the consumer is a memory-growth experiment in a feature's clothes.

**`tiktok-reels` shipped last, and it is the one preset that checks rather
than only encoding.** The old objection — that a platform's name would sit
over a quiet pillarbox and trip this document's constraint 2 — was answered by
`canvas` plus the reframe (§ Aspect swap). What was left was that the canvas
half belongs to the *project* rather than to an export flag, and the build
resolved it by having the preset **refuse** a canvas that is not 9:16, naming
the `canvas` command that fixes it, rather than setting the shape itself: an
export argument rewriting project state is the same failure as picking the
writer from an argument. Its four encode values are `youtube`'s, because both
platforms re-encode the upload. A caller-supplied `resolution` stays refused
on the melt path for the memory reason above — a refusal of the *argument*,
not an inability of the renderer.

### MCP over HTTP — built

Daydream's always-on local HTTP server is what lets an *already-running*
editor be driven from outside; lucid's MCP was stdio only, a client spawning
its own server per project.

**Built 2026-08-17** (PLAN.md § The completion queue, the HTTP-transport
item). `lucid mcp` gains `--transport {stdio,http}`, **default stdio,
unchanged** — every existing client still spawns the server the old way —
plus `--host`, `--port` (default 8711, one above webui's own; 0 picks a free
port), `--allow-remote`, and repeatable `--allow-remote-host NAME`.

**The guard is loopback plus Host — `webui.py`'s model, not
`reviewserver.py`'s token**, which is the parity answer this row owed: an
always-on local server is for local clients, and the thing that makes
`review serve` need a token is that it is meant to leave the machine. What
the SDK actually offers, why the app is assembled by hand rather than
through `mcp.run(transport="streamable-http")`, and the `--allow-remote`
defect that was dishonest in both directions: HISTORY.md § MCP over HTTP,
built.

### Import roles + assets pane — built

Rode the b-roll design as this section predicted: the role split (voiceover
vs footage) only meant something once indexing existed, and it shipped as
the assets pane's own grouping key.

**Built 2026-08-17.** **It is a declaration the assets pane groups by, and
deliberately nothing more**: the obvious reading of "role" is that it
changes what import *does* — which footage gets transcribed, which gets
indexed — and it does not. Neither `transcribe`/`attach_transcript` nor
`describe` reads it; both still gate on their own evidence exactly as before
the role existed, asserted by
`test_setting_a_role_does_not_touch_describe_eligibility`. Widening its
meaning to actually steer eligibility was deliberately left out of scope.

The assets pane lists both halves of the cue vocabulary — a `clip_id` or a
`card:name` — grouped by role, and clicking a row hands the properties pane
something to inspect. HISTORY.md § The assets, properties and filmstrip
backend, and its panes.

### Multi-project — built, still one project per process

**Shipped 2026-08-17, and deliberately the small option.** Daydream's
breadcrumb implies a project list; lucid now has one too, via `lucid web
--root DIR` serving a picker over a bounded scan rather than one fixed
project. The deliberate choice is that a picker widens what can be
**listed**, never what one server can **serve**: the first project a person
picks is a one-way bind for that process's life, so two projects open at
once is still two processes, exactly as `-C` always required. `--root` is
confined the same way an MCP tool's project selector already is — a path
resolving outside the scanned root is refused, not opened. MCP itself is
untouched, since a client already spawns its own server per project.
HISTORY.md § The multi-project picker, built.

### Properties pane — built

Deliberately removed from the workspace shell at first, in favour of the
agent feed, on a stated condition: return once there is something with
text/colour/transform to inspect. That condition was never "after motion
graphics" specifically — it was graphics, full stop — and PLAN.md § The
completion queue has carried it as **"properties, whose gate cleared when
graphics shipped"** since 2026-08-12, the static-template card work (quote/
receipt, safe zones) being graphics with text, colour and a canvas to
transform same as a motion graphic would be. This row still framed the gate
as a future condition past that date, and a reviewer reading it flagged the
built pane as shipped ahead of its own gate — a round trip that cost time on
a fact PLAN.md already had right.

**Built 2026-08-17.** `ops.properties` is composition, not a new
derivation — it assembles existing pieces (`status`, `canvas`,
`caption_style`, and once given a `clip_id`, `clip`/`reframe`/`cues`/`cue`),
proved by a monkeypatch test that makes `ops.canvas` lie and watches the lie
surface through `properties`, rather than asserted. `properties.js` draws
the returned bundle generically and listens for `inspect-asset`/
`inspect-word` off the existing ctx bus. HISTORY.md § The assets, properties
and filmstrip backend, and its panes.

### Languages — expected free, verify once

Whisper is already multilingual; run one non-English clip through the full
path (transcribe → cut → captions) before claiming the row.

### Local & private — already stronger; hold the line

No metering, no account, nothing leaves the box, and the b-roll design above
is constrained to keep it that way. This is the row where lucid is ahead,
and the README's pitch depends on it staying asterisk-free.

---

## What parity does not import

* **Metering, accounts, sign-up, cloud.** PLAN.md § Non-goals holds.
* **The desktop shell.** PLAN.md § Not a desktop app — unchanged; wrap the
  finished page in `--app`/Tauri later if chrome is ever wanted.
* **Premiere / Resolve / FCP XML exports.** No such apps exist on a Linux
  box; the OpenChatCut trial measured the handoff ceiling (HISTORY.md § First
  milestones). Kdenlive MLT stays the handoff.
* **Codex as a second agent.** The panel speaks the installed `claude`; a
  second CLI is a config problem for the day someone has one.
* **Cloud-shaped inference for b-roll search.** Local design or nothing.
* **Per-word caption animation, and the single-word highlight with it.**
  Declined on a watch 2026-08-10, not skipped for cost — all four treatments
  were rendered on the real film first. lucid sweeps a `\k` fill. § Captions
  has what would reopen it, and PLAN.md § Per-word caption animation has the
  measurements, which stay valid because they are facts about libass.
* **Marketing motion in the tool.** The replica-hero pattern is for a future
  landing page, not the workspace.

## Build order

Governed by two facts: **the layered timeline is the enabler for most of the
map** (b-roll, graphics, V2 — all illegal to draw before its picture lane),
and **the look pass is the one big item gated on nothing**. PLAN.md § Direction and order owns
where this queue sits against non-parity work; the verified bar for every UI
item is unchanged — real Scream VO, real browser (wiki `tooling.md`
§ Headless browser).

1. **The look/feel pass** — warm tokens light+dark, vendored fonts, three
   type voices, pastel timeline, top-bar parity: **shipped 2026-08-08**
   (HISTORY.md § The look pass). The six small items that were to ride it and
   didn't — model label, thumbs, `@`-mentions, inline pause markers,
   `restore`, export presets — **shipped the same day** as their own step
   (HISTORY.md § The head of the parity queue). This rung is done, except for
   `tiktok-reels`, whose blocker — § Aspect swap's preview letterbox — closed
   2026-08-10, leaving the preset itself.
2. **The layered timeline, steps 1–6** (PLAN.md § The layered timeline) —
   already Next; ends with the picture lane, the legal gate for the rest.
3. **Caption styling** — the style object, agent-settable, burn-in at
   export.
4. **Motion graphics + templates** — design note, then agent-authored assets
   as cues; static templates shipped ahead of this rung and cleared the
   Properties pane's own gate on the way, so the pane no longer waits on
   animation (§ Properties pane, built).
5. **B-roll by description** — costed local design note first, then
   indexing, search, and the placement flows (agent / transcript selection /
   timeline range).
6. **The long tail** — import roles + assets pane, multi-project picker, HTTP
   MCP transport and filmstrip thumbnails are **all built** (their own rows
   above); what is left is aspect swap and snap/lock lane toggles as their
   gates clear.

---

## The gallery, watched: 2026-09-23

Daydream's docs are unchanged since the August capture: the same nine pages,
and no new workflow. The homepage added one claim (CapCut as an export
target, outside what parity imports) and a **"Made with Daydream" gallery of
seven finished films**, 5 to 41 s, which the capture above never took apart.
Tyler's prompt for this pass was that the demo videos show how good the app
looks and how well it works, so the films were the thing to read, not the
feature list.

All seven plus the homepage's `product_motiongraphics.mp4` were downloaded
and tiled with burnt-in timestamps, at 1 to 4 frames a second. The work is at
`~/proofcut-work/spikes/daydream-gallery/`. None of it goes in the repo: the
footage is not proofcut's. **The site serves every file with its audio
stripped**, so this table says nothing about sound. That is not the whole
record: the airplane-windows film is on Daydream's YouTube channel with its
narration, and the channel is read with sound in § The channel, watched.

### What the films are made of

Six of the eight are animated graphics with little or no footage. The one
talking-head film and the one footage-led essay use the same graphic
vocabulary on top. Counted by film:

| technique | films | proofcut today |
|---|---|---|
| Words appear as spoken, fading in, accumulating into a centred line | 6 | **yes, with no new code**: a karaoke caption whose unspoken colour is transparent (below) |
| A single word alone and large in the centre ("well", "ruff.", "rut") | 3 | no; PLAN.md § Per-word caption animation, finding 3 measured this as a different construction and did not build it |
| Emphasis inside a line: weight sweep, highlighter bar, drawn underline | 3 | no |
| Cut-out image stickers (outlined PNGs) sliding or popping in | 4 | no: an overlay must be a recorded card from `lowerthird` or `scrim`, and import takes no still images |
| A tilted photo card with a drop shadow, a hand-drawn circle and arrow drawing on | 1 | no |
| A UI graphic animating: a field that types, then the camera pulls back | 3 | no |
| A letter-by-letter logo build, a spinning circular text badge, a stack of repeating words | 3 | no |
| A graphic-to-graphic transition: phrase fade-out, organic colour wipe | 5 | no: a cue change is a hard cut, and `dissolve` joins recordings only |
| Stills sliding across as a strip; staggered pill chips | 2 | no |
| Footage full frame with a line of type over it | 2 | yes (cue plus overlay or caption) |
| A screen recording inside a device frame | 2 | partly: insets and the eased reframe camera, no device frame |

The pattern: **every film moves something inside a graphic on every beat,
and proofcut's graphics are stills that move only as a whole** (an overlay's
rise and fade). That is exactly the piece § Motion graphics + templates
parked on 2026-08-09, *"Animation gets its own note, after a watch of a real
card-heavy cut"*. The gallery is that watch, and it says animation is most of
what makes these films look finished.

### The rebuild, and what it found

The 10 s dog-hotel ad (`example_airbnbfordogs.mp4`) was rebuilt natively with
proofcut's own commands only: an espeak-ng voice (the demo's route), `seed`,
two `bumper` cards cued by word, one `lowerthird` overlay, and captions. The
project is `rebuild-pupbnb/` in the spike. It renders and burns clean.

1. **Kinetic type is already there, and it is close.** `caption-style
   --preset karaoke --text '#3d414400' --highlight '#3d4144' --position middle
   --max-words 4` makes each word appear as it is spoken and stay until the
   line ends, which is the first four seconds of the original, frame for
   frame in structure. What it lacks is Daydream's short fade on each word.
   PLAN.md § Per-word caption animation, finding 4 already measured that
   `\alpha`, `\blur` and `\frz` animate one word without moving the rest of the
   line, so a per-word fade-in is one `\t` tag per word inside the event
   `to_ass` already writes.
2. **Captions cannot be switched off for a span.** They drew over the logo
   card ("Pup BNB, fetch" sitting under "PupBnB"). The original has no
   captions over its end card, and proofcut has no way to say so.
3. **A caption shows whisper's spelling, and nothing corrects it.** "ruff."
   burned as "rough." and the brand as "Pup BNB,". `lexicon.json` is read by
   `vo_synth` only.
4. **The logo is a hard cut to a static card**, where the original builds the
   letters in, bursts a mark behind them and writes the tagline on.
5. **No stickers.** The palm tree, surfboard and dog have no route in; the
   `lowerthird` stood in for the dog's "ruff!" label and nothing stood in for
   the images.
6. Papercut: `overlay add card:ruff` refuses with *"card 'card:ruff' has no
   record"*, while `overlay add ruff` works, and `cue add` wants the
   `card:` prefix. The refusal names the wrong fault.

The airplane-windows essay was read and not rebuilt: it needs real footage,
and its gaps are the same rows as the table (stickers, the photo card, drawn
annotations, highlighter emphasis, transitions).

### The gaps, ranked (first pass)

**Replaced the same day by § The channel, watched, § The gaps, re-ranked**,
which read 40 more videos. Kept because the rebuild's findings still stand.

1. **Caption reveal, as a preset.** Transparent-until-spoken, a per-word
   fade (`\alpha`, optionally `\blur`) and the single-large-word style,
   plus a caption-off span and a per-word display spelling. **Recommend:
   build first.** Days, not weeks; the metrics were measured in August; it is
   the technique in six of eight films; and the preview must draw the same
   fade, which § Per-word caption animation, finding 5 already warns about.
2. **Image overlays.** An overlay that takes a transparent PNG (a cut-out, or
   a photo proofcut frames with a border, shadow and tilt) placed by rect,
   with pop and slide entrances, and still images accepted at import.
   **Recommend: build second.** The writer already composites alpha PNGs with
   eased `rect` keys (HISTORY.md § Overlays, built); what is missing is the
   route in, not the drawing.
3. **Animated graphics.** Typing fields, drawn strokes, highlighter sweeps,
   letter builds, wipes, staggered chips. **Recommend: a design note, not a
   build.** § Animation is a length problem already settled the constraint
   (intro-then-hold or loop, refuse a fixed length at authoring) and named the
   cheapest mechanism (an SVG frame sequence rendered to a clip, no `mlt.py`
   change). What is open is the renderer: animated SVG or HTML needs a
   headless browser, a new runtime dependency, against per-frame SVG through
   `magick`, whose cost per frame is unmeasured. Daydream's own agent is asked
   for *"a 6s graphic"*, a fixed length, which is the build this repo's note
   refuses; the note should say why proofcut's answer differs.
4. **Graphic-to-graphic transitions.** A fade between cues, and a wipe. Small
   once 3 exists; a plain cue crossfade could come first on its own.
5. **Device frames for screen recordings.** Low: insets and the eased camera
   cover most of it.
6. **The `overlay add card:NAME` refusal**, fixed to accept the prefix or
   name the real fault. Minutes.

**Nothing here is built.**

---

## The channel, watched: 2026-09-23

The gallery pass above read only the eight files the site hosts. The site also
links two YouTube videos and the channel they sit on
(`youtube.com/@daydream_video`), and those are the videos that show the app
working. All 40 were downloaded with their captions (6 long, 33 shorts, one
unlisted demo: 56.7 minutes, every one with sound), and the eight site films
were sampled again at the same density. That is 260 contact sheets at 2 frames
a second (4 under a minute), read by six agents, one batch each, with every
transcript. Their notes, one section per video, are
`~/proofcut-work/spikes/daydream-gallery/NOTES.md`.

**How far those notes can be trusted was measured, not assumed.** A claim from
each batch was read back off a full-size frame (`notes/spot-checks.md`).
Prompts typed large held every time. Small text did not: one agent invented
Daydream's whole model picker (it reads Fable 5.1, Opus 5, Sonnet 5, Haiku 4.5,
GPT-6 Astra and three GPT-5.6 models, not the "Opus 6 / GPT-6.5 Turbo" the
notes gave), and "corrected" a transcript that had the names right. Others put
a feed at the wrong minute, got placement times wrong, or quoted a narration
caption as a typed prompt. **Every quotation below was read off a frame in
this pass**; anything else in NOTES.md is a lead.

### What the channel showed that the gallery could not

1. **A Daydream graphic is a web page, rendered by frame capture.** Its agent,
   on screen, flagging a bug in a fast-cut montage: *"at 1.5s the Anton page is
   still rendering in Helvetica ... The Google Fonts download is losing the
   race against frame capture, so those cuts intermittently show a fallback
   face ... It may resolve in a real export since the page stays loaded longer
   than a preview"* (`tkQXxa2Czqs`, 0:26). The agent writes the page; preview
   and export both capture it. That settles which renderer § The gallery,
   watched left open, at least as Daydream's answer: a browser.
2. **The agent checks its own graphic by looking at a rendered frame.** The
   feed for *"add a graphic of a dog walking in a park"* runs "Loading design
   guidance", "Planning graphic" (a committed concept), "Creating graphic"
   (*"Started 'Dog walking in the park' on V2 at 0.0s (duration 6.0s"*), a
   series of "Editing graphics" steps that build it in layers, and, after the
   user said the legs looked wrong, "Inspecting graphic" and "Capturing
   preview": *"I'm checking a rendered frame to confirm the mouth and leg
   placement"* (`mePPNdZ9lP0`, 11:24 to 11:36). **A graphic is a clip with a
   fixed length**, placed on V2 at a time: the build § Animation is a length
   problem refuses.
3. **Graphics and caption styles are saved to a library that crosses
   projects.** *"save that graphic"* answers *"Saved as 'Recipe Sidebar —
   Lemon Pasta' in Templates → Your graphics"*, *"available in every
   proj[ect]"* (`mePPNdZ9lP0`, 12:01). The Templates tab has three sub-tabs,
   Built-in, Your graphics and Your captions; the built-ins include Basic
   Title, Aurora Text, Typing Text and Spinning Text (`LF_AC7Cckak`, 0:21).
   Named caption looks ("Blue Highlight", "Magazine Cut Out", "Confetti",
   "Blur In", a mascot bouncing on the active word) are saved the same way.
4. **Standing corrections, as memory.** After *"remember to check the captions
   chatgpt often gets mistranscribed to tratchy pt"*, the agent files
   "Suggesting a memory" and says *"Sent that as a 'Remember this?' card in
   the app — approve it and I'll apply it across all your projects"*
   (`LMZezzaS9Tc`, 2:30). This is the rebuild's "rough" for "ruff", solved as
   a user preference rather than a per-word edit.
5. **The agent reports what it did in numbers and asks before a judgment
   call.** *"now 1 second instead of 3 seconds with easeOut easing"*
   (`site_editwithdaydream`); *"Nine attempts at the same line here — I'll
   keep the last clean take (112.2s–116.9s)"* and *"What I left: the ~11s of
   post-take chatter ... Want me to cut that too?"* (`LMZezzaS9Tc`). This is
   the model's behaviour as much as Daydream's, and proofcut's agent panel
   runs the same models.
6. **A transcript selection or an asset rides the prompt as a chip.** A
   selected word shows as `"So" ×` above *"add a typing sound"*
   (`yrnxIjre2VQ`); image files attach as chips and are named with `@` in the
   prompt (`qgwj16ai8mE`, *"Then I want @cursor-transparent.png to slide in
   from off screen and press the button"*).

### Each video, in one line

Longest first. The id is the YouTube id; `site_` is a file the site hosts.

| id | s | what it shows |
|---|---|---|
| `mePPNdZ9lP0` | 988 | The full workflow on GPT-6 Astra: bad takes, breaths, trim to a minute, b-roll by description, pasted and web-found images, generated graphics refined by frame, the graphics and captions libraries, a zoom, a pop synced to an icon |
| `7QfZdeKQn-c` | 525 | The three-part method (clean the talking head, match b-roll, add graphics); a brief read from a Google Doc; a 16-clip house montage; XML into Resolve |
| `gqDcHJWRNjA` | 217 | The site's own demo: import by role, cut pauses and takes, Show cuts, b-roll matched to a selected span, the "fade reveal" template as a CTA, XML into Resolve |
| `LMZezzaS9Tc` | 192 | Cut report ("What I cut"), a doubled transcript found and rebuilt, the memory card, a brief turned into a picture-in-picture layout |
| `LhYoOtG3a0o` | 186 | A Vox-style article graphic from screenshots: staggered entrance, zoom, a focus-hunt blur, a paper texture, a hand-drawn highlight |
| `u0kmyctcVzg` | 156 | An animated bar chart from pasted data, then a second layer and a restyle by prompt |
| `HBXUYaOPppI` | 72 | One footage dump split into four shorts, each its own project, each cleaned |
| `tvkJF_0GOR0` | 72 | One long prompt: cut takes, add b-roll, captions and a graphic |
| `NqxepHkM-F0` | 60 | Easing vocabulary (linear, ease-in, ease-out, ease-in-out) for asking an agent for keyframes |
| `zOC-JgBy2DI` | 49 | The Claude desktop app driving Daydream over MCP: import, transcribe, name projects |
| `CdnzkSSYDE0` | 44 | One product demo into several vertical shorts, screen reframed with a webcam bubble |
| `M5-YtBIehKg` | 43 | Breath removal: the agent finds breaths in the waveform and dips them with volume keyframes; a before/after toggle |
| `58t0JCPaTsU` | 41 | The launch video (same as `site_daydreamlaunch`): typing graphic from a prompt, voiceover as a document, b-roll from a popup |
| `site_daydreamlaunch` | 41 | The silent site copy of `58t0JCPaTsU` |
| `Oa55Y8FSFdo` | 37 | A brief pasted as a Google Doc link; the export menu (MP4, Premiere, Resolve, Final Cut) |
| `hHKO_bJgawQ` | 37 | The one-word fast-cut montage ("Opus") and its prompts: vignette, blur, a scale punch per cut |
| `yrnxIjre2VQ` | 37 | A sound effect placed at a selected transcript word |
| `cM-vA5q_eQM` | 36 | A quick zoom with a hold and a whoosh, from one prompt |
| `lGDSIOTuLhA` | 36 | The Vox article graphic, short version, with the agent's frame-by-frame timing |
| `WBlKeiq8fQc` | 34 | The pitch against editing "in a GitHub page" or in the desktop chat app |
| `site_airplanewindows` | 34 | The silent site copy of `TIdG_LZ4K20` |
| `5q0AFI4n9N4` | 33 | Images three ways: pasted into chat, found on the web ("a logo for google docs"), from an image library |
| `CbHDaqvrumQ` | 33 | Breath removal again, on GPT-6 Astra |
| `TIdG_LZ4K20` | 33 | The airplane-windows explainer with its narration: footage, stickers, a photo card, highlighter and underline sweeps |
| `PhjoiWlrXlk` | 31 | B-roll matched by the footage's own labels, no source reused back to back |
| `hnnlzFcOeHY` | 31 | Captions restyled with an asset (`@claudecodemascot.png`) bouncing on the active word |
| `tkQXxa2Czqs` | 31 | The "Opus" montage again, with the agent's font warning that shows graphics are web pages |
| `Am8s09xpVfg` | 28 | A title on graph paper with a plane cut-out flying under it, then a two-frame hold for a stop-motion feel |
| `ODNnwNLpjFM` | 28 | Five flower images sliding in from four edges to frame a title |
| `hSz2cw9lf5w` | 28 | Find a clip by description in a 13-minute walkthrough |
| `oAfgw58QN_E` | 28 | Trim a five-minute take to a one-minute short |
| `r2WJmK9_tYA` | 28 | Remove pauses and duplicate takes, and the transcript view of the cuts |
| `LF_AC7Cckak` | 26 | Save a graphic; the Templates tab with Built-in, Your graphics, Your captions |
| `HiI6yLKJ2QQ` | 24 | Generated graphics as editable templates: a map route, a recipe card, a poster |
| `gr6eBj_5Kfs` | 24 | Caption styles by prompt: karaoke, boxed, a character bouncing on the word |
| `36R-Twe6ZFs` | 22 | Import by role, clean up, find a clip by description |
| `qgwj16ai8mE` | 22 | A subscribe button with a cursor pressing it, a laser show and a dancing mascot, from one prompt |
| `2d2liLTw3qI` | 21 | Why editing inside a chat app spends its time writing tools (an animated checklist and bar) |
| `2g4eS1t2D2E` | 21 | Blur-in text, word by word, then slower and blurrier by prompt |
| `zEtgEH2IO2M` | 21 | Tabs: two projects, two agents running at once |
| `site_desktopdemo` | 18 | A webcam window with floating image cards and a search box typing, over a gradient |
| `qR4mvf-9kxM` | 18 | "Dynamic tracks": lanes for clips, music, sound effects and graphics |
| `site_editwithdaydream` | 14 | A typing prompt pill, the airplane project in a laptop, the easing reply, a spinning "try for free" badge |
| `site_nova` | 11 | Kinetic type, flower stickers, an organic colour wipe, staggered chips |
| `site_airbnbfordogs` | 10 | Kinetic type, stickers, a letter-by-letter logo build (the rebuild above) |
| `fa3KuL5aOn4` | 10 | Finished graphics rated out of ten, no UI |
| `site_product_motiongraphics` | 6 | A search field typing a URL, then a pull-back |
| `site_skincare` | 5 | A strip of photos sliding across, then a weight sweep across a word |

### Every feature shown, against proofcut

| Daydream feature | videos, about | proofcut |
|---|---|---|
| Remove pauses and duplicate takes, with a cut report | 9 | **yes**: `cut`, `verify`, the transcript's cut view |
| Show cuts, restore a cut word | 3 | **yes** |
| Import by role (talking head vs footage) | 5 | **yes**: `role` |
| Find a clip by description; b-roll matched to narration | 8 | **yes**: `describe`, `broll_brief`, `cue_add` |
| Fast montage of many short clips | 1 | **yes**, as cues |
| Trim to a target length | 2 | **yes**, the agent cuts by transcript; `reel` for a derived cut |
| Breath removal | 3 | **yes**: `attenuate` pulls down short non-speech sounds between words |
| Zoom or push-in with easing | 3 | **yes**: `reframe` windows with an easing |
| Picture-in-picture camera over a screen recording | 3 | **yes**: `inset` |
| Vertical short from a horizontal recording | 2 | **yes**: `canvas`, `reframe` |
| Sound effect at a word or an on-screen moment | 4 | **yes**: `sound add` at a word or event |
| Model picker in the agent panel | 8 | **yes** |
| Agent looks at its own rendered frame | 2 | **yes** for the edit (the four sheets); no graphic to look at |
| A second agent driving the same project from a terminal (MCP) | 3 | **yes**: `proofcut mcp` |
| Words appear as spoken, fading or blurring in | 12 | **partly**: transparent karaoke (§ The gallery, watched), no per-word fade |
| Named, saved caption styles; a character bouncing on the active word | 4 | **partly**: three presets and `caption-style`; no library, no character |
| Caption spelling corrections kept as a standing preference | 2 | **no** |
| Captions off for a span | 1 | **no** |
| Paste or `@` an image; find one on the web | 5 | **no**: import refuses a still |
| Stickers, textures and photo cards over the picture | 8 | **no** |
| Generated animated graphics (typing, charts, illustration, highlight sweeps, montages of fake pages) | 17 | **no**: static cards only |
| Saved graphics library across projects; built-in animated templates | 3 | **no**: eight static templates in the package |
| Per-cut effects: vignette, blur, scale punch, frame-hold | 3 | **no** |
| A brief read from a Google Doc | 3 | **no in the panel**, which has no web or connectors; a person's own Claude Code has them |
| Split one footage dump into several projects | 2 | **no**: a server is bound to one project |
| Tabs, two projects' agents at once | 1 | **partly**: two `proofcut web` processes |
| Memory card for any preference | 1 | **no** (a person's own Claude Code has its own memory) |
| XML to Premiere, Resolve, Final Cut | 4 | **declined** (§ What parity does not import) |

**Everything in the top half is already proofcut's.** (The video counts are tallied off NOTES.md by
hand, so read them as sizes, not measurements.) The cutting and
b-roll half of these videos is the half proofcut was built on. What it lacks
is the look: the graphics, images over the picture, and captions with
character. About a third of the 48 videos are about generated animated
graphics.

One recorded reason needs a second look. § What parity does not import drops
the XML exports because *"No such apps exist on a Linux box"*, but DaVinci
Resolve, the NLE four of these videos export into, ships for Linux. That is
a question for Tyler, not a ranked gap: the handoff is Kdenlive MLT today.

### The gaps, re-ranked

Most value per cost first, each with a recommendation. This replaces
§ The gaps, ranked (first pass).

1. **Caption reveal, and captions that can be corrected.** A per-word fade or
   blur-in, the single-large-word style, captions off for a span, and a
   standing correction list (the project's `lexicon.json` folds, applied to
   captions as well as synthesis). **Recommend: build first.** It is in about a
   dozen videos, the text layout was measured in August, and the correction list
   reuses a file proofcut already reads.
2. **Animated graphics: a design note and a spike, not yet a build.** The
   channel answers the renderer question the first pass left open: Daydream
   captures a web page frame by frame, and its own font bug shows the cost
   (remote fonts racing the capture, which proofcut's vendored fonts would
   not have). The spike should measure headless-browser frame capture on
   this box (speed, determinism, fonts, alpha) against per-frame SVG through
   `magick`. The note must answer the length question too: Daydream's
   graphics are fixed-length clips, the shape § Animation is a length problem
   refuses, so proofcut either keeps intro-then-hold or says why not.
   **Recommend: start the note and spike now, since everything in 3 and 4
   builds on its answer.**
3. **Images in: stills at import, `@` an asset in the prompt, and image
   overlays** (stickers, textures, photo cards) with pop and slide
   entrances. **Recommend: build after 1.** The writer already composites
   alpha PNGs with eased keys; the route in is what is missing, and the
   graphics in 2 need it anyway.
4. **Saved libraries across projects**, for graphics and caption styles.
   **Recommend: design with 2**, since a saved graphic is the thing 2 makes.
5. **Per-cut effects and transitions**: a crossfade between cues, vignette,
   scale punch. Small once 2 exists.
6. **Small and independent**: the `overlay add card:NAME` refusal (minutes);
   whether a server may open a second project for a footage split (a
   question, since `-C` binding is deliberate); the Resolve question above.

**Nothing here is built.** The order waits on Tyler's review.

---

## Copyright, the DMCA, and this work: 2026-09-23

Tyler asked that everything planned here stay within the DMCA. These are the
working rules, and every step of § The gaps, re-ranked is built under them.
They are a careful reading, not legal advice; anything with real money or a
real notice behind it goes to a lawyer.

**What the DMCA reaches here, in two parts.**

- **§ 512, notice and takedown**, reaches what proofcut *publishes*: the
  public repo, the PyPI package, the registry listing. A rights holder's
  notice to GitHub removes the file that infringes. Audited 2026-09-23: the
  repo holds no Daydream media, code, screenshot or frame. Its only material
  from Daydream is this document's description of the product, short
  quotations of its on-screen text for commentary, and the palette's HSL
  values in § Tokens (colours are not protected expression). Daydream's name
  appears in code comments that cite this file, never in the README, the
  manual, the demo, the listings or the window.
- **§ 1201, circumvention**, reaches *how material was obtained*. The eight
  site films were plain files the site serves to every browser. The 40
  YouTube videos were fetched with `yt-dlp`, which YouTube's terms forbid and
  whose status under § 1201 has been argued both ways (the 2020 takedown of
  youtube-dl on GitHub, reversed; a 2021 German lower-court ruling against the host of its website).
  That copy was private research that nobody received, and it was the one
  exposure this work had: a breach of You1. **Downloading videos for research is allowed, and Tyler chose that on
   2026-09-23**, knowing the trade: it breaks YouTube's terms, and its § 1201
   status is unsettled. So a download stays private, is used only for
   analysis, is never shared or committed, and is deleted once its notes are
   written and checked. The notes, in our own words with short quotations,
   are what is kept.
reaming site's videos for research again.** Watch in
   a browser and take notes, or use files a site serves in the open. The
   2026-09-23 downloads are deleted once Tyler has reviewed the notes;
   `NOTES.md` (our words, short quotations) is what is kept.
2. **Nothing of Daydream's goes into the repo or a release**: no frame,
   screenshot, video, audio, template file, graphic, prompt text, "design
   guidance", code from its app bundle, or its marketing copy. CLAUDE.md
   already bars committing a frame of footage proofcut does not own.
3. **Copy the idea, never the expression.** A feature, a workflow, a UI
   layout convention and a technique (a word fading in, a highlighter sweep)
   are free to build. A specific template's artwork, a specific ad's script
   or animation, or a mascot are not. Our templates are drawn from scratch,
   and the PupBnB rebuild stays a private measurement, never a published
   render or demo.
4. **Say "Daydream" only to compare, never to borrow.** No use of its name or
   mark in proofcut's UI, listings or marketing, and nothing that suggests
   a connection. A factual comparison in docs is fine.
5. **Features that fetch other people's work leave the licence with the
   user, on the record.** proofcut does not scrape or download images from
   the web, and ships no third-party stickers, textures or clips unless
   their licence allows it and says so beside the file (the vendored fonts'
   `LICENSE-*.txt` precedent). An image a person adds keeps its provenance
   (where it came from and under what licence) the way a font keeps
   `font_provenance`, and nothing hides that record.
6. **The graphics renderer loads nothing remote.** A browser-rendered graphic
   draws only vendored fonts and project assets. That is the licence rule,
   and it is also the fix for Daydream's own font race (§ The channel,
   watched).
7. **proofcut never grows a downloader.** Import reads files a person
   already has; nothing in `src/` fetches media from YouTube or any
   streaming site, and nothing strips DRM.
