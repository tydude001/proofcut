#!/usr/bin/env python3
"""Render one project's MLT document through several melts, many times, and
count how each one stopped.

This is the A/B half of `.github/workflows/melt-soak.yml`: it measures a
patched MLT against its own unpatched base on the same runner, renders
interleaved so that neither arm gets the quieter half of the job.

**It renders the document proofcut writes, not the one proofcut renders.**
`mlt.write` puts `LC_NUMERIC="C"` on the root and `picture.render_document`
strips it, because a locale on any service sends MLT through the per-property
`setlocale` that crashed Shotcut's Mac melt (mltframework/mlt#1326, HISTORY.md
§ The Mac melt crash, found). A test of a fix for that crash has to keep the
attribute, and has to see every crash, so melt is run directly here: no strip,
and none of `picture.render`'s re-runs after a SIGSEGV.

    melt_soak.py PROJECT OUT --renders 50 --melt fix=/path/melt --melt master=/path/melt

A `--melt` value is split like a shell word list, so a flatpak melt works
too. The summary goes to stdout and `OUT/summary.txt`. A crashed render's
output is kept as `OUT/<arm>-<n>.log`. The exit is 0 whatever the count,
because this is a measurement, not a gate.
"""

from __future__ import annotations

import argparse
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from proofcut import mlt, ops, picture
from proofcut.project import Project


def write_document(project_dir: Path, out: Path) -> Path:
    """The project's MLT document, written the way `export --render` writes it."""
    project = Project.open(project_dir)
    built = ops._build_mlt(project, ops._load_edit(project), fps=None)
    document = mlt.write(built["document"], out / "soak.mlt")
    root = document.read_bytes()[:4096]
    if b'LC_NUMERIC="' not in root:
        sys.exit(f"{document} names no LC_NUMERIC on its root, so it cannot reach the setlocale path")
    return document


def how(returncode: int) -> str:
    if returncode == 0:
        return "ok"
    if returncode < 0:
        try:
            return signal.Signals(-returncode).name
        except ValueError:
            return f"signal {-returncode}"
    return f"exit {returncode}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--renders", type=int, default=50, help="renders per melt")
    parser.add_argument("--melt", action="append", required=True, metavar="ARM=COMMAND")
    args = parser.parse_args()

    arms = {}
    for given in args.melt:
        name, _, command = given.partition("=")
        if not command:
            parser.error(f"--melt {given!r} is not ARM=COMMAND")
        arms[name] = shlex.split(command)

    args.out.mkdir(parents=True, exist_ok=True)
    document = write_document(args.project, args.out)
    env = picture.numeric_c_env(picture.display_env())
    summary = args.out / "summary.txt"
    tally: dict[str, dict[str, int]] = {name: {} for name in arms}

    def line(text: str) -> None:
        print(text, flush=True)
        with summary.open("a") as handle:
            handle.write(text + "\n")

    line(f"document {document}, {args.renders} renders per arm, interleaved")
    for i in range(1, args.renders + 1):
        for name, command in arms.items():
            rendered = args.out / f"{name}.mp4"
            start = time.monotonic()
            completed = subprocess.run(
                [*command, str(document), "-consumer", f"avformat:{rendered}", *picture.RENDER_ARGS],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            outcome = how(completed.returncode)
            tally[name][outcome] = tally[name].get(outcome, 0) + 1
            if outcome != "ok":
                (args.out / f"{name}-{i}.log").write_text(completed.stdout + completed.stderr)
            line(f"{i} {name} {outcome} {time.monotonic() - start:.1f}s")

    for name, counts in tally.items():
        crashed = sum(n for outcome, n in counts.items() if outcome != "ok")
        detail = ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))
        line(f"{name}: {crashed} of {args.renders} renders failed ({detail})")


if __name__ == "__main__":
    main()
