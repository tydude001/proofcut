"""The vision-model side of `describe`, run under a *different* interpreter.

**This module is never imported by proofcut.** It is executed by the Python that
`describe.vlm_python()` resolves — a venv with torch, transformers and
bitsandbytes in it — which is the whole reason it is a separate file. proofcut's
own venv stays free of torch, exactly as `asr.py` keeps whisper behind a
binary. It lives inside the package only so it ships with it.

It reads one JSON job from `argv[1]` and writes one JSON result to `argv[2]`.
A file rather than stdout because transformers, bitsandbytes and torch all
write to whichever stream they feel like, and a progress bar landing in the
middle of a JSON document is a parse error that reads like a model failure.

The model is loaded **once** for the whole job — every window of every clip
goes through one process. Loading is ~15s and the description of a single
window is ~3s, so a process per window would spend more time loading than
describing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _frames(media: str, timestamps: list[float], size: str) -> list:
    """Pull one RGB frame per timestamp, through the ffmpeg binary.

    Deliberately not `ffmpeg-python`: this file's dependencies are whatever
    the resolved interpreter happens to have, and every extra import is
    another way for it to fail on a box that could otherwise describe.
    torch, transformers, numpy and PIL are unavoidable; a convenience wrapper
    around a subprocess is not.
    """
    import numpy as np
    from PIL import Image

    width, height = (int(x) for x in size.split("x"))
    out = []
    for ts in timestamps:
        completed = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error",
                "-ss", f"{ts:.3f}", "-i", media,
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", size, "pipe:",
            ],
            capture_output=True,
            check=True,
        )
        want = width * height * 3
        if len(completed.stdout) < want:
            raise RuntimeError(
                f"ffmpeg returned {len(completed.stdout)} bytes for the frame at "
                f"{ts:.2f}s, wanted {want} — seeking past the end of {media}?"
            )
        buf = np.frombuffer(completed.stdout[:want], np.uint8).reshape(height, width, 3)
        out.append(Image.fromarray(buf))
    return out


def _load(model_path: str, device: str) -> tuple:
    """Qwen2.5-VL, 4-bit NF4 with bf16 compute, and its processor.

    The config every VRAM figure in PLAN.md § B-roll by description was
    measured under — until 2026-09-10 it was imported from a sibling repo's
    tagger at run time, and it is carried here unchanged so that proofcut can
    describe on a box that has never seen that repo.
    """
    import torch
    from transformers import (
        AutoProcessor,
        BitsAndBytesConfig,
        Qwen2_5_VLForConditionalGeneration,
    )

    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
    )
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_path,
        quantization_config=quant,
        device_map=device,
        torch_dtype=torch.bfloat16,
    ).eval()
    processor = AutoProcessor.from_pretrained(model_path)
    return model, processor


def _generate(
    model, processor, images: list, prompt: str, *, max_new_tokens: int, device: str
) -> str:
    """One greedy pass over the frames and the prompt; the decoded reply."""
    import torch

    content = [{"type": "image", "image": im} for im in images]
    content.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": content}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=list(images), return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    generated = out[:, inputs["input_ids"].shape[1] :]
    return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    destination = Path(sys.argv[2])

    # `describe.device()`; absent is what every job before the key meant.
    # Refused before torch is imported: the 4-bit load below is bitsandbytes,
    # which has no backend but CUDA, and an unquantised load on another device
    # is a different memory budget and a different model output — a decision
    # for whoever measures one (docs/plans/PORTABILITY.md step 3).
    device = job.get("device") or "cuda"
    if not device.startswith("cuda"):
        sys.stderr.write(
            f"the vision model loads 4-bit through bitsandbytes, which is CUDA-only; "
            f"device {device!r} has no path in this worker yet\n"
        )
        return 2

    model, processor = _load(job["model"], device)

    results = []
    for done, window in enumerate(job["windows"], 1):
        try:
            frames = _frames(window["media"], window["timestamps"], job["frame_size"])
            text = _generate(
                model,
                processor,
                frames,
                job["prompt"],
                max_new_tokens=job["max_new_tokens"],
                device=device,
            )
            results.append({"index": window["index"], "text": text})
        except Exception as exc:  # noqa: BLE001 — reported per window, not fatal
            results.append({"index": window["index"], "error": f"{type(exc).__name__}: {exc}"})
        # Freeing between windows is what keeps peak VRAM at one window's
        # worth rather than the run's. This box has 11.5 GiB usable and an
        # always-on llama-server holding 3.5 of it.
        _empty_cache()
        _progress(done, len(job["windows"]))

    destination.write_text(json.dumps({"results": results}), encoding="utf-8")
    return 0


def _progress(done: int, total: int) -> None:
    """`proofcut.progress.MARKER`'s line, restated: this runs under another
    interpreter and imports nothing from proofcut."""
    sys.stderr.write(f"proofcut-progress {done} {total}\n")
    sys.stderr.flush()


def _empty_cache() -> None:
    import torch

    torch.cuda.empty_cache()


if __name__ == "__main__":
    raise SystemExit(main())
