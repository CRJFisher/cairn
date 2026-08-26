"""The frozen step-graph schema and the defaults a derived graph is normalised against."""

import re
from typing import Any, NotRequired, TypedDict, cast

# 2 reads `tools` as deny patterns. A version-1 graph's allow list would silently invert
# into a denial of exactly the tools its author meant to permit, so it is refused.
GRAPH_VERSION = 2

# A plan authors two kinds. Everything else in the step-kind vocabulary — worktree,
# verify, commit, merge, lock, join — is emitted by the topology and the verify gate
# from the graph's own shape, so it can never appear in a plan's step record. A step that
# waits is a command that calls `cairn wait`, not a kind of its own.
COMMAND_KIND = "command"
AGENT_KIND = re.compile(r"^agent\.[a-z][a-z0-9_]*$")

# The provider half of an `agent.*` kind is written here and in the provider dictionary
# only. Naming a specific provider anywhere else would make adding one a schema change.
AGENT_FAMILY = "agent."
DEFAULT_KIND = "agent.claude"

# The freshness vocabulary is named here, with the rest of what a plan may declare, and
# the runtime keys each name from these. Two enumerations would let a scope exist in the
# schema that nothing can key, or key one the schema rejects.
ONCE_SCOPE = "once"
RUN_SCOPE = "run"
INPUTS_SCOPE = "inputs"
WEEKLY_SCOPE = "weekly"
PERIOD_SCOPES: tuple[str, ...] = ("hourly", "daily", WEEKLY_SCOPE, "monthly")
SCOPES: tuple[str, ...] = (ONCE_SCOPE, RUN_SCOPE, INPUTS_SCOPE, *PERIOD_SCOPES)

DEP_ORIGINS: tuple[str, ...] = ("declared", "derived")

# A command that always exits zero reads as verified in the report while asserting
# nothing, which is worse than the declared absence its author could have chosen instead.
_UNASSERTABLE: tuple[str, ...] = ("true", ":", "exit 0")


def cannot_fail(command: str) -> bool:
    """Whether a verify command could never report the end state missing."""
    return not command.strip() or command.strip() in _UNASSERTABLE

# What a human answered when shown a proposed assertion. A step carries an answer only
# once someone has been asked, so the field's absence is what "never asked" means — and
# a declined step is the only way a step becomes unverified. `authored` is distinct from
# `edited` because a command written where nothing was offered edited no proposal, and
# counting it as one overstates how often the proposals are carrying their weight.
ASSERTION_OUTCOMES: tuple[str, ...] = ("accepted", "edited", "authored", "declined")

OMISSION_REASONS: tuple[str, ...] = ("deferred", "gated", "already_done", "out_of_scope")

QUESTION_KINDS: tuple[str, ...] = (
    "unjustified_edge",
    "missing_verify",
    "non_convergent_task",
    "ambiguous_dependency",
    "unresolved_reference",
    "plan_gated",
)

# I7 forbids an unbounded step, so a timeout is always present. The engine's own default
# is none (01), and a 35m agent step ran uninterrupted there, so the agent bound is set
# above any observed session rather than at the engine's.
AGENT_TIMEOUT = 3600
COMMAND_TIMEOUT = 600

# An agent step's other two bounds, and both are always present on one: a session with no
# dollar ceiling is the one thing a run's offer cannot price, and a session whose model came
# from the environment leaves a record that cannot say which model did the work. The plan
# document sets either; a plan that says nothing gets these. The ceiling sits above any
# observed step cost (17.3 measured $1.54) the way the timeout sits above any observed
# session, and the model is the provider's own stable alias rather than a dated identifier,
# so the default does not go stale with a release.
AGENT_BUDGET_USD = 5.0
AGENT_MODEL = "sonnet"

# Nothing is retried. A step that failed because the provider blinked and one that failed
# because the task is wrong are indistinguishable from outside, and a second paid session
# would run against a repository the first one already changed.
#
# A rate limit is distinguishable — the agent reports it on its own exit status — and was
# the one case argued to be worth a bounded retry. It is not, because the engine's retry
# policy is a static number in a file and cannot read the `resetsAt` the agent supplies.
# A fixed wait short enough to be worth anything is far shorter than a real limit's reset,
# so the retry would usually meet the same limit and pay a second session's tokens to find
# out. The moment is reported instead: a run that stops on a limit says when it is worth
# starting again, and the committed marker means the re-run skips what already landed.
AGENT_RETRIES = 0
COMMAND_RETRIES = 0
RETRY_INTERVAL = 1

# Cairn's own subcommands — worktree setup, commit, prune, lock, and the merge's own
# assertion — plus the plan's verify assertions. Never retried: each is idempotent, and a
# second attempt would only contend with the first one's own lock.
SUPPORT_TIMEOUT = 600
SUPPORT_RETRIES = 0

# A support step's budget has to cover waiting for the git write mutex and then doing the
# git work, and still leave room to write a report. These three are stated together
# because that sum is the whole of the relation; separately they would drift until a
# jammed mutex was killed by the engine with nothing recorded.
GIT_TIMEOUT = 240
MUTEX_WAIT = 300
REPORT_HEADROOM = SUPPORT_TIMEOUT - MUTEX_WAIT - GIT_TIMEOUT

# Landing a wave is the one step that does both jobs: it may pay for a coding-agent session,
# because a conflict is a question about intent no command can answer, and it does the git
# work of a support step on either side of that. So it is priced as the sum rather than as
# the session alone — at `AGENT_TIMEOUT` the mutex wait and the merge in front of the
# session come out of the session's own budget, and the engine's kill lands mid-resolution,
# leaving exactly the unsettled tree the halt path exists to produce only deliberately.
MERGE_TIMEOUT = AGENT_TIMEOUT + MUTEX_WAIT + GIT_TIMEOUT + REPORT_HEADROOM
MERGE_RETRIES = 0

# A wait owns the step's declared bound, so the engine's own kill must land strictly after
# it — otherwise the two fire together and `wait_timeout` never reaches a report. Every
# number derived from a wait step counts this, so the bound stated and the bound enforced
# are the same one.
WAIT_REPORT_GRACE = 15

# The engine applies `timeout_sec` to each attempt rather than to the step [V], so a step's
# worst case is every attempt plus every wait between them. Every duration Cairn states —
# the run's maximum, and the lock reclaim window derived from it — is built from this.
def step_max_seconds(timeout: int, retries: int, interval: int) -> int:
    return timeout * (retries + 1) + interval * retries


# The names Cairn derives for a step's assertion, its record, and a wave's merge slots. A
# plan step whose own id began with one of these would share a node name with a derived
# node, so the namespace is refused to plans.
WORK_PREFIX = "work_"
VERIFY_PREFIX = "verify_"
MARK_PREFIX = "mark_"
MERGE_PREFIX = "merge_"
# `work_` is not reserved: a node name is `<role>_<subject>` and the role is the text before
# the first underscore, so a step called `work_config` yields `work_work_config` and still
# round-trips. The three below are reserved because a step taking one of those names would
# collide with the node another step's name derives.
RESERVED_ID_PREFIXES: tuple[str, ...] = (VERIFY_PREFIX, MARK_PREFIX, MERGE_PREFIX)

# The engine rejects a hyphenated step id with a `use '_' instead of '-'` hint and
# enforces ^[a-zA-Z][a-zA-Z0-9_]*$ (01). Cairn narrows it to lower case so sanitisation
# is a total function with one output per input.
STEP_ID_PATTERN = r"[a-z][a-z0-9_]*"

# A plan slug names a directory and a workflow filename, neither of which carries the
# engine's identifier constraint, so it keeps the hyphens a plan's own name uses. It does
# carry the length one — see below, because the filename is the DAG name.
PLAN_SLUG_PATTERN = r"[a-z0-9][a-z0-9-]*"

# Measured against Dagu 2.11.0: a name of 40 characters loads and 41 is refused at load with
# `- field 'name': name must be less than 40 characters` — the message is off by one against
# the measurement, so the measurement is what is written here. Stated once because three
# names are bounded by it and they are one engine rule: a node name ([topology.py]), the
# handle an assertion's exit status is read through ([verify.py]), and the DAG's own name,
# which is the workflow's filename and therefore the plan slug. The engine counts bytes;
# every name Cairn derives is ASCII by grammar, so bytes and characters coincide.
ENGINE_NAME_MAX_BYTES = 40


def is_plan_kind(value: object) -> bool:
    return value == COMMAND_KIND or (
        isinstance(value, str) and AGENT_KIND.fullmatch(value) is not None
    )


def default_timeout(kind: str) -> int:
    return AGENT_TIMEOUT if kind.startswith(AGENT_FAMILY) else COMMAND_TIMEOUT


def default_retries(kind: str) -> int:
    return AGENT_RETRIES if kind.startswith(AGENT_FAMILY) else COMMAND_RETRIES


def default_budget(kind: str) -> float | None:
    return AGENT_BUDGET_USD if kind.startswith(AGENT_FAMILY) else None


def default_model(kind: str) -> str | None:
    return AGENT_MODEL if kind.startswith(AGENT_FAMILY) else None


def has_assertion(step: "Step") -> bool:
    """Whether this step gets an assertion node at all.

    The topology derives the nodes and the emitters build their bodies, so both have to
    answer this and neither may answer it differently: a step the topology gave no
    assertion node but whose marker was gated on one would wait on a node that is not
    there.
    """
    return step["verify"] is not None


def is_unverified(step: "Step") -> bool:
    """Whether a human looked at a proposed assertion for this step and declined it."""
    assertion = step["assertion"]
    return step["verify"] is None and assertion is not None and assertion["outcome"] == "declined"


def is_unasserted(step: "Step") -> bool:
    """Whether nobody has yet been asked what asserts this step's end state."""
    return step["verify"] is None and step["assertion"] is None


class Source(TypedDict):
    path: str
    sha256: str


class Dep(TypedDict):
    id: str
    origin: str
    evidence: str | None


class Assertion(TypedDict):
    """One human's answer to one proposed assertion."""

    outcome: str
    proposed: str | None
    reason: str | None


class Step(TypedDict):
    id: str
    slug: str
    title: str
    task: str
    command: NotRequired[str]
    command_type: NotRequired[str]
    deps: list[Dep]
    verify: str | None
    assertion: Assertion | None
    kind: str
    tools: list[str] | None
    scope: str
    reads: list[str]
    timeout: int
    retries: int
    max_budget_usd: float | None
    model: str | None


class Collision(TypedDict):
    slug: str
    sanitised_to: str
    assigned: str
    clashed_with: str


class Plan(TypedDict):
    slug: str
    title: str
    source: str
    sources: list[Source]
    default_kind: str
    id_collisions: list[Collision]


class Omission(TypedDict):
    slug: str
    title: str
    reason: str
    evidence: str


class Question(TypedDict):
    kind: str
    step: str | None
    question: str
    evidence: str | None
    # The derivation's own reading of an unasserted step's stated end state: the command it
    # would offer, resting on the sentence `evidence` quotes. Only the agent that read the
    # plan may write one; code afterwards checks the quote, never the reading.
    proposed: str | None


class Graph(TypedDict):
    cairn_graph_version: int
    plan: Plan
    steps: list[Step]
    omissions: list[Omission]
    questions: list[Question]


Spec = dict[str, dict[str, Any]]

STEP_FIELDS: Spec = {
    "id": {"type": str, "required": True},
    "slug": {"type": str, "required": True},
    "title": {"type": str, "required": True},
    "task": {"type": str, "required": True},
    "command": {"type": str},
    "command_type": {"type": str, "enum": ("exec", "wait_until")},
    "deps": {"type": list, "default": []},
    # Required as a key and nullable as a value: a missing verify command is recorded,
    # never invented (08).
    "verify": {"type": str, "required": True, "nullable": True},
    # Absent until someone has been asked. Emission refuses a step with neither a command
    # nor an answer, so an unverified step can only ever be a declined proposal.
    "assertion": {"type": dict, "default": None, "nullable": True},
    "kind": {"type": str, "check": is_plan_kind, "default_from": "plan.default_kind"},
    # null means the provider's own default tool policy; a list is deny patterns.
    "tools": {"type": list, "default": None, "nullable": True, "item_type": str},
    "scope": {"type": str, "enum": SCOPES, "default": "once"},
    "reads": {"type": list, "default": [], "item_type": str},
    "timeout": {"type": int, "default_from": "kind", "nullable": True},
    "retries": {"type": int, "default_from": "kind", "nullable": True},
    # Both null on a command step, which opens no session; both always resolved on an
    # agent step, whose session cannot be priced without them.
    "max_budget_usd": {"type": float, "default_from": "kind", "nullable": True},
    "model": {"type": str, "default_from": "kind", "nullable": True},
}

ASSERTION_FIELDS: Spec = {
    "outcome": {"type": str, "enum": ASSERTION_OUTCOMES, "required": True},
    # What Cairn offered, kept whatever the answer was: a declined proposal is what the
    # report shows beside an unverified step, and an accepted one is what tells an
    # edit from an acceptance.
    "proposed": {"type": str, "default": None, "nullable": True},
    "reason": {"type": str, "default": None, "nullable": True},
}

DEP_FIELDS: Spec = {
    "id": {"type": str, "required": True},
    "origin": {"type": str, "enum": DEP_ORIGINS, "required": True},
    # The recheck pass's justification, quoted from the document. Required on a derived
    # edge: an edge nobody can justify from the plan's own words is a question, not a fact.
    "evidence": {"type": str, "default": None, "nullable": True},
}

SOURCE_FIELDS: Spec = {
    "path": {"type": str, "required": True},
    "sha256": {"type": str, "required": True},
}

COLLISION_FIELDS: Spec = {
    "slug": {"type": str, "required": True},
    "sanitised_to": {"type": str, "required": True},
    "assigned": {"type": str, "required": True},
    "clashed_with": {"type": str, "required": True},
}

PLAN_FIELDS: Spec = {
    "slug": {"type": str, "required": True},
    "title": {"type": str, "required": True},
    "source": {"type": str, "required": True},
    "sources": {"type": list, "default": []},
    "default_kind": {"type": str, "check": is_plan_kind, "default": DEFAULT_KIND},
    "id_collisions": {"type": list, "default": []},
}

OMISSION_FIELDS: Spec = {
    "slug": {"type": str, "required": True},
    "title": {"type": str, "required": True},
    "reason": {"type": str, "enum": OMISSION_REASONS, "required": True},
    "evidence": {"type": str, "required": True},
}

QUESTION_FIELDS: Spec = {
    "kind": {"type": str, "enum": QUESTION_KINDS, "required": True},
    "step": {"type": str, "default": None, "nullable": True},
    "question": {"type": str, "required": True},
    "evidence": {"type": str, "default": None, "nullable": True},
    "proposed": {"type": str, "default": None, "nullable": True},
}

GRAPH_FIELDS: Spec = {
    "cairn_graph_version": {"type": int, "default": GRAPH_VERSION},
    "plan": {"type": dict, "required": True},
    "steps": {"type": list, "required": True},
    "omissions": {"type": list, "default": []},
    "questions": {"type": list, "default": []},
}


class SchemaError(Exception):
    """A graph that cannot be normalised — malformed before any topology check runs."""


def _check_fields(obj: object, spec: Spec, where: str, errors: list[str]) -> None:
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object, found {type(obj).__name__}")
        return
    fields = cast(dict[str, Any], obj)
    for name in sorted(fields):
        if name not in spec:
            errors.append(f"{where}: unknown field {name!r}")
    for name, rule in spec.items():
        if name not in fields:
            if rule.get("required"):
                errors.append(f"{where}: missing required field {name!r}")
            continue
        value: Any = fields[name]
        if value is None:
            if not rule.get("nullable"):
                errors.append(f"{where}.{name}: must not be null")
            continue
        expected: type = rule["type"]
        if expected in (int, float) and isinstance(value, bool):
            errors.append(f"{where}.{name}: expected {expected.__name__}, found bool")
            continue
        # A whole-dollar ceiling arrives from JSON as an int, and rejecting it would make
        # the honest spelling of "at most 5 dollars" a type error.
        accepted: tuple[type, ...] = (int, float) if expected is float else (expected,)
        if not isinstance(value, accepted):
            errors.append(
                f"{where}.{name}: expected {expected.__name__}, found {type(value).__name__}"
            )
            continue
        if "enum" in rule and value not in rule["enum"]:
            allowed = ", ".join(rule["enum"])
            errors.append(f"{where}.{name}: {value!r} is not one of {allowed}")
        check = rule.get("check")
        if check is not None and not check(value):
            errors.append(f"{where}.{name}: {value!r} is not a kind a plan can author")
        item_type: type | None = rule.get("item_type")
        if item_type is not None and isinstance(value, list):
            for index, item in enumerate(cast(list[Any], value)):
                if not isinstance(item, item_type):
                    errors.append(
                        f"{where}.{name}[{index}]: expected {item_type.__name__}, "
                        f"found {type(item).__name__}"
                    )


def _apply_defaults(record: dict[str, Any], spec: Spec) -> None:
    """Fill every field the spec gives a literal default for.

    The spec is the single statement of what a default is. A default applied anywhere
    else would let the two disagree, which is the class of defect this contract exists
    to stop — so `default_from` fields, whose value depends on another field, are the
    only ones the caller resolves.
    """
    for name, rule in spec.items():
        if "default" not in rule or record.get(name) is not None:
            continue
        default: Any = rule["default"]
        record[name] = list(cast(list[Any], default)) if isinstance(default, list) else default


def _items(container: Any, name: str) -> list[Any]:
    """The list under `name`, or empty when it is not a list — the check reports that."""
    if not isinstance(container, dict):
        return []
    value: Any = cast(dict[str, Any], container).get(name)
    return cast(list[Any], value) if isinstance(value, list) else []


def _check_assertion(step: dict[str, Any], where: str, errors: list[str]) -> None:
    """The answer and the command it produced have to say the same thing.

    A graph is the only record of what a human decided, so a shape that could be read two
    ways is refused here rather than resolved later by whichever reader gets there first.
    """
    assertion: Any = step.get("assertion")
    if assertion is None:
        return
    _check_fields(assertion, ASSERTION_FIELDS, f"{where}.assertion", errors)
    if not isinstance(assertion, dict):
        return
    answer = cast(dict[str, Any], assertion)
    outcome = answer.get("outcome")
    verify = step.get("verify")
    if outcome == "declined":
        if verify is not None:
            errors.append(
                f"{where}: a declined assertion leaves the step unverified, so it cannot "
                "also carry a verify command"
            )
        if not (answer.get("reason") or "").strip():
            errors.append(f"{where}.assertion: a declined proposal must say why")
    elif outcome in ("accepted", "edited", "authored"):
        proposed = answer.get("proposed")
        if verify is None:
            errors.append(
                f"{where}: an assertion answered {outcome!r} must carry the verify command "
                "it answered with"
            )
        elif outcome == "accepted" and verify != proposed:
            errors.append(
                f"{where}: an accepted proposal must be the command that was proposed"
            )
        elif outcome == "edited" and (proposed is None or verify == proposed):
            errors.append(
                f"{where}: an edited proposal must differ from the command that was "
                "proposed, and there must have been one"
            )
        elif outcome == "authored" and proposed is not None:
            errors.append(
                f"{where}: an authored command answered no proposal, so none may be recorded"
            )


def _check_graph(raw: Any, errors: list[str]) -> None:
    """Every structural check, run to completion before a single value is used.

    Checking and building are separate passes because a value that failed its type check
    must never be dereferenced: reading it would raise where the contract promises a
    verdict, and a crash and a rejection are indistinguishable to a caller.
    """
    _check_fields(raw, GRAPH_FIELDS, "graph", errors)
    if errors:
        return

    plan = raw["plan"]
    _check_fields(plan, PLAN_FIELDS, "plan", errors)
    for index, source in enumerate(_items(plan, "sources")):
        _check_fields(source, SOURCE_FIELDS, f"plan.sources[{index}]", errors)
    for index, collision in enumerate(_items(plan, "id_collisions")):
        _check_fields(collision, COLLISION_FIELDS, f"plan.id_collisions[{index}]", errors)

    for index, raw_step in enumerate(cast(list[Any], raw["steps"])):
        where = f"steps[{index}]"
        _check_fields(raw_step, STEP_FIELDS, where, errors)
        if isinstance(raw_step, dict):
            step_fields = cast(dict[str, Any], raw_step)
            kind = step_fields.get("kind", plan.get("default_kind", DEFAULT_KIND))
            command = step_fields.get("command")
            command_type = step_fields.get("command_type")
            if kind == COMMAND_KIND and (
                not isinstance(command, str) or not command.strip()
            ):
                errors.append(f"{where}: command kind requires a non-empty 'command' field")
            if kind == COMMAND_KIND and command_type not in ("exec", "wait_until"):
                errors.append(f"{where}: command kind requires a 'command_type' field")
            if kind == COMMAND_KIND and step_fields.get("tools") is not None:
                errors.append(
                    f"{where}: command kind must not carry 'tools' — a tool policy is an "
                    "agent's blast radius and nothing translates it for a shell command"
                )
            for bound in ("max_budget_usd", "model"):
                if kind == COMMAND_KIND and step_fields.get(bound) is not None:
                    errors.append(
                        f"{where}: command kind must not carry {bound!r} — it opens no "
                        "agent session for the bound to apply to"
                    )
            if (
                isinstance(kind, str)
                and kind.startswith(AGENT_FAMILY)
                and command is not None
            ):
                errors.append(f"{where}: agent kind must not carry a 'command' field")
            if (
                isinstance(kind, str)
                and kind.startswith(AGENT_FAMILY)
                and command_type is not None
            ):
                errors.append(f"{where}: agent kind must not carry a 'command_type' field")
            _check_assertion(step_fields, where, errors)
        for dep_index, raw_dep in enumerate(_items(raw_step, "deps")):
            _check_fields(raw_dep, DEP_FIELDS, f"{where}.deps[{dep_index}]", errors)

    for index, omission in enumerate(_items(raw, "omissions")):
        _check_fields(omission, OMISSION_FIELDS, f"omissions[{index}]", errors)
    for index, question in enumerate(_items(raw, "questions")):
        where = f"questions[{index}]"
        _check_fields(question, QUESTION_FIELDS, where, errors)
        if isinstance(question, dict):
            fields = cast(dict[str, Any], question)
            if fields.get("proposed") is not None and fields.get("kind") != "missing_verify":
                errors.append(
                    f"{where}: only a missing_verify question can carry a proposed "
                    "assertion — on any other question the field answers nothing"
                )


def _copy(record: dict[str, Any]) -> dict[str, Any]:
    """A copy whose list fields are the caller's no longer."""
    return {
        key: list(cast(list[Any], value)) if isinstance(value, list) else value
        for key, value in record.items()
    }


def normalise(raw: Any) -> Graph:
    """Apply every default and return the canonical graph, or raise SchemaError."""
    errors: list[str] = []
    _check_graph(raw, errors)
    if errors:
        raise SchemaError("\n".join(errors))

    plan = _copy(cast(dict[str, Any], raw["plan"]))
    _apply_defaults(plan, PLAN_FIELDS)
    plan["sources"] = [_copy(cast(dict[str, Any], s)) for s in plan["sources"]]
    plan["id_collisions"] = [_copy(cast(dict[str, Any], c)) for c in plan["id_collisions"]]

    steps: list[Step] = []
    for raw_step in cast(list[Any], raw["steps"]):
        step = _copy(cast(dict[str, Any], raw_step))
        _apply_defaults(step, STEP_FIELDS)
        if step["assertion"] is not None:
            assertion = _copy(cast(dict[str, Any], step["assertion"]))
            _apply_defaults(assertion, ASSERTION_FIELDS)
            step["assertion"] = cast(Assertion, assertion)
        if step.get("kind") is None:
            step["kind"] = plan["default_kind"]
        if step.get("timeout") is None:
            step["timeout"] = default_timeout(step["kind"])
        if step.get("retries") is None:
            step["retries"] = default_retries(step["kind"])
        if step.get("max_budget_usd") is None:
            step["max_budget_usd"] = default_budget(step["kind"])
        else:
            step["max_budget_usd"] = float(step["max_budget_usd"])
        if step.get("model") is None:
            step["model"] = default_model(step["kind"])
        deps: list[Dep] = []
        for raw_dep in cast(list[Any], step["deps"]):
            dep = _copy(cast(dict[str, Any], raw_dep))
            dep.setdefault("evidence", None)
            deps.append(cast(Dep, dep))
        step["deps"] = deps
        steps.append(cast(Step, step))

    omissions: list[Omission] = [
        cast(Omission, _copy(cast(dict[str, Any], item))) for item in raw.get("omissions", [])
    ]
    questions: list[Question] = []
    for item in raw.get("questions", []):
        question = _copy(cast(dict[str, Any], item))
        _apply_defaults(question, QUESTION_FIELDS)
        questions.append(cast(Question, question))

    return {
        "cairn_graph_version": raw.get("cairn_graph_version", GRAPH_VERSION),
        "plan": cast(Plan, plan),
        "steps": steps,
        "omissions": omissions,
        "questions": questions,
    }
