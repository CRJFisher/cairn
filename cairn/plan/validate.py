"""The deterministic validator. Interpretation proposes; this script rules."""

import hashlib
import os
import re
from pathlib import PurePosixPath
from typing import Any

from cairn.plan.ids import is_engine_id, is_plan_slug
from cairn.plan.schema import (
    AGENT_FAMILY,
    AGENT_MODEL,
    ANSWERED_ORIGIN,
    EDGE_KINDS,
    ENGINE_NAME_MAX_BYTES,
    GRAPH_VERSION,
    MISSING_VERIFY,
    NON_CONVERGENT_TASK,
    RESERVED_ID_PREFIXES,
    STEP_ID_PATTERN,
    UNRESOLVED_REFERENCE,
    VERIFY_TIMEOUT,
    Graph,
    Question,
    SchemaError,
    Step,
    cannot_fail,
    has_assertion,
    is_unasserted,
    is_unverified,
    normalise,
)
from cairn.protocol import NOT_A_STEP, leading_command

_WHITESPACE = re.compile(r"\s+")


class Finding:
    def __init__(self, code: str, message: str, step: str | None = None) -> None:
        self.code = code
        self.message = message
        self.step = step

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "step": self.step}

    def __repr__(self) -> str:
        where = f" [{self.step}]" if self.step else ""
        return f"{self.code}{where}: {self.message}"


class Result:
    def __init__(
        self, graph: Graph | None, errors: list[Finding], warnings: list[Finding]
    ) -> None:
        self.graph = graph
        self.errors = errors
        self.warnings = warnings

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [f.as_dict() for f in self.errors],
            "warnings": [f.as_dict() for f in self.warnings],
        }


def _find_cycle(edges: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle as a list of ids, or None. Deterministic in id order.

    Iterative, because a plan long enough to exhaust the interpreter's stack would
    otherwise get a crash on one machine and a verdict on another.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(edges, WHITE)

    for root in sorted(edges):
        if colour[root] != WHITE:
            continue
        path: list[str] = []
        frontier: list[tuple[str, list[str]]] = [(root, sorted(edges[root]))]
        colour[root] = GREY
        path.append(root)
        while frontier:
            node, pending = frontier[-1]
            if not pending:
                frontier.pop()
                path.pop()
                colour[node] = BLACK
                continue
            successor = pending.pop(0)
            state = colour.get(successor, BLACK)
            if state == GREY:
                return path[path.index(successor) :] + [successor]
            if state == WHITE:
                colour[successor] = GREY
                path.append(successor)
                frontier.append((successor, sorted(edges[successor])))
    return None


def _redundant_parents(
    parents: list[str], dependencies: dict[str, list[str]]
) -> dict[str, list[str]]:
    """For each parent, the sibling parents that already reach it transitively.

    Walked per step rather than materialised for the whole graph: a step with one parent
    can have no redundant edge, and in a plan of any length almost every step has one.
    """
    implied: dict[str, list[str]] = {}
    if len(parents) < 2:
        return implied
    wanted = set(parents)
    for other in parents:
        reached: set[str] = set()
        frontier = list(dependencies.get(other, []))
        while frontier:
            node = frontier.pop()
            if node in reached:
                continue
            reached.add(node)
            frontier.extend(dependencies.get(node, []))
        for parent in wanted & reached:
            implied.setdefault(parent, []).append(other)
    return implied


def _flatten(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _source_path_fault(path: str) -> str | None:
    """Why a pinned path cannot name a document beneath the plan's root, or None.

    Judged on the text alone, so a graph that could only ever be checked against a file
    outside its own plan is refused whether or not a root is supplied.
    """
    if not path.strip():
        return "is empty"
    if os.path.isabs(path) or PurePosixPath(path).is_absolute():
        return "is absolute"
    if ".." in PurePosixPath(path).parts:
        return "climbs out of the plan's directory"
    return None


def _read_sources(graph: Graph, root: str, errors: list[Finding]) -> str:
    """Return every source document as one flattened corpus, checking each pin.

    Each document is read once, and its digest and the corpus every quotation is checked
    against come from those same bytes, so no check describes a file the others did not.
    """
    canonical = os.path.realpath(root)
    corpus: list[str] = []
    for source in graph["plan"]["sources"]:
        if _source_path_fault(source["path"]) is not None:
            continue
        path = os.path.realpath(os.path.join(canonical, source["path"]))
        if os.path.commonpath((canonical, path)) != canonical:
            errors.append(
                Finding(
                    "source_escape",
                    f"the graph pins {source['path']!r}, which resolves to {path} — outside "
                    f"the plan's root {canonical}",
                )
            )
            continue
        if not os.path.isfile(path):
            errors.append(
                Finding("missing_source", f"the graph pins {source['path']!r}, which is not there")
            )
            continue
        with open(path, "rb") as handle:
            raw = handle.read()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != source["sha256"]:
            errors.append(
                Finding(
                    "stale_source",
                    f"{source['path']!r} has changed since the graph was derived from it",
                )
            )
        corpus.append(_flatten(raw.decode("utf-8", errors="replace")))
    return "\n".join(corpus)


def _question_key(question: Question) -> tuple[str, str | None, str | None]:
    """What one question is about. Both edge kinds ask about the edge itself, so they share
    a key: an edge dropped as unjustified and kept as ambiguous is two readings of one fact,
    and two answers to it could each say the opposite."""
    kind = "edge" if question["kind"] in EDGE_KINDS else question["kind"]
    return (kind, question["step"], question["dep"])


def _check_answers(
    graph: Graph, by_id: dict[str, Step], errors: list[Finding]
) -> None:
    """Every recorded answer is the graph's fact, and every answered fact has its answer.

    A resolution that says one thing while the step or edge it settled says another is two
    records of one decision, and whichever a reader reached first would win.
    """
    accepted_edges: dict[tuple[str, str], str] = {}
    for question in graph["questions"]:
        resolution = question["resolution"]
        step_id = question["step"]
        step = by_id.get(step_id) if step_id is not None else None
        dep_id = question["dep"]
        if dep_id is not None and dep_id not in by_id:
            errors.append(
                Finding(
                    "unknown_question_step",
                    f"a {question['kind']} question names {dep_id!r} as the edge's other "
                    "end, which is not a step in this graph",
                    step_id,
                )
            )
        if resolution is None or step is None and step_id is not None:
            continue
        outcome = resolution["outcome"]
        if question["kind"] in EDGE_KINDS and step is not None and dep_id is not None:
            present = any(dep["id"] == dep_id for dep in step["deps"])
            if outcome == "accepted":
                accepted_edges[(step_id or "", dep_id)] = resolution["reason"] or ""
            if present != (outcome == "accepted"):
                errors.append(
                    Finding(
                        "unapplied_answer",
                        f"the {question['kind']} question on {dep_id} -> {step_id} was "
                        f"{outcome}, but the edge is {'in' if present else 'not in'} the graph",
                        step_id,
                    )
                )
        if (
            question["kind"] in (NON_CONVERGENT_TASK, UNRESOLVED_REFERENCE)
            and step is not None
            and outcome in ("accepted", "edited")
            and step["task"] != resolution["reading"]
        ):
            errors.append(
                Finding(
                    "unapplied_answer",
                    f"the {question['kind']} question on {step_id!r} was {outcome} with a "
                    "restated task, and the step does not carry it",
                    step_id,
                )
            )
    for step in graph["steps"]:
        for dep in step["deps"]:
            if dep["origin"] != ANSWERED_ORIGIN:
                continue
            reason = accepted_edges.get((step["id"], dep["id"]))
            if reason is None or (dep["evidence"] or "") != reason:
                errors.append(
                    Finding(
                        "unanswered_edge",
                        f"the edge {dep['id']} -> {step['id']} is recorded as the author's "
                        "answer, and no accepted question on that edge gives it those words",
                        step["id"],
                    )
                )


def validate(raw: Any, source_root: str | None = None) -> Result:
    errors: list[Finding] = []
    warnings: list[Finding] = []
    try:
        graph = normalise(raw)
    except SchemaError as exc:
        return Result(None, [Finding("schema", line) for line in str(exc).splitlines()], [])

    if graph["cairn_graph_version"] != GRAPH_VERSION:
        errors.append(
            Finding(
                "graph_version",
                f"graph version {graph['cairn_graph_version']} is not the supported "
                f"version {GRAPH_VERSION}",
            )
        )

    plan = graph["plan"]
    if not is_plan_slug(plan["slug"]):
        # Two halves of one rule, phrased apart because they are cleared differently: a
        # grammar fault is a slug someone wrote by hand, and a length fault is a slug that
        # was derived before the bound existed. The length half is refused here rather than
        # at the gate so a graph that cannot be authored is refused where a person can still
        # act on it ([19 A]).
        over = len(plan["slug"].encode("utf-8")) > ENGINE_NAME_MAX_BYTES
        errors.append(
            Finding(
                "plan_slug",
                (
                    f"plan slug {plan['slug']!r} is "
                    f"{len(plan['slug'].encode('utf-8'))} bytes, over the engine's "
                    f"{ENGINE_NAME_MAX_BYTES}-byte bound on a DAG name — and the slug is "
                    "the workflow file's name, which is the name the engine reads. "
                    "Re-derive it with `python3 -m cairn plan slug <path>`"
                )
                if over
                else f"plan slug {plan['slug']!r} must match ^[a-z0-9][a-z0-9-]*$",
            )
        )

    pinned = [source["path"] for source in plan["sources"]]
    if not pinned:
        errors.append(
            Finding("no_sources", "the graph pins no source document, so it cannot be rechecked")
        )
    elif plan["source"] not in pinned:
        errors.append(
            Finding(
                "source_not_pinned",
                f"the index document {plan['source']!r} is not among the pinned sources",
            )
        )
    if len(set(pinned)) != len(pinned):
        errors.append(Finding("duplicate_source", "the same document is pinned more than once"))
    for path in pinned:
        fault = _source_path_fault(path)
        if fault is not None:
            errors.append(
                Finding(
                    "source_path",
                    f"the pinned path {path!r} {fault}; a source is named relative to the "
                    "plan's own root",
                )
            )

    corpus = _read_sources(graph, source_root, errors) if source_root else None

    model_evidence = (plan["model_evidence"] or "").strip()
    if not plan["default_model"].strip():
        errors.append(
            Finding(
                "plan_model",
                "the plan names no default model, so nothing says which model its sessions "
                "and its merge resolver open on",
            )
        )
    elif plan["default_model"] != AGENT_MODEL and not model_evidence:
        # The one bound that carries its own quotation: a model is a translation of the
        # person's words into a value the provider is invoked with, and a translation
        # nothing quotes is the derivation's choice presented as theirs ([30 B]).
        errors.append(
            Finding(
                "unquoted_model",
                f"the plan pins every session to {plan['default_model']!r} and quotes no "
                "sentence for it; a model the plan states rests on the words that state it",
            )
        )
    if model_evidence and corpus is not None and _flatten(model_evidence) not in corpus:
        errors.append(
            Finding(
                "evidence_not_in_source",
                f"the plan's model quotes {_flatten(model_evidence)!r}, which no source "
                "document contains",
            )
        )

    steps = graph["steps"]
    if not steps:
        errors.append(Finding("empty_graph", "the graph contains no steps"))
        return Result(graph, errors, warnings)

    by_id: dict[str, Step] = {}
    for step in steps:
        step_id = step["id"]
        if not is_engine_id(step_id):
            errors.append(
                Finding(
                    "step_id",
                    f"id {step_id!r} must match ^{STEP_ID_PATTERN}$ — "
                    f"the engine rejects anything else",
                    step_id,
                )
            )
        if step_id in by_id:
            errors.append(
                Finding("duplicate_id", f"id {step_id!r} is used by more than one step", step_id)
            )
        if step_id.startswith(RESERVED_ID_PREFIXES):
            errors.append(
                Finding(
                    "reserved_id",
                    f"id {step_id!r} takes a name Cairn derives for another node — a "
                    f"step's assertion, its record, or a wave's merge slot — so the two "
                    f"would be one node in the workflow",
                    step_id,
                )
            )
        by_id[step_id] = step

    seen_slugs: dict[str, str] = {}
    for step in steps:
        slug = step["slug"]
        if slug in seen_slugs:
            errors.append(
                Finding(
                    "duplicate_slug",
                    f"slug {slug!r} names both {seen_slugs[slug]!r} and {step['id']!r}",
                    step["id"],
                )
            )
        seen_slugs[slug] = step["id"]

    dependencies: dict[str, list[str]] = {}
    for step in steps:
        step_id = step["id"]
        resolved: list[str] = []
        seen: set[str] = set()
        for dep in step["deps"]:
            target = dep["id"]
            if target == step_id:
                errors.append(
                    Finding("self_dependency", f"step {step_id!r} depends on itself", step_id)
                )
                continue
            if target not in by_id:
                errors.append(
                    Finding(
                        "unresolved_dependency",
                        f"step {step_id!r} depends on {target!r}, "
                        f"which is not a step in this graph",
                        step_id,
                    )
                )
                continue
            if target in seen:
                errors.append(
                    Finding(
                        "duplicate_dependency",
                        f"step {step_id!r} declares {target!r} as a dependency twice",
                        step_id,
                    )
                )
                continue
            seen.add(target)
            resolved.append(target)
            evidence = (dep.get("evidence") or "").strip()
            if not evidence:
                errors.append(
                    Finding(
                        "unjustified_edge",
                        f"the {dep['origin']} edge {target} -> {step_id} carries no evidence "
                        f"from the document",
                        step_id,
                    )
                )
            elif (
                # An answered edge rests on the author's words, not the documents', and
                # `_check_answers` holds it to the question that recorded them instead.
                dep["origin"] != ANSWERED_ORIGIN
                and corpus is not None
                and _flatten(evidence) not in corpus
            ):
                errors.append(
                    Finding(
                        "evidence_not_in_source",
                        f"the edge {target} -> {step_id} quotes "
                        f"{_flatten(evidence)!r}, which no source document contains",
                        step_id,
                    )
                )
        dependencies[step_id] = resolved

    edges: dict[str, list[str]] = {step_id: [] for step_id in by_id}
    for step_id, parents in dependencies.items():
        for parent in parents:
            edges[parent].append(step_id)

    cycle = _find_cycle(edges)
    if cycle:
        errors.append(
            Finding(
                "cycle",
                "these steps form a cycle and cannot be a topology: " + " -> ".join(cycle),
                cycle[0],
            )
        )
    else:
        for step_id in sorted(by_id):
            implied = _redundant_parents(dependencies[step_id], dependencies)
            for parent in sorted(implied):
                warnings.append(
                    Finding(
                        "redundant_edge",
                        f"the edge {parent} -> {step_id} is already implied through "
                        + ", ".join(sorted(implied[parent])),
                        step_id,
                    )
                )

    for step in steps:
        step_id = step["id"]
        if step["kind"].startswith(AGENT_FAMILY):
            model = step["model"]
            if model is None or not model.strip():
                errors.append(
                    Finding(
                        "model",
                        f"step {step_id!r} names no model, so the run's record could not "
                        "say which one did its work",
                        step_id,
                    )
                )
        if step["scope"] == "inputs" and not step["reads"]:
            errors.append(
                Finding(
                    "scope_inputs",
                    f"step {step_id!r} is scoped to its inputs but declares none in `reads`",
                    step_id,
                )
            )
        if step["scope"] != "inputs" and step["reads"]:
            warnings.append(
                Finding(
                    "unused_reads",
                    f"step {step_id!r} declares `reads` but its scope is "
                    f"{step['scope']!r}, so they are never hashed",
                    step_id,
                )
            )
        if step["remediate"] and (
            not step["kind"].startswith(AGENT_FAMILY) or not has_assertion(step)
        ):
            errors.append(
                Finding(
                    "unremediable_step",
                    f"step {step_id!r} declares remediate, which needs an agent step whose "
                    "session can be resumed and an assertion to run again",
                    step_id,
                )
            )
        # An edge carries the words that justify it and the validator checks the quote; a
        # step's bound carries nothing, so one that departs from the plan's own is the
        # derivation's reading and the person confirming the parse is told so. The plan-wide
        # model is the exception, and it is quoted and rechecked above.
        if step["model"] is not None and step["model"] != plan["default_model"]:
            warnings.append(
                Finding(
                    "derived_model",
                    f"step {step_id!r} opens on {step['model']!r} rather than the plan's "
                    f"{plan['default_model']!r}; the derivation read that from the step's "
                    "own document and nothing quotes it",
                    step_id,
                )
            )
        if step["verify_timeout"] != VERIFY_TIMEOUT:
            warnings.append(
                Finding(
                    "derived_timeout",
                    f"step {step_id!r} bounds its assertion at {step['verify_timeout']} s "
                    f"rather than the {VERIFY_TIMEOUT} s default; the derivation supplied "
                    "it and nothing quotes the document for it",
                    step_id,
                )
            )
        if not step["task"].strip():
            errors.append(Finding("empty_task", f"step {step_id!r} carries no task", step_id))
        led = leading_command(step["task"]) if step["kind"].startswith(AGENT_FAMILY) else None
        if led is not None and led[0] in NOT_A_STEP:
            errors.append(
                Finding(
                    "not_a_step",
                    f"step {step_id!r} leads with `{led[1]}`, which {NOT_A_STEP[led[0]]}; "
                    "restate the step's work or leave the step out",
                    step_id,
                )
            )
        command = step.get("command")
        if command is not None and corpus is not None and _flatten(command) not in corpus:
            errors.append(
                Finding(
                    "invented_command",
                    f"step {step_id!r} carries a command no source document gives",
                    step_id,
                )
            )
        assertion = step["assertion"]
        if is_unasserted(step):
            warnings.append(
                Finding(
                    "missing_verify",
                    f"step {step_id!r} has no verify command, so its end state is unasserted",
                    step_id,
                )
            )
        elif is_unverified(step):
            warnings.append(
                Finding(
                    "unverified_step",
                    f"step {step_id!r} is declared unverified: {assertion['reason']}"
                    if assertion is not None
                    else f"step {step_id!r} is declared unverified",
                    step_id,
                )
            )
        elif (
            # A command a human authored at the authoring conversation is by definition
            # absent from the documents. `invented_verify` exists to stop a derivation
            # fabricating one, and an answer on the record is what tells the two apart.
            assertion is None
            and corpus is not None
            and step["verify"] is not None
            and _flatten(step["verify"]) not in corpus
        ):
            errors.append(
                Finding(
                    "invented_verify",
                    f"step {step_id!r} carries a verify command no source document gives",
                    step_id,
                )
            )

    step_slugs = set(seen_slugs)
    for omission in graph["omissions"]:
        if omission["slug"] in step_slugs:
            errors.append(
                Finding(
                    "omitted_and_included",
                    f"{omission['slug']!r} is both a step and an omission",
                )
            )
        if corpus is not None and _flatten(omission["evidence"]) not in corpus:
            errors.append(
                Finding(
                    "evidence_not_in_source",
                    f"the omission {omission['slug']!r} quotes "
                    f"{_flatten(omission['evidence'])!r}, which no source document contains",
                )
            )

    seen_questions: set[tuple[str, str | None, str | None]] = set()
    for question in graph["questions"]:
        if question["step"] and question["step"] not in by_id:
            errors.append(
                Finding(
                    "unknown_question_step",
                    f"a question names step {question['step']!r}, which is not in this graph",
                )
            )
        key = _question_key(question)
        if key in seen_questions:
            errors.append(
                Finding(
                    "duplicate_question",
                    f"two {'edge' if question['dep'] else question['kind']} questions ask "
                    "about the same "
                    + ("edge" if question["dep"] else "step" if question["step"] else "plan")
                    + ", so one answer could not say which it closed",
                    question["step"],
                )
            )
        seen_questions.add(key)
        asked_of = by_id.get(question["step"] or "")
        if (
            question["kind"] == MISSING_VERIFY
            and asked_of is not None
            and not is_unasserted(asked_of)
        ):
            # Its answer is already on the step, and the answer clears the question — so one
            # still standing is a question nothing lists and publication would refuse.
            errors.append(
                Finding(
                    "settled_question",
                    f"a missing_verify question stands on step {asked_of['id']!r}, which "
                    "already has a verify command or a recorded answer",
                    asked_of["id"],
                )
            )
        # The two readings the derivation declares — a task that will duplicate on a
        # resumed run, and a proposed assertion for an end state the plan states in prose —
        # must each quote the sentence they rest on. The quote is what code can check
        # without reading it: a declaration is present and verbatim, or it is refused.
        declares = question["kind"] == NON_CONVERGENT_TASK or (
            question["kind"] == MISSING_VERIFY and question["proposed"] is not None
        )
        evidence = (question.get("evidence") or "").strip()
        if declares and not evidence:
            errors.append(
                Finding(
                    "unquoted_reading",
                    f"the {question['kind']} declaration"
                    + (f" on step {question['step']!r}" if question["step"] else "")
                    + " quotes no words, so nothing can check it against the documents",
                    question["step"],
                )
            )
        elif declares and corpus is not None and _flatten(evidence) not in corpus:
            errors.append(
                Finding(
                    "evidence_not_in_source",
                    f"the {question['kind']} declaration quotes {_flatten(evidence)!r}, "
                    f"which no source document contains",
                    question["step"],
                )
            )
        proposed = question["proposed"]
        if (
            question["kind"] == MISSING_VERIFY
            and proposed is not None
            and cannot_fail(proposed)
        ):
            errors.append(
                Finding(
                    "unassertable_proposal",
                    f"the proposal {proposed!r} cannot fail, so it asserts nothing — "
                    "propose the end state, or propose nothing",
                    question["step"],
                )
            )
    _check_answers(graph, by_id, errors)
    unresolved = open_questions(graph)
    if unresolved:
        warnings.append(
            Finding(
                "open_questions",
                f"{len(unresolved)} question(s) are unanswered and must be put "
                f"to the author",
            )
        )

    return Result(graph, errors, warnings)


def open_questions(graph: Graph) -> list[Question]:
    """Every question the author has not yet answered.

    A `missing_verify` question is cleared by its answer, so standing at all is what makes
    one open; every other kind stays on the graph with the answer beside it.
    """
    return [question for question in graph["questions"] if question["resolution"] is None]


def validate_for_publication(raw: Any, source_root: str) -> Result:
    """The verdict a graph must pass to become a workflow, against its documents as they are.

    Everything `validate` refuses, with every pin and quotation rechecked in this one
    invocation — plus every reading nobody has answered. During derivation an open question
    is work still to do; at publication it is a reading the workflow would silently adopt.
    """
    result = validate(raw, source_root=source_root)
    graph = result.graph
    if graph is None:
        return result
    errors = list(result.errors)
    asked = {
        question["step"]
        for question in graph["questions"]
        if question["kind"] == MISSING_VERIFY
    }
    for question in open_questions(graph):
        where = f" on {question['step']!r}" if question["step"] else ""
        errors.append(
            Finding(
                "unresolved_question",
                f"the {question['kind']} question{where} has no recorded answer: "
                f"{_flatten(question['question'])}",
                question["step"],
            )
        )
    for step in graph["steps"]:
        if is_unasserted(step) and step["id"] not in asked:
            errors.append(
                Finding(
                    "unasserted_step",
                    f"step {step['id']!r} has no verify command and nobody has been asked "
                    "for one",
                    step["id"],
                )
            )
    return Result(graph, errors, result.warnings)
