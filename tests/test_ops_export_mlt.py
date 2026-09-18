"""`export` on a multi-source timeline — step 4 of the layered timeline.

The routing is the point. auto-editor 31.x refuses to *export* a second `src`
(exit 2) and *renders* one at 720x576 while exiting 0, so a project that has
grown a picture lane must never reach it — and the choice cannot be a flag,
because a flag can be left off. These tests are about which writer runs, and
about the frame grid the two halves of the document are quantised on.

The document's own structure is `test_mlt.py`; what melt does with it is
HISTORY.md § The MLT writer, measured against a real render rather than
asserted here (melt lives in a flatpak that cannot read `tmp_path`).
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from proofcut import autoeditor, mlt, ops, picture
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

EXPORT_FPS = 30.0


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """An audio-only VO cut to [0, 2) and [3, 6) — 5s of timeline — plus a
    video clip and a card to lay over it.

    The project timebase is milliseconds, which is the whole reason the export
    has to state its own rate: `build_shots` answers on the project's grid by
    default, and 1000 is not a frame rate.
    """
    project = Project.create(tmp_path / "proj")

    film = tmp_path / "film.mp4"
    film.write_bytes(b"not really a video, just needs to exist")

    manifest = project.read_manifest()
    manifest["clips"] = [
        {
            "clip_id": "vo",
            "source": str(tmp_path / "vo.wav"),
            "duration": 6.0,
            "has_video": False,
            "has_audio": True,
        },
        {
            "clip_id": "film",
            "source": str(film),
            "duration": 10.0,
            "has_video": True,
            "has_audio": True,
        },
    ]
    manifest["timebase"] = 1000.0
    project.write_manifest(manifest)

    edit = tl.Edit([tl.Segment("vo", 0.0, 2.0), tl.Segment("vo", 3.0, 6.0)])
    tl.write(
        tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0, name="proj"),
        project.timeline_path,
    )

    tx.save(
        tx.Transcript(
            clip_id="vo",
            words=tuple(
                tx.Word(index=i, text=text, start=start, end=end)
                for i, (text, start, end) in enumerate(
                    [("one", 0.2, 0.5), ("two", 1.2, 1.5), ("three", 3.4, 3.7), ("four", 4.6, 4.9)]
                )
            ),
        ),
        project.transcript_path("vo"),
    )

    project.cards_dir.joinpath("red.png").write_bytes(b"\x89PNG")
    return project


def _document(path: Path) -> ET.Element:
    return ET.fromstring(path.read_text(encoding="utf-8"))


def _declared(path: Path) -> int:
    """What a stand-in melt would report for a document: its own declared
    length. `mlt.declared_frames` has already refused a document whose spots
    disagree, so there is exactly one number to read back."""
    return next(iter(set(mlt.declared_frames(_document(path)).values())))


def test_a_cue_table_routes_the_export_through_the_mlt_writer(
    project: Project, tmp_path: Path
) -> None:
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")

    result = ops.export(project.root, tmp_path / "out.kdenlive")

    assert result["writer"] == "mlt"
    assert result["shots"] == 2
    assert result["sources"] == 3  # the VO, the film, the card
    assert Path(result["output"]).is_file()


def test_the_exported_document_is_the_edits_own_frame_total(
    project: Project, tmp_path: Path
) -> None:
    """Not `round(duration * fps)` — every segment edge quantises on its own,
    and the layout's sum is the timeline that actually gets rendered
    (CLAUDE.md)."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")

    result = ops.export(project.root, tmp_path / "out.kdenlive")

    edit = tl.read(project.timeline_path)
    expected = autoeditor.frame_total(edit, EXPORT_FPS)
    assert result["frames"] == expected
    assert set(mlt.declared_frames(_document(Path(result["output"]))).values()) == {expected}


def test_the_picture_lane_is_quantised_on_the_exports_grid_not_the_projects(
    project: Project, tmp_path: Path
) -> None:
    """The project's timebase is 1000 (milliseconds); the export's is 30. Ask
    for shots on the wrong grid and the lane comes out 33x too long, which
    `mlt.document` would refuse — so this asserts the frames that reached the
    document, not just that it was written."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")

    result = ops.export(project.root, tmp_path / "out.kdenlive")

    playlist = _document(Path(result["output"])).find("*[@id='playlist2']")
    assert playlist is not None
    lane = [int(e.get("out", "0")) - int(e.get("in", "0")) + 1 for e in playlist.findall("entry")]
    assert sum(lane) == result["frames"]
    assert ops.build_shots(project.root, fps=EXPORT_FPS)["total_frames"] == result["frames"]


def test_build_shots_defaults_to_the_projects_own_timebase(project: Project) -> None:
    """1000 frames a second, because that is what an audio-only project's
    timebase is. Stated so the default is a decision rather than a surprise."""
    ops.cue_add(project.root, "vo", 0, "film")

    assert ops.build_shots(project.root)["rate"] == 1000.0
    assert ops.build_shots(project.root, fps=EXPORT_FPS)["rate"] == EXPORT_FPS


def test_a_second_clip_on_the_timeline_routes_through_mlt_without_any_cues(
    project: Project, tmp_path: Path
) -> None:
    """Two `src` files is the wall, whether they arrived from a cue table or
    from the edit itself."""
    edit = tl.Edit([tl.Segment("vo", 0.0, 2.0), tl.Segment("film", 0.0, 3.0)])
    manifest = project.read_manifest()
    tl.write(
        tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0, name="proj"),
        project.timeline_path,
    )

    result = ops.export(project.root, tmp_path / "out.kdenlive")

    assert result["writer"] == "mlt"
    assert result["shots"] == 0
    assert result["sources"] == 2


def test_rendering_a_multi_source_timeline_goes_to_melt_not_auto_editor(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this routes around is silent: auto-editor renders two
    sources at 720x576 and exits 0, so a fallback would write a file that looks
    like a success. melt has no source-count gate."""
    ops.cue_add(project.root, "vo", 0, "film")
    seen: dict[str, Any] = {}

    def fake_render(project_file: Path, output: Path, **kwargs: Any) -> dict[str, Any]:
        seen["project_file"] = Path(project_file)
        seen["expect"] = kwargs
        return {"output": str(output), "width": 1920, "height": 1080, "frames": 150}

    monkeypatch.setattr(
        autoeditor,
        "run_timeline",
        lambda *a, **k: pytest.fail("auto-editor must never be handed a multi-source timeline"),
    )
    monkeypatch.setattr(picture, "project_frames", lambda p: _declared(Path(p)))
    monkeypatch.setattr(picture, "render", fake_render)

    result = ops.export(project.root, tmp_path / "out.mp4", export_format=None)

    assert result["writer"] == "melt"
    assert result["format"] == "media"
    assert result["rendered"]["width"] == 1920
    # What melt was handed, and what it was checked against.
    assert seen["expect"]["expect_frames"] == result["frames"]
    assert seen["expect"]["expect_resolution"] == (1920, 1080)
    assert seen["expect"]["expect_duration"] == pytest.approx(result["frames"] / EXPORT_FPS)


def test_the_rendered_document_is_written_where_melt_can_read_it(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not `/tmp`: melt runs from a flatpak that cannot see the host's, and
    exits 0 having read nothing (CLAUDE.md). The document is also the same one
    `export` would have written, not a second construction of it."""
    ops.cue_add(project.root, "vo", 0, "film")
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    handed: dict[str, Any] = {}

    def fake_project_frames(path: Path) -> int:
        handed["path"] = Path(path)
        handed["text"] = Path(path).read_text(encoding="utf-8")
        return _declared(Path(path))

    monkeypatch.setattr(picture, "project_frames", fake_project_frames)
    monkeypatch.setattr(
        picture, "render", lambda p, output, **k: {"output": str(output), "frames": 150}
    )

    ops.export(project.root, tmp_path / "out.mp4", export_format=None)
    exported = ops.export(project.root, tmp_path / "out.kdenlive")

    assert handed["path"].is_relative_to(tmp_path / "scratch")
    assert handed["text"] == Path(exported["output"]).read_text(encoding="utf-8")


def test_a_render_is_refused_before_encoding_when_melt_disagrees(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """melt renders to the longest declared length it finds, so a document it
    already reads as a different length would render that long and exit 0.
    Cheaper to catch with `-consumer xml` than with an encode."""
    ops.cue_add(project.root, "vo", 0, "film")
    monkeypatch.setattr(picture, "project_frames", lambda p: _declared(Path(p)) + 1)
    monkeypatch.setattr(
        picture, "render", lambda *a, **k: pytest.fail("the encode must not be spent")
    )

    with pytest.raises(ProjectError, match="refusing to spend an encode"):
        ops.export(project.root, tmp_path / "out.mp4", export_format=None)


def test_another_nles_format_is_refused_on_a_multi_source_timeline(
    project: Project, tmp_path: Path
) -> None:
    ops.cue_add(project.root, "vo", 0, "film")

    with pytest.raises(ProjectError, match="exit 2"):
        ops.export(project.root, tmp_path / "out.xml", export_format="premiere")


def test_mlt_is_a_spelling_of_the_same_export(project: Project, tmp_path: Path) -> None:
    """`.kdenlive` is MLT; the extension is the only thing Kdenlive cares
    about, so both names have to work and produce one document."""
    ops.cue_add(project.root, "vo", 0, "film")

    kdenlive = ops.export(project.root, tmp_path / "out.kdenlive")
    plain = ops.export(project.root, tmp_path / "out.mlt", export_format="mlt")

    assert Path(kdenlive["output"]).read_text() == Path(plain["output"]).read_text()


def test_a_single_source_timeline_with_no_cues_still_goes_to_auto_editor(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrowing, asserted from the other side: nothing about today's
    single-source behaviour moves (PLAN.md § What this does to "lucid never
    writes MLT"). Stubbed at `run_timeline` so the assertion is about the
    route taken, not about auto-editor being installed."""
    monkeypatch.setattr(
        autoeditor, "run_timeline", lambda payload, output, export=None: Path(output)
    )
    monkeypatch.setattr(
        autoeditor, "template", lambda source: {"version": "3", "timebase": "30/1"}
    )

    result = ops.export(project.root, tmp_path / "out.kdenlive")

    assert result["writer"] == "auto-editor"


# -- a tail: two ordinary entries after the last frame (PLAN.md § Tail time) -


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


@needs_ffmpeg
def test_a_tail_appends_a_card_and_silence_after_the_last_frame(
    project: Project, tmp_path: Path
) -> None:
    """The card lands on the picture lane's own playlist, a silent WAV of the
    same length lands on the edit's audio playlist — by construction the two
    stay equal, so `mlt.document`'s lane-covers-track check needs no change to
    accept either."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    without_tail = ops.export(project.root, tmp_path / "no-tail.kdenlive")

    ops.tail(project.root, asset="card:red", seconds=1.0)
    result = ops.export(project.root, tmp_path / "with-tail.kdenlive")

    tail_frames = round(1.0 * EXPORT_FPS)
    assert result["tail"] == {
        "asset": "card:red",
        "seconds": 1.0,
        "fade": 0.0,
        "frames": tail_frames,
    }
    assert result["frames"] == without_tail["frames"] + tail_frames

    document = _document(Path(result["output"]))
    assert _declared(Path(result["output"])) == result["frames"]

    audio_playlist = document.find("*[@id='playlist0']")
    picture_playlist = document.find("*[@id='playlist2']")
    assert audio_playlist is not None and picture_playlist is not None

    last_audio = audio_playlist.findall("entry")[-1]
    last_picture = picture_playlist.findall("entry")[-1]
    assert int(last_audio.get("out", "0")) - int(last_audio.get("in", "0")) + 1 == tail_frames
    assert int(last_picture.get("out", "0")) - int(last_picture.get("in", "0")) + 1 == tail_frames

    # The silence entry's own producer is a real avformat chain, not a still —
    # `document()` needed no new MLT concept, just one more of each ordinary
    # kind of node.
    silence_node = document.find(f"*[@id='{last_audio.get('producer')}']")
    assert silence_node is not None and silence_node.tag == "chain"
    silence_resource = silence_node.find("property[@name='resource']").text
    assert silence_resource.endswith(".wav")
    assert Path(silence_resource).is_file()

    card_node = document.find(f"*[@id='{last_picture.get('producer')}']")
    assert card_node.find("property[@name='mlt_service']").text == "qimage"
    assert card_node.find("property[@name='resource']").text.endswith("red.png")


@needs_ffmpeg
def test_a_tail_adds_exactly_seconds_never_seconds_plus_fade(
    project: Project, tmp_path: Path
) -> None:
    """The known trap, verified by frame readback rather than by reading the
    filter graph, exactly as CLAUDE.md's tail item requires: `xfade` finishes
    a transition at the length it is given, so a `fade` added on top of
    `seconds` would run the render long by exactly the fade. This asserts the
    frame count the document actually declares, not the arithmetic that
    produced it."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    without_tail = ops.export(project.root, tmp_path / "no-tail.kdenlive")

    ops.tail(project.root, asset="card:red", seconds=2.0, fade=0.5)
    result = ops.export(project.root, tmp_path / "with-tail.kdenlive")

    assert result["frames"] == without_tail["frames"] + round(2.0 * EXPORT_FPS)
    assert _declared(Path(result["output"])) == result["frames"]


def test_a_tail_with_no_picture_lane_is_refused(project: Project, tmp_path: Path) -> None:
    """No cues at all — the film's own picture, if any, comes straight off its
    clip, and there is no second lane a card could join without duplicating
    the whole film onto one just to make room for the last few seconds."""
    ops.tail(project.root, asset="card:red", seconds=1.0)

    with pytest.raises(ProjectError, match="picture cue lane"):
        ops.export(project.root, tmp_path / "out.kdenlive")


@needs_ffmpeg
def test_a_tail_on_a_picture_timeline_with_no_cues_goes_on_the_edits_own_track(
    project: Project, tmp_path: Path
) -> None:
    """RECUT.md step 3: B7's bumper never rendered, because its film was a
    screen recording with no cues. The card now follows the Edit on its own
    track, and no picture lane is made: one would draw over the insets."""
    manifest = project.read_manifest()
    edit = tl.Edit([tl.Segment("film", 0.0, 2.0), tl.Segment("film", 4.0, 7.0)])
    tl.write(
        tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0, name="proj"),
        project.timeline_path,
    )
    ops.tail(project.root, asset="card:red", seconds=1.0)

    built = ops._build_mlt(project, ops._load_edit(project), fps=EXPORT_FPS)
    result = ops.export(project.root, tmp_path / "out.kdenlive")

    assert built["on_edit_track"] is True
    assert result["frames"] == round(5.0 * EXPORT_FPS) + round(1.0 * EXPORT_FPS)
    assert _declared(Path(result["output"])) == result["frames"]
    document = _document(Path(result["output"]))
    assert document.find("*[@id='playlist2']") is None
    edit_entries = document.find("*[@id='playlist0']").findall("entry")
    card = document.find(f"*[@id='{edit_entries[-1].get('producer')}']")
    assert card.find("property[@name='mlt_service']").text == "qimage"
    assert card.find("property[@name='resource']").text.endswith("red.png")
    assert int(edit_entries[-1].get("out")) - int(edit_entries[-1].get("in")) + 1 == round(1.0 * EXPORT_FPS)


def test_a_cue_less_project_with_no_tail_is_not_on_the_edit_track(project: Project) -> None:
    """Nothing moves for a project without a head or tail."""
    manifest = project.read_manifest()
    edit = tl.Edit([tl.Segment("film", 0.0, 2.0)])
    tl.write(
        tl.to_otio(edit, {c["clip_id"]: c for c in manifest["clips"]}, rate=1000.0, name="proj"),
        project.timeline_path,
    )

    built = ops._build_mlt(project, ops._load_edit(project), fps=EXPORT_FPS)

    assert built["on_edit_track"] is False


def test_a_tail_asset_that_resolves_to_a_clip_is_refused_at_build_time(
    project: Project, tmp_path: Path
) -> None:
    """`tail()` itself already refuses a non-card asset; this is the same
    refusal from the writer's own side, reached by a manifest edited by hand
    or carried over from a project `tail()` never touched."""
    ops.cue_add(project.root, "vo", 0, "film")
    manifest = project.read_manifest()
    manifest["tail"] = {"asset": "film", "seconds": 1.0, "fade": 0.0}
    project.write_manifest(manifest)

    with pytest.raises(ProjectError, match="not a card"):
        ops.export(project.root, tmp_path / "out.kdenlive")


@needs_ffmpeg
def test_check_frames_agrees_with_what_a_tail_export_actually_writes(
    project: Project, tmp_path: Path
) -> None:
    """`check_frames`' `expected_frames` and `_build_mlt`'s own `frames` are
    the same call (`_frame_total_with_tail`) — this is that agreement, against
    the document actually written rather than against each other's arithmetic."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    ops.tail(project.root, asset="card:red", seconds=1.0)

    result = ops.export(project.root, tmp_path / "out.kdenlive")
    checked = ops.check_frames(project.root, fps=EXPORT_FPS)

    assert checked["expected_frames"] == result["frames"]


# -- a head: two ordinary entries before the first frame (PLAN.md § Tail time,
# mirrored at the other end) -------------------------------------------------


@needs_ffmpeg
def test_a_head_prepends_the_clip_and_its_audio_before_the_first_frame(
    project: Project, tmp_path: Path
) -> None:
    """The clip's own audio lands on the edit's audio playlist, its picture on
    the picture lane's own playlist — both *prepended*, so `mlt.document`'s
    lane-covers-track check needs no change to accept either, `tail`'s own
    test mirrored at the other end. `gain_db=15.1` with no fades still emits
    a flat two-key `volume` filter — the plateau `_fade_level` generalized
    from a hardcoded 0."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    without_head = ops.export(project.root, tmp_path / "no-head.kdenlive")

    ops.head(project.root, asset="film", src_start=1.0, seconds=1.0, gain_db=15.1)
    result = ops.export(project.root, tmp_path / "with-head.kdenlive")

    head_frames = round(1.0 * EXPORT_FPS)
    assert result["head"] == {
        "asset": "film",
        "src_start": 1.0,
        "seconds": 1.0,
        "fade_in": 0.0,
        "fade_out": 0.0,
        "gain_db": 15.1,
        "frames": head_frames,
    }
    assert result["frames"] == without_head["frames"] + head_frames

    document = _document(Path(result["output"]))
    assert _declared(Path(result["output"])) == result["frames"]

    audio_playlist = document.find("*[@id='playlist0']")
    picture_playlist = document.find("*[@id='playlist2']")
    assert audio_playlist is not None and picture_playlist is not None

    first_audio = audio_playlist.findall("entry")[0]
    first_picture = picture_playlist.findall("entry")[0]
    assert int(first_audio.get("out", "0")) - int(first_audio.get("in", "0")) + 1 == head_frames
    assert (
        int(first_picture.get("out", "0")) - int(first_picture.get("in", "0")) + 1 == head_frames
    )
    assert int(first_audio.get("in", "0")) == round(1.0 * EXPORT_FPS)
    assert int(first_picture.get("in", "0")) == round(1.0 * EXPORT_FPS)

    # A flat gain with no fades still emits a filter — a constant two-key
    # animation, never the fade-free "no filter at all" a fade-free entry gets.
    filt = first_audio.find("filter")
    assert filt is not None
    level = filt.find("property[@name='level']")
    # Keyframe positions are relative to the *producer*, offset by this
    # entry's own `src_in` (`round(1.0 * EXPORT_FPS)`, this head's
    # `src_start`) — not 0-based, a real melt render of a nonzero-`src_in`
    # faded entry played back total silence under the old 0-based positions
    # (mlt.py's own `_fade_level` docstring; caught by the holds lane's own
    # readback test, the first caller with a nonzero `src_in`).
    src_in = round(1.0 * EXPORT_FPS)
    assert level is not None and level.text == f"{src_in}=15.1;{src_in + head_frames - 1}=15.1"
    # The lane's own picture entry carries no gain — only the audio side is
    # a "level" concept, and the head's own lane node is otherwise ordinary.
    assert first_picture.find("filter") is None

    # Both entries point at the head's own asset — `film` — resolved by
    # `_resolve_asset` the same way any other picture cue's clip is.
    audio_resource = document.find(f"*[@id='{first_audio.get('producer')}']")
    picture_resource = document.find(f"*[@id='{first_picture.get('producer')}']")
    assert audio_resource is not None
    assert audio_resource.find("property[@name='resource']").text.endswith("film.mp4")
    assert picture_resource is not None
    assert picture_resource.find("property[@name='resource']").text.endswith("film.mp4")


@needs_ffmpeg
def test_a_head_and_a_tail_together_bookend_the_document(
    project: Project, tmp_path: Path
) -> None:
    """A head prepends, a tail appends, and the frame total grows by both —
    the two mechanisms share nothing but the pattern."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    without_either = ops.export(project.root, tmp_path / "no-bookends.kdenlive")

    ops.head(project.root, asset="film", seconds=1.0)
    ops.tail(project.root, asset="card:red", seconds=2.0)
    result = ops.export(project.root, tmp_path / "with-bookends.kdenlive")

    head_frames = round(1.0 * EXPORT_FPS)
    tail_frames = round(2.0 * EXPORT_FPS)
    assert result["frames"] == without_either["frames"] + head_frames + tail_frames
    assert _declared(Path(result["output"])) == result["frames"]

    document = _document(Path(result["output"]))
    audio_playlist = document.find("*[@id='playlist0']")
    assert audio_playlist is not None
    entries = audio_playlist.findall("entry")
    first, last = entries[0], entries[-1]
    assert int(first.get("out", "0")) - int(first.get("in", "0")) + 1 == head_frames
    assert int(last.get("out", "0")) - int(last.get("in", "0")) + 1 == tail_frames


def test_a_head_with_no_picture_lane_is_refused(project: Project, tmp_path: Path) -> None:
    """No cues at all — `tail`'s own refusal, mirrored: there is no second
    lane a head could join without duplicating the whole film onto one just
    to make room for the first few seconds."""
    ops.head(project.root, asset="film", seconds=1.0)

    with pytest.raises(ProjectError, match="picture cue lane"):
        ops.export(project.root, tmp_path / "out.kdenlive")


def test_a_head_asset_that_resolves_to_a_card_is_refused_at_build_time(
    project: Project, tmp_path: Path
) -> None:
    """`head()` itself already refuses a card asset; this is the same
    refusal from the writer's own side, reached by a manifest edited by hand
    or carried over from a project `head()` never touched."""
    ops.cue_add(project.root, "vo", 0, "film")
    manifest = project.read_manifest()
    manifest["head"] = {
        "asset": "card:red",
        "src_start": 0.0,
        "seconds": 1.0,
        "fade_in": 0.0,
        "fade_out": 0.0,
        "gain_db": 0.0,
    }
    project.write_manifest(manifest)

    with pytest.raises(ProjectError, match="resolved to a card"):
        ops.export(project.root, tmp_path / "out.kdenlive")


@needs_ffmpeg
def test_check_frames_agrees_with_what_a_head_export_actually_writes(
    project: Project, tmp_path: Path
) -> None:
    """The same agreement `tail`'s own version of this test proves, for the
    other bookend."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    ops.head(project.root, asset="film", seconds=1.0)

    result = ops.export(project.root, tmp_path / "out.kdenlive")
    checked = ops.check_frames(project.root, fps=EXPORT_FPS)

    assert checked["expected_frames"] == result["frames"]


# -- the two-clock ruling: `locate`/`timeline_view` stay Edit-relative ------


def test_locate_stays_edit_relative_with_a_head_but_reports_head_seconds(
    project: Project,
) -> None:
    """RULING (overrides brief 01's open question 1): the web player cannot
    play a cold open yet, so `locate` keeps the `Edit`'s own clock even once
    a head is configured — `timeline_start`/`timeline_end` must not move.
    `head_seconds` is the offset a render-time caller adds itself."""
    without_head = ops.locate(project.root, "vo", first=2)
    assert without_head["head_seconds"] == 0.0

    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    ops.head(project.root, asset="film", seconds=2.0)

    with_head = ops.locate(project.root, "vo", first=2)
    assert with_head["timeline_start"] == without_head["timeline_start"]
    assert with_head["timeline_end"] == without_head["timeline_end"]
    assert with_head["head_seconds"] == pytest.approx(2.0)


def test_timeline_view_reports_head_but_segments_stay_edit_relative(
    project: Project,
) -> None:
    """The same ruling, on `timeline_view`: `head_seconds` (0.0 with none)
    and `head` (the stored config plus its resolved frame count, `tail`'s
    own echo shape) are new; `segments`/`shots` do not move."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")
    without_head = ops.timeline_view(project.root, clip_id="vo")
    assert without_head["head_seconds"] == 0.0
    assert without_head["head"] is None

    ops.head(project.root, asset="film", src_start=1.0, seconds=2.0, gain_db=15.1)
    with_head = ops.timeline_view(project.root, clip_id="vo")

    assert with_head["head_seconds"] == pytest.approx(2.0)
    assert with_head["head"] == {
        "asset": "film",
        "src_start": 1.0,
        "seconds": 2.0,
        "fade_in": 0.0,
        "fade_out": 0.0,
        "gain_db": 15.1,
        "frames": round(2.0 * EXPORT_FPS),
    }
    # A head is not part of the `Edit` — segments and shots stay exactly
    # what they were, the ruling's whole point.
    assert with_head["segments"] == without_head["segments"]
    assert with_head["shots"] == without_head["shots"]


@needs_ffmpeg
def test_add_captions_shifts_the_ass_write_by_head_seconds(
    project: Project, tmp_path: Path
) -> None:
    """`add_captions` is the one render-facing shift this feature owns: the
    burn target is the real export, whose own first frame is `head_seconds`
    before the `Edit`'s. `caption_view`/`_caption_cues` are untouched —
    only the ASS write moves."""
    ops.cue_add(project.root, "vo", 0, "film")
    ops.cue_add(project.root, "vo", 2, "card:red")

    without_head = ops.add_captions(project.root, tmp_path / "no-head.ass")
    assert without_head["head_seconds"] == 0.0

    ops.head(project.root, asset="film", seconds=2.0)
    with_head = ops.add_captions(project.root, tmp_path / "with-head.ass")
    assert with_head["head_seconds"] == pytest.approx(2.0)

    # Every cue timestamp in the file is shifted by exactly the head's own
    # length — read back from the ASS file itself, not from the in-memory
    # cues, since the write is the thing that must have moved.
    unshifted_text = Path(without_head["output"]).read_text(encoding="utf-8")
    shifted_text = Path(with_head["output"]).read_text(encoding="utf-8")

    def _first_start(text: str) -> float:
        for line in text.splitlines():
            if line.startswith("Dialogue:"):
                stamp = line.split(",")[1]
                h, m, s = stamp.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
        raise AssertionError("no Dialogue line in the .ass file")

    assert _first_start(shifted_text) == pytest.approx(_first_start(unshifted_text) + 2.0, abs=0.02)


def test_verify_trims_a_heads_own_words_and_reports_the_count(
    project: Project, tmp_path: Path
) -> None:
    """A configured head's own dialogue transcribes at the front of the
    heard sequence with nothing in `expected` (Edit-only words) to match it
    against — trimmed before the diff, and the count reported as
    `head_words_trimmed` rather than silently applied (`unspoken`'s own
    convention). `transcript_path` stands in for a real whisper pass, the
    same fixture shape `test_server_stdio.py`'s own verify tests use."""
    import json

    ops.head(project.root, asset="film", seconds=2.0)

    # The body's own four transcript words map onto the Edit at [0.2, 1.2,
    # 2.4, 3.6] (see the fixture's own timeline math); a real render shifts
    # every one of them forward by `head_seconds`, and the head's own two
    # words sit ahead of that.
    heard_path = tmp_path / "heard.json"
    heard_path.write_text(
        json.dumps(
            {
                "language": "en",
                "words": [
                    {"word": "intro", "start": 0.5, "end": 0.8},
                    {"word": "beat", "start": 1.0, "end": 1.3},
                    {"word": "one", "start": 2.2, "end": 2.5},
                    {"word": "two", "start": 3.2, "end": 3.5},
                    {"word": "three", "start": 4.4, "end": 4.7},
                    {"word": "four", "start": 5.6, "end": 5.9},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = ops.verify(
        project.root, tmp_path / "render.mp4", transcript_path=heard_path
    )

    assert result["head_seconds"] == pytest.approx(2.0)
    assert result["head_words_trimmed"] == 2
    assert result["heard_words"] == 4
    assert result["similarity"] == 1.0
    assert result["dropped"] == []
    assert result["repeated"] == []


def test_verify_reports_zero_head_words_trimmed_with_no_head(
    project: Project, tmp_path: Path
) -> None:
    import json

    heard_path = tmp_path / "heard.json"
    heard_path.write_text(
        json.dumps(
            {
                "language": "en",
                "words": [
                    {"word": "one", "start": 0.2, "end": 0.5},
                    {"word": "two", "start": 1.2, "end": 1.5},
                    {"word": "three", "start": 2.4, "end": 2.7},
                    {"word": "four", "start": 3.6, "end": 3.9},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = ops.verify(
        project.root, tmp_path / "render.mp4", transcript_path=heard_path
    )

    assert result["head_seconds"] == 0.0
    assert result["head_words_trimmed"] == 0
    assert result["similarity"] == 1.0



def test_loudness_is_refused_on_an_nle_export(tmp_path: Path) -> None:
    """Mastering is for rendered media — an NLE project file has no audio of
    its own to master (docs/plans/NATIVE.md § A3)."""
    from proofcut.project import Project, ProjectError

    project = Project.create(tmp_path / "proj")
    with pytest.raises(ProjectError, match="loudness masters rendered media"):
        ops.export(project.root, tmp_path / "out.kdenlive", export_format="kdenlive", loudness=-16.0)
