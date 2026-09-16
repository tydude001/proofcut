"""Listening checks over a finished render — the seam machinery `hold_check`
and (later) `finish_check` share.

Two things live here, and neither reads a project: both take a media path and
report what the audio actually does, the same "a render is verified by
transcribing it, not by reading the timeline" stance CLAUDE.md states for
`verify`.

- **`loudness`** — integrated LUFS and true peak, via ffmpeg's `ebur128`
  filter at 4x oversampling (`aresample=192000,ebur128=peak=true,
  aresample=48000` — `verify_longlegs.py`'s own command, ported). This is
  proofcut's *report* half of loudness measurement, parsed by `parse_ebur128`;
  it is deliberately not `energy.integrated_loudness`, which is the *formula*
  half — a single scalar off a `loudnorm` analysis pass, for `ops._vo_loudness`
  and `ops._hold_gain_db` to do arithmetic with. Two mechanisms, not one
  duplicated: a formula wants a float, a report wants integrated loudness
  *and* true peak together.
- **`hold_seams`** — the level right at a named instant against the quiet
  floor just after it, `verify_longlegs.py`'s `rms_profile` ported: decoded
  once at 48 kHz with stdlib `array` (no numpy in proofcut, CLAUDE.md), because a
  hold's seam is judged by ear-shaped windows — the loudest 50 ms in the last
  0.15 s before the cut against the quiet floor 0.05-0.40 s after it — not by
  an average that would blur a clean cut and a mid-word one into the same
  number.

Built by `04-film-holds` for `ops.hold_check`; `02-finish-check` extends this
module rather than duplicating it (WORK-ORDERS ruling 5) — kept general on
purpose, not hold-specific, so a second caller composes rather than forks it.
"""

from __future__ import annotations

import array
import math
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from proofcut import progress

FFMPEG = "ffmpeg"

#: 48 kHz: fine enough for the 20-50ms seam windows below, and the rate
#: `hold_seams` decodes at regardless of the source file's own rate — a
#: fixed clock is what makes the window math (`SEAM_SPAN`/`PEAK_WINDOW`/
#: `FLOOR_WINDOW`) mean the same thing on every render.
DECODE_RATE = 48000

#: `verify_longlegs.py:rms_profile`'s own windows, ported verbatim rather than
#: re-derived: the loudest 50ms slice in the last 0.15s before a mark is
#: "what is happening right at the cut"; the floor is measured 0.05-0.40s
#: after it, which is far enough past the cut that the *next* thing playing
#: (VO resuming, a bed at its ducked level) does not fill the floor back in.
SEAM_LOOKBACK = 0.15
SEAM_PEAK_STEP = 0.05
SEAM_PEAK_SLICES = 3
FLOOR_START = 0.05
FLOOR_END = 0.40
#: The window either side reported alongside the seam-specific ones, for a
#: human comparing "the second around the cut" against "right at it".
SEAM_SPAN = 1.0

#: `verify_longlegs.py`'s own fault thresholds, ported: a cut is "still loud"
#: when the last 50ms before it reads above this — a word or a line was not
#: finished — and a "noise-floor cliff" when the drop to the floor after it
#: exceeds `CLIFF_DROP_DB` while the pre-cut level is still above
#: `CLIFF_FLOOR_DB` (a cut that was already quiet has nowhere to cliff from).
STILL_LOUD_DB = -25.0
CLIFF_DROP_DB = 12.0
CLIFF_FLOOR_DB = -35.0

#: 16-bit full scale, `energy.py`'s own constant, restated here rather than
#: imported — this module reads no other proofcut module, on purpose: it is the
#: shared floor two features (04, 02) build on, and importing sideways from
#: `energy.py` would tie this module's own stability to that one's.
FULL_SCALE = 32768.0


class FinishError(Exception):
    """Raised when a render's audio cannot be decoded or measured."""


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # check=False on purpose, `energy.integrated_loudness`'s own discipline: a
    # bad file is a finding to report (a clear FinishError naming ffmpeg's own
    # stderr), not a traceback two frames from here.
    if progress.cancel_armed():
        return progress.run(cmd)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def parse_ebur128(stderr: str) -> dict[str, float | None]:
    """Pull integrated loudness, loudness range and true peak out of ffmpeg's
    `ebur128` filter stderr.

    The filter logs a momentary/short-term/integrated reading roughly once a
    second *and* a final `Summary:` block at the end — both spellings share
    the same `I:`/`LRA:`/`Peak:` labels, so this takes the **last** match of
    each rather than the first: the summary is always printed last, and
    `verify_longlegs.py` leans on exactly this ordering (it grabs the last 8
    matching lines rather than parsing the summary block by structure).
    `None` for any field ffmpeg never printed, rather than raising — a caller
    that needs the file to have measured is `loudness`, below, which does
    raise when `integrated` came back empty.
    """

    def last(pattern: str) -> float | None:
        found = re.findall(pattern, stderr)
        return float(found[-1]) if found else None

    return {
        "integrated": last(r"I:\s*(-?\d+\.?\d*)\s*LUFS"),
        "lra": last(r"LRA:\s*(-?\d+\.?\d*)\s*LU\b"),
        # The unit ffmpeg prints here is not to be trusted from memory
        # (CLAUDE.md): the installed 8.1.2 prints "Peak: -8.7 dBFS" in the
        # `ebur128=peak=true` summary, not the "dBTP" the filter's own name
        # would suggest — measured, not assumed, so the unit is matched
        # loosely (`dB\w*`) rather than pinned to one spelling.
        "true_peak": last(r"Peak:\s*(-?\d+\.?\d*)\s*dB\w*"),
    }


def loudness(
    path: Path | str, *, start: float | None = None, end: float | None = None
) -> dict[str, float | None]:
    """Integrated loudness, loudness range and true peak of `path`'s audio.

    `verify_longlegs.py`'s own command, ported verbatim: `ebur128` run at 4x
    oversampling (`aresample=192000,...,aresample=48000`) is what true-peak
    measurement wants — a peak between samples is invisible at the source
    rate and this is how ffmpeg's own filter is documented to catch it.
    `start`/`end` trim the input first, for one span of a longer file.
    """
    source = Path(path).expanduser()
    if not source.exists():
        raise FinishError(f"no media to measure: {source}")

    cmd = [FFMPEG, "-hide_banner", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{float(start):.3f}"]
    cmd += ["-i", str(source)]
    if end is not None:
        cmd += ["-t", f"{float(end) - float(start or 0.0):.3f}"]
    cmd += [
        "-af",
        "aresample=192000,ebur128=peak=true,aresample=48000",
        "-f",
        "null",
        "-",
    ]

    proc = _run(cmd)
    result = parse_ebur128(proc.stderr)
    if result["integrated"] is None:
        raise FinishError(
            f"could not measure the loudness of {source.name}: "
            f"{proc.stderr[-400:].strip()}"
        )
    return result


#: How far a mastered file may land from the integrated target, in LU, and
#: above the true-peak ceiling, in dB, before `master_loudness` refuses it.
#: The peak allowance is AAC's: the encoder re-adds inter-sample peak after
#: the limiter, which is why v10 came out at −1.21 dBTP for a −1 ceiling and
#: Scream v8 at −1.13.
MASTER_LU_TOLERANCE = 1.0
MASTER_PEAK_ALLOWANCE = 0.5

#: dB under the true-peak ceiling the limiter aims, for the AAC encoder's own
#: overshoot: at 48 kHz a −1.5 dB sample-peak limit landed −0.63 dBTP in the
#: file, and oversampled it lands inside the ceiling (`music_bed.py`).
LIMIT_HEADROOM = 0.5


def master_loudness(
    path: Path | str, *, integrated: float, true_peak: float = -1.0, lra: float = 11.0
) -> dict[str, Any]:
    """`path`'s audio to `integrated` LUFS under a `true_peak` ceiling, in
    place — one gain and a true-peak limiter, measured before, measured after,
    and refused rather than kept when the result misses.

    **One gain, never `loudnorm`'s second pass.** `loudnorm` keeps
    `linear=true` only while the gain fits under the ceiling; past it, it
    switches to dynamic mode and rides the whole mix's gain — which on the
    Scream rebuild put 5 dB of a 12 dB duck back, the bed rising in every
    pause and falling under every line, loudness on target and exit 0
    (HISTORY.md § The duck). A master that has to move the mix is a mix
    decision, not a master. So: the measured gain, then `alimiter` at 4x
    oversampling — a limiter that sees inter-sample peaks is a true-peak
    limiter to within the encoder's overshoot, and it aims `LIMIT_HEADROOM`
    under the ceiling for that overshoot (`music_bed.py`'s chain, measured on
    Lambs/Longlegs v4). `level=disabled`, or alimiter makes its output up to
    the ceiling; `latency=true`, or it delays every sample 5 ms. `lra` is
    kept for callers and reported, and no longer shapes anything.
    docs/plans/NATIVE.md § A3.
    """
    source = Path(path).expanduser()
    if not source.exists():
        raise FinishError(f"no media to master: {source}")
    before = loudness(source)
    if before.get("integrated") is None or not math.isfinite(before["integrated"]):
        raise FinishError(f"{source.name} has no measurable loudness to master")
    gain = integrated - before["integrated"]
    limit = 10 ** ((true_peak - LIMIT_HEADROOM) / 20)
    peak = before.get("true_peak")
    limited = peak is not None and peak + gain > true_peak - LIMIT_HEADROOM

    staged = source.with_name(f"{source.stem}.mastering{source.suffix}")
    codec = ["-c:a", "pcm_s16le"] if source.suffix.lower() == ".wav" else ["-c:a", "aac", "-b:a", "256k"]
    second = _run(
        [FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", str(source), "-map", "0:v?", "-map", "0:a:0",
         "-c:v", "copy",
         "-af", (
             f"volume={gain:.3f}dB,aresample=192000,"
             f"alimiter=limit={limit:.5f}:level=disabled:latency=true,aresample=48000"
             # A filter's frame timestamps can run ahead of its samples — loudnorm's
             # did, by up to ~90 ms in one AAC packet, and everything after it
             # played that late. Restamp by samples, in AAC's own 1024-sample
             # frames: stamped from samples consumed, the 192 kHz round trip
             # still left one packet a sample long. TRIAL.md § The third trial.
             ",asetnsamples=n=1024:p=0,asetpts=N/SR/TB"
         ),
         *codec, "-movflags", "+faststart", str(staged)]
    )  # fmt: skip
    if second.returncode != 0 or not staged.exists():
        staged.unlink(missing_ok=True)
        raise FinishError(f"ffmpeg could not master {source.name}: {second.stderr[-400:].strip()}")
    after = loudness(staged)
    misses = []
    if abs(after["integrated"] - integrated) > MASTER_LU_TOLERANCE:
        misses.append(f"integrated {after['integrated']:.1f} LUFS against {integrated:g}")
    if after.get("true_peak") is not None and after["true_peak"] > true_peak + MASTER_PEAK_ALLOWANCE:
        misses.append(f"true peak {after['true_peak']:.2f} dBTP against a {true_peak:g} ceiling")
    if misses:
        staged.unlink(missing_ok=True)
        raise FinishError(
            f"mastering {source.name} missed: {'; '.join(misses)} — the render is left as it was"
        )
    staged.replace(source)
    return {
        "target_integrated": integrated,
        "target_true_peak": true_peak,
        # "linear" when the gain fit under the ceiling, "limited" when the
        # limiter had peaks to hold — never "dynamic", which this no longer does.
        "normalization": "limited" if limited else "linear",
        "gain_db": round(gain, 2),
        "lra_target": lra,
        "before": before,
        "after": after,
    }


def _decode(media: Path | str, *, rate: int = DECODE_RATE) -> array.array:
    """Decode `media` to mono 16-bit PCM at `rate` — `energy.decode`'s own
    mechanism, at 48 kHz rather than 8 kHz: the seam windows below are
    20-50ms wide, fine enough that the coarser envelope rate would blur them.
    """
    source = Path(media).expanduser()
    if not source.exists():
        raise FinishError(f"no media to decode: {source}")

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
    completed = subprocess.run(cmd, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise FinishError(f"ffmpeg could not decode {source.name}: {detail}")

    raw = completed.stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % samples.itemsize])
    if not samples:
        raise FinishError(f"{source.name} decoded to no audio at all")
    return samples


def _window_db(samples: array.array, rate: int, t0: float, t1: float) -> float:
    """RMS, in dBFS, of `samples[t0:t1]` — -120.0 (silence, finite) for an
    empty or out-of-range window rather than a divide-by-zero."""
    lo, hi = max(0, int(t0 * rate)), min(len(samples), max(0, int(t1 * rate)))
    if hi <= lo:
        return -120.0
    total = sum(s * s for s in samples[lo:hi])
    rms = math.sqrt(total / (hi - lo)) / FULL_SCALE
    return 20.0 * math.log10(max(rms, 1e-9))


def hold_seams(
    media: Path | str,
    marks: Sequence[tuple[str, float]],
    *,
    span: float = SEAM_SPAN,
    rate: int = DECODE_RATE,
) -> list[dict[str, Any]]:
    """RMS either side of each named instant, decoded once at `rate`.

    `verify_longlegs.py:rms_profile`'s own windows (see the module docstring):
    `peak_pre` is the loudest of `SEAM_PEAK_SLICES` 50ms slices in the last
    `SEAM_LOOKBACK` before the mark — what is actually happening right at the
    cut, not diluted by averaging over a whole second the way `pre` is;
    `floor_post` is the quiet floor `FLOOR_START`-`FLOOR_END` after it.

    `fault` is one of `None`, `"still_loud"` (the last 50ms before the cut is
    still above `STILL_LOUD_DB` — a word or a line was not finished) or
    `"noise_floor_cliff"` (the level drops more than `CLIFF_DROP_DB` to the
    floor while the pre-cut level itself was above `CLIFF_FLOOR_DB` — a cut
    that was already quiet has nowhere to cliff from). Reported, never
    raised — this is a listening check on a render that already exists, the
    same stance every other audio check in proofcut takes (`verify`,
    `film_check`).
    """
    samples = _decode(media, rate=rate)
    rows: list[dict[str, Any]] = []
    for name, t in marks:
        pre = _window_db(samples, rate, t - span, t - 0.02)
        post = _window_db(samples, rate, t + 0.02, t + span)
        peak_pre = max(
            _window_db(
                samples,
                rate,
                t - SEAM_LOOKBACK + i * SEAM_PEAK_STEP,
                t - SEAM_LOOKBACK + i * SEAM_PEAK_STEP + SEAM_PEAK_STEP,
            )
            for i in range(SEAM_PEAK_SLICES)
        )
        floor_post = _window_db(samples, rate, t + FLOOR_START, t + FLOOR_END)

        fault: str | None = None
        if peak_pre > STILL_LOUD_DB:
            fault = "still_loud"
        elif peak_pre - floor_post > CLIFF_DROP_DB and peak_pre > CLIFF_FLOOR_DB:
            fault = "noise_floor_cliff"

        rows.append(
            {
                "name": name,
                "time": round(t, 3),
                "pre": round(pre, 1),
                "post": round(post, 1),
                "peak_pre": round(peak_pre, 1),
                "floor_post": round(floor_post, 1),
                "fault": fault,
            }
        )
    return rows
