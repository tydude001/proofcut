# The vendored caption face

`src/proofcut/web/FONTS.md` ships three typefaces for the *browser*. This
directory ships one for the *renderers* — libass, and librsvg through
`magick` — and the two deliveries have nothing in common but the word "font".
A `woff2` beside `app.css` does not put a face where `fc-match`, libass or
librsvg can see it (PLAN.md § A default font), so neither directory can stand
in for the other.

| file | family | role | licence |
|---|---|---|---|
| `Outfit[wght].ttf` | Outfit (variable, 100–900) | `captions.CAPTION_FONT` — the caption default every `PRESETS` entry names | OFL-1.1 — `OFL-Outfit.txt` |
| `static/Outfit-Regular.ttf`, `static/Outfit-Bold.ttf` | Outfit 400 and 700 (static) | the same face, staged into libass's own font directory for a **Windows** burn only (`fonts.libass_fontsdir`) | OFL-1.1 — `OFL-Outfit.txt` |
| `pages/ZillaSlab-SemiBold.ttf` | Zilla Slab 600 | the `endcard` graphic template's tagline, served to a graphic's page at `/_proofcut/fonts/pages/` and nowhere else | OFL-1.1 — `pages/OFL-ZillaSlab.txt` |

## Why one file and not the brand set

Captions are the only exposed surface. libass's `\fn` takes exactly one family
name, so a caption style that names an absent face has nowhere to fall back to.
Card templates use CSS fallback *stacks* ending in a generic
(`graphics.py`'s `title_font`/`body_font`/`quote_font`), which is the
SVG-native survival trick, so they degrade rather than vanish. Zilla Slab is
the brand's other face and belongs to the channel preset pack
(PLAN.md § The completion queue), not here — nothing proofcut draws asks for it.

## Provenance

Byte-identical to `Branding/Fonts/Outfit[wght].ttf` on the NAS, which
goodsometimes `branding.md` § Type names as canonical, together with its
licence. Both faces there are SIL OFL, which is what makes vendoring the file
rather than naming the family legal as well as sensible.

`~/.local/share/fonts/Outfit[wght].ttf` on this box has the same md5
(`e31a3aa5fce3366bcadb8e9027f26178`) but was fetched incidentally, by
goodsometimes' own brand-art tooling, months before captions named the family.
That is the failure this directory exists to close: the caption default
resolved on this machine **by coincidence**, and on a fresh box it would have
resolved to whatever fontconfig substitutes, silently, with every check still
clean.

## The static pair, and why Windows gets it

On Windows a registered, GDI-loaded Outfit still did not draw: libass under
DirectWrite picked ArialMT. So a Windows burn hands libass its own font
directory (`ass=…:fontsdir=fonts`), and a face there is matched before the OS
is asked. **The variable file cannot go there.** libass names a face in that
directory by its legacy family, name ID 1, and the variable file's default
instance is Thin, so its ID 1 is `Outfit Thin` and a request for `Outfit`
falls through to a substitute. The static files' ID 1 is `Outfit`, and on
libass 0.17.4 `(Outfit, 400)` and `(Outfit, 700)` select `Outfit-Regular` and
`Outfit-Bold` from the directory, ahead of fontconfig's variable Outfit
(HISTORY.md § The first windows-demo run).

They sit in `static/` so `fonts.vendored()` does not see them, and `install`
never puts them where fontconfig looks: the Linux and macOS burns resolve the
variable face exactly as they were measured.

Provenance: `fonts/ttf/Outfit-Regular.ttf` and `fonts/ttf/Outfit-Bold.ttf`
from github.com/Outfitio/Outfit-Fonts at commit
`902773808eb372f70fb34e8946dd1ffe604efc79`. That commit's
`fonts/variable/Outfit[wght].ttf` has the vendored file's md5, so the pair is
the same release. SHA-256: Regular
`3b64ac4f6ab6a8eebddd4b0bc03c811c43602e11e176382ab0ee6be615ab861b`, Bold
`f620b69582e06d7e1b3bbde74ed8c5876eadabb038390780db2a3414a1490197`. The OFL
declares no Reserved Font Name, so the family name carries over unchanged.

## The page faces, and why they sit in `pages/`

An animated graphic's page loads fonts only from this directory, served at
`browser.FONTS_PATH` (TRAPS.md § Animated graphics: the page is served, never
opened), so a face a built-in template names has to ship here.
`pages/ZillaSlab-SemiBold.ttf` is the `endcard` template's tagline
(docs/plans/ENDCARD.md). It sits in `pages/` so `fonts.vendored()` does not
see it: `install` never puts it where fontconfig looks, and no caption or card
starts resolving it by accident. The paragraph above still holds for those.

Provenance: byte-identical to `Branding/Fonts/ZillaSlab-SemiBold.ttf` on the
NAS (md5 `c003d19d75b3e94953705b8dcf976e44`, SHA-256
`aafcb295b88d520357db1ecf9a1c3167055e87e9ddf5f63e560cbd139ec2805e`), with
`OFL-zillaslab.txt` from the same folder as its licence. Copyright 2017, The
Mozilla Foundation; the OFL there declares no Reserved Font Name.

## Installing it where fontconfig looks

`fonts.install()` copies this file into `$XDG_DATA_HOME/fonts`
(`~/.local/share/fonts` by default), which `/etc/fonts/fonts.conf` puts on the
search path via `<dir prefix="xdg">fonts</dir>`. It is idempotent and compares
by content, so a box that already has the face is left alone.

## Settling which face actually drew

Never by `fc-match`. It answers "is the family present", and libass asks
something else — on this box its first pick for `Noto Sans` is a Nerd Font
symbol face that only reaches the real one by failing to find a Latin glyph
(HISTORY.md § The approvals round, answered).

There are **two** flavours of wrong here and they do not look like each other,
which is why the probe compares against the machine's own substitute rather
than against any particular wrong answer. Traced through `ffmpeg -v verbose`:
an *absent* family and `DejaVu Sans` both primary-pick `NotoSansArabic-Bold`,
fail the glyph, and fall back to `NotoSans-Bold.ttf`; the literal `Noto Sans`
primary-picks the Nerd Font face, fails the same glyph, and falls back to the
same file. All three draw their Latin glyphs out of one face — the pixels that
differ are the **space advance**, still supplied by whichever primary was
picked. A probe scored against "does it differ from font X" would therefore
score two wrong answers as one right one.

`fonts.probe()` burns the same line twice, once under the family and once
under a family that cannot exist, and compares the pixels. Identical renders
mean the name is not drawing, whatever `fc-match` says. That calibration is
the whole point of the second burn: it needs no golden image, so it cannot
rot, and it is the one comparison that distinguishes "substituted" from
"resolved" without knowing in advance what the substitute would look like.
