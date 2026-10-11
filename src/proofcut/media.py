"""Probing and registering source media.

`ffprobe` is the only thing that reads media metadata here — proofcut never
guesses fps or duration from a filename or a container assumption.

Imported media is *linked*, not copied, by default: `media/<clip_id><ext>` is a
symlink to wherever the file actually lives. A 74 MB VO on the NAS should not
be duplicated to be edited, and a broken symlink is a visible failure rather
than a silently wrong path. `copy=True` takes a real copy when the source is
somewhere that will not survive.

Not every filesystem cooperates — see `_place`. Read paths through
`media_path()` rather than assuming a `media/` entry exists.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from proofcut.project import Project

FFPROBE = "ffprobe"


class MediaError(Exception):
    """Raised when media cannot be probed or registered."""


class MultiAudioError(MediaError):
    """`import_media` refusing a container that holds more than one mic.

    A `MediaError` subclass, so every `except media.MediaError` already
    written catches it unchanged — the type exists so a *client* can offer
    the choice rather than reprint the sentence. `webui`'s import job reads
    `streams` off it and the assets pane turns it into a button; nothing
    string-matches the message to work out what happened.
    """

    def __init__(self, message: str, *, streams: int) -> None:
        super().__init__(message)
        self.streams = streams


@dataclass(frozen=True)
class MediaInfo:
    """What ffprobe knows about one file."""

    duration: float
    has_video: bool
    has_audio: bool
    fps: float | None
    width: int | None
    height: int | None
    sample_rate: int | None
    channels: int | None
    video_codec: str | None
    audio_codec: str | None
    #: r_frame_rate disagrees with avg_frame_rate. See PLAN.md — recorded, not
    #: acted on: cut-and-concat works in the time domain where VFR is mostly
    #: fine, so normalising is deferred to NLE export.
    vfr: bool
    #: How many audio streams the *container* holds. Every other audio field
    #: above describes the first one, which is the whole reason this is
    #: counted: a two-mic capture reads exactly like a one-mic capture
    #: through them. Defaulted so a hand-built `MediaInfo` still means what
    #: it always meant — one stream. PLAN.md § The co-hosted recording.
    audio_streams: int = 1
    #: A chapter list — or the inherited data/text track that carries one —
    #: was detected on this container. Measured against a real affected file
    #: (goodsometimes `Source/sl-0428-elevator.mp4`): a movie rip's chapter
    #: list survives every re-encode from clip to concat to delivery as a
    #: `codec_type: "data"` stream whose own declared `duration` is the
    #: *parent film's* runtime (6869.662s on a 27.027s clip), and ffprobe
    #: surfaces it as a top-level `chapters` array whenever the QuickTime
    #: chapter-track reference resolves. Defaulted like `audio_streams` so a
    #: hand-built `MediaInfo` elsewhere still means what it always meant.
    has_chapters: bool = False
    #: Bits per luma sample — 8 for `yuv420p`, 10 for `yuv420p10le`. It is
    #: here because **ffmpeg's `signalstats` reports on the source's own
    #: scale, so a luma reading is meaningless without it**: the film's
    #: `s4-overexposed` measures YAVG 429 / YMAX 927 against every 8-bit
    #: clip's 26–132 / 127–255, which is a different number system and not a
    #: brighter picture (measured 2026-08-25). Anything comparing luma across
    #: clips divides by `2**bit_depth - 1` first. Defaulted to 8 like the two
    #: fields above, so a hand-built `MediaInfo` still means what it meant.
    bit_depth: int = 8
    #: `(audio ordinal, codec_name)` for each audio stream this box's ffmpeg
    #: has no decoder for — asked only of a container with more than one.
    #: An iPhone recording Spatial Audio holds AAC stereo at audio 0 and
    #: `apple_apac` at audio 1: one performance written twice, not two mics,
    #: and the second is a stream nothing here can read, so summing the two
    #: is an ffmpeg error and not a mixdown. Found by the Windows probe on a
    #: real phone clip; ffmpeg 9.0.1 names the codec and 8.1.2 does not list
    #: it at all, and neither decodes it. HISTORY.md § The phone's Spatial
    #: Audio track. Omitted from `as_dict` when empty, so an ordinary clip
    #: record gains no key.
    undecodable_audio: tuple[tuple[int, str], ...] = ()
    #: Seconds from the file's zero to where its picture stops: the first
    #: video stream's `start_time + duration`, never the container's, which
    #: ends with the longer stream. Audio that outlasts the picture by more
    #: than half a frame otherwise seeds a timeline one frame past the video,
    #: and that frame renders black with `frames` agreeing. An attached
    #: picture (an audio file's cover art) is not picture, and None means no
    #: stream says. HISTORY.md § The phone's black last frame.
    picture_end: float | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        undecodable = out.pop("undecodable_audio")
        if undecodable:
            out["undecodable_audio"] = [{"stream": k, "codec": c} for k, c in undecodable]
        return out


def _fraction(value: str | None) -> float | None:
    """Parse ffprobe's "30000/1001" rate strings. Returns None for 0/0."""
    if not value or "/" not in value:
        return None
    num, _, den = value.partition("/")
    try:
        n, d = float(num), float(den)
    except ValueError:
        return None
    return n / d if d else None


def _ffprobe(media: Path, *args: str) -> dict[str, Any]:
    """One ffprobe invocation, with the two ways it can fail spelled out."""
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", *args, str(media)]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise MediaError(f"{FFPROBE} not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaError(f"ffprobe failed on {media}: {exc.stderr.strip()}") from exc
    return json.loads(completed.stdout)


_DECODABLE_AUDIO: frozenset[str] | None = None


def decodable_audio_codecs() -> frozenset[str] | None:
    """The audio `codec_name`s this box's ffmpeg can decode, or None if unknown.

    Read off `ffmpeg -codecs`, whose first flag column is `D` for a codec with
    a decoder. A codec the build has no descriptor for is not listed at all,
    which reads the same as listed without `D` — both are undecodable. None
    (ffmpeg would not run, or listed no decodable audio) means *don't know*,
    and every caller then treats every stream as decodable, which is what
    import did before this was asked. Cached for the process.
    """
    global _DECODABLE_AUDIO
    if _DECODABLE_AUDIO is None:
        try:
            completed = subprocess.run(
                ["ffmpeg", "-hide_banner", "-codecs"], capture_output=True, text=True, check=False
            )
        except OSError:
            return None
        names = set()
        for line in completed.stdout.splitlines():
            flags, _, rest = line.strip().partition(" ")
            if len(flags) == 6 and flags[0] == "D" and flags[2] == "A" and rest.split():
                names.add(rest.split()[0])
        if not names:
            return None
        _DECODABLE_AUDIO = frozenset(names)
    return _DECODABLE_AUDIO


#: How far `-show_format`'s own `duration` may disagree with the loudest of
#: the real video/audio streams before it counts as the second chapter-
#: contamination signal, below. Picked as a "clearly not rounding" gap, not
#: measured against a real disagreement — on every affected file this repo
#: has actually probed (goodsometimes `Source/`, sixteen clips, thirteen
#: carrying the data track) `-show_format` already reported the *correct*
#: figure; the data stream's own inflated duration never leaked into it.
#: This stays as the defensive second signal the two-signal idiom calls for
#: (safe zones, brightness bbox — "trust neither alone"), for a muxer that
#: computes container duration differently than this box's ffmpeg does.
CHAPTER_DURATION_TOLERANCE = 0.05


#: `yuv420p10le` and friends — the depth is a suffix on the planar name.
_PIX_FMT_DEPTH = re.compile(r"p(\d{1,2})(?:[bl]e)?$")
#: `p010le` / `p016le`, where the whole name *is* the depth, zero-padded to
#: three digits. Worth its own pattern rather than left to
#: `bits_per_raw_sample`: these are what hardware encoders emit, which is
#: phone and action-cam footage — exactly what `footage_sheet` is for.
_PIX_FMT_PACKED = re.compile(r"^p(\d{3})(?:[bl]e)?$")


def _bit_depth(video: dict[str, Any] | None) -> int:
    """Bits per luma sample, from ffprobe's own field or the pixel format.

    Several signals because none is always there: `bits_per_raw_sample` is
    absent on plenty of streams, and a planar pixel format only names its
    depth when it is not 8 (`yuv420p` against `yuv420p10le`). 8 is the answer
    when nothing says otherwise, which is what an unadorned
    `yuv420p`/`rgb24`/`gray` means — and it is the safe direction to be wrong
    in, since under-reporting the scale reads a frame as *brighter* than it is
    and no caller here acts on brightness except to say a tile is empty.
    """
    if video is None:
        return 8
    raw = video.get("bits_per_raw_sample")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    fmt = str(video.get("pix_fmt") or "")
    for pattern in (_PIX_FMT_DEPTH, _PIX_FMT_PACKED):
        found = pattern.search(fmt)
        if found:
            return int(found[1])
    return 8


def still_bit_depth(path: Path | str) -> int:
    """Bits per sample of a still image, by the same rule `probe` applies to video.

    `probe` refuses a still — ffprobe reports no duration for a PNG, and it is
    right to — so a sheet that read a card's bit depth through it errored on
    the card and took the other tiles with it (the first recorded agent run,
    LAUNCH.md § Step 1: `shot_sheet` failed on the title card the agent had
    just made). One ffprobe for the image's own stream, `_bit_depth` for the
    answer; 8 when ffprobe cannot say, which is `_bit_depth`'s own default
    and the safe direction to be wrong in.
    """
    done = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=pix_fmt,bits_per_raw_sample",
            "-of", "json", str(Path(path).expanduser()),
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    if done.returncode != 0:
        return 8
    try:
        streams = json.loads(done.stdout).get("streams") or []
    except json.JSONDecodeError:
        return 8
    return _bit_depth(streams[0] if streams else None)


def probe(path: Path | str) -> MediaInfo:
    """Run ffprobe against `path` and summarise its first video/audio stream.

    **Chapter/data-track contamination**, detected on two independent
    signals, neither trusted alone: a non-empty top-level `chapters` array,
    and/or `-show_format`'s own `duration` disagreeing with the loudest of
    the real video/audio stream durations by more than
    `CHAPTER_DURATION_TOLERANCE`. A movie rip cut with a third-party tool
    inherits its parent film's chapter list as a `codec_type: "data"` stream
    that survives every re-encode (goodsometimes `ideas/lambs-longlegs.md`,
    "v3"), and that data stream's own declared duration can run to the
    *film's* runtime rather than the clip's — measured directly against
    `Source/sl-0428-elevator.mp4`, a 27.027s clip carrying a data stream
    declaring 6869.662s. `audio_streams` below only counts `codec_type ==
    "audio"`, so this never interferes with the multi-mic count.

    When either signal fires, `duration` is read off the real video/audio
    stream instead of trusting `-show_format` — the same per-stream fallback
    already used when `-show_format` reports nothing at all, generalized to
    fire on disagreement too rather than only on absence.
    """
    media = Path(path).expanduser()
    if not media.exists():
        raise MediaError(f"no such media file: {media}")

    payload = _ffprobe(media, "-show_format", "-show_streams", "-show_chapters")
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def _stream_duration(stream: dict[str, Any] | None) -> float | None:
        value = stream.get("duration") if stream else None
        return float(value) if value is not None else None

    format_duration_raw = payload.get("format", {}).get("duration")
    format_duration = float(format_duration_raw) if format_duration_raw is not None else None
    video_duration = _stream_duration(video)
    audio_duration = _stream_duration(audio)
    stream_reference = max(
        (d for d in (video_duration, audio_duration) if d is not None), default=None
    )
    # The existing fallback's own preference — video over audio — generalized
    # to also serve as the correction when the container-level figure is
    # contaminated, not only when it is absent.
    stream_fallback = video_duration if video_duration is not None else audio_duration

    duration_disagrees = bool(
        format_duration is not None
        and stream_reference is not None
        and stream_reference > 0
        and abs(format_duration - stream_reference) / stream_reference > CHAPTER_DURATION_TOLERANCE
    )
    has_chapters = bool(payload.get("chapters")) or duration_disagrees

    if format_duration is None or has_chapters and stream_fallback is not None:
        duration = stream_fallback
    else:
        duration = format_duration
    if duration is None:
        raise MediaError(f"ffprobe reported no duration for {media}")

    picture = next(
        (s for s in streams
         if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    picture_duration = _stream_duration(picture)
    picture_end = (
        float(picture.get("start_time") or 0.0) + picture_duration
        if picture is not None and picture_duration is not None
        else None
    )

    fps = avg = None
    if video is not None:
        fps = _fraction(video.get("r_frame_rate"))
        avg = _fraction(video.get("avg_frame_rate"))

    audio_codecs = [s.get("codec_name") or "unknown" for s in streams if s.get("codec_type") == "audio"]
    undecodable: tuple[tuple[int, str], ...] = ()
    if len(audio_codecs) > 1:
        decodable = decodable_audio_codecs()
        if decodable is not None:
            undecodable = tuple((k, c) for k, c in enumerate(audio_codecs) if c not in decodable)

    return MediaInfo(
        duration=float(duration),
        has_video=video is not None,
        has_audio=audio is not None,
        fps=fps,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        channels=audio.get("channels") if audio else None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        # A 1% tolerance: 30000/1001 vs 29.97 is rounding, not variability.
        vfr=bool(fps and avg and abs(fps - avg) / fps > 0.01),
        audio_streams=len(audio_codecs),
        has_chapters=has_chapters,
        bit_depth=_bit_depth(video),
        undecodable_audio=undecodable,
        picture_end=picture_end,
    )


def count_frames(path: Path | str) -> dict[str, Any]:
    """Count a render's video frames, exactly enough to compare one against.

    `probe`'s `nb_frames` is read out of the container header, and a muxer is
    free to write it wrong or leave it out — which is no basis for a check
    whose entire value is that the number is exact. `-count_packets` walks the
    stream instead. For one-frame-per-packet video that is exact, and unlike
    `-count_frames` it never decodes a pixel.

    Both numbers come back, and a disagreement between them is reported rather
    than resolved here: two ffprobe readings of the same file differing is
    itself a finding, and picking the nicer one would bury it.

    `frames` is None when there is no video stream at all. An audio-only render
    is the ordinary case for a VO project, not an error — see `ops.check_frames`.

    `fps` is the stream's declared rate when it is constant (`r_frame_rate`
    equal to `avg_frame_rate`), else None, since a variable-rate stream has
    no one grid a frame count could be read against.
    """
    media = Path(path).expanduser()
    if not media.exists():
        raise MediaError(f"no such media file: {media}")

    payload = _ffprobe(
        media,
        "-select_streams",
        "v:0",
        "-count_packets",
        "-show_entries",
        "stream=nb_read_packets,nb_frames,r_frame_rate,avg_frame_rate:format=duration",
        "-show_format",
    )
    streams = payload.get("streams", [])
    duration = payload.get("format", {}).get("duration")

    def _count(value: Any) -> int | None:
        return int(value) if value is not None and str(value).isdigit() else None

    if not streams:
        return {
            "fps": None,
            "frames": None,
            "container_frames": None,
            "duration": float(duration) if duration is not None else None,
            "has_video": False,
        }
    def _rate(value: Any) -> Fraction | None:
        try:
            rate = Fraction(str(value))
        except (ValueError, ZeroDivisionError):
            return None
        return rate if rate > 0 else None

    declared = _rate(streams[0].get("r_frame_rate"))
    average = _rate(streams[0].get("avg_frame_rate"))
    return {
        "fps": float(declared) if declared is not None and declared == average else None,
        "frames": _count(streams[0].get("nb_read_packets")),
        "container_frames": _count(streams[0].get("nb_frames")),
        "duration": float(duration) if duration is not None else None,
        "has_video": True,
    }


#: How far a non-picture stream's own declared duration may outrun the
#: picture's before `stream_inventory` calls it a fault —
#: `verify_longlegs.py`'s own number, ported: a real audio/subtitle stream
#: agrees with the picture to well inside this; a stream still declaring a
#: parent film's runtime does not.
STREAM_DURATION_SLACK = 0.5


def stream_inventory(path: Path | str) -> dict[str, Any]:
    """Every stream in `path`, flagged the way `verify_longlegs.py`'s own
    `streams()` check was: a chapter list, a stream that is neither picture
    nor sound, and a non-picture stream whose own duration outruns the
    picture's by more than `STREAM_DURATION_SLACK` — each is a fault a
    *delivered* file should never carry.

    `ops.finish_check`'s step 1, ported 1:1 rather than re-derived: this is
    the finished-file check `probe()`'s own chapter detection exists to make
    unnecessary at *import* time (`import_media` strips a chapter list before
    it ever reaches a project), so this function does not consult `probe()`
    at all — a delivered file was built entirely outside proofcut and the two
    checks answer different questions about it. `clean` says nothing about
    whether the *total* duration agrees with the timeline; that comparison
    needs the project's own arithmetic and is `finish_check`'s to make, off
    `probe(path).duration` directly.
    """
    source = Path(path).expanduser()
    if not source.exists():
        raise MediaError(f"no such media file: {source}")

    payload = _ffprobe(source, "-show_streams", "-show_chapters")
    raw_streams = payload.get("streams", [])
    chapters = payload.get("chapters", [])

    def _duration(stream: dict[str, Any]) -> float | None:
        value = stream.get("duration")
        return float(value) if value is not None else None

    picture = next((s for s in raw_streams if s.get("codec_type") == "video"), None)
    picture_duration = _duration(picture) if picture is not None else None

    streams: list[dict[str, Any]] = []
    faults: list[str] = []
    if chapters:
        faults.append(f"{len(chapters)} chapter(s) present — -map_chapters -1 was missed")

    for stream in raw_streams:
        kind = stream.get("codec_type")
        duration = _duration(stream)
        fault: str | None = None
        if kind not in ("video", "audio"):
            fault = "not picture or sound"
        elif (
            kind != "video"
            and picture_duration is not None
            and duration is not None
            and duration > picture_duration + STREAM_DURATION_SLACK
        ):
            fault = "outruns the picture"
        entry = {
            "index": stream.get("index"),
            "codec_type": kind,
            "codec_name": stream.get("codec_name"),
            "duration": duration,
            "fault": fault,
        }
        if fault is not None:
            faults.append(f"stream {entry['index']} ({kind}): {fault}")
        streams.append(entry)

    return {
        "path": str(source),
        "streams": streams,
        "chapters": len(chapters),
        "faults": faults,
        "clean": not faults,
    }


#: The floor `scene_cuts` decodes at. Not the threshold anything is judged on —
#: scores come back and the caller thresholds them, because the expensive half
#: is the decode and re-deciding the number must not cost another pass. Below
#: this, ffmpeg's own scene score is noise.
SCENE_FLOOR = 0.05


def scene_cuts(
    path: Path | str, *, floor: float = SCENE_FLOOR, until: float | None = None
) -> list[dict[str, float]]:
    """Every place ffmpeg thinks the picture changed, with its score.

    A list of `{"src_time", "score"}` in source seconds, ascending — scored, not
    thresholded. Which threshold is right is not a preference here, and it is
    not agreement with a hand table either: **every candidate the film shows was
    judged on the frames either side**, and from 0.141 up all 31 are real
    camera cuts while the first non-cut is at 0.137. **0.15 is picked by that**,
    and it lives with the thing that applies it (`ops.SCENE_THRESHOLD`).
    HISTORY.md § The scene threshold, re-pinned.

    `until` stops the decode early, and it is the only cost knob: a 730s clip
    the film reads 71.8s of has no reason to be walked to the end.

    A file with no detectable change returns an empty list. That is an answer —
    a single continuous shot — and not a failure.
    """
    media = Path(path).expanduser()
    if not media.exists():
        raise MediaError(f"no such media file: {media}")

    with tempfile.TemporaryDirectory(prefix="proofcut-scene-") as tmp:
        # `metadata=print` to a *file*, not to stdout: bare `metadata=print`
        # writes nothing anywhere ffmpeg's own `-v error` leaves readable, which
        # reads exactly like a clip with no cuts in it.
        #
        # Named relative to a working directory, never as an absolute path:
        # `:` separates a filter's options, so a Windows temp dir's `C:` ended
        # the filename at the drive letter and ffmpeg refused the whole chain
        # (the first Windows CI run, 2026-09-10). `captions.burn`'s `ass=`
        # dodges the same trap the same way.
        report = Path(tmp) / "scenes.txt"
        command = [
            "ffmpeg", "-nostdin", "-v", "error",
            *(("-t", f"{float(until):.3f}") if until else ()),
            "-i", str(media.resolve()),
            "-vf", f"select='gt(scene,{floor})',metadata=print:file={report.name}",
            "-an", "-f", "null", "-",
        ]  # fmt: skip
        try:
            completed = subprocess.run(
                command, cwd=tmp, capture_output=True, text=True, check=False
            )
        except FileNotFoundError as exc:
            raise MediaError(f"could not run ffmpeg: {' '.join(command)}") from exc
        if completed.returncode != 0:
            raise MediaError(
                f"ffmpeg could not scan {media} for cuts: {completed.stderr.strip()[-800:]}"
            )
        text = report.read_text(encoding="utf-8") if report.exists() else ""

    cuts: list[dict[str, float]] = []
    when: float | None = None
    for line in text.splitlines():
        stamp = re.search(r"pts_time:([0-9.]+)", line)
        if stamp:
            when = float(stamp.group(1))
            continue
        score = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        if score and when is not None:
            cuts.append({"src_time": when, "score": float(score.group(1))})
            when = None
    return cuts


# -- what a browser will actually play ------------------------------------
#
# The preview pane is a <video> element, so "can this be edited" and "can this
# be *watched in the window*" are different questions and only ffprobe can tell
# them apart. Every set below is the intersection that holds for the browsers
# `proofcut web` is used from on this box (Chromium and Firefox on Linux), which
# is narrower than the spec and narrower than Safari — an `hvc1`-tagged HEVC
# plays on iOS and not here (wiki `home.md`).
_PLAYABLE_VIDEO = frozenset({"h264", "vp8", "vp9", "av1", "theora"})
_PLAYABLE_AUDIO = frozenset({"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_u8"})
#: 10-bit and 4:2:2 decode in ffmpeg and not in a browser's H.264 decoder, so
#: the pixel format is a separate gate from the codec name and not implied by it.
_PLAYABLE_PIX = frozenset({"yuv420p", "yuvj420p"})
_PLAYABLE_CONTAINER = frozenset(
    {".mp4", ".m4v", ".mov", ".webm", ".ogg", ".ogv", ".oga", ".mp3", ".m4a", ".wav", ".flac"}
)

#: What `discover()` looks for on disk. Deliberately wider than
#: `_PLAYABLE_CONTAINER` above — that set is what a *browser* decodes, this
#: one is what a camera or a movie rip hands proofcut, and `.mkv`/`.avi`/`.aac`
#: never play in the preview but import and edit exactly like anything else
#: (CLAUDE.md's own `Source/sl-0428-elevator.mp4` chapter-list example is a
#: movie rip). A filename filter, not a probe — `import_media` still refuses
#: or accepts on what ffprobe actually finds.
SOURCE_MEDIA_EXTENSIONS = frozenset(
    {
        ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".m2ts", ".mts",
        ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".oga", ".ogv",
    }
)


def discover(source_dir: Path | str, *, recursive: bool = True) -> list[Path]:
    """List files under `source_dir` whose extension names a media container.

    Sorted, resolved paths — cheap and filename-only, so a directory of raw
    footage does not cost a probe per file just to be listed. `import_media`
    does the real work of deciding whether a given file is actually usable.
    """
    root = Path(source_dir).expanduser()
    if not root.is_dir():
        raise MediaError(f"{root} is not a directory")
    pattern = "**/*" if recursive else "*"
    return sorted(
        p.resolve()
        for p in root.glob(pattern)
        if p.is_file() and p.suffix.lower() in SOURCE_MEDIA_EXTENSIONS
    )


def playability(path: Path | str) -> dict[str, Any]:
    """Can a browser play this file, and if not, name the reason.

    Answers the question the preview pane fails at silently: a `<video>` whose
    source it cannot decode fires one contentless `error` event and shows black,
    which is indistinguishable from a correct black frame in the edit. So the
    reason is worked out here, on the box that has ffprobe, and reported as
    words a person can act on rather than inferred in JS from an empty event.

    Only ever consulted *about* the preview — no render path reads it, because
    ffmpeg and melt decode everything in this list and a good deal more. A file
    this call refuses still exports correctly.

    `{"playable": bool, "reason": str | None, ...}` plus the four fields the
    verdict was reached on, so a surprising answer can be checked against what
    was actually measured rather than re-probed by hand.
    """
    media = Path(path).expanduser()
    if not media.exists():
        raise MediaError(f"no such media file: {media}")

    payload = _ffprobe(
        media,
        "-show_entries",
        "stream=codec_type,codec_name,codec_tag_string,profile,pix_fmt",
        "-show_streams",
    )
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    measured: dict[str, Any] = {
        "container": media.suffix.lower(),
        "video_codec": video.get("codec_name") if video else None,
        "video_tag": video.get("codec_tag_string") if video else None,
        "profile": video.get("profile") if video else None,
        "pix_fmt": video.get("pix_fmt") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
    }

    def verdict(reason: str | None) -> dict[str, Any]:
        return {"playable": reason is None, "reason": reason, **measured}

    if media.suffix.lower() not in _PLAYABLE_CONTAINER:
        return verdict(f"{media.suffix or 'this'} is not a container browsers open")
    if video is None and audio is None:
        return verdict("this file has neither a video nor an audio stream")
    if video is not None:
        codec = video.get("codec_name")
        if codec not in _PLAYABLE_VIDEO:
            tag = video.get("codec_tag_string")
            tagged = f" (tagged {tag})" if tag else ""
            return verdict(f"video codec {codec}{tagged} is not decodable in a browser here")
        pix = video.get("pix_fmt")
        if pix not in _PLAYABLE_PIX:
            profile = video.get("profile") or codec
            return verdict(f"{profile} at {pix} is beyond a browser's 8-bit 4:2:0 decoder")
    if audio is not None and audio.get("codec_name") not in _PLAYABLE_AUDIO:
        return verdict(f"audio codec {audio.get('codec_name')} is not decodable in a browser here")
    return verdict(None)


def slugify(name: str) -> str:
    """Turn a filename into a clip_id candidate: lowercase, dashes, no cruft."""
    stem = Path(name).stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return slug or "clip"


def _unique_clip_id(candidate: str, taken: set[str]) -> str:
    if candidate not in taken:
        return candidate
    n = 2
    while f"{candidate}-{n}" in taken:
        n += 1
    return f"{candidate}-{n}"


def derive_single_audio(
    source: Path,
    dest: Path,
    *,
    streams: int,
    pick: int | None = None,
    has_video: bool = False,
    undecodable: tuple[tuple[int, str], ...] = (),
) -> dict[str, Any]:
    """Write a one-audio-stream copy of `source`, either summed or picked.

    The whole point is that nothing downstream ever has to choose a stream.
    `mlt.py` emits `audio_index` only as `-1`, to silence a picture node, so
    the Edit lane's producer carries no property at all and MLT picks — and
    it picks the first. A dual-mic container rendered today loses the second
    mic entirely, at exit 0, with `verify`, `check_frames` and `film_check`
    all clean, because every one of them compares the render against the
    timeline and the timeline never knew there was a second stream. Measured
    by Goertzel readback of a real melt render: PLAN.md § The co-hosted
    recording, *The render trap*.

    So the choice is made once, here, and written down. `pick` is **ffmpeg's
    own audio ordinal** (`-map 0:a:1` is the second *audio* stream), which is
    deliberately not MLT's `audio_index` (an absolute stream index, where the
    second mic of a video container is 2). The two numberings agree exactly
    on an audio-only file, which is every fixture anyone writes first — this
    path never uses MLT's.

    A pick is a stream copy, so a rip with a commentary track costs a remux
    and no quality. A sum has to re-encode; video is copied through either
    way, or a mixdown would silently drop the picture.
    """
    if streams < 2:
        raise MediaError(f"{source} has {streams} audio stream(s) — nothing to derive")
    if pick is not None and not 0 <= pick < streams:
        raise MediaError(
            f"{source} has {streams} audio streams, numbered 0-{streams - 1}; asked for {pick}"
        )

    # `undecodable` (`MediaInfo.undecodable_audio`) never reaches a filter
    # graph: a sum takes the rest, and a sum of one is that one, copied.
    skipped = {k for k, _ in undecodable}
    usable = [k for k in range(streams) if k not in skipped]
    if pick is not None and pick in skipped:
        raise MediaError(f"{source} audio stream {pick} is {dict(undecodable)[pick]}, which ffmpeg cannot decode")
    if pick is None and len(usable) == 1:
        pick = usable[0]

    dest.parent.mkdir(parents=True, exist_ok=True)
    codec = "copy"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source)]
    if pick is None:
        taps = "".join(f"[0:a:{k}]" for k in usable)
        command += [
            "-filter_complex",
            f"{taps}amix=inputs={len(usable)}:duration=longest:normalize=1[a]",
        ]
        command += ["-map", "0:v"] if has_video else []
        command += ["-map", "[a]"]
        command += ["-c:v", "copy"] if has_video else []
        codec = "pcm_s16le" if dest.suffix.lower() == ".wav" else "aac"
        command += ["-c:a", codec]
    else:
        command += ["-map", "0:v"] if has_video else []
        command += ["-map", f"0:a:{pick}", "-c", "copy"]
    command += [str(dest)]

    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"could not run ffmpeg: {' '.join(command)}") from exc
    if completed.returncode != 0 or not dest.exists():
        raise MediaError(
            f"ffmpeg could not reduce {source} to one audio stream: "
            f"{completed.stderr.strip()[-800:]}"
        )
    report: dict[str, Any] = {
        "streams": streams,
        "mode": "pick" if pick is not None else "sum",
        "stream": pick,
        "codec": codec,
    }
    if undecodable:
        report["undecodable"] = [{"stream": k, "codec": c} for k, c in undecodable]
    return report


def downmix_to_stereo(source: Path, dest: Path, *, channels: int, has_video: bool = False) -> dict[str, Any]:
    """Write a copy of `source` whose one audio stream is a stereo downmix.

    **MLT plays only the first two channels of a stream with more than two
    and no channel layout**, so the centre — where a film's dialogue is —
    never reaches the render. Measured on the Lambs/Longlegs clips cut from a
    5.1 rip (`ll-1230-half-psychic`: dialogue on channel 2 at −32.6 dB, fronts
    at −64.6/−63.0): melt's stereo output read −64.7/−63.1, the fronts exactly,
    while `ffmpeg -ac 2` read −40.4 with the centre in it. A stream tagged 5.1
    did downmix in melt, but 9 dB hotter than ffmpeg's, so a hold's gain —
    measured by ffmpeg — did not describe what melt played; and MLT's own
    avformat notes call more than two channels above 16 bits unsupported. Its
    producer has no layout override (`channel_layout=5.1` changed nothing).

    So one downmix, ffmpeg's, made once at import and read by everything:
    whisper, the loudness measurement, the preview and melt. Video is copied
    through; audio re-encodes, as the multi-mic sum does.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    codec = "pcm_s16le" if dest.suffix.lower() == ".wav" else "aac"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source)]
    command += ["-map", "0:v", "-c:v", "copy"] if has_video else []
    command += ["-map", "0:a:0", "-ac", "2", "-c:a", codec, str(dest)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"could not run ffmpeg: {' '.join(command)}") from exc
    if completed.returncode != 0 or not dest.exists():
        raise MediaError(
            f"ffmpeg could not downmix {source} to stereo: {completed.stderr.strip()[-800:]}"
        )
    return {"channels": channels}


def strip_chapters(source: Path, dest: Path) -> dict[str, Any]:
    """Write a copy of `source` with its chapter list and data track dropped.

    Modeled on `derive_single_audio`: a plain stream copy, no re-encode.
    `-map 0` takes every real stream, `-map_chapters -1` drops the chapter
    list itself, and `-dn` drops the data/text track a chapter list travels
    on — matching goodsometimes' own fix (`ideas/lambs-longlegs.md`, "v3":
    "`-map_chapters -1 -dn` at both ends now"). Verified against a real
    affected file: `nb_streams` goes from 3 to 2 and the `chapters` array
    from 2 entries to 0, byte-identical picture and sound.

    There is no legitimate choice to offer here, unlike a multi-mic
    container — the source film's chapter list was never meant to travel
    with a clip cut from it — so this is unconditional, never gated behind a
    flag the way `mix`/`audio_stream` are.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-map", "0", "-map_chapters", "-1", "-dn", "-c", "copy", str(dest),
    ]  # fmt: skip
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"could not run ffmpeg: {' '.join(command)}") from exc
    if completed.returncode != 0 or not dest.exists():
        raise MediaError(
            f"ffmpeg could not strip chapters from {source}: {completed.stderr.strip()[-800:]}"
        )
    return {"had_chapters": True}


def decode_stream_wav(
    source: Path | str, dest: Path | str, *, stream: int, rate: int = 16000
) -> float:
    """Decode one audio stream of `source` to a 16-bit mono WAV, in seconds.

    `stream` is **ffmpeg's own audio ordinal** — `-map 0:a:1` is the second
    *audio* stream — the same numbering `derive_single_audio`'s `pick` takes
    and deliberately not MLT's `audio_index`, which is an absolute stream
    index where the second mic of a video container is 2. The two agree
    exactly on an audio-only file, which is every fixture anyone writes first
    (PLAN.md § The co-hosted recording, *The render trap*).

    This is the one place proofcut reaches past the mixdown to an individual
    mic, and it exists for `ops.attribute_speakers`. It writes a scratch file
    for something to measure and never enters the manifest, so nothing it
    produces can reach a render — `media_path()` gains no branch, exactly as
    the preview proxy does not get one.
    """
    src, out = Path(source), Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
        "-map", f"0:a:{stream}", "-vn", "-ac", "1", "-ar", str(rate),
        "-c:a", "pcm_s16le", str(out),
    ]  # fmt: skip
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"could not run ffmpeg: {' '.join(command)}") from exc
    if completed.returncode != 0 or not out.exists():
        raise MediaError(
            f"ffmpeg could not decode audio stream {stream} of {src.name}: "
            f"{completed.stderr.strip()[-800:]}"
        )

    with wave.open(str(out), "rb") as handle:
        frames, sample_rate = handle.getnframes(), handle.getframerate()
    if not frames:
        raise MediaError(f"audio stream {stream} of {src.name} decoded to no audio at all")
    return frames / sample_rate


def container_path(project: Project, clip: dict[str, Any]) -> Path:
    """The registered container itself — before any mixdown was derived from it.

    Every other resolver here answers "what should be edited or played", and
    for a two-mic clip that is the mixdown: `media_path()` prefers `mixed`,
    and so does `original_media_path`, because the untouched original of a
    two-mic container *is* the mixdown. This one answers the only question
    where that is wrong — "where are the individual mics" — and it has
    exactly one caller, `ops.attribute_speakers`.

    It reads keys already in the preference chain (`media`, then `source`)
    rather than adding one, so it cannot hand a derived project a path with
    nothing at it the way a new key would (CLAUDE.md § `_reel_media`'s key
    tuple).
    """
    local = clip.get("media")
    return (project.root / local) if local else Path(clip["source"])


def import_media(
    project: Project,
    path: Path | str,
    *,
    clip_id: str | None = None,
    copy: bool = False,
    mix: bool = False,
    audio_stream: int | None = None,
) -> dict[str, Any]:
    """Probe `path`, link it into the project, and record it in the manifest.

    Returns the clip record. Re-importing the same source path is a no-op that
    returns the existing record, so an agent retrying a call cannot silently
    register the same media twice under two ids. That dedup is also why the
    second audio stream of a container is *not* reached by importing the file
    twice, which is the workaround it should keep blocking.

    **A container with more than one audio stream is refused**, rather than
    registered as if the first one were the recording. Every audio field on
    the record describes the first stream, whisper is handed the container
    and ffmpeg picks, and MLT picks again at render — three independent
    places that would all quietly agree on mic A while mic B never reached
    the film. `mix=True` sums the streams into one track proofcut edits;
    `audio_stream=k` keeps one of them. Either way the choice is made once,
    written to `cache/mixed/`, recorded as `mixed`/`mix`, and picked up by
    `media_path()` everywhere downstream — the `attenuated` precedent.
    PLAN.md § The co-hosted recording.

    **A chapter list — or the data/text track it rides on — is stripped
    unconditionally**, no flag to opt out: unlike the audio-stream case above,
    there is no legitimate choice to offer, since the source film's chapter
    list was never meant to travel with a clip cut from it. Detected on two
    signals (`probe`'s own `has_chapters`), written to `cache/stripped/`,
    recorded as `stripped`/`strip`, and — like `mixed` — picked up by
    `media_path()` automatically. When a container is both multi-mic and
    chaptered the strip runs on the already-mixed copy, not the raw source,
    so there is one "which file is the untouched original" question rather
    than two. `duration` is re-probed off the most-derived file afterward,
    closing the latent gap this path shared with the multi-mic-only one
    (which never re-probed its own mixdown before this).
    """
    source = Path(path).expanduser().resolve()
    info = probe(source)

    if mix and audio_stream is not None:
        raise MediaError(
            "mix sums every audio stream and audio_stream keeps one of them — "
            "pass one or the other, not both"
        )

    manifest = project.read_manifest()
    clips: list[dict[str, Any]] = manifest.setdefault("clips", [])

    existing = next((c for c in clips if c["source"] == str(source)), None)
    if existing is not None:
        return existing

    # Only now, on a source this project has not already resolved: the dedup
    # above is a documented no-op that returns the existing record, and a
    # refusal ahead of it would make a retried call raise about a container
    # whose two mics were summed days ago.
    # A stream no decoder can read is not a mic to choose (`undecodable_audio`):
    # an iPhone's Spatial Audio track beside its AAC one is one recording, so
    # a container left with one readable stream is imported as holding one.
    undecodable = info.undecodable_audio
    readable = info.audio_streams - len(undecodable)
    if info.audio_streams > 1 and readable == 0:
        raise MediaError(
            f"{source.name} holds {info.audio_streams} audio streams and ffmpeg can decode none "
            f"of them ({', '.join(c for _, c in undecodable)})"
        )
    if readable > 1 and not mix and audio_stream is None:
        raise MultiAudioError(
            f"{source.name} holds {info.audio_streams} audio streams, and proofcut edits one. "
            "Registering it as it stands would record only the first: whisper picks a stream "
            "of its own and so does MLT at render, so the other streams would be missing from "
            "the film with every check clean. Sum them into the one track proofcut edits with "
            "`--mix` (mix=True) — two mics of one performance — or keep one with "
            "`--audio-stream k` (audio_stream=k), numbered from 0. Either writes a derived "
            "copy and records which was taken.",
            streams=info.audio_streams,
        )
    if audio_stream is not None and not 0 <= audio_stream < max(info.audio_streams, 1):
        raise MediaError(
            f"{source.name} has {info.audio_streams} audio stream(s), numbered "
            f"0-{max(info.audio_streams, 1) - 1}; asked for {audio_stream}"
        )

    taken = {c["clip_id"] for c in clips}
    if clip_id is None:
        clip_id = _unique_clip_id(slugify(source.name), taken)
    elif clip_id in taken:
        raise MediaError(f"clip_id {clip_id!r} is already registered in this project")

    record: dict[str, Any] = {"clip_id": clip_id, "source": str(source)}

    # Derive *before* placing, because `_place` writes a symlink or a whole
    # copy under `media/` and a derivation that then fails would leave it
    # there with no clip record pointing at it. This order leaves nothing
    # behind on any failure path.
    if info.audio_streams > 1:
        # An extensionless source would hand ffmpeg an output path it cannot
        # infer a muxer from, so a container with no name for itself gets one.
        derived = project.mixed_dir / f"{clip_id}{source.suffix or '.mkv'}"
        try:
            mix_report = derive_single_audio(
                source,
                derived,
                streams=info.audio_streams,
                pick=audio_stream,
                has_video=info.has_video,
                undecodable=undecodable,
            )
        except MediaError:
            derived.unlink(missing_ok=True)
            raise
        record["mix"] = mix_report
        record["mixed"] = str(derived.relative_to(project.root))

    most_derived: Path | None = derived if "mixed" in record else None
    # More than two channels — a clip cut from a 5.1 rip — is downmixed after
    # any stream choice (a picked stream can itself be six channels) and before
    # the chapter strip, and lands in `mixed`, the key every resolver and
    # `_reel_media` already prefer: `downmix_to_stereo`'s docstring has the
    # measurement. Unconditional, like the strip — there is no second reading
    # of six channels that a stereo render could honour.
    carried = probe(most_derived).channels if most_derived is not None else info.channels
    if carried is not None and carried > 2:
        stereo_source = most_derived or source
        stereo = project.mixed_dir / f"{clip_id}.stereo{source.suffix or '.mkv'}"
        try:
            record["downmix"] = downmix_to_stereo(
                stereo_source, stereo, channels=carried, has_video=info.has_video
            )
        except MediaError:
            stereo.unlink(missing_ok=True)
            if most_derived is not None:
                most_derived.unlink(missing_ok=True)
            raise
        if most_derived is not None:
            most_derived.unlink(missing_ok=True)
        record["mixed"] = str(stereo.relative_to(project.root))
        most_derived = stereo
    if info.has_chapters:
        # Same derive-before-place ordering as the mixed-copy branch above,
        # and for the same reason: a failure here must leave nothing
        # registered. Strip the already-mixed copy when both apply, rather
        # than taking a second independent pass over the raw container — one
        # derivation feeds the next, and there is then only one "which file
        # is the untouched original" question, not two.
        strip_source = most_derived or source
        stripped = project.stripped_dir / f"{clip_id}{strip_source.suffix}"
        try:
            strip_report = strip_chapters(strip_source, stripped)
        except MediaError:
            stripped.unlink(missing_ok=True)
            raise
        record["strip"] = strip_report
        record["stripped"] = str(stripped.relative_to(project.root))
        most_derived = stripped

    record.update(_place(project, source, clip_id, copy=copy))
    record.update(info.as_dict())
    if most_derived is not None:
        # Duration correctness on the file everything downstream will
        # actually read. `info.duration` above is already the *original*
        # container's corrected figure when chapters were detected there
        # (`probe`'s own two-signal fix) — this re-probe instead closes the
        # latent gap in the derivation itself: an amix re-encode (the
        # multi-mic sum path) can drift a hair from the source it was built
        # from, and nothing before this feature ever checked. `fps`/`width`/
        # `height` need no re-probe: every derivation here stream-copies
        # video (`-c:v copy` in `derive_single_audio`, plain `-c copy` in
        # `strip_chapters`), so the picture is bit-identical either way —
        # only the audio-driven `duration` can move.
        record["duration"] = probe(most_derived).duration

    clips.append(record)
    project.write_manifest(manifest)
    return record


def _place(project: Project, source: Path, clip_id: str, *, copy: bool) -> dict[str, Any]:
    """Give the clip an entry under `media/`, if the filesystem allows one.

    SMB/CIFS shares reject `symlink()` outright with EOPNOTSUPP, and the NAS
    this is used against is one — so the link is best-effort. Falling back to a
    *copy* would be worse than useless: it silently duplicates gigabytes of
    source footage to work around a filesystem limitation. Reference the file
    where it lies instead, and record which of the three happened.
    """
    project.media_dir.mkdir(parents=True, exist_ok=True)
    local = project.media_dir / f"{clip_id}{source.suffix}"
    if local.exists() or local.is_symlink():
        local.unlink()

    if copy:
        shutil.copy2(source, local)
        return {"media": str(local.relative_to(project.root)), "link": "copy"}

    try:
        local.symlink_to(source)
    except OSError:
        # Nothing under media/ for this clip; `media_path` falls back to source.
        return {"link": "reference"}
    return {"media": str(local.relative_to(project.root)), "link": "symlink"}


def get_clip(project: Project, clip_id: str) -> dict[str, Any]:
    """Look up a registered clip, or explain which ids do exist."""
    clips = project.read_manifest().get("clips", [])
    for clip in clips:
        if clip["clip_id"] == clip_id:
            return clip
    known = ", ".join(c["clip_id"] for c in clips) or "none"
    raise MediaError(f"no clip {clip_id!r} in this project (registered: {known})")


def media_path(project: Project, clip: dict[str, Any]) -> Path:
    """The path tools should hand to ffmpeg/auto-editor for this clip.

    The `attenuated` entry when `attenuate_noises` has produced one, else the
    `stripped` entry when the container carried a chapter/data track, else
    the `mixed` entry when the container held more than one audio stream,
    else the `media/` entry, else the original source when the filesystem
    would not take a symlink. Preferring them here — rather than threading a
    "use the calm/clean copy" flag through every caller — is what makes all
    three transparent to every downstream op (seed/cut/export/verify) for
    free, and it is what keeps the writer single-stream: nothing downstream
    of import ever chooses an audio stream, so MLT's `audio_index` trap is
    never in the render path at all.

    `stripped` sits *above* `mixed`: when a container is both multi-mic and
    chaptered, the strip runs *on* the mixed copy (`import_media`'s own
    derive-before-derive ordering), so `stripped` is the more-derived of the
    two and `mixed` and `stripped` would otherwise resolve to file bytes that
    agree in every byte but the dropped chapter/data track — recording both
    and preferring the more-derived one first is what "most-derived wins"
    means, without a special case for that overlap.
    """
    local = (
        clip.get("attenuated") or clip.get("stripped") or clip.get("mixed") or clip.get("media")
    )
    return (project.root / local) if local else Path(clip["source"])


def original_media_path(project: Project, clip: dict[str, Any]) -> Path:
    """What `media_path()` resolved to before `attenuate_noises` ever ran.

    `attenuate_noises` always reads from here, never from `media_path()`, so a
    second call cannot attenuate an already-attenuated file — every run is a
    clean rebuild from the untouched original, not a compounding one.

    It keeps the `stripped`/`mixed` copies, though, which is the point of the
    split: the untouched original of a two-mic or chaptered container is the
    stripped/mixdown copy, not the raw container. Reading the container here
    would hand `attenuate_noises` a copy still carrying the parent film's
    chapter list, or mic A alone, and hand `media_path()` back a file import
    already refused to hand it silently. Same "more-derived wins" preference
    as `media_path()`, for the same reason.
    """
    local = clip.get("stripped") or clip.get("mixed") or clip.get("media")
    return (project.root / local) if local else Path(clip["source"])


# -- the preview proxy ----------------------------------------------------
#
# PLAN.md § The preview proxy transcode. The structural rule, and the whole
# reason this is a second function rather than a branch inside `media_path()`:
# **the proxy never enters the manifest, and `media_path()` gains no branch.**
#
# `media_path()` prefers the attenuated copy by reading a manifest key, and
# that is exactly what makes attenuation transparent to `export`, `verify`,
# `check_frames` and every other downstream op for free. A `proxy` key folded
# into the same chain would inherit the identical reach, and the thing it would
# reach is the render: a delivered file at preview quality. So `export` cannot
# see a proxy because it never calls `preview_path` — not because a resolution
# order was written carefully.

#: A proxy is for a `<video>` in a browser window, not for delivery, so it is
#: downscaled. Measured on a 1080x1920 5.28 Mbps render of real footage: full
#: resolution at CRF 23 costs 24.2 MB/min, this costs 3.2 MB/min and encodes
#: 2.9x faster. That 7.6x is what lets `cache/proxy/` keep one entry per clip
#: with no eviction policy — the film's whole footage proxies to ~70 MB.
#:
#: Downscaling is geometrically free here and that had to be checked rather
#: than assumed: `player.js`'s `place()` positions the element by
#: `timeline_view`'s `dest` rect in *canvas* coordinates with `objectFit:
#: fill`, and never reads `videoWidth`/`videoHeight`, so a uniform downscale
#: draws in exactly the same place. Nothing else measures a proxy's pixels.
PROXY_HEIGHT = 720
PROXY_CRF = 26


def proxy_is_current(project: Project, clip: dict[str, Any]) -> bool:
    """Does a proxy exist for `clip`, and does it still describe its source?

    Keyed by the resolved source's size and mtime, verbatim from
    `ops._cached_waveform` — cheap to check (no re-read of a multi-hundred-MB
    file) and exactly what `attenuate_noises` or a re-import changes. Keyed off
    `media_path()`'s *result*, so a proxy of the attenuated copy is a different
    entry from a proxy of the raw original rather than a stale hit.
    """
    clip_id = clip["clip_id"]
    proxy, key = project.proxy_path(clip_id), project.proxy_key_path(clip_id)
    if not proxy.is_file() or not key.is_file():
        return False
    source = media_path(project, clip)
    try:
        stat = source.stat()
        payload = json.loads(key.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("size") == stat.st_size and payload.get("mtime_ns") == stat.st_mtime_ns


def preview_path(project: Project, clip: dict[str, Any]) -> Path:
    """What the *preview* should hand a browser for this clip.

    The proxy when a current one exists, else `media_path()`. Its only callers
    are the preview side — `ops.preview_source`, `webui._send_media` — and that
    is the containment: see this section's header.

    Falling back rather than raising is deliberate. Most footage is playable
    and will never have a proxy, so "no proxy" is the ordinary case, not a
    failure; and a *stale* proxy is treated as no proxy rather than as a file
    to serve, because serving the previous cut of a re-imported clip is the
    one wrong answer that looks right.
    """
    if proxy_is_current(project, clip):
        return project.proxy_path(clip["clip_id"])
    return media_path(project, clip)


def make_proxy(
    source: Path | str,
    output: Path | str,
    *,
    height: int = PROXY_HEIGHT,
    crf: int = PROXY_CRF,
) -> Path:
    """Transcode `source` into a browser-playable `output`. One ffmpeg pass.

    Closes three of `playability()`'s four refusal classes at once — an
    unopenable container by remuxing to `.mp4`, a codec outside
    `_PLAYABLE_VIDEO`/`_PLAYABLE_AUDIO` by re-encoding to h264/aac, and a
    pixel format outside `_PLAYABLE_PIX` by forcing `yuv420p`. The fourth,
    "no streams at all", is not a codec problem and stays a refusal.

    Grown in the `energy.attenuate` idiom rather than handed to homebase's
    encoder service, which was checked live and is wrong twice over: it has no
    per-file API (three routes, a directory-walking batch daemon), and its
    output is always `libx265` tagged `hvc1`, which `_PLAYABLE_VIDEO` excludes
    under every tag. It would fail the very gate it was called to satisfy.

    `scale=-2:` and not `-1:` — H.264 needs even dimensions, and an odd width
    is a hard encoder error rather than a rounded one.
    """
    media = Path(source).expanduser()
    if not media.exists():
        raise MediaError(f"no media to transcode: {media}")

    destination = Path(output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(media),
        # Only ever downscale. `min(ih,{height})` keeps an already-small source
        # at its own size rather than upscaling it into a *larger* proxy than
        # the file it stands in for.
        "-vf", f"scale=-2:min(ih\\,{height}):flags=bicubic",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        str(destination),
    ]  # fmt: skip

    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"could not run ffmpeg: {' '.join(command)}") from exc
    if completed.returncode != 0:
        # A partial file is worse than none: `proxy_is_current` would key it
        # and serve a truncated video as a good one.
        destination.unlink(missing_ok=True)
        raise MediaError(
            f"ffmpeg could not transcode {media.name} for preview: "
            f"{completed.stderr.strip()[-800:]}"
        )
    return destination
