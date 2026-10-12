# 48 — A generated workflow that finds its own interpreter

**Status: idea.** The half of [16](../archive/16-release.md) task 15 that 16 left open. The
minimum interpreter version is now stated. How a generated file resolves its interpreter has not
changed.

**North star ([principle 1](../../PRINCIPLES.md)).** A scheduled workflow runs the Cairn it was
authored against, or says plainly that it cannot. It must never skip every step and report a
clean success.

## Today

The emitted `env:` carries an absolute `PYTHONPATH` resolved on the authoring machine
(`cairn/workflow/build.py`, `cairn/workflow/pin.py`), and every body invokes the bare name
`python3`, resolved from the step's `PATH`. A generated workflow is therefore bound to the machine
that wrote it. If a run's `python3` is older than the package needs, it fails to import Cairn. The
engine reads that failure as a skipped precondition, so every step skips and the run reports a
clean success.

The preflight rehearses the invocation under the environment the file declares, so authoring
catches the case. It borrows `PATH` from the authoring process, though, so it cannot see a run
launched with a different `PATH`: a scheduler started from a login shell, a `launchd` agent, or
another user.

## Open questions

1. **Portable, or per-machine and guarded?** [47](../features/47-a-second-machine-walks-the-readme.md)
   holds the generated file to being a per-machine build product. If that stays the design, the
   remaining work is a guard: a body whose interpreter cannot import Cairn fails the step, so the
   run is never green. If the file is to travel, the body resolves an interpreter the file itself
   names.
2. **Which interpreter does a body name?** The authoring interpreter's absolute path is exact,
   but it ties the file to one machine. The bare `python3` is portable and is the reason for the
   hazard.

## Touches

`cairn/workflow/build.py`, `cairn/workflow/pin.py`, `cairn/workflow/preflight.py`,
`docs/workflow.md`.
