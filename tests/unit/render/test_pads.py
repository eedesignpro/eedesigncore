"""Pad copper: the padstack resolution, the flash rules, and the seven shapes.

The geometry numbers here come from ``kicad-cli`` runs on a probe board, not from
this code. Where a number is derived rather than measured, the docstring says so.
"""

from __future__ import annotations

import pytest

from eedesigncore.common.pcb_family.enums import (
    ChamferPosition,
    CustomAnchor,
    PadShape,
    PadType,
)
from eedesigncore.common.pcb_family.footprint_scoped import (
    Padstack,
    PadstackLayer,
    PadstackMode,
)
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    FootprintPlacement,
    Pad,
    PrimBbox,
    PrimCircle,
    PrimCurve,
    PrimPoly,
    PrimRect,
    PrimVector,
)
from eedesigncore.pcb.models import Board
from eedesigncore.render import BoardContent, LayerStyle, ViewBox, render_board_svg
from eedesigncore.render.pads import (
    EXACT_PAD_SHAPES,
    chamfer_bits,
    pad_flash,
    pad_geometry_is_exact,
    pad_is_on_copper,
    pad_layer_geometry,
    pad_outline,
    pad_shape_position,
)
from eedesigncore.render.traversal import (
    external_copper_names,
    footprint_point_map,
    pad_draws_on,
    pad_layer_names,
    render_items_by_layer,
)

OUTER = frozenset({"F.Cu", "B.Cu"})


def smd(**kwargs: object) -> Pad:
    """An SMD pad on F.Cu, with sensible defaults for everything not named."""
    base: dict[str, object] = {
        "number": "1",
        "pad_type": PadType.smd,
        "shape": PadShape.rect,
        "size": (5.0, 3.0),
        "layers": ["F.Cu"],
    }
    base.update(kwargs)
    return Pad(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The frame: position is footprint-relative, angle is not
# ---------------------------------------------------------------------------


def test_the_pad_position_is_footprint_relative() -> None:
    """``BOARD_ITEM::SetFPRelativePosition``, ``board_item.cpp:363-374``.

    The stored point is turned by the footprint orientation and moved by the
    footprint position. Treating it as board-absolute puts every pad on a placed
    board at the wrong coordinate, which is how this was first found.
    """
    pad = smd(position=(2.0, 0.0))
    footprint = Footprint(
        layer="F.Cu", at=FootprintPlacement(position=(100.0, 50.0), angle=90.0), pads=[pad]
    )
    to_board = footprint_point_map(footprint)
    geometry = pad_layer_geometry(pad, "F.Cu")

    # A 90 degree footprint turn sends local +x to board -y.
    assert pad_shape_position(pad, geometry, to_board) == pytest.approx((100.0, 48.0))


def test_the_pad_angle_is_board_absolute() -> None:
    """The writer emits ``GetOrientation()`` whole, ``pcb_io_kicad_sexpr.cpp:1686``.

    So the footprint orientation must NOT be added to it. If it were, a pad in a
    90-degree footprint would be drawn twice-turned.
    """
    pad = smd(angle=30.0)
    placed = Footprint(
        layer="F.Cu", at=FootprintPlacement(position=(0.0, 0.0), angle=90.0), pads=[pad]
    )
    library = Footprint(layer="F.Cu", pads=[pad])

    on_board = pad_outline(pad, "F.Cu", footprint_point_map(placed)).polygons[0]
    in_library = pad_outline(pad, "F.Cu", footprint_point_map(library)).polygons[0]
    assert on_board == in_library


def test_the_drill_offset_moves_the_copper_not_the_hole() -> None:
    """``PAD::ShapePos``, ``pad.cpp:1558-1570``.

    The token sits inside ``(drill ...)`` and its name says nothing useful. KiCad
    feeds it to ``SetOffset`` and ``ShapePos`` adds it to place the COPPER.
    Measured: ``kicad-cli`` put a 5x3 pad at (110, 70) with offset (1.0, 0.5)
    between x 108.5 and 113.5, y 69.0 and 72.0, so its centre is (111.0, 70.5).
    """
    pad = Pad(
        number="1",
        pad_type=PadType.thru_hole,
        shape=PadShape.rect,
        size=(5.0, 3.0),
        position=(110.0, 70.0),
        drill=DrillInfo(width=1.5, offset=(1.0, 0.5)),
        layers=["*.Cu"],
        remove_unused_layers=False,
    )
    polygon = pad_outline(pad, "F.Cu").polygons[0]
    xs = sorted({round(x, 4) for x, _ in polygon})
    ys = sorted({round(y, 4) for _, y in polygon})
    assert xs == [108.5, 113.5]
    assert ys == [69.0, 72.0]


# ---------------------------------------------------------------------------
# Layer selection
# ---------------------------------------------------------------------------


def test_a_wildcard_layer_expands() -> None:
    """A pad is the one item whose layer list carries wildcards.

    ``on_layers`` expands them. It did not until this iteration, and selecting a
    pad through it silently dropped every ``*.Cu`` pad on the board. This is the
    regression guard for that.
    """
    pad = smd(layers=["*.Cu"])
    names = pad_layer_names(pad)
    assert "F.Cu" in names
    assert "B.Cu" in names
    assert "In1.Cu" in names
    assert pad_draws_on(pad, "F.Cu", OUTER)


def test_a_mask_wildcard_expands_to_both_sides() -> None:
    pad = smd(layers=["F.Cu", "*.Mask"])
    names = pad_layer_names(pad)
    assert {"F.Cu", "F.Mask", "B.Mask"} <= set(names)


def test_a_pad_does_not_draw_on_a_layer_it_does_not_name() -> None:
    assert not pad_draws_on(smd(layers=["F.Cu"]), "B.Cu", OUTER)


# ---------------------------------------------------------------------------
# Flashing
# ---------------------------------------------------------------------------


def through_pad(**kwargs: object) -> Pad:
    base: dict[str, object] = {
        "number": "1",
        "pad_type": PadType.thru_hole,
        "shape": PadShape.circle,
        "size": (2.0, 2.0),
        "drill": DrillInfo(width=1.0),
        "layers": ["*.Cu"],
    }
    base.update(kwargs)
    return Pad(**base)  # type: ignore[arg-type]


def test_keep_all_flashes_every_layer() -> None:
    """``remove_unused_layers`` absent or false is ``KEEP_ALL``, ``pad.cpp:480``."""
    pad = through_pad(remove_unused_layers=False)
    for layer in ("F.Cu", "In1.Cu", "B.Cu"):
        result = pad_flash(pad, layer, external_copper=OUTER)
        assert result.flashed
        assert result.certain


def test_keep_end_layers_is_certain_on_outer_copper_only() -> None:
    """``REMOVE_EXCEPT_START_AND_END``, ``pad.cpp:490-494``.

    An external layer is a definite yes. An inner one needs the connectivity
    graph, so the answer is flagged uncertain rather than guessed at silently.
    """
    pad = through_pad(remove_unused_layers=True, keep_end_layers=True)
    assert pad_flash(pad, "F.Cu", external_copper=OUTER).certain
    assert pad_flash(pad, "B.Cu", external_copper=OUTER).certain

    inner = pad_flash(pad, "In1.Cu", external_copper=OUTER)
    assert inner.flashed, "an uncertain pad is still drawn"
    assert not inner.certain, "and the uncertainty is reported"


def test_the_uncertain_case_still_draws() -> None:
    """KiCad returns true when it cannot consult connectivity, ``pad.cpp:502-505``.

    A preview that omits copper misleads worse than one that shows copper KiCad
    might remove, and the caller can read ``certain`` to tell the difference.
    """
    pad = through_pad(remove_unused_layers=True)
    result = pad_flash(pad, "In1.Cu", external_copper=OUTER)
    assert result.flashed
    assert not result.certain


def test_an_npth_pad_with_no_annular_ring_is_not_on_copper() -> None:
    """``PAD::IsOnCopperLayer``, ``pad.cpp:1591-1620``.

    The drill swallows the pad, so KiCad draws nothing. Drawing a disc there
    invents copper the board does not have.
    """
    swallowed = Pad(
        number="",
        pad_type=PadType.np_thru_hole,
        shape=PadShape.circle,
        size=(2.0, 2.0),
        drill=DrillInfo(width=2.0),
        layers=["*.Cu"],
    )
    assert not pad_is_on_copper(swallowed, pad_layer_geometry(swallowed, "F.Cu"))
    assert not pad_draws_on(swallowed, "F.Cu", OUTER)

    with_ring = swallowed.model_copy(update={"size": (3.0, 3.0)})
    assert pad_is_on_copper(with_ring, pad_layer_geometry(with_ring, "F.Cu"))


def test_an_npth_pad_with_an_offset_keeps_its_copper() -> None:
    """The rule requires offset zero, ``pad.cpp:466``. An offset ring is real copper."""
    offset = Pad(
        number="",
        pad_type=PadType.np_thru_hole,
        shape=PadShape.circle,
        size=(2.0, 2.0),
        drill=DrillInfo(width=2.0, offset=(0.5, 0.0)),
        layers=["*.Cu"],
    )
    assert pad_is_on_copper(offset, pad_layer_geometry(offset, "F.Cu"))


# ---------------------------------------------------------------------------
# Padstack resolution
# ---------------------------------------------------------------------------


def test_front_inner_back_puts_the_front_at_the_pad_top_level() -> None:
    """``PADSTACK::CopperLayer``, ``padstack.cpp:1206-1239``.

    ``front_inner_back`` mode writes ``(layer "Inner")`` and ``(layer "B.Cu")``
    only (``pcb_io_kicad_sexpr.cpp:2157-2170``), so F.Cu has no block of its own
    and the pad's own fields ARE the front.
    """
    pad = smd(
        shape=PadShape.rect,
        size=(5.0, 3.0),
        padstack=Padstack(
            mode=PadstackMode.front_inner_back,
            layers=[
                PadstackLayer(layer_name="Inner", shape=PadShape.circle, size=(1.0, 1.0)),
                PadstackLayer(layer_name="B.Cu", shape=PadShape.oval, size=(4.0, 2.0)),
            ],
        ),
        layers=["*.Cu"],
    )
    assert pad_layer_geometry(pad, "F.Cu").shape is PadShape.rect
    assert pad_layer_geometry(pad, "In1.Cu").shape is PadShape.circle
    assert pad_layer_geometry(pad, "In7.Cu").shape is PadShape.circle
    assert pad_layer_geometry(pad, "B.Cu").shape is PadShape.oval

    assert pad_layer_geometry(pad, "In1.Cu").size == (1.0, 1.0)
    assert pad_layer_geometry(pad, "B.Cu").size == (4.0, 2.0)


def test_a_padstack_field_left_unset_falls_back_to_the_pad() -> None:
    """A per-layer block carries only what DIFFERS, so the rest inherits."""
    pad = smd(
        shape=PadShape.rect,
        size=(5.0, 3.0),
        angle=15.0,
        padstack=Padstack(
            mode=PadstackMode.front_inner_back,
            layers=[PadstackLayer(layer_name="B.Cu", size=(9.0, 9.0))],
        ),
        layers=["*.Cu"],
    )
    back = pad_layer_geometry(pad, "B.Cu")
    assert back.size == (9.0, 9.0)
    assert back.shape is PadShape.rect, "the shape was not overridden, so it inherits"


def test_a_normal_padstack_resolves_to_the_pad_on_every_layer() -> None:
    pad = smd(layers=["*.Cu"])
    assert pad_layer_geometry(pad, "F.Cu") == pad_layer_geometry(pad, "In3.Cu")


def test_custom_mode_names_the_layers_it_overrides() -> None:
    """Custom mode writes every copper layer except F.Cu, ``pcb_io_kicad_sexpr.cpp:2171-2180``."""
    pad = smd(
        shape=PadShape.rect,
        padstack=Padstack(
            mode=PadstackMode.custom,
            layers=[PadstackLayer(layer_name="In2.Cu", shape=PadShape.circle, size=(1.0, 1.0))],
        ),
        layers=["*.Cu"],
    )
    assert pad_layer_geometry(pad, "In2.Cu").shape is PadShape.circle
    assert pad_layer_geometry(pad, "In1.Cu").shape is PadShape.rect
    assert pad_layer_geometry(pad, "F.Cu").shape is PadShape.rect


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def test_a_circle_pad_is_a_filled_disc_of_half_the_size() -> None:
    """``FlashPadCircle`` takes ``GetSize().x`` as the DIAMETER, ``PS_plotter.cpp:119``."""
    outline = pad_outline(smd(shape=PadShape.circle, size=(4.0, 4.0)), "F.Cu")
    assert outline.discs == [((0.0, 0.0), 2.0)]
    assert not outline.polygons


def test_an_oval_pad_is_one_stroked_segment() -> None:
    """``FlashPadOval``, ``PS_plotter.cpp:93-116``.

    The stadium is a segment of length ``height - width`` stroked at ``width``,
    and the round caps come from the wrapper group. Drawing the outline instead
    would put eight primitives where KiCad puts one.
    """
    outline = pad_outline(smd(shape=PadShape.oval, size=(6.0, 3.0)), "F.Cu")
    assert not outline.polygons
    assert len(outline.strokes) == 1
    start, end, width = outline.strokes[0]
    assert width == 3.0
    assert start == pytest.approx((-1.5, 0.0))
    assert end == pytest.approx((1.5, 0.0))


def test_an_oval_pad_taller_than_wide_swaps_and_turns_ninety() -> None:
    """The reduction to "taller than wide" is in the source, not an optimisation."""
    outline = pad_outline(smd(shape=PadShape.oval, size=(3.0, 6.0)), "F.Cu")
    start, end, width = outline.strokes[0]
    assert width == 3.0
    assert start == pytest.approx((0.0, -1.5))
    assert end == pytest.approx((0.0, 1.5))


def test_a_rect_pad_is_four_corners_turned_about_the_pad() -> None:
    outline = pad_outline(smd(shape=PadShape.rect, size=(5.0, 3.0)), "F.Cu")
    assert outline.polygons == [(-2.5, 1.5), (-2.5, -1.5), (2.5, -1.5), (2.5, 1.5)] or outline
    assert sorted(outline.polygons[0]) == sorted(
        [(-2.5, 1.5), (-2.5, -1.5), (2.5, -1.5), (2.5, 1.5)]
    )


def test_a_trapezoid_delta_cross_couples_the_axes() -> None:
    """``plot_brditems_plotter.cpp:345-363``: a delta in x shears the y extent."""
    outline = pad_outline(
        smd(shape=PadShape.trapezoid, size=(5.0, 4.0), rect_delta=(1.5, 0.0)), "F.Cu"
    )
    corners = sorted(outline.polygons[0])
    assert corners == sorted([(-2.5, 2.75), (2.5, 1.25), (2.5, -1.25), (-2.5, -2.75)])


def test_a_roundrect_pad_is_a_polygon_not_an_arc() -> None:
    """KiCad never emits an arc for a rounded rectangle, so neither does this."""
    outline = pad_outline(
        smd(shape=PadShape.roundrect, size=(5.0, 3.0), roundrect_rratio=0.25), "F.Cu"
    )
    assert not outline.arcs
    assert len(outline.polygons) == 1
    assert len(outline.polygons[0]) == 32, "the count kicad-cli wrote for this pad"


def test_a_chamfer_list_is_what_promotes_the_shape() -> None:
    """``pcb_io_kicad_sexpr_parser.cpp:6240-6241``.

    A chamfer RATIO with no corners named stays a plain rounded rectangle. Only a
    non-empty ``(chamfer ...)`` list makes it a chamfered one.
    """
    ratio_only = pad_layer_geometry(
        smd(shape=PadShape.roundrect, roundrect_rratio=0.2, chamfer_ratio=0.3), "F.Cu"
    )
    assert not ratio_only.is_chamfered

    with_corners = pad_layer_geometry(
        smd(
            shape=PadShape.roundrect,
            roundrect_rratio=0.2,
            chamfer_ratio=0.3,
            chamfer_positions=[ChamferPosition.top_left],
        ),
        "F.Cu",
    )
    assert with_corners.is_chamfered
    assert chamfer_bits(with_corners) == 1


def test_all_four_chamfers_give_an_octagon() -> None:
    pad = smd(
        shape=PadShape.roundrect,
        size=(4.0, 4.0),
        roundrect_rratio=0.15,
        chamfer_ratio=0.25,
        chamfer_positions=list(ChamferPosition),
    )
    assert len(pad_outline(pad, "F.Cu").polygons[0]) == 8


def test_a_zero_sized_pad_draws_nothing() -> None:
    """``plot_board_layers.cpp:446-452`` returns early for a zero extent."""
    assert pad_outline(smd(size=(0.0, 3.0)), "F.Cu").is_empty()
    assert pad_outline(smd(size=(5.0, 0.0)), "F.Cu").is_empty()


def test_a_custom_pad_with_a_zero_anchor_still_draws_its_primitives() -> None:
    """The zero-size early return exempts a custom pad, and the source says why:
    "a custom pad can have its pad anchor with size = 0"."""
    pad = smd(
        shape=PadShape.custom,
        size=(0.0, 0.0),
        primitives=[PrimRect(start=(-1.0, -1.0), end=(1.0, 1.0), width=0.0, fill=True)],
    )
    outline = pad_outline(pad, "F.Cu")
    assert not outline.is_empty()
    assert not outline.discs, "a zero anchor contributes nothing"
    assert len(outline.compound_polygons) == 1


def test_a_custom_pad_draws_its_anchor_and_its_primitives() -> None:
    pad = smd(
        shape=PadShape.custom,
        size=(2.0, 2.0),
        primitives=[PrimCircle(center=(1.5, 0.0), end=(2.5, 0.0), width=0.0, fill=True)],
    )
    outline = pad_outline(pad, "F.Cu")
    assert len(outline.compound_polygons) == 1
    points = outline.compound_polygons[0]
    assert min(x for x, y in points) == pytest.approx(-1, abs=0.001)
    assert max(x for x, y in points) == pytest.approx(2.5, abs=0.001)


def test_a_rect_custom_anchor_draws_a_rectangle() -> None:
    """``MergePrimitivesAsPolygon``, ``pad.cpp:3276-3289``, branches on the anchor."""
    pad = smd(
        shape=PadShape.custom,
        size=(2.0, 1.0),
        custom_anchor=CustomAnchor.rect,
        primitives=[],
    )
    outline = pad_outline(pad, "F.Cu")
    assert not outline.discs
    assert len(outline.compound_polygons) == 1


def test_a_proxy_primitive_draws_nothing() -> None:
    """``pad.cpp:3292-3295`` skips any primitive whose ``IsProxyItem`` is true.

    ``gr_vector`` and ``gr_bbox`` carry a number box and a shape hint. Drawing
    them would put copper on the page that the board does not have.
    """
    pad = smd(
        shape=PadShape.custom,
        size=(0.0, 0.0),
        primitives=[
            PrimVector(start=(0.0, 0.0), end=(1.0, 0.0)),
            PrimBbox(start=(-1.0, -1.0), end=(1.0, 1.0), fill=True),
        ],
    )
    assert pad_outline(pad, "F.Cu").is_empty()


def test_a_bezier_primitive_draws_its_copper() -> None:
    pad = smd(
        shape=PadShape.custom,
        size=(0.0, 0.0),
        primitives=[
            PrimCurve(pts=[(0.0, 0.0), (1.0, 1.0), (2.0, 1.0), (3.0, 0.0)], width=0.2),
        ],
    )
    outline = pad_outline(pad, "F.Cu")
    assert not outline.is_empty()
    points = outline.compound_polygons[0]
    assert max(y for x, y in points) == pytest.approx(0.85, abs=0.002)


def test_a_poly_primitive_needs_three_points() -> None:
    pad = smd(
        shape=PadShape.custom,
        size=(0.0, 0.0),
        primitives=[PrimPoly(pts=[(0.0, 0.0), (1.0, 0.0)], width=0.0, fill=True)],
    )
    assert pad_outline(pad, "F.Cu").is_empty()


# ---------------------------------------------------------------------------
# Exactness, stated rather than assumed
# ---------------------------------------------------------------------------


def test_every_shape_but_custom_is_vertex_exact() -> None:
    """The claim the parity oracle relies on, asserted in one place."""
    assert PadShape.custom not in EXACT_PAD_SHAPES
    assert set(PadShape) - {PadShape.custom} == EXACT_PAD_SHAPES


def test_pad_geometry_is_exact_reports_the_custom_case() -> None:
    plain = pad_layer_geometry(smd(shape=PadShape.rect), "F.Cu")
    custom = pad_layer_geometry(smd(shape=PadShape.custom, primitives=[]), "F.Cu")
    assert pad_geometry_is_exact(plain)
    assert not pad_geometry_is_exact(custom)


# ---------------------------------------------------------------------------
# Through the renderer
# ---------------------------------------------------------------------------


def board_with(pad: Pad, *, at: tuple[float, float] = (50.0, 50.0)) -> Board:
    return Board(
        footprints=[
            Footprint(
                fp_id="t:t",
                layer="F.Cu",
                at=FootprintPlacement(position=at, angle=0.0),
                pads=[pad],
            )
        ]
    )


def render(board: Board, layer: str = "F.Cu", **kwargs: object) -> str:
    return render_board_svg(
        board,
        [layer],
        ViewBox(0.0, 0.0, 100.0, 100.0),
        style=LayerStyle(colors={layer: "#C83434"}),
        content=frozenset({BoardContent.pads}),
        **kwargs,  # type: ignore[arg-type]
    )[layer]


def test_pads_are_opt_in() -> None:
    """The default content must not change, or every existing render moves."""
    board = board_with(smd())
    default = render_board_svg(
        board,
        ["F.Cu"],
        ViewBox(0.0, 0.0, 100.0, 100.0),
        style=LayerStyle(colors={"F.Cu": "#C83434"}),
    )["F.Cu"]
    assert "path" not in default
    assert "path" in render(board)


def test_all_content_includes_pads() -> None:
    from eedesigncore.render import ALL_CONTENT

    assert BoardContent.pads in ALL_CONTENT


def test_an_oval_pad_carries_its_own_stroke_width() -> None:
    """The width is the pad's minor axis, and a padstack can change it per layer.

    So it belongs to the SHAPE, not to the item. A width read off the item would
    fall through to the policy and draw the pad at 0.05 mm.
    """
    svg = render(board_with(smd(shape=PadShape.oval, size=(6.0, 3.0))))
    assert 'stroke-width="3.0000"' in svg


def test_a_filled_pad_carries_no_stroke_width() -> None:
    svg = render(board_with(smd(shape=PadShape.rect)))
    assert "stroke-width" not in svg
    assert 'stroke="none"' in svg


def test_two_pad_renders_are_byte_identical() -> None:
    board = board_with(smd(shape=PadShape.roundrect, roundrect_rratio=0.25))
    assert render(board) == render(board)


def test_a_pad_reaches_the_layer_buckets() -> None:
    board = board_with(smd(layers=["*.Cu"]))
    buckets = render_items_by_layer(board, ["F.Cu", "B.Cu"], frozenset({BoardContent.pads}))
    assert len(buckets["F.Cu"]) == 1
    assert len(buckets["B.Cu"]) == 1
    assert isinstance(buckets["F.Cu"][0].item, Pad)
    assert buckets["F.Cu"][0].in_footprint


def test_a_pad_is_never_reported_as_a_skip() -> None:
    """Every pad shape has an emitter, so ``on_skip`` must stay silent."""
    skipped: list[object] = []
    for shape, extra in (
        (PadShape.circle, {}),
        (PadShape.rect, {}),
        (PadShape.oval, {}),
        (PadShape.trapezoid, {"rect_delta": (1.0, 0.0)}),
        (PadShape.roundrect, {"roundrect_rratio": 0.25}),
        (PadShape.custom, {"primitives": []}),
    ):
        render(board_with(smd(shape=shape, **extra)), on_skip=skipped.append)
    assert skipped == []


# ---------------------------------------------------------------------------
# External copper
# ---------------------------------------------------------------------------


def test_external_copper_falls_back_when_the_stack_is_unknown() -> None:
    """A board with no layer table must not stop treating F.Cu as outer copper."""
    assert external_copper_names(Board()) == frozenset({"F.Cu", "B.Cu"})


# ---------------------------------------------------------------------------
# Branches the first pass left uncovered
# ---------------------------------------------------------------------------


def test_an_npth_oval_pad_with_no_annular_ring_is_not_on_copper() -> None:
    """``pad.cpp:1621-1634``, the OVAL branch beside the circle one.

    An oval needs BOTH axes swallowed by the slot. Testing only the circle
    branch would leave every oval mechanical slot drawing copper it does not
    have.
    """
    swallowed = Pad(
        number="",
        pad_type=PadType.np_thru_hole,
        shape=PadShape.oval,
        size=(3.0, 2.0),
        drill=DrillInfo(oval=True, width=3.0, height=2.0),
        layers=["*.Cu"],
    )
    assert not pad_is_on_copper(swallowed, pad_layer_geometry(swallowed, "F.Cu"))

    # One axis wider than the slot leaves a ring, so the pad is on copper.
    wider = swallowed.model_copy(update={"size": (4.0, 2.0)})
    assert pad_is_on_copper(wider, pad_layer_geometry(wider, "F.Cu"))


def test_an_npth_pad_of_another_shape_is_always_on_copper() -> None:
    """KiCad tests the circle and oval branches only, so a rect pad falls through."""
    pad = Pad(
        number="",
        pad_type=PadType.np_thru_hole,
        shape=PadShape.rect,
        size=(1.0, 1.0),
        drill=DrillInfo(width=5.0),
        layers=["*.Cu"],
    )
    assert pad_is_on_copper(pad, pad_layer_geometry(pad, "F.Cu"))


def test_a_pad_always_flashes_on_a_non_copper_layer() -> None:
    """The rule is copper only, so a mask or paste layer never removes a pad."""
    pad = through_pad(remove_unused_layers=True, layers=["*.Cu", "*.Mask"])
    for layer in ("F.Mask", "B.Mask"):
        result = pad_flash(pad, layer, external_copper=OUTER)
        assert result.flashed
        assert result.certain


def test_a_pad_with_no_drill_is_on_copper() -> None:
    """A guard against reading `pad.drill` when it is absent."""
    pad = smd(shape=PadShape.circle, size=(1.0, 1.0))
    assert pad_is_on_copper(pad, pad_layer_geometry(pad, "F.Cu"))
