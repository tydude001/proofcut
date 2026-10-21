# NEXT — three directions after the queues closed — 2026-08-25

Provenance: a full-repo review made 2026-08-25, after the last item of the
completion queue closed and the workspace redesign's density pass shipped.
Sources: PLAN.md § Direction and order, § The completion queue, § Open
questions; the wiki's Open items table; HISTORY.md 2026-08-17 through
2026-08-25. This file owns the recommendation and the reasoning, cited by
name as ever. **Status of anything here lives in the wiki's Open items
table, never in this file** (root conventions § Knowledge stores).

## Where the repo stands

Every queue the project has is closed. The Daydream parity queue: closed
(PLAN.md § Next — the Daydream parity queue, final entry). The completion
queue from the Scream video: all ten items closed (PLAN.md § The completion
queue). docs/plans/POLISH.md: all seven steps shipped 2026-08-24. The workspace
redesign and its density pass shipped through 2026-08-25 (HISTORY.md § The
workspace redesign; § The five defects behind "their UI looks cleaner").

What remains open is almost entirely watches and calls only Tyler can make:
the redesigned workspace has never had a real edit done in it, the four
sheets are untried on a real job, the vo_synth listening verdict, the OBS
mic routing, the publish call. The build engine is idle not because nothing
is left, but because the plan's own next steps sit in Tyler's hands. That
leaves three real directions, ranked below — and the ranking is a
recommendation, not a survey.

## 1. Recommended first: the closed loop — an agent edits a video end to end, unattended, and the run is measured

proofcut's thesis has always been agent-driven editing, and every measurement
so far has been of *pieces* — a tool, a sheet, a check. The sheets that
landed 2026-08-24/25 were the last missing sense: for the first time an
agent can look at the timeline (`shot_sheet`), the footage
(`footage_sheet`, `contact_sheet`), and the framing (`reframe_sheet`)
inside a tool result (HISTORY.md § The shot sheet; § The two sheets an
agent could not see). Nobody has yet handed an agent a brief — "cut this
raw recording into a 60-second piece with b-roll and captions" — and
watched it drive `claude -p` against the MCP server from import to export,
then scored the result.

Why this ranks first:

- **It is the differentiator.** Everything in the parity queue chased what
  Daydream already does. An agent that edits a whole video through sheets
  and word-addressed cuts is the thing Daydream does not do.
- **It is the missing measurement.** The repo's standing rule is measure
  before building — and the product's central claim is the one thing never
  measured as a whole. The agent panel got tools 2026-08-18 (HISTORY.md
  § The agent panel had no tools at all) and eyes 2026-08-24; the run that
  uses both together has never happened.
- **It exercises the untried sheets from the agent side.** The wiki row
  asking whether the tiles answer the question stays Tyler's human-side
  watch; this answers the same question for the client the sheets were
  built for.
- **Its failures become the next build queue with evidence attached** —
  the shape every good queue in this repo has had.
- **It produces the launch demo** for direction 2: a recorded "watch
  Claude cut a video" run over the demo project is the README's missing
  centerpiece.

Concrete shape: run first on the demo project (generated, regenerable —
HISTORY.md § The demo project), then on a copy of a real project, never the
originals. Score against the checks the shipped film trusts (`film_check`,
`verify`, `finish_report`) and against a watch. Log every point where the
agent stalls, guesses, or reaches for a tool that does not exist; deliver
the failure list as the queue that follows this file.

## 2. Close behind, partly parallel: the public launch

The repo is one scrub away from publishable. Licence, the short README
front door, generated demo media, `proofcut doctor`, scripted screenshots —
the works-for-anyone gap was closed deliberately over the last two weeks
(docs/plans/POLISH.md, all seven steps). What is left, per the wiki row *publish
decisions before the repo goes public*:

- **The exposure scrub** — HISTORY.md carries 7 lines with the tailnet
  address, MagicDNS name, IPv6 and a `/home/<user>` path, plus `~/lucid-*`
  paths there and in PLAN.md. Agent-doable, delivered as a diff for
  review.
- **A fresh-checkout dry run** — clone to a clean directory, `doctor`, the
  demo, the suite. Agent-doable. The works-for-anyone claim has been built
  but never rehearsed on a checkout that is not this one.
- **Which internal docs ship** — Tyler's call, unchanged.
- **The screenshot question** — the set comes off the demo project by
  script; real footage reads better and that swap is undone.

A launch lands much better with direction 1's demo in hand, which is a
second reason 1 runs first.

## 3. The co-hosted recording — the critical path is physical

**Shelved, 2026-10-21:** Tyler is not buying the second mic. So nothing below
moves, and speaker attribution stays measured on synthetic voice only, until
a second mic exists.

The co-hosted note's steps 2 and 5 explicitly **have nothing to be built
against until the five-minute two-mic test recording exists** (PLAN.md § The
co-hosted recording — the design note), and right now `RecTracks=3` records two copies of one mix,
because the scene collection holds no audio sources at all.

The two things only Tyler can do, and the sooner the better:

- Route one mic to each track in OBS's Advanced Audio Properties, once the
  mics exist.
- Make the five-minute two-mic test recording, which pins the margin floor.

The moment that file exists, the proofcut-side work unblocks: pinning
`MARGIN_DB` against a real recording rather than one synthetic voice
(HISTORY.md § Speaker attribution, built — at chance on simultaneous
speech, measured), the co-hosted note's deferred steps, and attribution
judged on genuine cross-talk.

## The order, grouped by who can act

**Agent-actionable: TRIAL.md's second queue, three items.** Both of the
directions above ran the same day, and what they produced replaced them — the
trial's evidence and the seven-item queue it became are [TRIAL.md](TRIAL.md),
and the scrub and dry run are TRIAL.md § The publish rehearsal. That queue then
closed too, 2026-08-25/26 (HISTORY.md § The trial's queue, closed; § The
seventh queue item, decided and built).

Direction 1 then ran a **second** time, 2026-09-03, over real footage rather
than the generated demo — the confound the first run named itself — and
produced three more gaps: TRIAL.md § The second trial, § The queue — three
gaps. Those are the agent-actionable work today.

**Tyler's, time-sensitive first:**

1. OBS mic routing + the two-mic test recording — gates speaker attribution.
2. The standing watches: one real edit in the redesigned workspace, the
   sheets on a real job, the vo_synth listening verdict.
3. Making the GitHub repo public — every doc ships and the screenshots stay
   on the demo project (HISTORY.md § The repo, readied for strangers).
