# Would embedding the footage pick the b-roll? — preregistered 2026-09-20

The question PRIOR-ART.md § Seven repos a new stargazer had starred left open:
sentrysearch embeds footage *as video* and matches a text query against it, which
skips the lexical step HISTORY.md § Choosing the b-roll measured at 2 of 25. Does
that get anywhere `synopsis` does without a person writing the synopsis?

This file is written **before the run**, and the commit that adds it is the
preregistration. Nothing below is edited afterward except by appending a
`## Result` section; a variant tried after seeing the numbers is labelled
exploratory and never decides.

## The data

The 25 human choices are the non-card cues of
`~/proofcut-work/projects/final-cut/proj`, the shipped film's cue table (not
`brief-check`'s — HISTORY.md § The VO the project was holding). Each position
is `broll_brief`'s: the narration over the shot, and the footage asset a person
put there. **Nine candidate clips, 25 positions**, 14 s to 730 s each, about
1,300 s of footage in all.

Nine candidates is what makes the old table hard to read, so its controls are
stated here, not left to the reader:

| picking by | top-1 | shortlist of 3 |
|---|---|---|
| chance | 2.8 | 8.3 |
| the clips' filenames (HISTORY.md) | 3 | — |
| the `describe` index (HISTORY.md) | 2 | 8 |
| **always the commonest clip / commonest three** (`cold-open` picked 7 times, then four clips tied at 3, so the three commonest sum to 7 + 3 + 3) | **7** | **13** |
| a `synopsis` catalogue, hand-written (HISTORY.md) | 13–14 | 15 |

The commonest-three row **did not exist in the earlier table** and is an oracle:
it is learned off the answer key, so no method could use it. It is still the
honest floor for "shortlist of 3 out of 9", and the 15 the hand-written
catalogue scored is two above it.

## The method, fixed

- **Model:** `Qwen/Qwen3-VL-Embedding-2B`, bf16, local, one GPU job. The 8B needs
  about 18 GB by sentrysearch's README, and this card has 12 GB. **No cloud
  embedding**: the footage is other people's films and does not leave the box.
- **Footage:** each clip chunked at 30 s with 5 s overlap, sampled at 1 fps up to
  32 frames, downscaled — sentrysearch's local settings, restated here so the
  test is of the idea and not of that repo. Frames are extracted with ffmpeg and
  handed to the processor as a frame list; nothing is installed into another
  repo's venv.
- **Query:** the position's narration, verbatim, under the model's own retrieval
  instruction. **One primary configuration, not tuned on the picks.**
- **Score:** a clip's score is its **best chunk's** cosine with the query. Rank
  the nine.
- **Two declared variants, reported beside the primary and never in place of it:**
  (a) a clip's score is the mean of its best three chunks, because a 730 s clip
  has 24 chunks and a 14 s one has 1, so *max* favours length; (b) the query cut
  to its last 15 words, since the sentence a picture hangs on is usually the
  narration's end.

## The bar

**Pass** if the primary's **shortlist-of-3 is 15 of 25 or more.** That equals
what a person-written catalogue scored and clears the oracle prior (13). Reaching
15 by chance out of 25 at a one-in-three rate is about a 0.6% event (14 would
be 1.6%).

**Fail** if it is 13 or fewer — no better than guessing the commonest three —
and the primary's top-1 is also at or under 7. Between the two (14, or a top-1
above 7 with a shortlist under 15) is **inconclusive** and is reported as that.

On a **pass** the next step is a second test on a different film, never a
feature: one film and 25 picks is too thin to build on. On a **fail** the result
is recorded here and in HISTORY.md, nothing is built, and `describe` is left as
it is.

## What this cannot show

- **HISTORY.md argues the connection between a sentence and its picture is
  conceptual, not visual** (the Scream VI reveal earned by a sentence with no
  word of it in the footage). This test may simply confirm that.
- **It measures the *which clip* question only.** `describe`'s remaining job is
  *which second inside a clip*, which this does not touch, and which is tested
  only if this shows signal.
- One film, nine clips, one person's picks.
- A pass says the embedding *narrows*. HISTORY.md's own reading is that
  narrowing is what a better corpus does and that picking is a different step.

## The record

Only the numbers are committed. No frame, chunk or clip of the films leaves
`~/proofcut-work/spikes/footage-embed/`.

## Result — 2026-09-20

**Fail, on the bar as written.** `Qwen3-VL-Embedding-2B` (bf16, this box's RTX
5070, 37 s for 54 chunks and 50 queries, 4.4 GB VRAM), embedding settings as
above, scored by `score.py` and nothing else:

| configuration | top-1 | shortlist of 3 | mean rank of the human pick |
|---|---|---|---|
| **primary** — best chunk, full narration | **1** / 25 | **10** / 25 | 4.76 |
| (a) mean of the best three chunks | 4 | 10 | 4.64 |
| (b) last 15 words of the narration | 5 | 11 | 4.32 |

Against the controls: chance is 2.8 and 8.3, and a mean rank of 5.0; the
commonest clip is 7 and the commonest three 13; the hand-written catalogue's
shortlist was 15. **Ten of 25 at a one-in-three rate happens by chance three
times in ten**, so no configuration is distinguishable from guessing, and every
one is under the oracle prior. The primary's top-1 of 1 is *below* chance.

**The pipeline is not what failed.** Chunks from one clip sit at 0.73 cosine
from each other and 0.54 from other clips', so the video vectors do carry which
clip they came from. What they do not carry is the connection to a sentence:
the primary put five different clips first at four or five positions each, and
`cold-open`, which a person picked seven times, first only four times.
**Length was not the cause** — variant (a) exists for it and moved the top-1
from 1 to 4 and the shortlist not at all.

That is HISTORY.md § Choosing the b-roll's claim, now measured from the other
side: **the link between an essay's sentence and its picture is not in the
pixels, so a better embedding of the pixels does not find it.** Text scored
against text landed in single digits, and pixels scored against text does the
same.

**Not tested, and not implied:** the *which second inside a clip* question, the
8B model, and any film but this one. Per the bar's own terms nothing is built,
`describe` stays as it is, and the second-inside-a-clip arm is not run because
this showed no signal to follow. The vectors and the scripts are in
`~/proofcut-work/archive/spikes/footage-embed/` (run in `spikes/`, archived the
same day; the extracted frames were deleted, and `embed.py` regenerates them).
