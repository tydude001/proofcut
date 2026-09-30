"""`no_repeat_heard` and `length_on_brief` — the trial's two checks off the delivered file.

Both exist because the local director's 2026-09-22 real-footage film scored
10/10 while opening with its first line twice and running 93.7 s against an
asked-for 45 (docs/plans/LOCAL.md § The rerun with `hear` on the map). The
heard openings below are the windowed pass over that render and over Claude's
71 s cut of the same brief, read 2026-09-26.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agent_trial

LINE = "The first 12 minutes of Scream are still the best 12 minutes of horror in the 90s."
REST = "and I'm not going to spend this video telling you that because you probably already know."
LOCAL_FILM = f"{LINE} {LINE} {REST}"
CLAUDE_FILM = f"{LINE} {REST}"


def _heard(monkeypatch: pytest.MonkeyPatch, text: str | Exception, seconds: float = 60.0) -> None:
    def transcribe(*_a: object, **_k: object) -> dict:
        if isinstance(text, Exception):
            raise text
        return {"words": [{"word": w} for w in text.split()]}

    monkeypatch.setattr(agent_trial.asr, "transcribe_windowed", transcribe)
    monkeypatch.setattr(agent_trial, "_render_seconds", lambda _f: (seconds, ""))


def test_a_retake_heard_in_the_render_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _heard(monkeypatch, LOCAL_FILM)
    got = agent_trial._repeat_heard_check(Path("cut.mp4"), {})
    assert got["check"] == "no_repeat_heard" and got["ok"] is False
    assert "first 12 minutes of scream" in got["detail"]


def test_ordinary_repetition_inside_a_sentence_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    # "12 minutes of" twice in one sentence is three words: speech, not a retake.
    _heard(monkeypatch, CLAUDE_FILM)
    facts: dict = {}
    got = agent_trial._repeat_heard_check(Path("cut.mp4"), facts)
    assert got["ok"] is True and facts["heard_render_words"] == len(CLAUDE_FILM.split())


@pytest.mark.parametrize("text", ["", RuntimeError("whisper OOM")])
def test_nothing_heard_is_unsettled_not_failed(monkeypatch: pytest.MonkeyPatch, text: object) -> None:
    _heard(monkeypatch, text)  # type: ignore[arg-type]
    assert agent_trial._repeat_heard_check(Path("cut.mp4"), {})["ok"] is None


def test_the_render_is_heard_to_the_end_of_its_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    # The film control: 16.100 s by the container, 16.043 s of audio. A span
    # ending at the container's length is refused, so none is asked for.
    asked: dict = {}

    def transcribe(_media: object, **kwargs: object) -> dict:
        asked.update(kwargs)
        return {"words": [{"word": w} for w in CLAUDE_FILM.split()]}

    monkeypatch.setattr(agent_trial.asr, "transcribe_windowed", transcribe)
    monkeypatch.setattr(agent_trial, "_render_seconds", lambda _f: (16.100, ""))
    assert agent_trial._repeat_heard_check(Path("cut.mp4"), {})["ok"] is True
    assert asked.get("end") is None and asked.get("allow_silence") is True


def test_the_timeline_stutter_rule_is_unchanged() -> None:
    # `min_run` defaults to the two words `no_stutter` has always counted.
    assert agent_trial.find_restart(["make", "names", "a", "make", "names", "a", "word"]) is not None
    assert agent_trial.find_restart(["make", "names", "a", "make", "names", "a", "word"], min_run=5) is None


def test_a_retake_with_its_opening_cut_away_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # The 2026-09-30 film: the first take's tail, the good take's eight-word
    # opening, then the tail again. Eight is past `STUTTER_GAP`, which passed it.
    tail = "The best 12 minutes of horror in the 90s."
    _heard(monkeypatch, f"{tail} {LINE} {REST}")
    got = agent_trial._repeat_heard_check(Path("cut.mp4"), {})
    assert got["ok"] is False and "best 12 minutes of horror in the 90s" in got["detail"]
    assert agent_trial.find_restart(f"{tail} {LINE}".split(), min_run=5) is None


def test_a_refrain_a_long_way_apart_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    far = " ".join(f"w{n}" for n in range(agent_trial.RETAKE_GAP + 1))
    _heard(monkeypatch, f"{LINE} {far} {LINE}")
    assert agent_trial._repeat_heard_check(Path("cut.mp4"), {})["ok"] is True


@pytest.mark.parametrize(
    ("want", "seconds", "ok"),
    [
        ({"max": 80}, 93.7, False),
        ({"max": 80}, 71.0, True),
        ({"max": 80}, 45.2, True),
        ({"min": 40, "max": 50}, 71.0, False),
        ({"min": 40}, 30.0, False),
        (None, 93.7, None),
        ({}, 93.7, None),
    ],
)
def test_length_against_the_declared_band(
    monkeypatch: pytest.MonkeyPatch, want: dict | None, seconds: float, ok: bool | None
) -> None:
    monkeypatch.setattr(agent_trial, "_render_seconds", lambda _f: (seconds, ""))
    got = agent_trial._length_check(Path("cut.mp4"), want, {})
    assert got["check"] == "length_on_brief" and got["ok"] is ok


def test_an_unreadable_render_length_is_unsettled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_trial, "_render_seconds", lambda _f: (None, "moov atom not found"))
    assert agent_trial._length_check(Path("cut.mp4"), {"max": 80}, {})["ok"] is None
