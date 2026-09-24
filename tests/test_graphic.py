"""Animated graphics — a web page captured as intro, hold and outro.

docs/plans/DAYDREAM.md § Animated graphics, designed and spiked. Pinned here:
the phases' frame arithmetic, the template filler's escaping and slot kinds,
the capture stamp, the websocket framing and the page server's confinement;
that a placed graphic becomes exactly-sized intro, hold and outro pieces, a
span too short or a stale capture refused; the library round trip; and, with
a browser, that a capture is deterministic, refuses a hold that moves, a loop
that does not return and a font that failed, and lets the page reach nothing
but its own folder.

Unit tests fake a capture (`_fake_capture`), so they need no browser. What
melt draws from a real capture was read back by hand on the Pup BNB copy
(HISTORY.md § Animated graphics, built).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from proofcut import browser, mlt, motion, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

needs_browser = pytest.mark.skipif(browser.chrome_path() is None, reason="no headless browser (PROOFCUT_CHROME)")

CLIPS = {
    "vo": {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 6.0, "has_video": False, "has_audio": True},
}

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = list(CLIPS.values())
    project.write_manifest(manifest)
    words = [("one", 0.0, 0.3), ("two", 1.0, 1.3), ("three", 2.0, 2.3), ("four", 4.0, 4.3), ("five", 5.0, 5.4)]
    tx.save(
        tx.Transcript(clip_id="vo", words=tuple(tx.Word(index=i, text=t, start=s, end=e) for i, (t, s, e) in enumerate(words))),
        project.transcript_path("vo"),
    )
    tl.write(tl.to_otio(tl.Edit([tl.Segment("vo", 0.0, 6.0)]), CLIPS, rate=1000.0), project.timeline_path)
    return project


def _fake_capture(project: Project, name: str, *, intro: int, hold: int, outro: int, loop: bool) -> Path:
    """What `motion.capture` leaves, without a browser: frames and a stamped record."""
    width, height, rate = ops._graphic_target(project)
    frames = project.graphic_frames_dir / name
    for phase, count in (("intro", intro), ("hold", hold), ("outro", outro)):
        (frames / phase).mkdir(parents=True, exist_ok=True)
        for k in range(count):
            (frames / phase / f"f{k:04d}.png").write_bytes(PNG_1PX)
    record = {
        "stamp": motion.stamp(project.graphics_dir / name, width, height, rate),
        "canvas": [width, height], "fps": rate, "intro": intro, "hold": hold, "outro": outro, "loop": loop,
        "hold_at": intro / rate,
    }  # fmt: skip
    (frames / motion.CAPTURE_NAME).write_text(json.dumps(record))
    return frames


def _graphic(project: Project, name: str = "g", **phases: float) -> None:
    ops.graphic_new(project.root, name, html="<html></html>", capture=False, **phases)


# -- the spec and the phases -----------------------------------------------


def test_the_phases_are_counted_on_the_frame_grid() -> None:
    layout = motion.phase_frames({"intro": 1.2, "loop": None, "outro": 0.4}, 30.0)
    assert (layout["intro"], layout["hold"], layout["outro"], layout["loop"]) == (36, 1, 12, False)
    assert layout["hold_at"] == pytest.approx(1.2) and layout["outro_at"] == pytest.approx(1.2)
    looped = motion.phase_frames({"intro": 2.0, "loop": 1.0, "outro": 0.4}, 30.0)
    assert (looped["hold"], looped["loop"]) == (30, True)
    assert looped["outro_at"] == pytest.approx(3.0), "a loop's outro starts one period after the hold"


@pytest.mark.parametrize("raw", [{"intro": -1}, {"outro": -0.1}, {"loop": -2}, {"intro": "soon"}])
def test_a_phase_that_is_not_a_length_is_refused(raw: dict) -> None:
    with pytest.raises(motion.GraphicError):
        motion.normalise_spec(raw)


def test_the_stamp_moves_with_the_page_the_canvas_and_the_rate(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("a")
    first = motion.stamp(tmp_path, 1920, 1080, 30.0)
    assert motion.stamp(tmp_path, 1080, 1920, 30.0) != first
    assert motion.stamp(tmp_path, 1920, 1080, 25.0) != first
    (tmp_path / "index.html").write_text("b")
    assert motion.stamp(tmp_path, 1920, 1080, 30.0) != first


# -- templates ---------------------------------------------------------------


def test_every_template_declares_phases_and_documents_its_slots() -> None:
    listed = motion.templates()
    assert {t["name"] for t in listed} == {"typing", "highlight", "letters", "chips"}
    for template in listed:
        assert template["about"], template["name"]
        assert all(decl.get("about") for decl in template["slots"].values()), template["name"]


def test_a_slot_value_is_escaped_in_text_and_in_attributes(tmp_path: Path) -> None:
    motion.fill_template("letters", {"title": '"><script>alert(1)</script>'}, tmp_path / "g")
    page = (tmp_path / "g" / "index.html").read_text()
    assert "<script>alert" not in page
    assert 'data-text="&quot;&gt;&lt;script&gt;' in page


@pytest.mark.parametrize(
    "values", [{"word": "x", "marker_colour": "red; } body { display: none"}, {"word": "x", "size": "96px"}]
)
def test_a_colour_or_number_slot_is_held_to_its_shape(values: dict, tmp_path: Path) -> None:
    with pytest.raises(motion.GraphicError):
        motion.fill_template("highlight", values, tmp_path / "g")


def test_an_unknown_or_missing_slot_is_refused(tmp_path: Path) -> None:
    with pytest.raises(motion.GraphicError, match="no slot"):
        motion.fill_template("highlight", {"word": "x", "colour": "#fff"}, tmp_path / "a")
    with pytest.raises(motion.GraphicError, match="needs word"):
        motion.fill_template("highlight", {}, tmp_path / "b")


def test_a_filled_template_records_what_it_was_filled_with(tmp_path: Path) -> None:
    spec = motion.fill_template("typing", {"text": "hello"}, tmp_path / "g")
    assert spec["template"] == "typing" and spec["slots"]["text"] == "hello"
    assert motion.read_spec(tmp_path / "g")["loop"] == 1.0


# -- the browser plumbing that needs no browser --------------------------------


@pytest.mark.parametrize("n", [0, 1, 5, 125, 126, 70_000])
def test_the_websocket_mask_is_a_plain_xor(n: int) -> None:
    payload, mask = os.urandom(n), b"\x01\x80\xff\x10"
    assert browser._mask(payload, mask) == bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def test_the_page_server_serves_only_the_folder_and_the_vendored_fonts(tmp_path: Path) -> None:
    (tmp_path / "page").mkdir()
    (tmp_path / "page" / "index.html").write_text("hi")
    (tmp_path / "secret.txt").write_text("no")
    served = browser.Browser.__new__(browser.Browser)
    served.root, served.fonts = tmp_path / "page", []
    assert served._local("/")[0] == b"hi"
    assert served._local("/../secret.txt")[0] is None
    assert served._local("/%2e%2e/secret.txt")[0] is None
    body, mime = served._local("/_proofcut/fonts/static/Outfit-Bold.ttf")
    assert body and mime == "font/ttf"
    assert served._local("/_proofcut/fonts/../../ops.py")[0] is None


def test_the_launch_carries_the_measured_deterministic_flags() -> None:
    args = browser.launch_args("/bin/chrome", Path("/tmp/p"))
    assert set(browser.DETERMINISTIC_FLAGS) <= set(args)
    assert "--remote-debugging-port=0" in args


# -- placing a graphic -----------------------------------------------------------


def test_a_placed_graphic_is_its_intro_hold_and_outro_exactly(project: Project) -> None:
    _graphic(project, intro=1.2, outro=0.4)
    frames = _fake_capture(project, "g", intro=36, hold=1, outro=12, loop=False)
    added = ops.overlay_add(project.root, None, "vo", 0, graphic="g", until_word_index=3)
    plan = added["overlay"]
    total = plan["frames"]
    assert [(p["phase"], p["frames"]) for p in plan["phases"]] == [("intro", 36), ("hold", total - 48), ("outro", 12)]
    assert plan["phases"][1]["start_frame"] == 36 and plan["phases"][2]["start_frame"] == total - 12
    assert (plan["enter"], plan["leave"]) == ("none", "none"), "a graphic animates itself"
    drawn = ops._overlay_plan(project, ops._load_edit(project), 30.0, edit_frames=180)[0]["drawn"]
    assert drawn[0].resource == str(frames / "intro" / "f%04d.png")
    assert drawn[1].resource == str(frames / "hold" / "f0000.png"), "a still hold is one still"


def test_a_looping_hold_is_its_sequence_and_the_writer_draws_every_piece(project: Project) -> None:
    _graphic(project, intro=1.0, loop=1.0)
    frames = _fake_capture(project, "g", intro=30, hold=30, outro=0, loop=True)
    ops.overlay_add(project.root, None, "vo", 0, graphic="g", until_word_index=4)
    plan = ops._overlay_plan(project, ops._load_edit(project), 30.0, edit_frames=180)[0]
    assert [piece.resource for piece in plan["drawn"]] == [
        str(frames / "intro" / "f%04d.png"), str(frames / "hold" / "f%04d.png")
    ]  # fmt: skip
    assert len(set(plan["lanes"])) == 1, "sequential pieces share a lane"


def test_a_span_shorter_than_intro_and_outro_is_refused(project: Project) -> None:
    _graphic(project, intro=1.2, outro=0.4)
    _fake_capture(project, "g", intro=36, hold=1, outro=12, loop=False)
    with pytest.raises(ProjectError, match="intro and outro take 48"):
        ops.overlay_add(project.root, None, "vo", 0, graphic="g", seconds=1.0)


def test_a_stale_or_missing_capture_is_refused_not_drawn(project: Project) -> None:
    _graphic(project, intro=1.0)
    with pytest.raises(ProjectError, match="never been captured"):
        ops.overlay_add(project.root, None, "vo", 0, graphic="g", seconds=3.0)
    _fake_capture(project, "g", intro=30, hold=1, outro=0, loop=False)
    ops.overlay_add(project.root, None, "vo", 0, graphic="g", seconds=3.0)
    ops.graphic_edit(project.root, "g", html="<html>changed</html>", capture=False)
    view = ops.timeline_view(project.root)
    assert "changed since it was captured" in view["overlays_error"]
    assert ops.graphic_ls(project.root)["graphics"][0]["capture"] == "stale"


def test_the_view_hands_the_preview_one_item_per_piece(project: Project) -> None:
    _graphic(project, intro=1.0, loop=0.5, outro=0.5)
    _fake_capture(project, "g", intro=30, hold=15, outro=15, loop=True)
    ops.overlay_add(project.root, None, "vo", 0, graphic="g", until_word_index=4, enter="fade", enter_seconds=0.2)
    items = [o for o in ops.timeline_view(project.root)["overlays"] if o.get("graphic")]
    assert [(o["layer"], o["frame_count"]) for o in items] == [("intro", 30), ("hold", 15), ("outro", 15)]
    assert [o["enter"] for o in items] == ["fade", "none", "none"]
    assert items[0]["timeline_end"] == items[1]["timeline_start"]


def test_an_overlay_names_exactly_one_of_a_card_or_a_graphic(project: Project) -> None:
    with pytest.raises(ProjectError, match="exactly one"):
        ops.overlay_add(project.root, None, "vo", 0, seconds=1.0)
    with pytest.raises(ProjectError, match="exactly one"):
        ops.overlay_add(project.root, "c", "vo", 0, graphic="g", seconds=1.0)


def test_a_graphic_needs_one_source_and_a_new_name(project: Project) -> None:
    with pytest.raises(ProjectError, match="exactly one of template or html"):
        ops.graphic_new(project.root, "g", capture=False)
    _graphic(project)
    with pytest.raises(ProjectError, match="already exists"):
        _graphic(project)
    with pytest.raises(ProjectError, match="must be lowercase"):
        ops.graphic_new(project.root, "Bad Name", html="x", capture=False)


def test_editing_slots_keeps_the_other_slots_and_the_phases(project: Project) -> None:
    ops.graphic_new(project.root, "t", template="highlight", slots={"word": "one", "before": "the"}, intro=2.0, capture=False)
    ops.graphic_edit(project.root, "t", slots={"word": "two"}, capture=False)
    spec = motion.read_spec(project.graphics_dir / "t")
    assert spec["slots"]["word"] == "two" and spec["slots"]["before"] == "the"
    assert spec["intro"] == 2.0


def test_the_library_round_trips_a_graphic_between_projects(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROOFCUT_LIBRARY", str(tmp_path / "lib"))
    ops.graphic_new(project.root, "t", template="letters", slots={"title": "Hi"}, capture=False)
    ops.graphic_save(project.root, "t", as_name="title-card")
    assert [g["name"] for g in ops.graphic_library()["graphics"]] == ["title-card"]
    with pytest.raises(ProjectError, match="already exists"):
        ops.graphic_save(project.root, "t", as_name="title-card")
    other = Project.create(tmp_path / "other")
    ops.graphic_load(other.root, "title-card", capture=False)
    assert motion.read_spec(other.graphics_dir / "title-card")["slots"]["title"] == "Hi"
    assert not (other.graphic_frames_dir / "title-card").exists(), "frames never travel"


# -- with a browser ----------------------------------------------------------------

PAGE = """<!doctype html><html><head><style>
@font-face {{ font-family: "Outfit"; font-weight: 700; src: url("/_proofcut/fonts/static/Outfit-Bold.ttf"); }}
html, body {{ margin: 0; background: transparent; }}
.box {{ position: absolute; left: 10px; top: 10px; width: 40px; height: 40px; background: #f00;
  font: 700 20px "Outfit"; animation: slide .5s linear forwards; }}
@keyframes slide {{ from {{ transform: translateX(0); }} to {{ transform: translateX(100px); }} }}
{extra}
</style></head><body><div class="box">A</div>{body}</body></html>"""


def _page(folder: Path, *, extra: str = "", body: str = "", **phases: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(PAGE.format(extra=extra, body=body))
    motion.write_spec(folder, motion.normalise_spec({"intro": 0.5, **phases}))
    return folder


@needs_browser
def test_a_capture_is_frame_exact_transparent_and_the_same_twice(tmp_path: Path) -> None:
    folder = _page(tmp_path / "g", outro=0.2)
    first = motion.capture(folder, tmp_path / "a", width=160, height=90, fps=10.0)
    motion.capture(folder, tmp_path / "b", width=160, height=90, fps=10.0)
    assert (first["intro"], first["hold"], first["outro"]) == (5, 1, 2)
    assert first["vendored_fonts"] == ["static/Outfit-Bold.ttf"]
    for phase, count in (("intro", 5), ("hold", 1), ("outro", 2)):
        for k in range(count):
            name = f"{phase}/f{k:04d}.png"
            assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    assert (tmp_path / "a" / "intro" / "f0000.png").read_bytes() != (tmp_path / "a" / "hold" / "f0000.png").read_bytes()


@needs_browser
def test_a_hold_still_moving_is_refused_unless_it_loops(tmp_path: Path) -> None:
    blink = ".box { animation: slide .5s linear forwards, blink 1s steps(1) infinite; } @keyframes blink { 50% { opacity: 0; } }"
    with pytest.raises(motion.GraphicError, match="still moving at its hold"):
        motion.capture(_page(tmp_path / "g", extra=blink), tmp_path / "f", width=160, height=90, fps=10.0)
    looped = motion.capture(_page(tmp_path / "h", extra=blink, loop=1.0), tmp_path / "f2", width=160, height=90, fps=10.0)
    assert (looped["hold"], looped["loop"]) == (10, True)


@needs_browser
def test_a_loop_that_does_not_come_back_is_refused(tmp_path: Path) -> None:
    drift = ".box { animation: slide .5s linear forwards, blink 1s steps(1) infinite; } @keyframes blink { 50% { opacity: 0; } }"
    with pytest.raises(motion.GraphicError, match="does not come back"):
        motion.capture(_page(tmp_path / "g", extra=drift, loop=0.7), tmp_path / "f", width=160, height=90, fps=10.0)


@needs_browser
def test_a_font_that_failed_to_load_is_refused(tmp_path: Path) -> None:
    missing = '@font-face { font-family: "Gone"; src: url("/nope.ttf"); } .box { font-family: "Gone"; }'
    with pytest.raises(motion.GraphicError, match="could not load the font"):
        motion.capture(_page(tmp_path / "g", extra=missing), tmp_path / "f", width=160, height=90, fps=10.0)


@needs_browser
def test_the_page_reaches_nothing_outside_its_folder(tmp_path: Path) -> None:
    (tmp_path / "outside.txt").write_text("secret")
    probe = (
        "<script>window.proofcutSeek = async () => { const out = [];"
        " for (const url of ['https://example.com/', '../outside.txt', 'file:///etc/hostname', 'index.html']) {"
        "  try { const r = await fetch(url); out.push(r.ok ? 'ok' : 'status'); } catch (e) { out.push('blocked'); } }"
        " document.title = out.join(','); };</script>"
    )
    folder = _page(tmp_path / "g", body=probe)
    with browser.launch(folder) as chrome:
        page = chrome.new_page(160, 90)
        browser.load(page)
        browser.seek(page, 0.0)
        # `../` normalises to a path inside the origin, which the folder lacks.
        assert page.evaluate("document.title") == "blocked,blocked,blocked,ok"


def test_mlt_draws_a_sequence_and_a_still_through_the_same_overlay_node() -> None:
    """The writer is unchanged: a graphic's pieces are ordinary overlays."""
    pieces = [
        mlt.Overlay(resource="/g/intro/f%04d.png", start=0, frames=30, in_motion="none", out_motion="none"),
        mlt.Overlay(resource="/g/hold/f0000.png", start=30, frames=60, in_motion="none", out_motion="none"),
    ]
    assert mlt.overlay_lanes(pieces) == [0, 0]
    for piece in pieces:
        mlt._check_overlay(piece, 120)
