"""Word-timed captions: ASS out, optionally burned in with ffmpeg.

The one thing to get right here is which clock the captions are on. A
transcript indexes the **source** recording and never renumbers (see
`transcript.py`), but a caption has to fire when the word is *heard*, which is
timeline time — and by the time captions are wanted the timeline is an
accumulation of cuts. So every word is mapped through `Edit.timeline_span`,
and a word that has been cut is simply absent from the output rather than
emitted at a stale time.

That mapping is also why captions are generated from the project rather than
from the whisper JSON directly: the JSON alone cannot know what was removed.

ASS rather than SRT, for two reasons that both matter downstream: it carries
styling (Kdenlive and libass both honour it, SRT forces the renderer to
invent one), and it has `\\k` karaoke tags, which is the only subtitle format
that can express per-word timing *within* a displayed line. A cue is a line of
several words; the `\\k` tags inside it are what makes it word-timed rather
than line-timed.

Burn-in is `ffmpeg -vf ass=…`. It is opt-in, because on this box the edit
leaves through Kdenlive as a project rather than a render (PLAN.md), and a
sidecar `.ass` stays editable there while burned pixels do not.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from proofcut import fonts, progress
from proofcut.timeline import Edit
from proofcut.transcript import Transcript

FFMPEG = "ffmpeg"

#: Whisper occasionally emits a word with start == end. A zero-length interval
#: overlaps no segment, so it would vanish; widen it to something a mapping can
#: catch. One millisecond is far below the 33ms frame grid it will land on.
MIN_WORD = 0.001

#: PlayResX/Y is the coordinate space a style's sizes and margins are quoted
#: in, and libass scales it to whatever it is actually drawing on. So it is a
#: *reference* canvas, not an output resolution — writing the media's real
#: dimensions here would make a 64pt caption fill a 240-line clip and vanish on
#: a 4K one. Every preset below is authored against this height.
REFERENCE_HEIGHT = 1080

DEFAULT_RESOLUTION = (1920, REFERENCE_HEIGHT)


def canvas(width: int | None, height: int | None) -> tuple[int, int]:
    """A reference canvas matching the media's aspect ratio.

    Height is fixed so font sizes mean the same thing everywhere; width follows
    the aspect ratio, because libass scales the two axes independently and a
    16:9 reference over 9:16 footage stretches the glyphs.
    """
    if not width or not height:
        return DEFAULT_RESOLUTION
    return max(1, round(REFERENCE_HEIGHT * (width / height))), REFERENCE_HEIGHT


class CaptionError(Exception):
    """Raised when captions cannot be built, written, or burned in."""


# -- cues ----------------------------------------------------------------


@dataclass(frozen=True)
class CueWord:
    """One word, in *timeline* seconds."""

    text: str
    start: float
    end: float

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class Cue:
    """One displayed line: several words, shown as a unit."""

    words: tuple[CueWord, ...]
    #: When the line leaves the screen. Not `words[-1].end` — see `_hold`.
    end: float

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def karaoke_spans(self) -> list[tuple[CueWord, float, float]]:
        """Each word with the interval its `\\k` tag actually highlights.

        Not `(word.start, word.end)`: a `\\k` duration covers the *gap before*
        its word as well, so the highlight sits on a word from the moment the
        previous one finished. Emitted here rather than derived twice, because
        the preview overlay draws the same highlight the burn-in will and two
        implementations of this rule is two chances for the window to show a
        different film from the file.
        """
        spans = []
        cursor = self.start
        for word in self.words:
            spans.append((word, cursor, max(word.end, cursor)))
            cursor = word.end
        return spans

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "words": [
                {**word.as_dict(), "highlight_start": lit, "highlight_end": until}
                for word, lit, until in self.karaoke_spans()
            ],
        }


#: A word ending a sentence, allowing for a closing quote or bracket after the
#: punctuation. Breaking cues here is what stops a caption running across the
#: full stop, which reads worse than a short line.
_SENTENCE_END = re.compile(r"[.!?…][\"'’”)\]]*$")


def place(edit: Edit, transcripts: dict[str, Transcript]) -> tuple[list[CueWord], int]:
    """Map every transcribed word onto the timeline, dropping what was cut.

    Returns the surviving words in timeline order, and how many were dropped —
    the count is reported rather than swallowed, because "my captions are
    missing a sentence" and "I cut that sentence" look identical otherwise.
    """
    placed: list[CueWord] = []
    dropped = 0
    for clip_id, transcript in transcripts.items():
        for word in transcript.words:
            span = edit.timeline_span(clip_id, word.start, max(word.end, word.start + MIN_WORD))
            if span is None:
                dropped += 1
                continue
            placed.append(CueWord(text=word.text, start=span[0], end=span[1]))

    # Several clips can contribute, and their words interleave only by where
    # they sit on the timeline — source order says nothing across clips.
    placed.sort(key=lambda w: (w.start, w.end))
    return placed, dropped


def group(
    words: list[CueWord],
    *,
    max_words: int | None = None,
    max_gap: float | None = None,
    max_duration: float | None = None,
    hold: float | None = None,
) -> list[Cue]:
    """Gather words into displayable lines.

    Every break rule is measured in *timeline* time, which is the only clock
    the viewer has. That is deliberate and has a consequence worth knowing:
    two words seconds apart in the recording but adjacent after a cut belong
    to one cue, because that is how they now play.

    Omitted arguments come from `DEFAULT_GROUPING` rather than from literals in
    this signature, because a stored style carries the same four numbers and
    two sets of defaults is two answers to what an unstyled project groups on.
    """
    max_words = int(DEFAULT_GROUPING["max_words"] if max_words is None else max_words)
    max_gap = float(DEFAULT_GROUPING["max_gap"] if max_gap is None else max_gap)
    max_duration = float(
        DEFAULT_GROUPING["max_duration"] if max_duration is None else max_duration
    )
    hold = float(DEFAULT_GROUPING["hold"] if hold is None else hold)
    if max_words < 1:
        raise CaptionError("max_words must be at least 1")

    # Two passes, because the word limit is the one break with a choice in it.
    # A silence, a sentence end or an over-long line says exactly where to
    # break; a count only says how many lines a run needs. Filling each line to
    # the limit spends that choice badly — eight words at seven left "moon." on
    # a line of its own in the launch clip — so a run is split into as few lines
    # as the limit allows, as evenly as they go.
    runs: list[list[CueWord]] = []
    current: list[CueWord] = []
    for word in words:
        if current and (
            word.start - current[-1].end > max_gap
            or word.end - current[0].start > max_duration
            or _SENTENCE_END.search(current[-1].text) is not None
        ):
            runs.append(current)
            current = []
        current.append(word)
    if current:
        runs.append(current)

    lines: list[list[CueWord]] = []
    for run in runs:
        count = -(-len(run) // max_words)
        size, extra = divmod(len(run), count)
        at = 0
        for n in range(count):
            step = size + (1 if n < extra else 0)
            lines.append(run[at : at + step])
            at += step

    return [
        Cue(words=tuple(line), end=_hold(line, lines[n + 1] if n + 1 < len(lines) else None, hold))
        for n, line in enumerate(lines)
    ]


def _hold(line: list[CueWord], following: list[CueWord] | None, hold: float) -> float:
    """Keep a line up briefly after its last word, without overlapping the next.

    A cue that vanishes on the final consonant is unreadable, but two cues on
    screen at once is worse — libass will draw both, stacked.
    """
    end = line[-1].end + max(0.0, hold)
    return min(end, following[0].start) if following else end


# -- styling -------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    """One caption look. Colours are ASS `&HAABBGGRR` — alpha first, then BGR."""

    font: str
    size: int
    #: In karaoke, `primary` is the colour a word turns *as it is spoken* and
    #: `secondary` is how it sits before then. With the two equal, `\\k` tags
    #: are still emitted but nothing visibly changes.
    #:
    #: **And it stays primary for the rest of the line.** `\\k` is a fill that
    #: sweeps left to right, not one word lit at a time — measured by burning
    #: this and reading the pixels back, which is also how the preview overlay
    #: was caught disagreeing with it. A single-word highlight is a different
    #: construction and is not what `to_ass` writes.
    #:
    #: **It is not, however, one Dialogue event per word, which this comment
    #: used to claim.** Measured 2026-08-10 by the same method: per-word `\\t`
    #: colour steps inside the *one* event per line light exactly one word at
    #: every sample, where `\\k` on the same words accumulates 1..7. One event
    #: per word is a different feature — libass owns layout, so an event
    #: holding one word centres it alone in the frame. PLAN.md § Per-word
    #: caption animation.
    primary: str
    secondary: str
    outline_colour: str
    back: str
    bold: int
    #: 1 = outline + drop shadow, 3 = opaque box behind the text.
    border_style: int
    outline: float
    shadow: float
    #: numpad layout: 2 is bottom-centre, 8 top-centre.
    alignment: int
    margin_v: int
    karaoke: bool


#: Deliberately small (PLAN.md). The font was `DejaVu Sans` on the reasoning
#: that it ships with most Linux distributions, so a fancier default could not
#: render differently per machine. **Bazzite does not ship it** — `fc-match
#: "DejaVu Sans"` answers `Noto Sans` — so the safe-looking default was the
#: one font on this box guaranteed to be a substitution, and every caption
#: lucid ever burned here drew in a face nobody chose.
#:
#: Tyler settled it 2026-08-12 (~/proofcut-work/archive/spikes/approvals item 07): install the faces
#: we want rather than name whatever happens to resolve. `Outfit` is the brand
#: face for "tagline, titles, labels" (goodsometimes/branding.md § Type) and it
#: is installed here, so captions now match the cards drawn beside them.
#:
#: **Naming an installed font does not make substitution impossible**, on
#: another machine or on this one after a font is removed — `font_match` is
#: still what makes it visible instead of silent, and which face actually drew
#: is still settled by measuring a render, never by `fc-match`.
CAPTION_FONT = "Outfit"
PRESETS: dict[str, Preset] = {
    "clean": Preset(
        font=CAPTION_FONT,
        size=64,
        primary="&H00FFFFFF",
        secondary="&H00FFFFFF",
        outline_colour="&H00000000",
        back="&H80000000",
        bold=-1,
        border_style=1,
        outline=3.0,
        shadow=0.0,
        alignment=2,
        margin_v=80,
        karaoke=False,
    ),
    "karaoke": Preset(
        font=CAPTION_FONT,
        size=64,
        primary="&H0000C8FF",
        secondary="&H00FFFFFF",
        outline_colour="&H00000000",
        back="&H80000000",
        bold=-1,
        border_style=1,
        outline=3.0,
        shadow=0.0,
        alignment=2,
        margin_v=80,
        karaoke=True,
    ),
    "boxed": Preset(
        font=CAPTION_FONT,
        size=56,
        primary="&H00FFFFFF",
        secondary="&H00FFFFFF",
        outline_colour="&HB0000000",
        back="&HB0000000",
        bold=0,
        border_style=3,
        outline=8.0,
        shadow=0.0,
        alignment=2,
        margin_v=90,
        karaoke=False,
    ),
}


def preset(name: str) -> Preset:
    try:
        return PRESETS[name]
    except KeyError:
        raise CaptionError(
            f"unknown caption preset {name!r} — available: {', '.join(sorted(PRESETS))}"
        ) from None


#: CSS `font-weight` to fontconfig's own weight scale. **They are different
#: numbers for the same faces**, and that is not a detail: fontconfig's Bold
#: is 200, so a CSS weight handed straight to `fc-match` is above every real
#: value and every query answers Bold. Measured on this box 2026-08-10 against
#: what librsvg actually draws — CSS 600 renders SemiBold and 700 renders
#: Bold, and only the mapped query agrees with both (PLAN.md § The
#: emphasis-capable quote slot, finding 5).
CSS_TO_FC_WEIGHT = {
    100: 0,  # thin
    200: 40,  # extralight
    300: 50,  # light
    400: 80,  # regular
    500: 100,  # medium
    600: 180,  # demibold
    700: 200,  # bold
    800: 205,  # extrabold
    900: 210,  # black
}


def _fc_pattern(name: str, weight: int | None) -> str:
    """`name` as a fontconfig pattern, with an optional CSS weight.

    The escaping is not decoration. A fontconfig pattern is
    `family-size:key=value`, so an *unescaped* family containing `-` has its
    tail read as a point size and thrown away: `fc-match 'Zilla Slab-24'`
    answers Zilla Slab, reporting a face nobody has as installed. That is the
    silent-wrong-answer direction, so the separators are escaped and a family
    is asked for by its actual name.
    """
    escaped = name.strip()
    for char in ("\\", "-", ":", ","):
        escaped = escaped.replace(char, "\\" + char)
    if weight is None:
        return escaped
    return f"{escaped}:weight={CSS_TO_FC_WEIGHT[weight]}"


def _nearest_css_weight(weight: int) -> int:
    """The `CSS_TO_FC_WEIGHT` key nearest `weight` — CSS allows 1..1000."""
    return min(CSS_TO_FC_WEIGHT, key=lambda known: (abs(known - weight), known))


def font_match(name: str, *, weight: int | None = None) -> dict[str, Any]:
    """What fontconfig will actually hand libass for `name`.

    A missing font is the one styling failure with no symptom: libass
    substitutes without a warning, ffmpeg exits 0, and the render is in a
    typeface nobody picked — and the browser preview substitutes too, by its
    own rules, so the two do not even agree on the wrong answer. `fc-match`
    is the same resolution libass performs, so asking it is the check.

    `available` is null rather than false when `fc-match` is missing: "we
    could not tell" and "the font is not here" are different answers, and
    reporting the first as the second would send someone installing a font
    they already have.

    `weight` is a **CSS** weight and is mapped through `CSS_TO_FC_WEIGHT`
    before it is asked, because the two scales are not the same numbers. It
    is optional because it only changes the answer for a family with more
    than one weight installed, and because the ASS side has no CSS weight to
    give — libass takes a bold *flag*, whose resolution through fontconfig
    has not been measured here, so captions ask by family exactly as before
    rather than guessing 700.

    `style` comes back alongside `resolves_to` for the reason the weight
    argument exists at all: two faces of one family report the *same* family
    name, so the family alone cannot say which of them got picked.
    """
    if weight is not None:
        weight = _nearest_css_weight(int(weight))
    unknown = {"font": name, "weight": weight, "available": None, "resolves_to": None, "style": None}
    try:
        found = subprocess.run(
            ["fc-match", "--format=%{family}|%{style}", _fc_pattern(name, weight)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,  # a non-zero fc-match is "cannot tell", not a failure
        )
    except (OSError, subprocess.SubprocessError):
        return unknown

    matched, _, style = (found.stdout or "").partition("|")
    families = [f.strip() for f in matched.split(",") if f.strip()]
    if not families:
        return unknown

    wanted = name.strip().casefold()
    available = any(f.casefold() == wanted for f in families)
    styles = [s.strip() for s in style.split(",") if s.strip()]
    return {
        "font": name,
        "weight": weight,
        "available": available,
        "resolves_to": families[0],
        "style": styles[0] if styles else None,
        **(
            {}
            if available
            else {
                "warning": (
                    f"{name!r} is not installed — libass will draw these captions in "
                    f"{families[0]!r} without saying so, and the preview will pick its "
                    "own substitute"
                )
            }
        ),
    }


# -- the style a project stores -------------------------------------------
#
# `Preset` above is ASS's vocabulary. What a person — or the agent — asks for
# is not, and three of ASS's fields are actively misleading handed straight to
# a caller:
#
# * `PrimaryColour` is the colour a word turns *as it is spoken* and
#   `SecondaryColour` is how it sits before then. So with karaoke on, the base
#   text colour is the *secondary* one. Set "primary" to yellow expecting
#   yellow captions and you get white captions that flash yellow.
# * the alpha byte is **transparency**, not opacity: `&H00…` is fully opaque
#   where CSS's `#rrggbbaa` reads a trailing `00` as fully transparent. The two
#   conventions are exact inverses, so a value copied across without the
#   inversion is not slightly wrong, it is invisible.
# * `BorderStyle` is a two-value enum, not a border width — the width is
#   `Outline`, a different field with a similar name.
#
# So a stored style speaks in `text`/`highlight`/`box`/`position`, and this
# section is the only place the translation happens. Downstream still gets a
# `Preset`, which is what `to_ass` takes.

#: Named positions to ASS alignment (the numpad layout). Named because
#: `alignment=8` is unreadable and `alignment=9` is a plausible typo for it
#: that lands the captions in a different corner.
ALIGNMENTS: dict[str, int] = {
    "bottom-left": 1,
    "bottom": 2,
    "bottom-right": 3,
    "left": 4,
    "middle": 5,
    "right": 6,
    "top-left": 7,
    "top": 8,
    "top-right": 9,
}
_POSITIONS = {value: name for name, value in ALIGNMENTS.items()}

#: A deliberately tiny vocabulary — enough that "make the captions yellow"
#: needs no hex, not so much that this becomes a colour database. Anything
#: else is `#rgb`, `#rrggbb`, `#rrggbbaa`, or ASS's own `&H…` passed through.
NAMED_COLOURS: dict[str, str] = {
    "white": "#ffffff",
    "black": "#000000",
    "yellow": "#ffd400",
    "amber": "#ffb000",
    "red": "#e5484d",
    "green": "#30a46c",
    "blue": "#3b82f6",
    "cyan": "#22d3ee",
    "magenta": "#e93d82",
    "grey": "#8b8b8b",
    "gray": "#8b8b8b",
    "transparent": "#00000000",
}

_HEX = re.compile(r"^#?([0-9a-fA-F]{3,8})$")
_ASS_COLOUR = re.compile(r"^&H([0-9a-fA-F]{1,8})&?$")


def ass_colour(value: str) -> str:
    """Any colour a caller might write, as ASS `&HAABBGGRR`.

    Accepts a name from `NAMED_COLOURS`, `#rgb`, `#rrggbb`, `#rrggbbaa`, or an
    `&H…` value passed straight through. The alpha inversion happens here and
    nowhere else: CSS alpha is opacity, ASS alpha is transparency, so a fully
    opaque colour is `ff` on one side and `00` on the other.
    """
    if not isinstance(value, str) or not value.strip():
        raise CaptionError("a colour cannot be empty")
    text = value.strip()

    passthrough = _ASS_COLOUR.match(text)
    if passthrough:
        return f"&H{passthrough.group(1).upper().zfill(8)}"

    text = NAMED_COLOURS.get(text.lower(), text)
    match = _HEX.match(text)
    if match is None:
        raise CaptionError(
            f"unreadable colour {value!r} — write #rrggbb, #rrggbbaa, an ASS "
            f"&HAABBGGRR value, or one of: {', '.join(sorted(NAMED_COLOURS))}"
        )

    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    if len(digits) == 6:
        digits += "ff"
    if len(digits) != 8:
        raise CaptionError(f"unreadable colour {value!r} — 3, 6 or 8 hex digits, not {len(digits)}")

    red, green, blue, alpha = (int(digits[n : n + 2], 16) for n in (0, 2, 4, 6))
    return f"&H{255 - alpha:02X}{blue:02X}{green:02X}{red:02X}"


def css_colour(value: str) -> str:
    """The inverse of `ass_colour`, for the preview overlay.

    The browser draws the same look the burn-in will, and it can only do that
    from a colour it understands — so the translation is owned here rather
    than reimplemented in JavaScript, which would be a second place for the
    alpha inversion to be got wrong.
    """
    match = _ASS_COLOUR.match(value.strip())
    if match is None:
        raise CaptionError(f"not an ASS colour: {value!r}")
    digits = match.group(1).zfill(8)
    alpha, blue, green, red = (int(digits[n : n + 2], 16) for n in (0, 2, 4, 6))
    return f"#{red:02x}{green:02x}{blue:02x}{255 - alpha:02x}"


#: Grouping defaults. They live here rather than only in `group`'s signature
#: because a stored style carries them: how a line breaks is as much of the
#: look as the font is, and a preview that grouped differently from the burn-in
#: would be showing a caption the file will never contain.
DEFAULT_GROUPING: dict[str, float] = {
    "max_words": 7,
    "max_gap": 0.7,
    "max_duration": 6.0,
    "hold": 0.3,
}

#: Every field a stored style may carry, with the type each is coerced to.
#: `preset` is the base; the rest override one of its fields. Anything not
#: listed is refused rather than ignored, because a typo'd key that is
#: silently dropped looks exactly like a setting that had no effect.
STYLE_FIELDS: dict[str, str] = {
    "preset": "name",
    "font": "text",
    "size": "int",
    "text": "colour",
    "highlight": "colour",
    "outline_colour": "colour",
    "box_colour": "colour",
    "bold": "bool",
    "box": "bool",
    "outline_width": "float",
    "shadow": "float",
    "position": "position",
    "margin": "int",
    "karaoke": "bool",
    "max_words": "int",
    "max_gap": "float",
    "max_duration": "float",
    "hold": "float",
}

DEFAULT_PRESET = "clean"


@dataclass(frozen=True)
class Style:
    """A resolved caption look: the ASS style, plus how lines are broken.

    `stored` is what the project actually holds — a base preset name and only
    the fields overridden on top of it, never a flattened copy. That is what
    makes "the boxed preset, but bigger" survive a later improvement to the
    boxed preset, and what keeps the manifest readable by a person.
    """

    ass: Preset
    max_words: int
    max_gap: float
    max_duration: float
    hold: float
    stored: dict[str, Any]

    @property
    def base(self) -> str:
        return str(self.stored.get("preset", DEFAULT_PRESET))

    @property
    def grouping(self) -> dict[str, Any]:
        return {
            "max_words": self.max_words,
            "max_gap": self.max_gap,
            "max_duration": self.max_duration,
            "hold": self.hold,
        }

    def describe(self) -> dict[str, Any]:
        """The style as three views of one thing, because one is never enough.

        `stored` is what the project holds and what a later call edits;
        `resolved` is every field in force after the preset is applied, which
        is the only way to answer "so what colour *is* it" — and it quotes
        colours in CSS, because that is the form a reader can check and the
        preview overlay can draw. `ass` is the same four colours in the form
        that reaches the subtitle file, kept because the two differ by an
        alpha inversion and seeing both is how that stays honest.
        """
        colours = {
            "text": self.ass.secondary if self.ass.karaoke else self.ass.primary,
            "highlight": self.ass.primary,
            "outline_colour": self.ass.outline_colour,
            "box_colour": self.ass.back,
        }
        return {
            "stored": dict(self.stored),
            "resolved": {
                "preset": self.base,
                "font": self.ass.font,
                "size": self.ass.size,
                **{name: css_colour(value) for name, value in colours.items()},
                "bold": self.ass.bold != 0,
                "box": self.ass.border_style == 3,
                "outline_width": self.ass.outline,
                "shadow": self.ass.shadow,
                "position": _POSITIONS.get(self.ass.alignment, str(self.ass.alignment)),
                "margin": self.ass.margin_v,
                "karaoke": self.ass.karaoke,
                **self.grouping,
            },
            "ass": colours,
        }


def _coerce(field: str, kind: str, value: Any) -> Any:
    if kind == "colour":
        return ass_colour(value if isinstance(value, str) else str(value))
    if kind in ("name", "text"):
        name = str(value).strip()
        if not name:
            raise CaptionError(f"{field} cannot be empty")
        return name
    if kind == "position":
        name = str(value).strip().lower().replace("_", "-")
        if name not in ALIGNMENTS:
            raise CaptionError(
                f"unknown caption position {value!r} — one of: {', '.join(ALIGNMENTS)}"
            )
        return name
    if kind == "bool":
        return bool(value)
    try:
        return int(value) if kind == "int" else float(value)
    except (TypeError, ValueError):
        raise CaptionError(f"{field} must be a number, not {value!r}") from None


def normalise(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Check and canonicalise a stored style, without resolving it.

    Colours come out as ASS values whatever they went in as, so the manifest
    holds one representation rather than whichever the last caller happened to
    type. An unknown key is an error here — see `STYLE_FIELDS`.
    """
    if stored is None:
        return {}
    if not isinstance(stored, dict):
        raise CaptionError("a caption style must be a JSON object")

    unknown = sorted(set(stored) - set(STYLE_FIELDS))
    if unknown:
        raise CaptionError(
            f"unknown caption style field(s) {', '.join(unknown)} — "
            f"settable: {', '.join(sorted(STYLE_FIELDS))}"
        )

    clean: dict[str, Any] = {}
    for field, kind in STYLE_FIELDS.items():
        if field in stored and stored[field] is not None:
            clean[field] = _coerce(field, kind, stored[field])
    if "preset" in clean:
        preset(clean["preset"])  # fail here, not at resolve time
    if clean.get("size", 1) < 1:
        raise CaptionError("size must be at least 1")
    if clean.get("max_words", 1) < 1:
        raise CaptionError("max_words must be at least 1")
    return clean


def resolve(stored: dict[str, Any] | None) -> Style:
    """A stored style — base preset plus overrides — as something drawable.

    The karaoke swap happens here: `text` is what a word looks like before it
    is spoken and `highlight` what it turns into, which is ASS's *secondary*
    and *primary* in that order. With karaoke off there is no "before", so
    both ASS slots take `text` and `highlight` is carried but inert — stored
    rather than dropped, so turning karaoke on does not lose the colour that
    was chosen for it.
    """
    clean = normalise(stored)
    base = preset(str(clean.get("preset", DEFAULT_PRESET)))

    karaoke = bool(clean.get("karaoke", base.karaoke))
    text = clean.get("text", base.secondary if base.karaoke else base.primary)
    # A non-karaoke preset has no highlight colour to inherit — its two slots
    # hold the same value — so switching karaoke on over one would otherwise
    # produce a word-highlight nobody can see, which reads as the flag not
    # working. Fall back to the karaoke preset's own colour instead: the one
    # place in this file that has already decided what a highlight looks like.
    highlight = clean.get("highlight", base.primary if base.karaoke else PRESETS["karaoke"].primary)
    box = clean.get("box", base.border_style == 3)

    ass = Preset(
        font=str(clean.get("font", base.font)),
        size=int(clean.get("size", base.size)),
        primary=highlight if karaoke else text,
        secondary=text,
        outline_colour=clean.get("outline_colour", base.outline_colour),
        back=clean.get("box_colour", base.back),
        bold=(-1 if clean["bold"] else 0) if "bold" in clean else base.bold,
        border_style=3 if box else 1,
        outline=float(clean.get("outline_width", base.outline)),
        shadow=float(clean.get("shadow", base.shadow)),
        alignment=ALIGNMENTS[clean["position"]] if "position" in clean else base.alignment,
        margin_v=int(clean.get("margin", base.margin_v)),
        karaoke=karaoke,
    )
    grouping = {**DEFAULT_GROUPING, **{k: clean[k] for k in DEFAULT_GROUPING if k in clean}}
    return Style(
        ass=ass,
        max_words=int(grouping["max_words"]),
        max_gap=float(grouping["max_gap"]),
        max_duration=float(grouping["max_duration"]),
        hold=float(grouping["hold"]),
        stored=clean,
    )


# -- ASS -----------------------------------------------------------------


def _ass_time(seconds: float) -> str:
    """`H:MM:SS.cc` — ASS keeps centiseconds, and only one hour digit."""
    total = max(0, round(max(0.0, seconds) * 100))
    hours, total = divmod(total, 360_000)
    minutes, total = divmod(total, 6_000)
    secs, centis = divmod(total, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


#: `{` and `}` open and close override blocks and `\` starts a tag, so a word
#: containing one would be swallowed as markup. Transliterate rather than
#: escape: ASS has no escape for these inside dialogue text.
_UNSAFE = str.maketrans({"{": "(", "}": ")", "\\": "/"})


def _escape(text: str) -> str:
    return " ".join(text.translate(_UNSAFE).split())


def _dialogue_text(cue: Cue, style: Preset) -> str:
    if not style.karaoke:
        return _escape(cue.text)

    # Each \k is the duration of its own word *plus the gap before it*, so the
    # highlight stays locked to the audio instead of drifting forward by the
    # accumulated silence between words. `karaoke_spans` owns that rule; the
    # preview overlay reads the same spans off `as_dict`.
    return " ".join(
        f"{{\\k{max(0, round((until - lit) * 100))}}}{_escape(word.text)}"
        for word, lit, until in cue.karaoke_spans()
    )


def to_ass(
    cues: list[Cue],
    *,
    style: Preset,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    title: str = "proofcut",
) -> str:
    """Render cues as an ASS subtitle file."""
    width, height = resolution
    lines = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: proofcut,{style.font},{style.size},{style.primary},{style.secondary},"
            f"{style.outline_colour},{style.back},{style.bold},0,0,0,100,100,0,0,"
            f"{style.border_style},{style.outline:g},{style.shadow:g},{style.alignment},"
            f"{round(width * 0.08)},{round(width * 0.08)},{style.margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines.extend(
        f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},proofcut,,0,0,0,,"
        f"{_dialogue_text(cue, style)}"
        for cue in cues
    )
    return "\n".join(lines) + "\n"


# -- burn-in -------------------------------------------------------------


def burn(video: Path | str, subtitles: Path | str, output: Path | str) -> Path:
    """Burn `subtitles` into `video` with ffmpeg, writing `output`.

    The subtitle file is staged into a temporary directory under a fixed name
    and ffmpeg is run from there. That is not tidiness: the `ass=` filter
    argument lives inside a filtergraph, where `:`, `,`, `'` and `\\` all have
    meaning, and project paths on this box contain spaces and punctuation.
    Staging sidesteps the escaping problem instead of trying to win it.
    """
    source = Path(video).expanduser().resolve()
    if not source.exists():
        raise CaptionError(f"no video to burn captions onto: {source}")

    destination = Path(output).expanduser()
    if destination.resolve() == source:
        # ffmpeg refuses to write the file it is reading, and says so in a dozen
        # lines of library banner that hid the reason from every agent that hit it.
        raise CaptionError(
            f"the captioned video cannot be {source.name} itself, the render being "
            "burned onto — pass a different `burn_output` (or leave it unset)."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="proofcut-ass-") as tmp:
        staged = Path(tmp) / "proofcut.ass"
        staged.write_text(Path(subtitles).read_text(encoding="utf-8"), encoding="utf-8")
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(source),
            "-vf",
            f"ass=proofcut.ass{fonts.libass_fontsdir(Path(tmp))}",
            "-c:a",
            "copy",
            str(destination.resolve()),
        ]
        try:
            if progress.cancel_armed():
                progress.run(cmd, cwd=tmp, check=True)
            else:
                subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise CaptionError(f"{FFMPEG} not found on PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            raise CaptionError(
                "ffmpeg failed burning in captions:\n" + "\n".join(detail[-12:])
            ) from exc

    return destination
