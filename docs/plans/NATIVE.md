# proofcut — making "cut with proofcut" true

Provenance: Tyler asked on 2026-09-14 whether it is fair to say he made
several YouTube videos, and the launch video, "almost entirely using proofcut
+ Claude Code". It is not, yet: LAUNCH.md § Step 1 already says the launch clip
is a compositor script (`~/proofcut-work/spikes/launch-v4/clip.py`), and no
essay cut with proofcut has been posted — Scream v8 and Lambs/Longlegs v10 are
both cut and unposted (goodsometimes `analytics.md`), and both finish outside
proofcut. He then asked what proofcut would need for the claims to be true,
and whether the launch clip's editing style should become a feature. This is
the answer, written as a plan. Sources: a native rebuild of Lambs/Longlegs v10
(HISTORY.md § The Lambs/Longlegs native rebuild), a measurement of MLT's
retime and easing (`~/proofcut-work/spikes/mlt-retime/FINDINGS.md`), and a
prior-art survey of screen-recording editors
(`~/proofcut-work/spikes/screen-mode/PRIOR-ART.md`).

**The finding that frames it: the two claims cost very different amounts.**
The essays are mostly proofcut already, and the native rebuild of v10 turned
up six proofcut defects before it turned up a missing feature — four of them
the kind that renders the wrong film at exit 0. The launch clip is a different
editing grammar (a screen recording retimed and framed by UI events, not a
voiceover cut by words), and MLT turns out to have every primitive it needs.

Status lives **only** in the wiki's Open items table; this file never carries
a status header. When a step ships, HISTORY.md gets a named section and the
step here gains a one-line pointer.

## What "cut with proofcut" means here

**The delivered file comes out of `proofcut export` with nothing after it.**
A script that drives proofcut is fine — `build_longlegs.sh` is a record of
proofcut calls, exactly what CLAUDE.md's CLI-parity rule is for. Capture is
fine too: a screen recorder is a camera. What does not count is the category
v10 still has: ffmpeg or numpy reshaping the render after proofcut is done
with it (`music_bed.py`, the cold-open concat, `clip.py`).

## Part A — the essays

### What the rebuild measured

v10 rebuilt as `Project/proofcut-native` from the same tables
(`assemble_longlegs_native.py` imports `assemble_longlegs.py`'s HOLDS and
CUES rather than restating them) and v10's own VO transcript, with the cold
open as a proofcut `head` and the eight film holds as `hold add`: the timeline
is **352.97 s, v10's exactly**, all eight holds land on v10's in-points, and
after the fixes below whisper hears v10's own line in every hold and the cold
open (8609 of 8835 frames at SSIM ≥ 0.9; the rest is the cold open's framing).
Getting there fixed, each with a test that failed first:

| defect | what it did | commit |
|---|---|---|
| refusal named `proofcut transcript attach`; picker copied `proofcut migrate -C <path>` | both commands are refused by argparse | `f601848` |
| a splice the shot plan refused had already registered its silence | orphaned clip, and an undo press that changed nothing | `f0710b1` |
| a read-back segment ended at float start + duration (60.199999999999996) | a hold resolved past its own silence; export would refuse, or with slack render the film audio shifted by the hold's length | `7429abe` |
| the holds lane read the clip from the cue's in-point, not `play_at` | every hold played the seconds *before* its line | `a3ae39f` |
| `_transcribe_span` read a `words` key whisper does not write | `hold_check` heard "" on every real span, v10's included | `a3ae39f` |
| MLT plays the first two of six unlabelled channels | the cold open and three Longlegs holds at −47 to −53 LUFS against v10's −16: the centre, the dialogue, never reached the render | `bb9e8bd` |

**Still outside proofcut after those**, and each is a real feature:

### A1. Placed music cues, levelled under the VO

Shipped — HISTORY.md § The Scream native rebuild.

v10's bed is three passages alternated with 2.5 s crossfades, 22 LU under the
VO, out across every hold; Scream v8's is three placed cues (`A Cruel World`
from src 2.6, `A Killer Confrontation`, `A Cruel World` again) with 4.0 s and
2.5 s crossfades. proofcut's bed is one asset from its head at the asset's own
level. PLAN.md § The A2 music lane already said the next step would be
"cue *placement*, in the shape the cue table already has for picture — a new
item to raise when a video wants it"; two videos want it.

**Recommended:** a list of music cues, each `(asset, clip_id, word_index_start,
src_in, crossfade)` running to the next cue's start (or the film's end),
`build_shots`' own derivation for picture applied to sound — never a stored
length, for the reason the design note measured. One bed-wide `under` (LU below
the VO), measured the way a hold's gain already is. The bed still goes out
across every hold. The existing single bed reads as a one-cue list, so nothing
on disk changes meaning. *Open call:* whether the list rides `MUSIC_KEY` or a
new key — a new list key is the one shape CLAUDE.md says has bumped the schema
before; recommendation is to extend `MUSIC_KEY` (absent `cues` = today's
single bed) and not bump.

### A2. Film audio under the VO

Shipped as `hold under` — HISTORY.md § The Scream native rebuild.

v10 plays the fairy-tale narration 13 LU *under* the VO for one sentence —
the essay's own argument made with the edit (`assemble_longlegs.py`'s
FAIRY_TALE). A proofcut hold always opens a gap. **Recommended:** `hold add
--under-vo`, the same record with no splice: the clip's audio across a word
span at `under` LU below the VO, the bed out across it like any hold.
`hold_check` skips the seam check for it, since there is no seam.

### A3. A loudness target on export

Shipped — HISTORY.md § The Scream native rebuild. The two-pass `loudnorm`
recommended below is one gain and a true-peak limiter as of HISTORY.md § The
duck: past the peak ceiling `loudnorm` drops `linear=true` and rides the mix.

Both essays are mastered to −16 LUFS integrated / about −1 dBTP (v10 −16.0 /
−1.21, Scream v8 −16.2 / −1.13). proofcut measures (`finish.loudness`) and
never applies. **Recommended:** `export --loudness -16 --true-peak -1`, a
two-pass `loudnorm` over the render proofcut just made, recorded in the render
log, with `finish_check` reporting against the target it was asked for. It is
proofcut's own export doing it, so it counts.

### A4. The two cuts, rebuilt and posted

Both rebuilt and measured — HISTORY.md § The Scream native rebuild. Scream's
duck followed the same day (HISTORY.md § The duck). What remains of this step
is the posting.

- **Lambs/Longlegs:** re-run the native build on A1–A3, A/B it against v10 on
  the review page, then Tyler's two open calls (synth VO vs a re-record; *"It's
  not about her"*) — the wiki row's, not this plan's.
- **Scream:** the retake pass was done in Kdenlive and the v8 trims by
  `vo_trim.py` on `.kdenlive` files. `~/proofcut-work/projects/final-cut/proj`
  already holds the 63-segment edit; v8's two trims are two `cut` calls, its
  fifteen breath tames are `attenuate`, and the music is A1. Then the same A/B
  against `Video Final v8.mp4`.
- **Posting is Tyler's hand.** The claim "my last two essays were cut with
  proofcut and Claude Code" is true the day the second one is up.

## Part B — the launch clip's style, as proofcut features

### What `clip.py` does, and what proofcut has

| `clip.py` | proofcut today |
|---|---|
| a smooth speed ramp, output time → recording time (PCHIP), waits compressed, moments at 1x | nothing: `timeline.py` has no speed changes |
| beats anchored to UI events (`typing_started`, `sent`, `struck`) | everything is anchored to transcript words |
| an eased log-zoom camera over a 2560x1440 recording | `reframe` windows, discrete or linear (`--interp`) |
| the agent's render drawn into the preview's rectangle, following the camera | a stacked split pane; no inset |
| animated headline and footnote over a scrim | cards are full-frame stills |
| key clicks and UI sounds on events; a bed offset so its drop lands | one bed from its head (A1 covers the offset) |
| on-screen numbers read from the run, refusing a run that did not verify | `verify`/`check_frames` exist; nothing fills a card from them |

### What MLT can do — measured, not assumed

`FINDINGS.md`, melt 7.41.0, every number read back off a render:

- **`timeremap` is frame-exact.** A 1x/6x/1x `time_map` rendered 150 of 150
  frames on the frame asked, inside proofcut's own tractor nesting with a
  `qtblend` filter, and `melt -consumer xml` reports the remapped length.
  Keys are `<output frame><op>=<source seconds>`; smooth (`~`) eases and stayed
  monotonic.
- **Its trap:** a `length` property on the chain freezes the link on source
  frame 0 for every output frame, at exit 0, with the declared length and the
  file's frame count both correct. The writer must never emit one there.
- **Audio follows the map:** pitched by exactly the speed ratio by default;
  `pitch=1` keeps pitch at ~20 % RMS cost.
- **Easing:** 33 keyframe operators, not the three CLAUDE.md lists; 19
  measured within 0.5 px of their analytic curves. A scaling move is sub-pixel
  and antialiased; **a pure translation snaps to whole pixels**, so a slow pan
  at constant zoom may judder — unmeasured, and step B2 measures it first.

### What the field does, and the opening

`PRIOR-ART.md`: every screen-recording editor surveyed (Screen Studio, Cap,
Cursorful, Canvid, FocuSee, Camtasia, …) authors zoom as **segments** — start,
end, target, easing — and speed as a **constant per segment**; only MLT and
Remotion do a real ramp. Every auto-zoom decision found is made from clicks and
cursor telemetry, never from speech; nothing checks a retimed render against
its plan; no MCP server edits screen recordings. Screen Studio has not shipped
idle-pause compression. proofcut's two strengths — word-addressed edits and
checking the render against the edit — are exactly the gaps. Cap and Screenity
are AGPL/GPL: ideas, never code, in a PolyForm Shield repo; and avoid their
product names for anything proofcut ships.

### The build order

Each step is usable on its own, and each is judged on a served render.

- **B1. Events.** Shipped — see HISTORY.md § Events, and the pan that
  snapped to whole pixels; `locate` and `reframe` take the address; each later
  step adds it where it needs it.
  An event index per clip — `(clip_id, name, src_seconds)`,
  imported from the recorder's JSON — addressable anywhere a word is.
  Source-indexed like a footage description, so no edit can invalidate one.
  Named `events` (proofcut already has `marks`, and means something else).
- **B2. Eased camera windows on events.** Shipped — see HISTORY.md § Events,
  and the pan that snapped to whole pixels (the judder) and § Eased slides and
  event-addressed windows (the rest).
  `reframe` windows gain an easing
  name (a short list over MLT's operators: `linear`, `ease`, `smooth`) and an
  event or word address. First, measure a slow pan at constant zoom for
  judder; if it judders, a pan is written as a scaling move of the same rect.
- **B3. Overlays.** Shipped — see HISTORY.md § Overlays, built.
  A card rendered with alpha and placed on the canvas
  instead of filling it, with an opacity/position animation — a lower third.
  Also useful to the essays. Design below (§ B3, designed).
- **B4. Sound on events.** Shipped — see HISTORY.md § Sounds on events,
  built. A1's cue list plus one-shot effects at events
  (`send`, `land`, keystrokes). Design below (§ B4, designed).
- **B5. Retime.** Shipped — see HISTORY.md § Retime, built, which also
  records where the build departs from the design below. The hard one, last on purpose: it is the only step that
  changes what "timeline time" means, so captions, cues, holds, `locate`,
  `verify` and `check_frames` all have to compose through it.
  **Recommended authoring unit: a stretch** — "from event `sent` to event
  `words` in 1.0 s" — which the writer turns into `time_map` keys with easing
  between them. It matches the field's segment unit, is a sentence an agent
  can write, and still renders a ramp rather than steps. Refuse any map that
  runs the recording backwards (`clip.py` already had to). Source audio in a
  stretch that is not 1x is muted by default — `clip.py`'s own choice, with a
  separate VO — and `pitch=1` stays available. Design below (§ B5,
  designed); Tyler took all eight decisions on 2026-09-17.
- **B6. Inset.** The render drawn into a rectangle of the recording, following
  the camera: composite onto the recording's own track, then frame the
  composite. A nested tractor with the camera filter on it — measure before
  building.
- **B7. Re-cut the launch clip with proofcut**, ideally by an agent through the
  trial harness, and A/B it against `clip-v6.mp4` on Tyler's phone. Then
  LAUNCH.md § Step 1's "not cut with proofcut" line is retired with evidence.

### B3, designed — 2026-09-16

The spike is `~/proofcut-work/spikes/overlay-probe/FINDINGS.md`. Every claim
there was read back from rendered frames against a control, on both the
flatpak melt and Shotcut's portable melt. **MLT needs nothing new.** An
overlay is a `qimage` PNG with alpha on a blanked lane (the split pane's
playlist shape), composited by the writer's ordinary `qtblend` transition.
It animates with a `qtblend` filter whose `rect` carries opacity as a fifth
value. Alpha survives (0.998 opaque, 0.50 half), the transparent area is
untouched, the fade is frame-exact, and two lanes stack in order.

What the launch clip's type does (`clip.py` § `overlay`): a bottom gradient
scrim fades in and out. A headline rises 24 px while it fades in over
0.45 s, and a footnote does the same 0.25 s later. A headline handed to the
next one keeps the scrim up across the join.

**The shape.** An overlay is a **card with no background rect**, recorded in
`cards` like any other, so `card_new`, `card_reauthor`, variants, the
measured wrap and the font checks all apply unchanged. It is rendered at the
full canvas, so the template decides where the type sits. It is placed by a
new optional manifest key, `overlays`: `(card, start, end, in, out)`.
`start` and `end` are a word or an event address, resolved live through the
`Edit` on every build and never stored as seconds (the music bed's rule).
`in` and `out` are named animations. The writer lays overlays onto as few
lanes as keep their list order as the stacking order, and puts them above
the picture lane and its panes. An overlay is `_is_layered`'s ninth trigger.

**Findings the build has to hold to:**
- **Its entry reads the still from frame 0.** Keys count from the producer,
  so an entry reading from frame 30 plays 0-based keys early. This is the A2
  fade trap again.
- **A moving key is drawn at 1921x1081 and the resting key at exactly the
  canvas.** A pure 1:1 rise snaps to whole rows (B1's judder). Nudging every
  key leaves the type resting 0.2–0.7 px off and resampled. Nudging only the
  leaving key moves continuously and lands on the exact row.
- **One operator shapes both position and opacity**, so an animation is one
  curve, as `clip.py`'s is.

**Decisions** (a recommendation on each; Tyler took all six on 2026-09-16):

1. **One overlay is one card, and a stagger is two overlays** (recommended).
   Headline and footnote are separate cards with separate starts, and the
   scrim is a third (a shipped `scrim` template). The alternative, per-element
   timing inside one card, needs an animation language inside the SVG.
2. **Animations are a short named list, not raw keys** (recommended):
   `fade` and `rise` (24 px at 1080, scaled to the canvas; opacity with it),
   each with a length (default 0.45 s in and 0.3 s out) and an easing from
   B2's `EASINGS` (default `ease-out` in and `ease-in` out). Raw keyframes
   can come later if a real edit asks for them.
3. **The end is an address or a length** (recommended), e.g. `--until
   event:land` or `--for 3.2`. A length is resolved from the start's
   timeline time, so a cut inside it shortens nothing, unlike an end word.
   Refuse an overlay whose start is cut, as `build_shots` refuses an orphan.
   A derivation (`reel`) drops overlays and names them (`overlays_dropped`),
   the tail's rule.
4. **Templates shipped: `lowerthird` (headline + footnote, bottom left) and
   `scrim`, each with a portrait variant** (recommended). The portrait
   lower third sits above the reserved bottom fifth, and `card_safe_zones`
   reports on it as on any card. `TEMPLATE_BACKGROUND` gains a
   "none"/transparent entry so that report compares ink against the frame,
   not against a swatch the card never draws.
5. **The window draws overlays in the same step** (recommended): an `OV`
   lane in the timeline, and a preview layer placing the PNG at the canvas
   rectangle with CSS opacity and translate on the same curve. A film whose
   preview hides its type is the captions-not-in-the-file shape inverted.
   The alternative is CLI/MCP/writer first and the window as a follow-up
   step.
6. **The check is the export reply plus a readback test, not a new op**
   (recommended). `export` names the overlays it drew (the `music` field's
   precedent), and a real-melt test reads one frame at each overlay's
   plateau and its alpha against the control, as the spike did. A
   user-facing `overlay_check` is deferred until a real film asks for it.

Name: `overlay` (`overlay add/list/remove`, tools `overlay_add` …). No
schema bump, because the key is additive and optional.

### B4, designed — 2026-09-17

The spike is `~/proofcut-work/spikes/sfx-probe/FINDINGS.md`. Every number
there was read back sample by sample from renders by the flatpak melt and
Shotcut's portable melt, and the two agreed on every case. **MLT needs
nothing new.** A hit is an ordinary entry on an audio lane: the silent-WAV
padding and `mix sum=1` that the bed already uses, with a per-hit `volume`
filter.

What the launch clip does (`clip.py` § sound):
- **The keystrokes:** 279 of them, a random pick from 8 generated key
  variants at −18 dB ±3, thinned to no two within 45 ms.
- **The one-offs:** `send`, `land` and `strike` at named events, at −12 to
  −19 dB.
- Everything is placed to the sample, and the sounds are not ducked.

**Findings the build has to hold to:**
- **A file MLT counts as one frame long plays nothing, and an entry that
  claims more frames than its file has moves every later hit early.** Both
  happen at exit 0. The launch clip's keystrokes are 1.4 frames long, so
  both traps apply to them. So the writer never places the sound file
  itself. It places a derived copy padded to a whole number of frames, at
  least two.
- **Frame f starts at sample `floor(f × SR / fps)`** on both melts, at 30
  and at 29.97 fps. So a hit between frames is placed exactly by leading
  silence in that copy.
- **Rounding hits to frames moves them by up to 16.7 ms** and turns the
  run's 36–49 ms gaps into only 33 and 67 ms. Placing them between frames
  cost 2.6 s of melt for 279 hits, against 0.9 s rounded.

**Decisions** (a recommendation on each; Tyler took all eight on 2026-09-17):

1. **A sound is an imported clip, and a hit is a record in a new optional
   key, `sounds`** (recommended). The record is `(asset, clip_id, word_index
   | event | every, gain_db)`. `every` names an event, such as `key`, and
   places the sound at each occurrence of it. That makes 279 keystrokes one
   record, not 279. Import already resolves, probes and dedups files, which
   is the bed's precedent. No schema bump.
2. **A run can have variants, jitter and a minimum gap** (recommended):
   `assets` is a list, `jitter_db` has a default of 0, and `min_gap` has a
   default of 0.045 s (`clip.py`'s).
   - The pick and the jitter are seeded from the record, so every build
     writes the same document.
   - The alternative is one sound per record and no randomness, which makes
     a typed run repeat one sample 279 times.
3. **Placement between frames** (recommended). The derived copy's lead is
   quantised to 1 ms, so the cache holds at most 34 copies per sound at
   30 fps, and the error is at most 0.5 ms.
   - The copies live in `cache/sounds/`, keyed by `(asset, lead)`, and are
     never entered in the manifest.
   - The alternative is rounding to frames. It is cheaper, but it changes a
     typed rhythm. Nobody has listened to the difference; an ear A/B can be
     served first if you want one.
4. **Anything that goes missing is named, and some of it refuses**
   (recommended):
   - A single hit whose word or event is cut refuses, by overlay's rule.
   - An `every` run skips the occurrences that are cut and counts them
     (`sounds_skipped`), because cutting some keystrokes is normal.
   - `reel` drops sounds and names them (`sounds_dropped`), the tail's rule.
   - `sounds` becomes `_is_layered`'s tenth trigger.
5. **Level** (recommended):
   - Sounds do not duck the bed, and the bed does not duck under them. The
     duck stays keyed off the Edit's audio, as `clip.py` and `duck.py`
     already are.
   - `export --loudness` applies to the whole mix, as it does now.
   - The lanes are greedy first-fit, with a `mix` transition each.
6. **Generated sounds ship with proofcut** (recommended):
   `proofcut sounds generate DIR` writes `make_sfx.py`'s set: 8 keys, plus
   `send`, `land` and `strike`.
   - It is ported to the standard library and generated on demand, not
     vendored, so there is no licence question (`make_demo`'s precedent).
   - The alternative is to bring your own files.
7. **The window draws a sound lane of ticks and plays nothing**
   (recommended). The preview does not play the bed either, so a silent
   preview is consistent. The alternative is to leave the window out of B4.
8. **The check is the export reply plus a real-melt readback test**
   (recommended), as in B3:
   - `export` names the records and the hits it drew.
   - A test finds each hit's onset in the rendered PCM, to the sample.

Name: `sound` (`sound add/list/remove`, tools `sound_add` …).

### B5, designed — 2026-09-17

The spike is `~/proofcut-work/spikes/retime-compose/FINDINGS.md`, on top of
the link-alone spike above. Every number was read back from renders on both
the flatpak melt and Shotcut's portable melt, and the two agreed on every
case. **MLT needs nothing new: one `timeremap` link per Edit segment.**

What the launch clip does (`clip.py` § TIME): the recording is played through
one smooth ramp from output time to recording time. It is PCHIP through
hand-placed `(output s, recording s)` knots, and it refuses a map that runs
the recording backwards. The camera, the type and the sounds are all timed in
*output* seconds. Sounds sit at `to_output(event)`, and the music is never
retimed. The recording's own audio is not used.

**Findings the build has to hold to:**
- **MLT's `~` spline runs the launch clip's map backwards.** On `clip.py`'s
  knot shape, 1x beside ~30x, it made 52 backward steps, the worst jumping
  from source frame 1361 back to 1352. That happens at exit 0, with the
  frame count right. The four-key map in the earlier spike stayed monotone
  only because it was simple. **The writer never emits `~` in a
  `time_map`.**
- **Per-frame linear keys, sampled from proofcut's own PCHIP, are exact
  enough and cost nothing.** 900 keys over 900 frames made no backward step
  and were never more than 1 frame off (a rounding difference). melt took
  3.18 s, against 3.15 s for 16 keys.
- **A remapped chain's positions are output frames.** An entry's `in`, a
  `qtblend` key and a `volume` key all count them. An opacity key at 75
  switched at output 75, not at 62.5, where source frame 75 plays. With
  `in=30` it switched at 45, which is the existing `src_in` rule. **So a
  reframe window on a retimed segment is keyed in output frames, and a slide
  eases in output time**, which is what `clip.py`'s camera does.
- **One remapped chain per segment is exact** (0 frames off). Its declared
  length agrees through `melt -consumer xml`, so `declared_frames` needs no
  exception.

**The shape.** A retime is a **warp**: a monotone map from render time to
Edit time. It is the head's constant offset (`head_seconds`) generalised to
a curve.
- **It is stored as stretches in a new optional key, `retime`.** A stretch is
  `(clip_id, from, to, seconds)`. `from` and `to` are a word or an event
  address. They are resolved through the `Edit` on every build and never
  stored as seconds (the music bed's rule). Time outside every stretch plays
  at 1x.
- **The warp is PCHIP through the knots the stretches make.** The knots are
  in `(render s, Edit s)`, the orientation `clip.py` uses. It is sampled once
  per output frame.
- **Only the Edit track is remapped.** Each segment's chain gets the slice of
  the warp that covers it, with keys from 0.
- **Everything on another lane keeps its own speed.** Overlays, sounds, the
  bed, holds and captions are still planned in Edit seconds, as today, then
  moved to the render time the warp gives their start (and their end, where
  that is an address). An overlay's animation and the music play at 1x.
- **It is `_is_layered`'s eleventh trigger**, because auto-editor cannot
  retime.

**Decisions** (a recommendation on each; Tyler took all eight on
2026-09-17, and the build departed from 3 and added two refusals — HISTORY.md
§ Retime, built):

1. **Stretches, PCHIP between them** (recommended; the unit was taken
   2026-09-15). A stretch says "from `sent` to `words` in 1.0 s". The ramp in
   and out of it is the curve's, as in the approved clip. The alternative is
   an explicit `ease` per stretch, with exact 1x between stretches. That is
   more predictable but is not the look that was approved.
2. **The Edit's own audio is muted wherever the speed is not 1x**
   (recommended). "Not 1x" means more than 5% off, with a 40 ms fade at each
   edge. This is `clip.py`'s choice: its recording is silent, and speech is
   placed separately. The mute is `volume` keys on the segment's chain, in
   output frames (finding above). The alternative, `pitch=1`, keeps the
   sound at the earlier spike's ~20% level loss. It could be a per-stretch
   `audio: "pitch"` later.
3. **A picture cue over a retimed stretch is refused for now** (recommended),
   by name. Does b-roll under a retimed stretch play at 1x or retime with the
   Edit? That is a question about a film nobody has cut yet, and the launch
   clip has no b-roll. The window widens when the model does.
4. **Reframe windows follow the warp** (recommended, and the only option
   that matches `clip.py`). A window addressed by an event or `src_start`
   takes effect at that moment's render frame. Its keys are written in
   output frames on the segment's chain, and so are its blur-fill and pane
   nodes.
5. **The clocks** (recommended, the head's precedent):
   - `timeline_view`, `locate`, `status` and `caption_view` stay in Edit
     time. They gain a `retime` field listing the resolved stretches.
   - `export`, `check_frames`, `add_captions`, `verify` and `finish_check`
     map through the warp.
   - `verify` drops the expected words that fall inside a muted stretch and
     reports how many (`retimed_words_dropped`), next to its diff. That keeps
     the stretch from hiding a real miss, which is the `unspoken` rule.
   - Captions skip muted words too.
6. **The window previews at 1x and says so** (recommended). A chip reads
   "retimed — preview plays 1x", and the timeline draws each stretch as a
   band on the Edit lane. Playing the render is already `/api/output`'s job.
   The alternative is stepping `playbackRate` per stretch. That is an
   approximation of a curve that the preview would then claim to be.
7. **Refusals** (recommended):
   - A stretch whose address a cut removed refuses, by the overlay's rule.
   - `from` must come before `to`, and `seconds` must be positive.
   - Two stretches must not overlap.
   - A warp that runs backwards anywhere refuses, checked on the sampled
     frames the way `clip.py` checks its grid. This cannot happen with
     monotone knots and PCHIP, and the check is there because the spline
     finding shows it can happen at exit 0.
   - `reel` drops the retime and names it (`retime_dropped`), the tail's
     rule.
8. **The check** (recommended, as in B3 and B4):
   - `export` reports each stretch's Edit span, its render span and its
     speed.
   - A real-melt test decodes a self-identifying source and asserts every
     output frame against the warp, within 1 frame.
   - A second real-melt test asserts that a sound at an event inside a
     stretch lands on the event's retimed frame.

Name: `retime` (`retime add/list/remove`, tools `retime_add` …).

## Decisions for Tyler

Taken 2026-09-15: Tyler accepted every recommendation below (HISTORY.md § The
Scream native rebuild). Kept as written, since they are the record of what was
asked.

Grouped by what is actionable today; each carries a recommendation.

1. **The line-edge fault in `hold_check`** (actionable now). Built and held
   back in `git stash` ("line-edge fault, held for Tyler"): a hold whose line's
   start or end is not heard counts as a fault. Measured on 16 real spans it
   separates right from wrong 8/8 each way, where word recall does not.
   It disagrees with `test_hold_check_over_the_wire`, whose "clean" case hears
   "hello from the stub" for "i know what you did" and asserts zero faults.
   **Recommended: take the fault**, and have that test's stub hear the line —
   the test was asserting that nothing compared the two.
2. **A1's key** — extend `MUSIC_KEY`, no schema bump (recommended), or a new key.
3. **Part A before Part B** (recommended): the essays are days, Part B is weeks,
   and the essays are what the channel's October wants.
4. **B5's unit** — stretches (recommended), per-segment speed multipliers, or a
   raw time map.
5. **Who cuts the launch clip in B7** — an agent, recorded (recommended), or
   Tyler driving the CLI.
