/* player.js — the seam-jumping playback loop and transport.
 *
 * Ported UNCHANGED in logic from tier 2's app.js (PLAN.md § Files, and why
 * they split: "it is the one genuinely good piece and is not rewritten for
 * tidiness — it is the thing that makes seeing an edit cost no render").
 * What changed is wiring only: DOM ids for the new layout, and reading the
 * view through `ctx.getView()` instead of a module-level variable, because
 * the view now lives in app.js and every pane reads the same copy of it.
 *
 * New in tier 3, and not a logic change to the loop itself: the audio-only
 * level display (PLAN.md § Layout — #viewer is never `display:none`), and the
 * picture layer, which shows the V2 shot under the playhead over whatever the
 * transport is playing. Both hang off the same tick; neither touches the seam
 * logic, which still owns `media` alone. Newer still, and not on the tick at
 * all: the frame — #frame is the project canvas and every layer draws inside
 * it, with media placed at the rect the render places it at rather than fitted
 * to its own shape. See § the frame, below.
 *
 * Exports:
 *   init(ctx)      call once, after `$("media")` etc. exist. Wires the
 *                  <video>, the transport, the audio-only visualiser, and
 *                  starts the tick loop.
 *   update(state)  call with the current /api/view payload whenever it
 *                  changes. Toggles the audio-only state and loads the
 *                  timeline's own clip the first time a view arrives.
 *   captions(p)    call with the current /api/captions payload. A second
 *                  read model rather than a field on the view: it is derived
 *                  from the same edit but grouped and styled, and a front end
 *                  must never do either itself.
 *   player         the object placed on `ctx.player` for the other panes:
 *                    seek(t)       seek the timeline to `t` seconds
 *                    now()         the current timeline second
 *                    toggle()      play/pause
 *                    playing()     bool
 *                    seekWord(w)   seek to a word's timeline_start, if present
 *
 * player.js owns #viewer, #frame (with #frame-note), #media, #visualizer,
 * #picture (with #picture-video, #picture-pane,
 * #picture-still, #picture-note), #inset-layer, #overlay-layer, #caption-layer (with #caption-line),
 * #transport, #play, #clock, #playhint and touches no other pane's DOM. Every animation frame it emits
 * a 'playhead' event `{now, total}` on the shared bus, and a 'playing-word'
 * event `{index}` whenever the playing word changes — transcript.js and
 * timeline.js paint their own playheads/highlights from those rather than
 * this module reaching into their DOM (PLAN.md § Files, and why they
 * split: panes never import each other).
 *
 * Also new: the transport keymap (§ the transport keymap, below), which
 * widens the one existing global key binding (Space) into J/K/L, frame and
 * second stepping, Home/End, seam-to-seam, undo and the shortcut sheet. Undo
 * and the sheet are not this module's to act on — it emits 'shortcut-undo'
 * and 'shortcut-help' on the bus and leaves the DOM/network side of both to
 * whoever owns #undo and #shortcuts-sheet, the same split as 'playhead' and
 * 'playing-word' above.
 */

import { $, fmt } from "./dom.js";

/* A seam is a seek, and a seek is not sample-accurate. Stop a segment this
 * far early rather than let the first frames of cut material through. */
const SEAM_EPS = 0.02;

let ctx = null;
let media = null;
let frame = null; // the project canvas, in #viewer's pixels
let frameNote = null; // a refused crop rect, in words
let visualizer = null;
let vizCtx = null;
let audioCtx = null;
let analyser = null;

/* The picture layer's drift budget. The transport and the picture are two
 * media elements playing from two files, so they cannot be frame-locked —
 * `now()` is the only clock, and the picture is corrected back to it whenever
 * it wanders this far. Small enough that nobody sees the correction, large
 * enough that a decoder's ordinary jitter does not cause one every frame. */
const PICTURE_DRIFT = 0.15;
/* Paused is a different problem: nothing is jittering, so the picture is
 * seeked to the exact frame and only a real disagreement moves it. */
const PICTURE_EPS = 0.04;

let mediaClip = null; // which clip <video> currently has loaded
let segIndex = -1; // which timeline segment is playing
let pendingSeek = null; // a seek waiting on loadedmetadata
let wordCursor = 0; // cache for the playing-word scan

let picture = null; // the V2 layer, or null before init
let pictureVideo = null;
let picturePane = null;
let pictureStill = null;
let pictureNote = null;
let pictureAsset = null; // which asset the picture layer currently holds
let pictureDest = null; // and the shot's own placement, which the asset does not fix
let picturePaneDest = null; // the lower half of a stacked split, null on every other shot
let pendingPictureSeek = null; // as pendingSeek, for the picture element
let pendingPaneSeek = null; // and its own for the split's lower pane, which
// loads independently: one variable would be cleared by whichever element
// happened to fire `loadedmetadata` first, leaving the other at zero
let pictureFill = null; // a blur-filled shot's background element
let pictureFillView = null; // and the shot's `fill` it is drawing, null when none
let pendingFillSeek = null; // its own pending seek, for the pane's reason
let mediaFill = null; // the edit track's blur-fill background element
let shotCursor = 0; // cache for the shot-under-the-playhead scan
const pictureRefused = new Set(); // assets the browser would not decode
let insetLayer = null; // the insets' layer, or null before init
const insetSlots = []; // one {box, dim, video} per stack position, reused across views
let overlayLayer = null; // the overlays' layer, or null before init
const overlayImages = []; // one <img> per stack position, reused across views

function view() {
  return ctx.getView();
}

function currentClip() {
  const v = view();
  if (!v) return {};
  return v.clips.find((c) => c.clip_id === v.clip_id) || {};
}

function setClip(clipId) {
  if (mediaClip === clipId) return;
  mediaClip = clipId;
  media.src = `/api/media/${encodeURIComponent(clipId)}`;
  place(media, clipId);
  placeEditFill();
}

/* -- blur-fill backgrounds ------------------------------------------------
 *
 * A blur-filled window draws the whole source contained, over a blurred,
 * darkened copy of the same moment covering the canvas (PLAN.md § Blur-fill).
 * The writer's second node is a second element here, placed by the rect the
 * server sent. The blur is `fill.blur` of the width it is drawn at, never a
 * pixel constant: melt's radius is relative to the image, so a fixed CSS
 * radius would be three times as strong in a small window as in a large one.
 * A box of radius r spreads like a Gaussian of r/√3, which is what CSS takes.
 */

function edgeFill() {
  const v = view();
  const entry = mediaClip && v && v.reframe ? v.reframe[mediaClip] : null;
  return entry ? entry.fill || null : null;
}

function placeFill(el, fill) {
  if (!el) return;
  const canvas = canvasSize();
  const box = frameBox();
  if (!fill || !canvas || !box.width) {
    el.style.filter = "";
    return;
  }
  const scale = box.width / canvas[0];
  const [x, y, w, h] = fill.dest;
  const sigma = (fill.blur * w * scale) / Math.sqrt(3);
  // CSS blurs an element's own edges into transparency, and a covering
  // background's edges sit on the frame's, which drew a dark band along them
  // that melt's blur does not. Grown by 3σ each way, centred: a zoom of a few
  // percent on something already blurred, where the band was plainly visible.
  const grow = (h * scale + 6 * sigma) / (h * scale);
  const cx = x + w / 2;
  const cy = y + h / 2;
  place(el, null, [cx - (w * grow) / 2, cy - (h * grow) / 2, w * grow, h * grow]);
  el.style.filter = `blur(${sigma.toFixed(2)}px) brightness(${fill.darken})`;
}

/* Load `url` into a fill element when a fill needs it, or empty and hide it. */
function loadFill(el, url, fill) {
  if (!fill) {
    if (!el.hidden) {
      el.hidden = true;
      el.pause();
      el.removeAttribute("src");
      delete el.dataset.url;
    }
    return false;
  }
  el.hidden = false;
  if (el.dataset.url === url) return false;
  el.dataset.url = url;
  el.src = url;
  return true;
}

function placeEditFill() {
  if (!mediaFill) return;
  const fill = edgeFill();
  const url = mediaClip ? `/api/media/${encodeURIComponent(mediaClip)}` : "";
  loadFill(mediaFill, url, fill);
  placeFill(mediaFill, fill);
}

/* The edit track's clock is the transport itself, so its background follows
 * `media` directly. */
function followEditFill() {
  if (!mediaFill || mediaFill.hidden || mediaFill.readyState === 0) return;
  follow(mediaFill, media.currentTime, media.paused);
}

function segAt(t) {
  const segments = view().segments;
  for (let i = 0; i < segments.length; i++) {
    if (t < segments[i].timeline_end - 1e-6) return i;
  }
  return segments.length - 1;
}

function seek(t) {
  const v = view();
  if (!v || !v.segments.length) return;
  t = Math.max(0, Math.min(t, v.timeline_duration));
  segIndex = segAt(t);
  const seg = v.segments[segIndex];
  const target = seg.start + Math.max(0, t - seg.timeline_start);
  if (mediaClip !== seg.clip_id) {
    setClip(seg.clip_id);
    pendingSeek = target;
  } else if (media.readyState === 0) {
    pendingSeek = target;
  } else {
    media.currentTime = target;
  }
  paintPlayhead(t);
}

function now() {
  const v = view();
  if (!v || segIndex < 0 || !v.segments.length) return 0;
  const seg = v.segments[segIndex];
  if (!seg || mediaClip !== seg.clip_id) return seg ? seg.timeline_start : 0;
  const into = Math.min(Math.max(media.currentTime - seg.start, 0), seg.duration);
  return seg.timeline_start + into;
}

function playing() {
  return media ? !media.paused : false;
}

function toggle() {
  if (!media) return;
  if (media.paused) {
    ensureVisualizer();
    media.play().catch((err) => ctx.emit("toast", err.message));
  } else {
    media.pause();
  }
}

function seekWord(word) {
  if (word && word.present) seek(word.timeline_start);
}

export const player = { seek, now, toggle, playing, seekWord };

function tick() {
  requestAnimationFrame(tick);
  const v = view();
  if (!v || !v.segments.length) return;

  if (!media.paused && segIndex >= 0) {
    const seg = v.segments[segIndex];
    // The seam: this segment's source material has run out, and the next
    // frame of the file is something the edit removed.
    if (seg && mediaClip === seg.clip_id && media.currentTime >= seg.end - SEAM_EPS) {
      if (segIndex + 1 < v.segments.length) {
        const next = v.segments[segIndex + 1];
        segIndex += 1;
        if (mediaClip !== next.clip_id) {
          setClip(next.clip_id);
          pendingSeek = next.start;
        } else {
          media.currentTime = next.start;
        }
      } else {
        media.pause();
      }
    }
  }
  const t = now();
  paintPlayhead(t);
  paintWord(t);
  paintPicture(t);
  followEditFill();
  paintInsets(t);
  paintOverlays(t);
  paintCaption(t);
  drawVisualizer();
}

function paintPlayhead(t) {
  const v = view();
  $("clock").textContent = `${fmt(t)} / ${fmt(v ? v.timeline_duration : 0)}`;
  $("play").textContent = media.paused ? "▶" : "❚❚";
  ctx.emit("playhead", { now: t, total: (v && v.timeline_duration) || 1 });
}

function paintWord(t) {
  const v = view();
  if (!v || !v.words) return;
  const words = v.words;
  // Walk from where we left off; playback is monotonic except on a seek, and
  // a seek just costs one wrap.
  let found = -1;
  for (let n = 0; n < words.length; n++) {
    const i = (wordCursor + n) % words.length;
    const w = words[i];
    if (w.present && w.timeline_start <= t && t < w.timeline_end) {
      found = i;
      break;
    }
  }
  if (found === -1) return;
  wordCursor = found;
  const index = words[found].index;
  if (paintWord.last === index) return;
  paintWord.last = index;
  ctx.emit("playing-word", { index });
}

/* -- the frame: the project canvas, and where each source lands on it -----
 *
 * #frame is the rectangle the render declares — `timeline_view`'s `canvas`,
 * which is the MLT profile's own number — contain-fitted into #viewer. Every
 * layer in the pane lives inside it, so the picture and the captions cannot
 * disagree about where the frame is (PLAN.md § Aspect swap, finding 6).
 *
 * **Media is placed, not fitted.** Since the reframe shipped, the render
 * crops footage to fill this rectangle, so a `max-width: 100%` video would
 * draw exactly the material the export drops — the viewer's version of
 * drawing a lane `export` cannot produce. `reframe[clip].dest` is
 * `mlt.Reframe.dest_rect`: where the *whole* source frame lands on the canvas
 * so that its crop fills the frame, in canvas pixels, which is why it is
 * routinely wider than the canvas and starts at a negative x. Scaling it by
 * the frame's own size and letting #frame's `overflow: hidden` do the rest
 * reproduces the crop rather than re-deriving it — the same rect the writer
 * hands melt as a `qtblend` property.
 *
 * A clip with no entry (no picture, or no known size) and every still is left
 * to the stylesheet's `contain`, which is what MLT does when no filter is
 * emitted. So both branches here draw what the render draws.
 */

function frameBox() {
  return { width: frame.clientWidth, height: frame.clientHeight };
}

/* The canvas the frame is shaped to. Absent before the first view lands, and
 * on a project whose view failed — the frame is the viewer then, which is
 * what this pane did before it had a canvas at all. */
function canvasSize() {
  const v = view();
  const canvas = v && v.canvas;
  return canvas && canvas[0] > 0 && canvas[1] > 0 ? canvas : null;
}

/** Floors for the height trade below. The workspace one is the transcript's:
 *  below ~420px it stops being a document and becomes a viewport onto one. */
const WORKSPACE_MIN_H = 420;
const BALANCE_DEADBAND_PX = 8;

/** Give the preview's unused height to the timeline.
 *
 * `#viewer` is black on purpose — it is the letterbox around the canvas — but
 * a letterbox is only honest as far as the picture's own shape demands. A
 * 2.35:1 film in this pane is WIDTH-bound: the frame took 712x303 of a
 * 712x613 box, so 51% of the largest pane in the window was black, above and
 * below a picture that could not use it. Meanwhile the timeline underneath was
 * clipping its own bottom lane for want of five pixels.
 *
 * So the surplus moves. The target is computed ABSOLUTELY rather than by
 * adding a delta each pass — `wanted` is derived from the frame's own height
 * every time — which is what makes it idempotent: a re-run with nothing
 * changed computes the same number and the deadband stops the write. That
 * matters because this runs inside layoutFrame, and layoutFrame is what a
 * resize triggers; an incremental version would ratchet the timeline taller on
 * every resize and could never give the height back to a 9:16 project, where
 * the frame is height-bound and there is no surplus to take.
 *
 * The two floors are what keep it a trade rather than a takeover, and the
 * whole thing is skipped until the canvas is known — before that the frame is
 * `100%` of the box and its height is not a measurement of anything. */
function balancePanes(viewerHeight, frameHeight) {
  const workspace = $("workspace");
  const timeline = $("timeline-pane");
  if (!workspace || !timeline || workspace.hidden || timeline.hidden) return;
  // Layout pixels, like `lanes.clientHeight` below — a bounding rect is in
  // the transformed frame, and mixing the two under any ancestor transform
  // inflated the timeline on every reload until the preview was a 56px strip
  // (the launch recorder draws a 1280x720 layout at 1920x1080 through one).
  const workspaceH = workspace.offsetHeight;
  const timelineH = timeline.offsetHeight;
  if (!workspaceH || !timelineH) return;
  // Everything in the preview pane that is not the picture — its head row and
  // the transport — measured rather than assumed, so a control added to either
  // is accounted for without touching this.
  const chrome = workspaceH - viewerHeight;
  const total = workspaceH + timelineH;
  // Capped by what the timeline can actually USE. Handing it every spare
  // pixel just moves the dead space one pane down: its lanes grow to
  // `--lane-h-max` (timeline.js § fitLaneHeight) and stop, so anything past
  // that is a band of empty pane. Read off the DOM — the lane count is a
  // data question this file must not answer for itself, since which lanes
  // exist depends on the project (V1 only with video, A2 only with a bed).
  const lanes = document.getElementById("track-lanes");
  const laneCount = lanes ? lanes.querySelectorAll(".lane").length : 0;
  let wanted = total - (frameHeight + chrome);
  if (lanes && laneCount) {
    const style = getComputedStyle(document.documentElement);
    const laneMax = parseFloat(style.getPropertyValue("--lane-h-max")) || 88;
    const rulerH = parseFloat(style.getPropertyValue("--ruler-h")) || 22;
    const timelineChrome = timelineH - lanes.clientHeight;
    wanted = Math.min(wanted, timelineChrome + rulerH + laneCount * laneMax);
  }
  const target = Math.max(
    parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--timeline-h-min")) || 180,
    Math.min(wanted, total - WORKSPACE_MIN_H),
  );
  if (Math.abs(target - timelineH) < BALANCE_DEADBAND_PX) return;
  document.documentElement.style.setProperty("--timeline-h", `${Math.round(target)}px`);
}

function layoutFrame() {
  if (!frame) return;
  const viewer = $("viewer");
  const box = { width: viewer.clientWidth, height: viewer.clientHeight };
  const canvas = canvasSize();
  if (!canvas || !box.width || !box.height) {
    frame.style.width = "100%";
    frame.style.height = "100%";
  } else {
    const scale = Math.min(box.width / canvas[0], box.height / canvas[1]);
    frame.style.width = `${canvas[0] * scale}px`;
    frame.style.height = `${canvas[1] * scale}px`;
    balancePanes(box.height, canvas[1] * scale);
  }
  place(media, mediaClip);
  placeEditFill();
  place(pictureVideo, pictureAsset, pictureDest);
  place(picturePane, pictureAsset, picturePaneDest);
  placeFill(pictureFill, pictureFillView);
  // An inset re-places on its next paint only when its `dest` changes, and a
  // resize moves every pixel without changing one: forget what was placed.
  for (const slot of insetSlots) delete slot.video.dataset.dest;
}

/* Put one element where the render puts that source. Called whenever either
 * input moves: the element's asset (a seam, a new shot) or the frame's size.
 *
 * `dest` is a rect the caller already has — the picture layer's, because a
 * shot is framed per shot and the clip's entry is only its head window. With
 * none, the clip's own entry is the answer, which is what the edit track
 * wants. */
function place(el, clipId, dest) {
  if (!el) return;
  const v = view();
  const entry = clipId && v && v.reframe ? v.reframe[clipId] : null;
  const rect = dest || (entry ? entry.dest : null);
  const canvas = canvasSize();
  const box = frameBox();
  if (!rect || !canvas || !box.width) {
    // Back to the stylesheet's contain — MLT's own answer for anything it
    // emits no filter for.
    el.style.left = el.style.top = el.style.width = el.style.height = "";
    el.style.objectFit = "";
    return;
  }
  const scale = box.width / canvas[0];
  const [x, y, w, h] = rect;
  el.style.left = `${x * scale}px`;
  el.style.top = `${y * scale}px`;
  el.style.width = `${w * scale}px`;
  el.style.height = `${h * scale}px`;
  // `dest` already carries the source's aspect, so this only stops a rounded
  // pixel from re-letterboxing inside a box that is meant to be exact.
  el.style.objectFit = "fill";
}

/* -- the picture layer: the shot under the playhead ----------------------
 *
 * What makes V2 a picture rather than a plan of one. timeline.js draws the
 * shots as blocks; this shows the one the playhead is inside.
 *
 * It reads `view().shots` and nothing else — the same array the lane draws,
 * which is `timeline_view`'s projection *already through `mlt.plan_picture`*
 * (CLAUDE.md). That is the whole reason this is honest: `src_start` is where
 * the MLT writer decided this shot reads from inside its asset, so a clip used
 * three times previews from three different places, exactly as it will render.
 * Reading `build_shots` here instead would preview a shot `export` refuses.
 *
 * The audio is never this element's. The transport owns playback and `now()`
 * is the only clock in the window; the picture is a follower muted at the
 * source, so a shot whose asset has a soundtrack cannot talk over the VO.
 */

function shotAt(t) {
  const shots = view().shots;
  if (!shots || !shots.length) return null;
  // Same wrap-around walk as paintWord, and for the same reason: playback is
  // monotonic except on a seek, and a seek costs one lap.
  for (let n = 0; n < shots.length; n++) {
    const i = (shotCursor + n) % shots.length;
    const shot = shots[i];
    if (shot.start <= t && t < shot.start + shot.duration) {
      shotCursor = i;
      return shot;
    }
  }
  return null;
}

function assetURL(asset) {
  return `/api/asset/${encodeURIComponent(asset)}`;
}

function showPictureNote(text) {
  pictureNote.textContent = text;
  pictureNote.hidden = !text;
}

/* A refused asset is diagnosed on the box rather than guessed at in here: the
 * `error` event carries nothing, so the reason comes from `/api/preview`,
 * which probed the actual file. Asked once per asset — a failure is a property
 * of the file, and re-asking every time the playhead re-enters the shot would
 * put one fetch per frame on a codec problem. */
function diagnose(asset) {
  if (pictureRefused.has(asset)) return;
  pictureRefused.add(asset);
  showPictureNote(`${asset} — the browser refused this file`);
  fetch(`/api/preview/${encodeURIComponent(asset)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((info) => {
      if (info && info.reason && pictureAsset === asset) showPictureNote(`${asset} — ${info.reason}`);
    })
    .catch(() => {});
}

function loadShot(shot) {
  pictureAsset = shot.asset;
  showPictureNote(pictureRefused.has(shot.asset) ? `${shot.asset} — the browser refused this file` : "");
  if (shot.is_image) {
    pictureVideo.hidden = true;
    pictureVideo.pause();
    picturePane.hidden = true;
    picturePane.pause();
    loadFill(pictureFill, "", null);
    pictureFillView = null;
    pictureStill.hidden = false;
    pictureStill.src = assetURL(shot.asset);
  } else {
    pictureStill.hidden = true;
    pictureStill.removeAttribute("src");
    pictureVideo.hidden = false;
    pictureVideo.src = assetURL(shot.asset);
    // The split's second producer is the *same file* — which is exactly what
    // `mlt.document` emits, so loading it twice here is the preview being the
    // same shape as the render rather than an approximation of it.
    if (shot.dest_pane) {
      picturePane.hidden = false;
      picturePane.src = assetURL(shot.asset);
    } else {
      picturePane.hidden = true;
      picturePane.pause();
      picturePane.removeAttribute("src");
    }
    pendingPictureSeek = shot.src_start;
    pendingPaneSeek = shot.src_start;
  }
  placeShot(shot);
}

/* A fill can begin mid-asset — a window boundary inside one placement — so
 * its element is loaded when the shot's fill first appears, not only on a
 * new asset. */
function placeShotFill(shot) {
  const fill = shot.is_image ? null : shot.fill || null;
  if (loadFill(pictureFill, assetURL(shot.asset), fill)) pendingFillSeek = shot.src_start;
  if (fill === pictureFillView || (fill && pictureFillView && sameRect(fill.dest, pictureFillView.dest))) {
    pictureFillView = fill;
    return;
  }
  pictureFillView = fill;
  placeFill(pictureFill, fill);
}

/* A still is never placed (the render does not crop one); a clip goes where
 * the render puts *that shot*. `shot.dest` and not the clip's entry, because
 * framing is source-addressed: two shots of one asset can read either side of
 * a window boundary and so want different rects, with the asset unchanged —
 * which is why this is called per frame rather than only on a load. */
function placeShot(shot) {
  placeShotFill(shot);
  const dest = shot.is_image ? null : shot.dest || null;
  const pane = shot.is_image ? null : shot.dest_pane || null;
  if (sameRect(dest, pictureDest) && sameRect(pane, picturePaneDest) && pictureAsset === shot.asset)
    return;
  pictureDest = dest;
  picturePaneDest = pane;
  place(pictureVideo, shot.is_image ? null : shot.asset, dest);
  // Never the clip's own entry as a fallback: that is the *head* window, and
  // for a pane it would place the lower half by a rect belonging to another
  // shot. A shot with no pane is not a split, and the element is hidden.
  place(picturePane, null, pane);
}

function sameRect(a, b) {
  if (!a || !b) return a === b || (!a && !b);
  return a.length === b.length && a.every((value, i) => value === b[i]);
}

function paintPicture(t) {
  if (!picture) return;
  const shot = shotAt(t);
  if (!shot) {
    // No shot here is not a failure: the picture track has a hole, and what
    // shows through it is the edit's own track — which is what `export`
    // renders too. Hiding the layer *is* drawing that.
    if (picture.hidden) return;
    picture.hidden = true;
    pictureVideo.pause();
    picturePane.pause();
    pictureFill.pause();
    pictureAsset = null;
    pictureDest = null;
    picturePaneDest = null;
    return;
  }
  picture.hidden = false;
  if (pictureAsset !== shot.asset) loadShot(shot);
  else placeShot(shot);
  if (shot.is_image) return;

  const target = shot.src_start + Math.max(0, t - shot.start);
  if (pictureVideo.readyState === 0) {
    pendingPictureSeek = target;
    pendingPaneSeek = target;
    pendingFillSeek = target;
    return;
  }
  if (media.paused) {
    if (!pictureVideo.paused) pictureVideo.pause();
    if (Math.abs(pictureVideo.currentTime - target) > PICTURE_EPS) pictureVideo.currentTime = target;
    followPane(target, true);
    followFill(target, true);
    return;
  }
  if (Math.abs(pictureVideo.currentTime - target) > PICTURE_DRIFT) pictureVideo.currentTime = target;
  if (pictureVideo.paused) pictureVideo.play().catch(() => {});
  followPane(target, false);
  followFill(target, false);
}

/* -- insets ----------------------------------------------------------------
 *
 * The view's `insets` (`ops._inset_plan`), bottom of the stack first: a clip
 * drawn into a rect of the recording, with a black dim under it. Each is
 * placed at its `dest` — the rect mapped through the recording's head window,
 * which is where this pane draws the recording — so it sits on the same
 * pixels the recording shows it in; a slide the render makes is not drawn
 * here, as it is not for the recording either. Muted: the transport is the
 * only sound in this window. Held to `now()` the way the picture layer is.
 */
function insetSlot(i) {
  if (insetSlots[i]) return insetSlots[i];
  const box = document.createElement("div");
  box.className = "inset-slot";
  box.hidden = true;
  const dim = document.createElement("div");
  dim.className = "inset-dim";
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "metadata";
  box.append(dim, video);
  insetLayer.append(box); // DOM order is stack order
  insetSlots[i] = { box, dim, video, pending: null };
  video.addEventListener("loadedmetadata", () => {
    const slot = insetSlots[i];
    if (slot.pending !== null) {
      video.currentTime = slot.pending;
      slot.pending = null;
    }
  });
  return insetSlots[i];
}

function paintInsets(t) {
  if (!insetLayer) return;
  const v = view();
  const insets = (v && !v.insets_error && v.insets) || [];
  for (let i = 0; i < Math.max(insets.length, insetSlots.length); i += 1) {
    const inset = insets[i];
    const live = inset && t >= inset.timeline_start && t < inset.timeline_end;
    if (!live) {
      const idle = insetSlots[i];
      if (idle && !idle.box.hidden) {
        idle.box.hidden = true;
        idle.video.pause();
      }
      continue;
    }
    const slot = insetSlot(i);
    const url = assetURL(inset.asset);
    if (slot.video.dataset.src !== url) {
      slot.video.dataset.src = url;
      slot.video.src = url;
    }
    if (slot.video.dataset.dest !== String(inset.dest)) {
      slot.video.dataset.dest = String(inset.dest);
      place(slot.video, null, inset.dest);
    }
    const { opacity } = overlayMotion(inset, t - inset.timeline_start);
    slot.video.style.opacity = opacity.toFixed(3);
    slot.dim.style.opacity = ((inset.dim || 0) * opacity).toFixed(3);
    if (slot.box.hidden) slot.box.hidden = false;
    const target = inset.src_in + (t - inset.timeline_start);
    if (slot.video.readyState === 0) {
      slot.pending = target;
      continue;
    }
    if (media.paused) {
      if (!slot.video.paused) slot.video.pause();
      if (Math.abs(slot.video.currentTime - target) > PICTURE_EPS) slot.video.currentTime = target;
    } else {
      if (Math.abs(slot.video.currentTime - target) > PICTURE_DRIFT) slot.video.currentTime = target;
      if (slot.video.paused) slot.video.play().catch(() => {});
    }
  }
}

/* -- overlays --------------------------------------------------------------
 *
 * The view's `overlays` (`ops._overlay_plan`), bottom of the stack first, each
 * a canvas-sized transparent card drawn over the film. The writer animates
 * one `qtblend` rect per overlay — opacity, and for a rise a vertical offset
 * of `rise_px` at 1080 lines — on MLT's cubic curves, so this draws the same
 * two numbers on the same curves (`mlt.ease_fraction`, mirrored below). The
 * writer's one-pixel nudge is sub-pixel here and not drawn. Nothing in this
 * function decides where an overlay plays: the view already did.
 */
function easeFraction(name, t) {
  const x = Math.min(Math.max(t, 0), 1);
  if (name === "ease-in") return x ** 3;
  if (name === "ease-out") return 1 - (1 - x) ** 3;
  if (name === "ease") return x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2;
  return x;
}

/* The writer's own keys (`ops._view_keys`, `mlt.overlay_keys`), interpolated
 * at `u` seconds in: the curve leaving each key shapes the segment after it,
 * as MLT's operator does. A rect is fractions of the canvas, drawn as a
 * translate and a scale of the canvas-sized image from its top left — which
 * is what `qtblend` does with it — so a pop scales about the sticker and a
 * slide clears the frame exactly as the render does. */
function keyedMotion(keys, u) {
  if (!keys.length) return { x: 0, y: 0, w: 1, h: 1, opacity: 1 };
  if (u <= keys[0].t) return keys[0];
  for (let i = 0; i + 1 < keys.length; i += 1) {
    const a = keys[i];
    const b = keys[i + 1];
    if (u <= b.t) {
      const f = easeFraction(a.ease, b.t > a.t ? (u - a.t) / (b.t - a.t) : 1);
      const mix = (k) => a[k] + (b[k] - a[k]) * f;
      return { x: mix("x"), y: mix("y"), w: mix("w"), h: mix("h"), opacity: mix("opacity") };
    }
  }
  return keys[keys.length - 1];
}

/* Opacity and downward offset (as a fraction of the rise) at `u` seconds in.
 * Only for a view without keys; every overlay the view draws now carries them. */
function overlayMotion(overlay, u) {
  const span = overlay.timeline_end - overlay.timeline_start;
  const enter = overlay.enter === "none" ? 0 : overlay.enter_seconds || 0;
  const leave = overlay.leave === "none" ? 0 : overlay.leave_seconds || 0;
  if (enter > 0 && u < enter) {
    const f = easeFraction(overlay.enter_ease, u / enter);
    return { opacity: f, drop: overlay.enter === "rise" ? 1 - f : 0 };
  }
  if (leave > 0 && u > span - leave) {
    const f = easeFraction(overlay.leave_ease, (u - (span - leave)) / leave);
    return { opacity: 1 - f, drop: overlay.leave === "rise" ? f : 0 };
  }
  return { opacity: 1, drop: 0 };
}

/* An animated graphic's piece plays its captured frames: the frame at the
 * playhead, on the render's own grid (`rate`), held to the piece's count —
 * and a looping hold wraps, the way melt's `qimage` repeats a sequence. The
 * view already split the span into pieces; nothing here derives a phase.
 * Each piece's frames are fetched once, when it first goes live, and kept
 * decoded, so playback never waits on a request per frame. */
const graphicFrames = new Map(); // url -> Image, kept so the decode is kept

function graphicFrameURL(overlay, t) {
  const count = Math.max(1, overlay.frame_count || 1);
  const at = Math.floor((t - overlay.timeline_start) * overlay.rate + 1e-6);
  const k = overlay.layer === "hold" ? ((at % count) + count) % count : Math.min(count - 1, Math.max(0, at));
  // The capture's stamp rides the URL, so a recapture is new frames rather
  // than the browser's memory of the old ones.
  const base = `graphic:${overlay.graphic}/${overlay.layer}`;
  const url = (i) => `${assetURL(`${base}/${i}`)}?v=${overlay.stamp || ""}`;
  if (!graphicFrames.has(url(0))) {
    for (let i = 0; i < count; i += 1) {
      const image = new Image();
      image.src = url(i);
      graphicFrames.set(url(i), image);
    }
  }
  return url(k);
}

function paintOverlays(t) {
  if (!overlayLayer) return;
  const v = view();
  const overlays = (v && !v.overlays_error && v.overlays) || [];
  const box = frameBox();
  for (let i = 0; i < Math.max(overlays.length, overlayImages.length); i += 1) {
    const overlay = overlays[i];
    let img = overlayImages[i];
    const live = overlay && t >= overlay.timeline_start && t < overlay.timeline_end;
    if (!live) {
      if (img && !img.hidden) img.hidden = true;
      continue;
    }
    if (!img) {
      img = document.createElement("img");
      img.alt = "";
      img.hidden = true;
      overlayImages[i] = img;
      overlayLayer.append(img); // DOM order is stack order: position i over i-1
    }
    const url = overlay.graphic ? graphicFrameURL(overlay, t) : assetURL(overlay.asset);
    if (img.dataset.src !== url) {
      img.dataset.src = url;
      img.src = url;
    }
    if (Array.isArray(overlay.keys)) {
      const m = keyedMotion(overlay.keys, t - overlay.timeline_start);
      const moved = m.x || m.y || m.w !== 1 || m.h !== 1;
      img.style.opacity = m.opacity.toFixed(3);
      img.style.transformOrigin = "0 0";
      img.style.transform = moved
        ? `translate(${(m.x * (box.width || 0)).toFixed(2)}px, ${(m.y * (box.height || 0)).toFixed(2)}px) scale(${m.w.toFixed(4)}, ${m.h.toFixed(4)})`
        : "";
    } else {
      const { opacity, drop } = overlayMotion(overlay, t - overlay.timeline_start);
      const travel = ((overlay.rise_px || 0) * (box.height || 0)) / 1080;
      img.style.opacity = opacity.toFixed(3);
      img.style.transform = drop ? `translateY(${(travel * drop).toFixed(2)}px)` : "";
    }
    if (img.hidden) img.hidden = false;
  }
}

/* The split's lower pane, held to the same clock as the upper one.
 *
 * Against `target` rather than against `pictureVideo.currentTime`: `now()` is
 * the only clock in this window, and slaving one element to another compounds
 * their two drifts into one visible tear straight down the middle of the frame
 * — the two halves are the same footage a fraction of a second apart, which
 * reads as a decoder fault rather than as a split.
 */
function followPane(target, paused) {
  if (!picturePane || picturePane.hidden || picturePane.readyState === 0) return;
  follow(picturePane, target, paused);
}

/* A blur-fill background, on the same clock and for the same reason. */
function followFill(target, paused) {
  if (!pictureFill || pictureFill.hidden || pictureFill.readyState === 0) return;
  follow(pictureFill, target, paused);
}

function follow(el, target, paused) {
  if (paused) {
    if (!el.paused) el.pause();
    if (Math.abs(el.currentTime - target) > PICTURE_EPS) el.currentTime = target;
    return;
  }
  if (Math.abs(el.currentTime - target) > PICTURE_DRIFT) el.currentTime = target;
  if (el.paused) el.play().catch(() => {});
}

/* -- the caption layer: what the burn-in will put on the frame -----------
 *
 * Draws `/api/captions` — `ops.caption_view`, which is `add_captions` without
 * the file. That matters more here than anywhere else in this window: these
 * pixels are a claim about pixels ffmpeg will burn, so every visible property
 * arrives from the server already resolved (font, size, both colours, the
 * outline, the box, which corner, how far in) and nothing about the look is
 * decided in here or taken from proofcut's own palette. Where the CSS token rule
 * elsewhere is about a canvas silently refusing `light-dark(…)`, the rule here
 * is stronger and different: a caption that borrowed the theme would be a
 * preview of the window instead of a preview of the render.
 *
 * Three things it gets exactly right, because getting them approximately
 * right would make it a decoration:
 *
 *  * the cue boundaries and word timings are the ones `to_ass` writes — same
 *    grouping, from the same stored style, off the one derivation in
 *    `ops._caption_cues`;
 *  * the karaoke highlight uses `highlight_start`, not the word's own start,
 *    because a `\k` duration covers the gap before its word
 *    (`Cue.karaoke_spans`) — and it *fills* rather than stepping, see
 *    `paintKaraoke`;
 *  * the layer is placed over the *video's content box*, not the viewer's —
 *    a letterboxed frame must not show its captions floating in the black
 *    bars, which is precisely where the burn-in cannot put them.
 *
 * What it only approximates, and cannot do better in a browser: libass's
 * outline and box rendering (drawn here as a text stroke and a background),
 * and font substitution — libass picks its own replacement for a missing
 * family, and so does the browser, but not necessarily the same one.
 */

const CAPTION_REFERENCE = 1080; // captions.REFERENCE_HEIGHT — sizes are quoted
// against this, whatever the footage is, so the overlay scales by the ratio

let captionLayer = null;
let captionLine = null;
let captionState = null; // the /api/captions payload, or null before it lands
let cueCursor = 0;
let shownCue = null;

/* The frame the captions burn into: #frame, and it is no longer computed.
 *
 * It used to contain-fit `caption_view`'s `resolution` into #viewer, which was
 * a second construction of the same rectangle the picture was drawn against —
 * and finding 6 of PLAN.md § Aspect swap is that the two agreed only because
 * both derived from the media. Now one element is the canvas and everything
 * sits in it, so this returns that element's box and the CSS holds it.
 *
 * `resolution` is still the right *scale* reference and is read below: it is
 * the PlayRes `to_ass` writes, 1080 tall whatever the footage is, so a size in
 * it means the same thing on any canvas of that aspect. Measuring the box off
 * whichever element has picture would still be wrong for the reason it always
 * was — the Scream assembly's picture track has holes, and the captions are
 * the same size across them because the render's canvas does not move. */
function captionBox() {
  return { left: 0, top: 0, width: frame.clientWidth, height: frame.clientHeight };
}

/* ASS alignment is the numpad; the server sends it back as a name. */
function placeLine(position) {
  const bottom = position.startsWith("bottom");
  const top = position.startsWith("top");
  captionLayer.style.alignItems = bottom ? "flex-end" : top ? "flex-start" : "center";
  const left = position.endsWith("left");
  const right = position.endsWith("right");
  captionLayer.style.justifyContent = left ? "flex-start" : right ? "flex-end" : "center";
  return { bottom, top };
}

function cueAt(t) {
  const cues = captionState && captionState.cues;
  if (!cues || !cues.length) return null;
  // The same wrap-around walk as shotAt and paintWord, for the same reason.
  for (let n = 0; n < cues.length; n++) {
    const i = (cueCursor + n) % cues.length;
    if (cues[i].start <= t && t < cues[i].end) {
      cueCursor = i;
      return cues[i];
    }
  }
  return null;
}

/* Rebuild the line's contents. Word spans only when a word can differ from
 * its neighbours — karaoke or a reveal. Otherwise every word is the same
 * forever, and a span per word would be churn nobody can see. */
function buildLine(cue, look) {
  captionLine.textContent = "";
  if (!look.karaoke && (!look.reveal || look.reveal === "none")) {
    captionLine.textContent = cue.text;
    captionLine.style.color = look.text;
    return;
  }
  captionLine.style.color = look.text;
  cue.words.forEach((word, n) => {
    const span = document.createElement("span");
    span.className = "cw";
    span.textContent = n ? ` ${word.text}` : word.text;
    captionLine.append(span);
  });
}

/* A `\k` tag switches its word to PrimaryColour when its turn comes and the
 * word *stays* that colour for the rest of the line — karaoke is a fill that
 * sweeps left to right, not a single word lit at a time. So the test is
 * `highlight_start <= t`, with no upper bound.
 *
 * Measured, not assumed: burning this project's own `.ass` over a flat frame
 * and reading the pixels back put four words in the highlight colour at
 * t=4.70 where a one-word-at-a-time overlay had lit one (HISTORY.md § Caption
 * styling). A per-word-only highlight is a different construction — one
 * Dialogue event per word — and is not what `to_ass` writes today. */
function paintKaraoke(cue, look, t) {
  const spans = captionLine.children;
  for (let n = 0; n < spans.length && n < cue.words.length; n++) {
    spans[n].style.color = cue.words[n].highlight_start <= t ? look.highlight : look.text;
  }
}

/* The reveal, per word, from `t` alone — never a CSS transition, so a seek
 * lands on the right state. The mirror of `captions._reveal_tags`:
 *
 *  * opacity rises linearly over `reveal_ms` from the word's OWN start (not
 *    `highlight_start`, which is the fill's);
 *  * a blur starts at `reveal_blur`, a libass `\blur` whose gaussian sigma
 *    is 0.85 x that in reference pixels (measured, DAYDREAM.md § Caption
 *    reveal and corrections, designed, P2b) — and CSS `blur(r)` is sigma r;
 *  * libass blurs only the outline of a glyph that has one, so the burn draws
 *    no outline while it blurs and brings it back over the last 40%
 *    (`REVEAL_OUTLINE_FROM`). The stroke here follows the same ramp. The
 *    burn's fill turns sharp as that ramp starts; this preview's blur runs
 *    down to zero instead, which is the one place the two differ, and the
 *    difference lasts a few frames.
 *
 * `opacity` and `filter` do not affect layout, and neither do `\alpha` and
 * `\blur` — so both halves keep the line still (PLAN.md § Per-word caption
 * animation, finding 5's trap is about a property that reflows). */
const REVEAL_BLUR_SIGMA = 0.85; // captions.py: sigma per unit of \blur
const REVEAL_OUTLINE_FROM = 0.6; // captions.REVEAL_OUTLINE_FROM

function paintReveal(cue, look, t, scale) {
  const spans = captionLine.children;
  const seconds = look.reveal_ms / 1000;
  for (let n = 0; n < spans.length && n < cue.words.length; n++) {
    const p = Math.min(1, Math.max(0, (t - cue.words[n].start) / seconds));
    spans[n].style.opacity = String(p);
    if (look.reveal !== "blur") {
      spans[n].style.filter = "";
      continue;
    }
    const sigma = REVEAL_BLUR_SIGMA * look.reveal_blur * (1 - p) * scale;
    spans[n].style.filter = sigma > 0.01 ? `blur(${sigma}px)` : "";
    if (!look.box) {
      const ramp = Math.min(1, Math.max(0, (p - REVEAL_OUTLINE_FROM) / (1 - REVEAL_OUTLINE_FROM)));
      spans[n].style.webkitTextStroke = `${look.outline_width * ramp * scale}px ${look.outline_colour}`;
    }
  }
}

function paintCaption(t) {
  if (!captionLayer) return;
  const cue = cueAt(t);
  if (!cue) {
    if (!captionLayer.hidden) {
      captionLayer.hidden = true;
      shownCue = null;
    }
    return;
  }

  // A caption span draws its cues in a look of its own; `cue.style` indexes
  // `caption_view`'s `styles`, and look 0 is the project's.
  const look = (captionState.styles && captionState.styles[cue.style]) || captionState.style.resolved;
  // The layer's geometry is the frame's, held by CSS — only the scale is
  // computed here, and it is against the caption reference rather than the
  // canvas: a size is quoted at 1080 tall whatever the footage is.
  const scale = captionBox().height / CAPTION_REFERENCE;

  captionLayer.hidden = false;
  const edge = placeLine(look.position);
  captionLine.style.fontFamily = `"${look.font}", sans-serif`;
  // `size` is libass's: the face's win ascent+descent, not its em — so the
  // em CSS sizes by is `em_scale` of it (captions.em_scale).
  captionLine.style.fontSize = `${Math.max(1, look.size * (look.em_scale || 1) * scale)}px`;
  captionLine.style.fontWeight = look.bold ? "700" : "400";
  captionLine.style.paddingBottom = edge.bottom ? `${look.margin * scale}px` : "0";
  captionLine.style.paddingTop = edge.top ? `${look.margin * scale}px` : "0";

  if (look.box) {
    // BorderStyle 3 draws an opaque box, and libass paints it in the
    // *outline* colour — not the box/shadow colour, whose ASS name
    // (BackColour) suggests otherwise.
    captionLine.style.background = look.outline_colour;
    captionLine.style.webkitTextStroke = "";
    captionLine.style.boxDecorationBreak = "clone";
    captionLine.style.padding = `${0.12 * look.size * scale}px ${0.3 * look.size * scale}px`;
  } else {
    captionLine.style.background = "none";
    captionLine.style.webkitTextStroke = `${look.outline_width * scale}px ${look.outline_colour}`;
    captionLine.style.paintOrder = "stroke fill";
  }

  if (shownCue !== cue.start) {
    shownCue = cue.start;
    buildLine(cue, look);
  }
  if (look.karaoke) paintKaraoke(cue, look, t);
  if (look.reveal && look.reveal !== "none") paintReveal(cue, look, t, scale);
}

/* Called by app.js whenever /api/captions lands — on load, and again after
 * every 'project-changed', which now fires on a manifest write too so a
 * restyle from the agent panel reaches this without a reload
 * (webui.py `_revision`). */
export function captions(payload) {
  captionState = payload && payload.cues && payload.cues.length ? payload : null;
  cueCursor = 0;
  shownCue = null;
  if (captionLayer && !captionState) captionLayer.hidden = true;
}

/* -- the audio-only level display ----------------------------------------
 * Never `display:none` the viewer (PLAN.md § Layout) — an audio-only clip
 * gets a level display driven by the actual playing audio instead. A
 * `MediaElementSourceNode` can only be created once per element, so it is
 * created lazily on first play and then survives every later `src` change,
 * because `setClip` only ever reassigns `media.src`, never replaces the
 * element.
 */
function ensureVisualizer() {
  if (audioCtx || !visualizer) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    const source = audioCtx.createMediaElementSource(media);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    analyser.connect(audioCtx.destination);
  } catch {
    // Web Audio unsupported, or blocked until a user gesture that hasn't
    // happened yet — the transport still works either way, there is just no
    // level display drawn.
    audioCtx = null;
  }
}

/* The bar colour comes from CSS, so the palette keeps exactly one home
 * (app.css § TOKENS AND THEME). It is read off #visualizer's own computed
 * `color` and not from the `--viz-bar` custom property, which reads back as
 * unparsed `light-dark(…)` text that `fillStyle` silently refuses — see
 * app.css's header, and timeline.js's `canvasInk` for the same trap.
 *
 * Cached because this is called every animation frame, and invalidated by
 * theme.js's event rather than re-measured: that event covers both the toggle
 * and an OS preference change, which is every way the answer can move. */
let barColour = null;

function vizBarColour() {
  if (barColour === null) barColour = getComputedStyle(visualizer).color || "#74a8e8";
  return barColour;
}

function drawVisualizer() {
  if (!analyser || !vizCtx) return;
  if (!$("viewer").classList.contains("audio")) return;
  // The picture layer is opaque and covers this; drawing under it is work
  // nobody can see.
  if (picture && !picture.hidden) return;
  const data = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(data);
  const w = visualizer.width;
  const h = visualizer.height;
  vizCtx.clearRect(0, 0, w, h);
  vizCtx.fillStyle = vizBarColour();
  const barW = w / data.length;
  for (let i = 0; i < data.length; i++) {
    const barH = (data[i] / 255) * h;
    vizCtx.fillRect(i * barW, h - barH, Math.max(1, barW - 1), barH);
  }
}

export function update(state) {
  if (!state) return;
  $("viewer").classList.toggle("audio", !currentClip().has_video);
  // The canvas and every placement can have moved — a `canvas` or a `reframe`
  // is a manifest write, and `_revision` watches the manifest.
  layoutFrame();
  // A refused rect is drawn rather than raised, the same policy `shots_error`
  // has one pane over: the frame is drawn uncropped, and the reason it is
  // uncropped is on screen instead of being left to a pixel probe.
  if (frameNote) {
    frameNote.textContent = state.reframe_error ? `frame uncropped — ${state.reframe_error}` : "";
    frameNote.hidden = !state.reframe_error;
  }
  // A cue changed, or the plan started refusing: drop what the layer holds so
  // the next frame reloads against the new projection rather than keeping a
  // shot that no longer exists on screen.
  shotCursor = 0;
  pictureAsset = null;
  pictureDest = null;
  picturePaneDest = null;
  if (picture && !state.shots) picture.hidden = true;
  if (!state.segments.length) return;
  const wanted = state.segments[0].clip_id;
  if (mediaClip === null) setClip(wanted);
}

/* -- the transport keymap -------------------------------------------------
 *
 * F7: for a tool whose whole argument is that seeing an edit costs no
 * render, there was no way to scrub, step a frame, or jump seam to seam from
 * the keyboard, and the one binding that did exist (Space) let a focused
 * <select> both open its own dropdown and toggle playback on the same press.
 */

/* Widened from the original guard (tagName INPUT/TEXTAREA only), which is
 * exactly the gap F7 measured: a focused <select> is neither, so Space fell
 * through to the transport underneath it. A contenteditable element is on
 * the same list for the same reason nothing here is HTML-specific — a caret
 * living anywhere editable means a keypress is typing, not transport. */
function isTypingTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/* One frame, in seconds. `shots_rate` and not `timebase`: for a video
 * project the two agree, but an audio-only project's `timebase` is
 * milliseconds (ops.py `_rate` — CLAUDE.md documents this exact trap for the
 * picture lane, and stepping "one frame" by it would move 1ms at a time).
 * `shots_rate` is `_export_fps`'s own contract — "the picture's rate if
 * there is picture, else a sane default" — so it is always a real frame
 * rate. The `|| 30` only covers a keypress that lands before any view has.
 */
function frameSeconds() {
  const v = view();
  const rate = (v && v.shots_rate) || 30;
  return 1 / rate;
}

/* The nearest seam strictly before/after `t`, or the timeline's own edge
 * when there is none that way. `seams` is ops.py `_seams` — cut boundaries
 * named by the words either side — and this picks one rather than deriving
 * anything: `timeline_time` is the only field read. */
function seamStep(t, dir) {
  const v = view();
  const seams = (v && v.seams) || [];
  const times = seams.map((s) => s.timeline_time);
  if (dir < 0) {
    const before = times.filter((x) => x < t - 1e-6);
    return before.length ? Math.max(...before) : 0;
  }
  const after = times.filter((x) => x > t + 1e-6);
  return after.length ? Math.min(...after) : v ? v.timeline_duration : t;
}

/* L and K are directional, not a flip: pressing L while already playing (or
 * K while already paused) must be a no-op, which is why these are not just
 * `toggle()` called conditionally from two different keys. */
function playMedia() {
  if (!media || !media.paused) return;
  ensureVisualizer();
  media.play().catch((err) => ctx.emit("toast", err.message));
}

function pauseMedia() {
  if (media) media.pause();
}

export function init(passedCtx) {
  ctx = passedCtx;
  media = $("media");
  frame = $("frame");
  frameNote = $("frame-note");
  visualizer = $("visualizer");
  vizCtx = visualizer.getContext("2d");
  picture = $("picture");
  pictureVideo = $("picture-video");
  picturePane = $("picture-pane");
  pictureFill = $("picture-fill");
  mediaFill = $("media-fill");
  pictureStill = $("picture-still");
  pictureNote = $("picture-note");
  insetLayer = $("inset-layer");
  overlayLayer = $("overlay-layer");
  captionLayer = $("caption-layer");
  captionLine = $("caption-line");

  media.addEventListener("loadedmetadata", () => {
    if (pendingSeek !== null) {
      media.currentTime = pendingSeek;
      pendingSeek = null;
    }
  });

  pictureVideo.addEventListener("loadedmetadata", () => {
    if (pendingPictureSeek !== null) {
      pictureVideo.currentTime = pendingPictureSeek;
      pendingPictureSeek = null;
    }
  });

  // The pane has its own pending seek, because it loads independently of the
  // upper half and can arrive after `paintPicture` has stopped asking.
  picturePane.addEventListener("loadedmetadata", () => {
    if (pendingPaneSeek !== null) {
      picturePane.currentTime = pendingPaneSeek;
      pendingPaneSeek = null;
    }
  });

  pictureFill.addEventListener("loadedmetadata", () => {
    if (pendingFillSeek !== null) {
      pictureFill.currentTime = pendingFillSeek;
      pendingFillSeek = null;
    }
  });

  pictureVideo.addEventListener("error", () => {
    if (pictureVideo.src && pictureAsset) diagnose(pictureAsset);
  });

  pictureStill.addEventListener("error", () => {
    if (pictureStill.src && pictureAsset) diagnose(pictureAsset);
  });

  media.addEventListener("error", () => {
    if (media.src) ctx.emit("toast", `Could not play ${mediaClip} — the browser refused this file.`);
  });

  $("play").addEventListener("click", () => toggle());

  // Drop the cached bar colour; the next frame re-reads it in the new theme.
  window.addEventListener("proofcut:theme", () => {
    barColour = null;
  });

  // The frame's size is the pane's, so it moves with the window and with any
  // pane the layout resizes. Observed rather than recomputed every tick: this
  // is a layout read, and the answer changes on resize and nothing else.
  new ResizeObserver(() => layoutFrame()).observe($("viewer"));
  layoutFrame();

  window.addEventListener("keydown", (event) => {
    if (isTypingTarget(event.target)) return;
    // The sheet is a native <dialog> and owns Escape itself; every binding
    // below would otherwise fire underneath it while it is open on top of
    // the transport it is documenting.
    if ($("shortcuts-sheet")?.open) return;

    if (event.code === "Space") {
      event.preventDefault();
      toggle();
      return;
    }

    const key = event.key;
    const lower = key.toLowerCase();

    // There is no redo op anywhere in ops/webui — only /api/undo — so both
    // chords bind to the same undo, matching the muscle-memory of "I undid
    // too far" rather than inventing a redo that doesn't exist server-side.
    // Computing or POSTing undo is not this file's job: it emits and leaves
    // the rest to whoever owns #undo, the same split 'playhead' already is.
    if ((event.metaKey || event.ctrlKey) && lower === "z") {
      event.preventDefault();
      ctx.emit("shortcut-undo");
      return;
    }
    if (key === "?") {
      // `.key` and not `.code`, so this reaches Shift+/ on every layout, not
      // just the US one `.code` would assume.
      event.preventDefault();
      ctx.emit("shortcut-help");
      return;
    }

    if (lower === "j") {
      // Pause + step back 1s per press, relying on the OS's own key-repeat
      // for a shuttle feel — NOT continuous reverse playback. HTML5 <video>
      // has no reliably-supported negative playbackRate, and the seam loop
      // above is built around monotonic-forward playback throughout.
      event.preventDefault();
      pauseMedia();
      seek(now() - 1);
      return;
    }
    if (lower === "k") {
      event.preventDefault();
      pauseMedia();
      return;
    }
    if (lower === "l") {
      event.preventDefault();
      playMedia();
      return;
    }

    if (key === "ArrowLeft" || key === "ArrowRight") {
      event.preventDefault();
      pauseMedia();
      const step = event.shiftKey ? 1 : frameSeconds();
      seek(now() + (key === "ArrowLeft" ? -step : step));
      return;
    }
    if (key === "Home") {
      event.preventDefault();
      seek(0);
      return;
    }
    if (key === "End") {
      // The same epsilon app.js's own load() seeks the far end to, so
      // landing here and landing from a fresh load read as the same place.
      event.preventDefault();
      const v = view();
      seek((v ? v.timeline_duration : 0) - 0.01);
      return;
    }
    if (key === "[" || key === "]") {
      event.preventDefault();
      seek(seamStep(now(), key === "[" ? -1 : 1));
      return;
    }
  });

  requestAnimationFrame(tick);
}
