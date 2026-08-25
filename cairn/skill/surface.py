"""What the installed skill costs to have, measured rather than asserted.

Prospective users see a skill's context cost before they commit (D6), so the cost is a
published number and this is the one thing that computes it.

**A session that never names Cairn pays nothing.** `disable-model-invocation: true` keeps the
description out of every session's context, so nothing here is resident and the first thing
charged is charged to someone who asked. Three tiers, because "the installed context cost" is
not one number and publishing one would be the same sin as a plausible default:

| Tier              | Paid                                                            |
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

# Named so a reader can price the estimate themselves rather than trusting it. Four
# characters per token is the usual English approximation; the point of publishing it is
# that nobody mistakes the figure for a tokenizer's answer.
CHARACTERS_PER_TOKEN = 4

# A ratchet rather than an aspiration: a budget nothing enforces is a number that only ever
# goes up. Moving either of these is a decision to make out loud, and the reason belongs
# here beside the number.
#
# The description is what tells a person what `/cairn` is for, in the moment they have
# already asked for it, so it is held to a sentence and a half.
DESCRIPTION_CHARACTER_BUDGET = 600
# The trigger budget is a third of that again above the lean prose, because the dispatch
# table is 48 aligned cells and its padding is not slack — it is the one artifact a reader
# has to apply exactly, and a table nobody can scan is worse than a longer file.
#
# Moved to 13,500 from 13,000 for the ask list's own precision, which is worth about 140
# tokens a session. A paid sweep found three of its rules licensing the misread they exist to
# prevent: `many_verbs` said to ask *which comes first*, which a sentence stating its order
# has already answered; the same-cell exception read as covering verb classes; and the
# `recovering` class told a reader to take a named step as its run, which is the cell
# `dispatch.py` declares an ask. Nine sessions in that sweep started a run or a schedule
# nobody authorised. The words that close those gaps are the cost of the gaps being closed,
# and reclaiming the space by cutting other rules would have changed two things at once in a
# file whose only real test is a sweep that takes three hours.
#
# Moved to 13,650 from 13,500 for two sentences paid sweeps showed were missing, not implied:
# `no_subject` never lets a lone candidate in the world supply a missing subject — every
# measured miss on that shape answered over the only run there was — and a definition that
# exists for a named workflow makes a differing repository the encoded-or-re-author question,
# which the author-where-none-exists road was answering unasked in four of five draws. The
# 168 characters they cost were not reclaimable from the two rules they extend without
# cutting sentences other cases hold green, which is the same two-changes-at-once trade
# refused above.
ON_TRIGGER_CHARACTER_BUDGET = 13_650


class Cost(NamedTuple):
    characters: int
    lines: int

    @property
    def tokens(self) -> int:
        return -(-self.characters // CHARACTERS_PER_TOKEN)


class Surface(NamedTuple):
    described: Cost
    on_trigger: Cost
    on_capability: Cost
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


def _cost(text: str) -> Cost:
    return Cost(characters=len(text), lines=len(text.splitlines()))


def measure(root: Path) -> Surface:
    """The installed surface's cost, recomputed from the files as they are now.

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
        described=_cost(description(skill)),
        on_trigger=_cost(skill.read_text(encoding="utf-8")),
        on_capability=_cost(heaviest.read_text(encoding="utf-8")),
        heaviest_capability=heaviest.name,
    )


PUBLISHED_HEADING = "## What it costs to have installed"


def published(surface: Surface) -> str:
    """The block the README carries, composed here so there is one spelling of it.

    The table is emitted in the aligned style the repository's markdown formatter
    enforces, because this block is compared byte for byte against the README: padding
    the formatter would add back is padding that turns the oracle red over nothing.
    """
    rows = [
        ["Paid", "What", "Characters", "Lines", "Tokens (est.)"],
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
    "DESCRIPTION_CHARACTER_BUDGET",
    "ON_TRIGGER_CHARACTER_BUDGET",
    "PUBLISHED_HEADING",
    "SKILL_FILE",
    "Cost",
    "Surface",
    "description",
    "measure",
    "published",
]
