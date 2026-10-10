"""Ending a child a test launched, and closing what it was read through.

A child cleaned up with `kill` alone leaves the parent's ends of its pipes open: they are
closed when the object is collected, and the interpreter reports each one as an unclosed
file. A suite that launches hundreds of children then reports leaks it does not have, over
the one it would — a descriptor table filling up under a long run — so every test ends a
child through here rather than killing it in place.
"""

from __future__ import annotations

import subprocess
from typing import Any

REAP_TIMEOUT_SECONDS = 30.0


def reap(child: subprocess.Popen[Any], *, timeout: float = REAP_TIMEOUT_SECONDS) -> None:
    """Make sure a child is dead and its pipes are shut, whichever it already was.

    Safe to call on a child that has already exited, and safe to call twice: what it
    guarantees is the end state, not that it did anything to reach it.
    """
    try:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=timeout)
    finally:
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


__all__ = ["REAP_TIMEOUT_SECONDS", "reap"]
