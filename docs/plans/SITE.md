# A landing page for proofcut — considered 2026-09-24, not built yet

The prompt was a story going round: a career coach's decade-old WordPress
site, rebuilt in Next.js in an afternoon with Claude, then a Calendly
booking link and her own edits from there. Two months later revenue was
up fourfold, **credited to conversion**: more of the people already coming
to the site went on to book. The question it raised was whether proofcut
should have a free site of its own, such as `tydude001.github.io/proofcut`.

## Why not yet

**A site converts traffic. It does not make any.** Her business had twenty
years of clients and a steady stream of visitors that the old site was
losing. proofcut's gap comes before that. As of this writing Show HN was
refused (2026-09-21), the tester posts are down, and no stranger has done
the Mac run yet (LAUNCH.md § Step 2). A landing page now would be polishing
the step after the one that is failing.

Where proofcut already shows up is covered:
- **README.md** is the front door on GitHub, and PyPI shows the same file
  as its project page.
- **The MCP registry's `websiteUrl`** points at docs/DEMO.md (LAUNCH.md
  § Step 4).
- **The GitHub and Gitea descriptions have no website link on purpose**:
  Tyler's pick, 2026-09-16 (SHOWCASE.md).

A site would be a fourth front door to keep in sync with the other three
while there is nothing on it that they lack.

## What would make it worth building

Either of these, and the first is the likelier:

1. **The launch recording exists** (LAUNCH.md § Step 1). A landing page is
   mostly a frame for a sixty-second clip of proofcut cutting a film, and
   the clip is the thing the README cannot show. Without the clip the page
   is the README with nicer type.
2. **There is something to sell.** Open-core is deferred until the demo run
   (wiki `decisions.md`, PolyForm Shield). Once a paid tier exists,
   conversion is the problem her site solved, and a page that says what it
   costs and what to do next is where it gets solved.

## The cheap version, when one of them lands

- **GitHub Pages, free for a public repo.** It fits the mirror: GitHub is
  fed only by Gitea's push mirror (CLAUDE.md), so a `gh-pages` branch or a
  Pages Actions workflow committed on Gitea goes out with the ordinary sync.
  Public-repo Actions minutes are free. Nothing new to host, and **nothing
  is merged on GitHub**, the same rule as every PR.
- **One static page**, no framework: the clip, the install line, three
  short points on what proofcut does, and links to GitHub, docs/DEMO.md and
  docs/MANUAL.md. Its facts (tool count, version, supported OSes) are the
  README's. Either link to the README for them or state none, because a
  hand-copied count goes stale the same way the six version literals would
  without `tests/test_version.py`.
- **Custom domain optional.** `proofcut.dev` or similar runs about $12 a
  year. The `github.io` address works on day one, and a domain can come
  later without breaking it.
- **The copy follows the public-prose rules**: no em dashes (they read as
  an AI tell), never "generate" or "from scratch" (SHOWCASE.md), and no
  frame of footage proofcut does not own (CLAUDE.md § the README
  screenshots).
- **Once it exists, point the registry's `websiteUrl` at it** and decide
  again whether the GitHub description should carry the link.

Status of this item lives in the wiki's Open items table
(`proofcut-landing-page`), not here.
