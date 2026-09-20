"""The safe-zone guide's read model: `graphics.safe_zone_rects`,
`ops.safe_zone_view` and the `/api/safe-zones` route the preview draws from.

The window only draws rectangles, so what is worth pinning is where they come
from. `safe_zone_rects` is the one place a zone's geometry is worked out —
`safe_zone_ink` measures the same rectangles, so the guide on screen is the
region the report reads — and these tests hold both halves of that: the
arithmetic, and that the ink report's reserved area is the union of exactly
these rectangles.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from proofcut import graphics, ops, webui
from proofcut import timeline as tl
from proofcut.project import Project

needs_magick = pytest.mark.skipif(
    shutil.which("magick") is None, reason="ImageMagick is not installed"
)

CLIP = {
    "clip_id": "cold-open",
    "source": "/nonexistent/cold-open.mp4",
    "duration": 12.0,
    "has_video": True,
    "has_audio": True,
    "width": 1920,
    "height": 816,
}


def _make_project(root: Path, *, canvas: str | None) -> Project:
    project = Project.create(root)
    manifest = project.read_manifest()
    manifest["clips"] = [CLIP]
    if canvas is not None:
        manifest["canvas"] = canvas
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment(CLIP["clip_id"], 0.0, 12.0)])
    tl.write(tl.to_otio(edit, {CLIP["clip_id"]: CLIP}, rate=1000.0), project.timeline_path)
    return project


@pytest.fixture
def vertical(tmp_path: Path) -> Project:
    return _make_project(tmp_path / "vertical", canvas="1080x1920")


@pytest.fixture
def landscape(tmp_path: Path) -> Project:
    return _make_project(tmp_path / "landscape", canvas=None)


# -- graphics.safe_zone_rects --------------------------------------------------


def test_rects_are_the_reference_numbers_at_the_reference_canvas() -> None:
    band, rail = graphics.safe_zone_rects((1080, 1920), graphics.SAFE_ZONES["worst-case"])

    assert band == (0.0, 1536.0, 1080.0, 1920.0)  # the bottom 384px
    assert rail == (780.0, 960.0, 1080.0, 1920.0)  # 300 wide, below the halfway line


def test_rects_scale_by_the_canvas_height_over_1920() -> None:
    band, rail = graphics.safe_zone_rects((540, 960), graphics.SAFE_ZONES["worst-case"])

    assert band == (0.0, 768.0, 540.0, 960.0)
    assert rail == (390.0, 480.0, 540.0, 960.0)


def test_a_zone_with_no_rail_has_none() -> None:
    band, rail = graphics.safe_zone_rects((1080, 1920), {"bottom_px": 200})

    assert band == (0.0, 1720.0, 1080.0, 1920.0)
    assert rail is None


def test_a_rail_wider_than_the_canvas_is_clamped_to_its_left_edge() -> None:
    _, rail = graphics.safe_zone_rects(
        (100, 1920), {"bottom_px": 10, "action_rail": {"width": 500}}
    )

    assert rail is not None
    assert rail[0] == 0.0


@needs_magick
@pytest.mark.parametrize("zone", sorted(graphics.SAFE_ZONES))
def test_the_ink_reports_reserved_area_is_the_union_of_the_drawn_rectangles(
    tmp_path: Path, zone: str
) -> None:
    """The reason there is one function: what the guide draws and what the
    report measures are the same region, so their areas agree exactly."""
    png = tmp_path / "card.png"
    size = (1080, 1920)
    subprocess.run(
        ["magick", "-size", f"{size[0]}x{size[1]}", "xc:#1a1714", str(png)], check=True
    )
    band_rect, rail_rect = graphics.safe_zone_rects(size, graphics.SAFE_ZONES[zone])
    assert rail_rect is not None
    overlap = graphics._rect_intersect(band_rect, rail_rect)
    union = (
        graphics._rect_area(band_rect)
        + graphics._rect_area(rail_rect)
        - graphics._rect_area(overlap)
    )

    ink = graphics.safe_zone_ink(png, size, graphics.SAFE_ZONES[zone], "#1a1714")

    assert ink["reserved_area_px"] == round(union)


# -- ops.safe_zone_view --------------------------------------------------------


def test_the_view_lists_every_built_in_zone_as_rectangles_on_the_canvas(
    vertical: Project,
) -> None:
    view = ops.safe_zone_view(vertical.root)

    assert view["canvas"] == [1080, 1920]
    assert view["vertical"] is True
    assert sorted(view["zones"]) == sorted(graphics.SAFE_ZONES)
    worst = view["zones"]["worst-case"]
    assert worst["source"] == "built-in"
    assert worst["band"] == [0.0, 1536.0, 1080.0, 1920.0]
    assert worst["rail"] == [780.0, 960.0, 1080.0, 1920.0]


def test_a_landscape_canvas_is_not_vertical(landscape: Project) -> None:
    """The reference numbers are a short-form frame; on 1920x816 the window
    must not be offered a guide no platform draws."""
    view = ops.safe_zone_view(landscape.root)

    assert view["canvas"] == [1920, 816]
    assert view["vertical"] is False


def test_a_pack_zone_is_listed_and_replaces_a_built_in_of_the_same_name(
    vertical: Project,
) -> None:
    manifest = vertical.read_manifest()
    manifest["pack"] = {
        "name": "x",
        "source": "x.json",
        "active_variant": "default",
        "variants": {
            "default": {
                "hash": "h",
                "safe_zones": {
                    "my-platform": {"bottom_px": 200},
                    "worst-case": {"bottom_px": 100},
                },
            }
        },
        "caption_preset_applied": None,
    }
    vertical.write_manifest(manifest)

    view = ops.safe_zone_view(vertical.root)

    assert view["zones"]["my-platform"]["source"] == "pack"
    assert view["zones"]["my-platform"]["rail"] is None
    assert view["zones"]["worst-case"]["source"] == "pack"
    assert view["zones"]["worst-case"]["band"] == [0.0, 1820.0, 1080.0, 1920.0]
    assert view["zones"]["reels"]["source"] == "built-in"


def test_the_view_writes_nothing(vertical: Project) -> None:
    before = vertical.manifest_path.read_bytes()

    ops.safe_zone_view(vertical.root)

    assert vertical.manifest_path.read_bytes() == before


# -- the route and the files that draw it --------------------------------------


@pytest.fixture
def server(vertical: Project) -> Iterator[str]:
    httpd = webui.make_server(vertical.root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.agent.close()
        thread.join(timeout=5)


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as response:
        return response.read()


def test_the_route_serves_the_same_read_model(server: str, vertical: Project) -> None:
    served: Any = json.loads(_fetch(f"{server}/api/safe-zones"))

    assert served == ops.safe_zone_view(vertical.root)


def test_the_module_and_its_controls_are_served(server: str) -> None:
    js = _fetch(f"{server}/static/safezones.js").decode("utf-8")
    html = _fetch(f"{server}/").decode("utf-8")

    assert "/api/safe-zones" in js
    for ident in ("safezone-toggle", "safezone-select", "safezone-layer"):
        assert f'id="{ident}"' in html
    assert "safezones.js" in _fetch(f"{server}/static/app.js").decode("utf-8")


def test_the_guide_never_computes_a_rectangle_itself(server: str) -> None:
    """The window draws and never decides: the reference numbers (1920, the
    bottom bands, the rail widths) must not appear in the module."""
    js = _fetch(f"{server}/static/safezones.js").decode("utf-8")
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)

    assert "1920" not in code
    assert "bottom_px" not in code


def test_hidden_needs_its_companion_rules(server: str) -> None:
    """An author `display:` outranks the UA's `[hidden]`, so the layer and both
    controls carry an explicit `display: none` — the fifth-instance trap
    CLAUDE.md names."""
    css = _fetch(f"{server}/static/app.css").decode("utf-8")

    for selector in ("#safezone-layer[hidden]", "#safezone-toggle[hidden]", "#safezone-wrap[hidden]"):
        assert re.search(re.escape(selector) + r"[^{]*\{[^}]*display:\s*none", css), selector
