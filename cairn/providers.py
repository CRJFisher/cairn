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
from cairn.hooks import HOOK_VERB, STOP_EVENT
from cairn.protocol import RESUME_FOR_REPORT, STEP_REPORT_SCHEMA, compose_prompt

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

# Denied in every step's session, ahead of whatever the plan denies. These are the tools
# whose whole meaning is "something will re-invoke you later", and under `claude -p` nothing
# ever does: a session that arms one waits for an event that cannot arrive and ends its turn
# holding work nobody will read.
#
# **`Monitor` and `Agent` are deliberately absent, and that is the measured half.** A monitor
# *blocks* — an until-loop over a file waited twelve seconds and the session read the file in
# the same turn — and a background subagent holds the process open and re-invokes the session
# once per completion, three in parallel all arriving. Both have a blocking form, so denying
# them would take away the two ways a step has of waiting for concurrent work in order to
# prevent a leak neither of them causes. What actually leaks is a background shell, which
# cannot be denied by name without denying `Bash` — so the preamble carries that one.
#
# Measured against the installed CLI: with these patterns passed, `ScheduleWakeup` and all
# three `Cron*` tools are gone from the session's tool list and `Monitor` and `Agent` remain.
# An unmatched deny pattern is silently a no-op, which reads as protection while being none.
NEVER_DELIVERED: tuple[str, ...] = ("ScheduleWakeup", "Cron*")


def hook_settings() -> str:
    """The `--settings` document arming the one hook a step's session runs under.

    Composed per invocation and passed as an argument, so nothing is written to any settings
    file on the machine — a step arms its own session and leaves nothing behind. Measured: a
    hook armed this way fires under `-p` and works independently of `--setting-sources`.

    `sys.executable` rather than a bare `python3`: the hook runs as a grandchild of this
    process, and naming the running interpreter removes the one PATH dependency it would
    otherwise carry.
    """
    command = f"{shlex.quote(sys.executable)} -m cairn {HOOK_VERB} {STOP_EVENT}"
    return json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}},
        separators=(",", ":"),
        sort_keys=True,
    )


def _session_in(
    command: list[str],
    prompt: str,
    working_directory: Path,
    popen_factory: PopenFactory,
) -> tuple[int, dict[str, Any], list[dict[str, Any]], str | None, bool]:
    """One provider invocation, drained to its result message.

    Factored out because a session may have to be opened twice — once for the work and once
    to collect a report it ended a turn without giving ([19 D]) — and the pipe handling here
    is exactly the part that must not be written twice.
    """
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
    return return_code, result, rate_limits, api_key_source, exited_on_its_own


def ended_without_reporting(process_exit: int, result: dict[str, Any]) -> bool:
    """Whether the session ended a turn without reporting, rather than failing.

    **Measured, and this is the trap:** a *correct* report is itself a tool call, so a
    session that reported returns `stop_reason: "tool_use"` too. The stop reason decides
    nothing on its own — the absent `structured_output` beside it is what says the session
    never reported. A rescue keyed on the reason alone would resume every successful step.

    A nonzero process exit is a different fact again, and `_translate_result` already has
    typed causes for each of its terminal reasons.
    """
    return (
        process_exit == 0
        and result.get("stop_reason") == "tool_use"
        and result.get("structured_output") is None
    )


# What a rescue did, as the run's record spells it. One vocabulary rather than a field that
# is sometimes `True` and sometimes a sentence, because a person reading the record has to
# be able to tell a rescue that was declined from one that was tried and did not help.
# The one fact the gate needs that `provider_protocol` is too broad to carry. That cause is
# raised for every unreadable-protocol fault — a malformed stream line, an unknown status, a
# summary that is not a string — and only one of them is a session that said nothing at all.
# The gate's sentence about a step turns on exactly this, so it is recorded rather than
# re-derived from a cause that means more than it ([verify.judge]).
ENDED_WITHOUT_REPORTING = "ended_without_reporting"

RESUME_ATTEMPTED = "attempted"
RESUME_DECLINED_BUDGET = "declined_budget_exhausted"
RESUME_FAILED = "resume_failed"
RESUME_STILL_SILENT = "still_silent"


def _as_float(value: object) -> float:
    """A number the provider reported, or zero — never a raise over a malformed figure."""
    try:
        return float(cast(float, value))
    except (TypeError, ValueError):
        return 0.0


def _as_list(value: object) -> list[Any]:
    return list(cast(list[Any], value)) if isinstance(value, list) else []


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
    # The plan's own list **adds** to Cairn's; it never replaces it. A plan cannot hand a
    # step back a tool whose contract the session cannot keep.
    denied = [*NEVER_DELIVERED, *tools]

    def invocation(*, budget_usd: float | None, resuming: bool) -> list[str]:
        """The argv for one session — a fresh one, or the same one asked to report.

        Exactly one of `--session-id` and `--resume`, never both and never neither, so a
        rescue continues the session it is rescuing rather than opening a second one.
        """
        composed = [
            PROVIDER_BINARY,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            json.dumps(STEP_REPORT_SCHEMA, separators=(",", ":"), sort_keys=True),
            "--resume" if resuming else "--session-id",
            session_id,
            "--permission-mode",
            permission_mode,
            # The session is held to the shape the preamble states: a turn does not end
            # while a background shell it started is still running ([hooks.py]).
            "--settings",
            hook_settings(),
        ]
        if model is not None:
            composed.extend(("--model", model))
        if budget_usd is not None:
            composed.extend(("--max-budget-usd", str(budget_usd)))
        for pattern in denied:
            composed.extend(("--disallowedTools", pattern))
        return composed

    return_code, result, rate_limits, api_key_source, exited_on_its_own = _session_in(
        invocation(budget_usd=budget, resuming=False),
        prompt,
        working_directory,
        popen_factory,
    )

    rescue: dict[str, Any] = {}
    if ended_without_reporting(return_code, result):
        # A session that ended a turn without reporting is not a session that failed: it
        # may have done all of the work and simply stopped short of saying so. Measured,
        # the alternative was discarding $10.89 of work an assertion had just proved.
        spent = _as_float(result.get("total_cost_usd"))
        remaining = None if budget is None else round(budget - spent, 6)
        if remaining is not None and remaining <= 0:
            # The offer priced one ceiling for this step and a second invocation carrying a
            # fresh full budget would double the ceiling the person agreed to.
            rescue = {
                ENDED_WITHOUT_REPORTING: True,
                "resumed_for_report": RESUME_DECLINED_BUDGET,
            }
        else:
            # Recorded **before** the attempt. A resume that fails at the protocol level
            # raises out of `_session_in`, and an account written afterwards would never
            # reach the report — leaving nobody able to tell a rescue that failed from one
            # never tried, which is the whole of what this key is for.
            rescue = {
                ENDED_WITHOUT_REPORTING: True,
                "resumed_for_report": RESUME_ATTEMPTED,
                "abandoned_cost_usd": spent,
            }
            # What the first pass is worth saying even if the resume never answers. Its
            # cost is the figure the record is read for, and it is the money the rescue
            # exists to protect.
            first_detail: dict[str, Any] = {
                "session_id": result.get("session_id"),
                "total_cost_usd": spent,
                "turn_count": result.get("num_turns"),
            }
            try:
                (
                    resumed_code,
                    resumed,
                    more_limits,
                    more_source,
                    resumed_exited,
                ) = _session_in(
                    invocation(budget_usd=remaining, resuming=True),
                    RESUME_FOR_REPORT,
                    working_directory,
                    popen_factory,
                )
            except CairnError as unreachable:
                # The first session's account is now the only one there is, and its cost is
                # the number the record is read for.
                unreachable.detail = {
                    **first_detail,
                    **unreachable.detail,
                    **rescue,
                    "resumed_for_report": RESUME_FAILED,
                }
                raise
            # A first process that had to be stopped after giving its result is a leak the
            # record names; the resume's own exit must not erase it.
            exited_on_its_own = exited_on_its_own and resumed_exited
            rate_limits = [*rate_limits, *more_limits]
            api_key_source = more_source or api_key_source
            # Measured: a resumed session reports its **own invocation's** cost and turns
            # rather than the session's cumulative totals (0.0168 then 0.0031 over two
            # passes of one session), so a step's spend is the sum of the two.
            both_costs = spent + _as_float(resumed.get("total_cost_usd"))
            if resumed.get("structured_output") is None:
                # The resume did not report either. Keep the **first** pass's result, so
                # the step is still recorded `provider_protocol` rather than taking on
                # whatever ended the resume — a budget exhausted a cent short, or a rate
                # limit, would otherwise be read by the gate as a step that reported
                # failure, which is the exact sentence this cause exists to remove.
                result["total_cost_usd"] = both_costs
                rescue = {**rescue, "resumed_for_report": RESUME_STILL_SILENT}
            else:
                resumed["total_cost_usd"] = both_costs
                resumed["num_turns"] = int(_as_float(result.get("num_turns"))) + int(
                    _as_float(resumed.get("num_turns"))
                )
                # Every denial the step met, not only the resume's — a first session walled
                # by a permission is a plausible reason it stopped short of reporting.
                resumed["permission_denials"] = [
                    *_as_list(result.get("permission_denials")),
                    *_as_list(resumed.get("permission_denials")),
                ]
                return_code, result = resumed_code, resumed

    try:
        translated = _translate_result(
            return_code,
            result,
            rate_limits,
            generated_session_id=session_id,
            permission_mode=permission_mode,
            deny_patterns=denied,
            model=model,
            api_key_source=api_key_source,
        )
    except CairnError as unreported:
        # The rescue's own account has to survive the failure it was trying to prevent:
        # a step recorded `provider_protocol` with no word about whether a resume was
        # attempted leaves nobody able to tell a rescue that failed from one never tried.
        unreported.detail = {**unreported.detail, **rescue}
        raise
    detail = {**translated.detail, **rescue}
    if not exited_on_its_own:
        detail["provider_exit"] = "stopped_after_result"
    return translated._replace(detail=detail)


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
