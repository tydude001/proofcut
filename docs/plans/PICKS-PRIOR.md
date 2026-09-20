# Could a person's own picks pick the b-roll? — checked 2026-09-20, not built

PRIOR-ART.md § Seven repos a new stargazer had starred held one idea for a
design note: **B-Roll-Finder's learned trims.** Re-importing the user's edited
XML records their in and out points per clip (`clip_library.py:490-512`) — a
human's pick, kept, as signal for the next one. Read, not run. proofcut already
persists the same thing (the cue table is the picks; `src_start` is an
in-point), so the question is not whether to *store* them but whether they
*predict* — and that has an honest answer before any code.

## The floor was in-sample

FOOTAGE-EMBED.md's controls table lists "always the commonest clip / commonest
three" at **7 / 13** of 25, and calls it an oracle "learned off the answer key".
That is right, and it also means **13 is not what a prior learned from picks
would score.** Held out properly, each position predicted from the other 24:

| a prior learned from the other 24 picks | top-1 | shortlist of 3 |
|---|---|---|
| leave-one-out popularity | **7** | **7** |
| chance (9 clips) | 2.8 | 8.3 |

The shortlist collapses because of the counts, not the tie-break: the film's
picks fall 7, 3, 3, 3, 2, 2, 2, 2, 1, so holding out any clip other than the
commonest one leaves the top three filled without it. **7 under every one of 200
random tie-breaks** — a floor below chance, not the 13 the table implies. A
learned prior has to beat *that*, and 15 of 25 against it is a different claim
from 15 of 25 against 13.

The one sequential signal looked for is weak: a pick equals the one before it 5
times in 24, where independence at the same counts expects 2.7 (permutation
p ≈ 0.11). Not evidence of structure.

## Why this cannot be run to a verdict here

- **There is one film's worth of human picks.** Every 25-cue project under
  `~/proofcut-work` is that film (copies and derivations — HISTORY.md § The VO the
  project was holding); the one other with a cue table is an agent trial's 11
  cues over 4 assets, which are an agent's picks and not a person's. Nothing is
  held out from a different film.
- **The in-point half has two data points.** Two of the 38 cues are pinned
  (`src_start`); every other video cue runs off `plan_picture`'s cursor. A prior
  over *where in a clip* a person starts has nothing to learn from.
- **25 positions cannot separate methods a few picks apart.** 15 against a floor
  of 7 is clear; 15 against a synopsis catalogue's 15 is not, which is the
  comparison that would decide whether this beats what a person writes down.

## Decision

**Nothing built and no design to review.** The content routes are measured and
closed (`describe` 2, filenames 3, embeddings 10; HISTORY.md § Choosing the
b-roll, § Embedding the footage did not pick the b-roll), and the picks route has
no film to be judged on.

## What would reopen it

A second film with **at least 20 non-card video cues over at least 6 assets, cut
by a person** (an essay Tyler picks the b-roll for by hand, not one an agent
cued). Then, preregistered before the run and in this order:

1. The leave-one-out popularity floor on that film, with the first film as a
   second held-out set — the number every learned method must beat.
2. A prior learned from film 1's picks, applied to film 2 (cross-film: the only
   evaluation that is not leave-one-out on the same data).
3. Each read against the hand-written `synopsis` shortlist on the same film
   (15 of 25 on this one), because that is what a picks prior would replace.

**Bar:** shortlist of 3 at least **5 above the leave-one-out floor** on film 2,
and not below the synopsis shortlist. Otherwise recorded here and left.
