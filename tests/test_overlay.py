"""Overlays — a transparent card drawn over the film (NATIVE B3).

docs/plans/NATIVE.md § B3, designed, and the spike behind it
(`~/proofcut-work/spikes/overlay-probe/FINDINGS.md`). Pinned here: the
overlay templates draw no background; the writer packs overlays onto as few
lanes as keep list order as stacking order, animates only with a `qtblend`
`rect` whose *moving* key alone is nudged off 1:1, and leaves a document with
no overlays byte-identical; and the ops layer resolves every span through the
`Edit`, refuses an opaque card, an orphaned word and an animation longer than
the overlay, and routes a project with one through the MLT writer.

The project is built by hand, `test_ops_music.py`'s way. What melt draws is
read back in `test_server_stdio.py`
(`test_an_overlay_is_drawn_where_and_as_strongly_as_its_keys_say`).
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from proofcut import graphics, mlt, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

needs_magick = pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")

CLIPS = {
    "vo": {
        "clip_id": "vo",
        "source": "/tmp/vo.wav",
        "duration": 6.0,
        "has_video": False,
        "has_audio": True,
    },
}


def _words(*specs: tuple[str, float, float]) -> tx.Transcript:
    return tx.Transcript(
        clip_id="vo",
        words=tuple(tx.Word(index=i, text=t, start=s, end=e) for i, (t, s, e) in enumerate(specs)),
    )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = list(CLIPS.values())
    project.write_manifest(manifest)
    tx.save(
        _words(
            ("it", 0.0, 0.3),
            ("hears", 0.5, 0.9),
            ("the", 1.0, 1.2),
            ("false", 1.5, 1.9),
            ("start", 2.0, 2.4),
            ("and", 4.0, 4.3),
            ("cuts", 5.0, 5.4),
        ),
        project.transcript_path("vo"),
    )
    edit = tl.Edit([tl.Segment("vo", 0.0, 6.0)])
    tl.write(tl.to_otio(edit, CLIPS, rate=1000.0), project.timeline_path)
    return project


@pytest.fixture
def cards(project: Project) -> Project:
    ops.card_new(project.root, "scrim", "scrim", {})
    ops.card_new(project.root, "head", "lowerthird", {"headline": "It hears the false start."})
    ops.card_new(project.root, "opaque", "chapter", {"title": "Part two"})
    return project


# -- templates ---------------------------------------------------------------


def test_the_overlay_templates_are_marked_and_the_others_are_not() -> None:
    assert graphics.is_overlay("lowerthird") and graphics.is_overlay("scrim")
    assert not any(graphics.is_overlay(n) for n in ("receipt", "reveal", "chapter", "endcard"))
    # An overlay has no swatch to be measured against, so no entry.
    assert not set(graphics.TEMPLATE_BACKGROUND) & {"lowerthird", "scrim"}


@pytest.mark.parametrize("canvas", [(1920, 1080), (1080, 1920)])
@pytest.mark.parametrize("name", ["lowerthird", "scrim"])
def test_an_overlay_template_draws_no_background_rect(name: str, canvas: tuple[int, int]) -> None:
    values = {"headline": "x"} if name == "lowerthird" else {}
    svg = graphics.fill_template(name, values, width=canvas[0], height=canvas[1], flow=False)
    assert 'fill="#' not in svg.split("<text")[0].split("</defs>")[-1], "a background rect was drawn"


@needs_magick
def test_an_overlay_card_is_transparent_where_it_draws_nothing(tmp_path: Path) -> None:
    source = tmp_path / "l.svg"
    source.write_text(graphics.fill_template("lowerthird", {"headline": "It hears the false start."}))
    graphics.render_svg(source, tmp_path / "l.png")
    top = subprocess.run(
        ["magick", str(tmp_path / "l.png"), "-alpha", "extract", "-crop", "1920x600+0+0", "-format", "%[fx:maxima]", "info:"],
        capture_output=True, text=True, check=True,
    ).stdout
    band = subprocess.run(
        ["magick", str(tmp_path / "l.png"), "-alpha", "extract", "-crop", "1920x200+0+820", "-format", "%[fx:maxima]", "info:"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert float(top) == 0.0, "the top of a lower third is fully transparent"
    assert float(band) == 1.0, "and the headline is fully opaque"


def test_the_scrim_density_is_a_fraction() -> None:
    assert 'stop-opacity="0.500"' in graphics.fill_template("scrim", {"density": 0.5}, flow=False)
    with pytest.raises(graphics.GraphicsError, match="0 to 1"):
        graphics.fill_template("scrim", {"density": 1.5}, flow=False)
    with pytest.raises(graphics.GraphicsError, match="number"):
        graphics.fill_template("scrim", {"density": "dark"}, flow=False)


# -- the writer ---------------------------------------------------------------


def _ov(start: int, frames: int, **kw: object) -> mlt.Overlay:
    return mlt.Overlay(f"/x/{start}.png", start, frames, **kw)  # type: ignore[arg-type]


def test_overlays_that_never_overlap_share_one_lane() -> None:
    assert mlt.overlay_lanes([_ov(0, 30), _ov(30, 30), _ov(90, 10)]) == [0, 0, 0]


def test_a_later_overlay_is_drawn_above_an_earlier_one_it_overlaps() -> None:
    """List order is stacking order: the scrim first, the type over it."""
    assert mlt.overlay_lanes([_ov(0, 60), _ov(10, 40)]) == [0, 1]
    # A third, overlapping only the type, goes above the type — and one that
    # overlaps nothing drops back to the bottom lane.
    assert mlt.overlay_lanes([_ov(0, 60), _ov(10, 40), _ov(45, 30), _ov(100, 5)]) == [0, 1, 2, 0]


def test_an_overlay_is_never_placed_under_an_earlier_one_on_a_free_low_lane() -> None:
    # The third overlaps the second (lane 1) but not the first, so lane 0 is
    # free for its span — and still wrong, because it must draw above the second.
    assert mlt.overlay_lanes([_ov(0, 10), _ov(20, 40), _ov(30, 10)]) == [0, 0, 1]
    assert mlt.overlay_lanes([_ov(0, 30), _ov(20, 40), _ov(50, 10)]) == [0, 1, 2]


def test_a_rise_nudges_only_its_moving_key_and_rests_on_the_exact_canvas() -> None:
    """The spike's `rise-mixed`: a pure 1:1 rise snaps to whole rows, and a
    nudged resting key leaves the type sub-pixel off."""
    keys = mlt.overlay_rect(
        _ov(0, 90, in_motion="rise", in_frames=14, in_ease="ease-out", out_motion="fade", out_frames=9, out_ease="ease-in"),
        (1920, 1080),
    )
    assert keys == (
        "0h=0 24 1921 1081 0;14=0 0 1920 1080 1;80g=0 0 1920 1080 1;89=0 0 1920 1080 0"
    )


def test_a_rise_travels_the_same_share_of_a_taller_canvas() -> None:
    keys = mlt.overlay_rect(_ov(0, 30, in_motion="rise", in_frames=10), (1080, 1920))
    assert keys is not None and keys.startswith("0h=0 43 1081 1921 0;")


def test_a_fade_alone_never_leaves_one_to_one() -> None:
    keys = mlt.overlay_rect(_ov(0, 30, in_motion="fade", in_frames=5, out_motion="none"), (1920, 1080))
    assert keys == "0h=0 0 1920 1080 0;5=0 0 1920 1080 1"


def test_an_overlay_that_cuts_in_and_out_has_no_filter() -> None:
    assert mlt.overlay_rect(_ov(0, 30, in_motion="none", out_motion="none"), (1920, 1080)) is None


def test_an_exit_that_starts_where_the_entrance_ends_is_one_key() -> None:
    keys = mlt.overlay_rect(
        _ov(0, 11, in_motion="fade", in_frames=5, in_ease="linear", out_motion="fade", out_frames=5, out_ease="linear"),
        (1920, 1080),
    )
    assert keys == "0=0 0 1920 1080 0;5=0 0 1920 1080 1;10=0 0 1920 1080 0"


def _doc(overlays: list[mlt.Overlay] | None) -> ET.Element:
    return mlt.document(
        audio=[mlt.Entry("/x/vo.wav", 0, 120)], overlays=overlays, rate=30.0, name="t"
    )


def test_no_overlays_writes_the_same_document_as_before() -> None:
    assert mlt.to_string(_doc(None)) == mlt.to_string(_doc([]))
    assert "ochain" not in mlt.to_string(_doc(None))


def test_each_lane_is_a_blanked_track_over_the_picture() -> None:
    root = _doc([_ov(30, 60, in_frames=15, out_frames=15), _ov(40, 50, in_motion="rise", in_frames=14)])
    lane = root.find("playlist[@id='oplaylist0a']")
    assert [(c.tag, c.get("length") or c.get("out")) for c in lane] == [
        ("blank", "30"), ("entry", "59"), ("blank", "30"),
    ]
    assert [c.get("in") for c in lane if c.tag == "entry"] == ["0"], "a still is read from frame 0"
    sequence = next(t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None)
    tracks = [t.get("producer") for t in sequence.findall("track")]
    assert tracks[-2:] == ["tractorO0", "tractorO1"]
    blended = {
        p.find("property[@name='b_track']").text
        for p in sequence.findall("transition")
        if p.find("property[@name='mlt_service']").text == "qtblend"
    }
    assert {str(tracks.index("tractorO0")), str(tracks.index("tractorO1"))} <= blended
    assert mlt.reframed_nodes(root) == {}, "an overlay's animation is not a reframe"
    assert set(mlt.declared_frames(root).values()) == {120}


def test_the_writer_refuses_an_overlay_past_the_film_or_an_animation_past_the_overlay() -> None:
    with pytest.raises(mlt.MLTError, match="inside the film"):
        _doc([_ov(100, 30)])
    with pytest.raises(mlt.MLTError, match="shorten the animations"):
        _doc([_ov(0, 20, in_frames=12, out_frames=12)])
    with pytest.raises(mlt.MLTError, match="no motion"):
        _doc([_ov(0, 20, in_motion="spin", in_frames=2)])


# -- ops -----------------------------------------------------------------------


@needs_magick
def test_add_resolves_the_span_through_the_edit_and_echoes_its_words(cards: Project) -> None:
    result = ops.overlay_add(cards.root, "head", "vo", phrase="hears the", until_phrase="false start")
    overlay = result["overlay"]
    assert (overlay["word_index"], overlay["until_word_index"]) == (1, 4)
    assert (overlay["timeline_start"], overlay["timeline_end"]) == (0.5, 2.4)
    assert overlay["start_echo"]["text"] == "hears"
    assert overlay["end_echo"]["text"] == "start"
    assert (overlay["enter"], overlay["leave"]) == ("rise", "fade")
    stored = cards.read_manifest()[ops.OVERLAYS_KEY]
    assert stored[0]["card"] == "head" and "timeline_start" not in stored[0], "no second is stored"


@needs_magick
def test_a_cut_before_an_overlay_moves_it_and_a_cut_through_its_word_refuses(cards: Project) -> None:
    ops.overlay_add(cards.root, "head", "vo", 3, seconds=1.0)  # "false", 1.5s
    ops.cut_by_transcript(cards.root, "vo", cut=[[0, 1]])
    listed = ops.overlay_ls(cards.root)
    # Words 0-1 are 0.0-0.9s, so "false" (1.5s) moves up by 0.9.
    assert listed["overlays"][0]["timeline_start"] == pytest.approx(1.5 - 0.9, abs=0.04)
    ops.cut_by_transcript(cards.root, "vo", cut=[[3, 3]])
    listed = ops.overlay_ls(cards.root)
    assert "a cut removed" in listed["overlays_error"]
    assert listed["overlays"][0]["card"] == "head", "the stored record is still there to fix"
    with pytest.raises(ProjectError, match="a cut removed"):
        ops.export(cards.root, cards.root / "out.kdenlive")


@needs_magick
def test_an_event_addresses_an_overlay(cards: Project) -> None:
    ops.events(cards.root, "vo", name="sent", at=4.2)
    result = ops.overlay_add(cards.root, "head", "vo", event="sent", until_word_index=6)
    assert result["overlay"]["timeline_start"] == pytest.approx(4.2, abs=0.034)
    assert result["overlay"]["start_echo"]["name"] == "sent"


@needs_magick
def test_plan_writes_nothing(cards: Project) -> None:
    ops.overlay_add(cards.root, "head", "vo", 0, seconds=2.0, plan=True)
    assert not cards.read_manifest().get(ops.OVERLAYS_KEY)


@needs_magick
def test_an_opaque_or_unrecorded_card_is_refused(cards: Project) -> None:
    with pytest.raises(ProjectError, match="opaque"):
        ops.overlay_add(cards.root, "opaque", "vo", 0, seconds=1.0)
    (cards.cards_dir / "loose.png").write_bytes((cards.cards_dir / "head.png").read_bytes())
    with pytest.raises(ProjectError, match="no record"):
        ops.overlay_add(cards.root, "loose", "vo", 0, seconds=1.0)


@needs_magick
def test_an_overlay_card_is_refused_as_a_picture_cue(cards: Project) -> None:
    with pytest.raises(ProjectError, match="overlay_add"):
        ops._resolve_asset(cards, "card:head")


@needs_magick
def test_the_address_and_the_animations_are_checked_before_writing(cards: Project) -> None:
    with pytest.raises(ProjectError, match="one of word_index"):
        ops.overlay_add(cards.root, "head", "vo", 0, event="x", seconds=1.0)
    with pytest.raises(ProjectError, match="ends at one of"):
        ops.overlay_add(cards.root, "head", "vo", 0)
    with pytest.raises(ProjectError, match="shorten the animations"):
        ops.overlay_add(cards.root, "head", "vo", 0, seconds=0.5)
    with pytest.raises(ProjectError, match="not one of"):
        ops.overlay_add(cards.root, "head", "vo", 0, seconds=2.0, enter="spin")
    with pytest.raises(ProjectError, match="end after it starts"):
        ops.overlay_add(cards.root, "head", "vo", 4, until_word_index=1)
    assert not cards.read_manifest().get(ops.OVERLAYS_KEY)


@needs_magick
def test_position_sets_the_stack_and_remove_takes_one_off(cards: Project) -> None:
    ops.overlay_add(cards.root, "head", "vo", 1, until_word_index=4)
    ops.overlay_add(cards.root, "scrim", "vo", 0, until_word_index=5, position=0)
    listed = ops.overlay_ls(cards.root)["overlays"]
    assert [(o["card"], o["lane"]) for o in listed] == [("scrim", 0), ("head", 1)]
    with pytest.raises(ProjectError, match="outside the stack"):
        ops.overlay_add(cards.root, "head", "vo", 0, seconds=2.0, position=5)
    removed = ops.overlay_rm(cards.root, 0)
    assert removed["removed"]["card"] == "scrim" and removed["count"] == 1
    assert [o["card"] for o in ops.overlay_ls(cards.root)["overlays"]] == ["head"]
    with pytest.raises(ProjectError, match="no overlay at position 3"):
        ops.overlay_rm(cards.root, 3)


@needs_magick
def test_an_overlay_routes_export_through_the_writer_and_the_reply_names_it(
    cards: Project, tmp_path: Path
) -> None:
    ops.overlay_add(cards.root, "head", "vo", 1, until_word_index=4)
    assert ops._is_layered(cards, ops._load_edit(cards))
    result = ops.export(cards.root, tmp_path / "out.kdenlive")
    assert [o["card"] for o in result["overlays"]] == ["head"]
    text = (tmp_path / "out.kdenlive").read_text()
    assert "tractorO0" in text and "head.png" in text


@needs_magick
def test_the_view_carries_the_overlay_for_the_window(cards: Project) -> None:
    ops.overlay_add(cards.root, "head", "vo", 1, until_word_index=4)
    view = ops.timeline_view(cards.root, "vo")
    assert view["overlays"][0]["asset"] == "card:head"
    assert view["overlays"][0]["enter"] == "rise"
    assert "overlays_error" not in view


@needs_magick
def test_a_reel_drops_the_overlays_and_names_them(cards: Project, tmp_path: Path) -> None:
    ops.overlay_add(cards.root, "head", "vo", 1, until_word_index=4)
    report = ops.reel(cards.root, tmp_path / "reel", start=0.0, end=3.0, plan=True)
    assert [o["card"] for o in report["overlays_dropped"]] == ["head"]


# -- a lower third's footnote as its own layer (docs/plans/RECUT.md step 7) --


def _alpha_rows(png: Path) -> tuple[int, int]:
    """The first and last row with any ink in a transparent PNG."""
    out = subprocess.run(
        ["magick", str(png), "-alpha", "extract", "-format", "%@", "info:"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    size, _, offset = out.partition("+")
    height = int(size.split("x")[1])
    top = int(offset.split("+")[1])
    return top, top + height - 1


@needs_magick
def test_a_footnote_is_drawn_as_its_own_layer_where_the_whole_card_draws_it(project: Project) -> None:
    ops.card_new(project.root, "hears", "lowerthird", {"headline": "It hears the false start.", "footnote": "and cuts it"})
    layers = project.cards_dir / "layers"
    head, foot = layers / "hears.headline.png", layers / "hears.footnote.png"
    assert head.is_file() and foot.is_file()
    assert "hears.headline" not in ops._cards_on_disk(project), "a layer is never a card"

    whole = _alpha_rows(project.cards_dir / "hears.png")
    assert (_alpha_rows(head)[0], _alpha_rows(foot)[1]) == whole
    assert _alpha_rows(head)[1] < _alpha_rows(foot)[0], "the headline sits above the footnote"


@needs_magick
def test_a_layered_card_staggers_its_footnote_on_the_render_and_in_the_preview(project: Project) -> None:
    ops.card_new(project.root, "hears", "lowerthird", {"headline": "It hears the false start.", "footnote": "and cuts it"})
    ops.overlay_add(project.root, "hears", "vo", 1, seconds=2.0, enter="rise", enter_seconds=0.45, enter_ease="ease-out")

    [plan] = ops._overlay_plan(project, ops._load_edit(project), 30.0, edit_frames=180)

    headline, footnote = plan["drawn"]
    assert (headline.start, headline.frames, headline.rise) == (15, 60, 24)
    assert (footnote.start, footnote.frames, footnote.rise) == (15 + round(0.25 * 30), 60 - 8, 16)
    assert footnote.end == headline.end, "the two lines leave together"
    assert plan["layers"] == {"delay": 0.25, "rise": 16, "footnote_start_frame": 23}
    assert Path(footnote.resource).name == "hears.footnote.png"

    view = ops.timeline_view(project.root)["overlays"]
    assert [(v["asset"], v["rise_px"], v["timeline_start"]) for v in view] == [
        ("card:hears#headline", 24, 0.5),
        ("card:hears#footnote", 16, round(23 / 30, 3)),
    ]
    assert ops.preview_source(project.root, "card:hears#footnote")["path"].endswith("hears.footnote.png")
    with pytest.raises(ProjectError, match="does not name a card layer"):
        ops.preview_source(project.root, "card:hears#scrim")


@needs_magick
def test_a_card_without_a_footnote_or_made_before_layers_draws_whole(project: Project) -> None:
    ops.card_new(project.root, "plain", "lowerthird", {"headline": "It picks the shots."})
    assert not (project.cards_dir / "layers" / "plain.headline.png").exists()
    ops.overlay_add(project.root, "plain", "vo", 1, seconds=2.0)
    [plan] = ops._overlay_plan(project, ops._load_edit(project), 30.0, edit_frames=180)
    assert len(plan["drawn"]) == 1 and plan["layers"] is None

    ops.card_new(project.root, "old", "lowerthird", {"headline": "A", "footnote": "b"})
    shutil.rmtree(project.cards_dir / "layers")
    ops.overlay_add(project.root, "old", "vo", 5, seconds=0.9)
    plans = ops._overlay_plan(project, ops._load_edit(project), 30.0, edit_frames=180)
    assert len(plans[1]["drawn"]) == 1


@needs_magick
def test_an_overwrite_without_a_footnote_removes_its_layers(project: Project) -> None:
    ops.card_new(project.root, "hears", "lowerthird", {"headline": "A", "footnote": "b"})
    ops.card_new(project.root, "hears", "lowerthird", {"headline": "A"}, overwrite=True)
    assert not list((project.cards_dir / "layers").glob("hears.*"))
