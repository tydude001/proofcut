"""The synthesiser side of `tts`, run under a *different* interpreter.

**This module is never imported by proofcut.** It is executed by the Python that
`tts.tts_python()` resolves — a venv with `qwen_tts` and a CUDA torch — which is
the whole reason it is a separate file, exactly as `_vlm_worker.py` and
`_face_worker.py` keep torch and onnxruntime out of proofcut's own venv.

It reads one JSON job from `argv[1]` and writes one JSON result to `argv[2]`;
a file rather than stdout because transformers and qwen_tts both print to
whichever stream they feel like ("flash-attn is not installed…"), and that
landing in a JSON document is a parse error that reads like a synth failure.

The model is loaded **once** for the job and every seed renders through it.
The likeness number is the model's own speaker encoder — the same one it
conditions on — so `sim` is cosine(embedding(render), embedding(reference)).

`spread` is the second number, and it exists because the first one cannot see
flatness: likeness-only ranking systematically keeps the *flattest* read
(goodsometimes, 2026-08-24 — every one of the six flattest winners in a
38-chunk essay had a livelier take among its losing seeds, one lost on a dead
sim tie). It is the std of voiced pitch in semitones around the median (pyin,
60–400 Hz), the local-llm round-2 proxy for how much the read moves; it cannot
rank *where* emphasis lands, only whether there is any.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    destination = Path(sys.argv[2])

    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    # `tts.device()`; absent is what every job before the key meant. Plain
    # `cuda` keeps the `cuda:0` this always loaded onto.
    device = job.get("device") or "cuda"
    model = Qwen3TTSModel.from_pretrained(
        job["model"],
        device_map="cuda:0" if device == "cuda" else device,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )

    def pitch_spread(audio: np.ndarray, rate: int = 24000) -> float | None:
        """Std of voiced f0 in semitones around its median — None under 20 voiced frames."""
        f0 = librosa.pyin(audio, fmin=60, fmax=400, sr=rate)[0]
        voiced = f0[~np.isnan(f0)]
        if voiced.size < 20:
            return None
        return round(float(np.std(12 * np.log2(voiced / np.median(voiced)))), 2)

    @torch.inference_mode()
    def embed(audio: np.ndarray) -> np.ndarray:
        e = model.model.extract_speaker_embedding(audio.astype(np.float32), 24000)
        e = e.float().cpu().numpy().reshape(-1)
        return e / (np.linalg.norm(e) or 1.0)

    ref_audio, _ = librosa.load(job["ref_audio"], sr=24000, mono=True)
    ref = embed(ref_audio)

    out_dir = Path(job["out_dir"])
    candidates = []
    for done, seed in enumerate(job["seeds"], 1):
        try:
            torch.manual_seed(int(seed))
            torch.cuda.manual_seed_all(int(seed))
            wavs, sr = model.generate_voice_clone(
                text=job["text"],
                language=job["language"],
                ref_audio=job["ref_audio"],
                ref_text=job["ref_text"],
                max_new_tokens=int(job["max_new_tokens"]),
            )
            wav = np.asarray(wavs[0], dtype=np.float32)
            path = out_dir / f"s{int(seed)}.wav"
            sf.write(str(path), wav, sr)
            audio = wav if sr == 24000 else librosa.resample(wav, orig_sr=sr, target_sr=24000)
            candidates.append(
                {
                    "seed": int(seed),
                    "path": str(path),
                    "duration": round(len(wav) / sr, 3),
                    "sim": round(float(embed(audio) @ ref), 4),
                    "spread": pitch_spread(audio),
                }
            )
        except Exception as exc:  # noqa: BLE001 — reported per seed, not fatal
            candidates.append({"seed": int(seed), "error": f"{type(exc).__name__}: {exc}"})
        # `proofcut.progress.MARKER`'s line, restated: this interpreter imports
        # nothing from proofcut.
        sys.stderr.write(f"proofcut-progress {done} {len(job['seeds'])}\n")
        sys.stderr.flush()

    destination.write_text(json.dumps({"model": job["model"], "candidates": candidates}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
