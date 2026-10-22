"""Hierarchical propagation, phase 10.

Source: ``CONNECTION_GRAPH::propagateToNeighbors``
(``eeschema/connection_graph.cpp:2857-3240``), tag 10.0.3. Mapped by audit
``connection-graph-algorithm-2026-09-02.md`` phase 10.

This replaces the name-aliasing approximation an earlier attempt used. That
approximation linked a parent sheet pin to a child label by rewriting one name
into the other. The real phase does something different in four ways that change
results.

**It expands breadth-first across the WHOLE connected chain**, not one level. A
signal crossing four sheets forms one visited set, and the winner is chosen once
across all of it (`:3133-3141`).

**It defers a subgraph that can go both ways.** A subgraph holding both
hierarchical labels and sheet pins returns without processing unless forced, so
whichever direction reaches it first owns it (`:3112-3124`). That deferral is why
a later forced sweep exists at all. A port that drops the guard double-processes
those subgraphs, and a port that drops the forced sweep loses them.

**It picks the best driver by a strict total order**, so the result does not
depend on traversal order (`:3143-3185`). The alphabetical tie-break compares the
QUALIFIED name, because ``Name()`` defaults to the form carrying the path
(`sch_connection.h:143`). Comparing the unqualified form leaves two same-named
subgraphs on different sheets tied, and the winner then falls to list order.

**It drives bus member propagation, at two points.** Step 1 for a bus subgraph
(`:3106-3107`), and step 6 for every subgraph the winner was just cloned onto
(`:3196-3207`). Those two call sites are what make a bus member cross a sheet
boundary: the member is re-matched against the bus's new name every time the bus
is renamed. Calling bus propagation only after propagation has finished, as an
earlier version of this module did, re-matches names that are already final and
therefore changes nothing.
"""

from __future__ import annotations

from collections import deque

from eedesigncore.connectivity.bus import AliasResolver
from eedesigncore.connectivity.bus_propagate import live, propagate_bus_neighbors
from eedesigncore.connectivity.connection import MemberConn, clone_into, match_bus_member
from eedesigncore.connectivity.net_index import NetNameIndex
from eedesigncore.connectivity.subgraph import Priority, Subgraph

# A sheet path to the subgraphs that live on it. Ports m_sheet_to_subgraphs_map
# (`connection_graph.h:823`), built in phase 9a.
SheetMap = dict[tuple[str, ...], list[Subgraph]]

# A recursion bound. Source has none: bus propagation re-enters propagation for
# every neighbour it renames, and relies on the name-equality checks to settle.
# They do settle on every design measured, and an unbounded mutual recursion in
# Python ends in a crash rather than a wrong answer, so this bounds it instead.
_MAX_DEPTH = 32


def build_sheet_map(subgraphs: list[Subgraph]) -> SheetMap:
    """Group subgraphs by sheet path, porting the map phase 9a builds."""
    out: SheetMap = {}
    for subgraph in subgraphs:
        out.setdefault(subgraph.sheet_path, []).append(subgraph)
    return out


def _driver_rank(subgraph: Subgraph) -> Priority:
    return subgraph.driver.priority if subgraph.driver else Priority.NONE


def _best_driver_key(subgraph: Subgraph) -> tuple[int, int, int, int, str]:
    """The step 5 comparison, as a sort key (`:3165-3184`).

    Sign convention: every field is arranged so that SMALLER sorts better, which
    lets a caller take the minimum.

    Source writes five replacement conditions applied in order against a running
    best. Four of them compose exactly into the tuple below, checked case by case
    against the source clauses.

    **The fifth does not, and this is a DELIBERATE divergence.** Clause (a)
    replaces the running best whenever a candidate is at global-power rank or
    above, with no further test. So when a chain holds two or more such
    candidates, the winner in source is whichever comes last while iterating an
    UNORDERED set, and the outcome is not reproducible. This port takes the
    alphabetically lower qualified name instead. A faithful port of a
    non-deterministic choice is not possible, and reproducible output is what this
    library exists to provide, so the tie is broken and recorded here rather than
    left to memory layout.
    """
    rank = _driver_rank(subgraph)
    return (
        0 if rank >= Priority.GLOBAL_POWER_PIN else 1,
        0 if subgraph.strong_driver else 1,
        -int(rank),
        len(subgraph.sheet_path),
        # The QUALIFIED name, per `Name()`'s default (`:3167`).
        subgraph.full_name or subgraph.name,
    )


def _visit(parent: Subgraph, sheets: SheetMap, search: deque[Subgraph]) -> None:
    """Link *parent* to its hierarchical neighbors, both directions.

    Ports the ``visit`` lambda (`:2864-2951`). A match is by NAME on both sides.
    Never by identifier and never by pin index.
    """
    # Downward. A sheet pin on this subgraph opens onto a child sheet, so the
    # child path is this path plus that sheet (`:2866-2903`).
    for pin_name, child_sheet in parent.hier_pin_children:
        child_path = (*parent.sheet_path, child_sheet)
        for candidate in sheets.get(child_path, []):
            if not candidate.strong_driver or not candidate.hier_ports:
                continue
            if pin_name not in candidate.hier_ports:
                continue
            if candidate.hier_parent is None:
                candidate.hier_parent = parent
            if not any(child is candidate for child in parent.hier_children):
                parent.hier_children.append(candidate)
            search.append(candidate)

    # Upward. A hierarchical label on this subgraph ascends to the parent sheet,
    # so the parent path drops one level (`:2906-2950`).
    if parent.hier_ports and parent.sheet_path:
        up_path = parent.sheet_path[:-1]
        last_sheet = parent.sheet_path[-1]
        for candidate in sheets.get(up_path, []):
            # The upward branch requires the two connections to be the SAME KIND
            # (`:2921-2926`). The downward branch has no such test. Without it a
            # net-typed subgraph joins a bus-typed chain and the whole bus takes
            # one member's name, or the reverse.
            if candidate.is_bus != parent.is_bus:
                continue
            for pin_name, child_sheet in candidate.hier_pin_children:
                # Match by the last sheet plus name equality (`:2932-2944`).
                if child_sheet != last_sheet or pin_name not in parent.hier_ports:
                    continue
                # Source records the found parent as a CHILD of the subgraph that
                # went looking (`:2947`), and sets no hier_parent on this branch.
                # The link is bookkeeping for KiCad's incremental-update walk and
                # nothing in this engine reads it, but recording it backwards
                # would mislead the next reader.
                if not any(child is candidate for child in parent.hier_children):
                    parent.hier_children.append(candidate)
                search.append(candidate)
                break


def propagate(
    subgraph: Subgraph,
    sheets: SheetMap,
    index: NetNameIndex,
    *,
    force: bool = False,
    depth: int = 0,
    resolve_alias: AliasResolver | None = None,
) -> set[int]:
    """Propagate one subgraph's name across its hierarchical chain.

    Returns the identities of the subgraphs whose name changed, so a caller can
    log the work and a test can assert the phase fired.
    """
    if depth > _MAX_DEPTH:
        return set()

    renamed: set[int] = set()
    stale: list[MemberConn] = []

    def recurse(neighbor: Subgraph) -> set[int]:
        return propagate(
            neighbor,
            sheets,
            index,
            force=force,
            depth=depth + 1,
            resolve_alias=resolve_alias,
        )

    # Step 1. A bus propagates to its local neighbours BEFORE the hierarchy
    # (`:3105-3107`). This runs even for a subgraph the guard below defers.
    if subgraph.is_bus:
        renamed |= propagate_bus_neighbors(
            subgraph,
            index=index,
            stale=stale,
            recurse=recurse,
            resolve_alias=resolve_alias,
        )

    # Step 2. The deferral guard (`:3112-3124`).
    if not force and subgraph.hier_ports and subgraph.hier_pin_children:
        # Defer. Whichever direction reaches this subgraph first owns it.
        return renamed
    if not subgraph.hier_ports and not subgraph.hier_pin_children:
        subgraph.dirty = False
        return renamed

    # Steps 3 and 4. Breadth-first expansion across the whole chain.
    visited: list[Subgraph] = [subgraph]
    seen = {id(subgraph)}
    search: deque[Subgraph] = deque()
    _visit(subgraph, sheets, search)
    while search:
        node = search.popleft()
        node.dirty = False
        if id(node) in seen:
            continue
        seen.add(id(node))
        visited.append(node)
        _visit(node, sheets, search)

    # Step 5. One winner across the whole visited set.
    #
    # The search is GUARDED. Source only looks for a better driver when the
    # subgraph it started from is itself below global-power rank (`:3155`); a
    # globally driven subgraph keeps its own name and the loop never runs. An
    # earlier version of this port always searched, so two globally driven
    # subgraphs in one chain resolved to the alphabetically lower name instead of
    # to the one the walk began at.
    if _driver_rank(subgraph) >= Priority.GLOBAL_POWER_PIN:
        winner = subgraph
    else:
        winner = min(visited, key=_best_driver_key)
    winner_conn = winner.as_conn()

    # Step 6. Clone the winning connection onto every visited subgraph, then let
    # each one re-match its bus members against the name it just took.
    for node in visited:
        old_name = node.clone_conn_from(winner_conn)
        if old_name != (node.full_name or node.name):
            index.recache(node, old_name)
            renamed.add(id(node))
        if winner_conn.is_bus:
            renamed |= propagate_bus_neighbors(node, index=index, stale=stale, recurse=recurse)

    # Step 7. A bus member may have been upgraded to a global or a power name
    # anywhere along the way. Every instance of that member, on every visited
    # subgraph, follows it (`:3209-3237`).
    if winner_conn.is_bus and stale:
        for stale_member in list(stale):
            for node in visited:
                match = match_bus_member(node.conn_type, node.conn_members, stale_member)
                if match is None:
                    continue
                clone_into(match, stale_member)
                renamed |= propagate_bus_neighbors(node, index=index, stale=stale, recurse=recurse)

    # Step 8.
    subgraph.dirty = False
    return renamed


def _promote_secondary_globals(
    subgraph: Subgraph,
    globals_only: list[Subgraph],
    sheets: SheetMap,
    index: NetNameIndex,
    *,
    resolve_alias: AliasResolver | None = None,
) -> set[int]:
    """Pull same-named subgraphs onto this subgraph's name (`:2470-2510`).

    A subgraph driven by a global name can carry MORE than one global driver: two
    global labels on one net, or a global label sharing a net with a power
    symbol. Only one of them won the driver contest. Every other subgraph in the
    design named after one of the losers has to follow the winner, or the design
    ends up with two nets where the drawing says one.

    Two guards decide the reach, and dropping either one is a grouping error:

    - **A non-global secondary name reaches only the SAME SHEET** (`:2489-2490`).
      A local name that promoted design-wide would drag an unrelated net of the
      same name into this one from anywhere in the hierarchy.
    - **The whole pass is gated on multiple STRONG drivers** (`:2474`). With the
      flag computed from every driver instead, an ordinary two-pin net qualifies
      and the pass runs where KiCad never runs it.

    Each promoted candidate is marked clean and propagated immediately, so its own
    hierarchy chain follows before the outer loop moves on.
    """
    if subgraph.local_driver or not subgraph.multiple_drivers:
        return set()

    renamed: set[int] = set()
    own_conn = subgraph.as_conn()
    own_name = subgraph.full_name or subgraph.name

    for driver in subgraph.drivers:
        if driver is subgraph.driver:
            continue
        secondary_name = driver.name
        if secondary_name == own_name:
            continue
        secondary_is_global = driver.priority >= Priority.GLOBAL_POWER_PIN

        for candidate in globals_only:
            if candidate is subgraph:
                continue
            if not secondary_is_global and candidate.sheet_path != subgraph.sheet_path:
                continue
            if not any(d.name == secondary_name for d in candidate.drivers):
                continue

            old_name = candidate.clone_conn_from(own_conn)
            if old_name != (candidate.full_name or candidate.name):
                index.recache(candidate, old_name)
                renamed.add(id(candidate))

            # Source marks the candidate clean BEFORE propagating it (`:2503`),
            # so the outer loop will not visit it again.
            candidate.dirty = False
            renamed |= propagate(
                live(candidate), sheets, index, force=False, resolve_alias=resolve_alias
            )

    return renamed


def propagate_all(
    driver_subgraphs: list[Subgraph],
    index: NetNameIndex,
    *,
    resolve_alias: AliasResolver | None = None,
) -> int:
    """Run phases 9c and 9d in order, which is what makes the deferral work.

    First a normal pass over every dirty subgraph. Then a FORCED pass over
    whatever is still dirty, which collects the subgraphs the deferral guard
    skipped on their natural turn (`:2514-2522`).

    Returns the number of subgraphs renamed across both passes.
    """
    sheets = build_sheet_map(driver_subgraphs)
    renamed: set[int] = set()

    # `global_subgraphs`, the candidates the promotion below may rename
    # (`:2301-2308`). Everything whose driver reached global-power rank or above,
    # which is exactly the complement of `m_local_driver`.
    globals_only = [s for s in driver_subgraphs if not s.local_driver]

    for subgraph in driver_subgraphs:
        if not subgraph.dirty:
            continue
        renamed |= _promote_secondary_globals(
            subgraph, globals_only, sheets, index, resolve_alias=resolve_alias
        )
        renamed |= propagate(
            live(subgraph), sheets, index, force=False, resolve_alias=resolve_alias
        )

    # The leftover sweep. Without it, every subgraph the guard deferred keeps its
    # own name and a signal that crosses in both directions never joins.
    for subgraph in driver_subgraphs:
        if subgraph.dirty:
            renamed |= propagate(
                live(subgraph), sheets, index, force=True, resolve_alias=resolve_alias
            )

    return len(renamed)
