"""Record what the three view states actually answer with, from a real engine server.

    python3 -m scripts.record_view            # rewrite fixtures/view/answers.json

The reading in `cairn/view.py` turns one HTTP answer into one of three states, and the whole
reason it reads the body rather than the status code is a measurement: the pinned server
answers `/api/v1/health` with JSON and answers `/health` with its web app's HTML under the
same `200`. A hand-written fixture would assert that belief instead of recording it, so this
script stands a real server up on a port of its own, asks it both questions, asks a port
nothing is on, and writes down what came back.

The server runs against a scratch engine home and is stopped before the script exits: it is
a measurement, not a server anybody was told about. Its own home is where it writes its
example DAGs and its unclaimed authentication, and none of that reaches the machine's.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from cairn.layout import VIEW_HEALTH_PATH
from cairn.view import HEALTH_TIMEOUT, SERVING_TIMEOUT, ask_health
from cairn.workflow.gate import assert_pinned
from cairn.workflow.schema import ENGINE_VERSION

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RECORDING = PACKAGE_ROOT / "fixtures" / "view" / "answers.json"

# The engine's own web app, under a path that looks like a health check and is not one. The
# second question is the whole point of the recording.
WEB_APP_PATH = "/health"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def answered(url: str) -> dict[str, Any]:
    """One recorded answer, read through the same asker `cairn.view.status` uses.

    Going through `ask_health` rather than a second `urlopen` means the recording and the
    reading it feeds can never part ways on how an answer is parsed.
    """
    answer = ask_health(url, HEALTH_TIMEOUT)
    return {
        "code": answer.code,
        "body": answer.body.decode("utf-8", "replace"),
        "said": answer.said,
    }


def main() -> int:
    assert_pinned()
    port = free_port()
    nowhere = free_port()
    with tempfile.TemporaryDirectory(prefix="cairn-view-recording-") as home:
        server = subprocess.Popen(
            ("dagu", "server", "--host", "127.0.0.1", "--port", str(port)),
            # The caller's environment with the home redirected, because the engine resolves
            # its configuration against `HOME` and a stripped environment leaves it none.
            env={**os.environ, "DAGU_HOME": home},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + SERVING_TIMEOUT
            health = f"http://127.0.0.1:{port}{VIEW_HEALTH_PATH}"
            while answered(health)["code"] != 200:
                if time.monotonic() >= deadline:
                    raise SystemExit(f"the recording server never answered {health}")
                time.sleep(0.2)
            recording = {
                "recorded_with": ENGINE_VERSION,
                "how": (
                    "dagu server --host 127.0.0.1 --port <free>, against a scratch "
                    "DAGU_HOME; python3 -m scripts.record_view re-records it"
                ),
                "serving": answered(health),
                "foreign": answered(f"http://127.0.0.1:{port}{WEB_APP_PATH}"),
                "absent": answered(f"http://127.0.0.1:{nowhere}{VIEW_HEALTH_PATH}"),
            }
        finally:
            server.terminate()
            server.wait(timeout=30)
    RECORDING.parent.mkdir(parents=True, exist_ok=True)
    RECORDING.write_text(json.dumps(recording, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {RECORDING.relative_to(PACKAGE_ROOT)} against {ENGINE_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
