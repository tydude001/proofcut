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
