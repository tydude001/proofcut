"""The render log's second writer — `export`/`add_captions`, for the clients with no pipeline.

TRIAL.md § 2: `renderlog.append` was called in exactly one place, `webui.py`,
so every render made through the CLI or the MCP server left no log and
`finish_report` answered `captions.burned: "unknown"` on films whose captions
were demonstrably burned in. An agent hit that in a real trial and settled the
question with `spot_frames` instead — the right instinct, and a flag that is
dead weight for two of three clients teaches a caller to ignore the field.

The fixture is `test_ops_finish_report.py`'s, for the same reason: a real
`Project` and a hand-written `Edit`, no ffmpeg. The two heavy seams —
auto-editor's render and ffmpeg's burn — are monkeypatched to write a stub
file, because what is under test is *what gets recorded about a render*, not
the render.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from proofcut import autoeditor, captions, ops, renderlog, server
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """One VO clip with a transcript, cut to two surviving ranges."""
    project = Project.create(tmp_path / "proj")
    manifest = project.read_manifest()
    manifest["clips"] = [
        {
            "clip_id": "vo",
            "source": str(tmp_path / "vo.wav"),
            "duration": 4.0,
            "has_video": False,
            "has_audio": True,
        }
    ]
    project.write_manifest(manifest)
    (tmp_path / "vo.wav").write_bytes(b"not really audio, just needs to exist")

    edit = tl.Edit([tl.Segment("vo", 0.0, 1.0), tl.Segment("vo", 2.0, 3.5)])
    clips_by_id = {c["clip_id"]: c for c in manifest["clips"]}
    tl.write(tl.to_otio(edit, clips_by_id, rate=1000.0, name="proj"), project.timeline_path)

    tx.save(
        tx.Transcript(
            clip_id="vo",
            words=tuple(
                tx.Word(index=i, text=t, start=s, end=e)
                for i, (t, s, e) in enumerate(
                    [("cold", 0.0, 0.3), ("open", 0.5, 0.8), ("after", 2.2, 2.5)]
                )
            ),
        ),
        project.transcript_path("vo"),
    )
    return project


@pytest.fixture
def stub_render(monkeypatch: pytest.MonkeyPatch):
    """auto-editor's subprocess, replaced by a file appearing where it would.

    `_render_single` is the seam because it is the last thing between `export`
    and the encoder; everything above it — the preset/resolution refusals, the
    v3 payload, the reply — is the code under test and runs for real.
    """

    def _fake(payload: dict[str, Any], output: Any, **kwargs: Any) -> tuple[Path, dict[str, Any]]:
        written = Path(output)
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_bytes(b"rendered")
        return written, {}

    monkeypatch.setattr(ops, "_render_single", _fake)
    # `template` probes the media with the real auto-editor binary, and this
    # project's clip is a stub file. `to_v3` only reads the timebase off the
    # header, so a minimal one is enough to exercise everything above the seam.
    monkeypatch.setattr(
        autoeditor, "template", lambda media: {"version": "3", "timebase": "30/1"}
    )


@pytest.fixture
def stub_burn(monkeypatch: pytest.MonkeyPatch):
    """ffmpeg's burn, replaced by the burned file appearing where it would."""

    def _fake(source: Path, ass: Path, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"burned")
        return target

    monkeypatch.setattr(captions, "burn", _fake)


# -- export -------------------------------------------------------------------


def test_a_media_render_records_itself(project: Project, stub_render: None) -> None:
    out = project.render_dir / "cut.wav"
    ops.export(project.root, out, export_format=None)

    run = renderlog.last(project)
    assert run is not None
    assert run["output"] == str(out)
    assert run["stages"] == {"export": {"outcome": "done", "detail": None}}
    assert run["expected_duration"] == ops.status(str(project.root))["expected_duration"]


def test_an_nle_export_records_nothing(
    project: Project, tmp_path: Path, stub_render: None
) -> None:
    """There is no render for a burn to have been true of — an MLT project
    file is a handoff, and logging one would put a run in the log that
    `finish_report` would then answer `captions.burned` from."""
    ops.export(project.root, tmp_path / "proj.kdenlive", export_format="kdenlive")

    assert renderlog.last(project) is None


def test_the_preset_is_recorded_with_the_run(project: Project, stub_render: None) -> None:
    out = project.render_dir / "cut.wav"
    ops.export(project.root, out, export_format=None, preset="web")

    assert renderlog.last(project)["preset"] == "web"


def test_log_false_is_the_web_ui_s_opt_out(project: Project, stub_render: None) -> None:
    """`RenderJob` appends the whole run itself at every exit path. If this
    call logged too, `renderlog.last` would read an export with no burn stage
    — a prefix of the run — instead of the run."""
    ops.export(project.root, project.render_dir / "cut.wav", export_format=None, log=False)

    assert renderlog.last(project) is None


# -- burn ---------------------------------------------------------------------


def test_a_burn_continues_the_run_the_export_opened(
    project: Project, stub_render: None, stub_burn: None
) -> None:
    """The two calls an agent makes are one render, and the log has to say so:
    a second, separate run would hide the export behind a burn-only line."""
    out = project.render_dir / "cut.wav"
    ops.export(project.root, out, export_format=None, preset="web")
    result = ops.add_captions(project.root, project.render_dir / "cut.ass", burn=out)

    run = renderlog.last(project)
    assert run["output"] == result["burned"]
    assert run["stages"] == {
        "export": {"outcome": "done", "detail": None},
        "burn": {"outcome": "done", "detail": None},
    }
    # Carried from the export: `add_captions` has no preset of its own, and
    # writing null over it would make the burn look like its own unpresetted run.
    assert run["preset"] == "web"


def test_writing_captions_without_burning_records_nothing(
    project: Project, stub_render: None
) -> None:
    ops.export(project.root, project.render_dir / "cut.wav", export_format=None)
    ops.add_captions(project.root, project.render_dir / "cut.ass")

    assert renderlog.last(project)["stages"] == {"export": {"outcome": "done", "detail": None}}


def test_a_burn_onto_an_unlogged_file_starts_its_own_run(
    project: Project, stub_burn: None
) -> None:
    """Burning onto something this project never exported — an older render,
    a file from elsewhere — is a run of its own, not a continuation of
    whatever happens to be last in the log."""
    stray = project.render_dir / "from-somewhere-else.wav"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"not this project's export")

    ops.add_captions(project.root, project.render_dir / "cut.ass", burn=stray)

    assert renderlog.last(project)["stages"] == {"burn": {"outcome": "done", "detail": None}}


def test_a_media_path_for_output_is_refused_and_the_render_survives(
    project: Project, stub_render: None, stub_burn: None
) -> None:
    """`output` is the sidecar. The sidecar is written first, so an `output` that
    names the render being burned replaced it with caption text and the burn then
    read that text back as its picture ("Input #0, ass, from cut.mp4"). The tool's
    own text had called `output` "the burned video", and every agent that trusted
    it made this call. Refused before anything is written, the render as it was."""
    render = project.render_dir / "cut.wav"
    ops.export(project.root, render, export_format=None)
    before = render.read_bytes()

    for target in (render, project.render_dir / "elsewhere.mp4"):
        with pytest.raises(captions.CaptionError, match="burn_output"):
            ops.add_captions(project.root, target, burn=render)

    assert render.read_bytes() == before
    assert not (project.render_dir / "elsewhere.mp4").exists()


def test_burning_onto_the_render_itself_is_refused_by_name(project: Project, stub_render: None) -> None:
    """ffmpeg cannot write the file it reads, and its refusal is a dozen lines of
    library banner with the reason nowhere in them."""
    render = project.render_dir / "cut.wav"
    ops.export(project.root, render, export_format=None)
    before = render.read_bytes()

    with pytest.raises(captions.CaptionError, match="itself"):
        ops.add_captions(project.root, project.render_dir / "cut.ass", burn=render, burn_output=render)

    assert render.read_bytes() == before


def test_the_tool_text_says_the_sidecar_is_always_output_and_the_video_burn_output() -> None:
    docs = server._PARAM_DOCS["add_captions"]
    assert "burn_output" in docs["output"]
    assert "or the burned video" not in docs["output"]
    assert "instead of writing a sidecar" not in docs["burn"]


# -- what the whole thing is for ----------------------------------------------


def test_finish_report_can_now_answer_burned_for_a_cli_render(
    project: Project, stub_render: None, stub_burn: None
) -> None:
    """The bug in one test: before this, the second of these read `"unknown"`
    on a film whose captions were burned in, because nothing outside the web
    UI wrote the log `finish_report` reads.

    The first stays `"unknown"`, and that is right rather than a gap: an
    export that has not been burned yet has no answer to give, which is a
    different thing from the web UI's whole-run record where a missing burn
    stage means the pipeline stopped before reaching it."""
    out = project.render_dir / "cut.wav"
    ops.export(project.root, out, export_format=None)
    assert ops.finish_report(project.root)["captions"]["burned"] == "unknown"

    ops.add_captions(project.root, project.render_dir / "cut.ass", burn=out)
    assert ops.finish_report(project.root)["captions"]["burned"] == "yes"


# -- amend --------------------------------------------------------------------


def test_amend_supersedes_by_appending_never_by_editing(project: Project) -> None:
    """The file stays append-only — `last` reads from the end, so a merged
    line is a new line — and the superseded one stays visible to `all_runs`,
    which is how a render burned twice still shows both attempts."""
    renderlog.append(
        project, output="/r/a.mp4", preset=None, expected_duration=1.0,
        stages={"export": {"outcome": "done", "detail": None}},
    )
    renderlog.amend(
        project, output="/r/a-captioned.mp4", preset=None, expected_duration=1.0,
        stages={"burn": {"outcome": "done", "detail": None}}, continues="/r/a.mp4",
    )

    runs = renderlog.all_runs(project)
    assert len(runs) == 2
    assert runs[0]["stages"] == {"export": {"outcome": "done", "detail": None}}
    assert set(runs[1]["stages"]) == {"export", "burn"}


def test_amend_replaces_a_repeated_stage_rather_than_merging_it(project: Project) -> None:
    """A second burn onto the same export is a new answer to the same
    question, not an addition to the old one."""
    renderlog.append(
        project, output="/r/a.mp4", preset=None, expected_duration=1.0,
        stages={"burn": {"outcome": "error", "detail": {"error": "font missing"}}},
    )
    renderlog.amend(
        project, output="/r/a.mp4", preset=None, expected_duration=1.0,
        stages={"burn": {"outcome": "done", "detail": None}}, continues="/r/a.mp4",
    )

    assert renderlog.last(project)["stages"]["burn"] == {"outcome": "done", "detail": None}


# -- sources: which edit a render was made from --------------------------------


def test_an_export_stamps_the_edit_it_read(project: Project, stub_render: None) -> None:
    """The stamp is the two files' bytes as the export found them, so a render
    lying on disk can be matched to the edit that made it."""
    before = renderlog.stamp(project)
    out = project.render_dir / "cut.wav"
    reply = ops.export(project.root, out, export_format=None)

    assert reply["source"] == before
    assert before["timeline"] is not None and before["manifest"] is not None
    assert renderlog.last(project)["sources"] == {"export": before}


def test_a_render_goes_stale_when_the_edit_moves(project: Project, stub_render: None) -> None:
    """`current` is the whole point: a cut after the render means the file on
    disk is no longer the film the project describes."""
    ops.export(project.root, project.render_dir / "cut.wav", export_format=None)
    assert ops.finish_report(project.root)["last_render"]["current"] is True

    edit = tl.Edit([tl.Segment("vo", 0.0, 1.0)])
    clips_by_id = {c["clip_id"]: c for c in project.read_manifest()["clips"]}
    tl.write(tl.to_otio(edit, clips_by_id, rate=1000.0, name="proj"), project.timeline_path)

    assert ops.finish_report(project.root)["last_render"]["current"] is False


def test_a_manifest_change_counts_too(project: Project, stub_render: None) -> None:
    """Most authoring state is manifest state (the cue table, the caption
    style, the bed), so a render is stale when only the manifest moved."""
    ops.export(project.root, project.render_dir / "cut.wav", export_format=None)
    manifest = project.read_manifest()
    manifest["caption_style"] = {"preset": "clean"}
    Project.open(project.root).write_manifest(manifest)

    assert ops.finish_report(project.root)["last_render"]["current"] is False


def test_a_burn_goes_stale_on_a_lexicon_edit_and_an_export_does_not(
    project: Project, stub_render: None, stub_burn: None
) -> None:
    """The lexicon's `hear` table is what the captions print, so a burn that
    read one stamps it and is stale once it changes; an export never read it,
    and stays current — `current` compares each source on the hashes it
    carries (renderlog.py's module docstring)."""
    out = project.render_dir / "cut.wav"
    ops.export(project.root, out, export_format=None)
    export_run = renderlog.last(project)
    assert set(export_run["sources"]["export"]) == {"timeline", "manifest"}

    ops.lexicon_add(project.root, "teh", "the")
    assert renderlog.current(project, export_run) is True

    result = ops.add_captions(project.root, project.render_dir / "cut.ass", burn=out)
    assert set(result["source"]) == {"timeline", "manifest", "lexicon"}
    burned_run = renderlog.last(project)
    assert renderlog.current(project, burned_run) is True

    ops.lexicon_add(project.root, "teh", "thee")
    assert renderlog.current(project, burned_run) is False
    assert renderlog.current(project, export_run) is True


def test_a_burn_stamps_itself_and_keeps_the_export_s_stamp(
    project: Project, stub_render: None, stub_burn: None
) -> None:
    """The burn reads the edit's words, so it stamps what it read; the export's
    stamp rides forward beside it. An edit between the two calls leaves them
    different, and the render is then two edits at once, so it is not current."""
    out = project.render_dir / "cut.wav"
    exported = ops.export(project.root, out, export_format=None)
    result = ops.add_captions(project.root, project.render_dir / "cut.ass", burn=out)

    run = renderlog.last(project)
    assert run["sources"] == {"export": exported["source"], "burn": result["source"]}
    assert renderlog.current(project, run) is True

    renderlog.amend(
        project, output="/r/x.mp4", preset=None, expected_duration=1.0,
        stages={"burn": {"outcome": "done", "detail": None}},
        sources={"burn": {"timeline": "0" * 64, "manifest": "0" * 64}},
        continues=run["output"],
    )
    assert renderlog.current(project, renderlog.last(project)) is False


def test_a_line_from_before_stamps_is_unknown_never_current(project: Project) -> None:
    """"Not recorded" is not "unchanged": an old log line must not read as a
    render of the edit the project holds today."""
    renderlog.append(
        project, output="/r/a.mp4", preset=None, expected_duration=1.0,
        stages={"export": {"outcome": "done", "detail": None}},
    )
    run = renderlog.last(project)
    assert "sources" not in run
    assert renderlog.current(project, run) is None
