"""Rigid transforms of board content (work order P1).

These operations move board data. They are mechanism, not policy: they say what
happens to coordinates and nothing about why a caller wants them moved.

Every move runs in internal units (:mod:`eedesigncore.common.transform`), so a
moved coordinate is exactly a coordinate KiCad could have written and a KiCad
resave leaves it alone.

This module lives under ``pcb/`` because it bridges ``pcb.Board`` and
``mod.Footprint``; ``common`` may not import a format sub-package (ADR-015,
ADR-035). The per-item dispatch it drives lives in
``common/pcb_family/transform.py``.

The frame rules, and the evidence for them, are in
``context/audit/footprint-transform-frame-2026-09-02.md``. The one that shapes
this module: MOST footprint children are stored footprint-local and de-rotated,
so moving a footprint moves its placement and they follow.

Five kinds of child do not follow, because the file stores them in board
coordinates whatever the parent. Moving a footprint must move each one:

- a zone, including its outline and every fill record,
- a point marker,
- an embedded dimension, reference image or barcode,
- the cached glyph outlines of every text.

:func:`footprint_board_frame_children` returns the first three. The zone and the
point were missing until 2026-09-05, which left them at the old position while
the footprint moved away. ``FOOTPRINT::SetPosition``
(``pcbnew/footprint.cpp:3003-3025``) moves both, the zone writer emits its
outline through ``formatPolyPts(chain)`` with NO parent footprint, and
``parseZONE`` applies no parent transform on the way back in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from eedesigncore.common.kimath import normalize_angle, normalize_angle_180
from eedesigncore.common.layers import flip_layer_name
from eedesigncore.common.pcb_family.shapes import PcbText
from eedesigncore.common.pcb_family.transform import (
    BOARD_FRAME_TYPES,
    PointTransform,
    blocks_non_cardinal_rotation,
    drop_render_cache,
    keep_upright,
    move_item,
    move_points,
    move_pts,
    move_render_cache,
    rotate_item,
)
from eedesigncore.common.transform import (
    FLIP_AXIS,
    FlipDirection,
    Mirror,
    Point,
    Rotation,
    Translation,
)
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.layers import board_copper_layer_count, board_layer_opposites
from eedesigncore.pcb.models import (
    Board,
    GenPoint,
    GenPts,
    PcbGenerator,
    PcbTarget,
    TrackArc,
    TrackItem,
    TrackSegment,
    Via,
)


def _move_track(track: TrackItem, transform: PointTransform) -> None:
    """Move one track-stream item: a segment, an arc, or a via."""
    if isinstance(track, TrackSegment):
        track.start = transform(track.start)
        track.end = transform(track.end)
    elif isinstance(track, TrackArc):
        track.start = transform(track.start)
        track.mid = transform(track.mid)
        track.end = transform(track.end)
    else:
        track.at = transform(track.at)


def _move_generator(generator: PcbGenerator, transform: PointTransform) -> None:
    """Move the geometry a generator carries in its property bag.

    A generator property is a point when it holds a ``VECTOR2I`` and a chain when
    it holds a ``SHAPE_LINE_CHAIN`` (board-root grammar report A3, branches 3 and
    4). Both are positions by construction, so both move. The remaining branches
    are numbers, booleans and strings, which hold no coordinate.
    """
    for key, value in generator.properties.items():
        if isinstance(value, GenPoint):
            generator.properties[key] = GenPoint(value=transform(value.value))
        elif isinstance(value, GenPts):
            generator.properties[key] = GenPts(value=move_pts(value.value, transform))


def footprint_board_frame_children(footprint: Footprint) -> list[object]:
    """Return the children of *footprint* that the file stores in board coordinates.

    Most children are footprint-local and de-rotated, so they follow the
    placement and must NOT be transformed again. These do not follow:

    - Every zone. ``parseZONE`` applies no parent transform, and the zone writer
      emits its outline through ``formatPolyPts(chain)`` with no parent
      footprint. A placed footprint proves it: a tab footprint at
      (200.842984, 123.271287) carries a zone corner at
      (199.847984, 122.976289), which is the placement plus the library-local
      corner.
    - Every point marker, for the same reason. ``format(PCB_POINT*)`` writes
      ``GetPosition()`` with no parent footprint.
    - Every embedded dimension, reference image and barcode.

    ``FOOTPRINT::SetPosition`` and ``FOOTPRINT::SetOrientation``
    (``pcbnew/footprint.cpp:3003-3025`` and ``:3100-3138``) move and turn all
    three groups, which is the behaviour this list reproduces.
    """
    children: list[object] = [
        item for item in footprint.drawings if isinstance(item, BOARD_FRAME_TYPES)
    ]
    children.extend(footprint.zones)
    children.extend(footprint.points)
    return children


def _move_footprint(footprint: Footprint, transform: PointTransform) -> None:
    """Move a board-context footprint by moving its placement.

    A footprint-local child stays put, because its board position follows the
    placement. Every board-frame child moves, because nothing else would move it.
    See :func:`footprint_board_frame_children`.

    Raises:
        ValueError: when the footprint carries no ``at`` placement, meaning it is
            a library footprint and has no board position to move.
    """
    if footprint.at is None:
        raise ValueError(
            "translate_footprint needs a board-context footprint. This one has no "
            "(at …) placement, so it is a library footprint with no board position."
        )
    footprint.at.position = transform(footprint.at.position)

    for child in footprint_board_frame_children(footprint):
        move_item(child, transform)
    for item in footprint.drawings:
        if not isinstance(item, BOARD_FRAME_TYPES):
            move_render_cache(item, transform)
    for field in footprint.properties:
        move_render_cache(field, transform)


def _translate_board(board: Board, transform: PointTransform) -> None:
    """Apply *transform* to every coordinate the board stores.

    ``setup.aux_axis_origin`` and ``setup.grid_origin`` are deliberately left
    alone. They are board-frame settings that say where the origin sits, not
    content that sits somewhere.
    """
    for footprint in board.footprints:
        _move_footprint(footprint, transform)
    for item in (*board.drawings, *board.trailing_drawings, *board.points, *board.zones):
        move_item(item, transform)
    for track in board.tracks:
        _move_track(track, transform)
    for generator in board.generators:
        _move_generator(generator, transform)
    for target in board.targets:
        target.position = transform(target.position)


def translate_board(board: Board, delta: Point) -> None:
    """Move every item on *board* by *delta* millimetres, in place.

    Covers footprint placements, drawings, points, tracks, arcs, vias, zone
    outlines and fill records, dimensions, tables, reference images, barcodes,
    targets, generator geometry, and the cached glyph outlines of every text.
    Board origins in ``setup`` are not content and do not move.

    The board writer sorts items on emit, so a move that changes the sort order
    still produces canonical output.
    """
    _translate_board(board, Translation(delta))


def translate_footprint(footprint: Footprint, delta: Point) -> None:
    """Move a board-context *footprint* by *delta* millimetres, in place.

    Only the placement moves, plus the coordinates a footprint stores in the
    board frame regardless of its parent. See the module docstring.

    Raises:
        ValueError: when the footprint carries no ``at`` placement.
    """
    _move_footprint(footprint, Translation(delta))


def translate_items(items: Iterable[object], delta: Point) -> None:
    """Move a collection of BOARD-frame items by *delta* millimetres, in place.

    Accepts any board drawing, point, zone, track-stream item, target,
    generator, or board-context footprint. A footprint child passed on its own is
    NOT a board-frame item; moving one moves it inside its footprint, which this
    function does not do.

    Raises:
        TypeError: when an item is not one this dispatch covers.
        ValueError: when a footprint carries no ``at`` placement.
    """
    transform = Translation(delta)
    for item in items:
        if isinstance(item, Footprint):
            _move_footprint(item, transform)
        elif isinstance(item, TrackSegment | TrackArc | Via):
            _move_track(item, transform)
        elif isinstance(item, PcbGenerator):
            _move_generator(item, transform)
        elif isinstance(item, PcbTarget):
            item.position = transform(item.position)
        else:
            move_item(item, transform)


# ---------------------------------------------------------------------------
# Rotation (work order P1-rotate)
# ---------------------------------------------------------------------------


def _rotate_footprint(footprint: Footprint, rotation: Rotation) -> None:
    """Turn a board-context footprint, as ``FOOTPRINT::Rotate`` does.

    Source: ``pcbnew/footprint.cpp:2899-2919``. The placement moves and turns,
    ``SetOrientation`` turns every child, and then EVERY field and EVERY text
    child takes ``KeepUpright``.

    What reaches the file, per the frame audit:

    * ``at`` position turns about the rotation centre;
    * ``at`` angle takes the turn, normalised to ``(-180, 180]``;
    * every pad angle takes the turn, with NO normalisation
      (``PADSTACK::SetOrientation``, padstack.h:345, stores it raw);
    * every text and field angle takes the turn, normalised to ``[0, 360)``,
      then ``KeepUpright``;
    * every footprint-local position and every text-box angle stays put,
      because the placement already carries the turn for them;
    * an embedded dimension, reference image or barcode turns as a board item;
    * every cached glyph outline is dropped.

    Raises:
        ValueError: when the footprint has no ``at`` placement, or when the turn
            is not a quarter turn and a board-frame child is a rectangle.
    """
    if footprint.at is None:
        raise ValueError(
            "rotate_footprint needs a board-context footprint. This one has no "
            "(at ...) placement, so it is a library footprint with no board position."
        )
    if not rotation.is_cardinal:
        for item in footprint.drawings:
            if isinstance(item, BOARD_FRAME_TYPES) and blocks_non_cardinal_rotation(item):
                raise ValueError(
                    f"a {type(item).__name__} inside this footprint cannot take a "
                    f"{rotation.angle} degree turn. Rotate by a multiple of 90 degrees."
                )

    footprint.at.position = rotation(footprint.at.position)
    footprint.at.angle = normalize_angle_180(footprint.at.angle + rotation.angle)

    for pad in footprint.pads:
        pad.angle = pad.angle + rotation.angle

    for child in footprint_board_frame_children(footprint):
        rotate_item(child, rotation)
    for item in footprint.drawings:
        if isinstance(item, BOARD_FRAME_TYPES):
            continue
        drop_render_cache(item)
        if isinstance(item, PcbText):
            item.angle = rotation.turn(item.angle)
            keep_upright(item)
    for field in footprint.properties:
        drop_render_cache(field)
        field.angle = rotation.turn(field.angle)
        keep_upright(field)


def set_footprint_orientation(footprint: Footprint, angle: float) -> None:
    """Give *footprint* a new orientation without moving it, as ``SetOrientation`` does.

    Source: ``pcbnew/footprint.cpp:3100-3138``. This is the SECOND of KiCad's two
    turn operations and it is not the same as the first. ``FOOTPRINT::Rotate``
    moves the placement, turns the children, and then applies ``KeepUpright``.
    ``SetOrientation`` turns the children about the unmoved placement and applies
    no ``KeepUpright`` at all.

    KiCad reaches for this one when it writes a footprint to a library:
    ``PCB_IO_KICAD_SEXPR::FootprintSave`` clones the board footprint and calls
    ``SetOrientation( ANGLE_0 )`` (pcb_io_kicad_sexpr.cpp:3446-3447), which
    de-rotates every child angle back to a library-relative value.

    Raises:
        ValueError: when the footprint carries no ``at`` placement.
    """
    if footprint.at is None:
        raise ValueError(
            "set_footprint_orientation needs a board-context footprint. This one "
            "has no (at ...) placement, so it has no orientation to change."
        )
    delta = angle - footprint.at.angle
    footprint.at.angle = normalize_angle_180(angle)
    if delta == 0.0:
        return

    # SetOrientation turns every child about the UNMOVED placement, so a
    # board-frame child turns about the footprint position rather than the
    # board origin (``pcbnew/footprint.cpp:3108``).
    turn = Rotation(delta)
    about_placement = Rotation(delta, center=footprint.at.position)

    for pad in footprint.pads:
        pad.angle = pad.angle + delta
    for child in footprint_board_frame_children(footprint):
        rotate_item(child, about_placement)
    for item in footprint.drawings:
        if isinstance(item, BOARD_FRAME_TYPES):
            continue
        drop_render_cache(item)
        if isinstance(item, PcbText):
            item.angle = turn.turn(item.angle)
    for field in footprint.properties:
        drop_render_cache(field)
        field.angle = turn.turn(field.angle)


def _rotate_board(board: Board, rotation: Rotation) -> None:
    """Apply *rotation* to every coordinate and every board angle on the board."""
    for footprint in board.footprints:
        _rotate_footprint(footprint, rotation)
    for item in (*board.drawings, *board.trailing_drawings, *board.points, *board.zones):
        rotate_item(item, rotation)
    for track in board.tracks:
        _move_track(track, rotation)
    for generator in board.generators:
        _move_generator(generator, rotation)
    for target in board.targets:
        target.position = rotation(target.position)


def _guard_board_rotation(board: Board, rotation: Rotation) -> None:
    """Reject a non-quarter turn before a single coordinate changes.

    A partly rotated board is worse than a rejected one, so the whole board is
    checked first.
    """
    if rotation.is_cardinal:
        return
    for item in (*board.drawings, *board.trailing_drawings):
        if blocks_non_cardinal_rotation(item):
            raise ValueError(
                f"this board carries a {type(item).__name__}, which cannot take a "
                f"{rotation.angle} degree turn. KiCad converts a rectangle to a "
                "polygon for any angle that is not a multiple of 90 "
                "(eda_shape.cpp:957-972). Rotate by a multiple of 90 degrees."
            )


def rotate_board(board: Board, angle: float, center: Point = (0.0, 0.0)) -> None:
    """Turn every item on *board* by *angle* degrees about *center*, in place.

    Coverage matches :func:`translate_board`, plus the angle fields a turn
    changes: footprint placements, pad angles, text angles, board text-box
    angles, table-cell angles, barcode angles, and dimension text angles.

    A quarter turn is exact: KiCad's own ``RotatePoint`` takes a branch with no
    trigonometry for 0, 90, 180 and 270 degrees, so no coordinate picks up a
    rounding error. Any other angle takes the same trigonometric branch KiCad
    takes, with the same rounding, but it is rejected when the board carries a
    rectangle, because KiCad turns a non-cardinally rotated rectangle into a
    polygon and this library does not reproduce that tessellation.

    Cached glyph outlines are DROPPED, not turned. KiCad clears the same cache
    and rebuilds it from the outline font before it writes; this library has no
    font engine, so it writes no cache and KiCad rebuilds it on its next save.
    The cache is derived data and the file is valid without it.

    Raises:
        ValueError: when the turn is not a multiple of 90 degrees and the board
            carries a rectangle, or when a footprint has no placement.
    """
    rotation = Rotation(angle, center)
    _guard_board_rotation(board, rotation)
    _rotate_board(board, rotation)


def rotate_footprint(footprint: Footprint, angle: float, center: Point = (0.0, 0.0)) -> None:
    """Turn a board-context *footprint* by *angle* degrees about *center*, in place.

    The placement moves and turns, the pad and text angles take the turn, and
    every footprint-local position stays put. See :func:`rotate_board` for the
    quarter-turn rule and the render-cache rule.

    Raises:
        ValueError: when the footprint carries no ``at`` placement, or when the
            turn is not a multiple of 90 degrees and a board-frame child is a
            rectangle.
    """
    _rotate_footprint(footprint, Rotation(angle, center))


def rotate_items(items: Iterable[object], angle: float, center: Point = (0.0, 0.0)) -> None:
    """Turn a collection of BOARD-frame items by *angle* degrees about *center*.

    Accepts what :func:`translate_items` accepts.

    Raises:
        TypeError: when an item is not one this dispatch covers.
        ValueError: when the turn is not a multiple of 90 degrees and an item is
            a rectangle, or when a footprint carries no placement.
    """
    rotation = Rotation(angle, center)
    for item in items:
        if isinstance(item, Footprint):
            _rotate_footprint(item, rotation)
        elif isinstance(item, TrackSegment | TrackArc | Via):
            _move_track(item, rotation)
        elif isinstance(item, PcbGenerator):
            _move_generator(item, rotation)
        elif isinstance(item, PcbTarget):
            item.position = rotation(item.position)
        else:
            rotate_item(item, rotation)


__all__ = [
    "rotate_board",
    "rotate_footprint",
    "rotate_items",
    "set_footprint_orientation",
    "translate_board",
    "translate_footprint",
    "translate_items",
]


# ---------------------------------------------------------------------------
# Flip: a mirror plus a layer change
# ---------------------------------------------------------------------------
#
# KiCad writes Flip as Mirror plus a layer change for a shape, a zone and a
# dimension alike (pcb_shape.cpp:577-582, zone.cpp:1114-1142,
# pcb_dimension.cpp:576-581). A footprint is the exception: FOOTPRINT::Flip
# (footprint.cpp:2931-3000) is its own sequence and the ORDER is load-bearing.
#
# Source truth and the four measured exemplars:
# ``context/audit/pcb-flip-source-2026-09-11.md``.


#: A function that maps one layer name to the layer on the other side.
LayerMap = Callable[[str], str]


def _flip_local_y(position: Point) -> Point:
    """Negate the y of a footprint-LOCAL position, which is a mirror about y=0.

    ``0.0 + 0.0`` keeps a negated zero at plain zero. Python negates ``0.0`` to
    ``-0.0``, and the writer would emit ``-0``, which KiCad never writes.
    """
    return (position[0], -position[1] + 0.0)


def _flip_footprint_top_bottom(footprint: Footprint, center: Point, opposites: LayerMap) -> None:
    """The top-bottom half of a footprint flip, which every flip runs first.

    ``FOOTPRINT::Flip`` mirrors the placement Y whatever the requested
    direction, and reaches the requested direction through a half turn at the
    end (``footprint.cpp:2944-2996``). This is that first half.
    """
    at = footprint.at
    if at is None:
        raise ValueError(
            "flip_footprint needs a board-context footprint. This one has no "
            "(at ...) placement, so it is a library footprint with no board position."
        )
    at.position = Mirror("y", center)(at.position)
    at.angle = normalize_angle_180(-at.angle)
    if footprint.layer:
        footprint.layer = opposites(footprint.layer)

    for pad in footprint.pads:
        pad.position = _flip_local_y(pad.position)
        # Measured: the stored pad angle is board-absolute and comes back
        # negated. PAD::Flip negates the footprint-relative orientation while
        # the parent orientation reads zero (pad.cpp:1487), and the parent
        # orientation is itself negated, so the two cancel to a plain negation.
        pad.angle = -pad.angle
        if pad.rect_delta is not None:
            pad.rect_delta = _flip_local_y(pad.rect_delta)
        if pad.drill is not None and pad.drill.offset is not None:
            pad.drill.offset = _flip_local_y(pad.drill.offset)
        pad.layers = [opposites(name) for name in pad.layers]

    for field in footprint.properties:
        _flip_footprint_text(field, opposites)
    for item in footprint.drawings:
        if isinstance(item, BOARD_FRAME_TYPES):
            continue
        _flip_footprint_drawing(item, opposites)

    board_frame = Mirror("y", center)
    for child in footprint_board_frame_children(footprint):
        move_item(child, board_frame)
        if getattr(child, "layer", None):
            child.layer = opposites(child.layer)  # type: ignore[attr-defined]


def _flip_footprint_text(item: object, opposites: LayerMap) -> None:
    """Flip one footprint text or field, top to bottom.

    Three changes, from ``PCB_TEXT::Flip`` (``pcb_text.cpp:478-495``): the local
    position mirrors, the board-absolute angle becomes ``180 - angle``, and the
    ``mirror`` flag toggles because a footprint text is always side specific.
    """
    position = getattr(item, "position", None)
    if position is not None:
        item.position = _flip_local_y(position)  # type: ignore[attr-defined]
    angle = getattr(item, "angle", None)
    if angle is not None:
        item.angle = normalize_angle(180.0 - angle)  # type: ignore[attr-defined]
    layer = getattr(item, "layer", None)
    if layer:
        item.layer = opposites(layer)  # type: ignore[attr-defined]
    attributes = getattr(item, "attributes", None)
    if attributes is not None and hasattr(attributes, "mirror"):
        attributes.mirror = not attributes.mirror
    drop_render_cache(item)


def _flip_footprint_drawing(item: object, opposites: LayerMap) -> None:
    """Flip one footprint-local graphic, top to bottom, in the local frame.

    A text carries its own position, so it takes the text path alone. Sending it
    through both paths negates its y twice and leaves it where it started.
    """
    if isinstance(item, PcbText):
        _flip_footprint_text(item, opposites)
        return
    move_points(item, _flip_local_y)
    layer = getattr(item, "layer", None)
    if layer:
        item.layer = opposites(layer)  # type: ignore[attr-defined]
    layers = getattr(item, "layers", None)
    if layers:
        item.layers = [opposites(name) for name in layers]  # type: ignore[attr-defined]
    drop_render_cache(item)


def flip_footprint(
    footprint: Footprint,
    direction: FlipDirection = "left_right",
    center: Point | None = None,
    board: Board | None = None,
) -> None:
    """Flip *footprint* to the other side of the board, as ``FOOTPRINT::Flip`` does.

    Source: ``pcbnew/footprint.cpp:2931-3000``, measured against four exemplars
    the operator made in KiCad 10.0.4
    (``context/audit/pcb-flip-source-2026-09-11.md`` sections 4 and 8).

    ``direction`` takes KiCad's own two words. ``"left_right"`` is KiCad's
    shipped default, whatever the name of the settings key suggests.

    ``center`` defaults to the footprint's own placement, which is what KiCad
    uses for a single selected item (``edit_tool.cpp:2843-2849``). A footprint
    flipped about its own anchor does not move.

    ``board`` supplies the opposite-layer table. KiCad computes that table from
    the board, so two boards can send one layer to different places. Without a
    board the fixed table applies and no user layer pairs.

    The order below is the operation, not an implementation detail:

    1. Mirror the placement Y about ``center`` and negate the orientation.
       This runs for BOTH directions.
    2. Flip every child in the footprint-local frame, top to bottom.
    3. For ``"left_right"`` only, turn the whole footprint a half circle about
       ``center``. That turn carries ``KeepUpright``, which is why a locked
       field can come back at the angle it started from.

    Raises:
        ValueError: when the footprint carries no ``at`` placement.
    """
    if footprint.at is None:
        raise ValueError(
            "flip_footprint needs a board-context footprint. This one has no "
            "(at ...) placement, so it is a library footprint with no board position."
        )
    if direction not in FLIP_AXIS:
        raise ValueError(
            f"direction must be 'left_right' or 'top_bottom', not {direction!r}. "
            "These are KiCad's own two words for the flip direction."
        )
    if center is None:
        center = footprint.at.position

    if board is None:
        opposites: LayerMap = flip_layer_name
    else:
        table = board_layer_opposites(board)
        count = board_copper_layer_count(board)
        opposites = lambda name: table.get(name) or flip_layer_name(name, count)  # noqa: E731

    _flip_footprint_top_bottom(footprint, center, opposites)
    if direction == "left_right":
        _rotate_footprint(footprint, Rotation(180.0, center))
