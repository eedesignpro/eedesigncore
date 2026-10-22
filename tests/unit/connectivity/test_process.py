"""Subgraph processing, phase 8. Merging, suffixing, and what must NOT merge."""

from __future__ import annotations

import pytest

from eedesigncore.connectivity.geometry import PinRef
from eedesigncore.connectivity.process import _absorb, create_new_name, process
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph


def _pin(ref: str, num: str = "1") -> PinRef:
    return PinRef(
        reference=ref, pin_number=num, point=(0, 0), electrical_type="passive", lib_id="L:S"
    )


def _sg(
    name: str,
    *,
    strong: bool = True,
    path: tuple[str, ...] = (),
    kind: ItemKind = ItemKind.LABEL,
    priority: Priority = Priority.LOCAL_LABEL,
    pins: list[PinRef] | None = None,
) -> Subgraph:
    driver = Driver(kind=kind, name=name, priority=priority)
    sub = Subgraph(sheet_path=path, name=name, drivers=[driver], pins=pins or [])
    sub.driver = driver
    sub.strong_driver = strong
    # Phase 4 sets both name forms and the connection sheet together, so a helper
    # standing in for it must too: the collision check reads the QUALIFIED form.
    sub.conn_sheet = path
    sub.conn_sheet_prefix = "/" + "/".join(path) + "/" if path else "/"
    sub.conn_prepend = True
    sub.full_name = f"{sub.conn_sheet_prefix}{name}"
    return sub


class TestSameSheetMerging:
    """`:2072-2219`, and `Absorb` at `:502-536`."""

    def test_two_strong_subgraphs_with_one_name_merge(self) -> None:
        a = _sg("SIG", pins=[_pin("R1")])
        b = _sg("SIG", pins=[_pin("R2")])
        kept = process([a, b])
        assert len(kept) == 1
        assert kept[0].members == frozenset({("R1", "1"), ("R2", "1")})

    def test_different_names_never_merge(self) -> None:
        a = _sg("SIG", pins=[_pin("R1")])
        b = _sg("OTHER", pins=[_pin("R2")])
        assert len(process([a, b])) == 2

    def test_a_cross_sheet_pair_never_merges(self) -> None:
        """Absorption is same-sheet only. Crossing sheets is propagation."""
        a = _sg("SIG", path=("A",), pins=[_pin("R1")])
        b = _sg("SIG", path=("B",), pins=[_pin("R2")])
        assert len(process([a, b])) == 2

    def test_absorb_refuses_a_cross_sheet_merge_outright(self) -> None:
        """KiCad asserts one sheet at `:504`. This port raises rather than merging."""
        a = _sg("SIG", path=("A",))
        b = _sg("SIG", path=("B",))
        with pytest.raises(ValueError, match="one sheet"):
            _absorb(a, b)

    def test_a_weak_driver_cannot_merge(self) -> None:
        """`:2065-2066`. Only a strong driver reaches the merge step."""
        a = _sg(
            "Net-(R1-Pad1)",
            strong=False,
            kind=ItemKind.PIN,
            priority=Priority.PIN,
            pins=[_pin("R1")],
        )
        b = _sg(
            "Net-(R1-Pad1)",
            strong=False,
            kind=ItemKind.PIN,
            priority=Priority.PIN,
            pins=[_pin("R2")],
        )
        kept = process([a, b])
        assert len(kept) == 2

    def test_the_absorbed_subgraph_is_emptied_and_marked(self) -> None:
        a = _sg("SIG", pins=[_pin("R1")])
        b = _sg("SIG", pins=[_pin("R2")])
        process([a, b])
        absorbed = b if b.absorbed else a
        survivor = a if b.absorbed else b
        assert absorbed.absorbed
        assert absorbed.absorbed_by is survivor
        assert absorbed.pins == []
        assert absorbed.driver is None
        assert not absorbed.dirty


class TestWeakNameCollision:
    """`:1912-1983`, the suffix path."""

    def test_a_colliding_weak_name_takes_a_suffix(self) -> None:
        a = _sg(
            "Net-(R1-Pad1)",
            strong=False,
            kind=ItemKind.PIN,
            priority=Priority.PIN,
            pins=[_pin("R1")],
        )
        b = _sg(
            "Net-(R1-Pad1)",
            strong=False,
            kind=ItemKind.PIN,
            priority=Priority.PIN,
            pins=[_pin("R9")],
        )
        process([a, b])
        assert {a.name, b.name} == {"Net-(R1-Pad1)", "Net-(R1-Pad1)_1"}

    def test_a_strong_name_collision_is_left_alone(self) -> None:
        """Two labels sharing a name are the user's meaning, so they merge."""
        a = _sg("SIG", pins=[_pin("R1")])
        b = _sg("SIG", pins=[_pin("R2")])
        process([a, b])
        assert a.name == "SIG"

    def test_the_suffix_skips_a_name_already_taken(self) -> None:
        assert create_new_name("X", {"X", "X_1"}) == "X_2"
        assert create_new_name("X", {"X"}) == "X_1"


class TestSheetPinPromotion:
    """`:1994-2026`. A sheet-pin driver becomes strong so it can merge."""

    def test_a_lone_sheet_pin_driver_is_promoted(self) -> None:
        sub = _sg("SIG", strong=False, kind=ItemKind.SHEET_PIN, priority=Priority.SHEET_PIN)
        process([sub])
        assert sub.strong_driver

    def test_promotion_is_blocked_by_a_same_named_global_label(self) -> None:
        """The source checks for that collision before promoting."""
        pin_sub = _sg("SIG", strong=False, kind=ItemKind.SHEET_PIN, priority=Priority.SHEET_PIN)
        label = _sg("SIG", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL)
        process([pin_sub, label])
        assert not pin_sub.strong_driver


class TestReArmedForPropagation:
    """`:2057`. Every processed subgraph is dirty again when this phase ends."""

    def test_a_surviving_subgraph_is_dirty(self) -> None:
        sub = _sg("SIG", pins=[_pin("R1")])
        kept = process([sub])
        assert kept[0].dirty


class TestNoCodesAreModelled:
    """The audit rules a net code out. Nothing here may grow one."""

    def test_a_subgraph_carries_no_code_attribute(self) -> None:
        sub = _sg("SIG")
        assert not hasattr(sub, "code")
        assert not hasattr(sub, "net_code")


class TestBusFieldsStayEmpty:
    """Bus linking is not ported, so the fields hold nothing rather than a guess."""

    def test_processing_leaves_the_bus_links_empty(self) -> None:
        sub = _sg("SIG", pins=[_pin("R1")])
        process([sub])
        assert sub.bus_neighbors == []
        assert sub.bus_parents == []


class TestSheetPinsAreWeakAndDoNotMerge:
    """The strong threshold, and what phase 8 does with a weak sheet pin.

    A sheet pin is rank 2, BELOW ``PRIORITY::HIER_LABEL``, so a subgraph driven
    by one is WEAK (`connection_graph.cpp:179-180`). Phase 8 then either suffixes
    its name, when the name collides, or promotes it to strong when it does not
    (`:1949-2026`).

    Both branches matter. A port that treats a sheet pin as strong skips the
    collision check entirely, and five instances of one sheet sharing a pin name
    merge into a single net instead of staying five.
    """

    def test_five_sibling_sheet_pins_with_one_name_stay_five_nets(self) -> None:
        siblings = [
            _sg(
                "+1V8_IN",
                strong=False,
                path=("PARENT",),
                kind=ItemKind.SHEET_PIN,
                priority=Priority.SHEET_PIN,
                pins=[_pin(f"R{index}")],
            )
            for index in range(5)
        ]
        kept = process(siblings)
        assert len(kept) == 5
        # Each took its own suffixed name, so nothing can merge them later.
        assert len({sub.full_name for sub in kept}) == 5

    def test_a_unique_sheet_pin_is_promoted_to_strong_instead(self) -> None:
        """No collision, so the promotion branch runs (`:1994-2026`)."""
        lone = _sg(
            "SIG",
            strong=False,
            path=("PARENT",),
            kind=ItemKind.SHEET_PIN,
            priority=Priority.SHEET_PIN,
            pins=[_pin("R1")],
        )
        process([lone])
        assert lone.strong_driver
        assert lone.full_name == "/PARENT/SIG"

    def test_promotion_is_skipped_when_a_global_label_shares_the_name(self) -> None:
        """A same-sheet global would then merge it by implicit linking (`:2000-2016`)."""
        pin = _sg(
            "SIG",
            strong=False,
            path=("PARENT",),
            kind=ItemKind.SHEET_PIN,
            priority=Priority.SHEET_PIN,
        )
        glob = _sg("SIG", path=("PARENT",), kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL)
        process([pin, glob])
        assert not pin.strong_driver
