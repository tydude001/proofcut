/* dom.js — tiny DOM helpers and time formatting shared by every module.
 *
 * Nothing here talks to the network or to `ops` — see api.js for that. Kept
 * separate because every module needs `el`/`$`/`fmt` and only some of them
 * need `api`.
 */

/** `document.getElementById`, shortened — every id below is unique on the page. */
export function $(id) {
  return document.getElementById(id);
}

/** Build one element: tag, an optional class string, optional text content. */
export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** `m:ss.s` — the clock format used everywhere timeline seconds are drawn. */
export function fmt(t) {
  if (t === null || t === undefined || Number.isNaN(t)) return "–";
  const sign = t < 0 ? "-" : "";
  t = Math.abs(t);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${sign}${m}:${s.toFixed(1).padStart(4, "0")}`;
}

/**
 * How far a job's `progress` event says it has got, as `42%`, or "" when the
 * op could not say. Always a percentage: the total is seconds for one op and
 * frames or windows for another, and JSON cannot tell `4.0` from `4`. A job's
 * `progress` events are the reports `proofcut.progress` makes — the same the
 * MCP server sends a client — so this is the one place they are worded.
 */
export function progressText(data) {
  if (!data || typeof data.progress !== "number") return "";
  const { progress, total } = data;
  if (typeof total !== "number" || !(total > 0)) return "";
  return `${Math.min(100, Math.floor((100 * progress) / total))}%`;
}

/** `12.345s` — the source-second format quoted in tooltips and echoes. */
export function secs(t) {
  return t === null || t === undefined ? "–" : `${t.toFixed(3)}s`;
}

/**
 * Clamps a floating element's desired top-left corner so its FULL box (using
 * `width`/`height` — i.e. the far edge too, not just the corner) stays inside
 * `[minX, maxX] x [minY, maxY]`. Returns `{left, top}`; the caller still owns
 * applying it to `style.left`/`style.top`.
 *
 * Takes bounds, not a container element, because the two toolbars that use
 * this are not in the same coordinate space. timeline.js's cue toolbar sits
 * in a content-relative space that already has `#track-lanes`'s own
 * `scrollLeft` folded into the raw left/top at drag time (see
 * `refreshCueToolbar`'s own long comment there) — its bounds are
 * `[lanes.scrollLeft, lanes.scrollLeft + lanes.clientWidth]`, not
 * `[0, clientWidth]`. transcript.js's selection toolbar has no such fold-in —
 * it places itself off a word's plain `offsetLeft`/`offsetTop` — so its
 * bounds are `[0, container.clientWidth] x [0, container.clientHeight]`. A
 * version of this helper that derived its own bounds from
 * `container.clientWidth` would be correct for exactly one of the two
 * callers and silently wrong for the other, so every caller computes its own
 * minX/maxX/minY/maxY first rather than handing this a container.
 *
 * Unclamped, this is exactly how both toolbars broke: timeline.js's cue
 * toolbar rendered past both `#track-lanes`'s own `overflow-y: hidden` clip
 * and the browser viewport at zero opacity of "found" (no error, nothing a
 * person could see or click — see `refreshCueToolbar`'s comment). The
 * transcript's own sibling toolbar carried the identical, never-fixed bug —
 * measured live at +169px past `#transcript`'s right edge, with
 * `#transcript` gaining a sideways scrollbar and Cut/Keep only sitting off it
 * (F4 in the 2026-08-17 browser pass). A duplicated fix is how the first one
 * failed to reach here the first time, which is the whole reason this is one
 * shared helper rather than a second inline clamp.
 *
 * Unhide the element before measuring its `width`/`height`: a `hidden`
 * element reports a zero-size rect, which would clamp everything to
 * `(minX, minY)`.
 */
export function clampFloating(left, top, width, height, minX, maxX, minY, maxY) {
  const maxLeft = Math.max(minX, maxX - width);
  const maxTop = Math.max(minY, maxY - height);
  return {
    left: Math.min(Math.max(left, minX), maxLeft),
    top: Math.min(Math.max(top, minY), maxTop),
  };
}

/**
 * Trailing-edge debounce: returns a wrapper that calls `fn` with its most
 * recent arguments `ms` after the last call, cancelling any pending call
 * each time it is invoked again first. Step 04's session-save heartbeat
 * (app.js) is the first caller — no debounce/throttle helper existed in
 * `web/` before it (grepped, confirmed absent).
 */
export function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
