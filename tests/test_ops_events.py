"""`events` — named instants in a recording, and `locate`'s `event=` address.

A screen recording has no words to hang an edit on; what it has is the
recorder's own log of when things happened (`sent`, `typing_started`, every
keystroke). The properties pinned here are the ones that make that log safe to
address: it is on the recorder's clock until `origin` moves it, a wrong clock
is refused whole rather than half kept, a repeated name has to be said which,
and an event keeps its source second across a cut, the way a word index does.
docs/plans/NATIVE.md § Part B, B1.

Built by hand, `test_ops_broll.py`'s way: one 10s clip on a timeline with
2.0-5.0 cut out of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut.project import Project, ProjectError

CLIP = {"clip_id": "rec", "duration": 10.0, "has_video": True, "has_audio": False}

#: The launch recorder's `marks.json` shape — wall-clock stamps, `start` the
#: recording's zero.
MARKS = {
    "start": 1789321344.5,
    "typing_started": 1789321346.5,
    "sent": 1789321350.0,
    "words": 1789321352.25,
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    media = tmp_path / "rec.mp4"
    media.write_bytes(b"stands in for a screen recording")
    manifest = project.read_manifest()
    manifest["clips"] = [{**CLIP, "source": str(media)}]
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("rec", 0.0, 2.0), tl.Segment("rec", 5.0, 10.0)])
    tl.write(
        tl.to_otio(edit, {"rec": manifest["clips"][0]}, rate=1000.0, name="proj"),
        project.timeline_path,
    )
    return project


def _write(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_import_moves_the_recorder_clock_onto_the_recording(
    project: Project, tmp_path: Path
) -> None:
    marks = _write(tmp_path, "marks.json", MARKS)

    report = ops.events(project.root, "rec", source=marks, origin="start")

    assert report["written"] is True
    assert report["count"] == 4
    listed = ops.events(project.root, "rec")["events"]
    assert [(e["name"], e["at"]) for e in listed] == [
        ("start", 0.0),
        ("typing_started", 2.0),
        ("sent", 5.5),
        ("words", 7.75),
    ]
    assert listed[2]["address"] == "sent#0"
    assert ops.events(project.root)["total"] == 4


def test_wall_clock_stamps_without_an_origin_are_refused_whole(
    project: Project, tmp_path: Path
) -> None:
    """Every event of a wrong clock is off by the same amount, so none is kept."""
    marks = _write(tmp_path, "marks.json", MARKS)

    with pytest.raises(ProjectError, match="4 of 4 events.*origin="):
        ops.events(project.root, "rec", source=marks)
    assert ops.events(project.root, "rec")["count"] == 0


def test_one_event_past_the_end_refuses_the_set(project: Project, tmp_path: Path) -> None:
    """Keeping the ones that happen to land inside would index wrong moments."""
    marks = _write(tmp_path, "marks.json", {"a": 1.0, "b": 12.0})

    with pytest.raises(ProjectError, match="1 of 2 events fall outside rec's 10.000s"):
        ops.events(project.root, "rec", source=marks)


def test_a_bare_list_needs_a_name_and_repeats_are_addressed_by_occurrence(
    project: Project, tmp_path: Path
) -> None:
    keys = _write(tmp_path, "keys.json", [3.0, 1.0, 2.0, 4.0, 6.0])

    with pytest.raises(ProjectError, match="name= has to say"):
        ops.events(project.root, "rec", source=keys)
    ops.events(project.root, "rec", source=keys, name="key")

    with pytest.raises(ProjectError, match="5 'key' events — say which, as key#0 to key#4"):
        ops.events(project.root, "rec", event="key")
    resolved = ops.events(project.root, "rec", event="key#1")["resolved"]
    assert resolved["at"] == 2.0, "k counts in time order, not file order"
    assert [c["address"] for c in resolved["context"]] == [
        "key#0", "key#1", "key#2", "key#3", "key#4"
    ]
    with pytest.raises(ProjectError, match="out of range"):
        ops.events(project.root, "rec", event="key#5")


def test_import_replaces_only_the_names_it_brings(project: Project, tmp_path: Path) -> None:
    """A recorder writes marks and keystrokes to two files; the second import
    must not erase the first. Found importing the launch recorder's real pair."""
    marks = _write(tmp_path, "marks.json", {"x": 1.0, "y": 2.0})
    keys = _write(tmp_path, "keys.json", [3.0, 4.0])
    fewer_keys = _write(tmp_path, "keys2.json", [5.0])

    ops.events(project.root, "rec", source=marks)
    assert ops.events(project.root, "rec", source=marks)["written"] is False
    ops.events(project.root, "rec", source=keys, name="key")
    ops.events(project.root, "rec", source=fewer_keys, name="key")
    listed = ops.events(project.root, "rec")["events"]
    assert [(e["name"], e["at"]) for e in listed] == [("x", 1.0), ("y", 2.0), ("key", 5.0)]


def test_plan_validates_and_writes_nothing(project: Project, tmp_path: Path) -> None:
    marks = _write(tmp_path, "marks.json", MARKS)

    report = ops.events(project.root, "rec", source=marks, origin="start", plan=True)

    assert report["count"] == 4 and report["written"] is False
    assert ops.events(project.root, "rec")["count"] == 0


def test_add_one_is_idempotent_and_echoes_its_address(project: Project) -> None:
    ops.events(project.root, "rec", name="click", at=6.0)
    ops.events(project.root, "rec", name="click", at=1.0)
    again = ops.events(project.root, "rec", name="click", at=6.0)

    assert again["written"] is False
    assert again["count"] == 2
    assert again["resolved"]["address"] == "click#1"


def test_clear_removes_the_key_and_is_undoable(project: Project) -> None:
    ops.events(project.root, "rec", name="click", at=6.0)
    ops.events(project.root, "rec", clear=True)

    assert "events" not in project.read_manifest()["clips"][0]
    ops.undo(project.root)
    assert ops.events(project.root, "rec")["count"] == 1


@pytest.mark.parametrize("bad", ["", "two words", "a#1"])
def test_a_name_is_one_token_without_a_hash(project: Project, bad: str) -> None:
    with pytest.raises(ProjectError, match="one token"):
        ops.events(project.root, "rec", name=bad, at=1.0)


def test_locate_maps_an_event_through_the_cut(project: Project) -> None:
    """The source second holds; the timeline answer moves, or is absent."""
    ops.events(project.root, "rec", name="sent", at=6.0)
    ops.events(project.root, "rec", name="gone", at=3.0)

    sent = ops.locate(project.root, "rec", event="sent")
    assert sent["mode"] == "event"
    assert sent["source_start"] == 6.0
    assert sent["timeline_start"] == pytest.approx(3.0), "2.0-5.0 was cut ahead of it"
    assert sent["event"]["address"] == "sent#0"

    gone = ops.locate(project.root, "rec", event="gone")
    assert gone["present"] is False


def test_locate_refuses_an_event_beside_another_address(project: Project) -> None:
    ops.events(project.root, "rec", name="sent", at=6.0)
    with pytest.raises(tl.TimelineError, match="one of"):
        ops.locate(project.root, "rec", event="sent", source_start=1.0)
