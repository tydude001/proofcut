"""One-shot sounds — the generated UI set, and the padded copies melt reads.

docs/plans/NATIVE.md § B4, designed. Two jobs, both stdlib, `energy.py`'s
reason:

- **`generate`** writes the launch clip's UI sounds (`clip.py`'s
  `sfx/make_sfx.py`, ported off numpy): eight soft key ticks, `send`, `land`
  and `strike`. Synthesised here, so there is no licence question and nothing
  vendored — `scripts/make_demo.py`'s precedent. Deterministic: the same
  seed writes the same bytes.
- **`padded_copy`** writes the file a sound lane actually plays. melt has two
  exit-0 traps for a short sound (`~/proofcut-work/spikes/sfx-probe`): a file
  it counts as one frame long plays nothing, and an entry claiming more
  frames than its file has shortens the lane, so every later hit plays
  early. A copy padded to a whole number of frames, at least two, makes the
  frames an entry claims exactly the file's length; its leading silence puts
  the sound between frames, since frame f starts at sample
  `floor(f × SR / fps)` on both melts measured.
"""

from __future__ import annotations

import math
import random
import struct
import subprocess
import wave
from pathlib import Path

#: The rate every copy is written at, and the one melt's consumer mixes at.
SAMPLE_RATE = 48000

#: A copy's lead is rounded to this many samples (1 ms), so a film's hits
#: share a few dozen copies per sound rather than one each; the error is at
#: most half of it, far under anything heard.
LEAD_STEP = SAMPLE_RATE // 1000

#: The longest file taken as a one-shot. A copy is decoded whole into memory,
#: and anything longer is a music cue, which the bed places.
MAX_SECONDS = 30.0


class SoundError(Exception):
    """A one-shot that cannot be decoded or copied."""


# ---- the padded copy ---------------------------------------------------------------


def frame_start(frame: int, rate: tuple[int, int]) -> int:
    """The first sample of timeline frame `frame` at `rate` (num, den)."""
    num, den = rate
    return frame * SAMPLE_RATE * den // num


def place(seconds: float, rate: tuple[int, int]) -> tuple[int, int]:
    """Where a sound at `seconds` goes: `(frame, lead samples)`, lead rounded to 1 ms."""
    num, den = rate
    sample = round(seconds * SAMPLE_RATE)
    frame = max(0, sample * num // (SAMPLE_RATE * den))
    while frame_start(frame + 1, rate) <= sample:
        frame += 1
    while frame > 0 and frame_start(frame, rate) > sample:
        frame -= 1
    lead = round((sample - frame_start(frame, rate)) / LEAD_STEP) * LEAD_STEP
    return frame, lead


def copy_frames(samples: int, lead: int, rate: tuple[int, int], minimum: int) -> int:
    """The whole frames a copy of `samples` behind `lead` of silence needs."""
    num, den = rate
    return max(minimum, math.ceil((lead + samples) * num / (SAMPLE_RATE * den)))


def decode(path: Path | str) -> bytes:
    """A file's audio as 16-bit stereo PCM at `SAMPLE_RATE`."""
    command = [
        "ffmpeg", "-v", "error", "-i", str(path), "-t", str(MAX_SECONDS + 1),
        "-f", "s16le", "-ac", "2", "-ar", str(SAMPLE_RATE), "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        raise SoundError("ffmpeg is not on PATH, and a sound has to be decoded to be placed") from None
    if result.returncode != 0:
        raise SoundError(f"ffmpeg could not decode {path}: {result.stderr.decode(errors='replace').strip()}")
    if not result.stdout:
        raise SoundError(f"{path} decoded to no audio")
    if len(result.stdout) > MAX_SECONDS * SAMPLE_RATE * 4:
        raise SoundError(
            f"{path} runs past {MAX_SECONDS:g}s — a one-shot is short; place longer "
            "sound as a music cue"
        )
    return result.stdout


def padded_copy(pcm: bytes, dest: Path, lead: int, rate: tuple[int, int], minimum: int) -> int:
    """Write `pcm` behind `lead` samples of silence, padded to whole frames.

    Returns the frames the copy is — what its entry claims. The sample count
    is rounded up from the exact frame boundary, so the file's duration is at
    most a sample past it and melt's rounded length is exactly that count.
    """
    num, den = rate
    samples = len(pcm) // 4
    frames = copy_frames(samples, lead, rate, minimum)
    total = math.ceil(frames * SAMPLE_RATE * den / num)
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(".part")
        with wave.open(str(partial), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(bytes(4 * lead) + pcm + bytes(4 * (total - lead - samples)))
        partial.replace(dest)
    return frames


# ---- the generated set ---------------------------------------------------------------

#: What `generate` writes, by file stem — `make_sfx.py`'s set.
GENERATED = tuple(f"key_{i}" for i in range(8)) + ("send", "land", "strike")

_GEN_RATE = 48000


def _onepole(x: list[float], cutoff: float) -> list[float]:
    a = math.exp(-2 * math.pi * cutoff / _GEN_RATE)
    out, acc = [], 0.0
    for v in x:
        acc = (1 - a) * v + a * acc
        out.append(acc)
    return out


def _bandpass(x: list[float], lo: float, hi: float) -> list[float]:
    return [h - low for h, low in zip(_onepole(x, hi), _onepole(x, lo))]


def _noise(rng: random.Random, n: int) -> list[float]:
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


def _key(rng: random.Random, pitch: float, length: float) -> list[float]:
    n = int(_GEN_RATE * length)
    click = _bandpass(_noise(rng, n), 1800, 7000)
    thock = _bandpass(_noise(rng, n), 150, 900)
    out = []
    for i in range(n):
        t = i / _GEN_RATE
        out.append(
            click[i] * math.exp(-t / 0.0035)
            + math.sin(2 * math.pi * pitch * t) * math.exp(-t / 0.012) * 0.35
            + thock[i] * math.exp(-t / 0.02) * 0.5
        )
    return out


def _send(rng: random.Random) -> list[float]:
    n = int(_GEN_RATE * 0.12)
    band = _bandpass(_noise(rng, n), 400, 3000)
    return [
        band[i] * math.exp(-(i / _GEN_RATE) / 0.006)
        + math.sin(2 * math.pi * 180 * i / _GEN_RATE) * math.exp(-(i / _GEN_RATE) / 0.03) * 0.9
        for i in range(n)
    ]


def _land() -> list[float]:
    n = int(_GEN_RATE * 0.9)
    out = []
    for i in range(n):
        t = i / _GEN_RATE
        value = math.sin(2 * math.pi * (70 + 40 * math.exp(-t / 0.03)) * t) * math.exp(-t / 0.09)
        for freq, delay, amp in ((880, 0.0, 0.22), (1318.5, 0.07, 0.16)):
            if t >= delay:
                u = t - delay
                value += (
                    amp
                    * (math.sin(2 * math.pi * freq * u) + 0.3 * math.sin(4 * math.pi * freq * u) * math.exp(-u / 0.05))
                    * math.exp(-u / 0.22)
                    * min(1.0, u / 0.004)
                )
        out.append(value)
    return out


def _strike(rng: random.Random) -> list[float]:
    n = int(_GEN_RATE * 0.35)
    noise = _noise(rng, n)
    bands = [_bandpass(noise, lo, hi) for lo, hi in ((3000, 8000), (1500, 4500), (700, 2500))]
    out = []
    for i in range(n):
        t = i / _GEN_RATE
        value = sum(
            band[i] * math.exp(-(((t - (0.06 + k * 0.08)) / 0.06) ** 2)) for k, band in enumerate(bands)
        )
        out.append(value * min(1.0, t / 0.01))
    return out


def _write_mono(path: Path, x: list[float]) -> None:
    peak = max(abs(v) for v in x) or 1.0
    scale = 10 ** (-3 / 20) / peak  # peaks at -3 dBFS; the record's gain sets the level
    frames = b"".join(struct.pack("<h", max(-32767, min(32767, round(v * scale * 32767)))) for v in x)
    partial = path.with_suffix(".part")
    with wave.open(str(partial), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(_GEN_RATE)
        out.writeframes(frames)
    partial.replace(path)


def generate(dest: Path | str) -> list[Path]:
    """Write the generated UI set into `dest`, one mono WAV per `GENERATED` stem."""
    folder = Path(dest)
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1969)
    written = []
    for i in range(8):
        pitch, length = rng.uniform(260, 420), rng.uniform(0.045, 0.07)
        written.append((f"key_{i}", _key(rng, pitch, length)))
    written.append(("send", _send(rng)))
    written.append(("land", _land()))
    written.append(("strike", _strike(rng)))
    paths = []
    for stem, samples in written:
        path = folder / f"{stem}.wav"
        _write_mono(path, samples)
        paths.append(path)
    return paths
