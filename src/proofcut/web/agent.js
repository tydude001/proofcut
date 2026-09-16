/**
 * agent.js — the agent panel plus the in-window render UI:
 * PLAN.md § "The agent panel, in
 * mechanism" and § "Finishing — the render happens in the window".
 *
 * See transcript.js's header comment for the full pane-module interface —
 * `ctx` shape, bus event names, and the `state` shape — this file does not
 * repeat it.
 *
 * What this file draws, and nothing more (CLAUDE.md: the web UI draws and it
 * plays, it never decides):
 *
 *   - a composer that POSTs {prompt, model} to /api/agent, and a Stop
 *     control that POSTs to /api/agent/stop (PLAN.md § The agent panel, in
 *     mechanism) — `model` is the #agent-model-select value; the server
 *     (`AgentSession.send`) decides whether it actually changed anything,
 *     this file only decides whether to draw a "starting fresh" notice
 *     first, off its own record of what the live subprocess is running;
 *   - the `agent` SSE stream (stream-json passthrough) turned into a
 *     Daydream-style tool-progress list — consecutive tool_use blocks chain
 *     into one running checklist ("Reading transcript" → "Editing
 *     transcript" → "Done!"), assistant prose renders as its own entry and
 *     breaks the chain, and a shape this pane does not recognise degrades to
 *     a compact raw entry rather than throwing;
 *   - ONE feed for everything that happened to the edit (PLAN.md § Where the
 *     cut controls go): `op-result` — a person's own cut/keep/undo — renders
 *     as a system entry alongside the agent's own turns, in arrival order;
 *   - the render job: it does not initiate a render (the top bar's Export
 *     button already POSTs /api/render itself — see the note at the bottom
 *     of this file) but it owns everything after that — a Stop control for
 *     the running job, and a completion card reporting what the file
 *     actually is (dimensions, duration) plus the four verification checks,
 *     because success here is what ffprobe said, not that the job finished
 *     (PLAN.md § Finishing);
 *   - `agent-plan` on the shared bus (docs/plans/STUDIO.md Step 02 item 6): when a
 *     tool_result lands for a `cut_by_transcript`/`cut_by_time` call whose
 *     matching tool_use had `plan: true`, this file parses the tool's own
 *     JSON reply and emits `{tool, input, payload}` VERBATIM — transcript.js
 *     is the only consumer, drawing the proposed cut on the words and
 *     offering Apply/Dismiss. This file decides only WHETHER to emit (off
 *     the tool name and its own recorded input), never what the payload
 *     means.
 *
 * Every DOM node this pane needs beyond what index.html already provides
 * (#agent-feed, #agent-composer, #agent-prompt, #agent-send, #agent-stop) is
 * built here at runtime — the progress list, the completion card, the
 * per-job Stop button — using only the classes app.css already documents for
 * this pane (.agent-progress, .agent-progress-step, .completion-card,
 * .check-row, .check-badge, .rows) rather than inventing new ones this file
 * cannot style.
 */

import { $, el, fmt, progressText } from "./dom.js";

let ctx = null;
let busy = false;

// The model chip in the composer hint (item 1, docs/plans/DAYDREAM.md § Agent panel).
// Set once per subprocess lifetime from the stream-json `system`/`init`
// event's own `model` field — never guessed, and never regressed by a
// malformed event. `AgentSession.send()` reuses the live subprocess across
// turns and only `_spawn()` emits a fresh `init`, so this reads as "the
// model the *current* subprocess is running", which is right even across a
// New Task reset (the next init overwrites it).
let currentModel = null;

function setModel(model) {
  if (typeof model !== "string" || !model) return;
  currentModel = model;
  const chip = $("agent-model");
  if (chip) chip.textContent = ` · ${model}`;
}

// The #agent-model-select value — "" for "let claude choose its own
// default", else a model id. Distinct from `currentModel` above, which is
// what the *live* subprocess actually reports running: the two can differ
// (picking a new value here does nothing to a live subprocess until the next
// prompt is sent) and are reconciled by `spawnedModel` below, not by
// comparing these two directly — `currentModel` is claude's own resolved
// name for "Default" (e.g. "claude-sonnet-5"), not the empty string this
// holds for the same choice.
let selectedModel = "";

// The model value actually sent with the most recent prompt this browser
// tab has POSTed, or `null` before the first one. Used only to decide
// whether *this tab's* next Send is a model change worth announcing in the
// feed before the server silently restarts the conversation to honour it
// (AgentSession.send's own stale-process check) — not a record of what any
// other tab or a since-restarted server is actually running.
let spawnedModel = null;

export function getSelectedModel() {
  return selectedModel;
}

/** Restore a saved model choice into the `<select>` (Studio Step 04's
 * session contract, item E) — never called from a live change, only from
 * `restoreSession()`, so it never needs to touch `spawnedModel`.
 */
export function setSelectedModel(model) {
  selectedModel = typeof model === "string" ? model : "";
  const select = $("agent-model-select");
  if (select) select.value = selectedModel;
}

/** Shared by "New Task" and a mid-conversation model switch — both start a
 * fresh agent process and must leave nothing on screen claiming a
 * conversation the new process has no memory of.
 */
function resetForNewSession(message) {
  currentProgress = null;
  pendingSteps.clear();
  renderSlots.clear();
  currentModel = null;
  const chip = $("agent-model");
  if (chip) chip.textContent = "";
  setBusy(false);
  closeMention();
  feedEl().textContent = "";
  append(el("p", "pane-placeholder", message));
}

// -- feed plumbing ---------------------------------------------------------

function feedEl() {
  return $("agent-feed");
}

function scrollToBottom() {
  const feed = feedEl();
  feed.scrollTop = feed.scrollHeight;
}

function entry(cls, text) {
  const node = el("div", `agent-entry ${cls}`);
  node.append(el("div", null, text));
  return node;
}

/** Append a fully-built node and keep the feed scrolled to it. */
function append(node) {
  feedEl().append(node);
  scrollToBottom();
}

function setBusy(next) {
  busy = next;
  $("agent-send").disabled = busy;
  $("agent-stop").hidden = !busy;
}

// -- tool-name humanising ----------------------------------------------------

// Every name below is one of server.py's @mcp.tool() functions, which is
// also the whole set an agent turn can ever call — the allowlist is
// `mcp__proofcut__*` and nothing else (PLAN.md § The agent panel, and why it
// does not become a fourth implementation).
const TOOL_LABELS = {
  ping: "Checking connection",
  init: "Initializing project",
  import_media: "Importing media",
  attach_transcript: "Attaching transcript",
  transcribe: "Transcribing",
  get_transcript: "Reading transcript",
  seed_timeline: "Seeding timeline",
  cut_by_transcript: "Editing transcript",
  cut_by_time: "Cutting",
  locate: "Locating words",
  timeline_status: "Checking timeline",
  timeline_view: "Reading timeline",
  undo: "Undoing",
  export: "Rendering",
  add_captions: "Adding captions",
  verify: "Verifying render",
  check_frames: "Checking frames",
  check_black: "Checking for black frames",
  spot_frames: "Sampling frames",
  speech_overlap: "Checking speech overlap",
  attenuate_noises: "Attenuating noise",
};

const MCP_PREFIX = /^mcp__[^_]+__/;

/** A tool_use block's `name` → a short present-participle label. Never
 * throws — a name this pane does not recognise (a future tool, or a name
 * shape that changed) still gets a readable fallback rather than sitting
 * raw in the checklist. */
function humanizeTool(name) {
  if (!name) return "Working";
  const bare = String(name).replace(MCP_PREFIX, "");
  if (TOOL_LABELS[bare]) return TOOL_LABELS[bare];
  const words = bare.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : String(name);
}

/** Best-effort short text out of a tool_result block's `content`, for a
 * hover title — never the primary UI, just a debugging aid. */
function resultPreview(block) {
  const content = block?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => (b && b.type === "text" ? b.text : JSON.stringify(b)))
      .join(" ");
  }
  if (content && typeof content === "object") return JSON.stringify(content);
  return "";
}

/** The base64 images in a tool_result's content, in order — `[]` for a
 * result with none, or whose content is a bare string. Shape verified on a
 * real run: `{type:"image", source:{type:"base64", media_type, data}}`. */
function resultImages(block) {
  const content = block?.content;
  if (!Array.isArray(content)) return [];
  return content
    .filter((b) => b && b.type === "image" && b.source?.type === "base64" && typeof b.source.data === "string")
    .map((b) => ({ media_type: b.source.media_type || "image/png", data: b.source.data }));
}

/** Like `resultPreview`, but returns the tool's own parsed JSON reply rather
 * than a joined string — an MCP tool result's `content` is `[{type:"text",
 * text: "<json>"}]`, so this parses the first text block. Returns `null`
 * (never throws) on anything that is not parseable JSON, so a caller can
 * treat "no plan payload" and "unparseable" the same way. */
function parseResultPayload(block) {
  const content = block?.content;
  if (content && typeof content === "object" && !Array.isArray(content)) return content;
  let text = null;
  if (typeof content === "string") {
    text = content;
  } else if (Array.isArray(content)) {
    const first = content.find((b) => b && b.type === "text" && typeof b.text === "string");
    text = first ? first.text : null;
  }
  if (typeof text !== "string") return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

// -- the tool-progress checklist --------------------------------------------
//
// One `.agent-progress` list per run of consecutive tool_use blocks.
// Assistant prose closes the current list (a person reads "Reading
// transcript ✓" then a sentence, not a sentence stitched mid-checklist) and
// the turn's `result` event, if a list is open, appends a final "Done!" step
// — this is the "Reading transcript → Editing transcript → Done!" shape
// PLAN.md names, drawn as a vertical checklist per the CSS app.css already
// ships for it rather than an inline arrow chain.

let currentProgress = null; // the open .agent-progress list, or null
const pendingSteps = new Map(); // tool_use id -> {step, label}

function openProgress() {
  const wrap = el("div", "agent-entry agent-entry--tool");
  const list = el("div", "agent-progress");
  wrap.append(list);
  append(wrap);
  return list;
}

function addStep(block) {
  const label = humanizeTool(block?.name);
  const step = el("div", "agent-progress-step active", `○ ${label}`);
  if (block?.input) {
    try {
      step.title = JSON.stringify(block.input);
    } catch {
      // Non-serialisable input (a circular structure, in principle) — the
      // checklist still works without a tooltip.
    }
  }
  if (!currentProgress) currentProgress = openProgress();
  currentProgress.append(step);
  // A step is appended inside an entry `append()` already scrolled to, so
  // nothing else scrolls: the first recorded run's checklist grew forty
  // steps below the fold while the pane went on showing "Importing media".
  scrollToBottom();
  // `name`/`input` are carried alongside the checklist bookkeeping so the
  // matching tool_result can tell whether this was a plan-mode cut-family
  // call (docs/plans/STUDIO.md Step 02 item 6) — the checklist itself never reads them.
  if (block?.id) pendingSteps.set(block.id, { step, label, name: block?.name, input: block?.input });
}

function finishStep(toolUseId, ok, preview) {
  const pending = pendingSteps.get(toolUseId);
  if (!pending) return; // a result for a step outside this page's memory — drop it, not a crash
  pending.step.className = "agent-progress-step done";
  pending.step.textContent = `${ok ? "✓" : "✗"} ${pending.label}`;
  if (preview) pending.step.title = preview.slice(0, 400);
  pendingSteps.delete(toolUseId);
}

function closeProgress(final) {
  if (currentProgress && final) {
    currentProgress.append(el("div", "agent-progress-step done", `✓ ${final}`));
  }
  currentProgress = null;
}

// -- parsing one `agent` stream-json record ----------------------------------

function handleAssistantOrUser(data) {
  const role = data.type; // "assistant" | "user"
  const content = data.message?.content;
  const blocks = Array.isArray(content)
    ? content
    : typeof content === "string"
      ? [{ type: "text", text: content }]
      : [];

  for (const block of blocks) {
    if (!block || typeof block !== "object") continue;
    if (role === "assistant" && block.type === "text") {
      if (block.text && block.text.trim()) {
        closeProgress(null); // prose breaks the checklist without a synthetic "done"
        append(entry("agent-entry--agent", block.text));
      }
    } else if (role === "assistant" && block.type === "tool_use") {
      addStep(block);
    } else if (role === "user" && block.type === "tool_result") {
      // Tool results arrive as a "user" role message in stream-json — this
      // is the harness handing the tool's own output back, not a person
      // typing; it is never re-shown as a prompt (the composer already
      // echoed what the person actually sent).
      //
      // docs/plans/STUDIO.md Step 02 item 6: a successful cut_by_transcript/cut_by_time
      // call made with plan:true is a proposal, not a finished edit — it
      // gets drawn on the transcript rather than only logged. The payload is
      // handed to transcript.js VERBATIM (CLAUDE.md: the page renders ops'
      // own return value, never a re-derivation); this file only decides
      // WHETHER to emit, off the matching tool_use's own recorded name/input,
      // never what the payload means.
      const pending = pendingSteps.get(block.tool_use_id);
      if (pending && !block.is_error) {
        const bare = String(pending.name || "").replace(MCP_PREFIX, "");
        if (bare === "cut_by_transcript" || bare === "cut_by_time") {
          if (pending.input?.plan === true) {
            const payload = parseResultPayload(block);
            if (payload) ctx.emit("agent-plan", { tool: bare, input: pending.input, payload });
          } else {
            // The agent applied a cut itself, so whatever it last proposed
            // is no longer a proposal — with or without the banner's Apply
            // having been pressed. Left standing, the banner offered to cut
            // 13 words that were already struck through in the transcript
            // beside it, for the rest of the first recorded run.
            ctx.emit("agent-plan", null);
          }
        }
      }
      finishStep(block.tool_use_id, !block.is_error, resultPreview(block));
      // A sheet the agent asked for is a picture the agent read (CLAUDE.md:
      // an MCP tool result can carry an image, and `claude -p` puts it in
      // front of the model). Drawing it under the step is the one way a
      // person watching the pane sees what the agent saw — skipped as "not
      // part of the progress story" until the first recorded run, where the
      // shot sheet it reviewed its own picture on never appeared on screen.
      for (const image of resultImages(block)) {
        const wrap = el("div", "agent-entry agent-entry--image");
        const img = document.createElement("img");
        img.src = `data:${image.media_type};base64,${image.data}`;
        img.alt = `${humanizeTool(pending?.name)} — the image the tool returned`;
        img.loading = "lazy";
        wrap.append(img);
        append(wrap);
      }
    }
    // Any other block type (image, thinking, …) is silently skipped rather
    // than dumped raw — it is not part of the progress story this pane
    // draws, and skipping beats guessing at a shape this pane doesn't know.
  }
}

// -- per-turn thumbs (item 2) -----------------------------------------------
//
// docs/plans/DAYDREAM.md: "useful only if something reads it; build the log, defer any
// use." So this only appends a rating to Project.thumbs_path via
// /api/agent/thumbs — nothing here reads it back. One standalone feed row per
// successful turn rather than trying to locate "the" bubble for that turn: a
// prose-only turn never opens a .agent-progress list, so there is no single
// reliable anchor to attach a rating to.

let lastPrompt = "";

function buildThumbsRow(sessionId, turnId, prompt) {
  const wrap = el("div", "agent-entry agent-entry--tool agent-thumbs");
  const label = el("span", null, "Rate this turn:");
  const up = el("button", null, "Helpful");
  const down = el("button", null, "Not helpful");
  up.type = "button";
  down.type = "button";

  const rate = async (rating, btn) => {
    up.disabled = true;
    down.disabled = true;
    try {
      await ctx.api("/api/agent/thumbs", {
        rating,
        session_id: sessionId,
        turn_id: turnId,
        prompt,
      });
      wrap.textContent = "";
      wrap.append(el("span", null, rating === "up" ? "Marked helpful." : "Marked not helpful."));
    } catch (err) {
      ctx.emit("toast", err.message);
      up.disabled = false;
      down.disabled = false;
    }
  };
  up.addEventListener("click", () => rate("up", up));
  down.addEventListener("click", () => rate("down", down));

  wrap.append(label, up, down);
  return wrap;
}

function handleResult(data) {
  closeProgress("Done!");
  const ok = !data.subtype || data.subtype === "success";
  if (!ok) {
    const detail = typeof data.result === "string" ? data.result : JSON.stringify(data.result ?? {});
    append(entry("agent-entry--system bad", `Agent turn ended — ${data.subtype}${detail ? `: ${detail}` : ""}`));
  } else if (typeof data.session_id === "string" && typeof data.uuid === "string") {
    append(buildThumbsRow(data.session_id, data.uuid, lastPrompt));
  }
  setBusy(false);
}

// The pane's whole premise is that the agent reaches the project through
// proofcut's MCP server, so a server that failed to start is the one condition
// under which nothing it says can be trusted — and it is otherwise invisible:
// `claude` carries on with an empty tool set and answers the prompt in prose.
// The harness states the outcome in its own `init` event, so this draws that
// rather than deriving anything (this file's contract: the pane displays).
function reportMcpServers(servers) {
  if (!Array.isArray(servers)) return;
  const proofcut = servers.find((s) => s && s.name === "proofcut");
  if (proofcut && proofcut.status === "connected") return;
  const said = proofcut ? proofcut.status : "not started";
  append(
    entry(
      "agent-entry--system bad",
      `proofcut's MCP server is ${said} — this agent has no proofcut tools, so anything it ` +
        "says about the project is guesswork. Nothing it does can reach the timeline.",
    ),
  );
}

function handleAgentEvent(data) {
  if (!data || typeof data !== "object") return;
  switch (data.type) {
    case "assistant":
    case "user":
      handleAssistantOrUser(data);
      return;
    case "result":
      handleResult(data);
      return;
    case "system":
      // Session/init bookkeeping from the harness — not part of the
      // Daydream-style progress story, and noisy every turn if shown. The
      // one field worth keeping is `init`'s `model` (item 1) — every other
      // subtype (e.g. a future compaction notice) stays a no-op.
      if (data.subtype === "init") {
        setModel(data.model);
        reportMcpServers(data.mcp_servers);
      }
      return;
    case "rate_limit_event":
      // Quota bookkeeping the harness emits once per turn, `system`'s case
      // exactly: not part of the progress story, and noisy every turn if
      // shown. It is called out by name rather than left to the default
      // below because the default is for shapes nobody has SEEN — this one
      // arrives on every single prompt, and it landed a 300-character JSON
      // blob of reset timestamps and overage flags at the head of the feed,
      // above the agent's first sentence. Caught in the README retake, on
      // the first prompt sent through the pane.
      return;
    default: {
      // An event shape this pane does not recognise — degrade to a compact
      // raw entry rather than crash or stay silent (this file's contract).
      let raw;
      try {
        raw = JSON.stringify(data);
      } catch {
        raw = String(data);
      }
      append(entry("agent-entry--tool", raw.length > 300 ? raw.slice(0, 300) + "…" : raw));
    }
  }
}

// -- the render job ----------------------------------------------------------
//
// The top bar's Export button already POSTs /api/render itself and toasts
// the accepted job_id (app.js — the shell owns the top bar). This pane does
// not duplicate that POST; it owns everything the render SSE stream reports
// afterwards, keyed by job_id so a running/cancelled/error/done sequence for
// one job updates a single feed entry in place rather than spamming four.

const CHECK_ORDER = ["verify", "check_frames", "check_black", "spot_frames"];
const CHECK_LABELS = {
  verify: "Audio verify",
  check_frames: "Frame count",
  check_black: "Black frames",
  spot_frames: "Spot frames",
};

const renderSlots = new Map(); // job_id -> the .agent-entry wrapper for that job

function slotFor(jobId) {
  if (jobId && renderSlots.has(jobId)) return renderSlots.get(jobId);
  const wrap = el("div", "agent-entry agent-entry--tool");
  append(wrap);
  if (jobId) renderSlots.set(jobId, wrap);
  return wrap;
}

function stopRenderButton() {
  const btn = el("button", null, "Stop render");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      await ctx.api("/api/render/stop", {});
    } catch (err) {
      ctx.emit("toast", err.message);
      btn.disabled = false;
    }
  });
  return btn;
}

/** One check's badge class — "skip" when the server itself skipped it;
 * otherwise a small per-check heuristic over the fields that check's own ops
 * function actually returns. Never assumes a field is present. */
function badgeClass(name, result) {
  if (!result || result.skipped) return "skip";
  switch (name) {
    case "verify":
      return (result.repeated?.length || result.dropped?.length) > 0 ? "warn" : "ok";
    case "check_frames":
      return result.agrees === true ? "ok" : result.agrees === false ? "fail" : "skip";
    case "check_black":
      return result.clean === true ? "ok" : result.clean === false ? "warn" : "skip";
    case "spot_frames": {
      const errors = (result.frames || []).filter((f) => f && f.error).length;
      return errors > 0 ? "warn" : "ok";
    }
    default:
      return "skip";
  }
}

/** A one-line summary under each check's badge — the specific fields when
 * this pane knows the check, a generic dump when it does not. */
function checkSummary(name, result) {
  if (!result) return "no result";
  if (result.skipped) return `skipped — ${result.reason || "no reason given"}`;
  switch (name) {
    case "verify": {
      const repeated = result.repeated?.length ?? 0;
      const dropped = result.dropped?.length ?? 0;
      return `similarity ${result.similarity ?? "–"} · ${repeated} repeated run${repeated === 1 ? "" : "s"} · ${dropped} dropped run${dropped === 1 ? "" : "s"}`;
    }
    case "check_frames":
      return `expected ${result.expected_frames ?? "–"} frames · target ${result.target_frames ?? "–"} · delta ${result.delta ?? "–"}`;
    case "check_black": {
      const runs = result.runs?.length ?? 0;
      return `${runs} black run${runs === 1 ? "" : "s"}${result.clean === false ? " — unexplained" : ""}`;
    }
    case "spot_frames": {
      const frames = result.frames?.length ?? 0;
      return `${frames} frame${frames === 1 ? "" : "s"} sampled${result.mapping_trusted === false ? " · mapping not trusted" : ""}`;
    }
    default: {
      const dump = JSON.stringify(result);
      return dump.length > 200 ? dump.slice(0, 200) + "…" : dump;
    }
  }
}

function buildCompletionCard(data) {
  const card = el("div", "completion-card");
  card.append(el("div", null, "Export complete"));

  const rows = el("table", "rows");
  const row = (label, value) => {
    const tr = document.createElement("tr");
    tr.append(el("td", null, label), el("td", null, value));
    rows.append(tr);
  };
  row("output", (data.output || "").split("/").pop() || "–");
  row("dimensions", data.width && data.height ? `${data.width}×${data.height}` : "audio only");
  row("duration", fmt(data.duration));
  row("video / audio", `${data.has_video ? "yes" : "no"} / ${data.has_audio ? "yes" : "no"}`);
  card.append(rows);

  const checks = data.checks || {};
  for (const name of CHECK_ORDER) {
    const result = checks[name];
    const checkRow = el("div", "check-row");
    checkRow.append(el("span", null, CHECK_LABELS[name]));
    checkRow.append(el("span", `check-badge ${badgeClass(name, result)}`, badgeClass(name, result)));
    card.append(checkRow);
    const summary = el("div", null, checkSummary(name, result));
    summary.style.fontSize = "11px";
    summary.style.opacity = "0.75";
    summary.style.margin = "-4px 0 4px";
    card.append(summary);
  }
  return card;
}

function handleRenderEvent(data) {
  if (!data || typeof data !== "object") return;
  const wrap = slotFor(data.job_id);
  // A progress event rewrites only the label: rebuilding the card would
  // remove the Stop button from under a click in progress, and Chrome then
  // drops that click silently (CLAUDE.md § redraw only the node a gesture owns).
  if (data.status === "progress") {
    const label = wrap.querySelector(".render-progress");
    if (label) {
      const done = progressText(data);
      label.textContent = `${label.dataset.base}${done ? ` ${done}` : ""}`;
    }
    return;
  }
  wrap.textContent = "";
  switch (data.status) {
    case "running": {
      const base = `Rendering${data.preset ? ` · preset ${data.preset}` : ""}…`;
      const label = el("span", "render-progress", base);
      label.dataset.base = base;
      wrap.append(label, stopRenderButton());
      break;
    }
    case "cancelled":
      wrap.append(el("div", null, "Render cancelled — the partial output was deleted."));
      break;
    case "error":
      wrap.append(el("div", "warn bad", `Render failed — ${data.error || "unknown error"}`));
      break;
    case "done":
      wrap.append(buildCompletionCard(data));
      break;
    default: {
      let raw;
      try {
        raw = JSON.stringify(data);
      } catch {
        raw = String(data);
      }
      wrap.append(el("div", null, raw));
    }
  }
  scrollToBottom();
}

// -- op-result: one feed for everything that happened to the edit -----------

/** A short human line for a mutating op's own JSON reply. `ops` functions
 * return different shapes per op (undo has no `removed`; cut/keep do) — this
 * recognises the shapes this pane knows and still renders anything else
 * rather than staying silent, per PLAN.md's "one feed for everything that
 * happened to the edit". */
function describeOp(payload) {
  if (payload && typeof payload.restored_from === "string") {
    // A snapshot is a pair now, and the three answers look identical from
    // here unless the op's own flags are read: a cut coming back, a cue
    // table coming back, and a seed being taken away. Read `manifest_restored`
    // and `timeline_removed` off the reply rather than inferring either from
    // the segment count — the pane draws what the op returned.
    const parts = [];
    if (payload.timeline_removed) {
      parts.push("removed the timeline (undoing the seed)");
    } else if (payload.timeline_restored) {
      parts.push(`${payload.segments ?? "?"} segment(s), ${fmt(payload.timeline_duration)}`);
    }
    if (payload.manifest_restored) parts.push("project settings");
    else if (payload.manifest_restored === false) parts.push("timeline only (older snapshot)");
    const what = parts.length ? parts.join(" · ") : "the previous state";
    return `Undo — restored ${what}. Undo depth ${payload.undo_depth ?? "?"}.`;
  }
  if (payload && typeof payload.removed === "number") {
    const removed = payload.removed.toFixed(2);
    let line = payload.plan
      ? `Preview — nothing was written. Would remove ${removed}s.`
      : `Applied — removed ${removed}s. Undo rolls it back.`;
    const suspects = payload.suspect_boundaries?.length;
    if (suspects) line += ` ${suspects} suspect boundary${suspects === 1 ? "" : "ies"} flagged.`;
    return line;
  }
  if (!payload) return "Applied.";
  const bits = Object.entries(payload)
    .slice(0, 4)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`);
  return `Applied — ${bits.join(" · ")}`;
}

// -- @-mentions of assets (item 3) -------------------------------------------
//
// Pure composer sugar: the agent already reaches media through its own MCP
// tools, so this adds no capability and no endpoint — it only inserts text.
// Completion is over `ctx.getView().clips` (already shipped by
// `ops.timeline_view`, read fresh on every keystroke rather than cached,
// matching player.js/transcript.js's own "read the view through
// `ctx.getView()`" convention), scoped to "the project's registered
// clips/media" per the task's own wording — not the separate, unlisted
// card-asset registry (`cue_ls` only enumerates *placed* cues, never
// unplaced cards; completing over them would need a new endpoint).
//
// Not caret-precise: the popover anchors to the composer box
// (`.agent-composer { position: relative }`), not the exact caret pixel — a
// mirror-div measurement this "pure sugar" scope deliberately skips.

let mentionState = null; // { start, query, matches, activeIndex } | null

/** The active `@token` ending at `caret`, or null if the caret is not inside
 * one. A `@` must start a token (preceded by whitespace or the start of the
 * text) and the token may not itself contain whitespace. */
function activeMentionAt(text, caret) {
  const upto = text.slice(0, caret);
  const at = upto.lastIndexOf("@");
  if (at === -1) return null;
  if (at > 0 && !/\s/.test(upto[at - 1])) return null;
  const query = upto.slice(at + 1);
  if (/\s/.test(query)) return null;
  return { start: at, query };
}

function closeMention() {
  mentionState = null;
  $("agent-mentions")?.remove();
}

function updateMentionState() {
  const promptBox = $("agent-prompt");
  const active = activeMentionAt(promptBox.value, promptBox.selectionStart);
  if (!active) {
    closeMention();
    return;
  }
  const clips = ctx.getView()?.clips || [];
  const q = active.query.toLowerCase();
  const matches = clips.filter((c) => c.clip_id.toLowerCase().startsWith(q)).slice(0, 8);
  if (matches.length === 0) {
    // Nothing to complete — degrade to plain text, per this feature's own
    // contract: no popover, Enter still sends, "@text" goes to the agent
    // as-is.
    closeMention();
    return;
  }
  mentionState = { ...active, matches, activeIndex: 0 };
  renderMentionPopover();
}

function insertMention(clipId) {
  const promptBox = $("agent-prompt");
  const { start, query } = mentionState;
  const end = start + 1 + query.length;
  const before = promptBox.value.slice(0, start);
  const after = promptBox.value.slice(end);
  promptBox.value = `${before}@${clipId} ${after}`;
  const caret = before.length + clipId.length + 2;
  promptBox.focus();
  promptBox.setSelectionRange(caret, caret);
  closeMention();
}

function renderMentionPopover() {
  let box = $("agent-mentions");
  if (!box) {
    box = el("div", "mention-popover");
    box.id = "agent-mentions";
    $("agent-composer").append(box);
  }
  box.textContent = "";
  mentionState.matches.forEach((clip, i) => {
    const item = el(
      "div",
      `mention-item${i === mentionState.activeIndex ? " active" : ""}`,
      clip.clip_id + (clip.has_transcript ? "" : " (no transcript)"),
    );
    // mousedown + preventDefault, not click: stops the textarea blurring
    // (and closeMention() firing on that blur) before the insert runs.
    item.addEventListener("mousedown", (event) => {
      event.preventDefault();
      insertMention(clip.clip_id);
    });
    box.append(item);
  });
}

// -- wiring -------------------------------------------------------------

export function init(passedCtx) {
  ctx = passedCtx;
  // The guarantee is the user-facing half and stays on the page; the
  // citation behind it is for whoever maintains this and rides the tooltip.
  // Someone reading a screenshot of this pane wants to know what the agent
  // can reach, not which section of PLAN.md says so.
  const placeholder = el(
    "p",
    "pane-placeholder",
    "Ask the agent to edit this project. It reaches the timeline only " +
      "through proofcut's own MCP tools, and nothing else.",
  );
  placeholder.title = "PLAN.md § The agent panel, and why it does not become a fourth implementation";
  append(placeholder);

  const composer = $("agent-composer");
  const promptBox = $("agent-prompt");
  const modelSelect = $("agent-model-select");

  modelSelect.addEventListener("change", () => {
    selectedModel = modelSelect.value;
  });

  composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const prompt = promptBox.value.trim();
    if (!prompt || busy) return;
    // The server bakes `--model` in at spawn and cannot hot-swap it — a
    // change here is about to silently restart the live conversation
    // (AgentSession.send's own stale-process check) unless this is the
    // first prompt this tab has sent, so say so before it happens rather
    // than leaving a stale feed under the new reply.
    if (spawnedModel !== null && selectedModel !== spawnedModel) {
      resetForNewSession(
        `Model changed to ${modelSelect.selectedOptions[0]?.textContent || "the default"} — ` +
          "the previous conversation was cleared.",
      );
    }
    spawnedModel = selectedModel;
    append(entry("agent-entry--user", prompt));
    promptBox.value = "";
    closeProgress(null); // a fresh prompt starts a fresh checklist, not a continuation
    lastPrompt = prompt;
    closeMention();
    setBusy(true);
    try {
      await ctx.api("/api/agent", { prompt, model: selectedModel || null });
    } catch (err) {
      append(entry("agent-entry--system bad", err.message));
      setBusy(false);
    }
  });

  // Enter sends (matches every chat composer this panel is imitating);
  // shift-Enter still inserts a newline for a multi-line prompt. When a
  // mention popover is open, arrow/Enter/Tab/Escape drive it instead —
  // folded into this one listener rather than a second one on the same key.
  promptBox.addEventListener("keydown", (event) => {
    if (mentionState) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        mentionState.activeIndex = (mentionState.activeIndex + 1) % mentionState.matches.length;
        renderMentionPopover();
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        mentionState.activeIndex =
          (mentionState.activeIndex - 1 + mentionState.matches.length) % mentionState.matches.length;
        renderMentionPopover();
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        insertMention(mentionState.matches[mentionState.activeIndex].clip_id);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeMention();
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  promptBox.addEventListener("input", updateMentionState);
  promptBox.addEventListener("keyup", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) updateMentionState();
  });
  promptBox.addEventListener("click", updateMentionState);
  promptBox.addEventListener("blur", closeMention);

  $("agent-stop").addEventListener("click", async () => {
    try {
      await ctx.api("/api/agent/stop", {});
    } catch (err) {
      ctx.emit("toast", err.message);
    }
    setBusy(false);
  });

  // Item 4: "Start New Task" — kill the live subprocess so the next prompt
  // spawns fresh with no conversation history. The server suppresses the
  // synthetic error_no_output result this kill would otherwise trigger (see
  // webui.py's `_suppress_next_exit_report`), so nothing arrives on
  // /api/events to clear `busy` or in-flight progress state — this handler
  // does that directly rather than waiting on a stream event that will not
  // come.
  $("agent-new-task").addEventListener("click", async () => {
    try {
      await ctx.api("/api/agent/new-task", {});
    } catch (err) {
      ctx.emit("toast", err.message);
      return;
    }
    spawnedModel = null;
    resetForNewSession(
      "New task — the previous conversation was cleared. The next prompt starts a fresh agent process.",
    );
  });

  ctx.on("agent", (data) => {
    try {
      handleAgentEvent(data);
    } catch (err) {
      // This pane's own contract: an event shape it fails to parse still
      // shows up rather than crashing the panel or vanishing silently.
      append(entry("agent-entry--tool", `agent event (unparsed) — ${err.message}`));
    }
  });

  ctx.on("render", (data) => {
    try {
      handleRenderEvent(data);
    } catch (err) {
      append(entry("agent-entry--tool", `render event (unparsed) — ${err.message}`));
    }
  });

  ctx.on("op-result", ({ payload, error }) => {
    if (error) {
      append(entry("agent-entry--system bad", error));
      return;
    }
    try {
      append(entry("agent-entry--system", describeOp(payload)));
    } catch (err) {
      append(entry("agent-entry--system", `Applied — result not shown (${err.message}).`));
    }
  });
}

export function update(_state) {
  // The feed is a running log, not a projection of the view — there is
  // nothing to redraw when the view changes. Exported anyway: every pane
  // implements the same two-function interface (transcript.js's header
  // comment), so a later stage can rely on `update` existing here too.
}
