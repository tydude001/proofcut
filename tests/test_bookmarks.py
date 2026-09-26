"""Bookmarks — `events` named `bookmark`, added at an edit instant and drawn on
the web ruler from `timeline_view`'s `bookmarks`.

A bookmark is an ordinary event, so it indexes the source: the properties
pinned here are that `time=` finds the source instant under the playhead, that
a cut hides a bookmark without losing it, that `remove` drops exactly one, and
that the ruler draws bookmarks and not a recorder's keystrokes.
PRIOR-ART.md § OpenCut classic, driven.

Built by hand, `test_ops_events.py`'s way: one 10s clip on a timeline with
2.0-5.0 cut out of it, so edit second 3.0 is source second 6.0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut.project import Project, ProjectError

CLIP = {"clip_id": "rec", "duration": 10.0, "has_video": True, "has_audio": False}


def _write_edit(project: Project, edit: tl.Edit) -> None:
    clip = project.read_manifest()["clips"][0]
    tl.write(tl.to_otio(edit, {"rec": clip}, rate=1000.0, name="proj"), project.timeline_path)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    media = tmp_path / "rec.mp4"
    media.write_bytes(b"stands in for a recording")
    manifest = project.read_manifest()
    manifest["clips"] = [{**CLIP, "source": str(media)}]
    project.write_manifest(manifest)
    _write_edit(project, tl.Edit([tl.Segment("rec", 0.0, 2.0), tl.Segment("rec", 5.0, 10.0)]))
    return project


def _marks(project: Project) -> list[tuple[float, float]]:
    view = ops.timeline_view(project.root)
    return [(b["time"], b["at"]) for b in view["bookmarks"]]


def test_time_adds_at_the_source_instant_under_the_playhead(project: Project) -> None:
    report = ops.events(project.root, name=ops.BOOKMARK_EVENT, time=3.0)

    assert report["clip_id"] == "rec"
    assert report["resolved"]["at"] == pytest.approx(6.0)
    assert report["resolved"]["address"] == "bookmark#0"
    assert _marks(project) == [(pytest.approx(3.0), pytest.approx(6.0))]


def test_time_refuses_past_the_end_a_wrong_clip_and_at_beside_it(project: Project) -> None:
    with pytest.raises(ProjectError, match="past the end"):
        ops.events(project.root, name=ops.BOOKMARK_EVENT, time=7.5)
    with pytest.raises(ProjectError, match="rec is"):
        ops.events(project.root, "other", name=ops.BOOKMARK_EVENT, time=1.0)
    with pytest.raises(ProjectError, match="not both"):
        ops.events(project.root, name=ops.BOOKMARK_EVENT, time=1.0, at=1.0)


def test_remove_drops_exactly_one(project: Project) -> None:
    for t in (1.0, 3.0, 6.0):
        ops.events(project.root, name=ops.BOOKMARK_EVENT, time=t)

    report = ops.events(project.root, "rec", remove="bookmark#1")

    assert report["removed"] == {"name": "bookmark", "at": pytest.approx(6.0), "address": "bookmark#1"}
    assert [t for t, _ in _marks(project)] == [pytest.approx(1.0), pytest.approx(6.0)]
    with pytest.raises(ProjectError, match="out of range"):
        ops.events(project.root, "rec", remove="bookmark#5")


def test_a_cut_hides_a_bookmark_and_a_restore_brings_it_back(project: Project) -> None:
    ops.events(project.root, name=ops.BOOKMARK_EVENT, time=1.0)
    _write_edit(project, tl.Edit([tl.Segment("rec", 5.0, 10.0)]))

    view = ops.timeline_view(project.root)
    assert view["bookmarks"] == []
    assert view["bookmarks_cut"] == 1

    _write_edit(project, tl.Edit([tl.Segment("rec", 0.0, 10.0)]))
    assert _marks(project) == [(pytest.approx(1.0), pytest.approx(1.0))]


def test_the_ruler_draws_bookmarks_and_not_other_events(project: Project) -> None:
    ops.events(project.root, "rec", name="key", at=1.5)
    ops.events(project.root, name=ops.BOOKMARK_EVENT, time=4.0)

    view = ops.timeline_view(project.root)

    assert [(b["address"], b["time"]) for b in view["bookmarks"]] == [("bookmark#0", pytest.approx(4.0))]
    assert view["bookmarks_cut"] == 0
