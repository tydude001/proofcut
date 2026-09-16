# proofcut — the MCP surface, measured, and what to change

Provenance: Tyler asked on 2026-09-16 how proofcut could be improved for MCP,
as a plan and nothing built. Sources: `server.py` and the installed `mcp`
2.0.0 (read, not recalled); the three trials' own event streams
(`~/proofcut-work/spikes/agent-trial*/runs/*/events.jsonl`, re-profiled
today); Claude Code's MCP and CLI reference pages, fetched 2026-09-16 against
the installed 2.1.273; and two measurements made today, kept with their
scripts and raw output in `~/proofcut-work/spikes/mcp-plan/`. Every number
below is from one of those, and the two that decide the order were measured
rather than inferred.

Status lives **only** in the wiki's Open items table; this file never carries
a status header. When a step ships, HISTORY.md gets a named section and the
step here gains a one-line pointer.

## The finding that frames it

**Every model call the agent panel and the trial harness make pays for all 92
tool definitions, and that is proofcut's own doing.** Claude Code has deferred
MCP tool definitions by default since tool search shipped: at session start
only tool *names* and the server's `instructions` load, and a definition is
fetched through the built-in `ToolSearch` tool when the model needs it. The
agent panel spawns `claude -p --tools ""` to strip the built-in tools, and
that strips `ToolSearch` with them — so every proofcut definition loads
upfront, on every turn, for the whole run. Measured today, same prompt ("call
ping and report the version"), same server, same project:

| `--tools` | tools loaded at turn 1 | turn-1 context | turns | cost |
|---|---|---|---|---|
| `""` (the panel and the trials today) | 92 | 92,266 tokens | 2 | $1.87 |
| `"ToolSearch"` | 1 (`ToolSearch`; 92 names) | 9,359 tokens | 3 | $0.19 |

The second run spent one turn searching (`ToolSearch` returned a
`tool_reference` to `ping`), then called it. Ten times cheaper for the
smallest task proofcut has.

The three recorded trials show the same prefix from the other side. Their
turn-1 context was 57,865 / 61,053 / 63,327 tokens (87, 89 and 92 tools), and
that fixed prefix was **81%, 43% and 76%** of every context token billed
across the run — 3.8M of 4.6M on the demo trial, 8.4M of 19.4M on the
real-footage trial, 4.1M of 5.3M on the film. Meanwhile **53 of the 92 tools
were never called in any of the three runs**, and their definitions are 52%
of the `tools/list` payload (132 KB of 254 KB). The one-string change in step
1 makes both facts stop mattering, and it is why the rest of this plan is
about what deferral then makes load-bearing: the 786-byte `instructions`
become the only always-loaded text, a description over 2 KB is silently cut,
and a reply too large for the client becomes a file path a confined agent
cannot open.

## What the surface is today

Facts, each read off the wire or the installed package, so a later reader can
tell what moved.

- **92 tools**, every one a synchronous `def` returning `dict[str, Any]`
  (five sheets return `Any` to carry an `Image`). None takes the SDK's
  `Context`, so none reports progress, none logs, none elicits. No resources,
  no resource templates, no prompts, no completions, no icons, no `title`, no
  `website_url`; `MCPServer(...)` is passed `name`, `version` and
  `instructions` and nothing else. `_ANNOTATIONS`, `_PARAM_DOCS` and the
  `-C` confinement are the whole of what `_tool()` adds.
- **`tools/list` is 254 KB**: 93 KB of descriptions, 132 KB of input schema
  (13% of it pydantic's generated `title`s and `anyOf … null` unions), 7 KB of
  output schema. Ten descriptions — `reframe_sheet` (2,996 bytes), `music`,
  `vo_synth`, `reframe`, `vo_extend`, `reframe_coverage`, `reel`, `export`,
  `reframe_detect`, `footage_sheet` (2,093) — are over the 2 KB at which
  Claude Code truncates a description, so their tails are not read by the
  client this repo measures against. (Bytes, not characters: the em-dashes
  count three each.)
- **`instructions` is 786 bytes** (cap 2 KB): the usual order, the word-index
  rule, `phrase=`, `undo`. No trial doc has ever quoted or judged it.
- **Two clients are attested**: `claude -p` (the panel and
  `scripts/agent_trial.py`) and the Claude Code plugin. Claude Code's
  `initialize` advertises `roots` and `elicitation`, and calls `prompts/list`
  and `resources/list` at startup — both answer empty. The server is spawned
  in the directory `claude` was launched from, not in any project.
- **Reply sizes from the trials**: `get_transcript` 8.4 KB per call on real
  footage, `caption_view` 19.6 KB in one call, `footage_sheet` 8.2 KB of
  table beside each image, `cut_by_time` 3.1 KB per call, `card_templates`
  12.6 KB — of which the agent used one template. An unbounded
  `get_transcript` on the 5.6-minute Scream VO (1,150 words) is 94 KB, about
  23K tokens: Claude Code warns at 10K, and above 25K it writes the reply to a
  file and hands the model the path — which an agent under `--tools ""` has
  no `Read` to open.
- **Timeouts the client applies**: a stdio tool call is aborted after 30
  minutes with no reply *and no progress notification*; a wall-clock limit
  only if the config sets one. Interactive sessions move a call past two
  minutes to a background task; `-p` does not unless asked to. A `transcribe`
  or a melt render of a long film is silent for its whole run today.
- **Long-running ops already have a job shape in the window** — `RenderJob`,
  `TranscribeJob`, `ImportJob`, `ReframeDetectJob`, `ReframeSheetJob`,
  `ProxyJob`, `SeedJob` in `webui.py` — each wrapping the same `ops` call the
  MCP tool wraps, each publishing `running`/`done`/`error` on the pane bus.
  They report state, not progress: nothing in `ops` emits a fraction.

## The steps, ranked

Each is judged the way the tool definitions were judged: on the wire
(`tests/test_server_stdio.py` reads `tools/list` from the real process) and
by the instrument that already exists (`scripts/agent_trial.py`, all three
briefs, scored by `score()`, with `events.jsonl` kept). Never by reading a
tool body.

### Step 1 — let the confined agent search

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

The `--tools` value in `webui.AgentSession._spawn` becomes `"ToolSearch"`
instead of `""`, and gets a name beside `_AGENT_ALLOWED_TOOLS` — because
`scripts/agent_trial.py` restates the literal `""` at its own spawn today
(line 412) while its docstring says the flags are imported; naming it is
what makes the trial move with the panel. `ToolSearch` is a built-in that only
answers "which tool", and the confinement the panel exists for is untouched:
`--allowedTools mcp__proofcut__*` still names what may run, and the five
disallowed built-ins stay disallowed.

The risk is real and measurable: the agent now has to *find* a tool before it
can call it, from names and `instructions` alone, and a search is a turn. The
measurement is the three trials re-run once each — demo, real footage, film —
against their recorded runs: checks passed, turns, distinct tools, cost,
turn-1 context. A brief that passed 12/12 and now passes 11/12 is a finding
about step 2, not a reason to revert.

Done when: all three briefs pass every check they passed before, turn-1
context is under 12K tokens on each, and TRIAL.md records the fourth run of
each beside the third. Cost to find out: roughly the three trials' own
$1.31 + $6.94 + $2.27 on Opus 5.

### Step 2 — `instructions` becomes the map

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

With deferral, `instructions` is the one thing about proofcut the model reads
before it decides to search, and Claude Code's own guidance for it is a
skill's: what category of task, when to search, what the key capabilities
are. Today it is a command order plus three invariants. It becomes, under 2
KB, in this order: what proofcut is and is not (edits video from a project on
disk, nothing uploaded); the phases and the tool *family* to search for each
(footage in, transcript, cut, picture/cues, music and holds, cards, export,
checks); the invariants an agent breaks silently without (word indices never
renumber; `plan=` before a write; every sheet is a picture in the reply;
`undo`; `check_frames`/`verify` after an export); and the three tools to read
first on any project (`timeline_status`, `finish_report`, `list_media`).

A test pins it under 2,048 bytes, on the wire, because the cap is the
client's and the truncation is silent.

### Step 3 — no description over the client's cap

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

Ten descriptions are over 2 KB and lose their tails in Claude Code. The
tail is usually the most operational paragraph — `export`'s loudness
refusal band, `reel`'s pinning rule — and truncation reports nothing. Each
comes under the cap by moving argument-specific text into `_PARAM_DOCS`
(where it belongs and is not capped per description) and keeping the
description to what the tool does and when to reach for it. A test holds
every description at or under 2,048 bytes, beside the existing one holding
every argument documented.

### Step 4 — decide which tools are always loaded, by measurement

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

`_meta["anthropic/alwaysLoad"]` on a tool loads its definition upfront under
deferral; `_tool()` gains a way to set it. The candidates are the tools every
brief reached for — from the three runs, `import_media` (13 calls),
`cue_add` (18), `export`, `check_frames`, `verify`, `get_transcript`,
`cut_by_transcript`, `seed_timeline`, `timeline_status`, `list_media` — and
the question is whether their searches cost more turns than their definitions
cost tokens. Two trial runs answer it: none always-loaded (step 1's run) and
the top eight. The recommendation is to start with **none** and add only what
the run shows the agent searching for on every brief; each always-loaded
tool is context on every turn of every session, plugin users included.

### Step 5 — replies that grow with the film get a bound

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

`get_transcript` with no window returns the whole transcript, and on
anything longer than the Scream VO that is over the client's 25K-token limit
and becomes a path. It gets a default window (a `limit` with a stated
default, the reply naming `total_words` and where the window ended — the
shape `first`/`last` already have), and `search=` stays the recommended
first move. `caption_view` and `timeline_view` are the same shape one film
later. `card_templates` returns all six templates' full slot tables, 12.6 KB
for the one the agent went on to use; it gains a `name=` to ask for one. Where a reply legitimately needs the room —
`timeline_view` for the window — `_meta["anthropic/maxResultSizeChars"]`
raises the client's cap for that tool instead of trimming the reply.

A test asserts the unbounded default on the largest transcript fixture stays
under 25K tokens' worth of bytes; the number is the client's, and it is
written once in `server.py` beside the reason.

### Step 6 — progress from the long tools

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

`transcribe`, `export`, `vo_synth`, `describe`, `reframe_detect`,
`reframe_coverage` and `footage_sheet` take the SDK's `Context` and call
`ctx.report_progress(current, total, message)`. Two things make this worth
building rather than noting: a progress notification is what resets Claude
Code's 30-minute idle abort, so a long render that says nothing is a render
the client will kill while melt is still writing; and the message rides
into the client's task view when a call is backgrounded. The progress source
is a callback threaded through the `ops` call — whisper's own segment lines
on stderr, melt's frame counter, `plan_windows`'s window count — and the
`webui.py` jobs take the same callback to draw a bar where today they draw
"running". `ctx.info`-style log notifications are deprecated in the
installed SDK (SEP-2577) and are not the route.

The stdio test drives `transcribe` against the whisper stub and asserts at
least one `notifications/progress` arrived before the result; a real render
is watched once, by hand, for the frame count reaching the total.

### Step 7 — the briefs become prompts

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

Claude Code asks `prompts/list` at every start and answers empty. The three
briefs the trials passed with — a cut to length with b-roll and captions, a
whole film with score, card and master, and a check of a finished project —
are the best-measured text proofcut has, and they are a script's constants.
They ship as MCP prompts (`cut`, `film`, `review`), goal-not-steps as the
trial rule requires, taking the arguments the brief varies (the media
folder, the target length, the output path). In Claude Code that is
`/proofcut:film ~/footage 90s`, in `-p` too; in any other client it is
`prompts/get`. `agent_trial.py` reads its briefs from the same prompts rather
than its own constants, so the measured brief and the shipped one cannot
drift. A new callable thing is a minor bump (CLAUDE.md's rule).

### Step 8 — an unbound server binds to a project it is standing in

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

The plugin runs `proofcut mcp` unbound, so a Claude Code user working inside
a project passes `path` on every call — the ceremony the trial's first queue
item removed for the panel. Measured today: Claude Code spawns a stdio
server in the directory it was launched from. So `serve()` with no `-C`
binds to the cwd **when the cwd holds `proofcut.json`**, and is otherwise
exactly the unbound server it is now; `doctor` and `ping` say which. MCP's
`roots` is not the route — the installed SDK marks the capability deprecated
as of 2026-07-28. `test_binding_does_not_change_the_advertised_tool_schema`
already holds one schema for both states; a test starts the server in a
project directory with no `-C` and calls `timeline_status` with no `path`.

### Step 9 — what the directories read at `initialize`

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

`MCPServer` takes `title`, `website_url` and `icons`; `server.json` already
states the first two for the registry and the handshake states neither.
Filled in from the same strings, with `tests/test_version.py`'s discipline:
one test reads them back off the wire against the listing files. Small, and
the kind of gap a directory's next grading pass finds.

### Step 10 — resources, only if something asks for them

Claude Code lists them at start and lets a person type `@proofcut:…` to pull
one into context; a `proofcut://status`, `proofcut://transcript/{clip_id}`
and `proofcut://finish-report` are thin reads over ops functions that exist.
But the agent — the client this repo measures — reaches the same facts
through tools, and no run has wanted a resource. Deferred: revisit when a
stranger's install asks, or when the `@` mention is what a demo needs.

### Step 11 — trim the generated schema

Built 2026-09-16: HISTORY.md § The MCP surface, rebuilt for deferred loading.

13% of the input schema is `title: "Clip Id"` and `anyOf: [{type: string},
{type: null}]` that `type: ["string", "null"]` says shorter. Under deferral
each definition is loaded on demand, so this is a per-search saving, not a
per-turn one. Last, and only with a wire test that the collapsed schema
validates the same calls; not worth a hand-rolled schema pass otherwise.

## Considered, and not in the plan

- **Fewer, bigger tools.** 92 flat names read as the obvious thing to
  consolidate, and with deferral their definitions cost nothing until
  searched — names alone are under a thousand tokens. Per-action schemas keep
  pydantic validating each call, and every tool is a CLI subcommand by rule.
  Consolidation would trade both for a smaller list nobody now pays for.
- **Elicitation.** Claude Code supports the form and URL modes, and cancels
  every request in `-p`, which is where the panel and the trials run. The
  place it tempts — an ambiguous `phrase=` — already refuses with every
  candidate's words, and the agent has the context to choose; a dialog would
  hand the model's decision to a person.
- **Sampling** (`ctx.session.create_message`). proofcut does not choose the
  clip, the words or the frame (HISTORY.md § Choosing the b-roll); asking the
  client's model from inside a tool would be proofcut choosing by proxy.
- **`_meta["anthropic/requiresUserInteraction"]`** on `transcribe` or
  `seed_timeline`, the two SET/EDIT tools that replace without `undo`. It
  forces a prompt in every mode and is *denied* in non-interactive runs, so
  it would break the panel's own transcribe; `plan=`, the docstrings' warnings
  and the hint table are the guard.
- **MCP Apps** (`ui://` resources). Undocumented in Claude Code, and the
  window is `proofcut web`.
- **HTTP transport polish** — `stateless_http`, an event store for
  resumability, the SDK's `TransportSecuritySettings` in place of
  `_LoopbackGuard`. No client has ever driven `--transport http`; nothing to
  measure against.
- **`structuredContent` duplication.** A `dict` reply goes over the wire twice
  (pretty-printed text and structured content). Claude Code 2.1.273 hands the
  model one compact copy; the bytes are stdio's, not the context's.

## Decisions for Tyler

Grouped by what is actionable today; each with a recommendation.

1. **Step 1 first, and re-run the three trials on it** — recommended yes. One
   string, three runs, about $10 on Opus 5, and it settles whether deferral
   costs the agent anything on proofcut's own briefs.
2. **Which tools are always loaded** — recommended none until the step 4 runs
   say otherwise.
3. **Which briefs ship as prompts** — recommended the trial's own two
   (`cut`, `film`) plus a `review` written for the third; the arguments are
   the media folder, target length and output path.
4. **Auto-bind to the cwd** — recommended yes; the plugin's users are the
   ones paying the `path` ceremony now.
5. **Resources** — recommended deferred, with the trigger stated in step 10.

## What this plan deliberately does not do

It touches no `ops` function, renames no tool, changes no CLI subcommand, and
adds no new check on a film. It does not decide the fourth trial's material;
step 1's runs repeat the three recorded briefs so the comparison is like for
like. And it does not claim the panel gets cheaper for a *person* typing at
it: the saving is measured on `claude -p` runs, and a human session's cost is
whatever their Claude Code plan already charges.
