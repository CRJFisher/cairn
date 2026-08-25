"""The one path a run starts from, and the address it hands back.

Every other trigger surface belongs to the engine — its start dialog, a cron firing, a
webhook — and Cairn owns none of them ([docs/triggers.md]). This is the skill's, and it is
the only one in Cairn's own code that can begin a paid run. It accepts an `Authorisation`
and nothing else, so a run has to have been offered, priced and accepted before this module
has anything to act on.

**It composes no parameter it was not asked for.** The repository is the one the definition
was authored for, established before the offer was made ([resolve.py]), so nothing here
retargets a workflow — that is re-authoring, and varying the parameter instead writes the
run's whole record into the wrong repository ([cairn/parameters.py]).

**The view is a link, not a rendering.** The address comes from `layout.view_url`, the same
composer the run record uses, so there is one spelling of where a run can be watched.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from cairn.enginehome import ENGINE_BINARY
from cairn.layout import check_run_id, view_url
from cairn.skill.consent import Authorisation, refuse_uncarriable
from cairn.workflow.gate import EngineUnavailable, assert_pinned
from cairn.workflow.schema import OCCASION_PARAM, PARENT_BRANCH_PARAM, WORKFLOW_SUFFIX

# What actually launches the engine. A seam rather than a mock: the gate that proves no
# eval case starts a run substitutes a recorder here, so the assertion is about the argv
# this module composed rather than about a patched name somewhere else.
EngineRunner = Callable[[Sequence[str]], int]


class Started(NamedTuple):
    run_id: str
    dag_name: str
    view: str
    command: tuple[str, ...]
    exit_code: int


def refuse_unusable_engine() -> None:
    """Halt before an offer is spent if the engine could not run the plan anyway.

    Asked here rather than inside `start`, because an acceptance is consumed the moment the
    offer's marker is claimed and a machine carrying the wrong engine is a cause a person
    can clear. Making them answer for the run twice, for a refusal that started nothing, is
    the one thing the single-use rule must not be allowed to cost.
    """
    assert_pinned()


def dag_name(workflow: Path) -> str:
    """The engine's name for a definition, which is its filename and nothing else.

    The same value the view's URL and any webhook endpoint are keyed on ([docs/triggers.md]),
    so it is derived here rather than carried, and a definition renamed on disk is a
    different DAG to the engine whatever Cairn recorded.
    """
    return workflow.name.removesuffix(WORKFLOW_SUFFIX)


def start_command(authorisation: Authorisation, run_id: str) -> tuple[str, ...]:
    """The engine invocation this run is, spelled so a person can read or repeat it.

    **Every value here comes from the authorisation**, so the run that happens is the run
    that was priced and accepted. A parameter settled after the offer would be a term of the
    agreement nobody agreed to — the branch most of all, since it is what verified work is
    merged into.

    Never `dagu retry`. Re-running a plan is the whole recovery story (I4), and a continued
    occasion is what makes it cheap — so a recovery is an ordinary start carrying the
    occasion it continues, and there is no second recovery path to keep correct.
    """
    check_run_id(run_id)
    parameters = [f"{PARENT_BRANCH_PARAM}={authorisation.parent_branch}"]
    if authorisation.occasion is not None:
        parameters.append(f"{OCCASION_PARAM}={authorisation.occasion}")
    # Measured against Dagu 2.11.0, `--params` takes `P1=foo P2=bar` and splits on
    # whitespace, so a value holding a space arrives as two parameters. `make_offer` refuses
    # such a value when the offer is priced; this is the same rule where it can no longer
    # cost anybody their acceptance.
    for parameter in parameters:
        refuse_uncarriable(parameter)
    return (
        ENGINE_BINARY,
        "start",
        "--run-id",
        run_id,
        "--params",
        " ".join(parameters),
        authorisation.workflow,
    )


def start(
    authorisation: Authorisation, run_id: str, *, runner: EngineRunner | None = None
) -> Started:
    """Begin the run this authorisation bought, and say where it can be watched.

    The engine's exit status is carried rather than interpreted: a run with exclusions is
    the case Cairn does not trust the engine about, and the verdict is the record's to
    derive by walking every node ([docs/run-model.md]). What this returns is that a run
    started and where it is, never how it went.

    The engine is checked before the authorisation is spent rather than here — see
    `refuse_unusable_engine` — so a machine that cannot run the plan does not cost a person
    their acceptance.
    """
    command = start_command(authorisation, run_id)
    invoke: EngineRunner = subprocess.call if runner is None else runner
    exit_code = invoke(command)
    name = dag_name(Path(authorisation.workflow))
    return Started(
        run_id=run_id,
        dag_name=name,
        view=view_url(name, run_id),
        command=command,
        exit_code=exit_code,
    )


__all__ = [
    "EngineRunner",
    "EngineUnavailable",
    "Started",
    "dag_name",
    "refuse_unusable_engine",
    "start",
    "start_command",
]
