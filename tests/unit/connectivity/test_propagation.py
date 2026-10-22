"""The global-name closure and hierarchical propagation, phases 9b and 10.

Each test names the source line range it pins. Several are written so they would
PASS under the naive approximation an earlier attempt used and FAIL only under a
correct port, or the reverse. Those are marked, because they are the tests that
prove the port is a port.
"""

from __future__ import annotations

from eedesigncore.connectivity.global_names import close_global_names
from eedesigncore.connectivity.net_index import NetNameIndex
from eedesigncore.connectivity.propagate import build_sheet_map, propagate, propagate_all
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph


def _driven(
    name: str,
    *,
    kind: ItemKind = ItemKind.LABEL,
    priority: Priority = Priority.LOCAL_LABEL,
    path: tuple[str, ...] = (),
    ports: list[str] | None = None,
    pins: list[tuple[str, str]] | None = None,
    extra_names: list[str] | None = None,
) -> Subgraph:
    """A subgraph with its driver already resolved, as phase 4 would leave it."""
    drivers = [Driver(kind=kind, name=name, priority=priority)]
    for other in extra_names or []:
        drivers.append(Driver(kind=kind, name=other, priority=priority))
    sub = Subgraph(
        sheet_path=path,
        name=name,
        drivers=drivers,
        hier_ports=ports or [],
        hier_pin_children=pins or [],
    )
    sub.driver = drivers[0]
    # The source threshold, rank HIER_LABEL or above (`connection_graph.cpp:179`).
    sub.strong_driver = priority >= Priority.HIER_LABEL
    sub.multiple_drivers = len(drivers) > 1
    # Phase 3 marks a subgraph dirty at its creation site (`:1575`). The model
    # default matches the CONSTRUCTOR, which is false, so a helper standing in
    # for phase 3 sets it here.
    sub.dirty = True
    return sub


def _index(*subgraphs: Subgraph) -> NetNameIndex:
    """The name index propagation recaches as it renames.

    Phase 9e is its only reader, so a test that does not reach 9e can pass any
    index. It is required rather than optional so a phase cannot silently skip
    recaching and leave the index disagreeing with the subgraphs.
    """
    return NetNameIndex(list(subgraphs))


class TestGlobalNameClosure:
    """Phase 9b (`connection_graph.cpp:2327-2452`), upstream issue 23719."""

    def test_two_subgraphs_sharing_a_global_name_converge(self) -> None:
        a = _driven("VBUS", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL)
        b = _driven("VBUS", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL)
        close_global_names([a, b])
        assert a.name == b.name

    def test_a_three_hop_chain_converges(self) -> None:
        """The case merge-by-name CANNOT close, and the reason this phase exists.

        A drives {A, B}. B drives {B, C}. C drives {C}. All three are one net, but
        no two of them share their PRIMARY name. A port that merges nets by equal
        name leaves three nets.
        """
        s1 = _driven("A", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL, extra_names=["B"])
        s2 = _driven("B", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL, extra_names=["C"])
        s3 = _driven("C", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL)
        close_global_names([s1, s2, s3])
        assert len({s1.name, s2.name, s3.name}) == 1

    def test_the_representative_is_the_higher_rank_driver(self) -> None:
        """`:2375-2387`. Rank first, then the alphabetically lower name."""
        weak = _driven(
            "AAA", kind=ItemKind.PIN, priority=Priority.GLOBAL_POWER_PIN, extra_names=["SHARED"]
        )
        strong = _driven(
            "ZZZ", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL, extra_names=["SHARED"]
        )
        close_global_names([weak, strong])
        # The global label outranks the power pin, so its name wins despite
        # sorting later alphabetically.
        assert weak.name == "ZZZ"

    def test_a_tie_breaks_on_the_lower_name(self) -> None:
        a = _driven(
            "ZZZ", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL, extra_names=["SHARED"]
        )
        b = _driven(
            "AAA", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL, extra_names=["SHARED"]
        )
        close_global_names([a, b])
        assert a.name == "AAA"

    def test_a_local_label_never_joins_a_global_class(self) -> None:
        """`:2409-2418`. Only a driver at GLOBAL_POWER_PIN or above indexes."""
        local = _driven("SIG", priority=Priority.LOCAL_LABEL)
        other = _driven("SIG", priority=Priority.LOCAL_LABEL)
        renamed = close_global_names([local, other])
        assert renamed == 0

    def test_the_closure_is_order_independent(self) -> None:
        def build() -> list[Subgraph]:
            return [
                _driven(
                    "A", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL, extra_names=["B"]
                ),
                _driven(
                    "B", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL, extra_names=["C"]
                ),
                _driven("C", kind=ItemKind.GLOBAL_LABEL, priority=Priority.GLOBAL),
            ]

        forward = build()
        close_global_names(forward)
        backward = build()
        close_global_names(list(reversed(backward)))
        assert {s.name for s in forward} == {s.name for s in backward}


class TestHierarchicalPropagation:
    """Phase 10 (`connection_graph.cpp:2857-3240`)."""

    def test_a_parent_sheet_pin_joins_its_child_label(self) -> None:
        parent = _driven("/SIG", path=(), pins=[("SIG", "A")])
        child = _driven("/A/SIG", path=("A",), ports=["SIG"])
        assert propagate_all([parent, child], _index(parent, child)) >= 1
        assert parent.name == child.name

    def test_a_three_level_chain_converges_in_one_visited_set(self) -> None:
        """`:3133-3141`. Breadth-first across the WHOLE chain, not one level."""
        root = _driven("/SIG", path=(), pins=[("SIG", "A")])
        mid = _driven("/A/SIG", path=("A",), ports=["SIG"], pins=[("SIG", "B")])
        leaf = _driven("/A/B/SIG", path=("A", "B"), ports=["SIG"])
        propagate_all([root, mid, leaf], _index(root, mid, leaf))
        assert len({root.name, mid.name, leaf.name}) == 1

    def test_the_shallowest_sheet_wins_the_tie(self) -> None:
        """`:3165-3184` rule d. Equal rank and strength, shorter path wins."""
        root = _driven("/SIG", path=(), pins=[("SIG", "A")])
        child = _driven("/A/SIG", path=("A",), ports=["SIG"])
        propagate_all([root, child], _index(root, child))
        assert root.name == "/SIG"

    def test_a_global_power_driver_wins_regardless_of_depth(self) -> None:
        """`:3150`. A global power driver already wins by definition."""
        root = _driven("/SIG", path=(), pins=[("SIG", "A")])
        child = _driven(
            "+5V",
            kind=ItemKind.PIN,
            priority=Priority.GLOBAL_POWER_PIN,
            path=("A",),
            ports=["SIG"],
        )
        propagate_all([root, child], _index(root, child))
        assert root.name == "+5V"

    def test_a_subgraph_facing_both_ways_is_deferred_then_swept(self) -> None:
        """`:3112-3124` and `:2514-2522`, the most important deferral point.

        A subgraph holding both hierarchical labels and sheet pins returns without
        processing on its natural turn. Only the forced sweep collects it. This
        test fails if either the guard or the sweep is dropped.
        """
        mid = _driven("/A/SIG", path=("A",), ports=["SIG"], pins=[("SIG", "B")])
        sheets = build_sheet_map([mid])
        assert propagate(mid, sheets, _index(mid), force=False) == set()
        assert mid.dirty

        # The forced sweep processes it.
        propagate(mid, sheets, _index(mid), force=True)
        assert not mid.dirty

    def test_a_subgraph_with_no_hierarchy_is_marked_clean_and_left_alone(self) -> None:
        flat = _driven("/SIG", path=())
        propagate_all([flat], _index(flat))
        assert flat.name == "/SIG"
        assert not flat.dirty

    def test_a_name_only_crosses_where_the_names_match(self) -> None:
        """The match is by name. A different label name does not join."""
        parent = _driven("/SIG", path=(), pins=[("SIG", "A")])
        unrelated = _driven("/A/OTHER", path=("A",), ports=["OTHER"])
        propagate_all([parent, unrelated], _index(parent, unrelated))
        assert parent.name != unrelated.name

    def test_the_result_does_not_depend_on_traversal_order(self) -> None:
        """`:3143-3185` is a strict total order, so order cannot change the winner."""

        def build() -> list[Subgraph]:
            return [
                _driven("/SIG", path=(), pins=[("SIG", "A")]),
                _driven("/A/SIG", path=("A",), ports=["SIG"], pins=[("SIG", "B")]),
                _driven("/A/B/SIG", path=("A", "B"), ports=["SIG"]),
            ]

        forward = build()
        propagate_all(forward, _index(*forward))
        backward = build()
        propagate_all(list(reversed(backward)), _index(*backward))
        assert {s.name for s in forward} == {s.name for s in backward}

    def test_hierarchy_links_are_set_only_by_this_phase(self) -> None:
        parent = _driven("/SIG", path=(), pins=[("SIG", "A")])
        child = _driven("/A/SIG", path=("A",), ports=["SIG"])
        assert parent.hier_children == []
        assert child.hier_parent is None
        propagate_all([parent, child], _index(parent, child))
        assert child in parent.hier_children
        assert child.hier_parent is parent
