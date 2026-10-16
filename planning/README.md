# Planning

Every planning document, what kind it is, what state it is in, and what is left. This table is
the one index: reading it answers "what is outstanding" without opening a document.
[docs/](../docs/) holds the contracts the code keeps. This folder holds the plans that change them.

## How the folder is kept

- **The folder is the kind.** `fixes/` holds a defect found live, with a reproduction and an
  acceptance list. `features/` holds a capability-scale plan with Serves and Acceptance.
  `ideas/` holds open questions with no acceptance list yet. `archive/` holds every closed
  document, unchanged.
- **Line 3 is the state.** Every live document opens with its title, a blank line, and
  `**Status: <state>.**`, where the state is one of `idea`, `proposed`, `building` or `built`.
  Every document in `ideas/` is `idea`, and no document elsewhere is.
- **Archive means nothing left to do in that document.** A plan with residue archives whole, and
  the residue becomes a new short document in `fixes/`, `features/` or `ideas/`, linked back.
- **Numbers are global and never reused.** The next document is 56. Moving a document between
  folders is a rename, and every link to it is rewritten in the same change.
- **A document moves at most twice:** from `ideas/` to `features/` or `fixes/` when it gains an
  acceptance list, and from there to `archive/` when it closes.

`tests/test_planning.py` holds every document to these rules and to this table.

## Live, in the order to take them

| #   | Document                                                                                                  | Kind    | State    | Outstanding                                                                                     |
| --- | --------------------------------------------------------------------------------------------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------- |
| 53  | [A reused worktree carries a halted run's work](fixes/53-a-reused-worktree-carries-a-halted-runs-work.md) | fix     | proposed | Baseline a step's worktree at its branch tip; never land a marker beside uncommitted work       |
| 54  | [A done step's re-check cascades](fixes/54-a-done-steps-recheck-cascades.md)                              | fix     | proposed | A plan's `prepare` for worktrees; exit 126/127 as `assertion_unrunnable`                        |
| 55  | [Engine processes already running](fixes/55-engine-processes-already-running.md)                          | fix     | proposed | Run reads the scheduler and stops nothing; asks only on an unsafe machine                       |
| 51  | [Counting refused reports](fixes/51-counting-refused-reports.md)                                          | fix     | proposed | Count report refusals over ten runs; decide whether the summary must also be shorter            |
| 49  | [The allowance at a real limit](features/49-the-allowance-at-a-real-limit.md)                             | feature | proposed | Record a real `rejected` event, resume after it, read the endpoint live, cost a step in percent |
| 47  | [A second machine walks the README](features/47-a-second-machine-walks-the-readme.md)                     | feature | proposed | A second person on a clean machine reaches a verified step; publish the cold-start time         |
| 48  | [A generated workflow that travels](ideas/48-a-generated-workflow-that-travels.md)                        | idea    | idea     | Make the interpreter portable, or keep it per-machine and guard it so a run is never green      |
| 50  | [A model fallback the plan states](ideas/50-a-model-fallback-the-plan-states.md)                          | idea    | idea     | Whether to let a plan name a cheaper model instead of holding                                   |
| 35  | [Human in the loop](ideas/35-human-in-the-loop-steps.md)                                                  | idea    | idea     | A run asks a person mid-step and carries on with the answer                                     |
| 40  | [The task list as the tab source](ideas/40-task-list-as-the-tab-source.md)                                | idea    | idea     | Findings for 36: read the agent's own task list instead of summarising the transcript           |
| 36  | [Keeping tabs](ideas/36-keeping-tabs.md)                                                                  | idea    | idea     | A live, plain-language summary of every running session                                         |
| 37  | [One plane for watching and answering](ideas/37-one-plane-for-watching-and-answering.md)                  | idea    | idea     | One page for both, once 35 and 36 each work alone                                               |

## Archive

| #   | Document                                                                                         | Kind    | Residue                                                                                                                                                  |
| --- | ------------------------------------------------------------------------------------------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 16  | [Release](archive/16-release.md)                                                                 | feature | Tasks 12 and 13 → [47](features/47-a-second-machine-walks-the-readme.md); the open half of task 15 → [48](ideas/48-a-generated-workflow-that-travels.md) |
| 18  | [First-run friction](archive/18-first-run-friction.md)                                           | fix     | —                                                                                                                                                        |
| 19  | [Start friction](archive/19-start-friction.md)                                                   | fix     | None. Bucket H is closed by 23 B and 24 D, I by 22 and 24, J by the 2.18.2 pin (41, [triggers.md](../docs/triggers.md))                                  |
| 20  | [Watching a live session](archive/20-watching-a-live-session.md)                                 | feature | —                                                                                                                                                        |
| 21  | [Commit scope](archive/21-commit-scope.md)                                                       | fix     | —                                                                                                                                                        |
| 22  | [A timed-out step](archive/22-timed-out-step.md)                                                 | fix     | —                                                                                                                                                        |
| 23  | [Reading a broken run](archive/23-reading-a-broken-run.md)                                       | fix     | —                                                                                                                                                        |
| 24  | [Recovery economics](archive/24-recovery-economics.md)                                           | fix     | —                                                                                                                                                        |
| 25  | [Execution admission](archive/25-execution-admission-and-paid-bounds.md)                         | fix     | —                                                                                                                                                        |
| 26  | [Authoring closure and source provenance](archive/26-authoring-closure-and-source-provenance.md) | fix     | —                                                                                                                                                        |
| 27  | [Cross-plan git and lock isolation](archive/27-cross-plan-git-and-lock-isolation.md)             | fix     | —                                                                                                                                                        |
| 28  | [Record ingestion integrity](archive/28-record-ingestion-integrity.md)                           | fix     | —                                                                                                                                                        |
| 29  | [Operator and report ergonomics](archive/29-operator-and-report-ergonomics.md)                   | feature | —                                                                                                                                                        |
| 30  | [A plan stated in the request](archive/30-a-plan-stated-in-the-request.md)                       | fix     | —                                                                                                                                                        |
| 31  | [The repository the session is in](archive/31-the-repository-the-session-is-in.md)               | fix     | —                                                                                                                                                        |
| 32  | [No cost](archive/32-no-cost.md)                                                                 | feature | —                                                                                                                                                        |
| 33  | [The view link answers](archive/33-the-view-link-answers.md)                                     | fix     | —                                                                                                                                                        |
| 34  | [Follow-on runs](archive/34-follow-on-runs.md)                                                   | feature | —                                                                                                                                                        |
| 38  | [Subscription headroom](archive/38-subscription-headroom.md)                                     | feature | Spikes 1, 2, 3 and 5 and open questions 1 and 2 → [49](features/49-the-allowance-at-a-real-limit.md); question 3 is closed by 43                         |
| 39  | [A failed assertion is a question first](archive/39-a-failed-assertion-is-a-question-first.md)   | fix     | —                                                                                                                                                        |
| 41  | [Dagu 2.18 upgrade](archive/41-dagu-2-18-upgrade.md)                                             | feature | —                                                                                                                                                        |
| 42  | [A step held before it started](archive/42-a-step-held-before-it-started.md)                     | fix     | —                                                                                                                                                        |
| 43  | [The allowance belongs to an account](archive/43-the-allowance-belongs-to-an-account.md)         | fix     | —                                                                                                                                                        |
| 44  | [A held run resumes itself](archive/44-a-held-run-resumes-itself.md)                             | feature | —                                                                                                                                                        |
| 45  | [Spending the allowance knowingly](archive/45-spending-the-allowance-knowingly.md)               | feature | Section E → [50](ideas/50-a-model-fallback-the-plan-states.md)                                                                                           |
| 46  | [A report refused five times](archive/46-a-report-refused-five-times.md)                         | fix     | Section D → [51](fixes/51-counting-refused-reports.md)                                                                                                   |
| 52  | [The merge reads the wrong step name](archive/52-the-merge-reads-the-wrong-step-name.md)         | fix     | —                                                                                                                                                        |
| —   | [What to fix first](archive/what-to-fix-first.md)                                                | index   | The order it set is done. This table replaces it                                                                                                         |
