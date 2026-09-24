"""`caption_style` and `caption_view` — the look as project state.

The point of storing the style rather than passing it is that a restyle
survives every later edit: captions are *derived*, so regenerating after a cut
re-reads the style rather than preserving anything. Half these tests exist to
pin that down, and the other half to pin down that the two places a caption is
drawn — the `.ass` file and the window's preview — come from one derivation
and cannot disagree.

Built by hand rather than through `import_media`/`seed_timeline`, following
`test_ops_cues.py`: no ffprobe or auto-editor is needed to have a timeline
with words on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofcut import ops
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.captions import CaptionError, ass_colour
from proofcut.project import Project

CLIP = {
    "clip_id": "vo",
    "source": "/tmp/vo.wav",
    "duration": 6.0,
    "has_video": False,
    "has_audio": True,
}

WORDS = (
    ("the", 0.0, 0.3),
    ("first", 0.4, 0.8),
    ("twelve", 0.9, 1.3),
    ("minutes", 1.4, 1.9),
    ("of", 2.0, 2.2),
    ("scream", 2.3, 2.8),
)


def _seed(project: Project) -> None:
    """Lay the clip down as the whole timeline, without auto-editor."""
    edit = tl.Edit([tl.Segment("vo", 0.0, 6.0)])
    clips = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    tl.write(tl.to_otio(edit, clips, rate=1000.0), project.timeline_path)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = [CLIP]
    project.write_manifest(manifest)
    tx.save(
        tx.Transcript(
            clip_id="vo",
            words=tuple(
                tx.Word(index=i, text=t, start=a, end=b) for i, (t, a, b) in enumerate(WORDS)
            ),
        ),
        project.transcript_path("vo"),
    )
    _seed(project)
    return project


# -- reading and writing the style ---------------------------------------


def test_no_arguments_reads_without_writing(project: Project) -> None:
    """Also how a caller learns the field names, so it must not be a mutation."""
    before = project.manifest_path.stat().st_mtime_ns
    result = ops.caption_style(project.root)

    assert result["written"] is False
    assert result["changed"] == []
    assert result["stored"] == {}
    assert result["resolved"]["preset"] == "clean"
    assert project.manifest_path.stat().st_mtime_ns == before
    assert "caption_style" not in project.read_manifest()


def test_a_change_stores_only_the_override(project: Project) -> None:
    ops.caption_style(project.root, preset="boxed", size=72)

    assert project.read_manifest()["caption_style"] == {"preset": "boxed", "size": 72}


def test_changes_accumulate_rather_than_replace(project: Project) -> None:
    ops.caption_style(project.root, size=72)
    result = ops.caption_style(project.root, position="top")

    assert result["stored"] == {"size": 72, "position": "top"}
    assert result["changed"] == ["position"], "only this call's field is reported changed"


def test_reset_drops_every_override(project: Project) -> None:
    ops.caption_style(project.root, size=72, text="yellow")
    result = ops.caption_style(project.root, reset=True, preset="karaoke")

    assert result["stored"] == {"preset": "karaoke"}
    assert result["resolved"]["size"] == 64


def test_reset_alone_removes_the_key_entirely(project: Project) -> None:
    ops.caption_style(project.root, size=72)
    ops.caption_style(project.root, reset=True)

    assert "caption_style" not in project.read_manifest()


def test_plan_resolves_without_writing(project: Project) -> None:
    result = ops.caption_style(project.root, size=99, plan=True)

    assert result["written"] is False
    assert result["resolved"]["size"] == 99
    assert "caption_style" not in project.read_manifest()


def test_plan_still_refuses_a_bad_value(project: Project) -> None:
    """Otherwise `plan` would report a style the real call cannot store."""
    with pytest.raises(CaptionError, match="chartreuse"):
        ops.caption_style(project.root, text="chartreuse", plan=True)


def test_a_stored_colour_is_canonicalised(project: Project) -> None:
    """One representation in the manifest, whatever the caller typed."""
    ops.caption_style(project.root, text="yellow")
    stored = project.read_manifest()["caption_style"]

    assert stored["text"] == ass_colour("yellow")
    assert stored["text"].startswith("&H")


def test_the_style_is_echoed_in_both_vocabularies(project: Project) -> None:
    result = ops.caption_style(project.root, text="#ff0000")

    assert result["resolved"]["text"] == "#ff0000ff", "CSS, for a reader and the preview"
    assert result["ass"]["text"] == "&H000000FF", "and reversed, for the subtitle file"


def test_a_manifest_holding_something_other_than_an_object_is_refused(project: Project) -> None:
    manifest = project.read_manifest()
    manifest["caption_style"] = "boxed"
    project.write_manifest(manifest)

    with pytest.raises(CaptionError, match="JSON object"):
        ops.caption_style(project.root)


# -- the style reaching the captions -------------------------------------


def test_add_captions_takes_its_look_from_the_project(project: Project, tmp_path: Path) -> None:
    ops.caption_style(project.root, preset="boxed", size=72, position="top")
    result = ops.add_captions(project.root, tmp_path / "out.ass")

    text = (tmp_path / "out.ass").read_text()
    assert "Style: proofcut,Outfit,72," in text
    # Alignment is the 18th field after the style's Name — see to_ass's Format
    # line. 8 is top-centre on the numpad, which is what "top" resolves to.
    fields = text.split("Style: proofcut,")[1].split("\n")[0].split(",")
    assert fields[17] == "8", "top-centre"
    assert fields[14] == "3", "the boxed preset's opaque box survived the overrides"
    assert result["preset"] == "boxed"
    assert result["overrides"] == []


def test_a_restyle_survives_a_later_cut(project: Project, tmp_path: Path) -> None:
    """The property the whole design exists for: nothing about a generated
    caption file is remembered, so there is nothing for an edit to lose."""
    ops.caption_style(project.root, size=72, karaoke=True)
    ops.cut_by_transcript(project.root, "vo", cut=[(1, 2)])
    ops.add_captions(project.root, tmp_path / "after.ass")

    text = (tmp_path / "after.ass").read_text()
    assert "Style: proofcut,Outfit,72," in text
    assert "\\k" in text, "karaoke survived the cut too"


def test_an_override_is_not_written_back(project: Project, tmp_path: Path) -> None:
    """One writer for the style, and `add_captions` is not it."""
    ops.caption_style(project.root, preset="boxed")
    result = ops.add_captions(project.root, tmp_path / "out.ass", preset="karaoke", max_words=2)

    assert result["preset"] == "karaoke"
    assert result["overrides"] == ["max_words", "preset"]
    assert project.read_manifest()["caption_style"] == {"preset": "boxed"}


def test_stored_grouping_reaches_the_file(project: Project, tmp_path: Path) -> None:
    ops.caption_style(project.root, max_words=2)
    result = ops.add_captions(project.root, tmp_path / "out.ass")

    assert result["cues"] == 3, "six words, two to a line"


# -- caption_view --------------------------------------------------------


def test_caption_view_and_add_captions_group_identically(
    project: Project, tmp_path: Path
) -> None:
    """The preview draws this; the burn-in writes the other. One derivation."""
    ops.caption_style(project.root, max_words=2, karaoke=True)
    view = ops.caption_view(project.root)
    written = ops.add_captions(project.root, tmp_path / "out.ass")

    assert len(view["cues"]) == written["cues"]
    assert view["words"] == written["words"]
    assert [c["text"] for c in view["cues"]] == ["the first", "twelve minutes", "of scream"]


def test_caption_view_writes_nothing(project: Project) -> None:
    before = json.dumps(project.read_manifest(), sort_keys=True)
    ops.caption_view(project.root)

    assert json.dumps(project.read_manifest(), sort_keys=True) == before


def test_caption_view_reports_a_missing_transcript_rather_than_raising(
    tmp_path: Path,
) -> None:
    """This is the view a person has open while making exactly this mistake."""
    project = Project.create(tmp_path / "bare")
    manifest = project.read_manifest()
    manifest["clips"] = [CLIP]
    project.write_manifest(manifest)
    _seed(project)

    result = ops.caption_view(project.root)

    assert result["cues"] == []
    assert "transcript" in result["cues_error"]
    assert result["style"]["resolved"]["preset"] == "clean"


def test_caption_view_carries_the_reference_canvas(project: Project) -> None:
    """A preview scales sizes by this; the footage's own height is not it."""
    assert ops.caption_view(project.root)["resolution"] == [1920, 1080]


def test_caption_view_carries_the_em_scale_of_each_look(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """libass's `Fontsize` is the face's win ascent+descent and CSS's is its
    em, so the preview scales by this or draws Outfit a quarter too large
    (HISTORY.md § The preview's captions were a quarter too large)."""
    from proofcut import captions

    monkeypatch.setattr(captions, "em_scale", lambda name, *, bold=False: 0.5 if bold else 0.25)
    view = ops.caption_view(project.root)

    assert view["style"]["resolved"]["em_scale"] in (0.5, 0.25)
    assert [look["em_scale"] for look in view["styles"]] == [view["style"]["resolved"]["em_scale"]]


def test_the_vendored_caption_face_draws_its_em_at_1000_of_1260() -> None:
    """Outfit's win ascent+descent is 1000+260 on a 1000-unit em, read off the
    file itself — no fontconfig, so every CI runner asks the same question."""
    from proofcut import captions, fonts

    for face in ("Outfit[wght].ttf", "static/Outfit-Bold.ttf", "static/Outfit-Regular.ttf"):
        data = (fonts.VENDORED_DIR / face).read_bytes()
        assert captions._win_em_scale(data) == pytest.approx(1000 / 1260), face
    assert captions._win_em_scale(b"not a font") is None


def test_cues_are_in_timeline_seconds(project: Project) -> None:
    """The clock a viewer has. A cut before a cue moves it earlier."""
    before = ops.caption_view(project.root)["cues"][0]["start"]
    ops.cut_by_transcript(project.root, "vo", cut=[(0, 0)])
    after = ops.caption_view(project.root)["cues"][0]["start"]

    assert before == 0.0
    assert after < 0.4, "'first' now plays where 'the' used to"


# -- the reveal (DAYDREAM.md § Caption reveal and corrections, designed) ----


def test_a_reveal_is_stored_and_echoed_resolved(project: Project) -> None:
    result = ops.caption_style(project.root, reveal="blur", reveal_ms=300)
    assert result["stored"] == {"reveal": "blur", "reveal_ms": 300}
    assert result["resolved"]["reveal_blur"] == 6.0


def test_a_reveal_number_without_a_reveal_is_refused(project: Project) -> None:
    with pytest.raises(CaptionError, match="need a reveal"):
        ops.caption_style(project.root, reveal_ms=200)
    with pytest.raises(CaptionError, match="needs reveal=blur"):
        ops.caption_style(project.root, reveal="fade", reveal_blur=4)


def test_turning_the_reveal_off_takes_its_stored_numbers_with_it(project: Project) -> None:
    ops.caption_style(project.root, reveal="blur", reveal_ms=300, reveal_blur=8)
    ops.caption_style(project.root, reveal="fade")
    assert ops.caption_style(project.root)["stored"] == {"reveal": "fade", "reveal_ms": 300}
    ops.caption_style(project.root, reveal="none")
    assert ops.caption_style(project.root)["stored"] == {"reveal": "none"}


def test_the_reveal_preset_fades_words_in_mid_frame(project: Project, tmp_path: Path) -> None:
    ops.caption_style(project.root, preset="reveal")
    out = tmp_path / "c.ass"
    ops.add_captions(project.root, out)
    line = next(l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:"))
    # Every word starts transparent and fades to the style's own alphas, from
    # its own start in ms after the line's.
    assert r"{\1a&HFF&\2a&HFF&\3a&HFF&\4a&HFF&\t(0,150,\1a&H00&\2a&H00&\3a&H00&\4a&H80&)}the" in line
    assert r"\t(400,550," in line  # "first" starts 0.4 s into the line


def test_a_span_can_switch_the_projects_reveal_off(project: Project) -> None:
    ops.caption_style(project.root, reveal="blur", reveal_blur=8)
    ops.caption_span_add(project.root, "vo", 0, seconds=1.0, style={"reveal": "none"})
    assert ops.caption_view(project.root)["styles"][1]["reveal"] == "none"
