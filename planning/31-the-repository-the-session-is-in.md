# 31 — The repository the session is in is the answer unless something disagrees

`/cairn` was invoked from inside ariadne, over task IDs that exist only in ariadne's backlog.
Before anything else it asked which repository to use, and it named ariadne as the likely
answer while asking. That is the second time a person has met this wall.
[18 A](18-first-run-friction.md)
recorded the first, designed a fix, and has stayed open. This document takes that section
over, adds the case 18 did not foresee, and sets out when a question is still owed.

**Serves** every capability. A person working in a repository can run, author, report on or
schedule its plans without typing its path. They still see which repository was taken, in the
first line of every reply that acts on one. The repository is still never inferred from the
workflow.

## What a person hit

The session's reply, quoted:

> **What I need from you: which repository?** Cairn takes the target repository from the
> request and never assumes it from the directory we're in. The tasks are in `ariadne`'s
> backlog, so the likely answer is `/Users/chuck/workspace/ariadne` […] Please confirm that,
> or name another repository or branch.

The agent was following the rule exactly. `SKILL.md` says the repository "comes from the
request, for every capability, always … never defaulted to the directory this conversation is
in". The fixture `repository-absent` pinned that behaviour "even where only one repository is
in play". The question was not the agent's mistake. The rule produced it.

## Why the rule is too wide, and what it still protects

18 A's argument still holds. Three things derive from the repository path and all three fail
quietly when the path is wrong: the run lock (I6), the `<repo>-worktrees` parent, and the
definition's encoded repository. None of them is weakened here. Those three are protected by
getting the repository right, and a question does not do that. When the request's own
subjects and the session agree, they name the repository more reliably than a person
retyping a path. Asking is what helps when they disagree, and those are exactly the cases
listed below. Report and Explain change nothing, so a wrong guess there prints an empty
listing that names the repository it looked in.

## The change

**Resolve the repository in this order, and record where it came from.**

1. A repository named in the request. Provenance: `stated`.
2. The git root holding the request's subjects: the plan document, or, for a plan stated in
   the request ([30](30-a-plan-stated-in-the-request.md)), the task documents it names.
   Provenance: `subjects`. 18 A covered only a plan document on disk, so this case is new. In
   the ariadne session the subjects were six task IDs, all in one backlog.
3. The git root of the session's working directory. Provenance: `session`.

**Ask only when the candidates leave real doubt.** A question is owed when:

- the subjects resolve in more than one repository, or in none that can be found;
- the subjects resolve in one repository and the session sits in another;
- the session is not inside a git repository and nothing else gave one;
- the only candidate is Cairn's own checkout and Cairn's plans are not the subject. This is
  the trap 18 A warned about: the capability documents run `python3 -m cairn` from the skill
  directory, so the process working directory is never evidence. The session's directory is
  passed in from the skill layer and never read with `os.getcwd()`;
- a definition exists for the workflow and encodes a different repository. This is the
  existing encoded-or-re-author question, two answers and no third, and it is unchanged.

Outside those cases there is no question.

**Show it where it is accepted.** Run and Schedule name the resolved repository and its
provenance in the first line of their reply, before the run id. Report and Explain name the repository they read
in their first line. Authoring names it in the parse report.

**What must not change.** The repository is still never inferred from the workflow. The
absolute-path refusal and the worktrees-spelling refusal still run before anything starts. A
repository named in the request still beats every inference, including one that disagrees
with it. When it disagrees with where the subjects live, that disagreement is itself one of
the questions above.

**Touches.** `cairn/skill/resolve.py` (`resolve_repository` takes the subject roots and the
session directory, and returns a provenance with the path), `cairn/skill/cli.py:326` and
`cairn/report/cli.py:51` (their "never the directory" comments and parameters), `SKILL.md`
_The target repository_, the preconditions rows of `capabilities/authoring.md`,
`running.md` and `scheduling.md`, and `running.md` step 2,
`fixtures/invocations/cases.json` and its README (`repository-absent` becomes `resolved` with
provenance `session`, and one new case for each ask above), `tests/test_the_skill.py`
(around 1382–1400), `tests/test_report.py` (around 1103).

## Acceptance

- The ariadne request in [30](30-a-plan-stated-in-the-request.md), made from inside ariadne,
  reaches a parse report and then a started run without a repository question. The reply names
  `/Users/chuck/workspace/ariadne` with provenance `subjects`.
- The same request made from Cairn's own checkout asks, and the question names ariadne as the
  repository the tasks were found in.
- A report requested from inside a repository finds its runs, and its first line names that
  repository.
- Every ask case above has a fixture and asks. Every other repository fixture resolves
  without a question.
- No document, comment, fixture or test still states that the session's directory is never a
  default.

## Close-out

Done. Every criterion above holds, and the section 18 A left open is closed here.

A person working inside a repository runs, authors, reports on or schedules its plans without
typing its path. The repository is resolved from three candidates, strongest first — one named
in the request (`stated`), the git root holding the request's subjects (`subjects`), the git
root of the session's directory (`session`) — and the candidate that answered is named beside
the path in the first line of every reply that acts on one. The session's directory arrives as
`--session`, never as the process's own: the capability documents run `python3 -m cairn` from
the skill's directory, so reading it would answer "Cairn's checkout" to every question.

A question is owed only where the candidates that were found disagree or where none was found:
subjects spread across repositories or in none, subjects in one repository with the session in
another, a session in no repository and nothing else to go on, Cairn's own checkout as the only
candidate when Cairn's plans are not the subject, and a definition that encodes a different
repository. Each of those five is a fixture in the `repository` family of
`fixtures/invocations/cases.json` that asks, each of the three provenances is a fixture there
that resolves without asking, and the suite holds the family to covering every question the
resolution can owe and every candidate it can take. A repository named in the request still
beats every inference except the request's own subjects, and the absolute-path and
worktrees-spelling refusals still run before anything starts.
