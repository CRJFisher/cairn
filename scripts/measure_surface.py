"""What the installed skill puts in context, as the block the README publishes.

    python3 -m scripts.measure_surface            # print the block
    python3 -m scripts.measure_surface --write    # rewrite the README's block in place

The measurement itself is `cairn/skill/surface.py`'s, and the suite calls the same function,
so there is no path where the script and the test could compute different numbers. What this
adds is the print and the rewrite: the README's figures are regenerated after any edit to
the files they measure, never maintained by hand.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from cairn.skill.surface import PUBLISHED_HEADING, measure, published

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
README = PACKAGE_ROOT / "README.md"

# The heading through the last line of the table that follows it.
BLOCK = re.compile(
    rf"^{re.escape(PUBLISHED_HEADING)}\n(?:(?!\|).*\n)*?(?:\|[^\n]*\n?)+", re.MULTILINE
)


def block() -> str:
    return published(measure(PACKAGE_ROOT))


def write() -> bool:
    """Replace the README's block with the measured one; True when the file changed."""
    text = README.read_text(encoding="utf-8")
    matches = BLOCK.findall(text)
    if len(matches) != 1:
        raise SystemExit(
            f"{README.name} must carry the heading {PUBLISHED_HEADING!r} exactly once"
        )
    composed = block() + "\n"
    updated = BLOCK.sub(lambda _: composed, text)
    if updated == text:
        return False
    README.write_text(updated, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite the measured block in README.md",
    )
    args = parser.parse_args(argv)
    if not args.write:
        print(block())
    elif write():
        print(f"{README.name} now carries the measured surface")
    else:
        print(f"{README.name} already carried the measured surface")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
