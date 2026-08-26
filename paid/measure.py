"""The committed record: one line per unit, one per number, one per run.

Doc 17 task 5 asks for the numbers to be *kept* rather than printed, because a rate nobody
can compare to last month's is an anecdote. So every paid run appends a block to
`measurements.jsonl` — what it ran under, what each unit did, and what each measurement came
to — and the file is committed.

Three rules hold the record honest.

**A measurement carries its numerator and its denominator, never a bare rate.** `value` is
`None` where the denominator is zero rather than `0.0`, because a rate over nothing and a
rate of nothing are different facts and one of them is a lie.

**Every failure is classified, and the classification is what routes it.** `FAULT_BY_CAUSE`
is total over every cause the vocabulary declares, and `fault_of` refuses a cause it does not
hold — so a unit that missed its end state for a reason nobody classified cannot be written
at all. What a run reports is read off these lines and off nothing else ([verdict.py]): a
tool defect anywhere fails critical functionality, a model-quality miss fails it only where
it lands in one, and an environment fault takes the reading out of the rate rather than
reddening anything.

**Nothing personal is published.** Every string anywhere in a line is scrubbed by the writer
— the home directory it ran under and the temporary root it worked in — and
`assert_publishable` is the independent check that the scrub worked, run over every
serialised line before it is written. One transforms and one verifies, because a scrub
nobody checked is a scrub that silently stopped matching, and scrubbing in the writer rather
than at each call site means a field added later cannot bypass it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple, cast

from paid.redact import ACCOUNT_KEYS
from paid.vocabulary import (
    ENDING_ABORTED,
    ENDING_MISSED,
    ENDING_REACHED,
    ENDINGS,
    FAULT_BY_CAUSE,
    FAULT_ENVIRONMENT,
    GROUPS,
    MEASUREMENTS,
    POPULATION_BY_MEASUREMENT,
    ROLE_MERGE,
    ROLE_SESSION,
    ROLE_STEP,
    ROLES,
    SCHEMA_VERSION,
    SOURCE_BY_MEASUREMENT,
)

KIND_RUN = "run"
KIND_UNIT = "unit"
KIND_MEASUREMENT = "measurement"
KIND_END = "run_end"

HOME_MASK = "~"
TEMPORARY_MASK = "<tmp>"

# An API key and an email address are the two shapes a session's prose can carry that no
# amount of path rewriting would catch.
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_-]{4,}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# The recording machine's own account state. `paid/redact.py` takes these out of a fixture's
# step reports and out of a probe world; a measurement line has no such field, and the guard
# is here so that stays true rather than being assumed. Its names come from that module, so
# the scrub, the check that the scrub worked and this guard cannot drift apart.
FORBIDDEN_KEYS: tuple[str, ...] = ACCOUNT_KEYS

ACCOUNT_CHARACTERS = 400
TRUNCATION = "…"


class Unpublishable(Exception):
    """A line that would put something private in a committed file. Nothing is written."""


class Models(NamedTuple):
    """Which model each of the three roles ran on, recorded on every line.

    One field could not say that a run held the resolver fixed and moved the reading model,
    which is the question a person actually asks of a trend.
    """

    session: str
    step: str
    merge: str

    def as_record(self) -> dict[str, str]:
        # By name rather than by position: zipping against `ROLES` would relabel every
        # line in the trend if that tuple were ever reordered, and nothing would fail.
        return {ROLE_SESSION: self.session, ROLE_STEP: self.step, ROLE_MERGE: self.merge}


class Measurement(NamedTuple):
    """One number, with the population it was taken over."""

    name: str
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return round(self.numerator / self.denominator, 4)


class Unit(NamedTuple):
    """One thing the suite did, and how it ended.

    A unit is the smallest thing that can miss its end state on its own: one reading probe,
    one merge slot, one conversation. Every probe being its own line is what makes a red run
    readable as "71 of 76, and these five".
    """

    case: str
    unit: str
    ending: str
    cause: str | None
    seconds: float
    role: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    turns: int | None = None
    model_resolved: str | None = None
    expected: Any = None
    observed: Any = None
    account: str = ""
    detail: dict[str, Any] | None = None
    # Which draw of its case this line is, and how many that case declares. A case put to
    # one session says 1 of 1, which is true and costs nine bytes; the alternative is a key
    # that appears on some unit lines and not others, which is two shapes with one name.
    # Kept beside `expected` rather than in `detail` because a rate keys on it: the reading
    # rate counts first samples and the compliance rate counts every one, and a field a rate
    # keys on that lives in a free-form bag is a rate nobody can recompute from the file.
    sample: int = 1
    samples: int = 1


def fault_of(cause: str) -> str:
    """Which behaviour a failure indicts, or a refusal to write the line at all."""
    try:
        return FAULT_BY_CAUSE[cause]
    except KeyError as unclassified:
        raise Unpublishable(
            f"{cause!r} is not a cause this record can classify, so the failure it names "
            "cannot be written. Add it to FAULT_BY_CAUSE with the fault it indicts."
        ) from unclassified


def scrub(text: str, *, home: str, temporary: str) -> str:
    """Take the two paths a session's prose can carry back out of it.

    Masking only. Truncation is `bounded`'s, because a scrub that silently cut to 400
    characters made `stdout[-1200:]` a lie at every call site that asked for a window.
    """
    return " ".join(text.replace(temporary, TEMPORARY_MASK).replace(home, HOME_MASK).split())


def bounded(text: str, limit: int = ACCOUNT_CHARACTERS) -> str:
    """A field a session wrote, cut to a length a record can hold, and visibly cut.

    The marker matters: without it a cut field reads as a session that stopped mid-sentence,
    which is the one thing a reader of a red line most wants to tell apart.
    """
    return text if len(text) <= limit else text[: limit - 1] + TRUNCATION


def masked(value: Any, *, home: str, temporary: str) -> Any:
    """Every string anywhere in a line, scrubbed — including ones added later.

    Scrubbing at each call site is a rule a caller must remember; scrubbing here is a
    property of the record. Several fields reached the writer unscrubbed under the old
    arrangement — an engine's own stdout among them — and the guard's only answer would have
    been to end a paid run over a path it could have masked.
    """
    if isinstance(value, str):
        return scrub(value, home=home, temporary=temporary)
    if isinstance(value, dict):
        entries = cast(dict[str, Any], value)
        return {key: masked(item, home=home, temporary=temporary) for key, item in entries.items()}
    if isinstance(value, list):
        return [masked(item, home=home, temporary=temporary) for item in cast(list[Any], value)]
    return value


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        entries = cast(dict[str, Any], value)
        return [*entries, *(name for item in entries.values() for name in _keys(item))]
    if isinstance(value, list):
        return [name for item in cast(list[Any], value) for name in _keys(item)]
    return []


def assert_publishable(line: dict[str, Any], *, home: str, temporary: str) -> None:
    """The independent check that nothing private reached the committed file."""
    for key in FORBIDDEN_KEYS:
        # A key test rather than a substring one: a session that merely *said* the words
        # would otherwise end a paid run over its own prose.
        if key in _keys(line):
            raise Unpublishable(f"a line carries {key!r}, which is the machine's own state")
    body = json.dumps(line)
    for private, what in ((home, "the home directory"), (temporary, "the temporary root")):
        if private and private in body:
            raise Unpublishable(f"a line carries {what} it ran under: {private}")
    if SECRET.search(body):
        raise Unpublishable("a line carries something shaped like an API key")
    if EMAIL.search(body):
        raise Unpublishable("a line carries something shaped like an email address")


def run_line(
    *,
    run: str,
    models: Models,
    cases: list[str],
    sessions: int,
    committed_usd: float,
    ceiling_usd: float,
    versions: dict[str, str],
) -> dict[str, Any]:
    """What the run was, so a later reader can tell a model change from a code change."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_RUN,
        "run": run,
        "models": models.as_record(),
        "cases": cases,
        "sessions": sessions,
        "committed_usd": round(committed_usd, 4),
        "ceiling_usd": round(ceiling_usd, 4),
        "versions": versions,
    }


def unit_line(unit: Unit, *, run: str, models: Models) -> dict[str, Any]:
    """One unit's whole account, with its ending and its cause held to each other."""
    if unit.ending not in ENDINGS:
        raise Unpublishable(f"{unit.ending!r} is not an ending this record knows")
    if (unit.ending == ENDING_REACHED) != (unit.cause is None):
        raise Unpublishable(
            f"{unit.case}/{unit.unit} ended {unit.ending} with cause {unit.cause!r}: an "
            "end state is reached with no cause, and missed with exactly one"
        )
    if unit.role is not None and unit.role not in ROLES:
        raise Unpublishable(f"{unit.role!r} is not one of the three roles")
    if unit.samples < 1 or not 1 <= unit.sample <= unit.samples:
        raise Unpublishable(
            f"{unit.case}/{unit.unit} says it is sample {unit.sample} of {unit.samples}: a "
            "sample is one of the draws its case declares, and a line that says otherwise "
            "is a denominator nobody can recompute"
        )
    line: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_UNIT,
        "run": run,
        "case": unit.case,
        "unit": unit.unit,
        "sample": unit.sample,
        "samples": unit.samples,
        "ending": unit.ending,
        "cause": unit.cause,
        "fault": None if unit.cause is None else fault_of(unit.cause),
        "models": models.as_record(),
        "role": unit.role,
        "model_resolved": unit.model_resolved,
        "seconds": round(unit.seconds, 3),
        "cost_usd": unit.cost_usd,
        # Every session in this repository runs against a subscription allowance, so a
        # price is an API-equivalent figure rather than money that moved. The record
        # already spells that `notional` ([record/model.py]), and the word is kept beside
        # the number rather than publishing a payment that never happened.
        "notional": True,
        "turns": unit.turns,
        "session_id": unit.session_id,
        "expected": unit.expected,
        "observed": unit.observed,
        "account": unit.account,
    }
    if unit.detail is not None:
        line["detail"] = unit.detail
    return line


def measurement_line(
    measurement: Measurement, *, run: str, case: str, models: Models
) -> dict[str, Any]:
    if measurement.name not in MEASUREMENTS:
        raise Unpublishable(
            f"{measurement.name!r} is not one of the {len(MEASUREMENTS)} numbers this "
            "record publishes"
        )
    if measurement.numerator > measurement.denominator:
        raise Unpublishable(
            f"{measurement.name} counted {measurement.numerator} of "
            f"{measurement.denominator}, which is not a rate"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_MEASUREMENT,
        "run": run,
        "case": case,
        "measurement": measurement.name,
        "source": SOURCE_BY_MEASUREMENT[measurement.name],
        # What one counted thing is. Two rates on adjacent lines count corpus sentences and
        # sessions respectively, and a reader who did not know that would compare them.
        "population": POPULATION_BY_MEASUREMENT[measurement.name],
        "numerator": measurement.numerator,
        "denominator": measurement.denominator,
        "value": measurement.value,
        "models": models.as_record(),
    }


class Journal:
    """Where the lines land, written one at a time.

    Streaming rather than gathered: a run that aborts halfway has still paid for what it
    did, and a record written only at the end would lose exactly the units a reader most
    wants to see.
    """

    def __init__(self, path: Path, *, home: str, temporary: str) -> None:
        self._path = path
        self._home = home
        self._temporary = temporary
        self.units = 0
        self.reached = 0
        # Every line as it was published, which is what the verdict is taken over. A tally
        # kept beside the file would be a second account of the run, and the first thing a
        # second account does is disagree with the first — so the three groups a run reports
        # are a pure function of these, and the free suite runs that function over sweeps
        # bought months ago without buying anything.
        self.lines: list[dict[str, Any]] = []

    def write(self, line: dict[str, Any]) -> None:
        clean = cast(dict[str, Any], masked(line, home=self._home, temporary=self._temporary))
        assert_publishable(clean, home=self._home, temporary=self._temporary)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(clean, sort_keys=True) + "\n")
        self.lines.append(clean)
        if clean.get("kind") != KIND_UNIT:
            return
        # The verdict is counted off the lines that were actually written rather than off a
        # tally a case keeps beside them: a unit the record does not hold is a unit that did
        # not happen, and the exit code should say the same thing the file does.
        self.units += 1
        if clean.get("ending") == ENDING_REACHED:
            self.reached += 1


def end_line(
    *,
    run: str,
    ending: str,
    exit_code: int,
    units: int,
    reached: int,
    spent_usd: float,
    seconds: float,
    allowances: dict[str, dict[str, int]] | None = None,
    groups: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The line that closes a run.

    Without one, a run that was killed, refused or is still going is indistinguishable in
    the file from one that finished — and doc 17 asks the record to say what a run cost,
    which no per-unit line can answer on its own.

    It carries the three groups the run reported: critical functionality as a fraction with
    every check it missed, the benchmark's scores with the readings that were not taken at
    all, and each negative impact with what it reached. A release cites this line, so the
    three facts a release turns on are on it rather than assembled from two hundred others.

    They are absent where no verdict was reached. A run that a rate limit stopped at hour
    two has a real closing line and no real groups, and publishing a fraction over the cases
    that happened to have run would be a bar over a population nobody chose.

    It also says what each bounded allowance spent. Past an allowance the rest of a sweep is
    scored on different terms, so a run that used its last second turn and one that had room
    to spare publish rates that are not alike — and the closing line is where a reader
    comparing two runs looks first.
    """
    line: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_END,
        "run": run,
        "ending": ending,
        "exit_code": exit_code,
        "units": units,
        "reached": reached,
        "spent_usd": round(spent_usd, 4),
        "notional": True,
        "seconds": round(seconds, 1),
    }
    if allowances is not None:
        line["allowances"] = allowances
    if groups is not None:
        # By name, the way every other field on this line is held. A blind merge is the one
        # place a caller could overwrite `exit_code` or `spent_usd` on the line a release
        # cites, and the writer refusing what it does not hold is the rest of this module.
        line.update({name: groups[name] for name in GROUPS})
    return line


def ending_of(cause: str | None) -> str:
    """Where a unit arrived, taken from why it did not arrive.

    Three endings rather than two, because an environment fault is not a miss. A probe whose
    session ended in the provider's own error body did not arrive anywhere and did not fail
    to: it never ran, and a rate that counted it would be a rate over a population that
    includes the network. `aborted` is the ending that says so, and the ending and the cause
    cannot disagree because one is read off the other.
    """
    if cause is None:
        return ENDING_REACHED
    return ENDING_ABORTED if fault_of(cause) == FAULT_ENVIRONMENT else ENDING_MISSED
