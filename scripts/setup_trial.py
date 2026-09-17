#!/usr/bin/env python3
"""`proofcut setup` from nothing to a checked render, then back — for CI.

docs/plans/INSTALL.md § Step 5 says an install is judged by a checked render,
never by exit codes. The test kits (`scripts/mac_trial.sh`,
`scripts/windows_trial.ps1`) install with their own code, so a green kit run
says nothing about `proofcut setup`. This runs setup on a runner that has
none of proofcut's tools, then docs/DEMO.md's commands, and writes the same
`report.txt` the kits write, so `scripts/trial_check.py` judges it the same
way. A second run, after the judging (which needs setup's ffmpeg), uninstalls
and compares what setup touched against how the first run found it
(setup-demo.yml).

    uv run python scripts/setup_trial.py WORKDIR               install, demo
    uv run python scripts/setup_trial.py WORKDIR --uninstall   remove, compare

The kits stay as they are until the tester posts have been answered: they
are the instrument those people run.

What stands in for a person:
- `~/.local/bin` goes first on PATH, as uv's installer leaves it on a
  person's machine. The runner's uv came from an action instead.
- espeak-ng, which only the demo's footage needs, is `espeak_ng_lib.py` over
  the `espeakng-loader` wheel, the Intel Mac kit's stand-in, on every OS.

Exit 1 when setup, doctor or the uninstall comparison fails; the demo's own
verdict is trial_check's.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ESPEAK_WHEEL = "espeakng-loader==0.2.4"
RULE = "──"


class Log:
    def __init__(self, path: Path) -> None:
        self.handle = path.open("a", encoding="utf-8")
        self.steps: list[str] = []
        self.failed = ""

    def say(self, line: str) -> None:
        print(line, flush=True)
        self.handle.write(line + "\n")
        self.handle.flush()

    def step(self, what: str, argv: list[str], env: dict[str, str], *, informational: bool = False) -> bool:
        """Run `argv`, its output into the log under a kit-shaped header.

        An informational step's exit code is printed and never a failure.
        """
        self.say("")
        self.say(f"{RULE} {what}")
        self.say("$ " + " ".join(argv))
        started = time.monotonic()
        try:
            done = subprocess.run(
                argv, env=env, cwd=REPO, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            for line in done.stdout.decode("utf-8", "replace").splitlines():
                self.say(line)
            code: int | None = done.returncode
        except OSError as exc:
            self.say(str(exc))
            code = None
        took = f"{int(time.monotonic() - started)}s"
        if informational:
            self.steps.append(f"info  {took}  {what}  (exit {code})")
            return True
        if code == 0:
            self.steps.append(f"ok    {took}  {what}")
            return True
        why = "could not run" if code is None else f"exit {code}"
        self.steps.append(f"FAIL  {took}  {what}  ({why})")
        self.failed = what
        self.say(f"!! failed ({why}) after {took}")
        return False

    def summary(self) -> None:
        self.say("")
        self.say("════ summary")
        self.say(f"proofcut setup trial · {sys.platform}")
        for line in self.steps:
            self.say(f"  {line}")
        self.say(f"STOPPED AT: {self.failed}" if self.failed else "ALL STEPS RAN")


def espeak_shim(folder: Path) -> None:
    """An `espeak-ng` on PATH that is the library, as mac_trial.sh's Intel route has."""
    folder.mkdir(parents=True, exist_ok=True)
    command = ["uv", "run", "--no-project", "--python", "3.12", "--with", ESPEAK_WHEEL,
               "python", str(REPO / "scripts" / "espeak_ng_lib.py")]
    if os.name == "nt":
        (folder / "espeak-ng.cmd").write_text(
            "@echo off\r\n" + " ".join(f'"{part}"' for part in command) + " %*\r\n", encoding="ascii"
        )
    else:
        shim = folder / "espeak-ng"
        shim.write_text("#!/bin/sh\nexec " + " ".join(f"'{part}'" for part in command) + ' "$@"\n', encoding="utf-8")
        shim.chmod(0o755)


def touched(env: dict[str, str]) -> dict[str, list[str]]:
    """What setup may add, listed: its folder, ~/.local/bin, and uv's tools and Pythons."""
    from proofcut import deps, install

    def listing(folder: Path | None) -> list[str]:
        return sorted(p.name for p in folder.iterdir()) if folder and folder.is_dir() else []

    uv_dirs = {}
    for which in ("tool", "python"):
        done = subprocess.run(["uv", which, "dir"], env=env, capture_output=True, text=True, check=False)
        uv_dirs[which] = Path(done.stdout.strip()) if done.returncode == 0 else None
    return {
        "deps": listing(deps.root()),
        "bin": listing(install.bin_dir()),
        "uv tools": listing(uv_dirs["tool"]),
        "uv pythons": listing(uv_dirs["python"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, help="an empty folder for the demo and the report")
    parser.add_argument("--uninstall", action="store_true", help="uninstall, and compare against the first run's listing")
    args = parser.parse_args()
    work = args.work.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)

    from proofcut import install

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env["PATH"] = os.pathsep.join([str(install.bin_dir()), str(work / "shim"), env["PATH"]])
    proofcut = [sys.executable, "-m", "proofcut.cli"]
    snapshot = work / "before-setup.json"

    if args.uninstall:
        log = Log(work / "uninstall.txt")
        before = json.loads(snapshot.read_text(encoding="utf-8"))
        uninstalled = log.step("proofcut setup --uninstall", [*proofcut, "setup", "--uninstall", "--yes"], env)
        after = touched(env)
        log.say(f"before setup:    {before}")
        log.say(f"after uninstall: {after}")
        if after != before:
            log.say("!! the uninstall left these different from how setup found them")
        return 0 if uninstalled and after == before else 1

    log = Log(work / "report.txt")
    espeak_shim(work / "shim")
    # Before the listing: the shim's first run may download a Python 3.12,
    # which is the demo's doing and would read as setup leaving one behind.
    espeak = shutil.which("espeak-ng", path=env["PATH"]) or "espeak-ng"
    if not log.step("espeak-ng runs from the library", [espeak, "-w", str(work / "espeak-check.wav"), "-s", "150", "one two three"], env):
        log.summary()
        return 1
    before = touched(env)
    snapshot.write_text(json.dumps(before), encoding="utf-8")
    log.say(f"before setup: {before}")

    log.step("proofcut setup --plan", [*proofcut, "setup", "--plan"], env, informational=True)
    ok = log.step("proofcut setup", [*proofcut, "setup", "--yes"], env)
    ok = log.step("proofcut doctor", [*proofcut, "doctor"], env) and ok

    demo, proj = work / "demo", work / "demo" / "proj"
    project = [*proofcut, "-C", str(proj)]
    steps = [
        ("DEMO 1 make the footage", [sys.executable, "scripts/make_demo.py", str(demo)]),
        ("DEMO 2 init", [*proofcut, "init", str(proj)]),
        ("DEMO 2 import vo", [*project, "import", str(demo / "vo.wav"), "--clip-id", "vo"]),
        ("DEMO 2 import blue", [*project, "import", str(demo / "broll-blue.mp4"), "--clip-id", "blue"]),
        ("DEMO 2 import rust", [*project, "import", str(demo / "broll-rust.mp4"), "--clip-id", "rust"]),
        ("DEMO 2 transcribe", [*project, "transcribe", "vo"]),
        ("DEMO 2 seed", [*project, "seed", "vo"]),
        ("DEMO 4 cut", [*project, "cut", "vo", "11:23", "--pad", "0.1"]),
        ("DEMO 5 cue blue", [*project, "cue", "add", "vo", "--phrase", "Every cut you make names a word", "blue"]),
        ("DEMO 5 cue rust", [*project, "cue", "add", "vo", "--phrase", "the render can be checked", "rust"]),
        ("DEMO 6 import the score", [*project, "import", str(demo / "music.wav"), "--clip-id", "score"]),
        ("DEMO 6 score it", [*project, "music", "--asset", "score", "--clip-id", "vo", "--start-word", "0",
                             "--fade-in", "1", "--fade-out", "2", "--under", "18"]),
        ("DEMO 7 render and master (melt)", [*project, "export", str(demo / "demo.mp4"), "--render", "--loudness", "-16"]),
        ("DEMO 7 verify", [*project, "verify", str(demo / "demo.mp4")]),
        ("DEMO 7 frames", [*project, "frames", str(demo / "demo.mp4")]),
    ]
    if ok:
        for what, argv in steps:
            if not log.step(what, argv, env):
                break
    video = demo / "demo.mp4"
    if video.is_file():
        for second in (3, 10):
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", str(second), "-i", str(video), "-frames:v", "1",
                 str(work / f"frame-{second}s.png")],
                env=env, check=False,
            )
    log.summary()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
