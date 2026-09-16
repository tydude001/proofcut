"""The briefs: what an agent is asked to make, as text shared by the trials and the prompts.

`scripts/agent_trial.py` measures an agent against a brief, and the MCP server
ships the same goals as prompts (`cut`, `film`, `review`), so a person gets
the brief that was measured rather than a paraphrase of it
(docs/plans/MCP.md § Step 7). Both compose their text from the pieces below;
the goal lines are one copy.

**Goals, never steps** — the trial's rule (TRIAL.md): a brief names what the
finished thing has to be and leaves the route to the agent. A brief that
names a tool measures whether the agent can follow instructions.

The two compositions differ only where the reader differs. The trial's agent
has proofcut's tools and nothing else and is told so; a person's agent in
Claude Code has a shell too, and a prompt that said otherwise would be false.
The trial's material is the demo's generated files, listed by name; a prompt
names a folder. `tests/test_briefs.py` pins the trial rendering byte for byte,
because a brief that drifts makes the next run incomparable with the last.
"""

from __future__ import annotations

CONFINED = (
    "You are {doing}, and proofcut's tools are the only thing you have —\n"
    "there is no shell, no file browser, and no way to read a file except through a\n"
    "proofcut tool.\n\n"
)

SHIPPED = (
    "You are {doing} with proofcut. Do the editing through proofcut's tools — they are\n"
    "what keep every cut addressed to the words and every step checkable and undoable.\n\n"
)

DEMO_MATERIAL = (
    "The raw material is {count} files in {media}:\n\n"
    "  vo.wav            a voiceover, one speaker, with a fluffed take in it: the\n"
    "                    narrator starts a sentence, gives up, and says it again\n"
    "  broll-blue.mp4    b-roll\n"
    "  broll-rust.mp4    b-roll\n"
)
DEMO_MUSIC = "  music.wav         a piece of music for the film\n"

FOLDER_MATERIAL = "The raw material is the footage and audio in {media}.\n\n"

TRIAL_PROJECT = (
    "The project is already initialised at {project}, and every tool takes it as\n"
    "`path`. The server is bound to that one project and will refuse any other.\n\n"
)
NAMED_PROJECT = "Work in the proofcut project at {project}, making it there if there is none.\n\n"
BOUND_PROJECT = (
    "Work in the proofcut project this server is bound to; if it is bound to none,\n"
    "make a new project beside the material.\n\n"
)

#: The demo's own retake line: its voiceover has exactly one fluffed take.
DEMO_RETAKE = "the fluffed take gone, and nothing else that the narrator meant to say"
RETAKE = "every fluffed or abandoned take gone, and nothing else the speaker meant to say"

BROLL = (
    "b-roll on screen under the lines it belongs to, rather than over all of it\n"
    "    or none of it"
)
MUSIC = (
    "the music under the narration from its first word, low enough that every\n"
    "    word is still clear"
)
CAPTIONS = "captions burned into the picture"
CHECKED = "and the render checked against the timeline, not merely produced"

CUT_REPORT = (
    "When you are done, report in plain prose what you cut, what picture you hung\n"
    "where, and what every check you ran said — including anything that disagreed\n"
    "with what you expected.\n"
)
FILM_REPORT = (
    "When you are done, report in plain prose what you cut, what picture you hung\n"
    "where, what you did with the music, the end card and the level, and what every\n"
    "check you ran said — including anything that disagreed with what you expected.\n"
)

#: The film brief's delivery level, in LUFS integrated.
FILM_LOUDNESS = -16.0


def _bullets(lines: list[str]) -> str:
    return "".join(f"  * {line};\n" for line in lines[:-1]) + f"  * {lines[-1]}.\n\n"


def _loudness(lufs: float) -> str:
    return f"{lufs:g}"


def _cut_lines(retake: str, output: str, length: str | None) -> list[str]:
    lines = [retake, BROLL]
    if length:
        lines.append(f"about {length} long")
    return [*lines, CAPTIONS, f"rendered to {output}", CHECKED]


def _film_lines(
    retake: str, output: str, end_card: str | None, loudness: float, length: str | None
) -> list[str]:
    card = f'an end card after the last line, reading "{end_card}"' if end_card else (
        "an end card after the last line"
    )
    lines = [retake, BROLL, MUSIC, card]
    if length:
        lines.append(f"about {length} long before the end card")
    return [
        *lines,
        CAPTIONS,
        f"mastered to {_loudness(loudness)} LUFS integrated",
        f"rendered to {output}",
        CHECKED,
    ]


# ---------------------------------------------------------------- the trials


def trial_cut(media: object, project: object, output: object) -> str:
    """`agent_trial.py`'s demo brief."""
    return (
        CONFINED.format(doing="editing a short video")
        + DEMO_MATERIAL.format(count="three", media=media)
        + "\n"
        + TRIAL_PROJECT.format(project=project)
        + "Deliver a finished cut:\n\n"
        + _bullets(_cut_lines(DEMO_RETAKE, str(output), None))
        + CUT_REPORT
    )


def trial_film(media: object, project: object, output: object) -> str:
    """`agent_trial.py --film`'s brief."""
    return (
        CONFINED.format(doing="making a short film")
        + DEMO_MATERIAL.format(count="four", media=media)
        + DEMO_MUSIC
        + "\n"
        + TRIAL_PROJECT.format(project=project)
        + "Deliver a finished film, ready to upload:\n\n"
        + _bullets(_film_lines(DEMO_RETAKE, str(output), "proofcut", FILM_LOUDNESS, None))
        + FILM_REPORT
    )


# ---------------------------------------------------------------- the prompts


def _project(project: str | None) -> str:
    return NAMED_PROJECT.format(project=project) if project else BOUND_PROJECT


def _output(output: str | None) -> str:
    return output or "the project's renders/ folder"


def cut(media: str, output: str | None = None, length: str | None = None, project: str | None = None) -> str:
    """A cut to length with b-roll and captions — the demo trial's goals."""
    return (
        SHIPPED.format(doing="editing a video")
        + FOLDER_MATERIAL.format(media=media)
        + _project(project)
        + "Deliver a finished cut:\n\n"
        + _bullets(_cut_lines(RETAKE, _output(output), length))
        + CUT_REPORT
    )


def film(
    media: str,
    output: str | None = None,
    length: str | None = None,
    end_card: str | None = None,
    loudness: str | None = None,
    project: str | None = None,
) -> str:
    """A whole film — score, end card, master — the film trial's goals."""
    level = float(loudness) if loudness else FILM_LOUDNESS
    return (
        SHIPPED.format(doing="making a short film")
        + FOLDER_MATERIAL.format(media=media)
        + _project(project)
        + "Deliver a finished film, ready to upload:\n\n"
        + _bullets(_film_lines(RETAKE, _output(output), end_card, level, length))
        + FILM_REPORT
    )


def review(render: str | None = None, project: str | None = None) -> str:
    """A check of a finished project, changing nothing."""
    target = f"the render at {render}" if render else "the project's latest render"
    where = f"the proofcut project at {project}" if project else "the project this proofcut server is bound to"
    return (
        "You are checking a finished film before it is uploaded.\n\n"
        f"The film is {target}, made from {where}.\n"
        "Look with proofcut's tools and change nothing — no cut, no cue, no style, no\n"
        "new render.\n\n"
        "Say whether it is ready to upload:\n\n"
        + _bullets(
            [
                "whether the render is the timeline — the same length, frames and words",
                "whether every word meant to be heard is heard, and nothing cut is",
                (
                    "whether the picture, captions, music, cards and level are what the\n"
                    "    project says they are, and legible where they need to be"
                ),
                "whether anything the project's own checks flag is still open",
            ]
        )
        + "Report in plain prose, finding first: what is wrong and where, then what\n"
        "you checked and what each check said — including anything you could not check.\n"
    )
