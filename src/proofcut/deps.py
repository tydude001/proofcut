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
PATH alone (CLAUDE.md). docs/plans/INSTALL.md § Step 2.
"""

from __future__ import annotations

import os
from pathlib import Path


def root() -> Path:
    """`$XDG_DATA_HOME/proofcut/deps`, the one folder setup writes into."""
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "proofcut" / "deps"


def melt() -> Path:
    """Shotcut's `melt` wrapper, as setup unpacks it. It may not exist."""
    return root() / "melt" / "Shotcut.app" / "melt"


def auto_editor() -> Path:
    """auto-editor's release binary, as setup downloads it. It may not exist."""
    return root() / "auto-editor" / "auto-editor"
