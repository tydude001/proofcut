"""The energy envelope — the one arbiter a transcript cannot outvote.

Every other check proofcut runs believes a transcript about where the words are.
That belief has a documented ceiling: whisper collapses an immediate retake and
hands the *following* word a duration long enough to swallow it, so a whole
second reading of a sentence can sit inside what the transcript calls one word
(HISTORY.md § 2). On the Scream VO the worst case was a 4.12 s stretch between two
words holding 2.4 s of speech, and every transcript of that file — single pass,
windowed, `small`, `medium` — either merged it or wrote it down as silence.

The audio does not lie about it. Mask the waveform with the word map and
whatever audible energy is left over in the holes is *something*: a noise, or a
take the transcript dropped. This module measures that, and nothing else
decides what it means.

Two things worth knowing before reading a result:

- **The threshold calibrates off the file itself**, halfway in dB between its
  quiet tenth and the median level inside a word. Nothing here is an absolute
  dBFS number, because a VO stem and a scored render sit 20 dB apart and a
  fixed floor would be wrong on one of them.
- **A music bed raises the quiet end**, which is exactly what that
  self-calibration is for — but the bed is not flat, and a swell in a long pause
  can still clear the midpoint. Treat a reported gap as somewhere to listen,
  never as a verdict.

Stdlib only, on purpose: `audioop` went in 3.13 and numpy is not a dependency
proofcut carries for one RMS loop.
"""

from __future__ import annotations

import array
import itertools
import json
import math
import re
import statistics
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

FFMPEG = "ffmpeg"

#: Speech puts its energy under 4 kHz — fundamentals and the first two formants
#: — so 8 kHz keeps everything the question "is that a voice?" needs, and makes
#: the per-frame arithmetic below half what decoding at 16 kHz would cost.
RATE = 8000

#: 20 ms per envelope frame: shorter than any syllable, longer than a glottal
#: pulse, so a frame reads as either sound or not-sound rather than averaging
#: the two together.
FRAME = 0.02

#: Only gaps this long are asked about. Shorter ones are the ordinary pause
#: between two words, and there is nowhere in them for a retake to hide.
MIN_GAP = 1.0

#: And a gap has to hold this much sound to be worth a human's attention. A
#: click, a breath, or a chair creak does not reach it.
MIN_SOUND = 0.4

#: Where the threshold sits between the quiet tenth and speech level. 0.5 was
#: chosen to be obviously halfway rather than tuned — a real take clears it by
#: a wide margin, and tuning it against one video would be overfitting to that
#: video's noise floor.
THRESHOLD = 0.5

#: How far past the median a word's *duration* is believed when it is used to
#: mask the audio. Beyond this the word is masked for its first `CAP x median`
#: and the rest of its claimed span is treated as a hole to be measured.
#:
#: Without this the module cannot see the failure it was written for. A
#: collapsed retake does not leave a gap in the transcript — it inflates the
#: following word until that word's duration *covers* the second take, which is
#: the whole reason a diff cannot find it. Masking by the claimed span therefore
#: masks the evidence: on the Scream VO the word "bit" claims 3.96 s with a
#: complete second reading of its sentence inside, and believing it hides
#: exactly the 4.12 s hole that the method says cannot hide.
#:
#: 3x is the same multiple HISTORY § 2 flags suspect durations at, and the two
#: are the same observation: no word is three times the median long, so whatever
#: is in there is not the word.
CAP = 3.0

#: 16-bit full scale, for converting RMS to dBFS.
FULL_SCALE = 32768.0


class EnergyError(Exception):
    """Raised when audio cannot be decoded or the envelope cannot be judged."""


def decode(media: Path | str, *, rate: int = RATE) -> array.array:
    """Decode `media` to mono 16-bit PCM samples at `rate`.

    Straight off ffmpeg's stdout rather than through a temp file — the envelope
    is the only consumer and it wants the samples in memory anyway. A five
    minute render is ~4.8 MB at the default rate.
    """
    source = Path(media).expanduser()
    if not source.exists():
        raise EnergyError(f"no media to measure: {source}")

    cmd = [
        FFMPEG,
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(rate),
        "-f",
        "s16le",
        "-",
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise EnergyError(f"{FFMPEG} not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise EnergyError(f"ffmpeg could not decode {source.name}: {detail}") from exc

    raw = completed.stdout
    samples = array.array("h")
    # A truncated final sample would raise rather than be dropped, and a torn
    # last 20 ms is not worth failing a verify over.
    samples.frombytes(raw[: len(raw) - len(raw) % samples.itemsize])
    if not samples:
        raise EnergyError(
            f"{source.name} decoded to no audio at all — it has no audio track, "
            "or the track is empty"
        )
    return samples


def envelope(samples: Sequence[int], *, rate: int = RATE, frame: float = FRAME) -> list[float]:
    """RMS per fixed-length frame, in raw sample units.

    A trailing partial frame is dropped: it would be measured over fewer
    samples than every other frame and so read quieter than it is.
    """
    width = max(1, round(rate * frame))
    return [
        math.sqrt(sum(s * s for s in samples[i : i + width]) / width)
        for i in range(0, len(samples) - width + 1, width)
    ]


def _db(rms: float) -> float:
    """dBFS for an RMS in raw sample units, floored so silence is finite."""
    return 20.0 * math.log10(max(rms, 1e-6) / FULL_SCALE)


def _mask(spans: Sequence[tuple[float, float]], count: int, frame: float) -> list[bool]:
    """Which envelope frames a word covers."""
    covered = [False] * count
    for start, end in spans:
        lo = max(0, int(start / frame))
        hi = min(count, math.ceil(end / frame))
        for i in range(lo, hi):
            covered[i] = True
    return covered


def _runs(loud: Sequence[bool], lo: int, hi: int) -> list[tuple[int, int]]:
    """Contiguous above-threshold frame runs within [lo, hi)."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(lo, hi):
        if loud[i] and start is None:
            start = i
        elif not loud[i] and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, hi))
    return out


def _median_limit(spans: Sequence[tuple[float, float]], cap: float) -> float:
    durations = sorted(end - start for start, end in spans)
    return cap * durations[len(durations) // 2]


def believable(spans: Sequence[tuple[float, float]], *, cap: float = CAP) -> list[tuple[float, float]]:
    """Trim each span to a duration a single word could plausibly have.

    A word's *order* is reliable and its *duration* is not (CLAUDE.md), so the
    mask is built from what a word could have covered rather than what the
    transcript claims it did. See `CAP`.
    """
    if not spans:
        return []
    limit = _median_limit(spans, cap)
    return [(start, min(end, start + limit)) for start, end in spans]


def suspect_durations(
    spans: Sequence[tuple[float, float]], *, cap: float = CAP
) -> list[dict[str, Any]]:
    """Which spans, by index, claim more than `cap` times the median duration.

    The same rule `believable` masks by (see `CAP`), surfaced as a finding
    instead of only ever being consumed silently downstream. No word is
    legitimately three times the median word long, so whatever a span this
    long covers is not just the word — usually a swallowed retake
    (HISTORY.md § 2). HISTORY.md § Suspect word durations at `attach-transcript`.
    """
    if not spans:
        return []
    limit = _median_limit(spans, cap)
    return [
        {
            "index": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "limit": round(limit, 3),
        }
        for i, (start, end) in enumerate(spans)
        if end - start > limit
    ]


def loud_gaps(
    spans: Sequence[tuple[float, float]],
    env: Sequence[float],
    *,
    frame: float = FRAME,
    min_gap: float = MIN_GAP,
    min_sound: float = MIN_SOUND,
    threshold: float = THRESHOLD,
    cap: float = CAP,
) -> dict[str, Any]:
    """Find holes in the word map that the audio says are not empty.

    `spans` is the word map — (start, end) per word, in the same clock as the
    envelope. It is trimmed by `believable` first: a word that claims four
    seconds is masking a hole rather than filling one.

    Returns the calibration it used alongside the gaps, because a result whose
    numbers cannot be checked is not evidence of anything.
    """
    if not env:
        raise EnergyError("no envelope to measure — the audio decoded to nothing")
    if not spans:
        raise EnergyError(
            "no words to mask the audio with, so every frame is a gap and the "
            "threshold has no speech to calibrate against"
        )

    claimed, spans = spans, believable(spans, cap=cap)
    doubted = sum(1 for a, b in zip(claimed, spans, strict=True) if b[1] < a[1])
    covered = _mask(spans, len(env), frame)
    speech = [_db(env[i]) for i, hit in enumerate(covered) if hit]
    if not speech:
        raise EnergyError(
            "the word map lands outside the audio entirely — the transcript and "
            "the media are not the same recording, or not the same clock"
        )

    # The quiet tenth of the *whole* file, not of the gaps: a file whose gaps
    # are all full of a retake would otherwise calibrate its floor off the
    # retake and then find nothing.
    ordered = sorted(_db(v) for v in env)
    quiet_db = ordered[len(ordered) // 10]
    speech_db = statistics.median(speech)
    threshold_db = quiet_db + threshold * (speech_db - quiet_db)

    loud = [_db(v) >= threshold_db for v in env]
    gaps: list[dict[str, Any]] = []

    # Between words only. The head and tail of a render legitimately hold a
    # title card, a music sting or a bed tail, and flagging those every time
    # would train a reader to skip the whole field.
    ordered_spans = sorted(spans)
    for (_, gap_start), (gap_end, _) in itertools.pairwise(ordered_spans):
        if gap_end - gap_start < min_gap:
            continue
        lo = min(len(env), math.ceil(gap_start / frame))
        hi = min(len(env), int(gap_end / frame))
        runs = _runs(loud, lo, hi)
        sound = sum(b - a for a, b in runs) * frame
        if sound < min_sound:
            continue
        longest = max(runs, key=lambda r: r[1] - r[0])
        gaps.append(
            {
                "start": round(gap_start, 3),
                "end": round(gap_end, 3),
                "duration": round(gap_end - gap_start, 3),
                "sound_seconds": round(sound, 3),
                # Every contiguous loud run in the gap, not just the longest —
                # a gap can hold more than one noise event, and `attenuate_noises`
                # (ops.py) needs each one addressed on its own.
                "runs": [
                    {
                        "start": round(a * frame, 3),
                        "end": round(b * frame, 3),
                        "duration": round((b - a) * frame, 3),
                        "peak_db": round(max(_db(env[i]) for i in range(a, b)), 1),
                    }
                    for a, b in runs
                ],
                "loudest_run": {
                    "start": round(longest[0] * frame, 3),
                    "end": round(longest[1] * frame, 3),
                    "duration": round((longest[1] - longest[0]) * frame, 3),
                },
                "peak_db": round(max(_db(env[i]) for i in range(lo, hi)), 1),
            }
        )

    return {
        "speech_db": round(speech_db, 1),
        "quiet_db": round(quiet_db, 1),
        "threshold_db": round(threshold_db, 1),
        "min_gap": min_gap,
        "min_sound": min_sound,
        # How many words claimed a duration long enough that the mask did not
        # believe it. A gap next to one of these is a gap the transcript was
        # actively hiding, not one it merely failed to fill.
        "doubted_durations": doubted,
        "gaps": gaps,
    }


#: `sound_runs` has no word map to calibrate a speech level from, so it takes
#: the loud tenth of the file as the level sound sits at. On footage that is
#: mostly quiet with speech in it the two coincide; on footage under a music
#: bed the loud tenth is the bed's peaks, and the threshold rises with it.
LOUD_FRACTION = 0.9
#: A run of loud frames shorter than this is a click or a consonant, not a
#: stretch of sound worth reporting on its own; `speech.merge_runs` joins the
#: survivors afterwards.
MIN_RUN = 0.1


def sound_runs(
    env: Sequence[float],
    *,
    frame: float = FRAME,
    threshold: float = THRESHOLD,
    min_run: float = MIN_RUN,
) -> dict[str, Any]:
    """Where the audio holds *sound*, with no transcript to say what it is.

    `loud_gaps` asks a sharper question — where does the audio hold sound the
    transcript did not account for — and it can, because a word map tells it
    what speech level to calibrate against. This has no map. It calibrates the
    same way at the quiet end (the quiet tenth of the file) and takes the
    **loud tenth** as the other anchor, then reports every run of frames over
    the midpoint. That is sound, not speech: a music sting, a door, a scored
    bed swell all clear it. Measured on the trial's own footage (HISTORY.md
    § The trial's second queue) — read a run as *somewhere a voice could be*,
    and reach for `transcribe` when the answer has to be words.
    """
    if not env:
        raise EnergyError("no envelope to measure — the audio decoded to nothing")
    ordered = sorted(_db(v) for v in env)
    quiet_db = ordered[len(ordered) // 10]
    loud_db = ordered[min(len(ordered) - 1, int(len(ordered) * LOUD_FRACTION))]
    threshold_db = quiet_db + threshold * (loud_db - quiet_db)
    loud = [_db(v) >= threshold_db for v in env]
    runs = [
        (round(a * frame, 3), round(b * frame, 3))
        for a, b in _runs(loud, 0, len(env))
        if (b - a) * frame >= min_run
    ]
    return {
        "quiet_db": round(quiet_db, 1),
        "loud_db": round(loud_db, 1),
        "threshold_db": round(threshold_db, 1),
        "min_run": min_run,
        "sound_seconds": round(sum(b - a for a, b in runs), 3),
        "duration": round(len(env) * frame, 3),
        "runs": runs,
    }


def unaccounted_sound(media: Path | str, spans: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """Decode `media` and report the gaps in `spans` that hold sound anyway."""
    return loud_gaps(spans, envelope(decode(media)))


#: What `speech_rms_db` counts as silence, as a fraction of full scale —
#: `clip.py`'s `speech_level`, whose −18 dBFS the launch clip's film was
#: levelled to.
SPEECH_LIVE = 1e-3


def speech_rms_db(media: Path | str, *, start: float = 0.0, end: float | None = None) -> float:
    """RMS level (dBFS) of `media`'s audio from `start` to `end`, over its live
    samples only — `clip.py`'s `speech_level`: at 48 kHz, a frame live when
    any channel is above `SPEECH_LIVE`, and the mean square taken over every
    channel of the live frames. **In the file's own channels**, where
    `clip.py` forced stereo: ffmpeg upmixes mono at −3 dB, which would level a
    mono film 3 dB hot. For a stereo file the two are the same number. Silence between
    lines does not pull the level down, which is what makes it a speech level
    rather than a file's average (docs/plans/RECUT.md step 6).
    """
    source = Path(media).expanduser()
    if not source.exists():
        raise EnergyError(f"no media to measure: {source}")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
         "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    try:
        channels = max(1, int(probe.stdout.strip().splitlines()[0]))
    except (ValueError, IndexError):
        raise EnergyError(f"{source.name} has no audio stream to measure") from None
    cmd = [FFMPEG, "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{start:.6f}"]
    cmd += ["-i", str(source), "-vn"]
    if end is not None:
        cmd += ["-t", f"{end - start:.6f}"]
    cmd += ["-ac", str(channels), "-ar", "48000", "-f", "s16le", "-"]
    try:
        completed = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise EnergyError(f"{FFMPEG} not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise EnergyError(f"ffmpeg could not decode {source}: {exc.stderr.decode(errors='replace').strip()}") from exc
    width = 2 * channels
    samples = array.array("h")
    samples.frombytes(completed.stdout[: len(completed.stdout) // width * width])
    if sys.byteorder == "big":
        samples.byteswap()
    floor = SPEECH_LIVE * 32768
    power, count = 0, 0
    for i in range(0, len(samples), channels):
        frame = samples[i : i + channels]
        if any(abs(value) > floor for value in frame):
            power += sum(value * value for value in frame)
            count += channels
    if not count:
        raise EnergyError(f"{source.name} is silent from {start:g}s, so it has no speech level to measure")
    return 20 * math.log10(math.sqrt(power / count) / 32768)


def integrated_loudness(
    media: Path | str, *, start: float | None = None, end: float | None = None
) -> float:
    """Integrated loudness (LUFS) of `media`'s audio, one number.

    `music_bed.py:loudness()`'s own mechanism, ported: a single
    `loudnorm=print_format=json` analysis pass, parsed for `input_i`. This is
    the plain-scalar half of proofcut's two loudness measurements — the one a
    gain formula wants (`ops._vo_loudness`, `ops._hold_gain_db`) — and it is
    deliberately not `finish.loudness`, which measures via the `ebur128`
    filter for a fuller report (integrated *and* true peak) rather than a
    single number for arithmetic. Two mechanisms, not one duplicated, because
    they serve different callers: a formula wants a float, a report wants a
    dict a person reads.

    `start`/`end` trim the input first (`-ss`/`-t`), for measuring one span of
    a longer file rather than the whole thing.
    """
    source = Path(media).expanduser()
    if not source.exists():
        raise EnergyError(f"no media to measure: {source}")

    cmd = [FFMPEG, "-hide_banner", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{float(start):.3f}"]
    cmd += ["-i", str(source)]
    if end is not None:
        cmd += ["-t", f"{float(end) - float(start or 0.0):.3f}"]
    cmd += ["-af", "loudnorm=print_format=json", "-f", "null", "-"]

    # check=False on purpose: a bad file is a finding to report (no match, an
    # EnergyError naming ffmpeg's own stderr), not a traceback.
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    match = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.DOTALL)
    if not match:
        raise EnergyError(
            f"could not measure the loudness of {source.name}: "
            f"{proc.stderr[-400:].strip()}"
        )
    parsed = json.loads(match[-1])
    value = float(parsed["input_i"])
    # Pure digital silence measures as `"-inf"` — a real string `loudnorm`
    # prints, and `float()` parses it without complaint into an infinite
    # value that reads as finite to everything downstream. Refused here
    # rather than propagated: a gain formula built on it produces `-inf`,
    # which a caller (`ops._hold_gain_db` -> `mlt.Entry.gain_db`) would carry
    # straight into the writer's `volume` filter as a literal "-inf"
    # keyframe — measured to corrupt the *entire* rendered audio mix, not
    # just the one entry, at exit 0 (a completely silent VO input, real
    # `melt`). "Refuse, never clamp, and name the measured number" (CLAUDE.md).
    if not math.isfinite(value):
        raise EnergyError(
            f"{source.name} measured non-finite loudness ({parsed['input_i']!r}) — "
            "likely pure digital silence, which a gain formula cannot be built on"
        )
    return value


def attenuate(
    media: Path | str,
    spans: Sequence[tuple[float, float]],
    *,
    db: float,
    has_video: bool,
    output: Path | str,
) -> Path:
    """Pull `spans` (in seconds, source clock) down `db` and write `output`.

    One ffmpeg pass, one `volume=<gain>:enable='between(t,a,b)'` filter per
    span, comma-chained (goodsometimes `music_bed.py --tame`'s mechanism,
    verbatim: same filter shape, same `10**(db/20)` linear gain). A gain step
    cannot be written into a compressed stream without decoding it, so audio is
    always re-encoded — `aac -b:a 320k` when there is a picture to keep the
    container's video codec compatible with, `pcm_s16le` when the source is
    audio-only. Picture, when there is one, is never touched: `-c:v copy`.

    This only ever *applies* spans it is given — deciding which events in a
    clip qualify as noise lives in `ops._classify_noise_events`, not here.
    """
    source = Path(media).expanduser()
    if not source.exists():
        raise EnergyError(f"no media to attenuate: {source}")
    if not spans:
        raise EnergyError("attenuate needs at least one span")

    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    gain = 10 ** (db / 20)
    filt = ",".join(
        f"volume={gain:.4f}:enable='between(t,{start:.3f},{end:.3f})'" for start, end in spans
    )
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-y", "-i", str(source), "-af", filt]
    if has_video:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "320k"]
    else:
        cmd += ["-c:a", "pcm_s16le"]
    cmd.append(str(destination))

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise EnergyError(f"{FFMPEG} not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise EnergyError(f"ffmpeg could not attenuate {source.name}: {detail}") from exc
    return destination
