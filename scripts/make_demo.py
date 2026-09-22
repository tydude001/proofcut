#!/usr/bin/env python3
"""Build the demo footage `docs/DEMO.md` walks through — nothing is vendored.

The README quickstart assumes you have a voiceover with retakes lying around.
Most people do not, and a demo you can run in two minutes is the difference
between reading about proofcut and using it (docs/plans/POLISH.md § Step 02).

**Everything here is generated, and that is the design.** The repo carries no
media at all: the voiceover is synthesised from a script written a few lines
below, and the b-roll is ffmpeg's own test sources. Nothing to licence,
nothing to keep in step with an upstream, and the repo does not grow. It also
means the demo is *inspectable* — you can read exactly what the narrator is
about to say, including the fluff the walkthrough cuts out.

The voiceover carries a **deliberate retake**: the narrator starts a sentence,
stops, and says it again. That is the thing proofcut was built to remove, and
cutting it is what `docs/DEMO.md` does. The two takes are rendered separately
and joined with real silence between them, exactly the way a retake sits in a
real recording — which is what makes the cut land in silence rather than
clipping a consonant.

The b-roll is two clips that could not be confused for each other and whose
every second — and every corner — names itself: a burnt-in source-second
counter, a grid, and `TL`/`TR`/`BL`/`BR` tags. That is the repo's own "every
moment names itself" precedent (CLAUDE.md) extended one step, because framing
asks a question a counter cannot answer: a crop window that keeps all four
corners is not cropping. Real footage screenshots better; synthetic footage is
what ships because it costs nobody a licence review.

The score is a plucked pentatonic melody written with `wave` from a fixed seed
(`make_music`), so the demo can be scored and mastered as well as cut, and so a
render can be checked for the bed at the second it should be playing.

    python scripts/make_demo.py ~/proofcut-demo          # just the media
    python scripts/make_demo.py ~/proofcut-demo --build  # ...and a seeded project

`--build` runs the same `proofcut` commands `docs/DEMO.md` lists, so a reader can
skip ahead or check their own run against it.
"""

from __future__ import annotations

import argparse
import array
import math
import random
import shutil
import subprocess
import sys
import wave
from pathlib import Path

#: The narration, in the order it is spoken. `retake=True` marks the fluffed
#: take — the one `docs/DEMO.md` cuts. Kept as data rather than prose in a
#: string so the walkthrough can quote the exact words the transcript will
#: hold, and so a reader can see what is about to be removed before it is.
SCRIPT: list[tuple[str, bool]] = [
    ("This is a demo of proofcut, a local first video editor.", False),
    ("Every cut you make names a, hmm, no, let me try that again.", True),
    ("Every cut you make names a word in the transcript.", False),
    ("So the edit stays addressable, and the render can be checked against it.", False),
]

#: Seconds of silence between takes. Long enough that auto-editor's silence
#: pass has something to find and that a cut boundary lands in quiet rather
#: than on a consonant, short enough that the demo is not mostly waiting.
GAP = 0.6

#: The espeak-ng library as a wheel, for a machine whose package manager has no
#: espeak-ng — or whose owner would rather not be asked for a sudo password in
#: the middle of a two-minute demo. Pinned to the version
#: `scripts/setup_trial.py`'s shim pins, which carries the same library version
#: as the program (1.52.0), and that is the whole point: through
#: `scripts/espeak_ng_lib.py` the two render a **byte-identical** voiceover
#: (measured 2026-09-20 on Linux against system espeak-ng 1.52.0: the whole
#: 825,100-byte wav, same md5), so the walkthrough's word count and timings
#: hold whichever route built the voice.
#: It ships for Linux x86_64/aarch64, Windows x64/ARM64 and both Macs.
ESPEAK_WHEEL = "espeakng-loader==0.2.4"

#: The two b-roll clips. Flat, unmistakable colours with a burnt-in second
#: counter, so a frame of the finished render says which clip it came from and
#: how far into it — the "every moment names itself" rule.
BROLL = [
    ("broll-blue.mp4", "#1b3a5c", "BLUE"),
    ("broll-rust.mp4", "#7a3218", "RUST"),
]
#: Long enough that either clip can carry a whole shot of the demo cut. A
#: shorter one is not wrong — `plan_picture` refuses a shot longer than its
#: asset rather than rewinding, which is the right behaviour and a bad first
#: five minutes.
BROLL_SECONDS = 12
BROLL_SIZE = "640x360"
BROLL_FPS = 24

#: The score. Long enough to run under the whole cut and an end card after it,
#: so the bed never pads out with silence in the walkthrough.
MUSIC_SECONDS = 24
MUSIC_RATE = 22050
#: A minor pentatonic over two octaves — nothing in it can clash with anything
#: else in it, which is the whole of the composition.
MUSIC_NOTES = [220.0, 261.63, 293.66, 329.63, 392.0, 440.0, 523.25]
#: Seeds the melody, so every machine generates the same score and
#: `scripts/trial_check.py` can look for it in a render.
MUSIC_SEED = 7


class DemoError(RuntimeError):
    """A dependency is missing, or a generation step failed."""


def _require(binary: str, why: str, install: str) -> str:
    found = shutil.which(binary)
    if found is None:
        raise DemoError(f"{binary} is not on PATH — {why}. {install}")
    return found


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[-600:]
        raise DemoError(f"{command[0]} failed:\n{' '.join(command)}\n{detail}")


def _espeak_command() -> list[str]:
    """The command prefix answering `-w OUT -s RATE TEXT`, however espeak-ng is available.

    espeak-ng is the demo's one dependency `proofcut setup` cannot supply: it
    is a distribution package everywhere, and a sudo prompt in the middle of a
    two-minute demo is the wrong first impression for a tool whose claim is
    that it installs for you alone and reverses exactly. So the program is
    preferred where it exists, and the wheel's copy of the same library stands
    in where it does not — the route the Intel Mac kit took before it called
    setup, since no package manager will serve an Intel Mac.

    `--no-project` keeps the wheel out of proofcut's own environment, which is
    `scripts/espeak_ng_lib.py`'s own rule: nothing under `src/` imports it, and
    the demo is the library's one use in this repo.
    """
    found = shutil.which("espeak-ng")
    if found is not None:
        return [found]
    uv = shutil.which("uv")
    if uv is None:
        raise DemoError(
            "neither espeak-ng nor uv is on PATH, and the demo voiceover is "
            "synthesised rather than vendored. Either install uv "
            "(https://docs.astral.sh/uv/), which lets this script fetch the "
            f"espeak-ng library itself ({ESPEAK_WHEEL}, about 10 MB, into uv's "
            "cache and nowhere else), or install the program (`dnf install "
            "espeak-ng`, `apt install espeak-ng`, `brew install espeak-ng`)."
        )
    library = Path(__file__).resolve().parent / "espeak_ng_lib.py"
    if not library.is_file():
        raise DemoError(
            f"espeak-ng is not on PATH and its stand-in is missing: {library}. "
            "Run this script from a proofcut checkout."
        )
    print(
        f"espeak-ng is not on PATH — using its library from {ESPEAK_WHEEL} "
        "instead (about 10 MB, fetched once into uv's cache). It is the same "
        "library version as the program, and renders the same voice.",
        flush=True,
    )
    return [uv, "run", "--no-project", "--with", ESPEAK_WHEEL, "python", str(library)]


def make_voiceover(out: Path) -> Path:
    """Render the script to one wav, with a real gap at every take boundary.

    Each line is synthesised on its own and the silences are inserted between
    them, rather than letting the synthesiser run the whole script: a
    text-to-speech pause is a comma's worth of breath, and a retake seam is
    the speaker stopping. The difference is exactly what the demo's cut needs
    to land in.
    """
    espeak = _espeak_command()
    _require("ffmpeg", "every media step goes through it", "Install ffmpeg.")

    work = out.parent / "_demo-parts"
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for index, (line, _retake) in enumerate(SCRIPT):
        raw = work / f"line{index}.wav"
        # `-s 150` is close to an unhurried read; the default gabbles.
        _run([*espeak, "-w", str(raw), "-s", "150", line])
        if index:
            silence = work / f"gap{index}.wav"
            _run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono:d={GAP}",
                "-c:a", "pcm_s16le", str(silence),
            ])  # fmt: skip
            parts.append(silence)
        parts.append(raw)

    listing = work / "parts.txt"
    listing.write_text(
        "".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8"
    )
    # Resampled to one rate on the way out: espeak-ng and the silence source
    # can disagree, and concat demuxing files that disagree is a fast way to
    # a wav whose declared duration is not its real one.
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", str(out),
    ])  # fmt: skip
    shutil.rmtree(work, ignore_errors=True)
    return out


def _corner(tag: str, x: str, y: str) -> str:
    return (
        f"drawtext=text='{tag}':font=sans:fontsize=20:fontcolor=white@0.55:x={x}:y={y}"
    )


def make_broll(directory: Path) -> list[Path]:
    """Two clips nobody could mix up, where every second *and every corner*
    names itself.

    Three marks, each earning its place:

    * the **centred counter** is the source second, which is the number a
      cue's `src_start` and a contact sheet's label both quote — so a frame of
      the render says how far into its clip it is;
    * the **grid** gives a flat colour something a crop can be measured
      against, which is what makes `reframe`'s windows legible on footage
      nobody shot;
    * the **corner tags** are the crop tell. A window that keeps all four is
      not cropping; one showing `TL`/`BL` alone has taken the left half. On
      real footage you judge a crop by whether the subject survived, and there
      is no subject here — so the frame is built to answer the question
      instead.
    """
    _require("ffmpeg", "every media step goes through it", "Install ffmpeg.")
    made = []
    for name, colour, label in BROLL:
        dest = directory / name
        # `%{eif:t:d}` is ffmpeg's own frame-time expression, and the colons in
        # it are escaped because a filter argument is colon-separated.
        chain = ",".join([
            "drawgrid=w=80:h=80:t=1:c=white@0.10",
            _corner("TL", "12", "10"),
            _corner("TR", "w-tw-12", "10"),
            _corner("BL", "12", "h-th-10"),
            _corner("BR", "w-tw-12", "h-th-10"),
            (
                f"drawtext=text='{label} %{{eif\\:t\\:d}}s':font=sans:fontsize=48:"
                "fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2"
            ),
        ])
        _run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"color=c={colour}:s={BROLL_SIZE}:r={BROLL_FPS}:d={BROLL_SECONDS}",
            "-vf", chain,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ])  # fmt: skip
        made.append(dest)
    return made


def make_music(out: Path) -> Path:
    """A plucked pentatonic melody, written with `wave`.

    Music has to be generated for the same reason the voice is: nothing is
    vendored. It is built from the standard library rather than an ffmpeg
    `sine` chord because it has to be **findable in a render**. A held chord
    repeats every period, so it correlates with a render at the wrong second
    as well as the right one, and a check comparing the two would learn
    nothing. A melody whose notes are drawn from a seeded generator matches
    itself at one offset only — the same idea as the b-roll's burnt-in
    counter, for sound (`scripts/trial_check.py`'s bed check).
    """
    rng = random.Random(MUSIC_SEED)
    total = MUSIC_SECONDS * MUSIC_RATE
    samples = [0.0] * total
    # No drone under it: a sustained tone matches the render at every second
    # it is held, and measured a wrong-second control within 7 dB of the right
    # one. The melody alone carries the identity.
    at = 0.0
    while at < MUSIC_SECONDS:
        length = rng.choice([0.25, 0.25, 0.5, 0.5, 0.75])
        freq = rng.choice(MUSIC_NOTES)
        start = int(at * MUSIC_RATE)
        ring = int(min(length * 2.5, MUSIC_SECONDS - at) * MUSIC_RATE)
        for k in range(ring):
            t = k / MUSIC_RATE
            envelope = math.exp(-4.0 * t) * min(1.0, t / 0.005)
            samples[start + k] += 0.22 * envelope * (
                math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t)
            )
        at += length
    peak = max(abs(s) for s in samples) or 1.0
    frames = array.array("h", (int(32000 * s / peak) for s in samples))
    if sys.byteorder == "big":
        frames.byteswap()
    with wave.open(str(out), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(MUSIC_RATE)
        handle.writeframes(frames.tobytes())
    return out


def build_project(root: Path, media: Path) -> None:
    """Run the walkthrough's own commands, so `--build` and DEMO.md cannot drift."""
    proofcut = [sys.executable, "-m", "proofcut.cli"]
    steps = [
        [*proofcut, "init", str(root)],
        [*proofcut, "-C", str(root), "import", str(media / "vo.wav"), "--clip-id", "vo"],
        [*proofcut, "-C", str(root), "import", str(media / "broll-blue.mp4"), "--clip-id", "blue"],
        [*proofcut, "-C", str(root), "import", str(media / "broll-rust.mp4"), "--clip-id", "rust"],
        [*proofcut, "-C", str(root), "transcribe", "vo"],
        [*proofcut, "-C", str(root), "seed", "vo"],
    ]
    for step in steps:
        # Echoed as the command `docs/DEMO.md` prints, not as the argv this
        # runs: the interpreter prefix is how proofcut is reached without an
        # activated venv, and printing `step[2:]` left the line starting
        # `lucid.cli init …`, which is not a command anybody can type. Caught
        # on the first fresh-checkout dry run, which is what that rehearsal is
        # for (HISTORY.md § The closed-loop trial).
        shown = ["proofcut", *step[len(proofcut):]] if step[: len(proofcut)] == proofcut else step
        print("  $", " ".join(shown))
        _run(step)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dest", help="directory to write the demo media into")
    parser.add_argument(
        "--build",
        action="store_true",
        help="also create and seed a proofcut project from it (needs whisper and auto-editor)",
    )
    args = parser.parse_args(argv)

    media = Path(args.dest).expanduser()
    media.mkdir(parents=True, exist_ok=True)
    try:
        print(f"voiceover  -> {media / 'vo.wav'}")
        make_voiceover(media / "vo.wav")
        for clip in make_broll(media):
            print(f"b-roll     -> {clip}")
        print(f"music      -> {media / 'music.wav'}")
        make_music(media / "music.wav")
        if args.build:
            root = media / "proj"
            print(f"project    -> {root}")
            build_project(root, media)
            print(f"\nOpen it:  proofcut -C {root} open")
        else:
            print(f"\nNext:  {Path(__file__).parent.parent / 'docs' / 'DEMO.md'}")
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
