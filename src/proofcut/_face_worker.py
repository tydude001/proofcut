"""The detector side of `faces`, run under a *different* interpreter.

**This module is never imported by proofcut.** It is executed by the Python that
`faces.face_python()` resolves — a venv with insightface and onnxruntime in it —
which is the whole reason it is a separate file. proofcut's own venv stays free of
both, exactly as `asr.py` keeps whisper behind a binary and `_vlm_worker.py`
keeps torch behind an interpreter. It lives inside the package only so it ships
with it.

It reads one JSON job from `argv[1]` and writes one JSON result to `argv[2]`. A
file rather than stdout for `_vlm_worker.py`'s reason: insightface prints its
model directory and its provider list to whichever stream it feels like, and
that landing in the middle of a JSON document is a parse error that reads like a
detector failure.

The detector is built **once** for the whole job — every window of every clip
goes through one session.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _frame(media: str, ts: float):
    """Pull one BGR frame from `media` at `ts`, through the ffmpeg binary.

    PNG down the pipe and `cv2.imdecode` back, rather than rawvideo reshaped to
    a size passed in the job: the size would be proofcut's manifest talking about
    the file, and a reshape against a stale width is not an error — it is a
    sheared frame that detects plausible faces in the wrong places. Here the
    decoder tells us the geometry and there is nothing to disagree with.

    Deliberately not `ffmpeg-python`: this file's dependencies are whatever the
    resolved interpreter happens to have, and every extra import is another way
    for it to fail on a box that could otherwise detect. numpy and cv2 come with
    insightface; a convenience wrapper around a subprocess does not.
    """
    import cv2
    import numpy as np

    completed = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error",
            "-ss", f"{ts:.4f}", "-i", media,
            "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:",
        ],
        capture_output=True,
        check=True,
    )  # fmt: skip
    if not completed.stdout:
        raise RuntimeError(
            f"ffmpeg returned no frame at {ts:.3f}s — seeking past the end of {media}?"
        )
    image = cv2.imdecode(np.frombuffer(completed.stdout, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not decode the frame ffmpeg returned at {ts:.3f}s")
    return image


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    destination = Path(sys.argv[2])

    from insightface.app import FaceAnalysis

    # `allowed_modules=["detection"]` is the whole reason this is cheap: the
    # buffalo_l pack also carries recognition, landmark and gender/age models
    # worth ~300 MB that a framing pass has no use for. ctx_id=-1 is CPU.
    app = FaceAnalysis(
        name=job["model"], allowed_modules=["detection"], providers=job["providers"]
    )
    app.prepare(ctx_id=-1, det_size=(job["det_size"], job["det_size"]))

    results = []
    for done, window in enumerate(job["windows"], 1):
        try:
            frames = []
            for ts in window["timestamps"]:
                faces = app.get(_frame(window["media"], ts))
                frames.append(
                    {
                        "ts": ts,
                        "faces": [
                            {
                                "box": [float(v) for v in face.bbox],
                                "score": float(face.det_score),
                            }
                            for face in faces
                        ],
                    }
                )
            results.append({"index": window["index"], "frames": frames})
        except Exception as exc:  # noqa: BLE001 — reported per window, not fatal
            results.append({"index": window["index"], "error": f"{type(exc).__name__}: {exc}"})
        # `proofcut.progress.MARKER`'s line, restated: this interpreter imports
        # nothing from proofcut.
        sys.stderr.write(f"proofcut-progress {done} {len(job['windows'])}\n")
        sys.stderr.flush()

    destination.write_text(json.dumps({"results": results}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
