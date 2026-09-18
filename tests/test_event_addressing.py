"""The music bed and the picture cues addressed by events — RECUT.md step 2.

A screen recording has no words, so the B7 agent attached a transcript to
one to have something to hang the bed on (step 1 now refuses that). What it
needed was the bed and the cues taking the recorder's events, which
overlays, retime and insets already did through `_overlay_instant`. Both now
resolve through it: no second resolver.

Built by hand like `test_ops_events.py` — a 10 s video-only recording cut to
[0, 2) + [5, 10), so Edit time is source time up to 2 s and source minus 3 s
from 5 s on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proofcut import autoeditor, ops
from proofcut import timeline as tl
from proofcut.project import Project, ProjectError

RATE = 30.0
REC = {"clip_id": "rec", "duration": 10.0, "has_video": True, "has_audio": False}
BED = {"clip_id": "bed", "duration": 60.0, "has_video": False, "has_audio": True}
FILM = {"clip_id": "film", "duration": 20.0, "has_video": True, "has_audio": True}
#: (name, source second): `cut` sits in the removed [2, 5).
EVENTS = [("typing", 1.0), ("cut", 3.0), ("words", 6.0), ("sheets", 8.0)]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    clips = []
    for record in (REC, BED, FILM):
        source = tmp_path / f"{record['clip_id']}.media"
        source.write_bytes(b"stands in for media")
        clips.append({**record, "source": str(source)})
    manifest = project.read_manifest()
    manifest["clips"] = clips
    project.write_manifest(manifest)
    for name, at in EVENTS:
        ops.events(project.root, "rec", name=name, at=at)
    edit = tl.Edit([tl.Segment("rec", 0.0, 2.0), tl.Segment("rec", 5.0, 10.0)])
    tl.write(tl.to_otio(edit, {"rec": clips[0]}, rate=1000.0, name="proj"), project.timeline_path)
    return project


def _plan(project: Project) -> dict:
    edit = ops._load_edit(project)
    frames = sum(n for _, n in autoeditor.frame_layout(edit, RATE))
    return ops._music_plan(project, edit, RATE, edit_frames=frames)


# -- the bed ---------------------------------------------------------------


def test_a_bed_starts_and_ends_on_events_through_the_edit(project: Project) -> None:
    result = ops.music(project.root, asset="bed", clip_id="rec", event="typing", until_event="sheets")

    assert result["music"]["event"] == "typing"
    assert result["music"]["until_event"] == "sheets"
    assert "word_index_start" in result["music"] and result["music"]["word_index_start"] is None
    assert result["start_word"]["event"] == "typing" and result["start_word"]["at"] == 1.0
    plan = _plan(project)
    # typing at source 1.0 is Edit 1.0; sheets at source 8.0 is Edit 5.0.
    assert (plan["start_frame"], plan["end_frame"]) == (30, 150)


def test_a_passage_on_an_event_puts_the_drop_on_it(project: Project) -> None:
    """The launch clip's shape: the track from its head, then the drop (16 s
    into it) starting exactly where the transcript appears."""
    ops.music(
        project.root,
        asset="bed",
        clip_id="rec",
        event="typing",
        passages=[{"asset": "bed", "event": "words", "src_in": 16.0}],
    )
    pieces = _plan(project)["pieces"]
    drop = next(piece for piece in pieces if piece["passage"] == 1)
    # words at source 6.0 is Edit 3.0.
    assert drop["start_frame"] == 90
    assert drop["src_in_frames"] == 16 * 30


def test_a_bed_event_a_cut_removed_refuses_by_name(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="rec", event="cut")

    with pytest.raises(ProjectError, match=r"the music bed starts at 'rec' event 'cut'.*removed"):
        _plan(project)


def test_an_unknown_event_is_refused_before_anything_is_written(project: Project) -> None:
    with pytest.raises(ProjectError, match="rec has no event 'sent'"):
        ops.music(project.root, asset="bed", clip_id="rec", event="sent")
    assert ops.MUSIC_KEY not in project.read_manifest()


def test_a_word_and_an_event_for_one_boundary_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="or at an event, not both"):
        ops.music(project.root, asset="bed", clip_id="rec", event="typing", word_index_start=0)


def test_a_word_bed_gains_no_event_keys(project: Project) -> None:
    """Additive-optional: a bed that never used an event reads as it did."""
    ops.music(project.root, asset="bed", clip_id="rec", event="typing", until_event="sheets")
    ops.music(project.root, clear_end=True)
    stored = project.read_manifest()[ops.MUSIC_KEY]
    assert "until_event" not in stored
    assert stored["event"] == "typing"


# -- the cues --------------------------------------------------------------


def test_a_cue_on_an_event_projects_a_shot_there(project: Project) -> None:
    ops.cue_add(project.root, "rec", asset="film", event="typing")
    added = ops.cue_add(project.root, "rec", asset="rec", event="words")
    assert added["event"] == "words" and added["at"] == 6.0

    shots = ops.build_shots(project.root, fps=RATE)["shots"]
    assert [(s["asset"], s["event"], s["word_index"], s["start_frame"]) for s in shots] == [
        ("film", "typing", None, 0),
        ("rec", "words", None, 90),
    ]


def test_a_cue_on_a_cut_event_refuses_the_projection_as_a_cut_word_does(project: Project) -> None:
    ops.cue_add(project.root, "rec", asset="film", event="cut")

    with pytest.raises(tl.TimelineError, match=r"cue at 'rec' event 'cut' .* was cut from the edit"):
        ops.build_shots(project.root, fps=RATE)


def test_event_cues_list_remove_and_refuse_a_duplicate(project: Project) -> None:
    ops.cue_add(project.root, "rec", asset="film", event="words")
    with pytest.raises(Exception, match="already has a cue at event 'words'"):
        ops.cue_add(project.root, "rec", asset="rec", event="words")

    listed = ops.cue_ls(project.root)["cues"]
    assert [(c["event"], c["at"], c["word_index"]) for c in listed] == [("words", 6.0, None)]

    removed = ops.cue_rm(project.root, "rec", event="words")
    assert removed["asset"] == "film"
    assert project.read_manifest()["cues"] == []


def test_a_reel_keeps_the_event_cues_inside_its_span_and_drops_the_rest(project: Project) -> None:
    ops.cue_add(project.root, "rec", asset="film", event="typing")
    ops.cue_add(project.root, "rec", asset="rec", event="words")
    edit = ops._load_edit(project)

    orphans = ops._reel_orphan_cues(project, edit, 2.5, 5.0)

    assert [(c["event"], c["text"]) for c in orphans] == [("typing", "typing")]
