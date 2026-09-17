"""Insets — a clip drawn into a rectangle of the recording, following its camera (NATIVE B6).

docs/plans/NATIVE.md § B6, designed, and the spike behind it
(`~/proofcut-work/spikes/inset-probe/FINDINGS.md`). Pinned here: the inset's
rect is the camera's own keys — same positions on the render clock, same
operators — mapped through the box, on the inset's *playlist*; a key before the
film collapses onto frame 0; the fade and the dim are `brightness` alpha and
never keys merged into the rect; a muted inset carries no audio and gets no
mix; a document with no insets is unchanged. The ops resolve an inset through
the `Edit`, refuse what one cannot follow (a cut, a retimed stretch, a split,
a wrong shape), take the bed out under one with sound, and a reel drops them.

What melt draws is read back by `test_server_stdio.py`
(`test_an_inset_is_drawn_where_the_recording_says_its_rect_is`).
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from proofcut import mlt, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

RATE = 30.0
RES = (1920, 1080)
BOX = (873, 252, 1785, 936)


# -- the writer ---------------------------------------------------------------------


def test_the_box_maps_through_the_destination_edge_by_edge() -> None:
    assert mlt.inset_dest((0, 0, 1920, 1080), BOX, (2560, 1440)) == (655, 189, 684, 513)
    # each edge rounded where it lands, so the far edge carries one rounding
    x, y, w, h = mlt.inset_dest((-3, -2, 1925, 1083), (1, 1, 3, 3), (7, 7))
    assert (x + w, y + h) == (round(-3 + 3 * 1925 / 7), round(-2 + 3 * 1083 / 7))


def _inset(**extra) -> mlt.Inset:
    fields = {
        "resource": "/r/render.mp4", "src_in": 20, "start": 10, "frames": 80, "box": BOX,
        "host": "/r/rec.mp4", "host_source": (2560, 1440),
    }  # fmt: skip
    fields.update(extra)
    return mlt.Inset(**fields)


def _push() -> mlt.Reframe:
    full = (873, 228, 1300, 731)
    return mlt.Reframe(
        source=(2560, 1440),
        crop=(0, 0, 2560, 1440),
        later=((0.5, (0, 0, 2560, 1440)), (2.0, full)),
        interp=(2.0,),
        eases=((2.0, "ease"),),
    )


def _doc(insets, audio=None, reframe=None, **extra) -> ET.Element:
    audio = audio or [mlt.Entry("/r/rec.mp4", 0, 90, has_video=True)]
    return mlt.document(audio=audio, insets=insets, rate=RATE, resolution=RES, reframe=reframe or {}, **extra)


def _keys(text: str) -> list[tuple[int, str, tuple[int, ...]]]:
    out = []
    for key in text.split(";"):
        head, _, body = key.partition("=")
        op = head.lstrip("0123456789")
        out.append((int(head[: len(head) - len(op)]), op, tuple(int(v) for v in body.split())))
    return out


def test_no_insets_writes_the_document_it_always_wrote() -> None:
    audio = [mlt.Entry("/r/rec.mp4", 0, 90, has_video=True)]
    before = mlt.to_string(mlt.document(audio=audio, rate=RATE, resolution=RES))
    assert mlt.to_string(_doc([], audio=audio)) == before


def test_the_rect_is_the_cameras_keys_mapped_through_the_box_on_the_playlist() -> None:
    reframe = _push()
    root = _doc([_inset()], reframe={"/r/rec.mp4": reframe})
    camera = _keys(root.find("chain[@id='chain0']/filter/property[@name='rect']").text)
    rect = root.find("playlist[@id='iplaylist0a']/filter/property[@name='rect']").text
    inset = _keys(rect)
    # same positions (the host entry reads from 0 at render 0), same operators
    assert [(p, op) for p, op, _ in inset] == [(p, op) for p, op, _ in camera]
    for (_, _, cam), (_, _, ins) in zip(camera, inset):
        assert ins[:4] == mlt.inset_dest(cam, BOX, (2560, 1440))
    # nothing on the chain but the node's own properties: no rect there
    assert root.find("chain[@id='ichain0']/filter") is None
    # the readback of which nodes crop is unchanged: a playlist is not a node
    assert set(mlt.reframed_nodes(root)) == {"chain0"}
    assert mlt.rect_at(rect, 75)[:4] == pytest.approx(mlt.inset_dest(camera[-1][2], BOX, (2560, 1440)))


def test_on_a_later_entry_the_keys_move_to_the_render_frames_they_land_on() -> None:
    reframe = _push()
    audio = [
        mlt.Entry("/r/other.mp4", 0, 40, has_video=True),
        mlt.Entry("/r/rec.mp4", 30, 90, has_video=True),
    ]
    root = _doc([_inset(start=50, frames=60)], audio=audio, reframe={"/r/rec.mp4": reframe})
    inset = _keys(root.find("playlist[@id='iplaylist0a']/filter/property[@name='rect']").text)
    # source frame f plays at render 40 + f - 30: the keys at source 0, 15, 60
    assert [p for p, _, _ in inset] == [10, 25, 70]


def test_a_slide_begun_before_the_film_is_cut_at_frame_0_at_the_value_it_reached() -> None:
    reframe = _push()
    audio = [mlt.Entry("/r/rec.mp4", 30, 90, has_video=True)]
    root = _doc([_inset(start=0, frames=60)], audio=audio, reframe={"/r/rec.mp4": reframe})
    inset = _keys(root.find("playlist[@id='iplaylist0a']/filter/property[@name='rect']").text)
    # source 0 and 15 land at render -30 and -15; the slide 15 → 60 is under way
    assert [(p, op) for p, op, _ in inset] == [(0, "i"), (30, "|")]
    wide = mlt.inset_dest(reframe._dest((0, 0, 2560, 1440), RES), BOX, (2560, 1440))
    full = inset[-1][2][:4]
    t = mlt.ease_fraction("ease", 15 / 45)
    assert inset[0][2][0] == pytest.approx(wide[0] + (full[0] - wide[0]) * t, abs=2)


def test_on_a_retimed_entry_the_keys_are_already_render_frames() -> None:
    reframe = _push()
    retimed = mlt.Entry("/r/rec.mp4", 0, 60, has_video=True, time_map=((0, 0.0), (30, 1.0), (60, 4.0), (61, 4.0333)))
    root = _doc([_inset(start=5, frames=20)], audio=[retimed], reframe={"/r/rec.mp4": reframe})
    camera = _keys(root.find("chain[@id='chain0']/filter/property[@name='rect']").text)
    inset = _keys(root.find("playlist[@id='iplaylist0a']/filter/property[@name='rect']").text)
    assert [p for p, _, _ in inset] == [p for p, _, _ in camera]


def test_an_inset_across_a_cut_is_refused() -> None:
    audio = [mlt.Entry("/r/rec.mp4", 0, 40, has_video=True), mlt.Entry("/r/rec.mp4", 60, 50, has_video=True)]
    with pytest.raises(mlt.MLTError, match="cut under it refuses"):
        _doc([_inset(start=30, frames=20)], audio=audio)


def test_a_split_or_fill_host_is_refused() -> None:
    fill = mlt.Reframe(source=(2560, 1440), crop=(0, 0, 2560, 1440), later=((1.0, (0, 0, 2560, 1440)),), fills=(1.0,))
    with pytest.raises(mlt.MLTError, match="split or a blur-fill"):
        _doc([_inset()], reframe={"/r/rec.mp4": fill})


def test_a_static_camera_writes_one_bare_rect() -> None:
    root = _doc([_inset()])
    rect = root.find("playlist[@id='iplaylist0a']/filter/property[@name='rect']").text
    assert rect == "655 189 684 513 1"


def test_the_fade_and_the_dim_are_alpha_never_rect_keys() -> None:
    root = _doc([_inset(fade_in_frames=15, fade_out_frames=10, fade_out_ease="ease-in", dim=0.55)])
    fade = root.find("chain[@id='ichain0']/filter[@id='fade_ichain0']")
    assert fade.find("property[@name='mlt_service']").text == "brightness"
    # chain positions: from the entry's in-point, 20
    assert fade.find("property[@name='alpha']").text == "20=0;35=1;89g=1;99=0"
    dim = root.find("producer[@id='idim0']/filter/property[@name='alpha']").text
    assert dim == "0=0;15=0.55;69g=0.55;79=0"
    rect = root.find("playlist[@id='iplaylist0a']/filter/property[@name='rect']").text
    assert rect.endswith(" 1") and ";" not in rect
    sequence = next(t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None)
    stack = [track.get("producer") for track in sequence.findall("track")]
    assert stack.index("tractorJ0") == stack.index("tractor0") + 1
    assert stack.index("tractorI0") == stack.index("tractorJ0") + 1
    # the dim's playlist is blanked to the inset's span
    dim_lane = root.find("playlist[@id='idplaylist0a']")
    assert [child.tag for child in dim_lane] == ["blank", "entry"]


def test_an_inset_sits_under_the_picture_lane() -> None:
    audio = [mlt.Entry("/r/rec.mp4", 0, 90, has_video=True)]
    picture = [mlt.Entry("/r/broll.mp4", 0, 90, has_video=True)]
    root = _doc([_inset()], audio=audio, picture=picture)
    sequence = next(t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None)
    stack = [track.get("producer") for track in sequence.findall("track")]
    assert stack.index("tractor0") < stack.index("tractorI0") < stack.index("tractor1")


def _mixes(root: ET.Element) -> list[str]:
    sequence = next(t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None)
    stack = [track.get("producer") for track in sequence.findall("track")]
    return [
        stack[int(t.find("property[@name='b_track']").text)]
        for t in sequence.findall("transition")
        if t.find("property[@name='mlt_service']").text == "mix"
    ]


def test_an_inset_with_sound_is_mixed_and_a_muted_one_is_silent() -> None:
    loud = _doc([_inset(gain_db=-3.0, fade_in_frames=6)])
    assert "tractorI0" in _mixes(loud)
    level = loud.find("playlist[@id='iplaylist0a']/entry/filter/property[@name='level']").text
    assert level.startswith("20=-60;26=-3")
    quiet = _doc([_inset(has_audio=False, gain_db=-3.0)])
    assert "tractorI0" not in _mixes(quiet)
    node = quiet.find("chain[@id='ichain0']")
    assert node.find("property[@name='audio_index']").text == "-1"
    assert quiet.find("playlist[@id='iplaylist0a']/entry/filter") is None


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"start": 50, "frames": 60}, "inside the film"),
        ({"box": (10, 10, 10, 20)}, "empty"),
        ({"fade_in_frames": 50, "fade_out_frames": 40}, "shorten the fades"),
        ({"fade_in_ease": "bounce", "fade_in_frames": 3}, "no easing"),
        ({"dim": 1.5}, "fraction"),
    ],
)
def test_an_inset_the_writer_cannot_draw_is_refused(extra: dict, message: str) -> None:
    with pytest.raises(mlt.MLTError, match=message):
        _doc([_inset(**extra)])


def test_rect_at_reads_back_what_the_operators_draw() -> None:
    keys = "0|=0 0 100 100 1;10i=0 0 100 100 1;30=100 0 100 100 1"
    assert mlt.rect_at(keys, 5)[0] == 0
    assert mlt.rect_at(keys, 20)[0] == pytest.approx(50)
    assert mlt.rect_at(keys, 15)[0] == pytest.approx(100 * mlt.ease_fraction("ease", 0.25))
    assert mlt.rect_at(keys, 40)[0] == 100
    assert mlt.rect_at("1 2 3 4 1", 7) == (1, 2, 3, 4, 1)


# -- the ops ------------------------------------------------------------------------


def _video(path: Path, size: str, seconds: float, hz: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}:sample_rate=48000",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("inset-media")
    return {
        "rec": _video(root / "rec.mp4", "640x360", 10.0, 440),
        "cut": _video(root / "cut.mp4", "160x120", 4.0, 1000),
        "wide": _video(root / "wide.mp4", "320x120", 2.0, 700),
    }


@pytest.fixture
def project(tmp_path: Path, media: dict[str, Path]) -> Project:
    root = tmp_path / "proj"
    Project.create(root)
    for source in media.values():
        ops.import_media(root, source, sheet=False)
    project = Project.open(root)
    words = [("type", 0.5, 0.9), ("send", 3.0, 3.3), ("wait", 4.0, 4.4), ("lands", 8.0, 8.5), ("done", 9.0, 9.5)]
    tx.save(
        tx.Transcript(
            clip_id="rec",
            words=tuple(tx.Word(index=i, text=t, start=s, end=e) for i, (t, s, e) in enumerate(words)),
        ),
        project.transcript_path("rec"),
    )
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    tl.write(tl.to_otio(tl.Edit([tl.Segment("rec", 0.0, 10.0)]), clips, rate=1000.0), project.timeline_path)
    ops.events(root, "rec", name="playing", at=2.0)
    return Project.open(root)


RECT = [218, 63, 446, 234]


def test_an_inset_resolves_its_event_and_says_where_it_plays(project: Project) -> None:
    result = ops.inset_add(project.root, "rec", "cut", RECT, event="playing")
    inset = result["inset"]
    assert (inset["timeline_start"], inset["timeline_end"]) == pytest.approx((2.0, 6.0))
    assert inset["frames"] == 120
    assert inset["audible"] is True
    assert inset["dest"] == [218, 63, 228, 171]
    assert inset["start_echo"]["name"] == "playing"
    listed = ops.inset_ls(project.root)
    assert listed["insets_error"] is None and listed["insets"][0]["asset"] == "cut"
    assert ops._is_layered(project, ops._load_edit(project))
    view = ops.timeline_view(project.root, "rec")
    assert view["insets"][0]["dest"] == [218, 63, 228, 171]


def test_a_planned_inset_writes_nothing_and_rm_removes_the_key(project: Project) -> None:
    ops.inset_add(project.root, "rec", "cut", RECT, phrase="send", plan=True)
    assert "insets" not in project.read_manifest()
    ops.inset_add(project.root, "rec", "cut", RECT, phrase="send", seconds=1.0)
    removed = ops.inset_rm(project.root, 0)
    assert removed["removed"]["word_index"] == 1
    assert "insets" not in project.read_manifest()
    with pytest.raises(ProjectError, match="no inset at position 0"):
        ops.inset_rm(project.root, 0)


def test_an_inset_ends_at_its_address_or_its_length(project: Project) -> None:
    by_word = ops.inset_add(project.root, "rec", "cut", RECT, event="playing", until_phrase="wait", plan=True)
    assert by_word["inset"]["timeline_end"] == pytest.approx(4.4)
    by_length = ops.inset_add(project.root, "rec", "cut", RECT, event="playing", seconds=1.5, src_in=1.0, plan=True)
    assert by_length["inset"]["frames"] == 45


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rect": [218, 63, 446, 263]}, "clip's own shape"),
        ({"rect": [500, 63, 728, 234]}, "outside"),
        ({"asset": "wide", "rect": [0, 0, 320, 120], "seconds": 3.0}, "has only"),
        ({"src_in": 3.5, "seconds": 1.0}, "has only"),
        ({"src_in": 3.5}, "shorten the fades"),
        ({"enter": "rise"}, "does not rise"),
        ({"dim": 2.0}, "fraction"),
    ],
)
def test_an_inset_that_cannot_be_drawn_is_refused(project: Project, kwargs: dict, message: str) -> None:
    fields = {"asset": "cut", "rect": RECT, **kwargs}
    with pytest.raises(ProjectError, match=message):
        ops.inset_add(project.root, "rec", event="playing", **fields)


def test_an_inset_across_a_cut_is_refused_by_name(project: Project) -> None:
    edit = ops._load_edit(project)
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    edit.remove("rec", 5.0, 5.5)
    tl.write(tl.to_otio(edit, clips, rate=1000.0), project.timeline_path)
    with pytest.raises(ProjectError, match="across the cut at 5.000s"):
        ops.inset_add(project.root, "rec", "cut", RECT, event="playing")
    assert ops.inset_add(project.root, "rec", "cut", RECT, event="playing", seconds=2.9, plan=True)


def test_an_inset_whose_word_was_cut_is_reported_not_raised(project: Project) -> None:
    ops.inset_add(project.root, "rec", "cut", RECT, phrase="send", seconds=1.0)
    edit = ops._load_edit(project)
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    edit.remove("rec", 2.9, 3.4)
    tl.write(tl.to_otio(edit, clips, rate=1000.0), project.timeline_path)
    assert "a cut removed" in ops.inset_ls(project.root)["insets_error"]
    assert "a cut removed" in ops.timeline_view(project.root, "rec")["insets_error"]


def test_an_inset_over_a_retimed_stretch_is_refused(project: Project) -> None:
    ops.events(project.root, "rec", name="words", at=8.0)
    ops.retime_add(project.root, "rec", 1.0, event="playing", until_event="words")
    with pytest.raises(ProjectError, match="off 1x"):
        ops.inset_add(project.root, "rec", "cut", RECT, event="playing")
    # after the stretch and its ramp, at 1x, it is placed on the render clock
    placed = ops.inset_add(project.root, "rec", "cut", RECT, phrase="done", seconds=0.9, plan=True)
    warp = ops._project_warp(project, ops._load_edit(project))
    assert placed["inset"]["start_frame"] == warp.frame_of(9.0)


def test_an_inset_over_a_blur_fill_window_is_refused(project: Project) -> None:
    ops.reframe(project.root, "rec", fill="blur", src_start=5.0)
    with pytest.raises(ProjectError, match="blur-fill"):
        ops.inset_add(project.root, "rec", "cut", RECT, event="playing")
    assert ops.inset_add(project.root, "rec", "cut", RECT, event="playing", seconds=2.5, plan=True)


def test_the_build_draws_it_takes_the_bed_out_and_reports_where(project: Project, media: dict[str, Path]) -> None:
    ops.music(project.root, asset="wide", clip_id="rec", word_index_start=0)
    ops.inset_add(project.root, "rec", "cut", RECT, event="playing", seconds=1.0)
    built = ops._build_mlt(project, ops._load_edit(project), fps=RATE)
    report = built["insets"][0]
    assert (report["timeline_start"], report["frames"], report["audible"]) == (2.0, 30, True)
    assert report["rect_first"][:4] == [218, 63, 228, 171]
    assert report["rect_last"][:4] == [218, 63, 228, 171]
    document = built["document"]
    assert document.find("playlist[@id='iplaylist0a']") is not None
    bed = document.find("playlist[@id='playlist8']").findall("entry")
    assert len(bed) >= 3, "the bed is split around the inset, its middle silence"


def test_a_muted_inset_leaves_the_bed_alone(project: Project) -> None:
    ops.music(project.root, asset="wide", clip_id="rec", word_index_start=0)
    before = mlt.to_string(ops._build_mlt(project, ops._load_edit(project), fps=RATE)["document"])
    ops.inset_add(project.root, "rec", "cut", RECT, event="playing", seconds=1.0, mute=True)
    after = ops._build_mlt(project, ops._load_edit(project), fps=RATE)["document"]
    bed_before = ET.fromstring(before).find("playlist[@id='playlist8']")

    def entries(bed: ET.Element) -> list[dict[str, str]]:
        return [dict(entry.attrib) for entry in bed.iter("entry")]

    assert entries(after.find("playlist[@id='playlist8']")) == entries(bed_before)


def test_a_reel_drops_the_insets(project: Project, tmp_path: Path) -> None:
    ops.inset_add(project.root, "rec", "cut", RECT, event="playing", seconds=1.0)
    result = ops.reel(project.root, tmp_path / "teaser", start=8.5, end=9.8)
    assert result["insets_dropped"][0]["asset"] == "cut"
    assert "insets" not in Project.open(tmp_path / "teaser").read_manifest()


@pytest.mark.skipif(
    shutil.which("magick") is None or shutil.which("ffmpeg") is None,
    reason="the sheet is ffmpeg's frames drawn on by magick",
)
def test_the_framing_sheet_draws_the_inset_rect_inside_its_span(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops.inset_add(project.root, "rec", "cut", RECT, event="playing", seconds=1.0)
    drawn: list[tuple[float, list]] = []
    real = ops._draw_window

    def spy(tile, crop, source, label, pane=None, insets=()):
        drawn.append((label, list(insets)))
        real(tile, crop, source, label, pane, insets)

    monkeypatch.setattr(ops, "_draw_window", spy)
    ops.reframe_sheet(project.root, moments=[0.1, 0.25, 0.9])
    inside = [insets for label, insets in drawn if "@2.50s" in label]
    outside = [insets for label, insets in drawn if "@9.00s" in label]
    assert inside == [[(218, 63, 228, 171)]]
    assert outside == [[]]
    assert any("+ inset 218,63,228,171" in label for label, _ in drawn)
