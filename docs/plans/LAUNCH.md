# proofcut — the launch plan: getting eyes on a public repo

Provenance: Tyler asked on 2026-09-11 how a public `tydude001/lucid` gets
seen. This is the answer, written as a plan. Sources: NEXT.md § 1 (the closed
loop is the thesis), TRIAL.md (both runs of it, 9 of 9 each, and the publish
rehearsal), docs/plans/PORTABILITY.md (steps 4–6 are unrun and need machines
this box is not), HISTORY.md § The licence, chosen, and the wiki's Open items
rows `lucid-publish` and `lucid-portability`. The finding that frames it:
**flipping the repo public produces no eyes on its own. GitHub is where
people land after seeing something elsewhere, so the work is one asset worth
sharing and a short list of places whose audience already wants it — and
every leak between "saw it" and "ran it" is a first-run failure on a machine
that is not this one.**

This is the one document for that work. Status lives **only** in the wiki's
Open items table (`~/projects/wiki/README.md`, `lucid-publish`); this file
never carries a status header. When a step ships: HISTORY.md gets a named
section, the step here gains a one-line "Shipped — see HISTORY.md § <name>"
pointer, and the wiki row updates.

## How to work this plan

- **The order is the plan.** Each step is cheap on its own and worthless out
  of sequence: a Show HN before a stranger's run turns the launch thread into
  a bug tracker, and a directory listing before the flip points at a 404.
  Steps 1 and 3 are done; 2 and 4 overlap, 4 gates 5, and **2 gates 5 only
  until its time-box** (§ Step 2, *Why it gates Show HN*) — the stranger is
  found by launching, so an open-ended wait on one never ends.
  **Step 2 ran ahead of the flip until 2026-09-13**, when the friend it
  counted on fell through (Tyler: nobody he knows can test it). A stranger
  can only reach a public repo, so the flip moved in front of it.
- **Steps 1 and 3 are Tyler's hands; step 2 is somebody else's.** An agent
  can draft every post, build the recording pipeline, and prepare the
  directory submissions, but cannot press record on a screen, cannot flip the
  repo, and cannot be the stranger.
- **What "eyes" means here is measured in strangers who ran `proofcut doctor`,
  not stars.** A star is a bookmark. The numbers that say the launch worked
  are issues filed by people who are not Tyler, installs reported by the MCP
  directories, and one outside pull request. Set no targets — there is no
  baseline — but record those three in HISTORY.md at the two-week mark.
- **A launch is a record too.** What was posted where, when, and what came
  back goes in HISTORY.md § The launch, dated, the same as a measurement —
  because the second launch (a release, a port shipping) will want to know
  which channel produced the strangers.

## What is already done, and must not be redone

- **The closed loop has run three times and passed every check each time** —
  the generated demo project on 2026-08-25 (31 turns, 184s, $1.31), real
  footage on 2026-09-03 (77 turns, 914s, $6.94, the agent choosing picture by
  subject, reviewing its own shot sheet, and defeating the duration trap
  unprompted), and a whole film on 2026-09-15 (43 turns, 192s, $2.27, 12 of
  12, the music and the level and the end card each measured in the delivered
  file). TRIAL.md § The result, § The result — real footage, § The result — a
  whole film. The launch does not need a run; it needs a *recording* of one.
- **The fresh-checkout rehearsal passed on this box**: clone, `uv sync`,
  `lucid doctor`, every command in docs/DEMO.md, the full suite at 1921
  passed. TRIAL.md § The publish rehearsal. What it does not say is anything
  about a machine that is not Fedora with this box's binaries.
- **The exposure scrub is applied and the history rewritten** —
  HISTORY.md § The repo, readied for strangers. Nothing in the repo names a
  reachable address, and CLAUDE.md § Conventions holds the rule.
- **The licence is settled: PolyForm Shield**, chosen before the flip
  because a shipped MIT version stays MIT. HISTORY.md § The licence, chosen.
  Open-core, a hosted tier and a commercial licence are later calls, and
  **none of them is a launch step** — see § What this plan deliberately does
  not do.
- **CI runs on three OSes, on GitHub, and all three are green** — Linux,
  macOS and Windows (HISTORY.md § The second run on macOS and Windows).
  The three-OS suite is what lets step 2 ask a Mac to try it at all.

## Step 1 — the launch asset: a watchable closed loop

Built three times — HISTORY.md § The launch clip, § The launch clip, re-cut
as an announcement, § The launch clip, third shape — and rejected; the fourth
shape is the one that stands, `~/proofcut-work/spikes/launch-v4/clip-v6.mp4`
(HISTORY.md § The launch clip, approved). How each round was judged and why
the clip is the way it is: `~/proofcut-work/spikes/launch-v4-review/PIN.md`.

**What it is.** A 47-second cut, and the full runs uncut, of one brief driven
from `proofcut init` to a verified render twice: in the agent pane of the
workspace, then in Claude Code with proofcut loaded as a plugin. The cut version is what every channel below posts; the
uncut one is the link under it for anyone who wants to check it was not
staged. The pitch is the thing itself: an agent on a timeline, the render
checking itself against the edit, and no cloud in the frame.

**Footage rule.** The recording shows only footage proofcut owns — the generated
demo project, Tyler's own recorded material (the co-hosted recording once
the second mic is routed, wiki `lucid-second-mic`), or public-domain footage
with its provenance written down (the clip's is NASA's restored Apollo 11,
`~/proofcut-work/spikes/launch-v4/media/PROVENANCE.md`). **Never a frame of
Scream.** CLAUDE.md's committed-image rule is about the repo, but a launch
clip is more public than a README and the same reasoning applies: a
promotional video of copyrighted film clips invites the one argument the
launch does not need. The real-footage trial's *numbers* are quoted in the
post (9 of 9, 45.23s, similarity 0.984, 123 of 123 heard); its *frames* stay
in `~/proofcut-work/spikes/agent-trial/`.

**It is not cut with proofcut, so never say it was.** The camera, the speed
ramps, the type and the mix are a compositor script
(`~/proofcut-work/spikes/launch-v4/clip.py`) over the two recordings. proofcut's own
moves are linear and it cannot retime, which is most of why the first three
shapes read as choppy. "The launch video was cut by the tool" is false, and
the line that is true is stronger anyway: the film inside the clip is the
agent's own render, untouched, and every number on screen is read from the
run that produced it. The clip refuses to build from a run that did not
verify clean. Captions burned, because every platform below autoplays muted.

**What it must show, in order**, so the 60 seconds carries the argument:
1. the brief, typed into the agent pane in plain language;
2. `import` and `transcribe` landing in the assets tab;
3. the cut — a retake struck through in the transcript, the timeline
   redrawing;
4. picture hung off phrases, the shot sheet coming back as an image the agent
   reads;
5. `export`, then `verify` and `check_frames` reporting agreement — the
   "it checked its own work" beat, said specifically: the render was
   transcribed and its words diffed against the timeline's, and its frames
   counted against the timeline's. kinocut says "quality gates", and read
   from source those score brightness and loudness, not agreement with the
   edit (`~/proofcut-work/spikes/launch-listings/LISTINGS.md` § kinocut's gate, read);
6. the same brief in Claude Code, no window, ending on the agent's own check
   — because the launch's headline is "an MCP server" and the clip otherwise
   never shows one;
7. an end card with the name, the tagline, the repo, and the two `/plugin`
   commands that install it.

**The instrument already exists.** `scripts/agent_trial.py` runs exactly
this loop; what it lacks is a viewer. The recording is mostly of the
*workspace* pane, because the workspace is what a stranger will open first,
and the README's screenshot is of it. The terminal beat is a real `claude`
session recorded byte for byte (`~/proofcut-work/spikes/launch-v4/mcp/`), not a mock-up. Drive the trial's own
brief through `POST /api/agent` (the panel's route) so the run in the video
is the run the trial measured — no second script, no drift.

**Done when:** the cut exists at 1920x1080, the uncut runs are on a share
link, and both are viewable on Tyler's phone. **The uncut runs are assets
on the `v0.22.0` release** (2026-09-13) — the plan's own permalink, not a
third-party share — and README.md links them under the clip. HISTORY.md
§ The uncut runs. **No vertical cut** (Tyler,
2026-09-13): every channel in steps 3–6 plays 16:9, a 16:9 editor window
cropped to a phone column loses most of what it shows, and the one vertical
outlet here, step 6's making-of short, is the slow channel and can have its
own cut when it happens.

**The README plays it on GitHub and links it on Gitea, from one commit.**
GitHub strips `<video>` and will not play a committed file — its renderer,
asked 2026-09-13, turned `<video>` into an empty paragraph and `![](clip.mp4)`
into a broken image — so the file is a GitHub upload (a `user-attachments`
URL), and it is never committed. The README carries only the bare URL on its
own line, which GitHub draws as a player and Gitea as a link. **Never add a
poster linking to that URL**: GitHub turns every link to an upload into a
player, image or not, so a linked poster drew a second player and no poster
(the first sync, 2026-09-13). The upload is
`~/proofcut-work/spikes/launch-v5/readme/proofcut-v0.23.0-clip-readme-1080.mp4` (v0.23.0), 9.2 MB, because
GitHub's cap on a free plan is 10 MB.

## Step 2 — one stranger's run, on a Mac

**Closed unmet 2026-09-20** — what the week measured, and what replaced the
gate, is at the end of this step. The rest is the record of how it was run.

**What it is.** A person who is not Tyler clones the repo, runs `uv sync`,
`proofcut doctor`, and docs/DEMO.md end to end on a machine Tyler does not own,
and reports every ✗ and every wrong number. A Mac, because that is where the
HN / X / Claude Code audience mostly is, and no person has run proofcut on one
(README.md § Requirements says so, and must keep saying so until this step
passes — GitHub's runner is not a person, § The Mac test in CI below).

**Why it gates Show HN.** The launch thread is where first-run failures are
reported publicly and permanently. Each "doctor said ✗ five times" comment
costs more than the post earns, and the fix — a resolver, a font, a melt
path — is a one-line change if it is found the week before. Portability
steps 4–5 (measure melt, libass and magick per OS) are the deep version of
this; step 2 here is the shallow one: does the two-minute demo reach a
verified render, yes or no, and where did it stop.

**The gate is time-boxed, because nothing else ends it.** Written open-ended,
it is a loop: Show HN waits for a stranger's Mac run, and strangers arrive
through channels that all come after Show HN. With 1 star and no outside
issue on 2026-09-14, nothing but the tester post below breaks it. So Show HN
goes on the first weekday morning after **a Mac report, or 7 days after the
tester post, whichever comes first**. Going without a report costs less
than it did when this gate was written: GitHub's macOS runner takes the demo
to a checked render (§ The Mac test in CI below), so what a person would
still find is the install path, not proofcut failing on macOS — and the
first comment's platforms paragraph already says no person has run it on a
Mac, so a Mac ✗ in the thread confirms a stated limit rather than exposing
a hidden one.

**Who.** A stranger, reached through the public repo — there is no friend
or colleague to ask (2026-09-13). The ask is in three places, each pointing
at the next: README.md § Help wanted, right above § Requirements, where a
Mac user reading the install list meets it; a pinned issue saying the same
thing, for anyone who lands on the Issues tab (step 3, item 9); and the
`Mac test report` issue form, `.github/ISSUE_TEMPLATE/mac-test.yml`, which
asks for the report zip (the summary block is an optional fallback, since
the zip's `report.txt` already holds it, and a required paste box ahead of
the zip read as the form refusing an upload), so a report comes
back in one shape whoever files it. It is scripted so it costs the tester
nothing to think about: clone, one command, attach a zip. What finds the
stranger is step 4's listings — their audience runs Claude Code, mostly on
Macs — and, if a week of those produces nobody, one "Mac tester wanted"
post in a room step 6 does not use, so no launch channel's first
impression is spent on a request. **That week ends 2026-09-20**, a week from
the registry listing going live on 2026-09-13, so with no report by then the
tester post goes up on **2026-09-21**. (It used to be the awesome-list PR's
badgeless deadline too; Glama listed on 2026-09-15, so that half is gone and
the PR carries its badge whenever it opens.) The room is one built for requests — r/alphaandbetausers
first, r/SideProject if its rules turn a tester call away; read the rules
the day of posting. Moving a step 6 channel ahead of Show HN to do this was
weighed on 2026-09-14 and rejected: self-promotion rules commonly read a
second post about the same project as spam, so a request posted in a launch
room spends that room's launch post. What was posted, in three rooms and in
two wordings, is `~/proofcut-work/spikes/launch-listings/POSTS.md` § The
Apple silicon tester posts; the drafts are not repeated here, since no
further one is planned.

**What comes back is a queue, not a verdict** — the trial's own rule (TRIAL.md
§ The queue). Every ✗ becomes a fix or a documented requirement; every
number that differs from the walkthrough's is either a platform difference
(record it in DEMO.md) or a bug.

**The install path is measured before it is built.** The dependency list
(whisper, auto-editor's upstream binary, melt, magick, espeak-ng) is the
obvious place curiosity dies, and the obvious fix is a container or an
install script. **Don't build either until this step says where the stranger
actually stopped** — a container that packages the wrong thing is the
inherited-blocker mistake (wiki `practice.md`), and `doctor` already prints
the fix under each ✗. If the stranger stops at the same binary twice, that
binary gets a one-line installer in DEMO.md § What you need. espeak-ng came
off this list on 2026-09-20 — the demo falls back to the same library as a
wheel, so it asks for no package manager at all. Shipped — see HISTORY.md
§ The install a stranger was afraid of.

**The kit, 2026-09-11 — a deliberate departure from the rule above.** Tyler
asked for the tester's run to be "really really easy", for a friend rather
than a stranger, so `scripts/mac_trial.sh --pack OUT.sh` writes one file with
the repo at HEAD inside it: no git, no GitHub access, since the repo is still
private. On the Mac it installs `uv ffmpeg espeak-ng auto-editor` with
Homebrew (22 formulae with dependencies, checked against formulae.brew.sh
that day), melt from the Shotcut app (Homebrew's `mlt` pulls 135, OpenCV and
VTK among them), whisper with `uv tool` unless one is already on PATH. Then
it runs DEMO.md's commands to the first failure and zips a report to the
Desktop. `--uninstall` removes what the run recorded adding and nothing the
tester already had. What it measures is therefore **this install path plus
proofcut on macOS**, not whether a stranger can follow `doctor`'s fixes. That
second question is still open, and this run answers the one PORTABILITY.md
step 4 needs first: does Shotcut's melt carry the modules proofcut's documents
use. The report's two frames settle that, never melt's exit code.

**The same script runs from a clone** (2026-09-13): `bash
proofcut/scripts/mac_trial.sh` with no payload uses the checkout it sits in, and
tells the tester to attach the zip to the issue form rather than send it to
Tyler. Because that zip is posted publicly, the project's `proofcut.json` and
`project.otio` are scrubbed of the home folder along with the log — both store
absolute paths. `--pack` still works, for a tester with no GitHub access.
HISTORY.md § The Mac test, asked of strangers.

**GitHub's macOS runner runs the same kit** (`.github/workflows/mac-demo.yml`,
2026-09-13), and `scripts/trial_check.py` judges the run, since the kit
exits 0 on a render that disagrees with the timeline. That is most of this
step's technical question — does the demo reach a checked render on macOS —
answered without waiting for a person. It does not answer the rest: a runner
is not a stranger's Mac, it already has Homebrew, and nobody reads the
instructions. So a green run does not meet this step's done-when on its own;
it means the stranger's run is a check of the install path rather than a
first contact with macOS. HISTORY.md § The Mac test in CI.

**Done when:** one Mac run reaches `verify` agreeing with the timeline, its
queue is closed or recorded, and README.md's "no person has run it on a
Mac yet" is replaced by what was measured.

**Closed unmet on 2026-09-20, four days inside its own time-box.** Fourteen
days public returned 3 stars, 47 unique viewers and **one referred view from
reddit.com across all three tester posts**; the only engaged reply was an M1
owner who refused the *installer*, not the project. The three posts came
down the same day and the daily routine was disabled (HISTORY.md § The
tester nobody sent, and what replaced the gate). What the gate existed to
prevent — a launch thread turning into a first-run bug tracker for Apple
silicon — is answered by CI instead: `mac-demo.yml`'s `demo (macos-latest)`
takes the kit to a checked render on an arm64 runner, and `setup-demo.yml`
now does the same for `proofcut setup`, which no longer refuses that Mac
(HISTORY.md § `proofcut setup` on Apple silicon). That is the Windows
precedent — CI is the evidence and the post says so — not a claim that a
person ran it. **README.md § Help wanted keeps asking**, because a passive
ask costs nothing; what stopped is spending posts on it.

**Met for Intel 2026-09-17** by a friend's Intel Mac (issue #6); the run's
queue and its numbers are in HISTORY.md § An Intel Mac route, without
Homebrew. **That same day this line called the whole step met and the
tester post unneeded, which was wrong**: the Intel run went through pinned
downloads, while an Apple silicon Mac installs through Homebrew, a route no
person has run and CI's runner (Homebrew preinstalled) cannot. HISTORY.md
§ The first person's Mac run said so that morning, and most of the Show HN
audience's Macs are Apple silicon. So the gate stayed open for Apple silicon
and the tester post went up that day rather than on its 2026-09-21 fallback,
setting the time-box at 2026-09-24 — HISTORY.md § The Apple silicon tester
post.

## Step 3 — the flip, and the ten minutes after it

**No public commit may carry the MIT grant.** A flip publishes every tag
and commit, not just the tip, and until 2026-09-11 all 17 tags carried MIT.
They were rewritten to carry Shield instead (HISTORY.md § The MIT history,
rewritten). The GitHub repo was recreated for it, since GitHub serves an
overwritten commit by its hash; any later rewrite needs the same.

Tyler's hand, in this order, the same afternoon:

**The README's clip lives in the draft `v0.22.0` release** (uploaded
2026-09-13, `user-attachments/assets/3c3517cd-…` in README.md twice). The
upload belongs to that draft's notes, so **item 6 publishes that draft and
never deletes or recreates it**, and the notes it publishes keep the video
line. While the repo is private the URL answers 404 to anyone logged out,
which is GitHub's documented rule for private uploads, not a broken link.
After item 1, open it logged out (a private window) and it has to play; if
it does not, the README's first screen is a dead link on launch day.

1. Flip `tydude001/proofcut` public.
2. Sync the GitHub mirror so the public repo is at the tip (wiki
   `git-server.md` § GitHub push mirrors — **never `git push --mirror`**).
   **Flip first, then sync** (2026-09-13): the account's included Actions
   minutes are spent, and a sync touching `src/` runs ci and both demo
   workflows — about 250 billed minutes of overage on a private repo and
   nothing on a public one. The repo sits public at the previous sync for
   the minutes between, so that commit has to pass the same scrub as the
   tip. **Push the `v0.22.0` tag to Gitea before this sync**, or item 6's
   release creates it on GitHub alone and the sync after that prunes it.
3. Enable private vulnerability reporting — SECURITY.md already points at it
   and is wrong until this is on.
4. Repo description, on GitHub and Gitea alike: `An AI video editor that
   proves its cuts. Your recordings to a finished, mastered film, cut by
   transcript, on your own machine, by you or an agent. Every render is
   checked against the edit.` — the README's tagline; pyproject.toml,
   server.json and the plugin manifests carry the shorter `Local-first AI
   video editor: recordings to a finished film, cut by transcript, then
   verified`. Topics: `mcp`, `mcp-server`, `video-editing`, `whisper`, `ffmpeg`,
   `local-first`, `claude-code`, `opentimelineio`. **The old name was not
   searchable** — "lucid" is a car, a diagramming suite and a thousand dream
   apps — so the description and the tagline are what get found, and they say
   the same seven words everywhere: *proofcut, the local-first AI video editor*.
   Measured 2026-09-13: Glama's search for the name returns 25 servers led by
   Lucidchart's official `lucidsoftware/lucid-mcp-server`, and its "video
   editor" search returns 24, FableCut among them — the row is what sells, in
   the query that matters. The rename question was settled in § What this
   plan deliberately does not do; the HN thread confusing the two was the one
   thing that would reopen it, and the registry reopened it first — the name
   is proofcut (docs/plans/RENAME.md).
5. Social preview image: `docs/img/edit-mode.png`, which is what every link
   unfurls to on X, Bluesky and Slack. It is already screened for footage and
   paths (CLAUDE.md § Conventions, the screenshot rule).
6. Tag a release: `v0.22.0`, at the tip that goes public. `v0.21.0`
   predates the licence change. **All six version literals are already at
   0.22.0** (2026-09-12, `uv sync` behind them, `tests/test_version.py`
   holding them together and green), so this item is the annotated tag and
   the GitHub release, not the bump. It read "two" until `b217b18`, when
   step 4's own manifests added four more — CLAUDE.md § Conventions names
   all six. A GitHub release with notes gives the
   directories in step 4 something to cite and the HN post a permalink that
   will not move. Release notes are the HISTORY.md section names since the
   last tag, one line each — not a changelog, which the repo does not keep
   on purpose. The draft is `~/proofcut-work/spikes/launch-release/NOTES.md`.
7. Pin docs/DEMO.md from the README's first screen, if it is not already the
   first link a newcomer sees.
8. **A way to buy the author a coffee** (Tyler's ask, 2026-09-11; deferred —
   nothing else in this plan waits on it). The whole build is a
   `.github/FUNDING.yml` naming the account (GitHub Sponsors, Ko-fi or Buy
   Me a Coffee — one, not three) plus one line at the foot of the README,
   which puts the **Sponsor** button on the repo page. It is not revenue and
   is not the open-core call (§ What this plan deliberately does not do):
   it is the cheapest honest answer to "how do I say thanks", and a launch
   thread asks that question within the hour. Wire it before Show HN, since
   the button is what the thread will find. **Wired 2026-09-15 as Ko-fi
   (`ko_fi: tydude001`)**, not Sponsors: GitHub Sponsors approval can take
   weeks, which would put Show HN up with no button, and Ko-fi has no
   queue. **The file alone draws no button**: GitHub parsed it (GraphQL
   `fundingLinks` answered `KO_FI`) and the page showed nothing until
   Settings → General → Features → **Sponsorships** was ticked.
   When Sponsors approves, the line becomes `github: tydude001` —
   a swap, keeping one platform. Never name `github:` before approval; the
   button would open a sponsor page that does not exist.
9. **Open and pin the Mac test issue**, and create the `mac-test` label the
   issue form applies, so reports can be found by it — the repo has only
   GitHub's defaults. Step 2's stranger arrives from here on, so this is the same
   afternoon, not later. The draft body is
   `~/proofcut-work/spikes/launch-release/MAC-ISSUE.md`.

**Done when:** the public URL unfurls with the image and the tagline, the
Security tab shows "Report a vulnerability", a release exists, and the Mac
test issue is pinned.

## Step 4 — the MCP directories, listed quietly

"An MCP server that edits video" is **not** a category of one. The
awesome-mcp-servers Multimedia section holds several on 2026-09-12, FableCut
(667★, a browser NLE an agent drives live) and kinocut among them. PRIOR-ART.md
had said so on 2026-08-25, before this sentence claimed the opposite. The MCP
ecosystem is still the channel whose audience wants the shape, but a listing
has to say what proofcut does that the line above it does not. What each
directory actually takes today — PulseMCP paused and reading the registry,
Smithery local-only as `.mcpb`, Glama scoring tool descriptions — and the
drafted awesome-list line: `~/proofcut-work/spikes/launch-listings/LISTINGS.md`.
List it a week *before* Show HN so the first strangers arrive in ones and
twos, with time to fix what they find.

- **The official MCP registry** (`registry.modelcontextprotocol.io`) — **live
  at 0.29.1** (2026-09-16), with a `pypi` package block, so a client installs
  it as `uvx proofcut mcp`; older versions stay listed as history.
  `server.json` is in the repo against the published `2025-12-11` schema,
  still what `mcp-publisher init` emits — re-check that URL at each publish,
  the mechanics have changed more than once. `websiteUrl` points at
  docs/DEMO.md. The registry verifies the `pypi` block by finding
  `mcp-name: io.github.tydude001/proofcut` in the README *PyPI holds*, so
  **upload to PyPI before publishing to the registry**. HISTORY.md § The
  registry entry names its PyPI package. `mcp-publisher
  validate` checks the file against the registry itself and publishes
  nothing, so it settles the entry before the handoff. A publish proves the
  `io.github.tydude001` namespace with a GitHub device login, and **the token
  it writes lives five minutes**, so the login and the publish go back to
  back. HISTORY.md § The registry listing.
- **The community directories** — PulseMCP, Glama, Smithery, and a pull
  request to the `awesome-mcp-servers` list under its media/video heading.
  Each takes the repo URL, the description and the release; none needs
  anything built. **Glama's ownership claim, `glama.json`, is already at the
  repo root** (2026-09-12, checked against its published schema) — but a
  claim is not a listing: Glama takes a submission, through the **Add
  Server** button on `glama.ai/mcp/servers` (GitHub OAuth, write access to
  the repo verified), and two days public listed nothing on its own.
  Submitted 2026-09-15 and **live the same day**; the badge read `rated A`
  on the listing's first hours and `rated B` once the tool-definition half of
  the score landed (HISTORY.md § The tool definitions were graded, and `path`
  was the gap). The
  awesome-list PR is open and waits on that maintainer
  (punkpeye/awesome-mcp-servers#14483, one line, mergeable) — checked against
  that list's own CONTRIBUTING: right section, alphabetical slot, markers
  matching its legend, and a badge that resolves. **He merges in batches and
  not in order**, 255 in the week to 2026-09-16 against 2,227 left open, most
  within a day of opening, so queue position says nothing and there is nothing
  to do but wait. Its `🤖🤖🤖` agent fast-track buys nothing measurable — 57%
  of that week's merges carried it against 50% of the open backlog — and would
  be untrue of a PR filed by hand.
  Shipped — see HISTORY.md § The launch clip's product defects, fixed,
  § Glama takes a submission, and the probe that missed it.
- **A Claude Code plugin.** The agent pane already spawns `claude` against a
  generated MCP config (`webui._agent_bin`), so the one-command install for a
  Claude Code user is a plugin manifest naming `proofcut mcp` as its server.
  This is proofcut *being* a plugin, which PLAN.md's non-goal ("a plugin system
  before there are two users") does not touch — that non-goal is about proofcut
  *having* plugins. **Built 2026-09-12**: `.claude-plugin/plugin.json` and a
  single-plugin `.claude-plugin/marketplace.json`, both read off
  code.claude.com's current references. The server command is
  `uv run --project ${CLAUDE_PLUGIN_ROOT} proofcut mcp` — a static manifest
  cannot name `sys.executable`, and a bare `proofcut` is the silent `tools: []`
  failure HISTORY.md § The agent panel had no tools at all measured — and it
  was driven over stdio with the repo's venv scrubbed from PATH: 90 tools.
  README.md § Try it carries the two install commands
  (`/plugin marketplace add tydude001/proofcut`, `/plugin install proofcut@proofcut`),
  which 404 until the flip and need nothing else afterwards. Shipped — see
  HISTORY.md § The registry entry and the plugin manifest.

**What to watch.** Each listing reports something — installs, stars,
"tried it" comments. Record which ones actually sent a stranger (an issue, a
doctor paste, a pull request) in HISTORY.md § The launch; that is the number
the next release's listing order is chosen by. **GitHub's clone traffic is
not one of them**: every CI job checks the repo out from a fresh runner
address, so the day after the flip read 294 clones from 83 "unique cloners"
against 5 page views, and the curve tracks the workflow-run count day by
day. Read views and referrers, and know that a referrer can be Tyler's own
share (the one Slack referrer on 2026-09-15 was).

**Done when:** the four listings are live and one week has passed with the
queue they produced closed.

## Step 5 — Show HN

One shot, so it goes after steps 1 and 4 — both done — on a weekday morning
US Eastern, with Tyler at a keyboard for the following six hours. **Step 2
gates nothing now**: it closed unmet, and its 2026-09-24 was seven days from
a tester post rather than a launch date, so carrying it forward would be a
deadline outliving its reason. The date is the wiki row `lucid-publish`.

**Title** (draft; HN strips "Show HN:" formatting quirks, keeps it under 80
characters, no exclamation):
> Show HN: proofcut – an AI video editor that transcribes its render to check it

**Retitled 2026-09-15**, off "…that's an MCP server": MCP is how it is
reached rather than what it does, and it narrows the title to the readers who
already run one. The check is what nothing else in this field does, and the
first comment then measures it; that comment's first paragraph still says MCP
server, which is where a reader who cares meets it.

**Retitled again 2026-09-16**, off "…a local-first AI video editor that
checks its own render", because that premise stopped being true. OpenChatCut
(1.9k★) now ships `verify_export`, which checks a render's duration,
resolution, fps, and black, frozen or silent spans against its timeline, so a
commenter could point at it. What nothing else found does is **transcribe the
render and diff its words against the cut**, and the title now names that
mechanism rather than the general claim. "Local-first" came out to fit 80
characters; the first comment's opening sentence carries it.
PRIOR-ART.md § The re-check before Show HN.

**The first comment is Tyler's, posted immediately, and it does three
things**: says what it is in two sentences, links the 47-second clip and the
uncut run, and pre-empts the two questions that will otherwise be the
thread. **Redrafted 2026-09-15**, because the draft it replaces led with the
demo cut — it predated the third trial, which is the strongest measured thing
here and the one a reader should be handed first (TRIAL.md § The third
trial — a whole film). Draft:

> proofcut takes your recordings to a finished film, and then proves the
> film matches the edit. Transcription, editing and rendering run on your own
> machine — whisper, ffmpeg, auto-editor, MLT and OpenTimelineIO under an MCP
> server, with no cloud service of its own; the only thing that talks to a
> model provider is the agent you choose. Any agent that speaks MCP (Claude
> Code, Codex, your own) can cut by transcript, hang b-roll off phrases, score
> it, caption, render and master it, and then check the render against the
> edit.
>
> The repo scores three unattended runs, each handed a goal and no steps.
> Here is the first, cutting and captioning a demo start to finish:
> [clip](https://github.com/user-attachments/assets/4153d180-3d7c-4c70-af5f-54d63d0a8bd5) / [the uncut runs](https://github.com/tydude001/proofcut/releases/tag/v0.23.0).
> The newest is the one I'd judge it on: briefed as a finished film ready to
> upload, it cut the fluffed takes, laid music 18 LU under the voice, ended on
> a card, mastered to −16.1 LUFS and checked its own render — 387 frames
> against the timeline's 387, every word heard back — in 192 seconds and
> $2.27. The music, the level and the card were each measured in the delivered
> file, not read off the project. That one ran on generated demo footage; the
> run before it used real footage and stopped at a captioned cut. Two things
> people will ask:
>
> *Licence.* PolyForm Shield — source-available; you can read, run, modify
> and redistribute it, and the one thing reserved is shipping a competing
> product. I chose it before publishing because I'd like this to earn a
> living and a shipped MIT version stays MIT forever. Not open source by the
> OSI definition, and I'd rather say so here than have it found.
>
> *Platforms.* Developed on Linux. On macOS and Windows the suite passes in
> CI and GitHub's runners take the demo to a checked render — on Apple
> silicon too, both from a clone and through `proofcut setup`; one Windows PC
> has as well, mine, and so has a friend's Intel Mac. No person has run an
> Apple silicon Mac, so that one is the runner's word and not a person's.
> This tells you
> what's missing and how to fix it, without cloning anything:
>
>     uvx proofcut doctor
>
> I'd genuinely like the doctor output from your machine if it says ✗.

**Say what the whole-film run ran on.** It was generated demo footage, and
the sentence above concedes it rather than leaving it to be found — the run
on real footage is a different, earlier one that stopped at a captioned cut
(TRIAL.md § The second trial over real footage). Conceding it is the
licence paragraph's own tactic, and the same reason nobody has listened to
that render is worth saying if asked: the level and the fade are numbers
here, not a judgement (TRIAL.md § What this run does not settle).

**The licence paragraph is the load-bearing one.** "Source-available" draws
scrutiny on HN, and a defensive reply loses the thread; the paragraph above
concedes the OSI point before anyone makes it and gives the reason as a
person rather than a company. Do not argue the definition in replies — link
the PolyForm text and move on. HISTORY.md § The licence, chosen has the full
reasoning if anyone wants it, and it is public.

**What else will come up, with the answer ready:**
- *"Why not just use Descript / Opus Clip / CapCut?"* — README.md § Why
  proofcut's second bullet: those are desktop apps around a metered cloud;
  this is the same primitives, local, with an agent surface. Don't name a competitor the
  README does not.
- *"How is this different from kinocut / FableCut?"* — the question the
  Multimedia section guarantees. Answer with the mechanism, not a
  superlative: proofcut re-transcribes the render and diffs it word for word
  against the timeline, and counts its frames against the timeline's.
  kinocut's gate scores signal levels (brightness, saturation, loudness) and
  its receipts are hashes. FableCut is a browser NLE an agent edits live, with
  no transcript addressing, and its export runs in the open browser tab — so
  its agent cannot render or check a cut on its own. Credit both, and concede
  FableCut's hand editing and its one-command install, which are real.
  LISTINGS.md § kinocut's gate, read; PRIOR-ART.md § FableCut, read.
- *"Isn't this just a wrapper around ffmpeg?"* — yes, and around six other
  things, and the value is that an agent can drive them and *verify the
  result*: `verify`, `check_frames`, `film_check`. Point at the clip's last
  ten seconds.
- *"Whisper hallucinates."* — it does, both passes, and `asr.clean` catches
  two classes of it; HISTORY.md § The ingest path's hallucination guard.
  Answers that cite a measurement land; answers that reassure do not.
- *"What does a run cost?"* — the whole-film run was $2.27 and 192 seconds on
  Opus 5, over 43 turns and 42 tool calls. proofcut itself holds no API key and
  talks to no provider; the bill is whatever agent you point at it, and a
  cheaper model is a cheaper run. Give the number, not a range — it is
  measured (TRIAL.md § The third trial — a whole film).
- *"Does it need a GPU?"* — no for the core (whisper on CPU is slow but
  works); yes for `describe` and `reframe-detect`, both optional and both
  reported as "unavailable" rather than failing.

**The Release page has to name the version people will install.** A tag
ships to PyPI and the registry on its own; the Release is only the page the
repo's sidebar calls Latest. `v0.38.0 — setup on Apple silicon` holds it and
matches what PyPI serves (HISTORY.md § The 0.38.0 Release, and the date that
outlived its reason). So this morning needs a Release only if a later tag has
moved Latest off it — write that one against the current version, from a file
and never through email, and diff the published body against it (HISTORY.md
§ The release nobody had cut). The comment's link to `v0.23.0` stays: the uncut runs are attached
there.

**Done when:** posted, the first comment up within a minute, and every
question in the thread answered within the day. Whatever the thread finds
goes into a queue the same way the trial's did.

## Step 6 — the follow-on channels, one per day

Not all at once: each channel's feedback should land separately, so it can
be told apart. Order by fit, each with the same clip and a one-line hook
tuned to the room.
The five posts are drafted in
`~/proofcut-work/spikes/launch-listings/POSTS.md` (2026-09-16), each with
what it may not claim.

1. **r/LocalLLaMA** — the hook is local whisper, a local VLM, a local face
   model, no cloud. This is their thesis, not a video-editing pitch.
2. **The OpenTimelineIO community** — the hook is an agent editing an OTIO
   timeline by transcript, and the render checked against that timeline.
   Added 2026-09-15, on the one piece of evidence any channel has so far:
   the first star from someone other than Tyler came before any channel
   in this plan ran. It came from a film-studio pipeline developer, and
   the repos they starred just before and after proofcut were all OTIO
   tooling (adapters, diff tools, JS bindings). That is one person, which
   is why this is second and not first. The audience writes and maintains
   OTIO tooling, so it will open `project.otio` and take the interop claims
   literally.
   - **Where:** one post in the *Show and tell* category of
     `AcademySoftwareFoundation/OpenTimelineIO`'s GitHub Discussions. Also
     add one line to the OTIO wiki's *Tools and Projects Using
     OpenTimelineIO* page, under *Other Applications/Plugins/etc*,
     alphabetical, which asks readers to add projects. That line is a
     listing, like step 4's, not a post. The wiki may not take edits from
     outside the project; if it refuses, ask in the Discussions post
     instead. The OTIO mailing list and the ASWF Slack channel (both linked
     from OTIO's README) are the same people, so posting there too is the
     second post this step's rule avoids.
   - **Say only what is measured.** `project.otio` is the timeline proofcut
     edits, not an export. The Kdenlive round-trip is measured
     (HISTORY.md § The import that was one frame short, sixty-three times).
     Resolve and Premiere are **not**: Premiere is not installed here, and
     the free Resolve cannot decode H.264/AAC (HISTORY.md § First
     milestones — all seven ran). So do not repeat
     README.md § No lock-in's "finish in Resolve, Premiere or Kdenlive" in
     this room. The detail this audience will find interesting is that
     OTIO's edit algorithms have no Python bindings, so every cut is done
     by hand-rolled track surgery (CLAUDE.md § Things that will bite you).
   - Wiki line (draft): `- [proofcut](https://github.com/tydude001/proofcut)
     - Local-first AI video editor: an MCP server whose agent cuts an OTIO
     timeline by transcript and checks the render against it.`
3. **r/ClaudeAI** and the Claude Code Discord — the hook is the agent pane
   and the plugin from step 4. Tag it as an MCP server first, an editor
   second.
4. **X and Bluesky** — the clip, the seven-word tagline, and the MCP and
   Claude Code tags. Anthropic's developer-relations people repost MCP
   servers that do something visibly new, and an agent cutting video with a
   self-check is that; do not ask them to, just make it easy to find.
5. **r/selfhosted** — the hook is "no accounts, no metering", which is the
   README's stated non-goal and their whole reason for being there.
6. **The films themselves.** Every goodsometimes release from here carries
   "cut in proofcut" and the repo link in its description, and the launch clip's
   making-of is a short of its own. This is the slow channel and the honest
   one: the tool's best advertisement is the work made with it.

**r/VideoEditing is deliberately last, or never.** Editors are the eventual
audience and the wrong first one: they will judge it against Premiere on
finishing, which PLAN.md's non-goals already concede. Developers who make
screencasts adopt it first; editors come when one of them posts a film.

## Step 7 — say so

When the two-week mark passes: HISTORY.md § The launch records what was
posted where, what each channel returned (strangers by the three measures in
§ How to work this plan), and what the queue held. The wiki row
`lucid-publish` closes, or becomes whatever remains. This file gains its
"Shipped" pointers. And the question open-core was waiting on — "after the
demo run exists" — is now answerable with numbers rather than a guess, so
the decision moves to wiki `decisions.md`, not here.

## What this plan deliberately does not do

- **No paid tier, no hosted anything, no commercial licence before the
  launch has produced strangers.** Revenue is a standing goal
  (HISTORY.md § The licence, chosen) and none of those is a launch step; a
  paid feature shipped to zero users is a feature nobody bought.
- **No relicensing for adoption.** Shield was chosen with the trade-off
  known. If the HN thread argues for MIT, the answer is the paragraph in
  step 5, not a change.
- **No rename.** Decided on two grounds, both true: the
  discoverability problem was solved by the tagline appearing
  everywhere the name does, and a new name would orphan every doc citation
  in the repo. It was wrong on a third fact it never weighed: Lucid Software's
  own MCP server is named `Lucid` in the official registry, first result for
  the word, and the company holds a live US trademark on the bare word
  LUCID. So the project is proofcut — docs/plans/RENAME.md.
- **No container or install script before step 2 says where the stranger
  stopped.** Measure the blocker, then beat it. *Except Linux, decided
  2026-09-16:* two clean-container runs had already measured where a
  stranger stops there, so `proofcut setup` was built for Linux alone.
  docs/plans/INSTALL.md § The gate this plan works under. No container is
  built. **Windows and Intel Mac shipped on their reports (2026-09-17), and
  Apple silicon on 2026-09-20 with no report at all** — this step closed
  unmet, and the one reply it drew said the install *was* the blocker, which
  is a measurement of the same kind the rule asks for.
- **No Product Hunt, no paid promotion, no launch-day mass posting.** Wrong
  audience, wrong signal, and a launch that lands everywhere at once cannot
  say which channel worked.
- **No Windows run as a gate.** CI is the Windows evidence until somebody
  with a Windows box turns up, and the Show HN comment says exactly that.
