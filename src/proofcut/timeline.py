"""The timeline, and the track surgery that mutates it.

OTIO is the on-disk source of truth (`project.otio`), but it is a poor working
representation for cutting: its edit algorithms — `overwrite`, `ripple`,
`trim`, `slice` — are C++ only and have no Python bindings, so `algorithms`
gives us trimming, flattening and transition expansion and nothing else (see
CLAUDE.md). Everything here is therefore hand-rolled over a flat list of
segments, and OTIO is used for serialisation and NLE interchange.

The model is deliberately narrow, and the narrowness is the point for now:

* One track. A/V are **linked** — a clip carries both streams, the way a NLE
  treats a linked pair — so there is no way to cut picture without sound yet.
* Cut-and-concat only. Removing an interval ripples: the hole closes. There
  are no gaps, no overlaps, no transitions and no speed changes.

Both restrictions match what the auto-editor v3 round-trip can express, and
laying clips and graphics *over* a VO is currently Kdenlive's job. Widening
this is a real change, not a config flag — see PLAN.md.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import opentimelineio as otio

#: Segments shorter than this are dropped rather than emitted. A one-sample
#: sliver is never a real edit; it is a rounding artifact from a cut boundary
#: landing on top of an existing one.
MIN_SEGMENT = 0.001

#: An overlap this small is float noise, never material. A saved timeline's
#: boundaries are grid values (`to_otio` rounds to the timebase) and a
#: transcript's are whatever whisper's arithmetic left, so a cut made from a
#: word's own start reads back overlapping that word by ~1e-16 s — and a word
#: overlapping by anything was reported present, drawn unstruck and captioned
#: as a zero-length duplicate. Every overlap test in this module is `> EPSILON`
#: rather than `> 0`. Far below `MIN_SEGMENT` on purpose: a zero-width word
#: widened to `captions.MIN_WORD` must still count.
EPSILON = 1e-9


class TimelineError(Exception):
    """Raised when an edit operation cannot be applied."""


@dataclass(frozen=True)
class Segment:
    """A half-open source interval `[start, end)` of one registered clip."""

    clip_id: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
        }


@dataclass(frozen=True)
class Placement:
    """Where one piece of a source interval currently plays on the timeline.

    Carries both coordinate systems because the answer to "where does this
    play" is only checkable against the question: `source_start`/`source_end`
    say which part of the interval this piece is, which is what makes a
    partially-cut range readable rather than just short.
    """

    timeline_start: float
    timeline_end: float
    source_start: float
    source_end: float

    @property
    def duration(self) -> float:
        return self.timeline_end - self.timeline_start

    def contiguous_with(self, other: Placement) -> bool:
        """Does `other` start exactly where this piece ends, on the timeline?

        True across a cut seam — the hole closed, so the two play back-to-back
        — and false across an intervening segment of other material.
        """
        return abs(other.timeline_start - self.timeline_end) <= MIN_SEGMENT

    def as_dict(self) -> dict[str, Any]:
        return {
            "timeline_start": self.timeline_start,
            "timeline_end": self.timeline_end,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "duration": self.duration,
        }


class _SpanIndex:
    """One snapshot of an `Edit`, arranged for source->timeline lookup.

    The addressing methods below all walk every segment accumulating a timeline
    offset, which is O(segments) per call and therefore O(words x segments)
    across a caption pass — the shape the October scale spike projected and did
    not run (HISTORY.md § The scale spike, half-run). Measured here rather than
    reasoned about: the projection's own case, `build_shots` at 400 cues over
    2000 segments, costs 25 ms and is not a groan point at all. The word loops
    are, because their product is far larger — `captions.place` over 6000 words
    of a 2000-segment edit is 0.39 s, and a silence-cut hour (10000 words,
    6000 segments) is 2.03 s, on every mutation the web UI makes.

    So this precomputes, per clip, that clip's own segment bounds and the
    timeline offset each one starts at. Two things follow. Restricting the walk
    to one clip's segments is exact and needs no assumption. Replacing the walk
    with a bisect needs one, and it is **not** that the segments are sorted: two
    segments of the same clip may overlap in source (the same footage placed
    twice, which `import_edit` can produce from a `.kdenlive`), and then an
    earlier, longer segment is the first overlapper while a bisect on starts
    walks straight past it. The precondition is sorted *and disjoint*, which is
    what `ordered` records per clip — every operation this codebase ships
    produces it, and a clip that does not get the exact linear walk instead.
    `tests/test_timeline.py` holds the two paths to each other over random
    edits, overlapping ones included.
    """

    __slots__ = ("clips",)

    def __init__(self, segments: list[Segment]) -> None:
        # clip_id -> (starts, ends, offsets, ordered)
        gathered: dict[str, tuple[list[float], list[float], list[float]]] = {}
        offset = 0.0
        for seg in segments:
            starts, ends, offsets = gathered.setdefault(seg.clip_id, ([], [], []))
            starts.append(seg.start)
            ends.append(seg.end)
            offsets.append(offset)
            offset += seg.duration
        self.clips: dict[str, tuple[list[float], list[float], list[float], bool]] = {
            clip_id: (
                starts,
                ends,
                offsets,
                all(starts[i] >= ends[i - 1] for i in range(1, len(starts))),
            )
            for clip_id, (starts, ends, offsets) in gathered.items()
        }

    def first_overlapping(self, clip_id: str, start: float, end: float) -> int | None:
        """Index, within `clip_id`'s own arrays, of the first segment meeting
        `[start, end)` — the position the linear walk would have stopped at."""
        entry = self.clips.get(clip_id)
        if entry is None:
            return None
        starts, ends, _, ordered = entry
        if ordered:
            # `ends` rises strictly under the precondition, so this is the
            # first segment that has not already finished by `start`. If it
            # begins at or after `end`, so does every segment behind it.
            # Bisecting on `start + EPSILON` skips a segment that only grazes
            # it, so the next one — which may really overlap — is the one tested.
            j = bisect.bisect_right(ends, start + EPSILON)
            if j < len(starts) and min(ends[j], end) - max(starts[j], start) > EPSILON:
                return j
            return None
        for j in range(len(starts)):
            if min(ends[j], end) - max(starts[j], start) > EPSILON:
                return j
        return None


@dataclass
class Edit:
    """An ordered list of source segments — the whole timeline state.

    Assigning `segments` drops the cached `_SpanIndex`, via `__setattr__`
    rather than a property so the dataclass field stays a field: an index left
    standing over a changed timeline would answer every lookup confidently and
    wrongly, which is this repo's worst failure shape. Every mutator below
    therefore rebinds `self.segments` rather than mutating the list in place,
    and a caller holding the list it passed to `Edit(...)` and mutating that is
    the one way round the guard — so don't.
    """

    segments: list[Segment]

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "segments":
            object.__setattr__(self, "_index", None)
        object.__setattr__(self, name, value)

    def _span_index(self) -> _SpanIndex:
        index = getattr(self, "_index", None)
        if index is None:
            index = _SpanIndex(self.segments)
            object.__setattr__(self, "_index", index)
        return index

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration": self.duration,
            "segments": [s.as_dict() for s in self.segments],
        }

    # -- addressing ------------------------------------------------------

    def timeline_time(
        self, clip_id: str, source_time: float, *, closed_end: bool = False
    ) -> float | None:
        """Where a source instant currently sits on the timeline.

        Returns None when that instant has been cut — which is the honest
        answer, and the reason cuts are reported rather than silently skipped.

        Segments are half-open `[start, end)`, which is right for intervals and
        wrong for exactly one caller: a **zero-width word**. Whisper sometimes
        emits `start == end`, and when that instant lands on a segment's closing
        boundary — the last word of a transcript against the end of the last
        segment is the ordinary case — the half-open test says "cut" about
        material that is plainly still there. `closed_end=True` accepts
        `source_time == seg.end` as inside that segment, and is for instants
        only: passing it for one edge of a range would double-count the join
        between two segments. The default is unchanged, so every other caller
        keeps the half-open convention it was written against.

        Reads `_SpanIndex`'s per-clip arrays, but walks them exactly rather
        than bisecting: a zero-width instant sitting on a closing boundary is
        the one lookup whose answer does not follow from an overlap test, and
        it is precisely the case `closed_end` exists for.
        """
        entry = self._span_index().clips.get(clip_id)
        if entry is None:
            return None
        starts, ends, offsets, _ = entry
        for j, (a, b) in enumerate(zip(starts, ends, strict=True)):
            if a <= source_time < b or (closed_end and source_time == b):
                return offsets[j] + min(source_time - a, b - a)
        return None

    def timeline_span(self, clip_id: str, start: float, end: float) -> tuple[float, float] | None:
        """Where a source *interval* currently sits on the timeline.

        Returns the part of `[start, end)` that survives in the first segment
        overlapping it, mapped to timeline time — or None when the whole
        interval has been cut. An interval straddling a cut comes back
        truncated rather than stretched across material that is gone, which
        matters for captions: a word half-removed by a cut should show for the
        half that is still audible, not for its original length.

        The hot one: called once per word by `captions.place` and once per cue
        by `build_shots`, both of which run on every editing mutation. It is a
        bisect through `_SpanIndex` where that index is exact and the same
        first-overlapper walk otherwise — see `_SpanIndex` for which, and for
        the measurements that put the cost on this method rather than on the
        one the scale spike named.
        """
        entry = self._span_index().clips.get(clip_id)
        if entry is None:
            return None
        j = self._span_index().first_overlapping(clip_id, start, end)
        if j is None:
            return None
        starts, ends, offsets, _ = entry
        a, b = max(starts[j], start), min(ends[j], end)
        return offsets[j] + (a - starts[j]), offsets[j] + (b - starts[j])

    def timeline_spans(self, clip_id: str, start: float, end: float) -> list[Placement]:
        """Every timeline interval a source interval now plays at, in order.

        The aggregate inverse of `source_spans`, and the aggregate form of
        `timeline_span` — which deliberately stops at the first survivor
        because captions want one span per word, not a list. This one walks
        every segment, so a range a prior cut split comes back as >=2 pieces
        and a range fully cut comes back empty.

        The pieces are *not* merged even when their timeline coordinates touch.
        Two adjacent pieces mean the material is continuous to a listener but
        has a cut seam inside it, and those are different facts: merging would
        report the seam as absent. `Placement.contiguous_with` is how a caller
        that only cares about playback re-joins them.

        Every piece is wanted, so there is no bisect to do here — but the walk
        is over this clip's own segments rather than the whole timeline, which
        is what `_SpanIndex` buys a multi-clip project.
        """
        entry = self._span_index().clips.get(clip_id)
        if entry is None:
            return []
        starts, ends, offsets, _ = entry
        placements: list[Placement] = []
        for j, (seg_start, seg_end) in enumerate(zip(starts, ends, strict=True)):
            a, b = max(seg_start, start), min(seg_end, end)
            if b - a > EPSILON:
                placements.append(
                    Placement(
                        timeline_start=offsets[j] + (a - seg_start),
                        timeline_end=offsets[j] + (b - seg_start),
                        source_start=a,
                        source_end=b,
                    )
                )
        return placements

    def source_at(self, time: float) -> tuple[str, float] | None:
        """The (clip_id, source_time) playing at timeline instant `time`.

        The single-instant counterpart to `source_spans`: same timeline-time
        walk, but for one point rather than a range. The two disagree on
        purpose about what happens past the end — `source_spans` raises,
        because a *requested range* naming material that is not on the
        timeline at all is almost certainly a mistake worth stopping on. This
        instead returns None, matching `timeline_time`'s own policy for a cut
        source instant: a single sampled point (as `spot_frames` produces one
        per frame, from evenly-spaced arithmetic that can round to the exact
        duration) is routine, not a caller error, so the honest answer is
        reported rather than raised.
        """
        offset = 0.0
        for seg in self.segments:
            if offset <= time < offset + seg.duration:
                return seg.clip_id, seg.start + (time - offset)
            offset += seg.duration
        if self.segments and time == offset:
            last = self.segments[-1]
            return last.clip_id, last.end
        return None

    def covers(self, clip_id: str, start: float, end: float) -> float:
        """How much of a source interval is still present, in seconds."""
        total = 0.0
        for seg in self.segments:
            if seg.clip_id != clip_id:
                continue
            overlap = min(seg.end, end) - max(seg.start, start)
            if overlap > EPSILON:
                total += overlap
        return total

    def source_spans(self, start: float, end: float) -> list[tuple[str, float, float]]:
        """Where a timeline (render) interval currently maps back to source.

        The aggregate inverse of `timeline_span`: that walks source->timeline
        for one clip and returns the single truncated survivor, this walks
        timeline->source across however many segments (and, in a future
        multi-clip timeline, clips) `[start, end)` touches, in playback order.

        `[start, end)` is timeline time, half-open, like every interval in
        this module. Past-the-end is refused rather than clamped — unlike
        `pad`'s deliberate overreach, it names material that is not on the
        timeline at all.

        Segments are laid contiguously in timeline coordinates (only source
        coordinates have gaps), so a valid `[start, end)` inside
        `[0, duration)` can never come back with zero pieces. A range crossing
        a prior cut comes back as >=2 pieces of the same clip_id, now
        non-adjacent in source time; a range crossing a clip boundary comes
        back with a different clip_id per piece.
        """
        if end <= start:
            raise TimelineError(f"interval {start:.3f}-{end:.3f} is empty or backwards")
        if start < -1e-6 or end > self.duration + 1e-6:
            raise TimelineError(
                f"interval {start:.3f}-{end:.3f} is outside the timeline "
                f"(0.000-{self.duration:.3f}) — it names material that is not "
                "on the timeline at all"
            )

        pieces: list[tuple[str, float, float]] = []
        offset = 0.0
        for seg in self.segments:
            lo, hi = max(offset, start), min(offset + seg.duration, end)
            if hi > lo:
                pieces.append((seg.clip_id, seg.start + (lo - offset), seg.start + (hi - offset)))
            offset += seg.duration
        return pieces

    def gaps(self, clip_id: str, duration: float) -> list[tuple[float, float]]:
        """The source ranges of `clip_id` that are NOT on the timeline.

        `Edit` stores only survivors (see the module docstring), so "what was
        removed" is derived rather than read: the complement of the union of
        this clip's segments against `[0, duration)`, where `duration` is the
        clip's own registered length (an ffprobe value fixed at import,
        `media.py`, so it is a hard, reliable outer bound). This answers a
        head/tail drop and an interior cut in one uniform pass — a
        `keep_only` call that dropped the very start or end of a clip is not
        a special case, just another region the surviving segments don't
        cover. `restore` is this method's reason to exist.
        """
        present = _merge(sorted((s.start, s.end) for s in self.segments if s.clip_id == clip_id))
        out: list[tuple[float, float]] = []
        cursor = 0.0
        for lo, hi in present:
            if lo > cursor:
                out.append((cursor, lo))
            cursor = max(cursor, hi)
        if duration > cursor:
            out.append((cursor, duration))
        return out

    # -- mutation --------------------------------------------------------

    def remove(self, clip_id: str, start: float, end: float) -> int:
        """Ripple-delete a source interval. Returns the segments it touched."""
        if end <= start:
            raise TimelineError(f"interval {start:.3f}-{end:.3f} is empty or backwards")

        touched = 0
        out: list[Segment] = []
        for seg in self.segments:
            if seg.clip_id != clip_id or seg.end <= start or seg.start >= end:
                out.append(seg)
                continue
            touched += 1
            out.extend(_subtract(seg, start, end))
        self.segments = [s for s in out if s.duration >= MIN_SEGMENT]
        return touched

    def keep_only(self, clip_id: str, intervals: Iterable[tuple[float, float]]) -> None:
        """Keep only these source intervals of `clip_id`; drop the rest of it.

        Other clips are left alone, so this is "keep these bits of the VO",
        not "throw away everything else on the timeline".
        """
        wanted = _merge(sorted((float(a), float(b)) for a, b in intervals))
        if not wanted:
            raise TimelineError("keep_only needs at least one interval")

        out: list[Segment] = []
        for seg in self.segments:
            if seg.clip_id != clip_id:
                out.append(seg)
                continue
            for lo, hi in wanted:
                a, b = max(seg.start, lo), min(seg.end, hi)
                if b - a >= MIN_SEGMENT:
                    out.append(replace(seg, start=a, end=b))
        self.segments = out

    def restore(
        self, clip_id: str, start: float, end: float, *, duration: float
    ) -> list[tuple[float, float]]:
        """Bring back whichever part of `[start, end)` is currently a gap.

        The inverse of `remove`, bounded by `gaps()`: only source time this
        clip's own recording actually has (`duration`, its registered
        length) and that is not already on the timeline comes back, so the
        timeline stays a subset of the source throughout — the same
        invariant `remove`/`keep_only` already uphold, not a new one. This
        is NOT `vo_extend` (PLAN.md parks that separately), which would
        splice in material the source never had; restore only ever walks the
        invariant backward.

        A request that only partially overlaps a gap restores just the
        overlap; a request spanning two gaps restores both, as separate
        pieces, each reported. A request already fully present returns `[]`
        — a no-op, not an error, mirroring `cut_by_transcript`'s
        `already_cut`.

        Restored pieces are spliced back among `clip_id`'s own segments,
        merging into a neighbour that now touches it exactly (so a closed
        gap does not leave two source-adjacent, timeline-adjacent segments
        of the same clip sitting next to each other — `_seams` would read
        that as a phantom zero-duration cut). This only knows where to
        splice when `clip_id`'s segments already form one contiguous run in
        `self.segments`: every operation this codebase ships today produces
        exactly that (a single clip_id at a time — `seed_timeline`,
        `autoeditor.silence_edit`), so this raises rather than guess a
        placement if that is ever untrue, e.g. because the clip has no
        surviving segment left to anchor against, or a future interleaved
        multi-source timeline put another clip's material between two of
        this clip's segments.
        """
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            return []

        own_positions = [i for i, s in enumerate(self.segments) if s.clip_id == clip_id]
        if not own_positions:
            raise TimelineError(
                f"clip {clip_id!r} has no surviving segment in this edit — restore has "
                "nothing of it left to splice the requested range next to, so there is "
                "no well-defined place to put it back (undo, or re-seed the clip, instead)"
            )
        lo0, hi0 = own_positions[0], own_positions[-1]
        if own_positions != list(range(lo0, hi0 + 1)):
            raise TimelineError(
                f"clip {clip_id!r}'s segments are not contiguous in this edit (another "
                "clip's material sits between them) — restore does not support an "
                "interleaved multi-source timeline yet"
            )

        pieces = [
            (max(lo, start), min(hi, end))
            for lo, hi in self.gaps(clip_id, duration)
            if hi > start and lo < end
        ]
        if not pieces:
            return []

        own = self.segments[lo0 : hi0 + 1]
        for piece_start, piece_end in pieces:
            own = _insert_piece(own, clip_id, piece_start, piece_end)
        # Rebound rather than slice-assigned in place: the assignment is what
        # drops the cached `_SpanIndex`, and an in-place splice of the same
        # length would leave a stale one answering for a changed timeline.
        self.segments = self.segments[:lo0] + own + self.segments[hi0 + 1 :]
        return pieces

    def insert(self, clip_id: str, at: float, new_clip_id: str, new_start: float, new_end: float) -> None:
        """Open a gap for material the source never had — `vo_extend`'s mutator.

        Unlike every other method here, this **grows** the timeline: it splices
        `new_clip_id`'s `[new_start, new_end)` in at `clip_id`'s source instant
        `at`, splitting `clip_id`'s own segment there if `at` falls strictly
        inside one. This is the one deliberate exception to the module
        docstring's subtractive model (PLAN.md § `vo_extend` — the design
        note); nothing else in this file adds source that was not already on
        the timeline.

        `at` must land on material `clip_id` currently plays — the first
        segment (in timeline order) whose `[start, end]` contains it, `end`
        inclusive so a boundary shared by two segments picks the earlier one,
        splicing the new material in right after it rather than before its
        neighbour. A gap (already cut, or never on the timeline) has no
        segment to find and is refused rather than guessed at: opening a hold
        is "after this word", and a word that is not currently playing has no
        "after" to be at.

        The new segment is a real, separate `Segment` of `new_clip_id` — never
        folded into `clip_id`'s own run — so `restore`'s contiguity check sees
        it and refuses across the hold by construction (its docstring already
        names this future). Word indices into `clip_id`'s transcript are
        unaffected: every source coordinate already on the timeline keeps its
        meaning, only the timeline positions downstream of `at` move later.
        """
        if new_end <= new_start:
            raise TimelineError(f"interval {new_start:.3f}-{new_end:.3f} is empty or backwards")

        for j, seg in enumerate(self.segments):
            if seg.clip_id == clip_id and seg.start <= at <= seg.end:
                break
        else:
            raise TimelineError(
                f"source instant {at:.3f}s of clip {clip_id!r} is not on the timeline "
                "(already cut, or never present) — insert only opens a gap inside "
                "material that currently plays"
            )

        pieces: list[Segment] = []
        if at - seg.start >= MIN_SEGMENT:
            pieces.append(replace(seg, end=at))
        pieces.append(Segment(clip_id=new_clip_id, start=new_start, end=new_end))
        if seg.end - at >= MIN_SEGMENT:
            pieces.append(replace(seg, start=at))
        self.segments = self.segments[:j] + pieces + self.segments[j + 1 :]


def _subtract(seg: Segment, start: float, end: float) -> list[Segment]:
    """Remove `[start, end)` from one segment: 0, 1 or 2 segments come back."""
    pieces = []
    if seg.start < start:
        pieces.append(replace(seg, end=min(start, seg.end)))
    if seg.end > end:
        pieces.append(replace(seg, start=max(end, seg.start)))
    return pieces


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Coalesce sorted, possibly overlapping intervals."""
    merged: list[tuple[float, float]] = []
    for lo, hi in intervals:
        if hi <= lo:
            continue
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _insert_piece(own: list[Segment], clip_id: str, start: float, end: float) -> list[Segment]:
    """Splice one restored `[start, end)` into `own` — one clip's own segments,
    already sorted by source time (guaranteed by the walk in `restore`) —
    merging into either neighbour it now touches exactly.
    """
    j = 0
    while j < len(own) and own[j].end <= start + MIN_SEGMENT:
        j += 1
    merge_left = j > 0 and abs(own[j - 1].end - start) <= MIN_SEGMENT
    merge_right = j < len(own) and abs(own[j].start - end) <= MIN_SEGMENT
    new_start = own[j - 1].start if merge_left else start
    new_end = own[j].end if merge_right else end
    new_seg = Segment(clip_id=clip_id, start=new_start, end=new_end)
    lo = j - 1 if merge_left else j
    hi = j + 1 if merge_right else j
    return own[:lo] + [new_seg] + own[hi:]


# -- OTIO interchange ----------------------------------------------------


def _rational(seconds: float, rate: float) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(round(seconds * rate), rate)


def _file_url(source: str) -> str:
    """The reference's URL — interchange only; `from_otio` never reads it back.

    A manifest written on Linux carries `/home/…` sources, and on Windows a
    rooted path with no drive is not absolute, so `Path.as_uri()` raises — on
    every op that writes the timeline, over a field nothing in proofcut consumes.
    Such a path gets the URL Linux wrote for it. A genuinely relative source
    still refuses, on every OS, as it always has.
    """
    path = Path(source)
    if path.is_absolute():
        return path.as_uri()
    return PurePosixPath(source).as_uri()


#: The key `to_otio` stamps into every clip's and the timeline's metadata.
#: Written under this name only (docs/plans/RENAME.md, decision 2).
METADATA_KEY = "proofcut"

#: The key every `project.otio` carried before the rename to proofcut — and
#: that every snapshot in `cache/history/N.otio` taken before it still
#: carries, forever: history is never rewritten, and `Project.restore` puts a
#: snapshot back whole. So it is **read permanently**, through
#: `proofcut_metadata` alone, and a reader that asked for `METADATA_KEY`
#: directly would break the undo of every pre-rename edit. Never written.
LEGACY_METADATA_KEY = "lucid"


def to_otio(
    edit: Edit,
    clips: dict[str, dict[str, Any]],
    *,
    rate: float,
    name: str = "proofcut",
) -> otio.schema.Timeline:
    """Serialise an `Edit` to OTIO, resolving clip_ids against the manifest."""
    timeline = otio.schema.Timeline(name=name)
    has_video = any(clips.get(s.clip_id, {}).get("has_video") for s in edit.segments)
    track = otio.schema.Track(
        name="V1" if has_video else "A1",
        kind=otio.schema.TrackKind.Video if has_video else otio.schema.TrackKind.Audio,
    )
    timeline.tracks.append(track)

    for n, seg in enumerate(edit.segments):
        record = clips.get(seg.clip_id)
        if record is None:
            raise TimelineError(f"segment {n} references unregistered clip {seg.clip_id!r}")
        reference = otio.schema.ExternalReference(
            target_url=_file_url(record["source"]),
            available_range=otio.opentime.TimeRange(
                _rational(0.0, rate), _rational(float(record["duration"]), rate)
            ),
        )
        clip = otio.schema.Clip(
            name=f"{seg.clip_id}-{n:04d}",
            media_reference=reference,
            source_range=otio.opentime.TimeRange(
                _rational(seg.start, rate), _rational(seg.duration, rate)
            ),
        )
        clip.metadata[METADATA_KEY] = {"clip_id": seg.clip_id}
        track.append(clip)

    timeline.metadata[METADATA_KEY] = {"rate": rate}
    return timeline


def proofcut_metadata(item: Any) -> dict[str, Any]:
    """The proofcut metadata on an OTIO object, under either key — the one reader.

    `METADATA_KEY` first, then `LEGACY_METADATA_KEY`; an empty dict when
    neither is there, which `from_otio` turns into "not written by proofcut".
    Every reader of the stamp goes through here, so the fallback is stated
    once rather than at each call site that happens to remember it.
    """
    for key in (METADATA_KEY, LEGACY_METADATA_KEY):
        value = item.metadata.get(key)
        if value:
            return dict(value)
    return {}


def rename_legacy_metadata(timeline: otio.schema.Timeline) -> int:
    """Move every `LEGACY_METADATA_KEY` stamp to `METADATA_KEY`, in place.

    Touches exactly the two places `to_otio` writes — the timeline and each
    clip — and returns how many it moved. Where both keys are present the
    new one is kept, since it is what every reader already prefers, and the
    old one is dropped. `Project.migrate`'s filename step is the caller; it
    runs this over the live `project.otio` and never over `cache/history/`.
    """
    moved = 0
    items: list[Any] = [timeline]
    for track in timeline.tracks:
        items.extend(item for item in track if isinstance(item, otio.schema.Clip))
    for item in items:
        if LEGACY_METADATA_KEY not in item.metadata:
            continue
        # Assigned before the delete, never popped: the old value is a view
        # onto OTIO's C++ dictionary (nested ones too), and removing the key
        # destroys what it points at. Assignment is what copies it.
        if METADATA_KEY not in item.metadata:
            item.metadata[METADATA_KEY] = item.metadata[LEGACY_METADATA_KEY]
        del item.metadata[LEGACY_METADATA_KEY]
        moved += 1
    return moved


def count_legacy_metadata(path: Path | str) -> int:
    """How many `LEGACY_METADATA_KEY` stamps `rename_legacy_metadata` would move."""
    return rename_legacy_metadata(otio.adapters.read_from_file(str(path)))


def rewrite_legacy_metadata(path: Path | str) -> int:
    """Rewrite an `.otio` file's legacy stamps to `METADATA_KEY`, atomically.

    Written beside the file and moved over it, so a crash leaves either the
    old document or the new one and never half of each. A file with nothing
    to move is left byte-for-byte alone.
    """
    path = Path(path)
    timeline = otio.adapters.read_from_file(str(path))
    moved = rename_legacy_metadata(timeline)
    if moved:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(otio.adapters.write_to_string(timeline, "otio_json"), encoding="utf-8")
        tmp.replace(path)
    return moved


def from_otio(timeline: otio.schema.Timeline) -> Edit:
    """Read an `Edit` back out of an OTIO timeline written by `to_otio`."""
    segments: list[Segment] = []
    for track in timeline.tracks:
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            meta = proofcut_metadata(item)
            clip_id = meta.get("clip_id")
            if clip_id is None:
                raise TimelineError(
                    f"clip {item.name!r} has no proofcut metadata — "
                    "this timeline was not written by proofcut"
                )
            source_range = item.source_range
            # The rational end, never float start + float duration: 55.9 + 4.3
            # is 60.199999999999996, one ulp short of the next segment's 60.2,
            # and `timeline_time(closed_end=True)`'s `==` then resolved a word
            # ending on that join to the far side of whatever was spliced there.
            segments.append(
                Segment(
                    clip_id=clip_id,
                    start=source_range.start_time.to_seconds(),
                    end=source_range.end_time_exclusive().to_seconds(),
                )
            )
        break  # single-track model; see the module docstring
    return Edit(segments=segments)


def read(path: Path | str) -> Edit:
    return from_otio(otio.adapters.read_from_file(str(path)))


def write(timeline: otio.schema.Timeline, path: Path | str) -> None:
    """Write `timeline` to `path`, atomically.

    Not `otio.adapters.write_to_file`, which truncates the live file and
    writes into it — a process killed mid-save left a `project.otio` nothing
    could parse. Same bytes, written beside the file and moved over it, as
    `Project.write_manifest` and `rewrite_legacy_metadata` already do.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(otio.adapters.write_to_string(timeline, "otio_json"), encoding="utf-8")
    tmp.replace(path)
