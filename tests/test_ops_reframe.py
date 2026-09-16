"""`reframe` — which part of each clip survives into the frame.

Step 3 of the aspect swap, and the step that makes a swapped canvas fill the
frame instead of pillarboxing it. Two properties carry most of these tests:

- **A rect is geometry in source pixels, never a length**, so no cut can
  invalidate one — the same rule a footage description follows. Here that
  extends one step further: the rect is stored *as asked* and refit to
  whatever canvas is in force, so a canvas change cannot invalidate one
  either.
- **An override is a floor, not a frame.** A rect that is not the canvas's
  shape is *grown* to it rather than shrunk into it, because growing keeps
  everything asked for on screen and shrinking would cut the subject in half.

Built by hand rather than through `import_media`, following
`test_ops_canvas.py`: no ffprobe is needed to have a project with a shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proofcut import mlt, ops
from proofcut import timeline as tl
from proofcut.project import Project, ProjectError

WIDE = {
    "clip_id": "cold-open",
    "source": "/tmp/cold-open.mp4",
    "duration": 12.0,
    "has_video": True,
    "has_audio": True,
    "width": 1920,
    "height": 816,
}

VO = {
    "clip_id": "vo",
    "source": "/tmp/vo.wav",
    "duration": 12.0,
    "has_video": False,
    "has_audio": True,
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = [WIDE, VO]
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("cold-open", 0.0, 12.0)])
    tl.write(tl.to_otio(edit, {WIDE["clip_id"]: WIDE}, rate=1000.0), project.timeline_path)
    return project


def _clip(result: dict, clip_id: str) -> dict:
    return next(entry for entry in result["clips"] if entry["clip_id"] == clip_id)


# -- reading -------------------------------------------------------------


def test_no_arguments_reads_without_writing(project: Project) -> None:
    before = project.manifest_path.stat().st_mtime_ns
    result = ops.reframe(project.root)

    assert result["written"] is False
    assert project.manifest_path.stat().st_mtime_ns == before
    assert ops.REFRAME_KEY not in project.read_manifest()


def test_a_matching_aspect_keeps_everything_and_emits_nothing(project: Project) -> None:
    """No canvas override, so the canvas *is* the footage: the crop is the
    whole frame and no filter is written at all."""
    entry = _clip(ops.reframe(project.root), "cold-open")

    assert entry["crop"] == "0,0,1920,816"
    assert entry["reframes"] is False
    assert entry["kept"] == 1.0
    assert entry["origin"] == "centre"


def test_a_swapped_canvas_crops_and_says_what_it_costs(project: Project) -> None:
    """The measured centre crop: 459 of 1920 columns, so 76% of the footage
    goes rather than 76% of the frame going black."""
    ops.canvas(project.root, size="1080x1920")

    entry = _clip(ops.reframe(project.root), "cold-open")

    assert entry["crop"] == "730,0,459,816"
    assert entry["reframes"] is True
    assert entry["kept"] == 0.2391


def test_an_audio_clip_has_no_picture_to_crop(project: Project) -> None:
    assert [entry["clip_id"] for entry in ops.reframe(project.root)["clips"]] == ["cold-open"]


# -- setting an override -------------------------------------------------


def test_setting_stores_the_rect_as_asked(project: Project) -> None:
    """As asked, not as fitted — the record has to survive the canvas moving
    under it, and a fitted rect would silently bake in one canvas's shape."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="1200,0,459,816")

    assert project.read_manifest()[ops.REFRAME_KEY] == [
        {"clip_id": "cold-open", "rect": [1200, 0, 459, 816]}
    ]


def test_an_override_moves_the_crop_off_centre(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    result = ops.reframe(project.root, "cold-open", rect="1200,0,459,816")

    entry = _clip(result, "cold-open")
    assert entry["crop"] == "1200,0,459,816"
    assert entry["origin"] == "override"
    assert entry["asked"] == "1200,0,459,816"


def test_a_rect_of_the_wrong_shape_is_grown_rather_than_shrunk(project: Project) -> None:
    """The asymmetry the design turns on: growing pulls in surroundings,
    shrinking would cut the subject in half. So the ask is a floor, and both
    the ask and what it became are reported."""
    ops.canvas(project.root, size="1080x1920")
    result = ops.reframe(project.root, "cold-open", rect="800,300,300,200")

    entry = _clip(result, "cold-open")
    assert entry["asked"] == "800,300,300,200"
    assert entry["crop"] == "800,134,300,533"

    asked_x, asked_y, asked_w, asked_h = 800, 300, 300, 200
    crop_x, crop_y, crop_w, crop_h = (int(part) for part in entry["crop"].split(","))
    assert crop_x <= asked_x and crop_x + crop_w >= asked_x + asked_w
    assert crop_y <= asked_y and crop_y + crop_h >= asked_y + asked_h


def test_a_grown_rect_is_shifted_to_stay_inside_the_source(project: Project) -> None:
    """A rect that leaves the frame renders MLT's idea of what is past the
    edge, not the footage's."""
    ops.canvas(project.root, size="1080x1920")
    result = ops.reframe(project.root, "cold-open", rect="1850,700,60,100")

    crop_x, crop_y, crop_w, crop_h = (
        int(part) for part in _clip(result, "cold-open")["crop"].split(",")
    )
    assert crop_x >= 0 and crop_x + crop_w <= WIDE["width"]
    assert crop_y >= 0 and crop_y + crop_h <= WIDE["height"]


def test_plan_resolves_without_writing(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    planned = ops.reframe(project.root, "cold-open", rect="1200,0,459,816", plan=True)

    assert planned["written"] is False
    assert _clip(planned, "cold-open")["crop"] == "1200,0,459,816"
    assert ops.REFRAME_KEY not in project.read_manifest()


def test_reset_drops_one_clip_and_reset_alone_drops_every_one(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="1200,0,459,816")

    ops.reframe(project.root, "cold-open", reset=True)
    assert ops.REFRAME_KEY not in project.read_manifest()

    ops.reframe(project.root, "cold-open", rect="1200,0,459,816")
    ops.reframe(project.root, reset=True)
    assert ops.REFRAME_KEY not in project.read_manifest()


# -- what it refuses -----------------------------------------------------


@pytest.mark.parametrize(
    "rect, because",
    [
        ("1200,0,459", "X,Y,W,H"),
        ("a,b,c,d", "X,Y,W,H"),
        ("1200,0,0,816", "positive"),
        ("-10,0,459,816", "inside the source"),
        ("1800,0,459,816", "past the source"),
    ],
)
def test_refusals_name_the_value(project: Project, rect: str, because: str) -> None:
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match=because):
        ops.reframe(project.root, "cold-open", rect=rect)

    assert ops.REFRAME_KEY not in project.read_manifest(), "a refusal never half-writes"


def test_a_rect_that_cannot_be_shown_whole_names_the_one_that_can(project: Project) -> None:
    """"Use the whole 16:9 frame" in a 9:16 render is a genuinely impossible
    ask — refused rather than quietly clipped, and the refusal teaches the
    rect that works."""
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match="730,0,459,816"):
        ops.reframe(project.root, "cold-open", rect="0,0,1920,816")


def test_a_rect_without_a_clip_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="needs a clip_id"):
        ops.reframe(project.root, rect="1200,0,459,816")


def test_a_rect_and_reset_together_are_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="not both"):
        ops.reframe(project.root, "cold-open", rect="1200,0,459,816", reset=True)


def test_an_audio_clip_cannot_be_reframed(project: Project) -> None:
    with pytest.raises(ProjectError, match="no picture to crop"):
        ops.reframe(project.root, "vo", rect="0,0,10,10")


def test_an_unknown_clip_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="no clip"):
        ops.reframe(project.root, "nope", rect="0,0,10,10")


# -- the canvas moving under a stored rect -------------------------------


def test_a_stored_rect_is_refit_when_the_canvas_moves(project: Project) -> None:
    """The reason the ask is stored rather than the fit: the same record has
    to mean something at both shapes."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="800,300,300,200")
    vertical = _clip(ops.reframe(project.root), "cold-open")["crop"]

    ops.canvas(project.root, size="1920x1080")
    square_ish = _clip(ops.reframe(project.root), "cold-open")["crop"]

    assert vertical == "800,134,300,533"
    assert square_ish == "772,300,356,200"
    assert project.read_manifest()[ops.REFRAME_KEY][0]["rect"] == [800, 300, 300, 200]


def test_a_canvas_change_reports_a_rect_it_has_outgrown_rather_than_raising(
    project: Project,
) -> None:
    """Raising here would leave the project half-swapped — the manifest is
    already written by the time the report is built. So `canvas` names the
    clip to fix, and `export` is where it is refused."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="900,0,100,800")

    result = ops.canvas(project.root, size="1920x408")

    assert result["written"] is True
    assert [c["clip_id"] for c in result["reframe_conflicts"]] == ["cold-open"]
    assert "cannot be shown whole" in result["reframe_conflicts"][0]["why"]


def test_reading_the_table_still_works_with_a_rect_the_canvas_outgrew(
    project: Project,
) -> None:
    """Otherwise finding out which clip to reset would be impossible."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="900,0,100,800")
    ops.canvas(project.root, size="1920x408")

    entry = _clip(ops.reframe(project.root), "cold-open")

    assert entry["crop"] is None
    assert "cannot be shown whole" in entry["error"]

    ops.reframe(project.root, "cold-open", reset=True)
    assert _clip(ops.reframe(project.root), "cold-open")["error"] is None


# -- what `canvas` now says ----------------------------------------------


def test_canvas_reports_the_clips_a_swap_crops(project: Project) -> None:
    assert ops.canvas(project.root)["cropped"] == []

    result = ops.canvas(project.root, size="1080x1920")

    assert result["fills_frame"] is True
    assert result["cropped"] == ["cold-open"]


def test_plan_costs_the_canvas_being_planned_not_the_one_in_force(project: Project) -> None:
    """The whole point of planning is to see what the new shape costs."""
    planned = ops.canvas(project.root, size="1080x1920", plan=True)

    assert planned["cropped"] == ["cold-open"]
    assert ops.canvas(project.root)["cropped"] == [], "the project never took it"


# -- reaching the document -----------------------------------------------


def test_the_built_document_carries_the_crop(project: Project) -> None:
    """The end of the chain: a rect in the manifest becomes a `qtblend` filter
    on the timeline producer, keyed by resource rather than by clip_id."""
    ops.canvas(project.root, size="1080x1920")
    built = ops._build_mlt(project, ops._load_edit(project), fps=30.0)

    assert built["reframed"] == ["cold-open"]
    assert set(mlt.reframed_nodes(built["document"]).values()) == {"-1718 0 4518 1920 1"}


def test_an_unswapped_project_reaches_the_document_with_no_filter(project: Project) -> None:
    built = ops._build_mlt(project, ops._load_edit(project), fps=30.0)

    assert built["reframed"] == []
    assert mlt.reframed_nodes(built["document"]) == {}


# -- reaching the viewer -------------------------------------------------
#
# Step 4. The document half above is what melt renders; this is what the
# window draws, and the two have to be the same rectangle or the preview
# shows footage the export drops (PLAN.md § Aspect swap, step 4).


def test_the_view_carries_the_canvas_the_profile_declares(project: Project) -> None:
    assert ops.timeline_view(project.root)["canvas"] == [1920, 816]

    ops.canvas(project.root, size="1080x1920")

    assert ops.timeline_view(project.root)["canvas"] == [1080, 1920]


def test_the_view_hands_the_writers_own_destination_rect_to_the_page(
    project: Project,
) -> None:
    """The same numbers the `qtblend` filter carries, so the preview places
    media by reading the render's answer rather than re-deriving a crop."""
    ops.canvas(project.root, size="1080x1920")
    built = ops._build_mlt(project, ops._load_edit(project), fps=30.0)

    entry = ops.timeline_view(project.root)["reframe"]["cold-open"]

    assert entry["crop"] == [730, 0, 459, 816]
    assert entry["crops"] is True
    assert " ".join(str(n) for n in entry["dest"]) + " 1" in set(
        mlt.reframed_nodes(built["document"]).values()
    )


def test_an_unswapped_clip_is_still_placed_and_it_is_the_contain(
    project: Project,
) -> None:
    """One code path draws both. A clip the render does not crop still gets a
    `dest`, and it is `fit_rect` — what MLT does when no filter is emitted —
    so the page never has to choose between two ways of placing an element."""
    entry = ops.timeline_view(project.root)["reframe"]["cold-open"]

    assert entry["crops"] is False
    assert entry["dest"] == list(mlt.fit_rect((1920, 816), (1920, 816)))


def test_a_clip_with_no_picture_is_not_placed_at_all(project: Project) -> None:
    """There is nothing to crop, and an entry would invite the page to place
    an element that has no frame to put anywhere."""
    assert "vo" not in ops.timeline_view(project.root)["reframe"]


def test_a_rect_the_canvas_outgrew_is_reported_rather_than_raised(
    project: Project,
) -> None:
    """`shots_error`'s policy, for the same reason: the view is how a person
    finds the rect to fix, so it must not be what the stale rect takes down."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="900,0,100,800")
    ops.canvas(project.root, size="1920x408")

    view = ops.timeline_view(project.root)

    assert view["reframe"] == {}
    assert "cannot be shown whole" in view["reframe_error"]
    assert view["segments"], "the rest of the view still answers"


# -- per-shot framing ----------------------------------------------------
#
# PLAN.md § Per-shot framing. A cue cannot carry framing — cues and camera
# cuts are unrelated clocks, and at every scene threshold measured the film
# needs more windows than it has placements. So a window is addressed the way
# a footage description is: `(clip_id, src_start, rect)` in *source* seconds,
# which no cut can invalidate and which a clip used seven times reads seven
# different answers out of.


def test_a_window_stores_its_in_point_and_the_head_one_does_not(project: Project) -> None:
    """The head record is byte-identical to what a per-clip reframe always
    wrote — `src_start` absent means "from 0", which is what every rect on
    disk already meant. That is the whole reason this is not a schema bump."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816")
    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=4.0)

    assert project.read_manifest()[ops.REFRAME_KEY] == [
        {"clip_id": "cold-open", "rect": [0, 0, 459, 816]},
        {"clip_id": "cold-open", "rect": [1461, 0, 459, 816], "src_start": 4.0},
    ]


def test_the_windows_come_back_in_source_order(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=8.0)
    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=4.0)

    windows = _clip(ops.reframe(project.root), "cold-open")["windows"]

    assert [w["src_start"] for w in windows] == [0.0, 4.0, 8.0]
    # Nothing was asked for at the head, so it is still the centre default —
    # a window that has not started yet cannot frame what comes before it.
    assert windows[0] == {
        "src_start": 0.0,
        "crop": "730,0,459,816",
        "asked": None,
        "origin": "centre",
        "pane": None,
        "interp": False,
        "kept": round((459 * 816) / (1920 * 816), 4),
    }
    assert [w["origin"] for w in windows[1:]] == ["override", "override"]


def test_a_second_rect_at_one_in_point_replaces_it(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=4.0)
    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=4.0)

    records = project.read_manifest()[ops.REFRAME_KEY]

    assert len(records) == 1, "one entry per (clip_id, src_start)"
    assert records[0]["rect"] == [1461, 0, 459, 816]


def test_a_window_past_the_clip_is_refused(project: Project) -> None:
    """It would never come into force, and would sit in the manifest reading
    as framing that had been dealt with."""
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match="never come into force"):
        ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=12.0)


def test_reset_at_an_in_point_drops_only_that_window(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816")
    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=4.0)

    ops.reframe(project.root, "cold-open", src_start=4.0, reset=True)

    assert project.read_manifest()[ops.REFRAME_KEY] == [
        {"clip_id": "cold-open", "rect": [0, 0, 459, 816]}
    ]


def test_reset_at_an_in_point_with_no_window_there_is_refused(project: Project) -> None:
    """Silently succeeding would report a window dropped that is still in the
    render."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816")

    with pytest.raises(ProjectError, match="no reframe window at"):
        ops.reframe(project.root, "cold-open", src_start=4.0, reset=True)


def test_dropping_a_clip_drops_all_of_its_windows(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816")
    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=4.0)

    ops.reframe(project.root, "cold-open", reset=True)

    assert ops.REFRAME_KEY not in project.read_manifest()


def test_an_in_point_needs_a_rect_or_a_reset(project: Project) -> None:
    with pytest.raises(ProjectError, match="rect to put there"):
        ops.reframe(project.root, "cold-open", src_start=4.0)


def test_a_window_is_still_a_floor_and_is_grown_to_the_canvas(project: Project) -> None:
    """The same asymmetry as the head window: grown, never shrunk, because
    shrinking cuts the subject in half."""
    ops.canvas(project.root, size="1080x1920")
    result = ops.reframe(project.root, "cold-open", rect="800,300,200,200", src_start=4.0)

    window = next(w for w in _clip(result, "cold-open")["windows"] if w["src_start"] == 4.0)

    assert window["asked"] == "800,300,200,200"
    assert window["crop"] == "800,222,200,356"


def test_the_writer_gets_every_window_the_manifest_holds(project: Project) -> None:
    """The end-to-end shape: two stored windows become two keyframes on the
    one node the file already had."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816")
    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=4.0)

    built = ops._build_mlt(project, ops._load_edit(project), fps=30.0)

    rects = set(mlt.reframed_nodes(built["document"]).values())
    assert rects == {"0|=0 0 4518 1920 1;120|=-3438 0 4518 1920 1"}


def test_two_windows_at_one_in_point_are_reported_not_raised(project: Project) -> None:
    """Only reachable by hand-editing the manifest — the op refuses the second
    — and it is reported for `shots_error`'s reason: the table is how a person
    finds the window to drop."""
    ops.canvas(project.root, size="1080x1920")
    manifest = project.read_manifest()
    manifest[ops.REFRAME_KEY] = [
        {"clip_id": "cold-open", "rect": [0, 0, 459, 816], "src_start": 4.0},
        {"clip_id": "cold-open", "rect": [1461, 0, 459, 816], "src_start": 4.0},
    ]
    project.write_manifest(manifest)

    entry = _clip(ops.reframe(project.root), "cold-open")

    assert entry["crop"] is None
    assert "two reframe windows at one in-point" in entry["error"]
    assert "cannot" not in ops.timeline_view(project.root)["reframe_error"]


# -- the stacked split ------------------------------------------------------
#
# PLAN.md § The stacked split. For the shot one window cannot frame: a
# two-hander, where every face is a true positive and only one of them is the
# shot, so choosing between them loses one. Measured before it was built —
# 10 of the film's 59 windows hold more subjects than one crop can hold, and
# 3 of those hold them in every frame sampled.

#: A pane of a 1080x1920 canvas is 1080x960, so a pane window of this 1920x816
#: source is 918 wide against the 459 a single 9:16 crop gets.
PANE_LEFT = "0,0,918,816"
PANE_RIGHT = "1002,0,918,816"


def test_a_pane_makes_that_window_a_split(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")

    ops.reframe(project.root, "cold-open", rect=PANE_LEFT, pane=PANE_RIGHT)

    record = project.read_manifest()[ops.REFRAME_KEY][0]
    assert record["rect"] == [0, 0, 918, 816]
    assert record["pane"] == [1002, 0, 918, 816]
    window = _clip(ops.reframe(project.root), "cold-open")["windows"][0]
    assert window["crop"] == "0,0,918,816"
    assert window["pane"] == "1002,0,918,816"
    # A split keeps *more* of the source than the window it replaces — which
    # is the whole reason to draw one.
    assert window["kept"] > round((459 * 816) / (1920 * 816), 4)


def test_an_unsplit_window_still_writes_the_record_it_always_did(project: Project) -> None:
    """`pane` is absent-means-what-every-older-window-meant, so this is
    deliberately not a schema bump (CLAUDE.md)."""
    ops.canvas(project.root, size="1080x1920")

    ops.reframe(project.root, "cold-open", rect="0,0,459,816")

    assert project.read_manifest()[ops.REFRAME_KEY] == [
        {"clip_id": "cold-open", "rect": [0, 0, 459, 816]}
    ]


def test_a_pane_rect_is_grown_to_the_full_source_height(project: Project) -> None:
    """Growing to the pane's 9:8 alone is not enough, and this is the test that
    caught it: nothing masks a pane, so a rect of the right shape but a
    fraction of the height scales the frame up until it overruns its pane and
    draws into the other one. Full height makes the scaled frame exactly one
    pane tall, so the ask moves the window sideways and nothing else."""
    ops.canvas(project.root, size="1080x1920")

    ops.reframe(project.root, "cold-open", rect="400,300,200,200", pane=PANE_RIGHT)

    window = _clip(ops.reframe(project.root), "cold-open")["windows"][0]
    assert window["asked"] == "400,300,200,200"
    assert window["crop"] == "41,0,918,816"
    assert window["pane"] == "1002,0,918,816"


def test_a_source_too_tall_to_carry_a_pane_is_refused(project: Project) -> None:
    """And refused at the keyboard rather than rendering as two halves
    bleeding into each other, which melt would have exited 0 on."""
    manifest = project.read_manifest()
    manifest["clips"] = [{**WIDE, "width": 800, "height": 816}, VO]
    project.write_manifest(manifest)
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match="cannot be shown whole"):
        ops.reframe(project.root, "cold-open", rect="0,0,800,816", pane="0,0,800,816")


def test_a_pane_on_its_own_is_refused(project: Project) -> None:
    """A pane is half of a window rather than a window of its own."""
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match="needs an upper one"):
        ops.reframe(project.root, "cold-open", pane=PANE_RIGHT)


def test_a_canvas_the_split_cannot_survive_is_reported_not_raised(project: Project) -> None:
    """Stored as asked like every other rect, so a swap cannot invalidate one —
    but it can make one impossible: a 1920x540 pane of a landscape canvas needs
    2901 columns of an 1920-wide source. Reported rather than raised, for the
    reason every other outgrown rect is: reading the table is how someone finds
    out which window to fix, and `export` is where it is refused."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect=PANE_LEFT, pane=PANE_RIGHT)

    ops.canvas(project.root, size="1920x1080")

    entry = _clip(ops.reframe(project.root), "cold-open")
    assert entry["windows"] is None
    assert entry["asked"] == "0,0,918,816", "the ask itself is untouched"
    assert "too tall to stack" in entry["error"]


def test_a_split_reaches_the_document_as_a_second_node(project: Project) -> None:
    """The end of the chain: what `reframe` stores has to arrive at the writer
    as panes, or the manifest says split and the render says otherwise."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect=PANE_LEFT, pane=PANE_RIGHT, src_start=4.0)

    entry = ops._reframe_map(project, (1080, 1920))["cold-open"]

    assert entry.panes == ((4.0, (1002, 0, 918, 816)),)
    assert entry.is_split(5.0) is True
    assert entry.is_split(1.0) is False
    assert entry.pane_rect_property((1080, 1920), 24.0).split(";")[0].endswith(" 0")


def test_resetting_a_window_takes_its_pane_with_it(project: Project) -> None:
    """The pane rides the same record precisely so it cannot outlive the
    window it is half of."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect=PANE_LEFT, pane=PANE_RIGHT, src_start=4.0)

    ops.reframe(project.root, "cold-open", src_start=4.0, reset=True)

    assert ops.REFRAME_KEY not in project.read_manifest()


def test_a_rect_at_the_same_in_point_replaces_a_split_with_a_solo(project: Project) -> None:
    """Changing one's mind about a split is setting the window again without
    a pane — not a second op, and not a leftover half."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect=PANE_LEFT, pane=PANE_RIGHT, src_start=4.0)

    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=4.0)

    records = project.read_manifest()[ops.REFRAME_KEY]
    assert len(records) == 1
    assert "pane" not in records[0]


# -- the keyframed move: PLAN.md § Per-shot framing, refused section; § The
# keyframed move. `interp` marks the window arriving as one that slides in
# from whatever governed before it — the writer decides which MLT key that
# implies (mlt.Reframe.rect_property, test_mlt.py's own coverage); this file
# is only the store and the op that reaches it.


def test_interp_is_absent_by_default_and_reads_back_on_the_flagged_window(
    project: Project,
) -> None:
    """Absent-means-a-step is what every window before this existed meant, so
    setting one window's flag must not touch its neighbours' or the head's."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=4.0)

    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=8.0, interp=True)

    windows = _clip(ops.reframe(project.root), "cold-open")["windows"]
    by_start = {w["src_start"]: w["interp"] for w in windows}
    assert by_start == {0.0: False, 4.0: False, 8.0: True}


def test_interp_is_written_only_when_true(project: Project) -> None:
    """No new key on a record that never asked for one — the `pane`/`canvas`
    shape rather than `cards`, so an unflagged window's record is untouched."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=4.0)

    records = project.read_manifest()[ops.REFRAME_KEY]

    assert records == [{"clip_id": "cold-open", "rect": [0, 0, 459, 816], "src_start": 4.0}]


def test_interp_needs_a_rect(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match="needs a rect"):
        ops.reframe(project.root, "cold-open", interp=True)


def test_interp_cannot_combine_with_a_split(project: Project) -> None:
    """The lower pane has no interpolation of its own, so a window that is
    both would move on top and step underneath."""
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match="cannot also slide"):
        ops.reframe(
            project.root, "cold-open", rect=PANE_LEFT, pane=PANE_RIGHT,
            src_start=4.0, interp=True,
        )  # fmt: skip


def test_interp_on_the_head_is_refused(project: Project) -> None:
    """There is nothing before the head of the source to slide from — refused
    whether the head is named explicitly (`--at 0`) or by omission."""
    ops.canvas(project.root, size="1080x1920")

    with pytest.raises(ProjectError, match="head of"):
        ops.reframe(project.root, "cold-open", rect="0,0,459,816", interp=True)
    with pytest.raises(ProjectError, match="head of"):
        ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=0.0, interp=True)


def test_a_hand_edited_head_flagged_to_slide_is_reported_not_raised(project: Project) -> None:
    """Only reachable by hand-editing the manifest — `reframe` never writes
    one — so it is reported the same way a hand-edited duplicate in-point is,
    which is how reading the table stays possible at all."""
    ops.canvas(project.root, size="1080x1920")
    manifest = project.read_manifest()
    manifest[ops.REFRAME_KEY] = [
        {"clip_id": "cold-open", "rect": [0, 0, 459, 816], "interp": True}
    ]
    project.write_manifest(manifest)

    entry = _clip(ops.reframe(project.root), "cold-open")

    assert entry["windows"] is None
    assert "flagged to slide" in entry["error"]


def test_the_writer_gets_the_interp_flag(project: Project) -> None:
    """The end of the chain: what `reframe` stores has to arrive at the
    writer as `Reframe.interp`, or the manifest asks for a slide the render
    never draws."""
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=4.0)
    ops.reframe(project.root, "cold-open", rect="1461,0,459,816", src_start=8.0, interp=True)

    entry = ops._reframe_map(project, (1080, 1920))["cold-open"]

    assert entry.interp == (8.0,)
    assert entry.is_interp(4.0) is False
    assert entry.is_interp(8.0) is True


# -- blur-fill ---------------------------------------------------------------
#
# PLAN.md § Blur-fill: `fill="blur"` makes a window draw the whole source,
# contained, over a blurred copy of itself. Its record stores the whole
# source as its rect, so every existing reader keeps working.


def test_a_fill_window_is_stored_with_the_whole_source_as_its_rect(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", fill="blur", src_start=4.0)

    records = project.read_manifest()[ops.REFRAME_KEY]

    assert records == [
        {"clip_id": "cold-open", "rect": [0, 0, 1920, 816], "src_start": 4.0, "fill": "blur"}
    ]


def test_a_fill_window_reads_back_as_a_fill_and_keeps_the_whole_frame(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    result = ops.reframe(project.root, "cold-open", fill="blur", src_start=4.0)

    windows = _clip(result, "cold-open")["windows"]

    assert "fill" not in windows[0], "absent on a crop"
    assert windows[1]["fill"] == "blur"
    assert windows[1]["crop"] == "0,0,1920,816"
    assert windows[1]["kept"] == 1.0
    assert _clip(result, "cold-open")["reframes"] is True


def test_a_fill_at_the_head_is_a_fill_too(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    result = ops.reframe(project.root, "cold-open", fill="blur")

    assert _clip(result, "cold-open")["windows"][0]["fill"] == "blur"


def test_a_crop_replaces_a_fill_at_the_same_in_point(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", fill="blur", src_start=4.0)
    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=4.0)

    records = project.read_manifest()[ops.REFRAME_KEY]

    assert records == [{"clip_id": "cold-open", "rect": [0, 0, 459, 816], "src_start": 4.0}]


def test_fill_refuses_what_it_cannot_draw(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    with pytest.raises(ProjectError, match="no rect or pane"):
        ops.reframe(project.root, "cold-open", fill="blur", rect="0,0,459,816")
    with pytest.raises(ProjectError, match="cannot slide"):
        ops.reframe(project.root, "cold-open", fill="blur", interp=True, src_start=2.0)
    with pytest.raises(ProjectError, match="not one this build draws"):
        ops.reframe(project.root, "cold-open", fill="mirror")
    with pytest.raises(ProjectError, match="needs a clip_id"):
        ops.reframe(project.root, fill="blur")
    with pytest.raises(ProjectError, match="no picture"):
        ops.reframe(project.root, "vo", fill="blur")
    assert ops.REFRAME_KEY not in project.read_manifest()


def test_a_fill_cannot_be_slid_into_or_out_of(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    ops.reframe(project.root, "cold-open", fill="blur", src_start=2.0)
    with pytest.raises(ProjectError, match="cannot be slid out of"):
        ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=5.0, interp=True)

    ops.reframe(project.root, "cold-open", reset=True, src_start=2.0)
    ops.reframe(project.root, "cold-open", rect="0,0,459,816", src_start=8.0, interp=True)
    with pytest.raises(ProjectError, match="slides in from this one"):
        ops.reframe(project.root, "cold-open", fill="blur", src_start=6.0)


def test_a_hand_edited_fill_with_a_crop_rect_is_reported_not_rendered(project: Project) -> None:
    """Following a crop is not built, so a fill whose rect is not the whole
    source is refused where it is read — reported in the table, not guessed."""
    ops.canvas(project.root, size="1080x1920")
    manifest = project.read_manifest()
    manifest[ops.REFRAME_KEY] = [{"clip_id": "cold-open", "rect": [0, 0, 459, 816], "fill": "blur"}]
    project.write_manifest(manifest)

    entry = _clip(ops.reframe(project.root), "cold-open")

    assert entry["crop"] is None
    assert "not the whole source" in entry["error"]


def test_the_plan_writes_no_fill(project: Project) -> None:
    ops.canvas(project.root, size="1080x1920")
    result = ops.reframe(project.root, "cold-open", fill="blur", plan=True)

    assert result["written"] is False
    assert _clip(result, "cold-open")["windows"][0]["fill"] == "blur"
    assert ops.REFRAME_KEY not in project.read_manifest()
