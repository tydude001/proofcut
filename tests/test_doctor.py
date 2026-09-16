"""`proofcut doctor` — the probes, and the sentence after each ✗.

The report itself is trivially true on a working box, which is exactly why
these tests drive the *failing* shapes instead: a binary that is not there, an
auto-editor that is PyPI's stale fork, a melt that exits 0 with nothing to say,
a voice that is deliberately unset. Doctor's whole value is what it says in
those cases, so what is asserted is the named trap and the fix, not the ✗.

Every probe is monkeypatched at the resolver rather than shelled out, so this
file runs identically on a box with none of the six installed.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from stubs import write_stub

from proofcut import asr, autoeditor, doctor, graphics, ops, picture, tts
from proofcut.cli import main


@pytest.fixture(autouse=True)
def _no_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """A voice on the developer's box would change what these tests measure."""
    monkeypatch.delenv("PROOFCUT_TTS_VOICE", raising=False)


def _row(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(r for r in rows if r["name"] == name)


# -- shape ----------------------------------------------------------------


def test_every_row_carries_every_key() -> None:
    """A renderer should never need `.get` — an absent key and a null one
    would look the same to it, and one of them is a bug."""
    payload = doctor.report()
    keys = {"name", "what", "ok", "looked_for", "found", "version", "note", "why", "fix"}
    for row in payload["required"] + payload["optional"]:
        assert keys <= set(row), row["name"]


def test_ok_reads_the_required_section_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """An optional capability that is absent gates a feature, not the install."""
    monkeypatch.setattr(
        doctor, "_vlm_entry", lambda: doctor._entry("PROOFCUT_VLM", "describe", ok=False)
    )
    monkeypatch.setattr(
        doctor, "_face_entry", lambda: doctor._entry("PROOFCUT_FACE", "reframe", ok=False)
    )
    monkeypatch.setattr(doctor, "_tts_entry", lambda: doctor._entry("PROOFCUT_TTS", "vo", ok=False))
    monkeypatch.setattr(doctor, "_magick_entry", lambda: doctor._entry("magick", "cards", ok=False))
    payload = doctor.report()
    assert payload["ok"] == all(r["ok"] for r in payload["required"])
    assert not any(r["ok"] for r in payload["optional"])


def test_doctor_needs_no_project(tmp_path: Path) -> None:
    """The one op that answers a question asked before a project exists."""
    assert ops.doctor()["proofcut"]
    assert not list(tmp_path.iterdir())  # and it wrote nothing anywhere


# -- a missing required binary -------------------------------------------


def test_missing_ffmpeg_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    row = doctor._ffmpeg_entry("ffmpeg", "everything")
    assert row["ok"] is False
    assert "not on PATH" in row["why"]
    assert "install ffmpeg" in row["fix"]


_FFMPEG_VERSION = "ffmpeg version 8.1.2 Copyright (c) 2000-2026 the FFmpeg developers\n"
_ENCODERS_FREE = (
    "Encoders:\n V....D libopenh264          OpenH264 H.264 / AVC (codec h264)\n"
    " V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)\n A....D aac  AAC (Advanced Audio Coding)\n"
)
_ENCODERS_FULL = _ENCODERS_FREE + " V....D libx264              libx264 H.264 / AVC (codec h264)\n"


def _ffmpeg_answers(monkeypatch: pytest.MonkeyPatch, encoders: str) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        doctor, "_run", lambda cmd: ((encoders if "-encoders" in cmd else _FFMPEG_VERSION), "", 0)
    )


def test_an_ffmpeg_without_libx264_is_not_ffmpeg_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fedora's default `ffmpeg-free` ran, printed its banner and was called ✓,
    and the demo's first command died on `Unknown encoder 'libx264'`.
    HISTORY.md § A stranger's install, on a clean Fedora."""
    _ffmpeg_answers(monkeypatch, _ENCODERS_FREE)
    row = doctor._ffmpeg_entry("ffmpeg", "everything")
    assert row["ok"] is False
    assert "libx264" in row["why"]
    assert "dnf swap ffmpeg-free ffmpeg" in row["fix"]


@pytest.mark.parametrize("encoders", [_ENCODERS_FULL, ""])
def test_libx264_or_an_unanswered_listing_passes_ffmpeg(monkeypatch: pytest.MonkeyPatch, encoders: str) -> None:
    """An empty listing is a probe that did not answer, never a missing encoder."""
    _ffmpeg_answers(monkeypatch, encoders)
    row = doctor._ffmpeg_entry("ffmpeg", "everything")
    assert row["ok"] is True
    assert row["version"] == "8.1.2"


_FILTERS_HEAD = "Filters:\n  T.. = Timeline support\n  ------\n .. abench            A->A       Benchmark part of a filtergraph.\n"
_FILTER_DRAWTEXT = " T. drawtext          V->V       Draw text on top of video frames using libfreetype library.\n"
_FILTER_ASS = " .. ass               V->V       Render ASS subtitles onto input video using the libass library.\n"


def _ffmpeg_filters(monkeypatch: pytest.MonkeyPatch, filters: str) -> None:
    """An ffmpeg with libx264, answering `-filters` with `filters`."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(cmd: list[str]) -> tuple[str, str, int]:
        if "-encoders" in cmd:
            return _ENCODERS_FULL, "", 0
        if "-filters" in cmd:
            return filters, "", 0
        return _FFMPEG_VERSION, "", 0

    monkeypatch.setattr(doctor, "_run", run)


def test_homebrews_plain_ffmpeg_is_not_ffmpeg_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """Homebrew's `ffmpeg` has libx264 and no freetype or libass. Doctor called
    it ✓ on the first mac-demo run, and `make_demo.py` died on `No such filter:
    'drawtext'`. HISTORY.md § The Mac test in CI."""
    _ffmpeg_filters(monkeypatch, _FILTERS_HEAD)
    row = doctor._ffmpeg_entry("ffmpeg", "everything")
    assert row["ok"] is False
    assert "drawtext or ass" in row["why"]
    assert "freetype and libass" in row["why"]
    assert "brew install ffmpeg-full" in row["fix"]
    assert "brew --prefix ffmpeg-full" in row["fix"]


@pytest.mark.parametrize(
    ("filters", "missing", "library"),
    [(_FILTERS_HEAD + _FILTER_ASS, "drawtext", "freetype"), (_FILTERS_HEAD + _FILTER_DRAWTEXT, "ass", "libass")],
)
def test_ffmpeg_names_only_the_filter_it_lacks(
    monkeypatch: pytest.MonkeyPatch, filters: str, missing: str, library: str
) -> None:
    _ffmpeg_filters(monkeypatch, filters)
    row = doctor._ffmpeg_entry("ffmpeg", "everything")
    assert row["ok"] is False
    assert row["why"].startswith(f"this ffmpeg has no {missing} filter — it was built without {library}.")


def test_a_filter_name_inside_a_description_is_not_the_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """`subtitles` says "using the libass library" — a word in a description
    must not stand in for the filter's own name column."""
    decoy = " .. subtitles         V->V       Render text via drawtext or ass using libass.\n"
    _ffmpeg_filters(monkeypatch, _FILTERS_HEAD + decoy)
    assert doctor._ffmpeg_entry("ffmpeg", "everything")["ok"] is False


@pytest.mark.parametrize("filters", [_FILTERS_HEAD + _FILTER_DRAWTEXT + _FILTER_ASS, "", "garbage\n"])
def test_both_filters_or_an_unanswered_listing_passes_ffmpeg(monkeypatch: pytest.MonkeyPatch, filters: str) -> None:
    """A listing with no `Filters:` header did not answer, the encoder rule."""
    _ffmpeg_filters(monkeypatch, filters)
    assert doctor._ffmpeg_entry("ffmpeg", "everything")["ok"] is True


def test_ffprobe_is_not_asked_for_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffprobe draws nothing; the same keg's ffprobe must not fail on ffmpeg's rule."""
    _ffmpeg_filters(monkeypatch, _FILTERS_HEAD)
    monkeypatch.setattr(doctor, "_run", lambda cmd: ("ffprobe version 8.1.2 Copyright\n", "", 0))
    assert doctor._ffmpeg_entry("ffprobe", "probing")["ok"] is True


def test_this_boxs_ffmpeg_has_both_text_filters() -> None:
    """The listing parse against a real `ffmpeg -filters`, not only the fixtures."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("no ffmpeg on PATH")
    listing = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=False).stdout
    if not all(re.search(rf"\s{name}\s", listing) for name in doctor.TEXT_FILTERS):
        pytest.skip("this ffmpeg is built without freetype or libass")
    assert "filter" not in (doctor._ffmpeg_entry("ffmpeg", "everything")["why"] or "")


def test_missing_whisper_carries_the_resolution_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chain is three steps and none of them is obvious, so it is printed."""

    def refuse() -> Path:
        raise asr.ASRError("whisper not found. Looked at $PROOFCUT_WHISPER (unset), then PATH")

    monkeypatch.setattr(doctor.asr, "whisper_binary", refuse)
    row = doctor._whisper_entry()
    assert row["ok"] is False
    assert "PROOFCUT_WHISPER" in row["looked_for"]
    assert "does not have to live in proofcut's own venv" in row["fix"]


def test_the_whisper_fix_pins_the_python_an_intel_mac_can_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """torch's macOS x86_64 wheels stop at cp312, so an unpinned `uv tool
    install` does not resolve there on 3.13. Both trial kits pin 3.12, and the
    advice a stranger follows has to as well. docs/plans/INSTALL.md,
    measurement 6."""

    def refuse() -> Path:
        raise asr.ASRError("whisper not found.")

    monkeypatch.setattr(doctor.asr, "whisper_binary", refuse)
    fix = doctor._whisper_entry()["fix"]
    assert "uv tool install --python 3.12 openai-whisper" in fix
    assert "--torch-backend cpu" in fix


def test_whisper_on_disk_but_unstartable_is_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """A venv that has lost torch resolves fine and dies minutes into a job."""
    monkeypatch.setattr(doctor.asr, "whisper_binary", lambda: Path("/nope/whisper"))
    monkeypatch.setattr(
        doctor, "_run", lambda cmd: ("", "ModuleNotFoundError: No module named 'torch'", 1)
    )
    row = doctor._whisper_entry()
    assert row["ok"] is False
    assert "cannot start" in row["why"]
    assert "torch" in row["fix"]


def test_whisper_without_word_timestamps_is_noted_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It starts, so it is not broken — but every cut proofcut makes is word-indexed."""
    monkeypatch.setattr(doctor.asr, "whisper_binary", lambda: Path("/bin/whisper"))
    monkeypatch.setattr(doctor, "_run", lambda cmd: ("usage: whisper [-h]", "", 0))
    row = doctor._whisper_entry()
    assert row["ok"] is True
    assert "--word_timestamps" in row["note"]


# -- auto-editor: the stale-fork trap ------------------------------------


def test_stale_auto_editor_is_refused_by_major(monkeypatch: pytest.MonkeyPatch) -> None:
    """29.3.1 is PyPI's fork of a different program wearing the same name."""
    monkeypatch.setattr(doctor.autoeditor, "binary", lambda: "/usr/bin/auto-editor")
    monkeypatch.setattr(doctor, "_run", lambda cmd: ("29.3.1\n", "", 0))
    row = doctor._auto_editor_entry()
    assert row["ok"] is False
    assert row["version"] == "29.3.1"
    assert "needs 31 or newer" in row["why"]
    assert "pip install auto-editor" in row["fix"]
    assert "GitHub release" in row["fix"]


def test_current_auto_editor_reports_the_gate_as_designed_around(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paid-key gate is real and proofcut routes around it — say so, don't scare."""
    monkeypatch.setattr(doctor.autoeditor, "binary", lambda: "/usr/bin/auto-editor")
    monkeypatch.setattr(doctor, "_run", lambda cmd: ("31.4.2\n", "", 0))
    row = doctor._auto_editor_entry()
    assert row["ok"] is True
    assert "Nothing here needs the key" in row["note"]


def test_an_auto_editor_that_cannot_load_is_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean Ubuntu has no libgomp, and the loader's refusal came back as the
    version string beside a ✓ (INSTALL.md § Step 2, the first container run).
    A version is a number, and exit 127 is not one."""
    monkeypatch.setattr(doctor.autoeditor, "binary", lambda: "/deps/auto-editor")
    loader = (
        "/deps/auto-editor: error while loading shared libraries: libgomp.so.1: "
        "cannot open shared object file: No such file or directory\n"
    )
    monkeypatch.setattr(doctor, "_run", lambda cmd: ("", loader, 127))
    row = doctor._auto_editor_entry()
    assert row["ok"] is False
    assert row["version"] is None
    assert "libgomp.so.1" in row["why"]


def test_absent_auto_editor_still_names_the_stale_pypi_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> str:
        raise autoeditor.AutoEditorError("auto-editor not found.")

    monkeypatch.setattr(doctor.autoeditor, "binary", refuse)
    row = doctor._auto_editor_entry()
    assert row["ok"] is False
    assert "stale fork" in row["fix"]


# -- melt: probed by output, never by exit code --------------------------


def test_melt_exiting_zero_with_no_banner_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """melt's exit code is evidence of nothing — CLAUDE.md, measured twice."""
    monkeypatch.setattr(sys, "platform", "linux")  # the fix names the flatpak there
    monkeypatch.setattr(doctor.picture, "melt_command", lambda: ["melt"])
    monkeypatch.setattr(doctor, "_run", lambda cmd: ("Failed to load\n", "", 0))
    row = doctor._melt_entry()
    assert row["ok"] is False
    assert "exit code is not evidence" in row["why"]
    assert "org.kde.kdenlive" in row["fix"]


def test_melt_banner_is_what_passes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.picture, "melt_command", lambda: ["melt"])
    monkeypatch.setattr(doctor, "_run", lambda cmd: ("melt 7.40.0\nCopyright…\n", "", 0))
    row = doctor._melt_entry()
    assert row["ok"] is True
    assert row["version"] == "7.40.0"


@pytest.mark.parametrize("banner", ["mlt-melt 7.40.0", "melt-7 7.40.0"])
def test_fedoras_melt_banner_passes_it_too(monkeypatch: pytest.MonkeyPatch, banner: str) -> None:
    """melt names itself after argv[0], and Fedora's MLT has no `melt` to run."""
    monkeypatch.setattr(doctor.picture, "melt_command", lambda: ["/usr/bin/mlt-melt"])
    monkeypatch.setattr(doctor, "_run", lambda cmd: (f"{banner}\nCopyright (C) 2002-2026 Meltytech, LLC\n", "", 0))
    row = doctor._melt_entry()
    assert row["ok"] is True
    assert row["version"] == "7.40.0"


def test_freezes_melt_is_not_melt(monkeypatch: pytest.MonkeyPatch) -> None:
    """What Fedora's `melt` package answers `-version` with, measured."""
    monkeypatch.setattr(doctor.picture, "melt_command", lambda: ["/usr/bin/melt"])
    monkeypatch.setattr(
        doctor, "_run", lambda cmd: ("Unknown flag: 'e'; \nUsage: freeze [-cdfvVg] [file | +type ...]\n", "", 0)
    )
    assert doctor._melt_entry()["ok"] is False


def test_missing_melt_says_what_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-source cuts render without it; only layered timelines do not."""

    def refuse() -> list[str]:
        raise picture.PictureError("melt not found.")

    monkeypatch.setattr(doctor.picture, "melt_command", refuse)
    row = doctor._melt_entry()
    assert row["ok"] is False
    assert "single-source cuts still render through auto-editor" in row["fix"]


# -- magick ---------------------------------------------------------------


def test_magick_without_rsvg_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A different SVG renderer draws cards nobody measured on this box."""
    monkeypatch.setattr(doctor.graphics, "magick_command", lambda: ["magick"])
    monkeypatch.setattr(
        doctor, "_run", lambda cmd: (("Version: ImageMagick 7.1.2-27 Q16", "", 0) if "-version" in cmd else ("PNG* rw+\n", "", 0))
    )
    row = doctor._magick_entry()
    assert row["ok"] is True
    assert "RSVG coder" in row["note"]


def test_missing_magick_says_everything_else_works(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> list[str]:
        raise graphics.GraphicsError("magick not found.")

    monkeypatch.setattr(doctor.graphics, "magick_command", refuse)
    row = doctor._magick_entry()
    assert row["ok"] is False
    assert "everything else works" in row["fix"]


def test_missing_magick_is_unavailable_and_never_moves_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ubuntu 24.04 packages only ImageMagick 6, so a stranger there could never
    see `ok` although DEMO.md's one card is a step it says to skip without magick. Cards are one feature, like the
    other optional rows. HISTORY.md § A stranger's install, on a clean Ubuntu."""
    monkeypatch.setattr(
        doctor, "_magick_entry", lambda: doctor._entry("magick", "cards", ok=False, why="absent")
    )
    payload = doctor.report()
    assert "magick" not in {r["name"] for r in payload["required"]}
    assert "magick" in {r["name"] for r in payload["optional"]}
    assert payload["ok"] == all(r["ok"] for r in payload["required"])
    assert "– magick" in doctor.render(payload)


# -- the voice, which is a person and not tooling ------------------------


def test_unset_voice_is_an_expected_refusal_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")  # the voice is asked after the device
    monkeypatch.setattr(doctor.tts, "tts_python", lambda: Path("/venv/bin/python"))
    monkeypatch.setattr(doctor.tts, "model_dir", lambda: Path("/models/Qwen3-TTS"))
    row = doctor._tts_entry()
    assert row["ok"] is False
    assert "no default voice on purpose" in row["why"]
    assert "expected refusal" in row["why"]
    assert "PROOFCUT_TTS_VOICE" in row["fix"]


def test_a_configured_voice_never_has_its_path_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A voice is somebody's recorded speech; doctor prints on a shared screen."""
    voice = tmp_path / "private-voice"
    voice.mkdir()
    (voice / "ref.wav").write_bytes(b"")
    (voice / "ref.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setenv("PROOFCUT_TTS_VOICE", str(voice))
    monkeypatch.setattr(sys, "platform", "linux")  # the voice is asked after the device
    monkeypatch.setattr(doctor.tts, "tts_python", lambda: Path("/venv/bin/python"))
    monkeypatch.setattr(doctor.tts, "model_dir", lambda: Path("/models/Qwen3-TTS"))
    row = doctor._tts_entry()
    assert row["ok"] is True
    assert str(voice) not in json.dumps(row)
    assert "not printed" in row["note"]


def test_an_incomplete_voice_names_the_files_and_not_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    voice = tmp_path / "private-voice"
    voice.mkdir()
    (voice / "ref.wav").write_bytes(b"")
    monkeypatch.setenv("PROOFCUT_TTS_VOICE", str(voice))
    monkeypatch.setattr(sys, "platform", "linux")  # the voice is asked after the device
    monkeypatch.setattr(doctor.tts, "tts_python", lambda: Path("/venv/bin/python"))
    monkeypatch.setattr(doctor.tts, "model_dir", lambda: Path("/models/Qwen3-TTS"))
    row = doctor._tts_entry()
    assert row["ok"] is False
    assert "ref.txt" in row["why"]
    assert str(voice) not in json.dumps(row)


def test_missing_synthesiser_is_reported_before_the_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> Path:
        raise tts.TTSError("no interpreter with a voice synthesiser.")

    monkeypatch.setattr(doctor.tts, "tts_python", refuse)
    row = doctor._tts_entry()
    assert row["ok"] is False
    assert "synthesiser" in row["why"]


def test_the_synthesiser_resolves_from_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean Ubuntu's doctor printed `/home/you/lucid-work/voice-clone/…` —
    this box's layout, read back to a stranger. `PROOFCUT_TTS` and
    `PROOFCUT_TTS_MODEL` are the whole search now, `describe.vlm_python`'s rule.
    HISTORY.md § A stranger's install, on a clean Ubuntu."""
    monkeypatch.delenv("PROOFCUT_TTS", raising=False)
    monkeypatch.delenv("PROOFCUT_TTS_MODEL", raising=False)
    with pytest.raises(tts.TTSError) as venv:
        tts.tts_python()
    with pytest.raises(tts.TTSError) as model:
        tts.model_dir()
    row = doctor._tts_entry()
    for text in (str(venv.value), str(model.value), json.dumps(row)):
        assert "lucid-work" not in text


# -- display --------------------------------------------------------------


def test_no_display_names_offscreen_rather_than_just_refusing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unattended box needs no session at all — say which variable to set."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(doctor.picture, "display_env", dict)
    monkeypatch.setattr(doctor.picture, "qt_is_headless", lambda env=None: False)
    display = doctor._display()
    assert display["ok"] is False
    assert "QT_QPA_PLATFORM=offscreen" in display["fix"]
    assert "still exit 0" in display["why"]


def test_a_headless_qt_that_draws_nothing_is_not_a_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ubuntu 24.04's MLT 7.22 ignores QT_QPA_PLATFORM=offscreen, so the ✓ for a
    headless box has to come from what the probe drew. HISTORY.md § A stranger's
    install, on a clean Ubuntu."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(doctor.picture, "display_env", lambda: {"QT_QPA_PLATFORM": "offscreen"})
    monkeypatch.setattr(doctor.picture, "qt_draws", lambda env: False)
    display = doctor._display()
    assert display["ok"] is False
    assert "xvfb-run" in display["fix"]
    # The route that needs no X server at all: Shotcut's portable melt draws
    # headless where both distro MLTs do not. docs/plans/INSTALL.md, measurement 2.
    assert "proofcut setup" in display["fix"]
    assert "Shotcut" in display["fix"]
    monkeypatch.setattr(doctor.picture, "qt_draws", lambda env: True)
    assert doctor._display()["ok"] is True


def test_headless_qt_counts_as_a_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(doctor.picture, "display_env", lambda: {"QT_QPA_PLATFORM": "offscreen"})
    display = doctor._display()
    assert display["ok"] is True
    assert display["headless_qt"] is True
    assert "no display server" in display["how"]


@pytest.mark.parametrize(("platform", "plugin"), [("darwin", "cocoa"), ("win32", "windows")])
def test_a_native_qt_platform_is_not_applicable_rather_than_a_pass_or_a_cross(
    monkeypatch: pytest.MonkeyPatch, platform: str, plugin: str
) -> None:
    """Windows has no `os.getuid`, so doctor raised there before it printed a
    line; and neither OS has a display server to find, so a ✗ would be wrong
    and a ✓ would claim a measurement nobody has made (PORTABILITY.md step 1)."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delattr(os, "getuid", raising=False)
    for name in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "QT_QPA_PLATFORM"):
        monkeypatch.delenv(name, raising=False)

    display = doctor._display()
    assert display["ok"] is None
    assert display["applicable"] is False
    assert plugin in display["note"]

    text = doctor.render(
        {
            "proofcut": "0.0.0",
            "ok": True,
            "required": [],
            "optional": [],
            "display": display,
            "caption_font": {"ok": True, "font": "Outfit", "resolves_to": "Outfit"},
        }
    )
    assert "– not applicable on this platform" in text
    assert "✗ no display" not in text
    assert "Everything required is here." in text


# -- the caption face -----------------------------------------------------


def test_a_substituted_caption_face_carries_the_install_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """libass substitutes and ffmpeg exits 0 — the only symptom is this check."""
    # fontconfig is asked on Linux only; off it, `test_portability` covers
    # the CoreText/DirectWrite branch, and the macOS runner met that one.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        doctor.fonts,
        "probe",
        lambda family, **kw: {"drew": False, "warning": "renders identically to a family that cannot exist"},
    )
    monkeypatch.setattr(
        doctor.captions, "font_match", lambda name, **kw: {"available": False, "resolves_to": "DejaVu Sans"}
    )
    font = doctor._caption_font()
    assert font["ok"] is False
    assert font["resolves_to"] == "DejaVu Sans"
    assert "proofcut fonts --install" in font["fix"]


def test_fontconfig_and_the_render_are_reported_side_by_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two disagree on this box, so neither is folded into the other."""
    monkeypatch.setattr(sys, "platform", "linux")  # fontconfig is Linux's question
    monkeypatch.setattr(doctor.fonts, "probe", lambda family, **kw: {"drew": True})
    monkeypatch.setattr(
        doctor.captions,
        "font_match",
        lambda name, **kw: {"available": False, "resolves_to": "Noto Sans"},
    )
    font = doctor._caption_font()
    assert font["ok"] is True  # the render is what settles it
    assert font["fontconfig_available"] is False
    assert font["resolves_to"] == "Noto Sans"


def _render_font(font: dict[str, Any]) -> str:
    return doctor.render(
        {
            "proofcut": "0.0.0",
            "ok": True,
            "required": [],
            "optional": [],
            "display": {"ok": True, "how": "a Wayland session"},
            "caption_font": font,
        }
    )


def test_no_magick_leaves_the_caption_font_unchecked_never_crossed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neither test kit installs ImageMagick, so every tester's doctor read
    `✗ Outfit` and "captions cannot be burnt" — but the burn is ffmpeg's
    libass alone, and magick only compares the probe's two frames. The probe
    could not ask, which is not an answer about captions. HISTORY.md § The
    whole-film demo on the Windows laptop."""
    monkeypatch.setattr(sys, "platform", "linux")
    # The two burns succeed; the comparison meets a PATH with no magick on it.
    monkeypatch.setattr(doctor.fonts, "_burn_probe", lambda family, out, **kw: {"provider": None, "faces": []})
    monkeypatch.setenv("PATH", str(tmp_path))
    font = doctor._caption_font()
    assert font["ok"] is False
    assert font["unavailable"] is True
    assert "cannot be burnt" not in font["fix"]
    text = _render_font(font)
    assert "– Outfit" in text
    assert "✗ Outfit" not in text
    assert "cannot be burnt" not in text


def test_an_ffmpeg_that_cannot_burn_is_still_a_cross(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half: with no ffmpeg the probe fails at the burn, which is the
    case where captions really cannot be burnt, so it stays a failure."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PATH", str(tmp_path))
    font = doctor._caption_font()
    assert font["ok"] is False
    assert not font.get("unavailable")
    assert "libass" in font["fix"]
    assert "✗ Outfit" in _render_font(font)


# -- the human render, and the CLI ---------------------------------------


def test_render_puts_the_fix_under_every_cross() -> None:
    payload = {
        "proofcut": "0.0.0",
        "ok": False,
        "required": [
            doctor._entry(
                "melt", "layered renders", why="not here", fix="flatpak install org.kde.kdenlive"
            )
        ],
        "optional": [],
        "display": {"ok": True, "how": "a Wayland session"},
        "caption_font": {"ok": True, "font": "Outfit", "resolves_to": "Outfit"},
    }
    text = doctor.render(payload)
    assert "✗ melt" in text
    assert "flatpak install org.kde.kdenlive" in text
    assert "Missing or unusable: melt." in text


def test_cli_doctor_exits_nonzero_when_something_required_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command that always exits 0 cannot be gated on by a setup script."""
    broken = {
        "proofcut": "0.0.0",
        "ok": False,
        "required": [doctor._entry("melt", "layered renders", why="not here", fix="install it")],
        "optional": [],
        "display": {"ok": True, "how": "a Wayland session"},
        "caption_font": {"ok": True, "font": "Outfit", "resolves_to": "Outfit"},
    }
    monkeypatch.setattr(ops, "doctor", lambda: broken)
    assert main(["doctor"]) == 1
    assert "install it" in capsys.readouterr().out


def test_cli_doctor_prints_its_marks_into_a_pipe_that_cannot_encode_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows encodes a piped stdout as cp1252, which has no ✓ or ✗, and
    doctor died with `UnicodeEncodeError` before its first line — in CI's own
    doctor step, and in the paste a tester is asked for. The stream here is
    that pipe, built on Linux: the control against the old `main` raises."""
    broken = {
        "proofcut": "0.0.0",
        "ok": False,
        "required": [doctor._entry("melt", "layered renders", why="not here", fix="install it")],
        "optional": [],
        "display": {"ok": True, "how": "a Wayland session"},
        "caption_font": {"ok": True, "font": "Outfit", "resolves_to": "Outfit"},
    }
    monkeypatch.setattr(ops, "doctor", lambda: broken)
    pipe = io.BytesIO()
    stream = io.TextIOWrapper(pipe, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    assert main(["doctor"]) == 1
    stream.flush()
    assert "✗ melt" in pipe.getvalue().decode("utf-8")


def test_cli_leaves_a_stream_that_can_already_encode_the_marks_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the stream that would crash is touched — a UTF-16 console, or
    anything a caller configured on purpose, keeps its encoding."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-16")
    monkeypatch.setattr(sys, "stdout", stream)
    fine = {
        "proofcut": "0.0.0",
        "ok": True,
        "required": [],
        "optional": [],
        "display": {"ok": True, "how": "a Wayland session"},
        "caption_font": {"ok": True, "font": "Outfit", "resolves_to": "Outfit"},
    }
    monkeypatch.setattr(ops, "doctor", lambda: fine)
    main(["doctor", "--json"])
    assert stream.encoding == "utf-16"


def test_cli_doctor_json_is_the_same_dict_the_tool_returns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fine = {
        "proofcut": "0.0.0",
        "ok": True,
        "required": [doctor._entry("melt", "layered renders", ok=True)],
        "optional": [],
        "display": {"ok": True, "how": "a Wayland session"},
        "caption_font": {"ok": True, "font": "Outfit", "resolves_to": "Outfit"},
    }
    monkeypatch.setattr(ops, "doctor", lambda: fine)
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["required"][0]["name"] == "melt"


# -- the agent panel -------------------------------------------------------


def test_an_absent_claude_is_unavailable_and_never_moves_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent pane is one client's feature: missing is `–`, not a failure."""
    monkeypatch.setenv("PROOFCUT_AGENT_BIN", "proofcut-test-no-such-claude")
    payload = doctor.report()
    agent = payload["agent"]
    assert agent["ok"] is False
    assert "proofcut-test-no-such-claude" in agent["why"]
    assert "PROOFCUT_AGENT_BIN" in agent["fix"]
    assert payload["ok"] == all(r["ok"] for r in payload["required"])
    text = doctor.render(payload)
    assert "– claude" in text
    assert "✗ claude" not in text


def test_a_claude_is_run_for_its_version_rather_than_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Found-but-silent is not a pass — the rule every other probe holds to."""
    fake = write_stub(tmp_path / "claude", "print('9.9.9 (Claude Code)')\n")
    monkeypatch.setenv("PROOFCUT_AGENT_BIN", str(fake))
    agent = doctor._agent()
    assert agent["ok"] is True
    assert agent["version"] == "9.9.9 (Claude Code)"

    assert write_stub(tmp_path / "claude", "raise SystemExit(3)\n") == fake
    agent = doctor._agent()
    assert agent["ok"] is False
    assert agent["found"] == str(fake)
    assert "exited 3" in agent["why"]


# -- the old name's variables ----------------------------------------------


def _clear_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A box still carrying a `60-lucid.conf` sets some of them (this one did
    until RENAME.md step 5), so start from none."""
    for name in list(os.environ):
        if name.startswith("LUCID_"):
            monkeypatch.delenv(name)


def _healthy_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every other section answers clean, so the only thing moving is the variable."""
    for probe in ("_whisper_entry", "_auto_editor_entry", "_melt_entry"):
        monkeypatch.setattr(doctor, probe, lambda probe=probe: doctor._entry(probe, "stub", ok=True))
    monkeypatch.setattr(doctor, "_ffmpeg_entry", lambda binary, what: doctor._entry(binary, what, ok=True))
    for probe in ("_magick_entry", "_vlm_entry", "_face_entry", "_tts_entry"):
        monkeypatch.setattr(doctor, probe, lambda probe=probe: doctor._entry(probe, "stub", ok=False))
    monkeypatch.setattr(doctor, "_display", lambda: {"ok": True, "how": "a Wayland session"})
    monkeypatch.setattr(
        doctor, "_caption_font", lambda: {"ok": True, "font": "Outfit", "resolves_to": "Outfit"}
    )
    monkeypatch.setattr(doctor, "_agent", lambda: {"ok": True, "found": "/bin/claude", "version": "9.9.9"})


def test_no_old_name_variable_set_reads_as_the_all_clear(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty list has to say so — a quiet section reads like one that never ran."""
    _clear_legacy_env(monkeypatch)
    _healthy_box(monkeypatch)
    payload = doctor.report()
    assert payload["legacy_env"] == {"ok": True, "stale": [], "note": None, "fix": None}
    assert payload["ok"] is True
    assert main(["doctor"]) == 0
    assert "✓ none set" in capsys.readouterr().out


def test_a_stale_lucid_face_is_named_beside_its_new_name_and_never_moves_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """RENAME.md decision 3: without this a stranger's `60-lucid.conf` loses face
    detection at exit 0. Nothing reads the old name, so doctor is where it shows —
    as a note, never a ✗, on a box whose install is otherwise sound."""
    _clear_legacy_env(monkeypatch)
    _healthy_box(monkeypatch)
    monkeypatch.delenv("PROOFCUT_FACE", raising=False)
    monkeypatch.setenv("LUCID_FACE", "/sentinel/face-venv/bin/python")

    payload = doctor.report()
    assert payload["legacy_env"]["stale"] == [
        {"name": "LUCID_FACE", "rename_to": "PROOFCUT_FACE", "rename_to_set": False}
    ]
    assert payload["ok"] is True
    assert main(["doctor"]) == 0
    text = capsys.readouterr().out
    assert "– LUCID_FACE — now PROOFCUT_FACE" in text
    assert "✗ LUCID_FACE" not in text
    assert "Everything required is here." in text
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_an_old_name_variables_value_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`LUCID_TTS_VOICE` is somebody's recorded speech — the voice rule, applied
    to the old name too. The name is the whole answer a rename needs."""
    _clear_legacy_env(monkeypatch)
    _healthy_box(monkeypatch)
    voice = "/sentinel/private-voice-7f3a"
    face = "/sentinel/face-python-9c1e"
    monkeypatch.setenv("LUCID_TTS_VOICE", voice)
    monkeypatch.setenv("LUCID_FACE", face)
    monkeypatch.setenv("PROOFCUT_FACE", "/sentinel/new-face-python-2b8d")

    legacy = doctor.report()["legacy_env"]
    assert [v["name"] for v in legacy["stale"]] == ["LUCID_FACE", "LUCID_TTS_VOICE"]
    assert {v["name"]: v["rename_to_set"] for v in legacy["stale"]} == {
        "LUCID_FACE": True,
        "LUCID_TTS_VOICE": False,
    }

    main(["doctor"])
    text = capsys.readouterr().out
    assert "LUCID_FACE — now PROOFCUT_FACE (already set)" in text
    assert "LUCID_TTS_VOICE — now PROOFCUT_TTS_VOICE" in text
    main(["doctor", "--json"])
    raw = capsys.readouterr().out
    for output in (text, raw):
        assert "/sentinel/" not in output


# -- long paths ---------------------------------------------------------------


def test_long_paths_off_is_a_note_with_the_limit_and_never_moves_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stranger should learn the folder limit from doctor, before `init`
    refuses — and a stock Windows is not a broken install."""
    from proofcut import project

    _clear_legacy_env(monkeypatch)
    _healthy_box(monkeypatch)
    monkeypatch.setattr(project, "windows_long_paths", lambda: False)

    payload = doctor.report()
    assert payload["long_paths"]["enabled"] is False
    assert payload["long_paths"]["max_root"] == project.WINDOWS_DIR_LIMIT - project.PATH_HEADROOM
    assert payload["ok"] is True
    assert main(["doctor"]) == 0
    text = capsys.readouterr().out
    assert f"– off — a project folder can be at most {payload['long_paths']['max_root']} characters" in text
    assert "LongPathsEnabled 1" in text


def test_long_paths_on_says_so_and_off_windows_the_section_is_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from proofcut import project

    _clear_legacy_env(monkeypatch)
    _healthy_box(monkeypatch)
    monkeypatch.setattr(project, "windows_long_paths", lambda: True)
    assert doctor.report()["long_paths"] == {"enabled": True, "max_root": None, "note": None, "fix": None}
    main(["doctor"])
    assert "✓ enabled" in capsys.readouterr().out

    monkeypatch.setattr(project, "windows_long_paths", lambda: None)
    assert doctor.report()["long_paths"] is None
    main(["doctor"])
    assert "Long paths" not in capsys.readouterr().out
