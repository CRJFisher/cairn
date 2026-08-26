# 16 — Release: acquisition, posture, and a README that ends in a verified step

The last document. Nothing in the artifact is shaped like its author, the engine is obtained
rather than operated, the security posture is stated plainly, and a second person can go from
nothing to a verified step by reading one file. The test is a fresh machine, not an inspection.

**Serves** D1, D5 and D6.

This is the whole of delivery: compliance, packaging and onboarding together. Cairn is a git
repository with a README, published because it might help someone, with no launch sequence and
no adoption target. So packaging is a directory move plus a manifest, onboarding is a cold path
that has to be walked by someone who is not the author before it counts as a fact, and the
posture is here because it is measured and load-bearing.

## The claims

**Every path is resolved at install or invocation.** No hardcoded home directory, no assumed
repository location, no dependency on a configuration layout only one person has. This includes
paths that reach the artifact indirectly — a generated workflow encoding the generating machine's
absolute paths is author-shaped even when the source is not.

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
   invisible in the emitted plan.
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
   ([12](12-run-record.md)). Keeping current is a periodic manual judgement, not a subscription —
   there is no CHANGELOG to watch.
9. **State the acquisition path per platform**, and make the unsupported case an explicit, early,
   actionable message rather than a failure at first run.
10. **Write the README**, and let its shape be the deliverable rather than its length. It carries:
    what Cairn does in one sentence; the install path; the worked example; the posture section
    above, including the trust-boundary sentence; the licensing boundary; what a run costs, using
    the measured utilization figure a full run consumes ([02](02-agent-step-spike.md)) and the note
    that a subscription cost figure is notional rather than money spent; and the skill's measured
    installed context cost ([15](15-the-skill.md)).
11. **Ship the worked example, and make it the failing one.** [08](08-verify-gate.md)'s
    differentiating fixture — an agent that reports success and writes nothing, is excluded with
    its cause, and leaves the run not-clean — is the example, because the claim is invisible on a
    happy path. A demo where everything works demonstrates nothing that distinguishes Cairn.
12. **Verify on a clean machine**: a fresh VM with a different username, no pre-existing
    directories, and nothing of the author's. Install, author a plan, run it, and land a verified
    step. Time it, and publish the number.
13. **Verify the generated artifacts are portable too**: a workflow generated on one machine is
    inspected for absolute paths that only exist on it.
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

## Exit criteria

- A plan runs end to end on a machine that has never held the author's files, verified by doing it
  rather than by reading the code, and the cold-start time is published.
- The author-shaped-literal guard passes and runs in CI, over source and generated fixtures.
- The engine is installed by Cairn or by one documented command, and a version mismatch produces a
  clear halt rather than a silent format drift.
- The engine's base configuration carries Cairn's values on a machine where the user installed the
  engine themselves, asserted rather than assumed.
- No server Cairn starts is left with an unclaimed administrator account, asserted on the clean
  machine rather than reasoned about.
- The README states the trust boundary, the licensing boundary, and what a run costs — none of them
  softened.
- The worked example is the failing fixture, and a reader who runs it sees an exclusion with its
  cause.
- The skill directory is self-contained, asserted by a test, so extraction is a move.

## Depends on

[15](15-the-skill.md) — there must be a complete tool before it is de-authored — and
[research-dagu.md](research-dagu.md) for the licensing and schema-churn constraints.
