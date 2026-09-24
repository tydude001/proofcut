"""The lexicon's `hear` table as a caption correction list.

docs/plans/DAYDREAM.md § Caption reveal and corrections, designed. The words
are the pup-hotel rebuild's own, which burned "rough." for "ruff." and
"Pup BNB," for the brand — the two defects this exists to fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofcut import captions, lexicon, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.captions import CueWord
from proofcut.project import Project

LINE = "For Max, that can be, well, rough. Pup BNB, fetch a better stay."


def _words(text: str = LINE) -> list[CueWord]:
    return [CueWord(t, i * 0.4, i * 0.4 + 0.3) for i, t in enumerate(text.split())]


# -- matching ----------------------------------------------------------------


def test_a_fold_matches_whole_words_never_a_substring() -> None:
    """A substring fold is harmless to a WER and wrong on screen: `rough →
    ruff` must not print "ruffly"."""
    words, corrected = captions.fold(_words("roughly rough"), {"hear": {"rough": "ruff"}})
    assert [w.text for w in words] == ["roughly", "ruff"]
    assert len(corrected) == 1


def test_punctuation_either_side_survives_the_fold() -> None:
    """`group` breaks lines on a sentence end, so the full stop must stay."""
    words, _ = captions.fold(_words('"rough."'), {"hear": {"rough": "ruff"}})
    assert words[0].text == '"ruff."'


def test_a_multi_word_key_merges_the_words_it_matches() -> None:
    source = _words()
    words, corrected = captions.fold(source, {"hear": {"pup bnb": "PupBnB"}})
    merged = next(w for w in words if w.text.startswith("PupBnB"))
    assert merged.text == "PupBnB,"
    first = next(w for w in source if w.text == "Pup")
    second = next(w for w in source if w.text == "BNB,")
    assert (merged.start, merged.end) == (first.start, second.end)
    assert len(words) == len(source) - 1
    assert corrected == [{"at": first.start, "heard": "Pup BNB,", "shown": "PupBnB,", "rule": "pup bnb"}]


def test_a_lower_case_canonical_takes_a_sentence_opening_capital() -> None:
    words, _ = captions.fold(_words("Rough day"), {"hear": {"rough": "ruff"}})
    assert words[0].text == "Ruff"


def test_a_canonical_with_capitals_prints_as_written() -> None:
    words, _ = captions.fold(_words("the pup bnb site"), {"hear": {"pup bnb": "PupBnB"}})
    assert [w.text for w in words] == ["the", "PupBnB", "site"]


def test_the_longest_key_wins_where_two_start_on_one_word() -> None:
    table = {"hear": {"pup": "Pupper", "pup bnb": "PupBnB"}}
    words, _ = captions.fold(_words("pup bnb pup"), table)
    assert [w.text for w in words] == ["PupBnB", "Pupper"]


def test_a_neighbour_in_the_key_narrows_a_fold_to_one_place() -> None:
    """The design's answer to "only this one": a longer key, not a per-index record."""
    words, _ = captions.fold(_words("rough seas, well, rough."), {"hear": {"well rough": "well ruff"}})
    assert [w.text for w in words] == ["rough", "seas,", "well ruff."]


def test_a_merged_word_keeps_the_placed_audio_flag() -> None:
    """A retime never mutes a placed sound's words, and the flag has to
    survive the fold because the fold changes the word's value."""
    source = [CueWord("pup", 0.0, 0.3), CueWord("bnb", 0.4, 0.7, placed_audio=True)]
    words, _ = captions.fold(source, {"hear": {"pup bnb": "PupBnB"}})
    assert words[0].placed_audio is True


def test_the_wer_fold_uses_the_same_matcher() -> None:
    assert lexicon.fold_text("Long Legs is roughly rough", {"hear": {"long legs": "longlegs", "rough": "ruff"}}) == (
        "longlegs is roughly ruff"
    )


def test_a_key_with_no_words_is_refused() -> None:
    with pytest.raises(lexicon.LexiconError, match="no words"):
        lexicon.key_words(" ... ")


# -- the ops -----------------------------------------------------------------


CLIP = {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 8.0, "has_video": False, "has_audio": True}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = [CLIP]
    project.write_manifest(manifest)
    words = tuple(
        tx.Word(index=i, text=t, start=i * 0.5, end=i * 0.5 + 0.4) for i, t in enumerate(LINE.split())
    )
    tx.save(tx.Transcript(clip_id="vo", words=words), project.transcript_path("vo"))
    edit = tl.Edit([tl.Segment("vo", 0.0, 8.0)])
    tl.write(tl.to_otio(edit, {"vo": CLIP}, rate=1000.0), project.timeline_path)
    return project


def _shown(project: Project) -> list[str]:
    return [w["text"] for cue in ops.caption_view(project.root)["cues"] for w in cue["words"]]


def test_lexicon_add_corrects_the_captions_and_says_where(project: Project) -> None:
    result = ops.lexicon_add(project.root, "rough", "ruff")
    assert result["written"] is True
    assert result["matches"] == 1
    assert result["examples"][0]["shown"] == "ruff."
    view = ops.caption_view(project.root)
    assert "ruff." in _shown(project)
    assert "rough." not in _shown(project)
    assert [c["shown"] for c in view["corrected"]] == ["ruff."]


def test_the_file_is_the_projects_lexicon_json(project: Project) -> None:
    ops.lexicon_add(project.root, "Pup BNB", "PupBnB")
    assert json.loads((project.root / "lexicon.json").read_text(encoding="utf-8")) == {
        "hear": {"Pup BNB": "PupBnB"}
    }
    assert "PupBnB," in _shown(project)


def test_the_transcript_is_never_edited(project: Project) -> None:
    before = project.transcript_path("vo").read_bytes()
    ops.lexicon_add(project.root, "rough", "ruff")
    ops.add_captions(project.root, project.root / "out.ass")
    assert project.transcript_path("vo").read_bytes() == before


def test_the_sidecar_carries_the_correction(project: Project) -> None:
    ops.lexicon_add(project.root, "rough", "ruff")
    ass = project.root / "out.ass"
    result = ops.add_captions(project.root, ass)
    assert "ruff." in ass.read_text(encoding="utf-8")
    assert "rough" not in ass.read_text(encoding="utf-8")
    assert result["corrected"][0]["heard"] == "rough."


def test_plan_writes_nothing(project: Project) -> None:
    result = ops.lexicon_add(project.root, "rough", "ruff", plan=True)
    assert result["written"] is False
    assert result["matches"] == 1
    assert not (project.root / "lexicon.json").exists()


def test_the_same_key_differently_typed_replaces_and_says_so(project: Project) -> None:
    ops.lexicon_add(project.root, "Pup BNB", "PupBNB")
    result = ops.lexicon_add(project.root, "pup bnb,", "PupBnB")
    assert result["replaced"] == {"heard": "Pup BNB", "canonical": "PupBNB"}
    assert ops.lexicon_ls(project.root)["hear"] == {"pup bnb,": "PupBnB"}


def test_undo_does_not_revert_a_correction(project: Project) -> None:
    """The lexicon is a standing preference, not an edit in the history."""
    ops.caption_style(project.root, size=70)
    ops.lexicon_add(project.root, "rough", "ruff")
    ops.undo(project.root)
    assert "ruff." in _shown(project)


def test_lexicon_rm_removes_the_entry_and_the_last_one_the_file(project: Project) -> None:
    ops.lexicon_add(project.root, "rough", "ruff")
    ops.lexicon_rm(project.root, "ROUGH")
    assert not (project.root / "lexicon.json").exists()
    assert "rough." in _shown(project)


def test_lexicon_rm_refuses_a_key_it_lacks(project: Project) -> None:
    with pytest.raises(lexicon.LexiconError, match="no hear entry for 'rough'"):
        ops.lexicon_rm(project.root, "rough")


def test_say_entries_are_kept_beside_hear_ones(project: Project) -> None:
    ops.lexicon_add(project.root, "rough", "ruff")
    ops.lexicon_add(project.root, "Clarice", "Clariss", kind="say")
    listing = ops.lexicon_ls(project.root)
    assert listing["say"] == {"Clarice": "Clariss"}
    assert listing["hear"] == {"rough": "ruff"}


def test_a_broken_lexicon_is_reported_by_the_view_and_refused_by_the_file(project: Project) -> None:
    (project.root / "lexicon.json").write_text("{not json", encoding="utf-8")
    view = ops.caption_view(project.root)
    assert "not JSON" in view["lexicon_error"]
    assert view["cues"]
    with pytest.raises(captions.CaptionError, match="without their corrections"):
        ops.add_captions(project.root, project.root / "out.ass")
