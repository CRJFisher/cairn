"""How much context the installed skill occupies, measured rather than asserted.

Prospective users see a skill's footprint before they commit (D6), so it is a published
number and this is the one thing that computes it.

**A session that never names Cairn loads nothing.** `disable-model-invocation: true` keeps
the description out of every session's context, so nothing here is resident and the first
thing loaded is loaded for someone who asked. Three tiers, because "the installed context
footprint" is not one number and publishing one would be the same sin as a plausible
default:

| Tier              | Read                                                            |
| ----------------- | ---------------------------------------------------------------- |
| `description`     | when Cairn is named — the frontmatter's one field               |
| `on_trigger`      | when Cairn is named — the whole of `SKILL.md`                    |
| `on_capability`   | when one capability is selected — its own document, the largest |

Characters and lines are **measured**. Tokens are **estimated** at a stated divisor, because
this package imports nothing outside the standard library and therefore has no tokenizer;
labelling an estimate as a measurement is precisely what the rest of Cairn refuses to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

SKILL_FILE = "SKILL.md"
CAPABILITIES_DIRECTORY = "capabilities"

# Named so a reader can check the estimate themselves rather than trusting it. Four
# characters per token is the usual English approximation; the point of publishing it is
# that nobody mistakes the figure for a tokenizer's answer.
CHARACTERS_PER_TOKEN = 4

# A ratchet rather than an aspiration: a limit nothing enforces is a number that only ever
# goes up. Moving either of these is a decision to make out loud, and the reason belongs
# here beside the number.
#
# The description is what tells a person what `/cairn` is for, in the moment they have
# already asked for it, so it is held to a sentence and a half.
DESCRIPTION_CHARACTER_LIMIT = 600
# The trigger limit sits a little above the lean prose, because the dispatch table is 48
# aligned cells and its padding is not slack — it is the one artifact a reader has to apply
# exactly, and a table nobody can scan is worse than a longer file. It moves down when the
# file does, and up only for a sentence a sweep over real sessions showed was missing rather
# than implied.
#
# It moved up once, by the four paragraphs a measured session needed and did not have: a plan
# stated in the request, the model qualifier, the stop condition a near miss must not be read
# as meeting, and the reading the first reply states. That session read the file, found no row
# of the table that held its request, and went looking through Cairn's own source for the word
# "model" ([30]).
#
# It moved up a second time, for the repository resolution order and the four doubts that are
# still worth a question ([31]). The rule it replaced was one sentence long and cost a person
# a turn of the conversation every time they ran a plan from inside the repository it belongs
# to — a shorter file that asks a question nobody needed to answer is not a cheaper file.
ON_TRIGGER_CHARACTER_LIMIT = 13_300


class Size(NamedTuple):
    characters: int
    lines: int

    @property
    def tokens(self) -> int:
        return -(-self.characters // CHARACTERS_PER_TOKEN)


class Surface(NamedTuple):
    described: Size
    on_trigger: Size
    on_capability: Size
    heaviest_capability: str


def description(skill: Path) -> str:
    """The one field every session pays for, read out of the frontmatter block.

    A line reader rather than a YAML parser. There is no YAML parser in the standard
    library, and a hand-written one would have to be checked against itself
    ([docs/workflow.md]); one key read off the lines between the fences is exact and has
    nothing to get wrong. A block scalar is refused rather than half-read.
    """
    lines = skill.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{skill} carries no frontmatter block")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() == "description":
            described = value.strip()
            if described in ("|", ">", "|-", ">-"):
                raise ValueError(
                    f"{skill} writes its description as a block scalar, which this reader "
                    "does not follow — and the figure it publishes would be one character"
                )
            return described
    raise ValueError(f"{skill} declares no description")


def _size(text: str) -> Size:
    return Size(characters=len(text), lines=len(text.splitlines()))


def measure(root: Path) -> Surface:
    """The installed surface's size, recomputed from the files as they are now.

    Never a recorded constant. The figure the README publishes is compared against this on
    every test run, so editing `SKILL.md` and forgetting the README turns the suite red —
    which is the only thing that keeps a published number true.
    """
    skill = root / SKILL_FILE
    documents = sorted((root / CAPABILITIES_DIRECTORY).glob("*.md"))
    if not documents:
        raise ValueError(f"{root / CAPABILITIES_DIRECTORY} holds no capability document")
    heaviest = max(documents, key=lambda path: len(path.read_text(encoding="utf-8")))
    return Surface(
        described=_size(description(skill)),
        on_trigger=_size(skill.read_text(encoding="utf-8")),
        on_capability=_size(heaviest.read_text(encoding="utf-8")),
        heaviest_capability=heaviest.name,
    )


PUBLISHED_HEADING = "## What is read when it is installed"


def published(surface: Surface) -> str:
    """The block the README carries, composed here so there is one spelling of it.

    The table is emitted in the aligned style the repository's markdown formatter
    enforces, because this block is compared byte for byte against the README: padding
    the formatter would add back is padding that turns the oracle red over nothing.
    """
    rows = [
        ["Read", "What", "Characters", "Lines", "Tokens (est.)"],
        [
            "when Cairn is named",
            "the skill's description",
            f"`{surface.described.characters}`",
            f"`{surface.described.lines}`",
            f"`{surface.described.tokens}`",
        ],
        [
            "when Cairn is named",
            f"`{SKILL_FILE}`",
            f"`{surface.on_trigger.characters}`",
            f"`{surface.on_trigger.lines}`",
            f"`{surface.on_trigger.tokens}`",
        ],
        [
            "when a capability is selected",
            f"`{CAPABILITIES_DIRECTORY}/{surface.heaviest_capability}`, the largest",
            f"`{surface.on_capability.characters}`",
            f"`{surface.on_capability.lines}`",
            f"`{surface.on_capability.tokens}`",
        ],
    ]
    return "\n".join(
        (
            PUBLISHED_HEADING,
            "",
            (
                "Measured by `python3 -m scripts.measure_surface`. Tokens are an estimate "
                f"at {CHARACTERS_PER_TOKEN} characters each, not a tokenizer's count."
            ),
            "",
            *_aligned(rows, right_aligned=(2, 3, 4)),
        )
    )


def _aligned(rows: list[list[str]], *, right_aligned: tuple[int, ...]) -> list[str]:
    """A header row and body rows as one aligned markdown table."""
    widths = [
        max(3, *(len(row[column]) for row in rows)) for column in range(len(rows[0]))
    ]

    def line(cells: list[str]) -> str:
        padded = (
            cell.rjust(width) if column in right_aligned else cell.ljust(width)
            for column, (cell, width) in enumerate(zip(cells, widths))
        )
        return "| " + " | ".join(padded) + " |"

    separator = (
        "-" * (width - 1) + ":" if column in right_aligned else "-" * width
        for column, width in enumerate(widths)
    )
    return [
        line(rows[0]),
        "| " + " | ".join(separator) + " |",
        *(line(row) for row in rows[1:]),
    ]


__all__ = [
    "CAPABILITIES_DIRECTORY",
    "CHARACTERS_PER_TOKEN",
    "DESCRIPTION_CHARACTER_LIMIT",
    "ON_TRIGGER_CHARACTER_LIMIT",
    "PUBLISHED_HEADING",
    "SKILL_FILE",
    "Size",
    "Surface",
    "description",
    "measure",
    "published",
]
