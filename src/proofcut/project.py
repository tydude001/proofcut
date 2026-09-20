"""The proofcut project directory.

A project is a directory on disk. Nothing is uploaded, and every artifact is
inspectable with ordinary tools::

    myproject/
      proofcut.json         manifest — schema version, clip registry, cue table,
                            footage descriptions, card records, settings
      media/                imported source media (copies or symlinks)
      project.otio          the timeline; the source of truth tools mutate
      cache/
        transcripts/        <clip_id>.json — word-level timings, per clip
        verify/             <render>.json — what a finished render was heard to say
        frames/             <render-stem>/*.png — spot-check frames pulled from a render
        attenuated/         <clip_id>.<ext> — derived, gain-reduced copies of clip media
        waveform/           <clip_id>.json — RMS envelope, keyed by media size+mtime
        agent_thumbs.jsonl  one JSON line per per-turn thumbs-up/down rating
        renders.jsonl       one JSON line per finished render pipeline run
      assets/
        cards/              <name>.png — static picture cards a `card:<name>` cue resolves to
      renders/              preview.mp4, final.mp4, …

The OTIO file is authoritative for the edit; renders are derived from it and
are safe to delete. The manifest is authoritative for *identity* — which clip
a `clip_id` refers to, and where its media lives.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Bumped when the on-disk layout changes incompatibly.
#: 2 added the cue table (`cues`, PLAN.md § The layered timeline).
#: 3 added footage descriptions (`descriptions`, PLAN.md § B-roll by
#: description), and covers the optional in-point a cue gains with them.
#: 4 added card records (`cards`, PLAN.md § Aspect swap step 2) — what a
#: card was made from, so an aspect swap can re-author it.
SCHEMA_VERSION = 4

MANIFEST_NAME = "proofcut.json"
#: What the manifest was called before the rename (docs/plans/RENAME.md,
#: decision 1). Never read as a project: `Project.open` refuses a directory
#: holding it and names `proofcut migrate`, whose filename step renames it to
#: `MANIFEST_NAME`. A project that read under both names would be one that got
#: written under both, so there is no dual read — only this refusal and that
#: one rename.
LEGACY_MANIFEST_NAME = "lucid.json"
TIMELINE_NAME = "project.otio"

MEDIA_DIR = "media"
CACHE_DIR = "cache"
TRANSCRIPT_DIR = "cache/transcripts"
HISTORY_DIR = "cache/history"
VERIFY_DIR = "cache/verify"
FRAMES_DIR = "cache/frames"
#: Where `reframe_sheet` puts its tiles and the sheet it montages from them.
#: Cache because it is re-derivable from the manifest and the media, and
#: nothing reads it back — a person looks at it.
SHEET_DIR = "cache/sheets"
ATTENUATED_DIR = "cache/attenuated"
MIXED_DIR = "cache/mixed"
#: A copy of an imported clip with its chapter list and the data/text track
#: it rides on stripped (`media.strip_chapters`). Not in `_SUBDIRS`, the
#: `MIXED_DIR` precedent: a project never had one until the first chaptered
#: source was imported, and `media.import_media` creates it on demand the
#: way `derive_single_audio`'s own `mixed_dir` write does.
STRIPPED_DIR = "cache/stripped"
WAVEFORM_DIR = "cache/waveform"
#: Browser-playable stand-ins for footage a `<video>` cannot decode
#: (PLAN.md § The preview proxy transcode). Cache because it is re-derivable
#: from the media and *nothing downstream reads it*: no manifest key points
#: here, `media_path()` has no branch for it, and only the preview side
#: resolves through `media.preview_path`. One entry per clip, overwritten when
#: its sidecar key stops describing the resolved source — the same shape
#: `waveform/` has, and the reason this needs no eviction policy.
PROXY_DIR = "cache/proxy"
RENDER_DIR = "renders"
CARDS_DIR = "assets/cards"
#: The silent WAV a `tail` renders its audio-track entry from (PLAN.md § Tail
#: time — the design note). Cache, the `SHEET_DIR` precedent rather than
#: `PROXY_DIR`'s: it is re-derivable from the manifest's own `tail.seconds`
#: with no probe of anything on disk, and a project never had one until the
#: first tail was set — so it is not in `_SUBDIRS` either, and `ops._tail_silence`
#: creates it on demand the way `reframe_sheet` creates `SHEET_DIR`.
TAIL_DIR = "cache/tail"
#: The padded one-shot copies the sound lanes read (`ops._sound_copy`) —
#: `TAIL_DIR`'s precedent: re-derivable, created on demand, not in `_SUBDIRS`.
SOUNDS_DIR = "cache/sounds"
#: Per-turn thumbs-up/down log for the agent panel (docs/plans/DAYDREAM.md § Agent
#: panel) — one JSON line per rating. Lives under `cache/` because it is
#: derived telemetry, not part of the edit: nothing here is authoritative for
#: the timeline or the manifest, and `_revision()` in webui.py never stats it.
THUMBS_LOG = "cache/agent_thumbs.jsonl"
#: One JSON line per finished render pipeline run (docs/plans/STUDIO.md § Step 01) — the
#: `THUMBS_LOG` precedent exactly: cache because it is derived telemetry, not
#: part of the edit. No manifest key names it and `_revision()` never stats
#: it, so a render never counts as a project mutation.
RENDERS_LOG = "cache/renders.jsonl"
#: One JSON line per `ops.finish_check` run against a delivered file —
#: `RENDERS_LOG`'s own precedent, one lane over: derived telemetry about an
#: artifact proofcut did not produce (the external mix pass's own output), not
#: part of the edit. No manifest key names it and `_revision()` never stats
#: it, so a finish_check run never counts as a project mutation.
FINISH_CHECKS_LOG = "cache/finish_checks.jsonl"

_SUBDIRS = (
    MEDIA_DIR,
    CACHE_DIR,
    TRANSCRIPT_DIR,
    HISTORY_DIR,
    VERIFY_DIR,
    FRAMES_DIR,
    ATTENUATED_DIR,
    WAVEFORM_DIR,
    PROXY_DIR,
    RENDER_DIR,
    CARDS_DIR,
)


class ProjectError(Exception):
    """Raised when a path is not a usable proofcut project."""


class LegacyManifestError(ProjectError):
    """Raised for a directory holding `LEGACY_MANIFEST_NAME` and no
    `MANIFEST_NAME` — a project an older proofcut (named lucid) wrote, which
    `proofcut migrate` renames. A subclass so every `except ProjectError`
    handler still refuses it, and distinct so the picker's scan can list it as
    `needs_migration` without matching a sentence."""


#: Windows refuses to create a directory whose path is longer than this —
#: MAX_PATH (260) less 12 for an 8.3 name — unless `LongPathsEnabled` is set.
#: The laptop's probe with the setting off made a 235-character root and its
#: `cache` (241), then died on `cache\transcripts` (253) with WinError 206.
#: The same 266-character paths rendered cleanly through melt and ffmpeg with
#: the setting on, on the laptop and on GitHub's runner. HISTORY.md § A long
#: project path on Windows.
WINDOWS_DIR_LIMIT = 248
#: What proofcut's own layout gets under a project root. The shipped film's
#: project, every cache family populated, reaches 44 characters
#: (`renders/<a render someone named>.mp4`) and 41 through `cache/thumbs/<clip>/`
#: — both names a person chose, so this is that measurement doubled plus the
#: separator, not a bound. A clip id or render name long enough to spend the
#: rest still fails later, with `path_too_long`'s one line rather than a
#: traceback.
PATH_HEADROOM = 100
#: `ERROR_FILENAME_EXCED_RANGE` — what Win32 answers a path past its limit with.
WINERROR_PATH_TOO_LONG = 206
_LONG_PATHS_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"
LONG_PATHS_FIX = (
    "in an administrator PowerShell, run "
    "`Set-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' LongPathsEnabled 1`, "
    "then run the command again"
)


def windows_long_paths() -> bool | None:
    """Whether Windows lets this process past `WINDOWS_DIR_LIMIT`; `None` off
    Windows, where there is no such limit to ask about.

    Read once per process by Windows itself, at process start — so a change
    reaches the next command run, never the one already running. An
    unreadable key reads as off, which is the stock default.
    """
    if sys.platform != "win32":
        return None
    import winreg  # Windows-only stdlib, so imported where it is used

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _LONG_PATHS_KEY) as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def max_root_length() -> int | None:
    """The longest project root this machine can hold proofcut's layout under,
    or `None` when there is no limit (not Windows, or long paths on)."""
    if windows_long_paths() is not False:
        return None
    return WINDOWS_DIR_LIMIT - PATH_HEADROOM


def path_too_long(exc: OSError) -> str | None:
    """One line for a path Windows refused as too long, or `None` for any
    other `OSError` — the message a traceback would have buried."""
    if getattr(exc, "winerror", None) != WINERROR_PATH_TOO_LONG:
        return None
    where = f" ({len(str(exc.filename))} characters: {exc.filename})" if exc.filename else ""
    return (
        f"Windows refused a path as too long{where} — it limits a folder path to "
        f"{WINDOWS_DIR_LIMIT} characters while long paths are off. Move the project to a "
        f"shorter folder, or turn long paths on: {LONG_PATHS_FIX}."
    )


@contextlib.contextmanager
def refusing_path_too_long() -> Iterator[None]:
    """Re-raise Windows' too-long-path `OSError` inside the block as a
    `ProjectError` carrying `path_too_long`'s line; any other `OSError` passes
    through untouched.

    For the clients that flatten proofcut's own refusals and let everything
    else keep its traceback — the MCP tools and the web UI's jobs. Raising
    the family they already catch is what reaches every one of their `except`
    sites without a second clause in each."""
    try:
        yield
    except OSError as exc:
        message = path_too_long(exc)
        if message is None:
            raise
        raise ProjectError(message) from exc


class PathTooLongError(ProjectError):
    """Raised by `Project.create` for a root too long for proofcut's own layout
    on a Windows with long paths off — before anything is written, so a
    refusal leaves no half-made project behind. Carries `length` and `limit`,
    so a caller never reads the numbers out of the sentence."""

    def __init__(self, root: Path, *, limit: int) -> None:
        self.length = len(str(root))
        self.limit = limit
        super().__init__(
            f"this project's folder path is {self.length} characters, and Windows limits a "
            f"folder path to {WINDOWS_DIR_LIMIT} while long paths are off — proofcut keeps "
            f"files up to about {PATH_HEADROOM} characters deep inside a project, so a project "
            f"folder here can be at most {limit} characters: {root}. Put the project in a "
            f"shorter folder, or turn long paths on: {LONG_PATHS_FIX}."
        )


class ProjectConflictError(ProjectError):
    """Raised when `write_manifest` finds the manifest changed since it was
    last read by this `Project` instance — a second writer (another `proofcut
    web`, an agent panel, a CLI command run beside either) touched the
    project in between (TRIAL.md § Nothing in lucid notices two writers in
    one project). A `ProjectError` subclass so every existing `except
    ProjectError`/`EXPECTED` handler already catches it as a refusal; the
    distinct type is for a caller that wants to tell "stale write" apart
    from every other reason a project call can fail."""


def _manifest_digest(path: Path) -> str:
    """The stamp `_manifest_stamp` holds: a hash of the file's bytes as they
    are on disk, read back after every write rather than computed from what
    was handed to `json.dump` — text mode on Windows writes `\r\n`, so the
    bytes written and the bytes on disk are not the same string."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- schema migration --------------------------------------------------------


def _v1_to_v2(manifest: dict[str, Any]) -> dict[str, Any]:
    """v2 added the cue table (PLAN.md § The layered timeline).

    Additive: every v1 key means in v2 exactly what it meant in v1, so the
    step is the one missing list. `cue_add` would `setdefault` it anyway —
    writing it here is what makes the version number true rather than
    incidentally survivable.
    """
    manifest.setdefault("cues", [])
    return manifest


def _v2_to_v3(manifest: dict[str, Any]) -> dict[str, Any]:
    """v3 added footage descriptions (PLAN.md § B-roll by description).

    Additive, like v2 before it. It also covers the optional `src_start` a
    picture cue gains — one bump for both, because a v2 cue without one means
    in v3 exactly what it meant in v2 (take the asset from wherever the
    consumption cursor is), so no cue needs rewriting.
    """
    manifest.setdefault("descriptions", [])
    return manifest


def _v3_to_v4(manifest: dict[str, Any]) -> dict[str, Any]:
    """v4 added card records (PLAN.md § Aspect swap, step 2).

    Additive, and the list is empty on purpose: a record says what a card was
    *made from*, and nothing on disk can recover that for a card made before
    the key existed. An empty list after this step is the honest answer —
    "this project records no cards" — rather than a guess, and `card_reauthor`
    names every unrecorded card rather than skipping it.
    """
    manifest.setdefault("cards", [])
    return manifest


#: Keyed by the version each step migrates *from*; a step returns the manifest
#: at version key+1, and `migrate` stamps the number. Stepwise rather than
#: one function per (from, to) pair, so the next bump is a single entry and
#: every older project reaches the present through the same path the one
#: before it took.
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
}


def _migratable(found: Any) -> bool:
    """Whether `Project.migrate` has a path from `found` to the current version.

    `bool` is excluded explicitly because it is an `int` subclass, so a
    manifest reading `"schema_version": true` would otherwise be treated as
    version 1 and migrated.
    """
    return (
        isinstance(found, int)
        and not isinstance(found, bool)
        and found < SCHEMA_VERSION
        and all(v in _MIGRATIONS for v in range(found, SCHEMA_VERSION))
    )


def _migration_steps(found: Any, manifest_path: Path) -> list[int]:
    """The versions to step through, or a refusal naming why there is no path."""
    if found == SCHEMA_VERSION:
        return []
    if not _migratable(found):
        if isinstance(found, int) and not isinstance(found, bool) and found > SCHEMA_VERSION:
            why = "it was written by a newer proofcut, and migration is forward-only"
        else:
            why = f"no migration step is registered for schema_version {found!r}"
        raise ProjectError(
            f"cannot migrate {manifest_path}: {why} "
            f"(this proofcut understands {SCHEMA_VERSION})"
        )
    return list(range(found, SCHEMA_VERSION))


#: The suffix a snapshot's manifest half carries. Two dots on purpose: the
#: glob `*.manifest.json` cannot reach `proofcut-v3.json`, which
#: `_backup_manifest` writes into the same directory and which must stay
#: invisible to undo (rolling the timeline back one edit must not roll the
#: schema back with it).
MANIFEST_SNAPSHOT_SUFFIX = ".manifest.json"


@dataclass(frozen=True)
class Snapshot:
    """One saved project state: the timeline, the manifest, or both.

    A pair rather than a file, because most authoring state stopped living in
    the timeline. The cue table, framing rects, the music bed, the caption
    style, head/tail/holds, unspoken marks and card records are all manifest
    keys that touch no `project.otio` at all — so an undo that restored only
    the timeline covered cuts and nothing a gesture in the window can now do
    (docs/plans/POLISH.md § Step 03).

    Either half may be absent, and the two absences mean different things:

    - **no `manifest`** — a snapshot written by a proofcut that only saved
      timelines. It restores the timeline alone and says so; it never guesses
      at a manifest it does not have.
    - **no `timeline`** — the project had no timeline when this was taken, so
      the state being restored is one with no timeline in it. Undoing a
      `seed_timeline` is exactly this case, and restoring it removes the
      timeline the seed laid down.
    """

    index: int
    timeline: Path | None
    manifest: Path | None

    @property
    def legacy(self) -> bool:
        """A timeline-only snapshot from before manifests were saved."""
        return self.manifest is None


@dataclass(frozen=True)
class Project:
    """A handle to a project directory. Cheap to construct; does no I/O."""

    root: Path

    #: The snapshot this instance already took, if any — the guard that keeps
    #: one op to one undo step. `_save_edit` snapshots and so does
    #: `write_manifest`, and an op doing both (`seed_timeline`, `import_edit`)
    #: would otherwise leave two history entries for one user action, needing
    #: two undos to walk back one decision.
    #:
    #: Per *instance* rather than per directory, and that is what makes it
    #: correct rather than merely convenient: every op opens its own `Project`
    #: at the top and one op is one user action, while `reel` — which touches
    #: two projects — holds two instances and snapshots each on its own.
    #: A mutable field on a frozen dataclass, excluded from equality, so the
    #: handle stays hashable and comparable by root exactly as before.
    _taken: list[Snapshot] = field(default_factory=list, compare=False, repr=False)

    #: A digest of the manifest's on-disk bytes as of this instance's last
    #: `read_manifest()`, or empty for "never read here yet". `write_manifest`
    #: compares the file's *current* bytes against this before writing: a
    #: mismatch means another writer — a second `proofcut web`, an agent panel,
    #: a CLI command run beside either — wrote the manifest after this
    #: instance last read it, and writing blind now would silently discard
    #: that write the way it always has (TRIAL.md § Nothing in lucid notices
    #: two writers in one project). It was the file's mtime until 2026-09-13,
    #: `waveform/`'s size+mtime cache-key idiom applied to staleness — and a
    #: clock is the wrong witness for "did the bytes change": Windows stamps
    #: two writes inside one timer tick with the same mtime, so a second
    #: writer landing within ~15ms of the first read as no writer at all
    #: (HISTORY.md § The stamp that was a clock). The manifest is a few KB,
    #: so hashing it costs nothing a stat would not. A dict rather than a
    #: plain field for `_taken`'s own reason: mutated in place on a frozen
    #: dataclass, never reassigned.
    _manifest_stamp: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    # -- layout ----------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def legacy_manifest_path(self) -> Path:
        return self.root / LEGACY_MANIFEST_NAME

    @property
    def timeline_path(self) -> Path:
        return self.root / TIMELINE_NAME

    @property
    def media_dir(self) -> Path:
        return self.root / MEDIA_DIR

    @property
    def transcript_dir(self) -> Path:
        return self.root / TRANSCRIPT_DIR

    @property
    def render_dir(self) -> Path:
        return self.root / RENDER_DIR

    @property
    def history_dir(self) -> Path:
        return self.root / HISTORY_DIR

    @property
    def verify_dir(self) -> Path:
        return self.root / VERIFY_DIR

    @property
    def frames_dir(self) -> Path:
        return self.root / FRAMES_DIR

    @property
    def sheet_dir(self) -> Path:
        return self.root / SHEET_DIR

    @property
    def attenuated_dir(self) -> Path:
        return self.root / ATTENUATED_DIR

    @property
    def mixed_dir(self) -> Path:
        return self.root / MIXED_DIR

    @property
    def stripped_dir(self) -> Path:
        return self.root / STRIPPED_DIR

    @property
    def waveform_dir(self) -> Path:
        return self.root / WAVEFORM_DIR

    @property
    def proxy_dir(self) -> Path:
        return self.root / PROXY_DIR

    @property
    def cards_dir(self) -> Path:
        return self.root / CARDS_DIR

    @property
    def tail_dir(self) -> Path:
        return self.root / TAIL_DIR

    @property
    def sounds_dir(self) -> Path:
        return self.root / SOUNDS_DIR

    def transcript_path(self, clip_id: str) -> Path:
        return self.transcript_dir / f"{clip_id}.json"

    def waveform_path(self, clip_id: str) -> Path:
        return self.waveform_dir / f"{clip_id}.json"

    def proxy_path(self, clip_id: str) -> Path:
        """The preview stand-in for `clip_id`, whether or not one exists.

        Always `.mp4`: a proxy's whole point is that the container and the
        codecs are the ones a browser opens, so it does not inherit the
        source's suffix the way `media/<clip_id><ext>` does.
        """
        return self.proxy_dir / f"{clip_id}.mp4"

    def proxy_key_path(self, clip_id: str) -> Path:
        """The sidecar recording which source file `proxy_path` was made from.

        A separate file rather than a field, because the proxy is an mp4 and
        `waveform/`'s trick of writing the key into the payload has nowhere to
        go. Same key, same reason (`ops._cached_waveform`): size and mtime,
        cheap to check and exactly what a re-import or `attenuate_noises`
        changes.
        """
        return self.proxy_dir / f"{clip_id}.json"

    @property
    def thumbs_path(self) -> Path:
        return self.root / THUMBS_LOG

    @property
    def session_path(self) -> Path:
        """Studio Step 04 § B: playhead/zoom/scroll/pane/mode/selection.

        Cache, not manifest — no schema version, every key optional on read
        and write, disposable by design. `webui._session_get`/`_session_set`
        are the only readers/writers; `_revision()` never stats this file, so
        writing it deliberately cannot fire `project-changed`.
        """
        return self.root / CACHE_DIR / "session.json"

    @property
    def poster_path(self) -> Path:
        """A bound session's one still frame for the Home gallery card.

        Written once by `webui._ensure_poster` (a plain byte copy of an
        `ops.thumbnail` frame — `media.preview_path()` gains no new caller
        here), served by the picker's `_send_poster` only if this file
        already exists. Cache: re-derivable, never read by anything render-
        facing.
        """
        return self.root / CACHE_DIR / "poster.jpg"

    @property
    def renders_log_path(self) -> Path:
        return self.root / RENDERS_LOG

    @property
    def finish_checks_log_path(self) -> Path:
        return self.root / FINISH_CHECKS_LOG

    # -- history ---------------------------------------------------------

    def snapshots(self) -> list[Snapshot]:
        """Every saved state, oldest first — timelines and manifests paired up.

        Indices come off the filenames rather than a counter, so the two halves
        of one state find each other by number and a half-written pair is still
        a readable snapshot. Anything in `cache/history/` that is not numbered
        is not a snapshot: `proofcut-v3.json` (the pre-migration manifest backup)
        lives here too and stays invisible to undo on purpose.
        """
        if not self.history_dir.exists():
            return []
        found: dict[int, dict[str, Path]] = {}
        for path in self.history_dir.iterdir():
            if path.suffix == ".otio" and path.stem.isdigit():
                found.setdefault(int(path.stem), {})["timeline"] = path
            elif path.name.endswith(MANIFEST_SNAPSHOT_SUFFIX):
                stem = path.name[: -len(MANIFEST_SNAPSHOT_SUFFIX)]
                if stem.isdigit():
                    found.setdefault(int(stem), {})["manifest"] = path
        return [
            Snapshot(index=i, timeline=found[i].get("timeline"), manifest=found[i].get("manifest"))
            for i in sorted(found)
        ]

    def snapshot(self) -> Snapshot | None:
        """Copy the current state into history before it is overwritten.

        A non-deterministic agent mutating a single source of truth in place is
        exactly the case where undo is not a tier-2 feature (PLAN.md). Both
        files go, together: a cue drag, a framing rect and a music bed are
        mutations the window can make in one gesture and none of them touches
        `project.otio`.

        **At most once per `Project` instance.** An op that writes both files
        would otherwise cost two undos to walk back one decision; the second
        call returns the same snapshot the first took. The instance is the
        right scope because every op opens its own (see `_taken`).

        Returns None when there is nothing to lose — a project with neither
        file yet, which is the state `Project.create` writes its first manifest
        into. That is the same early return the timeline-only version had,
        widened by one file.
        """
        if self._taken:
            return self._taken[-1]
        halves = [
            (self.timeline_path, "{}.otio"),
            (self.manifest_path, "{}" + MANIFEST_SNAPSHOT_SUFFIX),
        ]
        live = [(src, pattern) for src, pattern in halves if src.exists()]
        if not live:
            return None

        self.history_dir.mkdir(parents=True, exist_ok=True)
        existing = self.snapshots()
        index = (existing[-1].index + 1) if existing else 0
        copied: dict[str, Path] = {}
        for src, pattern in live:
            dest = self.history_dir / pattern.format(index)
            shutil.copy2(src, dest)
            copied["timeline" if dest.suffix == ".otio" else "manifest"] = dest

        taken = Snapshot(
            index=index, timeline=copied.get("timeline"), manifest=copied.get("manifest")
        )
        self._taken.append(taken)
        return taken

    def restore(self) -> Snapshot:
        """Roll back to the most recent snapshot, consuming it.

        What each half means when it is absent is `Snapshot`'s own docstring,
        and the two are not symmetrical. A snapshot with no manifest is an
        older proofcut's, and the manifest is left exactly as it stands rather
        than guessed at. A snapshot with no *timeline* is a state that had no
        timeline, so the timeline is **removed** — that is what undoing a
        `seed_timeline` means, and leaving the seeded edit in place would
        report an undo that did not happen.

        **The same stale-write refusal `write_manifest` makes, made here
        too** — a raw `shutil.copy2` rather than `write_manifest` (the undo
        step is not itself an edit to snapshot), so it would otherwise be the
        one path around that check: an undo run against a manifest another
        writer has since changed would blindly overwrite their write rather
        than only the state this instance actually rolled back from.
        """
        existing = self.snapshots()
        if not existing:
            raise ProjectError("nothing to undo — this project has no history")
        latest = existing[-1]

        if latest.manifest is not None:
            expected = self._manifest_stamp.get("digest")
            if expected is not None and self.manifest_path.exists():
                current = _manifest_digest(self.manifest_path)
                if current != expected:
                    raise ProjectConflictError(
                        f"{self.manifest_path} changed on disk since it was last "
                        "read here — another writer touched this project after "
                        "the state being undone was read; restoring now would "
                        "silently discard their write. Re-read the project first."
                    )

        if latest.timeline is not None:
            shutil.copy2(latest.timeline, self.timeline_path)
            latest.timeline.unlink()
        elif latest.manifest is not None:
            self.timeline_path.unlink(missing_ok=True)
        if latest.manifest is not None:
            shutil.copy2(latest.manifest, self.manifest_path)
            latest.manifest.unlink()
            self._manifest_stamp["digest"] = _manifest_digest(self.manifest_path)
        return latest

    # -- lifecycle -------------------------------------------------------

    @classmethod
    def create(cls, root: Path | str, *, name: str | None = None) -> Project:
        """Create a project directory. Refuses to overwrite an existing one."""
        project = cls(Path(root).expanduser().resolve())
        if project.manifest_path.exists():
            raise ProjectError(f"a proofcut project already exists at {project.root}")
        if project.legacy_manifest_path.exists():
            # Creating here would write `proofcut.json` beside the old
            # manifest — the two-manifest directory `open` refuses.
            raise ProjectError(
                f"a project written before the rename already exists at {project.root} "
                f"({LEGACY_MANIFEST_NAME}) — run `proofcut migrate` there instead"
            )

        limit = max_root_length()
        if limit is not None and len(str(project.root)) > limit:
            raise PathTooLongError(project.root, limit=limit)
        project.root.mkdir(parents=True, exist_ok=True)
        for sub in _SUBDIRS:
            (project.root / sub).mkdir(parents=True, exist_ok=True)

        project.write_manifest(
            {
                "schema_version": SCHEMA_VERSION,
                "name": name or project.root.name,
                "clips": [],
                "cues": [],
                "descriptions": [],
                "cards": [],
            }
        )
        return project

    @classmethod
    def open(cls, root: Path | str) -> Project:
        """Open an existing project, validating its manifest.

        A directory holding only `LEGACY_MANIFEST_NAME` is refused naming
        `proofcut migrate`, never read under the old name and never renamed
        here — the schema rule applied to the filename (`migrate`'s docstring).
        """
        project = cls(Path(root).expanduser().resolve())
        project.check_manifest_name()

        manifest = project.read_manifest()
        found = manifest.get("schema_version")
        if found != SCHEMA_VERSION:
            way_out = (
                "run `proofcut migrate` to bring it forward"
                if _migratable(found)
                else "there is no migration path from it"
            )
            raise ProjectError(
                f"{project.manifest_path} has schema_version {found!r}, "
                f"but this proofcut understands {SCHEMA_VERSION} — {way_out}"
            )
        return project

    @classmethod
    def migrate(cls, root: Path | str, *, plan: bool = False) -> dict[str, Any]:
        """Bring an older manifest forward to `SCHEMA_VERSION`.

        Deliberately *not* folded into `Project.open`. Opening is a read, and a
        read that rewrites the file it just validated would migrate a project
        on `proofcut info` — including one the reader only meant to look at, and
        one an older proofcut elsewhere can still open until the moment it is
        touched. So `open` refuses and names this, and this does the writing.

        `plan=True` resolves the steps and writes nothing (CLAUDE.md), which is
        also the only way to ask "what version is this, and can it come
        forward?" without committing to the answer.

        **A project written before the rename gets a filename step first** —
        `lucid.json -> proofcut.json`, reported as the first entry of `steps`
        (docs/plans/RENAME.md, decision 1). It is not a `_MIGRATIONS` entry and
        bumps nothing: `SCHEMA_VERSION` says what the keys mean, not what the
        file is called, so a v4 `lucid.json` takes this step alone. It also
        rewrites the live `project.otio`'s metadata key (`timeline_keys` counts
        them) so a migrated project is clean on its face; the snapshots in
        `cache/history/` keep the old key and are read through
        `timeline.proofcut_metadata` forever. `manifest` names the file found
        (and, after a real run, the file now holding it).
        """
        project = cls(Path(root).expanduser().resolve())
        try:
            project.check_manifest_name()
            legacy = False
        except LegacyManifestError:
            legacy = True

        source = project.legacy_manifest_path if legacy else project.manifest_path
        manifest = project._read_manifest_file(source)
        found = manifest.get("schema_version")
        # Resolved before anything is renamed, so a project with no path
        # forward is refused with its files exactly as they were.
        steps = _migration_steps(found, source)

        legacy_keys = 0
        if legacy and project.timeline_path.exists():
            # Imported here: OTIO is heavy, and only the filename step needs it.
            from proofcut import timeline as tl

            try:
                legacy_keys = tl.count_legacy_metadata(project.timeline_path)
            except Exception as exc:  # OTIO's own errors share no base class worth naming
                raise ProjectError(
                    f"cannot migrate {project.root}: {project.timeline_path} could not be "
                    f"read to rename its keys ({exc}); nothing was changed"
                ) from exc

        report: dict[str, Any] = {
            "project": str(project.root),
            "manifest": source.name,
            "schema_version": found,
            "target": SCHEMA_VERSION,
            "steps": ([f"{LEGACY_MANIFEST_NAME} -> {MANIFEST_NAME}"] if legacy else [])
            + [f"{v} -> {v + 1}" for v in steps],
            "timeline_keys": legacy_keys,
            "migrated": False,
            "backup": None,
        }
        if plan:
            report["plan"] = True
            return report
        if not report["steps"]:
            return report

        # One backup, of the file as it was found and under the name it had:
        # the filename step and a version step both start from the same bytes,
        # so a second copy would be the same file twice.
        report["backup"] = str(project._backup_manifest(found, source))
        if legacy:
            project._rename_legacy_manifest(legacy_keys)
            report["manifest"] = MANIFEST_NAME
        for version in steps:
            manifest = _MIGRATIONS[version](manifest)
            manifest["schema_version"] = version + 1
        # `_backup_manifest` is this write's history, and it is deliberately
        # not `snapshot()`'s: rolling the timeline back one edit must not roll
        # the schema back with it.
        if steps:
            project.write_manifest(manifest, snapshot=False)
        report["schema_version"] = SCHEMA_VERSION
        report["migrated"] = True
        return report

    def check_manifest_name(self) -> None:
        """Refuse a directory whose manifest is not exactly `MANIFEST_NAME`.

        Three refusals, in the order they are checked: both manifests at once
        (a plain `ProjectError` — `migrate` refuses it too), the old name alone
        (`LegacyManifestError`, which `migrate` clears), and neither.

        **Both is refused, never resolved by preferring one.** Nothing on disk
        says which file is the project: `migrate` renames atomically and so
        never leaves both behind, which means a second file was put there by
        something else — a copy by hand, or an older lucid's `init`, which
        looked only for `lucid.json` and would have written a fresh one into a
        migrated project. Preferring `proofcut.json` would silently hide
        whatever was written to the other, and preferring `lucid.json` would
        undo a migration; either is a guess about which edits count.
        """
        has_current = self.manifest_path.exists()
        has_legacy = self.legacy_manifest_path.exists()
        if has_current and has_legacy:
            raise ProjectError(
                f"{self.root} holds both {MANIFEST_NAME} and {LEGACY_MANIFEST_NAME}, and "
                "proofcut will not guess which is the project — move aside the one "
                "that is not (a migrated project keeps only "
                f"{MANIFEST_NAME}; the pre-rename file is backed up in {HISTORY_DIR}/)"
            )
        if has_legacy:
            raise LegacyManifestError(
                f"{self.root} is a project written before the rename: its manifest is "
                f"{LEGACY_MANIFEST_NAME}, and this proofcut reads {MANIFEST_NAME} — "
                "run `proofcut migrate` to rename it"
            )
        if not has_current:
            raise ProjectError(f"no proofcut project at {self.root} (no {MANIFEST_NAME})")

    def _rename_legacy_manifest(self, legacy_keys: int) -> None:
        """`migrate`'s filename step: `lucid.json` -> `proofcut.json`, live timeline first.

        Ordered so a crash anywhere leaves a project `migrate` can simply be
        run on again. The live `project.otio`'s keys are rewritten first — an
        atomic write, harmless if nothing follows, because every reader takes
        either key — and the manifest is renamed last, by one `os.replace`, so
        there is never a moment with both manifests or neither. The timeline
        is not snapshotted and `cache/history/` is never touched: the old key
        there is read permanently (`timeline.proofcut_metadata`).

        **The stale-read stamp holds across the rename.** It is a digest of
        bytes, not of a path, and a rename moves the bytes unchanged — so the
        digest `_read_manifest_file` took of `lucid.json` is exactly the one
        `write_manifest` then checks `proofcut.json` against for the version
        steps. It is checked here as well, before the rename, so a second
        writer landing on `lucid.json` after `migrate` read it is refused
        rather than renamed into place under a manifest nobody re-read.
        """
        if legacy_keys:
            from proofcut import timeline as tl

            tl.rewrite_legacy_metadata(self.timeline_path)
        expected = self._manifest_stamp.get("digest")
        if expected is not None and _manifest_digest(self.legacy_manifest_path) != expected:
            raise ProjectConflictError(
                f"{self.legacy_manifest_path} changed on disk while `proofcut migrate` "
                "was running — another writer touched this project. Run migrate again."
            )
        self.legacy_manifest_path.replace(self.manifest_path)

    def _backup_manifest(self, version: Any, source: Path | None = None) -> Path:
        """Copy the manifest aside before a migration rewrites it.

        It sits beside the timeline snapshots because it is the same kind of
        thing: the state before a mutation. `snapshots()` globs `*.otio`, so a
        `.json` here is invisible to `undo` — which is right, since rolling the
        timeline back one edit must not roll the schema back with it.

        Named after the file it copies, so a pre-rename manifest is backed up
        as `lucid-vN.json` rather than `proofcut-vN.json`: the backup is a
        record of that file as it was, name included, and it cannot collide
        with a `proofcut-vN.json` a later schema migration writes.
        """
        source = source or self.manifest_path
        self.history_dir.mkdir(parents=True, exist_ok=True)
        dest = self.history_dir / f"{source.stem}-v{version}.json"
        shutil.copy2(source, dest)
        return dest

    # -- manifest --------------------------------------------------------

    def read_manifest(self) -> dict[str, Any]:
        return self._read_manifest_file(self.manifest_path)

    def read_legacy_manifest(self) -> dict[str, Any]:
        """Read `LEGACY_MANIFEST_NAME` without opening the project — for a
        caller that reports what an unmigrated project is (the picker's scan,
        `migrate --plan`) and must never treat it as one."""
        return self._read_manifest_file(self.legacy_manifest_path)

    def _read_manifest_file(self, path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectError(
                f"{path} is not valid JSON: {exc}{self._recovery_hint(path)}"
            ) from exc
        if not isinstance(manifest, dict):
            raise ProjectError(f"{path} must contain a JSON object")
        self._manifest_stamp["digest"] = hashlib.sha256(raw).hexdigest()
        return manifest

    def _recovery_hint(self, path: Path) -> str:
        """What a refusal on a corrupt manifest can say about a way back.

        **A sentence and never an action** — the manifest is somebody's
        authoring state and this is a read. It is also why the hint names a
        copy rather than `proofcut undo`: `undo` opens the project, which
        reads the manifest, which is the thing that just refused. So the only
        way back from here is by hand.

        The newest snapshot that *parses* — a snapshot is a copy of the state
        before an edit, so it is one edit behind the live file, and one that
        is itself unreadable is skipped rather than offered. Only the live
        manifest gets a hint: a legacy `lucid.json` has no snapshots under
        that name, and `migrate --plan` reading one that is corrupt is not a
        project to recover.
        """
        if path != self.manifest_path:
            return ""
        for snap in reversed(self.snapshots()):
            if snap.manifest is None:
                continue
            try:
                held = json.loads(snap.manifest.read_bytes().decode("utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(held, dict):
                return (
                    f" — the newest readable snapshot is {snap.manifest}, the state "
                    "before the last edit; copy it over the manifest to recover "
                    "(`proofcut undo` cannot, because it opens the project first)"
                )
        return ""

    def write_manifest(self, manifest: dict[str, Any], *, snapshot: bool = True) -> None:
        """Snapshot, then write the manifest atomically, so a crash can't truncate it.

        The snapshot is on by default and that is the load-bearing decision.
        Most authoring state lives here now — the cue table, framing rects, the
        music bed, the caption style, head/tail/holds, marks, card records —
        and a manifest write that skipped history would not merely be
        un-undoable: it would be **erased by the next undo**, since a restore
        puts back the whole file. So the safe direction is to snapshot unless
        told otherwise, and `Project.snapshot`'s own once-per-instance guard is
        what keeps an op that writes both files to one undo step.

        `snapshot=False` is for the two writes that are not a user's edit:
        `migrate` (which has `_backup_manifest`, and whose schema bump must not
        become an undo step) and `reel`'s seeding of a project it is in the
        middle of creating.

        **Refuses rather than clobbering when the file moved under this
        instance.** If `read_manifest` was called here and the manifest's
        on-disk bytes have since changed, a second writer touched this project
        in between — a second `proofcut web`, an agent panel, a CLI command
        beside either (TRIAL.md § Nothing in lucid notices two writers in one
        project) — and writing `manifest` now would silently discard theirs,
        atomically-but-wrongly. Skipped when nothing was ever read here
        (`_manifest_stamp` empty): `Project.create`'s first write and `reel`'s
        seeding of a project mid-construction have nothing to conflict with.
        """
        expected = self._manifest_stamp.get("digest")
        if expected is not None and self.manifest_path.exists():
            current = _manifest_digest(self.manifest_path)
            if current != expected:
                raise ProjectConflictError(
                    f"{self.manifest_path} changed on disk since it was last read "
                    "here — another writer (a second `proofcut web`, agent panel, or "
                    "CLI command running beside this one) touched this project in "
                    "between. Re-read the project and re-apply this change; "
                    "writing now would silently discard theirs."
                )
        if snapshot:
            self.snapshot()
        tmp = self.manifest_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        tmp.replace(self.manifest_path)
        self._manifest_stamp["digest"] = _manifest_digest(self.manifest_path)
