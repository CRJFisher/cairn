# 52 — A wave's merge lands nothing, because it reads the step name out of the branch wrongly

**Status: built.** Found live, on a fan-out plan's first wave. Every fan-out run is affected,
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
- A branch that carries work and whose mark report cannot be found, or cannot be read, halts
  the slot with `merge_unjudged_work`, naming the branch; the census records it as
  `gate_indeterminate`, never as `not_reached`.
- A candidate ref that `step_of_branch` does not parse is refused with `merge_foreign_branch`,
  naming it.
- No test in `tests/` builds a step branch by hand.

## As built

- **The halt has a cause of its own.** The slot halts with the CLI cause `merge_unjudged_work`
  rather than the exclusion value `gate_indeterminate`, because the two vocabularies are kept
  distinct ([cli-contract.md](../../docs/cli-contract.md)). The branch's survey entry and the
  join's census carry `gate_indeterminate`.
- **An unreadable mark report halts too.** It may have recorded the work as verified, so
  leaving the branch behind would be the same silent loss as a missing one. A report the gate
  wrote naming `not_reached` or `gate_indeterminate` is a judgement, and stays an exclusion.
- **The join refuses a foreign ref.** It shares the survey, and a ref no step owns is a fault
  in the emitted workflow; it still never refuses over what a gate decided.
- The remaining literal `step/<id>` refs in `tests/` are deliberate: foreign refs under test
  (`test_supervision.py`, the survey and join refusals), the grammar's expected outputs
  (`test_topology.py`), and record fixtures that never touch git.

## Touches

`cairn/merge.py` (`survey`, `classify_candidate`, `run_merge`), `cairn/wave.py`,
`tests/test_merge_step.py`, `tests/test_workflow.py`, `docs/merge-step.md`,
`docs/workflow.md`, `docs/cli-contract.md`, `docs/verify-gate.md`.
