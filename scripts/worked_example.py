"""The worked example: a run the engine called a clean success, read by Cairn.

    python3 -m scripts.worked_example              # the terminal report
    python3 -m scripts.worked_example --sink markdown

This is the example because it is the failing one. A demo where everything works
demonstrates nothing that distinguishes Cairn: the engine would have said so too. Here the
engine reports run status `Succeeded` and exits `0` over a step whose work never landed —
every node either succeeded or skipped, and none failed — and Cairn reports the step
excluded, names the cause, and exits `3`. The difference between those two accounts is the
whole product.

It needs no engine, no agent session and no network: the run is
`fixtures/runs/green-with-exclusions`, recorded from a real Dagu 2.18.2 run
([docs/run-model.md]), and what you see is the ordinary `cairn report` surface reading it.
The recording is staged into a scratch directory because a run's reports are addressed by
run identity, and the corpus is addressed by shape.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from cairn.report.cli import main as report_main
from cairn.report.spine import SINK_TERMINAL, SINKS

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SHAPE = "green-with-exclusions"
RECORDING = PACKAGE_ROOT / "fixtures" / "runs" / SHAPE


def stage(root: Path) -> str:
    """Put the recording where a report reads a run from, and return the run's identity.

    The identity comes from the recording itself rather than from this file, so a
    re-recording that changes it does not leave the example reading a run that is not there.
    """
    recording: Any = json.loads(
        (RECORDING / "recording.json").read_text(encoding="utf-8")
    )
    run_id = str(cast(dict[str, Any], recording)["run_id"])
    shutil.copytree(RECORDING / "reports", root / run_id / "reports")
    return run_id


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.worked_example", description=__doc__)
    parser.add_argument("--sink", choices=sorted(SINKS), default=SINK_TERMINAL)
    parser.add_argument("--out", help="write the rendering to this path instead of stdout")
    args = parser.parse_args(arguments)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_id = stage(root)
        return report_main(
            [
                "--run",
                run_id,
                "--reports",
                str(root),
                "--engine-records",
                str(RECORDING),
                "--format",
                args.sink,
                *(["--out", args.out] if args.out else []),
            ]
        )


if __name__ == "__main__":
    sys.exit(main())
