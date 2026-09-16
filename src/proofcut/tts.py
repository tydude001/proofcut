"""Synthesising a line in a cloned voice, as a subprocess — the `vo_synth` backend.

The finding this rests on is in local-llm's `notes/voice-clone-zero-shot.md`
(rounds 1–4, 2026-08-20/21), and the short version decides the shape here:

**Zero-shot beat every fine-tune on likeness, so the voice is a reference clip,
never a checkpoint.** Qwen3-TTS-12Hz-1.7B-Base given ~19 s of Tyler's VO and
its transcript scored 0.989 on the model's own speaker encoder against his
real takes' 0.993; a full fine-tune on 36 min scored 0.985 and *drifted away
with every epoch*, the upstream-lr run collapsed outright, and reference-in-
context / instruct / temperature moved intelligibility, not likeness. So a
*voice* is a directory holding `ref.wav` + `ref.txt`, and nothing here loads
a checkpoint that is not the stock model.

**Every render is a lottery ticket, so the op buys several and ranks them.**
Seed moved the result more than the reference did (round 2), so `synth` takes
a list of seeds and returns one candidate per seed with the speaker-encoder
cosine against the reference — the one number measured here that tracks
"sounds like him". The worker computes it in the same process, because the
encoder is inside the model it already loaded.

**A reference can run away.** One 21 s reference made every render hit 655 s
of audio (round 2); the codec runs at ~12.5 tokens/s, so `max_seconds` is
turned into a hard `max_new_tokens` cap and a candidate at the cap is reported
as `capped` rather than trusted.

**The model is a subprocess, resolved the way whisper, the VLM and the face
detector are.** `PROOFCUT_TTS` names a Python interpreter with `qwen_tts` and a
CUDA torch in it, and nothing after it. proofcut's own venv stays free of torch
(`asr.py`'s argument). `PROOFCUT_TTS_MODEL` names the model directory the same
way. Both used to fall back to the voice-clone spike under `~/lucid-work`,
which is one machine's layout, and a clean Ubuntu's doctor printed that path
back to a stranger. This box sets both in `~/.config/environment.d/60-proofcut.conf`.

**The voice is configuration, never a default in this file.** A voice is one
person's identity, so unlike the interpreter and the stock model there is no
fallback for it at all: `vo_synth` takes `voice=` or reads `PROOFCUT_TTS_VOICE`,
and with neither it refuses by name. A checkout of this repo holds no
reference clip and no path to one.

This module has no proofcut dependencies on purpose, the same as `asr`,
`describe` and `faces`.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from proofcut import progress

#: Qwen3-TTS's 12 Hz codec, measured: `max_new_tokens=420` rendered 33.5 s.
TOKENS_PER_SECOND = 12.5

#: The language string the model takes. English is the only one with a voice here.
LANGUAGE = "English"

_WORKER = Path(__file__).with_name("_tts_worker.py")


class TTSError(Exception):
    """Raised when no interpreter can synthesise, a voice is incomplete, or the worker fails."""


#: The torch device the worker loads the model onto, CUDA unless this says
#: otherwise. `mps` and `cpu` pass through to `from_pretrained` untouched, and
#: **neither has ever been run**: Qwen3-TTS has only been measured on CUDA
#: here, so a Mac is reported unavailable until someone sets this and listens
#: to the result (docs/plans/PORTABILITY.md step 3).
DEVICE_ENV = "PROOFCUT_TTS_DEVICE"


def device() -> str:
    """`$PROOFCUT_TTS_DEVICE`, else `cuda`."""
    return os.environ.get(DEVICE_ENV) or "cuda"


def platform_refusal() -> str | None:
    """Why this OS cannot run the synthesiser on the device it would ask for, or None."""
    if sys.platform == "darwin" and device().startswith("cuda"):
        return (
            "no CUDA on macOS, and the synthesiser loads onto CUDA. "
            f"{DEVICE_ENV}=mps (or cpu) passes through to the worker but has "
            "never been measured."
        )
    return None


def tts_python() -> Path:
    """Locate an interpreter that can run the synthesiser — `PROOFCUT_TTS`, and nothing after it.

    No PATH step, for `describe.vlm_python`'s reason: `python` is always on PATH
    and is almost never the one with a CUDA torch in it.
    """
    override = os.environ.get("PROOFCUT_TTS")
    if override and Path(override).expanduser().exists():
        return Path(override).expanduser()
    raise TTSError(
        f"no interpreter with a voice synthesiser. $PROOFCUT_TTS is {override or 'unset'}"
        f"{'' if not override else ', and nothing is there'}. Set PROOFCUT_TTS to the "
        "python in a venv that has qwen-tts and a CUDA torch."
    )


def model_dir() -> Path:
    """The stock Qwen3-TTS model directory — `PROOFCUT_TTS_MODEL`, and nothing after it.

    Nothing here downloads it: a synth that silently reaches for 3.7 GB on first
    call fails in a way nobody attributes to synthesis (`faces.MODEL`'s rule).
    """
    override = os.environ.get("PROOFCUT_TTS_MODEL")
    if override and Path(override).expanduser().is_dir():
        return Path(override).expanduser()
    raise TTSError(
        f"no Qwen3-TTS model directory. $PROOFCUT_TTS_MODEL is {override or 'unset'}"
        f"{'' if not override else ', and nothing is there'}. Set PROOFCUT_TTS_MODEL "
        "to a local snapshot of Qwen/Qwen3-TTS-12Hz-1.7B-Base."
    )


def voice_dir(voice: str | Path | None = None) -> Path:
    """Resolve a voice — a directory holding `ref.wav` and `ref.txt`.

    An explicit `voice` wins; then `PROOFCUT_TTS_VOICE`; there is deliberately no
    third step (this module's docstring — a voice is a person, not tooling).
    A directory missing either file is refused here, by name, rather than
    discovered as a worker traceback: the transcript is what makes the
    reference usable (ICL mode needs the words), and a voice with the audio
    alone would synthesise — with whatever the model guessed the words were.
    """
    if voice is not None:
        candidate = Path(voice).expanduser()
        source = "voice argument"
    else:
        override = os.environ.get("PROOFCUT_TTS_VOICE")
        if not override:
            raise TTSError(
                "no voice: pass voice=<dir> (CLI --voice) or set PROOFCUT_TTS_VOICE to a directory "
                "holding ref.wav (≈10–20 s of one speaker, no music) and ref.txt (its words). "
                "There is no default voice on purpose."
            )
        candidate, source = Path(override).expanduser(), "$PROOFCUT_TTS_VOICE"
    missing = [name for name in ("ref.wav", "ref.txt") if not (candidate / name).is_file()]
    if missing:
        raise TTSError(
            f"voice {candidate} ({source}) is missing {', '.join(missing)} — a voice is a "
            "directory holding ref.wav (≈10–20 s of one speaker, no music) and ref.txt (its words)."
        )
    return candidate


def available(voice: str | Path | None = None) -> dict[str, Any]:
    """Whether this box can synthesise, and what is missing if it cannot — a report, never a raise.

    `voice` is checked too (the argument, else `PROOFCUT_TTS_VOICE`), because a
    box with the model and no voice cannot synthesise either.

    Each of the three is resolved on its own, so one missing does not hide
    what the other two found, and `why` names the voice first — the order
    `ops.vo_synth` refuses in. Interpreter-first made the reason depend on the
    machine: a checkout with no TTS venv was told about the venv, and never
    that there is no default voice.
    """
    report: dict[str, Any] = {"available": False, "python": None, "model": None, "voice": None, "why": None}
    reasons = []
    for key, resolve in (
        ("voice", lambda: voice_dir(voice)),
        ("python", tts_python),
        ("model", model_dir),
    ):
        try:
            report[key] = str(resolve())
        except TTSError as exc:
            reasons.append(str(exc))
    if reasons:
        report["why"] = reasons[0]
        return report
    if refusal := platform_refusal():
        report["why"] = refusal
        return report
    if not _WORKER.exists():  # pragma: no cover — only a broken install
        report["why"] = f"proofcut's own worker script is missing: {_WORKER}"
        return report
    report["available"] = True
    return report


def max_new_tokens(max_seconds: float) -> int:
    """The token cap that bounds a render at `max_seconds` of audio."""
    if max_seconds <= 0:
        raise TTSError(f"max_seconds must be positive, not {max_seconds!r}")
    return math.ceil(max_seconds * TOKENS_PER_SECOND)


def synth(
    text: str,
    voice: Path,
    out_dir: Path,
    seeds: list[int],
    *,
    max_seconds: float,
    language: str = LANGUAGE,
) -> list[dict[str, Any]]:
    """Render `text` once per seed in `voice`, into `out_dir`, in one worker process.

    Returns one entry per seed, in seed order: `{"seed", "path", "duration",
    "sim", "capped"}` or `{"seed", "error"}`. A *seed* failing is reported
    against that seed — the other candidates are still the answer — while the
    worker failing to start at all is an exception, because nothing was
    rendered. One process for the whole list because the model loads in ~5 s
    and renders a sentence in ~3, `faces.detect`'s reason.

    Deliberately no timeout, for `asr.transcribe`'s reason.
    """
    if not seeds:
        return []
    if refusal := platform_refusal():
        raise TTSError(refusal)
    python = tts_python()
    model = model_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="proofcut-tts-") as tmp:
        job_path = Path(tmp) / "job.json"
        out_path = Path(tmp) / "out.json"
        job_path.write_text(
            json.dumps(
                {
                    "model": str(model),
                    "text": text,
                    "language": language,
                    "ref_audio": str(voice / "ref.wav"),
                    "ref_text": (voice / "ref.txt").read_text(encoding="utf-8").strip(),
                    "seeds": [int(s) for s in seeds],
                    "max_new_tokens": max_new_tokens(max_seconds),
                    "out_dir": str(out_dir),
                    "device": device(),
                }
            ),
            encoding="utf-8",
        )
        cmd = [str(python), str(_WORKER), str(job_path), str(out_path)]
        completed = (
            progress.run_worker(cmd, "synthesising the voice")
            if progress.active()
            else subprocess.run(cmd, capture_output=True, text=True, check=False)
        )
        if completed.returncode != 0 or not out_path.exists():
            raise TTSError(_worker_failure(python, completed))
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    results = {int(r["seed"]): r for r in payload.get("candidates", [])}
    missing = [s for s in seeds if int(s) not in results]
    if missing:
        raise TTSError(
            f"the synthesiser returned nothing for {len(missing)} of {len(seeds)} seeds (first: {missing[0]})"
        )
    cap = max_new_tokens(max_seconds) / TOKENS_PER_SECOND
    out = []
    for seed in seeds:
        entry = dict(results[int(seed)])
        if "error" not in entry:
            # A render at the cap did not end because the line ended; it ended
            # because the cap did, and is reported as such rather than ranked.
            entry["capped"] = float(entry["duration"]) >= cap - 1.0
        out.append(entry)
    return out


def _worker_failure(python: Path, completed: subprocess.CompletedProcess[str]) -> str:
    tail = "\n".join(completed.stderr.strip().splitlines()[-12:])
    return (
        f"the voice synthesiser failed (exit {completed.returncode}) under {python}."
        + (f"\n{tail}" if tail else " It wrote nothing to stderr.")
    )
