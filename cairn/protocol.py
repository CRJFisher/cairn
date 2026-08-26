"""What every agent step is told, and the shape of the answer it is constrained to give.

Both halves are stated once here and reproduced verbatim in `docs/step-protocol.md`,
which a test asserts. A prompt is not a place to state a contract: the report's shape
comes from the schema, and nothing anywhere parses a status out of prose.
"""

from __future__ import annotations

from typing import Any

# The measured value of this text is 69 percentage points of re-run cost (02): a resumed
# session without it never inspected the tree, rewrote six files that were already correct,
# and cost 152% of doing the work from scratch. It is what makes the fresh-session rule
# affordable, so it is mandatory rather than advisory.
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
end only by reporting.

Report through the structured output you are constrained to. `status` is `done` when the
end state now holds, `noop` when it already held and you changed nothing, and `failed`
when you could not reach it. List work you found but did not do in `follow_up_work`. Set
`needs_user_decision` when a human has to decide something before the plan can safely
proceed; that blocks the step rather than failing it.

The task:
"""

# What a session that ended a turn without reporting is asked, once. It is a request for
# the account it owes, never an instruction to do more work: the step's assertion has
# already run or is about to, and a resumed session that started editing again would be
# doing unpriced work outside the shape the offer stated.
RESUME_FOR_REPORT = """\
This session is ending now and nothing will re-invoke it. Do no further work.

Report what you have already done, through the structured output you are constrained to.
"""

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


def compose_prompt(task: str) -> str:
    """The prompt one agent step actually receives: the protocol, then the task.

    Composed here rather than baked into the emitted workflow so the whole preamble stays
    out of a step's argv, and so a provider added later inherits it without knowing it.
    """
    return f"{PREAMBLE}\n{task}"
