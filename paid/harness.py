"""The seam between the runner and a case: one place a session can be started from.

Every paid case reaches a session through `Harness.session`, and that is the whole point of
the type existing. Claiming from the ledger, launching under the claim, charging what came
back and recording which role paid are four steps that belong together; a case that had to
remember all four would eventually remember three.

A case's numbers go the same way, through `Harness.measure`. A case can settle a number long
before it finishes — authoring acceptance is decided while the run it precedes has not
started — so a number handed back at the end is a number a killed run loses after paying for
it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from paid.measure import (
    Journal,
    Measurement,
    Models,
    Unit,
    measurement_line,
    scrub,
    unit_line,
)
from paid.observe import Observed, observe
from paid.session import Bounds, Started, run
from paid.spend import Ledger
from paid.vocabulary import CAUSE_MODEL_ALIASED, ROLE_MERGE, ROLE_SESSION, ROLE_STEP


class Aborted(Exception):
    """The run stops without a verdict, because the environment made one impossible.

    Not a red run. A rate over half a population is a lie about the population, so an
    environment fault ends the run rather than reddening it — and the record says the units
    were aborted rather than missed.
    """

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


class Turn(NamedTuple):
    """One session, as it was started and as it came back."""

    started: Started
    seen: Observed


Taken = list[tuple[str, Measurement]]
Allowances = dict[str, dict[str, int]]


@dataclass(frozen=True)
class Harness:
    """What every case is given: an identity, the models, a purse and a journal."""

    run_id: str
    root: Path
    home: str
    models: Models
    ledger: Ledger
    journal: Journal
    taken: Taken = field(default_factory=Taken)
    # What each bounded allowance a case declares actually spent, carried to the line that
    # closes the run. A rate taken with a spent allowance and one taken with room left are
    # not the same measurement, and the closing line is where a reader comparing two runs
    # looks — no per-unit line can say what the sweep as a whole had left.
    allowances: Allowances = field(default_factory=Allowances)

    def record(self, unit: Unit) -> None:
        """One unit's line, stamped with this run's identity and its three models.

        And one line to whoever is watching. A run is an hour or two of silence otherwise,
        and a person who cannot see it working cannot tell it from a run that has hung.
        """
        self.journal.write(unit_line(unit, run=self.run_id, models=self.models))
        print(
            f"{unit.case:20} {unit.unit:34} {unit.ending:8} {unit.cause or '':26} "
            f"${unit.cost_usd or 0:5.2f}  {unit.seconds:6.0f}s  "
            f"${self.ledger.spent_usd:.2f} so far",
            file=sys.stderr,
        )

    def measure(self, case: str, measurement: Measurement) -> None:
        """One number, written where it was taken rather than where its case ended.

        The skill case settles authoring acceptance before it starts the run whose
        divergences are its other number, and the run is the part that can die. Written
        here, the first number survives whatever kills the second.
        """
        self.journal.write(
            measurement_line(measurement, run=self.run_id, case=case, models=self.models)
        )
        self.taken.append((case, measurement))

    def scrub(self, text: str) -> str:
        """The two paths a session's own prose can carry, taken back out of it."""
        return scrub(text, home=self.home, temporary=str(self.root))

    def charge_engine(self, role: str, usd: float | None, *, ceiling_usd: float) -> None:
        """Charge a session the engine opened, which the harness never launched.

        A merge slot and an agent step are paid sessions this suite caused and did not
        start, so the ledger only sees them through their receipts. Claiming and charging
        them here is what makes the run ceiling a bound on the run rather than on the
        conversations inside it.
        """
        self.ledger.claim(role, ceiling_usd)
        self.ledger.charge(usd, unpriced_usd=ceiling_usd)

    def model_for(self, role: str) -> str:
        return {
            ROLE_SESSION: self.models.session,
            ROLE_STEP: self.models.step,
            ROLE_MERGE: self.models.merge,
        }[role]

    def session(
        self,
        prompt: str,
        *,
        cwd: Path,
        variables: dict[str, str],
        bounds: Bounds,
        role: str = ROLE_SESSION,
        resume: str | None = None,
    ) -> Turn:
        """Claim, launch, observe, charge — in that order, and never any other."""
        requested = self.model_for(role)
        token = self.ledger.claim(role, bounds.budget_usd)
        started = run(
            token,
            prompt,
            cwd=cwd,
            model=requested,
            variables=variables,
            bounds=bounds,
            resume=resume,
        )
        seen = observe(started.transcript)
        self.ledger.charge(seen.cost_usd, unpriced_usd=bounds.budget_usd)
        # An alias that resolved to something else would put a different model's numbers in
        # a trend keyed on this one, and no reader could tell. Asked at the first session
        # that answers, and it ends the run rather than reddening it.
        if seen.model is not None and seen.model != requested:
            raise Aborted(
                CAUSE_MODEL_ALIASED,
                f"asked for {requested} and the provider ran {seen.model}. Every "
                "measurement is keyed on the model, so nothing further will be started.",
            )
        return Turn(started=started, seen=seen)
