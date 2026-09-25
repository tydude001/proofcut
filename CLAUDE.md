# proofcut

Architecture, stack decisions, and open questions live in [PLAN.md](docs/PLAN.md).
The build order and the rationale behind it is PLAN.md § Direction and
order. The competitor/dependency survey behind those decisions is in
[PRIOR-ART.md](docs/PRIOR-ART.md). The dated record of what shipped and what the
evidence said — including the first real video's findings — is
[HISTORY.md](docs/HISTORY.md). The Daydream parity plan — the product observed,
its design system, the feature map and its build order — is
[docs/plans/DAYDREAM.md](docs/plans/DAYDREAM.md). The Studio reshape — the
workspace reorganized around Home/Edit/Frame/Finish with direct manipulation
and the truth strip — is [docs/plans/STUDIO.md](docs/plans/STUDIO.md), which
supersedes docs/plans/DAYDREAM.md § Build order where they conflict. The
post-reshape polish plan — doctor, the demo project, manifest-aware undo, and
the rest of the works-for-anyone gap — is
[docs/plans/POLISH.md](docs/plans/POLISH.md); all seven of its steps shipped
2026-08-24 and each carries a pointer to the HISTORY.md section that records
it. **A closed plan lives in `docs/plans/` when the code cites it and in
`~/proofcut-work/archive/plans/` when it does not** — these three are cited from 100
comments across `src/`, `tests/` and `scripts/`, which is the only reason they
are still in the repo. The workspace redesign that followed — one tabbed rail
instead of two side columns, Frame
and Finish as list-and-detail, and the pane headers demoted out of the
chrome — is HISTORY.md § The workspace redesign; it has no plan document of
its own, because it was drawn as mockups, approved, and built in one pass.
The full command
walkthrough that was README.md's body is
[docs/MANUAL.md](docs/MANUAL.md) — moved verbatim 2026-08-19 when README.md
became a short newcomer-facing front door; nothing was deleted in the move.
The two-minute demo a stranger runs first is [docs/DEMO.md](docs/DEMO.md), on
media `scripts/make_demo.py` **generates** — the repo vendors none, so there
is no licence question and nothing to keep in step with an upstream. The three
directions after every queue closed, ranked, are [NEXT.md](docs/NEXT.md); the first
of them ran the same day and its evidence and failure queue are
[TRIAL.md](docs/TRIAL.md) — an agent cutting a video end to end, unattended,
scored. What a macOS/Windows port would take — the one Windows crash, the
Linux-shaped resolvers, and the melt/libass/magick measurements that have to
be redone per OS — is [docs/plans/PORTABILITY.md](docs/plans/PORTABILITY.md),
surveyed 2026-09-10. How a public repo gets seen — the recording, the flip,
the MCP directories beside one stranger's Mac run, Show HN, in that order,
with Show HN's wait for the Mac run time-boxed — is
[docs/plans/LAUNCH.md](docs/plans/LAUNCH.md), written 2026-09-11.
**The rename to `proofcut`** — why LAUNCH.md's "No rename" was wrong (Lucid
Software's own MCP server is the registry's first result for the word, and
it holds a live LUCID mark), the measured surface, the decisions, and the
steps a fresh session works — is
[docs/plans/RENAME.md](docs/plans/RENAME.md), written 2026-09-13; it has
landed, and HISTORY.md § The rename is its record. The records — HISTORY.md,
TRIAL.md, and DAYDREAM/POLISH/STUDIO — still say `lucid` by design, and so
does every citation of a heading that contains the word.
What proofcut needs before "cut with proofcut" is true of the essays and the
launch clip — the placed music cues, the loudness target, and the launch
clip's retime/eased-camera grammar as features, with MLT's retime and easing
measured — is [docs/plans/NATIVE.md](docs/plans/NATIVE.md), written
2026-09-14 off a native rebuild of Lambs/Longlegs v10.
How the repo says proofcut makes a whole film — the README, manual, demo and
listings that predate the finishing half, the third trial that measures an
agent doing it, why never "generate" or "from scratch", and why the name
stays — is [docs/plans/SHOWCASE.md](docs/plans/SHOWCASE.md), written
2026-09-15.
What the MCP surface costs its clients and what to change — the agent
panel's `--tools ""` loading all 92 definitions on every turn (measured
ten times the price of a `ping`), the ten descriptions Claude Code
truncates, the unbounded replies, progress for the long tools, the briefs
as prompts — is [docs/plans/MCP.md](docs/plans/MCP.md), written
2026-09-16 and built the same day but for resources, which it defers;
HISTORY.md § The MCP surface, rebuilt for deferred loading is the record.
How a stranger gets from a bare machine to a checked render — doctor's
advice corrected, a no-sudo Linux installer that fills only doctor's ✗s,
where it lives, the PyPI name, and why Linux goes ahead of LAUNCH.md's
install-script gate — is [docs/plans/INSTALL.md](docs/plans/INSTALL.md),
written 2026-09-16 off PRIOR-ART.md's FableCut read and built for Linux the
same day (HISTORY.md § `proofcut setup`, built). It measured **Shotcut's
portable Linux melt drawing with no X server**, where both distro MLTs need
`xvfb-run`.
How one agent per project would be enforced — a directory lock taken at the
first write-capable MCP call, a heartbeat counter, auto-break only on a dead
pid or a new boot, `proofcut unlock` for the rest, `_manifest_stamp` kept as
the backstop — is [docs/plans/PROJECT-LOCK.md](docs/plans/PROJECT-LOCK.md),
written 2026-09-21 and built 2026-09-22 (`projectlock.py`; HISTORY.md § The
project lock, built). **Only `serve()` turns it on**, so an in-process call
to a tool function takes no lock, and a SIGTERM handler must never
`sys.exit`: the stdio server then hangs rather than dies.
How the 13-minute suite became two — measured, then xdist against a serial
control — is [docs/plans/SUITE-SPEED.md](docs/plans/SUITE-SPEED.md), built
2026-09-17: **run it as `QT_QPA_PLATFORM=offscreen pytest -n auto`, about
2 minutes**, and CI runs the same flag since 2026-09-24 (HISTORY.md § The
suite in two minutes, and the flatpak launch race).
What proofcut needs so an agent's re-cut of the launch clip comes out as the
approved `clip-v6` — Tyler picked it at all nine moments of B7's native cut —
is [docs/plans/RECUT.md](docs/plans/RECUT.md), written 2026-09-18: events
for the bed and cues, a tail with no cue lane, a duck that hears every lane,
trimmable sounds, levelled insets, the lower third's fonts, and a dissolve.
Whether proofcut runs on local models alone — the four roles that already
do (whisper, the VLM, faces, TTS), the director that is still Claude, what
a local one would need from `PROOFCUT_AGENT_BIN` and the tool surface, and
the hardware — is [docs/plans/LOCAL.md](docs/plans/LOCAL.md), written
2026-09-18; the director half is **measured on two briefs**, where
Qwen3.6-35B-A3B passed all four runs' checks — and left a stutter the checks
cannot see in two of the four films; on the real-footage brief it scored
10/10 once the map named `hear`, and by ear **failed** — it heard the
hidden retake, explained both flags away, and ran 93.7 s against 45
(LOCAL.md § The rerun with `hear` on the map). **A trial score on that
brief is not a clean film until the render's head is heard.**
What a check for frozen and silent spans in a render would need — measured
on two films, where 13 of 14 frozen spans were cards, so the finding is a
span nothing explains — is
[docs/plans/RENDER-CHECKS.md](docs/plans/RENDER-CHECKS.md), written
2026-09-20 and unbuilt — **its positive control failed the same day**, so the op
as designed should not be built (§ The positive control, run); the smaller holds
read that survives is designed there and waits on a trial that needs it.
Undoing an agent's turn — `undo --steps N [--plan]`, and why `snapshot`'s
once-per-instance rule stays — is [docs/plans/GROUPED-UNDO.md](docs/plans/GROUPED-UNDO.md),
written and built 2026-09-20 but for the panel's button.
Whether a person's own b-roll picks could pick the next ones — checked and
not built 2026-09-20 — is [docs/plans/PICKS-PRIOR.md](docs/plans/PICKS-PRIOR.md).
Whether proofcut wants a landing page (GitHub Pages) — not yet, because a site
converts traffic and makes none; it waits on the launch recording or a paid
tier — is [docs/plans/SITE.md](docs/plans/SITE.md), written 2026-09-24.
A store outside every project for what whisper, the vision model and the
face detector say about a source, keyed by the media's content and what the
model was given, so a second project on the same shoot or a `--fresh` trial
pays no model time — the efficiency review's largest finding — is
[docs/plans/MODEL-CACHE.md](docs/plans/MODEL-CACHE.md), written 2026-09-24,
designed and not built; its controls are stated there.
**Competitor research and anything copied from another product follows
docs/plans/DAYDREAM.md § Copyright, the DMCA, and this work**: a research
download stays private and is deleted once its notes are checked, never commit
another product's frames, templates or copy, copy the idea and never the
expression, and proofcut never fetches media it was not handed. Open-item status lives in the wiki, not here. **This repo is public: a
goodsometimes video's production record (versions, renders, creative calls,
release state) goes in `goodsometimes/ideas/<video>.md`**, and HISTORY.md
keeps only what the film showed about proofcut. The rule and the reason are in
wiki `projects.md`.

**Every document but this one, README.md, CONTRIBUTING.md and SECURITY.md
lives under `docs/`** — the five that sat at the root moved there
2026-09-13, so the public tree's top level is only what a tool requires
there (`server.json` and `glama.json` included). Comments, tests and the
docs themselves cite them by bare name (`HISTORY.md § …`), and that is
deliberate: the names are unique in the repo, and a thousand path rewrites
would bury any real change. A Markdown *link* is a path and must resolve.

**The two sections below are headlines; each rule's mechanism, measurement
and HISTORY.md citation is in [TRAPS.md](docs/TRAPS.md) under the heading its
arrow names.** Read that section before changing code a rule covers — a
headline says what to do and never why, and the why is where the exceptions
are. A new rule goes into TRAPS.md in full and here as one line; this file is
held under Claude Code's instruction-size limit, which is why it was split
(2026-09-24). A comment citing a rule as "CLAUDE.md" means TRAPS.md.

## Things that will bite you

Each is a case where the training prior is confidently wrong. Check the
installed package or the upstream repo, not your memory.

- **`proofcut doctor` probes every binary below and prints the fix under each
  ✗.** → TRAPS.md § `proofcut doctor`
- **`proofcut setup` (`install.py`; Linux, Windows and both Macs —
  `deps.setup_installs_here`, which is also where a CPU is spelled the way
  `PINS` keys it) installs what doctor crosses and nothing doctor passed, and
  it is CLI-only on purpose** → TRAPS.md § `proofcut setup`
  - **Every download is pinned by URL and SHA-256 and bumped by hand.**
  - **melt and auto-editor resolve setup's folder (`deps.py`) ahead of PATH;
    ffmpeg is a `~/.local/bin` link.**
  - **A binary is kept only if `ldd` finds its libraries.**
  - **Everything added is recorded — links, folders, the uv tool, the Python
    uv downloaded for it — and `--uninstall` removes exactly that**
  - **Off Linux, each route grew out of its test kit's install half, and the
    kits now call setup instead of carrying one**
- **The MCP SDK is v2. `FastMCP` no longer exists** → TRAPS.md § The MCP SDK
  is v2
  - **`MCPServer.run()`'s `transport` argument is `Literal["stdio", "sse",
    "streamable-http"]`, verified against the installed 2.0.0**
- **auto-editor is Nim, and PyPI is stale.** → TRAPS.md § auto-editor
  - **31.x gates multi-*source* timelines behind a paid key, and the render
    path degrades to 720x576 with a warning and **exit 0** rather than
    failing.**
  - **That gate is auto-editor's, not this box's — don't design around it.**
- **Whisper is a subprocess.** → TRAPS.md § Whisper is a subprocess
  - **Both passes hallucinate, and the two rules that catch it are not one
    rule.**
  - **`describe`'s vision model is the same shape, and proofcut's venv has no
    torch either**
- **An MCP tool result can carry an image, and `claude -p` puts it in front of
  the model** → TRAPS.md § Images in tool results
  - **A tool returning one is annotated `-> Any`, and the obvious annotation
    silently returns no picture.**
  - **All four sheets return the bytes; `reframe_sheet` unpaged is the one
    path, deliberately.**
- **`claude -p` stream-json output requires `--verbose`, and the
  allow/disallow-tools flags do not gate built-in tools.** → TRAPS.md §
  `claude -p` and the agent panel
  - **`--tools` is `ToolSearch`, never `""` — `webui._AGENT_TOOLS`, which
    `agent_trial.py` imports.**
  - **A long tool reports progress through `proofcut.progress`, a context
    variable — never a callback argument on the op.**
  - **The trial briefs and the `cut`/`film`/`review` prompts are one text,
    `proofcut.briefs`**
  - **An unbound `proofcut mcp` started inside a project binds to it**
  - **A generated MCP config's `command` is resolved by `claude` against *its*
    PATH, never by proofcut — so it names `sys.executable` and `-m
    proofcut.cli`, never the string `proofcut`.**
  - **`--model` bakes into `claude -p`'s argv at spawn, so there is no way to
    hot-swap a running turn's model.**
- **OTIO's edit algorithms are C++ only.** → TRAPS.md § OTIO's edit algorithms
- **The OTIO metadata key is written `"proofcut"` and read as either, through
  `timeline.proofcut_metadata` and nothing else.** → TRAPS.md § The OTIO
  metadata key
  - **The manifest name is the opposite rule: a `lucid.json` is never read.**
- **The MCP server's own name is stated in five places, and they move
  together** → TRAPS.md § The MCP server's name

## Conventions

- **Scratch has one root, `~/proofcut-work`** → TRAPS.md § Scratch directories
- Every MCP tool gets a matching `proofcut` CLI subcommand. → TRAPS.md § CLI
  parity and tool registration
  - **Register tools with `@_tool()`, never `@mcp.tool()`**
  - **`_tool()` also refuses a tool with no row in `server._ANNOTATIONS`**
  - **A derivation never holds what it creates (`derives=`)**
  - **`path` is optional everywhere (`str | None = None`) and defaults to the
    bound project**
  - **`list_media` is what hands an unattended agent source paths**
- Tests exercise the real server process over stdio
  (`tests/test_server_stdio.py`), not just the tool functions. → TRAPS.md §
  Tests speak to real processes
- **The web UI (`webui.py`, `web/`) is a third client, never a third
  implementation.** → TRAPS.md § The web UI
  - **Off loopback it is opt-in, and `webui.remote_policy` is the only place
    that decision is made**
  - **`_revision` watches the manifest as well as `project.otio`**
  - **Timeline lanes are projections of one `Edit`, not tracks. Never draw a
    lane `export` cannot produce**
  - **The palette lives once, as `light-dark()` in `app.css`'s `:root`, and JS
    must never read a colour token.**
  - **The picture lane draws `timeline_view`'s `shots` — the projection
    *already through `mlt.plan_picture`* — never `build_shots` directly.**
  - **`#frame` is the project canvas, and media is *placed* in it, never
    fitted.**
  - **Verify the picture layer by canvas readback, never by screenshot**
  - **A browser pass driven at CDP's default zero dwell is not a pass**
  - **An author `display:` rule outranks the UA's `[hidden] { display: none
    }`, so `el.hidden = true` does nothing on its own.**
  - **A floating panel is clamped by `dom.js`'s `clampFloating`, and there is
    exactly one copy.**
  - **The chrome states facts and labels controls; it does not explain the
    build.**
  - **The rail (`#rail-pane`) is ONE pane with three tab panels, not three
    columns**
  - **The `?` sheet is hand-typed HTML — never generated — grouped by which
    pane owns each binding**
  - **A `<video>` that cannot decode fires one contentless `error` and shows
    black**
- **`proofcut review serve` (`reviewserver.py`) is a fourth client, on purpose
  not `webui.py`'s guard.** → TRAPS.md § `proofcut review serve`
- **`proofcut mcp` can serve over HTTP, and the guard is loopback+Host, not a
  token — `webui.py`'s model, not `reviewserver.py`'s.** → TRAPS.md § MCP over
  HTTP
  - **A wildcard bind's own host string is not a client identity**
- **`proofcut web --root DIR` serves a picker over many projects, but the
  process still binds to exactly one.** → TRAPS.md § The multi-project picker
  - **A picker that raises on one broken project hides every other one.**
  - **`Path.is_dir()` follows symlinks, and open-time confinement is too late
    for a listing that already leaked one**
- **Anything taking a word index echoes the words it resolved to, plus the
  three either side.** → TRAPS.md § Word indices echo their neighbours
- **Cite roadmap items by name, never by number** → TRAPS.md § Citing roadmap
  items
- **The README screenshots are regenerated by
  `scripts/capture_screenshots.py`, never captured by hand** → TRAPS.md § The
  README screenshots
- **An unattended agent edit is measured by `scripts/agent_trial.py`, which
  imports the panel's flags from `webui.py` rather than restating them** →
  TRAPS.md § Agent trials
  - **`--source` runs it over real footage, and the material is the only thing
    it moves**
- `ruff check` is the lint gate. **Never run `ruff format`** → TRAPS.md § Lint
- **CI runs the suite on Linux, macOS and Windows, so a test measuring a Linux
  mechanism pins `sys.platform` to `linux`** → TRAPS.md § CI on three OSes
  - **A fake binary goes through `tests/stubs.py`'s `write_stub`, never a `#!`
    script**
  - **A stock Windows cannot create a directory past 248 characters**
- **A reachable identifier in the docs is elided, never swapped for a
  plausible one.** → TRAPS.md § Eliding reachable identifiers
  - **That rule now binds the history too: it was rewritten with `git
    filter-repo` on 2026-09-10**
- **The version is a hand-typed literal in six places and is bumped
  deliberately, never derived.** → TRAPS.md § The version
  - **A GitHub Release is a hand-written launch post, not a per-tag note, so
    it does not follow a bump**
- **A snapshot is a *pair* — `N.otio` + `N.manifest.json` — and
  `Project.write_manifest` takes one by default.** → TRAPS.md § Snapshots and
  undo
  - **`snapshot()` fires at most once per `Project` instance**
- **`Project.open` refuses an old manifest and must never migrate one** →
  TRAPS.md § Schema migration
  - **An additive *optional* key does not bump**
- **A footage description indexes the source, so no edit can invalidate one**
  → TRAPS.md § Footage descriptions and pinned cues
  - **A cue's `src_start` pins the in-point: a pinned shot refuses rather than
    rewinds.**
  - **A shot's addressing clip is not its footage.**
  - **A description does not choose the clip — `synopsis` does, and proofcut
    does not choose at all.**
- **A cut cannot invalidate a cue and can still orphan one, and `build_shots`
  refuses the whole projection on a single orphan.** → TRAPS.md § Orphaned
  cues, tails, holds and the music bed
  - **Read that list for the cue nearest the head first.**
  - **A bumper or end card is project state (`TAIL_KEY`), and a derivation
    inherits nothing — it reports `tail_dropped`.**
  - **`vo_extend` is built, and it is the one op authorized to grow `Edit`
    rather than only cut it.**
  - **A cold open (`HEAD_KEY`) and a film-audio hold (`HOLDS_KEY`) are
    `_is_layered`'s sixth and seventh triggers, each landed in the same commit
    as the writer support it protects**
  - **The A2 music bed is project state too (`MUSIC_KEY`), and no field in it
    is a timeline second or a frame count**
- Resolve media through `media.media_path()`, never `root / clip["media"]`. →
  TRAPS.md § Resolving media
  - **The preview resolves through `media.preview_path()`, and that split is
    the containment.**
  - **`GET /api/output` is the only route that serves `renders/`, and it
    resolves through `renderlog.last` — never the manifest and never
    `preview_path()`.**
  - **A Render click on unchanged stamps reuses the last web run's file and
    runs only the checks; a burn's stamp carries the lexicon.**
  - **A thumbnail is a preview artifact and keeps the same containment rather
    than adding a caller to it.**
- **Trust a transcript's word order, never its word durations.** → TRAPS.md §
  Transcript word timing
  - **A retake seam also *adds* words**
- **`vfr` is recorded at import and reported, never acted on** → TRAPS.md §
  VFR
- **A clip's `duration` is its container's, which ends with the longer stream,
  so no span may reach past `_timeline_bound`** → TRAPS.md § A clip's duration
- **A frame count comes from `autoeditor.frame_layout`, never from the
  duration.** → TRAPS.md § Frame counts
  - **Read back, `out` is the last frame *index*, so a range's exclusive end
    is `out + 1`**
- **`melt` is inside the Kdenlive flatpak, and that flatpak cannot see
  `/tmp`.** → TRAPS.md § melt and the flatpak
  - **Concurrent flatpak launches can lose a startup race**
  - **Every melt `subprocess.run` passes `stdin=subprocess.DEVNULL`.**
  - **A melt that dies of SIGSEGV or SIGBUS is run again, never of SIGKILL.**
  - **A *failed* render's staging directory survives on purpose, and
    `sweep_scratch` drops it after `SCRATCH_RETENTION_DAYS`.**
  - **`WAYLAND_DISPLAY` alone is not a display.**
- **A caption's look is project state (`caption_style`), and ASS is never
  written by hand** → TRAPS.md § Captions
  - **`caption_style` says what a burn *would* draw, never that one
    happened.**
  - **The font named in a style may not be installed**
  - **A caption reveal ends each alpha channel at the style's own value, never
    `\alpha&H00&`**
  - **`lexicon.json`'s `hear` table is what captions print**
- **Cards rasterise through `magick`, and the size knob goes *before* the
  input.** → TRAPS.md § Cards
  - **A wrap is measured or there is no wrap**
  - **Per-run `<tspan>`s eat the whitespace between them**
  - **A per-aspect layout is a variant *file*, resolved from the canvas —
    never a second template name.**
- **An overlay is a card with no background (`graphics.is_overlay`), drawn
  over the film by `OVERLAYS_KEY` — `_is_layered`'s ninth trigger — and it
  goes over nothing else.** → TRAPS.md § Overlays
- **An animated graphic is a web page a headless browser captures
  (`browser.py`, `motion.py`), placed by an overlay record's `graphic` as
  three ordinary overlay pieces: intro, hold, outro.** → TRAPS.md § Animated
  graphics
  - **`browser.DETERMINISTIC_FLAGS` are the capture, not tuning**
  - **Ubuntu 23.10+ refuses Chrome for Testing its sandbox**
  - **The page is served, never opened**
  - **Every piece is exactly its phase's length, because `qimage` loops a
    frame pattern**
  - **A capture is stamped with the page's bytes, the canvas and the rate**
- **A still (`stills.py`) is made upright once, at `image_add`, and a sticker
  is a canvas-sized PNG with a box** → TRAPS.md § Stills and stickers
  - **`mlt.overlay_keys` is the only statement of an overlay's motion**
- **A one-shot sound (`SOUNDS_KEY`, `_is_layered`'s tenth trigger) never plays
  its own file — it plays a padded copy from `cache/sounds/`.** → TRAPS.md §
  One-shot sounds
  - **A sound's `src_in`/`src_out` slice is decoded, never the file then cut**
- **A retime (`RETIME_KEY`, `_is_layered`'s eleventh trigger) is a warp from
  render time to Edit time, and only the edit and picture lanes are remapped**
  → TRAPS.md § Retime
  - **Never `~` in a `time_map`**
  - **Keys on a remapped chain count render frames**
  - **Every map ends one key past its entry's last frame**
- **An inset (`INSETS_KEY`, `_is_layered`'s twelfth trigger) is its own track,
  never a nested tractor** → TRAPS.md § Insets
  - **The rect filter hangs on the inset's PLAYLIST**
  - **Fade and dim are `brightness` alpha**
  - **Judge an edge in a yuv420p render by luma**
  - **Lossless-RGB sources drawn smaller than themselves shift colour**
- **A dissolve (`DISSOLVES_KEY`, `_is_layered`'s thirteenth trigger) is the
  incoming clip's pre-roll on its own track, never two overlapping Edit
  entries** → TRAPS.md § Dissolves
- **A cue's crossfade and punch (`dissolve`/`punch` on the cue, `cue_set`)
  ride the writer's existing pieces, and where each sits is the mechanism.** →
  TRAPS.md § Cue crossfades and punches
- **With no cues, a head or tail goes on the Edit's own track** → TRAPS.md §
  Heads and tails with no cues
- **A channel preset pack is a snapshot, never a live reference to a sibling
  repo's file.** → TRAPS.md § Channel preset packs
  - **A font that draws correctly and a font that is vendored are two
    different findings, and only one of them refuses.**
  - **Safe zones are report-only, on `SCENE_THRESHOLD`'s own precedent.**
- **Footage follows a canvas change by cropping, and the crop is a rect in
  *source* pixels stored as asked** → TRAPS.md § Reframing
  - **A sheet row is a window shown, not a placement**
  - **A slide drawn at exactly 1:1 snaps to whole pixels.**
  - **A window's `interp` is `true` or an easing name, and `event` rides
    beside `src_start` as provenance.**
  - **A window can hold a second rect (`pane`), and then it draws as a stacked
    split**
  - **A window can be blur-filled (`fill: "blur"`)**
  - **An export preset never sets the canvas — `tiktok-reels` *checks* it and
    refuses.**
  - **A brightness bbox answers "where is the bright part", never "where is
    the frame."**
  - **`reframe_detect` proposes and never frames**
- **A clip being registered is not a clip being on the timeline, and
  `timeline_view` answers for one anyway — read `off_timeline`.** → TRAPS.md §
  `off_timeline`
- Anything that emits times *for playback* maps through the edit, never
  straight off the transcript. → TRAPS.md § Playback times
  - **Segments are half-open `[start, end)`, and that is wrong for exactly one
    thing: a zero-width word.**
  - **Two clocks, once a head is configured: render time = Edit time +
    `head_seconds`.**
- **`Edit`'s addressing reads a cached `_SpanIndex`, so never mutate
  `edit.segments` in place** → TRAPS.md § `Edit`'s span index
- **`Edit` never stored what it removed** → TRAPS.md § What `Edit` removed
  - **A dogfood project can be the wrong cut while every check passes.**
