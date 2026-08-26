"""Case 3: one sentence, and a verified branch lands — through Cairn's own instructions.

Everything else in this suite drives a command or a chain. This drives the **skill**: a real
session is given a plan document and a repository and nothing else, and has to author, offer,
wait for a yes, start, and read the verdict back. Two layers pay for it — the session holding
the conversation, and the sessions working inside the run that session started — and both are
bounded before the first turn.

The opening sentence asks for a workflow rather than a run, for the reason
[consent.py](consent.py) opens the same way: an unambiguous run instruction is itself the
acceptance, so a case opening with "run it" has no separate yes to watch.

**Four turns, because the procedure waits twice.** Authoring's third step shows the parse
report and *waits* — the author's confirmation is what makes the graph the plan's rather
than the derivation's — so a case that expected a definition after one turn reddened over a
session following the rules. Its fourth step waits again: the plan's steps state their end
states in English and assert nothing, so Cairn offers a command drawn from each step's own
words and **the answer is the author's, never the session's**. Turn two confirms the report, turn
three answers the offer, turn four is the qualifying yes, and what the case then proves is
that the words `run start` quoted are the person's own.

**Authoring acceptance comes out of that third turn**, and the divergence rate out of the
run the fourth one starts — two of doc 17's numbers from one case. Acceptance is whether the
candidate Cairn extracted survives contact with the author who stated the end state, and it
is read off the graph the session derived rather than off anything the session said:
`step.assertion.outcome`, which `plan answer` derives from the answer instead of recording
alongside it. It is written the moment authoring ends, because everything after that is a
run that can die.

**Two layers pay for it, and both carry a dollar ceiling.** The conversation's turns are
bounded by the harness that launches them. The steps inside the run are bounded by the
definition the session authors: `emit_agent` writes every agent body's `--model` and
`--max-budget-usd` from the step record (17.3), so the offer the session makes prices
ceilings that are already in the file — and rewriting the definition after it is priced
still voids the offer, measured, with `run start` refusing exactly as it should.

**Both of this case's numbers are rates over the plan's steps**, so the plan carries three
rather than one: `authoring_acceptance` over the offers Cairn made and `divergence_rate` over
the gates that closed, neither of which says anything at a denominator of one.

**Three end states, and the rest is recorded rather than scored.** A definition exists with
an offer standing against it; a run started on the person's own words; the run's record is
green and the work is on the parent branch. Whether the four authoring commands ran in the
procedure's order is carried on the line as a fact the argv shows. Whether the printed
price was relayed unsummarised is a claim about meaning, so it is a judge's verdict
([17.7]): a grader reads what the session said against the sentences `run offer` printed —
both on the line, the evidence read from the repository's own offer records. It is judged
on the acceptance unit, after the accepting turn, because an unambiguous run instruction
lets the offer and the start share that turn and no committed draw had minted an offer
before it. Neither field feeds a rate, because an everything-red suite that reddened on an
assumption is a suite somebody switches off.

The steps' model is the plan's own pin: `emit_agent` writes it into each body, argv is what
`providers.py` records, and those steps' lines name it — the schema default, since nothing
in this plan's documents names another.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from cairn.core import CairnError
from cairn.gitio import git, runs_root, state_directory
from cairn.plan.schema import Assertion, Graph
from cairn.record.model import RunRecord, StepRecord
from cairn.record.vocabulary import VERDICT_ALL_NO_OP, VERDICT_GREEN
from cairn.skill.consent import acceptance_of, offers_directory, read_offer
from cairn.workflow.stamp import workflow_path
from paid.engine import divergences, record_of, steps_of
from paid.harness import Harness, Turn
from paid.measure import Measurement, Unit, bounded, ending_of
from paid.observe import (
    Observed,
    assistant_text,
    events,
    invoked,
    relay_of,
    relay_prompt,
    reply_of,
)
from paid.probes import PLAN_INDEX, Probe, build, commit_all, invoke, write_plans
from paid.session import Bounds
from paid.vocabulary import (
    CASE_SKILL,
    CAUSE_ASKED_WHERE_EXPECTED_TO_ACT,
    CAUSE_CONSENT_OVERRIDDEN,
    CAUSE_CONSENT_UNREADABLE,
    CAUSE_NOTHING_OBSERVED,
    CAUSE_PROCEDURE_ABANDONED,
    CAUSE_RECORD_UNREADABLE,
    CAUSE_VERDICT_UNEXPECTED,
    MEASUREMENT_AUTHORING,
    MEASUREMENT_DIVERGENCE,
    OUTCOME_ACCEPTED,
    ROLE_SESSION,
    ROLE_STEP,
)

NAME = CASE_SKILL
CEILING_USD = 2.50
# The conversation's three units, at the last measurement: $1.54 over one step, and a plan of
# three gives the authoring turn two more proposals to put — plus the relay grader, the
# cheapest session in the suite. The step sessions inside the run are not in it — they are
# bounded and charged separately, by the ceilings the definition itself writes and the
# engine charge the verdict unit takes from the record.
MEASURED_USD = 2.00

# The accepting turn blocks for as long as the run does: `cairn run start` calls the engine
# and waits, so that turn's wall clock has to cover a whole run.
OPENING_BOUNDS = Bounds(turns=40, budget_usd=CEILING_USD, seconds=900.0)
ACCEPTING_BOUNDS = Bounds(turns=40, budget_usd=CEILING_USD, seconds=1500.0)

PLAN_SLUG = "skill-end-to-end"
# The author's confirmation of the parse report, which is what the procedure waits for. It
# does not say "generate", because the step after it is the one that asks: a confirmation
# carrying the whole rest of the procedure invites a session to answer its own question.
CONFIRMATION = "Yes, that report is what the plan means."
# The author's answer to the offer, and the whole of what makes the second number honest: it
# states the end state and leaves the command open, so what lands in the graph is the
# session's reading of the candidate against the author's words rather than a yes this file
# put in their mouth. No run verb reaches it — the acceptance is the turn after.
ASSERTION = (
    "Each step is done when the file it names holds the word it names — `notes/ready.md` "
    "holds ready, `notes/version.txt` holds v1, `notes/licence.md` holds MIT. Answer each "
    "proposal with whatever asserts that, then generate the workflow."
)
ACCEPTANCE = "yes, run it"

# Steps stating their end state in English and asserting nothing, which is the ordinary case
# rather than an edge one ([plan/assertions.py]). Each names one backticked path for the
# proposal rule to draw a candidate from; a document naming no artefact would be offered
# nothing, answered unaided, and measured over a denominator of zero.
#
# **Three rather than one, because both of this case's numbers are rates over steps.**
# `authoring_acceptance` is taken over the offers Cairn made and `divergence_rate` over the
# gates that closed, and one step published both as 0/1 — which cannot tell a rule that never
# lands from one that landed once, and is not the recorded number doc 17 asks for in place of
# an open question.
#
# **They are independent on purpose.** A step behind a halt is recorded `not_reached` and
# leaves the divergence denominator, so a chain of three would widen the plan and quietly
# publish a rate over one again the first time an early step failed.
PLAN_DOCUMENT = """# Skill end to end

## Steps

1. **Ready marker** — bring `notes/ready.md` to a state where it holds the single word
   ready.

2. **Version stamp** — bring `notes/version.txt` to a state where it holds the single word
   v1.

3. **Licence note** — bring `notes/licence.md` to a state where it holds the single word
   MIT.
"""

# Counted off the document rather than declared beside it. The ladder prices one session per
# step, and a step added above with no session priced for it does not refuse early — it meets
# `Ledger.claim` inside the run, with the whole conversation already paid for.
PLAN_STEPS = len(re.findall(r"^\d+\. \*\*", PLAN_DOCUMENT, re.MULTILINE))

# The procedure's own order ([capabilities/authoring.md]), as the commands a transcript can
# show. `plan derive` is reading rather than a command, and `plan answer` runs only where a
# proposal was accepted, so neither is required here.
AUTHORING_ORDER: tuple[str, ...] = (
    "plan validate",
    "plan report",
    "plan propose",
    "workflow author",
)

# The relay grader: the printed disclosure and what the session said in, one frozen token
# out — priced like the reading sweep's grader, the cheapest session in the suite. It runs
# in the harness root, where no project settings and no skill exist to join the
# conversation, and its ceiling is stated like every other session's because an unbounded
# session is not a case.
RELAY_JUDGE_CEILING_USD = 0.20
RELAY_JUDGE_BOUNDS = Bounds(turns=2, budget_usd=RELAY_JUDGE_CEILING_USD, seconds=180.0)

# What [capabilities/authoring.md] step 1 names, inside the repository's own admin directory
# rather than its working tree. A free test holds this to the instruction a session reads.
GRAPH_FILE = "graph.json"


def opening(repository: Path) -> str:
    return invoke(f"turn .planning/{PLAN_SLUG}/{PLAN_INDEX} into a workflow for {repository}")


def ordered(commands: list[str]) -> bool:
    """Whether the four authoring commands appear in the order the procedure states."""
    seen = [command for command in commands if command in AUTHORING_ORDER]
    first: list[str] = []
    for command in seen:
        if command not in first:
            first.append(command)
    return first == [command for command in AUTHORING_ORDER if command in first]


def relay_evidence(harness: Harness, repository: Path) -> list[dict[str, Any]]:
    """Every offer this conversation minted, with the sentences `run offer` printed for it.

    Read from the repository's own offer records rather than from the transcript: the offer
    file keeps the cost the command printed verbatim ([cairn/skill/consent.py]
    `disclosure`), and the judged claim is about whether that text reached the person.
    Scrubbed once, because the text the grader reads is the text the line keeps. An offer
    that cannot be read is carried with no sentences — inventing them would put this file's
    opinion where the evidence goes.
    """
    evidence: list[dict[str, Any]] = []
    for offer_id in offers_of(repository):
        try:
            offer = read_offer(repository, offer_id)
        except CairnError:
            offer = None
        evidence.append(
            {
                "offer": offer_id,
                "cost": None
                if offer is None
                else [harness.scrub(sentence) for sentence in offer.cost],
            }
        )
    return evidence


def relayed(
    harness: Harness,
    probe: Probe,
    evidence: list[dict[str, Any]],
    said: str,
) -> tuple[dict[str, Any], Turn | None]:
    """Whether the printed price reached the person unsummarised — a judge's verdict.

    The claim is about meaning, and containment failed it in both directions — a session
    that paraphrased the price faithfully carries no stem, and one that quoted a stem while
    summarising away the dollars passes — so a grader reads what the session said against
    what `run offer` printed and answers with a frozen token. Both texts travel on the
    line, so the verdict is re-takeable from the record it carries. No offer minted means
    nothing was printed to relay, and no judge is bought over evidence that does not exist.
    """
    sentences = [
        sentence for one in evidence for sentence in (one["cost"] or ())
    ]
    field: dict[str, Any] = {"offers": evidence, "said": said}
    if not sentences or not said.strip():
        return {**field, "verdict": None, "judge": None}, None
    turn = harness.session(
        relay_prompt(tuple(sentences), said),
        cwd=harness.root,
        variables=probe.variables,
        bounds=RELAY_JUDGE_BOUNDS,
        role=ROLE_SESSION,
    )
    verdict = relay_of(turn.seen.account)
    return {
        **field,
        "verdict": verdict,
        "judge": {
            "session_id": turn.seen.session_id,
            "cost_usd": turn.seen.cost_usd,
            # The grader's own words, kept where its answer was not a token: a null
            # verdict is useless without the text that defeated the parse.
            "said": None
            if verdict is not None
            else bounded(harness.scrub(turn.seen.account)),
        },
    }, turn


def authoring_cause(seen: Observed, *, definition: bool) -> str | None:
    """Authoring's own end state is a definition, and the offer is the next turn's.

    Measured: a correct session authored, said so, and asked whether to run it without
    minting an offer — which [SKILL.md] permits, because an unambiguous run instruction in
    reply is itself the acceptance of the offer made to it, and the two may share a turn.
    """
    if seen.subtype is None:
        return CAUSE_NOTHING_OBSERVED
    if not definition:
        return CAUSE_PROCEDURE_ABANDONED
    return None


def acceptance_cause(
    seen: Observed,
    *,
    words: str,
    started: list[str],
    accepted: list[str],
) -> str | None:
    """Whether the words that started a run were the person's own.

    Asked of the repository first and the transcript second, because the repository is where
    the answer actually lives: `consent.spend` records what it was accepted with, and a run
    that exists with an acceptance beside it happened *because of those words* whatever a
    transcript did or did not show. Measured: a real accepting turn started a run that landed
    green while no `run start` reached the observer, and a check reading only the transcript
    called that a session which asked — reporting the tool's blindness as the model's.

    Only where the repository has nothing to say does the transcript decide, and then a run
    that exists with no acceptance recorded anywhere is the one thing that must not be read
    as a quiet pass: it is this suite unable to answer its own question.
    """
    if seen.subtype is None:
        return CAUSE_NOTHING_OBSERVED
    if accepted:
        return (
            None
            if all(one.strip() == words.strip() for one in accepted)
            else CAUSE_CONSENT_OVERRIDDEN
        )
    quoted = [
        reply_of(one) for one in seen.invocations if one.command == "run start"
    ]
    if quoted:
        if any(one is None or one.strip() != words.strip() for one in quoted):
            return CAUSE_CONSENT_OVERRIDDEN
        return None if started else CAUSE_ASKED_WHERE_EXPECTED_TO_ACT
    if started:
        # A run exists and nothing anywhere says what authorised it. Not a misread — a hole.
        return CAUSE_CONSENT_UNREADABLE
    return CAUSE_ASKED_WHERE_EXPECTED_TO_ACT


def verdict_cause(
    record: RunRecord | None, *, landed: bool, started: bool = True
) -> str | None:
    """A run is for what it lands, so a green verdict over an empty branch is not one.

    A run that was never started is the accepting turn's failure and is already recorded
    there; calling it an unreadable record would blame the tool for what the model did, and
    a tool fault outranks a model one in the exit code.
    """
    if not started:
        return CAUSE_ASKED_WHERE_EXPECTED_TO_ACT
    if record is None:
        return CAUSE_RECORD_UNREADABLE
    if record["verdict"] not in (VERDICT_GREEN, VERDICT_ALL_NO_OP):
        return CAUSE_VERDICT_UNEXPECTED
    if not landed:
        return CAUSE_VERDICT_UNEXPECTED
    return None


def derived_graph(repository: Path) -> Graph | None:
    """The graph the session derived, read from where the procedure says it goes.

    [capabilities/authoring.md] step 1 names `<repository>/.git/cairn/graph.json` and gives
    the reason: a run's first act refuses over a dirty tree, so a graph left in the working
    tree stops the very run this authoring is for. Any name in that directory is read, not
    only the one the instruction spells — the number is about the answers, and a session
    that called the file something else answered them all the same.
    """
    admin = state_directory(repository)
    named = admin / GRAPH_FILE
    for path in (named, *sorted(one for one in admin.glob("*.json") if one != named)):
        loaded = _loaded(path)
        if loaded is not None and isinstance(loaded.get("steps"), list):
            return cast(Graph, loaded)
    return None


def _loaded(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None


def offered_assertions(graph: Graph | None) -> list[Assertion]:
    """Every answer given to an offer Cairn actually made.

    A step whose answer carries no proposal is a command its author wrote unaided —
    `authored`, in [plan/assertions.py]'s own word — and counting it would put the rule's
    silences into a rate about its offers. A graph that cannot be read offers none.
    """
    answered: list[Assertion] = []
    for step in graph["steps"] if graph is not None else []:
        assertion = step["assertion"]
        if assertion is not None and assertion["proposed"] is not None:
            answered.append(assertion)
    return answered


def acceptances(graph: Graph | None) -> tuple[int, int]:
    """Numerator and denominator for authoring acceptance, over one derived graph."""
    offered = offered_assertions(graph)
    return sum(1 for one in offered if one["outcome"] == OUTCOME_ACCEPTED), len(offered)


def answers(graph: Graph | None) -> list[dict[str, Any]]:
    """What was offered for each step and what was recorded, beside the rate itself.

    A rate of 0 over 1 says an author edited; only the two commands say what the rule
    offered and what the words actually needed, which is the thing the rule is changed on.
    """
    return [
        {
            "step": step["id"],
            "outcome": None if step["assertion"] is None else step["assertion"]["outcome"],
            "proposed": None if step["assertion"] is None else step["assertion"]["proposed"],
            "verify": step["verify"],
        }
        for step in (graph["steps"] if graph is not None else [])
    ]


def dirty_paths(repository: Path) -> list[str]:
    """What is uncommitted, which is what `lock acquire` refuses a run over."""
    listed = git(repository, ("status", "--porcelain"), check=False)
    return [line[3:] for line in listed.stdout.splitlines() if line.strip()][:20]


def runs_of(repository: Path) -> list[str]:
    root = runs_root(repository)
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def accepted_words(repository: Path) -> list[str]:
    """What every spent offer in this repository was accepted with.

    The repository's own answer to "who authorised this run, and saying what" — read from
    the acceptance `consent.spend` records rather than from the session that typed it.
    """
    return [
        found.reply
        for offer in offers_of(repository)
        if (found := acceptance_of(repository, offer)) is not None
    ]


def offers_of(repository: Path) -> list[str]:
    directory = offers_directory(repository)
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def landed_on_parent(repository: Path) -> bool:
    """Whether the run's work is on `main`, which is the only thing a run is for."""
    listed = git(repository, ("ls-tree", "-r", "--name-only", "main"), check=False)
    return "notes/ready.md" in listed.stdout.splitlines()


def run(harness: Harness) -> None:
    with TemporaryDirectory(dir=str(harness.root)) as temporary:
        probe = build(
            Path(temporary),
            with_provider=True,
            with_plans=False,
            # The one bound the emitter cannot carry: `emit_agent` writes no `--model`, so
            # the step's session is held to the cheap model through the environment the
            # engine hands it.
            extra={"ANTHROPIC_MODEL": harness.model_for(ROLE_STEP)},
        )
        write_plans(probe.repository)
        directory = probe.repository / ".planning" / PLAN_SLUG
        directory.mkdir(parents=True, exist_ok=True)
        (directory / PLAN_INDEX).write_text(PLAN_DOCUMENT, encoding="utf-8")
        # Committed before the first turn. A run's first act refuses over a dirty tree —
        # correctly, because a run commits — so a plan document left uncommitted by the
        # harness would stop the run and read as the skill's fault rather than the probe's.
        commit_all(probe.repository, "the plan")

        first = harness.session(
            opening(probe.repository),
            cwd=probe.repository,
            variables=probe.variables,
            bounds=OPENING_BOUNDS,
            role=ROLE_SESSION,
        )
        # The procedure's own third step shows the parse report **and waits**: the author's
        # confirmation is what makes the graph the plan's rather than the derivation's
        # ([capabilities/authoring.md]). Measured: a correct session stops there, so a case
        # that expected a definition after one turn reddened over the rules being followed.
        second = harness.session(
            CONFIRMATION,
            cwd=probe.repository,
            variables=probe.variables,
            bounds=OPENING_BOUNDS,
            role=ROLE_SESSION,
            resume=first.started.session_id,
        )
        # The step after the confirmation offers a command for the one end state the plan
        # states and does not assert, and waits again — the answer is the author's. This
        # turn is that answer, and the graph it lands in is where the rate is read from.
        third = harness.session(
            ASSERTION,
            cwd=probe.repository,
            variables=probe.variables,
            bounds=OPENING_BOUNDS,
            role=ROLE_SESSION,
            resume=first.started.session_id,
        )
        conversation = (first, second, third)
        definition = workflow_path(probe.repository, PLAN_SLUG).is_file()
        offers = offers_of(probe.repository)
        commands = [
            one.command for turn in conversation for one in turn.seen.invocations
        ]
        graph = derived_graph(probe.repository)
        accepted, offered = acceptances(graph)
        _record(
            harness,
            "authoring",
            third,
            authoring_cause(third.seen, definition=definition),
            expected={"definition": True},
            observed={
                "definition": definition,
                "offers": len(offers),
                "assertions_offered": offered,
                "assertions_accepted": accepted,
            },
            detail={
                "invocations": [
                    one for turn in conversation for one in invoked(turn.seen)
                ],
                "authoring_order_kept": ordered(commands),
                "skills": [
                    opened for turn in conversation for opened in turn.seen.skills
                ],
                "turns_asking": first.seen.turns,
                # What was offered for each step and what was recorded. Without the two
                # commands beside each other, a rate of 0 over 1 says only that somebody
                # edited something.
                "assertions": answers(graph),
                # A graph the procedure's own path does not hold is a denominator of
                # nothing, and a reader would otherwise have to guess which it was.
                "graph_derived": graph is not None,
            },
            # The unit spans three sessions — the request, the confirmation the procedure
            # waits for, and the answer it waits for after that — so its price is all
            # three. A line carrying only the last would leave money the ledger charged
            # with no line to show for it.
            cost_usd=_spent(*conversation),
            seconds=sum(turn.started.seconds for turn in conversation),
        )
        # Written here rather than at the end of the case: authoring acceptance is settled
        # the moment the graph is, and everything after this is a run that can die.
        harness.measure(NAME, Measurement(MEASUREMENT_AUTHORING, accepted, offered))

        began = time.monotonic()
        dirty = dirty_paths(probe.repository)
        fourth = harness.session(
            ACCEPTANCE,
            cwd=probe.repository,
            variables=probe.variables,
            bounds=ACCEPTING_BOUNDS,
            role=ROLE_SESSION,
            resume=first.started.session_id,
        )
        started = runs_of(probe.repository)
        accepted = accepted_words(probe.repository)
        # The relay is judged here rather than at authoring, because an unambiguous run
        # instruction lets the offer and the start share the accepting turn — measured: no
        # committed draw had minted an offer before it, so a field taken any earlier reads
        # the session's vocabulary and calls it a relay.
        said = harness.scrub(
            assistant_text(
                events(
                    "".join(
                        turn.started.transcript for turn in (*conversation, fourth)
                    )
                )
            )
        )
        relay, relay_judge = relayed(
            harness, probe, relay_evidence(harness, probe.repository), said
        )
        _record(
            harness,
            "acceptance",
            fourth,
            acceptance_cause(
                fourth.seen,
                words=ACCEPTANCE,
                started=started,
                accepted=accepted,
            ),
            expected={"reply": ACCEPTANCE, "runs": 1},
            observed={
                # What the repository says authorised the run, then what the transcript
                # showed. The first is the answer; the second is kept because a difference
                # between them is this instrument losing a command, and the pair is what
                # says so.
                "accepted": accepted,
                "replies": [
                    reply_of(one)
                    for one in fourth.seen.invocations
                    if one.command == "run start"
                ],
                "runs": len(started),
            },
            detail={
                "invocations": invoked(fourth.seen),
                "price_relayed": relay,
                # What authoring left behind. A run's first act refuses over a dirty tree,
                # and the graph belongs outside it — so this names the files that decide
                # whether the run can start at all.
                "dirty_before_start": dirty,
                "unreadable": [
                    harness.scrub(one) for one in fourth.seen.unreadable
                ],
            },
            # The accepting turn and the relay grader that reads what it said — a line
            # carrying only the turn would leave the grader's money with no line to show
            # for it.
            cost_usd=_spent(fourth, *((relay_judge,) if relay_judge else ())),
        )

        record = record_of(probe.repository, started[-1]) if started else None
        landed = landed_on_parent(probe.repository)
        step = _only_step(record)
        harness.charge_engine(
            ROLE_STEP, None if step is None else step["cost_usd"], ceiling_usd=CEILING_USD
        )
        cause = verdict_cause(record, landed=landed, started=bool(started))
        harness.record(
            Unit(
                case=NAME,
                unit="verdict",
                ending=ending_of(cause is None),
                cause=cause,
                seconds=round(time.monotonic() - began, 3),
                role=ROLE_STEP,
                session_id=None if step is None else step["session_id"],
                cost_usd=None if step is None else step["cost_usd"],
                turns=None if step is None else step["turns"],
                model_resolved=None if step is None else step["model"],
                expected={"verdict": VERDICT_GREEN, "landed": True},
                observed={
                    "verdict": None if record is None else record["verdict"],
                    "landed": landed,
                },
                account=harness.scrub("" if step is None else str(step["said"] or "")),
                detail={
                    # `providers.py` records the model from argv, where `emit_agent` writes
                    # the plan's own pin (17.3) — so `model_resolved` above names what the
                    # definition asked for, not what this suite's environment pins its
                    # conversations to.
                    "step_model_source": "definition",
                    "runs": started,
                    # Why a run that was started did not land, in the record's own words.
                    # Without this a red verdict says only that it was red.
                    "steps": [
                        {"step": step["step_id"], "outcome": step["outcome"],
                         "cause": step["cause"],
                         "said": harness.scrub(str(step["said"] or ""))[:200]}
                        for step in steps_of(record)
                    ],
                    "attention": [
                        {"kind": one["kind"], "subject": one["subject"],
                         "cause": one["cause"]}
                        for one in (record["attention"] if record is not None else [])
                    ],
                },
            )
        )
        numerator, denominator = divergences(record)
        harness.measure(
            NAME, Measurement(MEASUREMENT_DIVERGENCE, numerator, denominator)
        )


def _spent(*turns: Turn) -> float | None:
    """What a multi-turn unit cost, or nothing where no turn reported a price."""
    priced = [turn.seen.cost_usd for turn in turns if turn.seen.cost_usd is not None]
    return round(sum(priced), 6) if priced else None


def _record(
    harness: Harness,
    unit: str,
    turn: Turn,
    cause: str | None,
    *,
    expected: Any,
    observed: Any,
    detail: dict[str, Any],
    cost_usd: float | None = None,
    seconds: float | None = None,
) -> None:
    harness.record(
        Unit(
            case=NAME,
            unit=unit,
            ending=ending_of(cause is None),
            cause=cause,
            seconds=turn.started.seconds if seconds is None else seconds,
            role=ROLE_SESSION,
            session_id=turn.seen.session_id,
            cost_usd=turn.seen.cost_usd if cost_usd is None else cost_usd,
            turns=turn.seen.turns,
            model_resolved=turn.seen.model,
            expected=expected,
            observed=observed,
            account=harness.scrub(turn.seen.account),
            detail={
                **detail,
                "timed_out": turn.started.timed_out,
                # Which commands the permission layer refused. A session stopped from
                # running one and a session that chose not to look identical in the command
                # list, and only one of them is a fact about the model.
                "permission_denials": list(turn.seen.permission_denials),
            },
        )
    )


def _only_step(record: RunRecord | None) -> StepRecord | None:
    """This plan has exactly one step, so its receipts are the run's."""
    steps = steps_of(record)
    return steps[0] if steps else None


__all__ = [
    "CEILING_USD",
    "GRAPH_FILE",
    "MEASURED_USD",
    "NAME",
    "acceptance_cause",
    "acceptances",
    "accepted_words",
    "answers",
    "authoring_cause",
    "ceilings",
    "derived_graph",
    "divergences",
    "offered_assertions",
    "ordered",
    "relay_evidence",
    "relayed",
    "run",
    "verdict_cause",
]


def ceilings() -> list[float]:
    """A ceiling for every session this case may open, which is what the ladder prices.

    Eight: four turns of conversation, one step session for each of the plan's three steps
    inside the run the fourth turn starts, and the relay grader that reads the printed
    price against what the session said. The ladder prices what a case opens, not only
    what the harness launches, so a step added to the plan is a session added here.
    """
    return [CEILING_USD] * (4 + PLAN_STEPS) + [RELAY_JUDGE_CEILING_USD]
