"""`reframe_sheet` — the window drawn on the source frame, for review.

PLAN.md § Per-shot framing, step 3, and it is a build item *beside* the
framing rather than after it: the hand-framed teaser had 2 of its 15 windows
wrong and **neither was visible in motion**. A badly-placed window reads as
framing, because nothing in the frame says otherwise — what catches one is
the whole source frame with the window drawn on it, where the material being
left out sits beside the material being kept.

Shells ffmpeg and magick for real, because the thing under test is what comes
out of them: a tile per sample and one montage. The video is generated here
rather than fixtured, so the frames the sheet draws on are real decoded ones.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from proofcut import faces, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

needs_tools = pytest.mark.skipif(
    shutil.which("magick") is None or shutil.which("ffmpeg") is None,
    reason="the sheet is ffmpeg's frames drawn on by magick",
)
needs_face = pytest.mark.skipif(
    not faces.available()["available"],
    reason="`extremes` probes with the real face detector (PROOFCUT_FACE)",
)

VO = {
    "clip_id": "vo",
    "source": "/tmp/vo.wav",
    "duration": 4.0,
    "has_video": False,
    "has_audio": True,
}


def _encode_video(path: Path, seconds: int = 8) -> None:
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size=1920x816:rate=30:duration={seconds}",
        "-pix_fmt", "yuv420p", str(path),
    ]  # fmt: skip
    subprocess.run(command, check=True)


@pytest.fixture(scope="session")
def clip_cache(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Path]:
    """The real `testsrc` encodes this file draws frames from, keyed by
    duration and built once for the whole session — every test wants the same
    1920x816 clip (or its 4s cousin for the no-picture-lane case), and
    re-encoding identical bytes per test bought nothing but the wait."""
    root = tmp_path_factory.mktemp("clips")
    cache: dict[int, Path] = {}
    for seconds in (8, 4):
        master = root / f"testsrc{seconds}.mp4"
        _encode_video(master, seconds=seconds)
        cache[seconds] = master
    return cache


def _video(path: Path, clip_cache: dict[int, Path], seconds: int = 8) -> None:
    """Copy the session's `seconds`-long encode to `path` — tests mutate
    their own project and media can be probed by mtime, so each one needs its
    own file rather than the cached one."""
    shutil.copy(clip_cache[seconds], path)


@pytest.fixture
def project(tmp_path: Path, clip_cache: dict[int, Path]) -> Project:
    """A VO with two cues onto one real 1920x816 clip, on a vertical canvas —
    so the film has two placements of one asset and something to crop."""
    project = Project.create(tmp_path / "proj")
    footage = tmp_path / "clipa.mp4"
    _video(footage, clip_cache)

    manifest = project.read_manifest()
    manifest["clips"] = [
        VO,
        {
            "clip_id": "clipa",
            "source": str(footage),
            "duration": 8.0,
            "has_video": True,
            "has_audio": False,
            "width": 1920,
            "height": 816,
        },
    ]
    project.write_manifest(manifest)

    edit = tl.Edit([tl.Segment("vo", 0.0, 4.0)])
    tl.write(
        tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0),
        project.timeline_path,
    )
    tx.save(
        tx.Transcript(
            clip_id="vo",
            words=(
                tx.Word(index=0, text="cold", start=0.0, end=0.4),
                tx.Word(index=1, text="open", start=2.0, end=2.4),
            ),
        ),
        project.transcript_path("vo"),
    )
    ops.canvas(project.root, size="1080x1920")
    return project


@needs_tools
def test_the_sheet_draws_a_row_per_placement_and_three_moments_each(
    project: Project,
) -> None:
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.cue_add(project.root, "vo", 1, "clipa")

    result = ops.reframe_sheet(project.root)

    assert result["count"] == 2, "a row per window shown — one each here, and not per clip"
    assert [row["asset"] for row in result["rows"]] == ["clipa", "clipa"]
    assert all(len(row["samples"]) == 3 for row in result["rows"])
    assert Path(result["sheet"]).exists()
    for row in result["rows"]:
        for sample in row["samples"]:
            assert Path(sample["png"]).exists()


@needs_tools
def test_each_row_is_labelled_with_the_window_in_force_at_that_moment(
    project: Project,
) -> None:
    """The two placements read different stretches of the one file, so a
    window that starts between them frames only the second — which is the
    whole reason the sheet is per placement."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=1.9)

    rows = ops.reframe_sheet(project.root)["rows"]

    assert rows[0]["samples"][0]["crop"] == "0,0,459,816"
    assert rows[1]["samples"][-1]["crop"] == "1461,0,459,816"


@needs_tools
def test_a_placement_crossing_a_window_boundary_says_so(project: Project) -> None:
    """`windows` counts the windows the *placement* crosses — off the geometry,
    not off where the samples happened to land. More than one means this shot
    is not framed alike throughout, which is what a reviewer needs pointing at,
    and it is also the preview/render asymmetry: the preview places the whole
    shot by the window at its `src_start`."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=1.0)

    rows = ops.reframe_sheet(project.root)["rows"]

    assert rows[0]["windows"] == 2
    assert [row["windows"] for row in rows] == [2, 2], "a property of the shot, not of the row"


@needs_tools
def test_a_blur_filled_window_is_named_on_its_row(project: Project) -> None:
    """PLAN.md § Blur-fill, step 4: a fill's rect is the whole frame, which is
    what it shows — so the row says it is a fill, not a crop that kept
    everything, and the stretch after it is an ordinary crop again."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", fill="blur")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=1.0)

    rows = ops.reframe_sheet(project.root)["rows"]

    assert [row["fill"] for row in rows] == [True, False]
    assert rows[0]["samples"][0]["crop"] == "0,0,1920,816"
    assert rows[1]["samples"][0]["crop"] == "1461,0,459,816"


# -- the keyframed move: a sliding window is never one static rect ---------
#
# PLAN.md § Per-shot framing, refused section; § The keyframed move.
# CLAUDE.md: "a wrong window reads as framing in motion" — this is that
# trap's mirror image, motion read as no window at all, so the row that
# precedes a sliding window has to draw its two ends rather than `crop_at`'s
# single answer for the whole stretch.


@needs_tools
def test_a_sliding_window_draws_both_ends_not_a_static_crop(project: Project) -> None:
    """The stretch *before* the flagged window is the one that is actually
    moving (MLT interpolates the segment leaving a keyframe — `test_mlt.py`
    has the render that settled it), so it is that row, not the destination
    window's own row, that gets the sliding treatment.

    One cue, not two — mirrors `test_a_placement_crossing_a_window_boundary_
    says_so`: two cues make two placements, each starting its own cursor
    fresh, and neither would cross the window this test needs crossed."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=2.0, interp=True)

    rows = ops.reframe_sheet(project.root)["rows"]

    assert [row["sliding"] for row in rows] == [True, False]
    assert rows[0]["slides_to"] == 2.0
    assert rows[1]["sliding"] is False and rows[1]["slides_to"] is None
    # Both ends, not three arbitrary looks at the departure rect: the first
    # sample is exactly where the window started and the last is exactly
    # where it lands.
    samples = rows[0]["samples"]
    assert samples[0]["crop"] == "0,0,459,816"
    assert samples[-1]["crop"] == "1461,0,459,816"
    assert samples[0]["pick"] == "slide-from"
    assert samples[-1]["pick"] == "slide-to"
    # Never a split — a sliding window cannot also carry a pane
    # (mlt.Reframe.__post_init__), and reusing that field would misreport
    # `split` below.
    assert all(sample["pane"] is None for sample in samples)
    assert rows[0]["split"] is False
    assert rows[0]["pane_overlap"] is None
    # The row after the flagged window is an ordinary step, holding the new
    # rect throughout — nothing about it looks like a slide.
    assert rows[1]["samples"][0]["crop"] == "1461,0,459,816"


@needs_tools
@needs_face
def test_a_sliding_row_keeps_the_montage_grid_even_with_extremes(project: Project) -> None:
    """`columns` is `SHEET_PICKS` under `extremes`, not `len(at)` — a sliding
    row still has to match whatever every other row in this run is drawing,
    or the montage's fixed-width grid shifts after it."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=2.0, interp=True)

    result = ops.reframe_sheet(project.root, extremes=True)

    assert len(result["rows"]) == 2, "one row for each side of the crossed window"
    sliding_row = next(row for row in result["rows"] if row["sliding"])
    assert len(sliding_row["samples"]) == ops.SHEET_PICKS
    assert all(len(row["samples"]) == ops.SHEET_PICKS for row in result["rows"])


@needs_tools
def test_a_slide_needs_at_least_two_columns(project: Project) -> None:
    """A single moment cannot show both ends, and one repeated tile would be
    the same false claim of a static crop this feature exists to fix — so a
    project holding a slide refuses rather than drawing one dishonestly."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=2.0, interp=True)

    with pytest.raises(ProjectError, match="at least two"):
        ops.reframe_sheet(project.root, moments=[0.5])


# -- the row is a window, which is what the coverage fix is ---------------


@needs_tools
def test_a_window_no_round_fraction_lands_in_is_still_drawn(project: Project) -> None:
    """The finding. Three fixed fractions of a placement missed 14 of the
    vertical cut's 55 windows and eight of those were hand-approved — a window
    covering a small slice of a long placement is one no round fraction lands
    in, and it was reported as reviewed (HISTORY.md § The thirty-nine windows,
    reviewed)."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=3.9)

    result = ops.reframe_sheet(project.root)

    assert result["placements"] == 1 and result["count"] == 2
    assert [row["window"] for row in result["rows"]] == [0.0, 3.9]
    assert result["rows"][1]["samples"][0]["crop"] == "1461,0,459,816"
    # And the old unit could not have drawn it: every fraction of the whole
    # placement lands before the boundary.
    assert all(s["src_time"] < 3.9 for s in result["rows"][0]["samples"])


@needs_tools
def test_a_boundary_within_a_frame_of_a_placement_edge_is_that_edge(
    project: Project,
) -> None:
    """A frame of tolerance, never an epsilon — `reframe_coverage`'s rule, and
    not optional here either.

    A window boundary and the placement that starts on it are the same instant
    a frame apart: 20.39538 against 20.39541 on the real vertical cut. Compared
    exactly, that splits off a stretch 30µs long, draws three tiles of it, and
    leaves the placement labelled with the window it is about to leave —
    fifteen of that cut's rows were exactly this. A boundary within a frame of
    the *end* goes for the mirror reason: a window with under a frame of a
    placement left is not one that placement shows, and whichever placement
    starts there draws it as its own head.
    """
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=2.0001)

    rows = ops.reframe_sheet(project.root)["rows"]

    assert len(rows) == 2, "two placements, one window each — not a 30µs sliver"
    assert [row["windows"] for row in rows] == [1, 1]
    # And the second placement is labelled with what the render actually steps
    # to, which is the later of the two addresses.
    assert rows[1]["window"] == 2.0
    assert rows[1]["samples"][0]["crop"] == "1461,0,459,816"


@needs_tools
def test_moments_are_fractions_of_the_window_not_of_the_placement(
    project: Project,
) -> None:
    """Which is what makes the coverage claim true rather than approximate: a
    stretch is sampled inside itself, so a short window gets the same three
    looks a long one does."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=2.0)

    rows = ops.reframe_sheet(project.root, moments=[0.5])["rows"]

    assert [row["src_start"] for row in rows] == [0.0, 2.0]
    assert [row["duration"] for row in rows] == [2.0, 2.0]
    assert [row["samples"][0]["src_time"] for row in rows] == [1.0, 3.0]
    assert [row["placement_duration"] for row in rows] == [4.0, 4.0]


@needs_tools
def test_a_card_is_skipped_and_named(project: Project) -> None:
    """A still is authored at the canvas and never cropped, so it has no
    window to review — saying which rows are missing is the difference between
    "nothing to check" and "not checked"."""
    project.cards_dir.joinpath("outro.png").write_bytes(b"\x89PNG")
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.cue_add(project.root, "vo", 1, "card:outro")

    result = ops.reframe_sheet(project.root)

    assert result["count"] == 1
    assert result["skipped"] == [
        {"index": 1, "asset": "card:outro", "why": "a still is never cropped"}
    ]


@needs_tools
def test_the_edits_own_track_is_sheeted_when_there_is_no_picture_lane(
    tmp_path: Path, clip_cache: dict[int, Path]
) -> None:
    """With no cues the edit *is* the picture, and it is framed by the same
    rects — so it is the same review, not a different one."""
    project = Project.create(tmp_path / "solo")
    footage = tmp_path / "solo.mp4"
    _video(footage, clip_cache, seconds=4)
    manifest = project.read_manifest()
    manifest["clips"] = [
        {
            "clip_id": "clipa",
            "source": str(footage),
            "duration": 4.0,
            "has_video": True,
            "has_audio": False,
            "width": 1920,
            "height": 816,
        }
    ]
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("clipa", 0.0, 2.0), tl.Segment("clipa", 3.0, 4.0)])
    tl.write(
        tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0),
        project.timeline_path,
    )
    ops.canvas(project.root, size="1080x1920")

    result = ops.reframe_sheet(project.root)

    assert result["count"] == 2, "a row per surviving segment"
    assert [row["src_start"] for row in result["rows"]] == [0.0, 3.0]


def test_moments_outside_a_placement_are_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="fractions of a placement"):
        ops.reframe_sheet(project.root, moments=[0.5, 1.5])


def _face(centre: float, *, width: float = 120.0) -> dict[str, Any]:
    """One plausible box, centred where the caller wants the subject."""
    return {
        "box": [centre - width / 2, 120.0, centre + width / 2, 400.0],
        "score": 0.9,
    }


def _stub_detector(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[dict[str, Any]]:
    """Stand in for the detector and hand back the jobs it was asked for.

    `test_ops_reframe_detect.py`'s helper, for its reason: insightface lives in
    another interpreter and what a box *means* is measured in
    `test_framing_control.py`. What is under test here is which frames get
    drawn, and the captured jobs are how that gets asserted.
    """
    seen: list[dict[str, Any]] = []

    def fake_detect(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen.extend(jobs)
        out = []
        for job in jobs:
            got = answer(job)
            if isinstance(got, str):
                out.append({"index": job["index"], "error": got})
            else:
                out.append(
                    {
                        "index": job["index"],
                        "frames": [{"ts": ts, "faces": got(ts)} for ts in job["timestamps"]],
                    }
                )
        return out

    monkeypatch.setattr(
        faces,
        "available",
        lambda: {"available": True, "python": "/stub", "model": "x", "why": None},
    )
    monkeypatch.setattr(faces, "detect", fake_detect)
    return seen


@needs_tools
def test_extremes_draws_the_subjects_ends_worst_tile_first(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the mode: a static rect over a moving subject has a worst
    moment, and three fixed fractions have no reason to find it. The crop does
    not move inside a stretch, so the worst moment is at one of the subject's
    own ends — leftmost or rightmost — which is what makes these three the
    right three rather than merely a denser sampling."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    # A subject walking right across the stretch, so its ends are unambiguous
    # and neither of them is where a round fraction lands.
    _stub_detector(monkeypatch, lambda _job: lambda ts: [_face(200.0 + ts * 400.0)])

    rows = ops.reframe_sheet(project.root, extremes=True)["rows"]

    assert [sample["pick"] for sample in rows[0]["samples"]] == ["rightmost", "median", "leftmost"]
    offsets = [abs(sample["offset"]) for sample in rows[0]["samples"]]
    assert offsets == sorted(offsets, reverse=True), "worst first — a sheet is read left to right"
    assert rows[0]["probe"] == "extremes"
    assert rows[0]["worst_offset"] == max(sample["offset"] for sample in rows[0]["samples"])


@needs_tools
def test_the_probe_grid_includes_the_fixed_fractions(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The measurement's own correction.** Probing at a rate finds the
    extreme of the probed sample and not of the stretch, so a fraction landing
    between two probes can catch a worse moment than any of them — it did on 5
    of the teaser's 16 rows, by up to 29px, which is a sheet that changed its
    sampling and got quietly worse. With the fractions in the grid the old
    sheet is a subset of this one and that cannot happen."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    seen = _stub_detector(monkeypatch, lambda _job: lambda _ts: [_face(900.0)])

    rows = ops.reframe_sheet(project.root, extremes=True)["rows"]

    begin, span = rows[0]["src_start"], rows[0]["duration"]
    asked = seen[0]["timestamps"]
    for moment in ops.SHEET_MOMENTS:
        when = begin + span * moment
        assert any(abs(ts - when) < 1e-9 for ts in asked), f"fraction {moment} was not probed"


@needs_tools
def test_a_stretch_with_no_face_says_so_and_is_still_drawn(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fraction presented as an extreme is a tile claiming evidence it does
    not have — the same failure as a refused window read as a centre crop. The
    tiles are still drawn, because a window nobody can review is worse than one
    reviewed without a subject number."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    _stub_detector(monkeypatch, lambda _job: lambda _ts: [])

    rows = ops.reframe_sheet(project.root, extremes=True)["rows"]

    assert rows[0]["probe"].startswith("no face in ")
    assert rows[0]["located"] == 0
    assert rows[0]["worst_offset"] is None
    assert len(rows[0]["samples"]) == ops.SHEET_PICKS, "the montage is a fixed grid"
    assert all(sample["subject_x"] is None for sample in rows[0]["samples"])
    assert all(Path(sample["png"]).exists() for sample in rows[0]["samples"])


@needs_tools
def test_a_two_face_frame_is_flagged_beside_its_offset(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`frame_centre` is area-weighted across every face, so two faces put the
    "subject" between them where neither is: the teaser's largest offset, 608px,
    is Stu at 1079 averaged with a bystander at 1775. The number is kept — it is
    the same subject rule the framing itself uses — and the count that explains
    it travels with it."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    _stub_detector(monkeypatch, lambda _job: lambda _ts: [_face(400.0), _face(1600.0)])

    rows = ops.reframe_sheet(project.root, extremes=True)["rows"]

    assert rows[0]["multi_face"] is True
    assert all(sample["faces"] == 2 for sample in rows[0]["samples"])


@needs_tools
def test_the_detector_failing_on_one_stretch_does_not_lose_the_others(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.cue_add(project.root, "vo", 1, "clipa")
    _stub_detector(
        monkeypatch,
        lambda job: "seek failed" if job["index"] == 0 else (lambda _ts: [_face(900.0)]),
    )

    rows = ops.reframe_sheet(project.root, extremes=True)["rows"]

    assert "seek failed" in rows[0]["probe"]
    assert rows[0]["worst_offset"] is None
    assert rows[1]["probe"] == "extremes"


def test_extremes_and_moments_are_refused_together(project: Project) -> None:
    """One replaces the other. A tuning argument silently ignored is how a run
    reports fractions it never sampled."""
    with pytest.raises(ProjectError, match="one or the other"):
        ops.reframe_sheet(project.root, moments=[0.5], extremes=True)


def test_extremes_refuses_before_decoding_when_there_is_no_detector(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reframe_detect`'s rule: a missing interpreter is a refusal that should
    arrive now rather than after ffmpeg has walked the film."""
    monkeypatch.setattr(
        faces,
        "available",
        lambda: {"available": False, "python": None, "model": None, "why": "no PROOFCUT_FACE here"},
    )
    with pytest.raises(faces.FaceError, match="no PROOFCUT_FACE here"):
        ops.reframe_sheet(project.root, extremes=True)


@needs_tools
def test_a_page_is_rows_and_keeps_the_project_wide_row_numbers(project: Project) -> None:
    """Paging is what makes this sheet reachable by something that can only
    see bytes, and the rule it must not break is addressing: `row` is the
    number a reader takes back to `reframe --src-start`, so page 1's first row
    is row 1 and never row 0 again."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.cue_add(project.root, "vo", 1, "clipa")

    whole = ops.reframe_sheet(project.root)
    first = ops.reframe_sheet(project.root, per_page=1, page=0)
    second = ops.reframe_sheet(project.root, per_page=1, page=1)

    assert [row["row"] for row in whole["rows"]] == [0, 1]
    assert [row["row"] for row in first["rows"]] == [0]
    assert [row["row"] for row in second["rows"]] == [1]
    # `count` stays the project's windows on every page — a reader that took
    # it for "what I am looking at" would report a film reviewed off one row.
    assert first["count"] == second["count"] == whole["count"] == 2
    assert first["drawn"] == second["drawn"] == 1
    assert first["pages"] == second["pages"] == 2
    assert first["page"] == 0 and second["page"] == 1
    assert first["per_page"] == 1 and whole["per_page"] is None


@needs_tools
def test_a_page_is_a_jpeg_and_the_whole_project_is_still_a_png(project: Project) -> None:
    """The two callers want different files. A person opens the montage, so
    unpaged stays the PNG at `SHEET_TILE_WIDTH` it has always been; a page is
    handed back as bytes in a tool result, where the shot sheet's measurement
    applies — JPEG, inside `SHEET_PAGE_WIDTH`, or vision downscales the rects
    and the labels away."""
    ops.cue_add(project.root, "vo", 0, "clipa")

    whole = ops.reframe_sheet(project.root)
    paged = ops.reframe_sheet(project.root, per_page=1, page=0)

    assert whole["sheet"].endswith("sheet.png")
    assert paged["sheet"].endswith("page0.jpg")
    assert Path(paged["sheet"]).is_file()


@needs_tools
def test_a_page_past_the_end_draws_nothing_and_says_so(project: Project) -> None:
    """`montage` raises on an empty tile list, correctly — so the page walk
    has to stop at `pages` rather than discovering the end as an exception."""
    ops.cue_add(project.root, "vo", 0, "clipa")

    past = ops.reframe_sheet(project.root, per_page=1, page=9)

    assert past["sheet"] is None
    assert past["rows"] == []
    assert past["drawn"] == 0
    assert past["count"] == 1 and past["pages"] == 1


@needs_tools
def test_a_page_only_draws_its_own_tiles(project: Project) -> None:
    """The page is sliced before a frame is extracted, not after the montage
    — under `extremes` the unsliced version pays the face detector for the
    whole project to draw six rows of it."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.cue_add(project.root, "vo", 1, "clipa")

    paged = ops.reframe_sheet(project.root, per_page=1, page=0)

    drawn = sorted(Path(p).name for p in Path(paged["sheet"]).parent.glob("*.png"))
    assert drawn and all(name.startswith("000-") for name in drawn), drawn


def test_paging_arguments_are_refused_before_anything_decodes(project: Project) -> None:
    with pytest.raises(ProjectError, match="at least one row"):
        ops.reframe_sheet(project.root, per_page=0)
    with pytest.raises(ProjectError, match="counted from 0"):
        ops.reframe_sheet(project.root, page=-1)


@needs_tools
def test_the_sheet_wipes_its_own_tiles_and_not_the_shared_frame_cache(project: Project) -> None:
    """It writes flat into `cache/sheets/` and every other sheet keeps a
    subdirectory of it — `SHEET_FRAMES_DIR` above all, the frame cache shared
    by every sheet there is. An `rmtree` of the whole directory threw that
    away on each framing review, and the only symptom was the next
    `shot_sheet` silently re-extracting frames it already had."""
    ops.cue_add(project.root, "vo", 0, "clipa")
    ops.reframe_sheet(project.root)

    sibling = project.sheet_dir / "frames" / "clipa"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "1000@384.png").write_bytes(b"cached frame")
    # A tile from a wider earlier run, which this one will not rewrite by name.
    leftover = project.sheet_dir / "999-0-0.15.png"
    leftover.write_bytes(b"an old row")

    ops.reframe_sheet(project.root)

    assert (sibling / "1000@384.png").read_bytes() == b"cached frame"
    # Its own tiles are still swept — 39 rows of the real film is ~120 MB of
    # them, so accumulating is the other way to be wrong here.
    assert not leftover.exists()


@needs_tools
def test_a_window_in_the_clips_last_tenth_draws_its_samples_off_the_last_frame(
    tmp_path: Path, clip_cache: dict[int, Path]
) -> None:
    """The stretch ends at the clip's end, but a frame is asked for by its
    start: ffmpeg's `-ss t` answers the first frame at or after `t`, so a
    sample past the last frame's start gets no frame at all and the whole
    sheet refused. A window 3 frames from the end put two of its three
    moments there. Samples are bounded by `_timeline_bound` less a frame."""
    project = Project.create(tmp_path / "proj")
    footage = tmp_path / "clipa.mp4"
    _video(footage, clip_cache)
    manifest = project.read_manifest()
    manifest["clips"] = [
        {
            "clip_id": "clipa",
            "source": str(footage),
            "duration": 8.0,
            "fps": 30.0,
            "has_video": True,
            "has_audio": False,
            "width": 1920,
            "height": 816,
        }
    ]
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("clipa", 0.0, 8.0)])
    tl.write(
        tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=30.0),
        project.timeline_path,
    )
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=7.9)

    rows = ops.reframe_sheet(project.root)["rows"]

    last = rows[-1]
    assert last["samples"][0]["crop"] == "1461,0,459,816"
    last_frame = 8.0 - 1 / 30
    assert all(sample["src_time"] <= round(last_frame, 3) for sample in last["samples"])
    assert all(Path(sample["png"]).exists() for sample in last["samples"])
