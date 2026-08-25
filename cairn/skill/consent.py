"""The offer a run is authorised by, and the single execution it buys.

Doc 15 asks for a consent rule and for a hard gate on it. A rule a model is told to follow
is not a gate, so the rule is stated once — in `SKILL.md`, in full, and nowhere else — and
what a filesystem can hold of it is held here. Three clauses, and the mechanism of each:

| The clause is about    | What holds it here                                           |
| ---------------------- | ------------------------------------------------------------ |
| the price being stated | an offer is the only source of an id, and it prints the cost |
| a stale yes            | spending quotes an id that did not exist before the offer    |
| a second execution     | spending is an exclusive create, once                        |

Every one of those is a property of a file on disk, which is why they are a gate. **Whether
the words mean yes is not in the table and cannot be**: the reply reaching `spend` is the
session's own `--reply` argument, so the judgement has already happened upstream of anything
here, and no comparison made here could see the case where it went wrong. Nothing in this
module inspects the reply for meaning. `SKILL.md` states the fourth clause — a bare
acknowledgement is not a yes — as a rule binding the session, which is where it can be kept.

What none of this proves is that a person was ever asked. The ledger records the moment the
offer was made, the moment it was spent, and the words it was spent with, so what authorised
a run is answerable from the repository afterwards and a zero gap between the two moments is
visible in the record rather than prevented — an honest instrument rather than a claim.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.core import CairnError
from cairn.gitio import CAIRN_STATE, common_directory
from cairn.marker import OCCASION_PATTERN, mint_occasion
from cairn.skill.vocabulary import (
    COST_SENTENCES,
    REFUSED_ALREADY_SPENT,
    REFUSED_NO_SUCH_OFFER,
    REFUSED_NO_WORDS,
    REFUSED_OFFER_UNREADABLE,
    REFUSED_WORKFLOW_MOVED,
    RUN_COST_FACTS,
)
from cairn.topology import worktrees_parent
from cairn.workflow.schema import (
    PARENT_BRANCH_PARAM,
    REPOSITORY_PARAM,
    declared_parameter,
    is_agent_body,
    read,
    split_argv,
)
from cairn.workflow.stamp import file_digest

OFFERS_DIRECTORY = "offers"
SPENT_SUFFIX = ".spent"

class Offer(NamedTuple):
    offer_id: str
    plan: str
    workflow: str
    repository: str
    parent_branch: str
    occasion_reading: str
    occasion: str | None
    body_sha256: str
    offered_at: str
    cost: tuple[str, ...]


class Authorisation(NamedTuple):
    """Proof that one execution may happen, and the only thing `trigger.start` accepts.

    Constructed here and nowhere else, asserted by a test. A classification cannot mint one
    because `dispatch.py` cannot see this module at all.
    """

    offer_id: str
    plan: str
    workflow: str
    repository: str
    parent_branch: str
    occasion: str | None
    granted_at: str


class Refused(NamedTuple):
    outcome: str
    why: str


SpendOutcome = Authorisation | Refused


def offers_directory(repository: Path) -> Path:
    """Beside the run records, in git's admin directory rather than the working tree.

    A commit step stages paths it names, and an offer is not one of them; a worktree removal
    reaches the working tree, and this is not in it.

    Named rather than created: reading an offer must not write to a repository, so the one
    caller that needs the directory to exist makes it.
    """
    return common_directory(repository) / CAIRN_STATE / OFFERS_DIRECTORY


def offer_path(repository: Path, offer_id: str) -> Path:
    """Where one offer lives, refusing an id that could name anything else.

    An offer id arrives from a caller and is interpolated into a path, so it is judged the
    way every other id in this package is: `marker.marker_path` refuses a step id the
    grammar does not admit, and an occasion is judged by `occasion_moment`. Unchecked, a
    separator in the id reads a file outside the ledger, and `with_name` then raises where a
    refusal belongs.
    """
    if OCCASION_PATTERN.match(offer_id) is None:
        raise CairnError(
            "invalid_arguments",
            f"{offer_id!r} is not an offer id. An offer id is what `cairn run offer` "
            "minted and printed; nothing else names an offer",
        )
    return offers_directory(repository) / f"{offer_id}.json"


def _readable(workflow: Path) -> Any:
    """The definition, or a refusal naming it — never a traceback.

    A hand-edited definition is the ordinary input here, not an exceptional one: it is what
    Explain exists to describe and what an offer must refuse to price.
    """
    try:
        return read(workflow)
    except (OSError, ValueError) as unreadable:
        raise CairnError(
            "invalid_arguments",
            f"{workflow} is not the JSON document Cairn writes, so what a run of it would "
            f"cost cannot be stated: {unreadable}. Re-author the plan",
        ) from unreadable


def _bodies(document: Any) -> list[str]:
    if not isinstance(document, dict):
        return []
    steps = cast(dict[str, Any], document).get("steps")
    if not isinstance(steps, list):
        return []
    return [
        body
        for step in cast(list[Any], steps)
        if isinstance(step, dict) and isinstance(body := cast(dict[str, Any], step).get("run"), str)
    ]


def agent_steps(document: Any) -> int:
    """How many paid sessions this definition can start, counted from its own bodies.

    Read from the argv rather than from a node's name, because a name is a convention the
    topology maintains while the argv is what the step actually does — and the emitter that
    writes those bodies owns the shape, so the reader asks it rather than restating it.
    """
    return sum(1 for body in _bodies(document) if is_agent_body(body))


def _flag_value(words: tuple[str, ...], flag: str) -> str | None:
    for index, word in enumerate(words[:-1]):
        if word == flag:
            return words[index + 1]
    return None


class SessionBound(NamedTuple):
    """The two bounds one agent body writes for the session it opens."""

    ceiling_usd: float
    model: str


def session_bounds(document: Any) -> list[SessionBound]:
    """Every paid session's written ceiling and model, read from the bodies themselves.

    A refusal rather than a guess where one is missing: an agent body with no ceiling is
    the one thing a run's price cannot be stated over, and an offer that skipped it would
    price the run as though the session were free ([17.3]).
    """
    found: list[SessionBound] = []
    for body in _bodies(document):
        if not is_agent_body(body):
            continue
        words = split_argv(body)
        ceiling = _flag_value(words, "--max-budget-usd")
        model = _flag_value(words, "--model")
        try:
            priced = ceiling is not None and float(ceiling) > 0
        except ValueError:
            priced = False
        if not priced or model is None or not model.strip():
            raise CairnError(
                "invalid_arguments",
                f"an agent body in this definition writes no dollar ceiling or no model "
                f"({body[:120]}…), so what a run of it would cost cannot be stated. "
                "Re-author the plan",
            )
        found.append(SessionBound(ceiling_usd=float(cast(str, ceiling)), model=model))
    return found


def longest_timeout(document: Any) -> int:
    """The largest per-attempt bound any step in this definition runs under."""
    if not isinstance(document, dict):
        return 0
    steps = cast(dict[str, Any], document).get("steps")
    if not isinstance(steps, list):
        return 0
    timeouts = [
        timeout
        for step in cast(list[Any], steps)
        if isinstance(step, dict)
        and isinstance(timeout := cast(dict[str, Any], step).get("timeout_sec"), int)
        and not isinstance(timeout, bool)
    ]
    return max(timeouts, default=0)


def refuse_uncarriable(parameter: str) -> None:
    """Refuse a parameter the engine could not carry to the run intact.

    The engine re-splits one `--params` string on whitespace into key=value pairs, so a value
    holding a space arrives as two parameters and the run acts on something other than what
    was agreed. Asked when the offer is made, because every refusal has to happen before the
    acceptance is spent — a person must not lose their yes to a value they gave before it.
    """
    if any(character.isspace() for character in parameter):
        raise CairnError(
            "invalid_arguments",
            f"{parameter!r} holds whitespace, and the engine splits `--params` on it, so "
            "the run would be started with a value other than the one agreed to",
        )


def declared_branch(workflow: Path) -> str | None:
    """The branch a definition merges into unless a run is offered for another."""
    return declared_parameter(_readable(workflow), PARENT_BRANCH_PARAM) or None


def disclosure(workflow: Path, parent_branch: str | None = None) -> tuple[str, ...]:
    """What a run of this definition costs, every fact of it, read from the file in hand.

    Composed rather than templated into prose a model retypes, and composed from the
    definition rather than from the request — so a cost cannot be quoted for a workflow
    nobody has, and a re-authored workflow cannot be offered at yesterday's price.

    The one value a caller may vary is the branch, and it is priced here rather than
    settled later: what a run merges verified work into is a term of the agreement, so the
    branch quoted must be the branch used.
    """
    document = _readable(workflow)
    repository = declared_parameter(document, REPOSITORY_PARAM) or None
    parent = parent_branch or declared_parameter(document, PARENT_BRANCH_PARAM) or None
    if repository is None or parent is None:
        raise CairnError(
            "invalid_arguments",
            f"{workflow} declares no {REPOSITORY_PARAM} or no {PARENT_BRANCH_PARAM}, so "
            "what a run of it would cost cannot be stated and it cannot be offered",
        )
    bounds = session_bounds(document)
    filled = {
        "agent_steps": len(bounds),
        "ceiling_usd": f"{sum(bound.ceiling_usd for bound in bounds):.2f}",
        "models": ", ".join(sorted({bound.model for bound in bounds})) or "none",
        "longest_timeout_seconds": longest_timeout(document),
        "repository": repository,
        "parent_branch": parent,
        "worktrees_root": worktrees_parent(Path(repository)),
    }
    return tuple(COST_SENTENCES[fact].format(**filled) for fact in RUN_COST_FACTS)


def make_offer(
    repository: Path,
    *,
    plan: str,
    workflow: Path,
    occasion_reading: str,
    occasion: str | None,
    parent_branch: str | None = None,
    moment: datetime | None = None,
) -> tuple[Offer, tuple[str, ...]]:
    """Mint one offer and state its price in the same act.

    The two are one act on purpose: this id is the only thing `spend` accepts, `spend` is
    the only thing that mints the authorisation `trigger.start` needs, and an id is only
    obtainable from a call that has already composed the cost. There is no path to a run
    whose price was never stated.
    """
    stated = disclosure(workflow, parent_branch)
    branch = parent_branch or declared_branch(workflow)
    if branch is None:
        raise CairnError(
            "invalid_arguments",
            f"{workflow} declares no {PARENT_BRANCH_PARAM} and none was asked for, so "
            "there is no branch this run could land on",
        )
    refuse_uncarriable(f"{PARENT_BRANCH_PARAM}={branch}")
    now = (datetime.now(UTC) if moment is None else moment.astimezone(UTC)).isoformat()
    record = Offer(
        offer_id=mint_occasion(moment),
        plan=plan,
        workflow=str(workflow),
        repository=str(repository),
        parent_branch=branch,
        occasion_reading=occasion_reading,
        occasion=occasion,
        body_sha256=file_digest(workflow),
        offered_at=now,
        cost=stated,
    )
    path = offer_path(repository, record.offer_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record._asdict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return record, stated


def read_offer(repository: Path, offer_id: str) -> Offer | None:
    """The offer that id names, or `None` where there has never been one.

    Only absence returns `None`. An offer that is there and cannot be read raises, because
    telling a person their yes predated an offer that in fact exists and is merely damaged
    is a claim about their conversation drawn from a filesystem fault. The verify gate keeps
    the same distinction between "could not be established" and "never happened", for the
    same reason ([docs/verify-gate.md]), as does `locks._read_record` about a payload it
    cannot parse.
    """
    path = offer_path(repository, offer_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as unreadable:
        raise CairnError(
            "invalid_arguments", f"{path} cannot be read: {unreadable}"
        ) from unreadable
    try:
        held = cast(dict[str, Any], json.loads(text))
        return Offer(
            offer_id=str(held["offer_id"]),
            plan=str(held["plan"]),
            workflow=str(held["workflow"]),
            repository=str(held["repository"]),
            parent_branch=str(held["parent_branch"]),
            occasion_reading=str(held["occasion_reading"]),
            occasion=None if held["occasion"] is None else str(held["occasion"]),
            body_sha256=str(held["body_sha256"]),
            offered_at=str(held["offered_at"]),
            cost=tuple(str(line) for line in cast(list[Any], held["cost"])),
        )
    except (ValueError, KeyError, TypeError) as damaged:
        raise CairnError(
            "invalid_arguments",
            f"{path} is not an offer Cairn wrote ({damaged}). It exists, so this is a "
            "damaged ledger rather than a yes given before any offer; offer the run again",
        ) from damaged


def has_words(reply: str) -> bool:
    """Whether there is anything here to have been said.

    The one thing asked of a reply, and it is a question about the argument rather than about
    English: an empty `--reply` would mean a run authorised by no words, and a ledger holding
    none could never answer what authorised it. What the words *mean* is not asked.
    """
    return any(character.isalnum() for character in reply)


def _claim(marker: Path, note: str) -> bool:
    """Take the marker, or report that someone already has.

    Linked into place from a fully-written temporary rather than created and then written,
    so the file is never observable empty — `marker._publish_occasion` makes the same move
    for the same reason, and here an empty marker would make the already-spent refusal say
    the offer was spent at nothing.
    """
    marker.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{marker.name}.", suffix=".tmp", dir=marker.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(note)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, marker)
        except FileExistsError:
            return False
        except OSError as unclaimable:
            # A filesystem with no hard links, or a full disk. The offer is untouched, so
            # the acceptance still stands — which the message has to say, or a person reads
            # a failure to claim as a run that happened.
            raise CairnError(
                "invalid_arguments",
                f"{marker} could not be claimed, so nothing was started and the offer is "
                f"still yours to spend: {unclaimable}",
            ) from unclaimable
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def _spent_at(marker: Path) -> str:
    spent = read_acceptance(marker)
    if spent is None:
        return "a moment that can no longer be read"
    return spent.spent_at or "a moment it did not record"


class Acceptance(NamedTuple):
    """What was said to start a run, and when it was said.

    The words are kept because a run spends money and commits on someone's say-so, and an
    offer that recorded only *that* it was accepted leaves nothing able to answer which
    words did it. The offer beside this marker says what was agreed to; this says who agreed
    and in what terms.
    """

    spent_at: str
    reply: str


def read_acceptance(marker: Path) -> Acceptance | None:
    """The acceptance a spent offer recorded, or nothing where none can be read."""
    try:
        held: Any = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(held, dict):
        return None
    fields = cast(dict[str, Any], held)
    return Acceptance(
        spent_at=str(fields.get("spent_at", "")),
        reply=str(fields.get("reply", "")),
    )


def acceptance_of(repository: Path, offer_id: str) -> Acceptance | None:
    """The words one offer was accepted with, read from the repository rather than a session.

    An acceptance is a fact about the repository once the offer is spent, so anything asking
    "was this run authorised, and by what" reads it here rather than from the transcript of
    whoever was in the room.
    """
    marker = offer_path(repository, offer_id).with_name(f"{offer_id}{SPENT_SUFFIX}")
    return read_acceptance(marker)


def spend(
    repository: Path, offer_id: str, *, reply: str, moment: datetime | None = None
) -> SpendOutcome:
    """Consume one offer, once, or refuse saying which clause stopped it.

    Every refusal here happens before the marker is written, so a refused acceptance is
    still spendable — which is what lets a person clear the cause and answer once rather
    than being asked to decide the same thing twice.

    **Nothing here reads the reply for meaning.** Whether the words were a yes was settled by
    whoever passed them, and this refuses only on facts a filesystem holds. What the reply is
    for is the record: it is written beside the spent offer, so what authorised a run is
    answerable afterwards from the repository rather than from someone's memory of the room.
    """
    if OCCASION_PATTERN.match(offer_id) is None:
        # A string that cannot be an offer id names no offer, which is the same fact as an
        # id that names none — and a different one from a ledger that is damaged.
        return Refused(
            outcome=REFUSED_NO_SUCH_OFFER,
            why=(
                f"{offer_id!r} is not an offer id. An offer id is what `cairn run offer` "
                "minted and printed; nothing else names an offer"
            ),
        )
    if not has_words(reply):
        return Refused(
            outcome=REFUSED_NO_WORDS,
            why=(
                f"{reply.strip()!r} accepts nothing — there are no words in it. A run "
                "spends money and commits, so it takes words that say to run it"
            ),
        )
    try:
        held = read_offer(repository, offer_id)
    except CairnError as damaged:
        return Refused(outcome=REFUSED_OFFER_UNREADABLE, why=str(damaged))
    if held is None:
        return Refused(
            outcome=REFUSED_NO_SUCH_OFFER,
            why=(
                f"{offer_id!r} names no offer against {repository}. An acceptance quotes "
                "the offer it accepts, so a yes given before the offer was made cannot "
                "carry one"
            ),
        )
    workflow = Path(held.workflow)
    if not workflow.exists() or file_digest(workflow) != held.body_sha256:
        return Refused(
            outcome=REFUSED_WORKFLOW_MOVED,
            why=(
                f"{workflow} is not the definition this offer priced, so what was agreed "
                "to is not what would run. Offer the run again"
            ),
        )
    marker = offer_path(repository, offer_id).with_name(f"{offer_id}{SPENT_SUFFIX}")
    now = (datetime.now(UTC) if moment is None else moment.astimezone(UTC)).isoformat()
    claimed = json.dumps({"spent_at": now, "reply": reply}, sort_keys=True)
    if not _claim(marker, f"{claimed}\n"):
        return Refused(
            outcome=REFUSED_ALREADY_SPENT,
            why=(
                f"offer {offer_id} was already spent ({_spent_at(marker)}). It bought one "
                "execution and that execution has happened; a second needs its own offer"
            ),
        )
    return Authorisation(
        offer_id=offer_id,
        plan=held.plan,
        workflow=held.workflow,
        repository=held.repository,
        parent_branch=held.parent_branch,
        occasion=held.occasion,
        granted_at=now,
    )


__all__ = [
    "Acceptance",
    "Authorisation",
    "Offer",
    "Refused",
    "SessionBound",
    "SpendOutcome",
    "acceptance_of",
    "agent_steps",
    "declared_branch",
    "disclosure",
    "has_words",
    "longest_timeout",
    "make_offer",
    "offer_path",
    "offers_directory",
    "read_acceptance",
    "read_offer",
    "refuse_uncarriable",
    "session_bounds",
    "spend",
]
