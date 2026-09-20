# Undoing an agent's turn as one decision

Written 2026-09-20, off PRIOR-ART.md § OpenChatCut (its `begin_edit_session` /
`review_edit_session` lands an agent's edits as a single undo step). Unbuilt.
Status of the work lives in the wiki, not here.

## The gap

An agent that runs ten mutating tools leaves ten snapshots, and walking its
turn back is ten `undo` calls — with no redo, and nothing on the way telling
the caller where the turn began. `Project.snapshot` is once per `Project`
instance (`project.py`), and every op opens its own, so one op is one undo
step and one *turn* is many. The pieces that would make a turn reviewable
already exist and stop one call short: `changes(steps=N)` states what the
last N mutations did, and `undo` takes no `steps`.

**How many is many.** The unattended trial projects (`agent_trial.py` starts
each from `proofcut init`) hold 10–21 snapshots — `agent-trial-film` 16,
`agent-trial-real` 21, `agent-trial-film-control` 11, `local-director`'s two
10 and 12 — counted off `cache/history/*.manifest.json` under
`~/proofcut-work/spikes/`. That counts a whole project, imports and seed
included, so it is an upper bound on one turn's steps, not a measurement of
one. The long B7 rebuilds hold 100–165. History is never pruned. Nobody has
asked for this; the evidence is that the shape exists elsewhere and the cost
is real.

## What it is not

**Not a change to `snapshot`'s once-per-instance rule.** That rule is what
keeps an op writing both files to one undo press, and `reel`, `migrate`'s
`snapshot=False` and the "undo depth of a fresh seed is 2" tests all lean on
it. A session that suppresses inner snapshots would also lose per-op undo
*inside* the turn, need process-wide state (a CLI call is a process), and put
a second writer path beside `ops`. None of that is needed: the boundary is a
number the caller can already read (`undo_depth`, on `status`,
`timeline_view`, `import_edit` and `undo` — a mutator's own reply does not
carry it, so an agent reads `status` at the start of a turn), and a snapshot is already a separate numbered file.

**Not OpenChatCut's approval gate.** Holding an agent's writes until a person
approves is a queue in front of `ops`, which is a different feature from
undoing after the fact. `plan` on each mutator is the preview proofcut has.

**Not explicit `session_begin`/`session_end` tools.** Every definition is
paid on every turn (docs/plans/MCP.md), an agent forgets the closing call or
nests them, and the state would need persisting across CLI processes to mean
the same thing there.

## The design

1. **`undo(steps=1, plan=False)`**, mirroring `changes(steps)`. It refuses,
   before restoring anything, a `steps` outside `1..undo_depth` — there is no
   redo, so a partial walk-back is the one outcome that cannot be repaired.
   `plan=True` resolves and writes nothing, and returns `changes(steps=N)`'s
   answer: the read-before-you-destroy the tool description already asks for
   by hand. The stale-write refusal (`ProjectConflictError`) applies on the
   first restore and holds for the rest, since `restore` re-stamps after each.
   Reply keys stay what they are, computed after the last restore, plus
   `steps`; `restored_from` is the last snapshot consumed, i.e. the state the
   project now equals.
2. **CLI twin `undo --steps N [--plan]`**, `_PARAM_DOCS` rows for both, and the
   tool description says to read `changes` first. `_ANNOTATIONS` keeps
   `undo` destructive; a larger `steps` widens what it destroys and does not
   change the class.
3. **Optional, and only on request: the agent panel's "Undo this turn".**
   `AgentSession.send` records `len(project.snapshots())`; the button shows
   `changes(steps=delta)` and then calls `undo(steps=delta)`. It hides at
   delta 0 and **refuses when the depth is now below the start** — someone
   undid by hand mid-turn and the count no longer names the turn. The window
   posts to the same op and decides nothing itself.

## What it cannot do

- **A snapshot is not everything a turn wrote.** Transcripts are not
  snapshotted (`changes`' own docstring), and renders, thumbnails and the
  sheets are cache. Undoing a turn that ran `transcribe` leaves the transcript.
  Undoing an import leaves its media on disk (`undo`'s existing rule).
- **A concurrent edit is inside the count.** The window's own edits during a
  turn are snapshots too, so "undo this turn" undoes them with it. Two
  writers in one project is the unsolved problem TRIAL.md names; this
  inherits it and does not add to it.
- **No redo**, unchanged. `plan` is the guard.

## Order

1. Step 1–2 above — a day, one op, one CLI flag, tests beside
   `tests/test_ops_undo.py` (all-or-nothing on an over-long `steps`; `plan`
   writes nothing; a two-step undo equals two one-step undos byte for byte;
   the stale-write refusal fires mid-walk without half-restoring).
2. Measure the real number first if it is to be quoted: run one trial and
   diff `undo_depth` across the unattended turn, rather than the whole
   project's count above.
3. Step 3 only if a person using the panel asks for it.
