"""The platform refusal every entry point passes through before anything else is imported.

Cairn is POSIX-only by construction rather than by omission: a repository's run lock is an
`fcntl` advisory lock, a cancelled step is a process-group signal, and a step's isolation is
a git worktree. On a platform without those, the first command a person runs fails on a
missing standard-library module — which reads as a broken install rather than as an
unsupported platform, and sends them looking for the wrong fix.

Every `cairn.*` import runs this module first, so the refusal arrives before the import that
would otherwise fail, and it names the thing to do instead ([16] task 9).
"""

from __future__ import annotations

import sys

# Measured on macOS and Linux. Windows is refused rather than attempted: the modules the
# locks and the cancellation are built on do not exist there, and a partial port would be a
# repository mutated by a run whose lock did nothing.
UNSUPPORTED_PLATFORMS = ("win32", "cygwin")


def unsupported(platform: str) -> str | None:
    """Why Cairn will not run on this platform, or None where it will.

    A function rather than a bare condition, so the refusal can be read by a test without
    a second interpreter — and so the message has one author.
    """
    if not platform.startswith(UNSUPPORTED_PLATFORMS):
        return None
    return (
        f"Cairn does not run on {platform!r}. Its run locks are fcntl advisory locks, a "
        "cancelled step is a process-group signal, and a step stands in a git worktree — "
        "none of which this platform provides. Run it under WSL, where it is the Linux "
        "install path in the README and nothing here changes."
    )


_refusal = unsupported(sys.platform)
if _refusal is not None:
    raise ImportError(_refusal)


__all__ = ["UNSUPPORTED_PLATFORMS", "unsupported"]
