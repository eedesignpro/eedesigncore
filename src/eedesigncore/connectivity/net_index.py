"""The net-name to subgraphs index, ``m_net_name_to_subgraphs_map``.

Source: ``connection_graph.h:829`` and ``CONNECTION_GRAPH::recacheSubgraphName``
(``eeschema/connection_graph.cpp:3365-3378``), tag 10.0.3.

Only one phase reads this index: the multi-bus-parent linking sweep, phase 9e
(``:2600-2597``). When that sweep renames a bus member, it must also rename every
OTHER subgraph that still answers to the member's old name, wherever in the
hierarchy it sits. Without an index there is no way to find them, and each one
keeps a name that no longer exists as a net.

The index is keyed on the QUALIFIED name, which is what ``Name()`` returns by
default (``sch_connection.h:143``). Every phase that renames a subgraph must
recache it here, or the index answers with the pre-rename grouping.
"""

from __future__ import annotations

from eedesigncore.connectivity.subgraph import Subgraph


class NetNameIndex:
    """Qualified net name to the subgraphs currently carrying it.

    Source keeps this map from phase 5 onward. This port builds it once at the
    start of phase 9, because phase 9e is its only reader and no earlier phase
    consults it. Building it later cannot change a result; failing to RECACHE it
    during phase 9 can, so every rename in phase 9 goes through ``recache``.
    """

    def __init__(self, subgraphs: list[Subgraph]) -> None:
        self._by_name: dict[str, list[Subgraph]] = {}
        for subgraph in subgraphs:
            if subgraph.absorbed:
                continue
            self._by_name.setdefault(_key(subgraph), []).append(subgraph)

    def recache(self, subgraph: Subgraph, old_name: str) -> None:
        """Move *subgraph* from *old_name* to its current name (`:3365-3378`)."""
        entry = self._by_name.get(old_name)
        if entry is not None:
            self._by_name[old_name] = [s for s in entry if s is not subgraph]
        current = self._by_name.setdefault(_key(subgraph), [])
        if not any(s is subgraph for s in current):
            current.append(subgraph)

    def get(self, name: str) -> list[Subgraph]:
        """The subgraphs under *name*, as a COPY.

        Source copies the vector before iterating, because recaching inside the
        loop would invalidate the iterator (`:2571-2572`). The copy is part of the
        contract, not a convenience.
        """
        return list(self._by_name.get(name, ()))


def _key(subgraph: Subgraph) -> str:
    return subgraph.full_name or subgraph.name
