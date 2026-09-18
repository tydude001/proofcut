"""`proofcut review serve` — the review round as a feature, not a throwaway script.

Every version of the Scream video moved on a served page, and that serving
was hand-rebuilt at least four times (`~/proofcut-work/archive/spikes/approvals/`, `~/proofcut-work/archive/spikes/watch/`,
`~/proofcut-work/archive/spikes/review/`, `~/proofcut-work/archive/spikes/flash-review/`), each its own throwaway HTTP
server and its own `decisions.json` living beside the project rather than in
it (PLAN.md § The completion queue, item 6). This is that serving, once:
`proofcut review add` registers named renders/sheets/A-B members
(`ops.review_add`), this streams them with Range support so a phone can
scrub, and a plain HTML form posts a verdict straight into the manifest
(`ops.review_verdict`) — no JS, no build step, the `webui.py` stance.

**Different trust model from `webui.py`, deliberately.** `webui.py` binds
loopback and checks the `Host` header, because it can rewrite the whole
project and a page in any other tab on the same machine could reach it under
an attacker-chosen name. This server exists specifically to be reached off
the machine — from a phone on Tailscale — so loopback+Host buys nothing here
and a **token** stands in for it instead: `serve()` mints one
(`secrets.token_urlsafe`) unless the caller supplies one, and every request,
GET or POST, must carry it as `?t=`. The one line printed at startup is the
whole credential — that is what gets copied to the phone. The blast radius
is also smaller than `webui.py`'s: the only mutation this server can cause is
recording a verdict string against an already-registered item, never an edit.

Reuses `webui._stream_file`/`webui._ranges` for the one hand-rolled piece of
HTTP in the package (Range), rather than a second, divergent copy of the
byte math — see `webui.py`'s own module docstring.

**Each item's badge also joins `finishlog` by sha256** — every registered
item already carries its own hash (`ops.review_add`), so a `finish_check`
result follows the delivered *bytes* rather than a name or path that could
be re-registered under something new. `" — no finish_check yet"` or
`" — ⚠ finish_check: N fault(s)"` beside the byte-identical/MISMATCH control
badge — a **report**, never a refusal: nothing here blocks a page from
serving, the same stance `finish_report`'s `burned: "unknown"` and a
`control_ok: False` item (displayed, never refused at *serve* time) take.

**`kind="ab"` items of the same media kind get one shared player, carrying
the playhead across a pick** — two or more renders that want to be A/B'd at
the same moment (with/without a music bed is the goodsometimes case this was
built for; `pipeline.md`'s Edit section points there). This is the one place
this module emits JS, and it is scoped deliberately narrowly: every
`data-src`/`src`/form-`action` it touches is already server-rendered with
`?t=<token>` baked in, exactly like every other URL on this page — the
inline script reads `data-src` as an opaque string and assigns it to
`.src`, and never reads, stores, or constructs the token itself. That is
what keeps this page's no-JS-for-anything-token-bearing stance intact
(`webui.py`'s "never thread a token through a JS request" reasoning,
applied here even though this page has no cookie to protect): the script
exists to carry a `currentTime`/`paused` state across a `src` swap, nothing
the token needs to be involved in.

Because an inline script now exists, `Content-Security-Policy`'s
`default-src 'self'` (which has no `'unsafe-inline'` carve-out) needs a
`script-src` that allows it — a fresh **per-response nonce**
(`secrets.token_urlsafe`, generated in `_send_page`, echoed into both the
header and the one `<script nonce="...">` tag), never the review token
reused as a nonce: they are different secrets with different lifetimes,
and reusing one would tie the CSP nonce's exposure (in every page's HTML
source) to the credential that guards every request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from proofcut import finishlog, ops
from proofcut import webui as _webui
from proofcut.project import Project, ProjectError

DEFAULT_HOST = "127.0.0.1"
#: Deliberately not 8710 (`webui.DEFAULT_PORT`) or 8000/8080 — a review round
#: and the edit UI are commonly run against the same project at once.
DEFAULT_PORT = 8720

_MEDIA_KIND = {
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mkv": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".aac": "audio",
    ".m4a": "audio",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
}


class ReviewServerError(Exception):
    """A malformed request — reported as 400, not a crash."""


class Handler(BaseHTTPRequestHandler):
    """One request. `project_root` and `token` are set by `make_server`."""

    project_root: Path
    token: str
    verbose: bool = False
    server_version = "proofcut-review"
    sys_version = ""
    #: Keep-alive, so scrubbing a video does not reopen a connection per range.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.verbose:
            super().log_message(fmt, *args)

    # -- auth --------------------------------------------------------------

    def _token_ok(self, query: dict[str, list[str]]) -> bool:
        supplied = (query.get("t") or [""])[0]
        # Constant-time: this token is the only thing standing between the
        # LAN and a project's verdicts, so it gets the same comparison a
        # password would.
        return hmac.compare_digest(supplied, self.token)

    # -- plumbing ------------------------------------------------------------

    def _send(
        self, status: HTTPStatus, body: bytes, content_type: str, *, nonce: str | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        csp = "default-src 'self'; media-src 'self'"
        if nonce is not None:
            # Only the A/B group's one inline script needs this, and only a
            # page that actually emitted one gets a script-src at all — a
            # page with no script stays covered by default-src's implicit
            # script-src 'none' equivalent (no 'unsafe-inline', no nonce).
            csp += f"; script-src 'nonce-{nonce}'"
            # The page's stylesheet is inline as well, and `default-src
            # 'self'` blocks an inline <style> exactly as it blocks a script:
            # without this every rule was dropped, and a 1920px <video> ran
            # off a phone's screen at full width (2026-09-18).
            csp += f"; style-src 'nonce-{nonce}'"
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, status: HTTPStatus, html: str, *, nonce: str | None = None) -> None:
        self._send(status, html.encode("utf-8"), "text/html; charset=utf-8", nonce=nonce)

    def _fail(self, status: HTTPStatus, message: str) -> None:
        self._send_html(status, f"<!doctype html><p>{escape(message)}</p>")

    # -- routing -------------------------------------------------------------

    def do_GET(self) -> None:
        self._route(head_only=False)

    def do_HEAD(self) -> None:
        self._route(head_only=True)

    def _route(self, *, head_only: bool) -> None:
        url = urlparse(self.path)
        if not self._token_ok(parse_qs(url.query)):
            self._fail(HTTPStatus.FORBIDDEN, "missing or wrong token")
            return
        try:
            if url.path == "/":
                self._send_page(head_only=head_only)
            elif url.path.startswith("/media/"):
                self._send_media(unquote(url.path[len("/media/") :]), head_only=head_only)
            else:
                self._fail(HTTPStatus.NOT_FOUND, f"no such endpoint: {url.path}")
        except (ReviewServerError, ProjectError) as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if not self._token_ok(parse_qs(url.query)):
            self._fail(HTTPStatus.FORBIDDEN, "missing or wrong token")
            return
        if url.path != "/verdict":
            self._fail(HTTPStatus.NOT_FOUND, f"no such endpoint: {url.path}")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            form = parse_qs(raw.decode("utf-8"))
            name = (form.get("name") or [""])[0]
            verdict = (form.get("verdict") or [""])[0]
            note = (form.get("note") or [""])[0] or None
            if not name or not verdict:
                raise ReviewServerError("both 'name' and 'verdict' are required")
            ops.review_verdict(str(self.project_root), name, verdict, note=note)
        except (ReviewServerError, ProjectError) as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", f"/?t={quote(self.token)}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- handlers --------------------------------------------------------

    def _send_media(self, name: str, *, head_only: bool) -> None:
        listing = ops.review_list(str(self.project_root))
        item = next((it for it in listing["items"] if it["name"] == name), None)
        if item is None:
            raise ReviewServerError(f"no registered review item named {name!r}")
        source = self.project_root / item["path"]
        if not source.is_file():
            raise ReviewServerError(f"{name}'s file is missing from disk: {source}")
        # The shared Range/streaming mechanism (`webui.py`'s module docstring:
        # "the one thing hand-rolled rather than inherited"), not a second copy.
        _webui._stream_file(self, source, head_only=head_only)

    def _send_page(self, *, head_only: bool) -> None:
        listing = ops.review_list(str(self.project_root))
        # Fresh every response — never reused across requests, and never the
        # review token: see the module docstring's "never the review token
        # reused as a nonce".
        nonce = secrets.token_urlsafe(16)
        html = _render_page(listing, self.token, Project.open(self.project_root), nonce=nonce)
        if head_only:
            self._send(HTTPStatus.OK, b"", "text/html; charset=utf-8", nonce=nonce)
            return
        self._send_html(HTTPStatus.OK, html, nonce=nonce)


def _media_kind(item: dict[str, Any]) -> str | None:
    return _MEDIA_KIND.get(Path(item["path"]).suffix.lower())


def _finish_badge(item: dict[str, Any], project: Project) -> str:
    badge = ""
    if item["kind"] == "control":
        badge = " — byte-identical" if item.get("control_ok") else " — MISMATCH"

    # `finish_check`'s own WARN, joined on sha256 rather than name or path —
    # a review item can be re-registered under a new name, or the same
    # delivered bytes registered twice, and the finish_check result should
    # follow the *bytes*. **Report, never refuse**: this is a badge beside
    # an item that already serves, the same stance `finish_report`'s
    # `burned: "unknown"` and a `control_ok: False` item (displayed, never
    # refused at *serve* time) both take.
    fc = finishlog.for_sha256(project, item["sha256"])
    if fc is None:
        badge += " — no finish_check yet"
    elif not fc["ok"]:
        badge += f" — ⚠ finish_check: {fc['faults']} fault(s)"
    return badge


def _current_verdict_html(existing: dict[str, Any] | None) -> str:
    if not existing:
        return ""
    note = f" — {escape(existing['note'])}" if existing.get("note") else ""
    return f'<p class="current">Verdict: <strong>{escape(existing["verdict"])}</strong>{note}</p>'


def _verdict_form(name: str, token: str) -> str:
    return f"""<form method="post" action="/verdict?t={quote(token)}">
            <input type="hidden" name="name" value="{escape(name)}">
            <input type="text" name="verdict" placeholder="Verdict" aria-label="verdict" required>
            <input type="text" name="note" placeholder="Note (optional)" aria-label="note">
            <button type="submit">Save</button>
          </form>"""


def _item_section(
    item: dict[str, Any], token: str, project: Project, verdicts: dict[str, Any]
) -> str:
    """One item, independent player and verdict form — every kind but a
    grouped `"ab"` renders this way, unchanged from before A/B grouping
    existed."""
    name = item["name"]
    media_kind = _media_kind(item)
    src = f"/media/{quote(name)}?t={quote(token)}"
    if media_kind == "video":
        media_html = f'<video controls preload="metadata" src="{src}"></video>'
    elif media_kind == "audio":
        media_html = f'<audio controls preload="metadata" src="{src}"></audio>'
    elif media_kind == "image":
        media_html = f'<img src="{src}" alt="{escape(name)}">'
    else:
        media_html = f'<a href="{src}">{escape(item["path"])}</a>'

    badge = _finish_badge(item, project)
    current = _current_verdict_html(verdicts.get(name))

    return f"""
        <section>
          <h2>{escape(name)} <small>{escape(item["kind"])}{badge}</small></h2>
          {media_html}
          {current}
          {_verdict_form(name, token)}
        </section>
        """


def _ab_group_id(ab_items: list[dict[str, Any]]) -> str:
    """A stable id keyed off the group's own item names, not a fixed string —
    there is normally exactly one `"ab"` group per round, but two independent
    pairs in one round must not collide on the same DOM ids."""
    key = ",".join(sorted(it["name"] for it in ab_items))
    return "ab-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _render_ab_group(
    ab_items: list[dict[str, Any]],
    token: str,
    project: Project,
    verdicts: dict[str, Any],
    nonce: str,
) -> str:
    """Two or more `"ab"` items of one media kind: one shared player carrying
    the playhead across a pick, plus every item's own unchanged verdict form
    stacked below it.

    The inline script never sees the token: `data-src` is server-rendered
    with `?t=` already in it, exactly like every other `src=` on this page,
    and the script only ever reads that attribute and assigns it to
    `.src` — copied from goodsometimes' `serve_review.py` swap logic, minus
    any token handling, because there was none to copy.
    """
    group_id = _ab_group_id(ab_items)
    player_id = f"{group_id}-player"
    tag = "video" if _media_kind(ab_items[0]) == "video" else "audio"
    first_src = f"/media/{quote(ab_items[0]['name'])}?t={quote(token)}"

    buttons = "\n            ".join(
        f'<button type="button" data-src="/media/{quote(it["name"])}?t={quote(token)}" '
        f'data-name="{escape(it["name"])}" aria-pressed="{"true" if i == 0 else "false"}">'
        f"{escape(it['name'])}</button>"
        for i, it in enumerate(ab_items)
    )

    picks = "\n".join(
        f"""
        <div class="ab-pick">
          <h3>{escape(it["name"])} <small>{escape(it["kind"])}{_finish_badge(it, project)}</small></h3>
          {_current_verdict_html(verdicts.get(it["name"]))}
          {_verdict_form(it["name"], token)}
        </div>
        """
        for it in ab_items
    )

    script = f"""<script nonce="{nonce}">
(function () {{
  var player = document.getElementById("{player_id}");
  var picks = document.querySelectorAll("#{group_id} [data-src]");
  picks.forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      picks.forEach(function (b) {{ b.setAttribute("aria-pressed", String(b === btn)); }});
      var wasAt = player.currentTime;
      var wasPlaying = !player.paused;
      player.src = btn.getAttribute("data-src");
      player.addEventListener("loadedmetadata", function onReady() {{
        player.removeEventListener("loadedmetadata", onReady);
        player.currentTime = Math.min(wasAt, player.duration || wasAt);
        if (wasPlaying) {{ player.play(); }}
      }});
    }});
  }});
}})();
</script>"""

    return f"""
        <section id="{group_id}" class="ab-group">
          <h2>A/B</h2>
          <p class="hint">Pick one to switch; the playhead stays where it is.</p>
          <{tag} id="{player_id}" controls playsinline preload="metadata" src="{first_src}"></{tag}>
          <div class="ab-picks">
            {buttons}
          </div>
          <h2 class="verdicts">Verdicts</h2>
          {picks}
        </section>
        {script}
        """


def _render_page(
    listing: dict[str, Any], token: str, project: Project, *, nonce: str
) -> str:
    items = sorted(listing["items"], key=lambda it: it["added_at"])
    verdicts = listing["verdicts"]

    # Group only when there is something to switch between and every member
    # can share one <video>/<audio> element — a lone "ab" item has nothing
    # to pick against (falls back below, same path as any other item), and
    # mixed video/audio can't share a player at all (defensive: don't build
    # a broken one).
    ab_items = [it for it in items if it["kind"] == "ab"]
    kinds = {_media_kind(it) for it in ab_items}
    group_ok = len(ab_items) >= 2 and len(kinds) == 1 and None not in kinds
    ab_names = {it["name"] for it in ab_items} if group_ok else set()

    sections = []
    group_emitted = False
    for item in items:
        if item["name"] in ab_names:
            if not group_emitted:
                sections.append(_render_ab_group(ab_items, token, project, verdicts, nonce))
                group_emitted = True
            continue
        sections.append(_item_section(item, token, project, verdicts))

    body = "\n".join(sections) if sections else "<p>Nothing registered yet — `proofcut review add`.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>proofcut review</title>
<style nonce="{nonce}">
  /* `viewport-fit=cover` above and the `env()`s in `body` are one fix: each
     is a no-op without the other. This page is opened on a phone by design,
     and an iPhone home-screen shortcut runs standalone, with the status bar,
     the dynamic island and the home indicator over the page. 0px everywhere
     else. The `nonce` is what lets this sheet apply at all: see `_send`. */
  :root {{ color-scheme: light dark;
           --bg: #f6f5f2; --card: #ffffff; --ink: #1c1a17; --dim: #6b665e;
           --line: #e2ded6; --accent: #b86a12; --accent-ink: #ffffff; --field: #ffffff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #141311; --card: #1e1c19; --ink: #f1ede6; --dim: #a39c91;
             --line: #34302a; --accent: #e8a13c; --accent-ink: #1a1714; --field: #16140f; }}
  }}
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{ margin: 0 auto; max-width: 720px; background: var(--bg); color: var(--ink);
          font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
          padding: calc(16px + env(safe-area-inset-top, 0px)) calc(16px + env(safe-area-inset-right, 0px))
                   calc(24px + env(safe-area-inset-bottom, 0px)) calc(16px + env(safe-area-inset-left, 0px)); }}
  h1 {{ font-size: 0.8rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
        color: var(--dim); margin: 0 0 12px; }}
  h2 {{ font-size: 1.05rem; margin: 0 0 8px; overflow-wrap: anywhere; }}
  h3 {{ font-size: 1rem; margin: 0 0 6px; overflow-wrap: anywhere; }}
  h2.verdicts {{ margin-top: 20px; }}
  small {{ display: block; font-size: 0.8rem; font-weight: 400; color: var(--dim); margin-top: 2px; }}
  section {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px;
             padding: 14px; margin: 0 0 16px; }}
  video, audio, img {{ display: block; width: 100%; max-width: 100%; border-radius: 10px; }}
  video {{ background: #000; aspect-ratio: 16 / 9; }}
  /* The shared player stays in view while the verdicts below it scroll, so
     a switch is never made blind. */
  .ab-group > video, .ab-group > audio {{ position: sticky; z-index: 1;
                                           top: calc(8px + env(safe-area-inset-top, 0px)); }}
  a {{ color: var(--accent); overflow-wrap: anywhere; }}
  .hint {{ color: var(--dim); font-size: 0.85rem; margin: -2px 0 10px; }}
  .current {{ margin: 6px 0; }}
  .ab-picks {{ display: grid; gap: 8px; margin: 12px 0 0;
               grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); }}
  .ab-picks button {{ min-height: 48px; padding: 10px 12px; text-align: left; line-height: 1.25;
                      background: transparent; color: var(--ink); border: 1.5px solid var(--line); }}
  .ab-picks button[aria-pressed="true"] {{ background: var(--accent); color: var(--accent-ink);
                                           border-color: var(--accent); font-weight: 600; }}
  .ab-pick {{ border-top: 1px solid var(--line); padding-top: 12px; margin-top: 12px; }}
  form {{ display: grid; gap: 8px; grid-template-columns: 1fr; margin-top: 8px; }}
  @media (min-width: 560px) {{ form {{ grid-template-columns: 1fr 1.4fr auto; }} }}
  input[type=text] {{ width: 100%; min-width: 0; min-height: 44px; font: inherit; padding: 8px 12px;
                      color: var(--ink); background: var(--field); border: 1px solid var(--line);
                      border-radius: 10px; }}
  button {{ font: inherit; border-radius: 10px; cursor: pointer; }}
  form button {{ min-height: 44px; padding: 8px 18px; font-weight: 600; border: 0;
                 background: var(--ink); color: var(--bg); }}
  button:focus-visible, input:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
</style>
</head>
<body>
<h1>proofcut review</h1>
{body}
</body>
</html>"""


def make_server(
    path: Path | str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    verbose: bool = False,
) -> ThreadingHTTPServer:
    """Build a server for one project's review round. Opens it first, so a
    bad path fails now rather than on the first request.
    """
    project = Project.open(path)
    resolved_token = token or secrets.token_urlsafe(24)

    handler = type(
        "BoundReviewHandler",
        (Handler,),
        {"project_root": project.root, "token": resolved_token, "verbose": verbose},
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.token = resolved_token  # type: ignore[attr-defined]
    return server


def serve(
    path: Path | str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    verbose: bool = False,
) -> None:
    """Run a review round until interrupted. `port=0` picks a free one."""
    server = make_server(path, host=host, port=port, token=token, verbose=verbose)
    bound = server.server_address[1]
    url = f"http://{host}:{bound}/?t={server.token}"  # type: ignore[attr-defined]
    # Flushed, the `webui.serve` reason: this is the one line to copy to a
    # phone, and a piped stdout would otherwise hold it in the buffer.
    print(f"proofcut review: {url}  (project: {Project.open(path).root})", flush=True)
    print("Ctrl-C to stop.", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.shutdown()
        server.server_close()


__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "Handler", "ReviewServerError", "make_server", "serve"]
