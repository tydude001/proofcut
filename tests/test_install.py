"""`proofcut setup` — install only what doctor crossed, pinned, and reverse it exactly.

docs/plans/INSTALL.md is the design. Nothing here downloads: every pin is
replaced by a `file://` archive built in the test, hashed the way a real one
is, and doctor's report is handed in rather than probed, so the suite runs the
same on a box with every tool installed and on one with none. Each test pins
`sys.platform` to linux unless it is about another OS, and then fakes that
one; the link tests skip on a real Windows host, where a symlink needs
Developer Mode.

Every test runs in a fake home, since setup's whole job is writing into one.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import shutil
import ssl
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from stubs import write_stub

from proofcut import autoeditor, deps, doctor, install, picture
from proofcut.cli import main

links = pytest.mark.skipif(os.name == "nt", reason="a symlink needs Developer Mode on Windows")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with ~/.local/bin first on PATH and a system bin behind it."""
    home = tmp_path / "home"
    home.mkdir()
    system = tmp_path / "usr-bin"
    system.mkdir()
    # Windows finds a home through USERPROFILE and ignores HOME: setting HOME
    # alone installed into the CI runner's real profile, where every later
    # resolver found it (2026-09-16).
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(home / ".local" / "bin"), str(system)]))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(install.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(install, "_gpu", lambda: False)
    if os.name == "nt":
        # A stub is `uv.cmd` there, and `shutil.which` under a faked linux
        # applies no PATHEXT, so it would find no uv at all.
        monkeypatch.setattr(install, "_uv", lambda: shutil.which("uv") or shutil.which("uv.cmd"))
    assert tmp_path in deps.root().parents, "setup's folder must be inside the fake home"
    return home


def _report(*, failing: tuple[str, ...] = (), display: dict[str, Any] | None = None) -> dict[str, Any]:
    names = ("ffmpeg", "ffprobe", "whisper", "auto-editor", "melt")
    required = [
        doctor._entry(n, n, ok=n not in failing, why=None if n not in failing else f"{n} is broken")
        for n in names
    ]
    return {
        "ok": not failing,
        "required": required,
        "optional": [],
        "display": display or {"ok": True, "applicable": True, "wayland_display": "wayland-0"},
    }


def _pin(path: Path, version: str = "test", member: str | None = None) -> install.Pin:
    data = path.read_bytes()
    return install.Pin(version, path.as_uri(), hashlib.sha256(data).hexdigest(), len(data), member)


def _tarball(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:xz") as tar:
        for name, text in files.items():
            body = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(body))
    return path


def _listing(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.fixture
def pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, install.Pin]:
    """ffmpeg, auto-editor and Shotcut pins that point at local files."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    ffmpeg = _tarball(
        downloads / "ffmpeg.tar.xz",
        {"ffmpeg-x/bin/ffmpeg": "ffmpeg\n", "ffmpeg-x/bin/ffprobe": "ffprobe\n", "ffmpeg-x/bin/ffplay": "ffplay\n"},
    )
    auto_editor = downloads / "auto-editor"
    auto_editor.write_text("auto-editor\n", encoding="utf-8")
    shotcut = _tarball(downloads / "shotcut.txz", {"Shotcut.app/melt": "melt\n", "Shotcut.app/bin/melt-7": "melt\n"})
    table = {"ffmpeg": _pin(ffmpeg), "auto-editor": _pin(auto_editor), "melt": _pin(shotcut)}
    monkeypatch.setattr(install, "PINS", {name: {"x86_64": pin} for name, pin in table.items()})
    monkeypatch.setattr(install, "_missing_libraries", lambda binaries, lib_dir=None: [])
    return table


# -- the plan --------------------------------------------------------------


@pytest.mark.parametrize(("system", "cpu"), [("freebsd14", "x86_64"), ("darwin", "ppc")])
def test_setup_refuses_where_it_does_not_install(
    home: Path, monkeypatch: pytest.MonkeyPatch, system: str, cpu: str
) -> None:
    """An OS with no route, and a Mac CPU no pin covers. Apple silicon was
    the second of these until 2026-09-20 and is now installed on."""
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: cpu)
    with pytest.raises(install.InstallError, match="Macs on Intel or Apple silicon"):
        install.plan(_report(failing=("ffmpeg",)))


def test_only_the_pieces_doctor_crossed_are_planned(home: Path, pins: dict[str, install.Pin]) -> None:
    """A working system tool is never replaced: the report is the input."""
    steps = install.plan(_report(failing=("ffprobe", "auto-editor")))
    assert [p["name"] for p in steps["pieces"]] == ["ffmpeg", "auto-editor"]
    assert steps["bytes"] == pins["ffmpeg"].size + pins["auto-editor"].size
    assert steps["unavailable"] == []
    assert install.plan(_report())["pieces"] == []


def test_a_reason_is_the_refusals_first_sentence_not_its_advice(home: Path, pins: dict[str, install.Pin]) -> None:
    """A resolver's refusal ends in advice that, on Linux, recommends `proofcut
    setup` — read inside setup's own plan on the first clean Ubuntu run."""
    report = _report(failing=("auto-editor",))
    row = next(r for r in report["required"] if r["name"] == "auto-editor")
    row["why"] = "auto-editor not found. `proofcut setup` installs it. Or by hand."
    assert install.plan(report)["pieces"][0]["why"] == "auto-editor not found."


def test_a_melt_that_draws_nothing_headless_plans_shotcut(home: Path, pins: dict[str, install.Pin]) -> None:
    """Both distro MLTs pass the melt row and draw nothing without a display.
    INSTALL.md, measurement 2."""
    display = {"ok": False, "applicable": True, "headless_qt": True, "qt_probe": False}
    steps = install.plan(_report(display=display))
    assert [p["name"] for p in steps["pieces"]] == ["melt"]
    assert "draws nothing" in steps["pieces"][0]["why"]


def test_a_box_with_no_display_is_probed_under_offscreen(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting the variable is doctor's next advice, so setup asks the question
    it would lead to rather than needing a second run — and says to set it."""
    asked: list[dict[str, str]] = []
    monkeypatch.setattr(install.picture, "display_env", dict)
    monkeypatch.setattr(install.picture, "qt_draws", lambda env: asked.append(env) or False)
    steps = install.plan(_report(display={"ok": False, "applicable": True}))
    assert asked and asked[0]["QT_QPA_PLATFORM"] == "offscreen"
    assert [p["name"] for p in steps["pieces"]] == ["melt"]
    assert any("QT_QPA_PLATFORM=offscreen" in note for note in steps["notes"])


def test_a_cpu_no_pin_covers_is_told_to_follow_doctor(home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.platform, "machine", lambda: "armv7l")
    steps = install.plan(_report(failing=("ffmpeg",)))
    assert steps["pieces"] == []
    assert steps["unavailable"][0]["name"] == "ffmpeg"
    assert "armv7l" in steps["unavailable"][0]["why"]


def _fake_uv(tmp_path: Path, tool_list: str = "") -> tuple[Path, Path]:
    log = tmp_path / "uv-argv.txt"
    uv = write_stub(
        tmp_path / "usr-bin" / "uv",
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"if sys.argv[1:3] == ['tool', 'list']: print({tool_list!r}, end='')\n",
    )
    return uv, log


def test_whisper_is_a_uv_tool_on_the_python_an_intel_mac_can_install(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin both kits use, and CPU torch without an NVIDIA driver: the
    default pulls CUDA torch, 5.5 GB against 1.9 GB."""
    _uv, log = _fake_uv(tmp_path)
    steps = install.plan(_report(failing=("whisper",)))
    assert [p["name"] for p in steps["pieces"]] == ["whisper"]
    assert steps["bytes"] == install.WHISPER_BYTES["cpu"]
    monkeypatch.setattr(install.doctor, "report", _report)
    result = install.install(steps, say=lambda line: None)
    assert result["installed"] == ["whisper"]
    assert "tool install --python 3.12 openai-whisper --torch-backend cpu" in log.read_text()
    assert install.read_record()["pieces"]["whisper"]["uv_tool"] == "openai-whisper"


@links
def test_the_python_and_folders_uv_added_for_whisper_go_with_it(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--python 3.12` downloads a Python into uv's folder on a fresh box, and
    the first tool install makes uv's folders and ~/.local/bin. All of it is
    setup's doing, so uninstall takes it all back, as mac_trial.sh does."""
    share = home / ".local" / "share" / "uv"
    write_stub(
        tmp_path / "usr-bin" / "uv",
        "import os, sys\n"
        f"share = {str(share)!r}\n"
        f"bin_dir = {str(home / '.local' / 'bin')!r}\n"
        "args = [a for a in sys.argv[1:] if a not in ('--color', 'never')]\n"
        "if args[:2] == ['python', 'dir']: print(share + '/python')\n"
        "elif args[:2] == ['tool', 'dir']: print(share + '/tools')\n"
        "elif args[:2] == ['tool', 'install']:\n"
        "    os.makedirs(share + '/python/cpython-3.12-linux/bin')\n"
        "    os.makedirs(share + '/tools/openai-whisper')\n"
        "    os.makedirs(bin_dir, exist_ok=True)\n"
        "elif args[:2] == ['tool', 'uninstall']:\n"
        "    import shutil; shutil.rmtree(share + '/tools/openai-whisper')\n",
    )
    before = _listing(home)
    monkeypatch.setattr(install.doctor, "report", _report)
    result = install.install(install.plan(_report(failing=("whisper",))), say=lambda line: None)
    assert result["installed"] == ["whisper"]
    assert install.read_record()["pieces"]["whisper"]["uv_pythons"] == [str(share / "python" / "cpython-3.12-linux")]
    install.uninstall()
    assert _listing(home) == before


def test_a_whisper_the_user_already_installed_is_never_reinstalled(home: Path, tmp_path: Path) -> None:
    """Reinstalling it would make `--uninstall` remove something of theirs."""
    _fake_uv(tmp_path, tool_list="openai-whisper v20250625\n- whisper\n")
    steps = install.plan(_report(failing=("whisper",)))
    assert steps["pieces"] == []
    assert "--reinstall" in steps["unavailable"][0]["fix"]


def test_no_uv_means_no_whisper_and_says_how_to_get_it(home: Path) -> None:
    steps = install.plan(_report(failing=("whisper",)))
    assert steps["pieces"] == []
    assert "docs.astral.sh/uv" in steps["unavailable"][0]["fix"]


# -- installing, and reversing it ------------------------------------------


@links
@pytest.mark.parametrize("had_local_bin", [False, True])
def test_install_then_uninstall_leaves_the_home_as_it_was(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch, had_local_bin: bool
) -> None:
    """The kits' contract, carried over: everything added is recorded, and
    only that is removed. A file of the user's in ~/.local/bin is neither
    replaced nor removed, and a ~/.local/bin setup made goes with it."""
    if had_local_bin:
        (home / ".local" / "bin").mkdir(parents=True)
        (home / ".local" / "bin" / "ffprobe").write_text("theirs\n", encoding="utf-8")
    before = _listing(home)
    monkeypatch.setattr(install.doctor, "report", _report)

    steps = install.plan(_report(failing=("ffmpeg", "auto-editor", "melt")))
    result = install.install(steps, say=lambda line: None)
    assert result["installed"] == ["ffmpeg", "auto-editor", "melt"]
    assert result["failed"] == []

    ffmpeg = home / ".local" / "bin" / "ffmpeg"
    assert ffmpeg.is_symlink() and deps.root() in ffmpeg.resolve().parents
    ffprobe = home / ".local" / "bin" / "ffprobe"
    if had_local_bin:
        assert not ffprobe.is_symlink() and ffprobe.read_text() == "theirs\n"
        assert any("left" in note and "ffprobe" in note for note in result["notes"])
    else:
        assert ffprobe.is_symlink()
    assert not list(deps.root().glob("ffmpeg/*/bin/ffplay"))  # a third of the download, unused
    assert deps.auto_editor().is_file() and os.access(deps.auto_editor(), os.X_OK)
    assert deps.melt().is_file()

    install.uninstall()
    assert _listing(home) == before


@links
def test_a_link_the_user_repointed_is_theirs_now(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install.doctor, "report", _report)
    install.install(install.plan(_report(failing=("ffmpeg",))), say=lambda line: None)
    link = home / ".local" / "bin" / "ffmpeg"
    link.unlink()
    elsewhere = tmp_path / "their-ffmpeg"
    elsewhere.write_text("theirs\n", encoding="utf-8")
    link.symlink_to(elsewhere)
    install.uninstall()
    assert link.is_symlink() and link.resolve() == elsewhere.resolve()


def test_a_download_that_does_not_hash_to_its_pin_keeps_nothing(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = install.Pin("test", pins["auto-editor"].url, "0" * 64, pins["auto-editor"].size)
    monkeypatch.setitem(install.PINS, "auto-editor", {"x86_64": wrong})
    monkeypatch.setattr(install.doctor, "report", lambda: _report(failing=("auto-editor",)))
    result = install.install(install.plan(_report(failing=("auto-editor",))), say=lambda line: None)
    assert result["installed"] == []
    assert "not the pinned" in result["failed"][0]["why"]
    assert not deps.auto_editor().exists()
    assert not list(deps.root().rglob("*.part"))
    assert "auto-editor" not in install.read_record()["pieces"]
    assert result["ok"] is False


def test_a_melt_missing_desktop_libraries_names_them_and_keeps_nothing(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server image has none of the 20 libraries `ldd` names in a bare
    container (INSTALL.md, measurement 2); setup has no sudo to add them."""
    monkeypatch.setattr(install.shutil, "which", lambda name, path=None: "/usr/bin/ldd" if name == "ldd" else None)
    monkeypatch.setattr(install, "_missing_libraries", lambda binaries, lib_dir=None: ["libasound.so.2", "libGL.so.1"])
    monkeypatch.setattr(install.doctor, "report", lambda: _report(failing=("melt",)))
    result = install.install(install.plan(_report(failing=("melt",))), say=lambda line: None)
    assert result["installed"] == []
    assert "libasound.so.2, libGL.so.1" in result["failed"][0]["why"]
    assert not deps.melt().parent.parent.exists()


def test_an_auto_editor_missing_libgomp_says_so_and_keeps_nothing(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare Ubuntu has no libgomp; the first container run found out at `seed`."""
    monkeypatch.setattr(install.shutil, "which", lambda name, path=None: "/usr/bin/ldd" if name == "ldd" else None)
    monkeypatch.setattr(install, "_missing_libraries", lambda binaries, lib_dir=None: ["libgomp.so.1"])
    monkeypatch.setattr(install.doctor, "report", lambda: _report(failing=("auto-editor",)))
    result = install.install(install.plan(_report(failing=("auto-editor",))), say=lambda line: None)
    assert result["installed"] == []
    assert "libgomp1" in result["failed"][0]["why"]
    assert not deps.auto_editor().parent.exists()


def _browserless() -> dict[str, Any]:
    report = _report()
    report["optional"] = [doctor._entry("browser", "animated graphics", why="no browser found")]
    return report


def test_the_browser_rides_its_optional_row_and_keeps_its_modes(
    home: Path, pins: dict[str, install.Pin], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headless shell is installed only when doctor's optional browser row is
    unavailable, and its binary comes out executable: `zipfile` drops the modes."""
    assert [p["name"] for p in install.plan(_report())["pieces"]] == [], "a present browser installs nothing"
    folder = "chrome-headless-shell-linux64"
    archive = tmp_path / "downloads" / "chrome.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        binary = zipfile.ZipInfo(f"{folder}/chrome-headless-shell")
        binary.external_attr = 0o100755 << 16
        zipped.writestr(binary, "#!chrome\n")
        zipped.writestr(f"{folder}/resources.pak", "data")
    monkeypatch.setitem(install.PINS, "chrome", {"x86_64": _pin(archive)})
    monkeypatch.setattr(install.doctor, "report", _browserless)
    steps = install.plan(_browserless())
    assert [p["name"] for p in steps["pieces"]] == ["chrome"]
    assert steps["pieces"][0]["why"].startswith("animated graphics")
    result = install.install(steps, say=lambda line: None)
    assert result["installed"] == ["chrome"]
    assert deps.chrome().is_file()
    if os.name != "nt":
        assert os.access(deps.chrome(), os.X_OK)
    install.uninstall()
    assert not deps.chrome().parent.parent.exists()


def test_a_piece_setup_installed_that_still_fails_is_not_installed_again(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Usually PATH order; reinstalling would loop instead of saying so."""
    monkeypatch.setattr(install.doctor, "report", _report)
    install.install(install.plan(_report(failing=("auto-editor",))), say=lambda line: None)
    steps = install.plan(_report(failing=("auto-editor",)))
    assert steps["pieces"] == []
    assert "installed it already" in steps["unavailable"][0]["why"]


# -- the resolvers find it -------------------------------------------------


def test_the_resolvers_prefer_what_setup_installed_over_path(home: Path, tmp_path: Path) -> None:
    """Setup installs one only when the PATH one failed doctor, so a PATH-first
    search would find the failing one again (deps.py)."""
    if os.name == "nt":
        pytest.skip("a stub on Windows is a .cmd, which neither resolver looks for under a faked linux")
    system = tmp_path / "usr-bin"
    write_stub(system / "auto-editor", "print('29.3.1')\n")
    banner = "import sys\nprint('melt 7.41.0')\n"
    write_stub(system / "melt", banner)
    assert autoeditor.binary() != str(deps.auto_editor())

    deps.auto_editor().parent.mkdir(parents=True)
    installed_ae = write_stub(deps.auto_editor(), "print('31.6.0')\n")
    deps.melt().parent.mkdir(parents=True)
    installed_melt = write_stub(deps.melt(), banner)
    assert autoeditor.binary() == str(installed_ae)
    assert picture.melt_command() == [str(installed_melt)]


# -- the pins, and the command ---------------------------------------------


#: The two pins not on GitHub, and it is one reason: no macOS ffmpeg with
#: libass is released there for either CPU. evermeet.cx names each build by
#: version and osxexperts.net by major version, so both are still pins.
OFF_GITHUB = {
    ("ffmpeg", "macos-x86_64"): re.compile(r"https://evermeet\.cx/ffmpeg/ff(mpeg|probe)-\d+(\.\d+)+\.zip"),
    ("ffmpeg", "macos-aarch64"): re.compile(r"https://www\.osxexperts\.net/ff(mpeg|probe)\d+arm\.zip"),
    # Chrome for Testing publishes only to Google's bucket, one folder per
    # version, and keeps every one — a pinned build, never `Stable`.
    **{
        ("chrome", target): re.compile(
            r"https://storage\.googleapis\.com/chrome-for-testing-public/\d+(\.\d+){3}/"
            r"(linux64|win64|mac-x64|mac-arm64)/chrome-headless-shell-\2\.zip"
        )
        for target in ("x86_64", "windows-x86_64", "macos-x86_64", "macos-aarch64")
    },
}

#: Where a pin carries the publisher's own hash of the binary inside it.
PINS_ITS_MEMBER = {("ffmpeg", "macos-aarch64")}


def test_every_pin_is_a_github_release_with_a_sha256() -> None:
    for name, by_target in install.PINS.items():
        for target, pins in by_target.items():
            assert target in ("x86_64", "aarch64", "windows-x86_64", "macos-x86_64", "macos-aarch64"), name
            for pin in pins if isinstance(pins, tuple) else (pins,):
                if (name, target) in OFF_GITHUB:
                    assert OFF_GITHUB[name, target].fullmatch(pin.url), (name, target)
                else:
                    assert pin.url.startswith("https://github.com/") and "/releases/download/" in pin.url, name
                assert "latest" not in pin.url, f"{name}: a moving tag is not a pin"
                assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256), name
                assert pin.size > 1_000_000, name
                if (name, target) in PINS_ITS_MEMBER:
                    assert re.fullmatch(r"[0-9a-f]{64}", pin.member_sha256 or ""), (name, target)
                else:
                    assert pin.member_sha256 is None, (name, target)
    assert set(install.PINS) == {"ffmpeg", "auto-editor", "melt", "chrome"}
    for target in ("x86_64", "windows-x86_64", "macos-x86_64", "macos-aarch64"):
        assert all(target in install.PINS[name] for name in install.PINS), target
    # One universal dmg, not two pins that could drift apart.
    assert install.PINS["melt"]["macos-aarch64"] is install.PINS["melt"]["macos-x86_64"]


def test_setup_with_no_terminal_asks_for_yes_rather_than_installing(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(install.doctor, "report", lambda: _report(failing=("auto-editor",)))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["setup"]) == 1
    assert "pass --yes" in capsys.readouterr().err
    assert not deps.root().exists()


def test_setup_plan_writes_nothing_and_exits_by_what_is_missing(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(install.doctor, "report", lambda: _report(failing=("auto-editor",)))
    assert main(["setup", "--plan"]) == 1
    assert "auto-editor — test" in capsys.readouterr().out
    assert not deps.root().exists()
    monkeypatch.setattr(install.doctor, "report", _report)
    assert main(["setup", "--plan"]) == 0


def test_setup_yes_installs_and_exits_by_what_doctor_says_after(
    home: Path, pins: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reports = iter([_report(failing=("auto-editor",)), _report()])
    monkeypatch.setattr(install.doctor, "report", lambda: next(reports))
    assert main(["setup", "--yes"]) == 0
    assert "Doctor now reports everything required." in capsys.readouterr().out
    assert main(["setup", "--uninstall", "--yes"]) == 0
    assert not deps.root().exists()


@pytest.mark.parametrize(
    ("system", "cpu", "leads"),
    [
        ("linux", "x86_64", True),
        ("win32", "AMD64", True),
        ("darwin", "x86_64", True),
        ("darwin", "arm64", True),
        ("freebsd14", "x86_64", False),
    ],
)
def test_doctor_leads_with_setup_only_where_setup_installs(
    monkeypatch: pytest.MonkeyPatch, system: str, cpu: str, leads: bool
) -> None:
    """Nobody is told to run a command that refuses them. An Apple silicon
    Mac was the case this guarded until 2026-09-20; a platform with no route
    at all is the one that keeps it honest now."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(deps.platform, "machine", lambda: cpu)
    assert ("`proofcut setup`" in doctor._ffmpeg_entry("ffmpeg", "x")["fix"]) is leads


# -- Windows and the Intel Mac ---------------------------------------------


def _zip(path: Path, files: dict[str, str], mode: int = 0o644) -> Path:
    with zipfile.ZipFile(path, "w") as zipped:
        for name, text in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zipped.writestr(info, text)
    return path


@pytest.fixture
def windows(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, install.Pin]:
    """A faked Windows: its own LOCALAPPDATA, and the kit's archive shapes.

    `shutil.which` is stubbed to a plain PATH walk, since under a faked win32
    the real one reaches for `_winapi`.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    # `urlopen` builds its default HTTPS opener even for a `file://` pin, and
    # Python 3.13.14 builds that context eagerly: under a faked win32 it reaches
    # ssl's Windows-only certificate store and dies on `enum_certificates`, which
    # exists only on a real Windows. There is no store to read here.
    monkeypatch.setattr(ssl, "enum_certificates", lambda store: [], raising=False)
    monkeypatch.setattr(install.platform, "machine", lambda: "AMD64")
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))

    def which(name: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
        for folder in os.environ["PATH"].split(os.pathsep):
            for candidate in (Path(folder) / name, Path(folder) / f"{name}.exe"):
                if candidate.is_file():
                    return str(candidate)
        return None

    monkeypatch.setattr(shutil, "which", which)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    ffmpeg = _zip(downloads / "ffmpeg.zip", {
        "ffmpeg-9-essentials_build/bin/ffmpeg.exe": "ffmpeg\n",
        "ffmpeg-9-essentials_build/bin/ffprobe.exe": "ffprobe\n",
        "ffmpeg-9-essentials_build/bin/ffplay.exe": "ffplay\n",
        "ffmpeg-9-essentials_build/LICENSE": "gpl\n",
    })
    auto_editor = downloads / "auto-editor.exe"
    auto_editor.write_text("auto-editor\n", encoding="utf-8")
    shotcut = _zip(downloads / "shotcut.zip", {"Shotcut/melt.exe": "melt\n", "Shotcut/avcodec-62.dll": "dll\n"})
    table = {"ffmpeg": _pin(ffmpeg), "auto-editor": _pin(auto_editor), "melt": _pin(shotcut)}
    monkeypatch.setattr(install, "PINS", {name: {"windows-x86_64": pin} for name, pin in table.items()})
    assert home in deps.root().parents and deps.root().parts[-4:-2] == ("AppData", "Local")
    return table


def test_windows_puts_the_ffmpeg_binaries_themselves_on_path_and_takes_them_back(
    home: Path, windows: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink needs Developer Mode there, so the two static .exe files are
    moved into ~/.local/bin, recorded by hash, and removed only while they
    are still setup's. Everything else stays in the one folder."""
    (home / ".local" / "bin").mkdir(parents=True)
    before = _listing(home)
    monkeypatch.setattr(install.doctor, "report", _report)
    steps = install.plan(_report(failing=("ffmpeg", "auto-editor", "melt")))
    assert [p["name"] for p in steps["pieces"]] == ["ffmpeg", "auto-editor", "melt"]
    result = install.install(steps, say=lambda line: None)
    assert result["installed"] == ["ffmpeg", "auto-editor", "melt"], result["failed"]
    assert not any("PATH" in note for note in result["notes"]), result["notes"]

    local_bin = home / ".local" / "bin"
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        assert (local_bin / name).is_file() and not (local_bin / name).is_symlink()
    assert not (local_bin / "ffplay.exe").exists()
    assert not list((deps.root() / "ffmpeg").iterdir()), "the rest of the zip is not kept"
    assert deps.auto_editor() == deps.root() / "auto-editor" / "auto-editor.exe"
    assert deps.auto_editor().is_file()
    assert deps.melt() == deps.root() / "melt" / "Shotcut" / "melt.exe"
    assert deps.melt().is_file()

    install.uninstall()
    assert _listing(home) == before


def test_windows_leaves_an_ffmpeg_that_is_not_setups_any_more(
    home: Path, windows: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.doctor, "report", _report)
    install.install(install.plan(_report(failing=("ffmpeg",))), say=lambda line: None)
    replaced = home / ".local" / "bin" / "ffmpeg.exe"
    replaced.write_text("theirs now\n", encoding="utf-8")
    install.uninstall()
    assert replaced.read_text() == "theirs now\n"
    assert not (home / ".local" / "bin" / "ffprobe.exe").exists()


def test_windows_never_moves_over_an_ffmpeg_the_user_has(
    home: Path, windows: dict[str, install.Pin], monkeypatch: pytest.MonkeyPatch
) -> None:
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "ffmpeg.exe").write_text("theirs\n", encoding="utf-8")
    monkeypatch.setattr(install.doctor, "report", _report)
    result = install.install(install.plan(_report(failing=("ffmpeg",))), say=lambda line: None)
    assert (local_bin / "ffmpeg.exe").read_text() == "theirs\n"
    assert any("left" in note and "ffmpeg.exe" in note for note in result["notes"])
    install.uninstall()
    assert (local_bin / "ffmpeg.exe").read_text() == "theirs\n"


@pytest.mark.parametrize(("system", "cpu"), [("win32", "AMD64"), ("darwin", "x86_64"), ("darwin", "arm64")])
def test_whisper_off_linux_installs_the_way_its_kit_did(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str, cpu: str
) -> None:
    """No `--torch-backend` on any of them, GPU or not: PyPI's torch there is
    the CPU build a person ran. The Intel Mac adds numpy 1.x and wheels-only
    numba/llvmlite, mac_trial.sh's arguments — and Apple silicon takes none
    of that, since torch still ships arm64 wheels."""
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: cpu)
    monkeypatch.setattr(install, "_gpu", lambda: True)
    argv = install.whisper_argv("uv")
    assert "--torch-backend" not in argv
    assert argv[:5] == ["uv", "tool", "install", "--python", "3.12"] and argv[-1] == "openai-whisper"
    intel = ["--with", "numpy<2", "--no-build-package", "numba", "--no-build-package", "llvmlite"]
    assert argv[5:-1] == (intel if (system, cpu) == ("darwin", "x86_64") else [])
    # The plan's size follows the same rule. Asked on the Mac only: under a
    # faked win32 the real `shutil.which` reaches for `_winapi`.
    if system == "darwin" and cpu == "x86_64" and os.name != "nt":
        _fake_uv(tmp_path)
        steps = install.plan(_report(failing=("whisper",)))
        assert steps["bytes"] == install.WHISPER_BYTES["cpu"]


@links
def test_an_intel_mac_takes_two_ffmpeg_zips_and_shotcut_off_its_dmg(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mac_trial.sh's Intel route: evermeet's one-binary zips, linked like
    Linux's, and Shotcut.app copied off a read-only mount that is detached
    after. `hdiutil` is a stub that builds the mounted image's app."""
    monkeypatch.setattr(sys, "platform", "darwin")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    ffmpeg = _zip(downloads / "ffmpeg-9.zip", {"ffmpeg": "ffmpeg\n"}, mode=0o755)
    ffprobe = _zip(downloads / "ffprobe-9.zip", {"ffprobe": "ffprobe\n"}, mode=0o755)
    dmg = downloads / "shotcut.dmg"
    dmg.write_text("a disk image\n", encoding="utf-8")
    log = tmp_path / "hdiutil.txt"
    write_stub(
        tmp_path / "usr-bin" / "hdiutil",
        "import os, sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'attach':\n"
        "    mount = sys.argv[sys.argv.index('-mountpoint') + 1]\n"
        "    app = os.path.join(mount, 'Shotcut.app', 'Contents', 'MacOS')\n"
        "    os.makedirs(app)\n"
        "    open(os.path.join(app, 'melt'), 'w').write('melt\\n')\n",
    )
    monkeypatch.setenv("PATH", os.pathsep.join([os.environ["PATH"], "/bin", "/usr/bin"]))
    monkeypatch.setattr(install, "PINS", {
        "ffmpeg": {"macos-x86_64": (_pin(ffmpeg), _pin(ffprobe))},
        "melt": {"macos-x86_64": _pin(dmg)},
        "auto-editor": {},
    })
    before = _listing(home)
    monkeypatch.setattr(install.doctor, "report", _report)
    steps = install.plan(_report(failing=("ffmpeg", "melt")))
    assert steps["pieces"][0]["urls"] == [ffmpeg.as_uri(), ffprobe.as_uri()]
    assert steps["pieces"][0]["bytes"] == ffmpeg.stat().st_size + ffprobe.stat().st_size
    result = install.install(steps, say=lambda line: None)
    assert result["installed"] == ["ffmpeg", "melt"], result["failed"]

    for name in ("ffmpeg", "ffprobe"):
        link = home / ".local" / "bin" / name
        assert link.is_symlink() and os.access(link.resolve(), os.X_OK)
    assert deps.melt() == deps.root() / "melt" / "Shotcut.app" / "Contents" / "MacOS" / "melt"
    assert deps.melt().is_file()
    calls = log.read_text().splitlines()
    assert calls[0].startswith("attach -nobrowse -readonly") and calls[1].startswith("detach")
    assert sorted(p.name for p in (deps.root() / "melt").iterdir()) == ["Shotcut.app"]

    install.uninstall()
    assert _listing(home) == before


@links
def test_an_apple_silicon_mac_takes_the_arm64_zips_and_the_same_universal_dmg(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Intel route with arm64 downloads in its place. Two things are its
    own: the zips carry the Finder's `__MACOSX` copies, which are not
    installed, and each pin names the hash osxexperts.net publishes for the
    binary inside — checked after unpacking, since the zip is what was
    hashed on the way down."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert install.target() == "macos-aarch64"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    ffmpeg = _zip(downloads / "ffmpeg9arm.zip", {"ffmpeg": "ffmpeg\n", "__MACOSX/._ffmpeg": "junk\n"}, mode=0o755)
    ffprobe = _zip(downloads / "ffprobe9arm.zip", {"ffprobe": "ffprobe\n"}, mode=0o755)
    member = {name: hashlib.sha256(f"{name}\n".encode()).hexdigest() for name in ("ffmpeg", "ffprobe")}
    monkeypatch.setattr(install, "PINS", {
        "ffmpeg": {"macos-aarch64": (_pin(ffmpeg, member=member["ffmpeg"]), _pin(ffprobe, member=member["ffprobe"]))},
        "melt": {},
        "auto-editor": {},
    })
    before = _listing(home)
    monkeypatch.setattr(install.doctor, "report", _report)
    result = install.install(install.plan(_report(failing=("ffmpeg",))), say=lambda line: None)
    assert result["installed"] == ["ffmpeg"], result["failed"]
    for name in ("ffmpeg", "ffprobe"):
        link = home / ".local" / "bin" / name
        assert link.is_symlink() and os.access(link.resolve(), os.X_OK)
    assert not (deps.root() / "ffmpeg" / "bin" / "__MACOSX").exists()

    install.uninstall()
    assert _listing(home) == before


@links
def test_a_rezipped_ffmpeg_is_refused_by_the_hash_its_publisher_states(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zip hashes as pinned and holds a different binary — what a
    publisher who rebuilds without renaming the file would ship. Nothing of
    that piece is kept, and the refusal names the published number."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    ffmpeg = _zip(downloads / "ffmpeg9arm.zip", {"ffmpeg": "a newer build\n"}, mode=0o755)
    ffprobe = _zip(downloads / "ffprobe9arm.zip", {"ffprobe": "ffprobe\n"}, mode=0o755)
    stale = hashlib.sha256(b"ffmpeg\n").hexdigest()
    monkeypatch.setattr(install, "PINS", {
        "ffmpeg": {"macos-aarch64": (_pin(ffmpeg, member=stale), _pin(ffprobe))},
        "melt": {},
        "auto-editor": {},
    })
    before = _listing(home)
    monkeypatch.setattr(install.doctor, "report", _report)
    result = install.install(install.plan(_report(failing=("ffmpeg",))), say=lambda line: None)
    assert result["installed"] == []
    (failure,) = result["failed"]
    assert failure["name"] == "ffmpeg" and stale in failure["why"]
    assert not (home / ".local" / "bin" / "ffmpeg").exists()

    install.uninstall()
    assert _listing(home) == before
