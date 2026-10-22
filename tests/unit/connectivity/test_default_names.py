"""The naming layer: ``getDefaultConnection``, ``GetDefaultNetName``, and the
phase 9f unconnected-prefix fixup.

Each test names the source line range it pins, per ADR-090.
"""

from __future__ import annotations

import pytest

from eedesigncore.connectivity.default_names import (
    UnannotatedSymbolNameNotSupported,
    apply_unconnected_prefix_fixup,
    get_default_connection_name,
    get_default_net_name,
)
from eedesigncore.connectivity.geometry import PinRef
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph


def _pin(
    ref: str = "R1",
    num: str = "1",
    name: str = "",
    *,
    power: bool = False,
    value: str = "",
    unit_letter: str = "",
    electrical_type: str | None = None,
) -> PinRef:
    # `PinRef.is_power` requires BOTH `electrical_type == "power_in"` and
    # `from_power_symbol`, so a power pin must carry the matching electrical
    # type unless the test overrides it on purpose.
    if electrical_type is not None:
        resolved_type = electrical_type
    else:
        resolved_type = "power_in" if power else "passive"
    return PinRef(
        reference=ref,
        pin_number=num,
        point=(0, 0),
        electrical_type=resolved_type,
        lib_id="L:S",
        pin_name=name,
        from_power_symbol=power,
        symbol_value=value,
        unit_letter=unit_letter,
    )


class TestPowerShortCircuit:
    """``sch_pin.cpp:1441-1455``, runs before any of the three branches."""

    def test_a_power_pin_names_from_the_symbol_value(self) -> None:
        pin = _pin(ref="#PWR01", num="1", name="GND", power=True, value="GND")
        assert get_default_net_name(pin, force_no_connect=False) == "GND"

    def test_the_power_short_circuit_pre_empts_branch_b(self) -> None:
        """A power pin with a distinct pin name still names from VALUE, not
        from the pin name (`:1441-1447` runs before `:1516-1530` is reached).
        """
        pin = _pin(ref="#PWR03", num="1", name="VCC", power=True, value="+5V")
        assert get_default_net_name(pin, force_no_connect=True) == "+5V"

    def test_the_symbol_value_is_escaped(self) -> None:
        """`sch_pin.cpp:1447`, `EscapeString( ..., CTX_NETNAME )`."""
        pin = _pin(ref="#PWR04", num="1", name="GND", power=True, value="A/B")
        assert get_default_net_name(pin, force_no_connect=False) == "A{slash}B"


class TestBranchA:
    """`sch_pin.cpp:1504-1515`. No symbol UUID is available on `PinRef`."""

    def test_an_unannotated_reference_raises_rather_than_guesses(self) -> None:
        pin = _pin(ref="R?", num="1", name="")
        with pytest.raises(UnannotatedSymbolNameNotSupported):
            get_default_net_name(pin, force_no_connect=False)

    def test_the_check_is_on_the_reference_not_the_pin(self) -> None:
        """An annotated reference never raises, even with the same pin shape."""
        pin = _pin(ref="R1", num="1", name="")
        assert get_default_net_name(pin, force_no_connect=False) == "Net-(R1-Pad1)"


class TestBranchB:
    """`sch_pin.cpp:1516-1530`. Pin name distinct from pin number.

    The reference takes the unit token (`:1518-1520`).
    """

    def test_reference_carries_the_unit_token(self) -> None:
        pin = _pin(ref="U9", num="3", name="CLK", unit_letter="A")
        assert get_default_net_name(pin, force_no_connect=False) == "Net-(U9A-CLK)"

    def test_no_pad_suffix_when_connected_and_not_has_multiple(self) -> None:
        """`:1523-1527`: `-Pad<n>` only appears when unconnected or has_multiple."""
        pin = _pin(ref="U9", num="3", name="CLK", unit_letter="A")
        name = get_default_net_name(pin, force_no_connect=False)
        assert "-Pad" not in name

    def test_pad_suffix_appears_when_unconnected(self) -> None:
        pin = _pin(ref="U9", num="3", name="CLK", unit_letter="A")
        assert get_default_net_name(pin, force_no_connect=True) == "unconnected-(U9A-CLK-Pad3)"

    def test_pad_suffix_appears_when_has_multiple(self) -> None:
        """`:1481-1490`: another pin shares this shown name, differs in shown
        number, and matches this pin's NC status.
        """
        pin = _pin(ref="U9", num="3", name="CLK", unit_letter="A")
        sibling = _pin(ref="U9", num="4", name="CLK", unit_letter="A")
        name = get_default_net_name(pin, force_no_connect=False, sibling_pins=[sibling])
        assert name == "Net-(U9A-CLK-Pad3)"

    def test_has_multiple_requires_matching_nc_status(self) -> None:
        """A sibling with a different NC status does not trigger `has_multiple`
        (`:1485`, `unconnected == ( pin->GetType() == PT_NC )`).
        """
        pin = _pin(ref="U9", num="3", name="CLK", unit_letter="A")
        nc_sibling = _pin(
            ref="U9", num="4", name="CLK", unit_letter="A", electrical_type="no_connect"
        )
        name = get_default_net_name(pin, force_no_connect=False, sibling_pins=[nc_sibling])
        assert "-Pad" not in name

    def test_the_pin_name_is_escaped(self) -> None:
        pin = _pin(ref="U9", num="3", name="A/B", unit_letter="A")
        assert get_default_net_name(pin, force_no_connect=False) == "Net-(U9A-A{slash}B)"

    def test_reference_itself_is_not_escaped(self) -> None:
        """Audit escaping table: reference designator from `GetRef` is not
        escaped (`sch_pin.cpp:1520`).
        """
        pin = _pin(ref="U9", num="3", name="CLK", unit_letter="A")
        assert "{slash}" not in get_default_net_name(pin, force_no_connect=False).split("-CLK")[0]


class TestBranchC:
    """`sch_pin.cpp:1531-1536`. Pin name equals the number, or is empty.

    NO unit token (`:1533`).
    """

    def test_empty_pin_name_skips_the_unit_token(self) -> None:
        pin = _pin(ref="R1", num="1", name="", unit_letter="A")
        assert get_default_net_name(pin, force_no_connect=False) == "Net-(R1-Pad1)"

    def test_pin_name_equal_to_number_skips_the_unit_token(self) -> None:
        pin = _pin(ref="R1", num="1", name="1", unit_letter="A")
        assert get_default_net_name(pin, force_no_connect=False) == "Net-(R1-Pad1)"

    def test_unconnected_form(self) -> None:
        pin = _pin(ref="R1", num="1", name="", unit_letter="A")
        assert get_default_net_name(pin, force_no_connect=True) == "unconnected-(R1-Pad1)"

    def test_effective_pad_number_is_escaped(self) -> None:
        pin = _pin(ref="R1", num="1/2", name="")
        assert get_default_net_name(pin, force_no_connect=False) == "Net-(R1-Pad1{slash}2)"


class TestUnconnectedFromNoConnectPinType:
    """`sch_pin.cpp:1470-1474`: a `PT_NC` pin is unconnected even when
    `force_no_connect` is false.
    """

    def test_a_no_connect_pin_gets_the_unconnected_prefix_unforced(self) -> None:
        pin = _pin(ref="R1", num="1", name="", electrical_type="no_connect")
        assert get_default_net_name(pin, force_no_connect=False) == "unconnected-(R1-Pad1)"


class TestGetDefaultConnectionName:
    """`connection_graph.cpp:3243-3273`."""

    def test_a_power_pin_returns_its_resolved_name(self) -> None:
        pin = _pin(ref="#PWR01", num="1", name="GND", power=True, value="GND")
        driver = Driver(kind=ItemKind.PIN, name="GND", priority=Priority.GLOBAL_POWER_PIN, pin=pin)
        assert get_default_connection_name(driver) == "GND"

    def test_a_plain_pin_returns_none(self) -> None:
        """`:3250-3254`: only a pin with `IsPower()` builds a connection."""
        pin = _pin(ref="R1", num="1")
        driver = Driver(kind=ItemKind.PIN, name="Net-(R1-Pad1)", priority=Priority.PIN, pin=pin)
        assert get_default_connection_name(driver) is None

    @pytest.mark.parametrize("kind", [ItemKind.GLOBAL_LABEL, ItemKind.HIER_LABEL, ItemKind.LABEL])
    def test_every_label_kind_returns_its_resolved_name(self, kind: ItemKind) -> None:
        """`:3256-3260`."""
        driver = Driver(kind=kind, name="MY_NET", priority=Priority.GLOBAL)
        assert get_default_connection_name(driver) == "MY_NET"

    def test_a_directive_label_returns_none(self) -> None:
        """`:3262-3264`, the `default:` case. A directive label is not one of
        the three handled label types.
        """
        driver = Driver(kind=ItemKind.DIRECTIVE_LABEL, name="rule", priority=Priority.NONE)
        assert get_default_connection_name(driver) is None

    def test_a_sheet_pin_returns_none(self) -> None:
        driver = Driver(kind=ItemKind.SHEET_PIN, name="DATA", priority=Priority.SHEET_PIN)
        assert get_default_connection_name(driver) is None


class TestTheTwoPassesDisagree:
    """The single most important behavior: driver-resolution naming and the
    phase 9f fixup produce DIFFERENT prefixes for the same floating pin
    (`connection_graph.cpp:2606-2614` vs. the ordinary, unforced call).
    """

    def test_a_floating_pin_is_net_by_default_and_unconnected_after_fixup(self) -> None:
        pin = _pin(ref="R1", num="1", name="")

        # Driver resolution's own call (`driverName`, connection_graph.cpp:404)
        # passes the SUBGRAPH's own no-connect state. A floating pin with no
        # no-connect flag on it gets the ordinary "Net-(" prefix.
        first_pass_name = get_default_net_name(pin, force_no_connect=False)
        assert first_pass_name == "Net-(R1-Pad1)"

        # The phase 9f fixup forces the flag unconditionally (`:2611`).
        first_pass_driver = Driver(
            kind=ItemKind.PIN, name=first_pass_name, priority=Priority.PIN, pin=pin
        )
        sub = Subgraph(drivers=[first_pass_driver])
        fixed_up_name = apply_unconnected_prefix_fixup(sub, pin)

        assert fixed_up_name == "unconnected-(R1-Pad1)"
        assert fixed_up_name != first_pass_name


class TestApplyUnconnectedPrefixFixup:
    """`connection_graph.cpp:2606-2614`, the gate plus the recompute."""

    def test_the_gate_is_needs_unconnected_prefix(self) -> None:
        """A strongly driven subgraph never gets the fixup, no matter how
        many drivers it has (`:2606`, `!subgraph->m_strong_driver`).
        """
        pin = _pin(ref="R1", num="1")
        driver = Driver(kind=ItemKind.PIN, name="Net-(R1-Pad1)", priority=Priority.PIN, pin=pin)
        sub = Subgraph(strong_driver=True, drivers=[driver])
        assert sub.needs_unconnected_prefix is False
        assert apply_unconnected_prefix_fixup(sub, pin) is None

    def test_multiple_drivers_never_gets_the_fixup(self) -> None:
        """`:2607`, `subgraph->m_drivers.size() == 1`."""
        pin = _pin(ref="R1", num="1")
        sub = Subgraph(
            drivers=[
                Driver(kind=ItemKind.PIN, name="Net-(R1-Pad1)", priority=Priority.PIN, pin=pin),
                Driver(kind=ItemKind.PIN, name="Net-(R2-Pad1)", priority=Priority.PIN, pin=pin),
            ]
        )
        assert sub.needs_unconnected_prefix is False
        assert apply_unconnected_prefix_fixup(sub, pin) is None

    def test_a_non_pin_sole_weak_driver_never_gets_the_fixup(self) -> None:
        """`:2608`, `subgraph->m_driver->Type() == SCH_PIN_T`."""
        sub = Subgraph(drivers=[Driver(kind=ItemKind.LABEL, name="FOO", priority=Priority.PIN)])
        assert sub.needs_unconnected_prefix is False

    def test_the_recompute_forces_no_connect_regardless_of_pin_type(self) -> None:
        """`:2611`, the literal `true` argument, independent of whatever
        `pin->GetType()` actually is.
        """
        pin = _pin(ref="R1", num="1", name="", electrical_type="passive")
        driver = Driver(kind=ItemKind.PIN, name="x", priority=Priority.PIN, pin=pin)
        sub = Subgraph(drivers=[driver])
        assert apply_unconnected_prefix_fixup(sub, pin) == "unconnected-(R1-Pad1)"

    def test_sibling_pins_still_feed_has_multiple_through_the_fixup(self) -> None:
        pin = _pin(ref="U9", num="3", name="CLK", unit_letter="A")
        sibling = _pin(
            ref="U9", num="4", name="CLK", unit_letter="A", electrical_type="no_connect"
        )
        driver = Driver(kind=ItemKind.PIN, name="x", priority=Priority.PIN, pin=pin)
        sub = Subgraph(drivers=[driver])
        name = apply_unconnected_prefix_fixup(sub, pin, sibling_pins=[sibling])
        assert name == "unconnected-(U9A-CLK-Pad3)"
