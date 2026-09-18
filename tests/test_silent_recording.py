"""A screen recording with no audio stream on the Edit — RECUT.md step 1.

The B7 agent cut the launch clip from one, and two ops met it badly:
`export` crashed inside ffmpeg on `[0:a]` measuring the VO a bed's duck is
levelled against, and `attach_transcript` accepted a transcript for it,
which is how the bed came to hang on words nobody says. Both now refuse by
name. Built by hand like `test_ops_events.py`: the clip's `has_audio` is
declared in the manifest, never probed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from proofcut import media, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

CLIP = {"clip_id": "rec", "duration": 10.0, "has_video": True, "has_audio": False}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    source = tmp_path / "rec.mp4"
    source.write_bytes(b"stands in for a screen recording")
    manifest = project.read_manifest()
    manifest["clips"] = [{**CLIP, "source": str(source)}]
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("rec", 0.0, 2.0), tl.Segment("rec", 5.0, 10.0)])
    tl.write(tl.to_otio(edit, {"rec": manifest["clips"][0]}, rate=1000.0, name="proj"), project.timeline_path)
    return project


def test_vo_loudness_refuses_a_video_only_edit_before_ffmpeg(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Edit has segments, so the empty-Edit guard passes it; what it has
    not got is audio. ffmpeg must never be asked."""

    def no_ffmpeg(*args: object, **kwargs: object) -> None:
        raise AssertionError("ffmpeg ran against a clip with no audio stream")

    monkeypatch.setattr(subprocess, "run", no_ffmpeg)
    edit = ops._load_edit(project)
    assert edit.segments

    with pytest.raises(ProjectError, match=r"this timeline's video has no audio track \('rec'\)"):
        ops._vo_loudness(project, edit)


def test_attach_transcript_refuses_a_clip_with_no_audio(project: Project, tmp_path: Path) -> None:
    words = tx.Transcript(clip_id="rec", words=(tx.Word(index=0, text="words", start=1.0, end=1.5),))
    transcript = tmp_path / "rec.json"
    tx.save(words, transcript)

    with pytest.raises(media.MediaError, match="has no audio track"):
        ops.attach_transcript(project.root, "rec", transcript)
    assert not project.transcript_path("rec").exists()


def test_attach_transcript_still_takes_a_clip_never_probed_for_audio(project: Project, tmp_path: Path) -> None:
    """Only an explicit `False` refuses — a clip registered before
    `has_audio` existed says nothing either way."""
    manifest = project.read_manifest()
    del manifest["clips"][0]["has_audio"]
    project.write_manifest(manifest)
    words = tx.Transcript(clip_id="rec", words=(tx.Word(index=0, text="words", start=1.0, end=1.5),))
    transcript = tmp_path / "rec.json"
    tx.save(words, transcript)

    assert ops.attach_transcript(project.root, "rec", transcript)["words"] == 1
