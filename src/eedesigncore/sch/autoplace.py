"""Place schematic fields using only their owner's geometry."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal

from eedesigncore.common.bbox import BoundingBox
from eedesigncore.common.escape import unescape_string
from eedesigncore.common.models import JustifyH, JustifyV, Property, TextAttributes
from eedesigncore.common.sch_family.ordering import resolve_symbol_definition
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.connectivity.geometry import apply, symbol_transform
from eedesigncore.render.dnp import symbol_bounds, text_bounds
from eedesigncore.render.schematic_labels import draw_label
from eedesigncore.render.symbols import SymbolFont, SymbolStyle, _SymbolDrawing
from eedesigncore.sch.models import Label, LabelKind, PlacedSymbol, Schematic

_Side = Literal["left", "right", "top", "bottom"]
_Style = Literal["auto", "left", "right", "top", "bottom"]
_DIRECTIONS: dict[_Side, tuple[int, int]] = {
    "left": (-1, 0),
    "right": (1, 0),
    "top": (0, -1),
    "bottom": (0, 1),
}


def autoplace_fields(
    target: Schematic | PlacedSymbol | Label,
    *,
    style: Literal["auto", "left", "right", "top", "bottom"] = "auto",
    grid_mm: float = 1.27,
    gap_mm: float = 0.635,
    definition: Symbol | None = None,
    font: SymbolFont | None = None,
) -> None:
    """Place visible symbol and global/hierarchical-label fields without file access.

    A schematic supplies its embedded definitions. A single placed symbol requires
    ``definition`` with resolved inheritance. Other label kinds are unsupported
    individually and untouched in a schematic. Styles use sheet directions.
    Symbol anchors snap on the placement axis. Text displays horizontally.
    Hidden, private, empty, and do-not-autoplace fields remain unchanged.
    The operation validates every placement before changing any target.
    """
    if style not in ("auto", "left", "right", "top", "bottom"):
        raise ValueError("The placement style must be auto, left, right, top, or bottom.")
    if not math.isfinite(grid_mm) or grid_mm < 0.0001:
        raise ValueError("The field grid must be finite and at least 0.0001 mm.")
    if not math.isfinite(gap_mm) or gap_mm < 0:
        raise ValueError("The field gap must be finite and nonnegative.")
    definitions: dict[str, Symbol] = {}
    if isinstance(target, Schematic):
        if definition is not None:
            raise ValueError("A schematic supplies its own symbol definitions.")
        for entry in target.lib_symbols:
            if entry.name in definitions:
                raise ValueError(f"Duplicate embedded symbol definition: {entry.name}.")
            definitions[entry.name] = entry
        items: list[PlacedSymbol | Label] = [
            *target.symbols,
            *(
                label
                for label in target.labels
                if label.kind in (LabelKind.global_, LabelKind.hierarchical)
            ),
        ]
    elif isinstance(target, PlacedSymbol | Label):
        items = [target]
        if definition is not None:
            if not isinstance(target, PlacedSymbol):
                raise ValueError("A label does not accept a symbol definition.")
            definitions[target.lib_name or target.lib_id] = definition
    else:
        raise TypeError(
            "Field placement requires a schematic, symbol, global label, or hierarchical label."
        )
    plans: list[tuple[PlacedSymbol | Label, PlacedSymbol | Label]] = []
    for item in items:
        planned = item.model_copy(deep=True)
        _place(planned, definitions, style, grid_mm, gap_mm, font)
        plans.append((item, planned))
    for item, planned in plans:
        for old, new in zip(item.fields, planned.fields, strict=True):
            if old != new:
                old.position, old.angle, old.attributes = new.position, new.angle, new.attributes
        item.fields_autoplaced = planned.fields_autoplaced


def _snap(value: float, grid: float, direction: int = 0) -> float:
    scaled = value / grid
    if direction:
        steps = math.ceil(scaled - 1e-10) if direction > 0 else math.floor(scaled + 1e-10)
    else:
        steps = math.floor(abs(scaled) + 0.5) * (1 if scaled >= 0 else -1)
    return round(steps * grid, 10)


def _eligible(field: Property) -> bool:
    return bool(
        field.visible
        and not field.private
        and not field.do_not_autoplace
        and field.value
        and (field.attributes is None or field.attributes.visible)
    )


def _place(
    item: PlacedSymbol | Label,
    definitions: Mapping[str, Symbol],
    style: _Style,
    grid: float,
    gap: float,
    font: SymbolFont | None = None,
) -> None:
    if not all(math.isfinite(v) for v in (*item.position, item.angle)) or item.angle % 90:
        raise ValueError("Field placement requires finite coordinates and a cardinal angle.")
    if isinstance(item, Label) and item.kind not in (LabelKind.global_, LabelKind.hierarchical):
        raise ValueError("Only global and hierarchical labels support automatic field placement.")
    fields = [field for field in item.fields if _eligible(field)]
    if not fields:
        return
    offset = (0.0, 0.0)
    if isinstance(item, PlacedSymbol):
        box, automatic = _symbol_box(item, definitions)
    else:
        box, offset, automatic = _label_box(item, font)
    side: _Side = automatic if style == "auto" else style
    if isinstance(item, Label) and style == "auto":
        refs = [f for f in fields if f.name == "Intersheetrefs"]
        others = [f for f in fields if f.name != "Intersheetrefs"]
        _place_page_references(item, refs, side, font)
        _group(
            item,
            others,
            box,
            "bottom" if side in ("left", "right") else "right",
            offset,
            grid,
            gap,
            font,
        )
    else:
        _group(item, fields, box, side, offset, grid, gap, font)
    item.fields_autoplaced = True


def _symbol_box(
    item: PlacedSymbol, definitions: Mapping[str, Symbol]
) -> tuple[BoundingBox, _Side]:
    definition = definitions.get(item.lib_name or "") or definitions.get(item.lib_id)
    if definition is None:
        raise ValueError(f"Missing symbol definition for field placement: {item.lib_id}.")
    source, _ = resolve_symbol_definition(definition, definitions)
    if item.mirror not in (None, "x", "y"):
        raise ValueError("A symbol mirror must be x, y, or absent.")
    units = {u.unit_index for u in source.units if u.unit_index} or {1}
    bodies = {u.body_style_index for u in source.units if u.body_style_index} or {1}
    if item.unit not in units or item.body_style not in bodies:
        raise ValueError("The selected symbol unit or body style is absent.")
    contents = [
        shape
        for unit in source.units
        if unit.unit_index in (0, item.unit) and unit.body_style_index in (0, item.body_style)
        for shape in unit.items
    ]
    transform = symbol_transform(item.angle, item.mirror)

    def sheet(point: tuple[float, float]) -> tuple[float, float]:
        x, y = apply(transform, (point[0], -point[1]))
        return x + item.position[0], y + item.position[1]

    body, _whole = symbol_bounds(contents, sheet, None)
    counts: dict[_Side, int] = dict.fromkeys(_DIRECTIONS, 0)
    for pin in contents:
        if isinstance(pin, Pin) and (pin.visible or source.power_type is not None):
            angle = math.radians(pin.angle)
            direction = apply(transform, (-math.cos(angle), math.sin(angle)))
            nearest = max(
                _DIRECTIONS,
                key=lambda s: sum(a * b for a, b in zip(direction, _DIRECTIONS[s], strict=True)),
            )
            counts[nearest] += 1
    preference: list[_Side] = ["right", "top", "left", "bottom"]
    if source.power_type is not None:
        preferred = apply(transform, (0, -1))
        first = max(
            _DIRECTIONS,
            key=lambda s: sum(a * b for a, b in zip(preferred, _DIRECTIONS[s], strict=True)),
        )
        opposite: dict[_Side, _Side] = {
            "left": "right",
            "right": "left",
            "top": "bottom",
            "bottom": "top",
        }
        preference = [
            first,
            opposite[first],
            *[s for s in preference if s not in (first, opposite[first])],
        ]
    elif body.width > 3 * max(body.height, 0.0001):
        preference = ["top", "bottom", "right", "left"]
    elif transform[0] < 0:
        preference = ["left", "top", "right", "bottom"]
    side = min(preference, key=lambda s: counts[s])
    return body, side


def _label_box(
    item: Label, font: SymbolFont | None = None
) -> tuple[BoundingBox, tuple[float, float], _Side]:
    drawing = _SymbolDrawing(SymbolStyle(), 6, font)
    drawing.measured_bounds = []
    draw_label(
        drawing,
        item.model_copy(update={"fields": []}),
        "#000000",
        text_offset_ratio=0.15,
        label_size_ratio=0.375,
    )
    boxes = drawing.measured_bounds
    if not boxes:
        raise ValueError("The global label has no measurable body.")
    box = boxes[0]
    for extra in boxes[1:]:
        box = box.merged(extra)
    attrs = item.attributes or TextAttributes()
    angle = int(item.angle) % 180 + (180 if attrs.justify_h == JustifyH.right else 0)
    side: _Side = {0: "right", 90: "top", 180: "left", 270: "bottom"}[angle]  # type: ignore[assignment]
    height = attrs.font_size[1]
    distance = height * (
        0.375
        + (
            0.75
            if (item.shape or "bidirectional") in ("input", "bidirectional", "tri_state")
            else 0
        )
    )
    dx, dy = _DIRECTIONS[side]
    offset = (
        dx * distance + (height * 0.0715 if dy else 0),
        dy * distance + (height * 0.0715 if dx else 0),
    )
    if item.kind == LabelKind.hierarchical:
        offset = (0.0, 0.0)
    return box, offset, side


def _group(
    owner: PlacedSymbol | Label,
    fields: list[Property],
    box: BoundingBox,
    side: _Side,
    offset: tuple[float, float],
    grid: float,
    gap: float,
    font: SymbolFont | None = None,
) -> None:
    measured: list[tuple[Property, TextAttributes, float]] = []
    for field in fields:
        attrs = (field.attributes or TextAttributes()).model_copy(deep=True)
        if attrs.font_face and font is None:
            raise ValueError("Automatic field placement currently requires the KiCad stroke font.")
        if not all(math.isfinite(v) and v > 0 for v in attrs.font_size):
            raise ValueError("Field text sizes must be finite and positive.")
        attrs.justify_h = {"left": JustifyH.right, "right": JustifyH.left}.get(
            side, JustifyH.center
        )
        attrs.justify_v, attrs.mirror = JustifyV.center, False
        text = unescape_string(f"{field.name}: {field.value}" if field.show_name else field.value)
        bounds = text_bounds(text, (0, 0), 0, attrs, font)
        measured.append((field, attrs, bounds.height))
    if not measured:
        return
    symbol_owner = isinstance(owner, PlacedSymbol)
    slots = [_snap(entry[2], grid, 1) for entry in measured]
    separations = [
        _snap((previous[2] + current[2]) / 2 + gap, grid, 1)
        for previous, current in zip(measured, measured[1:], strict=False)
    ]
    if symbol_owner:
        separations = [(a + b) / 2 for a, b in zip(slots, slots[1:], strict=False)]
    total = measured[0][2] / 2 + sum(separations) + measured[-1][2] / 2
    y = box.center[1] - total / 2 + measured[0][2] / 2
    vertical_gap = gap * 0.6 if symbol_owner else gap
    if symbol_owner:
        total = sum(slots)
        y = box.center[1] - total / 2 + slots[0] / 2
    if side == "bottom":
        y = box.max_y + vertical_gap + (slots[0] if symbol_owner else measured[0][2]) / 2
    elif side == "top":
        y = box.min_y - vertical_gap - total + (slots[0] if symbol_owner else measured[0][2]) / 2
    x = {"left": box.min_x - gap, "right": box.max_x + gap}.get(side, box.center[0])
    x_direction, y_direction = _DIRECTIONS[side]
    if not symbol_owner:
        y = _snap(y - offset[1], grid, y_direction)
    for index, (field, attrs, _height) in enumerate(measured):
        field.position = (
            _snap(x - offset[0], grid, x_direction)
            if x_direction or not symbol_owner
            else round(x, 10),
            _snap(y, grid, y_direction) if symbol_owner and y_direction else round(y, 10),
        )
        field.angle = 0
        if isinstance(owner, PlacedSymbol):
            transform = symbol_transform(owner.angle, owner.mirror)
            field.angle = 90 if transform[1] else 0
            radians = math.radians(field.angle)
            direction = apply(transform, (math.cos(radians), -math.sin(radians)))
            if direction[0] < 0:
                attrs.justify_h = {
                    JustifyH.left: JustifyH.right,
                    JustifyH.right: JustifyH.left,
                }.get(attrs.justify_h, attrs.justify_h)
        field.attributes = attrs
        if index < len(separations):
            y += separations[index]


def _place_page_references(
    label: Label, fields: list[Property], side: _Side, font: SymbolFont | None
) -> None:
    from eedesigncore.common.font import default_font, effective_pen_width

    attrs = label.attributes or TextAttributes()
    if attrs.font_face and font is None:
        raise ValueError("Named label fonts require an explicit loaded font.")
    size = tuple(math.floor(v * 10000 + 0.5) for v in attrs.font_size)
    pen = effective_pen_width(
        math.floor((attrs.font_thickness or 0) * 10000 + 0.5),
        (size[0], size[1]),
        bold=attrs.bold,
    )
    metrics = font or default_font()
    measure = getattr(metrics, "native_text_box", None) or getattr(metrics, "text_box", None)
    if not callable(measure):
        raise ValueError("Page-reference placement requires font text measurements.")
    text = measure(
        unescape_string(label.content),
        (size[0], size[1]),
        pen,
        bold=attrs.bold,
        italic=attrs.italic,
    )
    height = size[1]
    margin = math.floor(height * 0.375 + 0.5)
    half = height // 2 + margin
    length = text.width + 2 * margin + pen + 3
    shape = label.shape or "bidirectional"
    if shape in ("input", "output", "bidirectional", "tri_state"):
        length += half
    if shape in ("bidirectional", "tri_state"):
        length += half
    length = max(length, 2 * (half + pen + 3)) + 2 * (pen // 2)
    distance = (length + 2 * math.floor(height * 0.15 + 0.5)) / 10000
    dx, dy = _DIRECTIONS[side]
    for field in fields:
        field.position = (
            round(label.position[0] + dx * distance, 10),
            round(label.position[1] + dy * distance, 10),
        )
        field.angle = 90 if dy else 0
        effects = (field.attributes or TextAttributes()).model_copy(deep=True)
        effects.justify_h = JustifyH.left if side in ("right", "top") else JustifyH.right
        effects.justify_v = JustifyV.center
        field.attributes = effects
