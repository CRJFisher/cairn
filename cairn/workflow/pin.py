"""The Cairn a published workflow runs: an immutable copy, pinned when the file is authored.

A run executes `python3 -m cairn` from whatever `PYTHONPATH` its file declares, in a fresh
process per node, for as long as the run lasts. A path to a checkout is therefore not one
Cairn but every state that checkout passes through — and a plan that changes Cairn itself
rewrites the very files its later nodes import. One node then writes a report in one shape
and the next reads it under another, across a seam whose whole job is to agree — a work
node's snapshot written by one Cairn and read by the next as no snapshot at all lands the
step's marker over work that never reached `HEAD`.

So `author` copies the package into Cairn's own state directory under the digest of its
content, and the file names that copy. Every node of every run of that file runs the same
bytes, whatever happens to the checkout meanwhile, and the generator that wrote the bodies
is the runtime that executes them. A change to Cairn reaches a workflow by re-authoring it,
which is already how every other change does.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from cairn.core import CairnError
from cairn.gitio import state_directory

PACKAGE = "cairn"
PINNED_DIRECTORY = "source"

# Build products Python writes beside the sources it imports. They are not Cairn, and the
# interpreter that runs the pinned copy writes its own.
_NOT_SOURCE = shutil.ignore_patterns("__pycache__", "*.pyc")


def package_root() -> Path:
    """The directory this running Cairn is imported from — the default thing to pin."""
    return Path(__file__).resolve().parents[2]


def pin(root: Path, repository: Path) -> Path:
    """Pin the Cairn under `root` for `repository`, and return the path `PYTHONPATH` names.

    The copy is taken first and named by the digest of what was copied, so a checkout edited
    mid-copy still yields a pin whose name is its content. It is moved into place whole, so
    a path that exists is a complete pin, and authoring the same Cairn twice — or twice at
    once — pins it once. Its files are read-only, so nothing edits one by accident.
    """
    package = root / PACKAGE
    if not (package / "__main__.py").is_file():
        raise CairnError(
            "invalid_arguments",
            f"{root} holds no Cairn package to pin: there is no {PACKAGE}/__main__.py under it",
            detail={"package_root": str(root)},
        )
    pinned = state_directory(repository) / PINNED_DIRECTORY
    pinned.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pinning.", dir=pinned))
    try:
        shutil.copytree(package, staging / PACKAGE, ignore=_NOT_SOURCE)
        target = pinned / _digest(staging / PACKAGE)
        if (target / PACKAGE).is_dir():
            return target
        for directory, _, files in os.walk(staging / PACKAGE):
            for name in files:
                os.chmod(Path(directory) / name, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            os.rename(staging, target)
        except OSError:
            if not (target / PACKAGE).is_dir():
                raise
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _digest(package: Path) -> str:
    """The identity of a package's source: every file's path and bytes, in a fixed order."""
    running = hashlib.sha256()
    for path in sorted(_files(package)):
        running.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
        running.update(path.read_bytes())
        running.update(b"\0")
    return running.hexdigest()


def _files(package: Path) -> list[Path]:
    return [
        Path(directory) / name for directory, _, files in os.walk(package) for name in files
    ]


__all__ = ["PINNED_DIRECTORY", "package_root", "pin"]
