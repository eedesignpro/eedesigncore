"""Visible schematic measurements from the existing drawing operations."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

from eedesigncore.common.bbox import BoundingBox
from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.common.sch_family.ordering import resolve_symbol_definition
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.connectivity.geometry import pin_sheet_position, symbol_transform
from eedesigncore.render.document import ViewBox
from eedesigncore.render.schematic_metadata import DrawingMetadata
from eedesigncore.render.schematics import SchematicStyle, _render_schematic_svg
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.models import (
    BusEntry,
    GraphicShape,
    Image,
    Junction,
    Label,
    LabelKind,
    Line,
    LineType,
    NoConnect,
    PlacedSymbol,
    RuleArea,
    Schematic,
    SchTable,
    SchText,
    SchTextBox,
    Sheet,
    SheetPin,
)
from eedesigncore.text_resolution import TextResolutionContext, resolve_document_text

_STYLE = SchematicStyle()

Point = tuple[float, float]
SchematicItem = (
    Label
    | Sheet
    | SheetPin
    | PlacedSymbol
    | Line
    | BusEntry
    | Junction
    | NoConnect
    | SchText
    | SchTextBox
    | SchTable
    | Image
    | RuleArea
    | GraphicShape
)
T = TypeVar("T", bound=SchematicItem)


@dataclass(frozen=True)
class SchematicPartBounds:
    """One visible part, with optional segment geometry and electrical contacts."""

    name: str
    bounds: BoundingBox
    contacts: tuple[Point, ...] = ()
    segment: tuple[Point, Point] | None = None
    stroke_mm: float = 0.0
    obstacle: bool = True


@dataclass(frozen=True)
class SchematicMeasurement:
    """A detached measurement. Measure again after changing the source or context."""

    item_id: str
    kind: str
    bounds: BoundingBox | None
    parts: tuple[SchematicPartBounds, ...]
    contacts: tuple[Point, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class MeasuredSchematicItem(Generic[T]):
    item: T
    measurement: SchematicMeasurement


_COLLECTIONS = (
    "symbols",
    "sheets",
    "labels",
    "lines",
    "texts",
    "text_boxes",
    "tables",
    "junctions",
    "no_connects",
    "bus_entries",
    "images",
    "graphic_shapes",
    "rule_areas",
)


def _collection(item: SchematicItem) -> str:
    for cls, name in (
        (PlacedSymbol, "symbols"),
        (Sheet, "sheets"),
        (Label, "labels"),
        (Line, "lines"),
        (SchText, "texts"),
        (SchTextBox, "text_boxes"),
        (SchTable, "tables"),
        (Junction, "junctions"),
        (NoConnect, "no_connects"),
        (BusEntry, "bus_entries"),
        (Image, "images"),
        (RuleArea, "rule_areas"),
    ):
        if isinstance(item, cls):
            return name
    return "graphic_shapes"


def _visible_models(value: BaseModel) -> Iterator[BaseModel]:
    if isinstance(value, Property) and (not value.visible or value.private):
        return
    if isinstance(value, TextAttributes) and not value.visible:
        return
    yield value
    for name in type(value).model_fields:
        child = getattr(value, name)
        if isinstance(child, BaseModel):
            yield from _visible_models(child)
        elif isinstance(child, list):
            for element in child:
                if isinstance(element, BaseModel):
                    yield from _visible_models(element)


def _diagnostics(item: BaseModel, font: SymbolFont | None) -> tuple[str, ...]:
    issues = set()
    for value in _visible_models(item):
        if isinstance(value, TextAttributes) and value.font_face and font is None:
            issues.add("Named font requires an explicit measurement font.")
        for name in ("content", "value", "name"):
            text = getattr(value, name, None)
            if isinstance(text, str) and ("${" in text or "@{" in text):
                issues.add("Displayed text remains unresolved. Supply the sheet text context.")
    return tuple(sorted(issues))


def _box(points: tuple[Point, Point], pad: float) -> BoundingBox:
    return BoundingBox(
        min_x=min(p[0] for p in points) - pad,
        min_y=min(p[1] for p in points) - pad,
        max_x=max(p[0] for p in points) + pad,
        max_y=max(p[1] for p in points) + pad,
    )


def measure_schematic_item(
    item: SchematicItem,
    *,
    schematic: Schematic | None = None,
    text_context: TextResolutionContext | None = None,
    style: SchematicStyle = _STYLE,
    font: SymbolFont | None = None,
    item_id: str | None = None,
) -> SchematicMeasurement:
    """Measure visible geometry without moving it or reading files.

    Supply the schematic for embedded symbol definitions. Supply text context
    for hierarchy-dependent fields. Incomplete measurements carry diagnostics
    and the collision checker rejects them by default.
    """
    identity = item_id or getattr(item, "uuid", None) or type(item).__name__
    source = item
    if isinstance(item, SheetPin):
        item = Label(
            kind=LabelKind.hierarchical,
            content=item.name,
            shape={"input": "output", "output": "input"}.get(item.shape, item.shape),
            position=item.position,
            angle=(item.angle + 180) % 360,
            attributes=item.attributes,
            uuid=item.uuid,
        )
    category = _collection(item)
    isolated = Schematic(lib_symbols=schematic.lib_symbols if schematic else [])
    setattr(isolated, category, [item])
    if text_context is not None:
        isolated = resolve_document_text(isolated, text_context).document
        item = getattr(isolated, category)[0]
    issues = _diagnostics(item, font)
    metadata = DrawingMetadata()
    try:
        _render_schematic_svg(
            isolated, view_box=ViewBox(0, 0, 297, 210), style=style, font=font, metadata=metadata
        )
    except ValueError as error:
        return SchematicMeasurement(
            identity, type(source).__name__, None, (), diagnostics=issues + (str(error),)
        )
    grouped: dict[str, BoundingBox] = {}
    for name, box in metadata.parts:
        grouped[name] = grouped[name].merged(box) if name in grouped else box
    contacts: tuple[Point, ...] = ()
    pin_contacts: dict[str, Point] = {}
    segment: tuple[Point, Point] | None = None
    width = style.symbol.line_width_mm
    if isinstance(item, Label | Junction | NoConnect):
        contacts = (item.position,)
        if isinstance(item, Junction):
            width = item.diameter or style.junction_diameter_mm
        elif isinstance(item, NoConnect):
            width = style.no_connect_size_mm + style.symbol.line_width_mm
    elif isinstance(item, Sheet):
        contacts = tuple(pin.position for pin in item.pins)
        pin_contacts = {f"pin:{i}:{pin.name}": pin.position for i, pin in enumerate(item.pins)}
    elif isinstance(item, PlacedSymbol):
        definitions = {entry.name: entry for entry in isolated.lib_symbols}
        definition = definitions.get(item.lib_name or "") or definitions.get(item.lib_id)
        if definition is not None:
            symbol, _ = resolve_symbol_definition(definition, definitions)
            transform = symbol_transform(item.angle, item.mirror)
            pins = [
                pin
                for unit in symbol.units
                if unit.unit_index in (0, item.unit)
                and unit.body_style_index in (0, item.body_style)
                for pin in unit.items
                if isinstance(pin, Pin)
            ]
            for i, pin in enumerate(pins):
                point = pin_sheet_position(pin.position, transform, item.position)
                pin_contacts[f"pin:{i}:{pin.number}"] = (point[0] / 1e6, point[1] / 1e6)
            contacts = tuple(pin_contacts.values())
            issues += _diagnostics(symbol, font)
    elif isinstance(item, Line):
        segment = (item.start, item.end)
        contacts = segment if item.line_type != LineType.polyline else ()
        width = item.stroke.width or (
            style.bus_width_mm if item.line_type == LineType.bus else style.wire_width_mm
        )
    elif isinstance(item, BusEntry):
        end = (item.position[0] + item.size[0], item.position[1] + item.size[1])
        segment = (item.position, end)
        contacts = segment
        width = item.stroke.width or style.wire_width_mm
    parts = []
    for name, box in grouped.items():
        part_contacts = () if ":text" in name or "field:" in name else contacts
        if name.startswith("pin:"):
            key = name.removesuffix(":text")
            part_contacts = (
                (pin_contacts[key],) if key in pin_contacts and ":text" not in name else ()
            )
        parts.append(
            SchematicPartBounds(
                name, box, part_contacts, segment, width, obstacle=name != "container"
            )
        )
    if isinstance(item, Sheet):
        x, y = item.position
        w, h = item.size
        corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        width = item.stroke.width or style.symbol.line_width_mm
        if width > 0:
            for i, start in enumerate(corners):
                edge = (start, corners[(i + 1) % 4])
                parts.append(
                    SchematicPartBounds(
                        f"border:{i}", _box(edge, width / 2 + 0.0001), contacts, edge, width
                    )
                )
    bounds = None
    for part in parts:
        if not all(math.isfinite(v) for v in part.bounds.model_dump().values()):
            raise ValueError("Measured geometry must be finite.")
        bounds = part.bounds if bounds is None else bounds.merged(part.bounds)
    kind = item.line_type.value if isinstance(item, Line) else type(source).__name__
    return SchematicMeasurement(identity, kind, bounds, tuple(parts), contacts, issues)


def measure_schematic(
    schematic: Schematic,
    *,
    text_context: TextResolutionContext | None = None,
    style: SchematicStyle = _STYLE,
    font: SymbolFont | None = None,
) -> tuple[SchematicMeasurement, ...]:
    """Measure every drawable item on one sheet, resolving its display copy once."""
    displayed = (
        resolve_document_text(schematic, text_context).document
        if text_context is not None
        else schematic
    )
    return tuple(
        measure_schematic_item(
            item,
            schematic=displayed,
            style=style,
            font=font,
            item_id=f"{name}:{index}:{getattr(item, 'uuid', '')}",
        )
        for name in _COLLECTIONS
        for index, item in enumerate(getattr(displayed, name))
    )


def measured_item(
    item: T, context: TextResolutionContext | None = None, *, font: SymbolFont | None = None
) -> MeasuredSchematicItem[T]:
    return MeasuredSchematicItem(
        item, measure_schematic_item(item, text_context=context, font=font)
    )
