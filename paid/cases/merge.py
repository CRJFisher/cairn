"""Case 1: a real session resolves a real conflict, and the proof passes over its work.

This ran once, by accident, while doc 10 was being built, and passed. It is put back here
deliberately rather than rediscovered: two branches rewrite the same line, the first slot
lands with no session at all, the second meets the conflict and hands it to a real model,
and `merge verify` proves the result in its own process.

**The chain is the emitted one, with one token changed.** The free suite drives the same
nodes with `--provider nothing-resolves-this`, because a real session would make the
assertion cost money and depend on what a model decided that day. Here the provider is left
as the emitters wrote it, which is the whole of the difference and the only line that decides
whether the run spends anything.

What it proves that nothing else does: that the prompt in `docs/merge-step.md` produces a
correct resolution from a real model, that the session *completes* the merge rather than
leaving it, and that the four proofs pass over work an agent did. The stubbed suite proves
the routing around the session. This proves the session.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from cairn.gitio import git, is_ancestor, runs_root
from cairn.layout import reports_directory
from cairn.locks import unresolved_merge
from cairn.marker import mint_occasion
from cairn.merge import CONFLICT_MARKERS
from cairn.plan.schema import normalise
from cairn.topology import derive
from cairn.verify import mark_name
from cairn.workflow.build import envelope
from cairn.workflow.schema import OCCASION_PARAM, PARENT_BRANCH_PARAM, EngineStep
from paid.engine import (
    bound_body,
    emitted_node,
    start,
    unbounded_bodies,
    write_definition,
)
from paid.harness import Harness
from paid.measure import Measurement, Unit, ending_of
from paid.probes import PACKAGE_ROOT, build, commit_all
from paid.vocabulary import (
    CASE_MERGE,
    CAUSE_COMMAND_FAILED,
    CAUSE_INTENT_LOST,
    CAUSE_MARKERS_LEFT,
    CAUSE_MERGE_ABANDONED,
    MEASUREMENT_RESOLUTION,
    ROLE_MERGE,
)

NAME = CASE_MERGE
CEILING_USD = 1.50
MEASURED_USD = 0.17

ENGINE_SECONDS = 900.0
PARENT = "main"
FIXTURE = "fan-out"
SHARED = "shared.txt"

# The two intentions the resolution has to keep, and the branches that carry them: two steps
# of one plan, each registering its own reader in the same place.
#
# The content is meaningful on purpose. Measured with the placeholder pair doc 17 records —
# one / from-a / from-b / three — a real session read both sides, could not tell what either
# meant, and **declined to resolve**, leaving the merge exactly as it found it and asking for
# a human. That is the behaviour doc 10 asks for when intent is unknowable, so a fixture
# whose sides mean nothing measures a model's willingness to guess rather than its ability to
# keep both intentions.
SIDES: dict[str, str] = {
    "step/a": "theme = theme.toml",
    "step/b": "keymap = keys.toml",
}

BEFORE = "[readers]\n# each reader registers itself here\n[end]\n"

# The fan-out fixture's own step branches, replaced by the two this repository has. The free
# suite makes the same substitution at `tests/test_merge_step.py:1036`.
SUBSTITUTIONS: dict[str, str] = {
    "step/keymap_reader": "step/a",
    "step/theme_reader": "step/b",
}


def graph() -> Any:
    path = PACKAGE_ROOT / "fixtures" / "plans" / FIXTURE / "graph.json"
    return normalise(json.loads(path.read_text(encoding="utf-8")))


def chain(repository: Path, *, model: str, budget_usd: float) -> list[EngineStep]:
    """The emitted merge chain, with this repository's candidates and a bounded session.

    The nodes are the generator's, in the generator's order, depended one on the next so the
    engine runs the slots one at a time — which is what the merge step's whole design rests
    on.
    """
    plan = graph()
    fan = derive(plan, repository_root=repository, parent_branch=PARENT)
    steps = {step["id"]: step for step in plan["steps"]}
    emitted: list[EngineStep] = []
    for node in fan["nodes"]:
        if node["role"] not in ("merge", "verify") or node["step"] is not None:
            continue
        body = emitted_node(node, steps, fan["max_seconds"])
        body["working_dir"] = str(repository)
        run = str(body["run"])
        for fixture_branch, actual in SUBSTITUTIONS.items():
            run = run.replace(fixture_branch, actual)
        body["run"] = bound_body(run, model=model, budget_usd=budget_usd)
        body.pop("depends", None)
        if emitted:
            body["depends"] = [emitted[-1]["name"]]
        emitted.append(body)
    return emitted


def seed(repository: Path, run_id: str) -> None:
    """Two branches rewriting one line, and the gate report each slot lands on.

    A slot lands only what the gate recorded, so a chain without those reports would land
    nothing and prove nothing.
    """
    (repository / SHARED).write_text(BEFORE, encoding="utf-8")
    commit_all(repository, "shared")
    reports = reports_directory(runs_root(repository), run_id)
    reports.mkdir(parents=True, exist_ok=True)
    for branch, line in SIDES.items():
        step = branch.split("/", 1)[1]
        git(repository, ("checkout", "--quiet", "-b", branch, PARENT))
        (repository / SHARED).write_text(
            BEFORE.replace("# each reader registers itself here", line), encoding="utf-8"
        )
        commit_all(repository, f"work on {branch}")
        git(repository, ("checkout", "--quiet", PARENT))
        (reports / f"{mark_name(step)}.json").write_text(
            json.dumps(
                {
                    "step_id": step,
                    "run_id": run_id,
                    "status": "done",
                    "cause": None,
                    "summary": "recorded",
                    "needs_user_decision": False,
                }
            ),
            encoding="utf-8",
        )


RESOLVING_SLOT = "merge_w2_2"


def resolving_report(repository: Path, run_id: str) -> dict[str, Any]:
    """The slot that met the conflict, as it reported itself.

    Its receipts — the session's identity, its price, its turns — are the only place this
    case's cost exists: the chain writes step reports rather than a run record, because it
    is the emitted merge nodes alone rather than a whole topology.
    """
    path = reports_directory(runs_root(repository), run_id) / f"{RESOLVING_SLOT}.json"
    if not path.is_file():
        return {}
    try:
        report: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(report, dict):
        return {}
    detail: Any = cast(dict[str, Any], report).get("detail")
    return cast(dict[str, Any], detail) if isinstance(detail, dict) else {}


def kept_both_intentions(landed: str) -> bool:
    """Whether the resolution kept what each side meant, which no command can judge.

    Doc 10 says a resolution that is clean and semantically wrong is the one failure nothing
    catches. This is the cheapest honest test of it there is for this fixture: both sides
    wrote a distinct line, and a resolution that dropped one dropped that side's intent.
    """
    return all(intention in landed for intention in SIDES.values())


def judge(repository: Path, *, engine_exit: int, landed: str, resolved: bool) -> str | None:
    """The end state, proved against git rather than read off what the session said.

    A merge left conflicted and a merge nobody could start look identical in the landed file,
    so the two facts that separate them are asked first: an engine that did not finish, and a
    resolving slot that left no report. Asking them last would publish an engine that never
    ran as a model that gave up — the exact confusion doc 17 task 7 exists to prevent.
    """
    if not resolved:
        return CAUSE_COMMAND_FAILED
    if unresolved_merge(repository) is not None:
        return CAUSE_MERGE_ABANDONED
    if any(marker in landed for marker in CONFLICT_MARKERS):
        return CAUSE_MARKERS_LEFT
    if engine_exit != 0:
        return CAUSE_COMMAND_FAILED
    if not all(is_ancestor(repository, branch, PARENT) for branch in SIDES):
        return CAUSE_MERGE_ABANDONED
    if not kept_both_intentions(landed):
        return CAUSE_INTENT_LOST
    return None


def run(harness: Harness) -> None:
    with TemporaryDirectory(dir=str(harness.root)) as temporary:
        probe = build(Path(temporary), with_provider=True, with_plans=False)
        occasion = mint_occasion()
        run_id = mint_occasion()
        seed(probe.repository, run_id)
        steps = chain(
            probe.repository,
            model=harness.model_for(ROLE_MERGE),
            budget_usd=CEILING_USD,
        )
        document = envelope(
            steps,
            repository=str(probe.repository),
            parent_branch=PARENT,
            occasion=occasion,
            python_path=str(PACKAGE_ROOT),
            runs_root=str(runs_root(probe.repository)),
        )
        left = unbounded_bodies(document)
        if left:
            raise RuntimeError(f"an unbounded session reached the chain: {left[0]}")
        path = write_definition(document, Path(temporary) / "merge.yaml")
        began = time.monotonic()
        completed = start(
            path,
            run_id,
            cwd=probe.repository,
            variables=probe.variables,
            parameters={PARENT_BRANCH_PARAM: PARENT, OCCASION_PARAM: occasion},
            timeout=ENGINE_SECONDS,
        )
        landed = (probe.repository / SHARED).read_text(encoding="utf-8")
        receipts = resolving_report(probe.repository, run_id)
        # The engine opened this session; the ladder priced it and the ledger never saw it.
        harness.charge_engine(
            ROLE_MERGE, receipts.get("total_cost_usd"), ceiling_usd=CEILING_USD
        )
        cause = None
        cause = judge(
            probe.repository,
            engine_exit=completed.returncode,
            landed=landed,
            resolved=bool(receipts),
        )
        harness.record(
            Unit(
                case=NAME,
                unit=RESOLVING_SLOT,
                ending=ending_of(cause),
                cause=cause,
                seconds=round(time.monotonic() - began, 3),
                role=ROLE_MERGE,
                session_id=receipts.get("session_id"),
                cost_usd=receipts.get("total_cost_usd"),
                turns=receipts.get("turn_count"),
                model_resolved=receipts.get("model"),
                expected={"kept": sorted(SIDES.values()), "engine_exit": 0},
                observed={
                    "kept": [side for side in sorted(SIDES.values()) if side in landed],
                    "engine_exit": completed.returncode,
                    "lines": len(landed.splitlines()),
                },
                account=harness.scrub(landed),
                detail={
                    "conflicted_paths": [SHARED],
                    "resolution": landed.split(),
                    # What the engine said, kept because a merge that halted and a merge
                    # nobody could start look identical from the landed file alone.
                    "engine": harness.scrub(completed.stdout[-1200:]),
                    "engine_stderr": harness.scrub(completed.stderr[-600:]),
                },
            )
        )
        harness.measure(
            NAME, Measurement(MEASUREMENT_RESOLUTION, 1 if cause is None else 0, 1)
        )


__all__ = [
    "CEILING_USD",
    "MEASURED_USD",
    "NAME",
    "ceilings",
    "chain",
    "judge",
    "kept_both_intentions",
    "run",
]


def ceilings() -> list[float]:
    """A ceiling for every session this case may open, which is what the ladder prices.

    Two, not one: the chain has two landing slots and either can meet a conflict, so either
    can open a session. Pricing the one this fixture expects would leave the second unpriced
    on the day a fixture changed.
    """
    return [CEILING_USD] * 2
