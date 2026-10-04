# 40 — The agent's task list as the source for keeping tabs

**North star ([principle 2](../PRINCIPLES.md)).** A person glances at one page and knows what each
running session is doing. The agent's own task list is a structured, model-authored statement of
exactly that — read it instead of summarising the transcript with a second model.

**Status: findings, feeding [36](36-keeping-tabs.md).** Replaces 36's summariser (`claude -p`) in
the first iteration. Everything under "Verified" was observed on Claude Code 2.1.220 with a logging
hook on every event, in headless `claude -p` mode.

## Capability surface

For each running node the page can show the session's task list — each task's subject, status
(`pending` / `in_progress` / `completed`) and dependencies — current to the moment the agent last
changed it. No model call, no cost, no summary that can be wrong about what the agent said it was
doing. A session that never writes a task list shows nothing for this layer and falls back to
[20](20-watching-a-live-session.md)'s mechanical `where` line.

## Verified: the agent has two task tools, one at a time

| Tool                              | Selected by                     | State shape                       |
| --------------------------------- | ------------------------------- | --------------------------------- |
| `TodoWrite`                       | default in headless `claude -p` | whole list rewritten on each call |
| `TaskCreate` / `TaskUpdate` / ... | `CLAUDE_CODE_ENABLE_TASKS=true` | one record per task, with deps    |

`TodoWrite` is a deferred tool in headless mode: the agent loads it with `ToolSearch` first, which
is itself a `PostToolUse` event with `tool_name: ToolSearch` and can be ignored.

Cairn controls the child's environment in `run_claude`, so it chooses which tool the agent uses.
The Task tools give the better surface (stable ids, dependencies, per-task events, a store on
disk); `TodoWrite` is the fallback if the env var is unavailable.

The docs describe `CLAUDE_CODE_ENABLE_TASKS=0` as forcing `TodoWrite` and say the Task tools are
the default on some models; headless `claude -p` with Haiku defaulted to `TodoWrite` in this
experiment. The default therefore varies by model and mode, and Cairn sets the variable
explicitly rather than relying on it.

## Verified: hook events that fire on task changes

Every payload carries `session_id`, `transcript_path`, `cwd`, `prompt_id`.

| Event                              | Fires on                | Task-specific payload                                                                                                                |
| ---------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `PostToolUse` matcher `TodoWrite`  | every list rewrite      | `tool_input.todos` (full new list); `tool_response.oldTodos` and `.newTodos` (before and after)                                      |
| `PostToolUse` matcher `TaskCreate` | task created            | `tool_input.subject/description`; `tool_response.task.{id,subject}`                                                                  |
| `PostToolUse` matcher `TaskUpdate` | any field change        | `tool_input` (a delta: `taskId` plus changed fields); `tool_response.updatedFields`, and `statusChange.{from,to}` on a status change |
| `TaskCreated`                      | task created            | `task_id`, `task_subject`                                                                                                            |
| `TaskCompleted`                    | task set to `completed` | `task_id`, `task_subject`, `task_description`                                                                                        |

Consequences:

- No polling is needed. Task changes are hook events, and `PostToolUse` fires after the change is
  committed (`PreToolUse` fires before and has no `tool_response`).
- `TodoWrite` hands the hook the complete list on every event, so the hook needs no state.
- `TaskUpdate` hands the hook a delta only. `in_progress` transitions, subject edits and
  dependency edits are visible only in `tool_input`. To render the whole list the hook reads the
  store (next section) rather than accumulating deltas.
- `TaskCreated` / `TaskCompleted` are narrower than `PostToolUse`: they miss `in_progress`
  transitions, which is the signal that says "what is it doing right now". Use `PostToolUse`
  with matcher `TaskCreate|TaskUpdate|TodoWrite`.

## Verified: the Task tools persist to disk

`~/.claude/tasks/<session_id>/<task_id>.json`, plus an empty `.lock`:

```json
{
  "id": "2",
  "subject": "...",
  "description": "...",
  "status": "pending",
  "blocks": [],
  "blockedBy": ["1"]
}
```

- Keyed by the hook payload's `session_id`, so a hook (or Cairn, or the page) can read the full
  current list with a directory read. The files are the authoritative state; hooks only say when
  it changed.
- `TodoWrite` in headless mode wrote nothing under `~/.claude/todos/` or `~/.claude/tasks/`. Its
  only durable copy is the transcript (the tool call and its `toolUseResult`). Its state must be
  captured from the hook payload.
- The store lives under the user's `~/.claude`, outside the node's worktree. A reader needs the
  `session_id`, which Cairn's stream tee already sees (`SessionStart` / the stream's init event).

## Verified: events that frame the list

`SessionStart`, `UserPromptSubmit`, `PostToolBatch`, `Stop`, `SessionEnd` all fire in headless
mode. `Stop` carries `last_assistant_message`, `background_tasks`, `session_crons`,
`stop_hook_active`: the turn-end summary the agent already wrote, free. `SessionEnd` carries
`reason`. `PostToolBatch` fires once per batch of parallel tool calls and carries `tool_calls`; it
is the cheapest "something happened" tick if per-task events prove too chatty.

## Where to extract: the three choices

1. **Hook on `PostToolUse` (`TodoWrite|TaskCreate|TaskUpdate`).** Event-driven, exact, current
   within a second. Cost: hook config must reach the session. `claude --settings <file>` accepts
   hooks per invocation (used for this experiment), so Cairn adds the file to the child's command
   line and nothing is installed in the target repo. Recursion is not a concern: no second
   `claude` is launched.
2. **Cairn's stream tee.** `run_claude` already sees every tool-use event. Task tool calls are
   visible there with no hooks at all, and the Task store is readable from `session_id`. Simplest
   and nothing to install; the tee is the single place that already owns the session's lifetime.
3. **Poll `~/.claude/tasks/<session_id>/`.** Works for the Task tools only; adds latency and a
   timer for no gain over 1 or 2.

Recommendation for the first iteration: **the tee, with the Task store as the read model**; add the
`--settings` hook only for what the tee cannot see (subagents, below).

## From the documentation (not run here)

- The status line JSON has `session_id` and `transcript_path` but no task or todo fields.
- Subagents have their own context and transcript, and their tasks are not visible to the parent.
  Only agent teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) share a list, under
  `~/.claude/tasks/<team-name>/`. A node's tab shows the top-level session's list only.
- Task files are retained per `cleanupPeriodDays` (default 30) and persist across `/resume`.
- The docs name the team directory `session-<first 8 chars of session id>`; the headless run here
  used the full `session_id`. Readers look for the full `session_id` directory first and
  fall back to a glob on the 8-character prefix.
- Transcript JSONL is documented as internal and version-dependent. Read task state from hook
  payloads or the Task store, not by parsing the transcript.

## Open

- Whether a subagent's tool calls fire the parent's `--settings` hooks (the docs imply separate
  contexts; confirm with a `SubagentStop` logger).
- Interactive-mode behaviour: this experiment ran headless, which is the mode Cairn's nodes use.
- Whether a model writes a task list unprompted. Both runs here were told to. A plan step may need
  a standing instruction ("keep a task list").
