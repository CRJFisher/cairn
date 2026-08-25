"""The missing-verify conversation: show what the derivation offered, take the human's answer.

Real plans state their end states in English and leave the command unwritten, so a plan
arriving with no assertions anywhere is the expected case rather than an edge one. What
happens then is a designed conversation: for each step nobody has been asked about, quote
the step's own words back, show the assertion the derivation proposed for them — declared on
the `missing_verify` question, resting on the sentence it quotes — and record accept, edit,
or decline. Nothing here composes an offer: the agent that read the plan is the only thing
that proposes, and this module only carries its proposal to the person.

Nothing here writes a command into a graph. A proposal is an offer; only an answer is a
decision, and only `answer` writes.
"""

from __future__ import annotations

import shlex
from typing import Any, TypedDict, cast

from cairn.plan.schema import (
    ASSERTION_OUTCOMES,
    Assertion,
    Graph,
    Question,
    cannot_fail,
    is_unasserted,
)


class Proposal(TypedDict):
    """One offer, and everything the human needs beside it to answer."""

    step: str
    title: str
    task: str
    acceptance: str | None
    proposed: str | None


class Tally(TypedDict):
    """How each step's assertion was arrived at.

    `accepted` and `edited` count answers to a proposal Cairn actually made; `authored`
    counts a command written where it could offer none, which is the difference between
    a proposal carrying its weight and a human doing the work unaided.
    """

    accepted: int
    edited: int
    authored: int
    declined: int
    unasserted: int
    documented: int


class AnswerError(Exception):
    """An answer that cannot be applied — a step that is not there, or a shape that lies."""


def _refuse_unassertable(step_id: str, command: str) -> None:
    """Refuse an assertion that cannot fail, while the human who wrote it is still here."""
    if cannot_fail(command):
        raise AnswerError(
            f"step {step_id!r}: {command!r} cannot fail, so it asserts nothing. Assert the "
            "step's end state, or decline and say why it has none."
        )


def _question_of(graph: Graph, step_id: str) -> Question | None:
    """The `missing_verify` question the derivation raised for this step.

    The conversation's whole leverage is showing an author their own acceptance line and
    the proposal the derivation read out of it, so both are read from the question already
    on the graph rather than re-derived here.
    """
    for question in graph["questions"]:
        if question["kind"] == "missing_verify" and question["step"] == step_id:
            return question
    return None


def propose(graph: Graph) -> list[Proposal]:
    """Every step nobody has been asked about, with what the derivation offered for it."""
    proposals: list[Proposal] = []
    for step in graph["steps"]:
        if not is_unasserted(step):
            continue
        question = _question_of(graph, step["id"])
        proposals.append(
            Proposal(
                step=step["id"],
                title=step["title"],
                task=step["task"],
                acceptance=None if question is None else question["evidence"],
                proposed=None if question is None else question["proposed"],
            )
        )
    return proposals


def render(proposals: list[Proposal], graph_path: str = "<graph>") -> str:
    """The worksheet a human answers from, with each offer beside the words it came from.

    Every answer is printed as the whole invocation that records it. A worksheet whose
    instruction has to be corrected before it works records nothing, and the flags it
    would drop are the two that decide whether the answer reaches the graph at all and
    whether accepting a proposal is counted as accepting it.
    """
    if not proposals:
        return "Every step's end state is answered for.\n"
    lines: list[str] = [
        f"{len(proposals)} step(s) have no assertion and no recorded answer.",
        "",
    ]
    for proposal in proposals:
        lines.append(f"## `{proposal['step']}` — {proposal['title']}")
        lines.append("")
        lines.append(f"The step's own words: {proposal['task']}")
        if proposal["acceptance"]:
            lines.append("")
            lines.append(f"What the document says is done: {proposal['acceptance']}")
        lines.append("")
        offered = proposal["proposed"]
        if offered is None:
            lines.append(
                "The derivation offered nothing for this step. Write a command, or "
                "declare the step unverified and say why."
            )
        else:
            lines.append(f"Proposed: `{offered}`")
        lines.append("")
        # Every part of the invocation is quoted as a shell argument, the graph's own path
        # included: a line an operator has to repair before it runs records nothing. The
        # offer itself is not on the line — it lives on the graph's own question, and
        # `answer` records it from there, so no answer can drop or misquote it.
        where = shlex.quote(graph_path)
        invocation = f"python3 -m cairn plan answer {where} --step {proposal['step']}"
        lines.append("Accept or edit it with:")
        lines.append(
            f"    {invocation} --command {shlex.quote(offered or '<the command>')}"
            f" --out {where}"
        )
        lines.append("")
        lines.append("Or declare it unverified with:")
        lines.append(
            f"    {invocation} --decline --reason '<why no command can assert this>'"
            f" --out {where}"
        )
        lines.append("")
    return "\n".join(lines)


def answer(
    graph: Graph,
    step_id: str,
    *,
    command: str | None,
    reason: str | None,
) -> Graph:
    """Record one human's decision about one step, and clear the question it answers.

    The outcome is derived from the answer against the offer the graph itself carries, so
    an accept, an edit and a command written unaided cannot be miscounted by whoever ran
    the conversation — and no answer can drop or misquote what was offered.
    """
    for step in graph["steps"]:
        if step["id"] != step_id:
            continue
        if command is None and reason is None:
            raise AnswerError(
                f"step {step_id!r}: an answer is either a command or a decline with a reason"
            )
        if command is not None and reason is not None:
            raise AnswerError(
                f"step {step_id!r}: a step is either asserted or declined, never both"
            )
        question = _question_of(graph, step_id)
        proposed = None if question is None else question["proposed"]
        if command is None:
            outcome = "declined"
        elif proposed is None:
            outcome = "authored"
        else:
            outcome = "accepted" if command == proposed else "edited"
        if command is not None:
            _refuse_unassertable(step_id, command)
        step["verify"] = command
        step["assertion"] = Assertion(outcome=outcome, proposed=proposed, reason=reason)
        # The derivation raised this question to be answered, and it now is. Leaving it
        # standing would have the parse report ask the author for the very command they
        # just supplied, and keep the graph reporting unanswered questions for ever.
        graph["questions"] = [
            question
            for question in graph["questions"]
            if not (question["kind"] == "missing_verify" and question["step"] == step_id)
        ]
        return graph
    raise AnswerError(f"{step_id!r} is not a step in this graph")


def tally(graph: Graph) -> Tally:
    """How the corpus's assertions were arrived at — the conversation's own measurement."""
    counts = Tally(
        accepted=0, edited=0, authored=0, declined=0, unasserted=0, documented=0
    )
    for step in graph["steps"]:
        assertion = step["assertion"]
        if assertion is not None:
            outcome = assertion["outcome"]
            if outcome in ASSERTION_OUTCOMES:
                counts[cast(Any, outcome)] += 1
        elif is_unasserted(step):
            counts["unasserted"] += 1
        else:
            counts["documented"] += 1
    return counts


__all__ = [
    "AnswerError",
    "Proposal",
    "Tally",
    "answer",
    "propose",
    "render",
    "tally",
]
