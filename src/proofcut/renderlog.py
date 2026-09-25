"""The render log — `cache/renders.jsonl`, one line per finished pipeline run.

**Cache artifact only. No manifest key. No schema bump.** Mirrors
`Project.thumbs_path`'s own precedent exactly (`project.py`'s `THUMBS_LOG`):
this is derived telemetry, not part of the edit — nothing here is
authoritative for the timeline or the manifest, `_revision()` in webui.py
never stats it, and a render pipeline run is not a project mutation. It
exists so `ops.finish_report` can answer "did the last render actually burn
captions in" without re-running or re-parsing anything — docs/plans/STUDIO.md § Step 01
is explicit that an absent log is a warning (`captions.burned == "unknown"`),
never an error and never a reason to guess.

One line is one whole run: which stages were attempted, what each one's
outcome was, and the output path/preset/expected duration at the time.
Nothing here parses the render pipeline's own stage logic — this module only
serializes and deserializes what the caller already decided happened.

**There are two writers, because there are two shapes of render.** The web
UI's `RenderJob` runs the whole pipeline itself and knows the whole run at
once, so it calls `append`. The CLI and the MCP server have no pipeline —
an agent renders with `export` and then burns with `add_captions`, two
separate calls minutes apart — so those two ops call `amend`, which carries
the earlier stages of the same render forward onto a new line rather than
starting a second run that would hide the first. Until 2026-09-04 neither
wrote anything at all, and `finish_report` answered `captions.burned` with
`"unknown"` for two of proofcut's three clients on films whose captions were
demonstrably burned in (TRIAL.md § 2).

**A line's `sources` maps each stage that read the project to the hashes
of `project.otio` and the manifest as that stage found them** (`stamp`).
Only `export` and `burn` read the project; `check_frames` and `verify` read
the render. It sits beside `stages` rather than inside each stage's dict, so
a stage reads exactly as it did before stamps existed, and `amend` carries it
forward the way it carries `stages`. It is kinocut's receipt idea at its cheapest (PRIOR-ART.md § kinocut),
and it answers the question a render lying on disk otherwise cannot: which
edit is this? A dogfood project has held the wrong cut while every check
passed four times (CLAUDE.md), and each time the render was settled by length
and eye. `current` compares a run's stamps against the project now. Hashes of
the two files and never an mtime: a clock is the wrong witness for "did the
bytes change" (HISTORY.md § The stamp that was a clock). A line written
before this has no `sources`, and `current` answers `None` for it, never
`True`.

**A burn's stamp carries a third hash, `lexicon`**, because a burn reads
`lexicon.json` (its `hear` table is what the captions print, CLAUDE.md) and
an export does not. `current` compares each hash a source carries and no
other, so an export is still current after a lexicon edit and a burn is not,
and a line stamped before the key existed reads as it always did.

**A line's `request` is what the web pipeline was asked for** — preset,
resolution and whether it burned — and only that writer records one. It is
what lets `RenderJob` answer "would rendering again produce this file?": the
same request against the same stamps is the same film, and the job then
reuses the file and runs only the checks (HISTORY.md § A render on an
unchanged edit is not re-rendered). A line with no `request`, which is every
agent-assembled render, is never reused, because its export may have been
asked for things the log does not record (a loudness target, say).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from proofcut import lexicon as lex
from proofcut.project import Project


def stamp(project: Project, *, lexicon: bool = False) -> dict[str, str | None]:
    """The project's edit as a stage is about to read it: a sha256 of each file's bytes.

    `timeline` is `None` for a project with no `project.otio`, which no render
    can come from, but a stamp is a record and not a check, so it says so
    rather than raising. `lexicon=True` adds `lexicon.json`'s hash under
    `lexicon`, for the one stage that reads it (a burn; module docstring).
    """

    def digest(path: Path) -> str | None:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    stamped = {
        "timeline": digest(project.timeline_path),
        "manifest": digest(project.manifest_path),
    }
    if lexicon:
        stamped["lexicon"] = digest(lex.project_path(project.root))
    return stamped


def current(project: Project, run: dict[str, Any]) -> bool | None:
    """Whether every stage of `run` that read the project read the project as it is now.

    `None` when the line has no `sources` — one older than the stamp, or a
    run that failed before any stage finished — because "not recorded" is not
    "unchanged". `False` means the edit has moved since the render, so the
    render is not the film the project describes any more. Each source is
    compared on the hashes it carries: a burn's `lexicon`, and never a key
    the stage did not stamp.
    """
    sources = [s for s in (run.get("sources") or {}).values() if s]
    if not sources:
        return None
    now = stamp(project, lexicon=True)
    return all(now.get(key) == value for source in sources for key, value in source.items())


def append(
    project: Project,
    *,
    output: str,
    preset: str | None,
    expected_duration: float,
    stages: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, str | None]] | None = None,
    request: dict[str, Any] | None = None,
) -> None:
    """Append one run to the log. Stamps its own timestamp — callers never pass one.

    Only stages actually *attempted* belong in `stages`; a run that failed at
    `export` writes a `stages` dict holding only `export`, nothing for the
    stages never reached (`check_frames`/`verify` in particular, since those
    are always the last two attempted). `request` is the web pipeline's
    (module docstring); `amend` never carries one forward, since a later
    stage added by hand is no longer the run that request describes.
    """
    project.renders_log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "output": output,
        "preset": preset,
        "expected_duration": expected_duration,
        "stages": stages,
    }
    if sources:
        # Absent rather than `{}` when no stage stamped anything, so a run
        # that failed before its export finished reads like a pre-stamp line.
        record["sources"] = sources
    if request is not None:
        record["request"] = request
    with project.renders_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def amend(
    project: Project,
    *,
    output: str,
    preset: str | None,
    expected_duration: float,
    stages: dict[str, dict[str, Any]],
    continues: str | None = None,
    sources: dict[str, dict[str, str | None]] | None = None,
) -> None:
    """Append a run that carries the last one's stages forward, when it is the same render.

    `continues` is the file the new stage consumed — `add_captions(burn=X)`
    passes `X`. When it matches the last run's `output`, this is a later stage
    of *that* render and its stages are merged onto the new line; otherwise
    this is a new render and only `stages` is written.

    The file stays append-only — a merge is a new line, not an edit — because
    `last` reads from the end and a superseding line is what it will find. The
    superseded line is left in place for `all_runs`, which is how a render that
    was burned twice still shows both attempts.

    The stage dicts themselves are never merged: a stage present in `stages`
    replaces the earlier one wholesale, since a second burn onto the same
    export is a new answer to the same question, not an addition to the old
    one.
    """
    carried: dict[str, dict[str, Any]] = {}
    carried_sources: dict[str, dict[str, str | None]] = {}
    if continues is not None:
        previous = last(project)
        if previous is not None and previous.get("output") == continues:
            carried = dict(previous.get("stages") or {})
            carried_sources = dict(previous.get("sources") or {})
            # The preset is the export's property, and a later stage of the
            # same render does not know it — `add_captions` has no preset of
            # its own to pass. Carry the earlier one rather than writing null
            # over it, which would make the burn look like an unpresetted
            # render of its own.
            if preset is None:
                preset = previous.get("preset")
    append(
        project,
        output=output,
        preset=preset,
        expected_duration=expected_duration,
        stages={**carried, **stages},
        sources={**carried_sources, **(sources or {})},
    )


def last(project: Project) -> dict[str, Any] | None:
    """The most recent run, or `None` if the log is absent or unreadable.

    Walks the file in reverse so a corrupted trailing line — a crash mid-write
    left a half-written last line — cannot hide every real run before it;
    the first line (from the end) that parses as JSON wins.
    """
    path = project.renders_log_path
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def all_runs(project: Project) -> list[dict[str, Any]]:
    """Every parseable run, oldest first — a malformed line is skipped, not fatal."""
    path = project.renders_log_path
    if not path.exists():
        return []
    runs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs
