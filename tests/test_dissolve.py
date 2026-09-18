"""A second recording after the first, and the dissolve between — RECUT.md step 8.

What melt draws is read back frame by frame in `test_server_stdio.py`
(`test_a_second_recording_follows_the_first_through_a_dissolve`), and the
mechanism was measured on both melts first
(`~/proofcut-work/spikes/dissolve-probe`). Pinned here: `follow` splices the
incoming clip after the first and records the dissolve by the incoming
clip's in-point; the plan resolves the join through the timeline and refuses
one it cannot find or a pre-roll the file has not got; and the writer draws
it on its own track without the Edit growing.

Built by hand like `test_ops_events.py`: `has_video` is declared, never probed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut.project import Project, ProjectError

RATE = 30.0
WINDOW = {"clip_id": "window", "duration": 10.0, "has_video": True, "has_audio": False, "width": 640, "height": 360, "picture_end": None}
TERMINAL = {"clip_id": "terminal", "duration": 20.0, "has_video": True, "has_audio": False, "width": 640, "height": 360, "picture_end": None}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    clips = []
    for record in (WINDOW, TERMINAL):
        source = tmp_path / f"{record['clip_id']}.mp4"
        source.write_bytes(b"stands in for a recording")
        clips.append({**record, "source": str(source)})
    manifest = project.read_manifest()
    manifest["clips"] = clips
    project.write_manifest(manifest)
    ops.events(project.root, "terminal", name="sent", at=4.0)
    ops.events(project.root, "terminal", name="done", at=12.0)
    edit = tl.Edit([tl.Segment("window", 0.0, 3.0), tl.Segment("window", 5.0, 8.0)])
    tl.write(tl.to_otio(edit, {"window": clips[0]}, rate=1000.0, name="proj"), project.timeline_path)
    return project


def test_follow_splices_after_the_last_of_the_first_and_records_the_dissolve(project: Project) -> None:
    result = ops.follow(project.root, "terminal", "window", from_event="sent", until_event="done", dissolve=0.5)

    segments = [(s.clip_id, s.start, s.end) for s in ops._load_edit(project).segments]
    assert segments == [("window", 0.0, 3.0), ("window", 5.0, 8.0), ("terminal", 4.0, 12.0)]
    assert result["timeline_duration"] == pytest.approx(14.0)
    assert project.read_manifest()[ops.DISSOLVES_KEY] == [
        {"clip_id": "terminal", "src_start": 4.0, "seconds": 0.5, "ease": "linear"}
    ]
    dissolve = result["dissolve"]
    assert (dissolve["after"], dissolve["join_frame"], dissolve["timeline_join"]) == ("window", 180, 6.0)
    assert ops._is_layered(project, ops._load_edit(project))


def test_the_writer_draws_the_pre_roll_over_the_join_and_the_film_does_not_grow(project: Project) -> None:
    ops.follow(project.root, "terminal", "window", src_start=4.0, src_end=6.0, dissolve=0.5)
    built = ops._build_mlt(project, ops._load_edit(project), fps=RATE)

    assert built["frames"] == 240
    [plan] = built["dissolves"]
    assert plan["join_frame"] == 180
    root = built["document"]
    entry = root.find("playlist[@id='xplaylist0a']/entry")
    assert (entry.get("in"), entry.get("out")) == ("105", "119"), "the 15 frames before the in-point"
    blank = root.find("playlist[@id='xplaylist0a']/blank")
    assert blank.get("length") == "165"
    fade = root.find("chain[@id='xchain0']/filter[@id='fade_xchain0']/property[@name='alpha']").text
    assert fade == "105=0;120=1"


def test_a_dissolve_longer_than_the_clip_has_before_its_start_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="needs 1s of 'terminal' before its in-point and the file has 0.5s"):
        ops.follow(project.root, "terminal", "window", src_start=0.5, src_end=6.0, dissolve=1.0)
    assert ops.DISSOLVES_KEY not in project.read_manifest()
    assert len(ops._load_edit(project).segments) == 2, "nothing was written"


def test_a_join_a_cut_removed_refuses_by_name(project: Project) -> None:
    ops.follow(project.root, "terminal", "window", src_start=4.0, src_end=8.0, dissolve=0.5)
    edit = ops._load_edit(project)
    edit.remove("terminal", 4.0, 5.0)
    tl.write(tl.to_otio(edit, ops._clips_by_id(project), rate=1000.0), project.timeline_path)

    with pytest.raises(ProjectError, match="no join on the timeline has 'terminal' starting at 4s"):
        ops._dissolve_plan(project, ops._load_edit(project), RATE)


def test_dissolve_changes_and_clears_the_crossfade_at_a_join(project: Project) -> None:
    ops.follow(project.root, "terminal", "window", src_start=4.0, src_end=8.0)
    assert ops.DISSOLVES_KEY not in project.read_manifest(), "no dissolve is a cut"

    ops.dissolve_set(project.root, "terminal", 4.0, 0.25, ease="ease")
    assert project.read_manifest()[ops.DISSOLVES_KEY][0]["seconds"] == 0.25
    ops.dissolve_set(project.root, "terminal", 4.0, 0.0)
    assert ops.DISSOLVES_KEY not in project.read_manifest()
    with pytest.raises(ProjectError, match="no join"):
        ops.dissolve_set(project.root, "terminal", 5.0, 0.5)


def test_follow_is_one_undo(project: Project) -> None:
    ops.follow(project.root, "terminal", "window", src_start=4.0, src_end=8.0, dissolve=0.5)
    ops.undo(project.root)
    assert len(ops._load_edit(project).segments) == 2
    assert ops.DISSOLVES_KEY not in project.read_manifest()
