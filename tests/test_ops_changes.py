"""`changes` — what the last mutations did, read off the undo history.

PRIOR-ART.md § The OTIO + MCP niche names otio-diff as the "what did the agent
just change?" primitive. proofcut already keeps every pre-state in
`cache/history/`, so the op compares a snapshot against the live project and
stores nothing. What is asserted here: a cut reads as the words it removed and
not as every later segment moving, a restore reads as material added, a
record edited in place reads as changed, a reorder is named, the op writes
nothing, and the two asymmetric snapshot absences `undo` knows about are
reported rather than guessed at.

Built by hand like test_ops_undo.py — no ffprobe needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import MANIFEST_SNAPSHOT_SUFFIX, Project, ProjectError

CLIP: dict[str, Any] = {
    "clip_id": "vo",
    "source": "/tmp/vo.mp4",
    "media": "media/vo.mp4",
    "duration": 12.0,
    "has_video": True,
    "has_audio": True,
    "width": 1920,
    "height": 816,
    "fps": 25.0,
}


def _write_edit(project: Project, edit: tl.Edit) -> None:
    tl.write(tl.to_otio(edit, {"vo": dict(CLIP)}, rate=25.0, name="proj"), project.timeline_path)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A 12s single-clip project, one word a second (`w{i}` at [i, i+0.4))."""
    project = Project.create(tmp_path / "proj")
    (tmp_path / "footage").mkdir()
    real = tmp_path / "footage" / "vo.mp4"
    real.write_bytes(b"not really an mp4")
    (project.media_dir / "vo.mp4").symlink_to(real)

    manifest = project.read_manifest()
    manifest["clips"] = [dict(CLIP)]
    manifest["timebase"] = 25.0
    project.write_manifest(manifest, snapshot=False)

    _write_edit(project, tl.Edit([tl.Segment(clip_id="vo", start=0.0, end=12.0)]))
    words = tuple(tx.Word(index=i, text=f"w{i}", start=float(i), end=float(i) + 0.4) for i in range(12))
    tx.save(tx.Transcript(clip_id="vo", words=words), project.transcript_path("vo"))
    return project


def test_a_cut_reads_as_the_words_it_removed(project: Project) -> None:
    """One span, not every later segment shifting up by two seconds."""
    ops.cut_by_time(project.root, spans=[[3.0, 5.0]])

    report = ops.changes(project.root)
    timeline = report["timeline"]

    assert report["unchanged"] is False
    assert timeline["changed"] is True
    assert timeline["added"] == []
    assert timeline["removed_count"] == 1
    span = timeline["removed"][0]
    assert (span["clip_id"], span["source_start"], span["source_end"]) == ("vo", 3.0, 5.0)
    assert span["timeline_start"] == 3.0
    assert span["words"] == {"count": 2, "first_word": 3, "last_word": 4, "text": "w3 w4"}
    assert timeline["before"]["duration"] == pytest.approx(12.0)
    assert timeline["after"]["duration"] == pytest.approx(10.0)
    assert report["manifest"]["changed_keys"] == []


def test_a_word_is_matched_by_overlap_not_containment(project: Project) -> None:
    """A cut through the middle of a word still names it — partial survival is
    normal, and a containment test would call this cut wordless."""
    ops.cut_by_time(project.root, spans=[[5.2, 6.1]])

    span = ops.changes(project.root)["timeline"]["removed"][0]

    assert span["words"]["text"] == "w5 w6"


def test_a_restore_reads_as_material_added(project: Project) -> None:
    """Laid down by hand: `restore` probes the media, which this fixture fakes."""
    ops.cut_by_time(project.root, spans=[[3.0, 5.0]])
    Project.open(project.root).snapshot()
    _write_edit(
        project,
        tl.Edit([tl.Segment(clip_id="vo", start=0.0, end=4.4), tl.Segment(clip_id="vo", start=5.0, end=12.0)]),
    )

    report = ops.changes(project.root)

    assert report["timeline"]["removed"] == []
    assert [(s["source_start"], s["source_end"]) for s in report["timeline"]["added"]] == [(3.0, 4.4)]
    assert report["timeline"]["added"][0]["timeline_start"] == 3.0


def test_steps_covers_everything_since_that_point(project: Project) -> None:
    ops.cut_by_time(project.root, spans=[[1.0, 2.0]])
    ops.cue_add(project.root, clip_id="vo", word_index=6, asset="card:title")

    last = ops.changes(project.root)
    both = ops.changes(project.root, steps=2)

    assert last["timeline"]["changed"] is False
    assert last["manifest"]["changed_keys"] == ["cues"]
    assert both["timeline"]["removed_count"] == 1
    assert both["manifest"]["changed_keys"] == ["cues"]
    assert both["undo_depth"] == 2


def test_a_cue_added_echoes_its_word(project: Project) -> None:
    ops.cue_add(project.root, clip_id="vo", word_index=6, asset="card:title")

    cues = ops.changes(project.root)["manifest"]["keys"]["cues"]

    assert cues["added_count"] == 1
    assert cues["added"][0]["asset"] == "card:title"
    assert cues["added"][0]["echo"] == "w3 w4 w5 [w6] w7 w8 w9"
    assert "removed" not in cues


def test_a_record_edited_in_place_reads_as_changed(project: Project) -> None:
    """A cue re-pointed at another asset is one change, not a removal and an addition."""
    ops.cue_add(project.root, clip_id="vo", word_index=6, asset="card:title")
    ops.cue_rm(project.root, clip_id="vo", word_index=6)
    ops.cue_add(project.root, clip_id="vo", word_index=6, asset="card:other")

    cues = ops.changes(project.root, steps=2)["manifest"]["keys"]["cues"]

    assert "added" not in cues and "removed" not in cues
    assert cues["changed_count"] == 1
    change = cues["changed"][0]
    assert change["record"]["echo"] == "w3 w4 w5 [w6] w7 w8 w9"
    assert change["fields"]["asset"] == {"before": "card:title", "after": "card:other"}


def test_a_reorder_is_named_when_no_material_moved_in_or_out(project: Project) -> None:
    Project.open(project.root).snapshot()
    _write_edit(
        project,
        tl.Edit([tl.Segment(clip_id="vo", start=6.0, end=12.0), tl.Segment(clip_id="vo", start=0.0, end=6.0)]),
    )

    timeline = ops.changes(project.root)["timeline"]

    assert timeline["removed"] == [] and timeline["added"] == []
    assert timeline["reordered"] is True
    assert timeline["changed"] is True


def test_it_writes_nothing(project: Project) -> None:
    ops.cut_by_time(project.root, spans=[[3.0, 5.0]])
    before = {p: p.read_bytes() for p in (project.manifest_path, project.timeline_path)}
    depth = len(project.snapshots())

    ops.changes(project.root)

    assert len(project.snapshots()) == depth
    assert {p: p.read_bytes() for p in before} == before


def test_an_undone_change_is_gone_from_the_answer(project: Project) -> None:
    ops.cut_by_time(project.root, spans=[[3.0, 5.0]])
    ops.cue_add(project.root, clip_id="vo", word_index=6, asset="card:title")
    ops.undo(project.root)

    report = ops.changes(project.root)

    assert report["manifest"]["changed_keys"] == []
    assert report["timeline"]["removed_count"] == 1


def test_no_history_and_out_of_range_steps_refuse(project: Project) -> None:
    with pytest.raises(ProjectError, match="no history"):
        ops.changes(project.root)
    ops.cut_by_time(project.root, spans=[[3.0, 5.0]])
    for steps in (0, 2):
        with pytest.raises(ProjectError, match="between 1 and 1"):
            ops.changes(project.root, steps=steps)


def test_a_timeline_only_snapshot_compares_only_the_timeline(project: Project) -> None:
    """An older proofcut's snapshot has no manifest half, and nothing is guessed."""
    ops.cut_by_time(project.root, spans=[[3.0, 5.0]])
    for path in project.history_dir.glob(f"*{MANIFEST_SNAPSHOT_SUFFIX}"):
        path.unlink()

    report = ops.changes(project.root)

    assert report["manifest"] is None
    assert "before proofcut saved manifests" in report["note"]
    assert report["timeline"]["removed_count"] == 1


def test_a_snapshot_from_before_the_timeline_says_it_was_seeded(project: Project) -> None:
    timeline = project.timeline_path.read_bytes()
    project.timeline_path.unlink()
    Project.open(project.root).snapshot()
    project.timeline_path.write_bytes(timeline)

    report = ops.changes(project.root)

    assert report["timeline"]["before"] is None
    assert report["timeline"]["after"]["segments"] == 1
    assert "seeded" in report["timeline"]["note"]
    assert report["timeline"]["changed"] is True
    assert report["timeline"]["added_count"] == 1


def test_an_untranscribed_clip_is_named_and_spans_carry_no_words(project: Project) -> None:
    project.transcript_path("vo").unlink()
    ops.cut_by_time(project.root, spans=[[3.0, 5.0]])

    report = ops.changes(project.root)

    assert report["untranscribed"] == ["vo"]
    assert "words" not in report["timeline"]["removed"][0]


def test_a_frame_edge_moving_is_counted_not_listed(project: Project) -> None:
    """Two edits of one recording disagree by a frame at many edges; listed,
    those slivers crowd the real cuts out of the window."""
    ops.cut_by_time(project.root, spans=[[3.0, 5.0], [8.0, 8.04]])

    timeline = ops.changes(project.root)["timeline"]

    assert [s["source_start"] for s in timeline["removed"]] == [3.0]
    assert timeline["removed_count"] == 1
    assert timeline["removed_slivers"] == {"count": 1, "seconds": 0.04}  # one 25 fps frame
    assert timeline["changed"] is True
