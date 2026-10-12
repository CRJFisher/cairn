# 47 — A second person on a second machine walks the README to a verified step

**Status: proposed.** Tasks 12 and 13 of [16](../archive/16-release.md), which 16 built everything
for and could not do itself: they are facts about a machine this repository has never run on.

**North star ([principle 1](../../PRINCIPLES.md)).** A person who did not build Cairn can go from
nothing to a scheduled, verified step by reading one file. Until someone other than the author
has done it on a machine that never held the author's files, that is a claim, not a fact.

## The walk

1. **A clean machine.** A fresh VM with a different username, no pre-existing directories, and
   nothing of the author's. Someone who is not the author follows the README from its first line:
   prerequisites, the engine install, the base configuration, the clone, the symlink and the three
   checks. They then run the failing worked example, author a plan, run it, and land a verified
   step. The walk is timed from the first command to the verified step.
2. **The generated workflow is honest about the machine it names.** A generated workflow is a
   per-machine build product by design ([workflow.md](../../docs/workflow.md)). The check is that
   re-authoring the plan on the target machine produces a workflow that runs there. Whether the
   file carries an absolute path is not the check.

Every place the walker stops, guesses or reads source is a finding. It goes to `fixes/` with the
step it happened at, the way [18](../archive/18-first-run-friction.md) and
[19](../archive/19-start-friction.md) were found.

## Acceptance

- A plan runs end to end on a machine that has never held the author's files, by someone who is
  not the author, following the README alone.
- The cold-start time, first command to verified step, is published in the README.
- A plan authored on one machine and re-authored on the target runs there.
- Every stop the walker hit is either fixed or filed in `fixes/`.

## Touches

`README.md`, and whatever the walk finds.
