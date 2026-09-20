"""Manifest-aware undo — docs/plans/POLISH.md § Step 03.

`snapshot()` copied `project.otio` and `restore()` put it back, which covered
cuts and nothing else. Most authoring state stopped living in the timeline
some time ago: the cue table, framing rects, the music bed, the caption style,
head/tail/holds, unspoken marks and card records are manifest keys that touch
no `project.otio` at all. So a mis-dragged cue had no undo while a cut undid
fine, and the window is what made both one gesture.

What is asserted here is the pair, the two asymmetric absences (an older
snapshot with no manifest; a snapshot from before there was a timeline), and
the once-per-instance guard that keeps an op writing both files to one undo
step. The wire-level half is in test_server_stdio.py.

Built by hand rather than through `import_media`, following
test_ops_reel.py: no ffprobe is needed to have a project with a shape.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.ops import REFRAME_KEY
from proofcut.project import (
    LEGACY_MANIFEST_NAME,
    MANIFEST_SNAPSHOT_SUFFIX,
    Project,
    ProjectError,
)

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


def _transcript() -> tx.Transcript:
    """One word a second, so a timeline second reads as a word index."""
    return tx.Transcript(
        clip_id="vo",
        words=tuple(tx.Word(index=i, text=f"w{i}", start=float(i), end=float(i) + 0.4) for i in range(12)),
    )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A 12s single-clip project with a timeline and a transcript."""
    project = Project.create(tmp_path / "proj")
    (tmp_path / "footage").mkdir()
    real = tmp_path / "footage" / "vo.mp4"
    real.write_bytes(b"not really an mp4")
    (project.media_dir / "vo.mp4").symlink_to(real)

    manifest = project.read_manifest()
    manifest["clips"] = [dict(CLIP)]
    manifest["timebase"] = 25.0
    project.write_manifest(manifest, snapshot=False)

    edit = tl.Edit([tl.Segment(clip_id="vo", start=0.0, end=12.0)])
    tl.write(tl.to_otio(edit, {"vo": dict(CLIP)}, rate=25.0, name="proj"), project.timeline_path)
    tx.save(_transcript(), project.transcript_path("vo"))
    return project


# -- the pair --------------------------------------------------------------


def test_a_snapshot_saves_both_files(project: Project) -> None:
    taken = project.snapshot()

    assert taken is not None
    assert taken.timeline is not None and taken.timeline.exists()
    assert taken.manifest is not None and taken.manifest.exists()
    assert taken.legacy is False


def test_a_manifest_only_mutation_is_undoable(project: Project) -> None:
    """The hole this step closes: a cue touches no `project.otio` at all."""
    before = ops.cue_ls(project.root)["cues"]
    ops.cue_add(project.root, clip_id="vo", word_index=4, asset="card:title")
    assert len(ops.cue_ls(project.root)["cues"]) == len(before) + 1

    report = ops.undo(project.root)

    assert report["manifest_restored"] is True
    assert ops.cue_ls(project.root)["cues"] == before


def test_undo_reports_which_halves_came_back(project: Project) -> None:
    """Three answers look identical from outside; only the flags separate them."""
    ops.cue_add(project.root, clip_id="vo", word_index=4, asset="card:title")

    report = ops.undo(project.root)

    assert report["timeline_restored"] is True
    assert report["manifest_restored"] is True
    assert report["timeline_removed"] is False
    assert report["segments"] == 1


def test_a_framing_rect_undoes(project: Project) -> None:
    """`reframe` is manifest-only too, and is a one-gesture mutation in Frame mode."""
    ops.reframe(project.root, clip_id="vo", rect="100,0,1720,816")
    assert project.read_manifest()[REFRAME_KEY]

    ops.undo(project.root)

    assert not project.read_manifest().get(REFRAME_KEY)


def test_a_mixed_sequence_undoes_in_reverse_one_mutation_at_a_time(project: Project) -> None:
    """A cut, a cue and a cut — each undo walks back exactly one decision.

    The failure this rules out is an op that snapshots twice (it writes both
    files) and so costs two presses to take back one thing.
    """
    ops.cut_by_time(project.root, spans=[[1.0, 2.0]])
    ops.cue_add(project.root, clip_id="vo", word_index=6, asset="card:title")
    ops.cut_by_time(project.root, spans=[[8.0, 9.0]])

    assert ops.status(project.root)["timeline_duration"] == pytest.approx(10.0)
    assert len(ops.cue_ls(project.root)["cues"]) == 1

    ops.undo(project.root)  # the second cut
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(11.0)
    assert len(ops.cue_ls(project.root)["cues"]) == 1

    ops.undo(project.root)  # the cue
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(11.0)
    assert ops.cue_ls(project.root)["cues"] == []

    ops.undo(project.root)  # the first cut
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(12.0)


# -- the once-per-instance guard ------------------------------------------


def test_one_op_is_one_undo_step_even_when_it_writes_both_files(project: Project) -> None:
    """`_save_edit` snapshots and so does `write_manifest`; an op doing both
    must still cost one press. The guard is per `Project` instance, which is
    per op, because every op opens its own at the top."""
    handle = Project.open(project.root)
    first = handle.snapshot()
    second = handle.snapshot()

    assert first is not None
    assert second is first
    assert len(handle.snapshots()) == 1


def test_two_instances_take_two_snapshots(project: Project) -> None:
    """Two ops, two undo steps — the guard must not span calls."""
    Project.open(project.root).snapshot()
    Project.open(project.root).snapshot()

    assert [s.index for s in project.snapshots()] == [0, 1]


# -- the two asymmetric absences ------------------------------------------


def test_a_legacy_timeline_only_snapshot_never_guesses_at_a_manifest(project: Project) -> None:
    """A history written by a proofcut that only saved timelines. It restores the
    timeline alone and says so, rather than inventing a manifest for it."""
    cut = ops.cut_by_time(project.root, spans=[[1.0, 2.0]])
    assert cut["duration_after"] == pytest.approx(11.0)
    # Rewind the stack to what an older proofcut would have left: the `.otio`
    # half alone. Seeded by hand, because no proofcut writes this shape any more.
    for path in project.history_dir.glob(f"*{MANIFEST_SNAPSHOT_SUFFIX}"):
        path.unlink()
    ops.cue_add(project.root, clip_id="vo", word_index=4, asset="card:title")
    for path in sorted(project.history_dir.glob(f"*{MANIFEST_SNAPSHOT_SUFFIX}")):
        path.unlink()

    report = ops.undo(project.root)

    assert report["manifest_restored"] is False
    assert report["timeline_restored"] is True
    assert "before proofcut saved manifests" in report["note"]
    # Untouched, not guessed at — the cue is still there.
    assert len(ops.cue_ls(project.root)["cues"]) == 1


def test_undoing_a_state_that_had_no_timeline_removes_the_one_that_was_laid_down(
    tmp_path: Path,
) -> None:
    """Undoing a seed. The snapshot holds a manifest and no timeline, so the
    state being restored is one with no timeline in it — leaving the seeded
    edit in place would report an undo that did not happen."""
    project = Project.create(tmp_path / "fresh")
    manifest = project.read_manifest()
    manifest["clips"] = [dict(CLIP)]
    project.write_manifest(manifest)  # snapshots: manifest only, no timeline yet
    assert project.snapshots()[-1].timeline is None

    edit = tl.Edit([tl.Segment(clip_id="vo", start=0.0, end=12.0)])
    tl.write(tl.to_otio(edit, {"vo": dict(CLIP)}, rate=25.0, name="fresh"), project.timeline_path)

    report = ops.undo(project.root)

    assert report["timeline_removed"] is True
    assert report["timeline_duration"] is None
    assert report["segments"] is None
    assert "was removed" in report["note"]
    assert not project.timeline_path.exists()


def test_nothing_to_undo_still_refuses(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "fresh")

    with pytest.raises(ProjectError, match="nothing to undo"):
        ops.undo(project.root)


# -- what a whole-manifest restore drags with it, stated rather than hidden --


def test_undoing_an_import_leaves_the_media_on_disk(project: Project) -> None:
    """A manifest is a registry. Un-registering a clip is an undo; deleting
    somebody's footage is not, and undo's docstring says so rather than the
    behaviour being discovered."""
    media_file = project.media_dir / "vo.mp4"
    manifest = project.read_manifest()
    manifest["clips"] = [*manifest["clips"], {**CLIP, "clip_id": "broll"}]
    project.write_manifest(manifest)

    ops.undo(project.root)

    assert [c["clip_id"] for c in project.read_manifest()["clips"]] == ["vo"]
    assert media_file.exists()


def test_the_migration_backup_is_not_an_undo_step(tmp_path: Path) -> None:
    """`proofcut-v3.json` shares `cache/history/` with the numbered snapshots and
    must stay invisible: rolling the timeline back one edit must not roll the
    schema back with it."""
    project = Project.create(tmp_path / "old")
    project.write_manifest({"schema_version": 1, "name": "old", "clips": []}, snapshot=False)

    Project.migrate(project.root)

    assert project.snapshots() == []
    assert (project.history_dir / "proofcut-v1.json").exists()


def test_a_snapshot_pair_is_numbered_together(project: Project) -> None:
    """The two halves find each other by index, so a half-written pair is
    still a readable snapshot rather than a crash in `int()`."""
    project.snapshot()
    (project.history_dir / "notes.txt").write_text("not a snapshot", encoding="utf-8")

    snapshots = project.snapshots()

    assert [s.index for s in snapshots] == [0]
    assert snapshots[0].manifest is not None
    assert json.loads(snapshots[0].manifest.read_text(encoding="utf-8"))["clips"]


# -- the rename (docs/plans/RENAME.md, decisions 1 and 2) -------------------


def _rekey_live_timeline_as_pre_rename(project: Project) -> None:
    """Re-key `project.otio` the way a lucid-era `to_otio` wrote it."""
    timeline = otio.adapters.read_from_file(str(project.timeline_path))
    stamped = [timeline, *(item for track in timeline.tracks for item in track)]
    for item in stamped:
        item.metadata["lucid"] = item.metadata["proofcut"]
        del item.metadata["proofcut"]
    tl.write(timeline, project.timeline_path)


def _metadata_keys(path: Path) -> set[str]:
    """Which stamp keys an `.otio` file carries, read off the file itself."""
    timeline = otio.adapters.read_from_file(str(path))
    stamped = [timeline, *(item for track in timeline.tracks for item in track)]
    return {key for item in stamped for key in item.metadata if key in {"lucid", "proofcut"}}


def test_a_snapshot_carrying_the_pre_rename_key_undoes(project: Project) -> None:
    """The snapshots in `cache/history/` carry the old key forever and undo
    puts one back whole — so the restored timeline must read, through the
    real `ops.undo` path, not only through the reader helper."""
    _rekey_live_timeline_as_pre_rename(project)
    ops.cut_by_time(project.root, spans=[[1.0, 2.0]])  # snapshots the old-key timeline
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(11.0)
    assert _metadata_keys(project.timeline_path) == {"proofcut"}  # the writer's key

    report = ops.undo(project.root)

    assert report["timeline_restored"] is True
    # The file put back is the pre-rename one, byte for key — not re-keyed.
    assert _metadata_keys(project.timeline_path) == {"lucid"}
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(12.0)
    # And an edit on top of it reads it and writes the new key.
    ops.cut_by_time(project.root, spans=[[3.0, 4.0]])
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(11.0)
    assert _metadata_keys(project.timeline_path) == {"proofcut"}


def _as_pre_rename_project(project: Project) -> dict[str, bytes]:
    """A lucid-era project with history: an old-key snapshot in
    `cache/history/`, an old-key live timeline, and `lucid.json`. Returns
    the history's bytes so a test can prove migration never touched them."""
    _rekey_live_timeline_as_pre_rename(project)
    ops.cut_by_time(project.root, spans=[[1.0, 2.0]])
    _rekey_live_timeline_as_pre_rename(project)
    project.manifest_path.rename(project.root / LEGACY_MANIFEST_NAME)
    return {p.name: p.read_bytes() for p in project.history_dir.iterdir()}


def test_migrate_rewrites_the_live_timeline_keys_and_never_history(project: Project) -> None:
    history = _as_pre_rename_project(project)
    otio_snapshots = [name for name in history if name.endswith(".otio")]
    assert otio_snapshots
    assert all(_metadata_keys(project.history_dir / name) == {"lucid"} for name in otio_snapshots)

    report = Project.migrate(project.root)

    assert report["steps"] == ["lucid.json -> proofcut.json"]
    assert report["timeline_keys"] == 3  # the timeline and both clips of the cut edit
    # Clean on its face...
    assert _metadata_keys(project.timeline_path) == {"proofcut"}
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(11.0)
    # ...and history exactly as it was, plus only the manifest backup.
    after = {p.name: p.read_bytes() for p in project.history_dir.iterdir()}
    assert set(after) - set(history) == {Path(report["backup"]).name}
    assert {name: after[name] for name in history} == history

    # Undo past the migration still reads the old-key snapshot.
    ops.undo(project.root)
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(12.0)
    assert _metadata_keys(project.timeline_path) == {"lucid"}


def test_migrate_plan_reports_the_filename_step_and_rewrites_no_keys(project: Project) -> None:
    history = _as_pre_rename_project(project)
    timeline_before = project.timeline_path.read_bytes()
    legacy_before = (project.root / LEGACY_MANIFEST_NAME).read_bytes()

    report = Project.migrate(project.root, plan=True)

    assert report["plan"] is True
    assert report["steps"] == ["lucid.json -> proofcut.json"]
    assert report["timeline_keys"] == 3
    assert report["migrated"] is False
    assert project.timeline_path.read_bytes() == timeline_before
    assert (project.root / LEGACY_MANIFEST_NAME).read_bytes() == legacy_before
    assert not project.manifest_path.exists()
    assert {p.name: p.read_bytes() for p in project.history_dir.iterdir()} == history
    # Still refused by every op until it is run for real.
    with pytest.raises(ProjectError, match="proofcut migrate"):
        ops.status(project.root)


# -- `steps` and `plan`: an agent's turn undone as one call ----------------


def _three_mutations(project: Project) -> None:
    ops.cut_by_time(project.root, spans=[[1.0, 2.0]])
    ops.cue_add(project.root, clip_id="vo", word_index=6, asset="card:title")
    ops.cut_by_time(project.root, spans=[[8.0, 9.0]])


def _state(project: Project) -> tuple[bytes, bytes]:
    return project.timeline_path.read_bytes(), project.manifest_path.read_bytes()


def test_steps_walks_back_exactly_that_many_mutations(project: Project) -> None:
    before = _state(project)
    _three_mutations(project)

    report = ops.undo(project.root, steps=2)

    assert report["steps"] == 2
    assert report["undo_depth"] == 1
    assert ops.status(project.root)["timeline_duration"] == pytest.approx(11.0)
    assert ops.cue_ls(project.root)["cues"] == []
    ops.undo(project.root)
    assert _state(project) == before


def test_steps_equals_that_many_single_undos_byte_for_byte(project: Project, tmp_path: Path) -> None:
    _three_mutations(project)
    twin = tmp_path / "twin"
    shutil.copytree(project.root, twin, symlinks=True)

    ops.undo(project.root, steps=3)
    for _ in range(3):
        ops.undo(twin)

    assert _state(project) == _state(Project.open(twin))
    assert ops.status(project.root)["undo_depth"] == 0


@pytest.mark.parametrize("steps", [0, -1, 4])
def test_an_out_of_range_steps_is_refused_before_anything_is_restored(
    project: Project, steps: int
) -> None:
    _three_mutations(project)
    before = _state(project)

    with pytest.raises(ProjectError, match="steps must be between 1 and 3"):
        ops.undo(project.root, steps=steps)

    assert _state(project) == before
    assert ops.status(project.root)["undo_depth"] == 3


def test_plan_writes_nothing_and_is_changes_account_of_the_same_steps(project: Project) -> None:
    _three_mutations(project)
    before = _state(project)
    files = sorted(p.name for p in project.history_dir.iterdir())

    planned = ops.undo(project.root, steps=2, plan=True)

    assert planned["plan"] is True
    assert planned["steps"] == 2
    assert planned["undo_depth"] == 3
    assert planned["changes"] == ops.changes(project.root, steps=2)
    assert _state(project) == before
    assert sorted(p.name for p in project.history_dir.iterdir()) == files


def test_plan_refuses_the_same_range_undo_does(project: Project) -> None:
    _three_mutations(project)

    with pytest.raises(ProjectError, match="steps must be between 1 and 3"):
        ops.undo(project.root, steps=9, plan=True)


def test_a_write_landing_mid_walk_stops_it_and_says_how_far_it_got(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stale-write check is `restore`'s, once per step, so a second writer
    arriving between two steps is refused rather than overwritten — and the
    refusal names the steps that had already come back."""
    from proofcut.project import ProjectConflictError

    _three_mutations(project)
    real = Project.restore
    calls = {"n": 0}

    def racing(self: Project):
        calls["n"] += 1
        if calls["n"] == 2:
            self.manifest_path.write_text(self.manifest_path.read_text() + " ")
        return real(self)

    monkeypatch.setattr(Project, "restore", racing)

    with pytest.raises(ProjectConflictError, match="1 of 3 steps had already been undone"):
        ops.undo(project.root, steps=3)

    assert len(Project.open(project.root).snapshots()) == 2
