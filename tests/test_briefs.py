"""The trial briefs, held to the text the recorded runs were given.

`proofcut.briefs` composes both the trial briefs and the MCP prompts, so a
change to a shared goal line changes what the next trial measures. That is
sometimes the point — and then this file changes with it, beside the run that
measured the new text. It is never a formatting accident. The film brief below
is the one `~/proofcut-work/spikes/agent-trial-film/runs/20260915-140025`
was given, byte for byte; the demo brief is the text in force since the rename.
docs/plans/MCP.md § Step 7.
"""

from __future__ import annotations

from proofcut import briefs

DEMO = """\
You are editing a short video, and proofcut's tools are the only thing you have —
there is no shell, no file browser, and no way to read a file except through a
proofcut tool.

The raw material is three files in /m:

  vo.wav            a voiceover, one speaker, with a fluffed take in it: the
                    narrator starts a sentence, gives up, and says it again
  broll-blue.mp4    b-roll
  broll-rust.mp4    b-roll

The project is already initialised at /p, and every tool takes it as
`path`. The server is bound to that one project and will refuse any other.

Deliver a finished cut:

  * the fluffed take gone, and nothing else that the narrator meant to say;
  * b-roll on screen under the lines it belongs to, rather than over all of it
    or none of it;
  * captions burned into the picture;
  * rendered to /o.mp4;
  * and the render checked against the timeline, not merely produced.

When you are done, report in plain prose what you cut, what picture you hung
where, and what every check you ran said — including anything that disagreed
with what you expected.
"""

FILM = """\
You are making a short film, and proofcut's tools are the only thing you have —
there is no shell, no file browser, and no way to read a file except through a
proofcut tool.

The raw material is four files in /m:

  vo.wav            a voiceover, one speaker, with a fluffed take in it: the
                    narrator starts a sentence, gives up, and says it again
  broll-blue.mp4    b-roll
  broll-rust.mp4    b-roll
  music.wav         a piece of music for the film

The project is already initialised at /p, and every tool takes it as
`path`. The server is bound to that one project and will refuse any other.

Deliver a finished film, ready to upload:

  * the fluffed take gone, and nothing else that the narrator meant to say;
  * b-roll on screen under the lines it belongs to, rather than over all of it
    or none of it;
  * the music under the narration from its first word, low enough that every
    word is still clear;
  * an end card after the last line, reading "proofcut";
  * captions burned into the picture;
  * mastered to -16 LUFS integrated;
  * rendered to /o.mp4;
  * and the render checked against the timeline, not merely produced.

When you are done, report in plain prose what you cut, what picture you hung
where, what you did with the music, the end card and the level, and what every
check you ran said — including anything that disagreed with what you expected.
"""


def test_the_trial_briefs_are_the_text_the_runs_were_given() -> None:
    assert briefs.trial_cut("/m", "/p", "/o.mp4") == DEMO
    assert briefs.trial_film("/m", "/p", "/o.mp4") == FILM


def test_a_shipped_prompt_shares_the_trials_goal_lines() -> None:
    """Everything but the reader-specific lines is the trial's own text."""
    for line in (briefs.BROLL, briefs.CAPTIONS, briefs.CHECKED):
        assert line in briefs.cut("/m")
        assert line in briefs.film("/m")
    assert briefs.MUSIC in briefs.film("/m")
    assert briefs.FILM_REPORT in briefs.film("/m")
    assert briefs.CUT_REPORT in briefs.cut("/m")


def test_a_shipped_prompt_never_claims_the_agent_has_no_shell() -> None:
    """A person's agent in Claude Code has one; only the trial's is confined."""
    for text in (briefs.cut("/m"), briefs.film("/m"), briefs.review()):
        assert "no shell" not in text


def test_a_prompt_names_a_tool_nowhere() -> None:
    """Goals, never steps: a brief that names a tool measures instruction-following."""
    from proofcut import server

    tools = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    for text in (briefs.cut("/m", length="90s"), briefs.film("/m", end_card="x"), briefs.review("/r")):
        words = {word.strip(".,;:`()") for word in text.split()}
        assert not ({name for name in tools if "_" in name} & words)
