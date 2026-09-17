/**
 * timeline.js — the real timeline of PLAN.md § Tier 3 is the goal:
 * a ruler with time labels, pixels-per-second zoom (fit-to-window
 * by default), sticky track headers, named clip blocks, an A1 waveform
 * canvas from `/api/waveform/<clip_id>`, click-to-seek, and a
 * playhead driven by player.js (PLAN.md § The timeline).
 *
 * Lanes are projections of one `Edit`, never independent tracks, and no
 * lane is drawn that `export` cannot produce — auto-editor 31.x degrades a
 * multi-*source* render to 720x576 with a warning and exit 0 rather than
 * failing, so a lane this view drew ahead of the model would look right
 * and export wrong (CLAUDE.md; PLAN.md § The trap this section exists to
 * write down). V1 only when the displayed clip `has_video`, A1 always (the
 * recording has audio even for a picture clip), CC only when a transcript
 * exists to caption from. V1 and A1 are built from `state.segments`, the same
 * single track `export` reads.
 *
 * **CC is built from `/api/captions`, not from segments**, and the difference
 * is the same rule V2 is held to: a cue breaks on sentence ends, silences and
 * a word count, so one block per segment drew caption lines the `.ass` file
 * will never contain. See `buildCaptionRow`.
 *
 * **V2 is the picture lane, and it became legal at step 5 and not before**
 * (PLAN.md § The layered timeline, build order step 6): `export` renders a
 * layered timeline through MLT and `melt` now, so there is finally a lane
 * the file will agree with. It is drawn from `state.shots` and nothing
 * else — `ops._picture_plan`'s answer, which is `build_shots` *already put
 * through the MLT writer's planner*, so a shot the writer would refuse is
 * never drawn as though it would render. When the plan refuses, the view
 * sends `shots_error` instead of shots and this lane draws the message:
 * a stale cue is exactly the thing a person opens this window to find, so
 * the lane says so rather than quietly disappearing.
 *
 * **A2 is the music bed's lane, legal since the render carried a bed**
 * (PLAN.md § The A2 music lane, step 5): it is drawn from `state.music` and
 * nothing else — `ops._music_plan`'s answer, the same derivation `export`
 * builds its lane from, so the block here is the bed the render will mix
 * (start, audible length, fades) rather than the manifest's ask. When the
 * plan refuses — an orphaned boundary word, fades a cut shrank the bed
 * under — the view sends `music_error` and the lane draws the message,
 * `shots_error`'s policy exactly. No bed, no lane.
 *
 * **And A2 is settable from here, not only drawable.** The lane shipped
 * read-only, with the CLI owning every way to create, move, fade or clear
 * the bed under it — the shape that produced the captionless film, a
 * surface reading clean while the terminal owned the operation. A drag
 * across A2 re-spans the bed, a click on it edits the asset and the fades,
 * and the cue toolbar's "Music bed" verb places the first one (there is no
 * A2 lane to drag on before a bed exists). All four land on
 * `POST /api/music` → `ops.music`, and nothing here decides: the merge
 * rule, the card refusal and the ordering test are the op's, and a bed that
 * stops resolving comes back as this lane's own `music_error`.
 *
 * One honest asymmetry to expect: V2 runs on `export`'s frame grid
 * (`state.shots_rate`) while the ruler runs on the edit's own seconds, so
 * the last shot can end a fraction of a frame past the ruler — 411.077s of
 * picture against 410.963s of edit on the Scream assembly. That is
 * `frame_total` versus a summed duration (CLAUDE.md), not a drawing bug,
 * and the lane is sized to whichever is longer rather than clipped to hide
 * it.
 *
 * The waveform is drawn THROUGH the edit (PLAN.md § Read-model additions):
 * `drawWaveformLane` slices the *cached source* RMS array by each
 * segment's own `start`/`end` every time it draws, so a cut needs no
 * recompute and no timeline-space envelope is ever built or cached here.
 *
 * **Filmstrip thumbnails, V1 and V2, same trap the waveform names above.** A
 * shot (or a segment) plays a stretch of its own source starting at
 * `src_start` (V2) or `start` (V1/A1) — never from the head of the file, so
 * a clip used three times reads from three different places and a filmstrip
 * that reloaded each block from 0 would draw a real-looking, wrong film
 * (CLAUDE.md). `buildFilmstrip` below is given exactly the one source second
 * each block already carries and walks forward from there — the same
 * source-second-plus-offset arithmetic `drawWaveformLane` already does one
 * lane down, just against `/api/thumb/<clip_id>?at=<seconds>` (`ops.thumbnail`,
 * itself bucketed to `THUMB_INTERVAL` and cached under `cache/thumbs/`)
 * instead of a cached RMS array. No client-side cache is kept for it —
 * unlike the waveform, a thumbnail is a plain `<img src>`, and the browser's
 * own HTTP cache already dedupes identical URLs across re-renders.
 *

 * See transcript.js's header comment for the full pane-module interface —
 * `ctx` shape, bus event names, and the `state` shape — this file does not
 * repeat it.
 */

import { $, el, fmt, secs, clampFloating } from "./dom.js";

/** Widest a drag-trim handle is ever drawn, and the narrowest block that gets
 * a pair at all.
 *
 * Both numbers are measured, not picked. Two flat 6px handles cover a block
 * only 14px wide outright, leaving no interior to seek by — but widening the
 * threshold past that is `snapTolerance()`'s doing, not the handles': the
 * tolerance is ~6px expressed in seconds, and `handleLanesMouseUp` calls a
 * trim `moved` only once it exceeds that. On a block narrower than roughly
 * two tolerances, an inward drag is clamped to less than one and the gesture
 * resolves to nothing — **silently**: no preview, no popover, no toast.
 * Measured in a browser at 2.8px handles on a 14px block: the preview drew
 * 2.5px wide and the drag posted nothing at all.
 *
 * So a block under `TRIM_MIN_BLOCK_PX` is not given handles. It stays what it
 * always was, a plain click-to-seek target, and the way to trim a short
 * segment is to zoom in — which works: the film's 1.5s opening segment is
 * 6px of timeline at the zoom the page opens at and 51px at zoom 10, where it
 * trims correctly. Not offering a gesture is better than offering one that
 * does nothing without saying so. */
const TRIM_HANDLE_PX = 6;
const TRIM_MIN_BLOCK_PX = 24;

let ctx = null;
let lastState = null;
let zoomMultiplier = 1; // multiplies the fit-to-window base — #zoom is 1..10
let currentPxPerSec = 1; // cached for the per-frame playhead handler, which
// must not pay for a full re-render 60 times a second
let laidOutHeight = null; // ...and its clientHeight, for the observer below
let laidOutWidth = null; // the #track-lanes clientWidth the last render() laid
// the lanes out against — NOT a cache, a staleness check. A hidden pane
// measures 0, and computePxPerSec falls back to 800 rather than to nothing, so
// a render that lands while Edit is not the visible mode draws every lane
// against a width no pane has. Nothing re-measures on the way back in:
// `hidden` flips, layout happens, and the lanes keep the geometry they were
// built with. Comparing against this on the `mode` event is what catches it —
// see init()'s subscription.
let followPlayhead = true; // F5, default ON per the contract — index.html's
// own #follow-playhead ships with its 'on' class already applied so there is
// no flash of the wrong state before this file's init() runs; this variable
// just has to agree with that markup, not set it.
let lastFollowScrollLeft = null; // the scrollLeft THIS FILE last set via the
// follow nudge, compared against what #track-lanes's own 'scroll' event later
// reports — not a boolean "ignore the next event" flag. A flag cannot survive
// a nudge that lands on a value the lane is already at: setting scrollLeft to
// its current value dispatches NO 'scroll' event at all, so a flag armed and
// never consumed would misattribute some LATER real user scroll as the nudge
// that never fired, and follow would silently fail to disengage. Comparing
// the reported value against the one this file itself last wrote means a
// stale unconsumed value only ever fails to match a real scroll to a
// different pixel — it does not falsely swallow one.
let selection = null; // word indices to highlight — set by this file's own
// drag gesture below, or by the 'selection' bus event for any other pane
// that wants to drive the highlight
let gesture = null; // {kind: "cue"|"trim"|"razor", ...} while a mousedown on
// the lanes is live — the ONE piece of live-gesture state, discriminated by
// `kind` so mousemove/mouseup can branch. Replaces the old single-purpose
// `cueDrag`; a "cue" gesture is exactly what `cueDrag` used to be
// ({anchorIndex, currentIndex, moved} — anchorIndex is the drag's *start*
// word, the only address `cue_add` uses). A "trim" gesture is
// {clipId, edge, origStart, origEnd, proposedTime, direction}; a "razor"
// gesture is {startTime, currentTime, moved}. Still exactly one mousedown/
// mousemove/mouseup triple (init(), below) — a second gesture kind is a new
// branch inside the existing handlers, never a second listener.
let cueSelection = null; // {wordIndex, boxLeft, boxTop} once a real drag
// (gesture.moved) finishes — drives the floating cue-placement toolbar
let cueToolbarEl = null;
let cueInfoEl = null;
let cueAssetInputEl = null; // exposed (not just a buildCueToolbar() local) so
// openCuePlacement — shared by the razor "place b-roll" verb and an
// asset-drag drop — can prefill it
let suppressNextClick = false; // set when a drag moved, so the native
// 'click' a mouseup can still fire doesn't also trigger seekOnClick

let toolMode = "select"; // "select" | "razor" — a tool MODE, not a
// persistent preference, so #razor-tool ships with no anti-flash class the
// way #follow-playhead's 'on' has
let snapEnabled = true; // #snap-toggle — on by default, markup carries the
// 'on' class already for the same no-flash reason follow-playhead's does

let planSelection = null; // the plan-echo popover's own selection, parallel
// to cueSelection: {kind:"cut", span, clipId, boxLeft, boxTop, result?, error?} |
// {kind:"restore", ranges, clipId, boxLeft, boxTop, result?, error?} |
// {kind:"razor-choice", span, boxLeft, boxTop} — never mutated by a gesture
// directly, only read by refreshPlanToolbar()
let planToolbarEl = null;
let planInfoEl = null;
let planWarnEl = null;
let planActionsEl = null;
let planConfirmCheckbox = null; // the suspect-boundary confirmation
// checkbox, rebuilt fresh into planActionsEl each refreshPlanToolbar() call
// that has suspect boundaries to show — null otherwise, so applyCutPlan can
// tell "no gate needed" from "gate needed, unchecked" without a stale
// reference to a checkbox no longer in the DOM

let musicSelection = null; // the A2 bed panel's own selection, a third
// parallel to cueSelection/planSelection: {wordIndexStart, wordIndexEnd,
// boxLeft, boxTop}. BOTH indices are null when the panel was opened by a
// click on the bed rather than by a drag — that open changes the asset or
// the fades and leaves the span exactly where it is, which is `ops.music`'s
// own partial-update shape rather than a second one invented here.
let musicToolbarEl = null;
let musicInfoEl = null;
let musicAssetInputEl = null;
let musicFadeInEl = null;
let musicFadeOutEl = null;
let musicToEndEl = null;
let musicActionsEl = null; // rebuilt each refresh (planActionsEl's own
// treatment), because "Remove bed" is only a verb once a bed exists

let dropGhostEl = null; // the ONE standalone node the HTML5 drag-and-drop
// listeners drive (item G) — a separate lifecycle from `gesture` above
// (native dragover/dragleave/drop, not mouse events), so it is never touched
// by updateGestureOverlay()

const MIN_PX_PER_SEC = 4; // guards a zero/near-zero duration from a divide
const LANE_H_FALLBACK = 42; // matches app.css's --lane-h if the var lookup fails
/** Ceiling for a lane grown by `fitLaneHeight`, read from app.css so the
 *  stylesheet stays the one place a layout metric is stated. Past it a lane
 *  is not more legible, just bigger — the filmstrip frames are already
 *  source-resolution and the waveform is already at full scale. */
const LANE_H_MAX_FALLBACK = 88;
const LABEL_MIN_PX = 70; // minimum on-screen spacing before a ruler label repeats
const THUMB_TARGET_PX = 64; // desired on-screen width per filmstrip frame
const THUMB_MIN_BLOCK_PX = 24; // below this a block is too narrow for even one legible frame

/** Narrowest block that gets a text label at all.
 *
 * Measured, not picked — the sibling rule above, and the same method. Every
 * block on every lane is clipped to its own width, so a label does not
 * shrink, it *truncates*, and a truncation short enough stops being a word:
 * the film's V2 lane at the zoom the page opens at drew `c`, `re`, `col`,
 * `s199` and `rece`, and the CC lane drew one or two letters of a sentence
 * in all 178 cues. None of it names anything.
 *
 * The boundary was read off the film's own 38 shots by measuring how many
 * characters each block actually shows (Geist Sans 11px, against the label's
 * real position inside the block rather than a padding model):
 *
 *     26.7px -> 3ch  "col"      (cold-open)
 *     30.2px -> 4ch  "rece"     (receipt-scream-2022)
 *     37.9px -> 4ch  "s199"     (s1996-billy-stu)
 *     ----------------------------------------- 40
 *     41.5px -> 6ch  "vi-bai"   (vi-bailey)
 *     46.5px -> 6ch  "s4-ove"   (s4-overexposed)
 *     50.4px -> 7ch  "cold-op"  (cold-open)
 *
 * Six characters is where a fragment starts telling two assets apart —
 * `vi-bai` is not `vi-ric`, `s4-ove` is not `s4-rev` — and 37.9 -> 41.5 is
 * where the sixth character arrives, so 40 is the gap it arrives across.
 * Nothing is lost by dropping the rest: **every block already carries a full
 * `title`** (this function, `buildPictureRow` and `buildCaptionRow` each set
 * one), and zooming in brings the label back the way it brings back a trim
 * handle — the film's narrowest shot is 14px at zoom 1 and 140px at zoom 10.
 *
 * It gates on the block, not on the text, so a label short enough to fit
 * whole in 30px (`vo`, on a one-clip A1) goes too. That is deliberate: 63
 * blocks all reading `vo` over a waveform is the same debris in a tidier
 * font, and a rule that measures each string would make what the lane draws
 * depend on how the clips happen to be named. */
const LABEL_MIN_BLOCK_PX = 40;

/** Widest on-screen gap between two cues that still counts as one run.
 *
 * A *drawing* threshold, deliberately in pixels rather than seconds: it asks
 * "would a person see daylight between these two blocks", which is a question
 * about the zoom, not about the captions. That is what makes the coalescing
 * in buildCaptionRow safe — it un-merges on its own as you zoom in, and at
 * any zoom where the blocks clear LABEL_MIN_BLOCK_PX it never fires at all,
 * so the lane's per-cue shape is always one gesture away. */
const MERGE_GAP_PX = 3;

/** Closest two drawn cue boundaries may sit inside a band before the band
 *  stops reading as one block with divisions in it. */
const TICK_MIN_GAP_PX = 14;

//: "Nice" ruler intervals, seconds — the smallest one that keeps labels this
//: side of LABEL_MIN_PX apart at the current zoom is picked.
const NICE_INTERVALS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

// clip_id -> {status: "loading" | "ready" | "error", data}. One fetch per
// clip for the life of the page; a project with one source clip (the only
// kind that exists today, per the decision gate) fetches this exactly once.
const waveformCache = new Map();

function header(label, title) {
  const node = el("div", "track-header", label);
  if (title) node.title = title;
  return node;
}

/** Click anywhere in a lane — including on a child block — seeks the player.
 * Event bubbling plus the row's own bounding rect, so it works regardless of
 * scroll or which child was hit. Shared by every lane so the picture lane
 * cannot drift into being the one that does not seek. */
function seekOnClick(row, pxPerSec) {
  row.addEventListener("click", (event) => {
    if (!ctx) return;
    const rect = row.getBoundingClientRect();
    ctx.player.seek((event.clientX - rect.left) / pxPerSec);
  });
}

/** Grow the lanes to fill the height the pane actually has.
 *
 * `--lane-h` was a flat 42px, which is right at the pane's old 198px and
 * leaves 188px of nothing once the preview hands its unused height over
 * (player.js § balancePanes). Dead space in the timeline is not an
 * improvement on dead space in the preview: what the extra height is FOR is
 * taller filmstrip frames and a waveform drawn at a scale you can read.
 *
 * Writes `--lane-h` rather than a per-element height on purpose — `.lane` and
 * `.track-header` both size off that one variable, and the sticky header
 * column desyncing from the lanes it labels is the failure this avoids by
 * construction. Returns whether it changed anything, because the waveform is
 * a canvas and has to be repainted at the new height by the caller. */
function fitLaneHeight(count) {
  const lanes = $("track-lanes");
  if (!lanes || !count) return false;
  const root = document.documentElement;
  const style = getComputedStyle(root);
  const rulerH = parseFloat(style.getPropertyValue("--ruler-h")) || 22;
  const maxH = parseFloat(style.getPropertyValue("--lane-h-max")) || LANE_H_MAX_FALLBACK;
  const room = lanes.clientHeight - rulerH;
  // A hidden pane measures 0 — the same trap the `mode` listener below
  // records. Laying out against it would pin every lane to the floor.
  if (room <= 0) return false;
  const next = Math.max(LANE_H_FALLBACK, Math.min(maxH, Math.floor(room / count)));
  if (Math.abs(next - laneHeightPx()) < 1) return false;
  root.style.setProperty("--lane-h", `${next}px`);
  return true;
}

function laneHeightPx() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--lane-h");
  const n = parseFloat(raw);
  return Number.isFinite(n) && n > 0 ? n : LANE_H_FALLBACK;
}

/** The waveform's ink, read off the canvas's own computed `color`.
 *
 * A canvas fill cannot inherit, so the colour has to come across from CSS
 * somehow — and it is deliberately NOT read from the `--waveform-ink` custom
 * property, because a custom property reads back as its literal text
 * (`light-dark(…)`), which `fillStyle` cannot parse and silently ignores. A
 * real `color` on a real element is resolved to `rgb(…)` before JS sees it.
 * app.css's header has the full account; this was a black-on-black waveform
 * in the dark theme until a real browser showed it. */
function canvasInk(canvas, fallback) {
  return getComputedStyle(canvas).color || fallback;
}

/** Fit-to-window pixels-per-second, times the #zoom slider's multiplier. */
function computePxPerSec(duration) {
  const container = $("track-lanes");
  const width = (container && container.clientWidth) || 800;
  const base = duration > 0 ? width / duration : width;
  return Math.max(MIN_PX_PER_SEC, base * zoomMultiplier);
}

function pickInterval(pxPerSec) {
  for (const interval of NICE_INTERVALS) {
    if (interval * pxPerSec >= LABEL_MIN_PX) return interval;
  }
  return NICE_INTERVALS[NICE_INTERVALS.length - 1];
}

/** The duration every lane and the ruler are laid out against.
 *
 * `state.timeline_duration` is the Edit's own answer and is very nearly this
 * number, but not exactly: each lane draws a different projection, and the
 * last thing in one can end a few milliseconds past it — a segment edge
 * quantises on its own (CLAUDE.md § A frame count comes from
 * `autoeditor.frame_layout`), and `/api/captions` is a separate derivation
 * whose final cue ends where the .ass ends. Laying the lanes out at
 * `timeline_duration` and then drawing content past it is what made
 * "fit to window" not fit: on the film the last A1 block overran by 5px and
 * the last three cues by up to 12px, `scrollWidth` exceeded `clientWidth`,
 * and the horizontal scrollbar that appeared took enough height off
 * `#track-lanes` to trigger a VERTICAL one as well — which clipped the CC
 * lane's bottom edge. Two scrollbars on a timeline that fits, from twelve
 * pixels. Measured in a real browser; `render()` is the only caller. */
function contentDuration(state, captions) {
  // Every widen goes through this, and it drops anything that is not a real
  // number rather than propagating it. `Math.max(x, undefined)` is NaN, NaN
  // survives every subsequent Math.max, and `computePxPerSec` answers a NaN
  // duration with the container's full width as PIXELS PER SECOND — which
  // laid the film's lanes out 442618px wide, drew every caption block 2800px
  // across, and threw no error at all. Cost one browser pass; the guard is
  // the fix, not the field name that happened to be wrong (a shot carries
  // `start` + `duration`, never `end`).
  let end = 0.001;
  const widen = (value) => {
    if (Number.isFinite(value) && value > end) end = value;
  };
  widen(state.timeline_duration);
  // Every segment, not `segments[segments.length - 1]`: the array is the
  // Edit's own order and its last element is not necessarily the latest on
  // the timeline. Taking the last one left the film's A1 lane 5px short of
  // its own final block, which is the whole class of bug this function
  // exists to close.
  for (const seg of state.segments) widen(seg.timeline_end);
  if (captions && captions.cues && captions.cues.length) {
    widen(captions.cues[captions.cues.length - 1].end);
  }
  for (const shot of state.shots || []) widen(shot.start + shot.duration);
  for (const overlay of state.overlays || []) widen(overlay.timeline_end);
  return end;
}

function buildRuler(duration, pxPerSec) {
  const ruler = el("div", "ruler");
  const width = Math.max(1, duration * pxPerSec);
  ruler.style.width = `${width}px`;
  const interval = pickInterval(pxPerSec);
  for (let t = 0; t <= duration + 1e-6; t += interval) {
    const tick = el("div", "ruler-tick");
    tick.style.left = `${(t * pxPerSec).toFixed(1)}px`;
    ruler.append(tick);
    // The tick always draws; its label draws only if it fits inside the
    // ruler. Estimated rather than measured, because the element is not in
    // the document yet and a layout read per tick would be a reflow per
    // tick — JetBrains Mono at 10px is ~6.1px per character, and the text is
    // always `m:ss.s`, so the estimate is over the real 36px, never under.
    // Without this the last label is the widest thing in the whole scroller:
    // on the film "5:30.0" sat at 1323px of a 1345px ruler and pushed
    // `scrollWidth` to 1359 on its own.
    const text = fmt(t);
    const left = t * pxPerSec + 3;
    if (left + text.length * 6.4 <= width) {
      const label = el("div", "ruler-label", text);
      label.style.left = `${left.toFixed(1)}px`;
      ruler.append(label);
    }
  }
  return ruler;
}

/** The CC lane: one block per *cue*, straight off `/api/captions`.
 *
 * Not one block per segment, which is what this drew until captions grew a
 * stored style. A segment is a piece of the edit and a cue is a line of
 * subtitle, and they are not the same shape — cues break on sentence ends,
 * silences and a word count, so a lane of segments showed caption blocks the
 * `.ass` file will never contain. That is the picture lane's rule applied to
 * this one (never draw a lane the export cannot produce), and it is the same
 * failure in a quieter register: nothing looks wrong, it is just a different
 * set of captions from the ones that ship.
 *
 * Consequently the grouping is never computed here — `ops._caption_cues` is
 * the one derivation, and this draws its answer, refusals included. */
function buildCaptionRow(captions, pxPerSec, duration) {
  const row = el("div", "lane lane-cc");
  row.style.width = `${Math.max(1, duration * pxPerSec)}px`;

  if (!captions || captions.cues_error) {
    const why = captions ? captions.cues_error : "captions unavailable";
    row.append(el("div", "lane-refusal", `no captions — ${why}`));
    // Same fix as buildPictureRow's identical branch, same reason: a
    // refusal should not silently drop the lane out of seekOnClick's "every
    // lane" guarantee.
    seekOnClick(row, pxPerSec);
    return row;
  }

  // Runs, not cues, and only where a cue is too narrow to say anything.
  //
  // The label gate below (LABEL_MIN_BLOCK_PX, shared with the clip lanes) is
  // right — a sentence in 14px of lane draws one letter — but on its own it
  // traded illegible text for NOTHING: on the film every one of the 178 cues
  // is 14–16px at fit zoom, so this lane drew 178 empty bordered boxes, a
  // barcode across the bottom of the window that reads as a lane that failed
  // to load rather than as captions. The one thing a caption lane has to
  // answer at a glance is *where the captions are and where they are not*,
  // and 178 identical empty boxes answer it worse than one band does.
  //
  // So: consecutive cues that are each below the gate and separated by less
  // than MERGE_GAP_PX of daylight draw as a single band, with a hairline tick
  // at every internal cue boundary. Nothing is merged that is not adjacent,
  // no cue is invented or dropped, and the ticks keep the real shape visible
  // — `ops._caption_cues` is still the only thing that decides what a cue is
  // (this function's header rule). Both halves of the test are in PIXELS, so
  // this is a statement about the zoom rather than about the film: zoom in
  // and the runs come apart into the per-cue blocks again, each with its text.
  const runs = [];
  for (const cue of captions.cues) {
    const narrow = (cue.end - cue.start) * pxPerSec < LABEL_MIN_BLOCK_PX;
    const open = runs.length ? runs[runs.length - 1] : null;
    const joins =
      open && open.narrow && narrow && (cue.start - open.end) * pxPerSec < MERGE_GAP_PX;
    if (joins) {
      open.end = cue.end;
      open.cues.push(cue);
    } else {
      runs.push({ start: cue.start, end: cue.end, cues: [cue], narrow });
    }
  }

  for (const run of runs) {
    const block = el("div", "clip-block");
    const blockWidth = Math.max(1, (run.end - run.start) * pxPerSec);
    block.style.left = `${(run.start * pxPerSec).toFixed(1)}px`;
    block.style.width = `${blockWidth.toFixed(1)}px`;

    if (run.cues.length === 1) {
      const cue = run.cues[0];
      // Same gate as the clip lanes, same tooltip holding the whole text.
      if (blockWidth >= LABEL_MIN_BLOCK_PX) block.textContent = cue.text;
      block.title = `${fmt(cue.start)}–${fmt(cue.end)} · ${cue.words.length} words\n${cue.text}`;
    } else {
      block.classList.add("cc-band");
      // The count, never the first cue's text: a band spans whole sentences
      // and labelling it with one of them claims the others are it.
      const words = run.cues.reduce((n, c) => n + c.words.length, 0);
      if (blockWidth >= LABEL_MIN_BLOCK_PX) block.textContent = `${run.cues.length} lines`;
      block.title = `${fmt(run.start)}–${fmt(run.end)} · ${run.cues.length} caption lines · ${words} words\nzoom in to read them`;
      // Internal boundaries. The first cue's own start is the band's left
      // edge and is already drawn by the border, so ticks begin at the second
      // — and a tick is dropped unless it clears the last DRAWN one by
      // TICK_MIN_GAP_PX. Without that gate the band is the barcode again in
      // miniature: 73 cues across 559px is a hairline every 7.6px, which
      // stops reading as "these are the boundaries" and starts reading as a
      // texture. Spaced ticks say the same thing and the count in the label
      // says the rest.
      let lastTick = 0;
      for (const cue of run.cues.slice(1)) {
        const at = (cue.start - run.start) * pxPerSec;
        if (at - lastTick < TICK_MIN_GAP_PX) continue;
        lastTick = at;
        const tick = el("div", "cc-cue-tick");
        tick.style.left = `${at.toFixed(1)}px`;
        block.append(tick);
      }
    }

    // A CueWord (captions.py) carries text/start/end only, no transcript word
    // index — grouping is a placed, styled derivation and does not keep one.
    // Nearest-by-time against the transcript's own words is the same
    // approximation `nearestWordAt` already makes for a lane drag. A band
    // resolves the click to the cue actually under it first, so clicking one
    // still inspects that line and not the run's first.
    block.addEventListener("click", (event) => {
      if (!ctx || !lastState || !lastState.words) return;
      const at = run.start + (event.offsetX || 0) / pxPerSec;
      const cue = run.cues.find((c) => at < c.end) || run.cues[run.cues.length - 1];
      const word = nearestWordAt(lastState.words, cue.start);
      if (word) ctx.emit("inspect-word", { clipId: lastState.clip_id, wordIndex: word.index });
    });
    row.append(block);
  }

  seekOnClick(row, pxPerSec);
  return row;
}

/** One filmstrip: a row of `<img>` elements reading `clipId`'s own source
 * forward from `sourceStart`, one every `THUMB_TARGET_PX`-ish on-screen
 * pixels — the mapping this file's header comment names as the trap. Each
 * frame's `at=` is `/api/thumb`'s own address (source seconds, snapped
 * server-side to `THUMB_INTERVAL`), never a timeline second and never 0.
 *
 * Every image gets an explicit pixel width up front rather than sizing off
 * its own decoded aspect ratio — an unset width collapses to 0 until the
 * network round trip finishes, which would draw an empty lane on first
 * paint and reflow every block under it once thumbnails arrived. `object-fit:
 * cover` (app.css) crops each frame to that fixed box instead.
 *
 * A block wider than its own duration's worth of pixels near its right edge
 * (the last frame, clipped by the block's own `overflow: hidden`) is normal
 * and left alone — matching how the picture lane already tolerates its own
 * frame-grid/ruler-seconds mismatch (this file's header comment, one
 * paragraph up). Returns `null` for a block too narrow to bother (below
 * `THUMB_MIN_BLOCK_PX`), so a caller can skip appending it. */
function buildFilmstrip(clipId, sourceStart, durationSec, pxPerSec) {
  if (!(durationSec > 0) || !(pxPerSec > 0) || durationSec * pxPerSec < THUMB_MIN_BLOCK_PX) {
    return null;
  }
  const strip = el("div", "filmstrip");
  const stepSec = Math.max(1, THUMB_TARGET_PX / pxPerSec);
  for (let t = 0; t < durationSec; t += stepSec) {
    const widthPx = Math.min(stepSec, durationSec - t) * pxPerSec;
    if (widthPx < 2) continue;
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.style.width = `${widthPx.toFixed(1)}px`;
    img.src = `/api/thumb/${encodeURIComponent(clipId)}?at=${(sourceStart + t).toFixed(3)}`;
    // A clip with no video track (audio-only, or media missing from disk)
    // 400s — same "refused" discipline as the picture lane's own
    // `shots_error`, but at single-frame granularity a toast per image would
    // be noise, so this just leaves the block's own label showing through
    // an empty strip rather than a broken-image glyph.
    img.addEventListener("error", () => {
      img.style.display = "none";
    });
    strip.append(img);
  }
  return strip;
}

/** One row: a `.clip-block` per timeline segment (hover/title/hit-testing,
 * per PLAN.md § The timeline — DOM, not canvas, for exactly this reason)
 * plus a `.seam-tick` per cut boundary, and a click-to-seek handler on the
 * row itself so a click anywhere in the lane — including on a child block —
 * seeks the player (event bubbling; the handler reads the row's own
 * bounding rect, so it works regardless of scroll or which child was hit). */
function buildLaneRow(kind, segments, pxPerSec, duration, state, withFilmstrip) {
  const row = el("div", `lane lane-${kind.toLowerCase()}`);
  row.style.width = `${Math.max(1, duration * pxPerSec)}px`;

  for (const seg of segments) {
    const block = el("div", "clip-block");
    const blockWidth = Math.max(1, (seg.timeline_end - seg.timeline_start) * pxPerSec);
    block.style.left = `${(seg.timeline_start * pxPerSec).toFixed(1)}px`;
    block.style.width = `${blockWidth.toFixed(1)}px`;
    block.title = `${seg.clip_id} · source ${secs(seg.start)}–${secs(seg.end)} · timeline ${fmt(seg.timeline_start)}–${fmt(seg.timeline_end)}`;
    // The label is drawn only where it can be read; `block.title` above is
    // what a narrow block answers with instead (LABEL_MIN_BLOCK_PX).
    const labelled = blockWidth >= LABEL_MIN_BLOCK_PX;
    if (withFilmstrip) {
      const strip = buildFilmstrip(seg.clip_id, seg.start, seg.end - seg.start, pxPerSec);
      if (strip) block.append(strip);
      if (labelled) block.append(el("span", "clip-label", seg.clip_id));
    } else if (labelled) {
      block.textContent = seg.clip_id;
    }
    // Drag-trim handles on **both** lanes this function draws, because V1 and
    // A1 are the same `state.segments` shown twice and a trim on either
    // resolves to the same `cut_by_time` span. docs/plans/STUDIO.md says "V1 block
    // edges", and taking that literally put the gesture out of reach of
    // exactly the projects proofcut exists for: V1 is built only when the
    // displayed clip `has_video` (see render()), so a VO-driven essay — the
    // shipped film included — has no V1 lane at all, and drag-trim was
    // unreachable on it. Measured in a browser against a real project, where
    // the lanes came back V2/A1/CC and `.trim-handle` count was 0.
    //
    // Carries the segment's own clip_id and timeline edges as data-
    // attributes rather than a closure, because handleLanesMouseDown reads
    // them straight off `event.target.closest(".trim-handle")` — the handle
    // is the mousedown's own target, and per this file's central rule a live
    // gesture must never rebuild the node it is anchored to, so nothing here
    // may depend on `seg` still being in scope by drag time.
    //
    // Handles only where the gesture can actually complete — see
    // TRIM_MIN_BLOCK_PX. On the dogfood film's own 63 segments this is not an
    // edge case: at the zoom the page opens at the median block is 17.9px and
    // 33 of the 63 are under 20px, so the un-thresholded version offered a
    // trim on more than half the film that silently did nothing.
    const handlePx = Math.min(TRIM_HANDLE_PX, blockWidth / 3);
    if ((kind === "V1" || kind === "A1") && blockWidth >= TRIM_MIN_BLOCK_PX) {
      const inH = el("div", "trim-handle trim-handle-in");
      const outH = el("div", "trim-handle trim-handle-out");
      for (const h of [inH, outH]) {
        h.dataset.clipId = seg.clip_id;
        h.dataset.segTimelineStart = seg.timeline_start;
        h.dataset.segTimelineEnd = seg.timeline_end;
        h.style.width = `${handlePx.toFixed(1)}px`;
      }
      inH.dataset.edge = "in";
      outH.dataset.edge = "out";
      block.append(inH, outH);
    }
    row.append(block);
  }

  // A retime's stretches, as bands under the blocks: where the render plays
  // this Edit at another speed. Never a hit target — the blocks and handles
  // under them keep every gesture; the preview's chip carries the numbers.
  if ((kind === "V1" || kind === "A1") && state.retime && !state.retime.error) {
    for (const stretch of state.retime.stretches) {
      const band = el("div", "retime-band");
      band.style.left = `${(stretch.edit_start * pxPerSec).toFixed(1)}px`;
      band.style.width = `${Math.max(1, (stretch.edit_end - stretch.edit_start) * pxPerSec).toFixed(1)}px`;
      band.dataset.speed = stretch.speed;
      row.append(band);
    }
  }

  for (const seam of state.seams) {
    const tick = el("div", "seam-tick");
    tick.style.left = `${(seam.timeline_time * pxPerSec).toFixed(1)}px`;
    const before = seam.before ? seam.before.text : "…";
    const after = seam.after ? seam.after.text : "…";
    tick.title = `cut ${secs(seam.removed)} of source — ]${before}  ${after}[`;
    row.append(tick);
  }

  seekOnClick(row, pxPerSec);
  return row;
}

/** What a shot shows, said the way a person names it: a card by its own name,
 * a film clip by its clip_id. `card:` is the cue table's own prefix and it is
 * noise once the block is tinted as a still. */
function shotLabel(shot) {
  return shot.asset.startsWith("card:") ? shot.asset.slice("card:".length) : shot.asset;
}

/** Everything a shot is, on hover — and the two facts that are only visible
 * here. **Where inside the asset it reads**, because a clip used three times
 * shows three different stretches of itself and a lane of identical blocks
 * cannot say which; and **the cue that put it there**, by word index and text,
 * because that is the thing a person edits to move the shot (the word index
 * addresses the source and never renumbers — PLAN.md § The property everything
 * below defends). The first shot says out loud that it does not start at its
 * own cue: the picture track is contiguous by construction, so whichever cue
 * resolves first covers from the open regardless of where its word lands.
 *
 * Whether that in-point is *pinned* is the third fact, and it is not cosmetic:
 * an unpinned shot's content slides when an upstream cue moves, and a pinned
 * one's does not — it shows the moment its cue names or `export` refuses
 * (PLAN.md § B-roll by description). Two shots reading from the same second
 * look identical here otherwise. */
function shotTitle(shot, state, index) {
  const rate = state.shots_rate;
  const pinned = shot.src_pin !== null && shot.src_pin !== undefined;
  const lines = [
    `${shotLabel(shot)} · ${fmt(shot.start)}–${fmt(shot.start + shot.duration)} · ${shot.frames} frames${rate ? ` @ ${rate.toFixed(3)}fps` : ""}`,
    shot.is_image
      ? "a card, held for the shot"
      : `reads the asset from ${fmt(shot.src_start)}${pinned ? " — pinned there by the cue" : ""}`,
    `cue: ${shot.clip_id} word ${shot.word_index} — ${shot.text}`,
  ];
  if (index === 0) lines.push("(the first shot covers from the open, not from its own cue's word)");
  return lines.join("\n");
}

/** V2 — the picture lane, drawn from `state.shots` and nothing else.
 *
 * `state.shots` is the projection *already put through the MLT writer's
 * planner* (`ops._picture_plan`), so every block here is a shot `export` will
 * actually produce. When the plan refuses instead, the view sends
 * `shots_error` and this draws the message across the lane: a cue that was
 * cut, or a shot longer than the clip it points at, is the thing a person
 * opened this window to find, and a lane that silently vanished would hide it.
 */
function buildPictureRow(state, pxPerSec, duration) {
  const row = el("div", "lane lane-v2");
  const shots = state.shots || [];
  // The picture runs on export's frame grid and the ruler on the edit's
  // seconds, so the last shot can end a hair past the ruler — size to the
  // longer of the two rather than clip the difference out of sight.
  const last = shots.length ? shots[shots.length - 1] : null;
  const end = last ? last.start + last.duration : duration;
  row.style.width = `${Math.max(1, Math.max(duration, end) * pxPerSec)}px`;

  if (state.shots_error) {
    row.append(el("div", "lane-refusal", `picture refused — ${state.shots_error}`));
    // seekOnClick is "shared by every lane so the picture lane cannot drift
    // into being the one that does not seek" (this file's own doc comment
    // on seekOnClick) — a refusal is exactly the state that comment is
    // guarding against silently losing, so it still applies here. Found via
    // the browser pass's assertion-5 control click landing on a refused V2
    // lane and doing nothing.
    seekOnClick(row, pxPerSec);
    return row;
  }

  shots.forEach((shot, index) => {
    const block = el("div", `clip-block shot-block${shot.is_image ? " shot-card" : ""}`);
    const blockWidth = Math.max(1, shot.duration * pxPerSec);
    block.style.left = `${(shot.start * pxPerSec).toFixed(1)}px`;
    block.style.width = `${blockWidth.toFixed(1)}px`;
    block.title = shotTitle(shot, state, index);
    const labelled = blockWidth >= LABEL_MIN_BLOCK_PX;
    // A card is a still asset, not a clip's own footage — /api/thumb only
    // ever answers for a video-carrying clip_id (ops.thumbnail refuses one
    // with no video track), so there is nothing to sample for one.
    //
    // **`shot.asset` is the footage being shown; `shot.clip_id` is the cue's
    // OWN address — the transcript clip its word_index lives on, which on
    // this project is `vo`, an audio-only clip with no video track of its
    // own.** Sampling `shot.clip_id` here would ask `/api/thumb` for a frame
    // of the voiceover on every single shot and 400 every time — exactly
    // the reload-from-the-wrong-source trap this file's header comment
    // names, just one field over rather than one asset-use over. `asset` is
    // what `shotLabel`/`shotTitle` already draw the block's own label from.
    if (!shot.is_image) {
      const strip = buildFilmstrip(shot.asset, shot.src_start, shot.duration, pxPerSec);
      if (strip) block.append(strip);
      if (labelled) block.append(el("span", "clip-label", shotLabel(shot)));
    } else if (labelled) {
      block.textContent = shotLabel(shot);
    }
    // Target-phase listener: fires before the row's own bubble-phase
    // seekOnClick (below), so a shot click both inspects its cue AND seeks —
    // harmless, and it means this needs no stopPropagation.
    block.addEventListener("click", () => {
      if (ctx) ctx.emit("inspect-word", { clipId: shot.clip_id, wordIndex: shot.word_index });
    });
    row.append(block);
  });

  seekOnClick(row, pxPerSec);
  return row;
}

/** A2 — the music bed's lane, drawn from `state.music` and nothing else.
 *
 * `state.music` is the resolved bed (`ops._music_plan`), the derivation
 * `export` builds its lane from — so the block is what the render mixes:
 * it spans the bed's *audible* stretch, which ends where the asset runs out
 * (`padded_frames`), not where the manifest's span does, and the fade ramps
 * are the writer's own frame counts scaled to seconds. The rest of the lane
 * is the silence the writer pads with — drawn as lane background, because
 * that is what silence is. On `music_error` this draws the message across
 * the lane instead (`shots_error`'s policy): a bed a cut orphaned, or fades
 * a cut shrank the bed under, is a thing worth walking into this window to
 * find.
 */
function buildMusicRow(state, pxPerSec, duration) {
  const row = el("div", "lane lane-a2");
  row.style.width = `${Math.max(1, duration * pxPerSec)}px`;

  if (state.music_error) {
    const refusal = el("div", "lane-refusal", `music refused — ${state.music_error}`);
    // A refused bed still has a manifest entry, and this is the only thing
    // drawn for it — so the refusal is what opens the panel, or the one
    // state that most needs fixing is the one state with no way in. The
    // panel offers "Remove bed" off `music_error` for exactly this.
    refusal.addEventListener("click", (event) => {
      const lanes = $("track-lanes");
      if (!lanes) return;
      const rect = lanes.getBoundingClientRect();
      openMusicPanel(
        null,
        null,
        Math.max(0, event.clientX - rect.left + lanes.scrollLeft),
        Math.max(0, event.clientY - rect.top + 10),
      );
    });
    row.append(refusal);
    seekOnClick(row, pxPerSec);
    return row;
  }

  const music = state.music;
  const span = music.timeline_end - music.timeline_start;
  const spanFrames = music.music_frames + music.padded_frames;
  // Frames → seconds through the span's own ratio rather than a rate the
  // view does not send: the two counts were built on one grid, so the
  // fraction is exact and no second clock enters this file.
  const secondsPerFrame = spanFrames ? span / spanFrames : 0;
  // One block per piece the writer plays (docs/plans/NATIVE.md § A1): a bed
  // of passages or a rotation is several assets, and a single block labelled
  // with the first would draw a bed the render does not mix. A view from
  // before pieces existed has none, and draws the one audible span it meant.
  const pieces = Array.isArray(music.pieces) && music.pieces.length
    ? music.pieces
    : [{
        asset: music.asset,
        lane: 0,
        timeline_start: music.timeline_start,
        timeline_end: music.timeline_start + music.music_frames * secondsPerFrame,
        fade_in: music.fade_in_frames * secondsPerFrame,
        fade_out: music.fade_out_frames * secondsPerFrame,
      }];
  const overlapping = pieces.some((piece) => piece.lane === 1);

  for (const piece of pieces) {
    const width = Math.max(0, piece.timeline_end - piece.timeline_start);
    const block = el("div", "clip-block music-block");
    // Two lanes share one row only where a crossfade overlaps them: the
    // second takes the lower half, so an overlap reads as two things at once.
    if (overlapping) block.classList.add(piece.lane === 1 ? "music-block-lower" : "music-block-upper");
    block.style.left = `${(piece.timeline_start * pxPerSec).toFixed(1)}px`;
    block.style.width = `${Math.max(1, width * pxPerSec).toFixed(1)}px`;
    const fades = [];
    if (piece.fade_in) fades.push(`fade in ${fmt(piece.fade_in)}`);
    if (piece.fade_out) fades.push(`fade out ${fmt(piece.fade_out)}`);
    block.title = [
      `music: ${piece.asset} — mixed under the edit`,
      `plays ${fmt(piece.timeline_start)}–${fmt(piece.timeline_end)}`
        + (piece.src_in ? `, from ${fmt(piece.src_in)} into the asset` : ""),
      music.under != null ? `levelled ${music.under} LU under the VO` : "at the asset's own level",
      music.to_end
        ? "no end word — the bed runs to the end of the timeline"
        : `cue: ${music.clip_id} words ${music.word_index_start}–${music.word_index_end}`,
      ...fades,
    ].join("\n");
    if (piece.fade_in) {
      const ramp = el("div", "fade-ramp fade-ramp-in");
      ramp.style.width = `${(piece.fade_in * pxPerSec).toFixed(1)}px`;
      block.append(ramp);
    }
    if (piece.fade_out) {
      const ramp = el("div", "fade-ramp fade-ramp-out");
      ramp.style.width = `${(piece.fade_out * pxPerSec).toFixed(1)}px`;
      block.append(ramp);
    }
    block.append(el("span", "clip-label", piece.asset));
    // Target-phase, like a shot block: clicking the bed inspects its start
    // word, so the cue that placed it is one click from the thing it placed —
    // and opens the bed's panel on the asset and the fades, the edits that do
    // not want a new span. A drag across the lane is the one that re-spans it.
    block.addEventListener("click", (event) => {
      if (ctx) ctx.emit("inspect-word", { clipId: music.clip_id, wordIndex: music.word_index_start });
      const lanes = $("track-lanes");
      if (!lanes) return;
      const rect = lanes.getBoundingClientRect();
      openMusicPanel(
        null,
        null,
        Math.max(0, event.clientX - rect.left + lanes.scrollLeft),
        Math.max(0, event.clientY - rect.top + 10),
      );
    });
    row.append(block);
  }

  seekOnClick(row, pxPerSec);
  return row;
}

/** OV — the overlays, drawn from `state.overlays` and nothing else.
 *
 * `state.overlays` is `ops._overlay_plan` — what `export` draws over the
 * film, bottom of the stack first, each on the writer lane it will occupy.
 * The row is split into one band per writer lane, lowest lane at the
 * bottom, so a scrim and the type over it read as two things at once. The
 * ramps are the entrance and exit lengths. On `overlays_error` the message
 * is drawn across the lane instead, `buildMusicRow`'s policy: a stack a cut
 * orphaned is the thing worth walking into this window to find.
 */
function buildOverlayRow(state, pxPerSec, duration) {
  const row = el("div", "lane lane-ov");
  row.style.width = `${Math.max(1, duration * pxPerSec)}px`;
  if (state.overlays_error) {
    row.append(el("div", "lane-refusal", `overlays refused — ${state.overlays_error}`));
    seekOnClick(row, pxPerSec);
    return row;
  }
  const overlays = state.overlays || [];
  const bands = Math.max(1, ...overlays.map((o) => (Number.isFinite(o.lane) ? o.lane + 1 : 1)));
  for (const overlay of overlays) {
    const span = Math.max(0, overlay.timeline_end - overlay.timeline_start);
    const block = el("div", "clip-block overlay-block");
    block.style.left = `${(overlay.timeline_start * pxPerSec).toFixed(1)}px`;
    block.style.width = `${Math.max(1, span * pxPerSec).toFixed(1)}px`;
    if (bands > 1) {
      const lane = overlay.lane || 0;
      block.style.top = `calc(${(((bands - 1 - lane) / bands) * 100).toFixed(2)}% + 2px)`;
      block.style.bottom = `calc(${((lane / bands) * 100).toFixed(2)}% + 2px)`;
    }
    const enter = overlay.enter === "none" ? 0 : overlay.enter_seconds;
    const leave = overlay.leave === "none" ? 0 : overlay.leave_seconds;
    block.title = [
      `overlay ${overlay.position}: ${overlay.card} — drawn over the film`,
      `plays ${fmt(overlay.timeline_start)}–${fmt(overlay.timeline_end)}`,
      enter ? `enters: ${overlay.enter}, ${fmt(enter)}, ${overlay.enter_ease}` : "enters: cut",
      leave ? `leaves: ${overlay.leave}, ${fmt(leave)}, ${overlay.leave_ease}` : "leaves: cut",
    ].join("\n");
    if (enter) {
      const ramp = el("div", "fade-ramp fade-ramp-in");
      ramp.style.width = `${(Math.min(enter, span) * pxPerSec).toFixed(1)}px`;
      block.append(ramp);
    }
    if (leave) {
      const ramp = el("div", "fade-ramp fade-ramp-out");
      ramp.style.width = `${(Math.min(leave, span) * pxPerSec).toFixed(1)}px`;
      block.append(ramp);
    }
    block.append(el("span", "clip-label", overlay.card));
    row.append(block);
  }
  seekOnClick(row, pxPerSec);
  return row;
}

/** SFX — the one-shot sounds, one tick per hit, from `state.sounds` alone.
 *
 * `state.sounds` is `ops._sound_plan` flattened: every hit `export` places,
 * in Edit seconds, after the cuts skipped and the thinning dropped theirs.
 * A tick is a mark, not a block — a keystroke is 50 ms, and a run of them is
 * the rhythm this lane exists to show. Nothing here plays: the preview does
 * not play the bed either. On `sounds_error` the message is drawn instead,
 * `buildMusicRow`'s policy.
 */
function buildSoundRow(state, pxPerSec, duration) {
  const row = el("div", "lane lane-sfx");
  row.style.width = `${Math.max(1, duration * pxPerSec)}px`;
  if (state.sounds_error) {
    row.append(el("div", "lane-refusal", `sounds refused — ${state.sounds_error}`));
    seekOnClick(row, pxPerSec);
    return row;
  }
  for (const hit of state.sounds || []) {
    if (!Number.isFinite(hit.at)) continue;
    const tick = el("div", "sound-tick");
    tick.style.left = `${(hit.at * pxPerSec).toFixed(1)}px`;
    tick.title = `${hit.asset} at ${fmt(hit.at)} (${hit.address}), ${hit.gain_db} dB — sound ${hit.position}`;
    row.append(tick);
  }
  seekOnClick(row, pxPerSec);
  return row;
}

/** Lazily fetches and caches one clip's waveform (`GET
 * /api/waveform/<clip_id>`, contract in PLAN.md § Read-model additions).
 * Triggers one re-render when it lands so the canvas that asked for it
 * redraws with real data instead of the blank frame it drew while waiting. */
function ensureWaveform(clipId) {
  const cached = waveformCache.get(clipId);
  if (cached) return cached;
  const entry = { status: "loading", data: null };
  waveformCache.set(clipId, entry);
  if (ctx) {
    ctx
      .api(`/api/waveform/${encodeURIComponent(clipId)}`)
      .then((data) => {
        entry.status = "ready";
        entry.data = data;
        if (lastState) render();
      })
      .catch(() => {
        // No waveform for this clip (missing media, no clips at all) — the
        // lane just stays blank there. The clip-blocks underneath still
        // carry hover/title, so the lane is not otherwise broken.
        entry.status = "error";
      });
  }
  return entry;
}

/** Draws A1's envelope THROUGH the edit: each segment reslices its own
 * clip's cached *source* RMS array by its own `start`/`end` and paints
 * that slice at the segment's timeline position. No timeline-length
 * envelope is ever assembled — a cut changes which slice of the cached
 * array a segment reads and where it paints, nothing this function
 * precomputes. */
function drawWaveformLane(canvas, segments, pxPerSec, laneHeight, contentWidth) {
  canvas.width = Math.max(1, Math.round(contentWidth));
  canvas.height = Math.max(1, Math.round(laneHeight));
  const g = canvas.getContext("2d");
  if (!g) return;
  g.clearRect(0, 0, canvas.width, canvas.height);
  const mid = canvas.height / 2;
  g.fillStyle = canvasInk(canvas, "rgba(20, 22, 26, 0.55)"); // read over the block's own pastel

  for (const seg of segments) {
    const entry = ensureWaveform(seg.clip_id);
    if (entry.status !== "ready") continue;
    const { rms, frame_ms } = entry.data;
    const frameSec = frame_ms / 1000;
    const startFrame = Math.max(0, Math.floor(seg.start / frameSec));
    const endFrame = Math.min(rms.length, Math.ceil(seg.end / frameSec));
    const frameCount = Math.max(1, endFrame - startFrame);
    const segLeft = seg.timeline_start * pxPerSec;
    const segWidth = Math.max(1, (seg.timeline_end - seg.timeline_start) * pxPerSec);
    const barWidth = Math.max(1, segWidth / frameCount);
    for (let i = 0; i < frameCount; i++) {
      const value = rms[startFrame + i] ?? 0;
      const h = (value / 255) * (canvas.height - 4);
      g.fillRect(segLeft + (i / frameCount) * segWidth, mid - h / 2, barWidth, h);
    }
  }
}

/** The selected words' span, if there is one. Driven by this file's own
 * drag gesture (below) — a click-and-drag on any lane resolves to a pair
 * of word indices — or by an external `'selection'` event `{indices:
 * [wordIndex, …]}` (mirroring `'playing-word'`'s `{index}`), for any other
 * pane that wants to drive the highlight without owning the box math. Only
 * the extremal two of `indices` matter: `state.words` is filtered to the
 * given set and the box spans their min `timeline_start` to max
 * `timeline_end`, so `[first, last]` alone is enough. */
function drawSelectionHighlight(lanes, indices, pxPerSec, state) {
  if (!state.words || !indices || !indices.length) return;
  const set = new Set(indices);
  const spans = state.words.filter((w) => w.present && set.has(w.index));
  if (!spans.length) return;
  const start = Math.min(...spans.map((w) => w.timeline_start));
  const end = Math.max(...spans.map((w) => w.timeline_end));
  const box = el("div", "drag-box");
  box.style.left = `${(start * pxPerSec).toFixed(1)}px`;
  box.style.width = `${Math.max(1, (end - start) * pxPerSec).toFixed(1)}px`;
  lanes.append(box);
}

/** Timeline second -> nearest word, client-side mirror of `ops._nearest_word`
 * (overlap test first, nearest by edge distance across a gap otherwise) —
 * but over `state.words`' own `timeline_start`/`timeline_end`, since the
 * client already has that array for drawing and a drag needs no server
 * round trip to resolve. Settled by measurement, not guessed at (PLAN.md §
 * Three uncosted parity items: 85-87% of drags land on a word directly; the
 * residual is inter-word silence with a 0.54s median gap, snapped here to
 * whichever word is closer). Present words only — a cut word has no
 * meaningful timeline position to measure from. */
function nearestWordAt(words, t) {
  let nearest = null;
  let nearestDist = Infinity;
  for (const w of words) {
    if (!w.present) continue;
    if (t >= w.timeline_start && t < w.timeline_end) return w;
    const dist = t < w.timeline_start ? w.timeline_start - t : t - w.timeline_end;
    if (dist < nearestDist) {
      nearestDist = dist;
      nearest = w;
    }
  }
  return nearest;
}

function laneTimeFromEvent(event) {
  const lanes = $("track-lanes");
  const rect = lanes.getBoundingClientRect();
  // `getBoundingClientRect` is the container's own on-screen box, which does
  // NOT move when its content scrolls (only `overflow-x: auto`'s content
  // does) — so a click after scrolling the timeline needs `scrollLeft` added
  // back in, or it resolves against whatever was under this same screen x at
  // scrollLeft 0. Confirmed by driving a real drag at a fixed screen point
  // under two scroll positions: unpatched, both resolved the same word
  // regardless of which part of the transcript was actually under the
  // cursor (browser pass, CLAUDE.md's discipline of verifying against the
  // real running service). `seekOnClick` does not have this bug because it
  // reads the *row's* rect, which does move with scroll.
  return Math.max(0, (event.clientX - rect.left + lanes.scrollLeft) / currentPxPerSec);
}

/** A frame, never a bare epsilon (`reframe_coverage`'s own lesson, CLAUDE.md)
 * — but a frame in *seconds* is meaningless once zoomed out far enough that
 * one frame is sub-pixel, so this is the LARGER of a frame and 6 screen
 * pixels converted to seconds at the current zoom. `shots_rate` (export's
 * own frame grid) is preferred over `timebase` because it is the grid a real
 * cut boundary actually lands on; either is a better guess than a bare 30. */
function snapTolerance() {
  const frameSec = 1 / ((lastState && (lastState.shots_rate || lastState.timebase)) || 30);
  const pxTolerance = 6 / currentPxPerSec;
  return Math.max(frameSec, pxTolerance);
}

/** Snaps a drag's timeline-second position onto the nearest word boundary,
 * cue edge, or the playhead — a click-precision aid, never a source of
 * truth: the snapped value still becomes an ordinary render-time span
 * through `laneTimeFromEvent`'s own coordinate space, resolved to source
 * time server-side same as any other drag (`cut_by_time`'s own docstring).
 * Targets are read straight off data already in memory (`lastState.words`/
 * `.shots`, `ctx.player.now()`) — no fetch, and no epsilon: `snapTolerance()`
 * above. A no-op (returns `t` unchanged) when `#snap-toggle` is off or there
 * is no state loaded yet to snap against. */
function snap(t) {
  if (!snapEnabled || !lastState) return t;
  const tolerance = snapTolerance();
  let best = t;
  let bestDist = tolerance;
  const consider = (candidate) => {
    if (candidate === null || candidate === undefined) return;
    const dist = Math.abs(candidate - t);
    if (dist <= bestDist) {
      bestDist = dist;
      best = candidate;
    }
  };
  for (const w of lastState.words || []) {
    if (!w.present) continue;
    consider(w.timeline_start);
    consider(w.timeline_end);
  }
  for (const shot of lastState.shots || []) {
    consider(shot.start);
    consider(shot.start + shot.duration);
  }
  if (ctx) consider(ctx.player.now());
  return best;
}

/** Word-range echo, three either side (CLAUDE.md) — the placement word
 * bracketed so it reads correctly even if it lands one off from what the
 * drag looked like it meant. */
function cueEcho(words, wordIndex) {
  return words
    .filter((w) => w.index >= wordIndex - 3 && w.index <= wordIndex + 3)
    .map((w) => (w.index === wordIndex ? `[${w.text}]` : w.text))
    .join(" ");
}

/** Clamps a floating toolbar's raw drop point to the lanes pane's own
 * *visible* box — never trusted as-is (browser pass: an unclamped drop on
 * the bottom-most lane rendered the whole toolbar past both `#track-lanes`'s
 * own `overflow-y: hidden` clip and the browser viewport, at zero opacity of
 * "found" — no error, just nothing a person could see or click). Unhide
 * before measuring: a `hidden` element reports a zero-size rect, which would
 * clamp everything to (0, 0). `boxLeft` is in the same content-relative
 * coordinate space `laneTimeFromEvent` resolves a click into (scrollLeft
 * already folded in at drag time), so the clamp's own bounds are the visible
 * window converted into that same space — `[lanes.scrollLeft, scrollLeft +
 * clientWidth]` — not `[0, clientWidth]`.
 *
 * The clamp MATH now lives once, in `dom.js`'s `clampFloating` — shared with
 * transcript.js's own selection toolbar, which had this exact bug fixed here
 * first and then carried it separately (a duplicated fix is how F4 reached
 * only one of the two toolbars the first time). This file still supplies its
 * OWN bounds rather than a container element: transcript.js's toolbar lives
 * in unscrolled space (`[0, container.clientWidth]`) while this one's
 * `boxLeft`/`boxTop` already have `lanes.scrollLeft` folded in, so its bounds
 * are the visible window in that same scrolled space — a helper that derived
 * bounds from `clientWidth` alone would be correct for one caller and
 * silently wrong for the other. */
function refreshCueToolbar() {
  if (!cueToolbarEl) return;
  if (!cueSelection || !lastState || !lastState.words) {
    cueToolbarEl.hidden = true;
    return;
  }
  cueInfoEl.textContent = cueEcho(lastState.words, cueSelection.wordIndex);
  cueToolbarEl.hidden = false;

  const lanes = $("track-lanes");
  if (lanes) {
    const { left, top } = clampFloating(
      cueSelection.boxLeft,
      cueSelection.boxTop,
      cueToolbarEl.offsetWidth,
      cueToolbarEl.offsetHeight,
      lanes.scrollLeft,
      lanes.scrollLeft + lanes.clientWidth,
      0,
      lanes.clientHeight,
    );
    cueToolbarEl.style.left = `${left.toFixed(1)}px`;
    cueToolbarEl.style.top = `${top.toFixed(1)}px`;
  } else {
    cueToolbarEl.style.left = `${cueSelection.boxLeft.toFixed(1)}px`;
    cueToolbarEl.style.top = `${cueSelection.boxTop.toFixed(1)}px`;
  }
}

/** Draws the live trim-drag overlay: for an inward drag, a band over the
 * material that would be REMOVED; for an outward (restore) drag, a band over
 * the gap that would come BACK. Same standalone-node lifecycle as
 * `.drag-box` (`drawSelectionHighlight`) — appended straight to `lanes`,
 * spanning the full lane stack the way `.drag-box` already does (top:0;
 * bottom:0 on a node that is a *direct* child of `#track-lanes`, not of any
 * one `.lane` row) — never the `.trim-handle`/`.clip-block` the drag is
 * anchored to. */
function drawTrimPreview(lanes, g, pxPerSec) {
  const origEdge = g.edge === "in" ? g.origStart : g.origEnd;
  const start = Math.min(origEdge, g.proposedTime);
  const end = Math.max(origEdge, g.proposedTime);
  if (!(end > start)) return;
  const cls = g.direction === "restore" ? "mode-restore" : "mode-cut";
  const box = el("div", `trim-preview ${cls}`);
  box.style.left = `${(start * pxPerSec).toFixed(1)}px`;
  box.style.width = `${Math.max(1, (end - start) * pxPerSec).toFixed(1)}px`;
  lanes.append(box);
}

/** The razor tool's live select-a-range band — same lifecycle as
 * `.drag-box`/`.trim-preview` above: one standalone node, direct child of
 * `lanes`, torn down and redrawn whole by `updateGestureOverlay`. */
function drawRangeBand(lanes, g, pxPerSec) {
  const start = Math.min(g.startTime, g.currentTime);
  const end = Math.max(g.startTime, g.currentTime);
  const box = el("div", "range-band");
  box.style.left = `${(start * pxPerSec).toFixed(1)}px`;
  box.style.width = `${Math.max(1, (end - start) * pxPerSec).toFixed(1)}px`;
  lanes.append(box);
}

/** Redraws only the live gesture's own overlay node, leaving every
 * lane/row/block/handle untouched.
 *
 * `render()` is not safe to call from `handleLanesMouseDown`/`Move`, or from
 * `handleLanesMouseUp`'s non-drag branches — it does `lanes.textContent = ""`
 * then rebuilds every lane wholesale, which removes whatever node the
 * in-progress gesture is anchored to. A `setTimeout(render, 0)` used to sit
 * in those spots instead of a synchronous call, and it is not a fix, only a
 * race it usually wins: CDP's back-to-back mousePressed/mouseReleased has no
 * gap for the timer to land in before mouseup, so it read as correct against
 * a scripted test. Driven with a realistic human dwell between press and
 * release (measured 10ms-250ms; a real click dwells roughly 60-150ms), the
 * timer fires *during* the dwell, the mousedown target is gone by the time
 * mouseup arrives, and Chrome suppresses the trailing native 'click' exactly
 * as it did before the timer existed — click-to-seek stayed broken for every
 * real click on a transcript lane, just no longer for a script-driven one.
 * Measured with a dwell-time probe: seeks at 0ms and 5ms dwell, silently
 * fails at 10ms and every dwell above it. The full table, and why this is
 * the most transferable finding of that session, is HISTORY.md § The
 * dwell-timing lesson.
 *
 * A cue drag's `.drag-box`, a trim drag's `.trim-preview`, and a razor drag's
 * `.range-band` are the ONLY three things this function ever draws or
 * removes, and it removes all three every call before redrawing the one
 * `gesture.kind` in progress (or none, if the gesture just ended and neither
 * `selection` nor `gesture` still wants one drawn) — never a row/block/
 * handle, and never `render()`. */
function updateGestureOverlay() {
  const lanes = $("track-lanes");
  if (!lanes || !lastState) return;
  for (const n of lanes.querySelectorAll(".drag-box, .trim-preview, .range-band")) n.remove();
  if (gesture && gesture.kind === "trim") {
    drawTrimPreview(lanes, gesture, currentPxPerSec);
  } else if (gesture && gesture.kind === "razor") {
    drawRangeBand(lanes, gesture, currentPxPerSec);
  } else if (selection) {
    drawSelectionHighlight(lanes, selection, currentPxPerSec, lastState);
  }
}

function cancelCueSelection() {
  gesture = null;
  cueSelection = null;
  selection = null;
  refreshCueToolbar();
  render();
}

/** The existing cue-placement flow, generalized so both the razor tool's
 * "place b-roll over it" verb and an asset drag-drop (item G) can open it —
 * a thin wrapper around the `cueSelection`/`refreshCueToolbar`/
 * `cueAssetInputEl` machinery that already exists for the plain drag-a-word
 * gesture, so neither caller has to know its internals. `prefillAsset` is
 * the FOOTAGE a drag named (`clip.clip_id`/`card.name`), never the
 * addressing clip — `placeCue` below still reads `lastState.clip_id` for
 * that half, unchanged. */
function openCuePlacement(wordIndex, boxLeft, boxTop, prefillAsset) {
  cueSelection = { wordIndex, boxLeft, boxTop };
  refreshCueToolbar();
  if (prefillAsset && cueAssetInputEl) cueAssetInputEl.value = prefillAsset;
}

/** `POST /api/cue` — the fourth caller into `ops.cue_add`, alongside the
 * CLI and MCP tool (CLAUDE.md: every mutation posts to the same `ops`
 * function). The result is never rendered here, same rule as Cut/Restore
 * in transcript.js — it goes on the shared bus and agent.js draws it into
 * the feed; 'project-changed' brings the new shot through the normal
 * update() path. */
async function placeCue(assetInput) {
  if (!cueSelection || !ctx || !lastState) return;
  const asset = assetInput.value.trim();
  if (!asset) {
    assetInput.focus();
    ctx.emit("toast", "Type an asset — a clip_id or card:name — before placing the cue.");
    return;
  }
  let payload = null;
  let error = null;
  try {
    payload = await ctx.api("/api/cue", {
      clip_id: lastState.clip_id,
      word_index: cueSelection.wordIndex,
      asset,
    });
  } catch (err) {
    error = err.message;
    ctx.emit("toast", error);
  }
  ctx.emit("op-result", { payload, error });
  if (!error) {
    assetInput.value = "";
    cancelCueSelection();
  }
}

function buildCueToolbar() {
  const bar = el("div", "selection-toolbar cue-toolbar");
  bar.hidden = true;

  const info = el("div", "quote");
  const assetInput = document.createElement("input");
  assetInput.type = "text";
  assetInput.placeholder = "asset — clip_id or card:name";
  assetInput.style.width = "18em";
  cueAssetInputEl = assetInput; // module-level, so openCuePlacement can prefill it
  const placeBtn = el("button", null, "Place cue");
  // The FIRST bed has no A2 lane to drag on — "no bed, no lane" is the
  // lane's own rule — so the range gesture that places a cue is also how a
  // bed gets created, one verb over. Once there is a bed, dragging A2
  // itself is the shorter road to the same panel.
  const musicBtn = el("button", null, "Music bed");
  const cancelBtn = el("button", null, "Cancel");

  placeBtn.addEventListener("click", () => placeCue(assetInput));
  musicBtn.addEventListener("click", () => {
    if (!cueSelection) return;
    const { wordIndexStart, wordIndexEnd, boxLeft, boxTop } = cueSelection;
    const typed = assetInput.value.trim();
    openMusicPanel(
      wordIndexStart === undefined ? cueSelection.wordIndex : wordIndexStart,
      wordIndexEnd === undefined ? null : wordIndexEnd,
      boxLeft,
      boxTop,
    );
    // Whatever was typed for the cue is the likelier answer here than the
    // bed's own asset, and openMusicPanel has just filled the field from the
    // bed — so this override runs after it, never instead of it.
    if (typed && musicAssetInputEl) musicAssetInputEl.value = typed;
  });
  assetInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") placeCue(assetInput);
    if (event.key === "Escape") cancelCueSelection();
  });
  cancelBtn.addEventListener("click", cancelCueSelection);

  bar.append(info, assetInput, placeBtn, musicBtn, cancelBtn);
  cueToolbarEl = bar;
  cueInfoEl = info;
}

/** Mirrors `buildCueToolbar` exactly — same `.selection-toolbar` base class
 * (so the existing `.selection-toolbar[hidden]{display:none}` companion rule
 * already covers it, no new `[hidden]` rule needed), same persistent-node/
 * re-appended-every-render treatment, same `clampFloating` call. Content
 * varies by `planSelection.kind`, filled in by `refreshPlanToolbar()`. */
function buildPlanToolbar() {
  const bar = el("div", "selection-toolbar plan-toolbar");
  bar.hidden = true;
  const info = el("div", "quote");
  const warn = el("div", "warn bad"); // suspect-boundary text, hidden when none
  warn.hidden = true;
  const actions = el("div", "plan-actions");
  bar.append(info, warn, actions);
  planToolbarEl = bar;
  planInfoEl = info;
  planWarnEl = warn;
  planActionsEl = actions;
}

/** The A2 bed's panel — `POST /api/music`, the fourth caller into
 * `ops.music` alongside the CLI and MCP tool.
 *
 * Built on `.selection-toolbar .plan-toolbar` rather than beside them: the
 * column layout, the `[hidden]` companion rule and — the one that matters —
 * the `--plan-max-h` height cap are all already there, and a second copy of
 * that cap is exactly how the clamp fix reached one toolbar and not the
 * other the first time (dom.js's `clampFloating` header). `.music-toolbar`
 * adds nothing but the field row.
 *
 * The inputs persist across renders (the node is re-appended, never rebuilt,
 * so a half-typed asset survives a `project-changed`); the ACTIONS row is
 * rebuilt on every refresh, because "Remove bed" is a verb only when there
 * is a bed to remove. */
function buildMusicToolbar() {
  const bar = el("div", "selection-toolbar plan-toolbar music-toolbar");
  bar.hidden = true;

  const info = el("div", "quote");
  const fields = el("div", "music-fields");

  const asset = document.createElement("input");
  asset.type = "text";
  asset.placeholder = "music asset — a registered clip_id";
  asset.style.width = "16em";

  const fadeIn = document.createElement("input");
  fadeIn.type = "number";
  fadeIn.min = "0";
  fadeIn.step = "0.1";
  const fadeInLabel = el("label", null);
  fadeInLabel.append(document.createTextNode("fade in "), fadeIn, document.createTextNode("s"));

  const fadeOut = document.createElement("input");
  fadeOut.type = "number";
  fadeOut.min = "0";
  fadeOut.step = "0.1";
  const fadeOutLabel = el("label", null);
  fadeOutLabel.append(document.createTextNode("fade out "), fadeOut, document.createTextNode("s"));

  const toEnd = document.createElement("input");
  toEnd.type = "checkbox";
  const toEndLabel = el("label", null);
  toEndLabel.append(toEnd, document.createTextNode(" to the end of the film"));

  fields.append(asset, fadeInLabel, fadeOutLabel, toEndLabel);
  const actions = el("div", "plan-actions");
  bar.append(info, fields, actions);

  asset.addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyMusic();
    if (event.key === "Escape") cancelMusicSelection();
  });

  musicToolbarEl = bar;
  musicInfoEl = info;
  musicAssetInputEl = asset;
  musicFadeInEl = fadeIn;
  musicFadeOutEl = fadeOut;
  musicToEndEl = toEnd;
  musicActionsEl = actions;
}

/** Opens the bed panel. `wordIndexStart`/`wordIndexEnd` null means "not
 * re-spanning" — the click-the-bed open, which edits the asset and the
 * fades and leaves the cue's words alone.
 *
 * Every field is filled from the bed in force (`lastState.music`, the
 * resolved projection `export` builds its lane from) rather than from
 * whatever was typed last, so the panel always opens saying what the render
 * currently mixes. */
function openMusicPanel(wordIndexStart, wordIndexEnd, boxLeft, boxTop) {
  cueSelection = null;
  refreshCueToolbar();
  planSelection = null;
  refreshPlanToolbar();

  musicSelection = { wordIndexStart, wordIndexEnd, boxLeft, boxTop, stored: null };
  const bed = bedInForce();
  if (musicAssetInputEl) musicAssetInputEl.value = bed ? bed.asset : "";
  if (musicFadeInEl) musicFadeInEl.value = bed && bed.fade_in ? String(bed.fade_in) : "";
  if (musicFadeOutEl) musicFadeOutEl.value = bed && bed.fade_out ? String(bed.fade_out) : "";
  if (musicToEndEl) {
    // A drag that named an end word means that end word; anything else keeps
    // whatever the bed already says, and a first bed with no drag runs to the
    // end (the single-pass hold the music listen settled on, `ops.music`'s
    // own default).
    musicToEndEl.checked = wordIndexEnd === null ? (bed ? !!bed.to_end : true) : false;
  }
  refreshMusicToolbar();

  // A REFUSED bed is still a bed, and it is the state most likely to be
  // opened here — but `timeline_view` sends `music_error` *instead of* the
  // projection, so there is nothing in `state` to fill the fields from and
  // the panel said "no bed yet" over a bed that exists (browser pass). The
  // stored cue comes back from the op's own read shape — no arguments
  // changes nothing and reports what is in force — rather than from a new
  // view field, and it is what makes the refusal's named fix ("shorten the
  // fades") doable from the panel the refusal opens.
  if (!bed && lastState && lastState.music_error && ctx) {
    const opened = musicSelection;
    ctx
      .api("/api/music", {})
      .then((payload) => {
        if (musicSelection !== opened || !payload || !payload.music) return;
        opened.stored = payload.music;
        if (musicAssetInputEl) musicAssetInputEl.value = payload.music.asset || "";
        if (musicFadeInEl) musicFadeInEl.value = payload.music.fade_in ? String(payload.music.fade_in) : "";
        if (musicFadeOutEl) musicFadeOutEl.value = payload.music.fade_out ? String(payload.music.fade_out) : "";
        if (musicToEndEl && wordIndexEnd === null) {
          musicToEndEl.checked = payload.music.word_index_end === null;
        }
        refreshMusicToolbar();
      })
      .catch(() => {
        // The read failed, so the panel stays on what it could draw — the
        // Remove verb below is offered off `music_error` either way, which
        // is the one action that always works on a bed that cannot resolve.
      });
  }
}

/** The bed the panel is editing: the resolved projection when there is one,
 * the stored cue read back when the projection refused. Never a third
 * shape — both carry `asset`/`fade_in`/`fade_out`, and only `to_end` needs
 * the stored form's `word_index_end === null` reading. */
function bedInForce() {
  if (lastState && lastState.music) return lastState.music;
  if (musicSelection && musicSelection.stored) {
    const stored = musicSelection.stored;
    return { ...stored, to_end: stored.word_index_end === null, refused: true };
  }
  return null;
}

function cancelMusicSelection() {
  gesture = null;
  musicSelection = null;
  selection = null;
  refreshMusicToolbar();
  render();
}

/** Blank means "leave this field alone" — `ops.music` treats an absent key
 * as unchanged, and sending 0 for an empty box would silently wipe a fade
 * the panel was never asked to touch. */
function fadeValue(input) {
  const raw = (input.value || "").trim();
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** The word echo, three either side and the boundary word bracketed — the
 * same `cueEcho` the cue toolbar draws, because it is the same question
 * (CLAUDE.md: an index one past the intended phrase reads correctly on its
 * own, so the neighbours are the point). Falls back to the bed's own words
 * when this open is not re-spanning it. */
function musicEcho() {
  const words = (lastState && lastState.words) || null;
  const bed = bedInForce();
  const start = musicSelection.wordIndexStart !== null
    ? musicSelection.wordIndexStart
    : bed
      ? bed.word_index_start
      : null;
  const end = musicSelection.wordIndexEnd !== null
    ? musicSelection.wordIndexEnd
    : musicSelection.wordIndexStart !== null
      ? null
      : bed && !bed.to_end
        ? bed.word_index_end
        : null;
  if (start === null) {
    if (!bed) return "no bed yet — drag a range to place one";
    // Say that the bed is refused rather than drawing its cue as though it
    // played: the lane behind this panel is showing the refusal, and a
    // panel that reads like an ordinary bed over it is the surface
    // disagreeing with itself.
    return bed.refused ? `bed on ${bed.asset} — refused, see the lane` : `bed on ${bed.asset}`;
  }
  // A refused bed says so wherever it is drawn — the lane behind this panel
  // is showing the refusal, and a panel reading like an ordinary bed over it
  // is the surface disagreeing with itself.
  const mark = bed && bed.refused ? "refused — " : "";
  if (!words) return `${mark}bed from word ${start}${end === null ? "" : ` to ${end}`}`;
  const head = `${mark}from ${cueEcho(words, start)}`;
  return end === null ? head : `${head}\nto ${cueEcho(words, end)}`;
}

/** Clamped by the one `clampFloating`, with `--plan-max-h` set before the
 * measure — `refreshPlanToolbar`'s own two rules, for the reason its comment
 * gives: the clamp moves a box and cannot shrink one. */
function refreshMusicToolbar() {
  if (!musicToolbarEl) return;
  if (!musicSelection || !lastState) {
    musicToolbarEl.hidden = true;
    return;
  }
  musicToolbarEl.hidden = false;
  musicInfoEl.textContent = musicEcho();
  musicActionsEl.textContent = "";

  const bed = bedInForce();
  const applyBtn = el("button", "primary", bed ? "Update bed" : "Set bed");
  applyBtn.addEventListener("click", () => applyMusic());
  musicActionsEl.append(applyBtn);
  if (bed || lastState.music_error) {
    // `music_error` and no `music`: the bed is real, it just cannot resolve
    // (a cut orphaned its word, or fades outgrew it). Removing it is exactly
    // the verb that case wants, so the button is offered off either.
    const removeBtn = el("button", null, "Remove bed");
    removeBtn.addEventListener("click", () => applyMusic({ reset: true }));
    musicActionsEl.append(removeBtn);
  }
  const cancelBtn = el("button", null, "Cancel");
  cancelBtn.addEventListener("click", cancelMusicSelection);
  musicActionsEl.append(cancelBtn);

  const lanes = $("track-lanes");
  if (lanes) {
    musicToolbarEl.style.setProperty("--plan-max-h", `${lanes.clientHeight}px`);
    const { left, top } = clampFloating(
      musicSelection.boxLeft,
      musicSelection.boxTop,
      musicToolbarEl.offsetWidth,
      musicToolbarEl.offsetHeight,
      lanes.scrollLeft,
      lanes.scrollLeft + lanes.clientWidth,
      0,
      lanes.clientHeight,
    );
    musicToolbarEl.style.left = `${left.toFixed(1)}px`;
    musicToolbarEl.style.top = `${top.toFixed(1)}px`;
  } else {
    musicToolbarEl.style.left = `${musicSelection.boxLeft.toFixed(1)}px`;
    musicToolbarEl.style.top = `${musicSelection.boxTop.toFixed(1)}px`;
  }
}

/** `POST /api/music`. The result is never rendered here — same rule as
 * Cut/Restore and Place cue: it goes on the shared bus for agent.js to draw
 * into the feed, and 'project-changed' brings the new lane through the
 * normal update() path. A bed that no longer *resolves* comes back as the
 * lane's own `music_error`, which is where that belongs. */
async function applyMusic({ reset = false } = {}) {
  if (!musicSelection || !ctx || !lastState) return;

  let body;
  if (reset) {
    body = { reset: true };
  } else {
    const asset = musicAssetInputEl.value.trim();
    if (!asset) {
      musicAssetInputEl.focus();
      ctx.emit("toast", "Type a music asset — a registered clip_id — before setting the bed.");
      return;
    }
    body = { asset };
    // `clip_id` rides the word index and never travels alone: it is the
    // transcript the index is an index INTO, so sending the view's current
    // clip on a fades-only change would silently re-address a bed placed on
    // another clip — correct in every call that happens to be a first set,
    // which is exactly how that class of bug survives. Absent, `ops.music`
    // keeps the one it stored.
    if (musicSelection.wordIndexStart !== null) {
      body.clip_id = lastState.clip_id;
      body.word_index_start = musicSelection.wordIndexStart;
    }
    if (musicToEndEl.checked) body.clear_end = true;
    else if (musicSelection.wordIndexEnd !== null) body.word_index_end = musicSelection.wordIndexEnd;
    const fadeIn = fadeValue(musicFadeInEl);
    const fadeOut = fadeValue(musicFadeOutEl);
    if (fadeIn !== null) body.fade_in = fadeIn;
    if (fadeOut !== null) body.fade_out = fadeOut;
  }

  let payload = null;
  let error = null;
  try {
    payload = await ctx.api("/api/music", body);
  } catch (err) {
    error = err.message;
    ctx.emit("toast", error);
  }
  ctx.emit("op-result", { payload, error });
  if (!error) cancelMusicSelection();
}

/** Plain context words, space-joined — the unbracketed half of an echo. */
function wordsPlain(list) {
  return (list || []).map((w) => w.text).join(" ");
}

/** `cut_by_time`'s plan response, read into the same bracketed-context
 * convention `cueEcho` already draws — one `applied[]` entry per requested
 * span (this popover only ever requests one), one `pieces[]` entry per
 * clip/seam the span crossed. A piece with no transcript still gets a
 * legible line (`transcript_missing`), never a blank one. */
function cutPlanEcho(result) {
  const applied = (result && result.applied) || [];
  if (!applied.length) return "(nothing here)";
  return applied
    .flatMap((a) =>
      (a.pieces || []).map((p) => {
        if (p.transcript_missing) return `${p.clip_id}: no transcript to echo`;
        const before = wordsPlain(p.context_before);
        const hit = (p.words_overlapped || []).map((w) => `[${w.text}]`).join(" ") || "(silence)";
        const after = wordsPlain(p.context_after);
        return [before, hit, after].filter(Boolean).join(" ");
      }),
    )
    .join(" / ");
}

/** `restore`'s plan response, same `_echo` convention `cut_by_transcript`
 * uses (CLAUDE.md: one word-echo convention, reused everywhere a word range
 * resolves). `already_present` is reported, not an error — this popover
 * still shows it rather than treating it as a fetch failure. */
function restorePlanEcho(result) {
  const applied = (result && result.applied) || [];
  if (!applied.length) return "(nothing here)";
  const a = applied[0];
  if (a.already_present) return "Nothing cut here — already present.";
  const n = a.last_word - a.first_word + 1;
  const before = wordsPlain(a.context_before);
  const after = wordsPlain(a.context_after);
  const echo = [before, `[${a.text}]`, after].filter(Boolean).join(" ");
  const seconds = (a.restored_seconds ?? 0).toFixed(2);
  return `Restore ${n} word(s) cut here: ${echo} (${seconds}s)`;
}

/** `POST /api/cut-at` with `plan: true` — fires once, on trim-inward
 * mouseup or the razor popover's "Cut this range" verb. Guards against a
 * stale response landing after the user cancelled or moved on: only writes
 * back into `planSelection` if it is STILL the same "cut" selection this
 * call was made for (identity-checked by object reference, since Cancel/a
 * new gesture always replaces the object rather than mutating it). */
async function requestCutPlan(span) {
  if (!ctx || !planSelection) return;
  const forSelection = planSelection;
  refreshPlanToolbar(); // shows the "…" loading state immediately
  let payload = null;
  let error = null;
  try {
    payload = await ctx.api("/api/cut-at", { spans: [span], plan: true });
  } catch (err) {
    error = err.message;
  }
  if (planSelection !== forSelection) return; // superseded — cancelled or replaced
  planSelection.result = payload;
  planSelection.error = error;
  refreshPlanToolbar();
}

/** `POST /api/restore` with `plan: true` — same stale-response guard as
 * `requestCutPlan`. */
async function requestRestorePlan(ranges) {
  if (!ctx || !planSelection) return;
  const forSelection = planSelection;
  refreshPlanToolbar();
  let payload = null;
  let error = null;
  try {
    payload = await ctx.api("/api/restore", { clip_id: planSelection.clipId, ranges, plan: true });
  } catch (err) {
    error = err.message;
  }
  if (planSelection !== forSelection) return;
  planSelection.result = payload;
  planSelection.error = error;
  refreshPlanToolbar();
}

/** Apply — the real, non-plan write. Mirrors `placeCue`'s pattern: the
 * result is never rendered here, only emitted on the shared bus (`op-result`,
 * for agent.js's feed) and `project-changed` (from the SSE loop, once the
 * write lands) brings the new state through the normal `update()` path. */
async function applyCutPlan() {
  if (!planSelection || planSelection.kind !== "cut" || !ctx) return;
  const confirmNeeded = Boolean(planSelection.result && planSelection.result.suspect_boundaries?.length);
  if (confirmNeeded && !(planConfirmCheckbox && planConfirmCheckbox.checked)) return;
  let payload = null;
  let error = null;
  try {
    payload = await ctx.api("/api/cut-at", {
      spans: [planSelection.span],
      plan: false,
      confirm_suspect: confirmNeeded,
    });
  } catch (err) {
    error = err.message;
    ctx.emit("toast", error);
  }
  ctx.emit("op-result", { payload, error });
  if (!error) {
    planSelection = null;
    gesture = null;
    refreshPlanToolbar();
    updateGestureOverlay();
  }
}

async function applyRestorePlan() {
  if (!planSelection || planSelection.kind !== "restore" || !ctx) return;
  let payload = null;
  let error = null;
  try {
    payload = await ctx.api("/api/restore", {
      clip_id: planSelection.clipId,
      ranges: planSelection.ranges,
      plan: false,
    });
  } catch (err) {
    error = err.message;
    ctx.emit("toast", error);
  }
  ctx.emit("op-result", { payload, error });
  if (!error) {
    planSelection = null;
    gesture = null;
    refreshPlanToolbar();
    updateGestureOverlay();
  }
}

function cancelPlanSelection() {
  planSelection = null;
  gesture = null;
  refreshPlanToolbar();
  updateGestureOverlay();
}

/** A small "…"/Cancel body, shared by both plan kinds while their fetch is
 * in flight or failed — the only variation is which kind's fetch is being
 * waited on, never the shape of this fallback. */
function fillPlanLoadingOrError(text) {
  planInfoEl.textContent = text;
  const cancelBtn = el("button", null, "Cancel");
  cancelBtn.addEventListener("click", cancelPlanSelection);
  planActionsEl.append(cancelBtn);
}

/** Clamped by `dom.js`'s ONE `clampFloating` — same bounds convention
 * `refreshCueToolbar` already established (scrolled content-space, not
 * `[0, clientWidth]`), because `planSelection.boxLeft/boxTop` are computed
 * the same way `cueSelection`'s are. */
function refreshPlanToolbar() {
  if (!planToolbarEl) return;
  if (!planSelection) {
    planToolbarEl.hidden = true;
    return;
  }
  planToolbarEl.hidden = false;
  planActionsEl.textContent = "";
  planWarnEl.hidden = true;
  planWarnEl.textContent = "";
  planConfirmCheckbox = null;

  if (planSelection.kind === "razor-choice") {
    planInfoEl.textContent = `${fmt(planSelection.span[0])}–${fmt(planSelection.span[1])}`;
    const cutBtn = el("button", null, "Cut this range");
    const brollBtn = el("button", null, "Place b-roll over it");
    const cancelBtn = el("button", null, "Cancel");
    cutBtn.addEventListener("click", () => {
      const span = planSelection.span;
      const { boxLeft, boxTop } = planSelection;
      planSelection = { kind: "cut", span, boxLeft, boxTop };
      requestCutPlan(span);
    });
    brollBtn.addEventListener("click", () => {
      if (!lastState || !lastState.words) return;
      const startWord = nearestWordAt(lastState.words, planSelection.span[0]);
      const { boxLeft, boxTop } = planSelection;
      planSelection = null;
      refreshPlanToolbar();
      updateGestureOverlay();
      if (!startWord) {
        if (ctx) ctx.emit("toast", "No word here to anchor a cue to.");
        return;
      }
      openCuePlacement(startWord.index, boxLeft, boxTop, null);
    });
    cancelBtn.addEventListener("click", cancelPlanSelection);
    planActionsEl.append(cutBtn, brollBtn, cancelBtn);
  } else if (planSelection.kind === "cut") {
    if (planSelection.error) {
      fillPlanLoadingOrError(planSelection.error);
    } else if (!planSelection.result) {
      fillPlanLoadingOrError("…");
    } else {
      planInfoEl.textContent = cutPlanEcho(planSelection.result);
      const suspects = planSelection.result.suspect_boundaries || [];
      if (suspects.length) {
        planWarnEl.hidden = false;
        const list = el(
          "div",
          null,
          suspects
            .map((s) => `"${s.text}" claims ${s.duration.toFixed(2)}s, more than ${s.limit.toFixed(2)}s`)
            .join("; "),
        );
        planConfirmCheckbox = document.createElement("input");
        planConfirmCheckbox.type = "checkbox";
        planConfirmCheckbox.id = "plan-confirm-suspect";
        const label = el("label", null);
        label.append(planConfirmCheckbox, document.createTextNode(" this boundary is fine — cut it anyway"));
        planWarnEl.append(list, label);
      }
      const applyBtn = el("button", "primary", "Apply");
      const cancelBtn = el("button", null, "Cancel");
      applyBtn.addEventListener("click", applyCutPlan);
      cancelBtn.addEventListener("click", cancelPlanSelection);
      planActionsEl.append(applyBtn, cancelBtn);
    }
  } else if (planSelection.kind === "restore") {
    if (planSelection.error) {
      fillPlanLoadingOrError(planSelection.error);
    } else if (!planSelection.result) {
      fillPlanLoadingOrError("…");
    } else {
      planInfoEl.textContent = restorePlanEcho(planSelection.result);
      const applyBtn = el("button", "primary", "Restore");
      const cancelBtn = el("button", null, "Cancel");
      applyBtn.addEventListener("click", applyRestorePlan);
      cancelBtn.addEventListener("click", cancelPlanSelection);
      planActionsEl.append(applyBtn, cancelBtn);
    }
  }

  const lanes = $("track-lanes");
  if (lanes) {
    // Cap the height BEFORE measuring. `clampFloating` moves a box; it cannot
    // shrink one, so a popover taller than the lane box gets pinned to the
    // top with its actions row hanging past `overflow: hidden` and past the
    // viewport — reachable by a synthetic `.click()` and by nothing a person
    // can do. Measured at 218px inside a 143px box: Apply landed at y 920 of
    // a 900px viewport and `elementFromPoint` there was null. `--plan-max-h`
    // is what app.css's `.plan-toolbar` max-height reads, and the quote
    // scrolls inside it so the actions row stays on screen.
    planToolbarEl.style.setProperty("--plan-max-h", `${lanes.clientHeight}px`);
    const { left, top } = clampFloating(
      planSelection.boxLeft,
      planSelection.boxTop,
      planToolbarEl.offsetWidth,
      planToolbarEl.offsetHeight,
      lanes.scrollLeft,
      lanes.scrollLeft + lanes.clientWidth,
      0,
      lanes.clientHeight,
    );
    planToolbarEl.style.left = `${left.toFixed(1)}px`;
    planToolbarEl.style.top = `${top.toFixed(1)}px`;
  } else {
    planToolbarEl.style.left = `${planSelection.boxLeft.toFixed(1)}px`;
    planToolbarEl.style.top = `${planSelection.boxTop.toFixed(1)}px`;
  }
}

/** The maximal contiguous run of `present:false` words immediately adjacent
 * to a trimmed block's dragged edge — preceding the block's first word for
 * an in-edge drag, following its last word for an out-edge drag, stopping at
 * the first `present:true` word or the transcript boundary. Returns `null`
 * if nothing is there to restore (the anchor word itself is not found, or
 * the adjacent run is empty). This is the WHOLE gap deliberately, never a
 * fraction of it — a cut gap is zero-width on a compressed timeline, so
 * there is no pixel distance an outward drag could map to any particular
 * fraction, and inventing one would be exactly what CLAUDE.md forbids
 * ("nothing in JS derives a crop," applied here to a restore span). */
function adjacentCutRun(edge, origStart, origEnd) {
  if (!lastState || !lastState.words) return null;
  const words = lastState.words.slice().sort((a, b) => a.index - b.index);
  const EPS = 1e-4;
  let anchorIdx = -1;
  if (edge === "in") {
    for (let i = 0; i < words.length; i++) {
      if (words[i].present && Math.abs(words[i].timeline_start - origStart) < EPS) {
        anchorIdx = i;
        break;
      }
    }
    if (anchorIdx === -1) return null;
    let first = -1;
    let last = -1;
    for (let i = anchorIdx - 1; i >= 0; i--) {
      if (words[i].present) break;
      if (last === -1) last = words[i].index;
      first = words[i].index;
    }
    return first === -1 ? null : [first, last];
  }
  for (let i = 0; i < words.length; i++) {
    if (words[i].present && Math.abs(words[i].timeline_end - origEnd) < EPS) anchorIdx = i;
  }
  if (anchorIdx === -1) return null;
  let first = -1;
  let last = -1;
  for (let i = anchorIdx + 1; i < words.length; i++) {
    if (words[i].present) break;
    if (first === -1) first = words[i].index;
    last = words[i].index;
  }
  return first === -1 ? null : [first, last];
}

/** The ONE mousedown entry point for every lane gesture — cue-drag,
 * drag-trim, and razor select-a-range all dispatch from here, never from a
 * second listener (CLAUDE.md / this step's own central rule). Order:
 * 1) a `.trim-handle` under the pointer always wins, regardless of tool
 *    mode; 2) the razor tool, if armed, starts a range-select drag over any
 *    lane background; 3) otherwise the original cue-drag behaviour, word-
 *    resolved off `nearestWordAt`, unchanged. */
function handleLanesMouseDown(event) {
  if (event.button !== 0) return;
  // A drag's own trailing native 'click' is not guaranteed to follow its
  // mouseup — measured by driving real Input events: a press/release pair at
  // different points produced no 'click' at all here, so a stale `true` left
  // by a drag would otherwise swallow the *next* click instead of the drag's
  // own, which — right after placing a drag selection — is a click on the
  // toolbar's own "Place cue" button (browser pass). Any new mousedown means
  // that window has closed either way, so clear it unconditionally before
  // anything else below runs, including the toolbar-click early return.
  suppressNextClick = false;
  if (cueToolbarEl && cueToolbarEl.contains(event.target)) return;
  if (planToolbarEl && planToolbarEl.contains(event.target)) return;
  if (musicToolbarEl && musicToolbarEl.contains(event.target)) return;
  if (!lastState) return;

  const handle = event.target.closest && event.target.closest(".trim-handle");
  if (handle) {
    const edge = handle.dataset.edge;
    const origStart = parseFloat(handle.dataset.segTimelineStart);
    const origEnd = parseFloat(handle.dataset.segTimelineEnd);
    gesture = {
      kind: "trim",
      clipId: handle.dataset.clipId,
      edge,
      origStart,
      origEnd,
      proposedTime: edge === "in" ? origStart : origEnd,
      direction: "inward",
    };
    event.preventDefault(); // a handle sits inside a .clip-block; don't also seek
    return;
  }

  if (toolMode === "razor") {
    const t = snap(laneTimeFromEvent(event));
    gesture = { kind: "razor", startTime: t, currentTime: t, moved: false };
    return;
  }

  if (!lastState.words || !lastState.words.length) return;
  const word = nearestWordAt(lastState.words, laneTimeFromEvent(event));
  if (!word) {
    if (ctx) ctx.emit("toast", "No word here to anchor a cue to — every word in this clip is cut.");
    return;
  }
  cueSelection = null;
  refreshCueToolbar();
  // Which lane the press landed on decides which panel the drag OPENS, and
  // nothing else about the gesture: A2 is the bed's lane, so a drag across
  // it re-spans the bed, while the same drag anywhere else places a cue.
  // Read at mousedown rather than at mouseup because the pointer has moved
  // by then — a drag that starts on A2 and ends on V1 is still the bed's.
  const onMusicLane = !!(event.target.closest && event.target.closest(".lane-a2"));
  gesture = { kind: "cue", anchorIndex: word.index, currentIndex: word.index, moved: false, onMusicLane };
  selection = [word.index];
  // properties.js's word-inspection input — raised on every lane mousedown
  // that resolves to a word, drag or plain click alike, since a plain click
  // throws the resolved word away below (no cue gesture follows it) and
  // inspecting it is a reasonable thing for a click to do along the way.
  if (ctx) ctx.emit("inspect-word", { clipId: lastState.clip_id, wordIndex: word.index });
  // See updateGestureOverlay's own comment: a full render() here (even
  // deferred) tears down whatever node this mousedown landed on, and a
  // realistic human dwell before mouseup gives a deferred one time to fire
  // before the click is dispatched — measured, not assumed.
  updateGestureOverlay();
}

function handleLanesMouseMove(event) {
  if (!gesture || !lastState) return;

  if (gesture.kind === "cue") {
    if (!lastState.words) return;
    const word = nearestWordAt(lastState.words, laneTimeFromEvent(event));
    if (!word) return;
    if (word.index !== gesture.anchorIndex) gesture.moved = true;
    gesture.currentIndex = word.index;
    selection = [gesture.anchorIndex, gesture.currentIndex];
    // Same reason as handleLanesMouseDown: a full render() on every
    // mousemove tore down and rebuilt every lane dozens of times over one
    // drag, for a change that is only ever the highlight box.
    updateGestureOverlay();
    return;
  }

  if (gesture.kind === "trim") {
    const tolerance = snapTolerance();
    let t = snap(laneTimeFromEvent(event));
    // Clamp: an in-edge must stay short of the block's own out edge, and an
    // out-edge must stay past its own in edge — either direction of drag is
    // still meaningful (inward trims, outward restores), just never past the
    // opposite edge into a negative-width block.
    if (gesture.edge === "in") t = Math.min(t, gesture.origEnd - tolerance);
    else t = Math.max(t, gesture.origStart + tolerance);
    gesture.proposedTime = t;
    // Direction flips the moment the drag crosses one FRAME (never a bare
    // epsilon) past the block's own original edge, outward — short of that,
    // it reads as "still deciding," and inward is the more common gesture.
    const frameSec = 1 / ((lastState.shots_rate || lastState.timebase) || 30);
    if (gesture.edge === "in") {
      gesture.direction = t < gesture.origStart - frameSec ? "restore" : "inward";
    } else {
      gesture.direction = t > gesture.origEnd + frameSec ? "restore" : "inward";
    }
    updateGestureOverlay();
    return;
  }

  if (gesture.kind === "razor") {
    const t = snap(laneTimeFromEvent(event));
    if (t !== gesture.currentTime) gesture.moved = true;
    gesture.currentTime = t;
    updateGestureOverlay();
  }
}

function handleLanesMouseUp(event) {
  if (!gesture) return;

  if (gesture.kind === "cue") {
    if (gesture.moved) {
      suppressNextClick = true;
      const lanes = $("track-lanes");
      const rect = lanes.getBoundingClientRect();
      // boxLeft lives in the same content-relative space laneTimeFromEvent
      // resolves a click into — scrollLeft folded back in, for the same
      // reason (the container's own rect does not move when its content
      // scrolls). refreshCueToolbar's clamp expects this space.
      const boxLeft = Math.max(0, event.clientX - rect.left + lanes.scrollLeft);
      const boxTop = Math.max(0, event.clientY - rect.top + 10);
      // A cue is placed at the drag's START word and has no end (`cue_add`
      // is in-point only); a music bed spans two words, so the ORDERED pair
      // is carried too — a right-to-left drag means the same span, and
      // `ops.music` refuses an end before its start rather than reordering.
      const first = Math.min(gesture.anchorIndex, gesture.currentIndex);
      const last = Math.max(gesture.anchorIndex, gesture.currentIndex);
      if (gesture.onMusicLane) {
        gesture = null;
        openMusicPanel(first, last, boxLeft, boxTop);
        updateGestureOverlay();
        return;
      }
      cueSelection = {
        wordIndex: gesture.anchorIndex,
        wordIndexStart: first,
        wordIndexEnd: last,
        boxLeft,
        boxTop,
      };
      refreshCueToolbar();
    } else {
      selection = null;
      // Same reason as handleLanesMouseDown: this mouseup's handlers run
      // before the browser decides whether to dispatch the trailing 'click'
      // for this exact gesture, and any render() here — deferred or not —
      // risks removing the clicked node out from under that decision.
      updateGestureOverlay();
    }
  } else if (gesture.kind === "trim") {
    const origEdge = gesture.edge === "in" ? gesture.origStart : gesture.origEnd;
    const moved = Math.abs(gesture.proposedTime - origEdge) > snapTolerance();
    if (!moved) {
      // No real drag — clear `gesture` BEFORE the redraw so
      // `updateGestureOverlay` (which draws a trim's preview off `gesture`
      // itself, unlike the cue drag-box which draws off `selection`) has
      // nothing left to draw; otherwise a barely-moved handle click would
      // leave a stray sliver of `.trim-preview` with nothing left to clear
      // it until the next gesture or render().
      gesture = null;
      updateGestureOverlay();
      return;
    }
    suppressNextClick = true;
    const lanes = $("track-lanes");
    const rect = lanes.getBoundingClientRect();
    const boxLeft = Math.max(0, event.clientX - rect.left + lanes.scrollLeft);
    const boxTop = Math.max(0, event.clientY - rect.top + 10);
    if (gesture.direction === "inward") {
      const span =
        gesture.edge === "in"
          ? [gesture.origStart, gesture.proposedTime]
          : [gesture.proposedTime, gesture.origEnd];
      planSelection = { kind: "cut", span, clipId: gesture.clipId, boxLeft, boxTop };
      requestCutPlan(span);
    } else {
      const run = adjacentCutRun(gesture.edge, gesture.origStart, gesture.origEnd);
      if (!run) {
        if (ctx) ctx.emit("toast", "nothing cut here to restore");
      } else {
        planSelection = { kind: "restore", ranges: [run], clipId: gesture.clipId, boxLeft, boxTop };
        requestRestorePlan([run]);
      }
    }
    // The trim-preview band stays showing the final proposed range while the
    // plan popover confirms it — same precedent as the cue drag-box, which
    // is also left drawn (not cleared) across a successful drag's mouseup.
    updateGestureOverlay();
  } else if (gesture.kind === "razor") {
    if (!gesture.moved) {
      // Same reasoning as the trim branch above: a plain razor click with no
      // drag is a deliberate no-op (contract: "not an error"), so clear
      // `gesture` before the redraw rather than leave a stray range-band.
      gesture = null;
      updateGestureOverlay();
      return;
    }
    const lanes = $("track-lanes");
    const rect = lanes.getBoundingClientRect();
    const boxLeft = Math.max(0, event.clientX - rect.left + lanes.scrollLeft);
    const boxTop = Math.max(0, event.clientY - rect.top + 10);
    const span = [Math.min(gesture.startTime, gesture.currentTime), Math.max(gesture.startTime, gesture.currentTime)];
    planSelection = { kind: "razor-choice", span, boxLeft, boxTop };
    refreshPlanToolbar();
    // Same precedent as the trim branch: the range-band stays showing the
    // selected range while the two-verb popover is open.
    updateGestureOverlay();
  }

  gesture = null;
}

/** F5 — nudges `#track-lanes`'s `scrollLeft` so the playhead stays inside
 * the middle ~60% of the visible lane while playing. Called from the SAME
 * per-frame `'playhead'` subscription that already moves the line, and must
 * touch nothing but `scrollLeft` — no `render()`, on this file's own
 * "redraw only the node a gesture owns" discipline (`updateGestureOverlay`'s
 * comment above): this runs every animation frame, so anything heavier than
 * a scroll assignment here would cost what the deferred-render race already
 * cost this repo a day to find, just on a hot path instead of a gesture.
 *
 * Measured at zoom 6.0x before this existed: viewport 1516px, content
 * 9101px, playhead at 4548px with scrollLeft stuck at 0 — the timeline
 * silently stopped being a view of what was playing within seconds of
 * pressing play. */
function nudgePlayhead(nowSec) {
  if (!followPlayhead || !ctx || !ctx.player.playing()) return;
  const lanes = $("track-lanes");
  if (!lanes) return;
  const viewport = lanes.clientWidth;
  if (!(viewport > 0)) return;
  const playheadPx = nowSec * currentPxPerSec;
  const visibleLeft = lanes.scrollLeft;
  const margin = viewport * 0.2; // 20% each side leaves the middle 60% named above
  if (playheadPx >= visibleLeft + margin && playheadPx <= visibleLeft + viewport - margin) return;
  const maxScroll = Math.max(0, lanes.scrollWidth - viewport);
  const target = Math.min(maxScroll, Math.max(0, playheadPx - viewport / 2));
  lanes.scrollLeft = target;
  // Read back rather than trust `target`: the browser clamps scrollLeft to
  // its own valid range, and the value the 'scroll' event later reports is
  // THAT clamped number, not the one just assigned.
  lastFollowScrollLeft = lanes.scrollLeft;
}

/** The click handler for `#follow-playhead` and the disengage branch of the
 * `#track-lanes` 'scroll' listener share this — one place that keeps the
 * variable and the button's `.on` class from drifting apart. */
function setFollowPlayhead(value) {
  followPlayhead = value;
  const btn = $("follow-playhead");
  if (btn) btn.classList.toggle("on", followPlayhead);
}

function render() {
  const headers = $("track-headers");
  const lanes = $("track-lanes");
  if (!headers || !lanes) return;
  headers.textContent = "";
  lanes.textContent = "";
  headers.append(el("div", "track-header ruler-spacer"));

  const state = lastState;
  if (!state || !state.segments) {
    lanes.append(el("div", "ruler"));
    return;
  }

  // Read before the lanes are laid out, because the width they get laid out
  // at has to be the width of everything they will draw — see contentDuration.
  const captions = ctx ? ctx.getCaptions() : null;
  const duration = contentDuration(state, captions);
  const pxPerSec = computePxPerSec(duration);
  currentPxPerSec = pxPerSec;
  laidOutWidth = lanes.clientWidth; // 0 while Edit is hidden — see the declaration
  lanes.append(buildRuler(duration, pxPerSec));

  const clip = state.clips.find((c) => c.clip_id === state.clip_id) || {};

  // Which lanes exist is a data question, answered by the view. V1/A1/CC are
  // projections of the one `Edit`, drawn from `state.segments` — the same
  // segments `export` renders. V2 is the cue table's picture, drawn from
  // `state.shots` — which `export` renders through MLT and `melt`, and could
  // not before step 5 (CLAUDE.md; PLAN.md § The layered timeline).
  // The captions are their own read model (/api/captions) — same edit, but
  // placed, grouped and styled, and none of those three are this file's to do.
  // (Read above, with the duration it also feeds.)

  const kinds = [];
  // Topmost of all: an overlay is drawn over every picture track.
  if ((state.overlays && state.overlays.length) || state.overlays_error) kinds.push("OV");
  if (state.shots || state.shots_error) kinds.push("V2"); // topmost: the picture sits over the edit's own track
  if (clip.has_video) kinds.push("V1");
  kinds.push("A1"); // always — the recording has audio even for a picture clip
  if (state.music || state.music_error) kinds.push("A2"); // only with a bed recorded — never a lane `export` does not mix
  if ((state.sounds && state.sounds.length) || state.sounds_error) kinds.push("SFX");
  if (state.words && state.words.length) kinds.push("CC"); // captions come out of the timeline (CLAUDE.md) — any transcript is enough to try

  // Before any row is built: `laneHeightPx()` and each row's own
  // `clientHeight` are read while building, so a lane height settled
  // afterwards would draw this pass at the previous one's scale.
  fitLaneHeight(kinds.length);
  laidOutHeight = lanes.clientHeight;

  const waveformDraws = [];
  for (const kind of kinds) {
    let note;
    if (kind === "OV") note = "overlays, drawn over the film";
    if (kind === "V2") note = "the cue table's picture, over the edit";
    if (kind === "A2") note = "the music bed, mixed under the edit";
    if (kind === "SFX") note = "one-shot sounds, one tick per hit";
    if (kind === "CC") note = "one block per cue, as the .ass will break them";
    headers.append(header(kind, note));
    const row =
      kind === "OV"
        ? buildOverlayRow(state, pxPerSec, duration)
        : kind === "V2"
        ? buildPictureRow(state, pxPerSec, duration)
        : kind === "A2"
          ? buildMusicRow(state, pxPerSec, duration)
          : kind === "SFX"
          ? buildSoundRow(state, pxPerSec, duration)
          : kind === "CC"
            ? buildCaptionRow(captions, pxPerSec, duration)
            : buildLaneRow(kind, state.segments, pxPerSec, duration, state, kind === "V1");
    if (kind === "A1") {
      const canvas = el("canvas", "waveform-canvas");
      // The blocks underneath still carry hover/title/hit-testing; letting
      // the canvas ignore pointer events is what keeps that true once it's
      // painted on top of them.
      canvas.style.pointerEvents = "none";
      row.append(canvas);
      waveformDraws.push(() =>
        drawWaveformLane(canvas, state.segments, pxPerSec, row.clientHeight || laneHeightPx(), duration * pxPerSec),
      );
    }
    lanes.append(row);
  }

  const playhead = el("div", "playhead-line");
  playhead.id = "timeline-playhead";
  playhead.style.left = `${(ctx ? ctx.player.now() : 0) * pxPerSec}px`;
  lanes.append(playhead);

  if (selection) drawSelectionHighlight(lanes, selection, pxPerSec, state);

  // Persistent nodes, re-appended every render — `lanes.textContent = ""`
  // above would otherwise drop them along with the rows, and a fresh element
  // each time would lose whatever the asset field has typed in it (cue) or
  // which plan/verb the person is mid-decision on (plan).
  lanes.append(cueToolbarEl);
  refreshCueToolbar();
  lanes.append(planToolbarEl);
  refreshPlanToolbar();
  lanes.append(musicToolbarEl);
  refreshMusicToolbar();

  // Deferred until the rows are actually in the DOM: drawWaveformLane reads
  // row.clientHeight, which is 0 for a detached node.
  for (const draw of waveformDraws) draw();
}

/** Drag source of an assets-pane row → drop on the V2 lane, item G's other
 * half (assets.js's `dragstart` is builder 3's — this is the target side).
 * A single standalone `.drop-ghost` node, repositioned on every `dragover`
 * and removed on `dragleave`/`drop` — a separate lifecycle from `gesture`
 * above (native DnD events, not mouse events), so it is never touched by
 * `updateGestureOverlay()` and never rebuilds a row/block either. */
function drawDropGhost(t) {
  const lanes = $("track-lanes");
  if (!lanes) return;
  if (!dropGhostEl) dropGhostEl = el("div", "drop-ghost");
  if (!dropGhostEl.isConnected) lanes.append(dropGhostEl);
  dropGhostEl.style.left = `${(t * currentPxPerSec).toFixed(1)}px`;
}

function clearDropGhost() {
  if (dropGhostEl && dropGhostEl.isConnected) dropGhostEl.remove();
}

export function init(passedCtx) {
  ctx = passedCtx;
  buildCueToolbar();
  buildPlanToolbar();
  buildMusicToolbar();

  const lanes = $("track-lanes");
  if (lanes) {
    lanes.addEventListener("mousedown", handleLanesMouseDown);
    window.addEventListener("mousemove", handleLanesMouseMove);
    window.addEventListener("mouseup", handleLanesMouseUp);
    // Capture phase, so this runs before any row's own bubble-phase
    // seekOnClick listener — a drag that moved should not also seek.
    lanes.addEventListener(
      "click",
      (event) => {
        if (!suppressNextClick) return;
        suppressNextClick = false;
        event.stopPropagation();
        event.preventDefault();
      },
      true,
    );

    // Item G's drop-target half — HTML5 drag-and-drop, an entirely separate
    // event family from the mousedown triple above, so this is not a second
    // mousedown listener. assets.js (builder 3) is the drag SOURCE, setting
    // `application/x-proofcut-asset` to `{kind:"clip"|"card", id}` — the
    // FOOTAGE being dragged, never an addressing clip_id.
    lanes.addEventListener("dragover", (event) => {
      if (!event.dataTransfer || !event.dataTransfer.types.includes("application/x-proofcut-asset")) return;
      event.preventDefault();
      if (!lastState) return;
      drawDropGhost(snap(laneTimeFromEvent(event)));
    });
    lanes.addEventListener("dragleave", (event) => {
      if (event.target === lanes) clearDropGhost();
    });
    lanes.addEventListener("drop", (event) => {
      event.preventDefault();
      clearDropGhost();
      if (!event.dataTransfer || !lastState || !lastState.words) return;
      const raw = event.dataTransfer.getData("application/x-proofcut-asset");
      if (!raw) return;
      let asset;
      try {
        asset = JSON.parse(raw);
      } catch {
        return;
      }
      const word = nearestWordAt(lastState.words, snap(laneTimeFromEvent(event)));
      if (!word) {
        if (ctx) ctx.emit("toast", "No word here to anchor a cue to.");
        return;
      }
      const rect = lanes.getBoundingClientRect();
      openCuePlacement(
        word.index,
        Math.max(0, event.clientX - rect.left + lanes.scrollLeft),
        Math.max(0, event.clientY - rect.top + 10),
        asset.id,
      );
    });
  }

  const followBtn = $("follow-playhead");
  if (followBtn) {
    followBtn.classList.toggle("on", followPlayhead); // agree with the markup's own default-on class
    followBtn.addEventListener("click", () => setFollowPlayhead(!followPlayhead));
  }

  const razorBtn = $("razor-tool");
  if (razorBtn) {
    razorBtn.addEventListener("click", () => {
      toolMode = toolMode === "razor" ? "select" : "razor";
      razorBtn.classList.toggle("on", toolMode === "razor");
      if (lanes) lanes.style.cursor = toolMode === "razor" ? "col-resize" : "";
    });
  }

  const snapBtn = $("snap-toggle");
  if (snapBtn) {
    snapEnabled = snapBtn.classList.contains("on"); // agree with the markup's own default-on class
    snapBtn.addEventListener("click", () => {
      snapEnabled = !snapEnabled;
      snapBtn.classList.toggle("on", snapEnabled);
    });
  }
  if (lanes) {
    lanes.addEventListener("scroll", () => {
      // See lastFollowScrollLeft's own comment: compare the reported value,
      // don't trust a flag. A match means this file's own nudge produced
      // this event — consume it and leave follow engaged. Anything else,
      // including a nudge that landed on the SAME pixel it started at (which
      // dispatches no event and so is never seen here at all), is a real
      // scroll and releases follow so it never fights a person scrubbing by
      // hand.
      if (lastFollowScrollLeft !== null && lanes.scrollLeft === lastFollowScrollLeft) {
        lastFollowScrollLeft = null;
        return;
      }
      if (followPlayhead) setFollowPlayhead(false);
    });
  }

  const zoomInput = $("zoom");
  if (zoomInput) {
    zoomMultiplier = parseFloat(zoomInput.value) || 1;
    zoomInput.addEventListener("input", () => {
      zoomMultiplier = parseFloat(zoomInput.value) || 1;
      if (lastState) render();
    });
  }

  window.addEventListener("resize", () => {
    if (lastState) render();
  });

  // The pane's height changes without the window's: player.js hands the
  // preview's unused height over by writing `--timeline-h`, and that fires no
  // `resize` at all. Without this the lanes keep the height they were built
  // at and the pane grows a band of empty space under them — which is the
  // dead space simply relocated, the exact thing balancePanes exists to
  // remove. Guarded on the height the way the `mode` listener below is
  // guarded on the width, and for the same reason: render() repaints the
  // waveform canvas and is not free.
  const lanesBox = $("track-lanes");
  if (lanesBox && typeof ResizeObserver === "function") {
    new ResizeObserver(() => {
      if (!lastState) return;
      if (lanesBox.clientHeight !== laidOutHeight) render();
    }).observe(lanesBox);
  }

  // A pane's work rides being LOOKED AT, and this is the other half of that
  // rule: `frame.js` uses this event to avoid working while hidden, and this
  // file uses it to redo work it could only do wrongly while hidden.
  //
  // `computePxPerSec` reads #track-lanes's clientWidth **or 800**, and a
  // hidden pane measures 0 — so any render that lands while Edit is not the
  // visible mode lays every lane out at 800px. Two ordinary things do that:
  // a `project-changed` (the render job's own completion is one) and the
  // `resize` listener above, both of which fire regardless of mode. Coming
  // back to Edit never re-measured, so the film drew a third short inside a
  // 1316px pane with `zoom` still reading 1 — right enough to look
  // deliberate, and wrong at every scale a gesture converts through.
  //
  // Guarded on the width rather than fired on every mode switch: render()
  // rebuilds every lane and repaints the waveform canvas, and paying that to
  // redraw what is already correct is how a fix becomes the next complaint.
  ctx.on("mode", (mode) => {
    if (mode !== "edit" || !lastState) return;
    const lanesNow = $("track-lanes");
    if (lanesNow && lanesNow.clientWidth !== laidOutWidth) render();
  });

  // The lanes are CSS and repaint themselves, but the waveform is a canvas
  // and holds whatever ink it was drawn with — so a theme flip has to redraw
  // it or it keeps the previous theme's. theme.js raises this for both the
  // toggle and an OS preference change.
  window.addEventListener("proofcut:theme", () => {
    if (lastState) render();
  });

  // Cheap per-frame path: player.js emits this every animation frame, so
  // this must not re-render the whole lane set — just slide the one line,
  // in the same px-per-second coordinate space `render()` last computed.
  ctx.on("playhead", ({ now }) => {
    const line = $("timeline-playhead");
    if (line) line.style.left = `${now * currentPxPerSec}px`;
    nudgePlayhead(now); // F5 — see nudgePlayhead's own comment: scrollLeft only, never render()
  });

  // This file's own drag gesture (handleLanesMouseDown/Move/Up) sets
  // `selection` directly rather than round-tripping through the bus — this
  // subscription is for any other pane that wants to drive the highlight
  // without duplicating drawSelectionHighlight's box math.
  ctx.on("selection", (payload) => {
    selection = payload && Array.isArray(payload.indices) && payload.indices.length ? payload.indices : null;
    if (lastState) render();
  });
}

export function update(state) {
  lastState = state;
  render();
}

// -- session restore accessors (docs/plans/STUDIO.md Step 04, contract § E) -----------
//
// Four small getters/setters, added for `app.js`'s session restore/save and
// nothing else — no other caller exists yet. Each setter reuses the exact
// path the UI control it mirrors already uses (the #zoom slider's own
// `input` listener, above) rather than inventing a second redraw route.

/** The #zoom slider's current multiplier — 1..10, whatever `#zoom`'s own
 * `input` listener last set (or the slider's markup default before init()). */
export function getZoom() {
  return zoomMultiplier;
}

/** Sets the multiplier and re-renders, exactly like a person dragging the
 * slider would — also moves `#zoom`'s own value so the widget agrees with
 * what got restored, not just the internal state. */
export function setZoom(v) {
  zoomMultiplier = v;
  const zoomInput = $("zoom");
  if (zoomInput) zoomInput.value = v;
  if (lastState) render();
}

/** `#track-lanes`'s raw `scrollLeft` — the element IS the state, there is no
 * module variable to read (see `lastFollowScrollLeft`'s own comment above:
 * that tracks a nudge, not the position). */
export function getScrollLeft() {
  const lanes = $("track-lanes");
  return lanes ? lanes.scrollLeft : 0;
}

export function setScrollLeft(v) {
  const lanes = $("track-lanes");
  if (lanes) lanes.scrollLeft = v;
}

/** The word-highlight selection, `{indices}` (never the bare array — matches
 * the shape the `'selection'` bus event above already accepts) or `null`
 * when nothing is selected. No setter: driving a selection in already goes
 * through `ctx.emit("selection", {indices})`, per this file's own listener
 * a few lines up — a second way in would be a second thing to keep in sync. */
export function getSelection() {
  return selection ? { indices: selection } : null;
}
