# The proofcut logo

A tick split by one vertical cut, beside the name split by the same cut. The
tick is the proof: the render was heard back and it matches. The cut is the
edit. The name is Geist SemiBold (SIL OFL, the face the web UI ships), drawn
as outlines, so no font is needed to show it.

Every file is in [img/logo/](img/logo/). Each layout comes in four
colourways: `colour`, `reversed` (for dark grounds), `black` and `white`.

| Layout | File | Use |
|---|---|---|
| Horizontal | `proofcut-horizontal-*.svg` | The default |
| Stacked | `proofcut-stacked-*.svg` | Square spaces |
| Wordmark | `proofcut-wordmark-*.svg` | Where the tick would repeat something beside it |
| Symbol | `proofcut-symbol-*.svg` | Icons and avatars, where the name is said nearby |
| Symbol, small | `proofcut-symbol-small-*.svg` | 32 px and below |

## Colour

| Name | Hex | Use |
|---|---|---|
| Proof blue | `#2461BC` | The tick and the cut bar, on light grounds (5.8:1 on paper) |
| Proof blue, light | `#73A8E7` | The tick and the cut bar, on dark grounds (7.5:1 on ink) |
| Ink | `#14130F` | The name; the icon tile |
| Paper | `#FBFBF9` | The ground |

The blues are the web UI's own `--accent` in its light and dark themes. Never
put the deep blue on ink: it is 3.1:1. The favicon uses `#3F7AD1`, between the
two, because a browser's tab bar can be either. Print values (CMYK, Pantone)
are not set; they need a printed proof.

## Rules

- **Clear space:** at least the name's x-height on every side. The files carry
  that margin already.
- **Minimum size:** horizontal 160 px wide, stacked 120 px wide. Below those
  the tick's cut closes, so use the symbol alone. The symbol goes to 16 px in
  its small drawing, which has a heavier stroke and a wider gap.
- **Backgrounds:** colour on paper or white, reversed on ink or any dark
  ground, one-colour black or white on photos and mid tones.
- **The cut bar** in the name always matches the tick.
- **Don't** recolour the name, stretch, rotate, retype it in another face, or
  put the colour version on a mid tone.

Not yet trademark-searched. A tick is a common shape.
