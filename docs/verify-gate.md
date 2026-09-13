# The verify gate

Every step is followed by a command that asserts the step's end state on disk. That
command's exit status — not the step's account of itself — decides what happens next.

**Verify owns the green light. Self-report owns the veto.** A step's report can lower its
outcome and never raise it. A step whose assertion fails produced no verified work, whatever
it says about itself, and a disagreement between the two is recorded as a divergence rather
than resolved.

## The three nodes a step becomes

```yaml
- name: config_schema # the work — its own gate, and both routing flags
  run: python3 -m cairn agent run --provider claude --prompt '…' --model sonnet --max-budget-usd 5.0 --timeout 3600
  working_dir: /worktrees/config_schema
  timeout_sec: 3780 # the step's own bound plus the report grace
  retry_policy: { limit: 0, interval_sec: 1 }
  preconditions:
    - condition: python3 -m cairn marker absent --step config_schema --scope once
  continue_on: { failure: true, skipped: true }

- name: verify_config_schema # the plan's own command, verbatim
  id: verify_config_schema
  depends: [config_schema]
  run: test -e config-schema.md
  working_dir: /worktrees/config_schema
  timeout_sec: 600
  retry_policy: { limit: 0, interval_sec: 1 }
  preconditions:
    - condition: python3 -m cairn verify needed --step config_schema --command-digest <sha256>
  continue_on: { failure: true, skipped: true }

- name: mark_config_schema # the record, gated on the assertion and the report
  depends: [verify_config_schema]
  run: python3 -m cairn marker write --step config_schema --scope once
  working_dir: /worktrees/config_schema
  timeout_sec: 600
  retry_policy: { limit: 0, interval_sec: 1 }
  preconditions:
    - condition:
        python3 -m cairn verify gate --step config_schema --position branch
        --verify-exit '${verify_config_schema.exit_code}'
  continue_on: { skipped: true } # BRANCH only — omitted in a CHAIN
```

**The assertion is the plan's own command and nothing else.** No wrapper, no subcommand, no
quoting, no exit-code translation. A step whose exit status is the verdict must have nothing
of Cairn's between it and the engine. It runs in the same working directory as the step it
asserts, and it **never retries**: a fact check asked twice is a different question.

**Whether it runs at all is Cairn's, and that is its precondition.** `cairn verify needed`
declines the assertion in two cases, and the first is this: the step's work node left no
report of this run, which is a step an upstream halt skipped — or one the engine killed
before it could write one, which is why such a step carries no assertion verdict
([run-model.md](run-model.md)). Its assertion would run against a tree the step
never touched and its gate would close `not_reached` regardless — measured, thirteen
full-suite executions past one fault, half an hour proving nothing a gate could read. A
marker no-op leaves its `noop` report and keeps its assertion, which is the recovery
guarantee: a step already done still has its end state asserted on today's tree. The gate
fails open like the marker gate, because running an assertion that need not run costs
minutes and skipping one that must run costs the step its record; and it writes its
decision — `run`, `shared` or `skipped_upstream` — to the assertion node's own report
before it answers, on every path. The verify gate below trusts the engine's exit-status reference
only where that decision says the assertion ran: measured against Dagu 2.11.0,
`${<id>.exit_code}` resolves to `0` for a node its precondition skipped, and a gate that
read it would record a marker over an assertion that never ran. The assertion node carries
`continue_on: {skipped: true}` beside `failure: true` so a declined assertion still lets the
marker's gate run and record the halt; a precondition skip cascades otherwise.

**One proof per command per tree, within a run.** The same gate declines an assertion whose
exact command has already been proven, in this run, against exactly this tree — a recovery
no-ops every finished step in milliseconds and would otherwise re-prove one command
fourteen times, half an hour of the same suite over the same bytes ([24 A]). The key is the
command's bytes **and** the tree's state, never the command alone: a step doing new work
commits, and every later gate quoting the same command would otherwise read a proof taken
before the work it is meant to assert. The tree's state is the commit it stands on plus
every dirty path's content, with two things Cairn itself writes left out because a digest
that moved with them would never hit: the markers under `.steps/`, one of which is written
on every step that is recorded, and the runs root, where every gate files its own account.
The proof lives at
`runs/<run-id>/assertions/<command-sha256>.json`, keyed by run identity, so a recovery — a
fresh run id — proves everything again against the tree it finds. A shared failure closes
every gate quoting it: sharing never widens what passes. A step-specific command shares
nothing, because its bytes are its own. The assertion node's report says which it was —
`executed` by this step, or `shared` from the step whose execution backed it — and the run
record carries the exit and the execution that backed it for every step. A proof already
filed as a failure for the same command against the same tree is never replaced by a
passing one: two steps can execute one command concurrently, each missing the other's
proof, and a pass written over a failure would reopen every gate the failure closed.

**The marker write is a separate step.** Verification decides; a step gated on that decision
records. `cairn marker write` is the only writer of `.steps/<id>.done`, so a marker means
_verified_ and never _claimed_ — and a step that failed verification leaves no completion
record to make the next run skip it ([step-protocol.md](step-protocol.md)).

**The marker quotes the step's own account.** Its one-line summary is read from the step's
report, because only the step that did the work can say what it did, and the marker reaches
git and outlives every report beside it.

## Failure routes by position, through one flag

| Position   | `continue_on` on `commit_<id>` | What a failed assertion does                                        |
| ---------- | ------------------------------ | ------------------------------------------------------------------- |
| **chain**  | omitted                        | halts the chain: downstream steps depend on work that is not there  |
| **branch** | `{ skipped: true }`            | excludes the branch: its commit is skipped and the merge still runs |

One pattern, emitted everywhere; no step kind routes differently from another. The verify
step's `continue_on: {failure: true}` is what keeps the run alive to reach the join while the
failure stays recorded as **failed** — a run whose exclusions are all `skipped`, with no
failed node anywhere, reports plain `Succeeded` with exit 0.

**Exactly one node in a step's group carries the flag, and it is the commit.** A closed gate
skips `mark_<id>`, and that skip has to reach the commit — which is what excludes the
branch. Measured against Dagu 2.11.0, a `continue_on: {skipped: true}` on the marker instead
lets the commit run over a gate that refused to record anything, landing exactly the
unverified work this document exists to stop. The same measurement settles the other half:
the flag works on a node skipped by cascade, not only on one skipped by its own
precondition, so the commit stops the cascade before the join without ever running itself.

The work step carries `{failure: true, skipped: true}`. `skipped` stops a correct no-op
cascading into its own assertion and marker; `failure` keeps a step's own reported failure
from aborting its assertion, without which a step that reported failure over work that is
actually there could never be told from one that did nothing.

## What the gate reads

The gate reads two things and opens only when both agree:

| The assertion  | The step's report                        | The gate | Cause                    | Divergence                                                                   |
| -------------- | ---------------------------------------- | -------- | ------------------------ | ---------------------------------------------------------------------------- |
| exit 0         | `done` or `noop`                         | opens    | —                        | —                                                                            |
| either         | `failed` with `cause: provider_protocol` | closes   | `provider_protocol`      | **yes** where the assertion passed — verified true, reported nothing         |
| either         | `failed` with `cause: timed_out`         | closes   | `timed_out`              | **yes** where the assertion passed — verified true, stopped before reporting |
| exit 0         | `failed`                                 | closes   | `reported_failure`       | **yes** — verified true, reported failed                                     |
| nonzero        | `done` or `noop`                         | closes   | `verify_failed`          | **yes** — reported done, verified false                                      |
| nonzero        | `failed`                                 | closes   | `reported_failure`       | — they agree                                                                 |
| either         | `needs_user_decision`                    | closes   | `user_decision_required` | —                                                                            |
| either         | none from this run                       | closes   | `not_reached`            | —                                                                            |
| unreadable     | any                                      | closes   | `gate_indeterminate`     | —                                                                            |
| any            | unreadable                               | closes   | `gate_indeterminate`     | —                                                                            |
| _(unverified)_ | `done` or `noop`                         | opens    | —                        | —                                                                            |

A report can carry `failed` **and** `cause: provider_protocol`, or `failed` **and**
`cause: timed_out`; the cause is the narrower fact, so those rows are judged before the
plain `failed` rows below them. The second is the wrapper's own account of a session it
stopped at the step's bound and could not get a report from ([step-kinds.md](step-kinds.md)):
the step said nothing, so reading `failed` as a veto would put a verdict in its mouth.

**Every fault closes it.** This is the exact inverse of the marker gate, which opens on every
fault it meets. Both are the safe direction, and the asymmetry is the design: redoing
convergent work costs one run, while a marker over unverified work reaches git, rides every
merge, and makes the next run skip the step that would have caught it.

The gate is a **precondition, not a step**, so it writes its own report only when it closes
— the one path where no step will run to write one. It runs as the marker step's
precondition, so that report is filed under the marker step's name, at
`<run-dir>/reports/mark_<id>.json`, and carries the cause, the position, the assertion's
exit status, and the divergence when there is one. It also names the exclusion on stderr,
because an exclusion is never silent. Where the assertion ran, the gate completes the
assertion node's own report too — the one its gate opened with its decision — with the exit
it read, and files that exit as the proof later gates share; the assertion runs bare and can
write nothing itself.

**A divergence is recorded and never resolved.** Both accounts stand side by side, and
nothing names a winner.

## Why a step contributed no verified work

Every step the gate declines to record carries a cause from this frozen set, as an enum
value and never a message string. `not_reached` is the one member that is a halt rather
than an exclusion: the gate found no report of this run, so as far as anything durable
shows, the step never ran. The engine's own node status is what separates a step that was
never reached from one killed before it could write.

| Cause                    | Meaning                                              | Written by                  |
| ------------------------ | ---------------------------------------------------- | --------------------------- |
| `verify_failed`          | the assertion exited nonzero                         | the gate                    |
| `reported_failure`       | the step's own report vetoed it                      | the gate                    |
| `provider_protocol`      | the step left no readable account of itself          | the gate                    |
| `user_decision_required` | the step is blocked on a human decision              | the gate                    |
| `not_reached`            | the step left no report of this run, so it never ran | the gate                    |
| `gate_indeterminate`     | the gate could not establish what happened           | the gate                    |
| `timed_out`              | the step was stopped at its bound before it reported | the gate, or the run record |
| `retry_exhausted`        | the step hit its retry bound                         | the run record              |
| `orchestrator_died`      | the run's own process was killed under the step      | the run record              |

`gate_indeterminate` exists because folding an unreadable report into `not_reached` would
claim a step never ran when it may have done all of its work.

`provider_protocol` exists for the same shape of reason one row up. It covers every
unreadable-protocol fault — a malformed stream line, an unknown status, a summary that is not
a string — and a session that ended a turn without reporting at all is only the commonest of
them; the step's own recorded summary says which it was. A session that ends a
turn without producing its structured report is written `failed` by the runtime, because
that is the only status a report can carry when there is nothing to carry — and reading
that back as `reported_failure` tells the person their session claimed a failure it never
claimed. It is also the more expensive mistake: measured, a step whose session ended without
reporting had done the work and its assertion passed, and the divergence recorded against it
said _"the step reported 'failed' over an assertion that passed"_ about a step that reported
nothing at all. The word appears in two vocabularies, as `reported_failure` and
`user_decision_required` already do — a report cause ([cli-contract.md](cli-contract.md))
seen from the runtime's side, and an exclusion cause seen from the gate's.

**Where the assertion passed, the divergence is still recorded**, with the step's own word
given as `nothing`. That is the only channel the fact has: a mark report contributes its
cause, its position and its divergence to the run record, and neither the gate's summary nor
the assertion's exit status reaches it. Without the divergence, a step whose work is sitting
verified in the tree would be indistinguishable from one that did nothing.

**A marker that outlived its work is surfaced, not repaired.** A step whose marker is fresh
is skipped, and its assertion still runs; if the tree has since regressed the assertion
fails, the gate closes, and the divergence records a step reporting `noop` over an assertion
that did not hold. Nothing deletes the marker, because a mismatch between marker and tree
can be someone's later deliberate decision ([step-protocol.md](step-protocol.md)). Removing
the marker is the operator's move, and the record is what tells them to make it.

**A chain halt and a branch exclusion are distinguishable.** The engine spells both
`skipped`, so the distinction is Cairn's: the gate records the `position` it was emitted at,
and a step that left no report of this run is `not_reached` rather than excluded. A
cascade-skipped step evaluates no precondition and runs no body, and a step carrying
`continue_on: {skipped: true}` still lets the nodes behind it run — its assertion's own gate
declines the assertion for want of a report, and the marker's gate then reads the same
absent report as the halt it is. The absent report is read first, before any exit status,
which is what stops a passing assertion over a tree the step never touched from being
recorded.

## The step with no checkable effect

A step whose deliverable is the agent's own output has nothing on disk to assert. It is
declared unverified in the plan, emits no assertion node, and its marker is gated on its own
report alone. An unverified step is honest, not free: it is a warning on every parse report,
it appears in the run's attention section, and a run containing one cannot claim every step
was verified.

**A step is unverified only where a human declined a proposal.** A step carrying neither an
assertion nor a recorded answer is refused at emission, naming the step and the command that
opens the conversation. The parse report spells the two apart — `**unverified**` against
`**never asked**` — because one spelling for both would make `unverified` mean nothing.

## The authoring conversation

**10 of the corpus's 41 steps carry no assertion, and 8 of those 10 are the two real plan
documents** — every step they contain. So the missing-assertion path is the normal path for
documents people have already written, and what happens then is a designed conversation:

```text
python3 -m cairn plan propose <graph>
python3 -m cairn plan answer  <graph> --step <id> --command <text> --out <graph>
python3 -m cairn plan answer  <graph> --step <id> --decline --reason <text> --out <graph>
```

`propose` shows, for each unanswered step, the step's own words, the document's words the
derivation read, and the command the derivation proposed — or the plain statement that it
offered nothing. It **writes nothing**: a proposal is an offer, and only an answer is a
decision.

A proposal is **the derivation's own reading, declared on the graph**. The agent deriving
the graph is the only thing in the system that has read the plan, so it is the only thing
that proposes: it records the command on the step's `missing_verify` question, resting on
the sentence the question quotes ([plan-derivation.md](plan-derivation.md)). Code carries
that declaration and checks its provenance, never its meaning — the validator refuses a
proposal quoting no words, a quote no document contains, and a proposal that cannot fail
([plan-contract.md](plan-contract.md)). Where the derivation reads no assertable end state
in the document's words, it proposes nothing, and saying so is the honest offer.

A command that cannot fail — `true`, `:`, `exit 0`, or nothing at all — is refused when it
is answered, not later: it would read as verified in the report while asserting nothing. An
assertion runs under the operator's own `$SHELL` with `pipefail` off, so the last command of
a pipeline is the assertion, and an assertion that already holds before the step runs is not
an assertion.

`answer` derives the outcome rather than being told it, against the offer the graph itself
carries: a command equal to the proposal is **accepted**, a different one is **edited**, one
written where nothing was offered is **authored**, and a decline records the proposal that
was declined so the report can say what was offered. Every answer carries the offer it
answered, a decline included, because `answer` records it from the question it clears — no
invocation can drop or misquote it.

The corpus records the conversation against both real plans — eight steps, none of which
named a command: **6 proposals offered: accepted 6, edited 0; authored 0, declined 2**. The
two declines are the steps whose end states no command in the plan's own tree can assert —
behavioural parity inside another repository, and a unit whose very name the document leaves
undecided. Whether live authors accept what a live derivation proposes is the paid suite's
`authoring_acceptance`, and that number — not this corpus — is what a release is judged on.

## What this hands forward

- **The commit step** stages `.steps/<id>.done` by path rather than staging the directory,
  so neither a fragment a killed writer left nor a marker an agent forged reaches history —
  and beside it only the paths the step's own session dirtied, so another session's
  in-flight work in the same checkout is left alone and named rather than committed under
  the step's message ([cli-contract.md](cli-contract.md)).
- **The preflight** rejects `mark_success` anywhere and `continue_on: {output: …}` anywhere;
  it requires `continue_on: {failure: true}` on every assertion node, and it must accept
  `${<id>.exit_code}` in a precondition as a reference that resolves at run time.
- **The run record** reads the gate's reports for the exclusion cause, the position, and the
  divergence, and derives `timed_out`, `retry_exhausted` and `orchestrator_died` from the
  engine's own record — the last of them where nothing decided the step's fate at all,
  because the process that would have was killed under it. It
  also reads `assertion.outcome == "declined"` from the plan graph, which is the only place
  an unverified step is recorded, and owes it a place in the report's attention section.
- **A step killed outright** leaves no report, so the gate records it `not_reached` — but
  it may have done all of its work. The engine holds that node as `failed` rather than
  `skipped`, with the kill spelled in its error, and the run record reads that node over
  the gate's word: the step is `timed_out`, carrying the bound that fired and how long it
  had run. It carries no assertion verdict, because the gate above turns on the same
  absent report the kill caused and declines the assertion — so nothing asserts over what
  such a step left, and the report says the work is unproven rather than claiming it holds.
  A session the wrapper stops at its own bound is the other shape and does leave a report,
  which is where a divergence over a stopped step comes from ([22 B]). A gate cause the
  gate established on evidence of its own — a failed assertion, a step that did report —
  is never overridden; only the absent-report reading is.

## Measured against Dagu 2.11.0

The routing rests on facts that source reading got wrong, so each is pinned by a test.

| Fact                                                                                                                                                                           | Consequence                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `${steps.<id>.exit_code}` resolves to nothing and fails the precondition                                                                                                       | it is never emitted; the command it names never launches                                                          |
| `${<id>.exit_code}` resolves to the predecessor's exit status                                                                                                                  | this is the reference the gate is handed                                                                          |
| the reference resolves only where the step declares an explicit `id`                                                                                                           | every assertion node carries one                                                                                  |
| a step `id` over 40 characters is refused at load                                                                                                                              | a long step id takes a digest handle instead                                                                      |
| a step cannot combine `run:` with its own `shell:`                                                                                                                             | an assertion runs under the machine's `$SHELL`                                                                    |
| two preconditions on one step are ANDed                                                                                                                                        | —                                                                                                                 |
| a cascade-skipped step evaluates no precondition and leaves no report                                                                                                          | `not_reached` is derivable                                                                                        |
| a node its own precondition skipped lets its dependents run only under `continue_on: {skipped: true}`, and `${<id>.exit_code}` resolves to `0` for it                          | the assertion node carries the flag, and the gate reads the reference only where the assertion's gate says it ran |
| a step excluded with no failed node anywhere reports `Succeeded` with exit 0                                                                                                   | the failed assertion node is what keeps a run honest                                                              |
| a step killed at its bound is a `failed` node whose error reads `step timed out after <elapsed> (timeout: <bound>): context deadline exceeded`, the durations in Go's spelling | the run record derives `timed_out`, the bound and the elapsed time from that sentence                             |
| `DAG_RUN_STEP_NAME` carries a node's `name` even where it declares a different `id`                                                                                            | the assertion's gate writes its account under `verify_<step>`, which is where both readers of it look             |

An assertion runs under the interpreter named by the operator's `$SHELL`, with `pipefail` and
`nounset` off. The last command of a pipeline is the assertion, and an assertion that already
holds before the step runs is not an assertion.

The divergence rate — how often a step reports success over work that is not there — needs
real agent sessions against real repositories, and none have been run. The instrument is
here: every closed gate records both accounts. The number is owed by the worked example.
