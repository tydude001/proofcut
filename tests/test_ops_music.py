"""`music` — the A2 bed as project state, and `_music_plan`, its derivation.

PLAN.md § The A2 music lane — the design note, built after review. Three
things pinned here, in the note's own order: the cue stores word indices and
never a length (`tail`'s read/write shape, `cue_add`'s addressing); the
resolver derives the frame span live through `Edit.timeline_span`, so a cut
before either boundary moves both and an orphaned boundary refuses by name
(`build_shots`' policy); and a bed alone tips `_is_layered`, because a
project with music recorded but routed through auto-editor renders with no
music in it at exit 0 — the silent failure the fifth trigger exists to
prevent.

Built by hand rather than through `import_media`, following
`test_ops_tail.py`: no ffprobe is needed to have a project with a shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proofcut import autoeditor, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.media import MediaError
from proofcut.project import Project, ProjectError

CLIPS = {
    "vo": {
        "clip_id": "vo",
        "source": "/tmp/vo.wav",
        "duration": 6.0,
        "has_video": False,
        "has_audio": True,
    },
    "bed": {
        "clip_id": "bed",
        "source": "/tmp/bed.wav",
        "duration": 2.0,
        "has_video": False,
        "has_audio": True,
    },
    "long-bed": {
        "clip_id": "long-bed",
        "source": "/tmp/long-bed.wav",
        "duration": 100.0,
        "has_video": False,
        "has_audio": True,
    },
}

RATE = 30.0


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
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = list(CLIPS.values())
    project.write_manifest(manifest)
    tx.save(
        _words(
            "vo",
            ("the", 0.0, 0.3),
            ("first", 0.5, 0.9),
            ("twelve", 1.0, 1.4),
            ("minutes", 1.5, 1.9),
            ("of", 2.0, 2.2),
            ("scream", 5.0, 5.4),
        ),
        project.transcript_path("vo"),
    )
    edit = tl.Edit([tl.Segment("vo", 0.0, 6.0)])
    tl.write(tl.to_otio(edit, {"vo": CLIPS["vo"]}, rate=1000.0), project.timeline_path)
    return project


def _edit_frames(edit: tl.Edit) -> int:
    return sum(frames for _, frames in autoeditor.frame_layout(edit, RATE))


# -- reading and writing ---------------------------------------------------


def test_no_arguments_reads_without_writing(project: Project) -> None:
    before = project.manifest_path.stat().st_mtime_ns
    result = ops.music(project.root)

    assert result["music"] is None
    assert result["written"] is False
    assert project.manifest_path.stat().st_mtime_ns == before
    assert ops.MUSIC_KEY not in project.read_manifest()


def test_setting_stores_the_cue_and_echoes_both_words(project: Project) -> None:
    result = ops.music(
        project.root, asset="bed", clip_id="vo", word_index_start=1, word_index_end=4
    )

    assert result["written"] is True
    assert result["music"] == {
        "asset": "bed",
        "clip_id": "vo",
        "word_index_start": 1,
        "word_index_end": 4,
        "fade_in": 0.0,
        "fade_out": 0.0,
    }
    assert project.read_manifest()[ops.MUSIC_KEY] == result["music"]
    # Anything taking a word index echoes what it resolved to (CLAUDE.md).
    assert result["start_word"]["text"] == "first"
    assert result["end_word"]["text"] == "of"
    assert [w["text"] for w in result["start_word"]["context_before"]] == ["the"]


def test_no_end_word_means_to_the_end_and_echoes_no_end(project: Project) -> None:
    result = ops.music(project.root, asset="bed", clip_id="vo", word_index_start=0)

    assert result["music"]["word_index_end"] is None
    assert result["end_word"] is None


def test_a_field_alone_updates_only_that_field(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1, fade_in=0.5)
    result = ops.music(project.root, asset="long-bed")

    assert result["music"]["asset"] == "long-bed"
    assert result["music"]["word_index_start"] == 1
    assert result["music"]["fade_in"] == 0.5


def test_clear_end_drops_the_end_word_back_to_the_hold(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1, word_index_end=4)
    result = ops.music(project.root, clear_end=True)

    assert result["music"]["word_index_end"] is None
    assert result["end_word"] is None


def test_reset_drops_the_key(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    result = ops.music(project.root, reset=True)

    assert result["music"] is None
    assert result["written"] is True
    assert ops.MUSIC_KEY not in project.read_manifest()


def test_plan_resolves_without_writing(project: Project) -> None:
    planned = ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1, plan=True)

    assert planned["music"]["asset"] == "bed"
    assert planned["start_word"]["text"] == "first"
    assert planned["written"] is False
    assert ops.MUSIC_KEY not in project.read_manifest()


# -- phrase addressing (feature: phrase-addressed cues) ----------------------
#
# The fixture transcript is "the first twelve minutes of scream" (indices
# 0-5) — the same one `test_setting_stores_the_cue_and_echoes_both_words`
# pins word_index_start=1 ("first")/word_index_end=4 ("of") against, which is
# the control these compare to. Two-or-more-word phrases throughout, so a
# wrong edge (start should bind first, end should bind last) is observable.


def test_phrase_start_and_phrase_end_resolve_to_the_same_words_as_the_index_form(
    project: Project,
) -> None:
    result = ops.music(
        project.root,
        asset="bed",
        clip_id="vo",
        phrase_start="first twelve",
        phrase_end="minutes of",
    )

    assert result["music"]["word_index_start"] == 1
    assert result["music"]["word_index_end"] == 4
    assert result["music"]["phrase_start"] == "first twelve"
    assert result["music"]["phrase_end"] == "minutes of"
    assert result["start_word"]["text"] == "first"
    assert result["end_word"]["text"] == "of"


def test_a_raw_index_can_set_the_start_while_a_phrase_sets_the_end(project: Project) -> None:
    result = ops.music(
        project.root, asset="bed", clip_id="vo", word_index_start=1, phrase_end="minutes of"
    )

    assert result["music"]["word_index_start"] == 1
    assert "phrase_start" not in result["music"]
    assert result["music"]["word_index_end"] == 4
    assert result["music"]["phrase_end"] == "minutes of"


def test_setting_the_start_by_index_clears_a_previously_stored_phrase(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", phrase_start="first twelve")
    result = ops.music(project.root, word_index_start=2)

    assert result["music"]["word_index_start"] == 2
    assert "phrase_start" not in result["music"]


def test_phrase_start_needs_a_clip_id_the_first_time(project: Project) -> None:
    with pytest.raises(ProjectError, match="clip_id"):
        ops.music(project.root, asset="bed", phrase_start="first twelve")


# -- what it refuses --------------------------------------------------------


def test_reset_and_a_field_together_are_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="not both"):
        ops.music(project.root, asset="bed", reset=True)


def test_end_word_and_clear_end_together_are_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="not both"):
        ops.music(project.root, word_index_end=4, clear_end=True)


def test_the_first_set_needs_asset_clip_and_start_together(project: Project) -> None:
    for partial in (
        {"asset": "bed"},
        {"clip_id": "vo"},
        {"word_index_start": 1},
        {"asset": "bed", "clip_id": "vo"},
    ):
        with pytest.raises(ProjectError, match="together"):
            ops.music(project.root, **partial)
    assert ops.MUSIC_KEY not in project.read_manifest()


def test_a_card_asset_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="no sound"):
        ops.music(project.root, asset="card:outro", clip_id="vo", word_index_start=1)
    assert ops.MUSIC_KEY not in project.read_manifest()


def test_an_unregistered_asset_is_refused_with_the_known_ids(project: Project) -> None:
    with pytest.raises(MediaError, match="registered"):
        ops.music(project.root, asset="nope", clip_id="vo", word_index_start=1)
    assert ops.MUSIC_KEY not in project.read_manifest()


def test_an_out_of_range_word_index_is_refused(project: Project) -> None:
    with pytest.raises(tx.TranscriptError, match="outside"):
        ops.music(project.root, asset="bed", clip_id="vo", word_index_start=99)


def test_an_end_before_the_start_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="before"):
        ops.music(
            project.root, asset="bed", clip_id="vo", word_index_start=4, word_index_end=1
        )


def test_negative_fades_are_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="negative"):
        ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1, fade_in=-0.1)


def test_a_hand_broken_manifest_key_names_the_shape(project: Project) -> None:
    manifest = project.read_manifest()
    manifest[ops.MUSIC_KEY] = {"asset": "bed"}
    project.write_manifest(manifest)

    with pytest.raises(ProjectError, match="word_index_start"):
        ops.music(project.root)


# -- the resolver -----------------------------------------------------------


def test_no_bed_resolves_to_none(project: Project) -> None:
    edit = ops._load_edit(project)
    assert ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit)) is None


def test_an_unbounded_bed_runs_from_its_word_to_the_timeline_end(project: Project) -> None:
    ops.music(project.root, asset="long-bed", clip_id="vo", word_index_start=1)
    edit = ops._load_edit(project)
    frames = _edit_frames(edit)

    plan = ops._music_plan(project, edit, RATE, edit_frames=frames)

    assert plan["start_frame"] == round(0.5 * RATE)
    assert plan["end_frame"] == frames
    assert plan["to_end"] is True
    # 100s of bed against a ~5.5s span: trimmed by frame count, no padding.
    assert plan["music_frames"] == frames - plan["start_frame"]
    assert plan["padded_frames"] == 0


def test_a_bounded_bed_runs_through_its_end_word(project: Project) -> None:
    ops.music(
        project.root, asset="long-bed", clip_id="vo", word_index_start=1, word_index_end=3
    )
    edit = ops._load_edit(project)

    plan = ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))

    assert plan["start_frame"] == round(0.5 * RATE)
    assert plan["end_frame"] == round(1.9 * RATE)
    assert plan["to_end"] is False


def test_a_short_asset_pads_rather_than_loops(project: Project) -> None:
    """The single-pass hold: 2.0s of bed under a ~5.5s span plays once and
    the lane pads out with real silence — the listen that rejected the loop
    (HISTORY.md § The three served answers) made not-looping the contract."""
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    edit = ops._load_edit(project)
    frames = _edit_frames(edit)

    plan = ops._music_plan(project, edit, RATE, edit_frames=frames)

    assert plan["music_frames"] == round(2.0 * RATE)
    assert plan["padded_frames"] == (frames - plan["start_frame"]) - round(2.0 * RATE)


def test_a_cut_before_the_start_moves_the_bed_with_it(project: Project) -> None:
    """The property everything in the note defends: no stored length, so a
    cut upstream of the bed shifts it for free, exactly like every other
    word-indexed cue."""
    ops.music(project.root, asset="long-bed", clip_id="vo", word_index_start=2)
    edit = ops._load_edit(project)
    before = ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))

    edit.remove("vo", 0.0, 0.5)  # cut ahead of word 2 ("twelve", 1.0s)
    after = ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))

    assert before["start_frame"] == round(1.0 * RATE)
    assert after["start_frame"] == round(0.5 * RATE)


def test_an_orphaned_start_word_refuses_by_name(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    edit = ops._load_edit(project)
    edit.remove("vo", 0.4, 1.0)  # removes "first" (0.5-0.9) entirely

    with pytest.raises(ProjectError, match="'first'"):
        ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))


def test_an_orphaned_end_word_refuses_by_name(project: Project) -> None:
    ops.music(
        project.root, asset="bed", clip_id="vo", word_index_start=0, word_index_end=3
    )
    edit = ops._load_edit(project)
    edit.remove("vo", 1.45, 1.95)  # removes "minutes" (1.5-1.9) entirely

    with pytest.raises(ProjectError, match="'minutes'"):
        ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))


# -- the gate ---------------------------------------------------------------


def test_a_bed_alone_makes_the_project_layered(project: Project) -> None:
    """The fifth trigger, landed in the same change as the writer's lane: a
    project with a bed recorded but still single-source-eligible would export
    through auto-editor and the render would carry no music, at exit 0."""
    edit = ops._load_edit(project)
    assert ops._is_layered(project, edit) is False

    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    assert ops._is_layered(project, edit) is True


# -- the projection ---------------------------------------------------------


def test_timeline_view_has_no_music_without_a_bed(project: Project) -> None:
    view = ops.timeline_view(project.root)
    assert view["music"] is None
    assert "music_error" not in view


def test_timeline_view_projects_the_resolved_bed(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    view = ops.timeline_view(project.root)

    assert view["music"]["asset"] == "bed"
    assert view["music"]["timeline_start"] == pytest.approx(0.5, abs=1e-6)
    assert view["music"]["to_end"] is True
    assert "music_error" not in view
    assert view["layered"] is True


def test_timeline_view_reports_an_unresolvable_bed_as_music_error(project: Project) -> None:
    """`shots_error`'s policy: a stale cue must not take the whole view down
    with it, because the view is how a person finds the cue to fix."""
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    ops.cut_by_transcript(project.root, "vo", cut=[[1, 1]])  # cut "first", the bed's start word

    view = ops.timeline_view(project.root)

    assert view["music"] is None
    assert "first" in view["music_error"]
    assert view["segments"], "the rest of the view still renders"


# -- the fades ----------------------------------------------------------------


def test_fade_frames_are_derived_on_the_export_rate(project: Project) -> None:
    """Seconds in the manifest, frames in the plan — converted here, once,
    on the same grid the writer builds at."""
    ops.music(
        project.root, asset="bed", clip_id="vo", word_index_start=1,
        fade_in=0.5, fade_out=1.0,
    )
    edit = ops._load_edit(project)

    plan = ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))

    assert plan["fade_in_frames"] == round(0.5 * RATE)
    assert plan["fade_out_frames"] == round(1.0 * RATE)


def test_fades_that_outgrow_the_bed_refuse_with_the_fix_named(project: Project) -> None:
    """A cut upstream can shrink the bed under fades that used to fit — a
    decision point, never a quiet clamp. The bed is 2.0s of asset here, so
    1.5 + 1.0 of fade cannot fit its audible span."""
    ops.music(
        project.root, asset="bed", clip_id="vo", word_index_start=1,
        fade_in=1.5, fade_out=1.0,
    )
    edit = ops._load_edit(project)

    with pytest.raises(ProjectError, match="shorten the fades"):
        ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))


def test_timeline_view_carries_the_fade_frames(project: Project) -> None:
    """The view states the fade the render will carry (writer units), beside
    the seconds the manifest asked for — the lane draws from these."""
    ops.music(
        project.root, asset="bed", clip_id="vo", word_index_start=1,
        fade_in=0.5, fade_out=0.5,
    )
    view = ops.timeline_view(project.root)

    assert view["music"]["fade_in"] == pytest.approx(0.5)
    assert view["music"]["fade_in_frames"] == round(0.5 * RATE)
    assert view["music"]["fade_out_frames"] == round(0.5 * RATE)


def test_oversized_fades_surface_as_music_error_not_an_exception(project: Project) -> None:
    ops.music(
        project.root, asset="bed", clip_id="vo", word_index_start=1,
        fade_in=1.5, fade_out=1.0,
    )
    view = ops.timeline_view(project.root)

    assert view["music"] is None
    assert "shorten the fades" in view["music_error"]
    assert view["segments"], "the rest of the view still renders"


# -- passages, rotation and level (docs/plans/NATIVE.md § A1) ------------------


def _film_project(tmp_path: Path, *, vo_seconds: float = 100.0) -> Project:
    """A 100 s VO with a word every ten seconds, and v10's three calm passages
    at their real lengths — 34, 34 and 36 s."""
    project = Project.create(tmp_path / "film")
    clips = [
        {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": vo_seconds, "has_video": False, "has_audio": True},
        {"clip_id": "calm-a", "source": "/tmp/a.wav", "duration": 34.0, "has_video": False, "has_audio": True},
        {"clip_id": "calm-b", "source": "/tmp/b.wav", "duration": 34.0, "has_video": False, "has_audio": True},
        {"clip_id": "calm-c", "source": "/tmp/c.wav", "duration": 36.0, "has_video": False, "has_audio": True},
    ]
    manifest = project.read_manifest()
    manifest["clips"] = clips
    project.write_manifest(manifest)
    tx.save(
        _words("vo", *[(f"w{k}", 10.0 * k, 10.0 * k + 0.4) for k in range(10)]),
        project.transcript_path("vo"),
    )
    edit = tl.Edit([tl.Segment("vo", 0.0, vo_seconds)])
    tl.write(tl.to_otio(edit, {"vo": clips[0]}, rate=1000.0), project.timeline_path)
    return project


def _pieces(project: Project) -> list[tuple[str, float, float, int]]:
    edit = ops._load_edit(project)
    plan = ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))
    return [
        (p["asset"], p["start_frame"] / RATE, (p["start_frame"] + p["frames"]) / RATE, p["lane"])
        for p in plan["pieces"]
    ]


def test_a_rotation_tiles_the_bed_the_way_v10s_mix_did(tmp_path: Path) -> None:
    """goodsometimes `mix_longlegs_v10.sh` laid its bed at 0.00, 31.50, 63.00
    and 96.50 — each 34/34/36 s pass overlapping the last by 2.5 s. The same
    arrangement as project state lands on the same seconds, alternating lanes
    because every junction overlaps."""
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0,
              rotate=["calm-b", "calm-c"], crossfade=2.5)

    pieces = _pieces(project)

    assert [(a, round(s, 2)) for a, s, _, _ in pieces] == [
        ("calm-a", 0.0), ("calm-b", 31.5), ("calm-c", 63.0), ("calm-a", 96.5)
    ]
    assert [lane for *_, lane in pieces] == [0, 1, 0, 1]
    assert pieces[-1][2] == pytest.approx(100.0)


def test_a_passage_starts_at_its_word_from_its_in_point_and_crossfades_in(tmp_path: Path) -> None:
    """Scream v8's shape: a second cue placed at a section turn, from its own
    in-point, the first running on past the turn by the crossfade."""
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, src_in=2.6,
              passages=[{"asset": "calm-c", "phrase_start": "w3", "src_in": 1.0, "crossfade": 4.0}])
    edit = ops._load_edit(project)
    plan = ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))
    first, second = plan["pieces"]

    assert first["src_in_frames"] == round(2.6 * RATE)
    # calm-a has 31.4 s left from 2.6, so it ends before word 3 (30 s) + 4 s.
    assert first["frames"] == round(31.4 * RATE)
    assert second["start_frame"] == round(30.0 * RATE)
    assert second["src_in_frames"] == round(1.0 * RATE)
    assert second["fade_in_frames"] == first["start_frame"] + first["frames"] - second["start_frame"]
    assert (first["lane"], second["lane"]) == (0, 1)


def test_a_passage_before_the_one_ahead_of_it_is_refused(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=5)
    manifest = project.read_manifest()
    manifest[ops.MUSIC_KEY]["passages"] = [{"asset": "calm-b", "word_index_start": 2}]
    project.write_manifest(manifest)
    edit = ops._load_edit(project)

    with pytest.raises(ProjectError, match="not after the passage before it"):
        ops._music_plan(project, edit, RATE, edit_frames=_edit_frames(edit))


def test_passage_phrases_resolve_forward_and_echo_their_words(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    result = ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0,
                       passages=[{"asset": "calm-b", "phrase_start": "w4"}])

    assert result["music"]["passages"] == [{"asset": "calm-b", "word_index_start": 4, "phrase_start": "w4"}]
    assert result["passage_words"][0]["text"] == "w4"


def test_a_bed_using_no_arrangement_stores_exactly_what_it_did_before(project: Project) -> None:
    result = ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)

    assert set(result["music"]) == {"asset", "clip_id", "word_index_start", "word_index_end", "fade_in", "fade_out"}


def test_clearing_passages_and_rotation(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, rotate=["calm-b"],
              passages=[{"asset": "calm-c", "word_index_start": 5}], under=22.0)
    result = ops.music(project.root, rotate=[], passages=[], clear_under=True)

    assert "rotate" not in result["music"] and "passages" not in result["music"]
    assert "under" not in result["music"]


def test_an_unregistered_passage_asset_is_refused(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    with pytest.raises(MediaError):
        ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0,
                  passages=[{"asset": "nope", "word_index_start": 3}])


def test_a_rotation_asset_cannot_be_removed_while_the_bed_plays_it(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, rotate=["calm-c"])

    with pytest.raises(ProjectError, match="music bed"):
        ops.clip_rm(project.root, "calm-c")


def test_timeline_view_carries_every_piece(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0,
              rotate=["calm-b", "calm-c"], crossfade=2.5)

    view = ops.timeline_view(project.root)

    assert [p["asset"] for p in view["music"]["pieces"]] == ["calm-a", "calm-b", "calm-c", "calm-a"]
    assert view["music"]["pieces"][1]["timeline_start"] == pytest.approx(31.5)


def test_a_crossfading_bed_leaves_the_picture_lane_its_footage(tmp_path: Path) -> None:
    """The bed's lane loop in `_build_mlt` once bound `lane` — the picture
    lane's own name — to its music lanes, so a bed with a second lane handed
    the picture lane its music: every render of Scream and Lambs/Longlegs on
    A1 was black under correct audio, at exit 0 (HISTORY.md § The Scream
    native rebuild)."""
    project = _film_project(tmp_path)
    film = tmp_path / "film.mp4"
    film.write_bytes(b"")  # the planner asks only that the footage exists
    manifest = project.read_manifest()
    manifest["clips"].append(
        {"clip_id": "film", "source": str(film), "duration": 120.0, "has_video": True,
         "has_audio": True, "width": 1920, "height": 816, "fps": 30.0}
    )
    project.write_manifest(manifest)
    ops.cue_add(project.root, "vo", 0, "film")
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0,
              rotate=["calm-b", "calm-c"], crossfade=2.5)

    built = ops._build_mlt(project, ops._load_edit(project), fps=RATE)

    picture = [
        node.find("property[@name='resource']").text
        for node in built["document"].findall("chain")
        if (node.get("id") or "").startswith("vchain")
    ]
    assert picture and all(resource.endswith("film.mp4") for resource in picture), picture


# -- duck: the bed down under the voice, up in its pauses ----------------------


def test_a_duck_is_stored_on_the_bed_and_cleared_by_name(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, under=17.5)

    set_ = ops.music(project.root, duck=8.0)
    assert set_["music"]["duck"] == 8.0 and set_["music"]["under"] == 17.5
    assert ops._stored_music(project)["duck"] == 8.0

    cleared = ops.music(project.root, clear_duck=True)
    assert "duck" not in cleared["music"]
    assert ops._stored_music(project)["duck"] is None


def test_a_bed_stored_before_the_duck_reads_as_undocked(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    assert ops._stored_music(project)["duck"] is None


@pytest.mark.parametrize("depth", [0.0, -3.0, 60.0, float("nan")])
def test_a_duck_that_is_not_a_depth_is_refused(tmp_path: Path, depth: float) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0)

    with pytest.raises(ProjectError, match="duck"):
        ops.music(project.root, duck=depth)
    assert "duck" not in ops._stored_music(project) or ops._stored_music(project)["duck"] is None


def test_a_planned_duck_writes_nothing(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0)

    result = ops.music(project.root, duck=6.0, plan=True)
    assert result["music"]["duck"] == 6.0 and not result["written"]
    assert ops._stored_music(project)["duck"] is None


# -- the bed's fixed level, and the bed under the tail --------------------------


def test_loudness_and_under_replace_each_other(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, under=17.5)

    loud = ops.music(project.root, loudness=-23.0)
    assert loud["music"]["loudness"] == -23.0 and "under" not in loud["music"]
    under = ops.music(project.root, under=12.0)
    assert under["music"]["under"] == 12.0 and "loudness" not in under["music"]
    ops.music(project.root, loudness=-23.0)
    cleared = ops.music(project.root, clear_loudness=True)
    assert "loudness" not in cleared["music"]
    assert ops._stored_music(project)["loudness"] is None


def test_loudness_and_under_together_are_refused(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    with pytest.raises(ProjectError, match="not both"):
        ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, under=10.0, loudness=-23.0)


@pytest.mark.parametrize("lufs", [0.0, 3.0, -80.0, float("nan")])
def test_a_loudness_that_is_not_lufs_is_refused(tmp_path: Path, lufs: float) -> None:
    project = _film_project(tmp_path)
    with pytest.raises(ProjectError, match="LUFS"):
        ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, loudness=lufs)


def test_over_tail_runs_the_bed_on_under_the_tail(tmp_path: Path) -> None:
    project = _film_project(tmp_path, vo_seconds=30.0)
    project.cards_dir.joinpath("end.png").write_bytes(b"\x89PNG")
    ops.tail(project.root, asset="card:end", seconds=4.0)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0)
    edit = ops._load_edit(project)
    frames = _edit_frames(edit)

    before = ops._music_plan(project, edit, RATE, edit_frames=frames)
    result = ops.music(project.root, over_tail=True)
    after = ops._music_plan(project, edit, RATE, edit_frames=frames)

    assert result["music"]["over_tail"] is True
    assert before["end_frame"] == frames
    assert after["end_frame"] == frames + round(4.0 * RATE)
    assert after["timeline_end"] == pytest.approx(34.0)
    off = ops.music(project.root, over_tail=False)
    assert "over_tail" not in off["music"]


def test_a_bed_stored_before_over_tail_ends_with_the_edit(project: Project) -> None:
    ops.music(project.root, asset="bed", clip_id="vo", word_index_start=1)
    assert ops._stored_music(project)["over_tail"] is False
    assert ops._stored_music(project)["loudness"] is None


def test_over_tail_on_a_bed_with_an_end_is_refused(tmp_path: Path) -> None:
    project = _film_project(tmp_path)
    ops.music(project.root, asset="calm-a", clip_id="vo", word_index_start=0, word_index_end=5)

    with pytest.raises(ProjectError, match="clear_end"):
        ops.music(project.root, over_tail=True)
