/* safezones.js — the safe-zone guide over the preview frame.
 *
 * CLAUDE.md's rule for the whole window: it draws and it plays, it never
 * decides. So nothing here works out where a platform's buttons sit —
 * `GET /api/safe-zones` (`ops.safe_zone_view`) returns each zone as rectangles
 * in canvas pixels, computed by `graphics.safe_zone_rects`, the same function
 * `card_safe_zones` measures a card's ink with. This module scales them into
 * `#safezone-layer` and stops there: the guide on screen is the region the
 * report reads, by construction.
 *
 * **A guide, not a check.** It is report-only, like everything else about
 * safe zones (graphics.SAFE_ZONES), and it is never in the render — the
 * toggle's tooltip says so, since that is the one thing worth saying on the
 * control itself.
 *
 * Offered only where the server says `vertical`. The reference numbers are a
 * 1080x1920 short-form frame, so on a 2.35:1 film the same rectangles would
 * describe a band no platform draws; both controls stay hidden there.
 *
 * Owns #safezone-toggle, #safezone-wrap, #safezone-select and
 * #safezone-layer, and touches nothing else. The on/off and platform choice
 * are a per-viewer convenience, kept in localStorage with every access
 * guarded — the page renders the same without it.
 */

import { $, el } from "./dom.js";

const KEY = "proofcut:safezone";

let ctx = null;
let data = null; // the last /api/safe-zones payload, or null
let on = false;
let chosen = null; // the platform name the viewer picked, if any
let seq = 0; // drops an in-flight fetch that a newer load has overtaken

function readPrefs() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(KEY) || "null");
    if (saved && typeof saved === "object") {
      on = saved.on === true;
      chosen = typeof saved.zone === "string" ? saved.zone : null;
    }
  } catch {
    // Storage can be absent or throw (a private window); the guide just
    // starts off, which is the default anyway.
  }
}

function writePrefs() {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ on, zone: chosen }));
  } catch {
    // Not persisted; the choice still holds for this page's lifetime.
  }
}

/** The zone to draw: the viewer's pick if it still exists, else the first. */
function currentZone() {
  if (!data) return null;
  const names = Object.keys(data.zones);
  if (!names.length) return null;
  return names.includes(chosen) ? chosen : names.includes("worst-case") ? "worst-case" : names[0];
}

function pct(value, whole) {
  return `${(100 * value) / whole}%`;
}

function box(rect, canvas, cls, label) {
  const [x0, y0, x1, y1] = rect;
  const node = el("div", `sz-rect ${cls}`);
  node.dataset.zone = label;
  node.style.left = pct(x0, canvas[0]);
  node.style.top = pct(y0, canvas[1]);
  node.style.width = pct(x1 - x0, canvas[0]);
  node.style.height = pct(y1 - y0, canvas[1]);
  return node;
}

function draw() {
  const toggle = $("safezone-toggle");
  const wrap = $("safezone-wrap");
  const layer = $("safezone-layer");
  const usable = !!data && data.vertical && Object.keys(data.zones).length > 0;
  toggle.hidden = !usable;
  wrap.hidden = !usable || !on;
  toggle.classList.toggle("on", usable && on);
  toggle.setAttribute("aria-pressed", usable && on ? "true" : "false");
  layer.replaceChildren();
  if (!usable || !on) {
    layer.hidden = true;
    return;
  }

  const select = $("safezone-select");
  const name = currentZone();
  // Rebuilt only when the set of names changed: replacing the options under an
  // open dropdown would close it, and a `project-changed` is not a reason to.
  const names = Object.keys(data.zones);
  const built = Array.from(select.options, (o) => o.value);
  if (built.join("\n") !== names.join("\n")) {
    select.replaceChildren(
      ...names.map((n) => {
        const opt = el("option", "", data.zones[n].source === "pack" ? `${n} (pack)` : n);
        opt.value = n;
        return opt;
      }),
    );
  }
  select.value = name;

  const zone = data.zones[name];
  layer.append(box(zone.band, data.canvas, "sz-band", name));
  if (zone.rail) layer.append(box(zone.rail, data.canvas, "sz-rail", name));
  layer.hidden = false;
}

/** Called by app.js on every load: the zones can change with a pack or the
 * canvas, neither of which announces itself except through `project-changed`. */
export async function update() {
  const mine = ++seq;
  let next = null;
  try {
    next = await ctx.api("/api/safe-zones");
  } catch {
    // A project the read cannot answer for simply has no guide.
  }
  if (mine !== seq) return;
  data = next;
  draw();
}

export function init(passedCtx) {
  ctx = passedCtx;
  readPrefs();
  $("safezone-toggle").addEventListener("click", () => {
    on = !on;
    writePrefs();
    draw();
  });
  $("safezone-select").addEventListener("change", (event) => {
    chosen = event.target.value;
    writePrefs();
    draw();
  });
}
