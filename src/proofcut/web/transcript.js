/**
 * transcript.js — the transcript as a document, plus the floating
 * cut-controls toolbar (PLAN.md §
 * Where the cut controls go).
 *
 * What this file draws:
 *   - words grouped into `.para` blocks off each word's `paragraph` field
 *     (PLAN.md § Read-model additions — word-order-driven, not
 *     duration-driven), with an inline timestamp at each paragraph's head;
 *   - a show-cuts toggle: cut words struck through in place when on,
 *     omitted from the document entirely when off;
 *   - scroll-to-the-playing-word, driven by player.js's own 'playing-word'
 *     event rather than a timer of this pane's own (ported logic, not
 *     ported code, from tier 2's app.js — see its `paintPlayingWord`,
 *     `git show <tier-2 commit>:src/lucid/web/app.js`);
 *   - a selection: click a word, shift-click or drag to extend. Selecting
 *     raises a floating `.selection-toolbar` anchored under the selection —
 *     Preview / Cut / Keep only, plus a small `.toolbar-popover` for pad
 *     and the suspect-boundary confirmation. Preview is `plan: true` on the
 *     same `/api/cut` call Cut uses, not a separate endpoint. Restore (shown
 *     only when the selection covers struck text) posts to `/api/restore`
 *     instead — a different op with no suspect-duration guard to confirm.
 *     The toolbar's drop point is clamped into the pane's own visible box
 *     through dom.js's `clampFloating` (measured +169px past the pane's
 *     right edge before this fix — F4 in the 2026-08-17 browser pass) — the
 *     same helper timeline.js's cue toolbar uses, each caller supplying its
 *     own bounds because the two toolbars do not share a coordinate space
 *     (see `clampFloating`'s own comment).
 *   - every word is also a keyboard target (F6): a roving tabindex keeps
 *     exactly one `.w` span tabbable at a time. Arrow keys move it and
 *     collapse the selection to the new word; shift-arrow extends the
 *     selection using the identical `sel` shape the mouse's shift-click and
 *     drag branches already build (same `trailingPause` call, same
 *     `[index, isPause]` pairs — see `trailingPause`'s own comment before
 *     touching any of this); Enter/Space repeat a plain click (seek +
 *     collapse). The tabbable word is tracked by WORD INDEX
 *     (`focusedIndex`), never by the DOM node itself, because
 *     `renderWords()` rebuilds every span from scratch on every op, on the
 *     show-cuts toggle, and on every background 'project-changed' reload —
 *     the node a focus-restore would need is already gone by the time any
 *     post-render code runs.
 *
 * What this file does NOT do: render an op's result. `runOp` below emits
 * 'op-result' on the shared bus and stops — agent.js renders it into the
 * feed as a system entry, because the feed is "one feed for everything
 * that happened to the edit" (PLAN.md § Where the cut controls go), and a
 * second result panel here would just be tier 2's emptiest region rebuilt.
 *
 * Word-range echoes always show the three words either side of the range
 * (CLAUDE.md), never just the range itself — an index one past the
 * intended phrase has to read correctly on its own.
 *
 * Clicking a word seeks the player through the *edit*: `ctx.player.seekWord`
 * takes the word's own `timeline_start`/`timeline_end`, never its source
 * `start`/`end` — the transcript indexes the source, the timeline is what
 * plays (CLAUDE.md).
 *
 * ---------------------------------------------------------------------
 * THE PANE-MODULE INTERFACE — this is the one copy of this contract.
 * timeline.js and agent.js point back here rather than repeating it.
 * ---------------------------------------------------------------------
 *
 * Every pane module — this one, timeline.js, agent.js — exports exactly two
 * functions and talks to the rest of the page only through `ctx`. Pane
 * modules never import each other (PLAN.md § Files, and why they split);
 * app.js wires all three, and is generic enough that a pane can be rebuilt
 * without app.js changing.
 *
 *   init(ctx)      called once at startup, after all three panes exist and
 *                  before the first view has loaded. Wire DOM listeners and
 *                  `ctx.on(...)` subscriptions here.
 *   update(state)  called with the current `/api/view` payload every time it
 *                  changes: the initial load, a reload after
 *                  'project-changed', and after this pane's own mutating op.
 *                  `state` is `null` only if a load ever fails outright —
 *                  guard for it.
 *
 * ctx = {
 *   api(path, body?)     api.js's fetch wrapper. GET with no body, POST JSON
 *                        with one (`{}` for an endpoint that ignores its
 *                        body). Throws `Error(message)` on a non-2xx reply —
 *                        the message is the server's own `{"error": …}`.
 *   player               player.js's transport, already wired to the shared
 *                        view:
 *                          seek(t)      seek the timeline to `t` seconds
 *                          now()        the current timeline second
 *                          toggle()     play/pause
 *                          playing()    bool
 *                          seekWord(w)  seek to a word's timeline_start
 *                                       (a `state.words[i]` entry), a no-op
 *                                       if the word is cut
 *   getView()            the current view payload — same shape as `update`'s
 *                        `state` — for a handler that fires outside `update`
 *                        (e.g. a click callback closed over nothing else).
 *   getCaptions()        the current /api/captions payload (`ops.caption_view`):
 *                        `{style, cues, words, words_cut}`, or `cues: []` with
 *                        a `cues_error`, or null if the fetch failed. Fetched
 *                        by app.js alongside the view and refreshed with it —
 *                        a second read model because captions are the same
 *                        edit placed, grouped and styled, and no pane may do
 *                        any of those three itself.
 *   on(event, cb)        subscribe to the shared event bus. `cb(payload)`.
 *   emit(event, payload)  publish on the shared event bus.
 * }
 *
 * Bus events (app.js re-publishes every SSE record under its own event
 * name, so a later stage can listen for one without app.js ever needing to
 * change for it):
 *   'project-changed'   {revision: [otio_mtime, manifest_mtime, undo_depth]}
 *                       — the manifest is in there because the cue table and
 *                       the caption style live in it and not in the timeline
 *                       (webui.py `_revision`). app.js already
 *                       reloads the view and calls every pane's `update` on
 *                       this; only subscribe yourself for some *other*
 *                       reaction (e.g. a toast).
 *   'agent'              the parsed stream-json object from the agent
 *                        subprocess's own stdout, passed through unchanged —
 *                        agent.js's event to build the progress list from.
 *   'render'              render-job progress/completion, shaped per
 *                        webui.py's `RenderJob` — agent.js's event to build
 *                        the completion card from.
 *   'playhead'             {now, total} — emitted every animation frame by
 *                        player.js. Use this to scroll to the playing word;
 *                        do not poll `player.now()` in a timer of your own.
 *   'playing-word'         {index} — emitted by player.js only when the
 *                        playing word changes (`state.words[i].index`, i.e.
 *                        the transcript's own word index, not `i`).
 *   'op-result'            {payload, error} — emit this after a mutating op
 *                        (cut / keep / undo) so agent.js can render it into
 *                        the feed as a system entry (PLAN.md § Where the
 *                        cut controls go: "one feed for everything that
 *                        happened to the edit, whether a person or the
 *                        agent caused it, in order"). `payload` is the op's
 *                        own JSON reply, `null` on error; `error` is the
 *                        message string, `null` on success.
 *   'toast'                a message string for the bottom-right toast —
 *                        the shell owns #toast, so emit rather than reach
 *                        for the element directly.
 *   'agent-plan'            {tool, input, payload} — emitted by agent.js
 *                        (docs/plans/STUDIO.md Step 02 item 6) when a `tool_result`
 *                        lands for a `cut_by_transcript`/`cut_by_time` call
 *                        whose tool_use had `plan: true`. `tool` is the bare
 *                        tool name, `input` is that tool_use's own `input`
 *                        object verbatim, `payload` is the tool's own parsed
 *                        JSON reply verbatim. transcript.js strikes the
 *                        proposed words (`.w.proposed`/`.pause.proposed`,
 *                        distinct from `.w.gone`) and shows an Apply/Dismiss
 *                        banner; word indices are read straight off
 *                        `payload` (never re-derived — CLAUDE.md: the page
 *                        renders ops' own return value).
 *
 * `state` is `ops.timeline_view`'s payload verbatim: {project, name,
 * clip_id, clips, source_duration, timeline_duration, timebase, undo_depth,
 * segments, seams, words}. `words` is `null` when the clip has no
 * transcript (`transcript_missing: true` alongside it); each present word
 * carries `index`, `text`, `start`/`end` (source seconds), `present`,
 * `covered`, `partial`, `timeline_start`/`timeline_end` (`null` if cut),
 * `paragraph` (0-based, word-order-driven — PLAN.md § Read-model
 * additions), `suspect` when its duration looks inflated, and `pause_after`
 * — `{duration, present}` — only on a word whose gap to the next word
 * cleared `ops.PAUSE_MARKER_MIN` server-side; there is no client-side
 * threshold to keep in sync with it.
 */

import { $, el, fmt, plural, secs, clampFloating } from "./dom.js";

let ctx = null;

/* -- state private to this pane -------------------------------------------
 * None of this is the read model — it is what the read model looks like
 * through a selection and a display toggle, both purely local until an op
 * actually runs.
 */
let sel = null; // {first, last, throughPause} inclusive word indices, or null
let agentPlan = null; // {tool, input, payload} | null — the last 'agent-plan'
  // event, held until Applied or Dismissed. Independent of `sel`: a person
  // can have their own selection open at the same time as a standing agent
  // proposal — the two are drawn with distinct classes (`.sel` vs
  // `.proposed`) and never share state.
let showCuts = true; // struck through in place (true) or omitted (false)
let playingIndex = null; // state.words[i].index currently under the playhead
let wordIndexMap = new Map(); // word.index -> word, refreshed on every update()
let dragging = null; // {anchor, moved} while a selection drag is live
let focusedIndex = null; // word index of the current roving-tabindex target
  // (F6) — tracked separately from DOM focus, and by word index rather than
  // by node, because renderWords() destroys and rebuilds every `.w` span on
  // every op, the show-cuts toggle, and a background 'project-changed'
  // reload alike. Set by the focusin listener in init() and read (never
  // cleared) across a render-caused blur, since removing the focused node
  // from the DOM fires focusout with no `relatedTarget` — see
  // handleFocusOut's own comment for the genuine-departure case.

/* -- persistent DOM built once in init(), re-appended on every render, per
 * tier 2's playheadEl() trick: clearing the pane detaches these nodes, but
 * detaching does not destroy them or their listeners, so re-appending the
 * same instances is enough. ------------------------------------------- */
let toggleRow = null;
let toolbarEl = null;
let popoverEl = null;
let infoEl = null;
let padInput = null;
let confirmInput = null;
let optsBtn = null;
let restoreBtnEl = null;
let agentPlanBannerEl = null;
let agentPlanInfoEl = null;
let agentPlanWarnEl = null;
let agentPlanConfirmLabel = null;
let agentPlanConfirmInput = null;

function wordLabel(word) {
  const where = word.present ? `plays at ${fmt(word.timeline_start)}` : "cut — it is not in the timeline";
  const parts = [`#${word.index}  source ${secs(word.start)}–${secs(word.end)}`, where];
  if (word.partial) parts.push(`survives in part: ${secs(word.covered)} of ${secs(word.end - word.start)}`);
  if (word.suspect) {
    const dur = word.suspect.duration !== undefined ? secs(word.suspect.duration) : "?";
    const limit = word.suspect.limit !== undefined ? secs(word.suspect.limit) : "?";
    parts.push(`suspect duration — claims ${dur}, over the ${limit} limit; it may be hiding a retake`);
  }
  return parts.join("\n");
}

function rows(pairs) {
  const table = el("table", "rows");
  for (const [key, value] of pairs) {
    const tr = el("tr");
    tr.append(el("td", null, key), el("td", null, value));
    table.append(tr);
  }
  return table;
}

/* -- building the toolbars once ------------------------------------------ */

function buildToggleRow() {
  const row = el("div", "transcript-toolbar");
  const btn = el("button", "toggle on", "cuts shown");
  btn.title = "show cut words struck through, or hide them from the document entirely";
  btn.addEventListener("click", () => {
    showCuts = !showCuts;
    btn.classList.toggle("on", showCuts);
    btn.textContent = showCuts ? "cuts shown" : "cuts hidden";
    const view = ctx.getView();
    if (view && view.words) {
      const hadFocus = $("transcript").contains(document.activeElement);
      renderWords(view);
      applyRovingTabIndex(hadFocus);
      paintSelection();
      paintProposed();
      reapplyPlaying();
      refreshToolbar();
    }
  });
  toggleRow = row;
  row.append(btn);
}

function buildSelectionToolbar() {
  const bar = el("div", "selection-toolbar");
  bar.hidden = true;

  const previewBtn = el("button", null, "Preview");
  const cutBtn = el("button", null, "Cut");
  const keepBtn = el("button", null, "Keep only");
  const restoreBtn = el("button", null, "Restore");
  const opts = el("button", "toggle", "pad");

  const popover = el("div", "toolbar-popover");
  popover.hidden = true;
  const info = el("div");

  const padLabel = el("label", null, "pad ");
  const pad = document.createElement("input");
  pad.type = "number";
  pad.step = "0.05";
  pad.min = "0";
  pad.value = "0";
  pad.style.width = "5em";
  padLabel.append(pad);

  const confirmLabel = el("label", null, "");
  const confirm = document.createElement("input");
  confirm.type = "checkbox";
  confirmLabel.append(confirm, document.createTextNode(" allow a suspect boundary"));

  popover.append(info, padLabel, confirmLabel);

  opts.addEventListener("click", () => {
    popover.hidden = !popover.hidden;
    opts.classList.toggle("on", !popover.hidden);
  });

  previewBtn.addEventListener("click", () => runOp(true, "cut"));
  cutBtn.addEventListener("click", () => runOp(false, "cut"));
  keepBtn.addEventListener("click", () => runOp(false, "keep"));
  restoreBtn.addEventListener("click", () => runOp(false, "restore"));

  bar.append(previewBtn, cutBtn, keepBtn, restoreBtn, opts, popover);

  toolbarEl = bar;
  popoverEl = popover;
  infoEl = info;
  padInput = pad;
  confirmInput = confirm;
  optsBtn = opts;
  restoreBtnEl = restoreBtn;
}

/**
 * docs/plans/STUDIO.md Step 02 item 6 — a persistent banner (same re-append-not-rebuild
 * discipline as `toolbarEl`) offering Apply/Dismiss over the agent's last
 * plan-mode cut proposal. `#agent-plan-banner` needs its own
 * `[hidden]{display:none}` companion rule (builder 2 / app.css) — the same
 * author-`display:`-beats-UA-`[hidden]` trap every other floating panel here
 * already has to account for.
 */
function buildAgentPlanBanner() {
  const bar = el("div", "agent-plan-banner");
  bar.id = "agent-plan-banner";
  bar.hidden = true;

  const info = el("div", "quote");
  const warn = el("div", "warn bad");
  warn.hidden = true;

  const confirmLabel = el("label", null, "");
  const confirm = document.createElement("input");
  confirm.type = "checkbox";
  confirmLabel.append(confirm, document.createTextNode(" allow a suspect boundary"));
  confirmLabel.hidden = true;

  const actions = el("div", "plan-actions");
  const applyBtn = el("button", null, "Apply");
  const dismissBtn = el("button", null, "Dismiss");
  applyBtn.addEventListener("click", () => applyAgentPlan());
  dismissBtn.addEventListener("click", () => dismissAgentPlan());
  actions.append(applyBtn, dismissBtn);

  bar.append(info, warn, confirmLabel, actions);

  agentPlanBannerEl = bar;
  agentPlanInfoEl = info;
  agentPlanWarnEl = warn;
  agentPlanConfirmLabel = confirmLabel;
  agentPlanConfirmInput = confirm;
}

/* -- rendering the document ----------------------------------------------- */

function buildParagraphs(words) {
  const paras = [];
  let current = null;
  for (const w of words) {
    if (!current || current.paragraph !== w.paragraph) {
      current = { paragraph: w.paragraph, words: [] };
      paras.push(current);
    }
    current.words.push(w);
  }
  return paras;
}

function paragraphAnchorTime(paraWords) {
  const present = paraWords.find((w) => w.present);
  return present ? present.timeline_start : null;
}

function renderWords(state) {
  const pane = $("transcript");
  pane.textContent = "";
  pane.append(toggleRow);

  // The worse half of `off_timeline`, and the reason it is reported at all: a
  // transcribed clip that is not in the edit has every word `present: false`,
  // so this pane draws the whole document struck through and it reads as a cut
  // somebody made. It is the opposite — nothing was cut, the clip was never on
  // the track — and no amount of looking at the words themselves can tell the
  // two apart. `.warn` is app.css's existing flagged-condition style; this
  // needs no class of its own.
  if (state.off_timeline) {
    pane.append(
      el(
        "div",
        "warn",
        `${state.clip_id} is not on the timeline, so every word below reads as cut. ` +
          `Nothing here was removed — this clip is footage a cue points at, not part of ` +
          `the edit's own track, and Restore has nothing to put back.`,
      ),
    );
  }

  wordIndexMap = new Map();

  const frag = document.createDocumentFragment();
  for (const para of buildParagraphs(state.words)) {
    const p = el("p", "para");
    const anchor = paragraphAnchorTime(para.words);
    const ts = el("span", "para-ts", anchor === null ? "—" : fmt(anchor));
    if (anchor !== null) {
      ts.style.cursor = "pointer";
      ts.title = "seek here";
      ts.addEventListener("click", () => ctx.player.seek(anchor));
    }
    p.append(ts);
    for (const word of para.words) {
      wordIndexMap.set(word.index, word);
      if (!word.present && !showCuts) continue; // hidden entirely, not just dimmed
      const node = el("span", "w", word.text);
      if (!word.present) node.classList.add("gone");
      if (word.partial) node.classList.add("partial");
      if (word.suspect) node.classList.add("suspect");
      node.tabIndex = -1; // F6: roving tabindex — applyRovingTabIndex() below
      // promotes exactly one span to 0 after every render; every other one
      // starts, and normally stays, out of the Tab order entirely.
      node.dataset.i = String(word.index);
      node.title = wordLabel(word);
      p.append(node);
      if (word.pause_after) {
        const pa = word.pause_after;
        if (pa.present || showCuts) {
          const mark = el("span", "pause", `[${pa.duration.toFixed(1)}s]`);
          mark.dataset.after = String(word.index);
          if (!pa.present) mark.classList.add("gone");
          p.append(mark);
        }
      }
      p.append(document.createTextNode(" "));
    }
    frag.append(p);
  }
  pane.append(frag);
  pane.append(toolbarEl);
  pane.append(agentPlanBannerEl);
}

function paintSelection() {
  for (const node of $("transcript").querySelectorAll(".w")) {
    const i = Number(node.dataset.i);
    node.classList.toggle("sel", sel !== null && i >= sel.first && i <= sel.last);
  }
  for (const node of $("transcript").querySelectorAll(".pause")) {
    const after = Number(node.dataset.after);
    const interior = sel !== null && after >= sel.first && after < sel.last;
    const trailing = sel !== null && sel.throughPause && after === sel.last;
    node.classList.toggle("sel", interior || trailing);
  }
}

/* -- agent plans (docs/plans/STUDIO.md Step 02 item 6) --------------------------------
 *
 * `agentPlan.payload` is a cut-family op's own return value — the word
 * indices below are read straight off it, never re-derived: CLAUDE.md's rule
 * that the page renders ops' own return value, and never a re-derivation.
 * If a payload shape ever lacked word indices, that would be a finding to
 * report against the op (builder 1's territory), never a reason to compute
 * one here.
 */

/** The set of this pane's own word indices the plan proposes cutting —
 * empty (not an error) when the plan addresses a different clip than the one
 * currently loaded here, since a plan can span clips this transcript pane
 * is not showing. */
function proposedIndices(plan, view) {
  const indices = new Set();
  const payload = plan?.payload;
  const clipId = view?.clip_id;
  if (!payload || !clipId) return indices;
  if (plan.tool === "cut_by_transcript") {
    if (plan.input?.clip_id !== clipId) return indices;
    for (const applied of payload.applied || []) {
      const first = applied.first_word;
      const last = applied.last_word;
      if (typeof first === "number" && typeof last === "number") {
        for (let i = first; i <= last; i++) indices.add(i);
      }
    }
  } else if (plan.tool === "cut_by_time") {
    for (const applied of payload.applied || []) {
      for (const piece of applied.pieces || []) {
        if (piece.clip_id !== clipId) continue;
        for (const w of piece.words_overlapped || []) {
          if (typeof w?.index === "number") indices.add(w.index);
        }
      }
    }
  }
  return indices;
}

/** Strikes the proposed words in place — `.w.proposed`/`.pause.proposed`, a
 * class distinct from `.w.gone` (an applied cut) so a word can be BOTH
 * already-cut and separately proposed-for-cutting without the styles
 * fighting (a plan can propose cutting through what a prior op already
 * removed — `already_cut` in the op's own reply). Called alongside
 * `paintSelection()` everywhere that function is, so it survives every
 * `renderWords()` rebuild without being baked into the render loop itself. */
function paintProposed() {
  const pane = $("transcript");
  if (!pane) return;
  for (const node of pane.querySelectorAll(".w.proposed, .pause.proposed")) node.classList.remove("proposed");
  if (!agentPlan) return;
  const indices = proposedIndices(agentPlan, ctx.getView());
  for (const node of pane.querySelectorAll(".w")) {
    if (indices.has(Number(node.dataset.i))) node.classList.add("proposed");
  }
  for (const node of pane.querySelectorAll(".pause")) {
    if (indices.has(Number(node.dataset.after))) node.classList.add("proposed");
  }
}

function refreshAgentPlanBanner() {
  if (!agentPlanBannerEl) return;
  if (!agentPlan) {
    agentPlanBannerEl.hidden = true;
    return;
  }
  agentPlanBannerEl.hidden = false;
  const indices = proposedIndices(agentPlan, ctx.getView());
  agentPlanInfoEl.textContent =
    agentPlan.tool === "cut_by_transcript"
      ? `Agent proposes cutting ${indices.size} word(s): "${(agentPlan.payload.applied || [])
          .map((a) => a.text)
          .filter(Boolean)
          .join(" … ")}"`
      : `Agent proposes cutting ${indices.size} word(s) across ${agentPlan.input?.spans?.length ?? 0} span(s).`;
  const suspects = agentPlan.payload?.suspect_boundaries?.length || 0;
  agentPlanWarnEl.hidden = suspects === 0;
  if (suspects) {
    agentPlanWarnEl.textContent = `${plural(suspects, "suspect boundary", "suspect boundaries")} flagged — confirm below to apply anyway.`;
  }
  agentPlanConfirmLabel.hidden = suspects === 0;
}

/** Apply — re-posts the SAME op with `plan: false`, translating between the
 * MCP tool's own `cut`/`keep` body shape and `_cut_words`'s `mode`+`ranges`
 * shape (both already route to the identical `ops.cut_by_transcript`/
 * `ops.cut_by_time` call — this is not a re-derivation of the op's
 * semantics, just picking the right HTTP body for the same call). */
async function applyAgentPlan() {
  if (!agentPlan || !ctx) return;
  const { tool, input } = agentPlan;
  const suspects = (agentPlan.payload?.suspect_boundaries?.length || 0) > 0;
  if (suspects && !agentPlanConfirmInput.checked) return;
  const body =
    tool === "cut_by_transcript"
      ? {
          clip_id: input.clip_id,
          ranges: input.cut ?? input.keep,
          mode: input.cut ? "cut" : "keep",
          pad: input.pad ?? 0,
          through_pause: Boolean(input.through_pause),
          confirm_suspect: suspects,
          plan: false,
        }
      : {
          spans: input.spans,
          pad: input.pad ?? 0,
          confirm_suspect: suspects,
          plan: false,
        };
  const route = tool === "cut_by_transcript" ? "/api/cut" : "/api/cut-at";
  let payload = null;
  let error = null;
  try {
    payload = await ctx.api(route, body);
  } catch (err) {
    error = err.message;
    ctx.emit("toast", error);
  }
  ctx.emit("op-result", { payload, error });
  if (!error) {
    agentPlan = null;
    paintProposed();
    refreshAgentPlanBanner();
  }
}

function dismissAgentPlan() {
  agentPlan = null;
  paintProposed();
  refreshAgentPlanBanner();
}

function reapplyPlaying() {
  // Restores the highlight after a re-render without scrolling — a re-render
  // triggered by an edit (or the show-cuts toggle) should not yank the
  // reading position, only the live 'playing-word' event should.
  if (playingIndex === null) return;
  const node = $("transcript").querySelector(`.w[data-i="${playingIndex}"]`);
  if (node) node.classList.add("playing");
}

function paintPlayingWord(index) {
  if (playingIndex !== null && playingIndex !== index) {
    const old = $("transcript").querySelector(`.w[data-i="${playingIndex}"]`);
    if (old) old.classList.remove("playing");
  }
  playingIndex = index;
  const node = $("transcript").querySelector(`.w[data-i="${index}"]`);
  if (node) {
    node.classList.add("playing");
    node.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

/* -- keyboard: roving tabindex over words (F6) ----------------------------
 * A person tabs into the transcript once, lands on one word, and moves
 * within it with the arrow keys — the standard "roving tabindex" pattern:
 * every `.w` starts tabindex="-1" (renderWords()) and exactly one is
 * promoted to "0" at a time, here.
 */

function handleFocusIn(event) {
  const node = event.target.closest(".w");
  if (node) focusedIndex = Number(node.dataset.i);
}

function handleFocusOut(event) {
  // A relatedTarget outside #transcript is a genuine focus departure (Tab
  // out, a click into the agent composer) — drop the target so the next
  // render's survival chain does not pull focus back to a word the person
  // deliberately left. A render-caused blur (renderWords() just deleted the
  // focused span wholesale, mid-op or on the show-cuts toggle) reports
  // relatedTarget === null, so this leaves focusedIndex alone and lets
  // applyRovingTabIndex()'s own hadFocus snapshot (captured by the caller
  // BEFORE renderWords() ran) decide whether to restore it.
  if (event.relatedTarget && !$("transcript").contains(event.relatedTarget)) {
    focusedIndex = null;
  }
}

// Walks the currently RENDERED `.w` spans in document order — not
// wordIndexMap, which also holds indices with no span when a cut word is
// hidden by the show-cuts toggle ("hidden entirely, not just dimmed", per
// renderWords()'s own comment). Arrow keys must only ever land on something
// a person can actually see.
function adjacentWordIndex(from, dir) {
  const order = Array.from($("transcript").querySelectorAll(".w")).map((n) => Number(n.dataset.i));
  const pos = order.indexOf(from);
  if (pos === -1) return null;
  const next = pos + dir;
  return next >= 0 && next < order.length ? order[next] : null;
}

// Marks exactly one `.w` span as the roving tab stop. Does not itself move
// DOM focus — callers decide that, since a render-time re-application should
// not steal focus and a keyboard move always should.
function setRovingTarget(index) {
  for (const node of $("transcript").querySelectorAll(".w")) {
    node.tabIndex = Number(node.dataset.i) === index ? 0 : -1;
  }
}

function focusWord(index) {
  const node = $("transcript").querySelector(`.w[data-i="${index}"]`);
  if (node) node.focus(); // triggers focusin -> handleFocusIn keeps focusedIndex current
}

/**
 * Re-marks exactly one `.w` span tabindex="0" after renderWords() has just
 * rebuilt every one of them from scratch, and restores real DOM focus to it
 * if focus was inside the pane a moment ago. `hadFocus` MUST be read by the
 * caller BEFORE calling renderWords() — by the time this runs,
 * document.activeElement has already moved (removing a focused node blurs
 * it), so asking here is always too late; that is the entire reason
 * `focusedIndex` is tracked by word index rather than read back off the DOM.
 *
 * Priority: the word that already held the roving target, if it still has a
 * rendered span (the ordinary case — an op or a background reload that left
 * the reading position untouched); else the current selection's first word;
 * else the word under the playhead; else the first present word in document
 * order. The fallbacks are what the pane needs the moment the previous
 * target's own word stops being addressable — cut by the very op that
 * triggered this render, or hidden by a show-cuts toggle — which a plain
 * "keep the same index" rule cannot cover on its own.
 */
function applyRovingTabIndex(hadFocus) {
  const pane = $("transcript");
  const hasSpan = (i) => i !== null && pane.querySelector(`.w[data-i="${i}"]`) !== null;
  let target = null;
  if (hasSpan(focusedIndex)) target = focusedIndex;
  else if (sel && hasSpan(sel.first)) target = sel.first;
  else if (hasSpan(playingIndex)) target = playingIndex;
  else {
    for (const w of wordIndexMap.values()) {
      if (w.present) {
        target = w.index;
        break;
      }
    }
  }
  setRovingTarget(target);
  focusedIndex = target;
  if (hadFocus && target !== null) focusWord(target);
}

function handleTranscriptKeyDown(event) {
  const node = event.target.closest(".w");
  if (!node) return;
  const i = Number(node.dataset.i);

  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    const dir = event.key === "ArrowLeft" ? -1 : 1;
    const nextIndex = adjacentWordIndex(i, dir);
    if (nextIndex === null) return;
    event.preventDefault();
    // Same collision the Enter/Space branch below already stops: this event
    // also bubbles to player.js's window-level keydown listener, whose own
    // unshifted/shifted ArrowLeft/ArrowRight is a frame/second transport
    // step. A focused .w span is not an INPUT/TEXTAREA/SELECT and is not
    // contentEditable, so isTypingTarget there does not exclude it — without
    // stopping the bubble here, every arrow move through the transcript
    // would also pause playback and step the preview out from under it.
    event.stopPropagation();
    if (event.shiftKey) {
      // Mirrors handleMouseDown's shift-click branch exactly — same
      // trailingPause call, same [index, isPause] pairs — so a
      // keyboard-built selection and a mouse-built one are indistinguishable
      // to runOp/refreshToolbar ("same sel shape as the mouse one"). A word
      // reached by arrow key is never a `.pause` marker — those are never a
      // roving stop, same as they are never resolveIndex's own index — so
      // isPause is always false here.
      const anchorFirst = sel ? sel.first : i;
      const anchorLast = sel ? sel.last : i;
      const first = Math.min(anchorFirst, nextIndex);
      const last = Math.max(anchorLast, nextIndex);
      sel = {
        first,
        last,
        throughPause: trailingPause(last, [nextIndex, false], [anchorLast, sel ? sel.throughPause : false]),
      };
    } else {
      sel = { first: nextIndex, last: nextIndex, throughPause: false };
    }
    paintSelection();
    refreshToolbar();
    setRovingTarget(nextIndex);
    focusWord(nextIndex);
    return;
  }

  if (event.key === "Enter" || event.key === " ") {
    // Space also bubbles to player.js's window-level play/pause listener —
    // without stopping it here, pressing Space on a focused word would both
    // seek (this handler) AND toggle playback (player.js), the exact
    // double-fire the shared contract's own notes call out. Enter has no
    // such collision but is stopped too so the two keys behave identically.
    event.preventDefault();
    event.stopPropagation();
    const word = wordIndexMap.get(i);
    sel = { first: i, last: i, throughPause: false };
    paintSelection();
    refreshToolbar();
    if (word) ctx.player.seekWord(word);
  }
}

/* -- the floating selection toolbar --------------------------------------- */

function findAnchorNode() {
  if (!sel) return null;
  for (let i = sel.last; i >= sel.first; i--) {
    const node = $("transcript").querySelector(`.w[data-i="${i}"]`);
    if (node) return node;
  }
  return null;
}

function renderPopoverInfo() {
  infoEl.textContent = "";
  if (!sel) return;
  const first = wordIndexMap.get(sel.first);
  const last = wordIndexMap.get(sel.last);
  if (!first || !last) return;

  // The neighbours either side are the point of the echo (CLAUDE.md): a
  // range one word past the intended phrase has to read correctly alone.
  const quote = el("div", "quote");
  const context = 3;
  for (let i = Math.max(0, sel.first - context); i <= sel.last + context; i++) {
    const w = wordIndexMap.get(i);
    if (!w) continue;
    const inside = i >= sel.first && i <= sel.last;
    quote.append(el("span", inside ? "hit" : "ctx", w.text + " "));
  }
  infoEl.append(quote);

  const count = sel.last - sel.first + 1;
  const infoRows = [
    ["words", `${count} (#${sel.first}–#${sel.last})`],
    ["source", `${secs(first.start)} – ${secs(last.end)}`],
  ];
  if (sel.throughPause && last.pause_after) {
    infoRows.push(["+ trailing pause", secs(last.pause_after.duration)]);
  }
  infoEl.append(rows(infoRows));

  if (first.suspect || last.suspect) {
    infoEl.append(
      el(
        "div",
        "warn",
        "A boundary word here claims a suspect duration — it may be hiding a " +
          "retake rather than ending where it says. Cutting through it needs " +
          '"allow a suspect boundary" below.',
      ),
    );
  }
}

// Restore only makes sense over a selection that actually covers struck
// (cut) text — offering it over live words would just be a confusing no-op
// (`already_present: true`), so it stays hidden until the selection needs it.
function selectionHasCutWord() {
  if (!sel) return false;
  for (let i = sel.first; i <= sel.last; i++) {
    const w = wordIndexMap.get(i);
    if (w && !w.present) return true;
  }
  return false;
}

function refreshToolbar() {
  if (!sel) {
    toolbarEl.hidden = true;
    popoverEl.hidden = true;
    optsBtn.classList.remove("on");
    return;
  }
  const anchor = findAnchorNode();
  const rawLeft = anchor ? anchor.offsetLeft : 0;
  const rawTop = anchor ? anchor.offsetTop + anchor.offsetHeight + 4 : 0;
  // Unhide before measuring offsetWidth/offsetHeight below — a hidden
  // element reports a zero-size rect (dom.js's clampFloating comment), which
  // would clamp the toolbar to the pane's top-left corner every time.
  toolbarEl.hidden = false;
  restoreBtnEl.hidden = !selectionHasCutWord();
  renderPopoverInfo();
  // F4: unclamped, this drop point rendered 169px past #transcript's right
  // edge, measured live — Cut/Keep only sitting off the pane with no error,
  // just nothing a person could see or click (the same class of bug
  // timeline.js's cue toolbar already had fixed once). This pane folds in no
  // scroll of its own, so the bounds are simply its content box.
  const container = $("transcript");
  const { left, top } = clampFloating(
    rawLeft,
    rawTop,
    toolbarEl.offsetWidth,
    toolbarEl.offsetHeight,
    0,
    container.clientWidth,
    0,
    container.clientHeight,
  );
  toolbarEl.style.left = `${left}px`;
  toolbarEl.style.top = `${top}px`;
}

/* -- running an op ---------------------------------------------------------
 * Preview is `plan: true` on the same `/api/cut` call Cut makes — PLAN.md §
 * Where the cut controls go says this explicitly: not its own endpoint, so
 * the two paths cannot drift apart. The result never renders here; it is
 * handed to the bus and agent.js draws it into the feed.
 */
async function runOp(planned, mode) {
  if (!sel || !ctx) return;
  const view = ctx.getView();
  if (!view) return;
  const ranges = [[sel.first, sel.last]];
  const pad = Number(padInput.value) || 0;
  let payload = null;
  let error = null;
  try {
    // Restore has its own route and its own (smaller) body shape — no
    // mode/confirm_suspect/through_pause, since there is no suspect-duration
    // guard on restoring and nothing left to extend through a pause with.
    payload =
      mode === "restore"
        ? await ctx.api("/api/restore", { clip_id: view.clip_id, ranges, pad, plan: planned })
        : await ctx.api("/api/cut", {
            clip_id: view.clip_id,
            ranges,
            mode,
            pad,
            confirm_suspect: Boolean(confirmInput.checked),
            through_pause: Boolean(sel.throughPause),
            plan: planned,
          });
  } catch (err) {
    error = err.message;
    ctx.emit("toast", error);
  }
  ctx.emit("op-result", { payload, error });
  if (!error && !planned) {
    // The write landed — 'project-changed' will bring a fresh view through
    // the normal update() path; clear the selection now rather than wait,
    // so the toolbar does not linger over words that just moved.
    sel = null;
    paintSelection();
    refreshToolbar();
  }
}

/* -- selection: click, shift-click, drag ---------------------------------- */

function clearSelection() {
  if (!sel) return;
  sel = null;
  paintSelection();
  refreshToolbar();
}

// A `.pause` marker resolves to the word index it trails, never an index of
// its own — it is not in `wordIndexMap` and never becomes an addressable
// `sel.first`/`sel.last` value on its own.
function resolveIndex(node) {
  return Number(node.classList.contains("pause") ? node.dataset.after : node.dataset.i);
}

// `throughPause` is a property of the selection's TRAILING edge, so it must be
// computed from whichever bound ends up at `last` — never from the node that
// happened to move. Computing it from the moving node alone silently cancelled
// a chosen trailing pause on the two ordinary gestures that leave `last` where
// it is: shift-clicking leftward to add context, and dragging backward from the
// marker. Both bounds are offered here as [index, isPause] and the one sitting
// at `last` decides.
function trailingPause(last, ...bounds) {
  return bounds.some(([index, isPause]) => isPause && index === last);
}

function handleMouseDown(event) {
  if (toolbarEl.contains(event.target) || toggleRow.contains(event.target)) return;
  const node = event.target.closest(".w, .pause");
  if (!node) {
    clearSelection();
    return;
  }
  event.preventDefault();
  const i = resolveIndex(node);
  const isPause = node.classList.contains("pause");
  if (event.shiftKey && sel) {
    const first = Math.min(sel.first, i);
    const last = Math.max(sel.last, i);
    sel = {
      first,
      last,
      throughPause: trailingPause(last, [i, isPause], [sel.last, sel.throughPause]),
    };
  } else {
    dragging = { anchor: i, anchorPause: isPause, moved: false };
    sel = { first: i, last: i, throughPause: isPause };
  }
  paintSelection();
  refreshToolbar();
}

function handleMouseOver(event) {
  if (!dragging) return;
  const node = event.target.closest(".w, .pause");
  if (!node) return;
  const i = resolveIndex(node);
  const isPause = node.classList.contains("pause");
  if (i !== dragging.anchor) dragging.moved = true;
  const first = Math.min(dragging.anchor, i);
  const last = Math.max(dragging.anchor, i);
  sel = {
    first,
    last,
    throughPause: trailingPause(last, [i, isPause], [dragging.anchor, dragging.anchorPause]),
  };
  paintSelection();
  refreshToolbar();
}

function handleMouseUp() {
  if (dragging && !dragging.moved) {
    // A plain click on one word is also "play from here" — the fastest way
    // to check whether a cut landed where it reads like it did.
    const word = wordIndexMap.get(dragging.anchor);
    if (word) ctx.player.seekWord(word);
  }
  dragging = null;
}

/* -- the two exported entry points ----------------------------------------- */

export function init(passedCtx) {
  ctx = passedCtx;
  buildToggleRow();
  buildSelectionToolbar();
  buildAgentPlanBanner();

  $("transcript").append(el("p", "pane-placeholder", "Loading…"));

  const pane = $("transcript");
  pane.addEventListener("mousedown", handleMouseDown);
  pane.addEventListener("mouseover", handleMouseOver);
  window.addEventListener("mouseup", handleMouseUp);
  pane.addEventListener("keydown", handleTranscriptKeyDown);
  pane.addEventListener("focusin", handleFocusIn);
  pane.addEventListener("focusout", handleFocusOut);

  ctx.on("playing-word", ({ index }) => paintPlayingWord(index));
  ctx.on("agent-plan", (plan) => {
    agentPlan = plan;
    paintProposed();
    refreshAgentPlanBanner();
  });
}

/** The project has no timeline yet — `/api/view` refused, so there is no
 * state to draw. Says what is missing and where the next step is. */
export function unseeded() {
  const pane = $("transcript");
  pane.textContent = "";
  pane.append(
    el(
      "p",
      "pane-placeholder",
      "No timeline yet. Add footage in the Assets tab, or ask the agent to start the edit.",
    ),
  );
  sel = null;
  wordIndexMap = new Map();
  focusedIndex = null;
}

export function update(state) {
  const pane = $("transcript");

  if (!state) {
    pane.textContent = "";
    pane.append(el("p", "pane-placeholder", "Nothing loaded."));
    sel = null;
    wordIndexMap = new Map();
    focusedIndex = null;
    return;
  }

  if (!state.words) {
    pane.textContent = "";
    // Two different reasons this pane is empty, and naming the wrong one sends
    // a person somewhere that cannot help: `transcribe` does not put a clip on
    // the timeline, so advising it for a clip that is merely off the edit has
    // one effect, which is to produce the *worse* state below — a full
    // transcript drawn entirely struck through. `off_timeline` is
    // `timeline_view`'s own answer and this pane never re-derives it from
    // `segments[].clip_id`.
    pane.append(
      el(
        "p",
        "pane-placeholder",
        state.off_timeline
          ? `${state.clip_id} is registered but is not on the timeline, so there are no ` +
            `words here to address. It is footage a cue can point at rather than part of ` +
            `the edit's own track — the timeline below is what actually plays.`
          : `${state.clip_id} has no transcript, so there are no words to address. ` +
            `Run \`proofcut transcribe ${state.clip_id}\` or attach a whisper JSON.`,
      ),
    );
    sel = null;
    wordIndexMap = new Map();
    focusedIndex = null;
    return;
  }

  // A selection may reference indices from a clip this update just replaced
  // (e.g. the clip picker changed) — drop it rather than show a stale range
  // against new words.
  if (sel && (sel.last >= state.words.length || !state.words.some((w) => w.index === sel.first))) {
    sel = null;
  }

  const hadFocus = pane.contains(document.activeElement);
  renderWords(state);
  applyRovingTabIndex(hadFocus);
  paintSelection();
  paintProposed();
  reapplyPlaying();
  refreshToolbar();
  refreshAgentPlanBanner();
}
