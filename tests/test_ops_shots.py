"""`build_shots` — step 2 of the layered timeline — and the lane it feeds.

Maps the cue table (step 1, `test_ops_cues.py`) through the edit's surviving
ranges to contiguous shots. Builds a project by hand — clips, a transcript,
and a hand-written `Edit` with a real cut gap in it — the same pattern
`test_ops_speech_overlap.py` uses, so the fixture can name a cut range on
purpose rather than relying on `seed_timeline`'s auto-editor pass.

The second half of the file is step 6: what `timeline_view` reports for the
web UI's picture lane. It shares this fixture because the lane is this
projection's one consumer, and because the property worth testing is exactly
that the two do **not** agree — the lane is `build_shots` put through the MLT
writer's planner, so it refuses shots `build_shots` alone is happy with.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proofcut import mlt, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.media import MediaError
from proofcut.project import Project, ProjectError

CLIPS = {
    "vo": {
        "clip_id": "vo",
        "source": "/tmp/vo.wav",
        "duration": 4.0,
        "has_video": False,
        "has_audio": True,
    },
    "clipa": {
        "clip_id": "clipa",
        "duration": 10.0,
        "has_video": True,
        "has_audio": True,
    },
}


def _words(clip_id: str, *specs: tuple[str, float, float]) -> tx.Transcript:
    return tx.Transcript(
        clip_id=clip_id,
        words=tuple(
            tx.Word(index=i, text=text, start=start, end=end)
            for i, (text, start, end) in enumerate(specs)
        ),
    )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A VO clip cut to two surviving source ranges — [0.0, 1.0) and
    [2.0, 3.5) — with a gap at [1.0, 2.0) and nothing kept past 3.5, plus a
    registered video clip whose media file actually exists on disk and an
    `outro` card under `assets/cards/`.
    """
    project = Project.create(tmp_path / "proj")

    clip_a_media = tmp_path / "clipa.mp4"
    clip_a_media.write_bytes(b"not really a video, just needs to exist")

    manifest = project.read_manifest()
    manifest["clips"] = [CLIPS["vo"], {**CLIPS["clipa"], "source": str(clip_a_media)}]
    project.write_manifest(manifest)

    edit = tl.Edit([tl.Segment("vo", 0.0, 1.0), tl.Segment("vo", 2.0, 3.5)])
    clips_by_id = {c["clip_id"]: c for c in manifest["clips"]}
    tl.write(tl.to_otio(edit, clips_by_id, rate=1000.0, name="proj"), project.timeline_path)

    tx.save(
        _words(
            "vo",
            ("cold", 0.0, 0.3),
            ("open", 0.5, 0.8),
            ("cut1", 1.2, 1.5),  # inside the cut gap [1.0, 2.0)
            ("after", 2.2, 2.5),
            ("gap2", 3.6, 3.9),  # past the end of the second segment
            ("last", 3.0, 3.3),
            ("dup", 2.2, 2.4),  # same source time as "after", for the tie test
            ("straddle", 1.8, 2.3),  # starts in the gap, tail overlaps the next segment
        ),
        project.transcript_path("vo"),
    )

    project.cards_dir.joinpath("outro.png").write_bytes(b"\x89PNG")
    return project


def test_build_shots_maps_cues_through_the_edit_and_runs_each_to_the_next(
    project: Project,
) -> None:
    ops.cue_add(project.root, "vo", 1, "clipa")  # "open", 0.5s -> forced to frame 0
    ops.cue_add(project.root, "vo", 3, "card:outro")  # "after", 2.2s -> frame 1200
    ops.cue_add(project.root, "vo", 5, "clipa")  # "last", 3.0s -> frame 2000

    result = ops.build_shots(project.root)

    assert result["rate"] == 1000.0
    assert result["total_frames"] == 2500
    assert result["count"] == 3

    shots = result["shots"]
    assert [s["word_index"] for s in shots] == [1, 3, 5]
    assert [s["start_frame"] for s in shots] == [0, 1200, 2000]
    assert [s["frames"] for s in shots] == [1200, 800, 500]
    assert shots[0]["start"] == 0.0
    assert shots[2]["duration"] == pytest.approx(0.5)

    assert shots[0]["asset"] == "clipa"
    assert shots[0]["is_image"] is False
    assert shots[0]["asset_path"].endswith("clipa.mp4")
    assert shots[1]["asset"] == "card:outro"
    assert shots[1]["is_image"] is True
    assert Path(shots[1]["asset_path"]).as_posix().endswith("assets/cards/outro.png")


def test_build_shots_refuses_a_cue_whose_word_was_cut(project: Project) -> None:
    ops.cue_add(project.root, "vo", 2, "clipa")  # "cut1" sits inside the removed gap

    with pytest.raises(tl.TimelineError, match="was cut from the edit"):
        ops.build_shots(project.root)


def test_build_shots_survives_a_word_whose_start_is_cut_but_whose_tail_overlaps(
    project: Project,
) -> None:
    """Overlap, never containment of the word's start alone (CLAUDE.md) — the
    real Scream VO has exactly this shape at word 115, a swallowed false
    start whose *next* word's tail is the surviving take."""
    ops.cue_add(project.root, "vo", 0, "clipa")  # "cold", frame 0 — forced anyway
    ops.cue_add(project.root, "vo", 7, "card:outro")  # "straddle": 1.8-2.3, gap ends at 2.0

    result = ops.build_shots(project.root)

    assert result["count"] == 2
    # The word's own start (1.8s) sits in the cut gap; a point-containment
    # check would call this cut. The word overlaps the second segment from
    # its start (2.0s = frame 1000), which is where the surviving portion —
    # and so the shot — actually begins.
    assert result["shots"][1]["start_frame"] == 1000


def test_build_shots_refuses_a_cue_whose_word_falls_past_the_kept_material(
    project: Project,
) -> None:
    ops.cue_add(project.root, "vo", 4, "clipa")  # "gap2" is past the second segment's end

    with pytest.raises(tl.TimelineError, match="was cut from the edit"):
        ops.build_shots(project.root)


def test_build_shots_refuses_when_the_cue_table_is_empty(project: Project) -> None:
    with pytest.raises(tl.TimelineError, match="no cues yet"):
        ops.build_shots(project.root)


def test_build_shots_refuses_an_asset_naming_an_unknown_clip(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "nope")

    with pytest.raises(MediaError):
        ops.build_shots(project.root)


def test_build_shots_refuses_an_asset_clip_with_no_video(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "vo")  # itself: audio-only

    with pytest.raises(ProjectError, match="no video"):
        ops.build_shots(project.root)


def test_build_shots_refuses_an_empty_card_name(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "card:")

    with pytest.raises(ProjectError, match="names no card"):
        ops.build_shots(project.root)


def test_build_shots_refuses_a_card_that_does_not_exist_on_disk(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "card:missing")

    with pytest.raises(ProjectError, match="does not exist"):
        ops.build_shots(project.root)


def test_build_shots_refuses_two_cues_resolving_to_the_same_instant(project: Project) -> None:
    ops.cue_add(project.root, "vo", 1, "clipa")  # forced to frame 0
    ops.cue_add(project.root, "vo", 3, "card:outro")  # "after", frame 1200
    ops.cue_add(project.root, "vo", 6, "card:outro")  # "dup" — same source time as word 3

    with pytest.raises(tl.TimelineError, match="resolved to the same instant"):
        ops.build_shots(project.root)


# -- the picture lane in the view (step 6) --------------------------------
#
# `timeline_view.shots` is what the web UI's V2 lane is drawn from. Its whole
# contract is two things the lane depends on and nothing else provides: it is
# planned, not merely projected, so nothing is drawn that `export` would
# refuse; and a refusal comes back as a *message* rather than an exception,
# because the view is how a person finds the cue that needs fixing.


def test_the_view_reports_no_picture_lane_when_there_are_no_cues(project: Project) -> None:
    view = ops.timeline_view(project.root)

    assert view["shots"] is None
    assert "shots_error" not in view
    assert view["layered"] is False


def test_the_view_draws_the_lane_from_planned_shots_not_the_raw_projection(
    project: Project,
) -> None:
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.cue_add(project.root, "vo", 3, "card:outro")

    view = ops.timeline_view(project.root)

    assert view["layered"] is True
    assert view["shots_rate"] == 30.0  # export's grid, not `timebase` (1000.0 here)
    assert view["timebase"] == 1000.0
    shots = view["shots"]
    assert [s["word_index"] for s in shots] == [1, 3]
    # `src_in`/`src_start` come from `mlt.plan_picture` and from nowhere else —
    # they are the whole reason the lane goes through the planner rather than
    # reading `build_shots` directly, since a raw projection cannot say where
    # inside an asset a shot reads.
    assert [s["src_in"] for s in shots] == [0, 0]
    assert shots[0]["src_start"] == 0.0
    assert shots[1]["is_image"] is True


def test_the_lane_shows_a_reused_clip_reading_on_from_where_it_left_off(
    project: Project,
) -> None:
    """The re-use fact, which only the planner knows: a clip cued twice shows
    two different stretches of itself, and the lane is the one place a person
    can see that it does."""
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.cue_add(project.root, "vo", 3, "clipa")

    shots = ops.timeline_view(project.root)["shots"]

    assert shots[0]["src_in"] == 0
    assert shots[1]["src_in"] == shots[0]["frames"]
    assert shots[1]["src_start"] == pytest.approx(shots[0]["frames"] / 30.0)


def test_the_view_reports_a_cut_cue_instead_of_dying_on_it(project: Project) -> None:
    """The refusal a stale cue produces is reported, not raised. A view that
    raised would take down the very window a person opens to find the cue —
    and this fired twice for real on the Scream recut (PLAN.md § Build order,
    step 3)."""
    ops.cue_add(project.root, "vo", 2, "clipa")  # "cut1" sits inside the removed gap

    view = ops.timeline_view(project.root)

    assert view["shots"] is None
    assert "was cut from the edit" in view["shots_error"]
    # And the rest of the view is untouched — segments, seams and words still
    # answer, which is what makes the window usable while the cue is wrong.
    assert len(view["segments"]) == 2
    assert view["words"] is not None


def test_the_view_reports_the_writers_refusal_too_not_just_the_projections(
    project: Project,
) -> None:
    """The property that makes this a *planned* lane: `build_shots` is happy
    with a shot longer than the clip it points at, and `plan_picture` is not.
    Drawing the projection's answer would put a block on screen that `export`
    refuses to render — the same class of lie as drawing a track the renderer
    degrades. This is the real refusal from the Scream assembly (word 318's
    34.6s shot against a 30.1s clip), shrunk to fit the fixture.
    """
    manifest = project.read_manifest()
    tiny_media = project.root / "tiny.mp4"
    tiny_media.write_bytes(b"short")
    manifest["clips"].append(
        {"clip_id": "tiny", "source": str(tiny_media), "duration": 0.2, "has_video": True}
    )
    project.write_manifest(manifest)
    ops.cue_add(project.root, "vo", 1, "tiny")  # one shot, the whole 2.5s timeline

    # The projection alone is perfectly happy with it.
    assert ops.build_shots(project.root, fps=30.0)["count"] == 1

    view = ops.timeline_view(project.root)

    assert view["shots"] is None
    assert "is only" in view["shots_error"]
    assert "tiny" in view["shots_error"]


# -- the pinned cue, through the projection and onto the lane -------------
#
# `build_shots` carries the pin and decides nothing about it — the two field
# names are the point. `src_pin` is what the cue asked for; `src_start` is
# where the planner says the shot actually reads. That they agree for a
# pinned shot is the whole guarantee, and it is only a guarantee because
# `plan_picture` refuses instead of rewinding when they cannot.


def test_build_shots_carries_the_pin_without_deciding_anything_about_it(
    project: Project,
) -> None:
    ops.cue_add(project.root, "vo", 1, "clipa", src_start=5.0)
    ops.cue_add(project.root, "vo", 3, "clipa")

    shots = ops.build_shots(project.root, fps=30.0)["shots"]

    assert [s["src_pin"] for s in shots] == [5.0, None]
    # The projection says when and for how long, and nothing about where
    # inside the asset — that stays the planner's answer.
    assert "src_start" not in shots[0]
    assert "src_in" not in shots[0]


def test_the_lane_reads_a_pinned_shot_from_the_moment_its_cue_names(
    project: Project,
) -> None:
    ops.cue_add(project.root, "vo", 1, "clipa", src_start=5.0)  # forced to frame 0
    ops.cue_add(project.root, "vo", 3, "clipa")  # "after", 2.2s -> frame 66 at 30fps

    shots = ops.timeline_view(project.root)["shots"]

    # What the cue asked for and where the shot reads are the same number.
    assert shots[0]["src_pin"] == 5.0
    assert shots[0]["src_start"] == pytest.approx(5.0)
    assert shots[0]["src_in"] == 150
    # And the unpinned re-use after it carries on from where the pin ended,
    # rather than replaying the footage just shown.
    assert shots[1]["src_pin"] is None
    assert shots[1]["src_in"] == 150 + shots[0]["frames"]


def test_the_lane_reports_a_pin_that_runs_off_its_asset_rather_than_rewinding(
    project: Project,
) -> None:
    """The failure this step exists for, at the level a person sees it. The
    identical arrangement of frames without the pin is drawn happily — the
    cursor rewinds to 0, which is right for a re-use. Pinned it refuses,
    because rewinding would show the asset's opening seconds under a cue that
    says it shows the moment at 9.0s: correct pixels, wrong video.
    """
    ops.cue_add(project.root, "vo", 1, "clipa", src_start=9.0)
    ops.cue_add(project.root, "vo", 3, "card:outro")

    view = ops.timeline_view(project.root)

    assert view["shots"] is None
    assert "a pinned cue shows the moment it names" in view["shots_error"]
    # Reported, not raised: the rest of the view still answers, because this
    # window is how a person finds the cue to move.
    assert len(view["segments"]) == 2
    assert view["words"] is not None

    # The same shot, unpinned, draws without complaint.
    ops.cue_rm(project.root, "vo", 1)
    ops.cue_add(project.root, "vo", 1, "clipa")

    redrawn = ops.timeline_view(project.root)
    assert redrawn.get("shots_error") is None
    assert redrawn["shots"][0]["src_in"] == 0


# -- per-shot framing in the lane ----------------------------------------
#
# PLAN.md § Per-shot framing, step 4. The picture layer places its element at
# the shot's own `dest`, not the clip's: framing is addressed in source
# seconds, so two placements of one asset can sit under two different windows
# while the asset — the only thing `loadShot` watches — never changes.


def _wide(project: Project) -> None:
    """Give clipa a real shape and the project a vertical canvas, so there is
    something to crop and a reason to."""
    manifest = project.read_manifest()
    for clip in manifest["clips"]:
        if clip["clip_id"] == "clipa":
            clip.update({"width": 1920, "height": 816})
    project.write_manifest(manifest)
    ops.canvas(project.root, size="1080x1920")


def test_two_shots_of_one_asset_carry_their_own_windows(project: Project) -> None:
    _wide(project)
    ops.cue_add(project.root, "vo", 1, "clipa")  # reads clipa from 0.0s
    ops.cue_add(project.root, "vo", 5, "clipa")  # and again from where that left off
    ops.reframe(project.root, "clipa", rect="0,0,459,816")
    ops.reframe(project.root, "clipa", rect="1461,0,459,816", src_start=1.0)

    shots = ops.timeline_view(project.root)["shots"]

    assert [s["asset"] for s in shots] == ["clipa", "clipa"]
    assert shots[0]["src_start"] == 0.0 and shots[1]["src_start"] >= 1.0
    left = mlt.Reframe((1920, 816), (0, 0, 459, 816))
    right = mlt.Reframe((1920, 816), (1461, 0, 459, 816))
    assert shots[0]["dest"] == list(left.dest_rect((1080, 1920)))
    assert shots[1]["dest"] == list(right.dest_rect((1080, 1920)))
    # And the per-clip entry is the head window, which is the edit track's
    # answer — reading it for the picture lane is the bug this closes.
    assert ops.timeline_view(project.root)["reframe"]["clipa"]["dest"] == shots[0]["dest"]


def test_a_still_has_no_placement_because_a_card_is_never_cropped(project: Project) -> None:
    _wide(project)
    ops.cue_add(project.root, "vo", 1, "card:outro")

    shots = ops.timeline_view(project.root)["shots"]

    assert shots[0]["is_image"] is True
    assert shots[0]["dest"] is None

def test_a_split_shot_carries_both_halves(project: Project) -> None:
    """A preview reading only `dest` would place the shot at more than twice
    the render's scale and show one person where the film shows two — so the
    view carries the lower pane as well, and `dest` is already the upper one
    rather than the whole-canvas rect it is for every other shot."""
    _wide(project)
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,918,816", pane="1002,0,918,816")

    view = ops.timeline_view(project.root)
    shot = view["shots"][0]

    split = mlt.Reframe((1920, 816), (0, 0, 918, 816), panes=((0.0, (1002, 0, 918, 816)),))
    assert shot["dest"] == list(split.dest_rect_at(0.0, (1080, 1920)))
    assert shot["dest_pane"] == list(split.pane_dest_at(0.0, (1080, 1920)))
    assert shot["dest"][3] == 960 and shot["dest_pane"][3] == 960, "one pane tall each"
    assert shot["dest"][1] == 0 and shot["dest_pane"][1] == 960, "and on its own half"
    assert view["reframe"]["clipa"]["pane"] == shot["dest_pane"]


def test_an_ordinary_shot_has_no_pane(project: Project) -> None:
    """Null rather than absent: the picture layer branches on it, and a missing
    key would read as a split on a shot that is not one."""
    _wide(project)
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")

    view = ops.timeline_view(project.root)

    assert view["shots"][0]["dest_pane"] is None
    assert view["reframe"]["clipa"]["pane"] is None


def test_a_blur_filled_shot_carries_its_background(project: Project) -> None:
    """PLAN.md § Blur-fill, step 3: the preview draws the writer's own two
    rects — the contained shot as `dest`, the covering background as
    `fill.dest` — and its blur as a fraction of the width it draws, so the
    page derives none of it."""
    _wide(project)
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.reframe(project.root, "clipa", fill="blur")

    view = ops.timeline_view(project.root)
    shot = view["shots"][0]

    filled = mlt.Reframe((1920, 816), (0, 0, 1920, 816), fills=(0.0,))
    assert shot["dest"] == list(mlt.fit_rect((1920, 816), (1080, 1920)))
    assert shot["fill"] == {
        "dest": list(filled.cover_rect((1080, 1920))),
        "blur": mlt.FILL_BLUR / 1000,
        "darken": mlt.FILL_DARKEN,
    }
    assert shot["dest_pane"] is None
    assert view["reframe"]["clipa"]["fill"] == shot["fill"]


def test_an_ordinary_shot_has_no_fill(project: Project) -> None:
    _wide(project)
    ops.cue_add(project.root, "vo", 1, "clipa")
    ops.reframe(project.root, "clipa", rect="0,0,459,816")

    view = ops.timeline_view(project.root)

    assert view["shots"][0]["fill"] is None
    assert view["reframe"]["clipa"]["fill"] is None
