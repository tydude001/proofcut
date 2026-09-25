# A content-keyed store for what the models say about a source — written 2026-09-24, designed, not built

An efficiency review of the repo (HISTORY.md § A render on an unchanged edit
is not re-rendered records the second finding; this note is the first) ranked
one waste above every other, by the minutes it costs: **whisper, the vision
model and the face detector are cached only by `clip_id`, inside one
project's own `cache/` and manifest.** `transcribe` writes
`cache/transcripts/<clip_id>.json` and `describe` skips a clip whose
`clip_id` is already in the manifest's `descriptions`. Nothing keys on the
media itself. So a second project on the same shoot, a re-cut, or every
`agent_trial.py --fresh` run (which deletes the project and deliberately
keeps the media, `prepare`'s docstring) transcribes and describes the same
bytes again.

What that costs, from the measurements already in the repo:

| what runs again | measured | where |
|---|---|---|
| `transcribe` on the demo VO, CPU whisper | 26 s | LOCAL.md § The rerun with `hear` on the map |
| two `verify` passes and a `finish_check` | 20 s, 20 s, 16 s | same |
| `describe`, the film's 59 windows | 15 s load, then ~3 s a window, about 3 min | describe.py's docstring; HISTORY.md § Footage descriptions |
| VRAM while either runs | 6.5 GiB whisper, 11.5 GiB the VLM | LOCAL.md, on a 12 GiB card |

The last row is why this is not only wall clock: while one of these holds the
card nothing else on the box may use it (memory `one-gpu-job-at-a-time`, made
after a backgrounded synth failed a running take's verify), so every repeated
minute is a minute the box is single-threaded.

The pattern to copy already exists three times. Thumbnails and waveforms
are keyed by the resolved media's `(size, mtime_ns)` (ops.py beside
`THUMBS_LOG`); a design pack by its content hash (`pk.pack_hash`); a synth
candidate by `sha256(voice, ref_text, text, max_seconds)` (`vo_synth`).
None of them reach across projects, because each lives under one project's
`cache/`.

## What the store is

**A directory of source-indexed facts, addressed by content, outside any
project.** A footage description indexes the source, so no edit can
invalidate one (CLAUDE.md, TRAPS.md § Footage descriptions and pinned cues);
the same holds for a transcript of the source and for the faces in a frame of
it. Those are facts about bytes, and bytes have a hash. Everything the edit
can change stays out.

- **Where.** A sibling of `deps.root()`: `$XDG_DATA_HOME/proofcut/store`
  (`%LOCALAPPDATA%\proofcut\store` on Windows), resolved by one function
  beside `deps.root` so both folders move together. Not `~/proofcut-work`,
  which is this box's scratch and not the product's (TRAPS.md § Scratch
  directories), and not the project, which is the whole point.
- **The digest.** A full sha256 of the media file, computed once per
  project per file and memoised in `cache/digests.json` under
  `(size, mtime_ns)`, the key thumbnails already trust. Full, not sampled:
  a sampled hash cannot tell two encodes of the same footage apart, and a
  store that answers the wrong transcript for a re-encode is worse than no
  store. Measured 2026-09-24 on this box: a 251 MB render hashed at
  1.6 GB/s from a cold read, so a gigabyte costs well under a second, once.
- **The key is the digest plus everything the model was given.** For the
  single whisper pass: `model`, `language`, and the whisper binary's own
  `(size, mtime_ns)`, so a rebuilt whisper misses. For the windowed pass:
  the same plus `window` and `overlap`. For the VLM: `MODEL`, a sha256 of
  the prompt, `frame_size`, `max_new_tokens`, and the window's frame
  timestamps. For faces: `MODEL` and the timestamp. `device` is not in any
  key: the same weights on CPU and GPU are the same answer, to the tolerance
  the checks already accept.
- **What is stored is the raw model output, before proofcut's own rules.**
  Whisper's JSON as the binary wrote it, not the parsed `Transcript`; the
  VLM's text per window; the detector's boxes. `clean_payload`, the
  hallucination guard, `parse_whisper` and `_near_duplicates` still run on
  every call, so a change to any of those rules takes effect without a
  store flush, and `hallucinated_words` is still reported from what whisper
  actually said.
- **A hit is a fact the reply states.** `transcribe`, `verify`, `describe`
  and `reframe_detect` gain an additive `"store": "hit" | "miss"`, so a
  trial log shows why a call returned in 40 ms and an agent is not left
  wondering whether anything ran.
- **`force` bypasses the store as well as the project.** `describe(force=True)`
  is a person asking for the model to be run, and a cached identical answer
  is not that. The stored entry is replaced by what the run returns.
- **Writes are atomic and never contended.** Temp file then `os.replace`,
  in a directory named by the key, so two projects describing the same
  footage at once (two agents, two boxes on one NAS home) cannot interleave.
  The project lock does not apply: the store is outside every project.
- **No eviction, one report.** A transcript is about 100 KB and a clip's
  descriptions a few KB, so a year of shoots is tens of megabytes. `doctor`
  reports the store's size and entry count beside the deps folder, and
  `proofcut setup --uninstall` removes it with the rest of what setup made
  (TRAPS.md § `proofcut setup`, "everything added is recorded"). A
  `--clear` flag on setup is the only deletion.

## What changes in the callers, in order

Each step is one op, one hit test and one miss test, using the suite's
existing stub binaries (`tests/stubs.py`'s `write_stub`, which is how the
suite already fakes whisper), counting invocations of the stub.

1. **`store.py`.** `root()`, `digest(path, project)` with the memo, and
   `get(kind, key) / put(kind, key, payload)`. No caller yet. A test hashes a
   file, touches its mtime, and reads a second hash from disk rather than the
   memo.
2. **`asr.transcribe`, the single pass.** Wrapped at the `subprocess.run`,
   so every caller inherits it: `ops.transcribe`, `verify`'s default pass,
   `finish_check`, `speech_overlap` when it reaches for whisper. A render
   is a fresh file each time, so `verify` misses on a new render and **hits
   on a reused one**: the web pipeline now serves the last run's file when
   the edit has not moved (HISTORY.md § A render on an unchanged edit is not
   re-rendered) and re-runs the checks on it, and with this step the whisper
   half of those checks costs nothing on the second click. The demo's Render
   went from 10.9 s to 8.7 s on reuse; with this it would go to under a
   second.
3. **`asr.transcribe_windowed`.** Same wrap, keyed with `window` and
   `overlap`, on the whole media rather than per slice: the slices are
   proofcut's and the reconcile is proofcut's, and both must keep running.
4. **`describe.describe_windows`.** Per window, since a clip described at
   one `window` length and then another shares no entries. The worker still
   runs once for every window that misses, in one process, so a project with
   one new clip loads the weights once for that clip and not at all for the
   rest.
5. **`faces.detect`.** Per timestamp. Smallest payoff and the same shape.
6. **`agent_trial.py` reads the store's hits** out of the tool replies into
   the run's record, so a trial that ran against a warm store says so beside
   its timings, and a TRIAL.md row is never a warm run mistaken for a cold
   one.

## The control, before any of it is believed

The claim is "a second project on the same footage pays no model time and
gets the same words". So, once step 2 lands:

- **Positive.** Two `agent_trial.py --fresh` runs on the demo, back to back.
  The second's `transcribe` reports `store: hit` and returns in under a
  second, and its `cache/transcripts/vo.json` is byte-identical to the
  first's. If the words differ the store returned the wrong entry and the
  key is missing something.
- **Negative.** Re-encode the demo VO at the same settings (`ffmpeg -i vo.wav
  vo2.wav`) and import that: `store: miss`. A store that hits here has a
  sampled or truncated digest.
- **The version key.** Replace the whisper stub with a second stub of a
  different size: miss. Then the memory rule applies, `a-checks-window-is-a-
  claim-too`: the negative controls are what make a "hit" mean something.

## What this deliberately does not do

- **Does not put a digest in the manifest.** An additive optional key would
  not bump the schema (TRAPS.md § Schema migration), but a digest is derived
  and a manifest holds the edit; `cache/digests.json` is where a derived
  per-project memo lives, on `THUMBS_LOG`'s precedent.
- **Does not move `transcript_path`.** The project keeps its own copy of the
  parsed transcript, and every reader of it is untouched; the store is what
  fills it faster.
- **Does not share across machines or users.** A NAS home shared by two boxes
  gets it for free; nothing is designed for it.
- **Does not cache anything the edit can change.** Not `timeline_view`, not
  a render's checks, not `caption_view`. Those have their own stamps.

## Decision

Build it, steps 1 to 3 first: they are one module and one wrap, they pay on
every trial run and on every reused render, and the controls above settle
whether the key is complete before the VLM and faces steps copy it.
