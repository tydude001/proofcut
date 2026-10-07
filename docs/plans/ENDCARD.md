# An end card that moves

Written 2026-10-07, after Tyler watched B7 run five beside v6 (HISTORY.md
§ B7, run five). The sound passed. The card did not: v6's "is more dynamic
and pretty, it has a little motion to it." He picked the route: **the tail
becomes an animated web graphic**, the capture proofcut already uses for
overlays, rather than a staggered still card.

## What v6 does

`~/proofcut-work/spikes/launch-v4/clip.py`'s `end_card`, read from source
(sizes at 1080 lines; `smooth` is eased; times from the card's first opaque
frame, after a 0.5 s crossfade off the film):

| line | type | colour | motion |
|---|---|---|---|
| `proofcut` | Outfit 700, 190 px, at 0.27H | paper `#faf5ec` | fades in and rises 20 px over 0.2 to 1.0 s |
| `the local-first AI video editor` | Zilla Slab SemiBold, 58 px, at 0.55H | amber `#e8a13c` | fades in and rises 14 px over 0.7 to 1.3 s |
| `github.com/tydude001/proofcut` | Outfit 500, 40 px, at 0.70H | dim `#a89f92` | fades in over 1.1 to 1.7 s |

There is no rule. Everything rests from 1.7 s, and the film fades to black
over the last 0.7 s. Run five's card is `bumper`, a still under one 0.6 s
fade, with a rule and a Zilla URL.

## What gets built

**1. A tail can be a graphic (`tail asset=graphic:<name>`).** Today
`_resolve_asset` takes a `card:` or an `image:`, and the tail writes one
`qimage` entry. A `graphic:` tail plays the graphic's intro from the tail's
first frame and then its still hold to the tail's end, as the same two
pieces an overlay graphic writes (`motion`'s phase pieces, each exactly its
length, TRAPS.md § Animated graphics). It goes where the card goes now: the
picture lane with cues, the Edit's own track without (TRAPS.md § Heads and
tails with no cues). `fade` keeps its meaning: the first intro frame
dissolves in over the film's last `fade` seconds.

- **A tail graphic must be opaque.** An overlay graphic is transparent, and
  a transparent tail would draw its type over black, the mirror of
  `_resolve_asset`'s refusal of an overlay card as a cue. The capture
  already reads every frame, so it records whether any pixel has alpha
  under 1, and `tail` refuses a graphic that does.
- **No outro.** The tail ends the film. A graphic that declares one has it
  ignored as a tail and reported, since a loop or outro that plays past the
  last frame has nowhere to go. A looping hold is allowed and loops.
- **A stale capture refuses at export**, as for an overlay
  (`graphic_capture`).
- **Silent**, as a card is, so `verify` has nothing new to hear.
- **The preview** shows the graphic's frames in the tail where it shows the
  card today, through the same asset route as an overlay graphic, never a
  second way of drawing one (TRAPS.md § The web UI).

**2. A graphic template, `endcard`**, in `graphic_templates/`, drawing
v6's table above: three slots (`mark`, `tagline`, `url`), four colours
(`background`, `mark_colour`, `tagline_colour`, `url_colour`, defaulting to
the goodsometimes launch values), and the staggered rise as its intro (1.7
s) with a still hold. A template brings its timing, and `intro` overrides
it as for any graphic.

**3. Zilla Slab is vendored next to Outfit.** A graphic page loads only
`/_proofcut/fonts/` (TRAPS.md § Animated graphics: "The page is served,
never opened"), and that folder holds Outfit alone, so the tagline would
fall back to a serif the browser picks. Zilla Slab SemiBold is SIL OFL 1.1,
which allows bundling with its licence file. It is 264 KB in the
public repo, and the template names it.

## How it is checked

- Op tests: a graphic tail writes intro then hold on the right lane, with
  cues and without. A transparent graphic and a stale capture are both
  refused. `tail` with no arguments reports the graphic. `fade` still lands
  over the film.
- **A real melt render, read back by luma and hue** (never a screenshot,
  TRAPS.md § The web UI): at 0.1 s into the tail the tagline band is ink;
  at 1.8 s it reads amber; the mark's top edge sits 20 px higher at 1.0 s
  than at 0.3 s. A no-graphic control render is the baseline.
- The full suite at `-n auto`, and the inventory tests widened for the new
  template (approved standing; named in the report).

## Then run six

The brief's end-card line becomes: an end card on dark, each line easing
in after the one before: "proofcut", "the local-first AI video editor" in
amber, "github.com/tydude001/proofcut". Nothing else changes. Its clip is
loudness-matched and served beside v6.

## What this does not do

- No still template matching v6's layout. The graphic is the card.
- No outro or fade-out on the graphic. The film's own fade to black is
  `music`'s fade and the export's, unchanged.
- Nothing for an overlay graphic changes.

## Cost

About a day. The capture adds 1.7 s × 60 = 102 frames to a render's first
export: a few seconds, cached by the capture's stamp after that.
