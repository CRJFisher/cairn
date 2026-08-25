"""What a caller may vary between runs of one workflow, and what is refused.

A generated workflow declares three parameters, and the engine exposes each as an editable
field at trigger time ([03]) — so every value a step acts on arrives from outside anything
Cairn checked. The authoring-time path resolves and pins them; a trigger decides them
afterwards, and Cairn owns none of the four surfaces that do. Two of them vary a value — the
engine's start dialog and `dagu start --params` — and two run with whatever the file
declares, because a cron firing and a webhook have no override point at all. All four pass
through the run's first act.

**A definition is bound to the repository it was authored for**, and the parameter is what
lets a trigger vary the occasion and the branch rather than the target. The runs root is
resolved at authoring time and emitted into `env:`, where no trigger surface can move it, so
a repository varied away from the authored one is refused rather than served — see
`refuse_misfiled_records`.

The occasion is judged by `marker.resolve_occasion` rather than here, because minting one
and validating a supplied one are the same decision and splitting them would put two
statements of one rule where they could disagree.

**So the parameters are judged at the run's first act rather than at any trigger surface.**
`cairn lock acquire` is the one node every path passes through, and it runs before the
first worktree and before the first paid session. A refusal there is a failed node carrying
its reason — which is what the engine's own view draws, what `dagu start` exits on, and
what the run's record keeps.

## The divergence this exists to catch

Measured against Dagu 2.11.0. The emitter concatenates the repository parameter with the
worktrees suffix **as text**, because a parameter reference may stand in `working_dir:` and
nowhere else ([workflow.md]); `cairn worktree setup` derives the same directory from the
repository it is standing in, through `Path`, which normalises. The two agree for every
canonical spelling and disagree for at least one a person will type:

| Parameter          | Emitted `working_dir`                | What `worktree setup` creates    |
| ------------------ | ------------------------------------ | -------------------------------- |
| `/srv/product`     | `/srv/product.cairn-worktrees/p/s`   | `/srv/product.cairn-worktrees/p/s` |
| `/srv/product/`    | `/srv/product/.cairn-worktrees/p/s`  | `/srv/product.cairn-worktrees/p/s` |

The second row is a directory **inside the working tree**. The engine creates a missing
working directory rather than failing, so the step runs there against nothing, its branch
carries no work, and the wave's join, merge slots, proofs and prune all report success
having landed nothing — while the generated directory sits where the plan's own commit
step stages everything. Measured end to end: two commits and two files on the canonical
spelling, none on the other.

The check is therefore not a rule about slashes. It is the two derivations, run against the
value in hand, required to agree — so any spelling that would send the work somewhere the
setup did not create is refused, whichever spelling a future caller invents.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from cairn.core import CairnError
from cairn.gitio import CAIRN_STATE, common_directory, git
from cairn.layout import RUNS_DIRECTORY, RUNS_ROOT_ENV
from cairn.topology import WORKTREES_SUFFIX, worktrees_parent
from cairn.workflow.schema import PARENT_BRANCH_PARAM, REPOSITORY_PARAM


def parameter(name: str, environ: Mapping[str, str] | None = None) -> str:
    """Read one of the workflow's declared parameters out of the step's environment.

    A parameter cannot travel in a step's argv. Measured against Dagu 2.11.0, a `${...}`
    reference is left inert by the single quotes every emitted body is built with, splits on
    whitespace if written bare, and executes whatever the value holds if written in double
    quotes — and a parameter is an editable field at trigger time ([03]). The engine exports
    every declared parameter into the step's environment instead, which is the one path that
    is neither quoted nor re-evaluated ([workflow/schema.py]).
    """
    values = os.environ if environ is None else environ
    value = values.get(name)
    if not value:
        raise CairnError(
            "invalid_arguments",
            f"{name} is not set. The generated workflow declares it as a parameter and the "
            "engine exports every parameter into the step's environment, so an unset one "
            "means this step was not launched from a workflow Cairn generated",
        )
    return value


def _refuse(name: str, value: str, why: str) -> CairnError:
    return CairnError(
        "invalid_arguments",
        f"{name}={value!r} is not a value this run can be varied to: {why}",
        detail={"parameter": name, "value": value},
    )


def repository(standing_in: Path, environ: Mapping[str, str] | None = None) -> Path:
    """The repository this run targets, refused unless both derivations of it agree.

    `standing_in` is the step's own working directory, which the engine derived from this
    same parameter — so the two are compared as the repository each names rather than as
    text, and a value naming a different repository than the one the step was placed in is
    refused before the lock is taken.
    """
    value = parameter(REPOSITORY_PARAM, environ)
    if not os.path.isabs(value):
        raise _refuse(
            REPOSITORY_PARAM,
            value,
            "it is not an absolute path, so the engine resolves it against a scratch "
            "directory rather than against the repository",
        )
    # The emitter's splice, resolved the way the operating system will resolve it, against
    # the directory `cairn worktree setup` derives from where it is standing. Both sides
    # must be the real directory rather than a spelling of one: comparing the value against
    # itself would agree for a repository reached through a symlink, or through a `..`,
    # while the two really name different places.
    spliced = Path(value + WORKTREES_SUFFIX).resolve()
    derived = worktrees_parent(standing_in).resolve()
    if spliced != derived:
        raise _refuse(
            REPOSITORY_PARAM,
            value,
            f"every isolated step would run under {spliced}, while `cairn worktree setup` "
            f"creates {derived}. The engine creates a missing working directory rather "
            "than failing, so the branch would carry no work and the wave would land "
            f"nothing while reporting success. Pass {str(standing_in)!r}",
        )
    target = Path(value)
    if common_directory(target) != common_directory(standing_in):
        raise _refuse(
            REPOSITORY_PARAM,
            value,
            f"it names a different repository than the one this step is standing in "
            f"({standing_in})",
        )
    return target


def _admin_directory_of(runs_root_value: Path) -> Path | None:
    """The admin directory an emitter composed this runs root from, or `None`.

    Read off the path rather than asked of git, because the question is which repository an
    *emitter* wrote this value for, and the emitter composed it as
    `<admin directory>/cairn/runs`. Asking git would answer about the filesystem now, which
    is a different question and one a moved directory would answer wrongly.

    Matching that shape rather than a directory literally named `.git` is what makes the
    check reach a repository whose admin directory is somewhere else — a clone made with
    `--separate-git-dir` has no `.git` component at all, and its retarget is the same
    defect. A path that is not that shape belongs to no repository: it is a runs root
    somebody deliberately relocated, and their records are where they put them.
    """
    tail = (CAIRN_STATE, RUNS_DIRECTORY)
    if runs_root_value.parts[-len(tail) :] != tail:
        return None
    return runs_root_value.parents[len(tail) - 1]


def refuse_misfiled_records(
    target: Path, environ: Mapping[str, str] | None = None
) -> None:
    """Refuse a run whose records would be written somewhere other than its own repository.

    The third derivation of the target, and the one the other two cannot see. The runs root
    is resolved at **authoring** time and emitted into `env:`, where it is a fixed absolute
    path; the repository is a **parameter**, editable at every trigger surface. Both
    derivations in `repository` above follow that parameter — the emitter's splice and the
    step's own working directory move together — so a value naming a second repository
    satisfies them both.

    What it does not move is where the run writes. A retargeted run does its work in one
    repository while its occasion, every step report and its record land in the other, and
    `cairn report --repository <the one that was worked on>` then answers that no such run
    exists. A run that really happened reads as one that left nothing, on exactly the path
    nobody is watching.

    So a generated definition is bound to the repository it was authored for, and
    retargeting it is re-authoring it rather than varying a parameter.

    **What is refused is a runs root belonging to another repository, not one that is merely
    elsewhere.** A path under some repository's admin directory is one an emitter wrote for
    *that* repository, and a run standing in a different one is the retarget. A path under no
    repository at all is a relocation someone chose deliberately — a scratch root, a test
    harness — and the run's records are exactly where that person put them.
    """
    values = os.environ if environ is None else environ
    declared = values.get(RUNS_ROOT_ENV)
    if not declared:
        return
    if not os.path.isabs(declared):
        # Resolved against each step's own working directory, so a relative value means a
        # different place per step and the run's reports scatter across the worktrees.
        raise _refuse(
            RUNS_ROOT_ENV,
            declared,
            "it is not an absolute path, so every step resolves it against its own working "
            "directory and this run's reports would scatter",
        )
    resolved = Path(declared).resolve()
    admin = _admin_directory_of(resolved)
    if admin is None or admin == common_directory(target).resolve():
        return
    raise _refuse(
        REPOSITORY_PARAM,
        str(target),
        f"this run would work in {target} while writing its occasion, every step report "
        f"and its own record into {resolved}, which belongs to the repository "
        f"administered by {admin}. The runs "
        "root is resolved when the workflow is authored and does not move with this "
        "parameter, so the run would leave no record where anyone would look for it. "
        f"Re-author the plan for {target} instead of varying the parameter",
    )


def parent_branch(
    directory: Path | None = None, environ: Mapping[str, str] | None = None
) -> str:
    """The branch this run's work lands on, refused unless git itself accepts the name.

    The value reaches git's argv directly — as a merge target, a worktree base and a delete
    operand — so a leading `-` is an option rather than a branch. That much is refused on
    the text, at every read, because it costs nothing. The full grammar is git's own answer
    and is asked for only where a `directory` is given, which is the run's first act: it is
    a subprocess, and the steps that read this parameter afterwards are already past the
    point where a malformed one could have been acted on.
    """
    value = parameter(PARENT_BRANCH_PARAM, environ)
    if value.startswith("-"):
        raise _refuse(
            PARENT_BRANCH_PARAM,
            value,
            "it begins with '-', so git would read it as an option rather than a branch",
        )
    if directory is not None:
        checked = git(
            directory, ("check-ref-format", f"refs/heads/{value}"), check=False
        )
        if checked.exit_code != 0:
            raise _refuse(
                PARENT_BRANCH_PARAM, value, "git does not accept it as a branch name"
            )
    return value


__all__ = ["parameter", "parent_branch", "refuse_misfiled_records", "repository"]
