"""CLI-only behaviour: timecode parsing, and how `init` resolves a directory.

Both live only in the CLI by design, so the stdio suite cannot reach them.
`cut-at`'s whole value rests on the span parsing; `init`'s on not creating a
project somewhere the caller did not name.

One exception: `test_cut_through_pause_flag_parses_and_reaches_ops` below,
which checks the CLI-specific plumbing for `cut --through-pause`
(argparse's `store_true` reaching `ops.cut_by_transcript` under the right
keyword) — the underlying behaviour it enables is already covered end-to-end
over the wire in test_server_stdio.py; this only guards the one hop that
file cannot reach, since it never spawns `proofcut cut` itself.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import wave
from pathlib import Path

import pytest

from proofcut import ops
from proofcut.cli import _parse_timecode, _slot_assignments, _time_span, main
from proofcut.project import ProjectError

needs_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe is not installed"
)


def test_parse_timecode_reads_colon_parts_optional_from_the_right() -> None:
    assert _parse_timecode("4.4") == pytest.approx(4.4)
    assert _parse_timecode("0:40.4") == pytest.approx(40.4)
    assert _parse_timecode("1:00:40.4") == pytest.approx(3640.4)


def test_time_span_start_plus_duration() -> None:
    assert _time_span("0:40.4+4.4") == pytest.approx([40.4, 44.8])


def test_time_span_start_dash_end() -> None:
    assert _time_span("0:40.4-0:44.8") == pytest.approx([40.4, 44.8])


def test_time_span_rejects_garbage() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _time_span("banana")


# -- `init` and the global `-C` -------------------------------------------
#
# `init` is the one subcommand whose directory is an argument rather than a
# lookup, so it is the one place `-C` and a positional can disagree. It used
# to read only the positional, which made `proofcut -C myproj init` create a
# project in the *current* directory and report success.


def _project_exists(root: Path) -> bool:
    return (root / "proofcut.json").is_file()


def test_init_honours_the_global_project_flag(tmp_path: Path) -> None:
    assert main(["-C", str(tmp_path / "proj"), "init"]) == 0
    assert _project_exists(tmp_path / "proj")


def test_init_still_takes_a_positional_path(tmp_path: Path) -> None:
    assert main(["init", str(tmp_path / "proj")]) == 0
    assert _project_exists(tmp_path / "proj")


def test_init_refuses_two_different_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Neither spelling silently wins — the old behaviour picked the positional
    and created a project somewhere the caller had not named.
    """
    assert main(["-C", str(tmp_path / "flag"), "init", str(tmp_path / "positional")]) == 1
    assert not _project_exists(tmp_path / "flag")
    assert not _project_exists(tmp_path / "positional")
    assert "two directories" in capsys.readouterr().err


def test_web_refuses_both_dash_c_and_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--root` and `-C` pick a project two different ways (a picker over a
    scan vs. exactly one project) — same "no sensible way to pick between
    them" refusal `init`'s two-directories case uses above, and it fires
    before `webui.serve_root`/`webui.serve` is ever called, so nothing binds
    a socket either way."""
    assert (
        main(["-C", str(tmp_path / "proj"), "web", "--root", str(tmp_path), "--port", "0"]) == 1
    )
    assert "two ways" in capsys.readouterr().err


# -- `open` (Studio Step 04 § A) --------------------------------------------
#
# `open` blocks on `serve_forever()` once it actually starts a server, so
# only the mutual-refusal path (which raises before `webui.open_studio` is
# ever called) and plain argparse shape are safe to exercise here — nothing
# below binds a socket.


def test_open_refuses_both_dash_c_and_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same refusal as `web`'s, fired the same way: before `webui.open_studio`
    is ever called, so `open`'s own ephemeral-port bind never happens either."""
    assert main(["-C", str(tmp_path / "proj"), "open", "--root", str(tmp_path)]) == 1
    assert "two ways" in capsys.readouterr().err


def test_open_parses_root_with_no_host_or_port_flags() -> None:
    """`open` is deliberately narrower than `web`: no `--host`/`--port`/
    `--open`/`--verbose` — the port is always ephemeral, per docs/plans/STUDIO.md's own
    wording — so `--root` is the only flag it should accept."""
    from proofcut.cli import _build_parser

    args = _build_parser().parse_args(["open", "--root", "/tmp/somewhere"])
    assert args.command == "open"
    assert args.root == "/tmp/somewhere"
    assert not hasattr(args, "host")
    assert not hasattr(args, "port")
    assert not hasattr(args, "verbose")


def test_open_defaults_root_to_none(tmp_path: Path) -> None:
    """With no `--root`, `open` means "open -C's project directly" — `args.root`
    stays `None` so `_cmd_open` takes the `-C` branch, not the picker one."""
    from proofcut.cli import _build_parser

    args = _build_parser().parse_args(["-C", str(tmp_path / "proj"), "open"])
    assert args.root is None


def test_init_with_neither_uses_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert _project_exists(tmp_path)


def test_explicit_dash_c_dot_is_not_mistaken_for_an_unset_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`-C .` is a real answer, so pairing it with a positional is still the
    two-directories error rather than being waved through as "no -C given".
    """
    monkeypatch.chdir(tmp_path)
    assert main(["-C", ".", "init", "proj"]) == 1
    assert not _project_exists(tmp_path)
    assert not _project_exists(tmp_path / "proj")


# -- `card reauthor` -------------------------------------------------------
#
# The behaviour is proven over the wire in test_server_stdio.py and against
# ops in test_ops_card_reauthor.py. What only the CLI has is the optional
# positional: `card reauthor` with no name is the sweep, and argparse would
# otherwise make that a usage error rather than the common case.


def test_card_reauthor_takes_no_name_and_means_every_card(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "card", "reauthor", "--plan"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cards"] == []
    assert out["plan"] is True


# -- `cut --through-pause` -------------------------------------------------
#
# The behaviour `through_pause` enables is proven end-to-end over the wire in
# test_server_stdio.py; what only a real `proofcut cut` invocation can prove is
# that the flag's plumbing through argparse actually reaches `ops` under the
# right keyword.


def _make_wav(path: Path, *, tones: list[tuple[float, float]], duration: float = 12.0) -> None:
    """A wav with tone bursts at `tones` and silence elsewhere — the same
    shape test_server_stdio.py's `sources` fixture builds, kept local here so
    this file stays free of a cross-file import for one helper.
    """
    rate = 22050
    with wave.open(str(path), "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * duration)):
            t = i / rate
            loud = any(a <= t < b for a, b in tones)
            value = int(12000 * math.sin(2 * math.pi * 220 * t)) if loud else 0
            frames += struct.pack("<h", value)
        out.writeframes(bytes(frames))


def _make_sources(root: Path) -> tuple[Path, Path]:
    """A four-burst recording and a transcript with two words per burst —
    words 0.1s apart within a burst, 1.1s apart across a burst boundary, so
    word 3 -> word 4's gap (1.1s) clears PAUSE_MARKER_MIN and word 0 -> word
    1's gap (0.1s) does not.
    """
    audio = root / "vo.wav"
    _make_wav(audio, tones=[(0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 11.0)])

    words = []
    for burst, (start, _) in enumerate([(0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 11.0)]):
        for n in range(2):
            at = start + n
            words.append({"word": f"w{burst}{n}", "start": at, "end": at + 0.9})

    transcript = root / "vo.json"
    transcript.write_text(json.dumps({"language": "en", "words": words}), encoding="utf-8")
    return audio, transcript


@needs_ffprobe
def test_cut_through_pause_flag_parses_and_reaches_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    # Word 3 (w11, end 4.9s) sits right before the 1.1s gap to word 4 (w20,
    # start 6.0s) — wide enough to have drawn a `[1.1s]` marker.
    assert main(["-C", str(project), "cut", clip_id, "2:3", "--through-pause", "--plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    applied = plan["applied"][0]
    assert applied["word_end"] == pytest.approx(4.9)
    assert applied["source_end"] == pytest.approx(6.0)

    # Without the flag, the same range's boundary stays at the word's own end.
    assert main(["-C", str(project), "cut", clip_id, "2:3", "--plan"]) == 0
    plain = json.loads(capsys.readouterr().out)
    assert plain["applied"][0]["source_end"] == pytest.approx(4.9)


@needs_ffprobe
def test_transcript_checks_clip_id_is_optional(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one hop the stdio suite cannot reach: argparse's `nargs="?"`
    reaching `ops.transcript_checks` as None, which is what makes
    `proofcut transcript-checks` with no argument mean "every clip with a
    transcript" rather than a missing-argument error.
    """
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "transcript-checks"]) == 0
    every = json.loads(capsys.readouterr().out)
    assert [c["clip_id"] for c in every["clips"]] == [clip_id]
    assert {"near_duplicates", "suspect_durations", "overlaps", "repeats"} <= set(every["clips"][0])

    # Naming the clip explicitly reaches the same result.
    assert main(["-C", str(project), "transcript-checks", clip_id]) == 0
    named = json.loads(capsys.readouterr().out)
    assert named == every


# -- phrase addressing (feature: phrase-addressed cues) ---------------------
#
# The resolver and the ops-layer wiring are proven in test_transcript.py,
# test_ops_cues.py and test_ops_resolve_phrase.py; over the wire in
# test_server_stdio.py. What only a real CLI invocation can prove is
# argparse's own plumbing: `word_index` becoming an optional positional
# (`nargs="?"`) that still leaves room for `asset` right after it, and the
# brand-new `resolve` subcommand's dispatch.


@needs_ffprobe
def test_cue_add_phrase_flag_reaches_ops_as_the_first_word(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()

    # word_index omitted (the CLI-only nargs="?" hop) — resolved by --phrase
    # instead, and asset still lands right after it positionally.
    assert (
        main(["-C", str(project), "cue", "add", clip_id, "cold-open", "--phrase", "w10 w11"])
        == 0
    )
    added = json.loads(capsys.readouterr().out)
    assert added["word_index"] == 2
    assert added["asset"] == "cold-open"
    assert added["phrase"] == "w10 w11"


@needs_ffprobe
def test_cue_add_word_index_and_phrase_together_are_refused_by_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "-C", str(project), "cue", "add", clip_id, "2", "cold-open",
                "--phrase", "w10 w11",
            ]
        )
        == 1
    )
    assert "not both" in capsys.readouterr().err


@needs_ffprobe
def test_resolve_command_reaches_ops_resolve_phrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "resolve", clip_id, "w10 w11"]) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert (resolved["first_word"], resolved["last_word"]) == (2, 3)
    assert resolved["match"] == "exact"

    # --after reaches ops as the forward cursor: skipping past the phrase's
    # own words leaves nothing left to match.
    assert main(["-C", str(project), "resolve", clip_id, "w10 w11", "--after", "3"]) == 1
    assert "not found" in capsys.readouterr().err


@needs_ffprobe
def test_locate_phrase_is_a_fourth_mutually_exclusive_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--phrase` joins `--words`/`--at`/`--span` in the same required
    mutually-exclusive group — the CLI-only hop this argparse extension adds."""
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "locate", clip_id, "--phrase", "w10 w11"]) == 0
    located = json.loads(capsys.readouterr().out)
    assert located["mode"] == "phrase"
    assert (located["first_word"], located["last_word"]) == (2, 3)

    # --phrase alongside --words is refused the same way --words+--at is —
    # argparse's own mutually-exclusive-group usage error, exit 2.
    with pytest.raises(SystemExit) as excinfo:
        main(["-C", str(project), "locate", clip_id, "--words", "0", "--phrase", "w10 w11"])
    assert excinfo.value.code == 2


# -- `restore` --------------------------------------------------------------
#
# The op itself is proven end-to-end over the wire in test_server_stdio.py;
# what only a real `proofcut restore` invocation can prove is that argparse's
# word-range/`--pad`/`--plan` plumbing actually reaches `ops.restore` under
# the right keywords.


@needs_ffprobe
def test_restore_flag_parses_and_reaches_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "cut", clip_id, "2:3"]) == 0
    cut = json.loads(capsys.readouterr().out)
    assert cut["removed"] > 0.0

    # --plan resolves the same numbers a real restore would, without writing.
    assert main(["-C", str(project), "restore", clip_id, "2:3", "--plan"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["plan"] is True
    assert planned["applied"][0]["already_present"] is False
    assert planned["restored"] == pytest.approx(cut["removed"], abs=1e-6)

    # The real call reverses the cut exactly.
    assert main(["-C", str(project), "restore", clip_id, "2:3"]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["restored"] == pytest.approx(cut["removed"], abs=1e-6)
    assert restored["applied"][0]["already_present"] is False

    # A second restore of the same, now-present range is a no-op, not an error.
    assert main(["-C", str(project), "restore", clip_id, "2:3"]) == 0
    noop = json.loads(capsys.readouterr().out)
    assert noop["applied"][0]["already_present"] is True
    assert noop["restored"] == pytest.approx(0.0)


# -- `export --preset`/`--resolution` ----------------------------------------
#
# `ops.export` itself, its presets, and the resolution/melt/NLE refusals are
# proven end-to-end over the wire in test_server_stdio.py; this only guards
# the CLI-specific hop those tests cannot reach — `_resolution`'s own parsing,
# and that `--preset`/`--resolution` reach `ops.export` under the right
# keywords.


def test_resolution_reads_widthxheight() -> None:
    from proofcut.cli import _resolution

    assert _resolution("1920x1080") == (1920, 1080)
    assert _resolution("608x1080") == (608, 1080)


def test_resolution_rejects_garbage() -> None:
    from proofcut.cli import _resolution

    with pytest.raises(argparse.ArgumentTypeError):
        _resolution("banana")
    with pytest.raises(argparse.ArgumentTypeError):
        _resolution("1920")


def test_export_preset_and_resolution_flags_reach_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    # An unknown preset is refused by ops.export, not silently accepted —
    # argparse's own `choices=` can't even construct this call, so this
    # proves the CLI hop reaches ops.export's own check for 'custom' without
    # a resolution.
    out = tmp_path / "out.wav"
    assert main(["-C", str(project), "export", str(out), "--render", "--preset", "custom"]) == 1
    err = capsys.readouterr().err
    assert "custom" in err

    # `--preset`/`--resolution` do reach ops.export under the right keywords:
    # a bare NLE export (the default `--format kdenlive`, no `--render`)
    # combined with `--preset` is refused for the "no bitrate" reason, which
    # only fires if `preset` actually arrived at ops.export.
    assert (
        main(
            [
                "-C",
                str(project),
                "export",
                str(tmp_path / "out.kdenlive"),
                "--preset",
                "youtube",
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "bitrate" in err


def test_slot_assignments_parses_pairs_and_the_newline_escape() -> None:
    """CLI-only plumbing: `--set` pairs, and `\\n` standing in for a line break.

    The escape lives here rather than in `graphics` because it is a shell
    problem — the op and the MCP tool both take a string with real newlines
    already in it.
    """
    assert _slot_assignments(["title=Scream", "year=1996"]) == {
        "title": "Scream",
        "year": "1996",
    }
    assert _slot_assignments([r"quote=one\ntwo"]) == {"quote": "one\ntwo"}
    # A value containing `=` keeps it; only the first one separates.
    assert _slot_assignments(["date_line=watched 2021 — a=b"]) == {
        "date_line": "watched 2021 — a=b"
    }


def test_slot_assignments_refuses_a_pair_with_no_equals() -> None:
    with pytest.raises(ProjectError, match="SLOT=VALUE"):
        _slot_assignments(["title"])


# -- `info` and the 103 KB manifest ---------------------------------------
#
# Descriptions live in the manifest, and `info` prints the manifest — which
# took a described project's `info` to 103 KB of prose in a command whose job
# is being readable at a glance. `describe-ls` is where that text is meant to
# be read, so `info` points at it. Substituting a summary is only honest with
# an escape hatch, which is what `--raw` is and why it is asserted here.


def _described_project(tmp_path: Path) -> Path:
    from proofcut.project import Project

    root = tmp_path / "proj"
    project = Project.create(root)
    manifest = project.read_manifest()
    manifest["clips"] = [
        {"clip_id": "clipa", "source": "/tmp/a.mp4", "duration": 25.0, "has_video": True}
    ]
    manifest["descriptions"] = [
        {"clip_id": "clipa", "src_start": 0.0, "src_end": 12.5, "text": "A kitchen."},
        {"clip_id": "clipa", "src_start": 12.5, "src_end": 25.0, "text": "A car."},
    ]
    project.write_manifest(manifest)
    return root


def test_info_stands_descriptions_down_to_a_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-C", str(_described_project(tmp_path)), "info"]) == 0
    out = json.loads(capsys.readouterr().out)

    assert out["descriptions"] == {
        "count": 2,
        "clips": {"clipa": 2},
        "read": "proofcut describe-ls (or `proofcut info --raw` for the stored entries)",
    }
    # Everything else is still the manifest, verbatim.
    assert out["clips"][0]["clip_id"] == "clipa"


def test_info_raw_still_prints_the_stored_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing else in proofcut can show you what is actually on disk."""
    assert main(["-C", str(_described_project(tmp_path)), "info", "--raw"]) == 0
    out = json.loads(capsys.readouterr().out)

    assert [d["text"] for d in out["descriptions"]] == ["A kitchen.", "A car."]


def test_info_on_an_undescribed_project_is_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The summary is a substitution, so it must not appear where there is
    nothing to substitute — an empty list stays the empty list `describe`
    wrote."""
    assert main(["init", str(tmp_path / "plain")]) == 0
    capsys.readouterr()

    assert main(["-C", str(tmp_path / "plain"), "info"]) == 0
    assert json.loads(capsys.readouterr().out)["descriptions"] == []


@needs_ffprobe
def test_reel_span_parses_and_reaches_ops_as_two_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one hop the stdio suite cannot reach: `reel` takes `START+DURATION`
    on the command line, because that is how a watch-note is phrased, and the
    op takes `start=`/`end=` because that is what an agent can pass. Unpacking
    the pair is CLI-only plumbing, and getting it backwards would read as a
    correct span right up until the reel came out wrong.
    """
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "reel", str(tmp_path / "teaser"), "0:03+5", "--plan"]) == 0
    planned = json.loads(capsys.readouterr().out)

    assert planned["keep"] == pytest.approx([3.0, 8.0]), "a length, not a second timestamp"
    head, tail = planned["cut"]
    assert head == pytest.approx([0.0, 3.0])
    assert tail[0] == pytest.approx(8.0)
    assert tail[1] == pytest.approx(planned["source_duration"])
    assert not (tmp_path / "teaser").exists()


def test_head_flags_parse_and_reach_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--asset/--src-start/--seconds/--fade-in/--fade-out/--gain-db/--reset/
    --plan all reach `ops.head` under the right keywords — `tail`'s own test,
    mirrored, with a registered clip written straight into the manifest
    since `head` needs no ffprobe of its own."""
    from proofcut.project import Project

    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()

    opened = Project.open(project)
    manifest = opened.read_manifest()
    manifest["clips"] = [
        {
            "clip_id": "cold-open",
            "source": "/tmp/cold-open.mp4",
            "duration": 12.0,
            "has_video": True,
            "has_audio": True,
        }
    ]
    opened.write_manifest(manifest)

    assert (
        main(
            [
                "-C", str(project), "head",
                "--asset", "cold-open", "--src-start", "4.2", "--seconds", "6",
                "--fade-in", "0.15", "--fade-out", "0.5", "--gain-db", "15.1", "--plan",
            ]
        )
        == 0
    )  # fmt: skip
    planned = json.loads(capsys.readouterr().out)
    assert planned["written"] is False
    assert planned["head"] == {
        "asset": "cold-open",
        "src_start": 4.2,
        "seconds": 6.0,
        "fade_in": 0.15,
        "fade_out": 0.5,
        "gain_db": 15.1,
    }

    assert main(["-C", str(project), "head", "--asset", "cold-open", "--seconds", "6"]) == 0
    set_result = json.loads(capsys.readouterr().out)
    assert set_result["written"] is True
    assert set_result["head"]["src_start"] == 0.0, "defaults, and --plan left nothing behind"
    assert set_result["head"]["gain_db"] == 0.0

    assert main(["-C", str(project), "head"]) == 0
    read = json.loads(capsys.readouterr().out)
    assert read["head"]["asset"] == "cold-open"
    assert read["written"] is False

    assert main(["-C", str(project), "head", "--reset"]) == 0
    reset_result = json.loads(capsys.readouterr().out)
    assert reset_result["head"] is None
    assert "head" not in json.loads((project / "proofcut.json").read_text(encoding="utf-8"))


def test_tail_flags_parse_and_reach_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--asset/--seconds/--fade/--reset/--plan all reach `ops.tail` under the
    right keywords — this needs only `init`, since `tail` reads and writes
    the manifest and does not touch the timeline."""
    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "-C", str(project), "tail",
                "--asset", "card:outro", "--seconds", "6", "--fade", "0.167", "--plan",
            ]
        )
        == 0
    )  # fmt: skip
    planned = json.loads(capsys.readouterr().out)
    assert planned["written"] is False
    assert planned["tail"] == {"asset": "card:outro", "seconds": 6.0, "fade": 0.167}

    assert main(["-C", str(project), "tail", "--asset", "card:outro", "--seconds", "6"]) == 0
    set_result = json.loads(capsys.readouterr().out)
    assert set_result["written"] is True
    assert set_result["tail"]["fade"] == 0.0, "fade defaults, and --plan left nothing behind"

    assert main(["-C", str(project), "tail"]) == 0
    read = json.loads(capsys.readouterr().out)
    assert read["tail"]["asset"] == "card:outro"
    assert read["written"] is False

    assert main(["-C", str(project), "tail", "--reset"]) == 0
    reset_result = json.loads(capsys.readouterr().out)
    assert reset_result["tail"] is None
    assert "tail" not in json.loads((project / "proofcut.json").read_text(encoding="utf-8"))


@needs_ffprobe
def test_film_check_flags_parse_and_reach_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The positional `reference` and `--reset`/`--plan` all reach
    `ops.film_check` under the right keywords, and a declared reference is
    remembered on the project the same way `canvas`/`tail` remember theirs.
    This needs only a seeded timeline, not a real export — the export-backed
    duration comparison itself is exercised over the wire in
    test_server_stdio.py.
    """
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    # A second recording built the same way ffprobes to the same duration as
    # the source, so the timeline agrees with it without needing a real export.
    reference = project.parent / "reference.wav"
    _make_wav(reference, tones=[(0.0, 2.0)])

    assert main(["-C", str(project), "film-check", str(reference), "--plan"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["reference_source"] == "argument"
    assert planned["agrees"] is True
    assert "reference" not in json.loads((project / "proofcut.json").read_text(encoding="utf-8")), (
        "--plan must not write"
    )

    assert main(["-C", str(project), "film-check", str(reference)]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["reference"] == str(reference)
    manifest = json.loads((project / "proofcut.json").read_text(encoding="utf-8"))
    assert manifest["reference"] == str(reference)

    # No argument now reuses what was just declared.
    assert main(["-C", str(project), "film-check"]) == 0
    reread = json.loads(capsys.readouterr().out)
    assert reread["reference_source"] == "declared"
    assert reread["agrees"] is True

    assert main(["-C", str(project), "film-check", "--reset"]) == 0
    reset_result = json.loads(capsys.readouterr().out)
    assert reset_result["reference"] is None
    assert "reference" not in json.loads((project / "proofcut.json").read_text(encoding="utf-8"))


def test_finish_check_flags_parse_and_reach_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--hold`'s comma-delimited spec parses into `finish_check`'s own
    `{"name","start","length","ducked"}` shape, and every other flag reaches
    `ops.finish_check` under the right keyword — the real behind-it check
    (real ffmpeg/whisper) is covered by test_ops_finish_check*.py and the
    stdio suite; this only guards the CLI's own parsing and wiring, the way
    test_film_check_flags_parse_and_reach_ops does one command over.
    """
    project = tmp_path / "proj"
    final = tmp_path / "final.mp4"
    final.write_bytes(b"not real media, ops.finish_check is stubbed below")

    captured: dict[str, object] = {}

    def _stub(path: object, final_arg: object, **kwargs: object) -> dict[str, object]:
        captured["path"] = path
        captured["final"] = final_arg
        captured.update(kwargs)
        return {"faults": 0, "ok": True}

    monkeypatch.setattr(ops, "finish_check", _stub)

    assert main([
        "-C", str(project),
        "finish-check", str(final),
        "--hold", "miggs,42.0,1.9",
        "--hold", "point-taken,50.0,2.1,ducked",
        "--prepend-seconds", "11.5",
        "--duration-tolerance", "0.75",
        "--pix-th", "0.2",
        "--black-min-duration", "0.1",
        "--windowed-model", "medium",
        "--window", "8.0",
        "--overlap", "4.0",
        "--recheck-pad", "6.0",
        "--language", "en",
        "--clip-id", "vo",
        "--transcript", "heard.json",
    ]) == 0  # fmt: skip
    result = json.loads(capsys.readouterr().out)
    assert result == {"faults": 0, "ok": True}

    assert captured["final"] == str(final)
    assert captured["holds"] == [
        {"name": "miggs", "start": 42.0, "length": 1.9, "ducked": False},
        {"name": "point-taken", "start": 50.0, "length": 2.1, "ducked": True},
    ]
    assert captured["prepend_seconds"] == 11.5
    assert captured["duration_tolerance"] == 0.75
    assert captured["pix_th"] == 0.2
    assert captured["black_min_duration"] == 0.1
    assert captured["windowed_model"] == "medium"
    assert captured["window"] == 8.0
    assert captured["overlap"] == 4.0
    assert captured["recheck_pad"] == 6.0
    assert captured["language"] == "en"
    assert captured["clip_id"] == "vo"
    assert captured["transcript_path"] == "heard.json"


def test_finish_check_with_no_hold_flags_passes_none_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--hold` at all reaches `ops.finish_check` as `holds=None` — the
    sentinel WORK-ORDERS ruling 5 gives the stored-holds fallback, never an
    empty list (which would mean "explicitly no holds")."""
    project = tmp_path / "proj"
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stubbed")
    captured: dict[str, object] = {}

    def _stub(path: object, final_arg: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"faults": 0, "ok": True}

    monkeypatch.setattr(ops, "finish_check", _stub)

    assert main(["-C", str(project), "finish-check", str(final)]) == 0
    capsys.readouterr()

    assert captured["holds"] is None
    assert captured["prepend_seconds"] is None


def test_hold_rm_cli_reaches_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`proofcut hold rm <clip> <idx>` parses into `ops.hold_rm`'s own three
    positionals, and the CLI's `_emit` prints exactly what it returns —
    `test_finish_check_flags_parse_and_reach_ops`'s own wiring-only
    discipline, the real op covered at the ops and stdio layers."""
    project = tmp_path / "proj"
    captured: dict[str, object] = {}

    def _stub(path: object, clip_id: object, gap_word_index: object) -> dict[str, object]:
        captured["path"] = path
        captured["clip_id"] = clip_id
        captured["gap_word_index"] = gap_word_index
        return {
            "clip_id": clip_id,
            "gap_word_index": gap_word_index,
            "removed": {"asset": "film", "cue_word_index": 2},
        }

    monkeypatch.setattr(ops, "hold_rm", _stub)

    assert main(["-C", str(project), "hold", "rm", "vo", "3"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert captured["path"] == str(project)
    assert captured["clip_id"] == "vo"
    assert captured["gap_word_index"] == 3
    assert result["removed"] == {"asset": "film", "cue_word_index": 2}


def test_hold_check_cli_reaches_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`proofcut hold check <render>` parses into `ops.hold_check`'s own two
    positionals — the real transcription/seam machinery is covered at the
    ops and stdio layers; this only guards the CLI's own parsing and
    dispatch."""
    project = tmp_path / "proj"
    render = tmp_path / "out.mp4"
    captured: dict[str, object] = {}

    def _stub(path: object, render_arg: object) -> dict[str, object]:
        captured["path"] = path
        captured["render"] = render_arg
        return {"holds": [], "count": 0, "faults": 0}

    monkeypatch.setattr(ops, "hold_check", _stub)

    assert main(["-C", str(project), "hold", "check", str(render)]) == 0
    result = json.loads(capsys.readouterr().out)

    assert captured["path"] == str(project)
    assert captured["render"] == str(render)
    assert result == {"holds": [], "count": 0, "faults": 0}


@pytest.mark.skipif(
    shutil.which("magick") is None or shutil.which("ffmpeg") is None,
    reason="the render half of the font report needs ImageMagick and ffmpeg with libass",
)
def test_fonts_without_a_project_reports_the_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`-C` defaults to "." for every subcommand, so without this the font
    report would refuse from any directory that is not a project — and a font
    is not project state. `project_given` is what separates "asked about this
    project's caption style" from "asked about proofcut's default"."""
    assert main(["fonts"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["project"] is None
    assert result["caption_font"] == result["default_font"]
    assert result["fonts"][result["caption_font"]]["render"]["drew"] is True


@needs_ffprobe
def test_proxy_force_flag_parses_and_reaches_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one hop test_server_stdio.py cannot reach: argparse's `store_true`
    arriving at `ops.proxy_transcode` under the right keyword. The transcode
    itself is covered by real encodes in test_ops_proxy.py, so this stubs it —
    what is under test is the wiring, not ffmpeg."""
    from proofcut import cli as cli_module

    seen: dict[str, object] = {}

    def _stub(path: object, clip_id: str, *, force: bool = False) -> dict[str, object]:
        seen["clip_id"] = clip_id
        seen["force"] = force
        return {"clip_id": clip_id, "built": force}

    monkeypatch.setattr(cli_module.ops, "proxy_transcode", _stub)

    assert main(["-C", str(tmp_path / "proj"), "proxy", "some-clip"]) == 0
    capsys.readouterr()
    assert seen == {"clip_id": "some-clip", "force": False}

    assert main(["-C", str(tmp_path / "proj"), "proxy", "some-clip", "--force"]) == 0
    capsys.readouterr()
    assert seen["force"] is True


def test_reframe_interp_flag_parses_and_reaches_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one hop the stdio suite cannot reach for the keyframed move:
    argparse's `store_true` arriving at `ops.reframe` under `interp`, and
    `--at` arriving as `src_start` beside it. The flag is worthless without
    `--at` — the head can never slide — so the pair is what gets checked, not
    the flag alone (HISTORY.md § The keyframed move).

    The clip is written into the manifest rather than imported: a reframe is
    arithmetic over a declared shape, and ffprobe is not what is under test.
    """
    from proofcut.project import Project

    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()

    opened = Project.open(project)
    manifest = opened.read_manifest()
    manifest["clips"] = [
        {
            "clip_id": "cold-open",
            "source": "/tmp/cold-open.mp4",
            "duration": 12.0,
            "has_video": True,
            "has_audio": True,
            "width": 1920,
            "height": 816,
        }
    ]
    opened.write_manifest(manifest)

    assert main(["-C", str(project), "canvas", "1080x1920"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "reframe", "cold-open", "--rect", "200,0,459,816"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "-C",
                str(project),
                "reframe",
                "cold-open",
                "--rect",
                "1200,0,459,816",
                "--at",
                "4.0",
                "--interp",
            ]
        )
        == 0
    )
    slid = json.loads(capsys.readouterr().out)
    by_start = {w["src_start"]: w["interp"] for w in slid["clips"][0]["windows"]}
    assert by_start == {0.0: False, 4.0: True}

    # Without the flag the same window steps, and the key is not written at all.
    assert main(["-C", str(project), "reframe", "cold-open", "--rect", "600,0,459,816", "--at", "8.0"]) == 0
    stepped = json.loads(capsys.readouterr().out)
    assert stepped["clips"][0]["windows"][2]["interp"] is False
    stored = json.loads((project / "proofcut.json").read_text(encoding="utf-8"))
    at_eight = [r for r in stored["reframe"] if r.get("src_start") == 8.0]
    assert at_eight and "interp" not in at_eight[0], (
        "absent means steps, which is what every window written before this meant"
    )


@needs_ffprobe
def test_import_edit_flags_parse_and_reach_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one hop test_server_stdio.py cannot reach: the positional document
    and `--clip-id`/`--plan` arriving at `ops.import_edit` under the right
    keywords. The parse and the refusals are covered against ops in
    test_ops_import_edit.py and over the wire in test_server_stdio.py, so this
    stubs the op — what is under test is the wiring."""
    from proofcut import cli as cli_module

    seen: dict[str, object] = {}

    def _stub(path: object, document: object, *, clip_id: str | None = None,
              plan: bool = False) -> dict[str, object]:
        seen["document"] = str(document)
        seen["clip_id"] = clip_id
        seen["plan"] = plan
        return {"segments": 0}

    monkeypatch.setattr(cli_module.ops, "import_edit", _stub)

    project = tmp_path / "proj"
    assert main(["-C", str(project), "import-edit", "cut.kdenlive"]) == 0
    capsys.readouterr()
    assert seen == {"document": "cut.kdenlive", "clip_id": None, "plan": False}

    assert (
        main(["-C", str(project), "import-edit", "cut.kdenlive", "--clip-id", "vo", "--plan"])
        == 0
    )
    capsys.readouterr()
    assert seen == {"document": "cut.kdenlive", "clip_id": "vo", "plan": True}


def test_import_audio_flags_parse_and_reach_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--mix` and `--audio-stream` are the two ways to comply with the
    multi-mic refusal, so a flag that parses but never arrives would leave a
    two-mic container un-importable with the message saying otherwise.
    `--audio-stream 0` in particular must arrive as `0`, not as `None` —
    they are the same falsy value and mean opposite things here.
    """
    from proofcut import cli as cli_module

    seen: dict[str, object] = {}

    def _stub(path: object, source: object, *, clip_id: str | None = None, copy: bool = False,
              mix: bool = False, audio_stream: int | None = None,
              sheet: bool = True) -> dict[str, object]:
        seen.update({"mix": mix, "audio_stream": audio_stream, "copy": copy})
        return {"clip_id": "c"}

    monkeypatch.setattr(cli_module.ops, "import_media", _stub)
    project = tmp_path / "proj"

    assert main(["-C", str(project), "import", "cohost.mkv"]) == 0
    capsys.readouterr()
    assert seen == {"mix": False, "audio_stream": None, "copy": False}

    assert main(["-C", str(project), "import", "cohost.mkv", "--mix"]) == 0
    capsys.readouterr()
    assert seen == {"mix": True, "audio_stream": None, "copy": False}

    assert main(["-C", str(project), "import", "cohost.mkv", "--audio-stream", "0"]) == 0
    capsys.readouterr()
    assert seen == {"mix": False, "audio_stream": 0, "copy": False}


# -- `role`, `assets`, `properties`, `thumbnail` -----------------------------
#
# These landed this session with no CLI-level parsing coverage of their own,
# unlike their siblings above. Each has a shape the stdio suite's tool-level
# tests do not exercise: `role`'s optional positional plus `--reset` (and
# argparse's own `choices` guard on the positional), `properties`'s two
# independent `--clip-id`/`--word-index` narrowing flags, and `thumbnail`'s
# positional `at` (typed float) plus `--interval`'s real default reaching
# `ops.thumbnail` under the right keyword. `assets` takes no arguments at
# all, so its coverage is just that the subcommand dispatches and returns
# the shape a properties inspector expects.


def _write_clip(project: Path, clip_id: str = "c1", **fields: object) -> None:
    """A clip record written straight into the manifest — no real media
    needed for `role` (manifest-only) or to prove `thumbnail`'s own parsing
    reaches `ops.thumbnail` (which fails cleanly at "media is missing from
    disk" only after `clip_id`/`at`/`interval` have already been parsed and
    the clip has already been found, which is what is under test here).
    """
    manifest = json.loads((project / "proofcut.json").read_text(encoding="utf-8"))
    clip = {
        "clip_id": clip_id,
        "source": "/nonexistent/media.mp4",
        "duration": 12.0,
        "has_video": True,
        "has_audio": True,
        "width": 1920,
        "height": 1080,
        **fields,
    }
    manifest.setdefault("clips", []).append(clip)
    (project / "proofcut.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_role_reads_with_no_role_and_sets_and_resets_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    _write_clip(project)

    assert main(["-C", str(project), "role", "c1"]) == 0
    assert json.loads(capsys.readouterr().out)["role"] is None

    assert main(["-C", str(project), "role", "c1", "voiceover"]) == 0
    assert json.loads(capsys.readouterr().out)["role"] == "voiceover"

    assert main(["-C", str(project), "role", "c1", "--reset"]) == 0
    assert json.loads(capsys.readouterr().out)["role"] is None


def test_role_positional_choices_refuse_before_reaching_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`choices=sorted(ops.CLIP_ROLES)` is argparse's own guard — a value
    outside it is a usage error (exit 2) that never reaches `ops.clip_role`,
    which is what separates this from `ops.clip_role`'s own message for the
    same bad value (a `ProjectError`, exit 1) — both refuse, but only one of
    them is this command's own plumbing.
    """
    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()

    with pytest.raises(SystemExit) as excinfo:
        main(["-C", str(project), "role", "c1", "not-a-role"])
    assert excinfo.value.code == 2


def test_assets_takes_no_arguments_and_reports_both_kinds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    _write_clip(project)

    assert main(["-C", str(project), "assets"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [c["clip_id"] for c in out["clips"]] == ["c1"]
    assert out["cards"] == []


@needs_ffprobe
def test_finish_report_subcommand_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`finish-report` takes no arguments — just the CLI plumbing to
    `ops.finish_report`, already exercised end to end over the wire in
    test_server_stdio.py. Composes `ops.status`, which needs a seeded
    timeline, same as `properties` below."""
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "finish-report"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {
        "duration",
        "canvas",
        "captions",
        "picture",
        "marks",
        "seams",
        "sources",
        "unused_clips",
        "holds",
        "framing",
        "continuity",
        "last_render",
        "flags",
    }


@needs_ffprobe
def test_properties_narrows_on_clip_id_and_refuses_word_index_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`properties` composes `ops.status`, which needs a seeded timeline —
    unlike `role`/`assets`/`thumbnail` above, a bare manifest clip is not
    enough, so this one goes through the real `import`/`seed` pipeline
    `_make_sources` already builds for the tests above it."""
    project = tmp_path / "proj"
    audio, transcript = _make_sources(project.parent)

    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "import", str(audio)]) == 0
    clip_id = json.loads(capsys.readouterr().out)["clip_id"]
    assert main(["-C", str(project), "attach-transcript", clip_id, str(transcript)]) == 0
    capsys.readouterr()
    assert main(["-C", str(project), "seed", clip_id, "--keep-silences"]) == 0
    capsys.readouterr()

    assert main(["-C", str(project), "properties"]) == 0
    whole = json.loads(capsys.readouterr().out)
    assert "clip" not in whole

    assert main(["-C", str(project), "properties", "--clip-id", clip_id]) == 0
    narrowed = json.loads(capsys.readouterr().out)
    assert narrowed["clip"]["clip_id"] == clip_id

    # `--word-index` alone, with no `--clip-id`, is ops.properties's own
    # refusal — reached here rather than an argparse usage error, since
    # nothing about the pair is mutually exclusive at the parser level.
    assert main(["-C", str(project), "properties", "--word-index", "3"]) == 1
    assert "word_index needs a clip_id" in capsys.readouterr().err


@needs_ffprobe
def test_thumbnail_parses_at_and_interval_and_reaches_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`at` (positional, `type=float`) and `--interval` (optional, defaulting
    to `ops.THUMB_INTERVAL`) both reach `ops.thumbnail` under the right
    keywords — proven by getting *past* clip lookup and failing at the next
    guard (missing media on disk), rather than at an argparse usage error or
    at "no such clip"."""
    project = tmp_path / "proj"
    assert main(["-C", str(project), "init"]) == 0
    capsys.readouterr()
    _write_clip(project)

    assert main(["-C", str(project), "thumbnail", "c1", "4.25"]) == 1
    assert "media is missing from disk" in capsys.readouterr().err

    assert main(["-C", str(project), "thumbnail", "c1", "4.25", "--interval", "2"]) == 1
    assert "media is missing from disk" in capsys.readouterr().err

    # A non-numeric `at` never reaches ops at all — argparse's `type=float`
    # rejects it as a usage error.
    with pytest.raises(SystemExit) as excinfo:
        main(["-C", str(project), "thumbnail", "c1", "not-a-number"])
    assert excinfo.value.code == 2


def test_attribute_speakers_pairs_each_stream_with_its_label() -> None:
    """The two lists are positional and the op checks them against each other,
    so the parser's job is only to keep the order they were typed in."""
    from proofcut.cli import _build_parser

    args = _build_parser().parse_args(
        ["attribute-speakers", "vo", "--stream", "0", "--label", "ana",
         "--stream", "1", "--label", "ben"]
    )  # fmt: skip

    assert args.command == "attribute-speakers"
    assert args.clip_id == "vo"
    assert args.streams == [0, 1]
    assert args.labels == ["ana", "ben"]
    assert args.apply is False


def test_attribute_speakers_defaults_leave_every_choice_to_the_op() -> None:
    """No streams and no labels means "every mic the container holds", which is
    the op's default rather than a number the CLI picks."""
    from proofcut.cli import _build_parser

    args = _build_parser().parse_args(["attribute-speakers", "vo"])

    assert args.streams is None
    assert args.labels is None
    assert args.margin_db == ops.spk.MARGIN_DB


def test_a_path_windows_refuses_as_too_long_is_one_line_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`Project.create` refuses the common case up front; a clip id or render
    name that spends the headroom later still reaches Win32's 206, and a person
    gets the fix rather than a stack."""
    where = "C:\\deep\\cache\\thumbs\\a-very-long-clip"
    too_long = OSError(2, "The filename or extension is too long", where)
    too_long.winerror = 206  # type: ignore[attr-defined]

    def refuse(*args: object, **kwargs: object) -> None:
        raise too_long

    monkeypatch.setattr(ops, "init", refuse)
    assert main(["init", str(tmp_path / "proj")]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.startswith(f"proofcut: Windows refused a path as too long ({len(where)} characters: {where})")
    assert "LongPathsEnabled 1" in err


def test_any_other_os_error_still_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(ops, "init", refuse)
    with pytest.raises(PermissionError):
        main(["init", str(tmp_path / "proj")])


def test_the_missing_transcript_refusal_names_commands_that_parse(tmp_path: Path) -> None:
    """The refusal told every client to run `proofcut transcript attach`, which
    is not a command: `transcript` takes a clip id, so `attach` parsed as one
    and the words after it were refused. A first-token check passes that line
    too, so each named command is parsed whole, placeholders filled in.
    """
    import re
    import shlex

    from proofcut import transcript as tx
    from proofcut.cli import _build_parser
    from proofcut.project import Project

    project = Project.create(tmp_path / "proj")
    with pytest.raises(tx.TranscriptError) as refused:
        ops._transcript(project, "vo")
    commands = re.findall(r"`(proofcut [^`]+)`", str(refused.value))
    assert commands
    for command in commands:
        argv = shlex.split(re.sub(r"<[^>]*>", "X", command))[1:]
        _build_parser().parse_args(argv)


def test_brief_prints_the_prompt_the_server_ships(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The prompts' CLI twin prints the text itself, and `-C` fills the
    project the way a bound server does. docs/plans/MCP.md § Step 7."""
    from proofcut import briefs

    assert main(["brief", "cut", "/footage", "--length", "90s"]) == 0
    assert capsys.readouterr().out == briefs.cut("/footage", length="90s")

    assert main(["-C", str(tmp_path), "brief", "review"]) == 0
    assert capsys.readouterr().out == briefs.review(project=str(tmp_path.resolve()))

    assert main(["brief", "film"]) == 1
    assert "material folder" in capsys.readouterr().err
