# The demo — a cut, a picture, a score, and a render that checks itself

Two minutes, start to finish, on footage the repo generates rather than ships.
By the end you will have a small finished film. You will have cut a retake
out of a voiceover by naming its words, hung two b-roll clips off phrases in the
transcript, laid a score under the voice, rendered and mastered the result, and
had proofcut confirm the render says what the timeline says.

Every command below is verbatim. The output sketches are from real runs, on
2026-09-13 and, for steps 6–8, 2026-09-15. Yours will differ in the third
decimal place and in whatever whisper hears, and that is the point of step 7.

## What you need

`uv`, `ffmpeg`, plus **whisper** for the transcript, **auto-editor** for the
silence pass and **melt** for the picture. The ffmpeg has to be built with
`libx264`, freetype and libass: step 1 labels the footage with `drawtext`, and
it stops at its first command without it. On a Mac that means Homebrew's
`ffmpeg-full`, not its `ffmpeg`, and because it is keg-only, first on `PATH`:

```sh
brew install ffmpeg-full
export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"
```

If you are not sure:

```sh
uv run proofcut doctor
```

It probes all of them and, for anything missing, prints the fix rather than
just a ✗. It only looks; it installs nothing.

On Linux, Windows and a Mac of either kind, `uv run proofcut setup` installs whichever
of whisper, auto-editor, melt and ffmpeg doctor marked, for you alone and with
no sudo. Read it before it runs — `uv run proofcut setup --plan` prints every
piece, its size and why doctor asked for it, and stops without touching
anything. What lands where, and what takes it away, is
[README.md § What it puts on your machine](../README.md#what-it-puts-on-your-machine).

Nothing else on this page needs a package manager. Step 1 synthesises the
voice with espeak-ng, and where the program is not on PATH the script fetches
the same library as a wheel into uv's cache instead — the same version, and a
byte-identical voiceover, so the word counts below hold either way. Step 8's
end card is the exception and needs **ImageMagick 7** (`magick`); it is also
the one step you can skip.

On a box with no desktop (a server, a container, SSH), step 7's render needs
Qt to draw without one. `proofcut doctor`'s Display row renders a probe frame and
says whether `QT_QPA_PLATFORM=offscreen` is enough for your MLT. Where it is
not, as with Ubuntu 24.04's and Fedora 44's packaged melt, `uv run proofcut
setup` installs Shotcut's portable melt, which draws under it. (A desktop is
not a question on Windows or a Mac, where Qt draws through the OS itself.) Or run the
render as `xvfb-run -a uv run proofcut …` (`apt install xvfb`, or `dnf install
xorg-x11-server-Xvfb`).

## 1. Make the footage

```sh
uv sync
uv run python scripts/make_demo.py ~/proofcut-demo
```

```
voiceover  -> /home/you/proofcut-demo/vo.wav
b-roll     -> /home/you/proofcut-demo/broll-blue.mp4
b-roll     -> /home/you/proofcut-demo/broll-rust.mp4
music      -> /home/you/proofcut-demo/music.wav
```

The voiceover is about 19 seconds and says this — read it, because the fourth
line is the one you are about to remove:

> This is a demo of proofcut, a local first video editor.
> **Every cut you make names a, hmm, no, let me try that again.**
> Every cut you make names a word in the transcript.
> So the edit stays addressable, and the render can be checked against it.

That middle line is a **retake**: the narrator starts a sentence, stops, and
says it again. Removing it is the thing proofcut exists for.

The two b-roll clips are flat colours carrying three marks — a centred
counter (`BLUE 3s`), a faint grid, and `TL`/`TR`/`BL`/`BR` in the corners.
Ugly on purpose. Every frame names which clip it is and how far into it, so
one look at the render tells you whether the right footage is at the right
moment; and every *corner* names itself, so a crop window that keeps all four
is one that is not cropping. On real footage you judge a crop by whether the
subject survived — there is no subject here, so the frame answers the question
instead.

The score is a plucked melody, generated from a fixed seed. It sounds like a
music box and is findable the same way: its notes never repeat in the same
order, so a render can be checked for it at the exact second it should be
playing.

## 2. Make the project

```sh
uv run proofcut init ~/proofcut-demo/proj
uv run proofcut -C ~/proofcut-demo/proj import ~/proofcut-demo/vo.wav --clip-id vo
uv run proofcut -C ~/proofcut-demo/proj import ~/proofcut-demo/broll-blue.mp4 --clip-id blue
uv run proofcut -C ~/proofcut-demo/proj import ~/proofcut-demo/broll-rust.mp4 --clip-id rust
```

Each `import` prints the clip record — duration, codecs, dimensions, and
`vfr` (whether the source is variable frame rate). Nothing is copied: proofcut
links the media where it lies.

```sh
uv run proofcut -C ~/proofcut-demo/proj transcribe vo
```

whisper, with word timings. Expect **~47 words** — a count; the next step reads
the words back, disfluencies and all. This is the slow step; on a laptop
without a GPU it is a minute or two.

```sh
uv run proofcut -C ~/proofcut-demo/proj seed vo
```

auto-editor strips the silences and what is left becomes the timeline.

```
"segments": 4,
"source_duration": 18.708,
"timeline_duration": 16.833,
"silences_removed": true
```

Four segments, because the gaps between takes are gone.

## 3. Find the retake

```sh
uv run proofcut -C ~/proofcut-demo/proj transcript vo --search "let me try that again"
```

```json
{"first_word": 19, "last_word": 23, "start": 7.6, "end": 9.06,
 "text": "let me try that again."}
```

The fluff starts earlier than that, at the beginning of the abandoned
sentence. Read the words around it:

```sh
uv run proofcut -C ~/proofcut-demo/proj transcript vo --first 8 --last 26
```

Words 11–23 are the whole retake, `Every cut you make names a... Um, no, let
me try that again.` — and word 24 is where the good take starts.

## 4. Cut it

**Ask first.** Every mutating command takes `--plan`, which resolves the whole
thing and writes nothing:

```sh
uv run proofcut -C ~/proofcut-demo/proj cut vo 11:23 --plan
```

```json
"text": "Every cut you make names a... Um, no, let me try that again.",
"context_before": [{"index": 8, "text": "first"}, {"index": 9, "text": "video"},
                   {"index": 10, "text": "editor."}],
"context_after":  [{"index": 24, "text": "Every"}, {"index": 25, "text": "cut"},
                   {"index": 26, "text": "you"}],
"removed": 4.7
```

The three neighbours either side are the point: an index one past the phrase
you meant reads perfectly well on its own, and the echo is what makes that
visible. Word 10 ends the good line before, word 24 starts the good line
after, so this is the right range. Now do it:

```sh
uv run proofcut -C ~/proofcut-demo/proj cut vo 11:23 --pad 0.1
```

```
"removed": 4.8,
"duration_after": 12.006,
"segments": 4
```

`--pad 0.1` takes a tenth of a second either side, so the cut lands in silence
rather than on a consonant — which is the 4.7 → 4.8 difference between the
plan above and this. If you cut the wrong range, `proofcut -C … undo`
puts it back.

## 5. Hang a picture on it

A cue says "from this word onward, show this asset". Address it by phrase and
proofcut resolves it against the transcript, echoing what it matched:

```sh
uv run proofcut -C ~/proofcut-demo/proj cue add vo --phrase "Every cut you make names a word" blue
uv run proofcut -C ~/proofcut-demo/proj cue add vo --phrase "the render can be checked" rust
```

```sh
uv run proofcut -C ~/proofcut-demo/proj shots
```

```
0.00 + 9.79  blue
9.79 + 2.21  rust
```

Two shots. Note that a cue survives a cut — it names a *word*, not a second,
so nothing you do to the edit can move it out from under its own line.

## 6. Score it

Music is a clip like any other. A bed is addressed by the word it starts on, so
it moves with every cut rather than drifting off a stored second:

```sh
uv run proofcut -C ~/proofcut-demo/proj import ~/proofcut-demo/music.wav --clip-id score
uv run proofcut -C ~/proofcut-demo/proj music --asset score --clip-id vo --start-word 0 --fade-in 1 --fade-out 2 --under 18
```

```
"music": {"asset": "score", "word_index_start": 0,
          "fade_in": 1.0, "fade_out": 2.0, "under": 18.0},
"start_word": {"word_index": 0, "text": "This", "start": 0.0}
```

With no end word the bed runs to the end of the edit. `--under 18` sets its
level 18 LU below the voice, measured, rather than trusting the file's own
level.

## 7. Render, master, and check the render

```sh
uv run proofcut -C ~/proofcut-demo/proj export ~/proofcut-demo/demo.mp4 --render --loudness -16
```

Two b-roll sources, the voiceover and the score, so this goes through MLT rather
than auto-editor — proofcut picks the writer from the project, never from a
flag. `--loudness -16` then masters the render to a common target for online video
and measures it before and after:

```
"writer": "melt", "shots": 2, "sources": 4, "frames": 289,
"music": {"level_db": -25.41, "timeline_start": 0.0, ...},
"loudness": {"before": {"integrated": -20.6},
             "after":  {"integrated": -16.1, "true_peak": -1.0}}
```

A master that misses its target by more than 1 LU is refused, and the render is
left as it was.

Now the step that matters:

```sh
uv run proofcut -C ~/proofcut-demo/proj verify ~/proofcut-demo/demo.mp4
```

```
"similarity": 0.971,
"heard_words": 34,
"expected_words": 34
```

proofcut just transcribed its own render and diffed it against what the timeline
claims. 34 words expected, 34 heard, and the retake is not among them. The
score under the voice did not cost a word. A render that quietly dropped a
segment, or a cut that landed a frame early, shows up here as a number rather
than as something you notice a week later.

Frame counts have their own check, because a duration and a frame grid are
different questions:

```sh
uv run proofcut -C ~/proofcut-demo/proj frames ~/proofcut-demo/demo.mp4
```

```
"agrees": true, "delta": 0
```

## 8. End on a card

This step needs ImageMagick 7; skip it without. A card is drawn from a template
at the project's own frame size, and a `tail` plays it after the last word:

```sh
uv run proofcut -C ~/proofcut-demo/proj card new demo-end --template endcard --set mark=proofcut --set "footnote=cut, scored, mastered and checked"
uv run proofcut -C ~/proofcut-demo/proj tail --asset card:demo-end --seconds 4
uv run proofcut -C ~/proofcut-demo/proj export ~/proofcut-demo/final.mp4 --render --loudness -16
uv run proofcut -C ~/proofcut-demo/proj verify ~/proofcut-demo/final.mp4
uv run proofcut -C ~/proofcut-demo/proj frames ~/proofcut-demo/final.mp4
```

```
"tail": {"asset": "card:demo-end", "seconds": 4.0, "frames": 96},
"frames": 385
"similarity": 0.971, "heard_words": 34, "expected_words": 34
"agrees": true, "delta": 0
```

Four seconds and 96 frames longer, and still agreeing. The tail is project
state, so the next cut keeps it rather than dropping it: it was never applied
to a file after the render. The score ends with the edit, so the card holds
over silence.

## 9. Look at it

```sh
uv run proofcut -C ~/proofcut-demo/proj open
```

The workspace: the transcript with the cut struck through, the timeline lanes,
and a player that builds the edit live from the source — so seeing a cut costs
no render at all. Press `?` for the shortcuts.

Play to about three seconds and the frame reads `BLUE 3s`; to about ten and it
reads `RUST 0s`. That is the picture lane doing what the shot table said it
would, and it is why the demo footage is labelled.

## Shortcuts

`scripts/make_demo.py ~/proofcut-demo --build` does steps 1 and 2 in one go — the
same commands, run for you — if you would rather start from a seeded project
and skip to step 3.

## What to try next

- `proofcut -C ~/proofcut-demo/proj cut vo 11:23 --plan` again after cutting: it
  reports `already_cut`, because the words are still addressable even though
  they are no longer on the timeline.
- `proofcut -C ~/proofcut-demo/proj caption-style --size 64` then
  `proofcut -C ~/proofcut-demo/proj captions ~/proofcut-demo/demo.ass --burn ~/proofcut-demo/demo.mp4`
  — captions come out of the *timeline*, not the transcript, so they land where
  the words actually play. Note that `export --render` does **not** burn them
  — the burn is its own opt-in step against a finished file.
- `proofcut -C ~/proofcut-demo/proj finish-report` — everything the truth strip
  draws. Its `captions.burned` reads `"unknown"` after the hand-run burn
  above, and correctly: it reports what the last *render pipeline run* did,
  and a `captions --burn` on an existing file is not one. A manifest can say
  captions are configured while nothing on disk was ever burned, and this is
  the field that stops that reading as clean.
- `proofcut -C ~/proofcut-demo/proj reframe blue --rect 0,0,320,180` then
  `proofcut -C ~/proofcut-demo/proj reframe-sheet` — a crop window is a rect in the
  clip's own *source* pixels, so no cut can invalidate one. That rect keeps
  the top-left quadrant, which the corner tags make obvious: `TL` survives and
  the other three are gone. The sheet draws every window on the frames it
  actually governs, and Frame mode in the window is the same sheet with
  coverage chips over it.
- Point step 1 at your own voiceover instead. Nothing in the walkthrough after
  step 2 knows the footage was generated.
