"""The one policy every suite that needs a real engine holds to.

A suite whose subject is the engine's own behaviour proves nothing without the engine, and
the defects these suites cover are exactly the ones that otherwise report success — a run
the engine calls clean over a step that never ran, a definition it loads and then skips
whole. So **a missing engine fails the suite**. A silent skip would turn the machine with
no engine into the machine where everything passes, which is the shape of wrongness this
repository exists to refuse.

`CAIRN_SKIP_ENGINE_TESTS=1` is the one way to pass without one, and what it buys is a
record: the run did not check this, and said so.
"""

from __future__ import annotations

import os
import shutil
import unittest

from cairn.enginehome import ENGINE_BINARY

SKIP_ENV = "CAIRN_SKIP_ENGINE_TESTS"
ENGINE = shutil.which(ENGINE_BINARY)


def require_engine(case: unittest.TestCase, what: str) -> str:
    """The engine's path, or the right answer to not having one.

    `what` names the claim that goes unproven, so the failure says which capability is
    unverified rather than only that a binary is missing.
    """
    if ENGINE:
        return ENGINE
    if os.environ.get(SKIP_ENV):
        case.skipTest(f"{SKIP_ENV} is set: {what} was recorded as unchecked")
    case.fail(
        f"{ENGINE_BINARY} is not installed, so {what} is unverified against the engine "
        f"that runs it. Install it, or set {SKIP_ENV}=1 to record that this run did not "
        "check it."
    )


__all__ = ["ENGINE", "SKIP_ENV", "require_engine"]
