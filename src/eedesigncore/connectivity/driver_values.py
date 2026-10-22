"""Collect driver values into the graph-level name caches.

Ports ``CONNECTION_GRAPH::collectAllDriverValues``
(`eeschema/connection_graph.cpp:1657-1730`), phase 5 of the top-level call
sequence mapped by audit ``connection-graph-algorithm-2026-09-02.md``,
section "Phase 5 -- collectAllDriverValues" (audit lines 469-515). Called
third from ``buildConnectionGraph``, right after ``resolveAllDrivers``
(audit lines 471-472, `connection_graph.cpp:2284`).

The phase walks ``m_driver_subgraphs``, the subgraphs ``resolveAllDrivers``
kept because they carry a driver (`connection_graph.cpp:1649-1653`, ported by
``drivers.resolve_all``). For each one it files the subgraph under its
driver connection's name in ``m_net_name_to_subgraphs_map``, re-arms the
subgraph as dirty for the propagation phase that runs later, and, for a
strong driver only, files the subgraph into one of the two label caches by
driver kind.

Known gap, left unimplemented rather than guessed. Source reads
``full_name = Name()`` and ``name = Name(true)`` from ``m_driver_connection``
(`connection_graph.cpp:1665-1666`). ``Name()`` prepends the sheet path for
most driver kinds; ``Name(true)`` never does (`sch_connection.cpp:393-397`,
the prepend decision in ``recacheName`` at `sch_connection.cpp:416-452`). The
ported ``Subgraph`` model (`connectivity/subgraph.py`, out of this module's
write scope) carries one name field, ``name``, set by ``drivers.resolve()``
to the winning driver's raw text -- the ``Name(true)`` form. It carries no
sheet-path-qualified form yet, so this port reads ``subgraph.name`` for both
``full_name`` and ``name``. A later phase that adds the qualified form must
revisit the ``m_net_name_to_subgraphs_map`` key here: a collision test that
never sees the path can under-detect a same-name-different-sheet collision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eedesigncore.connectivity.subgraph import ItemKind, Priority, Subgraph


@dataclass
class DriverValueMaps:
    """The three ``CONNECTION_GRAPH``-level maps this phase fills.

    Field names match the C++ members that own them, per
    ``connection_graph.h:833-838``, so a reader can cross-reference directly.
    """

    # connection_graph.h:838. Every driver subgraph, keyed by its driver
    # connection's name. A bus driver also gets a prefix-only key (:1673-1677).
    m_net_name_to_subgraphs_map: dict[str, list[Subgraph]] = field(default_factory=dict)
    # connection_graph.h:833. Strong global labels and global power pins, keyed
    # by name alone, since a global name is not sheet-scoped.
    m_global_label_cache: dict[str, list[Subgraph]] = field(default_factory=dict)
    # connection_graph.h:835-836. Strong local and hierarchical labels, and
    # local power pins, keyed by (sheet, name), since these names are
    # sheet-scoped.
    m_local_label_cache: dict[tuple[tuple[str, ...], str], list[Subgraph]] = field(
        default_factory=dict
    )


def collect_all_driver_values(driver_subgraphs: list[Subgraph]) -> DriverValueMaps:
    """Port of ``collectAllDriverValues`` (`connection_graph.cpp:1657-1730`).

    Takes the subgraphs ``resolve_all`` returns. Every one of them already
    carries a driver, because ``resolve_all`` filters on it before returning
    (`connection_graph.cpp:1649-1653`).
    """
    maps = DriverValueMaps()

    for subgraph in driver_subgraphs:
        driver = subgraph.driver
        assert driver is not None, "collect_all_driver_values requires a resolved driver"

        # full_name = Name(), name = Name(true) (:1665-1666). See the module
        # docstring for the gap this collapses.
        full_name = subgraph.name
        name = subgraph.name

        # m_net_name_to_subgraphs_map[full_name].emplace_back(subgraph) (:1667).
        maps.m_net_name_to_subgraphs_map.setdefault(full_name, []).append(subgraph)

        # A bus driver also files under its prefix, so two differently ranged
        # vector buses sharing a prefix still collide on lookup (:1673-1677).
        if driver.is_bus:
            prefix_only = full_name.split("[", 1)[0] + "[]"
            maps.m_net_name_to_subgraphs_map.setdefault(prefix_only, []).append(subgraph)

        # Re-arms this subgraph for the propagation phase that runs later in
        # buildConnectionGraph (:1679).
        subgraph.dirty = True

        if not subgraph.strong_driver:
            continue

        # Classify the strong driver by kind and cache it (:1686-1727).
        if driver.kind in (ItemKind.LABEL, ItemKind.HIER_LABEL):
            maps.m_local_label_cache.setdefault((subgraph.sheet_path, name), []).append(subgraph)
        elif driver.kind is ItemKind.GLOBAL_LABEL:
            maps.m_global_label_cache.setdefault(name, []).append(subgraph)
        elif driver.kind is ItemKind.PIN:
            if driver.priority is Priority.GLOBAL_POWER_PIN:
                maps.m_global_label_cache.setdefault(name, []).append(subgraph)
            elif driver.priority is Priority.LOCAL_POWER_PIN:
                maps.m_local_label_cache.setdefault((subgraph.sheet_path, name), []).append(
                    subgraph
                )
            # Else: a plain pin cannot reach this branch through resolve(),
            # because a strong driver's priority always exceeds PIN. Source
            # logs this as an unexpected normal pin and does nothing else
            # (:1710-1715).
        # Else: any other driver kind is an unexpected strong driver. Source
        # logs it and does nothing else (:1719-1726).

    return maps
