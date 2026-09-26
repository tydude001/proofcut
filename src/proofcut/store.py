"""A store outside every project for what the models say about a source.

Whisper, the vision model and the face detector were cached only by
`clip_id`, inside one project's `cache/`, so a second project on the same
shoot, a re-cut, or every `agent_trial.py --fresh` run paid the model again
for the same bytes. docs/plans/MODEL-CACHE.md is the design and the measured
cost; this module is its step 1.

**An entry is a fact about bytes, addressed by content.** Its key is a full
sha256 of the media plus everything the model was given (`key`), and what is
stored is the model's raw output, before proofcut's own rules — so a change to
`clean_payload` or `parse_whisper` takes effect without a flush. Nothing the
edit can change is ever stored here.

The digest is full, never sampled: a sampled hash cannot tell two encodes of
the same footage apart, and a store that answers the wrong transcript for a
re-encode is worse than no store. It is memoised per project in
`cache/digests.json` under `(size, mtime_ns)`, the key thumbnails already
trust, so a file is hashed once per project.

Writes are a temp file then `os.replace`, in a directory named by the key, so
two projects writing the same entry at once cannot interleave. The project
lock does not apply: the store is outside every project.

`PROOFCUT_STORE` names another folder, the way `PROOFCUT_WHISPER` names
another binary; the suite points it at a temp dir so no test reads a warm
store it did not fill.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from proofcut import deps

#: Read size for the digest. A 251 MB render hashed at 1.6 GB/s from a cold
#: read on 2026-09-24 (MODEL-CACHE.md § The digest).
_CHUNK = 1 << 20

#: Bumped only if an entry's file layout changes, never for a model change —
#: those are in the key.
_LAYOUT = 1


def root() -> Path:
    """The store's folder: `PROOFCUT_STORE`, else a sibling of `deps.root()`.

    A sibling so both folders move together: `$XDG_DATA_HOME/proofcut/store`,
    or `%LOCALAPPDATA%\\proofcut\\store` on Windows. Not `~/proofcut-work`,
    which is this box's scratch and not the product's (TRAPS.md § Scratch
    directories), and not a project, which is the whole point.
    """
    override = os.environ.get("PROOFCUT_STORE")
    if override:
        return Path(override).expanduser()
    return deps.root().parent / "store"


def stamp(path: Path | str) -> dict[str, int]:
    """`(size, mtime_ns)` of a file, for a key that must miss when it changes.

    Used for the model binaries, so a rebuilt whisper misses rather than
    answering with the old one's words.
    """
    stat = Path(path).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def digest(path: Path | str, memo: Path | None = None) -> str:
    """The full sha256 of `path`, memoised in `memo` under `(size, mtime_ns)`.

    `memo` is a project's `cache/digests.json`; without one the file is hashed
    every time, which at 1.6 GB/s is still well under a second a gigabyte. A
    memo that cannot be read is treated as empty rather than raised: it is
    derived, and losing it costs one hash.
    """
    source = Path(path).expanduser().resolve()
    stat = source.stat()
    if memo is None:
        return _hash(source)

    try:
        known = json.loads(memo.read_text(encoding="utf-8"))
        if not isinstance(known, dict):
            known = {}
    except (OSError, ValueError):
        known = {}
    entry = known.get(str(source))
    if (
        isinstance(entry, dict)
        and entry.get("size") == stat.st_size
        and entry.get("mtime_ns") == stat.st_mtime_ns
        and isinstance(entry.get("sha256"), str)
    ):
        return entry["sha256"]

    value = _hash(source)
    known[str(source)] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": value}
    _write_atomic(memo, json.dumps(known, indent=1, sort_keys=True))
    return value


def key(kind: str, fields: dict[str, Any]) -> str:
    """The entry name for `fields` under `kind`: a sha256 of both, canonically.

    `fields` must be JSON — the digest, the model, and every argument the
    model was given. Anything left out of it is a way for two different runs
    to share an answer, which is what the negative controls in MODEL-CACHE.md
    exist to catch.
    """
    canonical = json.dumps({"kind": kind, "layout": _LAYOUT, **fields}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry(kind: str, fields: dict[str, Any]) -> Path:
    name = key(kind, fields)
    return root() / kind / name[:2] / name / "entry.json"


def get(kind: str, fields: dict[str, Any]) -> Any | None:
    """The stored payload for `fields`, or None on a miss.

    An entry that cannot be read, or whose recorded fields are not these, is
    a miss: the run that follows replaces it.
    """
    path = _entry(kind, fields)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or entry.get("fields") != json.loads(json.dumps(fields)):
        return None
    return entry.get("payload")


def put(kind: str, fields: dict[str, Any], payload: Any) -> None:
    """Store `payload` for `fields`, replacing any entry already there."""
    _write_atomic(_entry(kind, fields), json.dumps({"fields": fields, "payload": payload}))


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
