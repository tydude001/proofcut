"""The model store (`proofcut.store`) and the two whisper passes it wraps —
docs/plans/MODEL-CACHE.md steps 1 to 3.

A hit is only worth something if the things that must miss do miss, so every
hit test here sits beside its negative controls: other bytes, another model,
another whisper binary, other windows. Whisper is a stub that counts its own
invocations in a file beside it, which is the thing under test — "the model
did not run" — rather than the time a call took.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import wave
from pathlib import Path

import pytest
from stubs import write_stub

from proofcut import asr, ops, store
from proofcut.project import Project

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

RATE = 16000


def _wav(dest: Path, *, duration: float, freq: float = 440.0) -> Path:
    frames = int(duration * RATE)
    samples = bytearray()
    for i in range(frames):
        samples += struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / RATE)))
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(samples))
    return dest


def _counting_whisper(folder: Path, *, name: str = "fake-whisper", padding: str = "") -> Path:
    """A whisper that writes two words per input and appends a line to
    `<folder>/calls` for every invocation. `padding` changes the stub's size,
    which is how a rebuilt binary is stood in for."""
    folder.mkdir(parents=True, exist_ok=True)
    return write_stub(
        folder / name,
        f"# {padding}\n"
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('media', nargs='+')\n"
        "p.add_argument('--model')\n"
        "p.add_argument('--output_format')\n"
        "p.add_argument('--word_timestamps')\n"
        "p.add_argument('--output_dir')\n"
        "p.add_argument('--verbose', default=None)\n"
        "p.add_argument('--language', default=None)\n"
        "args = p.parse_args()\n"
        f"with open({str(folder / 'calls')!r}, 'a') as f:\n"
        "    f.write(args.model + '\\n')\n"
        "for m in args.media:\n"
        "    stem = Path(m).stem\n"
        "    words = [{'word': ' hello', 'start': 0.1, 'end': 0.4},\n"
        "             {'word': ' world', 'start': 0.5, 'end': 0.9}]\n"
        "    (Path(args.output_dir) / f'{stem}.json').write_text(\n"
        "        json.dumps({'language': 'en', 'text': ' hello world', 'words': words}))\n",
    )


def _calls(folder: Path) -> int:
    log = folder / "calls"
    return len(log.read_text().splitlines()) if log.exists() else 0


@pytest.fixture
def whisper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "whisper"
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_counting_whisper(folder)))
    return folder


# --- step 1: the store itself ------------------------------------------------


def test_the_store_lives_beside_setup_s_folder_unless_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROOFCUT_STORE", str(tmp_path / "elsewhere"))
    assert store.root() == tmp_path / "elsewhere"
    monkeypatch.delenv("PROOFCUT_STORE")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    assert store.root() == tmp_path / "proofcut" / "store"


def test_a_digest_is_memoised_and_read_from_disk_again_once_the_file_changes(tmp_path: Path) -> None:
    media = tmp_path / "a.bin"
    media.write_bytes(b"x" * 1000)
    memo = tmp_path / "cache" / "digests.json"
    first = store.digest(media, memo)
    assert len(first) == 64 and memo.exists()

    # The memo is what answers while the stamp holds: a planted value comes back.
    known = json.loads(memo.read_text())
    known[str(media.resolve())]["sha256"] = "f" * 64
    memo.write_text(json.dumps(known))
    assert store.digest(media, memo) == "f" * 64

    # Same size, new bytes, new mtime: the memo is stale and the file is re-read.
    media.write_bytes(b"y" * 1000)
    stat = media.stat()
    os.utime(media, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = store.digest(media, memo)
    assert second not in (first, "f" * 64)
    assert second == store.digest(media)  # no memo: a plain full hash


def test_an_unreadable_memo_costs_a_hash_not_a_failure(tmp_path: Path) -> None:
    media = tmp_path / "a.bin"
    media.write_bytes(b"abc")
    memo = tmp_path / "digests.json"
    memo.write_text("{not json")
    assert store.digest(media, memo) == store.digest(media)
    assert json.loads(memo.read_text())  # rewritten whole


def test_get_misses_on_an_entry_whose_fields_differ_or_that_cannot_be_read() -> None:
    fields = {"digest": "d" * 64, "model": "turbo"}
    assert store.get("whisper", fields) is None
    store.put("whisper", fields, {"words": [1]})
    assert store.get("whisper", fields) == {"words": [1]}
    assert store.get("whisper", {**fields, "model": "small"}) is None

    entry = next(store.root().rglob("entry.json"))
    entry.write_text("{truncated")
    assert store.get("whisper", fields) is None


# --- step 2: the single pass --------------------------------------------------


def test_the_same_bytes_transcribe_once_even_from_another_path(tmp_path: Path, whisper: Path) -> None:
    audio = _wav(tmp_path / "vo.wav", duration=1.0)
    first = asr.transcribe(audio)
    assert first["store"] == "miss" and _calls(whisper) == 1

    copy = tmp_path / "other" / "vo-copy.wav"
    copy.parent.mkdir()
    shutil.copy(audio, copy)
    second = asr.transcribe(copy)
    assert second["store"] == "hit" and _calls(whisper) == 1
    assert second["words"] == first["words"]
    assert second["hallucinated_words"] == first["hallucinated_words"] == 0


def test_other_bytes_another_model_or_another_binary_each_miss(
    tmp_path: Path, whisper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = _wav(tmp_path / "vo.wav", duration=1.0)
    asr.transcribe(audio)
    assert _calls(whisper) == 1

    # Other bytes: the negative control MODEL-CACHE.md names, a re-encode.
    reencoded = _wav(tmp_path / "vo2.wav", duration=1.0, freq=441.0)
    assert asr.transcribe(reencoded)["store"] == "miss"
    assert asr.transcribe(audio, model="small")["store"] == "miss"
    assert asr.transcribe(audio, language="en")["store"] == "miss"
    assert _calls(whisper) == 4

    # A rebuilt whisper — same name, different size — must not answer with
    # the old one's words.
    rebuilt = tmp_path / "rebuilt"
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_counting_whisper(rebuilt, padding="v2" * 40)))
    assert asr.transcribe(audio)["store"] == "miss"
    assert _calls(rebuilt) == 1


def test_the_hallucination_guard_runs_on_a_hit_because_the_raw_output_is_stored(
    tmp_path: Path, whisper: Path
) -> None:
    audio = _wav(tmp_path / "vo.wav", duration=1.0)
    asr.transcribe(audio)
    (entry,) = store.root().rglob("entry.json")
    stored = json.loads(entry.read_text())["payload"]
    assert "hallucinated_words" not in stored and "store" not in stored


@needs_ffmpeg
def test_a_second_project_on_the_same_media_gets_the_same_transcript_without_whisper(
    tmp_path: Path, whisper: Path
) -> None:
    audio = _wav(tmp_path / "vo.wav", duration=2.0)
    replies, transcripts = [], []
    for name in ("one", "two"):
        project = Project.create(tmp_path / name)
        manifest = project.read_manifest()
        manifest["clips"] = [
            {"clip_id": "vo", "source": str(audio), "duration": 2.0, "has_video": False, "has_audio": True}
        ]
        project.write_manifest(manifest)
        replies.append(ops.transcribe(project.root, "vo"))
        transcripts.append(project.transcript_path("vo").read_bytes())
        assert (project.root / ops.DIGESTS_FILE).exists()

    assert [r["store"] for r in replies] == ["miss", "hit"]
    assert _calls(whisper) == 1
    assert transcripts[0] == transcripts[1]


# --- step 3: the windowed pass ------------------------------------------------


@needs_ffmpeg
def test_the_windowed_pass_hits_on_the_same_windows_and_misses_on_any_other(
    tmp_path: Path, whisper: Path
) -> None:
    audio = _wav(tmp_path / "vo.wav", duration=12.0)
    first = asr.transcribe_windowed(audio, window=5.0, overlap=0.0)
    assert first["store"] == "miss" and _calls(whisper) == 1

    again = asr.transcribe_windowed(audio, window=5.0, overlap=0.0)
    assert again["store"] == "hit" and _calls(whisper) == 1
    assert again["words"] == first["words"] and again["windows"] == first["windows"]

    assert asr.transcribe_windowed(audio, window=5.0, overlap=2.0)["store"] == "miss"
    assert asr.transcribe_windowed(audio, window=4.0, overlap=0.0)["store"] == "miss"
    assert asr.transcribe_windowed(audio, window=5.0, overlap=0.0, start=1.0)["store"] == "miss"
    assert asr.transcribe_windowed(audio, window=5.0, overlap=0.0, model="turbo")["store"] == "miss"
    assert _calls(whisper) == 5


@needs_ffmpeg
def test_a_windowed_hit_is_not_the_single_pass_s_entry(tmp_path: Path, whisper: Path) -> None:
    audio = _wav(tmp_path / "vo.wav", duration=6.0)
    asr.transcribe(audio, model="small")
    assert asr.transcribe_windowed(audio, model="small")["store"] == "miss"
    assert asr.transcribe(audio, model="small")["store"] == "hit"
