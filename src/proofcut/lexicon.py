"""The project's lexicon: how its words are said, and how they are spelled.

`lexicon.json` in a project (or a file named explicitly) is
`{"say": {written: respelling}, "hear": {heard: canonical}}`:

* `say` respells what the voice model is *given* — "Clarice" → "Clariss" is
  how a mispronunciation is fixed, because instruct prompts made renders
  worse (local-llm round 3). `vo_synth` alone reads it.
* `hear` is the right spelling of what whisper heard — "long legs" →
  "Longlegs", "rough" → "ruff". `vo_synth` folds both sides of its WER
  through it, so a spelling stops costing error budget meant for misreads,
  and **captions print through it** (docs/plans/DAYDREAM.md § Caption
  reveal and corrections, designed): a standing correction list, Daydream's
  "remember this", kept as content in the project.

**Matching is by whole words, never by substring.** A substring fold is
harmless to a WER score, where both sides fold alike, and wrong on screen:
`rough → ruff` would print "ruffly". Tokens compare lower-cased with their
surrounding punctuation stripped, a multi-word key matches consecutive words,
and the longest key wins where two start on the same word. `fold_text` and
`matches` share this, so the file has one meaning.

**A caption fold is display only.** The transcript file is never touched —
word indices address every cue — and `verify` never sees one, because it
compares whisper against whisper.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from proofcut import timeline as tl

FILENAME = "lexicon.json"
KINDS = ("say", "hear")


class LexiconError(tl.TimelineError):
    """A lexicon that cannot be read, or an entry that cannot be kept."""


def project_path(project_root: Path) -> Path:
    return Path(project_root) / FILENAME


def load(path: Path | str | None, project_root: Path) -> tuple[dict[str, Any], str | None]:
    """The lexicon, and the file it came from (None when there is none).

    An explicit path wins and must exist; otherwise `<project>/lexicon.json`
    is picked up when present, because pronunciation and spelling fixes are
    content, not tooling, and the project is where the content lives.
    """
    if path is not None:
        p = Path(path).expanduser()
        if not p.is_file():
            raise LexiconError(f"no lexicon at {p}")
    else:
        p = project_path(project_root)
        if not p.is_file():
            return {}, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LexiconError(f"lexicon {p} is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise LexiconError(f"lexicon {p} must be a JSON object")
    for key in data:
        if key not in KINDS:
            raise LexiconError(f'lexicon {p} has an unknown key {key!r} — it takes "say" and "hear"')
        if not isinstance(data[key], dict):
            raise LexiconError(f"lexicon {p}'s {key!r} must be a JSON object")
    return data, str(p)


def save(project_root: Path, data: dict[str, Any]) -> Path:
    """Write `<project>/lexicon.json`, or remove it when nothing is left.

    Written through a temporary file and a rename, so a reader never sees
    half a file. An empty lexicon is no file rather than `{}`, because "no
    lexicon" is the state every other project is in.
    """
    target = project_path(project_root)
    kept = {kind: dict(data[kind]) for kind in KINDS if data.get(kind)}
    if not kept:
        target.unlink(missing_ok=True)
        return target
    fd, tmp = tempfile.mkstemp(prefix=".lexicon-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(kept, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def apply_say(text: str, lexicon: dict[str, Any]) -> str:
    """Respell each `say` word, whole words only, case-insensitively."""
    for written, respelt in (lexicon.get("say") or {}).items():
        text = re.sub(rf"\b{re.escape(written)}\b", respelt, text, flags=re.IGNORECASE)
    return text


_EDGE = re.compile(r"^([^\w']*)(.*?)([^\w']*)$", re.DOTALL)


def split_token(token: str) -> tuple[str, str, str]:
    """`(leading punctuation, core, trailing punctuation)` — '"Rough."' is
    `('"', 'Rough', '."')`."""
    match = _EDGE.match(token)
    assert match is not None  # the pattern matches every string
    return match.group(1), match.group(2), match.group(3)


def key_of(token: str) -> str:
    """The form a token is matched by: its core, lower-cased."""
    return split_token(token)[1].lower()


def key_words(heard: str) -> tuple[str, ...]:
    """A `hear` key as the word keys it matches, refusing one with no words."""
    words = tuple(k for k in (key_of(t) for t in heard.split()) if k)
    if not words:
        raise LexiconError(f"{heard!r} has no words to match")
    return words


def _table(lexicon: dict[str, Any]) -> dict[tuple[str, ...], str]:
    return {key_words(heard): str(canonical) for heard, canonical in (lexicon.get("hear") or {}).items()}


def matches(tokens: Sequence[str], lexicon: dict[str, Any]) -> list[tuple[int, int, str, str]]:
    """Every `hear` fold over `tokens`: `(first, stop, heard_key, canonical)`.

    `stop` is exclusive. Left to right, longest key first where two start on
    the same token, never overlapping — a folded run is not folded again.
    """
    table = _table(lexicon)
    if not table:
        return []
    longest = max(len(k) for k in table)
    keys = [key_of(t) for t in tokens]
    found: list[tuple[int, int, str, str]] = []
    at = 0
    while at < len(keys):
        for size in range(min(longest, len(keys) - at), 0, -1):
            run = tuple(keys[at : at + size])
            if run in table:
                found.append((at, at + size, " ".join(run), table[run]))
                at += size
                break
        else:
            at += 1
    return found


def display(tokens: Sequence[str], canonical: str) -> str:
    """How a folded run of `tokens` prints.

    The first token's leading and the last token's trailing punctuation stay
    — captions break lines on a sentence end. The canonical prints as written,
    except that an all-lower-case canonical takes the first token's opening
    capital, so a sentence-opening "Rough" becomes "Ruff".
    """
    lead, core, _ = split_token(tokens[0])
    trail = split_token(tokens[-1])[2]
    text = canonical
    if text and text == text.lower() and core[:1].isupper():
        text = text[0].upper() + text[1:]
    return f"{lead}{text}{trail}"


def fold_text(text: str, lexicon: dict[str, Any]) -> str:
    """`text` lower-cased with every `hear` fold applied — the WER's form."""
    tokens = text.split()
    out: list[str] = []
    at = 0
    for first, stop, _heard, canonical in matches(tokens, lexicon):
        out.extend(tokens[at:first])
        out.append(display(tokens[first:stop], canonical))
        at = stop
    out.extend(tokens[at:])
    return " ".join(out).lower()
