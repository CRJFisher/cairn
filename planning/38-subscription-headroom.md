# 38 — Subscription headroom: a queue holds at the limit and resumes when the window reopens

**North star ([principle 4](../PRINCIPLES.md)).** A person queues a large set of steps and walks
away. When the Claude subscription's 5-hour or weekly allowance runs low, Cairn stops starting
paid sessions, waits until the window that blocks the work reopens, and carries on. A limit is a
pause in the run, never the end of it.

**Status: researched plan, ready to build.** The measurement is settled by evidence below; two
behaviours that need a real limit hit to observe are named as spikes (§ Spikes).

**Serves** the capability surface of **Run**, **Report** and **Schedule**. No invariant moves: the
emitted graph stays static and digest-stamped (the wait lives inside a step body, as `cairn wait`
does), the record stays the only account of a run, and a verdict stays something a declared
assertion proved.

## Today

A subscription limit is a failure of the step that meets it.

- `cairn agent run` leaves on exit 75 with cause `rate_limited` when the session ends on
  `terminal_reason: blocking_limit` (`cairn/providers.py`, `_translate_result`). The step fails,
  everything downstream of it is skipped, and the run halts. The marker of every step that landed
  survives, so a person re-runs the plan by hand once the window reopens.
  [supervision.md](../docs/supervision.md) states this on purpose: the engine's retry policy
  cannot read a reset time, so the moment is _reported_ rather than waited out.
- The reported moment is lost. `_latest_reset` reads `event["resetsAt"]` and requires a string.
  A real event nests it as `rate_limit_info.resetsAt` and carries an integer (measured on Claude
  Code 2.1.220, below). On a real stream `detail.resets_at` is always `null`, and the unit tests
  pass because their fixtures (`{"type": "rate_limit_event", "rate_limit_info": {"resetsAt": 9}}`
  and the top-level form) encode the same wrong shape.
- Nothing looks at headroom before a session starts. Eight parallel steps admitted into a window
  at 96% all find out the same way.

## What was measured

### The channels that expose usage, as they behave

| Channel                                                          | Gives percentage?                    | Gives reset time?  | Available to `claude -p`?     | Costs allowance?                                              |
| ---------------------------------------------------------------- | ------------------------------------ | ------------------ | ----------------------------- | ------------------------------------------------------------- |
| `rate_limit_event` in the `stream-json` output                   | only at `allowed_warning`/`rejected` | yes, epoch seconds | **yes, already read**         | free (rides real sessions)                                    |
| A probe: `claude -p` with one turn and the stream read           | same as above                        | yes                | yes                           | ~$0.05 notional on a lean settings-free haiku turn (measured) |
| `GET api.anthropic.com/api/oauth/usage`                          | **yes, every window**                | yes, ISO 8601      | independent of the CLI        | free                                                          |
| Status line `rate_limits` JSON                                   | yes, 0–100                           | yes, epoch seconds | **no** — interactive TUI only | free                                                          |
| Hook payloads (`PreToolUse`, `Stop`, …)                          | no field carries it                  | no                 | —                             | —                                                             |
| Local transcript arithmetic (ccusage, Claude-Code-Usage-Monitor) | estimate only                        | estimate           | yes                           | free                                                          |

**`rate_limit_event`.** Measured on the installed CLI (2.1.220), one `claude -p … --output-format
stream-json --verbose` turn:

```json
{
  "type": "rate_limit_event",
  "rate_limit_info": {
    "status": "allowed",
    "resetsAt": 1791151800,
    "rateLimitType": "five_hour",
    "overageStatus": "rejected",
    "overageDisabledReason": "org_level_disabled_until",
    "isUsingOverage": false
  }
}
```

`status` is `allowed`, `allowed_warning` or `rejected`. At `allowed` there is no utilization.
Reports of the warning and rejected shapes add `utilization` (a 0–1 fraction) and
`surpassedThreshold` ([issue 41185](https://claudeissues.com/issue/41185-rate-limit-event-include-utilization-in-allowed-status)).
A request to put `used_percentage` on every event was closed _not planned_
([issue 77018](https://claudeissues.com/issue/77018-expose-rate-limit-utilization-used-percentage-in-headless-contexts-stream-json-o)).
The measured event names one window (`rateLimitType`); other tools describe a
`unifiedWindows` object holding both, so which shape a given CLI version emits is spike 4. The same
event is emitted by every session Cairn already runs and tees, so it costs nothing to keep.

**The OAuth usage endpoint.** `GET https://api.anthropic.com/api/oauth/usage` with
`Authorization: Bearer <access token>`, `anthropic-beta: oauth-2025-04-20` and a
`User-Agent: claude-code/<version>` returns `five_hour`, `seven_day`, `seven_day_opus`,
`seven_day_sonnet` and `extra_usage`, each `{utilization: 0–100, resets_at: ISO 8601}`
([Claude-Code-Usage-Monitor #202](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor/issues/202)).
It is the same data `/usage` renders, it is device-independent, and it is the only channel that
gives both windows' percentages on demand. Its costs:

- **Undocumented.** No contract; the schema and the beta header can change without notice.
- **Credentials.** The token lives in the macOS keychain item `Claude Code-credentials` (or
  `~/.claude/.credentials.json` elsewhere), expires about hourly, and is refreshed by Claude Code
  while it runs. A reader outside Claude Code must not refresh it (it would race Claude Code's own
  refresh) and must treat an expired token as "unknown".
- **Hostile to polling.** Without the `User-Agent` it answers 429 at once, and some accounts get
  a persistent 429 with `retry-after: 0`
  ([claude-code #30930](https://github.com/anthropics/claude-code/issues/30930)). Working tools
  cache for 180 s and back off 3 → 6 → 12 → 15 minutes.

Not run locally: reading the keychain token was left to the build's first spike, with the user's
say-so.

**The status line** is the one place Claude Code documents `rate_limits`
(`five_hour` and `seven_day`, `used_percentage` + `resets_at`), and only for Pro/Max, only after
the session's first API response, with a window dropped once its `resets_at` passes
([statusline docs](https://code.claude.com/docs/en/statusline)). It renders only in the
interactive TUI. Cairn's sessions are `-p`; the status line never runs for them. It has also
regressed silently before (absent from 2.1.138,
[claude-code #60612](https://claudeissues.com/issue/60612-statusline-hook-payload-no-longer-includes-rate-limits-field-v2-1-138)).
Tools built on it ([claude-usage-monitor](https://github.com/arturl95/claude-usage-monitor))
write one file per session from the status line and warn through a `PreToolUse` hook that reads
those files — which is the reason hooks can warn at all: no hook payload carries usage
([claude-code #38646](https://claudeissues.com/issue/38646-include-rate-limits-data-in-hook-inputs-stop-notification)).
That design depends on an interactive session rendering, so it cannot feed a headless queue.

**Local transcript arithmetic** (ccusage, Claude-Code-Usage-Monitor's default) sums tokens from
`~/.claude/projects/**/*.jsonl` and guesses the plan's cap. It counts only this machine, mislocates
the window start when other devices or claude.ai use the same account, and has no way to know
the denominator, which Anthropic does not publish and which changes
([#202](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor/issues/202)). It is the wrong
tool for a gate that must be right; it is not used.

**The weekly window is not trusted to be seven days.** One report claims the "weekly" counter
resets about every 72 hours while `resets_at` still says seven days
([gist](https://gist.github.com/monperrus/3ac4b303a84946bbeaf2b1123ee99491)). One source, and
Anthropic may have changed it since. The consequence is design, not belief: Cairn never computes
a reset itself. It waits for the `resets_at` a measurement reported, then measures again.

## The design

### A — One shared reading, three feeders, and the age on every number

A **headroom reading** is `{five_hour, seven_day}`, each `{used: 0–1 | null, status, resets_at,
source, read_at}`. It lives in one file under the runs root, replaced atomically, so every
concurrent step and the scheduler read the same fact and no step probes on its own.

Three feeders write it, cheapest first:

1. **The stream.** Every `rate_limit_event` a session produces updates its window's `status` and
   `resets_at` (and `used`, when the event carries it). Free, continuous, and the only feeder
   that sees `rejected`.
2. **The usage endpoint**, **off unless `CAIRN_HEADROOM_USAGE_ENDPOINT=1`**, and then used when a
   credential is readable and the account answers. Gives both windows' `used` and per-model
   weekly windows. Read-only, cached 180 s minimum, backed off on 429, never refreshes a token.
   It reads a credential Cairn did not create, so a person turns it on; the owner's own
   environment sets it, and Cairn's code carries no default of its own. Packaging may later ask
   each installer once, during onboarding.
3. **The probe**, when the reading is older than the admission TTL (10 minutes) and the endpoint
   has not answered. A one-turn `claude -p` session on the cheapest model with no project
   settings, no MCP and no session persistence, read for its first `rate_limit_event` and
   discarded. It yields `status` + `resets_at` and, near a limit, `used`.

Every consumer sees `read_at` and `source` with the number. A reading older than its TTL is
`unknown`, not stale-but-trusted.

### B — Admission: a step does not start a session into a closed or closing window

`cairn agent run` consults the reading before it opens the provider:

| Reading                                                     | Decision                                                  |
| ----------------------------------------------------------- | --------------------------------------------------------- |
| Any window `rejected`                                       | **hold** until that window's `resets_at`                  |
| A window's `used` ≥ the hold threshold (default 0.95)       | **hold** until that window's `resets_at`                  |
| `allowed_warning` with no `used`                            | **admit**, and record the warning in the step's report    |
| `unknown` (nothing fresh, no probe answer, API-key funding) | **admit**, and record `headroom: unknown` with its reason |
| Otherwise                                                   | admit                                                     |

Unknown admits because the backstop in § C makes a wrong admit recoverable, and a guard that
blocks on its own blindness would stall a queue for a fault in the instrument. That is the same
fail-open reasoning [hooks.py](../cairn/hooks.py) states for its own hook, and the inverse of the
verify gate: nothing durable depends on this check having run.

A step funded by an API key (`apiKeySource` ≠ `none`) has no subscription windows. The guard is
inert for it and the step's report says so.

**The hold is inside the step body, like `cairn wait`.** The emitted graph gains no node and no
logic. The step's wait is bounded by its own `quota_wait` ceiling and the engine's `timeout_sec`
grows by it exactly as `WAIT_REPORT_GRACE` does today, so the work bound stays the priced bound
and the run's maximum duration and lock-reclaim window stay derivable.

**Long holds go to a person.** A 5-hour hold is waited out in the step. A hold whose `resets_at` is
beyond `quota_wait` (a weekly window days away) does not sleep a worker for days: the step exits
with a new cause `quota_held`, carrying `resets_at` and which window, and the report states
_held until Thursday 04:00 — weekly allowance at 97%_. It is the human-in-the-loop path
([35](35-human-in-the-loop-steps.md)): the person chooses to wait, to switch to a cheaper model,
or to stop. With the scheduler ([triggers.md](../docs/triggers.md)) the same fact schedules the
resume for the reported moment.

**After the wait, measure again.** The step does not assume the window reopened because the clock
passed `resets_at`: it re-reads (a probe, if nothing fresher), admits on a clear reading, and backs
off (5, 10, 20 minutes, capped) when the window is still closed. This is what makes a wrong or
shifted `resets_at` — including the weekly case above — a short extra wait rather than a crash.

### C — The backstop: a limit hit mid-session is a pause, and the session resumes

Admission lowers the odds of meeting a limit; it cannot remove them, because one session spends an
unknown share of the window. A session that ends on `blocking_limit` is therefore **held, then
resumed**, not failed:

1. The wrapper records the `rejected` event (feeder 1), takes `resets_at` from it, and holds as in § B.
2. It resumes the same session by id (`--resume`), the machinery [22 §B](22-timed-out-step.md) and
   [19 §D](19-start-friction.md) already use to ask a stopped session for its report.
3. Work bound accounting excludes the held time: the priced bound is time spent working.

If a resume is refused or the session cannot continue, the step ends `quota_held` with its session
id, and the marker means the plan's re-run skips everything that landed. That is today's recovery,
now the last resort and not the only path.

### D — The reader of the reset time is fixed first

`_latest_reset` takes the maximum `rate_limit_info.resetsAt` across events, as an integer epoch,
and `detail.resets_at` is an ISO-8601 UTC string derived from it. The test fixtures are replaced
with the shape the CLI emits (the measured event above, plus recorded `allowed_warning` and
`rejected` events from the spikes). This lands before anything else: it is the defect that makes
the current "reported moment" a lie, and the wait is built on it.

### E — Seeing it

The report and the live view say, for a run: the latest reading with its age and source; each hold
(step, window, from, until, why); each resume. A held run reads as _waiting for the 5-hour window,
reopens 14:10_, not as stalled. This is the tab-keeping surface of [36](36-keeping-tabs.md) and
[20](20-watching-a-live-session.md) carrying one more fact.

## Where a hook fits

The request was for a hook that fires at various events. Claude Code hooks cannot measure usage —
no payload carries it — so a hook is a place to _read_ a measurement, not take one. Cairn puts the
decision where it is exact: **admission, in the wrapper, before each session**, and **resume, after a
rejected event**. A hook inside the session (`PreToolUse` reading the shared reading) can only
warn the agent or deny a tool call; it cannot pause the session for hours without holding a paid
turn open against hook timeouts. It is not used for the pause. A `PreToolUse` warning ("allowance
at 93%, finish and report") is a possible later addition, and it reads the same shared reading.

## Spikes

1. **A real rejection.** Run until a limit is hit (or a throwaway account) and record the
   `rejected` event, the result message's `terminal_reason` and `is_error`, and the exit status.
   Settles the rejected event's real shape and that `blocking_limit` is the terminal reason.
2. **Resume after rejection.** Whether `--resume <session>` after the window reopens continues the
   turn that was refused. If not, § C falls to `quota_held` plus the marker path.
3. **The usage endpoint, live.** With the user's say-so, read the keychain token once, call the
   endpoint with the `User-Agent` and compare its percentages with `/usage`. Record the schema, the
   token lifetime, and whether the 429 appears on this account.
4. **Warning thresholds.** Which utilizations flip `allowed` to `allowed_warning`, and whether the
   event names both windows or the nearer one.
5. **What a step costs in percent.** Pair `total_cost_usd` from run records with the change in the
   reading across the step. If a step's share of the window is predictable, admission can compare
   it with the headroom instead of a fixed 0.95.

## Open questions

1. **Hold threshold per window.** 0.95 on the 5-hour window is a different bet from 0.95 on the
   weekly one, which reopens days away. Spike 5 informs it; the default may be per window.
2. **Which model does the probe use?** The cheapest one, but a per-model weekly window (Opus,
   Sonnet) is only visible through that model's event or the endpoint.
3. **One account or several?** The reading is keyed by the logged-in account. Two Claude logins on
   one machine need two readings; today Cairn assumes one.

## Acceptance

- A plan whose steps outrun the 5-hour allowance completes: the step that meets the limit holds,
  resumes after the window reopens, and the run's verdict is the same as an uninterrupted run's.
- A reading below the hold threshold admits with no probe when the stream's last event is fresher
  than the TTL; eight concurrent steps cause at most one probe.
- A step admitted on `unknown` records why, and the run does not stall on a blind instrument.
- `detail.resets_at` is the ISO-8601 time of the `resetsAt` in a real event; the tests assert it
  against the recorded stream shape.
- A weekly hold longer than `quota_wait` ends the step `quota_held` with the moment and the window,
  and the report says so in a sentence; no worker sleeps for days.
- The report and view show the reading, its age and source, every hold and every resume.
- Reproduction: a throwaway account driven to its limit under a plan of five agent steps. The run
  record shows a hold, a resume, five landed steps, and no exit 75 reaching the engine.

## Touches

`cairn/providers.py` (`_parse_lines`, `_latest_reset`, `_translate_result`, resume),
`cairn/core.py` (`EXIT_RATE_LIMITED`, a `quota_held` cause), a new `cairn/headroom.py` (the
reading, the three feeders, the admission decision), `cairn/emitters.py` (`emit_agent` timeout
arithmetic), `cairn/plan/schema.py` (the retry comment, `quota_wait`, the thresholds),
`cairn/record/vocabulary.py`, `cairn/report/`, `docs/supervision.md` (the rate-limit paragraphs),
`docs/plan-contract.md` (the `retries` paragraph), `docs/step-kinds.md`, `tests/test_providers.py`,
`tests/test_step_kinds.py`, a new `tests/test_headroom.py`.

## Sources

- [Claude Code status line docs](https://code.claude.com/docs/en/statusline) — `rate_limits` fields and availability
- [Maciek-roboblog/Claude-Code-Usage-Monitor #202](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor/issues/202) — OAuth usage endpoint, credentials, caching, local-log failure modes
- [anthropics/claude-code #30930](https://github.com/anthropics/claude-code/issues/30930) — persistent 429 on the usage endpoint
- [anthropics/claude-code #29604](https://github.com/anthropics/claude-code/issues/29604) — the unified rate-limit response headers behind the status line
- [claude-agent-sdk-python #599](https://github.com/anthropics/claude-agent-sdk-python/issues/599) — the `rate_limit_event` message
- [claudeissues #41185](https://claudeissues.com/issue/41185-rate-limit-event-include-utilization-in-allowed-status), [#77018](https://claudeissues.com/issue/77018-expose-rate-limit-utilization-used-percentage-in-headless-contexts-stream-json-o) — utilization absent at `allowed`; headless has no percentage
- [open-tomato/rafa #725](https://github.com/open-tomato/rafa/issues/725) — probe-session design and cost
- [hathbanger/orc #127](https://github.com/hathbanger/orc/issues/127) — pace-aware thresholds
- [arturl95/claude-usage-monitor](https://github.com/arturl95/claude-usage-monitor) — status-line-fed, hook-warned design
- [monperrus gist](https://gist.github.com/monperrus/3ac4b303a84946bbeaf2b1123ee99491) — the 72-hour weekly reset report
