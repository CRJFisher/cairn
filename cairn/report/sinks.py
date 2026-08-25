"""Escaping at each final sink, and the one path a fact takes to reach one.

The record stores text **unescaped** by contract ([run-model.md]): an escape is a property of
where text is going rather than of what it is, so text escaped for one sink is wrong in the
next. Every escape therefore happens here, at the leaf, and never before.

Two things make that more than a convention.

`Raw` and `Escaped` are distinct types over `str`, so under pyright strict an escaper will
not accept what another escaper returned. "A pre-escaped string is never reused across
contexts" is checked when the code is checked, not when someone remembers.

And the record's own strings are **re-normalised on the way in**. `cairn/text.py` runs at
extraction, but not every field goes through it: node names, log paths, a census's branch
names and a divergence's reported status all reach the record through `engine.text`, which
returns them verbatim. More to the point, a renderer's input is a *file on disk* — a record
someone edited, or one an older extraction wrote, has met no normaliser at all. So the sink
strips control characters again before escaping, and an escape hatch that depends on the
extraction having run is not one.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import NamedTuple, NewType

from cairn.report.phrases import apply
from cairn.report.spine import Cell, Chrome, Fact
from cairn.text import normalise

Raw = NewType("Raw", str)
Escaped = NewType("Escaped", str)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Link and image syntax, and the code span. `[x](javascript:…)` in an agent's own account of
# itself is a live link in a pull request, and an image reference fetches whatever it names
# from a document that is meant to need no network. Emphasis is deliberately left alone:
# `*` and `_` forge nothing worse than italics, and escaping every underscore would render
# `work_alpha` as `work\_alpha` in the one document meant to be readable as text.
_MARKDOWN_INLINE = re.compile(r"([\\\[\]`])")
# A cell ends at an unescaped pipe, and a line break ends the whole table.
_MARKDOWN_CELL = re.compile(r"([|])")
# At the start of a line these are structure. Inside a line they are not, so only the line's
# opening is defended — escaping every one of them everywhere would make ordinary prose
# unreadable to a person reading the raw file, which is half of what markdown is for.
# CommonMark allows up to three spaces of indentation before a block marker, so the escape
# has to reach past them: a summary whose second line is `   # This run worked.` forges a
# heading inside the report of a run that did not.
_MARKDOWN_LEADER = re.compile(r"^( {0,3})([#>\-+*=|~]|\d+[.)])")
_HTTP = re.compile(r"https?://", re.IGNORECASE)
# What a one-line context cannot carry, folded to a space without touching anything else.
_BREAK = re.compile(r"[\n\t\r]+")


class Stated(NamedTuple):
    """One fact, in one section, under one label, as one rendering actually said it.

    The scribe's log. It is not a second copy of the truth: it records the single lookup that
    produced the text, so it cannot drift from what was rendered without the rendering being
    wrong in the same way.

    `label` is what makes it a binding rather than a bag of values. A rendering that shifted
    every value one row against its labels — a cost against the turns, a session against the
    model — states every fact correctly and answers a different question about each one, and
    a log without the label cannot tell that from a correct document.
    """

    section: str
    label: str
    keys: tuple[str, ...]
    rule: str
    shown: str


class Rendering(NamedTuple):
    """One document as one sink said it, beside the log of every fact it stated.

    The log is what makes agreement checkable. A rendering is a wall of text: asking whether
    it contains a value is either too weak — a short value matches by accident — or too
    strong, because each sink escapes differently and markdown has no inverse. The log says
    which fact was looked up, in which section, and what text that produced; the text is then
    required to appear in the document, which is what stops the log becoming a story the
    rendering tells about itself.
    """

    text: str
    stated: tuple[Stated, ...]


def raw(text: object) -> Raw:
    """Text on its way to a sink, stripped again of anything that could steer a terminal."""
    return Raw(normalise(text))


def one_line(text: object) -> Raw:
    """The same, collapsed onto one line where a break would break the structure around it.

    Deliberately uncapped. The record already bounds every untrusted field — two hundred
    characters for a summary, two thousand for prose — so a second cap here would be a sink
    quietly shortening a fact, which is the drift the oracle exists to catch. Length is the
    record's decision; escaping is the sink's.
    """
    return Raw(" ".join(normalise(text).split()))


def for_terminal(text: Raw) -> Escaped:
    """A terminal has no markup to escape, and one thing it must never be handed.

    `normalise` already strips every `Cc` and `Cf`, so an ESC cannot survive it — this is the
    belt to that pair of braces, because the cost of being wrong is a report that repaints
    the screen above itself and hides the exclusion it was written to show.
    """
    return Escaped(_CONTROL.sub("", text))


def for_markdown(text: Raw) -> Escaped:
    """Prose in a markdown document, which a pull request renders as HTML.

    So the angle bracket and the ampersand are escaped here as well: a summary containing a
    tag is live markup in every renderer that allows HTML through, which is most of them.
    """
    escaped = _MARKDOWN_INLINE.sub(r"\\\1", html.escape(text, quote=False))
    return Escaped(
        "\n".join(_MARKDOWN_LEADER.sub(r"\1\\\2", line) for line in escaped.split("\n"))
    )


def for_markdown_cell(text: Raw) -> Escaped:
    """One table cell. A stray pipe splits the row; a stray line break ends the table."""
    flat = html.escape(one_line(text), quote=False)
    return Escaped(_MARKDOWN_CELL.sub(r"\\\1", _MARKDOWN_INLINE.sub(r"\\\1", flat)))


def for_markdown_code(text: Raw) -> Escaped:
    """A span whose fence outruns any backtick run inside it, so the text survives intact.

    Nothing inside is escaped, which is the point: a resume command that reached a reader
    with an `&amp;&amp;` in it is a receipt that fails when it is pasted.
    """
    flat = Raw(_BREAK.sub(" ", normalise(text)))
    longest = max((len(run) for run in re.findall(r"`+", flat)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return Escaped(f"{fence}{pad}{flat}{pad}{fence}")


def for_html(text: Raw) -> Escaped:
    """Character data in an HTML or SVG element. Quotes go too, so one function serves both."""
    return Escaped(html.escape(text, quote=True))


def for_url(text: Raw) -> Escaped | None:
    """A link, or nothing at all where the scheme is not one a document may follow.

    The engine's base URL comes from an environment a person edits, so it is untrusted like
    everything else. A `javascript:` scheme reaching an `href` in a self-contained page is
    the whole injection in one field, and there is no rendering of it that is worth the risk.
    """
    if not _HTTP.match(text):
        return None
    return Escaped(html.escape(text, quote=True))


class Scribe:
    """The one path from a fact to a sink: look it up, phrase it, log it, escape it.

    Every value a rendering states passes through `say`. Nothing else may: a renderer holds
    no record and no projection, so a number it printed without asking here is a number it
    invented — which is what the digit test looks for and what the doc's rule forbids.
    """

    def __init__(self, facts: Mapping[str, str]) -> None:
        self._facts = facts
        self._section = ""
        self._label = ""
        self.stated: list[Stated] = []

    def enter(self, section: str) -> None:
        self._section = section
        self._label = ""

    def under(self, label: str) -> None:
        """Name what the next values answer, so the log records a binding and not a bag.

        Cleared by `enter`, and set to the empty string by anything that is prose rather than
        a labelled row: a stale label riding onto the next value would record a binding that
        was never rendered, which is worse than recording none.
        """
        self._label = label

    def shown(self, cell: Cell) -> Raw:
        """What this cell says, logged where it is a fact and taken as-is where it is ours."""
        if isinstance(cell, Chrome):
            return raw(cell.text)
        text = phrase(self._facts, cell)
        self.stated.append(Stated(self._section, self._label, cell.keys, cell.rule, text))
        return raw(text)


def join(words: list[str]) -> str:
    """Chrome and facts into one line, with punctuation kept against the word before it.

    Shared by all three sinks: a sentence is assembled from the same pieces in the same order
    everywhere, and only the escaping of those pieces differs. Three copies of this would be
    three chances for one rendering to punctuate a fact differently from another.
    """
    joined: list[str] = []
    for word in words:
        stripped = word.lstrip("\\")
        if joined and stripped[:1] in _CLINGS and stripped[:2] != "--":
            joined[-1] = joined[-1] + word
            continue
        joined.append(word)
    return " ".join(joined)


_CLINGS = ".,:;)!?"


def phrase(facts: Mapping[str, str], cell: Fact) -> str:
    """One leaf's text, from the projection alone, by the rule the composition declared.

    A key the projection does not carry raises: "a fact no key names is a fact no rendering
    may state" is the projection's own claim, and a report that quietly printed nothing for a
    key would be the one reader that disproved it.
    """
    return apply(cell.rule, tuple(facts[key] for key in cell.keys))


__all__ = [
    "Escaped",
    "Raw",
    "Rendering",
    "Scribe",
    "Stated",
    "for_html",
    "for_markdown",
    "for_markdown_cell",
    "for_markdown_code",
    "for_terminal",
    "for_url",
    "one_line",
    "phrase",
    "raw",
]
