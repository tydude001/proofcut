"""A crossfade between picture cues, a scale punch per cut, and the vignette.

docs/plans/DAYDREAM.md § Transitions and per-cut effects, designed and
spiked, and its spike (`~/proofcut-work/spikes/transitions-probe`). Pinned
here: the crossfade is the incoming cue's pre-roll on a track directly over
the picture lane (under it, it draws nothing — the spike's control), falls
back to the outgoing shot's post-roll where the incoming clip has nothing
before its in-point, and skips the first shot; a punch is a `qtblend` on the
shot's own playlist entry, keyed from its `src_in`; `cue_set` sets either on
one cue or every cue in one undo; and the view hands the preview the
writer's own keys. What melt draws is read back in `test_server_stdio.py`
(`test_a_crossfade_and_a_punch_between_cues_are_drawn_as_their_keys_say`).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from proofcut import mlt, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

RATE = 30.0


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A 5 s VO with four words at timeline 0.2, 1.2, 2.2 and 3.6 s, two
    10 s video clips and a card — every cut a cue can land on."""
    project = Project.create(tmp_path / "proj")
    clips = [
        {"clip_id": "vo", "source": str(tmp_path / "vo.wav"), "duration": 5.0, "has_video": False, "has_audio": True},
    ]
    for name in ("film", "reel"):
        source = tmp_path / f"{name}.mp4"
        source.write_bytes(b"not really a video, just needs to exist")
        clips.append({"clip_id": name, "source": str(source), "duration": 10.0, "has_video": True, "has_audio": True})
    manifest = project.read_manifest()
    manifest["clips"] = clips
    manifest["timebase"] = 1000.0
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("vo", 0.0, 5.0)])
    tl.write(tl.to_otio(edit, {c["clip_id"]: c for c in clips}, rate=1000.0, name="proj"), project.timeline_path)
    words = [("one", 0.2, 0.5), ("two", 1.2, 1.5), ("three", 2.2, 2.5), ("four", 3.6, 3.9)]
    tx.save(
        tx.Transcript(clip_id="vo", words=tuple(tx.Word(index=i, text=t, start=s, end=e) for i, (t, s, e) in enumerate(words))),
        project.transcript_path("vo"),
    )
    project.cards_dir.joinpath("red.png").write_bytes(b"\x89PNG")
    return project


def _built(project: Project) -> dict:
    return ops._build_mlt(project, ops._load_edit(project), fps=RATE)


def _stack(document: ET.Element) -> list[str]:
    sequence = next(t for t in document.iter("tractor") if t.find("property[@name='kdenlive:uuid']") is not None)
    return [track.get("producer") for track in sequence.findall("track")]


# -- the writer ----------------------------------------------------------------


def test_a_punch_is_keyed_from_its_entrys_in_point_and_holds() -> None:
    punch = mlt.Punch(1.1, 6, "ease-out", "in")
    keys = mlt.punch_keys(punch, (1920, 1080))
    assert keys == [
        {"frame": 0, "ease": "ease-out", "rect": (0, 0, 1920, 1080)},
        {"frame": 6, "ease": None, "rect": (-96, -54, 2112, 1188)},
    ]
    settle = mlt.punch_keys(mlt.Punch(1.1, 6, "linear", "settle"), (1920, 1080))
    assert settle[0]["rect"] == (-96, -54, 2112, 1188) and settle[1]["rect"] == (0, 0, 1920, 1080)

    entry = mlt.Entry("/b.mp4", 40, 60, has_video=True, punch=punch)
    playlist = mlt._playlist("playlist2", [entry], {mlt._node_key(entry): "chain1"}, (1920, 1080))
    rect = playlist.find("entry/filter/property[@name='rect']").text
    # Counted from 0 a punch never moves (spike probe 4c), so the keys start at src_in.
    assert rect == "40h=0 0 1920 1080 1;46=-96 -54 2112 1188 1"


def test_a_punch_longer_than_its_shot_or_on_a_retimed_entry_is_refused() -> None:
    with pytest.raises(mlt.MLTError, match="lasts 90 frames"):
        mlt._check_punch(mlt.Entry("/b.mp4", 0, 60, has_video=True, punch=mlt.Punch(1.1, 90)))
    with pytest.raises(mlt.MLTError, match="retimed"):
        mlt._check_punch(mlt.Entry("/b.mp4", 0, 60, has_video=True, time_map=((0, 0.0),), punch=mlt.Punch(1.1, 6)))


def test_a_post_roll_is_opaque_on_the_join_and_fades_out() -> None:
    assert [k["alpha"] for k in mlt.dissolve_alpha(mlt.Dissolve("/a", 60, 60, 12, fade_out=True))] == [1, 0]
    assert [k["alpha"] for k in mlt.dissolve_alpha(mlt.Dissolve("/a", 28, 48, 12))] == [0, 1]


def test_a_cue_crossfade_sits_over_the_picture_lane_and_under_the_overlays(tmp_path: Path) -> None:
    a, b, e, o = (str(tmp_path / n) for n in ("a.mp4", "b.mp4", "e.mp4", "o.png"))
    document = mlt.document(
        audio=[mlt.Entry(e, 0, 120, has_video=True)],
        picture=[mlt.Entry(a, 0, 60, has_video=True), mlt.Entry(b, 40, 60, has_video=True)],
        dissolves=[mlt.Dissolve(b, 28, 48, 12)],
        cue_dissolves=[mlt.Dissolve(b, 28, 48, 12)],
        overlays=[mlt.Overlay(o, 0, 120)],
        rate=RATE,
        resolution=(640, 360),
    )
    stack = _stack(document)
    # An Edit join's dissolve stays under the lane; a cue's goes over it.
    assert stack.index("tractorX0") < stack.index("tractor1") < stack.index("tractorK0")
    assert stack.index("tractorK0") < stack.index(next(t for t in stack if t.startswith("tractorO")))
    alpha = document.find(".//filter[@id='fade_kchain0']/property[@name='alpha']").text
    assert alpha == "28=0;40=1"


# -- the ops layer -------------------------------------------------------------


def test_a_crossfade_is_the_incoming_pre_roll_and_falls_back_to_the_outgoing_post_roll(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    # Pinned 2 s in, so it has 0.4 s before its in-point to fade in with.
    ops.cue_add(project.root, "vo", 1, "reel", src_start=2.0, dissolve=0.4)
    # An unpinned first use of `reel`... is not one: reel's cursor has moved on.
    # The card has its own pre-roll: itself.
    ops.cue_add(project.root, "vo", 2, "card:red", dissolve=0.2)
    built = _built(project)
    fades = built["crossfades"]
    assert [(f["into"], f["from"], f["start"], f["join"]) for f in fades] == [
        ("reel", "incoming", 0.8, 1.2),
        ("card:red", "incoming", 2.0, 2.2),
    ]
    assert fades[0]["src_start"] == pytest.approx(1.6)
    assert _stack(built["document"]).count("tractorK0") == 1


def test_an_unpinned_first_use_crosses_from_the_outgoing_shot_and_says_so(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    result = ops.cue_add(project.root, "vo", 1, "reel", dissolve=0.3)
    (fade,) = result["crossfades"]
    # `film` played 0..1.2 s of itself, so its post-roll is 1.2..1.5 s, fading out from the word.
    assert (fade["from"], fade["asset"], fade["start"], fade["src_start"]) == ("outgoing", "film", 1.2, 1.2)
    alpha = _built(project)["document"].find(".//filter[@id='fade_kchain0']/property[@name='alpha']").text
    assert alpha == "36=1;45=0"


def test_the_first_shot_has_nothing_to_cross_from_and_is_skipped_not_refused(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "film", dissolve=0.3)
    built = _built(project)
    assert built["crossfades"] == []
    assert built["crossfades_skipped"] == [
        {"clip_id": "vo", "word_index": 0, "event": None, "reason": "the first shot has nothing to cross from"}
    ]


def test_a_punch_rides_its_shots_entry_and_the_export_says_so(project: Project, tmp_path: Path) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 1, "reel", punch=1.1)
    ops.cue_add(project.root, "vo", 3, "film")
    built = _built(project)
    playlist = next(p for p in built["document"].iter("playlist") if p.get("id") == "playlist2")
    filters = [entry.find("filter") for entry in playlist.findall("entry")]
    assert [f is not None for f in filters] == [False, True, False]
    # reel read from its head: in-point 0, 0.2 s is 6 frames.
    width, height = built["resolution"]
    assert filters[1].find("property[@name='rect']").text.startswith(f"0h=0 0 {width} {height} 1;6=")
    result = ops.export(project.root, tmp_path / "out.kdenlive")
    assert result["punches"] == [
        {"clip_id": "vo", "word_index": 1, "event": None, "scale": 1.1, "seconds": 0.2, "ease": "ease-out", "mode": "in"}
    ]


def test_the_refusals_are_named_before_anything_is_written(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    before = project.read_manifest()["cues"]
    with pytest.raises(ProjectError, match="keep one"):
        ops.cue_add(project.root, "vo", 1, "reel", src_start=2.0, dissolve=0.3, punch=1.1)
    with pytest.raises(ProjectError, match="longer than the shot"):
        ops.cue_add(project.root, "vo", 1, "reel", src_start=2.0, dissolve=3.0)
    with pytest.raises(ProjectError, match="a scale of the canvas"):
        ops.cue_add(project.root, "vo", 1, "reel", punch=4.0)
    with pytest.raises(ProjectError, match="give its scale as punch"):
        ops.cue_add(project.root, "vo", 1, "reel", punch_mode="settle")
    assert project.read_manifest()["cues"] == before


def test_a_post_roll_out_of_a_shot_punched_in_is_refused(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 1, "film", punch=1.1)
    with pytest.raises(ProjectError, match="drawn unpunched"):
        ops.cue_add(project.root, "vo", 2, "reel", dissolve=0.3)


def test_neither_under_a_retime(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 1, "reel", punch=1.1)
    manifest = project.read_manifest()
    manifest[ops.RETIME_KEY] = [{"clip_id": "vo", "word_index": 2, "until_word_index": 3, "speed": 2.0}]
    project.write_manifest(manifest)
    with pytest.raises(ProjectError, match="retime"):
        ops._cue_effects(project, *ops._picture_plan(project, RATE), RATE)


def test_cue_set_takes_every_cut_in_one_undo_and_clears(project: Project) -> None:
    for word, asset in ((0, "film"), (1, "reel"), (2, "film"), (3, "card:red")):
        ops.cue_add(project.root, "vo", word, asset)
    depth = len(project.snapshots())
    planned = ops.cue_set(project.root, every=True, punch=1.08, plan=True)
    assert planned["count"] == 4 and not planned["written"]
    assert not any(c.get("punch") for c in project.read_manifest()["cues"])

    result = ops.cue_set(project.root, every=True, punch=1.08, punch_mode="settle")
    assert [c["text"] for c in result["cues"]] == ["one", "two", "three", "four"]
    assert all(c["punch"]["mode"] == "settle" for c in project.read_manifest()["cues"])
    assert len(project.snapshots()) == depth + 1

    ops.cue_set(project.root, "vo", phrase="three", punch=1.0)
    punched = [c["word_index"] for c in project.read_manifest()["cues"] if c.get("punch")]
    assert punched == [0, 1, 3]
    assert "punch" in ops.cue_ls(project.root)["cues"][0]


def test_the_view_hands_the_preview_the_writers_own_keys(project: Project) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 1, "reel", src_start=2.0, dissolve=0.4)
    ops.cue_add(project.root, "vo", 2, "film", punch=1.1)
    view = ops.timeline_view(project.root)
    assert view["effects_error"] is None
    shots = view["shots"]
    fade = shots[1]["crossfade"]
    assert (fade["start"], fade["end"], fade["asset"], fade["src_start"]) == (0.8, 1.2, "reel", pytest.approx(1.6))
    assert [k["opacity"] for k in fade["keys"]] == [0, 1] and fade["keys"][1]["t"] == pytest.approx(0.4)
    keys = shots[2]["punch_keys"]
    assert keys[0]["w"] == 1 and keys[1]["w"] == pytest.approx(1.1, abs=1e-3)
    assert keys[1]["t"] == pytest.approx(0.2) and shots[0]["punch_keys"] is None


def test_the_vignette_is_an_overlay_template_with_its_strength_on_the_corners() -> None:
    from proofcut import graphics

    assert graphics.is_overlay("vignette")
    svg = graphics.fill_template("vignette", {"strength": 0.5}, width=1920, height=1080, flow=False)
    assert 'offset="0.45" stop-color="#' in svg and 'stop-opacity="0.500"' in svg
