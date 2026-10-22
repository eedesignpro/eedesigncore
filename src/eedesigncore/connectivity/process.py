"""Subgraph processing, phase 8.

Source: ``CONNECTION_GRAPH::processSubGraphs``
(``eeschema/connection_graph.cpp:1892-2242``) and ``CONNECTION_SUBGRAPH::Absorb``
(``:502-536``), tag 10.0.3. Mapped by audit
``connection-graph-algorithm-2026-09-02.md`` phase 8.

This is where same-sheet subgraphs merge, and where a weakly driven name that
collides gets a suffix.

**Two things this module deliberately does NOT do, each with its reason.**

Net and bus CODES are not assigned. The audit records that a code's value depends
on schematic file item order through several layers, so a code is not
reproducible and must never be compared. Nothing downstream reads one.

Bus-neighbor linking IS ported (`:2194-2219`). A bus subgraph and a plain net
subgraph on the same sheet that share a member name link both ways, which is what
gives ``propagate_bus_neighbors`` something to walk. The deliberate alphabetical
ordering lives in that phase, not here.

**A link is keyed on the member CONNECTION, not on its name.** Source copies the
bus subgraph's driver connection first (`:2089`) and keys the map on a member of
that COPY, so the key is frozen here while the live connection keeps being
renamed by the hierarchy. Keying on the name instead would key on the one thing
that changes, and the member would stop matching the moment the bus was renamed.

**The order-dependence note that matters.** ``Absorb`` mutates both subgraphs
during the same loop that iterates them, so WHICH object survives as the absorber
depends on iteration order. The audit is explicit that the end result, meaning
which items sit together and what name they carry, does not. So this port tracks
the outcome through the item set and the name, never through subgraph identity.
"""

from __future__ import annotations

from eedesigncore.connectivity.bus import AliasResolver, ConnectionType
from eedesigncore.connectivity.connection import (
    MemberConn,
    connection_from_name,
    copy_conn,
)
from eedesigncore.connectivity.drivers import resolve
from eedesigncore.connectivity.subgraph import (
    BusNeighborLink,
    ItemKind,
    Priority,
    Subgraph,
    prepend_path,
)


def create_new_name(name: str, taken: set[str]) -> str:
    """Append a numeric suffix until the name is free, porting ``:1915-1948``.

    KiCad appends ``_1``, then ``_2``, and retries while the candidate is already
    claimed (`:1972-1973`). A bus group with a prefix takes the suffix inside the
    prefix instead of on the whole name, which this port does not reach because
    it does not handle bus groups yet.
    """
    index = 1
    while True:
        candidate = f"{name}_{index}"
        if candidate not in taken:
            return candidate
        index += 1


def _absorb(keeper: Subgraph, other: Subgraph) -> None:
    """Merge *other* into *keeper*, porting ``Absorb`` (`:502-536`).

    Both must sit on the same sheet, which the source asserts at ``:504``. The
    absorbed subgraph is emptied and marked, and its own absorbed children
    re-point at the survivor so a multi-level chain ends at the live subgraph.
    """
    if keeper.sheet_path != other.sheet_path:
        raise ValueError(
            "Absorb requires one sheet. KiCad asserts this at "
            "connection_graph.cpp:504, because a cross-sheet merge is propagation "
            "and not absorption."
        )
    keeper.pins.extend(other.pins)
    keeper.source_items.update(other.source_items)
    keeper.drivers.extend(other.drivers)
    keeper.hier_pins.extend(other.hier_pins)
    keeper.hier_ports.extend(other.hier_ports)
    keeper.hier_pin_children.extend(other.hier_pin_children)
    keeper.no_connect = keeper.no_connect or other.no_connect
    keeper.multiple_drivers = keeper.multiple_drivers or other.multiple_drivers

    other.absorbed = True
    other.absorbed_by = keeper
    other.dirty = False
    other.driver = None
    other.pins = []
    other.drivers = []


def _connection_copy(subgraph: Subgraph) -> MemberConn:
    """A frozen copy of a subgraph driver connection (`:2089`).

    Source pushes ``std::make_shared<SCH_CONNECTION>( *connection )``, a COPY.
    Every bus neighbour link below is keyed on a member of this copy, which is
    what makes the key survive a later rename of the live connection.
    """
    return MemberConn(
        type=subgraph.conn_type,
        local_name=subgraph.name,
        name=subgraph.name,
        sheet=subgraph.conn_sheet,
        sheet_prefix=subgraph.conn_sheet_prefix,
        members=[copy_conn(m) for m in subgraph.conn_members],
    )


def _secondary_connections(
    subgraph: Subgraph, resolve_alias: AliasResolver | None = None
) -> list[MemberConn]:
    """Port ``add_connections_to_check`` (`:2091-2118`).

    Every driver OTHER than the chosen one contributes a connection, so a
    subgraph carrying two labels can merge on either name. A connection of a
    different type, or one whose unqualified name already matches, is dropped.

    Source iterates ``m_items`` and asks ``getDefaultConnection``
    (`:3243-3273`) for each one. That function is a switch over FOUR item types,
    and everything else returns null:

    - the three label kinds, always;
    - a pin, but ONLY when it is a power pin.

    **A sheet pin has no case, so it never contributes a secondary name.** Nor
    does an ordinary signal pin. Being a driver is not enough, and an earlier
    version of this port assumed it was: it built a connection from every
    non-chosen driver, which handed this phase a sheet pin's name as something to
    merge on.

    That single extra name was the last grouping fault in the corpus. On one
    sheet a net carried two sheet pins, ``EN`` into one child and ``PGOOD`` into
    another, and its chosen driver was ``EN``. Offering ``PGOOD`` as a secondary
    name matched a DIFFERENT net on the same sheet that a hierarchical label
    really did name ``PGOOD``, so the two were absorbed into one. That pulled a
    regulator's enable net into a processor reset net two levels up. KiCad never
    forms the name, so it never sees the match.
    """
    out: list[MemberConn] = []
    for driver in subgraph.drivers:
        if subgraph.driver is not None and driver is subgraph.driver:
            continue
        if driver.kind is ItemKind.PIN:
            if driver.priority not in (Priority.GLOBAL_POWER_PIN, Priority.LOCAL_POWER_PIN):
                continue
        elif driver.kind not in (ItemKind.LABEL, ItemKind.GLOBAL_LABEL, ItemKind.HIER_LABEL):
            continue
        candidate = connection_from_name(
            driver.name,
            driver.raw_name,
            sheet=subgraph.conn_sheet,
            sheet_prefix=subgraph.conn_sheet_prefix,
            prepend=prepend_path(driver),
            resolve_alias=resolve_alias,
        )
        if candidate.type is not subgraph.conn_type:
            continue
        if candidate.cached_name == subgraph.name:
            continue
        out.append(candidate)
    return out


def _matches(candidate: Subgraph, test_name: str) -> bool:
    """Whether *candidate* answers to *test_name* (`:2145-2190`).

    The chosen driver name first. Failing that, and only when the candidate has
    several drivers, each non-chosen driver own name. A sheet pin is excluded
    outright, because a sheet pin is never a merge candidate.
    """
    if candidate.name == test_name:
        return True
    if not candidate.multiple_drivers:
        return False
    for driver in candidate.drivers:
        if candidate.driver is not None and driver is candidate.driver:
            continue
        if driver.kind is ItemKind.SHEET_PIN:
            continue
        if driver.name == test_name:
            return True
    return False


def _link(links: list[BusNeighborLink], member: MemberConn, other: Subgraph) -> None:
    """Add *other* under *member*, porting a map insert keyed on the connection.

    Source uses ``std::set``, so a repeated pair is inserted once (`:2202-2203`).
    """
    for link in links:
        if link.member is member:
            if other not in link.neighbors:
                link.neighbors.append(other)
            return
    links.append(BusNeighborLink(member=member, neighbors=[other]))


def process(
    driver_subgraphs: list[Subgraph], *, resolve_alias: AliasResolver | None = None
) -> list[Subgraph]:
    """Run phase 8 and return the subgraphs that survive absorption.

    Ports the main loop at ``:1903-2221`` and the follow-up re-resolution at
    ``:2225-2240``, minus the two deliberate omissions in the module docstring.
    """
    # Step 1. Group by sheet (`:1898-1899`). Absorption only ever happens within
    # one sheet, so this is what bounds the candidate search.
    by_sheet: dict[tuple[str, ...], list[Subgraph]] = {}
    for subgraph in driver_subgraphs:
        by_sheet.setdefault(subgraph.sheet_path, []).append(subgraph)

    # Names already claimed, for the weak-driver conflict check. Keyed on the
    # QUALIFIED name, because source reads ``m_net_name_to_subgraphs_map``, which
    # phase 5 keys on ``Name()`` (`:1954`, `:1665`). Keying on the unqualified
    # form counts two sheets' same-named nets as one collision when they are not.
    claimed: dict[str, int] = {}
    for subgraph in driver_subgraphs:
        key = subgraph.full_name or subgraph.name
        claimed[key] = claimed.get(key, 0) + 1

    invalidated: list[Subgraph] = []

    for subgraph in driver_subgraphs:
        if subgraph.absorbed:
            continue

        # Step 2.1. A weakly driven subgraph whose name collides takes a suffix
        # (`:1912-2030`). A strongly driven one keeps its name, because a label
        # is deliberate and a collision between two labels is the user's meaning.
        qualified = subgraph.full_name or subgraph.name
        if not subgraph.strong_driver and claimed.get(qualified, 0) > 1:
            # Source appends the suffix through ``SetSuffix`` (`:1944`), which
            # recaches BOTH name forms from one suffix string. So the same
            # index lands on both and they cannot drift apart.
            fresh = create_new_name(qualified, set(claimed))
            index = fresh.rsplit("_", 1)[1]
            claimed[qualified] -= 1
            claimed[fresh] = 1
            subgraph.name = f"{subgraph.name}_{index}"
            subgraph.full_name = fresh
        elif (
            not subgraph.strong_driver
            and subgraph.driver is not None
            and subgraph.driver.kind is ItemKind.SHEET_PIN
        ):
            # A sheet-pin driver with no collision is promoted to strong, which
            # makes it eligible to merge below (`:1994-2026`). The source first
            # checks that promotion would not collide with a same-named global
            # label on the same sheet.
            same_sheet = by_sheet.get(subgraph.sheet_path, [])
            collides = any(
                other is not subgraph
                and other.name == subgraph.name
                and other.driver is not None
                and other.driver.priority is Priority.GLOBAL
                for other in same_sheet
            )
            if not collides:
                subgraph.strong_driver = True

        # Step 2.3. Re-arm for propagation (`:2057`).
        subgraph.dirty = True

        # Step 2.4. A weak driver cannot merge (`:2065-2066`).
        if not subgraph.strong_driver:
            continue

        # Steps 2.5 to 2.8, one queue and one loop, as source has it
        # (`:2085-2219`). The queue starts from a COPY of this subgraph's own
        # connection and expands a bus into its members as it is walked, so a
        # vector nested in a group is reached on a later turn (`:2135-2140`).
        queue: list[MemberConn] = [_connection_copy(subgraph)]
        queue.extend(_secondary_connections(subgraph, resolve_alias))
        checked: set[int] = set()
        position = 0

        while position < len(queue):
            member = queue[position]
            position += 1

            if id(member) in checked:
                continue
            checked.add(id(member))

            if member.is_bus:
                queue.extend(member.members)

            # The UNQUALIFIED form on both sides (`:2141`, `:2148`). Comparing
            # qualified names here would never match, because a member takes its
            # path from its bus and a candidate from its own driver.
            test_name = member.cached_name

            for candidate in by_sheet.get(subgraph.sheet_path, []):
                if candidate.absorbed or candidate is subgraph or not candidate.strong_driver:
                    continue
                if not _matches(candidate, test_name):
                    continue

                if subgraph.is_bus and candidate.conn_type is ConnectionType.NET:
                    # A LINK, never a merge: a bus and a net are different
                    # connection types and stay separate objects (`:2202-2203`).
                    _link(subgraph.bus_neighbors, member, candidate)
                    _link(candidate.bus_parents, member, subgraph)
                elif (not subgraph.is_bus and not candidate.is_bus) or (
                    subgraph.conn_type is candidate.conn_type
                ):
                    queue.extend(_secondary_connections(candidate, resolve_alias))
                    _absorb(subgraph, candidate)
                    if subgraph not in invalidated:
                        invalidated.append(subgraph)

    # Step 3. Re-resolve any subgraph that grew, over its now larger driver set
    # (`:2225-2240`). This pass does not trigger further absorption.
    for subgraph in invalidated:
        if not subgraph.absorbed:
            kept_name = subgraph.name
            resolve(subgraph)
            if not subgraph.name:
                subgraph.name = kept_name

    return [s for s in driver_subgraphs if not s.absorbed]
