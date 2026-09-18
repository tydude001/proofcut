"""`ops.finish_report` — docs/plans/STUDIO.md § Step 01's truth-strip composer.

Built by hand, the same no-ffmpeg pattern `test_ops_shots.py` uses: a real
`Project`, a hand-written `Edit` with a genuine cut gap, and a registered
clip whose media file is a stub (its `has_video` flag is set in the manifest
directly, never probed). `finish_report` never touches media on disk itself
— every field is another op's own return, filtered or summed — so this
fixture is enough to exercise all of it.

The point of this file is the composition claim: every field must be
*another op's own answer*, not a parallel read of the manifest. Proved the
`test_properties_composes_rather_than_reimplements` way — monkeypatch the
real op to lie, and check the lie surfaces here too, rather than by
asserting a value the honest op happens to produce today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from proofcut import ops, renderlog
from proofcut import timeline as tl
from proofcut import transcript as tx
from proofcut.project import Project, ProjectError

CLIPS = {
    "vo": {
        "clip_id": "vo",
        "source": "/tmp/vo.wav",
        "duration": 4.0,
        "has_video": False,
        "has_audio": True,
    },
    "clipa": {
        "clip_id": "clipa",
        "duration": 10.0,
        "has_video": True,
        "has_audio": True,
    },
}


def _words(clip_id: str, *specs: tuple[str, float, float]) -> tx.Transcript:
    return tx.Transcript(
        clip_id=clip_id,
        words=tuple(
            tx.Word(index=i, text=text, start=start, end=end)
            for i, (text, start, end) in enumerate(specs)
        ),
    )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A VO clip cut to two surviving source ranges — [0.0, 1.0) and
    [2.0, 3.5) — with a gap at [1.0, 2.0), plus a registered video clip
    whose media file exists but is not real video (`has_video` is declared
    in the manifest, never probed — `_footage_resolution` only consults
    `width`/`height`, absent here, so the canvas falls back to
    `mlt.DEFAULT_RESOLUTION`, 1920x1080 — a 16:9 canvas, deliberately, so
    that `tiktok-reels`'s 9:16 claim is refused by every test in this file
    without any extra setup).
    """
    project = Project.create(tmp_path / "proj")

    clip_a_media = tmp_path / "clipa.mp4"
    clip_a_media.write_bytes(b"not really a video, just needs to exist")

    manifest = project.read_manifest()
    manifest["clips"] = [CLIPS["vo"], {**CLIPS["clipa"], "source": str(clip_a_media)}]
    project.write_manifest(manifest)

    edit = tl.Edit([tl.Segment("vo", 0.0, 1.0), tl.Segment("vo", 2.0, 3.5)])
    clips_by_id = {c["clip_id"]: c for c in manifest["clips"]}
    tl.write(tl.to_otio(edit, clips_by_id, rate=1000.0, name="proj"), project.timeline_path)

    tx.save(
        _words(
            "vo",
            ("cold", 0.0, 0.3),
            ("open", 0.5, 0.8),
            ("cut1", 1.2, 1.5),  # inside the cut gap [1.0, 2.0)
            ("after", 2.2, 2.5),
            ("last", 3.0, 3.3),
        ),
        project.transcript_path("vo"),
    )
    return project


def _with_one_pinned_cue(project: Project) -> Project:
    """A baseline, un-orphaned cue table: one cue, pinned, pointing at a
    surviving word — the shape `finish_report`'s non-error-path fields
    (`picture`, and the flags that would otherwise trip on it) assume."""
    ops.cue_add(project.root, "vo", 1, "clipa", src_start=0.0)  # "open" survives
    return project


def test_finish_report_refuses_a_project_with_nothing_seeded(tmp_path: Path) -> None:
    """`status` itself no longer raises for an un-seeded project (TRIAL.md §
    `timeline_status`) — it reports `seeded: false` — but `finish_report`
    still needs a finished cut to report on, so it must refuse clearly
    rather than KeyError on a field `status` no longer returns."""
    project = Project.create(tmp_path / "proj")

    with pytest.raises(ProjectError, match="no timeline yet"):
        ops.finish_report(project.root)


# -- composition --------------------------------------------------------------


def test_finish_report_composes_rather_than_reimplements(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`duration` must come from calling the real `status`, not from a
    parallel read of the edit/manifest — proved by making `status` lie and
    checking the lie shows up in `finish_report` too."""
    _with_one_pinned_cue(project)
    real_status = ops.status

    def _lying_status(path: object, **kwargs: object) -> dict[str, object]:
        result = real_status(path, **kwargs)  # type: ignore[arg-type]
        return {**result, "timeline_duration": 999.5, "expected_duration": 1000.5}

    monkeypatch.setattr(ops, "status", _lying_status)
    result = ops.finish_report(project.root)
    assert result["duration"]["edit_seconds"] == 999.5
    assert result["duration"]["total_seconds"] == 1000.5


# -- field shape ----------------------------------------------------------------


def test_finish_report_field_shape(project: Project) -> None:
    _with_one_pinned_cue(project)
    result = ops.finish_report(project.root)

    assert set(result) == {
        "duration",
        "canvas",
        "captions",
        "picture",
        "marks",
        "seams",
        "sources",
        "unused_clips",
        "framing",
        "holds",
        "continuity",
        "last_render",
        "flags",
    }
    assert set(result["duration"]) == {"edit_seconds", "tail_seconds", "total_seconds"}
    assert set(result["canvas"]) == {"canvas", "presets"}
    assert set(result["canvas"]["presets"]) == {"youtube", "web", "tiktok-reels"}
    for preset in result["canvas"]["presets"].values():
        assert set(preset) == {"ok", "message", "needs", "fix"}
    assert set(result["captions"]) == {"configured", "font", "burned"}
    assert set(result["picture"]) == {"cue_count", "pinned_count", "shots_error"}
    assert set(result["marks"]) == {"applied", "stale"}
    assert set(result["seams"]) == {"count"}
    # `None` here rather than a dict: this fixture has never rendered, which is
    # the same absent log that leaves `captions.burned` unknown.
    assert result["last_render"] is None
    assert set(result["flags"]) == {"count", "items"}
    for item in result["flags"]["items"]:
        assert set(item) == {"kind", "message", "mode"}
        assert item["mode"] == "finish"


# -- unused_clips (TRIAL.md § Registered-and-not-on-the-timeline) -----------


def test_unused_clips_is_empty_when_everything_is_referenced(project: Project) -> None:
    """`vo` is on the timeline and `clipa` is cued — both referenced, so
    nothing here to name."""
    _with_one_pinned_cue(project)

    assert ops.finish_report(project.root)["unused_clips"] == []


def test_unused_clips_names_a_clip_referenced_nowhere(project: Project) -> None:
    """The exact shape the trial found: a clip registered (`import_media`,
    or here — imported for `spot_frames`-style inspection) and never cued,
    held, or put on any lane."""
    _with_one_pinned_cue(project)
    manifest = project.read_manifest()
    manifest["clips"].append({**CLIPS["clipa"], "clip_id": "delivered", "source": "/tmp/d.mp4"})
    project.write_manifest(manifest)

    assert ops.finish_report(project.root)["unused_clips"] == ["delivered"]



def test_unused_clips_counts_an_inset_and_a_sound_as_referenced(project: Project) -> None:
    """The B7 agent's report called its film and its click "unused": the
    inset and the sound lanes were never read (RECUT.md step 1). Both a
    lane's footage and its addressing clip count, the cue's own rule."""
    _with_one_pinned_cue(project)
    manifest = project.read_manifest()
    manifest["clips"] += [
        {**CLIPS["clipa"], "clip_id": "film", "source": "/tmp/film.mp4"},
        {**CLIPS["vo"], "clip_id": "click", "source": "/tmp/click.wav"},
        {**CLIPS["clipa"], "clip_id": "delivered", "source": "/tmp/d.mp4"},
    ]
    manifest["insets"] = [{"clip_id": "vo", "asset": "film", "rect": [0, 0, 640, 360], "word_index": 0}]
    manifest["sounds"] = [{"assets": ["click"], "clip_id": "vo", "word_index": 0}]
    project.write_manifest(manifest)

    assert ops._referenced_clip_ids(project.read_manifest(), {"vo"}) >= {"film", "click"}
    assert ops.finish_report(project.root)["unused_clips"] == ["delivered"]

# -- captions.burned / the render log --------------------------------------------


def test_finish_report_captions_burned_unknown_with_no_render_log(project: Project) -> None:
    """No `cache/renders.jsonl` at all — reported as `unknown`, never as a
    clean or a `no`. The *flag* is a separate question, below."""
    _with_one_pinned_cue(project)
    assert not project.renders_log_path.exists()

    result = ops.finish_report(project.root)

    assert result["captions"]["burned"] == "unknown"


def test_finish_report_unknown_burn_flags_only_once_a_style_exists(project: Project) -> None:
    """The captionless-film failure is a *styled* project whose render never
    burned. An unstyled one has nothing to burn, so its unknown burn state is
    reported and not flagged — a flag no action can clear is a count that can
    never reach zero, which is the thing docs/plans/STUDIO.md's definition of done needs.
    """
    _with_one_pinned_cue(project)

    unstyled = ops.finish_report(project.root)
    assert unstyled["captions"]["configured"] is False
    assert unstyled["captions"]["burned"] == "unknown"
    assert [f for f in unstyled["flags"]["items"] if f["kind"] == "captions"] == []

    ops.caption_style(project.root, font="Noto Sans")

    styled = ops.finish_report(project.root)
    assert styled["captions"]["configured"] is True
    assert styled["captions"]["burned"] == "unknown"
    matching = [f for f in styled["flags"]["items"] if f["kind"] == "captions"]
    assert len(matching) == 1
    assert "no render log" in matching[0]["message"]
    assert matching[0]["mode"] == "finish"


# -- canvas.presets reuses _check_preset_canvas, never re-implements it ---------


def test_finish_report_canvas_presets_reuse_check_preset_canvas(project: Project) -> None:
    """The project's canvas defaults to 1920x1080 (fixture docstring) —
    `tiktok-reels` claims 9:16 and must be refused, with the *same* message
    `_check_preset_canvas` itself raises, not a restatement of it."""
    _with_one_pinned_cue(project)

    with pytest.raises(ProjectError) as excinfo:
        ops._check_preset_canvas(project, "tiktok-reels")
    expected_message = str(excinfo.value)

    result = ops.finish_report(project.root)

    tiktok = result["canvas"]["presets"]["tiktok-reels"]
    assert tiktok["ok"] is False
    assert tiktok["message"] == expected_message
    # The card's short line and the command it opens to, carried on the
    # refusal's own type rather than cut out of the sentence — and each one
    # still said inside the message, so neither can drift from it.
    assert excinfo.value.needs == tiktok["needs"] == "needs a 9:16 canvas"
    assert excinfo.value.fix == tiktok["fix"] == "proofcut canvas 1080x1920"
    assert f"`{tiktok['fix']}`" in tiktok["message"]

    # And the presets that name no fixed geometry pass clean.
    assert result["canvas"]["presets"]["youtube"] == {"ok": True, "message": None, "needs": None, "fix": None}
    assert result["canvas"]["presets"]["web"] == {"ok": True, "message": None, "needs": None, "fix": None}

    # And a refusing preset is NOT a flag. It is drawn on the preset's own
    # card with its fix; flagging it would say the film is wrong for having
    # chosen 16:9, permanently and unclearably.
    assert [f for f in result["flags"]["items"] if f["kind"] == "canvas"] == []


def test_a_preset_refused_for_having_no_picture_offers_no_command(project: Project) -> None:
    """The audio-only refusal has a short form too, but no one command fixes
    it — so `fix` is `None` and the card opens to the message alone, rather
    than to a `proofcut canvas` suggestion for a project with nothing to crop."""
    manifest = project.read_manifest()
    manifest["clips"] = [c for c in manifest["clips"] if not c.get("has_video")]
    project.write_manifest(manifest)

    with pytest.raises(ops.PresetCanvasError) as excinfo:
        ops._check_preset_canvas(project, "tiktok-reels")
    assert "no picture to shape" in str(excinfo.value)
    assert excinfo.value.needs == "needs a project with picture"
    assert excinfo.value.fix is None


# -- picture.shots_error is reported, never raised -------------------------------


def test_finish_report_picture_shots_error_reported_not_raised(project: Project) -> None:
    """A cue whose word was cut makes `timeline_view` refuse the projection
    (`test_ops_shots.py`'s own fixture for this: word 2, 'cut1', sits inside
    the [1.0, 2.0) gap) — `finish_report` must surface that as `shots_error`
    and a flag, and must not raise itself."""
    ops.cue_add(project.root, "vo", 2, "clipa")  # "cut1" is inside the cut gap

    # Sanity: the underlying view really does refuse this, so the assertion
    # below is exercising the pass-through and not a fixture that never hit it.
    view = ops.timeline_view(project.root)
    assert view["shots"] is None
    assert "was cut from the edit" in view["shots_error"]

    result = ops.finish_report(project.root)  # must not raise

    assert result["picture"]["shots_error"] is not None
    assert "was cut from the edit" in result["picture"]["shots_error"]
    assert result["picture"]["cue_count"] == 1

    flags = result["flags"]["items"]
    picture_flags = [f for f in flags if f["kind"] == "picture"]
    assert len(picture_flags) == 1
    assert picture_flags[0]["message"] == result["picture"]["shots_error"]


# -- flags.count always matches len(flags.items) ---------------------------------


def test_finish_report_flags_count_matches_items_length(project: Project) -> None:
    """A healthy project reaches zero flags. That is the property the whole
    strip rests on: the count is an inbox, and an inbox that cannot be emptied
    is a guard someone learns to ignore."""
    _with_one_pinned_cue(project)
    result = ops.finish_report(project.root)
    assert result["flags"]["count"] == len(result["flags"]["items"])
    assert result["flags"]["count"] == 0
    # Not vacuous — the fixture does carry the two conditions that used to
    # flag here and deliberately no longer do.
    assert result["canvas"]["presets"]["tiktok-reels"]["ok"] is False
    assert result["captions"]["burned"] == "unknown"


def test_finish_report_flags_count_matches_items_length_on_the_orphan_case(
    project: Project,
) -> None:
    ops.cue_add(project.root, "vo", 2, "clipa")  # orphaned, see above
    result = ops.finish_report(project.root)
    assert result["flags"]["count"] == len(result["flags"]["items"])
    assert [f["kind"] for f in result["flags"]["items"]] == ["picture"]


# -- framing is opt-in ----------------------------------------------------------


def test_framing_is_opt_in_and_none_is_not_zero(project: Project) -> None:
    """Off by default, and `None` rather than an empty dict when off.

    The framing section calls `reframe_coverage`, which decodes placed
    footage for a scene-cut scan — 5.7s wall and 46s of CPU on the real film,
    every call, uncached. The truth strip re-reads this op on every
    `project-changed`, so composing it in unconditionally made every cut pay
    for a number the cut had not asked about. `None` has to stay
    distinguishable from a measured zero, or "nobody scanned" reads as
    "nothing stale" — which is the captionless-film shape all over again.
    """
    _with_one_pinned_cue(project)

    off = ops.finish_report(project.root)
    assert off["framing"] is None
    assert [f for f in off["flags"]["items"] if f["kind"] == "framing"] == []

    on = ops.finish_report(project.root, framing=True)
    assert set(on["framing"]) == {"stale_seconds", "stale_stretches", "steps"}
    assert on["framing"]["stale_seconds"] == 0.0


def test_holds_is_opt_in_and_none_with_no_render(project: Project) -> None:
    """Off by default, `hold_check`'s own opt-in reasoning: it decodes and
    transcribes render spans, so it must not ride every `project-changed`
    event. Asking for it with no render on disk yet still reports `None` —
    a hold's mix is confirmed by listening to a file, not by reading the
    project."""
    _with_one_pinned_cue(project)

    off = ops.finish_report(project.root)
    assert off["holds"] is None

    on = ops.finish_report(project.root, holds=True)
    assert on["holds"] is None


def test_continuity_is_opt_in_and_none_is_not_zero(project: Project) -> None:
    """Off by default, `framing`'s own opt-in reasoning: `stubs=True` pays
    the identical scene-cut decode cost. `None` off, a real (non-`None`)
    report on — this fixture's single shot genuinely runs short (2.5s of
    edit against `CONTINUITY_MIN_SHOT`'s 3.0s), so `count` on is a real 1,
    not a vacuous zero.
    """
    _with_one_pinned_cue(project)

    off = ops.finish_report(project.root)
    assert off["continuity"] is None
    assert [f for f in off["flags"]["items"] if f["kind"] == "continuity"] == []

    on = ops.finish_report(project.root, continuity=True)
    assert set(on["continuity"]) == {"count", "by_kind", "accepted"}
    assert on["continuity"]["count"] == 1
    assert on["continuity"]["by_kind"] == {"short_shot": 1}
    # `short_shot` is not flag-worthy (routinely a deliberate fast cut) —
    # only `rewind`/`stub` are, so no flag here despite a non-zero count.
    assert [f for f in on["flags"]["items"] if f["kind"] == "continuity"] == []


def test_continuity_flags_rewind_and_stub_but_not_replay_or_short_shot(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`test_properties_composes_rather_than_reimplements`'s own method:
    monkeypatch the real op to lie, and check the lie surfaces here too,
    rather than asserting a value the honest op happens to produce today."""
    _with_one_pinned_cue(project)

    def _lying_continuity_check(*args: object, **kwargs: object) -> dict[str, Any]:
        return {
            "findings": [
                {"kind": "rewind"},
                {"kind": "rewind"},
                {"kind": "replay"},
                {"kind": "short_shot"},
                {"kind": "stub"},
            ],
            "count": 5,
            "accepted": 2,
            "accepted_stale": [],
            "shots_error": None,
            "stub_error": None,
        }

    monkeypatch.setattr(ops, "continuity_check", _lying_continuity_check)
    result = ops.finish_report(project.root, continuity=True)

    assert result["continuity"] == {
        "count": 5,
        "by_kind": {"rewind": 2, "replay": 1, "short_shot": 1, "stub": 1},
        "accepted": 2,
    }
    kinds = [f["kind"] for f in result["flags"]["items"] if f["kind"] == "continuity"]
    assert kinds == ["continuity", "continuity"]
    messages = " ".join(f["message"] for f in result["flags"]["items"] if f["kind"] == "continuity")
    assert "2 shot(s) rewind" in messages
    assert "1 shot(s) end on a real cut" in messages
    for flag in result["flags"]["items"]:
        if flag["kind"] == "continuity":
            assert flag["mode"] == "finish"


def test_holds_runs_hold_check_against_the_last_render(project: Project) -> None:
    """`finish_report`'s own composed-only rule: `last_render` is
    `renderlog.last`'s own answer (`webui.py`'s pipeline, not `export`
    itself), and once one exists `holds=True` runs `hold_check` against it —
    a project with no stored holds still gets a real (empty) report rather
    than `None`, the same "not measured" vs. "nothing to measure" split
    `framing` draws."""
    render = project.root / "renders" / "out.mp4"
    render.parent.mkdir(parents=True, exist_ok=True)
    render.write_bytes(b"not a real render, just needs to exist")
    renderlog.append(
        project, output=str(render), preset=None, expected_duration=1.5, stages={}
    )

    result = ops.finish_report(project.root, holds=True)

    assert result["holds"] is not None
    assert result["holds"] == {
        "project": str(project.root),
        "render": str(render),
        "holds": [],
        "count": 0,
        "faults": 0,
    }
