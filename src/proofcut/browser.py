"""A headless browser, driven over the DevTools protocol, that captures a page frame by frame.

The renderer behind animated graphics (docs/plans/DAYDREAM.md § Animated
graphics, designed and spiked). A graphic is a web page the agent writes; this
module loads it, pauses every animation on it, seeks them to each frame's time
and screenshots with a transparent background. `magick` still draws the static
cards — this exists because a per-frame SVG route would make proofcut an
animation engine, while a browser brings CSS, easing and text layout with it.

Four rules, each measured in the spike:

* **Determinism is flags, not luck.** With a browser's default flags two
  captures of one page differed on 62 of 91 frames — text antialiasing, and
  letters caught mid-pop at different moments of a seek. `DETERMINISTIC_FLAGS`
  made them identical, and nothing here lets a caller drop them.
* **The page never touches the network or the disk.** It is served at
  `ORIGIN` by answering every request itself (`Fetch.requestPaused`): a path
  inside the graphic's folder, or a vendored font under `/_proofcut/fonts/`,
  and nothing else. Daydream's own agent reported its web fonts *"losing the
  race against frame capture"*; a page that can only load local files has no
  race to lose, and a page an agent wrote can read nothing outside its folder.
* **One code path on every OS**: the browser is asked for a port
  (`--remote-debugging-port=0`, read back from `DevToolsActivePort`) and spoken
  to over a minimal websocket client below, because a pipe to fds 3 and 4 is
  not something Python's `subprocess` can hand a child on Windows. No
  websocket library is a dependency.
* **A seek is CSS animations and WAAPI, and `window.proofcutSeek` for the
  rest.** `document.getAnimations()` reaches every CSS animation and
  transition; a page animating from its own clock has to expose
  `proofcutSeek(seconds)` or it renders its first frame every time.

`PROOFCUT_CHROME` names the binary, then `proofcut setup`'s folder, then PATH.
"""

from __future__ import annotations

import base64
import contextlib
import json
import mimetypes
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from proofcut import deps

#: The binaries tried on PATH, most specific first. The headless shell is what
#: `proofcut setup` installs and what the spike measured.
CHROME_NAMES = (
    "chrome-headless-shell",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)

#: The flag set the spike measured as deterministic across runs and across
#: parallel pages. Which flag fixes which failure was not isolated; the set is
#: what was measured, so it is applied whole.
DETERMINISTIC_FLAGS = (
    "--disable-gpu",
    "--disable-threaded-animation",
    "--run-all-compositor-stages-before-draw",
    "--disable-lcd-text",
    "--font-render-hinting=none",
    "--disable-partial-raster",
    "--disable-checker-imaging",
)

#: Everything else a capture browser runs with: no profile state, no
#: background traffic, no scrollbars in the frame.
QUIET_FLAGS = (
    "--headless",
    "--hide-scrollbars",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-component-update",
    "--disable-default-apps",
)

#: Where the page is served from. `.invalid` is reserved (RFC 2606), so no
#: request to it can ever leave the machine even if interception failed.
ORIGIN = "https://graphic.proofcut.invalid"

#: The vendored fonts, served to every page under this path.
FONTS_PATH = "/_proofcut/fonts/"

#: How long a browser gets to start, and a page to load, before we give up.
START_TIMEOUT = 30.0
LOAD_TIMEOUT = 30.0
#: One DevTools command; a 4K screenshot is the slowest one there is.
COMMAND_TIMEOUT = 60.0


class BrowserError(RuntimeError):
    """The browser could not be found, started, or made to draw the page."""


def chrome_path() -> str | None:
    """The browser binary a capture runs, or None when there is none."""
    named = os.environ.get("PROOFCUT_CHROME")
    if named:
        return named if Path(named).is_file() else None
    installed = deps.chrome()
    if installed.is_file():
        return str(installed)
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


#: What Chrome prints when Linux will not let it build its sandbox — Ubuntu
#: 23.10 and later refuse unprivileged user namespaces to any binary without
#: an AppArmor profile, and Chrome for Testing ships none, so it dies with
#: SIGTRAP before DevTools opens. Measured on CI's Ubuntu 24.04 runner.
NO_SANDBOX_MESSAGE = "No usable sandbox"


def launch_args(binary: str, profile: Path, *, sandbox: bool = True) -> list[str]:
    """The browser's argv. `--no-sandbox` where a root user needs it, or where
    the system refused the sandbox (`launch`)."""
    args = [
        binary,
        *QUIET_FLAGS,
        *DETERMINISTIC_FLAGS,
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
    ]
    if not sandbox or (sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0):
        args.append("--no-sandbox")
    return [*args, "about:blank"]


# -- a websocket client, just enough for DevTools --------------------------


class _Socket:
    """RFC 6455 client framing over a plain TCP socket: text out, text in."""

    def __init__(self, host: str, port: int, path: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=COMMAND_TIMEOUT)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserError("the browser closed the DevTools connection during the handshake")
            head += chunk
        status, _, self.buffer = head.partition(b"\r\n\r\n")
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise BrowserError(f"the browser refused the DevTools connection: {status[:200]!r}")

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buffer)))
            if not chunk:
                raise BrowserError("the browser closed the DevTools connection")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 1 << 16:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(header + mask + _mask(payload, mask))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode())

    def recv(self) -> str:
        parts: list[bytes] = []
        while True:
            first, second = self._read(2)
            opcode, n = first & 0x0F, second & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", self._read(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", self._read(8))
            payload = self._read(n)
            if opcode == 0x9:  # ping
                self._frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise BrowserError("the browser closed the DevTools connection")
            if opcode in (0x1, 0x0):
                parts.append(payload)
                if first & 0x80:
                    return b"".join(parts).decode()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


def _mask(payload: bytes, mask: bytes) -> bytes:
    """XOR a large payload with its mask, a word at a time."""
    n = len(payload)
    key = int.from_bytes((mask * ((n // 4) + 1))[:n], "big")
    return (int.from_bytes(payload, "big") ^ key).to_bytes(n, "big")


# -- the browser -------------------------------------------------------------


class Page:
    """One tab, attached by session id on the browser's one connection."""

    def __init__(self, browser: Browser, session: str) -> None:
        self.browser = browser
        self.session = session

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.browser.send(method, params, session=self.session)

    def evaluate(self, expression: str) -> Any:
        reply = self.send(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True}
        )
        if "exceptionDetails" in reply:
            details = reply["exceptionDetails"]
            text = (details.get("exception") or {}).get("description") or details.get("text")
            raise BrowserError(f"the page raised: {text}")
        return reply.get("result", {}).get("value")


class Browser:
    """A running headless browser and its DevTools connection."""

    def __init__(self, proc: subprocess.Popen[bytes], ws: _Socket, root: Path | None) -> None:
        self.proc = proc
        self.ws = ws
        self.root = root  # the folder pages are served from, or None
        self.next_id = 0
        self.pending: dict[int, dict[str, Any]] = {}
        self.fonts: list[str] = []  # every font path a page asked for
        self.sandboxed = True  # False when Linux refused the sandbox (`launch`)

    # A command is answered in order with whatever events arrive between; the
    # one event this module acts on is a paused request, answered inline.
    def send(self, method: str, params: dict[str, Any] | None = None, *, session: str | None = None) -> dict[str, Any]:
        return self.wait(self.post(method, params, session=session))

    def post(self, method: str, params: dict[str, Any] | None = None, *, session: str | None = None) -> int:
        self.next_id += 1
        message: dict[str, Any] = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.ws.send(json.dumps(message))
        return self.next_id

    def wait(self, ident: int) -> dict[str, Any]:
        deadline = time.monotonic() + COMMAND_TIMEOUT
        while ident not in self.pending:
            if time.monotonic() > deadline:
                raise BrowserError(f"the browser did not answer DevTools command {ident} in {COMMAND_TIMEOUT:g}s")
            message = json.loads(self.ws.recv())
            if "id" in message:
                self.pending[message["id"]] = message
            elif message.get("method") == "Fetch.requestPaused":
                self._serve(message["params"], message.get("sessionId"))
        reply = self.pending.pop(ident)
        if "error" in reply:
            raise BrowserError(f"DevTools refused: {reply['error'].get('message')}")
        return reply.get("result", {})

    def _serve(self, params: dict[str, Any], session: str | None) -> None:
        """Answer one of the page's requests from its folder, or refuse it."""
        request_id = params["requestId"]
        url = params["request"]["url"]
        body = None
        if url.startswith(ORIGIN + "/"):
            body, mime = self._local(unquote(urlsplit(url).path))
        if body is None:
            self.post("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}, session=session)
            return
        self.post(
            "Fetch.fulfillRequest",
            {
                "requestId": request_id,
                "responseCode": 200,
                "responseHeaders": [{"name": "Content-Type", "value": mime}, {"name": "Access-Control-Allow-Origin", "value": "*"}],
                "body": base64.b64encode(body).decode(),
            },
            session=session,
        )

    def _local(self, path: str) -> tuple[bytes | None, str]:
        """The bytes a served path names, confined to the page's folder or the fonts."""
        if path.startswith(FONTS_PATH):
            base, name = FONTS_DIR, path.removeprefix(FONTS_PATH)
            self.fonts.append(name)
        elif self.root is not None:
            base, name = self.root, path.lstrip("/") or "index.html"
        else:
            return None, ""
        try:
            target = (base / name).resolve()
            target.relative_to(base.resolve())
        except (ValueError, OSError):
            return None, ""
        if not target.is_file():
            return None, ""
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".ttf", ".otf"):
            mime = "font/" + target.suffix[1:]
        elif target.suffix == ".woff2":
            mime = "font/woff2"
        return target.read_bytes(), mime

    def new_page(self, width: int, height: int) -> Page:
        target = self.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self.send("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        page = Page(self, session)
        page.send("Page.enable")
        page.send("Runtime.enable")
        page.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
        page.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        page.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        return page

    def close(self) -> None:
        self.ws.close()
        with contextlib.suppress(OSError):
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


#: The package's vendored fonts, the only fonts a page gets besides its own.
FONTS_DIR = Path(__file__).parent / "fonts"


@contextlib.contextmanager
def launch(root: Path | None = None) -> Iterator[Browser]:
    """Start a browser serving `root`'s files at `ORIGIN`, and stop it after."""
    binary = chrome_path()
    if binary is None:
        raise BrowserError(
            "no headless browser for animated graphics — run `proofcut setup`, or set "
            "PROOFCUT_CHROME to a Chrome, Chromium or chrome-headless-shell binary"
        )
    profile = Path(tempfile.mkdtemp(prefix="proofcut-browser-"))
    try:
        try:
            browser = _start(binary, profile, root, sandbox=True)
        except _SandboxRefused:
            # The page is the graphic's own folder, served with no network and
            # no disk, so this is the case the sandbox matters least in — and
            # the capture records that it ran without one (`Browser.sandboxed`).
            shutil.rmtree(profile, ignore_errors=True)
            profile.mkdir()
            browser = _start(binary, profile, root, sandbox=False)
    except BaseException:
        shutil.rmtree(profile, ignore_errors=True)
        raise
    try:
        yield browser
    finally:
        browser.close()
        shutil.rmtree(profile, ignore_errors=True)


class _SandboxRefused(BrowserError):
    """Linux refused the browser its sandbox (`NO_SANDBOX_MESSAGE`)."""


def _start(binary: str, profile: Path, root: Path | None, *, sandbox: bool) -> Browser:
    """Run the browser and connect to it, or raise with what it said on the way down."""
    # Inside the profile, which is removed only once the browser has closed:
    # a running browser holds this file open, and Windows will not delete it.
    log = profile / "proofcut-stderr.log"
    with log.open("wb") as err:
        proc = subprocess.Popen(
            launch_args(binary, profile, sandbox=sandbox),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
        )
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + START_TIMEOUT
        lines: list[str] = []
        while len(lines) < 2:
            if proc.poll() is not None:
                said = log.read_text(errors="replace")
                if sandbox and NO_SANDBOX_MESSAGE in said:
                    raise _SandboxRefused(said[-400:])
                tail = " ".join(said.strip().splitlines()[-3:])[-400:]
                raise BrowserError(
                    f"{binary} exited ({proc.returncode}) before it opened DevTools"
                    + (f": {tail}" if tail else "")
                )
            if time.monotonic() > deadline:
                raise BrowserError(f"{binary} did not open DevTools in {START_TIMEOUT:g}s")
            with contextlib.suppress(OSError):
                lines = port_file.read_text().split()
            time.sleep(0.05)
        browser = Browser(proc, _Socket("127.0.0.1", int(lines[0]), lines[1]), root)
        browser.sandboxed = sandbox
        return browser
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        raise


def load(page: Page, path: str = "/index.html") -> list[dict[str, Any]]:
    """Navigate `page` to a served path and wait for it and its fonts.

    Returns every font face the page declared with its status. A face that
    failed to load is refused by the caller, never drawn in a fallback: that
    is the silent substitution this renderer exists to not have.
    """
    page.send("Page.navigate", {"url": ORIGIN + path})
    deadline = time.monotonic() + LOAD_TIMEOUT
    while page.evaluate("document.readyState") != "complete":
        if time.monotonic() > deadline:
            raise BrowserError(f"the page did not finish loading in {LOAD_TIMEOUT:g}s")
        time.sleep(0.02)
    return page.evaluate(
        "document.fonts.ready.then(() => [...document.fonts].map(f => "
        "({family: f.family, weight: f.weight, style: f.style, status: f.status})))"
    )


#: Pause every animation and put it at `t` seconds, then let two frames
#: render so the compositor has drawn what the seek set.
SEEK = (
    "(async (t) => {"
    " for (const a of document.getAnimations()) { a.pause(); a.currentTime = t * 1000; }"
    " if (typeof window.proofcutSeek === 'function') await window.proofcutSeek(t);"
    " await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));"
    "})(%r)"
)

#: What is moving *through* `t`: an animation that started before it and has
#: not ended. One that starts exactly at `t` is the outro beginning, not the
#: hold moving; an infinite one (a blinking caret) is always moving.
MOVING = (
    "((t) => { const ms = t * 1000; return document.getAnimations().flatMap(a => {"
    " const c = a.effect && a.effect.getComputedTiming(); if (!c) return [];"
    " const start = c.delay || 0, end = start + c.activeDuration;"
    " if (!(ms > start + 0.5 && ms < end - 0.5)) return [];"
    " const el = a.effect.target;"
    " const who = el ? el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +"
    "   (el.classList.length ? '.' + [...el.classList].join('.') : '') : '?';"
    " return [(a.animationName || a.id || 'animation') + ' on ' + who]; }); })(%r)"
)


def seek(page: Page, seconds: float) -> None:
    page.evaluate(SEEK % float(seconds))


def moving_at(page: Page, seconds: float) -> list[str]:
    """The animations still running through `seconds`, named for a refusal."""
    return list(page.evaluate(MOVING % float(seconds)) or [])


def screenshot(page: Page) -> bytes:
    data = page.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})["data"]
    return base64.b64decode(data)
