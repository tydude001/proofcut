# LOCAL — can proofcut run on local models, and on what hardware — 2026-09-18

The question: can proofcut run *effectively* with no cloud model at all, and
what hardware would that take?

The short answer is that four of its five model roles already run locally,
and they have always been measured on this box's RTX 5070. The fifth is the
agent that reads a brief and drives the tools — the *director* — and every
scored run of it but four has been Claude. Whether a local model can direct is
**measured on two of the three briefs, and it passed their checks** (§ The run,
2026-09-19) — though two of its four films kept a stutter the checks cannot
see; the third brief is unmeasured. This note says what the job
asks for, what would make it hard, what hardware it would take, and what the
run found.

What is measured here is labelled measured, with the section that holds it.
Everything else is an estimate, and it says so.

## The five model roles

| Role | Model | How proofcut reaches it | Measured on the RTX 5070 (12 GiB) |
|---|---|---|---|
| Speech to text | openai-whisper `turbo` (`asr.DEFAULT_MODEL`) | a binary: `PROOFCUT_WHISPER`, then PATH | 16.16 s for 120 s of audio, 7.42× real time, **peak 6.47 GiB** — HISTORY.md § The scale spike's GPU half, measured |
| Footage description (`describe`) | Qwen2.5-VL-7B-Instruct, 4-bit NF4 (`describe.MODEL`) | an interpreter: `PROOFCUT_VLM` runs `_vlm_worker.py` | 139 windows over the Scream project in 498.6 s, **11.5 of 12.2 GiB** with `llama-server` resident — HISTORY.md § `describe`, step 1 of b-roll by description |
| Faces (`reframe_detect`, `--extremes`) | insightface `buffalo_l` (`faces.MODEL`) | an interpreter: `PROOFCUT_FACE` runs `_face_worker.py` | ~0.5 s a probe |
| Voice (`vo_synth`) | Qwen3-TTS, zero-shot from a reference clip | an interpreter: `PROOFCUT_TTS` + `PROOFCUT_TTS_MODEL` | 0.989 on the model's own speaker encoder — HISTORY.md § `vo_synth`, built |
| **Director** | **Claude, through `claude -p`** | the agent panel and `scripts/agent_trial.py` | three scored trials, below — all Claude; four local runs, § The run, 2026-09-19 |

The first four are local by design, not by accident: proofcut's own venv
holds no torch, and each model is a subprocess behind an environment
variable (CLAUDE.md § Whisper is a subprocess). None of them calls a
network. So "local proofcut" is a question about the director alone.

## The director

### Where the swap goes

There are two routes, and they are not the same size.

1. **Any MCP client.** `proofcut mcp` is model-agnostic — it serves tools
   over stdio or HTTP and has no idea what is calling. A local agent
   framework that speaks MCP can drive every tool the CLI has. This route
   needs nothing from proofcut, and it is the one the launch posts mean
   by "the agent is whatever MCP client you point at it"
   (`~/proofcut-work/spikes/launch-listings/POSTS.md`).
2. **The web UI's agent panel, and the trial harness.** Both spawn one
   binary: `webui._agent_bin()`, which is `PROOFCUT_AGENT_BIN` or `claude`.
   The override exists (added for Windows' `claude.cmd`,
   docs/plans/PORTABILITY.md), but the **contract is Claude Code's CLI**,
   not "an LLM". A replacement has to:
   - accept `-p --verbose --input-format stream-json --output-format
     stream-json --mcp-config <file> --strict-mcp-config --tools
     --allowedTools --disallowedTools --permission-mode manual`, and
     `--model` when one is chosen (`AgentSession._spawn`);
   - start the MCP server the config names and confine itself to it, which
     is what `--tools` and `--strict-mcp-config` do for `claude`;
   - emit Claude Code's stream-json: a `system`/`init` event carrying
     `mcp_servers` (`agent.js` draws a failure without proofcut in it),
     `assistant` events with `text` and `tool_use` blocks, `user` events
     with `tool_result` blocks — image parts included — and a closing
     `result` event with `num_turns` and `total_cost_usd`, which is what
     `agent_trial.py`'s report reads.

   So route 2 is a shim: a small program that speaks that argv and that
   event stream over a local model's OpenAI-compatible endpoint. One
   exists as a spike, outside the repo (§ The run, 2026-09-19).

### What the job asks of a model

The three scored trials are the workload (docs/TRIAL.md):

| Brief | Checks | Turns | Tool calls | Distinct tools | Images read | Wall | Cost |
|---|---|---|---|---|---|---|---|
| `cut`, the generated demo | 9/9 | 31 | 30 | 22 | 4 | 184 s | $1.31 |
| `cut`, real footage | 9/9 | 77 | 76 | 26 | 10 | 914 s | $6.94 |
| `film`, a whole film | 12/12 | 43 | 42 | 31 | 3 | 192 s | $2.27 |

All three on Opus 5. That is a long, stateful tool-calling session: 30 to
76 calls in a row, each reply read before the next call is chosen, with
no person to catch a wrong turn.

### What makes it hard locally

In order of how much each would cost.

1. **The tool definitions.** 109 tools are advertised, and their
   definitions come to **273,677 bytes of JSON** (measured 2026-09-18 off
   `server.mcp.list_tools()`). Claude Code never loads them all: under
   `--tools ToolSearch` it loads only `server.INSTRUCTIONS` and searches
   for a tool when it needs one, which took the turn-1 context of a `ping`
   from **92,266 tokens to 9,359** when there were 92 tools
   (docs/plans/MCP.md). Most local agent clients have no deferred loading
   and send every definition on every turn. On a model whose speed falls
   with context (next item), that is the whole problem. A shim would have
   to do what `ToolSearch` does, or the surface would have to shrink.
2. **Usable context is not the context window.** Measured on this box
   (local-llm `notes/reasoning-at-depth.md`): a 128K prompt in a
   `-c 131072` server leaves about 1.9K tokens to answer in, and a
   reasoning model spends all of it on its trace. Generation speed falls
   too — the best local candidate here, Qwen3.6-35B-A3B Q4_K_XL, runs at
   **63 tok/s on a short prompt and 19.3 tok/s at 128K**. A 40-turn
   session that carries the full tool surface is deep from its first turn.
3. **Some tools answer with a picture.** `shot_sheet`, `footage_sheet`,
   `contact_sheet` and `reframe_sheet` return image bytes for the model to
   look at, and the trials read 3 to 10 of them. A text-only director
   loses framing review and b-roll checking entirely. Nothing in proofcut
   gates on what a model says it saw (CLAUDE.md § An MCP tool result can
   carry an image), so a blind director still produces a render. It just
   cannot judge one.
4. **Fabrication is worse than failure here.** Of the three local models
   measured on this box, Kimi-Linear-48B-A3B fabricates when it cannot
   join across its context, already at 29K (local-llm README). A director
   that reports work it did not do produces a film whose report is false.
   The trial's `score()` reads the render, not the agent's account, so it
   would catch this — which is one more reason the trial is the instrument.

## Hardware

### What this box does now (measured)

RTX 5070, 12 GiB, 32 GB system RAM.

- **All four local roles fit, one at a time.** whisper peaks at 6.47 GiB;
  `describe` fills the card to 11.5 GiB; faces are small, and TTS's peak is
  not recorded here.
  They are sequenced, never run together — a backgrounded TTS synth once
  failed a running whisper verify.
- **A resident director and `describe` cannot share the card.**
  `llama-server` already holds 3.5 GiB permanently (PLAN.md § B-roll by
  description), and `describe` takes the rest. A local director would
  have to be unloaded for `describe` and `vo_synth`, or run on the CPU
  for those stretches.
- **The best local director candidate here is Qwen3.6-35B-A3B**, a MoE
  with its experts offloaded to system RAM (`--n-cpu-moe`). 32 GB of RAM
  is the binding limit for that recipe: a Q4 of a 48B MoE does not fit.

**Measured for the demo and `film` briefs, unmeasured for the real-footage
one:** 330–746 s on the demo and 361 s on the film against Claude's 134–191 s
(§ The run, 2026-09-19). An earlier estimate here — an hour or more for the
`film` brief — assumed no deferred tool loading, and was wrong by a factor of ten.

### What would make it comfortable (estimates, none measured)

- **32 GB of VRAM (one RTX 5090-class card).** A 30B-class MoE at Q4
  resident, with room to swap `describe` and whisper in beside it. The
  smallest real step up.
- **48 GB or more (two 24 GB cards, or one workstation card).** A 70B-class
  dense model at Q4 resident *and* whisper and the VLM beside it — the
  point where fully-local proofcut stops being a sequencing puzzle.
- **64 GB of system RAM.** For a MoE with offloaded experts, RAM matters as
  much as VRAM. Doubling it lifts the 48B-MoE ceiling without touching the
  card, and is the cheapest change on this list.
- **A vision-capable director** in any of these, for item 3 above.

None of these is a claim about quality. A card big enough to hold a model
says nothing about whether the model can direct a 40-call edit.

## The run that would settle it

`scripts/agent_trial.py` already imports the panel's own binary and flags
from `webui.py` and scores the render with the same `score()` the Claude
runs used, so a local run is directly comparable to docs/TRIAL.md.

1. Write the shim from "Where the swap goes", pointed at a local
   OpenAI-compatible endpoint serving Qwen3.6-35B-A3B — the one local
   model here that neither fabricates nor fails the join tests.
2. Point `PROOFCUT_AGENT_BIN` at it and run the `cut` brief on the
   generated demo project, the cheapest of the three.
3. Read the prompt size of the first turn from the model server's own log.
   **`agent_trial.py`'s report records turns, calls and cost but not token
   usage**, so it cannot answer this. If it is near the 92K the full
   surface cost Claude Code, deferred loading is the first thing to build,
   before judging the model at all.
4. Compare the score to TRIAL.md's first run, and the wall time to its
   184 s.
5. Only then decide whether a shortfall is the model or the tool surface —
   they want different fixes, and a model judged through a surface that
   costs 90K tokens a turn has not been judged.

## The run, 2026-09-19

Steps 1–4 ran on the generated demo, then the `film` brief, then the demo twice
more. Step 5 had no shortfall to explain: every run passed its checks. The shim
is `~/proofcut-work/spikes/local-director/` (`shim.py`, run through
`claude-local`), a spike and not part of the repo; each run's directory is
beside it. Qwen3.6-35B-A3B Q4_K_XL through llama-swap's `qwen3.6-35b-a3b` seat
(128K, `--n-cpu-moe 28`), thinking off, temperature 0.7.

| run | brief | checks | turns | calls | refusals | wall | what the film says |
|---|---|---|---|---|---|---|---|
| `20260919-153534` | demo | 9/9 | 30 | 38 | 5 | 333 s | "Every cut you make names" **twice** |
| `20260919-154710` | film | 12/12 | 31 | 38 | 4 | 361 s | "names a…" left in before the good take |
| `20260919-155342` | demo | 9/9 | 63 | 71 | 13 | 746 s | clean; 18 `add_captions` calls |
| `20260919-161200` | demo, after the fix below | 9/9 | 34 | 33 | 3 | 330 s | clean; 4 `add_captions` calls |

Claude on the same briefs: 134–184 s and 31–38 turns (demo), 149–191 s and
43–45 turns (film).

**Step 3 — the first turn's prompt.** With every one of the 109 definitions
sent: **64,324 tokens, and 131 s of prompt processing with nothing cached.**
Deferred, through a `ToolSearch` the shim implements: **1,163 tokens, 4.9 s.**
That is the surface's cost to a client with no deferred loading, and it sits
with the 57,867–63,329 Claude Code paid before MCP.md § Step 1. The 1,163 has
none of Claude Code's own system prompt in it, so it is not comparable to TRIAL.md's
8,113. What it settles: a local client needs deferred loading before the
model is judged at all.

**Where the time went** (first demo run, 333 s): 158 s reading prompts, 81 s
generating (3,513 tokens, 62 tok/s falling to 40 as the context grew from 1.5K
to 33.6K), and about 90 s in tools — which includes whisper **on the CPU**, since
the 35B seat holds ~10.7 GiB and turbo peaks at 6.47 GiB (`transcribe` 26 s, two
`verify` 20 s each, `finish_check` 16 s). Claude's runs had a GPU whisper, so the
wall times are not a like-for-like comparison.

**A tool loaded mid-session costs a full re-read.** Qwen's chat template puts
`tools` in the system prompt, so a `ToolSearch` that adds a definition
invalidates the KV cache from the top. In that run turns 2, 4 and 18 each read
their whole context again — 15.5K, 16.9K and 27.2K tokens, **31 s, 33 s and 53 s:
117 of the 158 s.** Every other turn was 90–99% cached and read its new tokens
in under 5 s. Claude's API appends the definitions as blocks and has no such
cost, so TRIAL.md's finding that always-loading a working set did not pay was
measured where a miss costs nothing, and does not carry over to a local
client unchanged.

**The refusals** are the ordering slips TRIAL.md's fourth runs already show
(`finish_report` before `seed_timeline`, `get_transcript` before `transcribe`, a
cut before the seed), plus the `add_captions` trap below.

**`add_captions` told every agent the wrong thing, and Claude was caught by it
too.** The second demo run spent 18 of its 71 calls on it and 746 s where the
first took 333. The tool text called `output` "the burned video under `burn`";
the code writes the `.ass` sidecar to `output` *first*, so an agent that took
it at its word and passed `output=cut.mp4` replaced its own rendered `cut.mp4`
with caption text, and a burn onto that path then read caption text back as its
picture (`Input #0, ass, from 'cut.mp4'`). Claude's four deferred-loading runs
made that call in 4 of their 8 `add_captions` calls and got through only
because each also passed `burn_output=cut.mp4`, which makes the burn overwrite
the caption text with the video. The text is corrected and a media suffix on
`output` is refused before anything is written (`ops.add_captions`, HISTORY.md
§ The local director's next three runs). The run after it made 4 calls, every
`output` a `.ass` path. That is one run and proves no cause; the mechanism is
the same one the earlier two showed.

**The score cannot see a stutter.** Two of the four local films kept the fluffed
take's opening: the first demo run's says "Every cut you make names | Every cut
you make names | a word in the transcript", and the film run's leaves "names
a…" before the good take. Both scored full marks. `retake_removed` asks that the
bad take's own words be gone and `good_take_kept` that the good take's tail is
there, and the two takes open with the same six words, so a cut that stops short
of them passes both; `verify` compares the render with the *timeline*, which has
the stutter in it. The two Claude projects still on disk — the last run of the
demo and of the film brief — are clean, which is two films and no comparison.
`score()` gained `no_stutter` on 2026-09-20 (HISTORY.md § The trial can see
a stutter now); no test had pinned its check list, whatever this paragraph said
until then.

**What this does not settle.**
- **Four runs, sampled.** The same brief took 30 to 63 turns and 330 to 746 s
  before the fix, so a single run's wall time is not a number to quote. The
  real-footage brief, the 77-turn one where context gets deeper and item 2
  above bites, has not been run.
- **Pictures.** The model has no vision projector. Every `footage_sheet` image
  came back and was dropped for it, and it hung b-roll off `broll_brief`'s
  synopses. The demo footage depicts nothing, so it cannot say whether a blind
  director matters; the real-footage brief can.
- **Not Claude Code's system prompt**, whisper on the CPU, thinking off.
- **Fabrication (item 4) is unchecked.** `score()` read each render; nobody
  compared an agent's own summary against its calls.

The launch posts' line — *every scored run used Claude* — is no longer true as
written. `~/proofcut-work/spikes/launch-listings/POSTS.md` is Tyler's draft and
was not edited.
