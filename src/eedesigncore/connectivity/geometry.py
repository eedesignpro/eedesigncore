"""Connection-point collection, per the item participation table.

Source of truth: audit ``connectivity-items-geometry-2026-07-28.md`` section 3.
An item type that does not take part in connectivity contributes nothing, and the
exclusions below each carry the citation that justifies them.

Two rules are easy to get wrong and both are load-bearing.

**Library symbol Y is negated at parse time.** ``parseXY( true )`` inverts Y for
every library-symbol geometry element, pin positions included
(`sch_io_kicad_sexpr_parser.cpp:169-178`, `:1669`). A pin position read from a
library symbol must be negated before the placement transform applies. Audit
headline 11.

**The transform matrix naming is misleading.** ``x1`` and ``y1`` produce the
output X. ``x2`` and ``y2`` produce the output Y (`transform.cpp:44-47`). Port the
arithmetic, not the intuition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from eedesigncore.common.sch_family.ordering import resolve_symbol_definition
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.connectivity.units import IUPoint, point_to_iu

if TYPE_CHECKING:
    from eedesigncore.sch.models import Schematic

# A placement transform as (x1, y1, x2, y2), from the placement angle.
# `sch_io_kicad_sexpr_parser.cpp:3175-3189`.
Transform = tuple[int, int, int, int]

_ANGLE_TRANSFORM: dict[int, Transform] = {
    0: (1, 0, 0, 1),
    90: (0, 1, -1, 0),
    180: (-1, 0, 0, -1),
    270: (0, -1, 1, 0),
}

# Incremental transforms applied AFTER the angle, by post-multiplication.
# `sch_symbol.cpp:2394-2408`.
_MIRROR_X: Transform = (1, 0, 0, -1)
_MIRROR_Y: Transform = (-1, 0, 0, 1)


def compose(base: Transform, temp: Transform) -> Transform:
    """Post-multiply two transforms, following `sch_symbol.cpp:2508-2514`."""
    bx1, by1, bx2, by2 = base
    tx1, ty1, tx2, ty2 = temp
    return (
        bx1 * tx1 + bx2 * ty1,
        by1 * tx1 + by2 * ty1,
        bx1 * tx2 + bx2 * ty2,
        by1 * tx2 + by2 * ty2,
    )


def apply(transform: Transform, point: tuple[float, float]) -> tuple[float, float]:
    """Map a point through a transform, following `transform.cpp:44-47`.

    Note the index layout. x1 and y1 build the output X.
    """
    x1, y1, x2, y2 = transform
    return (x1 * point[0] + y1 * point[1], x2 * point[0] + y2 * point[1])


def symbol_transform(angle: float, mirror: str | None) -> Transform:
    """The transform for a placement angle and optional mirror axis."""
    base = _ANGLE_TRANSFORM.get(int(angle) % 360)
    if base is None:
        raise ValueError(
            f"placement angle {angle} is not one of 0, 90, 180, 270. KiCad stores "
            "only these four for a schematic symbol "
            "(sch_io_kicad_sexpr_parser.cpp:3175-3189)."
        )
    if mirror == "x":
        return compose(base, _MIRROR_X)
    if mirror == "y":
        return compose(base, _MIRROR_Y)
    return base


@dataclass(frozen=True)
class PinRef:
    """One pin of one placed symbol, at its derived sheet position."""

    reference: str
    pin_number: str
    point: IUPoint
    electrical_type: str
    lib_id: str
    # The library pin name. A legacy power pin on a non-power symbol names the
    # net from this (`sch_pin.cpp:1441-1455`).
    pin_name: str = ""
    library_pin_name: str | None = None
    # True when the parent library symbol is a power symbol. Such a net takes its
    # name from the symbol VALUE, not from the pin (`sch_pin.cpp:1441-1455`).
    from_power_symbol: bool = False
    # The symbol VALUE field, which names the net for a power symbol.
    symbol_value: str = ""
    # The unit letter for a multi-unit symbol, empty for a single-unit one. The
    # implicit NET NAME carries it while the netlist node reference does not.
    unit_letter: str = ""
    # The placed symbol's own identifier. Branch A of the implicit-name rule uses
    # it when the reference is unannotated (`sch_pin.cpp:1504-1515`).
    symbol_uuid: str = ""
    # Every pin of the SAME placed symbol, as (shown name, shown number, is
    # no-connect). Stands in for ``symbol->GetPins( &aPath )`` (`sch_pin.cpp:1478`),
    # which the implicit-name rule needs to know whether this symbol carries the
    # same pin NAME on more than one pin. It cannot be recovered from a net's own
    # pins, because the twin usually sits on a different net. One tuple is built
    # per symbol and shared by reference across its pins.
    symbol_pins: tuple[tuple[str, str, bool], ...] = ()
    # Whether the parent symbol, or a sheet above it, is excluded from the board.
    # Such a symbol is skipped when the KiCad netlist is written
    # (`netlist_exporter_xml.cpp:316`, `:1178`, both guarded on GNL_OPT_KICAD),
    # so its pins are not netlist nodes. It is still electrically connected: this
    # is a rule about the netlist VIEW, not about connectivity.
    excluded_from_board: bool = False
    # Whether the library pin is visible. An INVISIBLE power-input pin on a
    # non-power symbol acts as global power, which is legacy support the source
    # spells out (`sch_pin.cpp:371-372`).
    visible: bool = True
    # Whether the parent symbol is a LOCAL power symbol. A local power symbol is
    # never global, even with invisible pins (`sch_pin.cpp:366-369`).
    from_local_power_symbol: bool = False
    # The LIBRARY symbol's Reference field, which is a prefix such as "R" or "#FLG"
    # and not the placed instance's reference. A leading hash marks a symbol that
    # is not a real component, and ``GetDriverPriority`` drops such a pin to rank
    # NONE (`connection_graph.cpp:607-608`). A power flag is the common case.
    lib_reference: str = ""
    symbol_index: int = 0
    pin_index: int = 0
    source_pin_key: str = ""

    @property
    def is_annotated(self) -> bool:
        """False when the reference ends in a question mark.

        ``GetRefDesUnannotated`` builds that form as prefix plus "?"
        (`common/refdes_utils.cpp:48-51`), and Branch A tests exactly the last
        character (`sch_pin.cpp:1504`).
        """
        return bool(self.reference) and not self.reference.endswith("?")

    @property
    def is_no_connect(self) -> bool:
        """A no-connect pin is electrically isolated, not merely unchecked.

        ``SCH_PIN::ConnectionPropagatesTo`` returns false for this type, so the
        two-way gate drops every link to it (`sch_pin.cpp:1631-1634`, audit
        headline 7).
        """
        return self.electrical_type == "no_connect"

    @property
    def is_global_power(self) -> bool:
        """Ports ``SCH_PIN::IsGlobalPower`` (`sch_pin.cpp:357-374`), in order.

        Not a power-input pin, so no. Parent is a global power symbol, so yes.
        Parent is a LOCAL power symbol, so no, even when the pin is invisible.
        Otherwise the legacy rule: an invisible power-input pin on an ordinary
        symbol acts as global power.
        """
        if self.electrical_type != "power_in":
            return False
        if self.from_power_symbol and not self.from_local_power_symbol:
            return True
        if self.from_local_power_symbol:
            return False
        return not self.visible

    @property
    def is_local_power(self) -> bool:
        """Ports ``SCH_PIN::IsLocalPower`` (`sch_pin.cpp:377-381`)."""
        return self.electrical_type == "power_in" and self.from_local_power_symbol

    @property
    def is_power(self) -> bool:
        """A power pin that DRIVES a net name, at rank 5 or 6.

        Narrow on purpose. The power short circuit in
        ``SCH_PIN::GetDefaultNetName`` requires the library pin's parent to be a
        power symbol (`sch_pin.cpp:1441-1455`). An ordinary power-input pin on a
        real part, such as a supply pin on an FPGA, is just a pin.

        An earlier version returned true for every power-input pin. Measured, one
        corpus design carries 146 power-input pins of which only 28 sit on a power
        symbol, so the broad rule hijacked the name of most nets and accounted for
        114 of 115 grouping misses on that design (BS-12.1-5).
        """
        return self.is_local_power or self.is_global_power


@dataclass
class SheetGeometry:
    """Every connection point on one sheet, grouped by what contributed it."""

    wire_ends: list[tuple[IUPoint, IUPoint]] = field(default_factory=list)
    bus_ends: list[tuple[IUPoint, IUPoint]] = field(default_factory=list)
    junctions: list[IUPoint] = field(default_factory=list)
    no_connects: list[IUPoint] = field(default_factory=list)
    bus_entries: list[tuple[IUPoint, IUPoint]] = field(default_factory=list)
    pins: list[PinRef] = field(default_factory=list)
    # (point, kind, text) for label, global_label, hierarchical_label.
    labels: list[tuple[IUPoint, str, str]] = field(default_factory=list)
    # (point, pin name, shape token). The SHAPE decides a driver contest
    # between two sheet pins on one net: an output shape wins outright
    # (`connection_graph.cpp:234-246`).
    sheet_pins: list[tuple[IUPoint, str, str]] = field(default_factory=list)
    # (point, pin name, child sheet name). Propagation needs the child path, and
    # a bare pin name cannot give it.
    sheet_pin_children: list[tuple[IUPoint, str, str]] = field(default_factory=list)
    # Names a directive label attaches, which never drive a net (audit 2 h5).
    directive_labels: list[tuple[IUPoint, str]] = field(default_factory=list)


def _symbol_field(placed: object, name: str) -> str:
    """One named property of a placed symbol, empty when absent."""
    for prop in getattr(placed, "fields", []) or []:
        if getattr(prop, "name", None) == name:
            return str(getattr(prop, "value", ""))
    return ""


def instance_reference(placed: object, sheet_uuid_path: str) -> str:
    """The reference for one placed symbol on one sheet path.

    Ports ``SCH_SYMBOL::GetRef`` (`eeschema/sch_symbol.cpp:624-670`), the part a
    port cannot skip. The instance table is matched on the sheet path, and only
    when no entry matches does the Reference PROPERTY serve as the fallback
    (`:653-657`).

    This matters wherever one sheet FILE sits at more than one place in a tree.
    Every instance shares the property and each has its own real reference, so
    reading the property collapses them into one and merges nets that are
    distinct.
    """
    for instance in getattr(placed, "instances", None) or []:
        for path in getattr(instance, "paths", None) or []:
            if getattr(path, "path", None) == sheet_uuid_path:
                reference = str(getattr(path, "reference", "") or "")
                if reference:
                    return reference
    return _symbol_field(placed, "Reference")


def _unit_letter(symbol: Symbol, unit: int) -> str:
    """The sub-reference letter for a multi-unit symbol, else empty.

    Measured against KiCad's netlist: the implicit net name for unit 8 of a
    multi-unit symbol reads ``U2H``, while the netlist NODE reference stays the
    bare ``U2``. The audit reading of ``GetDefaultNetName`` says the name is built
    without the unit token, and the oracle contradicts it (ADR-097).

    The default sub-part scheme has no separator and starts at the letter A
    (`schematic_settings.cpp:221`, `:224`).
    """
    unit_count = len({u.unit_index for u in symbol.units if u.unit_index > 0})
    if unit_count <= 1 or unit < 1:
        return ""
    letters = ""
    remaining = unit
    while remaining > 0:
        remaining, rem = divmod(remaining - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _library_pins(symbol: Symbol, unit: int, body_style: int) -> list[Pin]:
    """Every pin of the requested unit and body style.

    A unit index of 0 means the pin belongs to every unit, and the same holds for
    body style 0 (audit section 7).
    """
    out: list[Pin] = []
    for sym_unit in symbol.units:
        if sym_unit.unit_index not in (0, unit):
            continue
        if sym_unit.body_style_index not in (0, body_style):
            continue
        for item in sym_unit.items:
            if isinstance(item, Pin):
                out.append(item)
    return out


def pin_sheet_position(
    pin_position: tuple[float, float],
    transform: Transform,
    instance_position: tuple[float, float],
) -> IUPoint:
    """Derive a pin's sheet position in exact integer units.

    Three steps in this order, per audit section 6.

    1. Negate the library Y, undoing the parse-time inversion.
    2. Apply the placement transform.
    3. Add the instance position.
    """
    negated = (pin_position[0], -pin_position[1])
    rotated = apply(transform, negated)
    return point_to_iu((rotated[0] + instance_position[0], rotated[1] + instance_position[1]))


def collect(
    schematic: Schematic,
    sheet_uuid_path: str = "",
    child_names: dict[str, str] | None = None,
    sheet_excluded_from_board: bool = False,
) -> SheetGeometry:
    """Collect every connection point on one sheet.

    *sheet_uuid_path* is the instance path of THIS sheet, built from the root
    schematic identifier followed by each sheet identifier down the tree. A
    reference resolves against it, which is what distinguishes one instance of a
    repeated sheet file from another. An empty path falls back to the Reference
    property, which is what ``GetRef`` itself does when no instance matches.

    *child_names* maps a sheet identifier to that sheet's RESOLVED name. A sheet
    name may hold a text variable, and KiCad resolves it before the name is used
    anywhere (``GetShownText``). Propagation matches a sheet pin's child against a
    sheet PATH component, and the path is built from resolved names, so the child
    recorded here must be resolved too or the two can never match. A caller that
    passes nothing gets the raw field, which is correct only for a name with no
    variable in it.

    *sheet_excluded_from_board* is true when this sheet, or any sheet above it,
    is excluded from the board. Source ORs the sheet's own exclusion with each
    symbol's (`netlist_exporter_xml.cpp:316`), so the flag has to come down the
    tree rather than being read here.

    Excluded with citations, because a silent omission is the failure mode here.
    A line on the note layer is not connectable (`sch_line.cpp:664`). Text, a text
    box, a table, a rule area, an image, and a shape all return false from the
    base ``IsConnectable`` (`sch_item.h:527`). None of them contribute a point.
    """
    geo = SheetGeometry()

    for line in schematic.lines:
        ends = (point_to_iu(line.start), point_to_iu(line.end))
        if line.line_type.value == "wire":
            geo.wire_ends.append(ends)
        elif line.line_type.value == "bus":
            geo.bus_ends.append(ends)
        # LineType.polyline sits on the note layer and never connects.

    geo.junctions = [point_to_iu(j.position) for j in schematic.junctions]
    geo.no_connects = [point_to_iu(n.position) for n in schematic.no_connects]

    for entry in schematic.bus_entries:
        start = point_to_iu(entry.position)
        end = point_to_iu((entry.position[0] + entry.size[0], entry.position[1] + entry.size[1]))
        geo.bus_entries.append((start, end))

    for label in schematic.labels:
        point = point_to_iu(label.position)
        kind = label.kind.value if hasattr(label.kind, "value") else str(label.kind)
        if kind == "netclass_flag":
            geo.directive_labels.append((point, label.content))
        else:
            geo.labels.append((point, kind, label.content))

    for sheet in schematic.sheets:
        # The resolved name when the caller supplied one, because a raw name
        # holding a text variable never matches a resolved path component.
        child_name = (child_names or {}).get(
            str(sheet.uuid or ""), _symbol_field(sheet, "Sheetname")
        )
        for sheet_pin in sheet.pins:
            point = point_to_iu(sheet_pin.position)
            geo.sheet_pins.append((point, sheet_pin.name, str(sheet_pin.shape or "")))
            geo.sheet_pin_children.append((point, sheet_pin.name, child_name))

    by_name = {s.name: s for s in schematic.lib_symbols}
    for symbol_index, placed in enumerate(schematic.symbols):
        symbol = by_name.get(placed.lib_name or "") or by_name.get(placed.lib_id)
        if symbol is None:
            continue
        symbol, _ = resolve_symbol_definition(symbol, by_name)
        transform = symbol_transform(placed.angle, placed.mirror)
        reference = instance_reference(placed, sheet_uuid_path)
        value = _symbol_field(placed, "Value")
        power_type = str(getattr(symbol, "power_type", "") or "")
        is_power = bool(power_type)
        is_local_power = power_type == "local"
        unit_letter = _unit_letter(symbol, placed.unit or 1)
        # `libSymbolRef`, the LIBRARY symbol's Reference field
        # (`connection_graph.cpp:583-591`). Not the placed instance's reference.
        lib_reference = _symbol_field(symbol, "Reference")
        # Built ONCE per placed symbol and shared by reference, because the
        # implicit-name rule asks whether this symbol repeats a pin name and the
        # answer is a property of the symbol, not of any one net.
        placed_pins = list(_library_pins(symbol, placed.unit or 1, placed.body_style or 1))
        library_names = [pin.name for pin in placed_pins]
        alternates = {pin.number: pin.alternate for pin in placed.pins if pin.alternate}
        for index, pin in enumerate(placed_pins):
            if pin.number in alternates:
                alternate = next(
                    (a for a in pin.alternates if a.name == alternates[pin.number]), None
                )
                if alternate is None:
                    raise ValueError("The selected alternate pin is absent.")
                placed_pins[index] = pin.model_copy(
                    update={
                        "name": alternate.name,
                        "shape": alternate.shape,
                        "electrical_type": alternate.electrical_type,
                    }
                )
        symbol_pins = tuple(
            (
                other.name,
                other.number,
                str(
                    other.electrical_type.value
                    if hasattr(other.electrical_type, "value")
                    else other.electrical_type
                )
                == "no_connect",
            )
            for other in placed_pins
        )
        for pin_index, pin in enumerate(placed_pins):
            geo.pins.append(
                PinRef(
                    reference=reference,
                    symbol_index=symbol_index,
                    pin_index=pin_index,
                    source_pin_key=f"{pin.number}@{pin.position[0]},{pin.position[1]},{pin.angle}",
                    pin_number=pin.number,
                    point=pin_sheet_position(pin.position, transform, placed.position),
                    electrical_type=(
                        pin.electrical_type.value
                        if hasattr(pin.electrical_type, "value")
                        else str(pin.electrical_type)
                    ),
                    lib_id=placed.lib_id,
                    pin_name=pin.name,
                    library_pin_name=library_names[pin_index],
                    from_power_symbol=is_power,
                    symbol_pins=symbol_pins,
                    excluded_from_board=(sheet_excluded_from_board or placed.on_board is False),
                    symbol_value=value,
                    unit_letter=unit_letter,
                    symbol_uuid=str(getattr(placed, "uuid", "") or ""),
                    visible=bool(getattr(pin, "visible", True)),
                    from_local_power_symbol=is_local_power,
                    lib_reference=lib_reference,
                )
            )
    return geo
