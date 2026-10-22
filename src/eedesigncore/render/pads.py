"""Pad copper, one layer at a time.

A pad is the only board item whose geometry changes per layer. Everything else
carries one shape and a layer list; a pad carries a PADSTACK, so the same pad can
be a rounded rectangle on the front, a circle on the inner layers and absent from
the back. Resolving that is half of this module. The other half emits the seven
shapes.

What KiCad plots, and therefore what this module reproduces:

- ``PlotStandardLayer``, ``plot_board_layers.cpp:355-648``, picks the pads and
  the layers.
- ``BRDITEMS_PLOTTER::PlotPad``, ``plot_brditems_plotter.cpp:151-388``, switches
  on the resolved shape.
- ``PSLIKE_PLOTTER::FlashPad*``, ``PS_plotter.cpp:93-215``, turns each shape into
  SVG geometry. ``SVG_PLOTTER`` inherits every one of them unchanged.

Two facts about that path shape this module:

1. **An oval pad is a stroked segment, not an outline.** ``FlashPadOval`` reduces
   the pad to its minor axis and calls ``ThickSegment``. With the round caps the
   wrapper group already carries, one stroked line IS the stadium. Drawing the
   outline instead would put eight primitives on the page where KiCad puts one.
2. **A rounded or chamfered rectangle is a POLYGON.** KiCad never emits an arc
   for one. :mod:`eedesigncore.common.polygon` holds that approximation, checked
   vertex for vertex against ``kicad-cli``.

Custom pads merge their anchor and primitives into filled polygons on every layer.
Curve flattening and rounded offsets use a one-micron tolerance.
Their vertices can differ from KiCad, so :func:`pad_geometry_is_exact` remains false.

"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from eedesigncore.common.kimath import kiround, rotate_point_iu
from eedesigncore.common.layers import is_copper_layer_name
from eedesigncore.common.pcb_family.enums import (
    ChamferPosition,
    PadShape,
    PadType,
)
from eedesigncore.common.polygon import (
    PLOTTER_ARC_HIGH_DEF_IU,
    RECT_CHAMFER_BOTTOM_LEFT,
    RECT_CHAMFER_BOTTOM_RIGHT,
    RECT_CHAMFER_TOP_LEFT,
    RECT_CHAMFER_TOP_RIGHT,
    round_chamfered_rect_polygon,
)
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.mod.models import (
    Pad,
)
from eedesigncore.mod.pad_geometry import (
    PadLayerGeometry,
    PointMap,
    identity_map,
    pad_layer_geometry,
    pad_shape_position,
)

#: Bit per ``ChamferPosition``, matching ``eda_shape.h``'s ``RECT_CHAMFER_*``.
CHAMFER_BITS = {
    ChamferPosition.top_left: RECT_CHAMFER_TOP_LEFT,
    ChamferPosition.top_right: RECT_CHAMFER_TOP_RIGHT,
    ChamferPosition.bottom_left: RECT_CHAMFER_BOTTOM_LEFT,
    ChamferPosition.bottom_right: RECT_CHAMFER_BOTTOM_RIGHT,
}

#: Pad shapes whose SVG geometry matches ``kicad-cli`` vertex for vertex.
#:
#: ``custom`` is absent, and that absence is the honest part. See the module
#: docstring and :func:`pad_geometry_is_exact`.
EXACT_PAD_SHAPES = frozenset(
    {
        PadShape.circle,
        PadShape.rect,
        PadShape.oval,
        PadShape.trapezoid,
        PadShape.roundrect,
    }
)


def chamfer_bits(geometry: PadLayerGeometry) -> int:
    """The corner bitmask ``TransformRoundChamferedRectToPolygon`` expects."""
    bits = 0
    for corner in geometry.chamfer_positions:
        bits |= CHAMFER_BITS.get(corner, 0)
    return bits


def pad_geometry_is_exact(geometry: PadLayerGeometry) -> bool:
    """True when this module's SVG matches ``kicad-cli`` vertex for vertex.

    A caller comparing against ``kicad-cli`` filters on this. A caller drawing a
    preview ignores it.
    """
    return geometry.shape in EXACT_PAD_SHAPES


def pad_is_on_copper(pad: Pad, geometry: PadLayerGeometry) -> bool:
    """``PAD::IsOnCopperLayer``, ``pad.cpp:1591-1620``.

    A non-plated hole with no annular ring is not on copper at all: the drill
    swallows the whole pad, so KiCad draws nothing and a preview that draws a
    disc there invents copper.
    """
    if pad.pad_type is not PadType.np_thru_hole:
        return True
    if pad.drill is None:
        return True

    if geometry.shape is PadShape.circle:
        return not (geometry.offset == (0.0, 0.0) and geometry.size[0] <= pad.drill.width)
    if geometry.shape is PadShape.oval:
        height = pad.drill.height if pad.drill.oval and pad.drill.height else pad.drill.width
        return not (
            geometry.offset == (0.0, 0.0)
            and geometry.size[0] <= pad.drill.width
            and geometry.size[1] <= height
        )
    return True


@dataclass(frozen=True)
class PadFlash:
    """Whether a pad puts copper on one layer, and whether the answer is certain.

    ``certain`` is ``False`` for exactly one case: a plated through-hole pad with
    ``(remove_unused_layers yes)`` on an INNER layer. KiCad answers that from the
    board's connectivity graph (``PAD::FlashLayer`` →
    ``CONNECTIVITY::IsConnectedOnLayer``, ``pad.cpp:496-517``), and this library
    derives connectivity for schematics only. The renderer draws such a pad,
    matching what KiCad itself returns when it cannot consult connectivity
    (``aOnlyCheckIfPermitted``, ``pad.cpp:502-505``), because a preview missing
    copper misleads worse than one showing copper KiCad might remove.
    """

    flashed: bool
    certain: bool = True


def pad_flash(pad: Pad, layer: str, *, external_copper: frozenset[str]) -> PadFlash:
    """``PAD::FlashLayer( int, bool )``, ``pad.cpp:439-519``.

    *external_copper* names the layers this board treats as outer copper, which
    is ``F.Cu`` and ``B.Cu`` on every board with two or more copper layers.
    """
    if not is_copper_layer_name(layer):
        return PadFlash(True)

    if pad.pad_type is PadType.np_thru_hole:
        return PadFlash(pad_is_on_copper(pad, pad_layer_geometry(pad, layer)))

    if pad.pad_type is not PadType.thru_hole:
        return PadFlash(True)

    # remove_unused_layers absent or false is KEEP_ALL.
    if not pad.remove_unused_layers:
        return PadFlash(True)

    if pad.keep_end_layers and layer in external_copper:
        return PadFlash(True)

    return PadFlash(True, certain=False)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


#: A stadium: start, end, and the width it is stroked at.
PadStroke = tuple[tuple[float, float], tuple[float, float], float]
#: An arc primitive: start, mid, end, and the width it is stroked at.
PadArc = tuple[tuple[float, float], tuple[float, float], tuple[float, float], float]


@dataclass
class PadOutline:
    """The geometry of one pad on one layer, in millimeters.

    ``polygons`` are closed filled rings. ``discs`` are filled circles.
    ``strokes`` are (start, end, width) stadiums, which is how KiCad draws an
    oval pad and a line primitive.
    """

    polygons: list[list[tuple[float, float]]] = field(default_factory=list)
    discs: list[tuple[tuple[float, float], float]] = field(default_factory=list)
    strokes: list[PadStroke] = field(default_factory=list)
    arcs: list[PadArc] = field(default_factory=list)
    compound_polygons: list[list[tuple[float, float]]] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True when nothing would be drawn."""
        return not (
            self.polygons or self.discs or self.strokes or self.arcs or self.compound_polygons
        )


def pad_outline(
    pad: Pad,
    layer: str,
    to_board: PointMap = identity_map,
    *,
    margin: tuple[float, float] | None = None,
) -> PadOutline:
    """Return the copper of *pad* on *layer*, in board millimeters.

    ``margin`` gives per-side X/Y aperture clearances. When omitted, pad-local
    overrides apply. The board renderer also supplies footprint and board defaults.

    *to_board* maps the host footprint's frame onto the board. A library pad with
    no host passes the identity.

    The result is geometry only. Nothing here decides whether the pad belongs on
    the layer; :func:`pad_flash` answers that.
    """
    from eedesigncore.render.apertures import pad_aperture_margin
    from eedesigncore.render.pad_polygons import custom_polygons, offset_polygons, union_polygons

    geometry = pad_layer_geometry(pad, layer)
    if margin is None:
        margin = pad_aperture_margin(pad, layer)
    centre = pad_shape_position(pad, geometry, to_board)
    out = PadOutline()
    if margin != (0.0, 0.0) or geometry.shape is PadShape.custom:
        size = point_to_iu(geometry.size)
        clearance = point_to_iu(margin)
        expanded = (to_mm(size[0] + 2 * clearance[0]), to_mm(size[1] + 2 * clearance[1]))
        if geometry.shape is not PadShape.custom and min(expanded) <= 0:
            return out
        polygon_offset = geometry.shape in {PadShape.custom, PadShape.trapezoid} or (
            geometry.is_chamfered and clearance[0] > 0 and clearance[0] == clearance[1]
        )
        if polygon_offset:
            if geometry.shape is PadShape.custom:
                polygons = custom_polygons(geometry)
            elif geometry.shape is PadShape.trapezoid:
                polygons = [
                    [
                        point_to_iu(point)
                        for point in _trapezoid_polygon(
                            (0.0, 0.0), geometry.size, geometry.rect_delta, 0
                        )
                    ]
                ]
            else:
                polygons = [
                    [point_to_iu(point) for point in _roundrect_polygon((0.0, 0.0), geometry, 0)]
                ]
            polygons = offset_polygons(polygons, clearance[0])
            if geometry.shape is PadShape.custom and clearance[0] < 0:
                anchor = replace(
                    geometry, primitives=(), size=(max(0, expanded[0]), max(0, expanded[1]))
                )
                polygons = union_polygons(polygons + custom_polygons(anchor))
            out.compound_polygons = [
                [_placed(point_to_iu(centre), point, pad.angle) for point in polygon]
                for polygon in polygons
            ]
            return out
        if (
            geometry.shape is PadShape.roundrect
            and not geometry.is_chamfered
            and clearance[0] == clearance[1]
        ):
            radius = max(0, kiround(min(size) * geometry.roundrect_rratio) + clearance[0])
            geometry = replace(geometry, roundrect_rratio=min(0.5, to_mm(radius) / min(expanded)))
        geometry = replace(geometry, size=expanded)
        if geometry.shape is PadShape.rect and clearance[0] > 0:
            geometry = replace(
                geometry,
                shape=PadShape.roundrect,
                roundrect_rratio=min(0.5, margin[0] / min(expanded)),
            )

    # A zero-sized pad draws nothing, except a custom one whose anchor may be
    # zero while its primitives carry the shape (plot_board_layers.cpp:446-452).
    if geometry.shape is not PadShape.custom and (
        geometry.size[0] <= 0.0 or geometry.size[1] <= 0.0
    ):
        return out

    if geometry.shape is PadShape.circle:
        out.discs.append((centre, geometry.size[0] / 2.0))
    elif geometry.shape is PadShape.oval:
        out.strokes.append(_oval_stroke(centre, geometry.size, pad.angle))
    elif geometry.shape is PadShape.rect:
        out.polygons.append(_rect_polygon(centre, geometry.size, pad.angle))
    elif geometry.shape is PadShape.trapezoid:
        out.polygons.append(
            _trapezoid_polygon(centre, geometry.size, geometry.rect_delta, pad.angle)
        )
    elif geometry.shape is PadShape.roundrect:
        out.polygons.append(_roundrect_polygon(centre, geometry, pad.angle))

    return out


def _oval_stroke(
    centre: tuple[float, float], size: tuple[float, float], angle: float
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """``PSLIKE_PLOTTER::FlashPadOval``, ``PS_plotter.cpp:93-116``.

    The pad is first reduced to "taller than wide" by swapping the axes and
    turning a further 90 degrees. The stadium is then one segment of length
    ``height - width`` stroked at ``width``.
    """
    width, height = size
    if width > height:
        width, height = height, width
        angle += 90.0

    delta = height - width
    half = point_to_iu((0.0, delta / 2.0))[1]
    origin = point_to_iu(centre)
    first = rotate_point_iu((0, -half), angle)
    second = rotate_point_iu((0, half), angle)
    return (
        (to_mm(origin[0] + first[0]), to_mm(origin[1] + first[1])),
        (to_mm(origin[0] + second[0]), to_mm(origin[1] + second[1])),
        width,
    )


def _rect_polygon(
    centre: tuple[float, float], size: tuple[float, float], angle: float
) -> list[tuple[float, float]]:
    """``PSLIKE_PLOTTER::FlashPadRect``, ``PS_plotter.cpp:125-155``.

    The corners are built around the pad position in integer units and each one
    is turned about that position, which is the order KiCad uses. Halving the
    size truncates toward zero, so an odd nanometre count lands where KiCad's
    does.
    """
    origin = point_to_iu(centre)
    full = point_to_iu(size)
    dx = int(full[0] / 2)
    dy = int(full[1] / 2)
    corners = [(-dx, dy), (-dx, -dy), (dx, -dy), (dx, dy)]
    return [_placed(origin, corner, angle) for corner in corners]


def _trapezoid_polygon(
    centre: tuple[float, float],
    size: tuple[float, float],
    rect_delta: tuple[float, float],
    angle: float,
) -> list[tuple[float, float]]:
    """The four corners ``PlotPad`` builds for a trapezoid.

    ``plot_brditems_plotter.cpp:345-363``, then
    ``PSLIKE_PLOTTER::FlashPadTrapez`` turns them and moves them
    (``PS_plotter.cpp:203-217``). The delta cross-couples the axes: a delta in x
    shears the y extent and the other way about.
    """
    origin = point_to_iu(centre)
    full = point_to_iu(size)
    delta = point_to_iu(rect_delta)
    half_x = int(full[0] / 2)
    half_y = int(full[1] / 2)
    dx = int(delta[0] / 2)
    dy = int(delta[1] / 2)
    corners = [
        (-half_x - dy, half_y + dx),
        (half_x + dy, half_y - dx),
        (half_x - dy, -half_y + dx),
        (-half_x + dy, -half_y - dx),
    ]
    return [_placed(origin, corner, angle) for corner in corners]


def _roundrect_polygon(
    centre: tuple[float, float], geometry: PadLayerGeometry, angle: float
) -> list[tuple[float, float]]:
    """``PSLIKE_PLOTTER::FlashPadRoundRect``, ``PS_plotter.cpp:158-179``.

    A chamfered rectangle reaches here too. ``PlotPad`` sends it to the Gerber
    writer's own aperture only when the plotter IS Gerber, and falls through to
    the polygon otherwise (``plot_brditems_plotter.cpp:366-380``).

    The radius is the SHORTER side times the ratio, rounded
    (``PADSTACK::RoundRectRadius``, ``padstack.cpp:953-957``).
    """
    size = point_to_iu(geometry.size)
    radius = kiround(min(size[0], size[1]) * geometry.roundrect_rratio)
    chamfered = geometry.is_chamfered
    polygon = round_chamfered_rect_polygon(
        size,
        corner_radius=radius,
        chamfer_ratio=geometry.chamfer_ratio if chamfered else 0.0,
        chamfer_corners=chamfer_bits(geometry) if chamfered else 0,
        angle_degrees=angle,
        position=point_to_iu(centre),
        error=PLOTTER_ARC_HIGH_DEF_IU,
    )
    return [(to_mm(x), to_mm(y)) for x, y in polygon]


def _placed(origin: tuple[int, int], corner: tuple[int, int], angle: float) -> tuple[float, float]:
    """Turn *corner* about the pad position and return board millimeters."""
    turned = rotate_point_iu(corner, angle)
    return (to_mm(origin[0] + turned[0]), to_mm(origin[1] + turned[1]))


def pad_pen_width(pad: Pad, layer: str, to_board: PointMap = identity_map) -> float:
    """The stroke width a stadium-shaped pad draws with, in millimeters.

    Only an oval pad and a line primitive have one; every other pad shape is a
    filled area. Returning zero says "this pad has no stroked part".
    """
    outline = pad_outline(pad, layer, to_board)
    if not outline.strokes:
        return 0.0
    return max(width for _, _, width in outline.strokes)


__all__ = [
    "CHAMFER_BITS",
    "chamfer_bits",
    "EXACT_PAD_SHAPES",
    "PadArc",
    "PadFlash",
    "PadLayerGeometry",
    "PadOutline",
    "PointMap",
    "PadStroke",
    "identity_map",
    "pad_flash",
    "pad_geometry_is_exact",
    "pad_is_on_copper",
    "pad_layer_geometry",
    "pad_outline",
    "pad_pen_width",
    "pad_shape_position",
]
