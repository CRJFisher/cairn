"""Refuse to run a definition Cairn would not stake the run on.

The engine's own validator is strict about shape and blind to content, and every hole is a
hole that reports success. Measured against Dagu 2.11.0, `dagu validate` exits 0 on a file
carrying a dependency cycle, on an unresolved `${...}`, on `mark_success`, on a step with no
timeout, and on a step with no working directory. So this is a first-class component rather
than a lint: Cairn already holds a validated graph ([04]), and the preflight's job is to keep
that guarantee across a translation the engine will not check.

Every rule reads the document **re-parsed from the bytes on disk**, never the structure that
produced them, so a fault in serialisation is inside the blast radius rather than behind it.

**Nothing here may assume the document is one Cairn wrote.** `cairn workflow check` exists to
judge a file the engine's own editing surface rewrote, so every rule reads through accessors
that tolerate any shape and answers with a refusal rather than an exception. A rule that
raised on a malformed file would fail exactly where it is needed.

Three rules exist because the engine never looks at all: it builds no execution plan, so it
sees no **cycle**; it resolves no **reference**, so a dangling one is silent; and it reads no
step **name**, because its identifier grammar applies to a step's `id`. Most of the rest
cover places the engine looks and permits.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from typing import Any, NamedTuple, cast

from cairn.plan.schema import INPUTS_SCOPE, ONCE_SCOPE
from cairn.topology import TopologyError, check_name, parse_node_name
from cairn.verify import verify_handle
from cairn.workflow.build import EXIT_HANDLER, PYTHONPATH_ENV, step_concurrency
from cairn.workflow.schema import (
    CAIRN_INVOCATION,
    CATCHUP_DISABLED,
    GRAPH_TYPE,
    OCCASION_PARAM,
    OVERLAP_SKIP,
    PARAMETERS,
    REFERENCE,
    ROOT_KEYS,
    is_agent_body,
    references_in,
    resolvable_names,
)

GATE_PROBE_TIMEOUT = 30
EXIT_CODE_SUFFIX = ".exit_code"

# The two gates, as the argv the emitters actually build. Matched as a token prefix rather
# than as text anywhere in the condition: a plan supplies operands — a step id, a path it
# reads — and one containing the words "verify gate" would otherwise route a marker-gated
# step to the rule for a verify-gated one.
MARKER_GATE_ARGV = (*CAIRN_INVOCATION, "marker", "absent")
VERIFY_GATE_ARGV = (*CAIRN_INVOCATION, "verify", "gate")


class Rule(NamedTuple):
    """One reason a definition must not run, and what happens if it does."""

    name: str
    consequence: str


class Fault(NamedTuple):
    """One refusal, named where a person can act on it."""

    rule: str
    step: str | None
    detail: str

    def __str__(self) -> str:
        where = f" [{self.step}]" if self.step else ""
        return f"{self.rule}{where}: {self.detail} — {consequence_of(self.rule)}"


# Every row is a construct the engine's validator accepts — most of them it runs without
# complaint — and every one breaks an invariant. The consequence is printed with the refusal,
# because a refusal that does not say what it prevented reads as pedantry.
RULES: tuple[Rule, ...] = (
    Rule("cycle", "the run never starts, and `dagu validate` exits 0 on the same file"),
    Rule("unresolved_reference", "the reference empties and the step runs on a corrupted argument"),
    Rule("reference_out_of_position", "quoting decides whether it substitutes, splits, or executes"),
    Rule("with_block", "executor configuration is retyped by YAML behind Cairn's back"),
    Rule("mark_success", "a failed step is rewritten as succeeded, on disk and in the API"),
    Rule("continue_on_output", "routing on stdout text, which for an agent step is self-report"),
    Rule("assertion_absorbs_no_failure", "one branch's failed assertion aborts the merge join"),
    Rule("absorbs_a_failure", "the next merge slot writes over a conflicted index"),
    Rule("reference_without_id", "it resolves to nothing and the branch drops with no failed node"),
    Rule("gate_without_skipped", "a correct no-op cascades and the plan evaporates into a success"),
    Rule("commit_without_skipped", "an excluded branch's skip cascades and the wave lands nothing"),
    Rule("marker_with_skipped", "the commit runs anyway and lands exactly the unverified work"),
    Rule("gate_unresolvable", "every step skips into a clean success"),
    Rule("foreign_condition", "the gate runs a command Cairn did not write, and `dagu dry` runs it"),
    Rule("scope_without_occasion", "a recovery cannot continue the occasion it is recovering"),
    Rule("missing_timeout", "there is no default; the step can hang for ever"),
    Rule("unbounded_session", "a paid session opens whose price and model nobody stated, so no offer can price the run"),
    Rule("missing_working_dir", "the step runs in a scratch directory, not the repository"),
    Rule("wrong_graph_type", "one deletes the dependency graph, the other serialises it"),
    Rule("body_not_one_invocation", "logic in a generated file is untestable"),
    Rule("top_level_name", "the validator rejects the file while a run would accept it"),
    Rule("node_name", "the run model cannot parse the name back into a role and a step"),
    Rule("unexpected_id", "a step exempts its own body from the one-invocation rule"),
    Rule("unexpected_handler", "a lifecycle body runs that no rule has looked at"),
    Rule("unbounded_retry", "the machine's own configuration decides how often paid work repeats"),
    Rule("undeclared_parameter", "a caller can vary something the run cannot survive varying"),
    Rule("inherited_concurrency", "zero reads as unset, so the machine's cap decides the width"),
    Rule("catchup_replay", "a cron slot missed while the machine slept replays as a paid session"),
    Rule("inherited_overlap", "the machine decides what a firing arriving mid-run costs"),
    Rule("schedule_with_fixed_occasion", "every firing after the first no-ops into a clean success"),
    Rule("foreign_root_key", "the machine's own configuration decides a field no rule has read"),
    Rule("not_a_document", "there is nothing here a run could be built from"),
    Rule("engine_validate", "the engine refuses to load the file"),
    Rule("engine_dry", "the engine cannot build an execution plan from the file"),
)

_CONSEQUENCES: dict[str, str] = {rule.name: rule.consequence for rule in RULES}


def consequence_of(rule: str) -> str:
    return _CONSEQUENCES.get(rule, "it breaks an invariant Cairn holds")


def _mapping(document: Any) -> dict[str, Any]:
    return cast(dict[str, Any], document) if isinstance(document, dict) else {}


def _steps(document: Any) -> list[dict[str, Any]]:
    raw = _mapping(document).get("steps")
    if not isinstance(raw, list):
        return []
    return [s for s in cast(list[Any], raw) if isinstance(s, dict)]


def _handlers(document: Any) -> dict[str, dict[str, Any]]:
    """Every lifecycle handler, not only the one Cairn emits.

    A handler runs a body like any other node, so one added under a key the emitter never
    writes would otherwise carry a construct no rule had looked at.
    """
    raw = _mapping(document).get("handler_on")
    if not isinstance(raw, dict):
        return {}
    return {
        name: cast(dict[str, Any], body)
        for name, body in cast(dict[str, Any], raw).items()
        if isinstance(body, dict)
    }


def _nodes(document: Any) -> list[dict[str, Any]]:
    return _steps(document) + list(_handlers(document).values())


def _declared(document: Any) -> list[str]:
    raw = _mapping(document).get("params")
    if not isinstance(raw, list):
        return []
    return [
        key
        for entry in cast(list[Any], raw)
        if isinstance(entry, dict)
        for key in cast(dict[str, Any], entry)
    ]


def _handle(step: dict[str, Any]) -> str | None:
    handle = step.get("id")
    return handle if isinstance(handle, str) else None


def _is_plan_assertion(step: dict[str, Any]) -> bool:
    """Whether this node runs a command the plan's author wrote rather than one Cairn built.

    A plan's assertion is emitted bare — pipes, globs and all — so it is the one body exempt
    from the one-invocation rule. The exemption is bound to the node's *name* as well as its
    handle, because a document the preflight reads may not be one Cairn wrote: keyed on the
    presence of an `id` alone, any step could exempt its own body by declaring one.
    """
    name = step.get("name")
    handle = _handle(step)
    if not isinstance(name, str) or handle is None:
        return False
    role, _, subject = name.partition("_")
    return role == "verify" and bool(subject) and handle == verify_handle(subject)


def _conditions(step: dict[str, Any]) -> list[str]:
    raw = step.get("preconditions")
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    for entry in cast(list[Any], raw):
        if isinstance(entry, dict):
            condition = cast(dict[str, Any], entry).get("condition")
            if isinstance(condition, str):
                found.append(condition)
    return found


def _words(text: str) -> list[str] | None:
    """A command split into its words, or None where the text is not a command at all."""
    try:
        return shlex.split(text)
    except ValueError:
        return None


def _gate_kind(condition: str) -> str | None:
    words = _words(condition)
    if words is None:
        return None
    if tuple(words[: len(MARKER_GATE_ARGV)]) == MARKER_GATE_ARGV:
        return "marker"
    if tuple(words[: len(VERIFY_GATE_ARGV)]) == VERIFY_GATE_ARGV:
        return "verify"
    return None


def _find_keys(value: Any, wanted: str) -> bool:
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if wanted in mapping:
            return True
        return any(_find_keys(item, wanted) for item in mapping.values())
    if isinstance(value, list):
        return any(_find_keys(item, wanted) for item in cast(list[Any], value))
    return False


def _dependencies(step: dict[str, Any]) -> list[str]:
    """A step's edges, however the file spells them.

    The engine accepts a bare scalar as well as a list, so reading only lists would let a
    cyclic file spelled the other way past the one rule that exists to catch it.
    """
    raw = step.get("depends")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return sorted(d for d in cast(list[Any], raw) if isinstance(d, str))
    return []


def _cycle(steps: list[dict[str, Any]]) -> list[str] | None:
    """One cycle in the emitted dependency graph, deterministic in name order.

    The plan's own graph was checked before a topology existed, but the file's `depends`
    edges are what the engine reads and they are what could be cyclic here.
    """
    edges: dict[str, list[str]] = {}
    for step in steps:
        name = step.get("name")
        if isinstance(name, str):
            edges[name] = _dependencies(step)
    colour: dict[str, int] = {}
    stack: list[str] = []

    def walk(node: str) -> list[str] | None:
        colour[node] = 1
        stack.append(node)
        for nxt in edges.get(node, []):
            if colour.get(nxt) == 1:
                return stack[stack.index(nxt) :] + [nxt]
            if colour.get(nxt, 0) == 0 and nxt in edges:
                found = walk(nxt)
                if found:
                    return found
        stack.pop()
        colour[node] = 2
        return None

    for node in sorted(edges):
        if colour.get(node, 0) == 0:
            found = walk(node)
            if found:
                return found
    return None


def check(document: Any) -> list[Fault]:
    """Every structural refusal, over the document as the engine will parse it."""
    if not isinstance(document, dict):
        return [
            Fault(
                "not_a_document",
                None,
                f"the file holds {type(document).__name__}, not a workflow",
            )
        ]
    faults: list[Fault] = []
    fields = _mapping(document)
    steps = _steps(document)

    if "name" in fields:
        faults.append(
            Fault("top_level_name", None, "the workflow's name comes from its filename")
        )
    if fields.get("type") != GRAPH_TYPE:
        faults.append(
            Fault(
                "wrong_graph_type",
                None,
                f"type is {fields.get('type')!r}, not {GRAPH_TYPE!r}",
            )
        )
    concurrency: Any = fields.get("max_active_steps")
    width = step_concurrency(len(steps))
    if not isinstance(concurrency, int) or isinstance(concurrency, bool):
        faults.append(
            Fault(
                "inherited_concurrency",
                None,
                f"max_active_steps is {concurrency!r}; zero and absent both inherit the "
                "machine's cap, so it states a number no wave can exceed",
            )
        )
    elif concurrency < width:
        faults.append(
            Fault(
                "inherited_concurrency",
                None,
                f"max_active_steps is {concurrency}, under this file's {width} nodes",
            )
        )
    declared = _declared(document)
    if tuple(declared) != PARAMETERS:
        faults.append(
            Fault("undeclared_parameter", None, f"params are {declared}, not {list(PARAMETERS)}")
        )
    faults.extend(_check_triggering(fields))
    for name in _handlers(document):
        if name != EXIT_HANDLER:
            faults.append(
                Fault("unexpected_handler", name, f"handler_on carries {name!r}")
            )

    faults.extend(_check_references(document))

    cycle = _cycle(steps)
    if cycle:
        faults.append(Fault("cycle", cycle[0], " -> ".join(cycle)))

    for step in _nodes(document):
        faults.extend(_check_step(step, document))
    return faults


def _check_triggering(fields: dict[str, Any]) -> list[Fault]:
    """Everything about when this file runs, and what the machine would decide if it did not.

    Three of these cannot fire on Cairn's own output by construction. They exist because
    `cairn workflow check` judges a file the engine's own canvas rewrote, and every key here
    is one the engine accepts without complaint — measured, `dagu validate` and `dagu dry`
    both exit 0 on a file carrying any of them.
    """
    faults: list[Fault] = []
    if fields.get("catchup_window") != CATCHUP_DISABLED:
        faults.append(
            Fault(
                "catchup_replay",
                None,
                f"catchup_window is {fields.get('catchup_window')!r}, not the empty string "
                "that turns replay off; a positive duration replays every missed slot, and "
                "omitting it inherits whatever the machine's base configuration holds",
            )
        )
    if fields.get("overlap_policy") != OVERLAP_SKIP:
        faults.append(
            Fault(
                "inherited_overlap",
                None,
                f"overlap_policy is {fields.get('overlap_policy')!r}, not {OVERLAP_SKIP!r}",
            )
        )
    # A schedule has no override point at all ([03]), so a pinned occasion in a scheduled
    # file is fixed for every firing — and the freshness key for `run` scope *is* that
    # value. Measured over three firings of such a file: the first did the work, the
    # second and third reported `succeeded` with the step skipped.
    if "schedule" in fields:
        for entry in cast(list[Any], fields.get("params") or []):
            if isinstance(entry, dict) and cast(dict[str, Any], entry).get(OCCASION_PARAM):
                faults.append(
                    Fault(
                        "schedule_with_fixed_occasion",
                        None,
                        "a scheduled file declares a fixed occasion, which every firing "
                        "reuses because cron has no override point; leave it empty and the "
                        "run mints its own",
                    )
                )
    unexpected = sorted(set(fields) - ROOT_KEYS)
    for key in unexpected:
        faults.append(Fault("foreign_root_key", None, f"the document declares {key!r}"))
    return faults


def _check_references(document: Any) -> list[Fault]:
    """Every reference must resolve, and must stand where substitution is safe.

    Measured against Dagu 2.11.0: a reference substitutes shell-free in `working_dir`, but in
    a body or a condition it is left to the shell — where single quotes make it inert, a bare
    token splits on whitespace, and double quotes execute whatever the value holds. A
    parameter is an editable field at trigger time ([03]), so the last of those is an
    injection surface.

    The rule therefore fires on what the engine would **substitute** — a declared parameter or
    environment entry — rather than on every `${`. A plan author's own assertion may say
    `${HOME}`, and an agent's prompt may quote a shell variable; neither is a value a caller
    can vary, and neither is Cairn's to rewrite. The one reference Cairn does emit outside a
    working directory is `${<id>.exit_code}` in a precondition, which the engine resolves
    itself and which quoting does not defeat ([01]).
    """
    faults: list[Fault] = []
    resolvable = resolvable_names(document)
    varying = frozenset(_declared(document)) | frozenset(
        key
        for entry in cast(list[Any], _mapping(document).get("env") or [])
        if isinstance(entry, dict)
        for key in cast(dict[str, Any], entry)
    )
    for step in _nodes(document):
        name = cast(str | None, step.get("name"))
        directory = step.get("working_dir")
        if isinstance(directory, str):
            for found in references_in(directory):
                if found not in resolvable:
                    faults.append(
                        Fault("unresolved_reference", name, f"${{{found}}} resolves to nothing")
                    )
        body = step.get("run")
        if isinstance(body, str):
            for found in references_in(body):
                if found in varying:
                    faults.append(
                        Fault(
                            "reference_out_of_position",
                            name,
                            f"run carries ${{{found}}}, which the engine substitutes into a "
                            "body; a parameter reaches a step through its environment",
                        )
                    )
        for condition in _conditions(step):
            for found in REFERENCE.findall(condition):
                if found.endswith(EXIT_CODE_SUFFIX):
                    handle = found[: -len(EXIT_CODE_SUFFIX)]
                    if handle not in {h for s in _steps(document) if (h := _handle(s))}:
                        faults.append(
                            Fault(
                                "reference_without_id",
                                name,
                                f"${{{found}}} names no step declaring that id",
                            )
                        )
                elif found in varying:
                    faults.append(
                        Fault(
                            "reference_out_of_position",
                            name,
                            f"a precondition carries ${{{found}}}, which the engine "
                            "substitutes into a command",
                        )
                    )
                else:
                    faults.append(
                        Fault("unresolved_reference", name, f"${{{found}}} resolves to nothing")
                    )
    return faults


def _check_step(step: dict[str, Any], document: Any) -> list[Fault]:
    faults: list[Fault] = []
    name = cast(str | None, step.get("name"))
    is_handler = step in _handlers(document).values()

    if _find_keys(step, "mark_success"):
        faults.append(Fault("mark_success", name, "mark_success appears in this step"))
    continue_on = step.get("continue_on")
    flags = cast(dict[str, Any], continue_on) if isinstance(continue_on, dict) else {}
    if "output" in flags:
        faults.append(Fault("continue_on_output", name, "continue_on routes on output"))
    if _find_keys(step, "with"):
        faults.append(Fault("with_block", name, "a with block reaches an executor's config"))

    if not isinstance(step.get("timeout_sec"), int):
        faults.append(Fault("missing_timeout", name, "no timeout_sec"))
    if not isinstance(step.get("working_dir"), str) or not step.get("working_dir"):
        faults.append(Fault("missing_working_dir", name, "no working_dir"))
    retry = step.get("retry_policy")
    bounded = isinstance(retry, dict) and {"limit", "interval_sec"} <= set(
        cast(dict[str, Any], retry)
    )
    # A lifecycle handler takes no retry policy: the engine runs it once on the way out.
    if not is_handler and not bounded:
        faults.append(
            Fault("unbounded_retry", name, "retry_policy needs both limit and interval_sec")
        )

    faults.extend(_check_routing(step, name, flags))
    faults.extend(_check_gates(step, document, name, flags))
    faults.extend(_check_body(step, name))
    faults.extend(_check_bounds(step, name))
    if name is not None and not is_handler:
        faults.extend(_check_name(name))
    if _handle(step) is not None and not _is_plan_assertion(step):
        faults.append(
            Fault("unexpected_id", name, "only a step's own assertion declares an id")
        )
    return faults


def _is_merge_chain(name: str | None) -> bool:
    """Whether this node is a wave's landing or the proof of one.

    A merge's proof is a `verify` node like a step's assertion, so the two are told apart by
    what they name rather than by their role.
    """
    if not isinstance(name, str):
        return False
    return name.startswith(("merge_", "verify_merge_"))


def _check_routing(
    step: dict[str, Any], name: str | None, flags: dict[str, Any]
) -> list[Fault]:
    """Which nodes may absorb a failure, and which must not.

    A step's assertion has to stay recorded as failed while the run survives to reach the
    join, and a step's own work carries the same flag so that its self-reported failure
    cannot abort its assertion ([verify-gate.md]).

    **No node of a merge chain carries `continue_on` in either spelling.** A halt has to stop
    the slots behind it and the prune after them, and a flag there would let the next slot
    write over a conflicted index ([merge-step.md]).
    """
    if _is_plan_assertion(step):
        if flags.get("failure") is not True:
            return [
                Fault(
                    "assertion_absorbs_no_failure",
                    name,
                    "a step's assertion must stay recorded as failed while the run reaches "
                    "the join",
                )
            ]
        return []
    if _is_merge_chain(name) and flags:
        return [
            Fault(
                "absorbs_a_failure",
                name,
                f"a merge chain node carries continue_on: {sorted(flags)}",
            )
        ]
    return []


def _check_gates(
    step: dict[str, Any], document: Any, name: str | None, flags: dict[str, Any]
) -> list[Fault]:
    faults: list[Fault] = []
    kinds = {kind for c in _conditions(step) if (kind := _gate_kind(c)) is not None}
    for condition in _conditions(step):
        if _gate_kind(condition) is None:
            faults.append(
                Fault(
                    "foreign_condition",
                    name,
                    f"a precondition Cairn did not emit: {condition[:80]!r}",
                )
            )
    if "marker" in kinds:
        if flags.get("skipped") is not True:
            faults.append(
                Fault("gate_without_skipped", name, "a marker-gated step must survive its no-op")
            )
        faults.extend(_check_occasion(document, step, name))
    if "verify" in kinds and flags.get("skipped"):
        faults.append(
            Fault(
                "marker_with_skipped",
                name,
                "a verify-gated step must let a closed gate reach the commit",
            )
        )
    # The commit is the one node in a step's group that routes, and only in an isolated wave.
    # There the flag stops an exclusion's cascade at that branch so the join still runs;
    # without it the whole wave skips and the run reports a clean success having landed
    # nothing. In a chain the flag is absent on purpose, so the cascade carries on into
    # everything that depended on the work — which is why the position is read off the file
    # rather than guessed from the name: a commit is in an isolated wave exactly when a join
    # waits on it.
    if isinstance(name, str) and name in _joined(document) and not flags.get("skipped"):
        faults.append(
            Fault(
                "commit_without_skipped",
                name,
                "a commit a join waits on must stop the exclusion cascade",
            )
        )
    return faults


def _joined(document: Any) -> frozenset[str]:
    """Every node a wave's join waits on — the commits of an isolated wave."""
    waited: set[str] = set()
    for step in _steps(document):
        name = step.get("name")
        if isinstance(name, str) and name.startswith("join_"):
            waited.update(_dependencies(step))
    return frozenset(waited)


def _check_occasion(document: Any, step: dict[str, Any], name: str | None) -> list[Fault]:
    declared = frozenset(_declared(document))
    for condition in _conditions(step):
        words = _words(condition)
        if words is None or "--scope" not in words:
            continue
        index = words.index("--scope") + 1
        if index >= len(words):
            return [Fault("scope_without_occasion", name, "the gate names no scope")]
        scope = words[index]
        if scope in (ONCE_SCOPE, INPUTS_SCOPE) or OCCASION_PARAM in declared:
            continue
        return [
            Fault(
                "scope_without_occasion",
                name,
                f"scope {scope!r} keys on the run occasion, and nothing declares the "
                "parameter a recovery would pass to continue an earlier one",
            )
        ]
    return []


def _flag_value(words: list[str], flag: str) -> str | None:
    """The operand after one flag, or None where the flag or its operand is missing."""
    for index, word in enumerate(words[:-1]):
        if word == flag:
            return words[index + 1]
    return None


def _check_bounds(step: dict[str, Any], name: str | None) -> list[Fault]:
    """An agent body must state its own price and model, because the offer reads the file.

    The timeout has its own rule; these are the two bounds only the body can carry. A body
    that is not an agent invocation at all is another rule's business.
    """
    body = step.get("run")
    if not isinstance(body, str) or not is_agent_body(body):
        return []
    words = _words(body) or []
    faults: list[Fault] = []
    ceiling = _flag_value(words, "--max-budget-usd")
    try:
        priced = ceiling is not None and float(ceiling) > 0
    except ValueError:
        priced = False
    if not priced:
        faults.append(
            Fault("unbounded_session", name, "the body names no positive --max-budget-usd")
        )
    model = _flag_value(words, "--model")
    if model is None or not model.strip():
        faults.append(Fault("unbounded_session", name, "the body names no --model"))
    return faults


def _check_body(step: dict[str, Any], name: str | None) -> list[Fault]:
    body = step.get("run")
    if isinstance(body, list):
        # Measured: a list is a sequence of separate shell commands, not an argv vector.
        return [
            Fault("body_not_one_invocation", name, "a list body runs several commands in turn")
        ]
    if not isinstance(body, str) or not body.strip():
        return [Fault("body_not_one_invocation", name, "no command")]
    if _is_plan_assertion(step):
        return []
    words = _words(body)
    if words is None:
        return [Fault("body_not_one_invocation", name, "the body is not a command at all")]
    if body != shlex.join(words):
        return [Fault("body_not_one_invocation", name, "the body is not one quoted invocation")]
    return []


def _check_name(name: str) -> list[Fault]:
    """The grammar the run model parses names back with, enforced by that same parser."""
    try:
        parse_node_name(name)
        check_name(name)
    except TopologyError as exc:
        return [Fault("node_name", name, str(exc))]
    return []


def rehearse_gate(document: Any) -> list[Fault]:
    """Prove the interpreter this file declares can import Cairn, by running it.

    This is the one rule that cannot be read off the file. A gate that cannot launch exits
    nonzero from outside Cairn, and the engine reads any nonzero as "skip this step" — so a
    plan whose Cairn does not resolve skips every step and reports a clean success with
    nothing done. The engine hands a step a curated environment rather than the caller's
    ([06]), so the probe runs under the environment the file declares plus the `PATH` the run
    will inherit, and from a directory holding no `cairn` package: run from the package's own
    root the interpreter finds it through the working directory whatever the environment says,
    and the probe would pass for a workflow whose steps could not import it at all.
    """
    environment = {"PATH": os.environ.get("PATH", "")}
    for entry in cast(list[Any], _mapping(document).get("env") or []):
        if isinstance(entry, dict):
            environment.update(cast(dict[str, str], entry))
    if PYTHONPATH_ENV not in environment:
        return [Fault("gate_unresolvable", None, f"the file declares no {PYTHONPATH_ENV}")]
    try:
        completed = subprocess.run(
            list(CAIRN_INVOCATION),
            env=environment,
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            timeout=GATE_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [Fault("gate_unresolvable", None, f"{shlex.join(CAIRN_INVOCATION)}: {exc}")]
    if completed.returncode != 0:
        return [
            Fault(
                "gate_unresolvable",
                None,
                f"{shlex.join(CAIRN_INVOCATION)} exited {completed.returncode} under the "
                f"environment this file declares: {completed.stderr.strip()[:200]}",
            )
        ]
    return []


def preflight(document: Any) -> list[Fault]:
    """Every refusal this file earns, structural and rehearsed alike."""
    faults = check(document)
    return faults if faults else rehearse_gate(document)


__all__ = [
    "RULES",
    "Fault",
    "Rule",
    "check",
    "consequence_of",
    "preflight",
    "rehearse_gate",
]
