"""The Linux-shaped resolvers, widened, and the GPU workers — PORTABILITY.md steps 2 and 3.

Each resolver here found only what a Linux box has: melt in the Kdenlive
flatpak, auto-editor's Linux build, a chromium on PATH, `tailscale` on PATH,
fonts where fontconfig reads them. None of them crashed off Linux; each one
silently narrowed what a Mac or a Windows box could find. These tests stand
in for either OS by setting `sys.platform`, and every one of them also pins
what Linux still gets, since that is the only platform any of it has run on.

**None of this is a measurement of macOS or Windows.** The install locations
are leads the plan names; whether a Shotcut melt carries `qtblend`, or a
registered face draws under DirectWrite, is steps 4 and 5, on real machines.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path, PureWindowsPath
from typing import Self

import pytest
from stubs import write_stub

from proofcut import (
    autoeditor,
    describe,
    doctor,
    fonts,
    graphics,
    media,
    picture,
    timeline,
    tts,
    webui,
)

_PROBE = media.MediaInfo(
    duration=5.0,
    has_video=True,
    has_audio=True,
    fps=30.0,
    width=1920,
    height=1080,
    sample_rate=48000,
    channels=2,
    video_codec="h264",
    audio_codec="aac",
    vfr=False,
)

# -- melt ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "bundle"),
    [("darwin", "Shotcut.app/Contents/MacOS/melt"), ("win32", "melt.exe")],
)
def test_melt_is_found_in_an_editor_bundle_off_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, bundle: str
) -> None:
    """Shotcut and Kdenlive ship melt inside the app on both OSes and put
    neither on PATH, so PATH-then-flatpak found nothing on a Mac."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("PROOFCUT_MELT", raising=False)
    monkeypatch.setattr(picture.shutil, "which", lambda name: None)
    assert any(p.as_posix().endswith(bundle) for p in picture.melt_bundles())

    installed = tmp_path / "melt"
    installed.touch()
    monkeypatch.setattr(picture, "melt_version", lambda path: "7.40.0")  # an empty file has no banner
    monkeypatch.setattr(picture, "melt_bundles", lambda: [tmp_path / "absent", installed])
    assert picture.melt_command() == [str(installed)]


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_melt_not_found_off_linux_names_the_editors_not_the_flatpak(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """Doctor told a Mac to `flatpak install` — a package manager it has not got."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("PROOFCUT_MELT", raising=False)
    monkeypatch.setattr(picture.shutil, "which", lambda name: None)
    monkeypatch.setattr(picture, "melt_bundles", list)

    with pytest.raises(picture.PictureError) as refused:
        picture.melt_command()
    assert "Shotcut" in str(refused.value)
    assert "flatpak" not in str(refused.value)

    row = doctor._melt_entry()
    assert "Shotcut" in row["fix"] and "Shotcut" in row["looked_for"]
    assert "flatpak" not in row["fix"]
    assert "single-source cuts still render through auto-editor" in row["fix"]


def test_linux_still_looks_at_the_flatpak_and_no_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert picture.melt_bundles() == []
    where, install = picture.melt_search()
    assert picture.KDENLIVE_FLATPAK in where
    assert "flatpak install org.kde.kdenlive" in install


def test_linux_melt_advice_leads_with_the_distribution_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean Ubuntu 24.04 told only about the flatpak had a working `apt install
    melt` one line away. HISTORY.md § A stranger's install, on a clean Ubuntu."""
    monkeypatch.setattr(sys, "platform", "linux")
    _, install = picture.melt_search()
    assert "apt install melt" in install
    assert install.index("apt install melt") < install.index("flatpak install")
    assert "dnf install mlt" in install


def test_fedoras_mlt_is_found_ahead_of_its_freeze_melt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fedora's `mlt` installs `mlt-melt` and `melt-7` and no `melt`, and its
    `melt` package is freeze, a compression tool. With both installed, a bare
    `melt` searched first rendered through freeze. HISTORY.md § A stranger's
    install, on a clean Fedora."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("PROOFCUT_MELT", raising=False)
    fedora = {"melt": "/usr/bin/melt", "melt-7": "/usr/bin/melt-7", "mlt-melt": "/usr/bin/mlt-melt"}
    monkeypatch.setattr(picture.shutil, "which", fedora.get)
    monkeypatch.setattr(picture, "melt_version", lambda path: "7.40.0")  # the paths are not on this box
    assert picture.melt_command() == ["/usr/bin/mlt-melt"]

    only_mlt = {"melt-7": "/usr/bin/melt-7"}
    monkeypatch.setattr(picture.shutil, "which", only_mlt.get)
    assert picture.melt_command() == ["/usr/bin/melt-7"]


# -- a melt on PATH that is not MLT: PORTABILITY.md step 5a -----------------

_WIX_MELT = (
    "import sys\n"
    "print('Windows Installer XML Toolset MSI/MSM Decompiler version 3.14.1.8722')\n"
    "print(\"melt.exe : error MELT0240 : The file '.mlt' has an unexpected extension\")\n"
    "sys.exit(0)\n"
)
_REAL_MELT = "print('melt 7.41.0')\nprint('Copyright (C) 2002-2026 Meltytech, LLC')\n"


def _melt_on_path(monkeypatch: pytest.MonkeyPatch, *dirs: Path) -> None:
    """PATH is exactly `dirs`, no bundle and no flatpak, and nothing cached."""
    monkeypatch.delenv("PROOFCUT_MELT", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))
    monkeypatch.setattr(picture, "melt_bundles", list)
    monkeypatch.setattr(picture, "_MELT_VERDICTS", {}, raising=False)


def test_an_impostor_melt_on_path_is_skipped_for_the_real_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Windows runner's first PATH `melt` was WiX's MSI tool, and six render
    tests ran it (HISTORY.md § The Windows run that answered). It exits 0, so
    only the banner tells it apart."""
    wix, mlt = tmp_path / "wix", tmp_path / "mlt"
    wix.mkdir()
    mlt.mkdir()
    write_stub(wix / "mlt-melt", _WIX_MELT)
    real = write_stub(mlt / "melt", _REAL_MELT)
    _melt_on_path(monkeypatch, wix, mlt)

    assert picture.melt_command() == [shutil.which("melt")]
    assert Path(picture.melt_command()[0]).resolve() == real.resolve()


def test_an_impostor_melt_ahead_of_a_bundle_gives_way_to_the_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_stub(tmp_path / "melt", _WIX_MELT)
    shotcut = tmp_path / "Shotcut"
    shotcut.mkdir()
    real = write_stub(shotcut / "melt", _REAL_MELT)
    _melt_on_path(monkeypatch, tmp_path)
    monkeypatch.setattr(picture, "melt_bundles", lambda: [real])

    assert picture.melt_command() == [str(real)]


def test_only_impostors_refuses_and_names_each_by_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refusal that said only "not found" would send someone to install a melt
    while the one on PATH kept answering to the name."""
    wix = write_stub(tmp_path / "melt", _WIX_MELT)
    _melt_on_path(monkeypatch, tmp_path)

    with pytest.raises(picture.PictureError) as refused:
        picture.melt_command()
    found = shutil.which("melt")
    assert found is not None and Path(found).resolve() == wix.resolve()
    assert f"Skipped {found}" in str(refused.value)
    assert "not MLT's melt" in str(refused.value)

    row = doctor._melt_entry()
    assert row["ok"] is False
    assert found in row["why"]


def test_a_melt_verdict_is_cached_until_the_binary_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`melt_command` runs several times per render; one process start per
    binary, and a binary replaced in place is asked again."""
    runs = tmp_path / "runs"
    counting = f"open({str(runs)!r}, 'a').write('x')\n" + _REAL_MELT
    stub = write_stub(tmp_path / "melt", counting)
    _melt_on_path(monkeypatch, tmp_path)

    for _ in range(3):
        picture.melt_command()
    assert runs.read_text() == "x"

    target = Path(shutil.which("melt") or stub)
    stamp = target.stat().st_mtime_ns + 5_000_000_000
    os.utime(target, ns=(stamp, stamp))
    picture.melt_command()
    assert runs.read_text() == "xx"


def test_proofcut_melt_is_taken_at_its_word_and_never_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    """It may be a wrapper — the flatpak form is four words and no binary."""
    monkeypatch.setenv("PROOFCUT_MELT", "flatpak run --command=melt org.kde.kdenlive")
    monkeypatch.setattr(picture, "melt_version", lambda path: pytest.fail(f"probed {path}"), raising=False)
    assert picture.melt_command()[0] == "flatpak"


@pytest.mark.parametrize("banner", ["melt.exe 7.41.0", "melt.EXE 7.41.0", "mlt-melt.exe 7.41.0"])
def test_a_windows_melt_names_itself_with_its_extension(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, banner: str
) -> None:
    """melt prints `basename(argv[0])`, and on Windows that keeps `.exe`: Shotcut
    26.8.1's `melt.exe` exited 0 on the first windows-demo run and doctor called
    it no melt at all."""
    shotcut = write_stub(tmp_path / "melt", f"print({banner!r})\n")
    _melt_on_path(monkeypatch, tmp_path)
    monkeypatch.setattr(picture, "melt_bundles", lambda: [shotcut])
    monkeypatch.setenv("PATH", "")
    assert picture.melt_command() == [str(shotcut)]

    monkeypatch.setattr(doctor.picture, "melt_command", lambda: [r"C:\Shotcut\melt.exe"])
    monkeypatch.setattr(doctor, "_run", lambda cmd: (f"{banner}\nCopyright (C) 2002-2026 Meltytech, LLC\n", "", 0))
    row = doctor._melt_entry()
    assert row["ok"] is True
    assert row["version"] == "7.41.0"


def test_doctor_reads_melts_version_off_the_one_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    """One copy of the banner rule, in `picture`, for doctor and the resolver."""
    assert not hasattr(doctor, "_MELT_BANNER")
    assert picture.MELT_BANNER.match("melt-7 7.40.0").group(1) == "7.40.0"
    assert picture.MELT_BANNER.match("Windows Installer XML Toolset MSI/MSM Decompiler") is None


# -- PROOFCUT_MELT and PROOFCUT_MAGICK naming a Windows path ------------------------


@pytest.mark.parametrize(
    ("value", "argv"),
    [
        (r"C:\Users\runner\melt.exe", [r"C:\Users\runner\melt.exe"]),
        (
            r'"C:\Program Files\Shotcut\melt.exe" -verbose',
            [r"C:\Program Files\Shotcut\melt.exe", "-verbose"],
        ),
        ("flatpak run --command=melt org.kde.kdenlive", ["flatpak", "run", "--command=melt", "org.kde.kdenlive"]),
    ],
)
@pytest.mark.parametrize(("variable", "resolve"), [("PROOFCUT_MELT", "melt"), ("PROOFCUT_MAGICK", "magick")])
def test_a_windows_override_keeps_its_backslashes(
    monkeypatch: pytest.MonkeyPatch, value: str, argv: list[str], variable: str, resolve: str
) -> None:
    """POSIX `shlex.split` turned `C:\\Users\\runner\\melt.exe` into
    `C:Usersrunnermelt.exe`, so every doctor fix line telling a Windows user to
    set one of these was advice that could not work."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(variable, value)
    command = picture.melt_command() if resolve == "melt" else graphics.magick_command()
    assert command == argv


@pytest.mark.parametrize("variable", ["PROOFCUT_MELT", "PROOFCUT_MAGICK"])
def test_a_windows_override_naming_a_file_with_a_space_is_one_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str
) -> None:
    """Unquoted, `C:\\Program Files\\Shotcut\\melt.exe` is two words to any
    splitter — so a value that names a file that exists is taken whole."""
    folder = tmp_path / "Program Files"
    folder.mkdir()
    binary = folder / "melt.exe"
    binary.touch()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(variable, str(binary))
    command = picture.melt_command() if variable == "PROOFCUT_MELT" else graphics.magick_command()
    assert command == [str(binary)]


def test_linux_overrides_still_split_as_a_shell_would(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert picture.command_override("flatpak run '--command=melt' org.kde.kdenlive") == [
        "flatpak",
        "run",
        "--command=melt",
        "org.kde.kdenlive",
    ]


@pytest.mark.parametrize(
    ("platform", "note"),
    [("darwin", "Linux-only"), ("win32", "Linux-only"), ("linux", "systemd-run is not available")],
)
def test_the_uncapped_render_note_says_why_for_the_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, note: str
) -> None:
    """The memory cap is a systemd scope. "Not available here" on a Mac reads
    as something to install; it is something that does not exist there."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("PROOFCUT_MELT", "melt")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    # Named, so standing in for Linux on a Windows runner never reaches for
    # `os.getuid` to build `/run/user/<uid>` — Windows has none.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(picture.shutil, "which", lambda name: None)

    def fake_run(command: list[str], **kwargs: object) -> object:
        target = next(a for a in command if a.startswith("avformat:")).removeprefix("avformat:")
        Path(target).write_bytes(b"a render")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(picture.subprocess, "run", fake_run)
    monkeypatch.setattr(picture.media, "probe", lambda p: _PROBE)
    monkeypatch.setattr(
        picture.media,
        "count_frames",
        lambda p: {"frames": 150, "container_frames": 150, "duration": 5.0, "has_video": True},
    )
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    result = picture.render(project, tmp_path / "out.mp4", expect_frames=150)
    assert result["memory_cap"] is None
    assert any(note in n for n in result["notes"])


@pytest.mark.parametrize("bus", [False, True])
def test_the_memory_cap_needs_a_user_bus_not_just_systemd_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bus: bool
) -> None:
    """A clean Ubuntu container has `systemd-run` on PATH and no user session
    bus, so every capped render died with "Failed to connect to bus" before melt
    started, and was reported as "melt rendered nothing". On PATH is not the
    same as usable. HISTORY.md § A stranger's install, on a clean Ubuntu."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PROOFCUT_MELT", "melt")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setattr(picture, "qt_draws", lambda env: True)
    runtime = tmp_path / "run"
    runtime.mkdir()
    if bus:
        (runtime / "bus").write_bytes(b"")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(picture.shutil, "which", lambda name: f"/usr/bin/{name}")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        target = next(a for a in command if a.startswith("avformat:")).removeprefix("avformat:")
        Path(target).write_bytes(b"a render")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(picture.subprocess, "run", fake_run)
    monkeypatch.setattr(picture.media, "probe", lambda p: _PROBE)
    monkeypatch.setattr(
        picture.media,
        "count_frames",
        lambda p: {"frames": 150, "container_frames": 150, "duration": 5.0, "has_video": True},
    )
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    result = picture.render(project, tmp_path / "out.mp4", expect_frames=150)
    assert (commands[0][0] == "systemd-run") is bus
    assert (result["memory_cap"] is not None) is bus
    if not bus:
        assert any("user session bus" in n for n in result["notes"])


def test_a_capped_render_hands_systemd_run_the_runtime_dir_it_found_the_bus_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The MCP SDK's stdio client passes no `XDG_RUNTIME_DIR`, and with no
    desktop nothing else exports it. `user_bus` finds `/run/user/<uid>/bus`
    anyway, but `systemd-run` never looks there on its own ("$XDG_RUNTIME_DIR
    not defined"), so every headless render from a stdio server died before
    melt started, reported as "melt rendered nothing". Measured 2026-09-17."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PROOFCUT_MELT", "melt")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(picture, "qt_draws", lambda env: True)
    monkeypatch.setattr(os, "getuid", lambda: 4242, raising=False)
    monkeypatch.setattr(picture, "USER_RUNTIME_ROOT", tmp_path / "run-user")
    runtime = tmp_path / "run-user" / "4242"
    runtime.mkdir(parents=True)
    (runtime / "bus").write_bytes(b"")
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(picture.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs["env"]))  # type: ignore[arg-type]
        target = next(a for a in command if a.startswith("avformat:")).removeprefix("avformat:")
        Path(target).write_bytes(b"a render")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(picture.subprocess, "run", fake_run)
    monkeypatch.setattr(picture.media, "probe", lambda p: _PROBE)
    monkeypatch.setattr(
        picture.media,
        "count_frames",
        lambda p: {"frames": 150, "container_frames": 150, "duration": 5.0, "has_video": True},
    )
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")

    picture.render(project, tmp_path / "out.mp4", expect_frames=150)
    command, env = calls[0]
    assert command[0] == "systemd-run"
    assert env.get("XDG_RUNTIME_DIR") == str(runtime)


# -- auto-editor -------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "machine", "asset"),
    [
        # Every name on the right is on the 31.6.0 release's asset list, read
        # off GitHub 2026-09-10 — the plan's own guess had Windows wrong.
        ("linux", "x86_64", "auto-editor-linux-x86_64"),
        ("linux", "aarch64", "auto-editor-linux-aarch64"),
        ("linux", "armv7l", "auto-editor-linux-armv7"),
        ("darwin", "arm64", "auto-editor-macos-arm64"),
        ("darwin", "x86_64", "auto-editor-macos-x86_64"),
        ("win32", "AMD64", "auto-editor-windows-x86_64.exe"),
        ("win32", "ARM64", "auto-editor-windows-aarch64.exe"),
    ],
)
def test_the_not_found_message_names_this_machines_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, machine: str, asset: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(autoeditor.platform, "machine", lambda: machine)
    assert autoeditor.release_asset() == asset

    monkeypatch.delenv("PROOFCUT_AUTO_EDITOR", raising=False)
    monkeypatch.setattr(autoeditor.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.local/bin/auto-editor
    with pytest.raises(autoeditor.AutoEditorError, match=asset.replace(".", r"\.")):
        autoeditor.binary()
    assert asset in doctor._auto_editor_entry()["fix"]


def test_an_os_upstream_does_not_build_for_gets_no_invented_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "freebsd14")
    monkeypatch.setattr(autoeditor.platform, "machine", lambda: "amd64")
    assert not autoeditor.release_asset().startswith("auto-editor-")


# -- tailscale ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "tail"),
    [("darwin", "Tailscale.app/Contents/MacOS/Tailscale"), ("win32", "tailscale.exe")],
)
def test_tailscale_is_found_where_the_app_installs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, tail: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv(webui.PROOFCUT_TAILSCALE_ENV, raising=False)
    monkeypatch.setattr(webui.shutil, "which", lambda name: None)
    assert any(p.as_posix().endswith(tail) for p in webui._tailscale_installs())

    installed = tmp_path / "tailscale"
    installed.touch()
    monkeypatch.setattr(webui, "_tailscale_installs", lambda: [installed])
    assert webui._find_tailscale() == str(installed)


def test_tailscale_still_refuses_when_nothing_is_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--tailscale` refuses rather than falling back to loopback — unchanged."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv(webui.PROOFCUT_TAILSCALE_ENV, raising=False)
    monkeypatch.setattr(webui.shutil, "which", lambda name: None)
    monkeypatch.setattr(webui, "_tailscale_installs", lambda: [Path("/nowhere/Tailscale")])
    with pytest.raises(webui.ProjectError, match=re.escape(str(Path("/nowhere/Tailscale")))):
        webui.tailscale_identity()


# -- `proofcut open`'s browser --------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "tail"),
    [
        ("darwin", "Google Chrome.app/Contents/MacOS/Google Chrome"),
        ("win32", "msedge.exe"),
    ],
)
def test_a_chromium_is_found_in_its_install_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, tail: str
) -> None:
    """Neither OS puts a browser on PATH, so the chromeless `--app=` window
    was never offered there."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert any(str(p).replace("\\", "/").endswith(tail) for p in webui._app_browser_installs())

    monkeypatch.delenv(webui.PROOFCUT_BROWSER_ENV, raising=False)
    monkeypatch.setattr(webui.shutil, "which", lambda name: None)
    installed = tmp_path / "browser"
    installed.touch()
    monkeypatch.setattr(webui, "_app_browser_installs", lambda: [tmp_path / "absent", installed])
    assert webui._resolve_app_browser() == [str(installed)]


def test_chrome_outranks_edge_the_way_it_does_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Windows box has Edge; one with Chrome too should open Chrome."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", "PF")
    names = [p.name for p in webui._app_browser_installs()]
    assert names.index("chrome.exe") < names.index("msedge.exe")


def _no_app_browser(monkeypatch: pytest.MonkeyPatch, platform: str) -> list[str]:
    opened: list[str] = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(webui, "_resolve_app_browser", lambda: None)
    monkeypatch.setattr(webui.shutil, "which", lambda name: None)
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    return opened


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_with_no_chromium_the_url_opens_in_a_normal_tab(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    opened = _no_app_browser(monkeypatch, platform)
    webui._launch_app("http://127.0.0.1:1/")
    assert opened == ["http://127.0.0.1:1/"]


def test_linux_with_no_xdg_open_still_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`webbrowser` on a Linux box with no `xdg-open` can answer with w3m or
    lynx, which would seize the terminal `proofcut open` is serving from."""
    opened = _no_app_browser(monkeypatch, "linux")
    webui._launch_app("http://127.0.0.1:1/")
    assert opened == []


# -- fonts -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "tail"),
    [("darwin", "Library/Fonts"), ("win32", "Microsoft/Windows/Fonts")],
)
def test_the_user_font_dir_is_the_os_s_own(
    monkeypatch: pytest.MonkeyPatch, platform: str, tail: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("XDG_DATA_HOME", "/somewhere/else")  # ignored off Linux
    monkeypatch.setenv("LOCALAPPDATA", "/appdata/local")
    assert str(fonts.user_font_dir()).replace("\\", "/").endswith(tail)


def test_a_mac_install_asks_fontconfig_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fontconfig is not the font system there: neither `fc-cache` nor
    `fc-list` is run, and "on its path" is null rather than a False that
    sends someone reinstalling."""
    monkeypatch.setattr(sys, "platform", "darwin")

    def no_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"fontconfig asked on a Mac: {args}")

    monkeypatch.setattr(fonts.subprocess, "run", no_subprocess)
    report = fonts.install(dest=tmp_path)
    assert report["font_system"] == "CoreText"
    assert report["on_fontconfig_path"] is None
    assert report["cache_refreshed"] is None
    assert report["registered"] is None
    assert report["changed"] is True


class _FakeWinreg(types.ModuleType):
    """`winreg`'s surface, over a dict — Linux has no registry to write."""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self, *, drop: bool = False) -> None:
        super().__init__("winreg")
        self.values: dict[str, str] = {}
        self.drop = drop  # a write that does not read back

    def CreateKey(self, root: str, path: str) -> _FakeWinreg:
        assert (root, path) == ("HKCU", fonts._WINDOWS_FONTS_KEY)
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def SetValueEx(self, key: object, name: str, reserved: int, kind: int, data: str) -> None:
        if not self.drop:
            self.values[name] = data

    def QueryValueEx(self, key: object, name: str) -> tuple[str, int]:
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ


def test_a_windows_install_registers_each_face_and_reads_it_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A face copied into the per-user directory with no HKCU value is not
    installed — the copy alone would be reported as an install that is not."""
    registry = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(fonts.subprocess, "run", lambda *a, **k: pytest.fail("fontconfig asked"))

    report = fonts.install(dest=tmp_path)

    assert report["font_system"] == "DirectWrite"
    assert report["registered"] is True
    face = fonts.vendored()[0]
    assert registry.values == {f"{face.stem} (TrueType)": str(tmp_path / face.name)}

    # Unchanged on a second run, and still registered: a face copied by an
    # older lucid (or by hand) gets its value on the next install.
    registry.values.clear()
    again = fonts.install(dest=tmp_path)
    assert again["changed"] is False
    assert again["registered"] is True


def test_a_registration_that_does_not_read_back_is_reported_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(drop=True))
    monkeypatch.setattr(sys, "platform", "win32")
    assert fonts.install(dest=tmp_path)["registered"] is False


class _FakeWinDLL:
    """`gdi32`/`user32` over a list of calls — Linux has no GDI to load into."""

    def __init__(self, calls: list[tuple[str, tuple[object, ...]]], added: int) -> None:
        self.calls, self.added = calls, added

    def __call__(self, name: str, use_last_error: bool = False) -> _FakeWinDLL:
        self.calls.append(("dll", (name,)))
        return self

    def _record(self, function: str, result: int) -> types.FunctionType:
        def call(*args: object) -> int:
            self.calls.append((function, args))
            return result

        return call  # type: ignore[return-value]

    def __getattr__(self, function: str) -> object:
        call = self._record(function, self.added if function == "AddFontResourceW" else 1)
        setattr(self, function, call)
        return call


def test_a_windows_install_loads_each_face_into_gdi_and_says_so_beside_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outfit was `registered: true` and libass drew ArialMT: GDI reads a
    per-user font's registry value at logon only (HISTORY.md § The Windows run
    that answered). Install has to load it the way Windows' own Install does."""
    import ctypes

    calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg())
    monkeypatch.setattr(ctypes, "WinDLL", _FakeWinDLL(calls, added=1), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    report = fonts.install(dest=tmp_path)

    faces = fonts.vendored()
    assert report["registered"] is True
    assert report["loaded"] == len(faces)
    loads = [args[0] for name, args in calls if name == "AddFontResourceW"]
    assert loads == [str(tmp_path / face.name) for face in faces]
    broadcasts = [args for name, args in calls if name == "SendMessageTimeoutW"]
    assert len(broadcasts) == 1
    hwnd, message, _, _, flags, _, _ = broadcasts[0]
    assert (hwnd, message) == (fonts._HWND_BROADCAST, fonts._WM_FONTCHANGE)
    assert flags & fonts._SMTO_ABORTIFHUNG
    assert calls.index(("dll", ("gdi32",))) < calls.index(("SendMessageTimeoutW", broadcasts[0]))


def test_a_face_gdi_would_not_load_is_counted_out_and_not_folded_into_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import ctypes

    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg())
    monkeypatch.setattr(ctypes, "WinDLL", _FakeWinDLL([], added=0), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    report = fonts.install(dest=tmp_path)
    assert report["registered"] is True
    assert report["loaded"] == 0


def test_no_gdi_load_is_attempted_off_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(fonts.subprocess, "run", lambda *a, **k: None)
    assert fonts.install(dest=tmp_path)["loaded"] is None


# -- libass's own font directory, on Windows only ---------------------------


def _legacy_family(ttf: Path) -> str:
    """Name ID 1, read straight off the sfnt `name` table — what libass calls a
    face it loads from its font directory. No fontTools in the test deps."""
    import struct

    data = ttf.read_bytes()
    tables = struct.unpack(">H", data[4:6])[0]
    for i in range(tables):
        tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i : 28 + 16 * i])
        if tag != b"name":
            continue
        _, count, strings = struct.unpack(">HHH", data[offset : offset + 6])
        for j in range(count):
            platform, _, _, name_id, length, at = struct.unpack(
                ">HHHHHH", data[offset + 6 + 12 * j : offset + 18 + 12 * j]
            )
            if name_id == 1 and platform == 3:
                raw = data[offset + strings + at : offset + strings + at + length]
                return raw.decode("utf-16-be")
    raise AssertionError(f"no Windows-platform name ID 1 in {ttf}")


def test_the_static_outfit_is_named_outfit_where_the_variable_one_is_not() -> None:
    """libass names a face in its font directory by name ID 1. The variable
    file's default instance is Thin, so staging it answers a request for
    `Outfit` with a substitute — the first fontsdir build, measured before it
    shipped. HISTORY.md § The first windows-demo run."""
    assert _legacy_family(fonts.VENDORED_DIR / "Outfit[wght].ttf") == "Outfit Thin"
    statics = sorted(p.name for p in fonts.vendored(fonts.STATIC_DIR))
    assert statics == ["Outfit-Bold.ttf", "Outfit-Regular.ttf"]
    for face in fonts.vendored(fonts.STATIC_DIR):
        assert _legacy_family(face) == "Outfit"
    # Never installed where fontconfig looks: the Linux burn keeps the variable face.
    assert not any(p.parent == fonts.STATIC_DIR for p in fonts.vendored())


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_no_fontsdir_off_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str) -> None:
    """Linux and macOS burns resolve through their own font system as measured."""
    monkeypatch.setattr(sys, "platform", platform)
    assert fonts.libass_fontsdir(tmp_path) == ""
    assert list(tmp_path.iterdir()) == []


def test_a_windows_burn_stages_the_static_faces_and_the_installed_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    user = tmp_path / "user-fonts"
    user.mkdir()
    (user / "ZillaSlab-Regular.ttf").write_bytes(b"a pack's face")
    cwd = tmp_path / "burn"
    cwd.mkdir()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(fonts, "user_font_dir", lambda: user)

    assert fonts.libass_fontsdir(cwd) == ":fontsdir=fonts"
    staged = sorted(p.name for p in (cwd / "fonts").iterdir())
    assert staged == ["Outfit-Bold.ttf", "Outfit-Regular.ttf", "ZillaSlab-Regular.ttf"]


@pytest.mark.parametrize(("platform", "option"), [("win32", ":fontsdir=fonts"), ("linux", "")])
def test_the_caption_burn_names_the_font_directory_only_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, option: str
) -> None:
    from proofcut import captions

    video = tmp_path / "in.mp4"
    video.write_bytes(b"")
    subs = tmp_path / "in.ass"
    subs.write_text("[Script Info]\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], cwd: str, **kwargs: object) -> None:
        seen["filter"] = cmd[cmd.index("-vf") + 1]
        seen["staged"] = sorted(p.name for p in (Path(cwd) / "fonts").glob("*.ttf"))

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(fonts, "user_font_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(captions.subprocess, "run", fake_run)
    captions.burn(video, subs, tmp_path / "out.mp4")

    assert seen["filter"] == f"ass=proofcut.ass{option}"
    assert seen["staged"] == (["Outfit-Bold.ttf", "Outfit-Regular.ttf"] if option else [])


def _ffmpeg_has_libass() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
    return re.search(r"^\s*\S+\s+ass\s", listing.stdout, re.MULTILINE) is not None


@pytest.mark.skipif(not _ffmpeg_has_libass(), reason="needs an ffmpeg built with libass")
@pytest.mark.parametrize(("bold", "face"), [(False, "Outfit-Regular"), (True, "Outfit-Bold")])
def test_libass_draws_the_staged_static_outfit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bold: bool, face: str
) -> None:
    """A real burn with the Windows staging: libass's own account of what it
    picked. A face from its font directory is named by its PostScript name and
    no path, ahead of any provider's Outfit — fontconfig's variable one on this
    box, whatever DirectWrite has on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(fonts, "user_font_dir", lambda: tmp_path / "none")
    if bold:
        original = fonts._probe_ass

        def bold_ass(family: str, **kw: int) -> str:
            text = original(family, **kw)
            return re.sub(r"^(Style: probe,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,)0,", r"\g<1>-1,", text, flags=re.MULTILINE)

        monkeypatch.setattr(fonts, "_probe_ass", bold_ass)

    chose = fonts._burn_probe("Outfit", tmp_path / "named.png", size=72, width=640, height=120)
    assert chose["faces"], chose
    assert chose["faces"][0] == {"file": face, "face": face}


@pytest.mark.parametrize(("platform", "system"), [("darwin", "CoreText"), ("win32", "DirectWrite")])
def test_doctors_caption_font_does_not_ask_fontconfig_off_linux(
    monkeypatch: pytest.MonkeyPatch, platform: str, system: str
) -> None:
    """Homebrew has a fontconfig; asking it about a Mac's burn measures the
    wrong resolver, which is the disagreement CLAUDE.md records on Linux."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(doctor.fonts, "probe", lambda family, **kw: {"drew": True})
    monkeypatch.setattr(
        doctor.captions, "font_match", lambda *a, **k: pytest.fail("fontconfig asked")
    )
    font = doctor._caption_font()
    assert font["ok"] is True
    assert font["font_system"] == system
    assert font["resolves_to"] is None

    text = doctor.render(
        {
            "proofcut": "0.0.0",
            "ok": True,
            "required": [],
            "optional": [],
            "display": {"ok": True, "how": "a Wayland session"},
            "caption_font": font,
        }
    )
    assert f"{system} — fontconfig is not this platform's font system" in text


def test_a_substituting_face_off_linux_is_still_a_cross(monkeypatch: pytest.MonkeyPatch) -> None:
    """The render's answer is a measurement on any OS — only the fontconfig
    half is dropped, never the ✗ the probe earned."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        doctor.fonts, "probe", lambda family, **kw: {"drew": False, "warning": "substituting"}
    )
    font = doctor._caption_font()
    assert font["ok"] is False
    assert "where CoreText looks" in font["fix"]


# -- the GPU workers — step 3 -------------------------------------------------


def _synth_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A box where the interpreter, the model and a voice all resolve."""
    voice = tmp_path / "voice"
    voice.mkdir()
    (voice / "ref.wav").touch()
    (voice / "ref.txt").write_text("the words", encoding="utf-8")
    monkeypatch.setattr(tts, "tts_python", lambda: Path(sys.executable))
    monkeypatch.setattr(tts, "model_dir", lambda: tmp_path / "model")
    monkeypatch.setenv("PROOFCUT_TTS_VOICE", str(voice))
    monkeypatch.delenv(tts.DEVICE_ENV, raising=False)
    return voice


def test_a_mac_reports_the_synthesiser_unavailable_rather_than_failing_in_the_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Everything resolves and nothing can run: the worker loads onto CUDA.
    Said before the GPU is asked for, the way doctor reports any absent
    optional capability — and the device knob opens the door for whoever
    measures MPS, without claiming it works."""
    voice = _synth_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: pytest.fail("worker spawned"))

    report = tts.available()
    assert report["available"] is False
    assert "no CUDA on macOS" in report["why"]
    with pytest.raises(tts.TTSError, match="no CUDA on macOS"):
        tts.synth("a line", voice, tmp_path / "out", [1], max_seconds=5)
    assert "no CUDA on macOS" in doctor._tts_entry()["why"]

    monkeypatch.setenv(tts.DEVICE_ENV, "mps")
    assert tts.available()["available"] is True


@pytest.mark.parametrize(("env", "expected"), [(None, "cuda"), ("cpu", "cpu")])
def test_the_synth_job_carries_the_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str | None, expected: str
) -> None:
    voice = _synth_ready(monkeypatch, tmp_path)
    # CUDA is the default *on Linux*; a real Mac refuses before the device is
    # read (the test above), which is what the macOS runner met.
    monkeypatch.setattr(sys, "platform", "linux")
    if env:
        monkeypatch.setenv(tts.DEVICE_ENV, env)
    jobs: list[dict] = []

    def worker(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        jobs.append(json.loads(Path(command[2]).read_text(encoding="utf-8")))
        Path(command[3]).write_text(json.dumps({"candidates": []}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(tts.subprocess, "run", worker)
    with pytest.raises(tts.TTSError, match="returned nothing"):
        tts.synth("a line", voice, tmp_path / "out", [1], max_seconds=5)
    assert jobs[0]["device"] == expected


@pytest.mark.parametrize(
    ("platform", "env", "why"),
    [
        ("darwin", None, "no CUDA on macOS"),
        ("linux", "mps", "bitsandbytes, which is CUDA-only"),
        ("linux", "cpu", "bitsandbytes, which is CUDA-only"),
    ],
)
def test_describe_refuses_a_device_its_4bit_load_cannot_use(
    monkeypatch: pytest.MonkeyPatch, platform: str, env: str | None, why: str
) -> None:
    """bitsandbytes has no MPS backend, so a Mac cannot describe today and a
    non-CUDA device has no path in the worker. `available()` says so, which
    is what doctor and `describe --plan` both read."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(describe, "vlm_python", lambda: Path(sys.executable))
    if env:
        monkeypatch.setenv(describe.DEVICE_ENV, env)
    else:
        monkeypatch.delenv(describe.DEVICE_ENV, raising=False)
    monkeypatch.setattr(describe.subprocess, "run", lambda *a, **k: pytest.fail("worker spawned"))

    report = describe.available()
    assert report["available"] is False
    assert why in report["why"]
    assert why in doctor._vlm_entry()["why"]
    with pytest.raises(describe.DescribeError, match=why.split(",")[0]):
        describe.describe_windows([{"index": 0, "media": "x.mp4", "timestamps": [0.0]}])


def test_linux_on_cuda_still_describes_and_says_so_in_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(describe, "vlm_python", lambda: Path(sys.executable))
    monkeypatch.delenv(describe.DEVICE_ENV, raising=False)
    assert describe.available()["available"] is True

    jobs: list[dict] = []

    def worker(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        jobs.append(json.loads(Path(command[2]).read_text(encoding="utf-8")))
        Path(command[3]).write_text(
            json.dumps({"results": [{"index": 0, "text": "a room"}]}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(describe.subprocess, "run", worker)
    describe.describe_windows([{"index": 0, "media": "x.mp4", "timestamps": [0.0]}])
    assert jobs[0]["device"] == "cuda"


def test_the_vision_worker_itself_refuses_before_importing_torch(tmp_path: Path) -> None:
    """Run the real worker the way proofcut does — a subprocess, never an import.
    proofcut's own venv has no torch, so a worker that reached for it first would
    die on `ModuleNotFoundError`; the refusal has to come before that."""
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"model": "m", "device": "mps", "windows": []}), encoding="utf-8")
    worker = Path(describe.__file__).with_name("_vlm_worker.py")

    done = subprocess.run(
        [sys.executable, str(worker), str(job), str(tmp_path / "out.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert "bitsandbytes" in done.stderr
    assert "ModuleNotFoundError" not in done.stderr
    assert not (tmp_path / "out.json").exists()


# -- what the first Windows CI run found -------------------------------------


def test_a_manifest_written_on_linux_still_writes_a_timeline_under_windows_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows `/footage/a.mp4` has no drive, so it is not absolute and
    `Path.as_uri()` raised — on every op that writes `project.otio`, over a URL
    nothing in proofcut reads back. 470 of the first Windows run's 527 failures."""
    monkeypatch.setattr(timeline, "Path", PureWindowsPath)
    clip = {"clip_id": "a", "duration": 2.0, "has_video": True}
    edit = timeline.Edit([timeline.Segment("a", 0.0, 1.0)])

    def url(source: str) -> str:
        otio = timeline.to_otio(edit, {"a": {**clip, "source": source}}, rate=25.0)
        return otio.tracks[0][0].media_reference.target_url

    assert url("/footage/a.mp4") == "file:///footage/a.mp4"
    assert url(r"C:\footage\a.mp4") == "file:///C:/footage/a.mp4"
    with pytest.raises(ValueError, match="relative"):
        url("footage/a.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_the_scene_scan_survives_a_colon_in_its_temp_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`:` separates a filter's options, so the scan's report file named by an
    absolute path ended at a Windows drive letter and ffmpeg refused the chain.
    Linux can hold a `:` in a directory name, so the same trap is reproduced
    here rather than only on a Windows runner, where the temp dir has one
    already. The input is passed relative, since the fix moves ffmpeg's cwd."""
    colons = tmp_path if os.name == "nt" else tmp_path / "C:scratch"
    colons.mkdir(exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(colons))
    clip = tmp_path / "cut.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:size=160x120:rate=25:duration=1",
         "-f", "lavfi", "-i", "color=c=blue:size=160x120:rate=25:duration=1",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    monkeypatch.chdir(tmp_path)

    cuts = media.scene_cuts("cut.mp4")

    assert [round(c["src_time"], 2) for c in cuts] == [1.0]


def test_the_agent_pane_spawns_the_claude_doctor_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """An npm install on Windows is `claude.cmd`, which `shutil.which` finds
    and Popen does not — so doctor was ✓ over a pane that could not spawn."""
    monkeypatch.delenv(webui.AGENT_BIN_ENV, raising=False)
    monkeypatch.setattr(
        webui.shutil, "which", lambda name: r"C:\npm\claude.cmd" if name == "claude" else None
    )
    assert webui._agent_bin() == r"C:\npm\claude.cmd"

    monkeypatch.setattr(webui.shutil, "which", lambda name: None)
    assert webui._agent_bin() == "claude", "unresolved, the spawn's own error names it"


def test_the_probe_names_what_libass_drew_by_file_never_by_path() -> None:
    """Outfit did not draw on the Windows runner, and all the probe could say
    was "not Outfit". libass logs its provider and every face it picks under
    `-v verbose`, and the probe carries both. The Linux lines are verbatim from
    this box; the Windows line is **constructed** in the same shape — no
    DirectWrite log has been read yet, so this pins the parse, not the OS."""
    stderr = (
        "[Parsed_ass_0 @ 0x55] Using font provider fontconfig\n"
        "[Parsed_ass_0 @ 0x7f] fontselect: (proofcut No Such Face 0000, 400, 0) -> "
        "/usr/share/fonts/google-noto-vf/NotoSansArabic[wght].ttf, 0, NotoSansArabic-Regular\n"
        "[Parsed_ass_0 @ 0x7f] Glyph 0x48 not found, selecting one more font for "
        "(proofcut No Such Face 0000, 400, 0)\n"
        "[Parsed_ass_0 @ 0x7f] fontselect: (proofcut No Such Face 0000, 400, 0) -> "
        "/usr/share/fonts/google-noto/NotoSans-Regular.ttf, 0, NotoSans-Regular\n"
    )
    chose = fonts._libass_choices(stderr)
    assert chose["provider"] == "fontconfig"
    assert chose["faces"] == [
        {"file": "NotoSansArabic[wght].ttf", "face": "NotoSansArabic-Regular"},
        {"file": "NotoSans-Regular.ttf", "face": "NotoSans-Regular"},
    ]

    windows = fonts._libass_choices(
        "[Parsed_ass_0 @ 0000] Using font provider directwrite (with GDI)\n"
        "[Parsed_ass_0 @ 0000] fontselect: (Outfit, 400, 0) -> "
        r"C:\Users\runneradmin\AppData\Local\Microsoft\Windows\Fonts\Outfit[wght].ttf, 0, Outfit-Regular"
    )
    assert windows["provider"] == "directwrite"
    assert windows["faces"] == [{"file": "Outfit[wght].ttf", "face": "Outfit-Regular"}]
    assert "runneradmin" not in json.dumps(windows)

    assert fonts._libass_choices("ffmpeg version n7.1\n") == {"provider": None, "faces": []}


def test_doctors_font_cross_says_what_drew_instead(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ✗ that names the substitute is a finding; one that says only "not
    Outfit" is where the Windows run stopped."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        doctor.fonts,
        "probe",
        lambda family, **kw: {
            "drew": False,
            "warning": "substituting",
            "font_provider": "directwrite",
            "drawn_with": [{"file": "arial.ttf", "face": "ArialMT"}],
        },
    )
    font = doctor._caption_font()
    text = doctor.render(
        {
            "proofcut": "0.0.0",
            "ok": False,
            "required": [],
            "optional": [],
            "display": {"ok": True, "how": "native"},
            "caption_font": font,
        }
    )
    assert "libass (directwrite) drew it with: ArialMT (arial.ttf)" in text


def test_a_headless_qt_that_draws_nothing_refuses_the_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ubuntu 24.04's MLT 7.22 Qt module ignores QT_QPA_PLATFORM=offscreen and
    wants X11: a 9:16 render dropped its crop filter, letterboxed the footage,
    and still agreed with the timeline frame for frame. So a headless Qt is
    judged by what it draws, not by the variable. HISTORY.md § A stranger's
    install, on a clean Ubuntu."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PROOFCUT_MELT", "melt")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(picture, "display_env", lambda: {"QT_QPA_PLATFORM": "offscreen"})
    monkeypatch.setattr(picture, "qt_draws", lambda env: False)
    monkeypatch.setattr(picture, "RENDER_SCRATCH", tmp_path / "scratch")
    project = tmp_path / "timeline.mlt"
    project.write_text("<mlt/>", encoding="utf-8")
    with pytest.raises(picture.PictureError, match="xvfb-run"):
        picture.render(project, tmp_path / "out.mp4", expect_frames=150)
