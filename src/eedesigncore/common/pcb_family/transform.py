"""Per-item-type coordinate transforms for the shared PCB-family models.

Two operations, kept apart because they act on two different coordinate frames.

:func:`move_points` moves the geometry an item stores in its OWN frame. For a
board item that frame is the board. For a footprint child it is the footprint,
de-rotated (``formatInternalUnits( coord, parentFP )``,
``pcb_io_kicad_sexpr.cpp:466-478``), so a footprint child must NOT be moved when
the whole footprint moves.

:func:`move_render_cache` moves the cached glyph outlines of a text. Those are
board coordinates whatever the parent is (``formatRenderCache`` :522-547), so
they move with the board even when the text belongs to a footprint. KiCad does
the same in ``EDA_TEXT::Offset`` (``common/eda_text.cpp:578-597``), which shifts
the cached glyphs by the same integer offset.

Three item types store BOARD coordinates even inside a footprint, because their
writers never pass the parent footprint: ``Dimension`` (:906-910, :2264-2266),
``ReferenceImage`` (:1125-1127) and ``Barcode`` (:2198-2200). The board-level
caller in ``pcb/transform.py`` reads
:data:`BOARD_FRAME_TYPES` to decide which footprint children still move.

Full frame table and its evidence:
``context/audit/footprint-transform-frame-2026-09-02.md``.

The dispatch is exhaustive and hard-errors on an unknown type (ADR-017 in
spirit: silence is the one outcome a round-trip library cannot afford).
"""

from __future__ import annotations

from collections.abc import Callable

from eedesigncore.common.kimath import normalize_angle
from eedesigncore.common.models import (
    JustifyH,
    JustifyV,
    Property,
    PtsArc,
    PtsElement,
    RenderCache,
)
from eedesigncore.common.pcb_family.board_items import (
    Barcode,
    Dimension,
    DimensionText,
    ReferenceImage,
    Table,
    TableCell,
    Zone,
)
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
from eedesigncore.common.transform import Point, Rotation

# A point transform: millimetres in, millimetres out.
PointTransform = Callable[[Point], Point]

# The item types whose stored coordinates are board coordinates even when the
# item sits inside a footprint.
BOARD_FRAME_TYPES: tuple[type, ...] = (Dimension, ReferenceImage, Barcode)


def move_pts(pts: list[PtsElement], transform: PointTransform) -> list[PtsElement]:
    """Return a ``(pts …)`` chain with every element moved, arcs included."""
    moved: list[PtsElement] = []
    for element in pts:
        if isinstance(element, PtsArc):
            moved.append(
                PtsArc(
                    start=transform(element.start),
                    mid=transform(element.mid),
                    end=transform(element.end),
                )
            )
        else:
            moved.append(transform(element))
    return moved


def move_render_cache(item: object, transform: PointTransform) -> None:
    """Move the cached glyph outlines of *item*, when it carries any.

    The cache holds board coordinates, so it moves whenever the text moves on the
    board, including when the text is a footprint child and only the footprint
    placement changed. Items with no cache are left alone, so this is safe to
    call on anything.
    """
    cache = getattr(item, "render_cache", None)
    if not isinstance(cache, RenderCache):
        return
    for polygon in cache.polygons:
        polygon.pts = move_pts(polygon.pts, transform)


def move_points(item: object, transform: PointTransform) -> None:  # noqa: C901, PLR0912
    """Move every coordinate *item* stores in its own frame, in place.

    The render cache is NOT touched here, because it lives in a different frame.
    Call :func:`move_render_cache` alongside this when the item moves on the
    board.

    Raises:
        TypeError: when *item* is not a PCB-family item this dispatch covers.
    """
    if isinstance(item, PcbText):
        item.position = transform(item.position)
    elif isinstance(item, PcbLine | PcbRect | TableCell):
        item.start = transform(item.start)
        item.end = transform(item.end)
    elif isinstance(item, PcbCircle):
        item.center = transform(item.center)
        item.end = transform(item.end)
    elif isinstance(item, PcbArc):
        item.start = transform(item.start)
        item.mid = transform(item.mid)
        item.end = transform(item.end)
    elif isinstance(item, PcbPoly | PcbCurve):
        item.pts = move_pts(item.pts, transform)
    elif isinstance(item, PcbTextBox):
        if item.start is not None:
            item.start = transform(item.start)
        if item.end is not None:
            item.end = transform(item.end)
        item.pts = move_pts(item.pts, transform)
    elif isinstance(item, Table):
        for cell in item.cells:
            move_points(cell, transform)
    elif isinstance(item, Dimension):
        item.pts = move_pts(item.pts, transform)
        if item.text is not None:
            move_points(item.text, transform)
    elif isinstance(item, DimensionText | ReferenceImage | Barcode | PcbPoint | Property):
        item.position = transform(item.position)
    elif isinstance(item, Zone):
        for outline in item.polygons:
            outline.pts = move_pts(outline.pts, transform)
        for filled in item.filled_polygons:
            filled.pts = move_pts(filled.pts, transform)
        for layer_property in item.layer_properties:
            if layer_property.hatch_position is not None:
                layer_property.hatch_position = transform(layer_property.hatch_position)
    elif isinstance(item, PcbGroup):
        pass  # a group is a member list; it stores no geometry
    else:
        raise TypeError(f"move_points does not cover {type(item).__name__}")


def move_item(item: object, transform: PointTransform) -> None:
    """Move an item that lives in BOARD coordinates: own-frame points and cache.

    This is the whole move for a board-level item. A footprint child needs the
    two calls made separately, because only its cache is in board coordinates.
    """
    move_points(item, transform)
    move_render_cache(item, transform)
    if isinstance(item, Dimension) and item.text is not None:
        move_render_cache(item.text, transform)
    elif isinstance(item, Table):
        for cell in item.cells:
            move_render_cache(cell, transform)


# ---------------------------------------------------------------------------
# Rotation: the angle fields, the render cache, and the rectangle limit
# ---------------------------------------------------------------------------
#
# Rotation is a point move PLUS an angle change, and the angle rule is per type.
# An item in this tuple stores an angle in BOARD terms, so a rotation adds the
# turn to it. Every other item type either stores no angle or stores one that is
# footprint-local, which a rotation must leave alone. Evidence:
# ``context/audit/footprint-transform-frame-2026-09-02.md``.

# Types whose stored angle is absolute and normalises to [0, 360) on rotation.
_NORMALISING_ANGLE_TYPES = (PcbText, DimensionText, Property)

_FLIPPED_H = {JustifyH.left: JustifyH.right, JustifyH.right: JustifyH.left}
_FLIPPED_V = {JustifyV.top: JustifyV.bottom, JustifyV.bottom: JustifyV.top}


def drop_render_cache(item: object) -> None:
    """Discard the cached glyph outlines of *item*, when it carries any.

    A rotation invalidates the cache and this library cannot rebuild it: the
    glyphs come from an outline font it does not load. KiCad does the same on
    its side. ``EDA_TEXT::SetTextAngle`` calls ``ClearRenderCache``
    (``common/eda_text.cpp:283-288``); the difference is that KiCad rebuilds the
    block before it writes and this library leaves it out. The cache is derived
    data, the file stays valid without it, and KiCad rebuilds it on its next
    save.
    """
    if getattr(item, "render_cache", None) is not None:
        item.render_cache = None  # type: ignore[attr-defined]


def blocks_non_cardinal_rotation(item: object) -> bool:
    """True when a non-quarter turn would change *item* from a rectangle to a polygon.

    ``EDA_SHAPE::rotate`` (``common/eda_shape.cpp:957-972``) keeps a RECTANGLE a
    rectangle only for a cardinal angle. For any other angle it tessellates the
    rounded rectangle into a polygon with a curve-flattening error budget this
    library does not reproduce. Rather than emit geometry KiCad would write
    differently, the caller is told to rotate by a quarter turn.
    """
    if isinstance(item, PcbRect):
        return True
    if isinstance(item, PcbTextBox):
        return item.start is not None and item.end is not None
    if isinstance(item, Table):
        return bool(item.cells)
    return False


def turn_angles(item: object, rotation: Rotation) -> None:
    """Apply *rotation* to the absolute angle fields of *item*, in place.

    Call it beside :func:`move_points` to rotate an item that lives in board
    coordinates. A footprint child needs a different combination; see
    ``pcb/transform.py``.
    """
    if isinstance(item, _NORMALISING_ANGLE_TYPES):
        item.angle = rotation.turn(item.angle)
    elif isinstance(item, PcbTextBox | TableCell):
        # A board text box stores an absolute angle; a footprint one stores a
        # footprint-local angle and never reaches here. An absent angle is zero.
        item.angle = rotation.turn(item.angle or 0.0)
    elif isinstance(item, Barcode):
        # ``PCB_BARCODE::Rotate`` adds without normalising (pcb_barcode.cpp:295-301).
        item.angle = item.angle + rotation.angle
    elif isinstance(item, Table):
        for cell in item.cells:
            turn_angles(cell, rotation)
    elif isinstance(item, Dimension) and item.text is not None:
        turn_angles(item.text, rotation)


def keep_upright(item: object) -> None:
    """Apply ``PCB_TEXT::KeepUpright`` to a footprint text or field, in place.

    Source: ``pcbnew/pcb_text.cpp:374-393``, reached from ``FOOTPRINT::Rotate``
    (``pcbnew/footprint.cpp:2899-2919``) once per field and once per text child,
    AFTER the orientation change. A keep-upright text that ends up at or past
    180 degrees turns a further half circle and swaps BOTH justifications, so it
    still reads left to right.

    ``unlocked`` is the stored inverse of keep-upright, so an ``unlocked`` text
    is left alone.
    """
    if getattr(item, "unlocked", False):
        return
    angle = normalize_angle(getattr(item, "angle", 0.0))
    if angle < 180.0:
        return
    item.angle = normalize_angle(angle + 180.0)  # type: ignore[attr-defined]
    attributes = getattr(item, "attributes", None)
    if attributes is None:
        return
    attributes.justify_h = _FLIPPED_H.get(attributes.justify_h, attributes.justify_h)
    attributes.justify_v = _FLIPPED_V.get(attributes.justify_v, attributes.justify_v)


def rotate_item(item: object, rotation: Rotation) -> None:
    """Rotate an item that lives in BOARD coordinates: points, angles, cache.

    Raises:
        TypeError: when *item* is not a PCB-family item this dispatch covers.
        ValueError: when the turn is not a quarter turn and *item* is a
            rectangle KiCad would convert to a polygon.
    """
    if not rotation.is_cardinal and blocks_non_cardinal_rotation(item):
        raise ValueError(
            f"a {type(item).__name__} cannot take a {rotation.angle} degree turn. "
            "KiCad converts a rectangle to a polygon for any angle that is not a "
            "multiple of 90 (eda_shape.cpp:957-972), and this library does not "
            "reproduce that tessellation. Rotate by a multiple of 90 degrees."
        )
    move_points(item, rotation)
    turn_angles(item, rotation)
    drop_render_cache(item)
    if isinstance(item, Dimension) and item.text is not None:
        drop_render_cache(item.text)
    elif isinstance(item, Table):
        for cell in item.cells:
            drop_render_cache(cell)


__all__ = [
    "blocks_non_cardinal_rotation",
    "drop_render_cache",
    "keep_upright",
    "rotate_item",
    "turn_angles",
    "BOARD_FRAME_TYPES",
    "PointTransform",
    "move_item",
    "move_points",
    "move_pts",
    "move_render_cache",
]
