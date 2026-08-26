# 19 — Start friction: the three walls between an authored workflow and a running one

The second person to drive Cairn had a plan that parsed, validated, answered its assertions and
priced a run — and still needed three attempts and one spent acceptance to get an engine run
registered. None of the three walls is a bug in what the invariants promise. Each is a **fact
about the engine or the host that Cairn learns too late**: at the gate instead of the derivation,
inside the run instead of before the offer, or never.

**Serves** the capability surface of **Author** and **Run**. No invariant moves: consent stays a
stated price and a qualifying yes ([15](15-the-skill.md)), a verdict stays something a declared
assertion proved ([08](08-verify-gate.md)), and the run's record stays the only source of how it
went ([12](12-run-record.md)).

The plan behind every number here: a seventeen-step strict chain over one repository, derived
from an epic task document and its sub-task documents, authored to `.git/cairn/workflows/` and
started twice. Its emitted definition is 67 nodes — one `lock`, and `work`, `mark`, `commit` for
each of 17 steps with `verify` for the 15 that carry an assertion — and no `setup`, `join` or
`merge`, because every wave holds one step and a one-step wave runs in the repository itself
([07](07-topology.md)).

## What a person hit, in the order they hit it

1. **Generation refused the file at the engine gate, naming a path cut short.** The plan's slug,
   derived from its document's file name, was 112 characters; the engine caps a DAG name under 40. Cairn's own validator had passed the slug, and the refusal that finally came named neither
   the rule nor the length.
2. **`run start` blocked for the whole run, and a killed start cost the acceptance.** The command
   spends the offer, then calls the engine synchronously and prints the run id only when the engine
   returns — for this plan, up to 44 hours later. Started from a tool with a two-minute limit, it
   was killed; the offer was spent, the run id it had minted died with the process, and there was
   nothing for a recovery to name.
3. **The engine could not bind its socket from a sandboxed shell, and nothing said so.** Every
   engine run opens a unix socket. The shell the start was issued from forbade the bind. In a
   scratch engine home the failure is immediate and named; in the default home the same start sat
   silent for two minutes, wrote no status and no log, and was killed by wall 2 before it said
   anything. The authoring gate had passed in that same shell, because `dagu validate` and
   `dagu dry` never bind.

The first is a derivation defect with a one-line fix. The second and third are the same shape:
**a cause the person could have cleared before the offer was spent, surfaced only after it was.**
That is exactly the cost `refuse_unusable_engine` exists to prevent — _"a machine that cannot run
the plan does not cost a person their acceptance"_ — and it currently checks one thing, the
engine's version.

## A — The plan slug: bound it where the engine bounds it

**Today.** `python3 -m cairn plan slug <path>` derives the slug from the document's location —
_"a plan written as one document [is named] by the file"_ — sanitised to `^[a-z0-9][a-z0-9-]*$`
with no length rule, and the validator's `plan_slug` check enforces the grammar alone. The slug
names the worktree parent, the workflow file and the run record, and the workflow file's name is
what the engine reads as the DAG name. Measured against Dagu 2.11.0: a 112-character name is
refused at load with `field 'name': name must be less than 40 characters`, while a 10-character
one loads. Cairn's preflight reports the engine's refusal as `engine_validate: … Validation
failed for <path> — the engine refuses to load the file`, with the path truncated in the message
and the engine's own reason line dropped.

**Why it stayed hidden.** The corpus's fifteen fixture plans are all short-named folders. A
backlog task document — `task-381 - Report-entry-points-for-a-repository-of-vscode's-scale-…md` —
is the ordinary shape of a plan a person already has, and its name is the whole title.

**The change.**

- The slug derivation **bounds its output to the engine's limit**, and the bound is a named
  constant beside the grammar. A file name that opens with a task-shaped id (`task-381 - …`,
  `TASK-381.4 …`) takes the id as the slug, because it is the name every document in that plan
  already uses for it; any other name is cut at the last hyphen before the bound and, where the
  cut would collide in any of the three namespaces `--against` checks, carries a short digest of
  the full name.
- The validator's `plan_slug` error enforces the bound, so a graph that cannot be authored is
  refused at derivation and not at the gate.
- The preflight's `engine_validate` refusal carries the engine's own reason line, whole, and the
  path is never truncated in a refusal. A refusal that hides its cause is a refusal the person
  has to reproduce by hand to read.

**What must not change.** The slug still derives from the documents' own location, so two
plans in one repository cannot adopt each other's worktrees; `--against` still refuses a slug
already present in any of the three namespaces; the person still confirms the slug in the parse
report before anything is generated.

**Touches.** `cairn/plan/cli.py` (`slug`), `cairn/plan/validate.py` (`plan_slug`),
`cairn/workflow/gate.py` (the refusal text), `docs/plan-contract.md` _Identifiers_, a fixture
plan whose document name is longer than the bound, `tests/test_plan_contract.py`.

## B — `run start` returns when the engine has the run, not when the run ends

**Today, exactly.** `_start` in `cairn/skill/cli.py` mints the run id, checks the engine's
version, spends the offer, then calls `trigger.start`, which invokes `dagu start --run-id <id>
--params … <workflow>` through `subprocess.call` and returns its exit status. The four lines a
person needs — `started <run-id>`, the branch, the view, the `cairn report` command — print
after that call returns. For a plan whose slowest chain is bounded at 44 hours, that is up to 44
hours of a blocked terminal, and any caller with its own timeout — an agent harness's tool call
at two minutes by default and ten at most — kills the process tree under it. The `.spent` marker
records one line, `spent at <timestamp>`; the run id lives nowhere but in the dying process.

**What the kill costs.** The acceptance: the offer is claimed before the engine is invoked, which
is correct (a start that really began must consume it), so a killed start is a spent yes and a
fresh offer needs a fresh yes. And the recovery path: `run offer --trigger recovery --recovering
<run-id>` reads the occasion out of that run's record, and a run the engine never registered has
no record and no id anyone can quote. Here the first attempt left exactly the spent marker and
nothing else — not a `runs/<id>/` directory, because `lock_acquire` never ran ([C](#c--the-engine-cannot-bind-its-socket-from-a-sandboxed-shell)).

**The change.**

- **Print the identity before the engine is invoked.** The run id is minted first; `started
<run-id>`, the view and the `cairn report` line are known before `dagu start` is called and are
  printed then. What prints when the engine returns is the engine's exit status, which the record
  already carries.
- **Record the run id and the engine command in the spent marker**, beside the timestamp, so a
  start that died has a name a recovery can quote — and so `cairn report` can find the run the
  offer bought without the person having kept the terminal.
- **Detach by default.** `trigger.start` launches the engine in its own session with its stdout
  and stderr routed to `runs/<id>/engine.log`, waits only until the engine has taken the run on —
  the run's status data exists, or the engine exited without it — and returns. The release
  handler writes the run's record whether anyone is waiting or not
  ([12](12-run-record.md)), so nothing is lost by not waiting. A `--wait` flag keeps the current
  behaviour for a caller that wants the exit status in-line, and it says so in its own output.
- `capabilities/running.md` step 7 already reads _"the command prints where the run can be
  watched"_ — the text describes the detached shape, and the code should match it.

**What must not change.** The offer is still spent before the engine is invoked; an engine that
exits without taking the run on is still a refusal that names the command, and it still leaves
the acceptance spent, because a run that was handed to the engine is a run; recovery is still an
ordinary start carrying the occasion it continues, and never `dagu retry`.

**Touches.** `cairn/skill/trigger.py` (`start`, `Started`), `cairn/skill/cli.py` (`_start` print
order), `cairn/skill/consent.py` (`spend` and the marker's contents, `read_offer`),
`capabilities/running.md` steps 6–7, `docs/triggers.md` _Who started a run_,
`tests/test_the_skill.py`.

## C — The engine cannot bind its socket from a sandboxed shell

**Today, exactly.** Every `dagu start` opens a unix socket for the run —
`/tmp/@dagu__<home>_<dag>_<hash>.sock` — before any step runs. A shell that may not `bind` a
unix socket gets, in a scratch engine home, an immediate
`failed to start the unix socket server: listen unix …: bind: operation not permitted`. In the
default home the same start was observed to sit for two minutes with no status data and no log
before it was killed; whether it was retrying the bind or blocked elsewhere is not known, and
only the scratch-home form was reproduced in the open. `refuse_unusable_engine` runs before the
offer is spent and checks `assert_pinned()` — the engine's version — and nothing else. The
authoring gate runs `dagu validate` and `dagu dry` in that same shell and both pass, because
neither binds a socket, so a workflow authors cleanly in an environment that cannot run it.

**Who this hits.** Anyone driving Cairn through a coding-agent harness, which is the audience the
skill is written for. Such harnesses sandbox their shell by default and the sandbox's rules are
the harness's; the person may not know a socket is involved at all. The second attempt here ran
from a shell with the sandbox lifted for that one command and the engine took the run on within a
second.

**The change.**

- **`refuse_unusable_engine` rehearses a run.** Beside the version pin, it starts a one-step DAG
  in a scratch engine home — the same scratch the preflight's gate rehearsal already builds
  ([11](11-emitter-and-preflight.md)) — and refuses with the engine's own error line if the
  engine cannot take that run on. The check is cheap (measured: one second, `Result: Succeeded`)
  and it runs before the offer is spent, which is the whole point.
- **The offer names the host requirement.** One line in the price: the engine opens a unix socket
  per run and the shell that starts it must be allowed to bind one — so a sandboxed harness says
  so before the yes, not after.
- **The skill documents the symptom.** `capabilities/running.md` gains the two spellings — the
  immediate refusal and the silent wait — and what clears them.

**What must not change.** The rehearsal is against a scratch home, never the machine's own —
the same rule the gate already keeps, because an engine home the binary has never seen is
created carrying a retry policy that re-executes paid work ([09](09-supervision.md)). A refusal
here still leaves the acceptance standing.

**Touches.** `cairn/skill/trigger.py` (`refuse_unusable_engine`), `cairn/workflow/gate.py` (the
scratch-home builder, shared), `cairn/skill/consent.py` (`disclosure`), `capabilities/running.md`,
`tests/test_the_skill.py`, `tests/test_engine_supervision.py`.

## D — A headless session that defers its own completion leaves no report

**What happened, exactly.** Step 2 of the run, `work_task_381_10`: 77 turns, 16m37s, $10.89
notional. The session fetched the corpus, made the edit its task asks for — `SELF_KEYWORDS` as a
module-scoped `Map` — and then did what the interactive harness teaches: it launched five
background `Bash` jobs (the corpus fetch, a pre-fix full-corpus probe, the whole core suite),
armed three `Monitor`s, called `ScheduleWakeup` for 1,200 s, and ended its turn with _"Both watch
monitors are armed. I'll resume when the probe or suite completes."_ Under `claude -p` nothing
re-invokes a session. The CLI returned its `result` with `stop_reason: tool_use`, `result: ""`
and no `structured_output`; `_translate_result` raised `provider_protocol: structured_output is
not an object` and Cairn wrote a `failed` report. The assertion then ran and **passed**
(`verify_exit 0` — the Map is there, the suite and typecheck are green), so the gate closed on
`reported_failure` with a recorded divergence, _"the step reported 'failed' over an assertion that
passed"_; `mark` and `commit` skipped; the chain halted; the fifteen steps behind it were recorded
`gate_indeterminate`. The work sits in the repository, uncommitted and verified.

**Why.** The preamble says _"Report through the structured output you are constrained to"_ and
nothing about the session being one shot. The harness offers background tasks, monitors and a
wakeup scheduler whose contract — _the harness re-invokes you_ — is true interactively and false
under `-p`, and the model kept that contract. Nothing in Cairn denies those tools or names the
difference.

**The change.**

- **The preamble states the shape of the session.** _This session is one shot: nothing re-invokes
  you. Do not background work, arm monitors or schedule wakeups. Run what you must wait for in
  the foreground, and end only by reporting._
- **The provider denies what it can.** `run_claude` passes `--disallowedTools ScheduleWakeup` and
  `Monitor` by default; the plan's `tools` list adds to that, never replaces it. `Bash`'s
  `run_in_background` cannot be denied by name, so the preamble carries that one.
- **Rescue before discarding.** A result with `stop_reason: tool_use` and no `structured_output`
  is a session that ended a turn without reporting, not a session that failed. Resume it once —
  `claude -p --resume <session_id>`, the spelling `resume_command` already knows — with one
  message: _the session is ending; report now through the structured output_. One resume, bounded
  by the step's remaining timeout, recorded in the report's `detail` as `resumed_for_report`.
  Measured here, the alternative was discarding $10.89 of work an assertion had just proved.
- **The cause is named for what it is.** `provider_protocol` is not `reported_failure`: the step
  reported nothing. The gate carries the protocol cause through to the mark report and the
  record, and the divergence line stops saying the step reported failure.

**What must not change.** The gate still closes on a missing or unreadable report — a step that
did not say what it did is not recorded as done — and the assertion still never opens it alone.

**Touches.** `cairn/protocol.py` (the preamble), `cairn/providers.py` (`run_claude` deny
defaults, the one-resume rescue in `run_provider`), `cairn/verify.py` (the cause carried through),
`cairn/report/` (the divergence phrasing), `docs/step-protocol.md` _The preamble_,
`docs/verify-gate.md` _Why a step contributed no verified work_, `tests/test_step_protocol.py`.

## What worked, for contrast

The ceiling refusal. The first authoring attempt carried 4-hour and 6-hour step bounds that the
plan itself never stated, and the preflight refused with _"a worst-case duration of 96.3 hours
along its slowest chain, over the 48-hour ceiling … shorten a wait, lower a timeout, or split the
plan"_. It named the number, the rule and the three fixes; the graph was rebalanced and
re-authored in one turn. That is the shape every refusal in this document should have had.

## The measurement this owes

The run this document was written beside — `20260825T132605Z-26c99fcf`, seventeen steps,
`--run-timeout 158400` — is the first Cairn run started detached from a harness, by hand with
`nohup` from an unsandboxed shell. Three things it will answer that the design asserts: whether a
`run start` process reparented to launchd survives the session that launched it; whether the
four `started` lines ever reach a log nobody is reading; and what the record says when a step
overruns a 90- or 150-minute bound that was set to fit the ceiling rather than measured. Record
the answers here beside the run id.

**Recorded, 2026-08-25 15:44:43.** The detached `run start` survived its session: the run ended
under it 1h18m after launch, the release ran and gave the repository back, and the four `started`
lines have still not printed because the parent process is what blocks. Verdict
`green_with_exclusions`, exit 3, engine status `partially_succeeded`: step 1 (`task_381_2`) landed
as `89583ced` after a 47-minute session and a 2m05s assertion; step 2 (`task_381_10`) is
[D](#d--a-headless-session-that-defers-its-own-completion-leaves-no-report); the other fifteen
never ran. Notional cost $41.58 over 190 turns for the two priced steps. No bound was reached, so
the overrun question is still open.

## The bucket

Small start-path defects, appended as they surface, with how they were found.

| #   | Symptom                                                                                                                                                                                                                 | Where it lives                                                           | State |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ----- |
| A   | A plan slug longer than the engine's 40-character name limit passes the validator and dies at the gate, with the cause cut out of the message                                                                           | `cairn/plan/cli.py`, `cairn/plan/validate.py`, `cairn/workflow/gate.py`  | open  |
| B   | `run start` blocks for the whole run; a killed start spends the offer and loses the run id                                                                                                                              | `cairn/skill/trigger.py`, `cairn/skill/cli.py`, `cairn/skill/consent.py` | open  |
| C   | The engine cannot bind its run socket from a sandboxed shell; the version pin is the only pre-spend engine check                                                                                                        | `cairn/skill/trigger.py`                                                 | open  |
| D   | `plan propose --json` exits nonzero when steps are unanswered, so a caller cannot tell a listing from a failure by exit status                                                                                          | `cairn/plan/cli.py`                                                      | open  |
| E   | The offer prices worktrees and merges for a chain-shaped plan whose definition has neither; the disclosure is a fixed sentence, not the topology                                                                        | `cairn/skill/consent.py` (`disclosure`)                                  | open  |
| F   | A `-p` session that backgrounds work and schedules a wakeup ends without a structured report; $10.89 of assertion-passing work is discarded                                                                             | `cairn/protocol.py`, `cairn/providers.py`                                | open  |
| G   | A `provider_protocol` failure reaches the gate and the report as `reported_failure`, and the divergence says the step "reported failed"                                                                                 | `cairn/verify.py`, `cairn/report/`                                       | open  |
| H   | A chain-segment step that fails after editing leaves its edits uncommitted in the repository; a recovery's first act refuses the dirty tree, and the report's next action says `settle_merge` for a chain with no merge | `cairn/record/`, `capabilities/running.md`                               | open  |
| I   | Fifteen never-reached steps are recorded `gate_indeterminate` and listed as needing a person, where [08](08-verify-gate.md) says `not_reached`                                                                          | `cairn/verify.py`                                                        | open  |
| J   | The release writes `record.json` with verdict `running`, `finished_at: None` and the engine's status `running` for a run that has ended; `cairn report` rebuilds it as `green_with_exclusions`                          | `cairn/record/`, the `handler_on.exit` body                              | open  |

## Acceptance

- A plan whose document name exceeds the engine's bound derives a slug under it, confirmed in the
  parse report, and reaches a generated workflow without touching the gate.
- `run start` prints the run id, the view and the report command before the engine is invoked,
  returns once the engine has the run, and leaves both in the spent marker.
- A start issued from a shell that cannot bind a unix socket is refused before the offer is spent,
  with the engine's own reason, and the yes still stands.
- The offer's price is composed from the definition's topology, so a chain prices no worktrees and
  no merges.
- The measurement above is recorded against `20260825T132605Z-26c99fcf`.
