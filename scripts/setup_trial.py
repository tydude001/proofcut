#!/usr/bin/env python3
"""`proofcut setup` from nothing to a checked render, then back — for CI.

docs/plans/INSTALL.md § Step 5 says an install is judged by a checked render,
never by exit codes. This runs setup on a machine that has none of
proofcut's tools, then docs/DEMO.md's commands, and writes `report.txt`, which
`scripts/trial_check.py` judges. A second run, after the judging (which needs
setup's ffmpeg), uninstalls and compares what setup touched against how the
first run found it.

    uv run python scripts/setup_trial.py WORKDIR               install, demo
    uv run python scripts/setup_trial.py WORKDIR --uninstall   remove, compare

**It is the one install-and-demo path.** setup-demo.yml runs it directly on
a runner that already has uv. The test kits (`scripts/mac_trial.sh`,
`scripts/windows_trial.ps1`) run it too, after fetching a pinned uv, so a
person's kit run exercises `proofcut setup` itself and the kits carry no
install code or pins of their own (HISTORY.md § The kits call setup).
Each step's output is streamed as it arrives, because a person is watching
the 2 GB whisper install.

What stands in for a person:
- `~/.local/bin` goes first on PATH, as uv's installer leaves it on a
  person's machine. The runner's uv came from an action instead.
- espeak-ng, which only the demo's footage needs, is `espeak_ng_lib.py` over
  the `espeakng-loader` wheel, on every OS.

Exit 1 when any step fails — setup, doctor, a DEMO command — or the
uninstall comparison does. A DEMO command exiting 0 is not a good render,
which is trial_check's verdict to give; but a person running a kit reads the
kit's last line, and a demo that stopped must not end on ALL STEPS RAN.
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
            with subprocess.Popen(
                argv, env=env, cwd=REPO, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            ) as proc:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    self.say(raw.decode("utf-8", "replace").rstrip("\r\n"))
                code: int | None = proc.wait()
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
    """An `espeak-ng` on PATH that is the library: no Intel Mac program exists
    outside a package manager, and the wheel's output is byte-identical to the
    1.52.0 program's (`espeak_ng_lib.py`)."""
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
        ("DEMO 3 find the retake", [*project, "transcript", "vo", "--search", "let me try that again"]),
        ("DEMO 3 read around it", [*project, "transcript", "vo", "--first", "8", "--last", "26"]),
        ("DEMO 4 cut --plan", [*project, "cut", "vo", "11:23", "--plan"]),
        ("DEMO 4 cut", [*project, "cut", "vo", "11:23", "--pad", "0.1"]),
        ("DEMO 5 cue blue", [*project, "cue", "add", "vo", "--phrase", "Every cut you make names a word", "blue"]),
        ("DEMO 5 cue rust", [*project, "cue", "add", "vo", "--phrase", "the render can be checked", "rust"]),
        ("DEMO 5 shots", [*project, "shots"]),
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
    # Resolved against the children's PATH: Windows looks a bare name up on
    # this process's own PATH, which lacks ~/.local/bin (the first Windows run).
    ffmpeg = shutil.which("ffmpeg", path=env["PATH"])
    if video.is_file() and ffmpeg:
        for second in (3, 10):
            subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-ss", str(second), "-i", str(video), "-frames:v", "1",
                 str(work / f"frame-{second}s.png")],
                env=env, check=False,
            )
    log.summary()
    return 0 if ok and not log.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
