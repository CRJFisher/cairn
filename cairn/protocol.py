"""What every agent step is told, and the shape of the answer it is constrained to give.

Both halves are stated once here and reproduced verbatim in `docs/step-protocol.md`,
which a test asserts. A prompt is not a place to state a contract: the report's shape
comes from the schema, and nothing anywhere parses a status out of prose.
"""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

# The measured value of this text is 69 percentage points of re-run work (02): a resumed
# session without it never inspected the tree, rewrote six files that were already correct,
# and did 152% of the work of starting from scratch. It is what makes the fresh-session rule
# workable, so it is mandatory rather than advisory.
PREAMBLE = """\
Before you change anything, work out how much of this task's end state already holds.

The working tree may already carry some or all of this work. An earlier attempt at this
same step may have been interrupted part-way, and its partial edits are still here. Read
the tree and establish what is already true.

Then do only what is missing. Bring the tree to the end state the task describes and
leave whatever already matches it untouched. Do not start over, do not repeat work that
is already correct, and do not assume you are looking at an empty tree.

Do not record your own completion anywhere. Completion is recorded by the verification
that follows you, never by you.

This session is one shot: the process ends when your turn ends, and nothing re-invokes
you for a background shell. Subagents and `Monitor` are yours to use — a background
subagent is waited for, and `Monitor` blocks — but anything you start with `Bash`'s
`run_in_background` dies unread when your turn ends. Wait for whatever you start, and
end only by reporting. Ending a turn to wait for a subagent asks you for a report early;
give one, then report again once it finishes. Your last report is the one that counts.

Report through the structured output you are constrained to. `status` is `done` when the
end state now holds, `noop` when it already held and you changed nothing, and `failed`
when you could not reach it. List work you found but did not do in `follow_up_work`. Set
`needs_user_decision` when a human has to decide something before the plan can safely
proceed; that blocks the step rather than failing it.

A report that is accepted ends your turn, and may be the account this step is recorded by:
never file a placeholder or a test report. If the structured output refuses a report, it
names what is missing; file your whole report again with each of the four fields in its own
place.

The task:
"""

# What a session that ended a turn without reporting is asked, once. It is a request for
# the account it owes, never an instruction to do more work: the step's assertion has
# already run or is about to, and a resumed session that started editing again would be
# doing work outside the shape the plan stated.
RESUME_FOR_REPORT = """\
This session is ending now and nothing will re-invoke it. Do no further work.

Report what you have already done, through the structured output you are constrained to.
"""

# What a session the subscription's limit stopped is asked, once its window has reopened. It
# is the same session, so it still holds the task; what it does not hold is how far the turn
# the limit refused got, and the preamble ahead of this text sends it to the tree to find out.
RESUME_AFTER_LIMIT = """\
Your previous turn in this session was stopped by the subscription's usage limit, which has
since reset. Continue the task you were given earlier in this session from where the tree now
stands, then report through the structured output you are constrained to.
"""

# What a session the model provider cut off is asked, once a probe reaches the provider again.
# Same shape as the limit's: the session holds the task, and the tree holds how far it got.
RESUME_AFTER_OUTAGE = """\
Your previous turn in this session was cut off because the model provider could not be
reached, and it can be reached again now. Continue the task you were given earlier in this
session from where the tree now stands, then report through the structured output you are
constrained to.
"""

# What a session is asked when the report it filed was accepted only after the structured
# output refused earlier attempts. Measured: a session whose real account was refused three
# times — its `follow_up_work` written inside its `summary` — filed `summary: "test"` to see
# whether anything would pass, and that probe ended the session as the step's whole account,
# its eleven fixes and four follow-ups lost. The session still holds the account it meant;
# this asks for it once, and accepting the same report again is a full answer.
REFILE_REPORT = """\
This session is ending now and nothing will re-invoke it. Do no further work.

The structured output refused {refused} report(s) you filed before it accepted this one,
which is the account this step will be recorded by:

{accepted}

If that is your whole account of this step, file it again unchanged. If it is not — a
placeholder, a test, or a shortened version of what you meant — file your whole account
now, with status, summary, follow_up_work and needs_user_decision each in its own field.
"""

# What a remedy session is asked, after its own step's assertion ran and exited nonzero. The
# assertion is the plan's definition of done, so the one move this text forbids is the one
# that would make a failing assertion pass without the work changing.
REMEDY_TASK = """\
This step's work was asked for as the original task below. Its assertion, the command
`{assertion}`, then ran over the tree and exited {exit_code}, so the end state it checks
does not hold.

Run that command yourself, read what it reports, and change the work until it passes. The
assertion is the plan's definition of done: fix the work, never the assertion. Do not edit,
skip or weaken the checks it runs. If passing it would need the checks themselves to
change, change nothing, report `failed`, and say why.

What the step reported of itself: {said}

The original task:
{task}"""


STEP_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["done", "noop", "failed"]},
        "summary": {"type": "string"},
        "follow_up_work": {"type": "array", "items": {"type": "string"}},
        "needs_user_decision": {"type": "boolean"},
    },
    "required": [
        "status",
        "summary",
        "follow_up_work",
        "needs_user_decision",
    ],
}


def compose_remedy_task(task: str, assertion: str, exit_code: int, said: str) -> str:
    """The task a remedy session is given, before the preamble every session receives."""
    return REMEDY_TASK.format(assertion=assertion, exit_code=exit_code, said=said, task=task)


def compose_refile(refused: int, accepted: dict[str, Any]) -> str:
    """What a session is asked when its accepted report followed reports that were refused."""
    return REFILE_REPORT.format(
        refused=refused, accepted=json.dumps(accepted, indent=2, ensure_ascii=False)
    )


# A first line that is a slash command: `/name`, then optional arguments. A path such as
# `/usr/bin/env` is not one, because a command name holds no further slash.
_SLASH_COMMAND = re.compile(r"/([A-Za-z][\w:.-]*)(?: [^\n]*)?")

# Built-in commands that do nothing a step could be verified for. Each has no file whose
# frontmatter could say so, which is why Cairn keeps the list: the validator refuses a task
# led by one, naming what it would do instead.
NOT_A_STEP: dict[str, str] = {
    "clear": "empties the session it would run in, so it leaves nothing to verify",
    "compact": "summarises the session it would run in, so it leaves nothing to verify",
    "help": "prints help to a session nobody reads",
    "login": "asks a person to sign in, and no person is at a step's session",
    "loop": "schedules itself to run again, and a step's session denies scheduling",
}

# What the reporting half of a step led by a command is told about the half before it. The
# command has already run in this session, so its findings are above; what is not
# guaranteed is that the tree holds every change it claimed, so that is checked here.
FOLLOW_THROUGH = """\
The task's first line, `{command}`, has already run in this session, and what it said is
above. Do not run it again. Check the working tree with `git status` and `git diff` against
what it says it changed, and make every change it reported but did not apply. Then bring
the tree to the rest of the task's end state.
"""


class StepPrompt(NamedTuple):
    """What one agent step's session is given.

    `command` is a slash command the session runs alone, before anything else, with no
    report asked of it; `prompt` is what the same session is given next, and what it
    reports against. A step that leads with no command is the prompt alone.
    """

    command: str | None
    prompt: str


def leading_command(task: str) -> tuple[str, str] | None:
    """The slash command a task leads with, as its name and its whole line, or None."""
    first = task.partition("\n")[0]
    matched = _SLASH_COMMAND.fullmatch(first)
    return (matched.group(1), first) if matched else None


def compose_prompt(task: str) -> StepPrompt:
    """What one agent step's session is given: the protocol, then the task.

    Composed here rather than baked into the emitted workflow so the whole preamble stays
    out of a step's argv, and so a provider added later inherits it without knowing it.

    A task whose first line is a slash command runs in two halves of one session. A headless
    session runs `/skill args` as the person's own command only when it is the very first
    thing it is given, and the command takes everything after its name as its arguments, so
    the line is given alone: anything that followed it would become the command's target. The
    protocol, the whole task and the follow-through then continue the same session, which is
    where the command's findings are and where the report is asked for.
    """
    led = leading_command(task)
    if led is None:
        return StepPrompt(None, f"{PREAMBLE}\n{task}")
    _, line = led
    return StepPrompt(line, f"{PREAMBLE}\n{task}\n\n{FOLLOW_THROUGH.format(command=line)}")
