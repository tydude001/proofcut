"""Describing footage with a vision model, as a subprocess.

The design and the six measurements behind every constant here are PLAN.md
§ B-roll by description. The short version:

**The model is a subprocess, resolved the way whisper is.** `PROOFCUT_VLM` names
a Python interpreter that can load Qwen2.5-VL; failing that, a refusal naming
what it needs. proofcut never imports torch — `proofcut status` should not pay for a GPU context, and the
same argument that put whisper behind a binary puts the VLM behind an
interpreter (`asr.py`'s docstring).

**Fixed windows, and the window is the only cost knob.** A description costs
~2.6-3.3s regardless of how much footage it spans, so cost is per window, not
per second: ~1300s of footage is ~6 minutes at 10s windows. That makes
`describe` a job, not a request.

**A whole-clip pass is not merely vague, it is wrong.** Six frames over the
30s Billy/Stu clip described *six men* where there are two — it read six
frames as six people, fluently, with nothing saying anything was off. The same
clip in 10s windows reads correctly and concretely. So windows are never
widened to save time.

**Frames per call are fixed and never scale with clip length.** Six frames at
420x360 peaks at 6024 MiB and fits; twelve OOMs, and twelve is what
a length-scaled rule asks for on a 730s clip. Length is
absorbed by the window *count*.

Two residual error classes are known, measured, and not bugs:

- The model narrates *across* a cut inside a window as though it were one
  take. A window is evidence of what is visible in a span, never of a
  continuous shot.
- Generation can stop mid-sentence, and a truncated description reads exactly
  like a complete one. `truncated` is computed per entry rather than assumed
  away by a generous limit.

This module has no proofcut dependencies on purpose, the same as `asr`.
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

#: What `_vlm_worker.py` loads, as a Hugging Face id — fetched into the
#: interpreter's own HF cache on first use, and 4-bit quantised at load.
MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"

#: 10s. Measured against the alternative rather than chosen: at one pass per
#: clip the model invents people; at 10s it names the kitchen, the cabinets,
#: the blood and the knife. 20s halves the cost and is offered as a knob.
WINDOW = 10.0

#: Fixed, and deliberately not a function of clip length — the rule that
#: scales frames with duration is exactly what OOMs on the 730s cold open.
FRAMES_PER_WINDOW = 3

#: The size every VRAM measurement was taken at.
FRAME_SIZE = "420x360"

#: 120 truncated two windows of the Scream footage mid-sentence. This is
#: headroom over that, and `truncated` still checks rather than trusting it.
MAX_NEW_TOKENS = 220

#: proofcut's own prompt. It asks for the concrete nouns
#: a later search has to match on — a description that says "a person does
#: something" indexes nothing.
PROMPT = (
    "Describe what is visible in this short window of video, in two to four "
    "sentences. State only what is shown: where it takes place, who or what is "
    "present, what they are doing, and how the shot is framed. Name concrete "
    "details someone could search for later — colours, objects, clothing, "
    "location, time of day, lighting. Do not invent anything you cannot see, "
    "do not guess at story or motive, and do not refer to the video, the "
    "frames, or the camera work as such."
)

#: A description that ends anywhere else stopped because it ran out of tokens.
_SENTENCE_END = ".!?\"'”’)"

_WORKER = Path(__file__).with_name("_vlm_worker.py")


class DescribeError(Exception):
    """Raised when the vision model is missing, or fails on a clip."""


#: The torch device the worker loads the model onto, CUDA unless this says
#: otherwise. The knob exists for whoever measures another device; today the
#: worker refuses anything but CUDA, because its 4-bit load is bitsandbytes
#: and bitsandbytes has no other backend (docs/plans/PORTABILITY.md step 3).
DEVICE_ENV = "PROOFCUT_VLM_DEVICE"


def device() -> str:
    """`$PROOFCUT_VLM_DEVICE`, else `cuda`."""
    return os.environ.get(DEVICE_ENV) or "cuda"


def platform_refusal() -> str | None:
    """Why this box cannot run the vision model on the device it would ask for, or None.

    Answered here, before a worker is spawned, so `available()` — and so
    doctor and `describe --plan` — say it rather than a traceback from the
    worker after a model load.
    """
    wanted = device()
    if not wanted.startswith("cuda"):
        return (
            f"{DEVICE_ENV}={wanted}, but the vision model loads 4-bit through "
            "bitsandbytes, which is CUDA-only — the worker has no path for any "
            "other device yet."
        )
    if sys.platform == "darwin":
        return (
            "no CUDA on macOS, and the vision model's 4-bit load is bitsandbytes, "
            "which has no Metal (MPS) backend."
        )
    return None


def vlm_python() -> Path:
    """Locate an interpreter that can load the vision model.

    `PROOFCUT_VLM`, and nothing after it. There is no PATH step, unlike
    `asr.whisper_binary`: `python` is always on PATH and is almost
    never the one with torch in it, so searching it would resolve to an
    interpreter that fails several minutes later with an ImportError instead
    of refusing now.
    """
    override = os.environ.get("PROOFCUT_VLM")
    if override and Path(override).expanduser().exists():
        return Path(override).expanduser()
    raise DescribeError(
        f"no interpreter with a vision model. $PROOFCUT_VLM is {override or 'unset'}"
        f"{'' if not override else ', and nothing is there'}. Set PROOFCUT_VLM to "
        "the python in a venv that has torch, transformers, bitsandbytes and "
        "Pillow, on a machine with a CUDA GPU."
    )


def plan_windows(duration: float, *, window: float = WINDOW) -> list[tuple[float, float]]:
    """Split `duration` into equal windows of **at most** `window` seconds.

    Two decisions, both in the safe direction:

    Equal windows rather than `window`-then-a-remainder, because the
    remainder is the problem: a 0.4s tail samples three frames from one
    instant and then gets described as though it were ten seconds of footage.

    And the count rounds *up*, never to nearest, so a window is never longer
    than the one asked for. Nearest would make a 14s clip a single 14s window
    — 40% wider than requested, arrived at silently — and widening is the one
    direction that fails: the whole-clip pass this module exists to avoid is
    just a window widened far enough. Rounding up trades cost for fidelity,
    and cost is the knob the caller already has.
    """
    if duration <= 0:
        raise DescribeError(f"cannot describe {duration}s of footage")
    if window <= 0:
        raise DescribeError(f"window must be positive, got {window}")
    count = max(1, math.ceil(duration / window))
    step = duration / count
    return [(i * step, (i + 1) * step) for i in range(count)]


def frame_times(start: float, end: float, count: int = FRAMES_PER_WINDOW) -> list[float]:
    """Evenly spaced sample points inside `[start, end)`, off both edges.

    The offset matters: a window boundary is where a cut is most likely to
    be, and a frame landing exactly on one is as likely to catch the shot
    after it as the shot the window is describing.
    """
    span = end - start
    return [start + span * (i + 0.5) / count for i in range(count)]


def truncated(text: str) -> bool:
    """Whether generation stopped mid-sentence.

    Measured, not defensive: at `max_new_tokens=120` two windows of the
    Scream footage ended mid-fact, and an index entry that stops mid-fact
    reads exactly like a complete one to whoever searches it later.
    """
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] not in _SENTENCE_END


def describe_windows(
    windows: list[dict[str, Any]],
    *,
    prompt: str = PROMPT,
    frame_size: str = FRAME_SIZE,
    max_new_tokens: int = MAX_NEW_TOKENS,
) -> list[dict[str, Any]]:
    """Describe every window in one worker process, in order.

    `windows` is `{"index", "media", "timestamps"}` per entry; the return is
    `{"index", "text"}` or `{"index", "error"}`, one per input, in input
    order. A *window* failing is reported against that window — one
    unseekable moment in a 130-window run is not a reason to throw the other
    129 away — while the worker failing to start at all is an exception,
    because nothing was described.

    One process for the whole list is the point. The load is ~15s against
    ~3s per window, so a process per window would spend most of its life
    loading the same 31 GB of weights.

    Deliberately no timeout, for `asr.transcribe`'s reason: a project's worth
    of footage legitimately takes minutes, and killing a run that is nearly
    done is worse than waiting.
    """
    if not windows:
        return []
    if refusal := platform_refusal():
        raise DescribeError(refusal)
    python = vlm_python()

    with tempfile.TemporaryDirectory(prefix="proofcut-vlm-") as tmp:
        job_path = Path(tmp) / "job.json"
        out_path = Path(tmp) / "out.json"
        job_path.write_text(
            json.dumps(
                {
                    "model": MODEL,
                    "prompt": prompt,
                    "frame_size": frame_size,
                    "max_new_tokens": max_new_tokens,
                    "windows": windows,
                    "device": device(),
                }
            ),
            encoding="utf-8",
        )
        cmd = [str(python), str(_WORKER), str(job_path), str(out_path)]
        # `check=False`: a non-zero exit is reported through `_worker_failure`,
        # which carries the tail of stderr. CalledProcessError would drop it.
        completed = (
            progress.run_worker(cmd, "describing footage")
            if progress.active()
            else subprocess.run(cmd, capture_output=True, text=True, check=False)
        )
        if completed.returncode != 0 or not out_path.exists():
            raise DescribeError(_worker_failure(python, completed))
        payload = json.loads(out_path.read_text(encoding="utf-8"))

    results = {r["index"]: r for r in payload.get("results", [])}
    missing = [w["index"] for w in windows if w["index"] not in results]
    if missing:
        raise DescribeError(
            f"the vision model returned nothing for {len(missing)} of "
            f"{len(windows)} windows (first: {missing[0]})"
        )
    return [results[w["index"]] for w in windows]


def _worker_failure(python: Path, completed: subprocess.CompletedProcess[str]) -> str:
    """The message for a worker that did not produce a result file.

    The tail of stderr rather than the whole of it, and the tail rather than
    the head, for `asr`'s reason: the real cause of a CUDA failure is at the
    bottom of a traceback several frames deep, and the top of the output is a
    model-loading progress bar.
    """
    tail = "\n".join(completed.stderr.strip().splitlines()[-12:])
    return (
        f"the vision model failed (exit {completed.returncode}) under {python}."
        + (f"\n{tail}" if tail else " It wrote nothing to stderr.")
    )


def available() -> dict[str, Any]:
    """Whether this machine can describe, and what is missing if it cannot.

    A report rather than a raise, so `info` and the web UI can say "describe
    is unavailable here, because X" without a 31 GB model being the thing
    that answers the question.
    """
    report: dict[str, Any] = {"available": False, "python": None, "tagger": None, "why": None}
    try:
        report["python"] = str(vlm_python())
    except DescribeError as exc:
        report["why"] = str(exc)
        return report
    if refusal := platform_refusal():
        report["why"] = refusal
        return report
    if not _WORKER.exists():  # pragma: no cover — only a broken install
        report["why"] = f"proofcut's own worker script is missing: {_WORKER}"
        return report
    # What the interpreter above runs — proofcut's own worker since it stopped
    # importing a sibling repo's tagger, which is what this key used to name.
    report["tagger"] = str(_WORKER)
    report["available"] = True
    return report


if __name__ == "__main__":  # pragma: no cover — a hand-run availability check
    json.dump(available(), sys.stdout, indent=2)
    sys.stdout.write("\n")
