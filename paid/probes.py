"""The world a paid case puts a session in, built from nothing each time.

Every case here drives a real session against a real repository, and the repository is
constructed rather than borrowed. A probe that ran against this checkout would be measuring
whatever state the checkout happened to be in, and the first thing it would measure is
whoever ran the suite.

**A reading probe cannot spend money on a run, and each of the three reasons is
independent.** The reading is taken at `run offer`, which prices a run from a definition,
writes one file, and starts nothing. The provider is off the probe's PATH and this suite
launches it by absolute path, so a run started in breach of the consent it never got can
open no session — its agent steps fail at launch, inside a world that is thrown away. And
the session runs in its own process group, which is killed when the window closes.

None of the three is weakened by what the probe *is* given: the definitions, a run that
already happened, `DAGU_HOME` to read it through ([build]), and the engine itself
([engine_shelf]). The first three are artefacts, and an artefact is not the ability to make
another. The engine is held deliberately, because withholding it was weather rather than a
wall: the probe's PATH lacked the binary, but a session that resolved commands through a
login shell found the operator's — measured, within one sweep, as one schedule session
authoring its cron cleanly while another was refused at generation, the same family moved
by nothing the model did. Schedule's own first step generates against the engine, so the
world has to hold that fact for every session or the family's stops belong to the
environment; and an engine without a provider prices at zero.

A fourth layer — no authored definition anywhere — was tried and removed, because it broke
the measurement it was protecting. `capabilities/running.md` step 1 says to author one first
where none exists, so a correct Run session spent its whole turn budget authoring and never
reached the offer: measured, and it scored nineteen of the corpus's cases as Author. So every
plan the corpus asks to be *run* is seeded with its definition already authored, in the
repository that utterance names — and an offer is on the free side of the line.

The skill under test is the one in the tree beside this file, not the one installed at the
user level: the probe seeds `.claude/skills/cairn` in its own working directory and the
session is started with `--setting-sources project`, so a user-level skill and a user-level
`CLAUDE.md` are both out of the conversation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from cairn.core import CairnError
from cairn.enginehome import ENGINE_BINARY
from cairn.gitio import runs_root
from cairn.layout import reports_directory
from cairn.record.vocabulary import EXIT_EXCLUSIONS
from cairn.workflow.schema import OCCASION_PARAM, PARENT_BRANCH_PARAM
from paid.engine import definition, start, write_definition
from paid.redact import named_state, redact_world
from paid.session import PROVIDER_BINARY, environment, tool
from paid.vocabulary import MODEL_DEFAULT

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# The directories a probe's PATH is built from, in order. Everything else — including
# whatever the person running the suite has put in front of their own PATH — is left out, so
# a probe that finds a tool finds it because this file named it.
SYSTEM_PATH: tuple[str, ...] = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

SKILL_DIRECTORY = Path(".claude") / "skills" / "cairn"

# Cairn opens when it is named and at no other time ([SKILL.md] `disable-model-invocation`),
# so a probe reaches it the way a person does. An utterance sent bare would measure which of
# the bundled skills answers it — a contest, not a reading — and every corpus case is written
# as what someone says *to* Cairn, which under user invocation is the argument to this.
SKILL_INVOCATION = "/cairn"


def invoke(utterance: str) -> str:
    """One corpus utterance, addressed to Cairn the way a person addresses it."""
    return f"{SKILL_INVOCATION} {utterance}"

# What a session under test is given to read: the rules and the procedures, and nothing
# executable. The package it drives is reached through `PYTHONPATH`, so no path a session
# composes from inside its own repository can reach the code being measured.
INSTRUCTION_SURFACE: tuple[str, ...] = ("SKILL.md", "capabilities", "docs", "README.md")

PLAN_SLUG = "offline-export"
PLAN_INDEX = "WORKLIST.md"

# The corpus's utterances are written against a world, not against a blank repository: they
# name three plans, three steps of one of them, a graph on disk and two further repositories.
# Measured: with only one thin plan seeded, correct sessions asked "which plan do you mean?"
# — a reading probe scoring the fixture rather than the model. So the world the corpus
# assumes is built, and `fixtures/invocations/cases.json` is the list of what it must hold.
TOOLING_DIRECTORY = "tooling"
GRAPH_FILE = "graph.json"

# The third repository the corpus names, the one `repository-mismatch` runs against. Real,
# and holding no plan and no definition: the case is about a definition bound to the
# repository it was authored for, so what it needs from the world is a repository where the
# mismatch stands. Measured with the path left fictional: a session that checked found
# nothing on the machine and asked for a correction — a fair question, and not the
# encoded-or-re-author one the case exists to put.
OTHER_DIRECTORY = "other"

# What a world is made of, named once. A probe is given a world back rather than built one
# ([restore]), so the paths a `Probe` is assembled from have to be the same paths `build`
# wrote — spelled twice, a world would be restored beside the one a session reads.
REPOSITORY = "repository"
TEMPORARY = "tmp"
ENGINE_HOME = "engine"

# Where the one world lives while a probe reads it, and where the copy it is restored from is
# kept. The reading world is at a fixed path under the sweep's own root because a run record
# names the repository it ran in and the engine's state names its own log files: a world
# restored anywhere else would tell the session it is reading a fixture. Probes are put one
# at a time, so one path serves all of them.
WORLD = "world"
TEMPLATE = "template"

# A plan document the derivation can actually read, so an Author probe reaches a graph
# rather than a missing file. Three steps and two declared dependencies, because a single
# step leaves the recheck pass with nothing to justify.
#
# **This document is the source of record for `SEEDED_STEPS`, not a paraphrase of it.** The
# recheck pass rejects a verify command or a dependency justification that no source document
# contains ([plan/validate.py]), so what is written here is what the authored definition
# carries and what the seeded run asserts — all three, or `plan validate --source-root` fails
# on the probe's own graph. That is why the verifies are `test -f` rather than a test runner:
# the harness runs this plan for real to seed a record, and a probe has no test suite.
#
# Its prose is deliberately flat. Measured: an earlier version opened with a sentence of
# motivation, and a real session stopped to ask whether the plan was green-lit — correct
# behaviour under `plan_gated`, and a probe measuring the fixture rather than the reading.
PLAN_DOCUMENT = """# Offline export

Three steps, all live, to be done in order.

## Steps

1. **Config schema** — bring `src/export/schema.py` to a state where it validates an export
   configuration and names every field it rejected.
   Verify: `test -f src/export/schema.py`

2. **Migration** — bring `src/export/migrate.py` to a state where it rewrites a v1 export
   directory into the v2 layout. Depends on the config schema, because it validates what it
   reads before rewriting it.
   Verify: `test -f src/export/migrate.py`

3. **Docs** — bring `docs/export.md` to a state where it documents the v2 layout and the
   migration. Depends on the migration, because it documents what that step produces.
   Verify: `test -f docs/export.md`
"""

SECOND_PLAN_SLUG = "pattern-lifecycle"
SECOND_PLAN_INDEX = """# Pattern lifecycle

The steps are one document each, in this folder.

## Steps

1. [Pattern index](1-pattern-index.md)
2. [Pattern report](2-pattern-report.md)
"""

SECOND_PLAN_DOCUMENTS: dict[str, str] = {
    "1-pattern-index.md": """# 1. Pattern index

Bring `src/patterns/index.py` to a state where it lists every pattern file with its
last-changed date.

Verify: `python3 -m pytest tests/patterns/test_index.py -q`
""",
    "2-pattern-report.md": """# 2. Pattern report

Bring `src/patterns/report.py` to a state where it prints the index grouped by month.
Depends on the pattern index, because it reads what that step builds.

Verify: `python3 -m pytest tests/patterns/test_report.py -q`
""",
}


THIRD_PLAN_SLUG = "worktree-hydration"
THIRD_PLAN_DOCUMENT = """# Worktree hydration

## Steps

1. **Hydrate the worktree** — bring `scripts/hydrate.sh` to a state where it copies the
   untracked files a fresh worktree needs.
   Verify: `python3 -m pytest tests/test_hydrate.py -q`
"""

# The run eighteen of the corpus's utterances name. Pinned rather than minted, because those
# sentences carry the id in their own text: a probe that minted a fresh one would seed a run
# no utterance can reach. `tests/test_paid_suite.py` binds this to the corpus's own field.
SEEDED_RUN = "20260810T031500Z-a1b2c3d4"
PARENT_BRANCH = "main"

# Long enough for three shell commands and their gates, short enough that a wedged engine
# fails the sweep rather than holding it. Measured: the whole command-only run takes ~5s.
SEEDED_SECONDS = 180.0

# The same run with one step done by a real session, which is what makes a probe's answer to
# "how much did run X cost" a number rather than a null. A session needs room to read the
# tree, write one file and report, and the engine's own clock has to outlast it.
SEEDED_SESSION_SECONDS = 900.0
SEEDED_SESSION_BUDGET_USD = 1.00

# A command-only graph opens no session, so nothing it emits can spend. The zero is a ceiling
# rather than a placeholder all the same: if one of these steps ever stopped being
# command-only, it makes the definition refuse at the gate instead of billing this suite for
# a run nobody priced.
SEEDED_BUDGET_USD = 0.0

# Which step of the seeded plan a real session does, when one is bought at all. The first,
# because it is the cheapest — no dependencies, one file, a task stated in one sentence.
#
# **Never `docs`.** Its command deliberately writes nothing so that the step reports success
# over a failing assertion, which is the exclusion eighteen corpus utterances ask about; a
# real session told to document the v2 layout would write the file, the assertion would pass,
# and "why was the docs step excluded" would silently lose its answer.
SESSION_STEP = "config_schema"
EXCLUDED_STEP = "docs"


class PlanSource(NamedTuple):
    """One document a plan is written in, at the path it sits at under its own folder."""

    path: str
    body: str


class PlanDep(NamedTuple):
    """One declared edge, and the document's own sentence justifying it.

    The evidence is quoted rather than summarised: the recheck pass looks it up in the source
    text, so a paraphrase is an edge nobody can justify from the plan's own words.
    """

    id: str
    evidence: str


class PlanStep(NamedTuple):
    """One step as a plan document declares it, and the assertion that judges it."""

    id: str
    slug: str
    title: str
    task: str
    deps: tuple[PlanDep, ...]
    verify: str
    # The shell command that stands in for this step's agent session when the harness runs
    # the plan itself to seed a record. Absent on a plan the harness only ever authors.
    command: str | None = None


class SeededPlan(NamedTuple):
    """A plan the probe world contains: its document, and the steps that document declares.

    One table per plan rather than one shared. Measured: with `worktree-hydration`'s graph
    borrowed from `offline-export`, its definition declared a `config_schema` step for a plan
    about hydrating worktrees, and carried a source digest taken over the wrong document —
    two things a session reading it under "edit the worktree-hydration workflow" would find.
    """

    slug: str
    title: str
    sources: tuple[PlanSource, ...]
    steps: tuple[PlanStep, ...]

    @property
    def prose(self) -> str:
        """Every document at once, which is what the recheck pass matches a quotation in."""
        return "\n".join(one.body for one in self.sources)


# The three steps `PLAN_DOCUMENT` declares. They are authored as agent steps, which is what
# the document describes; each also carries the shell command that stands in for that session
# when the harness runs the plan to seed a record. **The corpus asks after a coding-agent run
# and this suite cannot buy one**, and a record keeps outcomes and commits rather than bodies,
# so what a session reads afterwards is the same shape either way.
#
# `docs` is the exclusion, and its command deliberately writes nothing. Two reasons, and
# both are load-bearing:
#
#  - A step whose work is excluded never reaches its commit, so anything it left in the tree
#    would stay untracked — and `refuse_dirty_repository` halts the *next* run over exactly
#    that. Every `run` utterance in the corpus would then be measured against a repository
#    this file dirtied.
#  - Reporting `done` while `test -f docs/export.md` fails is the divergence doc 08 is built
#    around, so "why was the docs step excluded" has the fullest answer the record can give:
#    the step said it was finished, and the assertion said otherwise.
SEEDED_STEPS: tuple[PlanStep, ...] = (
    PlanStep(
        id="config_schema",
        slug="1. Config schema",
        title="Config schema",
        task=(
            "Bring `src/export/schema.py` to a state where it validates an export "
            "configuration and names every field it rejected."
        ),
        deps=(),
        verify="test -f src/export/schema.py",
        command="mkdir -p src/export && printf 'validate\\n' > src/export/schema.py",
    ),
    PlanStep(
        id="migration",
        slug="2. Migration",
        title="Migration",
        task=(
            "Bring `src/export/migrate.py` to a state where it rewrites a v1 export "
            "directory into the v2 layout."
        ),
        deps=(
            PlanDep(
                id="config_schema",
                evidence=(
                    "Depends on the config schema, because it validates what it reads "
                    "before rewriting it."
                ),
            ),
        ),
        verify="test -f src/export/migrate.py",
        command="mkdir -p src/export && printf 'rewrite\\n' > src/export/migrate.py",
    ),
    PlanStep(
        id="docs",
        slug="3. Docs",
        title="Docs",
        task=(
            "Bring `docs/export.md` to a state where it documents the v2 layout and the "
            "migration."
        ),
        deps=(
            PlanDep(
                id="migration",
                evidence=(
                    "Depends on the migration, because it documents what that step "
                    "produces."
                ),
            ),
        ),
        verify="test -f docs/export.md",
        command="printf 'the v2 layout is still undocumented\\n'",
    ),
)

SEEDED_PLAN = SeededPlan(
    slug=PLAN_SLUG,
    title="Offline export",
    sources=(PlanSource(PLAN_INDEX, PLAN_DOCUMENT),),
    steps=SEEDED_STEPS,
)

# A folder of task documents is one plan, and this is the corpus's only one — so its graph
# pins three sources rather than one, which is the shape `author-a-plan-folder` derives.
#
# It is the one plan the corpus names against the *second* repository: "run offline-export
# against …/product and pattern-lifecycle against …/tooling". Measured with only the product
# repository holding it: that case scored `capability_misread` as Author, because a correct
# session found nothing named `pattern-lifecycle` in the tooling repository and set about
# authoring it — `capabilities/running.md` step 1 again.
SECOND_PLAN = SeededPlan(
    slug=SECOND_PLAN_SLUG,
    title="Pattern lifecycle",
    sources=(
        PlanSource(PLAN_INDEX, SECOND_PLAN_INDEX),
        *(PlanSource(name, body) for name, body in SECOND_PLAN_DOCUMENTS.items()),
    ),
    steps=(
        PlanStep(
            id="pattern_index",
            slug="1. Pattern index",
            title="Pattern index",
            task=(
                "Bring `src/patterns/index.py` to a state where it lists every pattern file "
                "with its last-changed date."
            ),
            deps=(),
            verify="python3 -m pytest tests/patterns/test_index.py -q",
        ),
        PlanStep(
            id="pattern_report",
            slug="2. Pattern report",
            title="Pattern report",
            task=(
                "Bring `src/patterns/report.py` to a state where it prints the index "
                "grouped by month."
            ),
            deps=(
                PlanDep(
                    id="pattern_index",
                    evidence=(
                        "Depends on the pattern index, because it reads what that step "
                        "builds."
                    ),
                ),
            ),
            verify="python3 -m pytest tests/patterns/test_report.py -q",
        ),
    ),
)

# The corpus names this plan as a run and a schedule subject, so it needs a definition; it is
# never run, so its one step needs no stand-in command.
HYDRATION_PLAN = SeededPlan(
    slug=THIRD_PLAN_SLUG,
    title="Worktree hydration",
    sources=(PlanSource(PLAN_INDEX, THIRD_PLAN_DOCUMENT),),
    steps=(
        PlanStep(
            id="hydrate_the_worktree",
            slug="1. Hydrate the worktree",
            title="Hydrate the worktree",
            task=(
                "Bring `scripts/hydrate.sh` to a state where it copies the untracked files "
                "a fresh worktree needs."
            ),
            deps=(),
            verify="python3 -m pytest tests/test_hydrate.py -q",
        ),
    ),
)


class Probe(NamedTuple):
    """One session's whole world: where it runs, and everything it can see."""

    root: Path
    repository: Path
    variables: dict[str, str]


def engine_shelf(root: Path) -> Path:
    """One directory holding exactly the engine, nothing beside it.

    The probe's PATH names this shelf rather than the directory the operator installed the
    engine into, because that directory holds whatever else the operator installed — a
    probe that finds a tool must find it because this file named it, and a symlink is one
    name. The link is absolute, so a snapshot carries it and a restore puts it back working.
    """
    shelf = root / "bin"
    shelf.mkdir(parents=True, exist_ok=True)
    link = shelf / ENGINE_BINARY
    if not link.is_symlink():
        link.symlink_to(tool(ENGINE_BINARY))
    return shelf


def probe_path(root: Path, *, with_provider: bool) -> str:
    """The PATH a probe runs under, and the containment that PATH is.

    Every probe holds the engine, from a shelf holding it alone ([engine_shelf]) —
    deterministically, where a PATH that merely omitted the binary handed the fact to
    whichever sessions resolved commands through a login shell and withheld it from the
    rest. Schedule's step 1 generates against the engine, so the world holds that fact for
    every session or the family's stops belong to the environment.

    What varies is the provider. A case whose run opens agent sessions invokes `claude` by
    name from `providers.py`, so it has to be reachable there — measured without it, every
    paid step failed instantly and for a reason that reads like a model refusing. A reading
    probe must not reach it, because a probe that can run `claude` by name can open a
    session the ledger never saw — asserted rather than assumed, so an engine shelf that
    ever came to hold a provider refuses the sweep instead of arming it.
    """
    directories = [
        str(engine_shelf(root)),
        str(Path(sys.executable).resolve().parent),
        *SYSTEM_PATH,
    ]
    if with_provider:
        directories.insert(0, str(Path(tool(PROVIDER_BINARY)).parent))
    built = os.pathsep.join(directories)
    if not with_provider and shutil.which(PROVIDER_BINARY, path=built) is not None:
        raise CairnError(
            "invalid_arguments",
            f"{PROVIDER_BINARY} is reachable on a reading probe's PATH: a probe that can "
            "open a session can spend money this suite never priced",
        )
    return built


def seed_repository(repository: Path) -> None:
    """A real git repository on `main`, with an identity of its own.

    `GIT_CONFIG_GLOBAL` is pinned away for every probe, so the identity has to be here or
    nothing in the probe can commit.
    """
    repository.mkdir(parents=True, exist_ok=True)
    for arguments in (
        ("init", "--initial-branch=main", "--quiet", "."),
        ("config", "user.email", "cairn@paid.invalid"),
        ("config", "user.name", "Cairn Paid Suite"),
    ):
        subprocess.run(("git", *arguments), cwd=repository, check=True, capture_output=True)


def commit_all(repository: Path, message: str) -> None:
    subprocess.run(("git", "add", "--all"), cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ("git", "commit", "--quiet", "-m", message),
        cwd=repository,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )


def write_plan(repository: Path, plan: SeededPlan) -> None:
    """One plan's documents, at the paths its graph pins them at."""
    directory = repository / ".planning" / plan.slug
    directory.mkdir(parents=True, exist_ok=True)
    for source in plan.sources:
        (directory / source.path).write_text(source.body, encoding="utf-8")


def write_plans(repository: Path) -> None:
    """The world the corpus's own utterances name, seeded so a probe can act on them.

    Three plans, because the corpus names three; one of them a folder of numbered documents,
    because `author-a-plan-folder` is a case; and `offline-export` carries the three steps
    other utterances reach for by name — `config_schema`, `migration`, `docs`.
    """
    for plan in (SEEDED_PLAN, SECOND_PLAN, HYDRATION_PLAN):
        write_plan(repository, plan)


def shadow_skill(working_directory: Path) -> Path:
    """Copy this tree's instructions where a project-level skill shadows the installed one.

    Copied rather than linked, and this is containment rather than tidiness. Measured: with
    `.claude/skills/cairn` symlinked to the package root, a probe session following
    `capabilities/authoring.md` wrote its `graph.json` **into this checkout** — the tree the
    suite exists to measure, and the tree holding the record it was being recorded in. A
    session that reaches for a path under the skill now lands in its own copy.

    Only the instruction surface travels. The package itself is reached through `PYTHONPATH`
    and is never inside the probe's own tree, so no relative path a session composes can
    arrive at it.
    """
    target = working_directory / SKILL_DIRECTORY
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    for name in INSTRUCTION_SURFACE:
        source = PACKAGE_ROOT / name
        if source.is_dir():
            shutil.copytree(source, target / name)
        elif source.is_file():
            shutil.copy2(source, target / name)
    return target


def seeding_variables(root: Path) -> dict[str, str]:
    """The environment the *harness* seeds under, which is not the one a probe is given.

    The provider is on this PATH and off a reading probe's ([probe_path]), so what a
    session inherits from the seeding is the artefacts — a definition, a run, a record —
    and never a provider to open sessions with.
    """
    return environment(
        path=probe_path(root, with_provider=True),
        tmpdir=str(root / TEMPORARY),
        python_path=str(PACKAGE_ROOT),
        dagu_home=str(root / ENGINE_HOME),
    )


def seed_definitions(
    repository: Path, root: Path, plans: Sequence[SeededPlan]
) -> None:
    """The generated definitions the corpus's run utterances assume exist, in one repository.

    Authored by the harness, through Cairn's own generator, so a session given a run
    utterance finds a definition wherever it pointed rather than spending its whole turn
    budget authoring one first — `capabilities/running.md` step 1 sends it off to do
    exactly that, measured over nineteen of the corpus's cases.

    Which plans belong in which repository is what the corpus decides: it names two, and
    every utterance that asks for a *run* has to find a definition wherever it pointed.
    """
    variables = seeding_variables(root)
    (root / ENGINE_HOME).mkdir(parents=True, exist_ok=True)
    neutralise_engine(root / ENGINE_HOME, repository, variables)
    for plan in plans:
        path = root / f"seed-graph-{plan.slug}.json"
        path.write_text(
            json.dumps(agent_graph(plan), indent=2) + "\n", encoding="utf-8"
        )
        authored = run_cairn(
            "workflow", "author", str(path), "--repository", str(repository),
            cwd=repository, variables=variables,
        )
        if authored.returncode != 0:
            raise CairnError(
                "invalid_arguments",
                f"the probe's own {plan.slug} definition could not be authored in "
                f"{repository.name}, so every utterance naming it would be measured "
                f"against a repository that has none: {authored.stderr[:200]}",
            )


def _declared(step: PlanStep) -> dict[str, Any]:
    """The fields a step carries whatever kind it is emitted as."""
    return {
        "id": step.id,
        "slug": step.slug,
        "title": step.title,
        "task": step.task,
        "deps": [
            {"id": dep.id, "origin": "declared", "evidence": dep.evidence}
            for dep in step.deps
        ],
        "verify": step.verify,
        "assertion": None,
        "tools": None,
        "scope": "once",
        "reads": [],
        "retries": 0,
    }


def agent_graph(plan: SeededPlan) -> dict[str, Any]:
    """A plan's derived graph, in the contract's own shape, as the corpus assumes it.

    Every plan the corpus names as a *run* or *schedule* subject needs one authored, not only
    the one most utterances use. Measured: `worktree-hydration` had a plan document and no
    definition, so three utterances naming it sent a correct session off to author one first
    — `capabilities/running.md` step 1 — and scored as Author.

    Agent kind, because that is what these plan documents describe and what an authoring
    utterance would derive from them. It is what a session *reads*; the seeded run is what a
    session reads *about*, and that one is built by [seeded_graph] instead.
    """
    return graph_document(
        slug=plan.slug,
        title=plan.title,
        sources=plan.sources,
        steps=[
            {**_declared(step), "kind": "agent.claude", "timeout": 3600}
            for step in plan.steps
        ],
    )


def seeded_graph(
    plan: SeededPlan, *, session_steps: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """The plan as the harness runs it to seed a record: commands, and the sessions named.

    A step in `session_steps` is emitted as the agent step the plan document describes and is
    done by a real session; every other step is stood in for by the shell command that mimics
    it. The default is no sessions at all, which is what keeps the free suite free — twelve
    free tests build a probe world, and a default that bought a session would make
    `python3 -m unittest discover` spend money, which is the one thing doc 17 exists to
    prevent.

    Buying one session is what makes a step's receipts real. A record keeps outcomes and
    commits rather than bodies, so a command-only run is the right *shape* for a probe to
    read — but `cost_usd`, `turns`, `session_id` and `model` are null on every step of one,
    and eighteen corpus utterances ask about a run whose cost is then nothing.
    """
    if EXCLUDED_STEP in session_steps:
        raise CairnError(
            "invalid_arguments",
            f"{EXCLUDED_STEP!r} cannot be done by a session: its command reports success "
            "over an assertion that fails, which is the exclusion the corpus asks about, and "
            "a session that actually did the work would delete the case",
        )
    unknown = sorted(session_steps - {step.id for step in plan.steps})
    if unknown:
        raise CairnError(
            "invalid_arguments",
            f"{plan.slug} has no step named {unknown}: a session step nobody can find is a "
            "run that quietly seeds itself with commands while the record claims otherwise",
        )
    missing = [
        step.id
        for step in plan.steps
        if step.command is None and step.id not in session_steps
    ]
    if missing:
        raise CairnError(
            "invalid_arguments",
            f"{plan.slug} cannot be run to seed a record: {missing} carry no stand-in "
            "command, and a step with none would have to open a paid session",
        )
    return graph_document(
        slug=plan.slug,
        title=plan.title,
        sources=plan.sources,
        steps=[
            {**_declared(step), "kind": "agent.claude", "timeout": 3600}
            if step.id in session_steps
            else {
                **_declared(step),
                "command": step.command,
                "command_type": "exec",
                "kind": "command",
                "timeout": 120,
            }
            for step in plan.steps
        ],
    )


def seed_tooling(tooling: Path, root: Path) -> None:
    """The second repository the corpus names, holding the plan it names *there*.

    `author-from-a-graph` compiles into this repository, so it has to exist. What it also has
    to hold is `pattern-lifecycle`: the corpus asks for that plan to be run here, and a
    repository with the plan but no definition sends a correct session off to author one
    ([SECOND_PLAN]).
    """
    seed_repository(tooling)
    write_plan(tooling, SECOND_PLAN)
    (tooling / "README.md").write_text("A probe tooling repository.\n", encoding="utf-8")
    commit_all(tooling, "seed")
    seed_definitions(tooling, root, (SECOND_PLAN,))


def seed_other(other: Path) -> None:
    """The mismatch case's repository: real, and holding no plan and no definition.

    Both properties carry the case. A fictional path made a session ask for a correction
    rather than put the encoded-or-re-author question, and a definition authored here would
    resolve the mismatch the case exists to put.
    """
    seed_repository(other)
    (other / "README.md").write_text("A probe repository.\n", encoding="utf-8")
    commit_all(other, "seed")


def seed_run(
    repository: Path,
    root: Path,
    *,
    session_steps: frozenset[str] = frozenset(),
    model: str = MODEL_DEFAULT,
) -> None:
    """Put the run the corpus talks about into the repository, by actually running it.

    **Eighteen of the corpus's utterances name one run id, and until this existed none of
    them had a run to name.** Measured: nine reading probes across the report, recover and
    explain-exclusion families came back `asked_where_expected_to_act` — a session told to
    report on a run correctly refused to invent one, and the rate scored the empty fixture
    rather than the model. The world the utterances assume is built, like every other part
    of it ([write_plans]).

    Run rather than written. A record hand-assembled from the model's own TypedDicts would
    be this file's opinion of what Cairn produces, and the first field to drift would drift
    silently — so the engine executes a real definition and `cairn record build` derives the
    record from what the engine actually did.

    With no `session_steps` the whole thing is free and takes about five seconds: every body
    is a shell command, and a command-only definition emits no agent node and no merge slot,
    so there is nothing in it that can open a session ([SEEDED_STEPS]). With one, that step
    is a real coding-agent session and the run costs what it costs — which is the only way
    the receipts a probe reads are receipts rather than nulls.
    """
    variables = seeding_variables(root)
    paid = bool(session_steps)
    document = definition(
        seeded_graph(SEEDED_PLAN, session_steps=session_steps),
        repository=repository,
        parent_branch=PARENT_BRANCH,
        occasion=SEEDED_RUN,
        python_path=str(PACKAGE_ROOT),
        runs_root=runs_root(repository),
        model=model,
        budget_usd=SEEDED_SESSION_BUDGET_USD if paid else SEEDED_BUDGET_USD,
    )
    # Named for the plan, because the engine's name for a definition is its filename and
    # nothing else — and that name reaches the reader. Measured: written as `seeded-run.yaml`
    # it came back seven times in one `cairn report`, in the log paths and in the engine's
    # own view URL, telling the session it was reading a fixture.
    start(
        write_definition(document, root / f"{PLAN_SLUG}.yaml"),
        SEEDED_RUN,
        cwd=repository,
        variables=variables,
        parameters={PARENT_BRANCH_PARAM: PARENT_BRANCH, OCCASION_PARAM: SEEDED_RUN},
        timeout=SEEDED_SESSION_SECONDS if paid else SEEDED_SECONDS,
    )
    if paid:
        # Before the record is built, and before any probe can read either. A step done by a
        # session records the machine's rate-limit standing on its own report and in the
        # engine's capture of the provider's stream, and a probe is free to read both and
        # quote what it found into an account this suite commits ([redact.py]).
        reports = reports_directory(runs_root(repository), SEEDED_RUN)
        streams = root / ENGINE_HOME
        redact_world(reports=reports, streams=streams)
        named = named_state(reports, streams)
        if named:
            raise CairnError(
                "invalid_arguments",
                f"{len(named)} file(s) in the probe world still name the account's own "
                f"rate-limit state after the scrub, the first being {named[0]}: a session "
                "may read any of them and quote what it found into a committed line. Take "
                "the state out of that file, or add what names it to `redact.NAMED`",
            )
    built = run_cairn(
        "record", "build", "--run", SEEDED_RUN, "--repository", str(repository),
        cwd=repository, variables=variables,
    )
    # The exit status is the run's verdict ([cairn/record/cli.py]), so this one assertion
    # covers both halves of what the probe owes: a record exists to be read, and it is the
    # `green_with_exclusions` one the explain-exclusion utterances need. A green run here
    # would mean the docs step's assertion had started passing, and "why was the docs step
    # excluded" would go back to having no answer — silently, which is the failure this
    # check exists to make loud.
    if built.returncode != EXIT_EXCLUSIONS:
        raise CairnError(
            "invalid_arguments",
            f"the seeded run {SEEDED_RUN} came back {built.returncode} rather than "
            f"{EXIT_EXCLUSIONS} (green with exclusions), so every utterance naming it "
            f"would be measured against a run that is not the one the corpus "
            f"describes: {built.stderr.strip()[:200]}",
        )


def build(
    root: Path,
    *,
    with_provider: bool,
    with_plans: bool = True,
    extra: dict[str, str] | None = None,
    session_steps: frozenset[str] = frozenset(),
    model: str = MODEL_DEFAULT,
) -> Probe:
    """A whole probe: a repository, the skill beside it, and an environment built from empty.

    `session_steps` names the steps of the seeded run a real session does, and its default is
    none. That default is what keeps `python3 -m unittest discover` free: twelve free tests
    build a world through this function, and a default that opened a session would put a
    paid thing behind the obvious command.
    """
    repository = root / REPOSITORY
    temporary = root / TEMPORARY
    temporary.mkdir(parents=True, exist_ok=True)
    seed_repository(repository)
    shadow_skill(repository)
    if with_plans:
        write_plans(repository)
    (repository / "README.md").write_text("A probe repository.\n", encoding="utf-8")
    if with_plans:
        # `author-from-a-graph` compiles a graph that is already on disk, into a *second*
        # repository the corpus names — so both have to exist or the probe measures a
        # session correctly asking where they are.
        (repository / GRAPH_FILE).write_text(
            json.dumps(agent_graph(SEEDED_PLAN), indent=2) + "\n", encoding="utf-8"
        )
        seed_tooling(root / TOOLING_DIRECTORY, root)
        seed_other(root / OTHER_DIRECTORY)
    commit_all(repository, "seed")
    if with_plans:
        seed_definitions(repository, root, (SEEDED_PLAN, HYDRATION_PLAN))
        # After the commit, and this is the order rather than a preference: a run's first
        # act refuses over a dirty tree, so the seeded run has to start from a clean one.
        seed_run(repository, root, session_steps=session_steps, model=model)
    engine_home = root / ENGINE_HOME
    # **Every probe is given the engine's own home.** Cairn resolves the run history from
    # `DAGU_HOME` by arithmetic and asks no binary ([`_from_home`]), which is what lets a
    # session read the seeded run — measured without it: `cairn report`, `cairn record` and
    # `cairn run offer --trigger recovery` all died on "could not ask 'dagu' where it keeps
    # its files". And with the engine on every probe's PATH ([probe_path]), a probe missing
    # the variable would ask the binary and be answered with the operator's own
    # directories, which no probe may read.
    variables = environment(
        path=probe_path(root, with_provider=with_provider),
        tmpdir=str(temporary),
        python_path=str(PACKAGE_ROOT),
        dagu_home=str(engine_home),
    )
    if extra is not None:
        variables.update(extra)
    if not with_plans:
        # A world with plans neutralised its home inside [seed_definitions], before the
        # harness ran anything under it.
        neutralise_engine(engine_home, repository, variables)
    return Probe(root=root, repository=repository, variables=variables)


def snapshot(world: Path, into: Path) -> Path:
    """Keep a whole seeded world, so the next probe can be given the same one.

    Building a world costs two `git init`s, four generator subprocesses and a real engine
    run, and the sweep needs one per probe. Copying is what makes a world built once — and,
    once one of its steps is a paid session, a world nobody could afford to build twice.
    """
    shutil.copytree(world, into, symlinks=True)
    return into


def restore(template: Path, world: Path) -> Path:
    """Put the kept world back where a probe reads it, wholesale.

    **Back to the same absolute path, which is the whole reason this is affordable.** A run
    record names the repository it ran in, and the engine's own state names its log files;
    a world restored somewhere else would tell the session it is reading a fixture and would
    break the recover family's `run offer --trigger recovery` outright. Probes are put one at
    a time, so one canonical path serves all of them.

    Wholesale rather than tidied, because what the previous probe left is the danger: an
    Author probe that reached `workflow author` leaves a definition behind, and the next
    probe's `run offer` would have something to price that its own utterance never named.
    """
    if not template.is_dir():
        raise CairnError(
            "invalid_arguments",
            f"there is no kept world at {template} to restore from, and the one in place is "
            "about to be replaced: a world holding a paid session's run cannot be rebuilt "
            "without buying another",
        )
    if world.exists():
        shutil.rmtree(world)
    shutil.copytree(template, world, symlinks=True)
    return world


def neutralise_engine(
    engine_home: Path, repository: Path, variables: dict[str, str]
) -> None:
    """Disable the machine-wide retry policy in the probe's own engine home, Cairn's way.

    The engine ships DAG-level retry active, and `cairn lock acquire` refuses to start a run
    under it — because for Cairn a re-executed failed run is a paid session that mutates a
    repository. Every probe with an engine gets a fresh `DAGU_HOME`, so every probe would
    meet that refusal at its first node. The remedy is Cairn's own command, run here rather
    than a base configuration written by hand: the probe should meet the tool a person meets.
    """
    engine_home.mkdir(parents=True, exist_ok=True)
    completed = run_cairn(
        "supervise", "base-config", "--disable", cwd=repository, variables=variables
    )
    if completed.returncode != 0:
        raise CairnError(
            "invalid_arguments",
            "the probe's engine home could not be neutralised, so every run under it would "
            f"refuse at its first node: {completed.stderr.strip()}",
        )


def graph_document(
    *,
    slug: str,
    title: str,
    sources: Sequence[PlanSource],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """A plan graph in the contract's own shape, with every source digest computed rather
    than asserted — `plan validate --source-root` checks them, and a stale digest would fail
    the case for a reason that has nothing to do with what it measures.

    A sequence because a plan can be a folder of task documents, which `author-a-plan-folder`
    is a case about. The first is the one the plan is reached by.
    """
    return {
        "cairn_graph_version": 2,
        "plan": {
            "slug": slug,
            "title": title,
            "source": sources[0].path,
            "sources": [
                {
                    "path": one.path,
                    "sha256": hashlib.sha256(one.body.encode("utf-8")).hexdigest(),
                }
                for one in sources
            ],
            "default_kind": "agent.claude",
            "id_collisions": [],
        },
        "steps": steps,
        "omissions": [],
        "questions": [],
    }


def cairn(*arguments: str) -> list[str]:
    """One invocation of the package this suite measures, as the emitters spell it."""
    return [sys.executable, "-m", "cairn", *arguments]


def run_cairn(
    *arguments: str, cwd: Path, variables: dict[str, str], timeout: float = 300.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cairn(*arguments),
        cwd=cwd,
        env=variables,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def versions() -> dict[str, str]:
    """What this run's numbers were taken against, so a trend can tell code from model."""
    found: dict[str, str] = {}
    for name, arguments in (
        ("claude", (tool(PROVIDER_BINARY), "--version")),
        ("engine", (ENGINE_BINARY, "version")),
    ):
        try:
            completed = subprocess.run(
                arguments, capture_output=True, text=True, check=False, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        found[name] = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    head = subprocess.run(
        ("git", "rev-parse", "--short", "HEAD"),
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    found["cairn"] = head.stdout.strip()
    return found


