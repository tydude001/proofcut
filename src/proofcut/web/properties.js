/**
 * properties.js — the properties pane: `GET /api/properties` (`ops.properties`)
 * drawn, and nothing this file works out for itself.
 *
 * `ops.properties`' own docstring is the contract this file leans on:
 * "composes only — every field here is another read-only op's own return,
 * assembled rather than re-derived". So this pane's job is display, not
 * interpretation — the generic `rows()` renderer below shows every field a
 * bundle carries, under a section heading naming which op it came from,
 * rather than this file hand-picking a subset and silently dropping the
 * rest whenever the backend adds a field.
 *
 * What is inspected — the pane-module interface's `state` (the `/api/view`
 * payload) says which clip is *loaded*, not which one is *inspected*, and
 * those are deliberately different: browsing the assets pane's catalogue
 * must not reload the transcript/timeline/preview out from under a person
 * doing something else. So `inspected` is this file's own state, set only by
 * the two bus events assets.js and timeline.js raise:
 *
 *   'inspect-asset'  {kind: "clip", clipId} | {kind: "card", name} | null
 *                    — raised by assets.js on a row click.
 *   'inspect-word'   {clipId, wordIndex} — raised by timeline.js when a
 *                    lane gesture resolves to a word (a cue-placement drag's
 *                    anchor, or a click on a V2 shot / CC cue block). Not
 *                    from transcript.js — this file does not own that
 *                    module and it raises no such event today; a
 *                    transcript-driven word inspector is future work, not a
 *                    silent gap (see the report).
 *
 * With nothing inspected, the pane still shows the three project-wide
 * bundles `ops.properties` always returns (`status`, `canvas`,
 * `caption_style`) — a properties pane that goes blank the moment nothing
 * is selected would hide state a person opened this pane to check in the
 * first place.
 */

import { $, el, secs } from "./dom.js";

let ctx = null;
let lastView = null;
let inspected = null; // {kind: "clip"|"word", clipId, wordIndex?} | {kind: "card", name} | null
let requestSeq = 0; // guards against an in-flight fetch from an earlier
// `inspected` landing after a later one — a fast double-click on two
// different assets rows must not have the first response overwrite the
// second's

/** The leaf types a flat line can render without hiding structure. `null`
 * counts: it has a rendering (`–`) that claims nothing about shape. */
function isPrimitive(value) {
  return value === null || value === undefined || ["boolean", "number", "string"].includes(typeof value);
}

/** One leaf, formatted — the rules `fmtValue` applies at the top level, so a
 * boolean inside a list reads `yes` there too and not `true`. */
function fmtPrimitive(value) {
  if (value === null || value === undefined) return "–";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  return String(value);
}

/** A field's value as one line.
 *
 * A list of primitives and a flat object of primitives each get a plain
 * rendering, because `["vo","cold-open",…]` and `{"applied":false}` are the
 * inspector printing its own transport rather than the fact. **Anything
 * nested keeps `JSON.stringify`**: this pane is an inspector, and flattening
 * a shape it cannot show would be lying about the data, which is worse than
 * being ugly. */
function fmtValue(key, value) {
  if (key === "playable" && value && typeof value === "object") {
    return `${value.playable ? "yes" : "no"}${value.reason ? ` — ${value.reason}` : ""}`;
  }
  if (isPrimitive(value)) return fmtPrimitive(value);
  if (Array.isArray(value)) {
    if (!value.length) return "none";
    return value.every(isPrimitive) ? value.map(fmtPrimitive).join(", ") : JSON.stringify(value);
  }
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length) return "none";
    return entries.every(([, v]) => isPrimitive(v))
      ? entries.map(([k, v]) => `${k}: ${fmtPrimitive(v)}`).join(" · ")
      : JSON.stringify(value);
  }
  return String(value);
}

/** Every field of a flat-ish bundle as a two-column table — `.rows`, the
 * same class the transcript/agent selection echoes use (app.css § Shared).
 * Complete rather than curated: a field this file does not special-case
 * still shows, JSON-compact, rather than silently disappearing. */
function rows(obj, omit = []) {
  const table = el("table", "rows");
  const body = document.createElement("tbody");
  for (const [key, value] of Object.entries(obj)) {
    if (omit.includes(key)) continue;
    const tr = document.createElement("tr");
    tr.append(el("td", null, key), el("td", null, fmtValue(key, value)));
    body.append(tr);
  }
  table.append(body);
  return table;
}

function section(title, contentNode) {
  const box = el("div", "prop-section");
  box.append(el("h3", null, title));
  box.append(contentNode);
  return box;
}

/** The word-range echo every word-indexed tool in this codebase uses —
 * three words either side, the target bracketed (CLAUDE.md) — built from a
 * cue/context entry's own `context_before`/`context_after` (`ops._context`)
 * rather than re-deriving it from the transcript this pane does not have. */
function wordQuote(entry) {
  const before = (entry.context_before || []).map((w) => w.text);
  const after = (entry.context_after || []).map((w) => w.text);
  const box = el("div", "quote");
  const ctxBefore = el("span", "ctx", before.join(" ") + (before.length ? " " : ""));
  const hit = el("span", "hit", entry.text);
  const ctxAfter = el("span", "ctx", (after.length ? " " : "") + after.join(" "));
  box.append(ctxBefore, hit, ctxAfter);
  return box;
}

function cueLine(cue) {
  const wrap = el("div");
  // An event cue has no words to quote (RECUT.md step 2): it says its event.
  if (cue.event) {
    wrap.append(
      el(
        "div",
        "asset-meta",
        `event ${cue.event}${cue.at != null ? ` · ${secs(cue.at)}` : ""} → ${cue.asset}` +
          (cue.event_error ? ` — ${cue.event_error}` : ""),
      ),
    );
    return wrap;
  }
  wrap.append(
    el(
      "div",
      "asset-meta",
      `word ${cue.word_index} · ${secs(cue.start)}–${secs(cue.end)} → ${cue.asset}` +
        (cue.src_start !== null && cue.src_start !== undefined ? ` @ ${secs(cue.src_start)}` : ""),
    ),
  );
  wrap.append(wordQuote(cue));
  return wrap;
}

function renderBundle(result) {
  const body = $("properties-body");
  if (!body) return;
  body.textContent = "";

  body.append(section("status", rows(result.status)));
  body.append(section("canvas", rows(result.canvas)));
  body.append(section("caption style", rows(result.caption_style)));

  if (Object.prototype.hasOwnProperty.call(result, "clip") && result.clip) {
    body.append(section(`clip · ${result.clip.clip_id}`, rows(result.clip)));
  }
  if (Object.prototype.hasOwnProperty.call(result, "reframe") && result.reframe) {
    body.append(section("reframe", rows(result.reframe)));
  }
  if (Array.isArray(result.cues)) {
    const list = el("div");
    if (!result.cues.length) list.append(el("div", "hint", "no cues on this clip"));
    for (const cue of result.cues) list.append(cueLine(cue));
    body.append(section(`cues · ${result.cues.length}`, list));
  }
  if (Object.prototype.hasOwnProperty.call(result, "cue")) {
    if (result.cue) {
      body.append(section("selected word's cue", cueLine(result.cue)));
    } else if (result.context) {
      body.append(section("selected word — no cue here yet", wordQuote(result.context)));
    }
  }
}

/** The card branch: `ops.properties` has no `clip_id` for a card (a card is
 * not a clip and never will be — webui.py's own note on `_send_asset`), so
 * this fetches the project-wide bundle for `status`/`canvas`/`caption_style`
 * and a second, already-existing read op (`/api/assets`, the same one
 * assets.js just drew) for the card's own record — composing two read ops
 * rather than inventing a third route for one field set. */
async function renderCard(name) {
  const seq = ++requestSeq;
  let result;
  let assets;
  try {
    [result, assets] = await Promise.all([ctx.api("/api/properties"), ctx.api("/api/assets")]);
  } catch (err) {
    if (seq === requestSeq) {
      const body = $("properties-body");
      if (body) {
        body.textContent = "";
        body.append(el("div", "warn bad", err.message));
      }
    }
    return;
  }
  if (seq !== requestSeq) return; // superseded by a later inspection
  renderBundle(result);
  const card = (assets.cards || []).find((c) => c.name === name);
  const body = $("properties-body");
  if (card) {
    body.append(section(`card · ${card.asset}`, rows(card)));
  } else {
    body.append(section(`card · card:${name}`, el("div", "hint", "no longer in the project")));
  }
}

async function renderInspected() {
  if (!ctx) return;
  if (inspected && inspected.kind === "card") {
    await renderCard(inspected.name);
    return;
  }
  const params = new URLSearchParams();
  if (inspected) {
    params.set("clip_id", inspected.clipId);
    if (inspected.kind === "word") params.set("word_index", String(inspected.wordIndex));
  }
  const query = params.toString();
  const seq = ++requestSeq;
  let result;
  try {
    result = await ctx.api(`/api/properties${query ? `?${query}` : ""}`);
  } catch (err) {
    if (seq === requestSeq) {
      const body = $("properties-body");
      if (body) {
        body.textContent = "";
        body.append(el("div", "warn bad", err.message));
      }
    }
    return;
  }
  if (seq !== requestSeq) return;
  renderBundle(result);
}

function updateClearButton() {
  const btn = $("properties-clear");
  if (btn) btn.hidden = !inspected;
}

export function init(passedCtx) {
  ctx = passedCtx;

  const clearBtn = $("properties-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      inspected = null;
      updateClearButton();
      renderInspected();
    });
  }

  ctx.on("inspect-asset", (payload) => {
    inspected = payload;
    updateClearButton();
    renderInspected();
  });

  // Raised by timeline.js — a lane gesture that resolved to a word. Word
  // inspection always wins over whatever asset row was last clicked; it is
  // the more specific selection.
  ctx.on("inspect-word", ({ clipId, wordIndex }) => {
    inspected = { kind: "word", clipId, wordIndex };
    updateClearButton();
    renderInspected();
  });
}

export function update(state) {
  lastView = state;
  renderInspected();
}
