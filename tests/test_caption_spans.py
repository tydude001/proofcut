"""Caption spans: captions off over a stretch, or a different look there.

docs/plans/DAYDREAM.md § Caption reveal and corrections, designed. The words
are the pup-hotel rebuild's, whose captions drew over its logo card and
could not put "well" alone and large the way the original does.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from proofcut import captions, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

LINE = "For Max, that can be, well, rough. Pup BNB, fetch a better stay."
WORDS = LINE.split()
CLIP = {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 8.0, "has_video": False, "has_audio": True}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = [CLIP]
    project.write_manifest(manifest)
    words = tuple(tx.Word(index=i, text=t, start=i * 0.5, end=i * 0.5 + 0.4) for i, t in enumerate(WORDS))
    tx.save(tx.Transcript(clip_id="vo", words=words), project.transcript_path("vo"))
    edit = tl.Edit([tl.Segment("vo", 0.0, 8.0)])
    tl.write(tl.to_otio(edit, {"vo": CLIP}, rate=1000.0), project.timeline_path)
    return project


def _cue_texts(project: Project) -> list[str]:
    return [cue["text"] for cue in ops.caption_view(project.root)["cues"]]


def test_an_off_span_hides_its_words_and_counts_them(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", phrase="Pup BNB,", until_phrase="stay.", off=True)
    view = ops.caption_view(project.root)
    shown = " ".join(_cue_texts(project))
    assert "fetch" not in shown and "Pup" not in shown
    assert shown.endswith("rough.")
    assert view["caption_off_words"] == 6


def test_a_style_span_draws_its_words_in_their_own_look(project: Project) -> None:
    ops.caption_span_add(
        project.root, "vo", phrase="well,", until_phrase="rough.", style={"max_words": 1, "size": 150, "position": "middle"}
    )
    view = ops.caption_view(project.root)
    big = [cue for cue in view["cues"] if cue["style"] == 1]
    assert [cue["text"] for cue in big] == ["well,", "rough."]
    assert view["styles"][1]["size"] == 150
    assert view["styles"][1]["position"] == "middle"
    assert view["styles"][0]["size"] == view["style"]["resolved"]["size"]


def test_a_line_never_crosses_a_span_edge(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", phrase="well,", until_phrase="well,", style={"size": 150})
    for cue in ops.caption_view(project.root)["cues"]:
        assert cue["style"] == 1 or "well," not in cue["text"].split()
    assert "well," in _cue_texts(project)


def test_the_line_before_a_span_does_not_hold_over_it(project: Project) -> None:
    """A span groups its own words, so the join needs `_hold`'s rule too, or
    libass draws two lines stacked."""
    ops.caption_style(project.root, hold=2.0)
    ops.caption_span_add(project.root, "vo", phrase="well,", until_phrase="well,", style={"size": 150})
    cues = ops.caption_view(project.root)["cues"]
    for before, after in pairwise(cues):
        assert before["end"] <= after["start"]


def test_a_span_takes_the_projects_later_restyle(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", phrase="well,", until_phrase="rough.", style={"size": 150})
    ops.caption_style(project.root, font="Zilla Slab")
    assert ops.caption_view(project.root)["styles"][1]["font"] == "Zilla Slab"


def test_the_sidecar_names_a_style_per_look(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", phrase="well,", until_phrase="rough.", style={"size": 150})
    ass = project.root / "out.ass"
    ops.add_captions(project.root, ass)
    text = ass.read_text(encoding="utf-8")
    assert "Style: proofcut-1,Outfit,150," in text
    big = [line for line in text.splitlines() if line.startswith("Dialogue:") and ",proofcut-1," in line]
    assert len(big) == 1 and "well," in big[0] and "rough." in big[0]


def test_a_later_span_wins_where_two_overlap(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", 0, until_word_index=len(WORDS) - 1, style={"size": 150})
    ops.caption_span_add(project.root, "vo", phrase="well,", until_phrase="rough.", off=True)
    assert "well," not in " ".join(_cue_texts(project))


def test_a_span_is_off_or_a_style_and_never_both(project: Project) -> None:
    with pytest.raises(ProjectError, match="exactly one"):
        ops.caption_span_add(project.root, "vo", 0, seconds=1.0)
    with pytest.raises(ProjectError, match="exactly one"):
        ops.caption_span_add(project.root, "vo", 0, seconds=1.0, off=True, style={"size": 2})


def test_a_span_cannot_change_the_preset(project: Project) -> None:
    with pytest.raises(captions.CaptionError, match="preset"):
        ops.caption_span_add(project.root, "vo", 0, seconds=1.0, style={"preset": "boxed"})


def test_a_span_a_cut_removed_is_reported_by_the_view_and_refused_by_the_file(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", phrase="well,", until_phrase="rough.", off=True)
    ops.cut_by_transcript(project.root, "vo", cut=[[6, 6]])
    view = ops.caption_view(project.root)
    assert "cut removed" in view["caption_spans_error"]
    with pytest.raises(captions.CaptionError, match="without their spans"):
        ops.add_captions(project.root, project.root / "out.ass")


def test_plan_writes_nothing_and_undo_removes_a_span(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", 0, seconds=1.0, off=True, plan=True)
    assert ops.caption_span_ls(project.root)["spans"] == []
    ops.caption_span_add(project.root, "vo", 0, seconds=1.0, off=True)
    assert len(ops.caption_span_ls(project.root)["spans"]) == 1
    ops.undo(project.root)
    assert ops.caption_span_ls(project.root)["spans"] == []


def test_rm_takes_a_span_off(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", 0, seconds=1.0, off=True)
    ops.caption_span_rm(project.root, 0)
    assert ops.caption_span_ls(project.root)["spans"] == []
    with pytest.raises(ProjectError, match="no caption span at position 0"):
        ops.caption_span_rm(project.root, 0)


def test_every_word_hidden_refuses_a_file_and_says_why_in_the_view(project: Project) -> None:
    ops.caption_span_add(project.root, "vo", 0, until_word_index=len(WORDS) - 1, off=True)
    assert ops.caption_view(project.root)["cues_error"] == "every word is inside a captions-off span"
    with pytest.raises(captions.CaptionError, match="captions-off span"):
        ops.add_captions(project.root, project.root / "out.ass")


def test_a_lines_hold_stops_where_an_off_span_begins(project: Project) -> None:
    ops.caption_style(project.root, hold=2.0)
    added = ops.caption_span_add(project.root, "vo", phrase="Pup", until_phrase="stay.", off=True)
    for cue in ops.caption_view(project.root)["cues"]:
        assert cue["end"] <= added["span"]["start"]
