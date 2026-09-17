"""Sounds — one-shots placed at words and events (NATIVE B4).

docs/plans/NATIVE.md § B4, designed, and the spike behind it
(`~/proofcut-work/spikes/sfx-probe/FINDINGS.md`). Pinned here: a hit is
placed between frames by a padded copy's leading silence, and every copy is
a whole number of frames, at least two, so melt's two exit-0 traps (a one-frame
file plays nothing; an entry claiming past its file shortens the lane) cannot
be reached; the writer packs hits onto as few lanes as overlaps need, each
padded to the film, and leaves a document with no sounds byte-identical; the
ops layer resolves every hit through the `Edit`, skips and thins an `every`
run, refuses a cut single hit, and draws the same variants every build.

What melt plays is read back to the sample by `test_server_stdio.py`
(`test_a_sound_lands_on_the_sample_its_event_names`).
"""

from __future__ import annotations

import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from proofcut import mlt, ops
from proofcut import sounds as snd
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError


def _wav(path: Path, samples: int, *, at: int | None = None, rate: int = 48000) -> Path:
    frames = bytearray(2 * samples)
    if at is not None:
        frames[2 * at : 2 * at + 2] = (20000).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return path


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    Project.create(root)
    ops.import_media(root, _wav(tmp_path / "vo.wav", 6 * 48000), sheet=False)
    # 1440 samples: under one frame at 30 fps, the length melt plays nothing of.
    ops.import_media(root, _wav(tmp_path / "click.wav", 1440, at=0), sheet=False)
    ops.import_media(root, _wav(tmp_path / "tock.wav", 2400, at=0), sheet=False)
    project = Project.open(root)
    tx.save(
        tx.Transcript(
            clip_id="vo",
            words=tuple(
                tx.Word(index=i, text=t, start=s, end=e)
                for i, (t, s, e) in enumerate(
                    [("it", 0.0, 0.3), ("hears", 0.5, 0.9), ("the", 1.0, 1.2), ("start", 2.0, 2.4)]
                )
            ),
        ),
        project.transcript_path("vo"),
    )
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    tl.write(tl.to_otio(tl.Edit([tl.Segment("vo", 0.0, 6.0)]), clips, rate=1000.0), project.timeline_path)
    for at in (1.0, 1.02, 1.1, 1.2, 3.5):
        ops.events(root, "vo", name="key", at=at)
    ops.events(root, "vo", name="sent", at=2.5)
    return Project.open(root)


def _cut(project: Project, start: float, end: float) -> None:
    edit = ops._load_edit(project)
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    edit.remove("vo", start, end)
    tl.write(tl.to_otio(edit, clips, rate=1000.0), project.timeline_path)


# -- placement arithmetic -------------------------------------------------------


@pytest.mark.parametrize("rate", [(30, 1), (30000, 1001), (25, 1)])
def test_a_hit_lands_on_its_frame_plus_a_whole_millisecond_lead(rate: tuple[int, int]) -> None:
    for seconds in (0.0, 1.0, 1.0101, 2.3456, 59.999):
        frame, lead = snd.place(seconds, rate)
        start = snd.frame_start(frame, rate)
        assert start <= round(seconds * 48000) < snd.frame_start(frame + 1, rate)
        assert lead % snd.LEAD_STEP == 0
        assert abs(start + lead - seconds * 48000) <= snd.LEAD_STEP / 2 + 1


def test_frame_starts_are_the_floor_measured_on_both_melts() -> None:
    # sfx-probe/ntsc.py: frame 60 at 29.97 starts at sample 96096.
    assert snd.frame_start(60, (30000, 1001)) == 96096
    assert snd.frame_start(61, (30000, 1001)) == 97697


@pytest.mark.parametrize("rate", [(30, 1), (30000, 1001)])
def test_a_copy_is_whole_frames_and_never_one(tmp_path: Path, rate: tuple[int, int]) -> None:
    num, den = rate
    for samples, lead in ((10, 0), (1440, 0), (1440, 1584), (5000, 480)):
        dest = tmp_path / f"{samples}-{lead}-{num}.wav"
        frames = snd.padded_copy(bytes(4 * samples), dest, lead, rate, mlt.SOUND_MIN_FRAMES)
        with wave.open(str(dest)) as read:
            length = read.getnframes()
        assert frames >= mlt.SOUND_MIN_FRAMES
        assert length >= lead + samples
        # melt counts round(duration × fps): exactly the frames the entry claims.
        assert round(length * num / (48000 * den)) == frames
        assert (length - 1) * num < frames * 48000 * den


def test_the_generated_set_is_the_launch_clips_and_repeats_byte_for_byte(tmp_path: Path) -> None:
    first = snd.generate(tmp_path / "a")
    second = snd.generate(tmp_path / "b")
    assert [p.stem for p in first] == list(snd.GENERATED)
    assert all(a.read_bytes() == b.read_bytes() for a, b in zip(first, second))
    with wave.open(str(first[-2])) as land:
        assert land.getnframes() == int(48000 * 0.9)


# -- the writer ---------------------------------------------------------------


def _audio(frames: int) -> list[mlt.Entry]:
    return [mlt.Entry("/v/vo.wav", 0, frames)]


def test_hits_share_a_lane_until_they_overlap_and_every_lane_covers_the_film() -> None:
    hits = [mlt.Hit("/s/a.wav", 10, 3), mlt.Hit("/s/a.wav", 11, 3), mlt.Hit("/s/b.wav", 13, 2), mlt.Hit("/s/a.wav", 50, 3)]
    lanes = mlt.sound_lanes(hits, 100, "/s/silence.wav")
    assert len(lanes) == 2
    assert all(sum(e.frames for e in lane) == 100 for lane in lanes)
    placed = [(e.resource, sum(x.frames for x in lane[:i])) for lane in lanes for i, e in enumerate(lane) if "silence" not in e.resource]
    assert sorted(placed) == [("/s/a.wav", 10), ("/s/a.wav", 11), ("/s/a.wav", 50), ("/s/b.wav", 13)]


def test_a_hit_at_the_end_is_trimmed_and_one_past_it_refused() -> None:
    lanes = mlt.sound_lanes([mlt.Hit("/s/a.wav", 98, 5)], 100, "/s/silence.wav")
    assert [e.frames for e in lanes[0]] == [98, 2]
    with pytest.raises(mlt.MLTError, match="outside"):
        mlt.sound_lanes([mlt.Hit("/s/a.wav", 100, 5)], 100, "/s/silence.wav")


def test_no_sounds_writes_the_same_document_as_before() -> None:
    plain = mlt.to_string(mlt.document(audio=_audio(90), rate=30.0))
    assert mlt.to_string(mlt.document(audio=_audio(90), sounds=[], rate=30.0)) == plain


def test_each_sound_lane_is_a_mixed_audio_track_with_a_gain_filter() -> None:
    lanes = mlt.sound_lanes([mlt.Hit("/s/a.wav", 5, 2, -6.0), mlt.Hit("/s/a.wav", 6, 2)], 90, "/s/silence.wav")
    root = mlt.document(audio=_audio(90), sounds=lanes, rate=30.0)
    sequence = next(t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None)
    tracks = [t.get("producer") for t in sequence.findall("track")]
    assert tracks[-2:] == ["tractorS0", "tractorS1"]
    mixes = [
        t for t in sequence.findall("transition")
        if t.find("property[@name='mlt_service']").text == "mix"
    ]  # fmt: skip
    assert {m.find("property[@name='b_track']").text for m in mixes} >= {str(tracks.index("tractorS0")), str(tracks.index("tractorS1"))}
    assert not any(
        t.find("property[@name='b_track']").text == str(tracks.index("tractorS0"))
        for t in sequence.findall("transition")
        if t.find("property[@name='mlt_service']").text == "qtblend"
    )
    level = root.find(".//playlist[@id='splaylist0a']/entry/filter/property[@name='level']")
    assert level is not None and level.text == "0=-6;1=-6"
    assert all(frames == 90 for frames in mlt.declared_frames(root).values())


def test_the_writer_refuses_a_sound_lane_that_does_not_cover_the_film() -> None:
    with pytest.raises(mlt.MLTError, match="sound lane 0 covers 80"):
        mlt.document(audio=_audio(90), sounds=[[mlt.Entry("/s/x.wav", 0, 80)]], rate=30.0)


# -- the ops layer --------------------------------------------------------------


def test_every_places_a_hit_at_each_event_and_thins_the_close_ones(project: Project) -> None:
    reply = ops.sound_add(project.root, ["click", "tock"], "vo", every="key")
    sound = reply["sound"]
    # key#1 is 20 ms after key#0: under the 45 ms gap, thinned.
    assert (sound["hit_count"], sound["thinned"], sound["skipped"]) == (4, 1, 0)
    assert [h["at"] for h in sound["hits"]] == [1.0, 1.1, 1.2]
    assert sound["echo"] == {"every": "key", "occurrences": 5}


def test_the_picks_and_the_jitter_repeat_every_build(project: Project) -> None:
    ops.sound_add(project.root, ["click", "tock"], "vo", every="key", jitter_db=3.0, gain_db=-12.0)
    first = ops._sound_plan(project, ops._load_edit(project))[0]["hits"]
    again = ops._sound_plan(project, ops._load_edit(project))[0]["hits"]
    assert first == again
    assert all(-15.0 <= h["gain_db"] <= -9.0 for h in first)
    assert len({h["gain_db"] for h in first}) > 1


def test_a_cut_skips_an_every_hit_and_keeps_the_others_draws(project: Project) -> None:
    ops.sound_add(project.root, ["click", "tock"], "vo", every="key", jitter_db=3.0)
    before = ops._sound_plan(project, ops._load_edit(project))[0]["hits"]
    _cut(project, 1.15, 1.25)
    plan = ops._sound_plan(project, ops._load_edit(project))[0]
    assert plan["skipped"] == 1
    after = {h["address"]: h for h in plan["hits"]}
    for hit in before:
        if hit["address"] in after:
            assert (after[hit["address"]]["asset"], after[hit["address"]]["gain_db"]) == (hit["asset"], hit["gain_db"])
    assert after["key#4"]["at"] == pytest.approx(3.4)


def test_a_single_hit_at_a_cut_event_or_word_refuses(project: Project) -> None:
    ops.sound_add(project.root, "tock", "vo", event="sent")
    ops.sound_add(project.root, "tock", "vo", phrase="start")
    _cut(project, 2.3, 2.7)
    with pytest.raises(ProjectError, match="event 'sent'.*a cut removed"):
        ops._sound_plan(project, ops._load_edit(project))
    view = ops.timeline_view(project.root, "vo")
    assert view["sounds"] == [] and "a cut removed" in view["sounds_error"]
    assert ops.sound_ls(project.root)["sounds_error"]


def test_a_word_places_the_hit_where_the_word_starts(project: Project) -> None:
    reply = ops.sound_add(project.root, "tock", "vo", phrase="hears")
    assert reply["sound"]["word_index"] == 1
    assert reply["sound"]["hits"][0]["at"] == 0.5
    assert reply["sound"]["echo"]["text"] == "hears"


def test_what_is_not_a_sound_is_refused_before_writing(project: Project) -> None:
    manifest = project.read_manifest()
    manifest["clips"].append({"clip_id": "mute", "source": "/tmp/mute.mp4", "duration": 1.0, "has_video": True, "has_audio": False})
    project.write_manifest(manifest)
    for kwargs, match in (
        ({"assets": "card:x", "event": "sent"}, "is a card"),
        ({"assets": "mute", "event": "sent"}, "no audio"),
        ({"assets": "nope", "event": "sent"}, "nope"),
        ({"assets": "tock", "every": "nothing"}, "no 'nothing' events"),
        ({"assets": "tock", "event": "sent", "every": "key"}, "one of"),
        ({"assets": "tock", "event": "sent", "min_gap": 0.1}, "thins an every run"),
        ({"assets": [], "event": "sent"}, "at least one"),
    ):
        with pytest.raises((ProjectError, Exception), match=match):
            ops.sound_add(project.root, clip_id="vo", **kwargs)
    assert "sounds" not in Project.open(project.root).read_manifest()


def test_plan_writes_nothing_and_remove_takes_one_off(project: Project) -> None:
    ops.sound_add(project.root, "tock", "vo", event="sent", plan=True)
    assert "sounds" not in project.read_manifest()
    ops.sound_add(project.root, "tock", "vo", event="sent")
    ops.sound_add(project.root, "click", "vo", every="key")
    assert [s["hit_count"] for s in ops.sound_ls(project.root)["sounds"]] == [1, 4]
    removed = ops.sound_rm(project.root, 0)
    assert removed["removed"]["event"] == "sent" and removed["count"] == 1
    ops.sound_rm(project.root, 0)
    assert "sounds" not in project.read_manifest()
    with pytest.raises(ProjectError, match="no sound at position 0"):
        ops.sound_rm(project.root, 0)


def test_a_sound_routes_export_through_the_writer_with_padded_copies(project: Project) -> None:
    ops.sound_add(project.root, ["click", "tock"], "vo", every="key", gain_db=-6.0)
    assert ops._is_layered(project, ops._load_edit(project))
    built = ops._build_mlt(project, ops._load_edit(project), fps=None)
    assert built["sounds"] == [
        {"position": 0, "assets": ["click", "tock"], "clip_id": "vo", "every": "key",
         "skipped": 0, "thinned": 1, "hits": 4, "lanes": 1}
    ]  # fmt: skip
    root = built["document"]
    entries = root.findall(".//playlist[@id='splaylist0a']/entry")
    chains = {c.get("id"): c.find("property[@name='resource']").text for c in root.findall("chain")}
    # A resource is written with the OS's own separator, so match on parts: a
    # "cache/sounds" substring finds none of them on Windows.
    def is_copy(resource: str) -> bool:
        return Path(resource).parent.parts[-2:] == ("cache", "sounds")

    copies = [chains[e.get("producer")] for e in entries if is_copy(chains[e.get("producer")])]
    assert len(copies) == 4
    for entry in entries:
        resource = chains[entry.get("producer")]
        if not is_copy(resource):
            continue
        with wave.open(resource) as read:
            length = read.getnframes()
        claimed = int(entry.get("out")) + 1
        assert claimed >= 2 and round(length * 30 / 48000) == claimed
    # Positions: frame starts are where the hits' frames are, and the copy's
    # lead is the rest.
    starts, cursor = [], 0
    for entry in entries:
        frames = int(entry.get("out")) - int(entry.get("in")) + 1
        if is_copy(chains[entry.get("producer")]):
            starts.append(cursor)
        cursor += frames
    assert starts == [30, 33, 36, 105]
    assert isinstance(ET.tostring(root), bytes)


def test_the_view_carries_every_hit_for_the_window(project: Project) -> None:
    ops.sound_add(project.root, "tock", "vo", every="key")
    view = ops.timeline_view(project.root, "vo")
    assert [h["at"] for h in view["sounds"]] == [1.0, 1.1, 1.2, 3.5]
    assert "sounds_error" not in view


def test_a_reel_drops_the_sounds_and_names_them(project: Project, tmp_path: Path) -> None:
    ops.sound_add(project.root, "tock", "vo", event="sent")
    report = ops.reel(project.root, tmp_path / "reel", start=0.0, end=3.0, plan=True)
    assert [s["event"] for s in report["sounds_dropped"]] == ["sent"]


def test_generate_writes_and_imports_the_set_once(project: Project) -> None:
    first = ops.sound_generate(project.root)
    assert [c["clip_id"] for c in first["clips"]][-3:] == ["sfx-send", "sfx-land", "sfx-strike"]
    assert all(c["imported"] for c in first["clips"])
    again = ops.sound_generate(project.root)
    assert not any(c["imported"] for c in again["clips"])
    reply = ops.sound_add(project.root, [f"sfx-key_{i}" for i in range(8)], "vo", every="key")
    assert reply["sound"]["hit_count"] == 4
