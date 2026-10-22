"""The ported bus grammar and phase 7, pinned rule by rule against source.

Each test names the source line range it pins. ``TestTheFourRules`` covers the
four rules the task brief asked this port to verify, including both negative
cases: a single-index vector is not a bus, and a bare alias name is not a bus.
"""

from __future__ import annotations

from eedesigncore.connectivity.bus import (
    BusConnection,
    ConnectionType,
    configure_from_label,
    is_bus_label,
    might_be_bus_label,
    parse_bus_group,
    parse_bus_vector,
)
from eedesigncore.connectivity.bus_members import generate_bus_alias_members
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph


def _bus_driver(name: str, kind: ItemKind = ItemKind.GLOBAL_LABEL) -> Driver:
    """A label driver whose text is a bus, the only ``is_bus`` proxy this
    model exposes (see ``bus_members.py``'s module docstring)."""
    return Driver(kind=kind, name=name, priority=Priority.GLOBAL, bus_members=frozenset({"x"}))


def _net_driver(name: str, kind: ItemKind = ItemKind.GLOBAL_LABEL) -> Driver:
    return Driver(kind=kind, name=name, priority=Priority.GLOBAL)


class TestTheFourRules:
    """The four rules the task brief required this port to verify."""

    def test_rule_1_single_index_vector_is_not_a_bus(self) -> None:
        """``begin == end`` rejects. ``net_settings.cpp:1321-1322``."""
        assert parse_bus_vector("D[3..3]") is None
        assert is_bus_label("D[3..3]") is False

    def test_rule_1_two_index_vector_is_a_bus(self) -> None:
        """The boundary case one step away from rule 1: ``begin != end``
        accepts. ``net_settings.cpp:1321-1322``."""
        result = parse_bus_vector("D[3..4]")
        assert result is not None
        assert result.members == ["D3", "D4"]

    def test_rule_2_zero_padding_drops(self) -> None:
        """Indices round-trip through integer parsing, dropping leading
        zeros. ``net_settings.cpp:1264``, ``:1286``, ``:1331-1338``."""
        result = parse_bus_vector("D[00..03]")
        assert result is not None
        assert result.members == ["D0", "D1", "D2", "D3"]

    def test_rule_2_descending_range_swaps_to_ascending(self) -> None:
        """``net_settings.cpp:1323-1324``. A descending and an ascending
        range of the same two indices produce an identical member list."""
        descending = parse_bus_vector("D[7..0]")
        ascending = parse_bus_vector("D[0..7]")
        assert descending is not None
        assert ascending is not None
        assert descending.members == ascending.members
        assert descending.members == ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]

    def test_rule_3_bare_alias_name_is_not_a_bus(self) -> None:
        """A name with no ``[`` or ``{`` fails both parsers and classifies as
        ``NET``. ``sch_connection.cpp:186``, alias lookup runs only inside a
        group member loop, never reached here."""
        assert parse_bus_vector("LVDS") is None
        assert parse_bus_group("LVDS") is None
        assert is_bus_label("LVDS") is False

        classified = configure_from_label(
            "LVDS", resolve_alias=lambda name: ["DB_N", "DB_P"] if name == "LVDS" else None
        )
        assert classified.type is ConnectionType.NET

    def test_rule_3_alias_resolves_only_inside_a_bus_group(self) -> None:
        """``{LVDS}`` is a group whose sole member is the alias name, and
        alias substitution happens inside that member loop
        (``sch_connection.cpp:186-196``)."""
        classified = configure_from_label(
            "{LVDS}", resolve_alias=lambda name: ["DB_N", "DB_P"] if name == "LVDS" else None
        )
        assert classified.type is ConnectionType.BUS_GROUP
        assert [m.full_local_name for m in classified.members] == ["DB_N", "DB_P"]
        assert all(m.is_net for m in classified.members)

    def test_rule_4_alias_members_are_a_caller_supplied_table(self) -> None:
        """This module reads no schematic or project token for bus aliases.
        A caller with no ``resolve_alias`` gets the literal, unresolved
        group member back instead of a crash or an invented lookup."""
        classified = configure_from_label("{LVDS}")
        assert classified.type is ConnectionType.BUS_GROUP
        assert len(classified.members) == 1
        assert classified.members[0].type is ConnectionType.NET
        assert classified.members[0].full_local_name == "LVDS"


class TestParseBusVector:
    """``NET_SETTINGS::ParseBusVector``, ``net_settings.cpp:1137-1342``."""

    def test_plain_vector(self) -> None:
        result = parse_bus_vector("A[3..0]")
        assert result is not None
        assert result.prefix == "A"
        assert result.members == ["A0", "A1", "A2", "A3"]

    def test_empty_prefix_accepted(self) -> None:
        """``:1221-1248``. A leading ``[`` leaves an empty prefix, and the
        source's own doxygen comment claiming a name is required is stale."""
        result = parse_bus_vector("[0..3]")
        assert result is not None
        assert result.prefix == ""
        assert result.members == ["0", "1", "2", "3"]

    def test_formatted_name_wraps_the_signal_name(self) -> None:
        """``~{BE[0..3]}``. Formatting wraps the name, the closing brace
        becomes the suffix. ``:1238-1243``, ``:1305-1306``."""
        result = parse_bus_vector("~{BE[0..3]}")
        assert result is not None
        assert result.members == ["~{BE0}", "~{BE1}", "~{BE2}", "~{BE3}"]

    def test_formatted_range_strips_the_wrapper(self) -> None:
        """``D_{[1..2]}``. Formatting decorates the range, not the name, and
        the wrapper drops entirely. ``:1230-1236``."""
        result = parse_bus_vector("D_{[1..2]}")
        assert result is not None
        assert result.prefix == "D"
        assert result.members == ["D1", "D2"]

    def test_unbalanced_brace_rejected(self) -> None:
        """``:1318-1319``."""
        assert parse_bus_vector("~{BE[0..3]") is None

    def test_no_bracket_is_not_a_vector(self) -> None:
        assert parse_bus_vector("PLAIN_NET") is None

    def test_suffix_characters(self) -> None:
        """``:1308-1311``, the only accepted suffix characters."""
        result = parse_bus_vector("A[0..1]P")
        assert result is not None
        assert result.members == ["A0P", "A1P"]

    def test_suffix_rejects_other_characters(self) -> None:
        assert parse_bus_vector("A[0..1]Q") is None


class TestSourceQuirks:
    """Behavior this reading found in source, beyond the four required rules."""

    def test_truncated_vector_missing_close_bracket_still_parses(self) -> None:
        """``net_settings.cpp:1275-1295`` has no guard for running off the
        end of the string without a ``]``, unlike the begin-number section
        immediately above it. ``end`` is silently left at its initial ``0``,
        and the begin/end swap at ``:1323-1324`` turns ``D[3..5`` (missing
        its closing bracket) into ``D[0..3]``. This is a genuine, surprising
        source behavior, ported faithfully rather than guarded against."""
        result = parse_bus_vector("D[3..5")
        assert result is not None
        assert result.members == ["D0", "D1", "D2", "D3"]


class TestParseBusGroup:
    """``NET_SETTINGS::ParseBusGroup``, ``net_settings.cpp:1345-1534``."""

    def test_unnamed_group(self) -> None:
        result = parse_bus_group("{USB_DP USB_DN}")
        assert result is not None
        assert result.prefix == ""
        assert result.members == ["USB_DP", "USB_DN"]

    def test_named_group(self) -> None:
        result = parse_bus_group("MEMORY{RW CE OE}")
        assert result is not None
        assert result.prefix == "MEMORY"
        assert result.members == ["RW", "CE", "OE"]

    def test_comma_separator(self) -> None:
        """``:1521-1528``. Comma and space are both member separators."""
        result = parse_bus_group("{RW,CE,OE}")
        assert result is not None
        assert result.members == ["RW", "CE", "OE"]

    def test_empty_group_has_zero_members(self) -> None:
        """``:1448-1451``, ``:1496-1510``. An empty group still parses."""
        result = parse_bus_group("{}")
        assert result is not None
        assert result.members == []

    def test_member_space_escapes(self) -> None:
        """``:1360-1375``. A literal space in a member becomes ``\\ ``."""
        result = parse_bus_group('{"A B" C}')
        assert result is not None
        assert result.members == ["A\\ B", "C"]

    def test_missing_close_brace_rejected(self) -> None:
        """``:1533``. No closing ``}`` before the string ends."""
        assert parse_bus_group("{RW CE") is None

    def test_no_brace_is_not_a_group(self) -> None:
        assert parse_bus_group("PLAIN_NET") is None


class TestConfigureFromLabel:
    """``SCH_CONNECTION::ConfigureFromLabel``, ``sch_connection.cpp:140-213``."""

    def test_plain_net_has_no_members(self) -> None:
        classified = configure_from_label("RESET")
        assert classified.type is ConnectionType.NET
        assert classified.members == ()

    def test_vector_members_are_all_net(self) -> None:
        classified = configure_from_label("D[1..0]")
        assert classified.type is ConnectionType.BUS
        assert [m.type for m in classified.members] == [ConnectionType.NET, ConnectionType.NET]
        assert [m.full_local_name for m in classified.members] == ["D0", "D1"]

    def test_named_group_member_gets_the_dotted_prefix(self) -> None:
        """``sch_connection.cpp:179-181``. A named group inserts ``.``
        between its own name and each direct member's local name."""
        classified = configure_from_label("MEMORY{RW}")
        assert classified.type is ConnectionType.BUS_GROUP
        assert classified.members[0].full_local_name == "MEMORY.RW"

    def test_unnamed_group_member_gets_no_prefix(self) -> None:
        classified = configure_from_label("{RW}")
        assert classified.members[0].full_local_name == "RW"

    def test_nested_vector_in_a_group_is_a_bus_typed_member(self) -> None:
        """A vector member of a group is a direct ``BUS``-typed child, not
        flattened to its own net leaves at this level.
        ``sch_connection.cpp:174-205``, the group-member loop recurses
        through ``ConfigureFromLabel`` exactly once per member."""
        classified = configure_from_label("MEMORY{D[1..0] RW}")
        assert classified.type is ConnectionType.BUS_GROUP
        types = [m.type for m in classified.members]
        assert types == [ConnectionType.BUS, ConnectionType.NET]
        assert classified.members[1].full_local_name == "MEMORY.RW"


class TestMightBeBusLabel:
    """``SCH_CONNECTION::MightBeBusLabel``, ``sch_connection.cpp:535-541``."""

    def test_true_for_bracket_or_brace(self) -> None:
        assert might_be_bus_label("D[7..0]") is True
        assert might_be_bus_label("{USB_DP USB_DN}") is True

    def test_false_for_a_plain_net(self) -> None:
        assert might_be_bus_label("RESET") is False

    def test_is_a_superset_of_is_bus_label(self) -> None:
        """``:594-596``, ``might_be_bus_label`` never returns ``False`` where
        ``is_bus_label`` returns ``True``."""
        for text in ("D[7..0]", "D[3..3]", "{RW CE}", "{}", "PLAIN"):
            if is_bus_label(text):
                assert might_be_bus_label(text) is True


class TestGenerateBusAliasMembers:
    """Phase 7, ``CONNECTION_GRAPH::generateBusAliasMembers``,
    ``connection_graph.cpp:1733-1794``."""

    def test_one_placeholder_per_net_member_of_a_vector_label(self) -> None:
        subgraph = Subgraph(sheet_path=("root",), drivers=[_bus_driver("D[1..0]")])

        new_subgraphs = generate_bus_alias_members([subgraph])

        assert len(new_subgraphs) == 2
        assert {s.name for s in new_subgraphs} == {"D0", "D1"}
        assert all(s.is_bus_member for s in new_subgraphs)
        assert all(s.strong_driver for s in new_subgraphs)

    def test_placeholder_starts_clean_not_dirty(self) -> None:
        """Source's ``CONNECTION_SUBGRAPH`` constructor default is
        ``m_dirty(false)`` (``connection_graph.h:82``), and
        ``generateBusAliasMembers`` never touches ``m_dirty`` on ``new_sg``,
        unlike ``buildItemSubGraphs`` which sets it true (``:1575``)."""
        subgraph = Subgraph(sheet_path=(), drivers=[_bus_driver("{RESET}")])
        (placeholder,) = generate_bus_alias_members([subgraph])
        assert placeholder.dirty is False

    def test_placeholder_has_no_driver_item(self) -> None:
        """Source never assigns ``new_sg->m_driver``, only
        ``m_driver_connection`` (``:1776``), so ``GetNetName()`` on a
        placeholder returns empty in source
        (``connection_graph.cpp:317-320``). Ported faithfully."""
        subgraph = Subgraph(sheet_path=(), drivers=[_bus_driver("{RESET}")])
        (placeholder,) = generate_bus_alias_members([subgraph])
        assert placeholder.driver is None

    def test_nested_bus_member_of_a_group_is_skipped(self) -> None:
        """``!conn->IsNet()`` at ``:1755-1756``. A vector nested inside a
        group produces no placeholder for its own net leaves at this
        phase."""
        subgraph = Subgraph(sheet_path=(), drivers=[_bus_driver("MEMORY{D[1..0] RW}")])

        new_subgraphs = generate_bus_alias_members([subgraph])

        assert {s.name for s in new_subgraphs} == {"MEMORY.RW"}

    def test_alias_reference_expands_through_the_resolver(self) -> None:
        subgraph = Subgraph(sheet_path=(), drivers=[_bus_driver("HSAD{LVDS}")])

        new_subgraphs = generate_bus_alias_members(
            [subgraph],
            resolve_alias=lambda name: ["DB_N", "DB_P"] if name == "LVDS" else None,
        )

        assert {s.name for s in new_subgraphs} == {"HSAD.DB_N", "HSAD.DB_P"}

    def test_non_bus_label_driver_produces_no_placeholder(self) -> None:
        """``GetAllBusLabels`` only admits a driver whose OWN connection type
        is ``BUS``/``BUS_GROUP`` (``connection_graph.cpp:341-358``)."""
        subgraph = Subgraph(sheet_path=(), drivers=[_net_driver("RESET")])
        assert generate_bus_alias_members([subgraph]) == []

    def test_a_pin_driver_is_never_a_bus_label(self) -> None:
        """A pin is excluded by ``GetAllBusLabels``'s own switch, whose
        default branch covers everything but a label, a global label, or a
        hierarchical label (``:341-358``)."""
        pin_driver = Driver(
            kind=ItemKind.PIN,
            name="D[1..0]",
            priority=Priority.PIN,
            bus_members=frozenset({"x"}),
        )
        subgraph = Subgraph(sheet_path=(), drivers=[pin_driver])
        assert generate_bus_alias_members([subgraph]) == []

    def test_returns_new_subgraphs_without_mutating_the_input_list(self) -> None:
        subgraph = Subgraph(sheet_path=(), drivers=[_bus_driver("D[1..0]")])
        driver_subgraphs = [subgraph]

        generate_bus_alias_members(driver_subgraphs)

        assert driver_subgraphs == [subgraph]


def test_bus_connection_is_bus_property() -> None:
    net = BusConnection(type=ConnectionType.NET, full_local_name="RESET")
    bus = BusConnection(type=ConnectionType.BUS, full_local_name="D[1..0]")
    group = BusConnection(type=ConnectionType.BUS_GROUP, full_local_name="MEMORY{RW}")
    assert net.is_bus is False
    assert bus.is_bus is True
    assert group.is_bus is True
