"""Assemble one SVG document: the window, the wrapper group, and the elements."""

from __future__ import annotations

from dataclasses import dataclass

from eedesigncore.common.font import effective_pen_width
from eedesigncore.common.models import Property, Stroke
from eedesigncore.common.pcb_family.board_items import Dimension
from eedesigncore.common.pcb_family.shapes import PcbText
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.pcb.models import TrackArc, TrackSegment
from eedesigncore.render.geometry import (
    DEFAULT_PRECISION,
    Attrs,
    SvgShape,
    format_mm,
    stroke_attrs,
)
from eedesigncore.render.style import ElementStyle, StylePolicy
from eedesigncore.render.traversal import RenderItem

_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"))


def escape_attr(value: str) -> str:
    """Escape *value* for use inside a double-quoted XML attribute."""
    for raw, replacement in _ESCAPES:
        value = value.replace(raw, replacement)
    return value


@dataclass(frozen=True)
class ViewBox:
    """The rendered window, in millimeters.

    The caller supplies this window. The renderer never crops to content, because
    a caller that stacks several layer renders into one frame needs every render
    to share one window.
    """

    min_x: float
    min_y: float
    width: float
    height: float

    def to_attr(self, precision: int = DEFAULT_PRECISION) -> str:
        """Return the ``viewBox`` attribute value."""
        return " ".join(
            format_mm(value, precision)
            for value in (self.min_x, self.min_y, self.width, self.height)
        )


def style_attrs(style: ElementStyle, precision: int) -> Attrs:
    """Return the presentation attributes of *style*, in a fixed order."""
    pairs: list[tuple[str, str]] = []
    if style.stroke is not None:
        pairs.append(("stroke", style.stroke))
    if style.stroke_opacity is not None:
        pairs.append(("stroke-opacity", format_mm(style.stroke_opacity, precision)))
    if style.fill is not None:
        pairs.append(("fill", style.fill))
    if style.fill_opacity is not None:
        pairs.append(("fill-opacity", format_mm(style.fill_opacity, precision)))
    return tuple(pairs)


def width_attrs(
    render_item: RenderItem,
    policy: StylePolicy,
    precision: int,
    shape: SvgShape | None = None,
    *,
    dash_ratio: float = 12.0,
    gap_ratio: float = 3.0,
) -> Attrs:
    """Return the stroke width and dash pattern of *render_item*.

    Five sources, in order:

    1. The SHAPE carries its own width. An oval pad is the case: KiCad draws it
       as one stroked segment at the pad's minor axis, and a padstack can give
       that axis a different value per layer, so the width cannot be read off the
       item alone.
    2. A drawing item carries a ``(stroke …)`` block, which is board data.
    3. A copper track carries a bare ``width`` instead, and it is still board
       data. Falling through to the policy here would draw every track at the
       point-marker width, which is 0.05 mm.
    4. A dimension carries its line width in its ``(style (thickness …))``.
    5. A point marker carries none of these, so the width comes from the policy.
    """
    if shape is not None and shape.stroke_width_mm is not None:
        if shape.stroke_width_mm <= 0.0:
            return (("stroke", "none"),)
        return (("stroke-width", format_mm(shape.stroke_width_mm, precision)),)

    item = render_item.item
    stroke = getattr(item, "stroke", None)
    if isinstance(stroke, Stroke):
        return stroke_attrs(stroke, precision, dash_ratio=dash_ratio, gap_ratio=gap_ratio)
    if isinstance(item, TrackSegment | TrackArc):
        return (("stroke-width", format_mm(item.width, precision)),)
    if isinstance(item, Dimension):
        # The line work uses the style thickness. The measurement text is drawn
        # with its own pen, but both reach here as one item, and KiCad plots a
        # dimension's text at the same width in practice.
        return (("stroke-width", format_mm(item.style.thickness, precision)),)
    if isinstance(item, PcbText | Property):
        return (("stroke-width", format_mm(_text_pen_mm(item), precision)),)
    return (("stroke-width", format_mm(policy.point_stroke_width_mm, precision)),)


def _text_pen_mm(item: PcbText | Property) -> float:
    """The pen a text item draws with, in millimetres.

    A thickness of zero or absent is not a hairline. It means unset, and KiCad
    derives the pen from the text size instead
    (``EDA_TEXT::GetEffectiveTextPenWidth``, ``eda_text.cpp:449-467``). Falling
    through to the policy would draw every label at the point-marker width.
    """
    attributes = item.attributes
    size_mm = (1.27, 1.27)
    thickness_mm = None
    bold = False
    if attributes is not None:
        if attributes.font_size:
            size_mm = attributes.font_size
        thickness_mm = attributes.font_thickness
        bold = bool(attributes.bold)

    size = point_to_iu(size_mm)
    thickness = point_to_iu((thickness_mm or 0.0, 0.0))[0]
    return to_mm(effective_pen_width(thickness, size, bold=bold))


def filled_attrs(style: ElementStyle, precision: int) -> Attrs:
    """Return the presentation of a solid area.

    A solid area takes the color the policy chose for the stroke, because the
    policy names one color per layer and this shape is that layer's ink. The
    element carries no stroke, so the disc has no hole through it.
    """
    pairs: list[tuple[str, str]] = [("stroke", "none")]
    if style.stroke is not None:
        pairs.append(("fill", style.stroke))
    if style.stroke_opacity is not None:
        pairs.append(("fill-opacity", format_mm(style.stroke_opacity, precision)))
    return tuple(pairs)


def render_element(
    shape: SvgShape,
    render_item: RenderItem,
    style: ElementStyle,
    policy: StylePolicy,
    precision: int,
    *,
    dash_ratio: float = 12.0,
    gap_ratio: float = 3.0,
) -> str:
    """Return one SVG element as text.

    Attribute order is fixed: geometry, then width, then presentation, then
    identity. A fixed order is what makes two renders of one board match byte for
    byte.
    """
    pairs: list[tuple[str, str]] = list(shape.attrs)
    if shape.filled:
        pairs.extend(filled_attrs(style, precision))
    else:
        width = width_attrs(
            render_item, policy, precision, shape, dash_ratio=dash_ratio, gap_ratio=gap_ratio
        )
        pairs.extend(width)
        # A layer color must not enable a stroke suppressed by the geometry.
        suppress_stroke = ("stroke", "none") in width
        pairs.extend(
            pair
            for pair in style_attrs(style, precision)
            if not (suppress_stroke and pair[0] == "stroke")
        )
    if policy.emit_ids and render_item.uuid:
        pairs.append(("id", render_item.uuid))

    body = " ".join(f'{name}="{escape_attr(value)}"' for name, value in pairs)
    return f"  <{shape.tag} {body}/>"


def build_document(
    view_box: ViewBox, elements: list[str], precision: int = DEFAULT_PRECISION
) -> str:
    """Wrap *elements* in one SVG document.

    The document carries no date. ``SVG_PLOTTER::StartPlot`` writes the current
    time into a ``<title>`` element (``SVG_plotter.cpp:806``), which would make
    two renders of one board differ. Determinism outranks that line.

    Round caps and round joins sit on the wrapper group, as they do in KiCad
    (``SVG_plotter.cpp:830``), so no element repeats them.

    ``width`` and ``height`` restate the window in millimeters, which holds the
    document at 1:1 physical scale. A viewer that wants pixels sets its own size.
    """
    header = (
        '<?xml version="1.0" standalone="no"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1"\n'
        f'  width="{format_mm(view_box.width, precision)}mm" '
        f'height="{format_mm(view_box.height, precision)}mm" '
        f'viewBox="{view_box.to_attr(precision)}">\n'
        '<g style="fill:none; stroke-linecap:round; stroke-linejoin:round;">\n'
    )
    body = "".join(f"{element}\n" for element in elements)
    return f"{header}{body}</g>\n</svg>\n"


__all__ = [
    "ViewBox",
    "build_document",
    "escape_attr",
    "filled_attrs",
    "render_element",
    "style_attrs",
    "width_attrs",
]
