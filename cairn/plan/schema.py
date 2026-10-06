"""The frozen step-graph schema and the defaults a derived graph is normalised against."""

import re
from typing import Any, NotRequired, TypedDict, cast

from cairn.bounds import nonnegative_integer, positive_integer

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

# `answered` is an edge the author supplied in the authoring conversation, in answer to a
# dependency question. Its evidence is the author's own words rather than a quotation, so it
# exists only where a resolved question on the same edge says so.
ANSWERED_ORIGIN = "answered"
DEP_ORIGINS: tuple[str, ...] = ("declared", "derived", ANSWERED_ORIGIN)

# A command that always exits zero reads as verified in the report while asserting
# nothing, which is worse than the declared absence its author could have chosen instead.
_UNASSERTABLE: tuple[str, ...] = ("true", ":", "exit 0")


def cannot_fail(command: str) -> bool:
    """Whether a verify command could never report the end state missing."""
    return not command.strip() or command.strip() in _UNASSERTABLE

# What a human answered when shown a proposed assertion. A step carries an answer only
# once someone has been asked, so the field's absence is what "never asked" means — and
# a declined step is the only way a step becomes unverified. `authored` is distinct from
# `edited` because a command written where nothing was proposed edited no proposal, and
# counting it as one overstates how often the proposals are carrying their weight.
ASSERTION_OUTCOMES: tuple[str, ...] = ("accepted", "edited", "authored", "declined")

OMISSION_REASONS: tuple[str, ...] = ("deferred", "gated", "already_done", "out_of_scope")

UNJUSTIFIED_EDGE = "unjustified_edge"
MISSING_VERIFY = "missing_verify"
NON_CONVERGENT_TASK = "non_convergent_task"
AMBIGUOUS_DEPENDENCY = "ambiguous_dependency"
UNRESOLVED_REFERENCE = "unresolved_reference"
PLAN_GATED = "plan_gated"
QUESTION_KINDS: tuple[str, ...] = (
    UNJUSTIFIED_EDGE,
    MISSING_VERIFY,
    NON_CONVERGENT_TASK,
    AMBIGUOUS_DEPENDENCY,
    UNRESOLVED_REFERENCE,
    PLAN_GATED,
)

# The two kinds that are questions about one edge. Each names the edge's two ends — `step`
# depends on `dep` — so an answer can put the edge in the graph or take it out.
EDGE_KINDS: tuple[str, ...] = (UNJUSTIFIED_EDGE, AMBIGUOUS_DEPENDENCY)

# The kinds a derivation may attach a proposal to: a command for an unasserted end state,
# and a convergent restatement of a task that would duplicate on a resumed run.
PROPOSING_KINDS: tuple[str, ...] = (MISSING_VERIFY, NON_CONVERGENT_TASK)

# How a question other than `missing_verify` was closed. `accepted` adopts the reading
# proposed — the derivation's own, or the edge the question names; `edited` adopts the
# author's own text instead; `declined` refuses the reading and, where that is a waiver, says
# why. A `missing_verify` answer is recorded on the step's own `assertion` instead, because
# that is the fact it settles and a second record could disagree with it.
RESOLUTION_OUTCOMES: tuple[str, ...] = ("accepted", "edited", "declined")

# Which outcomes each kind admits. The contract permits a waiver only where the graph stays
# sound without the reading: a plan the author has not called live is never published, so
# `plan_gated` can only be accepted; an unresolved reference can be restated only on a step.
RESOLUTIONS_BY_KIND: dict[str, tuple[str, ...]] = {
    UNJUSTIFIED_EDGE: ("accepted", "declined"),
    AMBIGUOUS_DEPENDENCY: ("accepted", "declined"),
    NON_CONVERGENT_TASK: ("accepted", "edited", "declined"),
    UNRESOLVED_REFERENCE: ("edited", "declined"),
    PLAN_GATED: ("accepted",),
    MISSING_VERIFY: (),
}

# The hang guard: the one internal deadline that kills a session which has stopped making
# progress. It is Cairn's own constant, the same for every step, and no plan can set it. I7
# forbids an unbounded step and the engine's own default is none (01), so this is what keeps
# that guarantee. It sits above any observed session — a 35m agent step ran uninterrupted
# against the engine's absent default — because its job is to catch a hang, not to shorten
# work that is still going.
HANG_GUARD = 14400

# An agent step always names its model, because a session whose model came from the
# environment leaves a record that cannot say which model did the work. The plan document
# sets it; a plan that says nothing gets this one, which is the provider's own stable alias
# rather than a dated identifier, so the default does not go stale with a release.
AGENT_MODEL = "sonnet"

# Nothing is retried. A step that failed because the provider blinked and one that failed
# because the task is wrong are indistinguishable from outside, and a second session would
# run against a repository the first one already changed.
#
# A subscription limit is not a failure and is not retried either: the engine's retry policy
# is a static number in a file and cannot read the reset time a limit carries. The step
# itself holds instead — before a session, when the shared reading says a window is closed or
# closing, and after one, when a session meets the limit and is resumed once the window
# reopens ([headroom.py]). A hold longer than the step can wait ends it `quota_held`, naming
# the moment, and the committed marker means the re-run skips what already landed.
AGENT_RETRIES = 0
COMMAND_RETRIES = 0
RETRY_INTERVAL = 1

# Cairn's own subcommands — worktree setup, commit, prune, lock, and the merge's own
# assertion — plus the plan's verify assertions. Never retried: each is idempotent, and a
# second attempt would only contend with the first one's own lock.
SUPPORT_TIMEOUT = 600
SUPPORT_RETRIES = 0

# A step's assertion is the plan's own command, so its bound is the plan's to state, and a
# plan that says nothing gets the support bound. Measured: a whole-suite assertion took
# 844 s against this default, so a plan asserting with a slow suite has to say so.
VERIFY_TIMEOUT = SUPPORT_TIMEOUT

# A support step's bound has to cover waiting for the git write mutex and then doing the
# git work, and still leave room to write a report. These three are stated together
# because that sum is the whole of the relation; separately they would drift until a
# jammed mutex was killed by the engine with nothing recorded.
GIT_TIMEOUT = 240
MUTEX_WAIT = 300
REPORT_HEADROOM = SUPPORT_TIMEOUT - MUTEX_WAIT - GIT_TIMEOUT

# Landing a wave is the one step that does both jobs: it may open a coding-agent session,
# because a conflict is a question about intent no command can answer, and it does the git
# work of a support step on either side of that. So its bound is the sum rather than the
# session alone — at `HANG_GUARD` the mutex wait and the merge in front of the session would
# come out of the session's own deadline, and the engine's kill would land mid-resolution,
# leaving exactly the unsettled tree the halt path exists to produce only deliberately.
# A wait owns the step's declared bound, so the engine's own kill must land strictly after
# it — otherwise the two fire together and `wait_timeout` never reaches a report. Every
# number derived from a wait step counts this, so the bound stated and the bound enforced
# are the same one.
WAIT_REPORT_GRACE = 15

# An agent step owns its declared bound the same way: the wrapper stops the session at
# `--timeout`, and the engine's own kill lands this much later. The grace is for the
# report, not the work — it covers stopping the provider, one resume that asks the session
# for the account it owes, and the write of the report the engine's kill would otherwise
# erase ([22 B]). Measured: a session that has committed its work answers that in a turn,
# and the alternative was a step with four commits and a passing assertion recorded as one
# that never ran.
AGENT_REPORT_GRACE = 180
# How much of the grace the resume may take. The remainder is headroom for stopping the
# provider and writing the report — the two things that must happen before the engine's
# bound, whatever the resumed session does.
AGENT_RESUME_MARGIN = 30

# How long an agent step may spend held at the subscription's allowance, on top of the hang
# guard it may spend working. Long enough to wait out a whole 5-hour window and the backed-off
# re-measurements behind it; a hold that would end later — a weekly window days away — ends
# the step `quota_held` instead of sleeping a worker for days. The engine's bound grows by it
# exactly as it grows by the report grace, so the run's maximum and the lock's reclaim window
# stay derivable from the graph ([headroom.py]).
QUOTA_WAIT = 21600

# How full a window may be before a session is not started into it. One figure per window,
# because 0.95 of a 5-hour window reopens within hours and 0.95 of a weekly one may not
# reopen for days; both start at the same bet until a measurement says otherwise.
HOLD_THRESHOLDS: dict[str, float] = {
    "five_hour": 0.95,
    "seven_day": 0.95,
    "seven_day_opus": 0.95,
    "seven_day_sonnet": 0.95,
}

# The engine's bound on an agent step: the work, the longest hold, and the report after
# them. The hold lives inside the step's own body, so the engine's kill lands after both and
# still the grace later.
AGENT_TIMEOUT = HANG_GUARD + QUOTA_WAIT + AGENT_REPORT_GRACE

# A merge resolver is an agent role like any other, so it names its model and runs under the
# same hang guard and the same hold at the allowance. Its engine step also leaves the same
# report grace as an ordinary agent session after the internal deadline.
MERGE_MODEL = AGENT_MODEL
MERGE_TIMEOUT = MUTEX_WAIT + GIT_TIMEOUT + HANG_GUARD + QUOTA_WAIT + AGENT_REPORT_GRACE
MERGE_RETRIES = 0

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
# A step that declares `remediate` gains two nodes between its assertion and its marker:
# one session that may fix what the assertion found, and the same assertion again.
REMEDY_PREFIX = "remedy_"
RECHECK_PREFIX = "recheck_"
# `work_` is not reserved: a node name is `<role>_<subject>` and the role is the text before
# the first underscore, so a step called `work_config` yields `work_work_config` and still
# round-trips. The three below are reserved because a step taking one of those names would
# collide with the node another step's name derives.
RESERVED_ID_PREFIXES: tuple[str, ...] = (
    VERIFY_PREFIX,
    MARK_PREFIX,
    MERGE_PREFIX,
    REMEDY_PREFIX,
    RECHECK_PREFIX,
)

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


def default_retries(kind: str) -> int:
    return AGENT_RETRIES if kind.startswith(AGENT_FAMILY) else COMMAND_RETRIES


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
    verify_timeout: int
    retries: int
    model: str | None
    remediate: bool


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


class Resolution(TypedDict):
    """One human's answer to one question, kept beside the question it closes."""

    outcome: str
    # The reading adopted: the proposal on an accept, the author's own text on an edit.
    reading: str | None
    # Why — required wherever the answer is the author's word rather than a reading: a
    # decline, an accepted edge, a plan called live.
    reason: str | None


class Question(TypedDict):
    kind: str
    step: str | None
    # The other end of the edge an edge question is about: `step` depends on `dep`.
    dep: str | None
    question: str
    evidence: str | None
    # The derivation's own reading, resting on the sentence `evidence` quotes: the command it
    # would write for an unasserted end state, or the convergent restatement of a task. Only
    # the agent that read the plan may write one; code afterwards checks the quote, never
    # the reading.
    proposed: str | None
    resolution: Resolution | None


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
    # The bound is judged here, before a default touches it: an integer that is not
    # positive is refused as the value it is rather than crashing the normalisation ([25]).
    "verify_timeout": {
        "type": int,
        "default": VERIFY_TIMEOUT,
        "check": positive_integer,
    },
    "retries": {
        "type": int,
        "default_from": "kind",
        "nullable": True,
        "check": nonnegative_integer,
    },
    # Null on a command step, which opens no session; always resolved on an agent step,
    # whose record could not otherwise say which model did the work.
    "model": {"type": str, "default_from": "kind", "nullable": True},
    # One session, after an assertion that ran and exited nonzero, resuming the step's
    # own session to fix what it found; then the same assertion again. Never after an
    # assertion a signal ended, which decided nothing a session could fix.
    "remediate": {"type": bool, "default": False},
}

ASSERTION_FIELDS: Spec = {
    "outcome": {"type": str, "enum": ASSERTION_OUTCOMES, "required": True},
    # What Cairn proposed, kept whatever the answer was: a declined proposal is what the
    # report shows beside an unverified step, and an adopted one is what tells an edit
    # from a plain accept.
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
    "dep": {"type": str, "default": None, "nullable": True},
    "question": {"type": str, "required": True},
    "evidence": {"type": str, "default": None, "nullable": True},
    "proposed": {"type": str, "default": None, "nullable": True},
    # Absent until the author has answered. Publication refuses a graph carrying one.
    "resolution": {"type": dict, "default": None, "nullable": True},
}

RESOLUTION_FIELDS: Spec = {
    "outcome": {"type": str, "enum": RESOLUTION_OUTCOMES, "required": True},
    "reading": {"type": str, "default": None, "nullable": True},
    "reason": {"type": str, "default": None, "nullable": True},
}

GRAPH_FIELDS: Spec = {
    # Required, never defaulted: a document that does not say which schema it speaks is not
    # silently read as this one. There is no earlier version Cairn migrates from.
    "cairn_graph_version": {"type": int, "required": True},
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
        if expected is int and isinstance(value, bool):
            errors.append(f"{where}.{name}: expected {expected.__name__}, found bool")
            continue
        if not isinstance(value, expected):
            errors.append(
                f"{where}.{name}: expected {expected.__name__}, found {type(value).__name__}"
            )
            continue
        if "enum" in rule and value not in rule["enum"]:
            allowed = ", ".join(rule["enum"])
            errors.append(f"{where}.{name}: {value!r} is not one of {allowed}")
        check = rule.get("check")
        if check is not None and not check(value):
            errors.append(f"{where}.{name}: {value!r} is not a valid value")
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
            if kind == COMMAND_KIND and step_fields.get("model") is not None:
                errors.append(
                    f"{where}: command kind must not carry 'model' — it opens no agent "
                    "session for a model to do the work of"
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
            _check_question(cast(dict[str, Any], question), where, errors)


def _check_question(fields: dict[str, Any], where: str, errors: list[str]) -> None:
    """A question's shape, and the shape of the answer it carries, agree with its kind.

    Only what can be read off the question itself is judged here. Whether the graph's facts
    say what the answer said is the validator's, because that needs the steps resolved.
    """
    kind = fields.get("kind")
    if kind not in QUESTION_KINDS:
        return
    if fields.get("proposed") is not None and kind not in PROPOSING_KINDS:
        errors.append(
            f"{where}: only a {' or '.join(PROPOSING_KINDS)} question can carry a proposed "
            "reading — on any other question the field answers nothing"
        )
    if kind in EDGE_KINDS and (fields.get("step") is None or fields.get("dep") is None):
        errors.append(
            f"{where}: a {kind} question is about one edge, so it names both ends — "
            "'step' and the 'dep' it would depend on"
        )
    if kind not in EDGE_KINDS and fields.get("dep") is not None:
        errors.append(f"{where}: only an edge question names a 'dep'")
    if kind in (MISSING_VERIFY, NON_CONVERGENT_TASK) and fields.get("step") is None:
        errors.append(f"{where}: a {kind} question is about one step, so it names it")

    resolution: Any = fields.get("resolution")
    if resolution is None:
        return
    found = len(errors)
    _check_fields(resolution, RESOLUTION_FIELDS, f"{where}.resolution", errors)
    # A field that failed its type check is never dereferenced: `.strip()` on a numeric
    # reason would raise where the contract promises a verdict.
    if len(errors) > found or not isinstance(resolution, dict):
        return
    answer = cast(dict[str, Any], resolution)
    outcome = answer.get("outcome")
    if outcome not in RESOLUTION_OUTCOMES:
        return
    allowed = RESOLUTIONS_BY_KIND[kind]
    if kind == MISSING_VERIFY:
        errors.append(
            f"{where}: a missing_verify question is answered on its step's 'assertion', and "
            "the answer clears the question — it never carries a resolution of its own"
        )
        return
    if outcome not in allowed:
        errors.append(
            f"{where}.resolution: a {kind} question cannot be {outcome}; it admits "
            + ", ".join(allowed)
        )
        return
    reading = answer.get("reading")
    reason = (answer.get("reason") or "").strip()
    proposed = fields.get("proposed")
    if outcome == "accepted":
        if kind in PROPOSING_KINDS:
            if proposed is None:
                errors.append(
                    f"{where}.resolution: nothing was proposed, so nothing can be accepted "
                    "— edit it, or decline it and say why"
                )
            elif reading != proposed:
                errors.append(
                    f"{where}.resolution: an accepted reading is the one that was proposed"
                )
        elif reading is not None:
            errors.append(
                f"{where}.resolution: a {kind} question proposes no reading, so an accept "
                "records none"
            )
        if kind not in PROPOSING_KINDS and not reason:
            errors.append(
                f"{where}.resolution: accepting a {kind} question is the author's word, so "
                "it says why"
            )
    elif outcome == "edited":
        if not (reading or "").strip():
            errors.append(f"{where}.resolution: an edit records the author's own reading")
        elif reading == proposed:
            errors.append(
                f"{where}.resolution: an edited reading must differ from the proposal — "
                "that is an accept"
            )
        if fields.get("step") is None:
            errors.append(f"{where}.resolution: an edit restates a step, so it names one")
    else:
        if reading is not None:
            errors.append(f"{where}.resolution: a declined reading adopts nothing")
        if not reason:
            errors.append(f"{where}.resolution: a decline must say why")


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
        if step.get("retries") is None:
            step["retries"] = default_retries(step["kind"])
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
        if question["resolution"] is not None:
            resolution = _copy(cast(dict[str, Any], question["resolution"]))
            _apply_defaults(resolution, RESOLUTION_FIELDS)
            question["resolution"] = cast(Resolution, resolution)
        questions.append(cast(Question, question))

    return {
        "cairn_graph_version": raw["cairn_graph_version"],
        "plan": cast(Plan, plan),
        "steps": steps,
        "omissions": omissions,
        "questions": questions,
    }
