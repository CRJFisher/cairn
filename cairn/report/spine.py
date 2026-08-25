"""The report's own frozen contract: six sections in one order, and the shapes they hold.

Doc 12 froze the vocabulary before anything read the record; this module does the same one
level up, because the same failure is available here. Three renderings of one run drift the
moment each decides for itself what to say and in what order, and the drift is invisible —
two documents that disagree about a fact both look fine on their own.

So the order is the design. A reader has one question first and it is never "what was the
topology": did it work, what do I do next, what needs my attention, what did each step do,
what shape was the run, what are the receipts. `SECTIONS` **is** that order — a tuple rather
than an enumeration plus a separate ranking, for the reason `vocabulary.py` gives: two
structures drift.

Nothing here imports anything of Cairn's, and nothing here renders. A renderer is handed the
document this vocabulary describes and never the record, so it cannot reach a fact the
composition did not place, cannot re-order what it was given, and cannot decide a verdict.
"""

from __future__ import annotations

from typing import Literal, NamedTuple


class Question(NamedTuple):
    """One section: the reader's question, the heading, and what it says when it has nothing.

    `nothing` is not decoration. A section that vanished when it was empty would leave a
    reader unable to tell "nothing needs your attention" from "this rendering does not carry
    attention", and the trust claim is that all six questions are answered from the report
    alone. So every section appears in every rendering of every run, and an empty one says so
    in its own words.
    """

    key: str
    heading: str
    question: str
    nothing: str


SECTION_VERDICT = "verdict"
SECTION_NEXT = "next"
SECTION_ATTENTION = "attention"
SECTION_STEPS = "steps"
SECTION_SHAPE = "shape"
SECTION_RECEIPTS = "receipts"

SECTIONS: tuple[Question, ...] = (
    Question(
        SECTION_VERDICT,
        "Did it work",
        "did it work",
        "This run recorded no verdict.",
    ),
    Question(
        SECTION_NEXT,
        "What to do next",
        "what do I do next",
        "There is nothing to do.",
    ),
    Question(
        SECTION_ATTENTION,
        "What needs attention",
        "what needs my attention",
        "Nothing needs your attention.",
    ),
    Question(
        SECTION_STEPS,
        "What each step did",
        "what did each step do",
        "This run recorded no step.",
    ),
    Question(
        SECTION_SHAPE,
        "What shape the run was",
        "what shape was the run",
        "This run recorded no graph.",
    ),
    Question(
        SECTION_RECEIPTS,
        "Receipts",
        "what are the receipts",
        "No step reported a cost, a session or a transcript.",
    ),
)

SECTION_ORDER: tuple[str, ...] = tuple(section.key for section in SECTIONS)

# How loudly a headline reads. Chosen once, from the frozen verdict, so that no rendering
# decides for itself how serious a run was — and so that a sink with no colour carries the
# same weighting as one with it.
TONE_ALARM = "alarm"
TONE_CAUTION = "caution"
TONE_PLAIN = "plain"

# How a fact becomes text. The rule is declared at the leaf and applied by whichever sink is
# rendering, so all three spell one fact one way and the oracle can assert they did.
RULE_VALUE = "value"
RULE_MONEY = "money"
RULE_ACTOR = "actor"
RULE_LINK = "link"
RULE_ASSERTION = "assertion"
RULES: tuple[str, ...] = (
    RULE_VALUE,
    RULE_MONEY,
    RULE_ACTOR,
    RULE_LINK,
    RULE_ASSERTION,
)


class Chrome(NamedTuple):
    """Cairn's own words: a label, a heading, a sentence. Never a fact about the run."""

    text: str


class Fact(NamedTuple):
    """One fact of the run, named by its projection key rather than carried as text.

    A leaf holds the key and not the value, so the value is looked up once at render time
    through the one function that also logs it. That log is what makes "every rendering
    agrees with the projection" an assertion rather than a hope.

    `keys` is a tuple because one shown string can rest on more than one fact — a cost and
    whether it is notional are one sentence, and a rendering that could drop half of it would
    be reporting money the run never spent.
    """

    keys: tuple[str, ...]
    rule: str = RULE_VALUE


Cell = Chrome | Fact


class Headline(NamedTuple):
    """The run's verdict, in one line, at the top of the first section."""

    kind: Literal["headline"]
    tone: str
    text: tuple[Cell, ...]


class Statement(NamedTuple):
    """One line of prose, assembled from chrome and facts."""

    kind: Literal["statement"]
    text: tuple[Cell, ...]


class Fields(NamedTuple):
    """Label-and-value rows, for receipts and for anything read one line at a time."""

    kind: Literal["fields"]
    title: str | None
    rows: tuple[tuple[str, Cell], ...]


class Table(NamedTuple):
    """One row per thing, for the step account and the census."""

    kind: Literal["table"]
    title: str | None
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]


class Facing(NamedTuple):
    """Two accounts of one step, side by side, with neither presented as the truth.

    A two-column table would let a rendering promote one side to a header. This block cannot:
    both sides carry a label of the same kind, and the symmetry is structural.
    """

    kind: Literal["facing"]
    subject: Cell
    left_label: str
    left: Cell
    right_label: str
    right: Cell


class Verbatim(NamedTuple):
    """Text that must survive a sink byte for byte — a command, or a step's whole ask.

    Its own block kind because every sink has to agree not to wrap it, not to fold it into
    prose and not to decorate it. A resume command a reader cannot paste is the receipt
    failing at the one moment it is used.
    """

    kind: Literal["verbatim"]
    title: str | None
    text: Cell


class GraphNode(NamedTuple):
    name: str
    status: str
    layer: int
    column: int


class GraphEdge(NamedTuple):
    upstream: str
    downstream: str
    back: bool


class Diagram(NamedTuple):
    """The run's graph as the record recorded it, laid out but not yet drawn.

    A figure rather than a statement: it enumerates what the record enumerates, and every
    label in it is a node's own name. One sink draws it and the others state its counts.
    """

    kind: Literal["diagram"]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    caption: tuple[Cell, ...]


class Nothing(NamedTuple):
    """What a section says when it has nothing to say."""

    kind: Literal["nothing"]
    text: str


Block = (
    Headline | Statement | Fields | Table | Facing | Verbatim | Diagram | Nothing
)

BLOCK_HEADLINE = "headline"
BLOCK_STATEMENT = "statement"
BLOCK_FIELDS = "fields"
BLOCK_TABLE = "table"
BLOCK_FACING = "facing"
BLOCK_VERBATIM = "verbatim"
BLOCK_DIAGRAM = "diagram"
BLOCK_NOTHING = "nothing"
BLOCK_KINDS: tuple[str, ...] = (
    BLOCK_HEADLINE,
    BLOCK_STATEMENT,
    BLOCK_FIELDS,
    BLOCK_TABLE,
    BLOCK_FACING,
    BLOCK_VERBATIM,
    BLOCK_DIAGRAM,
    BLOCK_NOTHING,
)


class Section(NamedTuple):
    question: Question
    blocks: tuple[Block, ...]


class Document(NamedTuple):
    """One run, said once, for every sink to render without deciding anything."""

    run_id: str
    sections: tuple[Section, ...]


SINK_TERMINAL = "terminal"
SINK_MARKDOWN = "markdown"
SINK_HTML = "html"
SINKS: tuple[str, ...] = (SINK_TERMINAL, SINK_MARKDOWN, SINK_HTML)

# Beyond this many nodes a drawn graph stops being a picture and becomes a wall. The engine
# draws the same graph live, zoomable, and its link is in the receipts — so past the cap the
# report states the counts and says where the drawing is.
GRAPH_NODE_CAP = 80

# What an attention kind may put on one screen before it is counted instead. Follow-up work
# is capped at fifty per step by the record, and fifty follow-ups above one exclusion is a
# report that buried the thing it exists to show.
ITEMS_PER_ATTENTION_KIND = 5

__all__ = [
    "BLOCK_DIAGRAM",
    "BLOCK_FACING",
    "BLOCK_FIELDS",
    "BLOCK_HEADLINE",
    "BLOCK_KINDS",
    "BLOCK_NOTHING",
    "BLOCK_STATEMENT",
    "BLOCK_TABLE",
    "BLOCK_VERBATIM",
    "GRAPH_NODE_CAP",
    "ITEMS_PER_ATTENTION_KIND",
    "RULES",
    "RULE_ACTOR",
    "RULE_ASSERTION",
    "RULE_LINK",
    "RULE_MONEY",
    "RULE_VALUE",
    "SECTIONS",
    "SECTION_ATTENTION",
    "SECTION_NEXT",
    "SECTION_ORDER",
    "SECTION_RECEIPTS",
    "SECTION_SHAPE",
    "SECTION_STEPS",
    "SECTION_VERDICT",
    "SINKS",
    "SINK_HTML",
    "SINK_MARKDOWN",
    "SINK_TERMINAL",
    "TONE_ALARM",
    "TONE_CAUTION",
    "TONE_PLAIN",
    "Block",
    "Cell",
    "Chrome",
    "Diagram",
    "Document",
    "Facing",
    "Fact",
    "Fields",
    "GraphEdge",
    "GraphNode",
    "Headline",
    "Nothing",
    "Question",
    "Section",
    "Statement",
    "Table",
    "Verbatim",
]
