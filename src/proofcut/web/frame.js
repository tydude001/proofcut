/**
 * frame.js — the Frame view: `GET /api/reframe/coverage` drawn as three
 * coverage chips, the sheet/detect job triggers and their live per-job SSE
 * progress, and one row per WINDOW the sheet drew — never a placement, and
 * never a hand-drawn canvas rect (CLAUDE.md § Per-shot framing, read in
 * full before touching this file):
 *
 *   - A sheet row is a window, not a placement. `row.windows` is the
 *     PLACEMENT's total window count (shown as context — "window n of
 *     windows"); `lastSheet.count` is windows, `lastSheet.placements` is
 *     placements. Never conflate the two.
 *   - The tile a row shows is the sheet's OWN drawn-on PNG, fetched through
 *     `/api/reframe/tile/<basename>` and rendered as a plain `<img>`. This
 *     file never opens a `<canvas>` and paints a rect over a `<video>`
 *     frame itself — a wrong window reads as framing in motion on a watch,
 *     invisibly, which is the whole reason the sheet exists as the review
 *     instrument.
 *   - `worst_offset` is drawn BESIDE `multi_face`, never after it alone —
 *     a two-face frame's worst offset sits between both faces, where
 *     nobody is.
 *   - Coverage chips read `stale_seconds`/`steps`, never `default_seconds`
 *     (the centre crop doing what it always did — not a defect).
 *   - A split row draws `pane_overlap` on the ROW, reported, never enforced.
 *   - Approve writes nothing at all — no fetch, purely local UI state.
 *   - Detect proposes; `apply` is never sent from here (the job and the
 *     endpoint both refuse it independently — see the backend report).
 *
 * The `finish.js` two-export module contract exactly: `init(ctx)` wires
 * listeners once, `update(state)` marks coverage owed. The sheet/detect
 * jobs are NOT re-triggered by update() — they stay whatever they were,
 * keyed on their own buttons, exactly like finish.js's render job.
 *
 * **Coverage is fetched when this view is looked at, never merely when the
 * project reloads.** `reframe_coverage` decodes every placed clip for its
 * scene-cut scan — 5.5s measured on the film, uncached, per call — and
 * `update()` runs on every `project-changed`, so re-fetching there made
 * every cut taken in Edit mode pay for a scan of a view nobody had open
 * (twice per page load, once per mutation). That is the exact cost
 * `finish_report`'s framing is opt-in to avoid (CLAUDE.md), and this pane
 * had reintroduced it. `update()` now sets `coverageStale`; the fetch runs
 * if Frame is already on screen, and otherwise when `app.js` emits `mode`
 * for it. The two rules that outrank the saving: the scan still says
 * "scanning for cuts…" while it runs, and a project with no placements
 * says so rather than drawing a blank chip.
 */

import { $, el, secs, clampFloating, progressText } from "./dom.js";

let ctx = null;

let lastSheet = null; // the full "done" event payload from the reframe-sheet job
let lastDetect = null; // the full "done" event payload from the reframe-detect job
let sheetBusy = false;
let detectBusy = false;
let lastState = null; // the last /api/view payload, read only for `shots`
// Bumped by every update(), so a scan that started before a reload can tell
// its answer is about a project that has since changed.
let coverageGeneration = 0;
let coverageStale = true; // is a coverage fetch owed? set by update(), paid
// for when this view is on screen — the scan is 5.5s of decoding per call,
// so it rides being *looked at* rather than every project reload.

// Ephemeral, both of them — reset whenever update() runs (a fresh project
// state), never persisted anywhere. Approve is pure bookkeeping for one
// sitting (STUDIO's "report-only is the standing precedent," restated for
// the reviewer's own use); reframedKeys tracks which windows this session
// wrote a rect for so their tile strip can say it is showing a stale
// picture until the sheet is rebuilt, cleared only when a fresh sheet
// actually lands.
let approvedKeys = new Set();
let reframedKeys = new Set();

let openPanel = null; // the currently-open Re-frame… panel element, if any

/* Which shot's windows the detail column is showing — a `row.shot` index, or
   null before a sheet exists. Held here rather than read back off the DOM so
   a re-render (a new sheet, a detect result, an approve toggle) keeps the
   selection instead of silently jumping to the first shot. It is validated
   against the current sheet on every render, so a rebuilt sheet with fewer
   shots falls back to the first rather than showing an empty column. */
let selectedShot = null;

const STEP_FINE = 4;
const STEP_COARSE = 32;

/* -- small local helpers ---------------------------------------------- */

function basename(path) {
  return String(path).split("/").pop();
}

function tileUrl(png) {
  // §B: a `png` field is an absolute filesystem path — never URL-encode it
  // as given, always strip to its basename first. The confinement check on
  // the server refuses anything else (a name that is not its own basename).
  return `/api/reframe/tile/${encodeURIComponent(basename(png))}`;
}

function windowAddress(row) {
  // The address `reframe --src-start` takes: the window's own start when
  // known, else the stretch's src_start (the head-window case) — CLAUDE.md's
  // rule for `window`, restated once here since every caller needs it.
  return row.window !== null && row.window !== undefined ? row.window : row.src_start;
}

function rowKey(row) {
  return `${row.asset}@${windowAddress(row)}`;
}

function parseRect(text) {
  if (!text) return { x: 0, y: 0, w: 0, h: 0 };
  const [x, y, w, h] = text.split(",").map((part) => Number(part));
  return { x: x || 0, y: y || 0, w: w || 0, h: h || 0 };
}

function rectText(rect) {
  // `ops._parse_rect` refuses anything but a plain integer X,Y,W,H — never
  // send the fractional values a nudge can accumulate.
  return [rect.x, rect.y, rect.w, rect.h].map((v) => String(Math.round(v))).join(",");
}

/** One coverage chip. `state` is `"warn"`, `"ok"` or null.
 *
 * The `"ok"` state was added 2026-08-24 and it is this file's own lesson
 * applied: a chip that draws EMPTY while the scan runs reads as "nothing to
 * report", which is why `refreshCoverage` below says "scanning for cuts…"
 * out loud. A chip that draws a flat, uncoloured "no stale framing" beside
 * two other flat chips has a milder version of the same problem — the
 * all-clear looks exactly like a chip nobody has filled in. Green says it.
 *
 * Only the two chips that can WARN can also go green; `cuts framed` is a
 * ratio, informational in both directions, and takes neither
 * (app.js § .chip.ok makes the same split in the truth strip). */
function setChip(node, text, state, title) {
  if (!node) return;
  node.textContent = text;
  node.classList.toggle("warn", state === "warn");
  node.classList.toggle("ok", state === "ok");
  if (title) node.title = title;
  else node.removeAttribute("title");
}

/** One decimal for a chip or a header, the full value for the tooltip.
 *
 * `secs()` renders three (`11.719s`, `20.395s`), which is the right precision
 * for an address someone might type back into `reframe --src-start` and the
 * wrong one for a quantity someone is reading — a coverage chip saying
 * `11.719s stale` spends three characters on a number nobody acts on at that
 * resolution. Display only: nothing downstream parses these, and the frame-of-
 * tolerance arithmetic in the coverage math never sees them. */
function coarse(t) {
  return t === null || t === undefined ? "–" : `${t.toFixed(1)}s`;
}

function setButtonBusy(btn, busyLabel, idleLabel) {
  if (!btn) return;
  btn.disabled = Boolean(busyLabel);
  btn.textContent = busyLabel || idleLabel;
}

/* -- coverage chips ------------------------------------------------------ */

/** Is the Frame view actually on screen? `app.js`'s `setMode` owns this
 * element's `hidden`, and this pane only ever reads it. */
function frameVisible() {
  const view = $("frame-view");
  return Boolean(view) && !view.hidden;
}

/** Does this project have anything a crop window could apply to?
 *
 * `ops.reframe_coverage` refuses outright when it has no footage
 * placements — correct for the CLI, but as a fetch it is a 400 and a red
 * toast on every load of an audio-only project, where having no picture is
 * the normal state rather than a fault. `/api/view` already answers this
 * (`shots`), so the question is not asked rather than asked and refused.
 * A `shots_error` means the projection itself refused — that is a real
 * finding and coverage is still worth asking, so it is NOT treated as
 * "nothing here". */
function hasPlacements(state) {
  if (!state) return false;
  if (state.shots_error) return true;
  return Boolean(state.shots && state.shots.length);
}

async function refreshCoverage() {
  if (!ctx) return;
  coverageStale = false;
  const generation = coverageGeneration;
  if (!hasPlacements(lastState)) {
    // Not a warning and not a blank chip — both would read as a verdict on
    // framing that nobody measured.
    setChip($("frame-chip-stale"), "no footage placements to frame", null);
    $("frame-chip-steps") && ($("frame-chip-steps").textContent = "");
    $("frame-chip-cuts") && ($("frame-chip-cuts").textContent = "");
    return;
  }
  // Say so while it runs. `reframe_coverage` decodes placed footage for a
  // scene-cut scan and took ~4s on the real film, during which these three
  // chips sat empty — and an empty chip where a warning would go reads as
  // "nothing to report", which is the one thing this view must never say by
  // accident. Measured in a browser: chips blank for 4s, then correct.
  setChip($("frame-chip-stale"), "scanning for cuts…", null);
  $("frame-chip-steps") && ($("frame-chip-steps").textContent = "");
  $("frame-chip-cuts") && ($("frame-chip-cuts").textContent = "");
  let coverage;
  try {
    coverage = await ctx.api("/api/reframe/coverage");
  } catch (err) {
    setChip($("frame-chip-stale"), "coverage unavailable", "warn");
    $("frame-chip-steps") && ($("frame-chip-steps").textContent = "");
    $("frame-chip-cuts") && ($("frame-chip-cuts").textContent = "");
    ctx.emit("toast", err.message);
    return;
  }
  renderCoverage(coverage);
  // The truth strip's framing chip says "not scanned" because
  // `finish_report` does not pay for this scan (CLAUDE.md § "Needs no face
  // detector" is not "cheap"). Right after this view HAS scanned, that reads
  // as a contradiction one bar above the answer — so hand the answer up, for
  // this revision only: a scan that outlived a reload is about a project
  // that no longer exists, and is dropped. HISTORY.md § The refusing preset
  // card.
  if (generation === coverageGeneration) ctx.emit("coverage", coverage);
}

function renderCoverage(coverage) {
  const staleOn = coverage.stale_seconds > 0;
  setChip(
    $("frame-chip-stale"),
    staleOn ? `${coarse(coverage.stale_seconds)} stale` : "no stale framing",
    staleOn ? "warn" : "ok",
    staleOn
      ? `${coverage.stale_seconds}s of framing held across a cut, over ${coverage.stale_stretches} stretch${coverage.stale_stretches === 1 ? "" : "es"}`
      : null,
  );
  const steps = coverage.steps.length;
  setChip(
    $("frame-chip-steps"),
    steps ? `${steps} unexplained step${steps === 1 ? "" : "s"}` : "no step gaps",
    steps > 0 ? "warn" : "ok",
  );
  // Informational only — never .warn and never .ok either. A ratio is not a
  // verdict: `0/0 cuts framed` is a project with no cuts, and `3/9` is not a
  // fault, it is nine cuts of which six are inside one continuous framing.
  // default_seconds (the centre crop doing what it always did) is
  // deliberately not read anywhere here.
  setChip($("frame-chip-cuts"), `${coverage.cuts_framed}/${coverage.cuts} cuts framed`, null);
}

/* -- the sheet job --------------------------------------------------------- */

async function onBuildSheetClick() {
  if (!ctx || sheetBusy) return;
  try {
    await ctx.api("/api/reframe/sheet", {});
  } catch (err) {
    // A 409 (already generating) or a 400 (bad project) both land here
    // before any "running" event would — the button was never flipped busy
    // by this call, so nothing to unwind.
    ctx.emit("toast", err.message);
  }
}

function onSheetEvent(data) {
  if (!data || typeof data !== "object") return;
  const btn = $("frame-build-sheet");
  if (data.status === "running") {
    sheetBusy = true;
    setButtonBusy(btn, "generating sheet…", "Build sheet");
  } else if (data.status === "progress") {
    const done = progressText(data);
    if (done) setButtonBusy(btn, `generating sheet… ${done}`, "Build sheet");
  } else if (data.status === "done") {
    sheetBusy = false;
    setButtonBusy(btn, null, "Build sheet");
    lastSheet = data;
    reframedKeys = new Set(); // a fresh sheet has fresh tiles — no row is stale anymore
    closeReframePanel();
    renderRows();
  } else if (data.status === "error") {
    sheetBusy = false;
    setButtonBusy(btn, null, "Build sheet");
    ctx.emit("toast", data.error || "reframe sheet failed");
  }
}

/* -- the detect job --------------------------------------------------------
 *
 * `apply` is never a control in this UI at all — the job hard-codes it off
 * and the endpoint refuses the key outright, so there is nothing here that
 * could send it even by accident.
 */

async function onDetectClick() {
  if (!ctx || detectBusy) return;
  setDetectError("");
  try {
    await ctx.api("/api/reframe/detect", {});
  } catch (err) {
    ctx.emit("toast", err.message);
  }
}

function setDetectError(message) {
  const node = $("frame-detect-error");
  if (!node) return;
  node.textContent = message || "";
  node.className = message ? "warn bad" : "";
}

function onDetectEvent(data) {
  if (!data || typeof data !== "object") return;
  const btn = $("frame-detect-gaps");
  if (data.status === "running") {
    detectBusy = true;
    setButtonBusy(btn, "detecting gaps…", "Detect gaps");
    setDetectError("");
  } else if (data.status === "progress") {
    const done = progressText(data);
    if (done) setButtonBusy(btn, `detecting gaps… ${done}`, "Detect gaps");
  } else if (data.status === "done") {
    detectBusy = false;
    setButtonBusy(btn, null, "Detect gaps");
    lastDetect = data;
    renderRows(); // re-join every drawn row's provenance chip — no new fetch
  } else if (data.status === "error") {
    detectBusy = false;
    setButtonBusy(btn, null, "Detect gaps");
    // Rendered inline, in this row, never as a toast and never left as a
    // silent spinner — this is exactly the PROOFCUT_FACE-absence case the
    // backend report names: with FaceError now EXPECTED, this branch is
    // what actually fires instead of the job hanging forever.
    setDetectError(data.error || "reframe detect failed");
  }
}

/* -- provenance join (display-only, never a decision) ---------------------- */

function findDetectEntry(row) {
  if (!lastDetect || !Array.isArray(lastDetect.windows)) return null;
  const at = windowAddress(row);
  return (
    lastDetect.windows.find(
      (entry) => entry.clip_id === row.asset && entry.src_start <= at && at <= entry.src_end,
    ) || null
  );
}

function provenanceChip(row) {
  const entry = findDetectEntry(row);
  if (!entry) return null;
  const text =
    entry.rect !== null
      ? `detector proposes: ${entry.rect}`
      : entry.refused
        ? `detector: refused (${entry.refused})`
        : null;
  if (!text) return null;
  return el("span", "frame-badge", text);
}

/* -- rows -------------------------------------------------------------- */

/** The sheet's rows grouped by shot, with the skipped placements merged in
 * at their own index.
 *
 * A sheet ROW is a window, not a placement (CLAUDE.md's own correction, and
 * `ops.reframe_sheet`'s), and the op emits one placement's rows contiguously
 * in source order. Until this existed that was the only grouping the view
 * had: a shot with four windows drew four cards with the same header, and
 * the film's 79 of them were one scroll with no way to reach a shot except
 * past every shot before it.
 *
 * **The gap in the shot numbers is answered, not left to be read as a
 * rendering fault.** Rows ran #0, #2, #3 on the film and #1 is a card:
 * `ops._sheet_placements` puts stills in its own `skipped` list precisely so
 * "nothing to check" and "not checked" stay different things, and each entry
 * carries the reason ("a still is never cropped"). Sorting both into one
 * list by index puts a skipped shot where its number would have been rather
 * than in a footnote — the copy is the op's, so a new skip reason arrives
 * here without an edit.
 */
function shotEntries() {
  if (!lastSheet || !Array.isArray(lastSheet.rows)) return [];
  const byShot = new Map();
  for (const row of lastSheet.rows) {
    if (!byShot.has(row.shot)) byShot.set(row.shot, []);
    byShot.get(row.shot).push(row);
  }
  const entries = [...byShot.entries()].map(([shot, rows]) => ({ kind: "shot", shot, rows }));
  for (const skip of Array.isArray(lastSheet.skipped) ? lastSheet.skipped : []) {
    entries.push({ kind: "skipped", shot: skip.index, skip });
  }
  entries.sort((a, b) => a.shot - b.shot);
  return entries;
}

/** The entry the detail column is showing, after validating the held
 * selection against the sheet actually in hand. A rebuilt sheet with fewer
 * shots, or one whose selected shot is now a skipped still, falls back to
 * the first selectable shot rather than leaving the column empty. */
function currentEntry() {
  const entries = shotEntries();
  if (!entries.length) return null;
  const held = entries.find((e) => e.shot === selectedShot && e.kind === "shot");
  if (held) return held;
  const first = entries.find((e) => e.kind === "shot") || null;
  selectedShot = first ? first.shot : null;
  return first;
}

function renderRows() {
  renderShotList();
  renderDetail();
}

function renderShotList() {
  const box = $("frame-shots");
  if (!box) return;
  box.textContent = "";
  const entries = shotEntries();
  if (!entries.length) {
    box.append(el("div", "hint", "no sheet yet"));
    return;
  }
  const active = currentEntry();
  for (const entry of entries) {
    if (entry.kind === "skipped") {
      const note = el("div", "frame-shot skipped");
      const top = el("div", "frame-shot-top");
      top.append(el("span", "frame-row-shot", `shot #${entry.shot}`));
      top.append(el("span", null, entry.skip.asset));
      note.append(top);
      note.append(el("div", "frame-shot-meta", entry.skip.why));
      box.append(note);
      continue;
    }
    const first = entry.rows[0];
    const last = entry.rows[entry.rows.length - 1];
    const btn = el("button", "frame-shot");
    btn.type = "button";
    // `aria-current` rather than `aria-selected`: this is "the one you are
    // looking at", not a selection you could have several of. The rail's own
    // tabs use aria-selected because they genuinely are a tablist.
    btn.setAttribute("aria-current", String(Boolean(active) && active.shot === entry.shot));
    const top = el("div", "frame-shot-top");
    top.append(el("span", "frame-row-shot", `shot #${entry.shot}`));
    top.append(el("span", null, first.asset));
    top.append(el("span", "spacer"));
    // Approval is per WINDOW, so a shot is only approved when all of its are
    // — an "approved" tick on a shot with one of four judged would be a lie
    // in exactly the direction that matters.
    if (entry.rows.every((row) => approvedKeys.has(rowKey(row)))) {
      top.append(el("span", "frame-shot-approved", "\u2713"));
    }
    btn.append(top);
    const span = `src ${coarse(first.src_start)}\u2013${coarse(last.src_start + last.duration)}`;
    const count = `${entry.rows.length} window${entry.rows.length === 1 ? "" : "s"}`;
    btn.append(el("div", "frame-shot-meta", `${span} \u00b7 ${count}`));
    btn.addEventListener("click", () => {
      if (selectedShot === entry.shot) return;
      selectedShot = entry.shot;
      renderRows();
    });
    box.append(btn);
  }
}

function renderDetail() {
  const box = $("frame-rows");
  if (!box) return;
  box.textContent = "";
  closeReframePanel();
  if (!lastSheet || !Array.isArray(lastSheet.rows)) {
    box.append(el("div", "hint", "build the sheet to see per-window crops"));
    return;
  }
  const entry = currentEntry();
  if (!entry) {
    // A sheet whose every placement was skipped — every shot is a still.
    // Distinct from having no sheet, and it has to read that way.
    box.append(el("div", "hint", "this sheet has no croppable shots — every placement is a still"));
    return;
  }
  const first = entry.rows[0];
  const last = entry.rows[entry.rows.length - 1];
  const head = el("div", "frame-detail-head");
  head.append(el("span", "frame-row-shot", `shot #${entry.shot}`));
  head.append(el("span", "frame-detail-asset", first.asset));
  const span = el(
    "span",
    "mono",
    `src ${coarse(first.src_start)}\u2013${coarse(last.src_start + last.duration)}`,
  );
  // The full source seconds stay one hover away: this is the address
  // `reframe --src-start` takes, and three decimals is how it is stored.
  span.title = `src ${secs(first.src_start)}\u2013${secs(last.src_start + last.duration)}`;
  head.append(span);
  head.append(
    el("span", "hint", `${entry.rows.length} window${entry.rows.length === 1 ? "" : "s"}`),
  );
  box.append(head);
  const strip = buildFilmstrip(entry);
  if (strip) box.append(strip);
  entry.rows.forEach((row, i) => box.append(buildRow(row, i + 1)));
}

/** The whole shot as a strip of source frames, with its window boundaries
 * and the three sampled instants marked on it.
 *
 * The tiles above are evidence about three INSTANTS; a rect is a claim about
 * a STRETCH (CLAUDE.md § The tile that made a wrong window look right). So a
 * clean row of tiles is not an approval of the span, and until this existed
 * nothing in the view said which instants you had actually looked at, or how
 * much of the shot sat between them.
 *
 * `/api/thumb/<clip_id>?at=` is `ops.thumbnail` — cached under
 * `cache/thumbs/`, never in the manifest, never resolved by
 * `media.media_path`/`preview_path`. It snaps `at` to a `THUMB_INTERVAL`
 * bucket before reading or writing anything, so a strip across one shot asks
 * for a handful of buckets rather than one file per cell.
 *
 * **The clip it asks for is `row.asset`, never `row.clip_id`.** A shot's
 * addressing clip is the transcript track the cue hangs off — `"vo"` on an
 * audio-only project — and its footage is `asset`. The first filmstrip draft
 * in this repo thumbnailed `clip_id` and every request would have 400'd
 * (CLAUDE.md § A shot's addressing clip is not its footage). A card asset is
 * skipped rather than asked for: `card:<name>` is not a clip id, and cards
 * reach this view through `skipped` anyway.
 */
function buildFilmstrip(entry) {
  const first = entry.rows[0];
  const last = entry.rows[entry.rows.length - 1];
  const asset = first.asset;
  if (!asset || asset.startsWith("card:")) return null;
  const start = first.src_start;
  const end = last.src_start + last.duration;
  const span = end - start;
  if (!(span > 0)) return null;

  const wrap = el("div", "frame-strip-wrap");
  wrap.append(
    el("div", "hint", `the whole shot — ${coarse(start)} to ${coarse(end)} of ${asset}`),
  );
  const strip = el("div", "frame-strip");
  const CELLS = 12;
  for (let i = 0; i < CELLS; i += 1) {
    // The MIDDLE of each cell's slice, not its left edge: a cell captioned
    // with the instant at its own boundary is a frame from the neighbouring
    // cell's territory.
    const at = start + (span * (i + 0.5)) / CELLS;
    const img = document.createElement("img");
    img.src = `/api/thumb/${encodeURIComponent(asset)}?at=${at.toFixed(3)}`;
    img.alt = "";
    img.loading = "lazy";
    img.title = `${asset} @ ${secs(at)}`;
    strip.append(img);
  }
  // Where this shot's windows divide it. The first row's own start is the
  // strip's left edge and is not a division, so the marks come off rows 2..n.
  for (const row of entry.rows.slice(1)) {
    const mark = el("div", "frame-strip-edge");
    mark.style.left = `${(((row.src_start - start) / span) * 100).toFixed(3)}%`;
    mark.title = `window boundary — src ${secs(row.src_start)}`;
    strip.append(mark);
  }
  // And which instants the tiles above actually looked at.
  for (const row of entry.rows) {
    for (const sample of row.samples || []) {
      if (sample.src_time === null || sample.src_time === undefined) continue;
      const tick = el("div", "frame-strip-tick");
      tick.style.left = `${(((sample.src_time - start) / span) * 100).toFixed(3)}%`;
      tick.title = `sampled here — src ${secs(sample.src_time)}`;
      strip.append(tick);
    }
  }
  wrap.append(strip);
  return wrap;
}

function buildRow(row, windowIndex) {
  const key = rowKey(row);
  const wrapper = el("div", "frame-row");
  if (row.split) wrapper.classList.add("frame-row-split");
  if (approvedKeys.has(key)) wrapper.classList.add("frame-row-approved");
  wrapper.dataset.rowKey = key;

  const header = el("div", "frame-row-header");
  // No `shot #N` and no asset here any more: the detail column is one shot,
  // and `renderDetail`'s own head states both once. Repeating them per row
  // was four identical headers on a four-window shot.
  header.append(el("span", "frame-row-window", `window ${windowIndex} of ${row.windows}`));
  const span = el(
    "span",
    "mono",
    `src ${coarse(row.src_start)}–${coarse(row.src_start + row.duration)}`,
  );
  // The full source seconds stay one hover away: this is the address
  // `reframe --src-start` takes, and three decimals is how it is stored.
  span.title = `src ${secs(row.src_start)}–${secs(row.src_start + row.duration)}`;
  header.append(span);
  // **The rect belongs to the row, not the tile** — a row is one window
  // (ops.reframe_sheet: "a sheet row is a window shown, not a placement"), so
  // its samples all read `crop_at` inside that one window and come back
  // identical. Three tiles captioned with the same rect, under a rect already
  // burnt into each tile by the sheet renderer, is the same number four
  // times. The exception is a **sliding** window, where the rects are
  // `_lerp_rect` interpolations and genuinely differ tile to tile — so this
  // asks the data rather than assuming, and a row whose samples disagree
  // keeps its per-tile captions.
  const crops = (row.samples || []).map((sample) => sample.crop);
  const sharedCrop = crops.length && crops.every((c) => c && c === crops[0]) ? crops[0] : null;
  if (sharedCrop) header.append(el("span", "mono", sharedCrop));
  if (row.sliding) {
    header.append(el("span", "hint", `slides to ${secs(row.slides_to)}`));
  }
  wrapper.append(header);

  const badges = el("div", "frame-row-badges");
  if (row.fill) badges.append(el("span", "frame-badge", "blur-fill"));
  if (row.split) {
    const overlapText =
      row.pane_overlap === null || row.pane_overlap === undefined
        ? "pane overlap: n/a"
        : `${Math.round(row.pane_overlap * 100)}% pane overlap`;
    badges.append(el("span", "frame-badge", overlapText));
  }
  // worst_offset beside multi_face, never alone — CLAUDE.md's rule, honoured
  // even though this step's own data path (no --extremes call) never
  // populates either field, so a later step turning extremes on needs no
  // change here.
  if (row.worst_offset !== null && row.worst_offset !== undefined) {
    const offsetText = `worst offset ${row.worst_offset}px${row.multi_face ? " (multi-face)" : ""}`;
    badges.append(el("span", "frame-badge", offsetText));
  }
  const provenance = provenanceChip(row);
  if (provenance) badges.append(provenance);
  if (badges.childNodes.length) wrapper.append(badges);

  const strip = el("div", "frame-tile-strip");
  for (const sample of row.samples || []) {
    strip.append(buildTile(sample, sharedCrop));
  }
  wrapper.append(strip);

  if (reframedKeys.has(key)) {
    wrapper.append(el("div", "frame-row-note", "reframed — rebuild the sheet to see it"));
  }

  const actions = el("div", "frame-row-actions");
  const approveBtn = el("button", null, approvedKeys.has(key) ? "Approved" : "Approve");
  approveBtn.type = "button";
  // **The pane's own head sentence, moved onto the control it is about**
  // (2026-08-24). "per-shot crop windows — Approve writes nothing, Re-frame
  // does" sat in dim small-caps at the top of the view, the fourth such
  // sentence on the screen and the only one of the five doing real work: it
  // disambiguates these two adjacent buttons. A warning read three rows away
  // from the button it is about is not a warning. Both buttons carry their
  // half, so the pair reads correctly whichever one the cursor lands on.
  approveBtn.title =
    "records your judgement of this window — writes no rect. Re-frame… is what changes the crop.";
  approveBtn.addEventListener("click", () => {
    if (approvedKeys.has(key)) approvedKeys.delete(key);
    else approvedKeys.add(key);
    wrapper.classList.toggle("frame-row-approved", approvedKeys.has(key));
    approveBtn.textContent = approvedKeys.has(key) ? "Approved" : "Approve";
    // The shot list carries a tick once every window of a shot is approved,
    // so it has to redraw — but only the list. Rebuilding the detail column
    // here would remove the button the click landed in, and Chrome then
    // drops the trailing `click` with nothing thrown (CLAUDE.md § So redraw
    // only the node a gesture owns while it is live).
    renderShotList();
  });
  const reframeBtn = el("button", null, "Re-frame…");
  reframeBtn.type = "button";
  reframeBtn.title = "change this window's crop rect — the half of the pair that writes";
  reframeBtn.addEventListener("click", () => toggleReframePanel(row, reframeBtn));
  actions.append(approveBtn, reframeBtn);
  wrapper.append(actions);

  return wrapper;
}

/** One tile. `sharedCrop` is the rect the row header already states, when
 * every sample in the row agreed on one — the caption is then redundant and
 * is dropped. The **pane** caption is never dropped: a split's lower rect is
 * per-row too, but its presence is the finding (CLAUDE.md § The stacked
 * split), and a row that silently stopped saying it was split would be
 * indistinguishable from one that is not. */
function buildTile(sample, sharedCrop) {
  const tile = el("div", "frame-tile");
  if (sample.png) {
    const img = document.createElement("img");
    img.src = tileUrl(sample.png);
    img.alt = sample.crop ? `crop ${sample.crop}` : "reframe sample";
    img.loading = "lazy";
    tile.append(img);
  }
  if (sample.crop && sample.crop !== sharedCrop) {
    tile.append(el("div", "frame-tile-caption", sample.crop));
  }
  if (sample.pane) tile.append(el("div", "frame-tile-caption pane", sample.pane));
  return tile;
}

/* -- the Re-frame… panel ----------------------------------------------------
 *
 * A floating panel positioned relative to `#frame-rows`, clamped with
 * dom.js's ONE `clampFloating` (never a second inline clamp — the trap that
 * cost both other toolbars once already) and height-capped in CSS so a
 * panel taller than the pane pins with its own scrollbar rather than with
 * its buttons pushed off-screen. `#frame-rows` and every element between it
 * and the button that opens this stay `position: static` on purpose, so the
 * button's own `offsetLeft`/`offsetTop` resolve against `#frame-rows` — the
 * exact transcript.js selection-toolbar precedent dom.js's own header
 * documents (no scroll offset to fold in here, since `#frame-rows` does not
 * scroll on its own — `#frame-view` is the one scrolling ancestor).
 */

function closeReframePanel() {
  if (openPanel && openPanel.parentNode) openPanel.remove();
  openPanel = null;
}

function toggleReframePanel(row, anchorBtn) {
  const key = rowKey(row);
  if (openPanel && openPanel.dataset.rowKey === key) {
    closeReframePanel();
    return;
  }
  closeReframePanel();
  const head = row.samples && row.samples[0];
  const draft = {
    rect: parseRect(head && head.crop),
    pane: row.split ? parseRect(head && head.pane) : null,
    fill: Boolean(row.fill),
  };
  const panel = buildReframePanel(row, draft);
  panel.dataset.rowKey = key;
  const container = $("frame-rows");
  container.append(panel);
  positionPanel(panel, anchorBtn, container);
  openPanel = panel;
}

function positionPanel(panel, anchorBtn, container) {
  const left = anchorBtn.offsetLeft;
  const top = anchorBtn.offsetTop + anchorBtn.offsetHeight;
  // `#frame-rows` is BOTH the positioning context and, since the view became
  // a list and a detail, the scroll container — so the visible box is
  // `scrollTop … scrollTop + clientHeight` in the same coordinates
  // offsetTop is measured in, not `0 … clientHeight`. Clamping against the
  // latter would pin the panel to the top of the CONTENT on any shot scrolled
  // past one screen, which is off the visible column entirely: a panel that
  // opens somewhere nobody can see, which is the exact failure clampFloating
  // exists to prevent (dom.js's header — and the reason it takes bounds
  // rather than a container is that its two other callers hand it two
  // different spaces).
  const { left: clampedLeft, top: clampedTop } = clampFloating(
    left,
    top,
    panel.offsetWidth,
    panel.offsetHeight,
    container.scrollLeft,
    container.scrollLeft + container.clientWidth,
    container.scrollTop,
    container.scrollTop + container.clientHeight,
  );
  panel.style.left = `${clampedLeft}px`;
  panel.style.top = `${clampedTop}px`;
}

function buildReframePanel(row, draft) {
  const panel = el("div", "toolbar-popover frame-reframe-panel");
  panel.append(el("div", "hint", `${row.asset} · src ${secs(windowAddress(row))}`));

  // Crop, or blur-fill: the whole frame contained over its own blurred copy
  // (PLAN.md § Blur-fill). A fill takes no rect, so its editors fold away.
  const mode = el("div", "frame-reframe-mode");
  const cropBtn = el("button", null, "Crop");
  const fillBtn = el("button", null, "Blur-fill");
  cropBtn.type = fillBtn.type = "button";
  cropBtn.title = "keep the rect below, cropped to fill the frame";
  fillBtn.title = "show the whole frame, over a blurred, darkened copy of itself";
  mode.append(cropBtn, fillBtn);
  panel.append(mode);

  const crop = el("div", "frame-reframe-crop");
  crop.append(el("div", "hint", "nudge or type the crop"));
  crop.append(buildRectEditor("rect", draft, "rect"));
  if (row.split) {
    crop.append(el("div", "hint", "pane (lower half of the split)"));
    crop.append(buildRectEditor("pane", draft, "pane"));
  }
  panel.append(crop);

  function syncMode() {
    cropBtn.setAttribute("aria-pressed", String(!draft.fill));
    fillBtn.setAttribute("aria-pressed", String(draft.fill));
    crop.hidden = draft.fill;
  }
  cropBtn.addEventListener("click", () => {
    draft.fill = false;
    syncMode();
  });
  fillBtn.addEventListener("click", () => {
    draft.fill = true;
    syncMode();
  });
  syncMode();

  const actions = el("div", "frame-reframe-actions");
  const cancelBtn = el("button", null, "Cancel");
  cancelBtn.type = "button";
  cancelBtn.addEventListener("click", () => closeReframePanel());
  const submitBtn = el("button", "primary", "Submit");
  submitBtn.type = "button";
  submitBtn.addEventListener("click", () => submitReframe(row, draft, submitBtn));
  actions.append(cancelBtn, submitBtn);
  panel.append(actions);

  return panel;
}

function buildRectEditor(label, draft, key) {
  const wrap = el("div", "frame-rect-editor");
  wrap.append(el("div", "hint", label));

  const fields = el("div", "frame-rect-fields");
  const inputs = {};
  for (const f of ["x", "y", "w", "h"]) {
    const fieldWrap = el("label", "frame-rect-field", f.toUpperCase());
    const input = document.createElement("input");
    input.type = "number";
    input.value = draft[key][f];
    input.addEventListener("change", () => {
      draft[key][f] = Number(input.value) || 0;
    });
    inputs[f] = input;
    fieldWrap.append(input);
    fields.append(fieldWrap);
  }
  wrap.append(fields);

  function syncInputs() {
    for (const f of ["x", "y", "w", "h"]) inputs[f].value = draft[key][f];
  }

  // Nudges move x/y only — a size change goes through the number inputs
  // above directly (STUDIO names nudge buttons for position; width/height
  // are direct-entry only, same field).
  const nudges = el("div", "frame-nudges");
  const nudgeSpecs = [
    ["←", -1, 0, STEP_FINE],
    ["→", 1, 0, STEP_FINE],
    ["↑", 0, -1, STEP_FINE],
    ["↓", 0, 1, STEP_FINE],
    ["← ×8", -1, 0, STEP_COARSE],
    ["→ ×8", 1, 0, STEP_COARSE],
    ["↑ ×8", 0, -1, STEP_COARSE],
    ["↓ ×8", 0, 1, STEP_COARSE],
  ];
  for (const [text, dx, dy, step] of nudgeSpecs) {
    const btn = el("button", null, text);
    btn.type = "button";
    btn.addEventListener("click", () => {
      draft[key].x += dx * step;
      draft[key].y += dy * step;
      syncInputs();
    });
    nudges.append(btn);
  }
  wrap.append(nudges);

  return wrap;
}

async function submitReframe(row, draft, submitBtn) {
  if (!ctx) return;
  submitBtn.disabled = true;
  const body = draft.fill
    ? { clip_id: row.asset, fill: "blur", src_start: windowAddress(row) }
    : { clip_id: row.asset, rect: rectText(draft.rect), src_start: windowAddress(row) };
  if (!draft.fill && row.split && draft.pane) body.pane = rectText(draft.pane);
  try {
    await ctx.api("/api/reframe", body);
  } catch (err) {
    submitBtn.disabled = false;
    ctx.emit("toast", err.message);
    return;
  }
  reframedKeys.add(rowKey(row));
  closeReframePanel();
  renderRows();
}

/* -- module contract --------------------------------------------------------- */

export function init(passedCtx) {
  ctx = passedCtx;

  const buildBtn = $("frame-build-sheet");
  if (buildBtn) buildBtn.addEventListener("click", onBuildSheetClick);

  const detectBtn = $("frame-detect-gaps");
  if (detectBtn) detectBtn.addEventListener("click", onDetectClick);

  ctx.on("reframe-sheet", onSheetEvent);
  ctx.on("reframe-detect", onDetectEvent);
  // The other half of the deferral in `update()`: opening Frame is what
  // pays for the scan, and only if something changed since the last one.
  ctx.on("mode", (mode) => {
    if (mode === "frame" && coverageStale) refreshCoverage();
  });
}

export function update(state) {
  // Ephemeral, per-sitting UI state — reset on every reload, never
  // persisted (STUDIO: "report-only is the standing precedent"). The sheet
  // and detect jobs themselves are NOT re-triggered here — they stay
  // whatever they were, keyed on their own buttons, the finish.js render-job
  // precedent.
  approvedKeys = new Set();
  lastState = state;
  // Whatever this view scanned was about the project before this reload.
  coverageGeneration += 1;
  ctx.emit("coverage", null);
  // Coverage is NOT re-fetched here unless this view is on screen. It was,
  // and it cost 5.5s of scene-cut decoding per call on the film — twice on
  // every page load and once more on every `project-changed`, so every cut
  // made in Edit mode paid for a scan of footage nobody was looking at.
  // That is precisely the cost `finish_report`'s framing was made opt-in to
  // avoid (CLAUDE.md), reintroduced through this pane. Deferred, the answer
  // is fetched when Frame is opened, and refreshed while it stays open.
  coverageStale = true;
  if (frameVisible()) refreshCoverage();
  // Draw the shot list and the detail column even with no sheet in hand, so
  // both say what they are waiting for. Until 2026-08-24 nothing called this
  // on load and `#frame-rows` was simply EMPTY until the first sheet event —
  // survivable when it was one blank area under a header, and not once the
  // view became two columns, where two empty boxes read as a pane that failed
  // to load rather than one that has nothing to draw yet. Same rule
  // `refreshCoverage` already follows out loud with "scanning for cuts…":
  // a view must never let silence stand in for an answer.
  //
  // Costs nothing — no fetch, and with no sheet it appends one line each.
  // A rebuild WITH a sheet is right here too: `approvedKeys` was just reset
  // above, and the shot list draws its tick from it.
  renderRows();
}
