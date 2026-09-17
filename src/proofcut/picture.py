"""The picture half of checking a render, starting with the frame count.

`verify` re-transcribes a render and diffs word order; it covers the audio and
says so. This covers the picture, and the frame count ranks first of its three checks: exact agreement between proofcut's
computed total and what `melt` says it will render is what made 68 cut
positions on the Scream essay trustworthy **before** anything was rendered
(HISTORY.md § 3). `blackdetect` (a black-run scan) and spot frames (sampled
PNGs with luma stats) are its siblings, reading a finished render directly
rather than a document melt would produce.

The check earns its place because the two numbers are arrived at differently.
proofcut's total comes from quantising every segment edge onto the export's frame
grid (`autoeditor.frame_layout`). melt's comes from an MLT document auto-editor
wrote, in which the timeline's length is declared in several places at once and
**melt renders to the longest of them** — the failure goodsometimes
`pipeline.md` § Rendering documents for hand-written MLT, where four declared
lengths had to be swept in step and the one that actually bit was a black
background track nobody had touched.

`melt` is not a host package on this box; it ships inside the Kdenlive flatpak.
`melt_command` resolves it, and `display_env` carries the Qt trap that costs a
render its card track — both ported from goodsometimes `scripts/render.py`
rather than rediscovered.

`render()` is the other half of that port: melt is also what *renders* a
multi-source timeline, since auto-editor degrades one to 720x576 while exiting
0 (CLAUDE.md). Its three traps and the memory cap are handled there, and
nothing about the render is believed on the strength of an exit code — the
finished file is probed, and the numbers it comes back with are compared
against the numbers the timeline promised.
"""

from __future__ import annotations

import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from proofcut import deps, media, progress

#: Suffixes routed to `melt` rather than to ffprobe. `.xml` is here because
#: that is what a bare MLT document is called; auto-editor writes `.kdenlive`.
NLE_SUFFIXES = {".kdenlive", ".mlt", ".xml"}

KDENLIVE_FLATPAK = "org.kde.kdenlive"

#: How long to wait on a blackdetect pass — it decodes the whole render.
BLACKDETECT_TIMEOUT = 300

#: How long to wait on pulling one frame. Generous because a seek near the
#: start of a long file still has to demux up to it.
FRAME_EXTRACT_TIMEOUT = 60

#: How long to wait on rendering a tail's silence. Trivial work — `anullsrc`
#: reads nothing — so this is generous only for the same reason the others
#: are: a cold subprocess start, not the encode.
SILENCE_TIMEOUT = 30

#: The rate/layout every silent tail WAV is rendered at. Not derived from the
#: project — a tail's audio-track entry is played back through MLT the same
#: way any other audio-only clip is, resampled by the consumer to whatever it
#: needs, and 48kHz stereo is the shape `media.probe` already reports for real
#: footage on this box (tests/test_picture.py's own fixture).
SILENCE_SAMPLE_RATE = 48000

_BLACK_RE = re.compile(
    r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)"
)

#: How long to wait on melt. It is reading a document, not rendering one, so
#: this is generous — it exists because a cold flatpak start is slow and a
#: `melt` pointed at unreachable media can sit rather than fail.
MELT_TIMEOUT = 180

#: **auto-editor's `--export kdenlive` output is one frame too long, and the
#: extra frame is black.** Measured on this box 2026-08-07, auto-editor 31.x
#: against MLT 7.40: a 360-frame timeline came back from `-consumer xml` as
#: `length` 361, rendered 361 frames, and the last one measured YAVG 16 against
#: ~123 for real picture. A 276-frame cut of the same source reported 277, and
#: an audio-only export carried the same +1 — so it is structural, not a
#: rounding accident.
#:
#: The cause is the shape goodsometimes already documents from the other side:
#: MLT's `out` is frame-*inclusive*, and auto-editor writes the tractors' `out`
#: as the frame *count* instead of the last frame *index*. The entries
#: themselves are right (`out="00:00:11.967"` is frame 359, correct for 360
#: frames); the three tractors declaring `00:00:12.000` are not.
#:
#: This is named so a reader can tell it apart from a timeline that is
#: genuinely wrong. It is **not** subtracted anywhere: the frame is really in
#: the render, `agrees` stays False, and rendering with auto-editor directly
#: (`export --render`) does not have it — that path counted 360, exactly.
KNOWN_TAIL_FRAME = 1

TAIL_FRAME_NOTE = (
    "melt reports exactly one frame more than the timeline holds, which is the "
    "known auto-editor kdenlive-export defect rather than a wrong cut: it "
    "declares the tractors' frame-inclusive `out` as a frame count, so melt "
    "renders a trailing black frame. Reported, not corrected — the frame is "
    "really there. `export --render` (auto-editor's own renderer) does not "
    "have it. See picture.KNOWN_TAIL_FRAME."
)


class PictureError(Exception):
    """Raised when a picture-side check cannot be run or cannot be read."""


def melt_bundles() -> list[Path]:
    """Where a desktop editor ships its own `melt` on this OS, searched after PATH.

    Shotcut and Kdenlive both bundle one on macOS and Windows, and neither
    puts it on PATH. **Whether these bundles carry every module proofcut's
    documents use (`qtblend`, `qimage`, `affine`, `avformat`) is unmeasured**
    — docs/plans/PORTABILITY.md step 4's first question — and a missing module
    renders *something* at exit 0, so finding one here is not a claim that it
    renders correctly. Empty on Linux, where the flatpak is the fallback.
    """
    if sys.platform == "darwin":
        return [
            Path(apps) / bundle
            for apps in ("/Applications", Path.home() / "Applications")
            for bundle in ("Shotcut.app/Contents/MacOS/melt", "kdenlive.app/Contents/MacOS/melt")
        ]
    if sys.platform == "win32":
        program_files = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        return [program_files / "Shotcut" / "melt.exe", program_files / "kdenlive" / "bin" / "melt.exe"]
    return []


def melt_search() -> tuple[str, str]:
    """(where `melt_command` looks after PATH, how to get a melt there), for this OS.

    Stated once so `melt_command`'s refusal and doctor's row say the same
    thing — a doctor naming the flatpak on a Mac sends someone to a package
    manager their OS has not got.
    """
    if sys.platform in ("darwin", "win32"):
        where = ", ".join(str(p) for p in melt_bundles())
        return (
            f"the Shotcut and Kdenlive installs ({where})",
            (
                "Install Shotcut (shotcut.org) or Kdenlive (kdenlive.org) — both "
                "ship melt inside the application — or set PROOFCUT_MELT to a melt command."
            ),
        )
    return (
        f"the Kdenlive flatpak ({KDENLIVE_FLATPAK})",
        (
            # The distribution package leads: a clean Ubuntu 24.04 following the
            # flatpak-only advice had `apt install melt` (7.22, renders) one line
            # away. HISTORY.md § A stranger's install, on a clean Ubuntu.
            # Fedora names it `mlt`; its `melt` package is freeze, a compression
            # tool that owns /usr/bin/melt. HISTORY.md § A stranger's install, on
            # a clean Fedora.
            "`proofcut setup` installs Shotcut's portable melt for this user "
            "(x86_64, no sudo), which draws with no X server. Or install your "
            "distribution's melt package (`apt install melt` on "
            "Debian/Ubuntu, `dnf install mlt` on Fedora — Fedora's own `melt` "
            "package is an unrelated compression tool), or Kdenlive's flatpak, "
            "which ships melt inside it and is found on its own (`flatpak install "
            "org.kde.kdenlive`) — or set PROOFCUT_MELT to a melt command."
        ),
    )


def user_bus(env: dict[str, str]) -> bool:
    """Whether `systemd-run --user` has a user session bus to reach.

    On PATH is not the same as usable. A clean Ubuntu container has the binary
    and no bus, so a capped render died with "Failed to connect to bus" before
    melt ever started, and was reported as melt rendering nothing. sd-bus finds
    the user bus through `DBUS_SESSION_BUS_ADDRESS`, else `$XDG_RUNTIME_DIR/bus`.
    Only a `unix:path=` address can be checked from here; any other form is
    taken at its word.
    """
    address = env.get("DBUS_SESSION_BUS_ADDRESS", "")
    if address:
        path = next(
            (part.removeprefix("unix:path=") for part in address.split(";") if part.startswith("unix:path=")),
            None,
        )
        return path is None or Path(path.split(",")[0]).exists()
    runtime = user_runtime_dir(env)
    return runtime is not None and (runtime / "bus").exists()


#: Where logind puts a user's runtime directory when nobody names it.
USER_RUNTIME_ROOT = Path("/run/user")


def user_runtime_dir(env: dict[str, str]) -> Path | None:
    """`$XDG_RUNTIME_DIR`, else logind's `/run/user/<uid>` — or None."""
    if env.get("XDG_RUNTIME_DIR"):
        return Path(env["XDG_RUNTIME_DIR"])
    return USER_RUNTIME_ROOT / str(os.getuid()) if hasattr(os, "getuid") else None


#: The names melt goes by on PATH, unambiguous ones first. Fedora's `mlt`
#: installs `melt-7` and `mlt-melt` and no `melt` at all, while its `melt`
#: package is freeze — so a bare `melt` searched first finds a compression tool
#: on exactly the box that also has the real one. HISTORY.md § A stranger's
#: install, on a clean Fedora.
MELT_NAMES = ("mlt-melt", "melt-7", "melt")

#: melt's own `-version` line. The banner is `basename(argv[0])`, so Fedora's
#: prints `mlt-melt 7.40.0` or `melt-7 7.40.0` — and on Windows the basename
#: keeps its extension, in whatever case the caller spelled it: Shotcut
#: 26.8.1's `melt.exe` carries the format `%s 7.41.0`, and the first
#: windows-demo run's doctor refused it for want of a banner. Stated once:
#: `doctor` reads a melt's version off it, and `melt_command` holds every
#: candidate it finds to it.
MELT_BANNER = re.compile(r"^(?:mlt-)?melt(?:-\d+)?(?i:\.exe)? (\d\S*)")

#: Seconds to wait on a candidate's `-version`. A PATH or bundle binary, never
#: the flatpak, so there is no cold start to wait out.
MELT_PROBE_TIMEOUT = 30

#: `(path, mtime_ns)` → the banner's version, or None for a binary that is not
#: melt. `melt_command` runs several times per render, and each probe is a
#: process start.
_MELT_VERDICTS: dict[tuple[str, int], str | None] = {}


def command_override(value: str) -> list[str]:
    """A `PROOFCUT_MELT`/`PROOFCUT_MAGICK` value as an argv prefix.

    Both are commands, not paths — the flatpak form is four words — so they are
    split. POSIX `shlex.split` eats backslashes, which turned
    `C:\\Users\\runner\\melt.exe` into `C:Usersrunnermelt.exe`
    (HISTORY.md § The Windows run that answered). So on Windows a value naming
    an existing file is one argv element whole, and anything else splits with
    `posix=False`, which keeps backslashes and keeps quotes, and has one layer
    of surrounding quotes stripped off each word.
    """
    if sys.platform != "win32":
        return shlex.split(value)
    whole = value.strip()
    if len(whole) >= 2 and whole[0] == whole[-1] == '"':
        whole = whole[1:-1]
    if Path(whole).is_file():
        return [whole]
    return [
        word[1:-1] if len(word) >= 2 and word[0] == word[-1] and word[0] in "\"'" else word
        for word in shlex.split(value, posix=False)
    ]


def melt_version(path: str) -> str | None:
    """The version a binary's `-version` banner names, or None if it is not melt.

    doctor's rule, applied to every candidate `melt_command` finds: **judged by
    the banner, never the exit code**. A bare `melt` on PATH is not evidence of
    MLT — Fedora's is freeze, and the Windows runner's is WiX's `melt.EXE`, an
    MSI tool that answered a render with `error MELT0240` (HISTORY.md § The
    Windows run that answered). One that cannot be started at all is not melt
    either. Cached per `(path, mtime)`, so a binary replaced in place is asked
    again.
    """
    try:
        key = (path, Path(path).stat().st_mtime_ns)
    except OSError:
        return None
    if key in _MELT_VERDICTS:
        return _MELT_VERDICTS[key]
    try:
        done = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=MELT_PROBE_TIMEOUT,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        output = (done.stdout or "") + (done.stderr or "")
    except (OSError, subprocess.SubprocessError):
        output = ""
    match = next((m for line in output.splitlines() if (m := MELT_BANNER.match(line))), None)
    _MELT_VERDICTS[key] = match.group(1) if match else None
    return _MELT_VERDICTS[key]


def melt_command() -> list[str]:
    """The argv prefix that runs `melt`, however it is installed here.

    Returns a list rather than a path because the flatpak form is four words
    and there is no binary to point at. The order is `PROOFCUT_MELT`, then
    what `proofcut setup` installed (`deps.melt`), then PATH, then the
    desktop bundles and the flatpak. Every PATH and bundle candidate must
    print melt's banner (`melt_version`) or it is skipped, and a refusal after
    skipping one names it. `PROOFCUT_MELT` is taken at its word — it may be a
    wrapper — and so is the flatpak, whose `flatpak info` already names MLT's
    host application.
    """
    override = os.environ.get("PROOFCUT_MELT")
    if override:
        return command_override(override)
    impostors: list[str] = []
    # What `proofcut setup` installed goes ahead of PATH: it installs a melt
    # only when the PATH one failed doctor (deps.py).
    installed = deps.melt()
    candidates = [str(installed)] if installed.is_file() else []
    candidates += [found for name in MELT_NAMES if (found := shutil.which(name))]
    candidates += [str(bundle) for bundle in melt_bundles() if bundle.is_file()]
    for candidate in dict.fromkeys(candidates):
        if melt_version(candidate) is not None:
            return [candidate]
        impostors.append(candidate)
    if shutil.which("flatpak"):
        installed = subprocess.run(
            ["flatpak", "info", KDENLIVE_FLATPAK], capture_output=True, text=True, check=False
        )
        if installed.returncode == 0:
            return ["flatpak", "run", "--command=melt", KDENLIVE_FLATPAK]
    where, install = melt_search()
    skipped = (
        f"Skipped {', '.join(impostors)}: "
        f"{'it prints' if len(impostors) == 1 else 'each prints'} no `melt <version>` banner, so "
        f"{'it is' if len(impostors) == 1 else 'they are'} not MLT's melt (WiX's melt.exe and "
        "Fedora's freeze share the name). "
        if impostors
        else ""
    )
    raise PictureError(
        f"melt not found. Looked at $PROOFCUT_MELT, then {deps.melt()} (`proofcut setup`'s), "
        f"then PATH ({', '.join(MELT_NAMES)}), then {where}. "
        f"{skipped}{install} Without it the timeline's own frame total is still reported; "
        "only the comparison against melt needs melt."
    )


#: Where Qt draws through the OS's own window system rather than a display
#: server: `cocoa` on macOS, `windows` on Windows. There is no socket to find
#: and nothing for the render gate to check, and `/run/user/<uid>` is not a
#: path either OS has — Windows has no `os.getuid` at all, so applying the
#: Linux dance there raised before any render or `proofcut doctor` ran. Every
#: other platform Qt runs on reaches a display through X11 or Wayland, which is
#: what `display_env` is for. **That Qt draws a `qimage` producer under either
#: is unmeasured** — docs/plans/PORTABILITY.md step 4 is where it gets settled.
NATIVE_QT_PLATFORMS = {"darwin": "cocoa", "win32": "windows"}


def native_qt_platform() -> str | None:
    """Qt's own platform plugin on this OS, or None where Qt needs a display server.

    Read at call time, never at import, so a test can stand in for either OS.
    """
    return NATIVE_QT_PLATFORMS.get(sys.platform)


def display_env() -> dict[str, str]:
    """Give MLT's Qt module a display, or it silently drops what it cannot load.

    Without `WAYLAND_DISPLAY` or `DISPLAY`, every `qimage` producer and the
    `qtblend` transition refuse to load and the render still exits 0 — a card
    track just vanishes (HISTORY.md § 4). Reading a document is less exposed
    than rendering one, but a project melt could not fully load is a project
    whose reported length is not the length it would render, so the display
    goes in either way.

    **`WAYLAND_DISPLAY` alone is not a display**: it is a socket *name*, and Qt
    resolves it under `XDG_RUNTIME_DIR`. Both have to travel together, which
    they do not when proofcut is launched from a scrubbed environment — the MCP
    stdio transport passes a handful of variables (HOME, PATH, USER, …) and
    `XDG_RUNTIME_DIR` is not among them. Measured 2026-08-08: naming the socket
    without the directory gives `Failed to create wl_display`, Qt then finds no
    platform plugin at all, and **melt aborts printing nothing** — which the
    empty-output guards read as a project that could not be loaded. So the
    directory this searched is exported alongside the socket it found.

    On macOS and Windows none of that applies (`NATIVE_QT_PLATFORMS`) and the
    environment is returned as it is.
    """
    env = dict(os.environ)
    if native_qt_platform():
        return env
    if env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        return env
    # Past the check above, so a named X display asks nothing of `os.getuid`,
    # which Windows has not got — a test standing in for Linux there is the case.
    runtime = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    if env.get("WAYLAND_DISPLAY"):
        if (runtime / env["WAYLAND_DISPLAY"]).exists():
            env["XDG_RUNTIME_DIR"] = str(runtime)
        return env
    for socket in sorted(runtime.glob("wayland-*")):
        if socket.suffix != ".lock":
            env["WAYLAND_DISPLAY"] = socket.name
            env["XDG_RUNTIME_DIR"] = str(runtime)
            return env
    if Path("/tmp/.X11-unix/X0").exists():
        env["DISPLAY"] = ":0"
    return env


#: Qt platform plugins that draw with no display server behind them. A render
#: under one of these needs no socket at all: measured 2026-08-23 on this box
#: (seat at the greeter, no wayland-* socket, no Xvfb anywhere), one frame of a
#: red PNG through a `qimage` producer came back (229, 0, 1) under both — the
#: same measurement `goodsometimes/scripts/render.py::qt_draws` makes. It is
#: the route for a background job with no session, which is every unattended
#: render; a host-socket check alone refused renders that would have worked.
HEADLESS_QT_PLATFORMS = frozenset({"offscreen", "minimal"})


def qt_is_headless(env: dict[str, str] | None = None) -> bool:
    """Is Qt told to draw without a display? (`QT_QPA_PLATFORM=offscreen`)."""
    value = (env if env is not None else os.environ).get("QT_QPA_PLATFORM", "")
    return value.split(":", 1)[0].strip().lower() in HEADLESS_QT_PLATFORMS


#: One frame, 64x36: a red colour producer squeezed into the left half by a
#: `qtblend` filter. Where the Qt module draws, the right half comes out black;
#: where it refused to load, the filter is dropped and the frame is red edge to
#: edge — with melt exiting 0 either way.
_QT_PROBE = """<?xml version="1.0" encoding="utf-8"?>
<mlt>
  <profile description="proofcut-qt-probe" width="64" height="36" progressive="1"
    sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="16"
    display_aspect_den="9" frame_rate_num="25" frame_rate_den="1" colorspace="709"/>
  <producer id="red" in="0" out="0">
    <property name="mlt_service">color</property>
    <property name="resource">#ffff0000</property>
    <filter>
      <property name="mlt_service">qtblend</property>
      <property name="rect">0 0 32 36 1</property>
    </filter>
  </producer>
</mlt>
"""

#: What `flatpak run` prints when it loses a startup race with other instances
#: of the same app, before melt itself runs: nothing is read or written, and
#: the call looks exactly like a project melt could not load. Measured
#: 2026-09-17 at 20 concurrent renders (3 rounds of 5 hit it) and never with
#: 120 concurrent `-version` calls, so it needs another instance doing real
#: work. docs/plans/SUITE-SPEED.md § Steps 1 and 2, measured.
FLATPAK_LAUNCH_RACE = "has invalid merge-dirs"

#: How many times a launch that lost that race is tried again, and the pause
#: before each retry, jittered so that launches racing together separate.
LAUNCH_RETRIES = 3
LAUNCH_RETRY_PAUSE = 0.3


def launch_raced(completed: subprocess.CompletedProcess[str]) -> bool:
    """Did this melt call die in flatpak's launcher rather than in melt?"""
    return FLATPAK_LAUNCH_RACE in (completed.stderr or "")


def _retrying_launch(
    call: Callable[[], subprocess.CompletedProcess[str]],
) -> subprocess.CompletedProcess[str]:
    """Run `call`, and again if flatpak's launcher lost its startup race.

    Safe only because the race fails before melt starts; a failure melt
    itself reports is returned as it came.
    """
    completed = call()
    for _ in range(LAUNCH_RETRIES):
        if not launch_raced(completed):
            break
        time.sleep(LAUNCH_RETRY_PAUSE * (1 + random.random()))
        completed = call()
    return completed


_qt_draws_cache: dict[tuple[str, ...], bool] = {}


def qt_draws(env: dict[str, str]) -> bool | None:
    """Does this melt's Qt module actually draw in this environment?

    `QT_QPA_PLATFORM=offscreen` is a request, and whether MLT honours it is up
    to the build. The Kdenlive flatpak's does (measured 2026-08-23). Ubuntu
    24.04's MLT 7.22 does not: its Qt module prints "requires a X11
    environment", drops every `qtblend`, and a 9:16 render came out letterboxed
    with every frame counted and agreeing. `xvfb-run -a` fixed that one. So
    this renders `_QT_PROBE` and reads two pixels back.

    True or False is a measurement and is cached per melt and platform. None
    means the probe itself could not conclude (no melt, no ffmpeg, nothing
    written). That is never grounds for a refusal: the render's own checks
    still speak for it.
    """
    try:
        melt = melt_command()
    except PictureError:
        return None
    key = (*melt, env.get("QT_QPA_PLATFORM", ""))
    if key in _qt_draws_cache:
        return _qt_draws_cache[key]
    work = scratch("render-")
    try:
        document = work / "qt-probe.mlt"
        frame = work / "qt-probe.png"
        document.write_text(_QT_PROBE, encoding="utf-8")
        subprocess.run(
            [*melt, str(document), "-consumer", f"avformat:{frame}", "vcodec=png"],
            capture_output=True, env=env, timeout=60, check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        if not frame.exists() or frame.stat().st_size == 0:
            return None
        pixels = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(frame), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=30, check=False,
        ).stdout  # fmt: skip
        if len(pixels) != 64 * 36 * 3:
            return None
        left, right = pixels[(18 * 64 + 8) * 3], pixels[(18 * 64 + 56) * 3]
        if left < 128:
            return None  # not even the red came out; the probe says nothing
        draws = right < 64
    except Exception:  # noqa: BLE001 — an inconclusive probe is never a refusal
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _qt_draws_cache[key] = draws
    return draws


def parse_melt_xml(document: str) -> int:
    """The frame count melt says it will render, out of `-consumer xml` output.

    melt flattens the whole project into one wrapping producer whose `length`
    property is that count. `length` and not the outer tractor's `out`, because
    MLT's `out` is frame-inclusive and the two differ by one — verified by
    rendering rather than reasoned about: a project reporting `length` 361
    produced exactly 361 frames.
    """
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise PictureError(f"melt did not return a readable MLT document: {exc}") from exc

    for producer in root.iter("producer"):
        properties = {p.get("name"): (p.text or "").strip() for p in producer.findall("property")}
        if properties.get("mlt_service") == "xml" and properties.get("length", "").isdigit():
            return int(properties["length"])

    # No wrapping producer — read the outermost tractor instead, converting
    # MLT's inclusive `out` to a count so both paths return the same thing.
    outs = [t.get("out", "") for t in root.iter("tractor")]
    for out in reversed(outs):
        if out.isdigit():
            return int(out) + 1

    raise PictureError(
        "melt returned an MLT document with no frame count in it — neither a "
        "wrapping producer with a `length` property nor a tractor with a "
        "frame-numbered `out`."
    )


#: How much of a misbehaving binary's output a refusal quotes.
_HEAD_CHARS = 300


def _head(text: str | None) -> str:
    """The start of `text`, repr'd so a BOM or a stray control byte is visible."""
    text = (text or "").strip()
    if not text:
        return ""
    return repr(text[:_HEAD_CHARS]) + (" …" if len(text) > _HEAD_CHARS else "")


def project_frames(project: Path | str) -> int:
    """Ask melt how many frames it would render `project` to.

    `-consumer xml` resolves the document and prints what it would use without
    encoding anything, which is what makes this check cheap enough to run
    before committing to a render.
    """
    path = Path(project).expanduser()
    if not path.exists():
        raise PictureError(f"no such NLE project: {path}")

    command = [*melt_command(), str(path), "-consumer", "xml"]
    try:
        # `check=False` on purpose: melt exits 0 having failed to load a project
        # (see `_TMP_HINT`), so the return code proves nothing either way and the
        # output is the only evidence there is.
        completed = _retrying_launch(
            lambda: subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=display_env(),
                timeout=MELT_TIMEOUT,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        )
    except FileNotFoundError as exc:
        raise PictureError(f"could not run melt: {' '.join(command)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PictureError(
            f"melt did not answer within {MELT_TIMEOUT}s for {path}. Usually the "
            "project references media it cannot reach — check that every "
            "`resource` path in it resolves."
        ) from exc

    if not completed.stdout.strip():
        detail = (completed.stderr or "").strip()
        raise PictureError(
            f"melt printed no timeline for {path}.\n{detail}"
            f"{_TMP_HINT if _invisible_to_flatpak(path, command) else ''}"
        )
    try:
        return parse_melt_xml(completed.stdout)
    except PictureError as exc:
        # Something answered to `melt` and printed a timeline that is not one.
        # The first Windows CI run hit exactly this six times and the log said
        # only "syntax error: line 1, column 0" — never which binary answered
        # or what it said, which were the two things needed to fix it.
        said = _head(completed.stdout) or _head(completed.stderr)
        raise PictureError(
            f"{exc}\nran: {' '.join(command)}\nit printed: {said}"
        ) from exc


_TMP_HINT = (
    "\n\nThis project is under /tmp and melt is running from the flatpak, "
    "which cannot see the host's /tmp — `filesystems=host` does not cover it "
    "(HISTORY.md § 4). Note melt exits 0 while failing to load, so the only "
    "evidence is the empty output above. Export somewhere under $HOME instead."
)


def _invisible_to_flatpak(path: Path, command: list[str]) -> bool:
    """Is this the flatpak reading a path its sandbox does not have?"""
    return command[:1] == ["flatpak"] and path.resolve().is_relative_to(Path("/tmp"))


# -- rendering a project: melt, its three traps, and the measurement -----


#: Where a render is staged before it is copied where it was asked for. Under
#: `$HOME` because the flatpak's `/tmp` is not the host's, and staged at all
#: because a render that dies halfway leaves a file behind — at the
#: destination it would be a half-muxed file that looks finished.
RENDER_SCRATCH = Path.home() / "proofcut-render"

#: **The consumer gets the codec and nothing else.** Restating the project
#: profile on it is what unbounded memory growth correlated with: 2167 MB peak
#: with `vcodec crf preset acodec` alone, still climbing past 6873 MB once
#: `ab`, `width`, `height` and `progressive` were added, on the way to the
#: 14.6 GB that froze the machine. No single one of the four reproduces it
#: alone, so the rule is the whole list rather than a suspect (HISTORY.md § 4).
RENDER_ARGS = ("vcodec=libx264", "crf=18", "preset=medium", "acodec=aac")

#: The backstop for whatever the next surprise is: the render runs inside a
#: systemd scope that gets OOM-killed at this rather than swapping the desktop
#: out. Skipped — with a note in the reply, never silently — where
#: `systemd-run` is not available.
RENDER_MAX_MEMORY = "6G"
RENDER_MAX_SWAP = "1G"

#: How long to wait on an encode. Generous: this is a whole video through
#: libx264 at `preset=medium`, not a document being read.
RENDER_TIMEOUT = 4 * 3600

#: How far a render's duration may sit from the timeline's before it counts as
#: a disagreement. Only ever consulted for a render with **no frames to
#: count** — an audio-only one — where a container duration is all there is.
#: A frame of AAC is 1024 samples (~23 ms) and the muxer pads to it, so a
#: tolerance below that would fail correct renders.
RENDER_DURATION_TOLERANCE = 0.15


#: How long a staging directory a render left behind is kept before the next
#: render sweeps it. A failed render's directory survives on purpose — the
#: document melt was given is the evidence for what it did with it — but that
#: retention had no expiry, and 235 of them accumulated over ten days. Two
#: weeks is well past any live investigation.
SCRATCH_RETENTION_DAYS = 14

#: What `scratch()` is allowed to sweep: exactly the names it makes itself, a
#: known prefix followed by `mkdtemp`'s eight characters. Anything a person
#: named — `kf-manual`, `kf-mini` — fails this and is never touched, which is
#: the whole guard: the sweep runs unattended inside somebody else's render.
_SCRATCH_NAME = re.compile(r"^(?:render|timeline)-[a-z0-9_]{8}$")


def sweep_scratch(*, retention_days: int = SCRATCH_RETENTION_DAYS) -> list[Path]:
    """Drop staging directories older than `retention_days`. Never raises.

    A sweep is a side effect of doing something else, so a failure here must
    not fail the render that triggered it — every step is guarded and the
    return value is what actually went, not what was chosen.
    """
    if not RENDER_SCRATCH.is_dir():
        return []
    cutoff = time.time() - retention_days * 86400
    swept: list[Path] = []
    try:
        entries = sorted(RENDER_SCRATCH.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not _SCRATCH_NAME.match(entry.name):
            continue
        try:
            #: `is_dir()` follows symlinks, and a link named like a staging
            #: directory would be read through to whatever it points at.
            #: `rmtree` refuses one anyway, but silently — say it here instead.
            if entry.is_symlink() or not entry.is_dir():
                continue
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            swept.append(entry)
    return swept


def scratch(prefix: str = "render-") -> Path:
    """A fresh working directory somewhere melt can actually read.

    Under `$HOME`, not `/tmp`: the flatpak cannot see the host's `/tmp` and
    exits 0 having read nothing (CLAUDE.md), so `tempfile.mkdtemp()`'s default
    would produce a project melt silently ignores.

    Making one is also when old ones go: `sweep_scratch` bounds a retention
    that otherwise had no expiry at all.
    """
    RENDER_SCRATCH.mkdir(parents=True, exist_ok=True)
    sweep_scratch()
    return Path(tempfile.mkdtemp(prefix=prefix, dir=RENDER_SCRATCH))


def render_problems(
    measured: dict[str, Any],
    *,
    expect_frames: int | None = None,
    expect_resolution: tuple[int, int] | None = None,
    expect_duration: float | None = None,
    tolerance: float = RENDER_DURATION_TOLERANCE,
) -> list[str]:
    """Every way this render disagrees with the timeline it was made from.

    Split from `render()` for the reason `parse_melt_xml` is split from
    `project_frames`: the interesting cases are a dict in and a list out, and
    stating them exactly should not cost an encode.

    The frame count is the check that matters and the resolution is the one
    that catches a degraded render. **Duration is a fallback, applied only
    when there are no frames to count** — an mp4's duration is the longest of
    its streams and an audio stream routinely outruns the video by a frame of
    AAC padding, so comparing it on a video render would fail correct ones.
    """
    problems: list[str] = []
    width, height = measured.get("width"), measured.get("height")
    if expect_resolution and measured.get("has_video") and (width, height) != expect_resolution:
        problems.append(
            f"rendered {width}x{height} where the timeline's profile declares "
            f"{expect_resolution[0]}x{expect_resolution[1]}. This is the shape a "
            "silently degraded render has (auto-editor's multi-source downgrade "
            "is 720x576, with exit 0) — the pixels, not the status, are what say so"
        )

    frames = measured.get("frames")
    if expect_frames is not None and frames is not None and frames != expect_frames:
        problems.append(
            f"rendered {frames} frames where the timeline is {expect_frames} "
            f"({frames - expect_frames:+d}). melt renders to the longest declared "
            "length in the document rather than to the playlist, so a difference "
            "here is a length that disagreed with the edit and padded or truncated it"
        )

    duration = measured.get("duration")
    if (
        expect_duration is not None
        and frames is None
        and duration is not None
        and abs(duration - expect_duration) > tolerance
    ):
        problems.append(
            f"rendered {duration:.3f}s where the timeline is {expect_duration:.3f}s. "
            "This render has no video stream, so its duration is the only length "
            f"there is to compare (tolerance {tolerance}s)"
        )
    return problems


#: melt's `-progress` line, redrawn in place with a carriage return.
_MELT_FRAME = re.compile(r"Current Frame:\s*(\d+)")


def _render_reporting(
    command: list[str],
    env: dict[str, str],
    timeout: int,
    frames: int | None,
    name: str,
) -> subprocess.CompletedProcess[str]:
    """`render`'s melt call with its frame counter reported as progress.

    Same stdin rule as the plain call. The counter is the frame index melt is
    on, so the last frame reads `frames - 1`; the render is only reported done
    by `render` itself, after the file has been checked.
    """
    message = f"rendering {name}"

    def on_line(line: str) -> None:
        match = _MELT_FRAME.search(line)
        if match:
            at = int(match.group(1))
            progress.report(min(at, frames - 1) if frames else at, frames, message)

    progress.report(0, frames, message)
    return progress.run(
        command, on_stderr=on_line, env=env, timeout=timeout, stdin=subprocess.DEVNULL
    )


def render(
    project: Path | str,
    output: Path | str,
    *,
    expect_frames: int | None = None,
    expect_resolution: tuple[int, int] | None = None,
    expect_duration: float | None = None,
    max_memory: str | None = RENDER_MAX_MEMORY,
    timeout: int = RENDER_TIMEOUT,
    consumer_args: tuple[str, ...] = RENDER_ARGS,
) -> dict[str, Any]:
    """Render an MLT project with `melt`, and check what actually came out.

    melt is the renderer for a multi-source timeline because auto-editor gates
    one to 720x576 while exiting 0 (CLAUDE.md). Its own three traps all produce
    output rather than an error, so all three are handled here rather than
    hoped past (HISTORY.md § 4):

    * **the codec and nothing else** goes on the consumer — `RENDER_ARGS` by
      default, or `consumer_args` — but never anything past those same four
      keys (`vcodec`/`crf`/`preset`/`acodec`): adding `ab`/`width`/`height`/
      `progressive` on top of them is what correlated with the unbounded
      memory growth this comment cites below, and no one has since isolated
      which of those additions was the cause. A caller may vary the *values*
      of the four measured-safe keys (`ops.EXPORT_PRESETS`); widening the key
      set itself needs that isolation work redone first;
    * **Qt needs a display**, or every `qimage` producer and the `qtblend`
      transition refuse to load, the picture lane vanishes and the render still
      exits 0. Missing one is a refusal here, not a warning, because the
      resulting file looks like a success;
    * **the flatpak's `/tmp` is not the host's**, so the staging directory is
      under `$HOME` (`scratch()`), and the project has to be somewhere melt can
      read too.

    Plus the memory cap `goodsometimes/scripts/render.py` added after a
    hand-run render filled 16 GB of swap and froze the machine.

    **The exit code is trusted for nothing.** The staged file is probed and its
    resolution, frame count and duration compared against what the timeline
    promised; only a render that agrees is copied to `output`. One that does
    not is left in its scratch directory and named in the error, because the
    evidence is the file.
    """
    path = Path(project).expanduser()
    if not path.exists():
        raise PictureError(f"no such NLE project to render: {path}")
    destination = Path(output).expanduser()

    env = display_env()
    # The gate is a Linux one: it catches melt aborting with a socket name and
    # no runtime dir, which cannot happen where Qt draws natively.
    if not native_qt_platform() and not (
        env.get("WAYLAND_DISPLAY") or env.get("DISPLAY") or qt_is_headless(env)
    ):
        raise PictureError(
            "no display for MLT's Qt module to open, so this render would drop "
            "every `qimage` producer and the `qtblend` transition — the picture "
            "lane would be missing and melt would still exit 0 (HISTORY.md § 4). "
            "Set WAYLAND_DISPLAY or DISPLAY, run this where a session exists, or "
            "set QT_QPA_PLATFORM=offscreen — measured 2026-08-23 to draw a "
            "`qimage` producer with no session at all."
        )
    if (
        not native_qt_platform()
        and not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))
        and qt_draws(env) is False
    ):
        raise PictureError(
            "QT_QPA_PLATFORM is set, but this melt's Qt module does not draw under "
            "it: a one-frame probe came back with its `qtblend` filter dropped. A "
            "render here would lose every card, crop and composite and still exit "
            "0. Some MLT builds want a real X display (Ubuntu 24.04's MLT 7.22 "
            "does), so run the render under a virtual one: `xvfb-run -a proofcut …` "
            "(`apt install xvfb`, `dnf install xorg-x11-server-Xvfb`)."
        )

    work = scratch("render-")
    staged = work / (destination.name or "render.mp4")
    melt = melt_command()
    # `-progress` is a melt option, not a consumer property, so the measured-safe
    # consumer key set above is untouched; asked only when someone listens.
    reporting = progress.active()
    command = [
        *melt,
        *(["-progress"] if reporting else []),
        str(path),
        "-consumer",
        f"avformat:{staged}",
        *consumer_args,
    ]
    has_systemd_run = shutil.which("systemd-run") is not None
    capped = bool(max_memory) and has_systemd_run and user_bus(env)
    if capped:
        # `user_bus` falls back to logind's directory; systemd-run does not, and
        # an MCP stdio server with no desktop is handed no XDG_RUNTIME_DIR.
        runtime = user_runtime_dir(env)
        if runtime is not None and not env.get("DBUS_SESSION_BUS_ADDRESS"):
            env = {**env, "XDG_RUNTIME_DIR": str(runtime)}
        command = [
            "systemd-run", "--user", "--scope", "--quiet",
            "-p", f"MemoryMax={max_memory}",
            "-p", f"MemorySwapMax={RENDER_MAX_SWAP}",
            "nice", "-n", "10",
            *command,
        ]  # fmt: skip

    # stdin is DEVNULL because melt with its output piped and a console on its
    # stdin writes the whole file and then never exits: on a Windows 11 laptop
    # this exact call hung past 60 s and exited in 1.3 s with stdin=DEVNULL,
    # while the console left on stdin with output to the console, or to NUL,
    # exited too. CI's runner has no console, so it never showed there.
    # HISTORY.md § The render that never exited.
    try:
        if progress.streamed():
            completed = _retrying_launch(
                lambda: _render_reporting(command, env, timeout, expect_frames, destination.name)
            )
        else:
            completed = _retrying_launch(
                lambda: subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=timeout,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            )
    except FileNotFoundError as exc:
        raise PictureError(f"could not run melt: {' '.join(command)}") from exc
    except progress.Cancelled:
        # A stopped render is nothing to inspect, unlike a failed one.
        shutil.rmtree(work, ignore_errors=True)
        raise
    except subprocess.TimeoutExpired as exc:
        raise PictureError(
            f"melt did not finish rendering {path} within {timeout}s. The partial "
            f"render is at {staged}."
        ) from exc

    if not staged.exists() or staged.stat().st_size == 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise PictureError(
            f"melt rendered nothing for {path} (exit {completed.returncode}, which "
            f"proves nothing either way — the missing file is the finding).\n{detail}"
            f"{_TMP_HINT if _invisible_to_flatpak(path, melt) else ''}"
        )

    info = media.probe(staged)
    counts = media.count_frames(staged)
    measured: dict[str, Any] = {
        "width": info.width,
        "height": info.height,
        "frames": counts["frames"],
        "container_frames": counts["container_frames"],
        "duration": counts["duration"],
        "has_video": info.has_video,
        "has_audio": info.has_audio,
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
    }
    problems = render_problems(
        measured,
        expect_frames=expect_frames,
        expect_resolution=expect_resolution,
        expect_duration=expect_duration,
    )
    if problems:
        raise PictureError(
            f"the render disagrees with the timeline it was made from, so it has "
            f"not been copied to {destination}. It is at {staged}, kept so the "
            "numbers can be checked against it:\n- " + "\n- ".join(problems)
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staged, destination)
    shutil.rmtree(work, ignore_errors=True)
    if reporting and measured["frames"]:
        # Done means checked and copied into place, never melt's last frame
        # index — which is one short of the total by construction.
        progress.report(measured["frames"], measured["frames"], f"rendered {destination.name}")

    notes: list[str] = []
    if measured["frames"] is None:
        notes.append(
            "this render has no video stream, so there were no frames to count "
            "and its duration was compared instead"
        )
    if max_memory and not capped:
        if not sys.platform.startswith("linux"):
            notes.append(
                f"the {max_memory} memory cap is a systemd scope and Linux-only, so "
                "this render ran without one"
            )
        elif has_systemd_run:
            notes.append(
                f"systemd-run is here but there is no user session bus for it to reach "
                f"(a container, or a login without one), so the render ran without the "
                f"{max_memory} memory cap"
            )
        else:
            notes.append(
                f"systemd-run is not available here, so the render ran without the "
                f"{max_memory} memory cap"
            )
    return {
        "output": str(destination),
        "project": str(path),
        "exit_code": completed.returncode,
        "memory_cap": max_memory if capped else None,
        "consumer": list(consumer_args),
        **measured,
        "expected_frames": expect_frames,
        "expected_resolution": list(expect_resolution) if expect_resolution else None,
        "agrees": True,
        "notes": notes,
    }


# -- reading a render directly: black runs and spot-checked frames -------


def parse_blackdetect(stderr: str) -> list[dict[str, float]]:
    """Every black_start/black_end/black_duration triple ffmpeg wrote to stderr.

    Split from `blackdetect()` for the same reason `parse_melt_xml` is split
    from `project_frames`: a parser is testable on a captured string, without
    a subprocess.
    """
    return [
        {"start": float(m["start"]), "end": float(m["end"]), "duration": float(m["duration"])}
        for m in _BLACK_RE.finditer(stderr)
    ]


def blackdetect(
    target: Path | str, *, pix_th: float = 0.10, min_duration: float = 0.1
) -> list[dict[str, float]]:
    """Scan a render for black stretches with ffmpeg's `blackdetect` filter.

    Decodes the whole file — there is no cheap document-only path here the
    way `project_frames` has with melt, because a black run is a property of
    the pixels, not of a declared length. `min_duration` is the caller's
    responsibility to set relative to the export's frame rate; this function
    keeps a generic standalone default since it does not know that rate.
    """
    path = Path(target).expanduser()
    if not path.exists():
        raise PictureError(f"no such file to scan for black: {path}")

    command = [
        "ffmpeg",
        "-i", str(path),
        "-vf", f"blackdetect=d={min_duration}:pix_th={pix_th}",
        "-an",
        "-f", "null",
        "-",
    ]  # fmt: skip
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=BLACKDETECT_TIMEOUT, check=False
        )
    except FileNotFoundError as exc:
        raise PictureError(f"could not run ffmpeg: {' '.join(command)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PictureError(
            f"ffmpeg did not finish scanning {path} for black within "
            f"{BLACKDETECT_TIMEOUT}s"
        ) from exc

    if completed.returncode != 0:
        raise PictureError(
            f"ffmpeg exited {completed.returncode} scanning {path} for black:\n"
            f"{completed.stderr[-2000:]}"
        )
    return parse_blackdetect(completed.stderr)


_STAT_RE = re.compile(r"lavfi\.signalstats\.(?P<key>\w+)=(?P<value>-?[0-9.]+)")


def parse_signalstats(output: str) -> dict[str, float]:
    """Every lavfi.signalstats.KEY=value line from a metadata=print dump.

    Keyed generically (YAVG, YMIN, YMAX, YDIF, ...) rather than hardcoding the
    handful one investigation needed — this is the same filter that measured
    `KNOWN_TAIL_FRAME` (YAVG 16 vs ~123). Named `output`, not `stdout`: verified
    against the installed ffmpeg (8.1.2) that `metadata=print` with no `file=`
    writes through the ordinary log, i.e. to **stderr**, not stdout — a
    training-prior trap of exactly the kind CLAUDE.md warns about.
    """
    return {m["key"]: float(m["value"]) for m in _STAT_RE.finditer(output)}


def extract_frame(target: Path | str, at: float, output: Path | str) -> dict[str, float]:
    """Pull one frame from `target` at `at` seconds, and report its luma stats.

    One ffmpeg call writes the PNG and prints its own signalstats — the filter
    doesn't touch pixels, so the frame it reports on is exactly the frame
    written, with no second pass to fall out of sync with the first.
    """
    path = Path(target).expanduser()
    if not path.exists():
        raise PictureError(f"no such file to pull a frame from: {path}")

    dest = Path(output).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg", "-y",
        "-ss", f"{at:.6f}",
        "-i", str(path),
        "-frames:v", "1",
        "-an",
        "-vf", "signalstats,metadata=print",
        "-f", "image2",
        str(dest),
    ]  # fmt: skip
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=FRAME_EXTRACT_TIMEOUT, check=False
        )
    except FileNotFoundError as exc:
        raise PictureError(f"could not run ffmpeg: {' '.join(command)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PictureError(
            f"ffmpeg did not answer within {FRAME_EXTRACT_TIMEOUT}s pulling a "
            f"frame from {path} at {at:.3f}s"
        ) from exc

    if completed.returncode != 0 or not dest.exists():
        raise PictureError(
            f"ffmpeg could not pull a frame from {path} at {at:.3f}s "
            f"(exit {completed.returncode}):\n{completed.stderr[-2000:]}"
        )
    return parse_signalstats(completed.stderr)


def render_silence(output: Path | str, seconds: float) -> Path:
    """Write a WAV of digital silence, at least `seconds` long.

    There is no silence producer in MLT's own vocabulary, and this is not one
    either — a tail's audio-track entry is an ordinary avformat clip like any
    other, and this is where the file it points at comes from, rendered the
    way a card PNG is rendered rather than checked in (PLAN.md § Tail time —
    the design note). `anullsrc` over `-t` is exact only to the encoder's own
    rounding, and a tail's requested length is quantised again onto whatever
    frame rate the project exports at — two roundings that need not agree — so
    this pads a half second past what was asked rather than matching it
    exactly. The MLT `Entry` built over the file states the tail's real frame
    count itself (`src_in`/`out`); the file only has to outlast it, the same
    way a still image's `IMAGE_LENGTH_SECONDS` outlasts every shot that could
    ever hold one.
    """
    if seconds <= 0:
        raise PictureError(f"silence must be a positive number of seconds, not {seconds}")

    dest = Path(output).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={SILENCE_SAMPLE_RATE}",
        "-t", f"{seconds + 0.5:.6f}",
        "-c:a", "pcm_s16le",
        str(dest),
    ]  # fmt: skip
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=SILENCE_TIMEOUT, check=False
        )
    except FileNotFoundError as exc:
        raise PictureError(f"could not run ffmpeg: {' '.join(command)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PictureError(
            f"ffmpeg did not answer within {SILENCE_TIMEOUT}s rendering {seconds}s of silence"
        ) from exc

    if completed.returncode != 0 or not dest.exists():
        raise PictureError(
            f"ffmpeg could not render {seconds}s of silence to {dest} "
            f"(exit {completed.returncode}):\n{completed.stderr[-2000:]}"
        )
    return dest
