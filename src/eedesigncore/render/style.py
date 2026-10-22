"""Color, opacity, filtering and element identity.

Everything general lives here. The geometry layer below is frozen, so a new
capability arrives as a new policy rather than a change to the drawing code.

One hook does two jobs. ``style_for`` returns ``None`` to drop an item, so a
caller filters and styles through the same function.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from eedesigncore.common.models import FillType
from eedesigncore.common.pcb_family.shapes import PcbCircle, PcbPoint, PcbPoly, PcbRect
from eedesigncore.render.traversal import RenderItem

#: Stroke width of a point marker, in millimeters.
#:
#: This is NOT a board dimension. KiCad draws the marker at a screen-constant
#: width (``pcb_painter.cpp:3145``), which has no millimeter meaning in a file.
#: The number is policy, so it lives here and a caller can change it.
DEFAULT_POINT_STROKE_MM = 0.05


@dataclass(frozen=True)
class ElementStyle:
    """The presentation of one element. Every field is optional.

    An omitted field writes no attribute, which lets the wrapper group decide.
    """

    stroke: str | None = None
    stroke_opacity: float | None = None
    fill: str | None = None
    fill_opacity: float | None = None


def _layer_element_style(render_item: RenderItem, color: str, opacity: float) -> ElementStyle:
    item = render_item.item
    solid = isinstance(item, PcbCircle | PcbRect | PcbPoly) and item.fill.type == FillType.outline
    return ElementStyle(
        stroke=color,
        stroke_opacity=opacity,
        fill=color if solid else "none",
        fill_opacity=opacity if solid else None,
    )


@runtime_checkable
class StylePolicy(Protocol):
    """What the document layer asks of a policy."""

    #: True writes the item uuid to the element ``id`` attribute.
    emit_ids: bool
    #: Stroke width of a point marker, in millimeters.
    point_stroke_width_mm: float

    def style_for(self, render_item: RenderItem) -> ElementStyle | None:
        """Return the style of *render_item*, or ``None`` to drop it."""
        ...


@dataclass(frozen=True)
class LayerStyle:
    """Color and opacity per layer. This is the general tool.

    The opacity a caller asks for reaches the page unchanged. Nothing here
    collapses an alpha to 1.0.

    A layer with no color in *colors* falls back to *default_color*. When that is
    ``None`` too, the item drops. Color absence is therefore a filter, which lets
    a caller select content by naming only the layers it wants.
    """

    colors: Mapping[str, str]
    opacity: Mapping[str, float] = field(default_factory=dict)
    default_color: str | None = None
    default_opacity: float = 1.0
    emit_ids: bool = True
    point_stroke_width_mm: float = DEFAULT_POINT_STROKE_MM

    def style_for(self, render_item: RenderItem) -> ElementStyle | None:
        color = self.colors.get(render_item.layer, self.default_color)
        if color is None:
            return None
        return _layer_element_style(
            render_item, color, self.opacity.get(render_item.layer, self.default_opacity)
        )


@dataclass(frozen=True)
class CliParityStyle:
    """The preset that keeps the ``kicad-cli`` oracle reproducible.

    Hold this preset for as long as the renderer exists. A preset costs one
    fixture. Recovering a lost oracle costs a re-audit of the KiCad source.

    Three settings differ from :class:`LayerStyle`, and each one matches a fact
    about ``kicad-cli`` output:

    1. Opacity is 1.0 and a caller cannot change it. ``SVG_PLOTTER::StartPlot``
       hard-codes ``opacity = 1.0`` (``SVG_plotter.cpp:818``).
    2. No element carries an ``id``. ``kicad-cli`` writes none.
    3. A point marker drops. KiCad's plot path holds no branch for that item, so
       ``kicad-cli`` exports nothing for it. Drawing one here would make the
       oracle disagree over content that KiCad never had.
    """

    colors: Mapping[str, str]
    default_color: str | None = None
    emit_ids: bool = False
    point_stroke_width_mm: float = DEFAULT_POINT_STROKE_MM

    def style_for(self, render_item: RenderItem) -> ElementStyle | None:
        if isinstance(render_item.item, PcbPoint):
            return None
        color = self.colors.get(render_item.layer, self.default_color)
        if color is None:
            return None
        return _layer_element_style(render_item, color, 1.0)


__all__ = [
    "DEFAULT_POINT_STROKE_MM",
    "CliParityStyle",
    "ElementStyle",
    "LayerStyle",
    "StylePolicy",
]
