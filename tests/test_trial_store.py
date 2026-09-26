"""`agent_trial.py` reads the model store's hits out of the tool replies —
docs/plans/MODEL-CACHE.md step 6 — so a trial against a warm store says so
beside its timings, and a TRIAL.md row is never a warm run mistaken for a
cold one."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import agent_trial


def _events(replies: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    """An init, one tool call per reply, and a result — the smallest stream `analyse` reads."""
    events: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init", "tools": [name for name, _ in replies]}
    ]
    for i, (name, reply) in enumerate(replies):
        text = reply if isinstance(reply, str) else json.dumps(reply)
        events.append(
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": name, "input": {}}
            ]}}
        )
        events.append(
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}
            ]}}
        )
    events.append({"type": "result", "subtype": "success"})
    return events


def test_hits_are_counted_wherever_the_reply_carries_them() -> None:
    analysis = agent_trial.analyse(
        _events(
            [
                ("transcribe", {"clip_id": "vo", "store": "hit"}),
                ("describe", {"described": 5, "store": {"hit": 3, "miss": 2}}),
                # `verify`'s windowed pass nests its count.
                ("verify", {"mode": "windowed", "windowed": {"windows": 4, "store": "miss"}}),
                ("reframe_detect", {"count": 9, "store": {"hit": 9, "miss": 0}}),
                ("timeline_view", {"shots": []}),
                ("cut", "not json at all"),
            ]
        )
    )
    assert analysis["store_hits"] == 13 and analysis["store_misses"] == 3
    assert [c["store"] for c in analysis["calls"]][-2:] == [None, None]


def test_a_count_past_the_kept_text_is_still_read() -> None:
    reply = {"descriptions": ["x" * 5000], "store": {"hit": 1, "miss": 0}}
    (call,) = agent_trial.analyse(_events([("describe", reply)]))["calls"]
    assert call["store"] == {"hit": 1, "miss": 0}
    assert len(call["result_text"]) == 4000


def test_the_report_says_warm_cold_or_no_model() -> None:
    warm = agent_trial.analyse(_events([("transcribe", {"store": "hit"})]))
    cold = agent_trial.analyse(_events([("transcribe", {"store": "miss"})]))
    none = agent_trial.analyse(_events([("cut", {"ok": True})]))
    assert "**warm**" in agent_trial._store_line(warm)
    assert "cold" in agent_trial._store_line(cold)
    assert agent_trial._store_line(none) == "- model store: no model ran"
