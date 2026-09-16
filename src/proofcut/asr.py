"""Running whisper as a subprocess.

Whisper is a binary here, not a library. Importing `whisper` into this process
would pull torch and a GPU context into every `proofcut` invocation — including
`proofcut status`, which needs neither — so ASR stays behind `subprocess`, the
same shape as ffmpeg and auto-editor.

It is openai-whisper, found through `PROOFCUT_WHISPER` and then PATH. Until
2026-09-10 the order ended in a hardcoded path into a sibling project's venv,
which is where this machine's install lives; that path now rides PATH instead,
so the order means the same thing on every machine.

Failures are frequently opaque: when another job holds the GPU, whisper exits
non-zero with the real reason buried several frames up a CUDA traceback. So the
tail of stderr is carried into the exception rather than dropped.

This module has no proofcut dependencies on purpose — `verify` and the
`transcribe` tool both call it.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from collections import Counter
from pathlib import Path
from typing import Any

from proofcut import progress

FFMPEG = "ffmpeg"

#: openai-whisper's default. `turbo` is ~8x faster than `large-v3` at close to
#: its accuracy, which is the right trade for checking a render.
DEFAULT_MODEL = "turbo"

#: The windowed pass wants the *opposite* trade — see `transcribe_windowed`.
#: A bigger model writes more fluent prose, and disfluency is the entire thing
#: being looked for.
WINDOWED_MODEL = "small"

#: 10 s windows. This is the load-bearing number: a segment that ends after ten
#: seconds has nowhere to put an eleventh, which is what stops a retake being
#: collapsed into the word before it.
WINDOW = 10.0

#: Half the window, so **every instant gets two independent readings**.
#:
#: The method as measured on the Scream VO used 3 s, and that is the wrong
#: number for a render. At 3 s the windows only overlap for 3 s in every 7, so
#: 57% of the file is inside exactly one window and a word that window missed
#: is simply gone — `_reconcile` has nothing to fall back on. On the scored v3
#: export that cost eleven words mid-sentence which no second reading could
#: restore. At 5 s the coverage is uniformly two, and the same run recovers
#: them: 859 words against 847, similarity 0.973 against 0.963, and the hole
#: `energy` had been reporting at 59-63 s closes.
#:
#: The cost is ~40% more windows for the same audio, which on a five-minute
#: render is seconds. Pass `overlap=3.0` for the original method.
OVERLAP = 5.0

#: whisper resamples to 16 kHz internally; handing it that directly saves it
#: the work and keeps the slices exact.
SLICE_RATE = 16000


class ASRError(Exception):
    """Raised when whisper is missing, or fails on a media file."""


def whisper_binary() -> Path:
    """Locate the whisper binary: `PROOFCUT_WHISPER`, then PATH."""
    override = os.environ.get("PROOFCUT_WHISPER")
    if override and Path(override).expanduser().exists():
        return Path(override).expanduser()

    found = shutil.which("whisper")
    if found:
        return Path(found)

    raise ASRError(
        "whisper not found. Looked at $PROOFCUT_WHISPER "
        f"({override or 'unset'}), then PATH. Install openai-whisper "
        "(`uv tool install --python 3.12 openai-whisper`, or any venv) and put its `whisper` "
        "on PATH, or set PROOFCUT_WHISPER to the binary."
    )


#: whisper's verbose segment line: `[00:12.340 --> 00:15.000]  text`, with an
#: hours field once the media passes an hour.
_SEGMENT_LINE = re.compile(r"^\[(?:\d+:)?\d+:\d+\.\d+ --> (?:(\d+):)?(\d+):(\d+\.\d+)\]")


def _run_reporting(cmd: list[str], name: str, duration: float | None) -> None:
    """Run whisper with its segment lines reported as progress, raising as
    `subprocess.run(check=True)` would."""

    def on_line(line: str) -> None:
        match = _SEGMENT_LINE.match(line.strip())
        if match:
            hours, minutes, seconds = match.groups()
            at = int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)
            total = duration if duration and duration > 0 else None
            progress.report(min(at, total) if total else at, total, f"transcribing {name}")

    # Unbuffered, or a piped Python child holds its lines until exit.
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    progress.report(0, duration or None, f"transcribing {name}")
    completed = progress.run(cmd, on_stdout=on_line, env=env)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, cmd, completed.stdout, completed.stderr
        )
    if duration and duration > 0:
        progress.report(duration, duration, f"transcribed {name}")


def transcribe(
    media: Path | str,
    *,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    duration: float | None = None,
) -> dict[str, Any]:
    """Transcribe `media` with word timestamps, returning whisper's JSON.

    With a `progress` reporter installed, whisper's own segment lines are read
    as they print and reported against `duration` (seconds of media). Nothing
    else changes: without one it is the same `subprocess.run` it always was.

    The output lands in a temporary directory and is read back rather than
    written beside the media: callers decide where a transcript belongs, and
    dropping a `.json` next to someone's render is not proofcut's call.

    Deliberately no timeout. A five-minute render legitimately takes minutes on
    this box, and killing a nearly-finished transcription is worse than waiting.

    The payload comes back through `clean_payload`, which is not cosmetic: this
    path shipped without a hallucination guard while the windowed one had two,
    on the assumption that a single long pass does not loop. It does — the
    October scale spike's 120 s slice ran away in its last 0.20 s. The count is
    stamped on as `hallucinated_words` rather than only logged, because quietly
    discarding ASR output is how a transcript ends up wrong in a way nobody can
    see, and every caller here reports it.
    """
    source = Path(media).expanduser()
    if not source.exists():
        raise ASRError(f"no media to transcribe: {source}")

    binary = whisper_binary()
    with tempfile.TemporaryDirectory(prefix="proofcut-asr-") as tmp:
        cmd = [
            str(binary),
            str(source),
            "--model",
            model,
            "--output_format",
            "json",
            "--word_timestamps",
            "True",
            "--output_dir",
            tmp,
        ]
        if language:
            cmd += ["--language", language]

        try:
            if progress.active():
                _run_reporting(cmd, source.name, duration)
            else:
                subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise ASRError(f"{binary} is not executable") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            raise ASRError(
                f"whisper failed on {source.name} (model {model}). It fails this "
                "way when another job holds the GPU — check `nvidia-smi`.\n"
                + "\n".join(detail[-12:])
            ) from exc

        # whisper names the output after the input stem, in --output_dir.
        written = Path(tmp) / f"{source.stem}.json"
        if not written.exists():
            found = ", ".join(sorted(p.name for p in Path(tmp).iterdir())) or "nothing"
            raise ASRError(
                f"whisper exited cleanly but wrote no {written.name} ({found} instead)"
            )
        payload = json.loads(written.read_text(encoding="utf-8"))

    payload["hallucinated_words"] = clean_payload(payload)
    return payload


# -- the windowed pass ---------------------------------------------------
#
# One transcription of a whole file is not enough, and the reason is specific.
# Whisper's collapse of an immediate retake has no size limit: on the Scream VO
# the word "bit" was handed 3.96 s with a complete second reading of its own
# sentence inside it, and the prose either side read clean. A single pass over
# the file cannot see that, however good the model — `medium` reported *one*
# take of that sentence where the energy envelope plainly shows two.
#
# Short windows can. A segment that ends after ten seconds has nowhere to put
# the eleventh, so the second take falls into the next window and is written
# down. The cost is that every word in an overlap is transcribed twice and one
# copy has to be thrown away; `_reconcile` decides which.


def plan_windows(
    duration: float, *, window: float = WINDOW, overlap: float = OVERLAP
) -> list[tuple[float, float]]:
    """Lay overlapping windows across `duration`, as (start, end) pairs.

    Starts are `k * step` rather than an accumulating sum: over the ~44 windows
    of a five-minute render, repeated addition of a float step walks the last
    window's start off by enough to matter to the offsets stamped onto its
    words.

    The final window is short but never shorter than the overlap — a window is
    only followed by another when it did not reach the end, which leaves more
    than `window - step` for the next one to cover.
    """
    if window <= 0:
        raise ASRError(f"window must be positive, got {window}")
    if not 0 <= overlap < window:
        raise ASRError(
            f"overlap must be at least 0 and less than the {window}s window, got {overlap}"
        )
    if duration <= 0:
        raise ASRError(f"nothing to window: duration is {duration}s")

    step = window - overlap
    out: list[tuple[float, float]] = []
    k = 0
    while True:
        start = k * step
        end = min(start + window, duration)
        out.append((start, end))
        if end >= duration:
            return out
        k += 1


def _owner(windows: list[tuple[float, float]], centres: list[float], t: float) -> int:
    """Which window keeps the word at `t`: the nearest centre among those covering it.

    Restricting to windows that actually contain `t` is load-bearing, not
    belt-and-braces. The last window is shorter than the rest, so its centre
    sits further left than the even spacing implies, and a word just before it
    can be nearer that centre than to the centre of the window it is genuinely
    inside — which would hand the word to a window that never heard it, and
    drop it from both.
    """
    covering = [i for i, (a, b) in enumerate(windows) if a <= t <= b]
    # Only reachable if a word's timings land outside every window, which means
    # whisper stamped it outside its own slice. Fall back rather than lose it.
    candidates = covering or list(range(len(windows)))
    return min(candidates, key=lambda i: abs(centres[i] - t))


def _reconcile(
    windows: list[tuple[float, float]], heard: list[list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Keep each word from the window whose centre it sits nearest.

    "Nearer its own window's centre" is the rule because a window transcribes
    its middle better than its edges: at an edge the model has half the context
    and a segment boundary to fight, which is where a word gets split, dropped,
    or spelled differently between two passes.

    This partitions rather than merges, and it has to. Taking the union would
    make every word in an overlap appear twice, and `verify.compare` reads a
    doubled phrase as a surviving retake — the windowed pass would then report
    a retake at every window seam.

    Then the discarded copies are given one chance back, because the owner
    missing a word is not rare. Measured on the scored Scream v3 export:
    partitioning on ownership alone lost 22 words mid-sentence — "three
    different movies, 12 years apart…" — from a transcript that read fluently
    either side of the hole. Re-admitting recovered 14 of them.

    So a discarded word is re-admitted when *no* kept word overlaps it: the
    owner is preferred where the owner heard anything, and an edge-quality copy
    beats no copy at all. Overlap, not containment, is the test — a word whose
    duration swallowed a retake spans several real ones (CLAUDE.md).

    The other 8 had no second copy to recover, and the reason is worth knowing
    before reaching for this as a safety net: at the default 10 s / 3 s, 57% of
    a file is inside exactly *one* window, so for most of its length there is no
    independent reading to fall back on. Backfill repairs the seams, not the
    middles. Raising `overlap` to half the window is what makes every instant
    two-covered.
    """
    centres = [(a + b) / 2 for a, b in windows]
    kept: list[dict[str, Any]] = []
    orphaned: list[dict[str, Any]] = []
    for i, words in enumerate(heard):
        for word in words:
            owned = _owner(windows, centres, (word["start"] + word["end"]) / 2) == i
            (kept if owned else orphaned).append(word)

    for word in sorted(orphaned, key=lambda w: (w["start"], w["end"])):
        # Against `kept` as it grows, so two windows both holding a copy of the
        # same unheard word re-admit it once rather than twice.
        if not any(w["start"] < word["end"] and word["start"] < w["end"] for w in kept):
            kept.append(word)
    kept.sort(key=lambda w: (w["start"], w["end"]))
    return kept


def _to_mono_wav(source: Path, destination: Path) -> float:
    """Decode `source` to a 16 kHz mono WAV. Returns its duration in seconds.

    One decode for the whole file, then the windows are sliced out of it with
    the `wave` module — rather than one ffmpeg seek per window, which on a
    five-minute render would be forty-odd decodes of the same audio.
    """
    cmd = [
        FFMPEG,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SLICE_RATE),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise ASRError(f"{FFMPEG} not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise ASRError(f"ffmpeg could not decode {source.name}: {detail}") from exc

    with wave.open(str(destination), "rb") as handle:
        frames, rate = handle.getnframes(), handle.getframerate()
    if not frames:
        raise ASRError(
            f"{source.name} decoded to no audio at all — it has no audio track, "
            "or the track is empty"
        )
    return frames / rate


def _slice(mono: Path, windows: list[tuple[float, float]], into: Path) -> list[Path]:
    """Write each window out as its own WAV, named in order."""
    parts: list[Path] = []
    with wave.open(str(mono), "rb") as src:
        rate = src.getframerate()
        total = src.getnframes()
        for i, (start, end) in enumerate(windows):
            first = min(total, int(start * rate))
            src.setpos(first)
            payload = src.readframes(min(total - first, math.ceil((end - start) * rate)))
            part = into / f"w{i:04d}.wav"
            with wave.open(str(part), "wb") as dst:
                dst.setnchannels(1)
                dst.setsampwidth(src.getsampwidth())
                dst.setframerate(rate)
                dst.writeframes(payload)
            parts.append(part)
    return parts


def transcribe_windowed(
    media: Path | str,
    *,
    window: float = WINDOW,
    overlap: float = OVERLAP,
    model: str = WINDOWED_MODEL,
    language: str | None = None,
    start: float = 0.0,
    end: float | None = None,
    allow_silence: bool = False,
) -> dict[str, Any]:
    """Transcribe `media` in short overlapping windows, stamped back to absolute time.

    Returns the same shape `transcribe` does — a whisper payload `parse_whisper`
    accepts — plus a `windows` count, so a caller can report how the pass was
    run without a second return value.

    `start`/`end` confine the pass to a span of the source, in source seconds;
    the windows are laid across that span and every word still comes back
    stamped in the file's own clock, so a caller asking about `[t1, t2)` reads
    the answer against the same numbers it asked with. The whole file is still
    decoded once (`_to_mono_wav`) — a span is sliced out of that decode the way
    every window is, not seeked in ffmpeg — because the decode is the cheap
    part and one code path for the slicing is one set of edge cases. `end`
    past the audio is refused rather than clamped: a caller that asked about
    seconds that do not exist should hear so, not get a shorter answer.

    `allow_silence=True` returns an empty `words` list where the default raises:
    `verify` wants a render with no speech to fail loudly, but a tool asking
    *what is said here* has "nothing" as a legitimate answer.

    The whole slice set goes to whisper in **one** invocation. The binary takes
    `nargs="+"` audio paths and loads the model once for all of them, which is
    the difference between one model load and forty-four.

    Pass `language` where it is known. Without it whisper detects per window,
    off ten seconds each time rather than the usual thirty, and a window that
    happens to be mostly breath can come back as something surprising — the
    detected languages are counted and the majority is reported, but a wrong
    detection still costs that window's words.
    """
    source = Path(media).expanduser()
    if not source.exists():
        raise ASRError(f"no media to transcribe: {source}")

    binary = whisper_binary()
    with tempfile.TemporaryDirectory(prefix="proofcut-asr-windowed-") as tmp:
        scratch = Path(tmp)
        mono = scratch / "mono.wav"
        duration = _to_mono_wav(source, mono)
        if start < 0:
            raise ASRError(f"start must be at least 0, got {start}")
        stop = duration if end is None else float(end)
        if stop <= start:
            raise ASRError(f"span {start:.3f}-{stop:.3f}s is empty or backwards")
        if stop > duration + 0.01:
            raise ASRError(
                f"span ends at {stop:.3f}s but {source.name} is {duration:.3f}s long"
            )
        stop = min(stop, duration)
        windows = [
            (a + start, b + start)
            for a, b in plan_windows(stop - start, window=window, overlap=overlap)
        ]
        parts = _slice(mono, windows, scratch)

        out = scratch / "json"
        out.mkdir()
        cmd = [
            str(binary),
            *(str(p) for p in parts),
            "--model",
            model,
            "--output_format",
            "json",
            "--word_timestamps",
            "True",
            "--output_dir",
            str(out),
            # Forty-four files of per-segment progress is noise, and it is the
            # only thing on this pipe if whisper fails.
            "--verbose",
            "False",
        ]
        if language:
            cmd += ["--language", language]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise ASRError(f"{binary} is not executable") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            raise ASRError(
                f"whisper failed on {source.name} ({len(parts)} windows, model "
                f"{model}). It fails this way when another job holds the GPU — "
                "check `nvidia-smi`.\n" + "\n".join(detail[-12:])
            ) from exc

        heard: list[list[dict[str, Any]]] = []
        languages: Counter[str] = Counter()
        for part, (w_start, _) in zip(parts, windows, strict=True):
            written = out / f"{part.stem}.json"
            if not written.exists():
                raise ASRError(
                    f"whisper exited cleanly but wrote no {written.name} for the "
                    f"window at {w_start:.1f}s"
                )
            payload = json.loads(written.read_text(encoding="utf-8"))
            if payload.get("language"):
                languages[payload["language"]] += 1
            heard.append(_absolute(payload, w_start))

    words, hallucinated = clean(_reconcile(windows, heard))
    if not words and not allow_silence:
        raise ASRError(
            f"whisper heard no speech in any of the {len(windows)} windows of "
            f"{source.name}."
        )
    return {
        "language": languages.most_common(1)[0][0] if languages else None,
        "words": words,
        "windows": len(windows),
        "start": start,
        "end": stop,
        # A window that came back empty is either real silence or the pass
        # failing on that stretch, and the caller cannot tell which from the
        # word list alone — so a clean-looking windowed result carries its own
        # health with it. Zero on both Scream exports; the words lost there
        # went missing from windows that returned plenty of other text, which
        # this does not and cannot show. `energy.loud_gaps` is what caught
        # those.
        "silent_windows": sum(1 for w in heard if not w),
        # Words discarded as a repetition loop rather than heard — see
        # `_drop_stacked`. Non-zero means whisper stumbled somewhere in this
        # pass, which is worth knowing even though the damage was contained.
        "hallucinated_words": hallucinated,
    }


#: How many words have to be stacked on one instant before the run is read as a
#: hallucination rather than as speech. Three is already impossible.
STACKED = 3

#: The second rule, and the one the identical-instant rule does not subsume.
#:
#: The October scale spike's 120 s slice degraded into a repeat loop at the tail
#: — eight words echoing an earlier sentence, crammed into the last 0.20 s
#: before whisper emitted nine empty segments and stopped. **Only three of those
#: eight share an instant**, so `_drop_stacked` alone drops three and leaves five
#: standing, which is what running the windowed guard on the ingest path would
#: have bought. Measured, not assumed: HISTORY.md § The ingest path's
#: hallucination guard.
#:
#: So the run is addressed by density instead — how many words fit inside a
#: window — and the numbers are the separation itself. Across every real
#: transcript on this box (three copies of the 1150-word Scream VO, the 941-word
#: essay verify pass, the 118-word teaser) the largest cluster inside 0.25 s is
#: **3 words**; the spike's loop holds **8**, and the cluster is exactly the
#: eight hallucinated words with no real one either side. A floor of 5 sits two
#: words clear of both. A *shorter* run does not separate at all — real speech
#: reaches 50 words/second over three of them, because whisper's word durations
#: are not to be trusted (CLAUDE.md), which is why this counts words in a window
#: rather than scoring a rate.
CLUSTER_WINDOW = 0.25
CLUSTER_WORDS = 5


def _drop_stacked(words: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Remove runs of words that whisper stamped at a single instant.

    Short windows make whisper's repetition loop more likely, not less, and it
    emits the repeat with degenerate timings. On the Scream v1 export one
    window produced sixteen words every one of which began and ended at
    229.98 s — a verbatim copy of an earlier phrase, spliced into the middle of
    a different sentence. `verify` then read the copy as a phrase the render
    plays twice and reported a retake that is not in the audio at all.

    Zero-length words are the whole signature and they cost nothing to lose:
    a word occupying no time cannot be cut on, cannot be captioned, and cannot
    be checked against the energy envelope. Returns the survivors and how many
    words were dropped, because quietly discarding ASR output is how a
    transcript ends up wrong in a way nobody can see.
    """
    kept: list[dict[str, Any]] = []
    dropped = 0
    start = 0
    while start < len(words):
        end = start + 1
        if words[start]["start"] == words[start]["end"]:
            while (
                end < len(words)
                and words[end]["start"] == words[end]["end"] == words[start]["start"]
            ):
                end += 1
        if end - start >= STACKED:
            dropped += end - start
        else:
            kept.extend(words[start:end])
        start = end
    return kept, dropped


def _drop_dense(words: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Remove runs of words packed tighter than anyone can speak.

    `_drop_stacked`'s sibling and the rest of the same failure — see
    `CLUSTER_WINDOW` for the measurement that set the two numbers, and for why
    the identical-instant rule does not cover this on its own.

    Greedy from the left, longest run first, so a loop that decays into
    ordinary timings partway through gives up only its dense head. A run is
    dropped whole: the words in it are a re-emission of text that appears
    correctly somewhere else, so there is no half of it worth keeping.
    """
    kept: list[dict[str, Any]] = []
    dropped = 0
    i = 0
    while i < len(words):
        j = i
        while (
            j + 1 < len(words)
            and words[j + 1]["end"] - words[i]["start"] <= CLUSTER_WINDOW
        ):
            j += 1
        if j - i + 1 >= CLUSTER_WORDS:
            dropped += j - i + 1
            i = j + 1
        else:
            kept.append(words[i])
            i += 1
    return kept, dropped


def clean(words: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Both hallucination rules, in the order they were measured.

    The single entry point for either transcription path, which is the whole
    point of it existing: the ingest path shipped without a guard for months
    because the windowed path called `_drop_stacked` inline and there was no
    named thing for the other one to call.
    """
    words, stacked = _drop_stacked(words)
    words, dense = _drop_dense(words)
    return words, stacked + dense


def clean_payload(payload: dict[str, Any]) -> int:
    """Apply `clean` to a whisper JSON dump in place; return what it dropped.

    Works on the payload rather than on a parsed transcript because that is
    where the single-pass path can reach: `asr.transcribe` hands whisper's own
    JSON straight to `transcript.parse_whisper`, so a guard downstream of the
    parse would have to be repeated by every caller. Handles both shapes
    `parse_whisper` accepts and in the same precedence — a flat top-level
    `words` wins over `segments[].words` — and a segment that loses words has
    its `text` rewritten from the survivors, so the payload never states a
    sentence it no longer holds the words for.

    Entries whose timings whisper wrote unusably are passed through untouched
    rather than guessed at: `parse_whisper` raises on those by design, and
    silently dropping them here would take that refusal away.
    """
    flat: list[dict[str, Any]] = []
    if isinstance(payload.get("words"), list):
        segments: list[dict[str, Any]] = []
        flat = list(payload["words"])
    else:
        segments = [s for s in (payload.get("segments") or []) if isinstance(s, dict)]
        for segment in segments:
            flat.extend(segment.get("words") or [])

    usable = []
    for entry in flat:
        try:
            float(entry["start"]), float(entry["end"])
        except (KeyError, TypeError, ValueError):
            continue
        usable.append(entry)
    if not usable:
        return 0

    kept, dropped = clean(usable)
    if not dropped:
        return 0

    survivors = {id(entry) for entry in kept}
    judged = {id(entry) for entry in usable}

    def _keep(entry: dict[str, Any]) -> bool:
        # An unusable entry was never a candidate, so it survives by not having
        # been judged — membership of `survivors` alone would drop it.
        return id(entry) in survivors or id(entry) not in judged

    if segments:
        for segment in segments:
            words = segment.get("words")
            if not words:
                continue
            left = [w for w in words if _keep(w)]
            if len(left) != len(words):
                segment["words"] = left
                segment["text"] = "".join(
                    (w.get("word") if "word" in w else w.get("text")) or "" for w in left
                )
    else:
        payload["words"] = [w for w in payload["words"] if _keep(w)]

    return dropped


def _absolute(payload: dict[str, Any], offset: float) -> list[dict[str, Any]]:
    """Pull one window's words out of a whisper payload, in whole-file time."""
    raw: list[dict[str, Any]] = []
    for segment in payload.get("segments") or []:
        raw.extend(segment.get("words") or [])
    if not raw and isinstance(payload.get("words"), list):
        raw = payload["words"]

    words: list[dict[str, Any]] = []
    for entry in raw:
        text = ((entry.get("word") if "word" in entry else entry.get("text")) or "").strip()
        if not text:
            continue
        try:
            start, end = float(entry["start"]), float(entry["end"])
        except (KeyError, TypeError, ValueError):
            # One unusable word in one window is not worth failing the pass;
            # the overlapping window almost certainly has a good copy.
            continue
        words.append({"word": text, "start": start + offset, "end": end + offset})
    return words
