"""`python3 -m paid` — the suite that spends money, and the only way to start it.

Not reachable from `python3 -m unittest discover`, by three independent facts the free suite
asserts by running the loader rather than by claiming it: nothing here matches `test*.py`,
nothing here is under the tests directory, and the loader collects nothing from `paid/`.

    python3 -m paid --price-only                       # what a run would commit, for nothing
    CAIRN_PAID=1 python3 -m paid --paid                # every case, cheapest first
    CAIRN_PAID=1 python3 -m paid --paid --case merge-resolution

**The order is cheapest first, and it is load-bearing.** A run that is going to stop because
the provider is unreachable, the model is aliased or nothing can be observed should discover
that on one conversation rather than after the merge session has been paid for.

**Four exit codes, and two of them are red.** `record/vocabulary.py`'s own numbers rather
than a second set: 0 when every unit reached its end state, 1 when something the tool does
was wrong, 3 when a model was worse than the case expected, and 4 when the run was refused or
aborted without a verdict. The pair of red codes is not a softening of everything-red — both
are red — it is the record's own classification made visible to whatever runs the suite.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

from cairn.core import CairnError
from cairn.marker import mint_occasion
from paid.cases import consent, differentiating, merge, reading, skill
from paid.harness import Aborted, Harness, Taken
from paid.measure import (
    Journal,
    Models,
    Unit,
    Unpublishable,
    end_line,
    run_line,
    unit_line,
)
from paid.probes import PACKAGE_ROOT, versions
from paid.spend import (
    Commitment,
    Ledger,
    Refused,
    opted_in,
    price,
    refuse_over_ceiling,
    refuse_unbounded,
    refuse_unpaid,
)
from paid.vocabulary import (
    CASES,
    CAUSE_PROVIDER_MISSING,
    CAUSE_RECORD_UNREADABLE,
    ENDING_ABORTED,
    ENDING_MISSED,
    ENDING_REACHED,
    EXIT_ALL_REACHED,
    EXIT_MODEL_QUALITY,
    EXIT_REFUSED,
    EXIT_TOOL_DEFECT,
    FAULT_ENVIRONMENT,
    FAULT_MODEL,
    FAULT_TOOL,
    MODEL_DEFAULT,
    MODEL_NAME,
    PAID_OPT_IN,
)

# Cheapest first, by what each has actually cost: a conversation, a run, a merge, the whole
# skill end to end, and last the sweep, which is worth more than everything before it together.
ORDER: tuple[ModuleType, ...] = (consent, differentiating, merge, skill, reading)

RECORD_PATH = PACKAGE_ROOT / "paid" / "measurements.jsonl"

# The worst case rather than the expectation: every declared ceiling, taken together, rounded
# up. The notice prints both, because a run that commits up to this and costs a fifth of it is
# the ordinary case and a person should see which number is which. It moves when the cases'
# own ceilings move — a default that stayed put would quietly become the thing that bounds the
# suite, in place of the per-session ceilings each case declares in the open. Five of every
# ask case rather than one is what put it here — 181 asking probes at $0.70 is most of it —
# and the grader sessions the reading sweep buys beside its probes added $53 of ceiling.
#
# The gap between this and the sweep's actual cost is wide on purpose — $43 spent against
# $242 committed, at the measurement that set the last figure. A ceiling is refused against
# before the first call and never paid, so headroom costs nothing and a ceiling reached
# mid-sweep costs the wall clock of everything before it.
DEFAULT_CEILING_USD = 400.0


def selected(names: list[str] | None) -> list[ModuleType]:
    """The chosen cases, always in the run order rather than in the order they were typed."""
    chosen = set(names) if names else set(CASES)
    return [module for module in ORDER if module.NAME in chosen]


def models_of(args: argparse.Namespace) -> Models:
    """The three roles' models, each held to the shape a model id has.

    Checked here rather than trusted: the run line is written before the first session, so an
    unvalidated `--model` would land a sentence in a trend field that every later line is
    keyed on, and the alias probe cannot fire until a session answers.
    """
    models = Models(
        session=str(args.session_model or args.model),
        step=str(args.step_model or args.model),
        merge=str(args.merge_model or args.model),
    )
    for named in models:
        if not MODEL_NAME.match(named):
            raise Refused(f"{named!r} is not a model id, and every line is keyed on one")
    return models


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paid", description=__doc__)
    parser.add_argument(
        "--paid",
        action="store_true",
        help=f"run the cases that spend money; also needs {PAID_OPT_IN}=1",
    )
    parser.add_argument("--case", action="append", choices=sorted(CASES))
    parser.add_argument(
        "--unit",
        action="append",
        help="one corpus case id, repeatable: re-take a probe without buying the sweep",
    )
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--session-model")
    parser.add_argument("--step-model")
    parser.add_argument("--merge-model")
    parser.add_argument("--max-total-usd", type=float, default=DEFAULT_CEILING_USD)
    parser.add_argument("--out", default=str(RECORD_PATH))
    parser.add_argument(
        "--price-only",
        action="store_true",
        help="print what the selection commits to and stop, having spent nothing",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(None if arguments is None else list(arguments))
    cases = selected(args.case)
    if not cases:
        print("no case selected", file=sys.stderr)
        return EXIT_REFUSED
    try:
        return _run(args, cases)
    except Refused as refused:
        print(f"refused  {refused}", file=sys.stderr)
        return EXIT_REFUSED
    except Unpublishable as refused_line:
        # The record refusing a line is the one failure that can reach here after the money
        # has moved, so it ends on an exit code rather than a traceback.
        print(f"refused  {refused_line}", file=sys.stderr)
        return EXIT_REFUSED
    except CairnError as unusable:
        # A missing provider, a missing engine, a probe that could reach one it must not.
        # None of these is a verdict about anything, so none of them borrows a red code.
        print(f"refused  {unusable}", file=sys.stderr)
        return EXIT_REFUSED


def _run(args: argparse.Namespace, cases: list[ModuleType]) -> int:
    # Before the ladder, because the run line is written before the first session and every
    # later line is keyed on these three names.
    models = models_of(args)
    commitments = [
        Commitment(module.NAME, tuple(module.ceilings()), module.MEASURED_USD)
        for module in cases
    ]
    refuse_unbounded(commitments)
    priced = price(commitments)
    refuse_over_ceiling(priced.committed_usd, args.max_total_usd)
    if args.price_only:
        print(
            f"{len(cases)} case(s), {priced.sessions} session(s), committing up to "
            f"${priced.committed_usd:.2f} against a ${args.max_total_usd:.2f} ceiling; "
            f"about ${priced.measured_usd:.2f} at the last measurement"
        )
        return EXIT_ALL_REACHED
    refuse_unpaid(
        [module.NAME for module in cases],
        opted_in=opted_in(args.paid),
        measured_usd=priced.measured_usd,
    )

    run_id = mint_occasion()
    with TemporaryDirectory(prefix="cairn-paid-") as temporary:
        root = Path(temporary)
        journal = Journal(Path(args.out), home=str(Path.home()), temporary=str(root))
        journal.write(
            run_line(
                run=run_id,
                models=models,
                cases=[module.NAME for module in cases],
                sessions=priced.sessions,
                committed_usd=priced.committed_usd,
                ceiling_usd=args.max_total_usd,
                versions=versions(),
            )
        )
        harness = Harness(
            run_id=run_id,
            root=root,
            home=str(Path.home()),
            models=models,
            ledger=Ledger(ceiling_usd=args.max_total_usd, sessions=priced.sessions),
            journal=journal,
        )
        began = time.monotonic()
        # Aborted until something says otherwise, because the closing line is written in a
        # `finally` and the run can end in ways no handler here catches — a person stopping a
        # four-hour sweep, above all. Initialised green, every one of those closes on a line
        # claiming the run reached its end state, and the record is committed.
        ending = ENDING_ABORTED
        code = EXIT_REFUSED
        running = cases[0].NAME
        try:
            for module in cases:
                running = module.NAME
                if module is reading and args.unit:
                    module.run(harness, units=args.unit)
                else:
                    module.run(harness)
        except (
            Aborted,
            Refused,
            CairnError,
            # A line the record refused to write. It reaches here rather than escaping as a
            # traceback because the money is already spent by the time one is raised, and a
            # run that dies without a closing line is one nobody can tell from a killed one.
            Unpublishable,
            OSError,
            subprocess.SubprocessError,
        ) as stopped:
            # Everything a case can raise ends the run the same way: one classified line for
            # the unit that was in flight, and an exit code that says no verdict was reached
            # rather than one that says the tool was wrong.
            cause = cause_of(stopped)
            journal.write(
                unit_line(
                    Unit(
                        case=running,
                        unit="run",
                        ending=ENDING_ABORTED,
                        cause=cause,
                        seconds=round(time.monotonic() - began, 3),
                        observed=str(stopped)[:400],
                    ),
                    run=run_id,
                    models=models,
                )
            )
            print(f"aborted  {stopped}", file=sys.stderr)
        else:
            code = run_verdict(journal, harness.taken, spent=harness.ledger.spent_usd)
            ending = ENDING_REACHED if code == EXIT_ALL_REACHED else ENDING_MISSED
        finally:
            journal.write(
                end_line(
                    run=run_id,
                    ending=ending,
                    exit_code=code,
                    units=journal.units,
                    reached=journal.reached,
                    spent_usd=harness.ledger.spent_usd,
                    seconds=time.monotonic() - began,
                    allowances=harness.allowances or None,
                )
            )
        return code



def cause_of(stopped: BaseException) -> str:
    """What ended the run, in the record's own causes.

    A line the record refused is the record being wrong about what it can hold, and reporting
    it as a missing provider would send the next reader to the machine rather than to the
    field that could not be published.
    """
    if isinstance(stopped, Aborted):
        return stopped.cause
    if isinstance(stopped, Unpublishable):
        return CAUSE_RECORD_UNREADABLE
    return CAUSE_PROVIDER_MISSING


def run_verdict(journal: Journal, measurements: Taken, *, spent: float) -> int:
    for case, measurement in measurements:
        value = "—" if measurement.value is None else f"{measurement.value:.3f}"
        print(
            f"{case:20} {measurement.name:20} "
            f"{measurement.numerator}/{measurement.denominator}  {value}",
            file=sys.stderr,
        )
    print(
        f"{journal.reached} of {journal.units} unit(s) reached; about ${spent:.2f} spent",
        file=sys.stderr,
    )
    if FAULT_ENVIRONMENT in journal.faults:
        return EXIT_REFUSED
    if FAULT_TOOL in journal.faults:
        return EXIT_TOOL_DEFECT
    if FAULT_MODEL in journal.faults:
        return EXIT_MODEL_QUALITY
    return EXIT_ALL_REACHED


if __name__ == "__main__":
    raise SystemExit(main())
