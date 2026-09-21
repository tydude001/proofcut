"""The comparison itself, with no ASR and no project on disk.

`verify.compare` is pure sequence work, so the cases that matter — a clean
render, a surviving retake, an over-long cut — are cheap to state exactly here.
The end-to-end wiring is checked over stdio in test_server_stdio.py.
"""

from __future__ import annotations

from proofcut import verify


def test_tokens_normalise_away_punctuation_and_case() -> None:
    assert verify.tokens(["Hello, World!"]) == ["hello", "world"]
    # Multi-word strings flatten; apostrophes are part of the word, not noise.
    assert verify.tokens(["It's a", "cut."]) == ["it's", "a", "cut"]
    assert verify.tokens(["", "  ", "one"]) == ["one"]


def test_identical_sequences_are_a_clean_render() -> None:
    words = ["the", "killer", "calls", "from", "inside", "the", "house"]
    result = verify.compare(words, list(words))

    assert result["similarity"] == 1.0
    assert result["repeated"] == []
    assert result["dropped"] == []
    assert result["diff"] == []


def test_spelling_and_case_differences_do_not_register_as_changes() -> None:
    """The two sides are normalised before comparison, not after."""
    expected = verify.tokens(["The killer calls,", "from inside the house."])
    heard = verify.tokens(["the KILLER calls from inside the HOUSE"])

    assert verify.compare(expected, heard)["similarity"] == 1.0


def test_a_phrase_played_twice_is_reported_as_repeated() -> None:
    """The defect verify exists for: a retake whisper collapsed on the way in."""
    expected = ["w00", "w01", "w20", "w21", "w30", "w31"]
    heard = ["w00", "w01", "w20", "w21", "w20", "w21", "w30", "w31"]

    result = verify.compare(expected, heard)

    assert result["repeated"] == [
        {
            "text": "w20 w21",
            "at_heard_word": 4,
            "expects": "w20 w21",
            "at_expected_word": 2,
            "similarity": 1.0,
        }
    ]
    assert result["dropped"] == []
    assert result["similarity"] < 1.0


def test_a_retake_is_reported_even_though_the_two_takes_differ() -> None:
    """Two takes are never word-for-word — the difference is how you tell them apart.

    Both of these are real, from the Scream v1 export: the render says "at the
    second half" and then "in the second half", and "that it's a coincidence"
    then "that's a coincidence". Requiring the extra run verbatim left both in
    the diff for a human to find among 900 lines.
    """
    expected = verify.tokens(["falls apart a bit in the second half the 2022 one"])
    heard = verify.tokens(
        ["falls apart a bit at the second falls apart a bit in the second half the 2022 one"]
    )

    repeated = verify.compare(expected, heard)["repeated"]

    assert len(repeated) == 1
    assert repeated[0]["similarity"] >= verify.SIMILAR
    # And it reports the reading the timeline does account for, so both are
    # readable side by side without going to the transcript.
    assert "falls apart a bit" in repeated[0]["expects"]


def test_a_near_match_still_has_to_be_a_near_match() -> None:
    """Sharing a couple of common words is not saying the line twice."""
    expected = verify.tokens(["the killer calls from inside the house"])
    heard = verify.tokens(["the killer calls from inside the house and then the credits roll"])

    assert verify.compare(expected, heard)["repeated"] == []


def test_extra_words_the_timeline_never_expected_are_not_called_repeats() -> None:
    """`repeated` means "played twice", not "unexpected" — the diff covers that."""
    expected = ["one", "two", "three"]
    heard = ["one", "two", "brand", "new", "three"]

    result = verify.compare(expected, heard)

    assert result["repeated"] == []
    assert any(line.startswith("+brand") for line in result["diff"])


def test_words_the_render_never_played_are_reported_as_dropped() -> None:
    """A cut that reached past its word range: the inverse failure."""
    expected = ["one", "two", "three", "four", "five"]
    heard = ["one", "two", "five"]

    result = verify.compare(expected, heard)

    assert result["dropped"] == [
        {"text": "three four", "at_expected_word": 2, "at_heard_word": 2}
    ]
    assert result["repeated"] == []


def test_a_drop_at_the_very_start_of_heard_reports_at_heard_word_zero() -> None:
    """`finish_check`'s boundary recheck needs `at_heard_word` at every edge,
    not just mid-sequence — a drop before anything survived has nowhere to
    anchor but position 0."""
    expected = ["one", "two", "three", "four"]
    heard = ["three", "four"]

    result = verify.compare(expected, heard)

    assert result["dropped"] == [{"text": "one two", "at_expected_word": 0, "at_heard_word": 0}]


def test_a_drop_at_the_very_end_of_heard_reports_at_heard_word_len_heard() -> None:
    """The other edge: a drop after everything else survived anchors past
    the last heard word, `len(heard)`."""
    expected = ["one", "two", "three", "four"]
    heard = ["one", "two"]

    result = verify.compare(expected, heard)

    assert result["dropped"] == [
        {"text": "three four", "at_expected_word": 2, "at_heard_word": len(heard)}
    ]


def test_a_single_missing_word_is_below_the_noise_floor() -> None:
    """One word differing between two passes is whisper, not an edit."""
    expected = ["one", "two", "three"]
    heard = ["one", "three"]

    assert verify.compare(expected, heard)["dropped"] == []


def test_the_diff_is_word_per_line() -> None:
    """So a 900-word transcript diffs as words, not as two changed paragraphs."""
    result = verify.compare(["alpha", "beta"], ["alpha", "gamma"])
    diff = result["diff"]

    assert diff[0].startswith("--- timeline") and diff[1].startswith("+++ render")
    assert "-beta" in diff and "+gamma" in diff


# -- find_adjacent_repeats: a transcript checked against itself, at attach
# -- time, before there is an edit for `compare` to diff against -----------


def test_a_retake_both_takes_survive_into_the_edit_is_still_flagged() -> None:
    """The one retake `verify` can never catch.
    HISTORY.md § Adjacent near-duplicate phrases at `attach-transcript`.

    From the Scream v1 export, words 621/627: "I don't think that it's a
    coincidence" then "I don't think that's a coincidence". The timeline
    expects both — nothing cut either — so `compare` has nothing to diff
    against. This is the other half: the source transcript compared with
    itself.
    """
    sentence = (
        "so much going on here it's more of a meta commentary "
        "I don't think that it's a coincidence "
        "I don't think that's a coincidence that ghostface"
    )
    words = verify.tokens([sentence])

    hits = verify.find_adjacent_repeats(words)

    # One report for the one restart, not one per overlapping candidate
    # window around it.
    assert len(hits) == 1
    hit = hits[0]
    assert hit["similarity"] >= verify.SIMILAR
    assert hit["first_word"] < hit["second_word"]
    # Both windows sit inside the "...I don't think that[...] a coincidence
    # I don't think that's a coincidence..." run — not off somewhere else in
    # the sentence.
    assert 9 <= hit["first_word"] <= 12
    assert 16 <= hit["second_word"] <= 19


def test_an_exact_deliberate_repeat_is_also_flagged() -> None:
    """The tool doesn't distinguish a callback from a swallowed retake — a
    reader does. Roadmap: 'says which is which is the reader's job.'"""
    words = verify.tokens(
        ["movies don't create psychos movies make psychos more creative and that line lands"]
    )
    words = words[:5] + words[:5] + words[5:]

    hits = verify.find_adjacent_repeats(words)

    assert len(hits) == 1
    assert hits[0]["similarity"] == 1.0


def test_ordinary_dialogue_is_not_flagged() -> None:
    """No adjacent line says itself twice — nothing to report."""
    words = verify.tokens(
        ["the killer calls from inside the house and nobody believes her at first"]
    )

    assert verify.find_adjacent_repeats(words) == []


def test_a_single_shared_word_is_not_a_restart() -> None:
    """One word in common is not saying the line twice."""
    words = ["and", "then", "she", "screamed", "louder", "than", "he", "expected"]

    assert verify.find_adjacent_repeats(words) == []


def test_repeats_far_apart_are_not_adjacent() -> None:
    """A callback near the end of a long recording is not a retake — a retake
    follows within a breath, not a scene later."""
    words = (
        ["I", "don't", "think", "that's", "a", "coincidence"]
        + [f"filler{i}" for i in range(verify.ADJACENT_LOOKAHEAD + 10)]
        + ["I", "don't", "think", "that's", "a", "coincidence"]
    )

    assert verify.find_adjacent_repeats(words) == []


# -- repeats the edit itself contains (HISTORY.md § B7, run three) ------------

TAKE = verify.tokens(["in july of 1969 half a billion people watched three men leave for the moon um no let me take that again"])
FILM_LINE = verify.tokens(["in july of 1969 half a billion people watched three men leave the earth four days later"])


def test_a_repeat_the_expected_words_also_say_twice_is_expected() -> None:
    """The narrator's false start is said again by the film: planned, not a retake."""
    heard = TAKE + FILM_LINE
    repeats = verify.find_adjacent_repeats(heard)
    assert repeats, "the heard words do repeat the line"

    unexpected, expected = verify.split_expected_repeats(repeats, TAKE + FILM_LINE)

    assert unexpected == []
    assert expected == repeats


def test_a_repeat_the_expected_words_say_once_is_still_a_fault() -> None:
    heard = TAKE + FILM_LINE
    repeats = verify.find_adjacent_repeats(heard)

    unexpected, expected = verify.split_expected_repeats(repeats, FILM_LINE)

    assert unexpected == repeats
    assert expected == []
