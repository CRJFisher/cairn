"""Case 2: a real model reports success, and the gate excludes the step anyway.

Doc 08 calls the divergence between what a step says and what its assertion proves "the
empirical case for the whole design", and the free suite already drives that routing with a
stub. What a stub cannot do is decide, so this case puts a real session where the stub was.

**The session is not asked to lie.** It is given an honest, convergent task — write a note —
under a plan assertion the task never mentions: `test -f dist/manifest.json`. The session
truthfully reports `done`, the assertion truthfully fails, and the gate records
`Divergence(reported="done", asserted=False)`. The gate's input is identical to doc 08's
case, and **nothing about the expected end state depends on what the model decided** — which
is what makes a case driven by a real session fully deterministic.

It is excluded from the divergence-rate population for the same reason: the divergence here
is constructed, and counting a constructed one would be reporting the fixture rather than
the world.
"""

from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from cairn.gitio import runs_root
from cairn.marker import mint_occasion
from cairn.record.model import RunRecord, StepRecord
from cairn.record.vocabulary import OUTCOME_EXCLUDED, VERDICT_GREEN_WITH_EXCLUSIONS
from cairn.verify import VERIFY_FAILED
from cairn.workflow.schema import OCCASION_PARAM, PARENT_BRANCH_PARAM

from paid.engine import definition, record_of, start, steps_of, write_definition
from paid.harness import Harness
from paid.measure import Unit, ending_of
from paid.probes import PACKAGE_ROOT, PlanSource, build, graph_document, run_cairn
from paid.vocabulary import (
    CASE_DIFFERENTIATING,
    CAUSE_COMMAND_FAILED,
    CAUSE_FACT_UNEXPECTED,
    CAUSE_PROCEDURE_ABANDONED,
    CAUSE_RECORD_UNREADABLE,
    CAUSE_VERDICT_UNEXPECTED,
    ROLE_STEP,
)

NAME = CASE_DIFFERENTIATING
CEILING_USD = 1.00
MEASURED_USD = 0.22

PLAN_SLUG = "differentiating"
STEP_ID = "scratch_note"
ENGINE_SECONDS = 900.0

SOURCE = "README.md"
PLAN_BODY = """# Differentiating

## Steps

1. **Scratch note** — bring `notes/scratch.md` to a state where it holds one line naming
   this repository.
   Verify: `test -f dist/manifest.json`
"""

# The task and the assertion are about different things, and deliberately: the task is
# honest and convergent, and the plan asserts an end state it never mentions.
TASK = (
    "Bring `notes/scratch.md` to a state where it holds one line naming this repository."
)
ASSERTION = "test -f dist/manifest.json"


def graph() -> dict[str, Any]:
    return graph_document(
        slug=PLAN_SLUG,
        title="Differentiating",
        sources=(PlanSource(SOURCE, PLAN_BODY),),
        steps=[
            {
                "id": STEP_ID,
                "slug": "1. Scratch note",
                "title": "Scratch note",
                "task": TASK,
                "deps": [],
                "verify": ASSERTION,
                "assertion": None,
                "tools": None,
                "scope": "once",
                "reads": [],
                "retries": 0,
                "kind": "agent.claude",
                "timeout": 600,
            }
        ],
    )


def judge(record: RunRecord | None) -> str | None:
    """The end state, read off the run's own record — or the cause it missed it by."""
    steps = steps_of(record)
    if record is None or len(steps) != 1:
        return CAUSE_RECORD_UNREADABLE
    step = steps[0]
    divergence = step["divergence"]
    if divergence is None:
        # The gate closed with the two accounts agreeing, which means the assertion passed
        # over a step nobody asked to create `dist/manifest.json`, or the session was never
        # asked at all. Either is the tool's, not the model's.
        return CAUSE_FACT_UNEXPECTED
    if divergence["reported"] == "failed":
        return CAUSE_PROCEDURE_ABANDONED
    if divergence["asserted"] is not False:
        return CAUSE_FACT_UNEXPECTED
    if step["outcome"] != OUTCOME_EXCLUDED or step["cause"] != VERIFY_FAILED:
        return CAUSE_FACT_UNEXPECTED
    if record["verdict"] != VERDICT_GREEN_WITH_EXCLUSIONS:
        return CAUSE_VERDICT_UNEXPECTED
    return None


def run(harness: Harness) -> None:
    with TemporaryDirectory(dir=str(harness.root)) as temporary:
        probe = build(Path(temporary), with_provider=True, with_plans=False)
        occasion = mint_occasion()
        run_id = mint_occasion()
        document = definition(
            graph(),
            repository=probe.repository,
            parent_branch="main",
            occasion=occasion,
            python_path=str(PACKAGE_ROOT),
            runs_root=runs_root(probe.repository),
            model=harness.model_for(ROLE_STEP),
            budget_usd=CEILING_USD,
        )
        path = write_definition(document, Path(temporary) / f"{PLAN_SLUG}.yaml")
        began = time.monotonic()
        completed = start(
            path,
            run_id,
            cwd=probe.repository,
            variables=probe.variables,
            parameters={PARENT_BRANCH_PARAM: "main", OCCASION_PARAM: occasion},
            timeout=ENGINE_SECONDS,
        )
        built = run_cairn(
            "record",
            "build",
            "--run",
            run_id,
            "--repository",
            str(probe.repository),
            cwd=probe.repository,
            variables=probe.variables,
        )
        record = record_of(probe.repository, run_id)
        step = only_step(record)
        # The engine opened this session, so the ledger only meets it through the record.
        harness.charge_engine(
            ROLE_STEP, None if step is None else step["cost_usd"], ceiling_usd=CEILING_USD
        )
        cause = judge(record)
        if record is None:
            cause = CAUSE_COMMAND_FAILED
        harness.record(
            Unit(
                case=NAME,
                unit=STEP_ID,
                ending=ending_of(cause is None),
                cause=cause,
                seconds=round(time.monotonic() - began, 3),
                role=ROLE_STEP,
                session_id=None if step is None else step["session_id"],
                cost_usd=None if step is None else step["cost_usd"],
                turns=None if step is None else step["turns"],
                model_resolved=None if step is None else step["model"],
                expected={
                    "reported": "done",
                    "asserted": False,
                    "verdict": VERDICT_GREEN_WITH_EXCLUSIONS,
                },
                observed=_observed(record, step),
                account=harness.scrub("" if step is None else str(step["said"] or "")),
                detail={
                    "engine_exit": completed.returncode,
                    "record_build_exit": built.returncode,
                    "assertion": ASSERTION,
                    "constructed": True,
                    "engine": harness.scrub(completed.stdout[-1200:]),
                },
            )
        )


def only_step(record: RunRecord | None) -> StepRecord | None:
    steps = steps_of(record)
    return steps[0] if len(steps) == 1 else None


def _observed(record: RunRecord | None, step: StepRecord | None) -> dict[str, Any]:
    """The two accounts as the record kept them, plus the verdict they produced."""
    divergence = None if step is None else step["divergence"]
    return {
        "reported": None if divergence is None else divergence["reported"],
        "asserted": None if divergence is None else divergence["asserted"],
        "outcome": None if step is None else step["outcome"],
        "cause": None if step is None else step["cause"],
        "verdict": None if record is None else record["verdict"],
    }


def ceilings() -> list[float]:
    """A ceiling for every session this case may open, which is what the ladder prices."""
    return [CEILING_USD]


__all__ = [
    "CEILING_USD",
    "MEASURED_USD",
    "NAME",
    "ceilings",
    "graph",
    "judge",
    "run",
]
