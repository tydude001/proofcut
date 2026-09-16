# TRIAL — the closed loop, measured, and the queue it produced — 2026-08-25

This document was written when the project was called lucid, and says so
throughout; nothing in it was rewritten for the rename (HISTORY.md § The rename).
The commands, environment variables and paths it quotes are as they were
run then: `lucid X` is `proofcut X` today.

NEXT.md § 1 asked for the one measurement lucid has never made: hand an agent a
brief and watch it drive `claude -p` against the MCP server from import to
export, unattended, then score the result. This file is the evidence and the
queue for **both** runs of that measurement — the first over the generated demo
project, and § The second trial over real footage. **Status of anything here lives in the wiki's
Open items table, never in this file** (root conventions § Knowledge stores).

The instrument is `scripts/agent_trial.py`. What shipped and what it cost is
HISTORY.md § The closed-loop trial. What NEXT.md recommended, and why, stays
NEXT.md's.

## What was run

One brief, on the generated demo project — `make_demo.py`'s voiceover with its
deliberate retake, and two b-roll clips whose every second names itself. The
project began at `lucid init` and nothing else: no import, no transcript, no
seeded timeline, because "import to export" is the loop being measured and a
pre-seeded project quietly measures the back half of it.

The client is the agent panel's, imported from `webui.py` rather than retyped:
`--tools ""` (the built-in set gone, so lucid's 87 tools are the agent's whole
reach), `--strict-mcp-config` against a generated one-server config, and that
config naming this interpreter with `-m lucid.cli`, never the name `lucid`.

The whole run is kept at `~/lucid-work/agent-trial/runs/20260825-165027/` —
`events.jsonl` verbatim, the brief it was given, the argv and MCP config it was
spawned with, and `report.md`/`report.json`. `--score-only` re-scores it
without spawning anything.

The brief names the goal and never the steps — the fluffed take gone and
nothing else the narrator meant, b-roll under the lines it belongs to, captions
burned in, rendered to a named path, and the render checked rather than merely
produced. A brief listing the commands would measure the brief's author.

## The result

**The agent met the brief. 9 of 9 checks pass.** 31 turns, 30 tool calls across
22 distinct tools, one refusal, 4 images returned, 184s, $1.31 on Opus 5.

| check | verdict |
|---|---|
| `timeline_seeded` | 2 segments, 12.968s |
| `media_imported` | 4 clips |
| `picture_hung` | 3 shots over 2 assets |
| `retake_removed` | 0 of 6 retake words survive |
| `good_take_kept` | 6 of 6 words of the good take's own tail survive |
| `render_exists` | 2 of 2 claimed paths on disk |
| `frames_agree` | delta 0 — 311 expected, 311 in the file |
| `verify_similarity` | 1.0, 34 heard against 34 expected |
| `captions_burned` | burned into the delivered file |

The edit is not the walkthrough's. It seeded with silence removal **off** and
made exactly one cut, reading "nothing else that the narrator meant to say" as
a reason not to let auto-editor find its own; it hung three shots rather than
two, one per surviving sentence; and it pinned the third shot 6s into the blue
clip so the two blue shots draw different material rather than replaying the
head — the `src_start` pin, reached for unprompted. It escalated to
`verify --windowed` on its own, naming the reason (a single pass collapses an
immediate repeat, so a surviving retake can read clean).

**The control run passes the same 9 checks in 11.6s and no money.**
`--control` meets the brief by script — `docs/DEMO.md`'s own commands plus the
caption burn — and is scored by the identical `score()`. It is not there for
comparison: it is there because a check that fails on the agent because it is
wrong about lucid reads exactly like a check that fails because the agent is.
Every check in the table above has been seen to pass on a known-good edit.

## The queue — where the agent stalled, guessed, or reached for something absent

Each row is evidence-backed, ordered by what it costs a real run.

**All seven are closed** — six on 2026-08-25 (HISTORY.md § The trial's queue,
closed — six of seven) and the last on 2026-08-26 (§ The seventh queue item,
decided and built). The rows below are kept as the run's evidence and are
written in the tense of the run, so **read a "Fix:" as what was proposed, and
every claim about lucid's surface as it stood that day**; each row ends with a
pointer to what actually landed. Status, as ever, is the wiki's.

### 1. `path` is a required argument on every tool, and under `-C` its only legal value is the one the server already knows

29 of the agent's 30 calls carried `"path": "/var/…/proj"` — the thirtieth was
`ping`, which takes none. It had been told outright that it would never need
one, and it passed one anyway on every call, and it was right to: measured
against a `-C`-bound server, omitting `path` is a **pydantic validation error**
(`Field required`), and passing a *different* project's path is refused by
`_confine`. Under `-C` the parameter is ceremony with exactly one accepted
value.

The brief's own wording is the rest of the evidence. It said *"Every tool call
is bound to it, so you never pass a project path"* — false, as the run proved —
and named the project directory anyway in the sentence before. That named path
is the only reason this agent could call anything at all, because it has no
shell and no `Read` with which to find one. The shipped panel does not even
have that: `_handle_agent_prompt` sends the person's prompt verbatim and
nothing injects the project root, so a panel agent is relying on `claude`
reporting its own cwd, which `webui.py` happens to set on the Popen. Either way
the agent must supply a filesystem path it has no tool to discover. (The
default brief now says that every tool takes the project as `path`; the run's
own copy is in its `brief.txt`, unchanged.)

Fix: make `path` optional when `_BOUND_ROOT` is set, defaulting to the binding.
`_confine` already resolves and refuses; the decorator would fill an absent
selector instead of the caller. Unbound servers are unaffected.
`server.py` § `_tool`, § `_confine`.

**Closed 2026-08-25**, as proposed, across all 82 tools — plus
`projectless=True` for `fonts`/`pack_show`, which already meant `path=None` as
"no project" rather than "which one".

### 2. `spot_frames` is the tool for looking at a delivered render, and it hands back paths the agent cannot open

Nothing numeric can say whether the captions are actually on screen or the
right card is under the right line — the repo's own hardest-won caption lesson.
The agent knew that. It registered `cut.mp4` into the project as a clip called
`delivered` so `footage_sheet` would draw it, and said so unprompted: *"That
leaves a fourth clip in the project manifest that is not on the timeline and
not cued… I'd remove it if lucid had a tool for it."*

The tool it wanted already exists. `spot_frames` takes an arbitrary `target`
file, samples frames as PNGs into `cache/frames/`, and ranks them
darkest-first — and it is annotated `-> dict[str, Any]`, so it returns their
**paths**. Under `--tools ""` there is no `Read`, so a path is unreachable;
this is precisely the defect HISTORY.md § The two sheets an agent could not see
fixed for `shot_sheet` and `footage_sheet`, still standing one tool over. The
agent's workaround is a client routing around that hole using the two tools
that were fixed.

Fix: `spot_frames` returns its montage the way the sheets do — same
`ImageContent` route, same `-> Any` annotation (a concrete return type makes
the SDK build an output schema and validate an `Image` against it, which
answers `is_error` from a correct body). Then check the rest of the surface for
the same shape rather than one tool at a time.

Second, smaller gap it exposed: **no way to un-register a clip.** There is
`cue_rm`, `hold_rm`, `unspoken_rm`, and no `clip_rm`. `undo` un-registers an
import but is positional — undoing this one would take every mutation after it.
The polluted state is quiet: `delivered` shows up in `assets`, is cue-able, and
counts toward `media_imported`.

**Both closed 2026-08-25.** `spot_frames` returns its montage the way the
sheets do, and `clip_rm` deregisters a clip — refusing, and naming every
reason, when the timeline, a cue, a hold, the bed, a mark or a transcript still
depends on it.

### 3. `timeline_status` is the first call an agent makes and it refuses on a fresh project

Both trial runs opened by calling it, and both got
`this project has no timeline yet — run lucid seed <clip_id>`. The refusal is
correct about the timeline and wrong about the question: the agent was asking
*what state is this project in*, and the tool that answers that on an
un-seeded project is `assets` or `doctor`, neither of which an agent would
guess first. The message names the fix (`seed`), which is the right next step
only if you already know media has been imported.

Cheapest fix: name the orientation tools in the refusal text. Better:
`timeline_status` answers `seeded: false` with the clip list instead of
raising, which is `off_timeline`'s own precedent — report rather than refuse.

**Closed 2026-08-25**, taking the better option.

### 4. Nothing in lucid notices two writers in one project

Measured the hard way. The first trial run's harness was killed; its `claude`
survived (it is spawned into its own session so a timeout can `killpg` the MCP
server with it) and went on calling tools for minutes. The second run's
`prepare` deleted and re-initialised the project underneath it, and the second
agent then found three cues in a project it had just watched be created and
reported it in its own prose (*"The cue table already existed in the project —
I didn't write it"*) — a run that reads like an agent hallucinating a cue table
and is in fact two agents in one project. Its `events.jsonl` was discarded when
the work directory was reset for the clean run, so this section is the record;
there is no artifact to go back to.

`scripts/agent_trial.py` now holds a PID lock, which fixes the instrument and
not the product. Lucid itself has no advisory lock and no write-conflict check.
`Project.write_manifest` is atomic per write — a temp file and a `replace`, so
nothing is ever torn — and that is a different property: across two writers it
is last-writer-wins, and the loser's edit is gone with nothing raised. A second
`lucid web`, a second panel, or a CLI command run beside either is the same
shape.

**Closed 2026-08-25.** `write_manifest` and `restore` both refuse with
`ProjectConflictError` when the manifest moved since this instance last read
it, so the loser loses cleanly. Nothing still stops two writers starting.

### 5. A refused check is not a failed one, and a consumer will get that wrong

The harness's own first score reported `verify_similarity` as a red **FAIL**
whose detail was a CUDA out-of-memory traceback — another job held the GPU. The
agent's own two `verify` calls had read 34/34 at similarity 1.0 minutes
earlier. Lucid's message is good (it says outright that this is what a busy GPU
looks like); what was wrong was the consumer, which treated "could not run" as
"disagreed". Fixed here — those checks now report unsettled, `lucid doctor`'s
own rule — and it is worth stating because every front end that composes a
check has the same trap available to it.

### 6. Registered-and-not-on-the-timeline has no report of its own

Related to (2) and worth its own row. After the trial the project holds four
clips, one of which (`delivered`) is on no lane and under no cue.
`timeline_view` answers `off_timeline` for a clip you *name*; nothing lists
which clips are in that state. A finish-time report of "registered, never
used" would have caught this without anybody reading the agent's prose.

**Closed 2026-08-25** as `finish_report`'s `unused_clips`.

### 7. The agent cannot see what media exists — it must be told

Deliberate, and recorded rather than proposed. With `--tools ""` there is no
directory listing, so the three source paths came from the brief. On a real job
something has to supply them: the panel's user, a wrapper, or a lucid tool that
lists importable media under a named directory. Worth deciding before the
first unattended run on real footage, not during it.

**Decided and built 2026-08-26** — the third option, as `list_media` /
`lucid list-media <source_dir>`, because a tool is reachable inside the same
`--tools ""` sandbox and needs neither a person nor a new client.

## What the trial says about the four sheets

The wiki row asking whether the tiles answer the question is Tyler's, on real
footage. This answers the other half — the client the image retrofit was built
for — and it answers yes. Four images came back inside tool results
(`contact_sheet` ×2, `shot_sheet`, `footage_sheet`) and the agent used all of
them: it identified the b-roll from its contact sheets (*"plain colour cards
with a burnt-in timecode… no depicted subject, so the choice is structural"*),
which is what made it hang picture by sentence rather than by subject; and it
read the delivered frames back as *"BLUE 0/1/3, then RUST, then BLUE
6/7/9/10"*, which is the `src_start` pin confirmed from the picture rather than
from the shot table. Nothing gated on a reading, per the standing rule.

What it also says is that the retrofit stopped one tool short — see queue item
2. The agent reached the delivered picture only by importing it as source
media, because the tool that samples a finished file returns paths.

## The publish rehearsal — NEXT.md § 2

Both agent-doable items ran.

**The exposure scrub is applied.** Nine edits in HISTORY.md: the tailnet IPv4
(three places), the IPv6 suffix, the MagicDNS name and the short host name
(two places each), and two absolute `/home/<user>` paths. Every reachable
identifier is now **elided** (`100.x.y.z`, `<host>.<tailnet>.ts.net`,
`fd7a:115c:a1e0::…`) rather than replaced with a plausible substitute, because
HISTORY.md records what was measured and a believable fake address would make
it claim a run against a machine nobody dialed — CLAUDE.md § Conventions now
states that rule. Left deliberately, each with a reason:

- `~/lucid-*` and `~/TheVaultData` — `$HOME`-relative, naming no user and no
  host, and cited throughout CLAUDE.md's conventions.
- the bare hostname in PLAN.md and docs/plans/DAYDREAM.md, where it names a
  homelab box in a decision record rather than a reachable address, and
  rewriting it would drift four prose lines for no gain now that the MagicDNS
  form is gone.
- `192.168.1.50` in HISTORY.md — an illustrative RFC1918 address, matching the
  test that uses it, not this node's.
- the author line in `pyproject.toml`, which is published on purpose.
- `scratch/`, which holds absolute paths and NAS directory names and is
  gitignored — confirmed absent from a fresh clone.

**The fresh-checkout dry run passes, and found one defect.** Clone to a clean
directory, `uv sync`, `uv run lucid doctor` (everything required present),
`make_demo.py --build`, then every command in `docs/DEMO.md` steps 3–6. Every
number the walkthrough prints still holds on a checkout that is not this one:
47 words, 4 segments, `removed` 4.7 planned and 4.8 padded, `duration_after`
11.866, shots `0.00 + 9.66 blue` / `9.66 + 2.21 rust`, 286 frames through
melt over 3 sources, `verify` 0.971 with 34 of 34, `frames` agrees with delta
0. The suite runs there too: **1921 passed, nothing skipped, 10m27s** — all
five melt-rendering tests included, since this box had a Wayland session. The
defect the rehearsal found: `make_demo.py --build` echoed its steps as
`$ lucid.cli init …`, an argv slice rather than a command anybody can type.
Fixed.

## What this trial does not settle

- **One brief, one project, one model, one run.** The demo footage has no
  subject, which is why the agent chose picture structurally; a brief over real
  footage is a different question and the harness takes one (`--brief-file`,
  and run it on a *copy*).
- **Nobody has watched the output.** Every check here is a number or a tile the
  agent read. `cut.mp4` is at `~/lucid-work/agent-trial/`.
- **The user-level `CLAUDE.md` is in the agent's context**, as it is for the
  shipped panel — `claude` loads user memory whatever the cwd. It is a confound
  for anyone reproducing this off this box, and it is what the panel really
  runs with, so it was left alone and is recorded here instead.

## The second trial — the same instrument over real footage — 2026-09-03

The first trial's own § What this trial does not settle names its biggest
confound first: *"the demo footage has no subject, which is why the agent chose
picture structurally."* Choosing footage by what it shows is the thing this
repo has measured hardest — against 25 human picks the description index agreed
2 times and the clips' own filenames 3 (HISTORY.md § Choosing the b-roll) — and
the demo material cannot pose that question at all, because nothing in it
depicts anything. This run poses it.

Everything above still describes the instrument; only the material moved.
`--source` reads real footage instead of generating any, `--phrases` declares
the two lines this brief's own checks ask about, and `score()` is the same
function. HISTORY.md § The trial over real footage.

### What was run — real material

96 seconds of the Scream essay's own VO2.wav — the real recording, its
mistakes in it — plus four of the film's source clips, all **copies** under
`~/lucid-work/agent-trial-real/media`. The four are `scream1996-randy-rules`,
`scream1996-reveal-billy-stu`, `scream4-reveal-jill-and-charlie` and
`scream2022-reveal-richie-and-amber`: four different films' worth of subject,
so "which clip goes under this line" has a right answer and several wrong ones.

The brief asked for a 45-second cut with the fluffs gone, a named aside cut, a
named line kept, picture *under the lines it belongs to* rather than whatever
is nearest to hand, captions burned, and the render checked. The declared
phrases were `once in a theater` (must go) and `the front of this movie is
good` (must stay).

The whole run is kept at `~/lucid-work/agent-trial-real/runs/20260903-201638/`.

### The result — real footage

**9 of 9 checks pass.** 77 turns, 76 tool calls across 26 distinct tools, one
refusal, 10 images returned, 914s, $6.94 on Opus 5. The delivered cut is 45.23s
/ 1084 frames at 1920x816, `check_frames` delta 0, `verify` similarity 0.984
with 123 heard against 123 expected, captions confirmed in the pixels.

Three things this run establishes that the demo one could not:

**Picture was chosen by subject, and the agent built the means to do it.** It
drew all four clips with `footage_sheet`, wrote a `synopsis` for each from what
it saw, and then cued: Scream 4 footage under *"Scream 4 falls apart in the
second half"*, the 2022 reveal under *"the 2022 version… the ending kinda
sucks"*, Billy and Stu under the thesis turn. That is `synopsis`'s designed
route — a description does not choose the clip — walked unprompted by a client
that had never been told it existed. Where the material could not answer (there
is no Scream 6 footage) it **said so** and stayed in the nearest film rather
than reaching for something unrelated.

**It reviewed its own picture and found two real defects.** Reading `shot_sheet`
back, it caught a shot visually identical to its neighbour — *"reads as no cut
at all"* — and a shot that had landed on the wrong character, re-pinned both,
and moved a third off a dark motion-blurred frame. The sheets did the job they
were built for, from the side they were built for.

**It defeated the duration trap without being told about it.** The transcript
read clean; whisper had collapsed both fluffs into impossible word durations
(word 2 spanning 3.2s, word 17 spanning 8.3s). The agent noticed the durations,
re-transcribed at `large-v3`, found an 8-second hole with no words in it, and
then — having no tool that answers *what does the source actually say here* —
**rendered the uncut narration to a WAV and ran `verify` on it**, which
reported *"the first — the first — the first 12 minutes…"* followed by the
whole opening sentence a second time. It cut both with `cut_by_time`, the tool
for a fluff that has no words to address, and later caught a 0.02s surviving
sliver of one. This is the repo's own "trust word order, never durations"
lesson (HISTORY.md § 2) rediscovered from the tool surface alone.

It also declared where it exceeded the brief, unprompted: the fluffs and the
aside together leave ~79 seconds, so **the brief's two constraints could not
both hold**, and it cut the personal-history digression to reach 45, named
exactly what it dropped, and offered the `restore` back. That contradiction is
the brief's, not lucid's — recorded here because a run that silently resolves
one is a run whose result means less.

### The queue — three gaps, each with the evidence that found it

**All three are closed** — item 2 on 2026-09-04, items 1 and 3 on 2026-09-05.
Status, as ever, is the wiki's.

#### 1. No tool answers "what does the source audio actually say between t1 and t2" — CLOSED 2026-09-05

The whole 5.5-minute middle of this run — three `transcribe` calls, a model
escalation, an export and a `verify` — was the agent building itself an answer
to that question. `asr.transcribe_windowed` exists and is exactly the right
pass (a smaller model, overlapping windows, the one that reads across a seam),
but its only callers are `verify` and `verify --windowed` (`ops.py`), which
both take a **render**. So the route to hearing your own source material is to
seed a timeline, export it, and verify the export — which the agent found, and
which nobody should have to.

Fix: a tool that windows `asr.transcribe_windowed` over a registered clip's own
audio across a source-second span. The suspect-duration report already says
*where* to look; this says what is there. Note the honest counter-argument:
the workaround worked, and a tool that reads source audio is a second answer to
"what is in this clip" beside the transcript — so it must report, never attach.

**Built: `hear`.** `asr.transcribe_windowed` took `start`/`end` (windows laid
across the span, words stamped in the file's own clock, a span past the audio
refused rather than clamped) and `allow_silence`, since "nothing is said here"
is an answer to this question where `verify` treats it as a failure. `ops.hear`
runs it over a registered clip's own media and hands back `heard_words` beside
the attached transcript's words over the same span — reports, never attaches,
so no word index moves. CLI `lucid hear clip --from 12 --to 20`, MCP `hear`.
HISTORY.md § The trial's second queue.

#### 2. `finish_report` can never confirm a burn for an agent, because nothing outside the web UI writes the render log — CLOSED 2026-09-04

`finish_report` answers `captions.burned` off `renderlog.last`, and
`renderlog.append` is called in exactly one place: `webui.py`. Every render made
through the CLI or the MCP server leaves no log, so an agent's `finish_report`
reads `"unknown"` on a film whose captions are demonstrably burned in — which
is what happened here, and the agent settled it with `spot_frames` instead.
That is the right instinct (the repo's own rule is that only pixels settle a
burn) but the flag is dead weight for two of the three clients, and "unknown"
sitting beside a real answer teaches a caller to ignore the field.

Fix: either `ops.export`/`add_captions` write the same log the web UI's pipeline
writes, or `finish_report` stops claiming to answer for clients that cannot
produce evidence. The first is the smaller change and makes the field mean
something everywhere; the second is honest and cheaper. Decide before building.

**Decided and built: the first.** A media render logs itself from `ops.export`,
and a burn continues that run from `ops.add_captions` — two calls minutes apart
are one render, so the burn carries the export's stages onto a new line rather
than opening a second run that would hide it (`renderlog.amend`). The web UI
passes `log=False` to both, since its pipeline still appends the whole run
itself and a second record would leave `renderlog.last` reading a prefix of the
run. An export not yet burned still reads `"unknown"`, which is the honest
answer to a question with no result yet rather than a residue of this gap.

#### 3. `speech_overlap` is the only tool shaped like "does this footage have talking in it", and it requires a transcript — CLOSED 2026-09-05

The run's one refusal. The agent asked `speech_overlap` of a b-roll clip and
got *"no transcript for 'scream1996-randy-rules'"* — correct, and beside the
point it was reaching for. Before laying dialogue-carrying film footage under a
voiceover, "will these two talk over each other" is a real question, and
answering it currently costs a whole transcription of a clip nobody wants
captions from.

Fix: this is the smallest of the three and may be a docstring rather than a
tool — `speech_overlap`'s refusal could name what to run instead. Measure
whether an energy-only answer (`energy.believable`'s machinery, no ASR) is good
enough before adding a transcription step to a picture decision.

**Measured, then built as a fallback.** `energy.sound_runs` thresholds a clip's
envelope between its quiet and loud tenths with no word map; against a
CPU-whisper transcript of this run's own two Scream 1996 clips it finds every
speech run but reports sound, not speech — the numbers and the reading are in
HISTORY.md § The trial's second queue. So `speech_overlap` now falls back to it
when `clip_id` has no transcript, says `clip_evidence: "energy"` in the result,
and its refusal (`clip_evidence="transcript"`) names `transcribe` as the
word-level route. The VO side still needs its transcript.

### What this run still does not settle

- **Nobody has watched it.** Four frames were read back — captions in the
  pixels, the right film under the right line — and every other check is a
  number. `cut.mp4` is at `~/lucid-work/agent-trial-real/`.
- **One brief, one model, one run**, again. This one's material was chosen to
  make the subject question answerable; a brief over footage with no clean
  answer is a different question.
- **The 45-second constraint did the cutting.** The agent's largest editorial
  decision was forced by a contradiction in the brief, so this run says little
  about how it cuts when nothing is over-constrained.

## The third trial — a whole film — 2026-09-15

The first two briefs asked for a cut: fluff gone, b-roll, captions, a render
and a check. Neither asked for a score, an end card or a master, so the claim
"an agent makes a whole film" had never been measured (docs/plans/SHOWCASE.md
§ Step 4). This run measures it.

`--film` is the same instrument: the same client, the same confinement, the
same nine checks. It adds `make_demo.py`'s generated score to the demo
material, a brief asking for a finished film, and three checks, each read off
the delivered file rather than the manifest:

- **`music_placed`** correlates the render's audio against the score at the
  second the project's bed plan puts it, and at four wrong seconds.
  `scripts/trial_check.py`'s kit check does the same, and both use one
  implementation.
- **`loudness_on_target`** measures the file's integrated loudness against the
  brief's −16 LUFS, within `export --loudness`'s own 1 LU band.
- **`end_card_rendered`** needs a card recorded as the tail, a render long
  enough to reach the tail's midpoint (counted forward from the edit's end),
  and ink on screen there.

**The checks were calibrated before the agent ran.** `--film --control`, the
walkthrough in DEMO.md §§ 6–8 run by script, passed 12 of 12. Three renders of
one project each failed only the check for what they lacked:
- **The finished render:** passed all three. The score read −16.6 dB at its own
  second against −26.8 at the best wrong one.
- **Mastered with the score but no tail:** failed `end_card_rendered`, because
  the render ends at 12.10 s, before the tail's midpoint at 14.01 s.
- **No score, no master, no tail:** failed all three. The score check's margin
  was 1.0 dB, and loudness measured −20.6 LUFS.

The brief is `FILM_BRIEF` in `scripts/agent_trial.py`. The whole run is kept at
`~/proofcut-work/spikes/agent-trial-film/runs/20260915-140025/`, and the
control at `~/proofcut-work/spikes/agent-trial-film-control/`.

### The result — a whole film

**12 of 12 checks pass.** 43 turns, 42 tool calls across 31 distinct tools,
**no refusals**, 3 images returned, 192 s, $2.27 on Opus 5.

| check | verdict |
|---|---|
| the first nine | as the first trial: 0 of 6 retake words survive, 6 of 6 of the good take do, 387 frames against 387, similarity 1.0 with 34 of 34 heard, captions burned |
| `music_placed` | −16.1 dB at its own second, −26.0 at the best wrong one (margin 9.9), 18 LU under the voice |
| `loudness_on_target` | −16.1 LUFS integrated, true peak −1.0 dBTP |
| `end_card_rendered` | `card:endcard` for 3 s, YMAX 239 at 14.63 s |

Two frames were read back to check beyond the numbers:
- **6 s:** rust b-roll under the caption "a word in the transcript."
- **14.6 s:** the end card reading "proofcut" alone.

**What the agent did with the parts no earlier brief asked for:**
- **Music:** it placed the bed with `music --plan` first, then wrote it from
  word 0 at 18 LU under, with a 1.5 s fade-out.
- **End card:** it read `card_templates`, drew `endcard` with the mark
  "proofcut", checked the canvas, and set a 3 s tail.
- **Export:** it exported with `loudness: -16`, then burned captions into a
  second file. The burn copies the audio, so the master survived it.
- **Checks:** it ran `check_black`, `spot_frames`, a windowed `verify`,
  `finish_check`, `continuity_check` and `finish_report` beyond what the brief
  named, and `attenuate_noises --plan`, which found nothing to do.
- **Cut:** it re-heard the retake span with `hear` before cutting it
  (`through_pause`), and chose not to strip silences, so the timeline holds 2
  segments.

### The queue — one defect, found by the agent

**1. `export --loudness` lengthens the audio stream, and `spot_frames` stops
trusting the render.**
- **What the agent reported:** the file reads 0.075 s longer than the
  timeline, and `spot_frames` returned `mapping_trusted: false`.
- **Measured on the control project:** the plain render's audio runs 12.053 s
  against 12.042 s of video. The same edit mastered runs 12.100 s, 47 ms longer.
  The cause first recorded here, "the encoder pads", was wrong. It is a
  timestamp jump in `loudnorm`'s output, and it moved audio late rather than
  only lengthening the stream (HISTORY.md § The master's late audio).
- **Why `spot_frames` distrusts it:** it trusts a mapping only within half a
  frame, 20.8 ms at 24 fps.
- **What still passes:** `finish_check`'s 0.5 s duration tolerance and
  `check_frames`, which counts video frames.

So every mastered render is one that `spot_frames` will not map to words,
while every other check stays clean.

**Fixed the same day.** The first idea, trimming the audio back to its old
length, would not have worked: a trim measured 3.092 s against 3.100 s,
because no samples were extra. HISTORY.md § The master's late audio.

The agent also flagged "sound in a pause" at 3.5–4.5 s from the windowed
`verify`, and guessed correctly that it was the score in the breath before the
good take. Its reading was not a defect.

### What this run does not settle

- **Nobody has listened to it.** The level, the fade and whether 18 LU under is
  the right place for the score are numbers here. `cut.mp4` is at
  `~/proofcut-work/spikes/agent-trial-film/`.
- **The demo's material has no subject,** so the picture was hung by sentence,
  as in the first trial. A whole film over real footage, with a real score, is
  the next run.
- **One brief, one model, one run.** The brief named the level, the card's text,
  and where the music starts. A brief that leaves those to taste asks a
  different question.

## The fourth runs — the three briefs under deferred loading — 2026-09-16

docs/plans/MCP.md § Step 1 changed the client every run above used.
`--tools ""` had stripped Claude Code's tool search along with its built-ins,
so all of proofcut's definitions were loaded on every turn. The client is
now `--tools ToolSearch`, which reads no file and runs nothing, and the agent
fetches definitions as it needs them. The step's done-when was that each
brief passes what it passed before, with turn-1 context under 12K tokens.
Each brief was re-run once, unchanged. The film brief is the recorded run's
byte for byte. The real-footage brief differs only in naming the tool
proofcut rather than lucid (`brief-proofcut.txt` beside the old one). What
changed in the code is HISTORY.md § The MCP surface, rebuilt for deferred
loading.

| brief | run | checks | turns | tool calls | refusals | cost | wall | turn-1 context | distinct tools | searches |
|---|---|---|---|---|---|---|---|---|---|---|
| demo | `20260825-165027` | 9/9 | 31 | 30 | 1 | $1.31 | 184 s | 57,867 | 22 | — |
| demo | `20260916-143015` | 9/9 | 38 | 37 | 1 | $1.45 | 134 s | 8,113 | 22 | 4 |
| real footage | `20260903-201638` | 9/9 | 77 | 76 | 1 | $6.94 | 913 s | 61,055 | 26 | — |
| real footage | `20260916-143523` | 9/9 | 60 | 59 | 2 | $3.43 | 414 s | 8,419 | 23 | 4 |
| film | `20260915-140025` | 12/12 | 43 | 42 | 0 | $2.27 | 191 s | 63,329 | 31 | — |
| film | `20260916-143238` | 12/12 | 45 | 44 | 1 | $1.56 | 149 s | 8,220 | 27 | 3 |

Tool calls count `ToolSearch`; distinct tools do not. Each run is kept
beside the one before it, under its own brief's `runs/` directory. The
projects the older runs left are copied into each run's `proj-after`,
because a new run's `prepare` deletes `proj`.

**Every check that passed still passes, and turn-1 context is 13–14% of what
it was.** The searches came in batches:
- **The first search loaded 10 to 20 definitions.** It was the same start
  every time: `timeline_status`, `finish_report`, `list_media`,
  `import_media`, `transcribe`, `get_transcript`, `seed_timeline`.
- **Each later search fetched a phase's worth:** cues and sheets, then
  cards, then a check.
- **29 of the 101 definitions fetched were never called.**
  `resolve_phrase`, `cue_ls` and `film_check` were fetched and never called
  on all three runs.

The demo run's seven extra turns are mostly those searches, and it still read
fewer tokens in total (1.16M against 1.51M).

**The refusals are ordering slips, not regressions.** All three new runs
called `finish_report` before anything was seeded, and got the refusal that
says to seed first. The first demo run made the same slip with
`timeline_status`. The real run's second refusal was `get_transcript`,
asked before `transcribe` had run.

**Always-loading the top eight did not pay** (plan § Step 4). The demo brief
was run once more with `import_media`, `cue_add`, `export`, `check_frames`,
`verify`, `get_transcript`, `cut_by_transcript` and `seed_timeline` always
loaded (`20260916-144351`). Against the deferred run:
- **Checks and refusals:** 9 of 9 passed, with no refusals.
- **Searches:** 3 against 4. The first search still went out, for fifteen
  other tools.
- **Turns and cost:** 37 turns against 38, and $1.44 against $1.45.
- **Turn-1 context:** 16,695 tokens against 8,113, a cost on every turn of
  every session.

**The real-footage run chose the other side of its brief's contradiction.**
The third run found that the fluffs and the aside together left ~79 seconds
against an asked-for 45. It cut the personal-history digression to reach 45
and said so. This run kept every line the narrator meant, delivered 71 s
(1,701 frames, similarity 0.985, 204 heard against 206), and said instead
that 45 was not reachable without cutting "about 25 seconds of real
argument", naming the digression as the candidate. Both readings are
defensible, and the brief's `length` line is not scored. So the cost and
turn figures above compare two different edits: a longer cut has more
shots (12 against the third run's 8) and a longer verify.
It also shows the second trial's queue closing from the agent's side. Item 1
asked for a tool that says what the source audio holds between two times.
This run called `hear` five times for it, where the third run had rendered a
scratch WAV and verified that. Picture was again chosen by subject. With no
Scream 6 footage, it drew a title card for that line rather than borrow
another film's footage.

### What these runs do not settle

- **One run per brief, again**, and the demo's seven extra turns may be no
  more than the variation between two runs of the same client. Nothing here separates the
  client change from run-to-run noise except turn-1 context, which is
  deterministic.
- **Nobody has watched or listened to the three new renders.** They are at
  each spike's `cut.mp4`.
