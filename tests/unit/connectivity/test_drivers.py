"""The ported driver resolution, rule by rule against the source ordering.

Each test names the source line range it pins. A test here must fail if the
corresponding rule in ``candidate_cmp`` were dropped or reordered, because rule
ORDER is what decides the winner when candidates share a rank.
"""

from __future__ import annotations

from eedesigncore.connectivity.drivers import driver_priority, resolve, resolve_all
from eedesigncore.connectivity.geometry import PinRef
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph


def _pin(ref: str = "R1", num: str = "1", *, power: bool = False) -> PinRef:
    return PinRef(
        reference=ref,
        pin_number=num,
        point=(0, 0),
        electrical_type="power_in" if power else "passive",
        lib_id="L:S",
        from_power_symbol=power,
    )


def _d(kind: ItemKind, name: str, priority: Priority, **kw: object) -> Driver:
    return Driver(kind=kind, name=name, priority=priority, **kw)  # type: ignore[arg-type]


class TestTheRankTable:
    """``GetDriverPriority`` (`connection_graph.cpp:577-616`), eight values."""

    def test_the_two_power_ranks_are_distinct(self) -> None:
        """The stale doxygen comment merges them. The enum does not."""
        glob = driver_priority(ItemKind.PIN, is_global_power=True, is_local_power=False)
        loc = driver_priority(ItemKind.PIN, is_global_power=False, is_local_power=True)
        assert glob is Priority.GLOBAL_POWER_PIN
        assert loc is Priority.LOCAL_POWER_PIN
        assert glob > loc

    def test_a_global_label_outranks_a_global_power_pin(self) -> None:
        assert Priority.GLOBAL > Priority.GLOBAL_POWER_PIN

    def test_a_local_label_outranks_a_hierarchical_label(self) -> None:
        assert Priority.LOCAL_LABEL > Priority.HIER_LABEL

    def test_a_directive_label_never_drives(self) -> None:
        """It falls to the switch default (`:613-615`)."""
        got = driver_priority(
            ItemKind.DIRECTIVE_LABEL, is_global_power=False, is_local_power=False
        )
        assert got is Priority.NONE

    def test_an_unlisted_kind_returns_none_rather_than_guessing(self) -> None:
        for kind in (ItemKind.WIRE, ItemKind.JUNCTION, ItemKind.NO_CONNECT):
            got = driver_priority(kind, is_global_power=False, is_local_power=False)
            assert got is Priority.NONE


class TestStrongBeatsWeak:
    """A strong driver is preferred outright, before any tie-break runs."""

    def test_a_label_beats_a_pin_even_with_a_worse_name(self) -> None:
        sub = Subgraph(
            drivers=[
                _d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin()),
                _d(ItemKind.LABEL, "ZZZ_LAST_ALPHABETICALLY", Priority.LOCAL_LABEL),
            ]
        )
        resolve(sub)
        assert sub.name == "ZZZ_LAST_ALPHABETICALLY"
        assert sub.strong_driver

    def test_a_plain_pin_is_a_weak_driver(self) -> None:
        sub = Subgraph(drivers=[_d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin())])
        resolve(sub)
        assert not sub.strong_driver


class TestTieBreakRuleOrder:
    """``candidate_cmp`` (`connection_graph.cpp:193-258`), five rules in order."""

    def test_rule_1_a_bus_superset_wins(self) -> None:
        wide = _d(ItemKind.BUS, "WIDE", Priority.LOCAL_LABEL, bus_members=frozenset("abc"))
        narrow = _d(ItemKind.BUS, "AAAA", Priority.LOCAL_LABEL, bus_members=frozenset("ab"))
        sub = Subgraph(drivers=[narrow, wide])
        resolve(sub)
        # The superset wins even though the subset sorts first alphabetically,
        # which proves rule 1 runs before rule 5.
        assert sub.name == "WIDE"

    def test_rule_2_a_global_power_pin_beats_a_local_power_pin(self) -> None:
        glob = _d(ItemKind.PIN, "ZZZ", Priority.GLOBAL_POWER_PIN, pin=_pin(power=True))
        loc = _d(ItemKind.PIN, "AAA", Priority.LOCAL_POWER_PIN, pin=_pin(power=True))
        sub = Subgraph(drivers=[loc, glob])
        resolve(sub)
        # Both are strong, so the rank filter does not settle it. Rule 2 does, and
        # it beats the alphabetical rule.
        assert sub.name == "ZZZ"

    def test_rule_3_an_output_sheet_pin_wins(self) -> None:
        out = _d(ItemKind.SHEET_PIN, "ZZZ", Priority.SHEET_PIN, is_output_shape=True)
        other = _d(ItemKind.SHEET_PIN, "AAA", Priority.SHEET_PIN, is_output_shape=False)
        sub = Subgraph(drivers=[other, out])
        resolve(sub)
        assert sub.name == "ZZZ"

    def test_rule_4_a_pad_name_is_low_quality_and_loses(self) -> None:
        pad = _d(ItemKind.LABEL, "AAA-Pad1", Priority.LOCAL_LABEL)
        plain = _d(ItemKind.LABEL, "ZZZ", Priority.LOCAL_LABEL)
        sub = Subgraph(drivers=[pad, plain])
        resolve(sub)
        # "AAA-Pad1" sorts first alphabetically and still loses, which proves
        # rule 4 runs before rule 5.
        assert sub.name == "ZZZ"

    def test_rule_5_alphabetical_settles_the_rest(self) -> None:
        sub = Subgraph(
            drivers=[
                _d(ItemKind.LABEL, "BBB", Priority.LOCAL_LABEL),
                _d(ItemKind.LABEL, "AAA", Priority.LOCAL_LABEL),
            ]
        )
        resolve(sub)
        assert sub.name == "AAA"

    def test_the_comparator_is_a_total_order(self) -> None:
        """A comparator that is not consistent can sort differently by input order."""
        drivers = [
            _d(ItemKind.LABEL, "AAA", Priority.LOCAL_LABEL),
            _d(ItemKind.LABEL, "BBB", Priority.LOCAL_LABEL),
            _d(ItemKind.LABEL, "CCC-Pad1", Priority.LOCAL_LABEL),
        ]
        first = Subgraph(drivers=list(drivers))
        second = Subgraph(drivers=list(reversed(drivers)))
        resolve(first)
        resolve(second)
        assert first.name == second.name


class TestPhaseState:
    """Phase ordering is assertable, which the earlier design could not express."""

    def test_a_hierarchy_link_stays_unset_until_propagation_runs(self) -> None:
        sub = Subgraph(drivers=[_d(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL)])
        resolve(sub)
        assert sub.hier_parent is None
        assert sub.hier_children == []

    def test_a_bus_link_stays_unset_until_subgraph_processing_runs(self) -> None:
        sub = Subgraph(drivers=[_d(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL)])
        resolve(sub)
        assert sub.bus_neighbors == []
        assert sub.bus_parents == []

    def test_resolve_all_keeps_only_subgraphs_that_got_a_driver(self) -> None:
        driven = Subgraph(drivers=[_d(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL)])
        undriven = Subgraph()
        kept = resolve_all([driven, undriven])
        assert kept == [driven]
        assert undriven.driver is None


class TestUnconnectedPrefixCondition:
    """The phase 9f condition (`connection_graph.cpp:2606-2614`), exactly."""

    def test_it_fires_for_a_single_weak_pin_driver(self) -> None:
        sub = Subgraph(drivers=[_d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin())])
        resolve(sub)
        assert sub.needs_unconnected_prefix

    def test_it_does_not_fire_when_a_strong_driver_exists(self) -> None:
        sub = Subgraph(drivers=[_d(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL)])
        resolve(sub)
        assert not sub.needs_unconnected_prefix

    def test_it_does_not_fire_with_two_drivers(self) -> None:
        sub = Subgraph(
            drivers=[
                _d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin()),
                _d(ItemKind.PIN, "Net-(R2-Pad1)", Priority.PIN, pin=_pin("R2")),
            ]
        )
        resolve(sub)
        assert not sub.needs_unconnected_prefix

    def test_the_condition_is_not_a_member_count(self) -> None:
        """The earlier engine guessed "one member". That is a different rule.

        A subgraph can hold several pins while carrying one weak pin DRIVER, and
        the fixup still applies. Counting members gets this wrong.
        """
        sub = Subgraph(
            pins=[_pin("R1", "1"), _pin("R2", "1"), _pin("R3", "1")],
            drivers=[_d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin())],
        )
        resolve(sub)
        assert len(sub.members) == 3
        assert sub.needs_unconnected_prefix


class TestMembers:
    """A power symbol drives a name and never becomes a netlist node."""

    def test_a_power_symbol_pin_is_excluded_from_members(self) -> None:
        sub = Subgraph(pins=[_pin("R1", "1"), _pin("#PWR01", "1", power=True)])
        assert sub.members == frozenset({("R1", "1")})


class TestOnlyTheTopRankCompetes:
    """`connection_graph.cpp:160-176`. Two collections, not one.

    ``strong_drivers`` decides what gets DROPPED. ``candidates`` decides who
    WINS, and it is cleared every time a higher rank appears, so the tie-break
    only ever runs among equals.

    Conflating the two makes the winner depend on FILE ORDER whenever two ranks
    share a name, because the tie-break finds the names equal and expresses no
    preference. Every test below is written so it fails under that conflation.
    """

    def test_a_global_label_beats_a_local_label_of_the_SAME_name(self) -> None:
        """The exact case KiCad and this engine disagreed on.

        A global bus label and a local label of one name on one net. KiCad picks
        the global. The conflated version picked whichever came first.
        """
        local = _d(ItemKind.LABEL, "GB{SDA SCL}", Priority.LOCAL_LABEL)
        glob = _d(ItemKind.GLOBAL_LABEL, "GB{SDA SCL}", Priority.GLOBAL)
        for order in ([local, glob], [glob, local]):
            sub = Subgraph(drivers=list(order))
            resolve(sub)
            assert sub.driver is not None
            assert sub.driver.kind is ItemKind.GLOBAL_LABEL

    def test_the_winner_does_not_depend_on_driver_order(self) -> None:
        """Across three ranks with one shared name, both orders agree."""
        drivers = [
            _d(ItemKind.SHEET_PIN, "SIG", Priority.SHEET_PIN),
            _d(ItemKind.HIER_LABEL, "SIG", Priority.HIER_LABEL),
            _d(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL),
        ]
        forward, backward = (
            Subgraph(drivers=list(drivers)),
            Subgraph(drivers=list(reversed(drivers))),
        )
        resolve(forward)
        resolve(backward)
        assert forward.driver is not None
        assert backward.driver is not None
        assert forward.driver.kind is backward.driver.kind is ItemKind.LABEL

    def test_a_lower_rank_never_wins_the_tie_break(self) -> None:
        """Alphabetically lower, but outranked, so it must lose (`:257`)."""
        sub = Subgraph(
            drivers=[
                _d(ItemKind.LABEL, "AAA", Priority.LOCAL_LABEL),
                _d(ItemKind.GLOBAL_LABEL, "ZZZ", Priority.GLOBAL),
            ]
        )
        resolve(sub)
        assert sub.driver is not None
        assert sub.driver.name == "ZZZ"


class TestStrengthAndLocality:
    """`:179-183`, two one-line rules that are easy to write from intuition."""

    def test_a_sheet_pin_alone_is_WEAK(self) -> None:
        """Rank 2, below the HIER_LABEL threshold. Phase 8 relies on this."""
        sub = Subgraph(drivers=[_d(ItemKind.SHEET_PIN, "SIG", Priority.SHEET_PIN)])
        resolve(sub)
        assert not sub.strong_driver

    def test_a_hierarchical_label_is_the_lowest_STRONG_rank(self) -> None:
        sub = Subgraph(drivers=[_d(ItemKind.HIER_LABEL, "SIG", Priority.HIER_LABEL)])
        resolve(sub)
        assert sub.strong_driver

    def test_a_pin_driven_subgraph_IS_local(self) -> None:
        """`m_local_driver = ( highest < GLOBAL_POWER_PIN )`, so a pin counts.

        An earlier version listed four ranks by hand and left a plain pin out,
        which called a pin-driven subgraph non-local. That is backwards.
        """
        sub = Subgraph(drivers=[_d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin())])
        resolve(sub)
        assert sub.local_driver

    def test_a_global_label_is_NOT_local(self) -> None:
        sub = Subgraph(drivers=[_d(ItemKind.GLOBAL_LABEL, "VBUS", Priority.GLOBAL)])
        resolve(sub)
        assert not sub.local_driver

    def test_weak_drivers_are_dropped_once_a_strong_one_exists(self) -> None:
        """`:266-271`. A later phase must not be offered a weak secondary name."""
        sub = Subgraph(
            drivers=[
                _d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin()),
                _d(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL),
            ]
        )
        resolve(sub)
        assert [d.kind for d in sub.drivers] == [ItemKind.LABEL]
        # One pin and one label is NOT multiple drivers. Source counts only the
        # STRONG set (`:163-164`, `:262-263`), and a pin is not in it. This
        # assertion previously demanded the opposite, which is how the flag came
        # to be set on almost every net in a design and let two phases run where
        # KiCad never runs them.
        assert not sub.multiple_drivers

    def test_multiple_drivers_needs_two_STRONG_drivers(self) -> None:
        """`:262-263`. Two labels set the flag; a label beside a pin does not."""
        sub = Subgraph(
            drivers=[
                _d(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL),
                _d(ItemKind.HIER_LABEL, "PORT", Priority.HIER_LABEL),
            ]
        )
        resolve(sub)
        assert sub.multiple_drivers

    def test_a_pin_on_a_hash_reference_symbol_does_not_compete(self) -> None:
        """`:156-164`. A symbol whose instance reference starts with a hash is not
        in the netlist, so its ordinary pin is skipped before the contest."""
        sub = Subgraph(
            drivers=[
                _d(ItemKind.PIN, "Net-(#FLG01-Pad1)", Priority.PIN, pin=_pin("#FLG01")),
                _d(ItemKind.PIN, "Net-(C2-Pad1)", Priority.PIN, pin=_pin("C2")),
            ]
        )
        resolve(sub)
        assert sub.name == "Net-(C2-Pad1)"

    def test_weak_drivers_survive_when_nothing_is_strong(self) -> None:
        sub = Subgraph(
            drivers=[
                _d(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN, pin=_pin()),
                _d(ItemKind.SHEET_PIN, "SIG", Priority.SHEET_PIN),
            ]
        )
        resolve(sub)
        assert len(sub.drivers) == 2
