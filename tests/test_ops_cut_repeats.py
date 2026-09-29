"""`transcript_checks`' `cut` section: a retake the edit kept, named before a
render (`ops._cut_repeats`).

The `test_ops_finish_report.py` pattern: a real `Project`, a hand-written
`Edit`, a transcript saved directly, no media decoded. The claim under test is
that the check reads the *edit* — the same source retake is reported when the
cut keeps both takes and not when it drops one, while the source's own
`repeats` says the same thing both times.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project

#: "the best twelve minutes" said twice, then the line carries on. Word i
#: runs [i, i + 0.8), so a source second names a word.
SAID = ["the", "best", "twelve", "minutes", "the", "best", "twelve", "minutes", "of", "horror"]


def _project(tmp_path: Path, segments: list[tl.Segment] | None) -> Project:
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = [
        {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 12.0, "has_video": False, "has_audio": True}
    ]
    project.write_manifest(manifest)
    tx.save(
        tx.Transcript(
            clip_id="vo",
            words=tuple(tx.Word(index=i, text=w, start=float(i), end=i + 0.8) for i, w in enumerate(SAID)),
        ),
        project.transcript_path("vo"),
    )
    if segments is not None:
        clips_by_id = {c["clip_id"]: c for c in manifest["clips"]}
        tl.write(tl.to_otio(tl.Edit(segments), clips_by_id, rate=1000.0, name="proj"), project.timeline_path)
    return project


def test_a_retake_the_cut_keeps_is_named_with_its_source_address(tmp_path: Path) -> None:
    # Everything but the first word, so the timeline clock and the source's
    # differ and `timeline_start` has to be the mapped one.
    project = _project(tmp_path, [tl.Segment("vo", 1.0, 12.0)])

    cut = ops.transcript_checks(project.root)["cut"]

    assert cut["words"] == len(SAID) - 1
    [hit] = cut["repeats"]
    assert hit["first"]["text"] == "best twelve minutes the"
    assert (hit["first"]["clip_id"], hit["first"]["first_word"], hit["first"]["last_word"]) == ("vo", 1, 4)
    assert (hit["second"]["first_word"], hit["second"]["last_word"]) == (5, 8)
    assert hit["first"]["timeline_start"] == pytest.approx(0.0)
    assert hit["second"]["timeline_start"] == pytest.approx(4.0)


def test_a_cut_that_drops_the_first_take_reports_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path, [tl.Segment("vo", 4.0, 12.0)])

    checks = ops.transcript_checks(project.root)

    assert checks["cut"]["repeats"] == []
    # The source still holds the retake: the difference is the edit's.
    assert len(checks["clips"][0]["repeats"]) == 1


def test_an_unseeded_project_has_no_cut_to_check(tmp_path: Path) -> None:
    project = _project(tmp_path, None)

    assert ops.transcript_checks(project.root)["cut"] is None

