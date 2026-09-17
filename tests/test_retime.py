"""Retime — spans of the film played at another speed (NATIVE B5).

docs/plans/NATIVE.md § B5, designed, and the spike behind it
(`~/proofcut-work/spikes/retime-compose/FINDINGS.md`). Pinned here: the warp
is monotone, plays everything outside its ramps at exactly 1x and each stretch
in exactly its seconds; a lane laid on it keeps its total and ends every map
one key past its last frame; the writer gives each retimed entry its own chain
with the link after its properties and never a `length`, and keys a reframe on
that chain's render clock; the ops resolve stretches through the `Edit`, refuse
what a warp cannot draw, and place overlays and sounds where their moment plays.

What melt draws is read back frame by frame by `test_server_stdio.py`
(`test_a_retimed_render_shows_the_frame_the_warp_names`).
"""

from __future__ import annotations

import wave
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path

import pytest

from proofcut import mlt, ops
from proofcut import retime as rt
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

RATE = 30.0


def _warp(*stretches: tuple[float, float, float], seconds: float = 90.0) -> rt.Warp:
    return rt.Warp([rt.Stretch(*s) for s in stretches], round(seconds * RATE), RATE)


# -- the warp -------------------------------------------------------------------


def test_a_stretch_plays_in_its_seconds_and_the_rest_at_exactly_1x() -> None:
    warp = _warp((10.0, 40.0, 1.0), (60.0, 62.0, 4.0))
    assert warp.frames == round((90 - 30 - 2 + 1 + 4) * RATE)
    assert warp.render_at(10.0) == pytest.approx(10.0, abs=1e-6)
    assert warp.render_at(40.0) - warp.render_at(10.0) == pytest.approx(1.0, abs=1 / RATE)
    assert warp.render_at(62.0) - warp.render_at(60.0) == pytest.approx(4.0, abs=1 / RATE)
    ramp = rt.RAMP_SECONDS
    for k in range(warp.frames):
        edit = warp.edit_at_frame(k)
        far_from_every_stretch = all(
            edit < s.start - ramp - 1 / RATE or edit > s.end + ramp + 1 / RATE for s in warp.stretches
        ) and warp.edit_at_frame(k + 1) < 90.0
        if far_from_every_stretch and not any(
            s.start - ramp - 1 / RATE <= warp.edit_at_frame(k + 1) <= s.end + ramp for s in warp.stretches
        ):
            assert warp.speed_at_frame(k) == pytest.approx(1.0, abs=1e-9), k


def test_the_warp_never_runs_the_edit_backwards() -> None:
    # clip.py's shape, the one MLT's own spline ran backwards on.
    warp = _warp((1.0, 9.0, 1.2), (9.5, 39.5, 1.0), (40.0, 41.0, 2.2), (42.0, 80.0, 1.1))
    samples = [warp.edit_at_frame(k) for k in range(warp.frames + 1)]
    assert all(b >= a for a, b in pairwise(samples))
    assert samples[-1] == pytest.approx(90.0)


@pytest.mark.parametrize(
    ("stretches", "message"),
    [
        ([(10.0, 20.0, 0.0)], "positive"),
        ([(20.0, 10.0, 1.0)], "end after it starts"),
        ([(10.0, 20.0, 1.0), (15.0, 25.0, 1.0)], "overlap"),
        ([(80.0, 95.0, 1.0)], "past the Edit"),
    ],
)
def test_a_warp_that_cannot_be_drawn_is_refused(stretches: list, message: str) -> None:
    with pytest.raises(rt.RetimeError, match=message):
        _warp(*stretches)


def test_render_at_inverts_edit_at_frame() -> None:
    warp = _warp((10.0, 40.0, 1.0))
    for k in (0, 7, 299, 301, 315, 330, 600, warp.frames - 1):
        assert warp.render_at(warp.edit_at_frame(k)) == pytest.approx(k / RATE, abs=1e-6)


def test_only_off_speed_frames_are_muted_and_a_ramp_does_not_blip() -> None:
    warp = _warp((10.0, 40.0, 1.0))
    muted = warp.muted()
    assert not any(muted[: round(9 * RATE)])
    assert all(muted[round(10.2 * RATE) : round(10.8 * RATE)])
    # one run around the stretch, not several with 1x frames between
    runs = sum(1 for k in range(1, len(muted)) if muted[k] and not muted[k - 1])
    assert runs == 1
    spans = warp.muted_edit_spans()
    assert len(spans) == 1
    assert spans[0][0] < 10.0
    assert spans[0][1] > 40.0


def _level(keys: tuple[tuple[int, float], ...], frame: int) -> float:
    """What MLT draws at a frame: straight lines between the keys."""
    for (p0, v0), (p1, v1) in pairwise(keys):
        if p0 <= frame <= p1:
            return v0 + (v1 - v0) * (frame - p0) / (p1 - p0)
    return keys[-1][1]


@pytest.mark.parametrize("gap", [1, 2, 3, 4, 5, 8])
def test_every_muted_frame_is_at_the_floor_however_close_two_muted_runs_are(gap: int) -> None:
    # Measured: a two-frame 1x run between muted runs lost the floor, and the
    # tone came back up to 2420 of 2900 across a muted span.
    muted = [False] * 10 + [True] * 8 + [False] * gap + [True] * 30 + [False] * 10
    keys = rt._mute_keys(muted, RATE)
    bridged = rt._bridged(muted, rt._fade_frames(RATE))
    for frame, is_muted in enumerate(bridged):
        if is_muted:
            assert _level(keys, frame) == mlt.FADE_FLOOR_DB, (gap, frame)
            # and the frame after it starts at the floor, so no ramp crosses it
            assert _level(keys, frame - 1) == mlt.FADE_FLOOR_DB or not bridged[frame - 1] or frame == 0
    for frame in range(len(muted)):
        if not any(bridged[max(0, frame - 3) : frame + 4]):
            assert _level(keys, frame) == 0.0, (gap, frame)


def test_the_mute_is_down_a_frame_before_the_first_muted_frame() -> None:
    # MLT ramps a level across the frame carrying its key: a key on the first
    # muted frame left it at half level (0.036 of 0.063), measured.
    muted = [False] * 10 + [True] * 10 + [False] * 10
    keys = dict(rt._mute_keys(muted, RATE))
    assert keys[9] == mlt.FADE_FLOOR_DB
    assert keys[20] == mlt.FADE_FLOOR_DB
    assert keys[21] == 0.0
    assert max(k for k, v in keys.items() if v == 0.0 and k < 10) <= 8


# -- a lane on the warp -----------------------------------------------------------


def test_a_lane_keeps_its_total_and_each_map_ends_one_key_past_its_frames() -> None:
    warp = _warp((5.0, 35.0, 1.0), (45.0, 47.0, 4.0), seconds=60.0)
    entries = [mlt.Entry("a.mp4", 0, 1200, has_video=True), mlt.Entry("a.mp4", 1800, 600, has_video=True)]
    lane, dropped = rt.warp_lane(entries, warp, mute=True)
    assert dropped == []
    assert sum(e.frames for e in lane) == warp.frames
    for entry in lane:
        assert entry.src_in == 0
        assert entry.time_map[0][0] == 0
        # a map ending on the last frame plays that frame silent (measured)
        assert entry.time_map[-1][0] == entry.frames
        seconds = [s for _, s in entry.time_map]
        assert seconds == sorted(seconds)
    assert lane[1].time_map[0][1] == pytest.approx(60.0 + rt.FRAME_BIAS / RATE)
    assert lane[0].gain_keys, "the first entry holds the 30x stretch, so it is muted there"


def test_a_still_on_a_warped_lane_only_changes_length() -> None:
    warp = _warp((1.0, 5.0, 1.0), seconds=10.0)
    lane, _ = rt.warp_lane([mlt.Entry("card.png", 0, 300, is_image=True, has_video=True)], warp, mute=False)
    assert lane[0].frames == warp.frames and not lane[0].time_map


def test_a_lane_that_is_not_the_warps_length_is_refused() -> None:
    warp = _warp((1.0, 5.0, 1.0), seconds=10.0)
    with pytest.raises(rt.RetimeError, match="covers 200"):
        rt.warp_lane([mlt.Entry("a.mp4", 0, 200, has_video=True)], warp, mute=False)


# -- the writer ---------------------------------------------------------------------


def _retimed_document(**extra) -> ET.Element:
    warp = _warp((2.0, 8.0, 1.0), seconds=12.0)
    audio, _ = rt.warp_lane(
        [mlt.Entry("/m/a.mp4", 0, 180, has_video=True), mlt.Entry("/m/a.mp4", 200, 180, has_video=True)],
        warp,
        mute=True,
    )
    return mlt.document(audio=audio, rate=RATE, resolution=(640, 360), **extra)


def test_each_retimed_entry_reads_its_own_chain_with_the_link_after_its_properties() -> None:
    root = _retimed_document()
    chains = [c for c in root.findall("chain") if c.get("id", "").startswith("chain")]
    assert len(chains) == 2
    for chain in chains:
        children = list(chain)
        link = chain.find("link")
        assert link is not None and link.get("mlt_service") == "timeremap"
        position = children.index(link)
        assert all(child.tag == "property" for child in children[:position])
        assert all(child.tag != "property" for child in children[position + 1 :])
        assert chain.find("property[@name='length']") is None
        time_map = link.find("property[@name='time_map']").text
        assert "~" not in time_map and "|" not in time_map
        assert "_time_map" not in chain.attrib
    entries = root.find("playlist[@id='playlist0']").findall("entry")
    assert [e.get("in") for e in entries] == ["0", "0"]
    # the bin holds the raw media, never a retime
    assert all(node.find("link") is None for node in root.findall("chain") if node.get("id", "").startswith("bin"))


def test_a_retime_on_an_audio_lane_is_refused() -> None:
    warp = _warp((2.0, 8.0, 1.0), seconds=12.0)
    music, _ = rt.warp_lane([mlt.Entry("/m/bed.wav", 0, 360)], warp, mute=False)
    with pytest.raises(mlt.MLTError, match="only the edit and the picture lane"):
        mlt.document(audio=[mlt.Entry("/m/vo.wav", 0, warp.frames)], music=music, rate=RATE)


def test_a_reframe_on_a_retimed_chain_is_keyed_where_the_warp_shows_each_window() -> None:
    reframe = mlt.Reframe(
        source=(1280, 720),
        crop=(0, 0, 640, 360),
        later=((5.0, (640, 360, 640, 360)), (20.0, (0, 360, 640, 360))),
    )
    place = mlt.Placement(((0, 3.0), (60, 5.0), (120, 20.0), (180, 21.0)), 150)
    keys = reframe.rect_property((640, 360), RATE, place).split(";")
    positions = [int(key.split("|")[0]) for key in keys]
    # the head window is in force at the entry's start, 5 s plays at 60, and
    # 20 s at 120 — which is still inside the 150-frame entry
    assert positions == [0, 60, 120]
    short = mlt.Placement(((0, 3.0), (60, 5.0), (120, 20.0)), 100)
    assert [int(k.split("|")[0]) for k in reframe.rect_property((640, 360), RATE, short).split(";")] == [0, 60]


def test_a_slide_the_entry_starts_inside_is_cut_at_the_value_it_has_reached() -> None:
    reframe = mlt.Reframe(
        source=(1280, 720),
        crop=(0, 0, 640, 360),
        later=((4.0, (640, 0, 640, 360)),),
        interp=(4.0,),
    )
    # the entry starts at source 2 s, halfway through the slide from 0 to 4 s
    place = mlt.Placement(((0, 2.0), (60, 4.0), (120, 6.0)), 120)
    first, second = reframe.rect_property((640, 360), RATE, place).split(";")
    assert first.startswith("0=")
    x_start = int(first.split("=")[1].split()[0])
    full_left = reframe._dest((0, 0, 640, 360), (640, 360))[0]
    full_right = reframe._dest((640, 0, 640, 360), (640, 360))[0]
    assert x_start == round((full_left + full_right) / 2)
    assert second.startswith("60|=")


def test_without_a_retime_the_keys_are_the_source_frames_they_always_were() -> None:
    reframe = mlt.Reframe(source=(1280, 720), crop=(0, 0, 640, 360), later=((5.0, (640, 360, 640, 360)),))
    assert [k.split("|")[0] for k in reframe.rect_property((640, 360), RATE).split(";")] == ["0", "150"]


# -- the ops ---------------------------------------------------------------------------


def _wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(bytes(2 * round(seconds * 48000)))
    return path


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    Project.create(root)
    ops.import_media(root, _wav(tmp_path / "rec.wav", 20.0), sheet=False)
    ops.import_media(root, _wav(tmp_path / "tick.wav", 0.1), sheet=False)
    project = Project.open(root)
    words = [("type", 0.5, 0.9), ("send", 3.0, 3.3), ("wait", 4.0, 4.4), ("lands", 14.0, 14.5), ("done", 18.0, 18.5)]
    tx.save(
        tx.Transcript(
            clip_id="rec",
            words=tuple(tx.Word(index=i, text=t, start=s, end=e) for i, (t, s, e) in enumerate(words)),
        ),
        project.transcript_path("rec"),
    )
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    tl.write(tl.to_otio(tl.Edit([tl.Segment("rec", 0.0, 20.0)]), clips, rate=1000.0), project.timeline_path)
    ops.events(root, "rec", name="sent", at=3.3)
    ops.events(root, "rec", name="words", at=14.0)
    return Project.open(root)


def test_a_stretch_resolves_its_events_and_says_where_it_plays(project: Project) -> None:
    result = ops.retime_add(project.root, "rec", 1.0, event="sent", until_event="words")
    stretch = result["stretch"]
    assert (stretch["edit_start"], stretch["edit_end"]) == pytest.approx((3.3, 14.0))
    assert stretch["render_end"] - stretch["render_start"] == pytest.approx(1.0, abs=1 / RATE)
    assert stretch["speed"] == pytest.approx(10.7)
    assert result["render_seconds"] == pytest.approx(20.0 - 10.7 + 1.0, abs=1 / RATE)
    assert ops.retime_ls(project.root)["stretches"][0]["event"] == "sent"
    assert ops._is_layered(project, ops._load_edit(project))


def test_a_planned_stretch_writes_nothing(project: Project) -> None:
    ops.retime_add(project.root, "rec", 1.0, phrase="send", until_phrase="lands", plan=True)
    assert "retime" not in project.read_manifest()


def test_a_stretch_whose_end_comes_first_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="backwards"):
        ops.retime_add(project.root, "rec", 1.0, event="words", until_event="sent")


def test_a_stretch_whose_word_was_cut_is_refused_by_name(project: Project) -> None:
    ops.retime_add(project.root, "rec", 1.0, word_index=1, until_word_index=3)
    edit = ops._load_edit(project)
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    edit.remove("rec", 13.9, 14.6)
    tl.write(tl.to_otio(edit, clips, rate=1000.0), project.timeline_path)
    listed = ops.retime_ls(project.root)
    assert "retime stretch 0 ends" in listed["retime_error"]
    status = ops.status(project.root)
    assert status["expected_frames"] is None and status["retime"]["error"]


def test_removing_the_last_stretch_removes_the_key(project: Project) -> None:
    ops.retime_add(project.root, "rec", 1.0, event="sent", until_event="words")
    ops.retime_rm(project.root, 0)
    assert "retime" not in project.read_manifest()
    with pytest.raises(ProjectError, match="no stretch at position 0"):
        ops.retime_rm(project.root, 0)


def test_status_states_the_render_length(project: Project) -> None:
    before = ops.status(project.root)["expected_frames"]
    ops.retime_add(project.root, "rec", 1.0, event="sent", until_event="words")
    after = ops.status(project.root)
    assert before == 600
    assert after["expected_frames"] == round((20.0 - 10.7 + 1.0) * RATE)
    assert after["retime"]["render_seconds"] == pytest.approx(after["expected_duration"])


def test_the_build_lays_the_edit_on_the_warp_and_places_a_sound_where_its_event_plays(
    project: Project,
) -> None:
    ops.retime_add(project.root, "rec", 1.0, event="sent", until_event="words")
    ops.sound_add(project.root, "tick", "rec", event="words")
    built = ops._build_mlt(project, ops._load_edit(project), fps=RATE)
    assert built["frames"] == round((20.0 - 10.7 + 1.0) * RATE)
    assert built["retime"]["stretches"][0]["speed"] == pytest.approx(10.7)
    warp = ops._warp(project, ops._load_edit(project), RATE, edit_frames=600)
    lane = built["document"].find("playlist[@id='splaylist0a']")
    first = lane.findall("entry")[0]
    lead_frames = int(first.get("out")) + 1
    assert lead_frames == pytest.approx(warp.render_at(14.0) * RATE, abs=1)


def test_a_hold_and_a_retime_are_refused_together(project: Project) -> None:
    manifest = project.read_manifest()
    manifest["holds"] = [{"clip_id": "rec"}]
    project.write_manifest(manifest)
    with pytest.raises(ProjectError, match="cannot carry one yet"):
        ops.retime_add(project.root, "rec", 1.0, event="sent", until_event="words")


# -- the clocks ------------------------------------------------------------------------


def _stretched(project: Project) -> rt.Warp:
    ops.retime_add(project.root, "rec", 1.0, event="sent", until_event="words")
    warp = ops._project_warp(project, ops._load_edit(project))
    assert warp is not None
    return warp


def test_captions_burn_on_the_render_clock_and_leave_muted_words_out(project: Project, tmp_path: Path) -> None:
    warp = _stretched(project)
    result = ops.add_captions(project.root, tmp_path / "subs.ass")
    # send, wait and lands play inside the stretch or its ramps
    assert result["retimed_words_dropped"] == 3
    text = (tmp_path / "subs.ass").read_text()
    assert "send" not in text and "wait" not in text
    done = warp.render_at(18.0)
    assert done == pytest.approx(18.0 - 10.7 + 1.0, abs=1 / RATE)
    stamp = f"0:00:{int(done):02d}"
    assert stamp in text, "the last word is timed where the retimed render plays it"
    assert "0:00:18" not in text


def test_verify_leaves_the_muted_words_out_of_what_it_expects(project: Project, tmp_path: Path) -> None:
    _stretched(project)
    heard = tmp_path / "heard.json"
    tx.save(
        tx.Transcript(
            clip_id="render",
            words=(tx.Word(index=0, text="type", start=0.5, end=0.9), tx.Word(index=1, text="done", start=8.3, end=8.8)),
        ),
        heard,
    )
    result = ops.verify(project.root, tmp_path / "render.mp4", transcript_path=heard)
    assert result["retimed_words_dropped"] == 3
    assert result["expected_words"] == 2
    assert result["similarity"] == pytest.approx(1.0)


def test_locate_says_where_the_retimed_render_plays_a_word(project: Project) -> None:
    warp = _stretched(project)
    found = ops.locate(project.root, "rec", first=4)
    assert found["timeline_start"] == pytest.approx(18.0)
    assert found["render_start"] == pytest.approx(round(warp.render_at(18.0), 3))
    assert found["muted"] is False
    assert ops.locate(project.root, "rec", first=2)["muted"] is True


def test_a_note_off_the_retimed_render_cuts_what_it_showed(project: Project) -> None:
    warp = _stretched(project)
    at = warp.render_at(18.0)
    result = ops.cut_by_time(project.root, spans=[[at - 0.1, at + 0.6]], plan=True)
    applied = result["applied"][0]
    assert applied["edit_start"] == pytest.approx(17.9, abs=0.01)
    assert applied["edit_end"] == pytest.approx(18.6, abs=0.01)
    words = [w["text"] for piece in applied["pieces"] for w in piece["words_overlapped"]]
    assert words == ["done"]


def test_a_reel_keeps_what_the_retimed_render_showed_and_drops_the_retime(
    project: Project, tmp_path: Path
) -> None:
    warp = _stretched(project)
    result = ops.reel(project.root, tmp_path / "teaser", start=warp.render_at(17.5), end=warp.render_at(19.0))
    assert result["retime_dropped"] and result["retime_dropped"][0]["event"] == "sent"
    teaser = Project.open(tmp_path / "teaser")
    assert "retime" not in teaser.read_manifest()
    assert ops._load_edit(teaser).duration == pytest.approx(1.5, abs=0.01)


def test_the_view_says_nothing_without_a_retime_and_the_stretches_with_one(project: Project) -> None:
    assert ops.timeline_view(project.root)["retime"] is None
    ops.retime_add(project.root, "rec", 1.0, event="sent", until_event="words")
    retime = ops.timeline_view(project.root)["retime"]
    assert retime["error"] is None
    assert retime["render_seconds"] == pytest.approx(10.3, abs=1 / RATE)
    assert retime["edit_seconds"] == pytest.approx(20.0)
    assert [(s["edit_start"], s["edit_end"]) for s in retime["stretches"]] == [
        pytest.approx((3.3, 14.0))
    ]
    assert ops.caption_view(project.root)["retime"]["render_seconds"] == retime["render_seconds"]
