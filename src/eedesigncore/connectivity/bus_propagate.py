"""Bus neighbour propagation, and the multi-bus-parent sweep.

Source, tag 10.0.3:

- ``propagate_bus_neighbors``, the inline lambda inside ``propagateToNeighbors``
  (``eeschema/connection_graph.cpp:2953-3103``)
- the multi-bus-parent linking sweep, phase 9e (``:2524-2597``)

A bus subgraph and a plain net subgraph can share a member name on one sheet.
When they do, one of them names the other. This module decides which, and the
decision carries three things a naive port loses.

**The member is RE-MATCHED, never re-used.** The link made in phase 8 is keyed on
a member connection frozen at that moment. By the time this runs, the bus has
been renamed by the hierarchy, so the frozen key names something that no longer
exists. ``matchBusMember`` maps the frozen key onto the bus's CURRENT member, by
vector index or by local name, and the neighbour takes THAT name. Skipping the
re-match leaves one member net per sheet instead of the one net that crosses the
hierarchy boundary, which is what this engine did before.

**Where it is called from decides whether it works.** Source calls it from INSIDE
``propagateToNeighbors``: once at step 1 for a bus subgraph (``:3106-3107``), and
again at step 6 for every subgraph the winner was just cloned onto
(``:3196-3207``). A port that calls it only after propagation has finished
re-matches against names that were already final, so it re-matches nothing.

**The sort is deliberate.** The member list is sorted by name before anything is
processed (``:2955-2969``). The source comment says why: when several members
such as A0 through A3 all short onto one child net, the first one processed wins,
so sorting makes the alphabetically first name the winner. This is one of only
two places in the whole pipeline where KiCad imposes order on purpose.

**A neighbour already renamed from another sheet is left alone** (``:3034-3062``,
issue 18299). The exception is narrow: when the neighbour was renamed by a
DIFFERENT member of this same bus, that is preserved for determinism, and only a
name matching no member at all counts as stale.

**Priority decides direction** (``:3074-3099``). A neighbour driven at global
power rank or above names the bus member. Otherwise the member names the
neighbour, the rename propagates onward, and the member is then checked AGAIN in
case the hierarchy handed the neighbour something stronger (issue 18119).
"""

from __future__ import annotations

from collections.abc import Callable

from eedesigncore.connectivity.bus import AliasResolver, ConnectionType
from eedesigncore.connectivity.connection import (
    MemberConn,
    clone_into,
    connection_from_name,
    match_bus_member,
)
from eedesigncore.connectivity.net_index import NetNameIndex
from eedesigncore.connectivity.subgraph import Priority, Subgraph, prepend_path

# What propagate_bus_neighbors calls to re-propagate a neighbour it renamed
# (`:3092`). Returns the identities of the subgraphs that changed name.
Recurse = Callable[[Subgraph], set[int]]


def live(subgraph: Subgraph) -> Subgraph:
    """Follow the absorption chain to the surviving subgraph (`:2981-2982`).

    An absorbed subgraph is emptied but never deleted, so a stale reference must
    be resolved before use. Bounded, because a corrupt chain would otherwise
    loop; source relies on the chain being acyclic by construction.
    """
    current = subgraph
    for _ in range(64):
        if not current.absorbed or current.absorbed_by is None:
            return current
        current = current.absorbed_by
    return current


def _rank(subgraph: Subgraph) -> Priority:
    """The rank of the subgraph's OWN driver, not of its connection (`:3073`)."""
    return subgraph.driver.priority if subgraph.driver else Priority.NONE


def _try_harder(
    parent: Subgraph, neighbors: list[Subgraph], resolve_alias: AliasResolver | None
) -> MemberConn | None:
    """Match on a secondary driver when the frozen key matched nothing (`:2992-3013`).

    A neighbour with several drivers answers to more than one name, and one of
    those other names may still be a member of this bus.
    """
    for candidate in neighbors:
        if not candidate.multiple_drivers:
            continue
        for driver in candidate.drivers:
            conn = connection_from_name(
                driver.name,
                driver.raw_name,
                sheet=candidate.conn_sheet,
                sheet_prefix=candidate.conn_sheet_prefix,
                prepend=prepend_path(driver),
                resolve_alias=resolve_alias,
            )
            member = match_bus_member(parent.conn_type, parent.conn_members, conn)
            if member is not None:
                return member
    return None


def propagate_bus_neighbors(
    parent: Subgraph,
    *,
    index: NetNameIndex,
    stale: list[MemberConn],
    recurse: Recurse | None = None,
    resolve_alias: AliasResolver | None = None,
) -> set[int]:
    """Name the neighbours of one bus subgraph, or be named by them.

    *stale* collects the members the neighbour won, which step 7 of propagation
    re-matches across the whole visited set. Returns the identities of the
    subgraphs whose name changed.
    """
    renamed: set[int] = set()

    # The deliberate sort, by the FROZEN key's name (`:2955-2969`).
    for link in sorted(parent.bus_neighbors, key=lambda item: item.member.qualified_name):
        for raw_neighbor in list(link.neighbors):
            neighbor = live(raw_neighbor)

            # The re-match. The frozen key names the member as it was at phase 8;
            # this finds what that member is called NOW (`:2988`).
            member = match_bus_member(parent.conn_type, parent.conn_members, link.member)
            if member is None:
                member = _try_harder(parent, link.neighbors, resolve_alias)
            if member is None:
                # Source calls this probably an ERC error and skips it, rather
                # than guessing a member (`:3016-3022`).
                continue

            neighbor_name = neighbor.full_name or neighbor.name

            # Matching name already, so nothing to do (`:3029-3031`).
            if neighbor_name == member.qualified_name:
                continue

            # Already renamed from another sheet. Leave it, unless a different
            # member of THIS bus did the renaming, in which case that is
            # preserved for determinism (`:3034-3062`, issue 18299).
            if neighbor.conn_sheet != neighbor.sheet_path:
                if neighbor.conn_sheet != parent.conn_sheet:
                    continue
                # A name that still matches SOME member of this same bus means a
                # sibling member did the renaming. A name matching no member
                # means the member itself was renamed, and only then does this
                # fall through and update (`:3050-3062`). Compared by NAME and
                # not by vector index, because a non-bus connection defaults to
                # index 0 and would falsely match the first member.
                if any(m.qualified_name == neighbor_name for m in parent.conn_members):
                    continue

            # The guard against infinite recursion (`:3065-3066`). A bus can
            # never be a bus member's neighbour.
            if neighbor.conn_type is not ConnectionType.NET:
                continue

            if _rank(neighbor) >= Priority.GLOBAL_POWER_PIN:
                # The neighbour wins. The member takes its name and goes on the
                # stale list for step 7 (`:3076-3080`).
                clone_into(member, neighbor.as_conn())
                if not any(m is member for m in stale):
                    stale.append(member)
                continue

            # The member wins. The neighbour takes its name, is re-armed, and
            # re-propagates, so the rename continues outward (`:3083-3092`).
            old_name = neighbor.clone_conn_from(member)
            index.recache(neighbor, old_name)
            renamed.add(id(neighbor))
            neighbor.dirty = True
            if recurse is not None:
                renamed |= recurse(neighbor)

            # After the hierarchy has run, the neighbour may have picked up a
            # stronger driver. If so the member follows it (`:3094-3101`,
            # issue 18119).
            if (neighbor.full_name or neighbor.name) != member.qualified_name:
                clone_into(member, neighbor.as_conn())
                if not any(m is member for m in stale):
                    stale.append(member)

    return renamed


def link_multi_parent_buses(subgraphs: list[Subgraph], index: NetNameIndex) -> int:
    """Phase 9e: link buses joined by a shared member net (`:2524-2597`).

    A net subgraph with TWO OR MORE bus parents means two buses were shorted
    together by one of their members. Each parent's final name may differ from
    the local name that made the link, because the hierarchy renamed it. So for
    each parent this re-matches the member and gives it the net's name, then
    renames every other subgraph in the design that still answers to the member's
    old name.

    That last step is why the name index exists. Without it, a member net
    elsewhere in the hierarchy keeps a name that is no longer any net's name.

    Returns the number of subgraphs renamed.
    """
    renamed = 0

    for subgraph in subgraphs:
        # Source asserts here that propagation processed every subgraph. A dirty
        # subgraph at this point is a pipeline-ordering fault, not bad input.
        if len(subgraph.bus_parents) < 2:
            continue
        # Only a NET can short two buses together (`:2550`).
        if subgraph.conn_type is not ConnectionType.NET:
            continue

        conn = subgraph.as_conn()

        for link in subgraph.bus_parents:
            for raw_parent in list(link.neighbors):
                parent = live(raw_parent)

                match = match_bus_member(parent.conn_type, parent.conn_members, link.member)
                if match is None:
                    continue
                if conn.qualified_name == match.qualified_name:
                    continue

                old_name = match.qualified_name
                clone_into(match, conn)

                # Every subgraph still carrying the member's old name follows it.
                for old_subgraph in index.get(old_name):
                    target = live(old_subgraph)
                    previous = target.clone_conn_from(conn)
                    if previous != (target.full_name or target.name):
                        index.recache(target, previous)
                        renamed += 1

    return renamed
