"""`no_stutter` — the trial's check for a fluffed take's opening left in the film.

The two sequences below are the surviving words of the two local-director films
that kept one (docs/plans/LOCAL.md § The score cannot see a stutter), read off
their projects on 2026-09-20; the clean ones are the two runs that did not and
the last Claude run. Both stutters scored full marks before this check existed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agent_trial

TAIL = "So the edit stays addressable, and the render can be checked against it."
HEAD = "This is a demo of ProofCut, a local first video editor."
CLEAN = f"{HEAD} Every cut you make names a word in the transcript. {TAIL}"
SAID_TWICE = (
    f"{HEAD} Every cut you make names Every cut you make names a word in the "
    f"transcript. {TAIL}"
)
FRAGMENT_FIRST = (
    f"{HEAD} Every cut you make names a... Every cut you make names a word in "
    f"the transcript. {TAIL}"
)


@pytest.mark.parametrize("text", [SAID_TWICE, FRAGMENT_FIRST])
def test_a_repeated_opening_is_found(text: str) -> None:
    found = agent_trial.find_restart(text.split())
    assert found is not None and found[:5] == ["every", "cut", "you", "make", "names"]


def test_a_clean_film_has_none() -> None:
    assert agent_trial.find_restart(CLEAN.split()) is None


def test_ordinary_repetition_is_not_a_restart() -> None:
    # A stopword-only run and a repeat a whole sentence apart are both speech.
    assert agent_trial.find_restart(["the", "end", "of", "the", "day", "and", "of", "the", "week"]) is None
    filler = [f"w{n}" for n in range(12)]
    far = ["a", "video", "editor", *filler, "a", "video", "editor"]
    assert agent_trial.find_restart(far) is None


def _project(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    words = [
        {"index": n, "text": w, "present": True} for n, w in enumerate(text.split())
    ]
    keeper = "names a word in the transcript."
    first = text.split().index("names", len(text.split()) - len(keeper.split()) - len(TAIL.split()))
    monkeypatch.setattr(
        agent_trial.ops,
        "resolve_phrase",
        lambda *_a, **_k: {"first_word": first, "last_word": first + 5},
    )
    monkeypatch.setattr(agent_trial.ops, "timeline_view", lambda *_a, **_k: {"words": words})


@pytest.mark.parametrize(
    ("text", "ok"), [(CLEAN, True), (SAID_TWICE, False), (FRAGMENT_FIRST, False)]
)
def test_check_reads_the_timeline(monkeypatch: pytest.MonkeyPatch, text: str, ok: bool) -> None:
    _project(monkeypatch, text)
    got = agent_trial._stutter_check(Path("proj"), "vo", agent_trial.KEEPER_PHRASE)
    assert got["check"] == "no_stutter" and got["ok"] is ok


def test_an_unreadable_timeline_is_unsettled_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> dict:
        raise RuntimeError("no transcript")

    monkeypatch.setattr(agent_trial.ops, "resolve_phrase", boom)
    got = agent_trial._stutter_check(Path("proj"), "vo", agent_trial.KEEPER_PHRASE)
    assert got["ok"] is None
