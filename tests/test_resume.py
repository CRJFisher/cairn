"""44's acceptance: a held run resumes itself when its window reopens.

One class per criterion. A run that stops at the allowance arms one waiter; the waiter sleeps
past the moment the run recorded and then **measures**, never entering a window it has not
seen open; it refuses where a person is needed and says why on the run; the record and the
report show it while it waits; nothing starts a scheduler; and the owner is told twice.

The clock is a fake one throughout and nothing here launches a process. A resume is hours
long by design, and a test that slept would be measuring the machine rather than the
decision — the same posture [test_headroom.py] takes for the hold these waits continue.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from contextlib import chdir, redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from cairn.__main__ import main as cairn_main
from cairn.core import CairnError
from cairn.gitio import git, runs_root
from cairn.headroom import Gauge, Instruments, local_moment
from cairn.layout import (
    NOTIFY_COMMAND_ENV,
    RUNS_ROOT_ENV,
    headroom_directory,
    reports_directory,
    resume_log_path,
    resume_path,
)
from cairn.locks import acquire_run_lock, release_run_lock
from cairn.providers import iso_moment
from cairn.record.extract import extract, read_resume
from cairn.record.facts import as_mapping
from cairn.record.model import PendingResume, RunRecord
from cairn.record.store import read_record, write_record
from cairn.record.vocabulary import (
    ATTENTION_RESUME_REFUSED,
    NEXT_AWAIT_ALLOWANCE,
    RESUME_REFUSED,
    RESUME_STARTED,
    RESUME_STARTING,
    RESUME_SUPERSEDED,
    RESUME_WAITING,
)
from cairn.report.compose import document
from cairn.report.terminal import render
from cairn.resume import (
    RESUME_HORIZON,
    Armed,
    Waiter,
    arm,
    cancel,
    held_at,
    notify,
    notify_command,
    supersede,
    waiter_command,
    write_resume,
)
from cairn.skill.cli import run_main
from cairn.view import VIEW_SERVING
from cairn.workflow.schema import LABEL_BODY_DIGEST, LABEL_PLAN
from cairn.workflow.stamp import workflow_path, write_stamp
from tests.ownership import own_repository
from tests.test_run_record import load

NOW = 1_791_140_000.0
# The moment the held run recorded its window as reopening: four hours after the run ended,
# which is inside the horizon and far enough away that no test wanders over it by accident.
REOPENS_AT = NOW + 4 * 60 * 60
GRAPH = "g" * 64


class Clock:
    """Wall time that moves only when the code under test sleeps or a test says so."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Probe:
    """A probe answering whatever the test queued next, counted so re-measuring can be seen."""

    def __init__(self, answers: list[list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.calls = 0

    def __call__(self, timeout: float) -> list[dict[str, Any]]:
        del timeout
        self.calls += 1
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        return answer


def event(status: str, resets_at: float) -> dict[str, Any]:
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "rateLimitType": "seven_day",
            "resetsAt": int(resets_at),
        },
    }


class Launch:
    """A process factory that records what it was asked to start and starts nothing."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Any, **options: Any) -> Any:
        del options
        self.commands.append(tuple(cast("list[str]", command)))
        return _Child()


class _Child:
    pid = 4242
    returncode: int | None = None
    stdin = None
    stdout = None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def poll(self) -> int | None:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


class Ran:
    """A `subprocess.run` that records its invocations and answers with a chosen status."""

    def __init__(self, returncode: int = 0, said: bytes = b"") -> None:
        self.returncode = returncode
        self.said = said
        self.calls: list[tuple[tuple[str, ...], bytes]] = []

    def __call__(self, command: Any, **options: Any) -> Any:
        given: Any = options.get("input") or b""
        self.calls.append((tuple(cast("list[str]", command)), cast(bytes, given)))
        return subprocess.CompletedProcess(
            args=list(cast("list[str]", command)),
            returncode=self.returncode,
            stdout=b"",
            stderr=self.said,
        )


class Signals:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []

    def __call__(self, pid: int, number: int) -> None:
        self.sent.append((pid, number))


def _armed(*arguments: Any, **options: Any) -> Armed:
    """An arming that launches nothing, so the release's own line is what is under test."""
    del arguments, options
    return Armed(["resuming later"], 7)


def _released(runs_root: Path, run_id: str) -> str:
    """What the release left in its own report, which is what `cairn report` reads."""
    return (reports_directory(runs_root, run_id) / "onExit.json").read_text(encoding="utf-8")


def recovery_command(plan: str, repository: Path, run_id: str) -> str:
    return shlex.join(
        [
            "python3",
            "-m",
            "cairn",
            "run",
            "start",
            "--plan",
            plan,
            "--repository",
            str(repository),
            "--trigger",
            "recovery",
            "--recovering",
            run_id,
        ]
    )


class Fixture(unittest.TestCase):
    """A repository a held run has just released, with that run's record on disk."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = self.root / "work"
        self.repository.mkdir()
        own_repository(self.repository, "setup-run")
        release_run_lock(self.repository, run_id="setup-run")
        self.runs_root = runs_root(self.repository)
        self.clock = Clock()
        self.record = self.held_record()
        self.run_id = self.record["run_id"]

    def held_record(self, *, at: float = REOPENS_AT, graph: str | None = GRAPH) -> RunRecord:
        """The record a run that stopped at the allowance leaves, retargeted at this fixture.

        Built through the extraction from the corpus's own held run rather than written by
        hand, so what the waiter reads is a record the derivation really produces — the next
        action, the subject and the recovery command included.
        """
        state, reports, run_id = load("held-after-limit")
        record = extract(state, reports, run_id=run_id)
        self.assertEqual(record["next_action"]["action"], NEXT_AWAIT_ALLOWANCE)
        fields = cast(dict[str, Any], record)
        fields["graph_sha256"] = graph
        record["provenance"].pop("graph_sha256", None)
        if graph is None:
            record["provenance"]["graph_sha256"] = "absent"
        record["git"]["repository"] = str(self.repository)
        plan = cast(str, record["plan"])
        record["next_action"]["command"] = recovery_command(plan, self.repository, run_id)
        subject = cast(str, record["next_action"]["subject"])
        for step in record["steps"]:
            headroom = step["headroom"]
            if step["step_id"] == subject and headroom is not None:
                headroom["held_until"] = iso_moment(at)
        write_record(self.runs_root, record)
        self.publish(plan, graph)
        return record

    def publish(self, plan: str, graph: str | None) -> None:
        """A workflow stamped with the graph this run ran, which is what a recovery needs."""
        if graph is None:
            return
        workflow = workflow_path(self.repository, plan)
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("steps: []\n", encoding="utf-8")
        write_stamp(
            workflow,
            cast(
                Any,
                {"labels": {LABEL_PLAN: plan, LABEL_BODY_DIGEST: "b" * 64}},
            ),
            graph,
            source_root=self.root,
        )

    def waiter(
        self,
        *,
        answers: list[list[dict[str, Any]]] | None = None,
        runner: Ran | None = None,
        environment: dict[str, str] | None = None,
        mint: Callable[[], str] | None = None,
    ) -> tuple[Waiter, Probe, Ran]:
        probe = Probe(answers or [[event("allowed", REOPENS_AT + 7 * 24 * 3600)]])
        ran = runner or Ran()
        waiting = Waiter(
            self.run_id,
            repository=self.repository,
            runs_root=self.runs_root,
            clock=self.clock.time,
            sleep=self.clock.sleep,
            instruments=Instruments(probe=probe, endpoint=None),
            gauge=Gauge(
                headroom_directory(self.runs_root),
                clock=self.clock.time,
                account_reader=lambda: None,
            ),
            runner=ran,
            environment=environment or {},
            mint=mint or (lambda: "resumed-run"),
        )
        return waiting, probe, ran

    def stored(self) -> PendingResume | None:
        return read_resume(resume_path(self.runs_root, self.run_id), self.run_id)

    def rebuilt(self) -> RunRecord:
        """The record as a reader asking for it now would get it, resume and all."""
        state, reports, run_id = load("held-after-limit")
        return extract(state, reports, run_id=run_id, resume=self.stored())


class TheRunStartsItsOwnRecovery(Fixture):
    """A run that ends `quota_held` with a known moment starts its own recovery after the
    window measures open, with no person involved."""

    def test_the_release_arms_one_waiter_for_the_run_it_just_released(self) -> None:
        launch = Launch()
        armed = arm(self.runs_root, self.run_id, popen_factory=launch, notifier=Ran())
        self.assertEqual(
            launch.commands,
            [waiter_command(self.run_id, str(self.repository))],
        )
        self.assertEqual(armed.pid, _Child.pid)
        self.assertIn(local_moment(REOPENS_AT), armed.said[0])

    def test_a_run_that_did_not_stop_at_the_allowance_arms_nothing(self) -> None:
        state, reports, run_id = load("green")
        write_record(self.runs_root, extract(state, reports, run_id=run_id))
        launch = Launch()
        self.assertEqual(arm(self.runs_root, run_id, popen_factory=launch), ([], None))
        self.assertEqual(launch.commands, [])

    def test_it_waits_past_the_moment_the_run_recorded_before_it_measures(self) -> None:
        waiting, probe, _ = self.waiter()
        waiting.run()
        self.assertEqual(probe.calls, 1)
        self.assertGreaterEqual(self.clock.slept[0], REOPENS_AT - NOW)

    def test_it_runs_the_recovery_command_the_record_carries(self) -> None:
        waiting, _, ran = self.waiter()
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(pending["state"], RESUME_STARTED)
        self.assertEqual(pending["started_run"], "resumed-run")
        self.assertEqual(
            ran.calls[0][0],
            (
                *shlex.split(cast(str, self.record["next_action"]["command"])),
                "--run-id",
                "resumed-run",
            ),
        )

    def test_the_recovery_continues_the_run_it_names_so_landed_work_no_ops(self) -> None:
        """The skipping is the recovery's own, and this is what hands it the occasion."""
        waiting, _, ran = self.waiter()
        waiting.run()
        self.assertIn("--recovering", ran.calls[0][0])
        self.assertIn(self.run_id, ran.calls[0][0])
        self.assertIn("--trigger", ran.calls[0][0])
        self.assertIn("recovery", ran.calls[0][0])

    def test_a_recovery_that_would_not_start_is_recorded_rather_than_retried(self) -> None:
        waiting, _, ran = self.waiter(runner=Ran(returncode=1, said=b"refused  no engine"))
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(pending["state"], RESUME_REFUSED)
        self.assertIn("no engine", cast(str, pending["reason"]))
        self.assertEqual(len(ran.calls), 1)


class AClosedWindowIsMeasuredAgainAndNeverEntered(Fixture):
    """A window still closed at its reported moment is backed off and measured again."""

    def test_a_window_still_shut_at_its_moment_is_waited_on_rather_than_entered(self) -> None:
        later = REOPENS_AT + 90 * 60
        waiting, probe, ran = self.waiter(
            answers=[
                [event("rejected", later)],
                [event("allowed", later + 7 * 24 * 3600)],
            ]
        )
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(probe.calls, 2)
        self.assertEqual(pending["state"], RESUME_STARTED)
        self.assertEqual(len(ran.calls), 1)
        self.assertGreaterEqual(self.clock.now, later)

    def test_while_it_backs_off_the_run_says_the_window_measured_closed(self) -> None:
        later = REOPENS_AT + 90 * 60
        seen: list[str] = []
        waiting, _, _ = self.waiter(
            answers=[[event("rejected", later)], [event("allowed", later + 86_400)]]
        )
        original = waiting.sleep

        def watching(seconds: float) -> None:
            stored = self.stored()
            if stored is not None and stored["reason"]:
                seen.append(stored["reason"])
            original(seconds)

        waiting.sleep = watching
        waiting.run()
        self.assertTrue(any("measured closed" in said for said in seen), seen)

    def test_a_window_that_never_reopens_inside_the_horizon_stops_rather_than_loops(
        self,
    ) -> None:
        waiting, probe, ran = self.waiter(
            answers=[[event("rejected", REOPENS_AT + RESUME_HORIZON + 86_400)]]
        )
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(pending["state"], RESUME_REFUSED)
        self.assertEqual(ran.calls, [])
        self.assertEqual(probe.calls, 1)


class EveryRefusalSaysWhyAndStartsNothing(Fixture):
    """Each of B's four refusals leaves the run's report saying why, and starts nothing."""

    def refused(self, **kwargs: Any) -> PendingResume:
        waiting, _, ran = self.waiter(**kwargs)
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(pending["state"], RESUME_REFUSED)
        self.assertEqual(ran.calls, [])
        return pending

    def test_a_dirty_tree_is_a_refusal_naming_what_a_person_must_settle(self) -> None:
        (self.repository / "half-done.txt").write_text("partial\n", encoding="utf-8")
        pending = self.refused()
        self.assertIn("clean tree", cast(str, pending["reason"]))

    def test_a_repository_another_run_holds_is_a_refusal_naming_the_holder(self) -> None:
        acquire_run_lock(
            self.repository, run_id="other-run", plan="other", run_timeout_seconds=3600
        )
        pending = self.refused()
        self.assertIn("other-run", cast(str, pending["reason"]))

    def test_a_workflow_re_authored_since_the_run_is_a_refusal(self) -> None:
        self.publish(cast(str, self.record["plan"]), "h" * 64)
        pending = self.refused()
        self.assertIn("re-authored", cast(str, pending["reason"]))

    def test_a_moment_beyond_the_horizon_refuses_without_waiting_at_all(self) -> None:
        self.held_record(at=NOW + RESUME_HORIZON + 86_400)
        waiting, probe, ran = self.waiter()
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(pending["state"], RESUME_REFUSED)
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(probe.calls, 0)
        self.assertEqual(ran.calls, [])

    def test_a_refusal_is_one_attention_item_on_the_run(self) -> None:
        (self.repository / "half-done.txt").write_text("partial\n", encoding="utf-8")
        self.refused()
        items = [
            item
            for item in self.rebuilt()["attention"]
            if item["kind"] == ATTENTION_RESUME_REFUSED
        ]
        self.assertEqual(len(items), 1)
        self.assertIn("clean tree", items[0]["summary"])


class TheViewAndTheReportShowAPendingResume(Fixture):
    """While it waits, the run reads as resuming at a moment rather than failed and idle."""

    def waiting_resume(self) -> PendingResume:
        held = held_at(self.record)
        assert held is not None
        pending = PendingResume(
            run_id=self.run_id,
            state=RESUME_WAITING,
            at=held.moment,
            window=held.window,
            account=held.account,
            command=held.command,
            pid=4242,
            reason=None,
            started_run=None,
            written_at=iso_moment(NOW),
        )
        write_resume(self.runs_root, pending)
        return pending

    def test_the_record_carries_the_resume_with_its_moment(self) -> None:
        self.waiting_resume()
        record = self.rebuilt()
        resume = record["resume"]
        assert resume is not None
        self.assertEqual(resume["state"], RESUME_WAITING)
        self.assertEqual(resume["at"], iso_moment(REOPENS_AT))

    def test_the_projection_states_the_resume_and_its_moment(self) -> None:
        self.waiting_resume()
        facts = as_mapping(self.rebuilt())
        self.assertEqual(facts["run.resume"], RESUME_WAITING)
        self.assertEqual(facts["run.resume_at"], iso_moment(REOPENS_AT))

    def test_the_report_says_the_run_is_resuming_rather_than_idle(self) -> None:
        self.waiting_resume()
        record = self.rebuilt()
        shown = render(document(record, VIEW_SERVING), as_mapping(record)).text
        self.assertIn("armed one resume", shown)
        self.assertIn(iso_moment(REOPENS_AT), shown)

    def test_a_run_with_no_resume_carries_the_keys_all_the_same(self) -> None:
        """A run-level key that appeared only for the runs that have one would leave every
        other run's projection a different shape."""
        facts = as_mapping(self.rebuilt())
        for key in ("run.resume", "run.resume_at", "run.resume_window", "run.resume_run"):
            self.assertEqual(facts[key], "absent", key)

    def test_the_release_says_it_where_the_engines_view_shows_it(self) -> None:
        """A release handler's own output is the only text of Cairn's that the engine's
        view shows for a run that has finished, so the line is printed as well as recorded.

        The release identifies itself from the engine's environment and the process's own
        directory, so both are stood up around it — a suite that ran this against whatever
        it inherited would be releasing somebody else's repository.
        """
        environment = {
            "DAG_RUN_ID": self.run_id,
            "DAG_RUN_STEP_NAME": "onExit",
            "DAG_RUN_WORK_DIR": str(self.repository),
            RUNS_ROOT_ENV: str(self.runs_root),
        }
        shown = io.StringIO()
        with (
            patch.dict(os.environ, environment),
            chdir(self.repository),
            patch("cairn.__main__.arm", _armed),
            redirect_stdout(shown),
        ):
            self.assertEqual(os.environ["DAG_RUN_ID"], self.run_id)
            result = cairn_main(["lock", "release"])
        self.assertEqual(result, 0)
        self.assertIn("resuming later", shown.getvalue())
        self.assertIn("resuming later", _released(self.runs_root, self.run_id))

    def test_the_waiter_has_somewhere_of_its_own_to_say_what_it_did(self) -> None:
        armed = arm(self.runs_root, self.run_id, popen_factory=Launch(), notifier=Ran())
        self.assertTrue(any("resuming" in said for said in armed.said), armed.said)
        self.assertTrue(resume_log_path(self.runs_root, self.run_id).parent.exists())


class ItCanBeCalledOff(Fixture):
    """`--cancel` stops the waiter, and any run started against the repository supersedes
    one still waiting, which then exits without acting."""

    def pending(self, state: str = RESUME_WAITING, run_id: str | None = None) -> PendingResume:
        held = held_at(self.record)
        assert held is not None
        pending = PendingResume(
            run_id=run_id or self.run_id,
            state=state,
            at=held.moment,
            window=held.window,
            account=held.account,
            command=held.command,
            pid=9191,
            reason=None,
            started_run=None,
            written_at=iso_moment(NOW),
        )
        write_resume(self.runs_root, pending)
        return pending

    def test_cancelling_stops_the_waiter_and_removes_what_it_wrote(self) -> None:
        self.pending()
        signals = Signals()
        said = cancel(self.runs_root, self.run_id, signaller=signals)
        self.assertEqual([pid for pid, _ in signals.sent], [9191])
        self.assertFalse(resume_path(self.runs_root, self.run_id).exists())
        self.assertIn("called off", said)

    def test_cancelling_a_run_with_no_resume_is_an_answer_rather_than_a_fault(self) -> None:
        self.assertIn("no resume", cancel(self.runs_root, self.run_id))

    def test_starting_a_run_stands_down_a_resume_still_waiting(self) -> None:
        self.pending()
        signals = Signals()
        self.assertEqual(
            supersede(self.runs_root, started_by="fresh-run", signaller=signals),
            [self.run_id],
        )
        stored = self.stored()
        assert stored is not None
        self.assertEqual(stored["state"], RESUME_SUPERSEDED)
        self.assertIn("fresh-run", cast(str, stored["reason"]))
        self.assertEqual([pid for pid, _ in signals.sent], [9191])

    def test_the_resume_that_is_already_starting_its_own_recovery_is_left_alone(self) -> None:
        """The run that is starting is the resume's own, and standing it down would stop the
        very recovery it is launching."""
        self.pending(state=RESUME_STARTING)
        self.assertEqual(supersede(self.runs_root, started_by="fresh-run"), [])
        stored = self.stored()
        assert stored is not None
        self.assertEqual(stored["state"], RESUME_STARTING)

    def test_a_superseded_waiter_exits_without_acting(self) -> None:
        waiting, _, ran = self.waiter()
        original = waiting.sleep

        def intercepted(seconds: float) -> None:
            supersede(self.runs_root, started_by="fresh-run")
            original(seconds)

        waiting.sleep = intercepted
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(pending["state"], RESUME_SUPERSEDED)
        self.assertEqual(ran.calls, [])


class TheOwnerIsToldTwice(Fixture):
    """With `CAIRN_NOTIFY_COMMAND` set, the command is invoked once when the run stops and
    once when it resumes."""

    def test_the_run_stopping_is_one_invocation_with_the_sentence_on_stdin(self) -> None:
        told = Ran()
        arm(
            self.runs_root,
            self.run_id,
            popen_factory=Launch(),
            environment={NOTIFY_COMMAND_ENV: "cat >/dev/null"},
            notifier=told,
        )
        self.assertEqual(len(told.calls), 1)
        self.assertEqual(told.calls[0][0], ("/bin/sh", "-c", "cat >/dev/null"))
        self.assertIn(b"stopped", told.calls[0][1])

    def test_the_run_resuming_is_the_other_invocation(self) -> None:
        told = Ran()
        waiting, _, _ = self.waiter(
            runner=told, environment={NOTIFY_COMMAND_ENV: "cat >/dev/null"}
        )
        waiting.run()
        notifications = [
            said for command, said in told.calls if command[0] == "/bin/sh"
        ]
        self.assertEqual(len(notifications), 1)
        self.assertIn(b"resumed", notifications[0])

    def test_an_unset_command_is_told_nothing_and_never_required(self) -> None:
        told = Ran()
        self.assertFalse(notify("anything at all", environment={}, runner=told))
        self.assertEqual(told.calls, [])

    def test_a_notify_command_that_fails_never_reaches_the_resume(self) -> None:
        def broken(command: Any, **options: Any) -> Any:
            del command, options
            raise OSError("no such shell")

        self.assertFalse(
            notify("x", environment={NOTIFY_COMMAND_ENV: "nope"}, runner=broken)
        )

    def test_the_command_is_read_where_a_workflow_is_generated(self) -> None:
        """The engine hands a step a curated environment, so a value the owner set in their
        own shell reaches the release only through the generated file."""
        self.assertIsNone(notify_command({}))
        self.assertEqual(notify_command({NOTIFY_COMMAND_ENV: " say it "}), "say it")


class NoSchedulerIsStartedOrRequired(Fixture):
    """The whole of A's constraint: a scheduler's retry scanner re-executes runs Cairn never
    wrote, and this must not need one running."""

    def test_nothing_the_waiter_runs_names_the_engine(self) -> None:
        waiting, _, ran = self.waiter()
        waiting.run()
        for command, _ in ran.calls:
            self.assertNotIn("dagu", " ".join(command))
            self.assertNotIn("schedule", " ".join(command))

    def test_the_arming_command_is_cairns_own_and_takes_no_parameters(self) -> None:
        command = waiter_command(self.run_id, str(self.repository))
        self.assertEqual(command[:5], ("python3", "-m", "cairn", "run", "resume-at"))
        self.assertNotIn("--params", command)

    def test_the_module_reaches_for_no_scheduler(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "cairn" / "resume.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("cairn.schedule", source)
        self.assertNotIn("ENGINE_BINARY", source)


class TheCommandLineIsTheWaitersOwnSurface(Fixture):
    """`cairn run resume-at` is the one way in, and `--cancel` the one way out."""

    def invoke(self, *arguments: str) -> tuple[int, str]:
        shown = io.StringIO()
        with redirect_stdout(shown):
            code = run_main(list(arguments))
        return code, shown.getvalue()

    def test_cancelling_through_the_command_removes_the_pending_resume(self) -> None:
        held = held_at(self.record)
        assert held is not None
        write_resume(
            self.runs_root,
            PendingResume(
                run_id=self.run_id,
                state=RESUME_WAITING,
                at=held.moment,
                window=held.window,
                account=held.account,
                command=held.command,
                pid=os.getpid(),
                reason=None,
                started_run=None,
                written_at=iso_moment(NOW),
            ),
        )
        code, said = self.invoke(
            "resume-at", "--run", self.run_id, "--repository", str(self.repository)
        , "--cancel")
        self.assertEqual(code, 0)
        self.assertIn("called off", said)
        self.assertFalse(resume_path(self.runs_root, self.run_id).exists())

    def test_a_run_id_that_would_leave_the_runs_root_is_refused(self) -> None:
        code, _ = self.invoke(
            "resume-at", "--run", "../elsewhere", "--repository", str(self.repository)
        )
        self.assertEqual(code, 1)


class TheResumeFileIsReadAsStrictlyAsAReport(Fixture):
    """A record carrying half a resume would show a run as resuming on evidence nothing
    wrote."""

    def written(self, payload: dict[str, Any]) -> PendingResume | None:
        path = resume_path(self.runs_root, self.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return read_resume(path, self.run_id)

    def test_a_file_naming_another_run_is_another_runs(self) -> None:
        self.assertIsNone(
            self.written(
                {
                    "run_id": "somebody-else",
                    "state": RESUME_WAITING,
                    "command": "c",
                    "pid": 1,
                    "written_at": "now",
                }
            )
        )

    def test_a_state_this_cairn_does_not_know_is_no_resume_at_all(self) -> None:
        self.assertIsNone(
            self.written(
                {
                    "run_id": self.run_id,
                    "state": "whatever",
                    "command": "c",
                    "pid": 1,
                    "written_at": "now",
                }
            )
        )

    def test_a_file_that_is_not_json_is_read_as_no_resume(self) -> None:
        path = resume_path(self.runs_root, self.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
        self.assertIsNone(read_resume(path, self.run_id))

    def test_a_record_whose_resume_is_unreadable_is_a_record_with_none(self) -> None:
        self.written({"run_id": self.run_id, "state": "whatever"})
        self.assertIsNone(self.rebuilt()["resume"])


class TheWaiterReadsTheRunRatherThanDecidingForItself(Fixture):
    """It acts on the record the run left, and on nothing it worked out on its own."""

    def test_a_record_that_cannot_be_read_resumes_nothing(self) -> None:
        git(self.repository, ("status", "--porcelain"))
        (self.runs_root / self.run_id / "record.json").write_text("{", encoding="utf-8")
        with self.assertRaises(CairnError):
            read_record(self.runs_root, self.run_id)
        waiting, probe, ran = self.waiter()
        self.assertIsNone(waiting.run())
        self.assertEqual((probe.calls, ran.calls), (0, []))

    def test_a_run_recording_no_graph_digest_is_refused_rather_than_started(self) -> None:
        self.held_record(graph=None)
        waiting, _, ran = self.waiter()
        pending = waiting.run()
        assert pending is not None
        self.assertEqual(pending["state"], RESUME_REFUSED)
        self.assertIn("graph digest", cast(str, pending["reason"]))
        self.assertEqual(ran.calls, [])


if __name__ == "__main__":
    unittest.main()
