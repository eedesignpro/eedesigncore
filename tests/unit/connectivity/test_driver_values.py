"""Ported ``collectAllDriverValues``, tested against source line ranges.

Each test names the source line range it pins and fails if that behavior were
dropped. Source: ``eeschema/connection_graph.cpp:1657-1730`` at tag 10.0.3.
"""

from __future__ import annotations

import pytest

from eedesigncore.connectivity.driver_values import DriverValueMaps, collect_all_driver_values
from eedesigncore.connectivity.drivers import resolve, resolve_all
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph


def _driver(
    kind: ItemKind,
    name: str,
    priority: Priority,
    *,
    bus_members: frozenset[str] = frozenset(),
) -> Driver:
    return Driver(kind=kind, name=name, priority=priority, bus_members=bus_members)


def _resolved(
    driver: Driver,
    *,
    sheet_path: tuple[str, ...] = (),
    strong_override: bool | None = None,
) -> Subgraph:
    """A subgraph run through ``resolve()``, then optionally re-flagged.

    ``strong_override`` isolates a branch of ``collect_all_driver_values``
    that ``resolve()`` cannot reach on its own, matching a source branch the
    comments call defensive rather than normal.
    """
    sub = Subgraph(sheet_path=sheet_path, drivers=[driver])
    resolve(sub)
    if strong_override is not None:
        sub.strong_driver = strong_override
    return sub


class TestNetNameMap:
    """``m_net_name_to_subgraphs_map`` population (`:1663-1677`)."""

    def test_every_driver_subgraph_is_filed_under_its_name(self) -> None:
        sub = _resolved(_driver(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL))
        maps = collect_all_driver_values([sub])
        assert maps.m_net_name_to_subgraphs_map["SIG"] == [sub]

    def test_two_subgraphs_with_the_same_name_share_one_bucket(self) -> None:
        """The source comment (`:1659-1661`) states this is the whole point:

        two wires driven by same-named but distinct hierarchical sheet pins
        must land in one bucket so a later phase can detect the collision.
        """
        first = _resolved(_driver(ItemKind.HIER_LABEL, "SIG", Priority.HIER_LABEL))
        second = _resolved(_driver(ItemKind.HIER_LABEL, "SIG", Priority.HIER_LABEL))
        maps = collect_all_driver_values([first, second])
        assert maps.m_net_name_to_subgraphs_map["SIG"] == [first, second]

    def test_a_bus_driver_is_also_filed_under_its_prefix(self) -> None:
        """`:1673-1677`. The prefix key lets differently ranged vector buses
        sharing a prefix still collide on lookup.
        """
        sub = _resolved(
            _driver(ItemKind.LABEL, "D[7..0]", Priority.LOCAL_LABEL, bus_members=frozenset("ab"))
        )
        maps = collect_all_driver_values([sub])
        assert maps.m_net_name_to_subgraphs_map["D[7..0]"] == [sub]
        assert maps.m_net_name_to_subgraphs_map["D[]"] == [sub]

    def test_a_non_bus_driver_is_not_filed_under_a_prefix(self) -> None:
        sub = _resolved(_driver(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL))
        maps = collect_all_driver_values([sub])
        assert len(maps.m_net_name_to_subgraphs_map) == 1

    def test_the_bus_prefix_drops_everything_before_the_bracket(self) -> None:
        """`:1675`, ``BeforeFirst('[') + "[]"``."""
        sub = _resolved(
            _driver(
                ItemKind.LABEL, "ADDR[15..0]", Priority.LOCAL_LABEL, bus_members=frozenset("a")
            )
        )
        maps = collect_all_driver_values([sub])
        assert "ADDR[]" in maps.m_net_name_to_subgraphs_map


class TestDirtyRearm:
    """`:1679`. Re-arms every subgraph for the propagation phase."""

    def test_every_subgraph_is_marked_dirty_after_this_phase(self) -> None:
        sub = _resolved(_driver(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL))
        sub.dirty = False
        collect_all_driver_values([sub])
        assert sub.dirty is True


class TestLocalLabelCache:
    """`:1688-1691`, `:1706-1708`. Sheet-scoped names."""

    def test_a_local_label_driver_is_cached_by_sheet_and_name(self) -> None:
        sub = _resolved(
            _driver(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL), sheet_path=("root", "child")
        )
        maps = collect_all_driver_values([sub])
        assert maps.m_local_label_cache[(("root", "child"), "SIG")] == [sub]

    def test_a_hierarchical_label_driver_shares_the_local_cache(self) -> None:
        """Source uses one ``case`` fallthrough for both label types (`:1688-1691`)."""
        sub = _resolved(
            _driver(ItemKind.HIER_LABEL, "SIG", Priority.HIER_LABEL), sheet_path=("root",)
        )
        maps = collect_all_driver_values([sub])
        assert maps.m_local_label_cache[(("root",), "SIG")] == [sub]

    def test_a_local_power_pin_driver_is_cached_by_sheet_and_name(self) -> None:
        sub = _resolved(
            _driver(ItemKind.PIN, "GND", Priority.LOCAL_POWER_PIN), sheet_path=("root",)
        )
        maps = collect_all_driver_values([sub])
        assert maps.m_local_label_cache[(("root",), "GND")] == [sub]


class TestGlobalLabelCache:
    """`:1694-1696`, `:1701-1704`. Names that are not sheet-scoped."""

    def test_a_global_label_driver_is_cached_by_name_alone(self) -> None:
        sub = _resolved(_driver(ItemKind.GLOBAL_LABEL, "VCC", Priority.GLOBAL))
        maps = collect_all_driver_values([sub])
        assert maps.m_global_label_cache["VCC"] == [sub]

    def test_a_global_power_pin_driver_is_cached_by_name_alone(self) -> None:
        sub = _resolved(_driver(ItemKind.PIN, "+5V", Priority.GLOBAL_POWER_PIN))
        maps = collect_all_driver_values([sub])
        assert maps.m_global_label_cache["+5V"] == [sub]


class TestWeakDriversSkipLabelCaches:
    """`:1681`. The label caches only fire for ``m_strong_driver``."""

    def test_a_weak_driver_never_enters_either_label_cache(self) -> None:
        sub = _resolved(_driver(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN))
        assert not sub.strong_driver
        maps = collect_all_driver_values([sub])
        assert maps.m_global_label_cache == {}
        assert maps.m_local_label_cache == {}
        # It still gets the net-name-map entry from the unconditional part of
        # the loop (`:1667`).
        assert maps.m_net_name_to_subgraphs_map


class TestUnexpectedDriverKindsAreDefensive:
    """Branches source logs and does nothing else with (`:1710-1715`, `:1719-1726`).

    Neither branch mutates a cache. A port that added one would disagree with
    source, which only calls ``wxLogTrace`` here.
    """

    def test_a_sheet_pin_driver_is_weak_and_is_not_cached(self) -> None:
        """A sheet pin sits at rank 2, BELOW the strong threshold (`:179-180`).

        So it never reaches the label caches, which fire only for a strong driver
        (`:1681`). Source's switch also has no ``SCH_SHEET_PIN_T`` case, so even a
        sheet pin promoted to strong in phase 8 falls to the default "unexpected
        strong driver" branch and only logs (`:1719-1726`).

        An earlier version of this test asserted the OPPOSITE, that a sheet pin
        is strong. That came from a wrong threshold in the model, and it is the
        defect that merged five sibling sheet instances into one net.
        """
        sub = _resolved(_driver(ItemKind.SHEET_PIN, "SIG", Priority.SHEET_PIN))
        assert not sub.strong_driver
        maps = collect_all_driver_values([sub])
        assert maps.m_global_label_cache == {}
        assert maps.m_local_label_cache == {}

    def test_a_strong_plain_pin_driver_does_not_crash_or_cache(self) -> None:
        """Unreachable through ``resolve()`` (a plain pin is never strong), so
        this isolates the branch directly, matching source's own claim that a
        plain pin here is unexpected (`:1710-1715`).
        """
        sub = _resolved(_driver(ItemKind.PIN, "Net-(R1-Pad1)", Priority.PIN), strong_override=True)
        maps = collect_all_driver_values([sub])
        assert maps.m_global_label_cache == {}
        assert maps.m_local_label_cache == {}


class TestPrecondition:
    """Every input subgraph must already carry a driver (`:1649-1653`, `resolve_all`)."""

    def test_a_subgraph_without_a_driver_is_rejected(self) -> None:
        undriven = Subgraph()
        with pytest.raises(AssertionError):
            collect_all_driver_values([undriven])


class TestEndToEndWithResolveAll:
    """Phase 4 then phase 5, matching the call order in ``buildConnectionGraph``
    (`:2282-2284`).
    """

    def test_global_label_and_local_label_land_in_different_caches(self) -> None:
        global_sub = Subgraph(
            sheet_path=("root",), drivers=[_driver(ItemKind.GLOBAL_LABEL, "VCC", Priority.GLOBAL)]
        )
        local_sub = Subgraph(
            sheet_path=("root",), drivers=[_driver(ItemKind.LABEL, "SIG", Priority.LOCAL_LABEL)]
        )
        driven = resolve_all([global_sub, local_sub])
        maps = collect_all_driver_values(driven)
        assert maps.m_global_label_cache == {"VCC": [global_sub]}
        assert maps.m_local_label_cache == {(("root",), "SIG"): [local_sub]}


def test_returns_a_fresh_maps_object_each_call() -> None:
    """No leftover state between calls, since ``DriverValueMaps`` fields use
    ``default_factory`` rather than a shared mutable default.
    """
    first = collect_all_driver_values([])
    second = collect_all_driver_values([])
    assert first is not second
    assert first.m_net_name_to_subgraphs_map is not second.m_net_name_to_subgraphs_map
    assert isinstance(first, DriverValueMaps)
