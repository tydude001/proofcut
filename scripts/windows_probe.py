#!/usr/bin/env python3
"""Measure what the Windows demo does not reach — docs/plans/PORTABILITY.md 5e.

`scripts/windows_trial.ps1` proves the demo runs, in one short ASCII folder on
C:. Every Windows-only defect class PORTABILITY.md 5e names lives outside that:
a space or an accent in a path, a character the ANSI code page cannot hold, a
project on another drive from its footage, a path past MAX_PATH, two spellings
of one directory against the confinement check, and libass drawing a caption
through DirectWrite. This runs the demo's own edit once per class, each in a
folder shaped to hit it, and reads every result back the way `trial_check.py`
does: the render's frame count, and the colour of the frame at 3 s and 10 s.

**The `plain` case is the control.** It is the same edit in a folder with
nothing unusual about it; if it fails, every other case's failure is about
this PC, not about the path, and the summary says so.

It reuses the kit's folder — the tools, the demo footage and the demo's own
transcript — so it transcribes nothing and downloads nothing, and
`windows_trial.ps1 -Uninstall` removes what it writes there. Run it through
`scripts/windows_probe.ps1`, which sets the kit's environment up first:

    powershell -ExecutionPolicy Bypass -File proofcut\\scripts\\windows_probe.ps1
    powershell -ExecutionPolicy Bypass -File proofcut\\scripts\\windows_probe.ps1 -SecondDrive E:\\ -Footage C:\\path\\to\\clip.mp4

Your own footage is measured and never shipped: the report carries its
numbers — codec, frame rate, streams, whether it rendered — and neither its
name nor a frame of it.

Off Windows every case still runs, as the harness's own test — this box's
run is how the checks were calibrated — but a case-sensitive filesystem has no
second spelling to give the confinement case, so it asks with one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_demo import BROLL

from proofcut.project import max_root_length

WINDOWS = os.name == "nt"
#: Same tolerance as `trial_check.py`: the two clips sit ~116 apart in RGB,
#: and a right frame measured within 3 on every kit run so far.
COLOUR_TOLERANCE = 30
FRAMES = [(3.0, "BLUE"), (10.0, "RUST")]
#: The long-path case's project root, in characters. Windows' MAX_PATH is 260
#: for a file and 248 for a directory, so a 235-character root puts
#: `cache\thumbs\blue\` (+18) past the directory limit and
#: `cache\transcripts\vo.json` (+26) past the file one, while the footage and
#: the render (+19 or less) stay under both — so a failure names which side
#: of the line it was on. With long paths off it measured exactly that on a
#: laptop, dying on `cache\transcripts`; `init` now refuses such a root, so
#: the case asks for that refusal and then runs the edit at the limit it names.
LONG_ROOT = 235
#: A luma change a caption's glyph makes and a re-encode does not.
CAPTION_LUMA_STEP = 60
#: Percent of the bottom third a burnt caption line must change. On Linux a
#: line measured 1.43–2.32 at seven moments, and the gap between the demo's
#: second and third lines (3.80–4.01 s, `CAPTION_GAP`) measured 0.0 at four.
CAPTION_MIN_SHARE = 0.5
#: When the burn is read with a line on screen, and in the gap with none — the
#: control, so a burn that changed everything (a re-scale, a colour shift)
#: does not pass as captions.
CAPTION_AT = 1.0
CAPTION_GAP = 3.9
STEP_TIMEOUT = 900

LOG: list[str] = []
#: Both spellings, for a home reached through a symlink (/home -> /var/home here).
HOMES = sorted({str(Path.home()), os.path.realpath(Path.home())}, key=len, reverse=True)
#: The tester's own clip, by name and by folder — both can say something personal.
FOOTAGE: list[str] = []


def say(line: str = "") -> None:
    print(line, flush=True)
    LOG.append(line)


def scrub(text: str) -> str:
    """The home folder in every spelling a path takes (the kit's `Hide-Home`),
    and the tester's own footage file by name."""
    # The footage first: its forms contain the home folder, and would no longer match after it.
    for form in sorted(FOOTAGE, key=len, reverse=True):
        text = re.sub(re.escape(form), "<your-footage>", text, flags=re.IGNORECASE)
    for home in HOMES:
        for form in (home.replace("\\", "\\\\"), home, home.replace("\\", "/")):
            text = re.sub(re.escape(form), "~", text, flags=re.IGNORECASE)
    return text


def scrub_values(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: scrub_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_values(v) for v in value]
    return value


def long_form(path: Path) -> str:
    """A path Win32 file APIs accept past MAX_PATH, for our own cleanup and
    measurement only — never handed to proofcut, which is what is measured."""
    text = str(path.resolve())
    return "\\\\?\\" + text if WINDOWS and not text.startswith("\\\\?\\") else text


def remove_tree(path: Path) -> None:
    if os.path.lexists(long_form(path)):
        shutil.rmtree(long_form(path), ignore_errors=True)


def run(argv: list[str], *, timeout: int = STEP_TIMEOUT) -> tuple[int, float, str]:
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,  # picture.py's own lesson: a console on stdin can hang a child
            timeout=timeout,
            check=False,
        )
        return done.returncode, time.monotonic() - started, (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, time.monotonic() - started, f"timed out after {timeout}s"
    except OSError as exc:
        return -1, time.monotonic() - started, f"could not start: {exc}"


def first_json(text: str) -> Any:
    brace = text.find("{")
    if brace < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text, brace)
        return value
    except json.JSONDecodeError:
        return None


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mean_colour(video: Path, at: float, png: Path) -> tuple[int, int, int] | None:
    """The frame at `at` as a PNG for a person, and its mean colour for the check."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    run([ffmpeg, "-v", "error", "-y", "-ss", f"{at}", "-i", str(video), "-frames:v", "1", str(png)], timeout=120)
    done = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=120, check=False,
    )
    if done.returncode != 0 or len(done.stdout) < 3:
        return None
    return done.stdout[0], done.stdout[1], done.stdout[2]


def band_difference(a: Path, b: Path, at: float) -> float | None:
    """The share of the bottom third's pixels, in percent, whose luma moved by
    more than `CAPTION_LUMA_STEP` between two videos' frames at `at` — how much
    of where captions draw a burn actually changed. A mean over the band was
    the first build and read 2.7 of 255 for a caption plainly on screen: thin
    text is a few percent of a band, and the re-encode's own noise moves every
    pixel a little."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    planes = []
    for video in (a, b):
        done = subprocess.run(
            [ffmpeg, "-v", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
             "-vf", "crop=iw:ih/3:0:ih*2/3", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=120, check=False,
        )
        if done.returncode != 0 or not done.stdout:
            return None
        planes.append(done.stdout)
    if len(planes[0]) != len(planes[1]):
        return None
    moved = sum(1 for x, y in zip(*planes) if abs(x - y) > CAPTION_LUMA_STEP)
    return round(100 * moved / len(planes[0]), 2)


def longest_path(root: Path) -> int:
    longest = 0
    for folder, dirs, files in os.walk(long_form(root)):
        for name in dirs + files:
            longest = max(longest, len(os.path.join(folder, name).removeprefix("\\\\?\\")))
    return longest


class Case:
    """One folder shape, one run of the demo's edit in it."""

    def __init__(self, name: str, what: str) -> None:
        self.name = name
        self.what = what
        self.steps: list[dict[str, Any]] = []
        self.facts: dict[str, Any] = {}
        self.outcome = "not run"

    def step(self, label: str, argv: list[str], check: Any = None) -> Any:
        """Run one command; a step passes on exit 0 with JSON whose `check` holds."""
        say(f"── {self.name}: {label}")
        say(scrub("$ " + " ".join(argv)))
        code, seconds, output = run(argv)
        say(scrub(output.rstrip()))
        value = first_json(output)
        why = ""
        if code != 0:
            why = f"exit {code}"
        elif value is None:
            why = "printed no JSON"
        elif check is not None:
            try:
                verdict = check(value)
            except (KeyError, TypeError, IndexError) as exc:
                verdict = f"unexpected reply ({exc!r})"
            if verdict is not True:
                why = verdict if isinstance(verdict, str) else "check failed"
        ok = not why
        self.steps.append({"step": label, "ok": ok, "seconds": round(seconds, 1), "why": why})
        say(f"{'ok' if ok else '!! FAIL'} ({seconds:.0f}s){'  ' + why if why else ''}")
        if not ok:
            raise StepFailed(label)
        return value

    def fact(self, label: str, ok: bool, why: str = "", **facts: Any) -> None:
        self.facts.update(facts)
        self.steps.append({"step": label, "ok": ok, "seconds": 0.0, "why": why})
        say(f"── {self.name}: {label}\n{'ok' if ok else '!! FAIL'}{'  ' + why if why else ''}")
        if not ok:
            raise StepFailed(label)


class StepFailed(RuntimeError):
    pass


class SkipCase(RuntimeError):
    """A case this run cannot ask — no second drive given, or its control failed."""


def proofcut(project: Path, *args: str) -> list[str]:
    # The interpreter and `-m`, never a bare `proofcut`: the same reason the
    # agent panel's MCP config names sys.executable (CLAUDE.md).
    return [sys.executable, "-m", "proofcut.cli", "-C", str(project), *args]


def demo_edit(case: Case, project: Path, footage: Path, demo: Path, frames_dir: Path) -> Path:
    """The kit's DEMO steps 2–6, with the kit's own transcript attached rather
    than re-transcribed, and every result read back. Returns the render."""
    footage.mkdir(parents=True, exist_ok=True)
    for name in ("vo.wav", "broll-blue.mp4", "broll-rust.mp4"):
        if not (footage / name).exists():
            shutil.copyfile(demo / name, footage / name)
    remove_tree(project)
    project.parent.mkdir(parents=True, exist_ok=True)
    case.facts["project_chars"] = len(str(project))

    case.step("init", [sys.executable, "-m", "proofcut.cli", "init", str(project)])
    for clip, name in (("vo", "vo.wav"), ("blue", "broll-blue.mp4"), ("rust", "broll-rust.mp4")):
        case.step(f"import {clip}", proofcut(project, "import", str(footage / name), "--clip-id", clip),
                  lambda v, c=clip: v["clip_id"] == c or f"registered {v.get('clip_id')!r}")
    case.step("attach the kit's transcript",
              proofcut(project, "attach-transcript", "vo", str(demo / "proj" / "cache" / "transcripts" / "vo.json")),
              lambda v: v["words"] == 47 or f"{v['words']} words, the demo has 47")
    case.step("seed (auto-editor)", proofcut(project, "seed", "vo"))
    case.step("cut the retake", proofcut(project, "cut", "vo", "11:23", "--pad", "0.1"),
              lambda v: v["applied"][0]["text"].startswith("Every cut you make names a")
              or f"cut {v['applied'][0]['text']!r}")
    case.step("cue blue", proofcut(project, "cue", "add", "vo", "--phrase", "Every cut you make names a word", "blue"))
    case.step("cue rust", proofcut(project, "cue", "add", "vo", "--phrase", "the render can be checked", "rust"))
    render = project / "renders" / "probe.mp4"
    case.step("render (melt)", proofcut(project, "export", str(render), "--render"),
              lambda v: v["rendered"]["agrees"] is True or "render disagrees with the timeline")
    case.step("frames", proofcut(project, "frames", str(render)),
              lambda v: v["agrees"] is True or f"delta {v.get('delta')}")
    for at, label in FRAMES:
        png = frames_dir / f"{case.name}-{int(at)}s.png"
        got = mean_colour(render, at, png)
        want = next(hex_rgb(colour) for _, colour, name in BROLL if name == label)
        if got is None:
            case.fact(f"frame at {at:.0f}s is {label}", False, "could not read the frame")
        distance = round(sum((a - b) ** 2 for a, b in zip(got, want)) ** 0.5, 1)
        case.fact(f"frame at {at:.0f}s is {label}", distance <= COLOUR_TOLERANCE,
                  f"mean colour {got} is {distance} from {label}" if distance > COLOUR_TOLERANCE else "")
    case.step("thumbnail", proofcut(project, "thumbnail", "blue", "4.5"))
    case.step("preview says the browser can play it", proofcut(project, "preview", "blue"))
    case.step("finish-report", proofcut(project, "finish-report"))
    return render


def run_case(case: Case, cases: list[Case], body: Any) -> None:
    cases.append(case)
    say()
    say(f"════ case {case.name} — {case.what}")
    try:
        body(case)
        case.outcome = "ok"
    except StepFailed as exc:
        case.outcome = f"failed at: {exc}"
    except SkipCase as exc:
        case.outcome = f"skipped: {exc}"
        say(f"skipped: {exc}")
    except Exception as exc:  # noqa: BLE001 — the probe must reach its report whatever one case does
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]  # anyio wraps an MCP server's death two groups deep
        case.outcome = f"probe error: {exc!r}"
        say(scrub(f"!! probe error: {exc!r}"))


async def confinement(case: Case, bound: Path, sibling: Path) -> None:
    """`-C` with one spelling, `path` with another: the same directory must be
    accepted, a different one refused. Windows compares resolved paths on a
    case-insensitive filesystem, which is the thing to measure."""
    from mcp import ClientSession, StdioServerParameters, stdio_client

    bound_spelling = str(bound).upper() if WINDOWS else str(bound)
    other_spelling = str(bound).lower() if WINDOWS else str(bound)
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "proofcut.cli", "-C", bound_spelling, "mcp"]
    )
    say(f"── {case.name}: server bound with -C spelled {'UPPER' if WINDOWS else 'as-is'}")
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        replies = {}
        for label, path in (("no path", None), ("the same folder, other case", other_spelling), ("a different project", str(sibling))):
            args = {} if path is None else {"path": path}
            result = await session.call_tool("timeline_status", args)
            # Scrubbed here, before anything slices it: a cut through the home
            # folder's name leaves a fragment no whole-path pattern matches.
            text = scrub(result.content[0].text if result.content else "")
            replies[label] = (result.is_error, text)
            say(f"{label}: {'refused' if result.is_error else 'accepted'} — {text[:160]}")
    no_path, same = replies["no path"], replies["the same folder, other case"]
    case.fact("bound project, no path: accepted", not no_path[0], no_path[1][:200] if no_path[0] else "")
    case.fact("same folder spelled in another case: accepted", not same[0], same[1][:200] if same[0] else "")
    case.fact("a different project: refused", replies["a different project"][0],
              "" if replies["a different project"][0] else "accepted a project outside the bound one")


def environment() -> dict[str, Any]:
    facts: dict[str, Any] = {"platform": sys.platform, "python": sys.version.split()[0]}
    if WINDOWS:
        import ctypes
        import platform
        import winreg

        facts["windows"] = platform.version()
        facts["ansi_code_page"] = ctypes.windll.kernel32.GetACP()
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
                facts["long_paths_enabled"] = winreg.QueryValueEx(key, "LongPathsEnabled")[0]
        except OSError:
            facts["long_paths_enabled"] = None
    facts["claude_on_path"] = shutil.which("claude") is not None
    facts["claude_is_cmd"] = (shutil.which("claude") or "").lower().endswith(".cmd")
    facts["magick_on_path"] = shutil.which("magick") is not None
    return facts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kit", type=Path, help="the kit's folder (%%LOCALAPPDATA%%\\proofcut-windows-trial)")
    parser.add_argument("--second-drive", type=Path, help="a folder on another drive, e.g. E:\\")
    parser.add_argument("--footage", type=Path, help="one of your own clips; measured, never shipped")
    parser.add_argument("--report", type=Path, help="the zip to write (default: <kit>/probe-report.zip)")
    parser.add_argument("--only", help="comma-separated case names, to re-ask a few (plain always runs)")
    args = parser.parse_args()

    kit = args.kit.resolve()
    demo = kit / "demo"
    if not (demo / "proj" / "cache" / "transcripts" / "vo.json").exists():
        print(f"No finished demo under {demo}. Run scripts/windows_trial.ps1 first; this reuses its footage and transcript.")
        return 1
    if args.footage:
        where = args.footage.absolute()
        # Longest first, so the folder is not replaced out from under the file's full path.
        # The folder is scrubbed because its name can say something (`Private Stuff`); a
        # drive root says nothing, and scrubbing `E:\\` turned every path on the second
        # drive into `<your-footage>…`, so the report never said which drive the
        # second-drive case ran on (HISTORY.md § The second-drive case, on a flash drive).
        for spelling in {where, where.resolve()}:
            FOOTAGE.extend([str(spelling).replace("\\", "\\\\"), str(spelling)])
            if spelling.parent != spelling.parent.parent:
                FOOTAGE.extend([str(spelling.parent).replace("\\", "\\\\"), str(spelling.parent)])
        FOOTAGE.append(where.name)
    work = kit / "probe"
    remove_tree(work)
    frames_dir = work / "frames"
    frames_dir.mkdir(parents=True)
    report = args.report or kit / "probe-report.zip"

    rev = run(["git", "-C", str(Path(__file__).resolve().parent.parent), "rev-parse", "--short", "HEAD"], timeout=30)
    env = environment()
    say(f"proofcut Windows probe · proofcut {rev[2].strip() if rev[0] == 0 else 'no-git'} · {time.strftime('%Y-%m-%d %H:%M %z')}")
    say(json.dumps(env))

    cases: list[Case] = []
    renders: dict[str, Path] = {}
    shared_footage = work / "plain" / "footage"

    def plain(c: Case) -> None:
        renders["plain"] = demo_edit(c, work / "plain" / "proj", shared_footage, demo, frames_dir)

    def spaced(c: Case) -> None:
        demo_edit(c, work / "with space" / "My Project", work / "with space" / "My Footage", demo, frames_dir)

    def accented(c: Case) -> None:
        demo_edit(c, work / "vidéo ñ" / "proj", work / "vidéo ñ" / "footage", demo, frames_dir)

    def cjk(c: Case) -> None:
        demo_edit(c, work / "映像 проект" / "proj", work / "映像 проект" / "footage", demo, frames_dir)

    def deep_root(length: int) -> Path:
        root = work / "long"
        n = 0
        while len(str(root)) < length - 12:
            root = root / f"nested-{n:02d}"
            n += 1
        return Path(str(root) + "-" + "p" * max(1, length - len(str(root)) - 1))

    def long_path(c: Case) -> None:
        project = deep_root(LONG_ROOT)
        c.facts["long_paths_enabled"] = env.get("long_paths_enabled")
        limit = max_root_length()
        try:
            if limit is None:
                demo_edit(c, project, project.parent / "f", demo, frames_dir)
                return
            # Long paths off: `init` refuses this root in one line before
            # writing anything (HISTORY.md § A long project path on Windows),
            # and a project at exactly the limit it names runs the whole edit.
            say(f"── {c.name}: init refuses a {LONG_ROOT}-character root (the limit here is {limit})")
            argv = [sys.executable, "-m", "proofcut.cli", "init", str(project)]
            say(scrub("$ " + " ".join(argv)))
            code, _, output = run(argv, timeout=60)
            say(scrub(output.rstrip()))
            c.facts["max_root_length"] = limit
            clean = code == 1 and "Traceback" not in output and f"at most {limit} characters" in output
            c.fact("refused in one line, naming the limit", clean,
                   "" if clean else f"exit {code}, {'a traceback' if 'Traceback' in output else 'no limit named'}")
            c.fact("and wrote nothing", not os.path.exists(long_form(project)),
                   "" if not os.path.exists(long_form(project)) else "the refused root exists")
            at_limit = deep_root(limit)
            demo_edit(c, at_limit, at_limit.parent / "f", demo, frames_dir)
        finally:
            c.facts["longest_path_written"] = longest_path(work / "long")
            say(f"longest path written under the long case: {c.facts['longest_path_written']} characters")

    def second_drive(c: Case) -> None:
        if not args.second_drive:
            raise SkipCase("no -SecondDrive given")
        base = args.second_drive / "proofcut-probe"
        try:
            demo_edit(c, base / "proj", shared_footage, demo, frames_dir)
        finally:
            remove_tree(base)

    def confine(c: Case) -> None:
        if "plain" not in renders:
            raise SkipCase("the plain case did not finish")
        sibling = work / "with space" / "My Project"
        if not sibling.exists():
            sibling = work / "sibling"
            run([sys.executable, "-m", "proofcut.cli", "init", str(sibling)], timeout=60)
        asyncio.run(confinement(c, work / "plain" / "proj", sibling))

    def burn(c: Case) -> None:
        if "plain" not in renders:
            raise SkipCase("the plain case did not finish")
        project = work / "plain" / "proj"
        captioned = project / "renders" / "probe-captioned.mp4"
        c.step("burn captions (libass)",
               proofcut(project, "captions", str(project / "renders" / "probe.ass"),
                        "--burn", str(renders["plain"]), "--burn-output", str(captioned)))
        diff = band_difference(renders["plain"], captioned, CAPTION_AT)
        gap = band_difference(renders["plain"], captioned, CAPTION_GAP)
        mean_colour(captioned, CAPTION_AT, frames_dir / "captions-1s.png")
        say(f"bottom third changed: {diff}% with a line up, {gap}% in the gap (frames/captions-1s.png shows the face)")
        drew = diff is not None and diff >= CAPTION_MIN_SHARE
        c.fact("the burn drew a line where captions go", drew,
               "" if drew else f"{diff}% of the bottom third changed", caption_changed_percent=diff)
        quiet = gap is not None and gap < CAPTION_MIN_SHARE
        c.fact("and nothing in the gap between lines", quiet,
               "" if quiet else f"{gap}% changed with no line on screen", caption_gap_changed_percent=gap)

    def own_footage(c: Case) -> None:
        if not args.footage:
            raise SkipCase("no -Footage given")
        project = work / "own" / "proj"
        remove_tree(project)
        project.parent.mkdir(parents=True, exist_ok=True)
        c.step("init", [sys.executable, "-m", "proofcut.cli", "init", str(project)])
        # An OBS recording can have two audio streams, which import refuses by
        # design; sum them, the way a person would be told to. An iPhone clip
        # recording Spatial Audio has two as well, and --mix is the call that
        # found import could not read the second (HISTORY.md § The phone's
        # Spatial Audio track), so it stays the call this asks.
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        streams = run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                       "-of", "csv=p=0", str(args.footage.resolve())], timeout=60)[2].split()
        extra = ["--mix"] if len(streams) > 1 else []
        c.facts["mixed_on_import"] = bool(extra)
        info = c.step("import", proofcut(project, "import", str(args.footage.resolve()), "--clip-id", "own", *extra))
        c.facts.update({k: info.get(k) for k in (
            "video_codec", "audio_codec", "audio_streams", "width", "height", "fps", "vfr", "bit_depth", "duration", "has_chapters")})
        c.step("preview says the browser can play it", proofcut(project, "preview", "own"))
        c.step("thumbnail", proofcut(project, "thumbnail", "own", "1.0"))
        if not info.get("has_audio"):
            raise SkipCase("the clip has no audio, so there is no silence cut to render")
        c.step("seed (auto-editor)", proofcut(project, "seed", "own"))
        render = project / "renders" / "own.mp4"
        c.step("render (auto-editor, single source)", proofcut(project, "export", str(render), "--render"),
               lambda v: (v.get("rendered") or {}).get("agrees", True) is not False or "render disagrees")
        c.step("frames", proofcut(project, "frames", str(render)),
               lambda v: v["agrees"] is True or f"delta {v.get('delta')}")

    plan = [
        ("plain", "the control: the demo's edit in an ordinary folder", plain),
        ("space", "a space in the project's and the footage's folder names", spaced),
        ("accented", "é and ñ in the path — characters the ANSI code page holds", accented),
        ("cjk", "Japanese and Cyrillic in the path — characters it does not", cjk),
        ("long-path", f"a {LONG_ROOT}-character project root, straddling MAX_PATH", long_path),
        ("second-drive", "the project on another drive, its footage on this one", second_drive),
        ("confinement", "MCP -C in one letter case, path in another", confine),
        ("captions", "a caption burn through libass", burn),
        ("own-footage", "your own clip: import, preview, a single-source render", own_footage),
    ]
    wanted = set((args.only or "").split(",")) - {""}
    for name, what, body in plan:
        if wanted and name != "plain" and name not in wanted:
            continue
        run_case(Case(name, what), cases, body)

    # A clean long-path case on a PC that has opted into long paths measured
    # the opt-in, not MAX_PATH: GitHub's runner has LongPathsEnabled=1 and a
    # stock Windows 11 has 0. Say which one this was, on the summary line.
    for case in cases:
        if case.name == "long-path" and case.outcome == "ok" and env.get("long_paths_enabled") == 1:
            case.outcome = "ok, but long paths are enabled here"
    say()
    say("════ summary")
    control_ok = cases[0].outcome == "ok"
    for case in cases:
        say(f"  {case.outcome:<40.40}  {case.name} — {case.what}")
    if not control_ok:
        say("THE CONTROL FAILED: every other case's result is about this PC, not about its path.")
    else:
        failed = [c.name for c in cases if c.outcome.startswith(("failed", "probe error"))]
        say("ALL CASES RAN CLEAN" if not failed else "FINDINGS IN: " + ", ".join(failed))

    work.mkdir(parents=True, exist_ok=True)
    (work / "report.txt").write_text(scrub("\n".join(LOG)) + "\n", encoding="utf-8")
    # Scrubbed value by value, never as dumped text: `json.dumps` doubles every
    # backslash again, and a Windows path inside an already-escaped reply then
    # matches no spelling `scrub` knows — the first runner report carried its
    # username in two `why` fields that way.
    (work / "probe.json").write_text(json.dumps(scrub_values({
        "environment": env,
        "cases": [{"case": c.name, "what": c.what, "outcome": c.outcome, "facts": c.facts, "steps": c.steps} for c in cases],
    }), indent=2), encoding="utf-8")
    report.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(report, "w", zipfile.ZIP_DEFLATED) as out:
        out.write(work / "report.txt", "report.txt")
        out.write(work / "probe.json", "probe.json")
        for png in sorted(frames_dir.glob("*.png")):
            out.write(png, f"frames/{png.name}")
    say()
    say(scrub(f"The report is at {report}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
