# 16 — Release: acquisition, posture, and a README that ends in a verified step

The last document. Nothing in the artifact is shaped like its author, the engine is obtained
rather than operated, the security posture is stated plainly, and a second person can go from
nothing to a verified step by reading one file. The test is a fresh machine, not an inspection.

**Serves** D1, D5 and D6.

**Status: implemented, except what only a second person on a second machine can establish
(see Outcome).** Tasks 1–11, 14 and the stated half of 15 are done and asserted. Task 12 —
the walk-through on a fresh machine by someone who is not the author, timed, with the number
published — and task 13's re-authoring check, which is one step of that walk, are left to a
person: they are facts about a machine this repository has never run on, and no test here
can stand in for them.

This is the whole of delivery: compliance, packaging and onboarding together. Cairn is a git
repository with a README, published because it might help someone, with no launch sequence and
no adoption target. So packaging is a directory move plus a manifest, onboarding is a cold path
that has to be walked by someone who is not the author before it counts as a fact, and the
posture is here because it is measured and load-bearing.

## Outcome

**What a person sees differently.** Cairn has an install path: prerequisites, one command
that installs the pinned engine and verifies it against the release's own `checksums.txt`,
one command that writes Cairn's values into the engine's machine-wide configuration, a
`git clone` whose checkout is both the package and the skill, one symlink that registers it,
and three checks that start nothing. The first thing the README then asks them to run is the
**failing** example: `python3 -m scripts.worked_example` renders a run the engine calls
`Succeeded` with exit `0`, over a step whose work never landed, and Cairn reports the step
excluded, names the cause `gate_indeterminate`, and exits `3`. It needs no engine, no agent
session, no network and no money. The README states the security posture it measured,
including the sentence that no posture closes — the trust boundary is the user account, not
the server — and the licensing boundary that keeps GPL obligations off the artifact.

**A view Cairn starts is no longer an open administrator account.** The engine's
authentication begins unclaimed and the first local process to reach its setup endpoint wins
([03](03-surface-spike.md)). `cairn view start` now claims it in the same breath as starting
the server: `POST /api/v1/auth/setup` with the user `cairn` and a password minted from the
platform's entropy source, printed once and kept nowhere. Measured against 2.18.2: the first
caller gets `200` and a token, every caller after it gets `403 Setup has already been
completed` — so an account somebody else claimed reads as held, and that is the same end
state. A claim that cannot be made stops the server Cairn just started. There are two end
states and no third, asserted against a real server in `tests/test_triggers.py`.

**Nothing in the artifact is shaped like its author.** `tests/test_release.py` fails on an
absolute home directory anywhere in any tracked file — source, generated fixture or recorded
one — with a declared placeholder list as the only way past it, and separately on this
machine's own home and checkout path, which is a different literal on every machine that
runs it. It found two real instances: the ten recorded runs in `fixtures/runs/` each carried
the recording machine's absolute interpreter path, because the engine writes a step's command
into its state file exactly as it ran it, and one planning document quoted an absolute path.
`scripts/record_runs.py` now normalises that path to the bare name `python3` on every
recording and declares the normalisation in `recording.json`, so the next re-recording cannot
reintroduce it.

**The gate is both halves and it runs somewhere that is not a laptop.**
`.github/workflows/ci.yml` installs the pinned engine by the documented command, writes and
then asserts the base configuration, runs `python3 -m unittest discover -s tests -t .` and
strict Pyright, and fails the build on a `ResourceWarning` — the leak check is a grep,
because a warning raised while the interpreter collects a leaked file is reported and
swallowed rather than raised. The leaks the audit found were test-launched children whose
pipes were never closed; `tests/processes.py` is the one way a test ends a child now.

**One engine-test policy, not two.** Every suite that needs the engine fails without it, and
`CAIRN_SKIP_ENGINE_TESTS=1` is the one way to pass without one. `tests/engine.py` is the
single statement of it; the two suites that used to skip silently — a machine with no engine
being the machine where everything passes — now do not.

**The manifest is real.** `pyproject.toml` carries project metadata, a build backend, a
`cairn` executable, no dependencies, and `requires-python = ">=3.11"`; the floor is the one
Cairn actually has, because the package imports `datetime.UTC`. One test holds the manifest,
Pyright's target and the README to the same number. Windows is refused by
`cairn/__init__.py` with the thing to do instead, before the import of `fcntl` that would
otherwise fail with a missing module on somebody's first command.

**What the audit's numbers are now.** The suite is roughly 1,600 tests and strict Pyright
reports none of the nine errors the audit found. No runtime is promised anywhere: the README
says minutes rather than seconds and names no figure, because no figure measured here is a
figure about CI.

**Not done, and why.** Task 12 and task 13: a plan run end to end on a machine that has
never held the author's files, timed, with the cold-start number published. Task 15's
replacement of the baked interpreter resolution also stands open — the minimum version is
now stated, which is the half this document owns outright, and the bare `python3` in every
emitted body is unchanged, so a generated workflow remains the per-machine build product the
claims below describe.

## The claims

**Every path is resolved at install or invocation.** No hardcoded home directory, no assumed
repository location, no dependency on a configuration layout only one person has. A generated
workflow is the one deliberate exception: it is a **per-machine build product**, not a portable
artifact. It encodes the package root, the repository and the runs root of the machine it was
authored on, by design ([workflow.md](../docs/workflow.md)), and it is authored on the machine
that runs it — re-authoring there is the whole of moving one. That stands until binary
resolution (item 15) replaces the interpreter resolution baked into every file; until then a
definition that names another machine's paths is a definition to re-author, never one to edit.

**The engine is obtained, not operated.** Installing Dagu is one documented command. It is not a
multi-service bootstrap, and it does not need a database or a daemon for the default one-shot run.

**The engine's own machine-level configuration is part of the artifact.** Dagu writes
`~/.config/dagu/base.yaml` on the first invocation of **any** of its commands, and every workflow
on that machine inherits it — the step concurrency cap, the shell, the output limit, and a
whole-DAG retry policy that re-executes failed runs ([01](01-engine-spike.md)). A file Cairn does
not write but every run reads is author-shaped by default: two machines with different base
configurations run the same emitted plan differently. Acquisition owns that file's contents, not
just the binary's presence.

**A running view is an account, not just a process.** The engine's server defaults to a built-in
authentication mode whose initial-admin account is unclaimed until something claims it, and the
first caller to the setup endpoint wins — in the spike a shell `curl` claimed admin before any
human opened the UI ([03](03-surface-spike.md)). A server Cairn starts and leaves unclaimed is an
open administrator account on the user's machine.

## The posture, stated rather than implied

Measured in [03](03-surface-spike.md), and the README says all of it.

**What is closed.** The server binds loopback only. The API rejects unauthenticated requests.
Cross-origin requests are refused outright — a preflight from a foreign origin returned
`403 cross-origin request denied` — so a page in the user's browser cannot drive the engine.
Framing is blocked. Webhook tokens are enforced. Secrets on disk are `0600`.

**What no posture can close, and the README must not imply otherwise.** Every hazard above concerns
the _API_. A local process does not need the API: it can run `dagu start` against any DAG file
directly, or read the token secret as the same user. **The trust boundary is the user account, not
the server.** Saying so plainly is more honest than a security section implying the API's auth
protects the repositories a trigger mutates.

**Cairn holds no credential**, and this is by construction rather than by policy. A step runs the
agent installation the user already authenticated — a subscription login through the OS keychain in
the measured case, with no `ANTHROPIC_*` variable anywhere in the environment
([02](02-agent-step-spike.md)). Nothing is minted, stored, transported or prompted for. An API key
remains available through the same binary's normal precedence and needs no Cairn-side support; the
moment Cairn placed a token in its own environment it would be handling a credential.

**Two outbound behaviours are disclosed rather than defended.** The engine's server phones home on
startup, writing a version check and rendering an update banner ([03](03-surface-spike.md)). And an
agent step's blast radius is bounded by its deny list and its timeout and by nothing else — an
allowlist is a floor, because the effective policy unions the step's grant with the operator's own
settings and a built-in read-only judgement ([02](02-agent-step-spike.md)).

**Licensing.** Dagu is GPL-3.0-or-later. Cairn invokes it as a subprocess and never imports its Go
API, which is what keeps GPL obligations off the distributed artifact
([research-dagu.md](research-dagu.md)). The README states the boundary and the reason, so a
contributor does not casually cross it.

## Unknowns this checks

- **What is author-shaped that inspection misses?** Absolute paths reach the artifact through
  generated files, cached state and default values. Only a machine with a different username, a
  different home, and no pre-existing directories finds them all.
- **Can the engine be installed without asking the user to?** A package manager they may not have,
  a binary download, or a documented one-liner — each fails differently on a machine that is not
  the author's.
- **Does the cold path survive a reader who is not the author?** The only test is watching someone
  else do it, and the honest failure mode is that they stop before the first verified step.

## Tasks

1. **Enumerate every path Cairn touches** and give each a resolution rule: the target repository
   (from the invocation), the worktrees root (derived from the repository), the engine binary (from
   acquisition), Cairn's state directory (from the platform's convention), and the plan document
   (from the invocation).
2. **Decide how an emitted step finds Cairn's CLI** ([05](05-step-kinds.md)) — a path resolved at
   generation time, or a name resolved from the step's environment at run time.
   [01](01-engine-spike.md) established that a step inherits the invoking process's `PATH`
   verbatim, so a bare name resolves under an interactive shell and would not under a
   service-managed scheduler, where the environment is minimal. Each fails differently: a resolved
   path breaks when the skill moves, a bare name breaks when the step's environment is not the
   shell's. State which is used, and make a workflow generated before an upgrade either keep
   working or fail loudly — never resolve to a different version silently.
3. **Own the engine's base configuration.** Write the fields Cairn depends on into it at install
   time — step concurrency, the shell, and above all a disabled DAG-level retry
   ([09](09-supervision.md)) — and verify them before a run rather than assuming them. The file
   already exists on any machine that has run `dagu` once, so acquisition edits rather than
   creates, and a user who installed the engine themselves has a different file whose difference is
   invisible in the emitted plan. On the pinned 2.18.2 the engine's first write of `base.yaml`
   leaves DAG-level retry commented out but arms `catchup_window: "6h"`, so on a fresh machine
   `lock acquire` passes and `cairn schedule start` refuses with `base_catchup_enabled` until
   `cairn supervise base-config --disable` runs ([41](41-dagu-2-18-upgrade.md) Outcome). The
   install path says so, or acquisition writes the setting itself.
4. **Claim the view's administrator account at acquisition**, or do not start a server. The two
   acceptable end states are a server whose admin account Cairn has created and handed to the user,
   and no server at all. An unclaimed running server is neither, and it is the state a naive
   "start the UI" produces.
5. **Emit against a machine-independent shell.** The engine resolves its interpreter from the
   user's `$SHELL`, so an emitted step runs under whatever that machine uses — `zsh` on the
   development machine, plausibly `bash` or `sh` elsewhere, with different glob and error semantics
   ([01](01-engine-spike.md)). Emitted steps are single invocations that behave identically under
   all three, or the shell is stated explicitly.
6. **Remove every author-shaped default.** Where a default is genuinely useful, derive it; where it
   cannot be derived, ask.
7. **Add a repository-wide guard**: a test that fails on an author-shaped literal — a home
   directory, a username, a hardcoded workspace root — anywhere in the source or in a generated
   fixture.
8. **Implement engine acquisition**: detect an installed engine, check its version against the pin,
   and install it if absent. A version mismatch halts clearly, naming both versions, rather than
   drifting the emitted format silently ([11](11-emitter-and-preflight.md)). **Pin, and say why**: the engine's
   input format is well governed, but its run-state file is an internal struct with integer enums,
   no schema and no conformance test, so the pin is what stops a reporter reading nonsense
   ([12](12-run-record.md)). Keeping current is a periodic manual judgement, not a subscription.
   The engine publishes per-release notes and, from 2.18.0, a signed release `packslip` beside
   `checksums.txt`; acquisition verifies the download against the release checksums at least, as
   the 2.18.2 move did ([41](41-dagu-2-18-upgrade.md)). The README's housekeeping guidance names
   the engine's own retention commands (`dagu rm` with a duration filter, `dagu prune-artifacts`)
   rather than Cairn inventing any ([41](41-dagu-2-18-upgrade.md) O7).
9. **State the acquisition path per platform**, and make the unsupported case an explicit, early,
   actionable message rather than a failure at first run.
10. **Write the README**, and let its shape be the deliverable rather than its length. It carries:
    what Cairn does in one sentence; the install path; the worked example; the posture section
    above, including the trust-boundary sentence; the licensing boundary; and the skill's measured
    installed context footprint ([15](15-the-skill.md)).
11. **Ship the worked example, and make it the failing one.** [08](08-verify-gate.md)'s
    differentiating fixture — an agent that reports success and writes nothing, is excluded with
    its cause, and leaves the run not-clean — is the example, because the claim is invisible on a
    happy path. A demo where everything works demonstrates nothing that distinguishes Cairn.
12. **Verify on a clean machine**: a fresh VM with a different username, no pre-existing
    directories, and nothing of the author's. Install, author a plan, run it, and land a verified
    step. Time it, and publish the number.
13. **Verify a generated artifact is honest about the machine it names**: a workflow generated on
    one machine is a per-machine build product by design, and the check is that re-authoring on
    the target machine produces one that runs there — not that the file carries no absolute path.
14. **Extract to a public repository** — a directory move plus a manifest, which is the whole of
    packaging. The rule that makes it cheap has held since the first commit: the skill directory is
    self-contained, nothing inside it path-references the rest of this repository, and nothing here
    reaches into it. Assert that with a test rather than trusting it.
15. **Replace the interpreter resolution [11](11-emitter-and-preflight.md) bakes into every
    generated file.** The emitted `env:` carries an absolute `PYTHONPATH` resolved on the authoring
    machine, and every body invokes the bare name `python3`, resolved from the step's `PATH`. So a
    generated workflow is bound to the machine that wrote it, and a `python3` older than the
    package needs fails to import Cairn — which the engine reads as a skipped precondition, so
    every step skips and the run reports a clean success. The preflight's rehearsal catches this at
    authoring time by running the invocation under the environment the file declares, but it
    borrows `PATH` from the authoring process, so it cannot see a run launched with a different
    one. This document owns the resolution that makes the file portable, and the minimum
    interpreter version, which nothing currently states.

## Release audit additions

The public tree still does not carry an installable cold path: `pyproject.toml` configures
Pyright but has no project metadata, build backend, or entry point, and the README assumes both
Dagu and an installed `/cairn` skill. Task 10 therefore includes prerequisites, skill
registration, and a discovery check that starts nothing, while tasks 14–15 include a real package manifest
and Python 3.11 as the explicit minimum.

The quality gate is also broader than the runtime suite:

- `python3 -m unittest discover -s tests -t .` currently passes 1,739 tests, but took about
  seventeen minutes in the audit rather than the README's stated eight;
- strict Pyright currently reports nine errors in `tests/test_run_record.py` and
  `tests/test_supervision.py`;
- the full run repeatedly emits unclosed-file `ResourceWarning`s;
- no checked-in CI workflow runs the suite and static checker together;
- the README's test count and missing-engine notes have drifted from the current suite.

Make test inventory generated or deliberately approximate, avoid promising a runtime without a
measured CI environment, document one common engine-test policy, close leaked subprocess streams,
and require both the suite and strict type checking in CI. [29](29-operator-and-report-ergonomics.md)
owns the user-facing journey; this document owns proving that journey on a clean machine.

## Exit criteria

- A plan runs end to end on a machine that has never held the author's files, verified by doing it
  rather than by reading the code, and the cold-start time is published.
- The author-shaped-literal guard passes and runs in CI, over source and generated fixtures.
- The full unit suite and strict Pyright pass in checked-in CI with no resource-leak warnings.
- The engine is installed by Cairn or by one documented command, and a version mismatch produces a
  clear halt rather than a silent format drift.
- The engine's base configuration carries Cairn's values on a machine where the user installed the
  engine themselves, asserted rather than assumed.
- No server Cairn starts is left with an unclaimed administrator account, asserted on the clean
  machine rather than reasoned about.
- The README states the trust boundary, the licensing boundary — neither of them
  softened.
- The worked example is the failing fixture, and a reader who runs it sees an exclusion with its
  cause.
- The skill directory is self-contained, asserted by a test, so extraction is a move.
- `pyproject.toml` states Python 3.11, install metadata, and the executable/skill acquisition
  path; the README's smoke path works from a fresh checkout.

## Depends on

[15](15-the-skill.md) — there must be a complete tool before it is de-authored — and
[research-dagu.md](research-dagu.md) for the licensing and schema-churn constraints.
