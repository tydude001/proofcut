# OPENROUTER: the agent panel on any OpenRouter model, 2026-09-29

The ask: make the local-director spike a real feature, pointed at OpenRouter,
so the web UI's agent panel and `scripts/agent_trial.py` can run on GPT,
Gemini, DeepSeek, Qwen or Claude through one key.

This note is the design. It was built the same day; § What was built is
the record, and HISTORY.md § The OpenRouter director, built has the tests.

## Why this reverses LOCAL.md's "the shim stays a spike"

LOCAL.md § The real-footage run kept the shim out of the repo because one
local model, on one brief, failed where Claude passed, and shipping the shim
"would make a local director look like a supported route". That reason was
about the *model*, a 35B MoE on a 12 GiB card. OpenRouter changes the model,
not the shim: 396 of its 464 listed models take `tools`, and 269 of those also
take images (measured off `GET /api/v1/models` on 2026-09-29), which includes
the frontier models from every large lab. The spike's own gaps (no vision, a
128K window) were properties of the local seat, not the protocol.

What LOCAL.md's reasoning still binds: **a route is claimed as supported
only for the models a scored trial passed on.** The feature ships with the
director, and the README names the models that scored, and no others.

## What the spike already settles

`~/proofcut-work/spikes/local-director/shim.py` (368 lines) ran seven scored
trials. Carried over as measured:

- **Deferred tool loading is required.** Every definition on every turn cost
  64,324 tokens on turn 1; a `ToolSearch` the shim implements cost 1,163
  (LOCAL.md § The run, 2026-09-19, step 3). At GPT-5.5's $5/M input that is
  $0.32 against $0.006 for the first turn alone.
- **Claude Code's stream-json is enough of a contract.** `agent_trial.py`'s
  `analyse()` scored the shim's output unchanged.
- **The trial harness needs no change** beyond reading the cost the director
  reports.

## What the spike does not do, and a feature must

1. **Multi-turn over stdin.** The spike reads one brief from `-p <text>`. The
   panel spawns `-p` with no text and writes `{"type":"user",...}` lines to
   stdin, one per prompt, into a live process (`AgentSession.send`). The
   director must loop on stdin, keep the conversation, and emit a `result`
   per turn.
2. **Stop.** The panel writes a `control_request` of subtype `interrupt`
   (`AgentSession.stop`). The director must end the running turn at the next
   model or tool boundary and emit a `result` with a non-success subtype,
   or the composer never clears `busy`.
3. **Pictures.** An OpenAI-shaped `role: tool` message carries a string.
   Images from `shot_sheet`, `footage_sheet`, `contact_sheet` and
   `reframe_sheet` go in a `role: user` message right after the tool results,
   as `image_url` parts with a `data:` URL, one short text line naming the
   tool that returned them. A model whose `input_modalities` lacks `image` gets
   the spike's text placeholder instead, and the `init` event says so.
4. **Cost.** OpenRouter returns `usage.cost` on every response by default.
   The director sums it into `result.total_cost_usd`, which is what the panel
   and `agent_trial.py` already read, and honours `--max-budget-usd` by
   stopping before a call that would start past the cap.
5. **Caching.** Automatic for OpenAI, DeepSeek, Gemini 2.5+ and the others
   OpenRouter lists. Anthropic and Qwen models need `cache_control`, so for
   those the director adds `{"type": "ephemeral"}` at the request's top level
   (OpenRouter's documented form). Cached tokens come back in
   `usage.prompt_tokens_details.cached_tokens` and are reported, not guessed.
6. **The key.** Read from `OPENROUTER_API_KEY`, the name OpenRouter's own docs
   use. Never written to the project, the manifest, the generated MCP config,
   a log, or any event on `/api/events`. The director process inherits it from
   the environment; nothing else in proofcut reads it but doctor.
7. **Errors a person can act on.** A 401 (bad key), 402 (no credit), 404 (no
   such model) or a model without `tools` in `supported_parameters` becomes a
   `result` event whose text says which, rather than a stack trace on stderr.

## Design

### Where it lives

`src/proofcut/director.py`, run as `sys.executable -m proofcut.director`, the
same resolution the MCP config and the model workers use (TRAPS.md § `claude
-p` and the agent panel). No new dependency: the spike uses the `mcp` client
already required and `urllib`. The spike's tunables for a local seat
(`top_k`, `chat_template_kwargs`, `cache_prompt`) are dropped.

**The base URL is `PROOFCUT_DIRECTOR_URL`, default
`https://openrouter.ai/api/v1`.** Keeping it overridable costs one line and
keeps LOCAL.md's local runs reproducible through the shipped code rather than
the spike. It is documented as untested. Ollama and llama.cpp are OpenAI-
compatible, but LOCAL.md's verdict on local directors stands.

### How the panel chooses it

**Recommendation: a model id with a `/` in it goes to the director.**
OpenRouter ids are always `vendor/model` (`openai/gpt-5.5`); no Claude Code
model id has contained one. `_agent_bin()` becomes `_agent_argv(model)`,
which returns `[claude, ...]` or `[sys.executable, "-m", "proofcut.director",
...]` with the same flags. `PROOFCUT_AGENT_BIN` keeps its meaning for the
`claude` side. The alternative, a separate `PROOFCUT_AGENT=openrouter`
switch, makes a person set two things to mean one.

The model `<select>` gains an "OpenRouter" group with a short list and a
"Custom…" entry that takes any id. The list is hand-typed, like the Claude
entries, and holds only models a trial has scored. The page never fetches
`/models`.

### Confinement

The director's only capabilities are the MCP server in `--mcp-config` and its
own `ToolSearch`. It has no shell, file, or web tool to disable, so the
`--tools` / `--allowedTools` / `--disallowedTools` / `--permission-mode` flags
are accepted and **checked**: it refuses to start if `--allowedTools` is
anything other than `mcp__proofcut__*`, so a future widening of the panel's
allowlist cannot silently mean something different on the two routes. This
goes into PLAN.md § The agent panel, in mechanism, beside the `claude` rules.

### doctor

`_agent()` gains an OpenRouter line: `OPENROUTER_API_KEY` set or not, and, if
set, one `GET /api/v1/key` (free, and it answers 401 without a valid key,
checked 2026-09-29) to report that the key works and how much credit is
left. Absent is `–`, never a failure, as for `claude`.

### Tests

These follow TRAPS.md § Tests speak to real processes. A fake OpenAI-compatible
server in the test process scripts replies (a tool call, then text; an image
tool; a 402; an interrupt mid-turn). The real director runs as a subprocess
against the real `proofcut mcp`. No test reaches the network, and CI needs no
key. One test drives `AgentSession` end to end through the director.

### What the trial measures before anything is claimed

`agent_trial.py --model <id>` routes through `_agent_argv` like the panel. The
acceptance runs, each capped with `--max-budget-usd`:

| brief | models | why |
|---|---|---|
| `cut`, generated demo | 3: one OpenAI, one Google, one open-weight (Qwen or DeepSeek) | cheapest; checks the protocol end to end |
| `cut`, real footage | the best two of those | the brief that separated Claude from Qwen locally, and the one with pictures that matter |

At Claude's measured costs ($1.31 and $6.94 on Opus 5) and similar list
prices, that is roughly $20 to $30 of OpenRouter credit. It needs Tyler's key,
so the runs are his to start. The README and MANUAL name the models that
scored, with their scores. "Works with any OpenRouter model" is not claimed.

## Build order

1. `director.py` from the spike: stdin loop, interrupt, images, cost, cache,
   errors, allowlist check. Tests against the fake server.
2. `_agent_argv`, used by `AgentSession._spawn` and `agent_trial.py`.
3. doctor's OpenRouter line.
4. The model picker's OpenRouter group, verified in a real browser
   (TRAPS.md § The web UI).
5. The trial runs above, on Tyler's key. Then MANUAL.md, README and
   LOCAL.md's pointer, which say only what the runs showed.

## Decisions for review

1. **Build it at all, given LOCAL.md's spike verdict.** Recommend yes, for the
   reason above: the verdict was about the model, and OpenRouter swaps the
   model.
2. **A `/` in the model id selects the director.** Recommend yes over a
   second env var.
3. **`PROOFCUT_DIRECTOR_URL` overridable, documented as untested.** Recommend
   yes: one line, and it keeps LOCAL.md reproducible.
4. **Supported means scored.** The picker and README list only models a trial
   passed. Recommend yes.
5. **Trial budget about $20 to $30, on your key.** Recommend the three-model
   demo pass first ($5 or less), then decide on the real-footage runs.

## What was built, 2026-09-29

Steps 1 to 4, as designed, with the five recommendations taken.

- `src/proofcut/director.py`: the spike's loop plus stdin turns, interrupt,
  pictures, `usage.cost` into `total_cost_usd`, `--max-budget-usd`,
  `cache_control` for `anthropic/` ids, `provider.require_parameters`, the
  `reasoning_details` round trip, the allowlist check, and errors that say
  what to do. The model's `/models` listing is read once at start: a model
  missing from it, or without `tools`, is refused before any tokens.
- Qwen models get no `cache_control`. OpenRouter wants their breakpoints on
  content blocks rather than at the top level, and nothing here measured
  whether it matters. A trial on a Qwen id should read `cache_read_input_tokens`
  in its result first.
- `webui.uses_director` and `_agent_command`, used by `AgentSession._spawn`
  and `agent_trial.py`.
- `doctor`'s OpenRouter line, one `GET /key`.
- The picker's OpenRouter group holds only "Other model…" (a typed id stays
  listed for the page's life and is restored from `cache/session.json`),
  because no model has been scored.

Not done: step 5, the scored trials, which need Tyler's key.
