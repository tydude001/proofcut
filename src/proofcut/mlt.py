"""Writing MLT — step 4 of the layered timeline, the multi-source path only.

Single-source export still goes through `auto-editor --export kdenlive` and
nothing here touches it. This file exists because on the multi-source path
auto-editor writes nothing at all: its kdenlive exporter **refuses, exit 2**,
and its renderer degrades a two-`src` timeline to 720x576 with **exit 0**
(CLAUDE.md; HISTORY.md § The multi-track costing spike). `melt` has no
source-count gate — 23 distinct sources rendered 1920x1080 from the real
Scream assembly — so the picture lane is rendered by melt, and somebody has to
hand melt a document. That somebody is now this module.

**Generated, never mutated.** The rule "lucid never writes MLT" is narrowed
rather than dropped (PLAN.md § What this does to "lucid never writes MLT"):
every document here is built from scratch out of the `Edit` plus the cue
table, so there is still no in-place MLT surgery anywhere in proofcut, and the
document is disposable — rebuild it, don't patch it.

Generating means owning MLT's two sharp edges, both of which produce a wrong
render rather than an error:

- **`<blank>`.** A playlist shorter than its neighbours pads with blank, which
  is runtime every cue downstream of it is blind to — the cut positions still
  say what they said and the picture is now late. No *lane* here ever emits a
  `<blank>`: both playlists are contiguous by construction, and `document()`
  refuses a picture lane whose frames do not sum to exactly the audio's. The
  one place a blank is written is a **split pane's** overlay playlist, where
  it is the point rather than an accident — the pane covers the stretches its
  clip is split over and nothing else, and a blank on an overlay track shows
  the track below. That is the opposite case from the one this rule guards:
  the danger is a lane *silently* becoming short, and a pane track is short
  deliberately and by the same frame arithmetic as the lane it sits over,
  which `document()` checks.
- **The declared lengths.** melt renders to the *longest* declared length in
  the document, not to the playlist, so a stale one pads the render out with
  a frozen frame and still exits 0. There are four of them (the two track
  tractors' `out`, the sequence tractor's `out`, and the black background
  producer's `length`), plus the outer project tractor. They are all written
  from one number and then read back and checked — `declared_frames()` is the
  assertion PLAN.md asked for in place of a comment.

Positions are **frame integers, not timecode**. MLT parses a bare integer as a
frame position and `HH:MM:SS.mmm` as a clock time; the clock form is what
Kdenlive and auto-editor write, and it is the form that cost auto-editor a
frame — millisecond text cannot name a 1/29.97 s edge exactly. Frames can, so
frames are what this writes. And `out` is **frame-inclusive**: an entry of
`frames` frames starting at `src_in` ends at `src_in + frames - 1`, which is
the arithmetic `KNOWN_TAIL_FRAME` records auto-editor getting wrong in the
other direction (it writes the count where the last index belongs, and melt
renders one black frame past the end).

Picture is silent. Film under a VO plays with its audio muted — the producers
carry `audio_index=-1`, and the sequence gets no audio mix for the picture
track because there is no picture audio to mix. Unmuting a shot is a real
feature (it needs a producer of its own and a second `mix` transition, since
`audio_index` is a producer property and not a per-entry one) and it lands
with the cue that asks for it, not speculatively.
"""

from __future__ import annotations

import math
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import pairwise
from math import gcd
from pathlib import Path
from typing import Any

#: What auto-editor writes and what the Kdenlive on this box reads. The
#: attribute is advisory — melt does not gate on it — but a document claiming
#: a version nobody here runs is a lie a reader would have to chase.
MLT_VERSION = "7.22.0"

#: The canvas when the project has no picture clip to take one from. A cue
#: table can be all cards, and a card is whatever size it was drawn at.
DEFAULT_RESOLUTION = (1920, 1080)

#: How long a still image claims to be. A `qimage` producer needs *some*
#: length or it ends after one frame; four hours is Kdenlive's own answer and
#: it is longer than any shot will ever be. `eof=continue` holds the last
#: frame if a shot somehow outruns it anyway, so the failure is a freeze
#: rather than a gap that MLT would fill with blank.
IMAGE_LENGTH_SECONDS = 4 * 3600


class MLTError(Exception):
    """Raised when a timeline cannot be written as MLT, or when the document
    written disagrees with the frame total it was built from."""


@dataclass(frozen=True)
class Entry:
    """`frames` frames of `resource`, read from `src_in` on.

    Frames throughout, and a *count* rather than an end — `src_out` derives
    the frame-inclusive end in one place so the off-by-one lives exactly once.

    `has_video` is a fact about the file, and it has to be one: a document
    with a wav and an mp4 on the same track cannot answer "does this carry
    picture" once for both, and answering it wrong tells MLT to render black
    frames off the wav.
    """

    resource: str
    src_in: int
    frames: int
    is_image: bool = False
    has_video: bool = False
    #: Audio fade lengths in frames, drawn as one entry-attached `volume`
    #: filter (`_playlist`). Zero means no filter at all, which is what keeps
    #: every fade-free document byte-identical to before fades existed. Only
    #: the music lane sets these today; the mechanism is generic.
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    #: A flat, non-fading level shift in dB — the plateau `_fade_level`
    #: ramps to and holds at, distinct from a fade (which only shapes the
    #: edges). Zero is exactly unity gain (measured, `FADE_FLOOR_DB`'s own
    #: note), so a document with every entry at the default renders
    #: byte-identically to before this field existed. The head's cold-open
    #: audio is the first caller to set it to something else.
    gain_db: float = 0.0
    #: Whether a fade edge is half of a crossfade, and so follows the
    #: equal-power curve rather than a straight line in dB. Two dB-linear
    #: fades crossing sum to a hole: on a 2.5 s crossfade the outgoing and
    #: incoming passages read −50 and −54 dB at 0.9 s against a −24 plateau
    #: (docs/plans/NATIVE.md § A1). False keeps every other fade, and every
    #: document before this field, byte-identical.
    crossfade_in: bool = False
    crossfade_out: bool = False
    #: A gain envelope in dB on top of the plateau and the fades — `(offset,
    #: dB)` with offsets relative to the entry's first frame, straight lines
    #: between them. The duck (`duck.py`) is the one caller. Empty writes
    #: exactly what an entry wrote before this field, and **anything that
    #: splits an entry must cut these with `slice_gain_keys`**, or the split
    #: pieces play undipped at exit 0.
    gain_keys: tuple[tuple[int, float], ...] = ()
    #: A retime's slice for this entry: `(position, source seconds)` keys,
    #: linear between them, with positions counted from the entry's first
    #: render frame. Set, the entry reads its own `timeremap` chain from
    #: position 0 and `src_in` is 0; empty writes exactly what an entry wrote
    #: before retimes existed. `retime.warp_lane` is the one maker.
    #: docs/plans/NATIVE.md § B5, designed.
    time_map: tuple[tuple[int, float], ...] = ()

    @property
    def src_out(self) -> int:
        """MLT's `out` is the last frame *index*, not the frame count."""
        return self.src_in + self.frames - 1


#: The animations an overlay enters and leaves with, by the name
#: `overlay_add` takes. `rise` travels `OVERLAY_RISE` (at 1080 lines, scaled
#: to the canvas) while it fades; `fade` only fades; `none` is a cut.
#: docs/plans/NATIVE.md § B3, designed.
OVERLAY_MOTIONS = ("fade", "rise", "none")

#: How far a `rise` travels, in pixels of a 1080-line canvas — the launch
#: clip's headline (`clip.py` § overlay).
OVERLAY_RISE = 24


@dataclass(frozen=True)
class Overlay:
    """A transparent still drawn over the film from `start` for `frames` frames.

    Timeline frames, like every position in this module. The still is a
    canvas-sized PNG, so where its type sits is the template's business and
    the node's `qtblend` filter only animates offset and opacity — `rect`'s
    fifth value is opacity, frame-exact (`~/proofcut-work/spikes/overlay-probe`).
    `in_frames`/`out_frames` are the entrance and exit lengths, each with a
    motion from `OVERLAY_MOTIONS` and an easing from `EASINGS`.
    """

    resource: str
    start: int
    frames: int
    in_motion: str = "fade"
    in_frames: int = 0
    in_ease: str = "ease-out"
    out_motion: str = "fade"
    out_frames: int = 0
    out_ease: str = "ease-in"
    #: How far a `rise` travels, in pixels at 1080 lines — a lower third's
    #: footnote layer rises less than its headline (RECUT.md step 7).
    rise: int = OVERLAY_RISE

    @property
    def end(self) -> int:
        """The first timeline frame after the overlay."""
        return self.start + self.frames


def overlay_lanes(overlays: list[Overlay]) -> list[int]:
    """The lane each overlay is drawn on, in list order.

    **List order is stacking order**: a later overlay that overlaps an
    earlier one is drawn above it, so it goes on a lane higher than every
    earlier one it overlaps — and on the lowest such lane that is free for
    its whole span, so a film of lower thirds that never overlap writes one
    lane, not one per overlay. One playlist cannot hold two stills at once,
    which is `music2`'s reason for a second lane too.
    """
    lanes: list[int] = []
    for index, overlay in enumerate(overlays):
        floor = 0
        for other, lane in zip(overlays[:index], lanes):
            if other.start < overlay.end and overlay.start < other.end:
                floor = max(floor, lane + 1)
        lane = floor
        while any(
            placed == lane and other.start < overlay.end and overlay.start < other.end
            for other, placed in zip(overlays[:index], lanes)
        ):
            lane += 1
        lanes.append(lane)
    return lanes


def overlay_rect(overlay: Overlay, resolution: tuple[int, int]) -> str | None:
    """The overlay's `qtblend` `rect` keys, or None where it never moves or fades.

    Keys count from the *producer's* frame, and the entry reads its still
    from frame 0, so they are the overlay's own frame numbers. **A moving key
    is drawn one pixel larger** (`_off_unity`) and a resting key at exactly
    the canvas: a pure 1:1 rise snaps to whole rows, and nudging the resting
    key too leaves the type sub-pixel off and resampled. With only the moving
    key nudged the scale reaches unity gradually, so the move is continuous
    and lands on the exact row (spike `rise-mixed`). One operator shapes both
    position and opacity, on the key that leaves.
    """
    width, height = resolution
    rest = (0, 0, width, height)
    travel = round(overlay.rise * height / 1080)

    def key(frame: int, operator: str, rect: tuple[int, int, int, int], opacity: int) -> str:
        return f"{frame}{operator}={rect[0]} {rect[1]} {rect[2]} {rect[3]} {opacity}"

    def away(motion: str) -> tuple[int, int, int, int]:
        if motion == "rise":
            return _off_unity((0, travel, width, height))
        return rest

    last = overlay.frames - 1
    keys: list[str] = []
    if overlay.in_motion != "none" and overlay.in_frames:
        keys.append(key(0, EASINGS[overlay.in_ease], away(overlay.in_motion), 0))
        keys.append(key(overlay.in_frames, "", rest, 1))
    if overlay.out_motion != "none" and overlay.out_frames:
        leave = last - overlay.out_frames
        if keys and leave == overlay.in_frames:
            keys[-1] = key(leave, EASINGS[overlay.out_ease], rest, 1)
        else:
            keys.append(key(leave, EASINGS[overlay.out_ease], rest, 1))
        keys.append(key(last, "", away(overlay.out_motion), 0))
    return ";".join(keys) or None


def _check_overlay(overlay: Overlay, total_frames: int) -> None:
    where = f"the overlay {overlay.resource!r} at frame {overlay.start}"
    if overlay.frames < 1 or overlay.start < 0 or overlay.end > total_frames:
        raise MLTError(
            f"{where} runs frames {overlay.start}..{overlay.end} of a "
            f"{total_frames}-frame timeline — an overlay has to sit inside the film"
        )
    for motion, frames, ease in (
        (overlay.in_motion, overlay.in_frames, overlay.in_ease),
        (overlay.out_motion, overlay.out_frames, overlay.out_ease),
    ):
        if motion not in OVERLAY_MOTIONS:
            raise MLTError(f"{where}: no motion {motion!r} (there are: {', '.join(OVERLAY_MOTIONS)})")
        if ease not in EASINGS:
            raise MLTError(f"{where}: no easing {ease!r} (there are: {', '.join(EASINGS)})")
        if frames < 0:
            raise MLTError(f"{where}: a negative animation length")
    used = (overlay.in_frames if overlay.in_motion != "none" else 0) + (
        overlay.out_frames if overlay.out_motion != "none" else 0
    )
    if used > overlay.frames - 1:
        raise MLTError(
            f"{where} is {overlay.frames} frames and its entrance and exit take "
            f"{used} — shorten the animations or lengthen the overlay"
        )


#: The fewest frames a one-shot's file may be: a file MLT counts as one frame
#: long plays nothing at all, at exit 0 (`~/proofcut-work/spikes/sfx-probe`).
SOUND_MIN_FRAMES = 2


@dataclass(frozen=True)
class Hit:
    """A one-shot sound from timeline frame `start`, reading `frames` frames.

    `resource` is a padded copy whose length MLT counts as `frames` exactly
    (see `document`'s `sounds`); its leading silence carries the sub-frame
    part of where the sound starts.
    """

    resource: str
    start: int
    frames: int
    gain_db: float = 0.0

    @property
    def end(self) -> int:
        return self.start + self.frames


def sound_lanes(hits: list[Hit], total_frames: int, silence: str) -> list[list[Entry]]:
    """Pack hits onto as few lanes as keep any two from overlapping, padded.

    Greedy first-fit in time order — sound sums, so which lane a hit lands on
    changes nothing heard. Each lane is padded to `total_frames` with entries
    of `silence`, which must be at least that long. A hit running past the end
    is trimmed, never allowed to extend the film.
    """
    lanes: list[list[Hit]] = []
    for hit in sorted(hits, key=lambda h: (h.start, h.resource)):
        if hit.start < 0 or hit.start >= total_frames or hit.frames < 1:
            raise MLTError(
                f"the sound {hit.resource!r} at frame {hit.start} is outside the "
                f"{total_frames}-frame timeline"
            )
        for lane in lanes:
            if lane[-1].end <= hit.start:
                lane.append(hit)
                break
        else:
            lanes.append([hit])
    built: list[list[Entry]] = []
    for lane in lanes:
        entries: list[Entry] = []
        cursor = 0
        for hit in lane:
            if hit.start > cursor:
                entries.append(Entry(silence, 0, hit.start - cursor))
            frames = min(hit.frames, total_frames - hit.start)
            entries.append(Entry(hit.resource, 0, frames, gain_db=hit.gain_db))
            cursor = hit.start + frames
        if cursor < total_frames:
            entries.append(Entry(silence, 0, total_frames - cursor))
        built.append(entries)
    return built


@dataclass(frozen=True)
class Inset:
    """A clip drawn into a rectangle of the recording, following its camera.

    docs/plans/NATIVE.md § B6, designed. Render frames, like every position in
    this module: the inset plays `frames` frames of `resource` from `src_in`,
    starting at render frame `start`, over the Edit entry of `host` (the
    recording) that covers that whole span. `box` is `(x0, y0, x1, y1)` in the
    recording's own source pixels and `host_source` is the recording's size,
    which is what maps the camera's rects onto the box.

    **Its own track, never a nested tractor**: a tractor composites at the
    profile's size, so composite-then-frame scaled the render to 684px before
    the camera zoomed 1.97x and kept 38% of its detail
    (`~/proofcut-work/spikes/inset-probe`). The fade is `brightness` alpha on
    the chain and the dim a black track under it — never keys merged into the
    camera's `rect`, which would bend its curve.
    """

    resource: str
    src_in: int
    start: int
    frames: int
    box: tuple[int, int, int, int]
    host: str
    host_source: tuple[int, int]
    has_audio: bool = True
    gain_db: float = 0.0
    fade_in_frames: int = 0
    fade_in_ease: str = "linear"
    fade_out_frames: int = 0
    fade_out_ease: str = "linear"
    dim: float = 0.0

    @property
    def end(self) -> int:
        """The first render frame after the inset."""
        return self.start + self.frames


def inset_dest(
    dest: tuple[int, ...], box: tuple[int, int, int, int], source: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Where `box` lands when the whole source is drawn into `dest`.

    Linear in `dest`, which is the whole reason copying the camera's keys
    locks: two rects that interpolate on the same operator between the same
    positions stay in step (0.88px in the spike, against 1.52px for keys
    re-sampled every frame).
    """
    x, y, w, h = dest[:4]
    sx, sy = w / source[0], h / source[1]
    # Each edge rounded where it lands, so the far edge carries one rounding
    # rather than the near edge's plus the size's.
    left, top = round(x + box[0] * sx), round(y + box[1] * sy)
    right, bottom = round(x + box[2] * sx), round(y + box[3] * sy)
    return (left, top, right - left, bottom - top)


def _host_entry(inset: Inset, audio: list[Entry]) -> tuple[int, Entry]:
    """The Edit entry the inset sits over, and the render frame it starts at."""
    at = 0
    for entry in audio:
        if entry.resource == inset.host and at <= inset.start and inset.end <= at + entry.frames:
            return at, entry
        at += entry.frames
    raise MLTError(
        f"the inset {inset.resource!r} at frames {inset.start}..{inset.end} is not inside "
        f"one Edit entry of {inset.host!r} — an inset follows one continuous stretch of "
        "its recording, so a cut under it refuses"
    )


def inset_rect(
    inset: Inset,
    audio: list[Entry],
    reframe: Reframe | None,
    resolution: tuple[int, int],
    rate: float,
) -> str:
    """The inset playlist's `qtblend` `rect`: the camera's keys, mapped onto the box.

    **Keyed on the playlist, in render frames from 0.** A chain's keys count
    its own source frames, so a camera key from before the inset's in-point
    would need a negative position, which MLT reads as counting back from the
    end; the playlist's clock has no such key (spike round three: an inset
    starting mid-push locked at 0.63px). Each of the host entry's keys goes to
    the render frame it lands on — `S + position - src_in` on the source
    clock, `S + position` on a retimed chain, whose positions are already
    render frames — with its own operator.
    """
    at, entry = _host_entry(inset, audio)
    if reframe is None or reframe.is_identity(resolution):
        rows: list[_KeyRow] = [(0.0, None, fit_rect(inset.host_source, resolution), 1)]
    else:
        if reframe.panes or reframe.fills:
            raise MLTError(
                f"the inset {inset.resource!r} is over {inset.host!r}, which has a split or a "
                "blur-fill window — an inset maps one rect, and neither draws the recording "
                "as one"
            )
        rows = reframe.key_rows(resolution)
    if len(rows) == 1:
        return " ".join(str(v) for v in inset_dest(rows[0][2], inset.box, inset.host_source)) + " 1"
    place = placement(entry)
    shift = at - (0 if place is not None else entry.src_in)
    keys = [(position + shift, ease, dest, opacity) for position, ease, dest, opacity in _key_tuples(rows, rate, place)]
    early = [i for i, key in enumerate(keys) if key[0] <= 0]
    if early:
        # A slide that starts before the film: its value at frame 0, the way
        # `_format_keys` cuts a slide at a retimed entry's edge.
        lead = early[-1]
        position, ease, dest, opacity = keys[lead]
        if ease is not None and lead + 1 < len(keys) and keys[lead + 1][0] > position:
            dest = _between(dest, keys[lead + 1][2], ease, -position / (keys[lead + 1][0] - position))
        keys = [(0, ease, dest, opacity), *keys[lead + 1 :]]
    return ";".join(
        _key_string(position, ease, inset_dest(dest, inset.box, inset.host_source), opacity)
        for position, ease, dest, opacity in keys
    )


def rect_at(keys: str, frame: int) -> tuple[float, ...]:
    """A `rect` animation's value at `frame`, as MLT draws it (x, y, w, h, opacity).

    For reporting — `export`'s inset echo — on the operators this module
    writes: discrete, linear and the cubic family (`ease_fraction`).
    """
    names = {operator: name for name, operator in EASINGS.items()}
    parsed: list[tuple[int, str | None, tuple[float, ...]]] = []
    for key in keys.split(";"):
        if "=" not in key:
            return tuple(float(v) for v in key.split())
        head, _, body = key.partition("=")
        operator = head.lstrip("-0123456789")
        position = int(head[: len(head) - len(operator)])
        ease = None if operator == "|" else names.get(operator, "linear")
        parsed.append((position, ease, tuple(float(v) for v in body.split())))
    if frame <= parsed[0][0]:
        return parsed[0][2]
    for (p0, ease, v0), (p1, _, v1) in pairwise(parsed):
        if p0 <= frame < p1:
            if ease is None:
                return v0
            t = ease_fraction(ease, (frame - p0) / (p1 - p0))
            return tuple(round(a + (b - a) * t, 3) for a, b in zip(v0, v1))
    return parsed[-1][2]


@dataclass(frozen=True)
class Dissolve:
    """The incoming recording's last `frames` before its in-point, drawn over
    the join and fading in (docs/plans/RECUT.md step 8).

    **Two entries never overlap on the Edit's track**: the Edit butt-joins the
    outgoing clip to the incoming one, and the dissolve is the incoming clip's
    *pre-roll* — the frames just before its in-point — on its own silent track
    over the join's last `frames`, its alpha rising from 0 to reach 1 exactly
    at the join, where the Edit's own track takes over on the very next source
    frame. So every declared length stays the Edit's, and `declared_frames`
    needs nothing new. Render frames, like every position here; `src_in` is the
    pre-roll's first source frame. Its node carries the resource's reframe, so
    it is framed by its own camera, and never a retime: the pre-roll plays at
    1x whatever the join's stretch.

    `is_image` is the tail's card fading in over the film's last frames
    (`document`'s `tail_fade`): a still, so its `src_in` is 0 and it takes no
    reframe, the tail's own card entry taking over at the join.
    """

    resource: str
    src_in: int
    start: int
    frames: int
    ease: str = "linear"
    is_image: bool = False

    @property
    def end(self) -> int:
        """The join: the first render frame after the dissolve."""
        return self.start + self.frames


def _check_dissolve(dissolve: Dissolve, total_frames: int) -> None:
    where = f"the dissolve into {dissolve.resource!r} at frame {dissolve.end}"
    if dissolve.frames < 1 or dissolve.start < 0 or dissolve.end > total_frames:
        raise MLTError(
            f"{where} runs frames {dissolve.start}..{dissolve.end} of a "
            f"{total_frames}-frame timeline — a dissolve has to sit inside the film"
        )
    if dissolve.src_in < 0:
        raise MLTError(f"{where} would read {-dissolve.src_in} frames before its clip starts")
    if dissolve.ease not in EASINGS:
        raise MLTError(f"{where}: ease {dissolve.ease!r} is not one of {', '.join(EASINGS)}")


def _alpha_keys(inset: Inset, first: int, plateau: float) -> str:
    """A `brightness` alpha animation: 0 → plateau over the fade in, back to 0
    over the fade out, keyed from `first` — `overlay_rect`'s timing."""
    last = first + inset.frames - 1
    keys: list[str] = []
    if inset.fade_in_frames:
        keys.append(f"{first}{EASINGS[inset.fade_in_ease]}=0")
        keys.append(f"{first + inset.fade_in_frames}={plateau:g}")
    if inset.fade_out_frames:
        leave = last - inset.fade_out_frames
        key = f"{leave}{EASINGS[inset.fade_out_ease]}={plateau:g}"
        if keys and leave == first + inset.fade_in_frames:
            keys[-1] = key
        else:
            keys.append(key)
        keys.append(f"{last}=0")
    return ";".join(keys) or f"{plateau:g}"


def _check_inset(inset: Inset, total_frames: int) -> None:
    where = f"the inset {inset.resource!r} at frame {inset.start}"
    if inset.frames < 1 or inset.start < 0 or inset.end > total_frames:
        raise MLTError(
            f"{where} runs frames {inset.start}..{inset.end} of a "
            f"{total_frames}-frame timeline — an inset has to sit inside the film"
        )
    x0, y0, x1, y1 = inset.box
    if not (x1 > x0 and y1 > y0):
        raise MLTError(f"{where}: its box {inset.box} is empty")
    for frames, ease in ((inset.fade_in_frames, inset.fade_in_ease), (inset.fade_out_frames, inset.fade_out_ease)):
        if ease not in EASINGS:
            raise MLTError(f"{where}: no easing {ease!r} (there are: {', '.join(EASINGS)})")
        if frames < 0:
            raise MLTError(f"{where}: a negative fade")
    if inset.fade_in_frames + inset.fade_out_frames > inset.frames - 1:
        raise MLTError(
            f"{where} is {inset.frames} frames and its fades take "
            f"{inset.fade_in_frames + inset.fade_out_frames} — shorten the fades"
        )
    if not 0 <= inset.dim <= 1:
        raise MLTError(f"{where}: dim is a fraction from 0 to 1, not {inset.dim}")


def fit_rect(source: tuple[int, int], resolution: tuple[int, int]) -> tuple[int, int, int, int]:
    """Where MLT puts a source frame when nothing tells it otherwise.

    Contain, never stretch: the source is scaled by the *smaller* of the two
    ratios and centred, so a 1920x816 source in a 1080x1920 profile occupies
    459 of 1920 rows and the other 76% is black bar. Measured on this box
    against the real footage before any of this was built — PLAN.md § Aspect
    swap, finding 2 — and written down here because it is the thing a reframe
    is defined *against*: a filter that reproduces this rect changes nothing
    and should not be emitted at all.
    """
    src_w, src_h = source
    width, height = resolution
    scale = min(width / src_w, height / src_h)
    dest_w = round(src_w * scale)
    dest_h = round(src_h * scale)
    return (round((width - dest_w) / 2), round((height - dest_h) / 2), dest_w, dest_h)


def centre_crop(source: tuple[int, int], resolution: tuple[int, int]) -> tuple[int, int, int, int]:
    """The largest rect of the canvas's aspect that fits inside the source.

    The default reframe, and the one PLAN.md § Aspect swap insists is
    *reported* rather than assumed: a centre crop is wrong whenever the
    subject is not centred, which in this footage is often, so the caller is
    told which rect it got and can name another.

    Integer pixels, so the centring is exact only when the leftover is even —
    1920x816 into 9:16 crops to 459 wide with 1461 to share, and this returns
    x=730 where the true centre is 730.5. The probe in finding 3 rendered the
    half-pixel version and read x back one pixel further left; a rect that
    cannot be typed is worse than a pixel, so the integer rect is what the
    override format and the default both speak.
    """
    src_w, src_h = source
    width, height = resolution
    if src_w * height >= src_h * width:
        crop_w, crop_h = round(src_h * width / height), src_h
    else:
        crop_w, crop_h = src_w, round(src_w * height / width)
    return ((src_w - crop_w) // 2, (src_h - crop_h) // 2, crop_w, crop_h)


def pane_boxes(resolution: tuple[int, int]) -> tuple[tuple[int, int, int, int], ...]:
    """Where the two panes of a stacked split sit inside the frame.

    Full width, half height each, the remainder going to the lower pane so
    the two always tile the canvas exactly — an odd height would otherwise
    leave a one-pixel seam of background showing between them.

    **Stacked rather than side by side, and that is not a preference.** The
    canvas this exists for is 9:16; two panes beside each other would be
    540x1920 apiece, taller than they are wide by nearly 4:1, and a face in
    one is a sliver. Stacked they are 1080x960 — wider than the 459px window
    a single crop of this footage gets, which is the whole gain.
    """
    width, height = resolution
    half = height // 2
    return ((0, 0, width, half), (0, half, width, height - half))


def pane_overlap(rect: tuple[int, int, int, int], pane: tuple[int, int, int, int]) -> float:
    """How much of the narrower pane the two panes share, 0.0 to 1.0.

    **The number a stacked split is judged on**, and until now the one nobody
    reported. Nothing masks a pane, so two crops that overlap are showing the
    same strip of source twice — once in each half — and a viewer reads that as
    a duplicated face rather than as two subjects. Where the line sits was
    measured on the film rather than chosen: its splits separate at 23–24%,
    where the halves hold distinct groups, against 52–63%, where the same face
    is in both (HISTORY.md § The thirty-nine windows, reviewed).

    It is reported and never enforced, for `reframe_detect`'s standing reason:
    the pass proposes and `reframe_sheet` disposes, and a duplicating split is
    sometimes the least bad answer for a shot one window cannot hold. What was
    wrong was making a reviewer compute it by hand every time the tool offered
    one.

    Horizontal only, because a pane is full source height by construction —
    growing a crop shorter than the source is what scales one pane into the
    other, which is a different failure and `_fit_pane_rect`'s job.
    """
    lower, upper = sorted((rect, pane), key=lambda box: box[0])
    shared = max(0, (lower[0] + lower[2]) - upper[0])
    narrower = min(rect[2], pane[2])
    return round(shared / narrower, 3) if narrower else 0.0


#: One reframe key before it is written: the source second its window starts,
#: the easing of the segment leaving it (None steps), the rect and the opacity.
_KeyRow = tuple[float, "str | None", tuple[int, int, int, int], int]


@dataclass(frozen=True)
class Placement:
    """A retimed entry's clock: where each source second falls on its chain.

    `time_map` is the entry's own `(position, source seconds)` keys and
    `frames` its length. A key before the entry starts collapses onto
    position 0, and one after it ends is not written, because a remapped
    chain's filter counts the chain's own positions (spike finding B).
    """

    time_map: tuple[tuple[int, float], ...]
    frames: int

    def position(self, seconds: float, rate: float) -> float:
        keys = self.time_map
        if seconds <= keys[0][1]:
            return keys[0][0] - (keys[0][1] - seconds) * rate
        if seconds >= keys[-1][1]:
            return keys[-1][0] + (seconds - keys[-1][1]) * rate + 1e-6
        for (p0, s0), (p1, s1) in pairwise(keys):
            if s0 <= seconds <= s1:
                return p0 if s1 <= s0 else p0 + (p1 - p0) * (seconds - s0) / (s1 - s0)
        return float(keys[-1][0])


def _key_string(position: int, ease: str | None, dest: tuple[int, ...], opacity: int) -> str:
    operator = "|=" if ease is None else f"{EASINGS[ease]}="
    return f"{position}{operator}{' '.join(str(value) for value in dest)} {opacity}"


def _between(
    a: tuple[int, ...], b: tuple[int, ...], ease: str, fraction: float
) -> tuple[int, ...]:
    t = ease_fraction(ease, fraction)
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _format_keys(rows: list[_KeyRow], rate: float, place: Placement | None) -> str:
    """The rows as MLT keys: at source frames, or on a retimed chain's clock."""
    return ";".join(_key_string(*key) for key in _key_tuples(rows, rate, place))


def _key_tuples(
    rows: list[_KeyRow], rate: float, place: Placement | None
) -> list[tuple[int, str | None, tuple[int, ...], int]]:
    """The rows at the positions `_format_keys` writes them, unjoined.

    On a chain, a slide the entry starts or ends inside is cut at the entry's
    edge at the value the curve has reached there, so the first and last
    frames draw what the source-clock document would. An inset copies these
    tuples rather than the string (`inset_rect`).
    """
    if place is None:
        return [(round(seconds * rate), *rest) for seconds, *rest in rows]
    at = [place.position(seconds, rate) for seconds, *_ in rows]
    lead = max(i for i in range(len(rows)) if at[i] <= 0)
    keys: list[tuple[int, str | None, tuple[int, ...], int]] = []
    _seconds, ease, dest, opacity = rows[lead]
    if ease is not None and lead + 1 < len(rows) and at[lead + 1] > at[lead]:
        dest = _between(dest, rows[lead + 1][2], ease, -at[lead] / (at[lead + 1] - at[lead]))
    keys.append((0, ease, dest, opacity))
    last = lead
    for i in range(lead + 1, len(rows)):
        if at[i] >= place.frames:
            break
        position = round(at[i])
        if position <= keys[-1][0]:
            keys[-1] = (keys[-1][0], rows[i][1], rows[i][2], rows[i][3])
        else:
            keys.append((position, rows[i][1], rows[i][2], rows[i][3]))
        last = i
    ease = rows[last][1]
    end = place.frames - 1
    if ease is not None and last + 1 < len(rows) and keys[-1][0] < end:
        span = at[last + 1] - at[last]
        fraction = (end - at[last]) / span if span > 0 else 1.0
        keys.append((end, None, _between(rows[last][2], rows[last + 1][2], ease, fraction), rows[last][3]))
    return keys


def placement(entry: Entry) -> Placement | None:
    """A retimed entry's clock for its filters, or None on the source clock."""
    return Placement(entry.time_map, entry.frames) if entry.time_map else None


@dataclass(frozen=True)
class Reframe:
    """A source's size, and the rects of it that survive into the frame.

    **Geometry in source pixels, never a length** — the same rule a footage
    description follows (CLAUDE.md), and for the same reason: an edit cannot
    invalidate a rect, so no cut has to re-derive one. `source` rides along
    because the placement needs it — MLT is told where the *whole* frame goes,
    and the crop is expressed by letting the rest overflow the profile.

    `crop` is the window from the head of the file onward and `later` holds
    the rest, each `(src_start seconds, rect)` and in force from that point in
    the **source** onward. That address is what makes framing per *shot*
    rather than per clip (PLAN.md § Per-shot framing, finding 1: a cue cannot
    carry it, because cues and camera cuts are unrelated clocks). Everything
    the source address buys falls out rather than being engineered: a clip
    used seven times picks up whichever windows each placement happens to read
    over, no cut can invalidate one, and none of them is a length.

    A per-clip reframe is the degenerate one-window case, and still writes the
    same single `rect` string it always did.

    `panes` is the **second** rect of a stacked split, addressed by the same
    source in-point as the window it belongs to: a window carrying one is
    drawn as two half-height panes, this series holding the lower one and the
    ordinary window series the upper. Empty on every project that has no
    split, which is what keeps their documents byte-identical.

    `interp` names which `later` windows **slide in** from whatever governed
    before them, instead of stepping to it (PLAN.md § Per-shot framing,
    refused section; built out as § The keyframed move). It is a set of the
    same `src_start` addresses `later` uses, not a parallel series, because a
    window either slides or it does not — there is no third rect to carry.
    Empty means every window steps, which is what every window before this
    existed meant and what keeps an unflagged project's document unchanged.

    `fills` names the windows drawn **blur-filled** (PLAN.md § Blur-fill):
    the whole source contained in the frame over a blurred, darkened copy of
    the same moment scaled to cover it. Addressed by window start like
    `interp`. A fill window's own crop is the whole source, and nothing
    reads it as a crop — `_window_dest` answers `fit_rect` for it. The
    background is a second node (`fill_rect_property`), switched by opacity
    the way a pane is. Empty on every project with no fill, which keeps
    their documents byte-identical.
    The mechanism was already paid for by the writer (`rect_property` below):
    every key already carried its own operator, discrete `|=` or
    interpolated `=`, this class just never wrote anything but `|=`. **Which
    key** is the one thing that was not obvious and was measured rather than
    assumed: MLT interpolates the segment *leaving* a keyframe, so a window
    asking to slide in puts `=` on the key *before* it, not its own
    (`rect_property`'s own docstring has the render that settled it). **The
    head can never be in `interp`** — there is nothing before frame 0 in the
    source to slide from — and `__post_init__` refuses one that claims to,
    the same discipline the "after the head" check above already applies to
    `later` itself.
    """

    source: tuple[int, int]
    crop: tuple[int, int, int, int]
    later: tuple[tuple[float, tuple[int, int, int, int]], ...] = ()
    panes: tuple[tuple[float, tuple[int, int, int, int]], ...] = ()
    interp: tuple[float, ...] = ()
    fills: tuple[float, ...] = ()
    eases: tuple[tuple[float, str], ...] = ()

    def __post_init__(self) -> None:
        at = [seconds for seconds, _ in self.later]
        if any(seconds <= 0 for seconds in at):
            raise MLTError(
                "a later reframe window starts after the head of the source — "
                "the window from 0 onward is `crop`"
            )
        if at != sorted(set(at)):
            raise MLTError(f"reframe windows must be in source order and distinct, not {at}")
        pane_at = [seconds for seconds, _ in self.panes]
        if pane_at != sorted(set(pane_at)):
            raise MLTError(f"split panes must be in source order and distinct, not {pane_at}")
        # A pane is the *other half* of a window, never a window of its own —
        # one without a partner would render as half a frame over whatever the
        # governing window happens to be, which is a picture nobody asked for.
        starts = {seconds for seconds, _ in self.windows()}
        orphans = [seconds for seconds in pane_at if seconds not in starts]
        if orphans:
            raise MLTError(
                f"a split pane at {orphans} has no window of its own to pair with — "
                "a pane is the lower half of a window, so both halves are addressed "
                "by the same source in-point"
            )
        # `interp` names a `later` window, never the head — refused here
        # rather than left to render wrong, since a bad key would still write
        # a document and exit 0.
        stray = sorted(set(self.interp) - set(at))
        if stray:
            raise MLTError(
                f"reframe cannot flag {stray} to slide — interp names a window "
                "in `later`, and the head has nothing before it to slide from"
            )
        # An easing is the curve of a slide, so it names a sliding window.
        unslid = sorted(seconds for seconds, _ in self.eases if not self.is_interp(seconds))
        if unslid:
            raise MLTError(f"window {unslid} has an easing but does not slide")
        unknown = sorted({name for _, name in self.eases} - set(EASINGS))
        if unknown:
            raise MLTError(
                f"easing {unknown} is not one this build writes — {', '.join(EASINGS)}"
            )
        # A pane track has no interpolation of its own (`pane_rect_property`
        # always writes `|=`), so a window that is both a split and a slide
        # would move on top and step underneath — two framings disagreeing in
        # the same frame, at exit 0. Refused rather than shipped half-built.
        fill_at = list(self.fills)
        if fill_at != sorted(set(fill_at)):
            raise MLTError(f"fill windows must be in source order and distinct, not {fill_at}")
        loose = [seconds for seconds in fill_at if seconds not in starts]
        if loose:
            raise MLTError(
                f"a fill at {loose} names no window — a fill is a mode of the window "
                "starting at that source in-point"
            )
        # A fill has no crop to split, and its background is keyed by opacity
        # steps, so a slide into or out of one would move the picture while
        # the blur behind it snaps.
        split_fill = sorted(set(fill_at) & set(pane_at))
        if split_fill:
            raise MLTError(f"window {split_fill} cannot both split and blur-fill")
        windows = self.windows()
        sliding = [
            windows[i][0]
            for i in range(1, len(windows))
            if self.is_interp(windows[i][0])
            and (self.is_fill(windows[i][0]) or self.is_fill(windows[i - 1][0]))
        ]
        if sliding:
            raise MLTError(
                f"window {sliding} cannot slide into or out of a blur-fill — the "
                "background steps at the join while the picture would travel"
            )
        both = sorted(set(self.interp) & set(pane_at))
        if both:
            raise MLTError(
                f"window {both} cannot both slide and split — its lower pane "
                "would still step while the upper half moves, drawing two "
                "different framings across the same join"
            )

    def windows(self) -> tuple[tuple[float, tuple[int, int, int, int]], ...]:
        """Every window in source order, the head one included."""
        return ((0.0, self.crop), *self.later)

    def pane_at(self, seconds: float) -> tuple[int, int, int, int] | None:
        """The lower pane of the window starting exactly here, if it is a split.

        Keyed on the window's own start rather than "in force from here",
        because that is what a pane is: the other half of one window. Asking
        which pane covers an arbitrary moment is `crop_at`'s question, and the
        answer for the lower half is found by looking up that window's start.
        """
        for start, rect in self.panes:
            if abs(start - seconds) < 1e-9:
                return rect
        return None

    def window_start(self, seconds: float) -> float:
        """Where the window in force at this point in the source begins."""
        start = 0.0
        for at, _ in self.later:
            if seconds + 1e-9 < at:
                break
            start = at
        return start

    def is_split(self, seconds: float) -> bool:
        """Is the window in force at this point in the source a stacked split?"""
        return self.pane_at(self.window_start(seconds)) is not None

    def is_interp(self, seconds: float) -> bool:
        """Does the window starting here slide in from whatever came before it?

        Keyed on the window's own start, the same address `pane_at` uses, and
        that address is a fact about *this* window regardless of which MLT
        key ends up carrying the `=` — the answer to "should the source be
        moving throughout the previous stretch" (`reframe_sheet`'s question)
        is the same either way. The default — and every window written before
        this existed — is False, a discrete step. `rect_property` is the one
        place the direction matters: it puts the operator this implies on the
        *preceding* key, because MLT interpolates the segment leaving a
        keyframe, not the one arriving at it (measured, not assumed — see
        `rect_property`'s own docstring).
        """
        return any(abs(seconds - at) < 1e-9 for at in self.interp)

    def ease_at(self, seconds: float) -> str | None:
        """The curve the window starting here slides in on, or None if it steps.

        `linear` for a slide with no easing named, which is what every slide
        written before easings existed meant.
        """
        if not self.is_interp(seconds):
            return None
        for at, name in self.eases:
            if abs(seconds - at) < 1e-9:
                return name
        return "linear"

    def is_fill(self, seconds: float) -> bool:
        """Is the window starting exactly here drawn blur-filled?"""
        return any(abs(seconds - at) < 1e-9 for at in self.fills)

    def is_fill_at(self, seconds: float) -> bool:
        """Is the window in force at this point in the source blur-filled?"""
        return self.is_fill(self.window_start(seconds))

    def _window_dest(
        self,
        start: float,
        crop: tuple[int, int, int, int],
        resolution: tuple[int, int],
        box: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, int, int]:
        """Where the source lands for the window starting at `start`: contained
        for a fill window, `_dest`'s crop-to-fill for every other."""
        if self.is_fill(start):
            return fit_rect(self.source, resolution)
        return self._dest(crop, resolution, box)

    def cover_rect(self, resolution: tuple[int, int]) -> tuple[int, int, int, int]:
        """Where a fill window's background lands: the whole source, scaled by
        the larger ratio so it covers the frame, centred. The profile clips it."""
        src_w, src_h = self.source
        return self._dest((0, 0, src_w, src_h), resolution)

    def fill_dest_at(
        self, seconds: float, resolution: tuple[int, int]
    ) -> tuple[int, int, int, int] | None:
        """The background's rect at this point in the source, or None where the
        window in force is not a fill — the preview's half of what the second
        node draws."""
        return self.cover_rect(resolution) if self.is_fill_at(seconds) else None

    def crop_at(self, seconds: float) -> tuple[int, int, int, int]:
        """The window in force at that point in the source."""
        found = self.crop
        for start, rect in self.later:
            if seconds + 1e-9 < start:
                break
            found = rect
        return found

    def _dest(
        self,
        crop: tuple[int, int, int, int],
        resolution: tuple[int, int],
        box: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, int, int]:
        """Where the whole source frame lands, so that `crop` fills `box`.

        `qtblend`'s rect is a *destination* in profile pixels, not a crop —
        which is why this returns something much larger than the profile and
        with a negative origin. Scale is `max` of the two ratios (fill), and
        the crop's centre is put on the box's centre; when the crop already
        carries the box's aspect the two ratios are equal and nothing is
        lost off the second axis.

        `box` is the whole frame for an ordinary window and one half of it for
        a pane of a stacked split. The profile does the clipping either way —
        nothing is masked and no crop filter is involved — which is why a pane
        needs a crop of *exactly* the pane's aspect to stay inside it. That is
        `ops` refitting each pane against `pane_boxes`, and it is the one thing
        holding the two panes apart. Measured rather than assumed: a pane
        window spans the full source height by construction, so the scaled
        frame is exactly the pane's height and cannot reach the other half.
        """
        src_w, src_h = self.source
        crop_x, crop_y, crop_w, crop_h = crop
        box_x, box_y, box_w, box_h = box if box is not None else (0, 0, *resolution)
        scale = max(box_w / crop_w, box_h / crop_h)
        return (
            round(box_x + box_w / 2 - (crop_x + crop_w / 2) * scale),
            round(box_y + box_h / 2 - (crop_y + crop_h / 2) * scale),
            round(src_w * scale),
            round(src_h * scale),
        )

    def dest_rect(self, resolution: tuple[int, int]) -> tuple[int, int, int, int]:
        """Where the whole source frame lands for the head window."""
        return self.dest_rect_at(0.0, resolution)

    def dest_rect_at(self, seconds: float, resolution: tuple[int, int]) -> tuple[int, int, int, int]:
        """The same, for whichever window that point in the source reads.

        A split window answers with its **upper** pane, because that is what
        this reframe's own node draws there — a preview taking the whole-canvas
        rect instead would place the shot at more than twice the render's
        scale and show one person where the film shows two.
        """
        upper, _lower = pane_boxes(resolution)
        box = upper if self.is_split(seconds) else None
        return self._window_dest(self.window_start(seconds), self.crop_at(seconds), resolution, box)

    def pane_dest_at(
        self, seconds: float, resolution: tuple[int, int]
    ) -> tuple[int, int, int, int] | None:
        """Where the *lower* pane's source frame lands, or None if not a split.

        The second half of what `dest_rect_at` answers, and the two together
        are the whole of what the render draws — which is what a preview has to
        have to draw the same picture rather than half of it.
        """
        start = self.window_start(seconds)
        pane = self.pane_at(start)
        if pane is None:
            return None
        _upper, lower = pane_boxes(resolution)
        return self._dest(pane, resolution, lower)

    def is_identity(self, resolution: tuple[int, int]) -> bool:
        """Would this filter tell MLT anything it was not already doing?

        A source already at the canvas's aspect, uncropped, lands on exactly
        `fit_rect`. Emitting a filter for that case would change every
        existing document to no effect, so the writer skips it — which is what
        keeps a project with no canvas override byte-identical to the one it
        exported before any of this existed. **Every** window has to be that
        rect: one window that moves is a filter worth emitting.

        A split is never identity whatever its rects say — half the frame is
        being handed to a second node, which is not something MLT was already
        doing.
        """
        if self.panes or self.fills:
            return False
        fitted = fit_rect(self.source, resolution)
        return all(self._dest(crop, resolution) == fitted for _, crop in self.windows())

    def rect_property(
        self, resolution: tuple[int, int], rate: float | None = None, place: Placement | None = None
    ) -> str:
        """The `rect` value: `x y w h opacity`, or MLT's animation of them.

        One window writes the bare string it always wrote. More than one
        writes keyframes — numbered in the producer's own **source** frames,
        which is the clock MLT runs a filter's animation on. That was
        measured rather than assumed, and refuted from both directions: a
        step keyed at source frame 310 on a producer read from 300 lands at
        output frame 10, and one keyed at 20 is already up at output frame 0
        (PLAN.md § Per-shot framing, finding 3). A timeline clock would have
        shown the opposite of both.

        **Each key's own operator is discrete (`|=`) unless the window that
        *follows* it is flagged `interp`**, in which case it is `=` — a
        framing window steps at a camera cut by default and does not slide
        into the next one, but a window named in `interp` is asking its
        predecessor to. **MLT interpolates the segment *leaving* a keyframe,
        not the one arriving at it** — measured directly (`~/proofcut-work/projects/kf-probe`,
        two renders differing only in which of a pair's two keys carried `=`):
        flagging the later key produced a hold at the earlier rect for the
        entire span and a hard cut to the later one exactly at its own frame,
        indistinguishable from `|=`; flagging the *earlier* key produced a
        render that visibly travelled between the two, crossing over roughly
        midway. So a window asking to slide in puts its flag on the key
        *before* it, not its own — which is also why the head can never
        satisfy `interp` (`__post_init__`): there being nothing before it to
        flag is the same fact as there being nothing before it to slide from.
        The mechanism is the same either way — every key already had its own
        operator, `interp` just decides which one gets `=` (PLAN.md § Per-shot
        framing, refused section; § The keyframed move).

        A window that is a split writes the *upper* pane here — the same rect
        against a half-height box — so this node keeps drawing the whole way
        through and only its destination changes. The lower pane is a second
        node, `pane_rect_property`, which has no `interp` of its own
        (`__post_init__` refuses a window that is both).

        `place` is a retimed entry's clock (`placement`): its chain counts
        render frames, so the keys go where the warp shows each window
        (docs/plans/NATIVE.md § B5, designed).
        """
        if not self.later and not self.panes:
            return " ".join(str(value) for value in self.dest_rect(resolution)) + " 1"
        if not rate:
            raise MLTError(
                "a reframe with more than one window needs the frame rate — its "
                "keyframes are numbered in the source's own frames"
            )
        return _format_keys(self.key_rows(resolution), rate, place)

    def key_rows(self, resolution: tuple[int, int]) -> list[_KeyRow]:
        """`rect_property`'s rows before they are placed: one per window, in
        source seconds, with the operator of the segment leaving each.

        The inset lane reads these too (`inset_rect`), which is why they are a
        method: the inset copies the camera's keys and never re-derives them.
        """
        upper, _ = pane_boxes(resolution)
        windows = self.windows()
        rows: list[_KeyRow] = []
        for index, (seconds, crop) in enumerate(windows):
            box = upper if self.pane_at(seconds) is not None else None
            dest = self._window_dest(seconds, crop, resolution, box)
            # This key's operator governs the segment *leaving* it, so it is
            # the *next* window's flag that decides — not this one's.
            next_start = windows[index + 1][0] if index + 1 < len(windows) else None
            ease = self.ease_at(next_start) if next_start is not None else None
            if ease is not None and dest[2:] == tuple(self.source):
                dest = _off_unity(dest)
            rows.append((seconds, ease, dest, 1))
        return rows

    def pane_rect_property(
        self, resolution: tuple[int, int], rate: float, place: Placement | None = None
    ) -> str:
        """The lower pane's own `rect`, on its own node — off where there is no split.

        **The pane is hidden by opacity, never by moving it off-canvas.** Both
        render byte-identical frames (the probe behind PLAN.md § The stacked
        split rendered the pair), and opacity is the one to write because a
        rect parked at -9999 reads as a bug to whoever opens the document next
        and invites being "fixed" into view.

        Keyed at every window boundary rather than only at the splits: a step
        that is not written is a value that carries on, so a pane left at
        opacity 1 past the end of its split would draw the following shot's
        footage into the bottom half of the frame. `melt` would exit 0.
        """
        if not self.panes:
            raise MLTError("this reframe has no split panes, so there is no second node")
        if not rate:
            raise MLTError(
                "a split pane needs the frame rate — its keyframes are numbered "
                "in the source's own frames"
            )
        _, lower = pane_boxes(resolution)
        parked = fit_rect(self.source, resolution)
        rows: list[_KeyRow] = []
        for seconds, _crop in self.windows():
            pane = self.pane_at(seconds)
            if pane is None:
                rows.append((seconds, None, parked, 0))
                continue
            rows.append((seconds, None, self._dest(pane, resolution, lower), 1))
        return _format_keys(rows, rate, place)


    def fill_rect_property(
        self, resolution: tuple[int, int], rate: float | None = None, place: Placement | None = None
    ) -> str:
        """The fill background's own `rect`: cover where a window is a fill, off elsewhere.

        `pane_rect_property`'s rule, for the same reason: keyed at every window
        boundary, because a step not written is a value that carries on, and a
        background left at opacity 1 past its fill would draw a blurred copy
        behind the next shot's crop — invisible there, until the shot after
        is contained too. One window writes the bare string.
        """
        if not self.fills:
            raise MLTError("this reframe has no fill windows, so there is no background node")
        cover = " ".join(str(value) for value in self.cover_rect(resolution))
        if not self.later:
            return f"{cover} 1"
        if not rate:
            raise MLTError(
                "a fill background over more than one window needs the frame rate — "
                "its keyframes are numbered in the source's own frames"
            )
        rows: list[_KeyRow] = []
        for seconds, _crop in self.windows():
            if self.is_fill(seconds):
                rows.append((seconds, None, self.cover_rect(resolution), 1))
            else:
                rows.append((seconds, None, fit_rect(self.source, resolution), 0))
        return _format_keys(rows, rate, place)


def plan_picture(shots: list[dict[str, Any]], rate: float) -> list[Entry]:
    """Decide what each shot actually shows, and from where in its asset.

    `build_shots` (step 2) says when each shot starts and how long it runs and
    deliberately stops there — where inside the asset to read is a question
    about the XML, so it is answered here. A clip used three times shows three
    different stretches of itself: the cursor per asset carries on from where
    the previous shot left it, the way `assemble_scream.py` did by hand,
    because replaying the same opening seconds under every third beat is the
    thing that reads as stock footage.

    A cursor that would run past the end of its asset rewinds to the start
    rather than clamping — clamping would hold a frozen frame, which looks
    like a render bug rather than a re-use. A single shot longer than its
    whole asset cannot be solved by rewinding and refuses instead.

    **A pinned shot has no cursor and never rewinds** — it refuses (PLAN.md
    § B-roll by description). `src_pin` is source seconds, put there by a cue
    carrying `src_start`: somebody read a description and asked for *that*
    moment. Rewinding a pin to 0 would show footage the search did not find —
    correct pixels, wrong video, and nothing on screen saying so — which is
    the one failure a b-roll placement can make silently. The rewind above
    stays right for an unpinned re-use, where the only claim being made is
    "some of this clip".

    A pin still advances the cursor, so an unpinned re-use after one carries
    on from where the pinned stretch ended rather than replaying it.

    Stills have no cursor: a card is one frame held for the shot's length. A
    pin on one refuses rather than being ignored — `cue_add` turns it away
    first, and a pin that reached here anyway would be a silent no-op.
    """
    cursors: dict[str, int] = {}
    entries: list[Entry] = []
    for shot in shots:
        resource = str(shot["asset_path"])
        frames = int(shot["frames"])
        pin = shot.get("src_pin")
        if frames < 1:
            raise MLTError(
                f"shot for {shot['asset']!r} at frame {shot['start_frame']} is "
                f"{frames} frames long — a cue landed on top of the next one"
            )

        if shot["is_image"]:
            if pin is not None:
                raise MLTError(
                    f"the cue at {shot['clip_id']!r} word {shot['word_index']} pins "
                    f"{shot['asset']!r} to {float(pin):.1f}s, but it is a still — a "
                    "held frame has no playhead to move; drop the in-point"
                )
            entries.append(Entry(resource, 0, frames, is_image=True, has_video=True))
            continue

        duration = shot.get("asset_duration")
        if not duration:
            raise MLTError(
                f"asset {shot['asset']!r} has no known duration, so there is no "
                "way to tell whether the shot fits inside it"
            )
        available = round(float(duration) * rate)
        if frames > available:
            raise MLTError(
                f"the shot at {shot['clip_id']!r} word {shot['word_index']} runs "
                f"{frames / rate:.1f}s but {shot['asset']!r} is only "
                f"{available / rate:.1f}s long — split the shot with another cue, "
                "or point it at longer material"
            )
        if pin is None:
            cursor = cursors.get(resource, 0)
            if cursor + frames > available:
                cursor = 0
        else:
            cursor = round(float(pin) * rate)
            if cursor < 0:
                raise MLTError(
                    f"the cue at {shot['clip_id']!r} word {shot['word_index']} pins "
                    f"{shot['asset']!r} to {float(pin):.1f}s, which is before the "
                    "start of the asset"
                )
            if cursor + frames > available:
                raise MLTError(
                    f"the cue at {shot['clip_id']!r} word {shot['word_index']} pins "
                    f"{shot['asset']!r} to {cursor / rate:.1f}s and the shot runs "
                    f"{frames / rate:.1f}s, which ends past the asset's "
                    f"{available / rate:.1f}s — a pinned cue shows the moment it "
                    "names or nothing, so move the in-point earlier or shorten the "
                    "shot with another cue"
                )
        entries.append(Entry(resource, cursor, frames, has_video=True))
        cursors[resource] = cursor + frames
    return entries


def _property(parent: ET.Element, name: str, value: str) -> ET.Element:
    node = ET.SubElement(parent, "property", {"name": name})
    node.text = value
    return node


def _frame_rate(rate: float) -> tuple[int, int]:
    """`rate` as MLT's numerator/denominator pair.

    Exact for the integer rates and for the 1001-denominator ones (29.97 is
    30000/1001, and writing 29.97 flat is a frame of drift every 100 seconds).
    """
    if float(rate).is_integer():
        return int(rate), 1
    fraction = Fraction(rate).limit_denominator(1001)
    return fraction.numerator, fraction.denominator


def _profile(rate: float, resolution: tuple[int, int]) -> ET.Element:
    width, height = resolution
    num, den = _frame_rate(rate)
    divisor = gcd(width, height) or 1
    return ET.Element(
        "profile",
        {
            "description": "proofcut",
            "width": str(width),
            "height": str(height),
            "progressive": "1",
            "sample_aspect_num": "1",
            "sample_aspect_den": "1",
            "display_aspect_num": str(width // divisor),
            "display_aspect_den": str(height // divisor),
            "frame_rate_num": str(num),
            "frame_rate_den": str(den),
            "colorspace": "709",
        },
    )


def _source_node(node_id: str, entry: Entry, bin_id: int, rate: float) -> ET.Element:
    """The producer a playlist entry reads from.

    Three shapes, and the differences are the whole reason distinct sources
    cannot share one node: a still is a `qimage` producer, picture is a chain
    with its audio switched off, and the edit's own track is a chain with its
    audio on.
    """
    if entry.is_image:
        node = ET.Element("producer", {"id": node_id})
        properties = {
            "resource": entry.resource,
            "mlt_service": "qimage",
            "length": str(round(IMAGE_LENGTH_SECONDS * rate)),
            "eof": "continue",
            "ttl": "1",
        }
    else:
        node = ET.Element("chain", {"id": node_id})
        properties = {
            "resource": entry.resource,
            "mlt_service": "avformat-novalidate",
            "vstream": "0",
            "astream": "0",
        }
    for name, value in properties.items():
        _property(node, name, value)
    _property(node, "kdenlive:id", str(bin_id))
    if entry.time_map and not entry.is_image:
        # Tagged, not attached: callers add properties after this, and the
        # link goes after the last of them (`_attach_links`).
        node.set("_time_map", ";".join(f"{position}={seconds:g}" for position, seconds in entry.time_map))
    return node


def _attach_links(root: ET.Element) -> None:
    """Give every retimed chain its `timeremap` link, after its properties.

    Never a `length` on such a chain: it pins the link to source frame 0 for
    every output frame, at exit 0 (`~/proofcut-work/spikes/mlt-retime`).
    Linear keys only, never `~`, which ran a launch-clip map backwards
    (`~/proofcut-work/spikes/retime-compose`).
    """
    for node in root.findall("chain"):
        time_map = node.attrib.pop("_time_map", None)
        if time_map is None:
            continue
        if node.find("property[@name='length']") is not None:
            raise MLTError(f"retimed chain {node.get('id')} has a length, which freezes its link")
        children = list(node)
        at = max(i for i, child in enumerate(children) if child.tag == "property") + 1
        link = ET.Element("link", {"mlt_service": "timeremap"})
        _property(link, "time_map", time_map)
        node.insert(at, link)


#: Where a fade starts and ends, in dB. Not silence — but the render's own
#: edge sample sits 60 dB under the bed's level, which is under any noise
#: floor this pipeline meets, and the dB ramp is the perceptually even fade.
#: Both facts measured, not recalled: `level`'s keyframe VALUES are dB
#: (gain-factor keys 0..1 rendered as a 1 dB wiggle at exit 0 — the silent
#: wrong answer), its POSITIONS are relative to the *producer*, not the
#: timeline entry (the original probe used a lead entry ahead of the faded
#: one on the timeline but never a nonzero `src_in` on the faded entry
#: itself, so "relative to the entry" and "relative to the producer" were
#: indistinguishable until holds' own entries — which always read from deep
#: inside their source — measured the difference: keyframes written
#: 0-based on a `src_in=268` entry rendered *silent throughout*, because by
#: the time playback reaches producer frame 268 the animation is long past
#: its last defined key. Offsetting every position by `entry.src_in` is
#: what makes it land on the frames the entry actually plays. `level=0` is
#: exactly unity (plateau at the no-filter control's own -33.12 dBFS).
#: `~/proofcut-work/spikes/a2-probe/fade_probe.py` (src_in=0 only), the holds-lane readback
#: that found the src_in gap, HISTORY.md § The A2 fades.
FADE_FLOOR_DB = -60


#: Keyframes along each half of a crossfade — MLT interpolates linearly in dB
#: between them, so enough points make the equal-power curve hold within a
#: fraction of a dB where two straight dB ramps would leave a hole.
CROSSFADE_STEPS = 8


def _power_ramp(start: int, frames: int, plateau: float, *, rising: bool) -> list[tuple[int, float]]:
    """One side of an equal-power crossfade: gain sin(πt/2) rising, cos(πt/2)
    falling, in dB against `plateau`, floored at `FADE_FLOOR_DB` — so the two
    sides' powers sum to the plateau's across the overlap."""
    keys: list[tuple[int, float]] = []
    for step in range(CROSSFADE_STEPS + 1):
        t = step / CROSSFADE_STEPS
        gain = math.sin(t * math.pi / 2) if rising else math.cos(t * math.pi / 2)
        level = max(FADE_FLOOR_DB, plateau + 20 * math.log10(gain)) if gain > 0 else FADE_FLOOR_DB
        keys.append((start + round(frames * t), round(level, 2)))
    return keys


def _line_at(keys: list[tuple[int, float]], frame: int) -> float:
    """The value of a keyframed line at `frame`, held flat past either end.
    Where two keys share a frame (a short fade rounds that way) the later
    one is the value from there on, which is how the string reads to MLT."""
    if frame <= keys[0][0]:
        return keys[0][1]
    for (x0, y0), (x1, y1) in pairwise(keys):
        if x0 <= frame <= x1:
            if x1 == x0:
                return y1
            if frame < x1:
                return y0 + (y1 - y0) * (frame - x0) / (x1 - x0)
    return keys[-1][1]


def slice_gain_keys(
    keys: tuple[tuple[int, float], ...], start: int, frames: int
) -> tuple[tuple[int, float], ...]:
    """`Entry.gain_keys` for the `frames` frames of an entry from offset
    `start` on, rebased to the piece — with a key at each cut edge carrying the
    envelope's value there, so the piece ramps exactly as the whole did."""
    if not keys:
        return ()
    listed = list(keys)
    end = start + frames - 1
    inside = [(offset - start, level) for offset, level in listed if start < offset < end]
    return ((0, round(_line_at(listed, start), 2)), *inside, (frames - 1, round(_line_at(listed, end), 2)))


def _fade_level(entry: Entry) -> str:
    """The `volume` filter's animation string for this entry's fades.

    Every edge is stated explicitly — the head key when only fading out, the
    tail key when only fading in — so nothing relies on how MLT extrapolates
    past a final keyframe, which the probe did not measure. Every position is
    `entry.src_in` plus its offset into the entry: byte-identical to before
    this offset existed for every caller so far (music, tail, an unpinned
    head), which all read from `src_in=0`, and correct for the first caller
    that does not (a hold, which always reads from deep inside its asset).

    The plateau a fade ramps to and holds at is `entry.gain_db`, not a
    hardcoded 0 — a flat, non-fading level shift (the cold-open head's own
    reason for existing) reuses this exact mechanism rather than a second
    filter type. `gain_db=0.0` is unity, so every caller before this field
    existed still gets exactly the plateau it always got.
    """
    first = entry.src_in
    last = entry.src_in + entry.frames - 1
    plateau = entry.gain_db
    keys: list[tuple[int, float]] = []
    if entry.fade_in_frames and entry.crossfade_in:
        keys += _power_ramp(first, entry.fade_in_frames, plateau, rising=True)
    elif entry.fade_in_frames:
        keys += [(first, FADE_FLOOR_DB), (first + entry.fade_in_frames, plateau)]
    else:
        keys += [(first, plateau)]
    if entry.fade_out_frames and entry.crossfade_out:
        keys += _power_ramp(last - entry.fade_out_frames, entry.fade_out_frames, plateau, rising=False)
    elif entry.fade_out_frames:
        keys += [(last - entry.fade_out_frames, plateau), (last, FADE_FLOOR_DB)]
    else:
        keys += [(last, plateau)]
    if entry.gain_keys:
        # Both are straight lines between their own keys, so their sum is a
        # straight line between the union of the two — evaluating it there is
        # exact, not an approximation of either.
        envelope = [(first + offset, level) for offset, level in entry.gain_keys]
        frames = sorted({frame for frame, _ in keys} | {frame for frame, _ in envelope})
        keys = [
            (frame, round(max(FADE_FLOOR_DB, _line_at(keys, frame) + _line_at(envelope, frame)), 2))
            for frame in frames
        ]
    # `:g` rather than a bare f-string: `plateau` is a float now (`gain_db`
    # defaults to 0.0), and a bare `{0.0}` prints "0.0" where the old
    # hardcoded-int plateau printed "0" — `:g` keeps every existing document
    # byte-identical (0.0 -> "0", -60 -> "-60") while still spelling a real
    # gain like 15.1 in full.
    return ";".join(f"{frame}={level:g}" for frame, level in keys)


def _node_key(entry: Entry) -> Any:
    """What a lane's node dict is keyed on: the file, or the entry itself when
    it carries a retime, since each retimed entry reads its own chain."""
    return entry if entry.time_map else entry.resource


def _playlist(playlist_id: str, entries: list[Entry], nodes: dict[Any, str]) -> ET.Element:
    """One track's entries, laid end to end.

    No `<blank>` is emitted, ever — see this module's docstring. The entries
    are contiguous because the caller has already been refused if they were
    not, so a gap cannot arrive here to be papered over.

    An entry carrying fades gets one `volume` filter attached to the entry
    itself — keyframe positions are relative to the entry (measured, see
    `FADE_FLOOR_DB`), which is what makes the fade land on the bed's own
    first and last audible frames however much silence sits beside it on
    the lane.
    """
    playlist = ET.Element("playlist", {"id": playlist_id})
    for index, entry in enumerate(entries):
        node = ET.SubElement(
            playlist,
            "entry",
            {
                "producer": nodes[_node_key(entry)],
                "in": str(entry.src_in),
                "out": str(entry.src_out),
            },
        )
        if entry.fade_in_frames or entry.fade_out_frames or entry.gain_db or entry.gain_keys:
            filt = ET.SubElement(node, "filter", {"id": f"{playlist_id}fade{index}"})
            _property(filt, "mlt_service", "volume")
            _property(filt, "level", _fade_level(entry))
    return playlist


def _pane_playlist(
    playlist_id: str, entries: list[Entry], nodes: dict[Any, str], split: set[Any]
) -> ET.Element:
    """A split pane's overlay track: the lane's own entries, blanked where it is not split.

    The deliberate `<blank>` this module's docstring carves out. The frame
    arithmetic is the lane's, entry for entry, so this track is exactly as
    long as the one it sits over and the run of blanks is what lets the lower
    half of the frame show the picture underneath.

    Consecutive blanks are merged, which is cosmetic and worth it: an
    unsplit film of 400 shots would otherwise write 400 one-shot blanks.
    """
    playlist = ET.Element("playlist", {"id": playlist_id})
    pending = 0
    for entry in entries:
        if _node_key(entry) not in split:
            pending += entry.frames
            continue
        if pending:
            ET.SubElement(playlist, "blank", {"length": str(pending)})
            pending = 0
        ET.SubElement(
            playlist,
            "entry",
            {
                "producer": nodes[_node_key(entry)],
                "in": str(entry.src_in),
                "out": str(entry.src_out),
            },
        )
    if pending:
        ET.SubElement(playlist, "blank", {"length": str(pending)})
    return playlist


def _transition(parent: ET.Element, transition_id: str, properties: dict[str, str]) -> None:
    node = ET.SubElement(parent, "transition", {"id": transition_id})
    for name, value in properties.items():
        _property(node, name, value)


#: A slide's curve, by the name `reframe` takes, as MLT's keyframe operator —
#: the character written before `=` on the key that leaves. Deliberately
#: short: linear and the cubic family only, because those are curves whose
#: shape can be reviewed without a render. The cubic in-out was measured
#: within 0.41px of its formula (`~/proofcut-work/spikes/mlt-retime/`), the
#: in and out singles through the writer itself (HISTORY.md § Eased slides and
#: event-addressed windows). The spline (`~`) is left out on purpose: its
#: shape depends on the keys either side, so a held window after a slide
#: could pull it past its own target.
EASINGS: dict[str, str] = {"linear": "", "ease": "i", "ease-in": "g", "ease-out": "h"}


def ease_fraction(name: str, t: float) -> float:
    """How far along a slide on curve `name` is at time fraction `t`.

    The formulas MLT's cubic operators follow — the review sheet's stand-in
    for the render, exact at both ends like the linear one it replaces.
    """
    t = min(max(t, 0.0), 1.0)
    if name == "ease-in":
        return t**3
    if name == "ease-out":
        return 1 - (1 - t) ** 3
    if name == "ease":
        return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
    return t


def _off_unity(dest: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """A slide's start drawn one pixel larger, so its move is never a pure translation.

    `qtblend` draws the source at exactly its own size as a pure translation,
    and **a pure translation snaps to whole pixels**: a 30px pan over 10s at
    1:1 moved in 1px jumps every tenth frame, while the same pan at 0.75,
    1.333 or 1.5 — or at 2561/2560 — moved smoothly, within 0.24px of the
    line. Reached whenever a crop is the canvas's own size, which is the
    1080p crop of a 1440p screen recording. Only a slide's own key is grown,
    so a held window keeps its exact rect and its document stays
    byte-identical. docs/plans/NATIVE.md § Part B, B2;
    `~/proofcut-work/spikes/eased-pan/`.
    """
    x, y, w, h = dest
    return (x, y, w + 1, h + 1)


def _reframe_filter(
    node: ET.Element,
    reframe: Reframe,
    resolution: tuple[int, int],
    rate: float,
    place: Placement | None = None,
) -> bool:
    """Hang the crop-to-fill filter on one timeline producer, if it says anything.

    `qtblend` as a *filter* rather than a transition — the same service the
    compositing transitions below use, which is why this needed no new
    dependency and no consumer change (PLAN.md § Aspect swap, finding 3).

    Still **one filter per node however many windows it carries**: the rect is
    keyframable and the keys run on source frames, so per-shot framing needed
    no second node and no new service (PLAN.md § Per-shot framing, finding 3).
    """
    if reframe.is_identity(resolution):
        return False
    node_filter = ET.SubElement(node, "filter", {"id": f"filter_{node.get('id')}"})
    _property(node_filter, "mlt_service", "qtblend")
    _property(node_filter, "rect", reframe.rect_property(resolution, rate, place))
    return True


def _pane_filter(
    node: ET.Element,
    reframe: Reframe,
    resolution: tuple[int, int],
    rate: float,
    place: Placement | None = None,
) -> None:
    """The same filter on a split's second node, carrying the lower pane.

    A second *node*, not a second service: the split needed no new MLT
    machinery at all, which the probe behind PLAN.md § The stacked split
    settled against the plan's own claim that it was a new render path.
    """
    node_filter = ET.SubElement(node, "filter", {"id": f"filter_{node.get('id')}"})
    _property(node_filter, "mlt_service", "qtblend")
    _property(node_filter, "rect", reframe.pane_rect_property(resolution, rate, place))


#: A blur-fill background's blur, as `box_blur`'s radius. Relative to the
#: image, which is why it is `box_blur` and never an avfilter blur: a pixel
#: sigma is three times as strong on a third-size canvas (PLAN.md § Blur-fill,
#: finding 2). **The unit is not a plain percent**: melt's own
#: `-query filter=box_blur` says 100 is a radius of 10% of the image *width*,
#: for `vradius` too, so 12 is 1.2% — 23px on a 1920-wide source, which is the
#: seam the spike measured. Fixed, with the darkening, until a real render has
#: been watched.
FILL_BLUR = 12
FILL_DARKEN = 0.7


def _fill_filters(
    node: ET.Element,
    reframe: Reframe,
    resolution: tuple[int, int],
    rate: float,
    place: Placement | None = None,
) -> None:
    """Blur, darken, then place — on a fill background's own node.

    The order is the spike's: the blur runs on the source frame before
    `qtblend` scales it, so its percentage is of the source and the look is the
    same on any canvas.
    """
    node_id = node.get("id")
    blur = ET.SubElement(node, "filter", {"id": f"blur_{node_id}"})
    _property(blur, "mlt_service", "box_blur")
    _property(blur, "hradius", str(FILL_BLUR))
    _property(blur, "vradius", str(FILL_BLUR))
    dark = ET.SubElement(node, "filter", {"id": f"dark_{node_id}"})
    _property(dark, "mlt_service", "brightness")
    _property(dark, "level", str(FILL_DARKEN))
    place_filter = ET.SubElement(node, "filter", {"id": f"filter_{node_id}"})
    _property(place_filter, "mlt_service", "qtblend")
    _property(place_filter, "rect", reframe.fill_rect_property(resolution, rate, place))


def reframed_nodes(root: ET.Element) -> dict[str, str]:
    """Every rendered producer carrying a reframe, as node id → rect.

    The readback half of the trap finding 3 names: `mlt.py` writes one node
    per distinct resource **per role**, so a file used by both the edit and
    the picture lane has two, and a reframe applied per *resource* would crop
    it on one track and letterbox it on the other in the same frame. The bin's
    producers are excluded on purpose — `xml_retain` keeps them out of the
    render, and a bin entry is the raw media, not a timeline placement.
    """
    found: dict[str, str] = {}
    for node in [*root.findall("chain"), *root.findall("producer")]:
        node_id = node.get("id") or ""
        if node_id.startswith(("bin", "ochain")):
            # An overlay's filter animates a still that is never cropped —
            # the same `rect` property, and not a reframe.
            continue
        for node_filter in node.findall("filter"):
            rect = node_filter.find("property[@name='rect']")
            if rect is not None and rect.text:
                found[node_id] = rect.text
    return found


def document(
    *,
    audio: list[Entry],
    picture: list[Entry] | None = None,
    music: list[Entry] | None = None,
    music2: list[Entry] | None = None,
    holds: list[Entry] | None = None,
    overlays: list[Overlay] | None = None,
    sounds: list[list[Entry]] | None = None,
    insets: list[Inset] | None = None,
    dissolves: list[Dissolve] | None = None,
    tail_fade: Dissolve | None = None,
    rate: float,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    reframe: dict[str, Reframe] | None = None,
    name: str = "proofcut",
) -> ET.Element:
    """Build the whole MLT document, and check it against its own frame total.

    `audio` is the `Edit` — proofcut's single subtractive track, whatever media
    it holds; `picture` is the cue table's lane over the top of it. The names
    are the roles they play in the finished video, not a claim about streams:
    an edit whose own clips carry video gets that track composited too, rather
    than rendered as sound over black, and each entry says for itself whether
    it has any.

    The picture lane must cover the timeline exactly. A short lane would
    become a `<blank>` and a long one would extend the render past the audio,
    and both are silent — hence a refusal here rather than a warning.

    `music` is the A2 lane — a second audio track mixed additively alongside
    the edit's own (PLAN.md § The A2 music lane — the design note). It is one
    more per-role node set, playlist pair, tractor, stack entry and `mix`
    transition, generalized from what this module's own docstring already
    named for unmuting a shot; nothing about it is a new writer concept, and
    that was measured against real `melt` rather than assumed
    (`~/proofcut-work/spikes/a2-probe`). **The lane must cover the timeline exactly, by
    construction** — the caller pads with real silent entries and trims an
    over-long asset by frame count, the note's resolution (b), so that
    `declared_frames()` below keeps needing zero exceptions. melt would in
    fact pad a short A2 with true silence and clip a long one (both measured,
    both exit 0), but the check refuses the mismatch anyway: relying on an
    un-checked melt behaviour because it happens to be safe today is the
    thing the tail's own silent-WAV precedent exists to not do.

    `reframe` maps a resource to the rects of it that survive into the frame —
    one, or a window per camera shot — and is what turns a swapped canvas from
    a pillarbox into a filled one. It is applied per *node* rather than per
    resource, and never to a still: a card is authored at the canvas and
    re-authored when the canvas moves
    (`card_reauthor`), so cropping one would be proofcut deciding to lose a
    corner of a title it drew itself.

    A reframe carrying **split panes** grows the document by a track per lane
    that has one: a second node of the same resource, a playlist holding that
    lane's entries with everything unsplit blanked out, and one more compositing
    transition. Nothing else changes — no new service, no mask, no crop filter.

    A reframe carrying **fill windows** does the same, one track per lane,
    placed *under* the lane it backs: `fchain`/`fvchain` nodes with
    `_fill_filters`, blanked where not filled. The picture lane's background
    sits above the edit lane, or the edit's footage would show through a
    filled shot's bars.

    `music2` is the bed's second lane, and exists only for a crossfade: two
    passages that overlap cannot share a playlist, so the writer alternates
    them across `music` and `music2`, each padded to the timeline like `music`
    and mixed the same way. Its ids are their own (nchain/playlist12/
    playlist13/tractorC), so a bed that never overlaps — every bed before
    passages existed — writes no second lane and the same bytes as before.

    `holds` is a fourth, audio-only lane, structurally identical to `music`
    (own coverage check, own node prefix, own playlist pair, own tractor, own
    additive `mix` transition) but with the opposite mute: its nodes carry
    real footage that has both picture and sound, and the picture is what
    gets switched off. The picture lane's own convention silences audio with
    `audio_index=-1` while leaving `video_index` alone; a hold node is the
    exact mirror — `video_index=-1` alone, no `audio_index` at all, because
    `astream="0"` (the base node's own default) is already correct regardless
    of container layout (a *relative* stream selector, not the absolute one
    `media.py`'s two-mic trap is about — CLAUDE.md — and every hold node
    resolves through `media.media_path()`, which already refuses a
    multi-stream container before this module ever sees one). Never a
    `qtblend` composite: this lane has nothing on screen to composite, the
    same reason `music` never gets one.

    `sounds` is the one-shot lanes (docs/plans/NATIVE.md § B4): each lane is
    `music`'s shape — padded to the timeline with real silent entries, its
    own nodes, playlist pair, tractor and additive `mix` — and there are as
    many as overlapping hits need. **Every hit entry must read a file whose
    length MLT counts as exactly the frames the entry claims, and at least
    two**: a file counted one frame long plays nothing, and an entry claiming
    past its file shortens the lane so every later hit plays early, both at
    exit 0 (`~/proofcut-work/spikes/sfx-probe`). The caller's padded copies
    are what make that true; the writer cannot see a file's length.

    `insets` are clips drawn into a rectangle of the recording
    (docs/plans/NATIVE.md § B6): each its own track directly over the edit's
    (and its split's), under the picture lane, so a cue that covers the
    recording covers its inset too. The rect is a `qtblend` on the inset's
    playlist, keyed from the host entry's own camera (`inset_rect`); a dim is
    a black `color` track under it. Ids in their own namespace
    (ichain/iplaylist/tractorI, idim/idplaylist/tractorJ), so a document with
    no insets is byte-identical to one built before they existed.
    """
    if not audio:
        raise MLTError("an MLT document needs at least one entry on the edit's track")
    total_frames = sum(entry.frames for entry in audio)
    picture = picture or []
    if picture:
        covered = sum(entry.frames for entry in picture)
        if covered != total_frames:
            raise MLTError(
                f"the picture lane covers {covered} frames but the timeline is "
                f"{total_frames} — MLT would pad the difference with a silent "
                "<blank> (or run the render long), so the lane has to be exact"
            )
    music = music or []
    if music:
        covered = sum(entry.frames for entry in music)
        if covered != total_frames:
            raise MLTError(
                f"the music lane covers {covered} frames but the timeline is "
                f"{total_frames} — the caller pads with real silent entries and "
                "trims an over-long asset by frame count, so every declared "
                "length keeps agreeing and declared_frames() needs no exception "
                "(PLAN.md § The A2 music lane, resolution (b))"
            )
        wrong = [entry.resource for entry in music if entry.is_image]
        if wrong:
            raise MLTError(
                f"the music lane holds a still ({wrong[0]!r}) — a held frame has "
                "no sound to mix, so a card can never be a music entry"
            )
    music2 = music2 or []
    if music2:
        if not music:
            raise MLTError("a second music lane needs a first — `music2` only carries crossfades")
        covered = sum(entry.frames for entry in music2)
        if covered != total_frames:
            raise MLTError(
                f"the second music lane covers {covered} frames but the timeline is "
                f"{total_frames} — `music`'s own discipline"
            )
        wrong = [entry.resource for entry in music2 if entry.is_image]
        if wrong:
            raise MLTError(f"the second music lane holds a still ({wrong[0]!r})")
    holds = holds or []
    if holds:
        covered = sum(entry.frames for entry in holds)
        if covered != total_frames:
            raise MLTError(
                f"the holds lane covers {covered} frames but the timeline is "
                f"{total_frames} — `music`'s own discipline: the caller pads "
                "with real silent entries and trims by frame count, so every "
                "declared length keeps agreeing"
            )
        wrong = [entry.resource for entry in holds if entry.is_image]
        if wrong:
            raise MLTError(
                f"the holds lane holds a still ({wrong[0]!r}) — a hold plays a "
                "clip's own clean audio, and a still has none to play"
            )
    overlays = overlays or []
    for overlay in overlays:
        _check_overlay(overlay, total_frames)
    sounds = [lane for lane in (sounds or []) if lane]
    for number, sound_lane in enumerate(sounds):
        covered = sum(entry.frames for entry in sound_lane)
        if covered != total_frames:
            raise MLTError(
                f"sound lane {number} covers {covered} frames but the timeline is "
                f"{total_frames} — `music`'s own discipline: padded with real "
                "silent entries, never a <blank>"
            )
        wrong = [entry.resource for entry in sound_lane if entry.is_image]
        if wrong:
            raise MLTError(f"sound lane {number} holds a still ({wrong[0]!r})")
    sound_entries = [entry for sound_lane in sounds for entry in sound_lane]
    insets = insets or []
    for inset in insets:
        _check_inset(inset, total_frames)
        _host_entry(inset, audio)
    dissolves = dissolves or []
    for dissolve in dissolves:
        _check_dissolve(dissolve, total_frames)
    if tail_fade is not None:
        _check_dissolve(tail_fade, total_frames)
    for entry in [*music, *music2, *holds, *sound_entries]:
        if entry.time_map:
            raise MLTError(
                f"{entry.resource!r} carries a retime on an audio lane — only the edit and "
                "the picture lane are retimed; everything else plays at 1x"
            )
    for entry in [*audio, *picture, *music, *music2, *holds, *sound_entries]:
        if entry.fade_in_frames < 0 or entry.fade_out_frames < 0:
            raise MLTError(f"negative fade frames on {entry.resource!r}")
        if entry.fade_in_frames + entry.fade_out_frames > max(entry.frames - 1, 0):
            raise MLTError(
                f"fades of {entry.fade_in_frames}+{entry.fade_out_frames} frames "
                f"do not fit inside the {entry.frames} frames of "
                f"{entry.resource!r} — the caller sizes fades to the audible "
                "span before they reach the writer"
            )

    root = ET.Element(
        "mlt",
        {
            "LC_NUMERIC": "C",
            "version": MLT_VERSION,
            "producer": "main_bin",
        },
    )
    root.append(_profile(rate, resolution))

    # The black background every track composites over. Its `length` is one of
    # the four declared lengths melt takes the longest of.
    background = ET.SubElement(root, "producer", {"id": "producer0"})
    for prop_name, value in {
        "length": str(total_frames),
        "eof": "continue",
        "resource": "black",
        "mlt_service": "color",
        "kdenlive:playlistid": "black_track",
        "mlt_image_format": "rgba",
        "aspect_ratio": "1",
    }.items():
        _property(background, prop_name, value)

    # One node per distinct resource per role. A file used by both the edit
    # and the picture lane gets two, because "is its audio on" is a property
    # of the producer, not of the entry — but both point at one bin entry, so
    # `kdenlive:id` is keyed on the resource and not on the node.
    sources: dict[str, Entry] = {}
    inset_entries = [Entry(inset.resource, inset.src_in, inset.frames, has_video=True) for inset in insets]
    dissolve_entries = [Entry(d.resource, d.src_in, d.frames, has_video=True) for d in dissolves]
    tail_fade_entries = (
        [Entry(tail_fade.resource, tail_fade.src_in, tail_fade.frames, is_image=tail_fade.is_image, has_video=True)]
        if tail_fade is not None
        else []
    )
    for entry in [
        *audio, *picture, *music, *music2, *holds, *sound_entries, *inset_entries, *dissolve_entries,
        *tail_fade_entries,
    ]:  # fmt: skip
        # A bin entry is the raw media, so it never carries an entry's retime.
        sources.setdefault(entry.resource, replace(entry, time_map=(), gain_keys=()))
    bin_ids = {resource: index + 2 for index, resource in enumerate(sources)}

    reframe = reframe or {}
    # Every rendered node this should have reached, counted before the nodes
    # exist so the readback below has something independent to check against.
    wants_reframe = {
        (role, _node_key(entry))
        for role, lane in (("edit", audio), ("picture", picture))
        for entry in lane
        if not entry.is_image
        and entry.has_video
        and entry.resource in reframe
        and not reframe[entry.resource].is_identity(resolution)
    }

    def _split_in(lane: list[Entry]) -> dict[str, Entry]:
        """The resources on this lane that are drawn as a stacked split.

        Ordered by first appearance, so a document's pane nodes are numbered
        the way its ordinary ones are and a rebuild is byte-identical.
        """
        found: dict[Any, Entry] = {}
        for entry in lane:
            if entry.is_image or not entry.has_video:
                continue
            if entry.resource in reframe and reframe[entry.resource].panes:
                found.setdefault(_node_key(entry), entry)
        return found

    def _fill_in(lane: list[Entry]) -> dict[str, Entry]:
        """The resources on this lane with a blur-filled window, first-seen order."""
        found: dict[Any, Entry] = {}
        for entry in lane:
            if entry.is_image or not entry.has_video:
                continue
            if entry.resource in reframe and reframe[entry.resource].fills:
                found.setdefault(_node_key(entry), entry)
        return found

    def _fill_lane(
        lane: list[Entry], prefix: str, playlists: tuple[str, str], tractor_id: str, track_name: str
    ) -> dict[str, str]:
        """A lane's blur-fill background: silent nodes, a blanked playlist, a tractor."""
        nodes: dict[Any, str] = {}
        for key, entry in _fill_in(lane).items():
            node_id = f"{prefix}{len(nodes)}"
            nodes[key] = node_id
            node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
            _property(node, "audio_index", "-1")
            _property(node, "video_index", "0")
            _property(node, "set.test_audio", "1")
            _fill_filters(node, reframe[entry.resource], resolution, rate, placement(entry))
            root.append(node)
        if nodes:
            root.append(_pane_playlist(playlists[0], lane, nodes, set(nodes)))
            root.append(ET.Element("playlist", {"id": playlists[1]}))
            track = ET.SubElement(
                root, "tractor", {"id": tractor_id, "in": "0", "out": str(total_frames - 1)}
            )
            _property(track, "kdenlive:timeline_active", "1")
            _property(track, "kdenlive:track_name", track_name)
            for playlist_id in playlists:
                ET.SubElement(track, "track", {"producer": playlist_id, "hide": "audio"})
        return nodes

    edit_fills = _fill_lane(audio, "fchain", ("playlist14", "playlist15"), "tractorD", "Edit fill")

    audio_nodes: dict[Any, str] = {}
    for entry in audio:
        if _node_key(entry) in audio_nodes:
            continue
        node_id = f"chain{len(audio_nodes)}"
        audio_nodes[_node_key(entry)] = node_id
        node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
        _property(node, "set.test_audio", "0")
        _property(node, "set.test_video", "0" if entry.has_video else "1")
        if entry.has_video and entry.resource in reframe:
            _reframe_filter(node, reframe[entry.resource], resolution, rate, placement(entry))
        root.append(node)

    root.append(_playlist("playlist0", audio, audio_nodes))
    root.append(ET.Element("playlist", {"id": "playlist1"}))
    edit_track = ET.SubElement(
        root, "tractor", {"id": "tractor0", "in": "0", "out": str(total_frames - 1)}
    )
    _property(edit_track, "kdenlive:timeline_active", "1")
    _property(edit_track, "kdenlive:track_name", "Edit")
    audio_has_video = any(entry.has_video for entry in audio)
    hide = {} if audio_has_video else {"hide": "video"}
    for playlist_id in ("playlist0", "playlist1"):
        ET.SubElement(edit_track, "track", {"producer": playlist_id, **hide})

    # The split's second half, one overlay track per lane that has one. Its
    # node is a *silent* copy of the lane's — `audio_index=-1` for the picture
    # lane's own reason, and here it also stops the edit's sound being mixed
    # in twice, which is a doubled VO at exit 0.
    edit_panes: dict[Any, str] = {}
    for key, entry in _split_in(audio).items():
        node_id = f"pchain{len(edit_panes)}"
        edit_panes[key] = node_id
        node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
        _property(node, "audio_index", "-1")
        _property(node, "video_index", "0")
        _property(node, "set.test_audio", "1")
        _pane_filter(node, reframe[entry.resource], resolution, rate, placement(entry))
        root.append(node)
    if edit_panes:
        root.append(_pane_playlist("playlist4", audio, edit_panes, set(edit_panes)))
        root.append(ET.Element("playlist", {"id": "playlist5"}))
        edit_pane_track = ET.SubElement(
            root, "tractor", {"id": "tractor3", "in": "0", "out": str(total_frames - 1)}
        )
        _property(edit_pane_track, "kdenlive:timeline_active", "1")
        _property(edit_pane_track, "kdenlive:track_name", "Edit split")
        for playlist_id in ("playlist4", "playlist5"):
            ET.SubElement(edit_pane_track, "track", {"producer": playlist_id, "hide": "audio"})

    picture_nodes: dict[Any, str] = {}
    if picture:
        for entry in picture:
            if _node_key(entry) in picture_nodes:
                continue
            node_id = f"vchain{len(picture_nodes)}"
            picture_nodes[_node_key(entry)] = node_id
            node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
            if not entry.is_image:
                # Film under a VO plays silent — and `audio_index=-1` is what
                # makes it silent at the source, so no mix downstream can
                # accidentally let it back in.
                _property(node, "audio_index", "-1")
                _property(node, "video_index", "0")
                _property(node, "set.test_audio", "1")
                if entry.resource in reframe:
                    _reframe_filter(
                        node, reframe[entry.resource], resolution, rate, placement(entry)
                    )
            root.append(node)

        root.append(_playlist("playlist2", picture, picture_nodes))
        root.append(ET.Element("playlist", {"id": "playlist3"}))
        picture_track = ET.SubElement(
            root, "tractor", {"id": "tractor1", "in": "0", "out": str(total_frames - 1)}
        )
        _property(picture_track, "kdenlive:timeline_active", "1")
        _property(picture_track, "kdenlive:track_name", "Picture")
        for playlist_id in ("playlist2", "playlist3"):
            ET.SubElement(picture_track, "track", {"producer": playlist_id, "hide": "audio"})

    # The insets, each on its own track (and its dim on another) — see the
    # docstring. The node is the edit's shape with its own audio, or silenced
    # like a picture node when the inset is muted.
    inset_tracks: list[tuple[str, bool]] = []
    for index, inset in enumerate(insets):
        if inset.dim:
            dim = ET.SubElement(root, "producer", {"id": f"idim{index}"})
            for name_, value in {
                "length": str(total_frames),
                "eof": "continue",
                "resource": "#ff000000",
                "mlt_service": "color",
                "mlt_image_format": "rgba",
            }.items():
                _property(dim, name_, value)
            dim_filter = ET.SubElement(dim, "filter", {"id": f"filter_idim{index}"})
            _property(dim_filter, "mlt_service", "brightness")
            _property(dim_filter, "level", "1")
            _property(dim_filter, "alpha", _alpha_keys(inset, 0, inset.dim))
            playlist = ET.SubElement(root, "playlist", {"id": f"idplaylist{index}a"})
            if inset.start:
                ET.SubElement(playlist, "blank", {"length": str(inset.start)})
            ET.SubElement(playlist, "entry", {"producer": f"idim{index}", "in": "0", "out": str(inset.frames - 1)})
            if inset.end < total_frames:
                ET.SubElement(playlist, "blank", {"length": str(total_frames - inset.end)})
            ET.SubElement(root, "playlist", {"id": f"idplaylist{index}b"})
            track = ET.SubElement(
                root, "tractor", {"id": f"tractorJ{index}", "in": "0", "out": str(total_frames - 1)}
            )
            _property(track, "kdenlive:timeline_active", "1")
            _property(track, "kdenlive:track_name", f"Inset {index + 1} dim")
            for playlist_id in (f"idplaylist{index}a", f"idplaylist{index}b"):
                ET.SubElement(track, "track", {"producer": playlist_id, "hide": "audio"})
            inset_tracks.append((f"tractorJ{index}", False))

        entry = Entry(
            inset.resource,
            inset.src_in,
            inset.frames,
            has_video=True,
            gain_db=inset.gain_db if inset.has_audio else 0.0,
            fade_in_frames=inset.fade_in_frames if inset.has_audio else 0,
            fade_out_frames=inset.fade_out_frames if inset.has_audio else 0,
        )
        node = _source_node(f"ichain{index}", entry, bin_ids[inset.resource], rate)
        _property(node, "video_index", "0")
        if inset.has_audio:
            _property(node, "set.test_audio", "0")
        else:
            _property(node, "audio_index", "-1")
            _property(node, "set.test_audio", "1")
        if inset.fade_in_frames or inset.fade_out_frames:
            fade = ET.SubElement(node, "filter", {"id": f"fade_ichain{index}"})
            _property(fade, "mlt_service", "brightness")
            _property(fade, "level", "1")
            _property(fade, "alpha", _alpha_keys(inset, inset.src_in, 1))
        root.append(node)
        playlist = ET.SubElement(root, "playlist", {"id": f"iplaylist{index}a"})
        if inset.start:
            ET.SubElement(playlist, "blank", {"length": str(inset.start)})
        placed = ET.SubElement(
            playlist, "entry", {"producer": f"ichain{index}", "in": str(entry.src_in), "out": str(entry.src_out)}
        )
        if entry.fade_in_frames or entry.fade_out_frames or entry.gain_db:
            level = ET.SubElement(placed, "filter", {"id": f"iplaylist{index}fade"})
            _property(level, "mlt_service", "volume")
            _property(level, "level", _fade_level(entry))
        if inset.end < total_frames:
            ET.SubElement(playlist, "blank", {"length": str(total_frames - inset.end)})
        # On the playlist, so its keys count render frames (`inset_rect`).
        place_filter = ET.SubElement(playlist, "filter", {"id": f"filter_iplaylist{index}"})
        _property(place_filter, "mlt_service", "qtblend")
        _property(
            place_filter,
            "rect",
            inset_rect(inset, audio, reframe.get(inset.host), resolution, rate),
        )
        ET.SubElement(root, "playlist", {"id": f"iplaylist{index}b"})
        track = ET.SubElement(
            root, "tractor", {"id": f"tractorI{index}", "in": "0", "out": str(total_frames - 1)}
        )
        _property(track, "kdenlive:timeline_active", "1")
        _property(track, "kdenlive:track_name", f"Inset {index + 1}")
        hide = {} if inset.has_audio else {"hide": "audio"}
        for playlist_id in (f"iplaylist{index}a", f"iplaylist{index}b"):
            ET.SubElement(track, "track", {"producer": playlist_id, **hide})
        inset_tracks.append((f"tractorI{index}", inset.has_audio))

    # The dissolves, each on its own silent track over the join it covers —
    # see `Dissolve`. Ids in their own namespace (xchain/xplaylist/tractorX),
    # so a document with none is byte-identical to one built before them.
    def dissolve_track(dissolve: Dissolve, entry: Entry, chain: str, playlist: str, tractor: str, name: str) -> str:
        node = _source_node(chain, entry, bin_ids[dissolve.resource], rate)
        if not dissolve.is_image:
            _property(node, "audio_index", "-1")
            _property(node, "video_index", "0")
            _property(node, "set.test_audio", "1")
            if dissolve.resource in reframe:
                _reframe_filter(node, reframe[dissolve.resource], resolution, rate, None)
        fade = ET.SubElement(node, "filter", {"id": f"fade_{chain}"})
        _property(fade, "mlt_service", "brightness")
        _property(fade, "level", "1")
        # Keyed in the producer's frames, the inset fade's rule, and 1 on the
        # join itself so the pre-roll's last frame is still a step short of it.
        _property(
            fade, "alpha", f"{dissolve.src_in}{EASINGS[dissolve.ease]}=0;{dissolve.src_in + dissolve.frames}=1"
        )
        root.append(node)
        lane = ET.SubElement(root, "playlist", {"id": f"{playlist}a"})
        if dissolve.start:
            ET.SubElement(lane, "blank", {"length": str(dissolve.start)})
        ET.SubElement(lane, "entry", {"producer": chain, "in": str(entry.src_in), "out": str(entry.src_out)})
        if dissolve.end < total_frames:
            ET.SubElement(lane, "blank", {"length": str(total_frames - dissolve.end)})
        ET.SubElement(root, "playlist", {"id": f"{playlist}b"})
        track = ET.SubElement(root, "tractor", {"id": tractor, "in": "0", "out": str(total_frames - 1)})
        _property(track, "kdenlive:timeline_active", "1")
        _property(track, "kdenlive:track_name", name)
        for playlist_id in (f"{playlist}a", f"{playlist}b"):
            ET.SubElement(track, "track", {"producer": playlist_id, "hide": "audio"})
        return tractor

    dissolve_tracks = [
        dissolve_track(dissolve, entry, f"xchain{index}", f"xplaylist{index}", f"tractorX{index}", f"Dissolve {index + 1}")
        for index, (dissolve, entry) in enumerate(zip(dissolves, dissolve_entries))
    ]
    # The tail's card fading in over the film's end: over everything the film
    # draws, overlays included, as the card after the join is (A crossfades
    # into its end card, RECUT.md § What A is). Its own ids, so a document
    # without one is byte-identical.
    tail_fade_tracks = [
        dissolve_track(tail_fade, entry, "tchain0", "tplaylist0", "tractorT", "Tail fade")
        for entry in tail_fade_entries
        if tail_fade is not None
    ]

    picture_fills = _fill_lane(
        picture, "fvchain", ("playlist16", "playlist17"), "tractorE", "Picture fill"
    )

    picture_panes: dict[Any, str] = {}
    for key, entry in _split_in(picture).items():
        node_id = f"pvchain{len(picture_panes)}"
        picture_panes[key] = node_id
        node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
        _property(node, "audio_index", "-1")
        _property(node, "video_index", "0")
        _property(node, "set.test_audio", "1")
        _pane_filter(node, reframe[entry.resource], resolution, rate, placement(entry))
        root.append(node)
    if picture_panes:
        root.append(_pane_playlist("playlist6", picture, picture_panes, set(picture_panes)))
        root.append(ET.Element("playlist", {"id": "playlist7"}))
        picture_pane_track = ET.SubElement(
            root, "tractor", {"id": "tractor4", "in": "0", "out": str(total_frames - 1)}
        )
        _property(picture_pane_track, "kdenlive:timeline_active", "1")
        _property(picture_pane_track, "kdenlive:track_name", "Picture split")
        for playlist_id in ("playlist6", "playlist7"):
            ET.SubElement(picture_pane_track, "track", {"producer": playlist_id, "hide": "audio"})

    # The A2 music lane: audio-only, so its nodes take the probe's own shape
    # (`~/proofcut-work/spikes/a2-probe/build_doc.py`) — sound on, picture declared absent —
    # and no reframe ever reaches one, because there is nothing of it on
    # screen to frame. Ids live in their own namespace (mchain/playlist8/
    # playlist9/tractorA) so a document without music is byte-identical to
    # the one this writer produced before the lane existed.
    music_nodes: dict[str, str] = {}
    if music:
        for entry in music:
            if entry.resource in music_nodes:
                continue
            node_id = f"mchain{len(music_nodes)}"
            music_nodes[entry.resource] = node_id
            node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
            _property(node, "set.test_audio", "0")
            _property(node, "set.test_video", "1")
            root.append(node)

        root.append(_playlist("playlist8", music, music_nodes))
        root.append(ET.Element("playlist", {"id": "playlist9"}))
        music_track = ET.SubElement(
            root, "tractor", {"id": "tractorA", "in": "0", "out": str(total_frames - 1)}
        )
        _property(music_track, "kdenlive:timeline_active", "1")
        _property(music_track, "kdenlive:track_name", "Music")
        for playlist_id in ("playlist8", "playlist9"):
            ET.SubElement(music_track, "track", {"producer": playlist_id, "hide": "video"})

    # The bed's second lane, for crossfades only — `music`'s exact shape with
    # its own node set, since one producer serves one lane per role here.
    music2_nodes: dict[str, str] = {}
    if music2:
        for entry in music2:
            if entry.resource in music2_nodes:
                continue
            node_id = f"nchain{len(music2_nodes)}"
            music2_nodes[entry.resource] = node_id
            node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
            _property(node, "set.test_audio", "0")
            _property(node, "set.test_video", "1")
            root.append(node)

        root.append(_playlist("playlist12", music2, music2_nodes))
        root.append(ET.Element("playlist", {"id": "playlist13"}))
        music2_track = ET.SubElement(
            root, "tractor", {"id": "tractorC", "in": "0", "out": str(total_frames - 1)}
        )
        _property(music2_track, "kdenlive:timeline_active", "1")
        _property(music2_track, "kdenlive:track_name", "Music 2")
        for playlist_id in ("playlist12", "playlist13"):
            ET.SubElement(music2_track, "track", {"producer": playlist_id, "hide": "video"})

    # The holds lane: real footage, picture switched off at the node rather
    # than declared absent (`music`'s `set.test_video=1`) — the resource has
    # actual video, so the node needs `video_index=-1` to stop it being
    # decoded at all, not merely a claim that none exists. `set.test_audio=0`
    # is `music`'s own flag, unchanged: sound is present. No `audio_index` —
    # the base astream selector is already correct (see this function's own
    # docstring, and `media.media_path()`'s containment). Ids in their own
    # namespace (hchain/playlist10/playlist11/tractorB) so a document with no
    # holds is byte-identical to one built before this lane existed.
    hold_nodes: dict[str, str] = {}
    if holds:
        for entry in holds:
            if entry.resource in hold_nodes:
                continue
            node_id = f"hchain{len(hold_nodes)}"
            hold_nodes[entry.resource] = node_id
            node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
            _property(node, "video_index", "-1")
            _property(node, "set.test_audio", "0")
            root.append(node)

        root.append(_playlist("playlist10", holds, hold_nodes))
        root.append(ET.Element("playlist", {"id": "playlist11"}))
        hold_track = ET.SubElement(
            root, "tractor", {"id": "tractorB", "in": "0", "out": str(total_frames - 1)}
        )
        _property(hold_track, "kdenlive:timeline_active", "1")
        _property(hold_track, "kdenlive:track_name", "Holds")
        for playlist_id in ("playlist10", "playlist11"):
            ET.SubElement(hold_track, "track", {"producer": playlist_id, "hide": "video"})

    # The overlay lanes: a `qimage` node per overlay, since each carries its
    # own animation, and a playlist per lane blanked outside its overlays —
    # the split pane's deliberate `<blank>`, by the same frame arithmetic.
    # Ids in their own namespace (ochain/oplaylist/tractorO), so a document
    # with no overlays is byte-identical to one built before they existed.
    overlay_tracks: list[str] = []
    if overlays:
        placed = overlay_lanes(overlays)
        for index, overlay in enumerate(overlays):
            node = ET.Element("producer", {"id": f"ochain{index}"})
            for name_, value in {
                "resource": overlay.resource,
                "mlt_service": "qimage",
                "length": str(round(IMAGE_LENGTH_SECONDS * rate)),
                "eof": "continue",
                "ttl": "1",
            }.items():
                _property(node, name_, value)
            keys = overlay_rect(overlay, resolution)
            if keys is not None:
                node_filter = ET.SubElement(node, "filter", {"id": f"filter_ochain{index}"})
                _property(node_filter, "mlt_service", "qtblend")
                _property(node_filter, "rect", keys)
            root.append(node)
        for lane in range(max(placed) + 1):
            playlist = ET.SubElement(root, "playlist", {"id": f"oplaylist{lane}a"})
            cursor = 0
            members = sorted(
                (overlay.start, index) for index, overlay in enumerate(overlays) if placed[index] == lane
            )
            for _, index in members:
                overlay = overlays[index]
                if overlay.start > cursor:
                    ET.SubElement(playlist, "blank", {"length": str(overlay.start - cursor)})
                ET.SubElement(
                    playlist,
                    "entry",
                    {"producer": f"ochain{index}", "in": "0", "out": str(overlay.frames - 1)},
                )
                cursor = overlay.end
            if cursor < total_frames:
                ET.SubElement(playlist, "blank", {"length": str(total_frames - cursor)})
            ET.SubElement(root, "playlist", {"id": f"oplaylist{lane}b"})
            track = ET.SubElement(
                root, "tractor", {"id": f"tractorO{lane}", "in": "0", "out": str(total_frames - 1)}
            )
            _property(track, "kdenlive:timeline_active", "1")
            _property(track, "kdenlive:track_name", f"Overlay {lane + 1}")
            for playlist_id in (f"oplaylist{lane}a", f"oplaylist{lane}b"):
                ET.SubElement(track, "track", {"producer": playlist_id, "hide": "audio"})
            overlay_tracks.append(f"tractorO{lane}")

    # The sound lanes: `music`'s audio-only node shape, a node set per lane
    # (one producer serves one lane per role here, `music2`'s reason), ids in
    # their own namespace (s<L>chain/splaylist<L>/tractorS<L>) so a document
    # with no sounds is byte-identical to one built before they existed.
    sound_tracks: list[str] = []
    for number, sound_lane in enumerate(sounds):
        nodes: dict[str, str] = {}
        for entry in sound_lane:
            if entry.resource in nodes:
                continue
            node_id = f"s{number}chain{len(nodes)}"
            nodes[entry.resource] = node_id
            node = _source_node(node_id, entry, bin_ids[entry.resource], rate)
            _property(node, "set.test_audio", "0")
            _property(node, "set.test_video", "1")
            root.append(node)
        root.append(_playlist(f"splaylist{number}a", sound_lane, nodes))
        root.append(ET.Element("playlist", {"id": f"splaylist{number}b"}))
        track = ET.SubElement(
            root, "tractor", {"id": f"tractorS{number}", "in": "0", "out": str(total_frames - 1)}
        )
        _property(track, "kdenlive:timeline_active", "1")
        _property(track, "kdenlive:track_name", f"Sounds {number + 1}")
        for playlist_id in (f"splaylist{number}a", f"splaylist{number}b"):
            ET.SubElement(track, "track", {"producer": playlist_id, "hide": "video"})
        sound_tracks.append(f"tractorS{number}")

    # A deterministic uuid: the same project rebuilt twice should produce the
    # same document, so a diff of two exports shows what actually changed.
    sequence_uuid = f"{{{uuid.uuid5(uuid.NAMESPACE_URL, f'proofcut:{name}')}}}"
    sequence = ET.SubElement(
        root, "tractor", {"id": sequence_uuid, "in": "0", "out": str(total_frames - 1)}
    )
    _property(sequence, "kdenlive:uuid", sequence_uuid)
    _property(sequence, "kdenlive:clipname", name)
    # Bottom to top: the black background, the edit, its split's second pane,
    # the picture lane, and that lane's second pane. A pane sits directly over
    # the track it is half of and under everything that was already above it,
    # so adding one cannot change what covers what.
    # A fill background goes directly under the lane it backs.
    stack = ["producer0"]
    if edit_fills:
        stack.append("tractorD")
    stack.append("tractor0")
    if edit_panes:
        stack.append("tractor3")
    # An inset is part of the recording's picture: over the edit, under
    # anything the cue table lays over the recording.
    stack.extend(track for track, _ in inset_tracks)
    # A dissolve is the next recording arriving over this one, its insets
    # included; still under anything the cue table lays over the film.
    stack.extend(dissolve_tracks)
    if picture_fills:
        stack.append("tractorE")
    if picture:
        stack.append("tractor1")
    if picture_panes:
        stack.append("tractor4")
    # Above every picture track: an overlay is drawn over the film.
    stack.extend(overlay_tracks)
    stack.extend(tail_fade_tracks)
    if music:
        stack.append("tractorA")
    if music2:
        stack.append("tractorC")
    if holds:
        stack.append("tractorB")
    stack.extend(sound_tracks)
    for producer in stack:
        ET.SubElement(sequence, "track", {"producer": producer})

    # Without transitions a tractor renders its first track and drops the
    # rest, silently (HISTORY.md § 4) — the sound needs an additive mix and
    # every picture track needs compositing over the black background.
    _transition(
        sequence,
        "transition0",
        {
            "a_track": "0",
            "b_track": "1",
            "mlt_service": "mix",
            "internal_added": "237",
            "always_active": "1",
            "sum": "1",
        },
    )
    # `b_track` is an index into the track list just written, which is why the
    # order above and the order here are one loop and not two lists that have
    # to be kept in step. The edit and the music/holds lanes are the only
    # tracks that can be soundless-picture audio; every other one carries
    # video by construction — and neither audio-only lane blends, because
    # neither has anything on screen to composite.
    blended = 0
    for index, producer in enumerate(stack):
        if (
            index == 0
            or producer in ("tractorA", "tractorB", "tractorC")
            or producer in sound_tracks
            or (producer == "tractor0" and not audio_has_video)
        ):
            continue
        _transition(
            sequence,
            f"transition{blended + 1}",
            {
                "a_track": "0",
                "b_track": str(index),
                "mlt_service": "qtblend",
                "internal_added": "237",
                "always_active": "1",
                "disable": "0",
            },
        )
        blended += 1
    # The music lane's own mix — the second `mix` the module docstring said an
    # unmuted track would need, here for a whole track rather than a shot.
    # Against track 0 like transition0: mix does not care that the black
    # background carries no sound, and `sum=1` keeps it additive and lossless
    # (measured — both tones survive at their exact source amplitudes,
    # `~/proofcut-work/spikes/a2-probe`). A running counter rather than `blended + 1` twice
    # over: with both a music lane and a holds lane, the second `+ 1` would
    # collide with the first's own transition id.
    extra_mix = blended
    if music:
        extra_mix += 1
        _transition(
            sequence,
            f"transition{extra_mix}",
            {
                "a_track": "0",
                "b_track": str(stack.index("tractorA")),
                "mlt_service": "mix",
                "internal_added": "237",
                "always_active": "1",
                "sum": "1",
            },
        )
    if music2:
        extra_mix += 1
        _transition(
            sequence,
            f"transition{extra_mix}",
            {
                "a_track": "0",
                "b_track": str(stack.index("tractorC")),
                "mlt_service": "mix",
                "internal_added": "237",
                "always_active": "1",
                "sum": "1",
            },
        )
    # The holds lane's own mix — `music`'s exact mechanism, one track over:
    # additive against track 0, never composited, because a hold has nothing
    # on screen either (its picture is switched off at the node, `video_index
    # = -1`, the whole reason this lane's nodes differ from music's).
    if holds:
        extra_mix += 1
        _transition(
            sequence,
            f"transition{extra_mix}",
            {
                "a_track": "0",
                "b_track": str(stack.index("tractorB")),
                "mlt_service": "mix",
                "internal_added": "237",
                "always_active": "1",
                "sum": "1",
            },
        )
    # An inset with sound is mixed like any other lane (spike finding F).
    for inset_track, audible in inset_tracks:
        if not audible:
            continue
        extra_mix += 1
        _transition(
            sequence,
            f"transition{extra_mix}",
            {
                "a_track": "0",
                "b_track": str(stack.index(inset_track)),
                "mlt_service": "mix",
                "internal_added": "237",
                "always_active": "1",
                "sum": "1",
            },
        )
    # Each sound lane's own mix — the same additive `mix`, one per lane.
    for sound_track in sound_tracks:
        extra_mix += 1
        _transition(
            sequence,
            f"transition{extra_mix}",
            {
                "a_track": "0",
                "b_track": str(stack.index(sound_track)),
                "mlt_service": "mix",
                "internal_added": "237",
                "always_active": "1",
                "sum": "1",
            },
        )

    # The bin. `xml_retain` keeps this playlist out of the render — it is the
    # project's media list, not a track — and every timeline producer points
    # at its bin entry through `kdenlive:id`, which is what stops Kdenlive
    # opening the file with a populated timeline over an empty bin.
    main_bin = ET.SubElement(root, "playlist", {"id": "main_bin"})
    _property(main_bin, "kdenlive:docproperties.uuid", sequence_uuid)
    _property(main_bin, "kdenlive:docproperties.version", "1.1")
    _property(main_bin, "xml_retain", "1")
    ET.SubElement(main_bin, "entry", {"producer": sequence_uuid, "in": "0", "out": "0"})
    for index, (resource, entry) in enumerate(sources.items()):
        node = _source_node(f"bin{index}", entry, bin_ids[resource], rate)
        root.insert(list(root).index(main_bin), node)
        ET.SubElement(main_bin, "entry", {"producer": f"bin{index}", "in": "0"})

    project = ET.SubElement(
        root, "tractor", {"id": "tractor2", "in": "0", "out": str(total_frames - 1)}
    )
    _property(project, "kdenlive:projectTractor", "1")
    ET.SubElement(
        project, "track", {"producer": sequence_uuid, "in": "0", "out": str(total_frames - 1)}
    )

    _attach_links(root)

    # The same discipline as the frame check below, for the same reason: a
    # reframe that reached one of a file's two nodes renders a film that is
    # cropped on one track and letterboxed on the other, and melt exits 0.
    expected = {
        (audio_nodes if role == "edit" else picture_nodes)[key]
        for role, key in wants_reframe
    } | set(edit_panes.values()) | set(picture_panes.values())
    expected |= set(edit_fills.values()) | set(picture_fills.values())
    # A dissolve's pre-roll is framed by its own camera, like the edit's node.
    expected |= {
        f"xchain{index}"
        for index, dissolve in enumerate(dissolves)
        if dissolve.resource in reframe and not reframe[dissolve.resource].is_identity(resolution)
    }
    found = set(reframed_nodes(root))
    if expected != found:
        raise MLTError(
            f"the reframe reached nodes {sorted(found)} but belongs on "
            f"{sorted(expected)} — one node per resource *per role*, so a file on "
            "both the edit and the picture lane needs it twice or it renders "
            "cropped on one track and letterboxed on the other"
        )

    declared = declared_frames(root)
    wrong = {where: frames for where, frames in declared.items() if frames != total_frames}
    if wrong:
        raise MLTError(
            f"the document just written declares {wrong} where the timeline is "
            f"{total_frames} frames — melt renders to the longest declared "
            "length, so this would have padded the render out with a frozen "
            "frame and exited 0"
        )
    return root


def declared_frames(root: ET.Element) -> dict[str, int]:
    """Every place this document states how long the timeline is, as a count.

    The sweep melt's behaviour makes necessary: it renders to the longest of
    these, not to the playlist, so they have to agree and something has to
    check that they do. Read back off the built tree rather than tracked while
    building it — a value that was correct in a variable and wrong in the
    attribute is exactly the bug this is for.

    Frame *counts*, converted from whatever each spot declares: a tractor's
    `out` is the last frame index, the background producer's `length` is
    already a count. Producer lengths other than the background's are not
    timeline lengths at all — a still image claims four hours — and are left
    out on purpose.
    """
    found: dict[str, int] = {}
    for producer in root.findall("producer"):
        if producer.get("id") != "producer0":
            continue
        length = producer.find("property[@name='length']")
        if length is not None and (length.text or "").strip().isdigit():
            found["producer0 length"] = int(length.text.strip())
    for tractor in root.findall("tractor"):
        out = tractor.get("out", "")
        if out.isdigit():
            found[f"tractor {tractor.get('id')} out"] = int(out) + 1
        for track in tractor.findall("track"):
            track_out = track.get("out", "")
            if track_out.isdigit():
                found[f"tractor {tractor.get('id')} track {track.get('producer')} out"] = (
                    int(track_out) + 1
                )
    return found


def to_string(root: ET.Element) -> str:
    """The document as text, indented — a generated file still gets read."""
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def write(root: ET.Element, output: Path | str) -> Path:
    """Write the document, and say where it landed.

    Nothing here checks that `output` is somewhere melt can reach it — the
    flatpak cannot see `/tmp` and exits 0 having read nothing (CLAUDE.md) —
    because the caller picks the path and `picture.project_frames` already
    explains that failure when it happens.
    """
    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(to_string(root), encoding="utf-8")
    return destination


# -- reading one back ------------------------------------------------------
#
# The module's rule is "generated, never mutated", and this does not bend it:
# nothing below writes, patches or round-trips a document. It reads an
# *outside* cut — a `.kdenlive` playlist somebody trimmed in Kdenlive — into
# the ranges `ops.import_edit` turns into an `Edit`. PLAN.md § Open questions,
# *How does a lucid project know it is the film*, names the gap it closes: the
# Scream retake pass was done in Kdenlive and its 63 ranges were parsed by hand
# and written straight to `Edit`, bypassing `cut` and its history entirely.
#
# It lives here rather than in `autoeditor.py` because what makes it hard is
# MLT's semantics, not auto-editor's, and those are already this module's to
# own: a frame-inclusive `out`, a rate that comes from `<profile>` rather than
# from any entry, and a `<blank>` that is real timeline runtime.


@dataclass(frozen=True)
class ImportedRange:
    """One surviving source interval read out of a playlist.

    `resource` is the producer's own `resource` string, untouched — resolving
    it to a file, and a file to a registered `clip_id`, is the caller's job
    and needs the project. `start`/`end` are **source seconds, half-open**,
    already converted out of MLT's frame-inclusive `out` so that nothing
    downstream has to remember to.
    """

    resource: str
    start: float
    end: float
    #: an entry of a `silence` producer: runtime with no file behind it, which
    #: `resource` is then empty for and the caller has to manufacture
    silence: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start


def profile_rate(root: ET.Element) -> float:
    """The document's frame rate, from `<profile>` and nowhere else.

    Every position in a playlist is a frame number on this clock, including
    the ones written as timecodes, so reading it wrong scales the whole
    import rather than shifting it.
    """
    profile = root.find("profile")
    if profile is None:
        raise MLTError("this document has no <profile>, so nothing says what a frame is")
    num = profile.get("frame_rate_num")
    den = profile.get("frame_rate_den") or "1"
    try:
        rate = float(num or "") / float(den)
    except (TypeError, ValueError) as exc:
        raise MLTError(f"unreadable profile frame rate {num!r}/{den!r}") from exc
    if rate <= 0:
        raise MLTError(f"profile frame rate is {rate}, which cannot be a clock")
    return rate


def _position(raw: str, rate: float) -> int:
    """A playlist position as a frame index, from either spelling.

    MLT accepts a bare frame number *and* an `HH:MM:SS.mmm` timecode in the
    same attribute, and which one a writer uses is its own business — Kdenlive
    writes frames, auto-editor 31.4.2 writes timecodes, and both are the same
    document format. Anything that only handled one would read the other as a
    zero and import a cut starting at the head of the file.
    """
    text = raw.strip()
    if not text:
        raise MLTError("a playlist entry has an empty position")
    if ":" not in text:
        try:
            return int(text)
        except ValueError as exc:
            raise MLTError(f"unreadable playlist position {raw!r}") from exc
    parts = text.split(":")
    if len(parts) != 3:
        raise MLTError(f"unreadable playlist timecode {raw!r}")
    try:
        hours, minutes, seconds = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError as exc:
        raise MLTError(f"unreadable playlist timecode {raw!r}") from exc
    return round((hours * 3600 + minutes * 60 + seconds) * rate)


def _resources(root: ET.Element) -> dict[str, str]:
    """Producer id → the file it names, for the producers that name one.

    Media arrives as `<chain>` in MLT 7 and as `<producer>` before it, and a
    document written by one Kdenlive can hold both — so both are read.
    Producers with no `resource`, and the `color` service that backs the black
    track, are left out: they are not footage and an entry referencing one is
    not a cut of anything.
    """
    found: dict[str, str] = {}
    for node in [*root.findall("chain"), *root.findall("producer")]:
        node_id = node.get("id")
        if not node_id:
            continue
        service = node.find("property[@name='mlt_service']")
        if service is not None and (service.text or "").strip() == "color":
            continue
        resource = node.find("property[@name='resource']")
        text = (resource.text or "").strip() if resource is not None else ""
        if text and text != "black":
            found[node_id] = text
    return found


def _silence_producers(root: ET.Element) -> set[str]:
    """The producer ids whose service is MLT's `silence` — no file, only runtime."""
    found: set[str] = set()
    for node in [*root.findall("chain"), *root.findall("producer")]:
        service = node.find("property[@name='mlt_service']")
        if node.get("id") and service is not None and (service.text or "").strip() == "silence":
            found.add(node.get("id"))
    return found


def declared_length(root: ET.Element, rate: float) -> dict[str, int]:
    """Every place an outside document states how long its own cut is.

    The reading-side twin of `declared_frames`, and separate from it because
    the two read different spellings: this module writes bare frame numbers,
    while Kdenlive writes `HH:MM:SS.mmm` into the same attributes. Folding
    them together would make the writer's own check quietly accept a timecode
    it should never see.

    It exists because it is the check that would have caught the hand-parse.
    A document that says 10151 frames three times, imported as 10088, is a
    disagreement nothing else would have reported — the ranges are all
    individually plausible and their sum is only wrong by one frame each.
    """
    found: dict[str, int] = {}
    for producer in root.findall("producer"):
        length = producer.find("property[@name='length']")
        if producer.get("id") == "producer0" and length is not None and length.text:
            found["producer0 length"] = _position(length.text, rate)
    for tractor in root.findall("tractor"):
        out = tractor.get("out")
        if not out:
            continue
        frames = _position(out, rate)
        # A tractor declaring nothing is the empty sequence wrapper every
        # Kdenlive document carries; it is not a claim about the cut.
        if frames > 0:
            found[f"tractor {tractor.get('id')} out"] = frames
    return found


def read_ranges(root: ET.Element) -> tuple[list[ImportedRange], float]:
    """Every surviving range in the document's cut, plus the profile rate.

    **`out` is the last frame *index*, inclusive**, so a range's exclusive end
    is `out + 1` — settled by measurement rather than by reading MLT's docs:
    auto-editor's `--export v3` and `--export kdenlive` of the same cut give
    `dur` and `(in, out)` for the same three segments, and `out - in + 1`
    equals `dur` on all three. That is also the arithmetic behind the
    off-by-one CLAUDE.md warns about from the writing side, met here from the
    reading side.

    **Which playlist is the cut** is not guessed. A cut-and-concat timeline
    writes the same intervals onto every track it uses — Kdenlive and
    auto-editor both emit an audio playlist and a video one carrying identical
    entries — so every playlist that holds entries is read and they must
    agree. Two playlists that disagree are a multi-track picture edit, which
    is a different and unbuilt thing, and it is refused by name rather than
    resolved by preferring a track: preferring one would import half of
    somebody's edit and report success.

    A `<blank>` is refused for the same reason it is never written (see this
    module's own rule at the top): it is real runtime on the timeline, and
    `Edit` has nowhere to put it — segments are laid contiguously and the hole
    would close silently, making the import a different film from the file it
    was read out of.
    """
    rate = profile_rate(root)
    resources = _resources(root)
    silences = _silence_producers(root)

    candidates: list[tuple[str, list[ImportedRange]]] = []
    for playlist in root.findall("playlist"):
        playlist_id = playlist.get("id") or "?"
        if playlist_id == "main_bin":
            # The bin is the project's media list, not a placement of it —
            # the same exclusion `reframed_nodes` makes for `xml_retain`.
            continue
        ranges: list[ImportedRange] = []
        for child in playlist:
            if child.tag == "blank":
                raise MLTError(
                    f"playlist {playlist_id!r} holds a <blank> of "
                    f"{child.get('length', '?')} — that is runtime with nothing under "
                    "it, and an Edit lays its segments contiguously, so importing it "
                    "would silently close the hole and shorten the cut"
                )
            if child.tag != "entry":
                continue
            producer = child.get("producer") or ""
            if producer in silences:
                # Runtime the cut plays, exactly as a `<blank>` is — Kdenlive
                # pads a VO timeline with these, and skipping one as "not
                # media" closes it silently (HISTORY.md § The Scream native
                # rebuild). Its in-point means nothing, so only its length is kept.
                frames = _position(child.get("out") or "", rate) - _position(child.get("in") or "0", rate) + 1
                ranges.append(ImportedRange(resource="", start=0.0, end=frames / rate, silence=True))
                continue
            if producer not in resources:
                continue
            start = _position(child.get("in") or "0", rate)
            out = _position(child.get("out") or "", rate)
            ranges.append(
                ImportedRange(
                    resource=resources[producer],
                    start=start / rate,
                    end=(out + 1) / rate,
                )
            )
        if ranges:
            candidates.append((playlist_id, ranges))

    if not candidates:
        raise MLTError(
            "no playlist in this document holds an entry referencing media — "
            "there is no cut here to import"
        )

    first_id, first = candidates[0]
    for other_id, other in candidates[1:]:
        if other != first:
            raise MLTError(
                f"playlists {first_id!r} and {other_id!r} carry different cuts "
                f"({len(first)} ranges against {len(other)}) — proofcut's timeline is one "
                "track with A/V linked, so a multi-track edit has no shape to import "
                "into and is refused rather than half-read"
            )
    return first, rate
