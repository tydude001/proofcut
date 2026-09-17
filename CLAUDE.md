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
How the 13-minute suite became two — measured, then xdist against a serial
control — is [docs/plans/SUITE-SPEED.md](docs/plans/SUITE-SPEED.md), built
2026-09-17: **run it as `QT_QPA_PLATFORM=offscreen pytest -n auto`, about
2 minutes**, and CI stays serial (HISTORY.md § The suite in two minutes, and
the flatpak launch race).
Open-item status lives in the wiki, not here. **This repo is public: a
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

## Things that will bite you

Each is a case where the training prior is confidently wrong. Check the
installed package or the upstream repo, not your memory.

**`proofcut doctor` probes every binary below and prints the fix under each ✗.**
Three rules it holds to and anything added to it must: judge melt by its
`-version` banner and never its exit code (`picture.MELT_BANNER`, which
`melt_command` also holds every PATH and bundle candidate to — Windows ships
WiX's `melt.EXE`, and a real one names itself `melt.exe`); *run* whisper rather than find it
(a venv that has lost torch resolves fine and dies minutes into a job); and
never print the TTS voice path — a voice is somebody's recorded speech. An
absent optional capability is "unavailable", never a failure, and never moves
`ok`. **It also lists every `LUCID_*` variable still set, beside its
`PROOFCUT_*` name** (`legacy_env`) — names, never values, and a note, never a
✗: no resolver reads the old name, so a stale `60-lucid.conf` otherwise loses what it
configured — face detection, say — at exit 0. HISTORY.md § `lucid doctor`.

**`proofcut setup` (`install.py`; Linux, Windows and Intel Macs —
`deps.setup_installs_here`, Apple silicon refused until a person's report)
installs what doctor crosses and nothing doctor passed, and it is CLI-only on
purpose** — never register it as
a tool; an agent must not start a 2 GB download or change PATH. Four rules it
holds to, each measured in a clean container (HISTORY.md § `proofcut setup`,
built; docs/plans/INSTALL.md):
- **Every download is pinned by URL and SHA-256 and bumped by hand.** BtbN
  deletes its dated daily builds after weeks and keeps month-ends, so the
  ffmpeg pin is a month-end build and never `latest`.
- **melt and auto-editor resolve setup's folder (`deps.py`) ahead of PATH;
  ffmpeg is a `~/.local/bin` link.** A melt link loses to Fedora's
  `/usr/bin/mlt-melt`, which draws nothing headless, and whisper's own
  `audio.py` calls `ffmpeg` bare, so neither half can be the other's shape.
- **A binary is kept only if `ldd` finds its libraries.** Shotcut's melt
  needs ordinary desktop libraries, and auto-editor needs `libgomp.so.1`,
  which a bare Ubuntu lacks. Doctor called that auto-editor ✓, with the
  loader's refusal printed as its version, so a version is now a number from
  an exit 0.
- **Everything added is recorded — links, folders, the uv tool, the Python uv
  downloaded for it — and `--uninstall` removes exactly that**; a test
  compares the home's listing before and after.
- **Off Linux, each route is its test kit's install half, pins included, and
  the kits are not yet rewired to call setup** — they are the instrument the
  tester posts hand out. Windows cannot symlink without Developer Mode, so
  its ffmpeg/ffprobe `.exe`s are *moved* into `~/.local/bin` and recorded by
  SHA-256; uninstall removes one only while it still hashes the same.
  `setup-demo.yml` (`scripts/setup_trial.py`) is the only thing that runs
  either route: judge a change there by that job's `trial_check`, never by
  the unit tests, which fake the OS. HISTORY.md § `proofcut setup` on
  Windows and an Intel Mac.

- **The MCP SDK is v2. `FastMCP` no longer exists** — it is `MCPServer`, from
  `mcp.server` (and there is no `mcp.server.fastmcp` module). Training priors
  overwhelmingly say `FastMCP`; check the installed package before writing
  server code, not your memory of the API.
  - **`MCPServer.run()`'s `transport` argument is `Literal["stdio", "sse",
    "streamable-http"]`, verified against the installed 2.0.0** — but calling
    it with `transport="streamable-http"` builds the Starlette app and the
    uvicorn `Config` internally, with no injection point for middleware and no
    way to learn a `port=0` ephemeral port before it blocks. `proofcut mcp
    --transport http` builds `streamable_http_app()`'s pieces by hand instead
    — a Starlette app with its lifespan already wired — because the loopback
    guard needs to wrap the app and the bound port needs to be knowable before
    it blocks. HISTORY.md § MCP over HTTP, built.
- **auto-editor is Nim, and PyPI is stale.** `pip install auto-editor` gets
  29.3.1; upstream ships 31.x. There is no Python API — shell out to the binary,
  like ffmpeg.
  - **31.x gates multi-*source* timelines behind a paid key, and the render
    path degrades to 720x576 with a warning and **exit 0** rather than
    failing.** Track count is free; two distinct `src` files is the wall.
    HISTORY.md § The multi-track costing spike.
  - **That gate is auto-editor's, not this box's — don't design around it.**
    **Single-source goes through auto-editor; multi-source generates MLT and
    renders through `melt`**, which has no source-count gate. `melt`'s own
    three traps all produce output rather than an error: HISTORY.md § 4.
    PLAN.md § The layered timeline.
  - The writer is `mlt.py`, and `export` picks it **from the project** — a cue
    table, a second clip_id on the edit, or a `canvas` override — never from an
    argument, because the failure it routes around is silent. Positions in it are frame
    integers, `out` is the last frame *index*, and every declared length is
    read back off the finished document by `mlt.declared_frames` before it is
    returned: melt renders to the longest one it finds. HISTORY.md § The MLT
    writer.
    - **A container with two audio streams renders its first mic and drops
      the second, at exit 0** — the Edit lane's producer carries no
      `audio_index` (the writer emits one only as `-1`, to silence a picture
      node), so MLT picks, and it picks the first. Every check stays clean:
      they compare the render against the timeline, and the timeline never
      knew there was a second stream. **And `audio_index` is the container's
      absolute stream index, not the audio ordinal** — with video at 0 the
      first mic is `1` and the second is `2`, while ffmpeg's own `-map 0:a:1`
      means the second audio. The two numberings agree exactly on an
      audio-only file, which is every fixture anyone writes first. Measured by
      Goertzel readback of a real melt render. PLAN.md § The co-hosted
      recording.
      - **That trap is unreachable from a project now: `import` refuses a
        multi-stream container.** `--mix` sums, `--audio-stream k` keeps one
        (ffmpeg's audio ordinal, *not* `audio_index`), and either writes a
        derived copy to `cache/mixed/` that `media_path()` prefers the way it
        prefers `attenuated` — so **nothing downstream of import ever chooses
        a stream**, and a caller that does is the whole hole.
        `original_media_path` keeps the mixdown too: the untouched original
        of a two-mic container *is* the mixdown. **Judge any of it by tone
        power, never by counting streams** — auto-editor's half of the trap
        passes both tracks through, so the file holds two and everything that
        decodes it takes the first. HISTORY.md § The two mics survive import.
        - **A stream no decoder reads is not a mic.** An iPhone recording
          Spatial Audio holds AAC at audio 0 and `apple_apac` at audio 1,
          which no ffmpeg decodes, so `--mix` was an ffmpeg error and the
          refusal asked a question with no right answer. `probe` records
          `undecodable_audio` off `ffmpeg -codecs`, and import counts only
          the readable streams: one is imported unflagged as a pick, found
          wherever it sits rather than assumed to be audio 0 — and the
          phone's video is stream index 2, not 0. HISTORY.md § The phone's Spatial Audio track.
        - **`_reel_media`'s key tuple *is* `media_path()`'s preference
          chain** (`ops.py`, and again in `reel(plan=True)`'s `would_link`);
          a key in one and not the other hands the derived project a path
          with nothing at it. New import guards go **behind** the source
          dedup, or a retried import raises about a choice taken days ago.
          **`"stripped"` (chapter/data-track stripping, `media_path()`'s
          `attenuated → mixed → stripped → media` order) rides both tuples as
          of the same commit that added the preference** — the rule this
          bullet states, applied rather than just cited. A key added to one
          without the other is invisible until a reel of a chaptered clip
          resolves a path with nothing at it. HISTORY.md § Import strips a
          chapter list.
        - **A long server message is a control that spends the pane's
          height** — this refusal renders 194px in the assets pane and took
          `#assets-list` from 129px to 0, hence `.asset-status`'s 60px cap.
          The pane complies with the refusal rather than only printing it:
          `media.MultiAudioError` carries `streams`, so nothing there
          string-matches a sentence.
        - **Both path resolvers prefer the mixdown, so the mics are
          reachable only through `media.container_path`** — one caller,
          `ops.attribute_speakers`, and that is the containment. Anything
          else asking for "the original file" wants the mixdown; asking
          `media_path()` for the mics compares it against itself and
          separates nobody, at exit 0.
        - **More than two channels are downmixed at import too, into the
          same `mixed` key** (`media.downmix_to_stereo`, recorded as
          `downmix`). MLT plays the *first two* of six unlabelled channels,
          so a clip cut from a 5.1 rip renders its fronts and never its
          centre — the dialogue — at exit 0; a tagged 5.1 stream downmixes in
          melt, but 9 dB off ffmpeg's, so a gain ffmpeg measured is wrong for
          what melt plays. A project imported before this has no copy, and
          import's dedup means only a fresh import makes one. HISTORY.md § The
          Lambs/Longlegs native rebuild.
          - **`attribute_speakers` is at chance on simultaneous speech**,
            measured on one synthetic voice, so `speakers.MARGIN_DB` is a
            reported default and never a threshold to trust — `apply` is
            off by `reframe_detect`'s precedent and keeps a label it
            refuses to replace. HISTORY.md § Speaker attribution, built.
      - **`transcript.Word` has a `speaker`, and it is a label, never an
        address** — `(clip_id, word_index)` still resolves every cue,
        description, mark, music anchor and caption. Additive and optional,
        so no schema bump, and **`Word.as_dict` omits it when unset**: an
        `asdict` would stamp `"speaker": null` onto every word of every
        transcript and rewrite each file on the next save for no change.
- **Whisper is a subprocess.** Do not `import whisper` —
  go through `asr.transcribe()`, which resolves the binary via `PROOFCUT_WHISPER`
  → PATH, and nothing after. **No resolver in `src/` may fall back into a
  path under `~/projects`** — that is another repo's layout on one machine,
  and printing it is how `doctor` told strangers about it; this box's installs
  ride `~/.local/bin/whisper` and `~/.config/environment.d/60-proofcut.conf`
  instead (HISTORY.md § The repo, readied for strangers). It is openai-whisper, not faster-whisper, whatever
  PLAN.md's older tables say. Why it is not an import: `asr.py`'s docstring.
  - **Both passes hallucinate, and the two rules that catch it are not one
    rule.** `asr.clean` is the entry point and the reason it exists is that the
    windowed path called `_drop_stacked` inline for months while the ingest
    path had nothing. Identical-instant (`STACKED`) and dense-cluster
    (`CLUSTER_WINDOW`/`CLUSTER_WORDS`) each miss what the other catches — on
    the scale spike's own artifact the first drops 3 of 8. **The dense rule
    counts words in a window and never scores a rate**: real speech reaches 50
    w/s over three words, because whisper's durations are not to be trusted.
    Every drop is reported as `hallucinated_words`, never only applied.
    HISTORY.md § The ingest path's hallucination guard.
  - **`describe`'s vision model is the same shape, and proofcut's venv has no
    torch either** — `PROOFCUT_VLM` names an *interpreter*, and `_vlm_worker.py`
    ships in the package to be run by it, never imported. HISTORY.md
    § `describe`.
- **An MCP tool result can carry an image, and `claude -p` puts it in front
  of the model** — both halves measured 2026-08-24, the second under the agent
  panel's own `--tools ""`. `ImageContent`, via
  `mcp.server.mcpserver.utilities.types.Image`. **A reading is an opinion, not
  a check**: `reframe_sheet`'s precedent, and nothing in proofcut gates on what a
  model said it saw. PLAN.md § The agent contact sheet.
  - **A tool returning one is annotated `-> Any`, and the obvious annotation
    silently returns no picture.** A concrete return type makes the SDK build
    an output schema and validate an `Image` against it, so `-> list[Any]` and
    `-> list[ContentBlock]` both answer `is_error` from a correct body. Assert
    on the raw `CallToolResult`: `test_server_stdio`'s `Client` reads
    `content[0]`, so a tool returning only its table passes.
  - **All four sheets return the bytes; `reframe_sheet` unpaged is the one
    path, deliberately.** The agent panel's `--tools ToolSearch` means no Read, so a
    path is unreachable there. Each answers a different question:
    `shot_sheet` is the *timeline's* picture track, `footage_sheet` browses
    one registered clip's own *source* and needs no edit, cues or transcript,
    `contact_sheet` is the first look at one clip's *head*, `reframe_sheet`
    reviews framing windows. HISTORY.md § The two sheets an agent could not
    see.
    - **`reframe_sheet` pages by ROW** — a row is one window, so a page never
      splits the thing being judged — and slices **before** decoding or face
      probing, so a page is cheaper and not merely smaller. `row`/`count` stay
      project-wide; unpaged (`per_page=None`) is the whole project as a PNG
      for a person, is what `webui.py` calls, and is 4.6x past the long edge
      vision keeps, so it must never be what an agent gets.
    - **A tile's label is drawn at whatever scale its picture is at**, so
      `_sheet_tile` on a full-resolution frame plus a downscaling `montage`
      sets the type at a fifth of its size — seven perfect frames, seven
      unreadable captions, every test green. Pass `width=`, and **judge a new
      sheet by reading it back**, never by magick's exit code.
    - **`reframe_sheet` writes flat into `cache/sheets/` and sweeps FILES,
      never the tree**: every other sheet keeps a subdirectory of it,
      `SHEET_FRAMES_DIR` included, and `webui._send_reframe_tile` serves from
      exactly that flat level.
    - **`import_media` asks `contact_sheet` for no montage** — an import
      reply cannot carry an image. `sheet: null` is asked-and-nothing-to-draw;
      the key *absent* is nobody asked (`finish_report`'s `framing`).
    - **Cache the frame, never the tile**: a frame is `(asset, source second)`,
      edit- *and* sheet-invariant, so both sheets share `SHEET_FRAMES_DIR`; a
      tile carries a label and a timeline second any cut moves. Downscaled,
      width in the filename, JPEG out — HISTORY.md § The shot sheet.
    - **`footage_sheet` defaults to a fixed interval; `scenes` is opt-in**, the
      opposite of the obvious build. A scan's yield is uncorrelated with
      anything the caller knows (0 cuts on a 29s b-roll loop, 17 in 60s of
      gameplay — deaths, not shots), so on the continuous takes this op is for
      it draws one tile at exit 0, and it decodes the whole clip besides
      (`reframe_coverage`'s rule). The interval **is** `describe.WINDOW` and
      calls `describe.plan_windows`: deriving the split by hand put the last
      mark past the final frame and lost a tile silently.
    - **A luma reading is meaningless without `media.MediaInfo.bit_depth`** —
      `signalstats` reports on the source's own scale, so a 10-bit clip reads
      YAVG 429 against its 8-bit neighbours' 26–132 and is not brighter; divide
      by `2**bit_depth - 1` before comparing clips. `SHEET_BLANK_MAX` is the
      only threshold on it and answers "is anything here", never "is this
      dark": normalised brightness is continuous 0.104→0.48 across 70 sampled
      frames with **no** gap to pin a line to, while YMAX separates black (16)
      from the darkest real frame (127) by 8x. HISTORY.md § The footage sheet.
- **`claude -p` stream-json output requires `--verbose`, and the
  allow/disallow-tools flags do not gate built-in tools.** Without
  `--verbose`, 2.1.226 errors and **exits 0** with empty stdout; a built-in
  tool named in neither list just runs, unprompted — `--tools` is what
  actually confines the agent panel to proofcut's MCP tools. Both verified by
  reproduction. PLAN.md § The agent panel, in mechanism.
  - **`--tools` is `ToolSearch`, never `""` — `webui._AGENT_TOOLS`, which
    `agent_trial.py` imports.** `""` strips tool search with the rest, and
    then Claude Code loads every one of proofcut's definitions on every turn:
    92,266 turn-1 tokens against 9,359 for the same `ping`, ten times the
    cost. `ToolSearch` reads no file and runs nothing, so the confinement
    holds; what it changes is that `instructions` (`server.INSTRUCTIONS`) is
    the only proofcut text loaded up front, so it is a **map of tool
    families**, capped at 2 KB like every description (both silently
    truncated past that, both held by a wire test). A reply past the
    client's 25K tokens becomes a file path the panel cannot open, so a
    reply that grows with the film is **windowed by default**
    (`get_transcript`, `caption_view`, `timeline_view`). HISTORY.md § The
    MCP surface, rebuilt for deferred loading.
  - **A long tool reports progress through `proofcut.progress`, a context
    variable — never a callback argument on the op.** `_tool()` installs a
    reporter when the tool takes the SDK's `Context`, a web UI job installs
    one per job, and whisper's segment lines, melt's `-progress` counter and
    the shipped workers' `proofcut-progress i n` stderr lines report into
    it. A stdio call silent for 30 minutes is aborted by Claude Code, so a
    new long tool takes `ctx: Context | None = None`. **The streaming path
    runs only while someone listens or can stop it** (`progress.streamed()`)
    — otherwise every call is the `subprocess.run` it was, which is what a
    dozen tests patch. **A stoppable job installs `progress.cancellable`**,
    and `progress.run` kills the child's whole process group and raises
    `progress.Cancelled`: melt is three processes deep, so a subprocess a
    web job waits on that skips `progress.run` is one Stop cannot reach.
    HISTORY.md § Stop reaches the render, and `batch` was measured. The tool
    body runs on an anyio worker thread and reports can come from a pipe
    reader under it, so the loop token is fetched with
    `from_thread.run_sync(lowlevel.current_token)`:
    `from_thread.current_token()` wants a loop in *this* thread and raises.
  - **The trial briefs and the `cut`/`film`/`review` prompts are one text,
    `proofcut.briefs`** — `tests/test_briefs.py` holds the trial rendering
    byte for byte, so editing a shared goal line changes what the next trial
    measures. The prompts differ only where the reader does: a person's
    agent has a shell, so no prompt says it has none.
  - **An unbound `proofcut mcp` started inside a project binds to it**
    (`bound_by: "cwd"` in `ping`/`doctor`), which is how the plugin spares
    its users `path`. It also means a server launched from a project refuses
    every other one; start it elsewhere to reach several.
  - **A generated MCP config's `command` is resolved by `claude` against
    *its* PATH, never by proofcut — so it names `sys.executable` and
    `-m proofcut.cli`, never the string `proofcut`.** A bare name is absent from
    PATH for every launch that skips an activated venv (`.venv/bin/proofcut
    web`, a desktop entry, what `proofcut open` spawns), and the failure is
    silent in the worst way: the harness reports the server `failed` with
    `tools: []`, `claude` runs anyway, exits 0, and answers the prompt in
    prose while the pane's banner still says it reaches the timeline through
    proofcut's tools. Measured both ways — 0 tools against 68. `agent.js` now
    draws the init event's `mcp_servers` when proofcut is not connected, because
    the next thing that breaks this will break it silently too. HISTORY.md
    § The agent panel had no tools at all.
    - **The Claude Code plugin manifest is static JSON, so it cannot name
      `sys.executable` and must never name a bare `proofcut`** — it is
      `uv run --project ${CLAUDE_PLUGIN_ROOT} proofcut mcp`, which for a plugin
      sourced at the repo root resolves to the checkout. HISTORY.md § The
      registry entry and the plugin manifest.
  - **`--model` bakes into `claude -p`'s argv at spawn, so there is no way to
    hot-swap a running turn's model.** `#agent-model-select` (`agent.js`)
    rides it along with every `POST /api/agent {prompt, model}` rather than a
    side-channel setter; `AgentSession.send` is what decides whether it
    actually changed anything — a difference from the model already baked
    into a live subprocess kills it exactly the way "New Task" does (same
    `_suppress_next_exit_report` dance) and the respawn below picks it up,
    while the ordinary path (same model, or the first turn) touches nothing
    extra. Never validated against a fixed list, the canvas/framing
    overrides' own reasoning: `claude`'s own accepted model names change out
    from under any list proofcut would keep, so a wrong one surfaces as
    `claude`'s own refusal. **The choice is `cache/session.json`, never the
    manifest** — it is a preference about this webui's own chat tool, not
    authored film content, and the manifest's undo/snapshot machinery has no
    business gaining an entry every time someone picks a different model.
    HISTORY.md § The agent panel got a model selector.
- **OTIO's edit algorithms are C++ only.** `overwrite`/`insert`/`trim`/`slice`/
  `ripple`/`roll`/… have no Python bindings; `opentimelineio.algorithms` gives
  you only trimming, flattening, and transition expansion. Cutting means
  hand-rolled track surgery over Track/Clip/Gap and `source_range`.
- **The OTIO metadata key is written `"proofcut"` and read as either, through
  `timeline.proofcut_metadata` and nothing else.** Every snapshot in
  `cache/history/N.otio` carries `"lucid"` forever and `restore` puts one back
  whole, so a reader of only `METADATA_KEY` reads a fresh project fine and
  breaks every undo past the rename. The writer writes the new key
  only; `migrate` rewrites the *live* `project.otio`
  (`rewrite_legacy_metadata`) and never a snapshot. The move is assign, then
  `del` — never `pop`: the value is a view into OTIO's C++ dictionary and dies
  with the key, nested parts included.
  - **The manifest name is the opposite rule: a `lucid.json` is never read.**
    A directory holding only it is refused by `Project.open`
    (`LegacyManifestError`, naming `proofcut migrate`) and renamed by
    `migrate`'s filename step — ahead of the version steps, not a
    `_MIGRATIONS` entry, `SCHEMA_VERSION` untouched. Holding both names is
    refused by `open` and `migrate` alike. The picker lists the first
    `needs_migration` and the second `unreadable`. HISTORY.md § The rename.
- **The MCP server's own name is stated in five places, and they move
  together** — `server.py`'s `MCPServer(name="proofcut")`, the `"proofcut"`
  key `webui.AgentSession._mcp_config` writes, `agent.js`'s
  `servers.find((s) => s && s.name === "proofcut")`,
  `webui._AGENT_ALLOWED_TOOLS`'s `mcp__proofcut__*`, and the `"proofcut"` key
  `scripts/agent_trial.py`'s own `_mcp_config` restates rather than imports.
  A mismatch is the silent no-tools failure, not an error: `claude` exits 0
  and answers in prose. HISTORY.md § The agent panel had no tools at all.

## Conventions

- **Scratch has one root, `~/proofcut-work`** — still `$HOME`, so melt's
  flatpak can see it. A new spike/probe goes in `spikes/<name>` and a finished
  one moves to `archive/spikes/<name>`; `projects/` holds the projects other
  files point into (`final-cut`, whose six manifests and `reference` render
  are absolute self-paths, `demo`, `scream-v2`, `kf-probe`, `cards-reauthor`,
  and the settle-against copies `brief-check` / `framing-detect` /
  `threshold` / `split-detect`); `voice-clone/` is a runtime dependency
  (below). **It was thirteen `~/lucid-*` directories until 2026-09-14**, and
  the records still cite those: `~/proofcut-work/MOVED.tsv` maps every moved
  directory. HISTORY.md § The working directories, gathered.
  `~/proofcut-render` stays flat — `picture.RENDER_SCRATCH` is a code literal
  and a directory a stranger's install creates. Manifests store absolute
  paths, so moving any project directory means rewriting them — grep its
  `*.json`/`*.otio`, undo history included, first.
- Every MCP tool gets a matching `proofcut` CLI subcommand. The CLI is how the
  same operation gets scripted and debugged without an agent in the loop, so
  parity is a feature, not overhead.
  - **Register tools with `@_tool()`, never `@mcp.tool()`** — it is what
    routes `path` through the `-C` binding. A tool registered the way every
    prior expects works, advertises an identical schema, and is silently
    unconfined; a test asserts against it. HISTORY.md § Binding the agent's
    MCP server to its project.
  - **`_tool()` also refuses a tool with no row in `server._ANNOTATIONS`** —
    the MCP read/destructive/idempotent hints. Classify by what the tool does
    when it *writes* (`apply`/`plan`/`out` included), and claim
    `destructive_hint=False` only for an op read to refuse rather than replace.
    HISTORY.md § Every tool says what it does to the project.
    - **And it refuses an argument missing from `server._PARAM_DOCS`** — the
      table beside the hint table holding what every argument *means*, never
      an `Annotated[...]` block inline. It refuses a row naming an argument
      the tool does not take too, which is what catches a rename.
      `_COMMON_PARAMS` is only for names meaning the same thing everywhere,
      so **`clip_id` and `phrase` are deliberately out of it**: `clip_id` is
      the addressing transcript on a cue and the footage on `thumbnail`, and
      `phrase` binds its first word on `cue_add` and its last on `vo_extend`.
      The text is hung on `fn.__annotations__` and never the wrapper
      (`functools.wraps` sets `__wrapped__`, which `inspect.signature`
      follows), so **judge it by reading `tools/list`**. `uvx tdqs lint`
      scores the surface offline — and read what you wrote back: it called
      100% coverage clean over three false claims. HISTORY.md § The tool
      definitions were graded, and `path` was the gap.
  - **`path` is optional everywhere (`str | None = None`) and defaults to
    the bound project** — under `-C` it is ceremony with one accepted value,
    and an agent measured on the real trial passed it on every one of 29
    calls anyway, having been told it never would need to. Unbound and
    omitted refuses by name (`_confine`) rather than a bare pydantic "Field
    required". `fonts`/`pack_show` opt out (`projectless=True`): their own
    docstrings mean `path=None` as "no project", not "which one". HISTORY.md
    § The trial's queue, closed.
  - **`list_media` is what hands an unattended agent source paths** —
    `--tools ToolSearch` gives it no directory listing of its own. `source_dir` is
    deliberately not a `_tool()` selector (`import_media`'s own `source`
    precedent): it names where footage lives, not a project, so confining
    it would refuse the tool's whole point. A filename filter against
    `media.SOURCE_MEDIA_EXTENSIONS`, not a probe — `import_media` still
    decides usability. HISTORY.md § The seventh queue item, decided and
    built.
- Tests exercise the real server process over stdio (`tests/test_server_stdio.py`),
  not just the tool functions. Unit-testing a tool body proves nothing about
  whether it is registered or reachable. Same discipline for the web UI:
  `tests/test_webui_http.py` speaks HTTP to a real socket, because a handler
  called directly proves nothing about routing, Range, or the guards.
- **The web UI (`webui.py`, `web/`) is a third client, never a third
  implementation.** It draws and it plays; it never decides — every mutation
  posts to the same `ops` function the CLI and MCP call, and the panel renders
  that function's own return value. It also binds loopback **and** checks the
  `Host` header **and** requires `application/json` on mutations; loopback
  alone does not guard a server that can rewrite your edit. HISTORY.md § The
  preview/timeline web UI.
  - **Off loopback it is opt-in, and `webui.remote_policy` is the only place
    that decision is made** — `proofcut web --allow-remote`, or `--tailscale`,
    which fills its arguments in from this node. Loopback+Host is this
    server's *whole* credential, so widening it **replaces** that credential
    rather than dropping it: the Host allow-list grows to the names the
    operator says clients will present and **never to "anything"**, and a
    token rides every request. **Host is checked before the token**, or a
    leaked token buys a rebinding page in.
    - **The token travels as a cookie, and that is why `web/` has no idea it
      exists.** `?t=` is answered with
      `Set-Cookie: proofcut_token=…; HttpOnly; SameSite=Strict` and the page's
      own fetches, media ranges and `EventSource` carry it unchanged — so
      **never thread a token through a JS request**, which is the obvious
      build and puts the credential in as many places as there are calls.
      `SameSite=Strict` is the CSRF half loopback was covering, which is why
      the `application/json` rule on mutations is untouched.
    - `--tailscale` binds the 100.x address itself, never a wildcard, so the
      socket is not on the LAN at all; it **refuses rather than falling
      back**, because one that quietly bound loopback looks like the feature
      working until a phone tries it. A wildcard bind refuses unless told
      what clients present — `server._serve_http`'s refusal, and
      `_WILDCARD_HOSTS` is stated once, in `webui.py`. HISTORY.md § The
      window, reachable from the tailnet.
    - **A phone reaching it is a home-screen shortcut, which runs
      standalone — under the status bar, the island and the home
      indicator.** The two halves of the fix are each inert alone:
      `env(safe-area-inset-*)` is 0px without `viewport-fit=cover` on the
      viewport meta, and that meta without the padding only draws more
      content under the island. All three phone-reachable pages carry both
      (`index.html`, `picker.html`, `reviewserver.py`), and the top inset
      rides `#bar` rather than `body` so the bar's own surface runs under
      the status bar. Unmeasurable here — CDP cannot set an inset — so it is
      judged on the phone. HISTORY.md § The window on an iPhone home screen.
  - **`_revision` watches the manifest as well as `project.otio`** — the cue
    table and the caption style live there and touch no timeline, so an otio-
    only revision leaves an open window drawing a stale lane.
    - **A job that writes only a transcript file trips neither watch.**
      `ops.transcribe` and `ops.attach_transcript` touch no `project.otio`
      and no manifest, so a finished transcription fires no `project-changed`
      at all — the job's own `done` bus event is the only reload signal a
      client has. Import is unaffected (`ops.import_media` writes the
      manifest). **Reload through the pane bus's `reload` event, never
      `location.reload()`**: the `done` event carries the op's whole return
      value and a page reload throws it away — on the first real
      transcription that was `4 suspect durations`, reported and then wiped
      off the screen. HISTORY.md § Import and transcribe became window
      operations.
  - **Timeline lanes are projections of one `Edit`, not tracks. Never draw a
    lane `export` cannot produce** — the multi-source render degrades silently
    (see auto-editor above), so the window would look right and the file would
    be wrong. The view widens when the model does, never ahead of it.
    PLAN.md § Tier 3 is the goal. The picture lane (V2) is **drawn as of step
    6**, and only because step 5 made `export` able to render it.
    - **Every lane lays out against `timeline.contentDuration`, and every
      widen through it is `Number.isFinite`-guarded.** Each lane is a
      different projection, so the last thing in one can end a few ms past
      `state.timeline_duration` and draw past its own lane. The guard is the
      load-bearing half: `Math.max(x, undefined)` is NaN, NaN survives every
      later `Math.max`, and `computePxPerSec` answers a NaN duration with the
      container's whole width **as pixels per second** — 442618px-wide lanes,
      nothing thrown. (A shot carries `start` + `duration`, never `end`.)
    - **A `.clip-block` cannot be used narrower than its own padding +
      border**, border-box or not, so its horizontal padding is a floor on
      every block's drawn width — at `2px 6px` a fifth of the film's A1
      segments were drawn wider than their duration. Keep it small; the label
      is unaffected, since text only draws at `LABEL_MIN_BLOCK_PX` and up.
    - **The preview's unused height goes to the timeline, and both writers
      compute absolutely, never by delta** — `player.js` § `balancePanes`
      sets `--timeline-h`, `fitLaneHeight` grows `--lane-h` to fill it. Both
      run on paths a resize triggers, so a delta version ratchets and can
      never hand height back to a height-bound frame; and neither may take
      more than the lanes can use, or the dead space is merely relocated.
      HISTORY.md § The five defects behind "their UI looks cleaner".
  - **The palette lives once, as `light-dark()` in `app.css`'s `:root`, and
    JS must never read a colour token.** `getPropertyValue('--x')` returns
    literal `light-dark(…)` text, which `fillStyle` **silently ignores** —
    right in one theme, black-on-black in the other. Give the element a real
    `color` and read `getComputedStyle(el).color`; a canvas also needs
    `theme.js`'s `proofcut:theme` event to know to repaint. HISTORY.md § The
    look pass.
  - **The picture lane draws `timeline_view`'s `shots` — the projection
    *already through `mlt.plan_picture`* — never `build_shots` directly.** The
    two disagree: `plan_picture` refuses a shot longer than its asset, so the
    raw projection would draw one `export` rejects. A refusal from either
    arrives as `shots_error` for the lane to draw, never as an exception.
    HISTORY.md § The picture lane.
    - The **preview** layer reads the same array, and one more field off it:
      `src_start` is where inside its asset the shot reads, so a clip used
      three times previews from three places. Reloading each asset from its
      head looks right and is a different film. `player.js` § the picture layer.
  - **`#frame` is the project canvas, and media is *placed* in it, never
    fitted.** Every layer draws inside that one rectangle, and each element
    goes at `timeline_view`'s `reframe[clip].dest` — the writer's own
    `dest_rect`, scaled — so the preview crops where the render crops. Fitting
    to the media's aspect draws footage `export` drops; so does `contain`ing
    it in the canvas, which adds bars the render has not got. A **still** is
    the exception and keeps `contain`, because that is what MLT does to one.
    Nothing in JS derives a crop. HISTORY.md § The viewer's frame.
  - **Verify the picture layer by canvas readback, never by screenshot** —
    whether headless Chrome composites a `<video>` into a capture is not
    settled: it did not on 2026-08-09 and did on 2026-08-19, same binary
    (wiki `tooling.md` § Headless browser). So a black capture proves nothing
    and a good-looking one proves nothing. Compare against ffmpeg's frame at
    the source timestamp the page claims. HISTORY.md § The preview picture
    layer.
    - **A readback proves neither that the frame is current nor that anyone
      can see it** — `drawImage` obliges on a mid-seek and on a
      `visibility: hidden` element alike, and both cost this repo a day. What
      to gate on, and the calibration that catches it: wiki `tooling.md`
      § Headless browser. What they cost here: HISTORY.md § The viewer's frame.
    - **Where the answer is "which source second is this", build the source so
      every moment names itself** — colour-coded blocks and a burnt-in counter,
      and then one sampled pixel settles it. Used to prove a pinned cue's
      in-point survived into melt, where the *wrong* answer is the clip's own
      opening seconds and so passes every plausibility check there is.
  - **A browser pass driven at CDP's default zero dwell is not a pass** — a
    fix whose transition sits inside a real click's 60-150ms dwell reads green
    at 0ms and is dead at every real speed, which has already shipped here.
    Drive every click at 0ms *and* ~120ms. The harness is
    `.claude/skills/verify-live/` — dwell, hit-tested clicks, drags, viewport
    overflow probes — rather than a fifth hand-rolled one. The measurement and
    the mechanism: wiki `tooling.md` § Headless browser. HISTORY.md § The
    dwell-timing lesson.
    - **So redraw only the node a gesture owns while it is live** — rebuilding
      the container the mousedown landed in removes its target, and Chrome
      then drops the trailing `click` with nothing thrown, which is how a lane
      that re-rendered on mousedown silently stopped seeking. HISTORY.md § The
      cue-drag browser pass, and six defects.
      - **A gesture whose hit target is smaller than `snapTolerance()`
        resolves to nothing and no-ops silently** — a drag clamped to under
        one tolerance never reads as `moved`, so there is no preview, no
        popover, no toast, just a dead drag. Don't offer a gesture on a
        target too small to complete it; on the shipped film's own segments
        the median block was under the threshold that would have made this
        the common case, not the edge one. HISTORY.md § Direct manipulation
        on the timeline.
    - **And scope the probe to the page, never to where the bug is expected** —
      a sweep of `#workspace *` measured clean at every width while `#bar`
      overflowed at 700px with Export and the theme toggle off a hidden edge.
      The window a check looks through is a claim too, and it was wrong in the
      review *and* in the pass verifying its fix. Walk `body *` and compare
      `body.scrollWidth` against `innerWidth`. HISTORY.md § The web UI review.
      - **And that probe is blind inside a scroll container, which is not a
        bug in it** — it has to skip them or every timeline lane is a
        finding. So the panes with `overflow-y: auto` are a second sweep, not
        the same one: walk the scroll containers and compare each one's own
        `scrollWidth` against its `clientWidth`. `overflow-y: auto` computes
        `overflow-x` to `auto` too, so a pane is one of these without ever
        saying so — `#properties-body` clipped `clips` mid-word at the window
        edge and every page-level probe called it clean. In Edit mode the
        sweep should find exactly one, `#track-lanes`. HISTORY.md § The
        README screenshots, and the five defects they found.
      - **A state the layout was never measured in is the same class of
        blind spot**: F2's rails were measured collapsed at every width and
        expanded at none, and expanding one at 700px put the pane itself off
        a hidden edge. Enumerate the states a probe runs in the way widths
        are already enumerated.
      - **And neither sweep looks at height, where `overflow: hidden` clips
        in silence** — `#track-lanes` held 148px of lanes in a 143px box and
        the CC lane lost its bottom edge, with no scrollbar to notice.
        `scrollHeight > clientHeight` on a clipping box is the check. It is
        `auto` + `scrollbar-gutter: stable` now, and the gutter is the
        load-bearing half: a scrollbar that appears changes `clientWidth`,
        which the fit-to-window px/sec was computed from, so the layout
        invalidates its own input.
      - **A synthetic `MouseEvent` carries `offsetX: 0` however you set
        `clientX`, so a pointer-offset gesture dispatched from JS silently
        resolves as if clicked at its left edge** — six positions across a
        caption band returned the same cue six times and read exactly like
        broken resolution. Real CDP input at the same points logs `offsetX`
        27 and 530. Drive these with the harness or not at all — and diff the
        section a gesture should have changed, never the whole document: the
        same pass had a regex matching a stale value from elsewhere in a
        4777-character pane. HISTORY.md § The five defects behind "their UI
        looks cleaner".
  - **An author `display:` rule outranks the UA's `[hidden] { display: none }`,
    so `el.hidden = true` does nothing on its own.** Anything this file set
    toggles by `hidden` needs a companion `[hidden]` rule or it is drawn
    permanently — and it reads as deliberate, because a floating panel sits
    where a selection would have put it. It cost both toolbars and the pad
    popover at once, invisible until words became tabbable. `#picture` is the
    one element that already had the companion rule, which is the only reason
    a stray `*/` deleting its whole block was subtle rather than catastrophic.
    **Fifth instance, and the first with a test: `.rail-panel`.** Its three
    panels are how the rail shows one of agent/assets/properties, so without
    the companion rule `hidden` does nothing and all three draw stacked.
    HISTORY.md § The web UI review, § The workspace redesign.
  - **A floating panel is clamped by `dom.js`'s `clampFloating`, and there is
    exactly one copy.** Its **three** callers hand it three different spaces,
    which is why it takes bounds rather than a container: the transcript
    toolbar is unscrolled, the cue toolbar has `scrollLeft` already folded in,
    and `#frame-rows` is itself the scroll container — so its bounds are
    `scrollTop … scrollTop + clientHeight`, never `0 … clientHeight`.
    `offsetTop` is in content coordinates, so clamping a scroller against its
    client box pins the panel to the top of the CONTENT, which is off the
    visible column entirely once anything is scrolled. A second clamp is how
    the first fix reached one of the two toolbars and not the other.
    - **`clampFloating` MOVES a box; it cannot SHRINK one.** A panel taller
      than its container is pinned to the top with its own buttons hanging
      off the bottom — measured at 218px inside the 143px `#track-lanes` of
      the day, Apply landing at y 920 of a 900px viewport, where
      `elementFromPoint` returns null. Hit-testable by a synthetic `.click()`
      and by nothing a person can do, which is exactly why nothing caught it
      first. Cap the panel's own height instead. (That box is now `auto` and
      grows with the pane — a taller lane stack makes this fire less often
      and does not retire the rule.) HISTORY.md § Direct manipulation on the
      timeline.
      - **Its sibling: a new control spends the pane's height, out of the
        list below it.** The add-footage form left `#assets-list` 126px of
        613px, so the *second* clip's own button sat outside the scroll
        window — where a rect still reads on-page and `elementFromPoint`
        answers about whatever is painted there instead. Measure what a new
        control leaves the pane; fold away anything used once per asset.
        HISTORY.md § Import and transcribe became window operations.
  - **The chrome states facts and labels controls; it does not explain the
    build.** Five pane headers each carried a sentence, and four of them were
    invariants addressed to whoever adds the next lane rather than to anyone
    editing a film — so the dim small-caps became uniformly ignorable,
    including the one line that disambiguated two adjacent buttons. A fact
    earns a chip (`640×360`, `read-only`); a gesture earns a tooltip and a
    row in the `?` sheet; an invariant belongs in PLAN.md or here, where it
    is enforceable; **and a warning about a control goes ON that control**.
    HISTORY.md § The workspace redesign.
    - **A quiet chip is not the all-clear — it has to SAY so.** An
      uncoloured "0 flags" beside four other uncoloured chips is
      indistinguishable from one that has not loaded, which is Frame's own
      blank-coverage-chip lesson one pane up. `.chip.ok` is deliberately
      narrow: a chip goes green only where zero is the op's own verdict
      about its own subject (`flags.count`, `stale_seconds`, `steps`), never
      on a fact that is neither good nor bad (a canvas, a caption state, a
      `cuts framed` ratio) — that would be the window forming an opinion.
  - **The rail (`#rail-pane`) is ONE pane with three tab panels, not three
    columns** — agent, assets and properties, each getting the whole rail
    when its tab is on. Two panes stacked in one column at `flex: 1 1 50%`
    is what clipped the second of three clips at 1400px, in the shipped
    README screenshot. `app.js`'s `setRailTab` is the only thing that moves
    the selection. HISTORY.md § The workspace redesign.
  - **The `?` sheet is hand-typed HTML — never generated — grouped by which
    pane owns each binding**, because that is what decides whether a key does
    anything: the transport bindings die the moment focus enters a field. Its
    body is a height-capped scrollport. HISTORY.md § The shortcut sheet grew
    the three panes it never listed.
  - **A `<video>` that cannot decode fires one contentless `error` and shows
    black**, which is exactly what a black frame the edit meant looks like.
    Never infer the reason in JS — `media.playability()` behind
    `/api/preview/<asset>` has it, and three of its four refusal classes pass a
    naive codec-name check.
- **`proofcut review serve` (`reviewserver.py`) is a fourth client, on purpose
  not `webui.py`'s guard.** It exists to be reached off the machine (a phone
  on Tailscale), so loopback+Host is replaced by a token every request must
  carry (`?t=`). It is still the right tool for a review round rather than
  `proofcut web --tailscale`: no edit surface at all, and a page that needs no
  JS, so the token rides the links rather than a cookie.
  `review add --kind control --baseline <name>` hashes both files and refuses
  the call on any mismatch — the byte-identical-control rule is enforced at
  registration, not left as a comment. Streaming reuses `webui._stream_file`
  (a standalone function, not a second copy of the Range math). PLAN.md § The
  completion queue, item 6. HISTORY.md § `lucid review`, built.
- **`proofcut mcp` can serve over HTTP, and the guard is loopback+Host, not a
  token — `webui.py`'s model, not `reviewserver.py`'s.** `--transport http`
  (default stays `stdio`; every existing client spawns the server that way
  unchanged) adds `--host`/`--port`/`--allow-remote`/`--allow-remote-host`.
  `_LoopbackGuard` is real ASGI middleware mirroring
  `webui.Handler._host_is_loopback`, importing `webui._LOOPBACK_NAMES` rather
  than re-stating the fact; it refuses a non-loopback `Host` at startup unless
  opted in, and `-C` confinement holds identically over HTTP.
  - **A wildcard bind's own host string is not a client identity** — for
    `0.0.0.0`/`::` it is backwards in both directions, refusing the real client
    (which sends the address it dialed) and admitting the attacker who sends
    the banner's own `Host: 0.0.0.0`. A wildcard bind refuses under
    `--allow-remote` unless `--allow-remote-host` names the addresses real
    clients will present. HISTORY.md § MCP over HTTP, built.
- **`proofcut web --root DIR` serves a picker over many projects, but the process
  still binds to exactly one.** `POST /api/open` is a *one-way* bind — the
  first project picked calls the same `_bind_singletons` that `-C` already
  calls, deferred under a lock, so `bus`/`agent`/`render_job`/`proxy_job` are
  never more than one project's. Two projects at once is still two processes;
  `--root` widens what a picker can list, never what one server can serve.
  `-C` together with `--root` is refused before either binds a socket, and MCP
  is untouched — a client already spawns its own server per project. HISTORY.md
  § The multi-project picker, built.
  - **A picker that raises on one broken project hides every other one.** The
    scan called `ops.status` with no handler, so one init-but-not-seeded
    project 400'd all of `GET /api/projects`; there are now four scan outcomes
    (`ok`/`needs_migration`/`unreadable`/`error`), never an exception that
    takes the rest of the listing down with it.
  - **`Path.is_dir()` follows symlinks, and open-time confinement is too late
    for a listing that already leaked one** — a symlink under `--root`
    pointing outside it was scanned and its metadata returned before anyone
    tried to open it.
- **Anything taking a word index echoes the words it resolved to, plus the
  three either side.** The neighbours are the point: an index one past the
  intended phrase reads correctly on its own. Mutating tools also take a
  `plan` that resolves without writing. HISTORY.md § `cut --plan`.
- **Cite roadmap items by name, never by number** — the numbers renumber on
  every ship, and four things once cited "item 1" meaning four different
  items. Point at the named PLAN.md or HISTORY.md `##` section instead. PLAN.md § Direction and order.
- **The README screenshots are regenerated by
  `scripts/capture_screenshots.py`, never captured by hand** — three rounds of
  drift, each one a step re-derived from memory and forgotten. Its checks are
  the point and none may be dropped: the theme is read back off `data-theme`,
  the Edit shot refuses without the agent pane's "Export complete", the set
  refuses a mean-luma spread over 30, and `check_timeline_width` is the repo's
  only regression test for the defect below. HISTORY.md § The screenshots
  stopped being captured by hand. **A committed image is public forever**, and
  no text filter reads pixels: never a frame of footage proofcut does not own,
  and never a pane that prints an absolute path (the properties pane does) —
  both cost blobs stripped out of the whole history. HISTORY.md § The repo,
  readied for strangers.
- **An unattended agent edit is measured by `scripts/agent_trial.py`, which
  imports the panel's flags from `webui.py` rather than restating them** —
  `_agent_bin`, `_AGENT_ALLOWED_TOOLS`, `_AGENT_DISALLOWED_TOOLS`, and the same
  interpreter-plus-`-m proofcut.cli` config; a trial that retypes them can drift
  into measuring a client nobody runs. Its own rules (goal-not-steps brief,
  `proofcut init` start, `--control`) are in its docstring. **Two agents in one
  project is possible and neither proofcut nor `claude` will say so** — `claude`
  is spawned into its own session, so killing a harness leaves the agent
  editing. `write_manifest`/`restore` now refuse (`ProjectConflictError`)
  rather than silently clobbering when the manifest moved under a stale
  read — `Project._manifest_stamp`, a sha256 of the manifest's bytes read
  back off disk after every write — but nothing stops the two agents from
  starting in the first place; one of them just loses cleanly now instead of
  losing silently. HISTORY.md § The trial's queue, closed. **The stamp was
  the file's mtime until 2026-09-13, and a clock is the wrong witness**:
  Windows stamps two writes inside one ~15ms timer tick with the same mtime,
  so the refusal passed one CI run and failed the next on identical source.
  Never compare mtimes to ask whether bytes changed; `waveform/`'s size+mtime
  cache key keeps the idiom only because its miss recomputes rather than
  discards. HISTORY.md § The stamp that was a clock.
  - **`--source` runs it over real footage, and the material is the only
    thing it moves** — same client, same confinement, same `score()`. It
    refuses a `--source` that is itself a proofcut project, which is the
    plausible mistake ("run it on a copy of a real project" reads as *hand it
    the project*), and it reads `MANIFEST_NAME`/`TIMELINE_NAME` off
    `project.py` to do so: a guard that looked for `manifest.json` waves every
    real project through, since the manifest is `proofcut.json`. A brief's own
    two checked phrases are `--phrases` and persist into the run directory, so
    `--score-only` scores against the pair the run was scored with; undeclared
    is **unsettled, never failed**. HISTORY.md § The trial over real footage.
- `ruff check` is the lint gate. **Never run `ruff format`** — there is no
  ruff config, so it applies its own 88-column default against this repo's
  wider lines and rewrites 26 of 30 files, burying whatever you actually
  changed.
- **CI runs the suite on Linux, macOS and Windows, so a test measuring a
  Linux mechanism pins `sys.platform` to `linux`** — the display dance, the
  flatpak, fontconfig — or the macOS runner meets the branch that skips it.
  Stand in for another OS by patching `sys.platform` itself, never
  `module.sys` (a control against old code then fails on the missing
  attribute and proves nothing), and stub `shutil.which` under a faked
  `win32`: it reaches for `_winapi`. HISTORY.md § The Windows crash and the
  display gate. **A fake home sets `USERPROFILE` as well as `HOME`** —
  Windows' `Path.home()` ignores `HOME`, and `test_install`'s fixture
  installed into the runner's real profile until it did (it now asserts
  `deps.root()` is inside `tmp_path`).
  - **A fake binary goes through `tests/stubs.py`'s `write_stub`, never a
    `#!` script** — Windows cannot run one, and every stub the suite had was
    one. Python source in, the path to run out; use the returned path, which
    is a `.cmd` on Windows. HISTORY.md § The first run on macOS and Windows.
  - **A stock Windows cannot create a directory past 248 characters**
    (`LongPathsEnabled` 0; GitHub's runner and Tyler's laptop both have 1,
    so neither shows it unasked). `Project.create` refuses a root past
    `248 − PATH_HEADROOM` while it is off; a new deep layout under a project
    spends that headroom, which is 100 and was measured at 44. A test of it
    fakes `winreg` in `sys.modules` beside `sys.platform`, or a Windows
    runner's own 1 decides it. **A 206 later — a long clip id or render
    name — is one line in every client**: `cli.main` catches it, and MCP and
    the web UI get it as a `ProjectError` through
    `project.refusing_path_too_long`, so **a new web UI job wraps its op call
    in that**, or a 206 escapes the job's `except` and the window waits on an
    event that never comes. HISTORY.md § A long project path on Windows.
- **A reachable identifier in the docs is elided, never swapped for a
  plausible one.** The tailnet address, MagicDNS name, IPv6 suffix and
  absolute `/home/<user>` paths that a measurement quoted are written
  `100.x.y.z`, `<host>.<tailnet>.ts.net`, `fd7a:115c:a1e0::…`, `/home/<user>/…`
  — because HISTORY.md is a record of what was measured, and substituting a
  different-but-believable address makes it claim a run against a machine
  nobody dialed. Placeholders that *are* fictional belong in tests, where
  they are the input rather than the report (`tests/test_webui_http.py`'s
  `_TAILNET_HOST`). `~/proofcut-work` paths stay as they are: they name no
  user and no host. HISTORY.md § The closed-loop trial, § The publish
  rehearsal.
  - **That rule now binds the history too: it was rewritten with `git
    filter-repo` on 2026-09-10** to remove what the scrub had left in old
    commits, and again on 2026-09-11 so no tag carries the MIT grant (every
    old hash maps through `~/proofcut-work/archive/git-history/*.commit-map`). GitHub (`tydude001/proofcut`, public since 2026-09-13) is fed
    only by Gitea's push mirror — there is no `github`
    remote here. **Never `git push --mirror`**: the reflog still reaches the
    pre-rewrite objects. **And never merge a PR on GitHub** — the next sync
    force-pushes over it; the route is wiki `git-server.md` § GitHub push
    mirrors. HISTORY.md § The repo, readied for strangers.
- **The version is a hand-typed literal in six places and is bumped
  deliberately, never derived.** `pyproject.toml`, `proofcut/__init__.py`, and
  the four launch listings (`server.json`, `.claude-plugin/plugin.json`, and
  `.claude-plugin/marketplace.json` twice), held together by
  `tests/test_version.py` — a VCS-derived or
  `importlib.metadata` version reads the *installed* dist-info, so an
  editable checkout reports whatever the last `uv sync` wrote. Bump the minor
  when something new becomes callable, `uv sync` behind it, tag, and name the
  HISTORY.md `##` section in the annotation. **A pushed `v*` tag publishes
  itself**: Gitea's mirror syncs on commit, and `release.yml` uploads to PyPI
  (trusted publisher, no token) and then the MCP registry, which reads the
  `mcp-name` marker off PyPI's README. So a tag is a release — never tag a
  version not meant to ship. PyPI never takes a second file for a version,
  so README.md — its project page, whose links must stay absolute — is
  settled before the tag. HISTORY.md § Releases publish from the tag. There is no `CHANGELOG.md` on
  purpose. HISTORY.md § The version caught up.
  - **A GitHub Release is a hand-written launch post, not a per-tag note, so
    it does not follow a bump** — which is how `v0.23.0` stayed "Latest" two
    versions on, describing a proofcut that stopped at the render and
    claiming nobody had run Windows. Cut one only where a release is worth
    announcing, and **a wording pass must name the published releases** or it
    silently misses them. HISTORY.md § The release nobody had cut.
- **A snapshot is a *pair* — `N.otio` + `N.manifest.json` — and
  `Project.write_manifest` takes one by default.** Most authoring state is
  manifest state (the cue table, framing rects, the music bed, the caption
  style, head/tail/holds, marks, card records), so the safe direction is
  opt-out rather than opt-in: a manifest write that skipped history would not
  merely be un-undoable, it would be **erased by the next undo**, because a
  restore puts the whole file back. `snapshot=False` has exactly two callers
  and both are named at the call site (`migrate`, which has
  `_backup_manifest`; `reel`'s seeding of a project it is still building).
  - **`snapshot()` fires at most once per `Project` instance**, which keeps an
    op writing both files (`seed_timeline`, `import_edit`) to one undo press —
    per instance is per op, and `reel` holds two for two projects.
  - The two absences are **not symmetrical** and `undo`'s return names which
    happened: no manifest is an older lucid's snapshot, left alone rather than
    guessed at; no *timeline* is a state that had none, so `project.otio` is
    removed. Undoing an import un-registers the clip and leaves its media on
    disk. **A freshly seeded project's undo depth is 2**, so a test counting
    steps measures a delta from a named baseline. HISTORY.md § Manifest-aware
    undo.
- **`Project.open` refuses an old manifest and must never migrate one** — it
  backs `info` and `status`, so a read would rewrite a project someone only
  looked at. Migration is explicit (`proofcut migrate`), and a schema bump adds a
  step to `_MIGRATIONS` — keyed by the version it migrates *from* — rather
  than widening `open`. HISTORY.md § The schema migration. **The schema is at
  4**; v4 added `cards`.
  - **An additive *optional* key does not bump** — `caption_style`, `canvas`,
    `tail`, `reference` and a window's `interp` are all
    absent-means-what-every-older-manifest-meant, and a bump would make
    `open` refuse every project on disk to gain nothing. Both bumps so far were
    for list keys another op would `setdefault` anyway, where the number is
    what makes the key true rather than incidentally survivable.
- **A footage description indexes the source, so no edit can invalidate one** —
  `(clip_id, src_start, src_end, text)` in *source* seconds, and there is
  deliberately no re-describe hook. Windows are never *widened* — a whole-clip
  pass described six frames as six people — so `plan_windows` rounds the count
  up, never to nearest. PLAN.md § B-roll by description.
  - **A cue's `src_start` pins the in-point: a pinned shot refuses rather than
    rewinds.** `plan_picture` rewinds an unpinned cursor that would overrun —
    right for a re-use, a silent wrong-video for a placement. In a shot dict
    the cue's ask is **`src_pin`** and the planner's answer is **`src_start`**;
    one key for both reads as correct in every test that has a pin in it.
    **A derivation is where this bites hardest, because it empties the
    cursor**: `reel` drops the cues it cut, so every survivor would replay its
    asset from the head and the reel's picture would *not* be the film's
    picture over the same seconds. So **`reel` pins every survivor** to the
    in-point the film's own plan gave it (`cues_pinned`, and `pins_error`
    where the film cannot project) — 2 of the teaser's 4 survivors needed one,
    and hand-derived reels before this did not have them. HISTORY.md § The
    pinned cue, § The framing control, § The three gaps, closed.
  - **A shot's addressing clip is not its footage.** In a shot dict,
    `clip_id` is the cue's own addressing clip — the transcript track, `"vo"`
    on an audio-only project — and `asset` is the footage actually shown.
    Anything that reaches into a shot by `clip_id` to fetch or thumbnail
    footage reaches the wrong file, or none: the first filmstrip draft
    thumbnailed `shot.clip_id` and every V2 request would have 400'd on this
    project. Caught by reading real `/api/view` data before wiring the
    harness, guarded now by a test named for exactly this trap. HISTORY.md
    § The assets, properties and filmstrip backend, and its panes. **Frame
    mode's shot filmstrip is the first shipped caller and obeys it**
    (`frame.js` § buildFilmstrip, `row.asset` and never `row.clip_id`);
    a `card:` asset is skipped, since a card is not a clip id and cards
    reach that view through `skipped` anyway.
  - **A description does not choose the clip — `synopsis` does, and proofcut does
    not choose at all.** Which footage goes under a sentence is never a lexical
    match: measured against 25 human picks, the description index agreed 2
    times and the clips' own *filenames* 3, so a better `describe` prompt was
    the wrong fix. `synopsis` is one line per clip saying what the footage
    *is*, allowed to carry what no camera can see, and `broll_brief` hands it
    plus the narration to whatever is reading — which writes back through
    `cue_add`. **A second reviewing pass was measured and is worse (13 → 10);
    do not add one.** HISTORY.md § Choosing the b-roll.
- **A cut cannot invalidate a cue and can still orphan one, and `build_shots`
  refuses the whole projection on a single orphan.** Word-indexing is what
  keeps a cue *valid* across cuts; it does not keep the word on the timeline.
  Ordinarily that refusal is right — someone cut the line a picture hung on —
  but anything removing material wholesale hits it at scale: `reel` orphaned 34
  of 38 cues, and the derived project opened, passed `status` and rendered
  nothing. So a derivation prunes and **names** what it pruned (`cues_dropped`),
  each entry being a picture the result will not have. HISTORY.md § `lucid
  reel`.
  - **Read that list for the cue nearest the head first.** One pruned from the
    far end trims the result; one pruned just outside the *kept* span opens the
    reel on no picture at all — 11 seconds of it on the teaser, reported as one
    line among 35. Move the edge to keep it. The other half of that watch —
    every survivor replaying its asset from the head — is `reel`'s own job as
    of § The three gaps, closed; **a reel derived before it has unpinned cues
    and is a different film**, so check `cues_pinned` on anything older.
    HISTORY.md § The teaser, re-cut.
  - Its sibling: **`cut_by_time` flags every suspect-duration word a removed
    span overlaps, not the ones at the boundary.** Right for an ordinary cut,
    noise for a wholesale one — 15 flags on a reel, none near either edge — so
    `reel` asks about the edges it *keeps* instead. A guard that has to be
    suppressed every time is the thing to fix, not to document.
  - **A bumper or end card is project state (`TAIL_KEY`), and a derivation
    inherits nothing — it reports `tail_dropped`.** That is the fix for what
    used to happen: applied downstream of `export`, re-cutting dropped it at
    exit 0 with `status`, `verify` and `check_frames` all silent, because
    nothing in the project ever knew. A cue is the wrong mechanism for it — a
    cue addresses a moment *inside* the film and a tail is after it, so that
    route needs appended silence first and `vo_extend` for the teaser. A tail
    is two ordinary MLT entries and **no new writer concept**; what it costs is
    that `Edit` stops being the single answer to "how long is this", which is
    why `status`, `check_frames` and `film_check` read `expected_duration`
    rather than the edit — `film_check` is the one that did not, and read
    `agrees: false` on the real film by exactly its end card until 2026-08-18. HISTORY.md § Tail time, built; § The end card and the bumper became
    templates.
  - **`vo_extend` is built, and it is the one op authorized to grow `Edit`
    rather than only cut it.** `Edit.insert` splices a real generated-silence
    clip in — never a clip_id widened past its registered duration — and
    `restore`/export routing need no changes of their own: `restore` already
    refuses once a clip's segments stop being contiguous, and `_is_layered`
    already routes to melt on a second `clip_id`, both permanently once a
    hold lands. **The one thing to check is `covered_by`** — `build_shots`
    runs each shot to the next cue, so a hold with no cue of its own gets
    whichever picture was already playing frozen across it by default, with
    `shots_error`/`verify`/`check_frames` all staying clean. HISTORY.md
    § `vo_extend`, built.
    - **`vo_synth` is built on the same splice, and the voice is a reference
      clip, never a checkpoint.** Zero-shot Qwen3-TTS with 19 s of VO scored
      0.989 on the model's own speaker encoder (real takes 0.993, a 3-semitone
      shift 0.96); a full fine-tune drifted *away* with every epoch, the
      upstream-lr run collapsed to babble, and the 2026-only control drifted
      the same — it is the recipe, not the data, and **do not reach for a
      fine-tune to fix likeness** (local-llm `notes/voice-clone-zero-shot.md`
      § Round 4). `tts.py` is the fourth interpreter-behind-an-env
      (`PROOFCUT_TTS`/`PROOFCUT_TTS_MODEL` from the environment alone since
      2026-09-12, when a clean Ubuntu's doctor printed the old fallback into
      `~/lucid-work` back to a stranger; this box sets both in
      `~/.config/environment.d/60-proofcut.conf`, pointing into
      `~/proofcut-work/voice-clone/` — **which is therefore a runtime dependency
      and stays where it is, never archived as a finished spike** — but **the voice has no default on
      purpose**: `--voice`/`PROOFCUT_TTS_VOICE` or it refuses, so a public
      checkout holds neither a reference clip nor a path to one). Seed moves
      a render more than the reference does, so the op renders N and ranks by
      `sim` less a flatness penalty — likeness alone keeps the flattest read
      (HISTORY.md § The synth ranks flatness); a render at the length cap is
      `capped` and never wins (a 21 s reference once ran every render to
      655 s); the winner is read back through whisper — through the project's
      `lexicon.json` folds, if one exists — and `heard`/`wer` are a
      **report, never a gate**. The
      splice is `_splice_after`, shared with `vo_extend`, and `_splice_point`
      refuses a cut word *before* the GPU is spent. HISTORY.md § `vo_synth`,
      built.
  - **A cold open (`HEAD_KEY`) and a film-audio hold (`HOLDS_KEY`) are
    `_is_layered`'s sixth and seventh triggers, each landed in the same
    commit as the writer support it protects** — the "must never lag the
    writer" rule the fifth trigger below already states, restated because two
    more lanes now depend on it. A hold's own second `clip_id` already trips
    the first check (`len({...clip_id}) > 1`); its trigger is kept anyway,
    belt-and-suspenders, rather than trusting one path to keep covering a case
    it happens to cover today. HISTORY.md § The cold open is project state,
    § A film-audio hold.
    - **A phrase resolved against the transcript is the one source of truth
      for a hold's addressing — never four hand-typed word indices.**
      `hold_add`'s `asset_phrase` resolves against the asset's *own*
      transcript and binds first-and-last together into
      `word_index_first`/`word_index_last`; `gap_phrase`/`cue_phrase` reuse
      the same `_resolve_word_or_phrase` machinery every other mutator does.
      Hand-typed indices drift the moment the transcript changes under them;
      a phrase re-resolves. `cue_reresolve` is the batch version of the same
      idea, for every phrase-addressed cue/mark/bed boundary at once
      (`apply=False` default). HISTORY.md § A cue is addressed by phrase.
  - **The A2 music bed is project state too (`MUSIC_KEY`), and no field in it
    is a timeline second or a frame count** — `(clip_id, word_index_start,
    word_index_end | None)`, duration derived live through
    `Edit.timeline_span` on every build (`_music_plan`), never stored and
    never cached: a stored length was measured drifting onto live material,
    and a cached one is two facts kept in step only by a hook nobody has
    forgotten yet. No end word means "to the end of the `Edit`" — a tail is
    after the film, so the end card holds over silence. The bed's lane is
    padded/trimmed to the document's exact total by construction (real
    silent-WAV entries, never a `<blank>`), so `mlt.declared_frames` still
    takes no exceptions. **`MUSIC_KEY` is `_is_layered`'s fifth trigger and
    must never lag the writer** — a bed recorded but routed through
    auto-editor renders with no music at exit 0, invisible to every check
    but listening; the export reply's `music` field is where a caller sees
    the render carried it, and `timeline_view`'s `music`/`music_error` is
    what the web UI's A2 lane gates on (`timeline.js` `buildMusicRow` — no
    bed, no lane). `reel` drops the bed and names it
    (`music_dropped`) — the tail's rule, because a bed re-opened from its
    head mid-film is the `cues_pinned` shape with no pin to give it.
    HISTORY.md § The A2 music lane, built.
    - **A `volume` filter's `level` keyframes are dB, positioned relative to
      the entry the filter is attached to — never gain factors.** Keys of
      0..1 render as a 1 dB wiggle at exit 0: a fade correct in the XML and
      absent from the audio. `level=0` is exactly unity. Both measured
      (`~/proofcut-work/spikes/a2-probe/fade_probe.py`); the fades ride the bed's entry so
      a fade-out ends where the music *audibly* ends, and a fade pair the
      bed cannot hold refuses at `_music_plan` ("shorten the fades"), never
      clamps. HISTORY.md § The A2 fades and the lane, drawn.
    - **`mlt.Entry.gain_db` (default 0.0) is the plateau a fade ramps to and
      holds at, not always 0** — a flat, non-fading level shift reuses the
      exact same `volume` filter rather than a second filter type (the cold
      open's own reason for existing), and `_fade_level` still emits the
      filter on `gain_db` alone even with no fade set. `gain_db=0.0` is
      unity, so every caller before this field existed still gets exactly the
      plateau it always got and their documents stay byte-identical.
      **Every keyframe position is `entry.src_in`-offset, not 0-based** — the
      music/tail/unpinned-head lanes all read from `src_in=0` so this was
      invisible until a hold's entry, which always reads from deep inside its
      asset, rendered *silent throughout* under 0-based keys: playback
      reaches producer frame 268 long after the animation's last defined key.
      HISTORY.md § A film-audio hold.
    - **Film audio under the VO (`UNDER_VO_KEY`, `hold under`) stores no
      in-point** — it reads from wherever the shot showing its asset has got
      to at the span's first word, so it cannot disagree with the picture,
      and it refuses an asset that is not on screen there. It rides the
      holds lane, gates the bed out like a hold, and is `_is_layered`'s
      eighth trigger. Mind that **the first shot is forced to frame 0**
      (`build_shots`): a first cue's asset is on screen from the open, so
      its playhead at a later word is the cue's in-point *plus that word's
      time*. docs/plans/NATIVE.md § A2.
    - **A hold's audio reads its clip from `play_at`, never `src_start`.**
      `src_start` is the picture cue's in-point; by the gap the clip has
      played `elapsed` further, and the line is there. The lane read
      `src_start` until 2026-09-14 and every hold on the Lambs/Longlegs native
      rebuild played the seconds before its line — past a real-render test
      whose film was one constant tone, which cannot say which second it is.
      And **fake whisper in whisper's own shape**: its JSON nests words under
      `segments[].words[]`, a stub writing a flat `words[]` passed, and
      `_transcribe_span` returned "" on every real span, so `hold_check`
      never heard a hold. HISTORY.md § The Lambs/Longlegs native rebuild.
    - **The window sets the bed too (`POST /api/music`), and both traps in
      that are general to any editor over a projection.** A **refusal is
      sent instead of the state** — `music_error` means `state.music` is
      null, so a panel filled from the view says "no bed yet" over a bed
      that exists; read the stored cue back through the op's no-argument
      read, never by adding a view field. And **`clip_id` rides the word
      index and never travels alone**: it is the transcript the index
      indexes, so sending the view's current clip on a fades-only change
      re-addresses the bed, and reads as correct in every call that happens
      to be a first set. HISTORY.md § The A2 lane became settable.
    - **A bed can be passages, a rotation and a level** — `passages`,
      `rotate`, `crossfade`, `src_in`, `under` on `MUSIC_KEY`, each additive
      and absent-means-today's-bed, so no schema bump (docs/plans/NATIVE.md
      § A1). `_music_pieces` lays them out as pieces in Edit frames, and
      **overlapping pieces go on a second lane** (`music2`, `tractorC`):
      one playlist cannot hold two things at once, and a bed that never
      overlaps writes no second lane and the same bytes as before.
      **A crossfade edge is `crossfade_in`/`crossfade_out` on `mlt.Entry`,
      and it must be** — the ordinary fade is a straight line in dB, and two
      crossing sum to a hole: −50 and −54 dB at 0.9 s into a 2.5 s overlap
      against a −24 plateau, where the equal-power keys hold the total flat.
      A new overlap that sets only `fade_*_frames` renders that hole at
      exit 0.
    - **A bed's duck (`duck` on `MUSIC_KEY`) is keyed off the Edit's own
      audio, never the transcript's words** — scored against Scream v8's bed
      recovered from its render, a word-span duck was 3.39 dB off against no
      duck's 3.62 and the audio gate's 2.72. It is a gate (`duck.py`), drawn
      as `mlt.Entry.gain_keys` on the bed's own `volume` filter, measured
      where `frame_layout` puts each segment, and **computed in `_build_mlt`
      only** — the decode is export's cost, never `_music_plan`'s, which runs
      on every `project-changed`. **Anything that splits an `Entry` cuts its
      keys with `mlt.slice_gain_keys`**: a rebuilt piece without them plays
      undipped at exit 0, which is `_gate_music_lane`'s two splits.
      HISTORY.md § The duck.
    - **`export --loudness` is one gain and a true-peak limiter, never
      `loudnorm`'s second pass.** `loudnorm` keeps `linear=true` only while
      the gain fits under the ceiling and otherwise switches to dynamic mode,
      which rides the whole mix: on Scream it put 5 dB of a 12 dB duck back,
      on target and at exit 0. `alimiter` needs `level=disabled` (or it makes
      up to the ceiling) and `latency=true` (or every sample is 5 ms late).
      HISTORY.md § The duck.
    - **The master restamps its audio in AAC's 1024-sample frames
      (`asetnsamples` + `asetpts=N/SR/TB`), and must keep doing so.**
      `loudnorm`'s frame timestamps ran ahead of its samples, so one AAC
      packet claimed up to ~90 ms more than its 1024 samples and everything
      after it played that late; stamping by samples consumed still left one
      packet a sample long after the limiter's 192 kHz round trip. **Judge
      audio timing by packet durations, never by decoded samples** (PCM
      ignores timestamps) **and never by pts contiguity** (the long packet
      still abuts the next one). Both missed it here. HISTORY.md § The
      master's late audio.
- Resolve media through `media.media_path()`, never `root / clip["media"]`. A
  `media/` entry is optional — the NAS rejects symlinks, so import falls back to
  referencing the source in place (wiki `files.md`).
  - **The preview resolves through `media.preview_path()`, and that split is
    the containment.** A proxy never enters the manifest and `media_path()` has
    no branch for it, so `export` cannot reach one because it never calls
    `preview_path` — whose only callers are `ops.preview_source` and
    `webui._send_media`. **Adding a third is the whole hole**; a manifest key
    would inherit `media_path()`'s reach and deliver a preview encode as the
    film. A proxy is also **downscaled** (`media.PROXY_HEIGHT`), safe only
    because `player.js`'s `place()` positions by canvas-coord `dest` and never
    reads `videoWidth`. Its size claim holds on real footage only — a
    `testsrc` fixture proxies *larger* than its hevc source, so never assert a
    reduction. HISTORY.md § The preview proxy.
  - **`GET /api/output` is the only route that serves `renders/`, and it
    resolves through `renderlog.last` — never the manifest and never
    `preview_path()`.** It streams the one file the last pipeline run
    recorded: not a listing, not a path the client names, and confined inside
    the project anyway, because proofcut writing that log itself is the argument
    for not letting one hand-edited line turn a loopback server into a file
    server. The window still plays the *project* everywhere else; this is the
    single place it plays an artifact, and widening it is a new decision.
    PLAN.md § Open questions, *Should the workspace play its own output*.
    HISTORY.md § The window plays its own render. **That containment became
    load-bearing on 2026-09-04**, when `ops.export` and `ops.add_captions`
    became writers of the log as well (renderlog.py § two writers): both take
    an output path from their caller, so the log can now name a file outside
    the project, and the `relative_to(root)` check is what refuses to stream
    it rather than a second belt on the same braces.
  - **A thumbnail is a preview artifact and keeps the same containment rather
    than adding a caller to it.** `ops.thumbnail` never enters the manifest
    and never calls `preview_path()` — it resolves media through
    `media.media_path()` and cuts one frame with `picture.extract_frame`,
    cached under `cache/thumbs/<clip_id>/<ms>.jpg`. Adding a third caller to
    `preview_path` is the whole hole this containment exists to prevent; a
    filmstrip route earns its keep by not needing one. HISTORY.md § The
    assets, properties and filmstrip backend, built.
- **Trust a transcript's word order, never its word durations.** Whisper hides
  a whole retake inside the duration of the word after it. So "did this word
  survive?" is an *overlap* test against the kept ranges, never containment —
  partial survival is normal. HISTORY.md § 2.
  - The trap when *masking audio* with a word map: an inflated duration covers
    the retake it swallowed, so believing it hides exactly the hole you are
    looking for. Trim spans through `energy.believable` first. HISTORY.md § `verify --windowed`.
  - **A retake seam also *adds* words** — whisper reads across the splice and
    interleaves both takes — **and the tell is that the word starts before the
    one ahead of it ends, never that it reads wrong.** Reading for sense removes
    the nonsense ones and leaves every grammatical one standing; nine were in
    44s of the Scream VO, and `transcript.find_overlaps` finds **40 seams in the
    whole film**. Overlap-scan anything derived from a transcript before it is
    drawn. HISTORY.md § The hand-framed teaser, watched.
    - It rides both attach paths as `overlaps`; a finding is computed at attach
      and returned once, so `transcript-checks` is how an older project asks
      again. **Seams, never pairs, and deliberately unthresholded** — a floor is
      the obvious improvement and it is wrong twice over. HISTORY.md § The
      overlap scan.
    - **What removes one is `unspoken`, and the witness is the render — never
      the seam and never a reading.** A mark is `(clip_id, word_index, text)`
      and **never touches the transcript file**: renumbering would move every
      cue. `_spoken_transcripts` is the one derivation, feeding captions,
      `caption_view` **and `verify`** — which reports the count beside its
      diff, or a mark becomes a way to make a real miss disappear.
      - **The seam scan sees only one of the two mechanisms.** The other is a
        *fragment*, a cut that left a sliver of a real word — drawn whole,
        inaudible, no overlap to find. `unspoken_detect` takes candidates from
        both and lets the render decide, so its kept-share floor **asks and
        never decides**; `apply` is off by default, like `reframe_detect`.
      - **A stale mark is kept, never applied** — recorded text ≠ current text
        means the transcript was replaced under it, and a word wrongly drawn is
        visible to anyone watching while a real word dropped is invisible to
        every check proofcut has. HISTORY.md § The teaser, re-cut.
- **`vfr` is recorded at import and reported, never acted on** — on
  `import`'s return, `assets`/`properties`, and `finish_report`'s `sources`,
  **informational and never a flag**, since the lean is not to transcode and a
  permanent flag is a count that can never reach zero. The signal is
  `r_frame_rate` vs `avg_frame_rate` at a 1% tolerance: **zero false positives
  over twelve real files here**, but the tightest true-CFR margin measured is
  0.33%, so that tolerance clears real material by ~3× and not 100×.
  Normalising at NLE export is still open. HISTORY.md § The VFR probe.
- **A clip's `duration` is its container's, which ends with the longer
  stream, so no span may reach past `_timeline_bound`** — the sooner of it and
  `picture_end`. Audio half a frame past the video, or auto-editor counting
  past a phone's stretched last frame, seeds one frame the video has not got:
  black, with `frames` agreeing, since it checks against the timeline. `seed`
  and `restore` go through it. HISTORY.md § The phone's black last frame.
- **A frame count comes from `autoeditor.frame_layout`, never from the
  duration.** Each segment edge quantises on its own, so `sum(dur)` and
  `round(edit.duration * fps)` are different numbers and the first one is the
  timeline that gets exported. Related, and measured rather than assumed:
  auto-editor's `--export kdenlive` output is **one frame longer than your
  edit, and the frame is black** — MLT's `out` is frame-inclusive and
  auto-editor writes a frame count into it. `export --render` does not have it.
  HISTORY.md § `check_frames`.
  - **Read back, `out` is the last frame *index*, so a range's exclusive end is
    `out + 1`** — and getting this wrong is invisible on any one range. The
    hand-parse that brought the Scream retake cut in read it as exclusive and
    lost a frame off the end of all 63, shipping a film 2.098s shorter than the
    `.kdenlive` says it is. `mlt.read_ranges` is the only reader; `import_edit`
    is the only caller, and it checks its own total against
    `mlt.declared_length` — a Kdenlive document states its length in three
    places and a misread disagrees with all three at once. **A multi-track
    `.kdenlive` is refused, not preferred-down to one track**: four of the
    Scream project's fourteen are assemblies with a real picture track, and
    picking a playlist would import half an edit at exit 0. HISTORY.md § The
    import that was one frame short, sixty-three times.
    **A `silence` entry is runtime too, and imports as generated silence**
    (`ImportedRange.silence`, registered only on a write) — skipped as "not
    media", Scream's 1.5 s head of silence closed and every cue landed 1.5 s
    early, `declares_otherwise` the only trace. `declares_otherwise` is named,
    never refused, so **read it**: non-empty means the import is not the
    document's film. HISTORY.md § The Scream native rebuild.
- **`melt` is inside the Kdenlive flatpak, and that flatpak cannot see
  `/tmp`.** It has no host package here; resolve it through
  `picture.melt_command()`. Pointed at a project under `/tmp` it prints
  `Failed to load` and **exits 0**, so its exit code proves nothing — check its
  output. Anything writing a project for melt to read puts it under `$HOME`,
  and that includes what it *writes*: `picture.render` stages into
  `~/proofcut-render/` and copies out only after the file agrees with the timeline.
  - **Concurrent flatpak launches can lose a startup race** — stderr says
    `… has invalid merge-dirs`, melt never ran, and the call reads as "melt
    printed no timeline". `picture._retrying_launch` retries only on that
    line; a new melt call site goes through it. HISTORY.md § The suite in
    two minutes, and the flatpak launch race.
  - **Every melt `subprocess.run` passes `stdin=subprocess.DEVNULL`.** With
    its output captured and a console on stdin, melt writes the whole file
    and never exits — a person's Windows PC hung on it while CI, which has
    no console, rendered in 4 s. A test holds the argument, since nothing
    here reproduces the hang. HISTORY.md § The render that never exited.
  - **A *failed* render's staging directory survives on purpose, and
    `sweep_scratch` drops it after `SCRATCH_RETENTION_DAYS`.** It sweeps by
    name (`_SCRATCH_NAME`), never by age alone — a hand-placed directory in
    that root would otherwise go — so **a new `scratch()` prefix that is not in
    that pattern is never swept**, which is the unbounded growth this replaced.
    HISTORY.md § The staging directories nobody swept.
  - **`WAYLAND_DISPLAY` alone is not a display.** It names a socket that Qt
    resolves under `XDG_RUNTIME_DIR`; with only the name, melt aborts printing
    nothing and the empty output reads as an unloadable project. Go through
    `picture.display_env()`, which exports both — and note the environment it
    is compensating for is the **MCP stdio transport's**, which passes HOME,
    PATH and little else. HISTORY.md § Rendering through `melt`.
    - **And a session with no desktop at all renders under
      `QT_QPA_PLATFORM=offscreen`** — measured 2026-08-23 with the seat at the
      greeter and no `wayland-*` socket anywhere: a red PNG through a `qimage`
      producer came back red, so `picture.render` accepts a headless Qt
      platform (`qt_is_headless`) where it used to refuse for a socket it did
      not need. The Kdenlive flatpak ships **no** `xvfb-run` — a goodsometimes
      note claiming its `melt` wrapped one was wrong about the mechanism — so
      this is the route for every unattended render. Set it alongside
      `DISPLAY`/`WAYLAND_DISPLAY` only if you want; it is sufficient alone
      **for the flatpak's MLT, and not for every build**: Ubuntu 24.04's
      packaged MLT 7.22 ignores it, wants X11, and dropped a 9:16 crop at
      exit 0 with every frame counted and agreeing. So a headless-only render
      is gated on `picture.qt_draws` — a one-frame `qtblend` probe judged by
      two pixels, never by the variable — and `xvfb-run -a` is that build's
      route. HISTORY.md § A stranger's install, on a clean Ubuntu.
      - **On Fedora, `melt` is not melt.** The `melt` package is freeze, a
        compression tool, and MLT's `mlt` package installs only `mlt-melt` and
        `melt-7`, whose banners name themselves — so `picture.MELT_NAMES`
        searches the unambiguous names first and doctor's banner match takes
        all three. Its default `ffmpeg-free` also has no `libx264`, which every
        render asks for; doctor's ffmpeg row checks the encoder list.
        HISTORY.md § A stranger's install, on a clean Fedora.
      - **`systemd-run` on PATH is not a usable memory cap**: with no user
        session bus (a container, CI, SSH without a login) the scope fails
        before melt starts and read as "melt rendered nothing".
        `picture.user_bus` decides, and the render runs uncapped with a note.
        Its bus may be logind's `/run/user/<uid>`, which `systemd-run` never
        finds on its own, so a capped render exports that `XDG_RUNTIME_DIR`
        — without it every headless stdio render died the same way.
        HISTORY.md § The capped render with no runtime dir.
    - So **a session with no desktop behind it cannot run the melt-rendering
      tests in `test_server_stdio.py`** (`grep -c '@needs_melt'`; a count
      written here went stale three times) — `export` refuses with "no
      display for MLT's Qt module to open", correctly. **Exporting
      `QT_QPA_PLATFORM=offscreen` to pytest does not help**: the SDK's stdio
      client hands the server only `get_default_environment()`'s allow-list,
      and the variable is not on it (measured 2026-09-17, when the session
      ended mid-run). `tests/conftest.py` widens
      `mcp.client.stdio.get_default_environment` to pass the variable
      through when it is set, so `QT_QPA_PLATFORM=offscreen pytest` runs
      all of them headless. Failures there and nowhere else are
      the environment, not a regression; confirm with an existing melt test
      as the control.
- **A caption's look is project state (`caption_style`), and ASS is never
  written by hand** — three of its fields mean the opposite of what they read
  as, `\k` is a left-to-right fill rather than a per-word step, and grouping
  is part of the look, so it is stored with it. `captions.resolve` is the only
  translation and `ops._caption_cues` the only derivation. HISTORY.md § Caption
  styling has each trap and the measurement behind it.
  - **`caption_style` says what a burn *would* draw, never that one happened.**
    `export --render` does not burn captions; `captions --burn` is a separate
    opt-in step and nothing reports a render made without it. So a manifest,
    `caption-view`, `verify` and `check_frames` can all agree about captions
    that are not in the file — the finished cut carried none for three days on
    the strength of a status line. Settle it by reading the render's pixels.
    HISTORY.md § The film had no captions in it.
    - And a legible one is a further question: over the light cards white
      captions measure **1.10:1**, held together only by the outline, while
      `verify` stays clean because the render does match the timeline. A box
      (`--box`) buys 20.87:1 and costs clean edges, since libass draws one box
      per override block and `\k` makes one block per word.
  - **The font named in a style may not be installed** — libass substitutes
    silently and ffmpeg still exits 0, and **a card's SVG has the same hole**:
    it rasterises pixel-identically whether the face exists or not.
    `captions.font_match` reports both; nothing prevents either. Which fonts
    this box has: wiki `tooling.md` § Fonts.
    - **CSS weights are not fontconfig weights** (its Bold is 200), so an
      unmapped CSS value is above every real one and every query answers the
      heaviest face installed — go through `CSS_TO_FC_WEIGHT`, and escape the
      family, because `fc-match 'Foo-24'` reads the tail as a point size and
      calls a missing face installed. `font_report` does both and reports
      `drawn_style`, since two faces of one family share a family name;
      `captions.font_match` asked bare does neither, deliberately (libass takes
      a bold *flag*, unmeasured here). **Settle which face draws by measuring a
      render, never by `fc-match`.** HISTORY.md § The emphasis-capable quote
      slot.
    - That rule is not a caution, it is a **disagreement**: `font_match` asks
      fontconfig and libass asks something else, so a clean `resolves_to` is
      not a claim about the burn. Two styles `fc-match` calls identical render
      3593 RMSE apart here, because libass's first pick for `Noto Sans` on this
      box is a **Nerd Font symbol face** and it only reaches the real one by
      failing to find `E` — the Latin glyphs then agree and the primary font
      still supplies the space advance. `ffmpeg -v verbose` prints every
      `fontselect` line; a caption font that resolves in one pick with no
      fallback is the only kind that has been settled. HISTORY.md § The
      approvals round, answered.
    - **A Windows burn gives libass its own font directory
      (`fonts.libass_fontsdir`), and a variable font cannot go there**:
      libass names a face in it by name ID 1, so `Outfit[wght].ttf` registers
      as `Outfit Thin` and `Outfit` falls through at exit 0. It stages the
      static pair in `fonts/static/`, which `vendored()` never scans so
      fontconfig never sees it. HISTORY.md § The first windows-demo run.
- **Cards rasterise through `magick`, and the size knob goes *before* the
  input.** `-size` is a vector render and **fits, never distorts**; `-resize`
  after the input resamples the pixels and wrecks text, so `render_svg` has no
  resize path and a card is *authored* at the canvas — which is why `card_new`
  defaults its canvas to `_mlt_resolution`. Never hand melt the SVG: it goes
  through Qt, not librsvg, and the two disagree with no error on either side.
  Templates escape every user value and insert only proofcut's own markup raw.
  HISTORY.md § The card renderer, § Card templates.
  - **A wrap is measured or there is no wrap** — `fill_template(flow=True)`
    renders each candidate through `render_svg`'s own coder, `flow=False` is
    the older newline-only contract, and a character count is unsafe in the
    one direction that overflows. The two traps are in the *measuring*: the
    scratch canvas is the entire cost of it, and it **clips rather than
    errors** when too small — a clipped line measures narrower and ends the
    wrap early. A slot that overruns its box is **refused; the card never
    grows to fit it.**
    - **Every placed text slot is measured too, and the unit is the `<text>`
      element rather than the slot** — a placed slot declares `kind: "line"`
      or it is named in another slot's `parts`, there is no third state, and
      a test holds every template to it. The element is the unit because a
      title is drawn beside things that take width: the year is a flat 229 of
      `receipt`'s 1640-unit box, so a title measured alone is measured against
      a box something else is standing in. A line has no wrap to fail, so an
      unmeasured one runs off the frame at `magick` exit 0 — measured, the ink
      of one such render spans the full 1920 with both margins gone. The box
      is derived from the anchor (`1920 - 2x`, `2x - 1920`, or the margins),
      never declared free-hand. HISTORY.md § The measured line.
  - **Per-run `<tspan>`s eat the whitespace between them**, so `the
    [em]perfect[/em] horror` draws as `theperfecthorror` at exit 0.
    `xml:space="preserve"`, once per line — it inherits. Both:
    HISTORY.md § The emphasis-capable quote slot.
    - **A `line` slot takes the same vocabulary, and its two rules are the
      opposite of a flowing slot's**: a value with no marker takes the plain
      path byte-for-byte (or a sweep reports every card redrawn), and an
      unmarked run inside a marked value declares nothing, because the
      `<text>` element already sets fill and weight. Emit no positional
      `x`/`dy` — half of them are `text-anchor="end"`, where an `x` opens a
      second chunk and moves the line. And **`line_parts` carries the run's
      weight into the measurement**: `[em]` is a weight change too, so
      stripping markers without it under-measures the emphasised fragment,
      which is a line slot's only guard. HISTORY.md § The brand mark on a
      line slot.
  - **A per-aspect layout is a variant *file*, resolved from the canvas —
    never a second template name.** `receipt` at a tall canvas draws
    `receipt.portrait.svg`; a `receipt-portrait` template would make an aspect
    swap rewrite the recorded template, and the record would stop saying what
    the card is. A variant declared with no file refuses rather than falling
    back — the fallback is the pillarboxed card the variant exists to remove,
    at exit 0 — and a variant file the manifest does not declare refuses too,
    because nothing would ever draw it and nothing would say so. Its geometry
    is declared beside it and reaches both the drift guard and the wrap
    through `_declared_slots`: a portrait file measured against landscape
    declarations overruns its box at `magick` exit 0. HISTORY.md § Variant
    resolution.
    - **Every slot fitting its box says nothing about whether the layout
      reads, so a variant is watched before it is called done**, and the
      measurement is an ink-band profile down the frame, not the slot table.
      The portrait reveal passed every budget and drew its year alone in the
      724px under the note — a cluster and an orphan, where the receipt is a
      cluster and a margin. **The reserved edges are the bottom fifth *and*
      the right side**, which is why the portrait wordmark is bottom *left*
      and the landscape one is not; the platform numbers are in
      `BASE_GEOMETRY`'s comment. HISTORY.md § The orphaned year.
  - So **a card is re-authored, never resized**: `card_new` records
    `(template, slots, canvas, variant)` and `card_reauthor` fills the template
    again at the project canvas. **The variant is on the record because a
    variant shipping changes what a canvas draws without changing the canvas**
    — a canvas-only sweep answered `redrawn: 0` over twelve cards that were all
    still the old layout, and reported the project up to date. It is additive
    and optional, so absent means none, which is what every older record meant.
    HISTORY.md § The portrait cards. A card with files but no record cannot be
    re-authored by anything — it is reported, never guessed at. The twelve in
    `~/proofcut-work/projects/final-cut/proj` **are recorded** (all thirteen with the outro, at
    1920x816 — verified in the manifest 2026-08-17); `~/proofcut-work/projects/cards-reauthor/`
    keeps the slot tables and `reauthor.py`, the recovery route if a copy
    without records ever resurfaces. All twelve author at 9:16; the two that
    refuse at 2.35:1 are a 16:9-only content fit. HISTORY.md § The card record,
    § Step 6 of the aspect swap, watched.
- **An overlay is a card with no background (`graphics.is_overlay`), drawn
  over the film by `OVERLAYS_KEY` — `_is_layered`'s ninth trigger — and it
  goes over nothing else.** An opaque card is refused as an overlay (it
  covers the film for its whole span) and an overlay card is refused as a
  cue, tail or head (`_resolve_asset`: its type over black). The span is a
  word/event address resolved through the `Edit` every build, never
  seconds, and **list order is the stack**. Two writer rules, both measured
  (`~/proofcut-work/spikes/overlay-probe`): the entry reads its still from
  frame 0, because `rect` keys count from the producer (the A2 fade trap);
  and **only a moving key is drawn off 1:1** — a 1:1 rise snaps to whole
  rows, and a nudged resting key leaves the type sub-pixel off and
  resampled. `reframed_nodes` skips `ochain` nodes, whose filter carries a
  `rect` and is not a reframe. **The preview's `#overlay-layer` mirrors
  `mlt.ease_fraction`** — change a curve in one and the window stops
  drawing the render. A frame melt composites with an overlay reads ~2 luma
  levels brighter all over, so judge an overlay against a baseline frame
  inside its span. HISTORY.md § Overlays, built.
- **A one-shot sound (`SOUNDS_KEY`, `_is_layered`'s tenth trigger) never
  plays its own file — it plays a padded copy from `cache/sounds/`.**
  - melt has two exit-0 traps for a short file: a file it counts as one
    frame long plays nothing, and an entry claiming more frames than its
    file has moves every later hit on the lane early.
  - So a copy is whole frames, at least `mlt.SOUND_MIN_FRAMES`, and its
    leading silence places the hit between frames. Frame f starts at
    `floor(f × 48000 / fps)`, measured on both melts.
  - An `every` run's dice are drawn for every occurrence before any is
    skipped, so a cut re-rolls no other hit.
  - Judge a hit by the render's PCM, never by the document.
  - HISTORY.md § Sounds on events, built.
- **A retime (`RETIME_KEY`, `_is_layered`'s eleventh trigger) is a warp
  from render time to Edit time, and only the edit and picture lanes are
  remapped** — one `timeremap` chain per entry, keyed from 0; everything
  else is planned in Edit seconds and placed through `_Clock`, keeping 1x
  lengths. HISTORY.md § Retime, built.
  - **Never `~` in a `time_map`**: it ran a launch-clip map backwards at
    exit 0. The curve is `retime.Warp`'s PCHIP, sampled per frame, and never
    a `length` on a remapped chain, which freezes it on frame 0.
  - **Keys on a remapped chain count render frames** — a reframe `rect` or a
    `volume` level goes through `mlt.Placement`, never `seconds * rate`.
  - **Every map ends one key past its entry's last frame**, or that frame
    plays silent; **the mute is down a frame early**, since MLT ramps a
    level across the frame carrying its key; and **short 1x runs between
    muted ones are bridged**, or the ramp keys collide and the floor is lost
    across a whole muted span. Judge a mute by the render's PCM on every
    muted frame, never by the keys.
  - `cut_by_time` and `reel` read their seconds through the warp. A hold and
    film audio under the VO refuse a retime, both ways.
- **An inset (`INSETS_KEY`, `_is_layered`'s twelfth trigger) is its own
  track, never a nested tractor** — a tractor composites at the profile's
  size, so composite-then-frame kept 38% of the render's detail. Its
  `qtblend` copies the host Edit entry's camera keys, same positions and
  operators with each rect mapped (`mlt.inset_rect`); re-sampling the curve
  was worse. HISTORY.md § Insets, built.
  - **The rect filter hangs on the inset's PLAYLIST**, whose keys count
    render frames from 0; on the chain, a camera key before the inset's
    in-point needs a negative position, which MLT counts from the end.
  - **Fade and dim are `brightness` alpha**, never keys merged into the
    rect, which would bend the camera's curve.
  - **Judge an edge in a yuv420p render by luma**: chroma is half size and
    moved the measured edge 1.8px on a render that was right to 0.72.
  - **Lossless-RGB sources drawn smaller than themselves shift colour** in
    melt, on every route; yuv420p ones do not. A colour finding on an RGB
    fixture is the fixture until a yuv one agrees.
- **A channel preset pack is a snapshot, never a live reference to a sibling
  repo's file.** `pack.load_pack` resolves one external JSON file (palette,
  fonts, mark, caption presets, weights) once; `pack_apply` writes the fully-
  resolved payload into the manifest's `pack` key and hashes it — `pack_hash`
  is sha256 of the *resolved* payload, not the file's bytes, so a whitespace
  reformat upstream cannot trigger a spurious re-author sweep. No schema bump —
  `pack` and a card's
  `pack_hash` are both additive-optional, the `CANVAS_KEY`/`CAPTION_STYLE_KEY`/
  `TAIL_KEY` precedent. `pack_apply_captions` is a separate op from
  `pack_apply` on purpose, so activating a pack never silently overwrites a
  hand-edited `caption_style` underneath it. HISTORY.md § The channel preset
  pack, built.
  - **A font that draws correctly and a font that is vendored are two
    different findings, and only one of them refuses.** `fonts.probe`
    reporting `drew: False` refuses unless `allow_fallback` (which then
    records the fallback rather than applying it silently); `drew: True` on an
    unvendored face is *not* refused — the render on this box is genuinely
    correct — but is permanently marked `font_provenance: "unvendored"`, so a
    project depending on a font nobody ships can say so without re-probing.
    Zilla Slab is exactly that case. Treating either check as standing in for
    the other misses what it alone catches.
  - **Safe zones are report-only, on `SCENE_THRESHOLD`'s own precedent.**
    `graphics.SAFE_ZONES` is `BASE_GEOMETRY`'s comment turned into data (the
    per-platform bottom bands, worst case 384px — the bottom fifth of 1920 —
    each with the right-hand action rail), and `card_safe_zones` reports ink
    inside the band **and**
    in a same-area sample outside it **and** against the card's own recorded
    background — three numbers, never one, because a brightness bbox has
    already misread a black source as a black bar twice in this repo (see the
    auto-framing detector below). No floor, no `--strict`: a threshold gets
    pinned by looking at real output, not picked cold.
- **Footage follows a canvas change by cropping, and the crop is a rect in
  *source* pixels stored as asked** — refit whenever the canvas moves, so
  neither a cut nor a swap can invalidate one. **A rect is addressed
  `(clip_id, src_start, rect)`, so framing is per *shot*, not per clip** —
  `src_start` absent is the window from the head of the file, which is what
  every older rect meant, so it is not a schema bump. It renders as **discrete
  (`|=`) keyframes on one `qtblend` filter, numbered in the producer's own
  source frames** — one node per resource per role still, measured. And the
  reviewing tool is not optional: a wrong window **reads as framing in
  motion** (2 of 15 hand numbers, twice now), so judge one on
  `reframe_sheet`'s drawn-on-the-source-frame tiles, never on a watch.
  HISTORY.md § Per-shot framing.
  - **A sheet row is a window shown, not a placement** — sampling placements at
    three fixed fractions never looked at 14 of the vertical's 55 windows,
    eight of them hand-approved. Each placement is split at the boundaries it
    crosses and `moments` are fractions of the stretch showing that window, so
    `count` is windows (58 on the vertical against 25 placements, and all 55
    stored windows drawn) and `placements` is placements. **The edges take a
    frame of tolerance, never an epsilon** — the same rule `steps` needs below,
    for the same 30µs. `windows` on a row is
    still the *placement's* count (the preview/render tell); `window` is the
    address `reframe --src-start` takes. HISTORY.md § The thirty-nine windows,
    reviewed; § The three gaps, closed.
    - **And a window it *does* sample can still pass while badly wrong**, so a
      clean sheet is not an approval of the span: a rect is a claim about a
      stretch, a tile is evidence about one instant, and a static rect over a
      moving subject has a best instant. HISTORY.md § The tile that made a
      wrong window look right.
      - **`--extremes` is the answer, and it is opt-in**: the rect does not
        move inside a stretch, so the worst moment is at the subject's own
        leftmost or rightmost by construction — three tiles, worst first. It
        costs `PROOFCUT_FACE` and ~0.5s a probe. **Its probe grid contains the
        fixed fractions deliberately**: probing at a rate finds the extreme of
        the *sample*, and without them it was worse than the default on 5 rows
        of 16. **Read `worst_offset` beside `multi_face`, never after it** —
        the subject is area-weighted over every face, so a two-face frame puts
        it between them where nobody is, and the teaser's largest offset (608px)
        is exactly that. HISTORY.md § The sheet samples where the subject is.
  - **A slide drawn at exactly 1:1 snaps to whole pixels.** `qtblend` draws a
    pure translation on the integer grid, so a 30px pan over 10s moved in 1px
    jumps every tenth frame, while any other scale — 0.75, 1.333, 2561/2560 —
    moved within a quarter pixel of the line. A 1080p crop of a 1440p screen
    recording is exactly 1:1, so `mlt._off_unity` draws a slide's own key a
    pixel larger; a held window keeps its exact rect. Judge motion by an edge
    position read back per frame, never by the document. HISTORY.md § Events,
    and the pan that snapped to whole pixels.
  - **A window's `interp` is `true` or an easing name, and `event` rides
    beside `src_start` as provenance.** Read `interp` for *whether* it slides
    and `mlt.Reframe.ease_at` for the curve. `reframe` rebuilds every record
    from the geometry tuples on each write, so a new record field has to be
    carried across that rebuild the way `event` is (`addressed`), or the
    next unrelated edit drops it at exit 0. HISTORY.md § Eased slides and
    event-addressed windows.
  - Its two asymmetries: the **preview** places a shot by the window at its
    `src_start`, so a boundary *inside* a placement previews as the first of
    the two while the render steps mid-shot correctly (`reframe_sheet`'s
    `windows` count is the tell); and `timeline_view`'s `reframe[clip].dest`
    is the **head** window, which is the edit track's answer — the picture
    layer draws the shot's own `dest` and reading the clip entry there is the
    bug. An override is a **floor**: a
  rect that is not the canvas's shape is grown to it, never shrunk into it,
  because shrinking cuts the subject in half. **A still is never cropped** (a
  card is re-authored) and no filter is emitted where MLT's own placement
  already matches — which is what keeps an unswapped project's document
  byte-identical. The trap is that `mlt.py` writes one node per resource **per
  role**, so a reframe applied per resource crops a file on one track and
  letterboxes it on the other, in the same frame, at exit 0. HISTORY.md § The
  MLT reframe.
  - **A window can hold a second rect (`pane`), and then it draws as a stacked
    split** — two half-height panes, a second node of the same resource,
    nothing new in MLT. **Both rects are grown to the *full source height*,
    never merely to the pane's aspect**: nothing masks a pane, so a crop
    shorter than the source scales the frame past its own pane and into the
    other one at exit 0. That full height is the only thing holding the halves
    apart. The pane node is switched off by **opacity 0 keyed at every window
    boundary** — a step not written is a value that carries on — and the sheet
    draws the lower rect dashed, which is where a split gets judged. It fires
    on 10 of the film's 79 windows at the 0.15 floor — **a count like that
    moves with the floor rather than describing the film**, since three moments
    spanning 8s agree far less often than three spanning 2s, and six of the ten
    sit on windows the old floor also had. **`reframe_detect`'s `faces` is
    detections summed over the sampled frames and is not a subject count** (33
    is eleven people), `subjects` is. HISTORY.md § The stacked split, built;
    § What the re-pin did to the detector.
    - **Judge a split on how much its panes overlap each other**, and the
      number is `pane_overlap` — on the proposal and on the sheet row, where it
      used to be worked out by hand off the two rects. The film's separate at
      23–30% (distinct groups) against 52–63%, where the same face is in both
      halves and stacking shows it twice, with 40–45% the band that is
      neither; **4 of its 10 proposals are the duplicating kind**, 3 of them
      predating the 0.15 floor. Reported, never enforced. HISTORY.md § The thirty-nine windows, reviewed; § What the
      re-pin did to the detector.
  - **A window can be blur-filled (`fill: "blur"`)**: the whole source
    contained, over a blurred, darkened copy covering the canvas, on its own
    node role (`fchain`/`fvchain`) and track *under* its lane, switched by
    opacity at every window boundary like a pane. Its record's `rect` is the
    whole source, so every reader of the stored window still works. Three
    traps, each measured. **The blur is `box_blur`, never an avfilter blur**:
    a pixel sigma is 3x as strong on a third-size canvas. **`box_blur`'s
    radius 100 is 10% of the image *width***, so `FILL_BLUR = 12` is 1.2%,
    not the 12% it reads as. **CSS `blur()` fades an element's own edges into
    transparency**, and a covering background's edges sit on the frame's, so
    the preview grows the element by 3σ or draws a dark band that melt does
    not. PLAN.md § Blur-fill; HISTORY.md § Blur-fill, built.
  - **An export preset never sets the canvas — `tiktok-reels` *checks* it and
    refuses.** The obvious build is the wrong one: a flag that reshapes the
    project is an export argument rewriting project state, the same failure as
    picking the writer from an argument, and it leaves a swapped manifest
    behind after a render nobody kept. `PRESET_ASPECT` is a claim a preset's
    *name* makes, tested cross-multiplied against `_mlt_resolution` (a float
    ratio refuses the one shape that is exactly right), and an audio-only
    project is refused *before* it, or the message quotes the 1080p fallback
    as if it were the project's frame. HISTORY.md § `tiktok-reels`.
  - **A brightness bbox answers "where is the bright part", never "where is
    the frame."** It has now misread the same render twice — once as worse
    than a pillarbox, once as a pillarbox — because the footage sampled was a
    dark scene and then opening credits on black, and a black *source* reads
    exactly like a black *bar*. Settle frame geometry by comparing against
    ffmpeg's own crop of the source, and against the wrong hypothesis too:
    0.9 vs 20.8 of 255 is an answer, either number alone is not. **As a
    *framing* signal it is worse than not asking** — scored against the
    approved windows the luma centroid loses to the centre crop it would
    replace (0.545 against 0.568), and loses a subject too. Faces are what
    beats it. PLAN.md § The auto-framing detector.
  - **`reframe_detect` proposes and never frames**: `apply` is off by default,
    the opposite of `cut --plan`, because the pass is 114px out on a 459px
    window and 2 of 15 hand numbers were wrong invisibly — judge it on
    `reframe_sheet`. It never writes over an existing override, and it is the
    *third* subprocess-behind-an-interpreter (`PROOFCUT_FACE`, with
    `_face_worker.py` shipped to be run and never imported). Its placement rule
    is sound — reviewed one window at a time, all 39 on the film are right for
    the shot they were placed on — and **every defect found is coverage**:
    - **"Is this window already framed?" is a frame, never an epsilon.** ffmpeg
      reports a cut at 0.834167 where the manifest holds 0.8342, so exact match
      called 15 of 16 hand windows unframed *and printed both as `0.8342`*.
    - **A refused window is not a centre-cropped one.** Nothing is written for
      it, so whatever is in force carries over — at a clip's head the centre
      crop, anywhere else **the previous shot's framing**, which is worse than
      the default because a stale window looks deliberate. Not an edge: it is
      13.6s of `cold-open`, one rect held across four camera setups, Casey's
      framing sitting over trees and a stovetop. `falls_back_to` names which;
      never infer it from `refused`.
      - **`reframe_detect` answers that of a proposal, `reframe_coverage` of
        the project on disk** — scene cuts against stored geometry, so it needs
        no face detector. **Its unit is the placement, not the cut**: a
        placement can *begin* downstream of the cut that stranded it and hold
        no cut at all, so walking each placement's own cuts misses exactly
        those — 6.0 of `cold-open`'s 13.6 stale seconds. Read `stale_seconds`
        (an override held across a cut), never `default_seconds` beside it (the
        centre crop, a different thing). HISTORY.md § `reframe_coverage`.
        - **"Needs no face detector" is not "cheap."** `reframe_coverage`
          decodes placed footage for its scene-cut scan — 5.7s wall, 46s of
          CPU on the film, uncached, every call — so nothing re-read on every
          `project-changed` may compose it in. `finish_report`'s `framing` is
          opt-in for exactly that (`framing=True`, `?framing=1`,
          `--framing`), and `None` when unasked, distinct from a measured
          zero: "nobody scanned" reading as "nothing stale" is the
          captionless-film shape again. HISTORY.md § Frame mode.
          **`frame.js` broke this rule from the other side** — its own
          `update()` runs on every `project-changed`, so every cut paid 5.5s
          of decoding for a hidden pane. A pane's work rides being *looked
          at*: `app.js`'s `setMode` emits `mode` for that. HISTORY.md § The
          two console 400s.
          - **And the converse: work a hidden pane could only do WRONGLY has
            to be redone when it is looked at.** A hidden pane measures 0, so
            `computePxPerSec`'s `clientWidth || 800` laid every lane out
            against a width no pane has — triggered by a `project-changed` or
            a `resize`, never by a mode switch, which is why a mode round-trip
            does not reproduce it. `timeline.js` re-renders on the same `mode`
            event, **only when the width differs** from `laidOutWidth`, since
            a rebuild repaints the waveform canvas. Anything a lane's geometry
            is derived from is in this class. HISTORY.md § The timeline
            re-measures when Edit is looked at.
        - **A blank chip where a warning would go reads as "nothing to
          report."** Frame mode's coverage chips drew empty for the ~4s the
          sheet job takes, which is indistinguishable from a clean project —
          the one thing this view must never say by accident. A slow check
          has to say it is running ("scanning for cuts…") or its silence
          reads as the answer. HISTORY.md § Frame mode.
        - **It also asks which windows have no cut (`steps`), and that is the
          one a viewer notices** — a boundary inside a continuous take reads as
          an edit that is not there, while the stale walk answers clean because
          nothing was held *across* a cut. Three rules, each measured: the two
          directions score against **different cut lists** (a cut must reach
          `threshold` to *demand* a window, only be detected to *explain*
          one); "interior" takes **a frame of tolerance**, a window placed at a
          shot boundary being the normal case and sitting ~1e-7 from the
          placement's own start; and **a near sub-threshold cut is not evidence
          of a missed one** — score the boundary itself. The first two are 13
          false findings of 15 apiece. HISTORY.md § The three gaps, closed.
    - **`SCENE_THRESHOLD` is 0.15, re-pinned 2026-08-12 by judging detections
      rather than by agreeing with the hand table.** The old 0.20 called a real
      cut in an unframed shot a false positive, which measured fifteen windows
      over three clips of nine. Every candidate the film shows was looked at on
      the frames either side: **all 31 from 0.141 to 0.244 are cuts, the first
      non-cut is 0.137**, so 0.20 was discarding 21 real cuts and buying
      nothing. A cut with no window is framing walked through, so the film's
      stale share going 6.3% → 28.0% is the reporting starting, not a
      regression. `tests/test_scene_threshold.py` pins it from both sides.
      HISTORY.md § The scene threshold, re-pinned.
    HISTORY.md § The auto-framing detector, built; § The thirty-nine windows,
    reviewed.
- **A clip being registered is not a clip being on the timeline, and
  `timeline_view` answers for one anyway — read `off_timeline`.** It reports
  rather than raises (`transcript_missing`'s policy), handing back the
  timeline's own segments under the `clip_id` asked for; a transcribed clip
  that is off the edit has every word `present: false`, indistinguishable from
  one cut in its entirety. The flag is the only thing separating them, and a
  front end must never re-derive it from `segments[].clip_id`. HISTORY.md
  § `off_timeline`, and the advice that made it worse.
- Anything that emits times *for playback* maps through the edit, never
  straight off the transcript. The transcript indexes the source; the timeline
  is what plays. See HISTORY.md § Captions came out of the timeline.
  - Singular vs plural: `Edit.timeline_span` stops at the first survivor
    (right for captions — one span per word); `Edit.timeline_spans` returns
    every surviving piece. A range a cut split has more than one answer, and
    the singular reports one without saying so. HISTORY.md § `locate`.
  - **Segments are half-open `[start, end)`, and that is wrong for exactly one
    thing: a zero-width word.** Whisper emits `start == end` often, and the
    last word of a transcript lands on the last segment's own end — where the
    half-open test says "cut" about material plainly still there. `closed_end=`
    on `timeline_time` is for *instants* only; passing it for one edge of a
    range double-counts the join between two segments. HISTORY.md § The head
    of the parity queue.
    - **That `==` needs a segment's end read as OTIO's rational end, never
      float start + duration**: 55.9 + 4.3 is 60.199999999999996, one ulp short
      of the next segment's 60.2, and a gap word ending on a splice's join
      resolved past the splice. `from_otio` reads `end_time_exclusive()`; any
      new reader of a `source_range` does the same. HISTORY.md § The
      Lambs/Longlegs native rebuild.
  - **Two clocks, once a head is configured: render time = Edit time +
    `head_seconds`.** `timeline_view`/`locate`/`status`/`caption_view` stay
    Edit-relative on purpose (0 is still the `Edit`'s own first frame) because
    the web player cannot play a cold open yet and shifting a view call's
    clock would desync it — they gain a `head_seconds` field (`_head_seconds`,
    0.0 with none) instead of shifting. A render-facing path must offset
    itself rather than read that field blind: `add_captions` shifts by
    `head_seconds` at its own call site, `verify` trims heard words before it
    (`head_words_trimmed`), and `finish_check` reports in `final`'s own
    absolute seconds throughout, `prepend_seconds` defaulting to the stored
    head's own length. Getting a caller's clock wrong here reads as a
    disagreement about *content*, not offset. HISTORY.md § The cold open is
    project state.
- **`Edit`'s addressing reads a cached `_SpanIndex`, so never mutate
  `edit.segments` in place** — assigning the attribute is what drops the index,
  and a stale one answers every lookup confidently and wrongly. All four
  mutators rebind (`remove`/`keep_only`/`restore`/`insert`); `restore` used to
  splice and no longer does. Its bisect's
  precondition is sorted **and disjoint** (an import can place the same source
  twice), and a clip failing it gets the exact walk. HISTORY.md § The scan the
  spike named was not the one that costs.
- **`Edit` never stored what it removed** — it is surviving segments and
  nothing else, so "what was cut" is derived (`Edit.gaps` against the clip's
  registered duration), never read back. `restore` is bounded by those gaps,
  which is what keeps the timeline a subset of the source and separates it
  from `vo_extend` (built — see the `TAIL_KEY` bullet above), the one
  mutator allowed to add source the recording never had.
  - **A dogfood project can be the wrong cut while every check passes.** The
    Scream project held the *silence-cut* VO, not the shipped one — 410.96s/73
    segments against 336.27s/63 — and the render, `verify`, the cue table and
    the shot plan all agreed with it. That was `~/proofcut-work/projects/final-cut/proj`, and it
    is **restored as of 2026-08-18** — 63 segments, all 38 shots projecting,
    `essay-flashfix.mp4` declared as its `reference` so `film_check` re-asks
    with no argument. `~/proofcut-work/projects/scream-v2` still holds the same stale edit,
    byte-identical; the shipped one is also in `brief-check`, `framing-detect`,
    `threshold` and `split-detect`. Settle any copy against the renders, which
    are 336.34s (`essay-cards-fixed.mp4`) and 342.36s with the 6s endcard,
    before building anything for review on it.
    - **The right edit is not the right film — `brief-check`'s cue table is
      not the film's, 27 of its 38 cues naming a different asset.** It is
      where § Choosing the b-roll was measured and the experiment stayed in
      it, and it is the *only* 336s project at the film's own 1920x816
      canvas, so it is exactly what a rebuild reaches for. Every check passes
      on it. **Diff a cue table against more than one project before trusting
      it**: the three vertical projects agree with `final-cut` 38/38.
      HISTORY.md § The film's project, restored.
    **And carry derived state back off a scratch copy** — the ten card records
    were written on the 411s copy, so the film's own project read as having
    none. Four instances now — the newest is `~/proofcut-work/projects/kf-probe`, which is
    `framed-teaser` rather than the shipped teaser, so its two renders are an
    A/B of each other and **neither is a control**. `film_check` is the cheap
    way to ask. HISTORY.md § The VO the project was holding, § The keyframed
    move.
