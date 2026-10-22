"""Geometry-level connectivity rules, each pinned to its source citation.

This file covers the modules that turn a schematic into an adjacency: integer
units, net-name escaping, pin position derivation, and the point-map rules.

Driver resolution, naming, propagation, and processing moved to their own test
files when those phases were ported. Keeping duplicates here would test the same
rule twice and hide which module owns it.
"""

from __future__ import annotations

import pytest

from eedesigncore.connectivity.build import _on_segment, build_subgraphs
from eedesigncore.connectivity.geometry import (
    PinRef,
    SheetGeometry,
    apply,
    compose,
    pin_sheet_position,
    symbol_transform,
)
from eedesigncore.connectivity.netname import SLASH_TOKEN, escape_netname
from eedesigncore.connectivity.subgraph import Subgraph
from eedesigncore.connectivity.units import iu_to_mm, mm_to_iu, point_to_iu


def _pin(ref: str, num: str, point: tuple[int, int], kind: str = "passive") -> PinRef:
    return PinRef(
        reference=ref,
        pin_number=num,
        point=point,
        electrical_type=kind,
        lib_id="L:S",
        pin_name=num,
    )


def _members(subgraphs: list[Subgraph]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for sub in subgraphs:
        out |= sub.members
    return out


class TestUnitsAreExact:
    """Integer conversion is lossless for anything KiCad writes."""

    @pytest.mark.parametrize(
        "value", [0.0, 1.0, 2.54, 0.1, 308.0385, 157.48, -0.000091, 0.000001, 115.57]
    )
    def test_round_trip_is_exact(self, value: float) -> None:
        assert iu_to_mm(mm_to_iu(value)) == value

    def test_scaling_a_float_directly_would_be_wrong(self) -> None:
        """0.1 has no exact binary form, so scale-then-round can land one unit off.

        One unit breaks a connection, because matching is exact equality.
        """
        assert mm_to_iu(0.1) == 100_000

    def test_a_point_becomes_a_lexicographic_tuple(self) -> None:
        """KiCad's own operator compares MAGNITUDE (`vector2d.h:577-581`).

        It would treat (3,4) and (5,0) as equivalent. A tuple does not, which is
        the behavior the point map needs.
        """
        assert point_to_iu((3.0, 4.0)) < point_to_iu((5.0, 0.0))


class TestNetNameEscaping:
    """``CTX_NETNAME`` is two substitutions and no others."""

    def test_slash_becomes_the_token(self) -> None:
        assert escape_netname("A/B") == f"A{SLASH_TOKEN}B"

    def test_newlines_drop(self) -> None:
        assert escape_netname("A\nB\rC") == "ABC"

    @pytest.mark.parametrize("text", ["a b", "a-b", "(a)", "{a}", "#a", "a.b", "a+b"])
    def test_nothing_else_changes(self, text: str) -> None:
        """The negative half of the rule, which is the half that goes wrong."""
        assert escape_netname(text) == text

    def test_escaping_twice_is_the_same_as_once(self) -> None:
        once = escape_netname("A/B")
        assert escape_netname(once) == once


class TestPinPositionDerivation:
    """The two corpus cross-checks from the geometry audit section 6."""

    def test_power_symbol_at_the_origin(self) -> None:
        got = pin_sheet_position((0.0, 0.0), symbol_transform(0, None), (257.81, 186.69))
        assert got == point_to_iu((257.81, 186.69))

    def test_resistor_with_y_inversion(self) -> None:
        """Library Y is negated at parse, so a pin at -2.54 reads as +2.54.

        The corpus file has wire endpoints at exactly these two points, which
        confirms the negation and the identity transform together.
        """
        transform = symbol_transform(0, None)
        assert pin_sheet_position((0.0, -2.54), transform, (115.57, 196.85)) == point_to_iu(
            (115.57, 199.39)
        )
        assert pin_sheet_position((0.0, 2.54), transform, (115.57, 196.85)) == point_to_iu(
            (115.57, 194.31)
        )

    @pytest.mark.parametrize(
        ("angle", "expected"),
        [
            (0, (1, 0, 0, 1)),
            (90, (0, 1, -1, 0)),
            (180, (-1, 0, 0, -1)),
            (270, (0, -1, 1, 0)),
        ],
    )
    def test_the_four_rotations(self, angle: int, expected: tuple[int, ...]) -> None:
        assert symbol_transform(angle, None) == expected

    def test_the_transform_index_layout_is_not_the_intuitive_one(self) -> None:
        """x1 and y1 build the output X, per `transform.cpp:44-47`."""
        assert apply((0, 1, -1, 0), (2.0, 3.0)) == (3.0, -2.0)

    def test_mirror_composes_after_the_angle(self) -> None:
        """Worked from the cited formula at `sch_symbol.cpp:2508-2514`.

        An earlier version of this test asserted a different tuple from intuition
        and was wrong. The formula is the authority.
        """
        assert compose((0, 1, -1, 0), (1, 0, 0, -1)) == (0, 1, 1, 0)

    def test_an_unsupported_angle_raises_rather_than_guessing(self) -> None:
        with pytest.raises(ValueError, match="not one of 0, 90, 180, 270"):
            symbol_transform(45, None)


class TestPointMapRules:
    """Phases 2 and 3 geometry, through the ported builder."""

    def test_a_wire_joins_two_pins_at_its_exact_endpoints(self) -> None:
        geo = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0))],
            pins=[_pin("R1", "1", (0, 0)), _pin("R2", "1", (1000, 0))],
        )
        subs = build_subgraphs(geo)
        assert len(subs) == 1
        assert _members(subs) == {("R1", "1"), ("R2", "1")}

    def test_one_unit_of_difference_breaks_the_connection(self) -> None:
        """There is no tolerance. This is the whole matching rule."""
        geo = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0))],
            pins=[_pin("R1", "1", (0, 0)), _pin("R2", "1", (1001, 0))],
        )
        assert len(build_subgraphs(geo)) == 2

    def test_three_wire_endpoints_at_one_point_all_connect(self) -> None:
        """A split T-junction needs no junction dot."""
        geo = SheetGeometry(
            wire_ends=[((0, 0), (500, 0)), ((500, 0), (1000, 0)), ((500, 0), (500, 500))],
            pins=[
                _pin("R1", "1", (0, 0)),
                _pin("R2", "1", (1000, 0)),
                _pin("R3", "1", (500, 500)),
            ],
        )
        assert len(build_subgraphs(geo)) == 1

    def test_two_wires_that_merely_cross_do_not_connect(self) -> None:
        """Neither contributes the crossing point."""
        geo = SheetGeometry(
            wire_ends=[((-500, 0), (500, 0)), ((0, -500), (0, 500))],
            pins=[_pin("R1", "1", (-500, 0)), _pin("R2", "1", (0, -500))],
        )
        assert len(build_subgraphs(geo)) == 2

    def test_a_junction_bridges_an_endpoint_to_an_unsplit_wire(self) -> None:
        """Without the junction there is no link."""
        without = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0)), ((500, 0), (500, 500))],
            pins=[_pin("R1", "1", (0, 0)), _pin("R2", "1", (500, 500))],
        )
        assert len(build_subgraphs(without)) == 2

        withdot = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0)), ((500, 0), (500, 500))],
            junctions=[(500, 0)],
            pins=[_pin("R1", "1", (0, 0)), _pin("R2", "1", (500, 500))],
        )
        assert len(build_subgraphs(withdot)) == 1

    def test_a_no_connect_pin_is_isolated_but_not_excluded(self) -> None:
        """Isolation is not exclusion. The gate blocks EDGES, not membership.

        ``ConnectionPropagatesTo`` returns false for a no-connect pin
        (`sch_pin.cpp:1631-1634`), so no edge reaches it. The pin still enters
        the item list and still seeds its OWN subgraph, because
        ``buildItemSubGraphs`` creates one for every item with no code yet
        (`connection_graph.cpp:1511-1526`). Phase 9f then names it.

        An earlier version of this test asserted the pin vanished, which is what
        the engine wrongly did, and that lost the single-pin net KiCad emits.
        """
        geo = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0))],
            pins=[_pin("R1", "1", (0, 0)), _pin("U1", "1", (1000, 0), kind="no_connect")],
        )
        subs = build_subgraphs(geo)

        # Two subgraphs: the wired one, and the no-connect pin alone.
        assert len(subs) == 2
        groups = {frozenset(s.members) for s in subs}
        assert frozenset({("R1", "1")}) in groups
        assert frozenset({("U1", "1")}) in groups

        # The two never share a subgraph, which is what the gate guarantees.
        for sub in subs:
            refs = {r for r, _ in sub.members}
            assert not ("R1" in refs and "U1" in refs)

    def test_a_wire_and_a_bus_never_join_directly(self) -> None:
        """The two-way gate requires the bus flag to match on both sides."""
        geo = SheetGeometry(
            wire_ends=[((0, 0), (500, 0))],
            bus_ends=[((500, 0), (1000, 0))],
            pins=[_pin("R1", "1", (0, 0)), _pin("R2", "1", (1000, 0))],
        )
        assert len(build_subgraphs(geo)) == 2

    def test_a_no_connect_marker_marks_but_never_names(self) -> None:
        geo = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0))],
            no_connects=[(1000, 0)],
            pins=[_pin("R1", "1", (0, 0))],
        )
        subs = build_subgraphs(geo)
        assert len(subs) == 1
        assert subs[0].no_connect

    def test_a_label_on_the_middle_of_a_wire_still_connects(self) -> None:
        """UNRESOLVED against source. See the open-question record.

        Source sets the overlap threshold at two lines. Observation across the
        corpus shows one line and KiCad still names the net from the label. This
        keeps the behavior that matches KiCad.
        """
        geo = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0))],
            labels=[((500, 0), "label", "SIG")],
            pins=[_pin("R1", "1", (0, 0))],
        )
        subs = build_subgraphs(geo)
        assert len(subs) == 1
        assert any(d.name == "SIG" for d in subs[0].drivers)

    def test_a_directive_label_contributes_no_driver(self) -> None:
        """It falls to the rank switch default, so it never names a net."""
        geo = SheetGeometry(
            wire_ends=[((0, 0), (1000, 0))],
            directive_labels=[((500, 0), "NETCLASS")],
            pins=[_pin("R1", "1", (0, 0))],
        )
        subs = build_subgraphs(geo)
        assert all(d.name != "NETCLASS" for s in subs for d in s.drivers)

    def test_a_bus_entry_never_joins_the_bus_it_touches(self) -> None:
        """The rule that keeps a bus from shorting all of its own members.

        ``SCH_BUS_WIRE_ENTRY::ConnectionPropagatesTo`` returns false for a bus
        line (`sch_bus_entry.cpp:557-565`). Source's own reason: there is a
        connectivity change at that point, A[7..0] to A7. The bus reaches its
        members by NAME, never through this edge.

        Two member wires hang off one bus through their own entries. Without the
        rule they short to the bus and therefore to each other, which collapses
        the whole bus into one net. That failure looked exactly like a bus member
        NAMING fault and is not one.
        """
        geo = SheetGeometry(
            bus_ends=[((0, 0), (1000, 0))],
            bus_entries=[(((100, -100), (200, 0))), (((600, -100), (700, 0)))],
            wire_ends=[((100, -100), (0, -100)), ((600, -100), (900, -100))],
            pins=[_pin("R1", "1", (0, -100)), _pin("R2", "1", (900, -100))],
        )
        subs = build_subgraphs(geo)
        for sub in subs:
            refs = {r for r, _ in sub.members}
            assert not ("R1" in refs and "R2" in refs)

    def test_two_bus_entries_never_join_each_other(self) -> None:
        """`sch_bus_entry.cpp:581-582`, the last clause of the same gate."""
        geo = SheetGeometry(
            bus_entries=[((0, 0), (100, 100)), ((100, 100), (200, 200))],
            pins=[_pin("R1", "1", (0, 0)), _pin("R2", "1", (200, 200))],
        )
        assert len(build_subgraphs(geo)) == 2

    def test_a_bus_entry_at_a_bus_point_connects_to_the_bus_only(self) -> None:
        """``bus_connection_ok`` (`connection_graph.cpp:1456-1466`).

        Source's reason: if the user overlapped another net line with the entry's
        bus end, that must not short the two together. Here a wire endpoint sits
        on the same point as the entry's bus end, and stays separate.
        """
        geo = SheetGeometry(
            bus_ends=[((0, 0), (1000, 0))],
            bus_entries=[((100, -100), (200, 0))],
            wire_ends=[((200, 0), (200, 500))],
            pins=[_pin("R1", "1", (100, -100)), _pin("R2", "1", (200, 500))],
        )
        subs = build_subgraphs(geo)
        for sub in subs:
            refs = {r for r, _ in sub.members}
            assert not ("R1" in refs and "R2" in refs)

    def test_on_segment_excludes_the_endpoints(self) -> None:
        assert _on_segment((500, 0), (0, 0), (1000, 0))
        assert not _on_segment((0, 0), (0, 0), (1000, 0))
        assert not _on_segment((500, 1), (0, 0), (1000, 0))
