# 53 — A reused worktree carries a halted run's work, and the commit disowns it

**Status: proposed.** Found live, recovering a fan-out run whose first attempt halted before
any step committed.

**North star ([principle 2](../../PRINCIPLES.md)).** A marker is published only over state the
commit carries ([21 B](../archive/21-commit-scope.md)). Today a recovery can publish a marker
over a branch that holds none of the work its assertion passed over, and the next run skips
the step.

## What happened

A run halted after its steps had done their work in their worktrees but before any commit.
The worktrees kept that work uncommitted. A recovery started and `worktree setup` reused each
worktree as `healthy`. Two outcomes followed, one per step:

- **A step that touched its own leftover work again was refused.** Its session finished the
  work and changed paths that were already dirty when the attempt started. The commit raised
  `excluded_path_changed`, committed nothing, and withdrew the marker.
- **A step that had nothing left to do committed the marker alone.** Its session changed no
  path. Its assertion passed over the whole tree, leftover work included. The commit staged
  only the marker, because every other dirty path predated the attempt, and reported the rest
  as `left_uncommitted`. Had that branch landed, the parent would have recorded the step as
  done with none of its code, and every later run would skip it.

## Root cause

The commit's baseline is "dirty when this attempt's session started" (`dirty_snapshot`,
recorded as `dirty_before`). That baseline exists to protect a person's uncommitted work in a
chain step, which runs in the primary checkout ([21](../archive/21-commit-scope.md)).
`commit_step` assumes a worktree run starts clean, so the baseline is harmless there.

A reused worktree breaks the assumption. Nobody but this plan's step writes in its worktree,
which the branch-owner ref already proves, so everything dirty there is the step's own work
from an earlier attempt. The per-attempt baseline classifies it as somebody else's.
`classify` in `cairn/worktrees.py` reads `dirty_paths` and does nothing with them for a
`healthy` worktree.

The second outcome also shows a gap in [21 B](../archive/21-commit-scope.md)'s guard. It
proves excluded paths unchanged, but it allows a marker-only commit while asserted content sits
uncommitted beside it.

A chain step is not exposed to this, because `run start` refuses a dirty primary tree before
the run's first act.

## What changes

### A — A step's own worktree has no foreign work

In a worktree topology the commit's baseline is the branch tip, not the attempt start: every
dirty path in the step's worktree is the step's, and it is staged. `worktree setup` records the
paths it found dirty on reuse as `carried` in its report, so the record says the commit holds
work from an earlier attempt and names that attempt's run where it can.

### B — A marker never lands beside work its assertion covered and the commit left out

Where a commit would leave any path uncommitted in a step-owned worktree, it refuses, withdraws
the marker, and names the paths. In the primary checkout, `left_uncommitted` stays as it is,
because there it means a person's work.

## Acceptance

- A recovery over a worktree holding a halted attempt's uncommitted work commits that work with
  the step's marker, whether or not the recovering session changes any path.
- `worktree setup`'s report names the carried paths.
- No step-owned worktree ends a commit with a marker in `HEAD` and a dirty path beside it.
- A chain step with a person's unrelated dirty path is unchanged: that path is left alone and
  named.
- Fixtures cover both outcomes above: a step that touched carried paths, and a step that touched
  nothing.

## Touches

`cairn/worktrees.py` (`commit_step`, `_commit_scoped`, `_converge`), `cairn/__main__.py`
(`_with_dirty_before`), `docs/topology.md`, `docs/run-model.md` for the `carried` field, `tests/`.
