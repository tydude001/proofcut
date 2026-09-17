"""Where `proofcut setup` puts what it installs, for the resolvers to find.

A module of paths and nothing else, because the resolvers that read it
(`picture.melt_command`, `autoeditor.binary`) are imported by `doctor`, which
`install` imports — so the paths cannot live in `install` without a cycle.

**What setup installed is searched before PATH**, for melt and auto-editor
only. Setup installs one of those only when doctor marked the one PATH gives
as unusable: Ubuntu's and Fedora's MLT draw nothing headless, and a PyPI
auto-editor is a stale 29.x. A PATH-first search would find that same one
again and the install would change nothing. ffmpeg and whisper are not here:
proofcut names ffmpeg bare, and so does whisper's own `audio.py`, so the only
place an installed ffmpeg can be found is PATH, and whisper resolves through
PATH alone (CLAUDE.md). docs/plans/INSTALL.md § Step 2 and § Step 5.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def setup_installs_here() -> bool:
    """Whether `proofcut setup` installs on this OS and CPU.

    Linux, Windows, and an Intel Mac. An Apple silicon Mac waits on a person's
    report from the tester post (INSTALL.md § Step 5): its route is Homebrew,
    and nothing has measured setup driving that. Here rather than in
    `install`, because doctor asks it too and `install` imports doctor.
    """
    if sys.platform.startswith("linux") or sys.platform == "win32":
        return True
    return sys.platform == "darwin" and platform.machine().lower() in ("x86_64", "amd64")


def root() -> Path:
    """The one folder setup writes into.

    `%LOCALAPPDATA%\\proofcut\\deps` on Windows, where the test kit's folder
    already lives; `$XDG_DATA_HOME/proofcut/deps` everywhere else, a Mac
    included, since that is where uv keeps its own tools there too.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "proofcut" / "deps"
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "proofcut" / "deps"


def melt() -> Path:
    """Shotcut's `melt`, where setup unpacks it on this OS. It may not exist.

    Linux's is the tarball's wrapper script, Windows' is the portable zip's
    `melt.exe`, and a Mac's is inside the app bundle copied off the dmg.
    """
    if sys.platform == "win32":
        return root() / "melt" / "Shotcut" / "melt.exe"
    if sys.platform == "darwin":
        return root() / "melt" / "Shotcut.app" / "Contents" / "MacOS" / "melt"
    return root() / "melt" / "Shotcut.app" / "melt"


def auto_editor() -> Path:
    """auto-editor's release binary, as setup downloads it. It may not exist."""
    name = "auto-editor.exe" if sys.platform == "win32" else "auto-editor"
    return root() / "auto-editor" / name
