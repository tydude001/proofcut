# proofcut — the manual

Every capability, walked in the order a project meets them, with the rationale
beside each behaviour. The short version — what proofcut is, what it needs
installed, and the quickstart — is the [README](../README.md); the design and
the dated evidence behind everything here are [PLAN.md](PLAN.md) and
[HISTORY.md](HISTORY.md).

## `doctor` and `setup` — the tools proofcut drives

proofcut does not cut, render or transcribe anything itself: it drives ffmpeg,
whisper, auto-editor and MLT's `melt`, and checks what they produced. So the
first command on a new machine asks whether they are there.

```sh
uv run proofcut doctor
```

It probes every one of them and prints the fix under each ✗ rather than only a
cross. Three things it does that a `which` would not: it judges `melt` by its
`-version` banner and never its exit code, because Windows ships an unrelated
WiX `melt.EXE` and Fedora's `melt` package is a compression tool; it *runs*
whisper rather than finding it, because a venv that has lost torch resolves
fine and dies minutes into a job; and its Display row renders a real probe
frame, so it can tell you whether your MLT draws under
`QT_QPA_PLATFORM=offscreen` or needs `xvfb-run`. It reads your machine and
writes nothing.

An optional capability that is absent is reported "unavailable", never as a
failure — everything else works without it. Doctor also lists any `LUCID_*`
environment variable still set beside its `PROOFCUT_*` name, because no
resolver reads the old name any more and a stale config file would otherwise
lose what it configured at exit 0.

### `setup` — filling in what doctor crossed

On Linux, Windows and a Mac of either kind, `setup` installs whatever doctor
marked ✗, into your own user account, with no sudo or administrator rights. It
installs nothing doctor passed: a working ffmpeg or `melt` of your own is never
touched and never upgraded behind your back. Every route but Apple silicon's has
a person's run behind it; that one has CI's own arm64 runner, which is what the
Mac route spent a week waiting on a stranger for (HISTORY.md § `proofcut setup`
on Apple silicon).

Read the plan first. `--plan` prints every piece, its version, its size and the
doctor row that asked for it, then stops:

```sh
uv run proofcut setup --plan
```

```
proofcut setup — installs into /home/you/.local/share/proofcut/deps

Will install
  ffmpeg — n8.1.2 (BtbN autobuild-2026-08-31-13-27), about 126 MB
      because: ffmpeg is not on PATH. ffprobe is not on PATH.
  whisper — openai-whisper (uv tool, Python 3.12, cpu torch), about 1.9 GB
      because: whisper is not on PATH.
  auto-editor — 31.6.0, about 46 MB
      because: auto-editor is not on PATH.
  melt — Shotcut 26.8.1 (melt 7.41.0), about 155 MB
      because: melt is not on PATH.
  total: about 2.2 GB
```

`--plan` writes nothing, and a test says so
(`test_setup_plan_writes_nothing_and_exits_by_what_is_missing`), so reading the
plan cannot turn into performing it. `--json` gives the same answer as a
structured document. Then:

```sh
uv run proofcut setup           # reprints the plan and asks once
uv run proofcut setup --yes     # for a script, with no terminal to answer on
```

Everything lands in one folder — `~/.local/share/proofcut/deps`, or
`%LOCALAPPDATA%\proofcut\deps` on Windows — except whisper, which is a uv tool
because that is how whisper ships. Two resolver details worth knowing, both of
them the reason the folder exists at all: `melt` and auto-editor are resolved
from setup's folder *ahead of* PATH, because a `melt` symlink loses to Fedora's
`/usr/bin/mlt-melt`, which draws nothing headless; and ffmpeg is a
`~/.local/bin` symlink instead, because whisper's own `audio.py` calls `ffmpeg`
bare. Neither half can be the other's shape. On Windows, where a symlink needs
Developer Mode, the two ffmpeg binaries are moved into `~/.local/bin` and
recorded by SHA-256; uninstall removes one only while it still hashes the same.

Every download is pinned by URL and SHA-256 and bumped by hand, never resolved
from a `latest` tag — BtbN deletes its dated daily ffmpeg builds after a few
weeks and keeps month-ends, so the pin is a month-end build. A download that
does not hash to its pin leaves nothing behind, and neither does a `melt`
missing the desktop libraries it links against: you are told which ones.

### Taking it back out

Every link, file, folder and uv tool setup added is recorded, and `--uninstall`
removes exactly those and nothing else — including the Python uv downloaded for
whisper. A link you have since repointed yourself is left alone, because it is
yours now.

```sh
uv run proofcut setup --uninstall --plan   # what would go
uv run proofcut setup --uninstall          # lists it, asks, removes it
```

`tests/test_install.py` installs everything into a fake home, uninstalls, and
asserts the home's listing is what it was before
(`test_install_then_uninstall_leaves_the_home_as_it_was`), so the claim is
checked on every run of the suite rather than merely written here.

Two things `setup` is deliberately not. It is **CLI-only and never an MCP
tool**: an agent must not start a 2 GB download or change what is on your PATH.
And it is **not a package manager** — it installs no distribution packages and
writes nothing over an existing file, so nothing outside your own user account
changes. What proofcut's own dependencies cost is separate and uv's: about
230 MB in uv's cache, which `uv cache clean` empties.

## The core loop

Trimming the retakes out of a voiceover, end to end:

```sh
uv sync
uv run proofcut init myproject
uv run proofcut -C myproject import VO.wav --clip-id vo
uv run proofcut -C myproject import cohost.mp4 --mix         # two mics on one recording, summed
uv run proofcut -C myproject attach-transcript vo VO.json   # word-timed whisper JSON
uv run proofcut -C myproject transcribe vo                  # or: run whisper on vo directly
uv run proofcut -C myproject seed vo                        # auto-editor strips silences
uv run proofcut -C myproject transcript vo --search "here's the thing"
uv run proofcut -C myproject cut vo 111:114 --plan          # what do those indices say?
uv run proofcut -C myproject cut vo 111:114 --pad 0.1       # inclusive word range
uv run proofcut -C myproject cut-at 40.4+4.4                # or cut by what an export played
uv run proofcut -C myproject restore vo 111:114             # changed your mind about one cut
uv run proofcut -C myproject export cut.kdenlive            # an MLT project to finish in
uv run proofcut -C myproject import-edit trimmed.kdenlive   # ...and the trim you made there, back
uv run proofcut -C myproject verify final.mp4               # did the render say what you edited?
```

## The window

Or watch it instead of reading it — the page plays the source through the
edit, so there is nothing to render first. On a project with a cue table it
also shows the shot under the playhead, read from the same place the export
will read it:

```sh
uv run proofcut -C myproject open            # server + an app window, and it reopens where you left off
uv run proofcut -C myproject web --open      # the same page in an ordinary tab
uv run proofcut open --root ~/projects/video # Home: every project under a directory
uv run proofcut -C myproject view            # the same read model as JSON
uv run proofcut -C myproject preview vo      # will a browser play this asset, and if not why
```

The window carries the workflow rather than only the timeline: **Edit** (the
transcript, the preview, and a timeline you can drag-trim and razor), **Frame**
(every crop window as a row of the sheet's own tiles, approved or nudged in
place), and **Finish** (the export presets, what the render will actually
contain, verify after, and the finished file playable in the page). Footage
goes in there too — the assets pane imports a clip and transcribes it, so the
first three commands above have a window equivalent and the terminal is never
required. Riding all three is the truth strip —
`proofcut finish-report` made ambient, so a film that would ship wrong says so
while you edit. docs/plans/STUDIO.md is the design.

## Multi-mic recordings

A recording with more than one audio stream is **refused** rather than
registered as if the first mic were the whole of it — three separate places
would otherwise pick a stream without saying so, and the film would play half
a conversation with every check clean. `--mix` sums the mics into the one
track proofcut edits; `--audio-stream k` keeps one. Either writes the choice down
and every later command reads it without knowing.

## Renders, presets, undo, migration

`--render` exports media instead of an NLE project,
`--preset youtube|web|tiktok-reels|custom` picks a quality bundle for it
(`tiktok-reels` checks the project's canvas is vertical and refuses rather than
reshaping it), and `undo` rolls back the last mutation while
`restore` un-cuts one specific range. `changes [--steps N]` says what `undo`
N times would roll back, in words: the source spans cut or restored and the
manifest records added, removed or changed. It writes nothing. `undo --steps N`
rolls back that many in one call, refusing a count past the undo depth before it
restores anything (there is no redo), and `undo --steps N --plan` prints the
`changes` account and writes nothing. A project written by an older proofcut is
refused rather than guessed at; `proofcut migrate` brings it forward (`--plan`
says what it would do first, and the old manifest is kept under
`cache/history/`). A timeline with a cue table or a second
clip on it is written as MLT by proofcut and rendered by `melt` — auto-editor never
sees one, because it degrades a two-source render to 720x576 and exits 0.
`--loudness` masters a render to a target (§ Music, holds, a cold open and
the master).

## The MCP server

Every MCP tool has a matching subcommand, enforced by the test suite — so an
agent drives the same operations. It runs the other way too, minus a short
allowlist of commands there is nothing for an agent to do with (`web`,
`open`, `waveform`, `preview`, `info`, `mcp`):

```sh
uv run proofcut mcp                                          # serve MCP over stdio
uv run proofcut -C myproject mcp                             # ...bound to one project
claude mcp add proofcut -- uv run --project /path/to/proofcut proofcut mcp
```

**A mutating call commits when it lands.** No tool asks for confirmation, and
`plan` is off unless the caller sets it — `cut_by_transcript` without
`plan=true` edits the timeline at once. The schema says so to the client
instead: every tool carries MCP's read-only/destructive/idempotent hints, in
four classes. Read-only (`verify`, the views, `thumbnail`, `contact_sheet`);
add (`import_media`, `cue_add`, `sound_add` — inserts that never replace a
record); set (`card_new`, `hold_under`, `export`, `reframe` — replaces one
record at its address, or writes a file where told; `destructiveHint: true`,
`idempotentHint: true`); and edit (the cuts, `hold_add`, `undo`, `vo_extend`,
`reel` — `destructiveHint: true`, `idempotentHint: false`). A permission layer
in front of the server should key on those hints rather than on a list of
names, which goes stale with the next tool; note that `shot_sheet`,
`footage_sheet` and `reframe_sheet` are *set*, not read-only, because each
writes a PNG under `cache/`. `plan`'s own description tells the agent to
prefer it over doing and undoing. What makes unattended use safe is recovery,
not a gate:

- every write snapshots the timeline and manifest first, so `undo` walks it back;
- a cut never touches the source media, and `restore` brings back anything cut;
- a cut whose boundary lands on a word with a suspect whisper duration is
  refused until the caller passes `confirm_suspect`;
- a write against a manifest that changed under it is refused
  (`ProjectConflictError`) rather than clobbering the other writer.
- one agent session per project: a server takes the project's lock at its
  first write, and a second session's writes are refused until the first
  exits or sits idle for 10 minutes.

If a session died without releasing its lock (a crash on another machine,
say), `proofcut -C myproject unlock` clears it: it breaks a dead session's
lock and refuses a live one unless given `--force`. Studio shows who holds the
project and offers the same clearing for a dead session. Deleting
`cache/agent.lock/` by hand is safe too; a live holder then refuses its next
write rather than writing blind. A one-shot CLI command that changes a held
project warns and goes ahead.

If you want a gate, it belongs in the client: leave the destructive tools off
its allow-list and it will ask before each one. The agent panel and
`scripts/agent_trial.py` allow `mcp__proofcut__*`, so they never ask.

## Word indices and `locate`

Word indices address the *original* recording and never renumber, so a range
stays valid however many cuts have accumulated on top of it. The flip side is
that a word index is *not* a render timestamp, and every cut moves the two
further apart — `locate` is the conversion, in the direction `cut-at` does not
go:

```sh
proofcut locate vo --words 874:875              # where does that phrase play now?
proofcut locate vo --at 360.1                   # or a source instant
proofcut locate vo --span 127.0-130.5           # or a source interval
```

It answers in the render's own seconds, reports `present: false` for material
a cut removed rather than sliding the answer onto the neighbouring words, and
distinguishes that from a time the recording never reached. A range a cut
split comes back as one piece per survivor, so "half of it is still in there"
is a readable answer rather than a short one.

## Events — named instants in a screen recording

A screen recording has few words to hang an edit on. What it has is the
recorder's own log of when things happened. `events` keeps that log on the
clip, in the recording's own seconds, so a cut can no more invalidate an
event than a word index:

```sh
proofcut events rec --import marks.json --origin start           # {"start": …, "sent": …}
proofcut events rec --import keys.json --name key --offset 1789321344.5   # a bare list of times
proofcut events rec --name click --add 41.2                      # one by hand
proofcut events rec                                              # list, with addresses
proofcut locate rec --event sent                                 # where it plays now
proofcut locate rec --event 'key#12'                             # the 13th keystroke
```

A recorder logs wall-clock stamps, so `--origin` names the key holding the
recording's start (`--offset` subtracts seconds, for a bare list). A set
with any event outside the clip is refused whole, since a wrong clock moves
every event by the same amount. An import replaces only the names the file
brings, so a marks file and a keystroke file combine. A name that repeats
has to be addressed as `name#k`, counted from 0 in time order, and every
resolution echoes three events either side.

## Captions

Captions are generated from the *timeline*, not the transcript, so they stay
correct after cuts. Their **look is stored on the project** and the captions
are derived from it, which is what makes a restyle survive every later edit —
there is nothing coupling the two, so regenerating just re-reads the style:

```sh
proofcut caption-style --preset karaoke --size 80 --highlight yellow
proofcut caption-style                          # read it back, resolved
proofcut caption-view                           # the cues this timeline produces
proofcut captions subs.ass                      # sidecar ASS, Kdenlive loads it
proofcut captions subs.ass --burn render.mp4    # or burn in with ffmpeg
proofcut fonts --install                        # put the face the preset names where libass looks
```

Colours take `#rrggbb`, a name, or ASS's own `&H…`, and come back resolved in
both — ASS quotes them channel-reversed and alpha-inverted, so a value that
looks right is routinely a different colour. The window draws the same style
over the preview and on the CC lane, so what you see is what burns in.

Each word can **arrive** as it is spoken rather than simply be there: a fade,
or a blur that sharpens. The line is laid out whole from its first frame, so
nothing moves while a word comes in. The `reveal` preset is words landing in
the middle of the frame, fading in:

```sh
proofcut caption-style --preset reveal --max-words 4
proofcut caption-style --reveal blur --reveal-ms 250   # blur in over a quarter second
proofcut caption-style --reveal none                   # back to words simply appearing
```

A **caption span** treats one stretch of the film differently. `--off` draws
no captions there (over an end card or a logo); `--style` changes any caption
field over the span only, which is how one word gets its own big beat inside
ordinary lines. Spans are addressed like overlays, by word, phrase or event:

```sh
proofcut caption-span add vo --phrase "Pup" --until-phrase "stay." --off
proofcut caption-span add vo --phrase "well," --until-phrase "well," \
    --style '{"max_words": 1, "size": 180}'
proofcut caption-span ls
proofcut caption-span rm 0
```

Whisper's spelling is not always the film's. A **correction** kept in the
project's `lexicon.json` is printed wherever whisper wrote the words, as whole
words, and several words can become one. It only changes what captions show:
the transcript and `verify` are untouched, and undo does not take it back.

```sh
proofcut lexicon add rough ruff
proofcut lexicon add "Pup BNB" PupBnB
proofcut lexicon ls
proofcut lexicon rm rough
```

## Transcript checks and `unspoken`

Attaching a transcript checks it against itself and reports four findings —
`near_duplicates`, phrases said twice back to back; `suspect_durations`, a word
claiming long enough to hide a swallowed retake; `repeats`, a retake that
survived transcription as clean duplicate words; and `overlaps`, where the
timings say two words were spoken at once. The last two see opposite halves of
one defect and neither subsumes the other. That overlap is a retake splice
whisper read straight across, interleaving both takes and **inventing words
nobody said** — the tell is the overlap, never the reading, since an invented
word is usually grammatical. Findings are returned by the attach call, so an
older project asks for them again:

```sh
proofcut transcript-checks                      # every clip with a transcript
proofcut transcript-checks vo                   # just this one
```

Finding one is not removing it, and the removing is settled by the render
rather than by reading — nine invented words in 44 seconds of the Scream VO,
one of which reads as perfect English. `unspoken` marks a word the recording
never said; captions, `caption-view` and `verify` all stop expecting it, and
the transcript file is untouched, so word indices never move under a cue:

```sh
proofcut unspoken detect render.mp4             # propose; writes nothing
proofcut unspoken detect render.mp4 --apply     # ...or write the marks
proofcut unspoken ls                            # what is marked, and what went stale
```

`detect` takes its candidates from a seam and from a word a cut left a sliver
of, then asks the render's own transcription which of them nobody said.

When the transcript and the audio might disagree — a suspect duration, a hole
with no words in it — `hear` reads the source itself across a span, with the
same short-overlapping-window pass `verify --windowed` uses, and reports what
it heard beside what the transcript says there. It attaches nothing:

```sh
proofcut hear vo --from 1:02 --to 1:14           # heard_text beside transcript_text
```

## Speaker attribution

A co-hosted recording captured on one mic per speaker can have each word
labelled with whoever said it. Import refuses a container holding two mics
until you say what it is — `--mix` sums them, `--audio-stream k` keeps one —
and attribution then reads the *container*, comparing each mic's level over
each word:

```sh
proofcut attribute-speakers ep1 --stream 0 --label ana --stream 1 --label ben
proofcut attribute-speakers ep1 --margin-db 9 --apply     # ...and write the labels
```

The speaker is a label on the word, never an address: every cue, caption and
mark still resolves through `(clip_id, word_index)`. It reports rather than
decides — the rule is ~99% right per word on clear speech and no better than
chance on two people talking at once, so anything whose loudest mic does not
lead by `--margin-db` comes back in `ambiguous_spans` to go and listen to,
and `--apply` keeps any label it refuses to replace. On a single mixed track
there is nothing to compare, and proofcut says so rather than guessing.

## Verifying a render

`verify` closes the loop the other way: it transcribes a finished render and
diffs it against the words the timeline should play. That catches a class of
defect nothing else does — a retake still in the picture. Whisper collapses an
immediate repeat into one utterance, so a doubled phrase can be missing from
the source transcript, never get cut, and survive into the render with nothing
in the project file to show for it. Reading the timeline can only prove the
cuts you made are the cuts you meant.

```sh
proofcut verify final.mp4                       # transcribes with whisper
proofcut verify final.mp4 --windowed            # second opinion, in short windows
proofcut verify final.mp4 --transcript render.json   # or re-diff without re-running it
```

The words expected are the timeline's own and any a placed sound or inset
plays, when that clip has a transcript: a narrator take placed as a sound over
a silent screen recording is checked like dialogue, merged in where it plays.
Transcribe the take first (`proofcut transcribe vo`); a voice sound (`ducks`)
with no transcript is listed in `voice_sounds_untranscribed` and not checked,
and `finish-check` expects the same words.

Similarity around 0.97 is normal on a clean render — whisper spells its own
output differently on a second pass — so the diff is the artifact, and a
`repeated` entry is the retake signal. Whisper is a subprocess, not a
dependency: set `PROOFCUT_WHISPER` if `whisper` is not on your `PATH`.

A clean single-pass result is not proof, because that pass is itself one
whisper transcription and collapses a repeat the same way the source did.
`--windowed` transcribes in 10-second windows with 5 seconds of overlap and a
deliberately *smaller* model — a segment that ends after ten seconds has
nowhere to put an eleventh, and a bigger model tidies away the disfluency being
looked for. It costs one whisper run over twice the audio, which is seconds on
a five-minute render.

Both passes also report `loud_gaps`, which answers to no transcript at all: the
render's own energy envelope, masked by the words that were heard, with any
hole that holds sound anyway reported as somewhere to listen. Word *durations*
are not believed when building that mask — a word claiming several seconds is
hiding a hole rather than filling one, which is exactly how a collapsed retake
escapes a diff.

## Frames, `film-check`, `black`, `spots`

`verify` covers the audio. `frames` covers the picture, and it is worth running
*before* you render — `melt` will tell you how long the exported project is for
the price of reading it:

```sh
proofcut frames                                 # what the timeline will be
proofcut frames cut.kdenlive                    # what melt says it would render
proofcut frames final.mp4                       # what actually came out
proofcut film-check the-film.mp4                # is this project even the right cut?
```

`agrees` is the answer and `delta` is how far off. Two things this finds that
nothing else was looking at: a render that is no longer of this timeline, and —
on its first real run — that **auto-editor's kdenlive export is one frame
longer than your edit, and the frame is black**. That one is upstream's, it is
reported rather than corrected, and `export --render` does not have it. HISTORY.md § `check_frames` has the measurements.

`film-check` asks the other question, and it is the one `frames` structurally
cannot: not "does this render match my arithmetic" but "is this project the
film at all". A project seeded from a stale stage of an outside edit passes
every check proofcut has — 411s of silence-cut VO once did, against a 336s film —
because nothing was comparing it to anything outside itself. The reference is
remembered, so a later call re-asks without retyping the path.

`black` and `spots` read a render that already exists. `black` runs ffmpeg's
blackdetect and only ever explains away a run as that known kdenlive tail
frame when it sits at the end *and* the frame count says so — a real dark
scene, or a dark outro card, is reported, not waved off. `spots` pulls sample
frames out as PNGs, darkest first, with the word and clip they land on when
the render still agrees with the timeline:

```sh
proofcut black final.mp4                        # black stretches, explained or not
proofcut spots final.mp4                        # sample frames, ranked darkest-first
```

## Attenuation

Short loud noises between words get pulled down, not cut — a hole where a
breath was reads as an edit; a quiet breath reads as a person. `attenuate`
only acts automatically on an event short enough, in a gap narrow enough, to
trust the transcript around it; anything riskier is reported and left alone
unless confirmed:

```sh
proofcut attenuate vo --plan                    # what would be attenuated, and why not the rest
proofcut attenuate vo --confirm-suspect         # write it, including the edge cases
```

## Previews and proxies

Some footage a browser cannot decode — HEVC, 10-bit, an unopenable container —
so the preview shows black and says why. `proxy` builds a downscaled H.264
stand-in for it, in the project's cache and nowhere near a render: nothing
records it in the manifest, so `export` has no way to reach it and cannot be
silently taken at preview quality.

```sh
proofcut preview clip-id                        # will a browser play this, and if not, why
proofcut proxy clip-id                          # make it playable in the window
```

## Looking at the picture track

`shot-sheet` draws one labelled tile per shot — the whole picture track as a
grid, each tile showing the exact source second that shot reads from, four
across and about two dozen a page. It is how you see what the film *looks*
like without rendering or scrubbing it, and it was the first tool built for an
agent to use: over MCP it returns the sheet's **bytes**, so the picture arrives
in the reply rather than as a path an agent under `--tools ""` cannot open. All
four sheets do that now — `shot-sheet`, `footage-sheet`, `contact-sheet` and
`reframe-sheet` — and so does `spots`, whose montage comes back the same way.

```sh
proofcut -C myproject shot-sheet                # the picture track, drawn
proofcut -C myproject shot-sheet --page 1       # the rest of a longer film
proofcut -C myproject shot-sheet --out /tmp/look.jpg
```

Each tile is labelled `asset t=<timeline second>s src=<source second>s`. Read
`asset` — the *footage* — and not `clip_id`, which is the transcript the cue is
addressed against and on a voiceover project is the VO itself. A clip used
three times shows three tiles at three different `src=` values, which is what
makes a re-used shot legible at a glance.

**What it shows is a hypothesis, not a check.** Confirm anything you notice
with something that measures — `black`, `frames`, `verify`, `film-check`.

## Looking at footage you have not cut yet

`shot-sheet` needs an edit. `footage-sheet` does not — it browses one
registered clip's own footage, and it is for material with nothing to search:
recordings, gameplay, event coverage, b-roll. No dialogue, no subtitles,
nothing for a transcript to address. `describe` and `describe-ls` can already
*find* a moment in that footage by text; this is how you look at one.

```sh
proofcut -C myproject footage-sheet gopro-04              # every 10 seconds
proofcut -C myproject footage-sheet gopro-04 --page 2     # a long recording
proofcut -C myproject footage-sheet gopro-04 --interval 30
proofcut -C myproject footage-sheet gopro-04 --mode scenes
```

`--mode` picks which instants get drawn, and the default is deliberately the
boring one:

- `auto` (default) — described windows if the clip has any, otherwise the
  interval. It never scans for cuts.
- `interval` — every `--interval` seconds, defaulting to `describe`'s own 10s
  window so a clip draws the same stretches before and after you describe it.
  The stretches are equal with no remainder, so the spacing actually drawn can
  be slightly under what you asked for; the reply reports both.
- `describe` — one tile per described window, each row carrying that window's
  own text. This is the pairing worth having: the tile and the sentence are
  about the same ten seconds.
- `scenes` — one tile per detected cut. **Rarely what you want.** On genuinely
  continuous footage there are no cuts, so you get one tile; on gameplay it
  fires on deaths and respawns, which are not shots. It also decodes the whole
  clip, which costs seconds a page does not.

Each row carries a `luma` reading, and a tile with nothing in it is marked
`[blank]` on the picture itself — so a black square is never mistaken for a
frame that failed to extract.

This is the sheet read in order to *choose* footage, so the rule above binds
hardest here: a tile is a hypothesis. `synopsis` is where you say what a clip
**is**; a tile shows what the camera saw, which is a different fact.

## Speech overlap

Before laying a clip's own audio over the VO, `speech-overlap` checks whether
the two would collide, both mapped through the timeline the same way captions
are:

```sh
proofcut speech-overlap clip-id --at 106.4      # does the VO already speak there?
```

The clip need not have a transcript. Without one its side is read off its
energy envelope — runs of *sound*, which a sting or a scored swell clears too —
and the result says `clip_evidence: energy` so the reading is not mistaken for
a word-level one. `--evidence transcript` refuses instead and names
`transcribe` as the route to words; the VO side always needs its transcript.

## Cards, cues and tails

Clips and cards lay over the VO from the same word-indexed address space:

```sh
proofcut -C myproject card templates                   # what each one takes
proofcut -C myproject card new reveal-scream2 --template reveal \
  --set 'title=Scream 2' --set "note=Billy's mother" --set year=1997
proofcut -C myproject card render reveal-scream2       # re-render after an edit
proofcut -C myproject cue add vo 318 s1996-billy-stu   # from this word on, show this
proofcut -C myproject cue add vo 503 card:reveal-scream2
proofcut -C myproject shots --fps 30                   # what that projects to, in frames
proofcut -C myproject export assembly.kdenlive         # both lanes, written as MLT
```

A card is an SVG under `assets/cards/` and the PNG `card:<name>` resolves to;
both are kept, so a card is re-edited rather than redrawn. `card new` fills one
of six full-frame templates — `receipt`, `reveal`, `rerate`, `chapter`,
`endcard`, `bumper` — or one of the two overlay templates below, and `card
render` re-rasterises after a hand edit. Five of the six full-frame ones
carry a `mark` slot (`chapter` does not) and every one of them defaults to
empty: proofcut stays generic and the channel supplies its own mark, usually
through a preset pack (`proofcut pack`). **Cards generate at the project's own
canvas**, so a card in a 1920x816 cut is 1920x816 rather than a 16:9 still with
a quarter of its width in black bar.

Change the canvas later and the cards are the one thing that cannot follow on
their own — the aspect is baked into the SVG's viewBox, and rasterising a 16:9
document into a 9:16 frame *fits* it rather than reflowing it. So proofcut records
what each card was made from and draws it again:

```sh
proofcut -C myproject canvas 1080x1920                 # names the cards left behind
proofcut -C myproject card reauthor --plan             # what would be redrawn
proofcut -C myproject card reauthor                    # every stale card, at the canvas
```

A card whose files predate the record — drawn elsewhere and copied in — is
reported by name rather than guessed at, because nothing on disk says what
made it.

Two more templates, `lowerthird` and `scrim`, draw **overlays**: cards with no
background, placed *over* the film rather than instead of it. A lower third
is a headline and an optional amber footnote, bottom left; a scrim is the
dark gradient the type sits on.

```sh
proofcut -C myproject card new scrim --template scrim
proofcut -C myproject card new hears --template lowerthird \
  --set 'headline=It hears the false start.' --set 'footnote=and cuts it by the words'
proofcut -C myproject overlay add scrim vo --phrase "false start" --until-phrase "by the words"
proofcut -C myproject overlay add hears vo --phrase "false start" --for 3.2
proofcut -C myproject overlay add hears vo --event sent --until-event land --enter fade
proofcut -C myproject overlay ls                       # the stack, and where each plays
proofcut -C myproject overlay rm 1                     # by position; the card stays
```

The span starts at a word, phrase or event and ends at one, or after a length
(`--for`, which a cut inside it does not shorten). It is resolved through the
timeline on every export and never stored as seconds, so a cut moves it; a cut
through its start word makes `export` refuse until it is moved. **The list is
the stack**: a later overlay draws over an earlier one it overlaps, so place
the scrim first (or `--position 0`). A footnote that enters after its headline
is its own `lowerthird` with an empty headline. Each one `--enter`s and
`--leave`s with `rise`, `fade` or `none` (defaults: a 0.45 s eased rise in, a
0.3 s fade out). An ordinary card is refused as an overlay — it is opaque and
would cover the film — and an overlay card is refused as a cue. Mind the bottom
band: a lower third and burned captions both live there.

### Animated graphics

A graphic is a web page, captured frame by frame by a headless browser and
placed over the film like an overlay card. It needs the browser `proofcut
doctor` lists under optional; `proofcut setup` installs a pinned Chrome for
Testing headless shell (about 120 MB), or point `PROOFCUT_CHROME` at any
Chrome or Chromium.

```sh
proofcut graphic templates                              # typing, highlight, letters, chips, and their slots
proofcut -C myproject graphic new title --template letters --set title="Pup BNB"
proofcut -C myproject graphic new url --template typing --set text=proofcut.dev --set y=40
proofcut -C myproject graphic new mine --html page.html --intro 1.5 --outro 0.5
proofcut -C myproject graphic sheet title               # tiles at each phase boundary
proofcut -C myproject overlay add graphic:title vo --phrase "pup bnb" --for 3
proofcut -C myproject graphic edit title --set title="PupBnB"   # refills, keeps the phases, recaptures
proofcut -C myproject graphic save title --as brand-title       # into this machine's library
proofcut -C other graphic load brand-title                      # and into another project
```

A graphic has three phases, in seconds of the page's own timeline: an
**intro** that plays once from the start of its span, a **hold** that fills
whatever the span leaves, and an **outro** that plays once to end the span.
The hold is the page's last intro frame, or with `--loop N` the next N seconds
of the page repeated, for a hold that moves (the `typing` template's caret
blinks). So the span decides the length and the graphic never does: a cut
that shortens the span shortens the hold. A span shorter than intro plus
outro is refused rather than cut mid-motion.

A page written by hand animates with CSS: animations and transitions are
paused and seeked to each frame's time. A script animating from its own clock
defines `window.proofcutSeek(seconds)`. Start the outro's animations where the
intro ends (one loop later, if it loops). The page loads its own folder's
files and the vendored fonts under `/_proofcut/fonts/static/`, and nothing
else: no network, so a web font cannot lose a race with the capture. Three
things are refused at capture: a hold still moving when it was declared
still, a loop that does not come back to where it started, and a font that
failed to load.

The capture is drawn at the project's canvas and export rate and is stamped
with both and with the page's bytes; change any of them and `export` refuses
the stale frames until `graphic capture` redraws them. A full-frame graphic is
one whose page has an opaque background. Undo moves the overlay that places a
graphic, never the page, which lives beside the manifest like a card's PNG.

### Stills and stickers

A photo, a logo or a cut-out is added once, then shown full frame or placed
over the film:

```sh
proofcut -C myproject image add ~/Pictures/dog.png            # lands as image:dog, upright
proofcut -C myproject image add IMG_2041.HEIC --name beach     # converted once, for every renderer
proofcut -C myproject cue add vo 12 image:beach                 # full frame, contained like a card
proofcut -C myproject overlay add image:dog vo --phrase "ruff" --for 2 --x 0.8 --y 0.7 --width 0.15
proofcut -C myproject overlay add image:beach vo 20 --for 3 --x 0.3 --width 0.25 --rotate -6 \
    --style photo --enter slide-left --leave slide-right
proofcut -C myproject image ls                                  # and what places each
```

`x` and `y` are the sticker's centre as fractions of the frame, `width` a
fraction of its width, `rotate` degrees clockwise, and `--style photo` gives
it a white border and a soft shadow. A sticker pops in (scaling up past full
size about its own centre, then settling) and fades out unless `--enter` and
`--leave` say otherwise; the `slide-left`, `slide-right`, `slide-top` and
`slide-bottom` motions come in from, or go out to, that edge, and cards and
graphics take them too. In the window, type `@` in the agent's prompt to name
a clip, card, image or graphic, and paste or drop an image there to add it.

A card can also go *after* the last frame, as a `tail` — an end card or a
bumper, which every earlier cut applied downstream of `export` and so lost on
any re-cut, silently:

```sh
proofcut -C myproject tail --asset card:endcard --seconds 6  # what plays after the film
proofcut -C myproject tail --fade 0.5                       # the card dissolves in over the film's end
proofcut -C myproject tail --reset                          # back to ending on the edit
```

`--fade` overlaps the film's last frames rather than adding to `--seconds`:
the card is opaque on the tail's first frame, and the render is exactly as
long as without it.

It is project state, so a derivation knows it existed rather than dropping it
without a word — `reel` reports `tail_dropped`. `Edit` does not grow to
describe one, so `timeline_duration` still answers for the cut itself.

## Music, holds, a cold open and the master

A cut voiceover with pictures over it is not yet a film. What finishes one is
sound: a score under the narration, the footage's own lines where the essay
quotes them, a cold open before the first word, and a master at a platform's
loudness. Each is project state, so it survives every later cut, and each
renders through `melt` like the cue table. Both essays in HISTORY.md § The
Scream native rebuild were built this way and measured against their delivered
files.

### The music bed

Music is a registered clip like any other, addressed by the word it starts on:

```sh
proofcut -C myproject import score-a.wav --clip-id score-a
proofcut -C myproject music --asset score-a --clip-id vo --start-word 0 \
  --fade-in 2 --under 22 --plan                          # resolve and check, write nothing
proofcut -C myproject music --asset score-a --clip-id vo --start-word 0 \
  --fade-in 2 --under 22
proofcut -C myproject music --duck 8                     # 8 dB down under the voice, up in its pauses
proofcut -C myproject music --loudness -23               # no VO to sit under: a fixed level instead
proofcut -C myproject music --over-tail --fade-out 1.5   # play on under the end card, fading with it
proofcut -C myproject music --passage score-b,412,0,2.5  # from word 412, score-b, crossfading 2.5 s
proofcut -C myproject music --rotate score-c --crossfade 2.5  # when an asset runs out, the next
proofcut -C myproject music                              # what is in force
proofcut -C myproject music --reset
```

**The bed stores word indices and assets, never a length.** It starts where
its word lands on the timeline, and with no `--end-word` it runs to the end of
the edit, so a cut anywhere moves it for free. A stored length was measured
drifting onto live material. An end card holds over silence, because a tail
comes after the film, unless `--over-tail` carries the bed on under it. `--phrase-start` and `--phrase-end` resolve a boundary
by what is said, the same way every word-addressed tool does.

- **`--passage ASSET,START[,SRC_IN[,CROSSFADE]]`** places a later cue. START
  is a word index or a phrase, SRC_IN is where in its asset it plays from, and
  the passage before it runs on past that word by the crossfade so the two
  overlap. It is repeatable, and each call replaces the whole list.
- **`--rotate`** tiles several assets in turn, overlapping by `--crossfade`,
  when one runs out before the film does.
- **`--under N`** levels the whole bed N LU below the voiceover, measured,
  rather than playing each asset at its own level.
- **`--duck DB`** pulls the bed DB down while the voice is speaking and lets it
  back up in the pauses, so `--under` is the level in a pause. It is keyed off
  the timeline's own audio when you export, never the transcript's word
  timings, and a cut moves it for free. `export`'s `music.duck` reports what
  the render carries. `--clear-duck` puts the bed back at one level.

Overlapping passages are crossfaded on an equal-power curve. Two plain fades
crossing leave a hole in the middle, measured at 26–30 dB under the bed. The
bed goes out across every hold, and `reel` drops it and says so
(`music_dropped`), because a bed restarted from its head halfway through a
film is not the film's score.

### Holds: the film's own lines

A hold opens a gap in the voiceover and plays a clip's own audio across it,
with the picture pinned to the line:

```sh
proofcut -C myproject hold add vo --gap-phrase "what she wanted" \
  --cue-phrase "the scene where" --asset lambs-clarice \
  --asset-phrase "the lambs are still screaming" --plan
proofcut -C myproject hold add vo --gap-phrase "what she wanted" \
  --cue-phrase "the scene where" --asset lambs-clarice \
  --asset-phrase "the lambs are still screaming"
proofcut -C myproject hold ls
proofcut -C myproject hold check final.mp4       # transcribe each hold off the render
proofcut -C myproject hold rm vo 211             # the record and its cue; the silence stays
```

- **The gap word** is the last voiceover word before the gap.
- **The cue word** is where the clip's picture starts.
- **The asset phrase** is the line that must be heard clean. It resolves
  against the clip's *own* transcript, so the clip has to be transcribed.

A phrase re-resolves when a transcript changes under it, and four hand-typed
indices would not.

**`hold check` listens to the render rather than trusting the plan.** It cuts
each hold's span out of the file, transcribes it, and reports a fault when
either end of the line is not heard, when a seam jumps in level, or when
the hold's picture cue has drifted off its line. It reports and never
refuses, like `verify`.

To play the footage *under* the narration rather than in a gap:

```sh
proofcut -C myproject hold under vo lambs-fairy-tale \
  --phrase-start "once upon a time" --phrase-end "happily ever after" --under 13
proofcut -C myproject hold under-rm vo 318
```

No in-point is stored. The audio reads from wherever the shot showing that
clip has got to at the span's first word, so it cannot disagree with the
picture, and a clip that is not on screen there is refused.

### Sounds on events

A screen recording's clicks and keystrokes are events (see *Events* above),
and a one-shot sound can play at each one: a key tick under typing, a click
at Send, a chime when a result lands.

```sh
proofcut -C myproject sound generate        # eight key ticks, send, land, strike, as sfx-* clips
proofcut -C myproject sound add screen --every key \
  --asset sfx-key_0 --asset sfx-key_1 --asset sfx-key_2 --asset sfx-key_3 \
  --gain -15 --jitter 2                      # a tick on every keystroke
proofcut -C myproject sound add screen --event sent --asset sfx-send --gain -10
proofcut -C myproject sound add vo --phrase "and cuts it" --asset sfx-strike
proofcut -C myproject sound ls              # each record and how many hits it places
proofcut -C myproject sound rm 0            # by position; the clip stays
```

A sound is any imported clip with audio, up to 30 s. With several `--asset`s,
each hit draws one, so a run does not repeat one sample, and `--jitter`
varies each hit's level. Both are seeded by the record, so every export
writes the same film. `--every` skips the events a cut removed and drops any
hit closer than `--min-gap` (45 ms) to the last one it kept. `sound ls`
counts both. A single hit whose word or event is cut makes `export` refuse
until it is moved.

Hits land to the millisecond, between frames. Each one plays from a padded
copy in `cache/sounds/`, because melt plays nothing of a file under two
frames long, at exit 0. Sounds do not duck the music bed, and `--loudness`
masters them with everything else. The window draws an SFX lane of ticks.
The preview does not play them, just as it does not play the bed.

### Retime: a span at another speed

A screen recording's waits can play in a second, and a moment can play slow.
A stretch runs from a word or event to another, in the seconds you give it:

```sh
proofcut -C myproject retime add screen 1.0 --event sent --until-event words   # a 31 s wait in 1 s
proofcut -C myproject retime add screen 4.0 --phrase "struck" --until-phrase "through"
proofcut -C myproject retime ls              # Edit span, render span and speed of each
proofcut -C myproject retime rm 0            # that span plays at 1x again
```

Everything outside a stretch plays at 1x, and the speed eases in and out over
half a second on either side. The film's own audio is muted wherever it plays
off speed. Music, sounds, overlays and captions keep 1x and land where the
retime puts their moment. A picture cue over a stretch speeds up with the
film. Stretches may not overlap, and a cut through an end word makes `export`
refuse until the end is moved. A film-audio hold cannot share a project with
a retime yet.

**It creates another clock.** `timeline-view`, `locate`, `status` and
`caption-view` stay in Edit time and carry a `retime` summary; `locate` also
says where the render plays the words. `cut-at` and `reel` take the
seconds a retimed export plays at. The preview plays the Edit at 1x, says so
on its header, and draws each stretch as a band on V1 and A1.

### Inset: a clip inside the recording

A screen recording of an app that plays a video can have that video drawn
into it for real: the render at full sharpness in place of the recording's
small copy, moving and zooming with the camera.

```sh
proofcut -C myproject inset add screen cut 873,252,1785,936 --event playing   # to the clip's end
proofcut -C myproject inset add screen cut 873,252,1785,936 --event playing --dim 0.55 --for 12
proofcut -C myproject inset ls              # where each plays, and where its rect lands
proofcut -C myproject inset rm 0            # the clip stays registered
```

The rect is `X0,Y0,X1,Y1` in the recording's own pixels, where it shows what
the inset replaces, and it has to be the clip's shape (within 1%). The inset
follows the recording's `reframe` windows, slides included. It fades in and
out over 0.4 s (`--enter/--leave none` for a cut), `--dim` darkens the
recording around it, and its own audio plays at `--gain-db` with the music
bed out underneath, unless `--mute`.

It plays at 1x, so it has to sit inside one continuous stretch of the
recording that also plays at 1x: a cut under it, a retimed span, or a split
or blur-fill window refuses. `reframe-sheet` draws the rect dashed on every
tile inside the inset, which is where a wrong rect is seen before a render.
The preview draws it too, at the recording's head window, and V1 marks its
span along the lane's foot.

### A cold open

```sh
proofcut -C myproject head --asset lambs-cellar --src-start 42.5 --seconds 9 --fade-out 0.5
proofcut -C myproject head --reset
```

A head plays before the edit's first frame, with its own audio. **It creates a
second clock:** render time is edit time plus `head_seconds`. `timeline-view`,
`locate`, `status` and `caption-view` stay edit-relative and report
`head_seconds` beside their answer. Everything that reads a render offsets for
it itself: `captions` shifts by it, and `verify` trims the head's words before
diffing (`head_words_trimmed`).

### The master

```sh
proofcut -C myproject export final.mp4 --render --loudness -16 --true-peak -1
```

`--loudness` masters the render proofcut just made with one measured gain and
a true-peak limiter, and measures it before and after. The reply's `loudness`
carries both numbers, the gain, and whether the limiter had peaks to hold
(`normalization`: `linear` or `limited`). It never rides the mix's level the
way `loudnorm`'s dynamic mode does, so a duck, a fade or a hold's level
arrives in the master as it was mixed. A master that misses its target by more than 1 LU, or its ceiling by
more than 0.5 dB, is refused, and the render is left as it was. It masters
media only, so an NLE export refuses it. `captions --burn` afterwards copies
the audio through untouched, so the master survives the burn.

## Saying a line in a cloned voice

`vo-synth` renders a sentence in a cloned voice — zero-shot Qwen3-TTS given a
reference clip and its words — several times, ranks the renders by how close
each one's voice is to the reference, reads the winner back through whisper,
and optionally splices it into the VO track the way `vo-extend` splices a hold:

```sh
proofcut -C myproject vo-synth "And that is the whole trick."            # 3 seeds, ranked, read back
proofcut -C myproject vo-synth "…" --candidates 5 --seed 10              # a different set of tickets
proofcut -C myproject vo-synth "…" --after vo 11                         # splice after word 11 of clip vo
proofcut -C myproject vo-synth "…" --after vo 11 --plan                  # rank (if cached) and preview, write nothing
proofcut -C myproject vo-synth "…" --voice ~/voices/me                   # a directory holding ref.wav + ref.txt
```

The reply names every candidate with its `sim` (the model's own speaker-encoder
cosine against the reference — a real take of the same speaker ≈0.99, a
three-semitone pitch shift ≈0.96), the `chosen` one, anything `capped` by the
length limit (`--max-seconds`, default 20 — a capped render did not end because
the line did, and never wins while an uncapped one exists), and `heard`/`wer`
from the readback. Renders cache under `cache/synth/` per (voice, text, cap), so
repeating a line is free and widening `--candidates` renders only the seeds it
lacks. A splice registers the winner as `synth-<key>-s<seed>` and goes through
`vo-extend`'s mechanism, with the same consequences: export switches to melt,
`restore` refuses across the seam, and `covered_by` names any picture now
running over the new seconds.

The synthesiser is a subprocess in another interpreter: `PROOFCUT_TTS` names it
and `PROOFCUT_TTS_MODEL` the Qwen3-TTS model directory, with no fallback for
either.
**The voice is yours to supply** — `--voice` or `PROOFCUT_TTS_VOICE`; proofcut ships
no reference clip and has no default voice. `proofcut vo-synth … --plan` reports
what it resolved. Why a reference clip and not a fine-tuned model: HISTORY.md
§ `vo_synth`, built. It loads onto CUDA; `PROOFCUT_TTS_DEVICE` (`mps`, `cpu`)
hands the worker another device, which has never been run, so a Mac reports
the synthesiser unavailable until it is set.

## Reframing

The footage does follow on its own, by cropping to fill rather than
pillarboxing — a 1920x816 clip in a 9:16 frame keeps a 459-pixel-wide band of
itself instead of 76% of the frame going black. Which band is `reframe`'s to
say, and the default is a centre crop, **which is wrong whenever the subject
is not centred**:

```sh
proofcut -C myproject reframe                          # every crop in force
proofcut -C myproject reframe cold-open --rect 1400,0,459,816
proofcut -C myproject reframe cold-open --rect 0,0,459,816 --at 20.4   # from there on
proofcut -C myproject reframe cold-open --rect 0,0,918,816 --pane 1002,0,918,816
proofcut -C myproject reframe cold-open --rect 930,0,450,800 --at 7.34 --interp  # slide, don't step
proofcut -C myproject reframe rec --rect 640,360,1280,720 --event sent --ease ease  # eased, at an event
proofcut -C myproject reframe cold-open --fill blur --at 12.0   # whole frame, over its own blur
proofcut -C myproject reframe cold-open --reset        # back to the centre
proofcut -C myproject reframe-detect                   # propose a window per shot
proofcut -C myproject reframe-sheet                    # every window, drawn, for review
proofcut -C myproject reframe-sheet --extremes         # ...drawn where the subject is extreme
proofcut -C myproject reframe-sheet --per-page 6 --page 0   # a page at a time, and cheaper
proofcut -C myproject reframe-coverage                 # seconds framed for an earlier shot
```

A rect is in that clip's own source pixels, so no cut can invalidate one, and
it is stored as asked and refit whenever the canvas moves. A rect that is not
already the canvas's shape is *grown* to it rather than shrunk into it —
everything named stays on screen — and one that cannot be shown whole is
refused with the largest rect that can.

A slide (`--interp`) runs across the **whole** of the window before it, from
that window's start to this one's, and `--ease` gives it a curve: `ease`
(slow at both ends), `ease-in`, `ease-out`, or the default `linear`. To move
between two moments, set a window holding the old rect at the first and the
eased one at the second — `--event` addresses either by a named instant from
`events` instead of `--at`. The window stores the event's second, and the
table says `event_moved` if a later import moves that event; it never
follows.

`--fill blur` is for the shot no crop and no split can hold: that window
shows the whole frame, contained, over a blurred, darkened copy of the same
moment covering the canvas. It takes no `--rect`, and the preview and the
sheet both draw it (the sheet labels the row `blur-fill`). It renders a
second copy of the footage, which cost about 80% more render time in the
measurement, and only a project that uses it pays that.

`reframe-detect` will propose one window per camera shot from where the faces
are — it beats the centre crop on every measure against fifteen hand-framed
windows — but **it proposes and never frames**: `--apply` is off by default,
because it is still a quarter of a window's width out on average and a wrong
automatic reframe makes a film with nothing on screen saying so. A window with
no face in it is *named* rather than guessed at, and the reply says what will
cover it instead, which is not the centre crop but whatever window is already
in force. That naming lives only in the reply, so `reframe-coverage` asks the
same of a project on disk: which placed seconds a camera cut has stranded under
a window chosen before it. On the vertical cut that is 15.6s, three of the four
stretches on one clip. It is scene cuts against stored geometry, so unlike
`reframe-detect` it needs no face detector. It asks the mirror too, and that is
the one a viewer notices: `steps` names each window boundary *inside* one
placement where the frame moves and the picture does not — an edit that is not
there. Each carries how far the frame travels and how near the closest picture
change is, which is what separates a boundary that missed a real cut narrowly
from a step in the middle of a take.

`--pane` draws that window as a **stacked split** — two half-height panes, the
rect on top and the pane below, each keeping about twice the width one crop
gets. It is for the shot one window cannot frame, a two-hander where every face
is a true positive and only one of them is the shot. `reframe-detect` offers
one where every sampled frame holds subjects a single window cannot hold — 10
of the 79 windows on the film it was measured against, a share that moves with
the scene floor rather than describing the film, since three sampled moments
agree far more readily over 2 seconds than over 8. Judge one on `pane_overlap`,
reported beside it: the film's splits separate at 23–30% and duplicate a face
across both halves at 52–63%.

`--at` is seconds into that clip's own source, so framing is per **shot**
rather than per clip: a clip used seven times picks up whichever window each
placement reads over, from one table. **Judge windows on `reframe-sheet`, never
on a watch** — it draws every window on three of its own source frames, and a
badly-placed window is invisible in motion because nothing in the frame
contradicts it. A row is a *window shown* rather than a placement: sampling
placements at fixed fractions never looked at 14 of the vertical cut's 55
windows, eight of them hand-approved. A tile is still evidence about the
instant it draws and a window is a claim about a span, so a static rect over a
moving subject can pass on its best moment — which is what `--extremes` is
for: it probes each window with the face detector and draws it where the
subject is **leftmost, median and rightmost**, worst tile first. The rect does
not move inside a window, so the worst moment is at one of those ends by
construction. It found 284px of error where the fixed fractions found 186 on
the window a watch had already indicted, and never draws a *better* moment than
the fractions would have, because they are in the probe grid. Read
`worst_offset` beside `multi_face`, never after it: the subject is
area-weighted across every face in the frame, so a two-face frame puts it
between them where nobody is — the teaser's largest offset, 608px, is that.

Every render reports the fonts the document names and what fontconfig will
actually draw — **a card naming a font this machine lacks renders
pixel-identically to one naming a font it has**, so `font_warnings` is the only
place that substitution is visible.

## The reel — a derived vertical cut

None of that should happen to the film itself, though, and a vertical cut is
usually a *second* deliverable rather than a replacement — a 5:36 essay does not
reach a feed that stops at 3:00. So the reel is a derived project:

```sh
proofcut -C myproject reel ../teaser 1:32+44 --plan     # what it would keep and drop
proofcut -C myproject reel ../teaser 1:32+44 --canvas 1080x1920
```

The span is what to **keep**, in the seconds the current export plays at — the
same numbers `cut-at` takes, read off a watch — and the head and tail are what
get cut. The canvas is set on the copy only, which is the point: reshaping the
film to take one render leaves it reshaped afterwards, and nothing reports that.
Media is linked rather than copied, so a reel costs its manifest and its
transcripts instead of its footage.

Three things are reported rather than done quietly. `cues_dropped` names every
cue whose word the reel cut — each one is a picture the reel will not have —
`cues_pinned` names the survivors it gave an in-point to, and `suspect_edges`
flags a kept edge landing on a word that likely hides a retake, which is the
reel opening or closing on the wrong take.

The pinning is why a reel shows what the film showed. Which stretch of an asset
a shot reads is decided by a cursor that walks from shot to shot, so dropping
the shots before a survivor makes it replay its clip from the head instead —
real frames, a valid projection, and a different film with nothing saying so.
Each survivor is pinned to the in-point the film's own plan gave it; 2 of the
4 that survived the Scream teaser's span needed one.

## Describing footage — b-roll search

To find the b-roll to cue in the first place, describe it:

```sh
proofcut -C myproject describe --plan          # what it would cost, no model loaded
proofcut -C myproject describe                 # every video clip not yet described
proofcut -C myproject describe cold-open       # or just one
```

Each clip is split into fixed ~10-second windows and each window gets a couple
of sentences of what is visible in it, stored against the clip in **source**
seconds — so cutting the edit can never invalidate one. It is a job rather
than a request: about three and a half seconds per window, so a project's
footage is minutes of GPU time, which is what `--plan` is for. **The windows
are never widened to save time** — one pass over a whole clip described six
frames as six people, fluently, with nothing on screen saying it was wrong.

The model runs under a separate interpreter (`PROOFCUT_VLM`), so nothing here
puts torch in proofcut's own environment. `--plan` reports whether this machine
can run it at all — never on a Mac today: the model loads 4-bit through
bitsandbytes, which is CUDA-only, so `PROOFCUT_VLM_DEVICE` exists for whoever
builds another path and the worker refuses anything but CUDA until then.

Then read them back, which **is** the search — no ranking, no embeddings, no
similarity score to tune:

```sh
proofcut -C myproject describe-ls                        # the whole table
proofcut -C myproject describe-ls --contains "kitchen knife"
proofcut -C myproject describe-ls cold-open              # or one clip's
```

`--contains` takes terms rather than a phrase: every term has to appear
somewhere in a description, so `kitchen knife` finds "a knife on the kitchen
counter". A filtered result reports what it filtered *out of*, so a narrow
answer cannot be mistaken for an empty project, and `words` says how much text
came back — at roughly 600 windows, reading them all stops being reasonable.

A window's `src_start` is what places it. `cue add --src-start` pins where
inside the asset the shot reads, so the cut shows the moment you searched for
rather than wherever that clip's re-use cursor had got to:

```sh
proofcut -C myproject cue add vo 318 cold-open --src-start 92.4
```

In-point only — the out-point stays derived from the next cue, so a later
recut still moves the shot. A pinned shot that would run past the end of its
asset is **refused** by `shots` and `export` rather than rewinding to the
clip's opening seconds, which would be plausible footage and the wrong film.

Descriptions live in the manifest, so `proofcut info` reports a count and points
here rather than printing them; `proofcut info --raw` still prints the manifest
verbatim.

A cue names a *word*, so a later recut recomputes every shot position rather
than invalidating it — and a cue whose word the recut removed is refused
rather than silently snapped forward. Once a project has a cue table (or a
second clip on the timeline) `export` writes the MLT itself instead of going
through auto-editor, which refuses a second source on export and quietly
renders one at 720x576.

## The layered timeline

Multi-track editing is **decided and built** — all six steps, 2026-08-08: the
cue table, the shot projection, the refusal that guards it, the MLT writer, the
`melt` render, and the picture lane in the window. `export --render` produces
the file; the timeline's V2 lane draws the shots that file will contain, and
draws them from the *planned* projection, so the window can never show a shot
`export` would refuse. [PLAN.md](PLAN.md) § The layered timeline has the design
and the build order.
