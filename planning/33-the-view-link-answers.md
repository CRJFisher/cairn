# 33 — The view link Cairn prints answers when it is opened

A run started, and Cairn printed `Live graph and logs:
http://127.0.0.1:8080/dag-runs/task-376-close-out-plan/20261003T152935Z-75872507`. The browser
could not reach it, because no `dagu server` was running. The person had to work that out and
start the server by hand. Cairn printed an address it had never checked, and it was the first
thing the person tried to use.

**Serves** **Run**, and **Report** and **Explain** wherever they link a run. The live graph
and logs Cairn points at actually load. When they cannot, the person is told why and offered
the fix, before they find out in a browser.

## Today

- `cairn/layout.py` builds the address from `CAIRN_VIEW_BASE`, then the engine's
  `DAGU_HOST`/`DAGU_PORT`, then `127.0.0.1:8080`. `trigger.address` prints it, and the run
  record stores it as `view_url`. Nothing checks that anything is listening there.
- Cairn deliberately starts no server. [docs/triggers.md](../docs/triggers.md) says "Cairn
  neither starts nor manages the process", and [20](20-watching-a-live-session.md) says "no
  engine server is required for the `where` line". This document starts the view server, and
  only the view server. The scheduler is unaffected.

## The change

**Check before the run starts.** Run's procedure gains a step before the engine is invoked:
`python3 -m cairn view status`. It sends `GET <view base>/api/v1/health` and reads the result
as one of three states:

- **`serving`**: a JSON body with `"status": "healthy"` and a `version`. The version is
  checked against the engine Cairn pins.
- **`absent`**: the connection is refused.
- **`foreign`**: anything else on that address. Dagu 2.11.0 answers `/health` and
  `/api/v2/health` with its web app's HTML and a 200, so a check on the status code alone
  would mistake any web server, or the wrong endpoint, for a healthy view.

**When it is `absent`, ask.** The skill puts one question with the ask tool. The question
concerns the view and not the run, and the run goes ahead whatever the answer is.

- **Start the view server.** Cairn runs `dagu server` with the host and port the address was
  built from, then polls the health check until it reports `serving`.
- **Not now.** The run starts, and the reply prints the address together with the one command
  that serves it.

**`foreign` is never answered by starting anything.** The reply says what is on the port.
`CAIRN_VIEW_BASE` is how to point Cairn at a server running elsewhere.

**The server outlives the session that started it.** It is launched the way
`launch_detached` already launches a run: `start_new_session`, stdin from `/dev/null`, and
output appended to a log under the engine home. Closing the conversation, a harness killing
its process tree, or a terminal hangup does not take it down. Nothing about the session has
to stay open.
The agent never starts it as a background job of its own shell, because that ties the
server's life to the session. Starting it is Cairn's command, not something the agent
improvises.

**Only the view.** The command Cairn runs is `dagu server`, never `dagu start-all` and never
`dagu scheduler`. `start-all` starts the scheduler too, and the scheduler's retry scanner
re-executes failed runs across the machine. That is
[scheduling.md](../capabilities/scheduling.md)'s escalation, and a request to see the graph
never reaches it.

**The two things a started server owes the person, said in the same reply:**

- **Claim the admin account.** The server starts with authentication unclaimed, and the first
  local process to call its setup endpoint becomes administrator
  ([triggers.md](../docs/triggers.md)). The reply says to open the address now and claim it.
- **How to stop it**, and that stopping it loses nothing, because it holds no run state.

**The sandbox wall, before the question.** A shell that may not bind a TCP port fails to
start the server in the same way [19](19-start-friction.md) wall 3 found for the engine's
unix socket. `view start` checks that it can bind before launching. If it cannot, the reply
names the cause instead of waiting on a server that never comes up.

**Report and Explain run the same check** wherever they print a run's `view_url`. A dead link
in a report reads as "not being served; `dagu server` serves it". They never ask, because
reading starts nothing.

## What must not change

- The run never waits on the view and never depends on it. `dagu start` runs a workflow with
  no server, and a run whose view is declined is the same run.
- No scheduler is ever started on the way to a view.
- The address is still built only from `layout.view_url`. Report and Run still print the same
  address for the same run.
- A server Cairn did not start is never stopped, restarted or reconfigured by Cairn.

## Touches

- `cairn/layout.py`: the health address beside `view_url`.
- A new `cairn/view.py`:
  - `status`, returning `serving`, `absent` or `foreign` with a reason.
  - `start`, the detached launch and the poll until `serving`.
  - The bind probe.
- `cairn/skill/trigger.py`: reuse `launch_detached`. It is not copied.
- `cairn/skill/cli.py`: `view status` and `view start`.
- `capabilities/running.md`: the new step before start, and the question.
- `capabilities/reading.md`: the dead-link sentence.
- `SKILL.md`: _The engine, and where it is the better answer_ says Cairn serves the view on
  request.
- `docs/triggers.md`: _Opening the view_ and _There are two daemons_.
- [20](20-watching-a-live-session.md): its "no engine server is required" sentence.
- `cairn/report/compose.py`: the link line.
- `tests/test_triggers.py`, `tests/test_the_skill.py`, and a `view` fixture family for
  `serving`, `absent` and `foreign`.

## Acceptance

- With no server running, a run is preceded by one view question. Choosing to start the view
  makes the printed address load in a browser, and it is still loading after the conversation
  that started it has ended.
- With a server already up, there is no question. The address is printed and it loads.
- With something other than Dagu on the port, there is no question and no launch, and the
  reply names the port and `CAIRN_VIEW_BASE`.
- Nothing Cairn launches on the way to a view runs a scheduler. `ps` shows `dagu server` only.
- A report on a run, with no server up, says the link is not being served and names the
  command.
