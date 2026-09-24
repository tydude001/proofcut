"""Still images: added to a project, drawn full frame, or placed over the film as a sticker.

DAYDREAM.md § The gaps, re-ranked, item 3 ("images in"). A still lives at
`assets/images/<name>.<ext>` with a sidecar `<name>.json` saying where it came
from; nothing about it is in the manifest, so, like a card's PNG, undo moves
what places it and never the file.

**What lands is what every renderer reads the same way.** MLT's `qimage`, the
browser and `magick` do not agree on every format, and a phone photo's EXIF
orientation is honoured by some and ignored by others, which draws the photo
on its side in one of them at exit 0. So a PNG or a JPEG already upright is
copied as it is, and anything else is written once, upright, as a PNG (or a
JPEG for a camera format, where a PNG would be ten times the size).

A sticker (`compose_sticker`) is the still sized, optionally framed as a
photo card (white border, soft shadow), rotated, and placed on a transparent
canvas-sized PNG — so it rides the overlay stack exactly as an overlay card
does, and the box it occupies travels with it for the pop and slide motions.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from proofcut import graphics

#: What `image_add` takes, and what `list_media` lists under `images`.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".heic", ".heif", ".tif", ".tiff", ".bmp"})
#: Kept as they are when already upright.
_KEPT = {".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg"}
#: Camera formats, written as a JPEG rather than a PNG ten times the size.
_PHOTO = frozenset({".heic", ".heif"})
STYLES = ("plain", "photo")
#: Bumped when a sticker of the same inputs would compose differently.
COMPOSE_VERSION = 1

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


class StillError(RuntimeError):
    """A still that cannot be added, read or placed as asked."""


def check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise StillError(
            f"image name {name!r} must be lowercase letters, digits, '-' or '_', "
            "starting with a letter or digit, at most 48 characters"
        )
    return name


def name_for(source: Path) -> str:
    """A name from a filename: lowercase, anything else a dash."""
    stem = re.sub(r"[^a-z0-9_-]+", "-", source.stem.lower()).strip("-_") or "image"
    return stem[:48]


def _magick(*args: str, timeout: int = 120) -> str:
    done = subprocess.run([*graphics.magick_command(), *args], capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode != 0:
        raise StillError(f"magick refused: {(done.stderr or done.stdout).strip()[-600:]}")
    return done.stdout


def orientation(source: Path) -> str:
    """The EXIF orientation magick reads, `Undefined` where there is none."""
    return _magick("identify", "-format", "%[orientation]", f"{source}[0]").strip() or "Undefined"


def find(folder: Path, name: str) -> Path | None:
    """The still called `name` in `folder`, whatever its extension."""
    for ext in (".png", ".jpg"):
        if (folder / f"{name}{ext}").is_file():
            return folder / f"{name}{ext}"
    return None


def add(source: Path, folder: Path, name: str, *, replace: bool = False, label: str | None = None) -> dict[str, Any]:
    """Land `source` in `folder` as `name`, upright and in a format every renderer reads.

    `label` stands in for the source path where there is none worth keeping —
    an image pasted into the window arrives as a temporary upload."""
    check_name(name)
    source = Path(source).expanduser()
    if not source.is_file():
        raise StillError(f"{source} is not a file")
    ext = source.suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise StillError(f"{source.name} is not a still proofcut takes ({', '.join(sorted(IMAGE_EXTENSIONS))})")
    existing = find(folder, name)
    if existing is not None and not replace:
        raise StillError(f"an image called {name!r} already exists — pass replace, or another name")
    folder.mkdir(parents=True, exist_ok=True)
    turned = orientation(source) not in ("Undefined", "TopLeft")
    with tempfile.TemporaryDirectory(dir=folder) as scratch:
        if ext in _KEPT and not turned:
            landed = Path(scratch) / f"{name}{_KEPT[ext]}"
            shutil.copyfile(source, landed)
            how = "copied"
        else:
            out_ext = ".jpg" if (ext in _PHOTO or (ext in (".jpg", ".jpeg"))) else ".png"
            landed = Path(scratch) / f"{name}{out_ext}"
            quality = ("-quality", "95") if out_ext == ".jpg" else ()
            _magick(f"{source}[0]", "-auto-orient", *quality, str(landed))
            how = "turned upright" if turned else "converted"
        width, height = graphics.identify(landed)
        if existing is not None:
            existing.unlink()
        final = folder / landed.name
        landed.replace(final)
    record = {
        "name": name,
        "file": final.name,
        "source": label or str(source.resolve()),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "width": width,
        "height": height,
        "how": how,
    }
    (folder / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def read(folder: Path, name: str) -> dict[str, Any]:
    path = find(folder, name)
    if path is None:
        raise StillError(f"there is no image {name!r}")
    try:
        record = json.loads((folder / f"{name}.json").read_text())
    except (OSError, ValueError):
        width, height = graphics.identify(path)
        record = {"name": name, "file": path.name, "source": None, "width": width, "height": height}
    return {**record, "path": str(path)}


def listing(folder: Path) -> list[dict[str, Any]]:
    if not folder.is_dir():
        return []
    names = sorted({p.stem for p in folder.iterdir() if p.suffix in (".png", ".jpg") and _NAME.match(p.stem)})
    return [read(folder, n) for n in names]


# -- stickers ----------------------------------------------------------------


def sticker_key(image: Path, canvas: tuple[int, int], placement: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps([COMPOSE_VERSION, list(canvas), placement], sort_keys=True).encode())
    digest.update(hashlib.sha256(image.read_bytes()).digest())
    return digest.hexdigest()[:24]


def compose_sticker(
    image: Path, cache: Path, canvas: tuple[int, int], placement: dict[str, Any]
) -> tuple[Path, tuple[int, int, int, int]]:
    """The still as a transparent canvas-sized PNG, and the box it occupies.

    `placement` is `x`/`y` (the sticker's centre, as fractions of the canvas),
    `width` (a fraction of the canvas width), `rotate` (degrees, clockwise)
    and `style` (`plain`, or `photo`: a white border and a soft shadow, the
    tilted photo card). Cached by its inputs, so a build that asks again reads
    the file; the box is kept beside it rather than measured again.
    """
    key = sticker_key(image, canvas, placement)
    png, box_file = cache / f"{key}.png", cache / f"{key}.json"
    if png.is_file() and box_file.is_file():
        return png, tuple(json.loads(box_file.read_text())["box"])  # type: ignore[return-value]
    cache.mkdir(parents=True, exist_ok=True)
    width, height = canvas
    target_w = max(2, round(float(placement["width"]) * width))
    style = placement.get("style", "plain")
    rotate = float(placement.get("rotate", 0.0))
    with tempfile.TemporaryDirectory(dir=cache) as scratch:
        piece = Path(scratch) / "piece.png"
        args = [f"{image}[0]", "-auto-orient", "-resize", f"{target_w}x", "-background", "none"]
        if style == "photo":
            border = max(2, round(target_w * 0.035))
            sigma = max(2, round(target_w * 0.02))
            args += [
                "-bordercolor", "white", "-border", str(border),
                "(", "+clone", "-background", "black", "-shadow", f"55x{sigma}+0+{sigma}", ")",
                "+swap", "-background", "none", "-layers", "merge", "+repage",
            ]  # fmt: skip
        if rotate:
            args += ["-background", "none", "-rotate", f"{rotate:g}"]
        _magick(*args, f"PNG32:{piece}")
        pw, ph = graphics.identify(piece)
        left = round(float(placement["x"]) * width - pw / 2)
        top = round(float(placement["y"]) * height - ph / 2)
        _magick(
            "-size", f"{width}x{height}", "xc:none", str(piece), "-geometry", f"{left:+d}{top:+d}",
            "-composite", f"PNG32:{Path(scratch) / 'canvas.png'}",
        )  # fmt: skip
        box = (max(0, left), max(0, top), min(width, left + pw), min(height, top + ph))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise StillError("that placement puts the image entirely off the frame")
        (Path(scratch) / "canvas.png").replace(png)
    box_file.write_text(json.dumps({"box": list(box)}))
    return png, box


def check_placement(placement: dict[str, Any]) -> dict[str, Any]:
    """The sticker's numbers, refused where they cannot place one."""
    try:
        out = {
            "x": float(placement.get("x", 0.5)),
            "y": float(placement.get("y", 0.5)),
            "width": float(placement.get("width", 0.3)),
            "rotate": float(placement.get("rotate", 0.0)),
            "style": str(placement.get("style", "plain")),
        }
    except (TypeError, ValueError):
        raise StillError("x, y, width and rotate are numbers") from None
    if not 0 < out["width"] <= 2:
        raise StillError(f"width is a fraction of the frame's width, not {out['width']}")
    if not (-1 <= out["x"] <= 2 and -1 <= out["y"] <= 2):
        raise StillError("x and y are the centre as fractions of the frame, 0 to 1")
    if out["style"] not in STYLES:
        raise StillError(f"style is one of {', '.join(STYLES)}, not {out['style']!r}")
    return out
