"""Finding faces in a frame, as a subprocess — the signal a framing proposal rests on.

The design and every number here is PLAN.md § The auto-framing detector, which
scored this against `tests/test_framing_control.py` before any of it was built.
The short version:

**A face detector beats the centre crop and the obvious alternative does not.**
Over the sixteen approved windows: the centre crop scores 0.568 mean overlap and
puts one approved subject entirely outside the frame; faces score 0.750 and never
do; the luma centroid — the saliency-flavoured signal anyone reaches for first —
scores **0.545, below not asking at all**. CLAUDE.md's standing warning that "a
brightness bbox answers 'where is the bright part', never 'where is the frame'"
has a number attached to it now, and this module is the other half of that
result.

**The aggregation rule is not a lever.** Largest face, mean face and
area-weighted faces land within 0.006 of each other on the control. The signal
is doing all the work, so `frame_centre` is one rule with no knob — tuning it
would be tuning noise, and there is only one approved ground truth in existence
to tune against.

**This does not choose the subject, and cannot.** An oracle allowed to pick
*which* detected face to frame on scores 0.863 against this module's 0.750: of
the centre crop's 199px error, faces remove 88px with no judgement at all,
knowing which face removes another 50, and 62px survive both. In a two-hander
every face is a true positive and only one of them is the shot; no property of
the boxes says which. That is the same finding as `synopsis` — a description
does not choose the clip (CLAUDE.md) — arriving in a new place, and it is why
`ops.reframe_detect` proposes and `reframe_sheet` disposes.

**The detector is a subprocess, resolved the way whisper and the VLM are.**
`PROOFCUT_FACE` names a Python interpreter with insightface and onnxruntime in it;
failing that, a refusal naming what it needs. proofcut's own venv holds neither, and should not start now — the argument that put whisper
behind a binary (`asr.py`'s docstring) and the vision model behind an interpreter
(`describe.py`) puts this behind one too. `proofcut status` should not pay for an
ONNX runtime.

This module has no proofcut dependencies on purpose, the same as `asr` and
`describe`.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from proofcut import progress

#: RetinaFace, via insightface's model zoo. The weights are already on this box
#: under `~/.insightface/models/`; nothing here downloads them, because a
#: framing pass that silently reaches for the network on first run is a framing
#: pass that fails in a way nobody attributes to framing.
MODEL = "buffalo_l"

#: The detector's own input size. Large, deliberately: this footage is 1920 wide
#: and the faces in a wide two-shot are small in it, and a missed face is not a
#: neutral outcome here — it is a window that gets refused.
DET_SIZE = 1280

#: CPU. onnxruntime is the CPU build in that venv, and there is nothing to gain
#: from a GPU one: the pass runs three frames per window, and the box's VRAM is
#: already spoken for by an always-on llama-server (`describe.py`).
PROVIDERS = ("CPUExecutionProvider",)

_WORKER = Path(__file__).with_name("_face_worker.py")


class FaceError(Exception):
    """Raised when no interpreter can detect faces, or the worker fails."""


def face_python() -> Path:
    """Locate an interpreter that can run the face detector.

    `PROOFCUT_FACE`, and nothing after it. No PATH step, for
    `describe.vlm_python`'s reason: `python` is always on PATH and is almost
    never the one with onnxruntime in it, so searching it would resolve to an
    interpreter that fails with an ImportError instead of refusing now.

    A second variable rather than a shared "vision sidecar" resolver, because
    the two are routinely different interpreters with different capabilities
    — measured here as insightface 1.0.1 and onnxruntime 1.27.0 in one venv
    and the vision model's torch stack in another. One resolver
    with two capabilities is the tidier build and it wants a third consumer
    before it is worth the indirection (PLAN.md § The auto-framing detector).
    """
    override = os.environ.get("PROOFCUT_FACE")
    if override and Path(override).expanduser().exists():
        return Path(override).expanduser()
    raise FaceError(
        f"no interpreter with a face detector. $PROOFCUT_FACE is {override or 'unset'}"
        f"{'' if not override else ', and nothing is there'}. Set PROOFCUT_FACE to "
        "the python in a venv that has insightface, onnxruntime and opencv."
    )


def available() -> dict[str, Any]:
    """Whether this machine can detect faces, and what is missing if it cannot.

    A report rather than a raise, so `info` and a refusal message can say
    "detection is unavailable here, because X" without loading an ONNX session
    to find out.
    """
    report: dict[str, Any] = {"available": False, "python": None, "model": MODEL, "why": None}
    try:
        report["python"] = str(face_python())
    except FaceError as exc:
        report["why"] = str(exc)
        return report
    if not _WORKER.exists():  # pragma: no cover — only a broken install
        report["why"] = f"proofcut's own worker script is missing: {_WORKER}"
        return report
    report["available"] = True
    return report


def detect(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect faces at every timestamp of every window, in one worker process.

    `windows` is `{"index", "media", "timestamps"}` per entry; the return is
    `{"index", "frames": [{"ts", "faces"}]}` or `{"index", "error"}`, one per
    input, in input order. A *window* failing is reported against that window —
    one unseekable moment in a 35-window run is not a reason to throw the other
    34 away — while the worker failing to start at all is an exception, because
    nothing was detected.

    One process for the whole list, for `describe.describe_windows`' reason:
    the detector loads in ~2s and answers a frame in a fraction of that, so a
    process per window would spend most of its life building the same session.

    Deliberately no timeout, for `asr.transcribe`'s reason: a film's worth of
    windows legitimately takes minutes on CPU, and killing a run that is nearly
    done is worse than waiting.
    """
    if not windows:
        return []
    python = face_python()

    with tempfile.TemporaryDirectory(prefix="proofcut-face-") as tmp:
        job_path = Path(tmp) / "job.json"
        out_path = Path(tmp) / "out.json"
        job_path.write_text(
            json.dumps(
                {
                    "model": MODEL,
                    "det_size": DET_SIZE,
                    "providers": list(PROVIDERS),
                    "windows": windows,
                }
            ),
            encoding="utf-8",
        )
        cmd = [str(python), str(_WORKER), str(job_path), str(out_path)]
        # `check=False`: a non-zero exit is reported through `_worker_failure`,
        # which carries the tail of stderr. CalledProcessError would drop it.
        completed = (
            progress.run_worker(cmd, "finding faces")
            if progress.active()
            else subprocess.run(cmd, capture_output=True, text=True, check=False)
        )
        if completed.returncode != 0 or not out_path.exists():
            raise FaceError(_worker_failure(python, completed))
        payload = json.loads(out_path.read_text(encoding="utf-8"))

    results = {r["index"]: r for r in payload.get("results", [])}
    missing = [w["index"] for w in windows if w["index"] not in results]
    if missing:
        raise FaceError(
            f"the face detector returned nothing for {len(missing)} of "
            f"{len(windows)} windows (first: {missing[0]})"
        )
    return [results[w["index"]] for w in windows]


def _worker_failure(python: Path, completed: subprocess.CompletedProcess[str]) -> str:
    """The message for a worker that did not produce a result file.

    The tail of stderr rather than the head, for `describe`'s reason: the real
    cause is at the bottom of a traceback several frames deep, and the top of
    the output is insightface announcing which model directory it found.
    """
    tail = "\n".join(completed.stderr.strip().splitlines()[-12:])
    return (
        f"the face detector failed (exit {completed.returncode}) under {python}."
        + (f"\n{tail}" if tail else " It wrote nothing to stderr.")
    )


def frame_centre(faces: list[dict[str, Any]]) -> float | None:
    """Where the faces in one frame sit horizontally, or None if there are none.

    Area-weighted: each box contributes its own centre, weighted by how much of
    the frame it takes up, so the near face in a two-shot pulls harder than the
    far one. Scored against largest-face and mean-face on the control the three
    land within 0.006 of each other — **the same answer three ways**, which is
    why this is a rule and not a setting. Horizontal only, because a
    full-height window is the one thing a framing decision moves.

    Degenerate boxes (zero area) would make the weights meaningless, so the
    fallback is the unweighted mean rather than a division by zero — reachable
    only from a detector returning something malformed, never from RetinaFace.
    """
    if not faces:
        return None
    centres, weights = [], []
    for face in faces:
        x0, y0, x1, y1 = (float(v) for v in face["box"])
        centres.append((x0 + x1) / 2)
        weights.append(max(0.0, (x1 - x0) * (y1 - y0)))
    total = sum(weights)
    if total <= 0:  # pragma: no cover — a malformed box, not a real detection
        return statistics.fmean(centres)
    return sum(c * w for c, w in zip(centres, weights, strict=True)) / total


def window_centre(frames: list[dict[str, Any]]) -> float | None:
    """One horizontal centre for a whole window, or None if no frame had a face.

    The median across frames rather than the mean, and that is the aggregation
    that *does* matter: a window is sampled at three moments precisely because
    one of them can land on a seek artefact or a figure crossing frame, and a
    mean lets that one moment move the window while a median does not.

    None is a refusal, not a default. The centre crop is the thing being
    replaced, so falling back to it here would put a proposal into the output
    that reads exactly like a detection — the failure every part of this item
    exists to prevent — eight of the film's fifty-nine windows arrive with no
    signal at all. What happens to one is `ops.reframe_detect`'s `falls_back_to`
    and it is not the centre crop: nothing is written, so the window already in
    force carries over.
    """
    centres = [c for c in (frame_centre(f.get("faces", [])) for f in frames) if c is not None]
    return statistics.median(centres) if centres else None


#: More faces than a two-pane split can hold. Above this a split frames
#: nobody, so the window falls back to one. Measured on the film: two windows
#: of fifty-nine are crowds (nine and eleven subjects, a party and a room
#: watching a television), and both are inside `s1996-randy`.
CROWD = 3

#: A split shorter than this flashes rather than reads. Nearly free, and that
#: is why it is here: of the ten windows on the film that hold more subjects
#: than one crop can, three are under 1.2s and they are 1.7s of the 36.4s
#: total. Excluding them costs almost no coverage and removes every window
#: where the second pane would be on screen for under thirty frames.
MIN_SPLIT_SECONDS = 1.0


def frame_span(faces: list[dict[str, Any]]) -> float | None:
    """How wide the faces in one frame sit, edge to edge, or None if there are none.

    The outside edges rather than the centres, because the question this
    answers is whether one window can *hold* them all — a centre-to-centre
    span calls a pair that half-fits a fit.
    """
    if not faces:
        return None
    lefts = [float(face["box"][0]) for face in faces]
    rights = [float(face["box"][2]) for face in faces]
    return max(rights) - min(lefts)


def _cluster(faces: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split one frame's faces into two groups at their largest horizontal gap.

    The gap rather than a k-means or a fixed midpoint: a two-hander is two
    people with the frame's widest empty space between them, and with three
    faces the pair that belong together are the pair that are close. There is
    no identity in these boxes to cluster on — this is geometry only, which is
    the same limit `frame_centre` runs into and for the same reason.
    """
    ordered = sorted(faces, key=lambda face: (float(face["box"][0]) + float(face["box"][2])) / 2)
    gaps = [
        float(ordered[i + 1]["box"][0]) - float(ordered[i]["box"][2])
        for i in range(len(ordered) - 1)
    ]
    at = gaps.index(max(gaps)) + 1
    return ordered[:at], ordered[at:]


def split_centres(
    frames: list[dict[str, Any]], window_width: int, crowd: int = CROWD
) -> tuple[float, float] | None:
    """Where a stacked split's two panes should sit, or None if this is not one.

    **Every sampled frame has to agree**, and that is the whole of the rule
    rather than a tightening of it. Measured against the film's own proposals:
    ten windows hold subjects one `window_width` crop cannot hold, but on four
    of them one of the three sampled frames disagrees — a figure crossing
    frame, a face turning away — and a split whose panes are wrong for a third
    of its length is worse than the single window it replaces, because a
    stacked frame that is framing nobody is unmistakably deliberate. Requiring
    all three takes it from ten windows to five, and from 14.8% of the film's
    picture-seconds to 6.3%.

    Two more refusals, both from the same measurement:

    * a **crowd** (`crowd` faces or more) gets no split, because two panes
      cannot hold nine people and would frame two of them at random;
    * a pair that *does* fit one window is not a split — one window is the
      better picture whenever it is possible, and 1 of the film's 13
      multi-subject windows is that case, at a 401px span against a 450px
      crop.

    The centres come back as the median across frames, `frame_centre`'s
    aggregation and for its reason: one sampled moment can land on a seek
    artefact, and a median does not let it move the window.
    """
    if not frames:
        return None
    lefts: list[float] = []
    rights: list[float] = []
    for frame in frames:
        found = frame.get("faces") or []
        if not 2 <= len(found) <= crowd:
            return None
        span = frame_span(found)
        if span is None or span <= window_width:
            return None
        near, far = _cluster(found)
        if not near or not far:  # pragma: no cover — two faces always split
            return None
        left, right = frame_centre(near), frame_centre(far)
        if left is None or right is None:  # pragma: no cover — non-empty by here
            return None
        lefts.append(left)
        rights.append(right)
    return statistics.median(lefts), statistics.median(rights)


def window_x(centre: float, source_width: int, window_width: int) -> int:
    """Where a window of `window_width` sits when centred on `centre`.

    Clamped into the source, which is the whole of the placement rule and is
    stated here rather than inline so the control scores *this* and not a
    re-derivation of it. A face near the edge of frame cannot be centred and the
    window stops at the edge — the alternative is a rect hanging off the source,
    which `reframe` would refuse a step later with nothing saying why.
    """
    return max(0, min(round(centre - window_width / 2), source_width - window_width))


if __name__ == "__main__":  # pragma: no cover — a hand-run availability check
    json.dump(available(), sys.stdout, indent=2)
    sys.stdout.write("\n")
