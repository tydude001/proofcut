"""Animated graphics: a web page, captured as intro, hold and outro.

docs/plans/DAYDREAM.md § Animated graphics, designed and spiked. A graphic is a
folder under `assets/graphics/<name>/`: an `index.html` the agent wrote (or a
template filled), any local files it loads, and `graphic.json` declaring its
three phases in seconds of the page's own timeline:

    {"intro": 2.9, "loop": null, "outro": 0.5}

* **intro** plays once from the start of wherever the graphic is placed;
* the **hold** is the page at the end of the intro, stretched to whatever the
  span leaves — one frame when `loop` is null, else `loop` seconds of the
  page, repeated;
* **outro** plays once, ending where the span ends. With a still hold the
  outro's page time starts where the intro ended; with a loop, one loop later.

So the span decides the length and the graphic never does. That is PLAN.md
§ Animation is a length problem's rule kept: a word-addressed span moves with
every cut, and a baked length would be the music bed's failure again.

**A hold that moves is refused unless it is declared a loop.** For a still
hold the capture asks the page what is still animating through the hold's
instant (`browser.moving_at`) — a blinking caret is. For a loop it compares
the loop's first frame against the frame one period on, byte for byte. That
comparison at capture is the authoring-time refusal the PLAN.md note asked for:
`mlt.plan_picture`'s refusal at export would be far too late.

Frames are cached under `cache/graphics/<name>/`, one folder per phase, and
stamped (`capture.json`) with the page's bytes, the canvas and the rate, so a
change to any of them makes the capture stale and an export refuses it rather
than drawing the old one.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from proofcut import browser, deps, progress

SPEC_NAME = "graphic.json"
PAGE_NAME = "index.html"
CAPTURE_NAME = "capture.json"
PHASES = ("intro", "hold", "outro")
#: Bumped when a capture of the same page would draw differently — the flags,
#: the seek — so every cached capture goes stale with it.
CAPTURE_VERSION = 1
#: Pages captured side by side. The spike measured 8 pages at 1.4 s for 91
#: frames against 10 s for one; two leaves the machine usable meanwhile.
DEFAULT_PAGES = 2
MAX_PAGES = 8

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

#: The built-in templates, one folder each, shaped like a graphic.
TEMPLATES_DIR = Path(__file__).parent / "graphic_templates"


class GraphicError(RuntimeError):
    """A graphic that cannot be made, read or captured as asked."""


def check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise GraphicError(
            f"graphic name {name!r} must be lowercase letters, digits, '-' or '_', "
            "starting with a letter or digit, at most 48 characters"
        )
    return name


# -- the spec ------------------------------------------------------------------


def read_spec(folder: Path) -> dict[str, Any]:
    """`graphic.json`, validated: the three phases and what the graphic came from."""
    path = folder / SPEC_NAME
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise GraphicError(f"{folder} has no {SPEC_NAME}") from None
    except (OSError, ValueError) as exc:
        raise GraphicError(f"{path} is not JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise GraphicError(f"{path} must be a JSON object")
    return normalise_spec(raw, where=str(path))


def normalise_spec(raw: dict[str, Any], *, where: str = "a graphic") -> dict[str, Any]:
    """The phases as numbers, refused where they cannot describe a graphic."""
    try:
        intro = float(raw.get("intro", 0.0))
        loop = None if raw.get("loop") in (None, 0, 0.0) else float(raw["loop"])
        outro = float(raw.get("outro", 0.0))
    except (TypeError, ValueError):
        raise GraphicError(f"{where}: intro, loop and outro are seconds") from None
    if intro < 0 or outro < 0 or (loop is not None and loop <= 0):
        raise GraphicError(f"{where}: intro and outro are lengths and a loop is positive")
    spec: dict[str, Any] = {"intro": intro, "loop": loop, "outro": outro}
    if raw.get("template") is not None:
        spec["template"] = str(raw["template"])
    if isinstance(raw.get("slots"), dict):
        spec["slots"] = {str(k): str(v) for k, v in raw["slots"].items()}
    return spec


def write_spec(folder: Path, spec: dict[str, Any]) -> None:
    (folder / SPEC_NAME).write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")


def phase_frames(spec: dict[str, Any], fps: float) -> dict[str, Any]:
    """Each phase's frame count and page time at `fps`.

    The hold starts on the frame grid — the intro's own frame count over the
    rate — so the frame a hold shows is a frame the intro would have reached.
    """
    intro = round(spec["intro"] * fps)
    hold_at = intro / fps
    loop = None if spec["loop"] is None else max(1, round(spec["loop"] * fps))
    outro_at = hold_at + (0 if loop is None else loop / fps)
    outro = round(spec["outro"] * fps)
    return {
        "intro": intro,
        "hold": 1 if loop is None else loop,
        "loop": loop is not None,
        "outro": outro,
        "hold_at": hold_at,
        "outro_at": outro_at,
    }


# -- capture -------------------------------------------------------------------


def stamp(folder: Path, width: int, height: int, fps: float) -> str:
    """What a capture was made from: every file of the page, the canvas, the rate."""
    digest = hashlib.sha256()
    digest.update(json.dumps([CAPTURE_VERSION, width, height, round(fps, 6)]).encode())
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def read_capture(frames: Path) -> dict[str, Any] | None:
    try:
        return json.loads((frames / CAPTURE_NAME).read_text())
    except (OSError, ValueError):
        return None


def is_current(folder: Path, frames: Path, width: int, height: int, fps: float) -> bool:
    record = read_capture(frames)
    return record is not None and record.get("stamp") == stamp(folder, width, height, fps)


def capture(
    folder: Path,
    frames: Path,
    *,
    width: int,
    height: int,
    fps: float,
    pages: int = DEFAULT_PAGES,
) -> dict[str, Any]:
    """Capture `folder`'s page into `frames/{intro,hold,outro}/f%04d.png`.

    Written beside `frames` and renamed into place only once every frame and
    every check has passed, so a refused or interrupted capture leaves the
    previous one standing.
    """
    spec = read_spec(folder)
    if not (folder / PAGE_NAME).is_file():
        raise GraphicError(f"{folder} has no {PAGE_NAME}")
    layout = phase_frames(spec, fps)
    if layout["intro"] + layout["outro"] == 0 and not layout["loop"]:
        raise GraphicError(
            f"{folder.name} has no intro, no loop and no outro — that is a still; make it a card"
        )
    pages = max(1, min(MAX_PAGES, int(pages)))

    # Every frame to take: (phase, index, page seconds). The loop's check
    # frame rides at the end, compared and never written.
    jobs: list[tuple[str, int, float]] = [("intro", k, k / fps) for k in range(layout["intro"])]
    jobs += [("hold", k, layout["hold_at"] + k / fps) for k in range(layout["hold"])]
    jobs += [("outro", k, layout["outro_at"] + k / fps) for k in range(layout["outro"])]
    if layout["loop"]:
        jobs.append(("loop-check", 0, layout["outro_at"]))

    frames.parent.mkdir(parents=True, exist_ok=True)
    staging = frames.parent / f".{frames.name}.capturing-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    for phase in PHASES:
        (staging / phase).mkdir(parents=True)
    started = time.monotonic()
    shots: dict[tuple[str, int], bytes] = {}
    try:
        with browser.launch(folder) as chrome:
            tabs = [chrome.new_page(width, height) for _ in range(min(pages, len(jobs)))]
            fonts: list[dict[str, Any]] = []
            for tab in tabs:
                fonts = browser.load(tab)
            failed = [f"{f['family']} {f['weight']}" for f in fonts if f.get("status") == "error"]
            if failed:
                raise GraphicError(
                    f"{folder.name}'s page could not load the font(s) {', '.join(failed)} — a page "
                    f"loads only its own folder and the vendored fonts under {browser.FONTS_PATH}"
                )
            if not layout["loop"]:
                moving = browser.moving_at(tabs[0], layout["hold_at"])
                if moving:
                    raise GraphicError(
                        f"{folder.name} is still moving at its hold ({layout['hold_at']:.3f}s): "
                        f"{'; '.join(moving[:4])} — end those animations by the end of the intro, "
                        "or declare the hold a loop"
                    )
            # Pipelined: every tab seeks, then every tab shoots, so the tabs
            # draw side by side on one connection.
            for batch_start in range(0, len(jobs), len(tabs)):
                batch = list(zip(tabs, jobs[batch_start : batch_start + len(tabs)], strict=False))
                seeks = [
                    chrome.post(
                        "Runtime.evaluate",
                        {"expression": browser.SEEK % float(t), "awaitPromise": True, "returnByValue": True},
                        session=tab.session,
                    )
                    for tab, (_, _, t) in batch
                ]
                for ident in seeks:
                    reply = chrome.wait(ident)
                    if "exceptionDetails" in reply:
                        raise GraphicError(f"{folder.name}'s page raised while seeking: {reply['exceptionDetails'].get('text')}")
                ids = [
                    chrome.post("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True}, session=tab.session)
                    for tab, _ in batch
                ]
                for (_, (phase, index, _)), ident in zip(batch, ids, strict=True):
                    png = base64.b64decode(chrome.wait(ident)["data"])
                    if phase == "loop-check":
                        shots[(phase, index)] = png
                    else:
                        (staging / phase / f"f{index:04d}.png").write_bytes(png)
                        if phase == "hold" and index == 0:
                            shots[(phase, index)] = png
                progress.report(min(batch_start + len(batch), len(jobs)), len(jobs), f"capturing {folder.name}")
            served_fonts = sorted(set(chrome.fonts))
            sandboxed = chrome.sandboxed
        if layout["loop"] and shots[("loop-check", 0)] != shots[("hold", 0)]:
            raise GraphicError(
                f"{folder.name}'s loop does not come back to where it started: the page at "
                f"{layout['outro_at']:.3f}s differs from the page at {layout['hold_at']:.3f}s — make the "
                "loop's animations repeat on its period, or change its length"
            )
        record = {
            "stamp": stamp(folder, width, height, fps),
            "canvas": [width, height],
            "fps": fps,
            **{phase: layout[phase] for phase in PHASES},
            "loop": layout["loop"],
            "hold_at": layout["hold_at"],
            "fonts": [f for f in fonts if f.get("status") == "loaded"],
            "vendored_fonts": served_fonts,
            "sandboxed": sandboxed,
            "seconds": round(time.monotonic() - started, 2),
            "pages": len(tabs),
        }
        (staging / CAPTURE_NAME).write_text(json.dumps(record, indent=2) + "\n")
        shutil.rmtree(frames, ignore_errors=True)
        staging.rename(frames)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return record


def phase_pattern(frames: Path, phase: str) -> str:
    """The `qimage` sequence resource for one phase's frames."""
    return str(frames / phase / "f%04d.png")


# -- templates -------------------------------------------------------------------


def templates() -> list[dict[str, Any]]:
    """Every built-in template: its phases, and each slot with its default and meaning."""
    out = []
    for folder in sorted(p for p in TEMPLATES_DIR.iterdir() if (p / SPEC_NAME).is_file()):
        raw = json.loads((folder / SPEC_NAME).read_text())
        out.append(
            {
                "name": folder.name,
                "about": raw.get("about", ""),
                "intro": raw.get("intro", 0.0),
                "loop": raw.get("loop"),
                "outro": raw.get("outro", 0.0),
                "slots": raw.get("slots", {}),
            }
        )
    return out


def template_names() -> list[str]:
    return [t["name"] for t in templates()]


def fill_template(name: str, values: dict[str, str], destination: Path) -> dict[str, Any]:
    """Write template `name` into `destination`, its slots filled and escaped.

    Every value is HTML-escaped, quotes included, so one rule holds in text
    and in attributes alike; a template reads a value a script needs from a
    `data-` attribute rather than from inside a `<script>`, which is what
    keeps that one rule enough. An unknown slot is refused, and a slot left
    out takes its default.
    """
    source = TEMPLATES_DIR / name
    if not (source / SPEC_NAME).is_file():
        raise GraphicError(f"no graphic template {name!r} — available: {', '.join(template_names())}")
    raw = json.loads((source / SPEC_NAME).read_text())
    slots: dict[str, dict[str, Any]] = raw.get("slots", {})
    unknown = sorted(set(values) - set(slots))
    if unknown:
        raise GraphicError(f"template {name!r} has no slot(s) {', '.join(unknown)} — it has {', '.join(slots)}")
    filled = {slot: str(values.get(slot, decl.get("default", ""))) for slot, decl in slots.items()}
    missing = sorted(slot for slot, value in filled.items() if slots[slot].get("required") and not value)
    if missing:
        raise GraphicError(f"template {name!r} needs {', '.join(missing)}")
    for slot, value in filled.items():
        _check_slot(name, slot, slots[slot].get("kind", "text"), value)
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if path.is_dir() or path.name == SPEC_NAME:
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in (".html", ".css", ".svg"):
            text = _PLACEHOLDER.sub(lambda m: html.escape(filled.get(m.group(1), m.group(0)), quote=True), path.read_text())
            target.write_text(text)
        else:
            shutil.copyfile(path, target)
    spec = normalise_spec(raw, where=f"template {name!r}")
    spec["template"] = name
    spec["slots"] = filled
    write_spec(destination, spec)
    return spec


_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _check_slot(template: str, slot: str, kind: str, value: str) -> None:
    """A colour or number slot lands inside CSS, so it is held to its shape.

    Escaping keeps a value out of the markup; it cannot keep `}` out of a
    stylesheet, and a number that is not one draws the element nowhere at
    exit 0.
    """
    if kind == "colour" and not _COLOUR.match(value):
        raise GraphicError(f"template {template!r} slot {slot!r} is a colour like #ffd84a, not {value!r}")
    if kind == "number":
        try:
            float(value)
        except ValueError:
            raise GraphicError(f"template {template!r} slot {slot!r} is a number, not {value!r}") from None


# -- the library ------------------------------------------------------------------


def library_root() -> Path:
    """Where saved graphics live, across every project on this machine.

    `PROOFCUT_LIBRARY`, else a `library/graphics` folder beside `proofcut
    setup`'s own — the same per-user data folder, never inside a project,
    because the whole point is that it outlives one.
    """
    named = os.environ.get("PROOFCUT_LIBRARY")
    if named:
        return Path(named).expanduser() / "graphics"
    return deps.root().parent / "library" / "graphics"


def library() -> list[dict[str, Any]]:
    root = library_root()
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if (p / SPEC_NAME).is_file()):
        try:
            spec = read_spec(folder)
        except GraphicError as exc:
            out.append({"name": folder.name, "error": str(exc)})
            continue
        out.append({"name": folder.name, **spec})
    return out


def copy_graphic(source: Path, destination: Path, *, replace: bool = False) -> None:
    """Copy one graphic folder whole. Refuses to overwrite unless `replace`."""
    if not (source / SPEC_NAME).is_file():
        raise GraphicError(f"{source} is not a graphic (no {SPEC_NAME})")
    if destination.exists():
        if not replace:
            raise GraphicError(f"{destination} already exists — pass replace to overwrite it")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def available() -> dict[str, Any]:
    """Whether this machine can capture, for doctor and for a refusal's wording."""
    binary = browser.chrome_path()
    return {"available": binary is not None, "binary": binary, "platform": sys.platform}
