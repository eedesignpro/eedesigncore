"""Driver resolution, porting ``CONNECTION_SUBGRAPH::ResolveDrivers``.

Source: ``eeschema/connection_graph.cpp:132-287`` at tag 10.0.3, mapped by audit
``connection-graph-algorithm-2026-09-02.md`` phase 4.

Two parts, and both matter.

**The rank.** ``GetDriverPriority`` (`connection_graph.cpp:577-616`) returns one
of eight values. The doxygen comment above the enum documents seven and merges the
power-pin ranks. It is stale.

**The tie-break.** When candidates share a rank, ``candidate_cmp``
(`connection_graph.cpp:193-258`) sorts them by five rules in order. The rules are
ported below in the same order and with the same asymmetries. Getting the ORDER
wrong changes which driver wins on any schematic with competing equal-rank
drivers, which is most of them.
"""

from __future__ import annotations

from functools import cmp_to_key

from eedesigncore.connectivity.subgraph import Driver, ItemKind, Priority, Subgraph

# The rank a driver of each item kind carries. A kind absent here returns NONE,
# which mirrors the `default` branch of the switch (`connection_graph.cpp:613-615`).
# A directive label falls to that default and so never drives a net.
_RANK: dict[ItemKind, Priority] = {
    ItemKind.PIN: Priority.PIN,
    ItemKind.SHEET_PIN: Priority.SHEET_PIN,
    ItemKind.HIER_LABEL: Priority.HIER_LABEL,
    ItemKind.LABEL: Priority.LOCAL_LABEL,
    ItemKind.GLOBAL_LABEL: Priority.GLOBAL,
}


def driver_priority(
    kind: ItemKind,
    *,
    is_global_power: bool,
    is_local_power: bool,
    excluded_from_board: bool = False,
    lib_reference: str = "",
) -> Priority:
    """The rank for one candidate driver, porting `connection_graph.cpp:593-612`.

    A power pin outranks a local label and is outranked by a global label. The
    two power ranks are distinct, which is exactly what the stale comment merges.

    **A pin on a symbol that is not a real component ranks NONE**, and the two
    tests for that come after the power tests, so a power pin is never demoted
    (`:600-608`):

    - the placed symbol is excluded from the board, or
    - the LIBRARY symbol's Reference field begins with a hash.

    A power flag is the everyday case of the second test: its library reference
    is ``#FLG``. Rank NONE still lets the pin drive when nothing else can, but it
    loses every contest against a real pin, so the net takes a real component's
    name. Omitting this rule named three supply nets after a power flag rather
    than after the first capacitor on them.
    """
    if kind is ItemKind.PIN:
        if is_global_power:
            return Priority.GLOBAL_POWER_PIN
        if is_local_power:
            return Priority.LOCAL_POWER_PIN
        if excluded_from_board or lib_reference.startswith("#"):
            return Priority.NONE
        return Priority.PIN
    return _RANK.get(kind, Priority.NONE)


def _compare(a: Driver, b: Driver) -> int:
    """Port of ``candidate_cmp``, five rules in source order.

    Returns a negative number when *a* sorts before *b*, mirroring the C++
    comparator's true result. The C++ returns true for "a before b", so each rule
    below maps a true to -1.
    """
    # Rule 1. A bus that is a strict superset of the other sorts first
    # (`:198-209`). Only applies when BOTH are buses.
    if a.is_bus and b.is_bus:
        b_subset = b.bus_members < a.bus_members
        a_subset = a.bus_members < b.bus_members
        if b_subset and not a_subset:
            return -1
        if not b_subset and a_subset:
            return 1

    # Rule 2. Between two pins, a global power parent wins, then a local power
    # parent (`:211-232`). Note the asymmetry: the check fires only when the two
    # differ, so equal values fall through rather than deciding.
    if a.kind is ItemKind.PIN and b.kind is ItemKind.PIN:
        a_global = a.priority is Priority.GLOBAL_POWER_PIN
        b_global = b.priority is Priority.GLOBAL_POWER_PIN
        if a_global != b_global:
            return -1 if a_global else 1

        a_local = a.priority is Priority.LOCAL_POWER_PIN
        b_local = b.priority is Priority.LOCAL_POWER_PIN
        if a_local != b_local:
            return -1 if a_local else 1

    # Rule 3. Between two sheet pins, an output shape wins (`:234-246`). Again
    # only when the shapes differ.
    if (
        a.kind is ItemKind.SHEET_PIN
        and b.kind is ItemKind.SHEET_PIN
        and a.is_output_shape != b.is_output_shape
    ):
        return -1 if a.is_output_shape else 1

    # Rule 4. A name containing the literal "-Pad" is low quality and loses
    # (`:248-255`). This is why an explicit label beats an implicit pin name.
    a_low = "-Pad" in a.name
    b_low = "-Pad" in b.name
    if a_low != b_low:
        return 1 if a_low else -1

    # Rule 5. Plain alphabetical on the driver name (`:257`).
    if a.name != b.name:
        return -1 if a.name < b.name else 1
    return 0


def resolve(subgraph: Subgraph) -> None:
    """Set the driver fields on *subgraph*, in place.

    Ports the tail of ``ResolveDrivers`` (`connection_graph.cpp:132-287`).

    **Only the drivers at the HIGHEST rank compete.** Source keeps two separate
    collections (`:160-176`): ``strong_drivers``, everything at rank HIER_LABEL or
    above, used solely to decide what to drop; and ``candidates``, which is
    CLEARED whenever a higher rank appears, so it ends up holding just the joint
    top rank. The tie-break then runs among equals.

    An earlier version of this port used the strong set as the candidate set. A
    rank-4 local label then competed with a rank-7 global label, the tie-break
    found their names equal and returned no preference, and the winner fell to
    whichever came first in the item list. So the result depended on FILE ORDER,
    which is the one thing this engine must never do. Caught on constructed
    geometry: a global bus label and a local label of the same name on one net,
    where KiCad picks the global and this picked the local.
    """
    if not subgraph.drivers:
        subgraph.driver = None
        subgraph.strong_driver = False
        subgraph.multiple_drivers = False
        subgraph.name = ""
        return

    # A pin at rank PIN whose symbol is not in the netlist takes no part in the
    # contest at all (`:156-164`). ``IsInNetlist`` is false when the INSTANCE
    # reference begins with a hash (`sch_symbol.cpp:722`) — a different reference
    # from the one `driver_priority` reads, which is the library symbol's.
    # The test is on rank PIN only, so a power pin is never skipped.
    eligible = [
        d
        for d in subgraph.drivers
        if not (
            d.priority is Priority.PIN and d.pin is not None and d.pin.reference.startswith("#")
        )
    ]
    if not eligible:
        subgraph.driver = None
        subgraph.strong_driver = False
        subgraph.local_driver = True
        subgraph.name = ""
        return

    highest = max(d.priority for d in eligible)
    candidates = [d for d in eligible if d.priority == highest]
    candidates.sort(key=cmp_to_key(_compare))

    winner = candidates[0]
    subgraph.driver = winner
    # `m_strong_driver = highest_priority >= PRIORITY::HIER_LABEL` (`:179-180`).
    subgraph.strong_driver = highest >= Priority.HIER_LABEL
    # `m_local_driver = ( highest_priority < PRIORITY::GLOBAL_POWER_PIN )`
    # (`:183`). Everything below that rank is local, INCLUDING a plain pin. An
    # earlier version listed four ranks by hand and so called a pin-driven
    # subgraph non-local, which is backwards.
    subgraph.local_driver = highest < Priority.GLOBAL_POWER_PIN
    subgraph.name = winner.name

    # `if( strong_drivers.size() > 1 ) m_multiple_drivers = true;` (`:262-263`).
    # It counts the STRONG set (`:163-164`), never every driver. An earlier
    # version of this port counted every driver, so an ordinary net with two pins
    # reported multiple drivers and two phases that gate on the flag ran on
    # subgraphs KiCad never offers them: the secondary-name match in phase 8 and
    # the global promotion in phase 9c.
    #
    # Source only ever sets the flag TRUE, and absorption ORs it in, so it is
    # sticky across the run.
    if sum(1 for d in subgraph.drivers if d.is_strong) > 1:
        subgraph.multiple_drivers = True

    # Drop the weak drivers once a strong one exists (`:266-271`). A later phase
    # that walks `drivers` to find secondary names must not be offered a weak one,
    # because source has already removed them by then.
    if subgraph.strong_driver:
        subgraph.drivers = [d for d in subgraph.drivers if d.is_strong]


def resolve_all(subgraphs: list[Subgraph]) -> list[Subgraph]:
    """Port of ``resolveAllDrivers`` (`connection_graph.cpp:1583-1654`).

    Returns the subgraphs that ended up with a driver, which is what KiCad keeps
    in ``m_driver_subgraphs`` (`:1649-1653`).
    """
    for subgraph in subgraphs:
        resolve(subgraph)
    return [s for s in subgraphs if s.driver is not None]
