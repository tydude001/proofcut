"""`ops.hear` and the span support behind it in `asr.transcribe_windowed`,
plus `speech_overlap`'s energy-only clip side — TRIAL.md § The queue, items
1 and 3.

No real whisper: `PROOFCUT_WHISPER` points `asr` at a stand-in that writes one
fixed word per window, window-relative, so the absolute stamps coming back
are the thing under test. Real ffmpeg for the decode.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import wave
from pathlib import Path

import pytest
from stubs import write_stub

from proofcut import asr, energy, ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

RATE = 16000


def _wav(dest: Path, *, duration: float, tones: list[tuple[float, float]] = ()) -> Path:
    """A mono 16 kHz WAV: silence, with a 440 Hz tone over each `(start, end)`."""
    frames = int(duration * RATE)
    samples = bytearray()
    for i in range(frames):
        t = i / RATE
        loud = any(a <= t < b for a, b in tones)
        v = int(12000 * math.sin(2 * math.pi * 440 * t)) if loud else 0
        samples += struct.pack("<h", v)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(samples))
    return dest


def _fake_whisper(path: Path, *, empty: bool = False) -> Path:
    """One word per window, at 0.3s into it: `w0000` -> 'alpha', `w0001` ->
    'bravo', ... — window-relative, exactly as real whisper stamps a slice."""
    return write_stub(
        path / "fake-whisper",
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
        "names = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot']\n"
        "for m in args.media:\n"
        "    stem = Path(m).stem\n"
        "    idx = int(stem.lstrip('w'))\n"
        f"    words = [] if {empty!r} else [{{'word': names[idx % len(names)], 'start': 0.3, 'end': 0.6}}]\n"
        "    (Path(args.output_dir) / f'{stem}.json').write_text(json.dumps({'language': 'en', 'words': words}))\n",
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_fake_whisper(tmp_path)))
    audio = _wav(tmp_path / "clip.wav", duration=30.0, tones=[(12.0, 14.0)])
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = [
        {"clip_id": "clip", "source": str(audio), "duration": 30.0, "has_video": False, "has_audio": True}
    ]
    project.write_manifest(manifest)
    return project


# --- asr: the span ------------------------------------------------------------

@needs_ffmpeg
def test_a_span_lays_its_windows_from_start_and_stamps_words_in_the_file_s_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_fake_whisper(tmp_path)))
    audio = _wav(tmp_path / "a.wav", duration=30.0)
    out = asr.transcribe_windowed(audio, start=10.0, end=20.0, window=5.0, overlap=0.0)
    assert out["windows"] == 2 and (out["start"], out["end"]) == (10.0, 20.0)
    # window 0 covers [10, 15): its word at 0.3 is the file's 10.3
    assert [round(w["start"], 3) for w in out["words"]] == [10.3, 15.3]
    assert [w["word"] for w in out["words"]] == ["alpha", "bravo"]


@needs_ffmpeg
def test_a_span_past_the_audio_is_refused_not_clamped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_fake_whisper(tmp_path)))
    audio = _wav(tmp_path / "a.wav", duration=5.0)
    with pytest.raises(asr.ASRError, match="5.000s long"):
        asr.transcribe_windowed(audio, start=1.0, end=9.0)
    with pytest.raises(asr.ASRError, match="empty or backwards"):
        asr.transcribe_windowed(audio, start=3.0, end=3.0)
    with pytest.raises(asr.ASRError, match="at least 0"):
        asr.transcribe_windowed(audio, start=-1.0, end=3.0)


@needs_ffmpeg
def test_silence_raises_by_default_and_is_an_answer_with_allow_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_fake_whisper(tmp_path, empty=True)))
    audio = _wav(tmp_path / "a.wav", duration=5.0)
    with pytest.raises(asr.ASRError, match="heard no speech"):
        asr.transcribe_windowed(audio)
    out = asr.transcribe_windowed(audio, allow_silence=True)
    assert out["words"] == [] and out["silent_windows"] == out["windows"]


@needs_ffmpeg
def test_the_default_pass_is_byte_identical_to_a_full_span(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROOFCUT_WHISPER", str(_fake_whisper(tmp_path)))
    audio = _wav(tmp_path / "a.wav", duration=12.0)
    whole = asr.transcribe_windowed(audio)
    spanned = asr.transcribe_windowed(audio, start=0.0, end=12.0)
    # The same windows are the same model-store entry (MODEL-CACHE.md), so the
    # span is answered from the store; everything else must match exactly.
    assert (whole.pop("store"), spanned.pop("store")) == ("miss", "hit")
    assert whole == spanned


# --- ops.hear ---------------------------------------------------------------

@needs_ffmpeg
def test_hear_reports_the_heard_words_beside_the_transcript_s_and_attaches_nothing(project: Project) -> None:
    tx.save(
        tx.Transcript(
            clip_id="clip",
            words=(
                tx.Word(index=0, text="before", start=8.0, end=8.5),
                tx.Word(index=1, text="inside", start=12.0, end=12.4),
                tx.Word(index=2, text="straddles", start=19.5, end=23.0),
                tx.Word(index=3, text="after", start=25.0, end=25.5),
            ),
        ),
        project.transcript_path("clip"),
    )
    before = project.transcript_path("clip").read_bytes()
    out = ops.hear(project.root, "clip", start=10.0, end=20.0, window=5.0, overlap=0.0)
    assert out["heard_text"] == "alpha bravo"
    assert [w["start"] for w in out["heard_words"]] == [pytest.approx(10.3), pytest.approx(15.3)]
    # overlap, never containment: the word that straddles the end is shown
    assert [w["text"] for w in out["transcript_words"]] == ["inside", "straddles"]
    assert out["transcript_text"] == "inside straddles"
    assert out["attached"] is False
    assert project.transcript_path("clip").read_bytes() == before
    assert out["model"] == asr.WINDOWED_MODEL and out["windows"] == 2


@needs_ffmpeg
def test_hear_without_a_transcript_reports_None_for_the_transcript_side(project: Project) -> None:
    out = ops.hear(project.root, "clip", start=0.0, end=5.0)
    assert out["transcript_words"] is None and out["transcript_text"] is None
    assert out["heard_text"] == "alpha"
    assert not project.transcript_path("clip").exists()


@needs_ffmpeg
def test_hear_refuses_a_span_the_clip_does_not_have(project: Project) -> None:
    with pytest.raises(tl.TimelineError, match="30.000s long"):
        ops.hear(project.root, "clip", start=25.0, end=31.0)
    with pytest.raises(tl.TimelineError, match="empty or backwards"):
        ops.hear(project.root, "clip", start=5.0, end=5.0)
    with pytest.raises(tl.TimelineError, match="negative"):
        ops.hear(project.root, "clip", start=-1.0, end=5.0)


# --- energy.sound_runs ----------------------------------------------------------

def test_sound_runs_finds_the_loud_stretch_and_drops_a_click() -> None:
    quiet, loud = 10.0, 5000.0
    env = [quiet] * 100 + [loud] * 50 + [quiet] * 100 + [loud] * 2 + [quiet] * 100
    out = energy.sound_runs(env, frame=0.02)
    assert out["runs"] == [(2.0, 3.0)]          # frames 100..150 at 20 ms
    assert out["sound_seconds"] == pytest.approx(1.0)
    assert out["quiet_db"] < out["threshold_db"] < out["loud_db"]
    assert energy.sound_runs(env, frame=0.02, min_run=0.0)["runs"] == [(2.0, 3.0), (5.0, 5.04)]


def test_sound_runs_needs_an_envelope() -> None:
    with pytest.raises(energy.EnergyError):
        energy.sound_runs([])


# --- speech_overlap: the energy-only clip side --------------------------------

def _vo_project(tmp_path: Path, broll: Path, *, has_audio: bool = True) -> Project:
    project = Project.create(tmp_path / "proj")
    clips = {
        "vo": {"clip_id": "vo", "source": "/tmp/vo.wav", "duration": 10.0, "has_video": False, "has_audio": True},
        "broll": {"clip_id": "broll", "source": str(broll), "duration": 6.0, "has_video": True, "has_audio": has_audio},
    }
    manifest = project.read_manifest()
    manifest["clips"] = list(clips.values())
    project.write_manifest(manifest)
    edit = tl.Edit([tl.Segment("vo", 0.0, 10.0)])
    tl.write(tl.to_otio(edit, clips, rate=1000.0, name="proj"), project.timeline_path)
    tx.save(
        tx.Transcript(
            clip_id="vo",
            words=(
                tx.Word(index=0, text="thesis", start=3.0, end=3.4),
                tx.Word(index=1, text="line", start=3.5, end=3.9),
                tx.Word(index=2, text="later", start=8.0, end=8.4),
            ),
        ),
        project.transcript_path("vo"),
    )
    return project


@needs_ffmpeg
def test_speech_overlap_falls_back_to_the_clip_s_energy_when_it_has_no_transcript(tmp_path: Path) -> None:
    broll = _wav(tmp_path / "broll.wav", duration=6.0, tones=[(1.0, 2.0)])
    project = _vo_project(tmp_path, broll)
    out = ops.speech_overlap(project.root, "broll", at=2.0)
    assert out["clip_evidence"] == "energy"
    assert out["clip_words"] == []
    assert out["clip_energy"]["runs_in_clip"] == 1
    [run] = out["clip_runs"]
    assert run["timeline_start"] == pytest.approx(3.0, abs=0.05)   # 2.0 + 1.0
    assert run["timeline_end"] == pytest.approx(4.0, abs=0.05)
    assert run["words"] == []
    # the tone sits under "thesis line": an overlap, read off sound alone
    assert [w["text"] for w in out["overlaps"][0]["vo_words"]] == ["thesis", "line"]
    assert out["overlaps"][0]["clip_words"] == []


@needs_ffmpeg
def test_speech_overlap_energy_respects_the_proposed_window(tmp_path: Path) -> None:
    broll = _wav(tmp_path / "broll.wav", duration=6.0, tones=[(1.0, 2.0), (4.0, 5.0)])
    project = _vo_project(tmp_path, broll)
    out = ops.speech_overlap(project.root, "broll", at=0.0, clip_in=3.0, clip_out=6.0)
    assert [(round(r["timeline_start"], 1), round(r["timeline_end"], 1)) for r in out["clip_runs"]] == [(1.0, 2.0)]


def test_speech_overlap_can_be_told_to_refuse_without_a_transcript(tmp_path: Path) -> None:
    project = _vo_project(tmp_path, tmp_path / "missing.wav")
    with pytest.raises(tx.TranscriptError, match="transcribe broll"):
        ops.speech_overlap(project.root, "broll", clip_evidence="transcript")
    with pytest.raises(Exception, match="clip_evidence"):
        ops.speech_overlap(project.root, "broll", clip_evidence="loud")


def test_speech_overlap_energy_refuses_a_clip_with_no_audio_track(tmp_path: Path) -> None:
    project = _vo_project(tmp_path, tmp_path / "silent.mp4", has_audio=False)
    with pytest.raises(Exception, match="no audio track"):
        ops.speech_overlap(project.root, "broll")


@needs_ffmpeg
def test_speech_overlap_energy_can_be_forced_on_a_clip_that_has_a_transcript(tmp_path: Path) -> None:
    broll = _wav(tmp_path / "broll.wav", duration=6.0, tones=[(1.0, 2.0)])
    project = _vo_project(tmp_path, broll)
    tx.save(
        tx.Transcript(clip_id="broll", words=(tx.Word(index=0, text="hi", start=1.0, end=1.3),)),
        project.transcript_path("broll"),
    )
    by_words = ops.speech_overlap(project.root, "broll")
    by_energy = ops.speech_overlap(project.root, "broll", clip_evidence="energy")
    assert by_words["clip_evidence"] == "transcript" and by_words["clip_words"][0]["text"] == "hi"
    assert by_energy["clip_evidence"] == "energy" and by_energy["clip_words"] == []
    assert json.dumps(by_energy["clip_energy"])  # serialisable, for the CLI's _emit
