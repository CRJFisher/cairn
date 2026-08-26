"""The one hook a step's session runs under, and the decision it makes.

A step's session runs headless, and a headless session's process ends when its turn ends.
Measured against the installed agent CLI, three of the four things such a session can do to
defer its own completion survive that and one does not:

| What the session did                     | What `-p` does with it                                          |
| ---------------------------------------- | --------------------------------------------------------------- |
| `Agent` in the background, three at once | the process is held; the session is re-invoked on each completion |
| `Monitor`                                | it blocks, and the session reads the result in the same turn    |
| `ScheduleWakeup`                         | nothing ever fires it — denied outright ([providers.py])         |
| `Bash` with `run_in_background`          | **the process exits with the shell still running**              |

The last is the leak, and it is the one that cannot be closed by denying a tool: denying
`Bash` denies the step its work, and denying the *argument* would cost a step its concurrent
shells — which is a capability the plan may need and which the other three prove is
affordable. So it is closed here instead, at the moment the turn tries to end.

**This hook fails open, which is the exact inverse of the verify gate.** The gate closes on
every fault because a marker over unverified work reaches git and rides every merge. This
one holds a paid session open, so a fault in it spends money in a loop — and it protects
nothing durable, because a session that ends without reporting is still caught by the
resume ([providers.py]) and still refused by the gate. Nothing in Cairn may depend on the
hook having run.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO, cast

HOOK_VERB = "hook"
STOP_EVENT = "stop"

# The harness's own two answers, measured: exit 2 re-enters the session with this process's
# stderr delivered to it as a user message, and anything else lets the turn end. There is no
# third answer — a nonzero code that is not 2 is a hook that failed, which is ignored.
LET_IT_END = 0
HOLD_IT_OPEN = 2

# A background task the harness reports at the end of a turn. A shell is the leaking kind;
# a subagent is a different `type` and is waited for, so refusing on the count alone would
# refuse exactly the fan-out this is written to preserve.
LEAKING_TYPE = "shell"
LIVE_STATUSES = frozenset({"running", "pending", "in_progress"})


def unread_shells(payload: object) -> list[str]:
    """Every background shell this turn would end with still running, named by its command.

    Total over any JSON value and never raises: a payload this cannot read is a turn that
    ends, for the reason the module docstring gives.
    """
    if not isinstance(payload, dict):
        return []
    tasks = cast(dict[str, Any], payload).get("background_tasks")
    if not isinstance(tasks, list):
        return []
    found: list[str] = []
    for task in cast(list[Any], tasks):
        if not isinstance(task, dict):
            continue
        entry = cast(dict[str, Any], task)
        if entry.get("type") != LEAKING_TYPE:
            continue
        if str(entry.get("status", "")).lower() not in LIVE_STATUSES:
            continue
        command = entry.get("command") or entry.get("description") or entry.get("id")
        found.append(str(command))
    return found


def already_held(payload: object) -> bool:
    """Whether this turn has been sent back once already.

    `stop_hook_active` is the harness's own bound and it is what keeps this from being a
    loop: a session that will not wait for its shell is let go on the second ask, and the
    missing report is caught downstream instead. Measured: false on the first stop of a
    turn, true on the re-entry.
    """
    return isinstance(payload, dict) and cast(dict[str, Any], payload).get(
        "stop_hook_active"
    ) is True


def reason(payload: object) -> str | None:
    """Why this turn may not end yet, or `None` where it may."""
    if already_held(payload):
        return None
    left = unread_shells(payload)
    if not left:
        return None
    named = "".join(f"\n  - {command}" for command in left)
    return (
        f"You have {len(left)} background shell(s) still running, and this session's "
        f"process ends when your turn does — nothing will read them:{named}\n"
        "Wait for each one and use what it says. `Monitor` blocks, a foreground `Bash` "
        "returns, and a background subagent is waited for; a background shell is not. "
        "Then report through the structured output you are constrained to."
    )


def hook_main(arguments: list[str], stream: TextIO | None = None) -> int:
    """Answer whether a step's session may end its turn. Exit 2 holds it open.

    Takes no runtime identity and writes no step report, so it is routed before the
    dispatch that resolves one — a hook that reached `RuntimeContext.from_env()` would
    inherit the step's own environment and overwrite the report the verify gate reads.
    """
    parser = argparse.ArgumentParser(prog="cairn hook", add_help=False)
    parser.add_argument("event", choices=(STOP_EVENT,))
    try:
        parser.parse_args(arguments)
        payload: object = json.load(stream or sys.stdin)
    except (SystemExit, ValueError, OSError):
        return LET_IT_END
    said = reason(payload)
    if said is None:
        return LET_IT_END
    print(said, file=sys.stderr)
    return HOLD_IT_OPEN


__all__ = [
    "HOLD_IT_OPEN",
    "HOOK_VERB",
    "LET_IT_END",
    "STOP_EVENT",
    "already_held",
    "hook_main",
    "reason",
    "unread_shells",
]
