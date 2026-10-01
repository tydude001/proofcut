"""whisper out of GPU memory is retried on the CPU, not skipped.

On 2026-09-30 a `llama-server` holding 9.3 GB of the card made every render's
audio verify skip with `torch.OutOfMemoryError` (wiki troubleshooting.md). The
stand-in whisper here fails that way unless it is given `--device cpu`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from stubs import write_stub

from proofcut import asr


def _whisper(folder: Path, *, error: str) -> Path:
    """A whisper that prints `error` and exits 1 unless `--device cpu`, and
    logs every call's device to `<folder>/calls`."""
    folder.mkdir(parents=True, exist_ok=True)
    return write_stub(
        folder / "fake-whisper",
        "import argparse, json, sys\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('media', nargs='+')\n"
        "p.add_argument('--model')\n"
        "p.add_argument('--output_format')\n"
        "p.add_argument('--word_timestamps')\n"
        "p.add_argument('--output_dir')\n"
        "p.add_argument('--verbose', default=None)\n"
        "p.add_argument('--language', default=None)\n"
        "p.add_argument('--device', default='cuda')\n"
        "args = p.parse_args()\n"
        f"with open({str(folder / 'calls')!r}, 'a') as f:\n"
        "    f.write(args.device + '\\n')\n"
        "if args.device != 'cpu':\n"
        f"    print({error!r}, file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "for m in args.media:\n"
        "    words = [{'word': ' hello', 'start': 0.1, 'end': 0.4}]\n"
        "    (Path(args.output_dir) / f'{Path(m).stem}.json').write_text(\n"
        "        json.dumps({'language': 'en', 'text': ' hello', 'words': words}))\n",
    )


def _calls(folder: Path) -> list[str]:
    return (folder / "calls").read_text().splitlines()


def test_cuda_oom_is_retried_on_the_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "whisper"
    oom = "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB"
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_whisper(folder, error=oom)))
    media = tmp_path / "clip.wav"
    media.write_bytes(b"not really audio")

    payload = asr.transcribe(media, model="tiny")

    assert [w["word"] for w in payload["words"]] == [" hello"]
    assert _calls(folder) == ["cuda", "cpu"]
    captured = capsys.readouterr()
    assert "retrying on the CPU" in captured.err
    assert captured.out == ""


def test_any_other_failure_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "whisper"
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_whisper(folder, error="RuntimeError: bad file")))
    media = tmp_path / "clip.wav"
    media.write_bytes(b"not really audio")

    with pytest.raises(asr.ASRError, match="bad file"):
        asr.transcribe(media, model="tiny")
    assert _calls(folder) == ["cuda"]
