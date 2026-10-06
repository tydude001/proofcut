# DECISIONS — what was ruled out, and what would reopen it

Ideas that were measured or argued and then deliberately not done. Read the
entry before starting work that appears here. Where the full derivation already
lives in another doc, the entry is a pointer and the conditions for reopening it;
where it does not, the reasoning is here. Moved from the homelab wiki's
decisions page 2026-10-06, so that a proofcut session sees them.

## Splitting `ops.py`

**Measured 2026-08-25 and left alone.** At 14,911 lines it was the largest file
in the repo by 4×. The standing reason not to split it ("every client routes
through one `ops` function, so splitting carries real risk") was wrong on both
counts:

- **Routing is untouched by a file split.** All four clients `import ops` and
  call `ops.foo(...)` (331 call sites: `cli.py` 141, `server.py` 105, `webui.py`
  79, `reviewserver.py` 6), plus one direct symbol import in the whole repo
  (`tests/test_ops_undo.py`, `REFRAME_KEY`). An `ops/` package with a
  re-exporting `__init__.py` leaves every call byte-identical.
- **The coupling is thin.** 89 public ops over 138 private helpers; the helper
  core reached by many ops is 13 helpers totalling 153 lines, and 98 of the 138
  are reached by three public ops or fewer. Module state is constants only.

So the split is cheap and safe. **It is not done because nothing measured says
the size costs anything**: no lint, test or trial finding names the file
([TRIAL.md](TRIAL.md)'s queue does not mention it). Reopen on the file's own
symptom — a session losing its place in it, an edit landing in the wrong op, a
helper duplicated because the existing one wasn't found. Then it is one commit:
`ops/` package, `__init__.py` re-exporting the public names, `_common.py` for
the shared core, one module per subject (render/export, edit-mutate, sheets,
reframe, transcript, cards/pack, captions, project/asset).

## Pre-push hooks

**Declined 2026-08-22, reversed by 2026-09-04:** `pre-push-ruff` and
`pre-push-secrets` run from `.git/hooks/pre-push.d/`. What the decline got right
still bounds what they buy: `pre-push-ruff` is near-redundant with linting on
every edit; neither reads what a public release actually risks — copyrighted
screenshots and local workflow detail in the docs, which no credential pattern
matches; and a pre-push hook sees only the commits being pushed, so it is not a
history scan. Don't treat them as a release check.

## A code of conduct

**Deferred 2026-09-17.** A CoC states how behaviour between contributors is
handled; with one maintainer and no outside contributors it would describe
nothing and exist only to move GitHub's community-health score, which is not a
goal. Enforcement also wants a private contact, and SECURITY.md already routes
private reports through GitHub. CONTRIBUTING.md sets expectations. Reopen if
real contributors arrive after Show HN: Contributor Covenant at
`.github/CODE_OF_CONDUCT.md`, keeping the rule that only
README/CONTRIBUTING/SECURITY/CLAUDE.md sit at the repo root.

## The licence: MIT, or closed source

**PolyForm Shield 1.0.0, decided 2026-09-10.** Derivation and the SPDX trap:
[HISTORY.md](HISTORY.md) § The licence, chosen. Two things held since:

- **Accepted cost: Glama grades the licence F** (seen 2026-09-16). GitHub
  detects no licence (`NOASSERTION`, Shield is not on the SPDX list), and Glama
  gives an A only to a recognised permissive licence. Nothing short of
  relicensing moves it.
- **A named competitor is the case for Shield, not against it** (2026-09-20):
  OpenCut's rewrite (MIT, ~90k★) lists an MCP server and headless mode on its
  roadmap — the lift Shield exists to refuse — and a public MIT grant cannot be
  withdrawn. A scoped MIT grant on a single file stays available if a
  collaboration ever needs one.

## Vendoring OpenCut's web UI, or collaborating upstream

**Declined 2026-09-20, both halves.** A copy is a replatform onto a state model
that decides, which the web UI must never be; upstream declines outside
contributions. The interaction design is worth harvesting, reimplemented:
[COMPETITORS.md](COMPETITORS.md) § What is worth taking, and what a copy would
cost.

## Keeping the name lucid

**Reversed 2026-09-13: renamed `proofcut`.** [plans/RENAME.md](plans/RENAME.md);
the record is [HISTORY.md](HISTORY.md) § The rename.

## A frozen-span render check, or a holds read

**Not built, 2026-09-20.** The check's own positive control failed — the defect
is a fact of the shot plan, not of any frame:
[plans/RENDER-CHECKS.md](plans/RENDER-CHECKS.md) § The positive control, run.
The holds read is designed and unbuilt (§ The holds read) because 0 of 20 trial
runs ever called `vo_extend`, so the sequence it guards has never been made.
Reopen if a brief hands an agent `vo_extend` and a run uses it, if a film shows
an unexplained freeze, or if one is hit by hand in the window.

## Learning b-roll picks from a person's earlier ones

**Not built, 2026-09-20.** [plans/PICKS-PRIOR.md](plans/PICKS-PRIOR.md),
including § What would reopen it.

## Snapping cut edges, or a word-edge drag handle

**Not built, 2026-09-21.** Two blind A/B rounds could not hear the difference
that snapping fixes: [COMPETITORS.md](COMPETITORS.md) § rescript. Reopen if a
listener hears a cut `cut_by_transcript` left dirty, or for mid-phrase cuts,
which were not listened to.
