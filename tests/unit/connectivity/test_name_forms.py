"""The two net-name forms and the sheet-path rule.

Source: ``SCH_CONNECTION::Name(bool)`` (`sch_connection.cpp:393-397`) and
``recacheName`` (`:417-452`).

KiCad caches two forms of every net name and reads both. One is never
sheet-qualified. The other is qualified when the driver kind calls for it.
``collectAllDriverValues`` keys its net-name map on the qualified form and its
label caches on the bare one (`connection_graph.cpp:1665-1667`), so an engine
carrying one field cannot distinguish a same-name collision across two sheets.
"""

from __future__ import annotations

import pytest

from eedesigncore.connectivity.subgraph import (
    Driver,
    ItemKind,
    Priority,
    Subgraph,
    prepend_path,
    qualified_name,
)


def _driver(kind: ItemKind, priority: Priority) -> Driver:
    return Driver(kind=kind, name="SIG", priority=priority)


class TestPrependPath:
    """The switch at `sch_connection.cpp:417-445`, case by case."""

    def test_a_global_label_carries_no_path(self) -> None:
        """A global name is not scoped, so it never takes a prefix."""
        assert not prepend_path(_driver(ItemKind.GLOBAL_LABEL, Priority.GLOBAL))

    def test_an_ordinary_pin_carries_no_path(self) -> None:
        """This is why an implicit name is a bare Net-(R1-Pad1) at any depth."""
        assert not prepend_path(_driver(ItemKind.PIN, Priority.PIN))

    def test_a_global_power_pin_carries_no_path(self) -> None:
        assert not prepend_path(_driver(ItemKind.PIN, Priority.GLOBAL_POWER_PIN))

    def test_a_local_power_pin_DOES_carry_a_path(self) -> None:
        """The case a naive reading gets backwards.

        The source comment says it outright: a normal pin and a global power pin
        need no path, but a LOCAL power pin does.
        """
        assert prepend_path(_driver(ItemKind.PIN, Priority.LOCAL_POWER_PIN))

    @pytest.mark.parametrize(
        ("kind", "priority"),
        [
            (ItemKind.LABEL, Priority.LOCAL_LABEL),
            (ItemKind.HIER_LABEL, Priority.HIER_LABEL),
            (ItemKind.SHEET_PIN, Priority.SHEET_PIN),
        ],
    )
    def test_a_sheet_scoped_driver_carries_a_path(
        self, kind: ItemKind, priority: Priority
    ) -> None:
        """These fall to the switch default, which prepends."""
        assert prepend_path(_driver(kind, priority))

    def test_no_driver_means_no_path(self) -> None:
        assert not prepend_path(None)


class TestQualifiedName:
    """`:449-452`. The prefix is applied only when the rule says so."""

    def test_a_local_label_takes_the_prefix(self) -> None:
        driver = _driver(ItemKind.LABEL, Priority.LOCAL_LABEL)
        assert qualified_name(driver, "SIG", "/POWER/") == "/POWER/SIG"

    def test_a_global_label_keeps_its_bare_name(self) -> None:
        driver = _driver(ItemKind.GLOBAL_LABEL, Priority.GLOBAL)
        assert qualified_name(driver, "VBUS", "/POWER/") == "VBUS"

    def test_an_implicit_pin_name_keeps_its_bare_name_at_depth(self) -> None:
        driver = _driver(ItemKind.PIN, Priority.PIN)
        got = qualified_name(driver, "Net-(R1-Pad1)", "/A/B/C/")
        assert got == "Net-(R1-Pad1)"


class TestBothFormsExist:
    """A subgraph carries both, because the pipeline reads both."""

    def test_the_two_fields_are_separate(self) -> None:
        sub = Subgraph(name="SIG", full_name="/POWER/SIG")
        assert sub.name == "SIG"
        assert sub.full_name == "/POWER/SIG"

    def test_the_bare_name_is_what_a_label_cache_keys_on(self) -> None:
        """Two sheets can hold the same bare label name and stay distinct.

        The net-name map keys on the qualified form, so these are two nets. An
        engine with one name field would merge them.
        """
        a = Subgraph(sheet_path=("A",), name="SIG", full_name="/A/SIG")
        b = Subgraph(sheet_path=("B",), name="SIG", full_name="/B/SIG")
        assert a.name == b.name
        assert a.full_name != b.full_name
