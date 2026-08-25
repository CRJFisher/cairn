"""The one opt-in gate, and the ladder of refusals in front of the first dollar.

One gate rather than two: `scripts/record_runs.py` is a client of this one and holds none of
its own. The suite that spends is the natural owner — pointing the dependency the other way
would make the paid suite import its front door from a fixture-recording script.

The ladder's shape is the point. Every refusal happens before any subprocess that costs
anything, and each rung answers a different way to spend money by accident: not meaning to
at all, a case nobody bounded, a selection whose total nobody looked at, and a loop that
opens sessions faster than anyone counted.

The last rung is a capability rather than a check. `Ledger.claim` is the only source of a
`Launch`, and a session cannot be started without one — so a runaway loop meets the cap after
one extra session rather than after seven hundred. A check that a caller must remember to
call is a check that a caller can forget.
"""

from __future__ import annotations

import os
import sys
from typing import NamedTuple

from paid.vocabulary import PAID_OPT_IN


class Refused(SystemExit):
    """Nothing ran, nothing spent, and the file is exactly as it was found."""


class Launch(NamedTuple):
    """Permission to start exactly one session, at a stated ceiling.

    Constructed only by `Ledger.claim`, which is what makes the session count a property of
    the ledger rather than of whoever remembered to increment it.
    """

    ordinal: int
    role: str
    ceiling_usd: float


class Commitment(NamedTuple):
    """What one case commits: a ceiling for every session it may open, and its recollection.

    A ceiling per session rather than one per case, because a case whose sessions are not
    alike cannot be priced by a single number times a count. The reading sweep's acting
    probes want more room than its asking ones, and a case that had to state one figure for
    both would either cut the dear probes off or commit the cheap ones to money they have
    never spent.
    """

    name: str
    ceilings: tuple[float, ...]
    measured_usd: float


class Priced(NamedTuple):
    sessions: int
    committed_usd: float
    measured_usd: float


def price(units: list[Commitment]) -> Priced:
    """What the selection commits to, and what it cost last time.

    Two numbers on purpose. `committed_usd` is arithmetic over declared ceilings and is what
    the ladder refuses on; `measured_usd` is a recollection and is what the notice prints. A
    suite bounded by a recollection would be bounded by whatever the cheapest model of last
    month happened to do.
    """
    sessions = sum(len(one.ceilings) for one in units)
    committed = sum(sum(one.ceilings) for one in units)
    measured = sum(one.measured_usd for one in units)
    return Priced(sessions, round(committed, 4), round(measured, 4))


def refuse_unpaid(units: list[str], *, opted_in: bool, measured_usd: float) -> list[str]:
    """Refuse to spend without being asked, and say what it will cost before spending it.

    The free suite is a gate: it runs on every change and it must cost nothing. A session is
    neither deterministic nor free, so the two are separated by making the paid thing
    unreachable from the obvious command rather than by hoping nobody types it.
    """
    if not units:
        return units
    if not opted_in:
        raise Refused(
            f"{', '.join(units)} runs real agent sessions and costs about "
            f"${measured_usd:.2f} at the last measurement. Re-run with --paid and "
            f"{PAID_OPT_IN}=1 set if that is what you meant; everything else here is free."
        )
    print(
        f"spending on {len(units)} unit(s): about ${measured_usd:.2f} at the last "
        f"measurement",
        file=sys.stderr,
    )
    return units


def refuse_unbounded(units: list[Commitment]) -> None:
    """A case that declares no per-session ceiling is not a case.

    An unbounded session is the one thing this suite cannot price before it runs, and a
    price nobody could state is the thing the whole ladder exists to prevent. Every ceiling
    a case declares rather than one of them: a case whose sessions differ can bound most of
    them and leave one open, and that one is the session that spends the afternoon.
    """
    unbounded = [
        one.name for one in units if not one.ceilings or min(one.ceilings) <= 0
    ]
    if unbounded:
        raise Refused(
            f"{', '.join(unbounded)} declares no per-session budget. An unbounded session "
            "is not a case: it cannot be priced before it runs."
        )


def refuse_over_ceiling(committed_usd: float, ceiling_usd: float) -> None:
    if committed_usd > ceiling_usd:
        raise Refused(
            f"the selection commits up to ${committed_usd:.2f} and the run ceiling is "
            f"${ceiling_usd:.2f}. Raise --max-total-usd deliberately, or select fewer cases."
        )


def opted_in(paid_flag: bool, environment: dict[str, str] | None = None) -> bool:
    """Both the flag and the variable, because either alone is reachable by accident."""
    source = os.environ if environment is None else environment
    return paid_flag and source.get(PAID_OPT_IN) == "1"


class Ledger:
    """The only place a session can be started from, and the only place spend is counted.

    Bounded forward rather than in arrears: the ceiling is checked before each session
    against what has already been charged, so the suite stops with money left rather than
    discovering the overrun in the total at the end.
    """

    def __init__(self, *, ceiling_usd: float, sessions: int) -> None:
        self._ceiling_usd = ceiling_usd
        self._sessions = sessions
        self._claimed = 0
        self._spent_usd = 0.0

    @property
    def spent_usd(self) -> float:
        return round(self._spent_usd, 6)

    @property
    def claimed(self) -> int:
        return self._claimed

    def claim(self, role: str, ceiling_usd: float) -> Launch:
        if self._claimed >= self._sessions:
            raise Refused(
                f"the selection declared {self._sessions} session(s) and a "
                f"{self._claimed + 1}th was asked for. Something is looping; nothing "
                "further will be started."
            )
        if self._spent_usd + ceiling_usd > self._ceiling_usd:
            raise Refused(
                f"${self.spent_usd:.2f} spent and the next session commits up to "
                f"${ceiling_usd:.2f}, over the ${self._ceiling_usd:.2f} run ceiling. "
                "Stopping with the ceiling intact."
            )
        self._claimed += 1
        return Launch(self._claimed, role, ceiling_usd)

    def charge(self, usd: float | None, *, unpriced_usd: float) -> None:
        """What a session cost, or what it was allowed to cost when it cannot be read.

        A session with no terminal result is charged its whole ceiling. That is the session
        most likely to have spent it — one the wall clock killed after running the longest —
        and charging it zero would leave the forward bound blindest exactly when the run is
        going worst.
        """
        self._spent_usd += unpriced_usd if usd is None else usd
