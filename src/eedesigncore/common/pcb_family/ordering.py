"""Canonical save order for the PCB-family sibling streams.

``PCB_IO_KICAD_SEXPR::format`` builds twelve sorted ``std::set`` containers before
it emits anything: seven for a board (``pcb_io_kicad_sexpr.cpp:803-816``) and five
for a footprint (``:1423-1434``). In-memory order never reaches the file. This
module ports the footprint-scoped half of that rule, plus the primitives the
board-scoped half in ``pcb/ordering.py`` reuses. Audit:
``context/audit/save-canonicalization-sweep-2026-08-13.md`` section 2.

Four traps govern the port.

*Return type.* Unlike the symbol comparators, the footprint comparators return a
BOOL from a strict weak ordering, not an ``int`` difference
(``pcbnew/footprint.cpp:4316``, ``:4426``, ``:4527``). No 32-bit wrap is reachable
in them. The board drawing comparator DOES delegate to ``int``-returning
``Compare`` methods, so ``pcb/ordering.py`` wraps those.

*Coordinates.* A PCB internal unit is 1 nm, and the PCB writer never negates Y.
But a footprint child is stored in ABSOLUTE board coordinates and written
RELATIVE to its parent (``pcb_io_kicad_sexpr.cpp:466-476``, parser ``:3609-3613``).
``FOOTPRINT::cmp_drawings`` therefore compares absolute coordinates, so
:class:`FootprintFrame` rebuilds them from the footprint ``(at …)``. A standalone
``.kicad_mod`` sits at the origin with no rotation, so the transform is the
identity there. Pads are the exception: ``cmp_pads`` reads
``GetFPRelativePosition`` (``footprint.cpp:4431``), which is the file value
verbatim. Points and zone outlines are written unrotated too
(``pcb_io_kicad_sexpr.cpp:1149-1152``, ``:3057-3066``), so they need no transform.

*Ties.* Every comparator ends on a runtime pointer compare that no file can
carry. Every sort here is STABLE, so tied items keep their encounter order.

*Under-discrimination is safe.* A key this port cannot represent faithfully is
left out rather than guessed. A missing tail key turns a comparison into a tie,
and a stable sort then preserves the KiCad-written order. A WRONG key would
reorder it.
"""

from __future__ import annotations

from functools import cmp_to_key
from typing import TYPE_CHECKING, Any

from eedesigncore.common.kimath import calc_arc_center_iu, kiround
from eedesigncore.common.layers import (
    LAYER_NAME_TO_ORDINAL,
    LAYER_WILDCARD_ORDINALS,
)
from eedesigncore.common.models import Property, PtsArc
from eedesigncore.common.ordering import str_num_cmp
from eedesigncore.common.pcb_family.board_items import (
    Barcode,
    Dimension,
    DimensionType,
    ReferenceImage,
    Table,
    Zone,
)
from eedesigncore.common.pcb_family.footprint_scoped import Variant, VariantField
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbGroup,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
    PcbTextBox,
)

if TYPE_CHECKING:  # pragma: no cover - annotations only, keeps common/ free of mod/
    from eedesigncore.mod.models import Pad

# PCB internal unit: 1 IU = 1 nm (``include/base_units.h``, ``pcbIUScale``).
PCB_IU_PER_MM = 1e6

# ``KICAD_T`` ordinals (``include/core/typeinfo.h:81-113``). Only the RELATIVE
# order matters, so the literal enum positions are kept for auditability.
TYPE_RANK_FOOTPRINT = 3
TYPE_RANK_PAD = 4
TYPE_RANK_SHAPE = 5
TYPE_RANK_REFERENCE_IMAGE = 6
TYPE_RANK_FIELD = 7
TYPE_RANK_GENERATOR = 8
TYPE_RANK_TEXT = 9
TYPE_RANK_TEXTBOX = 10
TYPE_RANK_TABLE = 11
TYPE_RANK_TRACE = 13
TYPE_RANK_VIA = 14
TYPE_RANK_ARC = 15
TYPE_RANK_BARCODE = 18
TYPE_RANK_DIM_ALIGNED = 19
TYPE_RANK_DIM_LEADER = 20
TYPE_RANK_DIM_CENTER = 21
TYPE_RANK_DIM_RADIAL = 22
TYPE_RANK_DIM_ORTHOGONAL = 23
TYPE_RANK_TARGET = 24
TYPE_RANK_ZONE = 25
TYPE_RANK_GROUP = 28
TYPE_RANK_POINT = 30

# ``SHAPE_T`` (``include/eda_shape.h:44-53``).
SHAPE_KIND_SEGMENT = 0
SHAPE_KIND_RECTANGLE = 1
SHAPE_KIND_ARC = 2
SHAPE_KIND_CIRCLE = 3
SHAPE_KIND_POLY = 4
SHAPE_KIND_BEZIER = 5

_DIMENSION_TYPE_RANK: dict[DimensionType, int] = {
    DimensionType.aligned: TYPE_RANK_DIM_ALIGNED,
    DimensionType.leader: TYPE_RANK_DIM_LEADER,
    DimensionType.center: TYPE_RANK_DIM_CENTER,
    DimensionType.radial: TYPE_RANK_DIM_RADIAL,
    DimensionType.orthogonal: TYPE_RANK_DIM_ORTHOGONAL,
}

# Mandatory footprint field order, from the four fields the ``FOOTPRINT``
# constructor pre-creates (``pcbnew/footprint.cpp:105-117``). This differs from
# the symbol side, which also carries ``Footprint`` (sweep contradiction C-6).
# KiCad 10 DROPS a ``Footprint`` property on a footprint
# (``pcb_io_kicad_sexpr_parser.cpp:5159-5166``); this library keeps it, so it
# ranks as a user field.
_MANDATORY_FOOTPRINT_FIELD_RANK: dict[str, int] = {
    "reference": 1,
    "value": 2,
    "datasheet": 3,
    "description": 4,
}

# Every user field ranks after every mandatory one.
_USER_FIELD_RANK_BASE = 42

# The layer wildcard table lives in common/layers.py, beside the canonical
# layer names it expands to. One table, so a reader that selects by name and
# a reader that sorts by ordinal can never disagree about what `*.Cu` means.

# A layer name this library cannot resolve sorts after every known one, which
# keeps the comparator total without inventing an ordinal.
_UNKNOWN_LAYER = 1 << 16


# ---------------------------------------------------------------------------
# Units, layers and coordinate frame
# ---------------------------------------------------------------------------


def to_iu(millimetres: float) -> int:
    """Convert a millimetre value to PCB internal units (nanometres)."""
    return kiround(millimetres * PCB_IU_PER_MM)


def point_iu(point: tuple[float, float]) -> tuple[int, int]:
    """Convert a file-space millimetre point to internal units.

    PCB Y is NOT negated on write, unlike the symbol library writer, so the file
    value and the internal value share a sign.
    """
    return (to_iu(point[0]), to_iu(point[1]))


def layer_ordinal(name: str | None) -> int:
    """Return the ``PCB_LAYER_ID`` of *name*, the key ``GetLayer()`` compares."""
    if name is None:
        return _UNKNOWN_LAYER
    return LAYER_NAME_TO_ORDINAL.get(name, _UNKNOWN_LAYER)


def layer_set_seq(names: list[str] | None) -> tuple[int, ...]:
    """Return ``LSET::Seq()`` for *names*: ascending layer ids, wildcards expanded.

    ``LSET::Seq`` walks the bitset low to high (``common/lset.cpp:327-340``) and
    the result is an ``LSEQ``, a vector compared lexicographically.
    """
    if not names:
        return ()
    ordinals: set[int] = set()
    for name in names:
        expanded = LAYER_WILDCARD_ORDINALS.get(name)
        if expanded is not None:
            ordinals.update(expanded)
        else:
            ordinals.add(layer_ordinal(name))
    return tuple(sorted(ordinals))


class FootprintFrame:
    """The affine map from a footprint child's FILE position to its BOARD position.

    The writer emits ``RotatePoint( coord - fpPosition, -fpOrientation )``
    (``pcb_io_kicad_sexpr.cpp:466-476``) and the parser inverts it
    (``pcb_io_kicad_sexpr_parser.cpp:3609-3613``). A standalone ``.kicad_mod``
    carries no ``(at …)``, so :data:`IDENTITY` applies and the two frames coincide.
    """

    __slots__ = ("angle", "offset")

    def __init__(self, offset: tuple[float, float] = (0.0, 0.0), angle: float = 0.0) -> None:
        self.offset = point_iu(offset)
        self.angle = angle

    def to_board(self, point: tuple[float, float]) -> tuple[int, int]:
        """Return the board-coordinate internal-unit position of a file-space point."""
        return self.to_board_iu(point_iu(point))

    def to_board_iu(self, point: tuple[int, int]) -> tuple[int, int]:
        """Map an ALREADY converted internal-unit point into board coordinates.

        A polygon needs this. KiCad interpolates an arc first and transforms the
        resulting run second (``pcb_io_kicad_sexpr_parser.cpp:3609-3613``), so
        the interpolation must run in footprint-local units.
        """
        from eedesigncore.common.kimath import rotate_point_iu

        if self.angle == 0.0 and self.offset == (0, 0):
            return point
        rotated = rotate_point_iu(point, self.angle)
        return (rotated[0] + self.offset[0], rotated[1] + self.offset[1])


IDENTITY_FRAME = FootprintFrame()


# ---------------------------------------------------------------------------
# Item classification
# ---------------------------------------------------------------------------


def drawing_type_rank(item: Any) -> int:
    """Return the ``KICAD_T`` rank of a drawing-stream item.

    Both drawing comparators key on ``Type()`` first
    (``footprint.cpp:4318-4319``, ``board.cpp:3428-3429``).
    """
    if isinstance(item, PcbLine | PcbRect | PcbCircle | PcbArc | PcbPoly | PcbCurve):
        return TYPE_RANK_SHAPE
    if isinstance(item, ReferenceImage):
        return TYPE_RANK_REFERENCE_IMAGE
    if isinstance(item, PcbText):
        return TYPE_RANK_TEXT
    if isinstance(item, PcbTextBox):
        return TYPE_RANK_TEXTBOX
    if isinstance(item, Table):
        return TYPE_RANK_TABLE
    if isinstance(item, Barcode):
        return TYPE_RANK_BARCODE
    if isinstance(item, Dimension):
        return _DIMENSION_TYPE_RANK[item.dim_type]
    return TYPE_RANK_TARGET


def shape_kind(item: Any) -> int:
    """Return the ``SHAPE_T`` of a PCB shape."""
    if isinstance(item, PcbRect):
        return SHAPE_KIND_RECTANGLE
    if isinstance(item, PcbArc):
        return SHAPE_KIND_ARC
    if isinstance(item, PcbCircle):
        return SHAPE_KIND_CIRCLE
    if isinstance(item, PcbPoly):
        return SHAPE_KIND_POLY
    if isinstance(item, PcbCurve):
        return SHAPE_KIND_BEZIER
    return SHAPE_KIND_SEGMENT


def item_layer_ordinal(item: Any) -> int:
    """Return the single-layer key ``GetLayer()`` yields for a drawing item.

    A multi-layer shape stores an ``LSET`` and ``GetLayer()`` returns its lowest
    member, which is the first entry of ``LSET::Seq()``.
    """
    layers = getattr(item, "layers", None)
    if layers:
        return layer_set_seq(layers)[0]
    return layer_ordinal(getattr(item, "layer", None))


def item_uuid(item: Any) -> str:
    """Return the UUID text a comparator falls back to, or the empty string.

    ``KIID::operator<`` compares the 16 raw bytes, and the canonical text renders
    each byte as two lowercase hex digits at a fixed offset, so the text compare
    that this port uses gives the same order.
    """
    return getattr(item, "id", None) or getattr(item, "uuid", None) or ""


def compare_uuid(first: str, second: str) -> int:
    """Compare two UUID texts, or TIE when either one is not persisted.

    Every PCB comparator ends on ``m_Uuid``, which every item carries at runtime
    but not every item writes. A teardrop zone is the live case: KiCad skips its
    ``(uuid …)`` entirely (``pcb_io_kicad_sexpr.cpp:2887-2888``) while still
    sorting on it. A missing UUID is unknown, not empty, so it must not decide.
    Tying keeps the KiCad-written neighbours in place under the stable sort.
    """
    if not first or not second:
        return 0
    if first == second:
        return 0
    return -1 if first < second else 1


def has_arc_element(item: Any) -> bool:
    """Report whether a ``(pts …)`` chain of *item* carries an ``(arc …)`` element.

    KiCad stores such a chain as an interpolated point RUN whose length depends
    on the arc radius and the interpolation error, so a vertex COUNT taken from
    the file cannot match ``SHAPE_POLY_SET::TotalVertices`` (ADR-061). Callers
    stop comparing rather than guess.
    """
    return any(isinstance(element, PtsArc) for element in getattr(item, "pts", []))


def _plain_point(element: tuple[float, float] | PtsArc) -> tuple[float, float]:
    """Return a ``(pts …)`` element as a plain point.

    A Bezier control slot always holds a plain ``(xy …)``, because KiCad writes
    exactly four of them (``pcb_io_kicad_sexpr.cpp:1047-1052``). An ``(arc …)``
    there would be malformed, so its start stands in.
    """
    if isinstance(element, PtsArc):
        return element.start
    return element


def _polygon_outline(item: Any, frame: FootprintFrame) -> list[tuple[int, int]] | None:
    """Return the board-space vertex list KiCad holds for a polygon shape.

    KiCad compares a polygon on ``SHAPE_POLY_SET::TotalVertices`` and then on
    each ``CVertex`` (``pcbnew/footprint.cpp:4358-4371``). An ``(arc …)`` inside
    the chain becomes an interpolated run first, so the file element count is
    not the vertex count.

    Interpolation runs in footprint-local units and the transform follows, which
    is the order the parser uses (``pcb_io_kicad_sexpr_parser.cpp:3609-3613``).
    """
    from eedesigncore.common.pcb_family.poly_outline import polygon_outline_iu

    outline = polygon_outline_iu(list(getattr(item, "pts", [])), point_iu)
    if outline is None:
        return None
    return [frame.to_board_iu(vertex) for vertex in outline]


def _shape_points(item: Any) -> list[tuple[float, float]]:
    """Return the plain vertices of a polygon-like shape, in file order.

    An ``(arc …)`` element inside a ``(pts …)`` chain contributes its start, mid
    and end. KiCad stores an interpolated point run there instead, so the count
    differs. The vertex key is only reached when type, layer and shape kind all
    tie, and a wrong count is worse than a tie, so this is documented rather than
    guessed (ADR-061).
    """
    points: list[tuple[float, float]] = []
    for element in getattr(item, "pts", []):
        if isinstance(element, PtsArc):
            points.extend([element.start, element.mid, element.end])
        else:
            points.append(element)
    return points


# ---------------------------------------------------------------------------
# Point comparison, the shared building block
# ---------------------------------------------------------------------------


def cmp_points(first: tuple[int, int], second: tuple[int, int]) -> int:
    """Compare two internal points as ``cmp_points_opt`` does (``footprint.cpp:4304-4313``)."""
    if first[0] != second[0]:
        return -1 if first[0] < second[0] else 1
    if first[1] != second[1]:
        return -1 if first[1] < second[1] else 1
    return 0


def _cmp_scalar(first: float, second: float) -> int:
    if first != second:
        return -1 if first < second else 1
    return 0


def _cmp_text(first: str, second: str) -> int:
    if first != second:
        return -1 if first < second else 1
    return 0


def _cmp_seq(first: tuple[int, ...], second: tuple[int, ...]) -> int:
    if first != second:
        return -1 if first < second else 1
    return 0


# ---------------------------------------------------------------------------
# FOOTPRINT::cmp_drawings (``pcbnew/footprint.cpp:4316-4423``)
# ---------------------------------------------------------------------------


def _cmp_footprint_shape(first: Any, second: Any, frame: FootprintFrame) -> int:
    """Compare two PCB shapes, per ``footprint.cpp:4326-4377``."""
    kind_first = shape_kind(first)
    kind_second = shape_kind(second)
    if kind_first != kind_second:
        return -1 if kind_first < kind_second else 1

    if kind_first != SHAPE_KIND_POLY:
        result = cmp_points(_shape_start(first, frame), _shape_start(second, frame))
        if result:
            return result
        result = cmp_points(_shape_end(first, frame), _shape_end(second, frame))
        if result:
            return result

    if kind_first == SHAPE_KIND_ARC:
        result = cmp_points(_arc_center(first, frame), _arc_center(second, frame))
        if result:
            return result
    elif kind_first == SHAPE_KIND_BEZIER:
        result = cmp_points(
            frame.to_board(_plain_point(first.pts[1])), frame.to_board(_plain_point(second.pts[1]))
        )
        if result:
            return result
        result = cmp_points(
            frame.to_board(_plain_point(first.pts[2])), frame.to_board(_plain_point(second.pts[2]))
        )
        if result:
            return result
    elif kind_first == SHAPE_KIND_POLY:
        outline_first = _polygon_outline(first, frame)
        outline_second = _polygon_outline(second, frame)
        if outline_first is None or outline_second is None:
            # An element neither a point nor an arc. Ordering on a count taken
            # from a chain this code does not understand would be a guess, so
            # tie and let the stroke width and the UUID decide.
            pass
        else:
            if len(outline_first) != len(outline_second):
                return -1 if len(outline_first) < len(outline_second) else 1
            for vertex_first, vertex_second in zip(outline_first, outline_second, strict=True):
                result = cmp_points(vertex_first, vertex_second)
                if result:
                    return result

    return _cmp_scalar(to_iu(first.stroke.width), to_iu(second.stroke.width))


def _shape_start(item: Any, frame: FootprintFrame) -> tuple[int, int]:
    """Return ``EDA_SHAPE::GetStart()`` in board internal units."""
    if isinstance(item, PcbCircle):
        return frame.to_board(item.center)
    if isinstance(item, PcbCurve):
        return frame.to_board(_plain_point(item.pts[0]))
    return frame.to_board(getattr(item, "start", (0.0, 0.0)))


def _shape_end(item: Any, frame: FootprintFrame) -> tuple[int, int]:
    """Return ``EDA_SHAPE::GetEnd()`` in board internal units."""
    if isinstance(item, PcbCurve):
        return frame.to_board(_plain_point(item.pts[3]))
    return frame.to_board(getattr(item, "end", (0.0, 0.0)))


def _arc_center(arc: PcbArc, frame: FootprintFrame) -> tuple[int, int]:
    """Return the arc centre, which ``SetArcGeometry`` derives from start, mid and end."""
    return calc_arc_center_iu(
        frame.to_board(arc.start), frame.to_board(arc.mid), frame.to_board(arc.end)
    )


def _text_size_iu(item: PcbText) -> tuple[int, int]:
    """Return ``GetTextSize()`` as ``(x, y)``, which is ``(width, height)``.

    Our ``font_size`` tuple is ``(height, width)``, because ``EDA_TEXT::Format``
    emits ``(size height width)`` (``common/eda_text.cpp:1071-1072``). The
    comparator reads ``m_Size.x`` first, so the tuple must be swapped here.
    """
    attributes = item.attributes
    if attributes is None:
        return (0, 0)
    return (to_iu(attributes.font_size[1]), to_iu(attributes.font_size[0]))


def _cmp_footprint_text(first: PcbText, second: PcbText, frame: FootprintFrame) -> int:
    """Compare two footprint texts, per ``footprint.cpp:4378-4411``."""
    result = cmp_points(frame.to_board(first.position), frame.to_board(second.position))
    if result:
        return result

    result = _cmp_scalar(first.angle, second.angle)
    if result:
        return result

    result = cmp_points(_text_size_iu(first), _text_size_iu(second))
    if result:
        return result

    first_attributes = first.attributes
    second_attributes = second.attributes
    if first_attributes is None or second_attributes is None:
        return _cmp_text(first.content, second.content)

    result = _cmp_scalar(
        to_iu(first_attributes.font_thickness or 0.0),
        to_iu(second_attributes.font_thickness or 0.0),
    )
    if result:
        return result

    result = _cmp_scalar(int(first_attributes.bold), int(second_attributes.bold))
    if result:
        return result

    result = _cmp_scalar(int(first_attributes.italic), int(second_attributes.italic))
    if result:
        return result

    result = _cmp_scalar(int(first_attributes.mirror), int(second_attributes.mirror))
    if result:
        return result

    result = _cmp_scalar(
        first_attributes.line_spacing if first_attributes.line_spacing is not None else 1.0,
        second_attributes.line_spacing if second_attributes.line_spacing is not None else 1.0,
    )
    if result:
        return result

    return _cmp_text(first.content, second.content)


def compare_footprint_drawings(first: Any, second: Any, frame: FootprintFrame) -> int:
    """Compare two footprint drawing items as ``FOOTPRINT::cmp_drawings`` orders them.

    Key order (``pcbnew/footprint.cpp:4316-4423``): type, layer, per-type keys,
    then UUID. Every type outside ``PCB_SHAPE_T`` and ``PCB_TEXT_T`` takes the
    default branch and falls straight to the UUID (``:4412-4416``).
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
        result = _cmp_footprint_shape(first, second, frame)
        if result:
            return result
    elif rank_first == TYPE_RANK_TEXT:
        result = _cmp_footprint_text(first, second, frame)
        if result:
            return result

    return compare_uuid(item_uuid(first), item_uuid(second))


# ---------------------------------------------------------------------------
# PCB_POINT::cmp_points (``pcbnew/pcb_point.cpp:62-80``)
# ---------------------------------------------------------------------------


def compare_pcb_points(first: PcbPoint, second: PcbPoint) -> int:
    """Compare two ``(point …)`` markers: layer, x, y, size, then UUID."""
    result = _cmp_scalar(layer_ordinal(first.layer), layer_ordinal(second.layer))
    if result:
        return result

    result = cmp_points(point_iu(first.position), point_iu(second.position))
    if result:
        return result

    result = _cmp_scalar(to_iu(first.size), to_iu(second.size))
    if result:
        return result

    return compare_uuid(item_uuid(first), item_uuid(second))


# ---------------------------------------------------------------------------
# FOOTPRINT::cmp_pads (``pcbnew/footprint.cpp:4426-4467``)
# ---------------------------------------------------------------------------


def _pad_layer_geometry(pad: Pad) -> list[tuple[int, int, str]]:
    """Return the per-unique-layer ``(size x, size y, shape)`` triples of *pad*.

    ``PADSTACK::ForEachUniqueLayer`` yields one entry for a NORMAL padstack, three
    for FRONT_INNER_BACK, and one per stored copper layer for CUSTOM
    (``pcbnew/padstack.cpp:1241-1258``). A padstack layer that overrides neither
    size nor shape inherits the pad-level value.
    """
    base = (to_iu(pad.size[0]), to_iu(pad.size[1]), pad.shape.value)
    padstack = pad.padstack
    if padstack is None:
        return [base]

    entries: list[tuple[int, int, str]] = []
    for layer in padstack.layers:
        size = layer.size
        entries.append(
            (
                to_iu(size[0]) if size is not None else base[0],
                to_iu(size[1]) if size is not None else base[1],
                layer.shape.value if layer.shape is not None else base[2],
            )
        )
    return entries or [base]


def compare_pads(first: Pad, second: Pad) -> int:
    """Compare two pads as ``FOOTPRINT::cmp_pads`` orders them.

    Key order: ``StrNumCmp`` of the pad number, then the FOOTPRINT-RELATIVE
    position, then per unique padstack layer the size and shape, then the layer
    set, then the UUID.

    The number branch runs only when the two number STRINGS differ
    (``footprint.cpp:4428``), so ``"01"`` and ``"1"`` fall through to the position
    keys even though ``StrNumCmp`` calls them equal.

    The padstack loop keeps the C++ overwrite semantics: it visits every unique
    layer and the LAST differing one decides (``:4446-4458``).
    """
    if first.number != second.number:
        result = str_num_cmp(first.number, second.number)
        if result:
            return -1 if result < 0 else 1

    result = cmp_points(point_iu(first.position), point_iu(second.position))
    if result:
        return result

    geometry_first = _pad_layer_geometry(first)
    geometry_second = _pad_layer_geometry(second)
    copper_verdict = 0
    for entry_first, entry_second in zip(geometry_first, geometry_second, strict=False):
        if entry_first[0] != entry_second[0]:
            copper_verdict = -1 if entry_first[0] < entry_second[0] else 1
        elif entry_first[1] != entry_second[1]:
            copper_verdict = -1 if entry_first[1] < entry_second[1] else 1
        elif entry_first[2] != entry_second[2]:
            copper_verdict = -1 if entry_first[2] < entry_second[2] else 1
    if copper_verdict:
        return copper_verdict

    result = _cmp_seq(layer_set_seq(first.layers), layer_set_seq(second.layers))
    if result:
        return result

    return compare_uuid(item_uuid(first), item_uuid(second))


# ---------------------------------------------------------------------------
# FOOTPRINT::cmp_zones (``pcbnew/footprint.cpp:4527-4551``)
# ---------------------------------------------------------------------------


def zone_layer_seq(zone: Zone) -> tuple[int, ...]:
    """Return ``ZONE::GetLayerSet()`` as an ``LSEQ`` (``pcbnew/zone.h:137-141``)."""
    if zone.layers:
        return layer_set_seq(zone.layers)
    return layer_set_seq([zone.layer] if zone.layer else None)


def _zone_vertices(zone: Zone) -> list[tuple[float, float]]:
    """Return every outline vertex of *zone*, in ``SHAPE_POLY_SET`` walk order."""
    vertices: list[tuple[float, float]] = []
    for polygon in zone.polygons:
        for element in polygon.pts:
            if isinstance(element, PtsArc):
                vertices.extend([element.start, element.mid, element.end])
            else:
                vertices.append(element)
    return vertices


def _zone_has_arc(zone: Zone) -> bool:
    return any(has_arc_element(polygon) for polygon in zone.polygons)


def compare_zones(first: Zone, second: Zone) -> int:
    """Compare two zones: priority, layer set, vertex count, vertices, then UUID.

    A zone outline is written unrotated even inside a footprint
    (``pcb_io_kicad_sexpr.cpp:3057-3066``), so the file coordinates are already
    the board coordinates the comparator reads.
    """
    result = _cmp_scalar(first.priority or 0, second.priority or 0)
    if result:
        return result

    result = _cmp_seq(zone_layer_seq(first), zone_layer_seq(second))
    if result:
        return result

    if _zone_has_arc(first) or _zone_has_arc(second):
        return 0

    vertices_first = _zone_vertices(first)
    vertices_second = _zone_vertices(second)
    if len(vertices_first) != len(vertices_second):
        return -1 if len(vertices_first) < len(vertices_second) else 1

    for vertex_first, vertex_second in zip(vertices_first, vertices_second, strict=True):
        result = cmp_points(point_iu(vertex_first), point_iu(vertex_second))
        if result:
            return result

    return compare_uuid(item_uuid(first), item_uuid(second))


# ---------------------------------------------------------------------------
# BOARD_ITEM::ptr_cmp (``pcbnew/board_item.cpp:314-326``)
# ---------------------------------------------------------------------------


def compare_by_layer_set_then_uuid(
    first_layers: tuple[int, ...],
    first_uuid: str,
    second_layers: tuple[int, ...],
    second_uuid: str,
) -> int:
    """Apply ``BOARD_ITEM::ptr_cmp`` to two items already known to share a type."""
    result = _cmp_seq(first_layers, second_layers)
    if result:
        return result
    return compare_uuid(first_uuid, second_uuid)


def group_layer_seq(group: PcbGroup, layer_sets: dict[str, tuple[int, ...]]) -> tuple[int, ...]:
    """Return ``PCB_GROUP::GetLayerSet()``, the union of its members' layer sets.

    Source: ``pcbnew/pcb_group.cpp:329-337``. A member this library cannot resolve
    contributes nothing, which under-discriminates rather than misorders.
    """
    ordinals: set[int] = set()
    for member in group.members:
        ordinals.update(layer_sets.get(member, ()))
    return tuple(sorted(ordinals))


def compare_groups(
    first: PcbGroup, second: PcbGroup, layer_sets: dict[str, tuple[int, ...]]
) -> int:
    """Compare two groups by ``BOARD_ITEM::ptr_cmp``: layer set union, then UUID."""
    return compare_by_layer_set_then_uuid(
        group_layer_seq(first, layer_sets),
        item_uuid(first),
        group_layer_seq(second, layer_sets),
        item_uuid(second),
    )


def footprint_member_layer_sets(footprint: Any) -> dict[str, tuple[int, ...]]:
    """Index every footprint child by UUID, mapping it to its ``LSET::Seq()``.

    A group's layer key is the union over its members, so resolving the members
    needs this index. Nested groups resolve after the leaves, in one pass, which
    is enough for the single nesting level KiCad's own group editor produces.
    """
    layer_sets: dict[str, tuple[int, ...]] = {}

    for item in footprint.drawings:
        uuid = item_uuid(item)
        if uuid:
            layers = getattr(item, "layers", None)
            layer_sets[uuid] = (
                layer_set_seq(layers) if layers else (layer_ordinal(getattr(item, "layer", None)),)
            )
    for point in footprint.points:
        uuid = item_uuid(point)
        if uuid:
            layer_sets[uuid] = (layer_ordinal(point.layer),)
    for pad in footprint.pads:
        uuid = item_uuid(pad)
        if uuid:
            layer_sets[uuid] = layer_set_seq(pad.layers)
    for zone in footprint.zones:
        uuid = item_uuid(zone)
        if uuid:
            layer_sets[uuid] = zone_layer_seq(zone)
    for group in footprint.groups:
        uuid = item_uuid(group)
        if uuid:
            layer_sets[uuid] = group_layer_seq(group, layer_sets)

    return layer_sets


# ---------------------------------------------------------------------------
# Footprint properties (``pcbnew/footprint.cpp:105-117``, parser ``:5159-5182``)
# ---------------------------------------------------------------------------


def footprint_field_rank(name: str, user_index: int) -> int:
    """Return the deque rank KiCad gives a footprint property.

    The ``FOOTPRINT`` constructor pre-creates Reference, Value, Datasheet and
    Description in that order, and the parser fills a matching field in place or
    appends a new user field (``pcb_io_kicad_sexpr_parser.cpp:5167-5182``). So the
    four mandatory names always lead, whatever the file order, and user fields
    follow in file order. The name match is case-insensitive, as
    ``FOOTPRINT::HasField`` is.
    """
    mandatory = _MANDATORY_FOOTPRINT_FIELD_RANK.get(name.casefold())
    if mandatory is not None:
        return mandatory
    return _USER_FIELD_RANK_BASE + user_index


def sort_footprint_properties(properties: list[Property]) -> list[Property]:
    """Return footprint properties in the order the field deque holds them."""
    ranks: list[int] = []
    user_index = 0
    for prop in properties:
        rank = footprint_field_rank(prop.name, user_index)
        if rank >= _USER_FIELD_RANK_BASE:
            user_index += 1
        ranks.append(rank)

    order = sorted(range(len(properties)), key=lambda index: (ranks[index], index))
    return [properties[index] for index in order]


# ---------------------------------------------------------------------------
# Public sort entry points
# ---------------------------------------------------------------------------


def sort_footprint_drawings(items: list[Any], frame: FootprintFrame = IDENTITY_FRAME) -> list[Any]:
    """Return the footprint drawing stream in KiCad save order."""
    return sorted(items, key=cmp_to_key(lambda a, b: compare_footprint_drawings(a, b, frame)))


def sort_pcb_points(points: list[PcbPoint]) -> list[PcbPoint]:
    """Return the ``(point …)`` stream in KiCad save order."""
    return sorted(points, key=cmp_to_key(compare_pcb_points))


def sort_pads(pads: list[Pad]) -> list[Pad]:
    """Return the pad stream in KiCad save order."""
    return sorted(pads, key=cmp_to_key(compare_pads))


def sort_zones(zones: list[Zone]) -> list[Zone]:
    """Return a footprint's zone stream in KiCad save order."""
    return sorted(zones, key=cmp_to_key(compare_zones))


def sort_groups(groups: list[PcbGroup], layer_sets: dict[str, tuple[int, ...]]) -> list[PcbGroup]:
    """Return the group stream in KiCad save order."""

    def compare(first: PcbGroup, second: PcbGroup) -> int:
        return compare_groups(first, second, layer_sets)

    return sorted(groups, key=cmp_to_key(compare))


def sort_group_members(members: list[str]) -> list[str]:
    """Return group member UUIDs in ascending string order (``:2495``).

    ``memberIds.Sort()`` sorts the UUID TEXT, not the raw bytes. The two orders
    agree, because the canonical text is fixed-width lowercase hex.
    """
    return sorted(members)


def sort_variants(variants: list[Variant]) -> list[Variant]:
    """Return footprint variants by name, case-insensitively.

    ``FOOTPRINT::GetVariants`` returns a ``CASE_INSENSITIVE_MAP``
    (``pcbnew/footprint.h:963``) whose comparator is ``CmpNoCase``
    (``include/case_insensitive_map.h:33-38``).
    """
    return sorted(variants, key=lambda variant: variant.name.casefold())


def sort_variant_fields(fields: list[VariantField]) -> list[VariantField]:
    """Return variant field overrides by name ascending.

    ``FOOTPRINT_VARIANT::GetFields`` returns a ``std::map<wxString, wxString>``
    (``pcbnew/footprint.h:218``), so the key order is plain code-unit ascending.
    """
    return sorted(fields, key=lambda field: field.name)
