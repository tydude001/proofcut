"""Still images: added upright, shown full frame, and placed as stickers.

DAYDREAM.md § The gaps, re-ranked, item 3 ("images in"). Pinned here: a
photo's EXIF rotation is applied once at add, a PNG is copied untouched, a
sticker is composed onto the canvas with its box, the pop and slide motions'
keys (scaled about the box, clearing the frame edge), the overlay and cue
routes, and the refusals. What melt draws was read back by hand on the Pup BNB
copy (HISTORY.md § Images in, built).
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from proofcut import mlt, ops, stills
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

needs_magick = pytest.mark.skipif(shutil.which("magick") is None, reason="ImageMagick is not installed")

#: A 16x8 JPEG, red in its left quarter, whose EXIF says "rotate 90 clockwise"
#: (orientation 6) — a phone photo in miniature, made with Pillow.
EXIF_ROTATED_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4QAiRXhpZgAATU0AKgAAAAgAAQESAAMAAAABAAYAAAAAAAD/2wBDAAIBAQEB"
    "AQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/2wBDAQIC"
    "AgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAAR"
    "CAAIABADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdI"
    "SUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL"
    "/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcY"
    "GRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOk"
    "paanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD8"
    "0f2qv+YD/wBvX/tGvIaKK/1S+hL/AMoxZB/3Nf8AqbiT9A+l9/ykTnf/AHLf+omHP//Z"
)

CLIPS = {"vo": {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 6.0, "has_video": False, "has_audio": True}}


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


def _png(path: Path, size: str = "100x50", colour: str = "#e76f51") -> Path:
    subprocess.run(["magick", "-size", size, f"xc:{colour}", f"PNG32:{path}"], check=True)
    return path


def _pixel(path: Path, x: int, y: int) -> str:
    return subprocess.run(
        ["magick", str(path), "-format", f"%[pixel:p{{{x},{y}}}]", "info:"], capture_output=True, text=True, check=True
    ).stdout


# -- adding ------------------------------------------------------------------


@needs_magick
def test_a_phone_photo_is_turned_upright_once_at_add(tmp_path: Path) -> None:
    source = tmp_path / "phone.jpg"
    source.write_bytes(EXIF_ROTATED_JPEG)
    record = stills.add(source, tmp_path / "images", "phone")
    assert (record["width"], record["height"], record["how"]) == (8, 16, "turned upright")
    landed = tmp_path / "images" / "phone.jpg"
    assert stills.orientation(landed) in ("TopLeft", "Undefined")
    red = [int(v) for v in _pixel(landed, 4, 1).split("(")[1].rstrip(")").split(",")[:3]]
    assert red[0] > 200 and red[2] < 80, f"the red quarter is now on top, not {red}"


@needs_magick
def test_a_png_is_copied_untouched_and_a_taken_name_refused(tmp_path: Path) -> None:
    source = _png(tmp_path / "Logo Mark.png")
    record = stills.add(source, tmp_path / "images", stills.name_for(source))
    assert record["name"] == "logo-mark" and record["how"] == "copied"
    assert (tmp_path / "images" / "logo-mark.png").read_bytes() == source.read_bytes()
    with pytest.raises(stills.StillError, match="already exists"):
        stills.add(source, tmp_path / "images", "logo-mark")


def test_a_file_that_is_not_a_still_is_refused(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"x")
    with pytest.raises(stills.StillError, match="not a still"):
        stills.add(tmp_path / "clip.mp4", tmp_path / "images", "clip")


# -- stickers ----------------------------------------------------------------


@needs_magick
def test_a_sticker_is_composed_on_the_canvas_where_it_was_placed(tmp_path: Path) -> None:
    image = _png(tmp_path / "s.png", "200x100")
    png, box = stills.compose_sticker(image, tmp_path / "cache", (1920, 1080), {"x": 0.25, "y": 0.5, "width": 0.1, "rotate": 0, "style": "plain"})
    assert box == (384, 492, 576, 588), "192 by 96, centred on (480, 540)"
    alpha = subprocess.run(
        ["magick", str(png), "-alpha", "extract", "-format", "%[fx:p{10,10}] %[fx:p{480,540}]", "info:"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert alpha == ["0", "1"], "transparent off the sticker, opaque on it"
    again, box_again = stills.compose_sticker(image, tmp_path / "cache", (1920, 1080), {"x": 0.25, "y": 0.5, "width": 0.1, "rotate": 0, "style": "plain"})
    assert again == png and box_again == box, "cached by its inputs"
    _, photo_box = stills.compose_sticker(image, tmp_path / "cache", (1920, 1080), {"x": 0.25, "y": 0.5, "width": 0.1, "rotate": 0, "style": "photo"})
    assert photo_box[2] - photo_box[0] > 192, "a photo card's border and shadow widen it"


@needs_magick
def test_a_sticker_placed_off_the_frame_is_refused(tmp_path: Path) -> None:
    image = _png(tmp_path / "s.png")
    with pytest.raises(stills.StillError, match="off the frame"):
        stills.compose_sticker(image, tmp_path / "c", (1920, 1080), {"x": 1.9, "y": 0.5, "width": 0.1, "rotate": 0, "style": "plain"})


@pytest.mark.parametrize("bad", [{"width": 0}, {"style": "polaroid"}, {"x": 5}, {"rotate": "tilted"}])
def test_a_placement_that_cannot_place_is_refused(bad: dict) -> None:
    with pytest.raises(stills.StillError):
        stills.check_placement(bad)


# -- the motions ---------------------------------------------------------------


def _ov(**kw) -> mlt.Overlay:
    return mlt.Overlay(resource="/s.png", start=0, frames=60, **kw)


def test_a_pop_scales_about_the_stickers_own_centre_and_overshoots() -> None:
    keys = mlt.overlay_keys(_ov(in_motion="pop", in_frames=10, out_motion="none", box=(400, 200, 600, 400)), (1920, 1080))
    assert [k["frame"] for k in keys] == [0, 7, 10]
    first, peak, rest = keys
    assert first["opacity"] == 0 and peak["opacity"] == 1 and rest["rect"] == (0, 0, 1920, 1080)
    # The box's centre (500, 300) stays put while the canvas scales about it.
    for key, factor in ((first, mlt.POP_FROM), (peak, mlt.POP_OVERSHOOT)):
        x, y, w, h = key["rect"]
        assert abs(x + 500 * w / 1920 - 500) <= 1 and abs(y + 300 * h / 1080 - 300) <= 1
        assert w == round(1920 * factor)


@pytest.mark.parametrize(
    ("motion", "expect"),
    [("slide-left", lambda r: r[0] + 600 * r[2] / 1920 <= 0), ("slide-right", lambda r: r[0] + 400 >= 1920),
     ("slide-top", lambda r: r[1] + 400 <= 0), ("slide-bottom", lambda r: r[1] + 200 >= 1080)],
)
def test_a_slide_starts_just_clear_of_its_edge(motion: str, expect) -> None:
    keys = mlt.overlay_keys(_ov(in_motion=motion, in_frames=10, out_motion="none", box=(400, 200, 600, 400)), (1920, 1080))
    assert keys[0]["opacity"] == 1, "a sticker slides in opaque"
    assert expect(keys[0]["rect"]), keys[0]["rect"]


def test_a_pop_out_mirrors_the_pop_in() -> None:
    keys = mlt.overlay_keys(_ov(in_motion="none", out_motion="pop", out_frames=10, box=(0, 0, 100, 100)), (1920, 1080))
    assert [k["frame"] for k in keys] == [49, 52, 59]
    assert keys[-1]["opacity"] == 0 and keys[-1]["rect"][2] == round(1920 * mlt.POP_FROM)


# -- the ops -------------------------------------------------------------------


@needs_magick
def test_an_image_overlay_pops_in_by_default_and_keeps_its_placement(project: Project, tmp_path: Path) -> None:
    ops.image_add(project.root, _png(tmp_path / "dog.png"))
    added = ops.overlay_add(project.root, None, "vo", 1, image="dog", x=0.8, y=0.7, width=0.15, until_word_index=3)
    overlay = added["overlay"]
    assert (overlay["enter"], overlay["leave"]) == ("pop", "fade")
    assert (overlay["x"], overlay["y"], overlay["width"], overlay["style"]) == (0.8, 0.7, 0.15, "plain")
    item = next(o for o in ops.timeline_view(project.root)["overlays"] if o.get("image"))
    assert item["asset"].startswith("sticker:") and item["keys"][0]["opacity"] == 0
    assert "image:dog" in ops.timeline_view(project.root)["mentionable"]
    with pytest.raises(ProjectError, match="placed by"):
        ops.image_rm(project.root, "dog")


def test_a_card_with_a_placement_is_refused(project: Project) -> None:
    with pytest.raises(ProjectError, match="place an image"):
        ops.overlay_add(project.root, "c", "vo", 0, x=0.5, seconds=1.0)


@needs_magick
def test_an_image_is_a_picture_cue_and_a_still_has_no_in_point(project: Project, tmp_path: Path) -> None:
    ops.image_add(project.root, _png(tmp_path / "photo.png", "400x300"), name="photo")
    resolved = ops._resolve_asset(project, "image:photo")
    assert resolved["is_image"] and resolved["asset_path"].endswith("photo.png")
    assert ops.preview_source(project.root, "image:photo")["kind"] == "image"
    with pytest.raises((ProjectError, tx.TranscriptError), match="still"):
        ops.cue_add(project.root, "vo", 1, "image:photo", src_start=2.0)


@needs_magick
def test_list_media_lists_the_stills_beside_the_footage(project: Project, tmp_path: Path) -> None:
    folder = tmp_path / "sources"
    folder.mkdir()
    _png(folder / "a.png")
    (folder / "b.heic").write_bytes(b"x")
    listed = ops.list_media(project.root, folder)
    assert [Path(i["path"]).name for i in listed["images"]] == ["a.png", "b.heic"]
    ops.image_add(project.root, folder / "a.png")
    assert ops.list_media(project.root, folder)["images"][0]["already_added"]


@pytest.mark.parametrize("asset", ["sticker:../../etc/passwd", "sticker:XYZ", "image:../x"])
def test_a_sticker_or_image_key_cannot_climb_out(project: Project, asset: str) -> None:
    with pytest.raises(ProjectError):
        ops.preview_source(project.root, asset)
