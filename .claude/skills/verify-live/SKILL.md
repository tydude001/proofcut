---
name: verify-live
description: Drive proofcut's web UI in a real headless browser over CDP — clicks with dwell and hit-testing, drags, viewport overflow probes, console capture, canvas readback. Use whenever a UI change has to be verified the way docs/plans/STUDIO.md requires, rather than through DOM stubs or the HTTP tests.
---

# verify-live

`tests/test_webui_http.py` speaks HTTP to a real socket and proves routing.
It cannot prove a gesture works. docs/plans/STUDIO.md's bar for every UI item is the
real project in a real browser, every click driven at **0ms and ~120ms**
dwell — a rule that exists because a fix once read green at 0ms and was dead
in the hand (HISTORY.md § The dwell-timing lesson).

`cdp.mjs` is that harness, kept here because it has been rebuilt from scratch
in more than one session. Node 24's global `WebSocket`, no dependencies.

## Run it

```sh
# 1. serve a project (a COPY of anything real — check it first with film-check)
#    Check the port first: a server another session left there answers the
#    goto below with ITS project, and the new one's bind error is only in its log.
ss -ltnp | grep :8793 && echo "8793 taken, pick another port"
uv run proofcut -C /path/to/proj web --port 8793

# 2. a browser to drive, left running between calls
~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell \
  --remote-debugging-port=9444 --headless --disable-gpu --no-sandbox \
  --window-size=1400,900 about:blank &

# 3. drive it — each call attaches to the same page, so state persists
node cdp.mjs goto http://127.0.0.1:8793/
node cdp.mjs eval '(() => document.querySelector("#truth-strip").textContent)()'
node cdp.mjs click "#finish-render" 120      # dwell in ms; 0 and ~120 both
node cdp.mjs dragxy 81 327 145 326 120       # press, move in steps, release
node cdp.mjs viewport 700 900                # resize; two sweeps (see below)
node cdp.mjs key "?" - 120                   # a real key press; `-` = no focus target
node cdp.mjs key Escape "#agent-prompt" 0    # ...or press it with focus in a field
node cdp.mjs console 3000                    # console errors for N ms, each with its url
node cdp.mjs shot out.png
```

`CDP_PORT` overrides 9444.

## Capturing the README screenshots

**Do not drive this by hand — run `scripts/capture_screenshots.py`.** It has
drifted three times, and the cause was the same each time: the theme seed, the
viewport and the demo project's state were re-derived from memory and one of
them got forgotten. The script is this section's prose turned into code, and
it ends with the check that would have caught every one of those rounds.

```sh
uv run python scripts/capture_screenshots.py          # build, serve, render, shoot
uv run python scripts/capture_screenshots.py --reuse  # keep the demo project
uv run python scripts/capture_screenshots.py --keep   # leave the page up to poke at
```

What it encodes, and what to keep true if you change it:

- **The theme is seeded before the page loads, never toggled after it.**
  `theme.js` is the one classic script in `<head>` and applies `data-theme`
  before paint, so a toggle afterwards is a repaint the two canvases only
  follow via its `proofcut:theme` event. Seeding means `localStorage`, which
  needs the origin, so it is goto, set, goto — `seed_dark`, which then
  asserts on `data-theme` rather than trusting the write.
- **Confirm the seed took, and then confirm the set agrees.** `#theme`
  reading `☾` says the attribute is set; only mean luma across the set says
  they read as *one theme*, which is `check_set` and is the check a recapture
  twice did not run. The shots are mostly dark b-roll on a mostly dark page,
  so a wide spread is a light capture wearing dark footage — HISTORY.md § The
  screenshots went back to dark.
- **Render once from Finish** (`#finish-render`). That one SSE stream fills
  both Finish's stage report *and* the Edit agent pane's completion card — the
  card is `handleRenderEvent`, not an agent run, so no `claude -p` is needed.
  It does not survive a reload, so the theme is seeded **before** rendering.
- **Frame is shot at its own content height, not at 900.** `fit_viewport`
  measures `#frame-view` and resizes twice, because the first resize reflows
  what it measured. Captured flat at 1400x900 the pane came out 44% dead
  black, which beside a full Edit shot reads as a different, emptier product
  — HISTORY.md § The screenshots stopped being captured by hand. A scroll
  container is measured by its `scrollHeight`: `#frame-rows` is one, so its
  box says nothing about how much is in it.
- **`check_timeline_width` is a regression test, not a capture step.** The
  repo has no JS test harness, so the assertion that the ruler matches
  `#track-lanes` is the only thing holding down HISTORY.md § The timeline
  re-measures when Edit is looked at — a render landing while Edit is hidden
  used to lay every lane out at `computePxPerSec`'s 800px fallback, and the
  render this script drives from Finish is exactly such an event. If it ever
  fails again, `timeline.js`'s `mode` listener is what regressed.
- **Edit's highlighted word is `.w.playing`, the playhead's, not `.w.sel`** —
  clicking a word to seek raises the `.selection-toolbar` over the transcript
  and Escape does not lower it (`refreshToolbar` hides on a null selection,
  and the only gesture that nulls one is a mousedown outside any `.w`). The
  script seeks the `<video>` directly, which leaves the playhead highlight and
  no toolbar.

Finish mode is no longer in the README — the Edit shot's agent pane already
draws the same Export-complete card off the same stream — so the script does
not shoot it. It still *drives* the render from Finish, because that is what
fills the Edit card. If it comes back, the two things the old capture had to
relearn: the player is not in the page until `Watch` is clicked, and
`#finish-view` is its own scroll container, so the report sits above the
picture at its own `scrollTop`, not the window's.

## What it will not do for you

- **`click` refuses a target it cannot hit.** It asserts
  `document.elementFromPoint` at the point it presses, because `el.click()`
  skips hit-testing and reads green on a control nobody can reach.
- **Whether a screenshot contains `<video>` is not settled** — black on
  2026-08-09, composited on 2026-08-19 and 2026-08-24, same binary. So a black
  capture proves nothing and a good-looking one proves nothing: `drawImage`
  into a canvas and read the pixels, and only after
  `!seeking && readyState >= 2`.
- **`viewport` reports two sweeps and you need both.** `overflowing` walks the
  page and skips anything inside a scroll container — without that skip every
  ruler tick and clip block is a finding, which is a probe that gets ignored.
  `scrollers` is each scroll container against its *own* `clientWidth`, which
  is the only way a pane that clips a value mid-word ever shows up. In Edit
  mode expect exactly one, `#track-lanes`.
- **`key` sends virtual key codes, and that is not cosmetic** — a code-less
  Escape reaches a JS listener but not Chrome's `<dialog>` close watcher, and
  reads as a bug in the page. Measurement in wiki `tooling.md` § Headless
  browser. It also takes a key NAME, so the transport is `key "Space"`; a
  literal `key " "` is silently a no-op, which reads as space-to-play being
  broken.
- **A lazy image is not a broken image.** Measure `naturalWidth` only after
  scrolling the element's *real* scroll parent; through the wrong one, 30
  perfectly good tiles read exactly like a route that 404s.

Every mechanic and its measurement: wiki `tooling.md` § Headless browser.
