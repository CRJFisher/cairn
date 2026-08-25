"""What the installed skill costs to have, as the block the README publishes.

    python3 -m scripts.measure_surface            # print the block
    python3 -m scripts.measure_surface --check    # exit nonzero if the README disagrees

The measurement itself is `cairn/skill/surface.py`'s, and the suite calls the same function,
so there is no path where the script and the test could compute different numbers. What this
adds is the paste-ready block and the refusal — the same posture as
`scripts/regenerate_workflows.py`, for the same reason: a published claim that nobody is
forced to look at goes stale silently.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cairn.skill.surface import PUBLISHED_HEADING, measure, published

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
README = PACKAGE_ROOT / "README.md"

EXIT_STALE = 1


def block() -> str:
    return published(measure(PACKAGE_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="refuse when README.md does not carry the measured block",
    )
    args = parser.parse_args(argv)
    composed = block()
    if not args.check:
        print(composed)
        return 0
    if composed in README.read_text(encoding="utf-8"):
        print(f"{README.name} carries the measured surface cost")
        return 0
    print(
        f"{README.name} does not carry the measured surface cost. The block below is what "
        f"it should hold, under the heading {PUBLISHED_HEADING!r}:\n\n{composed}",
        file=sys.stderr,
    )
    return EXIT_STALE


if __name__ == "__main__":
    raise SystemExit(main())
