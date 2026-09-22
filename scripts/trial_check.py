#!/usr/bin/env python3
"""Judge a test-kit run, for the CI jobs that run one unattended.

Every run it judges is `scripts/setup_trial.py`'s, which writes `report.txt`,
the demo and the frames into its working folder — run directly
(setup-demo.yml) or by either kit, `scripts/mac_trial.sh` and
`scripts/windows_trial.ps1`, which call it (mac-demo.yml, windows-demo.yml).
A kit's own console log is `kit.txt` beside it.

The kit was written for a person, who reads its report. Nothing in it fails:
`verify` and `frames` print their findings and exit 0 whether the render
agrees with the timeline or not, and the kit itself exits 0 after stopping at
a step. So a CI job gated on exit codes would go green on a render with the
retake still in it. This reads what the run printed and what it rendered:

- the log's summary says ALL STEPS RAN;
- `frames` reported `agrees: true`;
- `verify`'s similarity is at least 0.9 — `scripts/agent_trial.py`'s own
  floor, because whisper on another machine mishears the demo voice a word or
  two (this box's dry run heard "are" for "a" at 0.971) and a retake left in
  costs far more than that;
- the render was mastered to DEMO.md § 7's -16 LUFS, read off the master's
  own after-measurement and held to `export --loudness`'s 1 LU band;
- the score is **in the render**, not just in the manifest, which says only
  what a render would carry — `MUSIC_KEY`'s silent-bed trap. The render's
  audio is correlated against `make_demo`'s generated `music.wav` at the
  second the bed was placed and at four wrong seconds. The melody is seeded
  and has no sustained tone, so it matches itself at one offset only. This
  box measured -16.7 dB at the right second against -26.8 at the worst wrong
  one, and a render with no bed -29.4 against -30.5, so the bed must beat its
  wrong seconds by `BED_MARGIN_DB`;
- the frames at 3 s and 10 s are the blue and rust b-roll, judged by mean
  colour against `make_demo.BROLL`'s own colours — DEMO.md § 9's "BLUE 3s" and
  "RUST 0s", the check a person makes by looking. The two colours are ~116
  apart in RGB, and a frame of the wrong clip, or of black, measured 112–116
  from the right one; this box's frames came back within 3.

    python scripts/trial_check.py ~/proofcut-mac-trial
    python scripts/trial_check.py %LOCALAPPDATA%\\proofcut-windows-trial

Exit 1 on any failure. What it cannot say is anything a person would notice
and a number would not — that is what the tester's issue form is for.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_demo import BROLL

SIMILARITY_FLOOR = 0.9
LOUDNESS_TARGET = -16.0
#: `finish.MASTER_LU_TOLERANCE`, restated rather than imported: the kit's own
#: report is judged here, not proofcut's opinion of it.
LOUDNESS_TOLERANCE = 1.0
#: How far the bed's correlation at its own second must beat the best of the
#: wrong seconds. Measured 10.1 dB with the bed and 1.1 dB without it.
BED_MARGIN_DB = 6.0
BED_WRONG_SECONDS = (1.0, 2.0, 3.3, 5.1)
#: The span of the timeline correlated — inside the bed's 1 s fade-in and
#: its 2 s fade-out on DEMO.md's 12 s cut.
BED_WINDOW = (1.2, 9.8)
#: Every audio step decodes at this rate; the melody's top harmonic is 1046 Hz.
BED_RATE = 4000
#: Lag searched either side of each offset, for resampling and codec delay.
BED_SEARCH = 0.06
#: Euclidean RGB distance a frame's mean colour may sit from its clip's colour.
COLOUR_TOLERANCE = 30
FRAMES = [("frame-3s.png", "BLUE"), ("frame-10s.png", "RUST")]


def step_json(log: str, step: str) -> dict | None:
    """The JSON a step printed: the first object after its `── <step>` header."""
    at = log.find(f"── {step}\n")
    if at < 0:
        return None
    brace = log.find("{", at)
    if brace < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(log, brace)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def mean_rgb(png: Path) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(png), "-vf", "scale=1:1:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return raw[0], raw[1], raw[2]


def decode_mono(media: Path) -> array.array:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(BED_RATE),
         "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    return samples


def bed_share(
    render: array.array, music: array.array, offset: float, window: tuple[float, float] = BED_WINDOW
) -> float:
    """The best normalised correlation, in dB, of the render's `window` (render
    seconds) against the score, where render second s is score second s + `offset`.

    `scripts/agent_trial.py --film` asks the same question of an agent's own
    bed, over a window read off its plan, so this is the one implementation.
    """
    i0, i1 = (int(s * BED_RATE) for s in window)
    window = render[i0:i1]
    power = sum(x * x for x in window) or 1
    best = 0.0
    search = int(BED_SEARCH * BED_RATE)
    for lag in range(-search, search + 1):
        j0 = i0 + int(offset * BED_RATE) + lag
        piece = music[j0 : j0 + len(window)] if j0 >= 0 else []
        if len(piece) < len(window):
            continue
        dot = sum(x * y for x, y in zip(window, piece))
        best = max(best, dot * dot / (power * (sum(y * y for y in piece) or 1)))
    return 10 * math.log10(best) if best > 0 else -99.0


def hex_rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def check(trial: Path) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    log_path = trial / "report.txt"
    log = log_path.read_text(errors="replace") if log_path.exists() else ""
    stopped = [line for line in log.splitlines() if line.startswith("STOPPED AT:")]
    results.append(("ALL STEPS RAN" in log, stopped[-1] if stopped else
                    "ALL STEPS RAN" if "ALL STEPS RAN" in log else f"no summary in {log_path}"))

    render = step_json(log, "DEMO 7 render and master (melt)")
    master = ((render or {}).get("loudness") or {}).get("after") or {}
    integrated = master.get("integrated")
    results.append((
        isinstance(integrated, (int, float)) and abs(integrated - LOUDNESS_TARGET) <= LOUDNESS_TOLERANCE,
        (f"master: {integrated} LUFS integrated, true peak {master.get('true_peak')} "
         f"(target {LOUDNESS_TARGET:g} ± {LOUDNESS_TOLERANCE:g})") if master
        else "master: no loudness in the render step's output",
    ))

    video, score = trial / "demo" / "demo.mp4", trial / "demo" / "music.wav"
    pieces = ((render or {}).get("music") or {}).get("pieces") or []
    if not (video.exists() and score.exists() and pieces):
        results.append((False, (f"score: nothing to correlate (render {video.exists()}, "
                                f"music.wav {score.exists()}, bed pieces {len(pieces)})")))
    else:
        # A piece at timeline t plays its asset from src_in, so timeline second s
        # of the render is second s - start + src_in of the score.
        offset = float(pieces[0].get("src_in") or 0.0) - float(pieces[0].get("timeline_start") or 0.0)
        heard, score_samples = decode_mono(video), decode_mono(score)
        right = bed_share(heard, score_samples, offset)
        wrong = max(bed_share(heard, score_samples, offset + s) for s in BED_WRONG_SECONDS)
        results.append((
            right - wrong >= BED_MARGIN_DB,
            (f"score: {right:.1f} dB at its own second, {wrong:.1f} at the best wrong one "
             f"(margin {right - wrong:.1f}, needs {BED_MARGIN_DB:g})"),
        ))

    frames = step_json(log, "DEMO 7 frames")
    if frames is None:
        results.append((False, "frames: no output in the log"))
    else:
        line = (f"frames: agrees {frames.get('agrees')}, delta {frames.get('delta')} "
                f"({frames.get('expected_frames')} expected, {frames.get('target_frames')} in the file)")
        results.append((frames.get("agrees") is True, line))

    verify = step_json(log, "DEMO 7 verify")
    if verify is None:
        results.append((False, "verify: no output in the log"))
    else:
        similarity = verify.get("similarity")
        line = (f"verify: similarity {similarity} (floor {SIMILARITY_FLOOR}), "
                f"{verify.get('heard_words')} heard of {verify.get('expected_words')}, "
                f"{len(verify.get('dropped') or [])} dropped, {len(verify.get('repeated') or [])} repeated")
        results.append((isinstance(similarity, (int, float)) and similarity >= SIMILARITY_FLOOR, line))

    colours = {tag: hex_rgb(colour) for _, colour, tag in BROLL}
    for name, want in FRAMES:
        png = trial / name
        if not png.exists():
            results.append((False, f"{name}: missing"))
            continue
        got = mean_rgb(png)
        near = {tag: distance(got, rgb) for tag, rgb in colours.items()}
        nearest = min(near, key=near.get)
        line = f"{name}: mean rgb {got}, {near[want]:.0f} from {want} (tolerance {COLOUR_TOLERANCE}), nearest {nearest}"
        results.append((nearest == want and near[want] <= COLOUR_TOLERANCE, line))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trial", type=Path, help="the kit's working folder (~/proofcut-mac-trial, %%LOCALAPPDATA%%\\proofcut-windows-trial)")
    args = parser.parse_args()
    results = check(args.trial.expanduser())
    for ok, line in results:
        print(f"{'✓' if ok else '✗'} {line}")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
