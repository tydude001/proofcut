#!/usr/bin/env python3
"""Hand an agent a brief and watch it edit a video end to end — NEXT.md § 1.

Everything proofcut has measured so far has been a *piece*: a tool, a sheet, a
check. The product's central claim — that an agent can cut a video through
word-addressed tools and pictures in tool results — has never been measured as
a whole. This is the instrument that measures it, and it is a script rather
than a test because the thing under measurement costs real money and minutes
and its result is evidence, not a pass/fail gate.

It runs the same client the agent panel runs, with the same confinement:
`claude -p` against a generated one-server MCP config, `--tools ToolSearch` so
the built-in set is gone but for tool search, and proofcut's tools are the
agent's *only* reach (CLAUDE.md — the allow/disallow flags do not gate
built-ins, `--tools` does; docs/plans/MCP.md § Step 1), and the
command in that config is this interpreter with `-m proofcut.cli`, never the name
`proofcut`, which is absent from PATH for every launch that skips an activated
venv. Those facts are imported from `webui.py` rather than restated, so
the trial cannot silently measure a different client than the one that ships.

    python scripts/agent_trial.py ~/proofcut-work/spikes/agent-trial              # prepare, run, score
    python scripts/agent_trial.py ~/proofcut-work/spikes/agent-trial-control --control
    python scripts/agent_trial.py ~/proofcut-work/spikes/agent-trial --prepare-only
    python scripts/agent_trial.py ~/proofcut-work/spikes/agent-trial --score-only <run dir>
    python scripts/agent_trial.py ~/proofcut-work/spikes/agent-trial-film --film

    python scripts/agent_trial.py ~/proofcut-work/spikes/agent-trial-real \
        --source ~/proofcut-work/spikes/agent-trial-real/media \
        --brief-file brief.txt --phrases phrases.json

`--control` meets the same brief by script and is scored by the identical
checks. Run it in its **own** work directory — it edits the project it is
pointed at, and pointing both modes at one directory means each run wipes the
other's evidence.

**`--source` swaps the material, never the instrument.** The generated demo
footage depicts nothing, which is why the first trial's agent hung picture
structurally rather than by subject (TRIAL.md § What this trial does not
settle) — and choosing footage by what it shows is the thing this repo has
measured hardest (HISTORY.md § Choosing the b-roll). So a run over real
footage reads its media from `--source` instead of generating it, and
everything else is unchanged: the same client, the same confinement, the same
`score()`. It demands `--brief-file`, because the built-in brief names three
demo files and a fluffed take that a real folder does not have, and it refuses
a directory that is itself a proofcut project — the trial's agent must never be
pointed at real authored state, only at real *material*. Point it at a copy.

**`--film` asks for a whole film, not a cut** — docs/plans/SHOWCASE.md § Step 4.
The first two runs asked for cuts, b-roll, captions and a check, so "an agent
makes the whole film" was never measured. The film brief adds a score, an end
card and a master to the same material, and `score()` adds three checks, each
read off the delivered file: the score heard at its own second
(`music_placed`), the master measured (`loudness_on_target`), and ink on screen
in the tail (`end_card_rendered`). A manifest only says what a render would
carry, so none of them trusts one.

**What the agent is given is a goal, never the steps.** A brief listing the
commands would measure this file's authorship, not the agent — so the default
brief below names the material and what a finished cut looks like, and nothing
else. `--brief-file` swaps in another one for a run on a copy of a real
project.

The project starts at `proofcut init` and nothing else: no import, no transcript,
no seeded timeline. "Import to export" is the loop being measured, and a
pre-seeded project quietly measures the back half of it.

Two rules about what this writes. It never touches a real project — the media
and the project are generated under the work directory, `make_demo.py`'s own
footage, regenerable and licence-free (docs/plans/POLISH.md § Step 02). And every run
keeps its whole event stream (`events.jsonl`), because the failure list this
produces has to be re-readable months later without re-running anything.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_demo
import trial_check

from proofcut import briefs, finish, ops, projectlock, webui
from proofcut import media as proofcut_media
from proofcut.project import LEGACY_MANIFEST_NAME, MANIFEST_NAME, TIMELINE_NAME, Project


class TrialError(RuntimeError):
    """A precondition failed, or the run could not be started."""


#: The briefs live in `proofcut.briefs`, which the MCP server's prompts compose
#: from too, so the brief a run measured and the one that ships cannot drift
#: apart (docs/plans/MCP.md § Step 7). `{media}`, `{project}` and `{output}`
#: are absolute paths, because the agent has no shell to resolve a relative
#: one with and no Read tool to look around with. `tests/test_briefs.py` holds
#: both renderings to the text the recorded runs were given.
DEMO_BRIEF = briefs.trial_cut
FILM_BRIEF = briefs.trial_film

#: What the film brief asks the master to measure, and `finish`'s own band.
FILM_LOUDNESS = briefs.FILM_LOUDNESS
FILM_LOUDNESS_TOLERANCE = finish.MASTER_LU_TOLERANCE
#: A frame is ink rather than black above this 8-bit YMAX. Black is 16, and
#: CLAUDE.md's measured floor for a real frame is 127 (`SHEET_BLANK_MAX`'s rule).
INK_YMAX = 100

#: Killed at this many seconds by default. Generous: whisper on a cold cache
#: and a melt render are both minutes, and a trial that times out mid-render
#: measures this number rather than the agent.
DEFAULT_TIMEOUT = 45 * 60

#: The retake the demo voiceover carries (`make_demo.SCRIPT`'s `retake=True`
#: line). Scoring resolves it against whatever whisper actually heard rather
#: than against a word index, because the transcript is the agent's own and
#: two whisper runs do not agree on indices.
RETAKE_PHRASE = "no, let me try that again"

#: The tail of the *good* take — the words the fluffed one never reached. It is
#: the sharpest over-cut test the demo has, because the fluff and the keeper
#: open with the same six words: a cut that reads "remove the duplicate" too
#: broadly takes the good take instead of the fluffed one and still leaves a
#: film that plays. Only this tail tells them apart.
KEEPER_PHRASE = "names a word in the transcript"

#: The two phrases above, as the scoring parameter they really are. A run over
#: real footage declares its own pair in a `--phrases` JSON file, and a run
#: that declares none gets `None` on both brief checks — *unsettled*, never a
#: pass and never a failure, because "nobody said which line was the fluff" is
#: not evidence that the fluff survived. Persisted into the run directory, so
#: `--score-only` months later scores against the pair the run was scored with
#: rather than against whatever this file says today.
DEMO_PHRASES = {"remove": RETAKE_PHRASE, "keep": KEEPER_PHRASE}

#: `no_stutter` looks this many surviving words back from the keeper's first
#: word — the fluffed take's residue sits immediately ahead of the good take —
#: and calls a repeated run a stutter only when its second copy starts within
#: `STUTTER_GAP` words of the first ending. Both are read off the two films
#: that kept one (LOCAL.md § The score cannot see a stutter): a five-word
#: restart with no gap, and a two-word fragment four words ahead of its repeat.
STUTTER_LOOKBACK = 14
STUTTER_GAP = 6

#: A run made only of these is ordinary speech ("of the … of the"), not a
#: restart. Deliberately short: a word missing here costs a false finding the
#: control run shows, and a word wrongly here hides a real one.
STUTTER_STOPWORDS = frozenset(
    ("a", "an", "the", "of", "to", "in", "on", "at", "and", "or", "but", "is", "it",
     "its", "as", "for", "with", "that", "this", "be")
)

#: How many registered clips the demo brief's three files should produce. It
#: is a parameter because a real folder is not three files and an agent is not
#: obliged to import all of it: over real material the floor is what the
#: generic checks need to mean anything — a narration clip to ask the phrase
#: checks about, and a second asset for `picture_hung` to see.
DEMO_MIN_CLIPS = 3
SOURCE_MIN_CLIPS = 2


# ---------------------------------------------------------------- preparing


def hold_lock(work: Path) -> Path:
    """Refuse to start while another run's agent is still editing this project.

    Written before anything is generated or removed, because the failure this
    prevents was measured on the first real run of this file and is silent in
    both directions. `claude` is spawned into its own session (see
    `run_agent`), so killing the harness does **not** kill the agent: the first
    run's agent went on calling tools for minutes after its parent was gone,
    the second run's `prepare` deleted the project out from under it, and the
    second agent then found three cues in a project it had just initialised and
    said so in its own report — a run that reads like an agent hallucinating a
    cue table and is in fact two agents in one project.

    A PID file, not a lock library: the question is only "is that process still
    there", the answer has to survive this process being killed, and a stale
    file after a hard kill must not wedge the next run. `--keep-project` is not
    exempt — concurrent editing is the hazard, not deletion.
    """
    lock = work / "runs" / ".lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            held = json.loads(lock.read_text(encoding="utf-8"))
            pid = int(held["pid"])
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pid = -1
            held = {}
        # The product lock's probe, never `os.kill`: on Windows that call
        # terminates the process it asks about (docs/plans/PROJECT-LOCK.md
        # § The gap). The orphaned agent this file misses — the harness's pid
        # is dead while its `claude` edits on — is `prepare`'s refusal below.
        if pid > 0 and projectlock.pid_alive(pid):
            raise TrialError(
                f"another trial is live in {work}: pid {pid}, started "
                f"{held.get('started')}, run {held.get('run_dir')}. Two agents in "
                "one project produce a report neither of them earned — wait for it, "
                f"or kill it and delete {lock}."
            )
    lock.write_text(
        json.dumps({
            "pid": os.getpid(),
            "started": datetime.now(UTC).isoformat(),
            "run_dir": None,
        }),
        encoding="utf-8",
    )
    return lock


def check_source(source: Path) -> list[Path]:
    """Refuse a `--source` that is not a folder of usable material.

    Three refusals, and the middle one is the point. An empty or missing
    directory is an obvious typo. A directory that is itself a **proofcut
    project** is not: it is the plausible mistake — the trial is described as
    running "on a copy of a real project", and the nearest reading of that is
    to hand it the project. That run would spend an agent and a render before
    reporting that the material was nowhere, and the surrounding rule is
    sharper than convenience: this harness points at real *material*, never at
    real authored state. The third is the emptiness this cannot see past —
    a filename filter, `list_media`'s own rule, since `import_media` is still
    what decides whether a file is usable.
    """
    if not source.is_dir():
        raise TrialError(f"--source is not a directory: {source}")
    # The names come from `project.py` rather than being retyped (the legacy
    # `lucid.json` too — a pre-rename project is still authored state): the
    # first draft of this guard looked for `manifest.json` and would have
    # waved every real proofcut project straight through, since the manifest is
    # `proofcut.json` and `*.manifest.json` is the snapshot suffix.
    if any((source / name).exists() for name in (MANIFEST_NAME, LEGACY_MANIFEST_NAME, TIMELINE_NAME)):
        raise TrialError(
            f"--source names a proofcut project, not a media folder: {source}. This "
            "harness starts at `proofcut init` and imports from a source directory — "
            "point it at the footage (a copy), never at authored state."
        )
    found = sorted(
        child for child in source.iterdir()
        if child.is_file() and child.suffix.lower() in proofcut_media.SOURCE_MEDIA_EXTENSIONS
    )
    if not found:
        raise TrialError(
            f"no media in --source {source} "
            f"(looked for {sorted(proofcut_media.SOURCE_MEDIA_EXTENSIONS)})"
        )
    return found


def prepare(
    work: Path, *, fresh: bool, source: Path | None = None, film: bool = False
) -> tuple[Path, Path]:
    """Generate the footage and an empty project. Returns (media dir, project).

    `fresh` removes an existing project so a re-run is not scored against a
    previous agent's work — the one way this instrument can lie about itself.
    The media is left alone when it is already there: it is deterministic and
    slow-ish to build, and nothing the agent does can modify it (import links
    media in place, and proofcut never writes to a source).

    `source` is real footage, and then nothing is generated at all: the media
    directory is somebody else's, this function neither writes into it nor
    removes anything from it, and `fresh` still governs only `work/proj`. That
    asymmetry is deliberate — the demo media is this harness's own and can be
    rebuilt from `make_demo`; real material cannot.
    """
    if source is not None:
        check_source(source)
        work.mkdir(parents=True, exist_ok=True)
        media = source
    else:
        media = work
        media.mkdir(parents=True, exist_ok=True)
        vo = media / "vo.wav"
        if not vo.exists():
            make_demo.make_voiceover(vo)
        if not all((media / name).exists() for name, _c, _l in make_demo.BROLL):
            make_demo.make_broll(media)
        if film and not (media / "music.wav").exists():
            make_demo.make_music(media / "music.wav")

    project = work / "proj"
    # An orphaned agent from a killed run still holds the project through its
    # MCP server, whose pid is alive when the harness's is not — so this, and
    # not `hold_lock`, is what catches TRIAL.md § 4. The product's own check,
    # imported rather than restated.
    held = projectlock.holder(project) if project.exists() else None
    if held is not None and not held["stale"]:
        raise TrialError(
            f"an agent session still holds {project} (pid {held['pid']}, "
            f"{held['command']}, since {held['started']}) — a previous run's agent is "
            "still editing. Kill it, or run `proofcut -C <project> unlock` if it is dead."
        )
    if project.exists() and fresh:
        shutil.rmtree(project)
    if not project.exists():
        ops.init(project)
    return media, project


# ------------------------------------------------------------------ running


def _mcp_config(project: Path, dest: Path) -> Path:
    """The generated one-server config, written where the run keeps its record.

    Shaped exactly like `webui.AgentSession._mcp_config`'s — this interpreter
    and `-m proofcut.cli`, never the name `proofcut` — but written into the run
    directory rather than `$TMPDIR`, so a run's own config is part of its
    evidence instead of being swept.
    """
    config = {
        "mcpServers": {
            "proofcut": {
                "command": sys.executable,
                "args": ["-m", "proofcut.cli", "-C", str(project), "mcp"],
            }
        }
    }
    dest.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return dest


def run_agent(
    project: Path,
    brief: str,
    run_dir: Path,
    *,
    timeout: float,
    model: str | None,
    budget_usd: float | None,
) -> dict[str, Any]:
    """Spawn one confined `claude -p`, stream its events to disk, and wait.

    The flags are the panel's, imported rather than retyped, plus three this
    context needs and an interactive panel does not: a wall-clock kill, an
    optional spend ceiling, and no session persistence (a trial is not a
    conversation anyone resumes).

    `start_new_session=True` cuts both ways, and both halves are load-bearing.
    `claude` spawns the MCP server as a child, so a timeout that kills only the
    parent leaves a proofcut MCP process holding the trial project open — the new
    session is what makes `killpg` reach all of it. The cost is that this
    subprocess no longer dies with *its* parent either: kill the harness and
    the agent goes on editing, which is what `hold_lock` exists for. Measured,
    not reasoned about — a killed run's agent ran for minutes afterwards and
    wrote cues into a project the next run had already re-initialised.
    """
    argv = [
        webui._agent_bin(),
        "-p",
        brief,
        "--verbose",
        "--output-format",
        "stream-json",
        "--mcp-config",
        str(_mcp_config(project, run_dir / "mcp-config.json")),
        "--strict-mcp-config",
        "--tools",
        webui._AGENT_TOOLS,
        "--allowedTools",
        webui._AGENT_ALLOWED_TOOLS,
        "--disallowedTools",
        *webui._AGENT_DISALLOWED_TOOLS,
        "--permission-mode",
        "manual",
        "--no-session-persistence",
    ]
    if model:
        argv += ["--model", model]
    if budget_usd is not None:
        argv += ["--max-budget-usd", str(budget_usd)]

    (run_dir / "argv.json").write_text(json.dumps(argv, indent=2), encoding="utf-8")
    events_path = run_dir / "events.jsonl"
    stderr_path = run_dir / "stderr.log"

    started = time.time()
    with stderr_path.open("w", encoding="utf-8") as stderr_file:
        proc = subprocess.Popen(
            argv,
            cwd=str(project),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            bufsize=1,
            start_new_session=True,
        )

    events: list[dict[str, Any]] = []
    undecodable = 0

    def pump() -> None:
        nonlocal undecodable
        assert proc.stdout is not None
        with events_path.open("w", encoding="utf-8") as log:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                log.write(line + "\n")
                log.flush()
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    undecodable += 1
                    continue
                events.append(payload)
                _echo(payload, started)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=30)
    reader.join(timeout=30)

    return {
        "events": events,
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "wall_seconds": round(time.time() - started, 1),
        "undecodable_lines": undecodable,
        "stderr_tail": stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:],
    }


def _echo(payload: dict[str, Any], started: float) -> None:
    """One line per event on the console, so an unattended run is watchable.

    Every line is flushed. An unattended run is watched through a redirected
    log, where Python's block buffering means a run that is going fine and one
    that has hung look identical for minutes at a time.
    """
    stamp = f"{time.time() - started:7.1f}s"
    kind = payload.get("type")
    if kind == "system" and payload.get("subtype") == "init":
        servers = ", ".join(
            f"{s.get('name')}={s.get('status')}" for s in payload.get("mcp_servers", [])
        )
        print(f"{stamp}  init      {len(payload.get('tools', []))} tools [{servers}]", flush=True)
    elif kind == "assistant":
        for block in payload.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                print(f"{stamp}  call      {block.get('name')} {_brief_args(block.get('input'))}",
                      flush=True)
            elif block.get("type") == "text" and block.get("text", "").strip():
                print(f"{stamp}  say       {block['text'].strip()[:110]}", flush=True)
    elif kind == "user":
        for block in payload.get("message", {}).get("content", []):
            if block.get("type") == "tool_result" and block.get("is_error"):
                print(f"{stamp}  ERROR     {_result_text(block)[:140]}", flush=True)
    elif kind == "result":
        print(f"{stamp}  result    {payload.get('subtype')} "
              f"turns={payload.get('num_turns')} cost=${payload.get('total_cost_usd')}", flush=True)


def _brief_args(value: Any, limit: int = 90) -> str:
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()
    return ""


# ------------------------------------------------------------------ reading


def analyse(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn the event stream into the numbers a failure list is written from.

    Every field here answers a question NEXT.md § 1 asked out loud: did the
    agent have tools at all (the trap that made the panel answer in prose for
    six days), which tools did it reach for, what did it get back, where did it
    stall, and did a sheet's picture actually arrive as an image.
    """
    init = next(
        (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"),
        None,
    )
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)

    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    texts: list[str] = []
    for event in events:
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    record = {
                        "name": block.get("name"),
                        "input": block.get("input"),
                        "id": block.get("id"),
                        "is_error": None,
                        "result_text": None,
                        "images": 0,
                    }
                    calls.append(record)
                    by_id[block.get("id")] = record
                elif block.get("type") == "text" and block.get("text", "").strip():
                    texts.append(block["text"].strip())
        elif event.get("type") == "user":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") != "tool_result":
                    continue
                record = by_id.get(block.get("tool_use_id"))
                if record is None:
                    continue
                record["is_error"] = bool(block.get("is_error"))
                record["result_text"] = _result_text(block)[:4000]
                content = block.get("content")
                if isinstance(content, list):
                    record["images"] = sum(
                        1 for part in content
                        if isinstance(part, dict) and part.get("type") == "image"
                    )

    advertised = set(init.get("tools", [])) if init else set()
    errors = [c for c in calls if c["is_error"]]
    unanswered = [c for c in calls if c["is_error"] is None]
    unknown = [c for c in calls if c["name"] not in advertised] if advertised else []

    # A repeat is the same tool with the same arguments, which is the shape a
    # stall takes when an agent cannot read the refusal it just got. Identical
    # *read* calls are normal (checking a result), so the count is reported
    # per tool rather than as one number to alarm on.
    signatures = Counter(
        (c["name"], json.dumps(c["input"], sort_keys=True, default=str)) for c in calls
    )
    repeats = [
        {"name": name, "input": json.loads(args), "count": count}
        for (name, args), count in signatures.items()
        if count > 1
    ]

    return {
        "session_id": (init or {}).get("session_id"),
        "model": (init or {}).get("model"),
        "mcp_servers": (init or {}).get("mcp_servers"),
        "tools_advertised": len(advertised),
        "tool_calls": len(calls),
        "tools_used": sorted({c["name"] for c in calls}),
        "tool_call_counts": dict(Counter(c["name"] for c in calls).most_common()),
        "errors": [
            {"name": c["name"], "input": c["input"], "error": (c["result_text"] or "")[:600]}
            for c in errors
        ],
        "error_count": len(errors),
        "unanswered_calls": len(unanswered),
        "unknown_tool_calls": [c["name"] for c in unknown],
        "repeated_calls": sorted(repeats, key=lambda r: -r["count"]),
        "images_returned": sum(c["images"] for c in calls),
        "sheet_calls": [c["name"] for c in calls if c["name"].endswith(("_sheet",))],
        "assistant_texts": texts,
        "final_text": texts[-1] if texts else None,
        "result_subtype": (result or {}).get("subtype"),
        "num_turns": (result or {}).get("num_turns"),
        "cost_usd": (result or {}).get("total_cost_usd"),
        "duration_ms": (result or {}).get("duration_ms"),
        "calls": calls,
    }


# ------------------------------------------------------------------ scoring


def _check(name: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def evidence_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """What the run claims it wrote, read off the agent's own tool results.

    Read from the results rather than by globbing the work directory: a stray
    file from an earlier run is not this run's render, and `renderlog` is
    written by the web UI's render pipeline and never by the MCP `export`
    tool, so it is empty for a headless trial (`renderlog.py`'s docstring).

    Its shape is deliberately *not* "the agent's calls" — the control run
    (`--control`, the scripted walkthrough) produces the same dict from CLI
    output, so one set of checks scores both and a check that fails on the
    agent can be asked of a known-good edit without a second implementation.
    """
    outputs: list[str] = []
    burns: list[dict[str, Any]] = []
    for call in analysis["calls"]:
        if call["is_error"] or not call["name"].endswith(("export", "add_captions")):
            continue
        try:
            payload = json.loads(call["result_text"] or "")
        except json.JSONDecodeError:
            continue  # a tool that answered in prose has no path to read
        # `output` means two different things across these two tools, which is
        # why the keys are read per tool rather than from one list: on `export`
        # it is the render, on `add_captions` it is the **subtitle file**, and
        # reading it blind counted a `.ass` as a delivered video ("3 of 3
        # claimed paths on disk", one of them not a picture).
        burning = call["name"].endswith("add_captions")
        keys = ("burned", "burn_output") if burning else ("output", "rendered")
        wrote = [
            payload[key]
            for key in keys
            if isinstance(payload.get(key), str) and payload[key]
        ]
        outputs.extend(wrote)
        if burning and (call["input"] or {}).get("burn"):
            burns.append({"asked": (call["input"] or {}).get("burn"), "wrote": wrote})
    return {"outputs": outputs, "burns": burns}


def score(
    project: Path,
    evidence: dict[str, Any],
    *,
    phrases: dict[str, str] | None = None,
    min_clips: int = DEMO_MIN_CLIPS,
    film: bool = False,
) -> dict[str, Any]:
    """Score the finished project against the checks the shipped film trusts.

    Two groups, and the split matters. The *generic* checks (a render exists,
    `check_frames` agrees, `verify` hears what the timeline claims) are the
    ones the repo already trusts about any project. The *brief* checks ask
    whether this particular brief was met — the retake gone, the good line
    still there, picture hung, captions burned — and they resolve phrases
    against whatever whisper actually heard, never against word indices, which
    two transcription runs do not agree on.

    `phrases` and `min_clips` are the only two things a different brief moves,
    which is why they are arguments and the rest is not: a real-footage run is
    the same instrument over different material, and a second scoring function
    would be a second opinion about what a finished cut is. Both default to
    the demo's, so every run scored before they existed re-scores identically.
    `film` adds `--film`'s three checks (`_film_checks`) after the rest.
    """
    phrases = DEMO_PHRASES if phrases is None else phrases
    checks: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}

    # Nothing below may abort the rest: a run that never seeded a timeline is
    # exactly the run whose *whole* failure list matters, and an early return
    # would report one line where the queue needs every line. Each check
    # answers for itself and every op goes through `_safe`.
    status = _safe(lambda: ops.status(project))
    facts["status"] = status
    if isinstance(status, dict) and "error" not in status:
        checks.append(
            _check(
                "timeline_seeded",
                bool(status.get("segments")),
                f"{status.get('segments')} segments, {status.get('timeline_duration')}s",
            )
        )
    else:
        checks.append(_check("timeline_seeded", False, str(status.get("error"))))

    assets = _safe(lambda: ops.assets(project))
    clips = list(assets.get("clips") or []) if isinstance(assets, dict) else []
    facts["clips"] = [c.get("clip_id") for c in clips]
    checks.append(
        _check("media_imported", len(clips) >= min_clips,
               f"{len(clips)} clips (wanted {min_clips}+): {facts['clips']}")
    )

    # --- picture -----------------------------------------------------------
    try:
        shots = ops.build_shots(project)
        facts["shots"] = shots.get("shots")
        assets_shown = {s.get("asset") for s in shots.get("shots") or []}
        checks.append(
            _check(
                "picture_hung",
                len(assets_shown) >= 2,
                f"{len(shots.get('shots') or [])} shots over {sorted(a for a in assets_shown if a)}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("picture_hung", False, f"build_shots refused: {exc}"))

    # --- the brief's own two phrases ---------------------------------------
    remove, keep = phrases.get("remove"), phrases.get("keep")
    vo = _voiceover_clip(project, clips)
    for name, phrase, want_present in (
        ("retake_removed", remove, False),
        ("good_take_kept", keep, True),
    ):
        if not phrase:
            # Undeclared is unsettled. A brief that never named the fluffed
            # line has said nothing about whether it survived, and a `False`
            # here would read in the report as the agent having left it in.
            checks.append(_check(name, None, "no phrase declared for this run (--phrases)"))
        elif vo is None:
            checks.append(_check(name, None, "no transcribed voiceover clip to ask about"))
        else:
            checks.append(_phrase_check(project, vo, phrase, want_present=want_present,
                                        name=name))
    # The two checks above cannot see this one: the fluffed take and the good
    # one open with the same words, so a cut that stops short of the fluff's
    # opening leaves both phrase checks passing over a film that stutters.
    if not keep:
        checks.append(_check("no_stutter", None, "no phrase declared for this run (--phrases)"))
    elif vo is None:
        checks.append(_check("no_stutter", None, "no transcribed voiceover clip to ask about"))
    else:
        checks.append(_stutter_check(project, vo, keep))

    # --- the render --------------------------------------------------------
    outputs = list(evidence.get("outputs") or [])
    facts["claimed_outputs"] = outputs
    # `~` is expanded, because a brief written with `~/…` paths (so the pane
    # shows no username — LAUNCH.md § Step 1's footage rule, applied to the
    # feed) is echoed back unexpanded in the tool's own reply.
    existing = [p for p in outputs if Path(p).expanduser().exists()]
    facts["existing_outputs"] = existing
    checks.append(
        _check("render_exists", bool(existing), f"{len(existing)} of {len(outputs)} claimed paths on disk")
    )

    final = Path(existing[-1]) if existing else None
    if final is None:
        checks.append(_check("frames_agree", None, "no render to check"))
        checks.append(_check("verify_similarity", None, "no render to check"))
        checks.append(_check("captions_burned", None, "no render to check"))
    else:
        facts["final_render"] = str(final)
        try:
            frames = ops.check_frames(project, final)
            facts["check_frames"] = frames
            checks.append(
                _check("frames_agree", bool(frames.get("agrees")),
                       f"delta {frames.get('delta')} "
                       f"({frames.get('expected_frames')} expected, "
                       f"{frames.get('target_frames')} in the file)")
            )
        except Exception as exc:  # noqa: BLE001
            # Unsettled, never failed. An op that *refused* has not disagreed
            # with the render — `proofcut doctor`'s own rule, and it was measured
            # here on the first real run: a second job holding the GPU made
            # whisper OOM and `verify` came back a red FAIL beside an agent
            # whose own two verify passes had read 34/34 at similarity 1.0.
            checks.append(_check("frames_agree", None, f"check_frames refused: {exc}"))
        try:
            verified = ops.verify(project, final)
            facts["verify"] = verified
            similarity = verified.get("similarity")
            checks.append(
                _check("verify_similarity", similarity is not None and similarity >= 0.9,
                       f"similarity {similarity}, {verified.get('heard_words')} heard "
                       f"vs {verified.get('expected_words')} expected")
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("verify_similarity", None, f"verify refused: {exc}"))
        checks.append(_captions_check(final, evidence))
    if film:
        checks.extend(_film_checks(project, final, vo, facts))

    facts["finish_report"] = _safe(lambda: ops.finish_report(project))
    return {"checks": checks, "facts": facts}


def _film_checks(
    project: Path, final: Path | None, vo: str | None, facts: dict[str, Any]
) -> list[dict[str, Any]]:
    """`--film`'s three checks, each judged off the delivered file.

    Each is unsettled rather than failed when there is nothing to judge — no
    render, no narration clip — and failed when the project or the file says
    no, `score()`'s own rule.
    """
    if final is None:
        return [_check(name, None, "no render to check")
                for name in ("music_placed", "loudness_on_target", "end_card_rendered")]
    # `ops.status` above reports the edit's own length; the tail check counts
    # forward from it.
    return [
        _music_check(project, final, vo, facts),
        _loudness_check(final, facts),
        _end_card_check(project, final, facts),
    ]


def _music_check(project: Path, final: Path, vo: str | None, facts: dict[str, Any]) -> dict[str, Any]:
    """Is the score in the render, at the second the plan put it?

    The same correlation `scripts/trial_check.py` asks of the kits' run — one
    implementation — over a window read off this project's own bed: inside the
    first piece's fades, offset by any cold open, and correlated against the
    asset the agent actually placed.
    """
    if vo is None:
        return _check("music_placed", None, "no narration clip to read the bed's plan off")
    view = _safe(lambda: ops.timeline_view(project, vo))
    plan = view.get("music") if isinstance(view, dict) else None
    if not plan:
        error = view.get("music_error") if isinstance(view, dict) else None
        return _check("music_placed", False, f"no bed in the project{': ' + error if error else ''}")
    piece = (plan.get("pieces") or [None])[0]
    if not piece:
        return _check("music_placed", False, "the bed has no pieces to play")
    head = float(view.get("head_seconds") or 0.0)
    start = head + float(piece["timeline_start"]) + float(piece.get("fade_in") or 0.0) + 0.2
    end = head + float(piece["timeline_end"]) - float(piece.get("fade_out") or 0.0) - 0.2
    if end - start < 3.0:
        return _check("music_placed", None, f"the bed's unfaded span is {end - start:.1f}s, too short to judge")
    opened = Project.open(project)
    clip = next((c for c in opened.read_manifest().get("clips", []) if c.get("clip_id") == piece["asset"]), None)
    if clip is None:
        return _check("music_placed", None, f"the bed's asset {piece['asset']!r} is not a registered clip")
    score_path = proofcut_media.media_path(opened, clip)
    offset = float(piece.get("src_in") or 0.0) - float(piece["timeline_start"]) - head
    heard, score_samples = trial_check.decode_mono(final), trial_check.decode_mono(score_path)
    right = trial_check.bed_share(heard, score_samples, offset, (start, end))
    wrong = max(trial_check.bed_share(heard, score_samples, offset + s, (start, end))
                for s in trial_check.BED_WRONG_SECONDS)
    facts["music_correlation"] = {"right_db": right, "wrong_db": wrong, "window": [start, end],
                                  "asset": piece["asset"], "under": plan.get("under")}
    return _check(
        "music_placed",
        right - wrong >= trial_check.BED_MARGIN_DB,
        (f"{piece['asset']} heard at {right:.1f} dB at its own second, {wrong:.1f} at the best "
         f"wrong one (margin {right - wrong:.1f}, needs {trial_check.BED_MARGIN_DB:g}); "
         f"under {plan.get('under')} LU"),
    )


def _loudness_check(final: Path, facts: dict[str, Any]) -> dict[str, Any]:
    measured = _safe(lambda: finish.loudness(final))
    facts["loudness"] = measured
    integrated = measured.get("integrated") if isinstance(measured, dict) else None
    if not isinstance(integrated, int | float):
        return _check("loudness_on_target", None, f"could not measure: {measured}")
    return _check(
        "loudness_on_target",
        abs(integrated - FILM_LOUDNESS) <= FILM_LOUDNESS_TOLERANCE,
        (f"{integrated} LUFS integrated, true peak {measured.get('true_peak')} "
         f"(asked {FILM_LOUDNESS:g} ± {FILM_LOUDNESS_TOLERANCE:g})"),
    )


def _end_card_check(project: Path, final: Path, facts: dict[str, Any]) -> dict[str, Any]:
    """A card recorded as the tail, and ink on screen halfway through it.

    Halfway is counted forward from the edit's own end (plus any cold open),
    never back from the file's: a render that dropped its tail ends on b-roll,
    and the demo's b-roll carries white text a YMAX reads as ink. So a render
    shorter than that instant fails outright. The frame is read back, because a tail recorded in the manifest is not a
    tail in the file — the end card that re-cutting dropped at exit 0 is this
    repo's own history (CLAUDE.md, `TAIL_KEY`). YMAX, never a mean: the end
    card template is light ink on a dark ground.
    """
    tail = _safe(lambda: ops.tail(project))
    record = tail.get("tail") if isinstance(tail, dict) else None
    if not record or not str(record.get("asset", "")).startswith("card:"):
        return _check("end_card_rendered", False, f"no card recorded as the tail: {record}")
    status = facts.get("status") if isinstance(facts.get("status"), dict) else {}
    edit_seconds = status.get("timeline_duration")
    head = status.get("head_seconds") or 0.0
    if not isinstance(edit_seconds, int | float):
        return _check("end_card_rendered", None, f"no edit length to find the tail by: {status}")
    at = float(head) + float(edit_seconds) + float(record.get("seconds") or 0.0) / 2
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
        capture_output=True, text=True, check=False,
    )
    try:
        length = float(probe.stdout.strip())
    except ValueError:
        return _check("end_card_rendered", None, f"could not read the render's length: {probe.stderr[-200:]}")
    if length <= at:
        return _check("end_card_rendered", False,
                      f"{record['asset']} recorded for {record.get('seconds')}s, but the render ends at "
                      f"{length:.2f}s, before the tail's midpoint {at:.2f}s")
    stats = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(final), "-frames:v", "1",
         "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YMAX:file=-", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    ymax = next((float(line.split("=", 1)[1]) for line in stats.stdout.splitlines()
                 if line.startswith("lavfi.signalstats.YMAX=")), None)
    facts["end_card"] = {"tail": record, "at": at, "ymax": ymax}
    if ymax is None:
        return _check("end_card_rendered", None, f"no frame read at {at:.2f}s: {stats.stderr[-200:]}")
    return _check("end_card_rendered", ymax > INK_YMAX,
                  f"{record['asset']} for {record.get('seconds')}s; YMAX {ymax:g} at {at:.2f}s "
                  f"(ink above {INK_YMAX})")


def _voiceover_clip(project: Path, clips: list[dict[str, Any]]) -> str | None:
    """The clip the narration is on — the one with a transcript and audio.

    Asked of the project rather than assumed to be `vo`: the agent chooses its
    own clip ids, and a trial that only scores projects using this file's
    preferred names measures obedience instead of the edit.
    """
    for clip in clips:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        transcript = _safe(
            lambda cid=str(clip_id): ops.get_transcript(project, cid, first=0, last=0)
        )
        if isinstance(transcript, dict) and transcript.get("words"):
            return str(clip_id)
    return None


def _phrase_check(
    project: Path, clip_id: str, phrase: str, *, want_present: bool, name: str
) -> dict[str, Any]:
    """Is this phrase still on the timeline?

    Survival is read off `timeline_view`'s own `present` flags — the same
    overlap test the window draws with — never re-derived here, and never from
    a word's duration, which whisper does not report honestly (CLAUDE.md).
    """
    try:
        resolved = ops.resolve_phrase(project, clip_id, phrase)
    except Exception as exc:  # noqa: BLE001
        return _check(name, None, f"phrase did not resolve: {exc}")
    first, last = resolved.get("first_word"), resolved.get("last_word")
    if first is None:
        return _check(name, None, f"phrase not found in the transcript: {phrase!r}")
    try:
        view = ops.timeline_view(project, clip_id)
    except Exception as exc:  # noqa: BLE001
        return _check(name, None, f"timeline_view refused: {exc}")
    words = view.get("words") or []
    span = [w for w in words if first <= w.get("index", -1) <= last]
    present = [w for w in span if w.get("present")]
    if want_present:
        ok = len(present) == len(span) and bool(span)
        detail = f"{len(present)}/{len(span)} words of {phrase!r} still on the timeline"
    else:
        ok = not present
        detail = f"{len(present)}/{len(span)} words of {phrase!r} still on the timeline"
    return _check(name, ok, detail)


def find_restart(words: list[str]) -> list[str] | None:
    """The longest run of words that repeats within `STUTTER_GAP` words of itself.

    Order-only, like every reader of a transcript here: it takes the words as
    spoken and knows nothing of their durations. A run repeats when a second
    copy starts at most `STUTTER_GAP` words after the first one ends, and a run
    of two or more words with at least one word that is not a stopword counts.
    """
    tokens = ["".join(ch for ch in w.lower() if ch.isalnum()) for w in words]
    tokens = [t for t in tokens if t]
    best: list[str] | None = None
    for i in range(len(tokens)):
        for j in range(i + 1, len(tokens)):
            run = 0
            while j + run < len(tokens) and tokens[i + run] == tokens[j + run] and i + run < j:
                run += 1
            if run < 2 or j - (i + run) > STUTTER_GAP:
                continue
            found = tokens[i : i + run]
            if all(t in STUTTER_STOPWORDS for t in found):
                continue
            if best is None or len(found) > len(best):
                best = found
    return best


def _stutter_check(project: Path, clip_id: str, keep: str) -> dict[str, Any]:
    """Does the good take's opening survive twice, or in part, ahead of itself?

    Read off the words still on the timeline (`timeline_view`'s `present`), in
    a window ending at the keeper phrase — the residue of a fluffed take sits
    right in front of the take that replaced it. Unsettled when the phrase or
    the view cannot be read, `score()`'s own rule.
    """
    name = "no_stutter"
    try:
        resolved = ops.resolve_phrase(project, clip_id, keep)
        first, last = resolved.get("first_word"), resolved.get("last_word")
        if first is None:
            return _check(name, None, f"phrase not found in the transcript: {keep!r}")
        words = ops.timeline_view(project, clip_id).get("words") or []
    except Exception as exc:  # noqa: BLE001
        return _check(name, None, f"could not read the timeline: {exc}")
    present = [w for w in words if w.get("present")]
    at = next((n for n, w in enumerate(present) if w.get("index", -1) >= first), None)
    if at is None:
        return _check(name, None, f"no word of {keep!r} is on the timeline")
    window = [
        w for w in present[max(0, at - STUTTER_LOOKBACK):]
        if w.get("index", -1) <= last
    ]
    found = find_restart([str(w.get("text", "")) for w in window])
    if found:
        return _check(name, False, f"{' '.join(found)!r} is said twice within {STUTTER_GAP} words")
    return _check(name, True, f"no repeated run in the {len(window)} words up to {keep!r}")


def _captions_check(final: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    """Did captions reach the *picture*, rather than only the manifest?

    The repo's own hardest-won caption lesson is that a manifest, a
    `caption-view` and a `verify` can all agree about captions that are not in
    the file — the shipped film carried none for three days on the strength of
    a status line (CLAUDE.md). So this asks the burn call whether it wrote
    *this* file, and reports `None` rather than a pass when it cannot tell:
    settling it properly means reading the render's pixels, which is a watch,
    not a number.
    """
    burns = list(evidence.get("burns") or [])
    if not burns:
        return _check("captions_burned", False, "nothing asked for a burn")
    wrote = [w for burn in burns for w in burn.get("wrote") or []]
    hit = [w for w in wrote if Path(w) == final]
    if hit:
        return _check("captions_burned", True, f"burned into {final}")
    return _check(
        "captions_burned",
        None,
        f"a burn ran and wrote {wrote or '?'}, which is not the render scored ({final}) — "
        "settle by looking at the picture",
    )


def _safe(thunk: Any) -> Any:
    try:
        return thunk()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ control


def run_control(
    project: Path, media: Path, output: Path, run_dir: Path, *, film: bool = False
) -> dict[str, Any]:
    """Meet the same brief by script, so the checks have a known-good answer.

    A first run of a new check gives candidates, not findings (the repo's
    standing rule), and the failure mode this guards against is the instrument:
    a check that fails on the agent because it is wrong about proofcut reads
    exactly like a check that fails because the agent is. So the walkthrough
    `docs/DEMO.md` prints is run here, through the same CLI a person types,
    plus the caption burn the brief asks for and DEMO.md leaves to its
    what-to-try-next list — and scored by the identical `score()`.

    The cut is resolved rather than hard-coded. DEMO.md quotes `11:23` because
    that is what one whisper run heard; a control that pins those indices
    breaks the first time the transcript moves under it, which is the same
    trap `cue_reresolve` exists for.
    """
    proofcut = [sys.executable, "-m", "proofcut.cli", "-C", str(project)]
    log: list[dict[str, Any]] = []

    def step(*argv: str) -> str:
        done = subprocess.run([*proofcut, *argv], capture_output=True, text=True, check=False)
        log.append({
            "argv": list(argv),
            "returncode": done.returncode,
            "stdout": done.stdout[-4000:],
            "stderr": done.stderr[-2000:],
        })
        if done.returncode != 0:
            raise TrialError(f"control step failed: {' '.join(argv)}\n{done.stderr.strip()[-800:]}")
        return done.stdout

    step("import", str(media / "vo.wav"), "--clip-id", "vo")
    step("import", str(media / "broll-blue.mp4"), "--clip-id", "blue")
    step("import", str(media / "broll-rust.mp4"), "--clip-id", "rust")
    step("transcribe", "vo")
    step("seed", "vo")

    # The fluffed take runs from where the abandoned sentence starts to the
    # end of the apology. Both ends are resolved against this run's own
    # transcript; `occurrence=1` on the opening words is what keeps the good
    # take — which says the same six words — out of the range.
    opening = ops.resolve_phrase(project, "vo", "Every cut you make names a", occurrence=1)
    closing = ops.resolve_phrase(project, "vo", RETAKE_PHRASE)
    first, last = opening.get("first_word"), closing.get("last_word")
    if first is None or last is None or last < first:
        raise TrialError(f"the control could not resolve the retake: {opening} / {closing}")
    step("cut", "vo", f"{first}:{last}", "--pad", "0.1")

    step("cue", "add", "vo", "--phrase", "Every cut you make names a word", "blue")
    step("cue", "add", "vo", "--phrase", "the render can be checked", "rust")

    plain = output.with_name(output.stem + "-plain" + output.suffix)
    if film:
        # DEMO.md §§ 6–8, which is the film brief met by the walkthrough.
        step("import", str(media / "music.wav"), "--clip-id", "score")
        step("music", "--asset", "score", "--clip-id", "vo", "--start-word", "0",
             "--fade-in", "1", "--fade-out", "2", "--under", "18")
        step("card", "new", "end", "--template", "endcard", "--set", "mark=proofcut")
        step("tail", "--asset", "card:end", "--seconds", "4")
        step("export", str(plain), "--render", "--loudness", f"{FILM_LOUDNESS:g}")
    else:
        step("export", str(plain), "--render")
    captions = output.with_suffix(".ass")
    step("captions", str(captions), "--burn", str(plain), "--burn-output", str(output))

    (run_dir / "control-log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return {
        "outputs": [str(plain), str(output)],
        "burns": [{"asked": str(plain), "wrote": [str(output)]}],
        "cut": {"first_word": first, "last_word": last},
        "log": log,
    }


# ----------------------------------------------------------------- reporting


def _wall(run: dict[str, Any], analysis: dict[str, Any]) -> str:
    """How long it took, falling back to the agent's own clock.

    `wall_seconds` is the harness's measurement and is absent when a run is
    re-scored from its events alone; `duration_ms` on the `result` event is
    the agent's own and survives in the event log, so a re-score reports a
    slightly smaller real number instead of the string "None".
    """
    if run.get("wall_seconds") is not None:
        return f"{run['wall_seconds']}s wall"
    ms = analysis.get("duration_ms")
    if isinstance(ms, int | float):
        return f"{round(ms / 1000, 1)}s of agent time"
    return "duration not recorded"


def write_report(run_dir: Path, run: dict[str, Any], analysis: dict[str, Any] | None,
                 scored: dict[str, Any], brief: str) -> Path:
    """One JSON of everything, one Markdown of what a person reads first.

    `analysis` is `None` for a control run, which has no agent to analyse —
    the check table is the half both runs share, and it is written identically
    for both so the two reports can be read side by side.
    """
    (run_dir / "brief.txt").write_text(brief, encoding="utf-8")
    payload: dict[str, Any] = {
        "when": datetime.now(UTC).isoformat(),
        "run": {k: v for k, v in run.items() if k != "events"},
        "score": scored,
    }
    if analysis is not None:
        payload["analysis"] = {k: v for k, v in analysis.items() if k != "calls"}
        payload["calls"] = [
            {k: v for k, v in call.items() if k != "result_text"}
            | {"result_head": (call["result_text"] or "")[:400]}
            for call in analysis["calls"]
        ]
    (run_dir / "report.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    checks = scored["checks"]
    passed = sum(1 for c in checks if c["ok"] is True)
    failed = [c for c in checks if c["ok"] is False]
    unknown = [c for c in checks if c["ok"] is None]
    title = "Agent trial" if analysis is not None else "Control run (the scripted walkthrough)"
    lines = [
        f"# {title} — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"`{run_dir}`",
        "",
    ]
    if analysis is not None:
        lines += [
            (f"- model: `{analysis['model']}`, {analysis['tools_advertised']} tools advertised, "
             f"MCP: `{analysis['mcp_servers']}`"),
            (f"- {analysis['num_turns']} turns, {analysis['tool_calls']} tool calls, "
             f"{analysis['error_count']} of them errors, "
             f"{analysis['images_returned']} images returned"),
            (f"- {_wall(run, analysis)}, ${analysis['cost_usd']}, result "
             f"`{analysis['result_subtype']}`" + (", **timed out**" if run["timed_out"] else "")),
        ]
    else:
        lines.append(f"- {run['wall_seconds']}s wall, no agent — `docs/DEMO.md`'s own commands")
    if run.get("material"):
        lines.append(f"- material: `{run['material']}`")
    lines += [
        f"- score: {passed} passed, {len(failed)} failed, {len(unknown)} unsettled",
        "",
        "## Checks",
        "",
        "| check | verdict | detail |",
        "| --- | --- | --- |",
    ]
    mark = {True: "pass", False: "**FAIL**", None: "unsettled"}
    for check in checks:
        lines.append(f"| `{check['check']}` | {mark[check['ok']]} | {check['detail']} |")

    if analysis is not None:
        lines += ["", "## Tools reached for", "", "| tool | calls |", "| --- | --- |"]
        for name, count in analysis["tool_call_counts"].items():
            lines.append(f"| `{name}` | {count} |")

        if analysis["errors"]:
            lines += ["", "## Every refusal the agent got", ""]
            for err in analysis["errors"]:
                lines += [
                    f"- `{err['name']}` {_brief_args(err['input'], 200)}",
                    f"  - {err['error'].strip()[:400]}",
                ]

        if analysis["repeated_calls"]:
            lines += ["", "## Repeated identical calls", ""]
            for rep in analysis["repeated_calls"]:
                lines.append(f"- `{rep['name']}` ×{rep['count']} — {_brief_args(rep['input'], 160)}")

        lines += ["", "## What the agent said it did", "", "```",
                  (analysis["final_text"] or "(nothing)")[:4000], "```", ""]
    path = run_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", help="the work directory (media, project and runs live here)")
    parser.add_argument("--brief-file", help="a brief to use instead of the built-in one")
    parser.add_argument("--source", help="a directory of real footage to edit instead of "
                        "generated demo media (requires --brief-file)")
    parser.add_argument("--phrases", metavar="FILE",
                        help='JSON {"remove": "...", "keep": "..."} — the two lines the '
                        "brief's own checks ask about; unset leaves both unsettled")
    parser.add_argument("--output", help="where the agent is told to render (default <work>/cut.mp4)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--model", default=None, help="pin the model (default: whatever claude picks)")
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--keep-project", action="store_true",
                        help="score the project as it stands instead of starting from `init`")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--film", action="store_true",
                        help="the whole-film brief (score, end card, master) and its three checks")
    parser.add_argument("--control", action="store_true",
                        help="meet the brief by script instead of by agent, and score it identically")
    parser.add_argument("--score-only", metavar="RUN_DIR",
                        help="re-score an existing run's events without spawning anything")
    args = parser.parse_args(argv)

    work = Path(args.work).expanduser().resolve()
    output = Path(args.output).expanduser() if args.output else work / "cut.mp4"
    source = Path(args.source).expanduser().resolve() if args.source else None

    # Both refusals are before anything is generated, spawned or removed.
    if source is not None and not args.brief_file:
        parser.error(
            "--source needs --brief-file: the built-in brief names three demo files and "
            "a fluffed take that real material does not have, and an agent handed a brief "
            "about footage it cannot see is measuring the brief."
        )
    if source is not None and args.control:
        parser.error(
            "--control is `docs/DEMO.md`'s own walkthrough over the demo footage, so it "
            "cannot meet a brief about other material. Score a real-footage run against "
            "the demo control, or write a scripted control for that brief."
        )

    if args.score_only:
        run_dir = Path(args.score_only).expanduser().resolve()
        # A project from before the rename is refused by every op `score`
        # calls, and `_safe` turns each refusal into a failed check — so the
        # re-score would overwrite a passing report with a false one, at exit 0.
        # Refuse before anything is rewritten (HISTORY.md § The rename).
        if (work / "proj" / LEGACY_MANIFEST_NAME).exists():
            raise TrialError(
                f"{work / 'proj'} holds {LEGACY_MANIFEST_NAME}, a project from before the "
                f"rename: run `proofcut -C {work / 'proj'} migrate`, then re-score. "
                "Nothing was rewritten."
            )
        events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        # The prior report's own `run` block is carried forward rather than
        # stubbed: wall time, exit code and the timeout flag are facts about
        # the run that re-scoring cannot re-measure, and a stub silently
        # replaced them with `None` — printed in the rewritten report as
        # "Nones wall".
        prior = _safe(lambda: json.loads((run_dir / "report.json").read_text(encoding="utf-8")))
        run = (prior or {}).get("run") if isinstance(prior, dict) else None
        if not isinstance(run, dict):
            run = {"returncode": None, "timed_out": False, "wall_seconds": None,
                   "undecodable_lines": 0, "stderr_tail": ""}
        # The run's own scoring parameters, not this file's defaults: a
        # re-score months later must answer the question the run was scored on.
        # Absent for every run made before they existed, and those were all
        # demo runs, which is exactly what the defaults are.
        params = _safe(lambda: json.loads((run_dir / "scoring.json").read_text(encoding="utf-8")))
        params = params if isinstance(params, dict) and "error" not in params else {}
        analysis = analyse(events)
        scored = score(
            work / "proj",
            evidence_from_analysis(analysis),
            phrases=params.get("phrases"),
            min_clips=params.get("min_clips", DEMO_MIN_CLIPS),
            film=bool(params.get("film")),
        )
        report = write_report(run_dir, run, analysis, scored, (run_dir / "brief.txt").read_text())
        print(f"\n{report}")
        return 0

    lock = hold_lock(work)
    try:
        media, project = prepare(work, fresh=not args.keep_project, source=source, film=args.film)
        print(f"media   -> {media}" + ("  (real footage, not generated)" if source else ""))
        print(f"project -> {project}")
        if args.prepare_only:
            return 0

        brief = (
            Path(args.brief_file).expanduser().read_text(encoding="utf-8")
            if args.brief_file
            else (FILM_BRIEF if args.film else DEMO_BRIEF)(media=media, project=project, output=output)
        )

        phrases = (
            json.loads(Path(args.phrases).expanduser().read_text(encoding="utf-8"))
            if args.phrases
            else (None if source is not None else DEMO_PHRASES)
        )
        min_clips = SOURCE_MIN_CLIPS if source is not None else DEMO_MIN_CLIPS

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = work / "runs" / (f"control-{stamp}" if args.control else stamp)
        run_dir.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({
                "pid": os.getpid(),
                "started": datetime.now(UTC).isoformat(),
                "run_dir": str(run_dir),
            }),
            encoding="utf-8",
        )
        (run_dir / "scoring.json").write_text(
            json.dumps({"phrases": phrases, "min_clips": min_clips, "material": str(media),
                        "film": args.film},
                       indent=2),
            encoding="utf-8",
        )
        print(f"run     -> {run_dir}\n")

        if args.control:
            started = time.time()
            evidence = run_control(project, media, output, run_dir, film=args.film)
            run = {"returncode": 0, "timed_out": False,
                   "wall_seconds": round(time.time() - started, 1),
                   "undecodable_lines": 0, "stderr_tail": ""}
            analysis = None
        else:
            run = run_agent(project, brief, run_dir, timeout=args.timeout,
                            model=args.model, budget_usd=args.budget_usd)
            analysis = analyse(run["events"])
            evidence = evidence_from_analysis(analysis)
        run["material"] = str(media)
        scored = score(project, evidence, phrases=phrases, min_clips=min_clips, film=args.film)
        report = write_report(run_dir, run, analysis, scored, brief)
        print(f"\n{report}")
        failed = [c["check"] for c in scored["checks"] if c["ok"] is False]
        print("failed checks:", ", ".join(failed) if failed else "none")
        return 0
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
