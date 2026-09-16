"""Track surgery: subtracting source intervals, and the OTIO round trip.

OTIO's own edit algorithms are C++ only (CLAUDE.md), so this arithmetic is
hand-rolled and is exactly the part that has to be pinned down by tests.
"""

from __future__ import annotations

import opentimelineio as otio
import pytest

from proofcut import timeline as tl
from proofcut.timeline import EPSILON, Edit, Segment, TimelineError, from_otio, to_otio

CLIPS = {
    "vo": {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 60.0, "has_video": False, "has_audio": True},
    "cam": {"clip_id": "cam", "source": "/tmp/cam.mp4", "duration": 60.0, "has_video": True, "has_audio": True},
}


def _edit(*spans: tuple[float, float], clip_id: str = "vo") -> Edit:
    return Edit([Segment(clip_id, a, b) for a, b in spans])


def _spans(edit: Edit) -> list[tuple[float, float]]:
    return [(s.start, s.end) for s in edit.segments]


# -- removal -------------------------------------------------------------


def test_removing_the_middle_splits_a_segment() -> None:
    edit = _edit((0.0, 10.0))
    assert edit.remove("vo", 4.0, 6.0) == 1
    assert _spans(edit) == [(0.0, 4.0), (6.0, 10.0)]


def test_removing_a_whole_segment_drops_it() -> None:
    edit = _edit((0.0, 4.0), (5.0, 9.0))
    edit.remove("vo", 5.0, 9.0)
    assert _spans(edit) == [(0.0, 4.0)]


def test_removal_spanning_segments_trims_both_and_drops_the_middle() -> None:
    edit = _edit((0.0, 4.0), (5.0, 9.0), (10.0, 14.0))
    assert edit.remove("vo", 3.0, 11.0) == 3
    assert _spans(edit) == [(0.0, 3.0), (11.0, 14.0)]


def test_removal_ripples_so_the_hole_closes() -> None:
    edit = _edit((0.0, 4.0), (5.0, 9.0))
    edit.remove("vo", 1.0, 2.0)
    # Total duration drops by exactly the removed span; nothing is left behind.
    assert edit.duration == pytest.approx(7.0)


def test_removal_leaves_other_clips_alone() -> None:
    edit = Edit([Segment("vo", 0.0, 10.0), Segment("cam", 0.0, 10.0)])
    edit.remove("vo", 0.0, 10.0)
    assert _spans(edit) == [(0.0, 10.0)]
    assert edit.segments[0].clip_id == "cam"


def test_removing_an_already_cut_range_is_a_no_op() -> None:
    """An agent retrying a cut must not damage the timeline."""
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    assert edit.remove("vo", 4.0, 6.0) == 0
    assert _spans(edit) == [(0.0, 4.0), (6.0, 10.0)]


def test_removal_rejects_an_empty_or_backwards_interval() -> None:
    edit = _edit((0.0, 10.0))
    with pytest.raises(TimelineError, match="empty or backwards"):
        edit.remove("vo", 5.0, 5.0)
    with pytest.raises(TimelineError, match="empty or backwards"):
        edit.remove("vo", 6.0, 5.0)


def test_slivers_below_the_floor_are_dropped() -> None:
    edit = _edit((0.0, 10.0))
    edit.remove("vo", 0.0, 9.9999)
    assert _spans(edit) == []


# -- keep ----------------------------------------------------------------


def test_keep_only_retains_the_requested_intervals() -> None:
    edit = _edit((0.0, 10.0))
    edit.keep_only("vo", [(1.0, 2.0), (5.0, 6.0)])
    assert _spans(edit) == [(1.0, 2.0), (5.0, 6.0)]


def test_keep_only_clips_to_what_is_still_on_the_timeline() -> None:
    """Asking to keep something already cut yields nothing, not a resurrection."""
    edit = _edit((0.0, 4.0))
    edit.keep_only("vo", [(2.0, 8.0)])
    assert _spans(edit) == [(2.0, 4.0)]


def test_keep_only_merges_overlapping_intervals() -> None:
    edit = _edit((0.0, 10.0))
    edit.keep_only("vo", [(1.0, 4.0), (3.0, 6.0)])
    assert _spans(edit) == [(1.0, 6.0)]


def test_keep_only_leaves_other_clips_alone() -> None:
    edit = Edit([Segment("vo", 0.0, 10.0), Segment("cam", 0.0, 10.0)])
    edit.keep_only("vo", [(1.0, 2.0)])
    assert [(s.clip_id, s.start, s.end) for s in edit.segments] == [
        ("vo", 1.0, 2.0),
        ("cam", 0.0, 10.0),
    ]


def test_keep_only_needs_an_interval() -> None:
    with pytest.raises(TimelineError, match="at least one interval"):
        _edit((0.0, 10.0)).keep_only("vo", [])


# -- gaps and restore ------------------------------------------------------


def test_gaps_reports_an_interior_hole_and_head_and_tail() -> None:
    edit = _edit((10.0, 20.0), (25.0, 30.0))
    assert edit.gaps("vo", 40.0) == [(0.0, 10.0), (20.0, 25.0), (30.0, 40.0)]


def test_gaps_of_a_fully_present_clip_is_empty() -> None:
    edit = _edit((0.0, 10.0))
    assert edit.gaps("vo", 10.0) == []


def test_gaps_of_an_entirely_absent_clip_is_the_whole_duration() -> None:
    edit = Edit([Segment("cam", 0.0, 10.0)])
    assert edit.gaps("vo", 10.0) == [(0.0, 10.0)]


def test_restore_of_a_full_interior_gap_merges_the_neighbours_into_one_segment() -> None:
    edit = _edit((0.0, 10.0), (20.0, 30.0))
    pieces = edit.restore("vo", 10.0, 20.0, duration=30.0)
    assert pieces == [(10.0, 20.0)]
    assert _spans(edit) == [(0.0, 30.0)]
    assert len(edit.segments) == 1


def test_restore_partially_overlapping_a_gap_only_restores_the_overlap() -> None:
    edit = _edit((0.0, 10.0), (20.0, 30.0))
    pieces = edit.restore("vo", 12.0, 15.0, duration=30.0)
    assert pieces == [(12.0, 15.0)]
    assert _spans(edit) == [(0.0, 10.0), (12.0, 15.0), (20.0, 30.0)]


def test_restore_spanning_two_gaps_restores_both_as_separate_pieces() -> None:
    edit = _edit((0.0, 10.0), (20.0, 30.0), (40.0, 50.0))
    pieces = edit.restore("vo", 5.0, 45.0, duration=50.0)
    assert pieces == [(10.0, 20.0), (30.0, 40.0)]
    assert _spans(edit) == [(0.0, 50.0)]
    assert len(edit.segments) == 1


def test_restore_of_a_head_gap_merges_into_the_first_segment() -> None:
    edit = _edit((10.0, 30.0))
    pieces = edit.restore("vo", 0.0, 10.0, duration=30.0)
    assert pieces == [(0.0, 10.0)]
    assert _spans(edit) == [(0.0, 30.0)]


def test_restore_of_a_tail_gap_merges_into_the_last_segment() -> None:
    edit = _edit((0.0, 20.0))
    pieces = edit.restore("vo", 20.0, 30.0, duration=30.0)
    assert pieces == [(20.0, 30.0)]
    assert _spans(edit) == [(0.0, 30.0)]


def test_restore_of_already_present_material_is_a_no_op() -> None:
    edit = _edit((0.0, 10.0), (20.0, 30.0))
    pieces = edit.restore("vo", 0.0, 10.0, duration=30.0)
    assert pieces == []
    assert _spans(edit) == [(0.0, 10.0), (20.0, 30.0)]


def test_restore_leaves_other_clips_alone() -> None:
    edit = Edit([Segment("vo", 0.0, 10.0), Segment("cam", 0.0, 10.0)])
    pieces = edit.restore("vo", 10.0, 20.0, duration=20.0)
    assert pieces == [(10.0, 20.0)]
    clip_ids = [s.clip_id for s in edit.segments]
    assert clip_ids.count("cam") == 1
    assert clip_ids.count("vo") == 1


def test_restore_raises_when_the_clip_has_no_surviving_segment() -> None:
    edit = Edit([Segment("cam", 0.0, 10.0)])
    with pytest.raises(TimelineError, match="nothing of it left to splice"):
        edit.restore("vo", 0.0, 5.0, duration=10.0)


def test_restore_raises_when_the_clips_segments_are_interleaved() -> None:
    edit = Edit(
        [Segment("vo", 0.0, 5.0), Segment("cam", 0.0, 5.0), Segment("vo", 5.0, 10.0)]
    )
    with pytest.raises(TimelineError, match="not contiguous"):
        edit.restore("vo", 4.0, 6.0, duration=10.0)


# -- insert (vo_extend) -----------------------------------------------------


def test_insert_splits_a_segment_at_an_interior_instant() -> None:
    edit = _edit((0.0, 10.0))
    edit.insert("vo", 4.0, "hold", 0.0, 2.0)
    assert [(s.clip_id, s.start, s.end) for s in edit.segments] == [
        ("vo", 0.0, 4.0),
        ("hold", 0.0, 2.0),
        ("vo", 4.0, 10.0),
    ]
    assert edit.duration == pytest.approx(12.0)


def test_insert_at_a_segment_end_needs_no_split() -> None:
    edit = _edit((0.0, 4.0), (4.0, 10.0))
    edit.insert("vo", 4.0, "hold", 0.0, 2.0)
    assert [(s.clip_id, s.start, s.end) for s in edit.segments] == [
        ("vo", 0.0, 4.0),
        ("hold", 0.0, 2.0),
        ("vo", 4.0, 10.0),
    ]


def test_insert_at_the_very_start_prepends() -> None:
    edit = _edit((0.0, 10.0))
    edit.insert("vo", 0.0, "hold", 0.0, 2.0)
    assert [(s.clip_id, s.start, s.end) for s in edit.segments] == [
        ("hold", 0.0, 2.0),
        ("vo", 0.0, 10.0),
    ]


def test_insert_at_the_very_end_appends() -> None:
    edit = _edit((0.0, 10.0))
    edit.insert("vo", 10.0, "hold", 0.0, 2.0)
    assert [(s.clip_id, s.start, s.end) for s in edit.segments] == [
        ("vo", 0.0, 10.0),
        ("hold", 0.0, 2.0),
    ]


def test_insert_picks_the_earlier_segment_on_a_shared_boundary() -> None:
    """A boundary an earlier cut left behind: `at` sits at both the end of one
    segment and the start of the next. The hold goes right after the first,
    never before the second — the same segment `first_overlapping`'s
    `ends`-inclusive walk would land on."""
    edit = _edit((0.0, 4.0), (4.0, 10.0))
    edit.insert("vo", 4.0, "hold", 0.0, 2.0)
    assert [(s.clip_id, s.start, s.end) for s in edit.segments] == [
        ("vo", 0.0, 4.0),
        ("hold", 0.0, 2.0),
        ("vo", 4.0, 10.0),
    ]


def test_insert_leaves_other_clips_alone() -> None:
    edit = Edit([Segment("vo", 0.0, 10.0), Segment("cam", 0.0, 10.0)])
    edit.insert("vo", 5.0, "hold", 0.0, 2.0)
    clip_ids = [s.clip_id for s in edit.segments]
    assert clip_ids == ["vo", "hold", "vo", "cam"]


def test_insert_refuses_a_backwards_or_empty_new_range() -> None:
    edit = _edit((0.0, 10.0))
    with pytest.raises(TimelineError, match="empty or backwards"):
        edit.insert("vo", 4.0, "hold", 2.0, 2.0)
    with pytest.raises(TimelineError, match="empty or backwards"):
        edit.insert("vo", 4.0, "hold", 3.0, 2.0)


def test_insert_refuses_an_instant_that_is_not_on_the_timeline() -> None:
    """A cut source instant (in a gap) has no segment to split — `vo_extend`
    is "open a gap after this word", not "resurrect a cut one"."""
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    with pytest.raises(TimelineError, match="not on the timeline"):
        edit.insert("vo", 5.0, "hold", 0.0, 2.0)


def test_insert_refuses_a_clip_with_no_surviving_segment() -> None:
    edit = _edit((0.0, 10.0), clip_id="cam")
    with pytest.raises(TimelineError, match="not on the timeline"):
        edit.insert("vo", 5.0, "hold", 0.0, 2.0)


def test_insert_then_restore_across_it_is_refused() -> None:
    """§ `vo_extend` — the design note's item 1: `restore`'s existing
    interleaved-segments check catches a hold by construction, needing no
    change of its own."""
    edit = _edit((0.0, 10.0))
    edit.remove("vo", 2.0, 3.0)
    edit.insert("vo", 6.0, "hold", 0.0, 2.0)
    with pytest.raises(TimelineError, match="not contiguous"):
        edit.restore("vo", 2.0, 3.0, duration=10.0)


def test_insert_then_export_routing_sees_two_clip_ids() -> None:
    """§ `vo_extend` — the design note's item 2: once a hold lands, the
    timeline holds more than one clip_id, which is `_is_layered`'s own test
    in ops.py — nothing in `Edit` has to say so itself, but the segment list
    it produces is what that test reads."""
    edit = _edit((0.0, 10.0))
    edit.insert("vo", 5.0, "hold", 0.0, 2.0)
    assert len({s.clip_id for s in edit.segments}) == 2


# -- addressing ----------------------------------------------------------


def test_timeline_time_accounts_for_earlier_cuts() -> None:
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    assert edit.timeline_time("vo", 1.0) == pytest.approx(1.0)
    # Source 7.0 sits 1s into the second segment, which starts at timeline 4.0.
    assert edit.timeline_time("vo", 7.0) == pytest.approx(5.0)


def test_timeline_time_is_none_for_cut_material() -> None:
    """Reporting the cut honestly beats silently pointing somewhere else."""
    assert _edit((0.0, 4.0), (6.0, 10.0)).timeline_time("vo", 5.0) is None


def test_timeline_time_excludes_a_segments_own_end_by_default() -> None:
    """Segments are half-open, and every range caller depends on that."""
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    # 4.0 is the first segment's exclusive end and has been cut away.
    assert edit.timeline_time("vo", 4.0) is None
    # 10.0 is the whole timeline's end — one past the last playable instant.
    assert edit.timeline_time("vo", 10.0) is None


def test_closed_end_locates_an_instant_sitting_on_a_segment_boundary() -> None:
    """The zero-width-word case: whisper's last word lands on the last
    segment's end, and the half-open test calls plainly-present material cut.
    """
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    # The timeline's own end, which is where a final zero-width word sits.
    assert edit.timeline_time("vo", 10.0, closed_end=True) == pytest.approx(8.0)
    # An interior seam resolves to the outgoing segment's end, not the
    # incoming one's start — they are the same timeline instant either way.
    assert edit.timeline_time("vo", 4.0, closed_end=True) == pytest.approx(4.0)
    # It does not resurrect material from the middle of a cut.
    assert edit.timeline_time("vo", 5.0, closed_end=True) is None


def test_source_at_returns_the_playing_clip_and_source_time() -> None:
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    assert edit.source_at(1.0) == ("vo", 1.0)
    # Timeline 5.0 sits 1s into the second segment, which starts at source 6.0.
    assert edit.source_at(5.0) == ("vo", 7.0)
    # The very last instant on the timeline still resolves, at the last
    # segment's own end.
    assert edit.source_at(8.0) == ("vo", 10.0)


def test_source_at_returns_none_past_the_end() -> None:
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    assert edit.source_at(8.001) is None
    assert edit.source_at(-1.0) is None


def test_source_at_is_the_inverse_of_timeline_time() -> None:
    """Documents the pairing explicitly rather than leaving it implicit."""
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    for source_time in (0.5, 3.9, 6.0, 9.999):
        timeline_time = edit.timeline_time("vo", source_time)
        assert timeline_time is not None
        clip_id, back = edit.source_at(timeline_time)
        assert clip_id == "vo"
        assert back == pytest.approx(source_time)


def test_covers_measures_surviving_overlap() -> None:
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    assert edit.covers("vo", 3.0, 7.0) == pytest.approx(2.0)
    assert edit.covers("vo", 4.0, 6.0) == pytest.approx(0.0)


def test_a_cut_word_does_not_survive_as_float_noise_after_a_save() -> None:
    """The launch clip's "In In July": a cut from a word's own start, saved.

    Whisper stored the first word's start as 0.6199999999999994. The cut
    removed from there, and `to_otio` wrote the kept head's end on the project's
    millisecond grid, which reads back as 0.62 — so the word overlapped the head
    by 6e-16 s and was reported present: drawn unstruck in the transcript and
    burned into the captions as a zero-length duplicate of the retake's "In".
    """
    edit = _edit((0.0, 23.24))
    edit.remove("vo", 0.6199999999999994, 9.64)
    saved = from_otio(to_otio(edit, CLIPS, rate=1000.0))
    assert _spans(saved)[0][1] == 0.62

    assert saved.timeline_span("vo", 0.6199999999999994, 1.18) is None
    assert saved.covers("vo", 0.6199999999999994, 1.18) == 0.0
    assert saved.timeline_spans("vo", 0.6199999999999994, 1.18) == []
    # The kept retake's own first word is untouched, and a range reaching past
    # the noise into it resolves to it rather than to the head it grazed.
    assert saved.timeline_span("vo", 9.64, 10.2) == pytest.approx((0.62, 1.18))
    assert saved.timeline_span("vo", 0.6199999999999994, 10.2) == pytest.approx((0.62, 1.18))


def test_source_spans_matches_a_simple_offset_inside_one_segment() -> None:
    edit = _edit((10.0, 20.0))
    assert edit.source_spans(2.0, 5.0) == [("vo", 12.0, 15.0)]


def test_source_spans_splits_across_a_ripple_closed_seam() -> None:
    """A cut in the middle closes the timeline; a render-time range straddling
    where the seam now sits must come back as two pieces of the same clip,
    non-adjacent in source time, that together cover exactly the request.
    """
    edit = _edit((0.0, 4.0), (6.0, 10.0))  # source 4.0-6.0 already cut
    pieces = edit.source_spans(3.0, 5.0)

    assert [p[0] for p in pieces] == ["vo", "vo"]
    assert pieces[0][1:] == pytest.approx((3.0, 4.0))
    assert pieces[1][1:] == pytest.approx((6.0, 7.0))
    assert sum(b - a for _, a, b in pieces) == pytest.approx(2.0)


def test_source_spans_crosses_a_clip_boundary() -> None:
    """Representable now even though `seed_timeline` doesn't build it yet."""
    edit = Edit([Segment("vo", 0.0, 4.0), Segment("cam", 0.0, 4.0)])
    pieces = edit.source_spans(3.0, 5.0)

    assert [p[0] for p in pieces] == ["vo", "cam"]
    assert pieces[0][1:] == pytest.approx((3.0, 4.0))
    assert pieces[1][1:] == pytest.approx((0.0, 1.0))


def test_source_spans_rejects_past_the_end() -> None:
    edit = _edit((0.0, 10.0))
    with pytest.raises(TimelineError, match="outside the timeline"):
        edit.source_spans(8.0, 11.0)


def test_source_spans_rejects_empty_or_backwards() -> None:
    edit = _edit((0.0, 10.0))
    with pytest.raises(TimelineError, match="empty or backwards"):
        edit.source_spans(5.0, 5.0)
    with pytest.raises(TimelineError, match="empty or backwards"):
        edit.source_spans(6.0, 5.0)


def test_source_spans_rejects_negative_start() -> None:
    edit = _edit((0.0, 10.0))
    with pytest.raises(TimelineError, match="outside the timeline"):
        edit.source_spans(-1.0, 5.0)


def test_source_spans_zero_length_at_the_very_end_is_still_refused() -> None:
    """Pinning the "zero-length never allowed" decision, rather than leaving
    it to fall out of the `end<=start` check by accident.
    """
    edit = _edit((0.0, 10.0))
    with pytest.raises(TimelineError, match="empty or backwards"):
        edit.source_spans(10.0, 10.0)


# -- OTIO ----------------------------------------------------------------


def test_otio_round_trip_preserves_segments() -> None:
    edit = _edit((1.5, 4.25), (10.0, 12.5))
    restored = from_otio(to_otio(edit, CLIPS, rate=1000))
    assert _spans(restored) == _spans(edit)
    assert [s.clip_id for s in restored.segments] == ["vo", "vo"]


def test_otio_track_kind_follows_the_media() -> None:
    audio = to_otio(_edit((0.0, 1.0)), CLIPS, rate=1000)
    video = to_otio(_edit((0.0, 1.0), clip_id="cam"), CLIPS, rate=30)
    assert audio.tracks[0].kind == "Audio"
    assert video.tracks[0].kind == "Video"


def test_otio_rejects_an_unregistered_clip() -> None:
    with pytest.raises(TimelineError, match="unregistered clip"):
        to_otio(_edit((0.0, 1.0), clip_id="ghost"), CLIPS, rate=30)


def test_from_otio_rejects_a_foreign_timeline() -> None:
    """Without proofcut metadata there is no clip_id, and guessing one is worse."""
    timeline = to_otio(_edit((0.0, 1.0)), CLIPS, rate=1000)
    del timeline.tracks[0][0].metadata["proofcut"]
    with pytest.raises(TimelineError, match="not written by proofcut"):
        from_otio(timeline)


# -- the metadata key across the rename (docs/plans/RENAME.md, decision 2) --


def _stamped(timeline: otio.schema.Timeline) -> list[otio.core.SerializableObject]:
    """Every object `to_otio` stamps: the timeline and each clip."""
    return [timeline, *(item for item in timeline.tracks[0] if isinstance(item, otio.schema.Clip))]


def _as_pre_rename(timeline: otio.schema.Timeline) -> otio.schema.Timeline:
    """Re-key a timeline the way a lucid-era `to_otio` wrote it."""
    for item in _stamped(timeline):
        item.metadata["lucid"] = item.metadata["proofcut"]
        del item.metadata["proofcut"]
    return timeline


def test_the_metadata_keys_are_pinned() -> None:
    """Literals on purpose: the legacy key names what old files carry forever."""
    assert tl.METADATA_KEY == "proofcut"
    assert tl.LEGACY_METADATA_KEY == "lucid"


def test_to_otio_writes_the_new_key_only() -> None:
    timeline = to_otio(_edit((0.0, 1.0), (2.0, 3.0)), CLIPS, rate=1000)

    for item in _stamped(timeline):
        assert "proofcut" in item.metadata
        assert "lucid" not in item.metadata


def test_from_otio_reads_a_timeline_stamped_with_the_pre_rename_key() -> None:
    """Every snapshot in `cache/history/` taken before the rename carries the
    old key, and undo puts one back whole — so it reads, through a real
    serialise/deserialise round trip rather than an in-memory object."""
    written = _as_pre_rename(to_otio(_edit((0.0, 1.0), (2.0, 3.0)), CLIPS, rate=1000))
    text = otio.adapters.write_to_string(written, "otio_json")
    assert '"proofcut": {' not in text and '"lucid": {' in text

    edit = from_otio(otio.adapters.read_from_string(text, "otio_json"))

    assert _spans(edit) == [(0.0, 1.0), (2.0, 3.0)]
    assert {s.clip_id for s in edit.segments} == {"vo"}


def test_the_new_key_wins_when_both_are_present() -> None:
    clip = otio.schema.Clip(name="c")
    clip.metadata["lucid"] = {"clip_id": "old"}
    clip.metadata["proofcut"] = {"clip_id": "new"}

    assert tl.proofcut_metadata(clip) == {"clip_id": "new"}
    assert tl.proofcut_metadata(otio.schema.Clip(name="bare")) == {}


def test_rewrite_legacy_metadata_rekeys_a_file_and_leaves_a_clean_one_alone(tmp_path) -> None:
    legacy = tmp_path / "legacy.otio"
    tl.write(_as_pre_rename(to_otio(_edit((0.0, 1.0), (2.0, 3.0)), CLIPS, rate=1000)), legacy)

    assert tl.count_legacy_metadata(legacy) == 3  # the timeline and two clips
    assert '"lucid"' in legacy.read_text(encoding="utf-8")  # counting wrote nothing
    assert tl.rewrite_legacy_metadata(legacy) == 3

    reread = otio.adapters.read_from_file(str(legacy))
    for item in _stamped(reread):
        assert "lucid" not in item.metadata
        assert "proofcut" in item.metadata
    assert reread.metadata["proofcut"]["rate"] == 1000
    assert _spans(tl.read(legacy)) == [(0.0, 1.0), (2.0, 3.0)]
    assert [p.name for p in tmp_path.iterdir()] == ["legacy.otio"]

    clean = tmp_path / "clean.otio"
    tl.write(to_otio(_edit((0.0, 1.0)), CLIPS, rate=1000), clean)
    before = clean.read_bytes()
    assert tl.rewrite_legacy_metadata(clean) == 0
    assert clean.read_bytes() == before


def test_write_never_leaves_half_a_timeline(tmp_path, monkeypatch) -> None:
    """OTIO's own `write_to_file` truncates the live file and writes into it
    (same inode, measured on 0.18.1), so a process killed mid-save — a Stop,
    a closed terminal — left a `project.otio` that no op can parse. `write`
    goes beside the file and moves over it, the manifest's own rule; the
    move failing here stands in for dying before it."""
    path = tmp_path / "project.otio"
    tl.write(to_otio(_edit((0.0, 1.0)), CLIPS, rate=1000), path)
    before = path.read_bytes()

    def dies(self, target):
        raise OSError("killed before the move")

    monkeypatch.setattr(type(path), "replace", dies)
    with pytest.raises(OSError):
        tl.write(to_otio(_edit((0.0, 1.0), (2.0, 3.0)), CLIPS, rate=1000), path)
    assert path.read_bytes() == before
    monkeypatch.undo()

    # And the text is what OTIO's own writer produced, so no project on disk
    # reads as changed the first time it is saved again. Line endings are
    # left out: text mode translates them on Windows, unmeasured for OTIO's.
    timeline = to_otio(_edit((0.0, 1.0), (2.0, 3.0)), CLIPS, rate=1000)
    tl.write(timeline, path)
    reference = tmp_path / "reference.otio"
    otio.adapters.write_to_file(timeline, str(reference))

    def text(p):
        return p.read_bytes().replace(b"\r\n", b"\n")

    assert text(path) == text(reference)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["project.otio", "reference.otio"]


# -- timeline_spans: the aggregate source -> timeline inverse --------------


def test_timeline_spans_reports_every_surviving_piece() -> None:
    """`timeline_span` stops at the first survivor because captions want one
    span per word. This one does not, so a range a cut split comes back whole
    in pieces rather than silently truncated.
    """
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    pieces = edit.timeline_spans("vo", 2.0, 8.0)

    assert [(p.source_start, p.source_end) for p in pieces] == [(2.0, 4.0), (6.0, 8.0)]
    # The hole closed, so the survivors are back-to-back on the timeline.
    assert [(p.timeline_start, p.timeline_end) for p in pieces] == [(2.0, 4.0), (4.0, 6.0)]

    single = edit.timeline_span("vo", 2.0, 8.0)
    assert single == (2.0, 4.0), "the captions-shaped call still stops at one"


def test_timeline_spans_of_a_fully_cut_range_is_empty() -> None:
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    assert edit.timeline_spans("vo", 4.5, 5.5) == []


def test_pieces_split_by_another_clip_are_not_contiguous() -> None:
    """The case the single-clip timeline cannot produce: two pieces of one
    clip with someone else's material playing between them. They are still
    both `vo`, so a merge on adjacency would join them across material that is
    not theirs.
    """
    edit = Edit(
        [Segment("vo", 0.0, 4.0), Segment("cam", 0.0, 3.0), Segment("vo", 8.0, 12.0)]
    )
    pieces = edit.timeline_spans("vo", 2.0, 10.0)

    assert len(pieces) == 2
    assert (pieces[0].timeline_start, pieces[0].timeline_end) == (2.0, 4.0)
    # cam occupies 4.0-7.0, so vo resumes at 7.0.
    assert (pieces[1].timeline_start, pieces[1].timeline_end) == (7.0, 9.0)
    assert not pieces[0].contiguous_with(pieces[1])


def test_a_cut_seam_still_counts_as_contiguous() -> None:
    edit = _edit((0.0, 4.0), (6.0, 10.0))
    first, second = edit.timeline_spans("vo", 2.0, 8.0)
    assert first.contiguous_with(second)


# -- the span index ------------------------------------------------------
#
# `Edit`'s addressing methods went from walking every segment to reading a
# cached `_SpanIndex`, because the walk is O(words x segments) across a caption
# pass and that is 2.03 s on a silence-cut hour. Two things have to be pinned:
# the index answers exactly what the walk answered (including on the edits the
# bisect's precondition does not hold for), and it cannot survive a mutation.


def _walk_span(edit: Edit, clip_id: str, start: float, end: float):
    """The pre-index implementation, kept here as the control it is — with
    the one semantic the index gained since, an overlap of float noise not
    being an overlap (`EPSILON`)."""
    offset = 0.0
    for seg in edit.segments:
        if seg.clip_id == clip_id:
            a, b = max(seg.start, start), min(seg.end, end)
            if b - a > EPSILON:
                return offset + (a - seg.start), offset + (b - seg.start)
        offset += seg.duration
    return None


def _walk_time(edit: Edit, clip_id: str, t: float, *, closed_end: bool = False):
    offset = 0.0
    for seg in edit.segments:
        if seg.clip_id == clip_id and (
            seg.start <= t < seg.end or (closed_end and t == seg.end)
        ):
            return offset + min(t - seg.start, seg.duration)
        offset += seg.duration
    return None


@pytest.mark.parametrize("overlapping", [False, True])
def test_the_index_answers_what_the_walk_answered(overlapping: bool) -> None:
    """Random edits, random lookups, against the walk the index replaced.

    `overlapping=True` is the case the bisect is *not* allowed to take: two
    segments of one clip meeting in source, which `import_edit` can produce
    from a `.kdenlive` that places the same footage twice. The index has to
    notice and walk instead, so the disagreement would show up here as an
    off-by-a-segment answer rather than as an error.

    `overlapping=False` builds genuinely disjoint clips and asserts the index
    said so, because a parametrisation where both cases fall down the same
    branch would pin the bisect to nothing.
    """
    import random

    rng = random.Random(20260813)
    took_the_bisect = 0
    for _ in range(200):
        segs = []
        if overlapping:
            for _ in range(rng.randint(1, 20)):
                cid = rng.choice(["vo", "cam"])
                a = round(rng.uniform(0.0, 50.0), 3)
                segs.append(Segment(cid, a, a + round(rng.uniform(0.05, 3.0), 3)))
        else:
            cursors = {"vo": 0.0, "cam": 0.0}
            for _ in range(rng.randint(1, 20)):
                cid = rng.choice(["vo", "cam"])
                a = cursors[cid] + round(rng.uniform(0.0, 2.0), 3)
                b = a + round(rng.uniform(0.05, 3.0), 3)
                segs.append(Segment(cid, a, b))
                cursors[cid] = b
        edit = Edit(segs)
        index = edit._span_index()
        took_the_bisect += sum(1 for entry in index.clips.values() if entry[3])

        for _ in range(30):
            cid = rng.choice(["vo", "cam", "nobody"])
            a = round(rng.uniform(-2.0, 55.0), 3)
            b = a + round(rng.uniform(0.0, 4.0), 3)
            assert edit.timeline_span(cid, a, b) == _walk_span(edit, cid, a, b)
            assert edit.timeline_time(cid, a) == _walk_time(edit, cid, a)
            assert edit.timeline_time(cid, a, closed_end=True) == _walk_time(
                edit, cid, a, closed_end=True
            )

    if overlapping:
        # Not "never": a randomly overlapping draw can still come out disjoint
        # for a one-segment clip. What matters is that the walk is reached.
        assert took_the_bisect < 200
    else:
        assert took_the_bisect > 200


def test_a_mutation_drops_the_index() -> None:
    """The failure the cache would otherwise buy: a confident wrong answer.

    Every mutator rebinds `segments`, which is what clears the index. `restore`
    is the one that used to splice in place, so it is the one worth naming.
    """
    edit = _edit((0.0, 10.0))
    assert edit.timeline_span("vo", 4.0, 6.0) == (4.0, 6.0)

    edit.remove("vo", 2.0, 3.0)
    assert edit.timeline_span("vo", 4.0, 6.0) == (3.0, 5.0)

    edit.restore("vo", 2.0, 3.0, duration=10.0)
    assert edit.timeline_span("vo", 4.0, 6.0) == (4.0, 6.0)

    edit.keep_only("vo", [(5.0, 10.0)])
    assert edit.timeline_span("vo", 4.0, 6.0) == (0.0, 1.0)


def test_the_index_is_not_shared_between_edits() -> None:
    a = _edit((0.0, 10.0))
    b = _edit((5.0, 10.0))
    assert a.timeline_span("vo", 6.0, 7.0) == (6.0, 7.0)
    assert b.timeline_span("vo", 6.0, 7.0) == (1.0, 2.0)


def test_a_round_tripped_segment_ends_exactly_where_its_neighbour_starts() -> None:
    """`from_otio` added the float duration to the float start, and 55.9 + 4.3
    is 60.199999999999996 — so a segment written ending at 60.2 read back one
    ulp short of the next segment's 60.2 start. `closed_end`'s instant test is
    `==`, which then missed: a gap word ending on the join of a spliced hold
    resolved to the far side of the hold's own silence, `_hold_plan` counted
    the hold into its own `elapsed`, and on the Lambs/Longlegs native rebuild
    one of eight holds read "no room" for a placement `hold add` had accepted.
    The rational end is what OTIO stores; read that.
    """
    edit = Edit([Segment("vo", 0.0, 55.9), Segment("cam", 0.0, 5.3), Segment("vo", 55.9, 60.2),
                 Segment("cam", 10.0, 13.35), Segment("vo", 60.2, 60.0 + 14.34)])
    back = from_otio(to_otio(edit, CLIPS, rate=1000.0))

    assert back.segments[2].end == back.segments[4].start == 60.2
    assert back.timeline_time("vo", 60.2, closed_end=True) == pytest.approx(55.9 + 5.3 + 4.3)
