"""The agent panel's director on any OpenRouter model: a stand-in for `claude -p`.

docs/plans/OPENROUTER.md. `webui._agent_argv` spawns this instead of `claude`
when the chosen model id has a `/` in it (`openai/gpt-5.5`); everything else,
the default included, still spawns `claude`. It takes the argv the panel and
`scripts/agent_trial.py` hand `claude`, starts the one MCP server `--mcp-config`
names, runs the tool-calling loop against an OpenAI-compatible endpoint, and
prints Claude Code's stream-json, so `agent.js` and the trial's `analyse()` read
it unchanged. It grew out of the local-director spike (docs/plans/LOCAL.md §
The run, 2026-09-19).

Two ways in, as `claude -p` has them:

- **A brief on the argv** (`-p <text>`, the trial): one turn, then exit.
- **Turns on stdin** (`--input-format stream-json`, the panel): each
  `{"type": "user", ...}` line is a turn in one conversation, answered with its
  own `result`; a `control_request` of subtype `interrupt` ends the running turn.

Its only capabilities are that MCP server and its own `ToolSearch`. It has no
shell, file or web tool, so the panel's permission flags have nothing to gate
here; `--allowedTools` is checked instead, so a wider allowlist cannot mean one
thing to `claude` and another here (PLAN.md § The agent panel, in mechanism).

Configuration is environment, because the trial passes its own argv untouched:

    OPENROUTER_API_KEY     the key, sent to openrouter.ai and nowhere else
    PROOFCUT_DIRECTOR_URL  another OpenAI-compatible base URL; untested
    PROOFCUT_DIRECTOR_KEY  the key for that URL, if it wants one
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters, stdio_client, types

OPENROUTER_URL = "https://openrouter.ai/api/v1"
URL_ENV = "PROOFCUT_DIRECTOR_URL"
KEY_ENV = "OPENROUTER_API_KEY"
OTHER_KEY_ENV = "PROOFCUT_DIRECTOR_KEY"
#: What `webui._AGENT_ALLOWED_TOOLS` says today. Checked, never widened here.
ALLOWED_TOOLS = "mcp__proofcut__*"
PREFIX = "mcp__proofcut__"
#: Model calls in one turn before it stops. Claude's longest scored run was 77.
MAX_TURNS = 150
#: A tool reply past this many characters is cut, with a note, the way Claude
#: Code cuts one past 25K tokens.
REPLY_CAP = 100_000
REQUEST_TIMEOUT = 600
MAX_TOKENS = 16_000

TOOL_SEARCH = {
    "type": "function",
    "function": {
        "name": "ToolSearch",
        "description": (
            "Load the full definitions of deferred tools so they can be called. A tool "
            "listed as deferred cannot be called until it has been loaded here. Query "
            "'select:name1,name2' loads exactly those tools; any other query is keywords, "
            "best matches first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "description": "keyword queries only; default 5"},
            },
            "required": ["query"],
        },
    },
}


class ModelError(Exception):
    """A model request that failed for a reason a person can act on."""


class Interrupted(Exception):
    """The panel's Stop arrived while this turn was running."""


def base_url() -> str:
    return (os.environ.get(URL_ENV) or OPENROUTER_URL).rstrip("/")


def is_openrouter(url: str) -> bool:
    return url.startswith("https://openrouter.ai/")


def api_key(url: str) -> str | None:
    """The key for `url`, and only its own: an OpenRouter key never goes to another host."""
    return os.environ.get(KEY_ENV if is_openrouter(url) else OTHER_KEY_ENV) or None


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


def note(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """`claude -p`'s argv, as far as the panel and the trial use it."""
    p = argparse.ArgumentParser(prog="proofcut.director", add_help=False)
    p.add_argument("prompt", nargs="?")
    p.add_argument("-p", "--print", action="store_true")
    p.add_argument("--mcp-config", required=True)
    p.add_argument("--model")
    p.add_argument("--max-budget-usd", type=float)
    p.add_argument("--output-format")
    p.add_argument("--input-format")
    p.add_argument("--tools")
    p.add_argument("--allowedTools", "--allowed-tools")
    p.add_argument("--disallowedTools", "--disallowed-tools", nargs="+")
    p.add_argument("--permission-mode")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--strict-mcp-config", action="store_true")
    p.add_argument("--no-session-persistence", action="store_true")
    args, unknown = p.parse_known_args(argv)
    if unknown:
        note(f"[director] ignoring unrecognised argv: {unknown}")
    return args


# -- the model endpoint ----------------------------------------------------


def _request(url: str, key: str | None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if is_openrouter(url):
        headers["HTTP-Referer"] = "https://github.com/tydude001/proofcut"
        headers["X-OpenRouter-Title"] = "proofcut"
        headers["X-Title"] = "proofcut"
    request = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        raise ModelError(explain_http(err.code, err.read().decode("utf-8", "replace"))) from err
    except urllib.error.URLError as err:
        raise ModelError(f"could not reach {url}: {err.reason}") from err


def explain_http(code: int, body: str) -> str:
    """An HTTP failure as the thing to do about it, with the server's own words after."""
    said = body.strip()
    try:
        said = json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        pass
    said = str(said)[:600]
    why = {
        401: f"the key was refused; check {KEY_ENV}",
        402: "the account is out of credit",
        403: "the request was refused (a moderation flag or a key limit)",
        404: "no such model, or no provider serves it with tools",
        408: "the model timed out",
        429: "rate limited; wait and send again",
    }.get(code)
    if why is None and code >= 500:
        why = "the model's provider failed"
    return f"{code}: {why}. The server said: {said}" if why else f"{code}: {said}"


def model_info(url: str, key: str | None, model: str) -> dict[str, Any] | None:
    """The model's listing, or None when the endpoint does not list it richly.

    OpenRouter's `/models` carries `architecture.input_modalities` and
    `supported_parameters`; a local server's carries ids only. A model absent
    from a rich listing is refused before any tokens are spent.
    """
    try:
        listing = _request(f"{url}/models", key)
    except ModelError as err:
        note(f"[director] could not list models: {err}")
        return None
    entries = listing.get("data") or []
    rich = any("architecture" in e for e in entries)
    for entry in entries:
        if entry.get("id") == model:
            return entry if rich else None
    if rich:
        raise ModelError(f"no model named {model!r} on {url}; ids look like openai/gpt-5.5")
    return None


def sees_images(info: dict[str, Any] | None) -> bool:
    return bool(info) and "image" in ((info or {}).get("architecture") or {}).get("input_modalities", [])


def cache_control(model: str) -> dict[str, Any] | None:
    """Anthropic models cache only when asked; the others OpenRouter lists cache on their own."""
    return {"type": "ephemeral"} if model.startswith("anthropic/") else None


# -- tools -----------------------------------------------------------------


def openai_tool(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema or {"type": "object", "properties": {}},
        },
    }


def system_prompt(instructions: str | None, names: list[str], vision: bool) -> str:
    parts = [
        (
            "You are an agent editing a video project through proofcut's tools. Work "
            "until the request is met, then reply with a short plain summary of what you "
            "did. Call tools rather than describing what you would do. Report only what "
            "the tools' replies show; never claim work you did not do."
        ),
    ]
    if instructions:
        parts.append(instructions.strip())
    if not vision:
        parts.append(
            "You cannot see pictures. A tool that returns one says so in its reply; "
            "judge framing and b-roll from the tools' text instead."
        )
    parts.append(
        "These tools exist but their definitions are not loaded, so none can be "
        "called yet. Call ToolSearch with query \"select:name1,name2\" to load the "
        "ones you need, or with keywords to search. Deferred tools: " + ", ".join(names)
    )
    return "\n\n".join(parts)


def search_tools(query: str, limit: int, catalogue: dict[str, Any]) -> list[str]:
    query = query.strip()
    if query.startswith("select:"):
        wanted = [n.strip().removeprefix(PREFIX) for n in query[len("select:"):].split(",")]
        return [n for n in wanted if n in catalogue]
    terms = [t for t in query.lower().replace(",", " ").split() if t]
    scored: list[tuple[int, str]] = []
    for name, tool in catalogue.items():
        hay_desc = (tool["function"]["description"] or "").lower()
        score = sum(3 for t in terms if t in name.lower()) + sum(1 for t in terms if t in hay_desc)
        if score:
            scored.append((-score, name))
    return [n for _, n in sorted(scored)[:limit]]


def flatten(result: Any, vision: bool) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """(text for the model, parts for the event stream, image parts for the model)."""
    texts: list[str] = []
    parts: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    for block in result.content:
        kind = getattr(block, "type", None)
        if kind == "text":
            texts.append(block.text)
            parts.append({"type": "text", "text": block.text})
        elif kind == "image":
            mime = getattr(block, "mime_type", None) or getattr(block, "mimeType", None) or "image/png"
            data = getattr(block, "data", "") or ""
            parts.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
            if vision:
                images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
                texts.append("[a picture was returned; it follows in the next message]")
            else:
                texts.append("[a picture was returned here; this model cannot see pictures]")
    text = "\n".join(texts)
    if len(text) > REPLY_CAP:
        text = text[:REPLY_CAP] + f"\n[reply cut at {REPLY_CAP} characters of {len(text)}]"
    return text, parts, images


def user_text(message: dict[str, Any]) -> str:
    content = (message.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


# -- the session -----------------------------------------------------------


class Director:
    def __init__(self, args: argparse.Namespace, session: ClientSession, catalogue: dict[str, Any],
                 instructions: str | None, info: dict[str, Any] | None) -> None:
        self.args = args
        self.session = session
        self.catalogue = catalogue
        self.url = base_url()
        self.key = api_key(self.url)
        self.model: str = args.model
        self.vision = sees_images(info)
        self.loaded: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(instructions, list(catalogue), self.vision)},
        ]
        self.session_id = str(uuid.uuid4())
        self.cost = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.interrupt = asyncio.Event()

    def chat(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "tools": [TOOL_SEARCH, *self.loaded.values()],
            "tool_choice": "auto",
            "max_tokens": MAX_TOKENS,
        }
        if is_openrouter(self.url):
            # Route only to providers that take every parameter sent, `tools` included.
            body["provider"] = {"require_parameters": True}
            if (cache := cache_control(self.model)) is not None:
                body["cache_control"] = cache
        response = _request(f"{self.url}/chat/completions", self.key, body)
        if "error" in response and not response.get("choices"):
            error = response["error"]
            raise ModelError(explain_http(int(error.get("code") or 0), json.dumps({"error": error})))
        return response

    async def _until_interrupted(self, awaitable: Any) -> Any:
        task = asyncio.ensure_future(awaitable)
        stop = asyncio.ensure_future(self.interrupt.wait())
        done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            stop.cancel()
            return task.result()
        task.cancel()
        raise Interrupted

    async def turn(self, prompt: str) -> bool:
        """One user turn, to its `result`. True when it succeeded."""
        started = time.time()
        self.interrupt.clear()
        self.messages.append({"role": "user", "content": prompt})
        calls_made = 0
        subtype, final = "success", ""
        try:
            while True:
                if calls_made >= MAX_TURNS:
                    subtype, final = "error_max_turns", f"stopped after {MAX_TURNS} model calls"
                    break
                budget = self.args.max_budget_usd
                if budget is not None and self.cost >= budget:
                    subtype, final = "error_max_budget_usd", f"spent ${self.cost:.4f} of a ${budget} cap"
                    break
                response = await self._until_interrupted(asyncio.to_thread(self.chat))
                calls_made += 1
                if await self._step(response):
                    final = self._last_text
                    if self._finish == "length":
                        subtype = "error_max_tokens"
                    break
        except Interrupted:
            subtype, final = "error_during_execution", "stopped by the user"
        except ModelError as err:
            subtype, final = "error_during_execution", str(err)
        emit({
            "type": "result", "subtype": subtype, "is_error": subtype != "success",
            "num_turns": calls_made, "duration_ms": int((time.time() - started) * 1000),
            "total_cost_usd": round(self.cost, 6), "result": final, "session_id": self.session_id,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                      "cache_read_input_tokens": self.cached_tokens},
        })
        return subtype == "success"

    async def _step(self, response: dict[str, Any]) -> bool:
        """Emit one model reply and run its tool calls. True when the turn is over."""
        choice = response["choices"][0]
        message = choice.get("message") or {}
        usage = response.get("usage") or {}
        self.cost += float(usage.get("cost") or 0.0)
        self.input_tokens += int(usage.get("prompt_tokens") or 0)
        self.output_tokens += int(usage.get("completion_tokens") or 0)
        self.cached_tokens += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        self._finish = choice.get("finish_reason")
        text = message.get("content") or ""
        calls = message.get("tool_calls") or []
        self._last_text = text

        blocks: list[dict[str, Any]] = []
        if text.strip():
            blocks.append({"type": "text", "text": text})
        for call in calls:
            raw = (call.get("function") or {}).get("arguments")
            try:
                arguments = raw if isinstance(raw, dict) else json.loads(raw or "{}")
                parse_error = None if isinstance(arguments, dict) else "the arguments were not an object"
            except json.JSONDecodeError as err:
                arguments, parse_error = {}, f"the arguments were not valid JSON ({err})"
            call["_arguments"], call["_parse_error"] = arguments if isinstance(arguments, dict) else {}, parse_error
            name = call["function"]["name"]
            blocks.append({
                "type": "tool_use", "id": call["id"],
                "name": name if name == "ToolSearch" else PREFIX + name.removeprefix(PREFIX),
                "input": call["_arguments"],
            })
        emit({
            "type": "assistant", "session_id": self.session_id,
            "message": {"role": "assistant", "model": self.model, "content": blocks,
                        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                                  "output_tokens": usage.get("completion_tokens", 0)}},
        })

        kept: dict[str, Any] = {"role": "assistant", "content": text}
        # Gemini's and the reasoning models' tool calls fail on the next request
        # unless their reasoning comes back with them; OpenRouter carries it here.
        if message.get("reasoning_details"):
            kept["reasoning_details"] = message["reasoning_details"]
        if calls:
            kept["tool_calls"] = [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["function"]["name"], "arguments": json.dumps(c["_arguments"])}}
                for c in calls
            ]
        self.messages.append(kept)
        if not calls:
            return True

        results: list[dict[str, Any]] = []
        pictures: list[dict[str, Any]] = []
        interrupted = False
        for call in calls:
            if interrupted:
                body, parts, err = "Error: stopped by the user before this call ran.", None, True
            else:
                try:
                    body, parts, err, images = await self._call(call)
                    pictures += [{"type": "text", "text": f"The picture {call['function']['name']} returned:"},
                                 *images] if images else []
                except Interrupted:
                    interrupted = True
                    body, parts, err = "Error: stopped by the user while this call ran.", None, True
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": body})
            results.append({
                "type": "tool_result", "tool_use_id": call["id"], "is_error": err,
                "content": parts if parts is not None else [{"type": "text", "text": body}],
            })
        if pictures:
            self.messages.append({"role": "user", "content": pictures})
        emit({"type": "user", "session_id": self.session_id, "message": {"role": "user", "content": results}})
        if interrupted:
            raise Interrupted
        return False

    async def _call(self, call: dict[str, Any]) -> tuple[str, list[dict[str, Any]] | None, bool, list[dict[str, Any]]]:
        name = call["function"]["name"].removeprefix(PREFIX)
        if call["_parse_error"]:
            return f"Error: {call['_parse_error']}", None, True, []
        if name == "ToolSearch":
            query = str(call["_arguments"].get("query", ""))
            try:
                limit = int(call["_arguments"].get("max_results") or 5)
            except (TypeError, ValueError):
                limit = 5
            found = search_tools(query, limit, self.catalogue)
            for n in found:
                self.loaded[n] = self.catalogue[n]
            if found:
                return f"Loaded {len(found)} tool(s), now callable: {', '.join(found)}", None, False, []
            return f"No deferred tool matched {query!r}.", None, True, []
        if name not in self.catalogue:
            return f"Error: no tool named {name!r}.", None, True, []
        if name not in self.loaded:
            deferred = (
                f"Error: {name} is deferred and has not been loaded. "
                f"Call ToolSearch with query \"select:{name}\" first."
            )
            return deferred, None, True, []
        try:
            outcome = await self._until_interrupted(self.session.call_tool(name, call["_arguments"]))
        except Interrupted:
            raise
        except Exception as exc:  # noqa: BLE001 — a tool that raises is a tool_result
            return f"Error: {exc}", None, True, []
        body, parts, images = flatten(outcome, self.vision)
        return body, parts, bool(outcome.is_error), images


def refusal(args: argparse.Namespace, url: str) -> str | None:
    """Why this director will not start, or None."""
    if args.allowedTools != ALLOWED_TOOLS:
        return (f"--allowedTools is {args.allowedTools!r}; this director gates nothing but "
                f"{ALLOWED_TOOLS!r}, so it refuses anything else rather than widen silently")
    if not args.model:
        return "no --model; pick one with a / in it, such as openai/gpt-5.5"
    if is_openrouter(url) and not api_key(url):
        return f"{KEY_ENV} is not set; make a key at https://openrouter.ai/keys and export it"
    return None


def _stdin_reader(loop: asyncio.AbstractEventLoop, inbox: asyncio.Queue[str | None],
                  director: list[Director]) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("type") == "user":
            loop.call_soon_threadsafe(inbox.put_nowait, user_text(message))
        elif message.get("type") == "control_request":
            request = message.get("request") or {}
            if request.get("subtype") == "interrupt" and director:
                loop.call_soon_threadsafe(director[0].interrupt.set)
            emit({"type": "control_response", "response": {
                "subtype": "success", "request_id": message.get("request_id")}})
    loop.call_soon_threadsafe(inbox.put_nowait, None)


async def run(args: argparse.Namespace) -> int:
    url = base_url()
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[str | None] = asyncio.Queue()
    holder: list[Director] = []
    if args.prompt is not None:
        inbox.put_nowait(args.prompt)
        inbox.put_nowait(None)
    else:
        threading.Thread(target=_stdin_reader, args=(loop, inbox, holder), daemon=True).start()

    def fail(why: str) -> int:
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "num_turns": 0, "total_cost_usd": 0.0, "result": why})
        return 1

    if (why := refusal(args, url)) is not None:
        return fail(why)
    try:
        info = await asyncio.to_thread(model_info, url, api_key(url), args.model)
    except ModelError as err:
        return fail(str(err))
    if info is not None and "tools" not in (info.get("supported_parameters") or []):
        return fail(f"{args.model} does not take tools, and every proofcut edit is a tool call")

    config = json.loads(await asyncio.to_thread(Path(args.mcp_config).read_text, encoding="utf-8"))
    servers = config.get("mcpServers") or {}
    if len(servers) != 1:
        return fail(f"--mcp-config names {len(servers)} servers; the director drives exactly one")
    name, spec = next(iter(servers.items()))
    server = StdioServerParameters(
        command=spec["command"], args=spec.get("args", []), env={**os.environ, **spec.get("env", {})},
    )
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        handshake = await session.initialize()
        listed: list[Any] = []
        cursor = None
        while True:
            page = (await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
                    if cursor else await session.list_tools())
            listed += page.tools
            cursor = page.next_cursor
            if not cursor:
                break
        catalogue = {t.name: openai_tool(t) for t in listed}
        director = Director(args, session, catalogue, handshake.instructions, info)
        holder.append(director)
        emit({
            "type": "system", "subtype": "init", "session_id": director.session_id, "model": args.model,
            "mcp_servers": [{"name": name, "status": "connected"}],
            "tools": ["ToolSearch", *[PREFIX + n for n in catalogue]],
            "director": {"url": url, "vision": director.vision},
        })
        ok = True
        while (prompt := await inbox.get()) is not None:
            ok = await director.turn(prompt)
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    sys.exit(main())
