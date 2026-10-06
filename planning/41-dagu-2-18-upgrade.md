# 41 — Moving the engine pin from Dagu 2.11.0 to 2.18.2

**North star ([principles 1–4](../PRINCIPLES.md)).** The engine is what runs a person's planned work
while they are away. A pin move is worth making only if nothing a person relies on changes under
them, and if the new engine gives a better answer to "what is it doing" and "what does it need
from me".

**Status: research. Nothing was changed, upgraded or started on the machine.** The live run on
2.11.0 was untouched (checked after the last probe: its `dagu start` and agent session were still
running). Measurements used a checksum-verified 2.18.2 binary (darwin_amd64, sha256 matched the
release's `checksums.txt`) under a throwaway `HOME` and `DAGU_HOME`. `/usr/local/bin/dagu`,
`~/Library/Application Support/dagu` and `.git/cairn` were not used; a timestamp check before and
after showed no write to the first two.

**Serves** Run, Report and Schedule. No invariant moves.

**Evidence tags.** **[R]** read from release notes of the named tag. **[S]** read from the schema,
API spec or Go source at a tag. **[M]** measured on 2.18.2 in this session. The 2.11.0 side of
every comparison is Cairn's own "Measured against Dagu 2.11.0" comment, which I did not re-run.
There is no v2.17.1 release; the series is v2.11.1 … v2.17.0, v2.17.2, v2.18.0 … v2.18.2.

## Verdict

**The pin can move to 2.18.2.** Every engine behaviour Cairn depends on that I could probe
behaved as its comment says on 2.11.0. I found no break. Three things must change first, and one
thing must not happen yet:

1. **Do not move it while the 2.11.0 run is live.** The gate halts on any other version
   (`cairn/workflow/gate.py`), and the live run's remaining steps run Cairn code that asks the
   engine on `PATH` where its files are (`cairn/enginehome.py`). Replacing that binary, or the pin,
   mid-run is exactly the unmeasured case. I did not test it.
2. **Move the pin as one change**: `ENGINE_VERSION` in `cairn/workflow/schema.py`, the
   `cairn_engine` label in `fixtures/workflows/*.yaml` (regenerate with `scripts/regenerate_workflows`),
   `tests/test_workflow.py:1250`, the `engine` field of every `fixtures/runs/*/recording.json`
   (re-record with `scripts/record_runs.py`), and the pin statements in the docs. No shim for the
   old version, per the constitution.
3. **Fix four statements that are now stale** (below, B2–B4, B6) so the comments say what 2.18.2 does.
4. **Take the three hands-on measurements in U1–U3 before relying on the new engine for scheduled or
   overnight work.** None blocks a manual `dagu start`.

## What a person would see or do differently

- **Nothing they do today changes.** `dagu start`, `--run-id`, `--params`, `--dagu-home`, `dagu
config`, `dagu validate`, `dagu dry`, the run record Cairn reads, and the view at
  `/dag-runs/<name>/<run-id>` all behave as before [M].
- **A person can be asked and can answer inside a run** with materially better engine support than
  2.11.0: push-back and rewind, run artifacts attached to a task, independent branches that keep
  running while a task waits [R 2.17.0, 2.17.2], and approvals that survive a restart [R 2.18.0].
  This is the biggest gain (O1).
- **Failure notifications can say which steps failed** and link to the run [R 2.11.4, 2.12.0,
  2.18.0] (O2).
- **A scheduler can be paused as a whole** from the API [R 2.16.5] (O3).

## Breaking or moved behaviours

None of these breaks a measured dependency. Each is a place where Cairn's text or a guard is now
inaccurate, or where the engine's own surface moved.

**B1. Version stamp (certain, mechanical).** Everything in "Verdict" item 2. `dagu version` prints
`2.18.2` [M], so the gate's exact comparison works once the constant moves.

**B2. `max_active_steps` now documents "non-positive means unlimited" and a way to clear a
base value.** [R 2.17.0 #2777] [S dag.schema.json: "Non-positive values mean unlimited; use -1 to
override a positive value inherited from base.yaml"]. **Measured:** a DAG file with
`max_active_steps: 0` beside a base of 10 still ran 12 three-second steps in 6 s (ten wide), the
same as an omitted field; `12` ran them in 4 s [M]. So `step_concurrency` in `cairn/workflow/build.py`
(emit the node count, never zero) is still right and its measured claim still holds. What moved:
`-1` is now a real override, and `0` inside a _base_ file is documented as unlimited. Update the
docstring to say both. _Depends on it:_ `cairn/workflow/build.py:57`.

**B3. A fresh `base.yaml` no longer arms DAG retry.** [M] A new home's generated `base.yaml` has
`retry_policy` commented out ("Retries are opt-in because workflows may have non-idempotent side
effects") and still carries `catchup_window: "6h"`. On this machine's 2.11.0-era file both are
already defused by hand. _Depends on it:_ `cairn/baseconfig.py` (`assert_dag_retry_disabled`,
`assert_catchup_disabled`). The catch-up guard stays necessary. The retry guard's stated reason
("a file that invoking the engine creates, carrying `retry_policy: {limit: 3}` active"; also
`cairn/enginehome.py:20-26`) is no longer true of a fresh file, but a file someone edits to arm it
is still refused, so keep the guard and correct the comment. Also noted: invoking the engine on a
fresh home still creates `base.yaml` and example DAGs [M], and still prints the
`No auth.mode configured` warning to stderr on every command [M], which `enginehome.py` already
parses around.

**B4. `dagu validate` now refuses `mark_success`.** [M] exit 1, `'spec.step' has invalid keys:
mark_success`. Cairn's `cairn/workflow/preflight.py` docstring and `docs/workflow.md:8` say
validate exits 0 on it. The other holes are unchanged: a dependency cycle, an unresolved `${NOPE}`,
and a step with no timeout all still exit 0 [M], so the preflight stays first-class. Keep the
`mark_success` rule (it is cheap and covers older/other engines' behaviour) but stop citing it as a
hole. 2.11.1 added `validate --show-unresolved` and a "Needs a fix" classification [R]; on
`${NOPE}` it printed nothing and exited 0 [M], so it does not close the unresolved-reference hole.

**B5. The run record's last snapshot dropped three keys and gained one.** [M] `autoRetryInterval`,
`autoRetryMaxInterval`, `suspendFlagName` are gone; `definitionId` is new. Cairn reads none of
the three (grep over `cairn/`: only `pidStartedAt` is read, in `cairn/supervise.py:128`, and it is
still present [M]). Node and trigger integer encodings are unchanged [S internal/core/status.go at
v2.11.0 vs internal/ir/status.go at v2.18.2: identical enums]. The attempt directory layout is
`data/dag-runs/<dag>/dag-runs/YYYY/MM/DD/dag-run_<ts>_<id>/<attempt>/status.jsonl` [M]; I did not
read the 2.11.0 layout, so I cannot say it moved, but `find_attempts` uses `rglob("status.jsonl")`
and keys on the `dagRunId` inside the record, so it does not care. [R 2.14.0 #2564] separates
per-run work directories from history; `dagu config` now also prints `DAG-run work directory`
(`data/dag-run-work`) and `Wiki directory` [M]. The two labels Cairn requires, `DAGs directory`
and `DAG runs`, are still printed [M].

**B6. Status vocabulary gap that is not new but now matters.** Both versions define run statuses
`7 waiting` and `8 rejected` and node statuses `6 partially_succeeded`, `7 waiting`, `8 rejected`,
`9 retrying` [S, identical at both tags]. `cairn/record/engine.py` maps none of 7, 8, 9 and raises
on them by design. Today that cannot fire because Cairn emits no waiting step. The moment O1 is
adopted, a run waiting on a person would make the record builder raise. That is a prerequisite of
O1, not of the upgrade.

**B7. A timed-out step resolves `${id.exit_code}` to `124`** [M]; a SIGTERM'd and a SIGKILL'd step
both resolve to `-1` with errors `signal: terminated` / `signal: killed` [M]. `cairn/assertions.py:129`
documents only the `-1`; add the `124` case beside it so a timeout is not read as an assertion
that exited 124 on its own. I do not know whether 2.11.0 gave `124`.

### Behaviours re-checked and unchanged on 2.18.2 [M]

| Cairn dependency (code)                                                                                                                                                            | Result                                                                                                                                                                                                                  |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Step timeout message `step timed out after 2.001s (timeout: 2s): context deadline exceeded`, node is plain `failed` (`record/engine.py:110`)                                       | same text; regex still matches (`2s` form for exact whole seconds)                                                                                                                                                      |
| `handler_on.exit` exiting nonzero fails the run and `dagu start` exits 1 even if every step passed (`__main__.py:280`)                                                             | same: run `2`, start exit 1, steps `4`, handler node `2 exit status 7`                                                                                                                                                  |
| What the handler sees of its own record: final run status and final step nodes, no `finishedAt`, handler node not final (`__main__.py:280`)                                        | same; the handler node reads `1` (running); the file is compacted to one line afterward                                                                                                                                 |
| `env:` reaches steps and the exit handler (`layout.py:26`, `build.py:157`)                                                                                                         | same                                                                                                                                                                                                                    |
| `${<id>.exit_code}` is `0` for a precondition-skipped node, and a step is only reachable with an explicit `id` (`verify.py:221-236`, `assertions.py:38`)                           | skipped node resolved to `0`, downstream ran                                                                                                                                                                            |
| `--params "a=b c=d"` splits on whitespace (`skill/trigger.py:201`)                                                                                                                 | same: `A=foo B=bar baz` gave `A=foo B=bar 3=baz`                                                                                                                                                                        |
| Name limit: 40 loads, 41 refused (`plan/schema.py:242`)                                                                                                                            | same text, `name must be less than 40 characters`                                                                                                                                                                       |
| `catchup_window: ""` passes; `"0s"` and `"0"` refused (`workflow/schema.py:184`)                                                                                                   | same                                                                                                                                                                                                                    |
| Parameter positions: double-quoted executes the value, single-quoted is inert, env var is safe (`workflow/schema.py:23`, `parameters.py:68`)                                       | same: `$(touch …)` in a double-quoted body ran; `'${P}'` stayed literal; `"$P"` was safe. (My bare and space cases were confounded by whitespace splitting of `--params`, so the "bare splits" row is not re-measured.) |
| A missing `working_dir` is created (`parameters.py:29`)                                                                                                                            | same                                                                                                                                                                                                                    |
| Default bind `127.0.0.1:8080`; builtin auth with first-visit setup; `/api/v1/*` returns 401 unauthenticated; `/dag-runs/<name>/<id>` serves the page (`layout.py:127`)             | same (`setupRequired=true`; page 200, API 401, `/api/v1/health` 200)                                                                                                                                                    |
| `kill -9` of `dagu start` leaves the record `running` forever with no `finishedAt`; `dagu status` still says Running; `dagu retry` refuses with `already running` (`supervise.py`) | same, and `dagu ps` still lists the dead run as `FRESH yes` after a few seconds. The reconciler is still needed                                                                                                         |
| The gate's three engine calls: `validate`, `dry`, rehearsal `start`, all with `--dagu-home` (`workflow/gate.py`)                                                                   | all six `fixtures/workflows/*.yaml` validate and dry-run clean; the one-step rehearsal succeeds in 0.27 s                                                                                                               |

Not re-measured: the `continue_on: {skipped: true}`-on-the-marker claim (`emitters.py:376`), where
my variant placed the flag on the downstream step and so did not reproduce Cairn's shape. And the
claim that a step gets a curated environment so the caller's `PYTHONPATH` does not survive
(`build.py:150`): not tested.

## Opportunities

Ordered by value to the principles. Effort is Cairn work, not engine work.

**O1. Human tasks with push-back, rewind and artifacts — principle 3.** `human.task` exists in
2.11.0 (planning [35](35-human-in-the-loop-steps.md) already designs on it). Added since:
push-back that rewinds to a named earlier step with typed feedback (`DAG_PUSHBACK`) [R 2.17.2 #2840, S schema `humanTaskPushBackConfig`]; run artifacts attached to a task as review context [R
2.17.0 #2824]; **independent branches resume while a task waits** [R 2.17.2 #2838] — without
this a task in one wave would stall every sibling; approvals recovered and direct resumes
preserved [R 2.18.0 #2901]; new API `…/resume` and `…/human-tasks/{stepId}/push-back` [S api.yaml]. Limit
(unchanged): human tasks are root-DAG only and cannot be handlers or `foreach` bodies [S]. Effort:
**medium–large** — new emitted step shape, record mapping of the `waiting` statuses (B6), the
marker/resume question already open in doc 35. Prerequisite for the whole item: B6.

**O2. Notifications that name the failed steps — principle 2.** Partial-success rules [R 2.11.3];
step status lists (failed, partial, aborted, succeeded, including `foreach` items) in templates [R
2.12.0]; the run link rendered when the scheduler sends it [R 2.11.4]; `run.error` filled from
failed steps [R 2.18.0 #2894]; Teams and custom webhook payloads [R 2.13.0]; notification rule
and channel management [R 2.16.6]. Effort: **low** if Cairn only documents the base.yaml
settings; **medium** if it writes them. Not measured.

**O3. Scheduler pause and catch-up recovery — principles 1 and 4.** A global scheduler pause
switch (`/services/scheduler/pause`) [R 2.16.5 #2762, S api.yaml]. This is the natural hold for
"allowance exhausted, nothing new should start" ([38](38-subscription-headroom.md) says the
scheduler's own resume is not built). "Recover catchup after scheduler pauses" [R 2.16.4 #2742]
changes catch-up behaviour after a pause; with `catchup_window: ""` Cairn should be immune, but
that is unmeasured (U2). Effort: **low–medium**.

**O4. Carry parameters with spaces — principle 1.** `--params-stdin` (up to 1 MiB, quoted values
keep spaces) [R 2.18.2 #2884]. Also measured: trailing `-- A="foo bar" B=2` arrived as `A=[foo
bar]` [M]. Cairn's trigger refuses any parameter with whitespace (`refuse_uncarriable`,
`skill/trigger.py:201`) because `--params` splits. Switching to the `--` form would lift that
restriction. I did not measure whether 2.11.0 already honoured the `--` form. Effort: **low**.

**O5. Resume part of a run — principles 1 and 3.** `dagu start --only <step>` with `--outputs-from`
and `--output` for skipped steps [R 2.17.2 #2855, #2859]; `dagu retry --bypass-preconditions` [R
2.17.0 #2785] (also in API and UI [R 2.18.0 #2880]); retry a step with its downstream [R 2.15.0 #2582]. This bears on [24](24-recovery-economics.md) and [34](34-follow-on-runs.md), but Cairn's
verified-work gate is built from markers and preconditions, and `--bypass-preconditions` would
bypass the gate itself. Adopt `--only`, not the bypass. Effort: **medium**; needs a measurement of
how `--only` records skipped nodes next to markers.

**O6. Liveness without inference — principle 2.** `dagu ps` with `--json` [R 2.11.3, 2.16.5] and an
API field exposing the local process of a running run [R 2.16.5 #2758]. Cairn decides liveness from
`pid` and `pidStartedAt` because the record lies after a kill (confirmed unchanged [M]). If the
API reports the process truthfully it could simplify that, but `dagu ps` listed my killed run as
fresh, so do not assume it does. Effort: **low** to evaluate; unknown payoff.

**O7. Retention and log hygiene.** `dagu rm` with duration filtering [R 2.11.3]; `prune-artifacts`
[R 2.18.2 #2900]; empty log directories removed [R 2.14.0 #2575]; history cleanup idempotent [R
2.16.2 #2694]; stdout/stderr artifacts replaced per attempt [R 2.17.2 #2847]. Effort: low,
documentation only.

**O8. A real log/artifact view for watching — principle 2.** Step logs as ZIP and streamed
individual downloads [R 2.17.0 #2781, 2.17.2 #2837]; an Artifacts page [R 2.17.0 #2788]; live step
output after starting a run [R 2.16.5 #2752]; the run inspector over MCP [R 2.11.3 #2480]. These
help the engine's own view that Cairn already links to ([33](33-the-view-link-answers.md),
[36](36-keeping-tabs.md)). Effort: low.

**Nothing found for the sleep timeout.** The step timeout is `context.WithTimeout` in
`internal/runtime/node.go` at both tags [S]; 2.18.2 only adds an elapsed-time measurement for the
message. No release note mentions wake-from-sleep, wall-clock timeouts or a sleep-aware deadline.
Go's timer clock on macOS does not count time the machine spent asleep; that is my understanding
of the Go runtime, not something I read in these notes or measured here (U1). If correct, the
10-minute step bound that did not fire across ~3 hours of maintenance sleep would behave the same
on 2.18.2. The remedy would then be Cairn's own wall-clock check, not an engine feature.

**Caution, not an opportunity: signal propagation.** An opt-in `signal_handling.enable_propagation`
forwards the server's or scheduler's SIGINT/SIGTERM to its running DAG subprocess groups [R 2.18.2 #2885, S config.schema.json, default false]. Leave it off: a restart of a viewing server should not
kill agent sessions.

## Security and stability between the versions

- **No published advisory is newer than 2.11.0.** The GitHub advisories API lists six for the repo;
  the newest, GHSA-5m6c-38r8-5w76 (MCP-reachable arbitrary YAML read), was published 2026-07-26
  with patched version v2.11.0 — the version already pinned. The others are older [read from the
  advisories API].
- **Hardening that bears on a locally bound server with builtin auth:** server IP allowlist (`ip_access`)
  [R 2.15.0 #2586]; HTTP access logs off by default [R 2.13.0 #2538]; secrets masked in stored
  step outputs [R 2.17.2 #2861]; malformed JSON now returns 400 [R 2.18.2 #2955]; OIDC role
  mapping reloaded at login [R 2.11.2]; webhook profile tokens [R 2.18.2 #2964]; Go raised to
  1.26.6 then 1.27 [R 2.15.0]; go-git and OpenTelemetry security bumps [R 2.14.0, 2.17.0]; signed
  release `packslip` [R 2.18.0 #2891]. The default auth posture I measured is unchanged: builtin
  mode, no account until `/setup`, unauthenticated API refused.
- **Stability fixes for how Cairn runs it:** running status writes preserved during teardown [R
  2.14.0 #2574]; proc liveness survives a damaged proc file [R 2.12.0 #2507]; status made durable
  before continuing past a node [R 2.17.0 #2809]; dotenv loaded for scheduler-triggered runs [R
  2.18.0 #2937] (a regression "since 2.10.x", so it applies to 2.11.0 too); scheduler memory
  growth under worker outages [R 2.15.2, 2.15.3]; a sub-DAG call is skipped when the root's
  preconditions are unmet [R 2.15.3 #2614].
- **No fix for the kill-9 stuck record** [M]. Cairn's reconciler stays.

## Unknowns that need a hands-on measurement

Take all three in a throwaway home (`HOME` and `DAGU_HOME` under a temp directory, a scratch port,
the downloaded 2.18.2 binary by absolute path), never against the live engine home.

- **U1. Step timeout across machine sleep.** Run a 60 s `timeout_sec` step doing `sleep 300`, put
  the machine to sleep for about two minutes, wake it, and record when the engine fails the step
  and what `elapsed` the message reports. Repeat with a 2.11.0 binary in its own throwaway home so
  the comparison is like for like. **Do not do this while the live run exists**: sleeping the
  machine would freeze it. It must also not use `pmset` on a machine with work in flight. Answers
  whether the engine has any wall-clock deadline at all, and so whether Cairn must add one
  ([22](22-timed-out-step.md)).
- **U2. Scheduler-triggered runs.** A one-minute-cron DAG with `catchup_window: ""` and
  `overlap_policy: skip`, run under `dagu scheduler --dags <temp>` for three minutes; then stop
  the scheduler for three minutes and restart it. Expect no replayed slots. Also confirm dotenv,
  queue pacing [R 2.17.0 #2775] and base-default reloading [R 2.16.6 #2770] do not change what
  `cairn/schedule.py` assumes (it launches `dagu scheduler --dags <root>`). Not measured at all
  here.
- **U3. A waiting run, end to end.** One `human.task` DAG: record how a waiting run appears in
  `status.jsonl` (run `7`, node `7`), what `dagu start` does while waiting (blocks? exits?), what
  `dagu status` and the REST status label say, and what a run that is waiting looks like after a
  `kill -9`. This is the input to B6 and O1.

Smaller checks worth doing at the same time: the 2.11.0 layout of `dag-runs/` (to know whether the
`<dag>/dag-runs/` level is new); whether `--` parameter passing preserved spaces on 2.11.0 (O4);
and `${id.exit_code}` for a timed-out step on 2.11.0 (B7).

## Recommended order of work

1. **Wait for the live run to finish.** Nothing here needs the engine moved before then.
2. **Install 2.18.2 alongside, not over, 2.11.0** (the release asset is checksum-verified;
   `dagu upgrade` was not used) and run U2 and U3 against it in a throwaway home.
3. **Move the pin** as one change (Verdict item 2), correcting B2, B3, B4 and B7's text in the same
   commit. Re-record `fixtures/runs/*` and regenerate `fixtures/workflows/*`; the diff should be
   the stamp and the three dropped record keys, and nothing else. If anything else moves, stop.
4. **Resolve B6** (map statuses 7, 8, 9 in `record/engine.py`; the run record gains a waiting
   cause) before any step shape that can wait.
5. **Adopt, cheapest first:** O4 (spaces in parameters), O7, O2, O3, then O5, O6, and O1 as its own
   plan once U3 answers.
6. **Take U1** on a quiet machine after step 3 and decide whether Cairn needs its own wall-clock
   bound beside the engine's.
