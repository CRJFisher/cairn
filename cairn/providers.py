"""Agent-provider runners and the deliberately plain provider dictionary."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO, cast

from cairn.commands import stop_child
from cairn.core import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_RATE_LIMITED,
    CairnError,
    CommandResult,
    PopenFactory,
    launch,
)
from cairn.protocol import STEP_REPORT_SCHEMA, compose_prompt

PROMPT_WRITER_JOIN_SECONDS = 5.0
PROVIDER_EXIT_GRACE_SECONDS = 30.0

ProviderRunner = Callable[
    [
        str,
        Path,
        str,
        str | None,
        float | None,
        list[str],
        PopenFactory,
    ],
    CommandResult,
]


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CairnError("provider_protocol", f"{field} is not an object")
    return cast(dict[str, Any], value)


def _required(record: dict[str, Any], names: tuple[str, ...]) -> None:
    missing = [name for name in names if name not in record]
    if missing:
        raise CairnError(
            "provider_protocol",
            "result missing required fields: " + ", ".join(missing),
        )


def _parse_lines(
    lines: Iterable[str], *, tee: TextIO | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    result: dict[str, Any] | None = None
    rate_limits: list[dict[str, Any]] = []
    api_key_source: str | None = None
    for number, raw_line in enumerate(lines, 1):
        if tee is not None:
            tee.write(raw_line)
            tee.flush()
        if not raw_line.strip():
            continue
        try:
            message = _object(json.loads(raw_line), f"line {number}")
        except json.JSONDecodeError as exc:
            raise CairnError(
                "provider_protocol", f"line {number} is not valid JSON: {exc}"
            ) from exc
        message_type = message.get("type")
        if message_type == "result":
            # The result is terminal. Reading on would wait for EOF, which needs every
            # inheritor of the pipe to close it — a provider's own children can hold it
            # open indefinitely, and the answer is already in hand.
            result = message
            break
        if message_type == "rate_limit_event":
            rate_limits.append(message)
        if message_type == "system" and message.get("subtype") == "init":
            source = message.get("apiKeySource")
            api_key_source = source if isinstance(source, str) else None
    if result is None:
        raise CairnError("provider_protocol", "stream ended without a result message")
    _required(
        result,
        (
            "subtype",
            "session_id",
            "total_cost_usd",
            "num_turns",
            "permission_denials",
        ),
    )
    return result, rate_limits, api_key_source


def _latest_reset(rate_limits: list[dict[str, Any]]) -> str | None:
    """The furthest reset time the session was warned about, or None.

    A rate-limited step is an exclusion that can name the time the plan could be re-run
    ([02]), so the moment is carried out of the stream even when the session then succeeds.
    """
    moments = [
        value
        for event in rate_limits
        for value in (event.get("resetsAt"),)
        if isinstance(value, str) and value
    ]
    return max(moments) if moments else None


def _translate_result(
    process_exit: int,
    result: dict[str, Any],
    rate_limits: list[dict[str, Any]],
    *,
    generated_session_id: str,
    permission_mode: str,
    deny_patterns: list[str],
    model: str | None,
    api_key_source: str | None,
) -> CommandResult:
    structured = result.get("structured_output")
    raw_terminal_reason: object = result.get("terminal_reason")
    terminal_reason = raw_terminal_reason if isinstance(raw_terminal_reason, str) else None
    detail: dict[str, Any] = {
        "resets_at": _latest_reset(rate_limits),
        "session_id": result["session_id"],
        "generated_session_id": generated_session_id,
        "total_cost_usd": result["total_cost_usd"],
        # The stream's init message names what funded the session: "none" is the
        # subscription login, whose figure is an API-equivalent recollection rather than
        # money that moved, and any named key is money. A stream that never said is
        # recorded as money, because the one lie this field must never tell is that real
        # spend was notional — and the source rides beside it so the record shows its work.
        "cost_is_notional": api_key_source == "none",
        "api_key_source": api_key_source,
        "turn_count": result["num_turns"],
        "subtype": result["subtype"],
        "terminal_reason": terminal_reason,
        "permission_denials": result["permission_denials"],
        "permission_mode": permission_mode,
        "deny_patterns": list(deny_patterns),
        "rate_limits": rate_limits,
        "process_exit": process_exit,
        "model": model,
    }
    if result["session_id"] != generated_session_id:
        raise CairnError(
            "provider_protocol",
            "result session_id does not match requested session",
            detail=detail,
        )
    if process_exit != 0:
        causes: dict[str, str] = {
            "blocking_limit": "rate_limited",
            "budget_exhausted": "budget_exhausted",
            "max_turns": "turn_limit",
            "structured_output_retry_exhausted": "provider_protocol",
        }
        cause = (
            causes.get(terminal_reason, "provider_failed")
            if terminal_reason is not None
            else "provider_failed"
        )
        return CommandResult(
            EXIT_RATE_LIMITED if cause == "rate_limited" else EXIT_FAILED,
            "failed",
            f"agent process ended with {cause}",
            [],
            False,
            cause,
            detail,
        )

    try:
        output = _object(structured, "structured_output")
        _required(
            output,
            ("status", "summary", "follow_up_work", "needs_user_decision"),
        )
        status = output["status"]
        summary = output["summary"]
        follow_up: object = output["follow_up_work"]
        decision = output["needs_user_decision"]
        if status not in ("done", "noop", "failed"):
            raise CairnError("provider_protocol", f"unknown structured status {status!r}")
        if not isinstance(summary, str):
            raise CairnError("provider_protocol", "structured summary is not a string")
        if not isinstance(follow_up, list):
            raise CairnError("provider_protocol", "follow_up_work is not a string array")
        follow_up_items = cast(list[object], follow_up)
        if not all(isinstance(item, str) for item in follow_up_items):
            raise CairnError("provider_protocol", "follow_up_work is not a string array")
        if not isinstance(decision, bool):
            raise CairnError("provider_protocol", "needs_user_decision is not a boolean")
    except CairnError as exc:
        exc.detail = detail
        raise

    if status == "failed":
        exit_code, cause = EXIT_FAILED, "reported_failure"
    elif decision:
        exit_code, cause = EXIT_FAILED, "user_decision_required"
    else:
        exit_code, cause = EXIT_OK, None
    return CommandResult(
        exit_code,
        status,
        summary,
        cast(list[str], follow_up_items),
        decision,
        cause,
        detail,
    )


def _send_prompt(stream: TextIO, prompt: str) -> None:
    try:
        stream.write(prompt)
    except (BrokenPipeError, ValueError):
        # The provider exited or was stopped first; its own status is the real outcome.
        pass
    finally:
        try:
            stream.close()
        except (BrokenPipeError, ValueError):
            pass


PROVIDER_BINARY = "claude"


def run_claude(
    prompt: str,
    working_directory: Path,
    permission_mode: str,
    model: str | None,
    budget: float | None,
    tools: list[str],
    popen_factory: PopenFactory = subprocess.Popen,
) -> CommandResult:
    """Run the selected plain-CLI path and translate its two status channels."""
    session_id = str(uuid.uuid4())
    command = [
        PROVIDER_BINARY,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(STEP_REPORT_SCHEMA, separators=(",", ":"), sort_keys=True),
        "--session-id",
        session_id,
        "--permission-mode",
        permission_mode,
    ]
    if model is not None:
        command.extend(("--model", model))
    if budget is not None:
        command.extend(("--max-budget-usd", str(budget)))
    for pattern in tools:
        command.extend(("--disallowedTools", pattern))

    process = launch(
        popen_factory,
        command,
        cwd=working_directory,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
    )
    writer: threading.Thread | None = None
    try:
        if process.stdin is None or process.stdout is None:
            raise CairnError("provider_protocol", "provider pipes were not created")
        # The prompt is a whole task document and the reply is a whole session: either
        # can outgrow a pipe buffer, so neither side may be written before the other is
        # being drained.
        writer = threading.Thread(
            target=_send_prompt, args=(process.stdin, prompt), daemon=True
        )
        writer.start()
        result, rate_limits, api_key_source = _parse_lines(process.stdout, tee=sys.stdout)
        process.stdout.close()
        exited_on_its_own = True
        try:
            return_code = process.wait(timeout=PROVIDER_EXIT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            # The result message is the provider's answer; a process that will not exit
            # after giving it is a leak to clean up, not a reason to discard the answer.
            stop_child(process)
            exited_on_its_own = False
            return_code = 0
    except BaseException:
        stop_child(process)
        raise
    finally:
        if writer is not None:
            writer.join(timeout=PROMPT_WRITER_JOIN_SECONDS)
        # Closing a pipe the writer thread still holds would block on its buffer lock,
        # turning a stuck provider into a stuck step.
        if process.stdin is not None and (writer is None or not writer.is_alive()):
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
    translated = _translate_result(
        return_code,
        result,
        rate_limits,
        generated_session_id=session_id,
        permission_mode=permission_mode,
        deny_patterns=tools,
        model=model,
        api_key_source=api_key_source,
    )
    if exited_on_its_own:
        return translated
    return translated._replace(
        detail={**translated.detail, "provider_exit": "stopped_after_result"}
    )


PROVIDER_RUNNERS: dict[str, ProviderRunner] = {
    "claude": run_claude,
}


def resume_command(session_id: str, working_directory: str) -> str:
    """The command a person pastes to open a step's session by hand.

    A receipt, not a control flow: each step runs as a fresh session by design, and chaining
    a prior one into a re-run would carry stale beliefs about a tree other steps have since
    changed ([step-protocol.md]). It is spelled here because this is the module that knows
    what a provider's command line looks like.
    """
    return (
        f"cd {shlex.quote(working_directory)} && "
        f"{PROVIDER_BINARY} --resume {shlex.quote(session_id)}"
    )


def run_provider(
    provider: str,
    prompt: str,
    working_directory: Path,
    permission_mode: str,
    model: str | None,
    budget: float | None,
    tools: list[str],
    *,
    runners: dict[str, ProviderRunner] = PROVIDER_RUNNERS,
    popen_factory: PopenFactory = subprocess.Popen,
) -> CommandResult:
    try:
        runner = runners[provider]
    except KeyError as exc:
        raise CairnError("provider_unavailable", f"unknown provider {provider!r}") from exc
    return runner(
        compose_prompt(prompt),
        working_directory,
        permission_mode,
        model,
        budget,
        tools,
        popen_factory,
    )
