# 24 — What a recovery repeats

Found live across five runs of one seventeen-step chain-shaped plan — the task-381 dogfood that also produced [21](21-commit-scope.md), [22](22-timed-out-step.md) and [23](23-reading-a-broken-run.md). The recovery story held every time: markers no-opped every finished step's work, and nothing was ever done twice by an agent. What was done twice — and four times, and fourteen times — was everything else.

**Serves** the capability surface of **Run**. The invariants stand: a verdict stays something a declared assertion proved on the current tree, and a step nobody asserted still never records. What moves is only how many times one proof is run.

## A — Fourteen byte-identical assertions are proven fourteen times per run

**What happened.** Nearly every step of the plan carries the same accepted assertion suffix: `cd packages/core && npx tsc --noEmit && npx vitest run` — a typecheck and a 4,360-test suite, ~2.2 minutes. On a recovery, every finished step's work no-ops in milliseconds and its assertion runs in full, so each recovery takes ~30 minutes proving the same command against the same tree fourteen times before any new work starts. Four recoveries ran it four times. The person driving asked, verbatim: _"why is it running verification steps on tasks that were completed A LONG TIME AGO????"_

**Why each proof is right on its own.** The tree has changed since each marker was written — later steps landed on it — so re-asserting an earlier step's end state on today's tree is the guarantee, not waste. What is waste is proving one command's exit status more than once per tree state.

**The change.** Within one run, the verify layer proves each distinct assertion **command
against one tree state** once and lets every gate quoting that command and tree read the result.
The key is `(command bytes, HEAD, dirty-state digest)`: a pure recovery shares the proof, while
any landed commit or dirty-tree change invalidates it. Two steps with different commands or tree
states share nothing. The record still shows every step's assertion with its exit status —
provenance says which execution backed it. Nothing changes across runs.

**What must not change.** A step-specific assertion (the `grep`-shaped clauses) still runs per step; only byte-identical commands coalesce. A shared result that failed closes every gate that would have read it — sharing never widens what passes.

### Publishing a shared proof is a concurrent decision

Two assertion processes quoting one command both observe no standing proof, so an unlocked
read/check/write lets whichever finishes last decide what every later gate reads — a pass
replacing a failure, which is sharing widening what passes. Publication is an interprocess
critical section keyed by the proof path:

- lock, then re-read the standing result before deciding what to write;
- failure is dominant for one command/tree key and can never be replaced by success;
- readers never observe a partial file;
- a crashed writer leaves a reclaimable lock and no invented result;
- the multiprocess test uses a barrier so both writers reach the decision concurrently.

Shared proof caching is a safety property before it is a performance one. A sequential test
cannot exercise the race, so the concurrent one is what the sharing stands on.

**Touches.** `cairn/assertions.py` (the assertion's own precondition and its proof), `cairn/verify.py`, `cairn/emitters.py`, `cairn/locks.py`, `cairn/layout.py`, `docs/verify-gate.md`, `tests/test_verify_gate.py`.

## B — After a gate closes, the run takes half an hour proving nothing

**What happened.** One failed assertion at the chain's second step skipped every downstream work node — and every downstream **verify** still ran, thirteen more full-suite executions against a tree the missing work never touched, ~30 minutes of CPU whose results no gate could use: each downstream gate closed as `not_reached` regardless, because its work step left no report. The run's last half hour existed to decorate a verdict that was already decided.

**Today.** The verify node runs under `continue_on: {failure: true, skipped: true}` so that a _cascade-skipped_ work step's assertion can still pass and be distinguished from work never attempted ([verify-gate.md](../docs/verify-gate.md)) — but the gate reads the absent work report and refuses either way, so on the chain-broken path the assertion's result is unreadable by construction.

**The change.** A verify whose work node the engine skipped for an upstream cause — not a marker no-op, which must keep its assertion — is skipped with it, and its gate records `not_reached` directly. The distinction the current design protects is preserved by reading the _cause_ of the work skip: a marker no-op keeps its verify; an upstream-skip drops it.

**What must not change.** A no-op step's assertion always runs — that is the recovery guarantee. A verify already running when the upstream fault lands finishes and is recorded.

**Touches.** `cairn/emitters.py`, `cairn/topology.py`, `cairn/verify.py`, `docs/verify-gate.md`, `docs/step-protocol.md`.

## C — One flaky test closes a gate, twice

**What happened.** Two consecutive recoveries died on a single test exceeding vitest's 5,000 ms default under load — a different test each time, in a suite of 4,360 — each one read by the gate as a failed assertion, each cascading fifteen steps. The repair belonged to the repository and was made there (an explicit `testTimeout` stating the machine's real bound). What belongs here is the observation: an assertion that runs a whole test suite makes the gate's false-negative rate the suite's flake rate times fourteen executions per recovery.

**The open question, recorded rather than decided.** Verify nodes are emitted with `retries: 0` like everything else, on the plan contract's reasoning that arbitrary shell is not idempotent and a session must not run twice. Neither reason applies to an assertion: it is read-only by contract and opens no session. Whether a verify may retry once — turning a transient false negative into a second read instead of a fifteen-step cascade — is a plan-contract question ([plan-contract.md](../docs/plan-contract.md)'s `retries` paragraph), and the counterargument is real: a flaky assertion that passes on retry has asserted less, and the honest fix is the plan stating an assertion that does not flake. Recorded for the contract's owner to decide.

## Acceptance

- A recovery of an n-step chain whose steps share one assertion command and tree state executes
  that command once, and every gate that quotes it reads the shared result; the record names
  which execution backed each step.
- Concurrent passing and failing executions for one proof key always leave failure standing,
  regardless of write order or process timing.
- A run whose chain breaks at step k runs no assertion for steps after k that were skipped for the upstream cause, and its wall clock past the fault is seconds, not minutes; a marker no-op's assertion still runs.
- The verify-retry question is answered in [plan-contract.md](../docs/plan-contract.md) one way or the other, with the reasoning recorded beside `retries`.

## Implementation Notes

**Status: done.** Sections A, B and C are built.

A recovery of the seventeen-step chain waits on one execution of its shared assertion
command instead of fourteen, and a run whose chain breaks stops using wall clock the
moment the verdict is decided. Nothing about what a verdict means moved: every step's record
still names an assertion and its exit status, and a step nobody asserted still never records.

- **A.** A step's assertion is fronted by its own precondition, `cairn verify needed`, which
  declines an assertion whose exact command has already been proven in this run against
  exactly this tree. The key is the command's bytes plus the commit the tree stands on plus
  every dirty path's content, with `.steps/` and the runs root left out because Cairn writes
  to both on every step ([cairn/assertions.py](../cairn/assertions.py)). The mark gate
  completes the assertion node's report with the exit it read and files that exit as the
  proof later gates share, so the record says for every step whether its verdict was
  `executed` or `shared` and which step's execution backed it. Publication happens inside an
  advisory lock on the proof's own key, re-reads the standing result there, and never
  replaces a failure with a pass; `exclusive_lock`
  ([cairn/locks.py](../cairn/locks.py)) is the one implementation of that lock, shared with
  the git write mutex, and the kernel drops it when its holder dies. A key that cannot be
  taken publishes nothing, so the fallback is a spared execution rather than a weaker proof.
  `PublishingAProofIsOneDecision` releases three and four writers through a file barrier in
  separate processes and asserts the failure stands whichever of them arrives last.
- **B.** The same precondition declines an assertion whose work node left no report of this
  run, which is what an upstream halt leaves behind: the gate would close `not_reached`
  regardless, so the assertion is skipped with the work and the run's wall clock past a
  fault is seconds. A marker no-op leaves a `noop` report and keeps its assertion, which is
  the recovery guarantee. The mark gate reads the precondition's recorded decision rather
  than the engine's exit-status reference, because `${<id>.exit_code}` reads `0` for a node
  its precondition skipped and a gate trusting it would record a marker over an assertion
  that never ran.
- **C.** Answered in [plan-contract.md](../docs/plan-contract.md) beside `retries`: an
  assertion never retries, whatever the step's own value. The engine records no per-node
  retry count, so a retried pass would read exactly like a first-try pass in the run record,
  and an assertion that passes on its second asking has asserted less while looking like
  more. The remedy for one that flakes is the plan stating an assertion that does not, and
  what leaving one in does is stated there: one proof is shared by every gate quoting a
  command, so one flaky execution closes every one of them.
