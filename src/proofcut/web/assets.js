/**
 * assets.js — the assets pane: every asset a cue can point at, straight off
 * `GET /api/assets` (`ops.assets`), which is already the whole inspector's
 * worth of data (media probe fields, transcript/described flags, cue-usage
 * counts, card records) — this file draws it and does nothing else.
 *
 * Follows the pane-module interface documented in transcript.js's header
 * comment: `init(ctx)` once, `update(state)` on every view change. `state`
 * (the `/api/view` payload) is used only to know which clip is the one
 * currently loaded in the transcript/timeline/preview — everything this
 * pane actually draws comes from its own `/api/assets` fetch, re-issued
 * every time `update` fires (the initial load, a reload after
 * 'project-changed', and after this pane's own role-toggle mutation), the
 * same "state changed, redraw" contract every other pane follows even
 * though the data itself lives outside `state`.
 *
 * CLAUDE.md's rule applies here like everywhere else in this file set: this
 * pane draws, it never decides. The one mutation it makes — `POST
 * /api/clip-role` — is `ops.clip_role`, the fourth caller alongside the CLI
 * and MCP tool (webui.py's own doc comment on the route says as much), and
 * its result is rendered by re-fetching `/api/assets`, never computed here.
 *
 * Clicking a row does not select a clip for the transcript/timeline/preview
 * — that is the top bar's `#clip` picker, a different piece of state
 * (`view.clip_id`) this pane does not own or touch. A click here only asks
 * properties.js to inspect that asset, over the `inspect-asset` bus event —
 * the two panes are wired through `ctx`, never importing each other
 * (PLAN.md § Files, and why they split).
 *
 * This file also owns "footage in" as a window operation (docs/plans/STUDIO.md's
 * unmet definition-of-done): the "add footage" form (`POST /api/import`),
 * and per-clip Transcribe (`POST /api/transcribe`) / Attach… (`POST
 * /api/transcript/attach`). The first two are ProxyJob-shaped one-slot
 * jobs — the reply only acknowledges (`job_id`), `running`/`done`/`error`
 * arrive as bus events (`ctx.on("import", …)` / `ctx.on("transcribe", …)`,
 * wired the way frame.js wires its own sheet/detect jobs) — attach is a
 * plain, instant mutation with no job of its own.
 *
 * THE TRAP (webui.py's own docstrings on `ImportJob`/`TranscribeJob`, and
 * confirmed by reading `ops.transcribe`/`ops.attach_transcript`): a
 * finished import writes the manifest (`ops.import_media`), so
 * `project-changed` follows it like any other mutation and this pane's own
 * `refresh()` — plus every other pane's `update()` — picks it up through
 * app.js's normal reload path. A finished transcription or attach writes
 * ONLY a transcript file. `webui._revision()` stats `project.otio`, the
 * manifest and undo depth — none of which moved — so NO `project-changed`
 * event ever follows either one. So both the "transcribe" job's `done`
 * event and a successful attach ask for the reload themselves, over
 * `ctx.emit("reload")` — app.js subscribes and runs the same `load()` its
 * `project-changed` path runs, so transcript.js, timeline.js and
 * properties.js all redraw rather than sitting on stale data for a clip
 * that just gained a transcript.
 *
 * It is `load()` and NOT `window.location.reload()`, and the difference is
 * not tidiness. The op's reply is the finding — words attached, whisper's
 * hallucinated words dropped, the retakes and seams the four attach-time
 * checks found — and a page reload throws it away before anyone reads it,
 * which is the one thing a panel here must not do (CLAUDE.md: the panel
 * renders that function's own return value). Measured in the browser: the
 * summary line survives a `load()` and did not survive the reload.
 */

import { $, el, progressText, secs } from "./dom.js";

let ctx = null;
let lastView = null;
let data = null; // the last successful /api/assets payload, or null
let inspected = null; // {kind: "clip", clipId} | {kind: "card", name} | null
// — mirrors properties.js's own `inspected`, kept in sync one-way (a click
// here sets both; a click over there — none exists yet — would not update
// this). Used only to draw the `.inspected` highlight.
let refreshSeq = 0; // guards against an in-flight /api/assets fetch from an
// earlier `refresh()` landing after a later one and overwriting fresh data
// with stale — properties.js's `requestSeq` is the same guard for the same
// reason (its own header comment). Two triggers race here without it: a
// role-toggle click's own post-mutation refresh, and the SSE `project-changed`
// poll's reload (app.js's `load()` -> `assets.update()`) firing around the
// same manifest write. Demonstrated live: a role toggle wrote the manifest
// correctly but the DOM reverted to "off" because an older, slower-resolving
// fetch (issued before the click) rendered after the click's own fresher one.

//: ops.CLIP_ROLES, echoed rather than imported — there is no shared module
//: between the Python ops layer and this file, and the set is small and
//: stable (docs/plans/DAYDREAM.md § Import roles + assets pane: "voiceover" vs
//: "footage"). A role this pane does not recognise cannot reach here in the
//: first place — `clip_role` refuses anything outside the tuple before it
//: ever writes.
const CLIP_ROLES = ["voiceover", "footage"];

// -- "add footage" job state (ImportJob, one slot server-side) -------------
let importBusy = false;
// Latched once, so the auto-open for an empty project happens on the first
// render that sees no clips and never again — a re-render mid-typing that
// re-opened the form would also steal focus back into it.
let importOpenedForEmpty = false;

// -- per-clip transcribe job state (TranscribeJob, one slot server-side —
// only one clip can be transcribing at a time, so this is a single flag
// plus which clip it belongs to, not a per-clip map) ------------------------
let transcribeBusy = false;
let transcribingClipId = null;
// Which clip's inline "attach an existing transcript" mini-form is open, if
// any — local UI state only, never sent anywhere, reset whenever a reload
// or a fresh render would leave it pointing at a row that may not exist
// (the reload after a successful attach already clears it moot).
let attachOpenFor = null;

function fmtHz(n) {
  return n ? `${n.toLocaleString()} Hz` : "–";
}

/** One fact about a clip: a marker, then the thing it is a fact ABOUT.
 *
 * This replaced six coloured pills per clip (2026-08-24). The pills were
 * legible one at a time and a wall six across — three of them permanently
 * red on any ordinary voiceover, which is a lot of alarm for "this audio
 * file has no video in it". Worse, the redness is why the labels carried
 * the negative in the WORD (`no video`, `not described`): a red pill reading
 * `described` says "described, and that is bad", so the colour and the word
 * disagreed and the word is what a screenshot carries.
 *
 * A marker column fixes both at once. `— video` cannot be misread, so the
 * labels go back to being plain nouns, and absence stops being an alarm:
 *
 *   `ok`      ✓ in the kept green, label in ink — the thing is there
 *   `off`     – dim, label dim — the thing is absent, which is ordinary
 *   `fail`    ✕ in the cut red, label red — the thing is WRONG
 *   `count`   · dim marker, label in ink — a NUMBER, neither good nor bad
 *
 * Only `fail` is loud, and only two facts can reach it: a media file that is
 * not on disk, and one the decoder refuses. Those are real defects in the
 * project; nothing else here is. `count` exists so a cue tally stops
 * borrowing one of the other three — the old pill went green at one cue,
 * which said a clip with none had something wrong with it.
 */
function fact(label, state, title) {
  const node = el("span", `asset-fact${state === "ok" ? "" : ` ${state}`}`);
  const mark =
    state === "ok" ? "\u2713" : state === "fail" ? "\u2715" : state === "count" ? "\u00b7" : "\u2013";
  node.append(el("span", "asset-fact-mark", mark));
  node.append(el("span", null, label));
  if (title) node.title = title;
  return node;
}

/** `media.playability`'s verdict, or `null` when the file was not even
 * reachable to probe (a different claim than "unplayable" — ops.assets'
 * own docstring). Both are worth telling apart at a glance rather than
 * collapsing into one grey fact. */
function playableFact(clip) {
  if (!clip.media_exists) return fact("missing", "fail", `not on disk: ${clip.media_path}`);
  if (clip.playable === null || clip.playable === undefined) {
    return fact("unchecked", "off", "media.playability was not run for this clip");
  }
  const p = clip.playable;
  if (p.playable) return fact("playable", "ok", p.reason || "");
  // The one other loud state, and it earns it: a clip the decoder refuses is
  // a clip the render will not have. Three of playability's four refusal
  // classes pass a naive codec-name check (CLAUDE.md), so the reason is the
  // content and it rides the tooltip.
  return fact("unplayable", "fail", p.reason || "");
}

/** How many cues point at this asset. Shared by the clip and card rows, so
 * the two cannot drift — and `count` rather than `ok`/`off`, because a clip
 * with no cues is not a clip with a problem. */
function cueFact(n) {
  // Always the `count` marker, never the absence dash: "– 0 cues" says the
  // same thing twice and reads as a missing capability rather than a tally
  // that happens to be zero. `off` is added on top for the dim colour, which
  // is the one thing zero should change.
  const node = fact(`${n} cue${n === 1 ? "" : "s"}`, "count");
  if (!n) node.classList.add("off");
  return node;
}

function roleChips(clip) {
  const row = el("div", "asset-roles");
  for (const role of CLIP_ROLES) {
    const btn = el("button", `toggle${clip.role === role ? " on" : ""}`, role);
    btn.type = "button";
    btn.title =
      clip.role === role
        ? `clear ${clip.clip_id}'s role (back to undeclared)`
        : `set ${clip.clip_id}'s import role to ${role}`;
    btn.addEventListener("click", (event) => {
      event.stopPropagation(); // do not also trigger the row's own inspect click
      setRole(clip.clip_id, clip.role === role ? null : role);
    });
    row.append(btn);
  }
  return row;
}

async function setRole(clipId, role) {
  if (!ctx) return;
  let payload = null;
  let error = null;
  try {
    payload = await ctx.api(
      "/api/clip-role",
      role === null ? { clip_id: clipId, reset: true } : { clip_id: clipId, role },
    );
  } catch (err) {
    error = err.message;
    ctx.emit("toast", error);
  }
  ctx.emit("op-result", { payload, error });
  if (!error) await refresh();
}

function inspect(next) {
  inspected = next;
  ctx.emit("inspect-asset", next);
  render();
}

function buildClipRow(clip) {
  const row = el("div", "asset-row");
  row.dataset.clipId = clip.clip_id; // a stable hook — .asset-id's own text
  // grows a " · loaded" suffix on the active clip, below, so it is not one
  if (inspected && inspected.kind === "clip" && inspected.clipId === clip.clip_id) {
    row.classList.add("inspected");
  }
  row.addEventListener("click", () => inspect({ kind: "clip", clipId: clip.clip_id }));

  // Drag source for step 02 item 5 (drag-from-assets to V2). The dragged
  // thing is the ASSET — clip.clip_id, the footage — never the addressing
  // clip a shot/cue happens to be read through (CLAUDE.md: "a shot's
  // addressing clip is not its footage"). timeline.js's V2 drop target reads
  // this back off `application/x-proofcut-asset` and resolves the drop point to
  // a word on the loaded transcript clip separately — this file only names
  // what was dragged, never where it lands.
  row.draggable = true;
  row.addEventListener("dragstart", (event) => {
    const payload = { kind: "clip", id: clip.clip_id };
    event.dataTransfer.setData("application/x-proofcut-asset", JSON.stringify(payload));
    event.dataTransfer.setData("text/plain", payload.id);
    event.dataTransfer.effectAllowed = "copy";
  });

  // A poster frame, on the same route the filmstrip already uses — one more
  // caller of `/api/thumb`, never of `preview_path()` (CLAUDE.md: adding a
  // third caller to that is the whole hole the split exists to prevent).
  //
  // This pane listed ten clips as `lsblk` output — a mono id, a mono metadata
  // line and a six-cell ✓/– grid — with no picture of the footage anywhere in
  // it, while the timeline two panes down was already drawing thumbnails of
  // the same files. It costs no height, and that is measured rather than
  // hoped for (CLAUDE.md § a new control spends the pane's height): the body
  // beside it is 153px of id/meta/facts/roles/controls, so a 54px frame fits
  // inside the row's existing height and every clip row stays at 189px —
  // checked in a browser against the pre-change geometry, both 189.
  //
  // Two seconds in, not zero: a head frame is a fade-in on about half of real
  // footage, and `/api/thumb` snaps `at=` to its own cache interval anyway, so
  // a nudge off the head costs nothing and is the difference between a
  // catalogue of black rectangles and one you can read.
  const body = el("div", "asset-body");
  if (clip.has_video) {
    const thumb = document.createElement("img");
    thumb.className = "asset-thumb";
    thumb.alt = "";
    thumb.loading = "lazy";
    thumb.draggable = false;
    thumb.src = `/api/thumb/${encodeURIComponent(clip.clip_id)}?at=${Math.min(2, (clip.duration || 4) / 2).toFixed(3)}`;
    // Media missing from disk 400s the same way a filmstrip frame does; the
    // row still has every fact on it, so this leaves no gap and says nothing.
    thumb.addEventListener("error", () => thumb.remove());
    row.append(thumb);
  }
  row.append(body);

  const id = el("div", "asset-id", clip.clip_id);
  if (lastView && lastView.clip_id === clip.clip_id) {
    id.append(el("span", "hint", "  · loaded"));
  }
  body.append(id);

  const dims = clip.width && clip.height ? `${clip.width}×${clip.height}` : null;
  const meta = [
    secs(clip.duration),
    dims,
    clip.fps ? `${Number(clip.fps).toFixed(2)}fps` : null,
    clip.video_codec,
    clip.has_audio ? `${clip.channels || "?"}ch @ ${fmtHz(clip.sample_rate)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  body.append(el("div", "asset-meta", meta || "no probe metadata"));

  // Two columns, read across then down, and the pairing is deliberate: the
  // two stream facts, then the two index facts, then what the file can do.
  // The API fields are untouched — every label here is display text.
  const facts = el("div", "asset-facts");
  facts.append(fact("video", clip.has_video ? "ok" : "off"));
  facts.append(fact("audio", clip.has_audio ? "ok" : "off"));
  facts.append(
    fact(
      "transcript",
      clip.transcript ? "ok" : "off",
      clip.transcript ? "transcribed" : "not transcribed",
    ),
  );
  facts.append(
    fact(
      "described",
      clip.described ? "ok" : "off",
      clip.described ? "indexed by describe" : "not indexed",
    ),
  );
  facts.append(playableFact(clip));
  facts.append(cueFact(clip.cues));
  body.append(facts);

  body.append(roleChips(clip));

  // Transcribe / Attach… only make sense before a transcript exists —
  // `clip.transcript` is `ops.assets`'s own live check
  // (`project.transcript_path(clip_id).is_file()`), not something this
  // file derives.
  if (!clip.transcript) body.append(transcribeControls(clip));

  return row;
}

function buildCardRow(card) {
  const row = el("div", "asset-row");
  row.dataset.cardName = card.name;
  if (inspected && inspected.kind === "card" && inspected.name === card.name) {
    row.classList.add("inspected");
  }
  row.addEventListener("click", () => inspect({ kind: "card", name: card.name }));

  // Same drag source as a clip row, above — but a card's addressable asset
  // key is `card:<name>`, not the bare name (cue_add's own asset syntax,
  // ops.py: `asset.startswith("card:")`). cue_add does not validate `asset`
  // at all (that is the shot projection's job), so a bare name here would
  // write a cue nothing refuses at cue-add time and nothing resolves later —
  // silently wrong rather than an error. `card.asset` is only the display
  // id, never what gets dragged.
  row.draggable = true;
  row.addEventListener("dragstart", (event) => {
    const payload = { kind: "card", id: `card:${card.name}` };
    event.dataTransfer.setData("application/x-proofcut-asset", JSON.stringify(payload));
    event.dataTransfer.setData("text/plain", payload.id);
    event.dataTransfer.effectAllowed = "copy";
  });

  row.append(el("div", "asset-id", card.asset));
  const meta = [
    card.template ? `template ${card.template}` : "no template record",
    card.canvas,
    card.variant ? `variant ${card.variant}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  row.append(el("div", "asset-meta", meta));

  // The clip row's own fact grid, with a card's two facts. Both take the
  // loud state when false, and both earn it: a card with no `(template,
  // slots, canvas, variant)` record cannot be re-authored by ANYTHING — it
  // is reported, never guessed at (CLAUDE.md § So a card is re-authored,
  // never resized) — and a card whose files are gone is a shot the render
  // will not have.
  const facts = el("div", "asset-facts");
  facts.append(
    fact(
      "recorded",
      card.recorded ? "ok" : "fail",
      card.recorded
        ? "card_new/card_reauthor wrote this"
        : "no record — nothing can re-author this card at a new canvas",
    ),
  );
  facts.append(
    fact(
      card.files_exist ? "files on disk" : "missing files",
      card.files_exist ? "ok" : "fail",
    ),
  );
  facts.append(cueFact(card.cues));
  row.append(facts);

  return row;
}

/* -- transcribe / attach (per clip) ---------------------------------------- */

function transcribeControls(clip) {
  const wrap = el("div", "asset-transcribe");
  wrap.addEventListener("click", (event) => event.stopPropagation()); // do not also inspect the row

  const busyHere = transcribeBusy && transcribingClipId === clip.clip_id;
  const tBtn = el("button", null, busyHere ? "transcribing…" : "Transcribe");
  tBtn.type = "button";
  tBtn.disabled = transcribeBusy;
  tBtn.title = "run whisper (asr.transcribe) on this clip's own media";
  tBtn.addEventListener("click", () => startTranscribe(clip.clip_id));
  wrap.append(tBtn);

  const attachBtn = el("button", null, attachOpenFor === clip.clip_id ? "cancel" : "Attach…");
  attachBtn.type = "button";
  attachBtn.disabled = transcribeBusy;
  attachBtn.title = "point at an existing word-timed transcript file instead of running ASR";
  attachBtn.addEventListener("click", () => {
    attachOpenFor = attachOpenFor === clip.clip_id ? null : clip.clip_id;
    render();
  });
  wrap.append(attachBtn);

  if (attachOpenFor === clip.clip_id) wrap.append(buildAttachForm(clip));

  return wrap;
}

function buildAttachForm(clip) {
  const form = el("form", "asset-attach-form");
  form.addEventListener("click", (event) => event.stopPropagation());
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "/path/on/the/proofcut/host/transcript.json";
  input.setAttribute("aria-label", `transcript path for ${clip.clip_id}`);
  input.autocomplete = "off";
  form.append(input);
  const submit = el("button", null, "Attach");
  submit.type = "submit";
  form.append(submit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (submit.disabled) return; // in-flight guard against a double click
    input.disabled = true;
    submit.disabled = true;
    attachTranscript(clip.clip_id, input.value.trim()).finally(() => {
      // Only reachable on failure — success reloads the page before this
      // would ever run, so there is no row left to re-enable.
      input.disabled = false;
      submit.disabled = false;
    });
  });
  return form;
}

async function startTranscribe(clipId) {
  if (!ctx || transcribeBusy) return;
  try {
    await ctx.api("/api/transcribe", { clip_id: clipId });
  } catch (err) {
    // A 400 (unknown clip_id, unopenable project) or a 409
    // (TranscribeBusyError) both land here before any "running" event
    // would — frame.js's onBuildSheetClick's own precedent.
    ctx.emit("toast", err.message);
  }
}

/** What an attach or a transcription actually found, off the op's own reply.
 *
 * The counts are the point rather than decoration: `hallucinated_words` is
 * whisper stumbling and the guard containing it, and the four findings are
 * the retakes, seams and suspect durations `ops.attach_transcript` computes
 * once, at attach, and never again unless someone runs `transcript-checks`
 * (CLAUDE.md). A reply that reported them to nobody is the same as not
 * having asked — so this renders the op's return value rather than
 * summarising it into "done".
 */
function transcriptSummary(clipId, payload) {
  const bits = [`${payload.words} words`];
  if (payload.language) bits.push(payload.language);
  if (payload.hallucinated_words) bits.push(`${payload.hallucinated_words} hallucinated dropped`);
  const findings = [
    ["repeat", payload.repeats],
    ["seam", payload.overlaps],
    ["near-duplicate", payload.near_duplicates],
    ["suspect duration", payload.suspect_durations],
  ];
  for (const [name, list] of findings) {
    if (list && list.length) bits.push(`${list.length} ${name}${list.length === 1 ? "" : "s"}`);
  }
  return `${clipId}: ${bits.join(" · ")}`;
}

async function attachTranscript(clipId, path) {
  if (!ctx || !path) return;
  let payload = null;
  try {
    payload = await ctx.api("/api/transcript/attach", { clip_id: clipId, path });
  } catch (err) {
    ctx.emit("toast", err.message);
    return;
  }
  attachOpenFor = null;
  // Same trap as the transcribe job's own "done" handler below —
  // ops.attach_transcript also writes only a transcript file, so no
  // `project-changed` follows this either, and the reload has to be asked
  // for. `ctx.emit("reload")` is app.js's `load()`, NOT a page reload: the
  // summary line below has to survive the redraw to be read at all.
  setTranscribeStatusLine("done", transcriptSummary(clipId, payload));
  ctx.emit("reload");
}

function setTranscribeStatusLine(kind, text) {
  const node = $("transcribe-status");
  if (!node) return;
  if (!text) {
    node.hidden = true;
    node.textContent = "";
    node.className = "asset-status";
    return;
  }
  node.hidden = false;
  node.textContent = text;
  node.className = `asset-status ${kind}`;
}

function onTranscribeEvent(data) {
  if (!data || typeof data !== "object") return;
  if (data.status === "running") {
    transcribeBusy = true;
    transcribingClipId = data.clip_id;
    setTranscribeStatusLine("running", `transcribing ${data.clip_id}… (minutes, not seconds)`);
    render();
  } else if (data.status === "progress") {
    const done = progressText(data);
    if (done) setTranscribeStatusLine("running", `transcribing ${data.clip_id}… ${done}`);
  } else if (data.status === "done") {
    transcribeBusy = false;
    transcribingClipId = null;
    // The event carries ops.transcribe's whole return value, so this draws
    // what the transcription actually found rather than that it finished.
    setTranscribeStatusLine("done", transcriptSummary(data.clip_id, data));
    render();
    // THE TRAP, restated at the one place it actually bites: ops.transcribe
    // writes only a transcript file, so this "done" event is the client's
    // *only* reload signal (webui.py's own docstring on TranscribeJob).
    // `reload` is app.js's `load()` — every pane redraws and the line above
    // stays on screen; `location.reload()` would throw the report away.
    ctx.emit("reload");
  } else if (data.status === "error") {
    transcribeBusy = false;
    transcribingClipId = null;
    // The server's own message, verbatim — never invent a reason.
    setTranscribeStatusLine("error", data.error || "transcription failed");
    render();
  }
}

/* -- add footage (import) -------------------------------------------------- */

function setImportStatus(kind, text) {
  const node = $("import-status");
  if (!node) return;
  if (!text) {
    node.hidden = true;
    node.textContent = "";
    node.className = "asset-status";
    return;
  }
  node.hidden = false;
  node.textContent = text;
  node.className = `asset-status ${kind}`;
}

function setImportFormBusy(busy) {
  const form = $("import-form");
  if (!form) return;
  for (const field of form.elements) field.disabled = busy;
}

/* The payload of the last import attempted, so the mix offer below can
   re-post exactly it with `mix` set — rather than re-reading the form, which
   the user may have edited since the refusal came back. */
let lastImportPayload = null;

function setMixOffer(streams) {
  const offer = $("import-offer");
  const sum = $("import-mix");
  const pick = $("import-pick");
  if (!offer || !sum || !pick) return;
  offer.hidden = !streams;
  if (!streams) {
    sum.textContent = "";
    pick.textContent = "";
    return;
  }
  sum.textContent = `Sum the ${streams} mics`;
  // The other real shape: a film rip whose second stream is a commentary
  // track, where summing is nonsense. Track 1 is `audio_stream: 0` — the
  // label counts the way a person does, the payload the way ffmpeg does.
  pick.textContent = "Keep track 1";
}

async function postImport(payload) {
  lastImportPayload = payload;
  setMixOffer(0);
  try {
    await ctx.api("/api/import", payload);
  } catch (err) {
    // A 400 (bad path, bad project) or 409 (ImportBusyError) both land
    // here before any "running" event would.
    setImportStatus("error", err.message);
    ctx.emit("toast", err.message);
  }
}

async function onImportSubmit(event) {
  event.preventDefault();
  if (!ctx || importBusy) return;
  const source = $("import-source").value.trim();
  const clipIdRaw = $("import-clip-id").value.trim();
  const copy = $("import-copy").checked;
  if (!source) return;
  await postImport({ source, clip_id: clipIdRaw || null, copy });
}

/* A two-mic container is refused rather than registered as if the first mic
   were the recording, and until this existed the only ways to comply were
   CLI flags — a refusal the window could state and not act on, in the one
   pane whose point is that the terminal is never required. The server sends
   `audio_streams` on the error event, so nothing here reads the sentence. */
async function onImportMix() {
  if (!ctx || importBusy || !lastImportPayload) return;
  await postImport({ ...lastImportPayload, mix: true });
}

async function onImportPick() {
  if (!ctx || importBusy || !lastImportPayload) return;
  await postImport({ ...lastImportPayload, audio_stream: 0 });
}

function onImportEvent(data) {
  if (!data || typeof data !== "object") return;
  if (data.status === "running") {
    importBusy = true;
    setImportFormBusy(true);
    setMixOffer(0);
    setImportStatus("running", `importing ${data.source}…`);
  } else if (data.status === "done") {
    importBusy = false;
    setImportFormBusy(false);
    setMixOffer(0);
    lastImportPayload = null;
    setImportStatus("done", `imported ${data.clip_id}`);
    const form = $("import-form");
    if (form) form.reset();
    // Unlike transcribe/attach above, ops.import_media DOES write the
    // manifest, so `project-changed` follows on its own (webui.py's
    // `_revision` poll, ≤0.5s) and every pane — including this one, via
    // app.js's normal reload — picks it up without help. This refresh() is
    // just for a snappier reflection in THIS pane specifically, not a
    // second reload mechanism.
    refresh();
  } else if (data.status === "error") {
    importBusy = false;
    setImportFormBusy(false);
    // The server's own message, verbatim — never invent a reason.
    setImportStatus("error", data.error || "import failed");
    // `setImportFormBusy(false)` re-enables every form element including
    // this button, so the offer has to be (re)drawn after it, not before.
    setMixOffer(data.audio_streams || 0);
  }
}

function render() {
  const list = $("assets-list");
  if (!list) return;
  list.textContent = "";

  if (!data) {
    list.append(el("div", "pane-placeholder", "loading…"));
    return;
  }

  list.append(el("div", "asset-section-label", `clips · ${data.clips.length}`));
  if (!data.clips.length) {
    list.append(el("div", "pane-placeholder", "no clips imported"));
    // The one state where adding footage IS this pane's job, so the form
    // opens itself. Only ever opened here, never closed — a project that
    // gains its first clip keeps whatever the person last chose.
    if (!importOpenedForEmpty) {
      importOpenedForEmpty = true;
      setImportOpen(true);
    }
  }
  for (const clip of data.clips) list.append(buildClipRow(clip));

  list.append(el("div", "asset-section-label", `cards · ${data.cards.length}`));
  if (!data.cards.length) list.append(el("div", "pane-placeholder", "no cards yet"));
  for (const card of data.cards) list.append(buildCardRow(card));
}

async function refresh() {
  if (!ctx) return;
  const seq = ++refreshSeq;
  let next;
  try {
    next = await ctx.api("/api/assets");
  } catch (err) {
    // A project with a broken manifest is the only realistic way this
    // fails, and `/api/view` already surfaced that on load — no second
    // toast for the same fact, just an empty pane rather than a stale one.
    next = null;
  }
  if (seq !== refreshSeq) return; // superseded by a later refresh
  data = next;
  render();
}

/** Open or close the add-footage form, keeping `aria-expanded` honest.
 *
 * `hidden` alone would not close it — `.import-form` carries an author
 * `display: flex`, which outranks the UA's `[hidden] { display: none }`, so
 * app.css carries the companion `[hidden]` rule this depends on. */
function setImportOpen(open) {
  const form = $("import-form");
  const toggle = $("import-toggle");
  if (!form || !toggle) return;
  form.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  toggle.textContent = open ? "− Add footage" : "+ Add footage";
  if (open) {
    const source = $("import-source");
    if (source) source.focus();
  }
}

export function init(passedCtx) {
  ctx = passedCtx;
  ctx.on("import", onImportEvent);
  ctx.on("transcribe", onTranscribeEvent);
  const form = $("import-form");
  if (form) form.addEventListener("submit", onImportSubmit);
  const mix = $("import-mix");
  if (mix) mix.addEventListener("click", onImportMix);
  const pick = $("import-pick");
  if (pick) pick.addEventListener("click", onImportPick);
  const toggle = $("import-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => setImportOpen(form ? form.hidden : true));
  }
}

export function update(state) {
  lastView = state;
  refresh();
}
