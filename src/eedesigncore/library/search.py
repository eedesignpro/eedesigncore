"""Rank and filter part records, with KiCad's own scoring as the floor.

Two halves, because an agent asks two different questions.

**Free text**, ranked. "that low-noise op-amp". This is a port of KiCad's own
part search, exact on the weights and on the arithmetic:

- the term set and its weights, ``LIB_SYMBOL::cacheSearchTerms``
  (``eeschema/lib_symbol.cpp:158-182``),
- the per-word scoring, ``EDA_COMBINED_MATCHER::ScoreTerms``
  (``common/eda_pattern_match.cpp:479-514``): the whole query word equal to a
  whole term scores 8 times its weight, a match at position zero scores 2
  times, any other match scores 1 times,
- the word combination, ``LIB_TREE_NODE_ITEM::UpdateScore``
  (``common/lib_tree_model.cpp:236-256``): the score starts at 1, each word
  adds its own sum, and a word that scores zero against every term drops the
  part outright. Multiple words are an AND, and
- the query split, ``LIB_TREE_MODEL_ADAPTER::UpdateSearchString``
  (``common/lib_tree_model_adapter.cpp:322-333``): split on space, tab,
  carriage return and newline, lowered, at most 100 words.

**Structured**, exact. "0402, 1 percent, 10k" as field tests, because an agent
should not guess at a substring when it means a field. See :class:`FieldTest`.

Two deviations from KiCad, both deliberate and both recorded in ADR-101.

1. **Every field is a search term.** KiCad's term set reads six things and no
   custom field, so ``IPN``, ``ItemManufacturerPartId``, ``Tolerance`` and
   ``SEPackage`` are invisible to it. Each field that is not already a KiCad
   term contributes a term at :data:`DEFAULT_FIELD_WEIGHT`, and
   ``field_weights`` overrides any of them.
2. **The relational matcher is not ported.** KiCad accepts a word shaped
   ``key>10k`` and compares it numerically after scaling the unit suffix
   (``eda_pattern_match.cpp:248-300``). Reading ``10k`` as a number is
   parametric knowledge, which stays outside this library (ADR-100). Such a
   word is matched as plain text here. Use a :class:`FieldTest` with ``gt``
   for a numeric comparison against a column that already holds a number.

The regex and wildcard matchers ARE ported, so ``/^R_[0-9]+$/`` and ``R_*``
behave as they do in KiCad. One guard is added that KiCad does not have: a
pattern that is too long, or that repeats a group which itself repeats, is
refused and the word matches as plain text instead. A query word is untrusted
input, and ``^(a+)+$`` against a 60-character term ran for over five minutes
before the guard. See :func:`regex_is_safe` and ADR-104.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final, Literal

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.library.records import (
    DESCRIPTION_FIELD,
    FOOTPRINT_FIELD,
    PartRecord,
)

WEIGHT_LIB_ID: Final = 16
WEIGHT_NAME: Final = 8
WEIGHT_NICKNAME: Final = 4
WEIGHT_KEYWORD_TOKEN: Final = 4
WEIGHT_KEYWORDS: Final = 1
WEIGHT_DESCRIPTION: Final = 1
WEIGHT_FOOTPRINT: Final = 1
"""KiCad's six term weights (``lib_symbol.cpp:164-182``)."""

DEFAULT_FIELD_WEIGHT: Final = 1
"""The weight of a field KiCad does not search. ADR-101 deviation 2."""

MAX_QUERY_WORDS: Final = 100
"""``MAX_TERMS`` (``lib_tree_model_adapter.cpp:324``)."""

MAX_TERM_LENGTH: Final = 1000
"""A term longer than this is truncated (``eda_pattern_match.cpp:494-497``)."""

MAX_REGEX_PATTERN_LENGTH: Final = 200
"""Cap on an explicit regex query word. KiCad has no cap.

A query is free text, so a word is untrusted input. This bounds the PATTERN,
which :data:`MAX_TERM_LENGTH` does not: catastrophic backtracking is driven by
the pattern against a short non-matching text, not by the text length.
"""

_QUANTIFIERS: Final = frozenset({"*", "+", "{"})
"""Characters that repeat what precedes them, so they can drive backtracking."""

SCORE_EXACT: Final = 8
SCORE_PREFIX: Final = 2
SCORE_SUBSTRING: Final = 1
"""The three multipliers (``eda_pattern_match.cpp:500-509``)."""

_QUERY_SPLIT: Final = re.compile(r"[ \t\r\n]+")

_WILDCARD_ESCAPE: Final = frozenset(".*+?^${}()|[]/\\")

MatchKind = Literal["exact", "prefix", "substring"]

FieldOperator = Literal[
    "equals",
    "not_equals",
    "contains",
    "one_of",
    "present",
    "absent",
    "gt",
    "ge",
    "lt",
    "le",
]


class FieldTest(KicadModel):
    """One exact test against one field of a record.

    ``field`` is a field name, matched case-insensitively against the record's
    field names so ``ipn`` finds ``IPN``. ``nickname`` and ``name`` are also
    accepted as field names, so a test can pin the library or the item.

    The operators:

    ============ =====================================================
    ``equals``   the whole value, ignoring case unless ``cased``
    ``not_equals`` the negation of ``equals``
    ``contains`` a substring of the value
    ``one_of``   the whole value equals one of ``values``
    ``present``  the field exists and is not empty
    ``absent``   the field is missing or empty
    ``gt`` ``ge`` ``lt`` ``le``  numeric comparison against ``number``
    ============ =====================================================

    A numeric operator reads the field with :func:`float` and nothing more. It
    does not understand a unit, a suffix or a prefix: ``10000`` compares,
    ``10K Ohm`` does not. That is the same line as ADR-101 deviation 3 —
    reading ``10k`` as a resistance is parametric knowledge and belongs to the
    caller. A database can carry ``value_numeric`` for exactly this.
    A value that will not read as a number fails the test rather than raising.
    """

    field: str
    op: FieldOperator = "equals"
    value: str = ""
    values: list[str] = Field(default_factory=list)
    number: float = 0.0
    cased: bool = False

    def matches(self, record: PartRecord) -> bool:
        """Report whether *record* passes this test."""
        found = _field_value(record, self.field)

        if self.op == "present":
            return bool(found)
        if self.op == "absent":
            return not found
        if found is None:
            return False

        if self.op in {"gt", "ge", "lt", "le"}:
            try:
                left = float(found.strip())
            except ValueError:
                return False
            if self.op == "gt":
                return left > self.number
            if self.op == "ge":
                return left >= self.number
            if self.op == "lt":
                return left < self.number
            return left <= self.number

        subject = found if self.cased else found.casefold()

        if self.op == "one_of":
            wanted = self.values if self.cased else [v.casefold() for v in self.values]
            return subject in wanted

        wanted_one = self.value if self.cased else self.value.casefold()
        if self.op == "equals":
            return subject == wanted_one
        if self.op == "not_equals":
            return subject != wanted_one
        return wanted_one in subject


class MatchReason(KicadModel):
    """Why one query word matched, so a caller can say why it proposes a part.

    ``term`` names the source: ``lib_id``, ``name``, ``nickname``,
    ``keywords``, ``keyword_token``, ``description``, ``footprint``, or
    ``field:<Name>`` for every other field.
    """

    query_word: str
    term: str
    text: str
    kind: MatchKind
    points: int


class PartMatch(KicadModel):
    """One record that survived the query, with its score and its reasons.

    ``reasons`` is ordered by falling points, and holds at most
    ``max_reasons`` entries per search.
    """

    record: PartRecord
    score: int
    reasons: list[MatchReason] = Field(default_factory=list)

    @property
    def lib_id(self) -> str:
        """The record's library id."""
        return self.record.lib_id


class SearchTerm(KicadModel):
    """One weighted, normalised string a query word is scored against.

    ``label`` names where the text came from, and it is what a
    :class:`MatchReason` reports. Normalisation copies
    ``EDA_COMBINED_MATCHER::ScoreTerms`` (``eda_pattern_match.cpp:485-498``):
    lower the text, trim both ends, then truncate to
    :data:`MAX_TERM_LENGTH`, which is KiCad's guard against a whole schematic
    pasted into the search box.
    """

    label: str
    text: str
    weight: int

    @classmethod
    def build(cls, label: str, text: str, weight: int) -> SearchTerm:
        """Normalise *text* and return the term."""
        return cls(label=label, text=text.casefold().strip()[:MAX_TERM_LENGTH], weight=weight)


def _field_value(record: PartRecord, field: str) -> str | None:
    """Return a field of *record* by case-insensitive name, or ``None``.

    ``name``, ``nickname``, ``lib_id`` and ``keywords`` resolve to the record
    members of those names, so a test can pin them like any field. A field of
    the same name wins, because a KiCad symbol may legitimately carry one.
    """
    for key, value in record.fields.items():
        if key.casefold() == field.casefold():
            return value

    members = {
        "name": record.name,
        "nickname": record.nickname,
        "lib_id": record.lib_id,
        "keywords": record.keywords,
        "sub_library": record.sub_library,
        "source_path": record.source_path,
    }
    return members.get(field.casefold())


def search_terms(
    record: PartRecord,
    *,
    field_weights: dict[str, int] | None = None,
    include_every_field: bool = True,
) -> list[SearchTerm]:
    """Build the weighted term set for one record.

    The first seven entries are KiCad's, in KiCad's order and with KiCad's
    weights (``lib_symbol.cpp:164-182``). The footprint term is omitted when
    the field is empty, which is what KiCad does (``:178-181``).

    Every other field follows, at ``field_weights[name]`` or
    :data:`DEFAULT_FIELD_WEIGHT`. Set ``include_every_field`` false for KiCad's
    term set alone.
    """
    weights = field_weights or {}
    terms = [
        SearchTerm.build("nickname", record.nickname, WEIGHT_NICKNAME),
        SearchTerm.build("name", record.name, WEIGHT_NAME),
        SearchTerm.build("lib_id", record.lib_id, WEIGHT_LIB_ID),
    ]
    terms.extend(
        SearchTerm.build("keyword_token", token, WEIGHT_KEYWORD_TOKEN)
        for token in _QUERY_SPLIT.split(record.keywords)
        if token
    )
    terms.append(SearchTerm.build("keywords", record.keywords, WEIGHT_KEYWORDS))
    terms.append(SearchTerm.build("description", record.description, WEIGHT_DESCRIPTION))
    if record.footprint:
        terms.append(SearchTerm.build("footprint", record.footprint, WEIGHT_FOOTPRINT))

    if include_every_field:
        for name, value in record.fields.items():
            if name in {DESCRIPTION_FIELD, FOOTPRINT_FIELD} or not value:
                continue
            weight = weights.get(name, DEFAULT_FIELD_WEIGHT)
            if weight <= 0:
                continue
            terms.append(SearchTerm.build(f"field:{name}", value, weight))

    return terms


class _WordMatcher:
    """One query word, with the matchers KiCad builds for ``CTX_LIBITEM``.

    KiCad adds a regex matcher, a wildcard matcher, a relational matcher and a
    substring matcher, and keeps whichever accepted the pattern
    (``eda_pattern_match.cpp:390-405``, ``:523-528``). The relational matcher
    is not ported, per ADR-101 deviation 3.

    An explicit regex is one wrapped in ``^…$`` or in ``/…/``
    (``:81-106``). Anything else is not treated as a regex, which is KiCad's
    rule, not a simplification.
    """

    __slots__ = ("patterns", "word")

    def __init__(self, word: str) -> None:
        self.word = word
        self.patterns: list[re.Pattern[str]] = []

        explicit = _explicit_regex(word)
        if explicit is not None:
            self.patterns.append(explicit)

        translated = _wildcard_to_regex(word)
        wildcard = _compile(translated) if regex_is_safe(translated) else None
        if wildcard is not None:
            self.patterns.append(wildcard)

    def find(self, text: str) -> int | None:
        """Return the lowest match position across every matcher, or ``None``."""
        best: int | None = None

        position = text.find(self.word)
        if position != -1:
            best = position

        for pattern in self.patterns:
            match = pattern.search(text)
            if match is not None and (best is None or match.start() < best):
                best = match.start()

        return best

    def score(self, terms: Sequence[SearchTerm]) -> tuple[int, list[MatchReason]]:
        """Score this word against a term set (``eda_pattern_match.cpp:479-514``)."""
        total = 0
        reasons: list[MatchReason] = []

        for term in terms:
            if not term.text:
                continue

            if self.word == term.text:
                points = SCORE_EXACT * term.weight
                kind: MatchKind = "exact"
            else:
                position = self.find(term.text)
                if position is None:
                    continue
                if position == 0:
                    points = SCORE_PREFIX * term.weight
                    kind = "prefix"
                else:
                    points = term.weight
                    kind = "substring"

            total += points
            reasons.append(
                MatchReason(
                    query_word=self.word,
                    term=term.label,
                    text=term.text,
                    kind=kind,
                    points=points,
                )
            )

        return total, reasons


def regex_is_safe(pattern: str) -> bool:
    """Report whether an explicit regex may be compiled and run.

    Two refusals, and both are about the CALLER's availability rather than
    KiCad fidelity. KiCad has neither, because its search box is driven by a
    person at a keyboard; a library's query word is untrusted input.

    1. A pattern longer than :data:`MAX_REGEX_PATTERN_LENGTH`.
    2. A pattern that repeats a group which itself repeats or branches:
       ``(a+)+``, ``(a|a)*``, ``(a{2}){3}``. That is the shape that backtracks
       catastrophically. Measured before the guard: ``^(a+)+$`` against a
       60-character term did not finish in five minutes. Python's ``re`` has no
       step budget, so the pattern has to be refused rather than timed out.

    The test is deliberately blunt and refuses some patterns that would have
    been safe. The alternative is a search surface that any one query word can
    hang. ADR-104.
    """
    if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
        return False

    # One flag per open group: does anything inside it repeat or branch?
    group_repeats: list[bool] = []

    for index, char in enumerate(pattern):
        if index and pattern[index - 1] == "\\":
            continue  # an escaped character is a literal
        if char == "(":
            group_repeats.append(False)
        elif char == ")":
            if not group_repeats:
                continue
            inner_repeats = group_repeats.pop()
            tail = pattern[index + 1 :]
            repeated = bool(tail) and tail[0] in _QUANTIFIERS
            if repeated and inner_repeats:
                return False
            if group_repeats and (inner_repeats or repeated):
                group_repeats[-1] = True
        elif (char in _QUANTIFIERS or char == "|") and group_repeats:
            group_repeats[-1] = True

    return True


def _explicit_regex(word: str) -> re.Pattern[str] | None:
    """Port ``EDA_PATTERN_MATCH_REGEX::SetPattern`` (``:81-106``).

    A regex must be explicit: wrapped in ``^…$`` or in ``/…/``. That is KiCad's
    rule, not a simplification. Unlike KiCad, a pattern that fails
    :func:`regex_is_safe` is refused and the word then matches as plain text.
    """
    if word.startswith("^") and word.endswith("$"):
        return _compile(word) if regex_is_safe(word) else None
    if word.startswith("/"):
        body = word[1:]
        if body.endswith("/"):
            body = body[:-1]
        return _compile(body) if regex_is_safe(body) else None
    return None


def _wildcard_to_regex(word: str) -> str:
    """Port ``EDA_PATTERN_MATCH_WILDCARD::SetPattern`` (``:158-191``).

    ``?`` becomes one character, ``*`` becomes any run, and every regex
    metacharacter is escaped. A word with no wildcard therefore compiles to
    itself, which is why KiCad keeps this matcher for every query.
    """
    out: list[str] = []
    for char in word:
        if char == "?":
            out.append(".")
        elif char == "*":
            out.append(".*")
        elif char in _WILDCARD_ESCAPE:
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


def _compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def split_query(query: str) -> list[str]:
    """Split a query the way KiCad does (``lib_tree_model_adapter.cpp:326-333``).

    Split on space, tab, carriage return and newline; lower each word; keep at
    most :data:`MAX_QUERY_WORDS`.
    """
    words = [word.casefold() for word in _QUERY_SPLIT.split(query) if word]
    return words[:MAX_QUERY_WORDS]


def score_record(
    record: PartRecord,
    query: str,
    *,
    field_weights: dict[str, int] | None = None,
    include_every_field: bool = True,
    max_reasons: int = 4,
) -> PartMatch:
    """Score one record against a query.

    The returned score is zero when any query word matched nothing, which is
    KiCad's AND (``lib_tree_model.cpp:240-248``). An empty query scores 1, the
    same base KiCad starts every node at, so an unfiltered list is a valid
    result rather than an empty one.
    """
    terms = search_terms(
        record,
        field_weights=field_weights,
        include_every_field=include_every_field,
    )

    score = 1
    reasons: list[MatchReason] = []

    for word in split_query(query):
        word_score, word_reasons = _WordMatcher(word).score(terms)
        if word_score == 0:
            return PartMatch(record=record, score=0, reasons=[])
        score += word_score
        reasons.extend(word_reasons)

    reasons.sort(key=lambda reason: (-reason.points, reason.term, reason.query_word))
    return PartMatch(record=record, score=score, reasons=reasons[:max_reasons])


def search_records(
    records: Iterable[PartRecord],
    query: str = "",
    *,
    tests: Sequence[FieldTest] = (),
    field_weights: dict[str, int] | None = None,
    include_every_field: bool = True,
    limit: int | None = None,
    max_reasons: int = 4,
) -> list[PartMatch]:
    """Filter, score, and rank part records.

    Order of work, which is also the cheap order: every :class:`FieldTest` runs
    first and drops a record with no scoring, then the surviving records are
    scored against ``query``.

    The result is sorted by falling score, then by library id, then by name, so
    two runs over the same records return the same order. ``limit`` truncates
    after the sort.
    """
    matches: list[PartMatch] = []

    for record in records:
        if not all(test.matches(record) for test in tests):
            continue
        match = score_record(
            record,
            query,
            field_weights=field_weights,
            include_every_field=include_every_field,
            max_reasons=max_reasons,
        )
        if match.score == 0:
            continue
        matches.append(match)

    matches.sort(key=lambda m: (-m.score, m.record.lib_id, m.record.name))
    return matches if limit is None else matches[:limit]
