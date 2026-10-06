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

A page's `<video>` runs on the placement's clock, so a still hold plays it on
rather than freezing it, and the outro picks it up where the hold left it
(`play`, below): the footage needs no sizing to the intro.

So the span decides the length and the graphic never does. That is PLAN.md
§ Animation is a length problem's rule kept: a word-addressed span moves with
every cut, and a baked length would be the music bed's failure again.

**A hold that moves is refused unless it is declared a loop.** For a still
hold the capture asks the page what is still animating through the hold's
instant (`browser.moving_at`) — a blinking caret is. For a loop it compares
the loop's first frame against the frame one period on, byte for byte. That
comparison at capture is the authoring-time refusal the PLAN.md note asked for:
`mlt.plan_picture`'s refusal at export would be far too late.

**A capture with nothing in any frame is refused too**, compared byte for
byte against the empty tab's own screenshot. Only all of them: an intro's
first frame is often empty on purpose (hyperframes' liveness probe, adapted,
COMPETITORS.md § hyperframes).

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
import math
import os
import re
import shutil
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from proofcut import browser, deps, progress

SPEC_NAME = "graphic.json"
PAGE_NAME = "index.html"
CAPTURE_NAME = "capture.json"
PHASES = ("intro", "hold", "outro")
#: Bumped when a capture of the same page would draw differently — the flags,
#: the seek — so every cached capture goes stale with it.
CAPTURE_VERSION = 2
#: Salted into the stamp of a page that shows a `<video>`, which draws
#: differently since proofcut serves its frames (`browser.VIDEOS`): those
#: captures go stale, and no other page's does. 2: a capture records when
#: its videos end (`video`), which a hold that plays them on needs (`play`).
VIDEO_CAPTURE_VERSION = 2
_SHOWS_VIDEO = re.compile(rb"<video\b|createElement\(\s*['\"]video['\"]", re.IGNORECASE)
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
    if isinstance(raw.get("clips"), list):
        spec["clips"] = [dict(c) for c in raw["clips"] if isinstance(c, dict)]
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
        if path.suffix in (".html", ".js") and _SHOWS_VIDEO.search(path.read_bytes()):
            digest.update(f"video:{VIDEO_CAPTURE_VERSION}".encode())
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
            # The canvas with nothing on it, for the refusal after the loop.
            blank = browser.screenshot(tabs[0])
            drawn = False
            fonts: list[dict[str, Any]] = []
            for tab in tabs:
                fonts = browser.load(tab)
            _refuse_failed_fonts(folder, fonts)
            if not layout["loop"]:
                moving = browser.moving_at(tabs[0], layout["hold_at"])
                if moving:
                    raise GraphicError(
                        f"{folder.name} is still moving at its hold ({layout['hold_at']:.3f}s): "
                        f"{'; '.join(moving[:4])} — end those animations by the end of the intro, "
                        "or declare the hold a loop"
                    )
            for (phase, index, _, _), png in _shoot(chrome, tabs, folder.name, [(*job, job[2]) for job in jobs]):
                drawn = drawn or png != blank
                if phase == "loop-check":
                    shots[(phase, index)] = png
                else:
                    (staging / phase / f"f{index:04d}.png").write_bytes(png)
                    if phase == "hold" and index == 0:
                        shots[(phase, index)] = png
            # When its videos stop moving, for a hold that plays them on.
            video = browser.video_ends(tabs[0])
            # Again after the last frame: a face no text used at load was
            # `unloaded` then, and fails only once a frame shows its text.
            seen: dict[tuple[str, str, str], dict[str, Any]] = {}
            for tab in tabs:
                for face in browser.fonts(tab):
                    key = (face["family"], face["weight"], face["style"])
                    if seen.get(key, {}).get("status") != "error":
                        seen[key] = face
            fonts = list(seen.values())
            _refuse_failed_fonts(folder, fonts)
            served_fonts = sorted(set(chrome.fonts))
            sandboxed = chrome.sandboxed
        if not drawn:
            # A first frame may be empty on purpose, before a fade-in; a
            # capture with nothing in any frame is a page that never drew,
            # and it would otherwise go over the film as nothing at all.
            raise GraphicError(
                f"{folder.name}'s page drew nothing: all {len(jobs)} frames are as blank as an "
                "empty page — a script that threw before it drew, or content that never arrives "
                "on screen"
            )
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
        if video.get("videos"):
            record["video"] = {"videos": video["videos"], "ends": video.get("ends")}
        (staging / CAPTURE_NAME).write_text(json.dumps(record, indent=2) + "\n")
        shutil.rmtree(frames, ignore_errors=True)
        staging.rename(frames)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return record


def _shoot(
    chrome: browser.Browser, tabs: list[browser.Page], name: str, jobs: list[tuple[str, int, float, float]]
) -> Iterator[tuple[tuple[str, int, float, float], bytes]]:
    """Each job's screenshot, in order: `(phase, index, page seconds, video seconds)`.

    Pipelined: every tab seeks, then every tab shoots, so the tabs draw side
    by side on one connection.
    """
    for batch_start in range(0, len(jobs), len(tabs)):
        batch = list(zip(tabs, jobs[batch_start : batch_start + len(tabs)], strict=False))
        seeks = [
            chrome.post(
                "Runtime.evaluate",
                {"expression": browser.seek_expression(t, vt), "awaitPromise": True, "returnByValue": True},
                session=tab.session,
            )
            for tab, (_, _, t, vt) in batch
        ]
        for ident in seeks:
            reply = chrome.wait(ident)
            if "exceptionDetails" in reply:
                raise GraphicError(f"{name}'s page raised while seeking: {browser.raised(reply['exceptionDetails'])}")
        ids = [
            chrome.post("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True}, session=tab.session)
            for tab, _ in batch
        ]
        for (_, job), ident in zip(batch, ids, strict=True):
            yield job, base64.b64decode(chrome.wait(ident)["data"])
        progress.report(min(batch_start + len(batch), len(jobs)), len(jobs), f"capturing {name}")


def _refuse_failed_fonts(folder: Path, fonts: list[dict[str, Any]]) -> None:
    failed = [f"{f['family']} {f['weight']}" for f in fonts if f.get("status") == "error"]
    if failed:
        raise GraphicError(
            f"{folder.name}'s page could not load the font(s) {', '.join(failed)} — a page "
            f"loads only its own folder and the vendored fonts under {browser.FONTS_PATH}"
        )


def phase_pattern(frames: Path, phase: str) -> str:
    """The `qimage` sequence resource for one phase's frames."""
    return str(frames / phase / "f%04d.png")


# -- a still hold that plays the page's video on -------------------------------
#
# A page's video runs on the placement's clock, not the page's: through the
# intro the two agree, and through a still hold the page stands at `hold_at`
# while the video keeps going, for however many frames the span leaves. So
# the hold is no longer one still but the page at `hold_at` with its video at
# each frame of the hold, and the outro is the page's outro with its video
# where the hold left it. Neither can be captured before the span is known,
# and the span moves with every cut, so they are captured per length, at
# export (`play`), into the capture's own folder — a recapture drops them with
# everything else. A hold frame does not depend on the span, so `hold-play/`
# is one sequence every placement shares, grown as a longer one asks; an
# outro depends on where the hold left the video, so it is one folder per
# offset, `outro-<frames>/`.

PLAY_HOLD = "hold-play"


def video_hold(record: dict[str, Any], held: int) -> dict[str, Any] | None:
    """How a still hold of `held` frames plays the page's video, or None when
    the hold is today's one still: no video, a loop, or every video already
    on its last frame when the hold begins.

    `playing` frames of `hold-play/` play, and the last of them is the still
    the hold `rest`s on once every video has reached its last frame (the page
    holds a video that ran out there, `browser.VIDEOS`). `outro` is the hold
    length the outro's video starts from — capped where the videos freeze,
    since every longer hold leaves them on the same frame.
    """
    video = record.get("video")
    if not video or record.get("loop") or held <= 0:
        return None
    fps, hold_at = float(record["fps"]), float(record["hold_at"])
    ends = video.get("ends")
    # The first hold frame on which every video shows its last frame.
    frozen = None if ends is None else max(0, math.ceil((float(ends) - hold_at) * fps - 1e-6))
    if frozen == 0:
        return None
    playing = held if frozen is None else min(held, frozen + 1)
    return {
        "playing": playing,
        "rest": held - playing,
        "outro": held if frozen is None else min(held, frozen),
        "frozen": frozen,
    }


def _prefix(folder: Path) -> int:
    """How many frames `folder` holds from f0000 on without a gap."""
    count = 0
    while (folder / f"f{count:04d}.png").is_file():
        count += 1
    return count


def play_ready(frames: Path, record: dict[str, Any], plan: dict[str, Any]) -> bool:
    """Whether every frame `plan` draws is on disk already."""
    outro = int(record["outro"])
    return _prefix(frames / PLAY_HOLD) >= plan["playing"] and (
        not outro or not plan["outro"] or _prefix(frames / f"outro-{plan['outro']}") >= outro
    )


def play(folder: Path, frames: Path, held: int, *, pages: int = DEFAULT_PAGES) -> dict[str, Any] | None:
    """Capture what a still hold of `held` frames needs to play the page's
    video on — the `hold-play/` frames it lacks and its outro — and return
    `video_hold`'s plan, or None when the hold is one still.

    The page must be the one `frames` was captured from (the caller checks
    the stamp first). The last `hold-play` frame before a `rest` is checked
    against the frame after it, byte for byte, as a loop is: a still that is
    not still would be a frozen frame where the page moves.
    """
    record = read_capture(frames)
    if record is None:
        raise GraphicError(f"{folder.name} has not been captured")
    plan = video_hold(record, held)
    if plan is None or play_ready(frames, record, plan):
        return plan
    fps, hold_at, outro = float(record["fps"]), float(record["hold_at"]), int(record["outro"])
    width, height = (int(n) for n in record["canvas"])
    have = _prefix(frames / PLAY_HOLD)
    jobs: list[tuple[str, int, float, float]] = [
        ("hold", k, hold_at, hold_at + k / fps) for k in range(have, plan["playing"])
    ]
    if plan["rest"] and have < plan["playing"]:
        jobs.append(("still-check", 0, hold_at, hold_at + plan["playing"] / fps))
    outro_dir = frames / f"outro-{plan['outro']}"
    if outro and plan["outro"] and _prefix(outro_dir) < outro:
        jobs += [("outro", k, hold_at + k / fps, hold_at + (plan["outro"] + k) / fps) for k in range(outro)]
    staging = frames / f".play-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "hold").mkdir(parents=True)
    (staging / "outro").mkdir()
    last = check = None
    try:
        with browser.launch(folder) as chrome:
            tabs = [chrome.new_page(width, height) for _ in range(max(1, min(MAX_PAGES, int(pages), len(jobs))))]
            for tab in tabs:
                _refuse_failed_fonts(folder, browser.load(tab))
            for (phase, index, _, _), png in _shoot(chrome, tabs, folder.name, jobs):
                if phase == "still-check":
                    check = png
                    continue
                (staging / phase / f"f{index:04d}.png").write_bytes(png)
                if phase == "hold" and index == plan["playing"] - 1:
                    last = png
        if check is not None and check != last:
            raise GraphicError(
                f"{folder.name}'s page still changes after its videos reached their last frame "
                f"({hold_at + plan['playing'] / fps:.3f}s of video) — proofcut cannot hold it still there"
            )
        # In index order, so an interrupted move leaves a prefix `_prefix` reads.
        (frames / PLAY_HOLD).mkdir(exist_ok=True)
        for k in range(have, plan["playing"]):
            os.replace(staging / "hold" / f"f{k:04d}.png", frames / PLAY_HOLD / f"f{k:04d}.png")
        if any((staging / "outro").iterdir()):
            shutil.rmtree(outro_dir, ignore_errors=True)
            (staging / "outro").rename(outro_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return plan


def sweep_outros(frames: Path, keep: set[str]) -> list[str]:
    """Drop every `outro-<frames>/` under `frames` not named in `keep`, and
    return the names dropped.

    One is captured per hold length a render asked for, so every cut that
    moves a span leaves one behind. The render that captures is the one that
    knows which it draws (`_build_mlt`), and it keeps exactly those: a length
    asked for again is recaptured, which costs its outro's frames alone.
    `hold-play/` is not swept, since every length shares its prefix. Never
    raises — a sweep is a side effect of rendering.
    """
    dropped = []
    try:
        folders = sorted(frames.iterdir())
    except OSError:
        return dropped
    for folder in folders:
        if re.fullmatch(r"outro-\d+", folder.name) and folder.name not in keep and folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            dropped.append(folder.name)
    return dropped


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
