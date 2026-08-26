import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Generator
from contextlib import chdir, contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch

from cairn.__main__ import COMMAND_HANDLERS, main
from cairn.commands import run_exec, run_wait_duration, run_wait_until, stop_child
from cairn.core import (
    EXIT_OK,
    CairnError,
    CommandResult,
    PopenFactory,
    RuntimeContext,
    write_report,
)
from cairn.emitters import KIND_EMITTERS, emit_step, emit_verify
from cairn.layout import reports_directory
from cairn.plan.schema import normalise
from cairn.protocol import RESUME_FOR_REPORT, compose_prompt
from cairn.providers import (
    ENDED_WITHOUT_REPORTING,
    NEVER_DELIVERED,
    PROVIDER_RUNNERS,
    RESUME_ATTEMPTED,
    RESUME_DECLINED_BUDGET,
    RESUME_STILL_SILENT,
    ended_without_reporting,
    run_claude,
    run_provider,
)


def run_echo(
    prompt: str,
    working_directory: Path,
    permission_mode: str,
    model: str | None,
    budget: float | None,
    tools: list[str],
    popen_factory: PopenFactory = subprocess.Popen,
) -> CommandResult:
    """A whole second provider: doc 05's seam claim is that this is all one costs."""
    del popen_factory
    return CommandResult(
        EXIT_OK,
        "done",
        prompt,
        [],
        False,
        None,
        {
            "working_directory_seen": str(working_directory),
            "permission_mode": permission_mode,
            "model": model,
            "max_budget_usd": budget,
            "deny_patterns": list(tools),
        },
    )


# Neither of these waits is the subject of any test here: one waits for an interpreter to
# start, the other for a signalled process to finish dying. So the bound is generous on
# purpose. It costs nothing when the thing works, because every wait returns the moment it
# does; a tight one only turns a loaded machine into a failing suite, which is what a
# one-second bound on an interpreter start plus two git invocations had been doing.
#
# A deadline rather than an iteration count, because a count multiplied by a sleep is not a
# duration — every turn of the loop also pays for its own syscalls, so the same number means
# a different bound on every machine.
READY_SECONDS = 5.0
GONE_SECONDS = 5.0
POLL_SECONDS = 0.01


def wait_for_file(path: Path, *, seconds: float = READY_SECONDS) -> bool:
    """Whether the file appeared — which is how a launched process says it is running."""
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            return path.exists()
        time.sleep(POLL_SECONDS)
    return True


def wait_for_exit(pid: int, *, seconds: float = GONE_SECONDS) -> bool:
    """Whether the process is gone — which is how it says the signal reached it."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def runtime_env(root: Path) -> dict[str, str]:
    return {
        "DAG_RUN_ID": "run-1",
        "DAG_RUN_STEP_NAME": "step_a",
        "DAG_RUN_WORK_DIR": str(root),
        "CAIRN_RUNS_DIR": str(root / "runs"),
    }


def report_file(env: dict[str, str], step: str = "step_a") -> Path:
    """Where a step's account lands, composed the way the step itself composes it."""
    return reports_directory(Path(env["CAIRN_RUNS_DIR"]), env["DAG_RUN_ID"]) / f"{step}.json"


@contextmanager
def engine_step(root: Path, env: dict[str, str] | None = None) -> Generator[None]:
    """Run as the engine runs a step: its injected identity, in its working directory."""
    values = runtime_env(root) if env is None else env
    with patch.dict(os.environ, values, clear=True), chdir(root):
        yield


def step(
    kind: str,
    task: str = "printf '%s' hello",
    command_type: str | None = "exec",
) -> Any:
    raw: dict[str, Any] = {
        "plan": {"slug": "p", "title": "P", "source": "README.md"},
        "steps": [
            {
                "id": "a",
                "slug": "a",
                "title": "A",
                "task": task,
                "verify": "test -f result",
                "kind": kind,
            }
        ],
    }
    if kind == "command":
        raw["steps"][0]["command"] = task
        raw["steps"][0]["command_type"] = command_type or "exec"
    return normalise(raw)["steps"][0]


class ContextAndReport(unittest.TestCase):
    def test_context_uses_override_and_work_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with engine_step(root):
                context = RuntimeContext.from_env(runtime_env(root))
            self.assertEqual(context.working_directory, root.resolve())
            self.assertEqual(context.report_path, root / "runs/run-1/reports/step_a.json")

    def test_the_step_directory_is_never_the_engines_own_scratch_directory(self) -> None:
        # `DAG_RUN_WORK_DIR` names a directory under the run's data, not the step's
        # `working_dir` [V]. A subcommand that used it would run every git command
        # somewhere that is not a repository at all.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scratch = root / "engine-scratch"
            scratch.mkdir()
            step_directory = root / "checkout"
            step_directory.mkdir()
            env = runtime_env(root)
            env["DAG_RUN_WORK_DIR"] = str(scratch)
            with engine_step(step_directory, env):
                context = RuntimeContext.from_env(env)
        self.assertEqual(context.working_directory, step_directory.resolve())

    def test_the_report_path_is_keyed_by_the_run_beneath_the_runs_root(self) -> None:
        """Two runs against one repository must never write over each other's accounts."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = runtime_env(root)
            context = RuntimeContext.from_env(env)
            self.assertEqual(
                context.report_path, root / "runs" / "run-1" / "reports" / "step_a.json"
            )
            later = RuntimeContext.from_env({**env, "DAG_RUN_ID": "run-2"})
            self.assertNotEqual(context.report_path, later.report_path)

    def test_a_run_id_that_would_leave_the_runs_root_is_refused(self) -> None:
        """The run id is a caller's string and becomes a path segment."""
        with tempfile.TemporaryDirectory() as temporary:
            env = {**runtime_env(Path(temporary)), "DAG_RUN_ID": "../escape"}
            with self.assertRaises(CairnError) as caught:
                RuntimeContext.from_env(env)
            self.assertEqual(caught.exception.cause, "invalid_run_id")

    def test_missing_identity_is_loud(self) -> None:
        with self.assertRaisesRegex(CairnError, "DAG_RUN_ID"):
            RuntimeContext.from_env({})

    def test_report_is_shared_shape_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = RuntimeContext.from_env(runtime_env(Path(temporary)))
            report = write_report(
                context, CommandResult(0, "done", "ok", [], False, None, {"command": "true"}), 1.25
            )
            self.assertEqual(
                set(report),
                {
                    "step_id",
                    "run_id",
                    "status",
                    "duration",
                    "working_directory",
                    "summary",
                    "follow_up_work",
                    "needs_user_decision",
                    "cause",
                    "detail",
                },
            )
            self.assertIsNone(report["cause"])
            self.assertEqual(json.loads(context.report_path.read_text()), report)
            self.assertEqual(list(context.report_path.parent.glob("*.tmp")), [])

            # Two ways a write can fail, and neither may leave a half-written report or a
            # stray temporary behind: one before the file is opened at all, and one after
            # the bytes are down but before the replacement makes them the report.
            for where in ("cairn.core.json.dumps", "cairn.core.os.replace"):
                with (
                    self.subTest(where=where),
                    patch(where, side_effect=RuntimeError("write failed")),
                    self.assertRaisesRegex(RuntimeError, "write failed"),
                ):
                    write_report(
                        context,
                        CommandResult(1, "failed", "new", [], False, "command_failed", {}),
                        2,
                    )
                self.assertEqual(json.loads(context.report_path.read_text()), report)
                self.assertEqual(list(context.report_path.parent.glob("*.tmp")), [])


class ExecAndWait(unittest.TestCase):
    def test_exec_runs_in_context_and_preserves_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_exec(
                f"{shlex.quote(sys.executable)} -c 'import os,sys; "
                "open(\"cwd\", \"w\").write(os.getcwd()); sys.exit(7)'",
                Path(temporary),
                "/bin/sh",
            )
            self.assertEqual(result[0], 7)
            self.assertEqual(result[1], "failed")
            self.assertEqual(result[5], "command_failed")
            self.assertEqual(Path(Path(temporary, "cwd").read_text()).resolve(), Path(temporary).resolve())

    def test_exec_calls_local_http_endpoint(self) -> None:
        seen: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen.append(self.path)
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/ready"
            command = (
                f"{shlex.quote(sys.executable)} -c "
                + shlex.quote(f"import urllib.request; urllib.request.urlopen({url!r})")
            )
            result = run_exec(command, Path.cwd(), "/bin/sh")
            self.assertEqual(result[0], 0)
            self.assertEqual(seen, ["/ready"])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_duration_is_positive_and_bounded(self) -> None:
        self.assertEqual(run_wait_duration(0.5, 1, sleeper=lambda _n: None)[1], "done")
        for duration, timeout in ((0, 1), (2, 1)):
            with self.subTest(duration=duration), self.assertRaises(CairnError):
                run_wait_duration(duration, timeout, sleeper=lambda _n: None)

    def test_until_polls_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary, "ready")
            command = f"test -f {shlex.quote(str(marker))}"
            calls = 0

            def sleeper(_seconds: float) -> None:
                nonlocal calls
                calls += 1
                marker.touch()

            result = run_wait_until(
                command,
                Path(temporary),
                "/bin/sh",
                2,
                0.01,
                sleeper=sleeper,
            )
            self.assertEqual(result[1], "done")
            self.assertEqual(result[6]["attempts"], 2)
            self.assertEqual(calls, 1)

    def test_until_times_out_with_cause(self) -> None:
        result = run_wait_until("false", Path.cwd(), "/bin/sh", 0.02, 0.01)
        self.assertNotEqual(result[0], 0)
        self.assertEqual(result[5], "wait_timeout")

    def test_cleanup_terminates_active_child(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            text=True,
            start_new_session=True,
        )
        stop_child(process)
        self.assertIsNotNone(process.poll())

    def test_timeout_terminates_condition_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pid_path = Path(temporary, "condition.pid")
            script = (
                "import os,pathlib,signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                "time.sleep(30)"
            )
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
            result = run_wait_until(
                command, Path(temporary), "/bin/sh", 5.0, 0.01
            )
            self.assertEqual(result[5], "wait_timeout")
            self.assertTrue(
                pid_path.exists(), "condition never started inside the wait bound"
            )
            condition_pid = int(pid_path.read_text())
            self.assertTrue(
                wait_for_exit(condition_pid),
                f"condition process {condition_pid} survived wait timeout",
            )

    def test_condition_inherits_the_engine_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            group_path = Path(temporary, "group")
            script = (
                "import os,pathlib; "
                f"pathlib.Path({str(group_path)!r}).write_text(str(os.getpgrp()))"
            )
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
            result = run_wait_until(
                command, Path(temporary), "/bin/sh", 10.0, 0.05
            )
            self.assertEqual(result[1], "done")
            self.assertEqual(int(group_path.read_text()), os.getpgrp())

    def test_cli_dispatch_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with engine_step(Path(temporary), env):
                self.assertEqual(main(["exec", "--command", "exit 3"]), 3)
            report = json.loads(report_file(env).read_text())
            self.assertEqual(report["cause"], "command_failed")
            self.assertEqual(report["detail"]["process_exit"], 3)

    def test_cli_cairn_error_writes_typed_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with engine_step(Path(temporary), env):
                self.assertEqual(
                    main(["wait", "--for", "0", "--timeout", "1"]),
                    1,
                )
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["cause"], "invalid_wait")

    def test_interrupted_wait_stops_condition_and_records_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {**os.environ, **runtime_env(root)}
            pid_path = root / "condition.pid"
            condition = (
                f"{shlex.quote(sys.executable)} -c "
                + shlex.quote(
                    "import os,pathlib,time; "
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                    "time.sleep(30)"
                )
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cairn",
                    "wait",
                    "--until",
                    condition,
                    "--timeout",
                    "30",
                ],
                cwd=Path(__file__).parents[1],
                env=env,
                start_new_session=True,
            )
            if not wait_for_file(pid_path):
                process.kill()
                self.fail("condition process did not start")

            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10), 1)
            condition_pid = int(pid_path.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(condition_pid, 0)
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["cause"], "cancelled")

    def test_interrupted_exec_stops_its_child_and_records_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {**os.environ, **runtime_env(root)}
            pid_path = root / "child.pid"
            body = (
                f"{shlex.quote(sys.executable)} -c "
                + shlex.quote(
                    "import os,pathlib,time; "
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                    "time.sleep(30)"
                )
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "cairn", "exec", "--command", body],
                cwd=Path(__file__).parents[1],
                env=env,
                start_new_session=True,
            )
            if not wait_for_file(pid_path):
                process.kill()
                self.fail("exec child did not start")

            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10), 1)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_path.read_text()), 0)
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["cause"], "cancelled")

    def test_an_unrecordable_outcome_degrades_to_a_report(self) -> None:
        def out_of_vocabulary(_args: Any, _context: RuntimeContext) -> Any:
            return CommandResult(0, "finished", "", [], False, None, {})

        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with (
                engine_step(Path(temporary), env),
                patch.dict(COMMAND_HANDLERS, {"exec": out_of_vocabulary}),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(["exec", "--command", "true"]), 1)
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["cause"], "invalid_report")

    def test_a_timed_out_wait_leaves_no_orphan_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {**os.environ, **runtime_env(root)}
            pid_path = root / "helpers"
            # A condition that backgrounds a helper and then fails: every poll would
            # otherwise leave one more sleeper behind, and the step exits on its own, so
            # the engine never group-kills them.
            condition = f"sleep 60 & echo $! >> {shlex.quote(str(pid_path))}; false"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cairn",
                    "wait",
                    "--until",
                    condition,
                    "--timeout",
                    "1",
                    "--interval",
                    "0.1",
                ],
                cwd=Path(__file__).parents[1],
                env=env,
                start_new_session=True,
            )
            self.assertEqual(process.wait(timeout=30), 1)
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["cause"], "wait_timeout")

            helpers = [
                int(line) for line in pid_path.read_text().split() if line.isdigit()
            ]
            self.assertTrue(helpers, "the condition never started a helper")
            survivors: list[int] = []
            for pid in helpers:
                if not wait_for_exit(pid):
                    survivors.append(pid)
                    os.kill(pid, signal.SIGKILL)
            self.assertEqual(survivors, [])

    def test_unclassified_crash_still_leaves_a_report(self) -> None:
        def crash(_args: Any, _context: RuntimeContext) -> Any:
            raise ValueError("no handler contract covers this")

        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with (
                engine_step(Path(temporary), env),
                patch.dict(COMMAND_HANDLERS, {"exec": crash}),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(["exec", "--command", "true"]), 1)
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["cause"], "internal_error")
            self.assertEqual(report["detail"]["exception"], "ValueError")

    def test_cli_reports_process_launch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with engine_step(Path(temporary), env):
                self.assertEqual(
                    main(["exec", "--command", "true", "--shell", "/no/such/shell"]),
                    1,
                )
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["cause"], "process_launch_failed")

    def test_cli_uses_dictionary_dispatch(self) -> None:
        self.assertEqual(
            set(COMMAND_HANDLERS),
            {
                "exec",
                "wait",
                "agent",
                "marker",
                "lock",
                "worktree",
                "commit",
                "merge",
                "wave",
            },
        )

    def test_handlers_take_the_working_directory_from_the_engine(self) -> None:
        seen: list[Path] = []

        def record(
            _command: str, working_directory: Path, _shell: str
        ) -> CommandResult:
            seen.append(working_directory)
            return CommandResult(0, "done", "", [], False, None, {})

        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with (
                engine_step(Path(temporary), env),
                patch("cairn.__main__.run_exec", record),
            ):
                self.assertEqual(main(["exec", "--command", "true"]), 0)
        self.assertEqual(seen, [Path(temporary).resolve()])

    def test_agent_handler_carries_the_plan_tool_policy_to_the_provider(self) -> None:
        seen: list[tuple[str, Path, str | None, float | None, list[str]]] = []

        def record(
            provider: str,
            _prompt: str,
            working_directory: Path,
            _permission_mode: str,
            model: str | None,
            budget: float | None,
            tools: list[str],
        ) -> CommandResult:
            seen.append((provider, working_directory, model, budget, tools))
            return CommandResult(0, "done", "", [], False, None, {})

        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with (
                engine_step(Path(temporary), env),
                patch("cairn.__main__.run_provider", record),
            ):
                self.assertEqual(
                    main(
                        [
                            "agent",
                            "run",
                            "--provider",
                            "claude",
                            "--prompt",
                            "do work",
                            "--model",
                            "opus",
                            "--max-budget-usd",
                            "3",
                            "--tool",
                            "Bash(rm:*)",
                            "--tool",
                            "Write",
                        ]
                    ),
                    0,
                )
        self.assertEqual(
            seen,
            [
                (
                    "claude",
                    Path(temporary).resolve(),
                    "opus",
                    3.0,
                    ["Bash(rm:*)", "Write"],
                )
            ],
        )

    def test_argument_skew_is_a_report_not_a_usage_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with (
                engine_step(Path(temporary), env),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(["exec", "--command", "true", "--unknown"]), 1)
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["cause"], "invalid_arguments")

    def test_a_relative_shell_is_refused_before_anything_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with (
                engine_step(Path(temporary), env),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    main(["exec", "--command", "true", "--shell", "sh"]), 1
                )
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["cause"], "invalid_command")

    def test_a_run_without_a_locatable_report_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            del env["CAIRN_RUNS_DIR"]
            with self.assertRaisesRegex(CairnError, "CAIRN_RUNS_DIR"):
                RuntimeContext.from_env(env)

    def test_unknown_provider_is_a_typed_refusal(self) -> None:
        with self.assertRaises(CairnError) as caught:
            run_provider("nobody", "x", Path.cwd(), "auto", None, None, [])
        self.assertEqual(caught.exception.cause, "provider_unavailable")

    def test_a_signalled_command_reports_the_shell_status(self) -> None:
        result = run_exec(
            f"{shlex.quote(sys.executable)} -c 'import os,signal;"
            " os.kill(os.getpid(), signal.SIGKILL)'",
            Path.cwd(),
            "/bin/sh",
        )
        self.assertEqual(result.exit_code, 137)
        self.assertEqual(result.cause, "command_failed")

    def test_exec_child_inherits_the_engine_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            group_path = Path(temporary, "group")
            script = (
                "import os,pathlib; "
                f"pathlib.Path({str(group_path)!r}).write_text(str(os.getpgrp()))"
            )
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
            self.assertEqual(run_exec(command, Path(temporary), "/bin/sh").status, "done")
            self.assertEqual(int(group_path.read_text()), os.getpgrp())

    def test_every_bound_is_finite(self) -> None:
        for bound in (float("inf"), float("nan")):
            with self.subTest(bound=bound):
                with self.assertRaises(CairnError) as caught:
                    run_wait_until("false", Path.cwd(), "/bin/sh", bound, 0.01)
                self.assertEqual(caught.exception.cause, "invalid_wait")
                with self.assertRaises(CairnError):
                    run_wait_duration(1, bound, sleeper=lambda _n: None)

    def test_polling_never_sleeps_past_the_bound(self) -> None:
        slept: list[float] = []
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def sleeper(seconds: float) -> None:
            slept.append(seconds)
            clock[0] += seconds

        result = run_wait_until(
            "false",
            Path.cwd(),
            "/bin/sh",
            0.25,
            10.0,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        self.assertEqual(result.cause, "wait_timeout")
        self.assertLessEqual(sum(slept), 0.25)

    def test_cancellation_reaches_a_grandchild_the_shell_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {**os.environ, **runtime_env(root)}
            pid_path = root / "grandchild.pid"
            inner = (
                f"{shlex.quote(sys.executable)} -c "
                + shlex.quote(
                    "import os,pathlib,time; "
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                    "time.sleep(30)"
                )
            )
            # The trailing `; true` denies the shell its exec optimisation, so the
            # sleeper is a grandchild rather than the direct child cairn holds.
            process = subprocess.Popen(
                [sys.executable, "-m", "cairn", "exec", "--command", f"{inner}; true"],
                cwd=Path(__file__).parents[1],
                env=env,
                start_new_session=True,
            )
            if not wait_for_file(pid_path):
                process.kill()
                self.fail("grandchild did not start")

            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10), 1)
            grandchild = int(pid_path.read_text())
            if not wait_for_exit(grandchild):
                os.kill(grandchild, signal.SIGKILL)
                self.fail(f"grandchild {grandchild} survived cancellation")


class FakeInput:
    def __init__(self) -> None:
        self.value = ""

    def write(self, value: str) -> int:
        self.value += value
        return len(value)

    def close(self) -> None:
        pass


class FakeOutput:
    def __init__(self, text: str) -> None:
        self._lines = iter(text.splitlines(keepends=True))
        self.consumed = False

    def __iter__(self) -> "FakeOutput":
        return self

    def __next__(self) -> str:
        self.consumed = True
        return next(self._lines)

    def close(self) -> None:
        pass


class FakeProcess:
    """A provider double that models exit state, so cleanup assertions are not no-ops."""

    def __init__(self, command: list[str], **kwargs: object) -> None:
        self.command = command
        self.kwargs = kwargs
        self.returncode: int | None = 0
        self.pid = 2**30
        self.stopped = False
        self.finished = False
        self.stdin = FakeInput()
        self.stdout = FakeOutput(json.dumps(self.output()) + "\n")

    def session(self) -> str:
        flag = "--session-id" if "--session-id" in self.command else "--resume"
        return self.command[self.command.index(flag) + 1]

    def output(self) -> dict[str, Any]:
        session_id = self.session()
        return {
            "type": "result",
            "subtype": "success",
            "session_id": session_id,
            "total_cost_usd": 0.25,
            "num_turns": 2,
            "permission_denials": [],
            "structured_output": {
                "status": "done",
                "summary": "finished",
                "follow_up_work": [],
                "needs_user_decision": False,
            },
        }

    @property
    def prompt(self) -> str:
        return self.stdin.value

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if not self.stdout.consumed:
            raise AssertionError("provider waited before consuming its stream")
        self.finished = True
        return self.returncode if self.returncode is not None else 0

    def poll(self) -> int | None:
        return self.returncode if self.finished else None

    def terminate(self) -> None:
        self.stopped = True
        self.finished = True

    def kill(self) -> None:
        self.terminate()


class ProviderBehavior(unittest.TestCase):
    def test_the_deny_list_names_only_what_a_session_cannot_wait_for(self) -> None:
        """Measured under `claude -p`: nothing ever fires a scheduled wakeup and nothing
        drains a cron, so both are promises the harness cannot keep. `Monitor` blocks and a
        background subagent re-invokes the session on completion — three in parallel all
        arrived — so denying either would take away the two ways a step has of waiting for
        concurrent work, to prevent a leak neither of them causes ([19 D])."""
        self.assertEqual(NEVER_DELIVERED, ("ScheduleWakeup", "Cron*"))
        self.assertNotIn("Monitor", NEVER_DELIVERED)
        self.assertNotIn("Agent", NEVER_DELIVERED)

    def test_the_record_shows_what_the_session_was_actually_denied(self) -> None:
        """A record naming only the plan's half would understate the run."""
        made: list[FakeProcess] = []

        def factory(command: list[str], **kwargs: object) -> FakeProcess:
            process = FakeProcess(command, **kwargs)
            made.append(process)
            return process

        stream = io.StringIO()
        with redirect_stdout(stream):
            result = run_claude(
                "do work", Path("/tmp"), "auto", None, None, ["Bash(rm:*)"], factory
            )
        self.assertEqual(
            result.detail["deny_patterns"], [*NEVER_DELIVERED, "Bash(rm:*)"]
        )

    def test_plain_invocation_and_optional_flags(self) -> None:
        made: list[FakeProcess] = []

        def factory(command: list[str], **kwargs: object) -> FakeProcess:
            process = FakeProcess(command, **kwargs)
            made.append(process)
            return process

        stream = io.StringIO()
        with redirect_stdout(stream):
            result = run_claude(
                "do work",
                Path("/tmp"),
                "auto",
                "opus",
                2.5,
                ["Bash(rm:*)"],
                factory,
            )
        command = made[0].command
        self.assertEqual(command[:2], ["claude", "-p"])
        self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", command)
        self.assertIn("--json-schema", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "auto")
        self.assertEqual(command[command.index("--model") + 1], "opus")
        self.assertEqual(command[command.index("--max-budget-usd") + 1], "2.5")
        denied = [
            command[index + 1]
            for index, word in enumerate(command)
            if word == "--disallowedTools"
        ]
        # Cairn's own patterns lead and the plan's are added to them, never in place of
        # them: a plan cannot hand a step back a tool whose contract it cannot keep.
        self.assertEqual(denied, [*NEVER_DELIVERED, "Bash(rm:*)"])
        self.assertEqual(
            denied[-1],
            "Bash(rm:*)",
        )
        self.assertNotIn("start_new_session", made[0].kwargs)
        self.assertEqual(made[0].prompt, "do work")
        self.assertIn('"type": "result"', stream.getvalue())
        self.assertEqual(result[1], "done")
        self.assertIsNone(result[5])

    def test_the_stream_says_who_funded_the_session_and_the_record_keeps_it(self) -> None:
        """`apiKeySource: none` is the subscription login, whose figure is an API
        equivalent rather than money that moved; a named key is money."""

        def funded(source: str) -> type[FakeProcess]:
            class Funded(FakeProcess):
                def __init__(self, command: list[str], **kwargs: object) -> None:
                    super().__init__(command, **kwargs)
                    init = {"type": "system", "subtype": "init", "apiKeySource": source}
                    self.stdout = FakeOutput(
                        json.dumps(init) + "\n" + json.dumps(self.output()) + "\n"
                    )

            return Funded

        for source, notional in (("none", True), ("ANTHROPIC_API_KEY", False)):
            with self.subTest(source=source):
                with redirect_stdout(io.StringIO()):
                    result = run_claude(
                        "do work", Path("/tmp"), "auto", None, None, [], funded(source)
                    )
                self.assertEqual(result.detail["cost_is_notional"], notional)
                self.assertEqual(result.detail["api_key_source"], source)

    def test_a_stream_that_never_says_who_funded_it_is_recorded_as_real_spend(self) -> None:
        """The one lie the field must never tell is that real spend was notional."""
        with redirect_stdout(io.StringIO()):
            result = run_claude("do work", Path("/tmp"), "auto", None, None, [], FakeProcess)
        self.assertFalse(result.detail["cost_is_notional"])
        self.assertIsNone(result.detail["api_key_source"])

    def test_structured_failure_translates_zero_exit(self) -> None:
        class Failed(FakeProcess):
            def output(self) -> dict[str, Any]:
                record = super().output()
                record["structured_output"]["status"] = "failed"
                return record

        result = run_claude(
            "fail", Path.cwd(), "auto", None, None, [], Failed
        )
        self.assertNotEqual(result[0], 0)
        self.assertEqual(result[5], "reported_failure")

    def test_missing_stream_field_is_protocol_failure(self) -> None:
        class Missing(FakeProcess):
            def output(self) -> dict[str, Any]:
                record = super().output()
                del record["num_turns"]
                return record

        with self.assertRaisesRegex(CairnError, "num_turns"):
            run_claude("x", Path.cwd(), "auto", None, None, [], Missing)

    def test_nonzero_provider_outcomes_keep_typed_causes_without_a_report(self) -> None:
        causes = {
            "blocking_limit": "rate_limited",
            "budget_exhausted": "budget_exhausted",
            "max_turns": "turn_limit",
            "structured_output_retry_exhausted": "provider_protocol",
        }
        def failed_factory(reason: str) -> type[FakeProcess]:
            class Failed(FakeProcess):
                def __init__(self, command: list[str], **kwargs: object) -> None:
                    super().__init__(command, **kwargs)
                    self.returncode = 1

                def output(self) -> dict[str, Any]:
                    record = super().output()
                    record["terminal_reason"] = reason
                    del record["structured_output"]
                    return record

            return Failed

        for terminal_reason, expected in causes.items():
            with self.subTest(terminal_reason=terminal_reason), redirect_stdout(
                io.StringIO()
            ):
                result = run_claude(
                    "x",
                    Path.cwd(),
                    "auto",
                    None,
                    None,
                    [],
                    failed_factory(terminal_reason),
                )
            self.assertEqual(result[5], expected)
            self.assertEqual(result[6]["session_id"], result[6]["generated_session_id"])

    def test_protocol_failure_preserves_available_provider_detail(self) -> None:
        class Mismatched(FakeProcess):
            def output(self) -> dict[str, Any]:
                record = super().output()
                record["session_id"] = "foreign-session"
                return record

        with self.assertRaises(CairnError) as caught:
            run_claude(
                "x", Path.cwd(), "auto", None, None, [], Mismatched
            )
        self.assertEqual(caught.exception.cause, "provider_protocol")
        self.assertEqual(caught.exception.detail["session_id"], "foreign-session")
        self.assertEqual(caught.exception.detail["total_cost_usd"], 0.25)

    def test_protocol_failure_terminates_provider(self) -> None:
        made: list[subprocess.Popen[str]] = []

        def factory(_command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import time; print('not-json', flush=True); time.sleep(30)",
                ],
                **kwargs,
            )
            made.append(process)
            return process

        with (
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(CairnError, "not valid JSON"),
        ):
            run_claude("x", Path.cwd(), "auto", None, None, [], factory)
        self.assertIsNotNone(made[0].poll())

    def test_prompt_and_stream_larger_than_a_pipe_buffer_both_flow(self) -> None:
        def factory(command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
            session_id = command[command.index("--session-id") + 1]
            record = json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": session_id,
                    "total_cost_usd": 0.0,
                    "num_turns": 1,
                    "permission_denials": [],
                    "structured_output": {
                        "status": "done",
                        "summary": "large exchange survived",
                        "follow_up_work": [],
                        "needs_user_decision": False,
                    },
                }
            )
            script = (
                "import sys\n"
                'sys.stdout.write(\'{"type": "noise"}\\n\' * 20000)\n'
                "sys.stdout.flush()\n"
                "received = sys.stdin.read()\n"
                f"sys.stdout.write({record!r} + '\\n')\n"
                'sys.stdout.write(\'{"type": "echo", "length": %d}\\n\' % len(received))\n'
            )
            process = subprocess.Popen([sys.executable, "-c", script], **kwargs)
            started.append(process)
            return process

        started: list[subprocess.Popen[str]] = []
        prompt = "p" * (1 << 18)
        outcome: list[Any] = []

        def call() -> None:
            with redirect_stdout(io.StringIO()):
                outcome.append(
                    run_claude("x" + prompt, Path.cwd(), "auto", None, None, [], factory)
                )

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        thread.join(timeout=20)
        try:
            self.assertFalse(
                thread.is_alive(),
                "provider deadlocked when prompt and stream both exceeded the pipe buffer",
            )
            self.assertTrue(outcome, "provider thread raised; see the traceback above")
            self.assertEqual(outcome[0].status, "done")
        finally:
            for process in started:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_an_answer_survives_a_provider_that_will_not_exit(self) -> None:
        started: list[subprocess.Popen[str]] = []

        def factory(command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
            session_id = command[command.index("--session-id") + 1]
            record = json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": session_id,
                    "total_cost_usd": 0.0,
                    "num_turns": 1,
                    "permission_denials": [],
                    "structured_output": {
                        "status": "done",
                        "summary": "answered then lingered",
                        "follow_up_work": [],
                        "needs_user_decision": False,
                    },
                }
            )
            # Answers, then holds its stdout open forever — what a provider that spawns
            # its own long-lived children looks like from here.
            script = (
                "import sys, time\n"
                f"sys.stdout.write({record!r} + '\\n')\n"
                "sys.stdout.flush()\n"
                "time.sleep(300)\n"
            )
            process = subprocess.Popen([sys.executable, "-c", script], **kwargs)
            started.append(process)
            return process

        try:
            with (
                patch("cairn.providers.PROVIDER_EXIT_GRACE_SECONDS", 0.5),
                redirect_stdout(io.StringIO()),
            ):
                result = run_claude("x", Path.cwd(), "auto", None, None, [], factory)
            self.assertEqual(result.status, "done")
            self.assertEqual(result.summary, "answered then lingered")
            self.assertIsNone(result.cause)
            self.assertEqual(result.detail["provider_exit"], "stopped_after_result")
            self.assertIsNotNone(started[0].poll())
        finally:
            for process in started:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_a_second_provider_costs_one_dictionary_entry(self) -> None:
        self.assertEqual(set(PROVIDER_RUNNERS), {"claude"})
        with self.assertRaises(CairnError) as absent:
            run_provider("echo", "hello", Path.cwd(), "auto", None, None, [])
        self.assertEqual(absent.exception.cause, "provider_unavailable")

        # One entry in the dictionary is the whole cost: the same step, unchanged,
        # now runs end to end through the CLI's own agent path.
        with tempfile.TemporaryDirectory() as temporary:
            env = runtime_env(Path(temporary))
            with (
                engine_step(Path(temporary), env),
                patch.dict(PROVIDER_RUNNERS, {"echo": run_echo}),
            ):
                self.assertEqual(
                    main(
                        [
                            "agent",
                            "run",
                            "--provider",
                            "echo",
                            "--prompt",
                            "hello",
                            "--tool",
                            "Bash(rm:*)",
                        ]
                    ),
                    0,
                )
            report = json.loads(
                report_file(env).read_text()
            )
            self.assertEqual(report["summary"], compose_prompt("hello"))
            self.assertEqual(report["status"], "done")
            self.assertEqual(report["detail"]["deny_patterns"], ["Bash(rm:*)"])
            self.assertEqual(
                report["detail"]["working_directory_seen"],
                str(Path(temporary).resolve()),
            )


class ASessionThatEndedWithoutReportingIsResumedOnce(unittest.TestCase):
    """A session that ended a turn without reporting is not a session that failed.

    Measured: the alternative was discarding $10.89 of work an assertion had just proved.
    """

    def _scripted(
        self, *replies: dict[str, Any]
    ) -> tuple[Callable[..., FakeProcess], list[FakeProcess]]:
        made: list[FakeProcess] = []
        pending = list(replies)

        def factory(command: list[str], **kwargs: object) -> FakeProcess:
            process = FakeProcess(command, **kwargs)
            if pending:
                said = {**process.output(), **pending.pop(0)}
                process.stdout = FakeOutput(json.dumps(said) + "\n")
            made.append(process)
            return process

        return factory, made

    def _ran(self, factory: Callable[..., FakeProcess], budget: float | None = 5.0):
        stream = io.StringIO()
        with redirect_stdout(stream):
            return run_claude(
                "do work", Path("/tmp"), "auto", "sonnet", budget, [], factory
            )

    UNREPORTED: ClassVar[dict[str, Any]] = {
        "stop_reason": "tool_use",
        "structured_output": None,
    }

    def test_a_correct_report_also_stops_for_a_tool_call_and_is_never_resumed(self) -> None:
        """The trap the discrimination exists for. A structured report *is* a tool call, so
        `stop_reason` alone says nothing — a rescue keyed on it would resume every step."""
        self.assertFalse(
            ended_without_reporting(
                0, {"stop_reason": "tool_use", "structured_output": {"status": "done"}}
            )
        )
        factory, made = self._scripted({"stop_reason": "tool_use"})
        result = self._ran(factory)
        self.assertEqual(len(made), 1)
        self.assertEqual(result.status, "done")
        self.assertNotIn("resumed_for_report", result.detail)

    def test_a_session_that_reported_nothing_is_resumed_for_its_report(self) -> None:
        factory, made = self._scripted(self.UNREPORTED)
        result = self._ran(factory)
        self.assertEqual(len(made), 2, "the session was not resumed exactly once")
        self.assertEqual(result.status, "done")
        self.assertEqual(result.detail["resumed_for_report"], RESUME_ATTEMPTED)

    def test_the_resume_continues_the_session_rather_than_opening_another(self) -> None:
        factory, made = self._scripted(self.UNREPORTED)
        self._ran(factory)
        opened, resumed = made[0].command, made[1].command
        self.assertIn("--session-id", opened)
        self.assertNotIn("--resume", opened)
        self.assertIn("--resume", resumed)
        self.assertNotIn("--session-id", resumed)
        self.assertEqual(
            resumed[resumed.index("--resume") + 1],
            opened[opened.index("--session-id") + 1],
        )

    def test_the_resume_asks_for_the_report_and_not_for_more_work(self) -> None:
        """The load-bearing half of the rescue: the step's assertion has already run or is
        about to, so a resumed session that started editing again would be doing unpriced
        work outside the shape the offer stated."""
        factory, made = self._scripted(self.UNREPORTED)
        self._ran(factory)
        self.assertEqual(made[0].prompt.rstrip().endswith("do work"), True)
        self.assertEqual(made[1].prompt, RESUME_FOR_REPORT)
        self.assertNotIn("do work", made[1].prompt)

    def test_every_half_of_the_discrimination_is_load_bearing(self) -> None:
        """Measured: a correct report is itself a tool call, so `stop_reason` decides
        nothing alone. Each of the three facts is asserted, or a variant dropping one of
        them would resume a session that failed and charge for it twice."""
        silent = {"stop_reason": "tool_use", "structured_output": None}
        self.assertTrue(ended_without_reporting(0, silent))
        # A session that failed for its own typed reason is not a silence.
        self.assertFalse(ended_without_reporting(1, silent))
        # A turn that genuinely ended is not a silence either.
        self.assertFalse(
            ended_without_reporting(0, {**silent, "stop_reason": "end_turn"})
        )
        # And the trap: a correct report stops for a tool call too.
        self.assertFalse(
            ended_without_reporting(
                0, {"stop_reason": "tool_use", "structured_output": {"status": "done"}}
            )
        )

    def test_a_resume_that_fails_for_its_own_reason_stays_the_silence_it_was(self) -> None:
        """A budget exhausted a cent short must not overwrite the honest cause: the gate
        would then read `reported_failure` and print the divergence line D exists to
        remove — about a step that reported nothing."""
        # The resume runs out of budget and so reports nothing either.
        factory, made = self._scripted(
            self.UNREPORTED,
            {**self.UNREPORTED, "terminal_reason": "budget_exhausted"},
        )
        with self.assertRaises(CairnError) as caught:
            self._ran(factory)
        self.assertEqual(len(made), 2)
        self.assertEqual(caught.exception.cause, "provider_protocol")
        self.assertEqual(
            caught.exception.detail["resumed_for_report"], RESUME_STILL_SILENT
        )
        # And the step's spend is still both passes, not just the first.
        self.assertAlmostEqual(caught.exception.detail["total_cost_usd"], 0.50)

    def test_the_gate_is_told_the_narrow_fact_and_not_only_the_broad_cause(self) -> None:
        """`provider_protocol` covers every unreadable-protocol fault; only one of them is
        a session that said nothing. The gate's sentence turns on this key."""
        factory, _ = self._scripted(self.UNREPORTED, self.UNREPORTED)
        with self.assertRaises(CairnError) as caught:
            self._ran(factory)
        self.assertIs(caught.exception.detail[ENDED_WITHOUT_REPORTING], True)

    def test_the_resume_runs_under_what_is_left_of_the_steps_ceiling(self) -> None:
        """The offer priced one ceiling for this step; a second pass carrying a fresh full
        budget would double the ceiling the person agreed to."""
        factory, made = self._scripted(self.UNREPORTED)
        self._ran(factory, budget=5.0)
        resumed = made[1].command
        self.assertEqual(float(resumed[resumed.index("--max-budget-usd") + 1]), 4.75)

    def test_a_step_with_nothing_left_to_spend_is_not_resumed(self) -> None:
        factory, made = self._scripted({**self.UNREPORTED, "total_cost_usd": 5.0})
        with self.assertRaises(CairnError) as caught:
            self._ran(factory, budget=5.0)
        self.assertEqual(len(made), 1)
        self.assertEqual(caught.exception.cause, "provider_protocol")
        self.assertEqual(
            caught.exception.detail["resumed_for_report"], RESUME_DECLINED_BUDGET
        )

    def test_the_recorded_cost_and_turns_are_both_passes(self) -> None:
        """A record naming only the second pass would under-report what the step spent."""
        factory, _ = self._scripted(self.UNREPORTED)
        result = self._ran(factory)
        self.assertAlmostEqual(result.detail["total_cost_usd"], 0.50)
        self.assertEqual(result.detail["turn_count"], 4)
        self.assertAlmostEqual(result.detail["abandoned_cost_usd"], 0.25)

    def test_a_resume_that_also_reports_nothing_is_the_failure_it_was(self) -> None:
        """One resume, never a loop — and the outcome is never worse for having tried."""
        factory, made = self._scripted(self.UNREPORTED, self.UNREPORTED)
        with self.assertRaises(CairnError) as caught:
            self._ran(factory)
        self.assertEqual(len(made), 2)
        self.assertEqual(caught.exception.cause, "provider_protocol")

    def test_the_resume_carries_the_same_bounds_as_the_session_it_continues(self) -> None:
        factory, made = self._scripted(self.UNREPORTED)
        self._ran(factory)
        opened, resumed = made[0].command, made[1].command
        for flag in ("--model", "--json-schema", "--permission-mode", "--settings"):
            with self.subTest(flag=flag):
                self.assertEqual(
                    resumed[resumed.index(flag) + 1], opened[opened.index(flag) + 1]
                )
        self.assertEqual(resumed.count("--disallowedTools"), opened.count("--disallowedTools"))


class EmitterContract(unittest.TestCase):
    def test_table_handles_mixed_plan_kinds(self) -> None:
        self.assertEqual(set(KIND_EMITTERS), {"command", "agent.*"})
        fixture = (
            Path(__file__).parents[1]
            / "fixtures/plans/mixed-kinds/graph.json"
        )
        plan = normalise(json.loads(fixture.read_text()))
        emitted = [emit_step(item, "/repo") for item in plan["steps"]]
        self.assertIn("cairn agent run", emitted[0]["run"])
        self.assertIn("cairn exec", emitted[1]["run"])
        self.assertIn("cairn wait", emitted[2]["run"])
        self.assertNotIn("cairn exec", emitted[2]["run"])
        self.assertIn("bin/reindex --full", shlex.split(emitted[1]["run"]))
        self.assertIn("bin/index-status --quiet", shlex.split(emitted[2]["run"]))
        self.assertIn("Bash(rm:*)", shlex.split(emitted[0]["run"]))
        for item in emitted:
            self.assertEqual(item["working_dir"], "/repo")
            self.assertIn("timeout_sec", item)
            self.assertIn("retry_policy", item)

    def test_an_agent_body_writes_the_plans_own_ceiling_and_model(self) -> None:
        """The definition is what an offer prices, so the bounds are in the body rather
        than resolved from the environment at run time."""
        fixture = Path(__file__).parents[1] / "fixtures/plans/mixed-kinds/graph.json"
        plan = normalise(json.loads(fixture.read_text()))
        tokens = shlex.split(emit_step(plan["steps"][0], "/repo")["run"])
        self.assertEqual(tokens[tokens.index("--model") + 1], "opus")
        self.assertEqual(tokens[tokens.index("--max-budget-usd") + 1], "8.0")

        defaulted = shlex.split(emit_step(step("agent.claude"), "/repo")["run"])
        self.assertEqual(defaulted[defaulted.index("--model") + 1], "sonnet")
        self.assertEqual(defaulted[defaulted.index("--max-budget-usd") + 1], "5.0")

    def test_an_agent_step_without_its_bounds_is_refused_at_emission(self) -> None:
        for bound, value in (("max_budget_usd", None), ("max_budget_usd", 0.0), ("model", None)):
            with self.subTest(bound=bound, value=value):
                unbounded = dict(step("agent.claude"))
                unbounded[bound] = value
                with self.assertRaisesRegex(ValueError, "could not be priced"):
                    emit_step(cast(Any, unbounded), "/repo")

    def test_wait_step_outlives_the_bound_it_reports_on(self) -> None:
        fixture = Path(__file__).parents[1] / "fixtures/plans/mixed-kinds/graph.json"
        plan = normalise(json.loads(fixture.read_text()))
        waiting = next(
            item for item in plan["steps"] if item.get("command_type") == "wait_until"
        )
        emitted = emit_step(waiting, "/repo")
        tokens = shlex.split(emitted["run"])
        self.assertEqual(
            float(tokens[tokens.index("--timeout") + 1]), waiting["timeout"]
        )
        self.assertGreater(emitted["timeout_sec"], waiting["timeout"])

    def test_every_run_is_one_safely_quoted_invocation(self) -> None:
        hostile = "printf '%s\\n' 'a b'; touch /tmp/not-executed && rm -rf /nope"
        for kind, command_type in self.emitted_shapes():
            with self.subTest(kind=kind, command_type=command_type):
                body = emit_step(step(kind, hostile, command_type), "/repo")["run"]
                tokens = shlex.split(body)
                self.assertEqual(tokens[:3], ["python3", "-m", "cairn"])
                self.assertIn(hostile, tokens)
                self.assertEqual(body, shlex.join(tokens))

    SHAPES: ClassVar[dict[str, list[tuple[str, str | None]]]] = {
        "command": [("command", "exec"), ("command", "wait_until")],
        "agent.*": [("agent.claude", None)],
    }

    def emitted_shapes(self) -> list[tuple[str, str | None]]:
        self.assertEqual(
            set(KIND_EMITTERS),
            set(self.SHAPES),
            "a new emitter needs its emitted shapes here before it can ship",
        )
        return [shape for key in KIND_EMITTERS for shape in self.SHAPES[key]]

    def test_a_body_that_is_not_one_invocation_is_refused_at_emission(self) -> None:
        def sprawling(step_record: Any, working_directory: str) -> Any:
            del working_directory
            return {"name": step_record["id"], "run": "set -e\ntrue && echo done"}

        with (
            patch.dict(KIND_EMITTERS, {"command": sprawling}),
            self.assertRaisesRegex(ValueError, "one quoted invocation"),
        ):
            emit_step(step("command"), "/repo")

    def test_verify_stays_bare(self) -> None:
        verify = emit_verify(step("command"), "/repo")
        self.assertEqual(verify["run"], "test -f result")

    def test_provider_identifier_is_quarantined(self) -> None:
        package = Path(__file__).parents[1] / "cairn"
        allowed = {package / "providers.py", package / "plan/schema.py"}
        needle = "clau" + "de"
        offenders = [
            path.relative_to(package).as_posix()
            for path in package.rglob("*.py")
            if path not in allowed and needle in path.read_text().casefold()
        ]
        self.assertEqual(offenders, [])

    def test_the_provider_name_is_never_spelled_by_the_caller(self) -> None:
        """A split literal evades a text grep; the dispatch itself cannot."""
        source = (Path(__file__).parents[1] / "cairn/__main__.py").read_text()
        self.assertIn("args.provider", source)
        seen: list[str] = []

        def record(provider: str, *_rest: Any) -> CommandResult:
            seen.append(provider)
            return CommandResult(0, "done", "", [], False, None, {})

        with (
            tempfile.TemporaryDirectory() as temporary,
            engine_step(Path(temporary)),
            patch("cairn.__main__.run_provider", record),
        ):
            main(["agent", "run", "--provider", "someone_else", "--prompt", "x"])
        self.assertEqual(seen, ["someone_else"])


if __name__ == "__main__":
    unittest.main()
