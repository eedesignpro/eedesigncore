"""Canonical save order for the board-scoped sibling streams.

``format( const BOARD* )`` builds seven sorted ``std::set`` containers before it
emits anything (``pcb_io_kicad_sexpr.cpp:803-816``), so stored order never
reaches the file. This module ports those seven comparators. The shared and
footprint-scoped half lives in ``common/pcb_family/ordering.py``, which this
module reuses; the split keeps ``common/`` free of ``pcb/`` imports (ADR-057).
Audit: ``context/audit/save-canonicalization-sweep-2026-08-13.md`` section 2.2.

Three notes govern this half of the port.

*Return type.* ``BOARD::cmp_drawings`` delegates to ``Compare`` methods that
return an ``int`` DIFFERENCE (``board.cpp:3434-3471``), so the 32-bit wrap of
:func:`eedesigncore.common.kimath.int32` applies here. The other six
comparators are strict-weak-ordering bools and need no wrap.

*Net codes.* ``PCB_TRACK::cmp_tracks`` keys on the net CODE
(``pcb_track.cpp:2720-2721``), and KiCad 10 writes no board net table (sweep
2.7). A code is assigned when the parser first meets a net NAME, counting up
from the unconnected net 0 (``netinfo_list.cpp:40-46, 149-176``). So
:func:`derive_net_codes` replays that walk over the STORED model, which is the
file order for a loaded board.

*Targets.* Our model keeps ``(target …)`` in its own stream while KiCad holds it
in the drawings list (sweep OQ-A). ``PCB_TARGET_T`` is 24, above every other
drawing type in ``KICAD_T`` (``typeinfo.h:88-107``), so emitting the target
stream after the drawing stream is exactly the sorted order.
"""

from __future__ import annotations

from functools import cmp_to_key
from typing import Any

from eedesigncore.common.kimath import int32
from eedesigncore.common.layers import is_copper_layer_name
from eedesigncore.common.models import Color, PtsArc
from eedesigncore.common.ordering import FILL_RANK, LINE_STYLE_RANK
from eedesigncore.common.pcb_family.board_items import Barcode, Table, Zone
from eedesigncore.common.pcb_family.ordering import (
    TYPE_RANK_ARC,
    TYPE_RANK_SHAPE,
    TYPE_RANK_TABLE,
    TYPE_RANK_TEXT,
    TYPE_RANK_TEXTBOX,
    TYPE_RANK_TRACE,
    TYPE_RANK_VIA,
    compare_by_layer_set_then_uuid,
    compare_uuid,
    drawing_type_rank,
    group_layer_seq,
    has_arc_element,
    item_layer_ordinal,
    item_uuid,
    layer_ordinal,
    layer_set_seq,
    point_iu,
    shape_kind,
    to_iu,
    zone_layer_seq,
)
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbGroup,
    PcbPoly,
)
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import (
    Board,
    PcbGenerator,
    PcbTarget,
    TrackArc,
    TrackItem,
    Via,
)

# ``EDA_SHAPE::Compare`` rounding tolerance, in internal units
# (``common/eda_shape.cpp:2451``).
_SHAPE_EPSILON = 2

# ``GR_TEXT_H_ALIGN_T`` and ``GR_TEXT_V_ALIGN_T``
# (``include/font/text_attributes.h:42-57``).
_H_ALIGN_RANK = {"left": -1, "center": 0, "right": 1}
_V_ALIGN_RANK = {"top": -1, "center": 0, "bottom": 1}

F_CU = 0
B_CU = 2
F_MASK = 1
B_MASK = 3

_TRACK_TYPE_RANK = {"segment": TYPE_RANK_TRACE, "via": TYPE_RANK_VIA, "arc": TYPE_RANK_ARC}


# ---------------------------------------------------------------------------
# Difference helpers, for the ``int``-returning ``Compare`` chain
# ---------------------------------------------------------------------------


def _test(first: int, second: int) -> int:
    """The ``TEST`` macro of ``EDA_SHAPE::Compare`` (``eda_shape.cpp:2453``)."""
    return int32(first - second) if first != second else 0


def _test_e(first: int, second: int) -> int:
    """The ``TEST_E`` macro, which ignores a difference of two internal units."""
    return int32(first - second) if abs(first - second) > _SHAPE_EPSILON else 0


def _test_pt(first: tuple[int, int], second: tuple[int, int]) -> int:
    result = _test_e(first[0], second[0])
    if result:
        return result
    return _test_e(first[1], second[1])


def _cmp_str(first: str, second: str) -> int:
    if first == second:
        return 0
    return -1 if first < second else 1


def _cmp_num(first: float, second: float) -> int:
    if first == second:
        return 0
    return -1 if first < second else 1


# ---------------------------------------------------------------------------
# EDA_SHAPE::Compare (``common/eda_shape.cpp:2449-2491``)
# ---------------------------------------------------------------------------


def _points(item: Any) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for element in getattr(item, "pts", []):
        if isinstance(element, PtsArc):
            points.extend([element.start, element.mid, element.end])
        else:
            points.append(element)
    return points


def _m_start(item: Any) -> tuple[int, int]:
    """Return ``EDA_SHAPE::m_start`` in internal units.

    ``SetPolyPoints`` never touches ``m_start``
    (``common/eda_shape.cpp:1947-1954``), so a polygon reports the origin here and
    discriminates on its vertices later. A circle stores its centre in
    ``m_start``.
    """
    if isinstance(item, PcbCircle):
        return point_iu(item.center)
    if isinstance(item, PcbPoly):
        return (0, 0)
    if isinstance(item, PcbCurve):
        points = _points(item)
        return point_iu(points[0]) if points else (0, 0)
    return point_iu(getattr(item, "start", None) or (0.0, 0.0))


def _m_end(item: Any) -> tuple[int, int]:
    """Return ``EDA_SHAPE::m_end`` in internal units."""
    if isinstance(item, PcbPoly):
        return (0, 0)
    if isinstance(item, PcbCurve):
        points = _points(item)
        return point_iu(points[3]) if len(points) > 3 else (0, 0)
    return point_iu(getattr(item, "end", None) or (0.0, 0.0))


def compare_eda_shape(first: Any, second: Any) -> int:
    """Port of ``EDA_SHAPE::Compare`` (``common/eda_shape.cpp:2449-2491``).

    Note the key order differs from ``FOOTPRINT::cmp_drawings``: start and end
    come BEFORE the shape kind here, and a polygon does not skip them. The
    flattened bezier point list is derived state the file never stores, so it is
    not compared.
    """
    result = _test_pt(_m_start(first), _m_start(second))
    if result:
        return result

    result = _test_pt(_m_end(first), _m_end(second))
    if result:
        return result

    kind_first = shape_kind(first)
    kind_second = shape_kind(second)
    result = _test(kind_first, kind_second)
    if result:
        return result

    if isinstance(first, PcbArc) and isinstance(second, PcbArc):
        result = _test_pt(_arc_mid(first), _arc_mid(second))
        if result:
            return result
    elif isinstance(first, PcbCurve) and isinstance(second, PcbCurve):
        points_first = _points(first)
        points_second = _points(second)
        result = _test_pt(point_iu(points_first[1]), point_iu(points_second[1]))
        if result:
            return result
        result = _test_pt(point_iu(points_first[2]), point_iu(points_second[2]))
        if result:
            return result
    elif isinstance(first, PcbPoly) and isinstance(second, PcbPoly):
        if has_arc_element(first) or has_arc_element(second):
            return 0
        vertices_first = _points(first)
        vertices_second = _points(second)
        result = _test(len(vertices_first), len(vertices_second))
        if result:
            return result
        for vertex_first, vertex_second in zip(vertices_first, vertices_second, strict=True):
            result = _test_pt(point_iu(vertex_first), point_iu(vertex_second))
            if result:
                return result

    stroke_first = getattr(first, "stroke", None)
    stroke_second = getattr(second, "stroke", None)
    if stroke_first is None or stroke_second is None:
        return 0

    result = _test_e(to_iu(stroke_first.width), to_iu(stroke_second.width))
    if result:
        return result

    result = _test(LINE_STYLE_RANK[stroke_first.type], LINE_STYLE_RANK[stroke_second.type])
    if result:
        return result

    fill_first = getattr(first, "fill", None)
    fill_second = getattr(second, "fill", None)
    if fill_first is None or fill_second is None:
        return 0
    return _test(FILL_RANK[fill_first.type], FILL_RANK[fill_second.type])


def _arc_mid(arc: PcbArc) -> tuple[int, int]:
    """Return ``GetArcMid()``, the mid point of the stored arc.

    ``EDA_SHAPE::SetArcGeometry`` derives the centre from the three file points
    (``common/eda_shape.cpp:1185``) and recomputes the mid from it, which returns
    the file mid for any arc whose winding already matches. ADR-056 records the
    winding gap that the remaining arcs carry.
    """
    return point_iu(arc.mid)


# ---------------------------------------------------------------------------
# EDA_TEXT::Compare (``common/eda_text.cpp:1201-1222``)
# ---------------------------------------------------------------------------


def _cmp_color(first: Color | None, second: Color | None) -> int:
    """Port of ``COLOR4D::Compare`` (``common/gal/color4d.cpp:610-630``)."""
    if first is None and second is None:
        return 0
    left = first or Color(r=0, g=0, b=0, a=0.0)
    right = second or Color(r=0, g=0, b=0, a=0.0)
    for value_first, value_second in (
        (left.r, right.r),
        (left.g, right.g),
        (left.b, right.b),
        (left.a, right.a),
    ):
        result = _cmp_num(value_first, value_second)
        if result:
            return result
    return 0


def _text_angle(item: Any) -> float:
    angle = getattr(item, "angle", None)
    return 0.0 if angle is None else float(angle)


def compare_eda_text(first: Any, second: Any) -> int:
    """Port of ``EDA_TEXT::Compare`` (``common/eda_text.cpp:1201-1222``).

    ``TEXT_ATTRIBUTES::Compare`` runs first
    (``common/font/text_attributes.cpp:44-103``), then the position, then the font
    name again, then the text. Underline and multiline are not in the PCB
    grammar, so they never decide.
    """
    attributes_first = getattr(first, "attributes", None)
    attributes_second = getattr(second, "attributes", None)
    if attributes_first is None or attributes_second is None:
        return 0

    font_first = attributes_first.font_face or ""
    font_second = attributes_second.font_face or ""
    result = _cmp_str(font_first, font_second)
    if result:
        return result

    # Our ``font_size`` is ``(height, width)``, because ``EDA_TEXT::Format``
    # emits ``(size height width)`` (``common/eda_text.cpp:1071-1072``), while
    # ``m_Size`` is ``(x, y)``. The tuple is swapped here.
    result = _test(to_iu(attributes_first.font_size[1]), to_iu(attributes_second.font_size[1]))
    if result:
        return result
    result = _test(to_iu(attributes_first.font_size[0]), to_iu(attributes_second.font_size[0]))
    if result:
        return result

    result = _test(
        to_iu(attributes_first.font_thickness or 0.0),
        to_iu(attributes_second.font_thickness or 0.0),
    )
    if result:
        return result

    result = _cmp_num(_text_angle(first), _text_angle(second))
    if result:
        return result

    result = _cmp_num(
        attributes_first.line_spacing if attributes_first.line_spacing is not None else 1.0,
        attributes_second.line_spacing if attributes_second.line_spacing is not None else 1.0,
    )
    if result:
        return result

    result = _test(
        _H_ALIGN_RANK[attributes_first.justify_h.value],
        _H_ALIGN_RANK[attributes_second.justify_h.value],
    )
    if result:
        return result

    result = _test(
        _V_ALIGN_RANK[attributes_first.justify_v.value],
        _V_ALIGN_RANK[attributes_second.justify_v.value],
    )
    if result:
        return result

    result = _test(int(attributes_first.italic), int(attributes_second.italic))
    if result:
        return result

    result = _test(int(attributes_first.bold), int(attributes_second.bold))
    if result:
        return result

    result = _cmp_color(attributes_first.color, attributes_second.color)
    if result:
        return result

    result = _test(int(attributes_first.mirror), int(attributes_second.mirror))
    if result:
        return result

    position_first = point_iu(_text_position(first))
    position_second = point_iu(_text_position(second))
    result = _test(position_first[0], position_second[0])
    if result:
        return result
    result = _test(position_first[1], position_second[1])
    if result:
        return result

    result = _cmp_str(font_first, font_second)
    if result:
        return result

    return _cmp_str(getattr(first, "content", ""), getattr(second, "content", ""))


def _text_position(item: Any) -> tuple[float, float]:
    """Return ``EDA_TEXT::m_pos``, which a text box stores as its rectangle start."""
    position: tuple[float, float] | None = getattr(item, "position", None)
    if position is not None:
        return position
    start: tuple[float, float] | None = getattr(item, "start", None)
    return start or (0.0, 0.0)


# ---------------------------------------------------------------------------
# PCB_TABLE::Compare and PCB_BARCODE::Compare
# ---------------------------------------------------------------------------


def compare_tables(first: Table, second: Table) -> int:
    """Port of ``PCB_TABLE::Compare`` (``pcbnew/pcb_table.cpp:695-733``)."""
    result = _test(len(first.cells), len(second.cells))
    if result:
        return result

    result = _test(first.column_count, second.column_count)
    if result:
        return result

    for width_first, width_second in zip(first.column_widths, second.column_widths, strict=False):
        result = _test(to_iu(width_first), to_iu(width_second))
        if result:
            return result

    for height_first, height_second in zip(first.row_heights, second.row_heights, strict=False):
        result = _test(to_iu(height_first), to_iu(height_second))
        if result:
            return result

    for cell_first, cell_second in zip(first.cells, second.cells, strict=False):
        result = compare_eda_shape(cell_first, cell_second)
        if result:
            return result
        result = compare_eda_text(cell_first, cell_second)
        if result:
            return result

    return 0


def compare_barcodes(first: Barcode, second: Barcode) -> int:
    """Port of ``PCB_BARCODE::Compare`` (``pcbnew/pcb_barcode.cpp:821-854``)."""
    position_first = point_iu(first.position)
    position_second = point_iu(second.position)
    result = _test(position_first[0], position_second[0])
    if result:
        return result
    result = _test(position_first[1], position_second[1])
    if result:
        return result

    result = _cmp_str(first.text, second.text)
    if result:
        return result

    result = _test(to_iu(first.size[0]), to_iu(second.size[0]))
    if result:
        return result
    result = _test(to_iu(first.size[1]), to_iu(second.size[1]))
    if result:
        return result

    result = _test(to_iu(first.text_height), to_iu(second.text_height))
    if result:
        return result

    result = _cmp_str(first.barcode_type.value, second.barcode_type.value)
    if result:
        return result

    return _cmp_num(first.angle, second.angle)


# ---------------------------------------------------------------------------
# BOARD::cmp_drawings (``pcbnew/board.cpp:3426-3474``)
# ---------------------------------------------------------------------------


def compare_board_drawings(first: Any, second: Any) -> int:
    """Compare two board drawings as ``BOARD::cmp_drawings`` orders them.

    Key order: type, layer, then the per-type ``Compare``. A shape, text, text
    box, table or barcode RETURNS its ``Compare`` verdict, so the UUID tail at
    ``board.cpp:3473`` is reachable only for the remaining types.
    """
    rank_first = drawing_type_rank(first)
    rank_second = drawing_type_rank(second)
    if rank_first != rank_second:
        return -1 if rank_first < rank_second else 1

    layer_first = item_layer_ordinal(first)
    layer_second = item_layer_ordinal(second)
    if layer_first != layer_second:
        return -1 if layer_first < layer_second else 1

    if rank_first == TYPE_RANK_SHAPE:
        return compare_eda_shape(first, second)
    if rank_first == TYPE_RANK_TEXT:
        return compare_eda_text(first, second)
    if rank_first == TYPE_RANK_TEXTBOX:
        result = compare_eda_shape(first, second)
        if result:
            return result
        return compare_eda_text(first, second)
    if rank_first == TYPE_RANK_TABLE and isinstance(first, Table) and isinstance(second, Table):
        return compare_tables(first, second)
    if isinstance(first, Barcode) and isinstance(second, Barcode):
        return compare_barcodes(first, second)

    return compare_uuid(item_uuid(first), item_uuid(second))


# ---------------------------------------------------------------------------
# PCB_TRACK::cmp_tracks (``pcbnew/pcb_track.cpp:2718-2733``)
# ---------------------------------------------------------------------------


def _net_name(item: Any) -> str:
    """Return the net NAME an item references, or the empty unconnected name."""
    name = getattr(item, "net", None)
    if isinstance(name, str):
        return name
    return getattr(item, "net_name", None) or ""


def derive_net_codes(board: Board) -> dict[str, int]:
    """Return the net-name-to-rank map that orders the track stream.

    ``PCB_TRACK::cmp_tracks`` keys on ``GetNetCode()``
    (``pcbnew/pcb_track.cpp:2720-2721``), and a net code is a RUNTIME number: the
    board file never stores one in KiCad 10 (sweep 2.7), and the number an
    in-memory board carries comes from its whole edit history, not from a rule a
    reader can replay.

    The saved track stream nevertheless reveals the order, because it IS sorted
    by that code. Distinct net names therefore first appear in the stream in
    ascending code order, so their first-appearance rank reproduces the ordering
    exactly. A name with no track never decides a track comparison.

    The unconnected net keeps rank 0. KiCad omits ``(net …)`` for it
    (``pcb_io_kicad_sexpr.cpp:2855``), so an unnamed track leads the stream, and
    seeding the map keeps that true for a track added later. A net name this
    board has never routed also ranks 0, which sorts it first rather than
    guessing a code.
    """
    codes: dict[str, int] = {"": 0}

    for track in board.tracks:
        name = _net_name(track)
        if name and name not in codes:
            codes[name] = len(codes)

    return codes


def _track_layer(track: TrackItem) -> int:
    """Return ``PCB_TRACK::GetLayer()``.

    ``PCB_VIA::GetLayer`` returns the drill START layer
    (``pcbnew/pcb_track.cpp:1527-1530``), which is the first name of the via
    ``(layers …)`` pair.
    """
    if isinstance(track, Via):
        return layer_ordinal(track.layers[0])
    return layer_ordinal(track.layer)


def _track_type_rank(track: TrackItem) -> int:
    if isinstance(track, Via):
        return _TRACK_TYPE_RANK["via"]
    if isinstance(track, TrackArc):
        return _TRACK_TYPE_RANK["arc"]
    return _TRACK_TYPE_RANK["segment"]


def compare_tracks(first: TrackItem, second: TrackItem, net_codes: dict[str, int]) -> int:
    """Compare two track-stream items: net code, layer, type, then UUID.

    ``PCB_ARC_T`` is 15 and ``PCB_VIA_T`` is 14, so a via precedes an arc on the
    same net and layer (``include/core/typeinfo.h:97-98``).
    """
    code_first = net_codes.get(_net_name(first), 0)
    code_second = net_codes.get(_net_name(second), 0)
    if code_first != code_second:
        return -1 if code_first < code_second else 1

    layer_first = _track_layer(first)
    layer_second = _track_layer(second)
    if layer_first != layer_second:
        return -1 if layer_first < layer_second else 1

    rank_first = _track_type_rank(first)
    rank_second = _track_type_rank(second)
    if rank_first != rank_second:
        return -1 if rank_first < rank_second else 1

    return compare_uuid(item_uuid(first), item_uuid(second))


# ---------------------------------------------------------------------------
# The board item index, for group layer-set resolution
# ---------------------------------------------------------------------------


def copper_stack(board: Board) -> tuple[int, ...]:
    """Return the board's copper layer ids in stack order, front to back.

    ``(layers …)`` lists the copper rows in ``LSET::CuStack()`` order
    (``pcb_io_kicad_sexpr.cpp:664-674``), which is F.Cu, the inner layers, then
    B.Cu. ``PCB_VIA::GetLayerSet`` needs that sequence and its length.
    """
    return tuple(row.ordinal for row in board.layers if is_copper_layer_name(row.canonical_name))


def _mask_extended(layers: tuple[int, ...], has_solder_mask: bool) -> tuple[int, ...]:
    """Add the matching mask layer, as ``PCB_SHAPE`` and ``PCB_TRACK`` do.

    Source: ``pcbnew/pcb_shape.cpp:239-253`` and ``pcbnew/pcb_track.cpp:1552-1565``.
    The parser raises ``m_hasSolderMask`` when it reads a
    ``(solder_mask_margin …)`` token, so our optional field is the same flag.
    """
    if not has_solder_mask:
        return layers
    extended = set(layers)
    if F_CU in extended:
        extended.add(F_MASK)
    if B_CU in extended:
        extended.add(B_MASK)
    return tuple(sorted(extended))


def via_layer_seq(via: Via, stack: tuple[int, ...]) -> tuple[int, ...]:
    """Return ``PCB_VIA::GetLayerSet()`` (``pcbnew/pcb_track.cpp:1568-1594``).

    A THROUGH via covers EVERY copper layer the board enables, not just the two
    names its ``(layers …)`` pair lists. A blind, buried or micro via covers the
    stack range between its two drill layers.
    """
    if not stack:
        return layer_set_seq(list(via.layers))
    if via.via_type == "through":
        return tuple(sorted(stack))

    start = layer_ordinal(via.layers[0])
    end = layer_ordinal(via.layers[1])
    if start not in stack or end not in stack:
        return layer_set_seq(list(via.layers))
    first, last = sorted((stack.index(start), stack.index(end)))
    return tuple(sorted(stack[first : last + 1]))


def _track_layer_seq(track: TrackItem, stack: tuple[int, ...]) -> tuple[int, ...]:
    if isinstance(track, Via):
        return via_layer_seq(track, stack)
    return _mask_extended((layer_ordinal(track.layer),), track.solder_mask_margin is not None)


def board_member_layer_sets(board: Board) -> dict[str, tuple[int, ...]]:
    """Index every board item by UUID, mapping it to its ``LSET::Seq()``.

    A group's layer key is the union over its members
    (``pcbnew/pcb_group.cpp:329-337``), and a board group may hold a footprint or
    one of its children, so the index covers both levels. A member this library
    cannot resolve contributes nothing, which under-discriminates rather than
    misorders.
    """
    layer_sets: dict[str, tuple[int, ...]] = {}
    stack = copper_stack(board)

    def note(item: Any, seq: tuple[int, ...]) -> None:
        uuid = item_uuid(item)
        if uuid:
            layer_sets[uuid] = seq

    for footprint in board.footprints:
        note(footprint, (layer_ordinal(footprint.layer),))
        for drawing in footprint.drawings:
            note(
                drawing,
                _mask_extended(
                    (item_layer_ordinal(drawing),),
                    getattr(drawing, "solder_mask_margin", None) is not None,
                ),
            )
        for point in footprint.points:
            note(point, (layer_ordinal(point.layer),))
        for pad in footprint.pads:
            note(pad, layer_set_seq(pad.layers))
        for zone in footprint.zones:
            note(zone, zone_layer_seq(zone))

    for drawing in [*board.drawings, *board.trailing_drawings]:
        note(
            drawing,
            _mask_extended(
                (item_layer_ordinal(drawing),),
                getattr(drawing, "solder_mask_margin", None) is not None,
            ),
        )
    for point in board.points:
        note(point, (layer_ordinal(point.layer),))
    for track in board.tracks:
        note(track, _track_layer_seq(track, stack))
    for zone in board.zones:
        note(zone, zone_layer_seq(zone))
    for target in board.targets:
        note(target, (layer_ordinal(target.layer),))

    # Groups and generators resolve last, because their key is a member union.
    for group in board.groups:
        note(group, group_layer_seq(group, layer_sets))
    for generator in board.generators:
        note(generator, _generator_layer_seq(generator, layer_sets))

    return layer_sets


def _generator_layer_seq(
    generator: PcbGenerator, layer_sets: dict[str, tuple[int, ...]]
) -> tuple[int, ...]:
    """Return ``PCB_GENERATOR::GetLayerSet()`` (``pcbnew/pcb_generator.cpp:134-137``).

    It is the group member union united with the generator's own layer.
    """
    ordinals = set(group_layer_seq(PcbGroup(members=generator.members), layer_sets))
    if generator.layer:
        ordinals.add(layer_ordinal(generator.layer))
    return tuple(sorted(ordinals))


# ---------------------------------------------------------------------------
# Public sort entry points
# ---------------------------------------------------------------------------


def sort_board_footprints(footprints: list[Footprint]) -> list[Footprint]:
    """Return the board footprint stream in KiCad save order.

    ``BOARD_ITEM::ptr_cmp`` keys on type, layer set and UUID
    (``pcbnew/board_item.cpp:314-326``). A ``FOOTPRINT`` does not override
    ``GetLayerSet``, so its set is the single ``F.Cu`` or ``B.Cu`` it sits on
    (``include/board_item.h:257-263``). Sweep discriminator 2 shows twelve
    footprints on ``F.Cu`` ordering by UUID alone.
    """

    def compare(first: Footprint, second: Footprint) -> int:
        return compare_by_layer_set_then_uuid(
            (layer_ordinal(first.layer),),
            first.uuid or "",
            (layer_ordinal(second.layer),),
            second.uuid or "",
        )

    return sorted(footprints, key=cmp_to_key(compare))


def sort_board_drawings(drawings: list[Any]) -> list[Any]:
    """Return the board drawing stream in KiCad save order."""
    return sorted(drawings, key=cmp_to_key(compare_board_drawings))


def sort_board_targets(targets: list[PcbTarget]) -> list[PcbTarget]:
    """Return the ``(target …)`` stream in KiCad save order.

    A target takes the default branch of ``BOARD::cmp_drawings``, so layer then
    UUID decide. ``PCB_TARGET_T`` outranks every other drawing type, so this
    stream belongs after the drawing stream (sweep OQ-A).
    """

    def compare(first: PcbTarget, second: PcbTarget) -> int:
        layer_first = layer_ordinal(first.layer)
        layer_second = layer_ordinal(second.layer)
        if layer_first != layer_second:
            return -1 if layer_first < layer_second else 1
        return compare_uuid(first.uuid or "", second.uuid or "")

    return sorted(targets, key=cmp_to_key(compare))


def sort_board_tracks(tracks: list[TrackItem], net_codes: dict[str, int]) -> list[TrackItem]:
    """Return the track, arc and via stream in KiCad save order."""

    def compare(first: TrackItem, second: TrackItem) -> int:
        return compare_tracks(first, second, net_codes)

    return sorted(tracks, key=cmp_to_key(compare))


def sort_board_zones(zones: list[Zone]) -> list[Zone]:
    """Return the board zone stream in KiCad save order.

    A board zone goes through ``BOARD_ITEM::ptr_cmp``
    (``pcb_io_kicad_sexpr.cpp:811-812``), NOT through ``FOOTPRINT::cmp_zones``.
    Priority and outline geometry are not keys here.
    """

    def compare(first: Zone, second: Zone) -> int:
        return compare_by_layer_set_then_uuid(
            zone_layer_seq(first), item_uuid(first), zone_layer_seq(second), item_uuid(second)
        )

    return sorted(zones, key=cmp_to_key(compare))


def sort_board_groups(
    groups: list[PcbGroup], layer_sets: dict[str, tuple[int, ...]]
) -> list[PcbGroup]:
    """Return the board group stream in KiCad save order."""

    def compare(first: PcbGroup, second: PcbGroup) -> int:
        return compare_by_layer_set_then_uuid(
            group_layer_seq(first, layer_sets),
            item_uuid(first),
            group_layer_seq(second, layer_sets),
            item_uuid(second),
        )

    return sorted(groups, key=cmp_to_key(compare))


def sort_board_generators(
    generators: list[PcbGenerator], layer_sets: dict[str, tuple[int, ...]]
) -> list[PcbGenerator]:
    """Return the ``(generated …)`` stream in KiCad save order."""

    def compare(first: PcbGenerator, second: PcbGenerator) -> int:
        return compare_by_layer_set_then_uuid(
            _generator_layer_seq(first, layer_sets),
            first.uuid or "",
            _generator_layer_seq(second, layer_sets),
            second.uuid or "",
        )

    return sorted(generators, key=cmp_to_key(compare))


__all__ = [
    "board_member_layer_sets",
    "compare_barcodes",
    "compare_board_drawings",
    "compare_eda_shape",
    "compare_eda_text",
    "compare_tables",
    "compare_tracks",
    "derive_net_codes",
    "sort_board_drawings",
    "sort_board_footprints",
    "sort_board_generators",
    "sort_board_groups",
    "sort_board_targets",
    "sort_board_tracks",
    "sort_board_zones",
]
