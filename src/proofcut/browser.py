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
* **A seek is CSS animations and WAAPI, the page's clock, and
  `window.proofcutSeek` for the rest.** `document.getAnimations()` reaches
  every CSS animation and transition. `CLOCK` runs before the page's own
  scripts and hands it a clock only a seek moves — `Date`,
  `performance.now` and `requestAnimationFrame` — so a page that animates
  from rAF draws the seeked frame with no hook (hyperframes' page-side clock,
  COMPETITORS.md § hyperframes). Timers and `Math.random` are not frozen: a
  page leaning on either still needs `proofcutSeek(seconds)`.
* **A frame is shot only once the page says it is ready.** A page with slow
  setup (a fetch, then real work) hands the promise to
  `window.proofcutWaitFor(promise, label)`, installed beside `CLOCK`; every
  seek waits for each open one, then for `document.fonts.ready`. A wait that
  rejects, or is open past `WAIT_TIMEOUT`, fails the seek by its label rather
  than shooting what the page drew so far (Remotion's `delayRender`, the
  idea and not the code, COMPETITORS.md § Remotion). Measured 2026-10-03: a
  page drawing after a fetch and 150 ms of work was shot empty without it.
* **A `<video>` is never decoded by the browser.** Measured 2026-10-03
  (`~/proofcut-work/spikes/video-in-graphic`): paused, it never moved off
  its first frame (served whole, it is not seekable), and with byte ranges
  served a seek to a time on a frame boundary still showed the frame
  before, 4 of 45 wrong at 24 fps over a 30 fps clip. So the first seek detaches each video's
  source and `VIDEOS` paints the frame showing at its media time as the
  element's own background, served at `VIDEO_PATH` from one ffmpeg decode of
  the file (hyperframes' injector and Remotion's frame server, the idea and
  not the code, COMPETITORS.md § Remotion). A video's sound is not used.

`PROOFCUT_CHROME` names the binary, then `proofcut setup`'s folder, then PATH.
"""

from __future__ import annotations

import base64
import bisect
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
from urllib.parse import parse_qs, unquote, urlsplit

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
#: A page's videos, frame by frame: `VIDEO_PATH + <its path>?t=<seconds>` is
#: the frame showing at that media time, `?info` its size and length.
VIDEO_PATH = "/_proofcut/video/"
#: Frames a page's video decodes per ffmpeg run: two seconds at 24 fps.
VIDEO_WINDOW = 48

#: How long a browser gets to start, and a page to load, before we give up.
START_TIMEOUT = 30.0
LOAD_TIMEOUT = 30.0
#: One DevTools command; a 4K screenshot is the slowest one there is.
COMMAND_TIMEOUT = 60.0
#: How long one seek waits on the page's `proofcutWaitFor` promises. Under
#: `COMMAND_TIMEOUT`, so the page's own refusal, naming what it waited on,
#: arrives before the connection's.
WAIT_TIMEOUT = 30.0


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
            raise BrowserError(f"the page raised: {raised(reply['exceptionDetails'])}")
        return reply.get("result", {}).get("value")


def raised(details: dict[str, Any]) -> str:
    """What a page's exception said: its message, not DevTools' "Uncaught"."""
    return (details.get("exception") or {}).get("description") or details.get("text") or "an exception"


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
        self.scratch: Path | None = None  # where videos are decoded (`launch`)
        self.videos: dict[Path, _Video] = {}

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
            parts = urlsplit(url)
            path = unquote(parts.path)
            if path.startswith(VIDEO_PATH):
                body, mime = self._video(path.removeprefix(VIDEO_PATH), parse_qs(parts.query, keep_blank_values=True))
            elif params.get("resourceType") == "Media":
                # A <video> or <audio> loading its own file. `VIDEOS` shows
                # the frames and the sound is never used, so the bytes are
                # never needed, and a big clip sent whole (one base64 CDP
                # message) resets the connection: 106 MB did, 2026-10-03.
                pass
            else:
                body, mime = self._local(path)
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
        target = _confined(base, name)
        if target is None:
            return None, ""
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".ttf", ".otf"):
            mime = "font/" + target.suffix[1:]
        elif target.suffix == ".woff2":
            mime = "font/woff2"
        return target.read_bytes(), mime

    def _video(self, name: str, query: dict[str, list[str]]) -> tuple[bytes | None, str]:
        """A frame of one of the page's videos, or its `info`; None refuses the request."""
        target = _confined(self.root, name) if self.root is not None and self.scratch is not None else None
        if target is None:
            return None, ""
        video = self.videos.get(target)
        if video is None:
            try:
                video = _Video.decode(target, self.scratch / f"v{len(self.videos)}")
            except BrowserError:
                return None, ""
            self.videos[target] = video
        if "info" in query:
            return json.dumps(video.info()).encode(), "application/json"
        try:
            seconds = float(query["t"][0])
        except (KeyError, ValueError):
            return None, ""
        return video.frame_at(seconds).read_bytes(), "image/png"

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
        page.send("Page.addScriptToEvaluateOnNewDocument", {"source": CLOCK})
        page.send("Page.addScriptToEvaluateOnNewDocument", {"source": WAITS})
        page.send("Page.addScriptToEvaluateOnNewDocument", {"source": VIDEOS})
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


def _confined(base: Path, name: str) -> Path | None:
    """The file `name` names under `base`, or None when it is outside or absent."""
    try:
        target = (base / name).resolve()
        target.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


class _Video:
    """One of a page's videos: its frame timestamps from ffprobe, and its
    frames decoded by ffmpeg a window at a time, only where a seek asks.

    A frame shows from its own timestamp until the next one's, counted from
    the first, so a time exactly on a boundary is the frame that starts
    there: the case the browser's decoder got wrong. A timestamp is known
    only to one tick of its stream's timebase (WebM's is a millisecond, so
    a 30 fps frame 20 is stored at 0.667 s, after its true 0.6667 s), and a
    frame starting within one tick of a time counts as started.

    Windowed, because decoding a whole 46 s phone clip took 9.6 s and 859 MB
    (measured 2026-10-03), where a graphic shows seconds of it, and the
    browser's profile can sit on a RAM-backed /tmp.
    """

    def __init__(self, source: Path, folder: Path, times: list[float], tick: float) -> None:
        self.source, self.folder, self.times, self.tick = source, folder, times, tick
        self.starts = [t - times[0] for t in times]
        self.frames: dict[int, Path] = {}
        step = self.starts[-1] / (len(times) - 1) if len(times) > 1 else 0.0
        self.duration = self.starts[-1] + step
        first = self.frame(0)
        self.width, self.height = struct.unpack("!II", first.read_bytes()[16:24])

    @classmethod
    def decode(cls, source: Path, folder: Path) -> _Video:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=time_base:packet=pts_time", "-of", "json", str(source)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        )
        try:
            read = json.loads(probe.stdout)
            num, _, den = read["streams"][0]["time_base"].partition("/")
            tick = int(num) / int(den)
            times = sorted(float(p["pts_time"]) for p in read.get("packets", []) if "pts_time" in p)
        except (ValueError, KeyError, IndexError, ZeroDivisionError):
            times, tick = [], 0.0
        if probe.returncode or not times:
            raise BrowserError(f"{source.name} has no video frames ffprobe can read")
        folder.mkdir(parents=True, exist_ok=True)
        return cls(source, folder, times, tick)

    def frame_at(self, seconds: float) -> Path:
        # Plus a microsecond: the page sends `t` rounded to one.
        return self.frame(max(0, bisect.bisect_right(self.starts, seconds + self.tick + 1e-6) - 1))

    def frame(self, index: int) -> Path:
        if index not in self.frames:
            self._window(index)
        return self.frames[index]

    def _window(self, first: int) -> None:
        """Decode `VIDEO_WINDOW` frames from `first`, seeking to half a tick
        before its timestamp, so the seek's first frame is that one."""
        count = min(VIDEO_WINDOW, len(self.times) - first)
        out = self.folder / f"w{first:06d}"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        for passthrough in (["-fps_mode", "passthrough"], ["-vsync", "passthrough"]):  # ffmpeg 5.1+, then older
            done = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{self.times[first] - self.tick / 2:.6f}",
                 "-i", str(self.source), "-map", "0:v:0", "-an", *passthrough, "-frames:v", str(count),
                 "-c:v", "png", str(out / "f%06d.png")],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
            )
            if "Unrecognized option" not in done.stderr:
                break
        decoded = sorted(out.glob("f*.png"))
        if done.returncode or len(decoded) != count:
            # A frame dropped or doubled would shift every time after it,
            # which is the error this route exists to remove.
            raise BrowserError(
                f"{self.source.name} decoded {len(decoded)} frames from frame {first} where it has {count}"
            )
        for k, path in enumerate(decoded):
            self.frames.setdefault(first + k, path)

    def info(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "duration": self.duration, "frames": len(self.times)}


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
    browser.scratch = profile / "proofcut-videos"
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
    return fonts(page)


def fonts(page: Page) -> list[dict[str, Any]]:
    """Every font face the page declares, once loading settles, with its status.

    A face no element has used yet is `unloaded`, and may fail only when a
    later frame shows text in it, so a capture asks again after its last frame.
    """
    return page.evaluate(
        "document.fonts.ready.then(() => [...document.fonts].map(f => "
        "({family: f.family, weight: f.weight, style: f.style, status: f.status})))"
    )


#: The page's clock, installed before any of its scripts run: `Date`,
#: `performance.now` and `requestAnimationFrame` stand still until a seek
#: moves them. A rAF callback is queued, and a seek sets the time and runs
#: the queue once, as one frame would — so a page's render loop draws the
#: seeked instant. `Date` counts from a fixed epoch, so a page that prints
#: the time prints the same thing on every capture. A callback that throws
#: fails the seek: the capture is the only view of the page anyone gets.
#: The real rAF is kept for `SEEK`'s own wait on the compositor.
CLOCK = """(() => {
  const RealDate = Date, realRAF = window.requestAnimationFrame.bind(window);
  const EPOCH = RealDate.UTC(2026, 0, 1);
  let now = 0, queue = [], next = 1;
  function FrozenDate(...a) {
    if (!new.target) return new RealDate(EPOCH + now).toString();
    return a.length ? new RealDate(...a) : new RealDate(EPOCH + now);
  }
  FrozenDate.prototype = RealDate.prototype;
  FrozenDate.now = () => EPOCH + now;
  FrozenDate.UTC = RealDate.UTC;
  FrozenDate.parse = RealDate.parse;
  window.Date = FrozenDate;
  performance.now = () => now;
  window.requestAnimationFrame = (cb) => { queue.push([next, cb]); return next++; };
  window.cancelAnimationFrame = (id) => { queue = queue.filter((e) => e[0] !== id); };
  window.__proofcutClock = {
    realRAF,
    advance(ms) { now = ms; const due = queue; queue = []; for (const [, cb] of due) cb(now); },
  };
})();"""

#: `window.proofcutWaitFor(promise, label)`: the page's way to say a frame
#: is not ready. Installed before the page's scripts, so setup that starts
#: at load can hand its promise over at once. `drain` waits for every open
#: one, including any a settling one opened, and throws by label on a
#: rejection or once `limit` real milliseconds pass; the timer is the real
#: `setTimeout`, which `CLOCK` leaves running.
WAITS = """(() => {
  const realSetTimeout = window.setTimeout.bind(window);
  let open = [];
  window.proofcutWaitFor = (promise, label) => {
    const w = { label: String(label || 'a proofcutWaitFor promise'), done: false, error: null };
    w.promise = Promise.resolve(promise).then(
      () => { w.done = true; },
      (e) => { w.done = true; w.error = e; });
    open.push(w);
    return promise;
  };
  window.__proofcutWaits = {
    async drain(limit) {
      // One timer for the whole drain: `Date` and `performance.now` are
      // `CLOCK`'s and stand still, so elapsed time cannot be read off them.
      const expired = new Promise((r) => realSetTimeout(() => r(true), limit));
      while (open.some((w) => !w.done || w.error)) {
        const failed = open.find((w) => w.done && w.error);
        if (failed) {
          open = open.filter((w) => w !== failed);
          const why = failed.error && failed.error.message ? failed.error.message : String(failed.error);
          throw new Error(`the page's wait on ${failed.label} failed: ${why}`);
        }
        const pending = open.filter((w) => !w.done);
        const timedOut = await Promise.race([
          Promise.all(pending.map((w) => w.promise)).then(() => false),
          expired,
        ]);
        if (timedOut) {
          const names = open.filter((w) => !w.done).map((w) => w.label).join(', ');
          throw new Error(`the page is still waiting on ${names} after ${limit / 1000}s`);
        }
      }
      open = open.filter((w) => !w.done);
    },
  };
})();"""

#: Each `<video>` drawn as the frame showing at its media time. The first
#: seek that finds one detaches its source (and poster), so the browser
#: never shows a frame of its own, and gives it the video's own size back
#: through `contain-intrinsic-size` (measured to lay out as the loaded video
#: did with no CSS size, a height, a width, both, a percentage and a
#: max-height; the width and height attributes match only two); then each seek paints that frame as the
#: element's background, `object-fit` and `object-position` carried over.
#: Media time is the page's time less `data-start` (seconds, default 0),
#: wrapped by `loop`, else held on the last frame. Each frame's load is a
#: `proofcutWaitFor`, so a video the folder cannot serve refuses the capture.
VIDEOS = """(() => {
  const state = new WeakMap();
  const FIT = { contain: 'contain', cover: 'cover', fill: '100% 100%', none: 'auto', 'scale-down': 'contain' };
  const sourceOf = (v) => {
    const own = v.getAttribute('src'), child = v.querySelector('source[src]');
    return own || (child && child.getAttribute('src'));
  };
  function adopt(v) {
    const src = sourceOf(v);
    if (!src) return null;
    const path = new URL(src, location.href).pathname.replace(/^\\//, '');
    v.pause();
    v.removeAttribute('src');
    v.removeAttribute('poster');
    v.querySelectorAll('source').forEach((e) => e.remove());
    v.load();
    const s = { path, shown: null };
    s.info = fetch('/_proofcut/video/' + path + '?info').then((r) => {
      if (!r.ok) throw new Error('proofcut cannot decode ' + path);
      return r.json();
    }).then((info) => {
      // The size the video had, without its source. Never the width and
      // height attributes: on a <video> those set the CSS size, and a page
      // sizing it by height alone got a box 1080 wide.
      v.style.aspectRatio = info.width + ' / ' + info.height;
      v.style.containIntrinsicSize = info.width + 'px ' + info.height + 'px';
      v.style.contain = 'size';
      const cs = getComputedStyle(v);
      v.style.backgroundSize = FIT[cs.objectFit] || 'contain';
      v.style.backgroundPosition = cs.objectPosition;
      v.style.backgroundRepeat = 'no-repeat';
      return info;
    });
    state.set(v, s);
    return s;
  }
  window.__proofcutVideos = {
    seek(t) {
      for (const v of document.querySelectorAll('video')) {
        const s = state.get(v) || adopt(v);
        if (!s) continue;
        window.proofcutWaitFor(s.info.then(async (info) => {
          let m = Math.max(0, t - Number(v.dataset.start || 0));
          m = v.loop && info.duration > 0 ? m % info.duration : Math.min(m, info.duration);
          const url = '/_proofcut/video/' + s.path + '?t=' + m.toFixed(6);
          if (url === s.shown) return;
          const img = new Image();
          img.src = url;
          await img.decode();
          v.style.backgroundImage = 'url("' + url + '")';
          s.shown = url;
        }), 'the video ' + s.path);
      }
    },
  };
})();"""

#: Move the page's clock to `t` seconds and run its frame, pause every
#: animation there, wait for what the page asked to be waited on and for
#: its fonts, then let two real frames render so the compositor has drawn
#: what the seek set. The clock goes first: a frame callback can create the
#: very animations the second step pauses, and the waits last, because the
#: frame callback or `proofcutSeek` can start the work being waited on.
SEEK = (
    "(async (t, limit) => {"
    " const clock = window.__proofcutClock;"
    " if (clock) clock.advance(t * 1000);"
    " for (const a of document.getAnimations()) { a.pause(); a.currentTime = t * 1000; }"
    " if (typeof window.proofcutSeek === 'function') await window.proofcutSeek(t);"
    " if (window.__proofcutVideos) window.__proofcutVideos.seek(t);"
    " if (window.__proofcutWaits) await window.__proofcutWaits.drain(limit);"
    " await document.fonts.ready;"
    " const raf = clock ? clock.realRAF : requestAnimationFrame;"
    " await new Promise(r => raf(() => raf(r)));"
    "})(%r, %r)"
)


def seek_expression(seconds: float) -> str:
    """The `Runtime.evaluate` expression that seeks a page to `seconds`."""
    return SEEK % (float(seconds), WAIT_TIMEOUT * 1000)

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
    page.evaluate(seek_expression(seconds))


def moving_at(page: Page, seconds: float) -> list[str]:
    """The animations still running through `seconds`, named for a refusal."""
    return list(page.evaluate(MOVING % float(seconds)) or [])


def screenshot(page: Page) -> bytes:
    data = page.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})["data"]
    return base64.b64decode(data)
