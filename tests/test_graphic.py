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
import shutil
import subprocess
from pathlib import Path

import pytest
from stubs import write_stub

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


def _stub_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, refuse_sandbox: bool) -> Path:
    """A browser that records its argv and dies: sandboxed with Chrome's own
    sandbox refusal (or another fault), and without one with a plain exit."""
    calls = tmp_path / "calls.txt"
    body = (
        "import sys\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if '--no-sandbox' not in sys.argv:\n"
        f"    sys.stderr.write({(browser.NO_SANDBOX_MESSAGE + '! see AppArmor') if refuse_sandbox else 'GPU process crashed'!r})\n"
        "    sys.exit(5)\n"
        "sys.stderr.write('ran without a sandbox')\n"
        "sys.exit(3)\n"
    )
    monkeypatch.setenv("PROOFCUT_CHROME", str(write_stub(tmp_path / "chrome", body)))
    return calls


def test_a_refused_sandbox_is_retried_once_without_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ubuntu 23.10+ refuses Chrome for Testing a sandbox (SIGTRAP before
    DevTools, measured on CI's 24.04 runner); proofcut retries without it."""
    calls = _stub_browser(tmp_path, monkeypatch, refuse_sandbox=True)
    with pytest.raises(browser.BrowserError, match="ran without a sandbox"), browser.launch(tmp_path):
        pass
    launches = calls.read_text().splitlines()
    assert len(launches) == 2 and "--no-sandbox" not in launches[0] and "--no-sandbox" in launches[1]


def test_any_other_death_is_reported_in_its_own_words_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_browser(tmp_path, monkeypatch, refuse_sandbox=False)
    with pytest.raises(browser.BrowserError, match="GPU process crashed"), browser.launch(tmp_path):
        pass
    assert len(calls.read_text().splitlines()) == 1


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


@needs_browser
def test_a_page_that_never_draws_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "g"
    folder.mkdir()
    (folder / "index.html").write_text(
        "<!doctype html><html><body style='margin:0;background:transparent'>"
        "<script>throw new Error('before anything drew');</script></body></html>"
    )
    motion.write_spec(folder, motion.normalise_spec({"intro": 0.5}))
    with pytest.raises(motion.GraphicError, match="drew nothing"):
        motion.capture(folder, tmp_path / "f", width=160, height=90, fps=10.0)
    assert not (tmp_path / "f").exists()


@needs_browser
def test_a_first_frame_empty_on_purpose_is_captured(tmp_path: Path) -> None:
    fade = ".box { animation: fade .5s linear forwards; } @keyframes fade { from { opacity: 0; } to { opacity: 1; } }"
    record = motion.capture(_page(tmp_path / "g", extra=fade), tmp_path / "f", width=160, height=90, fps=10.0)
    assert record["intro"] == 5


@needs_browser
def test_a_page_animating_from_its_own_clock_draws_the_seeked_instant(tmp_path: Path) -> None:
    """No `proofcutSeek`: the page's rAF loop reads `performance.now`, its
    frame timestamp and `Date`, and the seek moves all three."""
    loop = (
        "<script>const t0 = Date.now(); (function tick(ts) {"
        " document.title = [performance.now(), ts ?? '-', Date.now() - t0, typeof Date(), new Date() instanceof Date].join(',');"
        " requestAnimationFrame(tick); })();</script>"
    )
    folder = _page(tmp_path / "g", body=loop)
    with browser.launch(folder) as chrome:
        page = chrome.new_page(160, 90)
        browser.load(page)
        browser.seek(page, 0.3)
        assert page.evaluate("document.title") == "300,300,300,string,true"
        browser.seek(page, 0.1)
        assert page.evaluate("document.title") == "100,100,100,string,true"


#: A page that draws only once slow setup finishes: a fetch, then 150 ms of
#: real time. `{wrap}` is the promise as written, or handed to the capture.
SLOW_SETUP = (
    "<script>const ready = fetch('data.json').then(r => r.json())"
    " .then(j => new Promise(r => setTimeout(() => r(j), 150)))"
    " .then(j => { document.title = j.word; });{wrap}</script>"
)


@needs_browser
def test_a_seek_waits_for_what_the_page_asked_it_to(tmp_path: Path) -> None:
    """The control is the same page not asking: its first frame is shot
    before it drew (measured 2026-10-03, ~/proofcut-work/spikes/readiness)."""
    for wrap, expected in (("", ""), (" window.proofcutWaitFor(ready, 'data');", "drawn")):
        folder = _page(tmp_path / f"g{len(wrap)}", body=SLOW_SETUP.replace("{wrap}", wrap))
        (folder / "data.json").write_text('{"word": "drawn"}')
        with browser.launch(folder) as chrome:
            page = chrome.new_page(160, 90)
            browser.load(page)
            browser.seek(page, 0.0)
            assert page.evaluate("document.title") == expected


@needs_browser
def test_a_wait_that_fails_or_never_ends_refuses_the_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failing = _page(tmp_path / "f", body="<script>window.proofcutWaitFor(Promise.reject(new Error('no data')), 'chart data');</script>")
    with pytest.raises(motion.GraphicError, match=r"chart data.*no data"):
        motion.capture(failing, tmp_path / "out", width=160, height=90, fps=10.0)
    monkeypatch.setattr(browser, "WAIT_TIMEOUT", 0.5)
    stuck = _page(tmp_path / "s", body="<script>window.proofcutWaitFor(new Promise(() => {}), 'logo');</script>")
    with pytest.raises(motion.GraphicError, match=r"still waiting.*logo"):
        motion.capture(stuck, tmp_path / "out", width=160, height=90, fps=10.0)
    assert not (tmp_path / "out").exists()


@needs_browser
def test_a_font_that_fails_after_the_page_loaded_is_refused(tmp_path: Path) -> None:
    """The face is unused at load, so `unloaded` then, and fails only once a
    later frame shows the text that names it."""
    late = (
        '<style>@font-face { font-family: "Late"; src: url("/nope.ttf"); }'
        ' #late { font: 20px "Late"; display: none; }</style><div id="late">B</div>'
        "<script>(function tick(ts) { document.getElementById('late').style.display ="
        " (ts || 0) >= 300 ? 'block' : 'none'; requestAnimationFrame(tick); })();</script>"
    )
    with pytest.raises(motion.GraphicError, match="could not load the font"):
        motion.capture(_page(tmp_path / "g", body=late), tmp_path / "f", width=160, height=90, fps=10.0)


needs_video_tools = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("magick") is None, reason="needs ffmpeg and ImageMagick"
)


def _numbered_clip(path: Path) -> list[int]:
    """A 2 s, 30 fps clip whose frame N is flat grey 16 + 3N, and the grey
    ffmpeg itself decodes for each frame, which is what a frame is matched to."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x36:r=30:d=2",
         "-vf", "geq=lum='16+N*3':cb=128:cr=128", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", str(path)],
        check=True,
    )
    frames = path.parent / "truth"
    frames.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), str(frames / "f%03d.png")], check=True)
    return [_grey(p.read_bytes()) for p in sorted(frames.glob("f*.png"))]


def _grey(png: bytes, x: int = 32, y: int = 18) -> int:
    out = subprocess.run(
        ["magick", "png:-", "-colorspace", "gray", "-format", f"%[fx:round(255*p{{{x},{y}}}.r)]", "info:"],
        input=png, capture_output=True, check=True,
    )
    return int(out.stdout)


def _frame_of(table: list[int], grey: int) -> int:
    return min(range(len(table)), key=lambda i: abs(table[i] - grey))


@needs_browser
@needs_video_tools
def test_a_page_video_draws_the_exact_frame_at_every_capture_frame(tmp_path: Path) -> None:
    """24 fps over a 30 fps clip puts some capture frames exactly on a source
    boundary, where the browser's own decoder showed the frame before
    (4 of 45, ~/proofcut-work/spikes/video-in-graphic)."""
    folder = tmp_path / "g"
    folder.mkdir()
    table = _numbered_clip(folder / "clip.mp4")
    (folder / "index.html").write_text(
        "<!doctype html><body style='margin:0'><video src='clip.mp4' autoplay muted"
        " style='display:block;width:64px'></video></body>"
    )
    motion.write_spec(folder, motion.normalise_spec({"intro": 1.9}))
    record = motion.capture(folder, tmp_path / "f", width=64, height=36, fps=24.0)
    shown = [_frame_of(table, _grey((tmp_path / "f" / "intro" / f"f{k:04d}.png").read_bytes())) for k in range(record["intro"])]
    assert shown == [int(k / 24 * 30 + 1e-9) for k in range(record["intro"])]


@needs_browser
@needs_video_tools
def test_a_page_video_starts_at_data_start_and_loops(tmp_path: Path) -> None:
    folder = tmp_path / "g"
    folder.mkdir()
    table = _numbered_clip(folder / "clip.mp4")
    (folder / "index.html").write_text(
        "<!doctype html><body style='margin:0'><video src='clip.mp4' data-start='0.5' loop"
        " style='display:block;width:64px'></video></body>"
    )
    with browser.launch(folder) as chrome:
        page = chrome.new_page(64, 36)
        browser.load(page)
        for seconds, frame in ((0.2, 0), (0.5, 0), (1.5, 30), (2.6, 3)):
            browser.seek(page, seconds)
            assert _frame_of(table, _grey(browser.screenshot(page))) == frame, seconds


@needs_browser
@needs_video_tools
def test_a_page_video_never_hands_the_browser_its_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The element's own load is refused: a 106 MB clip served whole, as one
    base64 CDP message, reset the connection (2026-10-03, a real 7.4 s span)."""
    folder = tmp_path / "g"
    folder.mkdir()
    table = _numbered_clip(folder / "clip.mp4")
    (folder / "index.html").write_text(
        "<!doctype html><body style='margin:0'><video src='clip.mp4' style='display:block;width:64px'></video></body>"
    )
    served: list[str] = []
    local = browser.Browser._local

    def spy(self: browser.Browser, path: str) -> tuple[bytes | None, str]:
        served.append(path)
        return local(self, path)

    monkeypatch.setattr(browser.Browser, "_local", spy)
    with browser.launch(folder) as chrome:
        page = chrome.new_page(64, 36)
        browser.load(page)
        browser.seek(page, 0.5)
        assert _frame_of(table, _grey(browser.screenshot(page))) == 15
    assert "/clip.mp4" not in served


@needs_browser
@needs_video_tools
def test_a_detached_video_keeps_the_size_its_own_frames_gave_it(tmp_path: Path) -> None:
    """Sized by height alone, a 64x36 video is 32x18 at 18 px tall. The
    width and height attributes would have made it 64 wide (2026-10-03)."""
    folder = tmp_path / "g"
    folder.mkdir()
    _numbered_clip(folder / "clip.mp4")
    (folder / "index.html").write_text(
        "<!doctype html><body style='margin:0'><video src='clip.mp4' style='height:18px'></video>"
        "<video src='clip.mp4' style='width:50%'></video></body>"
    )
    with browser.launch(folder) as chrome:
        page = chrome.new_page(64, 36)
        browser.load(page)
        browser.seek(page, 0.0)
        sizes = page.evaluate("[...document.querySelectorAll('video')].map(v => [v.offsetWidth, v.offsetHeight])")
        assert sizes == [[32, 18], [32, 18]]


@needs_browser
@needs_video_tools
def test_a_video_outside_the_folder_refuses_the_capture(tmp_path: Path) -> None:
    _numbered_clip(tmp_path / "outside.mp4")
    folder = _page(tmp_path / "g", body="<video src='../outside.mp4'></video>")
    with pytest.raises(motion.GraphicError, match="the video"):
        motion.capture(folder, tmp_path / "f", width=160, height=90, fps=10.0)


def test_only_a_page_that_shows_a_video_is_restamped(tmp_path: Path) -> None:
    folder = _page(tmp_path / "g")
    plain = motion.stamp(folder, 160, 90, 10.0)
    (folder / "index.html").write_text((folder / "index.html").read_text() + "<video src='a.mp4'></video>")
    salted = motion.stamp(folder, 160, 90, 10.0)
    motion.VIDEO_CAPTURE_VERSION += 1
    try:
        assert motion.stamp(folder, 160, 90, 10.0) != salted
        (folder / "index.html").write_text((folder / "index.html").read_text().replace("<video src='a.mp4'></video>", ""))
        assert motion.stamp(folder, 160, 90, 10.0) == plain
    finally:
        motion.VIDEO_CAPTURE_VERSION -= 1


def _with_shot(project: Project) -> list[int]:
    """Register the numbered clip as project clip `shot`."""
    source = project.root.parent / "src" / "shot.mp4"
    source.parent.mkdir()
    table = _numbered_clip(source)
    manifest = project.read_manifest()
    manifest["clips"].append(
        {"clip_id": "shot", "source": str(source), "duration": 2.0, "picture_end": 2.0, "has_video": True, "has_audio": False}
    )
    project.write_manifest(manifest)
    return table


@needs_browser
@needs_video_tools
def test_graphic_new_cuts_a_clip_span_the_page_shows_frame_exactly(project: Project, tmp_path: Path) -> None:
    table = _with_shot(project)
    made = ops.graphic_new(
        project.root, "inset", html="<body style='margin:0'><video src='shot.mp4' style='width:64px'></video></body>",
        clips=[{"clip": "shot", "start": 0.5, "end": 1.5}], intro=0.5, capture=False,
    )  # fmt: skip
    assert made["graphic"]["clips"] == [{"clip": "shot", "start": 0.5, "end": 1.5, "file": "shot.mp4"}]
    folder = project.graphics_dir / "inset"
    record = motion.capture(folder, tmp_path / "f", width=64, height=36, fps=30.0)
    shown = [_frame_of(table, _grey((tmp_path / "f" / "intro" / f"f{k:04d}.png").read_bytes())) for k in range(record["intro"])]
    assert shown == list(range(15, 15 + record["intro"]))


@needs_video_tools
def test_two_spans_of_one_clip_take_two_names(project: Project) -> None:
    _with_shot(project)
    twice = [{"clip": "shot", "end": 0.5}, {"clip": "shot", "start": 1.0}]
    with pytest.raises(ProjectError, match="give one a name"):
        ops.graphic_new(project.root, "g", html="<html></html>", clips=twice, capture=False)
    made = ops.graphic_new(project.root, "g", html="<html></html>", clips=[twice[0], {**twice[1], "name": "late"}], capture=False)
    assert [c["file"] for c in made["graphic"]["clips"]] == ["shot.mp4", "late.mp4"]
    assert sorted(p.name for p in (project.graphics_dir / "g").glob("*.mp4")) == ["late.mp4", "shot.mp4"]


@pytest.mark.parametrize(
    ("clips", "template", "message"),
    [
        ([{"clip": "nope"}], None, "no clip 'nope'"),
        ([{"clip": "vo"}], None, "no picture"),
        ([{"clip": "vo", "start": 7.0}], None, "no picture"),
        ([{"clip": "shot", "start": 1.5, "end": 2.5}], None, "not inside it"),
        ([{"clip": "shot", "name": "../x"}], None, "cannot name"),
        ([{"clip": "shot"}], "typing", "clips are for a page written as html"),
    ],
)
def test_graphic_new_refuses_a_clip_it_cannot_cut(project: Project, clips: list, template: str | None, message: str) -> None:
    manifest = project.read_manifest()
    manifest["clips"].append({"clip_id": "shot", "source": "/nowhere/shot.mp4", "duration": 2.0, "picture_end": 2.0, "has_video": True})
    project.write_manifest(manifest)
    source = {"template": template} if template else {"html": "<html></html>"}
    with pytest.raises(ProjectError, match=message):
        ops.graphic_new(project.root, "g", clips=clips, capture=False, **source)
    assert not (project.graphics_dir / "g").exists()


@needs_video_tools
def test_graphic_edit_cuts_a_clip_in_replacing_one_of_that_name(project: Project) -> None:
    _with_shot(project)
    ops.graphic_new(project.root, "g", html="<html></html>", clips=[{"clip": "shot", "end": 0.5}], capture=False)
    folder = project.graphics_dir / "g"
    first = (folder / "shot.mp4").read_bytes()
    edited = ops.graphic_edit(
        project.root, "g", clips=[{"clip": "shot", "start": 1.0}, {"clip": "shot", "end": 0.2, "name": "flash"}], capture=False
    )  # fmt: skip
    assert edited["graphic"]["clips"] == [
        {"clip": "shot", "start": 1.0, "end": 2.0, "file": "shot.mp4"},
        {"clip": "shot", "start": 0.0, "end": 0.2, "file": "flash.mp4"},
    ]
    assert (folder / "shot.mp4").read_bytes() != first
    assert sorted(p.name for p in folder.iterdir() if p.suffix == ".mp4") == ["flash.mp4", "shot.mp4"]
    # Every clip is cut before any lands: one refused leaves the folder as it was.
    before = (folder / "shot.mp4").read_bytes()
    with pytest.raises(ProjectError, match="not inside it"):
        ops.graphic_edit(project.root, "g", clips=[{"clip": "shot", "end": 0.4}, {"clip": "shot", "start": 3.0, "name": "x"}], capture=False)
    assert (folder / "shot.mp4").read_bytes() == before
    assert ops.graphic_edit(project.root, "g", intro=0.3, capture=False)["graphic"]["clips"] == edited["graphic"]["clips"]
    assert not list(project.graphics_dir.glob(".*"))


def test_graphic_edit_refuses_clips_on_a_template_graphic(project: Project) -> None:
    ops.graphic_new(project.root, "t", template="typing", slots={"text": "hi"}, capture=False)
    with pytest.raises(ProjectError, match="clips are for a page written as html"):
        ops.graphic_edit(project.root, "t", clips=[{"clip": "vo"}], capture=False)


def test_the_cli_reads_a_clip_span_and_a_name() -> None:
    from proofcut.cli import _graphic_clip_args

    assert _graphic_clip_args(["shot", "shot:0.5-1.5=inset", "shot:1"]) == [
        {"clip": "shot"}, {"clip": "shot", "start": 0.5, "end": 1.5, "name": "inset"}, {"clip": "shot", "start": 1.0, "end": None},
    ]  # fmt: skip
    assert _graphic_clip_args([]) is None


def test_mlt_draws_a_sequence_and_a_still_through_the_same_overlay_node() -> None:
    """The writer is unchanged: a graphic's pieces are ordinary overlays."""
    pieces = [
        mlt.Overlay(resource="/g/intro/f%04d.png", start=0, frames=30, in_motion="none", out_motion="none"),
        mlt.Overlay(resource="/g/hold/f0000.png", start=30, frames=60, in_motion="none", out_motion="none"),
    ]
    assert mlt.overlay_lanes(pieces) == [0, 0]
    for piece in pieces:
        mlt._check_overlay(piece, 120)


# -- a still hold plays the page's video on -------------------------------------


def test_a_still_hold_plays_its_video_until_every_video_is_on_its_last_frame() -> None:
    """Frame k of the hold shows the video at hold_at + k/fps; from the first
    frame on its last frame, the hold rests there and every outro is the same."""
    record = {"fps": 30.0, "hold_at": 0.5, "loop": False, "video": {"videos": 1, "ends": 1.95}}
    assert motion.video_hold(record, 20) == {"playing": 20, "rest": 0, "outro": 20, "frozen": 44}
    assert motion.video_hold(record, 60) == {"playing": 45, "rest": 15, "outro": 44, "frozen": 44}
    assert motion.video_hold({**record, "video": {"videos": 1, "ends": None}}, 600)["playing"] == 600, "a looping video never stops"
    assert motion.video_hold({**record, "video": {"videos": 1, "ends": 0.4}}, 20) is None, "it ran out in the intro"
    assert motion.video_hold({**record, "loop": True}, 20) is None, "a loop is today's loop"
    assert motion.video_hold({k: v for k, v in record.items() if k != "video"}, 20) is None


def _fake_play(frames: Path, playing: int, outro: tuple[int, int] | None) -> None:
    (frames / motion.PLAY_HOLD).mkdir(exist_ok=True)
    for k in range(playing):
        (frames / motion.PLAY_HOLD / f"f{k:04d}.png").write_bytes(PNG_1PX)
    if outro is not None:
        key, count = outro
        (frames / f"outro-{key}").mkdir()
        for k in range(count):
            (frames / f"outro-{key}" / f"f{k:04d}.png").write_bytes(PNG_1PX)


def test_a_hold_playing_its_video_is_its_frames_then_a_rest_and_the_outro_after_them(project: Project) -> None:
    _graphic(project, intro=1.0, outro=0.4)
    frames = _fake_capture(project, "g", intro=30, hold=1, outro=12, loop=False)
    record = json.loads((frames / motion.CAPTURE_NAME).read_text())
    record["video"] = {"videos": 1, "ends": 2.0}  # frozen from hold frame 30
    (frames / motion.CAPTURE_NAME).write_text(json.dumps(record))
    ops.overlay_add(project.root, None, "vo", 0, graphic="g", seconds=4.0)  # 120 frames: 30 + 78 + 12

    # Not captured at this length yet: the view draws today's still and says so.
    items = [o for o in ops.timeline_view(project.root)["overlays"] if o.get("graphic")]
    assert [(o["layer"], o["asset"]) for o in items] == [("intro", "graphic:g/intro"), ("hold", "graphic:g/hold"), ("outro", "graphic:g/outro")]
    assert items[1]["video"] == "plays from the next render, which captures it"

    _fake_play(frames, 31, (30, 12))
    plan = ops._overlay_plan(project, ops._load_edit(project), 30.0, edit_frames=180)[0]
    assert [(p["phase"], p["frames"]) for p in plan["phases"]] == [("intro", 30), ("hold", 31), ("rest", 47), ("outro", 12)]
    assert [piece.resource for piece in plan["drawn"]] == [
        str(frames / "intro" / "f%04d.png"),
        str(frames / "hold-play" / "f%04d.png"),
        str(frames / "hold-play" / "f0030.png"),
        str(frames / "outro-30" / "f%04d.png"),
    ]
    items = [o for o in ops.timeline_view(project.root)["overlays"] if o.get("graphic")]
    assert [(o["asset"], o["first"], o["frame_count"]) for o in items] == [
        ("graphic:g/intro", 0, 30), ("graphic:g/hold-play", 0, 31), ("graphic:g/hold-play", 30, 1), ("graphic:g/outro-30", 0, 12),
    ]  # fmt: skip
    assert items[1]["video"] == "plays"
    assert ops.preview_source(project.root, "graphic:g/outro-30/11")["path"] == str(frames / "outro-30" / "f0011.png")
    with pytest.raises(ProjectError, match="does not name a graphic frame"):
        ops.preview_source(project.root, "graphic:g/outro-../0")


def test_a_render_drops_every_outro_a_cut_left_behind(project: Project) -> None:
    """One `outro-<n>/` per hold length a render asked for: the next render
    keeps the one it draws and drops the rest, in every graphic's folder."""
    _graphic(project, intro=1.0, outro=0.4)
    frames = _fake_capture(project, "g", intro=30, hold=1, outro=12, loop=False)
    record = json.loads((frames / motion.CAPTURE_NAME).read_text())
    record["video"] = {"videos": 1, "ends": 2.0}
    (frames / motion.CAPTURE_NAME).write_text(json.dumps(record))
    ops.overlay_add(project.root, None, "vo", 0, graphic="g", seconds=4.0)
    _fake_play(frames, 31, (30, 12))
    for stale in ("outro-12", "outro-31"):
        _fake_play(frames, 0, (int(stale.split("-")[1]), 12))
    unplaced = project.graphic_frames_dir / "other"
    (unplaced / "outro-7").mkdir(parents=True)
    (unplaced / "outro").mkdir()

    ops._build_mlt(project, ops._load_edit(project), fps=None)
    assert sorted(p.name for p in frames.iterdir() if p.is_dir()) == ["hold", "hold-play", "intro", "outro", "outro-30"]
    assert sorted(p.name for p in unplaced.iterdir()) == ["outro"], "a graphic no render draws keeps no outro-<n>"
    assert motion.sweep_outros(project.root / "absent", set()) == []


def _video_page(tmp_path: Path, **phases: float) -> tuple[Path, list[int]]:
    folder = tmp_path / "g"
    folder.mkdir()
    table = _numbered_clip(folder / "clip.mp4")
    (folder / "index.html").write_text(
        "<!doctype html><body style='margin:0'><video src='clip.mp4' muted style='display:block;width:64px'></video></body>"
    )
    motion.write_spec(folder, motion.normalise_spec(phases))
    return folder, table


def _shown(table: list[int], folder: Path) -> list[int]:
    return [_frame_of(table, _grey(path.read_bytes())) for path in sorted(folder.glob("f*.png"))]


@needs_browser
@needs_video_tools
def test_a_still_hold_plays_the_page_video_on_and_the_outro_picks_it_up(tmp_path: Path) -> None:
    """The footage is never sized to the intro: frame k of a 20-frame hold is
    source frame 15 + k, and the outro's video goes on from 35."""
    folder, table = _video_page(tmp_path, intro=0.5, outro=0.3)
    frames = tmp_path / "f"
    record = motion.capture(folder, frames, width=64, height=36, fps=30.0)
    assert record["video"]["videos"] == 1 and 1.9 < record["video"]["ends"] < 59 / 30
    assert _shown(table, frames / "intro") == list(range(15))
    assert _shown(table, frames / "outro") == list(range(15, 24)), "the page's own outro, for a hold of no frames"
    plan = motion.play(folder, frames, 20)
    assert plan is not None and plan["playing"] == 20 and plan["rest"] == 0
    assert _shown(table, frames / "hold-play") == list(range(15, 35))
    assert _shown(table, frames / "outro-20") == list(range(35, 44))


@needs_browser
@needs_video_tools
def test_a_hold_longer_than_its_video_rests_on_the_last_frame(tmp_path: Path) -> None:
    """The 2 s clip's last frame (59) is source time 1.967 s: hold frame 44.
    A 30-frame hold captured first is extended, not recaptured."""
    folder, table = _video_page(tmp_path, intro=0.5, outro=0.1)
    frames = tmp_path / "f"
    motion.capture(folder, frames, width=64, height=36, fps=30.0)
    motion.play(folder, frames, 30)
    first = (frames / "hold-play" / "f0000.png").stat().st_mtime_ns
    plan = motion.play(folder, frames, 90)
    assert plan == {"playing": 45, "rest": 45, "outro": 44, "frozen": 44}
    assert (frames / "hold-play" / "f0000.png").stat().st_mtime_ns == first
    assert _shown(table, frames / "hold-play") == list(range(15, 60))
    assert _shown(table, frames / "outro-44") == [59, 59, 59]
    assert motion.play(folder, frames, 200) == {"playing": 45, "rest": 155, "outro": 44, "frozen": 44}
