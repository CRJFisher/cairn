"""Where a session is, read from the event stream it writes as it goes.

One reader for one stream, used from both ends of it. `providers.py` sees the events live as
it tees them to the step's standard output; `record/extract.py` reads that same file back
while the step is still running. A second parser would be a second opinion about what a turn
is, in the one place the two have to agree ([20 A]).

Nothing here guesses. An event this does not recognise contributes nothing rather than a
plausible action: a line naming the wrong tool would be read as a measurement of the session,
and a reader cannot tell one from the other.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple, TextIO, cast

from cairn.text import flatten

# What tells a breadcrumb apart from anything the session itself writes. The two share the
# node's standard error — the session's own process inherits the wrapper's — so a person
# reading that file in the engine's view has nothing but this prefix to filter on ([20 A]).
BREADCRUMB_PREFIX = "[cairn]"

# The one line a breadcrumb or a `where` line gets is already carrying a step id, a turn
# number and an age, so the object is held well under the cap untrusted text usually gets.
OBJECT_LIMIT = 80

# Where a tool call keeps the thing it acts on, most specific first. A call carrying none of
# them names its tool and no object, which is the whole of what was read.
OBJECT_KEYS = (
    "file_path",
    "notebook_path",
    "path",
    "pattern",
    "url",
    "query",
    "skill",
    "command",
    "description",
    "prompt",
)


class Reading(NamedTuple):
    """How far into its session a transcript says the step has got.

    `turns` counts the session's own assistant turns, so it is a count of this file rather
    than the provider's `num_turns` — which only exists once the session has ended, and is
    what the finished step's `turns` carries.
    """

    turns: int
    action: str | None
    object: str | None


def _own_turn(message: dict[str, Any]) -> bool:
    """One of the session's own turns, rather than a subagent's.

    A subagent's messages carry the id of the call that started it. Counting those would
    report a session as twenty turns further on than it is, because one `Task` call fans out
    into a whole second stream.
    """
    return (
        message.get("type") == "assistant"
        and message.get("parent_tool_use_id") is None
    )


def called_in(message: dict[str, Any]) -> tuple[str | None, str | None]:
    """The tool this turn called and the thing it called it on, where it called one.

    The last block wins, because a turn that read three files is a turn whose last act was
    the third.
    """
    body: object = message.get("message")
    content: object = cast(dict[str, Any], body).get("content") if isinstance(body, dict) else None
    if not isinstance(content, list):
        return None, None
    action: str | None = None
    subject: str | None = None
    for raw in cast(list[object], content):
        if not isinstance(raw, dict):
            continue
        block = cast(dict[str, Any], raw)
        if block.get("type") != "tool_use":
            continue
        name: object = block.get("name")
        if not isinstance(name, str):
            continue
        action, subject = flatten(name, limit=OBJECT_LIMIT), None
        arguments: object = block.get("input")
        if not isinstance(arguments, dict):
            continue
        given = cast(dict[str, Any], arguments)
        for key in OBJECT_KEYS:
            value: object = given.get(key)
            if isinstance(value, str) and value.strip():
                subject = flatten(value, limit=OBJECT_LIMIT)
                break
    return action, subject


def read(path: str) -> Reading | None:
    """What a transcript says about its session now, or nothing where it says nothing.

    A live transcript is a file being appended to, so its last line is routinely half
    written. A line that does not parse is skipped rather than refused: the session is
    mid-event, which is not damage and is not evidence of anything either.

    `None` is returned only where the file could not be read at all or held no event. The
    caller still has the file's own last-write time, which is the age this is read for.
    """
    turns = 0
    action: str | None = None
    subject: str | None = None
    seen = False
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    raw: object = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                message = cast(dict[str, Any], raw)
                seen = True
                if not _own_turn(message):
                    continue
                turns += 1
                called, on = called_in(message)
                if called is not None:
                    action, subject = called, on
    except OSError:
        return None
    return Reading(turns, action, subject) if seen else None


class Trail:
    """One legible line per turn, on the channel a person watching the node already has.

    The count spans every pass of one step. A session opened to run a command and continued
    for the work is one session ([providers.py]), and a breadcrumb that restarted at
    turn 1 half way through would read as a second session starting.

    A write that fails stops the trail rather than the step: the breadcrumb is an addition
    beside the transcript, and a step that died because nobody was reading its standard
    error would be the opposite of what it is for.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream: TextIO | None = stream
        self.turns = 0

    def turn(self, message: dict[str, Any]) -> None:
        """Count one of the session's own turns and say what it did, or ignore anything else."""
        if not _own_turn(message):
            return
        self.turns += 1
        action, subject = called_in(message)
        did = "no tool call" if action is None else action
        if subject is not None:
            did = f"{did} {subject}"
        self.write(f"turn {self.turns}: {did}")

    def write(self, line: str) -> None:
        """One prefixed line, flushed, so it reaches the file while the step still runs."""
        stream = self._stream
        if stream is None:
            return
        try:
            stream.write(f"{BREADCRUMB_PREFIX} {line}\n")
            stream.flush()
        except (OSError, ValueError):
            self._stream = None


__all__ = [
    "BREADCRUMB_PREFIX",
    "OBJECT_KEYS",
    "OBJECT_LIMIT",
    "Reading",
    "Trail",
    "called_in",
    "read",
]
