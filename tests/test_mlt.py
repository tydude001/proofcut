"""The MLT writer — step 4 of the layered timeline.

Every assertion here is about a failure mode that produces a *render* rather
than an error: MLT pads a short playlist with `<blank>`, melt renders to the
longest declared length in the document, and both exit 0. So the tests read
the document back rather than trusting the variables it was built from —
`declared_frames` exists for the same reason.

The counterpart evidence is a real render: the document this module writes was
handed to melt on this box and came back 1920x1080 at exactly the declared
frame count, with the card compositing and the audio at unity. That is in
HISTORY.md § The MLT writer; it cannot live in a unit test, because melt is
inside a flatpak that cannot see the `/tmp` `tmp_path` hands out.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from proofcut import mlt

RATE = 30.0


def _shot(asset: str, frames: int, *, is_image: bool = False, duration: float | None = None,
          path: str | None = None, start_frame: int = 0,
          src_pin: float | None = None) -> dict[str, object]:
    return {
        "asset": asset,
        "asset_path": path or f"/media/{asset}.mp4",
        "asset_duration": duration,
        "is_image": is_image,
        "frames": frames,
        "start_frame": start_frame,
        "clip_id": "vo",
        "word_index": 1,
        "src_pin": src_pin,
    }


def _audio(*frames: int) -> list[mlt.Entry]:
    entries, cursor = [], 0
    for count in frames:
        entries.append(mlt.Entry("/media/vo.wav", cursor, count))
        cursor += count
    return entries


# -- entries and the off-by-one ------------------------------------------


def test_src_out_is_the_last_frame_index_not_the_count() -> None:
    """The arithmetic auto-editor gets wrong in the other direction: it writes
    the count where MLT wants the inclusive end, and melt renders a trailing
    black frame for it (picture.KNOWN_TAIL_FRAME)."""
    assert mlt.Entry("/media/a.mp4", 0, 30).src_out == 29
    assert mlt.Entry("/media/a.mp4", 90, 60).src_out == 149


# -- plan_picture: where inside each asset a shot reads from --------------


def test_plan_picture_carries_a_cursor_across_re_uses_of_one_clip() -> None:
    entries = mlt.plan_picture(
        [_shot("film", 45, duration=10.0), _shot("film", 60, duration=10.0)], RATE
    )
    assert [(e.src_in, e.frames) for e in entries] == [(0, 45), (45, 60)]


def test_plan_picture_keeps_a_separate_cursor_per_asset() -> None:
    entries = mlt.plan_picture(
        [
            _shot("a", 30, duration=10.0, path="/media/a.mp4"),
            _shot("b", 30, duration=10.0, path="/media/b.mp4"),
            _shot("a", 30, duration=10.0, path="/media/a.mp4"),
        ],
        RATE,
    )
    assert [e.src_in for e in entries] == [0, 0, 30]


def test_plan_picture_rewinds_rather_than_running_off_the_end() -> None:
    """Clamping would hold a frozen frame, which reads as a render bug. A
    rewind reads as a re-use, which is what it is."""
    entries = mlt.plan_picture(
        [_shot("film", 45, duration=2.0), _shot("film", 30, duration=2.0)], RATE
    )
    assert [e.src_in for e in entries] == [0, 0]


def test_plan_picture_refuses_a_shot_longer_than_its_whole_asset() -> None:
    with pytest.raises(mlt.MLTError, match="only 2.0s long"):
        mlt.plan_picture([_shot("film", 90, duration=2.0)], RATE)


def test_plan_picture_holds_a_still_from_its_first_frame_every_time() -> None:
    entries = mlt.plan_picture(
        [
            _shot("card:x", 40, is_image=True, path="/cards/x.png"),
            _shot("card:x", 25, is_image=True, path="/cards/x.png"),
        ],
        RATE,
    )
    assert [(e.src_in, e.frames, e.is_image) for e in entries] == [(0, 40, True), (0, 25, True)]


def test_plan_picture_refuses_an_asset_with_no_known_duration() -> None:
    with pytest.raises(mlt.MLTError, match="no known duration"):
        mlt.plan_picture([_shot("film", 30, duration=None)], RATE)


def test_plan_picture_refuses_a_shot_with_no_frames_in_it() -> None:
    with pytest.raises(mlt.MLTError, match="a cue landed on top of the next one"):
        mlt.plan_picture([_shot("film", 0, duration=10.0)], RATE)


# -- the pinned cue: it shows the moment it names, or it refuses -----------


def test_plan_picture_reads_a_pinned_shot_from_its_in_point() -> None:
    """The whole point of the pin: somebody read a description and asked for
    *that* moment, so the cursor does not get a say."""
    entries = mlt.plan_picture([_shot("film", 30, duration=10.0, src_pin=4.0)], RATE)

    assert [(e.src_in, e.frames) for e in entries] == [(120, 30)]


def test_a_pin_beats_the_cursor_a_previous_use_of_the_same_asset_left() -> None:
    entries = mlt.plan_picture(
        [_shot("film", 60, duration=10.0), _shot("film", 30, duration=10.0, src_pin=6.0)], RATE
    )

    assert [e.src_in for e in entries] == [0, 180]


def test_an_unpinned_re_use_after_a_pin_carries_on_from_where_the_pin_ended() -> None:
    """A pin consumes its stretch like any other shot — otherwise the shot
    after it replays footage the viewer has just seen."""
    entries = mlt.plan_picture(
        [_shot("film", 30, duration=10.0, src_pin=4.0), _shot("film", 30, duration=10.0)], RATE
    )

    assert [e.src_in for e in entries] == [120, 150]


def test_plan_picture_refuses_a_pinned_shot_that_runs_past_its_asset() -> None:
    """The one thing this step exists to prevent. Unpinned, this rewinds to 0
    and shows the asset's opening seconds — correct pixels, wrong video, and
    nothing on screen saying the search result was not what got placed."""
    with pytest.raises(mlt.MLTError, match="pins 'film' to 8.0s and the shot runs 3.0s"):
        mlt.plan_picture([_shot("film", 90, duration=10.0, src_pin=8.0)], RATE)

    # And the edge it stops exactly at: ending on the asset's last frame fits.
    entries = mlt.plan_picture([_shot("film", 60, duration=10.0, src_pin=8.0)], RATE)
    assert [(e.src_in, e.src_out) for e in entries] == [(240, 299)]


def test_the_pinned_refusal_is_not_the_rewind_the_same_shot_would_have_got() -> None:
    """Stated as a pair, because the two answers to one arrangement of frames
    are the whole design: the rewind is right for a re-use and wrong for a
    placement."""
    unpinned = _shot("film", 45, duration=2.0)
    entries = mlt.plan_picture([_shot("film", 45, duration=2.0), unpinned], RATE)
    assert [e.src_in for e in entries] == [0, 0]

    with pytest.raises(mlt.MLTError, match="a pinned cue shows the moment it names"):
        mlt.plan_picture([_shot("film", 45, duration=2.0, src_pin=1.0)], RATE)


def test_plan_picture_refuses_a_pin_before_the_start_of_the_asset() -> None:
    with pytest.raises(mlt.MLTError, match="before the start of the asset"):
        mlt.plan_picture([_shot("film", 30, duration=10.0, src_pin=-1.0)], RATE)


def test_plan_picture_refuses_a_pin_on_a_still_rather_than_ignoring_it() -> None:
    """`cue_add` turns this away first. If one reached here it would be a
    silent no-op, which is the failure mode this file is written against."""
    with pytest.raises(mlt.MLTError, match="it is a still"):
        mlt.plan_picture(
            [_shot("card:x", 30, is_image=True, path="/cards/x.png", src_pin=2.0)], RATE
        )


def test_a_shot_with_no_src_pin_key_at_all_still_plans() -> None:
    """The projection is the only caller that sets the key, and a caller
    building shots by hand (the export path's own tests do) must not have to."""
    bare = {
        "asset": "film",
        "asset_path": "/media/film.mp4",
        "asset_duration": 10.0,
        "is_image": False,
        "frames": 30,
        "start_frame": 0,
        "clip_id": "vo",
        "word_index": 1,
    }
    assert [e.src_in for e in mlt.plan_picture([bare], RATE)] == [0]


# -- the document, read back off itself -----------------------------------


def test_every_declared_length_is_the_timeline_total() -> None:
    """melt renders to the longest of them, so they all have to agree. Read
    back off the built tree, because a number that was right in a variable and
    wrong in an attribute is precisely the bug."""
    document = mlt.document(audio=_audio(60, 60), rate=RATE)

    declared = mlt.declared_frames(document)
    assert declared, "the sweep found nothing to check, which means it is not checking"
    assert set(declared.values()) == {120}


def test_a_tractors_out_is_the_last_frame_index() -> None:
    """The exact spot auto-editor writes the frame *count* into, costing every
    kdenlive export a trailing black frame (picture.KNOWN_TAIL_FRAME). This
    writer does not have that defect, and this is the test that keeps it so."""
    document = mlt.document(audio=_audio(120), rate=RATE)

    assert [t.get("out") for t in document.findall("tractor")] == ["119"] * 3


def test_declared_frames_ignores_a_still_images_four_hour_length() -> None:
    """A `qimage` producer claims four hours so a shot can never outrun it.
    That is not a statement about the timeline, and sweeping it in would make
    every document with a card in it look wrong."""
    document = mlt.document(
        audio=_audio(60),
        picture=[mlt.Entry("/cards/x.png", 0, 60, is_image=True)],
        rate=RATE,
    )
    assert set(mlt.declared_frames(document).values()) == {60}


def test_the_picture_lane_must_cover_the_timeline_exactly() -> None:
    with pytest.raises(mlt.MLTError, match="covers 90 frames but the timeline is 120"):
        mlt.document(
            audio=_audio(120), picture=[mlt.Entry("/media/film.mp4", 0, 90)], rate=RATE
        )


def test_a_picture_lane_running_long_is_refused_too() -> None:
    """Short pads with `<blank>`; long extends the render past the audio. Both
    are silent, so both are refusals rather than warnings."""
    with pytest.raises(mlt.MLTError, match="covers 150 frames but the timeline is 120"):
        mlt.document(
            audio=_audio(120), picture=[mlt.Entry("/media/film.mp4", 0, 150)], rate=RATE
        )


def test_no_blank_is_ever_written() -> None:
    document = mlt.document(
        audio=_audio(30, 30),
        picture=[mlt.Entry("/media/film.mp4", 0, 45), mlt.Entry("/cards/x.png", 0, 15, is_image=True)],
        rate=RATE,
    )
    assert document.find(".//blank") is None


def test_an_empty_timeline_is_refused() -> None:
    with pytest.raises(mlt.MLTError, match="at least one entry"):
        mlt.document(audio=[], rate=RATE)


# -- what the producers say -----------------------------------------------


def _producer(document: ET.Element, node_id: str) -> ET.Element:
    found = document.find(f"*[@id='{node_id}']")
    assert found is not None, f"no node {node_id!r} in the document"
    return found


def _properties(node: ET.Element) -> dict[str, str]:
    return {p.get("name", ""): (p.text or "") for p in node.findall("property")}


def test_picture_from_a_video_clip_plays_silent() -> None:
    """Film under a VO is muted at the producer, so no downstream mix can let
    it back in — `audio_index` is a producer property, not a per-entry one."""
    document = mlt.document(
        audio=_audio(60), picture=[mlt.Entry("/media/film.mp4", 0, 60)], rate=RATE
    )
    assert _properties(_producer(document, "vchain0"))["audio_index"] == "-1"


def test_a_still_becomes_a_qimage_producer_that_holds_past_its_end() -> None:
    document = mlt.document(
        audio=_audio(60),
        picture=[mlt.Entry("/cards/x.png", 0, 60, is_image=True)],
        rate=RATE,
    )
    properties = _properties(_producer(document, "vchain0"))
    assert properties["mlt_service"] == "qimage"
    assert properties["eof"] == "continue"
    assert int(properties["length"]) == round(mlt.IMAGE_LENGTH_SECONDS * RATE)


def test_an_audio_only_edit_hides_video_on_its_own_track() -> None:
    document = mlt.document(audio=_audio(60), rate=RATE)

    track = _producer(document, "tractor0")
    assert {t.get("hide") for t in track.findall("track")} == {"video"}
    assert _properties(_producer(document, "chain0"))["set.test_video"] == "1"


def test_an_edit_carrying_video_is_composited_rather_than_hidden() -> None:
    """Otherwise the timeline's own picture renders as sound over black — and
    exits 0 doing it."""
    document = mlt.document(
        audio=[mlt.Entry("/media/talk.mp4", 0, 60, has_video=True)], rate=RATE
    )

    track = _producer(document, "tractor0")
    assert {t.get("hide") for t in track.findall("track")} == {None}
    assert _properties(_producer(document, "chain0"))["set.test_video"] == "0"
    services = [
        _properties(t)["mlt_service"] for t in document.findall(".//transition")
    ]
    assert services == ["mix", "qtblend"]


def test_the_picture_track_gets_its_own_composite() -> None:
    """Without a transition a tractor renders its first track and drops the
    rest, silently (HISTORY.md § 4)."""
    document = mlt.document(
        audio=_audio(60), picture=[mlt.Entry("/media/film.mp4", 0, 60)], rate=RATE
    )
    blends = [
        _properties(t)
        for t in document.findall(".//transition")
        if _properties(t)["mlt_service"] == "qtblend"
    ]
    assert [b["b_track"] for b in blends] == ["2"]


def test_a_wav_beside_a_video_clip_is_still_told_it_has_no_picture() -> None:
    """`set.test_video` is a fact about the file, so it cannot be answered
    once for a track holding both — told 0, MLT renders black frames off the
    wav for as long as it is on screen."""
    document = mlt.document(
        audio=[
            mlt.Entry("/media/vo.wav", 0, 30),
            mlt.Entry("/media/talk.mp4", 0, 30, has_video=True),
        ],
        rate=RATE,
    )
    assert _properties(_producer(document, "chain0"))["set.test_video"] == "1"
    assert _properties(_producer(document, "chain1"))["set.test_video"] == "0"


def test_one_bin_entry_per_source_however_many_producers_it_needs() -> None:
    """A file on both the edit and the picture lane gets two producers,
    because "is its audio on" is a producer property — but it is one piece of
    media and Kdenlive's bin should say so."""
    document = mlt.document(
        audio=_audio(60), picture=[mlt.Entry("/media/vo.wav", 0, 60)], rate=RATE
    )
    main_bin = _producer(document, "main_bin")
    assert len(main_bin.findall("entry")) == 2  # the sequence, plus one source
    assert _properties(_producer(document, "chain0"))["kdenlive:id"] == (
        _properties(_producer(document, "vchain0"))["kdenlive:id"]
    )


# -- the profile ----------------------------------------------------------


def test_a_fractional_rate_is_written_as_its_real_fraction() -> None:
    """29.97 is 30000/1001. Written flat it is a frame of drift every hundred
    seconds, which on a six-minute video is four frames of lip-sync."""
    profile = mlt.document(audio=_audio(60), rate=30000 / 1001).find("profile")
    assert profile is not None
    assert (profile.get("frame_rate_num"), profile.get("frame_rate_den")) == ("30000", "1001")


def test_an_integer_rate_stays_an_integer() -> None:
    profile = mlt.document(audio=_audio(60), rate=RATE).find("profile")
    assert profile is not None
    assert (profile.get("frame_rate_num"), profile.get("frame_rate_den")) == ("30", "1")


def test_the_same_project_written_twice_is_the_same_document() -> None:
    """The sequence uuid is derived, not random, so a diff of two exports
    shows what changed in the edit rather than a new uuid every time."""
    first = mlt.to_string(mlt.document(audio=_audio(60), rate=RATE, name="scream"))
    second = mlt.to_string(mlt.document(audio=_audio(60), rate=RATE, name="scream"))
    assert first == second
    other = mlt.to_string(mlt.document(audio=_audio(60), rate=RATE, name="other"))
    assert other != first


# -- positions are frames, not clock time ---------------------------------


def test_entry_positions_are_written_as_frame_integers() -> None:
    """Millisecond text cannot name a 1/29.97s edge; a frame index can. This
    is also what makes the arithmetic checkable by eye."""
    document = mlt.document(audio=_audio(60, 60), rate=RATE)

    playlist = _producer(document, "playlist0")
    assert [(e.get("in"), e.get("out")) for e in playlist.findall("entry")] == [
        ("0", "59"),
        ("60", "119"),
    ]


# -- the reframe ---------------------------------------------------------
#
# Step 3 of the aspect swap. Every assertion here is against geometry that was
# measured on this box before any of it was built (PLAN.md § Aspect swap,
# findings 2 and 3) — a 1920x816 source in a 1080x1920 profile — because on
# this path every failure mode produces a file and exit 0.

WIDE = (1920, 816)
VERTICAL = (1080, 1920)


def test_unaided_mlt_contains_rather_than_fills() -> None:
    """The measured pillarbox: 459 of 1920 rows, the rest black bar. This is
    what a reframe is defined against, not an incidental fact."""
    assert mlt.fit_rect(WIDE, VERTICAL) == (0, 730, 1080, 459)


def test_the_centre_crop_is_the_largest_rect_of_the_canvas_aspect() -> None:
    crop = mlt.centre_crop(WIDE, VERTICAL)

    assert crop == (730, 0, 459, 816)
    assert crop[2] * VERTICAL[1] == crop[3] * VERTICAL[0], "carries the canvas aspect exactly"


def test_the_dest_rect_is_the_measured_fill() -> None:
    """`qtblend`'s rect is a destination in profile pixels, which is why it is
    larger than the profile and starts negative. The probe rendered
    `-1719 0 4518 1920` off the half-pixel centre; the integer rect this
    speaks lands one pixel to its right."""
    reframe = mlt.Reframe(source=WIDE, crop=mlt.centre_crop(WIDE, VERTICAL))

    assert reframe.dest_rect(VERTICAL) == (-1718, 0, 4518, 1920)
    assert reframe.rect_property(VERTICAL) == "-1718 0 4518 1920 1"


def test_a_source_already_at_the_canvas_aspect_is_an_identity() -> None:
    """And so gets no filter at all — which is what keeps every document
    written before this existed byte-identical."""
    for resolution in [(1920, 1080), (1280, 720), (3840, 2160)]:
        reframe = mlt.Reframe(source=(1920, 1080), crop=(0, 0, 1920, 1080))
        assert reframe.is_identity(resolution) is True


def test_a_crop_inside_a_matching_aspect_is_not_an_identity() -> None:
    """A zoom into a 16:9 region of a 16:9 source changes the frame even
    though nothing about the shape did."""
    reframe = mlt.Reframe(source=(1920, 1080), crop=(480, 270, 960, 540))

    assert reframe.is_identity((1920, 1080)) is False
    assert reframe.dest_rect((1920, 1080)) == (-960, -540, 3840, 2160)


def test_the_filter_reaches_both_of_a_files_nodes() -> None:
    """The trap finding 3 names: one node per resource *per role*, so a file
    on the edit and on the picture lane has two. Reaching one of them renders
    a film cropped on one track and letterboxed on the other, at exit 0."""
    audio = [mlt.Entry("/media/cold-open.mp4", 0, 60, has_video=True)]
    lane = mlt.plan_picture([_shot("cold-open", 60, duration=30.0,
                                   path="/media/cold-open.mp4")], RATE)
    reframe = {"/media/cold-open.mp4": mlt.Reframe(WIDE, mlt.centre_crop(WIDE, VERTICAL))}

    root = mlt.document(audio=audio, picture=lane, rate=RATE,
                        resolution=VERTICAL, reframe=reframe)

    reframed = mlt.reframed_nodes(root)
    assert sorted(reframed) == ["chain0", "vchain0"]
    assert set(reframed.values()) == {"-1718 0 4518 1920 1"}


def test_the_bin_keeps_the_raw_media() -> None:
    """The bin is the project's media list, `xml_retain`-ed out of the render.
    A crop is a timeline placement, not a property of the file."""
    audio = [mlt.Entry("/media/cold-open.mp4", 0, 60, has_video=True)]
    reframe = {"/media/cold-open.mp4": mlt.Reframe(WIDE, mlt.centre_crop(WIDE, VERTICAL))}

    root = mlt.document(audio=audio, rate=RATE, resolution=VERTICAL, reframe=reframe)

    bins = [n for n in root.findall("chain") if (n.get("id") or "").startswith("bin")]
    assert bins, "the bin entry exists"
    assert all(not node.findall("filter") for node in bins)


def test_a_still_is_never_cropped() -> None:
    """A card is authored at the canvas and re-authored when it moves
    (`card_reauthor`) — cropping one would be proofcut losing a corner of a
    title it drew itself."""
    audio = [mlt.Entry("/media/vo.wav", 0, 60)]
    lane = mlt.plan_picture([_shot("card:title", 60, is_image=True,
                                   path="/cards/title.png")], RATE)
    reframe = {"/cards/title.png": mlt.Reframe(WIDE, mlt.centre_crop(WIDE, VERTICAL))}

    root = mlt.document(audio=audio, picture=lane, rate=RATE,
                        resolution=VERTICAL, reframe=reframe)

    assert mlt.reframed_nodes(root) == {}


def test_no_reframe_leaves_the_document_exactly_as_it_was() -> None:
    audio = [mlt.Entry("/media/cold-open.mp4", 0, 60, has_video=True)]
    plain = mlt.to_string(mlt.document(audio=audio, rate=RATE, resolution=VERTICAL))
    empty = mlt.to_string(
        mlt.document(audio=audio, rate=RATE, resolution=VERTICAL, reframe={})
    )

    assert plain == empty
    assert "qtblend" not in mlt.reframed_nodes(ET.fromstring(plain))


# -- per-shot framing: one node, a window per camera shot ------------------
#
# PLAN.md § Per-shot framing. The address is `(clip_id, src_start, rect)` in
# *source* seconds, and finding 3 measured that this needs no new node: a
# `qtblend` rect is keyframable and its keyframes run on the producer's own
# source frames. So one node still carries every window for that file, which
# is what keeps `reframed_nodes`' one-per-role invariant intact.

LEFT = (0, 0, 459, 816)
RIGHT = (1461, 0, 459, 816)


def test_a_second_window_becomes_discrete_keyframes_in_source_frames() -> None:
    """Source frames, because that is the clock MLT runs a filter's animation
    on — measured from both directions in the probe. Discrete (`|=`) because
    a framing window steps at a camera cut; interpolating would slide the
    frame across the join."""
    reframe = mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),))

    assert reframe.rect_property(VERTICAL, RATE) == (
        "0|=0 0 4518 1920 1;300|=-3438 0 4518 1920 1"
    )


def test_one_window_still_writes_the_bare_rect() -> None:
    """The per-clip reframe is the degenerate case, and its document must not
    change: an animated string where a plain one used to be would rewrite
    every project on disk to no effect."""
    reframe = mlt.Reframe(WIDE, mlt.centre_crop(WIDE, VERTICAL))

    assert reframe.rect_property(VERTICAL, RATE) == "-1718 0 4518 1920 1"
    assert reframe.rect_property(VERTICAL) == "-1718 0 4518 1920 1"


def test_keyframes_need_the_rate_and_say_so() -> None:
    reframe = mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),))

    with pytest.raises(mlt.MLTError, match="source's own frames"):
        reframe.rect_property(VERTICAL)


def test_the_window_in_force_is_the_last_one_started() -> None:
    reframe = mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT), (20.0, LEFT)))

    assert reframe.crop_at(0.0) == LEFT
    assert reframe.crop_at(9.999) == LEFT
    assert reframe.crop_at(10.0) == RIGHT
    assert reframe.crop_at(19.0) == RIGHT
    assert reframe.crop_at(20.0) == LEFT
    assert reframe.dest_rect_at(10.0, VERTICAL) == reframe._dest(RIGHT, VERTICAL)


def test_a_moving_window_is_never_an_identity() -> None:
    """The head window can be the exact contain rect while a later one is not;
    skipping the filter on the strength of the first would render the rest of
    the file uncropped, at exit 0."""
    reframe = mlt.Reframe((1920, 1080), (0, 0, 1920, 1080), later=((5.0, (480, 270, 960, 540)),))

    assert reframe.is_identity((1920, 1080)) is False


def test_windows_must_be_ordered_distinct_and_after_the_head() -> None:
    with pytest.raises(mlt.MLTError, match="after the head"):
        mlt.Reframe(WIDE, LEFT, later=((0.0, RIGHT),))
    with pytest.raises(mlt.MLTError, match="source order"):
        mlt.Reframe(WIDE, LEFT, later=((20.0, RIGHT), (10.0, LEFT)))
    with pytest.raises(mlt.MLTError, match="source order"):
        mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT), (10.0, LEFT)))


# -- the keyframed move: PLAN.md § Per-shot framing, refused section; § The
# keyframed move. Authoring only — the mechanism (a per-key operator) was
# already there; `interp` is the first thing that ever asks for `=`.
#
# **Which key carries `=` is not obvious, and was settled on a real render,
# not reasoned about**: MLT interpolates the segment *leaving* a keyframe, not
# the one arriving at it. Flagging a window's own key held it at the previous
# rect for the whole stretch and cut hard at its own frame — indistinguishable
# from `|=` — while flagging the *previous* key produced a render that
# genuinely travelled between the two, crossing over roughly midway
# (`~/proofcut-work/projects/kf-probe`, `s4-reveal` at source 7.343s, the shipped teaser's own
# 410px follow). So `rect_property` puts the operator on the key *before* the
# one a caller names in `interp`, and that is what the tests below pin.


def test_interp_puts_the_equals_on_the_preceding_key_not_its_own() -> None:
    """The window at 20.0 is the one asking to slide in, but 10.0's key is the
    one that ends up carrying `=` — MLT's own segment-leaving-a-keyframe rule,
    not a free choice. Every other key stays discrete."""
    reframe = mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT), (20.0, LEFT)), interp=(20.0,))

    assert reframe.rect_property(VERTICAL, RATE) == (
        "0|=0 0 4518 1920 1;300=-3438 0 4518 1920 1;600|=0 0 4518 1920 1"
    )


def test_interp_on_the_first_later_window_flags_the_head_key() -> None:
    """The head can be a slide's departure even though it can never be its
    destination — there is a real rect at 0.0 to leave, even when nothing
    was asked for there."""
    reframe = mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),), interp=(10.0,))

    assert reframe.rect_property(VERTICAL, RATE) == "0=0 0 4518 1920 1;300|=-3438 0 4518 1920 1"


def test_a_slide_at_unity_scale_starts_one_pixel_larger() -> None:
    """A 1080p crop of a 1440p recording draws the source at its own size, and
    `qtblend` snaps a pure translation to whole pixels — a slow pan judders.
    The slide's own key is grown by a pixel; the held key it slides to is not.
    Measured: `~/proofcut-work/spikes/eased-pan/` (NATIVE.md § Part B, B2)."""
    screen = (2560, 1440)
    reframe = mlt.Reframe(
        screen, (320, 180, 1920, 1080), later=((10.0, (350, 180, 1920, 1080)),), interp=(10.0,)
    )

    assert reframe.rect_property((1920, 1080), RATE) == (
        "0=-320 -180 2561 1441 1;300|=-350 -180 2560 1440 1"
    )


def test_an_eased_slide_writes_its_operator_and_is_still_nudged_at_unity() -> None:
    screen = (2560, 1440)
    reframe = mlt.Reframe(
        screen,
        (320, 180, 1920, 1080),
        later=((10.0, (350, 180, 1920, 1080)),),
        interp=(10.0,),
        eases=((10.0, "ease-out"),),
    )

    assert reframe.rect_property((1920, 1080), RATE) == (
        "0h=-320 -180 2561 1441 1;300|=-350 -180 2560 1440 1"
    )
    assert reframe.ease_at(10.0) == "ease-out"
    assert reframe.ease_at(0.0) is None


def test_an_easing_must_name_a_sliding_window_and_a_known_curve() -> None:
    with pytest.raises(mlt.MLTError, match="does not slide"):
        mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),), eases=((10.0, "ease"),))
    with pytest.raises(mlt.MLTError, match="not one this build writes"):
        mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),), interp=(10.0,), eases=((10.0, "spline"),))


def test_a_held_window_at_unity_scale_keeps_its_exact_rect() -> None:
    """No slide, no nudge — a step is not a move, and resampling a still
    screen by a pixel would soften its text for nothing."""
    screen = (2560, 1440)
    reframe = mlt.Reframe(screen, (320, 180, 1920, 1080), later=((10.0, (350, 180, 1920, 1080)),))

    assert reframe.rect_property((1920, 1080), RATE) == (
        "0|=-320 -180 2560 1440 1;300|=-350 -180 2560 1440 1"
    )


def test_interp_must_name_a_later_window_not_the_head() -> None:
    """`interp` marks the window *arriving*, and there is nothing before the
    head of the source for it to slide from — the same "after the head" rule
    `later` itself already enforces."""
    with pytest.raises(mlt.MLTError, match="head has nothing before it"):
        mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),), interp=(0.0,))
    with pytest.raises(mlt.MLTError, match="head has nothing before it"):
        mlt.Reframe(WIDE, LEFT, interp=(10.0,))  # 10.0 names no window at all


def test_interp_and_split_on_the_same_window_are_refused() -> None:
    """The lower pane has no `interp` of its own (`pane_rect_property` always
    writes `|=`), so a window that is both would move on top and step
    underneath — two framings disagreeing in the same frame, at exit 0."""
    with pytest.raises(mlt.MLTError, match="cannot both slide and split"):
        mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),), panes=((10.0, RIGHT),), interp=(10.0,))


def test_is_interp_reads_the_flag_by_the_windows_own_start() -> None:
    reframe = mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT), (20.0, LEFT)), interp=(20.0,))

    assert reframe.is_interp(0.0) is False
    assert reframe.is_interp(10.0) is False
    assert reframe.is_interp(20.0) is True


def test_every_window_rides_one_node_per_role() -> None:
    """The whole point of finding 3: per-shot framing did not multiply the
    nodes, so the readback invariant that catches a half-applied reframe is
    unchanged."""
    audio = [mlt.Entry("/media/cold-open.mp4", 0, 60, has_video=True)]
    lane = mlt.plan_picture(
        [_shot("cold-open", 60, duration=30.0, path="/media/cold-open.mp4")], RATE
    )
    reframe = {"/media/cold-open.mp4": mlt.Reframe(WIDE, LEFT, later=((10.0, RIGHT),))}

    root = mlt.document(
        audio=audio, picture=lane, rate=RATE, resolution=VERTICAL, reframe=reframe
    )

    reframed = mlt.reframed_nodes(root)
    assert sorted(reframed) == ["chain0", "vchain0"]
    assert set(reframed.values()) == {"0|=0 0 4518 1920 1;300|=-3438 0 4518 1920 1"}
    rendered = [n for n in root.findall("chain") if not (n.get("id") or "").startswith("bin")]
    assert all(len(node.findall("filter")) == 1 for node in rendered)


# -- the stacked split: a second node, half a frame each --------------------
#
# PLAN.md § The stacked split, whose whole finding is that this needed no new
# render path. The mechanism was probed before it was built and the writer's
# own output was then rendered through melt and diffed against ffmpeg's two-
# pane composite of the same source frame — mean |diff| 1.59 against 66.6 for
# the solo crop it replaces, with the seam rows clean. That is HISTORY.md
# § The stacked split, built; it cannot live here, because melt is inside a
# flatpak that cannot see the `/tmp` `tmp_path` hands out.

#: A pane of a 1080x1920 canvas is 1080x960, so a pane window of a 1920x816
#: source is 816 * 1080/960 = 918 wide — twice the 459 one 9:16 crop gets.
PANE_LEFT = (0, 0, 918, 816)
PANE_RIGHT = (1002, 0, 918, 816)


def test_the_panes_tile_the_canvas_with_no_seam() -> None:
    """An odd height would otherwise leave a row of background showing
    between them, which reads as a hairline crack down the middle."""
    upper, lower = mlt.pane_boxes(VERTICAL)

    assert upper == (0, 0, 1080, 960)
    assert lower == (0, 960, 1080, 960)
    assert upper[3] + lower[3] == VERTICAL[1]
    odd_upper, odd_lower = mlt.pane_boxes((1080, 1921))
    assert odd_upper[3] + odd_lower[3] == 1921


def test_a_split_window_scales_each_pane_to_exactly_its_half() -> None:
    """The one thing holding the halves apart: no mask and no crop filter is
    involved anywhere, so a pane that scaled to more than 960 tall would draw
    into the other one. A pane window spans the full source height by
    construction, which makes the scaled frame exactly a pane tall."""
    reframe = mlt.Reframe(WIDE, PANE_LEFT, panes=((0.0, PANE_RIGHT),))

    upper, lower = mlt.pane_boxes(VERTICAL)
    top = reframe._dest(PANE_LEFT, VERTICAL, upper)
    bottom = reframe._dest(PANE_RIGHT, VERTICAL, lower)

    assert top[3] == 960 and bottom[3] == 960, "each scaled frame is one pane tall"
    assert top[1] == 0 and bottom[1] == 960, "and lands on its own half"


def test_a_split_writes_the_upper_pane_on_the_ordinary_node() -> None:
    """The primary node keeps drawing the whole way through and only its
    destination changes — which is why a split needs no `<blank>` on the lane
    itself and cannot leave a hole where it starts."""
    reframe = mlt.Reframe(WIDE, PANE_LEFT, panes=((0.0, PANE_RIGHT),))

    assert reframe.rect_property(VERTICAL, RATE) == "0|=0 0 2259 960 1"
    assert reframe.pane_rect_property(VERTICAL, RATE) == "0|=-1179 960 2259 960 1"


def test_the_pane_is_switched_off_by_opacity_at_every_other_window() -> None:
    """The failure this closes: a step that is not written is a value that
    carries on, so a pane left at opacity 1 past the end of its split draws
    the *next* shot's footage into the bottom half of the frame — at exit 0.
    Rendered rather than reasoned: solo/split/solo came back matching its own
    reference at each of the three, and 61 and 70 away from a pane still on."""
    reframe = mlt.Reframe(
        WIDE, LEFT, later=((2.0, PANE_LEFT), (5.0, RIGHT)), panes=((2.0, PANE_RIGHT),)
    )

    keys = reframe.pane_rect_property(VERTICAL, RATE).split(";")

    assert [key.split("|=")[0] for key in keys] == ["0", "60", "150"]
    assert keys[0].endswith(" 0") and keys[2].endswith(" 0"), "off either side"
    assert keys[1].endswith(" 1"), "and on for its own window"


def test_a_pane_needs_a_window_of_its_own_to_pair_with() -> None:
    """A pane addressed anywhere but at a window's own in-point would render
    as half a frame over whatever framing happened to be in force."""
    with pytest.raises(mlt.MLTError, match="no window of its own"):
        mlt.Reframe(WIDE, LEFT, later=((5.0, RIGHT),), panes=((3.0, PANE_RIGHT),))
    with pytest.raises(mlt.MLTError, match="source order"):
        mlt.Reframe(WIDE, LEFT, panes=((0.0, PANE_RIGHT), (0.0, PANE_LEFT)))


def test_a_split_is_never_an_identity() -> None:
    """Half the frame is being handed to a second node, which is not something
    MLT was already doing however innocent the rects look."""
    square = mlt.Reframe((1920, 1080), (0, 0, 1920, 1080), panes=((0.0, (0, 0, 1920, 1080)),))

    assert square.is_identity((1920, 1080)) is False


def test_a_split_grows_the_document_by_one_node_and_one_track() -> None:
    """A second *node*, not a second service. The pane track sits directly
    over the lane it is half of, so nothing that was already above it moves."""
    audio = [mlt.Entry("/media/vo.wav", 0, 60)]
    lane = mlt.plan_picture(
        [_shot("cold-open", 60, duration=30.0, path="/media/cold-open.mp4")], RATE
    )
    reframe = {"/media/cold-open.mp4": mlt.Reframe(WIDE, PANE_LEFT, panes=((0.0, PANE_RIGHT),))}

    root = mlt.document(
        audio=audio, picture=lane, rate=RATE, resolution=VERTICAL, reframe=reframe
    )

    assert sorted(mlt.reframed_nodes(root)) == ["pvchain0", "vchain0"]
    sequence = [t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None]
    stack = [track.get("producer") for track in sequence[0].findall("track")]
    assert stack == ["producer0", "tractor0", "tractor1", "tractor4"]
    # b_track is an index into that list, and a pane composites like any other
    # picture track — over the black background, above the lane it halves.
    blends = [
        t.find("property[@name='b_track']").text
        for t in sequence[0].findall("transition")
        if t.find("property[@name='mlt_service']").text == "qtblend"
    ]
    assert blends == ["2", "3"]


def test_the_pane_track_is_blanked_wherever_its_clip_is_not_split() -> None:
    """The one deliberate `<blank>` in this module. Its frame arithmetic is the
    lane's, entry for entry, so the pane track is exactly as long as the track
    it sits over — which `declared_frames` then checks."""
    audio = [mlt.Entry("/media/vo.wav", 0, 90)]
    lane = mlt.plan_picture(
        [
            _shot("card:title", 30, is_image=True, path="/cards/title.png"),
            _shot("cold-open", 30, duration=30.0, path="/media/cold-open.mp4"),
            _shot("card:end", 30, is_image=True, path="/cards/end.png"),
        ],
        RATE,
    )
    reframe = {"/media/cold-open.mp4": mlt.Reframe(WIDE, PANE_LEFT, panes=((0.0, PANE_RIGHT),))}

    root = mlt.document(
        audio=audio, picture=lane, rate=RATE, resolution=VERTICAL, reframe=reframe
    )

    pane_playlist = root.find("playlist[@id='playlist6']")
    assert pane_playlist is not None
    shape = [(child.tag, child.get("length") or child.get("producer")) for child in pane_playlist]
    assert shape == [("blank", "30"), ("entry", "pvchain0"), ("blank", "30")]
    assert sum(int(c.get("length")) for c in pane_playlist if c.tag == "blank") + 30 == 90


def test_the_edit_lanes_split_pane_is_silent() -> None:
    """It is a second producer of the same file — with its audio left on, the
    edit's own sound would be mixed in twice, at exit 0."""
    audio = [mlt.Entry("/media/talk.mp4", 0, 60, has_video=True)]
    reframe = {"/media/talk.mp4": mlt.Reframe(WIDE, PANE_LEFT, panes=((0.0, PANE_RIGHT),))}

    root = mlt.document(audio=audio, rate=RATE, resolution=VERTICAL, reframe=reframe)

    pane = root.find("chain[@id='pchain0']")
    assert pane is not None
    assert pane.find("property[@name='audio_index']").text == "-1"
    assert pane.find("property[@name='set.test_audio']").text == "1"


# -- blur-fill ---------------------------------------------------------------
#
# PLAN.md § Blur-fill: a window drawn whole (contained) over a blurred,
# darkened copy of itself scaled to cover the canvas. The background is a
# second node, switched by opacity at every window boundary like a pane.

WHOLE = (0, 0, *WIDE)


def test_a_fill_window_contains_the_source_and_its_background_covers_the_canvas() -> None:
    reframe = mlt.Reframe(WIDE, WHOLE, fills=(0.0,))

    contained = mlt.fit_rect(WIDE, VERTICAL)
    assert reframe.dest_rect(VERTICAL) == contained
    assert contained[2] == 1080, "contained: the full width, bars above and below"
    cover = reframe.cover_rect(VERTICAL)
    assert cover[3] == 1920 and cover[2] > 1080, "cover: the full height, overflowing sideways"
    assert cover[0] + cover[2] / 2 == pytest.approx(540, abs=1), "centred"
    assert reframe.fill_rect_property(VERTICAL) == " ".join(map(str, cover)) + " 1"
    assert reframe.fill_dest_at(0.0, VERTICAL) == cover


def test_the_fill_background_is_switched_off_at_every_other_window() -> None:
    """A pane's rule: a step not written is a value that carries on."""
    reframe = mlt.Reframe(WIDE, LEFT, later=((2.0, WHOLE), (5.0, RIGHT)), fills=(2.0,))

    keys = reframe.fill_rect_property(VERTICAL, RATE).split(";")
    assert [key.split("|=")[0] for key in keys] == ["0", "60", "150"]
    assert keys[0].endswith(" 0") and keys[2].endswith(" 0"), "off either side"
    assert keys[1].endswith(" 1"), "and on for its own window"
    # The picture node steps to the contained rect for the fill and back.
    picture = reframe.rect_property(VERTICAL, RATE).split(";")
    assert picture[1] == "60|=" + " ".join(map(str, mlt.fit_rect(WIDE, VERTICAL))) + " 1"
    assert reframe.fill_dest_at(1.0, VERTICAL) is None
    assert reframe.fill_dest_at(3.0, VERTICAL) == reframe.cover_rect(VERTICAL)


def test_a_fill_is_refused_where_it_would_render_wrong() -> None:
    with pytest.raises(mlt.MLTError, match="names no window"):
        mlt.Reframe(WIDE, LEFT, later=((5.0, WHOLE),), fills=(3.0,))
    with pytest.raises(mlt.MLTError, match="both split and blur-fill"):
        mlt.Reframe(WIDE, PANE_LEFT, panes=((0.0, PANE_RIGHT),), fills=(0.0,))
    with pytest.raises(mlt.MLTError, match="slide into or out of a blur-fill"):
        mlt.Reframe(WIDE, WHOLE, later=((2.0, LEFT),), interp=(2.0,), fills=(0.0,))
    with pytest.raises(mlt.MLTError, match="slide into or out of a blur-fill"):
        mlt.Reframe(WIDE, LEFT, later=((2.0, WHOLE),), interp=(2.0,), fills=(2.0,))


def test_a_fill_is_never_an_identity() -> None:
    """Even a source already at the canvas's shape: the fill adds a node."""
    same = mlt.Reframe(VERTICAL, (0, 0, *VERTICAL), fills=(0.0,))

    assert same.is_identity(VERTICAL) is False


def test_a_picture_fill_goes_under_the_lane_and_over_the_edit() -> None:
    """Under its own lane, so the contained picture draws over it; over the
    edit, or the edit's footage would show through the filled shot's bars."""
    audio = [mlt.Entry("/media/talk.mp4", 0, 60, has_video=True)]
    lane = mlt.plan_picture(
        [_shot("cold-open", 60, duration=30.0, path="/media/cold-open.mp4")], RATE
    )
    reframe = {"/media/cold-open.mp4": mlt.Reframe(WIDE, WHOLE, fills=(0.0,))}

    root = mlt.document(
        audio=audio, picture=lane, rate=RATE, resolution=VERTICAL, reframe=reframe
    )

    assert sorted(mlt.reframed_nodes(root)) == ["fvchain0", "vchain0"]
    sequence = [t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None]
    stack = [track.get("producer") for track in sequence[0].findall("track")]
    assert stack == ["producer0", "tractor0", "tractorE", "tractor1"]
    blends = [
        t.find("property[@name='b_track']").text
        for t in sequence[0].findall("transition")
        if t.find("property[@name='mlt_service']").text == "qtblend"
    ]
    assert blends == ["1", "2", "3"]
    mixes = [
        t.find("property[@name='b_track']").text
        for t in sequence[0].findall("transition")
        if t.find("property[@name='mlt_service']").text == "mix"
    ]
    assert mixes == ["1"], "the background is never mixed"


def test_the_fill_background_is_silent_blurred_darkened_then_placed() -> None:
    """Blur before `qtblend`, so the percentage is of the source frame and the
    look holds on any canvas (PLAN.md § Blur-fill, finding 2)."""
    audio = [mlt.Entry("/media/talk.mp4", 0, 60, has_video=True)]
    reframe = {"/media/talk.mp4": mlt.Reframe(WIDE, WHOLE, fills=(0.0,))}

    root = mlt.document(audio=audio, rate=RATE, resolution=VERTICAL, reframe=reframe)

    node = root.find("chain[@id='fchain0']")
    assert node is not None
    assert node.find("property[@name='audio_index']").text == "-1"
    services = [f.find("property[@name='mlt_service']").text for f in node.findall("filter")]
    assert services == ["box_blur", "brightness", "qtblend"]
    blur = node.findall("filter")[0]
    assert blur.find("property[@name='hradius']").text == str(mlt.FILL_BLUR)
    sequence = [t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None]
    stack = [track.get("producer") for track in sequence[0].findall("track")]
    assert stack == ["producer0", "tractorD", "tractor0"]


def test_a_fill_background_is_blanked_wherever_its_clip_is_not_on_the_lane() -> None:
    audio = [mlt.Entry("/media/vo.wav", 0, 90)]
    lane = mlt.plan_picture(
        [
            _shot("card:title", 30, is_image=True, path="/cards/title.png"),
            _shot("cold-open", 30, duration=30.0, path="/media/cold-open.mp4"),
            _shot("card:end", 30, is_image=True, path="/cards/end.png"),
        ],
        RATE,
    )
    reframe = {"/media/cold-open.mp4": mlt.Reframe(WIDE, WHOLE, fills=(0.0,))}

    root = mlt.document(
        audio=audio, picture=lane, rate=RATE, resolution=VERTICAL, reframe=reframe
    )

    playlist = root.find("playlist[@id='playlist16']")
    assert playlist is not None
    shape = [(child.tag, child.get("length") or child.get("producer")) for child in playlist]
    assert shape == [("blank", "30"), ("entry", "fvchain0"), ("blank", "30")]


def test_no_fill_writes_no_fill_node() -> None:
    """What keeps every unfilled project's document byte-identical."""
    audio = [mlt.Entry("/media/talk.mp4", 0, 60, has_video=True)]
    reframe = {"/media/talk.mp4": mlt.Reframe(WIDE, LEFT)}

    root = mlt.document(audio=audio, rate=RATE, resolution=VERTICAL, reframe=reframe)

    text = ET.tostring(root, encoding="unicode")
    assert "fchain" not in text and "tractorD" not in text and "box_blur" not in text


def test_pane_overlap_is_the_share_of_the_narrower_pane() -> None:
    """The number a stacked split is judged on. Nothing masks a pane, so what
    the two share is source shown twice — once in each half."""
    assert mlt.pane_overlap((0, 0, 900, 816), (900, 0, 900, 816)) == 0.0
    assert mlt.pane_overlap((0, 0, 900, 816), (450, 0, 900, 816)) == 0.5
    assert mlt.pane_overlap((450, 0, 900, 816), (0, 0, 900, 816)) == 0.5, "order does not matter"
    assert mlt.pane_overlap((0, 0, 900, 816), (0, 0, 900, 816)) == 1.0


def test_pane_overlap_separates_the_films_own_splits_from_its_duplicating_ones() -> None:
    """**The line is measured, not chosen.** These are real proposals off the
    Scream cut: the two that hold distinct groups against the two where the
    same face lands in both halves. A rule that could not tell them apart
    would be a number worth nothing to a reviewer.
    """
    distinct = [
        mlt.pane_overlap((153, 0, 900, 816), (845, 0, 900, 816)),  # s4-overexposed 7.632
        mlt.pane_overlap((131, 0, 904, 812), (810, 0, 904, 812)),  # s2022-reveal 4.087
    ]
    duplicating = [
        mlt.pane_overlap((447, 0, 904, 812), (878, 0, 904, 812)),  # vi-bailey 10.052
        mlt.pane_overlap((683, 0, 904, 812), (1016, 0, 904, 812)),  # vi-bailey 19.937
    ]
    assert max(distinct) < 0.30
    assert min(duplicating) > 0.50


# -- reading an outside cut back in ----------------------------------------
#
# The module's own rule is "generated, never mutated" and this does not bend
# it: `read_ranges` reads somebody else's document and writes nothing. What
# these pin is the arithmetic, because every way of getting it wrong produces
# a *timeline* rather than an error — a cut one frame short everywhere, or a
# cut that silently drops half of somebody's edit.


def _kdenlive(entries: str, *, rate: str = 'frame_rate_num="30" frame_rate_den="1"',
              extra: str = "") -> ET.Element:
    return ET.fromstring(
        f'<mlt root="/media"><profile {rate} />'
        '<producer id="producer0">'
        '<property name="resource">black</property>'
        '<property name="mlt_service">color</property>'
        "</producer>"
        '<chain id="chain0"><property name="resource">vo.mp4</property></chain>'
        f'<playlist id="playlist0">{entries}</playlist>{extra}'
        "</mlt>"
    )


def test_out_is_the_last_frame_index_not_a_count() -> None:
    """**Settled by measurement, not by reading MLT's documentation.**
    auto-editor's `--export v3` and `--export kdenlive` of one cut give `dur`
    and `(in, out)` for the same three segments: 67/103/97 frames against
    (0, 66), (114, 216), (264, 360). `out - in + 1` equals `dur` on all three,
    so the exclusive end is `out + 1`. Reading `out` as exclusive would make
    every imported segment one frame short — invisible on any single segment
    and 63 frames on the Scream cut.
    """
    ranges, rate = mlt.read_ranges(
        _kdenlive(
            '<entry producer="chain0" in="0" out="66"/>'
            '<entry producer="chain0" in="114" out="216"/>'
            '<entry producer="chain0" in="264" out="360"/>'
        )
    )
    assert rate == 30.0
    assert [round(r.duration * rate) for r in ranges] == [67, 103, 97]
    assert ranges[0].start == 0.0
    assert ranges[0].end == pytest.approx(67 / 30)


def test_a_position_reads_as_a_timecode_or_a_frame_number() -> None:
    """One attribute, two spellings, both legal: Kdenlive writes frames and
    auto-editor 31.4.2 writes `HH:MM:SS.mmm` into the same document format.
    Handling only one would read the other as zero and import a cut that
    starts at the head of the file.
    """
    frames, _ = mlt.read_ranges(_kdenlive('<entry producer="chain0" in="114" out="216"/>'))
    timecodes, _ = mlt.read_ranges(
        _kdenlive('<entry producer="chain0" in="00:00:03.800" out="00:00:07.200"/>')
    )
    assert frames == timecodes


def test_the_black_track_is_not_footage() -> None:
    """`producer0` is the colour producer every Kdenlive document carries. An
    entry against it is not a cut of anything, and counting it would add a
    range no clip could be resolved for.
    """
    ranges, _ = mlt.read_ranges(
        _kdenlive(
            '<entry producer="producer0" in="0" out="299"/>'
            '<entry producer="chain0" in="0" out="66"/>'
        )
    )
    assert [r.resource for r in ranges] == ["vo.mp4"]


def test_media_is_read_from_chain_and_from_producer() -> None:
    """MLT 7 writes `<chain>` and older versions write `<producer>`, and one
    document can hold both. A reader that knew only one would find no cut in
    half the files it was handed.
    """
    root = ET.fromstring(
        '<mlt><profile frame_rate_num="30" frame_rate_den="1" />'
        '<producer id="p1"><property name="resource">old.mp4</property></producer>'
        '<playlist id="playlist0"><entry producer="p1" in="0" out="29"/></playlist>'
        "</mlt>"
    )
    ranges, _ = mlt.read_ranges(root)
    assert [r.resource for r in ranges] == ["old.mp4"]


def test_a_blank_is_refused_rather_than_closed_over() -> None:
    """A `<blank>` is real runtime with nothing under it, and `Edit` lays its
    segments contiguously — so importing one would close the hole silently and
    make the timeline shorter than the file it came from. The same reason this
    module never writes one on a lane.
    """
    with pytest.raises(mlt.MLTError, match="close the hole"):
        mlt.read_ranges(
            _kdenlive(
                '<entry producer="chain0" in="0" out="66"/>'
                '<blank length="00:00:01.000"/>'
                '<entry producer="chain0" in="114" out="216"/>'
            )
        )


def test_playlists_carrying_the_same_cut_agree_and_are_read_once() -> None:
    """A cut-and-concat timeline writes identical intervals onto its audio and
    video tracks — which is exactly what auto-editor's kdenlive export does —
    so reading every playlist and checking they agree costs nothing and needs
    no rule about which track is authoritative.
    """
    ranges, _ = mlt.read_ranges(
        _kdenlive(
            '<entry producer="chain0" in="0" out="66"/>',
            extra='<playlist id="playlist2"><entry producer="chain0" in="0" out="66"/></playlist>',
        )
    )
    assert len(ranges) == 1


def test_playlists_carrying_different_cuts_are_refused_not_preferred() -> None:
    """**Preferring a track would import half of somebody's edit and report
    success.** Two playlists that disagree are a multi-track picture edit,
    which proofcut's one linked A/V track has no shape for, so it refuses by
    name.
    """
    with pytest.raises(mlt.MLTError, match="different cuts"):
        mlt.read_ranges(
            _kdenlive(
                '<entry producer="chain0" in="0" out="66"/>',
                extra=(
                    '<playlist id="playlist2">'
                    '<entry producer="chain0" in="0" out="66"/>'
                    '<entry producer="chain0" in="114" out="216"/>'
                    "</playlist>"
                ),
            )
        )


def test_the_bin_is_not_a_cut() -> None:
    """`main_bin` is the project's media list — every clip appears in it whole,
    so reading it as a playlist would find a "cut" that is the untrimmed
    source and then refuse for disagreeing with the real one.
    """
    ranges, _ = mlt.read_ranges(
        _kdenlive(
            '<entry producer="chain0" in="0" out="66"/>',
            extra='<playlist id="main_bin"><entry producer="chain0" in="0" out="359"/></playlist>',
        )
    )
    assert [round(r.duration * 30) for r in ranges] == [67]


def test_a_document_with_no_media_entry_says_so() -> None:
    with pytest.raises(mlt.MLTError, match="no cut here"):
        mlt.read_ranges(_kdenlive(""))


def test_the_rate_comes_from_the_profile_and_a_missing_one_refuses() -> None:
    """Every position in a playlist is a frame number on the profile's clock,
    so reading the rate wrong scales the whole import rather than shifting it.
    """
    _, rate = mlt.read_ranges(
        _kdenlive(
            '<entry producer="chain0" in="0" out="47"/>',
            rate='frame_rate_num="24000" frame_rate_den="1001"',
        )
    )
    assert rate == pytest.approx(23.976, abs=1e-3)

    with pytest.raises(mlt.MLTError, match="no <profile>"):
        mlt.read_ranges(ET.fromstring('<mlt><playlist id="p"/></mlt>'))


# -- the music lane: A2, one more of everything and no new concept ----------


def _music(*frames: int) -> list[mlt.Entry]:
    entries, names = [], iter(("/media/bed.wav", "/media/sil.wav", "/media/sil2.wav"))
    for count in frames:
        entries.append(mlt.Entry(next(names), 0, count))
    return entries


def test_the_music_lane_must_cover_the_timeline_exactly() -> None:
    """The one real finding of the A2 probe (PLAN.md § The A2 music lane):
    melt itself pads a short A2 with true silence and clips a long one, both
    at exit 0 — but the caller pads/trims by construction (resolution (b)),
    so a lane arriving short or long here is a bug, not a request."""
    with pytest.raises(mlt.MLTError, match="music lane covers 90 frames but the timeline is 120"):
        mlt.document(audio=_audio(120), music=_music(90), rate=RATE)
    with pytest.raises(mlt.MLTError, match="music lane covers 150"):
        mlt.document(audio=_audio(120), music=_music(150), rate=RATE)


def test_a_still_on_the_music_lane_is_refused() -> None:
    with pytest.raises(mlt.MLTError, match="no sound to mix"):
        mlt.document(
            audio=_audio(120),
            music=[mlt.Entry("/cards/x.png", 0, 120, is_image=True)],
            rate=RATE,
        )


def test_the_music_track_declares_the_timeline_total_with_no_exception() -> None:
    """Resolution (b)'s whole payoff: A2's own tractor agrees with every
    other declared length, so `declared_frames` keeps needing zero special
    cases."""
    document = mlt.document(audio=_audio(120), music=_music(90, 30), rate=RATE)
    declared = mlt.declared_frames(document)
    assert declared["tractor tractorA out"] == 120
    assert set(declared.values()) == {120}


def test_the_music_lane_gets_its_own_mix_and_no_qtblend() -> None:
    """The second `mix` the module docstring always said a second audio
    track would need — additive (`sum=1`), against the black background like
    transition0 — and no compositing transition, because nothing of the lane
    is on screen."""
    document = mlt.document(
        audio=_audio(120),
        picture=[mlt.Entry("/media/film.mp4", 0, 120, has_video=True)],
        music=_music(120),
        rate=RATE,
    )
    sequence = next(
        t for t in document.findall("tractor") if t.get("id", "").startswith("{")
    )
    tracks = [t.get("producer") for t in sequence.findall("track")]
    music_index = tracks.index("tractorA")

    mixes = []
    for transition in sequence.findall("transition"):
        service = transition.find("property[@name='mlt_service']")
        b_track = transition.find("property[@name='b_track']")
        mixes.append(((service.text or ""), (b_track.text or "")))
    assert ("mix", str(music_index)) in mixes, mixes
    assert ("qtblend", str(music_index)) not in mixes, "the music lane never blends"


def test_the_music_tracks_video_is_hidden() -> None:
    """A bed ripped from a video file still plays as sound only — the track
    hides video, so nothing of the file's picture can reach the frame."""
    document = mlt.document(audio=_audio(120), music=_music(120), rate=RATE)
    music_track = next(t for t in document.findall("tractor") if t.get("id") == "tractorA")
    assert all(t.get("hide") == "video" for t in music_track.findall("track"))


def test_music_sources_reach_the_bin() -> None:
    document = mlt.document(audio=_audio(120), music=_music(90, 30), rate=RATE)
    main_bin = next(p for p in document.findall("playlist") if p.get("id") == "main_bin")
    assert len(main_bin.findall("entry")) == 1 + 3  # sequence + vo + bed + silence


def test_a_document_without_music_is_byte_identical_to_before_the_lane_existed() -> None:
    """The lane's ids live in their own namespace (mchain/playlist8/9/
    tractorA) precisely so an unswapped project's document cannot move."""
    plain = mlt.to_string(mlt.document(audio=_audio(120), rate=RATE))
    with_none = mlt.to_string(mlt.document(audio=_audio(120), music=None, rate=RATE))
    with_empty = mlt.to_string(mlt.document(audio=_audio(120), music=[], rate=RATE))
    assert plain == with_none == with_empty
    assert "tractorA" not in plain and "playlist8" not in plain


# -- the holds lane: a fourth, audio-only, real footage's own clean sound ----


def _holds(*frames: int) -> list[mlt.Entry]:
    entries, names = [], iter(("/media/film.mp4", "/media/sil.wav", "/media/sil2.wav"))
    for count in frames:
        entries.append(mlt.Entry(next(names), 0, count, has_video=True))
    return entries


def test_the_holds_lane_must_cover_the_timeline_exactly() -> None:
    with pytest.raises(mlt.MLTError, match="holds lane covers 90 frames but the timeline is 120"):
        mlt.document(audio=_audio(120), holds=_holds(90), rate=RATE)
    with pytest.raises(mlt.MLTError, match="holds lane covers 150"):
        mlt.document(audio=_audio(120), holds=_holds(150), rate=RATE)


def test_a_still_on_the_holds_lane_is_refused() -> None:
    with pytest.raises(mlt.MLTError, match="none to play"):
        mlt.document(
            audio=_audio(120),
            holds=[mlt.Entry("/cards/x.png", 0, 120, is_image=True)],
            rate=RATE,
        )


def test_the_holds_track_declares_the_timeline_total_with_no_exception() -> None:
    document = mlt.document(audio=_audio(120), holds=_holds(90, 30), rate=RATE)
    declared = mlt.declared_frames(document)
    assert declared["tractor tractorB out"] == 120
    assert set(declared.values()) == {120}


def test_the_holds_lane_gets_its_own_mix_and_no_qtblend() -> None:
    """`music`'s own discipline, restated for the fourth lane: additive
    against the black background, no compositing transition — a hold's
    picture is switched off at the node, so there is nothing of it on
    screen to blend."""
    document = mlt.document(
        audio=_audio(120),
        picture=[mlt.Entry("/media/film.mp4", 0, 120, has_video=True)],
        holds=_holds(120),
        rate=RATE,
    )
    sequence = next(
        t for t in document.findall("tractor") if t.get("id", "").startswith("{")
    )
    tracks = [t.get("producer") for t in sequence.findall("track")]
    holds_index = tracks.index("tractorB")

    mixes = []
    for transition in sequence.findall("transition"):
        service = transition.find("property[@name='mlt_service']")
        b_track = transition.find("property[@name='b_track']")
        mixes.append(((service.text or ""), (b_track.text or "")))
    assert ("mix", str(holds_index)) in mixes, mixes
    assert ("qtblend", str(holds_index)) not in mixes, "the holds lane never blends"


def test_a_document_with_both_music_and_holds_gets_two_distinct_mix_transitions() -> None:
    """The trap a hardcoded `blended + 1` would hit: with both extra lanes
    present, the second one's transition id must not collide with the
    first's — `mlt.document`'s own `wrong = {...}` frame-total check would
    stay clean either way, so only the transition ids themselves catch it."""
    document = mlt.document(
        audio=_audio(120), music=_music(120), holds=_holds(120), rate=RATE
    )
    sequence = next(
        t for t in document.findall("tractor") if t.get("id", "").startswith("{")
    )
    ids = [t.get("id") for t in sequence.findall("transition")]
    assert len(ids) == len(set(ids)), f"duplicate transition ids: {ids}"
    tracks = [t.get("producer") for t in sequence.findall("track")]
    music_index, holds_index = tracks.index("tractorA"), tracks.index("tractorB")
    b_tracks = {
        t.find("property[@name='b_track']").text
        for t in sequence.findall("transition")
        if (t.find("property[@name='mlt_service']").text or "") == "mix"
    }
    assert {str(music_index), str(holds_index)} <= b_tracks


def test_the_holds_tracks_video_is_hidden() -> None:
    document = mlt.document(audio=_audio(120), holds=_holds(120), rate=RATE)
    holds_track = next(t for t in document.findall("tractor") if t.get("id") == "tractorB")
    assert all(t.get("hide") == "video" for t in holds_track.findall("track"))


def test_holds_sources_reach_the_bin() -> None:
    document = mlt.document(audio=_audio(120), holds=_holds(90, 30), rate=RATE)
    main_bin = next(p for p in document.findall("playlist") if p.get("id") == "main_bin")
    assert len(main_bin.findall("entry")) == 1 + 3  # sequence + vo + film + silence


def test_a_document_without_holds_is_byte_identical_to_before_the_lane_existed() -> None:
    """The lane's ids live in their own namespace (hchain/playlist10/11/
    tractorB) precisely so an unswapped project's document cannot move."""
    plain = mlt.to_string(mlt.document(audio=_audio(120), rate=RATE))
    with_none = mlt.to_string(mlt.document(audio=_audio(120), holds=None, rate=RATE))
    with_empty = mlt.to_string(mlt.document(audio=_audio(120), holds=[], rate=RATE))
    assert plain == with_none == with_empty
    assert "tractorB" not in plain and "playlist10" not in plain


def test_a_hold_node_carries_video_index_minus_one_and_no_audio_index() -> None:
    """The exact mirror of the picture lane's own convention (`audio_index=
    -1`, `video_index` untouched): a hold node silences picture, not sound,
    and it does so with `video_index` alone — no `audio_index` property at
    all, because the base `astream="0"` selector is already right (this
    module's own docstring, and CLAUDE.md's `media.media_path()` containment
    note)."""
    document = mlt.document(audio=_audio(120), holds=_holds(120), rate=RATE)
    node = next(n for n in document.findall("chain") if n.get("id") == "hchain0")
    video_index = node.find("property[@name='video_index']")
    audio_index = node.find("property[@name='audio_index']")
    assert video_index is not None and video_index.text == "-1"
    assert audio_index is None


# -- the A2 fades: one entry-attached volume filter, dB keyframes ------------


def test_a_fade_is_one_entry_attached_volume_filter_with_db_keyframes() -> None:
    """Both facts here were measured, not recalled (`mlt.FADE_FLOOR_DB`):
    `level` keyframe values are dB — gain-factor keys 0..1 render as a 1 dB
    wiggle at exit 0 — and positions are relative to the entry the filter is
    attached to, so the fade lands on the bed's own audible frames however
    much silence pads the lane."""
    bed = mlt.Entry("/media/bed.wav", 0, 120, fade_in_frames=30, fade_out_frames=30)
    document = mlt.document(audio=_audio(120), music=[bed], rate=RATE)
    playlist = next(p for p in document.findall("playlist") if p.get("id") == "playlist8")
    filters = playlist.findall("entry/filter")
    assert len(filters) == 1
    service = filters[0].find("property[@name='mlt_service']")
    level = filters[0].find("property[@name='level']")
    assert service is not None and service.text == "volume"
    assert level is not None and level.text == "0=-60;30=0;89=0;119=-60"


def test_a_one_sided_fade_states_the_other_edge_explicitly() -> None:
    """Nothing relies on how MLT extrapolates past a final keyframe — the
    probe did not measure it, so the animation string always pins both ends."""
    fade_in_only = mlt.Entry("/media/bed.wav", 0, 120, fade_in_frames=30)
    document = mlt.document(audio=_audio(120), music=[fade_in_only], rate=RATE)
    playlist = next(p for p in document.findall("playlist") if p.get("id") == "playlist8")
    assert playlist.find("entry/filter/property[@name='level']").text == "0=-60;30=0;119=0"

    fade_out_only = mlt.Entry("/media/bed.wav", 0, 120, fade_out_frames=30)
    document = mlt.document(audio=_audio(120), music=[fade_out_only], rate=RATE)
    playlist = next(p for p in document.findall("playlist") if p.get("id") == "playlist8")
    assert playlist.find("entry/filter/property[@name='level']").text == "0=0;89=0;119=-60"


def test_the_fade_rides_the_bed_entry_never_the_silence_beside_it() -> None:
    """Entry-attached is the whole point: lead/trail silence on the same lane
    gets no filter, so the fade-out ends where the music audibly ends."""
    lead = mlt.Entry("/media/sil.wav", 0, 30)
    bed = mlt.Entry("/media/bed.wav", 0, 60, fade_in_frames=15, fade_out_frames=15)
    trail = mlt.Entry("/media/sil2.wav", 0, 30)
    document = mlt.document(audio=_audio(120), music=[lead, bed, trail], rate=RATE)
    playlist = next(p for p in document.findall("playlist") if p.get("id") == "playlist8")
    entries = playlist.findall("entry")
    assert [len(e.findall("filter")) for e in entries] == [0, 1, 0]
    assert entries[1].find("filter/property[@name='level']").text == "0=-60;15=0;44=0;59=-60"


def test_fades_that_do_not_fit_the_entry_are_refused() -> None:
    with pytest.raises(mlt.MLTError, match="do not fit inside the 120 frames"):
        mlt.document(
            audio=_audio(120),
            music=[mlt.Entry("/media/bed.wav", 0, 120, fade_in_frames=90, fade_out_frames=60)],
            rate=RATE,
        )
    with pytest.raises(mlt.MLTError, match="negative fade"):
        mlt.document(
            audio=_audio(120),
            music=[mlt.Entry("/media/bed.wav", 0, 120, fade_in_frames=-1)],
            rate=RATE,
        )


def test_a_fade_free_bed_is_byte_identical_to_before_fades_existed() -> None:
    """`fade_*_frames=0` emits no filter node at all — the no-fade document
    cannot move, the same rule the no-music document already lives under."""
    document = mlt.document(audio=_audio(120), music=_music(120), rate=RATE)
    assert "<filter" not in mlt.to_string(document)


# -- `gain_db`: one new `Entry` field, the fade plateau generalized ---------
#
# Feature: the cold open as project state. `gain_db` is the head's own
# primitive — a flat, non-fading level shift — built by generalizing
# `_fade_level`'s hardcoded `0` plateau to `entry.gain_db` rather than adding
# a second filter type.


def test_gain_db_zero_is_byte_identical_to_before_the_field_existed() -> None:
    """The default (`gain_db=0.0`, unity) with no fades emits no filter at
    all — the exact document every caller before this field existed would
    have written, and the regression `gain_db`'s own default has to hold
    now that every `Entry` in every prior document carries it. And a fade
    that *does* fire still prints its plateau as `0`, never `0.0` — the
    `:g` formatting fix `gain_db` becoming a float made necessary."""
    plain = mlt.document(audio=_audio(120), music=_music(120), rate=RATE)
    assert "<filter" not in mlt.to_string(plain)

    faded = mlt.Entry("/media/bed.wav", 0, 120, fade_in_frames=30, fade_out_frames=30)
    document = mlt.document(audio=_audio(120), music=[faded], rate=RATE)
    playlist = next(p for p in document.findall("playlist") if p.get("id") == "playlist8")
    level = playlist.find("entry/filter/property[@name='level']")
    assert level is not None and level.text == "0=-60;30=0;89=0;119=-60"


def test_a_flat_gain_with_no_fades_still_emits_a_two_key_filter() -> None:
    """`gain_db` alone (no fades) is a constant plateau, held at both ends —
    `_playlist`'s condition has to catch this case too, or a flat gain with
    no fades would silently do nothing."""
    entry = mlt.Entry("/media/head.mp4", 0, 60, has_video=True, gain_db=15.1)
    document = mlt.document(audio=[entry], rate=RATE)
    playlist = next(p for p in document.findall("playlist") if p.get("id") == "playlist0")
    filters = playlist.findall("entry/filter")
    assert len(filters) == 1
    level = filters[0].find("property[@name='level']")
    assert level is not None and level.text == "0=15.1;59=15.1"


def test_gain_db_is_the_plateau_a_fade_ramps_to_and_holds_at() -> None:
    """With fades *and* a nonzero gain: the floor is still -60 (unchanged),
    but the plateau both fades ramp to is `gain_db`, not 0 — the
    generalization `_fade_level` makes."""
    entry = mlt.Entry(
        "/media/head.mp4", 0, 120, has_video=True,
        fade_in_frames=30, fade_out_frames=30, gain_db=-6.0,
    )  # fmt: skip
    document = mlt.document(audio=[entry], rate=RATE)
    playlist = next(p for p in document.findall("playlist") if p.get("id") == "playlist0")
    level = playlist.find("entry/filter/property[@name='level']")
    assert level is not None and level.text == "0=-60;30=-6;89=-6;119=-60"


def test_a_crossfade_edge_follows_the_equal_power_curve() -> None:
    """Two dB-linear fades crossing leave a hole; a crossfade edge takes
    `CROSSFADE_STEPS` keys along sin/cos in amplitude instead, so the two
    sides' powers sum to the plateau at every key (docs/plans/NATIVE.md § A1)."""
    rising = mlt.Entry("/b.wav", 0, 100, fade_in_frames=40, crossfade_in=True)
    falling = mlt.Entry("/a.wav", 0, 100, fade_out_frames=40, crossfade_out=True)
    up = [tuple(map(float, key.split("="))) for key in mlt._fade_level(rising).split(";")][: mlt.CROSSFADE_STEPS + 1]
    down = [tuple(map(float, key.split("="))) for key in mlt._fade_level(falling).split(";")][1:]

    assert up[0] == (0.0, float(mlt.FADE_FLOOR_DB)) and up[-1] == (40.0, 0.0)
    for (_, a), (_, b) in list(zip(up, down))[1:-1]:
        assert 10 ** (a / 10) + 10 ** (b / 10) == pytest.approx(1.0, abs=0.01)


def test_a_plain_fade_is_still_one_straight_line() -> None:
    entry = mlt.Entry("/a.wav", 0, 100, fade_in_frames=40, fade_out_frames=20)
    assert mlt._fade_level(entry) == "0=-60;40=0;79=0;99=-60"


def test_gain_keys_add_to_the_plateau_and_the_fades() -> None:
    """The duck's envelope rides the same `volume` filter as the level and the
    fades — summed in dB at the union of both key sets, which is exact for two
    straight-line envelopes, and floored where a fade already is."""
    entry = mlt.Entry(
        "/bed.wav", 10, 100, fade_in_frames=20, gain_db=-12.0, gain_keys=((0, 0.0), (50, -8.0), (99, -8.0))
    )
    keys = dict(tuple(map(float, key.split("="))) for key in mlt._fade_level(entry).split(";"))

    assert keys[10.0] == mlt.FADE_FLOOR_DB
    assert keys[30.0] == pytest.approx(-12.0 - 8.0 * 20 / 50)
    assert keys[60.0] == -20.0
    assert keys[109.0] == -20.0


def test_an_entry_with_only_gain_keys_still_gets_its_filter() -> None:
    entry = mlt.Entry("/bed.wav", 0, 10, gain_keys=((0, 0.0), (9, -6.0)))
    playlist = mlt._playlist("music", [entry], {"/bed.wav": "chain0"})
    level = playlist.find("entry/filter/property[@name='level']")
    assert level is not None and level.text == "0=0;9=-6"


def test_slicing_gain_keys_keeps_the_envelope_at_the_cut() -> None:
    keys = ((0, 0.0), (40, -8.0), (99, -8.0))
    assert mlt.slice_gain_keys(keys, 20, 30) == ((0, -4.0), (20, -8.0), (29, -8.0))
    assert mlt.slice_gain_keys((), 20, 30) == ()
