"""A run's follow-up work, written down as a plan document and nothing more.

The loop lives here, at the run layer, where every invariant already holds. A drafted plan is
an ordinary plan: it is parsed by the same derivation, refused by the same validator, asserted
through the same conversation, and started as its own run with its own record. Nothing it
produces is a step, and nothing in it runs until a person has confirmed the parse report
([capabilities/continuing.md]).

**It transcribes and does not interpret.** No code in Cairn reads English, so an item becomes
one heading and the item's own words beneath it, with the step that surfaced it named. The
task a step is given, the order the steps run in and the assertion each is proved by are the
author's, exactly as in a plan a person wrote — a derivation that restated the work here would
be a reading nobody declared, and an assertion written here would be the one category of step
that ships on an agent's say-so.

**The items are text Cairn did not write**, so this is a final sink and escapes like one
([cairn/report/sinks.py]): every line of an item is escaped for markdown and then carried as a
blockquote line, so no item can forge a heading, a table or a link in the document that
frames it.
"""

from __future__ import annotations

from pathlib import Path

from cairn.plan.home import plan_document_path, plans_directory
from cairn.plan.ids import derive_plan_slug
from cairn.record.harvest import FollowUp, Harvest
from cairn.report.sinks import for_markdown, one_line, raw

# What a follow-on plan's name says about where it came from. The run id rides in it before
# the slug's own bound is applied, so two rounds of one plan can never derive one slug — and
# so the worktree parent, the workflow file and the run record of a round are its own.
FOLLOW_ON = "after"


def draft_slug(repository: Path, harvested: Harvest) -> str:
    """The follow-on plan's own name, derived from the run it is the follow-on to.

    Derived by the same function every other plan's slug is, over the path this draft will be
    written under, so `plan slug` run over the written document answers with this slug again.
    The path is the one the document takes rather than a bare name, because that function
    resolves what it is given against the working directory — and a slug that varied with
    where the command was typed would name a different worktree parent each time ([ids.py]).
    """
    plan = harvested.plan or "plan"
    named = f"{plan}-{FOLLOW_ON}-{harvested.run_id}.md"
    return derive_plan_slug(str(plans_directory(repository) / named))


def draft_path(repository: Path, harvested: Harvest) -> Path:
    return plan_document_path(repository, draft_slug(repository, harvested))


def _quoted(text: str) -> str:
    escaped = for_markdown(raw(text))
    return "\n".join(f"> {line}".rstrip() for line in escaped.split("\n"))


def _provenance(found: FollowUp) -> str:
    named = for_markdown(one_line(found.step_slug or found.step_id))
    titled = f" ({for_markdown(one_line(found.step_title))})" if found.step_title else ""
    if found.said is None:
        return f"Surfaced by step {named}{titled}, which left no account of itself."
    return f"Surfaced by step {named}{titled}, which reported: {for_markdown(one_line(found.said))}"


def _opening(harvested: Harvest) -> str:
    named = f" of plan {for_markdown(one_line(harvested.plan))}" if harvested.plan else ""
    count = len(harvested.items)
    pieces = "one piece" if count == 1 else f"{count} pieces"
    return (
        f"Run {for_markdown(one_line(harvested.run_id))}{named} ended "
        f"{for_markdown(one_line(harvested.verdict))}, and its steps reported {pieces} of "
        "work they found and did not do. Each is one step below, transcribed from that "
        "run's record, escaped for markdown and otherwise unaltered."
    )


PREAMBLE = (
    "Nothing here has been restated, ordered or given an end state. Each step's task is the "
    "author's to make convergent and each step's assertion is the author's to write, exactly "
    "as in a plan a person wrote: an agent's account of what it found carries no authority "
    "over what runs."
)


def render(harvested: Harvest) -> str:
    """The whole plan document, for a harvest that holds at least one item."""
    sections = [
        f"# Follow-on from run {for_markdown(one_line(harvested.run_id))}",
        "",
        _opening(harvested),
        "",
        PREAMBLE,
    ]
    for index, found in enumerate(harvested.items, start=1):
        sections += [
            "",
            f"## Follow-up {index} — {for_markdown(one_line(found.step_slug or found.step_id))}",
            "",
            _quoted(found.item),
            "",
            _provenance(found),
        ]
    return "\n".join(sections) + "\n"


def write_draft(repository: Path, harvested: Harvest, out: Path | None = None) -> Path:
    """Write the drafted plan where a plan stated in a request is written, and say where.

    It replaces a draft of the same run that is already there rather than refusing: the
    document is derived from a settled run's record, so writing it twice writes the same
    words — and a person who edited one is told which of the two happened by the caller.
    """
    path = out if out is not None else draft_path(repository, harvested)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(harvested), encoding="utf-8")
    return path


__all__ = ["FOLLOW_ON", "PREAMBLE", "draft_path", "draft_slug", "render", "write_draft"]
