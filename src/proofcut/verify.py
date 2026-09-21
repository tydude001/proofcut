"""Comparing what the timeline should say against what the render says.

Pure sequence work: two lists of word tokens in, a diff out. No I/O, no ASR —
`ops.verify` supplies both sides and this decides what changed.

The comparison is over *word order*, not timings. That is the point. Whisper
collapses an immediate retake into one utterance and hides the second take
inside the duration of the following word (HISTORY.md § 2), so timings cannot
prove the retake was removed — but the render's own transcript will contain the
phrase twice, and the timeline's expected sequence contains it once. Order is
the signal that survives.

Similarity is triage, not a verdict: ~0.97 is a clean render, because whisper
spells its own output differently on a second pass ("whodunit" / "who done it",
"4" / "four"). The diff is the artifact a human or an agent reads.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from typing import Any

from proofcut.transcript import _normalise

#: Shorter runs are noise. A single extra word is usually a filler whisper
#: caught on one pass and not the other; two in a row that also appear in the
#: expected sequence is a phrase that played twice.
MIN_RUN = 2

#: How closely an extra run has to resemble something the timeline expects
#: before it is called a repeat rather than left in the diff.
#:
#: It cannot be an exact match, and that is measured, not assumed. Two takes of
#: a line differ — that is *how you tell them apart*. On the Scream v1 export
#: the render says "falls apart a bit **at** the second half" and then "**in**
#: the second half", and "I don't think **that it's** a coincidence" then "I
#: don't think **that's** a coincidence"; the notes call the wrong preposition
#: the tell. Requiring the run verbatim reported one of those three retakes and
#: left the other two for whoever read all 900 lines of the diff.
#:
#: 0.5, because the second take is transcribed *worse* than the first — it is
#: the one whisper was already inclined to swallow. On the Scream v1 export the
#: repeat of "Scream 4 falls apart a bit" came back as "screen 4", and that one
#: substitution took the run to 0.556; at 0.6 the retake went unreported. An
#: unrelated insert does not come close, because it must also share a
#: contiguous run of `MIN_RUN` before it is scored at all.
SIMILAR = 0.5


class VerifyError(Exception):
    """Raised when there is nothing to verify against."""


def tokens(texts: Iterable[str]) -> list[str]:
    """Flatten text into comparable word tokens.

    Uses the transcript module's own normalisation so that "the same word"
    means the same thing here as it does in `Transcript.find` — a phrase an
    agent searched for and a phrase this diff reports must not disagree about
    punctuation.
    """
    out: list[str] = []
    for text in texts:
        out.extend(_normalise(text).split())
    return out


def _closest_run(needle: list[str], haystack: list[str]) -> tuple[int, float]:
    """Where `haystack` most resembles `needle`, and how much: (index, ratio).

    Anchored on the longest shared run and then scored over a window of the
    same length, so that a second take is recognised as the same line as the
    first even though the two are not word-for-word — which they never are.

    Returns (-1, 0.0) when nothing of `MIN_RUN` length is shared at all.
    """
    if len(needle) < MIN_RUN or len(haystack) < MIN_RUN:
        return -1, 0.0

    anchor = difflib.SequenceMatcher(a=needle, b=haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack)
    )
    if anchor.size < MIN_RUN:
        return -1, 0.0

    # Line the window up so the shared run sits where it sits in `needle`.
    start = max(0, min(len(haystack) - len(needle), anchor.b - anchor.a))
    window = haystack[start : start + len(needle)]
    return start, difflib.SequenceMatcher(a=needle, b=window, autojunk=False).ratio()


#: Shorter than this and ordinary English repeats it constantly ("the the",
#: two "and"s either side of a pause). `compare`'s MIN_RUN=2 is tuned for
#: retakes that show up as a diff against a *different* sequence; this one
#: hunts a transcript for lines it says twice, so it wants more evidence
#: before calling something a restart.
ADJACENT_MIN_RUN = 4

#: A retake follows within a breath, not a scene later. Capping how far ahead
#: a match may be found is what keeps this a check for adjacent repeats
#: rather than a second `find` across the whole transcript.
ADJACENT_LOOKAHEAD = 40


def _ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def find_adjacent_repeats(words: list[str]) -> list[dict[str, Any]]:
    """Find phrases the transcript says twice, back to back.

    `compare` catches a retake by diffing the transcript against the
    timeline's *expected* sequence — but a retake the timeline keeps (both
    takes survive into the edit, as on the Scream v1 export) never disagrees
    with anything, so compare() is correct to report nothing. This is the
    other half: the transcript checked against **itself**, at attach time,
    before any edit exists to compare it to. PLAN.md § the third retake was
    never a `verify` miss.

    Reuses `_closest_run`'s near-duplicate scoring — same anchor-and-score
    approach that lets `compare` recognise a second take despite whisper
    transcribing it differently from the first — pointed at a window just
    ahead of each candidate run instead of at `compare`'s `expected`.

    Every position is a candidate window, so unlike `compare` (which only
    scores runs the diff already flagged as anomalous) this can raise more
    than one overlapping candidate around the same real repeat — "commentary
    i don't think" and "i don't think that" both score against the same
    restart. Keeping the highest-scoring, non-overlapping candidates is what
    turns that into one report instead of several for the same event.

    Not a verdict: a deliberate callback line reads the same as a swallowed
    retake to this test. Which is which is for whoever reads the result to
    decide, not this function.
    """
    n = len(words)
    candidates: list[tuple[float, int, int, int, int]] = []
    for i in range(n - ADJACENT_MIN_RUN + 1):
        run = words[i : i + ADJACENT_MIN_RUN]
        lookahead_start = i + ADJACENT_MIN_RUN
        lookahead_end = min(n, lookahead_start + ADJACENT_LOOKAHEAD)
        haystack = words[lookahead_start:lookahead_end]

        at, ratio = _closest_run(run, haystack)
        if at >= 0 and ratio >= SIMILAR:
            second_at = lookahead_start + at
            candidates.append((ratio, i, i + ADJACENT_MIN_RUN, second_at, second_at + ADJACENT_MIN_RUN))

    # Strongest match first; a candidate that overlaps one already kept is the
    # same restart seen from a neighbouring offset, not a second event.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    kept: list[tuple[float, int, int, int, int]] = []
    covered: list[tuple[int, int]] = []
    for candidate in candidates:
        _, f0, f1, s0, s1 = candidate
        if any(_ranges_overlap((f0, f1), c) or _ranges_overlap((s0, s1), c) for c in covered):
            continue
        kept.append(candidate)
        covered.append((f0, f1))
        covered.append((s0, s1))

    return [
        {
            "first_word": f0,
            "first_text": " ".join(words[f0:f1]),
            "second_word": s0,
            "second_text": " ".join(words[s0:s1]),
            "similarity": round(ratio, 3),
        }
        for ratio, f0, f1, s0, s1 in sorted(kept, key=lambda c: c[1])
    ]


#: How closely each side of a heard repeat must match the expected words for
#: the repeat to count as the edit's own. Stricter than `SIMILAR`: at 0.5 two
#: shared words of four ("of the") would excuse a real retake.
EXPECTED_REPEAT_SIMILAR = 0.75


def _says_twice(first: list[str], second: list[str], expected: list[str]) -> bool:
    """Whether `expected` holds `first` and, after it, `second` — either
    found first, since `_closest_run` returns only the best match."""
    at, ratio = _closest_run(first, expected)
    if at >= 0 and ratio >= EXPECTED_REPEAT_SIMILAR:
        at2, ratio2 = _closest_run(second, expected[at + len(first) :])
        if at2 >= 0 and ratio2 >= EXPECTED_REPEAT_SIMILAR:
            return True
    at, ratio = _closest_run(second, expected)
    if at >= 0 and ratio >= EXPECTED_REPEAT_SIMILAR:
        at2, ratio2 = _closest_run(first, expected[:at])
        if at2 >= 0 and ratio2 >= EXPECTED_REPEAT_SIMILAR:
            return True
    return False


def split_expected_repeats(
    repeats: list[dict[str, Any]], expected: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`find_adjacent_repeats` hits on a render, split into `(unexpected,
    expected)`: a line the edit itself says twice — a narrator's false start
    the film then says properly (HISTORY.md § B7, run three) — is the edit's,
    not a surviving retake. Reported either way; only the first is a fault.

    Not a count: a render that says a line three times where the edit says
    it twice can have its extra take excused here. `compare`'s `repeated`,
    which diffs against the expected words, is the check that sees that.
    """
    unexpected: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for repeat in repeats:
        first, second = repeat["first_text"].split(), repeat["second_text"].split()
        (planned if _says_twice(first, second, expected) else unexpected).append(repeat)
    return unexpected, planned


def compare(expected: list[str], heard: list[str]) -> dict[str, Any]:
    """Diff the timeline's expected words against the render's heard words.

    `repeated` and `dropped` are heuristics over the diff, surfaced because
    reading a 900-line word diff to find one duplicated phrase is exactly the
    work this tool exists to avoid. Neither is authoritative — `diff` is.
    """
    matcher = difflib.SequenceMatcher(a=expected, b=heard, autojunk=False)

    repeated: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            extra = heard[j1:j2]
            # Extra words that closely resemble a run the timeline expects
            # *somewhere* are that line played twice: a retake the transcript
            # never showed as a retake.
            at, ratio = _closest_run(extra, expected)
            if at >= 0 and ratio >= SIMILAR:
                repeated.append(
                    {
                        "text": " ".join(extra),
                        "at_heard_word": j1,
                        # The take the timeline does account for, so a reader
                        # can see both readings side by side and pick.
                        "expects": " ".join(expected[at : at + len(extra)]),
                        "at_expected_word": at,
                        "similarity": round(ratio, 3),
                    }
                )
        if tag in ("delete", "replace"):
            missing = expected[i1:i2]
            # The opposite failure: a cut that reached past its word range.
            if len(missing) >= MIN_RUN:
                dropped.append(
                    {
                        "text": " ".join(missing),
                        "at_expected_word": i1,
                        # SequenceMatcher's own `j1` for this opcode — where
                        # in `heard` the drop sits, symmetric with
                        # `repeated`'s existing `at_heard_word`. This is the
                        # boundary index `finish_check`'s step 6 recheck
                        # needs: the words immediately either side of it in
                        # `heard` are what a windowed pass actually
                        # transcribed right where the miss happened.
                        "at_heard_word": j1,
                    }
                )

    return {
        "similarity": round(matcher.ratio(), 3),
        "repeated": repeated,
        "dropped": dropped,
        # One word per line, so the diff reads as a word-level diff rather than
        # two enormous paragraphs marked wholly changed.
        "diff": list(
            difflib.unified_diff(
                expected, heard, fromfile="timeline", tofile="render", n=2, lineterm=""
            )
        ),
    }
