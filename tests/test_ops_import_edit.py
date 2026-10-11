"""`import-edit` — the supported way to bring an outside cut in.

The gap this closes is named in PLAN.md § Open questions, *How does a lucid
project know it is the film*: the Scream retake pass was done in Kdenlive, and
bringing it back meant 63 ranges parsed by hand and written straight to `Edit`,
bypassing `cut` and its history entirely (HISTORY.md § The VO the project was
holding).

The reader's own arithmetic is pinned in test_mlt.py. What is pinned here is
everything the *project* adds to it — which clip a resource resolves to, what
happens to a range that overruns its clip, and that the timeline it replaces is
snapshotted first. One test round-trips a real `auto-editor --export kdenlive`
file rather than a hand-written one, because the hand-written fixtures agree
with the reader by construction and a real export is the only thing that can
disagree with it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from proofcut import autoeditor, ops
from proofcut.project import ProjectError

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="the fixtures need ffmpeg and ffprobe",
)


def _has_auto_editor() -> bool:
    try:
        autoeditor.binary()
    except autoeditor.AutoEditorError:
        return False
    return True


needs_auto_editor = pytest.mark.skipif(
    not _has_auto_editor(), reason="auto-editor is not installed"
)

DOC = """<mlt root="{root}">
  <profile frame_rate_num="30" frame_rate_den="1" width="320" height="240" />
  <producer id="producer0">
    <property name="resource">black</property>
    <property name="mlt_service">color</property>
  </producer>
  <chain id="chain0"><property name="resource">{resource}</property></chain>
  <playlist id="playlist0">{entries}</playlist>
</mlt>
"""


def _entries(spans: list[tuple[int, int]]) -> str:
    return "".join(f'<entry producer="chain0" in="{a}" out="{b}"/>' for a, b in spans)


def _media(path: Path, *, seconds: float = 12.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=48000",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-y", str(path),
        ],
        check=True,
    )
    return path


def _project(tmp_path: Path, *, seconds: float = 12.0) -> tuple[Path, Path, dict[str, Any]]:
    source = _media(tmp_path / "vo.mp4", seconds=seconds)
    root = tmp_path / "proj"
    ops.init(root)
    clip = ops.import_media(root, source)
    return root, source, clip


def _write_doc(tmp_path: Path, source: Path, spans: list[tuple[int, int]]) -> Path:
    document = tmp_path / "cut.kdenlive"
    document.write_text(
        DOC.format(root=source.parent, resource=source.name, entries=_entries(spans)),
        encoding="utf-8",
    )
    return document


@needs_ffmpeg
def test_an_outside_cut_becomes_the_timeline(tmp_path: Path) -> None:
    root, source, _ = _project(tmp_path)
    document = _write_doc(tmp_path, source, [(0, 66), (114, 216)])

    report = ops.import_edit(root, document)

    assert report["ranges"] == 2
    assert report["segments"] == 2
    assert report["clips"] == ["vo"]
    assert report["rate"] == 30.0
    # 67 + 103 frames at 30fps — the reader's inclusive `out`, arriving intact.
    assert report["timeline_duration"] == pytest.approx(170 / 30)
    assert report["overshot"] == []
    assert ops.status(root)["segments"] == 2


@needs_ffmpeg
def test_plan_resolves_the_same_numbers_without_writing(tmp_path: Path) -> None:
    """`--plan` on a *replacing* op matters more than on an additive one: the
    thing it does not do is overwrite a timeline somebody spent a day on."""
    root, source, _ = _project(tmp_path)
    ops.seed_timeline(root, "vo", remove_silences=False)
    before = ops.status(root)
    document = _write_doc(tmp_path, source, [(0, 66), (114, 216)])

    planned = ops.import_edit(root, document, plan=True)

    assert planned["plan"] is True
    assert planned["segments"] == 2
    assert ops.status(root)["segments"] == before["segments"] == 1
    assert ops.status(root)["timeline_duration"] == pytest.approx(before["timeline_duration"])


@needs_ffmpeg
def test_the_replaced_timeline_is_snapshotted_first(tmp_path: Path) -> None:
    """The whole point of routing this through `_save_edit`. The hand-rolled
    version wrote `Edit` directly and left nothing to undo to."""
    root, source, _ = _project(tmp_path)
    ops.seed_timeline(root, "vo", remove_silences=False)
    depth = ops.status(root)["undo_depth"]
    document = _write_doc(tmp_path, source, [(0, 66)])

    report = ops.import_edit(root, document)

    assert report["undo_depth"] == depth + 1
    ops.undo(root)
    assert ops.status(root)["segments"] == 1


@needs_ffmpeg
def test_a_range_past_the_end_of_the_clip_is_clamped_and_named(tmp_path: Path) -> None:
    """**Not a hypothetical.** auto-editor's own exports run one frame past the
    last frame of the source, so a clean `overshot` is a finding rather than
    the default. Clamping silently would make the import shorter than the file
    it was read from, with nothing saying so — the same silence this op exists
    to end.
    """
    root, source, _ = _project(tmp_path)  # 12.0s = frames 0..359
    document = _write_doc(tmp_path, source, [(264, 360)])

    report = ops.import_edit(root, document)

    assert len(report["overshot"]) == 1
    overshot = report["overshot"][0]
    assert overshot["clip_id"] == "vo"
    assert overshot["asked_end"] == pytest.approx(361 / 30)
    assert overshot["clamped_to"] == pytest.approx(12.0)
    assert overshot["frames"] == pytest.approx(1.0, abs=0.01)
    assert report["timeline_duration"] == pytest.approx(12.0 - 264 / 30)


@needs_ffmpeg
def test_unregistered_media_is_named_never_imported_behind_your_back(tmp_path: Path) -> None:
    """Importing media as a side effect of importing an edit would make one op
    that reaches ffprobe, writes the manifest and replaces the timeline. The
    refusal names the file and where it was looked for, because a resource
    that did not resolve and one that resolved somewhere unexpected read the
    same otherwise.
    """
    root, _source, _ = _project(tmp_path)
    document = tmp_path / "other.kdenlive"
    document.write_text(
        DOC.format(root=tmp_path, resource="nowhere.mp4", entries=_entries([(0, 66)])),
        encoding="utf-8",
    )

    with pytest.raises(ProjectError) as excinfo:
        ops.import_edit(root, document)
    assert "has not registered" in str(excinfo.value)
    assert "nowhere.mp4" in str(excinfo.value)
    assert "looked for" in str(excinfo.value)


@needs_ffmpeg
def test_clip_id_maps_a_single_source_document_whose_path_moved(tmp_path: Path) -> None:
    """The document was written on another machine, or against a copy. One
    resource means there is no ambiguity about which clip it is."""
    root, _, _ = _project(tmp_path)
    document = tmp_path / "moved.kdenlive"
    document.write_text(
        DOC.format(root="/elsewhere", resource="vo.mp4", entries=_entries([(0, 66)])),
        encoding="utf-8",
    )

    report = ops.import_edit(root, document, clip_id="vo")
    assert report["clips"] == ["vo"]
    assert report["segments"] == 1


@needs_ffmpeg
def test_clip_id_is_refused_against_a_multi_source_document(tmp_path: Path) -> None:
    """One name cannot answer for two resources, and picking the first would
    map somebody's B-roll onto their voiceover."""
    root, source, _ = _project(tmp_path)
    document = tmp_path / "two.kdenlive"
    document.write_text(
        f'<mlt root="{source.parent}">'
        '<profile frame_rate_num="30" frame_rate_den="1" />'
        '<chain id="chain0"><property name="resource">vo.mp4</property></chain>'
        '<chain id="chain1"><property name="resource">broll.mp4</property></chain>'
        '<playlist id="playlist0">'
        '<entry producer="chain0" in="0" out="66"/>'
        '<entry producer="chain1" in="0" out="66"/>'
        "</playlist></mlt>",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="cannot say which is which"):
        ops.import_edit(root, document, clip_id="vo")


@needs_ffmpeg
def test_a_document_that_is_not_mlt_is_refused_by_its_root_tag(tmp_path: Path) -> None:
    root, _, _ = _project(tmp_path)
    document = tmp_path / "not.kdenlive"
    document.write_text("<project><clip/></project>", encoding="utf-8")

    with pytest.raises(ProjectError, match="not <mlt>"):
        ops.import_edit(root, document)

    broken = tmp_path / "broken.kdenlive"
    broken.write_text("<mlt", encoding="utf-8")
    with pytest.raises(ProjectError, match="not readable as XML"):
        ops.import_edit(root, broken)


@needs_ffmpeg
@needs_auto_editor
def test_a_real_auto_editor_export_round_trips_to_its_own_v3_numbers(tmp_path: Path) -> None:
    """**The only fixture that can disagree with the reader.** Every
    hand-written document above agrees with `read_ranges` by construction; a
    real `--export kdenlive` is written by something that has never seen this
    code. auto-editor emits the same cut in two formats, and proofcut already
    trusts `autoeditor.from_v3` — so the v3 export is the reference the
    kdenlive import is scored against, and the two agreeing frame-for-frame is
    what says the inclusive `out` was read right.
    """
    source = _media(tmp_path / "gappy.mp4")
    root = tmp_path / "proj"
    ops.init(root)
    ops.import_media(root, source)

    binary = autoeditor.binary()
    subprocess.run(
        [binary, str(source), "--export", "kdenlive", "-o", str(tmp_path / "cut.kdenlive")],
        cwd=tmp_path, check=False, capture_output=True,
    )
    subprocess.run(
        [binary, str(source), "--export", "v3", "-o", str(tmp_path / "cut.v3")],
        cwd=tmp_path, check=False, capture_output=True,
    )
    document = tmp_path / "cut.kdenlive"
    v3_path = tmp_path / "cut.v3"
    if not document.is_file() or not v3_path.is_file():
        pytest.skip("this auto-editor build did not write both exports")

    payload = json.loads(v3_path.read_text(encoding="utf-8"))
    reference = autoeditor.from_v3(payload, "vo")

    report = ops.import_edit(root, document, plan=True)

    assert report["ranges"] == len(reference.segments), (
        "the two exports describe the same cut, so they hold the same number of ranges"
    )
    # The one place they are allowed to differ is the tail auto-editor overruns,
    # which the import clamps and `from_v3` does not — so add it back before
    # comparing, rather than loosening the tolerance until both fit under it.
    clamped_away = sum(o["asked_end"] - o["clamped_to"] for o in report["overshot"])
    assert report["timeline_duration"] + clamped_away == pytest.approx(
        reference.duration, abs=1e-6
    )


@needs_ffmpeg
def test_the_document_is_checked_against_its_own_declared_length(tmp_path: Path) -> None:
    """**The check that would have caught the hand-parse.** Every range in a
    misread document is individually plausible — the only thing wrong is the
    total, and only against what the document says about itself. A Kdenlive
    file states its length in three places, so reading `out` as exclusive
    disagrees with all three at once and by exactly one frame per range.
    """
    root, source, _ = _project(tmp_path)
    document = tmp_path / "declared.kdenlive"
    entries = _entries([(0, 66), (114, 216)])
    document.write_text(
        f'<mlt root="{source.parent}">'
        '<profile frame_rate_num="30" frame_rate_den="1" />'
        '<producer id="producer0">'
        "<property name=\"length\">00:00:05.667</property>"
        '<property name="resource">black</property>'
        '<property name="mlt_service">color</property>'
        "</producer>"
        '<chain id="chain0"><property name="resource">vo.mp4</property></chain>'
        f'<playlist id="playlist0">{entries}</playlist>'
        '<tractor id="tractor0" out="00:00:05.667"/>'
        "</mlt>",
        encoding="utf-8",
    )

    report = ops.import_edit(root, document, plan=True)

    assert report["document_frames"] == 170  # 67 + 103, the inclusive read
    assert report["declared_frames"] == {"producer0 length": 170, "tractor tractor0 out": 170}
    assert report["declares_otherwise"] == [], "the document agrees with itself"


@needs_ffmpeg
def test_a_document_that_contradicts_itself_says_which_claim_disagrees(tmp_path: Path) -> None:
    """Named rather than raised: a disagreement is a fact about the file, and
    which of the two numbers is right is not proofcut's to decide."""
    root, source, _ = _project(tmp_path)
    document = tmp_path / "wrong.kdenlive"
    document.write_text(
        f'<mlt root="{source.parent}">'
        '<profile frame_rate_num="30" frame_rate_den="1" />'
        '<producer id="producer0">'
        "<property name=\"length\">00:00:10.000</property>"
        '<property name="resource">black</property>'
        '<property name="mlt_service">color</property>'
        "</producer>"
        '<chain id="chain0"><property name="resource">vo.mp4</property></chain>'
        f'<playlist id="playlist0">{_entries([(0, 66)])}</playlist>'
        "</mlt>",
        encoding="utf-8",
    )

    report = ops.import_edit(root, document, plan=True)

    assert report["document_frames"] == 67
    assert report["declares_otherwise"] == ["producer0 length"]


def _write_silence_doc(tmp_path: Path, source: Path) -> Path:
    """The shape `vo_extend.py` leaves in a Kdenlive VO timeline: a `silence`
    producer with no resource, entered before the first range and after the
    last — Scream's `final v3 + outro.kdenlive` opens on 1.467 s of one and
    ends on 6.367 s."""
    document = tmp_path / "padded.kdenlive"
    document.write_text(
        f'<mlt root="{source.parent}">'
        '<profile frame_rate_num="30" frame_rate_den="1" />'
        '<producer id="silence0"><property name="mlt_service">silence</property>'
        '<property name="length">00:00:12.400</property></producer>'
        f'<chain id="chain0"><property name="resource">{source.name}</property></chain>'
        '<playlist id="playlist0">'
        '<entry producer="silence0" in="0" out="44"/>'
        '<entry producer="chain0" in="0" out="66"/>'
        '<entry producer="silence0" in="0" out="29"/>'
        "</playlist>"
        '<tractor id="tractor0" out="00:00:04.733"/>'
        "</mlt>",
        encoding="utf-8",
    )
    return document


@needs_ffmpeg
def test_a_silence_entry_is_runtime_and_imports_as_silence(tmp_path: Path) -> None:
    """A silence entry is runtime the cut plays, exactly as a `<blank>` is —
    and skipped as "not media", it closed silently: every cue after a 1.5 s
    head of silence landed 1.5 s early, with `declares_otherwise` the only
    trace (HISTORY.md § The Scream native rebuild). It comes in as generated
    silence, the file `vo_extend` splices, so the timeline is the document's."""
    root, source, _ = _project(tmp_path)
    document = _write_silence_doc(tmp_path, source)
    clips_before = {c["clip_id"] for c in ops.assets(root)["clips"]}

    planned = ops.import_edit(root, document, plan=True)

    assert planned["document_frames"] == 45 + 67 + 30
    assert planned["declares_otherwise"] == []
    assert planned["timeline_duration"] == pytest.approx(142 / 30)
    assert [s["seconds"] for s in planned["silences"]] == [pytest.approx(1.5), pytest.approx(1.0)]
    assert {c["clip_id"] for c in ops.assets(root)["clips"]} == clips_before, "a plan registers nothing"

    report = ops.import_edit(root, document)

    assert report["segments"] == 3
    assert ops.status(root)["timeline_duration"] == pytest.approx(142 / 30)
    view = ops.timeline_view(root)
    first, middle, last = view["segments"]
    assert middle["clip_id"] == "vo"
    assert first["clip_id"] != "vo" and last["clip_id"] != "vo"
    registered = {c["clip_id"] for c in ops.assets(root)["clips"]} - clips_before
    assert registered == {first["clip_id"], last["clip_id"]}


# --- OTIO: a cut another editor wrote ---------------------------------------
#
# COMPETITORS.md § FilmCraft, run against `verify`: FilmCraft's OTIO export is
# exact but carries an empty `Video 1` ahead of the `A1` that holds the clips,
# writes fractional frames at rate 24, and drops proofcut's metadata. Each
# document below is built in that shape. The real export was imported by hand
# on 2026-10-11 and gave the same `verify` numbers as the shimmed run.


def _otio_doc(
    path: Path,
    tracks: list[tuple[str, list[Any]]],
    *,
    rate: float = 24.0,
) -> Path:
    import opentimelineio as otio

    timeline = otio.schema.Timeline(name="outside")
    for name, items in tracks:
        kind = otio.schema.TrackKind.Video if name.startswith("V") else otio.schema.TrackKind.Audio
        track = otio.schema.Track(name=name, kind=kind)
        for item in items:
            if isinstance(item, float):
                track.append(otio.schema.Gap(duration=otio.opentime.RationalTime(item * rate, rate)))
                continue
            url, start, end = item
            track.append(
                otio.schema.Clip(
                    name=f"clip-{len(track)}",
                    media_reference=otio.schema.ExternalReference(target_url=url),
                    source_range=otio.opentime.TimeRange(
                        otio.opentime.RationalTime(start * rate, rate),
                        otio.opentime.RationalTime((end - start) * rate, rate),
                    ),
                )
            )
        timeline.tracks.append(track)
    otio.adapters.write_to_file(timeline, str(path))
    return path


@needs_ffmpeg
def test_an_otio_cut_with_an_empty_track_first_becomes_the_timeline(tmp_path: Path) -> None:
    root, source, _ = _project(tmp_path)
    url = source.resolve().as_uri()
    # 91.2 frames at 24 fps: FilmCraft's fractional frames, which must come back to the millisecond.
    document = _otio_doc(
        tmp_path / "cut.otio",
        [("Video 1", []), ("A1", [(url, 0.0, 3.8), (url, 9.16, 9.366), (url, 9.933, 11.5)])],
    )

    report = ops.import_edit(root, document)

    assert report["ranges"] == 3
    assert report["rate"] == 24.0
    assert report["clips"] == ["vo"]
    assert report["timeline_duration"] == pytest.approx(3.8 + 0.206 + 1.567, abs=1e-9)
    assert report["declared_frames"] == {}
    assert ops.status(root)["segments"] == 3


@needs_ffmpeg
def test_linked_otio_tracks_must_carry_the_same_cut(tmp_path: Path) -> None:
    root, source, _ = _project(tmp_path)
    url = source.resolve().as_uri()
    same = [(url, 1.0, 3.0), (url, 5.0, 8.0)]
    document = _otio_doc(tmp_path / "linked.otio", [("V1", same), ("A1", same)])
    assert ops.import_edit(root, document, plan=True)["segments"] == 2

    differ = _otio_doc(tmp_path / "split.otio", [("V1", same), ("A1", [(url, 1.0, 3.0)])])
    with pytest.raises(ProjectError, match="carry different cuts"):
        ops.import_edit(root, differ)


@needs_ffmpeg
def test_an_otio_gap_is_refused_rather_than_closed(tmp_path: Path) -> None:
    root, source, _ = _project(tmp_path)
    url = source.resolve().as_uri()
    document = _otio_doc(tmp_path / "gap.otio", [("A1", [(url, 0.0, 2.0), 1.5, (url, 4.0, 6.0)])])
    with pytest.raises(ProjectError, match="gap of 1.500s"):
        ops.import_edit(root, document)


@needs_ffmpeg
def test_an_otio_reference_resolves_relative_to_the_document(tmp_path: Path) -> None:
    root, source, _ = _project(tmp_path)
    document = _otio_doc(tmp_path / "rel.otio", [("A1", [(source.name, 0.0, 2.0)])])
    assert ops.import_edit(root, document)["clips"] == ["vo"]


@needs_ffmpeg
def test_unregistered_otio_media_is_named(tmp_path: Path) -> None:
    root, _, _ = _project(tmp_path)
    stranger = (tmp_path / "elsewhere.mp4").resolve().as_uri()
    document = _otio_doc(tmp_path / "stranger.otio", [("A1", [(stranger, 0.0, 2.0)])])
    with pytest.raises(ProjectError, match="has not registered"):
        ops.import_edit(root, document)


@needs_ffmpeg
def test_proofcuts_own_otio_imports_as_the_same_edit(tmp_path: Path) -> None:
    """The handoff's other end: what proofcut writes, another project reads."""
    root, _, _ = _project(tmp_path)
    ops.seed_timeline(root, "vo", remove_silences=False)
    ops.cut_by_time(root, spans=[[2.0, 4.5]])
    exported = tmp_path / "handoff.otio"
    shutil.copy(root / "project.otio", exported)
    before = ops.status(root)

    other = tmp_path / "other"
    ops.init(other)
    ops.import_media(other, tmp_path / "vo.mp4")
    report = ops.import_edit(other, exported)

    assert report["segments"] == before["segments"]
    assert report["timeline_duration"] == pytest.approx(before["timeline_duration"], abs=1e-6)


def test_an_unreadable_otio_is_refused_as_otio(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    ops.init(root)
    bad = tmp_path / "bad.otio"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectError, match="not readable as OTIO"):
        ops.import_edit(root, bad)
