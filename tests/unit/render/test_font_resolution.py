"""Explicit font selection and saved board-coordinate text outlines."""

import sys
from dataclasses import replace

import pytest

from eedesigncore.common.models import RenderCache, RenderCachePolygon, TextAttributes
from eedesigncore.common.pcb_family.shapes import PcbText
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb.models import Board
from eedesigncore.render.board_text import pcb_text_shapes, saved_text_shapes
from eedesigncore.render.bounds import (
    PlotBoundsPolicy,
    PlotTextContext,
    plot_content_bbox,
    plot_font_requirements,
)
from eedesigncore.render.font_resolution import resolve_font
from eedesigncore.render.traversal import identity_map


def test_public_svg_uses_certified_cache_and_keeps_callback_identity() -> None:
    from eedesigncore.render import LayerStyle, ViewBox, render_board_svg

    item = cached()
    board = Board(
        footprints=[
            Footprint(at=FootprintPlacement(position=(100, 200), angle=90), drawings=[item])
        ]
    )
    before = board.model_dump()
    emitted = []
    pages = render_board_svg(
        board,
        ["F.Fab"],
        ViewBox(0, 0, 200, 300),
        style=LayerStyle(colors={"F.Cu": "#000000", "F.Fab": "#000000"}),
        plot_text_context=PlotTextContext(variables={"TEXT": "ABC"}, use_saved_text_cache=True),
        on_element=lambda source, fragment: emitted.append((source.item, fragment)),
    )
    assert len(emitted) == 1
    assert emitted[0][0] is item
    assert (
        "M1.000000,2.000000 L3.000000,2.000000 L3.000000,4.000000 L1.000000,4.000000 Z"
        in pages["F.Fab"]
    )
    assert board.model_dump() == before


def test_public_svg_rejects_uncertified_named_text_but_skips_hidden() -> None:
    from eedesigncore.render import LayerStyle, ViewBox, render_board_svg

    item = cached()
    with pytest.raises(ValueError, match="explicit loaded face"):
        render_board_svg(
            Board(drawings=[item]),
            ["F.Fab"],
            ViewBox(0, 0, 10, 10),
            style=LayerStyle(colors={"F.Cu": "#000000", "F.Fab": "#000000"}),
        )
    hidden = item.model_copy(update={"visible": False})
    render_board_svg(
        Board(drawings=[hidden]),
        ["F.Fab"],
        ViewBox(0, 0, 10, 10),
        style=LayerStyle(colors={"F.Cu": "#000000", "F.Fab": "#000000"}),
        plot_text_context=PlotTextContext(),
    )


def test_visible_text_rejects_metrics_only_font() -> None:
    from eedesigncore.render.geometry import text_shapes

    with pytest.raises(ValueError, match="text_strokes"):
        text_shapes("ABC", (0, 0), 0, TextAttributes(), identity_map, 6, object())


def cached() -> PcbText:
    return PcbText(
        content="${TEXT}",
        layer="F.Fab",
        angle=90,
        attributes=TextAttributes(font_face="Unavailable", mirror=True),
        render_cache=RenderCache(
            resolved_text="ABC",
            angle=90,
            polygons=[RenderCachePolygon(pts=[(1, 2), (3, 2), (3, 4), (1, 4)])],
        ),
    )


def test_saved_cache_opt_in_avoids_font_and_double_transform() -> None:
    item = cached()
    board = Board(
        footprints=[
            Footprint(at=FootprintPlacement(position=(100, 200), angle=90), drawings=[item])
        ]
    )
    before = board.model_dump()
    policy = PlotBoundsPolicy()
    context = PlotTextContext(variables={"TEXT": "ABC"}, use_saved_text_cache=True)
    assert plot_font_requirements(board, ["F.Fab"], policy=policy)
    assert plot_font_requirements(board, ["F.Fab"], policy=policy, text_context=context) == ()
    box = plot_content_bbox(board, ["F.Fab"], policy=policy, text_context=context)
    assert box is not None
    assert (box.min_x, box.max_x, box.min_y, box.max_y) == (1, 3, 2, 4)
    assert board.model_dump() == before


def test_changed_resolved_text_requires_a_font() -> None:
    board = Board(drawings=[cached()])
    policy = PlotBoundsPolicy()
    context = PlotTextContext(variables={"TEXT": "NEW"}, use_saved_text_cache=True)
    assert plot_font_requirements(board, ["F.Fab"], policy=policy, text_context=context)
    with pytest.raises(ValueError, match="explicit loaded face"):
        plot_content_bbox(board, ["F.Fab"], policy=policy, text_context=context)


def test_unresolved_text_does_not_use_cache() -> None:
    with pytest.raises(ValueError, match="Unresolved"):
        plot_font_requirements(
            Board(drawings=[cached()]),
            ["F.Fab"],
            policy=PlotBoundsPolicy(),
            text_context=PlotTextContext(use_saved_text_cache=True),
        )


def test_cache_rejects_angle_mismatch_and_knockout() -> None:
    item = cached()
    assert saved_text_shapes(item, "ABC")
    assert saved_text_shapes(item.model_copy(update={"angle": 0}), "ABC") is None
    assert saved_text_shapes(item.model_copy(update={"knockout": True}), "ABC") is None
    assert saved_text_shapes(item.model_copy(update={"render_cache": None}), "ABC") is None


def test_render_and_bounds_use_same_saved_geometry() -> None:
    item = cached().model_copy(update={"content": "ABC"})
    assert pcb_text_shapes(item, identity_map, 6, None, use_saved_cache=True) == saved_text_shapes(
        item, "ABC"
    )
    board = Board(drawings=[item])
    context = PlotTextContext(use_saved_text_cache=True)
    assert not plot_font_requirements(
        board, ["F.Fab"], policy=PlotBoundsPolicy(), text_context=context
    )
    assert plot_font_requirements(
        board,
        ["F.Fab"],
        policy=PlotBoundsPolicy(),
        text_context=replace(context, use_saved_text_cache=False),
    )


@pytest.mark.parametrize("family", ["", "abc\0def", "https://example.com/font.ttf"])
def test_resolution_rejects_invalid_family(family: str) -> None:
    with pytest.raises(ValueError):
        resolve_font(family)


def test_resolution_requires_explicit_policy() -> None:
    with pytest.raises(ValueError, match="not found"):
        resolve_font("FileIO deliberately missing font family 716923")
    with pytest.raises(ValueError, match="Unknown"):
        resolve_font("Arial", policy="unsupported")  # type: ignore[arg-type]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Requires Linux Fontconfig")
def test_linux_substitution_reports_actual_face_and_preserves_request() -> None:
    result = resolve_font("FileIO deliberately missing font family 716923", policy="kicad-system")
    assert result.substituted
    assert result.requested_family == "FileIO deliberately missing font family 716923"
    assert result.path.is_file()
    assert result.font.path == result.path
    assert result.font.for_style() is result.font


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Requires Linux Fontconfig")
def test_linux_embedded_registration_does_not_change_system_config() -> None:
    result = resolve_font("sans-serif", policy="kicad-system")
    repeated = resolve_font(
        result.resolved_family, policy="kicad-system", embedded_files=[result.path]
    )
    assert repeated.path == result.path
    assert repeated.source == "embedded"
