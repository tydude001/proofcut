# RENDER-CHECKS — frozen and silent spans in a render — 2026-09-20

Provenance: PRIOR-ART.md § The re-check before Show HN found that OpenChatCut's
`verify_export` and CutPilot's `visual-qa-engine.mjs` both scan a render for
frozen video and long silences, and that proofcut does neither. This file is the
measurement that came before any design, and the design it supports. **No code
has been written.** Status of the work lives in the wiki's Open items table
(`proofcut-render-still-check`), never here.

## The gap

proofcut checks a render for black (`check_black`), for an exact frame count
(`check_frames`), for its words (`verify`) and for loudness and true peak
(`finish.py`, which `hold_check` and `finish_check` share). A grep of `src/` for `freezedetect` and `silencedetect` finds
nothing. The failure that matters is one CLAUDE.md already names: a hold or
`vo_extend` splice with no cue of its own gets the picture that was playing
frozen across it, "with `shots_error`/`verify`/`check_frames` all staying
clean" (PLAN.md's `covered_by` warning). Nothing in the render-side checks
would see it.

## What was measured

Spike: `~/proofcut-work/spikes/freeze-silence-probe/` (`run.sh`, `join.py`, the
three `*.detect.log`). ffmpeg's `freezedetect=n=-60dB:d=0.5` and
`silencedetect=n=-50dB:d=0.75`, read back through the film's own shot plan
(`proofcut shots`).

| Case | Result |
|---|---|
| **Control** — 6 s `testsrc2`, freeze built at 1–4 s, silence at 2–4 s | Found exactly: freeze 1.0→4.0, silence 2.02→4.02. The detectors work at these settings |
| **The shipped film**, `essay-cards-fixed.mp4`, 336.34 s | 14 freeze spans (7 once butt-joined cards merge), **no silence**. The plan has 13 card shots, 90.6 s of them; **13 of the 14 spans sit inside a card shot** (±0.15 s) and every card shot is at least 80% covered — none missed |
| **The 14th span**, 4.63–5.13 s, 0.50 s, in `cold-open` | Not the edit's doing. The *source* freezes at the same second for the same 0.5005 s (and at 2.75 and 5.25 at d=0.3), and the frames are the title "SCREAM" held between glitch pulses — deliberate footage |
| **The same film with its 6 s end card** | Adds one span and one silence, both the tail: 336.2→342.4, over silence by design |
| **The approved launch clip**, `clip-v6.mp4`, 47.0 s | 8 freeze spans (7 merged), 0.53–1.98 s, about 8.8 s in all; no silence. Not joined to its project, so **which are cards and which are an idle screen is unknown**. None is a known defect: Tyler picked this cut |
| **Cost** on the 336 s film | 3.6 s wall, 40 s CPU for both; silence alone 0.23 s |

**What that says.** The detectors are sound and cheap, and a single-signal
report is noisy in two directions: 13 of 14 spans are cards, one is footage
that is still on purpose, and the launch clip's eight were all put there by
someone who approved it. A list of frozen spans is the wrong output. The
finding is a frozen span **the render has and the edit and the source do not
explain**.

## What was not measured

- **A true positive shaped like the real defect.** The film has no holds and no
  `vo_extend`, so the only positive is the synthetic control. The detectors
  find a freeze; whether the join to the shot plan flags a cue-less hold is
  untried, and it is the whole claim.
- **The noise floor.** `n=-60dB, d=0.5` was run on the whole film only. Other
  floors were run on the first 8 s alone, where the title flickers at -45dB.
- **Silence over a bed or room tone.** The film has neither, so `silencedetect`
  had nothing to be wrong about.
- **Two films.** The second is unjoined.

## Design, for review

A new read-only op **beside `check_black`**, ffmpeg only (no melt, no display).
Name open; `check_still` is provisional.

1. Run `freezedetect` over the render. Each span is returned with an
   `explained_by`, tried in this order: **`card`** (an image or `card:` shot
   covers it, ±0.15 s), **`tail`**, **`source`** (`freezedetect` over the same
   source seconds of the shot's asset — run **only for the spans left over**,
   which was 1 of 14 here), else `null`.
2. The headline is the count of `null`s. Explained spans are still listed and
   labelled, on `check_black`'s `KNOWN_TAIL_FRAME` precedent, so nothing is
   hidden. **Report, never a gate**: `apply`-style precedent from
   `reframe_detect` and `unspoken_detect`.
3. **Two clocks.** Shots are Edit-relative and a render is Edit time +
   `head_seconds`; a retime warps them again. The join has to go through
   `_head_seconds` and `_Clock`, not read seconds off `shots`. The spike had
   neither, so it never met either trap.
4. **Opt-in wherever it composes in.** It decodes the render, so it follows
   `finish_report`'s `framing=True` rule: `None` when unasked, never a clean
   zero. 3.6 s is under `framing`'s 5.7 s, and "cheap" is not evidence that a
   `project-changed` handler may pay it.
5. Surface work is the usual: `@_tool()`, a CLI twin, a row in `_ANNOTATIONS`
   and `_PARAM_DOCS`, `EXPECTED_TOOLS` widened and named in the report, and a
   line in `INSTRUCTIONS` inside the 2 KB cap.

## Decisions

1. **Build it?** Yes, but the first step is the positive control, not the op:
   a project with a cue-less `vo_extend` hold, run through the naive detector
   and then the join. If the join cannot flag it, the design is wrong and the
   op should not exist. About an hour.
2. **One op or a `check_black` flag?** A new op. `check_black`'s reply is
   specific to black and to the kdenlive tail frame, and a `freeze=` flag would
   make its name lie.
3. **Silence: in or out?** Out for now. It measured zero on two films except
   the tail, `verify` already hears a render that lost its words, and no known
   failure needs it. Add it in the same op when a case does, since it costs
   0.23 s.
4. **Does `finish_check` call it?** Unchecked. Read `finish_check` before the
   build; it may already scan a render and be the right home.

## The positive control, run — 2026-09-20

Decision 1's own test, run the same day: a project with a cue-less `vo_extend`
hold, rendered through melt, then `freezedetect=n=-60dB:d=0.5` over the file.
Spike: `~/proofcut-work/spikes/freeze-hold-control/` (the demo project, a 3 s
hold after word 10 landing at timeline 3.5–6.5 s, inside the first shot, which
has no cue of its own there — `covered_by` named it, as built).

| First shot | Render | What `freezedetect` saw at the hold |
|---|---|---|
| The demo's own b-roll (flat colour, a counter that ticks once a second) | 0–11.83 s | frozen 0→11.83 in three spans, the hold **indistinguishable from the shot around it** — the fixture is near-static, and so is the *source* (same three spans). Not a usable control |
| Moving footage (`testsrc2`), then `mandelbrot` for shot two | 19.88 s | **Nothing.** The clip plays straight on under the hold; no freeze exists to find |
| A card (`card:hero`, the bumper template) | 19.88 s | One span, 0→11.83, the whole card shot, hold inside it. **The join's first rule, `card`, explains it** |

**So the join cannot flag the defect, and by Decision 1's own rule the op as
designed should not exist.** The defect this whole item was opened for is not
a pixel fact:

- Under moving footage the hold does not freeze anything. PLAN.md's "stale
  picture over the manufactured silence" is the *editorial* problem — the
  picture does not change to suit a line that plays — and it is true of the
  shot plan, not of any frame.
- Under a card or still it does freeze, and the design labels that `card`,
  explained, which is the false negative: the hold is the reason the card is
  still up, and the join has no way to say so.

What it would take to flag it is a fourth `explained_by` that *subtracts* the
hold — a frozen span overlapping a hold's own timeline span is not explained
by the card under it. But that is a fact of the edit and the cue table
(`covered_by`'s own computation, over the saved edit), and needs no decode at
all: `vo_extend` reports it once, at write time, and nothing can be asked for
it later. If the concern is holds, the gap is **that read**, not a
render scan.

What survives of the op is the case the 2026-09-20 measurement first named:
footage that is static and that neither the edit nor the source explains (an
idle screen recording). Nothing measured here says that happens on a film
proofcut made — 13 of 14 spans were cards and the 14th was deliberate — so the
op is now a check with **no known positive**, which is the state RENDER-CHECKS
was meant to get out of before building.

**Also learned, for the next fixture:** the demo project's b-roll reads as
frozen at -60dB because its only motion is a counter. A freeze or motion
measurement needs footage that moves everywhere (`testsrc2`).
