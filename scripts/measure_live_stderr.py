"""Measure whether the engine's view shows a node's standard error while the step runs.

    python3 -m scripts.measure_live_stderr        # print the readings

This is [20]'s gate. The breadcrumb Cairn writes beside the transcript — one legible line
per turn of an agent session — is worth building only if a person watching the node sees
those lines as they happen. Whether the view does that is a property of the pinned engine,
not something the design may assume, so it is measured here and the result is written into
`planning/20-watching-a-live-session.md`.

A step writes one prefixed line to standard error every couple of seconds for half a minute.
While it is still running, three things are read: the node's `.err` file on disk, the
engine's own node record, and the step-log endpoint the view's log panel calls with
`stream=stderr`. Lines that are on disk and served by that endpoint before the step finishes
are lines the view has while the step runs.

Everything happens in a scratch engine home with authentication off, and the server is
stopped before this returns: it is a measurement, not a server anybody was told about.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cairn.layout import VIEW_HEALTH_PATH
from cairn.transcript import BREADCRUMB_PREFIX
from cairn.workflow.gate import assert_pinned
from cairn.workflow.schema import ENGINE_VERSION

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

RUN_ID = "cairn-live-stderr"
DAG_NAME = "chatty"
# Long enough that the readings below are unarguably taken mid-run, short enough that the
# measurement costs half a minute.
LINES = 15
EVERY_SECONDS = 2
# Taken after enough lines that a buffered channel would still be holding them.
READ_AFTER_SECONDS = 10

DAG = f"""steps:
  - name: {DAG_NAME}
    command: |
      for i in $(seq 1 {LINES}); do
        echo "event $i"
        echo "{BREADCRUMB_PREFIX} turn $i: Edit cairn/report/compose.py" 1>&2
        sleep {EVERY_SECONDS}
      done
"""

CONFIG = """auth:
  mode: none
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def asked(url: str) -> Any:
    """One answer from the view's own API, or the failure that came back instead."""
    try:
        with urllib.request.urlopen(url, timeout=10) as answer:
            return json.loads(answer.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {"error": str(exc)}


def main() -> int:
    assert_pinned()
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="cairn-live-stderr-") as scratch:
        home = Path(scratch) / "home"
        (home / "dags").mkdir(parents=True)
        definition = home / "dags" / f"{DAG_NAME}.yaml"
        definition.write_text(DAG, encoding="utf-8")
        config = Path(scratch) / "config.yaml"
        config.write_text(CONFIG, encoding="utf-8")
        environment = {**os.environ, "DAGU_HOME": str(home)}
        step = subprocess.Popen(
            ("dagu", "start", "--dagu-home", str(home), "--run-id", RUN_ID, str(definition)),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        server = subprocess.Popen(
            (
                "dagu", "server",
                "--config", str(config),
                "--dagu-home", str(home),
                "--host", "127.0.0.1",
                "--port", str(port),
            ),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            while asked(f"{base}{VIEW_HEALTH_PATH}").get("status") != "healthy":
                time.sleep(0.2)
            time.sleep(READ_AFTER_SECONDS)
            run = asked(f"{base}/api/v1/dag-runs/{DAG_NAME}/{RUN_ID}")
            node = run.get("dagRunDetails", {}).get("nodes", [{}])[0]
            served = asked(
                f"{base}/api/v1/dag-runs/{DAG_NAME}/{RUN_ID}"
                f"/steps/{DAG_NAME}/log?stream=stderr&tail=100"
            )
            on_disk = Path(node.get("stderr", "/nowhere"))
            reading = {
                "measured_against": ENGINE_VERSION,
                "seconds_into_a_step_of": [READ_AFTER_SECONDS, LINES * EVERY_SECONDS],
                "node_status": node.get("statusLabel"),
                "step_finished": bool(node.get("finishedAt")),
                "err_file_lines": (
                    on_disk.read_text(encoding="utf-8", errors="replace").count("\n")
                    if on_disk.exists()
                    else None
                ),
                "api_stderr_lines": served.get("lineCount"),
                "api_stderr_content": served.get("content"),
            }
        finally:
            server.terminate()
            server.wait(timeout=30)
            step.terminate()
            step.wait(timeout=30)
    print(json.dumps(reading, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
