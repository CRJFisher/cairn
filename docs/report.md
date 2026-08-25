# The report

One run, three renderings, one order. The report answers, in order: did it work, what do I do
next, what needs my attention, what did each step do, what shape was the run, and what are the
receipts. **The order is the design** — a person reading a report has one question first, and
it is never "what was the topology".

**Renderers render.** No renderer computes a fact the model does not carry, and no renderer
decides a verdict. Where two renderings disagree about a fact, that is a defect in one of
them, caught by comparing both against the canonical-facts projection
([run-model.md](run-model.md)) rather than by reading them side by side.

## The six questions

The spine is frozen in `cairn/report/spine.py`. The tuple **is** the order.

| Rank | Section     | The reader's question   | When it has nothing                                 |
| ---- | ----------- | ----------------------- | --------------------------------------------------- |
| 1    | `verdict`   | did it work             | This run recorded no verdict.                       |
| 2    | `next`      | what do I do next       | There is nothing to do.                             |
| 3    | `attention` | what needs my attention | Nothing needs your attention.                       |
| 4    | `steps`     | what did each step do   | This run recorded no step.                          |
| 5    | `shape`     | what shape was the run  | This run recorded no graph.                         |
| 6    | `receipts`  | what are the receipts   | No step reported a cost, a session or a transcript. |

**Every section appears in every rendering of every run**, and an empty one says so in its own
words. That is not decoration: a section that vanished when it was empty would leave a reader
unable to tell "nothing needs your attention" from "this rendering does not carry attention",
and the trust claim is that all six questions are answered from the report alone.

## What makes the order structural rather than conventional

`compose.document(record)` is the only entry point that reads the record, and it builds
sections by walking the spine; it hands the node and edge lists to `graph.layout`, which reads
nothing else of it. A renderer is handed the document it returns, and nothing else:

- **A renderer never sees the record.** It cannot reach a fact the composition did not place,
  re-order what it was given, or re-derive a verdict — not by discipline but because it was
  never handed the material. A test asserts that no sink module imports `cairn.record`;
  `compose` and `graph` read the model, and `phrases` sees the frozen vocabulary and nothing
  else.
- **A renderer never sees the projection except through the scribe.** Every value it prints
  comes from `Scribe.shown`, which looks the fact up, phrases it by the declared rule, logs
  what it produced, and hands back text to escape.
- **The order is asserted on the artifact, not on the object.** Each sink emits a
  machine-findable marker per section; a test extracts the sequence from the rendered text of
  every fixture in every sink and requires it to equal the spine's own order.

## The division of labour

**The record supplies enumeration** — which steps, nodes, edges, attention items and waves
exist, and in what order. **The projection supplies every scalar.** A number a surface worked
out for itself is a second opinion, so every count a rendering prints — how many steps
no-opped, how many were excluded, how many nodes the graph has, how many things need
attention — is a key in the projection. A test takes every digit run in a rendering and
requires it to come from a stated fact or from the report's own chrome.

`cairn/report/phrases.py` is the only module that spells a human sentence. Each of its maps is
total over the vocabulary it is keyed on, asserted by a test, so a word the record can hold
and no map can phrase raises rather than falling through to something plausible.

## The blocks

The block vocabulary is closed and semantic rather than typographic: `headline`, `statement`,
`fields`, `table`, `facing`, `verbatim`, `diagram`, `nothing`. Typography is each sink's.

Three earn their own kind rather than being a shape of another:

- **`facing`** carries two accounts of one step with neither presented as the truth. A
  two-column table would let a rendering promote one side to a header; this block cannot.
- **`verbatim`** is text that must survive byte for byte — a command, or a step's whole ask.
  Every sink agrees not to wrap it, fold it into prose or decorate it. A resume command a
  reader cannot paste is the receipt failing at the one moment it is used.
- **`diagram`** is a figure rather than a statement. It enumerates what the record
  enumerates, every label in it is a node's own name, and it is checked structurally instead
  of against the projection.

A leaf is either `Chrome` — Cairn's own words — or a `Fact`, which names projection keys and
a display rule rather than carrying text. The rules are `value`, `money`, `actor`, `link`
and `assertion`.

`money` exists because a cost and whether it is notional are one sentence: on a subscription
login the figure is an API-equivalent price rather than money spent, and a rendering that
printed the number alone would be inventing a payment. `actor` exists because an absent actor
means Cairn started the run and is never rendered as unknown. `link` exists because a value
that merely looks like a URL is not one a document may follow: a repository path, a
transcript location and a plan's name are all record strings, and linking on shape alone
would let any of them put an outbound link into a page whose contract is that it needs none.
`assertion` exists because a divergence is two accounts weighed against each other, and a
reader who cannot tell what one of them means cannot weigh it: the verification side says
whether the assertion passed rather than spelling a bare yes or no.

## The three renderings

| Sink       | What it is for                                                      | Its section marker             |
| ---------- | ------------------------------------------------------------------- | ------------------------------ |
| `terminal` | the default, produced for any run, readable in a scrolling terminal | `== HEADING ==`                |
| `markdown` | the durable artifact, readable in a repository or a pull request    | `<!-- cairn:section:<key> -->` |
| `html`     | self-contained, offline, with the run's graph drawn                 | `<section id="cairn-<key>">`   |

**The terminal and markdown renderings carry no colour at all.** Not a mode — a rendering that dropped to plain text on a pipe
would be a second rendering nobody reads before shipping. The weighting a colour would carry is
carried by words and by position: the verdict is the first line, and what makes it not a clean
success is the second.

## An exclusion is unmissable

A run with exclusions renders differently from a clean one **in the first screen of every
rendering**. The exclusion count, the wave census's declined branches, the no-op count and the
engine's disagreement are all blocks of the first section rather than sections of their own: a
section can be scrolled past, and I5 fails quietly if a dropped branch is a subsection.

Where the engine calls a run a success and Cairn does not, the report says so in words, names
the engine's own status, and states that the verdict was derived by walking every node. The
regression fixture is the run where the engine reported `Succeeded` with exit 0 over a step
that contributed nothing.

## A no-op run

"N steps skipped: already complete" is on the first screen, with the runs that did the work
named beside it. Each no-op then names the scope its key matched under and both keys, because
`once` and `daily` are the difference between correct caching and stale research, and a
recovery run rendered naively is a screen of grey with no account of who paid for it.

## The receipts

Per step: cost and whether it is notional, turns, model, session identity, transcript,
standard error, branch, commit, diffstat, exit code, timings, and a resume command.

The resume command is `verbatim`, and it carries its own `cd` into the directory the step ran
in. On a green run the wave's prune has since removed that worktree, so the command names a
directory that is gone. **The report does not check whether it is still there** — that would
be a fact the model does not carry, and it would be wrong the moment the filesystem changed.

The run's own receipts carry the budget, the git facts, the occasion, the earlier runs, and
the engine's own view of the run — the better surface for logs and timings, and one that
survives the run ending.

## Escaping

One normalisation pass at extraction, then **context-specific escaping at each final sink**,
never the other way round. A pre-escaped string is never stored or reused across contexts.

`Raw` and `Escaped` are distinct types over `str`, so under pyright strict an escaper will not
accept what another escaper returned: "a pre-escaped string is never reused" is checked when
the code is checked rather than when someone remembers.

| Sink     | Context | What it defends against                                                                                    |
| -------- | ------- | ---------------------------------------------------------------------------------------------------------- |
| terminal | all     | an escape sequence that repaints the screen above the report and hides the exclusion it exists to show     |
| markdown | prose   | a tag that is live markup in a pull request; a `#` that forges a heading                                   |
| markdown | cell    | a pipe that splits the row; a line break that ends the table                                               |
| markdown | code    | nothing — the fence is made longer than any backtick run inside instead, so the text survives to be pasted |
| html     | text    | a tag, an attribute break, an entity                                                                       |
| html     | url     | a scheme a document may not follow; only `http` and `https` become links                                   |
| svg      | text    | `</text>`, which would close the element and make everything after it markup                               |

Link and image syntax is neutralised in markdown, and no value the record supplies is ever
spelled as a link there. Emphasis is deliberately left alone: `*` and `_` forge nothing worse
than italics, and escaping every underscore would render `work_alpha` as `work\_alpha` in the
one document meant to be readable as text.

**The sink re-normalises on the way in.** A renderer's input is a file on disk — a record
someone edited, or one an older extraction wrote, has met no normaliser at all — so a defence
that only holds for values the extraction produced is not a defence.

## The graph

The HTML rendering draws every node the engine recorded, laid out in layers by longest path
over the record's own edges. It refuses nothing: a node whose name the topology's grammar does
not cover is drawn like any other, and an edge naming a node the record does not carry is kept
rather than dropped. A cycle cannot come out of the engine, but a hand-edited record can carry
one, and the layout settles instead of circling: the edge that goes back is marked, and the
layers are compacted afterwards so a cycle cannot leave a nine-node drawing with twenty empty
rows through the middle of it.

Past eighty nodes the drawing stops being a picture, and the report states the counts and
defers to the engine's own view, which draws the same graph live and zoomable.

**Self-contained** means one file that needs a browser and nothing else: no script, no
stylesheet, no font, no image, no network. That is both the offline requirement and the
cheapest security posture available — a page with no script has no place for one to appear,
which makes any `<script` in the output unambiguously a breakout rather than a feature. The
one link a page may carry is the engine's own view of the run.

## The drift oracle

Every rendering is checked against `canonical_facts` on every fixture, in three parts:

- **Fidelity** — the text shown is what the declared rule makes of the projected value.
- **Realisation** — every fact the log claims was stated actually appears in the document.
  Without it the log would be a story the rendering tells about itself.
- **Coverage** — every fact that has a value reaches every rendering. A fact whose value is an
  absence, nothing, or zero may go unstated; everything else is either stated or named in the
  suite's `UNSTATED` map with its reason.

There is no per-sink escape hatch, because all three sinks render the same blocks. Cross-sink
agreement then follows as a corollary and is asserted too — but the oracle is the projection,
never one rendering compared against another.

## What this does not do yet

**Nothing inside a run produces a report.** A report is rendered when someone asks for one,
and the skill asks for one when a run it started ends ([../capabilities/reading.md](../capabilities/reading.md)). A
report _step_ inside the run would render a run that has not finished, hold the repository
while it did, and add a step whose own failure would fail the run it was describing.

**Whether the drawn graph earns its place is still open.** The engine draws the same graph
live, zoomable, and its link survives the run ending; what the drawn one adds is offline
readability, and that is a claim about readers rather than about code. It is the cheapest
thing here to drop if the answer turns out to be "nothing".

**The trust claim is proven mechanically and not yet with a reader.** The suite asserts that
every question has its section in every rendering of every recorded run, and that each
section carries the facts that answer it rather than only its heading. Handing a report from
a run someone did not watch to that person, and checking they can answer all six questions
from it alone, is a study; it has not been run.

## Reading a report

```text
python3 -m cairn report --run <run-id> --repository <path>
                        [--format terminal|markdown|html] [--out <path>]
                        [--engine-records <path>] [--reports <path>]
```

Like `cairn plan`, `cairn supervise`, `cairn workflow` and `cairn record`, this runs outside
any run: it resolves no runtime identity, leaves no step report, takes no lock and starts
nothing. A report is a thing you can produce with the engine stopped.

The record is built fresh rather than read back from disk, for the same reason `cairn record
facts` builds one: it is derived from the engine's state and the run's own reports, both of
which outlive it.

**The exit status is the run's verdict, never the command's own health** — the codes are
frozen in [run-model.md](run-model.md). A run with exclusions exits 3 whatever any of the
three renderings chose to say about it, and a rendering can never redefine what automation
reads.
