# 52 — A wave's merge lands nothing, because it reads the step name out of the branch wrongly

**Status: proposed.** Found live, on a fan-out plan's first wave. Every fan-out run is affected,
not only the one it was found on.

**North star ([principle 1](../../PRINCIPLES.md), [principle 2](../../PRINCIPLES.md)).** A
wave whose branches all verified lands them in the parent branch, and the record says what
landed. Today it lands none of them, reports success, and the next step runs over an empty
parent.

## What happened

Two steps of one wave ran in their own worktrees, passed their assertions, and committed on
their branches. The merge slot then surveyed both branches, found no verified work on either,
and landed nothing. Its proof (`merge verify`) passed, because "nothing new is in the parent"
was true. The plan's next step, a review of the merged wave, found an empty diff and stopped.
Cairn pruned both worktrees and kept both branches.

## Root cause

A step's branch is `step/<plan>/<step>` ([topology.md](../../docs/topology.md),
`branch_name`). `survey` in `cairn/merge.py` recovers the step id with
`branch.split("/", 1)[-1]`, which strips only `step/` and yields `<plan>/<step>`. It then asks
`_gate_cause` for the mark report of a step called `<plan>/<step>`. No such report exists, so
the read raises `missing_report`, the cause becomes `not_reached`, and every candidate is
excluded as a step that never ran.

The inverse already exists: `topology.step_of_branch` parses `step/<plan>/<step>` back to the
step id and returns None for any other ref. `survey` predates the plan segment and was never
moved onto it. No test calls `survey`, and the merge tests build branches in the old
`step/<step>` shape, so nothing caught the drift.

## What changes

### A — The survey reads the step id through the grammar's own inverse

`survey` derives the step with `step_of_branch`. A candidate that does not parse is a fault
in the generated workflow, not a step that never ran: it is refused with its own cause naming
the ref, never classified `not_reached`.

### B — A merge that lands nothing over branches carrying work says so

A branch that carries work (`has_work`) and is excluded as `not_reached` is a contradiction:
work on a step's branch means the step ran. The survey treats that pair as
`gate_indeterminate` and the slot halts naming the branch, so a future drift between the
branch grammar and the report names cannot pass silently again.

### C — The merge tests use the branch the topology builds

Every merge test composes its branches with `branch_name(plan, step)`, and `survey` gains
direct tests: a verified branch is landable, an unverified one carries its gate's cause, and
a ref outside the grammar is refused.

## Acceptance

- A fan-out wave whose branches all verified lands every one of them in the parent, and the
  record lists each as landed.
- A branch that carries work and whose mark report cannot be found halts the slot with
  `gate_indeterminate`, naming the branch; it is never recorded as `not_reached`.
- A candidate ref that `step_of_branch` does not parse is refused with a cause naming it.
- No test in `tests/` builds a step branch by hand.

## Touches

`cairn/merge.py` (`survey`), `tests/test_merge_step.py`, `docs/merge-step.md` if the new
refusal joins its cause vocabulary.
