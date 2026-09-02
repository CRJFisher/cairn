# 21 — A step's commit takes the whole working tree, not the step's own work

Found live, on the same task-381 dogfood run [19](19-start-friction.md) and [20](20-visibility-and-follow-on.md) came from. A second Claude Code session was sitting in the same checkout the run was using, fixing an unrelated ESLint config gap (`eslint.config.js`, `package.json`, `pnpm-lock.yaml`), uncommitted, while `work_task_381_10`'s recovery session ran. That session noticed the hazard on its own and did the right thing — its structured output named the three files and said it left them out of its own commit, and its `fix(381.10)` / `feat(381.10)` commits do not touch them. Its wrapping `commit_task_381_10` step then committed them anyway, byte-identical, under a message that describes none of it: `cairn(task_381_10): Look self-keywords up in a Map so x.toString() stops being recorded as a call on self`.

**Serves** the capability surface of **Run**. No invariant moves: the offer's own words stay true — "each verified step's work and its marker land in one commit" — this is about what lands in _that_ commit, not whether one is made.

## A — `commit_all` stages everything in the working tree, not the step's own diff

**Today.** `cairn/worktrees.py:592`, `commit_all`:

```python
git(working_directory, ("add", "--all", "--", str(root)))
```

`root` is `working_tree_root(working_directory)` — the repository root, not anything scoped to the step. `commit_all` is the whole body of `cairn commit` (`cairn/__main__.py:289-291`, `_commit`), which is what every chain-shaped step's `commit_task_X` node runs (`docs/step-kinds.md`'s `commit` role). Whatever is dirty in the working tree at the moment this step runs, however it got there, is staged and lands inside the wrapping `cairn(task_X): <title>` commit.

**Measured, on this run.** `git show 711c1070 -- eslint.config.js package.json pnpm-lock.yaml` shows the full three-file diff, matching the concurrent session's edits line for line — a `globals` import, a `...globals.node` spread, a new devDependency, a lockfile entry. `work_task_381_10`'s own agent session never mentioned or depended on any of it beyond flagging it as someone else's in-flight work; the wrapping commit step ran after that session ended and picked it up anyway, with nothing in the run record noting that the commit carries more than the step's own diff.

**Why the design is not wrong for the topology it was written for.** `capabilities/authoring.md`'s worktree topology gives each step its own worktree, and nothing else is ever dirty there — `git add --all` inside it really is "commit what the step left behind." A chain-shaped plan drops the worktree deliberately (`capabilities/running.md`'s offer prices "no worktrees and no merge" for one) and runs directly in the primary tree instead, which `capabilities/running.md` and `SKILL.md` both treat as the ordinary case — Cairn is driven from inside a coding-agent session that is itself sitting in that same repository. That is exactly the one topology where `commit_all`'s premise — nothing else is here — is false by construction whenever a person is also working in the checkout, which is not a contrived case but the normal way this tool is used.

**The change.** `commit_all` should stage what the step's own session is answerable for, not the working tree at large:

- Snapshot dirty paths (`git status --porcelain`) before the step's agent session starts. At commit time, stage only paths that are dirty now and were not already dirty then.
- A path dirty both before and after — unrelated content that was already sitting uncommitted when the step started — is left alone: not staged, not committed, not silently absorbed. Same shape as `capabilities/running.md`'s existing "the working tree is dirty... a person settles it" refusal for a run's first act, applied per-step instead of only once at the start.
- Record what was excluded (paths seen dirty but not committed) in the step's own report, so `cairn(task_X): ...` can be read as scoped without re-deriving it by diffing the commit against the step's transcript.

**What must not change.** The no-op/failure distinction `commit_all`'s own docstring states stays exactly as it is — "nothing new to commit" and "nothing was ever dirty" are still different only in provenance. The git-write mutex around the whole operation stays; scoping what gets staged does not change who else may write concurrently, only what one step's commit is allowed to claim. `--no-verify` on the commit stays — the verify gate has already run by the time this step does.

**Touches.** `cairn/worktrees.py` (`commit_all`, `_staged_diffstat`), `cairn/__main__.py:289-291` (`_commit`), `docs/run-model.md` (the step-record vocabulary, if "excluded paths" becomes a field), `docs/supervision.md` if the exclusion belongs beside the git-write mutex it already documents, `tests/` — needs a fixture with an unrelated dirty path present before a chain-shaped step's session starts and still present after.

## Acceptance

- A chain-shaped run's `commit_task_X` step, with an unrelated file dirty before the step's session starts and still dirty after, produces a commit containing only the paths the session's own work touched.
- The excluded path is named in the step's own record, not silently dropped and not silently absent from any account of what happened.
- A worktree-topology run is unaffected: nothing there is ever dirty except the step's own work, so scoping the stage changes nothing observable for it.
- Reproduction: two Claude Code sessions in one checkout, one running a chain-shaped Cairn step, the other holding an unrelated uncommitted edit for the step's whole duration — the step's wrapping commit does not contain it.
