"""Create formatted schematic labels and sheet references from design choices."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, overload
from uuid import UUID, uuid4

from eedesigncore.common.models import Color, JustifyH, JustifyV, Property, Stroke, TextAttributes
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.autoplace import autoplace_fields
from eedesigncore.sch.measurement import (
    MeasuredSchematicItem,
    measure_schematic_item,
    measured_item,
)
from eedesigncore.sch.models import Label, LabelKind, Sheet, SheetPin
from eedesigncore.text_resolution import TextResolutionContext

_Direction = Literal["left", "right", "up", "down"]
_Shape = Literal["input", "output", "passive", "bidirectional", "tri_state"]
_GRID_MM = 1.27


@overload
def create_global_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    show_intersheet_refs: bool = True,
    text_size_mm: float = 1.27,
    font: SymbolFont | None = None,
    uuid: str | None = None,
    with_bounds: Literal[False] = False,
    text_context: TextResolutionContext | None = None,
) -> Label: ...


@overload
def create_global_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    show_intersheet_refs: bool = True,
    text_size_mm: float = 1.27,
    font: SymbolFont | None = None,
    uuid: str | None = None,
    with_bounds: Literal[True],
    text_context: TextResolutionContext | None = None,
) -> MeasuredSchematicItem[Label]: ...


@overload
def create_global_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    show_intersheet_refs: bool = True,
    text_size_mm: float = 1.27,
    font: SymbolFont | None = None,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> Label | MeasuredSchematicItem[Label]: ...


def create_global_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    show_intersheet_refs: bool = True,
    text_size_mm: float = 1.27,
    font: SymbolFont | None = None,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> Label | MeasuredSchematicItem[Label]:
    """Create a global label with coherent orientation and placed page references.

    The exact wire contact is retained. Direction is the side occupied by the
    label text, not electrical flow. Metadata fields are hidden. A supplied
    UUID is retained; otherwise this explicit creation operation allocates one.
    No schematic is changed and no file is read or written.
    """
    label = _create_label(
        LabelKind.global_,
        net_name,
        at,
        direction,
        shape,
        net_class,
        properties,
        show_intersheet_refs,
        text_size_mm,
        uuid,
        font=font,
    )
    return measured_item(label, text_context, font=font) if with_bounds else label


@overload
def create_hierarchical_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: Literal[False] = False,
    text_context: TextResolutionContext | None = None,
) -> Label: ...


@overload
def create_hierarchical_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: Literal[True],
    text_context: TextResolutionContext | None = None,
) -> MeasuredSchematicItem[Label]: ...


@overload
def create_hierarchical_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> Label | MeasuredSchematicItem[Label]: ...


def create_hierarchical_label(
    net_name: str,
    *,
    at: tuple[float, float],
    direction: Literal["left", "right", "up", "down"] = "right",
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] = "passive",
    net_class: str | None = None,
    properties: Mapping[str, str] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> Label | MeasuredSchematicItem[Label]:
    """Create a hierarchical label without moving its wire contact.

    The caller decides the net name, contact, body direction, and electrical
    shape. This does not create a matching pin on a parent sheet reference.
    """
    label = _create_label(
        LabelKind.hierarchical,
        net_name,
        at,
        direction,
        shape,
        net_class,
        properties,
        False,
        text_size_mm,
        uuid,
    )
    return measured_item(label, text_context) if with_bounds else label


@overload
def create_sheet(
    title: str,
    filename: str,
    *,
    at: tuple[float, float],
    size: tuple[float, float] = (50.8, 30.48),
    font: SymbolFont | None = None,
    fill_color: Color | None = None,
    border_color: Color | None = None,
    uuid: str | None = None,
    with_bounds: Literal[False] = False,
    text_context: TextResolutionContext | None = None,
) -> Sheet: ...


@overload
def create_sheet(
    title: str,
    filename: str,
    *,
    at: tuple[float, float],
    size: tuple[float, float] = (50.8, 30.48),
    font: SymbolFont | None = None,
    fill_color: Color | None = None,
    border_color: Color | None = None,
    uuid: str | None = None,
    with_bounds: Literal[True],
    text_context: TextResolutionContext | None = None,
) -> MeasuredSchematicItem[Sheet]: ...


@overload
def create_sheet(
    title: str,
    filename: str,
    *,
    at: tuple[float, float],
    size: tuple[float, float] = (50.8, 30.48),
    font: SymbolFont | None = None,
    fill_color: Color | None = None,
    border_color: Color | None = None,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> Sheet | MeasuredSchematicItem[Sheet]: ...


def create_sheet(
    title: str,
    filename: str,
    *,
    at: tuple[float, float],
    size: tuple[float, float] = (50.8, 30.48),
    font: SymbolFont | None = None,
    fill_color: Color | None = None,
    border_color: Color | None = None,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> Sheet | MeasuredSchematicItem[Sheet]:
    """Create a formatted sheet reference with every corner on the 50 mil grid.

    Snap the top-left corner to the nearest grid point and round dimensions
    upward. The title uses bold 75 mil text; the filename uses 35 mil text.
    Both fields align left. The border is dark green and the fill is pale green.
    This creates the parent-sheet rectangle, not a child file or project paths.
    """
    _text(title, "sheet title")
    _text(filename, "sheet filename")
    _point(at)
    _point(size)
    if any(value <= 0 for value in size):
        raise ValueError("Sheet dimensions must be positive.")
    relative = PurePosixPath(filename.replace("\\", "/"))
    windows = PureWindowsPath(filename)
    if windows.drive or windows.root or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("The sheet filename must stay relative to its parent project.")
    if relative.suffix.lower() != ".kicad_sch":
        raise ValueError("The sheet filename must end with .kicad_sch.")
    x, y = (_nearest_grid(value) for value in at)
    width, height = (_upward_grid(value) for value in size)
    if not all(math.isfinite(value) for value in (x + width, y + height)):
        raise ValueError("Sheet corners must be finite.")
    sheet = Sheet(
        position=(x, y),
        size=(width, height),
        fields_autoplaced=True,
        uuid=_uuid(uuid),
        stroke=Stroke(
            width=0.254, color=(border_color or Color(r=67, g=120, b=76, a=1)).model_copy()
        ),
        fill_color=(fill_color or Color(r=232, g=245, b=233, a=1)).model_copy(),
        fields=[
            Property(
                name="Sheetname",
                value=title,
                position=(x, round(y - (0.254 / 2 + 0.0004 + 1.905 * 0.5), 10)),
                attributes=TextAttributes(
                    font_size=(1.905, 1.905),
                    bold=True,
                    justify_h=JustifyH.left,
                    justify_v=JustifyV.bottom,
                ),
            ),
            Property(
                name="Sheetfile",
                value=str(relative),
                position=(x, round(y + height + 0.254 / 2 + 0.0004 + 0.889 * 0.4, 10)),
                attributes=TextAttributes(
                    font_size=(0.889, 0.889), justify_h=JustifyH.left, justify_v=JustifyV.top
                ),
            ),
        ],
    )
    title_bounds = measure_schematic_item(sheet, font=font, text_context=text_context)
    if not title_bounds.complete:
        raise ValueError("Cannot fit sheet title: " + "; ".join(title_bounds.diagnostics))
    title_right = max(
        (part.bounds.max_x for part in title_bounds.parts if "field:0:Sheetname" in part.name),
        default=x,
    )
    sheet.size = (max(width, _upward_grid(title_right - x + _GRID_MM)), height)
    return measured_item(sheet, text_context, font=font) if with_bounds else sheet


@overload
def add_sheet_pin(
    sheet: Sheet,
    net: str | Label,
    *,
    side: Literal["left", "right", "top", "bottom"] = "left",
    allow_top_bottom: bool = False,
    offset_mm: float | None = None,
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: Literal[False] = False,
    text_context: TextResolutionContext | None = None,
) -> SheetPin: ...


@overload
def add_sheet_pin(
    sheet: Sheet,
    net: str | Label,
    *,
    side: Literal["left", "right", "top", "bottom"] = "left",
    allow_top_bottom: bool = False,
    offset_mm: float | None = None,
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: Literal[True],
    text_context: TextResolutionContext | None = None,
) -> MeasuredSchematicItem[SheetPin]: ...


@overload
def add_sheet_pin(
    sheet: Sheet,
    net: str | Label,
    *,
    side: Literal["left", "right", "top", "bottom"] = "left",
    allow_top_bottom: bool = False,
    offset_mm: float | None = None,
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> SheetPin | MeasuredSchematicItem[SheetPin]: ...


def add_sheet_pin(
    sheet: Sheet,
    net: str | Label,
    *,
    side: Literal["left", "right", "top", "bottom"] = "left",
    allow_top_bottom: bool = False,
    offset_mm: float | None = None,
    shape: Literal["input", "output", "passive", "bidirectional", "tri_state"] | None = None,
    text_size_mm: float = 1.27,
    uuid: str | None = None,
    with_bounds: bool = False,
    text_context: TextResolutionContext | None = None,
) -> SheetPin | MeasuredSchematicItem[SheetPin]:
    """Append a grid-aligned sheet pin with inward text and return the pin.

    A child hierarchical label supplies its name and shape. Offsets run from
    the top or left. Omit the offset for the first free 200 mil slot.
    The sheet corners must be on the 50 mil grid. Child files stay unchanged.
    """
    if side not in ("left", "right", "top", "bottom"):
        raise ValueError("The sheet pin side must be left, right, top, or bottom.")
    if side in ("top", "bottom") and not allow_top_bottom:
        raise ValueError("Top and bottom sheet pins require allow_top_bottom=True.")
    if isinstance(net, Label):
        if net.kind != LabelKind.hierarchical:
            raise ValueError("A sheet pin requires a hierarchical child label.")
        name = net.content
        if shape is not None and shape != net.shape:
            raise ValueError("The pin shape must match the child label.")
        pin_shape = net.shape
    else:
        name = net
        pin_shape = shape or "passive"
    _text(name, "sheet pin name")
    if pin_shape not in ("input", "output", "passive", "bidirectional", "tri_state"):
        raise ValueError("The sheet pin electrical shape is unsupported.")
    if any(pin.name == name for pin in sheet.pins):
        raise ValueError("The sheet already has a pin with this name.")
    if not math.isfinite(text_size_mm) or text_size_mm <= 0:
        raise ValueError("The pin text size must be finite and positive.")
    _point(sheet.position)
    _point(sheet.size)
    if any(value <= 0 for value in sheet.size):
        raise ValueError("The sheet dimensions must be positive.")
    if any(abs(value - _nearest_grid(value)) > 1e-8 for value in (*sheet.position, *sheet.size)):
        raise ValueError("Sheet corners must be on the 50 mil grid. Use create_sheet first.")
    x, y = sheet.position
    width, height = sheet.size
    length = height if side in ("left", "right") else width

    def position(offset: float) -> tuple[float, float]:
        if side in ("left", "right"):
            return (round(x + (width if side == "right" else 0), 10), round(y + offset, 10))
        return (round(x + offset, 10), round(y + (height if side == "bottom" else 0), 10))

    def occupied(point: tuple[float, float]) -> bool:
        return any(math.dist(point, pin.position) < 1e-8 for pin in sheet.pins)

    if offset_mm is None:
        offset = 5.08
        while offset < length and occupied(position(offset)):
            offset = round(offset + 5.08, 10)
    else:
        if not math.isfinite(offset_mm) or not 0 < offset_mm < length:
            raise ValueError("The pin offset must lie strictly inside the sheet edge.")
        offset = _nearest_grid(offset_mm)
    if not 0 < offset < length:
        raise ValueError("No pin slot fits. Enlarge the sheet or specify an interior offset.")
    contact = position(offset)
    if occupied(contact):
        raise ValueError("Another sheet pin already occupies this contact.")
    pin = SheetPin(
        name=name,
        shape=pin_shape,
        position=contact,
        angle={"left": 180, "right": 0, "top": 90, "bottom": 270}[side],
        uuid=_uuid(uuid),
        attributes=TextAttributes(
            font_size=(text_size_mm, text_size_mm),
            justify_h=JustifyH.left if side in ("left", "bottom") else JustifyH.right,
            justify_v=JustifyV.center,
        ),
    )
    result = measured_item(pin, text_context) if with_bounds else pin
    sheet.pins.append(pin)
    return result


def _text(value: str, description: str) -> None:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"The {description} must be nonempty text without control characters.")


def _point(value: tuple[float, float]) -> None:
    if len(value) != 2 or not all(math.isfinite(coordinate) for coordinate in value):
        raise ValueError("Coordinates and dimensions must contain two finite values.")


def _uuid(value: str | None) -> str:
    if value is not None:
        try:
            UUID(value)
        except (ValueError, AttributeError) as error:
            raise ValueError("A supplied item UUID must be valid.") from error
    return str(uuid4()) if value is None else value


def _nearest_grid(value: float) -> float:
    return round(math.floor(abs(value) / _GRID_MM + 0.5) * math.copysign(_GRID_MM, value), 10)


def _upward_grid(value: float) -> float:
    return round(max(1, math.ceil(value / _GRID_MM - 1e-10)) * _GRID_MM, 10)


def _create_label(
    kind: LabelKind,
    net_name: str,
    at: tuple[float, float],
    direction: _Direction,
    shape: _Shape,
    net_class: str | None,
    properties: Mapping[str, str] | None,
    show_intersheet_refs: bool,
    text_size_mm: float,
    uuid: str | None,
    font: SymbolFont | None = None,
) -> Label:
    _text(net_name, "net name")
    _point(at)
    if direction not in ("left", "right", "up", "down"):
        raise ValueError("The label direction must be left, right, up, or down.")
    if shape not in ("input", "output", "passive", "bidirectional", "tri_state"):
        raise ValueError(
            "The electrical shape must be input, output, passive, bidirectional, or tri_state."
        )
    if not math.isfinite(text_size_mm) or text_size_mm <= 0:
        raise ValueError("The label text size must be finite and positive.")
    attrs = TextAttributes(
        font_size=(text_size_mm, text_size_mm),
        justify_v=JustifyV.center,
        justify_h=JustifyH.left if direction in ("right", "up") else JustifyH.right,
    )
    fields: list[Property] = []
    if kind == LabelKind.global_:
        fields.append(
            Property(
                name="Intersheetrefs",
                value="${INTERSHEET_REFS}",
                attributes=TextAttributes(font_size=attrs.font_size),
            )
        )
    if net_class is not None:
        _text(net_class, "net class")
        fields.append(
            Property(
                name="Netclass",
                value=net_class,
                visible=False,
                attributes=TextAttributes(font_size=attrs.font_size),
            )
        )
    for name, value in (properties or {}).items():
        _text(name, "property name")
        if name.casefold() in ("netclass", "intersheetrefs"):
            raise ValueError(
                "Use the dedicated net-class or page-reference option for reserved fields."
            )
        if not isinstance(value, str) or any(ord(c) < 32 for c in value):
            raise ValueError("Property values must be text without control characters.")
        fields.append(
            Property(
                name=name,
                value=value,
                visible=False,
                attributes=TextAttributes(font_size=attrs.font_size),
            )
        )
    label = Label(
        kind=kind,
        content=net_name,
        position=at,
        angle={"right": 0, "up": 90, "left": 180, "down": 270}[direction],
        shape=shape,
        attributes=attrs,
        fields=fields,
        uuid=_uuid(uuid),
    )
    autoplace_fields(label, font=font)
    if kind == LabelKind.global_ and not show_intersheet_refs:
        label.fields[0].visible = False
    label.fields_autoplaced = True
    return label
