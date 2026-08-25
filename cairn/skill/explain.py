"""Three questions, three sources, and nothing started to answer any of them.

Explain is a capability rather than the place a request falls to when nothing else fits.
That distinction is what this module is for: each question has a source it is answered
**from**, and none of the three is Cairn's memory of what a word probably means.

| Question                    | Source                                                     |
| --------------------------- | ----------------------------------------------------------- |
| what would this workflow do | the generated definition, re-read from disk                |
| what does this word mean    | the frozen vocabularies, phrased by `report/phrases.py`     |
| why was this step excluded  | the run record's own `cause`                                |

The second is why this is a command and not a page of prose. `report/phrases.py` is by its
own docstring the only module that turns a frozen word into a human sentence, so that three
renderings cannot describe one run three ways; a skill paraphrasing the same words out of a
document would be a fourth rendering, and the one nobody diffs.

Nothing here imports anything that runs, locks, commits or writes, and a test asserts it —
"Explain answers all three of its questions without starting anything" is then a property of
what this module was handed rather than of the care taken writing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.core import CairnError
from cairn.record.model import RunRecord
from cairn.record.vocabulary import (
    ATTENTION_ORDER,
    NEXT_ACTIONS,
    OVERLAYS,
    STEP_OUTCOMES,
    VERDICT_EXIT_CODES,
    VERDICT_PRECEDENCE,
)
from cairn.report.phrases import (
    HEADLINE_BY_VERDICT,
    LABEL_BY_ATTENTION,
    LABEL_BY_OUTCOME,
    SENTENCE_BY_ACTION,
    SENTENCE_BY_CAUSE,
)
from cairn.topology import Naming, TopologyError, parse_node_name
from cairn.verify import EXCLUSION_CAUSES
from cairn.workflow.schema import (
    CAIRN_INVOCATION,
    LABEL_PLAN,
    OCCASION_PARAM,
    PARENT_BRANCH_PARAM,
    REPOSITORY_PARAM,
    declared_parameter,
    is_agent_body,
    read,
    split_argv,
)
from cairn.workflow.stamp import Divergence, describe

WORD_FAMILY_VERDICT = "run verdict"
WORD_FAMILY_OUTCOME = "step outcome"
WORD_FAMILY_OVERLAY = "overlay"
WORD_FAMILY_ATTENTION = "attention"
WORD_FAMILY_CAUSE = "exclusion cause"
WORD_FAMILY_NEXT = "next action"

# Every frozen word Explain can answer for, and the map that phrases it. A word Cairn's
# record can hold and this table cannot reach is a word the skill would have to invent a
# meaning for, so a test requires the two to agree.
WORD_FAMILIES: tuple[tuple[str, tuple[str, ...], dict[str, str]], ...] = (
    (WORD_FAMILY_VERDICT, VERDICT_PRECEDENCE, HEADLINE_BY_VERDICT),
    (WORD_FAMILY_OUTCOME, STEP_OUTCOMES, LABEL_BY_OUTCOME),
    (WORD_FAMILY_ATTENTION, ATTENTION_ORDER, LABEL_BY_ATTENTION),
    (WORD_FAMILY_CAUSE, EXCLUSION_CAUSES, SENTENCE_BY_CAUSE),
    (WORD_FAMILY_NEXT, NEXT_ACTIONS, SENTENCE_BY_ACTION),
)

# `OVERLAYS` has no phrasing map of its own: an overlay is rendered as the attention item it
# raises, so its meaning is that item's. Named here rather than omitted, because a
# vocabulary Explain silently could not reach would look like one it did.
SENTENCE_FOR_OVERLAY = (
    "an overlay on a step's outcome rather than an outcome of its own; the report raises it "
    "as the matching attention item"
)


class Meaning(NamedTuple):
    word: str
    families: tuple[str, ...]
    sentences: tuple[str, ...]
    exit_code: int | None


def meaning(word: str) -> Meaning:
    """What one of Cairn's own words means, quoted from where it is frozen.

    Plural families, because `not_reached` is deliberately a step outcome and an exclusion
    cause at once and a reader asking about it is owed both.
    """
    families: list[str] = []
    sentences: list[str] = []
    for family, vocabulary, phrasing in WORD_FAMILIES:
        if word in vocabulary:
            families.append(family)
            sentences.append(phrasing[word])
    if word in OVERLAYS:
        families.append(WORD_FAMILY_OVERLAY)
        sentences.append(SENTENCE_FOR_OVERLAY)
    if not families:
        raise CairnError(
            "invalid_arguments",
            f"{word!r} is not one of Cairn's own words. Explaining it would mean inventing "
            f"a meaning for it; the words that have one are {', '.join(explainable())}",
        )
    return Meaning(
        word=word,
        families=tuple(families),
        sentences=tuple(sentences),
        exit_code=VERDICT_EXIT_CODES.get(word),
    )


def explainable() -> tuple[str, ...]:
    words: list[str] = []
    for _, vocabulary, _ in WORD_FAMILIES:
        words += [word for word in vocabulary if word not in words]
    words += [word for word in OVERLAYS if word not in words]
    return tuple(words)


class StepAccount(NamedTuple):
    node: str
    role: str | None
    step_id: str | None
    subcommand: tuple[str, ...]
    working_directory: str | None
    depends: tuple[str, ...]
    timeout_seconds: int | None
    assertion: str | None


class WouldDo(NamedTuple):
    plan: str
    repository: str | None
    parent_branch: str | None
    occasion: str | None
    schedule: str | None
    agent_steps: int
    steps: tuple[StepAccount, ...]
    provenance: Divergence


def _named(node: str) -> Naming:
    """The role and step a node name carries, or neither.

    A node the topology's grammar does not cover is itself evidence of a hand edit, and an
    account that says so is worth more than a traceback — so it is described like any other
    node, without a role.
    """
    try:
        return parse_node_name(node)
    except TopologyError:
        return Naming(role="", subject="")


def _steps(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        return []
    found = cast(dict[str, Any], document).get("steps")
    if not isinstance(found, list):
        return []
    return [step for step in cast(list[Any], found) if isinstance(step, dict)]


def _label(document: Any, name: str) -> str | None:
    if not isinstance(document, dict):
        return None
    labels = cast(dict[str, Any], document).get("labels")
    if not isinstance(labels, dict):
        return None
    value = cast(dict[str, Any], labels).get(name)
    return value if isinstance(value, str) else None


def would_do(workflow: Path, plan: str | None = None) -> WouldDo:
    """What a run of this definition would do, read off the definition and nothing else.

    An account rather than the file. A definition is tens of kilobytes and re-emitting one
    through a conversation is a copy nobody can reproduce faithfully ([docs/workflow.md]);
    what a reader wants is which steps there are, what each one runs, and whether the file
    is still the one Cairn wrote.

    Provenance is not optional here. Describing a hand-edited definition as though Cairn had
    written it is the one lie this capability could tell, and it is the lie a person asking
    "what would this do" is least equipped to catch.
    """
    try:
        document = read(workflow)
    except (OSError, ValueError) as unreadable:
        # The hand-edited definition is this capability's subject, not an exception to it:
        # a file that no longer parses is described by the one thing still readable about
        # it, which is its provenance.
        raise CairnError(
            "invalid_arguments",
            f"{workflow} is not the JSON document Cairn writes, so what it would do cannot "
            f"be read off it: {unreadable}. "
            f"{describe(workflow, plan or workflow.stem).summary}",
        ) from unreadable
    named = plan if plan is not None else (_label(document, LABEL_PLAN) or workflow.stem)
    accounts: list[StepAccount] = []
    agents = 0
    for step in _steps(document):
        node = str(step.get("name", ""))
        body = step.get("run")
        argv = split_argv(body) if isinstance(body, str) else ()
        cairn_argv = argv[len(CAIRN_INVOCATION) :] if argv[: len(CAIRN_INVOCATION)] == CAIRN_INVOCATION else ()
        if isinstance(body, str) and is_agent_body(body):
            agents += 1
        naming = _named(node)
        depends = step.get("depends")
        timeout = step.get("timeout_sec")
        working = step.get("working_dir")
        accounts.append(
            StepAccount(
                node=node,
                role=naming.role or None,
                step_id=naming.subject or None,
                subcommand=cairn_argv,
                working_directory=working if isinstance(working, str) else None,
                depends=tuple(str(name) for name in cast(list[Any], depends))
                if isinstance(depends, list)
                else (),
                timeout_seconds=timeout if isinstance(timeout, int) else None,
                assertion=body if naming.role == "verify" and isinstance(body, str) else None,
            )
        )
    schedule = cast(dict[str, Any], document).get("schedule") if isinstance(document, dict) else None
    return WouldDo(
        plan=named,
        repository=declared_parameter(document, REPOSITORY_PARAM) or None,
        parent_branch=declared_parameter(document, PARENT_BRANCH_PARAM) or None,
        occasion=declared_parameter(document, OCCASION_PARAM) or None,
        schedule=schedule if isinstance(schedule, str) else None,
        agent_steps=agents,
        steps=tuple(accounts),
        provenance=describe(workflow, named),
    )


class Exclusion(NamedTuple):
    step_id: str
    outcome: str
    cause: str | None
    meaning: str
    overlays: tuple[str, ...]
    divergence: str | None
    branch: str | None
    consequence: str


# What an exclusion means for the next run, owned here because no other surface says it and
# a person's real question after "why" is "and now what". It is one sentence and it is the
# same sentence for every cause, because the marker protocol does not vary by cause.
CONSEQUENCE = (
    "an excluded step's branch never reaches the trunk and no marker for it was written, so "
    "the next run of this plan re-attempts the step. There is no exclusion list to maintain "
    "and nothing to clear by hand"
)

NOT_EXCLUDED = (
    "this step was not excluded, so there is no cause to give. Its outcome is what the "
    "record holds and nothing was dropped"
)


def why_excluded(record: RunRecord, step_id: str) -> Exclusion:
    """Why one step contributed no verified work, from the cause the record carries."""
    for step in record["steps"]:
        if step["step_id"] != step_id:
            continue
        cause = step["cause"]
        divergence = step["divergence"]
        return Exclusion(
            step_id=step_id,
            outcome=step["outcome"],
            cause=cause,
            meaning=SENTENCE_BY_CAUSE[cause] if cause else NOT_EXCLUDED,
            overlays=tuple(step["overlays"]),
            # Both accounts, neither named the winner. A divergence is recorded and never
            # resolved ([docs/verify-gate.md]), so the sentence weighs them rather than
            # settling them.
            divergence=(
                f"the step reported {divergence['reported']!r} while its assertion "
                f"{'passed' if divergence['asserted'] else 'did not pass'}"
            )
            if divergence is not None
            else None,
            branch=step["branch"],
            consequence=CONSEQUENCE if cause else NOT_EXCLUDED,
        )
    known = ", ".join(step["step_id"] for step in record["steps"]) or "none"
    raise CairnError(
        "invalid_arguments",
        f"run {record['run_id']} records no step {step_id!r}. It records: {known}",
    )


__all__ = [
    "CONSEQUENCE",
    "NOT_EXCLUDED",
    "SENTENCE_FOR_OVERLAY",
    "WORD_FAMILIES",
    "Exclusion",
    "Meaning",
    "StepAccount",
    "WouldDo",
    "explainable",
    "meaning",
    "why_excluded",
    "would_do",
]
