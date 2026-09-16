"""Project operations — the single implementation behind both front ends.

Every MCP tool and every `proofcut` CLI subcommand is a thin wrapper over a
function here. That is what keeps the two in parity without duplicating logic,
and it is why the CLI is a debugging surface rather than a second codebase
(CLAUDE.md).

Each function returns a plain dict: MCP wants structured returns, and the CLI
wants something to print as JSON.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from math import ceil, gcd, hypot
from pathlib import Path
from typing import Any

from proofcut import (
    asr,
    autoeditor,
    captions,
    energy,
    faces,
    finish,
    finishlog,
    graphics,
    media,
    mlt,
    picture,
    progress,
    renderlog,
    tts,
)

# `describe` is also the name of the op below — the same collision `verify`
# has, and the same fix.
from proofcut import describe as dsc

# `doctor` is also the name of the op below — the `describe`/`verify`/`fonts`
# collision again, and the same fix.
from proofcut import doctor as doc

# `duck` is `music`'s own argument, so the module takes an alias rather than
# be shadowed inside the one function that sets it.
from proofcut import duck as dk

# `duck` is `music`'s own argument, so the module takes an alias rather than
# be shadowed inside the one function that sets it.
# `fonts` is also the name of the op below, so the module needs an alias here
# or the function would shadow it at call time — the `describe`/`verify` fix.
from proofcut import fonts as proofcut_fonts
from proofcut import pack as pk
from proofcut import speakers as spk
from proofcut import speech as sp
from proofcut import timeline as tl
from proofcut import transcript as tx

# `verify` is also the name of the op below, so the module needs an alias here
# or the function would shadow it at call time.
from proofcut import verify as vfy
from proofcut.project import Project, ProjectError

WordRange = tuple[int, int]


def _clips_by_id(project: Project) -> dict[str, dict[str, Any]]:
    return {c["clip_id"]: c for c in project.read_manifest().get("clips", [])}


def _rate(project: Project) -> float:
    manifest = project.read_manifest()
    if "timebase" in manifest:
        return float(manifest["timebase"])
    clips = manifest.get("clips", [])
    fps = next((c["fps"] for c in clips if c.get("has_video") and c.get("fps")), None)
    return float(fps) if fps else float(autoeditor.AUDIO_TIMEBASE)


def _load_edit(project: Project) -> tl.Edit:
    if not project.timeline_path.exists():
        raise ProjectError(
            "this project has no timeline yet — run `proofcut seed <clip_id>` "
            "(or the seed_timeline tool) to lay the source down first"
        )
    return tl.read(project.timeline_path)


def _save_edit(project: Project, edit: tl.Edit) -> None:
    """Snapshot, then write. Order matters: the snapshot is of the *old* state."""
    project.snapshot()
    otio = tl.to_otio(edit, _clips_by_id(project), rate=_rate(project), name=project.root.name)
    tl.write(otio, project.timeline_path)


# -- setup ---------------------------------------------------------------


def init(path: Path | str, *, name: str | None = None) -> dict[str, Any]:
    project = Project.create(path, name=name)
    return {"project": str(project.root), "manifest": project.read_manifest()}


def doctor() -> dict[str, Any]:
    """Probe every external dependency proofcut needs, and name each one's trap.

    The one op that takes no project, because it answers a question asked
    *before* there is one: can this machine run proofcut at all. Report-only —
    it installs nothing, writes nothing, and opens no project.

    `ok` reads the **required** section alone. The four optional
    capabilities gate one feature each (cards, `describe`, `reframe-detect`,
    `vo-synth`), and everything proofcut promises works without all four, so a
    box with none of them still gets a clean bill of health.

    The value is not the ✓/✗ — it is the sentence after a ✗. Every failure
    carries the named trap out of this repo's own record and the command that
    fixes it: that melt lives inside the Kdenlive flatpak, that PyPI's
    auto-editor is a stale fork of a different program, that an unattended box
    renders under `QT_QPA_PLATFORM=offscreen` rather than needing a session.
    `doctor.render()` is the human rendering of the same dict, which is what
    `proofcut doctor` prints.
    """
    return doc.report()


def info(path: Path | str, *, raw: bool = False) -> dict[str, Any]:
    """The project's manifest, summarised for reading at a glance.

    `describe` is the fastest way to make a manifest huge — a described
    project's `info` used to run 103 KB of prose in a command whose whole job
    is being readable at a glance. Descriptions are stood down to a per-clip
    count here; `describe_ls` is where the text is meant to be read, and
    `raw=True` is the escape hatch back to the exact stored bytes, since
    nothing else can show them.

    Was CLI-only, reading the manifest straight off disk rather than through
    an op — a parity violation (CLAUDE.md: the CLI is a thin wrapper, never a
    second implementation). This is that fixed, with the same behaviour.
    """
    manifest = Project.open(path).read_manifest()
    if raw or not manifest.get("descriptions"):
        return manifest
    descriptions = manifest["descriptions"]
    per_clip: dict[str, int] = {}
    for entry in descriptions:
        per_clip[entry["clip_id"]] = per_clip.get(entry["clip_id"], 0) + 1
    return {
        **manifest,
        "descriptions": {
            "count": len(descriptions),
            "clips": per_clip,
            "read": "proofcut describe-ls (or `proofcut info --raw` for the stored entries)",
        },
    }


def migrate(path: Path | str, *, plan: bool = False) -> dict[str, Any]:
    """Bring an older project manifest forward to the current schema version.

    Every other op goes through `Project.open`, which refuses a manifest it
    does not recognise rather than guessing at its shape; this is what clears
    that refusal. Forward-only, and the pre-migration manifest is copied into
    `cache/history/` before anything is written. A project written before the
    rename (`lucid.json`) takes a filename step first — `Project.migrate`.
    """
    return Project.migrate(path, plan=plan)


def import_media(
    path: Path | str,
    source: Path | str,
    *,
    clip_id: str | None = None,
    copy: bool = False,
    mix: bool = False,
    audio_stream: int | None = None,
    sheet: bool = True,
) -> dict[str, Any]:
    """Register a media file, with a first-look `contact_sheet` riding along.

    `sheet=True` by default: the whole point of a first-look sheet is that it
    is *seen*, not merely available for a caller to remember to ask for — an
    opt-in-only sheet reproduces the same silent-unless-looked-at shape
    CLAUDE.md keeps naming (`stale_seconds`, `off_timeline`). Best-effort and
    never fails the import itself: a thumbnail extraction problem on a clip
    that otherwise imported fine surfaces as `contact_sheet_error`, not as a
    raised exception a caller has to catch around a successful registration.
    """
    project = Project.open(path)
    record = media.import_media(
        project, source, clip_id=clip_id, copy=copy, mix=mix, audio_stream=audio_stream
    )
    if sheet and record.get("has_video"):
        try:
            record = {
                **record,
                # No montage: an import reply cannot carry an image, and the
                # pane that reads this draws the thumbs themselves.
                "contact_sheet": contact_sheet(path, record["clip_id"], montage=False),
            }
        except Exception as exc:  # noqa: BLE001 — best-effort, never fails the import
            record = {**record, "contact_sheet_error": str(exc)}
    return record


def list_media(path: Path | str, source_dir: Path | str, *, recursive: bool = True) -> dict[str, Any]:
    """List media files under `source_dir` that `import_media` could register.

    TRIAL.md item 7: with `--tools ""` the agent panel gives an agent no
    directory listing, so on a real job something has to hand it source
    paths. This is that something — a proofcut tool, so it is reachable inside
    the same sandbox the panel already confines the agent to, rather than a
    wrapper or a person pasting paths into the brief.

    A filename filter (`media.SOURCE_MEDIA_EXTENSIONS`), not a probe — cheap
    over a directory of raw footage. Each entry's `already_imported` compares
    against this project's own registered clips (`clip["source"]`, the same
    resolved-path key `import_media`'s dedup already uses), so a repeated
    call does not re-suggest what is already on the timeline's own asset
    list. `source_dir` is arbitrary and unconfined on purpose — it names
    where footage lives, not a project.
    """
    project = Project.open(path)
    manifest = project.read_manifest()
    imported = {c["source"] for c in manifest.get("clips", [])}
    found = media.discover(source_dir, recursive=recursive)
    files = [
        {"path": str(p), "size": p.stat().st_size, "already_imported": str(p) in imported}
        for p in found
    ]
    return {
        "source_dir": str(Path(source_dir).expanduser().resolve()),
        "recursive": recursive,
        "count": len(files),
        "new": sum(1 for f in files if not f["already_imported"]),
        "files": files,
    }


def _near_duplicates(parsed: tx.Transcript) -> list[dict[str, Any]]:
    """Flag adjacent near-duplicate phrases in a just-attached transcript.

    Called from both attach paths. The retake `verify` structurally cannot
    catch is one the timeline keeps both takes of, so there is nothing to diff
    against. Catching it means looking at attach time, before an edit exists.
    HISTORY.md § Adjacent near-duplicate phrases at `attach-transcript`.
    """
    return vfy.find_adjacent_repeats(vfy.tokens(w.text for w in parsed.words))


def _suspect_durations(parsed: tx.Transcript) -> list[dict[str, Any]]:
    """Flag words whose claimed duration is a lie about something.

    `energy.believable` already computes this same 3x-median cutoff to mask
    audio for `verify --windowed`'s envelope pass — this just surfaces it as a
    finding at attach time, before it is ever used as a cut boundary
    (`cut_by_transcript` refuses those without confirmation).
    HISTORY.md § Suspect word durations at `attach-transcript`.
    """
    spans = [(w.start, w.end) for w in parsed.words]
    flagged = energy.suspect_durations(spans)
    for item in flagged:
        item["text"] = parsed.words[item["index"]].text
    return flagged


def _overlaps(parsed: tx.Transcript) -> list[dict[str, Any]]:
    """Flag seams where two words' timings overlap — an invented word's tell.

    The third of the attach-time checks, and it catches what the other two
    structurally cannot. `_near_duplicates` matches *phrases*, so a splice
    that invents a single word repeats nothing for it to match
    (`coincidence incidents`, `guy's guys`, `is genu genuinely`); measured on
    the Scream VO, 39 of 56 overlapping pairs fall outside every
    near-duplicate window. `_suspect_durations` looks at one word's length,
    and a seam's words are ordinary-length — they are merely in two places at
    once. HISTORY.md § The hand-framed teaser, watched.

    The echo is the point here as much as anywhere else (CLAUDE.md): a seam
    reads as correct English on its own and the neighbours are what show it
    is a splice.
    """
    seams = tx.find_overlaps(parsed.words)
    for seam in seams:
        seam.update(_context(parsed, seam["first_word"], seam["last_word"]))
    return seams


def _repeats(parsed: tx.Transcript) -> list[dict[str, Any]]:
    """Flag back-to-back duplicated phrases — the shape a retake makes.

    The fourth attach-time check, and the one none of the other three can
    catch: `_near_duplicates` looks at the same span of transcript this does,
    but `tx.find_repeats` is the one ported straight from the tool that
    actually caught the Scream VO's retake pass by hand
    (`goodsometimes/scripts/vo_windows.py --repeats`, which lives outside
    proofcut). See its docstring for the blind spot this still has — it can only
    see a retake that survived as distinct words, which is a *different*
    subset of retakes than `_overlaps`' seam scan finds, not a smaller one.
    """
    repeats = tx.find_repeats(parsed.words)
    for item in repeats:
        item.update(_context(parsed, item["first_word"], item["last_word"]))
    return repeats


def attach_transcript(
    path: Path | str, clip_id: str, transcript_path: Path | str
) -> dict[str, Any]:
    """Ingest an existing word-timed transcript instead of re-running ASR.

    Recordings often already have one — the Scream VO was transcribed before
    proofcut existed. Re-transcribing to get an index proofcut could have read is
    wasted GPU time and a second set of timings to disagree with.
    """
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    parsed = tx.load(transcript_path, clip_id=clip["clip_id"])
    tx.save(parsed, project.transcript_path(clip_id))
    return {
        "clip_id": clip_id,
        "words": len(parsed),
        "language": parsed.language,
        "cached": str(project.transcript_path(clip_id)),
        "duration": parsed.words[-1].end,
        "near_duplicates": _near_duplicates(parsed),
        "suspect_durations": _suspect_durations(parsed),
        "overlaps": _overlaps(parsed),
        "repeats": _repeats(parsed),
    }


def transcribe(
    path: Path | str,
    clip_id: str,
    *,
    model: str = asr.DEFAULT_MODEL,
    language: str | None = None,
) -> dict[str, Any]:
    """Transcribe a clip's own media with whisper and attach the result.

    `attach_transcript`'s ASR-driven sibling: use that when the recording
    already has a transcript (common — the Scream VO was transcribed before
    proofcut existed), use this when it doesn't and whisper has to make one.

    `hallucinated_words` is reported for the same reason the windowed pass
    reports it: non-zero means whisper stumbled somewhere in this transcription
    and the guard contained it, which is worth knowing about a transcript every
    later cut is addressed against.
    """
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    source = media.media_path(project, clip)
    payload = asr.transcribe(
        source, model=model, language=language, duration=clip.get("duration")
    )
    # Whisper returns an empty `segments` list rather than failing when it
    # hears no speech, and parse_whisper would then blame the missing word
    # timestamps — which were requested. Say what actually happened (verify
    # hits the same case transcribing a render).
    if not payload.get("words") and not payload.get("segments"):
        raise asr.ASRError(
            f"whisper heard no speech at all in {source.name}. Either this "
            "clip has no dialogue on it, or the wrong clip was transcribed."
        )
    parsed = tx.parse_whisper(payload, clip_id=clip_id, origin=f"whisper:{model}")
    tx.save(parsed, project.transcript_path(clip_id))
    return {
        "clip_id": clip_id,
        "words": len(parsed),
        "language": parsed.language,
        "cached": str(project.transcript_path(clip_id)),
        "duration": parsed.words[-1].end,
        "hallucinated_words": payload.get("hallucinated_words", 0),
        "near_duplicates": _near_duplicates(parsed),
        "suspect_durations": _suspect_durations(parsed),
        "overlaps": _overlaps(parsed),
        "repeats": _repeats(parsed),
    }


def hear(
    path: Path | str,
    clip_id: str,
    *,
    start: float,
    end: float,
    model: str = asr.WINDOWED_MODEL,
    language: str | None = None,
    window: float = asr.WINDOW,
    overlap: float = asr.OVERLAP,
) -> dict[str, Any]:
    """What does `clip_id`'s source audio actually say between `start` and `end`?

    The question the real-footage trial spent five minutes building its own
    answer to (TRIAL.md § The queue, item 1): the transcript said the stretch
    was clean, the word durations said something was hidden in it, and the
    only route to *hearing* the source was to seed a timeline, export it and
    `verify` the export. This is that route as one call — `asr.transcribe_
    windowed` over the clip's own media across a source-second span, the
    same pass `verify --windowed` runs, because the smaller model in short
    overlapping windows is the one that does not tidy a retake away.

    **Reports, never attaches.** A second reading of the same audio beside the
    attached transcript is a second answer to "what is in this clip", and the
    transcript is the one every cut is addressed against — so this writes no
    transcript, moves no word index, and hands back the attached transcript's
    own words over the same span (`transcript_words`) so the two can be read
    side by side. When they disagree, `cut_by_time` addresses what the
    transcript has no word for.

    `heard_words` may be empty: silence is a real answer here, where `verify`
    treats it as a failure. `end` past the clip is refused, not clamped.
    Costs one whisper run over ~1.4x the span; on a long clip the whole file
    is still decoded once first.
    """
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    source = media.media_path(project, clip)
    start, end = float(start), float(end)
    if start < 0:
        raise tl.TimelineError(f"start={start:.3f} is negative")
    if end <= start:
        raise tl.TimelineError(f"span {start:.3f}-{end:.3f} is empty or backwards")
    duration = clip.get("duration")
    if duration is not None and end > float(duration) + 0.01:
        raise tl.TimelineError(
            f"span ends at {end:.3f}s but {clip_id!r} is {float(duration):.3f}s long"
        )
    payload = asr.transcribe_windowed(
        source,
        window=window,
        overlap=overlap,
        model=model,
        language=language,
        start=start,
        end=end,
        allow_silence=True,
    )
    heard = [
        {
            "index": i,
            "text": (w.get("word") or "").strip(),
            "start": w["start"],
            "end": w["end"],
        }
        for i, w in enumerate(payload["words"])
    ]

    cached = project.transcript_path(clip_id)
    attached: list[dict[str, Any]] | None = None
    if cached.exists():
        parsed = tx.load(cached, clip_id=clip_id)
        # Overlap, never containment — a word whose duration swallowed a
        # retake spans past the window and is exactly the word to show.
        attached = [
            {"index": w.index, "text": w.text, "start": w.start, "end": w.end}
            for w in parsed.words
            if w.start < end and w.end > start
        ]

    return {
        "clip_id": clip_id,
        "source": str(source),
        "start": start,
        "end": payload["end"],
        "model": model,
        "language": payload.get("language") or language,
        "window": window,
        "overlap": overlap,
        "windows": payload["windows"],
        "silent_windows": payload["silent_windows"],
        "hallucinated_words": payload["hallucinated_words"],
        "heard_words": heard,
        "heard_text": " ".join(w["text"] for w in heard if w["text"]),
        "transcript_words": attached,
        "transcript_text": (
            None if attached is None else " ".join(w["text"] for w in attached)
        ),
        "attached": False,
        "note": (
            "a reading of the source, not a transcript — nothing was written; "
            "address a cut the transcript has no word for with cut_by_time"
        ),
    }


def _transcript(project: Project, clip_id: str) -> tx.Transcript:
    cached = project.transcript_path(clip_id)
    if not cached.exists():
        raise tx.TranscriptError(
            f"no transcript for {clip_id!r} — make one with "
            f"`proofcut transcribe {clip_id}`, or attach one with "
            f"`proofcut attach-transcript {clip_id} <whisper.json>`"
        )
    return tx.load(cached, clip_id=clip_id)


# -- phrase addressing -------------------------------------------------------
#
# Every word-indexed mutator below (cue_add, cue_rm, unspoken_add/rm,
# vo_extend, music, locate) accepts `phrase=` as an alternative to a plain
# `word_index` — this is the one place that translates "a phrase" into "a
# word index (or two)", so the xor-validation and the ambiguity policy are
# written once. `edge` is a property of what the *tool* means, never
# something a caller chooses (CLAUDE.md-shaped: hardcoded per call site).


def _resolve_word_or_phrase(
    parsed: tx.Transcript | None,
    *,
    word_index: int | None,
    phrase: str | None,
    after: int = -1,
    occurrence: int | None = None,
    edge: str,
    single: bool = False,
) -> tuple[int, int]:
    """Resolve a tool's word address from a plain index or a phrase.

    Exactly one of `word_index`/`phrase` must be given — raises
    `TranscriptError` otherwise, `locate`'s own "two ways of naming one
    thing, pick one" idiom, generalized.

    `edge` picks which word of a *phrase* match becomes the returned
    address: `"first"`/`"last"` collapse a (possibly multi-word) match down
    to one word, returned as `(word, word)` — a plain `word_index` passes
    through unchanged either way, since a caller who named an index meant
    exactly that word. `"range"` returns the phrase match's own span
    untouched, for a tool (`locate`) whose address *is* a range already.

    `single=True` additionally refuses a phrase match wider than one word —
    `unspoken_add`/`unspoken_rm` address exactly one word, and picking an
    edge of a wider match would silently mark the wrong word half the time.
    """
    if (word_index is None) == (phrase is None):
        raise tx.TranscriptError(
            "pass word_index or phrase, not both and not neither — they are "
            "two ways of naming the same word, and a call giving both cannot "
            "say which one it meant"
        )
    if word_index is not None:
        idx = int(word_index)
        return idx, idx

    assert parsed is not None and phrase is not None
    resolved = parsed.resolve(phrase, after=after, occurrence=occurrence)
    first, last = resolved["first_word"], resolved["last_word"]
    if single and first != last:
        raise tx.TranscriptError(
            f"phrase {phrase!r} resolved to words {first}-{last} "
            f"({resolved['text']!r}) — this needs exactly one word, narrow "
            "the phrase"
        )
    if edge == "first":
        return first, first
    if edge == "last":
        return last, last
    return first, last


def resolve_phrase(
    path: Path | str,
    clip_id: str,
    phrase: str,
    *,
    after: int = -1,
    occurrence: int | None = None,
    fuzzy: bool = True,
) -> dict[str, Any]:
    """Resolve a phrase to a word range against `clip_id`'s transcript.

    Read-only, echoed like every word-indexed tool here — what `phrase=` on
    `cue_add`/`cue_rm`/`unspoken_add`/`unspoken_rm`/`vo_extend`/`music`/
    `locate` calls internally, exposed on its own so a script or an agent can
    inspect a resolution — including its full ambiguity list — without
    attempting a write. Companion to `get_transcript --search`, which lists
    every match with no cursor/occurrence/fuzzy; this picks exactly one, or
    explains why it can't (`Transcript.resolve`).
    """
    project = Project.open(path)
    parsed = _transcript(project, clip_id)
    resolved = parsed.resolve(phrase, after=after, occurrence=occurrence, fuzzy=fuzzy)
    return {
        "clip_id": clip_id,
        "phrase": phrase,
        **resolved,
        **_context(parsed, resolved["first_word"], resolved["last_word"]),
    }


def get_transcript(
    path: Path | str,
    clip_id: str,
    *,
    first: int | None = None,
    last: int | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Read the transcript: a window of it, or the hits for a phrase.

    `limit` caps the words one call returns, counted from `first`, and the
    reply says where it stopped: `next_first` is the index to ask for next,
    present only when words were left out. A whole transcript is 94 KB on a
    5.6-minute VO, past what an MCP client will put in context
    (docs/plans/MCP.md § Step 5), so the MCP tool sets one by default; the CLI
    does not.
    """
    project = Project.open(path)
    parsed = _transcript(project, clip_id)

    if search is not None:
        return {"clip_id": clip_id, "search": search, "matches": parsed.find(search)}

    lo = 0 if first is None else first
    end = len(parsed) - 1 if last is None else min(last, len(parsed) - 1)
    hi = end if limit is None else min(end, lo + max(limit, 1) - 1)
    words = parsed.window(lo, hi)
    result: dict[str, Any] = {
        "clip_id": clip_id,
        "total_words": len(parsed),
        "first_word": lo,
        "last_word": hi,
        "text": " ".join(w.text for w in words),
        "words": [w.as_dict() for w in words],
    }
    if hi < end:
        result["next_first"] = hi + 1
    return result


def window_list(
    report: dict[str, Any], key: str, first: int | None, limit: int | None
) -> dict[str, Any]:
    """Cut `report[key]` to `limit` items from `first`, and say so beside it.

    The one shape every bounded list in a reply takes: `<key>_total`, the
    `<key>_first`/`<key>_last` indices kept, and `<key>_next` only when items
    were left out. Unset `first` and `limit` leave the report exactly as it
    was, so the window and every caller that never asked are unaffected.
    """
    items = report.get(key)
    if not isinstance(items, list) or (first is None and limit is None):
        return report
    lo = max(first or 0, 0)
    hi = len(items) if limit is None else min(len(items), lo + max(limit, 1))
    report[key] = items[lo:hi]
    report[f"{key}_total"] = len(items)
    report[f"{key}_first"] = lo
    report[f"{key}_last"] = hi - 1
    if hi < len(items):
        report[f"{key}_next"] = hi
    return report


def transcript_checks(path: Path | str, clip_id: str | None = None) -> dict[str, Any]:
    """Re-run the attach-time transcript checks over what is already attached.

    The four findings `attach_transcript` returns are computed once, at
    attach, and handed back in that call's result — so a project attached
    before a check existed can never see it. That is not hypothetical: the
    Scream VO was attached long before `overlaps` or `repeats` existed, and
    both findings were invisible to the project holding it — `repeats` would
    have named the retakes directly (HISTORY.md § The VO the project was
    holding). Re-attaching to surface a finding would mean re-running ASR or
    hunting down the original whisper JSON, so the checks are addressable on
    their own.

    Reads only — nothing here writes to the project, which is what makes it
    safe to run over a finished cut.
    """
    project = Project.open(path)
    if clip_id is not None:
        wanted = [clip_id]
    else:
        wanted = [
            c["clip_id"]
            for c in project.read_manifest().get("clips", [])
            if project.transcript_path(c["clip_id"]).exists()
        ]

    clips = []
    for cid in wanted:
        parsed = _transcript(project, cid)
        clips.append(
            {
                "clip_id": cid,
                "words": len(parsed),
                "near_duplicates": _near_duplicates(parsed),
                "suspect_durations": _suspect_durations(parsed),
                "overlaps": _overlaps(parsed),
                "repeats": _repeats(parsed),
            }
        )
    return {"clips": clips}


# -- speaker attribution ----------------------------------------------------
#
# Step 3 of PLAN.md § The co-hosted recording. **The speaker is an attribute
# of a word, never a second address**: `(clip_id, word_index)` still resolves
# every cue, description, unspoken mark, music anchor and caption, so a
# two-mic recording adds a per-word fact and moves nothing. There is no second
# transcript per clip and no new `Edit` primitive — the mics are one
# performance, cut together.
#
# It reads the registered container rather than `media_path()`, which is the
# one place in proofcut that is right: import derives a mixdown and every other
# resolver prefers it, because the untouched original of a two-mic container
# *is* the mixdown. The mics themselves are only in the container.

#: Consecutive ambiguous runs reported before the list is cut off. The count
#: is always reported in full beside it — a truncated list that does not say
#: it was truncated reads as the whole finding.
AMBIGUOUS_SPANS = 20


def _ambiguous_spans(
    parsed: tx.Transcript, decisions: Sequence[spk.Decision], limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Runs of consecutive undecided words, in word order, worst margin named.

    Runs rather than words because that is what a person goes and listens to:
    a turn change is a stretch, and 30 separate word indices in a list is the
    same finding with the shape taken off it. Each carries the three words
    either side, the standing rule for anything that reports a word index.
    """
    runs: list[list[int]] = []
    for index, decision in enumerate(decisions):
        if decision.label is not None:
            continue
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])

    out = []
    for run in runs[:limit]:
        first, last = run[0], run[-1]
        margins = [decisions[i].margin_db for i in run if decisions[i].margin_db is not None]
        out.append(
            {
                "first_word": first,
                "last_word": last,
                "text": " ".join(w.text for w in parsed.window(first, last)),
                "words": len(run),
                "start": parsed.words[first].start,
                "end": parsed.words[last].end,
                "worst_margin_db": round(min(margins), 2) if margins else None,
                "why": decisions[first].why,
                **_context(parsed, first, last),
            }
        )
    return out, len(runs)


def attribute_speakers(
    path: Path | str,
    clip_id: str,
    *,
    streams: Sequence[int] | None = None,
    labels: Sequence[str] | None = None,
    margin_db: float = spk.MARGIN_DB,
    apply: bool = False,
    limit: int = AMBIGUOUS_SPANS,
) -> dict[str, Any]:
    """Label each word with the mic that was loudest while it was spoken.

    One pass over an existing transcript, never a second ASR run — transcribe
    once, from the mix or either mic, and attribute afterwards. Transcribing
    each mic separately is the obvious design and it is dead: half of each
    mic's own transcript is the *other* person at every isolation measured
    (`speakers.py` carries that finding and the one about envelope detectors).

    **It reports; it does not decide below the floor.** `apply` is off by
    default, `reframe_detect`'s precedent rather than `cut --plan`'s, and for
    the same reason: the rule is 98.9% correct per word on clear speech and at
    **chance** on words spoken over each other, which is the half of a
    co-hosted recording that matters. `margin_db` is what half-knows the
    difference — a word whose loudest mic does not lead by that much is left
    unlabelled and reported as an ambiguous span to go and listen to.

    Applying **keeps a label it cannot replace**: where this refuses to call a
    word, whatever label the transcript already had stays. Attribution is a
    derivation and re-running it with a different floor should move, but a
    word someone attributed by hand is not information this can recreate, so
    it is never cleared by a refusal — the same shape as a stale unspoken
    mark being kept rather than applied.
    """
    project = Project.open(path)
    clip = _clips_by_id(project).get(clip_id)
    if clip is None:
        raise ProjectError(f"no clip {clip_id!r} in this project")
    parsed = _transcript(project, clip_id)

    container = media.container_path(project, clip)
    if not container.exists():
        raise ProjectError(f"{clip_id}'s media is not where the project says it is: {container}")
    # The recorded count when the clip has one, so an ordinary clip costs no
    # probe; a record written before `audio_streams` existed gets asked.
    available = int(clip.get("audio_streams") or media.probe(container).audio_streams)
    # A stream nothing can decode is no mic (`MediaInfo.undecodable_audio`):
    # an iPhone's Spatial Audio track would otherwise be offered as speaker 2.
    unreadable = {u["stream"] for u in clip.get("undecodable_audio") or ()}
    if available - len(unreadable) < 2:
        raise ProjectError(
            f"{clip_id} was recorded on one audio stream, and attribution compares mics "
            "against each other. There is no local route to speaker identity on a mixed "
            "track — see PLAN.md § The co-hosted recording"
        )

    wanted = [k for k in range(available) if k not in unreadable] if streams is None else [int(s) for s in streams]
    if len(wanted) != len(set(wanted)):
        raise ProjectError(f"the same audio stream is named twice: {wanted}")
    for stream in wanted:
        if not 0 <= stream < available:
            raise ProjectError(
                f"{clip_id} has {available} audio streams, numbered 0-{available - 1}; "
                f"asked for {stream}"
            )
        if stream in unreadable:
            raise ProjectError(f"{clip_id}'s audio stream {stream} is one ffmpeg cannot decode")

    named = [f"speaker{k + 1}" for k in range(len(wanted))] if labels is None else list(labels)
    if len(named) != len(wanted):
        raise ProjectError(
            f"{len(named)} label(s) for {len(wanted)} mic(s) — every mic gets exactly one, "
            "in the order the streams were named"
        )
    try:
        named = spk.check_labels(named)
        with tempfile.TemporaryDirectory(prefix="proofcut-mics-") as scratch:
            mics = []
            for stream, label in zip(wanted, named, strict=True):
                decoded = Path(scratch) / f"{clip_id}-a{stream}.wav"
                media.decode_stream_wav(container, decoded, stream=stream)
                mics.append(spk.load_mic(decoded, label))
            decisions = spk.attribute(
                [(w.start, w.end) for w in parsed.words], mics, margin_db=margin_db
            )
    except spk.SpeakerError as exc:
        raise ProjectError(str(exc)) from exc

    spans, total_spans = _ambiguous_spans(parsed, decisions, limit)
    report: dict[str, Any] = {
        "clip_id": clip_id,
        "container": str(container),
        "audio_streams": available,
        "streams": wanted,
        "labels": named,
        "margin_db": margin_db,
        **spk.summarise(decisions),
        "ambiguous_spans": spans,
        "ambiguous_spans_total": total_spans,
        "applied": bool(apply),
    }

    if apply:
        relabelled = [
            replace(word, speaker=decision.label if decision.label is not None else word.speaker)
            for word, decision in zip(parsed.words, decisions, strict=True)
        ]
        report["changed"] = sum(
            1 for before, after in zip(parsed.words, relabelled, strict=True)
            if before.speaker != after.speaker
        )  # fmt: skip
        report["kept"] = sum(
            1
            for word, decision in zip(parsed.words, decisions, strict=True)
            if decision.label is None and word.speaker is not None
        )
        tx.save(replace(parsed, words=tuple(relabelled)), project.transcript_path(clip_id))
        report["transcript"] = str(project.transcript_path(clip_id))

    return report


# -- footage descriptions ---------------------------------------------------
#
# Step 1 of PLAN.md § B-roll by description. **A description indexes the
# source, which is why an edit cannot invalidate it**: the unit is
# `(clip_id, src_start, src_end, text)` in *source* seconds, and the b-roll
# asset is not the thing being cut, so its own times never renumber. That is
# the same property word indices have, and the reason nothing here needs a
# re-describe hook on edit.
#
# They live in the manifest rather than a sidecar directory or `cache/`: they
# are per-clip metadata `info` should report, they have no natural filename,
# and they cost GPU minutes, which is not what `cache/` is for.


def _descriptions(project: Project) -> list[dict[str, Any]]:
    return list(project.read_manifest().get("descriptions", []))


def _describable(project: Project, clip_id: str | None) -> list[dict[str, Any]]:
    """The clips `describe` can look at, refusing an audio-only one by name.

    Naming it matters: the Scream project's VO is a `.wav`, and "describe the
    project" quietly skipping it reads the same as describing it and finding
    nothing worth saying.
    """
    if clip_id is not None:
        clip = media.get_clip(project, clip_id)
        if not clip.get("has_video"):
            raise ProjectError(
                f"clip {clip_id!r} has no video track, so there is nothing to "
                "describe — descriptions index pictures, not dialogue. Its "
                "words are what `transcribe` indexes."
            )
        return [clip]
    clips = project.read_manifest().get("clips", [])
    return [c for c in clips if c.get("has_video")]


def describe(
    path: Path | str,
    clip_id: str | None = None,
    *,
    window: float = dsc.WINDOW,
    force: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Describe a clip's footage — or every video clip's — in fixed windows.

    This is a **job, not a request**: cost is per window at roughly three
    seconds each, so a project's footage is minutes of GPU time. `plan=True`
    resolves the whole work list and the estimate without loading a model,
    which is the only way to ask "what would this cost" without paying it.

    Already-described clips are skipped unless `force`, so re-running after
    importing one new clip describes one clip. `force` re-describes and
    replaces, since a description is derived and there is nothing in it to
    lose.

    The windows are fixed and are never widened to save time — a whole-clip
    pass invents people (`describe`'s module docstring). The two error
    classes the measurement left standing ride along on every result rather
    than being smoothed over: `errors` names windows the model could not
    describe, and `truncated` names ones whose text stops mid-sentence.
    """
    project = Project.open(path)
    clips = _describable(project, clip_id)
    existing = _descriptions(project)
    already = {d["clip_id"] for d in existing}

    todo: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    for clip in clips:
        if clip["clip_id"] in already and not force:
            skipped.append(
                {
                    "clip_id": clip["clip_id"],
                    "why": "already described — pass force to describe it again",
                    "windows": sum(1 for d in existing if d["clip_id"] == clip["clip_id"]),
                }
            )
            continue
        source = media.media_path(project, clip)
        spans = dsc.plan_windows(float(clip["duration"]), window=window)
        for start, end in spans:
            windows.append(
                {
                    "index": len(windows),
                    "clip_id": clip["clip_id"],
                    "media": str(source),
                    "src_start": start,
                    "src_end": end,
                    "timestamps": dsc.frame_times(start, end),
                }
            )
        todo.append({"clip_id": clip["clip_id"], "windows": len(spans)})

    report: dict[str, Any] = {
        "project": str(project.root),
        "window": window,
        "clips": todo,
        "skipped": skipped,
        "windows": len(windows),
        # 3.5s per window, near enough constant regardless of how much footage
        # the window spans, plus the model load the run pays once. Measured on
        # the whole Scream project at 1920x816 rather than taken from the
        # note's per-clip spike, which saw 2.6-3.3s on smaller frames.
        #
        # The load is in here because leaving it out makes the estimate wrong
        # by 3x on exactly the small runs someone checks it against: three
        # windows is 10s of describing and 25s of waiting.
        "estimated_seconds": round(len(windows) * 3.5 + 15) if windows else 0,
    }
    if plan:
        report["plan"] = True
        report["runtime"] = dsc.available()
        return report
    if not windows:
        report["described"] = 0
        report["errors"] = []
        report["truncated"] = []
        return report

    started = time.monotonic()
    # The worker takes the whole list at once and loads the model once for
    # it — ~15s of loading against ~3s per window, so a process per clip
    # would spend most of the run loading the same weights again.
    results = dsc.describe_windows(
        [{"index": w["index"], "media": w["media"], "timestamps": w["timestamps"]} for w in windows]
    )

    stored: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    truncations: list[dict[str, Any]] = []
    for spec, result in zip(windows, results, strict=True):
        where = {
            "clip_id": spec["clip_id"],
            "src_start": round(spec["src_start"], 3),
            "src_end": round(spec["src_end"], 3),
        }
        if "error" in result:
            errors.append({**where, "error": result["error"]})
            continue
        text = result["text"].strip()
        entry = {
            **where,
            "text": text,
            "truncated": dsc.truncated(text),
            "origin": f"qwen2.5-vl:{window:g}s/{dsc.FRAMES_PER_WINDOW}f",
        }
        stored.append(entry)
        if entry["truncated"]:
            truncations.append(where)

    redescribed = {w["clip_id"] for w in windows}
    manifest = project.read_manifest()
    kept = [d for d in manifest.get("descriptions", []) if d["clip_id"] not in redescribed]
    manifest["descriptions"] = sorted(
        kept + stored, key=lambda d: (d["clip_id"], d["src_start"])
    )
    project.write_manifest(manifest)

    report["described"] = len(stored)
    report["errors"] = errors
    report["truncated"] = truncations
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def describe_ls(
    path: Path | str,
    clip_id: str | None = None,
    *,
    contains: str | None = None,
) -> dict[str, Any]:
    """List the footage descriptions — the read half of b-roll by description.

    Read-only, and this *is* the search: no ranking, no embeddings, no
    similarity threshold. The descriptions are text, and whoever is looking
    reads them and picks. That holds up to a measured ceiling of roughly 600
    windows, past which reading them in one go stops being reasonable and a
    cross-project library would be the trigger to revisit (PLAN.md § B-roll by
    description). `words` reports the size of what came back so that cost is
    visible rather than guessed at.

    `contains` is a convenience on top, not a subsystem: whitespace-separated
    terms matched case-insensitively, and **every** term must appear somewhere
    in a description for it to match — so `"kitchen knife"` finds "a knife on
    the kitchen counter". The terms it split into come back under `filter`,
    the same reason a word-indexed tool echoes what it landed on.

    Ordered by `(clip_id, src_start)`: source order, which is the order the
    footage runs in and which no edit can renumber. `clips` covers **every**
    video clip in the project, described or not, so a zero there reads as "not
    described yet" rather than "no such clip".
    """
    project = Project.open(path)
    stored = _descriptions(project)
    total = len(stored)

    terms = (contains or "").split()
    entries = [
        d
        for d in stored
        if (clip_id is None or d["clip_id"] == clip_id)
        and all(term.lower() in d["text"].lower() for term in terms)
    ]
    entries.sort(key=lambda d: (d["clip_id"], d["src_start"]))

    # Every video clip, not just the described ones: "clipa: 0" is the answer
    # to "is this footage indexed", and leaving it out makes an undescribed
    # clip indistinguishable from a clip_id that does not exist.
    described: dict[str, list[dict[str, Any]]] = {}
    for d in stored:
        described.setdefault(d["clip_id"], []).append(d)
    clips = []
    for clip in _describable(project, None):
        cid = clip["clip_id"]
        mine = described.get(cid, [])
        clips.append(
            {
                "clip_id": cid,
                "windows": len(mine),
                "described_seconds": round(sum(d["src_end"] - d["src_start"] for d in mine), 3),
                # Rounded to match `described_seconds`, because the pair is
                # read as a coverage check and 14.013 against 14.013292 looks
                # like a shortfall that is not there.
                "duration": round(float(clip["duration"]), 3),
                # Truncated entries read exactly like complete ones to whoever
                # searches them, so the count rides along here too.
                "truncated": sum(1 for d in mine if d.get("truncated")),
            }
        )

    return {
        "descriptions": entries,
        "count": len(entries),
        # What a filter matched *out of*: three hits with no total reads the
        # same as a project with three descriptions in it.
        "total": total,
        "words": sum(len(d["text"].split()) for d in entries),
        "clips": clips,
        "filter": {"clip_id": clip_id, "contains": contains, "terms": terms},
    }


#: What a clip *is*, in one sentence of prose — the corpus a picker needs, and
#: a different kind of fact from a `describe` window. A description says what
#: is in front of the camera; a synopsis says what the footage *is*, which for
#: found footage means naming the work, the scene and the people. Absent means
#: nobody has said, which is what every project written before this key existed
#: meant, so it is additive the way `CANVAS_KEY` is and takes no
#: `SCHEMA_VERSION` bump. HISTORY.md § Choosing the b-roll.
SYNOPSIS_KEY = "synopsis"

#: Prose for a reader who already knows the material, not a search field. The
#: cap keeps it from quietly becoming a second transcript: every clip's
#: synopsis has to fit in one prompt *beside* the whole narration, and the
#: measurement that chose this mechanism used lines of about this length.
SYNOPSIS_MAX = 800


def synopsis(
    path: Path | str,
    clip_id: str | None = None,
    text: str | None = None,
    *,
    clear: bool = False,
) -> dict[str, Any]:
    """Read, set or clear a clip's one-line synopsis.

    Read/write/clear on one entry point, the shape `canvas` already uses:
    no `clip_id` lists every clip's synopsis, `clip_id` alone reads one,
    `text` writes, `clear` removes. Listing is the common call — a picker
    wants the whole catalogue, never one line.

    **This is the field that decides which clip goes under a sentence, and
    `descriptions` is not.** Measured on the Scream footage against 25 human
    choices: the vision index agreed 2 times, the clips' own filenames 3, and
    a synopsis catalogue read by a model that knows the films, 13. The reason
    is not that the descriptions were bad — they are accurate — it is that the
    connection is never lexical. "Every one of those is further outside the
    film than the one before it" belongs over the Scream VI reveal because its
    killers are a family avenging someone from the last movie, and no
    description of those pixels contains any word of that sentence. So a
    synopsis is *allowed and expected* to carry what a camera cannot see:
    who wrote it, what the twist means, which entry in the series it is.
    HISTORY.md § Choosing the b-roll.

    Nothing generates these. A VLM cannot — that is the finding — and proofcut
    will not guess a title from a filename, because a wrong synopsis is worse
    than an absent one: it produces confident, plausible, wrong placements
    rather than an empty catalogue somebody notices. `broll_brief` reports
    which clips are missing one instead.
    """
    project = Project.open(path)
    manifest = project.read_manifest()
    clips = manifest.get("clips", [])

    if clip_id is None:
        if text is not None or clear:
            raise ProjectError("naming a clip_id is what says which synopsis to write")
        return {
            "clips": [
                {"clip_id": c["clip_id"], "synopsis": c.get(SYNOPSIS_KEY)} for c in clips
            ],
            "missing": [c["clip_id"] for c in clips if not c.get(SYNOPSIS_KEY)],
            "count": len(clips),
        }

    record = media.get_clip(project, clip_id)
    if text is not None and clear:
        raise ProjectError("pass text to write a synopsis or clear to remove it, not both")

    written = False
    if clear:
        record.pop(SYNOPSIS_KEY, None)
        written = True
    elif text is not None:
        text = " ".join(str(text).split())
        if not text:
            raise ProjectError(
                "an empty synopsis is not the same as no synopsis — pass clear to remove one"
            )
        if len(text) > SYNOPSIS_MAX:
            raise ProjectError(
                f"synopsis is {len(text)} characters, over the {SYNOPSIS_MAX} cap — every "
                "clip's has to fit in one prompt beside the narration, so this is a "
                "sentence or three about what the footage is, not a summary of the work"
            )
        record[SYNOPSIS_KEY] = text
        written = True

    if written:
        for index, existing in enumerate(clips):
            if existing["clip_id"] == clip_id:
                clips[index] = record
                break
        project.write_manifest(manifest)
    return {
        "clip_id": clip_id,
        "synopsis": record.get(SYNOPSIS_KEY),
        "written": written,
        "cleared": bool(clear),
    }


# -- cards -----------------------------------------------------------------
#
# Step 1 of PLAN.md § Motion graphics and templates: the asset a `card:` cue
# resolves to, generated rather than drawn elsewhere and copied in. SVG is the
# source and PNG the rasterisation, both kept — the cue table and the preview
# `<img>` want a raster, and a card you cannot re-edit is a card you redraw
# from scratch to change a year.
#
# Step 2 of PLAN.md § Aspect swap added the *record*: the two files on disk
# have the canvas baked into them (the SVG's viewBox, the PNG's pixels), so
# the shape of a card is not derivable from the card. `cards` in the manifest
# is what it was made from, and `card_reauthor` is what turns that back into
# the two files at whatever shape the project is now. This one is a schema
# bump where `assets/cards/` was not, for the reason CLAUDE.md gives: it is a
# list another op would `setdefault`, so the version number is what makes it
# true rather than incidentally survivable.

#: Card records in the manifest, one per card `card_new` has made:
#: `{"card", "template", "slots", "canvas"}` — geometry and content, never a
#: length, so nothing here is in tension with PLAN.md § The property
#: everything below defends.
CARDS_KEY = "cards"


def _card_records(project: Project) -> list[dict[str, Any]]:
    return list(project.read_manifest().get(CARDS_KEY, []))


def _card_record(project: Project, name: str) -> dict[str, Any] | None:
    for record in _card_records(project):
        if record.get("card") == name:
            return record
    return None


def _write_card_record(project: Project, record: dict[str, Any]) -> None:
    """Store what a card was made from, replacing any record of that name.

    Replaces rather than appends because a card name is the key a cue points
    at: two records for one name would make "what is this card" a question
    with two answers, and `card_reauthor` would draw whichever came first.
    """
    manifest = project.read_manifest()
    records = [r for r in manifest.setdefault(CARDS_KEY, []) if r.get("card") != record["card"]]
    records.append(record)
    manifest[CARDS_KEY] = records
    project.write_manifest(manifest)


def _cards_on_disk(project: Project) -> list[str]:
    """Every card name with a file under `assets/cards/`, SVG or PNG.

    Both extensions, because the two are separately sufficient to make a card
    real: an SVG with no PNG is a card no cue can resolve yet, and a PNG with
    no SVG is a card made outside proofcut — which is what the Scream project
    holds, and the reason `card_new`'s guard cannot look at the SVG alone.
    """
    if not project.cards_dir.is_dir():
        return []
    return sorted({p.stem for p in project.cards_dir.iterdir() if p.suffix in (".svg", ".png")})


def card_templates(name: str | None = None) -> dict[str, Any]:
    """Every card template proofcut ships, with the slots each one takes.

    Takes no project: a template is package data, the same for every one.
    `name` returns that one template's full entry and every other one's name
    and description only — the whole table is 12.6 KB, and an agent that has
    chosen a template reads one slot list.
    """
    templates = graphics.templates()
    if name is None:
        return {"templates": templates}
    if name not in {t["template"] for t in templates}:
        raise ProjectError(
            f"no card template {name!r}; there are "
            f"{', '.join(t['template'] for t in templates)}"
        )
    return {
        "templates": [
            t if t["template"] == name else {"template": t["template"], "description": t["description"]}
            for t in templates
        ]
    }


def fonts(path: Path | str | None = None, *, install: bool = False) -> dict[str, Any]:
    """Will the caption font actually draw here — and is it here on purpose?

    Two questions that look like one, reported side by side and **never folded
    into each other**. `font_match` asks fontconfig, which answers "is this
    family present". `fonts.probe` asks the renderer, which answers "did it
    draw". This repo has measured them disagreeing more than once: two styles
    `fc-match` calls identical render 3593 RMSE apart, because libass's first
    pick for `Noto Sans` on this box is a Nerd Font symbol face that only
    reaches the real one by failing a Latin glyph (HISTORY.md § The approvals
    round, answered). A clean `resolves_to` is not a claim about the burn, so
    neither result is allowed to stand in for the other here.

    The probe is a **render comparison**, not a lookup: it burns the family and
    a family that cannot exist, and compares the pixels. Identical means the
    name is not drawing, whatever fontconfig says. That costs two ffmpeg runs
    and a `magick compare`, which is why nothing on a hot path calls it —
    `status`, `info` and `caption-view` all stay on `font_match` alone, and
    this op is where someone asks the expensive question deliberately.

    `path` is optional because a font is not project state, but a project's
    `caption_style` may *name* one — so given a project this reports the font
    that project would actually burn, and given none it reports proofcut's own
    default. `card_templates` is the precedent for the no-project half.

    **`install` is off by default**, like `reframe_detect`'s `apply` and for
    the same reason: it writes into `$HOME`. Vendoring the face is what makes
    the default resolve on a machine rather than aspirationally
    (`src/proofcut/fonts/FONTS.md`), and it is a side effect somebody should ask
    for rather than one a report performs on the way past.
    """
    checked: list[str] = []
    default = captions.CAPTION_FONT
    project_font: str | None = None
    if path is not None:
        project = Project.open(path)
        stored = project.read_manifest().get(CAPTION_STYLE_KEY) or {}
        project_font = str(stored.get("font") or default)
        checked.append(project_font)
    if default not in checked:
        checked.append(default)

    report: dict[str, Any] = {
        "project": str(Project.open(path).root) if path is not None else None,
        "caption_font": project_font or default,
        "default_font": default,
        "vendored": [p.name for p in proofcut_fonts.vendored()],
        "font_dir": str(proofcut_fonts.user_font_dir()),
    }
    if install:
        report["install"] = proofcut_fonts.install()

    seen: dict[str, dict[str, Any]] = {}
    for family in checked:
        if family in seen:
            continue
        entry: dict[str, Any] = {"fontconfig": captions.font_match(family)}
        try:
            entry["render"] = proofcut_fonts.probe(family)
        except proofcut_fonts.FontError as exc:
            # "could not tell" and "it does not draw" are different answers,
            # the same distinction `font_match` makes with a null `available`.
            entry["render"] = {"font": family, "drew": None, "error": str(exc)}
        seen[family] = entry
    report["fonts"] = seen
    return report


# -- channel preset pack -----------------------------------------------------
#
# PLAN.md § The completion queue, item 8. A pack is one external JSON file
# (`pack.load_pack`, pure — no project, no write), applied once: every
# variant it declares is resolved and *snapshotted* into the manifest, hashed
# on its own resolved content. Every op below reads that snapshot, never the
# file again, which is the whole design — no op ever depends on an external
# path staying reachable or unchanged after the moment it was applied.
#
# Additive and optional, the `CANVAS_KEY`/`CAPTION_STYLE_KEY`/`TAIL_KEY`
# precedent exactly: an older manifest with no `pack` key means what it
# always meant, nothing applied, so this is not a `SCHEMA_VERSION` bump.
PACK_KEY = "pack"


def _stored_pack(project: Project) -> dict[str, Any] | None:
    stored = project.read_manifest().get(PACK_KEY)
    if stored is None:
        return None
    if not isinstance(stored, dict):
        raise ProjectError(f"{project.manifest_path}'s {PACK_KEY!r} must be a JSON object")
    return stored


def _pack_variant(
    project: Project, stored: dict[str, Any], variant: str | None
) -> tuple[str, dict[str, Any]]:
    name = variant or str(stored.get("active_variant"))
    variants = stored.get("variants", {})
    if name not in variants:
        raise ProjectError(
            f"pack has no variant {name!r} (has: {sorted(variants)}) — re-run "
            "pack_apply to add it, it is not read from the file again here"
        )
    return name, variants[name]


def _active_pack_style(project: Project) -> tuple[dict[str, Any], str | None]:
    """The active pack variant's palette+fonts+weights, and its hash.

    `({}, None)` with no pack applied, which is what makes `card_new`'s
    pre-merge a no-op for a project that has never touched this — the same
    "additive, no behaviour change" discipline every optional key here keeps.
    """
    stored = _stored_pack(project)
    if stored is None:
        return {}, None
    payload = stored.get("variants", {}).get(stored.get("active_variant"))
    if payload is None:
        return {}, None
    style = {
        **payload.get("palette", {}),
        **payload.get("fonts", {}),
        **payload.get("weights", {}),
    }
    return style, payload.get("hash")


def _primary_family(stack: str) -> str:
    """The first font-family in a CSS stack, unquoted — what `fonts.probe` asks about."""
    return stack.split(",", 1)[0].strip().strip("'\"")


def _family_vendored(family: str, *directories: Path | None) -> bool:
    """Is `family` (loosely) one of the faces at any of `directories`?

    A filename-contains check rather than a read of the font's own `name`
    table: proofcut's vendored `Outfit[wght].ttf` does not share a byte-for-byte
    name with the family `Outfit`, and this only ever gates a *provenance
    label* — never a refusal — so an exact parse is not worth a second
    subprocess per font role.
    """
    needle = family.replace(" ", "").lower()
    for directory in directories:
        if directory is None:
            continue
        for face in proofcut_fonts.vendored(directory):
            if needle in face.stem.replace(" ", "").replace("-", "").lower():
                return True
    return False


def pack_apply(
    path: Path | str,
    pack_path: Path | str,
    *,
    variant: str = "default",
    allow_fallback: bool = False,
    install_fonts: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Load `pack_path`, resolve and hash every variant it declares, activate one.

    **Every declared variant is snapshotted, not only the one activated** —
    `pack_activate` switches between them later with no file re-read, which
    is only possible if every one was already resolved here. Nothing is
    written to `caption_style`; a pack's caption presets are separate state
    a project opts into with `pack_apply_captions`, so a later pack swap can
    never silently overwrite a hand-tuned style underneath a project.
    Nothing under `assets/cards/` is touched either — a card picks up new
    defaults only when `card_new` or `card_reauthor` next draws it.

    For every font role across every variant, `fonts.probe` asks whether the
    declared family actually draws *on this box* — not whether fontconfig
    merely claims to have it (`captions.font_match`'s own limits, CLAUDE.md).
    A family that does not draw refuses the whole call, unless
    `allow_fallback` — which uses the declared CSS stack's own fallback
    instead and **records that it did** (`font_fallback_used`), never
    silently. A family that does draw but is in neither proofcut's own vendored
    set nor a font directory shipped beside the pack file gets
    `font_provenance: "unvendored"` on the record permanently — not refused,
    because the render on *this* box is genuinely correct today, but the risk
    (a second machine substituting silently) is recorded rather than lost.

    `install_fonts` vendors a pack's own font directory (a `fonts/` folder
    beside the pack JSON, if it ships one) the same idempotent,
    content-hashed way `proofcut fonts --install` vendors proofcut's own —
    `fonts.install(source=...)`. Off by default, like that flag: it writes
    into `$HOME`, a side effect worth asking for rather than one a report
    performs on the way past.

    `plan` resolves, probes and reports without writing.
    """
    project = Project.open(path)
    loaded = pk.load_pack(pack_path)
    if variant not in loaded["variants"]:
        raise ProjectError(
            f"{pack_path} has no variant {variant!r} (has: {sorted(loaded['variants'])})"
        )

    resolved_pack_path = Path(pack_path).expanduser().resolve()
    fonts_source = resolved_pack_path.parent / "fonts"
    pack_fonts_dir = fonts_source if fonts_source.is_dir() else None

    probed: dict[str, dict[str, Any]] = {}

    def probe(family: str) -> dict[str, Any]:
        if family not in probed:
            try:
                probed[family] = proofcut_fonts.probe(family)
            except proofcut_fonts.FontError as exc:
                probed[family] = {"font": family, "drew": None, "error": str(exc)}
        return probed[family]

    snapshot: dict[str, Any] = {}
    font_report: dict[str, Any] = {}
    for vname, payload in loaded["variants"].items():
        record = dict(payload)
        record["hash"] = pk.pack_hash(payload)
        provenance: dict[str, str] = {}
        fallback_used: dict[str, str] = {}
        for role, stack in payload["fonts"].items():
            family = _primary_family(stack)
            result = probe(family)
            font_report[f"{vname}.{role}"] = result
            if result.get("drew") is False:
                if not allow_fallback:
                    raise ProjectError(
                        f"pack variant {vname!r}'s {role!r} names {family!r}, which "
                        f"does not draw on this machine ({result.get('warning', 'it substitutes')})"
                        " — pass allow_fallback to use its declared fallback stack "
                        "instead, which is then recorded rather than silent"
                    )
                fallback_used[role] = stack
            elif result.get("drew") is True and not _family_vendored(
                family, proofcut_fonts.VENDORED_DIR, pack_fonts_dir
            ):
                provenance[role] = "unvendored"
        if fallback_used:
            record["font_fallback_used"] = fallback_used
        if provenance:
            record["font_provenance"] = provenance
        snapshot[vname] = record

    installed = None
    if install_fonts and pack_fonts_dir is not None:
        installed = proofcut_fonts.install(source=pack_fonts_dir)

    write = not plan
    if write:
        manifest = project.read_manifest()
        previous = manifest.get(PACK_KEY) or {}
        manifest[PACK_KEY] = {
            "name": loaded["name"],
            "source": str(resolved_pack_path),
            "active_variant": variant,
            "variants": snapshot,
            "caption_preset_applied": previous.get("caption_preset_applied"),
        }
        project.write_manifest(manifest)

    return {
        "project": str(project.root),
        "pack": loaded["name"],
        "source": str(resolved_pack_path),
        "variants": sorted(snapshot),
        "active_variant": variant,
        "fonts": font_report,
        "fonts_installed": installed,
        "written": write,
        "plan": bool(plan),
    }


def pack_activate(path: Path | str, variant: str, *, plan: bool = False) -> dict[str, Any]:
    """Switch the active variant to one already snapshotted by `pack_apply`.

    No file re-read — refuses an unknown variant by name rather than
    guessing, and the message says to re-run `pack_apply` rather than trying
    to load one here, because this op never touches the file.
    """
    project = Project.open(path)
    stored = _stored_pack(project)
    if stored is None:
        raise ProjectError("no pack applied yet — run pack_apply first")
    variants = stored.get("variants", {})
    if variant not in variants:
        raise ProjectError(
            f"pack has no variant {variant!r} (has: {sorted(variants)}) — re-run "
            "pack_apply to add it, it is not read from the file again here"
        )
    was = stored.get("active_variant")
    write = not plan
    if write:
        manifest = project.read_manifest()
        manifest[PACK_KEY]["active_variant"] = variant
        project.write_manifest(manifest)
    return {
        "project": str(project.root),
        "was": was,
        "active_variant": variant,
        "written": write,
        "plan": bool(plan),
    }


def pack_apply_captions(path: Path | str, preset: str, *, plan: bool = False) -> dict[str, Any]:
    """Apply the active pack variant's caption preset through `caption_style`.

    **Concrete resolved fields, never a live pointer** — this reads the
    preset's already-resolved dict off the snapshot and hands it to the
    ordinary `caption_style(**overrides)` call, so `captions.py` stays
    untouched and a later pack swap can never silently overwrite a project's
    caption look out from under it. Separate from `pack_apply` on purpose:
    applying a pack never restyles captions on its own.
    """
    project = Project.open(path)
    stored = _stored_pack(project)
    if stored is None:
        raise ProjectError("no pack applied yet — run pack_apply first")
    active, payload = _pack_variant(project, stored, None)
    presets = payload.get("caption_presets", {})
    if preset not in presets:
        raise ProjectError(
            f"pack variant {active!r} has no caption preset {preset!r} "
            f"(has: {sorted(presets)})"
        )
    overrides = presets[preset]
    result = caption_style(path, plan=plan, **overrides)
    if not plan:
        manifest = project.read_manifest()
        manifest[PACK_KEY]["caption_preset_applied"] = {
            "preset": preset,
            "variant": active,
            "hash": payload.get("hash"),
        }
        project.write_manifest(manifest)
    return {"pack_variant": active, "preset": preset, **result}


def pack_show(
    pack_path: Path | str | None = None,
    *,
    path: Path | str | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    """What a pack declares — from the file, from a project's snapshot, or both.

    `pack_path` alone reads and resolves the file fresh, `card_templates`'s
    no-project shape. `path` alone asks what a project actually has applied —
    its stored snapshot, never the file again, which is the point of
    snapshotting one. Both together is how to compare "what the file says
    now" against "what the project is still running."
    """
    if pack_path is None and path is None:
        raise ProjectError("pack_show needs a pack_path, a project path, or both")
    result: dict[str, Any] = {}
    if pack_path is not None:
        loaded = pk.load_pack(pack_path)
        result["file"] = {
            "source": str(Path(pack_path).expanduser().resolve()),
            "name": loaded["name"],
            "variants": {
                vname: {**payload, "hash": pk.pack_hash(payload)}
                for vname, payload in loaded["variants"].items()
            },
        }
    if path is not None:
        project = Project.open(path)
        stored = _stored_pack(project)
        if stored is None:
            result["project"] = {"applied": False}
        else:
            vname, payload = _pack_variant(project, stored, variant)
            result["project"] = {
                "applied": True,
                "name": stored.get("name"),
                "source": stored.get("source"),
                "active_variant": stored.get("active_variant"),
                "variants": sorted(stored.get("variants", {})),
                "variant": vname,
                "resolved": payload,
                "caption_preset_applied": stored.get("caption_preset_applied"),
            }
    return result


def pack_status(path: Path | str) -> dict[str, Any]:
    """Active variant, which cards have drifted from it, and whether captions did.

    A card's own `pack_hash` (recorded by `card_new`/`card_reauthor`)
    compared against the active variant's *current* hash — **stale, not
    wrong**: `card_new` only pre-merges a pack's style slots and a per-call
    slot still wins, so a stale card is not necessarily drawing incorrectly,
    only from a superseded snapshot. `card_reauthor` is how to catch it up.
    """
    project = Project.open(path)
    stored = _stored_pack(project)
    if stored is None:
        return {"project": str(project.root), "applied": False}
    active = stored.get("active_variant")
    variants = stored.get("variants", {})
    active_hash = (variants.get(active) or {}).get("hash")

    stale_cards = [
        {"card": record.get("card"), "recorded_hash": record.get("pack_hash")}
        for record in _card_records(project)
        if record.get("pack_hash") is not None and record.get("pack_hash") != active_hash
    ]

    applied = stored.get("caption_preset_applied")
    captions_stale = None
    if applied:
        # Stale if the pack moved *or* the project activated a different
        # variant since the burn — comparing only against the recorded
        # variant's own current hash (the old bug) never notices the second
        # case, because that variant's hash hasn't changed at all.
        active_payload = variants.get(active) or {}
        captions_stale = applied.get("variant") != active or applied.get(
            "hash"
        ) != active_payload.get("hash")

    return {
        "project": str(project.root),
        "applied": True,
        "name": stored.get("name"),
        "active_variant": active,
        "variants": sorted(variants),
        "stale_cards": stale_cards,
        "caption_preset_applied": applied,
        "caption_preset_stale": captions_stale,
    }


def card_safe_zones(path: Path | str, card: str, platform: str) -> dict[str, Any]:
    """Measure a rendered card's ink in and around `platform`'s reserved band.

    **Report only.** No default floor, no `--strict`, and this never blocks
    a render — `SCENE_THRESHOLD`'s own history is the reason (CLAUDE.md): a
    threshold gets pinned by looking at real output, not picked cold, and
    this check has had exactly one look so far. Refuses only on a named card
    with no rendered PNG (never guesses dimensions from the manifest) or a
    platform in neither `graphics.SAFE_ZONES` nor the active pack variant's
    own `safe_zones`. The reserved-band *definition* (`platform`) is always
    the pack's current active variant; the card's own *background* is
    resolved against whichever variant the card was actually drawn from
    (its recorded `pack_hash`) — a `pack_activate` since authoring must not
    change what a rendered card's own pixels are measured against.
    """
    project = Project.open(path)
    _card_name(card)
    png = project.cards_dir / f"{card}.png"
    if not png.is_file():
        raise ProjectError(
            f"no rendered PNG for card {card!r} at {png} — card_new or card_render it first"
        )

    zones = dict(graphics.SAFE_ZONES)
    stored = _stored_pack(project)
    active_payload: dict[str, Any] | None = None
    if stored is not None:
        active_payload = stored.get("variants", {}).get(stored.get("active_variant"))
        if active_payload:
            zones.update(active_payload.get("safe_zones", {}))
    if platform not in zones:
        raise ProjectError(f"no safe zone named {platform!r} (has: {sorted(zones)})")

    width, height = graphics.identify(png)
    record = _card_record(project, card)
    background = None
    if record is not None:
        bg_slot = graphics.TEMPLATE_BACKGROUND.get(str(record.get("template")))
        if bg_slot:
            slots = record.get("slots") or {}
            if bg_slot in slots:
                background = slots[bg_slot]
            else:
                # The variant the card was actually authored from — its own
                # recorded `pack_hash` — never the pack's *current* active
                # variant. A later `pack_activate` must not change what this
                # reports an already-drawn card's background as; the pixels
                # on disk did not move.
                authoring_payload = active_payload
                recorded_hash = record.get("pack_hash")
                if recorded_hash is not None and stored is not None:
                    authoring_payload = next(
                        (
                            payload
                            for payload in stored.get("variants", {}).values()
                            if payload.get("hash") == recorded_hash
                        ),
                        None,
                    )
                if authoring_payload:
                    background = authoring_payload.get("palette", {}).get(bg_slot)
            if background is None:
                background = graphics.PALETTE.get(bg_slot)
    if background is None:
        background = graphics.PALETTE["paper"]

    ink = graphics.safe_zone_ink(png, (width, height), zones[platform], str(background))
    return {
        "project": str(project.root),
        "card": card,
        "platform": platform,
        "zone": zones[platform],
        **ink,
    }


def card_new(
    path: Path | str,
    name: str,
    template: str,
    slots: dict[str, Any],
    *,
    width: int | None = None,
    height: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Fill `template`'s slots and land both files under `assets/cards/`.

    The SVG is written first and then rendered *from disk* by the same
    `card_render` an edit-and-re-render would use — not from the string in
    memory. One path, so a card made here and a card re-rendered later
    cannot diverge.

    **The canvas defaults to the project's own** — `_mlt_resolution`, the
    same number the MLT profile declares — which is step 3 of PLAN.md
    § Motion graphics and templates and what closes its finding 4. That
    finding measured a 1920x1080 card in the Scream cut's 1920x816 frame
    losing 465 px of width, 24%, to black bar. The fix is not to resize on
    the way in: `-size` fits rather than distorts, so a card authored at the
    wrong aspect pillarboxes whatever it is scaled to. It is to *author* at
    the canvas, which a template can do because its geometry is in
    1920-wide units and its viewBox is written to the aspect it is asked for.

    Refused if the card already exists, unless `overwrite`. A card is
    referenced by cues, and silently replacing the asset under one is the
    kind of edit nobody can see happen. **Either file is enough to exist** —
    a card made outside proofcut has a PNG and no SVG, and a guard that looked
    only at the source would overwrite the raster a cue resolves to without
    ever tripping.

    What it was made from is recorded in the manifest (`cards`), which is
    what lets `card_reauthor` draw it again at a different canvas. The record
    is written after both files land, so a template error leaves no record of
    a card that does not exist.

    **If a pack is applied, its active variant's style slots are pre-merged
    underneath `slots`** — a project's palette, fonts and mark/footnote
    weights, with a slot this call passes explicitly still winning. Only the
    caller's own `slots` are recorded, never the merge: `card_reauthor`
    re-does this pre-merge against whatever pack is active *then*, which is
    what lets a later pack swap reach a card that already exists rather than
    freezing today's colours into its record.
    """
    project = Project.open(path)
    _card_name(name)
    if (width is None) != (height is None):
        raise ProjectError(
            "card_new takes both width and height or neither — one alone "
            "would have to guess the other, and the guess would be a card "
            "that pillarboxes in the frame it was made for"
        )
    canvas_from = "project"
    if width is None or height is None:
        width, height = _mlt_resolution(project)
        canvas_from = "project"
    else:
        canvas_from = "requested"
    source = project.cards_dir / f"{name}.svg"
    existing = [p for p in (source, project.cards_dir / f"{name}.png") if p.exists()]
    if existing and not overwrite:
        raise ProjectError(
            f"a card named {name!r} already exists at "
            f"{', '.join(str(p) for p in existing)} — pass overwrite "
            "to replace it, remembering that any cue pointing at card:"
            f"{name} will show the new one"
        )

    # Which *file* the canvas picked, not just the canvas: a variant shipping
    # changes what a shape draws without changing the shape, and then
    # `card_reauthor`'s sweep has nothing to compare and reports the project
    # up to date. Additive and optional — absent means the record predates
    # variants, which is the same as none, so it is not a schema bump.
    variant = graphics.template_layout(template, width, height)["variant"]
    pack_style, pack_hash_value = _active_pack_style(project)
    svg = graphics.fill_template(template, {**pack_style, **dict(slots)}, width=width, height=height)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(svg, encoding="utf-8")
    rendered = card_render(path, name)
    _write_card_record(
        project,
        {
            "card": name,
            "template": template,
            "slots": dict(slots),
            "canvas": f"{width}x{height}",
            **({"variant": variant} if variant else {}),
            **({"pack_hash": pack_hash_value} if pack_hash_value else {}),
        },
    )
    return {
        "template": template,
        "canvas": f"{width}x{height}",
        "canvas_from": canvas_from,
        "variant": variant,
        "recorded": True,
        "pack_applied": bool(pack_hash_value),
        **rendered,
    }


def _card_name(name: str) -> str:
    if not name or "/" in name or name.startswith("."):
        raise ProjectError(
            f"card name {name!r} is not a card name — it is the `<name>` in "
            "`card:<name>`, so it names one file in assets/cards/, not a path"
        )
    return name


def card_render(
    path: Path | str,
    name: str,
    *,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Rasterise `assets/cards/<name>.svg` to the PNG its cue resolves to.

    The PNG is what `_resolve_asset` looks for, and it is written beside the
    source under exactly the name `card:<name>` resolves to — a card
    rasterised anywhere else is a card the cue table cannot find, and the
    error for that arrives at export.

    The font report rides along on every call because it is the only guard
    there is: a card naming a face this box lacks renders pixel-identically
    to one naming a face it has, at exit 0 (`graphics`' docstring). It
    reports and does not prevent, the same call `captions.font_match` made.
    """
    project = Project.open(path)
    _card_name(name)
    source = project.cards_dir / f"{name}.svg"
    if not source.is_file():
        existing = sorted(p.name for p in project.cards_dir.glob("*.svg"))
        raise ProjectError(
            f"no card source at {source} (cards with an SVG source: "
            f"{', '.join(existing) or 'none'})"
        )
    rendered = graphics.render_svg(
        source, project.cards_dir / f"{name}.png", width=width, height=height
    )
    return {"card": name, "asset": f"card:{name}", **rendered}


def card_reauthor(
    path: Path | str,
    name: str | None = None,
    *,
    plan: bool = False,
) -> dict[str, Any]:
    """Draw recorded cards again, at the shape the project renders at now.

    Step 2 of PLAN.md § Aspect swap, and the thing that gates step 3: a
    vertical render with the old 16:9 cards pillarboxed inside it is the
    failure the item exists to close, not a partial win. **Cards are the only
    project state that is rasterised rather than derived** — captions survive
    a canvas change because they come off `caption_style` every time, while a
    card has its canvas baked into the SVG's viewBox and the PNG's pixels.
    This is what makes one derivable after the fact: the record says what it
    was made from, and the card is authored again from that at
    `_mlt_resolution`, which is `card_new`'s own default and the number the
    MLT profile declares.

    **It re-authors rather than resizes**, for the reason `render_svg` has no
    resize path: `-size` *fits*, so rasterising a 16:9 document into a 9:16
    frame pillarboxes the card inside the frame rather than reflowing it.
    Only `fill_template` can put the geometry at a new aspect, and only the
    record can feed it.

    With no `name` this sweeps: every recorded card whose canvas is not the
    project's, whose *layout* is not the one that canvas now resolves to, plus
    any whose files have gone missing. Named, it redraws that one whatever its
    canvas — an explicit ask is not second-guessed.

    **The layout half is not redundant with the canvas half**, and the day a
    variant ships is when that shows. A portrait file appearing changes what
    1080x1920 draws without changing 1080x1920, so a canvas-only sweep answers
    `redrawn: 0` over twelve cards that are all still the old layout — the
    project reads as up to date and every card is wrong. So the record says
    which file it was drawn from and the sweep compares that too.

    **A card with no record is reported, never skipped quietly.** Nothing on
    disk can recover what a card was made from, so the honest output is its
    name and the fact that `card new --overwrite` is the way back — which is
    also how such a card gains a record. `plan` resolves and writes nothing.

    There is deliberately no size argument. A card authored at anything but
    the project canvas is finding 4 of the note all over again, and the knob
    for "render at a different shape" is `canvas`, one level up.
    """
    project = Project.open(path)
    width, height = _mlt_resolution(project)
    canvas_now = f"{width}x{height}"
    _, active_pack_hash = _active_pack_style(project)

    records = _card_records(project)
    known = {r.get("card") for r in records}
    unrecorded = [c for c in _cards_on_disk(project) if c not in known]

    if name is not None:
        _card_name(name)
        record = _card_record(project, name)
        if record is None:
            where = "it has files on disk but no record" if name in unrecorded else "no such card"
            raise ProjectError(
                f"nothing recorded for card {name!r} — {where}. A record says what a "
                "card was made from, and no file on disk carries that; make it again "
                f"with `card new {name} --template ... --overwrite`, which records it "
                "and leaves every later swap a single command"
            )
        records = [record]

    results = []
    for record in records:
        card = record.get("card")
        was = str(record.get("canvas") or "")
        variant_was = record.get("variant")
        variant_now = graphics.template_layout(str(record.get("template")), width, height)["variant"]
        svg = project.cards_dir / f"{card}.svg"
        png = project.cards_dir / f"{card}.png"
        missing = [p.name for p in (svg, png) if not p.is_file()]
        if name is not None:
            why = "asked for"
        elif missing:
            why = "missing " + " and ".join(missing)
        elif was != canvas_now:
            why = f"{was or 'unrecorded canvas'} -> {canvas_now}"
        elif variant_was != variant_now:
            why = f"layout {variant_was or 'base'} -> {variant_now or 'base'}"
        elif record.get("pack_hash") and record.get("pack_hash") != active_pack_hash:
            why = "the pack moved"
        else:
            why = ""
        entry: dict[str, Any] = {
            "card": card,
            "template": record.get("template"),
            "canvas_was": was or None,
            "canvas": canvas_now,
            "variant_was": variant_was,
            "variant": variant_now,
            "redrawn": bool(why) and not plan,
            "why": why or "already at the project canvas",
        }
        if why and not plan:
            drawn = card_new(
                project.root,
                str(card),
                str(record.get("template")),
                dict(record.get("slots") or {}),
                width=width,
                height=height,
                overwrite=True,
            )
            entry["asset"] = drawn["asset"]
            entry["width"] = drawn["width"]
            entry["height"] = drawn["height"]
            entry["font_warnings"] = drawn["font_warnings"]
        results.append(entry)

    return {
        "project": str(project.root),
        "canvas": canvas_now,
        "cards": results,
        "redrawn": sum(1 for e in results if e["redrawn"]),
        "to_redraw": sum(1 for e in results if e["why"] != "already at the project canvas"),
        # Named rather than counted: the name is what a caller needs to make
        # one of these right, and the count is what lets it be ignored.
        "unrecorded": unrecorded,
        "plan": bool(plan),
    }


# -- cue table -------------------------------------------------------------
#
# Step 1 of the layered timeline (PLAN.md § The layered timeline): a picture
# overlay addressed by source word, never by timeline position. A cue says
# "from this word of this clip onward, show this asset"; the shot projection
# (step 2) turns the table into contiguous shots by mapping each cue's word
# through the edit's surviving ranges. Nothing here touches project.otio —
# the cue table lives in the manifest and is metadata until step 2 reads it.


def _cue_echo(parsed: tx.Transcript, word_index: int) -> dict[str, Any]:
    """What a cue's word index resolved to — the same convention cut/locate
    use for any tool that takes a word index (CLAUDE.md)."""
    start, end = parsed.span(word_index, word_index)
    word = parsed.words[word_index]
    return {
        "word_index": word_index,
        "text": word.text,
        "start": start,
        "end": end,
        **_context(parsed, word_index, word_index),
    }


def cue_add(
    path: Path | str,
    clip_id: str,
    word_index: int | None = None,
    asset: str | None = None,
    *,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    src_start: float | None = None,
) -> dict[str, Any]:
    """Add a cue: from `word_index` of `clip_id` onward, show `asset`.

    Source-addressed, like every other word-indexed tool here — `asset` is
    not resolved or checked against disk; that is the shot projection's job
    (step 2), which also knows how to turn a `card:name` key into a path.
    Refused if a cue already sits at this exact word; remove it first with
    `cue_rm` to replace it, so a call can never silently pick a winner
    between two assets at the same word.

    Addressed by `word_index` **or** `phrase`, never both — a phrase binds to
    its **first** word ("from this word onward" is what a cue means), the
    same edge goodsometimes' own `cue` binding used. `after`/`occurrence`
    disambiguate a phrase that matches more than once (`Transcript.resolve`);
    a phrase that resolved to more than one word is stored beside the
    resolved `word_index` as additive-optional `phrase` metadata — never the
    address itself (CLAUDE.md), so re-attaching a transcript invalidates
    nothing that was not already true of a plain word-index cue. See
    `cue_reresolve` for re-deriving a phrase-addressed cue after a re-record.

    `src_start` **pins the in-point**: seconds into `asset`, in that asset's
    own source time, which is exactly what a `describe_ls` window reports
    (PLAN.md § B-roll by description). Omit it and the shot reads from
    wherever `mlt.plan_picture`'s per-asset cursor has got to — the right
    default for re-using a clip, and wrong for placing a moment somebody
    searched for.

    **It is an in-point only, never a range.** The out-point stays derived
    from the next cue through the edit, because a cue carrying its own length
    is the failure PLAN.md § The property everything below defends exists to
    prevent — the music bed's lengths were tuned to a runtime and a later
    append invalidated every one of them. What the pin costs instead is a
    refusal: a pinned shot that outruns its asset is `plan_picture`'s error,
    not a rewind, and it surfaces on the picture lane as `shots_error`.

    Nothing here checks the pin against the asset's duration, for the same
    reason nothing here resolves the asset: that needs media on disk, and it
    is the projection's job. What it does check is the pin's own arithmetic —
    a negative in-point, or one on a `card:`, where a held frame has no
    playhead to move.
    """
    if asset is None:
        raise tx.TranscriptError(
            "cue_add needs asset — a cue says what to show, not only where"
        )
    project = Project.open(path)
    media.get_clip(project, clip_id)
    parsed = _transcript(project, clip_id)
    word_index, _ = _resolve_word_or_phrase(
        parsed, word_index=word_index, phrase=phrase, after=after, occurrence=occurrence, edge="first"
    )
    echo = _cue_echo(parsed, word_index)

    cue: dict[str, Any] = {"clip_id": clip_id, "word_index": word_index, "asset": asset}
    if phrase is not None:
        cue["phrase"] = phrase
    if src_start is not None:
        src_start = float(src_start)
        if src_start < 0:
            raise tx.TranscriptError(
                f"src_start {src_start} is before the start of {asset!r} — an "
                "in-point is seconds into the asset, in its own source time"
            )
        if asset.startswith("card:"):
            raise tx.TranscriptError(
                f"asset {asset!r} is a card, and a still has no playhead to move — "
                "drop src_start, or point the cue at a video clip_id"
            )
        cue["src_start"] = src_start

    manifest = project.read_manifest()
    cues = manifest.setdefault("cues", [])
    if any(c["clip_id"] == clip_id and c["word_index"] == word_index for c in cues):
        raise tx.TranscriptError(
            f"{clip_id!r} already has a cue at word {word_index} — remove it "
            "with cue_rm first (CLI: `proofcut cue rm`) if you meant to replace it"
        )
    cues.append(cue)
    cues.sort(key=lambda c: (c["clip_id"], c["word_index"]))
    project.write_manifest(manifest)
    return {
        "clip_id": clip_id,
        "asset": asset,
        "src_start": src_start,
        "phrase": phrase,
        "cues": len(cues),
        **echo,
    }


def cue_rm(
    path: Path | str,
    clip_id: str,
    word_index: int | None = None,
    *,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Remove the cue at `clip_id` word `word_index` — or wherever `phrase`
    resolves to (its first word, `cue_add`'s own binding — the same address
    space, for symmetry)."""
    project = Project.open(path)
    parsed = _transcript(project, clip_id)
    word_index, _ = _resolve_word_or_phrase(
        parsed, word_index=word_index, phrase=phrase, after=after, occurrence=occurrence, edge="first"
    )
    manifest = project.read_manifest()
    cues = manifest.get("cues", [])
    match = next(
        (c for c in cues if c["clip_id"] == clip_id and c["word_index"] == word_index), None
    )
    if match is None:
        known = ", ".join(f"{c['clip_id']}:{c['word_index']}" for c in cues) or "none"
        raise tx.TranscriptError(
            f"no cue at {clip_id!r} word {word_index} (existing cues: {known}) — see cue_ls"
        )
    manifest["cues"] = [c for c in cues if c is not match]
    project.write_manifest(manifest)
    return {
        "clip_id": clip_id,
        "asset": match["asset"],
        "src_start": match.get("src_start"),
        "cues": len(manifest["cues"]),
        **_cue_echo(parsed, word_index),
    }


def cue_ls(path: Path | str, clip_id: str | None = None) -> dict[str, Any]:
    """List the cue table, each entry echoed with its resolved word.

    Read-only. `clip_id` narrows to one clip's cues; omit it to see every
    cue in the project. Ordered by `(clip_id, word_index)`, not by resolved
    timeline position — that ordering is `build_shots`'s job, because it
    depends on the edit's surviving ranges.
    """
    project = Project.open(path)
    cues = project.read_manifest().get("cues", [])
    if clip_id is not None:
        cues = [c for c in cues if c["clip_id"] == clip_id]
    cues = sorted(cues, key=lambda c: (c["clip_id"], c["word_index"]))

    transcripts: dict[str, tx.Transcript] = {}
    entries = []
    for cue in cues:
        cid = cue["clip_id"]
        if cid not in transcripts:
            transcripts[cid] = _transcript(project, cid)
        entries.append(
            {
                "clip_id": cid,
                "asset": cue["asset"],
                "src_start": cue.get("src_start"),
                "phrase": cue.get("phrase"),
                **_cue_echo(transcripts[cid], cue["word_index"]),
            }
        )
    return {"cues": entries, "count": len(entries)}


def _reresolve_phrase(
    parsed: tx.Transcript | None, phrase: str | None, *, edge: str
) -> dict[str, Any]:
    """One phrase-addressed entry's re-resolution outcome, for `cue_reresolve`.

    Never raises: an ambiguous or unresolved phrase is reported, not thrown,
    because a re-record can legitimately make an old phrase stop meaning one
    thing — that is exactly the case `cue_reresolve` exists to surface.
    """
    if phrase is None:
        return {"phrase": None, "action": "unchanged (no phrase to re-resolve)"}
    if parsed is None:
        return {"phrase": phrase, "action": "no transcript for this clip"}
    try:
        resolved = parsed.resolve(phrase)
    except tx.AmbiguousPhraseError as exc:
        return {"phrase": phrase, "action": "ambiguous", "candidates": exc.candidates}
    except tx.TranscriptError as exc:
        return {"phrase": phrase, "action": "not found", "error": str(exc)}
    word_index = resolved["first_word"] if edge == "first" else resolved["last_word"]
    return {
        "phrase": phrase,
        "action": "resolved",
        "word_index": word_index,
        "match": resolved["match"],
        "ratio": resolved["ratio"],
    }


def cue_reresolve(
    path: Path | str, clip_id: str | None = None, *, apply: bool = False
) -> dict[str, Any]:
    """Re-resolve every phrase-addressed cue, unspoken mark and music-bed
    boundary against `clip_id`'s *current* transcript (every clip that has
    one, if `clip_id` is omitted) and report what moved.

    A re-record replaces a clip's transcript wholesale (`attach_transcript`,
    `transcribe`) and every stored `word_index` on that clip potentially now
    addresses the wrong word — already true today of a plain word-index cue,
    and this does not close that gap for one. What it closes it for is an
    entry that also carries the `phrase` (or `phrase_start`/`phrase_end`) it
    was placed with: re-running `Transcript.resolve()` against the transcript
    now attached says where that same wording landed, without hand
    re-indexing a whole cue table — the goodsometimes v3->v4 workflow
    (`assemble_longlegs.py --plan`/`--apply`), now inside proofcut.

    `apply=False` (default): report only, nothing is written —
    `reframe_detect`'s and `unspoken_detect`'s own posture, because a phrase
    that now resolves ambiguously or not at all needs a human decision, not a
    guess. `apply=True` rewrites `word_index` in place for every entry whose
    phrase still resolves to exactly one match (`report["applied"] = True`
    marks which); anything ambiguous or unresolved is reported and left
    untouched, never guessed.

    An entry with no stored phrase (hand-index-addressed, or written before
    this feature existed) is reported as `"phrase": None, "action":
    "unchanged (no phrase to re-resolve)"` — not silently skipped, so a
    caller can tell "checked, still fine" from "cannot check this one".
    """
    project = Project.open(path)
    manifest = project.read_manifest()

    cues = manifest.get("cues", [])
    marks = manifest.get(UNSPOKEN_KEY, [])
    bed = manifest.get(MUSIC_KEY)

    relevant_ids: set[str] = set()
    for cue in cues:
        relevant_ids.add(cue["clip_id"])
    for mark in marks:
        relevant_ids.add(mark["clip_id"])
    if bed is not None:
        relevant_ids.add(bed["clip_id"])
    if clip_id is not None:
        relevant_ids &= {clip_id}

    transcripts: dict[str, tx.Transcript | None] = {}
    for cid in relevant_ids:
        try:
            transcripts[cid] = _transcript(project, cid)
        except tx.TranscriptError:
            transcripts[cid] = None

    changed = False

    cue_reports = []
    for cue in cues:
        if clip_id is not None and cue["clip_id"] != clip_id:
            continue
        outcome = _reresolve_phrase(transcripts.get(cue["clip_id"]), cue.get("phrase"), edge="first")
        report = {
            "clip_id": cue["clip_id"],
            "asset": cue["asset"],
            "word_index": cue["word_index"],
            **outcome,
        }
        if apply and outcome["action"] == "resolved" and outcome["word_index"] != cue["word_index"]:
            cue["word_index"] = outcome["word_index"]
            report["applied"] = True
            changed = True
        cue_reports.append(report)

    mark_reports = []
    for mark in marks:
        if clip_id is not None and mark["clip_id"] != clip_id:
            continue
        outcome = _reresolve_phrase(transcripts.get(mark["clip_id"]), mark.get("phrase"), edge="first")
        report = {"clip_id": mark["clip_id"], "word_index": mark["word_index"], **outcome}
        if apply and outcome["action"] == "resolved" and outcome["word_index"] != mark["word_index"]:
            mark["word_index"] = outcome["word_index"]
            report["applied"] = True
            changed = True
        mark_reports.append(report)

    music_report = None
    if bed is not None and (clip_id is None or bed["clip_id"] == clip_id):
        parsed = transcripts.get(bed["clip_id"])
        start_outcome = _reresolve_phrase(parsed, bed.get("phrase_start"), edge="first")
        end_outcome = _reresolve_phrase(parsed, bed.get("phrase_end"), edge="last")
        music_report = {"clip_id": bed["clip_id"], "start": start_outcome, "end": end_outcome}
        if apply:
            if (
                start_outcome["action"] == "resolved"
                and start_outcome["word_index"] != bed["word_index_start"]
            ):
                bed["word_index_start"] = start_outcome["word_index"]
                music_report["start"] = {**start_outcome, "applied": True}
                changed = True
            if (
                end_outcome["action"] == "resolved"
                and end_outcome["word_index"] != bed.get("word_index_end")
            ):
                bed["word_index_end"] = end_outcome["word_index"]
                music_report["end"] = {**end_outcome, "applied": True}
                changed = True

    if changed:
        cues.sort(key=lambda c: (c["clip_id"], c["word_index"]))
        marks.sort(key=lambda m: (m["clip_id"], int(m["word_index"])))
        project.write_manifest(manifest)

    return {
        "clip_id": clip_id,
        "apply": bool(apply),
        "applied": changed,
        "cues": cue_reports,
        "unspoken": mark_reports,
        "music": music_report,
    }


#: Daydream's own split (docs/plans/DAYDREAM.md § Import roles + assets pane):
#: "voiceover" for footage that becomes the transcript-as-document,
#: "footage" for what `describe` indexes for b-roll search.
CLIP_ROLES = ("voiceover", "footage")


def clip_role(
    path: Path | str, clip_id: str, role: str | None = None, *, reset: bool = False
) -> dict[str, Any]:
    """Read or set a clip's import role — the assets pane's grouping, and
    nothing else.

    Called with no `role` and no `reset` it just reports what is stored.
    `role` must be one of `CLIP_ROLES`; `reset` clears it back to undeclared.

    **Absent means undeclared, not "neither," and setting one changes no
    other op's behaviour.** `transcribe`/`attach_transcript` gate on their
    own evidence (a transcript file) and `describe` gates on `has_video` —
    neither reads this field, so a clip with no role declared is exactly as
    eligible for both as it always was. That is what keeps every project
    written before this existed reading exactly as it always did, and it is
    why this is not a schema bump: an additive optional field on an existing
    clip record, the same shape `interp` has on a reframe window (CLAUDE.md)
    rather than a new list a migration would need to own.
    """
    if role is not None and reset:
        raise ProjectError("pass a role or `reset`, not both")
    if role is not None and role not in CLIP_ROLES:
        raise ProjectError(f"role {role!r} is not one of {', '.join(CLIP_ROLES)}")

    project = Project.open(path)
    media.get_clip(project, clip_id)  # the known-ids message if it doesn't exist

    manifest = project.read_manifest()
    clip = next(c for c in manifest["clips"] if c["clip_id"] == clip_id)

    write = role is not None or reset
    if write:
        if reset:
            clip.pop("role", None)
        else:
            clip["role"] = role
        project.write_manifest(manifest)

    return {"clip_id": clip_id, "role": clip.get("role"), "written": write, "reset": bool(reset)}


def clip_rm(path: Path | str, clip_id: str) -> dict[str, Any]:
    """Un-register a clip `import_media` added, when nothing depends on it yet.

    There is `cue_rm`, `hold_rm`, `unspoken_rm` and no way to undo an import
    on its own — `undo` is positional and would take every mutation after it
    too. The gap is quiet: a clip nobody meant to keep still shows up in
    `assets`, is still cue-able, and still counts toward `media_imported`
    (TRIAL.md § `spot_frames` is the tool for looking at a delivered render —
    the agent that found this had registered its own delivered render as a
    clip just to look at it, and had no way to take that back).

    **Refuses rather than orphaning a reference**, the same discipline
    `build_shots` applies to a cut orphaning a cue: on the timeline, cued (as
    either a cue's addressing `clip_id` or its `asset`), held, the music
    bed's own clip, marked unspoken, transcribed or described are all
    reasons this clip is no longer "just registered", and every one that
    applies is named in the refusal so the fix is obvious rather than a
    second round trip. A clip with none of those is deregistered outright —
    the manifest entry only; the media on disk is never touched, the way
    `undo`ing an import leaves it alone too.
    """
    project = Project.open(path)
    media.get_clip(project, clip_id)  # the known-ids message if it doesn't exist

    manifest = project.read_manifest()
    # Not `_load_edit`: a project with nothing seeded yet has no timeline at
    # all, and that is not a reason to refuse this check — only a reason
    # "on the timeline" can never be one of the blockers found.
    edit = tl.read(project.timeline_path) if project.timeline_path.exists() else None
    blockers: list[str] = []
    if edit is not None and any(seg.clip_id == clip_id for seg in edit.segments):
        blockers.append("it is on the timeline")
    if any(
        cue["clip_id"] == clip_id or cue["asset"] == clip_id for cue in manifest.get("cues", [])
    ):
        blockers.append("it is referenced by a cue (see cue_ls, cue_rm)")
    if any(
        hold["clip_id"] == clip_id or hold["asset"] == clip_id
        for hold in manifest.get(HOLDS_KEY, [])
    ):
        blockers.append("it is referenced by a hold (see hold_ls, hold_rm)")
    if any(
        item.get("clip_id") == clip_id or item.get("asset") == clip_id
        for item in manifest.get(UNDER_VO_KEY, [])
    ):
        blockers.append("it plays under the VO (see hold_ls, hold_under_rm)")
    bed = manifest.get(MUSIC_KEY)
    if bed and (bed.get("clip_id") == clip_id or clip_id in _music_assets(bed)):
        blockers.append("it is the music bed's own clip (see music reset=True)")
    if any(mark["clip_id"] == clip_id for mark in manifest.get(UNSPOKEN_KEY, [])):
        blockers.append("it has an unspoken mark (see unspoken_ls, unspoken_rm)")
    if project.transcript_path(clip_id).is_file():
        blockers.append("it has a transcript attached")
    if any(d["clip_id"] == clip_id for d in _descriptions(project)):
        blockers.append("it has been described (see describe_ls)")
    if blockers:
        raise ProjectError(
            f"clip_rm refuses {clip_id!r}: " + "; ".join(blockers) + ". Clear every "
            "reference first, or `proofcut undo` back to before it was imported."
        )

    manifest["clips"] = [c for c in manifest["clips"] if c["clip_id"] != clip_id]
    project.write_manifest(manifest)
    return {"clip_id": clip_id, "removed": True}


def assets(path: Path | str) -> dict[str, Any]:
    """Every asset a cue can point at — clip or card — with what an
    inspector pane needs to show about it.

    The cue vocabulary is `clip_id` or `card:name` (CLAUDE.md), so a list
    that only shows clips is half the catalogue; this reports both, each
    with how many cues reference it (`cues`) — an assets pane that cannot
    say "this is used 3 times" is a list, not an inspector.

    Per clip: the resolved media path (`media.media_path`, never a raw
    manifest field — CLAUDE.md), the probe metadata captured at import
    (duration, has_video/has_audio, fps, width/height, sample_rate,
    channels, codecs, vfr), whether a transcript is attached, whether
    `describe` has indexed it, its declared `role` (`clip_role`), and
    `media.playability`'s verdict when the file is actually reachable on
    disk — `playable: null` when it is not, which is a different claim than
    "unplayable".

    Per card: what `card_new` recorded it from (`template`, `canvas`,
    `variant`, all null when there is no record), whether its files exist
    under `assets/cards/`, and `recorded` — **a card with files and no
    record cannot be re-authored by anything, and this is where that is
    reported rather than guessed at** (CLAUDE.md, `card_reauthor`'s own
    `unrecorded` list). A name can appear with files and no record, or a
    record and no files; both halves are reported so they can disagree.

    Read-only, and composed entirely from state other ops already
    maintain — the cue table, the card records, the manifest's own clip
    rows — nothing here is a new derivation.
    """
    project = Project.open(path)
    manifest = project.read_manifest()

    usage: dict[str, int] = {}
    for cue in manifest.get("cues", []):
        usage[cue["asset"]] = usage.get(cue["asset"], 0) + 1

    described = {d["clip_id"] for d in _descriptions(project)}

    clip_entries = []
    for clip in manifest.get("clips", []):
        clip_id = clip["clip_id"]
        source = media.media_path(project, clip)
        exists = source.is_file()
        clip_entries.append(
            {
                "clip_id": clip_id,
                "kind": "clip",
                "media_path": str(source),
                "media_exists": exists,
                "duration": clip.get("duration"),
                "has_video": clip.get("has_video"),
                "has_audio": clip.get("has_audio"),
                "width": clip.get("width"),
                "height": clip.get("height"),
                "fps": clip.get("fps"),
                "sample_rate": clip.get("sample_rate"),
                "channels": clip.get("channels"),
                "video_codec": clip.get("video_codec"),
                "audio_codec": clip.get("audio_codec"),
                "vfr": clip.get("vfr"),
                "role": clip.get("role"),
                "transcript": project.transcript_path(clip_id).is_file(),
                "described": clip_id in described,
                "cues": usage.get(clip_id, 0),
                "playable": media.playability(source) if exists else None,
            }
        )

    records = {r["card"]: r for r in _card_records(project)}
    on_disk = set(_cards_on_disk(project))
    card_entries = []
    for name in sorted(records.keys() | on_disk):
        record = records.get(name)
        card_entries.append(
            {
                "name": name,
                "kind": "card",
                "asset": f"card:{name}",
                "template": record.get("template") if record else None,
                "canvas": record.get("canvas") if record else None,
                "variant": record.get("variant") if record else None,
                "files_exist": name in on_disk,
                "recorded": record is not None,
                "cues": usage.get(f"card:{name}", 0),
            }
        )

    return {"clips": clip_entries, "cards": card_entries}


def broll_brief(path: Path | str, *, fps: float | None = None) -> dict[str, Any]:
    """Everything needed to choose b-roll, and nothing that chooses it.

    One read-only call that assembles the whole question: the catalogue of
    footage with each clip's `synopsis`, and every shot position with the
    narration that plays over it and how long it is held. What comes back is
    meant to be handed to something that knows the material — the agent on the
    other end of the MCP server, a `claude -p` panel, or a person — which then
    writes its answers back through `cue_add`, where `plan_picture` checks
    them like any other cue.

    **proofcut does not pick, and this is a measurement rather than a
    preference.** Against 25 human choices on the Scream footage: the
    `describe` index agreed 2 times, the clips' own filenames 3, an explicit
    film-name match 4. A synopsis catalogue narrowed nine candidates to a
    correct three 15 times but still only picked right 5. The same catalogue
    read by a model that knows the films picked right 13. Every mechanism that
    scores text against text plateaus in single digits because the connection
    is not lexical — the sentence that earns the Scream VI reveal shares no
    word with any description of it. So the useful thing proofcut can build is
    the brief, not the ranker. HISTORY.md § Choosing the b-roll.

    What is in here is what was measured to matter, and one thing that was
    measured *not* to. `narration` per position and the synopsis catalogue are
    the signal. `duration` and the `card` positions are cheap and plausibly
    useful — a long hold wants footage that sustains, and a repeat reads as a
    repeat across a card — but adding them moved 12 correct to 13, which is
    noise, so nothing here should be defended on their behalf. The thing that
    was measured not to work is a **second reviewing pass**: handing these
    positions back with the picks already in them and asking for repeats and
    off-by-one beats to be fixed changed 6 answers and scored 13 → 10. It is
    not implemented for that reason, not because it was never tried.

    `card:` positions are reported and are **not** candidates. A card is
    authored for its moment; the choice this brief exists for is which footage
    goes under which sentence. They are here so the picker can see the rhythm
    it is choosing into, marked with `card: true`.

    Read-only, so it never leaves a project half-briefed, and it reports
    rather than raises: a project whose picture plan already refuses comes
    back with `shots_error` set and `positions` empty, because a brief listing
    a slot `export` will not produce invites a pick for a shot that cannot
    exist.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    clips = _clips_by_id(project)
    rate = float(fps) if fps else _export_fps(clips)

    shots_error: str | None = None
    try:
        shots, _ = _picture_plan(project, rate)
    except _PICTURE_REFUSALS as exc:
        shots, shots_error = [], str(exc)

    # Every word that still plays, with where it now plays. Placements come
    # per clip because a transcript indexes its own source; the timeline is
    # what they get sorted onto (CLAUDE.md: emits for playback map through
    # the edit, never straight off the transcript).
    playing: list[tuple[float, str]] = []
    for clip_id in clips:
        if not project.transcript_path(clip_id).exists():
            continue
        for item in _word_placements(edit, clip_id, _transcript(project, clip_id)):
            if item["present"]:
                playing.append((item["timeline_start"], item["text"]))
    playing.sort(key=lambda pair: pair[0])

    positions = []
    for shot in shots:
        start, end = shot["start"], shot["start"] + shot["duration"]
        positions.append(
            {
                "clip_id": shot["clip_id"],
                "word_index": shot["word_index"],
                "asset": shot["asset"],
                "card": bool(shot["asset"].startswith("card:")),
                "start": start,
                "duration": shot["duration"],
                "narration": " ".join(
                    text for at, text in playing if start <= at < end
                ).strip(),
            }
        )

    candidates = [
        {
            "clip_id": c["clip_id"],
            "synopsis": c.get(SYNOPSIS_KEY),
            "duration": c.get("duration"),
        }
        for c in clips.values()
        if c.get("has_video")
    ]
    result: dict[str, Any] = {
        "project": str(project.root),
        "candidates": candidates,
        "missing_synopsis": [c["clip_id"] for c in candidates if not c["synopsis"]],
        "positions": positions,
        "count": len(positions),
        "choices": sum(1 for p in positions if not p["card"]),
        "rate": rate,
    }
    if shots_error is not None:
        result["shots_error"] = shots_error
    elif not positions:
        # `_picture_plan` returns empty rather than refusing for a project with
        # no cues, which is right for the picture lane and silent here: a brief
        # is asked for precisely when nothing has been placed yet, and an empty
        # answer with no error reads as "nothing to choose". Say which it is.
        result["note"] = (
            "no cues yet, so there are no positions to choose for — a cue is where the "
            "picture changes, and deciding where those go is a separate call (cue_add, "
            "CLI: `proofcut cue add`). The catalogue below is what they can point at."
        )
    return result


def _resolve_asset(project: Project, asset: str) -> dict[str, Any]:
    """Turn a cue's opaque `asset` into a checked path, the way
    `assemble_scream.py`'s `resolve_media()` did by hand: `card:name` is a
    static picture under `assets/cards/`, anything else is a `clip_id`
    already registered with `import_media`. Raises if the asset does not
    resolve to real media — the projection is meant to catch a missing card
    or a typo'd clip_id here, not hand it to the MLT writer to find out.

    `asset_duration` rides along for the same reason: the MLT writer decides
    where inside a clip each shot reads from (`mlt.plan_picture`), and it can
    only tell a re-use from an overrun if it knows how long the clip is. A
    card has no duration — a still is held, not played.
    """
    if asset.startswith("card:"):
        name = asset.removeprefix("card:")
        if not name:
            raise ProjectError(f"asset {asset!r} names no card")
        resolved = project.cards_dir / f"{name}.png"
        is_image = True
        duration = None
    else:
        clip = media.get_clip(project, asset)
        if not clip.get("has_video"):
            raise ProjectError(
                f"asset {asset!r} is clip_id {asset!r}, which has no video — "
                "a picture cue needs a video clip or a card:name"
            )
        resolved = media.media_path(project, clip)
        is_image = False
        duration = float(clip["duration"])
    if not resolved.is_file():
        raise ProjectError(f"asset {asset!r} resolves to {resolved}, which does not exist")
    return {"asset_path": str(resolved), "is_image": is_image, "asset_duration": duration}


def build_shots(
    path: Path | str, *, fps: float | None = None, edit: tl.Edit | None = None
) -> dict[str, Any]:
    """Project the cue table into contiguous shots over the current edit.

    Step 2 of the layered timeline (PLAN.md § The layered timeline):
    `assemble_scream.py`'s `build_shots` minus the XML. Each cue's word maps
    to a timeline frame via `edit.timeline_span(clip_id, word.start,
    word.end)` — an overlap test across the whole word, never containment of
    its start instant alone (CLAUDE.md: "survival is an overlap test... never
    containment"). A word whose *start* lands in a gap but whose tail spills
    into the next surviving segment is exactly the case that distinction
    exists for, and it is not a corner case: it is what a cue riding a
    swallowed retake looks like, and the real Scream VO has one (word 115,
    the "here's" that survived a false start). `timeline_time` on the start
    alone was tried first and disagreed with `assemble_scream.py`'s own
    arithmetic on that exact word — this is HISTORY.md § The multi-track
    costing spike's validation, redone with the right method. A cue whose
    word has no overlap at all refuses rather than silently snapping
    forward: `timeline_span` returns None for a fully-cut word, and that is
    the safety property PLAN.md calls out as step 3 — it cannot be deferred
    past step 2, because the frame arithmetic has nothing to return
    otherwise.

    Cues are ordered by resolved timeline position, not by `(clip_id,
    word_index)` (`cue_ls`'s order) — the whole reason this is its own step
    rather than a `cue_ls` sort key: two clips' cues only have a shared order
    once mapped through the edit. The first shot is forced to frame 0 — the
    picture track is contiguous by construction, so whichever cue comes first
    covers from the open, not from wherever its own word happens to land.
    Every other shot runs from its cue's frame to the next cue's; the last
    runs to `autoeditor.frame_total`, never to a summed duration (CLAUDE.md).

    What this does *not* do, deliberately: no per-clip playback cursor, no
    source in/out points. Those decide what the MLT writer (step 4) actually
    shows for the duration computed here, and belong with the XML that
    consumes them — this step only says when and for how long.

    A cue's optional in-point rides through as `src_pin` and is not one of
    them: it is *carried*, never decided here. The distinction is worth the
    second field name — `src_pin` is what the cue asked for, and the
    `src_start` a shot picks up in `_picture_plan` is where it actually
    reads. For a pinned shot they agree by construction, which is what
    `plan_picture` refusing rather than rewinding buys; for an unpinned one
    `src_pin` is None and `src_start` is wherever the cursor had got to.

    `fps` states which frame grid to answer on, and defaults to the project's
    own timebase — which for an audio-only project is **milliseconds**, not
    frames (`autoeditor.AUDIO_TIMEBASE`). An export quantises to a real frame
    rate instead (`_export_fps`), so `export` passes its own rate through
    rather than converting the answer afterwards: two roundings of the same
    number are how a picture ends up a frame off the audio it was cut to.

    `edit` overrides the timeline read off disk — for a caller (`vo_extend`)
    that needs the projection over an edit it has mutated in memory but not
    yet decided to save, never for an ordinary read.
    """
    project = Project.open(path)
    edit = edit if edit is not None else _load_edit(project)
    rate = float(fps) if fps else _rate(project)
    total_frames = autoeditor.frame_total(edit, rate)

    cues = project.read_manifest().get("cues", [])
    if not cues:
        raise tl.TimelineError(
            "this project has no cues yet — add one with cue_add "
            "(CLI: `proofcut cue add`) before projecting shots"
        )

    transcripts: dict[str, tx.Transcript] = {}
    marks: list[dict[str, Any]] = []
    for cue in cues:
        clip_id = cue["clip_id"]
        if clip_id not in transcripts:
            transcripts[clip_id] = _transcript(project, clip_id)
        echo = _cue_echo(transcripts[clip_id], cue["word_index"])
        span = edit.timeline_span(clip_id, echo["start"], echo["end"])
        if span is None:
            raise tl.TimelineError(
                f"cue at {clip_id!r} word {cue['word_index']} ({echo['text']!r}) "
                "was cut from the edit — remove or move the cue (cue_rm/cue_add) "
                "before projecting shots"
            )
        timeline_start, _ = span
        marks.append(
            {
                "clip_id": clip_id,
                "word_index": cue["word_index"],
                "text": echo["text"],
                "asset": cue["asset"],
                "src_pin": cue.get("src_start"),
                **_resolve_asset(project, cue["asset"]),
                "start_frame": round(timeline_start * rate),
            }
        )

    marks.sort(key=lambda m: m["start_frame"])
    marks[0]["start_frame"] = 0

    for previous, current in pairwise(marks):
        if current["start_frame"] <= previous["start_frame"]:
            raise tl.TimelineError(
                f"cue at {current['clip_id']!r} word {current['word_index']} lands "
                f"at or before the previous cue ({previous['clip_id']!r} word "
                f"{previous['word_index']}) — two cues resolved to the same instant"
            )

    shots = []
    for index, mark in enumerate(marks):
        end_frame = marks[index + 1]["start_frame"] if index + 1 < len(marks) else total_frames
        frames = end_frame - mark["start_frame"]
        shots.append(
            {
                **mark,
                "frames": frames,
                "start": mark["start_frame"] / rate,
                "duration": frames / rate,
            }
        )
    return {"shots": shots, "count": len(shots), "rate": rate, "total_frames": total_frames}


#: Every way the picture plan refuses. All of them are deliberate — a cue that
#: was cut, two cues resolving to one instant, a shot longer than the asset it
#: points at, an asset that does not resolve — so a caller that wants to
#: *report* a refusal rather than raise it catches exactly these.
_PICTURE_REFUSALS = (
    tl.TimelineError,
    mlt.MLTError,
    ProjectError,
    tx.TranscriptError,
    media.MediaError,
)


def _picture_plan(
    project: Project, rate: float, *, edit: tl.Edit | None = None
) -> tuple[list[dict[str, Any]], list[mlt.Entry]]:
    """The picture track, projected and planned, on one frame grid.

    Two steps that have to travel together: `build_shots` (step 2) says when
    each shot starts and how long it runs, and `mlt.plan_picture` (step 4)
    decides what it actually shows and from where inside its asset. Either can
    refuse, and **a refusal from either is a shot `export` will not produce** —
    which is why the web UI's picture lane comes through here rather than off
    `build_shots` alone. A lane drawn from the projection only would draw the
    shot whose 34.6s runs past its 30.1s clip and the writer rejects (HISTORY.md
    § Rendering through `melt`), and that is the same class of lie as drawing a
    track the renderer silently degrades (CLAUDE.md).

    Shots come back annotated with where inside the asset each one reads —
    `plan_picture`'s per-asset cursor, which nothing downstream of it can see —
    so a clip used three times can be told from a clip replayed from its head
    three times.

    `([], [])` for a project with no cues, which is not a refusal: the edit's
    own track is the whole picture then.

    `edit` is passed straight through to `build_shots`, for the same
    not-yet-saved-edit case that parameter exists for.
    """
    if not project.read_manifest().get("cues"):
        return [], []
    shots = build_shots(project.root, fps=rate, edit=edit)["shots"]
    entries = mlt.plan_picture(shots, rate)
    annotated = [
        {**shot, "src_in": entry.src_in, "src_out": entry.src_out, "src_start": entry.src_in / rate}
        for shot, entry in zip(shots, entries)
    ]
    return annotated, entries


# -- timeline ------------------------------------------------------------


def _timeline_bound(project: Project, clip: dict[str, Any]) -> float:
    """How far into `clip` a timeline may reach: its duration, or where its picture stops if sooner.

    A clip's `duration` is its container's, which ends with the longer stream,
    so audio that outlasts the video by half a frame lays down one frame the
    video has not got. It renders black and `frames` agrees, because it
    compares the render against the timeline. A clip imported before
    `picture_end` was recorded is probed for it. HISTORY.md § The phone's
    black last frame.
    """
    end = float(clip["duration"])
    if clip.get("has_video"):
        if "picture_end" in clip:
            picture_end = clip["picture_end"]
        else:
            picture_end = media.probe(media.media_path(project, clip)).picture_end
        if picture_end is not None:
            end = min(end, float(picture_end))
    return end


def seed_timeline(
    path: Path | str,
    clip_id: str,
    *,
    remove_silences: bool = True,
    threshold: float = 0.04,
    margin: str | None = None,
    edit_expr: str | None = None,
) -> dict[str, Any]:
    """Lay a clip down as the timeline, optionally silence-cut on the way in.

    Silence detection is auto-editor's, not proofcut's — shell out rather than
    reimplement (PLAN.md scope rule).
    """
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    source = media.media_path(project, clip)

    # Neither seed may run past the file's picture: auto-editor can count a
    # frame past the end (an iPhone clip's stretched last frame gets a
    # 2999/100 timebase). HISTORY.md § The phone's black last frame.
    end = _timeline_bound(project, clip)

    if remove_silences:
        edit = autoeditor.silence_edit(
            source, clip_id, threshold=threshold, margin=margin, edit_expr=edit_expr
        )
        if edit.segments and edit.segments[-1].end > end:
            edit = tl.Edit([
                tl.Segment(clip_id=s.clip_id, start=s.start, end=min(s.end, end))
                for s in edit.segments
                if s.start < end
            ])
    else:
        edit = tl.Edit([tl.Segment(clip_id=clip_id, start=0.0, end=end)])

    manifest = project.read_manifest()
    manifest.setdefault(
        "timebase",
        float(clip["fps"]) if clip.get("has_video") and clip.get("fps") else autoeditor.AUDIO_TIMEBASE,
    )
    project.write_manifest(manifest)

    _save_edit(project, edit)
    return {
        "clip_id": clip_id,
        "segments": len(edit.segments),
        "source_duration": float(clip["duration"]),
        "timeline_duration": edit.duration,
        "silences_removed": remove_silences,
    }


def _resolve_resource(document: Path, root_attr: str | None, resource: str) -> Path:
    """Where a playlist's `resource` string actually points.

    MLT resolves a relative resource against the document's `root` attribute,
    and falls back to the document's own directory when there is none. Both
    are tried rather than one assumed, because a `.kdenlive` moved off the
    machine that wrote it keeps a `root` that no longer exists — and then the
    only honest answer is the directory the file is sitting in.
    """
    candidate = Path(resource).expanduser()
    if candidate.is_absolute():
        return candidate
    roots = [Path(root_attr).expanduser()] if root_attr else []
    roots.append(document.parent)
    for base in roots:
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved
    return (roots[0] / candidate).resolve()


def import_edit(
    path: Path | str,
    document: Path | str,
    *,
    clip_id: str | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Lay an outside cut down as the timeline — the supported way in.

    The other half of `seed_timeline`: that one lays a clip down and lets
    auto-editor find the cuts, this one takes a cut somebody already made in
    Kdenlive. PLAN.md § Open questions, *How does a lucid project know it is
    the film*, names why it exists — the Scream retake pass was done in
    Kdenlive, and bringing it in meant 63 ranges parsed by hand and written
    straight to `Edit`, "which is not a supported path — it bypasses `cut` and
    its history entirely" (HISTORY.md § The VO the project was holding). Going
    through `_save_edit` is most of the fix: the old timeline is snapshotted
    before this one replaces it, so an import is undoable like every other
    mutation.

    **Every clip has to be registered already.** Importing media as a side
    effect of importing an edit would make one op that reaches ffprobe, writes
    the manifest and replaces the timeline, and the failure mode is a project
    holding footage nobody asked for. Unregistered resources are named, all of
    them at once, rather than one per run.

    `clip_id` names the single clip a one-source document maps onto, for the
    case where the `.kdenlive` was written against a copy of the media at a
    path this project does not know. It is refused against a document holding
    more than one resource — there would be nothing to say which is which.

    Ranges that overrun their clip's registered duration are **clamped and
    reported**, never dropped and never taken on trust. That case is not
    hypothetical or rare: auto-editor's own exports overshoot the tail by
    exactly one frame (`--export v3` and `--export kdenlive` alike, measured),
    which is the reading-side face of the black frame CLAUDE.md warns about.
    A clamp that said nothing would make the import quietly one frame shorter
    than the file it came from, which is the same class of silence this op
    exists to end.
    """
    project = Project.open(path)
    source = Path(document).expanduser()
    if not source.is_file():
        raise ProjectError(f"no such edit document: {source}")
    try:
        tree = ET.parse(source)
    except ET.ParseError as exc:
        raise ProjectError(f"{source.name} is not readable as XML: {exc}") from exc
    root = tree.getroot()
    if root.tag != "mlt":
        raise ProjectError(
            f"{source.name} has a <{root.tag}> root, not <mlt> — a .kdenlive project "
            "is an MLT document, and this is not one"
        )

    try:
        ranges, rate = mlt.read_ranges(root)
    except mlt.MLTError as exc:
        raise ProjectError(str(exc)) from exc

    resources = list(dict.fromkeys(r.resource for r in ranges if not r.silence))
    if clip_id is not None and len(resources) > 1:
        raise ProjectError(
            f"{source.name} holds {len(resources)} distinct resources "
            f"({', '.join(resources)}), so `clip_id` cannot say which is which — "
            "register each one and let the paths match instead"
        )

    clips = _clips_by_id(project)
    if clip_id is not None:
        if clip_id not in clips:
            raise ProjectError(f"unknown clip {clip_id!r}")
        mapping = {resources[0]: clip_id}
    else:
        by_source = {Path(c["source"]).expanduser().resolve(): cid for cid, c in clips.items()}
        mapping = {}
        unmatched = []
        for resource in resources:
            resolved = _resolve_resource(source, root.get("root"), resource)
            if resolved in by_source:
                mapping[resource] = by_source[resolved]
            else:
                unmatched.append(f"{resource} (looked for {resolved})")
        if unmatched:
            raise ProjectError(
                "this edit references media the project has not registered: "
                + "; ".join(unmatched)
                + " — import each one first, or pass clip_id for a single-source document"
            )

    segments: list[tl.Segment] = []
    overshot: list[dict[str, Any]] = []
    silences: list[dict[str, Any]] = []
    for position, entry in enumerate(ranges):
        if entry.silence:
            # Generated silence, `vo_extend`'s own file — registered only on a
            # write, below; a plan carries a placeholder id that is never saved.
            silences.append({"entry": position, "seconds": entry.duration})
            segments.append(tl.Segment(clip_id=f"silence-{round(entry.duration * 1000)}ms", start=0.0, end=entry.duration))
            continue
        cid = mapping[entry.resource]
        duration = float(clips[cid]["duration"])
        end = entry.end
        if end > duration + 1e-9:
            overshot.append(
                {
                    "clip_id": cid,
                    "asked_end": round(entry.end, 6),
                    "clamped_to": round(duration, 6),
                    "frames": round((entry.end - duration) * rate, 3),
                }
            )
            end = duration
        if end - entry.start < tl.MIN_SEGMENT:
            continue
        segments.append(tl.Segment(clip_id=cid, start=entry.start, end=end))

    if not segments:
        raise ProjectError(
            f"{source.name} parsed to {len(ranges)} ranges and none of them survived "
            "against the registered clip durations — the document is describing "
            "different media from the project's"
        )

    edit = tl.Edit(segments=segments)
    # What the document says about its own length, against what its entries
    # actually sum to. This is the check that would have caught the hand-parse
    # (§ The import that was one frame short, sixty-three times): every range
    # is individually plausible and the total is the only thing that is wrong.
    read_frames = sum(round(entry.duration * rate) for entry in ranges)
    declared = mlt.declared_length(root, rate)
    disagrees = sorted({name for name, frames in declared.items() if frames != read_frames})
    report: dict[str, Any] = {
        "project": str(project.root),
        "document": str(source),
        "rate": rate,
        "ranges": len(ranges),
        "segments": len(edit.segments),
        "clips": sorted(set(mapping.values())),
        "timeline_duration": edit.duration,
        "overshot": overshot,
        "document_frames": read_frames,
        "declared_frames": declared,
        "declares_otherwise": disagrees,
        "silences": silences,
    }
    if plan:
        report["plan"] = True
        return report

    manifest = project.read_manifest()
    manifest.setdefault("timebase", rate)
    project.write_manifest(manifest)
    if silences:
        registered = {
            round(s["seconds"] * 1000): media.import_media(project, _tail_silence(project, s["seconds"]))["clip_id"]
            for s in silences
        }
        edit = tl.Edit(
            segments=[
                tl.Segment(clip_id=registered[round(seg.end * 1000)], start=0.0, end=seg.end)
                if ranges[i].silence else seg
                for i, seg in enumerate(segments)
            ]
        )
        report["clips"] = sorted({seg.clip_id for seg in edit.segments})
    _save_edit(project, edit)
    report["undo_depth"] = len(project.snapshots())
    return report


def status(path: Path | str) -> dict[str, Any]:
    """The current timeline: duration, segment count, undo depth, and canvas.

    `timeline_duration` is the `Edit`'s own length in seconds and stays exactly
    that whether or not a `head`/`tail` is set — `Edit` never grows to describe
    either bookend (PLAN.md § Tail time — the design note). `head`/`tail` echo
    what is configured (None for none, the `canvas`/`caption_style` shape), and
    `expected_frames`/`expected_duration` are what `export` would actually lay
    down at its own default frame rate — `_frame_total_with_tail`, so a caller
    asking "how long is this" gets the same number `check_frames` and
    `_build_mlt` would.

    **A project with nothing seeded answers `seeded: false` rather than
    refusing.** This is usually the first call an agent makes, asking "what
    state is this project in" — and the answer to that on a fresh project is
    not an error, it is "nothing seeded yet, here is what is registered"
    (`off_timeline`'s own precedent: report rather than refuse). Only the
    fields that need a timeline to mean anything (`timeline_duration`/
    `segments`/`expected_frames`/`expected_duration`/`pack`) are absent;
    `clips`, `undo_depth`, `canvas` and `head`/`tail` are all
    timeline-independent and still answered. TRIAL.md § `timeline_status` is
    the first call an agent makes and it refuses on a fresh project.
    """
    project = Project.open(path)
    if not project.timeline_path.exists():
        return {
            "project": str(project.root),
            "seeded": False,
            "clips": [c["clip_id"] for c in project.read_manifest().get("clips", [])],
            "undo_depth": len(project.snapshots()),
            "canvas": "{}x{}".format(*_mlt_resolution(project)),
            "head": _stored_head(project),
            "tail": _stored_tail(project),
        }
    edit = _load_edit(project)
    rate = _export_fps(_clips_by_id(project))
    expected = _frame_total_with_tail(project, edit, rate)
    stored_pack = _stored_pack(project)
    pack_section = (
        {"applied": False}
        if stored_pack is None
        else {
            "applied": True,
            "name": stored_pack.get("name"),
            "active_variant": stored_pack.get("active_variant"),
            # Named, the way `reel`'s `cues_dropped` is — a count alone
            # is not enough to know which card `card reauthor` needs.
            "stale_cards": [
                r.get("card")
                for r in _card_records(project)
                if r.get("pack_hash") is not None
                and r.get("pack_hash")
                != (stored_pack.get("variants", {}).get(stored_pack.get("active_variant")) or {}).get(
                    "hash"
                )
            ],
        }
    )
    return {
        "project": str(project.root),
        "seeded": True,
        "timeline_duration": edit.duration,
        "segments": len(edit.segments),
        "undo_depth": len(project.snapshots()),
        "clips": [c["clip_id"] for c in project.read_manifest().get("clips", [])],
        "canvas": "{}x{}".format(*_mlt_resolution(project)),
        "head": _stored_head(project),
        "tail": _stored_tail(project),
        "expected_frames": expected,
        "expected_duration": expected / rate,
        "pack": pack_section,
    }


def properties(
    path: Path | str, *, clip_id: str | None = None, word_index: int | None = None
) -> dict[str, Any]:
    """Everything a properties inspector needs, for the project or one selection.

    **Composes only** — every field here is another read-only op's own
    return, assembled rather than re-derived, so this can never disagree
    with the pane it borrowed a number from: `status`, `canvas` and
    `caption_style`'s reports project-wide; `assets`, `reframe` and `cue_ls`
    filtered to one clip when `clip_id` is given.

    `word_index` needs `clip_id` — a cue addresses one clip's own words, so a
    bare word index names nothing. With both: `cue` is the matching entry
    from that same `cue_ls` call, or null when the selected word carries none
    (a selection is not required to already have a cue). When it is null,
    `context` fills in from `get_transcript` instead — the word plus three
    either side, the same echo convention every word-indexed tool uses
    (CLAUDE.md), applied to a selection rather than a mutation. When `cue`
    is not null its own entry already carries that context (`cue_ls`'s
    `_cue_echo`), so `context` is left unset rather than duplicated.
    """
    if word_index is not None and clip_id is None:
        raise ProjectError(
            "word_index needs a clip_id — a cue addresses one clip's own words"
        )

    project = Project.open(path)
    result: dict[str, Any] = {
        "status": status(path),
        "canvas": canvas(path),
        "caption_style": caption_style(path),
    }
    if clip_id is None:
        return result

    media.get_clip(project, clip_id)  # the known-ids message if it doesn't exist
    clip_assets = assets(path)
    result["clip"] = next((c for c in clip_assets["clips"] if c["clip_id"] == clip_id), None)
    clip_reframe = reframe(path)
    result["reframe"] = next(
        (r for r in clip_reframe["clips"] if r["clip_id"] == clip_id), None
    )
    clip_cues = cue_ls(path, clip_id=clip_id)
    result["cues"] = clip_cues["cues"]

    if word_index is None:
        return result

    word_index = int(word_index)
    total_words = len(_transcript(project, clip_id))
    if not 0 <= word_index < total_words:
        raise ProjectError(
            f"word_index {word_index} is out of range for {clip_id!r} "
            f"(has {total_words} words)"
        )
    cue = next((c for c in clip_cues["cues"] if c["word_index"] == word_index), None)
    result["cue"] = cue
    if cue is None:
        lo, hi = max(0, word_index - 3), word_index + 3
        result["context"] = get_transcript(path, clip_id, first=lo, last=hi)
    return result


def _referenced_clip_ids(manifest: dict[str, Any], on_timeline: set[str]) -> set[str]:
    """Every clip_id the timeline, a cue, a hold or the music bed names.

    `on_timeline` is handed in rather than derived from a fresh `Edit` read
    — `finish_report`'s own caller already has `timeline_view`'s `segments`,
    composing rather than re-deriving being the whole discipline that
    function holds to. `asset` and `clip_id` both count on a cue and a hold
    — the addressing clip and the footage actually shown are different
    things (CLAUDE.md's own distinction), and either being a real dependency
    is the point. `finish_report`'s `unused_clips` is everything registered
    that misses this set entirely: on no lane, cued nowhere, held nowhere,
    not the bed.
    """
    referenced: set[str] = set(on_timeline)
    for cue in manifest.get("cues", []):
        referenced.add(cue["clip_id"])
        referenced.add(cue["asset"])
    for hold in [*manifest.get(HOLDS_KEY, []), *manifest.get(UNDER_VO_KEY, [])]:
        referenced.add(hold["clip_id"])
        referenced.add(hold["asset"])
    bed = manifest.get(MUSIC_KEY)
    if bed:
        referenced.add(bed.get("clip_id"))
        referenced.update(_music_assets(bed))
    referenced.discard(None)
    return referenced


def finish_report(
    path: Path | str, *, framing: bool = False, holds: bool = False, continuity: bool = False
) -> dict[str, Any]:
    """The truth strip's own numbers, and the Finish mode report behind it.

    **Composes only**, `properties`'s own precedent (docs/plans/STUDIO.md § Step 01):
    every field is another read-only op's whole return, or a plain
    filter/membership-test/sum over one — no new derivation, no new
    subprocess, no new arithmetic. In particular the preset/canvas
    compatibility check is not re-implemented here: `_check_preset_canvas`
    is the same helper `export` itself calls, caught per preset rather than
    let to propagate, so a refusal is reported the way `timeline_view`
    already reports a `shots_error` — never raised.

    `duration` is `status`'s own numbers, split into the edit's bare length,
    the configured tail (0.0 with none), and their sum — `expected_duration`,
    already `_frame_total_with_tail`'s answer (which now also folds in a
    configured head — see `status`), so this does not re-add them.

    `canvas` is the project's current shape plus, for every preset that
    claims a geometry (`EXPORT_PRESETS`, minus `custom` — it names no fixed
    shape to check), whether this project's canvas would clear it.

    `captions` reports whether a style is configured, that style's own font
    report (embedded verbatim — `caption_style`'s `font` field, not
    restated), and what the *last render* did about burning: `"unknown"`
    with no render log at all or a log whose last run never records a `burn`
    stage, `"yes"`/`"no"` off that stage's own `outcome`. This is the one
    field docs/plans/STUDIO.md is emphatic about: a manifest can say captions are
    configured while nothing on disk was ever burned, and this is the field
    that stops that from reading as clean.

    `picture` is the cue table's own count, how many of those cues pin an
    in-point, and `timeline_view`'s `shots_error` passed through unchanged —
    a stale or orphaned cue must not raise here, because this report is one
    of the places a person finds out about it.

    `sources` is how many clips are registered and which of them ffprobe read
    as variable frame rate at import — informational, never a flag. The
    recorded lean is not to transcode (PLAN.md § Open questions, *Variable
    frame rate footage*), so nothing in the window clears it; what it buys is
    that when a phone or screen recording *does* misbehave, the condition
    already has a name on the record. Unlike everything else here it reads
    the manifest's clip rows directly rather than `assets`, because `assets`
    probes playability per clip and this report rides every edit.

    `unused_clips` is which registered clips the timeline, a cue, a hold and
    the music bed all miss — on no lane, cued nowhere, held nowhere, not the
    bed (TRIAL.md § Registered-and-not-on-the-timeline has no report of its
    own; `assets`' own `cues` count answers half this question per clip and
    never names the ones at zero). Informational like `sources`, and unlike
    `sources` an action genuinely clears it — `clip_rm` when nothing else is
    keeping the clip, cueing it otherwise — so it is not folded into `flags`
    only because nothing here decides *which* of those two the clip needs.

    `marks` is `unspoken_ls`'s count split into applied vs stale (a stale
    mark's recorded text disagrees with the transcript now — see
    `unspoken_ls`). `seams` sums `transcript_checks`' own `overlaps` finding
    across every clip.

    `framing` is `reframe_coverage`'s own `stale_seconds`/`stale_stretches`
    and `len(steps)` (Studio Step 03) — **and it is off by default, because
    it is the one expensive thing in here.** "No face detector" is not the
    same as cheap: it decodes placed footage for a scene-cut scan, measured
    at 5.7s wall and 46s of CPU on the film, every call, uncached. The truth
    strip re-reads this op on every `project-changed` event — i.e. after
    every cut — so composing it in unconditionally made each edit pay six
    seconds for a number nothing on screen had asked to change. `framing`
    is `None` when it was not asked for, which is deliberately distinct from
    a measured zero: a consumer can tell "not measured" from "nothing
    stale", and no framing flag is raised either way.

    `holds` is `hold_check`'s own per-hold seam/transcription report against
    the render `last_render` names — **also off by default**, `framing`'s
    own reasoning restated: it decodes and transcribes render spans, so it
    must not ride every `project-changed` event the truth strip listens to
    (`ops.hold_check` docstring). `None` when not asked for, distinct from a
    project with no holds (which still returns a report, just an empty one)
    — and also `None` when asked for but there is no render to check
    against yet, since a hold's mix is only ever confirmed by listening to
    an actual file.

    `continuity` adds `continuity_check`'s own finding count, split by kind,
    and how many stored marks are currently suppressing one — **also off by
    default**: its `stubs=True` half pays the identical `media.scene_cuts`
    decode `framing` does, so it must not ride every `project-changed` event
    either. `None` when not asked for; a project with no findings still
    returns a report (empty `by_kind`, zero `count`), the `framing` shape.

    `last_render` is the render log's own last `output`, its basename, its
    timestamp, and whether that file is still on disk — `None` when nothing
    has ever rendered here. It is what `GET /api/output` streams and what the
    Finish pane offers to play: the window rebuilds the picture live and
    never reads `renders/`, so before this the Export button wrote a file the
    page could not open or even name (PLAN.md § Should the workspace play its
    own output?). This answers only the narrow half of that question — *the
    file this flow just made* — and deliberately not the general one: there
    is no listing of `renders/` here and no way to name a different file.

    `worst_offset` is not composed in at all: it only exists on a
    `reframe_sheet(extremes=True)` row, an opt-in job this function cannot
    block on and cannot read a stale answer for (`cache/sheets` is wiped
    every run), and it has no action that reliably clears it to zero — a
    static rect over a moving subject has an irreducible worst moment.

    `flags` is the truth strip's warning list, and the test every entry has to
    pass is **that an action in the window can clear it**. Six conditions do:
    a styled project with no render log confirming a burn, a styled project
    whose last render skipped the burn, a `shots_error`, a stale unspoken
    mark, stale framing held over a cut, and an unexplained window step.
    What is deliberately *not* a flag — a refusing preset, a seam count, an
    unstyled project's unknown burn state, `worst_offset` — is argued at the
    flag list itself (and at `framing` above, for `worst_offset`); each is
    permanent, and a permanent flag is a count that can never reach zero.
    Nothing here invents a further condition either: there is no
    duration-mismatch flag, and font resolution is informational only (only
    a measured render settles which face libass drew — CLAUDE.md).
    **Refuses on a project with nothing seeded** — unlike `status` itself
    (which now reports `seeded: false` rather than raising, TRIAL.md §
    `timeline_status`), there is no finished cut to report on yet, and
    `timeline_view` below still needs one regardless.
    """
    project = Project.open(path)
    proj_status = status(path)
    if not proj_status["seeded"]:
        raise ProjectError(
            f"{project.root} has no timeline yet — finish_report reports on a "
            "finished cut, and there is nothing to finish. Seed it first "
            "(`proofcut seed` / seed_timeline)."
        )
    tail = proj_status["tail"]
    duration_section = {
        "edit_seconds": proj_status["timeline_duration"],
        "tail_seconds": tail["seconds"] if tail else 0.0,
        "total_seconds": proj_status["expected_duration"],
    }

    presets: dict[str, dict[str, Any]] = {}
    for preset_name in EXPORT_PRESETS:
        # `needs` and `fix` are the refusal's own parts, carried on its type
        # (`PresetCanvasError`) so the window never splits `message` to get a
        # short line out of it. `None` on a preset that passes.
        try:
            _check_preset_canvas(project, preset_name)
        except PresetCanvasError as exc:
            presets[preset_name] = {"ok": False, "message": str(exc), "needs": exc.needs, "fix": exc.fix}
        except ProjectError as exc:
            # Anything else the check's own reads raise: still a refusal, with no
            # short form to offer, so the card shows the message whole.
            presets[preset_name] = {"ok": False, "message": str(exc), "needs": None, "fix": None}
        else:
            presets[preset_name] = {"ok": True, "message": None, "needs": None, "fix": None}
    canvas_section = {"canvas": proj_status["canvas"], "presets": presets}

    configured = CAPTION_STYLE_KEY in project.read_manifest()
    font = caption_style(path)["font"] if configured else None
    run = renderlog.last(project)
    if run is None:
        burned = "unknown"
    else:
        burn_stage = run.get("stages", {}).get("burn")
        if burn_stage is None:
            burned = "unknown"
        elif burn_stage.get("outcome") == "done":
            burned = "yes"
        else:
            burned = "no"
    captions_section = {"configured": configured, "font": font, "burned": burned}

    # The file the last pipeline run produced, so a caller can say what the
    # window just made — and, behind `GET /api/output`, play it. `exists` is
    # measured rather than assumed: the log records what a run wrote, and a
    # render deleted afterwards would otherwise be offered as watchable.
    # `None` with no log at all, which is the same "nobody has rendered here"
    # that leaves `burned` unknown.
    if run is None:
        last_render_section: dict[str, Any] | None = None
    else:
        out_path = Path(str(run.get("output") or ""))
        last_render_section = {
            "output": str(out_path),
            "name": out_path.name,
            "exists": out_path.is_file(),
            "timestamp": run.get("timestamp"),
        }

    view = timeline_view(path)
    cues = cue_ls(path)
    # `cue_ls`'s own entries echo the raw cue's `src_start` — the pin itself
    # (`cue_add`'s `src_start` kwarg, stored under that same key; see
    # `cue_add`'s docstring: "`src_start` pins the in-point"). `src_pin` is
    # only ever a *derived* field name, on a `build_shots`/`timeline_view`
    # shot dict — it does not exist on a `cue_ls` entry, so `pinned_count`
    # reads `src_start` here rather than a key `cue_ls` never returns.
    pinned_count = sum(1 for cue in cues["cues"] if cue.get("src_start") is not None)
    picture_section = {
        "cue_count": cues["count"],
        "pinned_count": pinned_count,
        "shots_error": view.get("shots_error"),
    }

    marks = unspoken_ls(path)
    marks_section = {
        "applied": marks["count"] - marks["stale"],
        "stale": marks["stale"],
    }

    checks = transcript_checks(path)
    seams_section = {"count": sum(len(c["overlaps"]) for c in checks["clips"])}

    # Which registered clips were probed as variable-frame-rate at import
    # (PLAN.md § Open questions, *Variable frame rate footage*). **Reported,
    # never flagged**: the recorded lean is not to transcode — cut-and-concat
    # works in the time domain, where VFR is mostly fine — so there is no
    # action in the window that clears this, and a permanent flag is a count
    # that can never reach zero (the flag list's own rule, below). What it
    # buys is that when something *is* off on a phone or screen recording,
    # the condition already has a name on the record instead of being a
    # mystery. Normalising on NLE export is deliberately still open.
    #
    # Read off the manifest's own clip rows rather than composed from
    # `assets`, which is the shape the rest of this function uses: `assets`
    # runs `media.playability` per clip, and that is an ffprobe subprocess
    # each — this report rides every `project-changed` event, so composing it
    # in would make every cut pay a probe per clip for a field that cannot
    # change without a re-import. Same reasoning as `framing` being opt-in,
    # applied one level cheaper. `vfr` is absent on every clip imported
    # before the field existed, and `.get` reads that as "not variable",
    # which is what an older manifest meant.
    manifest = project.read_manifest()
    clip_rows = manifest.get("clips", [])
    sources_section = {
        "clips": len(clip_rows),
        "vfr": [c["clip_id"] for c in clip_rows if c.get("vfr")],
    }

    # A clip on no lane and cued/held/bedded nowhere is quiet everywhere
    # else: it counts toward `sources.clips` above and shows up in `assets`
    # exactly like one doing real work. TRIAL.md § Registered-and-not-on-
    # the-timeline has no report of its own — this is that report, so a
    # clip registered by accident (or for `spot_frames`-style inspection and
    # never cleaned up, `clip_rm`'s own reason for existing) is visible
    # without reading an agent's prose to notice it.
    on_timeline = {seg["clip_id"] for seg in view.get("segments", [])}
    referenced = _referenced_clip_ids(manifest, on_timeline)
    unused_clips = [c["clip_id"] for c in clip_rows if c["clip_id"] not in referenced]

    # Cheap on purpose — `reframe_coverage` needs no face detector (ffmpeg
    # scene-cut scan only, bounded by placed footage), so this composed field
    # costs finish_report nothing beyond what the Frame view's own coverage
    # chips already pay for separately. `worst_offset` is deliberately not
    # composed in here: it only exists on a `reframe_sheet(extremes=True)`
    # row, an opt-in job this function cannot block on and cannot read a
    # stale answer for either (`cache/sheets` is wiped every run) — a flag
    # that is sometimes silently unavailable is a coin flip, not a flag. It
    # also has no action that reliably clears it to zero: a static rect over
    # a moving subject has an irreducible worst moment, unlike `stale_seconds`
    # and `steps`, which genuinely reach 0 once every flagged stretch/window
    # is reframed (CLAUDE.md § Per-shot framing).
    #
    # An audio-only project — the ordinary case throughout this repo — has no
    # footage placements for a window to apply to, and `reframe_coverage`
    # says so by raising rather than by returning zeroes (it is a report
    # about placed footage, and there is none to report on). That is not a
    # defect in this project for this report to surface: `check_frames` and
    # `check_black` already treat "nothing to check" as the ordinary VO case
    # rather than a failure, and this follows the same precedent — an empty
    # `framing` section, no flags, never a raise that would take the whole
    # report down over a project that was never going to have any picture.
    #
    # `_PICTURE_REFUSALS`, not bare `ProjectError`: `reframe_coverage` walks
    # the same `_picture_plan`/`build_shots` a stale or orphaned cue already
    # makes `timeline_view` refuse (`picture_section["shots_error"]`,
    # above) — and that refusal is a `tl.TimelineError`, not a `ProjectError`.
    # `picture_section` already reports that exact condition without raising;
    # this section must fail the same way over the same project, or a stale
    # cue would make `finish_report` raise from here while its own
    # `shots_error` field claims nothing is wrong.
    framing_section: dict[str, Any] | None = None
    if framing:
        try:
            coverage = reframe_coverage(path)
        except _PICTURE_REFUSALS:
            framing_section = {"stale_seconds": 0.0, "stale_stretches": 0, "steps": 0}
        else:
            framing_section = {
                "stale_seconds": coverage["stale_seconds"],
                "stale_stretches": coverage["stale_stretches"],
                "steps": len(coverage["steps"]),
            }

    # `hold_check`'s own report against the last render — `framing`'s own
    # opt-in reasoning: it decodes and transcribes render spans, so it must
    # not ride every `project-changed` event. `None` when not asked for
    # (distinct from a measured "no holds"), and also `None` when there is
    # no render on disk yet to check against — a hold's mix is confirmed by
    # listening to a file, not by reading the project.
    holds_section: dict[str, Any] | None = None
    if holds and last_render_section is not None and last_render_section["exists"]:
        holds_section = hold_check(path, last_render_section["output"])

    # `continuity_check`'s own numbers — `framing`'s own opt-in reasoning
    # restated: `stubs=True` pays the identical scene-cut decode cost, so it
    # must not ride every `project-changed` event either. `continuity_check`
    # already reports `shots_error` rather than raising over a stale/orphaned
    # cue (the same refusal `picture_section` above already surfaces), but a
    # stub scan's own `media.scene_cuts` can still raise on an asset that
    # will not decode — `reframe_coverage`'s own uncaught failure mode, which
    # is why `framing` above needs the same `except _PICTURE_REFUSALS` net.
    continuity_section: dict[str, Any] | None = None
    if continuity:
        try:
            continuity_report = continuity_check(path)
        except _PICTURE_REFUSALS:
            continuity_section = {"count": 0, "by_kind": {}, "accepted": 0}
        else:
            by_kind: dict[str, int] = {}
            for finding in continuity_report["findings"]:
                by_kind[finding["kind"]] = by_kind.get(finding["kind"], 0) + 1
            continuity_section = {
                "count": continuity_report["count"],
                "by_kind": by_kind,
                "accepted": continuity_report["accepted"],
            }

    # A flag is an *open item* — something an action in the window can clear.
    # Three candidates were measured against the real film on 2026-08-17 and
    # deliberately left out, because each of them is permanent and a guard that
    # has to be suppressed every time is the thing to fix rather than the thing
    # to document (CLAUDE.md, `cut_by_time`'s own precedent):
    #
    #   * **A refusing preset is not a project defect.** `tiktok-reels` refuses
    #     every 16:9 film for as long as it stays 16:9, so flagging it says the
    #     film is wrong for having chosen landscape. The refusal is drawn where
    #     it can be acted on — on the preset's own card, with its fix
    #     (docs/plans/STUDIO.md § Step 01, mockup screen 04 callout 1) — never here.
    #   * **A seam count is a property of the recording**, not of the edit: 40
    #     of them in the film, unchanged by anything the window can do. The
    #     warning-class question is how many sit near a *kept* edge, and
    #     docs/plans/STUDIO.md forbids inventing a nearness rule for it, so the total is
    #     reported under `seams` and flagged nowhere.
    #   * **An unstyled project has no burn to confirm.** `burned: "unknown"`
    #     is still reported for one — honesty about the render log costs
    #     nothing — but it only becomes a flag once a style exists, which is
    #     the shape the captionless-film incident actually had.
    flags: list[dict[str, str]] = []
    if captions_section["configured"] and captions_section["burned"] == "unknown":
        flags.append(
            {
                "kind": "captions",
                "message": (
                    "captions are styled, but no render log says whether any "
                    "render ever burned them in"
                ),
                "mode": "finish",
            }
        )
    if captions_section["configured"] and captions_section["burned"] == "no":
        flags.append(
            {
                "kind": "captions",
                "message": "captions are configured but the last render did not burn them in",
                "mode": "finish",
            }
        )
    if picture_section["shots_error"] is not None:
        flags.append(
            {
                "kind": "picture",
                "message": picture_section["shots_error"],
                "mode": "finish",
            }
        )
    if framing_section is not None and framing_section["stale_seconds"] > 0:
        # An override held over a cut — reframing the specific stretch drives
        # this stretch's contribution to zero, so the aggregate reaches 0
        # once every flagged stretch is reframed (unlike `worst_offset`,
        # excluded above).
        flags.append(
            {
                "kind": "framing",
                "message": (
                    f"{framing_section['stale_seconds']}s of picture is held over "
                    "from a different shot's framing across a cut"
                ),
                "mode": "frame",
            }
        )
    if framing_section is not None and framing_section["steps"] > 0:
        # A window boundary inside a placement with no cut behind it — "the
        # one a viewer notices" (CLAUDE.md § Per-shot framing).
        flags.append(
            {
                "kind": "framing",
                "message": (
                    f"{framing_section['steps']} window boundary(ies) with no cut "
                    "behind them — the frame will visibly jump"
                ),
                "mode": "frame",
            }
        )
    if marks_section["stale"] > 0:
        # `unspoken_ls`'s own meaning of stale: the recorded text and the
        # current transcript text disagree, i.e. the transcript was replaced
        # under the mark. It is kept rather than applied precisely so a
        # re-transcribe surfaces as a list to re-check — so the message says
        # that, and not "never applied to a render", which describes a
        # different (and non-existent) failure.
        flags.append(
            {
                "kind": "marks",
                "message": (
                    f"{marks_section['stale']} unspoken mark(s) no longer match "
                    "the transcript under them — re-check before the next render"
                ),
                "mode": "finish",
            }
        )
    if continuity_section is not None and continuity_section["by_kind"].get("rewind"):
        # Actionable and reaches zero on a fix, `framing`'s own test:
        # re-cueing the shot clears it. `replay`/`short_shot` are deliberately
        # not flagged — a replay is reported never refused precisely because
        # a rhyme and a mistake look identical from the cue table, so calling
        # it a defect would be wrong as often as it is right, and a short
        # shot is routinely a deliberate fast cut.
        flags.append(
            {
                "kind": "continuity",
                "message": (
                    f"{continuity_section['by_kind']['rewind']} shot(s) rewind behind "
                    "where their own footage last played"
                ),
                "mode": "finish",
            }
        )
    if continuity_section is not None and continuity_section["by_kind"].get("stub"):
        flags.append(
            {
                "kind": "continuity",
                "message": (
                    f"{continuity_section['by_kind']['stub']} shot(s) end on a real cut "
                    "inside their own footage — likely trimmed to a fragment"
                ),
                "mode": "finish",
            }
        )

    return {
        "duration": duration_section,
        "canvas": canvas_section,
        "captions": captions_section,
        "picture": picture_section,
        "marks": marks_section,
        "seams": seams_section,
        "sources": sources_section,
        "unused_clips": unused_clips,
        "framing": framing_section,
        "holds": holds_section,
        "continuity": continuity_section,
        "last_render": last_render_section,
        "flags": {"count": len(flags), "items": flags},
    }


def _placed_segments(edit: tl.Edit) -> list[dict[str, Any]]:
    """Every segment with its timeline coordinates alongside its source ones.

    `Edit.segments` carries source time only — where a segment *plays* is the
    running sum of everything before it, which is the arithmetic every cut
    invalidates and every caller otherwise redoes.
    """
    placed = []
    offset = 0.0
    for seg in edit.segments:
        placed.append({**seg.as_dict(), "timeline_start": offset, "timeline_end": offset + seg.duration})
        offset += seg.duration
    return placed


#: `paragraph` break tuning (PLAN.md § Read-model additions). The word-count
#: arm is the guarantee, independent of timing; the gap arm is opportunistic
#: and may break earlier but is never required to.
PARAGRAPH_MIN_WORDS = 40
PARAGRAPH_GAP_MIN_WORDS = 15
PARAGRAPH_GAP_SILENCE = 0.75

#: Daydream's own threshold (docs/plans/DAYDREAM.md § Transcript document — "theirs
#: show down to 0.4s"). Below it a gap renders as nothing, which is the
#: correct reading of an ordinary breath, not a state to hide.
PAUSE_MARKER_MIN = 0.4


def _gap_after(words: Sequence[tx.Word], i: int) -> float | None:
    """Seconds between `words[i]`'s end and the next word's start; None at the
    transcript's last word. The one place both `_paragraphs`' opportunistic
    break and `_word_placements`' pause marker read a gap from — which is
    what makes the duration-inflation asymmetry true for both without
    re-arguing it twice: whisper inflates the *duration* of the word after a
    swallowed retake, which only ever pushes that word's end later, which can
    only shrink this gap, never widen it. A bad transcript can suppress a
    paragraph break or a pause marker (cosmetic); it can never invent one.
    """
    if i + 1 >= len(words):
        return None
    return words[i + 1].start - words[i].end


def _paragraphs(words: Sequence[tx.Word]) -> dict[int, int]:
    """Which paragraph each word belongs to, word-order-driven (CLAUDE.md).

    Breaks after a sentence-ending word (`.`, `?`, `!`) once the current
    paragraph holds `PARAGRAPH_MIN_WORDS` — that is the guarantee, and it
    fires regardless of timing. A silence of `PARAGRAPH_GAP_SILENCE` after a
    sentence end may break earlier, once the paragraph already holds
    `PARAGRAPH_GAP_MIN_WORDS` — but that arm is opportunistic, not a promise.

    The asymmetry that makes the gap arm safe: whisper inflates the *duration*
    of the word following a swallowed retake, which only ever pushes that
    word's `end` later — and a later `end` can only *shrink* the measured gap
    to the next word's `start`, never widen it. A bad transcript can therefore
    only suppress an early break (an ugly paragraph); it cannot invent one
    (a lie about where a sentence ended).
    """
    assigned: dict[int, int] = {}
    current = 0
    count = 0
    for i, word in enumerate(words):
        assigned[word.index] = current
        count += 1
        if i + 1 >= len(words) or not word.text.rstrip().endswith((".", "?", "!")):
            continue
        if count >= PARAGRAPH_MIN_WORDS:
            current += 1
            count = 0
        elif count >= PARAGRAPH_GAP_MIN_WORDS:
            gap = _gap_after(words, i)
            if gap is not None and gap >= PARAGRAPH_GAP_SILENCE:
                current += 1
                count = 0
    return assigned


def _word_placements(edit: tl.Edit, clip_id: str, parsed: tx.Transcript) -> list[dict[str, Any]]:
    """Each word, with whether it survived the edit and where it now plays.

    Survival is an **overlap** test, never containment (CLAUDE.md): whisper
    inflates the duration of the word following a swallowed retake, so a word
    routinely straddles a cut edge and survives in part. `covered` is how much
    of it is left and `partial` says so out loud, because a word drawn as
    simply "kept" when half of it is gone is the same lie the containment test
    told.

    The timeline coordinates come from `Edit.timeline_span` — the singular,
    first-survivor form, which is right here for the same reason it is right
    for captions: one word wants one place to be highlighted, not a list.

    `paragraph` (see `_paragraphs`) is computed over every word in transcript
    order, cut or not — it is a property of the document, not of the edit.

    `pause_after` (see `_gap_after`, `PAUSE_MARKER_MIN`) is present as a key
    only when the gap to the next word clears the marker threshold — never a
    bare boolean, never an always-present number, so the threshold lives in
    exactly one place (here) and the front end never carries a second copy of
    it. Its `present` flag answers "does the pause itself still play", via
    the identical `timeline_span` overlap test used for the word two lines
    above — so a caller never has to infer a pause's survival from its
    flanking words' own `present` flags, which can disagree with it (e.g. a
    `cut_by_time` call that removed only the silence).
    """
    suspect = {item["index"]: item for item in _suspect_durations(parsed)}
    paragraphs = _paragraphs(parsed.words)
    placements = []
    for i, word in enumerate(parsed.words):
        # A zero-width word is not a range, so `timeline_span`'s `b > a` test
        # would report it cut wherever it actually sits. Locate the instant —
        # with the segment's end boundary counted as inside it, because the
        # last word of a transcript routinely sits exactly on the end of the
        # last segment and the half-open test calls that "cut". It is the one
        # place `closed_end` is correct; `timeline.timeline_time` says why.
        if word.end > word.start:
            span = edit.timeline_span(clip_id, word.start, word.end)
            covered = edit.covers(clip_id, word.start, word.end)
        else:
            at = edit.timeline_time(clip_id, word.start, closed_end=True)
            span = None if at is None else (at, at)
            covered = 0.0
        item: dict[str, Any] = {
            **word.as_dict(),
            "present": span is not None,
            "covered": covered,
            "partial": span is not None and (word.end - word.start) - covered > tl.MIN_SEGMENT,
            "timeline_start": span[0] if span else None,
            "timeline_end": span[1] if span else None,
            "paragraph": paragraphs[word.index],
        }
        if word.index in suspect:
            item["suspect"] = suspect[word.index]
        gap = _gap_after(parsed.words, i)
        if gap is not None and gap >= PAUSE_MARKER_MIN:
            nxt = parsed.words[i + 1]
            pause_span = edit.timeline_span(clip_id, word.end, nxt.start)
            item["pause_after"] = {"duration": gap, "present": pause_span is not None}
        placements.append(item)
    return placements


def _seams(edit: tl.Edit, clip_id: str, placements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cut boundaries, named by the words either side of each one.

    A seam is where two segments meet: material was removed between them and
    the hole closed, so it is a single instant on the timeline and a *gap* in
    the source. Naming it by word index rather than by timeline second is the
    whole property this project defends — a later cut moves the second and
    leaves the words alone.

    The words are looked up in **timeline** coordinates, among the survivors
    only. Asking the transcript what sits at the seam's source time answers
    with the word that was *removed* — the cut starts exactly where the
    outgoing segment ends — which is the one word a person reading across the
    join will not hear.
    """
    survivors = [w for w in placements if w["present"]]
    seams = []
    offset = 0.0
    for before, after in pairwise(edit.segments):
        offset += before.duration
        if before.clip_id != after.clip_id:
            # Not a cut in one recording; it is a join between two of them,
            # and "the words either side" would be from different transcripts.
            continue
        seam: dict[str, Any] = {
            "timeline_time": offset,
            "clip_id": before.clip_id,
            "source_end": before.end,
            "source_start": after.start,
            "removed": after.start - before.end,
        }
        if before.clip_id == clip_id:
            heard_before = [
                w for w in survivors if w["timeline_end"] <= offset + tl.MIN_SEGMENT
            ]
            heard_after = [
                w for w in survivors if w["timeline_start"] >= offset - tl.MIN_SEGMENT
            ]
            if heard_before:
                seam["before"] = {"index": heard_before[-1]["index"], "text": heard_before[-1]["text"]}
            if heard_after:
                seam["after"] = {"index": heard_after[0]["index"], "text": heard_after[0]["text"]}
        seams.append(seam)
    return seams


def timeline_view(
    path: Path | str,
    clip_id: str | None = None,
    *,
    first: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The whole edit in one payload: segments, seams, and every word's fate.

    `first`/`limit` window the `words` list only (`window_list`); the lanes
    are always whole. The window never passes them.

    The read model behind `proofcut web` (HISTORY.md § The preview/timeline web UI). It exists as an op
    rather than inside the server because a view that computed word survival
    itself would be a second implementation of the overlap test, and the front
    ends are meant to hold no logic of their own — the same rule that keeps
    the CLI and the MCP server in parity.

    `clip_id` defaults to whichever clip the timeline actually opens with,
    which is the one clip a single-track edit almost always has. A clip with
    no transcript still returns segments and seams; `words` is null and
    `transcript_missing` is set, matching `locate`'s policy rather than
    refusing a valid question about a picture-only clip.

    `off_timeline` is set when the addressed clip is registered but not in the
    edit — `segments` is then the timeline's own material under someone else's
    `clip_id`, and a transcribed clip's `words` all read `present: false`,
    which is indistinguishable from a clip that was cut in its entirety. Same
    report-rather-than-refuse policy as `transcript_missing` above: the
    question is valid (a footage clip a cue points at is legitimately not on
    the edit's track) and the answer is a fact about it, not an error.

    `layered` says whether this timeline names more than one source — a cue
    table or a second clip — which is what decides whether `export` writes and
    renders it through MLT/melt or hands it to auto-editor. It is reported here
    rather than recomputed by a front end for the usual reason: the answer is
    what routes around a silent failure, and a second implementation of it
    would be a second chance to get it wrong.

    `shots` is the picture lane — step 6 of the layered timeline, and the field
    the web UI's V2 lane is drawn from. It is `_picture_plan`'s answer, not
    `build_shots`'s: the lane may not draw a shot `export` would refuse, so the
    projection goes through the MLT writer's planner before it is reported. Two
    consequences a reader should expect:

    * `shots` is null for a project with no cues — there is no picture lane
      then, only the edit's own track — and null with a `shots_error` when the
      plan refused. **A refusal is reported, not raised**: a stale cue must not
      take the whole view down with it, because the view is how a person finds
      the cue to fix. It is the one thing here that answers with a message
      instead of an answer, and the front end is expected to draw the message.
    * `shots_rate` is the frame grid the shots were quantised on, which is
      `export`'s rate (`_export_fps`) and **not** `timebase` — an audio-only
      project's timebase is milliseconds, and the picture is not.

    `music` is the A2 lane's projection — the bed's cue resolved through
    `_music_plan`, the same derivation `export` builds its lane from, so a
    front end gates a music lane on what the render will actually carry
    rather than on the manifest key alone (PLAN.md § The A2 music lane,
    step 5). Null with no bed; null with a `music_error` when the bed cannot
    resolve — an orphaned boundary word, `shots_error`'s policy exactly.

    **`segments`/`shots`/`seams` stay Edit-relative even with a head
    configured** — `locate`'s own two-clock rule (see its docstring): the web
    player cannot play a cold open yet, and shifting this view's clock would
    desync it from the timeline it draws. `head_seconds` is the offset a
    render-time reader needs (0.0 with none — the sum this view has never
    had to add before); `head` is the stored config plus its resolved frame
    count, `_build_mlt`'s own `head`/`tail` reporting shape.

    `canvas` and `reframe` are the frame, and they are here so a preview can
    draw the shape the render declares instead of the shape its media happens
    to be — step 4 of PLAN.md § Aspect swap. `canvas` is `_mlt_resolution`,
    the profile's own number. `reframe` maps a clip to `dest`, **where its
    whole source frame lands on that canvas**, in canvas pixels: the same
    `Reframe.dest_rect` the MLT writer turns into a `qtblend` rect, so a
    front end places media by reading it rather than by re-deriving a crop.
    A clip whose reframe changes nothing still gets an entry, and it is the
    contain placement — one path draws both, and neither is the front end's
    own arithmetic. A stale stored rect comes back as `reframe_error`, for
    `shots_error`'s reason: the view is how a person finds the rect to fix.

    **Each shot carries its own `dest`, and the picture layer draws that one.**
    `reframe[clip].dest` is the head window, which is the edit track's answer
    and only accidentally the picture lane's: framing is addressed in source
    seconds, so two placements of one clip can sit under two windows (PLAN.md
    § Per-shot framing). A shot's `dest` is the window its `src_start` reads.
    It is null for a still, which is contained rather than cropped, and null
    for every shot while `reframe_error` stands.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    clips = _clips_by_id(project)

    if clip_id is None:
        clip_id = next((s.clip_id for s in edit.segments), None) or next(iter(clips), None)
    if clip_id is None:
        raise ProjectError("this project has no clips to view")
    clip = media.get_clip(project, clip_id)

    try:
        parsed: tx.Transcript | None = _transcript(project, clip_id)
    except tx.TranscriptError:
        parsed = None

    placements = [] if parsed is None else _word_placements(edit, clip_id, parsed)

    shots_rate = _export_fps(clips)
    shots_error: str | None = None
    try:
        shots, _ = _picture_plan(project, shots_rate)
    except _PICTURE_REFUSALS as exc:
        shots, shots_error = [], str(exc)

    # The A2 lane's projection — the resolved bed the writer would build, so
    # a front end has something to gate a music lane on, the picture lane's
    # own precedent: the lane is drawn only because `export` can render it
    # (PLAN.md § The A2 music lane, step 5). `None` with no bed; a bed that
    # cannot resolve is reported as `music_error`, `shots_error`'s policy —
    # the view is how a person finds the cue to fix.
    music_view: dict[str, Any] | None = None
    music_error: str | None = None
    if project.read_manifest().get(MUSIC_KEY):
        try:
            edit_frames = sum(
                frames for _, frames in autoeditor.frame_layout(edit, shots_rate)
            )
            plan = _music_plan(project, edit, shots_rate, edit_frames=edit_frames)
            if plan is not None:
                music_view = {
                    key: plan[key]
                    for key in (
                        "asset",
                        "clip_id",
                        "word_index_start",
                        "word_index_end",
                        "timeline_start",
                        "timeline_end",
                        "to_end",
                        "music_frames",
                        "padded_frames",
                        "fade_in",
                        "fade_out",
                        "fade_in_frames",
                        "fade_out_frames",
                        "under",
                    )
                }
                music_view["pieces"] = _music_pieces_view(plan["pieces"], shots_rate)
        except (ProjectError, tx.TranscriptError) as exc:
            music_error = str(exc)

    # The holds lane's own projection — `music_view`'s policy, per item
    # rather than once, because a project can hold several: each stored
    # hold's live `_hold_plan` resolution, or `hold_error` inline when it
    # cannot resolve right now (an orphaned cue word, a moved margin that no
    # longer fits) — never raised, so one bad hold cannot take the view down.
    holds_view: list[dict[str, Any]] = []
    if project.read_manifest().get(HOLDS_KEY):
        for stored_hold in _stored_holds(project):
            item: dict[str, Any] = dict(stored_hold)
            try:
                hold_plan = _hold_plan(project, edit, shots_rate, stored_hold)
            except _PICTURE_REFUSALS as exc:
                item["hold_error"] = str(exc)
            else:
                for key in (
                    "src_start",
                    "play_at",
                    "elapsed",
                    "hold_length",
                    "hold_frames",
                    "phrase_start",
                    "phrase_end",
                    "fade_in_frames",
                    "fade_out_frames",
                    "gap_at",
                    "cue_at",
                    "cue_echo",
                ):
                    item[key] = hold_plan[key]
            holds_view.append(item)

    resolution = _mlt_resolution(project)
    reframe_error: str | None = None
    entries: dict[str, mlt.Reframe] = {}
    try:
        entries = _reframe_map(project, resolution)
    except ProjectError as exc:
        reframe_error = str(exc)
    placement = {
        clip_id_: {
            "source": list(entry.source),
            "crop": list(entry.crop),
            "dest": list(entry.dest_rect(resolution)),
            # The lower half when the head window is a stacked split. Both
            # halves or the preview draws one person where the film draws two.
            "pane": (
                list(entry.pane_dest_at(0.0, resolution))
                if entry.pane_dest_at(0.0, resolution)
                else None
            ),
            "crops": not entry.is_identity(resolution),
        }
        for clip_id_, entry in entries.items()
    }

    # A shot carries its *own* placement, because framing is per shot: two
    # placements of one clip read different parts of its source and so can sit
    # under different windows. The picture layer draws this rather than the
    # per-clip `reframe` entry, which is the head window and right only for
    # the edit's own track. `null` for a still — a card is re-authored at the
    # canvas, never cropped (`mlt.document`).
    for shot in shots:
        found = entries.get(str(shot.get("asset")))
        drawn = found is not None and not shot.get("is_image")
        at = float(shot.get("src_start") or 0.0)
        shot["dest"] = list(found.dest_rect_at(at, resolution)) if drawn else None
        # A shot the render draws as two half-height panes carries both, and
        # `dest` above is already the upper one. Null is the ordinary case.
        pane = found.pane_dest_at(at, resolution) if drawn else None
        shot["dest_pane"] = list(pane) if pane else None

    head_cfg = _stored_head(project)
    head_view = {**head_cfg, "frames": _head_frames(project, shots_rate)} if head_cfg else None

    result: dict[str, Any] = {
        "project": str(project.root),
        "name": project.read_manifest().get("name", project.root.name),
        "clip_id": clip_id,
        "clips": [
            {
                "clip_id": c["clip_id"],
                "duration": c.get("duration"),
                "has_video": bool(c.get("has_video")),
                "has_transcript": project.transcript_path(c["clip_id"]).exists(),
            }
            for c in clips.values()
        ],
        "source_duration": clip.get("duration"),
        "timeline_duration": edit.duration,
        "timebase": _rate(project),
        "undo_depth": len(project.snapshots()),
        "layered": _is_layered(project, edit),
        "canvas": list(resolution),
        "reframe": placement,
        "shots": shots or None,
        "shots_rate": shots_rate,
        "music": music_view,
        # [] with no holds, each entry the stored record plus its
        # live-resolved fields (or `hold_error` when it cannot resolve right
        # now) — `music_view`'s policy, per item.
        "holds": holds_view,
        # Ruling: this view stays Edit-relative — `segments`/`shots`/`seams`
        # below are unchanged by a configured head, because the web player
        # cannot play one yet and shifting this view's clock would desync it
        # from the timeline it draws. `head_seconds` is the offset a future
        # render-time reader needs (0.0 with none); `head` is the stored
        # config plus its resolved frame count, `_build_mlt`'s own
        # `head`/`tail` reporting shape, so a front end can draw *that* a
        # head exists without yet drawing where it plays.
        "head_seconds": _head_seconds(project),
        "head": head_view,
        "segments": _placed_segments(edit),
        "seams": _seams(edit, clip_id, placements),
    }
    if shots_error is not None:
        result["shots_error"] = shots_error
    if reframe_error is not None:
        result["reframe_error"] = reframe_error
    if music_error is not None:
        result["music_error"] = music_error
    # A clip can be registered, transcribed, and still not be in the edit — and
    # then every one of its words comes back `present: false`, which is exactly
    # what a clip somebody cut entirely looks like. Reported rather than left to
    # be derived off `segments[].clip_id`, on `transcript_missing`'s own
    # precedent and for this function's own stated reason: a front end that
    # re-implements the test is a second chance to get it wrong, and without the
    # field the advice it gives is actively wrong — `transcribe` does not put a
    # clip on the timeline, and following that advice lands you in the *worse*
    # state, a full transcript struck through as though you had cut it.
    # Computed off `edit.segments` rather than `result["segments"]` so the flag
    # and the lane can never disagree about the same question.
    if all(segment.clip_id != clip_id for segment in edit.segments):
        result["off_timeline"] = True
    if parsed is None:
        result["words"] = None
        result["transcript_missing"] = True
    else:
        result["words"] = placements
    return window_list(result, "words", first, limit)


def _default_clip_id(project: Project, clips: dict[str, dict[str, Any]]) -> str | None:
    """The clip a project-level view opens with when none is named.

    Same preference as `timeline_view` — the timeline's own clip, else the
    first registered one — but does not require a timeline to exist, unlike
    `_load_edit`: a clip that has been imported but not yet seeded is still a
    valid thing to ask a waveform about.
    """
    if project.timeline_path.exists():
        found = next((s.clip_id for s in tl.read(project.timeline_path).segments), None)
        if found is not None:
            return found
    return next(iter(clips), None)


#: `ops.waveform`'s return contract, verbatim (PLAN.md § Read-model
#: additions) — the server and web UI stages code against this shape.
_WAVEFORM_FIELDS = ("clip_id", "frame_ms", "rms", "duration_s")


def _cached_waveform(cache_path: Path, stat: Any) -> dict[str, Any] | None:
    """The cached envelope, if `cache_path` still describes the file at `stat`.

    Keyed by size and mtime rather than a hash: cheap to check (no re-read of
    the media) and exactly what `attenuate_noises` or a re-import changes when
    they replace a clip's audio.
    """
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("size") != stat.st_size or payload.get("mtime_ns") != stat.st_mtime_ns:
        return None
    try:
        return {field: payload[field] for field in _WAVEFORM_FIELDS}
    except KeyError:
        return None


# Deliberately no MCP tool (PLAN.md § Read-model additions): the convention
# binds MCP tools to have a CLI subcommand, not the reverse, and 19,000 floats
# is a picture, not something an agent should reason over — `loud_gaps` and
# `unaccounted_sound` already answer the numeric questions about this same
# envelope. CLI only: `proofcut waveform`.
def waveform(path: Path | str, clip_id: str | None = None) -> dict[str, Any]:
    """RMS per 20ms frame for the timeline's waveform lane, normalised to bytes.

    `energy.envelope` is a Python loop — roughly a second of work per 385s of
    8kHz audio, and linear — so the result is cached under `cache/waveform/`,
    keyed by the resolved media file's size and mtime rather than recomputed
    per request. A cache hit never calls `energy.decode`.

    Resolves media through `media.media_path()`, never `root / clip["media"]`
    (CLAUDE.md): the waveform drawn is of the audio that will actually be
    exported, attenuated copy included.

    Each frame's RMS is normalised against the loudest frame in the file, to
    0-255 — the same scale a canvas waveform draws from directly, and a full
    file so the picture does not silently renormalise every time a cut
    changes what is visible.

    Return contract, fixed:
    `{"clip_id": str, "frame_ms": 20, "rms": [0-255 ints], "duration_s": float}`
    """
    project = Project.open(path)
    clips = _clips_by_id(project)
    if clip_id is None:
        clip_id = _default_clip_id(project, clips)
    if clip_id is None:
        raise ProjectError("this project has no clips to measure")
    clip = media.get_clip(project, clip_id)
    source = media.media_path(project, clip)
    if not source.is_file():
        raise ProjectError(f"{clip_id}'s media is missing from disk: {source}")

    stat = source.stat()
    cache_path = project.waveform_path(clip_id)
    cached = _cached_waveform(cache_path, stat)
    if cached is not None:
        return cached

    env = energy.envelope(energy.decode(source))
    peak = max(env) if env else 0.0
    scale = 255.0 / peak if peak > 0 else 0.0
    result: dict[str, Any] = {
        "clip_id": clip_id,
        "frame_ms": round(energy.FRAME * 1000),
        "rms": [min(255, round(v * scale)) for v in env],
        "duration_s": round(len(env) * energy.FRAME, 3),
    }

    project.waveform_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, **result}),
        encoding="utf-8",
    )
    return result


#: `cache/thumbs/` — one directory per clip, mirroring `waveform`'s cache
#: *shape* (a `cache/` subdirectory, keyed by the resolved media's size and
#: mtime) but deliberately not its all-at-once contract. `energy.envelope`
#: decodes the whole file in one linear pass, so caching the whole envelope
#: on first touch costs one decode; `picture.extract_frame` spawns one
#: ffmpeg per sample, so caching an entire multi-minute clip's filmstrip on
#: first touch would cost one process per `THUMB_INTERVAL` seconds of it.
#: Each requested instant is cached independently instead, lazily, the way
#: `_tail_silence` creates its own cache directory on demand.
THUMBS_DIR = "cache/thumbs"

#: Grid spacing a request snaps to, so a timeline lane sweeping past one shot
#: reuses a handful of cached frames instead of extracting a fresh one per
#: pixel of scroll. Coarser than a caption word, fine enough that a filmstrip
#: reads as motion rather than as one still held across a whole clip.
THUMB_INTERVAL = 1.0


def _thumb_dir(project: Project, clip_id: str) -> Path:
    return project.root / THUMBS_DIR / clip_id


def _thumb_key_path(project: Project, clip_id: str) -> Path:
    """One key file per clip — `proxy_key_path`'s shape, not `waveform`'s:
    cheap to check, and a source change invalidates every bucket without
    deleting any of them. Each stale bucket is only actually re-extracted the
    next time something asks for it."""
    return _thumb_dir(project, clip_id) / "_source.json"


def thumbnail(
    path: Path | str, clip_id: str, at: float, *, interval: float = THUMB_INTERVAL
) -> dict[str, Any]:
    """One filmstrip frame for `clip_id`, at the source time nearest `at`.

    **Deliberately addressed in source time, not by index or by timeline
    position.** docs/plans/DAYDREAM.md's filmstrip lane is "drawn through the edit the
    same way the waveform maps timeline->source slices" — the caller (a
    timeline lane walking `Edit`'s segments) already has a source second in
    hand, and this is the primitive it needs from there (CLAUDE.md: reloading
    each asset from its head looks right and is a different film — the same
    reason the picture layer previews from `src_start` rather than 0). `at`
    snaps to a multiple of `interval` before anything is read or written, so
    a lane sweeping across one shot asks for the same handful of buckets
    rather than a new one per pixel.

    **Containment.** The result is a path under `cache/thumbs/`, never
    written into the manifest and never resolved by `media.media_path` or
    `media.preview_path` — nothing downstream of an edit (`export`, `verify`,
    `check_frames`) can reach it, because none of them call this function or
    read anywhere near where it writes. It is a picture *of* the source, not
    a source, and the only paths to its bytes are this function and the web
    route that calls it (`webui._send_thumb`) — a third caller resolving it
    into anything render-facing would be the whole hole, the same one
    `media.preview_path`'s docstring names for the proxy.

    Cached like `waveform` in shape (a `cache/` subdirectory, keyed by the
    resolved media's size and mtime, not a hash) but not in contract — see
    `THUMBS_DIR`'s comment for why this is lazy per bucket instead of
    computed whole on first touch.

    Refuses a clip with no video (a thumbnail is a picture) and a clip whose
    media is not actually reachable on disk, the same two guards `waveform`
    applies on the audio side.
    """
    if interval <= 0:
        raise ProjectError(f"interval must be positive, not {interval}")

    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    if not clip.get("has_video"):
        raise ProjectError(
            f"clip {clip_id!r} has no video track — a thumbnail is a picture, "
            "and this clip has none to draw one from"
        )
    source = media.media_path(project, clip)
    if not source.is_file():
        raise ProjectError(f"{clip_id}'s media is missing from disk: {source}")

    duration = clip.get("duration")
    at = max(0.0, float(at))
    bucket = round(at / interval) * interval
    if duration:
        # A full frame period short of the end, not half: `-ss` landing
        # between the last frame's own timestamp and the file's declared
        # duration decodes zero frames on real footage (measured — ffmpeg
        # 8.1.2 against a 24fps testsrc refuses everything from half a frame
        # past the last frame's pts up to EOF), where landing exactly on the
        # last frame's own pts always works.
        fps = clip.get("fps") or 30.0
        max_time = max(0.0, float(duration) - 1.0 / fps)
        bucket = min(bucket, max_time)

    stat = source.stat()
    key = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    key_path = _thumb_key_path(project, clip_id)
    frame_path = _thumb_dir(project, clip_id) / f"{round(bucket * 1000)}.jpg"

    fresh = False
    if key_path.is_file():
        try:
            fresh = json.loads(key_path.read_text(encoding="utf-8")) == key
        except (OSError, json.JSONDecodeError):
            fresh = False
    hit = fresh and frame_path.is_file()

    if not hit:
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        picture.extract_frame(source, bucket, frame_path)
        key_path.write_text(json.dumps(key), encoding="utf-8")

    return {
        "clip_id": clip_id,
        "src_time": bucket,
        "requested": at,
        "interval": interval,
        "path": str(frame_path),
        "cached": hit,
    }


#: How far into a clip's head the first-look sheet reaches, and the spacing
#: between its frames — goodsometimes' own spacing choice for exactly this
#: (`ideas/lambs-longlegs.md`, "v3"). 10s comfortably covers the incident
#: that motivates this: two shots used `sl-0428-elevator.mp4` from its own
#: head, which is 4.5s of "BASED ON THE NOVEL BY THOMAS HARRIS" over black,
#: because nobody had looked at the clip's own first seconds before cueing
#: it. Bounded regardless of clip length — unlike the whole-clip-filmstrip
#: cost `THUMBS_DIR`'s own docstring argues against, this is a handful of
#: frames, always, so the eager-decode concern that shapes `thumbnail()`'s
#: laziness does not apply here.
FIRST_LOOK_SECONDS = 10.0
FIRST_LOOK_INTERVAL = 1.5

#: Where the first look's labelled tiles and its montage land — beside every
#: other sheet under `cache/sheets/`, and deliberately **not** in
#: `cache/thumbs/`. The frames stay `thumbnail()`'s and none are drawn twice;
#: what lands here is a *tile*, which carries a label, and a montage of
#: several of them. Neither is a filmstrip frame, and `webui._send_thumb`
#: must never grow a way to serve one.
FIRST_LOOK_DIR = "cache/sheets/first"


def _first_look_montage(project: Project, clip_id: str, frames: list[dict[str, Any]]) -> Path:
    """Montage a first look's frames into one labelled sheet, and return it.

    Drawn from the thumbnails `contact_sheet` has already made rather than
    from a second extraction: the picture is the same either way, and the one
    thing the tile adds is a label saying which second it is. Labels are
    **source** seconds — a clip's head has no timeline to be at, and this
    sheet is looked at before anything is cued to the clip at all.

    Its own directory is wiped each time rather than accumulating, because a
    first look is regenerated with different `seconds`/`interval` and a stale
    tile from a wider run would montage into the middle of a narrower one.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", str(clip_id))
    dest = project.root / FIRST_LOOK_DIR / slug
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    tiles: list[Path] = []
    for index, frame in enumerate(frames):
        tile = dest / f"{index:03d}.png"
        _sheet_tile(
            Path(frame["path"]),
            f"{clip_id} src={float(frame['src_time']):.1f}s",
            tile,
            # Labelled at the width it will be montaged at, never at the
            # thumbnail's own: see `_sheet_tile`.
            width=SHEET_PAGE_WIDTH // SHOT_SHEET_COLUMNS,
        )
        tiles.append(tile)
    return graphics.montage(
        tiles,
        dest / "sheet.jpg",
        columns=SHOT_SHEET_COLUMNS,
        tile_width=SHEET_PAGE_WIDTH // SHOT_SHEET_COLUMNS,
        quality=SHOT_SHEET_QUALITY,
    )


def contact_sheet(
    path: Path | str,
    clip_id: str,
    *,
    seconds: float = FIRST_LOOK_SECONDS,
    interval: float = FIRST_LOOK_INTERVAL,
    montage: bool = True,
) -> dict[str, Any]:
    """A handful of cached frames from a clip's head — the first look.

    So "the first 4.5s are opening credits" is seen before a shot is cued to
    it, never discovered after (goodsometimes' own incident, `FIRST_LOOK_SECONDS`
    above). Built entirely on `thumbnail()`'s own cache and containment — no
    new cache directory, no new manifest key, no new web route:
    `interval` here is the same knob `GET /api/thumb/<clip_id>?at=&interval=`
    already exposes, and every frame lands in the existing
    `cache/thumbs/<clip_id>/` layout `thumbnail()` already writes and
    `webui._send_thumb` already serves.

    **The frames are the sheet; the montage is how a caller that cannot open
    a path gets to see them.** `frames` is unchanged and is still N ordinary
    thumbnails served through the route that already existed — a second
    image-*serving* path here would be the unjustified third caller CLAUDE.md
    warns `preview_path`'s containment against. What `montage=True` adds is
    one labelled JPEG under `cache/sheets/`, drawn from those same thumbnails
    and drawn nowhere near the manifest, because the MCP tool hands its
    *bytes* back and the agent panel (`--tools ToolSearch`) can open nothing else.

    **`import_media` asks for `montage=False`, and that is not a cost
    decision.** Its reply is a record, read by a person through the web pane
    that draws the thumbs; nothing in an import reply can carry an image, so
    a montage drawn there would be a picture nobody is in a position to see.
    The caller that can see one asks for it, and asking again is one call.

    An audio-only clip returns `frames: []` rather than raising — there is
    nothing to sheet, and that is not a failure, matching `check_frames`'s
    own "nothing to check" precedent for an audio-only project.
    """
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    if not clip.get("has_video"):
        return {
            "clip_id": clip_id,
            "frames": [],
            "sheet": None,
            "interval": interval,
            "reason": "no video track",
        }

    duration = float(clip.get("duration") or 0.0)
    span = min(seconds, duration)
    frames: list[dict[str, Any]] = []
    at = 0.0
    while at <= span + 1e-6:
        frames.append(thumbnail(path, clip_id, at, interval=interval))
        at += interval

    report: dict[str, Any] = {"clip_id": clip_id, "frames": frames, "interval": interval}
    if not montage or not frames:
        return report
    try:
        report["sheet"] = str(_first_look_montage(project, clip_id, frames))
    except (graphics.GraphicsError, OSError) as exc:
        # Best-effort and never raised: the frames *are* the first look, and a
        # box without `magick` should lose the picture rather than the sheet.
        report["sheet"] = None
        report["sheet_error"] = str(exc)
    return report


#: Where `shot_sheet` writes. Beside `reframe_sheet`'s own tiles under
#: `cache/sheets/`, and **not** under `cache/thumbs/`: a thumbnail is snapped
#: to a `THUMB_INTERVAL` bucket, and a shot's in-point is exact. A shot
#: shorter than one bucket would be drawn from a second inside a different
#: shot, which is the one thing this sheet must never do.
SHOT_SHEET_DIR = "cache/sheets/shots"
FOOTAGE_SHEET_DIR = "cache/sheets/footage"

#: Extracted source frames, shared by **every** sheet rather than sat under
#: one of them. A frame is addressed `(asset, source second)` and that address
#: knows nothing about which sheet asked for it — the shot sheet's in-point and
#: a footage sheet's interval mark land on the same second constantly, and
#: caching that frame twice would be paying twice to store the same picture
#: under two names. The tiles and the montaged pages stay per-sheet, because
#: those *do* differ: a tile carries its own sheet's label.
SHEET_FRAMES_DIR = "cache/sheets/frames"

#: Four across, and about two dozen a page. Both are the model's own image
#: handling rather than a file-size limit: vision downscales anything past
#: ~1568px on its long edge, and a downscaled sheet is a downscaled *label*.
#: Measured 2026-08-24 — 25 tiles at 384 wide is 1552x1313, read back with
#: every label of the bottom row verbatim; the same grid at 320 also reads,
#: so 384 is the measured ceiling rather than a guess at one. PLAN.md § The
#: agent contact sheet.
SHOT_SHEET_TILE = 384
SHOT_SHEET_COLUMNS = 4
SHOT_SHEET_PER_PAGE = 24

#: The same measurement read as a *page* rather than as a tile: four 384px
#: tiles across is 1552px, which is what reads back verbatim. A sheet whose
#: grid is not four wide divides this budget instead of keeping the tile and
#: letting the page grow past the ceiling — a wider page is not a bigger
#: picture, it is the same picture downscaled with its labels.
SHEET_PAGE_WIDTH = 1536

#: The label band under each tile, and the type in it. `-splice` puts this
#: *below* the frame rather than over it, so a label never covers picture —
#: the measured reason `magick montage` beat `ffmpeg xstack`, whose
#: `drawtext` burns over the image.
SHOT_SHEET_BAND = 34
SHOT_SHEET_POINTSIZE = 15

#: The sheet is JPEG, and that is a payload decision rather than a picture
#: one: it travels base64 inside every tool result, and the same 24-tile grid
#: is 1.32 MiB as PNG against 311 KiB at this quality — 4.3x, for labels and
#: faces that read identically (measured by reading one back, 2026-08-24).
#: The *tiles* stay PNG: they are montaged, and generational JPEG on text is
#: the one place the artefacts would compound.
SHOT_SHEET_QUALITY = 88

#: A tile is `blank` when its brightest pixel is under this fraction of full
#: scale — measured, and it is the *only* luma threshold here, because it is
#: the only one the data supports. Sampling 70 frames across the film, real
#: unedited gameplay/capture and ambient b-roll (2026-08-25) found **no gap at
#: all** between "dark" and "normal": normalised YAVG runs 0.104 → 0.48 with
#: nothing missing in the middle, so any "this tile is dark" line would be
#: picked rather than pinned, and at a plausible 0.18 it would mark a quarter
#: of every sheet. What the same sample *does* show is a clean 8x gap on YMAX
#: — a synthesised black frame reads 16 while the darkest real frame in the
#: corpus reads 127 — so "there is nothing in this tile" is answerable and
#: "this tile is dim" is not. 0.10 of scale sits 1.6x above the black control
#: and 5x below the darkest real frame, deliberately nearer the control: a
#: false `blank` tells an agent to disregard real footage, which is the more
#: expensive direction to be wrong in. PLAN.md § The footage sheet.
SHEET_BLANK_MAX = 0.10

#: What a blank tile says on its label. It is on the picture rather than only
#: in the reply because the reply's two halves are read by different means:
#: whatever is looking at the sheet sees a black square, and the sentence that
#: stops it inventing content for one has to be in the square.
_BLANK_MARK = "[blank]"


def _sheet_luma(stats: dict[str, float], scale: float) -> dict[str, Any]:
    """One tile's luma, normalised so two clips can be compared.

    **`signalstats` reports on the source's own scale**, so the raw numbers
    are not comparable between an 8-bit and a 10-bit clip: the film's
    `s4-overexposed` measures YAVG 429 against its neighbours' 26–132 and is
    not four times brighter, it is 10-bit (`media.MediaInfo.bit_depth`).
    `fraction` is the comparable figure; `avg`/`max` ride along raw because a
    number ships with whatever explains it, and because they are what a
    person re-measuring this with ffmpeg would see.
    """
    avg = stats.get("YAVG")
    top = stats.get("YMAX")
    return {
        "avg": None if avg is None else round(avg, 2),
        "max": None if top is None else round(top, 2),
        "scale": scale,
        "fraction": None if avg is None else round(avg / scale, 4),
        "blank": top is not None and top <= scale * SHEET_BLANK_MAX,
    }


def _sheet_frame(
    project: Project, *, asset: str, source: Path, at: float, still: bool = False
) -> tuple[Path, dict[str, Any]]:
    """Extract (or reuse) one tile's source frame, with its luma.

    **Keyed by the footage's own asset key, never an addressing clip_id.** A
    shot's addressing clip is the transcript the cue hangs on — `"vo"` on this
    repo's own film — and its *footage* is `asset`; reaching for `clip_id`
    here fetches the wrong file or none at all (CLAUDE.md, and the filmstrip
    draft that made exactly this mistake). Callers hand in an already-resolved
    `source`, so nothing here re-resolves media.

    Containment is `thumbnail()`'s, deliberately without being built on it:
    the frame is written under `cache/`, never enters the manifest, and is
    reached by `media.media_path` for nothing — `preview_path` is not called
    and must never be, because a proxy is downscaled and a sheet drawn off
    one would be showing the agent a preview encode and calling it the film.

    **What is cached is the frame, not the tile**, and the split is the point:
    a source frame is addressed `(asset, source second)` and no edit can
    invalidate one, while a tile carries `t=` — its *timeline* second — which
    every upstream cut moves. Caching the labelled tile would hand back a
    correct picture under a stale time.

    It is cached already downscaled, and the width is in the filename. Full
    frames are what the first build stored, and one page of this film cost 18
    MB of 1920x816 PNGs to make a 311 KiB sheet; keying on the width means
    changing `SHOT_SHEET_TILE` misses the cache rather than silently
    upscaling yesterday's smaller frames.

    Staleness is `thumbnail()`'s scheme rather than a second one: one
    `_source.json` per asset holding the resolved media's size and mtime, so
    replacing a clip's footage invalidates every one of its frames at once and
    each is re-extracted only when something next asks for it. Without it a
    re-import under the same `clip_id` would be drawn as the old footage
    indefinitely — a sheet is *evidence*, so serving a stale one is worse here
    than anywhere else this cache pattern is used.

    **The luma sidecar is part of the frame, not an extra**: `extract_frame`
    measures the frame it writes in the same ffmpeg call, and a cache hit
    would otherwise hand back a picture with its measurement thrown away. A
    frame whose sidecar is missing is re-extracted rather than reported
    without one — half the evidence silently is the failure this whole cache
    is careful about. The source's bit depth is probed once per asset and
    kept in `_source.json`, since it can only change when the media does.
    """
    # `card:` and `/` both appear in an asset key; neither may become a path
    # separator or a parent hop in the cache layout.
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", str(asset))
    dest = project.root / SHEET_FRAMES_DIR / slug
    stem = f"{round(at * 1000)}@{SHOT_SHEET_TILE}"
    frame = dest / f"{stem}.png"
    sidecar = dest / f"{stem}.json"

    stat = source.stat()
    key = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    key_path = dest / "_source.json"
    fresh = False
    scale: float | None = None
    if key_path.is_file():
        try:
            stored = json.loads(key_path.read_text(encoding="utf-8"))
            fresh = all(stored.get(name) == value for name, value in key.items())
            scale = float(stored["scale"]) if stored.get("scale") else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            fresh = False
    if fresh and scale and frame.is_file() and sidecar.is_file():
        try:
            return frame, _sheet_luma(json.loads(sidecar.read_text(encoding="utf-8")), scale)
        except (OSError, json.JSONDecodeError):
            pass

    if scale is None or not fresh:
        # A card is a still, and `probe` refuses a still (no duration) — so a
        # `card:` row read its bit depth off ffprobe's refusal and errored
        # the whole sheet, on the title card the agent had just made.
        depth = media.still_bit_depth(source) if still else media.probe(source).bit_depth
        scale = float(2**depth - 1)

    frame.parent.mkdir(parents=True, exist_ok=True)
    # The full-resolution frame is scratch, so it goes to a temporary
    # directory rather than into `cache/`. `/tmp` is safe here precisely
    # because melt is not involved — ffmpeg and magick are host binaries, and
    # it is melt's flatpak alone that cannot see it (CLAUDE.md).
    with tempfile.TemporaryDirectory(prefix="proofcut-sheet-") as tmp:
        full = Path(tmp) / "full.png"
        # The luma is measured on the frame ffmpeg is writing here, at the
        # source's own resolution and depth — not on the downscaled tile,
        # which magick has resampled.
        stats = picture.extract_frame(source, at, full)
        command = [
            *graphics.magick_command(), str(full),
            "-resize", f"{SHOT_SHEET_TILE}x",
            str(frame),
        ]  # fmt: skip
        done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        if done.returncode != 0 or not frame.is_file():
            raise graphics.GraphicsError(
                f"magick could not downscale the frame for {asset!r}: {done.stderr[-800:]}"
            )
    sidecar.write_text(json.dumps(stats), encoding="utf-8")
    # Written after the frame and its sidecar, never before: a key file ahead
    # of the things it vouches for would mark a failed extraction fresh.
    key_path.write_text(json.dumps({**key, "scale": scale}), encoding="utf-8")
    return frame, _sheet_luma(stats, scale)


def _sheet_tile(frame: Path, label: str, tile: Path, *, width: int | None = None) -> None:
    """Draw one labelled tile from an extracted frame.

    The label rides a spliced band **below** the picture, so it can never
    cover the thing it names. `%` is doubled because `-annotate` interprets
    percent escapes: an asset called `50%-crop` would otherwise be read as a
    format string, and magick's own answer to an unknown escape is to emit
    something rather than to fail.

    **`width` resizes the frame *before* the band is spliced, and a caller
    handing in a full-resolution frame needs it.** The type is drawn at
    `SHOT_SHEET_POINTSIZE` against whatever scale the picture is at, so
    labelling a 1920px frame and letting `montage` shrink it to 384 draws the
    label at a fifth of its intended size — measured on the first look, where
    every tile came back with an illegible smear under it and the picture
    itself was perfect. `_sheet_frame`'s callers hand in frames already cached
    at `SHOT_SHEET_TILE`, so they pass nothing here and their tiles do not
    move.
    """
    command = [
        *graphics.magick_command(), str(frame),
        *(("-resize", f"{width}x") if width else ()),
        "-background", "black", "-gravity", "south",
        "-splice", f"0x{SHOT_SHEET_BAND}",
        "-fill", "white", "-pointsize", str(SHOT_SHEET_POINTSIZE),
        "-annotate", "+0+8", label.replace("%", "%%"),
        str(tile),
    ]  # fmt: skip
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if done.returncode != 0 or not tile.is_file():
        raise graphics.GraphicsError(f"magick could not draw the tile for {label!r}: {done.stderr[-800:]}")


def shot_sheet(
    path: Path | str,
    *,
    page: int = 0,
    per_page: int = SHOT_SHEET_PER_PAGE,
    out: str | None = None,
) -> dict[str, Any]:
    """One labelled tile per shot of the picture track — what an agent looks at.

    PLAN.md § The agent contact sheet. The question it answers is the oldest
    one open in this repo: an agent can already *listen* to what it made
    (`verify` reads a render back through whisper and diffs it against the
    timeline) and it could not *look* at it. A person opens the window and
    watches; an agent has no window and cannot watch an MP4.

    **Every other sheet here returns paths, and a path is not an image.** The
    agent panel runs `claude` with `--tools ToolSearch`, so proofcut's MCP tools are the
    entire surface it has and it cannot Read a file — which means
    `contact_sheet`'s frame list and `reframe_sheet`'s montage are both
    invisible to the one caller that most needs them. What makes this one
    different is not the drawing, it is that the MCP tool hands the *bytes*
    back as `ImageContent` (`server.shot_sheet`); this function's own return
    is the labelled index of what is in the picture, and the picture is the
    other half of the reply.

    **Tiles are per-shot in-points, not frames around each cut.** The
    recorded lean was ±0.5s around every cut boundary and measurement argues
    against it: the boundaries an agent can enumerate are the *VO's* — 62 of
    them on the film — and the picture does not change at a VO cut unless a
    cue lands there, so that sheet is 124 near-duplicate tiles of mostly the
    same frame. `shots` is already the projection through `mlt.plan_picture`
    and carries exactly what a tile needs.

    Drawn from `_picture_plan`, never `build_shots` — the two disagree, and
    the raw projection would draw a shot `export` refuses (CLAUDE.md). A plan
    that refuses comes back as `shots_error` with no sheet, `timeline_view`'s
    policy: the caller is told why rather than handed a picture of a film
    that will not render.

    **A reading is an opinion, not a check.** `reframe_sheet`'s precedent
    holds here exactly: this draws evidence and decides nothing, nothing
    downstream gates on what a model said it saw, and a hypothesis formed off
    a tile is confirmed with an op that measures.
    """
    if per_page < 1:
        raise ProjectError(f"a page holds at least one tile, not {per_page}")
    if page < 0:
        raise ProjectError(f"page is counted from 0, not {page}")

    project = Project.open(path)
    rate = _export_fps(_clips_by_id(project))
    try:
        shots, _ = _picture_plan(project, rate)
    except _PICTURE_REFUSALS as exc:
        # `timeline_view`'s policy, and the reason it is right here too: a
        # refusing plan is a real editorial state (someone cut the line a
        # picture hung on), and raising would make the agent's own look at
        # the film indistinguishable from proofcut being broken.
        return {
            "project": str(project.root),
            "sheet": None,
            "shots_error": str(exc),
            "tiles": [],
            "count": 0,
            "drawn": 0,
            "blank": 0,
            "shots": 0,
            "page": page,
            "pages": 0,
            "per_page": per_page,
        }

    pages = max(1, ceil(len(shots) / per_page)) if shots else 0
    window = shots[page * per_page : (page + 1) * per_page]

    tiles: list[dict[str, Any]] = []
    drawn: list[Path] = []
    dest_dir = project.root / SHOT_SHEET_DIR
    for offset, row in enumerate(window):
        index = page * per_page + offset
        # A still is held, not played: it has no playhead, so there is one
        # frame to show and `src_start` on it means nothing.
        at = 0.0 if row.get("is_image") else float(row.get("src_start") or 0.0)
        label = f"{row['asset']} t={row['start']:.1f}s src={at:.1f}s"
        try:
            frame, luma = _sheet_frame(
                project,
                asset=row["asset"],
                source=Path(row["asset_path"]),
                at=at,
                still=bool(row.get("is_image")),
            )
            if luma["blank"]:
                label = f"{label} {_BLANK_MARK}"
            tile = dest_dir / "tiles" / f"{index:04d}.png"
            tile.parent.mkdir(parents=True, exist_ok=True)
            _sheet_tile(frame, label, tile)
        except (picture.PictureError, graphics.GraphicsError, media.MediaError, OSError) as exc:
            # `MediaError` is in the tuple because one asset ffprobe refuses
            # is one errored tile, not a sheet that never comes back.
            # One unreadable moment is not a reason to throw the other
            # twenty-three away — `describe_windows`' rule, for the same
            # reason: the sheet is evidence, and partial evidence beats none.
            tiles.append({"index": index, "label": label, "asset": row["asset"], "error": str(exc)})
            continue
        drawn.append(tile)
        tiles.append(
            {
                "index": index,
                "label": label,
                # `asset` is the footage; `clip_id` is the cue's addressing
                # transcript. Both ride the row because reading the wrong one
                # is this projection's standing trap, and a caller that has
                # only one of them cannot tell it made the mistake.
                "asset": row["asset"],
                "clip_id": row["clip_id"],
                "start": round(float(row["start"]), 3),
                "src_start": round(at, 3),
                "duration": round(float(row["duration"]), 3),
                "is_image": bool(row.get("is_image")),
                "frame": str(frame),
                "luma": luma,
            }
        )

    sheet: Path | None = None
    if drawn:
        sheet = graphics.montage(
            drawn,
            Path(out).expanduser() if out else dest_dir / f"page{page}.jpg",
            columns=SHOT_SHEET_COLUMNS,
            tile_width=SHOT_SHEET_TILE,
            quality=SHOT_SHEET_QUALITY,
        )

    return {
        "project": str(project.root),
        "sheet": None if sheet is None else str(sheet),
        "shots_error": None,
        "tiles": tiles,
        "count": len(tiles),
        # Tiles on this page that actually carry a picture. It is `count`
        # minus the ones that failed, and it is reported rather than left to
        # be counted off `tiles` because "24 tiles" silently implying 24
        # pictures is exactly the shape a partial sheet must not have.
        "drawn": len(drawn),
        # Tiles with nothing in them. A finding about the *film* rather than
        # about the sheet — `check_black`'s subject — and reported here only
        # because these frames were decoded anyway.
        "blank": sum(1 for t in tiles if t.get("luma", {}).get("blank")),
        # Every shot in the picture track, so a page of 24 out of 38 reads as
        # "there is more" rather than as the whole film.
        "shots": len(shots),
        "page": page,
        "pages": pages,
        "per_page": per_page,
    }


#: How a footage sheet decides which instants to draw, and why the default is
#: not the interesting-sounding one. Measured 2026-08-25 across the film's own
#: cut footage and, for the first time in this repo, real *unedited* material
#: — gameplay DVR, a 1070s screen capture, two ambient b-roll loops:
#:
#:     clip              duration   cuts>=0.15   one cut every
#:     waves loop            18.8s        0            never
#:     car loop              29.3s        0            never
#:     cod dvr               60.1s       17             3.5s
#:     capture             1070.0s       38            28.2s
#:     s2022-reveal (film)  160.1s       62             2.6s
#:
#: **A scene scan's yield is uncorrelated with anything a caller knows in
#: advance.** Cut density spans 0 to 23 a minute — not the 3.3x spread the
#: film alone showed but an unbounded one, because it measures how *edited*
#: the material is. On the continuous takes this sheet exists for it returns
#: nothing at all, which `media.scene_cuts`' own contract says is a correct
#: answer and which makes a sheet of zero tiles; on 60s of gameplay it fires
#: 17 times at deaths and respawns, which are not shots. So `scenes` stays
#: opt-in, and the default is the address that yields the same tiles-per-
#: minute on every clip alive. PLAN.md § The footage sheet.
FOOTAGE_SHEET_MODES = ("auto", "interval", "describe", "scenes")

#: The default interval **is** `describe`'s window, and deliberately the same
#: number rather than a second one that happens to be near it. It makes the
#: two addresses commensurable: `interval` is what `describe` would have
#: indexed had anyone run it, so a clip sheets to the same tiles before and
#: after being described, and a page means the same span of footage either
#: way. A separate constant here would drift from `describe.WINDOW` the first
#: time one of them was tuned.
FOOTAGE_SHEET_INTERVAL = dsc.WINDOW


def _footage_marks(
    project: Project,
    clip: dict[str, Any],
    source: Path,
    *,
    mode: str,
    interval: float,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Which source seconds this clip gets a tile at, and how they were chosen.

    Returns the mode actually used, the marks, and whatever the choosing cost
    or noticed. `auto` prefers describe windows and falls back to the
    interval; it never reaches for `scenes`, because a scan is the one address
    here that decodes the whole clip and a default must not do that
    (`reframe_coverage`'s rule, and `frame.js`'s breach of it).
    """
    duration = float(clip["duration"])
    described = [d for d in _descriptions(project) if d["clip_id"] == clip["clip_id"]]
    notes: dict[str, Any] = {"described_windows": len(described)}

    if mode == "auto":
        mode = "describe" if described else "interval"

    if mode == "describe":
        if not described:
            raise ProjectError(
                f"clip {clip['clip_id']!r} has no descriptions to sheet — run "
                f"`proofcut describe {clip['clip_id']}` first, or ask for "
                "--mode interval, which needs nothing"
            )
        described.sort(key=lambda d: d["src_start"])
        # The middle of the window, not its start: a description is about the
        # whole 10s and its first frame is the one most likely to still be the
        # previous window's subject.
        marks = [
            {
                "at": min(duration, (float(d["src_start"]) + float(d["src_end"])) / 2),
                "src_start": round(float(d["src_start"]), 3),
                "src_end": round(float(d["src_end"]), 3),
                "text": d["text"],
            }
            for d in described
        ]
        return mode, marks, notes

    if mode == "scenes":
        started = time.monotonic()
        cuts = media.scene_cuts(source)
        notes["scan_seconds"] = round(time.monotonic() - started, 2)
        notes["candidates"] = len(cuts)
        at = [c["src_time"] for c in cuts if c["score"] >= SCENE_THRESHOLD]
        # The head is not a cut and is always the start of a shot, so without
        # it a clip's opening is the one stretch a cut-addressed sheet never
        # draws — and on a continuous take it would be the only tile there is.
        marks = [{"at": 0.0}] + [{"at": t} for t in at if t > 0.0]
        return mode, marks, notes

    # `describe`'s own planner, called rather than re-derived: equal windows
    # with the count rounded up, so there is never a remainder. That is what
    # makes the two addresses commensurable — a clip sheets to the same
    # stretches before and after anyone describes it — and it is also what
    # keeps the last mark off the end of the file. Deriving the same split by
    # hand put a 7th mark on a 60.1s clip at 60.1s exactly, which is past the
    # last frame, and ffmpeg refused it: 7 marks, 6 tiles, at exit 0.
    marks = [
        {
            "at": (start + end) / 2,
            "src_start": round(start, 3),
            "src_end": round(end, 3),
        }
        for start, end in dsc.plan_windows(duration, window=interval)
    ]
    return mode, marks, notes


def footage_sheet(
    path: Path | str,
    clip_id: str,
    *,
    mode: str = "auto",
    interval: float = FOOTAGE_SHEET_INTERVAL,
    page: int = 0,
    per_page: int = SHOT_SHEET_PER_PAGE,
    out: str | None = None,
) -> dict[str, Any]:
    """One labelled tile per moment of a clip's own footage — a browse, not a cut.

    PLAN.md § The footage sheet. `shot_sheet` sheets the **timeline**, which
    is addressed through the cue table and so exists only once there is an
    edit. This sheets a **source clip**, and the question it answers belongs
    to the people proofcut's transcript machinery cannot help at all: a GoPro
    dump, event coverage, gameplay — no dialogue, nothing for a word index to
    address. `describe` already indexes what is visible and `describe-ls`
    searches that text, so they can *find* a moment; what neither can do is
    let the thing choosing **look** at it. The find is a text match and the
    confirm was a path, which under the agent panel's `--tools ToolSearch` is no
    confirm at all.

    That gap is measured rather than supposed: against 25 human picks the
    description index agreed 2 times and the clips' own filenames 3
    (HISTORY.md § Choosing the b-roll). The conclusion drawn then — that a
    better `describe` prompt is the wrong fix and a reader should choose —
    points here once a reader can see.

    **`interval` is the default address and `scenes` is opt-in**, which is the
    opposite of the obvious build; `FOOTAGE_SHEET_MODES` carries the
    measurement. `auto` upgrades to `describe` where a clip has descriptions,
    because then a tile and a description share one address and the sheet's
    two halves are about the same 10 seconds — the pairing this whole op is
    for. It never picks `scenes`: that address decodes the entire clip.

    **A reading is an opinion, not a check** — `reframe_sheet`'s standing rule,
    and it binds hardest here, because this sheet's whole purpose is to inform
    a *choice* of footage. Nothing gates on a tile. `synopsis` remains where a
    person says what a clip **is**, which is a different fact from what a
    camera saw and is not replaced by one.
    """
    if per_page < 1:
        raise ProjectError(f"a page holds at least one tile, not {per_page}")
    if page < 0:
        raise ProjectError(f"page is counted from 0, not {page}")
    if mode not in FOOTAGE_SHEET_MODES:
        raise ProjectError(f"mode is one of {', '.join(FOOTAGE_SHEET_MODES)}, not {mode!r}")
    if interval <= 0:
        raise ProjectError(f"an interval is a positive number of seconds, not {interval}")

    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    if not clip.get("has_video"):
        raise ProjectError(
            f"clip {clip_id!r} has no video track, so there is nothing to look "
            "at — a sheet draws pictures, not dialogue. Its words are what "
            "`transcribe` indexes."
        )
    # `media_path`, never `preview_path`: a proxy is downscaled and a sheet
    # drawn off one shows a preview encode and calls it the footage. That
    # split is the containment and this must not become its third caller
    # (CLAUDE.md).
    source = media.media_path(project, clip)

    progress.report(0, None, f"choosing moments in {clip_id}")
    used, marks, notes = _footage_marks(
        project, clip, source, mode=mode, interval=float(interval)
    )

    pages = max(1, ceil(len(marks) / per_page)) if marks else 0
    window = marks[page * per_page : (page + 1) * per_page]

    tiles: list[dict[str, Any]] = []
    drawn: list[Path] = []
    dest_dir = project.root / FOOTAGE_SHEET_DIR / re.sub(r"[^A-Za-z0-9._-]", "_", clip_id)
    for offset, mark in enumerate(window):
        progress.report(offset, len(window), f"drawing {clip_id}")
        index = page * per_page + offset
        at = float(mark["at"])
        label = f"{clip_id} src={at:.1f}s"
        try:
            frame, luma = _sheet_frame(project, asset=clip_id, source=source, at=at)
            if luma["blank"]:
                label = f"{label} {_BLANK_MARK}"
            tile = dest_dir / "tiles" / f"{index:04d}.png"
            tile.parent.mkdir(parents=True, exist_ok=True)
            _sheet_tile(frame, label, tile)
        except (picture.PictureError, graphics.GraphicsError, OSError) as exc:
            # `describe_windows`' rule: one unreadable moment is not a reason
            # to throw the other twenty-three away. A sheet is evidence, and
            # partial evidence beats none.
            tiles.append({"index": index, "label": label, "src": round(at, 3), "error": str(exc)})
            continue
        drawn.append(tile)
        entry = {
            "index": index,
            "label": label,
            "clip_id": clip_id,
            "src": round(at, 3),
            "frame": str(frame),
            "luma": luma,
        }
        # A describe-addressed tile carries the window it was cut from and the
        # sentence about it, which is the address the two halves share. The
        # text is *not* drawn on the tile: it is a sentence, the band holds a
        # line, and a truncated description is worse than none.
        for key in ("src_start", "src_end", "text"):
            if key in mark:
                entry[key] = mark[key]
        tiles.append(entry)

    sheet: Path | None = None
    if drawn:
        sheet = graphics.montage(
            drawn,
            Path(out).expanduser() if out else dest_dir / f"page{page}.jpg",
            columns=SHOT_SHEET_COLUMNS,
            tile_width=SHOT_SHEET_TILE,
            quality=SHOT_SHEET_QUALITY,
        )

    return {
        "project": str(project.root),
        "clip_id": clip_id,
        "sheet": None if sheet is None else str(sheet),
        # What was asked for and what was done are different fields, because
        # `auto` resolves to one of the others and a caller reading back only
        # its own argument cannot tell which sheet it got.
        "mode": used,
        "asked": mode,
        # The spacing actually drawn, beside the one asked for — the same
        # mode/asked shape, and for the same reason. `plan_windows` divides
        # the clip into equal stretches rather than leaving a remainder, so a
        # 60.1s clip asked for 10s tiles gets seven of 8.586s. Reporting only
        # the request would describe a sheet nobody drew.
        "interval": (
            round(marks[1]["src_start"] - marks[0]["src_start"], 3)
            if used == "interval" and len(marks) > 1
            else round(float(clip["duration"]), 3)
            if used == "interval"
            else None
        ),
        "interval_asked": round(float(interval), 3) if used == "interval" else None,
        "duration": round(float(clip["duration"]), 3),
        "tiles": tiles,
        "count": len(tiles),
        "drawn": len(drawn),
        "blank": sum(1 for t in tiles if t.get("luma", {}).get("blank")),
        # Every mark in the clip, so a page of 24 out of 107 reads as "there
        # is more" rather than as the whole recording.
        "marks": len(marks),
        "page": page,
        "pages": pages,
        "per_page": per_page,
        **notes,
    }


#: Cards are written as PNG by every path that makes one, but a person can
#: drop any still into `assets/cards/`, and the preview shows it as an <img>.
_PREVIEW_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"})


def preview_source(path: Path | str, asset: str) -> dict[str, Any]:
    """Resolve one preview asset to a file, and say whether it will play.

    The picture lane draws shots; this is what lets the *viewer* show the shot
    under the playhead, which is what makes V2 a picture rather than a plan of
    one (PLAN.md § Next). One asset key in — a `card:name` or a clip_id, the
    same opaque string a cue carries — a resolved path and a verdict out.

    **Deliberately wider than `_resolve_asset`**, which refuses a clip with no
    video because a picture *cue* pointing at a VO is a mistake. The viewer has
    a second caller with the opposite need: the transport plays the timeline's
    own clip, and on a VO project that clip is exactly the audio-only one. The
    two resolvers agree on where a card lives and on what `media_path` means;
    they disagree only about what a valid answer is, and each is right for its
    own question.

    `kind` is what the front end draws with — an `<img>` holds a still, a
    `<video>` seeks — and it comes from the resolved file rather than from the
    cue, because `is_image` in a shot is a statement about the *cue key* and
    this is a statement about the bytes.

    `playable` is `media.playability`'s verdict, and it is reported rather than
    enforced: an unplayable file is still streamed if asked for, because the
    browser is the only real authority and this list is a prediction. What the
    verdict buys is the *reason*, which a `<video>`'s error event does not carry
    — without it, a codec refusal and a black frame in the edit look identical.
    """
    project = Project.open(path)
    if asset.startswith("card:"):
        name = asset.removeprefix("card:")
        # The one place an asset key arrives from outside the project (the web
        # route), so the traversal check is here rather than in the resolver
        # shared with the cue table.
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ProjectError(f"asset {asset!r} does not name a card")
        source = project.cards_dir / f"{name}.png"
    else:
        # `preview_path`, not `media_path`: the proxy when a current one
        # exists (PLAN.md § The preview proxy transcode). This is one of the
        # two callers that resolve that way, and `export` is deliberately not
        # among them — see media.py § the preview proxy.
        source = media.preview_path(project, media.get_clip(project, asset))
    if not source.is_file():
        raise ProjectError(f"asset {asset!r} resolves to {source}, which does not exist")

    result: dict[str, Any] = {"asset": asset, "path": str(source)}
    if source.suffix.lower() in _PREVIEW_IMAGE_SUFFIXES:
        return {**result, "kind": "image", "playable": True, "reason": None}

    verdict = media.playability(source)
    kind = "video" if verdict.get("video_codec") else "audio"
    return {**result, "kind": kind, **verdict}


def proxy_transcode(
    path: Path | str, clip_id: str, *, force: bool = False
) -> dict[str, Any]:
    """Build a browser-playable stand-in for footage the preview cannot decode.

    The other half of what `media.playability()` already reports: the viewer
    names the reason a clip shows black (`hev1`, 10-bit, an unopenable
    container, an undecodable audio track), and this is what makes it play.
    One ffmpeg pass, downscaled — a proxy is for a `<video>` in a window, not
    for delivery, and `media.PROXY_HEIGHT` carries the measurement behind that.

    **The result never enters the manifest**, which is the point rather than an
    omission: no key here means `media_path()` cannot reach it, so no render,
    `verify` or `check_frames` can be silently taken at preview quality. Only
    `media.preview_path` resolves it, and only the preview side calls that.

    Skips the work when a current proxy already exists — keyed by the resolved
    source's size and mtime — so this is safe to call on every unplayable
    asset in a project without re-encoding the ones already done. `force`
    rebuilds anyway, which is for a changed `PROXY_HEIGHT`/`PROXY_CRF` rather
    than for a changed source, since a changed source invalidates the key on
    its own.

    Refuses a clip that is already playable rather than transcoding it: a
    proxy of a file the browser opens directly is pure cost and a second,
    lower-quality copy of footage nothing needed a copy of. `force` does not
    override that — it overrides the *cache*, not the judgement.
    """
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    source = media.media_path(project, clip)
    if not source.is_file():
        raise ProjectError(f"{clip_id}'s media is missing from disk: {source}")

    verdict = media.playability(source)
    if verdict.get("playable"):
        raise ProjectError(
            f"{clip_id} already plays in a browser ({source.name}) — a proxy would be a "
            "second, lower-quality copy of footage nothing needs one of"
        )
    # The one refusal a transcode cannot close: not a codec problem.
    if not verdict.get("video_codec") and not verdict.get("audio_codec"):
        raise ProjectError(
            f"{clip_id} has no decodable streams ({verdict.get('reason')}) — that is a "
            "broken file, not a codec a transcode can change"
        )

    proxy = project.proxy_path(clip_id)
    if not force and media.proxy_is_current(project, clip):
        return {
            "clip_id": clip_id,
            "proxy": str(proxy),
            "built": False,
            "reason": verdict.get("reason"),
            "bytes": proxy.stat().st_size,
        }

    stat = source.stat()
    media.make_proxy(source, proxy)
    # Written *after* the transcode returns, never before: a key that exists
    # beside a half-written or absent mp4 is a stale hit that reads as current.
    project.proxy_key_path(clip_id).write_text(
        json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}),
        encoding="utf-8",
    )
    return {
        "clip_id": clip_id,
        "proxy": str(proxy),
        "built": True,
        "reason": verdict.get("reason"),
        "bytes": proxy.stat().st_size,
        "source_bytes": stat.st_size,
        # What the preview will now play, read back off the file rather than
        # assumed from the flags handed to ffmpeg — the same discipline every
        # render check here follows.
        "playable": media.playability(proxy),
    }


def _resolve(parsed: tx.Transcript, ranges: Iterable[Sequence[int]]) -> list[tuple[float, float]]:
    resolved = []
    for item in ranges:
        if len(item) != 2:
            raise tx.TranscriptError(f"word range {item!r} must be [first, last]")
        resolved.append(parsed.span(int(item[0]), int(item[1])))
    return resolved


#: Words shown either side of a resolved range. The defect this echo exists to
#: catch is an index one word past the intended phrase (HISTORY.md § 3), and
#: resolved text alone cannot show that — "the words I meant, plus one" reads
#: perfectly well on its own. It only looks wrong next to where the phrase
#: should have ended, so the neighbours travel with every echo.
CONTEXT_WORDS = 3


def _context(parsed: tx.Transcript, first: int, last: int) -> dict[str, Any]:
    """The words just outside a range, kept separate from the ones inside it."""
    before = parsed.window(max(0, first - CONTEXT_WORDS), max(0, first - 1))
    after = parsed.window(min(len(parsed) - 1, last + 1), last + CONTEXT_WORDS)
    return {
        "context_before": [{"index": w.index, "text": w.text} for w in before if w.index < first],
        "context_after": [{"index": w.index, "text": w.text} for w in after if w.index > last],
    }


def _pad_reach(
    parsed: tx.Transcript, first: int, last: int, lo: float, hi: float
) -> list[dict[str, Any]]:
    """Words outside `first`..`last` that the padded span nonetheless touches.

    `pad` widens a cut in *seconds*, so the echoed text — which is the words
    themselves — understates what the cut removes whenever the padding reaches
    into a neighbour. That disagreement between the number and the words is
    the same class of error the echo exists to prevent, so name the words the
    padding actually eats.

    Overlap, never containment (CLAUDE.md): a neighbour half-swallowed by the
    padding is exactly the case worth reporting, and containment would miss it.
    """
    reached = []
    for word in parsed.words:
        if first <= word.index <= last:
            continue
        if word.start < hi and word.end > lo:
            reached.append(
                {
                    "index": word.index,
                    "text": word.text,
                    "side": "before" if word.index < first else "after",
                }
            )
    return reached


def _echo(
    parsed: tx.Transcript, first: int, last: int, lo: float, hi: float
) -> dict[str, Any]:
    """What a word range resolved to, in words rather than indices."""
    start, end = parsed.span(first, last)
    echo: dict[str, Any] = {
        "first_word": first,
        "last_word": last,
        "text": " ".join(w.text for w in parsed.window(first, last)),
        # The range's own edges, before padding — so the pair below can be
        # compared against `source_start`/`source_end` to see what pad did.
        "word_start": start,
        "word_end": end,
        **_context(parsed, first, last),
    }
    reach = _pad_reach(parsed, first, last, lo, hi)
    if reach:
        echo["pad_reach"] = reach
    return echo


def cut_by_transcript(
    path: Path | str,
    clip_id: str,
    *,
    cut: Sequence[Sequence[int]] | None = None,
    keep: Sequence[Sequence[int]] | None = None,
    pad: float = 0.0,
    confirm_suspect: bool = False,
    through_pause: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Cut or keep word ranges — the operation proofcut exists for.

    Ranges are inclusive word indices into the clip's transcript, resolved to
    source time and applied to the accumulated timeline. `pad` widens each cut
    on both sides, which is how you reach the silence *between* words instead
    of clipping the consonant at the edge.

    Exactly one of `cut` or `keep` is accepted: a call that meant "keep" but
    was read as "cut" would produce the precise inverse of the intended edit,
    so there is no default.

    `through_pause=True` extends each cut range's trailing edge through the
    pause after its last word, when that gap clears `PAUSE_MARKER_MIN` — the
    same predicate `_word_placements` uses to decide whether the transcript
    pane draws a `[N.Ns]` marker there at all, so a range can only ever be
    extended onto a gap the pane actually showed; a stale flag sent for a gap
    that no longer qualifies is a safe no-op. `Transcript.span` stops at the
    last word's own `end`, so without this the trailing pause survives as
    audible dead air even after the words either side of it are cut — this is
    what makes "cutting a phrase cuts its trailing pause" (docs/plans/DAYDREAM.md §
    Transcript document) true rather than merely cosmetic. Only the `cut`
    branch reads it: `keep` already discards everything outside its ranges,
    including any trailing pause, so there is nothing separate to swallow.
    Applies uniformly to every range in one call, matching `pad`'s existing
    per-call (not per-range) precedent.

    A range whose first or last word claims a suspect duration
    (HISTORY.md § Suspect word durations) is refused unless `confirm_suspect=True`: that word's `start`/`end`
    is what the cut boundary resolves to, and a boundary that long is usually
    hiding a retake rather than ending where it claims.

    `plan=True` resolves everything and returns the same payload without
    writing: no snapshot, no timeline mutation. It runs the identical code path
    — the edit is mutated in memory and simply never saved — so the numbers it
    reports are the real ones, not a second implementation's guess at them.
    Six cues in the Scream shot plan pointed one word past the intended phrase
    and were caught exactly this way (HISTORY.md § 3). Planning also *reports*
    suspect boundaries rather than refusing them: looking is the thing you do
    before deciding, so refusing to look would be backwards.
    """
    if bool(cut) == bool(keep):
        raise tx.TranscriptError("pass exactly one of cut= or keep=")

    project = Project.open(path)
    media.get_clip(project, clip_id)
    parsed = _transcript(project, clip_id)
    edit = _load_edit(project)
    before = edit.duration

    suspect = {item["index"]: item for item in _suspect_durations(parsed)}
    flagged: list[dict[str, Any]] = []
    for first, last in cut or keep or []:
        hit = suspect.get(int(first)) or suspect.get(int(last))
        if not hit:
            continue
        flagged.append({**hit, "range": [int(first), int(last)]})
        if plan or confirm_suspect:
            continue
        raise tx.TranscriptError(
            f"word {hit['index']} ({hit['text']!r}) claims {hit['duration']}s, "
            f"more than {hit['limit']}s (the transcript's median x "
            f"energy.CAP) — it likely hides a retake rather than ending "
            "where it claims, so it is refused as a cut boundary "
            "(HISTORY.md § Suspect word durations). Check it, then retry "
            "with confirm_suspect=True (CLI: --confirm-suspect) if the "
            "boundary is actually fine, or pick a different word."
        )

    applied: list[dict[str, Any]] = []
    if cut:
        for (first, last), (start, end) in zip(cut, _resolve(parsed, cut)):
            if through_pause:
                gap = _gap_after(parsed.words, int(last))
                if gap is not None and gap >= PAUSE_MARKER_MIN:
                    end = parsed.words[int(last) + 1].start
            lo, hi = max(0.0, start - pad), end + pad
            present = edit.covers(clip_id, lo, hi)
            touched = edit.remove(clip_id, lo, hi)
            applied.append(
                {
                    **_echo(parsed, int(first), int(last), lo, hi),
                    "source_start": lo,
                    "source_end": hi,
                    "segments_touched": touched,
                    "already_cut": present <= 0.0,
                }
            )
    else:
        intervals = [
            (max(0.0, s - pad), e + pad) for s, e in _resolve(parsed, keep or [])
        ]
        edit.keep_only(clip_id, intervals)
        for (first, last), (start, end) in zip(keep or [], intervals):
            applied.append(
                {
                    **_echo(parsed, int(first), int(last), start, end),
                    "source_start": start,
                    "source_end": end,
                }
            )

    if not plan:
        _save_edit(project, edit)
    result = {
        "clip_id": clip_id,
        "mode": "cut" if cut else "keep",
        "applied": applied,
        "duration_before": before,
        "duration_after": edit.duration,
        "removed": before - edit.duration,
        "segments": len(edit.segments),
    }
    if plan:
        result["plan"] = True
        result["suspect_boundaries"] = flagged
    return result


def _reject_overlapping_spans(requests: Sequence[tuple[float, float]]) -> None:
    """Refuse a same-call span overlap rather than resolve and apply it twice.

    Every span resolves against the pre-cut timeline before any is applied
    (so a list of notes from one watch stays valid together), which is
    exactly what makes an overlap between two spans in one call unsafe to
    just apply in order: it is almost certainly one flub logged twice, not a
    thing to merge (`vo_trim.py`'s own choice).
    """
    ordered = sorted(requests)
    for (a_start, a_end), (b_start, b_end) in pairwise(ordered):
        if b_start < a_end:
            raise tl.TimelineError(
                f"spans {a_start:.3f}-{a_end:.3f} and {b_start:.3f}-{b_end:.3f} "
                "overlap in this call — resolve the overlap before cutting, "
                "since applying one would shift the timeline the other addresses"
            )


def _overlap_words(parsed: tx.Transcript, lo: float, hi: float) -> list[dict[str, Any]]:
    """Words a `[lo, hi)` render-time-derived span overlaps.

    Unlike `_pad_reach`, there is no known first/last word to stay relative
    to here, so this walks the whole transcript. Overlap, never containment
    (CLAUDE.md): a word half inside the span still counts.
    """
    return [
        {"index": w.index, "text": w.text, "start": w.start, "end": w.end}
        for w in parsed.words
        if w.start < hi and w.end > lo
    ]


def _nearest_context(parsed: tx.Transcript, lo: float, hi: float) -> dict[str, Any]:
    """Flanking words for a span that landed entirely in silence.

    Treats the silence gap as though it were the (empty) resolved range
    between the nearest word ending at/before `lo` and the nearest one
    starting at/after `hi`, so `_context` can be reused unmodified rather
    than reporting no context at all for a legitimate "cut some dead air" span.
    """
    before_idx = -1
    for word in parsed.words:
        if word.end <= lo:
            before_idx = word.index
        else:
            break
    after_idx = len(parsed)
    for word in parsed.words:
        if word.start >= hi:
            after_idx = word.index
            break
    return _context(parsed, before_idx + 1, after_idx - 1)


def cut_by_time(
    path: Path | str,
    *,
    spans: Sequence[Sequence[float]],
    pad: float = 0.0,
    confirm_suspect: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Cut spans of render/timeline time — what a human reports watching an export.

    Each span is `[start, end)` in the seconds the current export plays at,
    not source time and not word indices. Every span is converted to the
    source interval(s) it plays — `Edit.source_spans`, the aggregate inverse
    of the mapping captions and playback use — and cut through the exact same
    `Edit.remove` path `cut_by_transcript` uses. The render timestamp itself
    is never stored: the conversion happens once, here, at call time, so the
    roadmap's core property (every persisted coordinate is source time) holds.

    All spans resolve against the timeline as it stood before any of them was
    applied, so a list of notes taken against one watch stays valid together
    even though a real cut would shift every later timestamp. Overlapping
    spans in one call are refused rather than silently double-applied.

    Every piece a span produced (more than one when it crossed an earlier cut
    or a clip boundary) echoes the words it overlaps there — an overlap test,
    never containment — plus the words either side, reusing `cut_by_transcript`'s
    own echo convention. A span landing entirely in silence still gets its
    nearest flanking words, so there is always something to check the
    timestamp against. A clip with no transcript attached still gets cut;
    `words_overlapped` is null and `transcript_missing` is set instead of
    refusing a valid render-time cut just because a picture-only clip was
    never transcribed.

    `pad` widens only the two true OUTER edges of each requested span, never
    an inner seam the span happened to cross — padding an inner seam would
    reach toward whatever now sits on the far side of a prior cut, which is
    material the caller never named.

    Refused the same way `cut_by_transcript` is if a span overlaps a word with
    a suspect duration (HISTORY.md § Suspect word durations), unless
    `confirm_suspect=True` or `plan=True` — under `plan` they are reported as
    `suspect_boundaries` instead.

    `plan=True` runs the identical code path and simply skips the write, same
    as `cut_by_transcript` (HISTORY.md § `cut --plan`).
    """
    if not spans:
        raise tl.TimelineError("cut_by_time needs at least one span")
    requests: list[tuple[float, float]] = []
    for item in spans:
        if len(item) != 2:
            raise tl.TimelineError(f"span {item!r} must be [start, end]")
        requests.append((float(item[0]), float(item[1])))
    _reject_overlapping_spans(requests)

    project = Project.open(path)
    edit = _load_edit(project)
    before = edit.duration

    # Resolved against the pre-cut timeline, all at once, before any span is
    # applied — this is what makes a list of notes taken against one watch
    # stay valid together.
    pieces_by_span = [edit.source_spans(start, end) for start, end in requests]

    transcripts: dict[str, tx.Transcript | None] = {}
    suspects: dict[str, dict[int, dict[str, Any]]] = {}
    flagged: list[dict[str, Any]] = []
    span_pieces: list[list[dict[str, Any]]] = []

    for (req_start, req_end), pieces in zip(requests, pieces_by_span):
        built: list[dict[str, Any]] = []
        last_i = len(pieces) - 1
        for i, (clip_id, start, end) in enumerate(pieces):
            lo = max(0.0, start - pad) if i == 0 else start
            hi = end + pad if i == last_i else end

            if clip_id not in transcripts:
                try:
                    transcripts[clip_id] = _transcript(project, clip_id)
                except tx.TranscriptError:
                    transcripts[clip_id] = None
                suspects[clip_id] = (
                    {item["index"]: item for item in _suspect_durations(transcripts[clip_id])}
                    if transcripts[clip_id] is not None
                    else {}
                )
            parsed = transcripts[clip_id]

            if parsed is None:
                built.append(
                    {
                        "clip_id": clip_id,
                        "lo": lo,
                        "hi": hi,
                        "words_overlapped": None,
                        "transcript_missing": True,
                        "context_before": [],
                        "context_after": [],
                    }
                )
                continue

            words = _overlap_words(parsed, lo, hi)
            for word in words:
                hit = suspects[clip_id].get(word["index"])
                if hit:
                    flagged.append({**hit, "clip_id": clip_id, "span": [req_start, req_end]})
            ctx = (
                _context(parsed, words[0]["index"], words[-1]["index"])
                if words
                else _nearest_context(parsed, lo, hi)
            )
            built.append(
                {"clip_id": clip_id, "lo": lo, "hi": hi, "words_overlapped": words, **ctx}
            )
        span_pieces.append(built)

    if flagged and not (plan or confirm_suspect):
        hit = flagged[0]
        raise tl.TimelineError(
            f"word {hit['index']} ({hit['text']!r}) in clip {hit['clip_id']!r} claims "
            f"{hit['duration']}s, more than {hit['limit']}s (the transcript's median x "
            "energy.CAP) — it likely hides a retake rather than ending where it "
            "claims, so it is refused as a cut boundary (PLAN.md § Suspect "
            "word durations). Check it, then retry with confirm_suspect=True "
            "(CLI: --confirm-suspect) if the boundary is actually fine, or pick "
            "a different span."
        )

    applied: list[dict[str, Any]] = []
    for (req_start, req_end), built in zip(requests, span_pieces):
        piece_results: list[dict[str, Any]] = []
        for piece in built:
            clip_id, lo, hi = piece["clip_id"], piece["lo"], piece["hi"]
            present = edit.covers(clip_id, lo, hi)
            touched = edit.remove(clip_id, lo, hi)
            entry = {
                "clip_id": clip_id,
                "source_start": lo,
                "source_end": hi,
                "words_overlapped": piece["words_overlapped"],
                "context_before": piece["context_before"],
                "context_after": piece["context_after"],
                "segments_touched": touched,
                "already_cut": present <= 0.0,
            }
            if piece.get("transcript_missing"):
                entry["transcript_missing"] = True
            piece_results.append(entry)
        applied.append(
            {"requested_start": req_start, "requested_end": req_end, "pieces": piece_results}
        )

    removed = before - edit.duration
    requested_removed = sum(end - start for start, end in requests)
    if pad == 0.0 and abs(removed - requested_removed) > tl.MIN_SEGMENT:
        raise tl.TimelineError(
            f"internal invariant failed: requested {requested_removed:.3f}s "
            f"removed but the timeline shrank by {removed:.3f}s — "
            "source_spans and remove disagree with each other; this should "
            "be unreachable"
        )

    if not plan:
        _save_edit(project, edit)

    result: dict[str, Any] = {
        "mode": "cut",
        "applied": applied,
        "duration_before": before,
        "duration_after": edit.duration,
        "removed": removed,
        "requested_removed": requested_removed,
        "segments": len(edit.segments),
    }
    if plan:
        result["plan"] = True
        result["suspect_boundaries"] = flagged
    return result


def restore(
    path: Path | str,
    clip_id: str,
    ranges: Sequence[Sequence[int]],
    *,
    pad: float = 0.0,
    plan: bool = False,
) -> dict[str, Any]:
    """Un-cut whichever part of these inclusive word ranges is not currently present.

    `Edit` stores only surviving segments (`timeline.py`'s module docstring)
    — there is no removed-ranges log to read back — so what is absent is
    derived: `Edit.gaps` is the complement of this clip's segments against
    its own registered duration, and `Edit.restore` splices back whatever
    part of the requested range falls in a gap. Ranges are word indices,
    resolved exactly like `cut_by_transcript`'s `cut=`/`keep=`, because the
    only thing pointing at un-cut material naturally is the transcript a
    person is reading, the same way a cut is made; there is no time-based
    form mirroring `cut_by_time`, because that exists to convert a render
    timestamp a human just watched, and material that is off the timeline
    has no render timestamp to convert from. `pad` mirrors
    `cut_by_transcript`'s own `pad`: pass the value used on the original cut
    to bring its padding sliver back too, not just the words.

    Only the part `Edit.gaps` says is actually absent comes back — material
    still on the timeline is left alone. A request spanning two separate
    cuts restores both, as separate pieces; a request only touching part of
    one cut restores only that part; a request over material that was never
    cut is reported `already_present: True`, not an error, mirroring
    `cut_by_transcript`'s `already_cut`.

    Restoring only ever brings back material the source recording already
    has (bounded by `_timeline_bound`: the clip's registered duration, or its
    picture's end if sooner), so the timeline
    stays a subset of the source throughout — this is not `vo_extend`
    (PLAN.md parks that separately), which would splice in material the
    source never had.

    There is no suspect-duration refusal here, unlike a cut: a boundary that
    looks like it swallowed a retake is exactly the kind of thing restore
    exists to bring back, not a mistake to guard against.

    Refused (`TimelineError`) if `clip_id` has no surviving segment anywhere
    in the edit — nothing left of it to splice the range next to — or if its
    segments are not contiguous in the edit (an interleaved multi-source
    timeline, which this does not support yet).

    `plan=True` resolves and reports without writing, identically to
    `cut_by_transcript`.
    """
    if not ranges:
        raise tx.TranscriptError("restore needs at least one word range")

    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    parsed = _transcript(project, clip_id)
    edit = _load_edit(project)
    before = edit.duration
    duration = _timeline_bound(project, clip)

    applied: list[dict[str, Any]] = []
    for first, last in ranges:
        first, last = int(first), int(last)
        word_start, word_end = parsed.span(first, last)
        lo, hi = max(0.0, word_start - pad), word_end + pad
        pieces = edit.restore(clip_id, lo, hi, duration=duration)
        applied.append(
            {
                **_echo(parsed, first, last, lo, hi),
                "source_start": lo,
                "source_end": hi,
                "restored": [
                    {"source_start": p_lo, "source_end": p_hi, "duration": p_hi - p_lo}
                    for p_lo, p_hi in pieces
                ],
                "restored_seconds": round(sum(p_hi - p_lo for p_lo, p_hi in pieces), 6),
                "already_present": not pieces,
            }
        )

    if not plan:
        _save_edit(project, edit)

    result: dict[str, Any] = {
        "clip_id": clip_id,
        "applied": applied,
        "duration_before": before,
        "duration_after": edit.duration,
        "restored": edit.duration - before,
        "segments": len(edit.segments),
    }
    if plan:
        result["plan"] = True
    return result


def locate(
    path: Path | str,
    clip_id: str,
    *,
    first: int | None = None,
    last: int | None = None,
    source_start: float | None = None,
    source_end: float | None = None,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Where does this source word or source time play in the current render?

    `cut_by_time`'s read-only mirror: that takes render time and resolves it
    back to source, this takes source and resolves it forward to render time.
    Answering it by hand meant reading `project.otio` and adding up segment
    durations, which is exactly the arithmetic every cut invalidates.

    Address it one way per call: `first`/`last` are inclusive word indices
    into the clip's transcript (`last` defaults to `first`, so one index
    locates one word), `source_start`/`source_end` are seconds in the
    original recording (`source_end` omitted locates an instant rather than
    an interval), or `phrase` — a phrase naturally *is* a range, so it
    resolves straight to `first`/`last` with no edge to pick
    (`Transcript.resolve`; `after`/`occurrence` disambiguate a phrase that
    matches more than once).

    The distinction the payload exists to keep straight is **cut** versus
    **never there**. An interval that has been edited out returns
    `present: false` with no placements; one that runs past the end of the
    recording returns `beyond_source` as well, because "you cut it" and "it
    was never recorded" are different problems and the empty list looks the
    same in both. A partially-cut interval is the normal case, not an error —
    `placements` reports each surviving piece with the source coordinates that
    say which part of the phrase it is, `covered` how much of it is left, and
    `contiguous` whether the survivors still play back-to-back.

    Word mode (and phrase mode, which resolves into it) echoes the words it
    resolved to plus the three either side, the same convention `cut --plan`
    uses (CLAUDE.md); time mode echoes the words the interval overlaps — an
    overlap test, never containment — or its nearest flanking words when it
    landed in silence. A clip with no transcript still locates by time;
    `words` is null and `transcript_missing` is set, rather than refusing a
    valid question about a picture-only clip. Read-only: nothing is written,
    and there is no `plan=`.

    **Two clocks, and this reports the Edit's.** `timeline_start`/
    `timeline_end` (and every `placements[].timeline_start`/`.timeline_end`)
    are Edit-relative — 0 = the `Edit`'s own first frame — unchanged whether
    or not a head is configured, because the web player cannot play a cold
    open yet and shifting this call's clock would desync it. `head_seconds`
    is reported alongside (0.0 with no head) so a caller that *does* need
    render time — where this actually plays in the exported file, this
    call's own stated purpose — can add it: render time = Edit time +
    `head_seconds`. Render-facing paths do their own offsetting instead of
    reading this field blind: `add_captions` shifts cues by `head_seconds`
    at its own call site, and `verify` trims heard words before it.
    """
    by_words = first is not None or last is not None
    by_time = source_start is not None or source_end is not None
    by_phrase = phrase is not None
    if sum([by_words, by_time, by_phrase]) > 1:
        raise tl.TimelineError(
            "pass first/last, source_start/source_end, or phrase — not both "
            "(or all three) — they are different ways of naming the same "
            "thing, and a call giving more than one cannot say which it meant"
        )
    if not by_words and not by_time and not by_phrase:
        raise tl.TimelineError(
            "locate needs something to locate: first= (a word index), "
            "source_start= (seconds into the recording), or phrase="
        )
    if by_words and first is None:
        raise tl.TimelineError("last= needs first= — a range has to start somewhere")
    if by_time and source_start is None:
        raise tl.TimelineError("source_end= needs source_start=")

    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    edit = _load_edit(project)

    parsed: tx.Transcript | None
    if by_words or by_phrase:
        parsed = _transcript(project, clip_id)
    else:
        try:
            parsed = _transcript(project, clip_id)
        except tx.TranscriptError:
            parsed = None

    if by_phrase:
        first, last = _resolve_word_or_phrase(
            parsed, word_index=None, phrase=phrase, after=after, occurrence=occurrence, edge="range"
        )
        by_words = True

    # An instant is a zero-width interval everywhere below; only the reported
    # mode and the echo differ, so resolve both shapes to one pair here.
    instant = False
    if by_words:
        assert first is not None
        last = first if last is None else last
        lo, hi = parsed.span(first, last)  # type: ignore[union-attr]
    else:
        assert source_start is not None
        lo = float(source_start)
        if source_end is None:
            hi = lo
            instant = True
        else:
            hi = float(source_end)
        if hi < lo:
            raise tl.TimelineError(f"interval {lo:.3f}-{hi:.3f} runs backwards")
        if lo < 0:
            raise tl.TimelineError(f"source time {lo:.3f} is negative")

    if instant:
        at = edit.timeline_time(clip_id, lo)
        placements = (
            [tl.Placement(timeline_start=at, timeline_end=at, source_start=lo, source_end=lo)]
            if at is not None
            else []
        )
    else:
        placements = edit.timeline_spans(clip_id, lo, hi)

    covered = sum(p.duration for p in placements)
    requested = hi - lo
    contiguous = all(a.contiguous_with(b) for a, b in pairwise(placements))

    mode = "phrase" if by_phrase else ("words" if by_words else ("instant" if instant else "time"))
    result: dict[str, Any] = {
        "clip_id": clip_id,
        "mode": mode,
        "source_start": lo,
        "source_end": hi,
        "present": bool(placements),
        "placements": [p.as_dict() for p in placements],
        "timeline_start": placements[0].timeline_start if placements else None,
        "timeline_end": placements[-1].timeline_end if placements else None,
        "requested": requested,
        "covered": covered,
        "fully_present": bool(placements) and (requested - covered) <= tl.MIN_SEGMENT,
        "contiguous": contiguous,
        "timeline_duration": edit.duration,
        # Edit-relative, unlike the numbers above it never is — see the
        # two-clock note above. 0.0 with no head configured.
        "head_seconds": _head_seconds(project),
    }

    # "Cut" and "never recorded" look identical from the placements alone —
    # missing either way — and only the clip's own duration tells them apart.
    # Say so, rather than let a short `covered` be read as an edit decision.
    duration = clip.get("duration")
    if duration is not None and hi > float(duration):
        end = float(duration)
        result["beyond_source"] = True
        result["source_duration"] = end
        # Zero for an instant past the end — hence the bool above rather than
        # letting a caller test this number's truthiness.
        result["beyond_source_seconds"] = hi - max(lo, end)

    if parsed is None:
        result["words"] = None
        result["transcript_missing"] = True
        return result

    if by_words:
        assert first is not None and last is not None
        result["words"] = [w.as_dict() for w in parsed.window(first, last)]
        result.update(_echo(parsed, first, last, lo, hi))
    else:
        words = _overlap_words(parsed, lo, hi)
        result["words"] = words
        result.update(
            _context(parsed, words[0]["index"], words[-1]["index"])
            if words
            else _nearest_context(parsed, lo, hi)
        )
    return result


def _run_words(words: Sequence[dict[str, Any]], run: tuple[float, float]) -> list[dict[str, Any]]:
    """Which (already timeline-mapped) words overlap a speech `run`.

    Overlap, never containment (CLAUDE.md): a word only partly inside the run
    still names it — the same test `_pad_reach`/`_overlap_words` use.
    """
    lo, hi = run
    return [
        {"index": w["index"], "text": w["text"]}
        for w in words
        if w["timeline_start"] < hi and w["timeline_end"] > lo
    ]


def speech_overlap(
    path: Path | str,
    clip_id: str,
    *,
    at: float = 0.0,
    clip_in: float | None = None,
    clip_out: float | None = None,
    vo_clip_id: str | None = None,
    max_gap: float = 0.3,
    min_seam: float = 0.5,
    cap: float = energy.CAP,
    clip_evidence: str = "auto",
) -> dict[str, Any]:
    """Does a *proposed* placement of `clip_id` overlap the VO's speech?

    The prerequisite check behind "can this clip speak here?" — answer it
    before designing any ducking. `at`/`clip_in`/`clip_out` describe where
    `clip_id` *would* sit on the timeline (defaults: unplaced at 0, its whole
    duration) — the clip need not be on the timeline yet, and usually isn't,
    since the current model is single-track (`timeline.py`'s module
    docstring). The VO side maps through the existing edit
    (`Edit.timeline_span`, exactly as captions map words); `clip_id`'s own
    words are not in the edit, so they map by direct offset against the
    proposed window instead — a third use of one clip's own transcript,
    alongside `attach_transcript`/`transcribe` and `cut_by_transcript`.

    Both sides are trimmed with `energy.believable` first — an inflated word
    duration hides a real seam behind it (CLAUDE.md; HISTORY.md § 2) — then
    merged into speech *runs* with `max_gap` tolerance, since a 0.05s gap
    between two words is not a usable seam to duck into.

    Read `overlaps` first: any entry means this placement would step on VO
    speech, not empty air — this is exactly the shape a word-level pass
    caught on Billy/Stu, where the clip's speech nearly fully covered a VO
    thesis line with no clean seam to duck into. `clean_seams` (>= `min_seam`
    wide) are the windows where `clip_id` could speak without touching the
    VO. Read-only: nothing is written, and there is no `plan=`.

    **`clip_id` need not carry a transcript.** The trial's one refusal was
    this tool asked of a b-roll clip — "does this footage have talking in
    it" is a picture decision, and a whole transcription of a clip nobody
    wants captions from is a steep price for it (TRIAL.md § The queue, item
    3). With no transcript the clip side falls back to `energy.sound_runs`:
    the clip's own envelope, thresholded between its quiet and loud tenths,
    reported as **sound, not speech** — a sting or a scored swell clears it
    too. `clip_evidence` says which was used (`"transcript"` / `"energy"`),
    and `clip_evidence="energy"` forces the fallback on a clip that has one,
    which is how the two were measured against each other (HISTORY.md § The
    trial's second queue). The VO side always needs its transcript: its
    words are what the seams are cut around.
    """
    if clip_evidence not in ("auto", "transcript", "energy"):
        raise ProjectError(
            f"clip_evidence must be 'auto', 'transcript' or 'energy', got {clip_evidence!r}"
        )
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    clip_parsed: tx.Transcript | None = None
    if clip_evidence != "energy":
        if project.transcript_path(clip_id).exists():
            clip_parsed = _transcript(project, clip_id)
        elif clip_evidence == "transcript":
            raise tx.TranscriptError(
                f"no transcript for {clip_id!r} — run `transcribe {clip_id}` for a "
                "word-level answer, or leave clip_evidence on 'auto' for the "
                "energy-only one"
            )

    clip_in = 0.0 if clip_in is None else float(clip_in)
    if clip_out is None:
        duration = clip.get("duration")
        if duration is None:
            raise media.MediaError(
                f"{clip_id!r} has no known duration — probe failed; pass clip_out explicitly"
            )
        clip_out = float(duration)
    else:
        clip_out = float(clip_out)
    if clip_out <= clip_in:
        raise tl.TimelineError(f"interval {clip_in:.3f}-{clip_out:.3f} is empty or backwards")
    if at < 0:
        raise tl.TimelineError(f"at={at:.3f} is negative — a placement cannot start before 0")

    edit = _load_edit(project)
    if not edit.segments:
        raise ProjectError("the VO timeline has no segments to overlap against")

    present = {seg.clip_id for seg in edit.segments}
    if vo_clip_id is None:
        if len(present) > 1:
            raise ProjectError(
                "the timeline has more than one clip "
                f"({', '.join(sorted(present))}) — pass vo_clip_id to say which one is the VO"
            )
        vo_clip_id = next(iter(present))
    elif vo_clip_id not in present:
        raise ProjectError(
            f"{vo_clip_id!r} is not on the timeline (present: {', '.join(sorted(present))})"
        )
    vo_parsed = _transcript(project, vo_clip_id)

    # Clip B side: not in the edit, so words map by direct offset against the
    # proposed [clip_in, clip_out) -> [at, at + (clip_out - clip_in)) window.
    clip_words: list[dict[str, Any]] = []
    clip_spans: list[tuple[float, float]] = []
    clip_energy: dict[str, Any] | None = None
    if clip_parsed is not None:
        evidence = "transcript"
        clip_full_trimmed = energy.believable(
            [(w.start, w.end) for w in clip_parsed.words], cap=cap
        )
        clip_hits = [w for w in clip_parsed.words if w.start < clip_out and w.end > clip_in]
        clip_trimmed = [
            trimmed
            for word, trimmed in zip(clip_parsed.words, clip_full_trimmed)
            if word.start < clip_out and word.end > clip_in
        ]
        for word, (bs, be) in zip(clip_hits, clip_trimmed):
            a, b = max(clip_in, bs), min(clip_out, be)
            if b <= a:
                continue
            t0, t1 = at + (a - clip_in), at + (b - clip_in)
            clip_words.append(
                {
                    "index": word.index,
                    "text": word.text,
                    "source_start": word.start,
                    "source_end": word.end,
                    "believable_start": bs,
                    "believable_end": be,
                    "timeline_start": t0,
                    "timeline_end": t1,
                }
            )
            clip_spans.append((t0, t1))
    else:
        evidence = "energy"
        if not clip.get("has_audio", True):
            raise media.MediaError(
                f"{clip_id!r} has no audio track, so it cannot speak over anything"
            )
        measured = energy.sound_runs(energy.envelope(energy.decode(media.media_path(project, clip))))
        clip_energy = {k: v for k, v in measured.items() if k != "runs"}
        clip_energy["runs_in_clip"] = len(measured["runs"])
        for rs, re_ in measured["runs"]:
            a, b = max(clip_in, rs), min(clip_out, re_)
            if b <= a:
                continue
            clip_spans.append((at + (a - clip_in), at + (b - clip_in)))

    # VO side: already in the edit, so words map through the same
    # Edit.timeline_span captions use. A fully-cut word is never heard.
    vo_trimmed = energy.believable([(w.start, w.end) for w in vo_parsed.words], cap=cap)
    vo_words: list[dict[str, Any]] = []
    vo_spans: list[tuple[float, float]] = []
    for word, (bs, be) in zip(vo_parsed.words, vo_trimmed):
        mapped = edit.timeline_span(vo_clip_id, bs, be)
        if mapped is None:
            continue
        t0, t1 = mapped
        vo_words.append(
            {
                "index": word.index,
                "text": word.text,
                "source_start": word.start,
                "source_end": word.end,
                "believable_start": bs,
                "believable_end": be,
                "timeline_start": t0,
                "timeline_end": t1,
            }
        )
        vo_spans.append((t0, t1))

    clip_run_spans = sp.merge_runs(clip_spans, max_gap=max_gap)
    vo_run_spans = sp.merge_runs(vo_spans, max_gap=max_gap)
    clip_runs = [
        {
            "timeline_start": lo,
            "timeline_end": hi,
            "duration": hi - lo,
            "words": _run_words(clip_words, (lo, hi)),
        }
        for lo, hi in clip_run_spans
    ]
    vo_runs = [
        {
            "timeline_start": lo,
            "timeline_end": hi,
            "duration": hi - lo,
            "words": _run_words(vo_words, (lo, hi)),
        }
        for lo, hi in vo_run_spans
    ]

    overlaps = [
        {
            "timeline_start": lo,
            "timeline_end": hi,
            "duration": hi - lo,
            "clip_words": _run_words(clip_words, (lo, hi)),
            "vo_words": _run_words(vo_words, (lo, hi)),
        }
        for lo, hi in sp.intersect_runs(clip_run_spans, vo_run_spans)
    ]

    clean_seams: list[dict[str, Any]] = []
    for run in clip_run_spans:
        for lo, hi in sp.subtract_runs(run, vo_run_spans):
            if hi - lo >= min_seam:
                clean_seams.append(
                    {
                        "timeline_start": lo,
                        "timeline_end": hi,
                        "duration": hi - lo,
                        "clip_words": _run_words(clip_words, (lo, hi)),
                    }
                )

    return {
        "clip_id": clip_id,
        "vo_clip_id": vo_clip_id,
        "at": at,
        "clip_in": clip_in,
        "clip_out": clip_out,
        "max_gap": max_gap,
        "min_seam": min_seam,
        "cap": cap,
        "clip_evidence": evidence,
        "clip_energy": clip_energy,
        "clip_words": clip_words,
        "vo_words": vo_words,
        "clip_runs": clip_runs,
        "vo_runs": vo_runs,
        "overlaps": overlaps,
        "clean_seams": clean_seams,
        "summary": {
            "clip_words": len(clip_words),
            "vo_words": len(vo_words),
            "clip_runs": len(clip_runs),
            "vo_runs": len(vo_runs),
            "overlap_count": len(overlaps),
            "overlap_seconds": round(sum(o["duration"] for o in overlaps), 3),
            "clean_seam_count": len(clean_seams),
            "clean_seam_seconds": round(sum(c["duration"] for c in clean_seams), 3),
            "clip_speech_seconds": round(sum(r["duration"] for r in clip_runs), 3),
            "vo_speech_seconds": round(sum(r["duration"] for r in vo_runs), 3),
        },
    }


def undo(path: Path | str) -> dict[str, Any]:
    """Roll the project back one mutation — the timeline, the manifest, or both.

    Most authoring state is manifest state now (the cue table, framing rects,
    the music bed, the caption style, head/tail/holds, marks, card records),
    so a snapshot is a **pair** and undo restores whichever halves it holds.
    The return says which, because the three answers look identical from
    outside and only one of them is "your cut came back":

    - `manifest_restored: false` on an older, timeline-only snapshot. The
      manifest is left exactly as it stands rather than guessed at.
    - `timeline_removed: true` where the snapshot pre-dates there being a
      timeline at all — undoing a `seed_timeline`. Nothing is loaded back,
      because the state being restored had nothing to load.

    Two consequences worth stating rather than special-casing. Undoing an
    import un-registers the clip and **leaves its media on disk** — a
    manifest is a registry, and deleting somebody's footage is not an undo.
    And undoing past a `pack_apply` rolls the pack back, which is correct: it
    was a mutation like any other.
    """
    project = Project.open(path)
    restored = project.restore()
    report: dict[str, Any] = {
        "restored_from": str(restored.timeline or restored.manifest),
        "timeline_restored": restored.timeline is not None,
        "manifest_restored": restored.manifest is not None,
        "timeline_removed": restored.timeline is None and restored.manifest is not None,
        "undo_depth": len(project.snapshots()),
    }
    if project.timeline_path.exists():
        edit = _load_edit(project)
        report["timeline_duration"] = edit.duration
        report["segments"] = len(edit.segments)
    else:
        # A pre-seed state has no `Edit` to measure, and reporting 0.0 would
        # read as an empty timeline rather than no timeline.
        report["timeline_duration"] = None
        report["segments"] = None
    if restored.manifest is None:
        report["note"] = (
            "this snapshot was written before proofcut saved manifests, so only "
            "the timeline came back — the cue table, framing, music bed and "
            "caption style are untouched"
        )
    if report["timeline_removed"]:
        report["note"] = (
            "the state before this snapshot had no timeline, so `project.otio` "
            "was removed — run `proofcut seed <clip_id>` to lay one down again"
        )
    return report


# -- what changed ------------------------------------------------------------

#: How many entries one list in a `changes` reply carries before it is
#: counted rather than listed. A `reel` removes dozens of spans and an import
#: can add a long clip record; the counts beside each list stay exact.
CHANGES_LIMIT = 40

#: Words shown for one changed span — the head and the tail of it, since the
#: edges are where a cut is judged. Every span also carries its full count.
CHANGES_SPAN_WORDS = 12

#: A changed span this short is counted, never listed: one frame at any rate
#: from 20 fps up. Two edits of the same recording disagree by a frame at
#: many segment edges — 28 of the 75 removed spans between split-detect's
#: silence-cut VO and the shipped one were single 1/30 s frames carrying no
#: word, and listed they filled most of the window ahead of the real cuts.
CHANGES_SLIVER = 0.05

#: The fields that name one record in a manifest list, so a record edited in
#: place reads as *changed* rather than as one removal beside one addition.
#: A key missing here still diffs, as added and removed records. Spelled as
#: literals because `REFRAME_KEY` and `UNSPOKEN_KEY` are defined further down.
_RECORD_IDENTITY: dict[str, tuple[str, ...]] = {
    "clips": ("clip_id",),
    "cues": ("clip_id", "word_index"),
    "unspoken": ("clip_id", "word_index"),
    "reframe": ("clip_id", "src_start"),
    "cards": ("card",),
}


def _source_sets(edit: tl.Edit | None) -> dict[str, list[tuple[float, float]]]:
    """Each clip's source material on the timeline, merged — order ignored."""
    if edit is None:
        return {}
    by_clip: dict[str, list[tuple[float, float]]] = {}
    for seg in edit.segments:
        by_clip.setdefault(seg.clip_id, []).append((seg.start, seg.end))
    return {clip: sp.merge_runs(spans, max_gap=0.0) for clip, spans in by_clip.items()}


def _span_words(parsed: tx.Transcript | None, lo: float, hi: float) -> dict[str, Any] | None:
    """The words a changed source span touches — by overlap, never containment,
    because a word's duration is whisper's and a retake hides inside one."""
    if parsed is None:
        return None
    hit = [w for w in parsed.words if (w.end > lo and w.start < hi) or (w.start == w.end and lo <= w.start < hi)]
    if not hit:
        return {"count": 0, "first_word": None, "last_word": None, "text": ""}
    half = CHANGES_SPAN_WORDS // 2
    if len(hit) <= CHANGES_SPAN_WORDS:
        text = " ".join(w.text for w in hit)
    else:
        text = " ".join(w.text for w in hit[:half]) + " … " + " ".join(w.text for w in hit[-half:])
    return {"count": len(hit), "first_word": hit[0].index, "last_word": hit[-1].index, "text": text}


def _changed_spans(
    ahead: dict[str, list[tuple[float, float]]],
    behind: dict[str, list[tuple[float, float]]],
    placed_in: tl.Edit | None,
    transcripts: dict[str, tx.Transcript | None],
) -> list[dict[str, Any]]:
    """Source material in `ahead` that `behind` does not have, placed on the
    timeline of `placed_in` (the edit it is actually on)."""
    out: list[dict[str, Any]] = []
    for clip_id in sorted(ahead):
        for run in ahead[clip_id]:
            for lo, hi in sp.subtract_runs(run, behind.get(clip_id, [])):
                if hi - lo < tl.MIN_SEGMENT:
                    continue  # float residue of an unchanged edge, not material
                entry: dict[str, Any] = {
                    "clip_id": clip_id,
                    "source_start": round(lo, 3),
                    "source_end": round(hi, 3),
                    "duration": round(hi - lo, 3),
                }
                if placed_in is not None:
                    pieces = placed_in.timeline_spans(clip_id, lo, hi)
                    if pieces:
                        entry["timeline_start"] = round(pieces[0].timeline_start, 3)
                words = _span_words(transcripts.get(clip_id), lo, hi)
                if words is not None:
                    entry["words"] = words
                out.append(entry)
    out.sort(key=lambda e: (e.get("timeline_start", float("inf")), e["clip_id"], e["source_start"]))
    return out


def _capped(key: str, items: list[Any]) -> dict[str, Any]:
    return {key: items[:CHANGES_LIMIT], f"{key}_count": len(items)}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _record_echo(record: Any, transcripts: dict[str, tx.Transcript | None]) -> Any:
    """A word-addressed record, with the word it addresses and three either side."""
    if not (isinstance(record, dict) and isinstance(record.get("word_index"), int)):
        return record
    parsed = transcripts.get(str(record.get("clip_id")))
    index = record["word_index"]
    if parsed is None or not 0 <= index < len(parsed):
        return record
    words = parsed.window(index, index, context=3)
    echo = " ".join(f"[{w.text}]" if w.index == index else w.text for w in words)
    return {**record, "echo": echo}


def _list_changes(
    key: str, before: list[Any], after: list[Any], transcripts: dict[str, tx.Transcript | None]
) -> dict[str, Any]:
    """Records added, removed, and — where the key names its records — changed."""
    remaining = [_canonical(item) for item in after]
    removed: list[Any] = []
    for item in before:
        text = _canonical(item)
        if text in remaining:
            remaining.remove(text)
        else:
            removed.append(item)
    added_pool = [json.loads(text) for text in remaining]

    changed: list[dict[str, Any]] = []
    fields = _RECORD_IDENTITY.get(key)
    if fields:
        def ident(item: Any) -> tuple[Any, ...] | None:
            if not isinstance(item, dict) or any(f not in item for f in fields):
                return None
            return tuple(_canonical(item[f]) for f in fields)

        for old in list(removed):
            name = ident(old)
            match = next((new for new in added_pool if name is not None and ident(new) == name), None)
            if match is None:
                continue
            removed.remove(old)
            added_pool.remove(match)
            differs = sorted(set(old) | set(match))
            changed.append(
                {
                    "record": _record_echo({f: match[f] for f in fields}, transcripts),
                    "fields": {
                        f: {"before": old.get(f), "after": match.get(f)}
                        for f in differs
                        if old.get(f) != match.get(f)
                    },
                }
            )

    out: dict[str, Any] = {}
    for label, items in (("added", added_pool), ("removed", removed)):
        if items:
            out.update(_capped(label, [_record_echo(i, transcripts) for i in items]))
    if changed:
        out.update(_capped("changed", changed))
    return out


def _manifest_changes(
    before: dict[str, Any], after: dict[str, Any], transcripts: dict[str, tx.Transcript | None]
) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        if key not in after:
            keys[key] = {"removed": True, "before": before[key]}
        elif key not in before:
            keys[key] = {"added": True, "after": after[key]}
        elif _canonical(before[key]) == _canonical(after[key]):
            continue
        elif isinstance(before[key], list) and isinstance(after[key], list):
            keys[key] = _list_changes(key, before[key], after[key], transcripts)
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            old, new = before[key], after[key]
            keys[key] = {
                "fields": {
                    f: {"before": old.get(f), "after": new.get(f)}
                    for f in sorted(set(old) | set(new))
                    if _canonical(old.get(f)) != _canonical(new.get(f))
                }
            }
        else:
            keys[key] = {"before": before[key], "after": after[key]}
    return keys


def changes(path: Path | str, *, steps: int = 1) -> dict[str, Any]:
    """What the last `steps` mutations did — exactly what `undo` that many
    times would roll back, stated as material and records rather than files.

    Nothing is stored for this: every mutation already leaves its pre-state in
    `cache/history/`, so the answer is that snapshot against the live project.
    The timeline half compares each clip's source material **as a set**, so a
    cut reads as the words it removed, not as every later segment moving up;
    a pure reorder, which changes no set, is `reordered`. A span under
    `CHANGES_SLIVER` is counted in `*_slivers` rather than listed. Transcripts are not
    snapshotted, so words are read off the transcript as it stands now — a
    transcript replaced in between echoes the new words.
    """
    project = Project.open(path)
    history = project.snapshots()
    if not history:
        raise ProjectError("nothing has changed — this project has no history")
    if steps < 1 or steps > len(history):
        raise ProjectError(
            f"steps must be between 1 and {len(history)} (this project's undo depth), not {steps}"
        )
    base = history[-steps]
    # A snapshot with no timeline half is a state that had none (`restore`'s
    # rule), so `None` there is a fact, not a gap.
    old_edit = tl.read(base.timeline) if base.timeline is not None else None
    new_edit = tl.read(project.timeline_path) if project.timeline_path.exists() else None

    clip_ids = {s.clip_id for e in (old_edit, new_edit) if e is not None for s in e.segments}
    old_manifest = (
        json.loads(base.manifest.read_text(encoding="utf-8")) if base.manifest is not None else None
    )
    new_manifest = project.read_manifest()
    for m in (old_manifest, new_manifest):
        for record in (m or {}).get("cues", []) + (m or {}).get(UNSPOKEN_KEY, []):
            if isinstance(record, dict) and record.get("clip_id"):
                clip_ids.add(str(record["clip_id"]))
    transcripts: dict[str, tx.Transcript | None] = {}
    for clip_id in sorted(clip_ids):
        try:
            transcripts[clip_id] = _transcript(project, clip_id)
        except (tx.TranscriptError, ValueError, OSError):
            transcripts[clip_id] = None

    def shape(edit: tl.Edit | None) -> dict[str, Any] | None:
        if edit is None:
            return None
        return {"duration": round(edit.duration, 3), "segments": len(edit.segments)}

    old_sets, new_sets = _source_sets(old_edit), _source_sets(new_edit)
    removed = _changed_spans(old_sets, new_sets, old_edit, transcripts)
    added = _changed_spans(new_sets, old_sets, new_edit, transcripts)
    order = lambda e: [(s.clip_id, s.start, s.end) for s in e.segments] if e else []
    def listed(spans: list[dict[str, Any]], key: str) -> dict[str, Any]:
        slivers = [e for e in spans if e["duration"] < CHANGES_SLIVER]
        return {
            **_capped(key, [e for e in spans if e["duration"] >= CHANGES_SLIVER]),
            f"{key}_slivers": {"count": len(slivers), "seconds": round(sum(e["duration"] for e in slivers), 3)},
        }

    timeline: dict[str, Any] = {
        "before": shape(old_edit),
        "after": shape(new_edit),
        **listed(removed, "removed"),
        **listed(added, "added"),
        "reordered": bool(old_edit and new_edit and not removed and not added
                          and order(old_edit) != order(new_edit)),
    }
    timeline["changed"] = bool(
        removed or added or timeline["reordered"] or (old_edit is None) != (new_edit is None)
    )
    if old_edit is None and new_edit is not None:
        timeline["note"] = "there was no timeline before — this is where it was seeded"
    elif old_edit is not None and new_edit is None:
        timeline["note"] = "the timeline has been removed since"

    report: dict[str, Any] = {
        "steps": steps,
        "undo_depth": len(history),
        "snapshot": base.index,
        "timeline": timeline,
    }
    if old_manifest is None:
        report["manifest"] = None
        report["note"] = (
            "this snapshot was written before proofcut saved manifests, so only the "
            "timeline can be compared"
        )
    else:
        keys = _manifest_changes(old_manifest, new_manifest, transcripts)
        report["manifest"] = {"changed_keys": sorted(keys), "keys": keys}
    report["unchanged"] = not timeline["changed"] and not (
        report["manifest"] and report["manifest"]["changed_keys"]
    )
    if any(t is None for t in transcripts.values()):
        report["untranscribed"] = sorted(c for c, t in transcripts.items() if t is None)
    return report


# -- attenuating noise, rather than cutting it ----------------------------


def _neighbours(
    parsed: tx.Transcript, trimmed_ends: Sequence[float], gap: dict[str, Any]
) -> tuple[int, int] | None:
    """The two consecutive word indices a `loud_gaps` gap sits between.

    `energy.believable` only ever trims a span's *end*, so a gap's `start` is
    a trimmed end and its `end` is an untrimmed next-word start. Both sides
    already went through the same `round(x, 3)` proofcut applies everywhere, so
    this is an exact match, not a fuzzy one.
    """
    for i in range(len(parsed) - 1):
        if (
            round(trimmed_ends[i], 3) == gap["start"]
            and round(parsed.words[i + 1].start, 3) == gap["end"]
        ):
            return i, i + 1
    return None


def _classify_noise_events(
    parsed: tx.Transcript,
    trimmed_ends: Sequence[float],
    gaps: Sequence[dict[str, Any]],
    *,
    max_event_seconds: float,
    max_gap_seconds: float,
    pad: float,
    suspect: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The safety filter, isolated from I/O so it can be tested without ffmpeg.

    Every loud run in every gap becomes one event, tagged `"attenuated"`,
    `"suspect_neighbour"`, or `"disqualified"` — never both length and
    width reasons collapsed into one, so a caller can tell which side of the
    filter actually caught a given event.
    """
    events: list[dict[str, Any]] = []
    for gap in gaps:
        neighbours = _neighbours(parsed, trimmed_ends, gap)
        before_idx, after_idx = neighbours if neighbours else (None, None)
        neighbour_before = (
            {"index": before_idx, "text": parsed.words[before_idx].text}
            if before_idx is not None
            else None
        )
        neighbour_after = (
            {"index": after_idx, "text": parsed.words[after_idx].text}
            if after_idx is not None
            else None
        )

        for run in gap["runs"]:
            reasons: list[str] = []
            if run["duration"] > max_event_seconds:
                reasons.append(
                    f"event is {run['duration']}s, longer than "
                    f"max_event_seconds={max_event_seconds}"
                )
            if gap["duration"] > max_gap_seconds:
                reasons.append(
                    f"gap is {gap['duration']}s, wider than max_gap_seconds="
                    f"{max_gap_seconds} — too wide to prove the word map is "
                    "dense around this event"
                )
            if neighbours is None:
                reasons.append("could not resolve the words bounding this gap")

            if reasons:
                status = "disqualified"
            elif (before_idx is not None and before_idx in suspect) or (
                after_idx is not None and after_idx in suspect
            ):
                status = "suspect_neighbour"
                reasons = [
                    (
                        "a word bounding this gap claims a suspect duration, so "
                        "the narrow gap that qualified this event might itself be "
                        "hiding a swallowed retake"
                    )
                ]
            else:
                status = "attenuated"

            events.append(
                {
                    "start": run["start"],
                    "end": run["end"],
                    "duration": run["duration"],
                    "padded_start": max(gap["start"], round(run["start"] - pad, 3)),
                    "padded_end": min(gap["end"], round(run["end"] + pad, 3)),
                    "peak_db": run["peak_db"],
                    "gap": {
                        "start": gap["start"],
                        "end": gap["end"],
                        "duration": gap["duration"],
                    },
                    "neighbour_before": neighbour_before,
                    "neighbour_after": neighbour_after,
                    "status": status,
                    "reasons": reasons,
                }
            )
    return events


def attenuate_noises(
    path: Path | str,
    clip_id: str,
    *,
    db: float = -12.0,
    max_event_seconds: float = 1.5,
    max_gap_seconds: float = 2.0,
    pad: float = 0.05,
    confirm_suspect: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Pull down short loud non-speech events sitting in narrow word-map gaps.

    A word map has holes, and not everything loud in one is noise — the
    Scream v1 false positive was 0.4-0.9s events that turned out to be speech
    sitting in a 4.12s hole the transcript never wrote down (HISTORY.md § 2,
    `ideas/scream.md`). So an event only qualifies automatically when it is
    both short (`max_event_seconds`) *and* sitting in a gap narrow enough to
    prove the map is dense around it (`max_gap_seconds`) — a wide gap
    disqualifies even a very short, very loud event, because a narrow event
    duration is not evidence the *map* is trustworthy there. Qualifying
    events are pulled down `db` (not cut) via `energy.attenuate`'s
    `volume=...:enable='between(t,a,b)'` pass, padded `pad` seconds so the
    gain step lands in near-silence rather than clicking on the noise's edge.

    Unlike `cut_by_transcript`/`cut_by_time`, nothing here ever raises on
    what the scan finds. Those ops act on a handful of explicit,
    deliberately-chosen ranges, so refusing the call to force a look is
    right. This is an automatic scan that can turn up many independent
    candidates across a long clip; refusing the whole pass over one distant
    ambiguous candidate would defeat the point. So `suspect_neighbours` and
    `disqualified` are withheld *per event* and always reported in full —
    not gated behind `plan` the way `cut_by_transcript`'s
    `suspect_boundaries` is — which is a deliberate divergence from that
    convention, not an oversight of it.

    Three tiers: an event that qualifies on duration+gap-width *and* whose
    bounding words carry no suspect duration is attenuated automatically. An
    event whose bounding word does carry one (`suspect_neighbour`) is
    withheld from writing unless `confirm_suspect=True` — the neighbour
    might itself be hiding a swallowed retake, which would make the "gap is
    narrow" evidence unsound. `suspect_neighbours` is reported in full
    regardless of `confirm_suspect`/`plan`, so a caller can review before
    confirming; only whether it gets *written* depends on `confirm_suspect`.
    An event too long, or in too wide a gap (`disqualified`), is never
    written — no confirmation overrides it.

    Always reads the clip's *original* media (`media.original_media_path`),
    never a previous `"attenuated"` copy, so re-running with different
    parameters fully overwrites the derived file rather than compounding
    gain. `media_path()` picks the attenuated copy up automatically
    everywhere downstream once this has run.

    `plan=True` runs the identical classification — `to_write` is gated by
    `confirm_suspect` alone, exactly as a real run gates it — and reports the
    same payload, including `output_media`, the path a real run with the same
    `confirm_suspect` would write to, without calling ffmpeg or touching the
    manifest.
    """
    project = Project.open(path)
    clip = media.get_clip(project, clip_id)
    if not clip.get("has_audio"):
        raise media.MediaError(f"clip {clip_id!r} has no audio track to attenuate")
    parsed = _transcript(project, clip_id)
    source = media.original_media_path(project, clip)

    env = energy.envelope(energy.decode(source))
    claimed = [(w.start, w.end) for w in parsed.words]
    trimmed_ends = [end for _, end in energy.believable(claimed)]
    scan = energy.loud_gaps(claimed, env)

    suspect = {item["index"]: item for item in _suspect_durations(parsed)}
    events = _classify_noise_events(
        parsed,
        trimmed_ends,
        scan["gaps"],
        max_event_seconds=max_event_seconds,
        max_gap_seconds=max_gap_seconds,
        pad=pad,
        suspect=suspect,
    )

    to_write = [
        event
        for event in events
        if event["status"] == "attenuated"
        or (event["status"] == "suspect_neighbour" and confirm_suspect)
    ]

    output_media: Path | None = None
    written = False
    if to_write:
        output_media = project.attenuated_dir / f"{clip_id}{source.suffix}"
        if not plan:
            project.attenuated_dir.mkdir(parents=True, exist_ok=True)
            # Padded spans from adjacent runs in one gap can overlap; merge
            # them before building the filtergraph so ffmpeg's comma-chained
            # `volume` filters never double-attenuate the same window. This
            # is write-side only — `to_write`/`events` still report one entry
            # per detected event.
            raw_spans = [(event["padded_start"], event["padded_end"]) for event in to_write]
            spans = sp.merge_runs(raw_spans, max_gap=0.0)
            energy.attenuate(
                source, spans, db=db, has_video=bool(clip.get("has_video")), output=output_media
            )
            manifest = project.read_manifest()
            for record in manifest.get("clips", []):
                if record["clip_id"] == clip_id:
                    record["attenuated"] = str(output_media.relative_to(project.root))
                    record["attenuation"] = {
                        "db": db,
                        "max_event_seconds": max_event_seconds,
                        "max_gap_seconds": max_gap_seconds,
                        "pad": pad,
                        "events": len(to_write),
                    }
                    break
            project.write_manifest(manifest)
            written = True

    result: dict[str, Any] = {
        "clip_id": clip_id,
        "db": db,
        "gain": round(10 ** (db / 20), 4),
        "max_event_seconds": max_event_seconds,
        "max_gap_seconds": max_gap_seconds,
        "pad": pad,
        "threshold_db": scan["threshold_db"],
        "quiet_db": scan["quiet_db"],
        "speech_db": scan["speech_db"],
        "doubted_durations": scan["doubted_durations"],
        "events": events,
        "attenuated": to_write,
        "suspect_neighbours": [e for e in events if e["status"] == "suspect_neighbour"],
        "disqualified": [e for e in events if e["status"] == "disqualified"],
        "source_media": str(source),
        "output_media": str(output_media) if output_media else None,
        "written": written,
    }
    if plan:
        result["plan"] = True
    return result


# -- getting the edit out ------------------------------------------------


#: Frame rate for NLE exports from an audio-only project. Arbitrary but sane;
#: override with `fps` to match the picture the VO will be cut against.
DEFAULT_EXPORT_FPS = 30.0


#: What `export` will write itself, rather than asking auto-editor to. Both
#: names produce the same document — `.kdenlive` *is* MLT, and the extension
#: is the only thing Kdenlive cares about.
MLT_EXPORT_FORMATS = {"kdenlive", "mlt"}


#: Named export bundles (docs/plans/DAYDREAM.md § Export presets). Each maps to values
#: for the same four consumer keys `picture.RENDER_ARGS` already hardcodes —
#: `vcodec`/`crf`/`preset`/`acodec` — because those four, together, are the
#: combination HISTORY.md § 4 measured as memory-safe on the melt path;
#: adding a fifth key (`ab`/`width`/`height`/`progressive`) is what correlated
#: with the growth to 14.6 GB that froze the machine, and no one has since
#: isolated which addition caused it. `youtube`'s values are
#: `picture.RENDER_ARGS` verbatim — naming it changes nothing about what melt
#: already does. `web` only varies values within the same four keys.
#:
#: `tiktok-reels` arrived with PLAN.md § Aspect swap step 5, and it carries
#: `youtube`'s four values on purpose: both platforms re-encode what they are
#: given, so the upload wants the highest-quality source the measured-safe
#: keys can express, and there is no fifth key to reach for. **What the entry
#: actually adds is the aspect it asserts** (`PRESET_ASPECT`) — because the
#: canvas half of this preset belongs to the project rather than to an export
#: flag. `canvas` is where the shape is decided; a preset that quietly set it
#: would be an export argument reshaping a project, which is the same class of
#: silent wrong output `export`-picks-its-writer-from-the-project exists to
#: prevent. So the preset checks and refuses with the fix in it, and never
#: writes.
EXPORT_PRESETS: dict[str, dict[str, str]] = {
    "youtube": {"vcodec": "libx264", "crf": "18", "preset": "medium", "acodec": "aac"},
    "web": {"vcodec": "libx264", "crf": "23", "preset": "faster", "acodec": "aac"},
    "tiktok-reels": {"vcodec": "libx264", "crf": "18", "preset": "medium", "acodec": "aac"},
}


#: The frame shape a preset's *name* claims, for the presets whose name is a
#: claim about geometry. Checked against the canvas in force at export, never
#: applied — see `EXPORT_PRESETS`. Exact rather than "portrait enough": both
#: platforms specify 9:16, and a preset named after that spec accepting
#: 19.5:9 would be guessing on the caller's behalf about a shape the caller
#: can simply state. A vertical canvas that is not 9:16 is a legitimate
#: export; it just goes out under `youtube` or `web`.
PRESET_ASPECT: dict[str, tuple[int, int]] = {"tiktok-reels": (9, 16)}


class PresetCanvasError(ProjectError):
    """`_check_preset_canvas` refusing a preset, carrying the refusal's two parts.

    A `ProjectError`, so every caller catching that is unchanged. The type
    exists for the window, `media.MultiAudioError`'s precedent: Finish draws a
    refusing preset as a quiet card saying what it `needs`, and opens the full
    message and the `fix` command only when asked — and it must not get those
    by cutting up the sentence. `fix` is `None` where no single command fixes
    it (a project with no picture). HISTORY.md § The refusing preset card.
    """

    def __init__(self, message: str, *, needs: str, fix: str | None) -> None:
        super().__init__(message)
        self.needs = needs
        self.fix = fix


def _suggest_canvas(aspect: tuple[int, int]) -> str:
    """A concrete `WIDTHxHEIGHT` to put in a refusal, at the delivery size.

    Scaled so the *shorter* edge lands on 1080 — 9:16 → `1080x1920`, and a
    landscape ratio would come out `1920x1080` rather than upside down. The
    factor is even at every ratio that reaches here, which is what keeps the
    suggestion something `_parse_canvas` will actually accept.
    """
    factor = max(1, 1080 // min(aspect))
    return f"{aspect[0] * factor}x{aspect[1] * factor}"


def _check_preset_canvas(project: Project, preset: str | None) -> None:
    """Refuse a preset whose name claims a shape this project does not render at.

    The failure being closed is a landscape file with a vertical name on it:
    every downstream check passes, because nothing but the preset's name ever
    said the frame should be 9:16. The message names the one command that
    fixes it rather than describing the problem, since `canvas` is also what
    reports what the crop costs.

    **A project with no picture is refused separately, and not by geometry.**
    `_mlt_resolution` falls back to 1080p for an audio-only project, so the
    shared path would refuse a vertical preset by quoting a frame size that
    project does not have — a true refusal for a false reason. Asked of the
    manifest rather than of the rendered payload, deliberately: every other
    canvas derivation in this file walks `clips`, and finding 4 of PLAN.md
    § Aspect swap is about what happens when two of them stop agreeing.
    """
    want = PRESET_ASPECT.get(preset or "")
    if want is None:
        return
    if not any(clip.get("has_video") for clip in project.read_manifest().get("clips", [])):
        raise PresetCanvasError(
            f"preset {preset!r} names a frame shape ({want[0]}:{want[1]}) and this project "
            "has no picture to shape — the render would be audio. Drop the preset, or use "
            "'youtube'/'web', which claim nothing about the frame.",
            needs="needs a project with picture",
            fix=None,
        )
    width, height = _mlt_resolution(project)
    if width * want[1] != height * want[0]:
        # Cross-multiplied rather than compared as floats: the canvas is a pair
        # of integers and 1080/1920 is not exactly representable, so a ratio
        # test would refuse a shape that is exactly right.
        suggest = _suggest_canvas(want)
        raise PresetCanvasError(
            f"preset {preset!r} renders {want[0]}:{want[1]}, and this project's canvas is "
            f"{width}x{height} ({_aspect(width, height)}) — a preset names the encode, and "
            "the shape a project renders at is `canvas`'s job rather than an export flag's, "
            "so honouring this one would mean an export argument reshaping the project. "
            f"Set the shape first (`proofcut canvas {suggest}`), which "
            "routes through the MLT writer, crops to fill rather than pillarboxing, and "
            "reports what each clip loses; then export again. A vertical canvas that is not "
            f"{want[0]}:{want[1]} is a legitimate export — use 'youtube' or 'web' with it.",
            needs=f"needs a {want[0]}:{want[1]} canvas",
            fix=f"proofcut canvas {suggest}",
        )


def _resolve_preset(
    preset: str | None, resolution: tuple[int, int] | None
) -> dict[str, str] | None:
    """The consumer/quality bundle a preset name means, or `None` for the
    behavior-preserving default (`picture.RENDER_ARGS`, no `-res`).

    `"custom"` is not a fixed bundle — it means "apply `resolution` and leave
    quality at the `youtube`-equivalent default" (docs/plans/DAYDREAM.md's literal
    "resolution + quality" would mean accepting raw vcodec/crf/preset/acodec
    values from a caller, which widens the melt consumer to combinations
    HISTORY.md § 4 never measured; narrowed here on purpose). It requires
    `resolution` — nothing to customize is a likely caller mistake, not a
    legitimate no-op.
    """
    if preset is None:
        return None
    if preset == "custom":
        if resolution is None:
            raise ProjectError(
                "preset='custom' with no resolution customizes nothing — pass "
                "`resolution=(width, height)`, or drop the preset and use the "
                "default, 'youtube', or 'web'"
            )
        return dict(EXPORT_PRESETS["youtube"])
    if preset not in EXPORT_PRESETS:
        raise ProjectError(
            f"no export preset named {preset!r}. Available: "
            f"{sorted([*EXPORT_PRESETS, 'custom'])}. A preset names the encode only — "
            "the shape a project renders at is `canvas`'s, and 'tiktok-reels' checks "
            "it rather than setting it."
        )
    return dict(EXPORT_PRESETS[preset])


#: `bundle`'s keys, in the order auto-editor's own flags read them.
_AUTOEDITOR_QUALITY_FLAGS = {
    "vcodec": "-c:v",
    "crf": "-crf",
    "preset": "-preset",
    "acodec": "-c:a",
}


def _autoeditor_render_args(
    bundle: dict[str, str] | None, resolution: tuple[int, int] | None
) -> list[str]:
    """auto-editor argv for a resolved preset bundle plus an explicit resolution.

    Only ever built for the render path (`export_format=None`) — a v3 export
    writes a project file, which has no bitrate to set (`export()` refuses
    the combination before this is called).
    """
    args: list[str] = []
    if bundle is not None:
        for key, flag in _AUTOEDITOR_QUALITY_FLAGS.items():
            args += [flag, bundle[key]]
    if resolution is not None:
        args += ["-res", f"{resolution[0]},{resolution[1]}"]
    return args


def _melt_consumer_args(bundle: dict[str, str] | None) -> tuple[str, ...]:
    """The melt consumer argv for a resolved preset bundle: `picture.RENDER_ARGS`
    unchanged for the default, or `key=value` pairs for a named bundle —
    never a new key, only new values for the four already there.
    """
    if bundle is None:
        return picture.RENDER_ARGS
    return tuple(f"{key}={value}" for key, value in bundle.items())


#: Where a project keeps the shape it renders at, as `WIDTHxHEIGHT`. Absent
#: means "derive from the footage", which is exactly what every project
#: written before this key existed meant — so it is additive the way
#: `CAPTION_STYLE_KEY` is, and gets no `SCHEMA_VERSION` bump for the same
#: reason: a bump would make `Project.open` refuse every project on disk to
#: gain nothing. PLAN.md § Aspect swap.
CANVAS_KEY = "canvas"


def _parse_canvas(value: Any) -> tuple[int, int]:
    """`WIDTHxHEIGHT` → a pair, refusing what the encoders refuse quietly.

    The odd dimension is the one worth naming: libx264 at `yuv420p`
    subsamples chroma by two, so an odd edge is padded or refused depending
    on which link in the chain notices first — and a canvas that comes back
    one pixel different from the one asked for is the silent-wrong-output
    this whole item exists to avoid.
    """
    text = str(value).strip().lower().replace("×", "x")
    parts = text.split("x")
    if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
        raise ProjectError(f"canvas must read WIDTHxHEIGHT (e.g. 1080x1920), not {value!r}")
    width, height = (int(part) for part in parts)
    if width <= 0 or height <= 0:
        raise ProjectError(f"canvas must be positive, not {width}x{height}")
    if width % 2 or height % 2:
        raise ProjectError(
            f"canvas must be even on both edges, not {width}x{height} — libx264 at "
            "yuv420p subsamples chroma by two, and an odd edge is padded or refused "
            "depending on which link in the chain notices first"
        )
    return width, height


def _stored_canvas(project: Project) -> tuple[int, int] | None:
    """The project's canvas override, or None to derive from the footage."""
    stored = project.read_manifest().get(CANVAS_KEY)
    return None if stored is None else _parse_canvas(stored)


def _footage_resolution(project: Project) -> tuple[int, int]:
    """The shape the footage itself implies: the first real picture in the
    project, else 1080p. Cards are not consulted — scaling a still to the
    canvas is normal; sizing the canvas to a still is not.
    """
    for clip in project.read_manifest().get("clips", []):
        if clip.get("has_video") and clip.get("width") and clip.get("height"):
            return int(clip["width"]), int(clip["height"])
    return mlt.DEFAULT_RESOLUTION


def _aspect(width: int, height: int) -> str:
    """`1080x1920` → `9:16`. Reported because it is the question actually
    being asked, and because two canvases that differ only in scale are the
    same decision while two that differ in ratio are not.
    """
    divisor = gcd(width, height) or 1
    return f"{width // divisor}:{height // divisor}"


def _canvas_crop_report(project: Project, resolution: tuple[int, int]) -> dict[str, Any]:
    """What a canvas costs in footage: which clips crop, and which cannot.

    Reports rather than raises, and that is the whole reason it exists.
    A stored rect is kept as asked and refit to the canvas in force, so a
    canvas change can leave one that no longer fits — and discovering that by
    having `canvas` raise *after* it has written the manifest would leave the
    project half-swapped. `export` still refuses such a project; this is the
    warning that says which clip to fix and with what.
    """
    asked = _stored_reframes(project)
    cropped, conflicts = [], []
    for clip in project.read_manifest().get("clips", []):
        clip_id = str(clip.get("clip_id"))
        try:
            entry = _clip_reframe(clip, asked.get(clip_id), resolution)
        except ProjectError as error:
            conflicts.append({"clip_id": clip_id, "why": str(error)})
            continue
        if entry is not None and not entry.is_identity(resolution):
            cropped.append(clip_id)
    return {
        "cropped": cropped,
        "reframe_conflicts": conflicts,
    }


def canvas(
    path: Path | str,
    *,
    size: str | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the shape this project renders at.

    **The canvas is project state and every frame size is derived from it**,
    the same separation `caption_style` has and for the same reason: the MLT
    profile and the captions' reference canvas both read this, so a project
    cannot end up quoting caption sizes against one shape while rendering
    another. Called with no arguments it changes nothing and reports what is
    in force, including the footage-derived shape it would fall back to.

    Setting one has a routing consequence, reported as `routes_through`: an
    overridden project renders through the MLT writer whatever its source
    count, because auto-editor's `-res` letterboxes and has no reframe to
    teach (PLAN.md § Aspect swap). `reset` drops the override and returns the
    project to deriving from its footage. `plan` resolves without writing.

    An override that changes the *aspect* crops to fill rather than
    pillarboxing, so what it costs is footage rather than frame. `cropped`
    names every clip that loses some; which part each one keeps is `reframe`'s
    to report and to override.
    """
    if size is not None and reset:
        raise ProjectError("pass a size or `reset`, not both")

    project = Project.open(path)
    stored = _stored_canvas(project)
    if size is not None:
        after: tuple[int, int] | None = _parse_canvas(size)
    elif reset:
        after = None
    else:
        after = stored

    write = (size is not None or reset) and not plan
    if write:
        manifest = project.read_manifest()
        if after is None:
            manifest.pop(CANVAS_KEY, None)
        else:
            manifest[CANVAS_KEY] = f"{after[0]}x{after[1]}"
        project.write_manifest(manifest)

    footage = _footage_resolution(project)
    width, height = after or footage
    canvas_now = f"{width}x{height}"
    has_clips = any(c.get("has_video") for c in project.read_manifest().get("clips", []))
    records = _card_records(project)
    recorded = {r.get("card") for r in records}
    return {
        "project": str(project.root),
        "canvas": canvas_now,
        "width": width,
        "height": height,
        "aspect": _aspect(width, height),
        "source": "override" if after else ("footage" if has_clips else "default"),
        "footage": f"{footage[0]}x{footage[1]}",
        "footage_aspect": _aspect(*footage),
        # The consequence of setting one, said out loud rather than discovered
        # at export: auto-editor cannot be handed this.
        "routes_through": "mlt" if after else "auto-editor or mlt, by source count",
        # True since the reframe landed — the question worth asking now is not
        # whether the frame is filled but what filling it costs, so the clips
        # paying for it are named beside it.
        "fills_frame": True,
        # Against `(width, height)` rather than through `reframe`, which would
        # read the stored canvas — under `plan` that is the shape being
        # replaced, and the whole point of planning is to see what the new one
        # costs before writing it.
        **_canvas_crop_report(project, (width, height)),
        "captions_reference": "{}x{}".format(*captions.canvas(width, height)),
        # Cards are the only project state a canvas change cannot re-derive on
        # its own (PLAN.md § Aspect swap, finding 5), so the moment the shape
        # moves is the moment to name the ones now drawn at the old one.
        # Reported by both arms: reading the canvas is also how you ask
        # whether the cards agree with it.
        "cards_stale": [str(r.get("card")) for r in records if r.get("canvas") != canvas_now],
        "cards_unrecorded": [c for c in _cards_on_disk(project) if c not in recorded],
        "written": write,
        "reset": bool(reset),
        "plan": bool(plan),
    }


#: `reel` takes a `canvas=` argument, which shadows the function above inside
#: its body — the same collision `describe` and `verify` have with their
#: modules, and the same fix. Aliased here rather than worked around there, so
#: the argument keeps the name the CLI flag and the MCP tool use.
_set_canvas = canvas


#: Per-clip crop rects, `[{"clip_id", "rect": [x, y, w, h]}]` in **source
#: pixels**. Geometry and never a length, so an edit cannot invalidate one
#: (PLAN.md § Aspect swap) — and the rect stored is the one *asked for*, refit
#: to whatever canvas is in force at render time, so a canvas change cannot
#: invalidate one either.
#:
#: Additive and optional, and so no `SCHEMA_VERSION` bump — the
#: `CANVAS_KEY`/`CAPTION_STYLE_KEY` shape rather than the `cards` one. Absent
#: means "centre-crop every clip", which is a complete answer rather than a
#: gap: the migration a bump would carry is `setdefault([])`, and CLAUDE.md's
#: bar is that a bump exists where the number is what makes the key true.
#: Nothing about an older project is untrue without it.
REFRAME_KEY = "reframe"


def _parse_rect(value: Any) -> tuple[int, int, int, int]:
    """`X,Y,W,H` → a rect in source pixels, refusing the degenerate shapes."""
    if isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value]
    else:
        parts = [part.strip() for part in str(value).replace(" ", ",").split(",") if part.strip()]
    if len(parts) != 4 or not all(part.lstrip("-").isdigit() for part in parts):
        raise ProjectError(f"a crop rect must read X,Y,W,H in source pixels, not {value!r}")
    x, y, width, height = (int(part) for part in parts)
    if width <= 0 or height <= 0:
        raise ProjectError(f"a crop rect must be positive, not {width}x{height}")
    if x < 0 or y < 0:
        raise ProjectError(f"a crop rect starts inside the source, not at {x},{y}")
    return x, y, width, height


def _fit_rect_to_canvas(
    rect: tuple[int, int, int, int],
    source: tuple[int, int],
    resolution: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Grow a requested rect to the canvas's aspect, keeping it inside the source.

    **Grow rather than shrink, and the asymmetry is the whole argument.** A
    box drawn round a subject cannot be shown as-is in a frame of a different
    shape — something has to give. Growing keeps everything asked for on
    screen and pulls in surroundings; shrinking would keep the surroundings
    out and cut the subject in half. The second is the wrong-video failure
    this item exists to close, so the ask is treated as a floor.

    Recentred on the ask, then shifted whole to stay inside the source — a
    rect that leaves the frame renders MLT's idea of what is past the edge,
    not the footage's. When even the grown rect cannot fit, that is refused
    rather than quietly clipped, and the refusal names the largest rect that
    would have worked.
    """
    x, y, width, height = rect
    src_w, src_h = source
    canvas_w, canvas_h = resolution
    if x + width > src_w or y + height > src_h:
        raise ProjectError(
            f"crop rect {x},{y},{width},{height} runs past the source's {src_w}x{src_h}"
        )
    if width * canvas_h >= height * canvas_w:
        grown_w, grown_h = width, round(width * canvas_h / canvas_w)
    else:
        grown_w, grown_h = round(height * canvas_w / canvas_h), height
    if grown_w > src_w or grown_h > src_h:
        largest = mlt.centre_crop(source, resolution)
        raise ProjectError(
            f"crop rect {x},{y},{width},{height} cannot be shown whole in a "
            f"{canvas_w}x{canvas_h} frame — grown to that shape it is "
            f"{grown_w}x{grown_h}, past the source's {src_w}x{src_h}. The largest "
            f"rect that fits is {largest[0]},{largest[1]},{largest[2]},{largest[3]}"
        )
    grown_x = min(max(round(x + width / 2 - grown_w / 2), 0), src_w - grown_w)
    grown_y = min(max(round(y + height / 2 - grown_h / 2), 0), src_h - grown_h)
    return grown_x, grown_y, grown_w, grown_h


#: One stored window: where in the source it starts, the rect asked for, the
#: second rect when that window is drawn as a stacked split, and whether it
#: slides in from the previous window instead of stepping to it, and whether it
#: is drawn blur-filled (PLAN.md § Blur-fill).
StoredWindow = tuple[float, tuple[int, int, int, int], tuple[int, int, int, int] | None, bool, bool]

#: The one fill mode there is. A string on the record rather than `true`, so a
#: second treatment is a new value rather than a second key.
FILL_MODES = ("blur",)


def _stored_reframes(project: Project) -> dict[str, list[StoredWindow]]:
    """Every clip's requested crop rects, as asked for rather than as fitted.

    A series per clip, `(src_start seconds, rect, pane, interp)` in source
    order. A record with no `src_start` is the window from the head of the
    file onward, which is what every rect written before per-shot framing
    existed meant and still means — the key is optional and
    absent-means-what-it-always-meant, so this is deliberately not a schema
    bump (CLAUDE.md).

    `pane` is the same for the stacked split: absent means the window is one
    rect, which is what every window written before the split existed was. It
    rides on the *same record* rather than in a series of its own precisely so
    it cannot drift from the window it is the other half of — a pane with no
    window would render as half a frame over whatever framing happened to be
    in force.

    `interp` is the third such optional key, for the same reason: absent
    means the window steps rather than slides, which is what every window
    written before the keyframed move existed meant and still means. PLAN.md
    § Per-shot framing, refused section; § The keyframed move.

    `fill` is the fourth: absent means the window crops. A fill window's
    `rect` is the whole source, which is what its foreground shows; a fill
    whose rect is anything else is refused in `_clip_reframe` rather than
    read as a crop to contain, since following a crop is not built.
    """
    stored: dict[str, list[StoredWindow]] = {}
    for record in project.read_manifest().get(REFRAME_KEY, []):
        at = float(record.get("src_start") or 0.0)
        pane = record.get("pane")
        fill = record.get("fill")
        if fill is not None and fill not in FILL_MODES:
            raise ProjectError(
                f"clip {record.get('clip_id')!r} has a reframe window with fill {fill!r} — "
                f"the fills there are {', '.join(FILL_MODES)}"
            )
        stored.setdefault(str(record["clip_id"]), []).append(
            (
                at,
                _parse_rect(record["rect"]),
                _parse_rect(pane) if pane else None,
                bool(record.get("interp")),
                fill is not None,
            )
        )
    for series in stored.values():
        series.sort(key=lambda entry: entry[0])
    return stored


def _clip_source(clip: dict[str, Any]) -> tuple[int, int] | None:
    """A clip's own pixel size, or None if it is not picture with a known one."""
    if not clip.get("has_video") or not clip.get("width") or not clip.get("height"):
        return None
    return int(clip["width"]), int(clip["height"])


def _fit_pane_rect(
    rect: tuple[int, int, int, int],
    source: tuple[int, int],
    pane: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Grow a requested rect to a pane of the canvas — **full source height**.

    The pane's aspect alone is not enough, and that is the whole of this
    function. Nothing masks or crops a pane: `mlt.Reframe._dest` places the
    *entire* source frame so the rect fills the pane, and the profile does the
    clipping. So a rect of the pane's shape but only part of the source's
    height scales the frame up until it overruns the pane and draws into the
    other one — a two-hander with the other person's chin across the middle,
    at exit 0. A caught-by-a-test bug rather than a reasoned one: growing to
    9:8 alone turned a 200px-tall ask into a 4.8x zoom.

    Full height makes the scaled frame exactly one pane tall for any source
    shape, which is the only thing holding the two halves apart. The width
    follows from it, so the ask moves the window sideways and nothing else —
    which is what a framing decision is here.
    """
    src_w, src_h = source
    pane_w, pane_h = pane
    width = round(src_h * pane_w / pane_h)
    if width > src_w:
        raise ProjectError(
            f"a {src_w}x{src_h} source cannot be shown whole in a {pane_w}x{pane_h} "
            f"pane — a pane window is the full source height, so it would be "
            f"{width} wide against the source's {src_w}. This footage is too tall "
            "to stack; frame it with one window instead"
        )
    x, _y, ask_w, _ask_h = rect
    if x + ask_w > src_w:
        raise ProjectError(
            f"crop rect {rect[0]},{rect[1]},{rect[2]},{rect[3]} runs past the "
            f"source's {src_w}x{src_h}"
        )
    left = min(max(round(x + ask_w / 2 - width / 2), 0), src_w - width)
    return (left, 0, width, src_h)


def _clip_reframe(
    clip: dict[str, Any],
    asked: list[StoredWindow] | None,
    resolution: tuple[int, int],
) -> mlt.Reframe | None:
    """What survives into the frame for one clip, overrides or centre default.

    The head window is whichever override sits at 0, else the centre crop —
    so a clip whose first override starts partway in is centre-cropped up to
    that point rather than being framed by a window that has not begun.

    **A split's two rects are fitted to the pane, not to the canvas** — and to
    the full source height, which is `_fit_pane_rect`'s whole argument: the
    geometry is the only thing holding the two halves off each other, there
    being no mask and no crop filter anywhere in this (`mlt.Reframe._dest`).
    A source too tall to carry a pane is refused there, at the keyboard.

    A head window flagged `interp` is the same class of hand-edit as two
    windows at one in-point: `reframe` never writes one, since there is
    nothing before the head to slide from, so one found here can only have
    been typed into the manifest directly. Refused the same way, rather than
    silently dropped or handed to `mlt.Reframe` to raise less legibly.
    """
    source = _clip_source(clip)
    if source is None:
        return None
    series = list(asked or [])
    starts = [start for start, *_ in series]
    if len(set(starts)) != len(starts):
        # Only reachable by hand-editing the manifest — the op refuses a second
        # entry at one in-point. Refused as a `ProjectError` so the reading
        # paths report it (`reframe`'s table, `timeline_view`'s
        # `reframe_error`) rather than being taken down by it, which is how a
        # person finds the window to drop.
        raise ProjectError(
            f"clip {clip.get('clip_id')!r} has two reframe windows at one in-point "
            f"({sorted(starts)}) — a window is addressed by where it starts, so one of "
            "them is unreachable; drop it with `reframe <clip> --at <seconds> --reset`"
        )
    upper, _lower = mlt.pane_boxes(resolution)
    pane_shape = (upper[2], upper[3])

    whole = (0, 0, *source)

    def fit(
        at: float, rect: tuple[int, int, int, int], pane: object, _interp: bool = False, fill: bool = False
    ) -> tuple[int, int, int, int]:
        if fill:
            if tuple(rect) != whole:
                raise ProjectError(
                    f"clip {clip.get('clip_id')!r} has a blur-fill window at {at}s whose rect "
                    f"is {_rect_text(rect)}, not the whole source {_rect_text(whole)} — a fill "
                    "shows the whole frame; reset the window and set it again"
                )
            if pane:
                raise ProjectError(
                    f"clip {clip.get('clip_id')!r}'s window at {at}s is both a split and a fill"
                )
            return whole
        if pane:
            return _fit_pane_rect(rect, source, pane_shape)
        return _fit_rect_to_canvas(rect, source, resolution)

    # The head window is addressed as 0.0 whatever it was stored as, so that a
    # pane on it pairs with the window `Reframe` calls `crop`.
    head = (0.0, *series.pop(0)[1:]) if series and series[0][0] <= 0 else None
    if head is not None and head[3]:
        raise ProjectError(
            f"clip {clip.get('clip_id')!r} has its head window flagged to slide — "
            "there is nothing before the head of the source to slide from; only "
            "a later window can carry `interp`"
        )
    crop = mlt.centre_crop(source, resolution) if head is None else fit(*head)
    later = tuple((window[0], fit(*window)) for window in series)
    panes = tuple(
        (when, _fit_pane_rect(pane, source, pane_shape))
        for when, _rect, pane, _interp, _fill in ([head] if head else []) + series
        if pane is not None
    )
    interp = tuple(when for when, _rect, _pane, flag, _fill in series if flag)
    fills = tuple(when for when, _rect, _pane, _interp, fill in ([head] if head else []) + series if fill)
    try:
        return mlt.Reframe(
            source=source, crop=crop, later=later, panes=panes, interp=interp, fills=fills
        )
    except mlt.MLTError as error:
        # A combination only a hand edit reaches (a slide into a fill); read
        # paths report a `ProjectError`, as for two windows at one in-point.
        raise ProjectError(f"clip {clip.get('clip_id')!r}: {error}") from error


def _reframe_map(project: Project, resolution: tuple[int, int]) -> dict[str, mlt.Reframe]:
    """Clip id → the reframe to apply, for every video clip in the project.

    Keyed by clip id here and translated to a resource by the caller, because
    the manifest's unit is `(clip_id, rect)` while the MLT writer's is a node
    per resource per role.
    """
    asked = _stored_reframes(project)
    reframes = {}
    for clip in project.read_manifest().get("clips", []):
        clip_id = str(clip.get("clip_id"))
        reframe = _clip_reframe(clip, asked.get(clip_id), resolution)
        if reframe is not None:
            reframes[clip_id] = reframe
    return reframes


def _rect_text(rect: tuple[int, int, int, int]) -> str:
    return ",".join(str(value) for value in rect)


def reframe(
    path: Path | str,
    clip_id: str | None = None,
    *,
    rect: str | None = None,
    pane: str | None = None,
    src_start: float | None = None,
    interp: bool = False,
    fill: str | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or set which part of each clip survives into the frame.

    **What makes a swapped canvas fill the frame instead of pillarboxing it**
    (PLAN.md § Aspect swap, step 3). A rect is `X,Y,W,H` in the clip's own
    source pixels — geometry, never a length, so no cut can invalidate one —
    and it is stored exactly as asked and refit to whatever canvas is in force
    when the project renders. The default is a centre crop, which is *wrong
    whenever the subject is not centred*: that is the reason this reports the
    rect it used for every clip rather than quietly choosing one. Analysis
    that picks a crop lives in `reframe_detect` and **proposes** through here
    rather than framing — this op still never chooses anything by itself.

    **`src_start` frames a shot rather than a clip.** It is seconds into that
    clip's own source, and the rect it carries is in force from there onward,
    until the next window. That address is the source's clock and not the
    timeline's, so a clip used seven times gets seven correct windows without
    anything being said seven times, and no cut can invalidate one — the same
    reason a footage description indexes the source (PLAN.md § Per-shot
    framing). Omitted, it means the window from the head of the file, which is
    exactly what a per-clip reframe always meant.

    Called with no arguments it changes nothing and reports the crops in force
    per clip. `clip_id` with `rect` sets one window; `clip_id` with `reset`
    drops that clip's windows, or with `src_start` just the one at that point;
    `reset` alone drops every one. `plan` resolves — a rect that cannot fit is
    refused here either way — without writing.

    An override is a *floor*, not a frame: a rect whose shape is not the
    canvas's is grown to it, so everything asked for stays on screen, and the
    reply names both `asked` and the `crop` it became.

    **`pane` makes that window a stacked split**: two half-height panes, `rect`
    on top and `pane` below, each getting a window of the source twice the
    width a single 9:16 crop of this footage gets. It is for the shot one
    window cannot frame — a two-hander where every face is a true positive and
    only one of them is the shot (`faces.py`), so choosing between them is
    losing one. Both rects are grown to the *pane's* shape rather than the
    canvas's, and a source too tall to carry it is refused here rather than
    rendering as two halves bleeding into each other. Measured before it was
    built: PLAN.md § The stacked split.

    **`interp` makes that window slide in from the previous one** instead of
    stepping to it — the mechanism `mlt.Reframe.rect_property` always had
    (a keyframe's own operator is a per-key choice; this is the first thing
    that exercises it), authored rather than built, per PLAN.md § Per-shot
    framing's refused section and § The keyframed move. The flag names the
    window arriving, not the key that carries it — MLT interpolates the
    segment *leaving* a keyframe, so the operator this implies lands on the
    *previous* window's own key (measured, HISTORY.md § The keyframed move;
    `mlt.Reframe.rect_property` has the render that settled it). There is
    nothing before the head of a clip's source to slide from, so an `interp`
    window needs `src_start` after 0, and it cannot also carry
    `pane`: a split's lower half has no interpolation of its own, so the two
    would move out of step.

    **`fill="blur"` draws that window blur-filled** instead of cropping it:
    the whole source contained in the frame, over a blurred, darkened copy of
    the same moment covering the canvas (PLAN.md § Blur-fill). It takes no
    `rect` — the window shows the whole frame, and its record stores the whole
    source as its rect — and no `pane` or `interp`, since the background steps
    at every window boundary.
    """
    if fill is not None:
        if fill not in FILL_MODES:
            raise ProjectError(f"fill {fill!r} is not one this build draws — {', '.join(FILL_MODES)}")
        if rect is not None or pane is not None:
            raise ProjectError(
                "a blur-fill window shows the whole source, so it takes no rect or pane"
            )
        if interp:
            raise ProjectError(
                "a blur-fill window cannot slide — its background steps at the join "
                "while the picture would travel"
            )
        if reset:
            raise ProjectError("pass `fill` or `reset`, not both")
        if clip_id is None:
            raise ProjectError("a fill needs a clip_id — a window indexes one clip's source")
    if rect is not None and reset:
        raise ProjectError("pass a rect or `reset`, not both")
    if pane is not None and rect is None:
        raise ProjectError(
            "a split's lower pane needs an upper one — pass `rect` as well, since "
            "a pane is half of a window rather than a window of its own"
        )
    if rect is not None and clip_id is None:
        raise ProjectError("a rect needs a clip_id — a crop indexes one clip's source")
    if src_start is not None and clip_id is None:
        raise ProjectError(
            "an in-point needs a clip_id — a window indexes one clip's own source"
        )
    if src_start is not None and rect is None and fill is None and not reset:
        raise ProjectError("an in-point needs a rect to put there, or `reset` to drop one")
    if interp and rect is None:
        raise ProjectError(
            "interp flags a window as sliding in, so it needs a rect to store it on"
        )
    if interp and pane is not None:
        raise ProjectError(
            "a split window cannot also slide — its lower pane has no "
            "interpolation of its own, so the two halves would move out of step"
        )

    project = Project.open(path)
    resolution = _mlt_resolution(project)
    clips = {str(clip.get("clip_id")): clip for clip in project.read_manifest().get("clips", [])}
    if clip_id is not None:
        if clip_id not in clips:
            raise ProjectError(f"no clip {clip_id!r} in this project")
        if _clip_source(clips[clip_id]) is None:
            raise ProjectError(
                f"clip {clip_id!r} has no picture to crop — a reframe indexes video"
            )

    at = 0.0 if src_start is None else float(src_start)
    if clip_id is not None and src_start is not None:
        duration = clips[clip_id].get("duration")
        if at < 0:
            raise ProjectError(f"src_start {at} is before the start of clip {clip_id!r}")
        # A window past the end never applies, and would sit in the manifest
        # reading as framing that had been dealt with.
        if duration and at >= float(duration):
            raise ProjectError(
                f"src_start {at} is past clip {clip_id!r}'s {float(duration):.3f}s, so "
                "the window would never come into force"
            )
    if interp and at <= 0:
        # The head is `at == 0.0` whether that came from an explicit
        # `src_start=0` or from omitting `src_start` altogether — either way
        # there is nothing before the start of the source for it to slide
        # from, the same reasoning `mlt.Reframe.__post_init__` enforces on
        # the writer's own side.
        raise ProjectError(
            f"the window at {at}s is the head of {clip_id!r}'s source — there is "
            "nothing before it to slide from, so it cannot be flagged `interp`"
        )

    asked = _stored_reframes(project)
    if fill is not None:
        whole = (0, 0, *_clip_source(clips[clip_id]))  # type: ignore[arg-type,misc]
        series = [entry for entry in asked.get(str(clip_id), []) if entry[0] != at]
        # A slide into the window after this one would move the picture off a
        # fill while its background snaps; `mlt.Reframe` refuses it, and so
        # does this, at the keyboard.
        following = [entry for entry in series if entry[0] > at]
        if following and following[0][3]:
            raise ProjectError(
                f"the window at {following[0][0]}s slides in from this one, and a "
                "blur-fill cannot be slid out of — clear its interp first"
            )
        series.append((at, whole, None, False, True))  # type: ignore[arg-type]
        asked[str(clip_id)] = sorted(series, key=lambda entry: entry[0])
    elif rect is not None:
        # Resolved before it is stored, so an impossible rect is refused at the
        # keyboard rather than at the render an hour later.
        source = _clip_source(clips[clip_id])  # type: ignore[arg-type]
        parsed = _parse_rect(rect)
        parsed_pane = _parse_rect(pane) if pane is not None else None
        upper, _lower = mlt.pane_boxes(resolution)
        # A split's rects are fitted against the pane, an ordinary window's
        # against the canvas. Both refuse an impossible rect here rather than
        # at the render an hour later.
        if parsed_pane is not None:
            _fit_pane_rect(parsed, source, (upper[2], upper[3]))  # type: ignore[arg-type]
            _fit_pane_rect(parsed_pane, source, (upper[2], upper[3]))  # type: ignore[arg-type]
        else:
            _fit_rect_to_canvas(parsed, source, resolution)  # type: ignore[arg-type]
        series = [entry for entry in asked.get(str(clip_id), []) if entry[0] != at]
        preceding = [entry for entry in series if entry[0] < at]
        if interp and preceding and preceding[-1][4]:
            raise ProjectError(
                f"the window before {at}s is a blur-fill, and a fill cannot be slid "
                "out of — its background steps at the join while the picture would travel"
            )
        series.append((at, parsed, parsed_pane, bool(interp), False))
        asked[str(clip_id)] = sorted(series, key=lambda entry: entry[0])
    elif reset:
        if clip_id is None:
            asked = {}
        elif src_start is None:
            asked.pop(clip_id, None)
        else:
            series = [entry for entry in asked.get(clip_id, []) if entry[0] != at]
            if len(series) == len(asked.get(clip_id, [])):
                raise ProjectError(
                    f"clip {clip_id!r} has no reframe window at {at}s — `reframe {clip_id}` "
                    "lists the ones it has"
                )
            if series:
                asked[clip_id] = series
            else:
                asked.pop(clip_id, None)

    write = (rect is not None or fill is not None or reset) and not plan
    if write:
        manifest = project.read_manifest()
        records = []
        for key, series in sorted(asked.items()):
            for window_at, window_rect, window_pane, window_interp, window_fill in series:
                record: dict[str, Any] = {"clip_id": key, "rect": list(window_rect)}
                # The head window writes the record it wrote before per-shot
                # framing existed, so an unwindowed project's manifest is
                # unchanged by any of this. Same for `pane` and `interp`:
                # absent is what every window written before each existed
                # meant.
                if window_at:
                    record["src_start"] = window_at
                if window_pane is not None:
                    record["pane"] = list(window_pane)
                if window_interp:
                    record["interp"] = True
                if window_fill:
                    record["fill"] = "blur"
                records.append(record)
        if records:
            manifest[REFRAME_KEY] = records
        else:
            manifest.pop(REFRAME_KEY, None)
        project.write_manifest(manifest)

    report = []
    for key, clip in clips.items():
        source = _clip_source(clip)
        if source is None:
            continue
        try:
            entry = _clip_reframe(clip, asked.get(key), resolution)
        except ProjectError as error:
            # A stored rect the canvas has outgrown. Reported rather than
            # raised so that reading the table — and so finding out which clip
            # to reset — is possible at all; `export` is where it is refused.
            report.append(
                {
                    "clip_id": key,
                    "source": f"{source[0]}x{source[1]}",
                    "crop": None,
                    "asked": _rect_text(asked[key][0][1]),
                    "origin": "override",
                    "reframes": None,
                    "kept": None,
                    "windows": None,
                    "error": str(error),
                }
            )
            continue
        assert entry is not None
        overrides = {window[0]: window[1] for window in asked.get(key, [])}
        report.append(
            {
                "clip_id": key,
                "source": f"{source[0]}x{source[1]}",
                "crop": _rect_text(entry.crop),
                "asked": _rect_text(overrides[0.0]) if 0.0 in overrides else None,
                "origin": "override" if 0.0 in overrides else "centre",
                # False means the filter is not emitted at all: the clip already
                # carries the canvas's aspect uncropped, so MLT's own placement
                # is already the right one.
                "reframes": not entry.is_identity(resolution),
                "kept": round(
                    (entry.crop[2] * entry.crop[3]) / (source[0] * source[1]),
                    4,
                ),
                # Every window in force, head one included, so the shot-level
                # table is readable without re-deriving which override applies
                # where. One entry is the ordinary per-clip case.
                "windows": [
                    {
                        "src_start": window_at,
                        "crop": _rect_text(window_crop),
                        "asked": _rect_text(overrides[window_at])
                        if window_at in overrides
                        else None,
                        "origin": "override" if window_at in overrides else "centre",
                        # The lower half when this window is a stacked split,
                        # and the reason `kept` is the two of them together:
                        # a split keeps *more* of the source than the window it
                        # replaces, which is the whole point of drawing one.
                        "pane": _rect_text(entry.pane_at(window_at))
                        if entry.pane_at(window_at)
                        else None,
                        # Whether this window slides in from whatever governed
                        # before it rather than stepping to it. False for the
                        # head always — there is nothing before it to slide
                        # from — and for every window written before this
                        # existed.
                        "interp": entry.is_interp(window_at),
                        # `fill` appears only on a blur-filled window (below),
                        # absent-means-a-crop like the manifest record.
                        "kept": round(
                            (
                                window_crop[2] * window_crop[3]
                                + (
                                    entry.pane_at(window_at)[2] * entry.pane_at(window_at)[3]
                                    if entry.pane_at(window_at)
                                    else 0
                                )
                            )
                            / (source[0] * source[1]),
                            4,
                        ),
                    }
                    | ({"fill": "blur"} if entry.is_fill(window_at) else {})
                    for window_at, window_crop in entry.windows()
                ],
                "error": None,
            }
        )
    return {
        "project": str(project.root),
        "canvas": f"{resolution[0]}x{resolution[1]}",
        "clips": report,
        "count": len(report),
        "written": write,
        "reset": bool(reset),
        "plan": bool(plan),
    }


#: Where in each placement the sheet samples. Three, and not at the edges: an
#: edge frame is the one a seek is least likely to land on and the one a cut
#: is most likely to have made ambiguous. `~/proofcut-work/projects/final-cut/audit.py`'s own
#: numbers, which is the prototype this is a build of.
SHEET_MOMENTS = (0.15, 0.5, 0.85)
#: Tile width in the montage. The sheet is read on a phone (auto-memory:
#: review by served page), so three across at this width is a legible row.
SHEET_TILE_WIDTH = 420
#: Rows per page when this sheet is drawn for a caller that can only see
#: bytes. **Rows, not tiles**, because a row is one window and a window is the
#: unit being judged — a page that split one across its edge would be handing
#: back two half-answers about the same rect. Six rows of three moments is 18
#: tiles inside `SHEET_PAGE_WIDTH`, the same footprint as the 25-tile shot
#: sheet that reads back verbatim. Unpaged (`per_page=None`) is unchanged: the
#: whole project at `SHEET_TILE_WIDTH` as a PNG, which is what a person opens.
REFRAME_SHEET_PER_PAGE = 6
#: The window, drawn on the source frame. Red because nothing in this footage
#: is, and thick enough to read once the tile is 420px wide.
SHEET_STROKE = "#ff3b3b"
SHEET_LABEL = "#ffcc00"
#: Tiles a row draws when it is sampled for the subject's extremes: leftmost,
#: median, rightmost. Three because the montage is a fixed grid — a row with
#: fewer tiles shifts every row after it, and a sheet whose rows do not line up
#: mislabels the thing being reviewed.
SHEET_PICKS = 3
#: Probes per second of window when sampling for extremes. The detector costs
#: ~0.48s a frame on this box's CPU build, measured at 3/12/36 frames, so a
#: 245-second cut is about four minutes of probing — a price a review
#: instrument pays once, and why this is opt-in rather than the default.
SHEET_PROBE_HZ = 2.0
#: The floor is `DETECT_FRAMES`, so the shortest window is asked the same three
#: questions the detector asks of one. The ceiling keeps a long held shot from
#: costing a minute on its own; a subject's extremes are where it turns, and
#: sixteen looks find a turn that two do not.
SHEET_PROBE_MIN = 3
SHEET_PROBE_MAX = 16


def _spread(values: list[float], count: int) -> list[float]:
    """`count` items spaced evenly through `values`, in the order given.

    For padding a row out to its tile count when the subject was located in
    fewer probes than that: the leftovers still want to be spread across the
    stretch rather than bunched at its head.
    """
    if count <= 0 or not values:
        return []
    if count >= len(values):
        return list(values)
    step = (len(values) - 1) / (count - 1) if count > 1 else 0.0
    return [values[round(index * step)] for index in range(count)]


def _is_sliding(entry: mlt.Reframe | None, next_edge: float | None) -> bool:
    """Does this stretch end by sliding into the next window, not stepping?

    True exactly when the window this stretch runs up to is flagged `interp`
    — the render is already moving throughout the stretch in that case, not
    holding a single crop, so a tile drawn from `crop_at` alone would show a
    position the file holds for no more than an instant. `reframe_sheet`
    handles a row answering True here as a special case: it draws the two
    ends rather than sampling a rect that does not sit still (HISTORY.md § The
    keyframed move). `next_edge` is `None` when the stretch runs to the
    placement's own end rather than to another window, which can never be a
    slide's destination — nothing is there to slide *into*.
    """
    return entry is not None and next_edge is not None and entry.is_interp(next_edge)


def _sheet_extremes(
    stretches: list[tuple[dict[str, Any], float, float, int, float | None]],
    at: Sequence[float],
) -> dict[int, dict[str, Any]]:
    """Where the subject is extreme in each stretch, in one detector run.

    **The half of the sheet's finding that drawing every window did not
    close.** A tile is evidence about the instant it draws and a window is a
    claim about a span, so three fixed fractions have no reason to find either
    the best moment or the worst one — the teaser's opening window was 184px
    out at its median and the one tile that landed inside it was 122px out,
    which reads as tight and fine (HISTORY.md § The tile that made a wrong
    window look right).

    **The rect is static inside a stretch, so the worst moment is at one of the
    subject's own extremes** — the error is `|subject_x - crop centre|`, which
    is monotonic in `subject_x` either side of that centre. That is what makes
    leftmost/median/rightmost the right three and not merely a denser sampling:
    the worst moment is in them by construction, whatever the subject did in
    between.

    Probing does not decide anything and writes nothing. It picks *which
    frames get drawn*, and the drawn frame is still what a window is judged on
    — the same rule as `reframe_detect`, whose proposal is 114px out on a
    459px window.

    **The probe grid includes the fixed fractions, and that is a correction the
    measurement made.** Probing at a rate finds the extreme of the *probed*
    sample, not of the stretch, so a fraction landing between two probes can
    catch a worse moment than any of them — on the teaser it did on 5 rows of
    16, by up to 29px, which is a sheet that changed its sampling and got
    quietly worse. Sampling the fractions too costs at most three extra frames
    a row and makes the old sheet a subset of this one, so the drawn moment is
    never worse than the moment the default would have drawn.

    Returns the picks per row, plus the numbers behind them. A stretch where no
    probe held a face comes back **named** rather than quietly sampled the old
    way: a fraction presented as an extreme is a tile claiming evidence it
    does not have.
    """
    jobs: list[dict[str, Any]] = []
    probes: dict[int, list[float]] = {}
    for row, (placement, begin, stretch_end, _crossed, next_edge) in enumerate(stretches):
        entry = placement["reframe"]
        if entry is None or entry.crop_at(begin) is None:
            continue  # No geometry, so no rect to be extreme against.
        if _is_sliding(entry, next_edge):
            # The picks for a sliding stretch are its two ends, not wherever
            # the subject happens to be — `reframe_sheet` overrides `chosen`
            # for these rows regardless of what this function returns, so
            # spending a detector pass on them buys nothing.
            continue
        count = min(
            SHEET_PROBE_MAX, max(SHEET_PROBE_MIN, round((stretch_end - begin) * SHEET_PROBE_HZ))
        )
        times = sorted(
            {
                *dsc.frame_times(begin, stretch_end, count),
                *(begin + (stretch_end - begin) * moment for moment in at),
            }
        )
        probes[row] = times
        jobs.append({"index": row, "media": str(placement["path"]), "timestamps": times})

    detections = {result["index"]: result for result in faces.detect(jobs)}

    found: dict[int, dict[str, Any]] = {}
    for row, times in probes.items():
        placement, begin, _finish, _crossed, _next_edge = stretches[row]
        crop = placement["reframe"].crop_at(begin)
        middle = crop[0] + crop[2] / 2
        answer = detections[row]
        note: str | None = None
        located: list[tuple[float, float, int]] = []  # (subject_x, src_time, faces)
        if "error" in answer:
            # One unreadable stretch is not a reason to lose the other rows'
            # probing, so it is reported against this row and its tiles fall
            # back to the probe times, drawn with no subject number on them.
            note = f"the detector could not read this stretch: {answer['error']}"
        else:
            for frame in answer["frames"]:
                seen_faces = frame.get("faces", [])
                centre = faces.frame_centre(seen_faces)
                if centre is not None:
                    located.append((centre, float(frame["ts"]), len(seen_faces)))
        located.sort()

        picks: list[dict[str, Any]] = []
        seen: set[float] = set()
        if located:
            for name, index in (
                ("leftmost", 0),
                ("median", len(located) // 2),
                ("rightmost", len(located) - 1),
            ):
                subject, when, seen_faces = located[index]
                if when in seen:
                    continue
                seen.add(when)
                picks.append(
                    {
                        "src_time": when,
                        "subject_x": round(subject),
                        "offset": round(subject - middle),
                        # **The count is not decoration.** `frame_centre` is
                        # area-weighted across every face in the frame, so two
                        # faces put the "subject" between them, where neither
                        # is: the teaser's largest offset, 608px, is Stu at
                        # 1079 averaged with a bystander at 1775 against a crop
                        # centred on 830 — and Stu is 250px out, not 608. That
                        # is `faces.py`'s own which-face-is-the-shot finding
                        # arriving in a review number, so the number carries
                        # the count that explains it.
                        "faces": seen_faces,
                        "pick": name,
                    }
                )
        for when in _spread([ts for ts in times if ts not in seen], SHEET_PICKS - len(picks)):
            picks.append(
                {"src_time": when, "subject_x": None, "offset": None, "faces": 0, "pick": "probe"}
            )

        # Worst first. A reviewer reads a row left to right on a phone, and the
        # whole finding behind this is that the reassuring tile was the one
        # that got looked at. Unlocated tiles sort last, in time order.
        picks.sort(key=lambda p: (p["offset"] is None, -abs(p["offset"] or 0), p["src_time"]))
        offsets = [subject - middle for subject, _, _ in located]
        found[row] = {
            "probe": note or ("extremes" if located else f"no face in {len(times)} probes"),
            "probes": len(times),
            "located": len(located),
            # Whether any probe held more than one face, which is the flag on
            # every offset in the row: a two-face frame's weighted centre is a
            # question about which face is the shot, not a measurement of how
            # wrong the window is.
            "multi_face": any(count > 1 for _, _, count in located),
            "subject_min": round(located[0][0]) if located else None,
            "subject_max": round(located[-1][0]) if located else None,
            # Off every probe rather than only the drawn three — they agree by
            # construction, and saying so is what makes that claim checkable.
            "worst_offset": round(max(offsets, key=abs)) if offsets else None,
            "picks": picks[:SHEET_PICKS],
        }
    return found


def _sheet_placements(
    project: Project, resolution: tuple[int, int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every stretch of footage the render shows, and what it is framed by.

    The picture lane when there is one — `_picture_plan`'s shots, which is
    what `export` will actually produce — else the edit's own segments, which
    is the whole picture for a project with no cues. Stills come back in the
    second list rather than being dropped silently: a card is authored at the
    canvas and never cropped, so there is no window to review, and saying so
    is the difference between "nothing to check" and "not checked".
    """
    clips = _clips_by_id(project)
    reframes = _reframe_map(project, resolution)
    rate = _export_fps(clips)
    shots, _ = _picture_plan(project, rate)

    placements: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if shots:
        for index, shot in enumerate(shots):
            if shot.get("is_image"):
                skipped.append({"index": index, "asset": shot["asset"], "why": "a still is never cropped"})
                continue
            placements.append(
                {
                    "index": index,
                    "asset": str(shot["asset"]),
                    "path": Path(str(shot["asset_path"])),
                    "src_start": float(shot["src_start"]),
                    "duration": float(shot["duration"]),
                    "timeline_start": float(shot["start"]),
                    "reframe": reframes.get(str(shot["asset"])),
                }
            )
        return placements, skipped

    for index, seg in enumerate(_placed_segments(_load_edit(project))):
        clip = clips.get(seg["clip_id"])
        if clip is None or _clip_source(clip) is None:
            continue
        placements.append(
            {
                "index": index,
                "asset": seg["clip_id"],
                "path": media.media_path(project, clip),
                "src_start": float(seg["start"]),
                "duration": float(seg["duration"]),
                "timeline_start": float(seg["timeline_start"]),
                "reframe": reframes.get(seg["clip_id"]),
            }
        )
    return placements, skipped


def _lerp_rect(
    start: tuple[int, int, int, int], end: tuple[int, int, int, int], fraction: float
) -> tuple[int, int, int, int]:
    """A straight-line stand-in for MLT's own keyframe interpolation.

    Not a claim of bit-exactness — melt's curve is its own to draw, and this
    is a review tile, not the render. It is exact at `fraction` 0 and 1 (the
    two rects MLT actually holds as keyframes) and a reasonable approximation
    between them, which is what `reframe_sheet` needs to show a slide is
    moving without pretending to know precisely where it is at every instant.
    """
    x0, y0, w0, h0 = start
    x1, y1, w1, h1 = end
    return (
        round(x0 + (x1 - x0) * fraction),
        round(y0 + (y1 - y0) * fraction),
        round(w0 + (w1 - w0) * fraction),
        round(h0 + (h1 - h0) * fraction),
    )


def _draw_window(
    tile: Path,
    crop: tuple[int, int, int, int],
    source: tuple[int, int],
    label: str,
    pane: tuple[int, int, int, int] | None = None,
) -> None:
    """Draw one window on one extracted frame, in place.

    A stacked split draws **both** of its rects, because half a split judged
    on its own is the same failure the whole sheet exists to catch: the upper
    pane alone reads as a badly-centred single window, and whether the pair is
    right is a question about the pair. The lower one is dashed, so which half
    is which is legible in a montage rather than only in the label.

    `pane` is reused for a sliding window's other end (`reframe_sheet`'s
    "slide-from"/"slide-to" tiles): the same dashed-rectangle drawing, this
    time showing where the move starts or finishes rather than the other
    half of a split. The two never coincide — a window cannot be both
    (`mlt.Reframe.__post_init__`) — so nothing here needs to tell them apart.
    """
    x, y, width, height = crop
    stroke = max(2, round(source[0] / 240))
    panes = [
        "-draw", f"rectangle {x},{y} {x + width - 1},{y + height - 1}",
    ]  # fmt: skip
    if pane is not None:
        px, py, pw, ph = pane
        # The dash array is an MVG primitive inside `-draw`, not a command-line
        # option: `-strokedasharray` is ImageMagick 6's spelling and `magick`
        # rejects it outright — which at least fails loudly, unlike most of
        # what this file guards against.
        dashed = (
            f"stroke-dasharray {stroke * 4} {stroke * 3} "
            f"rectangle {px},{py} {px + pw - 1},{py + ph - 1}"
        )
        panes += ["-draw", dashed]
    command = [
        *graphics.magick_command(), str(tile),
        "-fill", "none", "-stroke", SHEET_STROKE, "-strokewidth", str(stroke),
        *panes,
        "-stroke", "none", "-fill", SHEET_LABEL,
        "-pointsize", str(max(18, round(source[0] / 36))),
        "-annotate", f"+{stroke * 3}+{round(source[1] / 12)}", label,
        str(tile),
    ]  # fmt: skip
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if done.returncode != 0:
        raise graphics.GraphicsError(f"magick could not draw the window on {tile}: {done.stderr[-800:]}")


def reframe_sheet(
    path: Path | str,
    *,
    out: str | None = None,
    moments: Sequence[float] | None = None,
    extremes: bool = False,
    page: int = 0,
    per_page: int | None = None,
) -> dict[str, Any]:
    """Draw every placement's framing window on its own source frames.

    **The output of any framing decision is unreviewable without this**, and
    that is why it is built beside the framing rather than after it. The hand-
    framed teaser had 2 of its 15 windows wrong and *neither was visible in
    motion*: a badly-placed window reads as framing, because nothing in the
    frame says otherwise. What catches one is the window drawn on the whole
    source frame, where the part it is leaving out is right there beside it
    (PLAN.md § Per-shot framing, step 3; `~/proofcut-work/projects/final-cut/audit.py` is the
    prototype).

    Every placement the render shows — the picture lane's shots, or the edit's
    own segments where there is no lane — walked window by window, with the
    window in force drawn on the frame in red and labelled with the rect.
    Placements rather than clips, because framing is per shot: one clip used
    seven times gets seven placements, each showing the windows its own stretch
    of source reads.

    **The row is a window, not a placement, and that is a correction.** Three
    fixed fractions of each placement missed 14 of the vertical cut's 55
    windows, eight of them hand-approved — a window covering a small slice of a
    long placement is one no round fraction lands in, and it was reported as
    reviewed. So each placement is split at the boundaries it crosses and each
    stretch is sampled inside itself: every window that reaches the screen gets
    drawn, and `moments` are fractions of the stretch that shows it rather than
    of the whole placement. `windows` on a row still says how many the
    *placement* crosses, which is the preview/render asymmetry's own tell
    (CLAUDE.md). HISTORY.md § The thirty-nine windows, reviewed.

    **A tile is evidence about an instant, not an approval of the span**, and
    `extremes` is the answer to that. A static rect over a moving subject has a
    best moment and a sample can land on it: the teaser's opening window was
    184px out at its median and the one tile inside it landed 122px out, which
    reads as tight and fine. Drawing every window closed the coverage half of
    that finding; this closes the other half. With `extremes` each stretch is
    probed with the face detector and drawn where the subject is **leftmost,
    median and rightmost** rather than where the clock is round — and since the
    rect does not move inside a stretch, the worst moment is one of those two
    ends by construction. Worst tile first, labelled with how far the subject
    sits from the middle of the crop. It costs a detector and minutes of
    decoding, which is why it is opt-in; `_sheet_extremes` has the rule and the
    measurement. HISTORY.md § The tile that made a wrong window look right.

    Writes a tile per sample and one montage under `cache/sheets/`, and
    returns both paths and the table. `out` names the sheet somewhere else;
    `moments` overrides where inside each window's stretch it samples, as
    fractions — and is refused alongside `extremes`, which is what replaces
    them rather than something they tune.

    **`per_page` is what makes this sheet reachable by an agent, and it
    changes two things at once on purpose.** Unpaged — the default, and every
    call written before this existed — it is the whole project montaged at
    `SHEET_TILE_WIDTH` as a PNG, which is what a person opens on a phone. Ask
    for a page and it becomes a JPEG inside `SHEET_PAGE_WIDTH`, because the
    caller is `server.reframe_sheet` handing the bytes back in a tool result:
    a montage of this film's 79 windows is ~4700px tall, and vision downscales
    anything past ~1568 on its long edge — so the unpaged sheet does not
    merely arrive large, it arrives with its rects and labels resampled away.
    A page is also **cheaper rather than merely smaller**: this sheet extracts
    a frame per tile with no shared cache behind it, and under `extremes` it
    probes with the face detector, so both are now bounded by the page rather
    than by the project.

    `page` is counted from 0 and rows keep their project-wide numbers, so
    `row` on page 2 still names the same window `reframe --src-start` would.
    """
    project = Project.open(path)
    resolution = _mlt_resolution(project)
    if per_page is not None and per_page < 1:
        raise ProjectError(f"a page holds at least one row, not {per_page}")
    if page < 0:
        raise ProjectError(f"page is counted from 0, not {page}")
    if extremes and moments is not None:
        raise ProjectError(
            "moments are fractions of the clock and extremes are where the "
            "subject is — ask for one or the other, not both"
        )
    at = tuple(float(m) for m in (moments or SHEET_MOMENTS))
    if not at or any(m < 0 or m > 1 for m in at):
        raise ProjectError(f"sample moments are fractions of a placement, not {list(at)}")
    if extremes:
        # Asked before any decoding, for `reframe_detect`'s reason: a missing
        # interpreter is a refusal that should arrive now rather than after
        # ffmpeg has walked the film.
        detector = faces.available()
        if not detector["available"]:
            raise faces.FaceError(str(detector["why"]))

    placements, skipped = _sheet_placements(project, resolution)
    if not placements:
        raise ProjectError(
            "this project has no footage placements to sheet — there is nothing "
            "framed here to look at"
        )

    tiles: list[Path] = []
    rows: list[dict[str, Any]] = []
    dest_dir = project.sheet_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    # **Files only, never the tree.** This op writes flat into `cache/sheets/`
    # (`webui._send_reframe_tile` serves from exactly that level), but every
    # other sheet keeps a *subdirectory* of it — `SHEET_FRAMES_DIR` most of
    # all, which is the frame cache the docstring calls shared by every sheet.
    # An `rmtree` here threw all of that away on each framing review: the next
    # `shot_sheet` re-extracted every frame it already had, silently and
    # correctly. The wipe itself stays — 39 rows of this film is ~120 MB of
    # tiles, so leaving them to accumulate is the other way to be wrong.
    for stale in dest_dir.iterdir():
        if stale.is_file():
            stale.unlink()

    # One entry per window a placement shows, in the order it shows them. The
    # split is the whole coverage fix: sampling the placement asks about the
    # window at whichever fractions happen to land inside it, and the windows
    # that get missed that way are precisely the short ones.
    #
    # **A frame of tolerance, never an epsilon** — `reframe_coverage`'s rule,
    # and it is not optional here either. A window boundary and the placement
    # that starts on it are the same instant a frame apart (20.39538 against
    # 20.39541 on the real cut), so an exact comparison splits off a stretch
    # 30µs long, draws three tiles of it, and labels the placement with the
    # window it is about to leave. Fifteen of the vertical's rows were that.
    frame = 1.0 / _export_fps(_clips_by_id(project))
    stretches: list[tuple[dict[str, Any], float, float, int, float | None]] = []
    for placement in placements:
        entry = placement["reframe"]
        begin = placement["src_start"]
        stretch_end = begin + placement["duration"]
        windows = entry.windows() if entry is not None else ()
        # Dropped at the tail for the same reason: a window with under a frame
        # of a placement left is one that placement does not show. Whichever
        # placement starts there draws it as its own head.
        edges: list[float] = []
        for edge in sorted(
            {begin, *(b for b, _ in windows if begin - frame <= b < stretch_end - frame)}
        ):
            if edges and edge - edges[-1] <= frame:
                # One instant. The later address wins, because that is the one
                # the render steps to and the one a rect is stored at.
                edges[-1] = edge
            else:
                edges.append(edge)
        for index, edge in enumerate(edges):
            # `None` when this stretch runs to the placement's own end rather
            # than to another window — never a slide's destination, since
            # nothing is there to slide into.
            next_edge = edges[index + 1] if index + 1 < len(edges) else None
            stop = next_edge if next_edge is not None else stretch_end
            stretches.append((placement, edge, stop, len(edges), next_edge))

    # Sliced **before** the detector and before a single frame is extracted:
    # paging that only cropped the montage would still pay for the whole
    # project, which on this film is minutes of decoding under `extremes`.
    count = len(stretches)
    if per_page is None:
        pages = 1 if count else 0
        start = 0
        drawn_rows = stretches
    else:
        pages = max(1, ceil(count / per_page)) if count else 0
        start = page * per_page
        drawn_rows = stretches[start : start + per_page]
    # Keyed by position within the page, while `row` below stays the window's
    # project-wide number — the number a reader takes back to `reframe`.
    probed = _sheet_extremes(drawn_rows, at) if extremes else {}
    # The montage is a fixed grid (`-tile {columns}x`), so every row has to
    # emit the same tile count or the rows after a short one shift into its
    # gap (SHEET_PICKS's own reasoning). A sliding row needs at least its two
    # ends to be honest about the move, so this many columns is the floor for
    # any project that has one.
    columns = SHEET_PICKS if extremes else len(at)

    for offset, (placement, begin, stretch_end, crossed, next_edge) in enumerate(drawn_rows):
        row = start + offset
        entry = placement["reframe"]
        source = entry.source if entry is not None else None
        sliding = _is_sliding(entry, next_edge)
        if sliding and columns < 2:
            raise ProjectError(
                f"clip {placement['asset']!r} slides into a window at {next_edge}s, "
                f"but this sheet draws {columns} tile per row — showing both ends "
                "of a slide needs at least two; pass more `moments`"
            )
        if sliding:
            # **A sheet row is a window shown, not a placement** — and a
            # sliding window is never shown as one static rect, because it
            # is not one. `crop_at` only knows the discrete window that
            # governs a source instant; it cannot say where the frame
            # actually sits mid-slide, so drawing it at three arbitrary
            # fractions would draw the *departure* rect three times and call
            # that a review of a move (CLAUDE.md: a wrong window reads as
            # framing in motion — this is that trap's mirror image, motion
            # read as no window at all). Evenly spaced fractions **including
            # both ends** stand in for the interpolation instead: exact at
            # the two keyframes MLT actually holds, and a straight-line
            # approximation of its curve in between, which is honest about
            # being an approximation because the label says so.
            assert next_edge is not None
            from_rect = entry.crop_at(begin)
            to_rect = entry.crop_at(next_edge)
            # `columns >= 2` here — the refusal above is what guarantees it.
            fractions = [index / (columns - 1) for index in range(columns)]
            chosen = []
            for fraction in fractions:
                if fraction <= 0.0:
                    pick_name = "slide-from"
                elif fraction >= 1.0:
                    pick_name = "slide-to"
                else:
                    pick_name = f"slide-{fraction:.2f}"
                chosen.append(
                    {
                        "src_time": begin + (next_edge - begin) * fraction,
                        "subject_x": None,
                        "offset": None,
                        "faces": None,
                        "pick": pick_name,
                    }
                )
        else:
            # In extremes mode the picks *are* the samples; a stretch with no
            # geometry to be extreme against still gets its fractions, so
            # every window is drawn either way.
            chosen = probed.get(offset, {}).get("picks") or [
                {
                    "src_time": begin + (stretch_end - begin) * moment,
                    "subject_x": None,
                    "offset": None,
                    "faces": None,
                    "pick": f"{moment:.2f}",
                }
                for moment in at
            ]
        samples = []
        for index, pick in enumerate(chosen):
            when = float(pick["src_time"])
            tile = dest_dir / f"{row:03d}-{index}-{pick['pick']}.png"
            picture.extract_frame(placement["path"], when, tile)
            if sliding:
                fraction = (when - begin) / (next_edge - begin) if next_edge > begin else 0.0
                crop = _lerp_rect(from_rect, to_rect, fraction)
                # The dashed rect is the *other* end — the target while it is
                # still travelling, the origin once it has arrived, so the
                # last tile does not dash an identical rect over its own
                # solid one.
                ghost = from_rect if fraction >= 1.0 else to_rect
                pane = None  # a sliding window is never also a split — mlt.Reframe refuses it.
                if source is not None:
                    label = (
                        f"{row} {placement['asset']} @{when:.2f}s SLIDE "
                        f"{_rect_text(from_rect)} -> {_rect_text(to_rect)}  {pick['pick']}"
                    )
                    _draw_window(tile, crop, source, label, ghost)
            else:
                crop = entry.crop_at(when) if entry is not None else None
                pane = entry.pane_at(entry.window_start(when)) if entry is not None else None
                if crop is not None and source is not None:
                    label = f"{row} {placement['asset']} @{when:.2f}s  {_rect_text(crop)}"
                    if pane is not None:
                        label += f" + {_rect_text(pane)} (split)"
                    if pick["subject_x"] is not None:
                        # The number the tile is being read for: where the subject
                        # is against the middle of the crop, signed, so which way
                        # the window is wrong is on the tile rather than inferred.
                        label += f"  subj {pick['subject_x']} {pick['offset']:+} {pick['pick']}"
                        if pick["faces"] > 1:
                            # On the tile, not only in the table: this is the one
                            # number on a sheet that can be large and mean nothing,
                            # and a sheet is read as pictures.
                            label += f" ({pick['faces']} faces)"
                    _draw_window(tile, crop, source, label, pane)
            tiles.append(tile)
            samples.append(
                {
                    "src_time": round(when, 3),
                    "crop": _rect_text(crop) if crop else None,
                    # Named rather than folded into `crop`, so a page built on
                    # this table can say which tiles are splits without parsing
                    # a label back apart. Always None on a sliding row — the
                    # dashed rect there is the slide's other end, not a pane,
                    # and reusing this field would misreport `split` below.
                    "pane": _rect_text(pane) if pane else None,
                    "subject_x": pick["subject_x"],
                    "offset": pick["offset"],
                    "faces": pick["faces"],
                    "pick": pick["pick"],
                    "png": str(tile),
                }
            )
        rows.append(
            {
                "row": row,
                "shot": placement["index"],
                "asset": placement["asset"],
                # The stretch this row is about — one window's worth of one
                # placement, which is what the tiles are frames of.
                "src_start": round(begin, 3),
                "duration": round(stretch_end - begin, 3),
                # And the placement it came out of, so a row can still be
                # traced back to a shot on the timeline.
                "placement_src_start": round(placement["src_start"], 3),
                "placement_duration": round(placement["duration"], 3),
                # The address the rect is stored at, which is what `reframe
                # --src-start` takes to change it.
                "window": round(entry.window_start(begin), 3) if entry is not None else None,
                # Whether this window slides into the next one rather than
                # stepping — the flag `reframe --interp` sets on the
                # *destination* window, so `slides_to` is that window's own
                # address (`reframe --src-start` again, to change or drop it).
                # A sliding row's tiles are its two ends, never `crop_at`'s
                # single answer, and `crop`/`pane` on its `samples` follow —
                # `pane` is always null there (§ The keyframed move).
                "sliding": sliding,
                "slides_to": round(next_edge, 3) if sliding else None,
                # How many windows this *placement* crosses — counted off the
                # geometry rather than off where the samples landed, which is
                # the number that was wrong before. More than one is also the
                # preview/render asymmetry: the preview places the whole shot
                # by the window at its `src_start` (CLAUDE.md).
                "windows": crossed,
                # Whether any sampled frame of this placement is drawn as a
                # stacked split, which is what a review page filters on.
                "split": any(sample["pane"] for sample in samples),
                # And how much of the two panes is the same strip of source,
                # which is what the split is *judged* on — the sheet draws the
                # lower pane dashed so a reviewer can see the duplication, and
                # this is the number under it. None where the row is not a
                # split. `mlt.pane_overlap`.
                "pane_overlap": next(
                    (
                        mlt.pane_overlap(_parse_rect(sample["crop"]), _parse_rect(sample["pane"]))
                        for sample in samples
                        if sample["pane"] and sample["crop"]
                    ),
                    None,
                ),
                # How the tiles were chosen, and what the probing found. A row
                # says "no face in N probes" rather than reporting extremes it
                # does not have — an unsupported claim of evidence is the same
                # failure as a refused window read as a centre crop.
                "probe": probed.get(offset, {}).get("probe"),
                "probes": probed.get(offset, {}).get("probes"),
                "located": probed.get(offset, {}).get("located"),
                # Read beside `worst_offset`, never after it: an offset off a
                # multi-face frame is the weighted centre of two subjects and
                # can be large with the shot's own face well inside the crop.
                "multi_face": probed.get(offset, {}).get("multi_face"),
                "subject_min": probed.get(offset, {}).get("subject_min"),
                "subject_max": probed.get(offset, {}).get("subject_max"),
                # The worst the window is off across every probe, not only the
                # drawn ones. This is the number a sheet gets sorted by.
                "worst_offset": probed.get(offset, {}).get("worst_offset"),
                "samples": samples,
            }
        )

    sheet: Path | None = None
    if tiles:
        if per_page is None:
            sheet = graphics.montage(
                tiles,
                Path(out).expanduser() if out else dest_dir / "sheet.png",
                columns=columns,
                tile_width=SHEET_TILE_WIDTH,
            )
        else:
            # A JPEG inside the page budget, `shot_sheet`'s reasoning: these
            # bytes travel base64 in a tool result, and a tile wider than the
            # budget divided by the grid buys nothing but a downscale later.
            sheet = graphics.montage(
                tiles,
                Path(out).expanduser() if out else dest_dir / f"page{page}.jpg",
                columns=columns,
                tile_width=max(1, SHEET_PAGE_WIDTH // columns),
                quality=SHOT_SHEET_QUALITY,
            )

    return {
        "project": str(project.root),
        "canvas": f"{resolution[0]}x{resolution[1]}",
        "sheet": None if sheet is None else str(sheet),
        "rows": rows,
        # A page past the end draws nothing and says so, rather than raising
        # out of `montage` about tiles nobody asked it to draw.
        "page": page,
        "pages": pages,
        "per_page": per_page,
        # A row is a window shown, so this is no longer the placement count —
        # the two differ by exactly the windows the old sampling could miss.
        # Every window in the project, not the page's — a paged reader that
        # took `count` for "what I am looking at" would report a film's
        # framing reviewed off six rows of it. `drawn` is the page.
        "count": count,
        "drawn": len(rows),
        "placements": len(placements),
        # Where the tiles came from. `moments` is null under `extremes`, so
        # nothing reading this table can report fractions a run never used.
        "extremes": extremes,
        "moments": None if extremes else list(at),
        "probed": sum(1 for row in rows if row["located"]) if extremes else 0,
        "skipped": skipped,
    }


#: The scene score above which a change of picture is a camera cut. **Pinned by
#: judging the detections, not by agreeing with the hand table** — which is the
#: correction that moved it from 0.20. The first pin scored candidates against
#: the sixteen approved framing boundaries and called precision the share that
#: matched one, so a real camera cut in a shot nobody had framed counted
#: against the floor; precision "climbing" to 0.20 was the hand table's own
#: coverage, over three clips of nine. Every candidate inside the film's
#: placements from 0.05 up was then looked at, on the frames either side, over
#: all nine: **21 real cuts sat between 0.15 and 0.20, and not one false
#: positive**. The first non-cut is at 0.137, so 0.15 is the lowest round value
#: that is still all-cut with a margin (0.14 clears too, at 0.003 from the first
#: mistake — which is not a margin). `tests/test_scene_threshold.py` holds the
#: judgements and pins this from both sides. HISTORY.md § The scene threshold,
#: re-pinned; PLAN.md § The auto-framing detector, finding 1.
SCENE_THRESHOLD = 0.15
#: Frames sampled per window, and `describe.FRAMES_PER_WINDOW`'s number for
#: `describe.frame_times`' reason: a window boundary is where a cut is most
#: likely to be, so samples sit off both edges. Three is what finding 5 was
#: measured with.
DETECT_FRAMES = 3


def _detect_windows(
    placements: list[dict[str, Any]], threshold: float
) -> list[dict[str, Any]]:
    """Split every placement at its own camera cuts, and merge the duplicates.

    A window is `(clip_id, src_start)` — the same address a stored rect uses —
    running to the next cut inside the same placement, or to the placement's
    end. Cuts come from the *source*, so the boundaries are the footage's own
    and no edit can move one.

    Two placements reading the same stretch of a clip produce the same window
    twice, and it is one window: they would write to one address, and framing
    it twice from two samplings is how the second silently wins. Merged, it
    keeps every shot it serves and the widest span either placement showed, so
    the sampling covers what both of them put on screen.
    """
    windows: dict[tuple[str, int], dict[str, Any]] = {}
    for placement in placements:
        start = placement["src_start"]
        end = start + placement["duration"]
        inside = [
            cut
            for cut in placement["cuts"]
            if cut["score"] >= threshold and start < cut["src_time"] < end
        ]
        edges = [{"src_time": start, "score": None}, *inside]
        for index, edge in enumerate(edges):
            stop = edges[index + 1]["src_time"] if index + 1 < len(edges) else end
            # Millisecond keys, because two placements of one clip agree on a
            # cut to ffmpeg's own precision and not to a float's.
            key = (placement["asset"], round(edge["src_time"] * 1000))
            found = windows.get(key)
            if found is None:
                windows[key] = {
                    "clip_id": placement["asset"],
                    "path": placement["path"],
                    "source": placement["source"],
                    "src_start": edge["src_time"],
                    "src_end": stop,
                    "boundary": "placement" if edge["score"] is None else "cut",
                    "scene_score": edge["score"],
                    "shots": [placement["index"]],
                }
                continue
            found["src_end"] = max(found["src_end"], stop)
            found["shots"].append(placement["index"])
            # A window that is one placement's head and another's cut is both;
            # "placement" is the truthful label because the edit supplies it
            # for free and no detector had to find it.
            if edge["score"] is None:
                found["boundary"] = "placement"
                found["scene_score"] = None
    return sorted(windows.values(), key=lambda w: (w["clip_id"], w["src_start"]))


def reframe_detect(
    path: Path | str,
    *,
    clip_id: str | None = None,
    threshold: float = SCENE_THRESHOLD,
    frames: int = DETECT_FRAMES,
    apply: bool = False,
    split: bool = True,
) -> dict[str, Any]:
    """Propose a framing window per camera shot, from where the faces are.

    **The first pass at the framing `reframe` deliberately refuses to guess**
    (PLAN.md § The auto-framing detector). Every placement is split at its own
    camera cuts, each window is sampled at three moments, and the window is
    centred on the faces found there — which beats the centre crop it replaces
    on every column of the control: 0.755 mean overlap against 0.568, 111.6px
    displacement against 199.4, and **never the approved subject left entirely
    outside the frame** that the centre crop has on one shot of fifteen.

    **It proposes; it does not frame.** `apply` is off by default, which is the
    opposite of `cut --plan` and deliberately so: the pass is still 111px out on
    a 459px window — 24% of its width — and 2 of the 15 hand-framed windows were
    wrong in a way *no watch showed*. `reframe_sheet` is how either gets caught,
    so the flow is detect, sheet, apply. Applying writes through `ops.reframe`
    one window at a time, the same function the CLI, MCP and web UI call — this
    is a fourth client, never a fourth implementation — and it **never writes
    over a window that is already an override**, because that window is
    someone's decision and this has no way to know it is the worse one.

    **A window with no face is named, never guessed at.** Eight of the film's
    fifty-nine windows have no signal at all, and a silent fallback is
    indistinguishable in the output from a framing decision. They come back with
    `refused` saying so, the way a card with no record is reported rather than
    reconstructed — and with `falls_back_to`, which is the half that is easy to
    get wrong. Nothing is written for a refused window, so **whatever window is
    already in force carries over**: at the head of a clip that is the centre
    crop, and anywhere else it is the *previous shot's* framing. That is worse
    than the default rather than equal to it, because a stale window looks
    deliberate. On the film 4 of the 8 refusals inherit one that way.

    **A window one crop cannot hold is proposed as a stacked split**, which is
    the answer to the case the paragraph below names: two faces, both true
    positives, only one of them the shot. Two half-height panes hold both, at
    twice the width. `split` turns the offer off; the rule behind it is
    `faces.split_centres`, and it is deliberately strict — every sampled frame
    must hold two or three faces that one window cannot, which on the film is
    3 windows of 59 and 5.7% of the picture-seconds against the 14.8% a
    median-frame rule would have claimed. The count that would have been
    inherited from the spike, 24.8%, is neither.

    Nothing here chooses the *subject*: an oracle picking which detected face to
    frame on scores 0.863 to this rule's 0.755, and no property of the boxes says
    which face is the shot. `faces.py` has that finding and the reason it is not
    a fixable one.
    """
    if not 0 < threshold <= 1:
        raise ProjectError(f"a scene threshold is a score between 0 and 1, not {threshold}")
    if frames < 1:
        raise ProjectError(f"a window needs at least one frame sampled, not {frames}")

    project = Project.open(path)
    resolution = _mlt_resolution(project)

    # Asked before the scene scan, because the scan is minutes of decoding and
    # a missing interpreter is a refusal that should arrive now.
    detector = faces.available()
    if not detector["available"]:
        raise faces.FaceError(str(detector["why"]))

    placements, skipped = _sheet_placements(project, resolution)
    if clip_id is not None:
        clips = {str(clip.get("clip_id")) for clip in project.read_manifest().get("clips", [])}
        if clip_id not in clips:
            raise ProjectError(f"no clip {clip_id!r} in this project")
        placements = [p for p in placements if p["asset"] == clip_id]
    for placement in list(placements):
        entry = placement["reframe"]
        if entry is None:
            # No registered geometry, so there is no rect to express and no
            # centre crop being replaced. Named rather than dropped.
            skipped.append(
                {
                    "index": placement["index"],
                    "asset": placement["asset"],
                    "why": "this clip has no registered picture size to crop against",
                }
            )
            placements.remove(placement)
            continue
        placement["source"] = entry.source
    if not placements:
        raise ProjectError(
            "this project has no footage placements to frame — there is nothing "
            "here a window would apply to"
        )

    # One scan per clip, stopped at the last frame any placement of it reads:
    # a 730s cold open the film uses 71.8s of has no reason to be walked to the
    # end, and the decode is the whole cost of this half.
    scans: dict[str, list[dict[str, float]]] = {}
    assets = sorted({p["asset"] for p in placements})
    for done, asset in enumerate(assets):
        progress.report(done, len(assets), f"scanning {asset} for cuts")
        used = [p for p in placements if p["asset"] == asset]
        scans[asset] = media.scene_cuts(
            used[0]["path"], until=max(p["src_start"] + p["duration"] for p in used)
        )
    for placement in placements:
        placement["cuts"] = scans[placement["asset"]]

    windows = _detect_windows(placements, threshold)
    jobs = [
        {
            "index": index,
            "media": str(window["path"]),
            "timestamps": dsc.frame_times(window["src_start"], window["src_end"], frames),
        }
        for index, window in enumerate(windows)
    ]
    detections = {result["index"]: result for result in faces.detect(jobs)}

    stored = _stored_reframes(project)
    # **A frame, not an epsilon.** ffmpeg reports this cut at 0.834167 and the
    # manifest holds 0.8342, because a stored window was addressed by hand
    # through a timeline offset while the scan reads raw presentation times —
    # 33µs apart, the same cut, and an exact-match test called fifteen of the
    # sixteen hand windows unframed and would have written a duplicate beside
    # each one. Two boundaries inside one source frame are one window: that is
    # not a tolerance for slop, it is the resolution the render has, since a
    # reframe is emitted as keyframes numbered in the producer's own source
    # frames (CLAUDE.md § The MLT reframe).
    same_window = 1.0 / _export_fps(_clips_by_id(project))
    _upper, _lower = mlt.pane_boxes(resolution)
    pane_shape = (_upper[2], _upper[3])
    report: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        source = window["source"]
        held = [at for at, *_ in stored.get(window["clip_id"], [])]
        current = (
            "override"
            if any(abs(at - window["src_start"]) <= same_window for at in held)
            else "centre"
        )
        entry: dict[str, Any] = {
            "clip_id": window["clip_id"],
            "src_start": round(window["src_start"], 4),
            "src_end": round(window["src_end"], 4),
            "boundary": window["boundary"],
            "scene_score": round(window["scene_score"], 3) if window["scene_score"] else None,
            "shots": sorted(set(window["shots"])),
            "sampled": [round(ts, 3) for ts in jobs[index]["timestamps"]],
            "faces": 0,
            "frames_with_faces": 0,
            # **Subjects, not detections.** `faces` above sums the boxes over
            # every sampled frame, so one face reads as 3 and a room watching a
            # television reads as 33. That number is the wrong one to set a
            # split threshold from and was nearly used as it: this is the
            # per-frame count, medianed, which makes the same window 11.
            "subjects": 0,
            "current": current,
            "rect": None,
            "pane": None,
            # How much of the two panes is the same strip of source, when this
            # window is offered as a split. **The number that decides whether a
            # split is worth having**, and it used to be worked out by hand off
            # the two rects every single time: the film's own separate at
            # 23–24% and its duplicating ones at 52–63%. Reported, never
            # enforced — `mlt.pane_overlap`.
            "pane_overlap": None,
            "applied": False,
            "refused": None,
            "falls_back_to": None,
        }
        found = detections[index]
        if "error" in found:
            entry["refused"] = f"the detector could not read this window: {found['error']}"
            report.append(entry)
            continue
        sampled = found["frames"]
        entry["faces"] = sum(len(frame["faces"]) for frame in sampled)
        entry["frames_with_faces"] = sum(1 for frame in sampled if frame["faces"])
        entry["subjects"] = int(
            statistics.median([len(frame["faces"]) for frame in sampled] or [0])
        )
        centre = faces.window_centre(sampled)
        if centre is None:
            entry["refused"] = f"no face in any of the {len(sampled)} frames sampled"
            report.append(entry)
            continue
        # The centre crop's own shape, moved. Taking the rect from
        # `mlt.centre_crop` rather than deriving one means the proposal is
        # already the canvas's aspect, so `reframe`'s grow-to-fit is a no-op on
        # it and the rect stored is the rect proposed.
        _x, y, width, height = mlt.centre_crop(source, resolution)
        entry["rect"] = _rect_text((faces.window_x(centre, source[0], width), y, width, height))

        # A window one crop cannot hold, offered as a stacked split. The pane
        # rects are the *pane's* geometry — full source height, so the scaled
        # frame is exactly one pane tall — which is the same rect
        # `_fit_pane_rect` would produce, so what is proposed is what gets
        # stored.
        seconds = window["src_end"] - window["src_start"]
        centres = (
            faces.split_centres(sampled, width)
            if split and seconds >= faces.MIN_SPLIT_SECONDS
            else None
        )
        if centres is not None:
            pane_width = round(source[1] * pane_shape[0] / pane_shape[1])
            if pane_width <= source[0]:
                near, far = (
                    faces.window_x(value, source[0], pane_width) for value in centres
                )
                # Two panes clamped to the same column are one window drawn
                # twice — the split gains nothing and costs half the height.
                if near != far:
                    upper = (near, 0, pane_width, source[1])
                    lower = (far, 0, pane_width, source[1])
                    entry["rect"] = _rect_text(upper)
                    entry["pane"] = _rect_text(lower)
                    entry["pane_overlap"] = mlt.pane_overlap(upper, lower)
        report.append(entry)

    # **A refused window is not a centre-cropped one, and saying so was wrong.**
    # Nothing is written for it, so whatever window is already in force simply
    # carries over — which at the head of a clip is the centre crop and
    # everywhere else is *the previous shot's framing*. On the film 4 of the 8
    # refusals inherit a different shot's window that way, and that is worse
    # than the default rather than equal to it: a stale window looks deliberate.
    # So each refusal names what will actually cover it — resolved as if these
    # proposals were applied, which is the question being asked even in a plan,
    # since a plan is read to decide whether to apply it.
    for entry in report:
        if entry["rect"] is not None:
            continue
        if entry["current"] == "override":
            entry["falls_back_to"] = "the override already at this in-point"
            continue
        covering = [at for at, *_ in stored.get(entry["clip_id"], [])] + [
            other["src_start"]
            for other in report
            if other["clip_id"] == entry["clip_id"]
            and other["rect"] is not None
            and other["current"] == "centre"
        ]
        earlier = [at for at in covering if at <= entry["src_start"] - same_window]
        entry["falls_back_to"] = (
            f"the window from {max(earlier):.3f}s — a different shot's framing"
            if earlier
            else "the centre crop"
        )

    written = 0
    if apply:
        for entry in report:
            if entry["rect"] is None:
                continue
            if entry["current"] == "override":
                entry["refused"] = (
                    "left alone — this window is already framed by hand, and a proposal "
                    "has no way to know it is the better one"
                )
                continue
            reframe(
                project.root,
                entry["clip_id"],
                rect=entry["rect"],
                pane=entry["pane"],
                src_start=entry["src_start"] or None,
            )
            entry["applied"] = True
            written += 1

    return {
        "project": str(project.root),
        "canvas": f"{resolution[0]}x{resolution[1]}",
        "threshold": threshold,
        "frames_per_window": frames,
        # How close a stored window has to be for this one to be the same
        # window. Reported rather than assumed, because it is the number that
        # decides whether `apply` leaves a hand-framed shot alone.
        "same_window_within": round(same_window, 5),
        "detector": detector,
        "windows": report,
        "count": len(report),
        "proposed": sum(1 for entry in report if entry["rect"] is not None),
        "refused": sum(1 for entry in report if entry["rect"] is None),
        # Of the proposals, how many are stacked splits. On the film this is 3
        # of 51 — a rule this strict is meant to be rare, and a run where it
        # is not is the signal to look at `reframe_sheet` before applying.
        "splits": sum(1 for entry in report if entry["pane"] is not None),
        "placements": len({shot for entry in report for shot in entry["shots"]}),
        "applied": written,
        "written": bool(written),
        "skipped": skipped,
    }


def reframe_coverage(
    path: Path | str,
    *,
    clip_id: str | None = None,
    threshold: float = SCENE_THRESHOLD,
) -> dict[str, Any]:
    """Which placed seconds are framed by a window chosen for an earlier shot.

    **The question `reframe_detect` cannot answer, because it is about the
    project as it stands rather than about a proposal.** A detect run reports
    `falls_back_to` for the windows it is refusing *this call*, and then throws
    it away; nothing is written for a refusal, so a project on disk has no way
    to say that 13.6s of one clip is held by a rect chosen for a shot that
    ended long before. The manifest, `status` and `reframe_sheet` were all
    clean over exactly that (HISTORY.md § The thirty-nine windows, reviewed).

    **One observable, two mechanisms, and this deliberately does not separate
    them** — because the render cannot. A window the detector refused writes
    nothing; a camera cut scoring under `threshold` is never offered a window
    at all. What reaches the film either way is one rect held across a cut, so
    what is measured is the cut with no window at it and the stretch of
    footage downstream of it.

    Every placement is walked against its own source's scene cuts. A cut with
    no window boundary within a frame of it opens a **stale stretch**, running
    to the next boundary or to the placement's end, and the whole stretch is
    framed by whatever was in force before the cut. Which is one of two things,
    and the distinction is the point: an **override** held across a cut is
    worse than the default, since a stale window looks deliberate, while the
    **centre crop** walking through one is only the default doing what it
    always did. `stale_seconds` counts the first; `default_seconds` the second.

    **A frame of tolerance, never an epsilon.** ffmpeg reports a cut at
    0.834167 where the manifest holds 0.8342 — the same cut, 33µs apart — and
    matching exactly reported 20 stale stretches on the film where there are 6.
    Two boundaries inside one source frame are one window, which is the
    resolution the render has (CLAUDE.md § The MLT reframe).

    **And the mirror, which is the one a viewer actually notices.** The walk
    above asks which cuts have no window; `steps` asks which windows have no
    cut — a boundary *inside* one placement, where the frame moves sideways
    and the picture behind it does not change. Coverage answers clean over
    exactly that, because nothing was held across anything: the teaser opened
    on 510px of sideways travel inside one continuous take, from a clip whose
    head was never framed, and every check in this project agreed with it
    (HISTORY.md § The teaser, re-cut). A boundary at a placement's own edge is
    not one of these — the timeline cuts there, so the frame is expected to.

    **The two directions do not use the same cut list, deliberately.** A cut
    has to score `threshold` to *demand* a window, because that floor was
    picked by a control against sixteen approved boundaries. It only has to be
    detected at all to *explain* one — a weak cut is still a picture change,
    and calling a justified boundary a defect sends someone to re-frame a shot
    that is already right. So `steps` is scored against the whole scan and each
    one carries `nearest_cut`, which is what says whether the boundary missed a
    real cut by 40ms or sits in the middle of a take.

    Needs no face detector: this is scene cuts against stored geometry, so it
    answers on a box where `reframe_detect` cannot run at all. It reads and
    never writes. Each stretch carries `timeline_start` — where it plays in the
    film — because the fix is to look at it, and `reframe_detect --clip` is
    what proposes a window for it.
    """
    if not 0 < threshold <= 1:
        raise ProjectError(f"a scene threshold is a score between 0 and 1, not {threshold}")

    project = Project.open(path)
    resolution = _mlt_resolution(project)
    placements, skipped = _sheet_placements(project, resolution)
    if clip_id is not None:
        clips = {str(clip.get("clip_id")) for clip in project.read_manifest().get("clips", [])}
        if clip_id not in clips:
            raise ProjectError(f"no clip {clip_id!r} in this project")
        placements = [p for p in placements if p["asset"] == clip_id]
    for placement in list(placements):
        if placement["reframe"] is None:
            skipped.append(
                {
                    "index": placement["index"],
                    "asset": placement["asset"],
                    "why": "this clip has no registered picture size to crop against",
                }
            )
            placements.remove(placement)
    if not placements:
        raise ProjectError(
            "this project has no footage placements to check — there is nothing "
            "here a window would apply to"
        )

    # One scan per clip, stopped at the last frame any placement of it reads —
    # `reframe_detect`'s own arithmetic, and for its reason: the decode is the
    # whole cost, and a 730s clip the film reads 71.8s of has no reason to be
    # walked to the end.
    scans: dict[str, list[dict[str, float]]] = {}
    assets = sorted({p["asset"] for p in placements})
    for done, asset in enumerate(assets):
        progress.report(done, len(assets), f"scanning {asset} for cuts")
        used = [p for p in placements if p["asset"] == asset]
        scans[asset] = media.scene_cuts(
            used[0]["path"], until=max(p["src_start"] + p["duration"] for p in used)
        )

    same_window = 1.0 / _export_fps(_clips_by_id(project))
    stored = _stored_reframes(project)

    stale: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    cuts_seen = 0
    cuts_framed = 0
    steps_seen = 0
    steps_cut = 0
    for placement in sorted(placements, key=lambda p: p["timeline_start"]):
        entry = placement["reframe"]
        start = placement["src_start"]
        end = start + placement["duration"]
        boundaries = [at for at, _ in entry.windows()]
        held = [at for at, *_ in stored.get(placement["asset"], [])]
        cuts = [cut for cut in scans[placement["asset"]] if cut["score"] >= threshold]

        def framed(at: float, edges: list[float] = boundaries) -> bool:
            return any(abs(edge - at) <= same_window for edge in edges)

        shown = [cut for cut in cuts if start < cut["src_time"] < end]
        cuts_seen += len(shown)
        cuts_framed += sum(1 for cut in shown if framed(cut["src_time"]))

        # The mirror. Only boundaries *inside* the placement: one at either
        # edge is a frame change the timeline's own cut already explains.
        #
        # **And "inside" takes the same frame of tolerance everything else
        # here does.** A window placed at a shot boundary is the normal case —
        # it is what `reframe_detect` writes — and the placement's own
        # `src_start` is computed while the window's is a rounded manifest
        # value, so they sit ~1e-7 apart and a strict comparison calls every
        # one of them an interior boundary. Measured before this line existed:
        # 13 of 15 findings on the vertical cut were that, and the two real
        # ones were the two a watch had already found.
        for at in [edge for edge in boundaries if start + same_window < edge < end - same_window]:
            before = entry.crop_at(at - same_window)
            after = entry.crop_at(at)
            was_split = entry.pane_at(entry.window_start(at - same_window))
            is_split = entry.pane_at(at)
            if before == after and was_split == is_split:
                # Two addresses, one framing — nothing moves, so there is
                # nothing for a cut to justify.
                continue
            steps_seen += 1
            # The whole scan, not the thresholded list: a cut too weak to
            # demand a window is still enough to explain one.
            near = min(scans[placement["asset"]], key=lambda c: abs(c["src_time"] - at), default=None)
            if near is not None and abs(near["src_time"] - at) <= same_window:
                steps_cut += 1
                continue
            steps.append(
                {
                    "index": placement["index"],
                    "asset": placement["asset"],
                    "timeline_at": round(placement["timeline_start"] + (at - start), 3),
                    "src_time": round(at, 4),
                    "from_rect": list(before),
                    "to_rect": list(after),
                    # In the source's own pixels, like the rects — how far the
                    # frame travels, which is what makes one of these visible
                    # rather than merely present.
                    "shift": round(
                        hypot(
                            (after[0] + after[2] / 2) - (before[0] + before[2] / 2),
                            (after[1] + after[3] / 2) - (before[1] + before[3] / 2),
                        )
                    ),
                    "nearest_cut": round(near["src_time"], 4) if near else None,
                    "nearest_cut_score": round(near["score"], 3) if near else None,
                    "nearest_cut_gap": round(abs(near["src_time"] - at), 3) if near else None,
                }
            )

        # **The question is asked of the footage, not of the cut** — because a
        # placement can begin *downstream* of the cut that stranded it and
        # never contain one. The film has three of those and an earlier walk
        # over the cuts inside each placement could not see any of them: the
        # cut is in source nothing shows, and the placement is stale from its
        # own first frame. So the stretch is split wherever the framing could
        # change — a window boundary, or a cut the framing does not follow —
        # and each piece is asked what is covering it.
        edges = {start}
        edges.update(at for at in boundaries if start < at < end)
        edges.update(
            cut["src_time"] for cut in shown if not framed(cut["src_time"])
        )
        points = sorted(edges)

        run: dict[str, Any] | None = None
        for index, at in enumerate(points):
            stop = points[index + 1] if index + 1 < len(points) else end
            governing = entry.window_start(at)
            # A cut between where this window began and where this footage
            # starts is the whole finding: the picture changed and the framing
            # did not follow it.
            crossed = [
                cut
                for cut in cuts
                if governing + same_window < cut["src_time"] <= at + same_window
            ]
            if not crossed:
                run = None
                continue
            # Two unframed cuts under one window are one stale stretch, not
            # two: `cold-open` holds a single rect across four camera setups
            # and that is one thing wrong. A change of governing window ends
            # the run even when the new one is stale too, because they are
            # different windows to go and fix.
            if run is not None and abs(run["held_from"] - governing) <= same_window:
                run["src_end"] = round(stop, 4)
                run["seconds"] = round(stop - run["src_start"], 3)
                run["cuts"] = sorted({*run["cuts"], *(round(c["src_time"], 4) for c in crossed)})
                continue
            override = framed(governing, held)
            run = {
                "index": placement["index"],
                "asset": placement["asset"],
                "timeline_start": round(placement["timeline_start"] + (at - start), 3),
                "src_start": round(at, 4),
                "src_end": round(stop, 4),
                "seconds": round(stop - at, 3),
                "held_from": round(governing, 4),
                # `reframe_detect`'s own two answers, in its own words: this is
                # the same question asked of a project rather than of a
                # proposal, and two vocabularies for one fact is how they drift.
                "framed_by": (
                    f"the window from {governing:.3f}s — a different shot's framing"
                    if override
                    else "the centre crop"
                ),
                "stale": override,
                "cuts": sorted({round(cut["src_time"], 4) for cut in crossed}),
                "scores": sorted({round(cut["score"], 3) for cut in crossed}),
            }
            stale.append(run)

    placed = sum(p["duration"] for p in placements)
    held_over = [row for row in stale if row["stale"]]
    stale_seconds = sum(row["seconds"] for row in held_over)
    default_seconds = sum(row["seconds"] for row in stale if not row["stale"])
    return {
        "project": str(project.root),
        "canvas": f"{resolution[0]}x{resolution[1]}",
        "threshold": threshold,
        "same_window_within": round(same_window, 5),
        "placements": len(placements),
        "placed_seconds": round(placed, 3),
        "cuts": cuts_seen,
        "cuts_framed": cuts_framed,
        "cuts_unframed": cuts_seen - cuts_framed,
        "stretches": stale,
        # The other direction, counted the same way round: boundaries that
        # move the frame inside one placement, and how many of them the
        # picture accounts for.
        "steps_seen": steps_seen,
        "steps_cut": steps_cut,
        "steps": steps,
        # The headline, and the only number that is a defect: an override held
        # across a camera cut. The centre crop walking through one is the
        # default doing what it always did, counted beside it and not with it.
        "stale_seconds": round(stale_seconds, 3),
        "stale_share": round(stale_seconds / placed, 4) if placed else 0.0,
        "stale_stretches": len(held_over),
        "default_seconds": round(default_seconds, 3),
        "skipped": skipped,
    }


# -- continuity checking --------------------------------------------------
#
# Ports goodsometimes' `shot_check.py` (rewind/replay) and its v5 scan (short
# shots, film-internal-cut stubs) into proofcut, over `_picture_plan`'s
# *resolved* `src_start` rather than `build_shots`' raw `src_pin` — the gap
# the standalone script had (`shot_check.py:82-84`'s own comment, wrong for
# any unpinned video cue: `src_pin` is `None` for one, `src_start` never is).
# Overrun needs no finding here: `mlt.plan_picture` already refuses it
# structurally (mlt.py's own overrun guard, "a pinned cue shows the moment it
# names or nothing" / the unpinned rewind-to-0 rather than clamp), so a shot
# that reaches this walk at all cannot overrun its asset. If that refusal is
# ever softened to a clamp, this stops catching what it silently relied on
# the other file to catch — worth this comment surviving that change.

CONTINUITY_MIN_SHOT = 3.0
CONTINUITY_STUB_TOLERANCE = 1.2
#: goodsometimes' own default — narrative time has to pass before a re-use
#: reads as a rhyme rather than a stumble.
CONTINUITY_GAP = 20.0
#: How far behind its asset's last position a shot has to land to count as a
#: rewind rather than measurement noise between two float computations of
#: "the same instant" — `reframe_coverage`'s own frame-of-tolerance idiom,
#: restated in seconds rather than frames because a rewind is a narrative
#: judgement, not a pixel-exact boundary.
CONTINUITY_REWIND_TOLERANCE = 0.05
#: How much of an earlier shot's source range a later one has to re-show to
#: count as a replay rather than two shots that merely sit near each other in
#: the same footage.
CONTINUITY_REPLAY_OVERLAP = 0.4

CONTINUITY_ACCEPTED_KEY = "continuity_accepted"


def _pseudo_head_shot(project: Project) -> dict[str, Any] | None:
    """The stored cold open, reshaped as a shot `continuity_check` can walk
    against — WORK-ORDERS ruling 6: "a body-first-shot rewind against the
    cold open is caught natively, no --prepend flag." It is never a
    finding's own *subject* (it has no cue to re-cue through cue_add/
    cue_rm), only the earliest same-asset context a real shot's rewind or
    replay is measured against.

    `start`/`duration` place it immediately before the Edit's own frame 0 —
    running from `-seconds` to `0.0` — so a first real shot that reuses the
    head's asset reads as a zero-timeline-gap rewind exactly the way two
    adjacent body shots would, with no second code path for the boundary.
    Returns `None` with no head, or with a head whose asset no longer
    resolves — the same "report, don't crash a read" treatment
    `reframe_coverage` gives an unregistered clip.
    """
    head_cfg = _stored_head(project)
    if head_cfg is None:
        return None
    try:
        resolved = _resolve_asset(project, head_cfg["asset"])
    except _PICTURE_REFUSALS:
        return None
    return {
        "clip_id": None,
        "word_index": None,
        "text": None,
        "asset": head_cfg["asset"],
        "src_start": head_cfg["src_start"],
        "start": -head_cfg["seconds"],
        "duration": head_cfg["seconds"],
        **resolved,
    }


def _continuity_finding(kind: str, shot: dict[str, Any], *, detail: str) -> dict[str, Any]:
    """One finding's fixed shape — always keyed to the shot it is *about*,
    never to whatever it was compared against. `word_index` is the cue's own
    address (never the asset — CLAUDE.md: "a shot's addressing clip is not
    its footage"), so a finding drives straight into `cue_add`/`cue_rm`.
    """
    return {
        "kind": kind,
        "clip_id": shot["clip_id"],
        "word_index": shot["word_index"],
        "text": shot.get("text"),
        "asset": shot["asset"],
        "start": round(float(shot["start"]), 3),
        "detail": detail,
    }


def _shot_continuity_findings(
    shots: list[dict[str, Any]],
    *,
    gap: float,
    min_shot: float,
    head_shot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Rewind, replay and short-shot findings, walked in timeline order.

    **Rewind** compares a shot only against the *immediately preceding* shot
    on the same asset — direct port of `shot_check.py`'s `faults()` — and
    fires when that shot lands behind where the previous one left off
    (`CONTINUITY_REWIND_TOLERANCE`) with less than `gap` seconds of timeline
    between them. **Replay** compares against *every* earlier shot on the
    same asset — goodsometimes' own design, reported rather than refused,
    because "a deliberate rhyme and a mistake look identical from the cue
    table" (the elevator's own two kept replays, `ideas/lambs-longlegs.md`)
    — and fires on a source-range overlap past `CONTINUITY_REPLAY_OVERLAP`
    with `gap` seconds or more between them. The two conditions are
    mutually exclusive on one pair (`< gap` vs `>= gap`), so a single
    (earlier, later) pair is never reported as both.

    A still is never walked here: a card has no source range to rewind or
    replay against, matching `_sheet_placements`' own "a still is never
    cropped" treatment.
    """
    findings: list[dict[str, Any]] = []
    walk: list[dict[str, Any]] = ([head_shot] if head_shot is not None else []) + list(shots)
    last_on_asset: dict[str, dict[str, Any]] = {}
    seen_on_asset: dict[str, list[dict[str, Any]]] = {}

    for shot in walk:
        if shot.get("is_image"):
            continue
        asset = str(shot["asset"])
        is_subject = shot.get("clip_id") is not None
        previous = last_on_asset.get(asset)

        if is_subject and previous is not None:
            behind = previous["src_start"] + previous["duration"] - shot["src_start"]
            timeline_gap = shot["start"] - (previous["start"] + previous["duration"])
            if behind > CONTINUITY_REWIND_TOLERANCE and timeline_gap < gap:
                findings.append(
                    _continuity_finding(
                        "rewind",
                        shot,
                        detail=(
                            f"lands {behind:.2f}s behind {asset!r}'s own last position, "
                            f"{timeline_gap:.2f}s of timeline later"
                        ),
                    )
                )

        if is_subject:
            for earlier in seen_on_asset.get(asset, []):
                overlap = min(
                    earlier["src_start"] + earlier["duration"], shot["src_start"] + shot["duration"]
                ) - max(earlier["src_start"], shot["src_start"])
                timeline_gap = shot["start"] - (earlier["start"] + earlier["duration"])
                if overlap > CONTINUITY_REPLAY_OVERLAP and timeline_gap >= gap:
                    findings.append(
                        _continuity_finding(
                            "replay",
                            shot,
                            detail=(
                                f"replays {overlap:.2f}s already shown of {asset!r}, "
                                f"{timeline_gap:.2f}s later"
                            ),
                        )
                    )

        if is_subject and shot["duration"] < min_shot:
            findings.append(
                _continuity_finding(
                    "short_shot",
                    shot,
                    detail=f"{shot['duration']:.2f}s, under the {min_shot:.2f}s floor",
                )
            )

        last_on_asset[asset] = shot
        seen_on_asset.setdefault(asset, []).append(shot)

    return findings


def _stub_findings(
    shots: list[dict[str, Any]],
    *,
    tolerance: float,
    threshold: float,
    same_window: float,
) -> list[dict[str, Any]]:
    """A shot that ends — or begins — right where its own footage has a real
    internal cut: a re-use trimmed to stop just short of, or start just past,
    a scene change *inside the source*, so what plays is a fragment of a shot
    rather than the shot itself (goodsometimes' v5 scan).

    Same call shape as `reframe_coverage`'s per-asset scan (one
    `media.scene_cuts` per asset, bounded by `until` — the furthest src
    second any shot of it reads), and the same frame-of-tolerance discipline:
    a cut sitting at or within `same_window` of a shot's own edge is the
    *expected* case — the timeline cuts there on purpose — and is excluded,
    scoring only the interior (`reframe_coverage`'s own "steps"/"stale"
    asymmetry, ops.py's own comment at its `steps` walk).

    Pays `media.scene_cuts`' decode cost per asset — not cheap, the same
    "needs no face detector is not the same as cheap" this repo already
    learned from `reframe_coverage` (CLAUDE.md § Frame mode) — so the caller
    gates this behind `stubs=` and never composes it into anything that
    re-runs on every `project-changed`.
    """
    real = [s for s in shots if not s.get("is_image") and s.get("clip_id") is not None]
    if not real:
        return []

    scans: dict[str, list[dict[str, float]]] = {}
    for asset in {str(s["asset"]) for s in real}:
        used = [s for s in real if str(s["asset"]) == asset]
        until = max(s["src_start"] + s["duration"] for s in used)
        scans[asset] = [
            cut for cut in media.scene_cuts(used[0]["asset_path"], until=until)
            if cut["score"] >= threshold
        ]  # fmt: skip

    findings: list[dict[str, Any]] = []
    for shot in real:
        asset = str(shot["asset"])
        start = shot["src_start"]
        end = start + shot["duration"]
        for cut in scans[asset]:
            at = cut["src_time"]
            if not (start + same_window < at < end - same_window):
                continue  # at (or outside) the shot's own edge — expected
            near_start = at - start <= tolerance
            near_end = end - at <= tolerance
            if not (near_start or near_end):
                continue
            edge, distance = ("start", at - start) if near_start else ("end", end - at)
            findings.append(
                _continuity_finding(
                    "stub",
                    shot,
                    detail=(
                        f"a real cut in {asset!r} sits {distance:.2f}s from this shot's own "
                        f"{edge} — likely trimmed to a fragment rather than the shot itself"
                    ),
                )
            )
    return findings


def _finding_fingerprint(finding: dict[str, Any]) -> dict[str, Any]:
    """The finding's own numbers, rounded — what `continuity_accept` stores
    and what a later run's recomputed finding is compared against.
    `unspoken`'s own staleness rule: a mismatch means the shot moved under
    the mark (re-cued, re-timed), and the finding is reported again rather
    than trusted blindly ("a stale mark is kept, never applied").
    """
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in finding.items()}


def _continuity_raw_findings(
    project: Project,
    *,
    gap: float,
    min_shot: float,
    stub_tolerance: float,
    stubs: bool,
    scene_threshold: float,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Every finding, unfiltered by any accepted mark — the one derivation
    `continuity_check`, `continuity_accept` and `continuity_ls` all share, so
    "is this finding still live" is answered the same way in all three.

    Returns `(findings, shots_error, stub_error)`. `shots_error` is
    `_PICTURE_REFUSALS` from `_picture_plan` itself — a stale/orphaned cue, an
    asset that no longer resolves — and empties `findings` entirely, the same
    "no picture, nothing to say" `timeline_view` already reports. `stub_error`
    is scoped to *only* the stub scan's own `media.scene_cuts` call: an asset
    that will not decode must not take the rewind/replay/short-shot findings
    — pure cue-table arithmetic, no media touched — down with it. Distinct
    fields because the two failures mean different things: one says nothing
    here could be checked, the other says *most* of it could.
    """
    rate = _export_fps(_clips_by_id(project))
    try:
        shots, _ = _picture_plan(project, rate)
    except _PICTURE_REFUSALS as exc:
        return [], str(exc), None
    head_shot = _pseudo_head_shot(project)
    findings = _shot_continuity_findings(shots, gap=gap, min_shot=min_shot, head_shot=head_shot)
    stub_error: str | None = None
    if stubs and shots:
        same_window = 1.0 / rate
        try:
            findings += _stub_findings(
                shots, tolerance=stub_tolerance, threshold=scene_threshold, same_window=same_window
            )
        except media.MediaError as exc:
            stub_error = str(exc)
    findings.sort(key=lambda f: f["start"])
    return findings, None, stub_error


def continuity_check(
    path: Path | str,
    *,
    gap: float = CONTINUITY_GAP,
    min_shot: float = CONTINUITY_MIN_SHOT,
    stub_tolerance: float = CONTINUITY_STUB_TOLERANCE,
    stubs: bool = True,
    scene_threshold: float = SCENE_THRESHOLD,
) -> dict[str, Any]:
    """Rewinds, replays, short shots, and film-internal-cut stubs.

    Ports goodsometimes' `shot_check.py` (rewind/replay) and its v5 scan
    (short shots, stubs) into proofcut, correcting the one gap the standalone
    script had: it read `build_shots`' raw `src_pin`, `None` for every
    *unpinned* cue, so it only ever checked pinned shots. This reads
    `_picture_plan`'s resolved `src_start` instead — the cursor-carried
    position `mlt.plan_picture` actually decided on — so an unpinned re-use
    is checked exactly like a pinned one.

    **The stored head is walked as a pseudo-shot before the first real
    one** (WORK-ORDERS ruling 6), so a body shot that rewinds into the cold
    open's own footage is caught the same way a body-to-body rewind is —
    no separate flag for it.

    **Overrun is not a finding here**: `mlt.plan_picture` already refuses it
    structurally, so a shot cannot reach this walk at all if it overruns its
    asset (see `_shot_continuity_findings`'s own module comment).

    **Replay is reported, never refused** — a deliberate narrative rhyme and
    a mistake look identical from the cue table alone (goodsometimes' own
    design, and proofcut's own `attribute_speakers`/`reframe_detect` precedent:
    a judgement call is surfaced, never silently decided).

    `stubs=True` by default and costs a `media.scene_cuts` decode per
    distinct asset placed — pass `stubs=False` to skip it. `scene_threshold`
    defaults to `SCENE_THRESHOLD` (0.15, pinned against this repo's own
    film) but is caller-settable on purpose: goodsometimes needed 0.12 on
    darker footage from a different film, and hard-coding 0.15 here would
    have silently under-detected on it.

    **Findings already acknowledged by `continuity_accept` are dropped**,
    unless the shot moved under the mark — `unspoken`'s own staleness rule.
    A stale one is kept (never silently re-suppressed) and marked
    `accepted_stale: True`; `accepted` counts the ones cleanly suppressed.

    A refused picture projection (a stale/orphaned cue, an asset that no
    longer resolves) reports `shots_error` and an empty finding list rather
    than raising — `timeline_view`'s own precedent for the same refusal
    class, `_PICTURE_REFUSALS`. A stub-scan failure on one asset is narrower:
    it reports `stub_error` and keeps every rewind/replay/short-shot finding
    computed from the cue table alone — that arithmetic touches no media, so
    one asset that will not decode must not take the rest of the report
    down with it.
    """
    project = Project.open(path)
    findings, shots_error, stub_error = _continuity_raw_findings(
        project,
        gap=gap,
        min_shot=min_shot,
        stub_tolerance=stub_tolerance,
        stubs=stubs,
        scene_threshold=scene_threshold,
    )
    if shots_error is not None:
        return {
            "findings": [],
            "count": 0,
            "accepted": 0,
            "accepted_stale": [],
            "shots_error": shots_error,
            "stub_error": None,
        }

    accepted = _stored_continuity_accepted(project)
    kept: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    suppressed = 0
    for finding in findings:
        key = (finding["clip_id"], finding["word_index"], finding["kind"])
        mark = accepted.get(key)
        if mark is None:
            kept.append(finding)
        elif mark["fingerprint"] == _finding_fingerprint(finding):
            suppressed += 1
        else:
            stale_finding = {**finding, "accepted_stale": True}
            kept.append(stale_finding)
            stale.append(stale_finding)
    return {
        "findings": kept,
        "count": len(kept),
        "accepted": suppressed,
        "accepted_stale": stale,
        "shots_error": None,
        "stub_error": stub_error,
    }


def _stored_continuity_accepted(project: Project) -> dict[tuple[str, int, str], dict[str, Any]]:
    stored = project.read_manifest().get(CONTINUITY_ACCEPTED_KEY, [])
    if not isinstance(stored, list):
        raise ProjectError(
            f"{project.manifest_path}'s {CONTINUITY_ACCEPTED_KEY!r} must be a JSON array"
        )
    marks: dict[tuple[str, int, str], dict[str, Any]] = {}
    for record in stored:
        key = (str(record["clip_id"]), int(record["word_index"]), str(record["kind"]))
        marks[key] = record
    return marks


def continuity_accept(path: Path | str, clip_id: str, word_index: int, kind: str) -> dict[str, Any]:
    """Acknowledge one continuity finding once — the deliberate elevator
    rhyme, never re-reported every run.

    Addressed the way a cue is (`clip_id`, `word_index` — the finding's own
    cue), plus `kind`, since one cue's shot can carry more than one finding.
    Stores a fingerprint of the finding's own numbers at accept time; a later
    run whose recomputed fingerprint disagrees means the shot moved under the
    mark (re-cued, re-timed) and the finding is reported again —
    `unspoken`'s own "a stale mark is kept, never applied" asymmetry: a
    suppressed real problem is invisible, a re-reported accepted one is only
    a minor annoyance.

    Refuses when no finding of `kind` currently sits at that cue — there is
    nothing to acknowledge, and accepting a finding that is not there would
    make a later real occurrence of it silently vanish the moment it appears
    (the fingerprint would already be stored, coincidentally or not). Runs
    the stub scan only when `kind == "stub"`, so accepting a rewind/replay/
    short-shot finding never pays `media.scene_cuts`' decode cost.
    """
    project = Project.open(path)
    findings, shots_error, stub_error = _continuity_raw_findings(
        project,
        gap=CONTINUITY_GAP,
        min_shot=CONTINUITY_MIN_SHOT,
        stub_tolerance=CONTINUITY_STUB_TOLERANCE,
        stubs=(kind == "stub"),
        scene_threshold=SCENE_THRESHOLD,
    )
    if shots_error is not None:
        raise ProjectError(f"cannot accept a continuity finding: {shots_error}")
    if kind == "stub" and stub_error is not None:
        raise ProjectError(f"cannot accept a stub finding: the scan itself failed: {stub_error}")
    match = next(
        (
            f
            for f in findings
            if f["clip_id"] == clip_id and f["word_index"] == word_index and f["kind"] == kind
        ),
        None,
    )
    if match is None:
        raise ProjectError(
            f"no {kind!r} finding at {clip_id!r} word {word_index} to accept — run "
            "continuity_check (CLI: `proofcut continuity-check`) to see current findings"
        )

    manifest = project.read_manifest()
    marks = manifest.setdefault(CONTINUITY_ACCEPTED_KEY, [])
    kept = [
        m
        for m in marks
        if not (
            str(m["clip_id"]) == clip_id
            and int(m["word_index"]) == word_index
            and str(m["kind"]) == kind
        )
    ]
    kept.append(
        {
            "clip_id": clip_id,
            "word_index": word_index,
            "kind": kind,
            "asset": match["asset"],
            "fingerprint": _finding_fingerprint(match),
        }
    )
    kept.sort(key=lambda m: (m["clip_id"], int(m["word_index"]), m["kind"]))
    manifest[CONTINUITY_ACCEPTED_KEY] = kept
    project.write_manifest(manifest)
    return {"clip_id": clip_id, "word_index": word_index, "kind": kind, "accepted": len(kept)}


def continuity_reject(path: Path | str, clip_id: str, word_index: int, kind: str) -> dict[str, Any]:
    """Unmark a continuity finding, putting it back into `continuity_check`."""
    project = Project.open(path)
    manifest = project.read_manifest()
    marks = manifest.get(CONTINUITY_ACCEPTED_KEY, [])
    kept = [
        m
        for m in marks
        if not (
            str(m["clip_id"]) == clip_id
            and int(m["word_index"]) == word_index
            and str(m["kind"]) == kind
        )
    ]
    if len(kept) == len(marks):
        raise ProjectError(f"no accepted {kind!r} finding at {clip_id!r} word {word_index}")
    if kept:
        manifest[CONTINUITY_ACCEPTED_KEY] = kept
    else:
        manifest.pop(CONTINUITY_ACCEPTED_KEY, None)
    project.write_manifest(manifest)
    return {"clip_id": clip_id, "word_index": word_index, "kind": kind, "accepted": len(kept)}


def continuity_ls(path: Path | str) -> dict[str, Any]:
    """Every accepted continuity finding, with whether it is still live and
    whether its fingerprint still matches what was accepted.

    `unspoken_ls`'s own shape: `stale` is true only when the finding is
    still found *and* disagrees with what was recorded — a finding that has
    disappeared entirely (the shot was re-cued away, or the issue was fixed)
    is reported via `still_found: False` rather than as stale, since there is
    nothing live to disagree with the mark. Pays the stub scan only when at
    least one accepted mark is itself a `stub` finding — and if that scan
    itself fails, every `stub`-kind row's `still_found` reports `None`
    (unknown) rather than `False`, so a scan failure never reads as "fixed".
    """
    project = Project.open(path)
    accepted = _stored_continuity_accepted(project)
    needs_stubs = any(kind == "stub" for (_, _, kind) in accepted)
    if accepted:
        findings, shots_error, stub_error = _continuity_raw_findings(
            project,
            gap=CONTINUITY_GAP,
            min_shot=CONTINUITY_MIN_SHOT,
            stub_tolerance=CONTINUITY_STUB_TOLERANCE,
            stubs=needs_stubs,
            scene_threshold=SCENE_THRESHOLD,
        )
    else:
        findings, shots_error, stub_error = [], None, None
    current = {(f["clip_id"], f["word_index"], f["kind"]): f for f in findings}

    rows: list[dict[str, Any]] = []
    for (clip_id, word_index, kind), mark in sorted(accepted.items()):
        if kind == "stub" and stub_error is not None:
            rows.append(
                {
                    "clip_id": clip_id,
                    "word_index": word_index,
                    "kind": kind,
                    "asset": mark.get("asset"),
                    "still_found": None,
                    "stale": False,
                }
            )
            continue
        live = current.get((clip_id, word_index, kind))
        stale = live is not None and _finding_fingerprint(live) != mark["fingerprint"]
        rows.append(
            {
                "clip_id": clip_id,
                "word_index": word_index,
                "kind": kind,
                "asset": mark.get("asset"),
                "still_found": live is not None,
                "stale": stale,
            }
        )
    return {
        "project": str(project.root),
        "count": len(rows),
        "stale": sum(1 for row in rows if row["stale"]),
        "shots_error": shots_error,
        "stub_error": stub_error,
        "accepted": rows,
    }


#: Where a project keeps a finishing pass on the very end — an end card or a
#: bumper, applied by `export` itself rather than by a person running ffmpeg
#: over a finished render afterward, which is a pass every derivation drops at
#: exit 0 with nothing in `status`, `verify` or `check_frames` ever noticing
#: (HISTORY.md § The bumper the teaser never had, § The end card). Read with
#: `.get()` and additive, the `CANVAS_KEY`/`CAPTION_STYLE_KEY` shape: absent
#: means what every project written before this key existed meant, that
#: nothing plays after the `Edit`'s own last frame — so this is not a
#: `SCHEMA_VERSION` bump. `{"asset": "card:name", "seconds": ..., "fade": ...}`
#: (PLAN.md § Tail time — the design note).
#:
#: **`asset` is always a card, never a clip, and `tail()` refuses the other
#: shape rather than storing it.** `verify` diffs a render's own
#: transcription against the timeline's words, and silence adds none of its
#: own — a media clip's audio would give `verify` something to disagree
#: about, permanently, on every project that ever set one.
TAIL_KEY = "tail"


def _stored_tail(project: Project) -> dict[str, Any] | None:
    """The project's tail, resolved to its three fields, or None for no tail.

    Validated on every read, not only on write — a manifest edited by hand or
    carried over from a future proofcut gets a message naming the shape rather
    than a `KeyError` three calls later inside `_build_mlt`.
    """
    stored = project.read_manifest().get(TAIL_KEY)
    if stored is None:
        return None
    if not isinstance(stored, dict):
        raise ProjectError(f"{project.manifest_path}'s {TAIL_KEY!r} must be a JSON object")
    try:
        asset = str(stored["asset"])
        seconds = float(stored["seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError(
            f"{project.manifest_path}'s {TAIL_KEY!r} must hold at least "
            f"'asset' and a numeric 'seconds', not {stored!r}"
        ) from exc
    fade = float(stored.get("fade", 0.0))
    return {"asset": asset, "seconds": seconds, "fade": fade}


def _tail_frames(project: Project, rate: float) -> int:
    """How many frames the configured tail adds at `rate`, 0 with none.

    Rounds the same way `autoeditor.frame_layout` rounds every segment edge —
    `round()`, not truncation — so a tail's own frame count is quantised on
    the export's grid by the same rule the rest of the timeline is, rather
    than by a second convention that happens to agree most of the time.
    `max(1, ...)` for the same reason a shot is: a positive `seconds` that
    rounds to zero frames at a coarse grid is a bug to surface downstream
    (an empty MLT entry), not silence the tail into never having existed.
    """
    tail = _stored_tail(project)
    if tail is None:
        return 0
    return max(1, round(tail["seconds"] * rate))


def _frame_total_with_tail(project: Project, edit: tl.Edit, rate: float) -> int:
    """`autoeditor.frame_total`, plus whatever a configured head or tail adds.

    The single answer to "how long is this" once a head or a tail exists to
    answer for (PLAN.md § Tail time — the design note): the `Edit` itself
    never grows to describe either bookend, so every caller that used to read
    `autoeditor.frame_total` straight moves to this instead of learning about
    `head`/`tail` on its own — a duration answered two ways is exactly how a
    render can disagree with its own timeline while both report clean, which
    is the failure `check_frames` exists to catch and would now be able to
    cause. Kept its original name rather than renamed for the head it also
    now covers — the name is quoted across a dozen docstrings and CLAUDE.md
    itself as "the single answer to how long is this," and a rename would
    have to chase every one of them in lockstep or the prose starts lying
    about which helper does what. A project that has never touched either
    renders through here byte-identically to `autoeditor.frame_total` alone,
    `_head_frames`/`_tail_frames` both being 0.
    """
    return autoeditor.frame_total(edit, rate) + _head_frames(project, rate) + _tail_frames(project, rate)


def _tail_silence(project: Project, seconds: float) -> Path:
    """The cached silent WAV a manufactured-silence entry reads from — the
    tail's audio-track entry, `vo_extend`'s hold, and the music lane's pads.

    Keyed on `seconds` alone — not on the frame rate a particular export
    happens to run at — because `picture.render_silence` already renders a
    half second past what was asked, so one file outlasts every grid a tail
    could ever be quantised onto. Rendered once and reused, the way a card's
    PNG is rendered once and re-read by every export after it.
    """
    dest = project.tail_dir / f"silence-{round(seconds * 1000)}ms.wav"
    if not dest.is_file():
        picture.render_silence(dest, seconds)
    return dest


def tail(
    path: Path | str,
    *,
    asset: str | None = None,
    seconds: float | None = None,
    fade: float | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the finishing pass this project plays after its last frame.

    An end card or a bumper, applied by `export` itself — the fix for a defect
    that has already shipped a video with one missing: a finishing pass glued
    on with ffmpeg after the fact is dropped by every derivation at exit 0,
    silently, because nothing in the project ever knew it existed (HISTORY.md
    § The bumper the teaser never had, § The end card). Called with no
    arguments it changes nothing and reports what is in force, which is also
    how to learn the field names.

    `asset` **must be a `card:name`, never a clip_id.** `verify` diffs a
    render's own transcription against the timeline's words; silence adds
    none of its own, and a media clip's audio would give it something to
    disagree about on every check from here on. `seconds` is the tail's whole
    length, card included — not the hold *before* a dissolve, with `fade`
    added on top of it. That is the known trap this key exists to not repeat
    (HISTORY.md § The bumper the teaser never had: `xfade` finishes exactly at
    the length it was given, so treating `seconds` as the hold-alone and
    adding `fade` on top runs the render long by exactly the fade). `fade` is
    recorded and echoed but **not yet drawn** — this build cuts to the card
    hard, at `seconds`, and a later pass can spend the stored value on an
    actual dissolve without a second manifest key.

    Setting `asset` or `seconds` the first time requires both together (there
    is no card with an unstated length, and no length with nothing to hold);
    either alone after that just updates its own field, `caption_style`'s
    partial-update shape. `reset` drops the tail entirely — a project with no
    `tail` key means exactly what it meant before this existed, nothing plays
    past the `Edit`'s own end.

    **This mechanism needs an existing picture cue lane covering the whole
    film.** A tail is two ordinary MLT entries — the card on the picture lane,
    a silent WAV on the audio track — and that only writes a document
    `mlt.document` will accept when the picture lane already covers every
    frame the audio track has (PLAN.md § Tail time — the design note, "What
    the writer already accepts"). A project whose picture comes straight off
    its own clip, with no cue table, has no second lane for a card to join;
    `export` refuses rather than duplicating the whole film onto one just to
    make room for six seconds at the end. Add cues first (`cue_add`), or this
    is the call that names why.

    `plan` resolves and validates without writing.
    """
    if reset and (asset is not None or seconds is not None or fade is not None):
        raise ProjectError("pass fields to change, or `reset`, not both")

    project = Project.open(path)
    stored = _stored_tail(project)
    changing = asset is not None or seconds is not None or fade is not None

    if reset:
        after: dict[str, Any] | None = None
    elif not changing:
        after = stored
    else:
        base = stored or {}
        merged_asset = asset if asset is not None else base.get("asset")
        merged_seconds = seconds if seconds is not None else base.get("seconds")
        merged_fade = fade if fade is not None else base.get("fade", 0.0)
        if merged_asset is None or merged_seconds is None:
            raise ProjectError(
                "a tail needs both `asset` and `seconds` set together the first "
                "time — there is no card with an unstated length, and no length "
                "with nothing to hold. Either alone after that updates its own "
                "field."
            )
        if not str(merged_asset).startswith("card:"):
            raise ProjectError(
                f"tail asset must be a card (card:name), not {merged_asset!r} — "
                "verify diffs a render's own transcription against the "
                "timeline's words, and silence adds none of its own; a media "
                "clip would give it something to disagree about on every check "
                "from here on"
            )
        if float(merged_seconds) <= 0:
            raise ProjectError(f"tail seconds must be positive, not {merged_seconds!r}")
        if float(merged_fade) < 0:
            raise ProjectError(f"tail fade must not be negative, not {merged_fade!r}")
        if float(merged_fade) > float(merged_seconds):
            raise ProjectError(
                f"tail fade ({merged_fade}) cannot exceed seconds ({merged_seconds}) "
                "— the fade is spent inside the tail's own length, never added to "
                "it (HISTORY.md § The bumper the teaser never had, the four-frame "
                "trap this key exists to not repeat)"
            )
        after = {
            "asset": str(merged_asset),
            "seconds": float(merged_seconds),
            "fade": float(merged_fade),
        }

    write = (reset or changing) and not plan
    if write:
        manifest = project.read_manifest()
        if after is None:
            manifest.pop(TAIL_KEY, None)
        else:
            manifest[TAIL_KEY] = after
        project.write_manifest(manifest)

    asset_exists: bool | None = None
    if after is not None:
        name = after["asset"].removeprefix("card:")
        asset_exists = (project.cards_dir / f"{name}.png").is_file()

    return {
        "project": str(project.root),
        "tail": after,
        "asset_exists": asset_exists,
        "written": write,
        "reset": bool(reset),
        "plan": bool(plan),
    }


#: A cold open, played before the `Edit`'s own first frame — `tail`'s sibling
#: at the other end of the film, and the fix for `goodsometimes`'
#: `cold_open()`: ffmpeg-concatenated onto the *already-rendered* body,
#: entirely outside proofcut, invisible to `status`, `verify`, `check_frames`,
#: and to `shot_check.py --prepend`'s hand-rolled offset, which existed only
#: because `proofcut shots` could not see the prepend at all. Additive and
#: optional — absent means exactly what every older manifest means, nothing
#: plays before the `Edit`'s own frame 0 — so this is not a `SCHEMA_VERSION`
#: bump, the `CANVAS_KEY`/`CAPTION_STYLE_KEY`/`TAIL_KEY` precedent.
#:
#: `{"asset": clip_id, "src_start": ..., "seconds": ..., "fade_in": ...,
#: "fade_out": ..., "gain_db": ...}`.
#:
#: **`asset` is always a registered clip_id, never a card — the exact inverse
#: of `tail`'s rule, deliberately.** A cold open is real footage with real
#: dialogue by definition, so restricting it to silence would defeat the
#: reason it exists; `verify` is taught to trim its own words instead
#: (`head_words_trimmed`) rather than the asset being forced silent.
HEAD_KEY = "head"


def _stored_head(project: Project) -> dict[str, Any] | None:
    """The project's head, resolved to its six fields, or None for no head.

    Validated on every read, not only on write — `_stored_tail`'s own
    discipline: a manifest edited by hand or carried over from a future
    proofcut gets a message naming the shape rather than a `KeyError` three
    calls later inside `_build_mlt`.
    """
    stored = project.read_manifest().get(HEAD_KEY)
    if stored is None:
        return None
    if not isinstance(stored, dict):
        raise ProjectError(f"{project.manifest_path}'s {HEAD_KEY!r} must be a JSON object")
    try:
        asset = str(stored["asset"])
        seconds = float(stored["seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError(
            f"{project.manifest_path}'s {HEAD_KEY!r} must hold at least "
            f"'asset' and a numeric 'seconds', not {stored!r}"
        ) from exc
    return {
        "asset": asset,
        "src_start": float(stored.get("src_start", 0.0)),
        "seconds": seconds,
        "fade_in": float(stored.get("fade_in", 0.0)),
        "fade_out": float(stored.get("fade_out", 0.0)),
        "gain_db": float(stored.get("gain_db", 0.0)),
    }


def _head_frames(project: Project, rate: float) -> int:
    """How many frames the configured head adds at `rate`, 0 with none.

    `_tail_frames`'s own rounding rule — `round()`, `max(1, ...)` — so a
    head's frame count is quantised on the export's grid the same way every
    other edge is, rather than by a second convention.
    """
    head = _stored_head(project)
    if head is None:
        return 0
    return max(1, round(head["seconds"] * rate))


def _head_seconds(project: Project) -> float:
    """The stored head's own length in seconds, or 0.0 with none.

    The single offset primitive every render-time reader adds. **Two clocks
    exist once a head is set**: Edit time (0 = the `Edit`'s own first frame —
    cue addressing, `Edit.timeline_span`/`timeline_spans`, `locate`'s and
    `timeline_view`'s reported numbers, all unchanged) and render time
    (0 = the actual exported file's first frame = Edit time + this). Nothing
    that reads Edit time needs this; `add_captions`'s ASS write, `verify`'s
    heard-word trim, and `_build_mlt`'s music lead pad all do, because they
    describe or check the render rather than the `Edit`.
    """
    head = _stored_head(project)
    return 0.0 if head is None else head["seconds"]


def head(
    path: Path | str,
    *,
    asset: str | None = None,
    src_start: float | None = None,
    seconds: float | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    gain_db: float | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the cold open this project plays before its first frame.

    `tail`'s mirror at the other end of the film — the same read/partial-
    update/reset/plan shape — but its asset rule runs the other way:
    **`asset` must be a registered clip_id, never `card:name`.** `tail`
    forbids real audio because `verify` diffs a render's own transcription
    against the timeline's words and would gain a permanent disagreement it
    can never resolve; a head is *for* real audio — that is the entire point
    of a cold open — so `verify` accounts for it instead
    (`head_words_trimmed`) rather than the asset being restricted to silence.

    Setting `asset` or `seconds` for the first time needs both together —
    `tail`'s rule, there is no clip with an unstated length and no length
    with nothing to hold; either alone after that updates just that field.
    `src_start` defaults to `0.0` on a first set — unlike `tail`'s `asset`,
    this is not project-private data with no sane default, so there is no
    `PROOFCUT_TTS_VOICE`-style refusal for omitting it. `seconds` is the head's
    own *whole* length — `tail`'s "seconds is not the hold before a fade"
    rule, restated: the fades are spent inside it, never added on top.

    `fade_in`/`fade_out` default to `0.0` and, **unlike `tail`'s `fade`, are
    drawn from day one** — `Entry.fade_in_frames`/`fade_out_frames` already
    exist (built for the A2 lane), and this feature's whole reason for
    existing is the seam a missing fade produces (room tone butt-joined to
    digital silence in one frame), so there is no "recorded but not yet
    drawn" stub state here. `gain_db` defaults to `0.0`, a flat non-fading
    level shift (`mlt.Entry.gain_db`) distinct from the fades — a cold open's
    own beat can need a different level from the body without ramping to
    reach it.

    Refused rather than clamped, every measured number named: `src_start`
    negative; `seconds` non-positive; either fade negative; the two fades
    summing past `seconds` (`tail`'s "spent inside the length" rule,
    restated for two fades); and `src_start + seconds` running past the
    asset's own duration (`plan_picture`'s "a shot longer than its asset"
    refusal, restated for a head).

    **This mechanism needs an existing picture cue lane covering the whole
    film**, `tail`'s own requirement: `mlt.document` only accepts a picture
    lane that covers the audio track exactly whenever one exists, and a
    project whose picture comes straight off its own clip has no second lane
    a head could join. Add cues first (`cue_add`), or `export` names why.

    `reset` drops the head entirely — a project with no `head` key means
    exactly what it meant before this existed, nothing plays before the
    `Edit`'s own first frame. `plan` resolves and validates without writing.
    """
    fields = (asset, src_start, seconds, fade_in, fade_out, gain_db)
    if reset and any(value is not None for value in fields):
        raise ProjectError("pass fields to change, or `reset`, not both")

    project = Project.open(path)
    stored = _stored_head(project)
    changing = any(value is not None for value in fields)

    if reset:
        after: dict[str, Any] | None = None
    elif not changing:
        after = stored
    else:
        base = stored or {}
        merged_asset = asset if asset is not None else base.get("asset")
        merged_seconds = seconds if seconds is not None else base.get("seconds")
        merged_src_start = src_start if src_start is not None else base.get("src_start", 0.0)
        merged_fade_in = fade_in if fade_in is not None else base.get("fade_in", 0.0)
        merged_fade_out = fade_out if fade_out is not None else base.get("fade_out", 0.0)
        merged_gain_db = gain_db if gain_db is not None else base.get("gain_db", 0.0)
        if merged_asset is None or merged_seconds is None:
            raise ProjectError(
                "a head needs both `asset` and `seconds` set together the first "
                "time — there is no clip with an unstated length, and no length "
                "with nothing to hold. Either alone after that updates its own "
                "field."
            )
        if str(merged_asset).startswith("card:"):
            raise ProjectError(
                f"head asset must be a registered clip_id, not a card "
                f"({merged_asset!r}) — the exact inverse of tail's rule: a "
                "cold open is real footage, and `tail` is the mechanism for a "
                "card"
            )
        resolved_clip = media.get_clip(project, str(merged_asset))
        if not resolved_clip.get("has_video"):
            raise ProjectError(
                f"head asset {merged_asset!r} has no video — a cold open "
                "needs a picture, the same reason a picture cue does"
            )
        if float(merged_src_start) < 0:
            raise ProjectError(f"head src_start must not be negative, not {merged_src_start!r}")
        if float(merged_seconds) <= 0:
            raise ProjectError(f"head seconds must be positive, not {merged_seconds!r}")
        if float(merged_fade_in) < 0:
            raise ProjectError(f"head fade_in must not be negative, not {merged_fade_in!r}")
        if float(merged_fade_out) < 0:
            raise ProjectError(f"head fade_out must not be negative, not {merged_fade_out!r}")
        if float(merged_fade_in) + float(merged_fade_out) > float(merged_seconds):
            raise ProjectError(
                f"head fades ({merged_fade_in}+{merged_fade_out}) cannot exceed "
                f"seconds ({merged_seconds}) — the fades are spent inside the "
                "head's own length, never added to it (tail's own rule, "
                "restated for two fades)"
            )
        asset_duration = resolved_clip.get("duration")
        if asset_duration is not None and float(merged_src_start) + float(
            merged_seconds
        ) > float(asset_duration) + tl.MIN_SEGMENT:
            raise ProjectError(
                f"head asset {merged_asset!r} is {asset_duration}s long, so "
                f"src_start {merged_src_start} + seconds {merged_seconds} = "
                f"{float(merged_src_start) + float(merged_seconds)} runs past "
                "its end — shorten seconds, move src_start back, or use a "
                "longer clip"
            )
        after = {
            "asset": str(merged_asset),
            "src_start": float(merged_src_start),
            "seconds": float(merged_seconds),
            "fade_in": float(merged_fade_in),
            "fade_out": float(merged_fade_out),
            "gain_db": float(merged_gain_db),
        }

    write = (reset or changing) and not plan
    if write:
        manifest = project.read_manifest()
        if after is None:
            manifest.pop(HEAD_KEY, None)
        else:
            manifest[HEAD_KEY] = after
        project.write_manifest(manifest)

    asset_registered: bool | None = None
    if after is not None:
        asset_registered = any(
            c.get("clip_id") == after["asset"]
            for c in project.read_manifest().get("clips", [])
        )

    return {
        "project": str(project.root),
        "head": after,
        "asset_registered": asset_registered,
        "written": write,
        "reset": bool(reset),
        "plan": bool(plan),
    }


def vo_extend(
    path: Path | str,
    clip_id: str,
    word_index: int | None = None,
    seconds: float | None = None,
    *,
    plan: bool = False,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Open a gap in `clip_id`'s track for material the recording never had.

    PLAN.md § `vo_extend` — the design note: the one item authorized to bend
    `Edit`'s subtractive invariant, for the case (and it is one case, not two —
    the note's own finding) of letting a line the film's own footage carries
    play under a hold in the VO, or manufacturing silence mid-film for the
    same reason. It is **not** the tail — that is downstream of `Edit` and
    unaffected by this (`tail`) — and it is not `restore`, which only ever
    walks the subtractive invariant backward.

    `word_index` names the last word *before* the gap; the hold opens
    immediately after that word's own end, in `clip_id`'s source time. The
    word must currently be on the timeline — an index that names cut material
    has nothing for "after" to mean, and is refused rather than guessed at,
    the same refusal `Edit.insert` raises for the case with no echo to give it
    a face.

    Addressed by `word_index` **or** `phrase` — a phrase binds to its
    **last** word, matching this tool's own meaning ("the last word before
    the gap"), the same edge goodsometimes' own `gap` binding used.
    `after`/`occurrence` disambiguate a phrase that matches more than once.
    There is no persistent gap-record to re-resolve later (`vo_extend` splices
    real source in, it does not store an address the way a cue does), so
    `cue_reresolve` does not cover this — resolve the phrase again by hand
    against a re-attached transcript if a hold needs to move.

    The manufactured stretch is real source, never a `clip_id` widened past
    its registered duration (the design note's shape A, disallowed as
    unreadable — `_build_mlt` would hand melt frames the file does not have).
    It is a silent WAV rendered by `picture.render_silence`, imported like any
    other asset (`media.import_media`) and spliced in as its own segment via
    `Edit.insert` — so a second call for the same `seconds` reuses the same
    registered clip, the same dedup `import_media` gives any re-imported path.
    Nothing about `clip_id`'s own word indices moves: `Edit.insert` adds
    timeline length downstream of the hold, never renumbers a source
    coordinate upstream of it (the line this note draws and forbids crossing
    — a manufactured stretch spliced into the *recording itself* would).

    **What this reports, and the reason the design note exists at all:**
    `build_shots` runs every shot from its cue's frame to the next, so
    whichever picture was already playing auto-extends across a hold by
    default — a silent success, with `shots_error`/`verify`/`check_frames`
    all staying clean, because nothing was orphaned and nothing went missing.
    `covered_by` names every shot (if any) whose span now overlaps the opened
    gap, computed over the *mutated* edit before it is decided whether to
    save it — a project with no cue table at all has no picture layer to
    freeze and reports `covered_by: []` truthfully, not as a lie of omission.

    Two real, one-way consequences ride along, both already true of the
    machinery rather than new code here: `restore` refuses across the hold
    the moment `clip_id`'s segments stop being contiguous (its own
    interleaved-segments check, unchanged), and export permanently switches
    to the MLT writer once the timeline holds more than one `clip_id`
    (`_is_layered`'s existing test) — there is no path back to auto-editor
    for a project that has ever been extended.

    `plan=True` resolves and reports `covered_by` without writing anything —
    not the timeline and not the manifest, `cut_by_time`'s and `tail`'s own
    rule. It renders the silence WAV (a cached, unregistered file under
    `tail_dir`, the same one a real call would reuse) so the preview's
    `covered_by` is computed exactly the way the real edit would be, but
    stops short of `import_media`, which is what would actually add the clip
    to the project — that write happens only once this runs for real, and a
    plan therefore reports a placeholder `hold_clip_id` rather than the one
    that will exist.
    """
    if seconds is None:
        raise tl.TimelineError("vo_extend needs seconds — how long the hold should be")
    seconds = float(seconds)
    if seconds <= 0:
        raise tl.TimelineError(f"seconds must be positive, not {seconds!r}")

    project = Project.open(path)
    parsed = _transcript(project, clip_id) if phrase is not None else None
    word_index, _ = _resolve_word_or_phrase(
        parsed, word_index=word_index, phrase=phrase, after=after, occurrence=occurrence, edge="last"
    )
    return _splice_after(
        project,
        clip_id,
        word_index,
        lambda: _tail_silence(project, seconds),
        seconds,
        plan=plan,
        placeholder=f"hold-{round(seconds * 1000)}ms",
    )


def _splice_point(
    project: Project, clip_id: str, word_index: int
) -> tuple[dict[str, Any], float, float, tl.Edit]:
    """Resolve where a splice after `word_index` lands: the word's echo, its
    source end, its timeline time, and the loaded edit — refusing a word that
    is not on the timeline. Separate from `_splice_after` so `vo_synth` can
    refuse *before* it spends the GPU on a render it would then not place."""
    media.get_clip(project, clip_id)
    parsed = _transcript(project, clip_id)
    word_index = int(word_index)
    echo = _cue_echo(parsed, word_index)
    at = echo["end"]
    edit = _load_edit(project)
    timeline_at = edit.timeline_time(clip_id, at, closed_end=True)
    if timeline_at is None:
        raise tl.TimelineError(
            f"word {word_index} ({echo['text']!r}) of clip {clip_id!r} is not on "
            "the timeline (already cut) — a splice opens after material "
            "that currently plays, and this word does not"
        )
    return echo, at, timeline_at, edit


def _splice_after(
    project: Project,
    clip_id: str,
    word_index: int,
    source: Callable[[], Path],
    seconds: float,
    *,
    plan: bool,
    placeholder: str,
    register_as: str | None = None,
) -> dict[str, Any]:
    """Splice `seconds` of a real file into `clip_id`'s track right after `word_index`.

    The mechanism `vo_extend` documents, factored so `vo_synth` can put a
    *voiced* clip through exactly the same path as a silent one: echo the word,
    refuse if it is not on the timeline, register the file (`import_media`,
    deduplicating a re-import), `Edit.insert`, then `covered_by` over the
    mutated edit before deciding whether to save it. `source` is a callable so
    that nothing is rendered or registered for a call that is about to be
    refused; `placeholder` is the `hold_clip_id` a plan reports, since a plan
    registers nothing; `register_as` is the clip_id the file is registered
    under (a synthesised line's `synth-<key>-s<seed>`, so the manifest reads
    as what it holds rather than `s1`), or `import_media`'s own slug when None.
    """
    echo, at, timeline_at, edit = _splice_point(project, clip_id, word_index)

    # The shot-plan check below runs against the placeholder, and only a splice
    # it lets through renders, registers or saves anything. Registering first
    # is a manifest write — so an undo snapshot — and a refusal after it left
    # an orphaned clip and an undo press that changed nothing (HISTORY.md § The
    # Lambs/Longlegs native rebuild).
    before = edit.duration
    edit.insert(clip_id, at, placeholder, 0.0, seconds)

    rate = _rate(project)
    shots, _ = _picture_plan(project, rate, edit=edit)
    gap_start, gap_end = timeline_at, timeline_at + seconds
    covered_by = [
        {
            "clip_id": shot["clip_id"],
            "word_index": shot["word_index"],
            "asset": shot["asset"],
            "start": shot["start"],
            "duration": shot["duration"],
        }
        for shot in shots
        if shot["start"] < gap_end and shot["start"] + shot["duration"] > gap_start
    ]

    hold_clip_id = placeholder
    if not plan:
        hold_clip_id = media.import_media(project, source(), clip_id=register_as)["clip_id"]
        edit = _load_edit(project)
        edit.insert(clip_id, at, hold_clip_id, 0.0, seconds)
        _save_edit(project, edit)

    return {
        "clip_id": clip_id,
        "hold_clip_id": hold_clip_id,
        "seconds": seconds,
        "timeline_start": timeline_at,
        "timeline_end": gap_end,
        "duration_before": before,
        "duration_after": edit.duration,
        "covered_by": covered_by,
        "written": not plan,
        "plan": bool(plan),
        **echo,
    }


#: `cache/synth/<key>/` — one directory per (voice, text, cap), holding one
#: WAV per seed and the worker's `candidates.json`. A preview-class artifact
#: like a thumbnail: never enters the manifest on its own, and is reached
#: through `vo_synth`'s return value, never by a client-named path. What *does*
#: enter the manifest is the winner, when a splice is asked for, through
#: `media.import_media` — the same registration a silent hold gets.
SYNTH_DIR = "cache/synth"

#: How many seeds a call renders when not told. Three is where round 2's
#: best-of was measured to matter and where a sentence still renders in well
#: under a minute on the 5070 (model load ≈5 s, then ≈3 s a render).
SYNTH_CANDIDATES = 3

#: The longest render a single call will accept, in seconds. One 21 s reference
#: once ran every render out to 655 s (local-llm's note, round 2); a sentence
#: is under fifteen, so twenty is a cap a real line never reaches.
SYNTH_MAX_SECONDS = 20.0

#: The whisper model the readback uses. `small.en` rather than `asr.DEFAULT_MODEL`
#: (turbo): a single sentence at 24 kHz transcribes in a couple of seconds on
#: it, and the question being asked — did the clone say the words — is one it
#: answered at 2–3% WER across 250 rendered lines in the spike.
SYNTH_READBACK_MODEL = "small.en"


#: The flatness penalty's two numbers, both measured (goodsometimes,
#: 2026-08-24, on the Lambs/Longlegs synth VO — the essay whose flat winners
#: prompted this). Likeness alone systematically keeps the flattest read: sims
#: inside one seed pool differ by thousandths while pitch spread differs by
#: *semitones*, so the flattest take wins on noise. The floor is where the
#: penalty starts — 4.5 st, just under the 4.6–6.8 st band the one measured
#: voice's own reference clips sit in — and the weight prices a semitone of
#: missing movement at 0.002 sim, the measured likeness gap between a clone
#: and a real take of the same speaker (0.9895 vs 0.993). A take *in* the band
#: pays nothing; a 2.5 st monotone pays 0.004, which outbids a thousandths sim
#: edge without ever outbidding a real likeness difference. Both are per-voice
#: numbers wearing defaults from the only voice measured so far; `flat_floor`/
#: `flat_weight` on the op are the override, and 0 disables the penalty.
SYNTH_FLAT_FLOOR = 4.5
SYNTH_FLAT_WEIGHT = 0.002


def _load_lexicon(path: Path | str | None, project_root: Path) -> tuple[dict[str, Any], str | None]:
    """The synth lexicon — `{"say": {written: respelling}, "hear": {variant: canonical}}`.

    An explicit path wins and must exist; otherwise `<project>/lexicon.json`
    is picked up when present, because pronunciation fixes are content, not
    tooling, and the project is where the content lives. `say` respells what
    the model is *given* ("Clarice" → "Clariss" is how a mispronunciation is
    fixed — instruct prompts made renders worse, local-llm round 3); `hear`
    folds whisper's spelling of a word back to the script's before the WER is
    scored ("long legs" → "longlegs"), so a transcription-spelling miss stops
    costing error budget that should be catching real misreads.
    """
    if path is not None:
        p = Path(path).expanduser()
        if not p.is_file():
            raise tl.TimelineError(f"no lexicon at {p}")
    else:
        p = project_root / "lexicon.json"
        if not p.is_file():
            return {}, None
    data = json.loads(p.read_text(encoding="utf-8"))
    for key in data:
        if key not in ("say", "hear"):
            raise tl.TimelineError(f'lexicon {p} has an unknown key {key!r} — it takes "say" and "hear"')
    return data, str(p)


def _apply_say(text: str, lexicon: dict[str, Any]) -> str:
    """Respell each `say` word, whole words only, case-insensitively."""
    for written, respelt in (lexicon.get("say") or {}).items():
        text = re.sub(rf"\b{re.escape(written)}\b", respelt, text, flags=re.IGNORECASE)
    return text


def _fold(text: str, lexicon: dict[str, Any]) -> str:
    """Lower-case and map every `hear` variant to its canonical form, both WER sides."""
    text = text.lower()
    for variant, canonical in (lexicon.get("hear") or {}).items():
        text = text.replace(variant.lower(), canonical.lower())
    return text


def _norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("-", " "))


def _wer(reference: list[str], hypothesis: list[str]) -> float:
    """Word error rate — Levenshtein over words, normalised by the reference length."""
    prev = list(range(len(hypothesis) + 1))
    for i in range(1, len(reference) + 1):
        cur = [i] + [0] * len(hypothesis)
        for j in range(1, len(hypothesis) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (reference[i - 1] != hypothesis[j - 1]))
        prev = cur
    return prev[-1] / max(1, len(reference))


def vo_synth(
    path: Path | str,
    text: str,
    *,
    voice: str | None = None,
    candidates: int = SYNTH_CANDIDATES,
    seed: int = 0,
    max_seconds: float = SYNTH_MAX_SECONDS,
    clip_id: str | None = None,
    word_index: int | None = None,
    readback: bool = True,
    plan: bool = False,
    lexicon: str | None = None,
    flat_floor: float = SYNTH_FLAT_FLOOR,
    flat_weight: float = SYNTH_FLAT_WEIGHT,
) -> dict[str, Any]:
    """Say `text` in a cloned voice: render `candidates` seeds, rank them by likeness less flatness, verify the winner by ear.

    The backend and the measurement behind its shape are `tts.py`'s docstring
    and local-llm's `notes/voice-clone-zero-shot.md`: zero-shot Qwen3-TTS with
    a ≈19 s reference beat every fine-tune on the model's own speaker-encoder
    likeness, and seed moved a render more than the reference did — so the op
    renders several and picks, rather than rendering once and hoping.

    **What is chosen and how.** Seeds `seed .. seed+candidates-1` render in one
    worker process; each comes back with `sim` — cosine of its speaker embedding
    against the reference's (real takes of the same speaker score ≈0.99, a
    three-semitone pitch shift ≈0.96) — and `spread`, its voiced pitch movement
    in semitones. The winner is the highest `sim` less a flatness penalty of
    `flat_weight` per semitone below `flat_floor` (`SYNTH_FLAT_FLOOR`'s comment
    is the measurement; `flat_weight=0` restores likeness-only, and a candidate
    with no `spread` — an old cache, too little voiced audio — pays nothing),
    a lower seed breaking ties. Likeness alone systematically keeps the
    flattest read, because sims inside one pool differ by thousandths while
    spread differs by semitones. A candidate that hit the length cap is
    `capped` and never wins while an uncapped one exists: it did not end
    because the line did. The winner is then **read back** through whisper
    (`SYNTH_READBACK_MODEL`) and `heard`/`wer` are reported beside it, because
    a clone that sounds like the speaker and says the wrong words is the
    failure nothing else here sees; `readback=False` skips it for a caller
    that will listen.

    **Nothing is decided from the transcript of a render** — the ranking is on
    the numbers above and the readback is a report. A caller wanting a
    different take re-runs with another `seed`, a different set of tickets.

    **The lexicon.** `lexicon` (else `<project>/lexicon.json`, if present) is
    `{"say": {...}, "hear": {...}}` — `_load_lexicon`'s docstring says which
    side fixes which fault. The model is given the `say`-respelt text (reported
    as `say_text` when it differs) and the WER is scored through the `hear`
    folds on both sides; the words spliced into the timeline are still `text`'s.

    **Cache.** Renders land under `cache/synth/<key>/`, keyed on the voice, the
    text and the cap, one WAV per seed, so a repeat call (or a `plan` after a
    real call) answers from disk without the GPU; a new `seed` range renders
    only the seeds it does not have. Preview-class containment, `thumbnail`'s:
    the directory never enters the manifest and no client names a path into it.

    **Splice.** With `clip_id` and `word_index`, the winner is registered
    (`media.import_media`) and spliced into that clip's track right after the
    word, through the mechanism `vo_extend` documents (`_splice_after`) — so
    every one-way consequence there (melt routing, `restore` refusing across
    the seam, `covered_by` naming the picture that now runs over it) is this
    op's too. Without them it only renders, and returns where.

    `plan=True` resolves the voice and the interpreter, reports the cache, and
    — if every seed is already rendered — the ranking and the splice preview,
    without synthesising, registering or writing the timeline. A plan with
    nothing cached says so (`rendered: False`) rather than spending the GPU.
    """
    text = " ".join(str(text).split())
    if not text:
        raise tl.TimelineError("text is empty — nothing to synthesise")
    candidates = int(candidates)
    if candidates < 1:
        raise tl.TimelineError(f"candidates must be at least 1, not {candidates!r}")
    if (clip_id is None) != (word_index is None):
        raise tl.TimelineError("clip_id and word_index go together — both or neither")
    max_seconds = float(max_seconds)
    tts.max_new_tokens(max_seconds)  # refuses a non-positive cap by name

    project = Project.open(path)
    if clip_id is not None and word_index is not None:
        _splice_point(project, clip_id, word_index)  # refuse before the render, not after it
    lex, lex_path = _load_lexicon(lexicon, project.root)
    say_text = _apply_say(text, lex)
    voice_path = tts.voice_dir(voice)
    ref_text = (voice_path / "ref.txt").read_text(encoding="utf-8").strip()
    # Keyed on what the model is given: a respelt line is different audio, and
    # a line no `say` rule touches keeps the key it had before lexicons existed.
    key = hashlib.sha256(
        json.dumps(
            {"voice": str(voice_path), "ref_text": ref_text, "text": say_text, "max_seconds": max_seconds},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    out_dir = project.root / SYNTH_DIR / key
    meta_path = out_dir / "candidates.json"
    known: dict[int, dict[str, Any]] = {}
    if meta_path.is_file():
        known = {int(c["seed"]): c for c in json.loads(meta_path.read_text(encoding="utf-8"))}
    seeds = list(range(int(seed), int(seed) + candidates))
    missing = [s for s in seeds if s not in known or not Path(known[s].get("path", "")).is_file()]

    if missing and plan:
        return {
            "text": text,
            "voice": str(voice_path),
            "seeds": seeds,
            "cache_dir": str(out_dir),
            "rendered": False,
            "missing_seeds": missing,
            "synth": tts.available(voice_path),
            "plan": True,
            "written": False,
        }
    if missing:
        detector = tts.available(voice_path)
        if not detector["available"]:
            raise tts.TTSError(str(detector["why"]))
        for entry in tts.synth(say_text, voice_path, out_dir, missing, max_seconds=max_seconds):
            known[int(entry["seed"])] = entry
        out_dir.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(sorted(known.values(), key=lambda c: c["seed"]), indent=1), encoding="utf-8"
        )

    rendered = [dict(known[s]) for s in seeds]
    ok = [c for c in rendered if "error" not in c]
    if not ok:
        raise tts.TTSError(
            "every candidate failed: " + "; ".join(f"seed {c['seed']}: {c['error']}" for c in rendered)
        )
    uncapped = [c for c in ok if not c.get("capped")]
    pool = uncapped or ok

    def _score(c: dict[str, Any]) -> float:
        spread = c.get("spread")
        penalty = float(flat_weight) * max(0.0, float(flat_floor) - float(spread)) if spread is not None else 0.0
        return float(c["sim"]) - penalty

    winner = min(pool, key=lambda c: (-_score(c), int(c["seed"])))

    result: dict[str, Any] = {
        "text": text,
        "voice": str(voice_path),
        "seeds": seeds,
        "cache_dir": str(out_dir),
        "rendered": True,
        "cached": not missing,
        "candidates": rendered,
        "chosen": winner,
        "capped": [c["seed"] for c in ok if c.get("capped")],
        "plan": bool(plan),
        "written": False,
    }
    if lex_path is not None:
        result["lexicon"] = lex_path
    if say_text != text:
        result["say_text"] = say_text
    if readback and not plan:
        payload = asr.transcribe(winner["path"], model=SYNTH_READBACK_MODEL)
        heard = " ".join(seg["text"] for seg in payload.get("segments", [])).strip()
        result["heard"] = heard
        result["wer"] = round(_wer(_norm_words(_fold(text, lex)), _norm_words(_fold(heard, lex))), 3)
    if clip_id is not None and word_index is not None:
        splice = _splice_after(
            project,
            clip_id,
            word_index,
            lambda: Path(winner["path"]),
            float(winner["duration"]),
            plan=plan,
            placeholder=f"synth-{key[:8]}-s{winner['seed']}",
            register_as=f"synth-{key[:8]}-s{winner['seed']}",
        )
        result["splice"] = splice
        result["written"] = splice["written"]
    return result


#: Where a project keeps its A2 music bed (PLAN.md § The A2 music lane — the
#: design note). Read with `.get()` and additive, the `CANVAS_KEY`/`TAIL_KEY`
#: shape: absent means what every project written before this key existed
#: meant — no second audio track — so this is not a `SCHEMA_VERSION` bump.
#:
#: **No field in it is a timeline second or a frame count.** The cue is
#: `(clip_id, word_index_start, word_index_end | None)` addressed into the
#: transcript exactly like `cue_add` addresses picture, and duration is
#: derived at build time through `Edit.timeline_span` — never stored, and
#: never cached either, because A2's boundaries sit *inside* the film where
#: an earlier cut is always upstream of them (the note's argument 2: a cached
#: frame count next to a word-index cue is two facts that can disagree,
#: unlike a tail's `seconds`, which is safe only because nothing is upstream
#: of the end). `word_index_end` absent means "to the end of the timeline" —
#: the hold HISTORY.md § The three served answers settled on, made the
#: default. `fade_in`/`fade_out` are drawn as one entry-attached `volume`
#: filter over the bed's audible frames (`mlt._fade_level`) — dB keyframes,
#: measured, HISTORY.md § The A2 fades.
MUSIC_KEY = "music"


def _stored_music(project: Project) -> dict[str, Any] | None:
    """The project's music cue, resolved to its fields, or None for no bed.

    Validated on every read, `_stored_tail`'s discipline: a manifest edited
    by hand or carried over from a future proofcut gets a message naming the
    shape rather than a `KeyError` inside `_build_mlt`.
    """
    stored = project.read_manifest().get(MUSIC_KEY)
    if stored is None:
        return None
    if not isinstance(stored, dict):
        raise ProjectError(f"{project.manifest_path}'s {MUSIC_KEY!r} must be a JSON object")
    try:
        asset = str(stored["asset"])
        clip_id = str(stored["clip_id"])
        word_index_start = int(stored["word_index_start"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError(
            f"{project.manifest_path}'s {MUSIC_KEY!r} must hold at least "
            f"'asset', 'clip_id' and an integer 'word_index_start', not {stored!r}"
        ) from exc
    end = stored.get("word_index_end")
    try:
        passages = [
            {
                "asset": str(passage["asset"]),
                "word_index_start": int(passage["word_index_start"]),
                "src_in": float(passage.get("src_in", 0.0)),
                "crossfade": float(passage.get("crossfade", stored.get("crossfade", 0.0))),
                "rotate": [str(a) for a in passage.get("rotate", [])],
                **({"phrase_start": passage["phrase_start"]} if passage.get("phrase_start") else {}),
            }
            for passage in stored.get("passages", [])
        ]
        rotate = [str(a) for a in stored.get("rotate", [])]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ProjectError(
            f"{project.manifest_path}'s {MUSIC_KEY!r} passages must each hold an "
            f"'asset' and an integer 'word_index_start', and 'rotate' a list of clip ids — {exc}"
        ) from exc
    under = stored.get("under")
    duck_db = stored.get("duck")
    return {
        "asset": asset,
        "clip_id": clip_id,
        "word_index_start": word_index_start,
        "word_index_end": int(end) if end is not None else None,
        "fade_in": float(stored.get("fade_in", 0.0)),
        "fade_out": float(stored.get("fade_out", 0.0)),
        # Additive-optional, so a bed stored before passages existed reads as
        # exactly what it meant: one asset from its head at its own level.
        "src_in": float(stored.get("src_in", 0.0)),
        "crossfade": float(stored.get("crossfade", 0.0)),
        "rotate": rotate,
        "passages": passages,
        "under": float(under) if under is not None else None,
        "duck": float(duck_db) if duck_db is not None else None,
    }


def _music_assets(bed: dict[str, Any] | None) -> set[str]:
    """Every clip id a stored bed plays — its own asset, every passage's, and
    every rotation's — for the reads that ask whether a clip is in use."""
    if not bed:
        return set()
    assets = {bed.get("asset"), *bed.get("rotate", [])}
    for passage in bed.get("passages", []):
        assets.add(passage.get("asset"))
        assets.update(passage.get("rotate", []))
    assets.discard(None)
    return {str(a) for a in assets}


def music(
    path: Path | str,
    *,
    asset: str | None = None,
    clip_id: str | None = None,
    word_index_start: int | None = None,
    word_index_end: int | None = None,
    phrase_start: str | None = None,
    phrase_end: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    clear_end: bool = False,
    src_in: float | None = None,
    crossfade: float | None = None,
    rotate: list[str] | None = None,
    passages: list[dict[str, Any]] | None = None,
    under: float | None = None,
    clear_under: bool = False,
    duck: float | None = None,
    clear_duck: bool = False,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the A2 music bed this project mixes under its edit.

    **`duck` pulls the bed that many dB down while the voice is speaking** and
    lets it back up in the pauses — gated on the Edit's own audio at build
    time, never on the transcript's word durations (`duck.py`), so a cut moves
    it with nothing to refresh. It sits under whatever level `under` set: that
    is the bed's level in a pause. `clear_duck` returns the bed to one level.

    **A bed can be several passages, placed and levelled** (docs/plans/
    NATIVE.md § A1). `passages` replaces the list of passages after the bed's
    own asset, each `{asset, word_index_start | phrase_start, src_in?,
    crossfade?, rotate?}`: it starts at its word (a phrase resolves forward
    from the passage before it), plays its asset from `src_in`, and the
    passage before runs on past that word by `crossfade` seconds so the two
    overlap. `rotate` (on the bed, or on a passage) is further assets played
    in turn when the first runs out, overlapping by the bed's `crossfade` —
    Lambs/Longlegs' three calm passages tiled to the film. `src_in` is where
    the bed's own asset starts. `under` levels the whole bed that many LU below
    the VO, measured, the way a hold is; unset, every asset plays at its own
    level, which is what every bed before this meant. `passages=[]` and
    `rotate=[]` clear them; `clear_under` drops the level.

    PLAN.md § The A2 music lane — the design note, and the one op step 05 of
    the Studio reshape stopped for review over. Called with no arguments it
    changes nothing and reports what is in force, `tail`'s shape throughout:
    first set needs `asset`, `clip_id` and `word_index_start` (or
    `phrase_start`) together; either alone after that updates its own field;
    `reset` drops the bed entirely.

    **The cue stores word indices and an asset — never a length.** The bed
    starts where `word_index_start` of `clip_id` lands on the timeline and
    runs to where `word_index_end` ends — or, with no end word, to the end of
    the timeline: the single-pass "hold" the music-bed listen settled on,
    made the default. A cut before either boundary moves both automatically,
    because word indices are what survives a cut for free; the alternative —
    a stored duration — was measured losing (0.341s of drift and a splice on
    live material, HISTORY.md § The music bed, measured against a dumb
    control). `word_index_end` is cleared back to "to the end" with
    `clear_end`, since None already means "don't change this field".

    `phrase_start`/`phrase_end` resolve against `clip_id`'s transcript the
    same way as every other word-indexed tool here — the start binds a
    phrase's **first** word ("the bed starts where the phrase starts"), the
    end binds its **last** ("the bed ends where the phrase ends"). Each is
    independent: a call can set the start by phrase and the end by index, or
    vice versa, and the existing "either alone updates its own field" merge
    logic composes with this for free. The resolved phrase (if any) is stored
    alongside the word index it resolved to, additive-optional, so
    `cue_reresolve` can re-derive it after a re-record; setting the field by
    plain index instead clears whatever phrase was stored for it, since a
    raw index says the caller is no longer trusting the phrase to find it.

    `asset` is a registered clip_id — checked here, because a music cue with
    a typo'd asset would otherwise surface three calls later inside
    `_build_mlt` — and never a `card:name`: a held frame has no sound to mix.
    The asset plays from its own head; a bed shorter than its span pads out
    with real silence, one longer is trimmed by frame count, both by
    construction so the writer's declared lengths keep agreeing
    (`mlt.document`).

    A tail is *after* the timeline, so an unbounded bed ends where the
    `Edit` does and the end card holds over silence — a cue addresses moments
    inside the film, the same reason a tail is not a cue.

    `fade_in`/`fade_out` are seconds of fade drawn over the bed's *audible*
    span — entry-attached in the writer, so a fade-out ends where the music
    actually ends, before any trail silence — and a pair that outgrows the
    bed refuses at build time (a cut upstream can shrink the bed under
    them), reported as `music_error` in `timeline_view` and by name from
    `export`. `plan` resolves and validates without writing.
    """
    if reset and any(
        value is not None
        for value in (
            asset,
            clip_id,
            word_index_start,
            word_index_end,
            phrase_start,
            phrase_end,
            fade_in,
            fade_out,
        )
    ):
        raise ProjectError("pass fields to change, or `reset`, not both")
    if clear_end and (word_index_end is not None or phrase_end is not None):
        raise ProjectError(
            "pass `word_index_end`/`phrase_end` or `clear_end`, not both"
        )

    project = Project.open(path)
    stored = _stored_music(project)
    changing = clear_end or any(
        value is not None
        for value in (
            asset,
            clip_id,
            word_index_start,
            word_index_end,
            phrase_start,
            phrase_end,
            fade_in,
            fade_out,
            src_in,
            crossfade,
            rotate,
            passages,
            under,
            duck,
        )
    ) or clear_under or clear_duck

    if reset:
        state: dict[str, Any] | None = None
    elif not changing:
        state = stored
    else:
        base = stored or {}
        resolved_clip_id = clip_id if clip_id is not None else base.get("clip_id")

        resolved_start = word_index_start
        if phrase_start is not None:
            if resolved_clip_id is None:
                raise ProjectError(
                    "phrase_start needs a clip_id to resolve against — pass "
                    "clip_id together with phrase_start the first time a bed "
                    "is set"
                )
            resolved_start, _ = _resolve_word_or_phrase(
                _transcript(project, resolved_clip_id),
                word_index=None,
                phrase=phrase_start,
                after=after,
                occurrence=occurrence,
                edge="first",
            )

        resolved_end = word_index_end
        if phrase_end is not None:
            if resolved_clip_id is None:
                raise ProjectError(
                    "phrase_end needs a clip_id to resolve against — pass "
                    "clip_id together with phrase_end"
                )
            _, resolved_end = _resolve_word_or_phrase(
                _transcript(project, resolved_clip_id),
                word_index=None,
                phrase=phrase_end,
                after=after,
                occurrence=occurrence,
                edge="last",
            )

        merged: dict[str, Any] = {
            "asset": asset if asset is not None else base.get("asset"),
            "clip_id": resolved_clip_id,
            "word_index_start": (
                int(resolved_start) if resolved_start is not None else base.get("word_index_start")
            ),
            "word_index_end": (
                None
                if clear_end
                else int(resolved_end)
                if resolved_end is not None
                else base.get("word_index_end")
            ),
            "fade_in": float(fade_in) if fade_in is not None else base.get("fade_in", 0.0),
            "fade_out": float(fade_out) if fade_out is not None else base.get("fade_out", 0.0),
        }
        # A raw index invalidates a previously-stored phrase for that same
        # field — the caller is no longer trusting the phrase to find it.
        stored_phrase_start = (
            phrase_start if phrase_start is not None
            else (None if word_index_start is not None else base.get("phrase_start"))
        )
        stored_phrase_end = (
            phrase_end if phrase_end is not None
            else (None if (clear_end or word_index_end is not None) else base.get("phrase_end"))
        )
        if stored_phrase_start is not None:
            merged["phrase_start"] = stored_phrase_start
        if stored_phrase_end is not None:
            merged["phrase_end"] = stored_phrase_end

        # The arrangement fields ride only when set, so a bed that uses none of
        # them is stored exactly as it was before they existed.
        resolved_src_in = float(src_in) if src_in is not None else float(base.get("src_in", 0.0))
        resolved_crossfade = (
            float(crossfade) if crossfade is not None else float(base.get("crossfade", 0.0))
        )
        resolved_rotate = [str(a) for a in rotate] if rotate is not None else list(base.get("rotate", []))
        resolved_under = None if clear_under else (float(under) if under is not None else base.get("under"))
        if passages is not None:
            resolved_passages: list[dict[str, Any]] = []
            previous = int(merged["word_index_start"]) if merged["word_index_start"] is not None else -1
            for number, raw in enumerate(passages):
                if not isinstance(raw, dict) or "asset" not in raw:
                    raise ProjectError(f"music passage {number} needs an 'asset', not {raw!r}")
                passage: dict[str, Any] = {"asset": str(raw["asset"])}
                if raw.get("phrase_start") is not None:
                    if resolved_clip_id is None:
                        raise ProjectError("a passage phrase needs the bed's clip_id to resolve against")
                    word, _ = _resolve_word_or_phrase(
                        _transcript(project, resolved_clip_id),
                        word_index=None,
                        phrase=str(raw["phrase_start"]),
                        after=previous,
                        occurrence=raw.get("occurrence"),
                        edge="first",
                    )
                    passage["word_index_start"] = int(word)
                    passage["phrase_start"] = str(raw["phrase_start"])
                elif raw.get("word_index_start") is not None:
                    passage["word_index_start"] = int(raw["word_index_start"])
                else:
                    raise ProjectError(
                        f"music passage {number} ({raw['asset']!r}) needs a word_index_start or phrase_start"
                    )
                for key in ("src_in", "crossfade"):
                    if raw.get(key) is not None:
                        passage[key] = float(raw[key])
                if raw.get("rotate"):
                    passage["rotate"] = [str(a) for a in raw["rotate"]]
                previous = passage["word_index_start"]
                resolved_passages.append(passage)
        else:
            resolved_passages = [
                {k: v for k, v in passage.items() if not (k == "rotate" and not v)}
                for passage in base.get("passages", [])
            ]
        if resolved_src_in:
            merged["src_in"] = resolved_src_in
        if resolved_crossfade:
            merged["crossfade"] = resolved_crossfade
        if resolved_rotate:
            merged["rotate"] = resolved_rotate
        if resolved_passages:
            merged["passages"] = resolved_passages
        if resolved_under is not None:
            merged["under"] = resolved_under
        resolved_duck = None if clear_duck else (float(duck) if duck is not None else base.get("duck"))
        if resolved_duck is not None:
            # The fade floor is -60 dB; a duck at or past it is the bed gone
            # under every line, which is a hold's job and not a level.
            if not (math.isfinite(resolved_duck) and 0 < resolved_duck < -mlt.FADE_FLOOR_DB):
                raise ProjectError(
                    f"music duck is the dB the bed drops under the voice, above 0 and "
                    f"below {-mlt.FADE_FLOOR_DB}, not {resolved_duck!r} — clear_duck removes it"
                )
            merged["duck"] = resolved_duck

        if merged["asset"] is None or merged["clip_id"] is None or merged["word_index_start"] is None:
            raise ProjectError(
                "a music bed needs `asset`, `clip_id` and `word_index_start` "
                "(or `phrase_start`) set together the first time — there is "
                "no bed without music to play, a transcript to address, and "
                "a word to start on. Either alone after that updates its own "
                "field."
            )
        if str(merged["asset"]).startswith("card:"):
            raise ProjectError(
                f"music asset must be a clip_id, not {merged['asset']!r} — a held "
                "frame has no sound to mix"
            )
        if merged["fade_in"] < 0 or merged["fade_out"] < 0:
            raise ProjectError(
                f"music fades must not be negative, not "
                f"{merged['fade_in']!r}/{merged['fade_out']!r}"
            )
        arrangement = [merged.get("src_in", 0.0), merged.get("crossfade", 0.0)] + [
            passage.get(key, 0.0) for passage in merged.get("passages", []) for key in ("src_in", "crossfade")
        ]
        if any(value < 0 for value in arrangement):
            raise ProjectError("music src_in and crossfade must not be negative")
        for extra in _music_assets(merged) - {str(merged["asset"])}:
            if extra.startswith("card:"):
                raise ProjectError(
                    f"music asset must be a clip_id, not {extra!r} — a held frame has no sound to mix"
                )
            media.get_clip(project, extra)
        if merged["word_index_end"] is not None and merged["word_index_end"] < merged["word_index_start"]:
            raise ProjectError(
                f"music word_index_end ({merged['word_index_end']}) sits before "
                f"word_index_start ({merged['word_index_start']}) — the bed runs "
                "forward from its start word"
            )
        # A typo'd asset or clip_id fails here, with the known-ids message,
        # rather than three calls later inside `_build_mlt`.
        media.get_clip(project, str(merged["asset"]))
        state = merged

    write = (reset or changing) and not plan
    if write:
        manifest = project.read_manifest()
        if state is None:
            manifest.pop(MUSIC_KEY, None)
        else:
            manifest[MUSIC_KEY] = state
        project.write_manifest(manifest)

    # Anything taking a word index echoes the words it resolved to (CLAUDE.md)
    # — an index one past the intended phrase reads correctly on its own.
    start_word: dict[str, Any] | None = None
    end_word: dict[str, Any] | None = None
    passage_words: list[dict[str, Any]] = []
    if state is not None:
        parsed = _transcript(project, state["clip_id"])
        start_word = _cue_echo(parsed, state["word_index_start"])
        if state["word_index_end"] is not None:
            end_word = _cue_echo(parsed, state["word_index_end"])
        passage_words = [
            {"asset": passage["asset"], **_cue_echo(parsed, passage["word_index_start"])}
            for passage in state.get("passages", [])
        ]

    return {
        "project": str(project.root),
        "music": state,
        "start_word": start_word,
        "end_word": end_word,
        "passage_words": passage_words,
        "written": write,
        "reset": bool(reset),
        "plan": bool(plan),
    }


def _music_plan(
    project: Project, edit: tl.Edit, rate: float, *, edit_frames: int
) -> dict[str, Any] | None:
    """Resolve the music cue to the frame span the writer builds its lane at.

    The `build_shots`-shaped derivation the design note argues for over a
    cached field: `word_index_start`/`word_index_end` through
    `Edit.timeline_span`, live, every time — never stored, so no hook has to
    remember to refresh it. None with no bed; a bed that cannot resolve
    raises, and the two callers split that the picture lane's way — `export`
    refuses, `timeline_view` reports it as `music_error` for the front end
    to draw.

    `edit_frames` is the timeline's own frame total off
    `autoeditor.frame_layout` — never derived from `edit.duration` here,
    because each segment edge quantises on its own (CLAUDE.md) — and the
    resolved boundaries are clamped to it: a word ending at the timeline's
    last instant can round one frame past the layout's own sum.

    An orphaned boundary — the word a cut removed entirely — refuses by name,
    `build_shots`' policy: word-indexing keeps a cue valid across cuts, it
    does not keep the word on the timeline.
    """
    stored = _stored_music(project)
    if stored is None:
        return None

    parsed = _transcript(project, stored["clip_id"])
    start_echo = _cue_echo(parsed, stored["word_index_start"])
    span = edit.timeline_span(stored["clip_id"], start_echo["start"], start_echo["end"])
    if span is None:
        raise ProjectError(
            f"the music bed starts at {stored['clip_id']!r} word "
            f"{stored['word_index_start']} ({start_echo['text']!r}), which a cut "
            "removed from the timeline — move the start word or restore the "
            "material (music, or CLI `proofcut music`)"
        )
    start_seconds = span[0]

    to_end = stored["word_index_end"] is None
    if to_end:
        end_seconds = edit.duration
        end_frame = edit_frames
    else:
        end_echo = _cue_echo(parsed, stored["word_index_end"])
        end_span = edit.timeline_span(stored["clip_id"], end_echo["start"], end_echo["end"])
        if end_span is None:
            raise ProjectError(
                f"the music bed ends at {stored['clip_id']!r} word "
                f"{stored['word_index_end']} ({end_echo['text']!r}), which a cut "
                "removed from the timeline — move the end word, or clear it to "
                "run to the end (music clear_end)"
            )
        end_seconds = end_span[1]
        end_frame = min(round(end_seconds * rate), edit_frames)

    start_frame = min(round(start_seconds * rate), end_frame)
    if end_frame <= start_frame:
        raise ProjectError(
            f"the music bed resolves to zero frames — it starts at timeline "
            f"{start_seconds:.3f}s and ends at {end_seconds:.3f}s on the "
            f"{rate:g} fps grid"
        )

    # The passages: the bed's own asset first, then each stored passage from
    # the timeline position of its start word — a word index, never a stored
    # second, for the design note's measured reason. Each runs to where the
    # next one starts, plus that one's crossfade, or to the bed's end.
    starts: list[tuple[int, dict[str, Any]]] = [
        (
            start_frame,
            {
                "asset": stored["asset"],
                "src_in": stored["src_in"],
                "crossfade": 0.0,
                "rotate": stored["rotate"],
                "word_index_start": stored["word_index_start"],
            },
        )
    ]
    for passage in stored["passages"]:
        echo = _cue_echo(parsed, passage["word_index_start"])
        span = edit.timeline_span(stored["clip_id"], echo["start"], echo["end"])
        if span is None:
            raise ProjectError(
                f"a music passage ({passage['asset']!r}) starts at {stored['clip_id']!r} "
                f"word {passage['word_index_start']} ({echo['text']!r}), which a cut "
                "removed from the timeline — move the passage's start word"
            )
        frame = min(round(span[0] * rate), end_frame)
        if frame <= starts[-1][0]:
            raise ProjectError(
                f"music passage {passage['asset']!r} starts at word "
                f"{passage['word_index_start']} ({echo['text']!r}), not after the passage "
                "before it — passages run forward, each from its own start word"
            )
        starts.append((frame, passage))

    pieces = _music_pieces(project, stored, starts, end_frame, rate)
    covered: set[int] = set()
    for piece in pieces:
        covered.update(range(piece["start_frame"], piece["start_frame"] + piece["frames"]))
    span_frames = end_frame - start_frame
    music_frames = len(covered)
    first = pieces[0]

    return {
        **stored,
        # The first piece's, which for a bed with no passages is the bed.
        "asset_path": first["asset_path"],
        "start_frame": start_frame,
        "end_frame": end_frame,
        "timeline_start": start_seconds,
        "timeline_end": end_seconds,
        "to_end": to_end,
        # Frames something plays over, and frames of the span nothing does —
        # an asset shorter than its span pads with real silence, never melt's
        # un-checked padding (the note's resolution (b)).
        "music_frames": music_frames,
        "padded_frames": span_frames - music_frames,
        # What the writer will actually draw, in its own units, so the reply
        # and the view state the fade the render carries rather than the one
        # the manifest asked for.
        "fade_in_frames": first["fade_in_frames"],
        "fade_out_frames": pieces[-1]["fade_out_frames"],
        "pieces": pieces,
    }


def _music_pieces_view(pieces: list[dict[str, Any]], rate: float) -> list[dict[str, Any]]:
    """The pieces as a reply or a lane states them: in Edit seconds, no paths."""
    return [
        {
            "asset": piece["asset"],
            "passage": piece["passage"],
            "lane": piece["lane"],
            "timeline_start": piece["start_frame"] / rate,
            "timeline_end": (piece["start_frame"] + piece["frames"]) / rate,
            "src_in": piece["src_in_frames"] / rate,
            "fade_in": piece["fade_in_frames"] / rate,
            "fade_out": piece["fade_out_frames"] / rate,
        }
        for piece in pieces
    ]


def _music_pieces(
    project: Project,
    stored: dict[str, Any],
    starts: list[tuple[int, dict[str, Any]]],
    end_frame: int,
    rate: float,
) -> list[dict[str, Any]]:
    """Lay the bed's passages out as the pieces the writer plays, in Edit frames.

    A passage plays its asset from `src_in`; when the asset runs out before
    the passage does, a passage with `rotate` carries on with the next asset
    in its rotation, each from its head, overlapping the last by the bed's
    `crossfade` — goodsometimes `gen_longlegs_mix.py`'s tiling, three
    passages alternated to fill the body — and a passage without one pads
    with silence, today's single bed, because the listen settled that one
    looped cue loses (HISTORY.md § The three served answers). A passage's
    last piece runs on past the next passage's start by that passage's own
    `crossfade`, so the two overlap there; the writer puts overlapping pieces
    on two lanes.

    Every fade is sized here, and one that does not fit refuses by name, so
    `timeline_view` reports it as `music_error` and `export` refuses — a cut
    can shrink a passage under a crossfade that used to fit, and that is a
    decision, not something to clamp.
    """
    bed_crossfade = round(stored["crossfade"] * rate)
    pieces: list[dict[str, Any]] = []
    for index, (passage_start, passage) in enumerate(starts):
        if index + 1 < len(starts):
            next_start, next_passage = starts[index + 1]
            span_end = min(next_start + round(next_passage["crossfade"] * rate), end_frame)
        else:
            span_end = end_frame
        cycle = [passage["asset"], *passage["rotate"]]
        cursor, turn = passage_start, 0
        while cursor < span_end:
            asset = cycle[turn % len(cycle)]
            clip = media.get_clip(project, asset)
            duration = clip.get("duration")
            if not duration:
                raise ProjectError(
                    f"music asset {asset!r} has no known duration, so there is no way "
                    "to trim it to its span"
                )
            src_in = round((passage["src_in"] if turn == 0 else 0.0) * rate)
            available = round(float(duration) * rate) - src_in
            if available <= 0:
                raise ProjectError(
                    f"music asset {asset!r} starts at src {src_in / rate:.3f}s, past its "
                    f"own {float(duration):.3f}s"
                )
            frames = min(available, span_end - cursor)
            pieces.append(
                {
                    "asset": asset,
                    "asset_path": str(media.media_path(project, clip)),
                    "start_frame": cursor,
                    "frames": frames,
                    "src_in_frames": src_in,
                    "passage": index,
                }
            )
            if cursor + frames >= span_end or len(cycle) == 1:
                break
            if frames <= bed_crossfade:
                raise ProjectError(
                    f"music asset {asset!r} plays {frames / rate:.3f}s, no longer than the "
                    f"bed's {bed_crossfade / rate:.3f}s crossfade — a rotation has to move forward"
                )
            cursor += frames - bed_crossfade
            turn += 1

    # Fades: the bed's own at its two ends, a crossfade wherever two pieces
    # overlap, nothing where a piece runs out into silence.
    for index, piece in enumerate(pieces):
        piece_end = piece["start_frame"] + piece["frames"]
        before = pieces[index - 1] if index else None
        after = pieces[index + 1] if index + 1 < len(pieces) else None
        overlap_in = (before["start_frame"] + before["frames"] - piece["start_frame"]) if before else 0
        overlap_out = (piece_end - after["start_frame"]) if after else 0
        piece["fade_in_frames"] = max(overlap_in, 0) if before else round(stored["fade_in"] * rate)
        piece["fade_out_frames"] = max(overlap_out, 0) if after else round(stored["fade_out"] * rate)
        # An overlap is a crossfade, and takes the equal-power curve; the
        # bed's own ends fade to and from silence the way they always did.
        piece["crossfade_in"] = bool(before) and overlap_in > 0
        piece["crossfade_out"] = bool(after) and overlap_out > 0
        if piece["fade_in_frames"] + piece["fade_out_frames"] > max(piece["frames"] - 1, 0):
            raise ProjectError(
                f"the music fades on {piece['asset']!r} "
                f"({piece['fade_in_frames'] / rate:.3f}s in + {piece['fade_out_frames'] / rate:.3f}s out) "
                f"do not fit inside its audible {piece['frames'] / rate:.3f}s — shorten the "
                "fades or crossfades, or move the boundary words to lengthen it "
                "(music, or CLI `proofcut music`)"
            )

    # Two lanes: a piece goes on the first lane whose last piece has ended.
    lane_ends = [0, 0]
    for piece in pieces:
        lane = next((k for k in (0, 1) if lane_ends[k] <= piece["start_frame"]), None)
        if lane is None:
            raise ProjectError(
                f"music asset {piece['asset']!r} would overlap two pieces at once — a "
                "crossfade longer than the piece between them"
            )
        piece["lane"] = lane
        lane_ends[lane] = piece["start_frame"] + piece["frames"]
    return pieces


# -- film-audio holds ------------------------------------------------------
#
# A "hold" is `vo_extend`'s own mechanism (a real silence spliced into the VO
# track right after a word) plus a picture cue pinning a film clip's own
# in-point, tied together as one manifest record and one compound op — the
# fix for the Longlegs retro's own complaint: today the gap, the pin and the
# mix are three independently hand-maintained pieces (goodsometimes
# `assemble_longlegs.py`/`music_bed.py`/`verify_longlegs.py`), and the
# failure mode is exactly that they drift apart with nothing proofcut can see.
#
# `HOLDS_KEY` ties them: an address into the VO transcript (`clip_id`,
# `gap_word_index` — unique, `cue_add`'s own duplicate refusal), the picture
# cue it owns (`cue_word_index`, `asset`), and which of the asset's own words
# must be heard clean (`word_index_first`/`word_index_last`).
#
# **Everything derivable is derived live, never stored** — `_music_plan`'s
# own discipline, restated because it matters even more here: a later hold
# or cut can genuinely move an earlier hold's resolved numbers (`elapsed`,
# `src_start`, `hold_frames`, the gain), so caching any of them would not be
# an optimisation, it would be a way to go stale silently.
HOLDS_KEY = "holds"

#: goodsometimes' own numbers (`assemble_longlegs.py` HOLDS/`music_bed.py`),
#: each overridable per stored hold.
HOLD_HEAD_MARGIN = 0.15
HOLD_TAIL_MARGIN = 0.45
HOLD_UNDER = 0.0
HOLD_FADE_IN = 0.10
HOLD_FADE_OUT = 0.30
#: `music_bed.py --hold-fade`'s own default: the ramp either side of a span
#: the bed is gated silent across.
HOLD_GATE_RAMP = 0.7


def _stored_holds(project: Project) -> list[dict[str, Any]]:
    """Every stored hold, validated — `_stored_tail`'s discipline, for a list
    rather than a dict: a manifest edited by hand or carried over from a
    future proofcut gets a message naming the shape, not a `KeyError` three
    calls later inside `_build_mlt`."""
    stored = project.read_manifest().get(HOLDS_KEY, [])
    if not isinstance(stored, list):
        raise ProjectError(f"{project.manifest_path}'s {HOLDS_KEY!r} must be a JSON array")
    holds: list[dict[str, Any]] = []
    for item in stored:
        if not isinstance(item, dict):
            raise ProjectError(
                f"{project.manifest_path}'s {HOLDS_KEY!r} entries must be JSON objects"
            )
        try:
            holds.append(
                {
                    "clip_id": str(item["clip_id"]),
                    "gap_word_index": int(item["gap_word_index"]),
                    "cue_word_index": int(item["cue_word_index"]),
                    "asset": str(item["asset"]),
                    "word_index_first": int(item["word_index_first"]),
                    "word_index_last": int(item["word_index_last"]),
                    "head_margin": float(item.get("head_margin", HOLD_HEAD_MARGIN)),
                    "tail_margin": float(item.get("tail_margin", HOLD_TAIL_MARGIN)),
                    "under": float(item.get("under", HOLD_UNDER)),
                    "fade_in": float(item.get("fade_in", HOLD_FADE_IN)),
                    "fade_out": float(item.get("fade_out", HOLD_FADE_OUT)),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError(
                f"{project.manifest_path}'s {HOLDS_KEY!r} entry must hold at least "
                "'clip_id', 'gap_word_index', 'cue_word_index', 'asset', "
                f"'word_index_first' and 'word_index_last', not {item!r}"
            ) from exc
    return holds


def _hold_plan(
    project: Project, edit: tl.Edit, rate: float, stored_hold: dict[str, Any]
) -> dict[str, Any]:
    """Resolve one stored hold to the frame span the writer builds its lane
    at — `_music_plan`'s own shape, live against `edit` every time.

    1. `cue_at`/`gap_at` — where the cue word and the gap word's own end now
       sit on the timeline, `_splice_point`'s exact resolution for `gap_at`
       so this can never disagree with what a real splice would compute.
       **This is correct whether `edit` still holds the gap unspliced (the
       first `hold_add` call) or already holds it (every later read)** — the
       first-matching-segment walk finds the position right after the
       original material either way (CLAUDE.md § A cut cannot invalidate a
       cue's own precedent for word-indexing, restated for a splice point).
    2. `elapsed = gap_at - cue_at` — how long the VO plays between showing the
       cue and reaching the gap.
    3. The asset's own phrase (`word_index_first`/`word_index_last`), which
       needs a transcript to exist.
    4. `src_start = phrase_start - elapsed - head_margin` — the algebra that
       makes `play_at` (`src_start + elapsed`) land at exactly
       `phrase_start - head_margin`, deterministically, every time. **Refused,
       never clamped**, when `src_start < 0` — goodsometimes' own "pinned to
       0" shortcut is exactly the v5→v6 seam bug this feature exists to stop
       reproducing by hand.
    5. `hold_length = (phrase_end - phrase_start) + head_margin + tail_margin`
       — falls straight out of the algebra above, independent of `elapsed`.
    6. The asset must have `hold_length` seconds from `src_start` on —
       refused with the measured shortfall, `music_bed.py`'s own check.
    """
    clip_id = stored_hold["clip_id"]
    gap_word_index = stored_hold["gap_word_index"]
    cue_word_index = stored_hold["cue_word_index"]
    asset = stored_hold["asset"]
    head_margin = float(stored_hold.get("head_margin", HOLD_HEAD_MARGIN))
    tail_margin = float(stored_hold.get("tail_margin", HOLD_TAIL_MARGIN))

    parsed = _transcript(project, clip_id)
    cue_echo = _cue_echo(parsed, cue_word_index)
    gap_echo = _cue_echo(parsed, gap_word_index)

    cue_at = edit.timeline_time(clip_id, cue_echo["start"])
    if cue_at is None:
        raise ProjectError(
            f"this hold's cue word {cue_word_index} ({cue_echo['text']!r}) of "
            f"{clip_id!r} is not on the timeline (already cut) — move the cue "
            "word, or restore the material"
        )
    # `_splice_point`'s own resolution for the gap's own end, so a hold can
    # never disagree with what `vo_extend`/`_splice_after` would compute.
    gap_at = edit.timeline_time(clip_id, gap_echo["end"], closed_end=True)
    if gap_at is None:
        raise ProjectError(
            f"this hold's gap word {gap_word_index} ({gap_echo['text']!r}) of "
            f"{clip_id!r} is not on the timeline (already cut) — a hold opens "
            "after material that currently plays, and this word does not"
        )
    elapsed = gap_at - cue_at

    if not project.transcript_path(asset).exists():
        raise ProjectError(
            f"hold asset {asset!r} has no transcript — `proofcut transcribe {asset}` "
            "first, so the hold knows which of its own words must survive clean"
        )
    asset_transcript = _transcript(project, asset)
    phrase_start = _cue_echo(asset_transcript, int(stored_hold["word_index_first"]))["start"]
    phrase_end = _cue_echo(asset_transcript, int(stored_hold["word_index_last"]))["end"]

    src_start = phrase_start - elapsed - head_margin
    if src_start < 0:
        raise ProjectError(
            f"hold at {clip_id!r} word {gap_word_index} has no room: the cue "
            f"word is {elapsed:.3f}s ahead of the gap, {asset!r}'s own phrase "
            f"starts at {phrase_start:.3f}s, and a {head_margin:.3f}s head "
            f"margin needs {-src_start:.3f}s more than {asset!r} has before "
            "that point — re-cue this hold's cue_word_index later in the VO "
            "to shrink elapsed, or move word_index_first earlier in the "
            "asset's own line"
        )

    hold_length = (phrase_end - phrase_start) + head_margin + tail_margin
    play_at = src_start + elapsed

    asset_clip = media.get_clip(project, asset)
    asset_duration = asset_clip.get("duration")
    if asset_duration is None:
        raise ProjectError(f"hold asset {asset!r} has no known duration")
    remaining = float(asset_duration) - src_start
    if remaining < hold_length:
        raise ProjectError(
            f"hold at {clip_id!r} word {gap_word_index} wants {hold_length:.3f}s "
            f"from {src_start:.3f}s of {asset!r} but only {remaining:.3f}s "
            f"remain (short by {hold_length - remaining:.3f}s) — shorten the "
            "margins, move word_index_last earlier, or use a longer clip"
        )

    hold_frames = max(1, round(hold_length * rate))
    fade_in = float(stored_hold.get("fade_in", HOLD_FADE_IN))
    fade_out = float(stored_hold.get("fade_out", HOLD_FADE_OUT))
    fade_in_frames = round(fade_in * rate)
    fade_out_frames = round(fade_out * rate)
    if fade_in_frames + fade_out_frames > max(hold_frames - 1, 0):
        raise ProjectError(
            f"hold at {clip_id!r} word {gap_word_index}'s fades "
            f"({fade_in:g}s + {fade_out:g}s) do not fit inside its "
            f"{hold_length:.3f}s span — shorten them"
        )

    return {
        **stored_hold,
        "cue_at": cue_at,
        "gap_at": gap_at,
        "elapsed": elapsed,
        "src_start": src_start,
        "play_at": play_at,
        "hold_length": hold_length,
        "hold_frames": hold_frames,
        "phrase_start": phrase_start,
        "phrase_end": phrase_end,
        "fade_in_frames": fade_in_frames,
        "fade_out_frames": fade_out_frames,
        "asset_path": str(media.media_path(project, asset_clip)),
        # Namespaced, not splatted: `_splice_after`'s own gap-word echo
        # (`text`/`start`/`end`/`word_index`/`context_before`/`context_after`)
        # rides at the top level of `hold_add`'s reply, and splatting the
        # cue word's echo under the *same* key names would silently drop one
        # of the two — a caller reading `result["text"]` cannot tell which
        # word it named. "Both words' echoes" means both are reachable.
        "cue_echo": _cue_echo(parsed, cue_word_index),
    }


def _vo_loudness(project: Project, edit: tl.Edit) -> float:
    """Integrated loudness (LUFS) of the Edit's own *surviving* audio, all of
    it concatenated into one scratch file and measured once.

    **Not cached** — `_music_plan`'s own reasoning: which segments survive is
    live edit state, and a stored number would drift the first time
    something upstream is cut. One ffmpeg `loudnorm` analysis pass over the
    VO's own duration is the cost, audio-only and much faster than real time,
    paid once per export/plan call rather than per hold — every stored hold
    in one call shares this single measurement.

    Built from distinct source *files*, not per-segment: a segment's
    `resource` repeats across an edit with many cuts of one clip, and this
    opens each distinct file only once as an ffmpeg input, referencing it by
    stream index from as many `atrim` filters as it has segments — the
    `music_bed.py:bed_filtergraph` pattern, generalized from "one music track
    per cue" to "one input per distinct source".
    """
    if not edit.segments:
        raise ProjectError(
            "this timeline has no audio at all, so there is nothing to measure "
            "a hold's level against"
        )

    resource_index: dict[str, int] = {}
    inputs: list[str] = []
    parts: list[str] = []
    labels: list[str] = []
    for i, seg in enumerate(edit.segments):
        clip = media.get_clip(project, seg.clip_id)
        resource = str(media.media_path(project, clip))
        if resource not in resource_index:
            resource_index[resource] = len(inputs) // 2
            inputs += ["-i", resource]
        idx = resource_index[resource]
        parts.append(
            f"[{idx}:a]atrim=start={seg.start:.6f}:end={seg.end:.6f},"
            f"asetpts=PTS-STARTPTS[vh{i}]"
        )
        labels.append(f"[vh{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[voall]")

    work = picture.scratch("vo-loudness-")
    out = work / "vo.wav"
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(parts),
        "-map",
        "[voall]",
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return energy.integrated_loudness(out)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise ProjectError(
            f"could not build the VO's own audio to measure a hold's level "
            f"against: {detail}"
        ) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _duck_frames(
    project: Project, edit: tl.Edit, rate: float, *, edit_frames: int, depth_db: float, vo_lufs: float
) -> list[float]:
    """The bed's duck in dB on every frame of the Edit, keyed off the Edit's
    own audio (`duck.py`).

    Each segment's source audio is measured where `autoeditor.frame_layout`
    puts that segment — never at a running sum of float durations, which
    drifts off the rendered timeline by a frame or two over a film. Each
    distinct file is decoded once, at `energy.RATE`, however many segments
    read it. **Not cached, and export-only**: `_music_plan` runs on every
    `project-changed` and must never compose this in (CLAUDE.md, the
    `reframe_coverage` rule) — the decode is a second or two on a film.
    """
    blocks = [dk.FLOOR_DB] * (math.ceil(edit_frames / rate / dk.BLOCK) + 1)
    decoded: dict[str, Any] = {}
    cursor = 0
    for seg, (_, frames) in zip(edit.segments, autoeditor.frame_layout(edit, rate), strict=True):
        resource = str(media.media_path(project, media.get_clip(project, seg.clip_id)))
        if resource not in decoded:
            try:
                decoded[resource] = energy.decode(resource)
            except energy.EnergyError as exc:
                raise ProjectError(f"the music bed's duck could not read the timeline's audio: {exc}") from exc
        samples = decoded[resource]
        levels = dk.block_levels(
            samples[round(seg.start * energy.RATE) : round(seg.end * energy.RATE)], energy.RATE
        )
        at = round(cursor / rate / dk.BLOCK)
        blocks[at : at + len(levels)] = levels
        cursor += frames
    del blocks[math.ceil(edit_frames / rate / dk.BLOCK) + 1 :]
    envelope = dk.gate(blocks, threshold_db=vo_lufs + dk.THRESHOLD_LU, depth_db=depth_db)
    return dk.per_frame(envelope, rate=rate, frames=edit_frames)


def _hold_gain_db(vo_lufs: float, hold_lufs: float, under: float) -> float:
    """`music_bed.py`'s own formula (`gain = 10**((vo_i - under - seg_i)/20)`),
    in dB directly rather than a linear factor — the writer's `gain_db` takes
    dB natively, so there is no `10**(x/20)` round trip to get wrong."""
    return (vo_lufs - under) - hold_lufs


def _hold_gate_spans(
    project: Project, edit: tl.Edit, rate: float, head_frames: int
) -> list[tuple[int, int, dict[str, Any]]]:
    """Every stored hold, resolved and placed in *lane* frame coordinates —
    the same coordinate space `_build_mlt`'s music lead pad already uses
    (`head_frames` folded in), because a head prepends real frames before the
    Edit's own start and every lane built alongside it has to agree on where
    frame 0 actually is.

    Shared by the holds lane itself and `_gate_music_lane` — one hold list,
    resolved once, is what keeps "where a hold plays" and "where the bed
    goes silent" from ever being able to disagree.
    """
    resolved = []
    for stored_hold in _stored_holds(project):
        plan = _hold_plan(project, edit, rate, stored_hold)
        start_frame = head_frames + round(plan["gap_at"] * rate)
        resolved.append((start_frame, start_frame + plan["hold_frames"], plan))
    # Film audio under the VO shares the lane and the bed's gate (NATIVE.md § A2).
    for stored in _stored_under_vo(project):
        plan = _under_vo_plan(project, edit, rate, stored)
        start_frame = head_frames + round(plan["gap_at"] * rate)
        resolved.append((start_frame, start_frame + plan["hold_frames"], plan))
    resolved.sort(key=lambda item: item[0])

    def label(plan: dict[str, Any]) -> str:
        if plan.get("kind") == "under_vo":
            return f"film audio under the VO at {plan['clip_id']!r} word {plan['word_index_start']}"
        return f"hold at {plan['clip_id']!r} word {plan['gap_word_index']}"

    for (a_start, a_end, a_plan), (b_start, b_end, b_plan) in pairwise(resolved):
        if b_start < a_end:
            raise ProjectError(
                f"{label(a_plan)} and {label(b_plan)} "
                "overlap on the timeline — holds cannot stack"
            )
    return resolved


def _gate_music_lane(
    project: Project,
    music_lane: list[mlt.Entry],
    bed_resource: str | set[str],
    hold_spans: list[tuple[int, int]],
    rate: float,
) -> list[mlt.Entry]:
    """Split the bed's own audio entry at every hold span that intersects it,
    substituting real silence for the covered stretch with `HOLD_GATE_RAMP`
    fades either side — "**OUT**, not ducked": stacking score on cleared
    dialogue is what makes a Content ID claim messy to contest, because the
    disputed span stops isolating (goodsometimes `music_bed.py`'s own
    reasoning, ported). A no-op with no bed, or when no hold intersects it —
    `music_lane` comes back unchanged either way, so this composes for zero,
    one, or many holds against zero, one, or (eventually) many bed segments.

    Only the bed's own entry is ever split — lead/trail silence padding is
    already silent and needs no gating (identified by `resource`, since a
    caller building `music_lane` already knows which entry is the bed).
    """
    if not music_lane or not hold_spans:
        return music_lane
    bed_resources = {bed_resource} if isinstance(bed_resource, str) else bed_resource

    ramp = max(1, round(HOLD_GATE_RAMP * rate))
    out: list[mlt.Entry] = []
    offset = 0
    for entry in music_lane:
        entry_start, entry_end = offset, offset + entry.frames
        offset = entry_end
        if entry.resource not in bed_resources:
            out.append(entry)
            continue

        cuts = sorted(
            (max(lo, entry_start), min(hi, entry_end))
            for lo, hi in hold_spans
            if hi > entry_start and lo < entry_end
        )
        if not cuts:
            out.append(entry)
            continue

        cursor = entry_start
        for index, (lo, hi) in enumerate(cuts):
            if lo > cursor:
                seg_frames = lo - cursor
                # The entry's own configured fade only at its real edge; a
                # gate ramp everywhere a hold cuts it off. When a hold cuts a
                # piece off inside its own fade, the ramp takes that fade
                # over: the real-edge fade shrinks to what the segment holds.
                out_ramp = min(ramp, max(seg_frames - 1, 0))
                fade_in = (
                    min(entry.fade_in_frames, max(seg_frames - 1 - out_ramp, 0))
                    if cursor == entry_start
                    else min(ramp, max(seg_frames - 1 - out_ramp, 0))
                )
                out.append(
                    mlt.Entry(
                        entry.resource,
                        entry.src_in + (cursor - entry_start),
                        seg_frames,
                        has_video=entry.has_video,
                        fade_in_frames=fade_in,
                        fade_out_frames=out_ramp,
                        gain_db=entry.gain_db,
                        crossfade_in=entry.crossfade_in and cursor == entry_start,
                        gain_keys=mlt.slice_gain_keys(entry.gain_keys, cursor - entry_start, seg_frames),
                    )
                )
            silence = _tail_silence(project, (hi - lo) / rate)
            out.append(mlt.Entry(str(silence), 0, hi - lo, is_image=False, has_video=False))
            cursor = hi
        if cursor < entry_end:
            seg_frames = entry_end - cursor
            in_ramp = min(ramp, max(seg_frames - 1, 0))
            out.append(
                mlt.Entry(
                    entry.resource,
                    entry.src_in + (cursor - entry_start),
                    seg_frames,
                    has_video=entry.has_video,
                    fade_in_frames=in_ramp,
                    fade_out_frames=min(entry.fade_out_frames, max(seg_frames - 1 - in_ramp, 0)),
                    gain_db=entry.gain_db,
                    crossfade_out=entry.crossfade_out,
                    gain_keys=mlt.slice_gain_keys(entry.gain_keys, cursor - entry_start, seg_frames),
                )
            )
    return out


def hold_add(
    path: Path | str,
    clip_id: str,
    gap_word_index: int | None = None,
    cue_word_index: int | None = None,
    asset: str | None = None,
    word_index_first: int | None = None,
    word_index_last: int | None = None,
    *,
    gap_phrase: str | None = None,
    cue_phrase: str | None = None,
    asset_phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    head_margin: float | None = None,
    tail_margin: float | None = None,
    under: float | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Splice a hold into `clip_id` after `gap_word_index`: a real gap opens
    in the VO (`vo_extend`'s own mechanism, reused not reimplemented) and a
    picture cue pins `asset`'s own in-point so its clean audio and picture
    play across it — the fix for the three independently hand-maintained
    pieces the Longlegs retro named (this module's own docstring).

    Addressed by `(clip_id, gap_word_index)`, unique — a second `hold_add` at
    the same address is refused, `cue_add`'s own "remove it first" refusal
    (word-index addressing, not `hold_clip_id`: `_tail_silence` dedups
    purely on seconds, so two holds of equal length would collide on it).
    `gap_word_index`/`cue_word_index`/`word_index_first`/`word_index_last`
    each also accept a `*_phrase` alternative — `gap_phrase` binds its
    **last** word (`vo_extend`'s own meaning: "the last word before the
    gap"), `cue_phrase` binds its **first** (`cue_add`'s own meaning), and
    `asset_phrase` resolves against `asset`'s own transcript and binds its
    **first and last** words to `word_index_first`/`word_index_last`
    together — derived from the cue table, one source of truth, rather than
    four numbers copied out of a transcript by hand.

    Everything else is resolved live (`_hold_plan`): `elapsed` (how long the
    VO plays between the cue and the gap), `src_start` (deterministically —
    `phrase_start - elapsed - head_margin`, so `play_at` lands exactly at
    `phrase_start - head_margin` every time, never chosen by ear), and
    `hold_length` (the phrase's own span plus both margins). **Refused, never
    clamped**, when there is no room (`src_start < 0`) or the asset runs out
    (`hold_length` past its end) — both name the measured numbers.

    On success this performs the same two mutations the hand process
    performs by hand, atomically: `_splice_after` opens the gap (a real
    silent WAV, `vo_extend`'s own mechanism — the hold's *audible* film
    audio comes from the writer's own fourth lane at build time, never from
    widening a clip_id past its registered duration), and the picture cue at
    `cue_word_index` is written pointing at `asset` with the computed
    `src_start` — refused if that word already shows a *different* asset,
    since this call owns that cue.

    **Mix-only fields are re-settable without re-splicing**: call again for
    the same `(clip_id, gap_word_index)` with only `head_margin`/
    `tail_margin`/`under`/`fade_in`/`fade_out` changed (and
    `word_index_first`/`word_index_last`/`cue_word_index`/`cue_phrase`
    matching what is already stored) and the manifest record updates in
    place — `music()`'s own "either field alone updates its own field"
    shape. Changing `word_index_first`/`word_index_last` is refused: that
    changes `hold_length`, which would require re-splicing a gap this call
    cannot safely resize. Changing `cue_word_index`/`cue_phrase` is refused
    too — this call owns exactly one cue, at its stored address, and moving
    it would mean writing a second cue and leaving the old one's `src_start`
    stale rather than re-addressing it. There is no clean way to resize or
    re-address a hold once it is spliced — only `proofcut undo` (snapshot
    rollback) or `hold_rm` (which strands the gap as an ordinary
    manufactured silence, not a true removal) — `vo_extend`'s own one-way
    nature, inherited rather than introduced.

    `plan=True` resolves and reports without writing anything — not the
    timeline and not the manifest, `vo_extend`'s own rule.
    """
    project = Project.open(path)
    stored = _stored_holds(project)

    parsed = _transcript(project, clip_id)
    resolved_gap, _ = _resolve_word_or_phrase(
        parsed, word_index=gap_word_index, phrase=gap_phrase, after=after, occurrence=occurrence, edge="last"
    )
    resolved_cue, _ = _resolve_word_or_phrase(
        parsed, word_index=cue_word_index, phrase=cue_phrase, after=after, occurrence=occurrence, edge="first"
    )

    existing = next(
        (h for h in stored if h["clip_id"] == clip_id and h["gap_word_index"] == resolved_gap),
        None,
    )

    if asset_phrase is not None:
        if word_index_first is not None or word_index_last is not None:
            raise ProjectError(
                "pass asset_phrase or word_index_first/word_index_last, not both"
            )
        if asset is None:
            if existing is None:
                raise ProjectError("hold_add needs asset the first time a hold is set")
            asset = existing["asset"]
        asset_resolved = _resolve_word_or_phrase(
            _transcript(project, asset),
            word_index=None,
            phrase=asset_phrase,
            after=after,
            occurrence=occurrence,
            edge="range",
        )
        resolved_first, resolved_last = asset_resolved
    else:
        resolved_first = word_index_first
        resolved_last = word_index_last

    if existing is None:
        if asset is None or resolved_first is None or resolved_last is None:
            raise ProjectError(
                "a new hold needs asset, word_index_first (or asset_phrase) and "
                "word_index_last set together — there is no hold with nothing "
                "to show"
            )
        merged = {
            "clip_id": clip_id,
            "gap_word_index": resolved_gap,
            "cue_word_index": resolved_cue,
            "asset": asset,
            "word_index_first": int(resolved_first),
            "word_index_last": int(resolved_last),
            "head_margin": HOLD_HEAD_MARGIN if head_margin is None else float(head_margin),
            "tail_margin": HOLD_TAIL_MARGIN if tail_margin is None else float(tail_margin),
            "under": HOLD_UNDER if under is None else float(under),
            "fade_in": HOLD_FADE_IN if fade_in is None else float(fade_in),
            "fade_out": HOLD_FADE_OUT if fade_out is None else float(fade_out),
        }
        resplice = True
    else:
        merged = dict(existing)
        if asset is not None:
            merged["asset"] = asset
        if resolved_cue != existing["cue_word_index"]:
            raise ProjectError(
                f"hold at {clip_id!r} word {resolved_gap} is already spliced — "
                "cue_word_index cannot change without re-splicing, which this "
                "call cannot do safely. `hold_rm` then `hold_add` again, or "
                "`proofcut undo`"
            )
        if resolved_first is not None and int(resolved_first) != existing["word_index_first"]:
            raise ProjectError(
                f"hold at {clip_id!r} word {resolved_gap} is already spliced — "
                "word_index_first cannot change without re-splicing, which this "
                "call cannot do safely. `hold_rm` then `hold_add` again, or "
                "`proofcut undo`"
            )
        if resolved_last is not None and int(resolved_last) != existing["word_index_last"]:
            raise ProjectError(
                f"hold at {clip_id!r} word {resolved_gap} is already spliced — "
                "word_index_last cannot change without re-splicing, which this "
                "call cannot do safely. `hold_rm` then `hold_add` again, or "
                "`proofcut undo`"
            )
        for field, value in (
            ("head_margin", head_margin),
            ("tail_margin", tail_margin),
            ("under", under),
            ("fade_in", fade_in),
            ("fade_out", fade_out),
        ):
            if value is not None:
                merged[field] = float(value)
        resplice = False

    # Resolved (and, for a new hold, refused-with-measured-numbers) against
    # the *pre-splice* edit — `_hold_plan`'s own contract: correct whether
    # the gap is already open (a mix-only update) or not yet (a new hold).
    edit = _load_edit(project)
    rate = _rate(project)
    hold_plan = _hold_plan(project, edit, rate, merged)

    if not resplice:
        # A mix-only update: nothing on the timeline changes, only the
        # manifest record and (if the cue's own src_start moved because the
        # margins changed) the cue it owns.
        write = not plan
        if write:
            manifest = project.read_manifest()
            cues = manifest.setdefault("cues", [])
            for cue in cues:
                if cue["clip_id"] == clip_id and cue["word_index"] == resolved_cue:
                    cue["src_start"] = hold_plan["src_start"]
                    break
            holds = manifest.setdefault(HOLDS_KEY, [])
            for item in holds:
                if item["clip_id"] == clip_id and item["gap_word_index"] == resolved_gap:
                    item.clear()
                    item.update(merged)
                    break
            project.write_manifest(manifest)
        return {
            "clip_id": clip_id,
            "hold_clip_id": None,
            "written": write,
            "plan": bool(plan),
            "resplice": False,
            "covered_by": [],
            **hold_plan,
        }

    # A cue already at this exact word, for a *different* asset, is left
    # alone rather than silently overwritten — the hold owns its own cue,
    # not anyone else's.
    manifest = project.read_manifest()
    existing_cue = next(
        (
            c
            for c in manifest.get("cues", [])
            if c["clip_id"] == clip_id and c["word_index"] == resolved_cue
        ),
        None,
    )
    if existing_cue is not None and existing_cue["asset"] != merged["asset"]:
        raise ProjectError(
            f"word {resolved_cue} of {clip_id!r} already shows "
            f"{existing_cue['asset']!r}, not {merged['asset']!r} — remove that "
            "cue first, or point this hold's cue word elsewhere"
        )

    splice = _splice_after(
        project,
        clip_id,
        resolved_gap,
        lambda: _tail_silence(project, hold_plan["hold_length"]),
        hold_plan["hold_length"],
        plan=plan,
        placeholder=f"hold-{round(hold_plan['hold_length'] * 1000)}ms",
    )

    if not plan:
        manifest = project.read_manifest()
        cues = manifest.setdefault("cues", [])
        if existing_cue is None:
            cues.append(
                {"clip_id": clip_id, "word_index": resolved_cue, "asset": merged["asset"]}
            )
        for cue in cues:
            if cue["clip_id"] == clip_id and cue["word_index"] == resolved_cue:
                cue["asset"] = merged["asset"]
                cue["src_start"] = hold_plan["src_start"]
        cues.sort(key=lambda c: (c["clip_id"], c["word_index"]))
        holds = manifest.setdefault(HOLDS_KEY, [])
        holds.append(merged)
        holds.sort(key=lambda h: (h["clip_id"], h["gap_word_index"]))
        project.write_manifest(manifest)

    return {
        "clip_id": clip_id,
        "written": not plan,
        "plan": bool(plan),
        "resplice": True,
        **splice,
        **{k: v for k, v in hold_plan.items() if k not in splice},
    }


def hold_rm(path: Path | str, clip_id: str, gap_word_index: int) -> dict[str, Any]:
    """Drop a hold's record and its owned cue — the spliced silence stays.

    **The gap does not close.** `vo_extend`'s own irreversibility, inherited
    rather than introduced: there is no clean "un-splice" in this codebase,
    only `proofcut undo` (snapshot rollback). What this removes is the *meaning*
    of the gap — after this call it reverts to being an ordinary manufactured
    silence, which is a perfectly coherent, pre-existing state, not a broken
    one.
    """
    project = Project.open(path)
    manifest = project.read_manifest()
    holds = manifest.get(HOLDS_KEY, [])
    found = next(
        (h for h in holds if h["clip_id"] == clip_id and h["gap_word_index"] == gap_word_index),
        None,
    )
    if found is None:
        raise ProjectError(f"no hold at {clip_id!r} word {gap_word_index}")

    manifest[HOLDS_KEY] = [h for h in holds if h is not found]
    cues = manifest.get("cues", [])
    manifest["cues"] = [
        c
        for c in cues
        if not (c["clip_id"] == clip_id and c["word_index"] == found["cue_word_index"])
    ]
    project.write_manifest(manifest)
    return {"clip_id": clip_id, "gap_word_index": gap_word_index, "removed": found}


def _hold_cue_drift(
    cues_by_key: dict[tuple[str, int], dict[str, Any]],
    stored_hold: dict[str, Any],
    plan: dict[str, Any],
) -> str | None:
    """Compare a hold's owned cue (`(clip_id, cue_word_index)`) against what
    `_hold_plan` resolves fresh right now — `None` when they agree, a message
    naming the disagreement otherwise. Shared by `hold_ls` and `hold_check` so
    the retro's own "two lists drift apart in one edit" check has exactly one
    implementation, not two that can themselves drift apart.
    """
    owned_cue = cues_by_key.get((stored_hold["clip_id"], stored_hold["cue_word_index"]))
    if owned_cue is None:
        return "the owned cue no longer exists"
    if owned_cue.get("asset") != stored_hold["asset"]:
        return (
            f"the owned cue now shows {owned_cue.get('asset')!r}, not "
            f"{stored_hold['asset']!r}"
        )
    cue_src_start = owned_cue.get("src_start")
    plan_src_start = plan["src_start"]
    if cue_src_start is None or abs(float(cue_src_start) - plan_src_start) > 1e-3:
        return (
            f"the owned cue's src_start is {cue_src_start!r}, but this "
            f"hold resolves to {plan_src_start:.3f} now"
        )
    return None


def hold_ls(path: Path | str) -> dict[str, Any]:
    """Every stored hold plus its live-resolved plan — `shots_error`'s
    policy: a hold that cannot currently resolve is reported inline
    (`hold_error`), never raised, so one bad entry cannot break the list.

    Each item also carries a **drift check** against its own owned cue: the
    hold owns `cue_word_index`'s cue, but nothing stops a plain `cue_rm`/
    `cue_add` on that exact word from an unrelated caller — `cue_drift` names
    the disagreement (the cue's stored `src_start` against what `_hold_plan`
    would compute fresh right now) rather than silently trusting either, the
    retro's own "two lists drift apart in one edit, and the failure is
    inaudible" failure mode, now possible *inside* proofcut instead of between
    proofcut and a hand-typed table.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    rate = _rate(project)
    cues_by_key = {
        (c["clip_id"], c["word_index"]): c for c in project.read_manifest().get("cues", [])
    }

    items: list[dict[str, Any]] = []
    for stored_hold in _stored_holds(project):
        entry: dict[str, Any] = dict(stored_hold)
        try:
            plan = _hold_plan(project, edit, rate, stored_hold)
        except _PICTURE_REFUSALS as exc:
            entry["hold_error"] = str(exc)
            entry["cue_drift"] = None
            items.append(entry)
            continue
        entry.update(
            {
                k: v
                for k, v in plan.items()
                if k not in stored_hold
            }
        )
        entry["cue_drift"] = _hold_cue_drift(cues_by_key, stored_hold, plan)
        items.append(entry)

    under_vo: list[dict[str, Any]] = []
    for stored in _stored_under_vo(project):
        entry = dict(stored)
        try:
            resolved = _under_vo_plan(project, edit, rate, stored)
            entry.update(
                {
                    "timeline_start": resolved["gap_at"],
                    "timeline_end": resolved["gap_at"] + resolved["hold_length"],
                    "play_at": resolved["play_at"],
                    "start_word": resolved["start_word"],
                    "end_word": resolved["end_word"],
                }
            )
        except _PICTURE_REFUSALS as exc:
            entry["under_vo_error"] = str(exc)
        under_vo.append(entry)

    return {"project": str(project.root), "holds": items, "count": len(items), "under_vo": under_vo}


# -- film audio under the VO ---------------------------------------------------
#
# docs/plans/NATIVE.md § A2. A hold opens a gap and plays the film's line in
# it; this plays a film clip's own audio *under* the VO, across a span of VO
# words, `under` LU below it — Lambs/Longlegs' fairy-tale narration, which the
# essay talks over on purpose (goodsometimes `assemble_longlegs.py` FAIRY_TALE).
# No splice, and no in-point of its own: the picture already decides what is
# on screen, so the audio reads from wherever the shot showing `asset` has got
# to when the span starts — one source of truth, the `play_at` lesson applied
# before it could be got wrong. It rides the holds lane (holds sit in gaps,
# this sits under speech, so the two never overlap) and the bed goes out
# across it, `music_bed.py --film`'s own rule for every film cue.

UNDER_VO_KEY = "under_vo"
UNDER_VO_UNDER = 13.0


def _stored_under_vo(project: Project) -> list[dict[str, Any]]:
    stored = project.read_manifest().get(UNDER_VO_KEY, [])
    if not isinstance(stored, list):
        raise ProjectError(f"{project.manifest_path}'s {UNDER_VO_KEY!r} must be a JSON array")
    items: list[dict[str, Any]] = []
    for item in stored:
        try:
            items.append(
                {
                    "clip_id": str(item["clip_id"]),
                    "word_index_start": int(item["word_index_start"]),
                    "word_index_end": int(item["word_index_end"]),
                    "asset": str(item["asset"]),
                    "under": float(item.get("under", UNDER_VO_UNDER)),
                    "fade_in": float(item.get("fade_in", HOLD_FADE_IN)),
                    "fade_out": float(item.get("fade_out", HOLD_FADE_OUT)),
                    **{k: item[k] for k in ("phrase_start", "phrase_end") if item.get(k)},
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError(
                f"{project.manifest_path}'s {UNDER_VO_KEY!r} entry must hold 'clip_id', "
                f"'word_index_start', 'word_index_end' and 'asset', not {item!r}"
            ) from exc
    return items


def _under_vo_plan(
    project: Project, edit: tl.Edit, rate: float, stored: dict[str, Any]
) -> dict[str, Any]:
    """Resolve one under-VO span to what the holds lane plays — in the keys the
    lane's own loop reads (`gap_at`, `play_at`, `hold_length`, …), so it needs
    no second loop."""
    parsed = _transcript(project, stored["clip_id"])
    first = _cue_echo(parsed, stored["word_index_start"])
    last = _cue_echo(parsed, stored["word_index_end"])
    head = edit.timeline_span(stored["clip_id"], first["start"], first["end"])
    tail = edit.timeline_span(stored["clip_id"], last["start"], last["end"])
    if head is None or tail is None:
        gone = first if head is None else last
        raise ProjectError(
            f"film audio under the VO ({stored['asset']!r}) is addressed to "
            f"{stored['clip_id']!r} word {gone['word_index']} ({gone['text']!r}), which a cut "
            "removed from the timeline — move the span's words"
        )
    span_start, span_end = head[0], tail[1]
    if span_end <= span_start:
        raise ProjectError(
            f"film audio under the VO resolves to no time — word {stored['word_index_end']} "
            f"ends before word {stored['word_index_start']} starts"
        )
    shots, _ = _picture_plan(project, rate, edit=edit)
    shot = next(
        (
            s for s in shots
            if s["asset"] == stored["asset"] and s["start"] - 1 / rate <= span_start < s["start"] + s["duration"]
        ),
        None,
    )
    if shot is None:
        raise ProjectError(
            f"{stored['asset']!r} is not on screen at {first['text']!r} "
            f"({span_start:.3f}s) — film audio under the VO plays the shot that is showing, "
            "so cue the asset at or before the span's first word"
        )
    play_at = shot["src_start"] + (span_start - shot["start"])
    length = span_end - span_start
    clip = media.get_clip(project, stored["asset"])
    if clip.get("duration") and play_at + length > float(clip["duration"]) + 1 / rate:
        raise ProjectError(
            f"{stored['asset']!r} runs out {play_at + length - float(clip['duration']):.3f}s "
            "before the span under the VO does — end the span earlier or cue the shot earlier"
        )
    frames = max(1, round(length * rate))
    fade_in_frames = round(stored["fade_in"] * rate)
    fade_out_frames = round(stored["fade_out"] * rate)
    if fade_in_frames + fade_out_frames > frames - 1:
        raise ProjectError(
            f"the fades on {stored['asset']!r} under the VO do not fit its {length:.3f}s"
        )
    return {
        **stored,
        "kind": "under_vo",
        "gap_word_index": None,
        "cue_word_index": shot["word_index"],
        "asset_path": str(media.media_path(project, clip)),
        "src_start": shot["src_start"],
        "play_at": play_at,
        "gap_at": span_start,
        "hold_length": length,
        "hold_frames": frames,
        "fade_in_frames": fade_in_frames,
        "fade_out_frames": fade_out_frames,
        "start_word": first,
        "end_word": last,
    }


def hold_under(
    path: Path | str,
    clip_id: str,
    asset: str,
    *,
    word_index_start: int | None = None,
    word_index_end: int | None = None,
    phrase_start: str | None = None,
    phrase_end: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
    under: float | None = None,
    fade_in: float | None = None,
    fade_out: float | None = None,
    plan: bool = False,
) -> dict[str, Any]:
    """Play `asset`'s own audio under a span of `clip_id`'s VO, `under` LU below it.

    Addressed by `(clip_id, word_index_start)`; a second call at the same
    address replaces the entry. Resolved against the current edit and picture
    before anything is written, so an asset that is not on screen at the
    span's first word is refused rather than stored. `plan` resolves without
    writing. Echoes both boundary words with their neighbours.
    """
    project = Project.open(path)
    parsed = _transcript(project, clip_id)
    start, _ = _resolve_word_or_phrase(
        parsed, word_index=word_index_start, phrase=phrase_start, after=after, occurrence=occurrence, edge="first"
    )
    end_after = start - 1 if phrase_end is not None else after
    _, end = _resolve_word_or_phrase(
        parsed, word_index=word_index_end, phrase=phrase_end, after=end_after, occurrence=occurrence, edge="last"
    )
    if asset.startswith("card:"):
        raise ProjectError(f"{asset!r} is a card — a held frame has no audio to play under the VO")
    media.get_clip(project, asset)
    record: dict[str, Any] = {
        "clip_id": clip_id,
        "word_index_start": int(start),
        "word_index_end": int(end),
        "asset": asset,
        "under": UNDER_VO_UNDER if under is None else float(under),
        "fade_in": HOLD_FADE_IN if fade_in is None else float(fade_in),
        "fade_out": HOLD_FADE_OUT if fade_out is None else float(fade_out),
    }
    if phrase_start is not None:
        record["phrase_start"] = phrase_start
    if phrase_end is not None:
        record["phrase_end"] = phrase_end
    resolved = _under_vo_plan(project, _load_edit(project), _rate(project), record)

    if not plan:
        manifest = project.read_manifest()
        kept = [
            item for item in manifest.get(UNDER_VO_KEY, [])
            if not (item.get("clip_id") == clip_id and item.get("word_index_start") == record["word_index_start"])
        ]
        kept.append(record)
        kept.sort(key=lambda item: (item["clip_id"], item["word_index_start"]))
        manifest[UNDER_VO_KEY] = kept
        project.write_manifest(manifest)
    return {
        "project": str(project.root),
        "under_vo": record,
        "timeline_start": resolved["gap_at"],
        "timeline_end": resolved["gap_at"] + resolved["hold_length"],
        "play_at": resolved["play_at"],
        "start_word": resolved["start_word"],
        "end_word": resolved["end_word"],
        "written": not plan,
        "plan": bool(plan),
    }


def hold_under_rm(path: Path | str, clip_id: str, word_index_start: int) -> dict[str, Any]:
    """Drop the film audio under the VO addressed by `(clip_id, word_index_start)`."""
    project = Project.open(path)
    manifest = project.read_manifest()
    items = manifest.get(UNDER_VO_KEY, [])
    found = next(
        (i for i in items if i.get("clip_id") == clip_id and i.get("word_index_start") == int(word_index_start)),
        None,
    )
    if found is None:
        raise ProjectError(f"no film audio under the VO at {clip_id!r} word {word_index_start}")
    remaining = [i for i in items if i is not found]
    if remaining:
        manifest[UNDER_VO_KEY] = remaining
    else:
        manifest.pop(UNDER_VO_KEY, None)
    project.write_manifest(manifest)
    return {"clip_id": clip_id, "word_index_start": int(word_index_start), "removed": found}


def _transcribe_span(
    media_path: Path, start: float, length: float, *, model: str = asr.DEFAULT_MODEL
) -> str:
    """The words heard in `[start, start + length)` of `media_path` —
    `verify_longlegs.py:transcribe_span`'s own mechanism: cut the span with
    ffmpeg, run it through `asr.transcribe`, join the words. A cut, not a
    seek-and-limit inside whisper itself, because whisper has no span
    argument of its own.

    `model` defaults to `asr.DEFAULT_MODEL` — `hold_check`'s own choice,
    unaffected by this becoming keyword-optional. `finish_check` passes
    `asr.WINDOWED_MODEL` explicitly for both its per-hold spans and its
    boundary rechecks (pipeline.md's "believe the smaller model" is
    directional, not just a default value — WORK-ORDERS ruling: a bigger
    model can *clean up* the very disfluency a recheck exists to catch).
    """
    work = picture.scratch("hold-check-")
    try:
        clip = work / "span.wav"
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            str(media_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(clip),
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        payload = asr.transcribe(clip, model=model)
        # openai-whisper nests words under `segments[].words[]` and has no
        # top-level `words` — reading only that returned "" for every real
        # span, so `hold_check` never heard a hold. A flat `words[]` is still
        # read, and silence is "" rather than `parse_whisper`'s refusal.
        words = payload.get("words")
        if not isinstance(words, list):
            words = [w for segment in payload.get("segments") or [] for w in segment.get("words") or []]
        return " ".join((w.get("word") or w.get("text") or "").strip() for w in words).strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)


#: How many words at each end of a hold's phrase `_line_edges_heard` looks for.
#: Two, not one: whisper respells a name ("Longlegs" heard as "Long Legs"), and
#: on v10 of Lambs/Longlegs that was the first word of a hold heard correctly.
LINE_EDGE_WORDS = 2


def _line_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("-", " "))


def _line_edges_heard(phrase: str, heard: str) -> dict[str, bool]:
    """Whether the start and the end of a hold's line are in what was heard.

    The edges and not a similarity score, because a mis-placed hold keeps most
    of its line: measured on the 16 hold spans of Lambs/Longlegs v10 and its
    native rebuild, word recall ran 0.80–1.00 on the right placements and up to
    0.94 on a wrong one (point-taken, 1.9 s early, lost only "point taken"),
    while "one of the last two words heard" was true on all eight right and
    false on all eight wrong. The start is checked for the placement that is
    late instead. A report, not a threshold anything gates on.
    """
    want, got = _line_tokens(phrase), set(_line_tokens(heard))
    if not want:
        return {"start": True, "end": True}
    return {
        "start": any(w in got for w in want[:LINE_EDGE_WORDS]),
        "end": any(w in got for w in want[-LINE_EDGE_WORDS:]),
    }


def hold_check(path: Path | str, render: Path | str) -> dict[str, Any]:
    """Transcribe each hold's own span off a render and check its seams.

    `verify_longlegs.py`'s own check, ported and made general: this project's
    stored holds resolved against the *current* edit (correct post-splice,
    because `edit.timeline_time` on the current edit finds the position right
    after the original material regardless of what else moved around it
    elsewhere), each span cut from `render` and transcribed
    (`asr.transcribe`), plus `finish.hold_seams`'s level check at both edges.

    **Report, never refuse** — this is a post-hoc listening check on a render
    that already exists, `verify`'s and `film_check`'s own stance. A hold
    that cannot currently resolve is reported with `hold_error`, `hold_ls`'s
    own policy, rather than taking the whole check down.

    Each resolving item also carries `hold_ls`'s own **`cue_drift`** check
    (`_hold_cue_drift`, shared rather than reimplemented) — the render-facing
    half of the same guard, since `finish_report(holds=True)` calls this op,
    not `hold_ls`, and a drifted owned cue must not read as `faults: 0`
    here. A drift counts toward `faults` alongside a seam fault.

    **And `line_edges`, the check it did not have** — whether the start and
    the end of the hold's own phrase are in what was heard
    (`_line_edges_heard`). A missing edge is a fault: it is the placement
    that plays the wrong stretch of the film, which the seams cannot see.
    """
    project = Project.open(path)
    render_path = Path(render).expanduser()
    if not render_path.is_file():
        raise ProjectError(f"no such render: {render_path}")

    edit = _load_edit(project)
    rate = _rate(project)
    head_seconds = _head_seconds(project)
    cues_by_key = {
        (c["clip_id"], c["word_index"]): c for c in project.read_manifest().get("cues", [])
    }

    items: list[dict[str, Any]] = []
    marks: list[tuple[str, float]] = []
    mark_owner: list[dict[str, Any]] = []
    for stored_hold in _stored_holds(project):
        label = f"{stored_hold['clip_id']}#{stored_hold['gap_word_index']}"
        entry: dict[str, Any] = {
            "clip_id": stored_hold["clip_id"],
            "gap_word_index": stored_hold["gap_word_index"],
            "asset": stored_hold["asset"],
        }
        try:
            plan = _hold_plan(project, edit, rate, stored_hold)
        except _PICTURE_REFUSALS as exc:
            entry["hold_error"] = str(exc)
            entry["cue_drift"] = None
            items.append(entry)
            continue

        entry["cue_drift"] = _hold_cue_drift(cues_by_key, stored_hold, plan)

        render_start = plan["gap_at"] + head_seconds
        render_end = render_start + plan["hold_length"]
        entry["render_start"] = render_start
        entry["render_end"] = render_end
        try:
            entry["heard"] = _transcribe_span(render_path, render_start, plan["hold_length"])
        except (asr.ASRError, subprocess.CalledProcessError) as exc:
            entry["heard"] = None
            entry["heard_error"] = str(exc)

        try:
            asset_parsed = _transcript(project, stored_hold["asset"])
            entry["phrase"] = " ".join(
                w.text
                for w in asset_parsed.words
                if stored_hold["word_index_first"] <= w.index <= stored_hold["word_index_last"]
            )
        except tx.TranscriptError:
            entry["phrase"] = None
        if entry["heard"] is not None and entry["phrase"]:
            entry["line_edges"] = _line_edges_heard(entry["phrase"], entry["heard"])

        marks.append((f"{label} in", render_start))
        marks.append((f"{label} out", render_end))
        mark_owner.append(entry)
        mark_owner.append(entry)
        items.append(entry)

    if marks:
        seams = finish.hold_seams(render_path, marks)
        for owner, seam in zip(mark_owner, seams, strict=True):
            owner.setdefault("seams", []).append(seam)

    faults = sum(
        1
        for item in items
        for seam in item.get("seams", [])
        if seam["fault"] is not None
    ) + sum(1 for item in items if item.get("cue_drift") is not None) + sum(
        1 for item in items if "line_edges" in item and not all(item["line_edges"].values())
    )
    return {
        "project": str(project.root),
        "render": str(render_path),
        "holds": items,
        "count": len(items),
        "faults": faults,
    }


def _resolved_hold_spans(
    project: Project, edit: tl.Edit, rate: float, head_seconds: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """This project's stored holds, resolved live to `finish_check`'s own
    `{"name", "start", "length", "ducked"}` shape — WORK-ORDERS ruling 5's
    fallback for a caller that passes no `holds` of its own.

    `start`/`start + length` land in *`final`'s own absolute seconds*
    (`head_seconds` folded into `gap_at`, `hold_check`'s own `render_start`
    arithmetic) — the same coordinate space an explicit `holds` argument is
    documented to use, so the two are interchangeable to every caller
    downstream.

    **Always `ducked=False`.** A proofcut hold always splices a real silence
    into the VO first (`_hold_plan`), so its own audio *replaces* the VO
    across the gap rather than playing under it — goodsometimes' `@UNDER`
    concept (a cue that ducks rather than pauses) has no proofcut-native
    equivalent to inherit, and every stored hold's edges are genuine seams.

    A hold that cannot currently resolve is reported in a second list
    (`hold_ls`'s own policy) rather than raised, so one bad stored hold
    cannot take the rest of `finish_check` down with it.
    """
    spans: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for stored_hold in _stored_holds(project):
        try:
            plan = _hold_plan(project, edit, rate, stored_hold)
        except _PICTURE_REFUSALS as exc:
            errors.append(
                {
                    "clip_id": stored_hold["clip_id"],
                    "gap_word_index": stored_hold["gap_word_index"],
                    "hold_error": str(exc),
                }
            )
            continue
        spans.append(
            {
                "name": f"{stored_hold['clip_id']}#{stored_hold['gap_word_index']}",
                "start": plan["gap_at"] + head_seconds,
                "length": plan["hold_length"],
                "ducked": False,
            }
        )
    return spans, errors


def _is_layered(project: Project, edit: tl.Edit) -> bool:
    """Does this timeline need the MLT writer?

    Seven ways to get there and they hit the same wall: a cue table lays
    picture over the edit, an edit naming two clips already holds two `src`
    files, a canvas override names a shape auto-editor can only letterbox
    into, a tail names a second and third resource (the card, the silence)
    auto-editor has no export for at all, a music bed asks for a second audio
    track auto-editor has no more concept of than it has of the tail, a head
    names a resource prepended before the `src` file itself, and a hold names
    a fourth audio-only track for a film clip's own clean sound — the same
    wall the music trigger hits, one lane over. auto-editor 31.x refuses to
    *export* a multi-source timeline (exit 2) and *renders* one at 720x576
    with exit 0 (CLAUDE.md); it would take a canvas override and quietly
    ignore it, which is the same failure wearing a different hat. All seven
    route through the MLT writer — and every trigger must never lag the
    writer's own lanes, or a project with a bed, a cold open or a hold
    recorded exports through auto-editor and the render comes back without
    it, at exit 0, invisible to every check but listening (PLAN.md § The A2
    music lane, the gate). The holds trigger is belt-and-suspenders here — a
    real hold always splices a second `clip_id` into the edit, which the
    first check below already catches — kept anyway on the same "never lag
    the writer" discipline every other trigger here follows, rather than
    trusting one path to cover a case it happens to cover today.
    """
    if len({segment.clip_id for segment in edit.segments}) > 1:
        return True
    manifest = project.read_manifest()
    return bool(
        manifest.get("cues")
        or manifest.get(CANVAS_KEY)
        or manifest.get(TAIL_KEY)
        or manifest.get(MUSIC_KEY)
        or manifest.get(HEAD_KEY)
        or manifest.get(HOLDS_KEY)
        # Film audio under the VO is a lane auto-editor has no export for —
        # recorded but routed there, the render comes back without it at exit 0.
        or manifest.get(UNDER_VO_KEY)
    )


def _mlt_resolution(project: Project) -> tuple[int, int]:
    """The canvas to declare in the MLT profile."""
    return _stored_canvas(project) or _footage_resolution(project)


def _build_mlt(project: Project, edit: tl.Edit, *, fps: float | None) -> dict[str, Any]:
    """The MLT document for this timeline, plus the facts it was built from.

    The frame grid is settled once, here, and everything downstream is handed
    it: the edit's entries come from `autoeditor.frame_layout` and the picture
    lane from `build_shots(fps=rate)`, so the two are quantised on the same
    grid by construction rather than by agreeing afterwards. `mlt.document`
    then refuses the pair if they still do not sum to the same total.

    Shared by the two things that can be done with a multi-source timeline —
    handing it to an NLE (`_export_mlt`) and rendering it (`_render_mlt`) — so
    that what gets rendered is the same document that would have been exported,
    rather than a second construction of it.
    """
    rate = float(fps) if fps else _export_fps(_clips_by_id(project))
    audio = []
    # Which clip each file on the timeline is, so a reframe stored against a
    # clip_id reaches the resource the writer keys its nodes on. Built from the
    # entries rather than from the manifest so an unused (or missing) clip
    # cannot be resolved on the way past.
    clip_of: dict[str, str] = {}
    for segment, (offset, frames) in zip(edit.segments, autoeditor.frame_layout(edit, rate)):
        clip = media.get_clip(project, segment.clip_id)
        resource = str(media.media_path(project, clip))
        clip_of[resource] = segment.clip_id
        audio.append(
            mlt.Entry(
                resource=resource,
                src_in=offset,
                frames=frames,
                has_video=bool(clip.get("has_video")),
            )
        )

    shots, lane = _picture_plan(project, rate)
    for shot in shots:
        if not shot["is_image"]:
            clip_of[shot["asset_path"]] = shot["asset"]

    # A tail is two ordinary entries appended after everything above — the
    # card on the picture lane, a silent WAV of the same length on the audio
    # track — added here rather than taught to `build_shots`/`_picture_plan`,
    # because a cue addresses a moment *inside* the film and a tail is after
    # it (PLAN.md § Tail time — the design note; CLAUDE.md "the completion
    # queue"). `lane`'s existing frames already sum to exactly `audio`'s (that
    # is what every prior `mlt.document` call on this project has already
    # required), so appending the same `tail_frames` to both keeps the
    # lane-covers-track invariant true by construction — nothing below has to
    # relax it.
    # The Edit's own frame total, off the layout the entries above were built
    # from — the music bed's boundaries resolve against this, *before* the
    # tail is appended: a cue addresses moments inside the film and a tail is
    # after it, so an unbounded bed ends where the `Edit` does and the end
    # card holds over silence.
    edit_frames = sum(entry.frames for entry in audio)

    # A head is the mirror of a tail at the other end: two ordinary entries
    # *prepended* rather than appended, before the Edit's own segments are
    # ever reached — a real clip on the audio track (with its own fades and
    # a flat `gain_db`) and its matching picture on the lane, muted the
    # ordinary way every picture-lane entry already is (`audio_index=-1`,
    # the writer's own per-role node split — no new muting logic needed
    # here). Prepending keeps the lane-covers-track invariant true by
    # construction, `tail`'s own reasoning run in reverse: `lane` and
    # `audio` grow by the same `head_frames` on the same end. `head_frames`
    # is read below by the music lane's lead-silence pad, which has to move
    # by exactly this much once `audio`'s own index 0 stops being the
    # Edit's own start.
    head_report: dict[str, Any] | None = None
    head_frames = 0
    head_cfg = _stored_head(project)
    if head_cfg is not None:
        if not lane:
            raise ProjectError(
                "this project has a head but no picture cue lane to hang the "
                "clip on — a head needs an existing cue table (cue_add) "
                "covering the whole film, because `mlt.document` requires the "
                "picture lane to cover the audio track exactly whenever one "
                "exists, and a project whose picture comes straight off its "
                "own clip has no second lane a head could join without "
                "duplicating the entire film onto one just to make room for "
                "the first few seconds"
            )
        head_resolved = _resolve_asset(project, head_cfg["asset"])
        if head_resolved["is_image"]:
            raise ProjectError(
                f"head asset {head_cfg['asset']!r} resolved to a card, not a "
                "clip — `head()` itself already refuses this, so a manifest "
                "edited by hand is the only way here"
            )
        head_frames = _head_frames(project, rate)
        head_src_in = round(head_cfg["src_start"] * rate)
        head_fade_in_frames = round(head_cfg["fade_in"] * rate)
        head_fade_out_frames = round(head_cfg["fade_out"] * rate)
        audio = [
            mlt.Entry(
                head_resolved["asset_path"],
                head_src_in,
                head_frames,
                has_video=True,
                fade_in_frames=head_fade_in_frames,
                fade_out_frames=head_fade_out_frames,
                gain_db=head_cfg["gain_db"],
            ),
            *audio,
        ]
        lane = [
            mlt.Entry(head_resolved["asset_path"], head_src_in, head_frames, has_video=True),
            *lane,
        ]
        clip_of[head_resolved["asset_path"]] = head_cfg["asset"]
        head_report = {**head_cfg, "frames": head_frames}

    tail_report: dict[str, Any] | None = None
    tail = _stored_tail(project)
    if tail is not None:
        if not lane:
            raise ProjectError(
                "this project has a tail but no picture cue lane to hang the "
                "card on — a tail needs an existing cue table (cue_add) "
                "covering the whole film, because `mlt.document` requires the "
                "picture lane to cover the audio track exactly whenever one "
                "exists, and a project whose picture comes straight off its "
                "own clip has no second lane a card could join without "
                "duplicating the entire film onto one just to make room for "
                "the last few seconds"
            )
        card = _resolve_asset(project, tail["asset"])
        if not card["is_image"]:
            raise ProjectError(f"tail asset {tail['asset']!r} resolved to a clip, not a card")
        tail_frames = _tail_frames(project, rate)
        silence = _tail_silence(project, tail["seconds"])
        audio.append(mlt.Entry(str(silence), 0, tail_frames, is_image=False, has_video=False))
        lane.append(mlt.Entry(card["asset_path"], 0, tail_frames, is_image=True, has_video=True))
        tail_report = {**tail, "frames": tail_frames}

    # The A2 music lane: the resolved bed plus real silent entries padding it
    # to the document's exact frame total — lead silence for a bed starting
    # mid-film, trail silence past where its own content (or its span) ends,
    # the tail's frames included. Pad/trim by construction, never melt's
    # un-checked blank-padding, so `mlt.document`'s declared-length check
    # needs no exception (PLAN.md § The A2 music lane, resolution (b)). An
    # offset is a real silent producer entry, never a `<blank>`.
    music_report: dict[str, Any] | None = None
    music_lane: list[mlt.Entry] = []
    music2_lane: list[mlt.Entry] = []
    music_resources: set[str] = set()
    music_plan = _music_plan(project, edit, rate, edit_frames=edit_frames)
    if music_plan is not None:
        total_frames = sum(entry.frames for entry in audio)
        # A piece's `start_frame` is resolved against the Edit's own frames
        # and knows nothing of a head — it cannot, a head is not part of the
        # `Edit`. But `audio`'s own index 0 is no longer the Edit's start once
        # a head has been prepended, so every lane position grows by
        # `head_frames`, or the bed plays `head_frames` too early — directly
        # on top of the cold open, at exit 0, invisible to `mlt.document`'s
        # own checks (which verify each lane's total, never its alignment).
        level_db = 0.0
        vo_lufs = _vo_loudness(project, edit) if music_plan["under"] is not None or music_plan["duck"] else None
        if music_plan["under"] is not None:
            # One gain for the whole bed, `music_bed.py`'s own rule: the bed's
            # loudness is the duration-weighted power mean of what each piece
            # plays, landed `under` LU below the VO the way a hold is levelled.
            powers, weights = 0.0, 0
            for piece in music_plan["pieces"]:
                start = piece["src_in_frames"] / rate
                lufs = energy.integrated_loudness(
                    piece["asset_path"], start=start, end=start + piece["frames"] / rate
                )
                powers += piece["frames"] * 10 ** (lufs / 10)
                weights += piece["frames"]
            bed_lufs = 10 * math.log10(powers / weights)
            level_db = round(vo_lufs - music_plan["under"] - bed_lufs, 2)
        duck_frames = (
            _duck_frames(
                project, edit, rate, edit_frames=edit_frames, depth_db=music_plan["duck"], vo_lufs=vo_lufs
            )
            if music_plan["duck"]
            else None
        )
        ducked_frames = 0
        duck_keys = 0
        lanes: list[list[mlt.Entry]] = [music_lane, music2_lane]
        cursors = [0, 0]
        for piece in music_plan["pieces"]:
            bed_lane, at = lanes[piece["lane"]], head_frames + piece["start_frame"]
            gap = at - cursors[piece["lane"]]
            if gap:
                silence = _tail_silence(project, gap / rate)
                bed_lane.append(mlt.Entry(str(silence), 0, gap, is_image=False, has_video=False))
            # Keyed in Edit frames, where the envelope was measured; the head
            # offset is the lane's business, not the envelope's.
            gain_keys = (
                dk.keys_for(duck_frames, piece["start_frame"], piece["frames"]) if duck_frames is not None else ()
            )
            if duck_frames is not None:
                ducked_frames += sum(
                    1
                    for frame in range(piece["start_frame"], piece["start_frame"] + piece["frames"])
                    if frame < len(duck_frames) and duck_frames[frame] <= dk.DUCKED_DB
                )
                duck_keys += len(gain_keys)
            bed_lane.append(
                mlt.Entry(
                    piece["asset_path"],
                    piece["src_in_frames"],
                    piece["frames"],
                    has_video=False,
                    gain_keys=gain_keys,
                    # Entry-attached, so the fades land on the piece's own
                    # first and last audible frames however much silence pads it.
                    fade_in_frames=piece["fade_in_frames"],
                    fade_out_frames=piece["fade_out_frames"],
                    gain_db=level_db,
                    crossfade_in=piece["crossfade_in"],
                    crossfade_out=piece["crossfade_out"],
                )
            )
            music_resources.add(piece["asset_path"])
            cursors[piece["lane"]] = at + piece["frames"]
        # `bed_lane`, never `lane`: that name is the picture lane's, and
        # rebinding it here handed the picture lane the bed's second lane
        for index, bed_lane in enumerate(lanes):
            if not bed_lane:
                continue
            trail = total_frames - cursors[index]
            if trail:
                silence = _tail_silence(project, trail / rate)
                bed_lane.append(mlt.Entry(str(silence), 0, trail, is_image=False, has_video=False))
        # Deliberately NOT added to `clip_of`: a reframe crops what is on
        # screen, and nothing of the music lane is — its nodes never take one.
        music_report = {
            key: music_plan[key]
            for key in (
                "asset",
                "clip_id",
                "word_index_start",
                "word_index_end",
                "timeline_start",
                "timeline_end",
                "to_end",
                "music_frames",
                "padded_frames",
                "fade_in",
                "fade_out",
                "fade_in_frames",
                "fade_out_frames",
                "under",
            )
        }
        music_report["level_db"] = level_db
        # What the render carries, not what the manifest asked: the depth, the
        # level the gate opened at, how much of the bed it pulled down (frames
        # of pieces, so a crossfade's overlap counts twice) and the keys drawn.
        music_report["duck"] = (
            {
                "depth_db": music_plan["duck"],
                "threshold_lufs": round(vo_lufs + dk.THRESHOLD_LU, 2),
                "ducked_seconds": round(ducked_frames / rate, 2),
                "keys": duck_keys,
            }
            if duck_frames is not None
            else None
        )
        music_report["pieces"] = _music_pieces_view(music_plan["pieces"], rate)

    # The holds lane: a fourth, audio-only lane, each stored hold's own
    # resolved film-clip span sitting at exactly the frame it plays, real
    # silence everywhere else — `_hold_gate_spans` resolves every hold once,
    # in the same lane coordinates (`head_frames` folded in) both this lane
    # and the bed's own gating below need to agree on, or "where a hold
    # plays" and "where the bed goes silent" could disagree.
    holds_report: list[dict[str, Any]] = []
    holds_lane: list[mlt.Entry] = []
    hold_spans = _hold_gate_spans(project, edit, rate, head_frames)
    if hold_spans:
        total_frames = sum(entry.frames for entry in audio)
        # One loudness pass over the whole VO, shared across every hold in
        # this build rather than re-measured per hold (`_vo_loudness`'s own
        # cost note).
        vo_lufs = _vo_loudness(project, edit)
        cursor = 0
        for start_frame, end_frame, hold_plan in hold_spans:
            if start_frame > cursor:
                gap = start_frame - cursor
                silence = _tail_silence(project, gap / rate)
                holds_lane.append(
                    mlt.Entry(str(silence), 0, gap, is_image=False, has_video=False)
                )
            # `play_at`, never `src_start`: the cue shows the clip from
            # `src_start`, and by the gap it has played `elapsed` further — the
            # line is at `play_at`. Read from `src_start`, every hold on the
            # Lambs/Longlegs native rebuild played the seconds before its line.
            hold_lufs = energy.integrated_loudness(
                hold_plan["asset_path"],
                start=hold_plan["play_at"],
                end=hold_plan["play_at"] + hold_plan["hold_length"],
            )
            level_db = _hold_gain_db(vo_lufs, hold_lufs, hold_plan.get("under", HOLD_UNDER))
            holds_lane.append(
                mlt.Entry(
                    hold_plan["asset_path"],
                    round(hold_plan["play_at"] * rate),
                    hold_plan["hold_frames"],
                    has_video=True,
                    fade_in_frames=hold_plan["fade_in_frames"],
                    fade_out_frames=hold_plan["fade_out_frames"],
                    gain_db=level_db,
                )
            )
            cursor = end_frame
            holds_report.append(
                {
                    "kind": hold_plan.get("kind", "hold"),
                    "clip_id": hold_plan["clip_id"],
                    "gap_word_index": hold_plan["gap_word_index"],
                    "cue_word_index": hold_plan["cue_word_index"],
                    "asset": hold_plan["asset"],
                    "src_start": hold_plan["src_start"],
                    "play_at": hold_plan["play_at"],
                    "hold_frames": hold_plan["hold_frames"],
                    "level_db": level_db,
                    "timeline_start": hold_plan["gap_at"],
                    "lane_start_frame": start_frame,
                }
            )
        if cursor < total_frames:
            trail = total_frames - cursor
            silence = _tail_silence(project, trail / rate)
            holds_lane.append(mlt.Entry(str(silence), 0, trail, is_image=False, has_video=False))

        # The bed goes **OUT, not ducked**, across every hold span — stacking
        # score on cleared dialogue is a Content ID problem, not a loudness
        # preference (this module's own docstring). Resolved after the holds
        # lane itself so the two can never disagree about where a hold plays.
        gate_spans = [(start, end) for start, end, _ in hold_spans]
        if music_lane:
            music_lane = _gate_music_lane(project, music_lane, music_resources, gate_spans, rate)
        if music2_lane:
            music2_lane = _gate_music_lane(project, music2_lane, music_resources, gate_spans, rate)

    resolution = _mlt_resolution(project)
    by_clip = _reframe_map(project, resolution)
    reframes = {
        resource: by_clip[clip] for resource, clip in clip_of.items() if clip in by_clip
    }
    document = mlt.document(
        audio=audio,
        picture=lane,
        music=music_lane,
        music2=music2_lane,
        holds=holds_lane,
        rate=rate,
        resolution=resolution,
        reframe=reframes,
        name=project.read_manifest().get("name") or project.root.name,
    )
    return {
        "document": document,
        "rate": rate,
        "resolution": resolution,
        "shots": shots,
        "frames": sum(entry.frames for entry in audio),
        "sources": len(
            {entry.resource for entry in [*audio, *lane, *music_lane, *music2_lane, *holds_lane]}
        ),
        # None with no head, or the resolved config plus the frames it
        # added — `tail`'s own echo shape, mirrored at the other end.
        "head": head_report,
        "tail": tail_report,
        # None with no bed, or the resolved cue plus the frames its asset
        # actually plays — the rest of its lane is silence padding.
        "music": music_report,
        # [] with no holds, or one entry per stored hold that resolved —
        # `music`'s own reasoning, reported on both export roads so a caller
        # sees the render actually carried each hold rather than trusting
        # the manifest key alone.
        "holds": holds_report,
        # What the render will actually crop, named where the render is built
        # rather than left for a pixel probe to discover.
        "reframed": sorted(
            clip
            for resource, clip in clip_of.items()
            if resource in reframes and not reframes[resource].is_identity(resolution)
        ),
    }


def _mlt_reply(built: dict[str, Any], edit: tl.Edit, **extra: Any) -> dict[str, Any]:
    """The fields both multi-source roads report, so they cannot drift apart."""
    return {
        "writer": extra.pop("writer"),
        # The shape the document was actually built at, said out loud because
        # a preset can now claim one (`PRESET_ASPECT`) and a reply that only
        # echoes the preset name proves nothing about what got written.
        "canvas": "{}x{}".format(*built["resolution"]),
        "timebase": built["rate"],
        "segments": len(edit.segments),
        "shots": len(built["shots"]),
        "sources": built["sources"],
        "frames": built["frames"],
        # None with no head, or the resolved config plus the frames it added
        # — `tail`'s own echo, mirrored at the other end of the film.
        "head": built["head"],
        # None with no tail, or the resolved config plus the frames it added
        # — `frames` above already includes them, this is what accounts for
        # the difference from `autoeditor.frame_total` alone.
        "tail": built["tail"],
        # None with no music bed, or the resolved cue — reported on both
        # roads because a bed recorded but not rendered is the exact silent
        # failure the `_is_layered` trigger exists to prevent, and the reply
        # is where a caller sees the render actually carried it.
        "music": built["music"],
        # [] with no holds, or one entry per resolved hold — the exact
        # `music` reasoning: a hold recorded but not rendered is the silent
        # failure `_is_layered`'s trigger exists to prevent.
        "holds": built["holds"],
        # Named on both roads because a crop is a decision about what is on
        # screen, and the render that made it looks entirely plausible.
        "reframed": built["reframed"],
        "timeline_duration": edit.duration,
        **extra,
    }


def _export_mlt(
    project: Project,
    edit: tl.Edit,
    output: Path | str,
    *,
    export_format: str | None,
    fps: float | None,
    preset: str | None = None,
    consumer_args: tuple[str, ...] = picture.RENDER_ARGS,
) -> dict[str, Any]:
    """Write the multi-source timeline as MLT — step 4 of the layered timeline."""
    if export_format is None:
        return _render_mlt(project, edit, output, fps=fps, preset=preset, consumer_args=consumer_args)
    if export_format not in MLT_EXPORT_FORMATS:
        raise ProjectError(
            f"this timeline has more than one source, so proofcut writes it itself, "
            f"and what it writes is MLT — {export_format!r} would have to go "
            "through auto-editor, whose exporter refuses a second source (exit 2). "
            f"Ask for one of {sorted(MLT_EXPORT_FORMATS)}."
        )

    built = _build_mlt(project, edit, fps=fps)
    written = mlt.write(built["document"], output)
    return _mlt_reply(
        built, edit, writer="mlt", output=str(written), format=export_format, preset=preset
    )


def _render_mlt(
    project: Project,
    edit: tl.Edit,
    output: Path | str,
    *,
    fps: float | None,
    preset: str | None = None,
    consumer_args: tuple[str, ...] = picture.RENDER_ARGS,
) -> dict[str, Any]:
    """Render the multi-source timeline through `melt` — step 5.

    auto-editor never sees this timeline: it degrades a two-source render to
    720x576 and exits 0 (CLAUDE.md), which is a file that looks like a success.
    `melt` has no source-count gate — it rendered the real 23-source Scream
    assembly at 1920x1080 (PLAN.md § The layered timeline).

    Three things this owes a reader, in the order they happen:

    * **The document goes under `$HOME`**, via `picture.scratch()`. melt runs
      from a flatpak that cannot see the host's `/tmp` and exits 0 having read
      nothing, so a project written to a temp dir would render silence.
    * **melt is asked what it would render before anything is encoded.**
      `project_frames` resolves the document without encoding a frame, and
      exact agreement there is what made 68 cut positions trustworthy before a
      render existed (HISTORY.md § 3). Disagreement refuses here rather than
      spending the encode to discover it.
    * **The finished file is measured, not believed** — `picture.render` does
      that, and only copies a render that agrees into place.

    The scratch directory survives a failure on purpose: the document melt was
    given is the evidence for what it did with it.
    """
    built = _build_mlt(project, edit, fps=fps)
    expected = built["frames"]
    work = picture.scratch("timeline-")
    project_file = mlt.write(built["document"], work / "timeline.mlt")

    declared = picture.project_frames(project_file)
    if declared != expected:
        raise ProjectError(
            f"melt reads {project_file} as {declared} frames where the timeline is "
            f"{expected} — refusing to spend an encode on a document that already "
            "disagrees with the edit. melt renders to the longest declared length "
            "it finds, so the render would have been that long too, and exited 0."
        )

    rendered = picture.render(
        project_file,
        output,
        expect_frames=expected,
        expect_resolution=built["resolution"],
        expect_duration=expected / built["rate"],
        consumer_args=consumer_args,
    )
    shutil.rmtree(work, ignore_errors=True)
    return _mlt_reply(
        built,
        edit,
        writer="melt",
        output=rendered["output"],
        format="media",
        melt_frames=declared,
        rendered=rendered,
        preset=preset,
    )


def _render_single(
    payload: dict[str, Any],
    output: Path | str,
    *,
    export_format: str | None,
    render_args: list[str],
    resolution: tuple[int, int] | None,
) -> tuple[Path, dict[str, Any]]:
    """Render (or export) a single-source v3 timeline through auto-editor.

    When `resolution` was explicitly requested, this owes the same discipline
    `picture.render` already applies on the melt path: auto-editor's exit
    code proves nothing (CLAUDE.md — the 31.x multi-source degrade exits 0 at
    720x576), so the render is staged, the staged file is probed, and it is
    copied to `output` only if it agrees — reusing `picture.render_problems`
    to decide agreement, the same predicate the melt path already trusts.
    A disagreement raises and leaves the staged file where it landed, for the
    same reason `picture.render` does: the evidence is the file, not the exit
    status.

    `resolution=None` (the common case — no preset, or a preset with no
    resolution) skips staging entirely and writes straight to `output`,
    unchanged from before this function existed. `render_args` is only
    forwarded when it is non-empty, for the same reason — a call this makes
    with nothing new to ask for is byte-identical to the call `export()` made
    before `render_args` existed.
    """
    extra_args: dict[str, Any] = {"render_args": render_args} if render_args else {}
    if resolution is None:
        written = autoeditor.run_timeline(payload, output, export=export_format, **extra_args)
        return written, {}

    work = Path(tempfile.mkdtemp(prefix="proofcut-render-"))
    staged = work / (Path(output).name or "render.mp4")
    written = autoeditor.run_timeline(payload, staged, export=export_format, **extra_args)

    measured = media.probe(written).as_dict()
    problems = picture.render_problems(measured, expect_resolution=resolution)
    if problems:
        raise ProjectError(
            f"the render disagrees with the resolution it was asked for, so it "
            f"has not been copied to {output}. It is at {written}, kept so the "
            "numbers can be checked against it:\n- " + "\n- ".join(problems)
        )

    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(written, destination)
    shutil.rmtree(work, ignore_errors=True)

    notes: list[str] = []
    extra: dict[str, Any] = {}
    if measured.get("has_video"):
        extra["resolution"] = [measured["width"], measured["height"]]
    else:
        extra["resolution"] = None
        notes.append("this render has no video stream — the requested resolution did not apply")
    extra["notes"] = notes
    return destination, extra


def _log_render(
    project: Project,
    *,
    output: str,
    preset: str | None,
    stages: dict[str, dict[str, Any]],
    continues: str | None = None,
) -> None:
    """Record a CLI/MCP render in the same log the web UI's pipeline writes.

    The web UI knows a whole run at once and calls `renderlog.append` itself;
    these two ops are each one stage of a render an agent assembles by hand,
    so they `amend` (renderlog.py's own note on the two writers). Called only
    after the stage actually succeeded — a stage that raised has no outcome to
    record here, and the caller's exception is the report.

    `expected_duration` is read now rather than passed in: it is "what the
    project claims its own finished length is", the number `check_frames` and
    `verify` measure a render against, and it is read at the same point in the
    render the web UI reads it.
    """
    renderlog.amend(
        project,
        output=output,
        preset=preset,
        expected_duration=status(str(project.root))["expected_duration"],
        stages=stages,
        continues=continues,
    )


def export(
    path: Path | str,
    output: Path | str,
    *,
    export_format: str | None = "kdenlive",
    fps: float | None = None,
    preset: str | None = None,
    resolution: tuple[int, int] | None = None,
    log: bool = True,
    loudness: float | None = None,
    true_peak: float = -1.0,
) -> dict[str, Any]:
    """Map the timeline to auto-editor v3 and render or export it.

    **`loudness` masters the render** to that many LUFS integrated under a
    `true_peak` ceiling (dBTP) — `finish.master_loudness`, two-pass, measured
    after and refused rather than kept when it misses — and the reply's
    `loudness` says what it measured before and after. Render only; an NLE
    project has no audio of its own to master. docs/plans/NATIVE.md § A3.

    `export_format="kdenlive"` writes an MLT project — the only handoff that
    actually opens on this box. `export_format=None` renders media instead.

    The two paths deliberately use different timebases. Rendering keeps the
    project's own rate, which for audio is milliseconds. An **NLE export must
    not**: the v3 timebase becomes the MLT `<profile frame_rate_num>`, so a
    millisecond timeline hands Kdenlive a 1000fps project. NLE timelines are
    frame-based, quantising to frames on the way out is what PLAN.md already
    wanted, and cut points land in inter-word silence where a 33ms grid is
    irrelevant.

    **A multi-source project takes a different road entirely** (steps 4 and 5
    of the layered timeline): proofcut generates the MLT itself, through `mlt`,
    and renders it with `melt`, because auto-editor refuses to export more than
    one `src` (exit 2) and degrades the render to 720x576 with exit 0. The
    choice is made from the project, not from a flag — a cue table or a second
    clip on the timeline *is* a multi-source timeline, and there is no
    combination of arguments that should route one through the path that
    silently ruins it. A `canvas` override joins them for the same reason:
    auto-editor would take the export and ignore the canvas, which is the same
    silent wrong output wearing a different hat. A configured `tail` joins
    them too, for a third reason with the same shape: auto-editor has no
    export for the second and third resources a card and its silence are, and
    the alternative is the defect this key exists to fix, a finishing pass
    that only ever existed downstream of `export` and vanished at exit 0 on
    every re-cut (PLAN.md § Tail time — the design note). The reply says
    which road was taken: `"writer"` is `"auto-editor"`, `"mlt"`, or `"melt"`,
    and `"tail"` (on the `mlt`/`melt` roads) is null with no tail or the
    resolved config plus the frames it added.

    `preset` names one of `EXPORT_PRESETS` (`"youtube"`, `"web"`,
    `"tiktok-reels"`) or `"custom"` (which requires `resolution`) — a bundle
    of the same four consumer keys `picture.RENDER_ARGS` already hardcodes on
    the melt path, and of auto-editor's own quality flags on the single-source
    path. `resolution` sets a `WIDTH,HEIGHT` output size on the single-source
    path only — **it letterboxes the existing 16:9 frame, it does not crop or
    reframe it**, so it is not a substitute for a vertical/9:16 export.

    **`"tiktok-reels"` checks the project's shape and never sets it**
    (`PRESET_ASPECT`, PLAN.md § Aspect swap step 5). A preset whose name is a
    claim about geometry refuses a canvas that contradicts it, naming the
    `canvas` command that fixes it — because the alternative, an export flag
    reshaping the project on the way past, is the same silent wrong output as
    picking the writer from an argument. Its four encode values are
    `"youtube"`'s: both platforms re-encode the upload, so the source wants
    the best quality the measured-safe keys can say. The reply's `"canvas"` is
    the shape the render was actually built at, on either road.
    Neither `preset` nor `resolution` may be combined with a non-`None`
    `export_format` — an NLE project file has no bitrate to set. `resolution`
    on a layered (multi-source) project is refused outright: widening the
    melt consumer to accept it was not re-isolated as memory-safe after
    HISTORY.md § 4's growth to 14.6 GB, so a single-source project is the
    workaround for now. When `resolution` was honoured, the reply's
    `"resolution"` is the *measured* size the finished file actually has —
    checked against the exit code proving nothing, same discipline as the
    melt path — and is `None` with a `"notes"` entry on an audio-only render,
    where a requested resolution has nothing to apply to.

    **A media render records itself in the render log** (`renderlog`), so
    `finish_report` can answer `captions.burned` for a render made from the
    CLI or the MCP server rather than reporting `"unknown"` on every one of
    them. `log=False` is for the web UI's `RenderJob`, which runs this as one
    stage of a pipeline it logs whole itself — two records of the same render
    would leave `last` reading a prefix of the run instead of the run. An NLE
    export writes nothing either way: there is no render to have burned
    anything into.
    """
    if (preset is not None or resolution is not None) and export_format is not None:
        raise ProjectError(
            "preset/resolution set the encode of rendered media — an NLE "
            f"handoff ({export_format!r}) writes a project file, which has no "
            "bitrate or pixel size of its own. Pass export_format=None to "
            "render, or drop preset/resolution to export the project as-is."
        )
    if loudness is not None and export_format is not None:
        raise ProjectError(
            "loudness masters rendered media — an NLE handoff "
            f"({export_format!r}) writes a project file with no audio of its own. "
            "Pass export_format=None to render, or drop loudness."
        )
    bundle = _resolve_preset(preset, resolution)

    project = Project.open(path)
    _check_preset_canvas(project, preset)
    edit = _load_edit(project)
    if not edit.segments:
        raise ProjectError("the timeline is empty — nothing to export")

    clips = _clips_by_id(project)
    if _is_layered(project, edit):
        if resolution is not None:
            raise ProjectError(
                "this timeline has more than one source, so it renders through "
                "melt, and melt's consumer is deliberately hardcoded to the "
                "codec and nothing else — adding width/height to it is what "
                "correlated with unbounded memory growth to 14.6 GB and froze "
                "the machine (HISTORY.md § 4), and no one has since isolated "
                "resolution as safe on its own. Render a single-source project "
                "if you need a specific resolution, or drop `resolution` and "
                "export at the project's own picture size."
            )
        reply = _export_mlt(
            project,
            edit,
            output,
            export_format=export_format,
            fps=fps,
            preset=preset,
            consumer_args=_melt_consumer_args(bundle),
        )
        if loudness is not None:
            reply["loudness"] = finish.master_loudness(
                reply["output"], integrated=loudness, true_peak=true_peak
            )
        if export_format is None and log:
            _log_render(
                project,
                output=reply["output"],
                preset=preset,
                stages={"export": {"outcome": "done", "detail": None}},
            )
        return reply

    primary = clips[edit.segments[0].clip_id]
    header = autoeditor.template(media.media_path(project, primary))

    if export_format is None:
        timebase = _rate(project)
    else:
        timebase = float(fps) if fps else _export_fps(clips)
    # `to_v3` reads each entry's "src" straight off the record it is given —
    # resolve every clip through media_path() here so an attenuated copy
    # (or a NAS symlink fallback) is what actually gets rendered/exported,
    # not the raw, immutable `source` field.
    resolved_clips = {
        clip_id: {**record, "source": str(media.media_path(project, record))}
        for clip_id, record in clips.items()
    }
    payload = autoeditor.to_v3(edit, resolved_clips, header=header, timebase=timebase)

    # A preset's flags are all video-encoding flags (`-c:v`/`-crf`/`-preset`)
    # plus `-c:a` — meaningless, and on some containers (a .wav destination
    # forcing `-c:a aac`, verified live) outright fatal, on a project with no
    # picture. Skip them there rather than let auto-editor fail on a
    # combination nobody asked for; `_render_single` still reports the
    # documented no-op note when `resolution` was requested.
    has_picture = bool(payload.get("v"))
    render_args = (
        _autoeditor_render_args(bundle, resolution)
        if export_format is None and has_picture
        else []
    )
    written, extra = _render_single(
        payload,
        output,
        export_format=export_format,
        render_args=render_args,
        resolution=resolution if export_format is None else None,
    )
    reply = {
        "output": str(written),
        "format": export_format or "media",
        "writer": "auto-editor",
        # The project's own shape, which is what this road renders — `-res`
        # letterboxes on top of it and is reported separately as
        # `"resolution"`, measured off the finished file rather than asked for.
        "canvas": "{}x{}".format(*_mlt_resolution(project)),
        "timebase": timebase,
        "segments": len(edit.segments),
        "timeline_duration": edit.duration,
        "preset": preset,
        **extra,
    }
    if loudness is not None:
        reply["loudness"] = finish.master_loudness(reply["output"], integrated=loudness, true_peak=true_peak)
    if export_format is None and log:
        _log_render(
            project,
            output=reply["output"],
            preset=preset,
            stages={"export": {"outcome": "done", "detail": None}},
        )
    return reply


def _transcripts_for(project: Project, clip_id: str | None) -> dict[str, tx.Transcript]:
    """The transcripts to caption from: one named clip, or every cached one."""
    if clip_id is not None:
        media.get_clip(project, clip_id)
        return {clip_id: _transcript(project, clip_id)}

    found: dict[str, tx.Transcript] = {}
    for clip in project.read_manifest().get("clips", []):
        cached = project.transcript_path(clip["clip_id"])
        if cached.exists():
            found[clip["clip_id"]] = tx.load(cached, clip_id=clip["clip_id"])
    if not found:
        raise tx.TranscriptError(
            "no clip in this project has a transcript — attach one with "
            "`proofcut attach-transcript <clip_id> <whisper.json>` first"
        )
    return found


def _caption_canvas(project: Project) -> tuple[int, int]:
    """The reference canvas for captions: the picture's shape, not its size.

    Reads the project's canvas override before the footage, because the two
    derivations of this fact have to move together — sizes and margins quoted
    against a 16:9 reference and burned into a 9:16 render stretch the
    glyphs, which is what `captions.canvas` exists to prevent.
    """
    override = _stored_canvas(project)
    if override is not None:
        return captions.canvas(*override)
    for clip in project.read_manifest().get("clips", []):
        if clip.get("has_video"):
            return captions.canvas(clip.get("width"), clip.get("height"))
    return captions.DEFAULT_RESOLUTION


#: Where a project keeps its caption look. A manifest key rather than a new
#: file, and read with `.get()` rather than behind a schema bump: a project
#: written before this existed is not wrong, it is unstyled, and bumping
#: `SCHEMA_VERSION` for an additive key would make `Project.open` refuse every
#: existing project to gain nothing.
CAPTION_STYLE_KEY = "caption_style"


def _stored_caption_style(project: Project) -> dict[str, Any]:
    stored = project.read_manifest().get(CAPTION_STYLE_KEY)
    if stored is None:
        return {}
    if not isinstance(stored, dict):
        raise captions.CaptionError(
            f"{project.manifest_path}'s {CAPTION_STYLE_KEY!r} must be a JSON object"
        )
    return stored


#: Words the transcript holds that the recording never said — whisper reading
#: across a retake splice and emitting both takes interleaved (HISTORY.md § The
#: hand-framed teaser, watched). Additive and optional the way `CANVAS_KEY` and
#: `CAPTION_STYLE_KEY` are: absent means what every older manifest meant, that
#: every transcribed word was spoken, so it takes no `SCHEMA_VERSION` bump.
#:
#: **Word-indexed, for the reason cues are** — the transcript indexes the
#: source, so no cut can invalidate a mark, and `Word.index` survives the
#: filtering below because a transcript never renumbers.
UNSPOKEN_KEY = "unspoken"


def _stored_unspoken(project: Project) -> dict[str, dict[int, str]]:
    """Per clip, the word indices marked never-spoken and the text each was.

    The text is on the record so the mark can be *checked* rather than
    trusted: an index is only meaningful against the transcript it was taken
    from, and re-transcribing a clip renumbers nothing but does change what
    sits at each index. `_spoken_transcripts` compares before it drops.
    """
    stored = project.read_manifest().get(UNSPOKEN_KEY, [])
    if not isinstance(stored, list):
        raise tx.TranscriptError(
            f"{project.manifest_path}'s {UNSPOKEN_KEY!r} must be a JSON array"
        )
    marked: dict[str, dict[int, str]] = {}
    for record in stored:
        marked.setdefault(str(record["clip_id"]), {})[int(record["word_index"])] = str(
            record.get("text", "")
        )
    return marked


def _spoken_transcripts(
    project: Project, transcripts: dict[str, tx.Transcript]
) -> tuple[dict[str, tx.Transcript], dict[str, Any]]:
    """The transcripts with the never-spoken words taken out.

    The one derivation, shared by `_caption_cues` and `verify` for the reason
    `_caption_cues` is itself shared: what the window draws, what the subtitle
    file contains and what the render is checked against cannot be three
    different word sequences. A word removed here is removed from all three,
    and `verify` reports the count so a render is never silently checked
    against a shortened expectation.

    **A stale mark is kept, never applied.** If the text on the record and the
    text at that index disagree, the transcript has been replaced under the
    mark, and the two failures are not symmetric: a word wrongly left on
    screen is visible to anyone watching, while a real word silently dropped
    is invisible in every check proofcut has. So a mismatch is reported as
    `unspoken_stale` and the word stays.
    """
    marked = _stored_unspoken(project)
    if not marked:
        return transcripts, {"unspoken": 0, "unspoken_stale": []}

    spoken: dict[str, tx.Transcript] = {}
    dropped = 0
    stale: list[dict[str, Any]] = []
    for clip_id, transcript in transcripts.items():
        indices = marked.get(clip_id)
        if not indices:
            spoken[clip_id] = transcript
            continue
        keep: list[tx.Word] = []
        for word in transcript.words:
            recorded = indices.get(word.index)
            if recorded is None:
                keep.append(word)
                continue
            if recorded and recorded != word.text:
                stale.append(
                    {
                        "clip_id": clip_id,
                        "word_index": word.index,
                        "recorded": recorded,
                        "found": word.text,
                    }
                )
                keep.append(word)
                continue
            dropped += 1
        spoken[clip_id] = replace(transcript, words=tuple(keep))
    return spoken, {"unspoken": dropped, "unspoken_stale": stale}


def caption_style(
    path: Path | str,
    *,
    preset: str | None = None,
    font: str | None = None,
    size: int | None = None,
    text: str | None = None,
    highlight: str | None = None,
    outline_colour: str | None = None,
    box_colour: str | None = None,
    bold: bool | None = None,
    box: bool | None = None,
    outline_width: float | None = None,
    shadow: float | None = None,
    position: str | None = None,
    margin: int | None = None,
    karaoke: bool | None = None,
    max_words: int | None = None,
    max_gap: float | None = None,
    max_duration: float | None = None,
    hold: float | None = None,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Read or change the caption look this project keeps.

    **The style is project state; the captions are derived.** That separation
    is the whole point of this op rather than a pile of arguments on
    `add_captions`: regenerating captions after a cut re-runs the derivation
    and picks the style back up, so "restyle, then keep editing" cannot lose
    the styling — there is nothing to preserve, because nothing was ever
    coupled to a particular generation.

    Called with no arguments it changes nothing and reports the current look,
    which is also how to find out what the fields are called. Any argument
    sets that field and leaves the rest alone; `reset` drops every override
    first, so `reset` plus `preset` is how to start clean from a preset.

    What is stored is the base preset name plus only the fields overridden on
    top of it — never a flattened copy — so the manifest stays readable and a
    later improvement to a preset still reaches a project that only changed
    its size. `plan` resolves and validates without writing.

    Colours take `#rrggbb`, `#rrggbbaa`, a name (`yellow`, `white`, …) or an
    ASS `&H…` value; positions are named (`bottom`, `top-right`, …). Both are
    echoed back resolved, in both vocabularies, because ASS quotes colours
    alpha-first-and-backwards and a value that looks right is routinely a
    different colour than the one meant.
    """
    project = Project.open(path)
    changes = {
        "preset": preset,
        "font": font,
        "size": size,
        "text": text,
        "highlight": highlight,
        "outline_colour": outline_colour,
        "box_colour": box_colour,
        "bold": bold,
        "box": box,
        "outline_width": outline_width,
        "shadow": shadow,
        "position": position,
        "margin": margin,
        "karaoke": karaoke,
        "max_words": max_words,
        "max_gap": max_gap,
        "max_duration": max_duration,
        "hold": hold,
    }
    changes = {field: value for field, value in changes.items() if value is not None}

    base = {} if reset else _stored_caption_style(project)
    style = captions.resolve({**base, **changes})

    write = bool(changes or reset) and not plan
    if write:
        manifest = project.read_manifest()
        if style.stored:
            manifest[CAPTION_STYLE_KEY] = style.stored
        else:
            manifest.pop(CAPTION_STYLE_KEY, None)
        project.write_manifest(manifest)

    return {
        "project": str(project.root),
        "changed": sorted(changes),
        "reset": bool(reset),
        "written": write,
        "plan": bool(plan),
        # Reported on every call, not only when the font changes: a project can
        # be opened on a machine that has a different set of fonts from the one
        # it was styled on, and the substitution is silent at every other layer.
        "font": captions.font_match(style.ass.font),
        **style.describe(),
    }


def _caption_cues(
    project: Project,
    edit: tl.Edit,
    style: captions.Style,
    clip_id: str | None,
) -> tuple[list[captions.Cue], list[captions.CueWord], int, dict[str, Any]]:
    """Place and group every transcribed word — the one derivation.

    Shared by `caption_view` and `add_captions` so that what the window draws
    and what the subtitle file contains cannot be two different groupings of
    the same words. The grouping numbers come off the style for the same
    reason the font does: line breaks are part of the look.
    """
    transcripts = _transcripts_for(project, clip_id)
    transcripts, unspoken = _spoken_transcripts(project, transcripts)
    placed, cut = captions.place(edit, transcripts)
    cues = captions.group(
        placed,
        max_words=style.max_words,
        max_gap=style.max_gap,
        max_duration=style.max_duration,
        hold=style.hold,
    )
    return cues, placed, cut, {"clips": sorted(transcripts), **unspoken}


def _offset_cues(cues: list[captions.Cue], offset: float) -> list[captions.Cue]:
    """Shift every cue, and every word inside it, forward by `offset` seconds.

    The render-time shift `add_captions` applies at its own call site — just
    before `captions.to_ass` — never inside `_caption_cues` itself:
    `caption_view` shares that derivation and stays Edit-relative on purpose
    (`locate`'s two-clock rule), so the shift belongs where the burn target
    is decided, not in the shared read. `offset=0.0` (no head) returns `cues`
    unchanged rather than rebuilding an identical list.
    """
    if not offset:
        return cues
    return [
        replace(
            cue,
            words=tuple(
                replace(word, start=word.start + offset, end=word.end + offset)
                for word in cue.words
            ),
            end=cue.end + offset,
        )
        for cue in cues
    ]


def unspoken_add(
    path: Path | str,
    clip_id: str,
    word_index: int | None = None,
    *,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Mark a word the transcript holds and the recording never said.

    The subject is one failure and not a general edit: whisper transcribes
    straight *across* a retake splice and emits words from both takes
    interleaved, so a word appears in the index that was never spoken
    (HISTORY.md § The hand-framed teaser, watched). It is in the transcript
    and in nothing else — not the audio, not the render — so every consumer of
    the transcript carries it and nothing downstream can tell it from a word
    somebody said quietly.

    This does not touch the transcript file, and it must not: the transcript
    is an immutable index over source media, word indices never renumber, and
    a cue at word 366 has to keep meaning word 366. What it writes is a mark
    beside the transcript, addressed the same way a cue is, so a cut can no
    more invalidate it than it can invalidate a cue.

    **It removes a word from captions, from `caption_view` and from what
    `verify` expects — the three that read the transcript rather than the
    audio.** It changes no audio, no timing and no shot: a marked word's
    seconds still belong to the words either side of it, because the sound in
    them is the take that was kept.

    Addressed by `word_index` **or** `phrase` — but unlike `cue_add`, a
    phrase that resolves to more than one word is refused rather than
    silently bound to an edge: unspoken addresses exactly one word (the word
    the recording never said), and picking a side of a wider match would
    silently mark the wrong one half the time. Narrow the phrase, or pass
    `occurrence=` if it is a disambiguation problem rather than a width one.

    Echoes the word it resolved to plus the three either side, for the reason
    every word-indexed tool here does: an index one past the intended word
    reads correctly on its own.
    """
    project = Project.open(path)
    media.get_clip(project, clip_id)
    parsed = _transcript(project, clip_id)
    word_index, _ = _resolve_word_or_phrase(
        parsed,
        word_index=word_index,
        phrase=phrase,
        after=after,
        occurrence=occurrence,
        edge="first",
        single=True,
    )
    echo = _cue_echo(parsed, word_index)

    manifest = project.read_manifest()
    marks = manifest.setdefault(UNSPOKEN_KEY, [])
    if any(m["clip_id"] == clip_id and int(m["word_index"]) == word_index for m in marks):
        raise tx.TranscriptError(
            f"word {word_index} of {clip_id!r} is already marked unspoken — "
            "remove it with unspoken_rm first (CLI: `proofcut unspoken rm`)"
        )
    mark: dict[str, Any] = {
        "clip_id": clip_id,
        "word_index": word_index,
        "text": parsed.words[word_index].text,
    }
    if phrase is not None:
        mark["phrase"] = phrase
    marks.append(mark)
    marks.sort(key=lambda m: (m["clip_id"], int(m["word_index"])))
    project.write_manifest(manifest)
    return {"clip_id": clip_id, "marked": len(marks), **echo}


def unspoken_rm(
    path: Path | str,
    clip_id: str,
    word_index: int | None = None,
    *,
    phrase: str | None = None,
    after: int = -1,
    occurrence: int | None = None,
) -> dict[str, Any]:
    """Unmark a word, putting it back into captions and into `verify`."""
    project = Project.open(path)
    parsed = _transcript(project, clip_id) if phrase is not None else None
    word_index, _ = _resolve_word_or_phrase(
        parsed,
        word_index=word_index,
        phrase=phrase,
        after=after,
        occurrence=occurrence,
        edge="first",
        single=True,
    )
    manifest = project.read_manifest()
    marks = manifest.get(UNSPOKEN_KEY, [])
    kept = [
        m
        for m in marks
        if not (m["clip_id"] == clip_id and int(m["word_index"]) == word_index)
    ]
    if len(kept) == len(marks):
        raise tx.TranscriptError(
            f"word {word_index} of {clip_id!r} is not marked unspoken"
        )
    if kept:
        manifest[UNSPOKEN_KEY] = kept
    else:
        manifest.pop(UNSPOKEN_KEY, None)
    project.write_manifest(manifest)
    return {"clip_id": clip_id, "word_index": word_index, "marked": len(kept)}


def unspoken_ls(path: Path | str) -> dict[str, Any]:
    """Every word marked never-spoken, with what the transcript says now.

    `stale` is the mark whose recorded text and current text disagree — the
    transcript was replaced under it — and those are reported here rather than
    applied anywhere, so a re-transcribe surfaces as a list to re-check rather
    than as words vanishing from a caption file.
    """
    project = Project.open(path)
    marked = _stored_unspoken(project)
    rows: list[dict[str, Any]] = []
    for clip_id in sorted(marked):
        try:
            parsed = _transcript(project, clip_id)
        except tx.TranscriptError:
            parsed = None
        for index in sorted(marked[clip_id]):
            recorded = marked[clip_id][index]
            found = (
                parsed.words[index].text
                if parsed is not None and 0 <= index < len(parsed.words)
                else None
            )
            row: dict[str, Any] = {
                "clip_id": clip_id,
                "word_index": index,
                "text": recorded,
                "found": found,
                "stale": bool(recorded and found is not None and recorded != found),
            }
            if parsed is not None and 0 <= index < len(parsed.words):
                row.update(_context(parsed, index, index))
            rows.append(row)
    return {
        "project": str(project.root),
        "count": len(rows),
        "stale": sum(1 for row in rows if row["stale"]),
        "unspoken": rows,
    }


#: How far either side of a candidate word to read the render's own words when
#: asking whether it was said. Wide enough to survive whisper placing a word a
#: few hundred milliseconds off, narrow enough that a common token borrowed
#: from the next sentence cannot vouch for one in this one.
UNSPOKEN_PAD = 1.5

#: A word this much of whose own duration survived the edit is *asked about*,
#: never removed — the removing is the render's answer, below. Set where it
#: asks about little and misses nothing: over the whole Scream film, 947 of
#: 958 surviving words survive **whole**, and the 11 under this floor hold
#: every clipped fragment in the cut, the shortest being 33ms of a `The`.
#: A floor that decided anything here would be wrong for the reason the
#: overlap scan has none (HISTORY.md § The overlap scan) — partial survival is
#: normal, and 0.48 of a word is a word.
UNSPOKEN_KEPT_SHARE = 0.5


def unspoken_detect(
    path: Path | str,
    render: Path | str,
    *,
    clip_id: str | None = None,
    transcript_path: Path | str | None = None,
    model: str | None = None,
    language: str | None = None,
    pad: float = UNSPOKEN_PAD,
    apply: bool = False,
) -> dict[str, Any]:
    """Propose the words the render's own ears say were never spoken.

    Two independent signals, and the intersection is the proposal — neither
    alone is safe. `transcript.find_overlaps` says *where a seam is*: a word
    starting before the one ahead of it ends is whisper reading across a
    splice, which is the only mechanism known to invent a word here. The
    render's transcription says *what was actually said*, and it is the only
    witness that answers to the audio rather than to the index. A seam word
    the render does not say is an invention; a seam word it does say is a word
    somebody said at a splice, and there are plenty.

    **Counted rather than looked up, because the inventions are function
    words.** Whisper's seams produce "The That's the ceiling" and "what was
    the this all about" as readily as "Billions" — asking "does the render say
    'the' near here" answers yes off the *real* `the` standing next to the
    invented one. So the candidate's token is counted in the timeline's words
    over the window and in the render's words over the same seconds, and it is
    proposed only where the timeline has more of them than the render heard.

    The two clocks agree by construction: a verified render is a render *of
    this timeline*, so a word's timeline seconds and the heard word's seconds
    are the same seconds. That is what makes a local window possible at all,
    and it is why this reads the render rather than diffing two whole word
    sequences — a global diff cannot say which of six `the`s it lost.

    `apply=False` is the default, the same way round as `reframe_detect` and
    for the same reason: this proposes a change to what a caption *says*, the
    evidence is a whisper run, and a wrongly applied mark deletes a real word
    from every check proofcut has. Read the echoes, then apply.

    `transcript_path` takes an existing transcription of the render — the
    cached one `verify` leaves behind is the obvious candidate, and it is
    passed explicitly rather than found, for `verify`'s own reason: a
    re-render under the same filename would otherwise be judged against the
    previous render's audio.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    transcripts = _transcripts_for(project, clip_id)
    already = _stored_unspoken(project)

    render_path = Path(render).expanduser()
    if transcript_path is not None:
        heard_transcript = tx.load(transcript_path, clip_id="render")
        origin = str(transcript_path)
    else:
        model = model or asr.DEFAULT_MODEL
        payload = asr.transcribe(
            render_path, model=model, language=language or _shared_language(transcripts)
        )
        if not payload.get("words") and not payload.get("segments"):
            raise vfy.VerifyError(
                f"whisper heard no speech at all in {render_path.name} — there is "
                "nothing here to judge a transcript against"
            )
        heard_transcript = tx.parse_whisper(
            payload, clip_id="render", origin=f"whisper:{model}"
        )
        cached = project.verify_dir / f"{render_path.stem}.json"
        tx.save(heard_transcript, cached)
        origin = str(cached)

    heard = [
        (word.start, word.end, token)
        for word in heard_transcript.words
        for token in vfy.tokens([word.text])
    ]

    proposals: list[dict[str, Any]] = []
    seams_seen = 0
    fragments_seen = 0
    for name, transcript in sorted(transcripts.items()):
        marked = already.get(name, {})
        # Every word placed on the timeline, with the source index kept: the
        # question is asked of what plays, and answered against what was heard.
        placed: list[tuple[int, float, float, str]] = []
        for word in transcript.words:
            span = edit.timeline_span(
                name, word.start, max(word.end, word.start + captions.MIN_WORD)
            )
            if span is not None:
                placed.append((word.index, span[0], span[1], word.text))
        at_index = {index: (start, end) for index, start, end, _ in placed}

        # Two candidate sources, one confirmation. A seam is where whisper
        # *invented* a word; a fragment is where a cut left a sliver of a real
        # one — 33ms of a `The` from an abandoned take reads on screen as a
        # whole word and is inaudible. Different mechanisms, same symptom, and
        # widening the candidates costs nothing because it is the render that
        # decides. `seam` is None for the second kind: there is no splice to
        # quote, and claiming one would put a false reason on the record.
        candidates: dict[int, dict[str, Any] | None] = {}
        for seam in tx.find_overlaps(transcript.words):
            seams_seen += 1
            for index in range(seam["first_word"], seam["last_word"] + 1):
                candidates.setdefault(index, seam)
        for index, start, end, _ in placed:
            word = transcript.words[index]
            spoken_for = word.end - word.start
            if spoken_for <= 0:
                continue
            if (end - start) / spoken_for < UNSPOKEN_KEPT_SHARE:
                fragments_seen += 1
                candidates.setdefault(index, None)

        for index in sorted(candidates):
            if index in marked or index not in at_index:
                continue
            seam = candidates[index]
            word = transcript.words[index]
            token = vfy.tokens([word.text])
            if not token:
                # Normalises to nothing — "-" and its friends. The render can
                # never be asked about it, so the candidacy is the only
                # evidence there is, and it is enough: a token with no letters
                # in it was never a word anyone said.
                proposals.append(
                    _unspoken_proposal(transcript, seam, index, heard_says=None)
                )
                continue
            start, end = at_index[index]
            window = (start - pad, end + pad)
            mine = sum(
                1
                for _, other_start, other_end, text in placed
                if window[0] <= other_start and other_end <= window[1]
                for other in vfy.tokens([text])
                if other == token[0]
            )
            theirs = sum(
                1
                for heard_start, heard_end, other in heard
                if other == token[0]
                and heard_end >= window[0]
                and heard_start <= window[1]
            )
            if mine > theirs:
                proposals.append(
                    _unspoken_proposal(transcript, seam, index, heard_says=(mine, theirs))
                )

    applied = 0
    if apply:
        manifest = project.read_manifest()
        marks = manifest.setdefault(UNSPOKEN_KEY, [])
        for proposal in proposals:
            marks.append(
                {
                    "clip_id": proposal["clip_id"],
                    "word_index": proposal["word_index"],
                    "text": proposal["text"],
                }
            )
            applied += 1
        marks.sort(key=lambda m: (m["clip_id"], int(m["word_index"])))
        project.write_manifest(manifest)

    return {
        "project": str(project.root),
        "render": str(render_path),
        "heard_transcript": origin,
        "pad": pad,
        "seams": seams_seen,
        "fragments": fragments_seen,
        "already_marked": sum(len(v) for v in already.values()),
        "count": len(proposals),
        "applied": applied,
        "apply": apply,
        "proposals": proposals,
    }


def _unspoken_proposal(
    transcript: tx.Transcript,
    seam: dict[str, Any] | None,
    index: int,
    *,
    heard_says: tuple[int, int] | None,
) -> dict[str, Any]:
    """One proposal, echoed the way every word-indexed tool here echoes."""
    word = transcript.words[index]
    why = (
        "no token — a candidate with no letters in it"
        if heard_says is None
        else f"the timeline says it {heard_says[0]}x here, the render says it {heard_says[1]}x"
    )
    return {
        "clip_id": transcript.clip_id,
        "word_index": index,
        "text": word.text,
        "start": word.start,
        "end": word.end,
        # Which mechanism put it up for the question, in its own words: a
        # splice whisper read across, or a cut that left a sliver. Naming the
        # wrong one is worse than naming none, so the second says `null`.
        "found_by": "seam" if seam is not None else "fragment",
        "seam": seam["text"] if seam is not None else None,
        "seam_words": [seam["first_word"], seam["last_word"]] if seam is not None else None,
        "overlap": seam["worst"] if seam is not None else None,
        "why": why,
        **_context(transcript, index, index),
    }


def caption_view(
    path: Path | str,
    clip_id: str | None = None,
    *,
    first: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The captions this timeline would produce, with the style in force.

    `first`/`limit` window the `cues` list (`window_list`); `words` stays the
    whole count.

    `add_captions` without the writing — the read model behind the preview
    overlay and the window's CC lane, and the way to see a restyle before
    committing a file to it. Cues are in *timeline* seconds, already grouped
    by the stored style's own break rules, so a front end draws them and
    decides nothing.

    Read-only, and it reports rather than raises where `add_captions` refuses:
    a project with no transcript, or one whose every word has been cut, comes
    back with `cues: []` and the reason, because this is the view a person has
    open while making exactly that mistake. `resolution` is the reference
    canvas the style's sizes and margins are quoted against — 1080 tall
    whatever the footage is (`captions.REFERENCE_HEIGHT`), which is what a
    preview must scale by to show the size that will burn in.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    style = captions.resolve(_stored_caption_style(project))
    width, height = _caption_canvas(project)

    result: dict[str, Any] = {
        "project": str(project.root),
        "clip_id": clip_id,
        "resolution": [width, height],
        "timeline_duration": edit.duration,
        "style": style.describe(),
        "font": captions.font_match(style.ass.font),
    }
    try:
        cues, placed, cut, meta = _caption_cues(project, edit, style, clip_id)
    except tx.TranscriptError as exc:
        return {**result, "cues": [], "words": 0, "words_cut": 0, "cues_error": str(exc)}

    result.update(meta)
    result["cues"] = [cue.as_dict() for cue in cues]
    result["words"] = len(placed)
    result["words_cut"] = cut
    if not cues:
        result["cues_error"] = "no transcribed word survives on the timeline"
    return window_list(result, "cues", first, limit)


def add_captions(
    path: Path | str,
    output: Path | str,
    *,
    clip_id: str | None = None,
    preset: str | None = None,
    max_words: int | None = None,
    max_gap: float | None = None,
    max_duration: float | None = None,
    hold: float | None = None,
    burn: Path | str | None = None,
    burn_output: Path | str | None = None,
    log: bool = True,
) -> dict[str, Any]:
    """Write word-timed ASS captions for the current timeline.

    Timings are the timeline's, not the recording's: every word is mapped
    through the accumulated edit, and words that have been cut do not appear.
    The count that did is reported as `words_cut`, so a missing sentence can be
    told apart from a bug.

    **A burn records itself in the render log** (`renderlog`), continuing the
    run `export` opened for the file it burned onto, so `finish_report` can
    say `captions.burned: "yes"` for a film an agent rendered and captioned
    through the CLI or the MCP server. `log=False` is the web UI's, which logs
    its pipeline whole. Writing the `.ass` alone logs nothing: no render
    happened, so there is nothing a burn could be true of.

    The look comes from the project (`caption_style`), not from this call.
    `preset` and the four grouping numbers still override it for a one-off
    file, but they override *for this file only* — they are not written back,
    so the next regeneration is styled the way the project says again. That
    asymmetry is deliberate: one writer for the style, and it is not this.

    `caption_view` is this op's `plan`: same cues, same style, nothing written.

    `burn` renders the captions into a video with ffmpeg. It has to be a render
    of *this* timeline — burning onto the untrimmed source lines the captions up
    against audio that has since moved. The default exit is the sidecar `.ass`,
    which Kdenlive loads and can restyle.
    """
    project = Project.open(path)
    edit = _load_edit(project)

    stored = _stored_caption_style(project)
    overrides = {
        "preset": preset,
        "max_words": max_words,
        "max_gap": max_gap,
        "max_duration": max_duration,
        "hold": hold,
    }
    overrides = {field: value for field, value in overrides.items() if value is not None}
    style = captions.resolve({**stored, **overrides})

    cues, placed, cut, meta = _caption_cues(project, edit, style, clip_id)
    if not placed:
        raise captions.CaptionError(
            "no transcribed word survives on the timeline — nothing to caption"
        )

    # The one render-facing shift this op owns: the burn target is the real
    # export, and a configured head means the export's own first frame is
    # `head_seconds` before the Edit's — see `locate`'s two-clock note.
    # `caption_view`/`_caption_cues` stay Edit-relative; only the ASS write
    # moves.
    head_seconds = _head_seconds(project)
    ass_cues = _offset_cues(cues, head_seconds)

    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        captions.to_ass(
            ass_cues,
            style=style.ass,
            resolution=_caption_canvas(project),
            title=project.read_manifest().get("name", "proofcut"),
        ),
        encoding="utf-8",
    )

    result: dict[str, Any] = {
        "output": str(destination),
        "preset": style.base,
        "style": style.describe(),
        "overrides": sorted(overrides),
        **meta,
        "cues": len(cues),
        "words": len(placed),
        "words_cut": cut,
        "captioned_duration": cues[-1].end - cues[0].start,
        "timeline_duration": edit.duration,
        "head_seconds": head_seconds,
    }

    if burn is not None:
        source = Path(burn).expanduser()
        target = (
            Path(burn_output).expanduser()
            if burn_output
            else project.render_dir / f"{source.stem}-captioned{source.suffix}"
        )
        result["burned"] = str(captions.burn(source, destination, target))
        if log:
            # `continues=source`: the file just burned onto is the render this
            # burn belongs to, so when `export` logged that same path this
            # carries its stages forward instead of opening a second run.
            _log_render(
                project,
                output=result["burned"],
                preset=None,
                stages={"burn": {"outcome": "done", "detail": None}},
                continues=str(source),
            )

    return result


def _export_fps(clips: dict[str, dict[str, Any]]) -> float:
    """The picture's frame rate if there is picture, else a sane default."""
    for clip in clips.values():
        if clip.get("has_video") and clip.get("fps"):
            return float(clip["fps"])
    return DEFAULT_EXPORT_FPS


def check_frames(
    path: Path | str, target: Path | str | None = None, *, fps: float | None = None
) -> dict[str, Any]:
    """Count the frames the timeline should run to, and check a target against it.

    The picture-side counterpart to `verify`, which deliberately covers only
    audio. `expected_frames` is what `export` lays down — `_frame_total_with_tail`,
    the same arithmetic the export itself uses (`autoeditor.frame_layout` plus
    whatever a configured `tail` adds), so the two cannot drift — and every
    segment edge is quantised on its own, which is why this is not
    `round(duration * fps)`.

    With no `target` it reports that number and stops, which is the cheap thing
    to do before an export. With one:

    * an NLE project (`.kdenlive`, `.mlt`, `.xml`) is put to `melt -consumer
      xml`, which resolves the document and says what it *would* render without
      encoding anything. This is the load-bearing check, and it is load-bearing
      because it runs **before** the render: exact agreement here is what made
      68 cut positions on the Scream essay trustworthy (HISTORY.md § 3).
    * anything else is treated as a render and counted with ffprobe.

    `fps` must be the rate the export used, or the two sides are counting
    against different grids; it defaults to the same rate `export` would pick.

    An audio-only render has no frames, and that is the ordinary case for a VO
    project rather than a failure: `agrees` comes back null with a note, and the
    NLE project is the thing to point this at instead.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    if not edit.segments:
        raise ProjectError("the timeline is empty — there are no frames to count")

    rate = float(fps) if fps else _export_fps(_clips_by_id(project))
    expected = _frame_total_with_tail(project, edit, rate)
    result: dict[str, Any] = {
        "fps": rate,
        "segments": len(edit.segments),
        "timeline_duration": edit.duration,
        "expected_frames": expected,
        "expected_duration": expected / rate,
    }
    if target is None:
        return result

    target_path = Path(target).expanduser()
    if not target_path.exists():
        raise picture.PictureError(f"no such file to check: {target_path}")
    result["target"] = str(target_path)
    notes: list[str] = []

    if target_path.suffix.lower() in picture.NLE_SUFFIXES:
        result["target_kind"] = "nle-project"
        counted: int | None = picture.project_frames(target_path)
        result["target_duration"] = counted / rate if counted is not None else None
    else:
        result["target_kind"] = "render"
        counts = media.count_frames(target_path)
        counted = counts["frames"]
        result["target_duration"] = counts["duration"]
        if not counts["has_video"]:
            result.update({"target_frames": None, "delta": None, "agrees": None})
            result["notes"] = [
                (
                    "this render has no video stream, so there are no frames to "
                    "count and the picture-side check does not apply to it. Compare "
                    "`target_duration` against `expected_duration`, and point this "
                    "at the NLE project if you want a frame count for a VO."
                )
            ]
            return result
        # Two ffprobe readings of one file disagreeing is itself the finding.
        container = counts["container_frames"]
        result["container_frames"] = container
        if container is not None and counted is not None and container != counted:
            notes.append(
                f"ffprobe's two counts disagree: {counted} packets against a "
                f"container header claiming {container}. The packet count is "
                "the one compared here; the header is metadata a muxer can get "
                "wrong. Worth knowing before trusting either."
            )

    if counted is None:
        raise picture.PictureError(
            f"could not get a frame count out of {target_path} — it has a video "
            "stream but ffprobe counted no packets in it."
        )

    delta = counted - expected
    result.update({"target_frames": counted, "delta": delta, "agrees": delta == 0})
    if delta == picture.KNOWN_TAIL_FRAME and result["target_kind"] == "nle-project":
        notes.append(picture.TAIL_FRAME_NOTE)
    if notes:
        result["notes"] = notes
    return result


#: Where a project records the export it is meant to agree with, as a path
#: string. Absent means "nothing declared" — which is exactly what every
#: project written before this key existed meant, so it is additive the way
#: `CANVAS_KEY`/`CAPTION_STYLE_KEY`/`TAIL_KEY` are and gets no `SCHEMA_VERSION`
#: bump for the same reason (HISTORY.md § The VO the project was holding: "a
#: caveat recorded in a results table is not a guard" — this is the guard, and
#: it has to live in the manifest rather than in whoever typed the path last).
REFERENCE_KEY = "reference"

#: How far `film_check`'s own `timeline_duration` may drift from a reference
#: render's ffprobe duration and still count as the same film. This is
#: deliberately not a frame grid — `check_frames` above already owns
#: frame-exact agreement, decoding the target through `melt` or counting its
#: packets. `film_check` compares two much cheaper numbers (a sum of segment
#: lengths against a container header) that were never going to land on the
#: same float: frame quantisation and encoder padding move a correct render
#: away from the raw `timeline_duration` by a measured 0.072s on the shipped
#: Scream film (336.269s timeline against a 336.341s render of it). The
#: disagreement this function exists to catch is nothing like that scale —
#: the same film's stale-VO project measured 74.622s away from the same
#: reference. One second sits two orders of magnitude above the noise a
#: correct render produces and two below the defect this is for.
FILM_CHECK_TOLERANCE = 1.0


def _stored_reference(project: Project) -> str | None:
    """The project's declared reference export, or None if never declared."""
    stored = project.read_manifest().get(REFERENCE_KEY)
    return None if stored is None else str(stored)


def film_check(
    path: Path | str,
    reference: Path | str | None = None,
    *,
    reset: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Compare this project against the export it is supposed to be, cheaply.

    Answers PLAN.md § Open questions, *How does a lucid project know it is
    the film* — the question the Scream project's stale VO left open.
    `~/proofcut-work/projects/scream-v2` sat at the silence-cut stage of an edit whose retake
    pass had already been done outside proofcut: 73 segments, 410.963s, against
    the shipped film's 63 segments, 336.269s. The render matched the
    timeline, `verify` had nothing to report, all 38 shots planned — **every
    check proofcut had agreed with itself the whole time**, because none of them
    compares a project to anything outside it. `check_frames` is the closest
    relative and is not this: it asks whether an export it is *about* to make
    (or one already made) matches *this* project's own arithmetic, framewise.
    It structurally cannot catch this project being the wrong film to begin
    with — it would have agreed with itself just as cleanly on the stale cut.
    This asks the other question: does this project's own answer resemble a
    *reference* export's, at all. (HISTORY.md § The VO the project was
    holding.)

    The comparison is deliberately coarse — cheaper than `check_frames`, and
    answering a coarser question. The number compared is
    **`expected_duration`** — `_frame_total_with_tail`, the edit plus whatever
    a configured `tail` adds, which is what `export` actually lays down — and
    not `timeline_duration`, which is the edit alone and is reported beside it
    for the record. Comparing the edit was wrong the moment `tail` became
    project state: the Scream film's own project renders 342.36s and its edit
    runs 336.27s, so this read `agrees: false` at a delta of exactly the
    6s end card, in the same direction and the same order of magnitude as the
    stale VO it exists to catch. A check that cries wolf on the film it is
    pointed at is worse than no check. (`_frame_total_with_tail`'s own
    docstring names the rule: a duration answered two ways is how a render
    disagrees with its own timeline while both report clean.) A project with
    no tail compares within a frame of where it used to, so nothing that
    agreed before stops agreeing.
    **`reference_duration`** is read off `reference` with ffprobe alone —
    no `melt`, no frame counting — because a *segment count* is not a number
    a finished render carries: once encoded there is no cut boundary left to
    count, only a stream of frames. So `segments` is reported for the record
    (what CAN be asked of this project) and there is nothing on the
    reference's side to set it against (what CANNOT be asked of a render) —
    naming that gap honestly is the point, rather than inventing a number
    or silently dropping the comparison. `agrees` is the two durations within
    `FILM_CHECK_TOLERANCE` of each other; `duration_delta` is the raw
    difference, which is the number that would have read as 74.622 the day
    this was needed and been impossible to miss.

    `reference` is remembered, not just used once: passing it stores it under
    the project's `reference` key (additive, no schema bump — the `canvas`/
    `caption_style` precedent) so every later call — from a script, from an
    agent that never saw the original conversation — asks the same question
    without the path being retyped or forgotten. That is the fix HISTORY.md's
    own postmortem names: "a caveat recorded in a results table is not a
    guard," because nothing re-checked it. This makes the claim project
    state instead of a sentence someone has to remember to re-read. Called
    with no `reference` and none declared, it reports the project's own
    numbers and says so rather than raising — the same shape `canvas` and
    `tail` use for "nothing set yet." `reset` drops the declared reference;
    `plan` resolves and validates without writing.
    """
    if reference is not None and reset:
        raise ProjectError("pass a reference or `reset`, not both")

    project = Project.open(path)
    stored = _stored_reference(project)
    if reference is not None:
        after: str | None = str(Path(reference).expanduser())
    elif reset:
        after = None
    else:
        after = stored

    write = (reference is not None or reset) and not plan
    if write:
        manifest = project.read_manifest()
        if after is None:
            manifest.pop(REFERENCE_KEY, None)
        else:
            manifest[REFERENCE_KEY] = after
        project.write_manifest(manifest)

    edit = _load_edit(project)
    if not edit.segments:
        raise ProjectError("the timeline is empty — there is no film here to check yet")

    rate = _export_fps(_clips_by_id(project))
    expected_duration = _frame_total_with_tail(project, edit, rate) / rate
    tail = _stored_tail(project)
    result: dict[str, Any] = {
        "project": str(project.root),
        "timeline_duration": edit.duration,
        "expected_duration": expected_duration,
        "tail_seconds": float(tail["seconds"]) if tail else 0.0,
        "segments": len(edit.segments),
        "reference": after,
        "reference_source": (
            "argument" if reference is not None else ("declared" if after is not None else None)
        ),
    }
    if after is None:
        result["notes"] = [
            (
                "no reference declared — pass `reference` to compare against an "
                "exported file. Passing one also records it, so the next call "
                "(from anyone, with no argument) asks the same question again."
            )
        ]
        return result

    target_path = Path(after).expanduser()
    if not target_path.exists():
        raise picture.PictureError(f"no such reference file: {target_path}")

    counts = media.count_frames(target_path)
    reference_duration = counts["duration"]
    if reference_duration is None:
        raise picture.PictureError(
            f"ffprobe reported no duration for {target_path} — it may not be a "
            "readable media file"
        )

    delta = expected_duration - reference_duration
    result.update(
        {
            "reference_duration": reference_duration,
            "tolerance": FILM_CHECK_TOLERANCE,
            "duration_delta": delta,
            "agrees": abs(delta) <= FILM_CHECK_TOLERANCE,
        }
    )
    result["notes"] = [
        (
            "segment count has nothing to compare against on the reference side — "
            "a finished render carries no cut boundaries, only frames, so "
            "`segments` is reported for the record and duration is the only "
            "number both sides can produce. See check_frames for a frame-exact "
            "check once this one agrees."
        )
    ]
    if result["tail_seconds"]:
        result["notes"].append(
            "the compared number is `expected_duration` — the edit plus this "
            f"project's {result['tail_seconds']:g}s tail — because that is what "
            "`export` lays down; `timeline_duration` is the edit alone."
        )
    return result


def check_black(
    path: Path | str,
    target: Path | str,
    *,
    fps: float | None = None,
    pix_th: float = 0.10,
    min_duration: float | None = None,
) -> dict[str, Any]:
    """Scan a render for black stretches, and say whether each is explained.

    ffmpeg's `blackdetect` finds every black run in `target`. Each is checked
    against the timeline's own `expected_frames`/`expected_duration`
    (`_frame_total_with_tail`, the same arithmetic `check_frames` already
    trusts) using `media.count_frames`'s packet count rather than a fresh
    probe, so the two checks' delta math cannot drift apart.

    A run is `explained` only when it sits at the tail of the render *and*
    the frame delta between `target` and the timeline is exactly
    `picture.KNOWN_TAIL_FRAME` (picture.py) — the documented auto-editor
    kdenlive-export defect. `check_frames` only ever compares that constant
    against an NLE-project target, because a `-consumer xml` read is the only
    place the tail frame shows up before anything is rendered. **This
    deliberately broadens the same reasoning to a bare render** — the
    trailing frame that defect produces is really encoded, not just
    declared, so it can equally turn up in a finished file, and the point of
    naming the defect is to keep it from being mistaken for a genuine one
    wherever it shows up, not only in the one place it was first caught. A
    run inside the declared picture is never explained regardless of delta —
    position has to match the known defect, not just the count.

    `min_duration` defaults to 0, not the export's half-a-frame grid the rest
    of this module measures against — verified against the installed ffmpeg
    (8.1.2), not assumed: `blackdetect` derives a run's reported duration
    from the *next* frame's timestamp, so every run it ever reports is
    already quantised to whole frames except one specific case — a black run
    that reaches end of stream with no following frame reports
    `black_duration:0` regardless of how many black frames it actually
    contains. That exact case is precisely the trailing kdenlive-export
    frame this function exists to explain, so a positive threshold (which
    would read as "half a frame of slack") would silently make `blackdetect`
    itself drop the one event this check is for. Nothing spurious gets in at
    0 that wouldn't already pass at half a frame: every other run's duration
    is a real multiple of the frame interval, never a fraction of one.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    if not edit.segments:
        raise ProjectError("the timeline is empty — there is no picture to check")

    rate = float(fps) if fps else _export_fps(_clips_by_id(project))
    expected_frames = _frame_total_with_tail(project, edit, rate)
    expected_duration = expected_frames / rate
    threshold = min_duration if min_duration is not None else 0.0

    target_path = Path(target).expanduser()
    if not target_path.exists():
        raise picture.PictureError(f"no such file to check: {target_path}")

    result: dict[str, Any] = {
        "target": str(target_path),
        "fps": rate,
        "pix_th": pix_th,
        "min_duration": threshold,
        "expected_duration": expected_duration,
        "expected_frames": expected_frames,
    }

    counts = media.count_frames(target_path)
    result["target_duration"] = counts["duration"]
    if not counts["has_video"]:
        result.update({"clean": None, "runs": [], "target_frames": None})
        result["notes"] = [
            (
                "this render has no video stream, so there is no picture to scan "
                "for black. Point this at a real render instead."
            )
        ]
        return result

    if counts["frames"] is None:
        raise picture.PictureError(
            f"could not get a frame count out of {target_path} — it has a video "
            "stream but ffprobe counted no packets in it."
        )

    delta = counts["frames"] - expected_frames
    result["target_frames"] = counts["frames"]
    tail_boundary = expected_duration - 0.5 / rate

    runs = picture.blackdetect(target_path, pix_th=pix_th, min_duration=threshold)
    reported: list[dict[str, Any]] = []
    for run in runs:
        inside = run["start"] < tail_boundary
        explained = not inside and delta == picture.KNOWN_TAIL_FRAME
        entry = {
            "start": run["start"],
            "end": run["end"],
            "duration": run["duration"],
            "inside_expected_picture": inside,
            "explained": explained,
        }
        if explained:
            entry["note"] = picture.TAIL_FRAME_NOTE
        reported.append(entry)

    result["runs"] = reported
    result["clean"] = all(r["explained"] for r in reported)

    notes: list[str] = []
    container = counts["container_frames"]
    if container is not None and container != counts["frames"]:
        notes.append(
            f"ffprobe's two counts disagree: {counts['frames']} packets against "
            f"a container header claiming {container}. The packet count is the "
            "one compared here."
        )
    if notes:
        result["notes"] = notes
    return result


def _nearest_word(parsed: tx.Transcript, t: float) -> dict[str, Any]:
    """The word playing at source time `t`, or the nearest one across a gap.

    Overlap test first (`word.start <= t < word.end`); a sample that lands in
    silence between words falls back to the nearest by edge distance rather
    than reporting nothing. Reuses `_context` either way — this is the
    CLAUDE.md echo convention run in reverse, time-to-word instead of
    word-to-time.
    """
    words = parsed.words
    for w in words:
        if w.start <= t < w.end:
            idx = w.index
            break
    else:
        idx = min(range(len(words)), key=lambda i: min(abs(words[i].start - t), abs(words[i].end - t)))
    return {"word": {"index": words[idx].index, "text": words[idx].text}, **_context(parsed, idx, idx)}


#: Beside every other sheet under `cache/sheets/`, its own subdirectory like
#: `SHOT_SHEET_DIR`/`FOOTAGE_SHEET_DIR`/`FIRST_LOOK_DIR` — `reframe_sheet`'s
#: flat-and-swept layout is the one exception, not the pattern to copy.
SPOT_SHEET_DIR = "cache/sheets/spots"


def _spot_frames_montage(
    project: Project, target: Path, frames: list[dict[str, Any]]
) -> Path | None:
    """Montage `spot_frames`' own extracted PNGs into one labelled sheet.

    Drawn from the same PNGs `spot_frames` already wrote to
    `cache/frames/<render-stem>/` — no second extraction — so the only new
    cost is the tile-and-montage step every other sheet already pays.
    `target`'s own stem slugs the directory, wiped each call: a target is
    re-sampled at a different `count`/`times` far more often than a clip is
    re-imported, and a stale tile from a wider run montaging into a narrower
    one is exactly `_first_look_montage`'s own reason for wiping first.

    Frames a bad seek already dropped (no `png` key, only `error`) are
    skipped rather than aborting the whole sheet — the same one-bad-frame
    tolerance `spot_frames` itself applies to extraction.
    """
    usable = [f for f in frames if "png" in f]
    if not usable:
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", target.stem)
    dest = project.root / SPOT_SHEET_DIR / slug
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    tiles: list[Path] = []
    for frame in usable:
        tile = dest / f"{frame['index']:03d}.png"
        yavg = frame.get("YAVG")
        label = f"t={frame['time']:.2f}s" + (f" YAVG={yavg:.0f}" if yavg is not None else "")
        _sheet_tile(
            Path(frame["png"]),
            label,
            tile,
            width=SHEET_PAGE_WIDTH // SHOT_SHEET_COLUMNS,
        )
        tiles.append(tile)
    return graphics.montage(
        tiles,
        dest / "sheet.jpg",
        columns=SHOT_SHEET_COLUMNS,
        tile_width=SHEET_PAGE_WIDTH // SHOT_SHEET_COLUMNS,
        quality=SHOT_SHEET_QUALITY,
    )


def spot_frames(
    path: Path | str,
    target: Path | str,
    *,
    count: int = 6,
    times: Sequence[float] | None = None,
    fps: float | None = None,
) -> dict[str, Any]:
    """Pull sample frames from a render as PNGs, with luma stats attached.

    `count` evenly-spaced frames (midpoint-sampled, so a sample never lands
    exactly on frame 0 or the last frame) plus any explicit `times`, each
    extracted with `picture.extract_frame` into
    `cache/frames/<render-stem>/` and ranked darkest-first by `YAVG`.

    Word/clip mapping via `Edit.source_at` is attempted only when `target`'s
    own probed duration agrees with the *current* timeline within a frame
    (`mapping_trusted`) — a stale render silently mapping to the wrong words
    would be worse than no mapping at all. When it is not trusted, every
    frame still gets its PNG and stats; only the clip_id/source_time/word
    fields are withheld, and a top-level note points at `check_frames` for
    the stronger check.

    A single bad extraction (a `PictureError` from a seek near a boundary) is
    caught and reported per-frame rather than aborting the whole batch — this
    is an exploratory tool over potentially many samples, and one bad seek
    should not cost the other N-1.

    **`frames[].png` is a path, and the agent panel runs `--tools ToolSearch` — no
    `Read`, so a path there is exactly as unreachable as it was for
    `shot_sheet`/`footage_sheet`/`contact_sheet` before each grew a montage
    (TRIAL.md § `spot_frames` hands back paths the agent cannot open). `sheet`
    is that fix applied here: the same frames montaged into one labelled JPEG
    under `cache/sheets/spots/`, best-effort like `contact_sheet`'s own
    (`sheet_error` rather than a raise on a box with no `magick`) — the PNGs
    are the record either way, so a sheet failure never costs the sample.
    """
    if count <= 0 and not times:
        raise picture.PictureError(
            "spot_frames needs count > 0 or explicit times — nothing to sample"
        )

    project = Project.open(path)
    edit = _load_edit(project)
    if not edit.segments:
        raise ProjectError("the timeline is empty — there is nothing to sample")

    target_path = Path(target).expanduser()
    if not target_path.exists():
        raise picture.PictureError(f"no such file to sample: {target_path}")

    counts = media.count_frames(target_path)
    target_duration = counts["duration"]
    result: dict[str, Any] = {
        "target": str(target_path),
        "target_duration": target_duration,
        "has_video": counts["has_video"],
    }
    if not counts["has_video"]:
        result.update({"frames": [], "darkest_first": [], "sheet": None})
        result["notes"] = ["this render has no video stream, so there are no frames to sample."]
        return result

    if target_duration is None or target_duration <= 0:
        raise picture.PictureError(f"could not read a duration for {target_path}")

    rate = float(fps) if fps else _export_fps(_clips_by_id(project))
    expected_frames = _frame_total_with_tail(project, edit, rate)
    expected_duration = expected_frames / rate
    half_frame = 0.5 / rate
    mapping_trusted = abs(target_duration - expected_duration) <= half_frame

    notes: list[str] = []
    if not mapping_trusted:
        notes.append(
            f"target_duration ({target_duration:.3f}s) disagrees with the current "
            f"timeline's export grid ({expected_duration:.3f}s) by more than a frame "
            f"at {rate}fps — this render may be stale, so clip/word mapping is "
            "refused. Run check_frames against it for the stronger check."
        )

    sampled: list[tuple[float, str]] = []
    if count > 0:
        step = target_duration / count
        sampled.extend((step * (i + 0.5), "sampled") for i in range(count))
    for t in times or []:
        sampled.append((float(t), "explicit"))
    sampled.sort(key=lambda item: item[0])

    max_time = max(0.0, target_duration - half_frame)
    transcripts: dict[str, tx.Transcript | None] = {}
    frames_out: list[dict[str, Any]] = []

    for index, (requested, origin) in enumerate(sampled):
        clamped = min(max(requested, 0.0), max_time)
        entry: dict[str, Any] = {"index": index, "time": clamped, "origin": origin}
        if clamped != requested:
            entry["clamped_from"] = requested

        dest = project.frames_dir / target_path.stem / f"{index:02d}_{clamped:.3f}s.png"
        try:
            stats = picture.extract_frame(target_path, clamped, dest)
        except picture.PictureError as exc:
            entry["error"] = str(exc)
            frames_out.append(entry)
            continue

        entry["png"] = str(dest)
        entry.update(stats)

        if mapping_trusted:
            located = edit.source_at(clamped)
            if located is not None:
                clip_id, source_time = located
                entry["clip_id"] = clip_id
                entry["source_time"] = source_time
                if clip_id not in transcripts:
                    try:
                        transcripts[clip_id] = _transcript(project, clip_id)
                    except tx.TranscriptError:
                        transcripts[clip_id] = None
                parsed = transcripts[clip_id]
                if parsed is not None and parsed.words:
                    entry.update(_nearest_word(parsed, source_time))

        frames_out.append(entry)

    darkest_first = sorted(
        (f["index"] for f in frames_out if "YAVG" in f), key=lambda i: frames_out[i]["YAVG"]
    )

    try:
        sheet = _spot_frames_montage(project, target_path, frames_out)
    except (graphics.GraphicsError, OSError) as exc:
        sheet = None
        notes.append(f"could not build a montage of the sampled frames: {exc}")

    result.update(
        {
            "fps": rate,
            "expected_duration": expected_duration,
            "mapping_trusted": mapping_trusted,
            "frames": frames_out,
            "darkest_first": darkest_first,
            "sheet": str(sheet) if sheet else None,
        }
    )
    if notes:
        result["notes"] = notes
    return result


# -- checking the render -------------------------------------------------


def _shared_language(transcripts: dict[str, tx.Transcript]) -> str | None:
    """The language every source transcript agrees on, if they agree at all.

    Passing it to whisper stops it language-detecting the render from scratch,
    which it occasionally gets wrong on a short or music-heavy one. Ambiguity
    means letting whisper decide is the safer default.
    """
    languages = {t.language for t in transcripts.values() if t.language}
    return languages.pop() if len(languages) == 1 else None


#: How far past a configured head's own length `verify` still counts a heard
#: word as the head's own, not the render's first body word. Whisper's word
#: timestamps are not frame-exact (CLAUDE.md: "trust a transcript's word
#: order, never its word durations"), so a head word timed a beat late must
#: not read as a spurious leading insertion in the body diff — the same
#: order of magnitude as `picture.RENDER_DURATION_TOLERANCE`, the slop this
#: codebase already allows between a claimed duration and a measured one.
HEAD_TRIM_TOLERANCE = 0.15


def verify(
    path: Path | str,
    render: Path | str,
    *,
    clip_id: str | None = None,
    transcript_path: Path | str | None = None,
    model: str | None = None,
    language: str | None = None,
    windowed: bool = False,
    window: float = asr.WINDOW,
    overlap: float = asr.OVERLAP,
) -> dict[str, Any]:
    """Transcribe a finished render and diff it against what the timeline says.

    proofcut already knows the words the timeline should play — every clip's
    transcript mapped through the accumulated edit, exactly as captions are
    placed. This transcribes the render itself and compares the two word
    sequences.

    It is the only check that catches a retake the transcript never contained:
    whisper collapses an immediate repeat, so a phrase said twice can appear
    once in the source transcript and be cut once, leaving the second take in
    the render with nothing in proofcut's index pointing at it (HISTORY.md § 2). The
    render's own transcript has it twice; the timeline expects it once; the diff
    says so.

    **`windowed=True` closes this check's own blind spot.** A single pass over
    the render is still one whisper transcription, and it collapses a repeat in
    the render for exactly the reason it collapsed one in the source: three
    retakes survived a correctly run single-pass verify of the Scream v1 export.
    Windowed mode transcribes in short overlapping windows instead, where a
    segment ends before it can swallow a second take, and it defaults to a
    *smaller* model on purpose — see `asr.transcribe_windowed`. It costs one
    whisper run over ~1.4x the audio, so it is opt-in rather than the default.

    `loud_gaps` is reported either way and answers to neither transcript: it is
    the render's own energy envelope, masked by the words that were heard, and
    a hole in the word map that holds sound anyway is a noise or a take nothing
    wrote down.

    `transcript_path` skips ASR and uses an existing transcript of the render —
    the re-run, debugging and test path, and how a transcript produced on a
    machine with a spare GPU gets used here.

    **A configured head is accounted for, not ignored.** `render` is assumed
    to be a full export of *this* project — head, body and tail together, the
    same assumption `check_frames`/`film_check` make about their own
    `target`/`reference` — so once a head carries real dialogue, its words
    transcribe at the front of the heard sequence with nothing in `expected`
    (Edit-only words) to match them against, which would otherwise read as a
    spurious leading insertion on every run. Every heard word starting before
    `head_seconds` is dropped before the diff and the count is reported as
    `head_words_trimmed` — visible and auditable, the `unspoken`/
    `hallucinated_words` convention: a real content problem inside the head's
    own dialogue must stay visible, just not counted against the body.
    """
    project = Project.open(path)
    edit = _load_edit(project)
    transcripts = _transcripts_for(project, clip_id)
    transcripts, unspoken = _spoken_transcripts(project, transcripts)

    placed, cut = captions.place(edit, transcripts)
    if not placed:
        raise vfy.VerifyError(
            "no transcribed word survives on the timeline — there is nothing "
            "for the render to be checked against"
        )
    expected = vfy.tokens(word.text for word in placed)

    render_path = Path(render).expanduser()
    result: dict[str, Any] = {}
    # Resolved here rather than in the signature because the right default
    # differs by mode: a single pass wants the most accurate model available,
    # a windowed pass wants the one least inclined to tidy a stutter away.
    model = model or (asr.WINDOWED_MODEL if windowed else asr.DEFAULT_MODEL)

    if transcript_path is not None:
        # Named for what produced the words, not for what was asked for: a
        # supplied transcript is whatever pass made it, and reporting it as
        # "windowed" because the flag was set would be a lie a reader acts on.
        result["mode"] = "supplied"
        heard_transcript = tx.load(transcript_path, clip_id="render")
    elif windowed:
        result["mode"] = "windowed"
        payload = asr.transcribe_windowed(
            render_path,
            window=window,
            overlap=overlap,
            model=model,
            language=language or _shared_language(transcripts),
        )
        heard_transcript = tx.parse_whisper(
            payload, clip_id="render", origin=f"whisper:{model} windowed"
        )
        result.update(
            {
                "windows": payload["windows"],
                "silent_windows": payload["silent_windows"],
                "hallucinated_words": payload["hallucinated_words"],
                "window": window,
                "overlap": overlap,
            }
        )
        # A distinct name from the single-pass cache: the two are different
        # readings of the same file and overwriting one with the other would
        # make `--transcript` reuse silently ambiguous.
        cached = project.verify_dir / f"{render_path.stem}.windowed.json"
        tx.save(heard_transcript, cached)
        result["heard_transcript"] = str(cached)
    else:
        result["mode"] = "single-pass"
        payload = asr.transcribe(
            render_path, model=model, language=language or _shared_language(transcripts)
        )
        # Whisper returns an empty `segments` list rather than failing when it
        # hears no speech, and `parse_whisper` would then blame the missing
        # word timestamps — which were requested. Say what actually happened.
        if not payload.get("words") and not payload.get("segments"):
            raise vfy.VerifyError(
                f"whisper heard no speech at all in {render_path.name}. Either the "
                "render has no dialogue on it — check that the export kept the "
                "audio track — or the wrong file was passed."
            )
        heard_transcript = tx.parse_whisper(
            payload, clip_id="render", origin=f"whisper:{model}"
        )
        # Reported in both modes now, and it was the single-pass mode that
        # needed it: a run-away tail read as words the render does not play,
        # which is a `verify` miss rather than a `verify` finding.
        result["hallucinated_words"] = payload.get("hallucinated_words", 0)
        # Keep the expensive artifact, but never read it back automatically: a
        # re-render under the same filename would then verify against the
        # previous render's audio and pass. Reuse is explicit, via
        # `transcript_path`.
        cached = project.verify_dir / f"{render_path.stem}.json"
        tx.save(heard_transcript, cached)
        result["heard_transcript"] = str(cached)

    # A head's own words trimmed from the *front* of the heard sequence
    # before tokenizing — see the docstring's "A configured head" note.
    # `HEAD_TRIM_TOLERANCE` past the nominal boundary, not the boundary
    # itself: whisper's own word timestamps are not frame-exact, so a word
    # that is really the head's own last word but timed a beat late must
    # still count as the head's, not arrive as a spurious leading body word.
    head_seconds = _head_seconds(project)
    heard_words = list(heard_transcript.words)
    head_words_trimmed = 0
    if head_seconds:
        cutoff = head_seconds + HEAD_TRIM_TOLERANCE
        kept = [w for w in heard_words if w.start >= cutoff]
        head_words_trimmed = len(heard_words) - len(kept)
        heard_words = kept

    heard = vfy.tokens(word.text for word in heard_words)

    result.update(
        {
            "render": str(render_path),
            "clips": sorted(transcripts),
            "expected_words": len(expected),
            "heard_words": len(heard),
            "words_cut_from_transcript": cut,
            # Reported beside the diff and never folded into it: this check's
            # expectation was *shortened* by hand, and a render checked against
            # a shortened expectation has to say so or the mark becomes a way
            # to make a real miss disappear.
            **unspoken,
            "timeline_duration": edit.duration,
            "head_seconds": head_seconds,
            "head_words_trimmed": head_words_trimmed,
            **vfy.compare(expected, heard),
        }
    )

    # Informational only, and never a failure: a render with a card hold or a
    # music tail legitimately runs past the last spoken word.
    try:
        result["render_duration"] = media.probe(render_path).duration
    except media.MediaError:
        pass

    # Likewise never fatal. The envelope is a second opinion on a diff that
    # already stands on its own, and a render proofcut cannot decode should not
    # cost the caller the transcription it just paid minutes for.
    try:
        result["loud_gaps"] = energy.unaccounted_sound(
            render_path, [(w.start, w.end) for w in heard_transcript.words]
        )
    except energy.EnergyError as exc:
        result["loud_gaps"] = {"error": str(exc)}

    return result


def _time_overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    """Whether `[a_start, a_end)` and `[b_start, b_end)` share any instant —
    `finish_check`'s own test for "does this fall inside a declared prepend
    or hold span", both for the blackdetect fault logic and the heard-word
    filter ahead of the windowed diff."""
    return a_start < b_end and b_start < a_end


def _boundary_recheck(
    dropped: Sequence[dict[str, Any]],
    heard_words: Sequence[Any],
    final_duration: float,
    *,
    pad: float,
    transcribe: Callable[[float, float], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`finish_check`'s step 6, pulled out for testability without ffmpeg or
    whisper — `picture.blackdetect`/`parse_blackdetect`'s own split.

    Every `dropped` entry (a `verify.compare` result, so each carries
    `at_heard_word`) is re-cut `pad` seconds past its own heard-side
    neighbours and handed to `transcribe(start, length) -> str` — injected
    rather than called directly, so this can be exercised against a canned
    function that returns fixed text for a fixed span
    (`test_verify.py`'s own discipline for `compare`, one level up).
    `heard_words` needs only `.start`/`.end` on each item — a real
    `Transcript.words` tuple, or a hand-built stand-in in a test.

    Returns `(missing, boundary_misses)` — every dropped entry ends up in
    exactly one. **Getting the direction backwards silently turns every real
    defect into "recovered"**: a recheck that *fails* to find the missing
    text close to the padded span means the miss is real (`missing`); one
    that *does* find it (even reworded — `vfy.SIMILAR`'s own tolerance for a
    second take) means the windowed pass lost it at a stitch and this
    recovered it (`boundary_misses`, not a fault).
    """
    missing: list[dict[str, Any]] = []
    boundary_misses: list[dict[str, Any]] = []
    for dropped_entry in dropped:
        j1 = dropped_entry["at_heard_word"]
        raw_start = heard_words[j1 - 1].end if j1 > 0 else 0.0
        raw_end = heard_words[j1].start if j1 < len(heard_words) else final_duration
        span_start = max(0.0, raw_start - pad)
        span_end = min(final_duration, raw_end + pad)
        recheck_entry = {**dropped_entry, "recheck_start": span_start, "recheck_end": span_end}
        if span_end <= span_start:
            recheck_entry["recheck_error"] = "nothing to re-cut — the span is empty"
            missing.append(recheck_entry)
            continue
        try:
            recheck_text = transcribe(span_start, span_end - span_start)
        except (asr.ASRError, subprocess.CalledProcessError) as exc:
            recheck_entry["recheck_error"] = str(exc)
            missing.append(recheck_entry)
            continue
        recheck_entry["recheck_text"] = recheck_text
        recheck_tokens = vfy.tokens([recheck_text])
        missing_tokens = vfy.tokens([dropped_entry["text"]])
        _, ratio = vfy._closest_run(missing_tokens, recheck_tokens)
        recheck_entry["recheck_similarity"] = round(ratio, 3)
        if ratio >= vfy.SIMILAR:
            boundary_misses.append(recheck_entry)
        else:
            missing.append(recheck_entry)
    return missing, boundary_misses


def finish_check(
    path: Path | str,
    final: Path | str,
    *,
    holds: Sequence[Mapping[str, Any]] | None = None,
    prepend_seconds: float | None = None,
    fps: float | None = None,
    duration_tolerance: float = 0.5,
    pix_th: float = 0.10,
    black_min_duration: float = 0.0,
    windowed_model: str | None = None,
    window: float = asr.WINDOW,
    overlap: float = asr.OVERLAP,
    recheck_pad: float = asr.WINDOW,
    language: str | None = None,
    clip_id: str | None = None,
    transcript_path: Path | str | None = None,
) -> dict[str, Any]:
    """Check a **delivered** file against this project's own timeline —
    `verify_longlegs.py`, generalized into a first-class op rather than one
    project's script. `final` is whatever an external mix pass produced (a
    cold open and/or holds concatenated onto one of proofcut's own renders,
    entirely outside `export`), not a render this project made itself —
    `verify`/`check_frames`/`check_black`/`film_check` are the checks for
    that.

    Every number this reports is in **`final`'s own absolute seconds**:
    `prepend_seconds` (a cold open or bumper glued on before the Edit's own
    first frame) and each hold's `start`/`start + length` all describe
    positions in `final`, not Edit time — `locate`'s two-clock rule.

    **WORK-ORDERS ruling 5 — holds and a head are project state now.**
    `prepend_seconds` defaults to the stored head's own length
    (`_head_seconds`) when left unset (`None`); pass `0.0` explicitly to
    check a file with no prepend even though this project has a head
    configured. `holds` defaults to this project's stored `HOLDS_KEY` spans,
    resolved live against the current edit and offset by the resolved
    `prepend_seconds` (`_resolved_hold_spans`); pass an explicit list (`[]`
    included) to check against a caller-supplied set instead — `film_check`'s
    `reference` argument's own shape.

    Eight steps, each reported and none individually fatal to the others —
    `hold_check`'s own stance, because this is a listening check on a file
    that already exists:

    1. **Streams & duration.** `media.stream_inventory(final)` (a chapter
       list, a stray non-picture/non-sound stream, a stream that outruns the
       picture) plus `final`'s total duration against
       `_frame_total_with_tail(...)/rate + prepend_seconds`, within
       `duration_tolerance`.
    2. **Loudness.** `finish.loudness(final)` — report only; no established
       target LUFS to fault against.
    3. **Blackdetect**, called directly (`picture.blackdetect`, never
       `ops.check_black`) — `check_black`'s own tail-frame reasoning is
       calibrated to an un-prepended proofcut render and does not transfer once
       `final` has a cold open glued onto the front. A run is a fault unless
       it overlaps `[0, prepend_seconds)` or a declared hold's own span.
    4. **Per-hold transcription + seam.** Each hold's own span, transcribed
       on its own (`_transcribe_span`, defaulting every ASR call this makes
       to `asr.WINDOWED_MODEL` — pipeline.md's "believe the smaller model"
       is directional, not just a default) and reported as text with no
       expected script to diff against — a hold plays the film's own
       dialogue, not the VO. `finish.hold_seams` alongside, at every
       non-ducked hold's in/out and (when `prepend_seconds > 0`) the
       prepend-to-body join.
    5. **Windowed VO diff.** `asr.transcribe_windowed` (or a supplied
       `transcript_path`, `verify`'s own escape hatch), with every heard
       word overlapping the prepend span or a hold span filtered out
       **before** the diff — `final`'s own non-VO audio would otherwise
       inflate the diff with noise that is not a defect, and risk a hold's
       vocabulary coincidentally shifting `SequenceMatcher`'s alignment
       elsewhere in the sequence.
    6. **The boundary recheck.** Every `dropped` entry `vfy.compare` reports
       is re-cut (padded `recheck_pad` past its own heard-side neighbours,
       via `verify.compare`'s `at_heard_word`) and re-transcribed on its
       own. Close to the missing text → `boundary_misses` (the windowed
       pass lost it at a stitch, recovered here, not a fault); not close →
       stays in `missing`, a real fault — **getting this direction backwards
       silently turns every real defect into "recovered"**.
    7. **Self-repeats.** `verify.find_adjacent_repeats` over the same
       filtered heard sequence — proofcut's existing tool, applied to a
       render's own transcript for the first time.
    8. **Aggregate**, and `finishlog.append` — the artifact-keyed log
       `proofcut review serve`'s WARN badge joins against by sha256.
    """
    project = Project.open(path)
    final_path = Path(final).expanduser()
    if not final_path.is_file():
        raise finish.FinishError(f"no such file to check: {final_path}")

    edit = _load_edit(project)
    if not edit.segments:
        raise ProjectError(
            "the timeline is empty — there is nothing to check a delivered file against"
        )

    rate = float(fps) if fps else _export_fps(_clips_by_id(project))
    head_seconds = _head_seconds(project)
    prepend = float(prepend_seconds) if prepend_seconds is not None else head_seconds
    if prepend < 0:
        raise ProjectError(f"prepend_seconds must not be negative, not {prepend!r}")

    hold_errors: list[dict[str, Any]] = []
    resolved_holds: list[dict[str, Any]]
    if holds is None:
        resolved_holds, hold_errors = _resolved_hold_spans(project, edit, rate, head_seconds)
    else:
        resolved_holds = []
        for i, h in enumerate(holds):
            try:
                resolved_holds.append(
                    {
                        "name": str(h.get("name", f"hold {i}")),
                        "start": float(h["start"]),
                        "length": float(h["length"]),
                        "ducked": bool(h.get("ducked", False)),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProjectError(
                    f"holds[{i}] must hold at least numeric 'start' and 'length', not {h!r}"
                ) from exc

    covering: list[tuple[float, float]] = []
    if prepend > 0:
        covering.append((0.0, prepend))
    for span in resolved_holds:
        covering.append((span["start"], span["start"] + span["length"]))

    # -- 1. streams & duration ----------------------------------------------
    expected_duration = _frame_total_with_tail(project, edit, rate) / rate
    want_duration = expected_duration + prepend
    final_info = media.probe(final_path)
    stream_report = media.stream_inventory(final_path)
    duration_delta = final_info.duration - want_duration
    duration_agrees = abs(duration_delta) <= duration_tolerance

    # -- 2. loudness — report only --------------------------------------------
    try:
        loudness_report = finish.loudness(final_path)
    except finish.FinishError as exc:
        loudness_report = {
            "integrated": None,
            "lra": None,
            "true_peak": None,
            "error": str(exc),
        }

    # -- 3. blackdetect, called directly --------------------------------------
    black_runs: list[dict[str, Any]] = []
    black_notes: list[str] = []
    if not final_info.has_video:
        black_notes.append(
            "this file has no video stream, so there is no picture to scan for black."
        )
    else:
        for run in picture.blackdetect(final_path, pix_th=pix_th, min_duration=black_min_duration):
            explained = any(_time_overlaps(run["start"], run["end"], lo, hi) for lo, hi in covering)
            black_runs.append({**run, "explained": explained})
    black_faults = sum(1 for run in black_runs if not run["explained"])

    # -- 4. per-hold transcription + seam -------------------------------------
    model = windowed_model or asr.WINDOWED_MODEL
    hold_items: list[dict[str, Any]] = []
    seam_marks: list[tuple[str, float]] = []
    seam_owners: list[dict[str, Any]] = []
    for span in resolved_holds:
        entry: dict[str, Any] = dict(span)
        try:
            entry["heard"] = _transcribe_span(
                final_path, span["start"], span["length"], model=model
            )
        except (asr.ASRError, subprocess.CalledProcessError) as exc:
            entry["heard"] = None
            entry["heard_error"] = str(exc)
        if not span["ducked"]:
            seam_marks.append((f"{span['name']} in", span["start"]))
            seam_marks.append((f"{span['name']} out", span["start"] + span["length"]))
            seam_owners.append(entry)
            seam_owners.append(entry)
        hold_items.append(entry)

    prepend_seam: dict[str, Any] | None = None
    if prepend > 0:
        prepend_seam = {"name": "prepend -> body"}
        seam_marks.append(("prepend -> body", prepend))
        seam_owners.append(prepend_seam)

    if seam_marks:
        seams = finish.hold_seams(final_path, seam_marks)
        for owner, seam in zip(seam_owners, seams, strict=True):
            owner.setdefault("seams", []).append(seam)

    seam_owner_list = [*hold_items, *([prepend_seam] if prepend_seam is not None else [])]
    seam_faults = sum(
        1 for owner in seam_owner_list for seam in owner.get("seams", []) if seam["fault"] is not None
    )

    # -- 5. windowed VO diff, prepend/hold words filtered first --------------
    transcripts = _transcripts_for(project, clip_id)
    transcripts, unspoken = _spoken_transcripts(project, transcripts)
    placed, cut = captions.place(edit, transcripts)
    if not placed:
        raise vfy.VerifyError(
            "no transcribed word survives on the timeline — there is nothing "
            "for finish_check to compare final against"
        )
    expected = vfy.tokens(word.text for word in placed)

    asr_result: dict[str, Any] = {}
    if transcript_path is not None:
        asr_result["mode"] = "supplied"
        heard_transcript = tx.load(transcript_path, clip_id="render")
    else:
        asr_result["mode"] = "windowed"
        payload = asr.transcribe_windowed(
            final_path,
            window=window,
            overlap=overlap,
            model=model,
            language=language or _shared_language(transcripts),
        )
        heard_transcript = tx.parse_whisper(
            payload, clip_id="render", origin=f"whisper:{model} windowed"
        )
        asr_result.update(
            {
                "windows": payload["windows"],
                "silent_windows": payload["silent_windows"],
                "hallucinated_words": payload["hallucinated_words"],
                "window": window,
                "overlap": overlap,
            }
        )
        cached = project.verify_dir / f"{final_path.stem}.finish-check.windowed.json"
        tx.save(heard_transcript, cached)
        asr_result["heard_transcript"] = str(cached)

    heard_words_all = list(heard_transcript.words)
    filtered_words = [
        w
        for w in heard_words_all
        if not any(_time_overlaps(w.start, w.end, lo, hi) for lo, hi in covering)
    ]
    words_filtered = len(heard_words_all) - len(filtered_words)
    heard = vfy.tokens(w.text for w in filtered_words)

    diff = vfy.compare(expected, heard)

    # -- 6. the boundary recheck ----------------------------------------------
    missing, boundary_misses = _boundary_recheck(
        diff["dropped"],
        filtered_words,
        final_info.duration,
        pad=recheck_pad,
        transcribe=lambda start, length: _transcribe_span(final_path, start, length, model=model),
    )

    # -- 7. self-repeats --------------------------------------------------------
    repeats = vfy.find_adjacent_repeats(heard)

    # -- 8. aggregate & log -----------------------------------------------------
    faults = (
        len(stream_report["faults"])
        + (0 if duration_agrees else 1)
        + black_faults
        + seam_faults
        + len(hold_errors)
        + len(missing)
        + len(repeats)
    )
    ok = faults == 0
    digest = _sha256(final_path)

    summary = {
        "duration_agrees": duration_agrees,
        "stream_faults": len(stream_report["faults"]),
        "loudness": {
            "integrated": loudness_report.get("integrated"),
            "true_peak": loudness_report.get("true_peak"),
        },
        "black_runs": len(black_runs),
        "black_faults": black_faults,
        "hold_errors": len(hold_errors),
        "seam_faults": seam_faults,
        "missing": len(missing),
        "boundary_misses": len(boundary_misses),
        "repeats": len(repeats),
    }
    finishlog.append(
        project, final=str(final_path), sha256=digest, faults=faults, ok=ok, summary=summary
    )

    return {
        "project": str(project.root),
        "final": str(final_path),
        "sha256": digest,
        "prepend_seconds": prepend,
        "duration": {
            "final": final_info.duration,
            "expected": expected_duration,
            "want": want_duration,
            "delta": duration_delta,
            "tolerance": duration_tolerance,
            "agrees": duration_agrees,
        },
        "streams": stream_report,
        "loudness": loudness_report,
        "black": {
            "has_video": final_info.has_video,
            "pix_th": pix_th,
            "min_duration": black_min_duration,
            "runs": black_runs,
            "faults": black_faults,
            **({"notes": black_notes} if black_notes else {}),
        },
        "holds": hold_items,
        "hold_errors": hold_errors,
        "prepend_seam": prepend_seam,
        **asr_result,
        "expected_words": len(expected),
        "heard_words": len(heard),
        "words_filtered": words_filtered,
        "clips": sorted(transcripts),
        "words_cut_from_transcript": cut,
        **unspoken,
        "similarity": diff["similarity"],
        "diff": diff["diff"],
        "repeated": diff["repeated"],
        "missing": missing,
        "boundary_misses": boundary_misses,
        "repeats": repeats,
        "faults": faults,
        "ok": ok,
    }


# -- deriving a project ---------------------------------------------------
#
# A reel is a *derived project*: a film, cut down to the span someone watched,
# at whatever shape the feed wants. PLAN.md § Three uncosted parity items found
# that the choosing needs nothing built — `cut_by_time` already takes the
# seconds an export plays at — and that what is missing sits one level up. The
# canvas is project state and the cuts are destructive, so the copy is
# mandatory, and it was a `cp -a` done by hand.
#
# Almost nothing here is transformation. Descriptions index the source and a
# reframe is a rect in source pixels refit at render time, so neither a cut
# nor a canvas change can invalidate one — the roadmap's core property paying
# out rather than work this op does.
#
# Two things do need handling, and both were found by deriving a reel of the
# real film rather than by reasoning about one. Cards are rasterised rather
# than derived, so `card_reauthor` runs at the end. And a cue, though it is
# word-indexed and so cannot be *invalidated* by a cut, can be orphaned by
# one: a reel removes most of the film, which takes most of the cues' words
# with it, and `build_shots` refuses a whole projection on a single orphan.
# `_reel_orphan_cues`. Pruning them is then what strands the survivors, since
# the cursor deciding what each shot shows is per-asset and cumulative —
# `_reel_cue_pins`, the quieter half of the same problem.

#: How long a platform will let a vertical post run. Reported beside the
#: reel's own duration and never enforced: it is why a reel exists at all
#: (a 5:36 film reaches no feed), and it is exactly the kind of fact that
#: goes stale in a codebase, so it is a note to whoever is reading rather
#: than a refusal to be worked around.
PLATFORM_CAP = 180.0


def _reel_media(source: Project, reel: Project, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Point a derived project's clips at the film's own media bytes.

    Never a copy: a reel of a five-minute film would duplicate every gigabyte
    that went into making one. Each `media/` and `cache/attenuated/` entry
    becomes a symlink to the file the *film* resolves, so `media_path()` on the
    reel and on the film return the same bytes.

    **Every key `media_path()` prefers, not just `media/`.** It resolves
    `attenuated` → `mixed` → `stripped` → `media`, so carrying `media/` alone
    would give the reel a render at full noise with nothing in the manifest
    saying so — the shape of bug
    `test_export_renders_the_attenuated_copy_not_the_original` exists for,
    pointing the other way. `mixed` and `stripped` join them for the same
    reason and a worse failure: the derived manifest is copied wholesale, so
    a two-mic or chaptered clip's `mixed`/`stripped` entry would name a
    `cache/mixed/`/`cache/stripped/` file that was never linked into the reel
    and every op resolving that clip would hit a path with nothing at it.
    **This tuple is the list of keys `media_path()` reads; adding one there
    without adding it here is the whole bug.**

    Falls back to writing the film's absolute path into the derived manifest
    where the filesystem will not take a symlink — the NAS case that already
    makes `media/` optional (wiki `files.md`). `media_path()` reads that
    correctly without a branch, since a `/`-joined absolute path is itself,
    and `mutates the manifest it was handed` is the point rather than a side
    effect. It does not fall back to *dropping* the key: that would resolve
    through `clip["source"]`, which is the original import path and so the
    un-attenuated file.
    """
    linked: list[dict[str, Any]] = []
    for clip in manifest.get("clips", []):
        for key in ("media", "attenuated", "mixed", "stripped"):
            entry = clip.get(key)
            if not entry:
                continue
            target = (source.root / entry).resolve()
            link = reel.root / entry
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(target)
                how = "symlink"
            except OSError:
                clip[key] = str(target)
                how = "absolute"
            linked.append(
                {
                    "clip_id": clip.get("clip_id"),
                    "key": key,
                    "how": how,
                    "target": str(target),
                    # Reported rather than refused, the way `media_path()`
                    # itself does not check: a film with one missing file
                    # should still derive, and a dangling link that says so
                    # beats one that does not.
                    "missing": not target.is_file(),
                }
            )
    return linked


def _reel_suspect_edges(
    project: Project, edit: tl.Edit, start: float, end: float
) -> list[dict[str, Any]]:
    """Suspect-duration words at the two edges a reel *keeps*.

    `cut_by_time` flags every suspect word a removed span overlaps. That is
    right for an ordinary cut, where the span and its boundary are nearly the
    same thing, and it is useless here: a reel removes most of the film, so
    one suspect word anywhere in it flags the operation whatever the reel
    keeps. Measured on the film this was written against — a 44s reel of a
    5:36 cut flagged fifteen, none of them within a hundred seconds of the
    reel. A guard that has to be suppressed every time guards nothing.

    The edges that can hide a retake are the two the reel keeps. An inflated
    duration there is a word that does not end where it claims, so the reel
    opens or closes on material from the wrong take (HISTORY.md § Suspect word
    durations). The other two edges are the film's own head and tail, and
    those hide nothing.
    """
    edges: list[tuple[str, float, str, float]] = []
    if start >= tl.MIN_SEGMENT:
        clip_id, _, src_end = edit.source_spans(0.0, start)[-1]
        edges.append((clip_id, src_end, "start", start))
    if edit.duration - end >= tl.MIN_SEGMENT:
        clip_id, src_start, _ = edit.source_spans(end, edit.duration)[0]
        edges.append((clip_id, src_start, "end", end))

    parsed_by_clip: dict[str, tx.Transcript | None] = {}
    found: list[dict[str, Any]] = []
    for clip_id, at, which, timeline_at in edges:
        if clip_id not in parsed_by_clip:
            try:
                parsed_by_clip[clip_id] = _transcript(project, clip_id)
            except tx.TranscriptError:
                parsed_by_clip[clip_id] = None
        parsed = parsed_by_clip[clip_id]
        if parsed is None:
            continue
        for item in _suspect_durations(parsed):
            word = parsed.words[item["index"]]
            # An *instant* test, so both ends count: an edge landing exactly on
            # a word's own boundary is the case this is looking for, and the
            # half-open rule the spans use would call it a miss (CLAUDE.md).
            if word.start <= at <= word.end:
                found.append(
                    {
                        **item,
                        "clip_id": clip_id,
                        "edge": which,
                        "timeline_at": timeline_at,
                        "source_at": at,
                    }
                )
    return found


def _reel_orphan_cues(
    project: Project, edit: tl.Edit, start: float, end: float
) -> list[dict[str, Any]]:
    """Cues whose word the derivation leaves off the timeline.

    A cue says "from this word onward, show this asset", so a cue whose word
    is gone points at nothing, and `build_shots` refuses the *whole*
    projection on one — rightly, since in a film that is someone having cut
    the line a picture was hung on. A reel cuts most of the film on purpose,
    so it orphans nearly every cue: on the real one, keeping 44s of 5:36 left
    30-odd of them and the derived project could not project shots at all.
    It passed every check and was unrenderable.

    So the derived cue table is the surviving cues, and this names the rest —
    quietly dropping them would be dropping a picture the reel was going to
    have. Resolved against the *film's* timeline, before any cut, which is the
    same question one asked afterwards: what survives the two cuts is exactly
    what mapped into `[start, end)` to begin with.
    """
    parsed_by_clip: dict[str, tx.Transcript | None] = {}
    orphans: list[dict[str, Any]] = []
    for cue in project.read_manifest().get("cues", []):
        clip_id = cue["clip_id"]
        if clip_id not in parsed_by_clip:
            try:
                parsed_by_clip[clip_id] = _transcript(project, clip_id)
            except tx.TranscriptError:
                parsed_by_clip[clip_id] = None
        parsed = parsed_by_clip[clip_id]
        if parsed is None:
            # Nothing to resolve the word index against. Left in place rather
            # than guessed at: `build_shots` owns that refusal and names it
            # better than a guess here would.
            continue
        word = parsed.words[cue["word_index"]]
        span = edit.timeline_span(clip_id, word.start, word.end)
        if span is None or not (span[0] < end and span[1] > start):
            orphans.append({**cue, "text": word.text})
    return orphans


def _reel_cue_pins(project: Project) -> tuple[dict[tuple[str, int], float], str | None]:
    """Where in its asset each of the *film's* shots actually reads.

    The counterpart to `_reel_orphan_cues`, and the same class of failure one
    level further in: pruning is what stops the derived project refusing, and
    pinning is what stops it rendering a different film.

    `plan_picture`'s per-asset cursor carries on from where the previous shot
    left it, so what a shot shows depends on every shot *before* it. A
    derivation drops the ones it cut, which empties that cursor — every
    survivor then replays its asset from the head, and the reel's picture is
    not the film's picture over the same seconds. It is the silent kind: the
    frames are real, the projection is valid, `status` and `verify` and
    `check_frames` all agree, and only a watch against the film says otherwise
    (CLAUDE.md; HISTORY.md § The teaser, re-cut).

    So the in-points are read off the film's own plan and written onto the
    survivors, which is exactly what `src_start` means — somebody asked for
    *that* moment — and what makes `plan_picture` refuse rather than rewind
    them. Stills are left alone: a card is a held frame with no playhead, and
    `plan_picture` refuses a pin on one.

    A refusal from the film's own projection comes back as a string rather
    than raising. A film that cannot project shots cannot be exported either,
    so the reel is not made newly wrong by deriving from one — but it is the
    reason its cues arrive unpinned, and that has to be said rather than
    inferred from an empty list.
    """
    rate = _export_fps(_clips_by_id(project))
    try:
        shots, _ = _picture_plan(project, rate)
    except _PICTURE_REFUSALS as exc:
        return {}, str(exc)
    return {
        (shot["clip_id"], shot["word_index"]): round(float(shot["src_start"]), 3)
        for shot in shots
        if not shot.get("is_image")
    }, None


def _reel_cue_table(
    cues: list[dict[str, Any]],
    orphans: list[dict[str, Any]],
    pins: dict[tuple[str, int], float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The derived cue table, and the in-points this derivation had to add.

    An existing `src_start` is never overwritten: the film's plan agrees with
    it by construction — a pinned shot has no cursor — so there is nothing to
    add, and a cue somebody pinned by hand is the last thing a derivation
    should be rewriting.
    """
    orphaned = {(cue["clip_id"], cue["word_index"]) for cue in orphans}
    kept: list[dict[str, Any]] = []
    pinned: list[dict[str, Any]] = []
    for cue in cues:
        key = (cue["clip_id"], cue["word_index"])
        if key in orphaned:
            continue
        if cue.get("src_start") is None and key in pins:
            cue = {**cue, "src_start": pins[key]}
            pinned.append(
                {
                    "clip_id": cue["clip_id"],
                    "word_index": cue["word_index"],
                    "asset": cue["asset"],
                    "src_start": cue["src_start"],
                }
            )
        kept.append(cue)
    return kept, pinned


def reel(
    path: Path | str,
    dest: Path | str,
    *,
    start: float,
    end: float,
    canvas: str | None = None,
    name: str | None = None,
    confirm_suspect: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    """Derive a new project holding `[start, end)` of this one's timeline.

    The times are the seconds *an export plays at* — what a human reports
    after a watch — and they are the span to **keep**, which is the only place
    in proofcut that reads that way round. Everything else here cuts; a reel is
    named by what survives, so the head and the tail are what get removed,
    through `cut_by_time` and therefore through the same `Edit.remove` path
    every other cut takes. Nothing new decides anything about the timeline.

    `canvas` reshapes the derived project only, which is why deriving is what
    makes a reel safe: `canvas` is project state, so setting it on the film to
    take one vertical render would leave the film swapped after a render
    nobody kept — the same failure `tiktok-reels` refuses at one level down
    (HISTORY.md § `tiktok-reels`). The copy is the fix, not a convenience.

    The media is linked rather than copied (`_reel_media`), so a reel costs
    its manifest and its transcripts rather than its footage. What comes with
    it is what indexes the *source* — the transcripts, and the cards as they
    stand. What stays behind is everything that described the film's own
    renders or its undo stack: `cache/verify/`, `cache/frames/`,
    `cache/history/`, `renders/`. `cache/waveform/` stays behind too, being the
    one derived thing that rebuilds itself from media that has not changed.

    The derived cue table is the cues whose word the reel still has.
    `cues_dropped` names the rest, and each one is a picture the reel will not
    have; without the pruning the derived project cannot project shots at all,
    since `build_shots` refuses the whole projection on one orphan.

    **And every survivor is pinned to the in-point the film gave it**
    (`_reel_cue_pins`), because the pruning empties `plan_picture`'s per-asset
    cursor: unpinned, each survivor replays its asset from the head, which is
    a different film at exit 0 with every check agreeing. `cues_pinned` names
    them, and `pins_error` says why there are none where the film's own
    projection refuses.

    Cards are re-authored at the end because they are the only project state a
    canvas change cannot re-derive (PLAN.md § Aspect swap, finding 5). A card
    with no record cannot be re-authored by anything, so `cards_unrecorded`
    rides in the result: on the film this was written against, that list is
    twelve long, and a reel of it is correct in every other respect while its
    cards are still 16:9.

    The suspect-duration guard is applied at the two edges the reel keeps
    rather than over everything it removes (`_reel_suspect_edges`), and
    `confirm_suspect` answers *that* question. This is the one place a reel
    does not simply defer to `cut_by_time`, and the reason is measured rather
    than argued.

    **A configured `tail` (an end card, a bumper) is never inherited** —
    `tail_dropped` reports what the film had, if anything, and the derived
    project gets none, the same "never" `cues_pinned` proves out for pruning
    (PLAN.md § Tail time — the design note: taken 2026-08-12, "a derivation
    carries nothing and reports"). A bumper is a decision about *this* cut,
    not a fact a span of the film carries with it — a teaser derived from an
    essay should not silently end on the essay's own end card, and register
    is the reason it is worth naming rather than just dropping: the film's
    register and a reel's are allowed to differ on purpose. **A configured
    `head` (a cold open) follows the identical rule at the other end** —
    `head_dropped` reports what the film had, and the derived project gets
    none, for the same reason: a reel derived from an essay should not
    silently open on the essay's own cold open.

    `plan=True` resolves the whole thing — the spans, the clips that would be
    linked, what the cut would remove — and creates nothing.
    """
    source = Project.open(path)
    edit = _load_edit(source)
    duration = edit.duration
    start, end = float(start), float(end)

    if start < 0:
        raise tl.TimelineError(f"a reel starts inside the timeline, not at {start}")
    if end <= start:
        raise tl.TimelineError(f"a reel keeps [start, end) and {start} is not before {end}")
    if end > duration + tl.MIN_SEGMENT:
        raise tl.TimelineError(
            f"this timeline is {duration:.3f}s long, so it has nothing at {end}s to keep. "
            "The times are the seconds an export plays at — check them against "
            "`info`'s timeline_duration, or against the render you watched"
        )
    end = min(end, duration)

    # The head and the tail, resolved together against the timeline as it
    # stands: `cut_by_time` applies a list of spans against the pre-cut state,
    # which is exactly what makes two ends of one watch stay valid together.
    # A sliver shorter than a segment is dropped rather than asked for, since
    # `Edit.remove` would decline it and the arithmetic would then disagree.
    cuts: list[list[float]] = []
    if start >= tl.MIN_SEGMENT:
        cuts.append([0.0, start])
    if duration - end >= tl.MIN_SEGMENT:
        cuts.append([end, duration])

    # Both resolved against the film, before anything is created, so a refusal
    # leaves nothing behind.
    orphans = _reel_orphan_cues(source, edit, start, end)
    # Read off the film, because that is the only place the answer exists: the
    # cursor `plan_picture` walks is emptied by the pruning above, so a
    # survivor arriving unpinned replays its asset from the head
    # (`_reel_cue_pins`).
    pins, pins_error = _reel_cue_pins(source)
    kept_cues, pinned = _reel_cue_table(source.read_manifest().get("cues", []), orphans, pins)
    # Read here, before anything is created, the same as `cues_dropped` above
    # — a `tail` is never inherited (taken 2026-08-12), so this is what the
    # film had, reported rather than silently left behind.
    tail_dropped = _stored_tail(source)
    # `tail_dropped`'s own rule, mirrored at the other end: a cold open is a
    # decision about *this* cut's own opening beat, not a fact a span of the
    # film carries forward into a teaser — a reel derived from an essay
    # should not silently open on the essay's own cold open.
    head_dropped = _stored_head(source)
    # The music bed follows the tail's rule, not the cue table's: it is
    # project state beside `Edit`, and a derivation inherits nothing — it
    # reports (PLAN.md § The A2 music lane, what the note does not settle,
    # item 1). Unlike a picture cue the bed cannot simply be kept where its
    # word survives: the film's bed has been playing for however long by the
    # reel's first second, and a reel re-opening it from its head is the
    # `cues_pinned` shape with no pin to give it. Dropped and named, so the
    # reel's author decides — the future design can do better.
    music_dropped = _stored_music(source)
    # A hold ties a VO gap to a picture cue *and* to a specific mix — none of
    # which the reel's own re-cut cue table has anything to do with. Dropped
    # unconditionally and named, `tail_dropped`/`music_dropped`'s own rule:
    # a hold re-opened blind on a derivation is the `cues_pinned`-without-a-
    # pin failure shape CLAUDE.md already documents for the picture side.
    holds_dropped = _stored_holds(source)
    under_vo_dropped = _stored_under_vo(source)
    # Checked here rather than left to `cut_by_time`, at the granularity a reel
    # actually has a boundary at — see `_reel_suspect_edges`. Under `plan` it
    # is reported and never refused, which is `cut_by_time`'s own convention
    # for the same finding.
    suspect_edges = _reel_suspect_edges(source, edit, start, end)
    if suspect_edges and not (plan or confirm_suspect):
        hit = suspect_edges[0]
        raise tl.TimelineError(
            f"the reel's {hit['edge']} lands on word {hit['index']} ({hit['text']!r}) "
            f"in clip {hit['clip_id']!r}, which claims {hit['duration']}s — more than "
            f"{hit['limit']}s, so it likely hides a retake rather than ending where it "
            "claims (PLAN.md § Suspect word durations). A reel that opens or closes on "
            "the wrong take reads as an editing choice, so check it and retry with "
            "confirm_suspect=True (CLI: --confirm-suspect), or move the edge"
        )

    dest_root = Path(dest).expanduser().resolve()
    existed = dest_root.exists()
    # Before the emptiness check rather than after it: a film is never empty,
    # so this one would otherwise only ever be reached as "that directory has
    # something in it", which is true and unhelpful.
    if dest_root == source.root:
        raise ProjectError(
            "a reel is derived *from* a project, so it cannot be that project — "
            "name a different directory"
        )
    if existed and any(dest_root.iterdir()):
        raise ProjectError(
            f"{dest_root} already has something in it, and a reel is a new project — "
            "name a path that does not exist yet"
        )

    report: dict[str, Any] = {
        "project": str(source.root),
        "reel": str(dest_root),
        "keep": [start, end],
        "cut": cuts,
        "source_duration": duration,
        # What the reel should come out at. The cut's own `duration_after` is
        # the measured answer and replaces this below; under `plan` there is
        # no cut to measure, so the arithmetic is the honest one to report.
        "duration": round(end - start, 3),
        "canvas": canvas,
        # The one to read. `cut_plan`'s own `suspect_boundaries` is every
        # suspect word in everything being removed, which for a reel is most
        # of the film and almost never about the reel.
        "suspect_edges": suspect_edges,
        # Each one is a picture the reel will not have, so they are named
        # rather than counted.
        "cues_dropped": orphans,
        # And each of these is a picture the reel would have had from the
        # wrong second. Named for the same reason.
        "cues_pinned": pinned,
        "pins_error": pins_error,
        # None if the film had no tail; otherwise what it had, never carried
        # onto the derived project — a bumper is a decision about this cut,
        # not a fact the span carries with it.
        "tail_dropped": tail_dropped,
        # `tail_dropped`'s own rule, mirrored: what the film's cold open was,
        # never carried onto the derived project.
        "head_dropped": head_dropped,
        # Same rule, same reason: what the film's bed was, never carried onto
        # the derived project.
        "music_dropped": music_dropped,
        # [] if the film had no holds; otherwise every one it had, never
        # carried onto the derived project — same rule, same reason.
        "holds_dropped": holds_dropped,
        "under_vo_dropped": under_vo_dropped,
        "plan": bool(plan),
    }
    report["over_platform_cap"] = report["duration"] > PLATFORM_CAP
    report["platform_cap"] = PLATFORM_CAP

    if plan:
        report["would_link"] = [
            {"clip_id": clip.get("clip_id"), "key": key}
            for clip in source.read_manifest().get("clips", [])
            for key in ("media", "attenuated", "mixed", "stripped")
            if clip.get(key)
        ]
        # The one call the real path makes, so what is planned is what would
        # run: `cut_by_time` resolves a whole list against the pre-cut
        # timeline, and planning them one at a time would resolve the tail
        # against a timeline the head had not been taken out of.
        report["cut_plan"] = cut_by_time(source.root, spans=cuts, plan=True) if cuts else None
        return report

    reel_project = Project.create(dest_root, name=name or dest_root.name)
    try:
        manifest = source.read_manifest()
        manifest["name"] = name or reel_project.root.name
        manifest["cues"] = kept_cues
        # Never inherited — see `tail_dropped` above. Popped explicitly rather
        # than left to the copy above carrying it across: `manifest` starts as
        # the *film's* manifest, tail and all, and this is the one line that
        # makes "never" true rather than "true until the next key gets copied
        # here by accident".
        manifest.pop(TAIL_KEY, None)
        # `tail_dropped`'s own "never" line, mirrored — see `head_dropped` above.
        manifest.pop(HEAD_KEY, None)
        # `music_dropped`'s own "never" line, for the same reason.
        manifest.pop(MUSIC_KEY, None)
        # `holds_dropped`'s own "never" line, for the same reason.
        manifest.pop(HOLDS_KEY, None)
        manifest.pop(UNDER_VO_KEY, None)
        # Provenance, and the answer to the question a hand-made scratch copy
        # could not answer once already: which film is this, and which seconds
        # of it (HISTORY.md § The VO the project was holding). Additive and
        # optional, so no schema bump — the `canvas`/`caption_style` shape.
        manifest["derived_from"] = {
            "project": str(source.root),
            "keep": [start, end],
            "source_duration": duration,
        }
        report["linked"] = _reel_media(source, reel_project, manifest)
        # No snapshot: this is the derived project being *built*, not edited.
        # A history entry here would offer an undo back to a half-constructed
        # reel — a manifest with no timeline beside it yet — which is not a
        # state anyone chose. Its own cut, below, is what belongs on the stack.
        reel_project.write_manifest(manifest, snapshot=False)

        shutil.copy2(source.timeline_path, reel_project.timeline_path)
        # A transcript indexes the source, so it is as true of the reel as of
        # the film and costs ASR minutes to rebuild. The cards come as they
        # stand, to be re-authored below.
        for src_dir, dst_dir in (
            (source.transcript_dir, reel_project.transcript_dir),
            (source.cards_dir, reel_project.cards_dir),
        ):
            if src_dir.is_dir():
                shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

        if cuts:
            # Always confirmed, because the decision was already made above at
            # the granularity a reel has boundaries at. Passing the caller's
            # flag through instead would re-ask the wrong question and refuse
            # on a suspect word two hundred seconds from either edge.
            cut = cut_by_time(reel_project.root, spans=cuts, confirm_suspect=True)
            report["removed"] = cut["removed"]
            report["duration"] = cut["duration_after"]
            report["segments"] = cut["segments"]
            report["over_platform_cap"] = report["duration"] > PLATFORM_CAP

        if canvas is not None:
            report["canvas_set"] = _set_canvas(reel_project.root, size=canvas)

        cards = card_reauthor(reel_project.root)
        report["cards_redrawn"] = cards["redrawn"]
        report["cards_unrecorded"] = cards["unrecorded"]
        report["cards"] = cards["cards"]
    except Exception:
        # Only what this call created, and only when there was nothing there
        # before it: `Project.create` will happily adopt an existing empty
        # directory, and removing one the caller had made is not this op's to
        # do. A half-derived project left behind is worse than no reel — it
        # opens, it reads as a film, and its timeline is the uncut one.
        if not existed:
            shutil.rmtree(reel_project.root, ignore_errors=True)
        raise

    return report


# `proofcut review` — PLAN.md § The completion queue, item 6. Every version of
# the Scream video moved on a served page rebuilt ad hoc at least four times
# (`~/proofcut-work/archive/spikes/approvals/`, `~/proofcut-work/archive/spikes/watch/`, `~/proofcut-work/archive/spikes/review/`,
# `~/proofcut-work/archive/spikes/flash-review/`), each its own throwaway server and its own
# `decisions.json`. This is that serving, in the project instead of beside it.
REVIEW_KEY = "review"
REVIEW_KINDS = ("render", "sheet", "ab", "control")


def _stored_review(project: Project) -> dict[str, Any]:
    """The project's review round: registered items and recorded verdicts.

    Validated on every read, the `_stored_tail` shape. Absent means what
    every older manifest already meant: no review round yet — additive and
    optional, so no schema bump.
    """
    stored = project.read_manifest().get(REVIEW_KEY)
    if stored is None:
        return {"items": {}, "verdicts": {}}
    if not isinstance(stored, dict):
        raise ProjectError(f"{project.manifest_path}'s {REVIEW_KEY!r} must be a JSON object")
    items = stored.get("items", {})
    verdicts = stored.get("verdicts", {})
    if not isinstance(items, dict) or not isinstance(verdicts, dict):
        raise ProjectError(
            f"{project.manifest_path}'s {REVIEW_KEY!r} must hold 'items' and "
            "'verdicts' objects"
        )
    return {"items": items, "verdicts": verdicts}


def _review_resolve_path(project: Project, source: Path | str) -> Path:
    """Resolve a review item's file against the project root, refusing an escape.

    Most op file arguments are left free for the caller's own filesystem
    (`server._confine`'s docstring: only the project *selector* is normally
    confined). A review item is different in kind — it is later streamed by
    `proofcut review serve` to whatever device holds the review URL, over the
    network, so `review add leak /etc/passwd` must not become a way to read
    the box rather than the project. Both sides resolved, the same way
    `_confine` refuses a symlink out.
    """
    candidate = Path(source)
    resolved = (candidate if candidate.is_absolute() else project.root / candidate).resolve()
    if resolved != project.root and project.root not in resolved.parents:
        raise ProjectError(
            f"review item {source!r} resolves outside the project ({resolved}) "
            f"— only files under {project.root} can be served"
        )
    if not resolved.is_file():
        raise ProjectError(f"review item {source!r} does not exist ({resolved})")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def review_add(
    path: Path | str,
    name: str,
    source: Path | str,
    *,
    kind: str,
    baseline: str | None = None,
) -> dict[str, Any]:
    """Register a rendered file, sheet or A/B member for a review round.

    Never copies `source` — a render already lives in `renders/`, a sheet in
    `project.sheet_dir` (`reframe_sheet`'s own precedent) — this just points
    `name` at it, so `proofcut review serve` has something to stream and a
    verdict has something to attach to.

    `kind` is one of `"render"`, `"sheet"`, `"ab"`, `"control"`. **A
    `"control"` requires `baseline`, the name of an already-registered item,
    and the two files' sha256 must match — a mismatch refuses the whole call
    rather than registering something as a control that isn't.** This is the
    enforcement of the one rule carried out of the round that went wrong
    (HISTORY.md § The bumper the teaser never had): a page served three cuts,
    one mislabelled "Hand-built ... control" when it was a different, later
    render with no bumper. "Settle a served control by its own measurement —
    duration, shot count — not by its filename." Here the measurement is the
    file's own bytes.
    """
    if kind not in REVIEW_KINDS:
        raise ProjectError(f"review kind must be one of {REVIEW_KINDS}, not {kind!r}")
    if kind != "control" and baseline is not None:
        raise ProjectError("`baseline` only applies to kind='control'")

    project = Project.open(path)
    resolved = _review_resolve_path(project, source)
    digest = _sha256(resolved)
    stored = _stored_review(project)

    control_ok: bool | None = None
    if kind == "control":
        if not baseline:
            raise ProjectError(
                "a control needs `baseline`, the name of the item it claims to "
                "match — nothing is labelled a control unless it is "
                "byte-identical to what it claims to be"
            )
        base_item = stored["items"].get(baseline)
        if base_item is None:
            raise ProjectError(f"no registered review item named {baseline!r}")
        control_ok = digest == base_item["sha256"]
        if not control_ok:
            raise ProjectError(
                f"{source!r} is not byte-identical to {baseline!r} "
                f"(sha256 {digest[:12]}… vs {base_item['sha256'][:12]}…) — "
                "refusing to register it as a control. Re-render it from the "
                "same source, or register it as a plain 'render' instead."
            )

    record = {
        "kind": kind,
        "path": str(resolved.relative_to(project.root)),
        "baseline": baseline,
        "sha256": digest,
        "control_ok": control_ok,
        "added_at": datetime.now(UTC).isoformat(),
    }

    manifest = project.read_manifest()
    items = dict(stored["items"])
    items[name] = record
    manifest[REVIEW_KEY] = {"items": items, "verdicts": dict(stored["verdicts"])}
    project.write_manifest(manifest)

    return {"project": str(project.root), "name": name, **record}


def review_verdict(
    path: Path | str, name: str, verdict: str, *, note: str | None = None
) -> dict[str, Any]:
    """Record a verdict against a registered review item.

    `verdict` is a free-form string, not an enum — past rounds answered
    yes/no, "loop"/"hold", or a specific choice by name, and a fixed
    vocabulary would misfit the next round the same way a fixed threshold
    misfits a new video.
    """
    project = Project.open(path)
    stored = _stored_review(project)
    if name not in stored["items"]:
        raise ProjectError(f"no registered review item named {name!r}")

    verdicts = dict(stored["verdicts"])
    verdicts[name] = {
        "verdict": str(verdict),
        "note": str(note) if note is not None else None,
        "at": datetime.now(UTC).isoformat(),
    }
    manifest = project.read_manifest()
    manifest[REVIEW_KEY] = {"items": dict(stored["items"]), "verdicts": verdicts}
    project.write_manifest(manifest)

    return {"project": str(project.root), "name": name, **verdicts[name]}


def review_list(path: Path | str) -> dict[str, Any]:
    """Every item registered for this project's review round, and its verdict.

    Read straight off the manifest — `proofcut review list` and the page
    `proofcut review serve` draws both call this, never the file directly.
    """
    project = Project.open(path)
    stored = _stored_review(project)
    items = [{"name": name, **record} for name, record in stored["items"].items()]
    return {"project": str(project.root), "items": items, "verdicts": stored["verdicts"]}
