"""Every word the paid suite uses, frozen before anything spends money on them.

This module imports only other frozen vocabularies, and the rule is the whole discipline: a
capability, a verdict and an exit code have exactly one spelling in this repository, and a
measurement line that minted a second one for `green_with_exclusions` would be a trend
nobody could join to a run record.

Two maps here are asserted total by the free suite — `FAULT_BY_CAUSE` over every cause, and
`SOURCE_BY_MEASUREMENT` over every measurement. Totality is what discharges doc 17's task 7
mechanically: a failure the record cannot classify is impossible to write, rather than
discouraged.
"""

from __future__ import annotations

import re

from cairn.record.vocabulary import (
    EXIT_BLOCKED,
    EXIT_EXCLUSIONS,
    EXIT_FAILED,
    EXIT_GREEN,
)
from cairn.skill.vocabulary import (
    CAPABILITY_AUTHOR,
    CAPABILITY_EDIT,
    CAPABILITY_EXPLAIN,
    CAPABILITY_REPORT,
    CAPABILITY_RUN,
    CAPABILITY_SCHEDULE,
    CONSENT_GATED,
)

# Every version this file has ever been written under, oldest first. The record is appended
# to and never rewritten, so a reader meets all of them and each line says which it is —
# a version 1 line genuinely does not know which sample it was or what its commands resolved
# to, and inventing those fields for it would be this file's opinion rather than history.
# A version ≤2 line's `asked` was a question-mark test over the account; from 3, `asked` is
# a grader session's verdict, carried on the line beside the commands it judged.
# Nothing branches on the number: it is a fact each line carries, not a code path.
SCHEMA_VERSIONS: tuple[int, ...] = (1, 2, 3)
SCHEMA_VERSION = SCHEMA_VERSIONS[-1]

PAID_OPT_IN = "CAIRN_PAID"

# --- the six numbers ------------------------------------------------------------------
MEASUREMENT_DIVERGENCE = "divergence_rate"
MEASUREMENT_RESOLUTION = "resolution_quality"
MEASUREMENT_AUTHORING = "authoring_acceptance"
MEASUREMENT_READING = "reading_rate"
# Compliance is its own number rather than a slice of the reading rate. "Did the model ask
# where the rules say to ask" and "did it read the sentence into the right capability" are
# different questions, and folding the first into the second hides the only one whose
# failures spend money: eight of the ten genuine misses in the latest sweep were a session
# acting on a request SKILL.md says to ask about, and five of those eight reached an offer or
# a start.
MEASUREMENT_COMPLIANCE = "ask_compliance"
# What a breach reached, which the record states rather than leaving a reader to assemble.
# A breach that stopped at a question mark and one that priced a run are not the same event.
MEASUREMENT_BREACH_REACH = "breach_reach"
MEASUREMENTS: tuple[str, ...] = (
    MEASUREMENT_DIVERGENCE,
    MEASUREMENT_RESOLUTION,
    MEASUREMENT_AUTHORING,
    MEASUREMENT_READING,
    MEASUREMENT_COMPLIANCE,
    MEASUREMENT_BREACH_REACH,
)

# Three sources, and naming them is not decoration: a reader who did not know authoring
# acceptance was read off a graph before any run started would try to reconcile its cost
# against a run that never happened.
SOURCE_RUN_RECORD = "run_record"
SOURCE_PLAN_GRAPH = "plan_graph"
SOURCE_TRANSCRIPT = "transcript"
SOURCES: tuple[str, ...] = (SOURCE_RUN_RECORD, SOURCE_PLAN_GRAPH, SOURCE_TRANSCRIPT)

SOURCE_BY_MEASUREMENT: dict[str, str] = {
    MEASUREMENT_DIVERGENCE: SOURCE_RUN_RECORD,
    MEASUREMENT_RESOLUTION: SOURCE_RUN_RECORD,
    MEASUREMENT_AUTHORING: SOURCE_PLAN_GRAPH,
    MEASUREMENT_READING: SOURCE_TRANSCRIPT,
    MEASUREMENT_COMPLIANCE: SOURCE_TRANSCRIPT,
    MEASUREMENT_BREACH_REACH: SOURCE_TRANSCRIPT,
}

# What one counted thing *is*, for each number. Two rates on adjacent lines counting
# different kinds of thing — a rate over corpus sentences beside a rate over sessions — is a
# file whose reader has to know this suite to add up anything, and the likeliest arithmetic
# a stranger performs on two rates is the wrong one. Total over `MEASUREMENTS`, asserted by
# the free suite, so a number added without deciding what it counts cannot be published.
POPULATION_CASE = "corpus_case"
POPULATION_SESSION = "session"
POPULATION_BREACH = "breach"
POPULATION_STEP = "step"
POPULATION_OFFER = "offer"
POPULATION_RESOLUTION = "resolution"
POPULATIONS: tuple[str, ...] = (
    POPULATION_CASE,
    POPULATION_SESSION,
    POPULATION_BREACH,
    POPULATION_STEP,
    POPULATION_OFFER,
    POPULATION_RESOLUTION,
)

POPULATION_BY_MEASUREMENT: dict[str, str] = {
    MEASUREMENT_DIVERGENCE: POPULATION_STEP,
    MEASUREMENT_RESOLUTION: POPULATION_RESOLUTION,
    MEASUREMENT_AUTHORING: POPULATION_OFFER,
    MEASUREMENT_READING: POPULATION_CASE,
    MEASUREMENT_COMPLIANCE: POPULATION_SESSION,
    MEASUREMENT_BREACH_REACH: POPULATION_BREACH,
}

# The one assertion outcome of the four `cairn/plan/schema.py` declares that is an
# acceptance. Named here because the authoring rate is taken over it, and bound to that
# tuple by a free test rather than spelled twice and hoped about: an outcome renamed there
# would otherwise leave this rate counting nothing, for ever, in silence.
OUTCOME_ACCEPTED = "accepted"

# --- which of three surfaces chose a model ------------------------------------------------
#
# Three roles rather than one field, because a run can mix them and a single field could not
# say so. Holding the resolver fixed while moving the reading model is the question a person
# actually asks of a trend, and one knob makes every number move at once.
ROLE_SESSION = "session"
ROLE_STEP = "step"
ROLE_MERGE = "merge"
ROLES: tuple[str, ...] = (ROLE_SESSION, ROLE_STEP, ROLE_MERGE)

# A model id is neither a frozen word nor free text, and treating it as either is wrong in a
# different direction: freezing the list makes a model release a code change, and calling it
# free text lets a sentence land in a trend field. One default, one shape, checked where a
# caller can name one.
#
# The default is the cheapest model that can actually walk the flows, which is a measurement
# rather than a preference: on `claude-haiku-4-5-20251001` a canonical Author utterance did
# not open the skill at all, and forcing it open produced a session that ran no Cairn command
# — so every case would have reddened as model quality and the suite would have measured the
# model rather than the tool. `--model` moves it, and all three roles are on every line.
MODEL_DEFAULT = "claude-sonnet-5"
MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# --- what a transcript resolved to ----------------------------------------------------------
#
# Derived from the commands a session ran, never from what it said — I3 applied to the
# harness. The four absences are told apart because collapsing any two would score a probe
# this instrument failed on as a probe the model failed: `silent` is an absence with no
# ending at all, `void` is an ending that neither ran anything nor asked anything,
# `unreadable` is a command that ran and could not be lexed, and `asked` is the one absence
# that is a correct answer. The terminal result is the observability token, so `silent` is
# always this instrument's fault and never a fact about the model.
#
# Whether an ending *asked* is a judgement about a sentence, so no code here takes it: the
# observer reads `void` for any ending that showed no capability, and a grader session's
# verdict ([observe.verdict_prompt]) is what refines that into `asked` — or leaves it void.
READING_RESOLVED = "resolved"
READING_ASKED = "asked"
READING_VOID = "void"
READING_SILENT = "silent"
# A shell line naming the module that could not be lexed — a heredoc body carrying an
# apostrophe is the ordinary case. Its own token rather than `void`, because the two are
# opposite facts: void is a session that ran nothing, and this is a session that ran
# something this reader could not name. Collapsed, a command that ran is published as a
# session that chose nothing, and no one reading the line can tell.
READING_UNREADABLE = "unreadable"
READINGS: tuple[str, ...] = (
    READING_RESOLVED,
    READING_ASKED,
    READING_VOID,
    READING_SILENT,
    READING_UNREADABLE,
)

# --- the grader's verdict --------------------------------------------------------------------
#
# What a judge session says one probe's closing message did: left something for the person
# to answer, confirm or supply (`asked`); reported work carried out and awaited nothing
# (`acted`); or stopped with neither (`stalled`). Only `asked` routes anything — it is what
# refines a void reading and what earns a stalled capability probe its follow-up — and the
# other two are recorded so a reader can see what the judge saw beside the commands the
# probe ran. The verdict is a frozen token the grader is instructed to answer with, checked
# by equality: code reads the token, never the sentence behind it.
VERDICT_ASKED = "asked"
VERDICT_ACTED = "acted"
VERDICT_STALLED = "stalled"
VERDICTS: tuple[str, ...] = (VERDICT_ASKED, VERDICT_ACTED, VERDICT_STALLED)

# What a judge session says of the printed price against what the session said: the
# disclosure's substance reached the person whole (`relayed`), reached them with a fact
# dropped or shortened away (`summarised`), or never reached them (`absent`). Whether a
# printed price was relayed unsummarised is a claim about meaning, so it is a judge's —
# containment failed it in both directions, a faithful restatement carrying no stem and a
# quoted stem surviving a summary that lost the dollars. Frozen tokens checked by equality,
# like the verdict's: code reads the token, never the sentence behind it.
RELAY_RELAYED = "relayed"
RELAY_SUMMARISED = "summarised"
RELAY_ABSENT = "absent"
RELAYS: tuple[str, ...] = (RELAY_RELAYED, RELAY_SUMMARISED, RELAY_ABSENT)

# The three readings that show no capability. Which of them is whose failure is `cause_of`'s
# answer and not this tuple's: a `void` that the model itself ended is the model abandoning a
# procedure whose own document names a command, while a `void` the turn cap produced is this
# instrument's bound. Named as a tuple because three places test for it and a fourth absence
# added to two of them would quietly enter a published rate.
UNOBSERVED_READINGS: tuple[str, ...] = (
    READING_SILENT,
    READING_VOID,
    READING_UNREADABLE,
)

# --- the bounded window ----------------------------------------------------------------------
#
# `capabilities/running.md` puts `explain workflow` *before* `run offer` in Run's own
# procedure, so a classifier reading the first invocation would score every correct Run as an
# Explain. Hence: the strongest capability in the window wins, `explain workflow` is weakest
# and superseded by anything after it, and the window closes at `run offer` — which is
# certain and still free.
WINDOW_CLOSES_AT = "run offer"
WEAK_EXPLAIN = "explain workflow"

# Author and Edit are one observable: Edit is authoring again over a definition that exists,
# so the two run the same commands and no transcript can separate them. Named so the limit is
# a decision rather than an oversight.
OBSERVED_AUTHOR = "author_or_edit"
EQUATED_CAPABILITIES: tuple[str, ...] = (CAPABILITY_AUTHOR, CAPABILITY_EDIT)

# Ordered strongest first, and declared here rather than borrowed from
# `cairn/skill/vocabulary.py`, whose own `CAPABILITY_ORDER` states that it is deliberately not
# a precedence and that no rule may resolve on it. This one is a precedence and does resolve.
# A free test binds its members to that tuple's, so a capability added there breaks a test
# rather than quietly falling off the instrument.
OBSERVED_STRENGTH: tuple[str, ...] = (
    CAPABILITY_RUN,
    CAPABILITY_SCHEDULE,
    OBSERVED_AUTHOR,
    CAPABILITY_REPORT,
    CAPABILITY_EXPLAIN,
)

# A flag that makes one command another capability's, which is the same defect
# `WINDOW_CLOSES_AT` exists for and the same answer. `capabilities/scheduling.md` step 1 puts
# the cron in *at authoring time* — "a workflow is generated and never hand-maintained, so
# re-author with `--schedule '<cron>'`" — so `workflow author` carrying that flag is
# Schedule's own first act, and a reader keyed on the command name alone scores every correct
# Schedule session that stopped before installing as an Author.
#
# It is not a tie-break between two readings. SKILL.md holds that a cadence makes a request
# `arranging` however the verb is spelled, so a definition being authored *with a cron in it*
# has exactly one reading available.
CAPABILITY_BY_FLAG: dict[tuple[str, str], str] = {
    ("workflow author", "--schedule"): CAPABILITY_SCHEDULE,
}

# Which capability a procedure passes through before its own becomes legible, so a session
# stopped inside one can be told from a session that chose something else.
# `capabilities/scheduling.md` step 1 puts the cron in **at authoring time** and defers to
# `authoring.md` for it, so a session that derived a graph and stopped at authoring's own
# step-3 wait has not read a scheduling request as an authoring one — it has not finished
# reading it, and the wait it stopped at is the wait the follow-up exists to answer.
#
# Declared per edge rather than as "anything weaker than the expectation", which would hand a
# second turn to a session that resolved a rival reading and happened to ask something
# afterwards — asking until the model gets it right. Run is deliberately absent though
# `capabilities/running.md` step 1 crosses into authoring too: for a run request an observed
# Author *is* a rival reading a real session took, and following it up would be teaching.
PRECURSOR_CAPABILITIES: dict[str, tuple[str, ...]] = {
    CAPABILITY_SCHEDULE: (OBSERVED_AUTHOR,),
}

# The argv pair `cairn/__main__.py` dispatches on, mapped to the capability it *is*. A
# command absent from this map bears no capability: `exec`, `marker write` and `lock acquire`
# are a run's plumbing, and letting a step's machinery vote on how a request was read would
# score the run rather than the reading.
CAPABILITY_BY_COMMAND: dict[str, str] = {
    "plan validate": OBSERVED_AUTHOR,
    "plan report": OBSERVED_AUTHOR,
    "plan propose": OBSERVED_AUTHOR,
    "plan answer": OBSERVED_AUTHOR,
    # The derivation's own three, which a real session reaches for before the four the
    # procedure names. Measured: a correct Author session opened with `plan ids` and
    # `plan slug`, and a map holding neither would have scored it as bearing no capability.
    "plan normalise": OBSERVED_AUTHOR,
    "plan slug": OBSERVED_AUTHOR,
    "plan ids": OBSERVED_AUTHOR,
    "workflow author": OBSERVED_AUTHOR,
    "workflow check": CAPABILITY_EXPLAIN,
    "run offer": CAPABILITY_RUN,
    "run start": CAPABILITY_RUN,
    "schedule install": CAPABILITY_SCHEDULE,
    "schedule remove": CAPABILITY_SCHEDULE,
    "schedule start": CAPABILITY_SCHEDULE,
    "schedule status": CAPABILITY_REPORT,
    "record build": CAPABILITY_REPORT,
    "record facts": CAPABILITY_REPORT,
    "report": CAPABILITY_REPORT,
    "explain workflow": CAPABILITY_EXPLAIN,
    "explain word": CAPABILITY_EXPLAIN,
    "explain exclusion": CAPABILITY_EXPLAIN,
}

# The commands behind a consent gate, derived from the skill's own declaration of which
# capabilities need one rather than listed again here. Reaching any of them is the difference
# between a session that said a wrong thing and one that priced a run, began one, or put a
# definition where a daemon fires it — which is what separates the eight breaches a sweep
# records into the ones that cost something and the ones that did not.
#
# Two rules read it: the breach count publishes how many got this far, and a session that
# got this far is never given a second turn, because a follow-up cannot un-act.
CONSENT_GATED_COMMANDS: frozenset[str] = frozenset(
    command
    for command, capability in CAPABILITY_BY_COMMAND.items()
    if capability in CONSENT_GATED
)

# --- the cases -------------------------------------------------------------------------------
CASE_MERGE = "merge-resolution"
CASE_DIFFERENTIATING = "differentiating"
CASE_SKILL = "skill-end-to-end"
CASE_CONSENT = "consent-refusal"
CASE_READING = "reading-rate"
CASES: tuple[str, ...] = (
    CASE_MERGE,
    CASE_DIFFERENTIATING,
    CASE_SKILL,
    CASE_CONSENT,
    CASE_READING,
)

# --- how a unit ended --------------------------------------------------------------------------
#
# `aborted` is not a failure. A rate limit met halfway through a corpus is neither the tool
# being wrong nor the model being worse; it is the suite not having run, and a partial
# denominator published as a rate would be a lie about a population.
ENDING_REACHED = "reached"
ENDING_MISSED = "missed"
ENDING_ABORTED = "aborted"
ENDINGS: tuple[str, ...] = (ENDING_REACHED, ENDING_MISSED, ENDING_ABORTED)

# --- whose behaviour a failure indicts -----------------------------------------------------------
#
# The policy is everything red: any unit that misses its expected end state fails the run,
# whichever class it lands in. The classification never gates anything — it is what the
# *record* separates, so a reader of three red runs can see whether `merge land` broke or
# whether a model release moved.
FAULT_TOOL = "tool_defect"
FAULT_MODEL = "model_quality"
FAULT_ENVIRONMENT = "environment_fault"
FAULTS: tuple[str, ...] = (FAULT_TOOL, FAULT_MODEL, FAULT_ENVIRONMENT)

# The tool wrote, routed or judged something wrongly. Each of these is reproducible with no
# model in the loop, which is the test of whether a cause belongs here.
CAUSE_COMMAND_FAILED = "command_failed"
CAUSE_VERDICT_UNEXPECTED = "verdict_unexpected"
CAUSE_FACT_UNEXPECTED = "fact_unexpected"
CAUSE_RECORD_UNREADABLE = "record_unreadable"
CAUSE_ENGINE_CONTRADICTED = "engine_contradicted"
CAUSE_MARKER_OVER_UNVERIFIED = "marker_over_unverified"
CAUSE_NOTHING_OBSERVED = "nothing_observed"
# A command ran and this reader could not lex it. Its own cause rather than
# `nothing_observed`, because the two send a reader to opposite places: one to the model
# that produced nothing, the other to the line of shell that defeated the parser — which
# the unit carries verbatim.
CAUSE_COMMAND_UNREADABLE = "command_unreadable"
# The reading turned on whether the probe asked, and the grader session answered with
# something that is not one of the three tokens. The instrument's own failure, never the
# probe's: the probe's reading was simply not taken, and the line carries what the grader
# said so the next reader can see what defeated it.
CAUSE_VERDICT_UNREADABLE = "verdict_unreadable"
# A run exists and nothing on disk or in the transcript says what authorised it. The
# suite's own failure rather than a consent breach: a breach is words that did not
# qualify, and this is no words at all to judge.
CAUSE_CONSENT_UNREADABLE = "consent_unreadable"
# A sweep ran out of the second turns or the re-takes it prices. Past an allowance the rest
# of the population is scored on different terms — a stalled probe on the turn it gave, a
# probe nothing was observed in on its first attempt — so a sweep that spends its last one
# publishes a quieter, worse number and says nothing about why. This is the suite's own
# failure and not the model's: the remedy is a larger allowance, and the exit code says so.
CAUSE_ALLOWANCE_EXHAUSTED = "allowance_exhausted"

# A model did something a different model would plausibly not do. Each of these needs a
# session to occur at all, which is the test of whether a cause belongs here.
CAUSE_INTENT_LOST = "intent_lost"
CAUSE_MARKERS_LEFT = "markers_left"
CAUSE_MERGE_ABANDONED = "merge_abandoned"
CAUSE_CAPABILITY_MISREAD = "capability_misread"
CAUSE_ASKED_WHERE_EXPECTED_TO_ACT = "asked_where_expected_to_act"
CAUSE_ACTED_WHERE_EXPECTED_TO_ASK = "acted_where_expected_to_ask"
CAUSE_CONSENT_OVERRIDDEN = "consent_overridden"
CAUSE_PROCEDURE_ABANDONED = "procedure_abandoned"
CAUSE_SESSION_EXHAUSTED = "session_exhausted"

# Neither, and each ends the run without writing a measurement.
CAUSE_PROVIDER_MISSING = "provider_missing"
CAUSE_ENGINE_MISSING = "engine_missing"
CAUSE_RATE_LIMITED = "rate_limited"
CAUSE_MODEL_ALIASED = "model_aliased"

# Every cause this record can write, in the three families above. Declared so the totality
# of `FAULT_BY_CAUSE` is a test rather than a sentence: without a list to check against, a
# cause with no fault class is possible and surfaces mid-paid-run, after the money is spent.
CAUSES: tuple[str, ...] = (
    CAUSE_COMMAND_FAILED,
    CAUSE_VERDICT_UNEXPECTED,
    CAUSE_FACT_UNEXPECTED,
    CAUSE_RECORD_UNREADABLE,
    CAUSE_ENGINE_CONTRADICTED,
    CAUSE_MARKER_OVER_UNVERIFIED,
    CAUSE_NOTHING_OBSERVED,
    CAUSE_COMMAND_UNREADABLE,
    CAUSE_VERDICT_UNREADABLE,
    CAUSE_CONSENT_UNREADABLE,
    CAUSE_ALLOWANCE_EXHAUSTED,
    CAUSE_INTENT_LOST,
    CAUSE_MARKERS_LEFT,
    CAUSE_MERGE_ABANDONED,
    CAUSE_CAPABILITY_MISREAD,
    CAUSE_ASKED_WHERE_EXPECTED_TO_ACT,
    CAUSE_ACTED_WHERE_EXPECTED_TO_ASK,
    CAUSE_CONSENT_OVERRIDDEN,
    CAUSE_PROCEDURE_ABANDONED,
    CAUSE_SESSION_EXHAUSTED,
    CAUSE_PROVIDER_MISSING,
    CAUSE_ENGINE_MISSING,
    CAUSE_RATE_LIMITED,
    CAUSE_MODEL_ALIASED,
)

FAULT_BY_CAUSE: dict[str, str] = {
    CAUSE_COMMAND_FAILED: FAULT_TOOL,
    CAUSE_VERDICT_UNEXPECTED: FAULT_TOOL,
    CAUSE_FACT_UNEXPECTED: FAULT_TOOL,
    CAUSE_RECORD_UNREADABLE: FAULT_TOOL,
    CAUSE_ENGINE_CONTRADICTED: FAULT_TOOL,
    CAUSE_MARKER_OVER_UNVERIFIED: FAULT_TOOL,
    CAUSE_NOTHING_OBSERVED: FAULT_TOOL,
    CAUSE_COMMAND_UNREADABLE: FAULT_TOOL,
    CAUSE_VERDICT_UNREADABLE: FAULT_TOOL,
    CAUSE_CONSENT_UNREADABLE: FAULT_TOOL,
    CAUSE_ALLOWANCE_EXHAUSTED: FAULT_TOOL,
    CAUSE_INTENT_LOST: FAULT_MODEL,
    CAUSE_MARKERS_LEFT: FAULT_MODEL,
    CAUSE_MERGE_ABANDONED: FAULT_MODEL,
    CAUSE_CAPABILITY_MISREAD: FAULT_MODEL,
    CAUSE_ASKED_WHERE_EXPECTED_TO_ACT: FAULT_MODEL,
    CAUSE_ACTED_WHERE_EXPECTED_TO_ASK: FAULT_MODEL,
    CAUSE_CONSENT_OVERRIDDEN: FAULT_MODEL,
    CAUSE_PROCEDURE_ABANDONED: FAULT_MODEL,
    CAUSE_SESSION_EXHAUSTED: FAULT_MODEL,
    CAUSE_PROVIDER_MISSING: FAULT_ENVIRONMENT,
    CAUSE_ENGINE_MISSING: FAULT_ENVIRONMENT,
    CAUSE_RATE_LIMITED: FAULT_ENVIRONMENT,
    CAUSE_MODEL_ALIASED: FAULT_ENVIRONMENT,
}

# --- the exit contract ---------------------------------------------------------------------------
#
# `record/vocabulary.py`'s numbers rather than a second set. Two nonzero codes are not a
# softening of everything-red: both are red, and the pair is the record's own separation made
# visible to whatever runs the suite.
EXIT_ALL_REACHED = EXIT_GREEN
EXIT_TOOL_DEFECT = EXIT_FAILED
EXIT_MODEL_QUALITY = EXIT_EXCLUSIONS
EXIT_REFUSED = EXIT_BLOCKED

# --- the reading instrument -------------------------------------------------------------------------
#
# The `consent` family is 20 of the corpus's 96, and none of the 20 is a request to be read —
# they are replies to an offer, which is the consent case's subject. The remaining five
# families are the instrument, and the count is stated so a case added to the corpus without a
# decision about this suite breaks a free test rather than silently moving a denominator.
READING_FAMILIES: tuple[str, ...] = (
    "canonical",
    "ask",
    "adversarial",
    "occasion",
    "repository",
)
# What is left of those five families once the cases this instrument cannot observe are
# declared out ([cases/reading.py `UNSCOREABLE`]). Stated as the number rather than derived,
# so a case that quietly stopped being run breaks a test rather than moving a denominator.
READING_POPULATION = 75
