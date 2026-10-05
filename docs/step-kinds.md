# Step kinds

Kind is a property of each plan step, so one plan may mix agent work and commands.

A plan authors `command` and `agent.<provider>`. The validator rejects every other family
in a step record: the rest of the vocabulary names nodes the topology derives, and no plan
writes them by hand.

| Family                                  | Engine body                                                                            | Body owner                 |
| --------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------- |
| `command`                               | one `python3 -m cairn exec --command …` invocation                                     | doc 05                     |
| command with `command_type: wait_until` | one `python3 -m cairn wait --until …` invocation, with engine headroom above its bound | doc 05                     |
| `agent.<provider>`                      | one `python3 -m cairn agent run --provider …` invocation                               | doc 02                     |
| `verify`                                | the plan's assertion command, bare, plus the marker step it gates                      | doc 08                     |
| `worktree`                              | `cairn worktree setup` / `cairn worktree prune`                                        | doc 07                     |
| `commit`                                | `cairn commit`                                                                         | doc 07                     |
| `merge`                                 | `cairn merge`                                                                          | doc 10                     |
| `lock`                                  | `cairn lock acquire/release`                                                           | doc 09                     |
| `join`                                  | `cairn wave join`                                                                      | [workflow.md](workflow.md) |

`plan.default_kind` supplies an omitted step kind and defaults to `agent.claude`. A
step-level `kind` overrides it. A command step separately carries its source-quoted
`command` and an explicit `command_type`; prose in `task` is never executed. Provider names
follow `[a-z][a-z0-9_]*`; this grammar is open and provider availability is checked at
execution.

`verify` is always the plan's command emitted bare, with no Cairn exit translation, followed
by the marker step its exit status gates ([verify-gate.md](verify-gate.md)). A wait is a
`command` step invoking `cairn wait`, not another plan kind. Dagu owns fan-out, nested plans,
approval gates, timeouts, retries, and supported built-in actions.

`emit_step` refuses any body that is not one quoted invocation, so the thinness of the
emitted YAML is a property of the emitter rather than a rule reviewers enforce by reading.
It also refuses an agent step whose task would duplicate its own work on a resumed run, a
step nobody has been asked to assert, and an assertion that cannot fail; and it emits every
plan step's marker gate and `continue_on: {failure: true, skipped: true}` together
([step-protocol.md](step-protocol.md), [verify-gate.md](verify-gate.md)).

An agent body carries `--model`, from the step record's `model`
([plan-contract.md](plan-contract.md)), and the emitter refuses an agent step without one: a
session whose model came from the environment would leave a record that cannot say which
model did the work. Every session runs under the hang guard, a fixed Cairn constant enforced
inside the wrapper, the way a wait's bound is: the session is stopped at the guard, resumed
once under the report grace to say what it did, and its report reaches the run directory
before the engine's own `timeout_sec`, which is the guard plus that grace and stops only a
wrapper that never reported. The grace and the arithmetic are stated with every other bound
in [supervision.md](supervision.md).

The plan author owns an agent step's `tools` deny list as its blast-radius declaration — and
it **adds** to a floor Cairn denies in every session, which the plan cannot take back
([cli-contract.md](cli-contract.md)).
Only the selected provider module translates those rules into provider flags. A `command`
step may not carry `tools`: nothing translates a tool policy for a shell command, so
declaring one there is refused rather than dropped.

The `python3 -m cairn` prefix is the doc-05 test seam, not the release resolution contract.
No emitter produces a Dagu `with:` value, and the preflight refuses one outright: the engine
coerces such a value by YAML type, so a string that reads as a number or a boolean would
arrive as one.

## Ownership of what doc 05 does not build

Every other document in this directory points here rather than restating any of it.

| Concern                                                       | Owner                                | State     |
| ------------------------------------------------------------- | ------------------------------------ | --------- |
| Markers, freshness scopes, no-op recovery, protocol lowering  | [step-protocol.md](step-protocol.md) | **built** |
| Worktree and commit bodies, convergence                       | [topology.md](topology.md)           | **built** |
| The verify gate and its exit-status routing                   | [verify-gate.md](verify-gate.md)     | **built** |
| The git write mutex, the repository run lock, `lock` commands | [supervision.md](supervision.md)     | **built** |
| Merge bodies                                                  | [merge-step.md](merge-step.md)       | **built** |
| Complete Dagu generation, preflight, whole-DAG thinness       | [workflow.md](workflow.md)           | **built** |
| The run record, its vocabulary, and the run-directory layout  | [run-model.md](run-model.md)         | **built** |
| Triggers and schedules                                        | [triggers.md](triggers.md)           | **built** |
| The run report                                                | [report.md](report.md)               | **built** |
| The skill surface a person invokes                            | `../SKILL.md`                        | **built** |
| A step's model default                                        | [plan-contract.md](plan-contract.md) | **built** |
| Binary resolution                                             | doc 16                               | to build  |

Doc 09 resolves one path ahead of doc 16: the engine's `base.yaml`, because its retry policy
has to be checked before a run rather than at install time.

The derived node types are **roles**, and their vocabulary is
[topology.md](topology.md)'s — not a second list here.

`emit_node` turns one topology node into one engine step, and every role emits.
[workflow.md](workflow.md) assembles them into a file and refuses to run a malformed one.
