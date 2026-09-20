"""Rasterise a card's SVG into the PNG a picture cue resolves to.

Step 1 of PLAN.md § Motion graphics and templates. The finding that shapes
this module is that motion graphics need no new timeline mechanism — the
Scream assembly's cards already ride the cue table as stills — so what was
missing is a *generator* for the asset. This is its renderer.

`magick` is shelled the way `asr` shells whisper and `picture` shells melt:
an external renderer with a resolution order and no Python API worth binding.
So this module has no proofcut dependencies beyond `captions.font_match`, which
it reuses rather than growing a second font check.

Four things measured on this box, 2026-08-09, that the code below depends on:

* **There is a real SVG rasteriser here and the obvious probes miss it.**
  `rsvg-convert`, `inkscape`, `resvg` and `cairosvg` are all absent;
  ImageMagick 7.1.2 links **librsvg 2.62.0** as its SVG coder. The row that
  shows it is `RSVG` in `magick -list format`, not ImageMagick's own weak
  internal `MSVG`. Details: wiki `tooling.md` § Rasterising SVG.
* **`-size WxH` before the input is a vector render at that size; `-resize`
  after it is a resample.** Measured on the same 1920x1080 card: `-size`
  gave a clean 8-bit/256-colour raster, `-resize` a 16-bit one an order of
  magnitude larger — it rasterises at the document's native size and then
  scales the pixels, which is exactly what you do not want done to text. So
  the size knob goes *before* the input, and `RENDER_FIT` records that it
  fits rather than distorts: 1920x816 asked of a 16:9 document gives
  1450x816, not a squashed 1920.
* **A missing font renders pixel-identical at exit 0** — the caption trap
  (CLAUDE.md), reproduced on a second renderer. The same card naming
  `Noto Sans` and naming a face that does not exist compared at AE 0. So
  the font report is the *only* guard there is, and like `captions.font_match`
  it reports rather than prevents.
* **Unlike melt, magick's exit code can be trusted here.** A truncated SVG
  and a file that is not SVG at all both exit 1 with a message naming
  `RenderRSVGImage`. That is worth writing down only because so much else in
  this repo exits 0 on failure; it means this module does not need to prove
  the output exists by other means. It reads the finished raster's dimensions
  back anyway — `mlt.declared_frames`' discipline — because "what size is the
  card" is a question about the file, not about the arguments.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from proofcut.captions import font_match
from proofcut.picture import command_override


class GraphicsError(RuntimeError):
    """A card could not be rendered."""


#: How `-size` treats an aspect it cannot match exactly. Recorded as a
#: constant because it is the whole of finding 4 in the design note: cards
#: pillarbox not because fitting is wrong but because a card authored at a
#: different aspect from its film has nowhere else to go. Step 3 closes that
#: by authoring at the canvas size; nothing here distorts to hide it.
RENDER_FIT = "fit"

#: CSS generic families. fontconfig resolves every one of them, so asking
#: `font_match` whether "sans-serif" is *installed* answers False about a
#: request that was never for a particular face — a false alarm of exactly
#: the kind the design note's finding 3 exists to not repeat.
GENERIC_FAMILIES = frozenset(
    {
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
        "math",
        "emoji",
        "fangsong",
        "inherit",
        "initial",
        "revert",
        "unset",
    }
)

_SVG_NS = "http://www.w3.org/2000/svg"

#: `selector { declarations }`, for the CSS inside a `<style>` element.
_RULE_BODY = re.compile(r"\{([^}]*)\}")


def magick_command() -> list[str]:
    """The argv prefix that runs ImageMagick, however it is installed here.

    A list rather than a path for the same reason `picture.melt_command` is
    one — `PROOFCUT_MAGICK` may name a wrapper with arguments. IM6's `convert`
    is deliberately not searched: its SVG handling is a different renderer
    with different defaults, and silently rendering through one when the
    box's measurements were taken on the other is this repo's recurring
    failure shape.
    """
    override = os.environ.get("PROOFCUT_MAGICK")
    if override:
        return command_override(override)
    found = shutil.which("magick")
    if found:
        return [found]
    raise GraphicsError(
        "magick not found. Card rendering needs ImageMagick 7 with its RSVG "
        "coder (`magick -list format | grep RSVG`), which is what rasterises "
        "the SVG. Install ImageMagick, or set PROOFCUT_MAGICK to a command that "
        "runs it."
    )


#: Inherited unchanged from `reframe_sheet`'s own inline call, which is the
#: larger of the two callers — it montages a tile per framing window (58 on the
#: vertical cut) where a shot sheet montages one page.
MONTAGE_TIMEOUT = 600


def montage(
    tiles: Sequence[Path | str],
    out: Path | str,
    *,
    columns: int,
    tile_width: int,
    background: str = "#222",
    gutter: int = 3,
    quality: int | None = None,
) -> Path:
    """Combine `tiles` into one grid image at `out`, and return it.

    The format is `out`'s own suffix, which is how the two callers differ:
    `reframe_sheet` writes a PNG for a person to open, and `shot_sheet` a JPEG
    because its bytes travel base64 inside an MCP tool result.

    One copy, two callers — `reframe_sheet`'s framing tiles and `shot_sheet`'s
    picture-track tiles. It was `reframe_sheet`'s inline argv until the second
    caller arrived; a second hand-built `montage` invocation is how the two
    would drift into disagreeing about gutters and background, which is the
    difference between "these two tiles are one shot" and "these two tiles are
    adjacent", read off a picture.

    `-geometry {width}x+{g}+{g}` is the whole layout: it scales each tile to a
    common width and gives it a gutter, which is what stops two dark tiles
    from running together into one apparent frame. A grid is `{columns}x` with
    the row count left to magick, so a short last row is a short row rather
    than a stretched one.

    `quality` is passed through only when given, because it does not mean one
    thing across formats — for JPEG it is the quantiser, for PNG it is a
    zlib/filter pair — so a default here would silently re-encode the callers
    that write PNG. Omitted, magick's own default stands and an existing
    caller's bytes do not move.

    Trusts magick's exit code, which this module's docstring establishes is
    safe here and is not safe for melt — and checks the file exists anyway,
    because "did it write the thing" is a question about the file.
    """
    if not tiles:
        raise GraphicsError("nothing to montage — no tiles were drawn")
    if columns < 1:
        raise GraphicsError(f"a montage needs at least one column, got {columns}")

    sheet = Path(out).expanduser()
    sheet.parent.mkdir(parents=True, exist_ok=True)
    command = [
        *magick_command(), "montage", *[str(tile) for tile in tiles],
        "-tile", f"{columns}x",
        "-geometry", f"{tile_width}x+{gutter}+{gutter}",
        "-background", background,
        *(["-quality", str(quality)] if quality is not None else []),
        str(sheet),
    ]  # fmt: skip
    done = subprocess.run(
        command, capture_output=True, text=True, timeout=MONTAGE_TIMEOUT, check=False
    )
    if done.returncode != 0 or not sheet.exists():
        raise GraphicsError(f"magick could not montage the sheet: {done.stderr[-800:]}")
    return sheet


def _families(declaration: str) -> list[str]:
    """Split one `font-family` value into the faces it lists, in order."""
    families = []
    for part in declaration.split(","):
        name = part.strip().strip("\"'").strip()
        if name:
            families.append(name)
    return families


def _declarations(style: str, prop_name: str = "font-family") -> list[str]:
    """Every value for `prop_name` in a CSS declaration block."""
    found = []
    for chunk in style.split(";"):
        prop, sep, value = chunk.partition(":")
        if sep and prop.strip().casefold() == prop_name and value.strip():
            found.append(value.strip())
    return found


#: CSS's own `bolder`/`lighter` table (CSS Fonts 4 § relative weights). Kept
#: because the alternative — treating them as "inherit" — is wrong in
#: silence, and this is nine lines rather than a measurement.
#: Read as "inherited at or below `above` becomes `to`".
_RELATIVE_WEIGHT = {
    "bolder": ((300, 400), (500, 700), (1000, 900)),
    "lighter": ((500, 100), (700, 400), (1000, 700)),
}

#: The CSS default. An element that names a family and no weight is drawn at
#: `normal`, and saying so beats reporting the weight as unknown — unknown is
#: reserved for the case below where it genuinely is.
DEFAULT_WEIGHT = 400


def _weight(value: str | None, inherited: int) -> int:
    """One `font-weight` value resolved against the weight it inherits."""
    if value is None:
        return inherited
    text = value.strip().casefold()
    if not text or text == "inherit":
        return inherited
    if text == "normal":
        return 400
    if text == "bold":
        return 700
    if text in _RELATIVE_WEIGHT:
        return next(to for above, to in _RELATIVE_WEIGHT[text] if inherited <= above)
    try:
        return max(1, min(1000, int(float(text))))
    except ValueError:
        return inherited


def _declared_weight(element: ET.Element) -> str | None:
    """The `font-weight` an element states, attribute or inline style."""
    inline = _declarations(element.get("style") or "", "font-weight")
    if inline:
        return inline[-1]
    return element.get("font-weight")


def declared_faces(svg: str) -> list[dict[str, Any]]:
    """Every distinct `(font-family, font-weight)` an SVG asks for.

    Walks the parsed tree rather than pattern-matching the markup, because
    the three places a family can be named — the presentation attribute, an
    inline `style=`, and CSS in a `<style>` element — do not share a syntax,
    and a regex loose enough to catch all three swallows the rest of the tag.

    **A family alone does not say which face gets drawn, and that is the
    whole reason this walks rather than collects.** Both properties inherit,
    so `receipt.svg`'s title — `font-family="…" font-weight="700"` around a
    `<tspan font-weight="400">` — asks for *two* faces of one family, and the
    tspan names no family at all. Reporting per family would answer once, for
    neither of them.

    `weight` is null only for a family named inside a `<style>` rule whose
    own body states no weight: a rule is attached by a selector this does not
    evaluate, so which elements it reaches — and what they inherit — is not
    knowable here. Null is "we did not evaluate the cascade", not `normal`.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise GraphicsError(f"not well-formed XML: {exc}") from exc

    seen: dict[tuple[str, int | None], dict[str, Any]] = {}

    def note(declaration: str, weight: int | None) -> None:
        seen.setdefault((declaration, weight), {"declared": declaration, "weight": weight})

    def walk(element: ET.Element, family: str | None, weight: int) -> None:
        if element.tag in ("style", f"{{{_SVG_NS}}}style") and element.text:
            for body in _RULE_BODY.findall(element.text):
                stated = _declarations(body, "font-weight")
                rule_weight = _weight(stated[-1], DEFAULT_WEIGHT) if stated else None
                for declaration in _declarations(body):
                    note(declaration, rule_weight)

        stated_family = element.get("font-family")
        inline = _declarations(element.get("style") or "")
        if inline:
            stated_family = inline[-1]
        stated_weight = _declared_weight(element)

        if stated_family and stated_family.strip():
            family = stated_family.strip()
        weight = _weight(stated_weight, weight)

        # Emitted when the element *states* something, so a `<g>` that sets a
        # family for its children is reported once rather than once per child,
        # and a `<tspan>` that changes only the weight is reported at all.
        if family and (stated_family or stated_weight):
            note(family, weight)

        for child in element:
            walk(child, family, weight)

    walk(root, None, DEFAULT_WEIGHT)
    return list(seen.values())


def declared_fonts(svg: str) -> list[str]:
    """Every distinct `font-family` declaration in an SVG, in document order.

    The families of `declared_faces`, deduped. Kept because "which families
    does this document name" is a question two callers ask without caring
    about weight, and because it is the cheapest well-formedness check there
    is.
    """
    families: dict[str, None] = {}
    for face in declared_faces(svg):
        families.setdefault(face["declared"], None)
    return list(families)


def font_report(svg: str) -> list[dict[str, Any]]:
    """What fontconfig will actually draw for each face `svg` asks for.

    One entry per *declaration and weight* rather than per face, because a
    declaration is a fallback stack and the stack is what decides the
    outcome: `'Card Face', sans-serif` with the first installed is not a
    substitution, and reporting its second entry as missing would be a
    warning about working output.

    **The weight is half the answer.** Two faces of one family report the
    same family name, so `drawn` alone cannot say which got picked — `style`
    is what separates SemiBold from Bold, and the weight is what selects it.
    Before this was weight-aware the report was already wrong about a shipped
    template: `receipt.svg`'s title is `font-weight="700"`, librsvg draws
    Bold, and asking fontconfig for the family alone answers SemiBold
    wherever both are installed.

    `available` is tri-state, inherited from `captions.font_match`: True when
    some named face in the stack is installed, False when none is and the
    card will be drawn in whatever fontconfig picks, and None when `fc-match`
    could not be reached at all — "we could not tell" and "the font is not
    here" send someone to different places.
    """
    cache: dict[tuple[str, int | None], dict[str, Any]] = {}

    def match(family: str, weight: int | None) -> dict[str, Any]:
        # Cached for this document only. A process-lifetime cache would go
        # stale against a font installed while proofcut is running, and the
        # thing this report exists to catch is a font that is not there.
        if (family, weight) not in cache:
            cache[(family, weight)] = font_match(family, weight=weight)
        return cache[(family, weight)]

    report = []
    for face in declared_faces(svg):
        declaration, weight = face["declared"], face["weight"]
        families = _families(declaration)
        named = [f for f in families if f.casefold() not in GENERIC_FAMILIES]
        matches = [match(f, weight) for f in named]

        installed = next((m for m in matches if m["available"]), None)
        style = None
        if installed is not None:
            available: bool | None = True
            drawn = installed["resolves_to"]
            style = installed["style"]
        elif not named:
            # An all-generic stack asked for no particular face, so nothing
            # was substituted for anything.
            available = True
            generic = match(families[0], weight) if families else None
            drawn = generic["resolves_to"] if generic else None
            style = generic["style"] if generic else None
        elif all(m["available"] is None for m in matches):
            available = None
            drawn = None
        else:
            available = False
            fallback = next((m for m in matches if m["resolves_to"]), None)
            drawn = fallback["resolves_to"] if fallback else None
            style = fallback["style"] if fallback else None

        entry: dict[str, Any] = {
            "declared": declaration,
            "families": families,
            "weight": weight,
            "available": available,
            "drawn": drawn,
            "drawn_style": style,
        }
        if available is False:
            entry["warning"] = (
                f"none of {named!r} is installed — this card will be drawn in "
                f"{drawn!r} without a warning from the renderer, and it renders "
                "pixel-identically either way, so nothing downstream can catch it"
            )
        report.append(entry)
    return report


def render_svg(
    source: Path | str,
    dest: Path | str,
    *,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Rasterise `source` to the PNG at `dest`, reporting its fonts.

    `width`/`height` are a *render* size, not a resize: they go before the
    input so librsvg draws at that scale (see this module's docstring), and
    they fit rather than distort, so a size at a different aspect from the
    document's own comes back smaller on one axis. Both must be given
    together — half a size is an aspect assumption, and this is the one
    place that assumption would be silent.

    Omitted, the document renders at its own declared size. That is the
    honest default at this step: which size a card *should* be is the
    project's canvas, which step 3 computes and passes in here.
    """
    src = Path(source).expanduser()
    out = Path(dest).expanduser()
    if not src.is_file():
        raise GraphicsError(f"no SVG to render: {src}")
    if (width is None) != (height is None):
        raise GraphicsError(
            "render_svg takes both width and height or neither — one alone "
            "would have to guess the other from the document's aspect, and "
            "the guess would not be visible in the output"
        )

    # Parsed for its fonts before anything is rendered, so a malformed
    # document is refused with a line number rather than with magick's
    # `RenderRSVGImage` message.
    text = src.read_text(encoding="utf-8", errors="replace")
    fonts = font_report(text)

    command = magick_command()
    command += ["-background", "none"]
    if width is not None and height is not None:
        if width <= 0 or height <= 0:
            raise GraphicsError(f"render size must be positive, got {width}x{height}")
        command += ["-size", f"{width}x{height}"]
    command += [str(src), str(out)]

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GraphicsError(f"could not run {command[0]}: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or "no output"
        raise GraphicsError(f"magick could not render {src}: {detail}")

    rendered = identify(out)
    return {
        "source": str(src),
        "output": str(out),
        "width": rendered[0],
        "height": rendered[1],
        "requested_size": None if width is None else f"{width}x{height}",
        "size_policy": RENDER_FIT,
        "fonts": fonts,
        "font_warnings": [f["warning"] for f in fonts if "warning" in f],
    }


def identify(image: Path | str) -> tuple[int, int]:
    """The pixel dimensions of a finished raster, read off the file.

    Read back rather than assumed for the reason `mlt.declared_frames`
    exists: `-size` fits, so the size that was asked for and the size that
    landed are different numbers whenever the aspects disagree, and the one
    worth reporting is the one on disk.
    """
    path = Path(image).expanduser()
    command = [*magick_command(), "identify", "-format", "%w %h", str(path)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GraphicsError(f"could not run {command[0]}: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or "no output"
        raise GraphicsError(f"magick could not read {path}: {detail}")
    try:
        width, height = (int(n) for n in done.stdout.split()[:2])
    except ValueError as exc:
        raise GraphicsError(f"magick reported no size for {path}: {done.stdout!r}") from exc
    return width, height


def _region_mean(
    png: Path, rect: tuple[float, float, float, float], *, alpha: bool = False
) -> float:
    """Mean luminance (0-255) of `png` cropped to `rect`, `(x0, y0, x1, y1)`.

    `alpha` measures the alpha channel instead — how much of the region a
    transparent overlay card covers, where its colour under zero alpha is
    whatever the encoder left there and means nothing.
    """
    x0, y0, x1, y1 = rect
    w, h = max(1, round(x1 - x0)), max(1, round(y1 - y0))
    if w <= 0 or h <= 0:
        return 0.0
    command = [
        *magick_command(),
        str(png),
        *(["-alpha", "extract"] if alpha else []),
        "-crop",
        f"{w}x{h}+{round(x0)}+{round(y0)}",
        "+repage",
        "-format",
        "%[fx:mean]",
        "info:",
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GraphicsError(f"could not run {command[0]} to measure a region: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or "no output"
        raise GraphicsError(f"magick could not measure a region of {png}: {detail}")
    try:
        return float(done.stdout.strip().split()[0]) * 255.0
    except (IndexError, ValueError):
        raise GraphicsError(
            f"magick measured a region as {done.stdout.strip()!r}, which is not a mean"
        ) from None


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = rect
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _rect_intersect(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _hex_luminance(colour: str) -> float:
    """Mean of a `#rrggbb`(`aa`) colour's own channels, 0-255.

    Computed directly rather than by rendering a swatch through `magick`: a
    solid colour's mean is its own arithmetic mean, and skipping the
    subprocess is one fewer thing that can disagree with the reading it is
    the baseline for.
    """
    text = colour.lstrip("#")
    r, g, b = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    return (r + g + b) / 3.0


def safe_zone_ink(
    png: Path | str, canvas: tuple[int, int], band: dict[str, Any], background: str | None
) -> dict[str, Any]:
    """Mean luminance inside a platform's reserved band, and beside it.

    **Three numbers, never one.** A brightness bbox has already misread the
    same render twice in this repo — once as worse than a pillarbox, once as
    a pillarbox — because a black *source* reads exactly like a black *bar*
    (CLAUDE.md). So nothing here is trusted as an absolute reading: every
    number is reported **relative to the card's own recorded background
    swatch**, `background`, which is `TEMPLATE_BACKGROUND`'s palette slot as
    the card was actually authored — never assumed to be paper or ink.

    `band` is one of `SAFE_ZONES`'s entries, or a project's own from an
    applied pack — declared at a 1080x1920 reference canvas, scaled here by
    `canvas`'s own height / 1920. The reserved region is the bottom
    `bottom_px` band, unioned with the `action_rail` rectangle where the zone
    declares one; the union's mean corrects for the corner the two overlap
    in rather than double-counting it. `ink_outside_band` samples an
    **equal-area** rectangle from the part of the frame nothing reserves —
    a same-size comparison is the whole point, not a fixed crop that happens
    to be nearby.

    **`background=None` is an overlay card**, which has no background to be
    relative to: the numbers are then its alpha coverage (0-255) in and
    beside the band, since the frame under it is whatever the film shows.

    **Report only.** No default floor and nothing here blocks a render, the
    same restraint `card_safe_zones` keeps — `SCENE_THRESHOLD`'s own history
    is that a threshold gets pinned by looking at real output, not picked
    cold, and this has had exactly one look so far.
    """
    path = Path(png)
    coverage = background is None
    width, height = canvas
    scale = height / 1920.0
    bottom_px = float(band["bottom_px"]) * scale
    band_top = max(0.0, height - bottom_px)
    band_rect = (0.0, band_top, float(width), float(height))

    rail = band.get("action_rail")
    if rail:
        rail_width = float(rail["width"]) * scale
        rail_top = float(rail.get("rail_below_ratio", 0.5)) * height
        rail_rect = (max(0.0, width - rail_width), rail_top, float(width), float(height))
        overlap = _rect_intersect(band_rect, rail_rect)
        band_area, rail_area, overlap_area = (
            _rect_area(band_rect),
            _rect_area(rail_rect),
            _rect_area(overlap),
        )
        union_area = band_area + rail_area - overlap_area
        band_mean = _region_mean(path, band_rect, alpha=coverage)
        rail_mean = _region_mean(path, rail_rect, alpha=coverage)
        overlap_mean = _region_mean(path, overlap, alpha=coverage) if overlap_area > 0 else 0.0
        weighted = band_mean * band_area + rail_mean * rail_area - overlap_mean * overlap_area
        in_band = weighted / union_area if union_area > 0 else 0.0
    else:
        union_area = _rect_area(band_rect)
        in_band = _region_mean(path, band_rect, alpha=coverage)

    outside_height = min(band_top, (union_area / width) if width else 0.0)
    outside = _region_mean(path, (0.0, 0.0, float(width), outside_height), alpha=coverage)

    if coverage:
        return {
            "canvas": f"{width}x{height}",
            "reserved_area_px": round(union_area),
            "measured": "alpha coverage",
            "ink_in_band": round(in_band, 3),
            "ink_outside_band": round(outside, 3),
            "background_ink": None,
            "ink_vs_background": None,
        }
    background_ink = _hex_luminance(background)
    return {
        "canvas": f"{width}x{height}",
        "reserved_area_px": round(union_area),
        "measured": "luminance",
        "ink_in_band": round(in_band, 3),
        "ink_outside_band": round(outside, 3),
        "background_ink": round(background_ink, 3),
        "ink_vs_background": round(in_band - background_ink, 3),
    }


# -- templates -------------------------------------------------------------
#
# Step 2 of PLAN.md § Motion graphics and templates. Templates are SVG files
# with `{{slot}}` placeholders filled by string substitution — deliberately
# not a template engine, because the whole vocabulary is "put this text
# there" and a dependency that can branch and loop is a dependency that can
# put logic in a card.
#
# The starter set is the three the Scream assembly actually used, not a
# speculative library, and they reproduce those cards' own design: the
# palette below is sampled from `receipt-scream-1996.png` and its siblings
# rather than invented.

#: Sampled off the real cards, not chosen: paper `(250,245,236)`, ink
#: `(26,23,20)`, amber `(232,161,60)`, muted `(110,99,87)`, faint
#: `(156,152,145)`. Every one is an ordinary slot with this as its default,
#: so a project restyles a card by passing a different value.
PALETTE = {
    "paper": "#faf5ec",
    "ink": "#1a1714",
    "amber": "#e8a13c",
    "muted": "#6e6357",
    "faint": "#9c9891",
}

#: Fallback *stacks* ending in a generic, never a single face. A stack whose
#: first entry is installed is not a substitution, and `font_report` scores it
#: that way — which is the whole reason the report is per declaration. The
#: named faces are ones this box has (wiki `tooling.md` § Fonts); the generic
#: tail is what keeps a card legible on a machine that has neither.
FONTS = {
    "title_font": "'Noto Serif', 'Liberation Serif', serif",
    "body_font": "'Lato', 'Noto Sans', sans-serif",
    "quote_font": "'Noto Serif', 'Liberation Serif', serif",
    # Declared for a pack to name and validate against, but **no shipped
    # template's `mark` slot reads it yet** — that slot's own `font` key
    # still says `title_font`, on purpose: `test_the_wordmark_is_drawn_in_
    # title_type` (test_graphics.py, not this session's to edit) pins it
    # there, and the goodsometimes brand this pack ships for needs no
    # separate wordmark face anyway — Zilla Slab is both its title and its
    # mark, Bold vs SemiBold, which `weight_role` alone already expresses.
    # The role exists so a *future* template that wants a distinct wordmark
    # face has a name to ask for without a second FONTS entry; until one
    # does, a pack naming it is validated and stored and simply unread.
    "mark_font": "'Noto Serif', 'Liberation Serif', serif",
}

#: The two weights a wordmark/footnote pair can diverge on without a second
#: font role. A card's `<text>` element bakes its own `font-weight` in the
#: SVG (never a placeholder there before this), so these are what
#: `{{mark_weight}}`/`{{footnote_weight}}` in `templates/*.svg` now resolve
#: to — 700 for both, matching every shipped card's literal `font-weight="700"`
#: byte-for-byte, so a project with no pack applied renders unchanged.
WEIGHTS = {"mark_weight": 700, "footnote_weight": 700}

#: The card a template is authored against. Geometry inside a template is in
#: these units — 1920 wide, whatever the canvas aspect makes it tall — so a
#: template renders at any canvas size without rewriting its coordinates.
TEMPLATE_WIDTH = 1920

#: Star geometry, in template units. The ratio is the standard five-point
#: star's; the size is what matches the real cards' rows.
STAR_RADIUS = 42.0
STAR_INNER_RATIO = 0.382
STAR_GAP = 22.0

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _escape(value: str) -> str:
    """Escape user text for either an attribute or element content.

    `"` is escaped as well as `&<>` because a slot can land inside a
    double-quoted attribute — `font-family="{{title_font}}"` does — and a
    value that closed the attribute early would rewrite the document rather
    than fail. `'` is left alone: nothing here emits single-quoted
    attributes, and CSS font stacks read better with it.
    """
    return escape(value, {'"': "&quot;"})


def _star_points(cx: float, cy: float) -> str:
    """One five-point star as an SVG polygon point list."""
    inner = STAR_RADIUS * STAR_INNER_RATIO
    points = []
    for step in range(10):
        radius = STAR_RADIUS if step % 2 == 0 else inner
        angle = math.radians(-90 + step * 36)
        points.append(f"{cx + radius * math.cos(angle):.2f},{cy + radius * math.sin(angle):.2f}")
    return " ".join(points)


def stars_markup(rating: float, fill: str, *, prefix: str) -> tuple[str, float]:
    """A row of stars for `rating`, anchored at (0, 0), plus its width.

    Rounded to the nearest half, and drawn as filled stars only — no empty
    outlines behind them, which is what the real cards do. A half is the same
    star under a clip rectangle rather than a second hand-drawn path, so the
    two halves cannot drift apart; the clip needs an id, and `prefix` is what
    keeps two rows in one document from sharing one.

    The width comes back because the caller may need to lay something out
    after the row, and a row's width depends on the rating — `comparison`
    below places its arrow that way rather than at a guessed offset.
    """
    halves = max(0, round(float(rating) * 2))
    full, half = divmod(halves, 2)
    step = STAR_RADIUS * 2 + STAR_GAP

    parts = []
    for index in range(full):
        parts.append(
            f'<polygon points="{_star_points(STAR_RADIUS + index * step, 0)}" fill="{fill}"/>'
        )
    if half:
        cx = STAR_RADIUS + full * step
        clip = f"{prefix}-half"
        parts.append(
            f'<clipPath id="{clip}">'
            f'<rect x="{cx - STAR_RADIUS:.2f}" y="{-STAR_RADIUS:.2f}" '
            f'width="{STAR_RADIUS:.2f}" height="{STAR_RADIUS * 2:.2f}"/>'
            f"</clipPath>"
            f'<polygon points="{_star_points(cx, 0)}" fill="{fill}" clip-path="url(#{clip})"/>'
        )

    count = full + half
    width = 0.0 if count == 0 else count * step - STAR_GAP
    return "".join(parts), width


def comparison_markup(before: float, after: float, muted: str, amber: str) -> str:
    """`before` stars, an arrow, then `after` stars — the re-rate row.

    Laid out left to right off the measured width of the first row, because
    the two ratings are what decide where the arrow goes. A fixed offset
    would collide the moment someone re-rates from four stars rather than
    from two.
    """
    gap = 78.0
    arrow_length = 118.0

    left, left_width = stars_markup(before, muted, prefix="before")
    arrow_x = left_width + gap
    right, _ = stars_markup(after, amber, prefix="after")
    right_x = arrow_x + arrow_length + gap

    head = arrow_x + arrow_length
    arrow = (
        f'<g fill="none" stroke="{amber}" stroke-width="11" stroke-linecap="round" '
        f'stroke-linejoin="round">'
        f'<path d="M {arrow_x:.2f} 0 L {head:.2f} 0"/>'
        f'<path d="M {head - 34:.2f} -26 L {head:.2f} 0 L {head - 34:.2f} 26"/>'
        f"</g>"
    )
    return f"{left}{arrow}<g transform=\"translate({right_x:.2f}, 0)\">{right}</g>"


#: The three ink levels one quote carries, as `(weight, palette slot,
#: fill-opacity)`. **Read off the real cards rather than invented** —
#: `make_scream_cards.py`'s own `STYLES` is `key: (zb600, INK, None)`,
#: `dim: (zb600, INK, 0.42)`, `em: (zb700, AMBER, None)`, and each of the
#: three was sampled back off a raster to confirm librsvg reproduces it
#: (PLAN.md § The emphasis-capable quote slot, finding 1).
#:
#: Note what `em` does: it is a *weight* change as well as a colour, which is
#: the whole reason the font report had to learn to read `font-weight` before
#: this landed. And `dim` is ink at 0.42 rather than a flat grey, so it stays
#: right when the paper is not cream.
RUN_STYLES: dict[str, tuple[int, str, float | None]] = {
    "key": (600, "ink", None),
    "dim": (600, "ink", 0.42),
    "em": (700, "amber", None),
}

#: Unmarked text. `key` rather than `dim` because plain prose is the primary
#: reading and de-emphasis is the marked case, whichever happens to be more
#: frequent in one film's receipts.
RUN_DEFAULT = "key"

_RUN_MARKER = re.compile(r"\[(/?)(" + "|".join(RUN_STYLES) + r")\]")


def parse_runs(value: str) -> list[list[tuple[str, str]]]:
    """Split a marked-up slot value into lines of `(text, level)` runs.

    The vocabulary is `[em]…[/em]`, `[dim]…[/dim]`, `[key]…[/key]`, and
    unmarked text is `RUN_DEFAULT`. **Markers rather than JSON runs** because
    the value arrives from a CLI argument and an MCP string, where JSON is
    hostile to type and hostile to quote.

    `[[` is the escape and yields a literal `[`, which a marker syntax owes
    the moment it claims a character prose already uses. A `[` that does not
    begin a known marker is left alone — `[sic]` is not markup — so the
    escape is only needed to write a literal `[em]`.

    Runs nest, and the innermost wins: `[dim]a [em]b[/em] c[/dim]` is a dim
    run, an em run, and a dim run. They also span line breaks, so a marked
    paragraph does not have to be re-marked on every line.

    Refused rather than guessed: a close with no matching open, and a run
    still open at the end of the value. Both are cases where the drawn card
    would look deliberate and be wrong.
    """
    lines: list[list[tuple[str, str]]] = [[]]
    stack: list[str] = []
    buf: list[str] = []
    text = str(value)

    def flush() -> None:
        if buf:
            lines[-1].append(("".join(buf), stack[-1] if stack else RUN_DEFAULT))
            buf.clear()

    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n":
            flush()
            lines.append([])
            index += 1
            continue
        if char == "[":
            if text.startswith("[[", index):
                buf.append("[")
                index += 2
                continue
            marker = _RUN_MARKER.match(text, index)
            if marker:
                closing, level = marker.group(1), marker.group(2)
                flush()
                if closing:
                    if not stack or stack[-1] != level:
                        open_now = f"{stack[-1]!r} is open" if stack else "nothing is open"
                        raise GraphicsError(
                            f"[/{level}] at character {index} closes a run that is not "
                            f"open — {open_now}. Write [[ for a literal '['."
                        )
                    stack.pop()
                else:
                    stack.append(level)
                index = marker.end()
                continue
        buf.append(char)
        index += 1
    flush()

    if stack:
        raise GraphicsError(
            f"{stack[-1]!r} is still open at the end of the value — a run that "
            f"never closes would draw the rest of the card in it. Close it with "
            f"[/{stack[-1]}], or write [[ for a literal '['."
        )
    return lines


Runs = list[tuple[str, str]]


def _runs_markup(
    lines: list[Runs], *, x: float, line_height: float, colours: dict[str, str]
) -> str:
    """Lines of runs as one `<tspan>` per line, one per run inside it.

    **Line breaks are decided before this, never guessed here.** SVG has no
    automatic wrapping, and a wrap computed from a character count is a wrap
    that overflows the frame silently on the first line of wide glyphs — the
    shape of failure this repo keeps finding. A newline in the caller's value
    is a line break; the only other source of one is `flow_runs`, which
    measures.

    `xml:space="preserve"` is not decoration, and it is measured: splitting
    one line into per-run `<tspan>`s collapses the whitespace at every chunk
    boundary, so "the [em]perfect[/em] horror" renders as "theperfecthorror"
    — 25px narrower at 48px, at exit 0, looking like a deliberate ligature
    rather than a bug. It is set once per line because `xml:space` inherits.
    """
    markup = []
    for index, runs in enumerate(lines):
        inner = "".join(
            f"<tspan{_run_attrs(level, colours)}>{_escape(text)}</tspan>"
            for text, level in runs
        )
        dy = 0 if index == 0 else line_height
        markup.append(f'<tspan x="{x:g}" dy="{dy:g}" xml:space="preserve">{inner}</tspan>')
    return "".join(markup)


def measure_runs(runs: Runs, *, font: str, size: float, box: float = 0.0) -> float:
    """The ink width of one candidate line, rendered through the real coder.

    **Rendered, not summed.** The cheap build measures each word once and
    adds a space advance; measured against this, that drifts — side bearings
    accumulate — and it cannot see the mixed faces a styled line actually
    contains without tracking run styles itself. Greedy wrap tests one
    *prefix* per word either way, so rendering the candidate costs the same
    number of renders and is ground truth rather than a sum: ±0.8% against
    the ink of the line as drawn, where a character count is out by −34.6% to
    +83.4% (PLAN.md § The emphasis-capable quote slot, finding 2 and 3).

    Drawn in flat opaque black at every level. Opacity is a colour question
    and `-trim` is a colour test — measuring `dim` at 0.42 would hand back
    the width of whatever survived the fuzz, not the width of the line.

    Every run is one size, which is what separates this from `measure_line`:
    a `runs` slot is body text at the slot's size and a line is a `<text>`
    element whose pieces may not be. `box` sizes the scratch canvas and
    nothing else — `_measure` has why that matters.
    """
    if not any(text.strip() for text, _ in runs):
        return 0.0

    inner = "".join(
        f'<tspan font-weight="{RUN_STYLES[level][0]}" fill="#000">{_escape(text)}</tspan>'
        for text, level in runs
    )
    return _measure(inner, font=font, size=size, box=box)


def measure_line(parts: list[dict[str, Any]], *, font: str, box: float = 0.0) -> float:
    """The ink width of one *whole drawn line*, companions and gaps included.

    The unit is the `<text>` element, not the slot, and that is the finding
    this exists for. `receipt`'s title element draws `{{title}}` and then the
    year, at its own smaller size, after a 36-unit `dx` — so a title measured
    alone is measured against a box the year is already standing in. Same
    shape in `reveal`, where the title carries a raised asterisk and the note
    a leading amber one.

    A part is `{"text", "size", "weight", "gap"}` in template units, drawn in
    the order given. `gap` is emitted as `dx`, so the advance the design asks
    for is inside the measurement rather than subtracted from it afterwards —
    the same **rendered, not summed** rule `measure_runs` records, applied to
    a line whose pieces are different sizes.
    """
    if not any(str(part["text"]).strip() for part in parts):
        return 0.0

    pieces = []
    for part in parts:
        gap = float(part.get("gap", 0) or 0)
        dx = f' dx="{gap:g}"' if gap else ""
        pieces.append(
            f'<tspan{dx} font-size="{float(part["size"]):g}" '
            f'font-weight="{int(part["weight"])}" fill="#000">'
            f"{_escape(str(part['text']))}</tspan>"
        )
    size = max(float(part["size"]) for part in parts)
    return _measure("".join(pieces), font=font, size=size, box=box)


def _measure(inner: str, *, font: str, size: float, box: float) -> float:
    """Draw `inner` on a scratch canvas and hand back the width of its ink.

    `box` is the width the answer will be compared against, and it only sizes
    the scratch canvas: **the canvas is the cost.** The same line measures
    1622 units on a 20000x400 scratch and 1622 on a 3000x120 one, at 413ms
    and 38ms — a measurement is rasterisation, so an oversized canvas is
    paid on every candidate. What it must never do is *clip*, because a
    clipped line measures narrower: for a wrap it would end the flow early,
    and for a fit check it would pass the one value that does not fit. So the
    canvas grows and re-measures rather than trusting the headroom.
    """
    height = max(int(size * 3), 60)
    canvas = max(int(box * 3), _MEASURE_FLOOR)
    for _ in range(_MEASURE_GROWTHS):
        document = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{height}">'
            f'<text x="0" y="{height // 2}" font-family="{_escape(font)}" '
            f'font-size="{size:g}" xml:space="preserve">{inner}</text>'
            f"</svg>"
        )
        measured = _ink_width(document)
        if measured < canvas - 1:
            return measured
        canvas *= 4  # the ink reached the edge, so the answer is a clip, not a width
    raise GraphicsError(
        f"a candidate line is wider than {canvas} units and could not be measured "
        "without clipping — nothing a card slot holds is that wide, so this is a "
        "font or a value that is not what it looks like"
    )


def _ink_width(document: str) -> float:
    """`magick`'s own measurement of how wide the ink in `document` is."""
    command = magick_command() + [
        "-background",
        "none",
        "svg:-",
        "-trim",
        "-format",
        "%w",
        "info:",
    ]
    try:
        done = subprocess.run(
            command,
            input=document,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GraphicsError(f"could not run {command[0]} to measure a line: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or "no output"
        raise GraphicsError(f"magick could not measure a line: {detail}")
    try:
        return float(done.stdout.strip().split()[0])
    except (IndexError, ValueError):
        raise GraphicsError(
            f"magick measured a line as {done.stdout.strip()!r}, which is not a width"
        ) from None


#: The smallest scratch canvas a measurement is drawn on, and how many times
#: it may grow before the value is treated as nonsense rather than as long.
_MEASURE_FLOOR = 2000
_MEASURE_GROWTHS = 4


def flow_runs(lines: list[Runs], *, width: float, font: str, size: float) -> list[Runs]:
    """Greedy word-wrap over `lines`, breaking each to fit `width`.

    The caller's own line breaks are kept — a break is an instruction, and
    re-flowing across one would join two paragraphs. What this adds is a
    break where a line does not fit, chosen by measuring the candidate rather
    than by counting characters.

    A line that already fits costs exactly one render, which is the common
    case; only a line that overruns pays per word. Widths are cached across
    the whole flow, so the prefixes a greedy wrap re-measures are free.
    """
    cache: dict[tuple[tuple[str, str], ...], float] = {}

    def measure(runs: Runs) -> float:
        key = tuple(runs)
        if key not in cache:
            cache[key] = measure_runs(runs, font=font, size=size, box=width)
        return cache[key]

    flowed: list[Runs] = []
    for runs in lines:
        if not runs or measure(runs) <= width:
            flowed.append(runs)
            continue
        flowed.extend(_wrap(runs, width=width, measure=measure))
    return flowed


def _wrap(runs: Runs, *, width: float, measure: Any) -> list[Runs]:
    """One over-wide line, broken greedily at word boundaries.

    A word that does not fit on a line of its own is placed anyway rather
    than refused: it is a single unbreakable token, and the alternative is an
    empty line followed by the same problem. The overflow it causes is what
    the box check in `fill_template` reports.
    """
    words = [(word, level) for text, level in runs for word in _split_keeping_spaces(text)]
    out: list[Runs] = []
    line: Runs = []
    for word, level in words:
        if not word.strip() and not line:
            continue  # a break never starts a line with the space that caused it
        candidate = _append(line, (word, level))
        if line and measure(candidate) > width:
            out.append(_rstrip(line))
            line = _append([], (word.lstrip(), level)) if word.strip() else []
        else:
            line = candidate
    if line:
        out.append(_rstrip(line))
    return out or [[]]


def _split_keeping_spaces(text: str) -> list[str]:
    """`text` as words with the whitespace that followed each still attached.

    Kept rather than normalised because the spacing is the caller's: a
    double space after a full stop is a choice, and a wrap that silently
    regularised it would be editing the quote.
    """
    return re.findall(r"\S+\s*|\s+", text)


def _append(line: Runs, piece: tuple[str, str]) -> Runs:
    """`line` with `piece` on the end, merged when the level is unchanged."""
    text, level = piece
    if line and line[-1][1] == level:
        return [*line[:-1], (line[-1][0] + text, level)]
    return [*line, piece]


def _rstrip(line: Runs) -> Runs:
    """`line` without the trailing space the break replaced."""
    trimmed = [(text, level) for text, level in line]
    while trimmed and not trimmed[-1][0].rstrip():
        trimmed.pop()
    if trimmed:
        text, level = trimmed[-1]
        trimmed[-1] = (text.rstrip(), level)
    return trimmed


def _run_attrs(level: str, colours: dict[str, str]) -> str:
    """One run's ink, stated in full rather than inherited.

    Every run names its own weight and fill even when they match the
    element's, so a run's look does not depend on what the template happens
    to set around it — and so the font report can see the weight.
    """
    weight, slot, opacity = RUN_STYLES[level]
    attrs = f' font-weight="{weight}" fill="{_escape(str(colours[slot]))}"'
    if opacity is not None:
        attrs += f' fill-opacity="{opacity:g}"'
    return attrs


def has_runs(value: str) -> bool:
    """Whether a value carries inline markup at all.

    The gate that keeps the vocabulary additive on the single-line slots: a
    value with no marker in it takes the plain-substitution path it has
    always taken, so every card already on disk re-authors to the same bytes
    and the drift the escape hatch exists to catch stays visible.
    """
    return bool(_RUN_MARKER.search(value)) or "[[" in value


def line_markup(value: str, colours: dict[str, str]) -> str:
    """One single-line slot's value, with its inline runs drawn.

    The same `[em]`/`[dim]`/`[key]` vocabulary the flowing slots have, on the
    slots that are one line — which is what lets a wordmark carry the brand's
    amber asterisk without the template hard-coding a brand into proofcut.

    Two things differ from `_runs_markup`, and both are what make it additive
    rather than a restyle. **Unmarked text states nothing** and inherits the
    `<text>` element's own fill and weight, where a flowing run always names
    both; a line slot's look is set by the template around it, so declaring
    it here would silently re-ink every value that never asked for markup.
    And there is no positional `<tspan x= dy=>`: half these elements are
    `text-anchor="end"`, where an explicit `x` opens a second text chunk and
    moves the line. `xml:space="preserve"` is still owed, for the reason
    `_runs_markup` records — per-run tspans eat the whitespace between them.

    A newline is refused rather than collapsed. It is not a line break in a
    line slot and never has been, so a marked value carrying one is asking
    for something the slot cannot draw.
    """
    lines = parse_runs(value)
    if len(lines) > 1:
        raise GraphicsError(
            "a single-line slot cannot take a line break — it draws one line, "
            "and a newline in it is not a break but a space. Split the value "
            "across slots, or use a slot that flows."
        )
    inner = "".join(
        f"<tspan>{_escape(text)}</tspan>"
        if level == RUN_DEFAULT
        else f"<tspan{_run_attrs(level, colours)}>{_escape(text)}</tspan>"
        for text, level in lines[0]
    )
    return f'<tspan xml:space="preserve">{inner}</tspan>'


#: The card's side margin in template units, and the constant the box of
#: every single-line slot is derived from: a line runs from one margin to its
#: mirror, so a `start`-anchored slot's box is `1920 - 2x` and an `end`-
#: anchored one's is `2x - 1920`. Declaring the box instead of deriving it is
#: what lets the drift guard check the pair.
BODY_MARGIN = 140

#: What each template asks for. `placed` slots appear in the SVG as
#: `{{name}}`; the rest feed a `derived` entry, which is markup proofcut
#: generates and the template positions. Descriptions are the tool surface an
#: agent reads, so they say what the field *is*, not what type it has.
#:
#: **A placed text slot declares `kind: "line"` or it is drawn inside another
#: slot's line** — there is no third state, and a test holds every shipped
#: template to it. A slot nothing measures overruns its margin at `magick`
#: exit 0, which is the silent failure `flow=True` closes for the quote;
#: PLAN.md § The vertical card layout, finding 3.
#: How many linear stops stand in for the scrim's u**1.6 ramp; the `scrim`
#: template's SVGs place one `ramp_k` per interior stop.
SCRIM_RAMP_STOPS = 8

TEMPLATES: dict[str, dict[str, Any]] = {
    "receipt": {
        "description": "A film, its rating out of five, when it was watched, and the note written then.",
        "slots": {
            "title": {
                "kind": "line",
                "x": 140,
                "width": 1640,
                "size": 122,
                "weight": 700,
                "font": "title_font",
                # The year is drawn in this same element, smaller and after a
                # 36-unit gap, so it is part of what the title's box holds.
                "parts": [
                    {"text": "{title}"},
                    {"text": "({year})", "gap": 36, "size": 66, "weight": 400},
                ],
                "description": "the film's title",
            },
            "year": {"description": "its release year, drawn in brackets after the title"},
            "rating": {
                "kind": "rating",
                "placed": False,
                "description": "stars out of five, to the nearest half (e.g. 4.5)",
            },
            "date_line": {
                "kind": "line",
                "x": 140,
                "width": 1640,
                "size": 42,
                "weight": 400,
                "font": "body_font",
                "default": "",
                "description": "the line under the stars, e.g. 'watched 20 May 2021'",
            },
            "quote": {
                "kind": "runs",
                "x": 140,
                "y": 572,
                "line_height": 58,
                # Body width and size are stated here *and* in the SVG, the
                # way `x` always has been. A test measures them against the
                # file rather than trusting the pair to stay in step.
                "width": 1640,
                "size": 46,
                "font": "quote_font",
                # What is drawn below this slot, and so what its box has to
                # stay clear of — only when that slot has a value.
                "footer_slot": "mark",
                "default": "",
                "description": (
                    "the note itself. A newline is a line break and nothing else wraps. "
                    "Mark a fragment with [em]…[/em] for the amber emphasis or "
                    "[dim]…[/dim] for the dimmed ink; unmarked text is full ink. "
                    "Write [[ for a literal '['."
                ),
            },
            "mark": {
                "kind": "line",
                "x": 1780,
                "anchor": "end",
                "width": 1640,
                "size": 52,
                "weight_role": "mark_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "a wordmark for the bottom corner, if any. Takes the same "
                    "[em]…[/em] emphasis the note does, which is how a mark whose "
                    "asterisk is a different colour from its letters gets drawn"
                ),
            },
        },
        "derived": {"stars": ("stars", "rating", "amber")},
        # The stack is what changes, not the coordinate system: the grid stays
        # 1920 wide and the file scales `stars` in its own transform, because
        # `stars_markup` draws at a fixed radius and a vector scales cleanly.
        # Sizes are a starting point for the watch, not a derivation — finding
        # 2 measured the whole 46u–96u band as fitting.
        "variants": {
            "portrait": {
                "geometry": {"foot_margin": 740},
                "slots": {
                    "title": {
                        "size": 176,
                        "parts": [
                            {"text": "{title}"},
                            {"text": "({year})", "gap": 52, "size": 95, "weight": 400},
                        ],
                    },
                    "date_line": {"size": 54},
                    "quote": {"y": 1140, "line_height": 100, "size": 80},
                    "mark": {"size": 80, "x": 140, "anchor": "start"},
                },
            }
        },
    },
    "reveal": {
        "description": "A title card on ink, with a footnote — the shape used for each sequel's reveal.",
        "slots": {
            "title": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 196,
                "weight": 700,
                "font": "title_font",
                # The raised asterisk is drawn in the title's own element and
                # takes width like any other glyph; `dy` is not modelled
                # because it moves the ink up, not along.
                "parts": [{"text": "{title}"}, {"text": "*", "size": 104}],
                "description": "the title, set large and centred",
            },
            "note": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 52,
                "weight": 700,
                "font": "title_font",
                "parts": [{"text": "* "}, {"text": "{note}"}],
                "default": "",
                "description": "the footnote under it, after an amber asterisk",
            },
            "year": {
                "kind": "line",
                "x": 140,
                "width": 1640,
                "size": 44,
                "weight": 400,
                "font": "body_font",
                "parts": [{"text": "({year})"}],
                "default": "",
                "description": "the year, drawn small under the title",
            },
            "mark": {
                "kind": "line",
                "x": 1780,
                "anchor": "end",
                "width": 1640,
                "size": 52,
                "weight_role": "mark_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "a wordmark for the bottom corner, if any. Takes the same "
                    "[em]…[/em] emphasis the note does, which is how a mark whose "
                    "asterisk is a different colour from its letters gets drawn"
                ),
            },
        },
        "derived": {},
        # `mid_ratio` is the whole composition here: 0.44 of a 3413-unit frame
        # puts a title that is 3.6% of it at the halfway line with nothing
        # under it, which is finding 1's empty paper. 0.34 reads as a top-third
        # title, which is the zone `goodsometimes/branding.md` asks for.
        #
        # The year is drawn *in* that block rather than in the footer, and
        # that is the one thing separating this from `receipt`. Left where
        # the landscape file has it, it was the only ink in the 724px between
        # the note and the bottom margin — a cluster and an orphan, where the
        # receipt is a cluster and a margin. Watched side by side, the reveal
        # is the one that read wrong. HISTORY.md § The orphaned year.
        "variants": {
            "portrait": {
                "geometry": {"mid_ratio": 0.34, "note_gap": 160, "foot_margin": 740},
                "slots": {
                    "title": {
                        "size": 240,
                        "parts": [{"text": "{title}"}, {"text": "*", "size": 127}],
                    },
                    "note": {"size": 80},
                    "year": {"size": 68, "x": 960, "anchor": "middle"},
                    "mark": {"size": 80, "x": 140, "anchor": "start"},
                },
            }
        },
    },
    "rerate": {
        "description": "A rating that changed: the old stars, an arrow, the new ones.",
        "slots": {
            "title": {
                "kind": "line",
                "x": 140,
                "width": 1640,
                "size": 122,
                "weight": 700,
                "font": "title_font",
                "parts": [
                    {"text": "{title}"},
                    {"text": "({year})", "gap": 36, "size": 66, "weight": 400},
                ],
                "description": "the film's title",
            },
            "year": {"description": "its release year, drawn in brackets after the title"},
            "before": {
                "kind": "rating",
                "placed": False,
                "description": "the old rating out of five, to the nearest half",
            },
            "after": {
                "kind": "rating",
                "placed": False,
                "description": "the new rating out of five, to the nearest half",
            },
            "date_line": {
                "kind": "line",
                "x": 140,
                "width": 1640,
                "size": 42,
                "weight": 400,
                "font": "body_font",
                "default": "",
                "description": "the line under the row, e.g. 're-rated 28 Feb 2026'",
            },
            "mark": {
                "kind": "line",
                "x": 1780,
                "anchor": "end",
                "width": 1640,
                "size": 52,
                "weight_role": "mark_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "a wordmark for the bottom corner, if any. Takes the same "
                    "[em]…[/em] emphasis the note does, which is how a mark whose "
                    "asterisk is a different colour from its letters gets drawn"
                ),
            },
        },
        "derived": {"comparison": ("comparison", "before", "after")},
        # Two numbers here are bound rather than chosen. The comparison row is
        # 1290 units at five stars against five, so it scales to 1.27 and no
        # further — it is the one element a tall frame cannot enlarge with the
        # type. And `date_line` stops at 56 because the widest one on the
        # twelve is `rerate-scream4`'s 1176 units at 42, which reaches the
        # 1640 box at 58.6. HISTORY.md § The portrait cards.
        "variants": {
            "portrait": {
                "geometry": {"foot_margin": 740},
                "slots": {
                    "title": {
                        "size": 176,
                        "parts": [
                            {"text": "{title}"},
                            {"text": "({year})", "gap": 52, "size": 95, "weight": 400},
                        ],
                    },
                    "date_line": {"size": 46},
                    "mark": {"size": 80, "x": 140, "anchor": "start"},
                },
            }
        },
    },
    "endcard": {
        "description": (
            "A closing card: a brand mark alone on ink, with its own footnote "
            "underneath. No rule, no CTA — HISTORY.md § The end card settled "
            "the mark holding the frame by itself as the film's own sign-off."
        ),
        "slots": {
            "mark": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 210,
                "weight_role": "mark_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "the wordmark, centred and large — this card's whole "
                    "content. Ships empty; a project supplies its own mark, "
                    "e.g. 'Name[em]*[/em]' for an amber accent glyph."
                ),
            },
            "footnote": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 60,
                "weight_role": "footnote_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "a smaller line under the mark, e.g. the asterisk's own "
                    "footnote spelled out ('[em]*[/em] the rest of the name'). "
                    "Optional; leave blank to let the mark hold the frame alone."
                ),
            },
        },
        "derived": {},
        # The mark this ports (HISTORY.md § The end card) was authored for
        # the essay's own 1920x816 canvas and never asked to be legible at
        # phone size, so a portrait use is speculative — but `mark` is a
        # wordmark on every template that has one, and that rule is held at
        # both canvases (`test_the_wordmark_is_drawn_in_title_type`), so this
        # gets `reveal.portrait`'s own numbers rather than an untested gap.
        "variants": {
            "portrait": {
                "geometry": {"mid_ratio": 0.34, "note_gap": 160},
                "slots": {"mark": {"size": 260}, "footnote": {"size": 90}},
            }
        },
    },
    "bumper": {
        "description": (
            "A mark, a rule, and up to two lines under it — the register "
            "used both as the teaser's own bumper and, at 16:9, the essay's "
            "tail card. HISTORY.md § The bumper the teaser never had."
        ),
        "slots": {
            "mark": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 220,
                "weight_role": "mark_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "the wordmark, centred above the rule. Ships empty; a "
                    "project supplies its own mark, e.g. 'Name[em]*[/em]'."
                ),
            },
            "footnote": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 56,
                "weight_role": "footnote_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "a smaller line under the mark and above the rule, e.g. "
                    "the asterisk's own footnote spelled out. `make_bumper.py` "
                    "draws it in both of its registers; optional here too."
                ),
            },
            "line1": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 50,
                "weight": 400,
                "font": "body_font",
                "default": "",
                "description": (
                    "one line under the rule — a tagline for a no-CTA card, "
                    "or the first line of a call to action. Optional; a mark "
                    "with neither line drawn is the 'mark only' register."
                ),
            },
            "line2": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 44,
                "weight": 400,
                "font": "body_font",
                "default": "",
                "description": (
                    "a second line under the first, drawn lighter — the rest "
                    "of a call to action ('on the channel'). Optional and "
                    "independent of line1."
                ),
            },
        },
        "derived": {},
        # The rule is fixed markup, not a slot: `make_bumper.py` draws it in
        # both of its registers, so there is nothing for a project to turn
        # off — a card that wants no divider uses `endcard` instead, which
        # is the one HISTORY.md records as having had the rule removed.
        "variants": {
            "portrait": {
                # `mid_ratio` 0.40 of a 3413-unit portrait frame puts the mark
                # at 768 actual px on a 1080-wide canvas — the source script's
                # own y=760, "above centre: the bottom third is where the
                # platform UI lands." Sizes scale up the way `reveal`'s do,
                # legible at phone size rather than merely present.
                "geometry": {"mid_ratio": 0.40, "note_gap": 140},
                "slots": {
                    "mark": {"size": 300},
                    "footnote": {"size": 76},
                    "line1": {"size": 64},
                    "line2": {"size": 56},
                },
            }
        },
    },
    "chapter": {
        "description": (
            "A chapter card: the section's own title on ink, sized for a "
            "phrase rather than a wordmark, with a kicker above and a "
            "footnote below."
        ),
        "slots": {
            "kicker": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 44,
                "weight_role": "footnote_weight",
                "font": "body_font",
                "default": "",
                "description": (
                    "a small amber line above the title — a numeral, 'part "
                    "two', an act name. Optional; a chapter often needs only "
                    "its title."
                ),
            },
            "title": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 150,
                "weight": 700,
                "font": "title_font",
                "description": (
                    "the chapter's title, centred — a phrase, so it is set at "
                    "a text size the box can actually hold, not a wordmark "
                    "size. Takes [em]…[/em] for the amber accent, e.g. a "
                    "raised asterisk the footnote answers."
                ),
            },
            "footnote": {
                "kind": "line",
                "x": 960,
                "anchor": "middle",
                "width": 1640,
                "size": 56,
                "weight_role": "footnote_weight",
                "font": "title_font",
                "default": "",
                "description": (
                    "a smaller line under the rule — the title's own aside "
                    "spelled out ('[em]*[/em] it isn't'). Optional."
                ),
            },
        },
        "derived": {},
        # The register the Lambs/Longlegs section bumpers reached for `bumper`
        # to draw (2026-08-24), where a chapter named "her second monster" was
        # three characters too wide for a mark box: `bumper`'s 220 is a
        # *wordmark* size, and a chapter's name is a phrase. 150 over the same
        # 1640-unit box holds the phrases an essay actually turns on, and the
        # rule keeps the bumper family's one fixed gesture — under the title
        # here, because what this card ends with is its footnote, not a CTA.
        "variants": {
            "portrait": {
                "geometry": {"mid_ratio": 0.34, "note_gap": 160},
                # The title stops at 160, not `reveal.portrait`'s 240: the box
                # stays 1640 template units at every aspect, and the phrase
                # this card was built for measures 1602 units there — a film
                # title has slack a chapter phrase does not.
                "slots": {
                    "kicker": {"size": 64},
                    "title": {"size": 160},
                    "footnote": {"size": 80},
                },
            }
        },
    },
    # **Overlays** (`overlay: True`): drawn with no background rect, so the
    # PNG is transparent wherever the type is not, and placed over the film
    # by `overlay_add` rather than shown by a cue. The launch clip's type
    # (`clip.py` § overlay) is the register: a headline and an amber
    # footnote bottom left, over a gradient scrim that is its own overlay so
    # it can stay up across a headline handed to the next.
    # docs/plans/NATIVE.md § B3, designed.
    "lowerthird": {
        "description": (
            "A lower third over the film: a headline and an optional amber "
            "footnote, bottom left, on a transparent card. Place it with "
            "overlay_add, usually over a `scrim`."
        ),
        "overlay": True,
        "slots": {
            "headline": {
                "kind": "line",
                "x": 96,
                "width": 1728,
                "size": 76,
                "weight": 700,
                "font": "title_font",
                "description": "the headline, one line, set in the title face",
            },
            "footnote": {
                "kind": "line",
                "x": 100,
                "width": 1720,
                "size": 44,
                "weight_role": "footnote_weight",
                "font": "body_font",
                "default": "",
                "description": (
                    "a smaller amber line under the headline. Optional; placed "
                    "as an overlay it enters footnote_delay after the headline"
                ),
            },
            # Not drawn: they say how the overlay animates the card's two
            # lines (docs/plans/RECUT.md step 7), and a card with a footnote
            # writes each line as its own layer so it can.
            "footnote_delay": {
                "kind": "number",
                "placed": False,
                "default": 0.25,
                "description": (
                    "seconds the footnote enters after the headline when the card is an "
                    "overlay; the launch clip's 0.25, and 0 enters them together"
                ),
            },
            "footnote_rise": {
                "kind": "number",
                "placed": False,
                "default": 16,
                "description": (
                    "pixels, at 1080 lines, the footnote rises through on a rise "
                    "entrance; the launch clip's 16, where the headline rises 24"
                ),
            },
        },
        "derived": {},
        # The portrait stack sits just above the reserved bottom fifth
        # (`foot_margin` 740, the receipt's), at the sizes a phone reads.
        "variants": {
            "portrait": {
                "geometry": {"foot_margin": 740},
                "slots": {"headline": {"size": 110}, "footnote": {"size": 64}},
            }
        },
    },
    "scrim": {
        "description": (
            "A transparent-to-ink gradient over the bottom of the frame, for "
            "type to sit on. Place it with overlay_add under a lowerthird."
        ),
        "overlay": True,
        "slots": {
            "density": {
                "kind": "fraction",
                "placed": False,
                "default": 0.98,
                "description": (
                    "how dark the bottom edge gets, 0 to 1 (the gradient's "
                    "midpoint is set in proportion)"
                ),
            },
        },
        # The launch clip's scrim: opacity climbs as u**1.6 over the ramp and
        # holds at `density` from 0.74 of the height to the bottom edge, which
        # is where a headline sits (RECUT.md § What B got wrong, measured
        # against clip.py's own). The old linear gradient was 54% ink at 0.74H
        # and 70% at the headline, so type printed over live text. The ramp is
        # eight linear stops standing in for the curve.
        "derived": {
            "edge_opacity": ("fraction", "density", 1.0),
            "mid_opacity": ("fraction", "density", 1.0),
            **{
                f"ramp_{k}": ("fraction", "density", (k / SCRIM_RAMP_STOPS) ** 1.6)
                for k in range(1, SCRIM_RAMP_STOPS)
            },
        },
        "variants": {"portrait": {}},
    },
}


def is_overlay(name: str) -> bool:
    """Whether `name` draws a transparent card meant for `overlay_add`."""
    return bool(TEMPLATES.get(name, {}).get("overlay"))

#: Slots every template gets: the palette, the font stacks, and the geometry
#: proofcut computes from the canvas. Style slots are overridable; the geometry
#: ones are not, because they are the canvas the caller already chose.
STYLE_SLOTS = {**PALETTE, **FONTS, **WEIGHTS}
RESERVED_SLOTS = frozenset({"width", "height", "view_height", "mid_y", "note_y", "foot_y"})

#: The variants a template may be drawn in, and the canvas that selects each.
#: A variant is a *file* — `receipt` at a tall canvas draws
#: `receipt.portrait.svg` — chosen from the shape of the frame and never named
#: by the caller. That is the whole reason it is not a second template name:
#: `card_new` records `(template, slots, canvas)` and `card_reauthor` fills the
#: same template again at the project's canvas, so a portrait *template* would
#: make an aspect swap rewrite the record and the record would stop saying what
#: the card is. Named this way, every record already on disk keeps meaning what
#: it meant. PLAN.md § The vertical card layout.
VARIANTS: dict[str, Callable[[int, int], bool]] = {
    "portrait": lambda width, height: height > width,
}

#: The geometry proofcut derives from the canvas rather than reading out of the
#: file, in template units. These are the landscape file's numbers, and they
#: are per-variant because a portrait layout stacks differently — `foot_margin`
#: especially, since `view_height - 110` puts the wordmark 62px from the bottom
#: of a 1080x1920 frame, inside the band `goodsometimes/branding.md` reserves
#: for the platform's own UI.
#:
#: That band is the bottom fifth — 384px of 1920, which clears every published
#: bottom overlay (TikTok organic ~324, TikTok in-feed ads ~370, Reels ~320,
#: Shorts ~300); the portrait `foot_margin` of 740 puts the footer baseline at
#: 78.4%, just above it. **The right edge is the one the vertical layouts also
#: had to move for**: the action rail is 180–300px wide below the halfway
#: line, and a 140-unit margin is 79px, so a wordmark at `x=1780` sits under
#: the like button. Portrait draws it bottom *left*. PLAN.md § The vertical
#: card layout.
BASE_GEOMETRY: dict[str, float] = {"mid_ratio": 0.44, "note_gap": 130, "foot_margin": 110}

#: Which palette slot a template's own background rect draws — not a slot a
#: caller sets, because the SVG's `<rect fill="...">` is fixed markup, one
#: colour per template. `card_safe_zones` needs to know it to measure ink
#: *relative to the background the card actually has*, since a brightness
#: number alone has already misread a render twice in this repo (once as
#: worse than a pillarbox, once as a pillarbox) — a dark source and a dark
#: bar read identically to a bare luminance check.
TEMPLATE_BACKGROUND: dict[str, str] = {
    "receipt": "paper",
    "reveal": "ink",
    "rerate": "paper",
    "endcard": "ink",
    "bumper": "ink",
    "chapter": "ink",
    # Overlay templates (`is_overlay`) have no entry: they draw no background,
    # and `safe_zone_ink` measures their alpha coverage instead.
}

#: Reserved-band platform safe zones, at 1080x1920 (the vertical canvas every
#: number below was measured against — a caller at a different canvas scales
#: these by its own height / 1920). Numbers lifted verbatim from the note
#: `mlt.py`'s reframe geometry already carries in `BASE_GEOMETRY`'s own
#: comment history, not invented here: the platform UI eats the bottom band
#: on every short-form surface, and an action rail (share/comment/like) sits
#: on the right below the halfway line on every one of them too.
#: `card_safe_zones` reports ink in and out of this band; it never blocks a
#: render on it, the same way `reframe_detect` never writes a framing
#: decision without being asked — a threshold is pinned by looking at real
#: output, not picked cold (HISTORY.md § The scene threshold, re-pinned).
SAFE_ZONES: dict[str, dict[str, Any]] = {
    "tiktok-organic": {
        "bottom_px": 324,
        "action_rail": {"width": 240, "side": "right", "rail_below_ratio": 0.5},
    },
    "tiktok-ads": {
        "bottom_px": 370,
        "action_rail": {"width": 240, "side": "right", "rail_below_ratio": 0.5},
    },
    "reels": {
        "bottom_px": 320,
        "action_rail": {"width": 200, "side": "right", "rail_below_ratio": 0.5},
    },
    "shorts": {
        "bottom_px": 300,
        "action_rail": {"width": 180, "side": "right", "rail_below_ratio": 0.5},
    },
    # The bottom fifth of a 1920-tall frame — no particular platform, the
    # floor to design against when the target is unannounced.
    "worst-case": {
        "bottom_px": 384,
        "action_rail": {"width": 300, "side": "right", "rail_below_ratio": 0.5},
    },
}


def template_path(name: str, variant: str | None = None) -> Path:
    if name not in TEMPLATES:
        known = ", ".join(sorted(TEMPLATES)) or "none"
        raise GraphicsError(f"no template named {name!r} (there are: {known})")
    path = _TEMPLATE_DIR / (f"{name}.{variant}.svg" if variant else f"{name}.svg")
    if path.is_file():
        return path
    if variant:
        raise GraphicsError(
            f"template {name!r} declares a {variant!r} variant and there is no "
            f"file at {path} — a canvas that asks for it cannot fall back to "
            "the base card, because that is the pillarboxed card the variant exists to replace"
        )
    raise GraphicsError(f"no template named {name!r} (there are: {', '.join(sorted(TEMPLATES))})")


def _declared_slots(name: str, variant: str | None) -> dict[str, dict[str, Any]]:
    """`name`'s slot table as the variant draws it — base, with its overrides.

    One merge, shared by the drift guard and the fill, because a variant that
    declared its geometry to one and not the other would wrap a quote to a box
    the file does not have.
    """
    spec = TEMPLATES[name]
    over = spec.get("variants", {}).get(variant, {}).get("slots", {}) if variant else {}
    return {slot: {**meta, **over.get(slot, {})} for slot, meta in spec["slots"].items()}


def template_layout(name: str, width: int, height: int) -> dict[str, Any]:
    """Which file `name` draws at this canvas, and the geometry that goes with it.

    **A variant exists because the manifest declares it, not because a file is
    sitting in the templates directory.** Both halves of that are guards. A
    declared variant with no file refuses rather than falling back, because a
    silent fall back to the landscape card is exactly the pillarboxed card the
    item exists to remove — and it would render at exit 0. An *undeclared*
    file refuses too, which is the same drift guard `template_slots` runs
    between a manifest and its placeholders: a variant authored but never
    declared would never be drawn, and nothing would say so.

    A template that declares no variant resolves to its own file and
    `BASE_GEOMETRY` at every canvas, which is what every template does today.
    """
    if name not in TEMPLATES:
        known = ", ".join(sorted(TEMPLATES)) or "none"
        raise GraphicsError(f"no template named {name!r} (there are: {known})")
    declared = TEMPLATES[name].get("variants", {})
    stray = [
        v for v in VARIANTS if v not in declared and (_TEMPLATE_DIR / f"{name}.{v}.svg").is_file()
    ]
    if stray:
        raise GraphicsError(
            f"template {name!r} has {sorted(stray)} variant file(s) its manifest "
            "does not declare — nothing would ever draw them, and no canvas "
            "would say so"
        )

    variant = next(
        (v for v, selects in VARIANTS.items() if v in declared and selects(width, height)),
        None,
    )
    geometry = declared.get(variant, {}).get("geometry", {}) if variant else {}
    return {
        "variant": variant,
        "path": template_path(name, variant),
        "slots": _declared_slots(name, variant),
        "geometry": {**BASE_GEOMETRY, **geometry},
    }


def template_slots(name: str, variant: str | None = None) -> dict[str, dict[str, Any]]:
    """Every slot `name` accepts, its default, and what it is for.

    Read against the template on disk rather than from the manifest alone:
    the placeholders in the file are the truth about what gets filled, and a
    manifest that has drifted from them is how a card ends up shipping with
    `{{year}}` printed on its face. A variant file is held to the same
    agreement, because it is the same manifest entry drawn a second way.
    """
    path = template_path(name, variant)
    spec = TEMPLATES[name]
    declared = _declared_slots(name, variant)
    drawn = f"{name}.{variant}" if variant else name
    found = set(_PLACEHOLDER.findall(path.read_text(encoding="utf-8")))

    placed = {n for n, s in declared.items() if s.get("placed", True)}
    expected = placed | set(spec["derived"]) | set(STYLE_SLOTS) | RESERVED_SLOTS
    if found - expected:
        raise GraphicsError(
            f"template {drawn!r} has placeholders nothing fills: "
            f"{sorted(found - expected)} — the manifest and the SVG disagree"
        )
    unplaced = (placed | set(spec["derived"])) - found
    if unplaced:
        raise GraphicsError(
            f"template {drawn!r} declares slots its SVG never places: "
            f"{sorted(unplaced)} — the manifest and the SVG disagree"
        )

    slots = {}
    for slot, meta in declared.items():
        slots[slot] = {
            "description": meta["description"],
            "kind": meta.get("kind", "text"),
            "required": "default" not in meta,
            "default": meta.get("default"),
        }
    for slot, value in STYLE_SLOTS.items():
        slots[slot] = {
            "description": "style; overridable",
            "kind": "text",
            "required": False,
            "default": value,
        }
    return slots


def templates() -> list[dict[str, Any]]:
    """Every template proofcut ships, with its slots.

    Content and style are reported separately even though `fill_template`
    takes them in one dict. Both front ends sort their JSON, so a single map
    puts `amber` and `body_font` above `title` — burying the three fields a
    caller has to supply under twelve it can ignore.
    """
    listing = []
    for name in sorted(TEMPLATES):
        slots = template_slots(name)
        listing.append(
            {
                "template": name,
                "description": TEMPLATES[name]["description"],
                "slots": {s: v for s, v in slots.items() if s not in STYLE_SLOTS},
                "style": {s: v for s, v in slots.items() if s in STYLE_SLOTS},
            }
        )
    return listing


def _fraction(slot: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise GraphicsError(f"{slot} must be a number from 0 to 1, not {value!r}") from None
    if not 0.0 <= number <= 1.0:
        raise GraphicsError(f"{slot} must be from 0 to 1, not {number}")
    return number


def _rating(slot: str, value: Any) -> float:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        raise GraphicsError(f"slot {slot!r} is a rating out of five, not {value!r}") from None
    if not 0 <= rating <= 5:
        raise GraphicsError(f"slot {slot!r} is a rating out of five, and {rating} is outside it")
    return rating


#: How close a flowed slot may come to a footer that is actually drawn, in
#: template units. A quote whose last baseline reaches `foot_y` does not
#: overlap the wordmark — it shares its line, which reads as one row of text
#: in two sizes. **It is only owed when the footer exists**: reserving it for
#: an empty slot costs a line of quote to avoid colliding with nothing.
FLOW_FOOTER_GAP = 40


def _flow_box(
    declared: dict[str, Any],
    view_height: int,
    footer: bool,
    geometry: dict[str, float] | None = None,
) -> int:
    """How many lines the slot's box holds at this canvas.

    Derived from the canvas rather than declared, because the box is the
    space between the slot's first baseline and the bottom of the card — and
    the bottom moves with the aspect. A 9:16 receipt has room for many more
    lines than a 16:9 one, and hard-coding either would refuse a quote that
    fits.

    `view_height - foot_margin` is the template's own bottom margin: it is
    where the footer's baseline sits, so it is the lowest baseline the design
    allows whether or not a footer is drawn on it. The margin comes from the
    resolved variant, because a portrait layout keeps clear of the platform's
    UI band and a landscape one has no such band to keep clear of.
    """
    margin = (geometry or BASE_GEOMETRY)["foot_margin"]
    bottom = view_height - margin - (FLOW_FOOTER_GAP if footer else 0)
    return max(1, int((bottom - declared["y"]) // declared["line_height"]) + 1)


def _check_fits(
    slot: str,
    lines: list[Runs],
    declared: dict[str, Any],
    view_height: int,
    footer: bool,
    geometry: dict[str, float] | None = None,
) -> None:
    """Refuse a flow that overruns its box, naming the overflow.

    **Refuse rather than grow the card.** The canvas is the project's, and a
    template that quietly got taller to fit its text would be a slot value
    deciding the frame — the same failure as a cue carrying a length. The
    original script had no check at all here: `wrap_runs` returns a final
    baseline and `receipt()` throws it away, so a long quote overran the
    footer at exit 0.
    """
    holds = _flow_box(declared, view_height, footer, geometry)
    if len(lines) <= holds:
        return
    raise GraphicsError(
        f"slot {slot!r} flows to {len(lines)} lines at {declared['width']} units "
        f"wide, and its box holds {holds} at this canvas — {len(lines) - holds} "
        "too many. Shorten the text, or render the card at a taller canvas; "
        "the card does not grow to fit its own slot."
    )


def line_parts(
    slot: str, declared: dict[str, Any], resolved: dict[str, Any]
) -> list[dict[str, Any]]:
    """The pieces of `slot`'s drawn line, filled in and sized.

    A slot that declares no `parts` is its own whole line, which is the
    common case; one that does names every piece of the `<text>` element it
    lives in, its own value included and in drawn order. Each piece inherits
    the slot's size and weight unless it states otherwise, so the declaration
    stays as short as the file's own markup is.
    """
    # A slot's own weight is a literal `weight` when it declares one — the
    # older contract, unchanged — or, for a slot that opted into the pack
    # axis instead (`mark`/`footnote`, since HISTORY.md § The channel preset
    # pack), `resolved[weight_role]`. The two never coexist on a real
    # declaration; a literal wins if both are present, which is what keeps a
    # caller free to override a slot's weight for one measurement (as a test
    # does) without knowing whether the slot behind it uses either axis.
    if "weight" in declared:
        slot_weight = declared["weight"]
    else:
        slot_weight = resolved[declared["weight_role"]]

    parts = declared.get("parts") or [{"text": "{" + slot + "}"}]
    filled = []
    for part in parts:
        text = str(part["text"]).format(**resolved)
        size = part.get("size", declared["size"])
        weight = part.get("weight", slot_weight)
        gap = part.get("gap", 0)
        if not has_runs(text):
            filled.append({"text": text, "size": size, "weight": weight, "gap": gap})
            continue
        # A marked piece measures as its runs, because `[em]` is a weight
        # change as well as a colour: measuring the markers away and not the
        # weight under-measures exactly the fragment the author emphasised,
        # which is the one most likely to be the reason the line got long.
        # Unmarked text keeps the piece's own weight rather than the runs'
        # default, for the reason `line_markup` states — it is inherited ink.
        runs = [run for line in parse_runs(text) for run in line]
        for index, (run_text, level) in enumerate(runs):
            filled.append(
                {
                    "text": run_text,
                    "size": size,
                    "weight": weight if level == RUN_DEFAULT else RUN_STYLES[level][0],
                    "gap": gap if index == 0 else 0,
                }
            )
    return filled


def _check_line_fits(
    slot: str,
    declared: dict[str, Any],
    resolved: dict[str, Any],
) -> None:
    """Refuse a single-line slot that draws wider than its box.

    The counterpart to `_check_fits`, and the same rule: refuse rather than
    shrink the type or grow the card, because either would be a slot value
    deciding the design. What it closes is narrower and was live at exit 0 —
    `title`, `note`, `date_line`, `year` and `mark` are plain substitutions
    with no wrap to fail, so an over-long one simply runs past the margin and
    off the card, and `magick` returns success. At the sizes a portrait
    variant wants, a `reveal` title reaches 1677 units in a 1640-unit box.
    PLAN.md § The vertical card layout, finding 3.

    A blank slot is not measured. There is nothing to refuse, and skipping it
    is most of the saving: a card fills five line slots and typically supplies
    two, so measuring the empty ones would triple the renders a fill costs to
    ask about text nobody wrote.
    """
    if not str(resolved[slot]).strip():
        return
    parts = line_parts(slot, declared, resolved)
    box = float(declared["width"])
    measured = measure_line(parts, font=str(resolved[declared["font"]]), box=box)
    if measured <= box:
        return
    beside = (
        " — measured as it is drawn, with what shares its line"
        if len(parts) > 1
        else ""
    )
    raise GraphicsError(
        f"slot {slot!r} draws {measured:.0f} units wide at size "
        f"{float(declared['size']):g}, and its box holds {box:g}{beside}. "
        f"That is {measured - box:.0f} too many: shorten the text, or draw the "
        "card at an aspect whose layout gives the line more room. The card "
        "does not grow to fit its own slot."
    )


def fill_template(
    name: str,
    values: dict[str, Any],
    *,
    width: int = 1920,
    height: int = 1080,
    flow: bool = True,
) -> str:
    """Fill `name`'s slots with `values`, returning the SVG to write.

    Every user value is escaped; the only unescaped markup is what proofcut
    generates itself for a `derived` slot. That split is the whole security
    story of a string-substitution template, and it is why a rating is parsed
    as a number here rather than pasted through as text.

    A missing required slot and an unknown slot are both refused. A card
    silently missing its year is the failure this exists to make loud — the
    render would still succeed and still be wrong.

    `width`/`height` are the canvas. Geometry inside a template is in
    1920-wide units and the viewBox is written to match the canvas aspect, so
    the same template renders at any size without pillarboxing.

    **The canvas also picks which of the template's files gets filled.** A
    template that declares a variant for this shape draws that file, with the
    geometry declared alongside it; one that declares none draws its own file
    at `BASE_GEOMETRY`, which is every template today. The grid stays 1920
    units wide across variants — a variant changes the vertical stack and the
    sizes, never the coordinate system. `template_layout`.

    `flow` measures a `runs` slot through `render_svg`'s own coder and wraps
    it to the body width the template declares, refusing when the result does
    not fit the slot's box. **It defaults on because the failure it closes is
    silent** — the script these cards come from threw away `wrap_runs`'s final
    baseline, so a long enough quote overran the footer and nothing said so.
    Off, the caller's line breaks are the only ones, which is the older
    contract and needs no renderer; it is for callers that are testing the
    substitution rather than authoring a card.
    """
    if width <= 0 or height <= 0:
        raise GraphicsError(f"canvas must be positive, got {width}x{height}")
    layout = template_layout(name, width, height)
    spec = TEMPLATES[name]
    declared_slots = layout["slots"]
    geometry = layout["geometry"]
    slots = template_slots(name, layout["variant"])

    unknown = set(values) - set(slots)
    if unknown:
        raise GraphicsError(
            f"template {name!r} has no slot {sorted(unknown)} (it takes: {sorted(slots)})"
        )
    missing = [s for s, meta in slots.items() if meta["required"] and s not in values]
    if missing:
        raise GraphicsError(f"template {name!r} needs {sorted(missing)}, which nothing supplied")

    resolved = {s: values.get(s, meta["default"]) for s, meta in slots.items()}
    # Refuse what no slot could draw before measuring any of them: a line
    # break in a single-line value is a malformed ask, and that answer should
    # neither wait on nor depend on having the renderer every fit is measured
    # through. `line_markup` is where the refusal lives; this only runs it early.
    colours = {name: str(resolved[name]) for name in PALETTE}
    for slot, meta in slots.items():
        if meta["kind"] == "line" and has_runs(str(resolved[slot])):
            line_markup(str(resolved[slot]), colours)

    view_height = round(TEMPLATE_WIDTH * height / width)
    mid_y = round(view_height * geometry["mid_ratio"])
    filled: dict[str, str] = {
        "width": str(width),
        "height": str(height),
        "view_height": str(view_height),
        "mid_y": str(mid_y),
        "note_y": str(mid_y + geometry["note_gap"]),
        "foot_y": str(view_height - geometry["foot_margin"]),
    }
    for slot, meta in slots.items():
        if meta["kind"] == "rating" or not declared_slots.get(slot, {}).get("placed", True):
            continue
        if meta["kind"] == "runs":
            declared = declared_slots[slot]
            lines = parse_runs(str(resolved[slot]))
            if flow:
                lines = flow_runs(
                    lines,
                    width=declared["width"],
                    font=str(resolved[declared["font"]]),
                    size=declared["size"],
                )
                footer = declared.get("footer_slot")
                _check_fits(
                    slot,
                    lines,
                    declared,
                    view_height,
                    bool(footer and str(resolved.get(footer, "")).strip()),
                    geometry,
                )
            filled[slot] = _runs_markup(
                lines,
                x=declared["x"],
                line_height=declared["line_height"],
                colours={name: str(resolved[name]) for name in PALETTE},
            )
        else:
            if flow and meta["kind"] == "line":
                _check_line_fits(slot, declared_slots[slot], resolved)
            value = str(resolved[slot])
            filled[slot] = (
                line_markup(value, {name: str(resolved[name]) for name in PALETTE})
                if meta["kind"] == "line" and has_runs(value)
                else _escape(value)
            )

    for slot, (builder, *sources) in spec["derived"].items():
        if builder == "stars":
            source, colour = sources
            markup, _ = stars_markup(
                _rating(source, resolved[source]), resolved[colour], prefix=slot
            )
            filled[slot] = markup
        elif builder == "comparison":
            before, after = sources
            filled[slot] = comparison_markup(
                _rating(before, resolved[before]),
                _rating(after, resolved[after]),
                resolved["muted"],
                resolved["amber"],
            )
        elif builder == "fraction":
            source, scale = sources
            filled[slot] = f"{_fraction(source, resolved[source]) * scale:.3f}"
        else:  # pragma: no cover - a builder name only this module writes
            raise GraphicsError(f"template {name!r} names an unknown builder {builder!r}")

    def substitute(match: re.Match[str]) -> str:
        return filled[match.group(1)]

    return _PLACEHOLDER.sub(substitute, layout["path"].read_text(encoding="utf-8"))
