"""Render library symbol definitions without file access or KiCad processes."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from eedesigncore.common.bbox import BoundingBox
from eedesigncore.common.escape import unescape_string
from eedesigncore.common.font import effective_pen_width
from eedesigncore.common.models import Color, FillType, Stroke, TextAttributes
from eedesigncore.common.sch_family.ordering import resolve_symbol_definition as _resolve_symbol
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.render.document import ViewBox, build_document, escape_attr
from eedesigncore.render.geometry import (
    DEFAULT_PRECISION,
    SvgShape,
    arc_path,
    format_mm,
    stroke_attrs,
    text_shapes,
)
from eedesigncore.render.traversal import identity_map
from eedesigncore.text_resolution import TextResolutionContext, resolve_document_text

Point = tuple[float, float]
Graphic = SchArc | SchBezier | SchCircle | SchPolyline | SchRect


class SymbolFont(Protocol):
    """Caller-supplied glyph strokes for every kind of symbol text.

    Coordinates, size, and thickness use the existing font contract: integer
    nanometers. Return one point list per pen-down run. Apply the supplied
    angle, alignment, mirror, and italic arguments before returning the points.
    The caller loads any external font before rendering. The exporter does not
    read installed fonts. StrokeFont implements this protocol.
    Text boxes also require advance(text, size), returning integer nanometers,
    so wrapping uses the supplied font. Their line pitch uses stroke-font metrics.
    """

    def text_strokes(
        self,
        text: str,
        size: tuple[int, int],
        position: tuple[int, int],
        thickness: int,
        *,
        angle: float = 0.0,
        mirror: bool = False,
        italic: bool = False,
        halign: str = "center",
        valign: str = "center",
    ) -> list[list[tuple[int, int]]]:
        """Return glyph strokes in drawing coordinates, with Y downward."""
        ...


@dataclass(frozen=True)
class SymbolStyle:
    """Symbol colors and the default line width, in millimeters.

    Explicit shape colors take precedence. A zero shape width uses
    *line_width_mm*. A negative shape width suppresses its outline.
    *show_pin_types* enables electrical type labels in the *pin_types* color.
    """

    body: str = "#840000"
    background: str = "#ffffc2"
    pins: str = "#840000"
    pin_names: str = "#008484"
    pin_numbers: str = "#840000"
    fields: str = "#008484"
    text: str = "#008484"
    line_width_mm: float = 0.1524
    show_pin_types: bool = False
    pin_types: str = "#8484ff"
    clock_style: Literal["editor", "plot"] = "editor"

    def __post_init__(self) -> None:
        if self.clock_style not in ("editor", "plot"):
            raise ValueError("The clock style must be editor or plot.")
        if not math.isfinite(self.line_width_mm) or self.line_width_mm <= 0:
            raise ValueError("The symbol default line width must be finite and positive.")


_DEFAULT_STYLE = SymbolStyle()


def _svg_point(point: Point) -> Point:
    return point[0], -point[1]


def _color(color: Color | None, fallback: str) -> tuple[str, float]:
    if color is None:
        return fallback, 1.0
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}", color.a


class _SymbolDrawing:
    def __init__(self, style: SymbolStyle, precision: int, font: SymbolFont | None) -> None:
        self.style = style
        self.precision = precision
        self.font = font
        self.measured_bounds: list[BoundingBox] | None = None
        self.measured_parts: list[tuple[str, BoundingBox]] | None = None
        self.measurement_role = "body"
        self.background: list[str] = []
        self.foreground: list[str] = []

    def number(self, value: float) -> str:
        return format_mm(value, self.precision)

    def point(self, value: Point) -> str:
        return f"{self.number(value[0])},{self.number(value[1])}"

    def element(
        self,
        shape: SvgShape,
        *,
        color: str,
        stroke: Stroke | None = None,
        fill: str = "none",
        opacity: float = 1.0,
        background: bool = False,
    ) -> None:
        attributes = dict(shape.attrs)
        attributes.update(
            stroke_attrs(stroke or Stroke(width=self.style.line_width_mm), self.precision)
        )
        attributes.setdefault("stroke", color)
        attributes["fill"] = fill
        if opacity != 1.0:
            attributes["opacity"] = self.number(opacity)
        if (
            self.measured_bounds is not None
            and opacity > 0
            and (attributes.get("stroke") != "none" or fill != "none" or shape.tag == "image")
        ):
            from eedesigncore.render.schematic_metadata import shape_bounds

            measured = shape_bounds(
                shape,
                float(attributes.get("stroke-width", "0"))
                if attributes.get("stroke") != "none"
                else 0,
            )
            if measured is not None:
                self.measured_bounds.append(measured)
                if self.measured_parts is not None:
                    self.measured_parts.append((self.measurement_role, measured))
        body = " ".join(f'{name}="{escape_attr(value)}"' for name, value in attributes.items())
        (self.background if background else self.foreground).append(f"  <{shape.tag} {body}/>")

    def line(self, points: list[Point], color: str) -> None:
        data = " ".join(f"{'M' if i == 0 else 'L'}{self.point(p)}" for i, p in enumerate(points))
        self.element(SvgShape("path", (("d", data),)), color=color)

    def circle(self, center: Point, radius: float, color: str) -> None:
        self.element(
            SvgShape(
                "circle",
                (
                    ("cx", self.number(center[0])),
                    ("cy", self.number(center[1])),
                    ("r", self.number(radius)),
                ),
            ),
            color=color,
        )

    def text(
        self,
        content: str,
        position: Point,
        angle: float,
        attributes: TextAttributes | None,
        color: str,
        *,
        halign: str | None = None,
        valign: str | None = None,
        minimum_line_width: float = 0.0,
    ) -> None:
        attrs = attributes or TextAttributes()
        if not attrs.visible or not content:
            return
        if self.font is not None and not callable(getattr(self.font, "text_strokes", None)):
            raise ValueError("Symbol text requires a font with text_strokes support.")
        if "\n" in content:
            from eedesigncore.render.text_boxes import line_positions

            lines = content.split("\n")
            layout_attrs = (
                attrs.model_copy(update={"justify_v": type(attrs.justify_v)(valign)})
                if valign
                else attrs
            )
            for line, anchor in zip(
                lines, line_positions(lines, position, angle, layout_attrs, self.font), strict=True
            ):
                self.text(
                    line,
                    anchor,
                    angle,
                    attrs,
                    color,
                    halign=halign,
                    valign=valign,
                    minimum_line_width=minimum_line_width,
                )
            return
        thickness = attrs.font_thickness or (0.0 if attrs.bold else self.style.line_width_mm)
        width = to_mm(
            effective_pen_width(
                point_to_iu((thickness, 0.0))[0],
                point_to_iu(attrs.font_size),
                bold=attrs.bold,
            )
        )
        width = max(width, minimum_line_width)
        attrs = attrs.model_copy(update={"font_thickness": width})
        color, opacity = _color(attrs.color, color)
        from eedesigncore.render.fonts import OutlineFont

        previous_role = self.measurement_role
        self.measurement_role = previous_role + ":text"
        if isinstance(self.font, OutlineFont):
            shape = self.font.svg_shape(
                content, position, angle, attrs, self.precision, halign=halign, valign=valign
            )
            selected = self.font.for_style(attrs.bold, attrs.italic)
            synthetic_bold = (
                attrs.font_size[1] * 0.035 if attrs.bold and not selected._bold else -1
            )
            self.element(
                shape,
                color=color,
                fill=color,
                stroke=Stroke(width=synthetic_bold),
                opacity=opacity,
            )
            self.measurement_role = previous_role
            return
        for shape in text_shapes(
            content,
            position,
            angle,
            attrs,
            identity_map,
            self.precision,
            self.font,
            halign=halign,
            valign=valign,
        ):
            self.element(shape, color=color, stroke=Stroke(width=width), opacity=opacity)
        self.measurement_role = previous_role

    def graphic(self, item: Graphic) -> None:
        if isinstance(item, SchCircle):
            shape = SvgShape(
                "circle",
                (
                    ("cx", self.number(item.center[0])),
                    ("cy", self.number(-item.center[1])),
                    ("r", self.number(item.radius)),
                ),
            )
        elif isinstance(item, SchRect):
            start, end = _svg_point(item.start), _svg_point(item.end)
            shape = SvgShape(
                "rect",
                (
                    ("x", self.number(min(start[0], end[0]))),
                    ("y", self.number(min(start[1], end[1]))),
                    ("width", self.number(abs(end[0] - start[0]))),
                    ("height", self.number(abs(end[1] - start[1]))),
                    ("rx", self.number(max(0.0, item.corner_radius or 0.0))),
                ),
            )
        elif isinstance(item, SchArc):
            shape = SvgShape(
                "path",
                (
                    (
                        "d",
                        arc_path(
                            _svg_point(item.start),
                            _svg_point(item.mid),
                            _svg_point(item.end),
                            self.precision,
                        ),
                    ),
                ),
            )
        elif isinstance(item, SchBezier):
            points = [self.point(_svg_point(p)) for p in item.pts]
            shape = SvgShape("path", (("d", f"M{points[0]} C{' '.join(points[1:])}"),))
        else:
            data = " ".join(
                f"{'M' if i == 0 else 'L'}{self.point(_svg_point(p))}"
                for i, p in enumerate(item.pts)
            )
            shape = SvgShape("path", (("d", data),))
        stroke = item.stroke.model_copy(
            update={
                "width": self.style.line_width_mm if item.stroke.width == 0 else item.stroke.width,
            }
        )
        color, opacity = _color(stroke.color, self.style.body)
        fill = item.fill
        if fill.type in (FillType.background, FillType.color):
            fill_color, fill_opacity = (
                (self.style.background, 1.0)
                if fill.type == FillType.background
                else _color(fill.color, self.style.body)
            )
            self.element(
                shape,
                color="none",
                stroke=Stroke(width=-1),
                fill=fill_color,
                opacity=fill_opacity,
                background=True,
            )
        elif fill.type in (FillType.hatch, FillType.reverse_hatch, FillType.cross_hatch):
            from eedesigncore.render.hatching import hatch_shapes

            if isinstance(item, SchCircle | SchPolyline | SchRect):
                for hatch in hatch_shapes(
                    item, _svg_point, self.precision, stroke.width, schematic=True
                ):
                    self.element(
                        hatch,
                        color="none",
                        stroke=Stroke(width=-1),
                        fill=color,
                        opacity=opacity * 0.4,
                        background=True,
                    )
        self.element(
            shape,
            color=color,
            stroke=stroke,
            opacity=opacity,
            fill=color if fill.type == FillType.outline else "none",
        )


def render_symbol_svg(
    symbol: Symbol,
    *,
    view_box: ViewBox,
    unit: int = 1,
    body_style: int = 1,
    style: SymbolStyle = _DEFAULT_STYLE,
    symbols: Mapping[str, Symbol] | None = None,
    include_fields: bool = True,
    text_context: TextResolutionContext | None = None,
    show_hidden_pins: bool = False,
    font: SymbolFont | None = None,
    precision: int = DEFAULT_PRECISION,
) -> str:
    """Draw one unit and body style of an in-memory library symbol.

    Return an SVG string without reading files, starting processes, or changing
    the inputs. Common unit-zero and body-style-zero items join the selection.
    Supply *symbols* for inherited definitions. Missing parents and cycles fail.
    *view_box* uses millimeters with Y increasing downward. Symbol positions
    convert from file coordinates by negating Y. Text remains readable.

    Visible fields show stored values unless *text_context* supplies resolution
    inputs. Resolution preserves the source symbol. Private items do not draw.
    Text boxes include wrapping,
    and hatched fills use clipped polygons. Text uses the
    existing stroke-font support unless *font* supplies text_strokes. The same
    font draws pin names, pin numbers, fields, and free text. Saved sizes,
    thickness, italic, mirror, and alignment reach that font. A font_face name
    in the input does not load a font from disk.
    """
    from eedesigncore.render.symbol_pins import draw_pin

    if unit < 1 or body_style < 1:
        raise ValueError("Select a positive symbol unit and body style.")
    source, fields = _resolve_symbol(symbol, symbols or {})
    if text_context is not None:
        source = source.model_copy(deep=True)
        source.fields = list(fields)
        source.name = symbol.name
        source = resolve_document_text(source, text_context).document
        fields = source.fields
    units = {entry.unit_index for entry in source.units if entry.unit_index}
    bodies = {entry.body_style_index for entry in source.units if entry.body_style_index}
    if unit not in (units or {1}) or body_style not in (bodies or {1}):
        raise ValueError("The selected unit or body style does not exist in the symbol.")
    drawing = _SymbolDrawing(style, precision, font)
    for entry in source.units:
        if entry.unit_index not in (0, unit) or entry.body_style_index not in (0, body_style):
            continue
        for item in entry.items:
            if getattr(item, "private", False):
                continue
            if isinstance(item, SchArc | SchBezier | SchCircle | SchPolyline | SchRect):
                drawing.graphic(item)
            elif isinstance(item, Pin):
                if item.visible or show_hidden_pins:
                    draw_pin(drawing, source, item)
            elif isinstance(item, SymTextBox):
                from eedesigncore.render.text_boxes import draw_symbol_text_box

                draw_symbol_text_box(
                    drawing,
                    item,
                    _svg_point(item.position),
                    _svg_point((item.position[0] + item.size[0], item.position[1] + item.size[1])),
                    item.angle,
                )
            elif isinstance(item, SymText):
                drawing.text(
                    unescape_string(item.content),
                    _svg_point(item.position),
                    item.angle_degrees,
                    item.attributes,
                    style.text,
                )
            else:
                raise ValueError(f"Symbol SVG does not support {type(item).__name__}.")
    if include_fields:
        for field in fields:
            if field.visible and not field.private:
                value = f"{field.name}: {field.value}" if field.show_name else field.value
                drawing.text(
                    unescape_string(value),
                    _svg_point(field.position),
                    field.angle,
                    field.attributes,
                    style.fields,
                )
    return build_document(view_box, drawing.background + drawing.foreground, precision)


__all__ = ["SymbolFont", "SymbolStyle", "render_symbol_svg"]
