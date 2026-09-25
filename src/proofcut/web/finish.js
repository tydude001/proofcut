/**
 * finish.js — the Finish view: `GET /api/finish` (`ops.finish_report`)
 * drawn, the preset picker, the burn checkbox, the Render button, and the
 * live per-stage report as the render job's SSE events land.
 *
 * The `properties.js` pattern exactly (see that file's own header): this
 * pane's job is display, not interpretation. Every number in `#finish-view`
 * comes straight off the fetched bundle — nothing here recomputes a
 * duration, re-checks a canvas, or re-derives whether captions burned.
 *
 * This is also the ONLY module that calls `GET /api/finish`. On every
 * successful fetch it renders `#finish-view`'s own contents AND calls
 * `ctx.emit("finish-report", bundle)` so the truth strip (shell chrome,
 * owned by app.js) never issues a second fetch of the same endpoint — see
 * app.js's `ctx.on("finish-report", renderTruthStrip)`. Because `update()`
 * is called by app.js's `load()` on every `project-changed` SSE record
 * (the same hook every other pane already gets), the truth strip is kept
 * current for free — no extra wiring needed for STUDIO's "refreshed on the
 * existing project-changed SSE" requirement.
 *
 * `finish.js` owns exclusively the contents of `#finish-view`: preset
 * cards, the "what this render will contain" manifest text, the burn
 * checkbox, the Render button, and the stage report. It does NOT own mode
 * switching or the truth strip itself — those are shell chrome (app.js).
 */

import { $, el, fmt } from "./dom.js";

let ctx = null;
let lastFinish = null; // the last /api/finish bundle
let requestSeq = 0; // guards an in-flight fetch from an earlier project
// state landing after a newer one — the same guard properties.js uses for
// its own out-of-order inspection fetches.

let selectedPresetKey = ""; // "" = the Default bundle (preset: null)
let userToggledBurn = false; // once true, update() stops overwriting the
// checkbox's default — a person's own choice mid-session must survive the
// next project-changed reload, which otherwise fires on every mutation.

let stageRows = new Map(); // stage name -> its .check-row element, for the
// render currently (or most recently) in flight — reset on every "running".
let reportCard = null; // the .completion-card wrapper currently in #finish-report

const PRESET_LABELS = {
  "": "Default",
  youtube: "YouTube",
  web: "Web",
  "tiktok-reels": "TikTok / Reels",
};

const STAGE_LABELS = {
  export: "Export",
  burn: "Burn captions",
  check_frames: "Frame count",
  verify: "Audio verify",
};

/** "done"→ok, "error"→fail, "skipped"→skip, "cancelled"→warn — the same
 * four-state badge vocabulary agent.js's own completion card already uses
 * for a render's checks (`.check-badge.{ok,warn,fail,skip}`, app.css).
 * "reused" is ok too: the stage's file is the last run's, unchanged
 * (webui.RenderJob), and the badge says so in its own word. */
function badgeClassForOutcome(outcome) {
  switch (outcome) {
    case "done":
    case "reused":
      return "ok";
    case "error":
      return "fail";
    case "skipped":
      return "skip";
    case "cancelled":
      return "warn";
    default:
      return "skip";
  }
}

function detailSummary(detail) {
  if (!detail || typeof detail !== "object") return "";
  if (typeof detail.reason === "string") return detail.reason;
  if (typeof detail.error === "string") return detail.error;
  if (typeof detail.reused === "string") return `the render of ${detail.reused}, unchanged`;
  if ("agrees" in detail) return `agrees: ${detail.agrees === null ? "–" : detail.agrees ? "yes" : "no"}`;
  if ("similarity" in detail) return `similarity ${detail.similarity ?? "–"}`;
  try {
    return JSON.stringify(detail);
  } catch {
    return "";
  }
}

/* -- preset cards ---------------------------------------------------------- */

function selectPreset(key) {
  selectedPresetKey = key;
  for (const card of document.querySelectorAll(".finish-preset-card")) {
    card.classList.toggle("selected", card.dataset.preset === key);
  }
  renderManifest();
}

function presetEntries(bundle) {
  const entries = [{ key: "", ok: true, message: null, needs: null, fix: null }];
  for (const [key, info] of Object.entries(bundle.canvas.presets)) {
    entries.push({ key, ok: info.ok, message: info.message, needs: info.needs, fix: info.fix });
  }
  return entries;
}

// Which refusing card is open, by preset key. Survives a re-render (every
// `project-changed` rebuilds the cards), so a card someone opened to read
// does not snap shut under them because an agent made a cut.
const openRefusals = new Set();

function renderPresets(bundle) {
  const box = $("finish-presets");
  if (!box) return;
  box.textContent = "";
  for (const { key, ok, message, needs, fix } of presetEntries(bundle)) {
    const card = el("div", "finish-preset-card");
    card.dataset.preset = key;
    card.dataset.ok = String(ok);
    card.classList.toggle("selected", key === selectedPresetKey);
    card.append(el("div", "finish-preset-name", PRESET_LABELS[key] || key));
    if (!ok) {
      // A refusing preset is quiet until asked (HISTORY.md § The refusing
      // preset card). It used to draw its whole refusal in the error red,
      // before anyone clicked it, and the author read a correct refusal as
      // Finish failing — nothing had gone wrong. Closed, it says what it
      // needs in one line; open, it shows the op's message verbatim and the
      // one command, which docs/plans/STUDIO.md's "its message AND the fix"
      // still requires. Both short parts come off the op (`needs`, `fix`),
      // never cut out of `message`; with no `needs` the card has no short
      // form and opens straight to the message.
      card.setAttribute("role", "button");
      card.tabIndex = 0;
      const isOpen = openRefusals.has(key) || !needs;
      card.classList.toggle("open", isOpen);
      card.setAttribute("aria-expanded", String(isOpen));
      if (needs) card.append(el("div", "finish-preset-needs", needs));
      const detail = el("div", "finish-preset-detail");
      detail.hidden = !isOpen;
      detail.append(el("div", "finish-preset-message", message));
      if (fix) detail.append(el("code", "finish-preset-fix mono", fix));
      card.append(detail);
      const toggle = () => {
        if (!needs) return;
        const nowOpen = !openRefusals.has(key);
        if (nowOpen) openRefusals.add(key);
        else openRefusals.delete(key);
        card.classList.toggle("open", nowOpen);
        card.setAttribute("aria-expanded", String(nowOpen));
        detail.hidden = !nowOpen;
      };
      card.addEventListener("click", (event) => {
        // Selecting the command to copy it is reading, not closing.
        if (event.target.closest(".finish-preset-fix")) return;
        toggle();
      });
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
    } else {
      card.addEventListener("click", () => selectPreset(key));
    }
    box.append(card);
  }
}

/* -- the render manifest ---------------------------------------------------- */

function renderManifest() {
  const box = $("finish-manifest");
  if (!box || !lastFinish) return;
  const b = lastFinish;
  const presetLabel = PRESET_LABELS[selectedPresetKey] || selectedPresetKey;
  // The three duration numbers are listed, never added up on the page. They
  // do not sum: `total_seconds` is `expected_duration`, which quantises each
  // segment edge to a frame on its own, so it and `edit + tail` differ by up
  // to a frame or two (CLAUDE.md § A frame count comes from
  // `autoeditor.frame_layout`). Writing "a + b = c" would assert arithmetic
  // that is false, and the render hits the third number, not the sum.
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const lines = [
    `preset: ${presetLabel}`,
    `duration: ${fmt(b.duration.total_seconds)} — the length the render must hit`,
    `           edit ${fmt(b.duration.edit_seconds)} · tail ${fmt(b.duration.tail_seconds)}`,
    `canvas: ${b.canvas.canvas}`,
    `captions: ${b.captions.configured ? "configured" : "no style"} · last render burned: ${b.captions.burned}`,
    `picture: ${plural(b.picture.cue_count, "cue")}, ${b.picture.pinned_count} pinned${b.picture.shots_error ? " · picture refused: " + b.picture.shots_error : ""}`,
    plural(b.flags.count, "flag"),
  ];
  box.textContent = "";
  for (const line of lines) box.append(el("div", null, line));
}

/* -- the last render, playable ---------------------------------------------- */

// The window plays the project, not the file — it rebuilds the picture live
// from the cue table and never reads `renders/`. That left Export writing
// something the page could not open or even name, which reads as "nothing
// happened" (PLAN.md § Should the workspace play its own output?). This says
// what the last run produced and, on request, plays it from /api/output.
// It never lists `renders/` and never lets the page name a file: the route
// resolves the render log's own last output, so what plays here is exactly
// what the report above describes.
function renderLastOutput() {
  const box = $("finish-output");
  if (!box) return;
  box.textContent = "";
  const last = lastFinish && lastFinish.last_render;
  if (!last) {
    // Its own column now, so "nothing here yet" has to be SAID — an empty
    // half of the screen reads as a pane that failed to load.
    box.append(el("div", "pane-placeholder", "nothing rendered from this window yet"));
    return;
  }
  if (!last.exists) {
    box.append(el("div", "warn", `last render: ${last.name} — no longer on disk`));
    return;
  }
  const line = el("div", "finish-output-line");
  line.append(el("span", "hint", "last render"));
  // The name is a 60-character hash and this column is not always wide, so
  // it ellipsises rather than wrapping the row into two — the full name
  // rides the tooltip.
  const name = el("span", "finish-output-name", last.name);
  name.title = last.name;
  line.append(name);
  // `current` is false only when the render log's stamps say the edit has
  // moved since this file was made; null (a render from before stamps) says
  // nothing, since "not recorded" is not "unchanged".
  if (last.current === false) {
    const stale = el("span", "warn", "older than the edit");
    stale.title = "The timeline or project settings changed after this render. Export again to watch the current cut.";
    line.append(stale);
  }
  line.append(el("span", "spacer"));
  const watch = el("button", null, "Watch");
  watch.type = "button";
  // Still lazy, and for the reason it always was: /api/output streams the
  // render, and building the element is what starts the fetch. Visiting
  // Finish to read the checklist must not pull the whole file over.
  watch.addEventListener("click", () => {
    if (box.querySelector("video")) return;
    watch.disabled = true;
    box.append(buildPlayer());
  });
  line.append(watch);
  box.append(line);
}

/** The render, with proofcut's transport rather than the browser's.
 *
 * `<video controls>` draws Chrome's own control bar — a different type
 * family, a different icon set, and a light-on-dark look that ignores the
 * theme toggle — inside a pane that is otherwise entirely this app's
 * chrome. This is #transport's idiom instead: a round play button, the mono
 * clock in `fmt()`'s own `m:ss.s`, and a range input for the scrub.
 *
 * The scrub is a real `<input type="range">` and not a styled div, for the
 * reason `#zoom` already is one: it is the only control here that a keyboard
 * has to be able to drive, and the native element brings arrow keys, Home/End
 * and a screen-reader value for free.
 *
 * It does NOT reach into player.js. The preview transport belongs to Edit
 * mode and this belongs to Finish; only one of the two views is ever on
 * screen, and the window-level space binding was already shared with a
 * `<video controls>` before this — unchanged, not newly broken.
 */
function buildPlayer() {
  const wrap = el("div", "finish-player");
  const video = el("video");
  video.preload = "metadata";
  video.playsInline = true;
  video.src = "/api/output";
  wrap.append(video);

  const transport = el("div", "finish-transport");
  const play = el("button", "finish-play", "▶");
  play.type = "button";
  play.title = "play / pause";
  play.setAttribute("aria-label", "play or pause the render");
  const clock = el("span", "clock mono", "0:00.0");
  const scrub = document.createElement("input");
  scrub.type = "range";
  scrub.className = "finish-scrub";
  scrub.min = "0";
  scrub.max = "1000";
  scrub.step = "1";
  scrub.value = "0";
  scrub.setAttribute("aria-label", "seek the render");
  const total = el("span", "clock mono", "–");
  const full = el("button", null, "⤢");
  full.type = "button";
  full.title = "fullscreen";
  full.setAttribute("aria-label", "play the render fullscreen");
  transport.append(play, clock, scrub, total, full);
  wrap.append(transport);

  // While a drag is live the scrub OWNS its own value: a `timeupdate` landing
  // mid-drag would write the video's position back into the thumb and yank it
  // out from under the cursor. Same rule the timeline's own gestures follow —
  // do not redraw the node a gesture is holding.
  let dragging = false;
  const seekFromScrub = () => {
    if (!Number.isFinite(video.duration)) return;
    video.currentTime = (Number(scrub.value) / 1000) * video.duration;
  };
  scrub.addEventListener("pointerdown", () => {
    dragging = true;
  });
  scrub.addEventListener("pointerup", () => {
    dragging = false;
  });
  scrub.addEventListener("input", seekFromScrub);

  video.addEventListener("loadedmetadata", () => {
    total.textContent = fmt(video.duration);
  });
  video.addEventListener("timeupdate", () => {
    clock.textContent = fmt(video.currentTime);
    if (dragging || !Number.isFinite(video.duration) || video.duration <= 0) return;
    scrub.value = String(Math.round((video.currentTime / video.duration) * 1000));
  });
  // The glyph follows the ELEMENT's own state, never the click: a video that
  // stalls, ends, or is paused by the OS media keys would otherwise leave the
  // button claiming the opposite of what is happening.
  const syncPlayGlyph = () => {
    play.textContent = video.paused ? "▶" : "❚❚";
  };
  video.addEventListener("play", syncPlayGlyph);
  video.addEventListener("pause", syncPlayGlyph);
  video.addEventListener("ended", syncPlayGlyph);

  play.addEventListener("click", () => {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  });
  full.addEventListener("click", () => {
    if (video.requestFullscreen) video.requestFullscreen().catch(() => {});
  });

  return wrap;
}

/* -- the burn checkbox ------------------------------------------------------ */

function applyBurnDefault(bundle) {
  const checkbox = $("finish-burn-checkbox");
  if (!checkbox || userToggledBurn) return;
  checkbox.checked = Boolean(bundle.captions.configured);
  $("finish-burn-toggle").classList.toggle("on", checkbox.checked);
}

/* -- the full bundle render -------------------------------------------------- */

function renderBundle(bundle) {
  lastFinish = bundle;
  renderPresets(bundle);
  renderManifest();
  renderLastOutput();
  applyBurnDefault(bundle);
  if (ctx) ctx.emit("finish-report", bundle);
}

async function fetchAndRenderFinish() {
  if (!ctx) return;
  const seq = ++requestSeq;
  let bundle;
  try {
    bundle = await ctx.api("/api/finish");
  } catch (err) {
    if (seq === requestSeq) {
      const box = $("finish-manifest");
      if (box) {
        box.textContent = "";
        box.append(el("div", "warn bad", err.message));
      }
    }
    return;
  }
  if (seq !== requestSeq) return; // superseded by a later project state
  renderBundle(bundle);
}

/* -- the render button ------------------------------------------------------ */

async function onRenderClick() {
  const preset = selectedPresetKey || null;
  const burn = $("finish-burn-checkbox").checked;
  try {
    await ctx.api("/api/render", { preset, resolution: null, burn });
  } catch (err) {
    ctx.emit("toast", err.message);
  }
}

/* -- the live stage report --------------------------------------------------- */

function startReportCard(data) {
  const box = $("finish-report");
  if (!box) return;
  box.textContent = "";
  stageRows = new Map();
  reportCard = el("div", "completion-card");
  reportCard.append(
    el("div", null, `Rendering${data.preset ? ` · preset ${data.preset}` : ""}…`),
  );
  box.append(reportCard);
}

function stageRow(stage) {
  if (stageRows.has(stage)) return stageRows.get(stage);
  if (!reportCard) return null;
  const row = el("div", "check-row");
  row.append(el("span", null, STAGE_LABELS[stage] || stage));
  const badge = el("span", "check-badge skip", "…");
  row.append(badge);
  reportCard.append(row);
  const summary = el("div", null, "");
  summary.style.fontSize = "11px";
  summary.style.opacity = "0.75";
  summary.style.margin = "-4px 0 4px";
  reportCard.append(summary);
  const entry = { row, badge, summary };
  stageRows.set(stage, entry);
  return entry;
}

function applyStageEvent(data) {
  const entry = stageRow(data.stage);
  if (!entry) return;
  const cls = badgeClassForOutcome(data.outcome);
  entry.badge.className = `check-badge ${cls}`;
  entry.badge.textContent = data.outcome;
  entry.summary.textContent = detailSummary(data.detail);
}

function finalizeReportCard(status, data) {
  if (!reportCard) return;
  if (status === "error") {
    reportCard.append(el("div", "warn bad", `Render failed — ${data.error || "unknown error"}`));
  } else if (status === "cancelled") {
    reportCard.append(el("div", null, "Render cancelled — the partial output was deleted."));
  } else if (status === "done") {
    reportCard.append(el("div", null, "Render complete."));
  }
}

function onRenderEvent(data) {
  if (!data || typeof data !== "object") return;
  switch (data.status) {
    case "running":
      startReportCard(data);
      break;
    case "stage":
      applyStageEvent(data);
      break;
    case "error":
    case "cancelled":
    case "done":
      finalizeReportCard(data.status, data);
      // The render log picked up a fresh entry the moment this landed —
      // re-fetch so the manifest and (via 'finish-report') the truth strip
      // reflect it immediately, rather than waiting for the next
      // project-changed poll.
      fetchAndRenderFinish();
      break;
    default:
      break;
  }
}

/* -- module contract --------------------------------------------------------- */

export function init(passedCtx) {
  ctx = passedCtx;

  const renderBtn = $("finish-render");
  if (renderBtn) renderBtn.addEventListener("click", onRenderClick);

  const checkbox = $("finish-burn-checkbox");
  if (checkbox) {
    checkbox.addEventListener("change", () => {
      userToggledBurn = true;
      $("finish-burn-toggle").classList.toggle("on", checkbox.checked);
    });
  }

  ctx.on("render", onRenderEvent);
}

export function update(_state) {
  // The pane-module contract passes the whole /api/view payload here, same
  // as every other pane's update() — this one needs none of it, since its
  // own bundle comes from a separate endpoint (GET /api/finish) fetched
  // fresh on every call, the properties.js precedent for a pane whose data
  // is not a slice of `view`.
  fetchAndRenderFinish();
}
