"""Phases 2 and 3: item pairing and the subgraph flood fill.

Source: ``updateItemConnectivity`` (`connection_graph.cpp:1313-1497`) and
``buildItemSubGraphs`` (`:1497-1580`), tag 10.0.3. Mapped by audit
``connection-graph-algorithm-2026-09-02.md`` phases 2 and 3.

The two phases split cleanly.

**Phase 2 builds the adjacency.** Every item pushes its own connection points
into a map keyed on the exact integer point. Then items sharing a key become
connected, subject to the two-way ``ConnectionPropagatesTo`` gate.

**Phase 3 floods the adjacency into components.** A connected-components walk
over that adjacency, which the audit confirms is order-independent because it is
graph reachability over a fixed relation.

Net codes are not assigned. Phase 3 assigns one from an incrementing counter, and
the audit records that the value depends on item iteration order and must never
carry net identity.

This module replaces ``graph.py``, which combined the same two phases but emitted
its own subgraph type instead of the ported state model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from eedesigncore.connectivity.bus import AliasResolver, configure_from_label, is_bus_label
from eedesigncore.connectivity.default_names import get_default_net_name
from eedesigncore.connectivity.drivers import driver_priority
from eedesigncore.connectivity.geometry import PinRef, SheetGeometry
from eedesigncore.connectivity.netname import escape_netname
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Subgraph
from eedesigncore.connectivity.units import IUPoint

# A label kind as the schematic spells it, mapped to the connectivity item kind.
_LABEL_KIND: dict[str, ItemKind] = {
    "label": ItemKind.LABEL,
    "global_label": ItemKind.GLOBAL_LABEL,
    "hierarchical_label": ItemKind.HIER_LABEL,
    "netclass_flag": ItemKind.DIRECTIVE_LABEL,
}


@dataclass(frozen=True)
class _Item:
    """One participant, identified by kind and index into the geometry lists."""

    kind: ItemKind
    index: int


@dataclass(frozen=True)
class _Gate:
    """What the propagation gate needs beyond the two items themselves.

    Two of the gate's clauses ask about an item's LAYER rather than its type, and
    a layer is decided elsewhere: a junction moves to the bus junction layer when
    a bus passes through its point (`connection_graph.cpp:1433-1436`), and a plain
    label counts as a bus label when its own text parses as one. Both are
    precomputed here so the gate itself stays a pure test.
    """

    pins: list[PinRef]
    # Junction indices sitting on a point a bus passes through.
    bus_junctions: frozenset[int]
    # Indices into geo.labels whose text parses as a bus label.
    bus_labels: frozenset[int]


def _propagates(a: _Item, b: _Item, gate: _Gate) -> bool:
    """The two-way gate, per ``:1468-1470``.

    ``CanConnect`` plays NO part. It is editor-only and the graph never calls it.
    """
    # A no-connect PIN propagates to nothing (`sch_pin.cpp:1631-1634`). This
    # blocks the EDGE and not the pin's membership. The pin still enters m_items
    # in phase 2 (`connection_graph.cpp:1277`, with no no-connect exclusion in
    # updatePinConnectivity at `:1256-1270`) and still seeds its own subgraph in
    # phase 3, because buildItemSubGraphs creates one for EVERY item whose
    # connection has no code yet (`:1511-1526`). It simply ends up alone, and
    # phase 9f then names it unconnected-(...).
    if a.kind is ItemKind.PIN and gate.pins[a.index].is_no_connect:
        return False
    if b.kind is ItemKind.PIN and gate.pins[b.index].is_no_connect:
        return False
    # A wire and a bus never join directly. Both sides must agree on bus-ness
    # (`sch_line.cpp:722-732`).
    if {a.kind, b.kind} == {ItemKind.WIRE, ItemKind.BUS}:
        return False

    # ``SCH_BUS_WIRE_ENTRY::ConnectionPropagatesTo`` (`sch_bus_entry.cpp:555-586`).
    #
    # **A bus entry never joins the bus in the graph.** Source's own words: there
    # is a connectivity change at that point, A[7..0] to A7. The bus reaches its
    # members by NAME through the bus member machinery, never through this edge.
    #
    # Without this clause every member wire shorts to the bus through its entry,
    # and therefore to every OTHER member wire. That collapses a whole bus into
    # one net, and it looked exactly like a bus member naming fault.
    if a.kind is ItemKind.BUS_WIRE_ENTRY:
        if b.kind is ItemKind.BUS:
            return False
        # A bus junction, meaning one a bus passes through (`:565-570`).
        if b.kind is ItemKind.JUNCTION and b.index in gate.bus_junctions:
            return False
        # A plain label whose own text is a bus label, landing on the same point
        # on the bus wire as this entry (`:573-578`). SCH_LABEL_T only, so a
        # global or hierarchical label is NOT covered.
        if b.kind is ItemKind.LABEL and b.index in gate.bus_labels:
            return False
        # Two bus entries never join each other (`:581-582`).
        if b.kind is ItemKind.BUS_WIRE_ENTRY:
            return False

    return True


def _on_segment(point: IUPoint, start: IUPoint, end: IUPoint) -> bool:
    """True when *point* lies strictly between the endpoints, colinear.

    Integer arithmetic only, so the test is exact. Endpoints are excluded, which
    matches ``GetBusesAndWires`` called with the ignore-endpoints flag.
    """
    if point in (start, end):
        return False
    px, py = point
    sx, sy = start
    ex, ey = end
    if (ex - sx) * (py - sy) != (ey - sy) * (px - sx):
        return False
    return min(sx, ex) <= px <= max(sx, ex) and min(sy, ey) <= py <= max(sy, ey)


def build_point_map(geo: SheetGeometry) -> dict[IUPoint, list[_Item]]:
    """Phase 2. Every item at every point it contributes.

    A wire contributes its two ENDPOINTS only, with no midpoints
    (`sch_line.cpp:716-719`). A label, a junction, a no-connect, and a sheet pin
    each contribute a single anchor. A pin contributes its transformed position.
    """
    buckets: dict[IUPoint, list[_Item]] = {}

    def push(point: IUPoint, item: _Item) -> None:
        buckets.setdefault(point, []).append(item)

    for i, (a, b) in enumerate(geo.wire_ends):
        push(a, _Item(ItemKind.WIRE, i))
        push(b, _Item(ItemKind.WIRE, i))
    for i, (a, b) in enumerate(geo.bus_ends):
        push(a, _Item(ItemKind.BUS, i))
        push(b, _Item(ItemKind.BUS, i))
    for i, (a, b) in enumerate(geo.bus_entries):
        push(a, _Item(ItemKind.BUS_WIRE_ENTRY, i))
        push(b, _Item(ItemKind.BUS_WIRE_ENTRY, i))
    for i, point in enumerate(geo.junctions):
        push(point, _Item(ItemKind.JUNCTION, i))
    for i, point in enumerate(geo.no_connects):
        push(point, _Item(ItemKind.NO_CONNECT, i))
    for i, pin in enumerate(geo.pins):
        push(pin.point, _Item(ItemKind.PIN, i))
    for i, (point, kind, _text) in enumerate(geo.labels):
        push(point, _Item(_LABEL_KIND.get(kind, ItemKind.LABEL), i))
    for i, (point, _name, _shape) in enumerate(geo.sheet_pins):
        push(point, _Item(ItemKind.SHEET_PIN, i))
    for i, (point, _text) in enumerate(geo.directive_labels):
        push(point, _Item(ItemKind.DIRECTIVE_LABEL, i))

    # A junction pulls in every wire or bus that passes THROUGH its point, which
    # is how a wire endpoint reaches the middle of an unsplit wire
    # (`connection_graph.cpp:1370-1381`).
    for point in geo.junctions:
        for i, (a, b) in enumerate(geo.wire_ends):
            if _on_segment(point, a, b):
                push(point, _Item(ItemKind.WIRE, i))
        for i, (a, b) in enumerate(geo.bus_ends):
            if _on_segment(point, a, b):
                push(point, _Item(ItemKind.BUS, i))

    # A label reaches a line it overlaps by TWO INDEPENDENT source paths, and a
    # port needs both. Each is documented on `_push_overlapping_lines`. Both apply
    # to every label kind, because both test SCH_LABEL_BASE, and to a directive
    # label, which derives from it. Neither applies to a sheet pin.
    partners = _exact_partner_points(geo)
    for point, _kind, _text in geo.labels:
        _push_overlapping_lines(geo, point, push, has_partner=point in partners)
    for point, _text in geo.directive_labels:
        _push_overlapping_lines(geo, point, push, has_partner=point in partners)

    return buckets


def _exact_partner_points(geo: SheetGeometry) -> frozenset[IUPoint]:
    """Points where a label finds an exact-position partner, blocking the line pass.

    Pass 1 of ``SCH_LABEL_BASE::UpdateDanglingState`` (`sch_label.cpp:1255-1283`)
    walks the dangling-end list at EXACTLY the label's own position and accepts
    four kinds: ``PIN_END``, ``LABEL_END``, ``SHEET_LABEL_END`` and
    ``NO_CONNECT_END``. The first one found clears the dangling flag and BREAKS,
    so neither line pass below it ever runs.

    A label kind counts only when a SECOND such item shares the point, because
    source skips the item that is asking (`:1262-1263`). One label alone at a
    point has no partner.
    """
    points: set[IUPoint] = set()
    points.update(pin.point for pin in geo.pins)
    points.update(point for point, _name, _shape in geo.sheet_pins)
    points.update(geo.no_connects)

    stacked: dict[IUPoint, int] = {}
    for point, _kind, _text in geo.labels:
        stacked[point] = stacked.get(point, 0) + 1
    for point, _text in geo.directive_labels:
        stacked[point] = stacked.get(point, 0) + 1
    points.update(point for point, count in stacked.items() if count > 1)

    return frozenset(points)


def _touches(point: IUPoint, start: IUPoint, end: IUPoint) -> bool:
    """``SCH_LINE::HitTest`` for a point, endpoints INCLUDED (`sch_line.cpp:733-745`)."""
    return point in (start, end) or _on_segment(point, start, end)


def _push_overlapping_lines(
    geo: SheetGeometry,
    point: IUPoint,
    push: Callable[[IUPoint, _Item], None],
    *,
    has_partner: bool,
) -> None:
    """Push the lines a label overlaps. TWO independent source paths, both ported.

    **Path one: the overlap case in phase 2** (`connection_graph.cpp:1346-1366`).
    When at least TWO lines hit-test the label's point, every one of them joins
    that point. An ENDPOINT counts, because ``HitTest`` returns true immediately
    for either endpoint (`sch_line.cpp:819-821`). Source calls the situation an
    ERC error, because there is no explicit junction, and connects it anyway.
    This path ignores dangling state entirely.

    **Path two: dangling-state resolution**
    (``SCH_LABEL_BASE::UpdateDanglingState``, `sch_label.cpp:1248-1348`). This is
    the path that was missing, and it was invisible because it does not live in
    the connection graph at all. ``Recalculate`` calls ``TestDanglingEnds`` after
    phase 2 and before phase 3 (`connection_graph.cpp:861`), under a comment
    saying exactly what it does: "UpdateDanglingState() also adds connected items
    for SCH_TEXT". Source's own words at the attachment site: "Add the line to
    the connected items, since it won't be picked up by a search of intersecting
    connection points".

    Path two runs in a strict precedence, and the order is the whole rule:

    1. An exact-position partner wins and STOPS everything (`:1255-1283`). See
       ``_exact_partner_points``. This is why two labels stacked at one interior
       point of a wire attach to each other and to NOTHING else, leaving the wire
       unnamed, while a single label at the same point names it.
    2. Otherwise the FIRST bus segment hit attaches, and nothing else
       (`:1287-1313`).
    3. Otherwise the FIRST wire segment hit attaches, and nothing else
       (`:1315-1342`).

    Both line passes use accuracy 1 with the source comment "We have rounding
    issues with an accuracy of 0", so an endpoint and a strict interior point
    both hit.

    Reading path one alone made this rule look like it could never name a
    mid-wire label, and a permissive stand-in was used instead. The stand-in
    happened to agree with path two on the common case of one overlapping line,
    which is why it scored well and hid its own absence for so long. Where it
    disagreed was precedence: it had none.

    See ``context/audit/RESOLVED-label-overlap-2026-09-03.md``.
    """
    wire_hits: list[_Item] = []
    bus_hits: list[_Item] = []
    for index, (a, b) in enumerate(geo.wire_ends):
        if _touches(point, a, b):
            wire_hits.append(_Item(ItemKind.WIRE, index))
    for index, (a, b) in enumerate(geo.bus_ends):
        if _touches(point, a, b):
            bus_hits.append(_Item(ItemKind.BUS, index))

    # Path one. Two or more hits and every one joins, whatever the dangling state.
    if len(wire_hits) + len(bus_hits) >= 2:
        for item in [*wire_hits, *bus_hits]:
            push(point, item)
        return

    # Path two, step 1.
    if has_partner:
        return

    # Path two, steps 2 and 3. A bus outranks a wire, and only the first hit
    # attaches. With fewer than two hits in total the choice is already unique,
    # so the ordering here is documentation of the source rule rather than a
    # decision this branch ever has to make.
    for item in [*bus_hits, *wire_hits][:1]:
        push(point, item)


def _bus_points(geo: SheetGeometry) -> Callable[[IUPoint], bool]:
    """``SCH_SCREEN::GetBus( point )``, the pre-scan at `:1391`.

    True when some bus line hit-tests *point*, which covers a bus endpoint and a
    point in the middle of a bus segment alike (`sch_screen.cpp:1287-1320` with
    the default ENTIRE_LENGTH_T).
    """

    def has_bus(point: IUPoint) -> bool:
        return any(_touches(point, a, b) for a, b in geo.bus_ends)

    return has_bus


def build_gate(geo: SheetGeometry) -> _Gate:
    """Precompute the layer-dependent facts the propagation gate needs."""
    has_bus = _bus_points(geo)
    bus_junctions = frozenset(index for index, point in enumerate(geo.junctions) if has_bus(point))
    bus_labels = frozenset(
        index
        for index, (_point, kind, text) in enumerate(geo.labels)
        if kind == "label" and is_bus_label(text)
    )
    return _Gate(pins=geo.pins, bus_junctions=bus_junctions, bus_labels=bus_labels)


def build_adjacency(
    buckets: dict[IUPoint, list[_Item]], geo: SheetGeometry
) -> dict[_Item, set[_Item]]:
    """Phase 2's pairing loop. Items sharing a point connect, gated both ways."""
    gate = build_gate(geo)
    has_bus = _bus_points(geo)
    adjacency: dict[_Item, set[_Item]] = {}

    for point, items in buckets.items():
        # The pre-scan for a bus at this location (`:1390-1391`).
        bus_here = has_bus(point)

        for item in items:
            adjacency.setdefault(item, set())

        for i, first in enumerate(items):
            for second in items[i + 1 :]:
                if first == second:
                    continue
                # ``bus_connection_ok`` (`:1456-1466`). At a point a bus passes
                # through, a bus entry connects ONLY to the bus. Source's reason:
                # if the user overlapped another net line with the entry's bus
                # end, that must not short the two together.
                if bus_here and (
                    (first.kind is ItemKind.BUS_WIRE_ENTRY and second.kind is not ItemKind.BUS)
                    or (second.kind is ItemKind.BUS_WIRE_ENTRY and first.kind is not ItemKind.BUS)
                ):
                    continue
                if _propagates(first, second, gate) and _propagates(second, first, gate):
                    adjacency[first].add(second)
                    adjacency[second].add(first)

    return adjacency


def _make_driver(
    item: _Item, geo: SheetGeometry, resolve_alias: AliasResolver | None = None
) -> Driver | None:
    """The driver an item contributes, or None when it cannot drive a name.

    A junction, a wire, a bus, a no-connect, and a bus entry all drive nothing.
    A directive label drives nothing either, which the rank table encodes by
    falling to the switch default.
    """
    if item.kind is ItemKind.PIN:
        pin = geo.pins[item.index]
        return Driver(
            kind=ItemKind.PIN,
            name=get_default_net_name(pin, force_no_connect=False),
            priority=driver_priority(
                ItemKind.PIN,
                is_global_power=pin.is_global_power,
                is_local_power=pin.is_local_power,
                excluded_from_board=pin.excluded_from_board,
                lib_reference=pin.lib_reference,
            ),
            pin=pin,
        )
    if item.kind in (ItemKind.LABEL, ItemKind.GLOBAL_LABEL, ItemKind.HIER_LABEL):
        _point, _kind, text = geo.labels[item.index]
        # Classify the label text through the bus grammar, which is what source
        # does when it sets a connection's TYPE from the label
        # (``ConfigureFromLabel``, `sch_connection.cpp:140-213`). Without this a
        # driver never learns it is a bus, so phase 7 finds no bus label and
        # creates no member subgraph. That was the root cause of every remaining
        # split and over-merge: a bus group behaved as one net.
        classified = configure_from_label(text, resolve_alias=resolve_alias)
        return Driver(
            kind=item.kind,
            name=escape_netname(text),
            priority=driver_priority(item.kind, is_global_power=False, is_local_power=False),
            shown_name=text,
            bus_members=frozenset(m.full_local_name for m in classified.members)
            if classified.is_bus
            else frozenset(),
        )
    if item.kind is ItemKind.SHEET_PIN:
        _point, name, shape = geo.sheet_pins[item.index]
        return Driver(
            kind=ItemKind.SHEET_PIN,
            name=escape_netname(name),
            priority=driver_priority(
                ItemKind.SHEET_PIN, is_global_power=False, is_local_power=False
            ),
            shown_name=name,
            # `sa->GetShape() == LABEL_FLAG_SHAPE::L_OUTPUT` (`:241-244`). Only
            # an output shape decides the contest; every other pair of differing
            # shapes falls through to the name comparison.
            is_output_shape=shape == "output",
        )
    return None


def build_subgraphs(
    geo: SheetGeometry,
    sheet_path: tuple[str, ...] = (),
    *,
    resolve_alias: AliasResolver | None = None,
    source_items: dict[tuple[ItemKind, int], str] | None = None,
    retain_undriven: bool = False,
) -> list[Subgraph]:
    """Phase 3. Flood the adjacency into connected components.

    A standard connected-components walk. The audit confirms the resulting SET of
    items per component is order-independent, because it is reachability over a
    fixed relation. Only the discarded net code depends on order.
    """
    buckets = build_point_map(geo)
    adjacency = build_adjacency(buckets, geo)

    seen: set[_Item] = set()
    out: list[Subgraph] = []

    for seed in adjacency:
        if seed in seen:
            continue
        component: list[_Item] = []
        queue = [seed]
        seen.add(seed)
        while queue:
            current = queue.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)

        subgraph = Subgraph(sheet_path=sheet_path)
        # Phase 3 marks every subgraph it creates dirty (`:1575`).
        subgraph.dirty = True
        for item in component:
            identity = (source_items or {}).get((item.kind, item.index))
            if identity is not None:
                subgraph.source_items.add(identity)
            if item.kind is ItemKind.PIN:
                # A no-connect pin IS a member. It is isolated from other items,
                # not excluded from its own subgraph. Dropping it here loses the
                # single-pin net KiCad emits for it.
                subgraph.pins.append(geo.pins[item.index])
            elif item.kind is ItemKind.NO_CONNECT:
                # Recorded on the subgraph and never a driver (`:1547-1548`).
                subgraph.no_connect = True
            elif item.kind is ItemKind.HIER_LABEL:
                subgraph.hier_ports.append(geo.labels[item.index][2])
            elif item.kind is ItemKind.SHEET_PIN:
                pin_name = geo.sheet_pins[item.index][1]
                subgraph.hier_pins.append(pin_name)
                # Index directly. The two lists are appended together so they are
                # parallel. An earlier version matched by NAME and stopped at the
                # first hit, which recorded one child when a parent subgraph holds
                # several sheet pins, or when sibling sheets share a pin name.
                # Five sibling sheets with a SYNC_AK pin then joined only one.
                if item.index < len(geo.sheet_pin_children):
                    _pt, name, child = geo.sheet_pin_children[item.index]
                    subgraph.hier_pin_children.append((name, child))
            driver = _make_driver(item, geo, resolve_alias)
            if driver is not None:
                subgraph.drivers.append(driver)
        if retain_undriven or subgraph.pins or subgraph.drivers:
            out.append(subgraph)
    return out
