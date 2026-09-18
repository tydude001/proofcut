"""`proofcut review serve`, exercised over a real socket.

The same discipline `test_webui_http.py` applies to the edit UI: a handler
function called directly proves nothing about routing, Range, or whether the
token guard actually fires. So these start the real `ThreadingHTTPServer` and
speak HTTP to it — including the case webui.py's own suite doesn't have to
cover, since loopback+Host isn't the guard here: a request with no token, or
the wrong one, at all.
"""

from __future__ import annotations

import http.client
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from proofcut import finishlog, ops, reviewserver
from proofcut.project import Project

TOKEN = "test-token-not-a-secret"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with one registered render, ready for a review round."""
    root = tmp_path / "proj"
    ops.init(root)
    (root / "renders" / "teaser.mp4").write_bytes(b"0123456789" * 50)
    ops.review_add(root, "teaser", "renders/teaser.mp4", kind="render")
    return root


@pytest.fixture
def server(project: Path) -> Iterator[str]:
    """The real server on a free port, torn down after the test."""
    httpd = reviewserver.make_server(project, port=0, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(url: str, **kwargs: Any) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, **kwargs)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _post_form_no_redirect(server: str, path: str, fields: dict[str, str]) -> tuple[int, dict[str, str]]:
    """POST form-encoded, without following a redirect — so the 302 itself is visible."""
    parts = urlsplit(server)
    assert parts.hostname is not None and parts.port is not None
    conn = http.client.HTTPConnection(parts.hostname, parts.port)
    try:
        body = urlencode(fields)
        conn.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        response.read()
        return response.status, dict(response.getheaders())
    finally:
        conn.close()


def test_a_request_with_no_token_is_forbidden(server: str) -> None:
    status, _, _ = _get(f"{server}/")
    assert status == 403


def test_a_request_with_the_wrong_token_is_forbidden(server: str) -> None:
    status, _, _ = _get(f"{server}/?t=wrong")
    assert status == 403


def test_the_page_lists_a_registered_item(server: str) -> None:
    status, _, body = _get(f"{server}/?t={TOKEN}")
    assert status == 200
    assert b"teaser" in body


def test_a_range_request_on_a_registered_item_returns_206_and_exactly_those_bytes(
    project: Path, server: str
) -> None:
    whole = (project / "renders" / "teaser.mp4").read_bytes()

    status, headers, body = _get(
        f"{server}/media/teaser?t={TOKEN}", headers={"Range": "bytes=10-19"}
    )
    assert status == 206
    assert headers["Content-Range"] == f"bytes 10-19/{len(whole)}"
    assert body == whole[10:20]


def test_media_with_no_token_is_forbidden(server: str) -> None:
    status, _, _ = _get(f"{server}/media/teaser")
    assert status == 403


def test_a_verdict_posted_with_the_right_token_is_recorded(project: Path, server: str) -> None:
    status, headers = _post_form_no_redirect(
        server, f"/verdict?t={TOKEN}", {"name": "teaser", "verdict": "ship it", "note": "watched twice"}
    )
    assert status == 302
    assert headers["Location"] == f"/?t={TOKEN}"

    verdicts = ops.review_list(project)["verdicts"]
    assert verdicts["teaser"]["verdict"] == "ship it"
    assert verdicts["teaser"]["note"] == "watched twice"


def test_a_verdict_posted_with_the_wrong_token_is_refused_and_not_recorded(
    project: Path, server: str
) -> None:
    status, _ = _post_form_no_redirect(
        server, "/verdict?t=wrong", {"name": "teaser", "verdict": "ship it"}
    )
    assert status == 403
    assert ops.review_list(project)["verdicts"] == {}


def test_a_verdict_for_an_unregistered_name_is_refused(server: str) -> None:
    status, _ = _post_form_no_redirect(
        server, f"/verdict?t={TOKEN}", {"name": "no-such-item", "verdict": "ship it"}
    )
    assert status == 400


# -- finish_check's WARN badge, joined on sha256 -----------------------------


def test_an_item_with_no_finish_check_shows_the_no_check_yet_note(server: str) -> None:
    status, _, body = _get(f"{server}/?t={TOKEN}")
    assert status == 200
    assert b"no finish_check yet" in body


def test_a_failing_finish_check_shows_the_warn_badge(project: Path, server: str) -> None:
    item = ops.review_list(project)["items"][0]
    finishlog.append(
        Project.open(project),
        final=str(project / item["path"]),
        sha256=item["sha256"],
        faults=3,
        ok=False,
        summary={"missing": 3},
    )

    status, _, body = _get(f"{server}/?t={TOKEN}")

    assert status == 200
    assert "⚠ finish_check: 3 fault(s)".encode() in body
    assert b"no finish_check yet" not in body


def test_a_passing_finish_check_shows_no_warn(project: Path, server: str) -> None:
    item = ops.review_list(project)["items"][0]
    finishlog.append(
        Project.open(project),
        final=str(project / item["path"]),
        sha256=item["sha256"],
        faults=0,
        ok=True,
        summary={},
    )

    status, _, body = _get(f"{server}/?t={TOKEN}")

    assert status == 200
    assert b"finish_check" not in body
    assert b"no finish_check yet" not in body


def test_a_finish_check_keyed_to_a_different_sha256_does_not_match(
    project: Path, server: str
) -> None:
    """The join is on bytes, not on name — a stale or unrelated log entry
    must not paint a badge that does not belong to this item."""
    finishlog.append(
        Project.open(project),
        final="/tmp/some-other-file.mp4",
        sha256="0" * 64,
        faults=5,
        ok=False,
        summary={},
    )

    status, _, body = _get(f"{server}/?t={TOKEN}")

    assert status == 200
    assert b"no finish_check yet" in body
    assert b"5 fault" not in body


# -- A/B grouping ------------------------------------------------------------


def _add_ab_pair(project: Path, suffix: str = "mp4") -> None:
    (project / "renders" / f"a.{suffix}").write_bytes(b"A" * 500)
    (project / "renders" / f"b.{suffix}").write_bytes(b"B" * 500)
    ops.review_add(project, "a", f"renders/a.{suffix}", kind="ab")
    ops.review_add(project, "b", f"renders/b.{suffix}", kind="ab")


def _extract_section(body: bytes, name: str) -> bytes:
    """The `<section>...</section>` whose `<h2>` names `name` — non-greedy so
    it stops at the first close, and DOTALL because the block spans lines."""
    pattern = re.compile(
        rb"<section>\s*<h2>" + re.escape(name.encode()) + rb".*?</section>", re.DOTALL
    )
    match = pattern.search(body)
    assert match, f"no <section> found for {name!r} in:\n{body!r}"
    return match.group(0)


def test_two_ab_items_render_one_shared_player_and_both_pick_buttons(
    project: Path, server: str
) -> None:
    _add_ab_pair(project)

    status, _, body = _get(f"{server}/?t={TOKEN}")
    assert status == 200

    # One shared player, not one <video> per A/B item — the "teaser" render
    # fixture item still gets its own ordinary <video>, so two total.
    assert body.count(b"<video") == 2
    ab_players = re.findall(rb'<video id="(ab-[0-9a-f]+)-player"', body)
    assert len(ab_players) == 1

    assert f'data-src="/media/a?t={TOKEN}"'.encode() in body
    assert f'data-src="/media/b?t={TOKEN}"'.encode() in body


def test_a_lone_ab_item_renders_as_an_ordinary_section(project: Path, server: str) -> None:
    (project / "renders" / "solo.mp4").write_bytes(b"S" * 500)
    ops.review_add(project, "solo", "renders/solo.mp4", kind="ab")

    status, _, body = _get(f"{server}/?t={TOKEN}")
    assert status == 200

    assert b"<script" not in body
    assert b'class="ab-group"' not in body
    solo_section = _extract_section(body, "solo")
    assert f'src="/media/solo?t={TOKEN}"'.encode() in solo_section


def test_ab_items_of_mixed_media_kind_fall_back_to_independent_sections(
    project: Path, server: str
) -> None:
    (project / "renders" / "vid.mp4").write_bytes(b"V" * 500)
    (project / "renders" / "aud.wav").write_bytes(b"W" * 500)
    ops.review_add(project, "vid", "renders/vid.mp4", kind="ab")
    ops.review_add(project, "aud", "renders/aud.wav", kind="ab")

    status, _, body = _get(f"{server}/?t={TOKEN}")
    assert status == 200

    assert b"<script" not in body
    assert b'class="ab-group"' not in body
    vid_section = _extract_section(body, "vid")
    aud_section = _extract_section(body, "aud")
    assert f'src="/media/vid?t={TOKEN}"'.encode() in vid_section
    assert f'src="/media/aud?t={TOKEN}"'.encode() in aud_section


def test_non_ab_items_are_unaffected_by_an_ab_group_on_the_same_page(
    project: Path, server: str
) -> None:
    """Regression guard against the partition logic leaking: the "render"
    item's own section must come out byte-for-byte identical whether or not
    an "ab" group shares the page with it."""
    status, _, before = _get(f"{server}/?t={TOKEN}")
    assert status == 200
    before_section = _extract_section(before, "teaser")

    _add_ab_pair(project)

    status, _, after = _get(f"{server}/?t={TOKEN}")
    assert status == 200
    after_section = _extract_section(after, "teaser")

    assert after_section == before_section


# -- CSP nonce ----------------------------------------------------------------
#
# The module docstring's explicit security claim: a fresh per-response
# nonce, echoed verbatim into both the `Content-Security-Policy` header and
# the one `<script nonce="...">` tag, and never the review token reused as
# a nonce — different secrets, different lifetimes. Nothing below exercised
# this before; a future edit that reused `self.token` as the nonce, cached
# it across requests, or let the header drift from the tag would have
# passed the whole suite.


def _csp_nonce(headers: dict[str, str]) -> str:
    csp = headers["Content-Security-Policy"]
    match = re.search(r"script-src 'nonce-([^']+)'", csp)
    assert match, f"no script-src nonce in CSP header: {csp!r}"
    return match.group(1)


def test_the_csp_header_nonce_matches_the_scripts_own_nonce_attribute(
    project: Path, server: str
) -> None:
    _add_ab_pair(project)

    status, headers, body = _get(f"{server}/?t={TOKEN}")
    assert status == 200

    header_nonce = _csp_nonce(headers)
    tag_match = re.search(rb'<script nonce="([^"]+)">', body)
    assert tag_match, f"no <script nonce=...> tag in body:\n{body!r}"
    tag_nonce = tag_match.group(1).decode()

    assert header_nonce == tag_nonce
    assert header_nonce != TOKEN


def test_the_pages_stylesheet_is_allowed_by_its_own_csp(server: str) -> None:
    """`default-src 'self'` blocks an inline <style> as surely as an inline
    script, so a sheet with no matching `style-src` is dropped whole — the
    page drew unstyled, and a 1920px <video> ran off a phone's screen, with
    every test green (2026-09-18)."""
    status, headers, body = _get(f"{server}/?t={TOKEN}")
    assert status == 200

    csp = headers["Content-Security-Policy"]
    style_src = re.search(r"style-src 'nonce-([^']+)'", csp)
    assert style_src, f"no style-src nonce in CSP header: {csp!r}"
    tag = re.search(rb'<style nonce="([^"]+)">', body)
    assert tag, "the page's <style> carries no nonce"
    assert tag.group(1).decode() == style_src.group(1)
    assert style_src.group(1) != TOKEN


def test_two_successive_requests_get_different_csp_nonces(server: str) -> None:
    _, first_headers, _ = _get(f"{server}/?t={TOKEN}")
    _, second_headers, _ = _get(f"{server}/?t={TOKEN}")

    first_nonce = _csp_nonce(first_headers)
    second_nonce = _csp_nonce(second_headers)

    assert first_nonce != second_nonce
    assert first_nonce != TOKEN
    assert second_nonce != TOKEN


def test_an_ab_group_member_with_no_token_is_forbidden(project: Path, server: str) -> None:
    """The fold must not create a new unauthenticated path: every data-src,
    src and form-action on the page still needs the token, including a
    grouped item's own /media/ route."""
    _add_ab_pair(project)

    status, _, _ = _get(f"{server}/media/a")
    assert status == 403
