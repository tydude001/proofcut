# proofcut — the launch clip, re-cut until it matches A

Provenance: NATIVE.md § B7 had an agent re-cut the launch clip inside proofcut
(HISTORY.md § B7: an agent re-cut the launch clip). Tyler compared it with
the approved `clip-v6.mp4` twice on 2026-09-18: once whole, then moment by
moment over nine moments. He picked the approved clip every time and said
"in general I want A to be the goal". This plan is what proofcut needs so
that the same brief, cut by an agent, comes out as A. Sources:
- the agent's own report of its gaps
  (`~/proofcut-work/spikes/native-b7/trial/runs/20260918-143553/report.md`);
- the moment-by-moment cut and measurements
  (`~/proofcut-work/spikes/native-b7/beats/`);
- three read-only research passes, on sound, on addressing and structure,
  and on the look, made the same day. Their citations are carried into the
  steps below.

Status lives **only** in the wiki's Open items table (`proofcut-native-b`).
This file never carries a status header. When a step ships, HISTORY.md gets a
named section and the step here gains a "Shipped — see HISTORY.md § …" pointer.

## The test

**A is the spec, measured, and each step is judged on the moments it
touches.** Every step below names which of the nine moments it moves. It is
done when a fresh agent run, with the brief unchanged
(`~/proofcut-work/spikes/native-b7/brief.txt`), re-cut into the same moments
by `beats/cut.py`, gets "A better" on none of them from Tyler. Numbers alone
do not close a step. They are what the fix aims at, and his tap is the
verdict (§ Review by served page).

The brief stays unchanged so that runs stay comparable (TRIAL.md's rule). The
material stays unchanged too: v6's own run, staged by `native-b7/prep.py`.

## What A is

A is `~/proofcut-work/spikes/launch-v4/clip.py`: 47.02 s, 1080p60.

| Moment | A's length | What A does there |
|---|---|---|
| Typing the brief | 4.4 s | Sentence one at reading speed in 3.2 s, the rest in ~1.2 s; every keystroke clicks |
| Send, and the wait | 1.5 s | The agent's 27 s wait squeezed to about a second; the camera eases out to the whole window |
| The false start | 8.5 s | Your "Um, no, let me take that again" only; music dips **10 dB** under it (0.3 s smoothstep ramps); the music's drop lands on the transcript |
| Sheets | 4.0 s | Music and a click, no voice |
| Timeline, checks | 4.05 s | Music, no voice; the checks fly past in about a second |
| The film | 11.17 s | The agent's render in the preview rect, frame-locked, its audio levelled to **−18 dBFS RMS** (`speech_level`, clip.py:471), and the music dipped under it |
| Report | 3.98 s | "35 of 35 words heard." over the report |
| Terminal | 5.6 s | Crossfades to the Claude Code recording over 0.5 s, plays 2½ minutes of calls in about 2 s, and the camera eases onto the checks |
| End card | 3.82 s | Full-frame ink; "proofcut" in Outfit Bold 190px, centred at 0.27H; the Zilla Slab 58px amber tagline at 0.55H, lifting 14px with a +0.2 s stagger; the URL, then the two install lines |

**The lower third, everywhere.** The headline is Outfit Bold 76px, off-white
`#faf5ec`, at x 96, and rises 24px over 0.45 s with a cubic ease. The footnote
is Zilla Slab SemiBold 44px, amber `#e8a13c`, and rises 16px starting 0.25 s
after the headline. The scrim is ink `#1a1714` with alpha on a `u^1.6` curve,
from 0.52H to 0.74H.

## What B got wrong, and why

Each finding is sorted into one of four kinds: a defect, a missing feature, a
template that is wrong, or a choice the agent made where proofcut already had
the means.

| Moment | B | Cause | Kind |
|---|---|---|---|
| Typing, send | 11.6 s + 4.4 s | Retime stretches it chose; `retime.Warp` is clip.py's PCHIP, so A's `TIME` table transcribes directly | agent's choice |
| False start | music never dips | `_duck_frames` (ops.py:12612) gates off the Edit's own audio only, and a screen recording has none | missing feature |
| Sheets, timeline | your take runs on under them | A sound cannot be trimmed: `_sound_hits` plays the whole file (ops.py:14897); `sounds.MAX_SECONDS` 30 | missing feature |
| All | music on a false transcript | `music` and `cue_add` resolve words only (ops.py:12025, :2483); `attach_transcript` accepts a transcript for a clip with no audio stream (ops.py:325) | missing feature + defect |
| Film | −23.8 LUFS, played unchanged | The inset plays its file at 0 dB (`Inset.gain_db`, mlt.py:372) and nothing levels it; A levelled it to −18 dBFS | missing feature |
| Terminal | three stills dissolved | No second recording can follow the first; there is no dissolve primitive in `mlt.py` | missing feature |
| End card | small type at the bottom left | `tail` refuses a project with no picture cue lane (ops.py:15238); the agent's bumper never rendered | missing feature |
| Every headline | serif headline, sans footnote | `lowerthird` defaults `title_font` to Noto Serif and `body_font` to Lato (graphics.py:682); A is Outfit over Zilla Slab | template |
| — | export crashed on ffmpeg `[0:a]` | `_vo_loudness` (ops.py:12540) builds `[0:a]` on a clip with no audio stream | defect |
| — | `finish_report` calls used clips "unused" | `_referenced_clip_ids` (ops.py:3699) never reads insets or sounds | defect |

**Correcting § B7's record:** the faint film is not a gain-stage bug. The
inset measured −23.8 LUFS against its own file's −23.7, so the writer is
exact. What is missing is levelling, together with a bed that never dipped.
The moment-by-moment page's "14 dB quieter" compared the loudness-matched
copy, in which B's loud bed had pulled everything down 7.8 dB.

## The build order

Each step can be used on its own, and each is judged on the moments it names.

**1. The three defects and one guard (S).** These moments are unaffected, but
the next run must not trip on any of them.
- **The export crash.** `_vo_loudness` refuses by name: "this timeline's
  video has no audio track". The test uses a video-only Edit clip, since today
  only an empty Edit is guarded.
- **`finish_report`.** `_referenced_clip_ids` reads `INSETS_KEY` and
  `SOUNDS_KEY` (`assets`).
- **`attach_transcript`** refuses a clip whose probe says `has_audio: false`.
  That refusal is what stops the next agent's false-transcript workaround.
- Shipped — see HISTORY.md § The recut's three defects and one guard.

**2. The bed and the cues take an event (M–L). Moves: false start, and every
moment's music.** `_overlay_instant` (ops.py:13706) already resolves a word
*or* an event, and overlays, retime and insets share it.
- `MUSIC_KEY` gains `event`/`until_event`, with `event` on each passage, and
  `_music_plan` resolves through `_overlay_instant`. The obvious build is a
  second resolver, and that is exactly what this step does not do.
- The same goes for `cue_add` and `build_shots`, since `build_shots` is the
  seam that has to stay single.
- The bed's `src_in` is what puts the drop on `words`. A1 already has it; it
  just has never had an event to line up against.
- Shipped — see HISTORY.md § The bed and the cues take an event.

**3. A tail with no cue lane (M). Moves: end card.** When a head or tail is
set and there are zero cues, `_build_mlt` puts in one identity shot covering
the Edit. That makes the picture lane exist, and `mlt.document`'s
equal-lengths rule keeps holding. It is needed even on a spoken film with no
cues. Separately, the agent's bumper swapped its `ink` and `paper` slots,
which the card was right to accept. That was a choice, so nothing here fixes
it.

Shipped differently — see HISTORY.md § A tail with no cue lane. The identity
shot drew over the insets; the card goes on the Edit's own track instead.

**4. The duck hears every lane (M–L). Moves: false start, film, report.**
`_duck_frames` takes the loudest level per block across Edit segments, sound
hits (`_sound_hits`), audible insets and hold spans. That makes it the same
gate over more sources, which is the shape of clip.py's `spans` (clip.py:527).
It is still computed in `_build_mlt` only (§ The duck). It is a real rewrite,
because the lanes live in three coordinate spaces, all of which `head_frames`
already resolves to frames.

**5. A sound can be trimmed (S–M). Moves: false start, sheets, timeline.** A
sound record gains `src_in`/`src_out`, and the decoded PCM is sliced before
`padded_copy`. `MAX_SECONDS` then caps the slice rather than the file. A span
addressed by the sound's own transcript words is a later option, and this step
does not build it.

**6. An inset can be levelled (S). Moves: film.** `inset_add` gains
`level="speech"`, which measures the asset once at add time and records the
gain that brings it to a target. The ramp that keeps the inset's
edges from clicking stays. A is the precedent for the target, −18 dBFS RMS.
It is measured and recorded as `gain_db`, never recomputed per build, on
`export --loudness`'s one-gain rule.

**7. The lower third looks like A (S). Moves: every headline.**
- The `lowerthird` template gains a `footnote_rise` and `footnote_delay`
  (16px, 0.25 s), so one card staggers its lines the way A's does. Today a
  stagger takes two overlay cards, which the agent did on one moment and not
  the others.
- **The launch clip's fonts come from the channel pack.**
  `goodsometimes/branding/proofcut-pack.json` has A's palette but its fonts in
  the wrong slots: `title_font` is Zilla Slab and `body_font` is Outfit, where
  A uses the opposite. That pack lives in goodsometimes, and the fix is one
  edit there. The template's defaults stay neutral.
- The scrim's `u^1.6` falloff is left alone unless a moment's tap asks for
  it, since at 0.9 density the difference is within what nobody has
  complained about.

**8. A second recording follows the first, with a dissolve (M–L). Moves:
terminal.** `Edit.insert` already splices a second `clip_id` at a segment's
end (timeline.py:524), and events, retime, reframe and sounds are all already
keyed per clip. What is new is two things:
- a picture splice, so `build_shots` reads through the new segment;
- **a dissolve**, where the two clips overlap on two tracks under a real
  transition. There is no `luma` or dissolve in `mlt.py` today.

The dissolve is the riskiest piece in the plan, because it is the first time
two sequential entries overlap, and `mlt.declared_frames` has to stay exact
across the overlap. Spike it on the flatpak and the portable melt before
building it, which is the rule every writer step has followed (§ Insets,
built).

**Where the material and the instruments are**, for whoever builds this:
- `~/proofcut-work/spikes/native-b7/`: `prep.py` (stages the material),
  `brief.txt`, `media/`, `events/`, `sfx/`, and `trial/`, which holds the B7
  agent's project, its run log and `clip.mp4`;
- `beats/cut.py`, which cuts A and a render into the nine moments with their
  lengths and LUFS;
- `review/`, the served A/B round.

The moment-by-moment page is a claude.ai artifact whose `beats` collection
holds Tyler's nine taps. Its source is `.b7-beats/` in the checkout, which is
excluded in `.git/info/exclude` and never committed. To judge a new run,
re-point `cut.py`'s `B` at the new render, re-cut, and republish that page
with the new clips.

**9. Run B7 again.** Use the same brief, material and harness, launched with
`QT_QPA_PLATFORM=offscreen` this time (§ B7). Cut it into the nine moments,
serve the page, and let Tyler tap. Any moment still "A better" goes back to
the step that owns it.

## Decisions for Tyler

**Taken 2026-09-18.** Tyler approved the plan as written ("I like the
plan"), so all four calls stand as recommended below.

1. **Order.** *Recommended: as written.* The defects go first because they
   would trip the next run. Then the music and the end card, which were the
   biggest reasons in both of his reviews. The dissolve goes last because it
   is the riskiest and touches one moment.
2. **The terminal beat.** *Recommended: build the dissolve (step 8).* A hard
   cut to the terminal is cheaper, but A dissolves and the goal is A.
3. **The pack's fonts.** *Recommended: fix `proofcut-pack.json` in
   goodsometimes.* The alternative is teaching an agent to override both
   fonts on every card, and a brief that has to say that is measuring the
   brief.
4. **The brief stays as it was** in the rerun. *Recommended: yes.* Pacing was
   the agent's choice, and a brief that dictates A's timings would measure
   whether an agent can follow a table.

## What this plan does not do

- **It does not make proofcut draw clip.py's frames.** It gives proofcut the
  same means, so that an agent can make the same choices. A run can still
  pace things differently, and the moment taps are what judge that.
- **It does not touch the essays** (NATIVE.md Part A).
- **It does not change the brief or the material.**
