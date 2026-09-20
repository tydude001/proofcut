"""Probing the six external binaries proofcut depends on, and naming their traps.

Every dependency here has a documented way of failing *silently* — that is the
whole reason this module exists. `melt` prints `Failed to load` and exits 0.
PyPI's auto-editor is a stale 29.x whose multi-source render degrades to
720x576 and exits 0. libass substitutes a font nobody chose and ffmpeg exits 0.
The repo's CLAUDE.md records each of them; a newcomer has none of that, so `proofcut doctor`
probes each one and — when a probe fails — prints the named trap and the fix
rather than a bare ✗.

Two rules the probes hold to:

**Never trust an exit code where the repo has measured it lying.** melt is
probed by reading its `-version` banner out of stdout; whisper by reading its
own `usage:` line back. A subprocess that returns 0 with nothing to say is a
failure here, not a pass.

**Cost nothing.** Every probe on this box totals well under two seconds, which
is what makes doctor something to run first rather than something to be talked
into. Nothing loads a model, decodes a frame, or writes into the project — and
`report()` needs no project at all.

No proofcut state is touched, and the only proofcut imports are the resolver modules
whose answers are being reported, so doctor says exactly what the real ops
would resolve rather than a second opinion about it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from proofcut import (
    asr,
    autoeditor,
    captions,
    deps,
    describe,
    faces,
    fonts,
    graphics,
    picture,
    project,
    tts,
)

#: The auto-editor major this repo is built against. 29.x is PyPI's stale fork
#: (CLAUDE.md) and is a different program wearing the same name, so the check
#: is a floor rather than a presence test.
MIN_AUTO_EDITOR = 31

#: Seconds. Generous, because the first `flatpak run` of a session pays a
#: cold start, and because a probe that times out reads as a broken binary.
PROBE_TIMEOUT = 60.0

#: auto-editor's paid-key gate, stated the way CLAUDE.md states it: a
#: condition proofcut already designs around, not a warning to hand the user.
AUTO_EDITOR_GATE = (
    "31.x gates multi-*source* timelines behind a paid key — the render "
    "degrades to 720x576 and still exits 0. proofcut designs around it: "
    "single-source goes through auto-editor, and anything layered (b-roll, "
    "cards, music) is written as MLT and rendered through melt, which has no "
    "source-count gate. Nothing here needs the key."
)


def _by_setup(fix: str) -> str:
    """`fix`, led on Linux by the command that applies it.

    `proofcut setup` installs a missing ffmpeg, whisper, auto-editor or melt
    for this user without sudo or admin, on Linux, Windows and both Macs
    (`deps.setup_installs_here`, docs/plans/INSTALL.md), so only there is it
    the first thing to say. The by-hand route stays, since setup installs
    nothing a working system tool already covers.
    """
    if not deps.setup_installs_here():
        return fix
    return f"`proofcut setup` installs this for you, with no sudo. By hand: {fix}"


def _run(command: list[str]) -> tuple[str, str, int | None]:
    """Run `command`, returning (stdout, stderr, returncode) — never raising.

    `returncode` is `None` when the process could not be started or timed out,
    which is a different answer from a non-zero exit and is reported as one.
    """
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "", "", None
    return done.stdout or "", done.stderr or "", done.returncode


def _entry(name: str, what: str, **fields: Any) -> dict[str, Any]:
    """One dependency row, with every key present so a renderer needs no `get`."""
    row: dict[str, Any] = {
        "name": name,
        "what": what,
        "ok": False,
        "looked_for": None,
        "found": None,
        "version": None,
        "note": None,
        "why": None,
        "fix": None,
    }
    row.update(fields)
    return row


# -- required ------------------------------------------------------------


#: The encoder every proofcut render asks for — `picture.RENDER_ARGS`, the export
#: presets, the preview proxy and `scripts/make_demo.py`. Fedora's default
#: `ffmpeg-free` lacks it, and doctor called that ffmpeg ✓ while the demo's
#: first command died on it. HISTORY.md § A stranger's install, on a clean Fedora.
H264_ENCODER = "libx264"

#: The filters proofcut and its demo draw text with, each naming the library an
#: ffmpeg has to be built against to have it. `drawtext` (freetype) labels the
#: demo's own footage, so without it DEMO.md stops at its first command; `ass`
#: (libass) is every caption burn and the caption-font probe. Homebrew's plain
#: `ffmpeg` has neither, and doctor called it ✓ on the first mac-demo run while
#: `make_demo.py` died on `No such filter: 'drawtext'`. HISTORY.md § The Mac
#: test in CI.
TEXT_FILTERS = {"drawtext": "freetype", "ass": "libass"}


def _ffmpeg_entry(binary: str, what: str) -> dict[str, Any]:
    """ffmpeg or ffprobe, both of which proofcut calls by bare name on PATH."""
    found = shutil.which(binary)
    if not found:
        return _entry(
            binary,
            what,
            looked_for="PATH",
            why=f"{binary} is not on PATH",
            fix=_by_setup(
                "install ffmpeg (it ships both binaries). Every media operation "
                "in proofcut goes through them, so nothing works without this one."
            ),
        )
    out, err, _ = _run([found, "-version"])
    banner = (out or err).splitlines()
    version = None
    if banner and banner[0].startswith(f"{binary} version "):
        version = banner[0].split(" ", 2)[2].split(" ")[0]
    if version is None:
        return _entry(
            binary,
            what,
            looked_for="PATH",
            found=found,
            why=f"{found} ran but printed no version banner",
            fix=(
                f"check that {found} is really ffmpeg and not a wrapper — "
                "`ffmpeg -version` should print `ffmpeg version …` on its first line"
            ),
        )
    if binary == "ffmpeg":
        encoders, _, _ = _run([found, "-hide_banner", "-encoders"])
        # An empty listing is a probe that did not answer, not an encoder that is
        # missing, so only a listing without it refuses.
        if encoders.strip() and not re.search(rf"\s{H264_ENCODER}\s", encoders):
            return _entry(
                binary,
                what,
                looked_for="PATH",
                found=found,
                version=version,
                why=(
                    f"this ffmpeg has no {H264_ENCODER} encoder, and every render, "
                    "preview proxy and the demo's own footage encode with it — "
                    "each would stop at `Unknown encoder`"
                ),
                fix=_by_setup(
                    "install an ffmpeg built with libx264. Fedora's default "
                    "`ffmpeg-free` is built without it: enable RPM Fusion, then "
                    "`dnf swap ffmpeg-free ffmpeg --allowerasing`. That swap replaces "
                    "the libraries MLT renders through as well."
                ),
            )
        filters, _, _ = _run([found, "-hide_banner", "-filters"])
        # The encoder rule, for a listing: output that is not one (no `Filters:`
        # header) is a probe that did not answer, never a filter that is missing.
        missing = (
            [name for name in TEXT_FILTERS if not re.search(rf"^[ \t]*\S+[ \t]+{name}[ \t]", filters, re.MULTILINE)]
            if filters.lstrip().startswith("Filters:")
            else []
        )
        if missing:
            libraries = " and ".join(TEXT_FILTERS[name] for name in missing)
            return _entry(
                binary,
                what,
                looked_for="PATH",
                found=found,
                version=version,
                why=(
                    f"this ffmpeg has no {' or '.join(missing)} filter — it was built "
                    f"without {libraries}. The demo's footage is labelled with "
                    "drawtext and every caption burn goes through libass, so each "
                    "would stop at `No such filter`"
                ),
                fix=_by_setup(
                    "install an ffmpeg built with freetype and libass. On a Mac, "
                    "Homebrew's `ffmpeg` has neither: `brew install ffmpeg-full`, then "
                    "put it first on PATH, since it is keg-only and a plain `ffmpeg` "
                    "(auto-editor installs one) otherwise answers first — "
                    '`export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"` in your '
                    "shell profile. Homebrew no longer installs on an Intel Mac; "
                    "there, evermeet.cx's ffmpeg has both."
                ),
            )
    return _entry(binary, what, ok=True, looked_for="PATH", found=found, version=version)


def _whisper_entry() -> dict[str, Any]:
    """whisper, probed by reading its own usage line back.

    It is deliberately *run*: the failure this catches is an interpreter on
    PATH whose venv has lost torch, which resolves fine and dies minutes into
    a transcription. `--help` imports the package and costs about a second.
    """
    looked_for = f"$PROOFCUT_WHISPER ({os.environ.get('PROOFCUT_WHISPER') or 'unset'}), then PATH"
    try:
        binary = asr.whisper_binary()
    except asr.ASRError as exc:
        return _entry(
            "whisper",
            "transcription, and reading a render back to check it",
            looked_for=looked_for,
            why=str(exc),
            fix=_by_setup(
                "install openai-whisper (`uv tool install --python 3.12 "
                "openai-whisper`, or into any venv) and put its `whisper` on PATH, "
                "or point PROOFCUT_WHISPER at the binary. proofcut never imports it — "
                "it is a subprocess, so it does not have to live in proofcut's own "
                "venv. 3.12 because torch builds for Intel Macs stop there; on an "
                "Intel Mac also add `--with 'numpy<2' --no-build-package numba "
                "--no-build-package llvmlite`, since that last torch cannot read a "
                "numpy 2 array and the newest numba has no Intel build. With no "
                "NVIDIA GPU, add `--torch-backend cpu`: the default pulls CUDA "
                "torch, 5.5 GB against 1.9 GB, for a card that is not there."
            ),
        )
    out, err, code = _run([str(binary), "--help"])
    text = out + err
    if "usage: whisper" not in text:
        return _entry(
            "whisper",
            "transcription, and reading a render back to check it",
            looked_for=looked_for,
            found=str(binary),
            why=(
                f"{binary} exited {code} without printing its usage line — it is "
                "on disk but cannot start"
                + (f": {text.strip().splitlines()[-1]}" if text.strip() else "")
            ),
            fix=(
                "that venv has lost a dependency (usually torch). Reinstall "
                "openai-whisper into it, or point PROOFCUT_WHISPER at a venv that works."
            ),
        )
    note = None
    if "--word_timestamps" not in text:
        note = (
            "this build does not advertise --word_timestamps, which is the "
            "only thing proofcut asks whisper for — every cut is addressed by "
            "word index. Check that it is openai-whisper and not a lookalike."
        )
    return _entry(
        "whisper",
        "transcription, and reading a render back to check it",
        ok=True,
        looked_for=looked_for,
        found=str(binary),
        note=note,
    )


def _auto_editor_entry() -> dict[str, Any]:
    """auto-editor, checked for presence *and* for a major of at least 31."""
    looked_for = (
        f"$PROOFCUT_AUTO_EDITOR ({os.environ.get('PROOFCUT_AUTO_EDITOR') or 'unset'}), "
        f"then {deps.auto_editor()} (`proofcut setup`'s), then PATH, then ~/.local/bin/auto-editor"
    )
    stale_fix = _by_setup(
        f"install the {autoeditor.release_asset()} binary from the GitHub release. "
        "`pip install auto-editor` gets 29.3.1 — a stale fork of the old "
        "Python program under the same name, which does not speak the v3 "
        "timeline proofcut writes."
    )
    try:
        binary = autoeditor.binary()
    except autoeditor.AutoEditorError as exc:
        return _entry(
            "auto-editor",
            "silence removal, and rendering a single-source cut",
            looked_for=looked_for,
            why=str(exc),
            fix=stale_fix,
        )
    out, err, code = _run([binary, "--version"])
    version = (out or err).strip().splitlines()[0].strip() if (out or err).strip() else None
    # A version is a number from a process that exited 0. A binary the loader
    # refuses prints its refusal on stderr at exit 127, and that line read as
    # the version beside a ✓ on a clean Ubuntu with no libgomp.
    if code != 0 or not version or not re.match(r"v?\d+\.\d+", version):
        said = (out + err).strip().splitlines()
        return _entry(
            "auto-editor",
            "silence removal, and rendering a single-source cut",
            looked_for=looked_for,
            found=binary,
            why=f"{binary} --version exited {code} without printing a version"
            + (f": {said[-1].strip()}" if said else ""),
            fix=(
                "a missing shared library is named above: install your distribution's "
                "package for it (libgomp.so.1 is `libgomp1` on Ubuntu, `libgomp` on "
                "Fedora). Otherwise: " + stale_fix
            )
            if said and "shared librar" in said[-1]
            else stale_fix,
        )
    major = _major(version)
    if major is not None and major < MIN_AUTO_EDITOR:
        return _entry(
            "auto-editor",
            "silence removal, and rendering a single-source cut",
            looked_for=looked_for,
            found=binary,
            version=version,
            why=(
                f"this is {version}, and proofcut needs {MIN_AUTO_EDITOR} or newer. "
                f"{version} is almost certainly PyPI's build."
            ),
            fix=stale_fix,
        )
    return _entry(
        "auto-editor",
        "silence removal, and rendering a single-source cut",
        ok=True,
        looked_for=looked_for,
        found=binary,
        version=version,
        note=AUTO_EDITOR_GATE,
    )


def _major(version: str) -> int | None:
    head = version.strip().lstrip("v").split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _melt_entry() -> dict[str, Any]:
    """melt, probed by its banner — **never by its exit code** (CLAUDE.md).

    Pointed at a project it cannot read, melt prints `Failed to load` and
    exits 0, so a zero return here proves nothing. What is checked is that
    stdout carries melt's own `melt <version>` line.
    """
    where, install = picture.melt_search()
    looked_for = (
        f"$PROOFCUT_MELT ({os.environ.get('PROOFCUT_MELT') or 'unset'}), "
        f"then {deps.melt()} (`proofcut setup`'s), "
        f"then PATH ({', '.join(picture.MELT_NAMES)}), then {where}"
    )
    fix = (
        f"{install} Without it, single-source cuts still render through "
        "auto-editor; anything layered (b-roll, cards, music) does not."
    )
    if not sys.platform.startswith("linux"):
        # Linux's advice names setup itself, beside the distribution packages.
        fix = _by_setup(fix)
    try:
        command = picture.melt_command()
    except picture.PictureError as exc:
        return _entry(
            "melt",
            "rendering layered timelines — b-roll, cards, the music bed",
            looked_for=looked_for,
            why=str(exc),
            fix=fix,
        )
    out, err, code = _run([*command, "-version"])
    banner = next((ln for ln in (out + err).splitlines() if picture.MELT_BANNER.match(ln)), None)
    if banner is None:
        return _entry(
            "melt",
            "rendering layered timelines — b-roll, cards, the music bed",
            looked_for=looked_for,
            found=" ".join(command),
            why=(
                f"`{' '.join(command)} -version` exited {code} and printed no "
                "`melt …` banner. Its exit code is not evidence either way — "
                "melt exits 0 on failures it only mentions in its output."
            ),
            fix=fix,
        )
    return _entry(
        "melt",
        "rendering layered timelines — b-roll, cards, the music bed",
        ok=True,
        looked_for=looked_for,
        found=" ".join(command),
        version=picture.MELT_BANNER.match(banner).group(1),
    )


def _magick_entry() -> dict[str, Any]:
    """ImageMagick 7, plus the RSVG coder cards are rasterised through."""
    looked_for = f"$PROOFCUT_MAGICK ({os.environ.get('PROOFCUT_MAGICK') or 'unset'}), then PATH"
    fix = (
        "install ImageMagick 7 (`magick`), or set PROOFCUT_MAGICK to a command "
        "that runs it. IM6's `convert` is deliberately not searched: it is a "
        "different SVG renderer with different defaults. Without magick, title "
        "and end cards cannot be drawn; everything else works."
    )
    try:
        command = graphics.magick_command()
    except graphics.GraphicsError as exc:
        return _entry(
            "magick", "rasterising title and end cards", looked_for=looked_for, why=str(exc), fix=fix
        )
    out, err, code = _run([*command, "-version"])
    text = out + err
    line = next((ln for ln in text.splitlines() if "ImageMagick" in ln), None)
    if line is None:
        return _entry(
            "magick",
            "rasterising title and end cards",
            looked_for=looked_for,
            found=" ".join(command),
            why=f"`{' '.join(command)} -version` exited {code} and printed no ImageMagick banner",
            fix=fix,
        )
    version = line.split("ImageMagick", 1)[1].strip().split(" ")[0]
    formats, _, _ = _run([*command, "-list", "format"])
    note = None
    if "RSVG" not in formats:
        note = (
            "this build has no RSVG coder (`magick -list format | grep RSVG`), "
            "so card SVGs will rasterise through a different renderer than the "
            "one proofcut's templates were measured on."
        )
    return _entry(
        "magick",
        "rasterising title and end cards",
        ok=True,
        looked_for=looked_for,
        found=" ".join(command),
        version=version,
        note=note,
    )


# -- optional ------------------------------------------------------------


def _optional(name: str, feature: str, report: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """An optional capability. Missing is `unavailable`, never a failure.

    Everything proofcut promises works without these, so a doctor run on a box
    with none of them is still a clean bill of health — `ok` on the report as
    a whole reads only the required section.
    """
    row = _entry(name, feature, ok=bool(report.get("available")), **fields)
    if not row["ok"]:
        row["why"] = report.get("why")
    return row


def _vlm_entry() -> dict[str, Any]:
    report = describe.available()
    row = _optional(
        "PROOFCUT_VLM",
        "describe — searching b-roll by what is on screen",
        report,
        looked_for=f"$PROOFCUT_VLM ({os.environ.get('PROOFCUT_VLM') or 'unset'})",
        found=report.get("python"),
    )
    if not row["ok"]:
        row["fix"] = (
            "point PROOFCUT_VLM at a python in a venv with torch, transformers, "
            "bitsandbytes and Pillow, on a machine with a CUDA GPU; the model "
            f"({describe.MODEL}) downloads on first use. `proofcut describe --plan` "
            "reports the same answer without paying for a model load."
        )
    return row


def _face_entry() -> dict[str, Any]:
    report = faces.available()
    row = _optional(
        "PROOFCUT_FACE",
        "reframe-detect — face-aware crop proposals when the canvas moves",
        report,
        looked_for=f"$PROOFCUT_FACE ({os.environ.get('PROOFCUT_FACE') or 'unset'})",
        found=report.get("python"),
    )
    if not row["ok"]:
        row["fix"] = (
            "point PROOFCUT_FACE at a python in a venv with insightface, "
            "onnxruntime and opencv-python. Framing still works by hand (`proofcut reframe`); only "
            "the proposals need this."
        )
    return row


def _tts_entry() -> dict[str, Any]:
    """The synthesiser, and — separately — whether a voice is configured.

    **The voice path is never reported.** A voice is a directory holding
    somebody's recorded speech, and doctor prints on a screen someone may be
    sharing; that it is set is the whole answer anyone needs. It has no
    default on purpose, so unset is an expected refusal rather than an error.
    """
    looked_for = (
        f"$PROOFCUT_TTS ({os.environ.get('PROOFCUT_TTS') or 'unset'}), "
        f"$PROOFCUT_TTS_MODEL ({os.environ.get('PROOFCUT_TTS_MODEL') or 'unset'})"
    )
    row = _entry(
        "PROOFCUT_TTS",
        "vo-synth — synthesising a line in the project's own voice",
        looked_for=looked_for,
    )
    try:
        row["found"] = str(tts.tts_python())
        row["version"] = Path(str(tts.model_dir())).name
    except tts.TTSError as exc:
        row["why"] = str(exc)
        row["fix"] = (
            "point PROOFCUT_TTS at a python with qwen-tts and a CUDA torch, and "
            "PROOFCUT_TTS_MODEL at a local Qwen3-TTS snapshot. Everything else in "
            "proofcut works without them."
        )
        return row
    if refusal := tts.platform_refusal():
        row["why"] = refusal
        row["fix"] = (
            "vo-synth has only ever run on a CUDA GPU, so run it on a Linux or "
            f"Windows box with one — or set {tts.DEVICE_ENV}=mps and measure it. "
            "Everything else in proofcut works without it."
        )
        return row

    voice = os.environ.get("PROOFCUT_TTS_VOICE")
    if not voice:
        row["why"] = (
            "no voice is configured. There is no default voice on purpose — a "
            "voice is a person, not tooling — so this is an expected refusal, "
            "not a broken install."
        )
        row["fix"] = (
            "set PROOFCUT_TTS_VOICE to a directory holding ref.wav (≈10–20 s of "
            "one speaker, no music) and ref.txt (its words), or pass "
            "`--voice <dir>` per call."
        )
        return row
    missing = [n for n in ("ref.wav", "ref.txt") if not (Path(voice).expanduser() / n).is_file()]
    if missing:
        row["why"] = f"the directory $PROOFCUT_TTS_VOICE names is missing {', '.join(missing)}"
        row["fix"] = (
            "a voice is a directory holding ref.wav (≈10–20 s of one speaker, "
            "no music) and ref.txt (its words). Both are needed: the reference "
            "audio alone synthesises against whatever the model guessed the "
            "words were."
        )
        return row
    row["ok"] = True
    row["note"] = "a voice is configured (its path is deliberately not printed here)"
    return row


# -- display, and the caption face ---------------------------------------


def _display() -> dict[str, Any]:
    """Whether MLT's Qt module has something to draw into.

    Without one, every `qimage` producer and the `qtblend` transition refuse
    to load and **melt still exits 0** — the picture lane simply is not in the
    file. An unattended box does not need a session: `QT_QPA_PLATFORM=offscreen`
    draws with no display server at all, measured on this repo's own box, and
    doctor says so rather than reporting a bare "no display".

    **On macOS and Windows the question does not arise** — Qt draws through
    the OS's own window system (`picture.NATIVE_QT_PLATFORMS`) — so `ok` is
    None and `applicable` False: neither a pass nor a failure, since whether
    Qt draws a card there is unmeasured (docs/plans/PORTABILITY.md step 4).
    """
    env = picture.display_env()
    headless = picture.qt_is_headless(env)
    wayland, x11 = env.get("WAYLAND_DISPLAY"), env.get("DISPLAY")
    native = picture.native_qt_platform()
    if native:
        return {
            "ok": None,
            "applicable": False,
            "wayland_display": wayland,
            "display": x11,
            "xdg_runtime_dir": env.get("XDG_RUNTIME_DIR"),
            "qt_platform": env.get("QT_QPA_PLATFORM") or None,
            "headless_qt": headless,
            "how": None,
            "why": None,
            "fix": None,
            "note": (
                f"Qt draws through its own `{native}` plugin, with no display "
                "server to find. Whether a "
                "layered render keeps its cards here has not been measured."
            ),
        }
    report: dict[str, Any] = {
        "ok": bool(wayland or x11 or headless),
        "applicable": True,
        "wayland_display": wayland,
        "display": x11,
        "xdg_runtime_dir": env.get("XDG_RUNTIME_DIR"),
        "qt_platform": env.get("QT_QPA_PLATFORM") or None,
        "headless_qt": headless,
        "why": None,
        "fix": None,
    }
    if report["ok"] and headless and not (wayland or x11):
        # The variable is a request; whether MLT honours it is the build's call.
        # Ubuntu 24.04's MLT 7.22 does not, and drops every `qtblend` at exit 0,
        # so a headless-only box is judged by a one-frame probe render.
        draws = picture.qt_draws(env)
        report["qt_probe"] = draws
        if draws is False:
            report["ok"] = False
            report["how"] = None
            report["why"] = (
                "QT_QPA_PLATFORM is set, but this melt's Qt module does not draw "
                "under it — a one-frame probe came back with its `qtblend` filter "
                "dropped, so a layered render would lose every card and crop and "
                "still exit 0."
            )
            report["fix"] = (
                "`proofcut setup` installs Shotcut's portable melt for this user, "
                "which draws with no X server at all — or run renders under a "
                "virtual X display: `xvfb-run -a proofcut …` "
                "(`apt install xvfb`, `dnf install xorg-x11-server-Xvfb`). Some MLT "
                "builds want X11 whatever QT_QPA_PLATFORM says; Ubuntu 24.04's MLT "
                "7.22 and Fedora 44's MLT 7.40 are two. "
                "`proofcut export --render` refuses rather than rendering without it."
            )
            return report
    if report["ok"]:
        report["how"] = (
            "QT_QPA_PLATFORM draws with no display server"
            if headless
            else ("a Wayland session" if wayland else "an X11 session")
        )
        return report
    report["how"] = None
    report["why"] = (
        "MLT's Qt module has no display and no headless platform, so a layered "
        "render would drop every card and every `qtblend` transition — and melt "
        "would still exit 0."
    )
    report["fix"] = (
        "run this where a desktop session exists, or set "
        "QT_QPA_PLATFORM=offscreen, which is the route for any unattended "
        "render. `proofcut export --render` refuses rather than rendering a film "
        "with its picture missing."
    )
    return report


def _caption_font() -> dict[str, Any]:
    """Does the default caption face actually draw, or is libass substituting?

    Two questions, kept apart because this repo has measured them disagreeing:
    `fonts.probe` asks the renderer whether the named family drew at all, and
    `captions.font_match` asks fontconfig which family it *thinks* resolves.
    A clean `resolves_to` is not a claim about the burn, so both are reported
    and neither is folded into the other.
    """
    family = captions.CAPTION_FONT
    report: dict[str, Any] = {
        "font": family,
        "ok": False,
        "drew": None,
        "resolves_to": None,
        "why": None,
        "fix": None,
    }
    try:
        drew = fonts.probe(family)
    except fonts.FontToolMissing as exc:
        report["why"] = str(exc)
        if exc.tool == "magick":
            # The probe burns both frames before it compares them, so a missing
            # magick is met after libass has already drawn. It is an optional
            # tool the check needs, not a caption defect: `–`, never `✗`.
            report["unavailable"] = True
            report["fix"] = (
                "put ImageMagick 7's `magick` on PATH to check which face draws "
                "(the probe does not read PROOFCUT_MAGICK). Captions burn without "
                f"it, through ffmpeg's libass, but whether in {family} or a "
                "substitute is unchecked."
            )
        else:
            report["fix"] = (
                "captions burn through ffmpeg with libass (`ffmpeg -filters | grep "
                "ass`), so they cannot be burnt until it runs."
            )
        return report
    except fonts.FontError as exc:
        report["why"] = str(exc)
        report["fix"] = (
            "the probe burns with ffmpeg's libass (`ffmpeg -filters | grep ass`) "
            "and compares with `magick`; the line above names the step that "
            "failed. If it is the burn, captions cannot be burnt either."
        )
        return report
    # Where the OS has its own font system, fontconfig is not asked at all:
    # even installed (Homebrew has one), it answers for a resolver libass is
    # not using there. The render's answer is the whole report.
    native = fonts.native_font_system()
    report["font_system"] = native or "fontconfig"
    matched = {} if native else captions.font_match(family)
    report["drew"] = drew.get("drew")
    # libass's own account of the burn. On a ✗ it is the only thing saying
    # *what* drew — the Windows run could report only "not Outfit".
    report["font_provider"] = drew.get("font_provider")
    report["drawn_with"] = drew.get("drawn_with") or []
    report["resolves_to"] = matched.get("resolves_to")
    report["fontconfig_available"] = matched.get("available")
    report["ok"] = drew.get("drew") is True
    if report["ok"]:
        report["note"] = (
            f"fontconfig is not this platform's font system ({native}), so only "
            "the render was asked — and it drew."
            if native
            else "fontconfig's answer and the render's agree here. They do not "
            "always: a family fc-match calls installed can still burn in a "
            "substitute, which is why both are asked."
        )
        return report
    report["why"] = drew.get("warning") or (
        f"{family!r} could not be shown to draw — the probe rendered nothing at all"
    )
    report["fix"] = (
        f"`proofcut fonts --install` copies the vendored face where "
        f"{native or 'fontconfig'} looks. Until then captions burn in a face "
        "nobody chose and ffmpeg exits 0 about it."
    )
    return report


def _agent() -> dict[str, Any]:
    """The agent panel's `claude`, run for its version rather than found.

    Its own section, like the display: it is not a capability of proofcut's
    engine but of one client — `proofcut web`'s agent pane spawns `claude -p`
    and nothing else does — so absent is `–`, never a failure, and `ok` on
    the report does not read it. The binary is resolved by `webui._agent_bin`
    itself rather than restated, so this answers what the pane will spawn.
    Whether that `claude` is logged in is not probed: finding out costs a
    model call.
    """
    from proofcut import webui

    binary = webui._agent_bin()
    found = shutil.which(binary)
    fix = (
        "install Claude Code (https://docs.claude.com/en/docs/claude-code) and "
        f"log in, or set {webui.AGENT_BIN_ENV} to its binary. Everything else — "
        "the CLI, `proofcut mcp` for any MCP client, and the rest of the workspace "
        "— works without it."
    )
    if not found:
        return {
            "ok": False,
            "found": None,
            "version": None,
            "why": f"no `{binary}` on PATH — the workspace's agent pane has nothing to spawn",
            "fix": fix,
        }
    out, _err, code = _run([found, "--version"])
    version = out.strip().splitlines()[0] if out.strip() else None
    if code != 0 or not version:
        return {
            "ok": False,
            "found": found,
            "version": None,
            "why": f"{found} --version exited {code} without printing a version",
            "fix": fix,
        }
    return {"ok": True, "found": found, "version": version, "why": None, "fix": None}


# -- the old name's variables --------------------------------------------

#: The prefix every variable carried before the rename, and the one it carries
#: now. docs/plans/RENAME.md decision 3: no resolver reads the old name — a
#: resolver that reads both is two facts — so this section is the only place
#: in proofcut that looks at a `LUCID_*` variable at all, and it only names it.
LEGACY_ENV_PREFIX = "LUCID_"
ENV_PREFIX = "PROOFCUT_"


def _legacy_env() -> dict[str, Any]:
    """Every `LUCID_*` variable still set, each beside the name that replaced it.

    Without this a stranger's `60-lucid.conf` silently loses face detection at
    exit 0 — the resolver finds `PROOFCUT_FACE` unset and reports the
    capability absent, which is true and says nothing about why. Its own
    section, like the agent panel's: a stale variable is a note, never a ✗,
    and `ok` on the report does not read it. An empty `stale` is the all-clear.

    **Names only, never values.** `LUCID_TTS_VOICE` is somebody's recorded
    speech (the voice rule `_tts_entry` holds to), and every other value is a
    path on this machine; the name is the whole answer a rename needs.
    """
    renames = {
        name: ENV_PREFIX + name[len(LEGACY_ENV_PREFIX) :]
        for name in sorted(os.environ)
        if name.startswith(LEGACY_ENV_PREFIX)
    }
    stale = [
        {"name": old, "rename_to": new, "rename_to_set": bool(os.environ.get(new))}
        for old, new in renames.items()
    ]
    if not stale:
        return {"ok": True, "stale": [], "note": None, "fix": None}
    return {
        "ok": False,
        "stale": stale,
        "note": (
            "proofcut was named lucid until 0.23.0 and reads only PROOFCUT_* "
            "variables, so each of these is ignored — whatever it configured "
            "reads as unset in the rows above."
        ),
        "fix": (
            "rename each variable where it is set (a shell profile, or a file "
            "under ~/.config/environment.d/ on a systemd desktop) to the name "
            "beside it, then start a new session. One marked `already set` is "
            "a leftover and can simply be removed."
        ),
    }


# -- long paths ----------------------------------------------------------


def _long_paths() -> dict[str, Any] | None:
    """Whether Windows' 248-character folder limit applies to this machine.

    `None` off Windows, where it does not exist. A note, never a ✗: with long
    paths off every project in an ordinary folder works, and `ok` does not read
    it — but a project folder past `max_root_length` is refused at `init`, and
    a stranger should be able to learn why before that. HISTORY.md § A long
    project path on Windows.
    """
    enabled = project.windows_long_paths()
    if enabled is None:
        return None
    if enabled:
        return {"enabled": True, "max_root": None, "note": None, "fix": None}
    limit = project.max_root_length()
    return {
        "enabled": False,
        "max_root": limit,
        "note": (
            f"Windows limits a folder path to {project.WINDOWS_DIR_LIMIT} characters, so a "
            f"project folder can be at most {limit} characters long on this PC."
        ),
        "fix": f"only if you need deeper project folders: {project.LONG_PATHS_FIX}.",
    }


# -- the report ----------------------------------------------------------


def report() -> dict[str, Any]:
    """Probe every dependency, and name the trap behind each one that fails.

    Report-only: nothing is installed, nothing is written, and no project is
    opened or needed. `ok` reads the **required** section alone — an optional
    capability that is absent is a feature that is unavailable, not a broken
    install, and everything proofcut promises works without all four of them.
    """
    from proofcut import __version__

    required = [
        _ffmpeg_entry("ffmpeg", "every media read, write and burn"),
        _ffmpeg_entry("ffprobe", "probing what a media file actually holds"),
        _whisper_entry(),
        _auto_editor_entry(),
        _melt_entry(),
    ]
    # magick draws cards and nothing else, so it gates one feature like the
    # rest of this list. It sat in `required` until a clean Ubuntu 24.04, whose
    # apt has only ImageMagick 6, could never read `ok` for a demo that draws
    # no card (HISTORY.md § A stranger's install, on a clean Ubuntu).
    optional = [_magick_entry(), _vlm_entry(), _face_entry(), _tts_entry()]
    return {
        "proofcut": __version__,
        "ok": all(entry["ok"] for entry in required),
        "required": required,
        "optional": optional,
        "display": _display(),
        "caption_font": _caption_font(),
        "agent": _agent(),
        "legacy_env": _legacy_env(),
        "long_paths": _long_paths(),
    }


# -- the human render ----------------------------------------------------

_TICK, _CROSS, _DASH = "✓", "✗", "–"


def _wrap(text: str, *, indent: str, width: int = 78) -> list[str]:
    """Wrap `text` to `width`, prefixing every line with `indent`."""
    lines, current = [], indent
    for word in text.split():
        candidate = f"{current} {word}" if current.strip() else f"{indent}{word}"
        if len(candidate) > width and current.strip():
            lines.append(current)
            current = f"{indent}{word}"
        else:
            current = candidate
    if current.strip():
        lines.append(current)
    return lines


def _render_entry(entry: dict[str, Any], *, optional: bool) -> list[str]:
    """One dependency, as the mark, the headline, and the sentence after it.

    The sentence after the mark is the whole value of doctor: knowing that
    melt is missing is worth very little, and knowing that it lives inside the
    Kdenlive flatpak is worth the command.
    """
    mark = _TICK if entry["ok"] else (_DASH if optional else _CROSS)
    head = f"  {mark} {entry['name']}"
    if entry["version"]:
        head += f" {entry['version']}"
    # `found` is shown even for a row that is not ok: "PROOFCUT_TTS is here but
    # has no voice" and "PROOFCUT_TTS is not here at all" are different answers,
    # and the path is what tells them apart at a glance.
    if entry["found"]:
        head += f" — {entry['found']}"
    lines = [head, *_wrap(entry["what"], indent="      ")]
    if entry["why"]:
        lines += _wrap(entry["why"], indent="      ")
    if not entry["ok"] and entry["looked_for"]:
        lines += _wrap(f"looked at: {entry['looked_for']}", indent="      ")
    if entry["fix"]:
        lines += _wrap(f"fix: {entry['fix']}", indent="      ")
    if entry["note"]:
        lines += _wrap(f"note: {entry['note']}", indent="      ")
    return lines


def render(payload: dict[str, Any]) -> str:
    """`report()` as something to read — the CLI's own rendering of the dict."""
    lines = [f"proofcut {payload['proofcut']}", ""]

    lines.append("Required")
    for entry in payload["required"]:
        lines += _render_entry(entry, optional=False)

    lines += ["", "Optional — every one of these gates a single feature"]
    for entry in payload["optional"]:
        lines += _render_entry(entry, optional=True)

    display = payload["display"]
    lines += ["", "Display (MLT's Qt module)"]
    # `.get`, for the same reason as `agent` below: hand-built payloads predate it.
    if display.get("applicable") is False:
        lines.append(f"  {_DASH} not applicable on this platform")
        lines += _wrap(display["note"], indent="      ")
    elif display["ok"]:
        lines.append(f"  {_TICK} {display['how']}")
    else:
        lines.append(f"  {_CROSS} no display")
        lines += _wrap(display["why"], indent="      ")
        lines += _wrap(f"fix: {display['fix']}", indent="      ")

    font = payload["caption_font"]
    lines += ["", "Caption font"]
    if font["ok"]:
        # `.get`: hand-built payloads predate the key, and absent meant fontconfig.
        system = font.get("font_system", "fontconfig")
        resolved = (
            f"fontconfig: {font['resolves_to']}"
            if system == "fontconfig"
            else f"{system} — fontconfig is not this platform's font system"
        )
        lines.append(f"  {_TICK} {font['font']} draws ({resolved})")
    elif font.get("unavailable"):
        lines.append(f"  {_DASH} {font['font']} — not checked")
        lines += _wrap(font["why"], indent="      ")
        lines += _wrap(f"fix: {font['fix']}", indent="      ")
    else:
        lines.append(f"  {_CROSS} {font['font']}")
        lines += _wrap(font["why"], indent="      ")
        # `.get`: hand-built payloads predate both keys.
        if font.get("drawn_with"):
            faces = ", ".join(f"{f['face']} ({f['file']})" for f in font["drawn_with"])
            provider = font.get("font_provider") or "unknown provider"
            lines += _wrap(f"libass ({provider}) drew it with: {faces}", indent="      ")
        lines += _wrap(f"fix: {font['fix']}", indent="      ")

    # `.get`, because the section is younger than the report's other keys and
    # a payload built by hand (the CLI's own tests build two) predates it.
    agent = payload.get("agent")
    if agent is not None:
        lines += ["", "Agent panel (`proofcut web`'s agent pane — optional)"]
        if agent["ok"]:
            lines.append(f"  {_TICK} {agent['version']} — {agent['found']}")
        else:
            lines.append(f"  {_DASH} claude" + (f" — {agent['found']}" if agent["found"] else ""))
            lines += _wrap(agent["why"], indent="      ")
            lines += _wrap(f"fix: {agent['fix']}", indent="      ")

    # `.get`, the agent section's reason: hand-built payloads predate it.
    legacy = payload.get("legacy_env")
    if legacy is not None:
        lines += ["", f"Old-name variables ({LEGACY_ENV_PREFIX}* — never read)"]
        if not legacy["stale"]:
            lines.append(f"  {_TICK} none set")
        else:
            for var in legacy["stale"]:
                lines.append(
                    f"  {_DASH} {var['name']} — now {var['rename_to']}"
                    + (" (already set)" if var["rename_to_set"] else "")
                )
            lines += _wrap(f"note: {legacy['note']}", indent="      ")
            lines += _wrap(f"fix: {legacy['fix']}", indent="      ")

    # `.get`, the agent section's reason; and `None` off Windows, where the
    # limit does not exist and the section is not drawn at all.
    long_paths = payload.get("long_paths")
    if long_paths is not None:
        lines += ["", "Long paths (Windows)"]
        if long_paths["enabled"]:
            lines.append(f"  {_TICK} enabled — project folders can be any depth")
        else:
            lines.append(f"  {_DASH} off — a project folder can be at most {long_paths['max_root']} characters")
            lines += _wrap(f"note: {long_paths['note']}", indent="      ")
            lines += _wrap(f"fix: {long_paths['fix']}", indent="      ")

    failures = [e["name"] for e in payload["required"] if not e["ok"]]
    lines.append("")
    lines.append(
        "Everything required is here."
        if payload["ok"]
        else f"Missing or unusable: {', '.join(failures)}."
    )
    return "\n".join(lines)
