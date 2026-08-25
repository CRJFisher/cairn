"""The parse report: what Cairn understood, shown back before anything is generated."""

import re
from typing import Any, cast

from cairn.plan.assertions import tally
from cairn.plan.schema import Graph, Step, is_unverified, normalise
from cairn.plan.validate import Result


def waves(graph: Graph) -> list[list[str]] | None:
    """Topological levels, or None when the graph has no topology to level.

    An unresolvable or circular dependency leaves an ordering that does not exist, and
    printing a plausible-looking wave list for it would show the author concurrency the
    plan never had.
    """
    known = {step["id"] for step in graph["steps"]}
    remaining = {step["id"]: {dep["id"] for dep in step["deps"]} for step in graph["steps"]}
    if any(deps - known for deps in remaining.values()):
        return None
    levels: list[list[str]] = []
    placed: set[str] = set()
    while remaining:
        ready = sorted(sid for sid, deps in remaining.items() if deps <= placed)
        if not ready:
            return None
        levels.append(ready)
        placed |= set(ready)
        for sid in ready:
            del remaining[sid]
    return levels


_WHITESPACE = re.compile(r"\s+")


def _escape(text: str) -> str:
    """Flatten a field into one table cell. A stray line break ends the whole table."""
    return _WHITESPACE.sub(" ", text).replace("|", "\\|").strip()


def _code(text: str) -> str:
    """An inline code span whose fence is longer than any backtick run it contains."""
    flat = _escape(text)
    longest = max((len(run) for run in re.findall(r"`+", flat)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return f"{fence}{pad}{flat}{pad}{fence}"


def _unasserted_cell(step: Step) -> str:
    """A step nobody has been asked about, and one whose author declined, read differently.

    One spelling for both states would make `unverified` mean nothing: a considered
    declaration and a question never put would look identical in the one place a reader
    goes to find out.
    """
    return "**unverified**" if is_unverified(step) else "**never asked**"


def render(raw: Any, result: Result | None = None) -> str:
    graph = normalise(raw)
    plan = graph["plan"]
    steps = graph["steps"]
    levels = waves(graph)
    lines: list[str] = []

    lines.append(f"# Parse report — {plan['title']}")
    lines.append("")
    lines.append(f"Source: `{plan['source']}`  ·  plan slug: `{plan['slug']}`")
    if levels is None:
        lines.append(f"{len(steps)} step(s). The dependencies do not form a topology.")
    else:
        widest = max((len(level) for level in levels), default=0)
        lines.append(f"{len(steps)} step(s) in {len(levels)} wave(s); widest wave {widest}.")
    if len(plan["sources"]) > 1:
        lines.append(f"Derived from {len(plan['sources'])} documents.")
    lines.append("")

    lines.append("## Steps")
    lines.append("")
    lines.append("| id | plan calls it | kind | scope | verify | timeout | ceiling | model |")
    lines.append("| -- | ------------- | ---- | ----- | ------ | ------- | ------- | ----- |")
    for step in steps:
        verify = _code(step["verify"]) if step["verify"] else _unasserted_cell(step)
        budget = step["max_budget_usd"]
        ceiling = "—" if budget is None else f"US$ {budget:.2f}"
        model = "—" if step["model"] is None else _escape(step["model"])
        lines.append(
            f"| `{step['id']}` | {_escape(step['slug'])} | {step['kind']} | "
            f"{step['scope']} | {verify} | {step['timeout']}s | {ceiling} | {model} |"
        )
    lines.append("")

    lines.append("## What each step is asked to do")
    lines.append("")
    for step in steps:
        lines.append(f"- **`{step['id']}`** — {_escape(step['task'])}")
    lines.append("")

    lines.append("## Dependencies")
    lines.append("")
    any_dep = False
    for step in steps:
        if not step["deps"]:
            continue
        any_dep = True
        lines.append(f"- **`{step['id']}`** depends on:")
        for dep in step["deps"]:
            mark = "**derived**" if dep["origin"] == "derived" else "declared"
            evidence = _escape(dep.get("evidence") or "(none)")
            lines.append(f"  - `{dep['id']}` — {mark}, on the words: {evidence}")
    if not any_dep:
        lines.append("No step depends on another; every step is a root.")
    lines.append("")

    lines.append("## Waves")
    lines.append("")
    if levels is None:
        lines.append("None. The dependencies do not form a topology.")
    else:
        for index, level in enumerate(levels, start=1):
            named = ", ".join(f"`{sid}`" for sid in level)
            lines.append(f"{index}. {named}")
    lines.append("")

    lines.append("## Left out of the graph")
    lines.append("")
    if graph["omissions"]:
        for omission in graph["omissions"]:
            lines.append(
                f"- **{_escape(omission['title'])}** — {omission['reason']}: "
                f"{_escape(omission['evidence'])}"
            )
    else:
        lines.append("Nothing in the document was left out.")
    lines.append("")

    if plan["id_collisions"]:
        lines.append("## Renamed to fit the engine")
        lines.append("")
        for collision in plan["id_collisions"]:
            lines.append(
                f"- {_escape(collision['slug'])} sanitises to "
                f"{_code(collision['sanitised_to'])}, already taken by "
                f"{_escape(collision['clashed_with'])} — it runs as "
                f"{_code(collision['assigned'])}"
            )
        lines.append("")

    lines.append("## Assertions")
    lines.append("")
    counts = tally(graph)
    lines.append(
        " · ".join(
            f"{label}: {counts[cast(Any, key)]}"
            for key, label in (
                ("accepted", "proposal accepted"),
                ("edited", "proposal edited"),
                ("authored", "written unaided"),
                ("declined", "declined"),
                ("documented", "stated by the document"),
                ("unasserted", "never asked"),
            )
        )
        + f"  (of {len(steps)} step(s))"
    )
    lines.append("")
    declined = [step for step in steps if is_unverified(step)]
    if declined:
        lines.append("Unverified, with the proposal each declined:")
        lines.append("")
        for step in declined:
            assertion = step["assertion"]
            proposal = (
                _code(assertion["proposed"])
                if assertion is not None and assertion["proposed"]
                else "nothing could be proposed"
            )
            reason = _escape(assertion["reason"] or "") if assertion is not None else ""
            lines.append(f"- **`{step['id']}`** declined {proposal} — {reason}")
        lines.append("")

    lines.append("## Questions for the author")
    lines.append("")
    if graph["questions"]:
        for question in graph["questions"]:
            where = f" (`{question['step']}`)" if question["step"] else ""
            lines.append(f"- [{question['kind']}]{where} {_escape(question['question'])}")
            evidence = question.get("evidence")
            if evidence:
                lines.append(f"  - on the words: {_escape(evidence)}")
            proposed = question.get("proposed")
            if proposed:
                lines.append(f"  - proposed: {_code(proposed)}")
    else:
        lines.append("None.")
    lines.append("")

    if result is not None:
        lines.append("## Validator")
        lines.append("")
        lines.append("Verdict: **{}**".format("passes" if result.ok else "FAILS"))
        lines.append("")
        for label, findings in (("Errors", result.errors), ("Warnings", result.warnings)):
            if not findings:
                continue
            lines.append(f"### {label}")
            lines.append("")
            for finding in findings:
                lines.append(f"- `{finding.code}` {_escape(finding.message)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
