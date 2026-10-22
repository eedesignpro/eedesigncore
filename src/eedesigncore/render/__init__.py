"""Draw boards, footprints, and symbols to SVG from memory.

The entry points are :func:`render_board_svg`, :func:`render_footprint_svg`,
and :func:`render_symbol_svg`. They perform no file access and start no subprocess.

SVG is an output format, not a KiCad file. The byte-identity rule that governs
``.kicad_pcb`` does not apply here, because nobody diffs a generated preview.
The rule here is determinism: one board and one set of arguments always give the
same bytes. Agreement with ``kicad-cli`` is a test oracle. See ADR-098.

The module splits into four layers. Keep new capability in the top two.

- :mod:`eedesigncore.render.geometry` maps millimeters to SVG. FROZEN.
- :mod:`eedesigncore.render.pads` resolves a padstack and emits pad copper.
- :mod:`eedesigncore.render.traversal` finds items and maps hosts to the board.
- :mod:`eedesigncore.render.style` owns color, opacity, filtering and identity.
- :mod:`eedesigncore.render.document` assembles the page.

Example::

    from eedesigncore.pcb.models import Board
    from eedesigncore.render import LayerStyle, ViewBox, render_board_svg

    board = Board.load("board.kicad_pcb")
    style = LayerStyle(colors={"F.SilkS": "#FFFFFF", "Edge.Cuts": "#D0D000"})
    pages = render_board_svg(
        board,
        ["F.SilkS", "Edge.Cuts"],
        ViewBox(0.0, 0.0, 297.0, 210.0),
        style=style,
    )
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence

from eedesigncore.common.font import TextMetrics
from eedesigncore.common.pcb_family.shapes import PcbTextBox
from eedesigncore.mod.models import Footprint, Pad
from eedesigncore.pcb.models import Board, PlotParams, Setup
from eedesigncore.render.apertures import pad_aperture_margin
from eedesigncore.render.board_extras import extra_shapes
from eedesigncore.render.board_text import pcb_text_shapes
from eedesigncore.render.bounds import PlotFontRequest, PlotTextContext, _shown
from eedesigncore.render.document import (
    ViewBox,
    build_document,
    escape_attr,
    render_element,
)
from eedesigncore.render.drawing_sheet import render_drawing_sheet_svg
from eedesigncore.render.fonts import OutlineFont, load_font
from eedesigncore.render.geometry import (
    DEFAULT_PRECISION,
    SvgShape,
    format_mm,
    hole_shapes,
    shapes_for,
)
from eedesigncore.render.pads import (
    EXACT_PAD_SHAPES,
    PadFlash,
    PadLayerGeometry,
    PadOutline,
    pad_flash,
    pad_geometry_is_exact,
    pad_layer_geometry,
    pad_outline,
)
from eedesigncore.render.schematics import SchematicStyle, render_schematic_svg
from eedesigncore.render.style import (
    DEFAULT_POINT_STROKE_MM,
    CliParityStyle,
    ElementStyle,
    LayerStyle,
    StylePolicy,
)
from eedesigncore.render.symbols import SymbolFont, SymbolStyle, render_symbol_svg
from eedesigncore.render.text_boxes import pcb_text_box_shapes
from eedesigncore.render.traversal import (
    ALL_CONTENT,
    DEFAULT_CONTENT,
    HOLE_LAYERS,
    HOLES,
    HOLES_NON_PLATED,
    HOLES_PLATED,
    KICAD_CONTENT,
    BoardContent,
    Hole,
    RenderItem,
    external_copper_names,
    identity_map,
    is_rule_area,
    iter_holes,
    pad_draws_on,
    pad_layer_names,
    render_items_by_layer,
)
from eedesigncore.render.vias import (
    DEFAULT_VIA_DIAMETER,
    ViaDiameter,
    via_diameter_mm,
    via_flash,
)
from eedesigncore.text_resolution import TextResolutionContext, resolve_document_text

#: Item types this renderer cannot draw yet. A caller sees them through
#: ``on_skip``. Text, dimensions and anything else needing font metrics stay out
#: on purpose: they are what keeps this feature small and testable.
SkipReport = Callable[[RenderItem], None]


def render_board_svg(
    board: Board,
    layers: Sequence[str],
    view_box: ViewBox,
    *,
    style: StylePolicy,
    content: Collection[BoardContent] = DEFAULT_CONTENT,
    font: TextMetrics | None = None,
    via_diameter: ViaDiameter = DEFAULT_VIA_DIAMETER,
    precision: int = DEFAULT_PRECISION,
    on_skip: SkipReport | None = None,
    on_element: Callable[[RenderItem, str], None] | None = None,
    text_context: TextResolutionContext | None = None,
    plot_text_context: PlotTextContext | None = None,
) -> dict[str, str]:
    """Draw *board* to one SVG document per layer.

    :param board: the board to draw. The function never changes it.
    :param plot_text_context: explicit font faces, plot variables, and opt-in
        saved board-coordinate glyph reuse. Reuse this context across layers.
    :param layers: canonical layer names, such as ``"F.SilkS"``. One document
        comes back per name, which mirrors ``kicad-cli --mode-multi`` usage.
    :param view_box: the window, in millimeters. The renderer never crops to
        content, so a caller can stack several renders into one frame.
    :param style: the policy that picks color and opacity, drops items, and
        decides whether an element carries its uuid.
    :param content: which board streams to draw. The default is drawings only,
        which is what this renderer has always drawn. Pass
        ``ALL_CONTENT``, or a set naming :class:`BoardContent` members, to add
        copper tracks, vias, zones and pads. Copper is opt-in because it is far
        larger than the drawings: one corpus board holds 3188 tracks against 160
        footprints, and turning it on by default would change every existing
        render.

        ``BoardContent.pads`` draws pad copper, resolved per layer through the
        pad's padstack. Every pad shape but ``custom`` matches ``kicad-cli``
        vertex for vertex. See
        :func:`eedesigncore.render.pads.pad_geometry_is_exact` for the one that
        does not and why.

        ``BoardContent.rule_areas`` goes PAST KiCad. KiCad's plot skips a rule
        area, so a render that asks for this stream holds outlines no KiCad
        export contains. Use ``KICAD_CONTENT`` for a render that must stay
        comparable against ``kicad-cli``, and ``ALL_CONTENT`` for every stream.
    :param via_diameter: which width to draw for a via that carries a padstack.
        The default is the width of the layer being drawn, which is what KiCad
        plots. ``ViaDiameter.largest`` draws the widest ring of the padstack on
        every layer, for a caller who wants one circle per via.
    :param font: measures text, which decides where a dimension cuts its own
        crossbar (``pcb_dimension.cpp:994-1007``). The default is KiCad's
        built-in stroke font, which is what a board's text uses unless it names
        an installed face. Pass a :class:`TextMetrics` to override it.
    :param precision: decimal places for every millimeter value. The default of 4
        is KiCad's own (``SVG_plotter.cpp:176``), which resolves 0.1 micron.
    :param on_skip: called once for each item this renderer cannot draw. An
        unsupported item never raises and never disappears in silence.
    :param on_element: called for each emitted SVG fragment, in page order,
        with the same RenderItem supplied to the style policy. It includes
        the actual layer, item, and hosting footprint, even without UUIDs.
        Multiple shapes from one item produce separate calls. The fragment
        retains the document wrapper's coordinate system. With text_context,
        items belong to the resolved copy. Treat callback items as read-only.
        Exceptions from the callback propagate to the caller.
    :returns: layer name mapped to one SVG document.

    A repeated layer name yields one entry, because the result is a mapping. The
    insertion order follows *layers*.
    """
    if text_context is not None:
        board = resolve_document_text(board, text_context).document
    plot_params = board.setup.pcbplotparams if board.setup is not None else PlotParams()
    pages: dict[str, str] = {}
    wanted = list(dict.fromkeys(layers))
    hole_layers = [name for name in wanted if name in HOLE_LAYERS]
    by_layer = render_items_by_layer(board, [n for n in wanted if n not in HOLE_LAYERS], content)

    for layer in wanted:
        render_items = (
            _hole_render_items(board, layer) if layer in hole_layers else by_layer.get(layer, [])
        )
        elements: list[str] = []

        for render_item in render_items:
            element_style = style.style_for(render_item)
            if element_style is None:
                continue

            shapes = _shapes_for_render_item(
                render_item, precision, font, via_diameter, board, plot_text_context
            )
            if shapes is None:
                if on_skip is not None:
                    on_skip(render_item)
                continue

            for shape in shapes:
                element = render_element(
                    shape,
                    render_item,
                    element_style,
                    style,
                    precision,
                    dash_ratio=plot_params.dashed_line_dash_ratio,
                    gap_ratio=plot_params.dashed_line_gap_ratio,
                )
                elements.append(element)
                if on_element is not None:
                    on_element(render_item, element)

        pages[layer] = build_document(view_box, elements, precision)

    return pages


def render_footprint_svg(
    footprint: Footprint,
    *,
    layers: Sequence[str],
    view_box: ViewBox,
    style: StylePolicy,
    content: Collection[BoardContent] = ALL_CONTENT,
    setup: Setup | None = None,
    font: TextMetrics | None = None,
    precision: int = DEFAULT_PRECISION,
    on_skip: SkipReport | None = None,
    on_element: Callable[[RenderItem, str], None] | None = None,
    text_context: TextResolutionContext | None = None,
    plot_text_context: PlotTextContext | None = None,
) -> dict[str, str]:
    """Draw an in-memory footprint to one SVG string per requested layer.

    No file access, subprocess, or input mutation occurs. Pads are included by
    default. Hole layers, style policies, and unsupported-item reporting follow
    :func:`render_board_svg`. The supplied placement is retained. Library
    footprints with no placement draw about their local origin.

    Supply *setup* for board-level mask and paste defaults. Without it, the
    normal defaults of a new Board apply. Pad and footprint overrides still win.
    *view_box* uses SVG coordinates in millimeters, with Y increasing downward.
    """
    board = Board(footprints=[footprint])
    if setup is not None:
        board.setup = setup
    return render_board_svg(
        board,
        layers,
        view_box,
        style=style,
        content=content,
        font=font,
        precision=precision,
        on_skip=on_skip,
        on_element=on_element,
        text_context=text_context,
        plot_text_context=plot_text_context,
    )


def _hole_render_items(board: Board, layer: str) -> list[RenderItem]:
    """Wrap every hole of *layer* as a render item, so one loop draws everything."""
    return [
        RenderItem(
            item=hole,
            layer=layer,
            layers=(layer,),
            footprint=None,
            uuid=hole.uuid,
            to_board=identity_map,
        )
        for hole in iter_holes(board, layer)
    ]


def _shapes_for_render_item(
    render_item: RenderItem,
    precision: int,
    font: TextMetrics | None = None,
    via_diameter: ViaDiameter = DEFAULT_VIA_DIAMETER,
    board: Board | None = None,
    plot_text_context: PlotTextContext | None = None,
) -> list[SvgShape] | None:
    """Return the shapes of one render item: a hole, or anything else.

    *font* reaches the text and dimension emitters. *via_diameter* reaches the
    via emitter. A hole needs neither.
    """
    item = render_item.item
    if plot_text_context is not None:
        from eedesigncore.common.models import Property, TextAttributes
        from eedesigncore.common.pcb_family.board_items import Dimension, Table
        from eedesigncore.common.pcb_family.shapes import PcbText
        from eedesigncore.render.tables import pcb_table_shapes

        def font_for(attrs: TextAttributes | None) -> TextMetrics | None:
            if attrs is None or not attrs.font_face:
                return font
            request = PlotFontRequest(attrs.font_face, attrs.bold, attrs.italic)
            selected = plot_text_context.fonts.get(request)
            if selected is None and not request.bold and not request.italic:
                selected = plot_text_context.fonts.get(attrs.font_face)
            return selected

        if isinstance(item, Table):
            params = (
                board.setup.pcbplotparams
                if board is not None and board.setup is not None
                else PlotParams()
            )
            return pcb_table_shapes(
                item,
                render_item.to_board,
                precision,
                font,
                dash_ratio=params.dashed_line_dash_ratio,
                gap_ratio=params.dashed_line_gap_ratio,
                font_for=font_for,
            )

        if isinstance(item, PcbText | Property):
            if not item.visible or (item.attributes is not None and not item.attributes.visible):
                return []
            raw = item.content if isinstance(item, PcbText) else item.value
            shown = _shown(render_item, raw, plot_text_context)
            prepared = item.model_copy(
                update={"content" if isinstance(item, PcbText) else "value": shown}
            )
            return pcb_text_shapes(
                prepared,
                render_item.to_board,
                precision,
                font_for(item.attributes),
                use_saved_cache=plot_text_context.use_saved_text_cache,
            )
        font = font_for(
            item.text.attributes
            if isinstance(item, Dimension) and item.text is not None
            else getattr(item, "attributes", None)
        )
    if isinstance(item, Hole):
        return hole_shapes(item.position, item.width, item.height, item.angle, precision)
    if isinstance(item, PcbTextBox):
        return pcb_text_box_shapes(item, render_item.to_board, precision, font)
    extra = extra_shapes(
        item,
        render_item.to_board,
        precision,
        font,
        dash_ratio=board.setup.pcbplotparams.dashed_line_dash_ratio
        if board is not None and board.setup is not None
        else 12.0,
        gap_ratio=board.setup.pcbplotparams.dashed_line_gap_ratio
        if board is not None and board.setup is not None
        else 3.0,
    )
    if extra is not None:
        return extra
    margin = None
    if isinstance(item, Pad):
        margin = pad_aperture_margin(
            item,
            render_item.layer,
            footprint=render_item.footprint,
            setup=board.setup if board else None,
            version=board.version if board else None,
        )
    return shapes_for(
        item,
        render_item.to_board,
        precision,
        render_item.layer,
        font,
        via_diameter,
        pad_margin=margin,
    )


__all__ = [
    "ALL_CONTENT",
    "DEFAULT_CONTENT",
    "DEFAULT_VIA_DIAMETER",
    "DEFAULT_POINT_STROKE_MM",
    "DEFAULT_PRECISION",
    "HOLES",
    "HOLES_NON_PLATED",
    "HOLES_PLATED",
    "HOLE_LAYERS",
    "KICAD_CONTENT",
    "BoardContent",
    "CliParityStyle",
    "Hole",
    "EXACT_PAD_SHAPES",
    "PadFlash",
    "PadLayerGeometry",
    "PadOutline",
    "ElementStyle",
    "LayerStyle",
    "RenderItem",
    "SkipReport",
    "StylePolicy",
    "SymbolStyle",
    "SchematicStyle",
    "SymbolFont",
    "SvgShape",
    "TextMetrics",
    "ViaDiameter",
    "ViewBox",
    "build_document",
    "escape_attr",
    "external_copper_names",
    "format_mm",
    "hole_shapes",
    "is_rule_area",
    "pad_draws_on",
    "pad_flash",
    "pad_geometry_is_exact",
    "pad_layer_geometry",
    "pad_layer_names",
    "pad_outline",
    "render_board_svg",
    "render_footprint_svg",
    "render_symbol_svg",
    "render_schematic_svg",
    "render_element",
    "shapes_for",
    "via_diameter_mm",
    "via_flash",
]


__all__ += ["render_drawing_sheet_svg"]

__all__ += ["OutlineFont", "load_font"]
