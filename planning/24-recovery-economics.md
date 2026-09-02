# 24 — What a recovery re-pays, and what a spent offer bought

Found live across five runs of one seventeen-step chain-shaped plan — the task-381 dogfood that also produced [21](21-commit-scope.md), [22](22-timed-out-step.md) and [23](23-reading-a-broken-run.md). The recovery story held every time: markers no-opped every finished step's work, and nothing was ever done twice by an agent. What was done twice — and four times, and fourteen times — was everything else.

**Serves** the capability surface of **Run**. The invariants stand: a verdict stays something a declared assertion proved on the current tree, and a step nobody asserted still never records. What moves is only how many times one proof is paid for.

## A — Fourteen byte-identical assertions are proven fourteen times per run

**What happened.** Nearly every step of the plan carries the same accepted assertion suffix: `cd packages/core && npx tsc --noEmit && npx vitest run` — a typecheck and a 4,360-test suite, ~2.2 minutes. On a recovery, every finished step's work no-ops in milliseconds and its assertion runs in full, so each recovery spends ~30 minutes proving the same command against the same tree fourteen times before any new work starts. Four recoveries paid it four times. The person driving asked, verbatim: _"why is it running verification steps on tasks that were completed A LONG TIME AGO????"_

**Why each proof is right on its own.** The tree has changed since each marker was written — later steps landed on it — so re-asserting an earlier step's end state on today's tree is the guarantee, not waste. What is waste is proving one command's exit status more than once per tree state.

**The change.** Within one run, the verify layer proves each distinct assertion **command** once and lets every gate that quotes that exact command read the one result. The key is the command's bytes; two steps with different commands share nothing. The record still shows every step's assertion with its exit status — provenance says which execution backed it. Nothing changes across runs: the next run proves everything again, because the tree moved.

**What must not change.** A step-specific assertion (the `grep`-shaped clauses) still runs per step; only byte-identical commands coalesce. A shared result that failed closes every gate that would have read it — sharing never widens what passes.

**Touches.** `cairn/verify.py`, `cairn/emitters.py` (the verify node body or a result cache keyed on command bytes under the run directory), `docs/verify-gate.md`, `tests/test_verify_gate.py`.

## B — After a gate closes, the run spends half an hour proving nothing

**What happened.** One failed assertion at the chain's second step skipped every downstream work node — and every downstream **verify** still ran, thirteen more full-suite executions against a tree the missing work never touched, ~30 minutes of CPU whose results no gate could use: each downstream gate closed as `not_reached` regardless, because its work step left no report. The run's last half hour existed to decorate a verdict that was already decided.

**Today.** The verify node runs under `continue_on: {failure: true, skipped: true}` so that a _cascade-skipped_ work step's assertion can still pass and be distinguished from work never attempted ([verify-gate.md](../docs/verify-gate.md)) — but the gate reads the absent work report and refuses either way, so on the chain-broken path the assertion's result is unreadable by construction.

**The change.** A verify whose work node the engine skipped for an upstream cause — not a marker no-op, which must keep its assertion — is skipped with it, and its gate records `not_reached` directly. The distinction the current design protects is preserved by reading the _cause_ of the work skip: a marker no-op keeps its verify; an upstream-skip drops it.

**What must not change.** A no-op step's assertion always runs — that is the recovery guarantee. A verify already running when the upstream fault lands finishes and is recorded.

**Touches.** `cairn/emitters.py`, `cairn/topology.py`, `cairn/verify.py`, `docs/verify-gate.md`, `docs/step-protocol.md`.

## C — One flaky test closes a gate, twice

**What happened.** Two consecutive recoveries died on a single test exceeding vitest's 5,000 ms default under load — a different test each time, in a suite of 4,360 — each one read by the gate as a failed assertion, each cascading fifteen steps. The repair belonged to the repository and was made there (an explicit `testTimeout` stating the machine's real bound). What belongs here is the observation: an assertion that runs a whole test suite makes the gate's false-negative rate the suite's flake rate times fourteen executions per recovery.

**The open question, recorded rather than decided.** Verify nodes are emitted with `retries: 0` like everything else, on the plan contract's reasoning that arbitrary shell is not idempotent and a paid session must not be paid twice. Neither reason applies to an assertion: it is read-only by contract and costs no session. Whether a verify may retry once — turning a transient false negative into a second read instead of a fifteen-step cascade — is a plan-contract question ([plan-contract.md](../docs/plan-contract.md)'s `retries` paragraph), and the counterargument is real: a flaky assertion that passes on retry has asserted less, and the honest fix is the plan stating an assertion that does not flake. Recorded for the contract's owner to decide.

## D — A dirty working tree costs the acceptance, though it was checkable before the offer was spent

**What happened.** A `run start` was accepted while one uncommitted file sat in the working tree — an edit made minutes earlier and not yet committed. The run's first act, `lock_acquire`, refused with `repository_dirty` — _"uncommitted work … which a step's commit would sweep up as its own output"_ — correctly, given [21](21-commit-scope.md). But the refusal came **inside** the run, so it consumed the offer and the acceptance, and continuing cost a fresh offer and a fresh yes.

**Today.** [19 §C](19-start-friction.md) built exactly the right machinery for exactly this shape: `run start` rehearses a one-step run in a scratch engine home _before the offer is spent_, because _"a machine that cannot run the plan does not cost a person their acceptance."_ The rehearsal checks the socket and the engine; it does not read `git status`. A dirty tree is knowable in the same breath, from the same repository path the start already holds.

**The change.** The start's preflight reads the working tree before claiming the offer and refuses — on the before-the-offer-is-spent list, leaving the yes standing — naming the dirty paths. The in-run check stays: the tree can dirty itself between preflight and `lock_acquire`, and the run's own refusal is the backstop, not the interface.

**What must not change.** The preflight refuses only what `lock_acquire` would refuse; it never stashes, commits, or otherwise touches the tree.

**Touches.** `cairn/skill/` (the start path's preflight), `capabilities/running.md` (the refusal lists), `tests/test_the_skill.py`.

## Acceptance

- A recovery of an n-step chain whose steps share one assertion command executes that command once, and every gate that quotes it reads the shared result; the record names which execution backed each step.
- A run whose chain breaks at step k runs no assertion for steps after k that were skipped for the upstream cause, and its wall clock past the fault is seconds, not minutes; a marker no-op's assertion still runs.
- `run start` over a dirty tree refuses before the offer is spent, names the paths, and the same acceptance starts the run once the tree is clean.
- The verify-retry question is answered in [plan-contract.md](../docs/plan-contract.md) one way or the other, with the reasoning recorded beside `retries`.
