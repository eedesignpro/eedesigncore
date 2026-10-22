"""Ported ``generateGlobalPowerPinSubGraphs``, tested against source line ranges.

Each test names the source line range it pins. Source:
``eeschema/connection_graph.cpp:1797-1889`` at tag 10.0.3.
"""

from __future__ import annotations

from eedesigncore.connectivity.geometry import PinRef
from eedesigncore.connectivity.power_subgraphs import (
    GlobalPowerPinEntry,
    _sort_key,
    generate_global_power_pin_subgraphs,
)
from eedesigncore.connectivity.subgraph import ItemKind, Priority


def _power_pin(ref: str = "#PWR01", value: str = "GND", num: str = "1") -> PinRef:
    return PinRef(
        reference=ref,
        pin_number=num,
        point=(0, 0),
        electrical_type="power_in",
        lib_id="power:GND",
        pin_name=value,
        from_power_symbol=True,
        symbol_value=value,
    )


def _legacy_pin(ref: str, pin_name: str, num: str = "1") -> PinRef:
    """An invisible power-in pin on an ordinary, non-power symbol."""
    return PinRef(
        reference=ref,
        pin_number=num,
        point=(0, 0),
        electrical_type="power_in",
        lib_id="lib:IC",
        pin_name=pin_name,
        from_power_symbol=False,
    )


def _entry(
    sheet_path: tuple[str, ...],
    pin: PinRef,
    *,
    reference: str | None = None,
    already_connected: bool = False,
    already_has_subgraph: bool = False,
) -> GlobalPowerPinEntry:
    return GlobalPowerPinEntry(
        sheet_path=sheet_path,
        pin=pin,
        reference=reference if reference is not None else pin.reference,
        already_connected=already_connected,
        already_has_subgraph=already_has_subgraph,
    )


class TestNaming:
    """`:1849-1855`. Which field names the net."""

    def test_a_power_symbol_pin_names_the_net_from_its_value(self) -> None:
        pin = _power_pin(ref="#PWR01", value="+5V")
        result = generate_global_power_pin_subgraphs([_entry(("root",), pin)])
        assert [s.name for s in result.subgraphs] == ["+5V"]

    def test_a_legacy_pin_on_a_non_power_symbol_names_the_net_from_its_pin_name(self) -> None:
        pin = _legacy_pin("U1", "VDD")
        result = generate_global_power_pin_subgraphs([_entry(("root",), pin)])
        assert [s.name for s in result.subgraphs] == ["VDD"]


class TestConnectedPinSkip:
    """`:1836-1841`. A wired-up invisible pin is an ERC case, not a merge case."""

    def test_a_connected_legacy_pin_is_skipped(self) -> None:
        pin = _legacy_pin("U1", "VDD")
        entry = _entry(("root",), pin, already_connected=True)
        result = generate_global_power_pin_subgraphs([entry])
        assert result.subgraphs == []

    def test_a_connected_power_symbol_pin_is_never_skipped(self) -> None:
        """A power symbol pin is normally wired to its local stub. The
        exemption for `libParent->IsGlobalPower()` at `:1837` covers this.
        """
        pin = _power_pin()
        entry = _entry(("root",), pin, already_connected=True)
        result = generate_global_power_pin_subgraphs([entry])
        assert [s.name for s in result.subgraphs] == ["GND"]


class TestAlreadyClaimedSkip:
    """`:1843-1847`. Phase 3 already gave this pin a subgraph."""

    def test_a_pin_with_a_subgraph_already_is_skipped(self) -> None:
        pin = _power_pin()
        entry = _entry(("root",), pin, already_has_subgraph=True)
        result = generate_global_power_pin_subgraphs([entry])
        assert result.subgraphs == []


class TestSameNameMergesAcrossSheets:
    """`:1861-1885`, and audit "Answers to the specific questions" 3
    (lines 1104-1118): a name match merges regardless of sheet, with no
    hierarchy walk.
    """

    def test_two_pins_on_different_sheets_with_the_same_name_share_one_subgraph(self) -> None:
        a = _power_pin(ref="#PWR01", value="GND")
        b = _power_pin(ref="#PWR02", value="GND")
        result = generate_global_power_pin_subgraphs(
            [_entry(("root", "sheetA"), a), _entry(("root", "sheetB"), b)]
        )
        assert len(result.subgraphs) == 1
        merged = result.subgraphs[0]
        assert merged.pins == [a, b]
        assert merged.sheet_path == ("root", "sheetA")

    def test_two_pins_with_different_names_get_different_subgraphs(self) -> None:
        a = _power_pin(ref="#PWR01", value="GND")
        b = _power_pin(ref="#PWR02", value="+5V")
        result = generate_global_power_pin_subgraphs([_entry(("root",), a), _entry(("root",), b)])
        assert {s.name for s in result.subgraphs} == {"GND", "+5V"}

    def test_the_net_code_map_is_keyed_by_name(self) -> None:
        pin = _power_pin(value="GND")
        result = generate_global_power_pin_subgraphs([_entry(("root",), pin)])
        assert list(result.net_code_to_subgraphs) == ["GND"]
        assert result.net_code_to_subgraphs["GND"] == result.subgraphs


class TestFoundingDriverDoesNotChangeOnMerge:
    """A later same-name pin joins `m_items` but never re-runs
    `ResolveDrivers` (`:1864-1867` versus `:1877`). The founding pin, meaning
    whichever the sort placed first, stays the driver.
    """

    def test_the_first_sorted_pin_stays_the_driver_after_a_merge(self) -> None:
        first = _power_pin(ref="#PWR01", value="GND")
        second = _power_pin(ref="#PWR02", value="GND")
        result = generate_global_power_pin_subgraphs(
            [_entry(("root",), second), _entry(("root",), first)]
        )
        merged = result.subgraphs[0]
        assert merged.driver is not None
        assert merged.driver.pin is first

    def test_multiple_drivers_never_becomes_true_on_a_merge(self) -> None:
        """`m_multiple_drivers` is a `ResolveDrivers` side effect
        (`connection_graph.cpp:263-264`), and `ResolveDrivers` runs once per
        subgraph here, before any merge.
        """
        first = _power_pin(ref="#PWR01", value="GND")
        second = _power_pin(ref="#PWR02", value="GND")
        result = generate_global_power_pin_subgraphs(
            [_entry(("root",), first), _entry(("root",), second)]
        )
        assert result.subgraphs[0].multiple_drivers is False
        assert len(result.subgraphs[0].drivers) == 2


class TestResolvedDriverState:
    """`ResolveDrivers` (`:132-287`), reused via `drivers.resolve`, called
    once per founding subgraph (`:1877`).
    """

    def test_a_founding_subgraph_is_strongly_driven(self) -> None:
        result = generate_global_power_pin_subgraphs([_entry(("root",), _power_pin())])
        sub = result.subgraphs[0]
        assert sub.strong_driver is True
        assert sub.driver is not None
        assert sub.driver.priority is Priority.GLOBAL_POWER_PIN
        assert sub.driver.kind is ItemKind.PIN

    def test_a_founding_subgraph_is_not_a_local_driver(self) -> None:
        """`highest_priority < PRIORITY::GLOBAL_POWER_PIN` is false at rank 6
        itself (`:183`).
        """
        result = generate_global_power_pin_subgraphs([_entry(("root",), _power_pin())])
        assert result.subgraphs[0].local_driver is False

    def test_full_name_equals_name_for_a_global_power_pin(self) -> None:
        """A `GLOBAL_POWER_PIN` driver never prepends a sheet path
        (`subgraph.py`'s `prepend_path`).
        """
        result = generate_global_power_pin_subgraphs([_entry(("root", "child"), _power_pin())])
        sub = result.subgraphs[0]
        assert sub.full_name == sub.name


class TestDirtyStartsFalse:
    """`connection_graph.h:80-82` initializes `m_dirty( false )`, and this
    phase's creation block (`:1861-1885`) never sets it, unlike
    `buildItemSubGraphs` (`:1575`).
    """

    def test_a_new_subgraph_is_not_dirty(self) -> None:
        result = generate_global_power_pin_subgraphs([_entry(("root",), _power_pin())])
        assert result.subgraphs[0].dirty is False


class TestTheDeliberateSort:
    """`:1804-1828`. One of two places KiCad orders on purpose.

    Primary key sheet path (`SCH_SHEET_PATH::Cmp`, length before content),
    then the per-path reference, then the pin number.
    """

    def test_sort_key_orders_by_path_length_before_content(self) -> None:
        """`sch_sheet_path.cpp:199-203`. A shorter path always sorts first,
        even when its own first component would otherwise sort later.
        """
        short = _entry(("z",), _power_pin(num="1"), reference="Z1")
        long = _entry(("a", "a"), _power_pin(num="1"), reference="A1")
        assert _sort_key(short) < _sort_key(long)

    def test_sort_key_falls_to_reference_when_paths_are_equal_length(self) -> None:
        first = _entry(("root",), _power_pin(ref="#PWR01"), reference="U1")
        second = _entry(("root",), _power_pin(ref="#PWR02"), reference="U2")
        assert _sort_key(first) < _sort_key(second)

    def test_sort_key_falls_to_pin_number_last(self) -> None:
        first = _entry(("root",), _power_pin(num="1"), reference="U1")
        second = _entry(("root",), _power_pin(num="2"), reference="U1")
        assert _sort_key(first) < _sort_key(second)

    def test_processing_order_does_not_depend_on_input_order(self) -> None:
        """The sort runs before the merge loop, so feeding entries in either
        order produces the same founding driver.
        """
        low = _power_pin(ref="#PWR01", value="GND")
        high = _power_pin(ref="#PWR02", value="GND")
        forward = generate_global_power_pin_subgraphs(
            [_entry(("root",), low, reference="A1"), _entry(("root",), high, reference="B1")]
        )
        backward = generate_global_power_pin_subgraphs(
            [_entry(("root",), high, reference="B1"), _entry(("root",), low, reference="A1")]
        )
        assert forward.subgraphs[0].driver is not None
        assert backward.subgraphs[0].driver is not None
        assert forward.subgraphs[0].driver.pin is backward.subgraphs[0].driver.pin


class TestReferenceIsNotPinRefReference:
    """The reference gap, module docstring. ``GlobalPowerPinEntry.reference``
    is a distinct field from ``PinRef.reference``, so a caller can supply a
    correct per-instance ``GetRef`` result without this pin recording it.
    """

    def test_the_entry_reference_can_differ_from_the_pin_reference(self) -> None:
        pin = _power_pin(ref="U2")
        entry = _entry(("root",), pin, reference="U2A")
        assert entry.reference == "U2A"
        assert entry.pin.reference == "U2"
