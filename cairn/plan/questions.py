"""The rest of the authoring conversation: every question that is not about an assertion.

A derivation raises a question wherever it read something it cannot settle alone — an edge
it could not justify, one it kept but doubts, a task that would duplicate on a resumed run, a
name the documents never define, a plan the documents never call live. Each is the author's
to answer, and an answer is two writes in one act: the reading adopted goes into the graph
fact the question concerns, and the answer itself stays on the question beside it, so the
graph says both what was decided and who decided it.

`missing_verify` is the exception, and it is answered in [assertions.py]: its fact is the
step's own `assertion`, which already is a typed answer, so the question is cleared rather
than annotated. A second record of one decision is a record that can disagree with it.
"""

from __future__ import annotations

import shlex

from cairn.plan.schema import (
    ANSWERED_ORIGIN,
    EDGE_KINDS,
    MISSING_VERIFY,
    NON_CONVERGENT_TASK,
    PROPOSING_KINDS,
    RESOLUTIONS_BY_KIND,
    UNRESOLVED_REFERENCE,
    Dep,
    Graph,
    Question,
    Resolution,
    Step,
)


class ResolutionError(Exception):
    """An answer that cannot be applied — no such question, or an outcome it does not admit."""


def _find(graph: Graph, kind: str, step: str | None, dep: str | None) -> Question:
    for question in graph["questions"]:
        if (question["kind"], question["step"], question["dep"]) == (kind, step, dep):
            return question
    where = (
        f" on {dep} -> {step}" if dep is not None else f" on {step!r}" if step else ""
    )
    raise ResolutionError(f"there is no {kind} question{where} in this graph")


def _step(graph: Graph, step_id: str | None) -> Step:
    for step in graph["steps"]:
        if step["id"] == step_id:
            return step
    raise ResolutionError(f"{step_id!r} is not a step in this graph")


def resolve(
    graph: Graph,
    kind: str,
    *,
    step: str | None,
    dep: str | None,
    outcome: str,
    reading: str | None,
    reason: str | None,
) -> Graph:
    """Record one answer to one question, and make the graph say what it decided.

    The reading an accept adopts is the question's own proposal, never an argument, so no
    invocation can accept something other than what was proposed.
    """
    if kind == MISSING_VERIFY:
        raise ResolutionError(
            "a missing_verify question is answered with --command or --decline, which "
            "records the step's assertion"
        )
    question = _find(graph, kind, step, dep)
    if question["resolution"] is not None:
        raise ResolutionError(
            f"this {kind} question was already answered "
            f"({question['resolution']['outcome']}); re-derive the graph to ask it again"
        )
    allowed = RESOLUTIONS_BY_KIND[kind]
    if outcome not in allowed:
        raise ResolutionError(
            f"a {kind} question cannot be {outcome}; it admits {', '.join(allowed)}"
        )
    reason = (reason or "").strip() or None
    if outcome == "accepted":
        if kind in PROPOSING_KINDS:
            if question["proposed"] is None:
                raise ResolutionError(
                    f"nothing was proposed for this {kind} question, so there is nothing to "
                    "accept — edit it, or decline it and say why"
                )
            reading = question["proposed"]
        else:
            reading = None
            if reason is None:
                raise ResolutionError(
                    f"accepting a {kind} question is the author's word, so --reason says why"
                )
    elif outcome == "edited":
        if reading is None or not reading.strip():
            raise ResolutionError("an edit records the author's own reading")
        if reading == question["proposed"]:
            raise ResolutionError(
                "the edited reading is the one that was proposed; accept it instead"
            )
    else:
        reading = None
        if reason is None:
            raise ResolutionError("a decline must say why: pass --reason")

    if kind in EDGE_KINDS and dep is not None:
        target = _step(graph, step)
        present = any(existing["id"] == dep for existing in target["deps"])
        if outcome == "accepted" and not present:
            target["deps"].append(Dep(id=dep, origin=ANSWERED_ORIGIN, evidence=reason))
        elif outcome == "declined":
            target["deps"] = [existing for existing in target["deps"] if existing["id"] != dep]
    elif kind in (NON_CONVERGENT_TASK, UNRESOLVED_REFERENCE) and reading is not None:
        _step(graph, step)["task"] = reading

    question["resolution"] = Resolution(outcome=outcome, reading=reading, reason=reason)
    return graph


def render(questions: list[Question], graph_path: str = "<graph>") -> str:
    """The worksheet for every open question that is not about an assertion.

    Each admissible answer is printed as the whole invocation that records it, for the reason
    the assertion worksheet prints them: a line an operator has to repair records nothing.
    """
    if not questions:
        return ""
    lines: list[str] = [f"{len(questions)} question(s) have no recorded answer.", ""]
    where = shlex.quote(graph_path)
    for question in questions:
        about = (
            f"`{question['dep']}` -> `{question['step']}`"
            if question["dep"] is not None
            else f"`{question['step']}`" if question["step"] else "the plan"
        )
        lines.append(f"## {question['kind']} — {about}")
        lines.append("")
        lines.append(question["question"])
        if question["evidence"]:
            lines.append("")
            lines.append(f"On the words: {question['evidence']}")
        if question["proposed"] is not None:
            lines.append("")
            lines.append(f"Proposed: {question['proposed']}")
        lines.append("")
        invocation = f"python3 -m cairn plan answer {where} --kind {question['kind']}"
        if question["step"] is not None:
            invocation += f" --step {question['step']}"
        if question["dep"] is not None:
            invocation += f" --dep {question['dep']}"
        for outcome in RESOLUTIONS_BY_KIND[question["kind"]]:
            if outcome == "accepted" and question["kind"] in PROPOSING_KINDS:
                if question["proposed"] is None:
                    continue
                lines.append("Accept the proposal with:")
                lines.append(f"    {invocation} --accept --out {where}")
            elif outcome == "accepted":
                lines.append("Accept it with:")
                lines.append(
                    f"    {invocation} --accept --reason '<why it holds>' --out {where}"
                )
            elif outcome == "edited":
                # An edit restates a step's task, so a question about the plan as a whole
                # has no edit to propose — printing one would hand over a line `resolve`
                # refuses.
                if question["step"] is None:
                    continue
                lines.append("Restate the step's task with:")
                lines.append(
                    f"    {invocation} --edit '<the task, restated>' --out {where}"
                )
            else:
                lines.append("Decline it with:")
                lines.append(
                    f"    {invocation} --decline --reason '<why>' --out {where}"
                )
        lines.append("")
    return "\n".join(lines)


__all__ = ["ResolutionError", "render", "resolve"]
