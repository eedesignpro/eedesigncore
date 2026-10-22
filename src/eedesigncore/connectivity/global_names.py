"""The global-name equivalence closure, phase 9b.

Source: the inline pre-pass at ``eeschema/connection_graph.cpp:2327-2452``, tag
10.0.3. Mapped by audit ``connection-graph-algorithm-2026-09-02.md`` section 9b.

**Why this phase exists, in KiCad's own words.** The per-subgraph promotion pass
that runs straight after it is order-dependent, and it keys off each subgraph's
ORIGINAL driver text. So a chain of differently named global labels across sheets
could leave a middle subgraph stranded with an intermediate name. This pre-pass
makes the whole chain converge before that order-dependent pass runs. The source
comment cites upstream issue 23719 (`:2327-2343`).

A port that merges nets by identical name handles only the trivial case. It
cannot close a three-hop chain, which is exactly the bug this fixes.

Two details a port gets wrong if it skips the source.

**The class representative is chosen, not arbitrary.** Higher driver rank wins,
and a tie breaks on the alphabetically lower current name (`:2371-2387`). That
mirrors the driver tie-break, so the closure and the resolution agree.

**Only a driver at rank GLOBAL_POWER_PIN or above indexes a name** (`:2409-2418`).
A local label never enters this closure, which is what keeps a local name local.
"""

from __future__ import annotations

from eedesigncore.connectivity.subgraph import Priority, Subgraph


class _UnionFind:
    """Union-find over subgraphs, with the representative rule from source.

    KiCad compresses one grandparent hop per lookup rather than the whole path
    (`connection_graph.cpp:2348-2369`). The end result is identical, so this uses
    full compression, which is simpler and cannot diverge.
    """

    def __init__(self) -> None:
        self._root: dict[int, Subgraph] = {}
        self._by_id: dict[int, Subgraph] = {}

    def _add(self, item: Subgraph) -> None:
        key = id(item)
        self._by_id.setdefault(key, item)
        self._root.setdefault(key, item)

    def find(self, item: Subgraph) -> Subgraph:
        self._add(item)
        seen: list[int] = []
        current = item
        while True:
            parent = self._root[id(current)]
            if parent is current:
                break
            seen.append(id(current))
            current = parent
        for key in seen:
            self._root[key] = current
        return current

    def union(self, a: Subgraph, b: Subgraph) -> None:
        """Join two classes, keeping the preferred representative as root."""
        self._add(a)
        self._add(b)
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a is root_b:
            return
        if _prefer_as_representative(root_a, root_b):
            self._root[id(root_b)] = root_a
        else:
            self._root[id(root_a)] = root_b

    def members(self) -> list[Subgraph]:
        return list(self._by_id.values())


def _prefer_as_representative(a: Subgraph, b: Subgraph) -> bool:
    """True when *a* should represent the class, per ``:2375-2387``.

    Higher driver rank wins. A tie breaks on the alphabetically lower current
    name, which matches the driver tie-break so the two phases agree.
    """
    rank_a = a.driver.priority if a.driver else Priority.NONE
    rank_b = b.driver.priority if b.driver else Priority.NONE
    if rank_a != rank_b:
        return rank_a > rank_b
    return a.name < b.name


def close_global_names(global_subgraphs: list[Subgraph]) -> int:
    """Canonicalize every global subgraph onto its class representative.

    Returns the number of subgraphs renamed, which lets a caller log the work and
    lets a test assert that the closure actually fired.

    Ports ``:2344-2452`` in four steps, in source order.
    """
    if not global_subgraphs:
        return 0

    union = _UnionFind()

    # Step 1. Index each subgraph under every global-or-above driver name
    # (`:2407-2421`). A rank below GLOBAL_POWER_PIN is skipped, so a local label
    # never joins a global class.
    name_to_subgraphs: dict[str, list[Subgraph]] = {}
    for subgraph in global_subgraphs:
        for driver in subgraph.drivers:
            if driver.priority < Priority.GLOBAL_POWER_PIN:
                continue
            name_to_subgraphs.setdefault(driver.name, []).append(subgraph)

    # Step 2. Union every subgraph sharing a name onto the first (`:2423-2430`).
    for shared in name_to_subgraphs.values():
        if len(shared) < 2:
            continue
        for other in shared[1:]:
            union.union(shared[0], other)

    # Step 3. Clone the representative's name into every member that differs
    # (`:2432-2451`).
    renamed = 0
    for subgraph in union.members():
        root = union.find(subgraph)
        if subgraph is root or subgraph.name == root.name:
            continue
        # Source calls Clone here (`:2441`), not a name assignment, so the
        # connection's sheet and bus state travel with the name. A global name
        # is not sheet-qualified, so the two name forms agree; the sheet still
        # has to move, because a later bus member built from this connection
        # takes its path from it.
        subgraph.clone_conn_from(root.as_conn())
        renamed += 1
    return renamed
