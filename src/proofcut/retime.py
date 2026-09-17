"""The retime warp: which moment of the `Edit` plays at each render frame.

A retime is a list of stretches, each "this span of the Edit plays in this many
seconds" (docs/plans/NATIVE.md § B5, designed). Everywhere else plays at 1x.
The warp is the head's constant offset generalised to a curve: a monotone map
from render time to Edit time, PCHIP through the knots the stretches make,
which is `clip.py`'s own curve.

Three facts from the spike (`~/proofcut-work/spikes/retime-compose/`) shape
this module:

* **MLT's `~` spline runs a launch-clip map backwards**, so the curve is
  computed here and sampled once per render frame. The writer only ever emits
  linear keys between those samples.
* **A remapped chain's positions are output frames**, so everything keyed on
  one — a reframe `rect`, a `volume` level — is keyed in render frames.
* **One remapped chain per entry is exact**, so each lane entry gets the slice
  of the warp covering it, keyed from 0.

Pure: no project, no subprocess. `ops` resolves stretches from addresses and
hands them here.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from proofcut.mlt import Entry

#: How far a speed change reaches into the 1x time beside a stretch, in Edit
#: seconds. PCHIP bends every piece whose neighbours have a different slope, so
#: without a knot here a 60 s gap next to a 30x stretch plays off speed for all
#: 60 s. A knot this far out keeps the ramp inside half a second and the rest
#: of the gap at exactly 1x. `clip.py` did the same by hand, with 1x pieces of
#: 0.3–0.5 s around every change.
RAMP_SECONDS = 0.5

#: A frame playing further from 1x than this has its Edit audio muted: the
#: link would pitch it by the speed ratio (measured 6.00x at 6x).
MUTE_TOLERANCE = 0.05

#: The fade in and out of a muted span, in seconds.
MUTE_FADE_SECONDS = 0.04

#: Where a source time is read inside its frame. MLT truncates a key's seconds
#: to a frame, so an exact `k / rate` written to six decimals can land on k−1
#: and play one frame twice (the link-alone spike's identity control did).
#: A quarter frame in is inside the right frame whether melt floors or rounds.
FRAME_BIAS = 0.25

#: A key the map passes within this many frames of is not written.
SIMPLIFY_FRAMES = 0.05


class RetimeError(Exception):
    """A retime that cannot be drawn: overlapping, empty or backwards."""


@dataclass(frozen=True)
class Stretch:
    """`start`..`end` of the Edit, in Edit seconds, played in `seconds`."""

    start: float
    end: float
    seconds: float


def _pchip(xs: list[float], ys: list[float]):
    """Monotone cubic through the knots — `clip.py`'s `pchip`, without numpy."""
    h = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(len(h))]
    m = [0.0] * len(xs)
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(xs) - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def f(x: float) -> float:
        i = min(max(bisect.bisect_right(xs, x) - 1, 0), len(xs) - 2)
        t = min(max((x - xs[i]) / h[i], 0.0), 1.0)
        h00, h10 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t
        h01, h11 = -2 * t**3 + 3 * t**2, t**3 - t**2
        return h00 * ys[i] + h10 * h[i] * m[i] + h01 * ys[i + 1] + h11 * h[i] * m[i + 1]

    return f


class Warp:
    """Render time → Edit time over an Edit of `edit_frames` frames at `rate`.

    `frames` is the render's length for the Edit's span. `edit_at_frame` is the
    Edit second render frame k shows; `render_at` and `frame_of` go the other
    way, for placing anything addressed in Edit time.
    """

    def __init__(self, stretches: list[Stretch], edit_frames: int, rate: float) -> None:
        if not stretches:
            raise RetimeError("a warp needs at least one stretch")
        self.rate = rate
        self.edit_frames = edit_frames
        duration = edit_frames / rate
        ordered = sorted(stretches, key=lambda s: s.start)
        previous_end = 0.0
        for stretch in ordered:
            if stretch.seconds <= 0:
                raise RetimeError(f"a stretch plays in {stretch.seconds}s — a length is positive")
            if stretch.end <= stretch.start:
                raise RetimeError(
                    f"a stretch runs from Edit {stretch.start:.3f}s to {stretch.end:.3f}s — "
                    "it has to end after it starts"
                )
            if stretch.start < previous_end - 1e-9:
                raise RetimeError(
                    f"two stretches overlap at Edit {stretch.start:.3f}s — one span of the "
                    "film can play at one speed"
                )
            if stretch.end > duration + 1e-9:
                raise RetimeError(
                    f"a stretch ends at Edit {stretch.end:.3f}s, past the Edit's {duration:.3f}s"
                )
            previous_end = stretch.end
        self.stretches = ordered

        knots: list[tuple[float, float]] = [(0.0, 0.0)]

        def one_x(to: float) -> None:
            """1x from the last knot to Edit `to`, with a shoulder knot at each end."""
            render, edit = knots[-1]
            gap = to - edit
            if gap <= 1e-9:
                return
            if gap > 2 * RAMP_SECONDS:
                for at in (edit + RAMP_SECONDS, to - RAMP_SECONDS):
                    knots.append((render + (at - edit), at))
            knots.append((render + gap, to))

        for stretch in ordered:
            one_x(stretch.start)
            render, _ = knots[-1]
            knots.append((render + stretch.seconds, stretch.end))
        one_x(duration)
        self.knots = knots
        self.duration = knots[-1][0]
        self.frames = max(1, round(self.duration * rate))
        curve = _pchip([k[0] for k in knots], [k[1] for k in knots])
        # One sample per frame boundary: the last one is the Edit's end exactly.
        self._edit = [curve(k / rate) for k in range(self.frames)] + [duration]
        backwards = [k for k in range(self.frames) if self._edit[k + 1] < self._edit[k] - 1e-9]
        if backwards:
            raise RetimeError(
                f"the retime runs the Edit backwards near render {backwards[0] / rate:.3f}s"
            )

    def edit_at_frame(self, k: int) -> float:
        """The Edit second render frame `k` starts on."""
        return self._edit[min(max(k, 0), self.frames)]

    def edit_at(self, render_seconds: float) -> float:
        """The Edit second showing at `render_seconds` of the render."""
        position = min(max(render_seconds * self.rate, 0.0), float(self.frames))
        k = min(int(position), self.frames - 1)
        lo, hi = self._edit[k], self._edit[k + 1]
        return lo + (hi - lo) * (position - k)

    def speed_at_frame(self, k: int) -> float:
        return (self.edit_at_frame(k + 1) - self.edit_at_frame(k)) * self.rate

    def render_at(self, edit_seconds: float) -> float:
        """The render second that shows `edit_seconds` of the Edit."""
        samples = self._edit
        if edit_seconds <= samples[0]:
            return 0.0
        if edit_seconds >= samples[-1]:
            return self.frames / self.rate
        k = bisect.bisect_right(samples, edit_seconds) - 1
        lo, hi = samples[k], samples[k + 1]
        part = 0.0 if hi <= lo else (edit_seconds - lo) / (hi - lo)
        return (k + part) / self.rate

    def frame_of(self, edit_seconds: float) -> int:
        """The render frame an Edit instant lands on, on the render's grid."""
        return min(round(self.render_at(edit_seconds) * self.rate), self.frames)

    def frame_of_edit_frame(self, edit_frame: int) -> int:
        """`frame_of` for an Edit frame boundary, exact at both ends."""
        if edit_frame <= 0:
            return 0
        if edit_frame >= self.edit_frames:
            return self.frames
        return self.frame_of(edit_frame / self.rate)

    def muted(self) -> list[bool]:
        """Per render frame: is this frame off 1x by more than the tolerance?

        A ramp's speed crosses 1x on its way through, so a run of unmuted
        frames between muted ones too short to fade up and back down in is
        muted too (`_bridged`), or the audio would blip up inside the ramp.
        """
        muted = [abs(self.speed_at_frame(k) - 1) > MUTE_TOLERANCE for k in range(self.frames)]
        return _bridged(muted, _fade_frames(self.rate))

    def muted_edit_spans(self) -> list[tuple[float, float]]:
        """The Edit spans whose own audio is muted, for `verify` and captions."""
        spans: list[tuple[float, float]] = []
        muted = self.muted()
        k = 0
        while k < self.frames:
            if not muted[k]:
                k += 1
                continue
            start = k
            while k < self.frames and muted[k]:
                k += 1
            spans.append((self.edit_at_frame(start), self.edit_at_frame(k)))
        return spans

    def report(self) -> list[dict[str, float]]:
        """Each stretch as the render draws it: its Edit span, its render span, its speed."""
        rows = []
        for stretch in self.stretches:
            render_start = self.render_at(stretch.start)
            render_end = self.render_at(stretch.end)
            rows.append(
                {
                    "edit_start": round(stretch.start, 3),
                    "edit_end": round(stretch.end, 3),
                    "render_start": round(render_start, 3),
                    "render_end": round(render_end, 3),
                    "speed": round((stretch.end - stretch.start) / stretch.seconds, 3),
                }
            )
        return rows


def _fade_frames(rate: float) -> int:
    return max(1, round(MUTE_FADE_SECONDS * rate))


def _bridged(muted: list[bool], fade: int) -> list[bool]:
    """Mute every unmuted run between two muted ones shorter than a fade up
    and a fade down need. Shorter, the two ramps' keys land on each other and
    the floor between them is lost: MLT then drew one line from −60 dB up to
    0 across a whole muted span, measured at 2420 of the tone's 2900."""
    bridged = list(muted)
    room = 2 * fade + 2
    k = 0
    while k < len(bridged):
        if bridged[k]:
            k += 1
            continue
        start = k
        while k < len(bridged) and not bridged[k]:
            k += 1
        if 0 < start and k < len(bridged) and k - start < room:
            bridged[start:k] = [True] * (k - start)
    return bridged


def _mute_keys(muted: list[bool], rate: float) -> tuple[tuple[int, float], ...]:
    """A gain envelope in dB, entry-relative, that silences the muted frames.

    The fades sit on the 1x side of each join, so no off-speed frame is heard.
    """
    from proofcut.mlt import FADE_FLOOR_DB

    if not any(muted):
        return ()
    fade = _fade_frames(rate)
    muted = _bridged(muted, fade)
    level = [FADE_FLOOR_DB if m else 0.0 for m in muted]
    keys: list[tuple[int, float]] = [(0, level[0])]
    for k in range(1, len(level)):
        if level[k] == level[k - 1]:
            continue
        if muted[k]:  # into a muted span: down by the frame before k
            # MLT ramps a level across the frame that carries its key, so a
            # key on k leaves frame k at half level (measured, 0.036 of 0.063).
            keys += [(max(k - fade - 1, 0), 0.0), (max(k - 1, 0), FADE_FLOOR_DB)]
        else:  # out of one: up from frame k
            keys += [(k, FADE_FLOOR_DB), (min(k + fade, len(level) - 1), 0.0)]
    keys.append((len(level) - 1, level[-1]))
    clean: list[tuple[int, float]] = []
    for position, value in sorted(keys, key=lambda key: key[0]):
        if clean and position <= clean[-1][0]:
            # Only an edge key can meet another now (bridging leaves every
            # interior run room for both ramps); the quieter level wins.
            clean[-1] = (clean[-1][0], min(clean[-1][1], value))
            continue
        clean.append((position, value))
    return tuple(clean)


def _simplify(points: list[tuple[int, float]], tolerance: float) -> list[tuple[int, float]]:
    """Drop every key the line between its kept neighbours passes within `tolerance` of."""
    if len(points) <= 2:
        return points
    kept = [points[0]]
    anchor = 0
    for i in range(1, len(points) - 1):
        a_pos, a_val = points[anchor]
        n_pos, n_val = points[i + 1]
        # Would a line from the anchor to the next point still pass every point between?
        ok = True
        for j in range(anchor + 1, i + 1):
            pos, val = points[j]
            line = a_val + (n_val - a_val) * (pos - a_pos) / (n_pos - a_pos)
            if abs(line - val) > tolerance:
                ok = False
                break
        if not ok:
            kept.append(points[i])
            anchor = i
    kept.append(points[-1])
    return kept


def warp_lane(
    entries: list[Entry], warp: Warp, *, mute: bool
) -> tuple[list[Entry], list[tuple[int, int, int]]]:
    """A lane laid out in Edit frames, laid out in render frames.

    Each media entry becomes a remapped one: `src_in` 0, `frames` its render
    span, and `time_map` the source second every frame reads. A still only
    changes length. An entry the warp squeezes to no frames is dropped and
    named, with its Edit frame span and index, so nothing disappears silently.
    `mute` silences the frames played off 1x (the Edit track's own audio).
    """
    rate = warp.rate
    muted = warp.muted() if mute else []
    out: list[Entry] = []
    dropped: list[tuple[int, int, int]] = []
    cursor = 0
    for index, entry in enumerate(entries):
        start, end = cursor, cursor + entry.frames
        cursor = end
        o0, o1 = warp.frame_of_edit_frame(start), warp.frame_of_edit_frame(end)
        if o1 <= o0:
            dropped.append((index, start, end))
            continue
        span = o1 - o0
        if entry.is_image:
            out.append(replace(entry, frames=span))
            continue
        first = entry.src_in / rate
        last = (entry.src_in + entry.frames - 1 + FRAME_BIAS) / rate
        points = []
        # One key past the entry's last frame: a map ending on that frame plays
        # it silent, since the link reads the frame's audio up to the next key
        # (measured: an identity map ending on 59 lost frames 59 and 119).
        for k in range(o0, o1 + 1):
            source = first + (warp.edit_at_frame(k) - start / rate) + FRAME_BIAS / rate
            ceiling = last if k < o1 else last + 1 / rate
            points.append((k - o0, round(min(max(source, first), ceiling), 6)))
        time_map = tuple(_simplify(points, SIMPLIFY_FRAMES / rate))
        gain = _mute_keys(muted[o0:o1], rate) if mute else ()
        if gain and entry.gain_keys:
            raise RetimeError("an entry with its own gain envelope cannot also be muted by a retime")
        out.append(
            replace(
                entry,
                src_in=0,
                frames=span,
                time_map=time_map,
                gain_keys=gain or entry.gain_keys,
            )
        )
    if cursor != warp.edit_frames:
        raise RetimeError(
            f"the lane covers {cursor} Edit frames but the warp was built for {warp.edit_frames}"
        )
    return out, dropped
