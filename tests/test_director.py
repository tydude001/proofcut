"""`proofcut.director`, the agent panel's route to an OpenRouter model.

docs/plans/OPENROUTER.md § Tests. The director runs as the real subprocess the
panel spawns, against the real `proofcut mcp`, and the model is a fake
OpenAI-compatible server in this process that replays a script and records
every request. Nothing here reaches the network, and no key is needed.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from proofcut import director, ops, webui

MODEL = "vendor/model-a"
PANEL_FLAGS = [
    "--tools", webui._AGENT_TOOLS,
    "--allowedTools", webui._AGENT_ALLOWED_TOOLS,
    "--disallowedTools", *webui._AGENT_DISALLOWED_TOOLS,
    "--permission-mode", "manual",
]
#: A 1x1 PNG, for the one tool here that returns a picture.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeModel:
    """An OpenAI-compatible `/chat/completions` that replays `script` in order.

    A script entry is a response body; `("status", code, body)` answers with
    that HTTP error; `"hold"` blocks until `release` is set, which is how a
    test puts Stop in the middle of a model call.
    """

    def __init__(self, models: list[dict[str, Any]] | None = None) -> None:
        self.script: list[Any] = []
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.release = threading.Event()
        self.holding = threading.Event()
        self.models = models if models is not None else [
            {"id": MODEL, "architecture": {"input_modalities": ["text", "image"]},
             "supported_parameters": ["tools", "tool_choice"]},
        ]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._send(200, {"data": fake.models})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                fake.headers.append(dict(self.headers))
                step = fake.script.pop(0) if fake.script else reply("(script ran out)")
                if step == "hold":
                    fake.holding.set()
                    fake.release.wait(30)
                    step = reply("too late")
                if isinstance(step, tuple):
                    self._send(step[1], step[2])
                else:
                    self._send(200, step)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.release.set()
        self.httpd.shutdown()


def reply(text: str = "", calls: list[tuple[str, dict[str, Any]]] | None = None, cost: float = 0.01) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": f"call_{i}_{name}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(calls)
        ]
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": cost,
                  "prompt_tokens_details": {"cached_tokens": 40}},
    }


@pytest.fixture
def fake() -> Iterator[FakeModel]:
    model = FakeModel()
    yield model
    model.close()


def _config(tmp_path: Path, args: list[str] | None = None, command: str | None = None) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"proofcut": {
        "command": command or sys.executable, "args": args if args is not None else ["-m", "proofcut.cli", "mcp"],
    }}}), encoding="utf-8")
    return path


def _env(fake: FakeModel) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in (director.KEY_ENV, director.OTHER_KEY_ENV)}
    env[director.URL_ENV] = fake.url
    return env


def _run(fake: FakeModel, config: Path, *extra: str, brief: str = "do it") -> tuple[int, list[dict[str, Any]]]:
    done = subprocess.run(
        [sys.executable, "-m", "proofcut.director", "-p", brief, "--verbose", "--output-format", "stream-json",
         "--mcp-config", str(config), "--strict-mcp-config", *PANEL_FLAGS, *extra],
        check=False, capture_output=True, text=True, env=_env(fake), timeout=120, stdin=subprocess.DEVNULL,
    )
    return done.returncode, [json.loads(line) for line in done.stdout.splitlines() if line.strip()]


def test_a_brief_on_the_argv_loads_a_tool_calls_it_and_reports_the_cost(fake: FakeModel, tmp_path: Path) -> None:
    fake.script = [
        reply(calls=[("ToolSearch", {"query": "select:ping"})]),
        reply(calls=[("ping", {})]),
        reply("It is alive."),
    ]
    code, events = _run(fake, _config(tmp_path), "--model", MODEL)
    assert code == 0, events
    init = events[0]
    assert init["subtype"] == "init" and init["model"] == MODEL
    assert init["mcp_servers"] == [{"name": "proofcut", "status": "connected"}]
    assert init["director"]["vision"] is True
    uses = [b for e in events if e["type"] == "assistant" for b in e["message"]["content"] if b["type"] == "tool_use"]
    assert [u["name"] for u in uses] == ["ToolSearch", "mcp__proofcut__ping"]
    results = [b for e in events if e["type"] == "user" for b in e["message"]["content"]]
    assert json.loads(results[1]["content"][0]["text"])["status"] == "ok"
    result = events[-1]
    assert result["type"] == "result" and result["subtype"] == "success"
    assert result["result"] == "It is alive."
    assert result["num_turns"] == 3
    assert result["total_cost_usd"] == pytest.approx(0.03)
    assert result["usage"]["cache_read_input_tokens"] == 120

    # Deferred loading: turn 1 offers ToolSearch alone, turn 2 has ping's definition too.
    assert [t["function"]["name"] for t in fake.requests[0]["tools"]] == ["ToolSearch"]
    assert [t["function"]["name"] for t in fake.requests[1]["tools"]] == ["ToolSearch", "ping"]
    assert "ping" in fake.requests[0]["messages"][0]["content"], "the system prompt names every deferred tool"
    # Not OpenRouter, so nothing OpenRouter-only went out, and no key did either.
    assert "provider" not in fake.requests[0]
    assert "Authorization" not in fake.headers[0]


def test_turns_on_stdin_are_one_conversation_and_stop_ends_only_the_running_turn(
    fake: FakeModel, tmp_path: Path
) -> None:
    """The panel's protocol: a live process, a `result` per prompt, and Stop."""
    fake.script = [reply("first answer"), "hold", reply("third answer")]
    proc = subprocess.Popen(
        [sys.executable, "-m", "proofcut.director", "-p", "--verbose", "--input-format", "stream-json",
         "--output-format", "stream-json", "--mcp-config", str(_config(tmp_path)), "--strict-mcp-config",
         *PANEL_FLAGS, "--model", MODEL],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env(fake),
    )
    lines: queue.Queue[dict[str, Any]] = queue.Queue()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip():
                lines.put(json.loads(line))

    threading.Thread(target=pump, daemon=True).start()

    def send(payload: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def until(kind: str) -> dict[str, Any]:
        while True:
            event = lines.get(timeout=60)
            if event["type"] == kind:
                return event

    def prompt(text: str) -> None:
        send({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    try:
        prompt("one")
        assert until("result")["result"] == "first answer"
        prompt("two")
        assert fake.holding.wait(30)
        send({"type": "control_request", "request_id": "r1", "request": {"subtype": "interrupt"}})
        assert until("control_response")["response"]["request_id"] == "r1"
        stopped = until("result")
        assert stopped["subtype"] == "error_during_execution"
        assert stopped["result"] == "stopped by the user"
        fake.release.set()
        prompt("three")
        assert until("result")["result"] == "third answer"
        sent = [m["content"] for m in fake.requests[-1]["messages"] if m["role"] == "user"]
        assert sent == ["one", "two", "three"], "one conversation across prompts"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_wider_allowlist_is_refused_before_anything_starts(fake: FakeModel, tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "proofcut.director", "-p", "x", "--mcp-config", str(_config(tmp_path)),
         "--allowedTools", "mcp__proofcut__*,Bash", "--model", MODEL],
        check=False, capture_output=True, text=True, env=_env(fake), timeout=60, stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 1
    result = json.loads(done.stdout.splitlines()[-1])
    assert result["type"] == "result" and "allowedTools" in result["result"]
    assert fake.requests == []


def test_openrouter_with_no_key_says_where_to_get_one(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in (director.KEY_ENV, director.URL_ENV)}
    done = subprocess.run(
        [sys.executable, "-m", "proofcut.director", "-p", "x", "--mcp-config", str(_config(tmp_path)),
         *PANEL_FLAGS, "--model", MODEL],
        check=False, capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 1
    result = json.loads(done.stdout.splitlines()[-1])
    assert director.KEY_ENV in result["result"] and "openrouter.ai/keys" in result["result"]


def test_no_credit_is_named_as_no_credit(fake: FakeModel, tmp_path: Path) -> None:
    fake.script = [("status", 402, {"error": {"code": 402, "message": "Insufficient credits"}})]
    code, events = _run(fake, _config(tmp_path), "--model", MODEL)
    assert code == 1
    assert events[-1]["subtype"] == "error_during_execution"
    assert "out of credit" in events[-1]["result"] and "Insufficient credits" in events[-1]["result"]


def test_a_model_without_tools_or_not_listed_is_refused_before_any_tokens(tmp_path: Path) -> None:
    fake = FakeModel(models=[{"id": MODEL, "architecture": {"input_modalities": ["text"]},
                              "supported_parameters": ["max_tokens"]}])
    try:
        code, events = _run(fake, _config(tmp_path), "--model", MODEL)
        assert code == 1 and "does not take tools" in events[-1]["result"]
        code, events = _run(fake, _config(tmp_path), "--model", "vendor/nope")
        assert code == 1 and "no model named 'vendor/nope'" in events[-1]["result"]
        assert fake.requests == []
    finally:
        fake.close()


def test_the_budget_stops_the_turn_before_the_next_call(fake: FakeModel, tmp_path: Path) -> None:
    fake.script = [reply(calls=[("ToolSearch", {"query": "select:ping"})], cost=0.5), reply("never sent")]
    code, events = _run(fake, _config(tmp_path), "--model", MODEL, "--max-budget-usd", "0.4")
    assert code == 1
    assert events[-1]["subtype"] == "error_max_budget_usd"
    assert len(fake.requests) == 1


def _image_server(tmp_path: Path) -> Path:
    """A one-tool MCP server whose tool returns a picture, as the sheet tools do."""
    script = tmp_path / "image_server.py"
    script.write_text(
        "from mcp.server import MCPServer\n"
        "from mcp.server.mcpserver.utilities.types import Image\n"
        "mcp = MCPServer('proofcut')\n"
        "@mcp.tool()\n"
        "def shot_sheet():\n"
        "    '''A picture.'''\n"
        f"    return ['the sheet', Image(data={PNG!r}, format='png')]\n"
        "mcp.run('stdio')\n",
        encoding="utf-8",
    )
    return _config(tmp_path, args=[str(script)])


def test_a_picture_reaches_a_model_that_sees_and_the_pane_either_way(fake: FakeModel, tmp_path: Path) -> None:
    config = _image_server(tmp_path)
    fake.script = [reply(calls=[("ToolSearch", {"query": "select:shot_sheet"})]),
                   reply(calls=[("shot_sheet", {})]), reply("looked")]
    code, events = _run(fake, config, "--model", MODEL)
    assert code == 0, events
    tool_result = [b for e in events if e["type"] == "user" for b in e["message"]["content"]][-1]
    image = next(p for p in tool_result["content"] if p["type"] == "image")
    assert base64.b64decode(image["source"]["data"]) == PNG, "the pane draws the real bytes"
    last = fake.requests[-1]["messages"]
    assert last[-2]["role"] == "tool" and "next message" in last[-2]["content"]
    assert last[-1]["role"] == "user"
    assert last[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")

    blind = FakeModel(models=[{"id": MODEL, "architecture": {"input_modalities": ["text"]},
                               "supported_parameters": ["tools"]}])
    try:
        blind.script = [reply(calls=[("ToolSearch", {"query": "select:shot_sheet"})]),
                        reply(calls=[("shot_sheet", {})]), reply("did not look")]
        code, events = _run(blind, config, "--model", MODEL)
        assert code == 0 and events[0]["director"]["vision"] is False
        last = blind.requests[-1]["messages"]
        assert last[-1]["role"] == "tool" and "cannot see pictures" in last[-1]["content"]
        assert "cannot see pictures" in blind.requests[0]["messages"][0]["content"]
    finally:
        blind.close()


# -- in process: what only an OpenRouter URL turns on ----------------------


def test_an_openrouter_key_never_goes_to_another_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(director.KEY_ENV, "sk-or-secret")
    monkeypatch.delenv(director.OTHER_KEY_ENV, raising=False)
    assert director.api_key(director.OPENROUTER_URL) == "sk-or-secret"
    assert director.api_key("http://127.0.0.1:8083/v1") is None


def test_openrouter_requests_route_to_tool_providers_and_cache_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(director.URL_ENV, raising=False)
    monkeypatch.setenv(director.KEY_ENV, "sk-or-secret")
    sent: list[tuple[str, str | None, dict[str, Any]]] = []
    monkeypatch.setattr(director, "_request", lambda url, key, body=None: sent.append((url, key, body)) or reply("ok"))
    args = director.parse_args(["-p", "x", "--mcp-config", "c", "--model", "anthropic/claude-opus-5"])
    d = director.Director(args, session=None, catalogue={}, instructions=None, info=None)  # type: ignore[arg-type]
    d.chat()
    url, key, body = sent[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions" and key == "sk-or-secret"
    assert body["provider"] == {"require_parameters": True}
    assert body["cache_control"] == {"type": "ephemeral"}
    d.model = "openai/gpt-5.5"
    d.chat()
    assert "cache_control" not in sent[1][2], "OpenAI caches on its own"


def test_reasoning_details_go_back_with_the_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini's tool calls fail on the next request without them."""
    import asyncio

    args = director.parse_args(["-p", "x", "--mcp-config", "c", "--model", MODEL])
    d = director.Director(args, session=None, catalogue={}, instructions=None, info=None)  # type: ignore[arg-type]
    response = reply(calls=[("ToolSearch", {"query": "select:nothing"})])
    response["choices"][0]["message"]["reasoning_details"] = [{"type": "reasoning.encrypted", "data": "x"}]
    monkeypatch.setattr(director, "emit", lambda event: None)
    asyncio.run(d._step(response))
    assert d.messages[1]["reasoning_details"] == [{"type": "reasoning.encrypted", "data": "x"}]


def test_http_failures_say_what_to_do() -> None:
    assert "check OPENROUTER_API_KEY" in director.explain_http(401, '{"error": {"message": "No auth"}}')
    assert "No auth" in director.explain_http(401, '{"error": {"message": "No auth"}}')
    assert "provider failed" in director.explain_http(502, "bad gateway")


# -- the panel's route -----------------------------------------------------


def test_only_a_slash_in_the_model_id_leaves_claude() -> None:
    """Claude Code stays the default: no model, and every Claude id, spawn `claude`."""
    for model in (None, "", "claude-opus-5", "claude-haiku-4-5-20251001"):
        assert not webui.uses_director(model)
        assert webui._agent_command(model) == [webui._agent_bin()]
    assert webui._agent_command("openai/gpt-5.5") == [sys.executable, "-m", "proofcut.director"]


def test_the_panel_runs_an_openrouter_model_end_to_end(
    fake: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AgentSession` spawns the director for a `/` model, bound to its project."""
    root = tmp_path / "proj"
    ops.init(root)
    for name, value in _env(fake).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(director.KEY_ENV, raising=False)
    fake.script = [reply(calls=[("ToolSearch", {"query": "select:ping"})]),
                   reply(calls=[("ping", {})]), reply("bound")]
    bus = webui.EventBus()
    events = bus.subscribe()
    session = webui.AgentSession(root, bus)
    try:
        session.send("which project?", model=MODEL)
        seen: list[dict[str, Any]] = []
        deadline = time.time() + 90
        while time.time() < deadline:
            _, data = events.get(timeout=90)
            seen.append(data)
            if data.get("type") == "result":
                break
        assert seen[-1]["subtype"] == "success", seen[-1]
        results = [b for e in seen if e["type"] == "user" for b in e["message"]["content"]]
        pong = json.loads(results[-1]["content"][0]["text"])
        assert Path(pong["project"]).resolve() == root.resolve()
        assert pong["bound_by"] == "-C"
    finally:
        session.close()
