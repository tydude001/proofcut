"""`verify` and `finish_check` hear a sound's and an inset's own words.

HISTORY.md § B7, run three: the narrator's take was a sound over a silent
screen recording, so the timeline had no words and both checks refused
every render ("no transcribed word survives on the timeline") — the agent
shipped with no speech check at all. A sound or an audible inset plays a
known slice of a registered clip at a known render second, so when that
clip has a transcript its words join the expectation, merged by where they
play. A voice sound (`ducks`) with no transcript is named in the refusal.

`transcript_path` stands in for whisper, `test_retime.py`'s shape.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut import verify as vfy
from proofcut.project import Project

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

RECT = [218, 63, 446, 234]
VO = [("three", 0.2, 0.5), ("men", 0.6, 0.9), ("leave", 1.0, 1.3), ("earth", 1.5, 1.9),
      ("four", 4.0, 4.3), ("days", 4.5, 4.9)]  # fmt: skip
FILM = [("one", 0.2, 0.4), ("small", 1.2, 1.5), ("step", 2.0, 2.4), ("giant", 3.2, 3.6)]


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("placed-audio")
    rec, film = root / "rec.mp4", root / "film.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=10", "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(rec))  # fmt: skip
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=700:duration=4:sample_rate=48000",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(film))  # fmt: skip
    out = {"rec": rec, "film": film}
    for name in ("vo", "vo2"):
        out[name] = root / f"{name}.wav"
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=6:sample_rate=48000", str(out[name]))
    return out


def _words(clip_id: str, words: list[tuple[str, float, float]]) -> tx.Transcript:
    return tx.Transcript(
        clip_id=clip_id,
        words=tuple(tx.Word(index=i, text=t, start=s, end=e) for i, (t, s, e) in enumerate(words)),
    )


@pytest.fixture
def project(tmp_path: Path, media: dict[str, Path]) -> Project:
    """A silent recording on the timeline, with a narrator take and a film to place."""
    root = tmp_path / "proj"
    Project.create(root)
    for source in media.values():
        ops.import_media(root, source, sheet=False)
    project = Project.open(root)
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    assert clips["rec"]["has_audio"] is False
    tx.save(_words("vo", VO), project.transcript_path("vo"))
    tx.save(_words("film", FILM), project.transcript_path("film"))
    tl.write(tl.to_otio(tl.Edit([tl.Segment("rec", 0.0, 10.0)]), clips, rate=1000.0), project.timeline_path)
    for name, at in (("early", 1.0), ("words", 2.0), ("film", 6.0)):
        ops.events(root, "rec", name=name, at=at)
    return Project.open(root)


def _heard(tmp_path: Path, texts: list[str]) -> Path:
    path = tmp_path / "heard.json"
    words = [{"word": t, "start": float(i), "end": i + 0.4} for i, t in enumerate(texts)]
    path.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return path


def test_a_voice_sound_over_a_silent_recording_is_what_verify_expects(project: Project, tmp_path: Path) -> None:
    ops.sound_add(project.root, "vo", "rec", event="words", src_out=2.0, ducks=True)

    result = ops.verify(project.root, tmp_path / "render.mp4", transcript_path=_heard(tmp_path, ["three", "men", "leave", "earth"]))

    assert result["expected_words"] == 4
    assert result["similarity"] == pytest.approx(1.0)
    assert result["placed_audio"] == [{"kind": "sound", "position": 0, "assets": ["vo"], "words": 4}]
    assert result["voice_sounds_untranscribed"] == []


def test_a_missing_word_of_the_voice_is_found(project: Project, tmp_path: Path) -> None:
    ops.sound_add(project.root, "vo", "rec", event="words", src_out=2.0, ducks=True)

    result = ops.verify(project.root, tmp_path / "render.mp4", transcript_path=_heard(tmp_path, ["three", "men", "earth"]))

    assert result["similarity"] < 1.0
    assert "-leave" in result["diff"]


def test_sounds_are_merged_by_where_they_play_not_by_record_order(project: Project, tmp_path: Path) -> None:
    ops.sound_add(project.root, "vo", "rec", event="film", src_in=4.0)
    ops.sound_add(project.root, "vo", "rec", event="early", src_out=2.0)

    result = ops.verify(
        project.root,
        tmp_path / "render.mp4",
        transcript_path=_heard(tmp_path, ["three", "men", "leave", "earth", "four", "days"]),
    )

    assert result["expected_words"] == 6
    assert result["similarity"] == pytest.approx(1.0)


def test_an_untranscribed_click_is_ignored_and_an_untranscribed_voice_is_named(project: Project, tmp_path: Path) -> None:
    ops.sound_add(project.root, "vo2", "rec", event="early")
    ops.sound_add(project.root, "vo2", "rec", event="words", ducks=True)

    with pytest.raises(vfy.VerifyError, match=r"sound 1 \(vo2\) is marked as a voice \(ducks\) but its clip has no transcript"):
        ops.verify(project.root, tmp_path / "render.mp4", transcript_path=_heard(tmp_path, ["x"]))


def test_an_audible_inset_says_its_own_slice_and_a_muted_one_says_nothing(project: Project, tmp_path: Path) -> None:
    ops.inset_add(project.root, "rec", "film", RECT, event="film", src_in=1.0, seconds=2.0)
    ops.inset_add(project.root, "rec", "film", RECT, event="early", seconds=2.0, mute=True)

    result = ops.verify(project.root, tmp_path / "render.mp4", transcript_path=_heard(tmp_path, ["small", "step"]))

    assert result["expected_words"] == 2
    assert result["similarity"] == pytest.approx(1.0)
    assert result["placed_audio"] == [{"kind": "inset", "position": 0, "assets": ["film"], "words": 2}]


def test_finish_check_no_longer_refuses_a_voice_sound_film(
    project: Project, tmp_path: Path, media: dict[str, Path]
) -> None:
    ops.sound_add(project.root, "vo", "rec", event="words", src_out=2.0, ducks=True)
    final = tmp_path / "final.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=10:sample_rate=48000", str(final))

    result = ops.finish_check(project.root, final, transcript_path=_heard(tmp_path, ["three", "men", "leave", "earth"]))

    assert result["expected_words"] == 4
    assert result["similarity"] == pytest.approx(1.0)
    assert result["missing"] == []
    assert result["placed_audio"][0]["words"] == 4
