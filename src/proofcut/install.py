"""`proofcut setup` — install, for this user, what `proofcut doctor` says is missing.

docs/plans/INSTALL.md is the design. This module holds to its rules:

- **Doctor's report is the input.** A piece is installed only when its row is
  ✗, so a working system ffmpeg or melt is never touched. A trial kit may
  install everything, because its tester agreed to that; a real installer
  that replaces a tool the user already had breaks something of theirs.
- **Every download is pinned by URL and SHA-256** (`PINS`), the way
  `scripts/windows_trial.ps1` pins its own, and a mismatch leaves nothing
  behind. The pins are bumped on purpose, never resolved from a `latest` tag:
  BtbN's `latest` moves daily, and its dated daily builds are deleted after a
  few weeks, so the ffmpeg pin is a month-end build, which BtbN keeps.
- **One folder** (`deps.root()`), plus symlinks in `~/.local/bin` for the
  names that have to be on PATH. A link is never written over an existing
  file. Every link, folder and uv tool is recorded, and `uninstall` removes
  exactly those.
- **No sudo, no distribution packages.** A server image missing the desktop
  libraries Shotcut's melt links against is told which ones, and nothing of
  that piece is left installed.
- **Linux only**, for now (INSTALL.md § Step 5 waits on a person's Mac run).
- **CLI only, never an MCP tool.** An agent must not start a 2 GB download
  and change what is on PATH on its own.

What each piece is, and why that source: INSTALL.md § What was measured.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from proofcut import deps, doctor, picture


class InstallError(Exception):
    """Raised when setup cannot run here, or one of its pieces fails."""


@dataclass(frozen=True)
class Pin:
    """One download: where it is, what it must hash to, and how big it is."""

    version: str
    url: str
    sha256: str
    size: int


#: Piece → architecture → download. Read off each release's own asset list and
#: checksums on 2026-09-16 (INSTALL.md § What was measured). Shotcut builds
#: Linux for x86_64 only, so an aarch64 box gets melt from its distribution.
PINS: dict[str, dict[str, Pin]] = {
    "ffmpeg": {
        "x86_64": Pin(
            "n8.1.2 (BtbN autobuild-2026-08-31-13-27)",
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-31-13-27/"
            "ffmpeg-n8.1.2-50-g1a748fe2cd-linux64-gpl-8.1.tar.xz",
            "c733b4b2951e5957e15505f788b2c65a7a41b6da4b289e295852cc38079b4d2b",
            125758156,
        ),
        "aarch64": Pin(
            "n8.1.2 (BtbN autobuild-2026-08-31-13-27)",
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-31-13-27/"
            "ffmpeg-n8.1.2-50-g1a748fe2cd-linuxarm64-gpl-8.1.tar.xz",
            "ae5da4f51b9052390f414005f8ab26c1eed1268f327cce7cb79aa076b29bd66e",
            107695184,
        ),
    },
    "auto-editor": {
        "x86_64": Pin(
            "31.6.0",
            "https://github.com/WyattBlue/auto-editor/releases/download/31.6.0/auto-editor-linux-x86_64",
            "ad38d62dda324bf5adf820e7c49fd982c30e4b9b89682a6f9a441b6485edd29a",
            46145848,
        ),
        "aarch64": Pin(
            "31.6.0",
            "https://github.com/WyattBlue/auto-editor/releases/download/31.6.0/auto-editor-linux-aarch64",
            "0233e4fab698c98709e14ca64782103f40f9b87248853b21dc1cb29b02f98df6",
            29387192,
        ),
    },
    "melt": {
        "x86_64": Pin(
            "Shotcut 26.8.1 (melt 7.41.0)",
            "https://github.com/mltframework/shotcut/releases/download/v26.8.1/"
            "shotcut-linux-x86_64-26.8.1.txz",
            "c4befab2240964389df6139f00aae0b92949f398fd98083b922f3aabd8b7a844",
            155181712,
        ),
    },
}

#: whisper is not a download setup hashes: uv resolves it. 3.12 because torch's
#: Intel-Mac wheels stop there, and the kits pin it too (INSTALL.md,
#: measurement 6); on Linux it only keeps the two routes the same.
WHISPER_TOOL = "openai-whisper"
WHISPER_PYTHON = "3.12"

#: What the whisper install costs on disk, measured on a clean Ubuntu
#: (HISTORY.md § A stranger's install, on a clean Ubuntu). Approximate: uv
#: decides the wheels.
WHISPER_BYTES = {"cpu": 1_900_000_000, "cuda": 5_500_000_000}

#: The libraries Shotcut's melt, its Qt module and Qt's offscreen platform
#: link against, checked with `ldd` before the piece is kept.
MELT_LINKED = (
    "bin/melt-7",
    "lib/mlt-7/libmltqt6.so",
    "lib/mlt-7/libmltavformat.so",
    "lib/qt6/platforms/libqoffscreen.so",
)

RECORD_NAME = "installed.json"
RECORD_VERSION = 1

_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


def machine() -> str | None:
    """This CPU in `PINS`' spelling, or None for one no pin covers."""
    return _ARCH.get(platform.machine().lower())


def bin_dir() -> Path:
    """Where the PATH links go: the directory uv's own installer puts on PATH."""
    return Path.home() / ".local" / "bin"


# -- the record ------------------------------------------------------------


def _record_path() -> Path:
    return deps.root() / RECORD_NAME


def read_record() -> dict[str, Any]:
    """What setup has installed so far, or an empty record."""
    try:
        record = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": RECORD_VERSION, "pieces": {}, "made_dirs": []}
    record.setdefault("pieces", {})
    record.setdefault("made_dirs", [])
    return record


def _write_record(record: dict[str, Any]) -> None:
    path = _record_path()
    # Through `_makedirs`, so the record's own folders are recorded too: a
    # whisper-only install creates them and nothing else would say so.
    _makedirs(path.parent, record)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _makedirs(path: Path, record: dict[str, Any]) -> None:
    """`mkdir -p`, recording each directory this call created, outermost first."""
    missing = []
    for parent in [path, *path.parents]:
        if parent.exists():
            break
        missing.append(parent)
    for directory in reversed(missing):
        directory.mkdir()
        record["made_dirs"].append(str(directory))


# -- the plan ----------------------------------------------------------------


def _reason(row: dict[str, Any]) -> str:
    """A doctor row's `why`, cut to its first sentence.

    The rest of a resolver's refusal is advice, and on Linux that advice now
    begins by recommending `proofcut setup`, which reads as nonsense inside
    setup's own plan. The whole row is one `proofcut doctor` away.
    """
    why = row["why"] or f"{row['name']} is unusable"
    head, dot, _rest = why.partition(". ")
    return head + "." if dot else why


def _rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in report["required"]}


def _uv() -> str | None:
    return shutil.which("uv")


def _uv_tools(uv: str) -> set[str]:
    """The uv tools already installed, by name, from `uv tool list`."""
    done = subprocess.run([uv, "tool", "list"], capture_output=True, text=True, check=False)
    return {line.split()[0] for line in done.stdout.splitlines() if line and not line[0].isspace() and not line.startswith("-")}


def _gpu() -> bool:
    """Whether an NVIDIA driver answers, which is what makes CUDA torch worth 5.5 GB."""
    return shutil.which("nvidia-smi") is not None


def _melt_wanted(report: dict[str, Any], rows: dict[str, dict[str, Any]]) -> str | None:
    """Why this box needs Shotcut's melt, or None.

    Either no usable melt at all, or a melt whose Qt module draws nothing
    without a display, which is both distro MLTs measured (INSTALL.md,
    measurement 2). A box with no display and no `QT_QPA_PLATFORM` is probed
    under `offscreen` here, since that is what it would be told to set next,
    and one run should settle it.
    """
    if not rows["melt"]["ok"]:
        return _reason(rows["melt"])
    display = report["display"]
    if display.get("applicable") is False or display["ok"]:
        return None
    if display.get("qt_probe") is False:
        return "this melt's Qt module draws nothing without a display"
    if not (display.get("wayland_display") or display.get("display") or display.get("headless_qt")):
        env = {**picture.display_env(), "QT_QPA_PLATFORM": "offscreen"}
        if picture.qt_draws(env) is False:
            return "this melt's Qt module draws nothing without a display, even under QT_QPA_PLATFORM=offscreen"
    return None


def plan(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """What setup would install here, and what it cannot, without touching anything.

    Each piece names the doctor row that asked for it. A piece setup already
    installed whose row is still ✗ is not installed again: that is a finding
    to read (usually PATH order), and reinstalling it would only loop.
    """
    if not sys.platform.startswith("linux"):
        raise InstallError(
            "`proofcut setup` installs on Linux only for now. On a Mac or a Windows "
            "PC, `proofcut doctor` prints the fix under each ✗, and "
            "scripts/mac_trial.sh or scripts/windows_trial.ps1 installs everything "
            "for a test run (README.md § Help wanted)."
        )
    report = report if report is not None else doctor.report()
    rows = _rows(report)
    arch = machine()
    record = read_record()
    pieces: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    notes: list[str] = []

    def want(name: str, why: str) -> None:
        if name in record["pieces"]:
            unavailable.append({
                "name": name,
                "why": f"setup installed it already and doctor still reports: {why}",
                "fix": "read the doctor row above; `proofcut setup --uninstall` removes what setup added",
            })
            return
        pin = PINS[name].get(arch or "")
        if pin is None:
            unavailable.append({
                "name": name,
                "why": f"there is no {name} download pinned for {platform.machine() or 'this CPU'}",
                "fix": "follow the fix `proofcut doctor` prints for it",
            })
            return
        pieces.append({"name": name, "why": why, "version": pin.version, "url": pin.url, "bytes": pin.size})

    ffmpeg_rows = [rows[n] for n in ("ffmpeg", "ffprobe") if not rows[n]["ok"]]
    if ffmpeg_rows:
        want("ffmpeg", " ".join(_reason(r) for r in ffmpeg_rows))

    if not rows["whisper"]["ok"]:
        uv = _uv()
        if uv is None:
            unavailable.append({
                "name": "whisper",
                "why": "uv is not on PATH, and setup installs whisper as a uv tool",
                "fix": "install uv (https://docs.astral.sh/uv/), then run `proofcut setup` again",
            })
        elif "whisper" in record["pieces"]:
            want("whisper", _reason(rows["whisper"]))
        elif WHISPER_TOOL in _uv_tools(uv):
            unavailable.append({
                "name": "whisper",
                "why": f"{WHISPER_TOOL} is already a uv tool here, and it does not start",
                "fix": f"reinstall it yourself: `uv tool install --reinstall --python {WHISPER_PYTHON} {WHISPER_TOOL}`",
            })
        else:
            backend = "cuda" if _gpu() else "cpu"
            pieces.append({
                "name": "whisper",
                "why": _reason(rows["whisper"]),
                "version": f"{WHISPER_TOOL} (uv tool, Python {WHISPER_PYTHON}, {backend} torch)",
                "url": None,
                "bytes": WHISPER_BYTES[backend],
            })

    if not rows["auto-editor"]["ok"]:
        want("auto-editor", _reason(rows["auto-editor"]))

    if (why := _melt_wanted(report, rows)) is not None:
        want("melt", why)
        display = report["display"]
        if not (display.get("wayland_display") or display.get("display") or display.get("headless_qt")):
            notes.append(
                "this session has no display: set QT_QPA_PLATFORM=offscreen where you render "
                "(a shell profile, or ~/.config/environment.d/), and Shotcut's melt draws under it"
            )

    return {
        "platform": sys.platform,
        "arch": platform.machine(),
        "root": str(deps.root()),
        "pieces": pieces,
        "unavailable": unavailable,
        "bytes": sum(p["bytes"] for p in pieces),
        "notes": notes,
    }


# -- installing ------------------------------------------------------------


Say = Callable[[str], None]


def _fetch(pin: Pin, dest: Path, say: Say) -> None:
    """Download `pin` to `dest`, refusing anything whose SHA-256 is not the pin's."""
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    say(f"  downloading {pin.url}")
    try:
        with urllib.request.urlopen(pin.url, timeout=60) as response, part.open("wb") as out:
            done, shown = 0, 0
            while chunk := response.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                tenth = done * 10 // pin.size if pin.size else 0
                if tenth > shown and tenth <= 10:
                    shown = tenth
                    say(f"    {done / 1e6:.0f} of {pin.size / 1e6:.0f} MB")
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise InstallError(f"could not download {pin.url}: {exc}") from exc
    got = digest.hexdigest()
    if got != pin.sha256:
        part.unlink(missing_ok=True)
        raise InstallError(f"{pin.url} hashed to {got}, not the pinned {pin.sha256}; nothing was kept")
    os.replace(part, dest)


def _unpack(archive: Path, into: Path) -> None:
    with tarfile.open(archive) as tar:
        tar.extractall(into, filter="data")
    archive.unlink()


def _link(target: Path, name: str, entry: dict[str, Any], record: dict[str, Any], notes: list[str]) -> None:
    """Link `name` in `bin_dir()` to `target`, unless something is already there."""
    link = bin_dir() / name
    if link.exists() or link.is_symlink():
        notes.append(f"left {link} alone: it already exists, so {name} from setup is not on PATH")
        return
    _makedirs(link.parent, record)
    link.symlink_to(target)
    entry["links"].append(str(link))
    found = shutil.which(name)
    if found != str(link):
        notes.append(
            f"{name} on PATH is {found or 'nothing'}, not setup's {link}: put {bin_dir()} "
            "ahead of it on PATH (`uv tool update-shell` adds it)"
        )


def _missing_libraries(binaries: list[Path], lib_dir: Path | None = None) -> list[str]:
    """The shared libraries `ldd` cannot find for any of `binaries`."""
    env = dict(os.environ)
    if lib_dir is not None:
        env["LD_LIBRARY_PATH"] = str(lib_dir)
    missing: set[str] = set()
    for binary in binaries:
        done = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, env=env, check=False)
        missing |= {line.split()[0] for line in done.stdout.splitlines() if "not found" in line}
    return sorted(missing)


def _install_ffmpeg(pin: Pin, entry: dict[str, Any], record: dict[str, Any], say: Say, notes: list[str]) -> None:
    home = deps.root() / "ffmpeg"
    _makedirs(home, record)
    entry["dir"] = str(home)
    archive = home / "ffmpeg.tar.xz"
    _fetch(pin, archive, say)
    say("  unpacking")
    _unpack(archive, home)
    found = sorted(home.glob("*/bin/ffmpeg"))
    if not found:
        raise InstallError(f"{pin.url} unpacked with no bin/ffmpeg in it")
    binaries = found[0].parent
    # ffplay is a third of the download and nothing here plays video.
    (binaries / "ffplay").unlink(missing_ok=True)
    for name in ("ffmpeg", "ffprobe"):
        _link(binaries / name, name, entry, record, notes)


def _install_auto_editor(pin: Pin, entry: dict[str, Any], record: dict[str, Any], say: Say, notes: list[str]) -> None:
    target = deps.auto_editor()
    _makedirs(target.parent, record)
    entry["dir"] = str(target.parent)
    _fetch(pin, target, say)
    target.chmod(0o755)
    # It links the system's OpenMP runtime, which a bare Ubuntu lacks; the
    # first clean-container run found it only when `seed` died.
    if shutil.which("ldd") and (missing := _missing_libraries([target])):
        raise InstallError(
            f"auto-editor needs {', '.join(missing)}, which this system does not have. "
            "Install your distribution's package for it (libgomp.so.1 is `libgomp1` "
            "on Ubuntu, `libgomp` on Fedora) and run `proofcut setup` again."
        )


def _install_melt(pin: Pin, entry: dict[str, Any], record: dict[str, Any], say: Say, notes: list[str]) -> None:
    home = deps.melt().parent.parent
    _makedirs(home, record)
    entry["dir"] = str(home)
    archive = home / "shotcut.txz"
    _fetch(pin, archive, say)
    say("  unpacking")
    _unpack(archive, home)
    if not deps.melt().is_file():
        raise InstallError(f"{pin.url} unpacked with no Shotcut.app/melt in it")
    shotcut = deps.melt().parent
    linked = [shotcut / rel for rel in MELT_LINKED]
    if shutil.which("ldd") and (missing := _missing_libraries(linked, shotcut / "lib")):
        raise InstallError(
            "Shotcut's melt needs desktop libraries this system does not have: "
            f"{', '.join(missing)}. A desktop install has them; on a server image, install "
            "your distribution's packages for them (on Ubuntu, libasound2t64 libgl1 libegl1 "
            "libopengl0 libx11-xcb1 libcairo2 libgbm1 libva-drm2 libva-x11-2 "
            "libwayland-client0 libwayland-cursor0 libwayland-egl1 libfontconfig1 "
            "libglib2.0-0t64) and run `proofcut setup` again."
        )


_INSTALLERS = {"ffmpeg": _install_ffmpeg, "auto-editor": _install_auto_editor, "melt": _install_melt}


def _uv_dir(uv: str, which: str) -> Path | None:
    done = subprocess.run([uv, "--color", "never", which, "dir"], capture_output=True, text=True, check=False)
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def _children(directory: Path | None) -> set[Path]:
    return set(directory.iterdir()) if directory and directory.is_dir() else set()


def _install_whisper(entry: dict[str, Any], record: dict[str, Any], say: Say, notes: list[str]) -> None:
    """`uv tool install` whisper, recording what that added outside the tool itself.

    `--python 3.12` can download a Python into uv's own folder, and a first
    tool install creates uv's tool folder. Both are setup's doing, so both
    are recorded, the way scripts/mac_trial.sh records `uv-python`. uv's
    download cache is uv's, and is left to `uv cache clean`.
    """
    uv = _uv()
    if uv is None:
        raise InstallError("uv is not on PATH")
    pythons, tools = _uv_dir(uv, "python"), _uv_dir(uv, "tool")
    pythons_before = _children(pythons)
    # uv's tool install also creates ~/.local/bin, where it links `whisper`.
    tops = [top for top in (pythons, tools, bin_dir()) if top]
    chain = [d for top in tops for d in (top, *top.parents) if Path.home() in d.parents]
    absent = [d for d in dict.fromkeys(chain) if not d.exists()]
    argv = [uv, "tool", "install", "--python", WHISPER_PYTHON, WHISPER_TOOL]
    if not _gpu():
        argv += ["--torch-backend", "cpu"]
    say(f"  running {' '.join(argv[1:])}")
    # Not captured: this is minutes of resolver and download output, and the
    # person waiting on it should see it move.
    if subprocess.run(argv, check=False).returncode != 0:
        raise InstallError(f"`{' '.join(argv)}` failed; its output is above")
    entry["uv_tool"] = WHISPER_TOOL
    entry["uv_pythons"] = sorted(str(p) for p in _children(pythons) - pythons_before)
    # Outermost first, so uninstall's deepest-first rmdir can empty the chain.
    record["made_dirs"] += [str(d) for d in sorted(absent, key=lambda d: len(d.parts)) if d.exists()]
    if shutil.which("whisper") is None:
        notes.append("whisper installed, but uv's tool directory is not on PATH: run `uv tool update-shell`, then open a new shell")


def install(steps: dict[str, Any], say: Say = print) -> dict[str, Any]:
    """Install every piece in `plan()`'s answer, then ask doctor again.

    A failed piece is cleaned out and reported, and the rest carry on. The
    record is written after each piece, so a run stopped halfway still
    uninstalls exactly.
    """
    record = read_record()
    installed: list[str] = []
    failed: list[dict[str, str]] = []
    notes: list[str] = list(steps.get("notes", []))
    for piece in steps["pieces"]:
        name = piece["name"]
        say(f"{name} — {piece['version']}")
        entry: dict[str, Any] = {"dir": None, "links": [], "uv_tool": None, "version": piece["version"]}
        try:
            if name == "whisper":
                _install_whisper(entry, record, say, notes)
            else:
                _INSTALLERS[name](PINS[name][machine() or ""], entry, record, say, notes)
        except (InstallError, OSError, tarfile.TarError) as exc:
            _remove_entry(entry)
            failed.append({"name": name, "why": str(exc)})
            say(f"  failed: {exc}")
            _write_record(record)
            continue
        record["pieces"][name] = entry
        _write_record(record)
        installed.append(name)
    after = doctor.report()
    return {
        "installed": installed,
        "failed": failed,
        "notes": notes,
        "ok": after["ok"],
        "doctor": {row["name"]: row["ok"] for row in after["required"]},
        "display": after["display"]["ok"],
    }


# -- uninstalling ----------------------------------------------------------


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _remove_entry(entry: dict[str, Any]) -> list[str]:
    """Remove one piece's links, uv tool and folder; return what was removed."""
    removed = []
    root = deps.root()
    for link in entry.get("links", []):
        path = Path(link)
        if not path.is_symlink():
            continue
        target = path.parent / os.readlink(path)
        # Only a link still pointing into setup's folder is setup's to remove.
        if _inside(target, root):
            path.unlink()
            removed.append(link)
    if entry.get("uv_tool") and (uv := _uv()):
        subprocess.run([uv, "tool", "uninstall", entry["uv_tool"]], check=False)
        removed.append(f"uv tool {entry['uv_tool']}")
        pythons = _uv_dir(uv, "python")
        for recorded in entry.get("uv_pythons", []):
            path = Path(recorded)
            if pythons is None or pythons not in path.parents:
                continue
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                continue
            removed.append(recorded)
    directory = entry.get("dir")
    if directory and _inside(Path(directory), root) and Path(directory).exists():
        shutil.rmtree(directory)
        removed.append(directory)
    return removed


def uninstall_plan() -> dict[str, Any]:
    """What `uninstall` would remove, read off the record."""
    record = read_record()
    return {
        "root": str(deps.root()),
        "pieces": {name: {"links": e.get("links", []), "uv_tool": e.get("uv_tool"), "dir": e.get("dir")} for name, e in record["pieces"].items()},
        "recorded": _record_path().is_file(),
    }


def uninstall() -> dict[str, Any]:
    """Remove everything the record names, and nothing else."""
    record = read_record()
    removed: list[str] = []
    for entry in record["pieces"].values():
        removed += _remove_entry(entry)
    root = deps.root()
    if root.exists():
        shutil.rmtree(root)
        removed.append(str(root))
    # Deepest first, and only while empty: a directory holding anything of
    # the user's stays.
    for directory in sorted(record["made_dirs"], key=len, reverse=True):
        try:
            Path(directory).rmdir()
        except OSError:
            continue
        removed.append(directory)
    return {"removed": removed}


# -- the human render ------------------------------------------------------


def _mb(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


def render_plan(steps: dict[str, Any]) -> str:
    lines = [f"proofcut setup — installs into {steps['root']}", ""]
    if steps["pieces"]:
        lines.append("Will install")
        for piece in steps["pieces"]:
            lines.append(f"  {piece['name']} — {piece['version']}, about {_mb(piece['bytes'])}")
            lines += doctor._wrap(f"because: {piece['why']}", indent="      ")
        lines.append(f"  total: about {_mb(steps['bytes'])}")
    else:
        lines.append("Nothing to install: doctor reports no missing piece setup can supply.")
    if steps["unavailable"]:
        lines += ["", "Cannot install"]
        for item in steps["unavailable"]:
            lines.append(f"  {item['name']}")
            lines += doctor._wrap(item["why"], indent="      ")
            lines += doctor._wrap(f"fix: {item['fix']}", indent="      ")
    for note in steps["notes"]:
        lines += doctor._wrap(f"note: {note}", indent="  ")
    return "\n".join(lines)


def render_result(result: dict[str, Any]) -> str:
    lines = [""]
    if result["installed"]:
        lines.append(f"Installed: {', '.join(result['installed'])}.")
    for item in result["failed"]:
        lines += doctor._wrap(f"✗ {item['name']}: {item['why']}", indent="")
    for note in result["notes"]:
        lines += doctor._wrap(f"note: {note}", indent="")
    lines.append(
        "Doctor now reports everything required."
        if result["ok"]
        else "Doctor still reports: "
        + ", ".join(name for name, ok in result["doctor"].items() if not ok)
        + ". Run `proofcut doctor` for the fix under each."
    )
    if result["display"] is False:
        lines.append("Doctor's Display row is still ✗; `proofcut doctor` says why.")
    return "\n".join(lines)
