"""The engine dialect Cairn emits, stated once.

A workflow is a `Workflow` before it is bytes, and `serialise` is the only thing that turns
one into text. The text is JSON, written into a `.yaml` file, because JSON is a subset of
YAML and the engine loads it — the same spelling the engine suites have always used. Two
properties follow, and both are the reason:

**The preflight can re-read exactly what the engine will read.** There is no YAML parser in
the standard library, so a hand-written block-YAML writer would need a hand-written reader
to check its own output, and the check would then measure the reader rather than the file.
`json.loads` is exact, total and already here.

**Quoting stops being a rule and becomes a property.** An unquoted `false` is rejected at
load ([01]); a JSON string can never render as a bare `false`, whatever it holds. The same
closes the engine's silent retyping of scalars, because JSON carries the type.

The cost is that a person reading the file in the engine's own view reads JSON. That is
accepted: the file is generated, never hand-maintained, and an edit to it is a divergence
[workflow.md] rather than a workflow.

## Where a parameter may stand

Measured against Dagu 2.11.0, a `${...}` reference behaves differently in each position it
can occupy, and only one of them is safe:

| Position                   | With a value holding a space | With a value holding `$(...)` |
| -------------------------- | ---------------------------- | ----------------------------- |
| `working_dir:`             | one path, correctly          | inert — it is never a shell   |
| `run:`, bare               | **split into three words**   | inert                         |
| `run:`, double-quoted      | one word, correctly          | **the substitution executes** |
| `run:`, single-quoted      | **not substituted at all**   | inert                         |

Single quotes are what `shlex.quote` produces, so a reference routed through the emitters'
own joiner would arrive as its own literal text. Bare loses any path containing a space.
Double quotes run whatever the value holds — and a parameter is an editable field at
trigger time ([03]), so that is a command-injection surface rather than a corner case.

So **a parameter reference stands in `working_dir:` and nowhere else**. Every other
per-target value reaches a step through the environment, because a declared parameter is
exported into every step, precondition and lifecycle handler.

One other reference does stand elsewhere: `${<id>.exit_code}`, in the precondition of the
marker a verify gate protects. The engine resolves that form itself, before any shell and
regardless of quoting, so none of the hazards above reach it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

# The pin. The DAG format carries no version of its own ([01]), so the pinned engine is the
# installed binary and nothing else, and the comparison is exact: a range would claim
# knowledge about versions nothing here has run against.
ENGINE_VERSION = "2.11.0"

# Moves whenever the emitted shape changes, so a stamp written by an older generator is
# recognisable as one. It is not a release version; doc 16 owns that.
#
# Two things read it. `describe` names a workflow an earlier generator wrote, rather than
# reporting it unmodified — which it is, of a shape Cairn no longer emits. And
# `scripts/regenerate_workflows` refuses to record a moved shape under a version that already
# described another one, which is what keeps this number honest: the recorded files in
# `fixtures/workflows/` go green either way once they are rewritten [workflow.md].
# 2: every emitted file declares `CAIRN_RUNS_DIR`, so a step composes its own report path
# from a root the generator chose rather than from wherever the engine put its log
# ([run-model.md]). A workflow written by 1 carries no such declaration and every one of its
# steps would fail to resolve its identity, which is exactly the shape difference this
# number exists to make legible.
# 3: `CAIRN_OCCASION` is declared empty and the run mints its own, where 2 baked one in at
# authoring; and every file states `catchup_window` and `overlap_policy` rather than
# inheriting them. A workflow written by 2 carries a fixed occasion, so every firing after
# the first finds a fresh marker for each `run`- and period-scoped step, skips it, and
# reports a clean success having done nothing ([triggers.md]) — a defect a person needs
# naming rather than a shape difference they can ignore.
# 4: every agent body carries `--model` and `--max-budget-usd`, so the definition states
# every session's model and price and an offer can be composed from the file alone. A
# workflow written by 3 opens sessions bounded only by their timeout, with the model left
# to the environment — which is why the preflight refuses its agent bodies as unbounded
# rather than running them ([17.3]).
GENERATOR_VERSION = 4

# The one execution type. The alternative reading serialises the graph, which is the defect
# Cairn exists to avoid — and `type: chain` without `depends` validates clean and silently
# runs one step at a time, so only this check catches it.
GRAPH_TYPE = "graph"

# What a caller may vary between runs of one workflow, and the whole of it. Each is exposed
# as an editable field by the engine's own start dialog ([03]) and exported into every
# step's environment.
REPOSITORY_PARAM = "CAIRN_REPOSITORY"
PARENT_BRANCH_PARAM = "CAIRN_PARENT_BRANCH"
OCCASION_PARAM = "CAIRN_OCCASION"
PARAMETERS: tuple[str, ...] = (REPOSITORY_PARAM, PARENT_BRANCH_PARAM, OCCASION_PARAM)

# The file's extension. It holds JSON; the engine reads it as the YAML it is.
WORKFLOW_SUFFIX = ".yaml"
STAMP_SUFFIX = ".stamp.json"

# The provenance stamp rides in the file as well as in Cairn's state. A new top-level key
# is rejected at load and so is an unknown key on a step, but `labels` takes arbitrary
# entries and survives both `dagu validate` and `dagu dry` — measured. Carrying the stamp in
# the file is what answers the two cases a state-only record cannot see: a workflow deleted
# and re-created, and one replaced wholesale, both of which arrive with no stamp at all.
LABEL_PREFIX = "cairn_"
LABEL_PLAN = "cairn_plan"
LABEL_GENERATOR = "cairn_generator"
LABEL_ENGINE = "cairn_engine"
LABEL_GRAPH_DIGEST = "cairn_graph_sha256"
LABEL_BODY_DIGEST = "cairn_body_sha256"

REFERENCE = re.compile(r"\$\{([^}]*)\}")


class RetryPolicy(TypedDict):
    """Always spelled in full: `interval_sec` is required whenever the policy is present."""

    limit: int
    interval_sec: int


class ContinueOn(TypedDict, total=False):
    """The two spellings Cairn emits.

    `mark_success` and `output` are absent on purpose rather than by omission. One rewrites
    a failed step as succeeded on disk and in the API, the other routes on stdout text; a
    type that cannot spell them is a stronger guarantee than a rule that forbids them, and
    the preflight still refuses both in case a document arrives from elsewhere.
    """

    failure: bool
    skipped: bool


class Precondition(TypedDict):
    """A bare condition, executed as a command and gating its step on the exit status.

    It carries no `expected`: with one, the engine compares output text instead of running
    the command for its status, which is not what any of Cairn's gates mean.
    """

    condition: str


# One node as the engine reads it. Left open rather than closed, because the emitters build
# a step key by key and the keys a role carries differ by role; what is closed is the
# document around them, and the preflight checks every key of every step against the rules
# rather than against a type.
#
# Two keys are absent from everything Cairn emits and the preflight refuses both: `action`
# and `with`, whose values the engine coerces by YAML type behind Cairn's back.
EngineStep = dict[str, Any]


# Replay is off, and the empty string is the only spelling that says so. Measured against
# Dagu 2.11.0: `catchup_window: "0s"` and `"0"` are both rejected — "duration must be
# positive" — while `""` passes both engine checks, and the engine's own shipped comment
# reads "Empty = no catchup (missed runs discarded)". Omitting the field inherits whatever
# the machine's `base.yaml` holds, and this machine's holds `"6h"`: every cron slot missed
# while the machine slept would replay, up to a thousand of them, and for Cairn a replayed
# slot is a paid agent session against a git repository.
CATCHUP_DISABLED = ""

# What the engine does with a firing that arrives while this workflow is still running.
# `all` queues paid work against a scheduler nobody may have started and `latest` discards
# all but one; `skip` drops it. None of the three is visible in a record, so the honest
# refusal is Cairn's own run lock naming the holder ([triggers.md]) — this states the least
# costly of the three rather than inheriting whichever the machine prefers.
OVERLAP_SKIP = "skip"

# Every root key Cairn emits, and the whole of it. A file carrying anything else is refused
# rather than run: the engine accepts keys that suppress a firing on its own verdict, mail a
# report from its own verdict, or replay missed work, and a rule per key would have to be
# kept current with an engine Cairn does not control.
ROOT_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "schedule",
        "max_active_steps",
        "retry_policy",
        "catchup_window",
        "overlap_policy",
        "labels",
        "params",
        "env",
        "handler_on",
        "steps",
    }
)


class Workflow(TypedDict):
    """A whole engine definition, before it is bytes.

    It has no `name`. The validator rejects any file that declares one while `dagu start`
    accepts it ([01]), so the name comes from the filename — and a type with no such key
    cannot emit one by accident.

    `schedule` is the one optional key, because a plan is a one-shot run until somebody asks
    for a recurring one. Every other field is stated on every file, including the two that
    exist only to close an inherited default.
    """

    type: str
    schedule: NotRequired[str]
    max_active_steps: int
    retry_policy: RetryPolicy
    catchup_window: str
    overlap_policy: str
    labels: dict[str, str]
    params: list[dict[str, str]]
    env: list[dict[str, str]]
    handler_on: dict[str, EngineStep]
    steps: list[EngineStep]


def reference(name: str) -> str:
    """The engine's spelling of a parameter, for the one position that may hold it."""
    return f"${{{name}}}"


def references_in(text: str) -> list[str]:
    """Every `${...}` name in one value, in the order they appear."""
    return REFERENCE.findall(text)


def _entries(document: Any, field: str) -> list[Any]:
    if not isinstance(document, dict):
        return []
    value: Any = cast(dict[str, Any], document).get(field)
    return cast(list[Any], value) if isinstance(value, list) else []


def resolvable_names(document: Any) -> frozenset[str]:
    """Every name a reference in this document could resolve to.

    Declared parameters and environment entries resolve by name; a step resolves as
    `<id>.exit_code` only where it declares an explicit `id`, because a step without one is
    unreachable and its reference expands to nothing at all ([01]).

    It takes any parsed document rather than a `Workflow`, because the preflight reads files
    Cairn may not have written and a missing key there is a refusal to report, not a crash.
    """
    names: set[str] = set()
    for field in ("params", "env"):
        for entry in _entries(document, field):
            if isinstance(entry, dict):
                names.update(cast(dict[str, Any], entry))
    for step in _entries(document, "steps"):
        if isinstance(step, dict):
            handle = cast(dict[str, Any], step).get("id")
            if isinstance(handle, str):
                names.add(f"{handle}.exit_code")
    return frozenset(names)


# How every emitted body begins, and the two words that make one a paid agent session. The
# writer and the readers share these: a body's shape is the emitted document's, so the
# module that types the document owns it rather than each reader restating it.
CAIRN_INVOCATION = ("python3", "-m", "cairn")
AGENT_SUBCOMMAND = ("agent", "run")


def is_agent_body(body: str) -> bool:
    """Whether this step body starts a paid agent session."""
    argv = split_argv(body)
    prefix = len(CAIRN_INVOCATION)
    return (
        argv[:prefix] == CAIRN_INVOCATION
        and argv[prefix : prefix + len(AGENT_SUBCOMMAND)] == AGENT_SUBCOMMAND
    )


def declared_parameter(document: Any, name: str) -> str | None:
    """One declared parameter's value, or `None` where the document declares none.

    The one reader, because there are several callers and a definition must not answer them
    differently: an emptiness policy belongs to whoever asked, so the raw value comes back
    including the empty string, and `None` means the key is genuinely absent.
    """
    for entry in _entries(document, "params"):
        if isinstance(entry, dict):
            value = cast(dict[str, Any], entry).get(name)
            if isinstance(value, str):
                return value
    return None


def split_argv(body: str) -> tuple[str, ...]:
    """A step body as argv, or empty where it is not argv at all.

    Every body the emitter writes is built with `shlex.quote`, so `shlex.split` recovers it
    exactly — but a reader here is looking at a file on disk that may have been hand-edited,
    and an unbalanced quote is a hand edit rather than a crash. An unreadable body is one
    nothing can be concluded about, which is what the empty tuple says.
    """
    try:
        return tuple(shlex.split(body))
    except ValueError:
        return ()


def serialise(document: Workflow) -> str:
    """The bytes the engine loads.

    `sort_keys` is off: the declaration order of the types above is a reading order, and a
    generated file that changes is read as a diff.
    """
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def body_digest(document: Any) -> str:
    """The hash of everything but the stamp, so the stamp can describe its own file.

    A digest over the whole document could never be written into it. Stripping Cairn's own
    labels first leaves a value that is stable across stamping, which is what lets the file
    carry a claim about itself that the next authoring can check.
    """
    body = {key: value for key, value in document.items() if key != "labels"}
    labels = document.get("labels")
    if isinstance(labels, dict):
        kept = {
            key: value
            for key, value in cast(dict[str, Any], labels).items()
            if not key.startswith(LABEL_PREFIX)
        }
        if kept:
            body["labels"] = kept
    canonical = json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read(path: Path) -> Any:
    """Re-read a workflow as the engine will parse it.

    The preflight checks this rather than the structure that produced it, so a fault in
    serialisation is inside the blast radius rather than behind it.
    """
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "AGENT_SUBCOMMAND",
    "CAIRN_INVOCATION",
    "CATCHUP_DISABLED",
    "ENGINE_VERSION",
    "GENERATOR_VERSION",
    "GRAPH_TYPE",
    "LABEL_BODY_DIGEST",
    "LABEL_ENGINE",
    "LABEL_GENERATOR",
    "LABEL_GRAPH_DIGEST",
    "LABEL_PLAN",
    "LABEL_PREFIX",
    "OCCASION_PARAM",
    "OVERLAP_SKIP",
    "PARAMETERS",
    "PARENT_BRANCH_PARAM",
    "REFERENCE",
    "REPOSITORY_PARAM",
    "ROOT_KEYS",
    "STAMP_SUFFIX",
    "WORKFLOW_SUFFIX",
    "ContinueOn",
    "EngineStep",
    "Precondition",
    "RetryPolicy",
    "Workflow",
    "body_digest",
    "declared_parameter",
    "is_agent_body",
    "read",
    "reference",
    "references_in",
    "resolvable_names",
    "serialise",
    "split_argv",
]
