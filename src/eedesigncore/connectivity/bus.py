"""The bus grammar: vector parsing, group parsing, and member expansion.

Source of truth: audit ``hierarchy-and-bus-2026-07-28.md``, PART B, sections
B1 through B4, verified directly against tag 10.0.3 in
a local clone of the KiCad source. Ports:

- ``NET_SETTINGS::ParseBusVector`` (``common/project/net_settings.cpp:1137-1342``)
- ``NET_SETTINGS::ParseBusGroup`` (``common/project/net_settings.cpp:1345-1534``)
- ``isSuperSubOverbar`` (``net_settings.cpp:1104-1107``)
- ``isEscaped`` (``net_settings.cpp:1117-1134``)
- the ``escapeSpacesForBus`` lambda (``net_settings.cpp:1360-1375``)
- ``SCH_CONNECTION::IsBusLabel`` (``eeschema/sch_connection.cpp:526-532``)
- ``SCH_CONNECTION::MightBeBusLabel`` (``eeschema/sch_connection.cpp:535-541``)
- ``SCH_CONNECTION::ConfigureFromLabel`` (``eeschema/sch_connection.cpp:140-213``),
  the classifier and member expansion.

Four rules hold against this source reading.

1. A single-index vector is not a bus. ``ParseBusVector`` rejects
   ``begin == end`` (``net_settings.cpp:1321-1322``). ``D[3..3]`` classifies
   as a plain net.
2. Zero padding and index direction both drop. Indices round-trip through
   integer parsing and plain concatenation (``:1264``, ``:1286``,
   ``:1331-1338``). A descending range swaps to ascending (``:1323-1324``).
3. A bare alias name is not a bus. ``ConfigureFromLabel`` looks up an alias
   only for a member of a bus group (``sch_connection.cpp:186``). A name with
   no ``[`` or ``{`` never reaches that lookup, because it fails both
   ``ParseBusVector`` and ``ParseBusGroup`` and classifies as ``NET``.
4. Bus aliases are supplied by the caller, never read from a schematic token
   here. This module takes a ``resolve_alias`` callback. The project-file
   storage this callback would read (``schematic.bus_aliases``, per audit
   headline finding 2) is out of this module's scope.

One source quirk this reading found, not one of the four rules above. A
vector missing its closing bracket, for example ``D[3..5`` with no ``]``,
still classifies as a bus. ``ParseBusVector``'s end-number loop has no
guard for reaching the end of the string without a ``]`` (unlike the
begin-number loop, which the following section's own bounds check catches).
The end value is silently left at its initial ``0``, and the begin/end swap
at ``:1323-1324`` then produces ``D[0..3]``. Ported faithfully. See
``test_bus.py::TestSourceQuirks``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from eedesigncore.common.escape import unescape_string
from eedesigncore.connectivity.netname import escape_netname

_DIGITS = frozenset("0123456789")

# isSuperSubOverbar, net_settings.cpp:1104-1107. A brace preceded by one of
# these three characters is a formatting construct, not the start of a bus
# group's member list.
_MARKUP_PREFIXES = frozenset("_^~")

# The suffix characters a vector accepts after its range, net_settings.cpp:1308.
_VECTOR_SUFFIX_CHARS = frozenset("+-PN")


def _is_super_sub_overbar(char: str) -> bool:
    """Port of ``isSuperSubOverbar`` (``net_settings.cpp:1104-1107``)."""
    return char in _MARKUP_PREFIXES


def _is_escaped(text: str, pos: int) -> bool:
    """Port of ``isEscaped`` (``net_settings.cpp:1117-1134``).

    An odd run of backslashes directly before *pos* escapes the character at
    *pos*.
    """
    count = 0
    i = pos
    while i > 0 and text[i - 1] == "\\":
        count += 1
        i -= 1
    return count % 2 == 1


def _escape_spaces_for_bus(member: str) -> str:
    """Port of the ``escapeSpacesForBus`` lambda (``net_settings.cpp:1360-1375``).

    Every literal space in a group member becomes a backslash-space pair, so
    the member survives a later, recursive bus parse unchanged.
    """
    return member.replace(" ", "\\ ")


@dataclass(frozen=True)
class BusVector:
    """The two out-parameters of a successful ``ParseBusVector`` call.

    ``members`` carries no escaping. Source builds each member with a plain
    concatenation (``net_settings.cpp:1331-1338``), not ``EscapeString``.
    """

    prefix: str
    members: list[str]


def parse_bus_vector(text: str, *, max_members: int | None = None) -> BusVector | None:
    """Port of ``NET_SETTINGS::ParseBusVector`` (``net_settings.cpp:1137-1342``).

    Returns ``None`` where the source function returns ``false``. *text* is
    used as given. The source itself calls this on unescaped text only
    through its own callers (``ConfigureFromLabel``, ``:151``); this function
    does not unescape.

    A single-index range rejects (``begin == end``, ``:1321-1322``): rule 1.
    A descending range swaps to ascending, and every index passes through
    plain integer parsing, so leading zeros and direction both drop
    (``:1323-1338``): rule 2.
    """
    length = len(text)
    i = 0
    prefix_chars: list[str] = []
    in_quotes = False
    brace_nesting = 0
    fmt_wraps_name = False

    while i < length:
        char = text[i]

        if char == '"' and not _is_escaped(text, i):
            in_quotes = not in_quotes
            i += 1
            continue

        if in_quotes:
            if char == "\\" and i + 1 < length:
                prefix_chars.append(text[i + 1])
                i += 2
            else:
                prefix_chars.append(char)
                i += 1
            continue

        if char == "{":
            if i > 0 and _is_super_sub_overbar(text[i - 1]):
                brace_nesting += 1
                prefix_chars.append("{")
                i += 1
                continue
            return None

        if char == "}":
            brace_nesting -= 1
            prefix_chars.append("}")
            i += 1
            continue

        if char == "\\" and i + 1 < length and text[i + 1] == " ":
            prefix_chars.append(text[i + 1])
            i += 2
            continue

        if char == " " or char == "]":
            return None

        if char == "[":
            if brace_nesting > 0:
                prefix_str = "".join(prefix_chars)
                fmt_start = prefix_str.rfind("{")
                if (
                    fmt_start != -1
                    and fmt_start > 0
                    and _is_super_sub_overbar(prefix_str[fmt_start - 1])
                ):
                    if fmt_start == len(prefix_str) - 1:
                        prefix_chars = list(prefix_str[: fmt_start - 1])
                    else:
                        fmt_wraps_name = True
            break

        prefix_chars.append(char)
        i += 1
    else:
        # The prefix loop ran off the end without an opening '['. Source
        # reaches the same result one step later, through the bounds check
        # that opens the "Parse start number" section (:1257-1258).
        return None

    prefix = "".join(prefix_chars)

    # Parse start number, net_settings.cpp:1253-1273.
    i += 1  # consume '['
    if i >= length:
        return None

    begin_digits: list[str] = []
    begin = 0
    found_range_marker = False
    while i < length:
        char = text[i]
        if char == "." and i + 1 < length and text[i + 1] == ".":
            begin = int("".join(begin_digits)) if begin_digits else 0
            i += 2
            found_range_marker = True
            break
        if char not in _DIGITS:
            return None
        begin_digits.append(char)
        i += 1
    if not found_range_marker:
        return None

    # Parse end number, net_settings.cpp:1275-1295. Source has no guard here
    # for running off the end without a closing ']', unlike the section
    # above. `end` is silently left at its initial 0 in that case. Ported
    # faithfully. See the module docstring's "source quirk" note.
    end_digits: list[str] = []
    end = 0
    if i >= length:
        return None
    while i < length:
        char = text[i]
        if char == "]":
            end = int("".join(end_digits)) if end_digits else 0
            i += 1
            break
        if char not in _DIGITS:
            return None
        end_digits.append(char)
        i += 1

    # Parse suffix, net_settings.cpp:1297-1316.
    suffix_chars: list[str] = []
    while i < length:
        char = text[i]
        if char == "}":
            brace_nesting -= 1
            if fmt_wraps_name:
                suffix_chars.append(char)
        elif char in _VECTOR_SUFFIX_CHARS:
            suffix_chars.append(char)
        else:
            return None
        i += 1
    suffix = "".join(suffix_chars)

    if brace_nesting != 0:
        return None

    if begin == end:
        return None
    if begin > end:
        begin, end = end, begin

    if max_members is not None and end - begin + 1 > max_members:
        raise ValueError("Bus vector exceeds the member limit")
    members = [f"{prefix}{idx}{suffix}" for idx in range(begin, end + 1)]
    return BusVector(prefix=prefix, members=members)


@dataclass(frozen=True)
class BusGroup:
    """The two out-parameters of a successful ``ParseBusGroup`` call.

    ``members`` carries the ``EscapeString( escapeSpacesForBus( tmp ),
    CTX_NETNAME )`` transform source applies at push time
    (``net_settings.cpp:1507``, ``:1524``).
    """

    prefix: str
    members: list[str]


def parse_bus_group(text: str) -> BusGroup | None:
    """Port of ``NET_SETTINGS::ParseBusGroup`` (``net_settings.cpp:1345-1534``).

    Returns ``None`` where the source function returns ``false``. *text* is
    used as given, matching ``parse_bus_vector``.

    An unnamed group, ``{A B}``, has no name in front of its opening brace.
    Source treats that opening brace as the start of the member list rather
    than an error, because the prefix loop only rejects a stray brace when it
    already found the closing side of a formatting marker
    (``:1408-1418``, the ``break`` branch).
    """
    length = len(text)
    i = 0
    prefix_chars: list[str] = []
    in_quotes = False
    brace_nesting = 0

    while i < length:
        char = text[i]

        if char == '"' and not _is_escaped(text, i):
            in_quotes = not in_quotes
            i += 1
            continue

        if in_quotes:
            if char == "\\" and i + 1 < length:
                prefix_chars.append(text[i + 1])
                i += 2
            else:
                prefix_chars.append(char)
                i += 1
            continue

        if char == "{":
            if i > 0 and _is_super_sub_overbar(text[i - 1]):
                brace_nesting += 1
                prefix_chars.append("{")
                i += 1
                continue
            break  # the group's own opening brace, not a formatting marker

        if char == "}":
            brace_nesting -= 1
            prefix_chars.append("}")
            i += 1
            continue

        if char == "\\" and i + 1 < length and text[i + 1] == " ":
            prefix_chars.append(text[i + 1])
            i += 2
            continue

        if char == " " or char == "[" or char == "]":
            return None

        prefix_chars.append(char)
        i += 1
    else:
        # Ran off the end without an opening '{'. Source reaches the same
        # result one step later, through the bounds check that opens the
        # member-parsing section (:1450-1451).
        return None

    if brace_nesting != 0:
        return None

    prefix = "".join(prefix_chars)

    # Parse members, net_settings.cpp:1446-1533.
    i += 1  # consume '{'
    if i >= length:
        return None

    in_quotes = False
    member_chars: list[str] = []
    members: list[str] = []
    while i < length:
        char = text[i]

        if char == '"' and not _is_escaped(text, i):
            in_quotes = not in_quotes
            i += 1
            continue

        if in_quotes:
            if char == "\\" and i + 1 < length:
                member_chars.append(text[i + 1])
                i += 2
            else:
                member_chars.append(char)
                i += 1
            continue

        if char == "{":
            if i > 0 and _is_super_sub_overbar(text[i - 1]):
                brace_nesting += 1
                member_chars.append("{")
                i += 1
                continue
            return None

        if char == "}":
            if brace_nesting:
                brace_nesting -= 1
                member_chars.append("}")
                i += 1
                continue
            member = "".join(member_chars)
            if member:
                members.append(escape_netname(_escape_spaces_for_bus(member)))
            return BusGroup(prefix=prefix, members=members)

        if char == "\\" and i + 1 < length and text[i + 1] == " ":
            member_chars.append(text[i + 1])
            i += 2
            continue

        if char == " " or char == ",":
            member = "".join(member_chars)
            if member:
                members.append(escape_netname(_escape_spaces_for_bus(member)))
            member_chars = []
            i += 1
            continue

        member_chars.append(char)
        i += 1

    return None  # ran off the end without a closing '}'


def is_bus_label(text: str) -> bool:
    """Port of ``SCH_CONNECTION::IsBusLabel`` (``sch_connection.cpp:526-532``).

    Unescapes *text* first, then tries a vector, then a group. A name with
    no bracket or brace fails both and returns ``False``: rule 3, a bare
    alias name is not a bus.
    """
    unescaped = unescape_string(text)
    return (
        parse_bus_vector(unescaped, max_members=10000) is not None
        or parse_bus_group(unescaped) is not None
    )


def might_be_bus_label(text: str) -> bool:
    """Port of ``SCH_CONNECTION::MightBeBusLabel`` (``sch_connection.cpp:535-541``).

    A cheap pre-filter, not a real parse. Source calls it "a weak heuristic
    for performance reasons" (``:537``). True whenever the unescaped text
    contains ``[`` or ``{``, regardless of whether that punctuation forms a
    valid vector or group.
    """
    unescaped = unescape_string(text)
    return "[" in unescaped or "{" in unescaped


class ConnectionType(Enum):
    """Port of the three ``CONNECTION_TYPE`` values ``ConfigureFromLabel`` sets.

    Source defines more values on the full enum (``NONE``, ``BUS_GROUP``,
    ...); only the three this classifier ever assigns are modeled here.
    """

    NET = "NET"
    BUS = "BUS"
    BUS_GROUP = "BUS_GROUP"


# The alias resolver a caller supplies. Takes an alias name and returns its
# member list in project-file order, or None when the name is not a known
# alias. Mirrors CONNECTION_GRAPH::GetBusAlias (connection_graph.cpp:3381-3386).
AliasResolver = Callable[[str], list[str] | None]


@dataclass(frozen=True)
class BusConnection:
    """One classified label, or one of its members, recursively.

    Ports the relevant fields of ``SCH_CONNECTION`` after
    ``ConfigureFromLabel`` runs (``sch_connection.cpp:140-213``).
    ``full_local_name`` ports ``FullLocalName()``
    (``sch_connection.h:147-150``), the local prefix plus the local name.

    A ``BUS`` or ``BUS_GROUP`` connection's own ``members`` are each a full
    ``BusConnection``, one level per ``ConfigureFromLabel`` call, matching
    source's ``m_members``. Deeper nesting is reachable only by classifying a
    member in turn. Source's own ``AllMembers()`` flattens exactly one level
    (``sch_connection.cpp:544-555``) and goes no further. This port does not
    even flatten that one level, because no phase in this task's scope reads
    past ``.members`` directly.
    """

    type: ConnectionType
    full_local_name: str
    members: tuple[BusConnection, ...] = ()
    # ``m_local_name`` and ``m_local_prefix`` (``sch_connection.h:273``, ``:278``).
    # ``full_local_name`` is their concatenation, so these look redundant here.
    # They are not: ``Clone`` REPLACES the name and KEEPS the local name
    # (``sch_connection.cpp:250-260``), and ``matchBusMember`` matches a group
    # member on the LOCAL name alone (``connection_graph.cpp:3298-3318``). A
    # member that carries only the joined form cannot be matched after a rename.
    local_name: str = ""
    local_prefix: str = ""
    # ``m_vector_index`` (``sch_connection.h:292``). Set per vector member at
    # ``sch_connection.cpp:168``. ``Reset`` defaults it to 0, NOT to -1
    # (``:236``), which is why ``matchBusMember``'s ``>= 0`` guard at
    # ``connection_graph.cpp:3321`` always passes.
    vector_index: int = 0

    @property
    def is_net(self) -> bool:
        return self.type is ConnectionType.NET

    @property
    def is_bus(self) -> bool:
        return self.type in (ConnectionType.BUS, ConnectionType.BUS_GROUP)


def configure_from_label(
    label_text: str,
    *,
    prefix: str = "",
    resolve_alias: AliasResolver | None = None,
    _depth: int = 0,
    _budget: list[int] | None = None,
) -> BusConnection:
    """Port of ``SCH_CONNECTION::ConfigureFromLabel`` (``sch_connection.cpp:140-213``).

    *label_text* is the raw label text, the same input source unescapes
    before classifying (``:151``). *prefix* is the caller's inherited
    ``m_prefix``: empty for a label read straight off a driver item, or the
    accumulated ``"NAME."`` a parent named group passes to its own members
    through ``SetPrefix`` before recursing (``:191``, ``:200``).
    *resolve_alias* is ``CONNECTION_GRAPH::GetBusAlias`` (``:186``). Passing
    ``None`` skips alias substitution, so an alias reference renders as a
    literal, unresolved group member instead: a caller with no alias table
    gets that fallback rather than a crash.

    Evaluation order matters and matches source: a vector is tried before a
    group, and both before a plain net (``:153-209``), rule 1 and rule 2's
    ``ParseBusVector`` behavior apply here through that call. Rule 3 follows
    from the same order: only a group member ever reaches
    ``resolve_alias`` (``:186``), never the top-level text.
    """
    if _depth > 64 or len(label_text) > 65536:
        raise ValueError("Bus expansion exceeds the depth or text limit.")
    budget = _budget if _budget is not None else [100000]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("Bus expansion exceeds the member limit.")
    unescaped = unescape_string(label_text)

    vector = parse_bus_vector(unescaped, max_members=min(10000, budget[0]))
    if vector is not None:
        budget[0] -= len(vector.members)
        members = tuple(
            BusConnection(
                type=ConnectionType.NET,
                full_local_name=f"{prefix}{member_name}",
                local_name=member_name,
                local_prefix=prefix,
                vector_index=index,
            )
            for index, member_name in enumerate(vector.members)
        )
        return BusConnection(
            type=ConnectionType.BUS,
            full_local_name=f"{prefix}{label_text}",
            members=members,
            local_name=label_text,
            local_prefix=prefix,
        )

    group = parse_bus_group(unescaped)
    if group is not None:
        # Named bus groups generate a net prefix, unnamed ones do not
        # (sch_connection.cpp:179-181).
        child_prefix = f"{group.prefix}." if group.prefix else ""
        group_members: list[BusConnection] = []
        for group_member in group.members:
            resolved = resolve_alias(group_member) if resolve_alias else None
            if resolved is not None:
                for alias_member in resolved:
                    group_members.append(
                        configure_from_label(
                            escape_netname(alias_member),
                            prefix=child_prefix,
                            resolve_alias=resolve_alias,
                            _depth=_depth + 1,
                            _budget=budget,
                        )
                    )
            else:
                group_members.append(
                    configure_from_label(
                        group_member,
                        prefix=child_prefix,
                        resolve_alias=resolve_alias,
                        _depth=_depth + 1,
                        _budget=budget,
                    )
                )
        return BusConnection(
            type=ConnectionType.BUS_GROUP,
            full_local_name=f"{prefix}{label_text}",
            members=tuple(group_members),
            local_name=label_text,
            local_prefix=prefix,
        )

    return BusConnection(
        type=ConnectionType.NET,
        full_local_name=f"{prefix}{label_text}",
        local_name=label_text,
        local_prefix=prefix,
    )
