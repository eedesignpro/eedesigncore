"""The category of a change, and the name a person reads for an item.

The category is a LIBRARY rule, set by the item type and the changed field. It
is not a KiCad rule and it does not run a connection check. Two consequences:

* A track moved so that it touches another net is labelled ``geometry``. Only
  its ``net`` field is labelled ``electrical``. The net summary that
  :func:`~eedesigncore.design_changes.compare_designs` adds for a board shows
  the effect on each net.
* A schematic wire, junction, no-connect, bus entry, or net label sets
  connections by its position, so a move of one is labelled ``electrical``.
  :func:`~eedesigncore.compare_schematic_electrical` gives the proof.

Categories:

``electrical``
    Nets, pad numbers and pad copper, pin data, and the schematic items that
    make connections.
``part``
    Which part is placed and its fields: library identifiers, Reference,
    Value, attributes such as DNP, 3D models, and the schematic link.
``geometry``
    Position, angle, size, shape, layer, and line width.
``settings``
    Board and footprint rules, setup, layers, title block, and project files.
``cosmetic``
    Text effects, visibility, colour, locks, groups, identifiers, and the file
    version and generator stamps.
``derived``
    Data KiCad recalculates: zone fills and stored text outlines.
``other``
    A field no rule covers. Review it.
"""

from __future__ import annotations

import json

from eedesigncore.comparison import _Node
from eedesigncore.design_changes.records import ChangeCategory

_E: ChangeCategory = "electrical"
_P: ChangeCategory = "part"
_G: ChangeCategory = "geometry"
_S: ChangeCategory = "settings"
_C: ChangeCategory = "cosmetic"
_D: ChangeCategory = "derived"

_STAMPS = {"version": _C, "generator": _C, "generator_version": _C, "uuid": _C, "id": _C}
_MARGINS = {
    "clearance": _S,
    "solder_mask_margin": _S,
    "solder_paste_margin": _S,
    "solder_paste_margin_ratio": _S,
}
_VIA_FINISH = {
    name: _S
    for name in (
        "teardrops",
        "tenting",
        "capping",
        "covering",
        "filling",
        "plugging",
        "backdrill",
        "tertiary_drill",
        "front_post_machining",
        "back_post_machining",
    )
}

# The rule for each changed field, by item type. "*" is the rule for a field the
# table does not name. A type the table does not name uses the generic rule.
_RULES: dict[str, dict[str, ChangeCategory]] = {
    "Board": {
        **_STAMPS,
        "nets": _E,
        "setup": _S,
        "general": _S,
        "layers": _S,
        "paper": _S,
        "title_block": _S,
        "properties": _S,
        "variants": _P,
        "groups": _C,
        "embedded_files": _P,
        "embedded_fonts": _C,
    },
    "Footprint": {
        **_STAMPS,
        **_MARGINS,
        "fp_id": _P,
        "properties": _P,
        "attributes": _P,
        "path": _P,
        "sheetname": _P,
        "sheetfile": _P,
        "description": _P,
        "keywords": _P,
        "models": _P,
        "component_classes": _P,
        "fp_filters": _P,
        "unit_info": _P,
        "variants": _P,
        "embedded_files": _P,
        "embedded_fonts": _C,
        "at": _G,
        "layer": _G,
        "placed": _C,
        "locked": _C,
        "groups": _C,
        "zone_connect": _E,
        "net_tie_pad_groups": _E,
        "jumper_pad_groups": _E,
        "duplicate_pad_numbers_are_jumpers": _E,
        "private_layers": _S,
        "stackup": _S,
    },
    "Pad": {
        **_MARGINS,
        "id": _C,
        "net_name": _E,
        "net_code": _C,
        "number": _E,
        "pinfunction": _E,
        "pintype": _E,
        "pad_type": _E,
        "layers": _E,
        "zone_connect": _E,
        "thermal_gap": _E,
        "thermal_spoke_width": _E,
        "thermal_spoke_angle": _E,
        "zone_layer_connections": _E,
        "remove_unused_layers": _E,
        "keep_end_layers": _E,
        "die_length": _E,
        "die_delay": _E,
        "teardrop_params": _S,
        "tenting_front": _S,
        "tenting_back": _S,
        "*": _G,
    },
    "TrackSegment": {"uuid": _C, "net": _E, "locked": _C, "solder_mask_margin": _S, "*": _G},
    "TrackArc": {"uuid": _C, "net": _E, "locked": _C, "solder_mask_margin": _S, "*": _G},
    "Via": {
        **_VIA_FINISH,
        "uuid": _C,
        "net": _E,
        "layers": _E,
        "via_type": _E,
        "free": _E,
        "zone_layer_connections": _E,
        "unconnected_layer_mode": _E,
        "padstack": _E,
        "locked": _C,
        "*": _G,
    },
    "Zone": {
        "id": _C,
        "net": _E,
        "net_name": _E,
        "connect_pads_mode": _E,
        "connect_pads_clearance": _E,
        "priority": _E,
        "keepout": _E,
        "layers": _E,
        "layer": _E,
        "filled_polygons": _D,
        "filled_areas_thickness": _D,
        "locked": _C,
        "name": _C,
        "fill": _S,
        "min_thickness": _S,
        "hatch_style": _S,
        "hatch_pitch": _S,
        "teardrop_type": _S,
        "placement": _S,
        "layer_properties": _S,
        "*": _G,
    },
    "Property": {"name": _P, "value": _P, "id": _C, "*": _C},
    "PlacedSymbol": {
        "uuid": _C,
        "lib_id": _P,
        "lib_name": _P,
        "unit": _P,
        "body_style": _P,
        "dnp": _P,
        "in_bom": _P,
        "on_board": _P,
        "in_pos_files": _P,
        "exclude_from_sim": _P,
        "instances": _P,
        "pins": _E,
        "fields": _P,
        "fields_autoplaced": _C,
        "*": _G,
    },
    "PlacedSymbolPin": {"*": _E},
    "Junction": {"uuid": _C, "position": _E, "*": _C},
    "NoConnect": {"uuid": _C, "position": _E, "*": _C},
    "BusEntry": {"uuid": _C, "stroke": _C, "*": _E},
    "Label": {
        "uuid": _C,
        "content": _E,
        "kind": _E,
        "shape": _E,
        "position": _E,
        "*": _C,
    },
    "Sheet": {
        "uuid": _C,
        "pins": _E,
        "fields": _P,
        "instances": _P,
        "dnp": _P,
        "in_bom": _P,
        "on_board": _P,
        "exclude_from_sim": _P,
        "stroke": _C,
        "fill_color": _C,
        "fields_autoplaced": _C,
        "*": _G,
    },
    "Schematic": {
        **_STAMPS,
        "lib_symbols": _P,
        "sheet_instances": _P,
        "title_block": _S,
        "paper": _S,
        "groups": _C,
        "embedded_files": _P,
        "embedded_fonts": _C,
    },
    "Symbol": {
        "name": _P,
        "extends": _P,
        "fields": _P,
        "fp_filters": _P,
        "keywords": _P,
        "in_bom": _P,
        "on_board": _P,
        "in_pos_files": _P,
        "exclude_from_sim": _P,
        "embedded_files": _P,
        "embedded_fonts": _C,
        "power_type": _E,
        "duplicate_pin_numbers_are_jumpers": _E,
        "jumper_pin_groups": _E,
        "units_locked": _P,
        "body_styles": _P,
        "pin_name_offset": _C,
        "show_pin_names": _C,
        "show_pin_numbers": _C,
    },
    "SymbolLib": dict(_STAMPS),
    "Pin": {
        "number": _E,
        "name": _E,
        "electrical_type": _E,
        "position": _E,
        "angle": _E,
        "length": _E,
        "alternates": _E,
        "*": _C,
    },
    "PcbGroup": {"*": _C},
    "SchGroup": {"*": _C},
    "ProjectFile": {"*": _S},
}

# Whole-item additions and removals, by item type. Anything else is geometry.
_ITEM_RULES: dict[str, ChangeCategory] = {
    "Footprint": _P,
    "Pad": _E,
    "TrackSegment": _E,
    "TrackArc": _E,
    "Via": _E,
    "Zone": _E,
    "PlacedSymbol": _P,
    "PlacedSymbolPin": _E,
    "Junction": _E,
    "NoConnect": _E,
    "BusEntry": _E,
    "Label": _E,
    "Sheet": _P,
    "Property": _P,
    "Symbol": _P,
    "Pin": _E,
    "PcbGroup": _C,
    "SchGroup": _C,
    "Net": _E,
}

_COSMETIC = frozenset(
    {
        "attributes",
        "effects",
        "font",
        "justify",
        "hide",
        "visible",
        "color",
        "fill_color",
        "knockout",
        "locked",
        "unlocked",
        "fields_autoplaced",
        "do_not_autoplace",
        "show_name",
        "uuid",
        "id",
    }
)
_GEOMETRY = frozenset(
    {
        "at",
        "position",
        "start",
        "end",
        "mid",
        "center",
        "angle",
        "size",
        "width",
        "pts",
        "polygons",
        "layer",
        "layers",
        "mirror",
        "radius",
        "length",
        "stroke",
        "fill",
        "shape",
        "rect_delta",
        "offset",
        "margins",
        "border_enabled",
        "corner_radius",
        "scale",
    }
)


def scalar(node: _Node | None) -> object:
    """Return the Python value of a scalar or enumeration node, else ``None``."""
    if node is None:
        return None
    if node.kind == "enum":
        return scalar(node.items[0])
    if node.kind == "scalar":
        return json.loads(node.value)
    return None


def field(node: _Node, name: str) -> _Node | None:
    """Return the child *name* of a record or mapping node, else ``None``."""
    return dict(node.fields).get(name)


def _is_wire(item: _Node | None) -> bool:
    return item is not None and scalar(field(item, "line_type")) in ("wire", "bus")


def categorize(item_type: str, item: _Node | None, path: tuple[str, ...]) -> ChangeCategory:
    """Return the category of a change to *path* inside an item of *item_type*.

    *path* is the field path inside the item. An empty path is the whole item.
    """
    if not path:
        if item_type == "Line":
            return _E if _is_wire(item) else _G
        return _ITEM_RULES.get(item_type, _G)
    if "render_cache" in path:
        return _D
    if item_type == "ProjectFile" and "net_settings" in path:
        return _E
    if item_type == "Line":
        if path[0] in ("start", "end", "line_type"):
            return _E if _is_wire(item) else _G
        return _C if path[0] in ("stroke", "uuid") else _G
    rules = _RULES.get(item_type)
    if rules is not None:
        if path[0] in rules:
            return rules[path[0]]
        if "*" in rules:
            return rules["*"]
    if path[0] in _COSMETIC or path[-1] in _COSMETIC:
        return _C
    if path[0] in _GEOMETRY:
        return _G
    return "other"


_ITEM_TYPES = frozenset(_ITEM_RULES) | {"Line"}


def is_item(node: _Node) -> bool:
    """True when *node* is an item a change record names on its own.

    A list element of another type, such as one polygon of a zone fill, stays
    a field of the item that holds it, so it keeps that item's category.
    """
    if node.kind != "record":
        return False
    if node.value in _ITEM_TYPES:
        return True
    from eedesigncore.comparison import _identity

    return _identity(node) is not None


# The field that names an item with no stored identifier, unique among its siblings.
_SECONDARY_KEYS = {
    "Pad": "number",
    "Property": "name",
    "PlacedSymbolPin": "number",
    "Pin": "number",
}


def secondary_key(node: _Node) -> tuple[str, str, str] | None:
    """Return ``(type, field, value)`` that names an item with no stored identifier.

    A footprint from a library stores no pad identifiers, so its pads match by
    number. A caller matches by this key only when it is unique on both sides.
    """
    name = _SECONDARY_KEYS.get(node.value) if node.kind == "record" else None
    child = None if name is None else field(node, name)
    if name is None or child is None or child.kind != "scalar" or child.value in ("null", '""'):
        return None
    return node.value, name, child.value


def _property_value(node: _Node, name: str) -> str | None:
    for entry in getattr(field(node, "properties") or field(node, "fields"), "items", ()):
        if entry.kind == "record" and scalar(field(entry, "name")) == name:
            value = scalar(field(entry, "value"))
            return value if isinstance(value, str) and value else None
    return None


def _point(node: _Node | None) -> str:
    if node is None or node.kind not in ("tuple", "list"):
        return "?"
    return "(" + ", ".join(str(scalar(item)) for item in node.items) + ")"


def item_name(item_type: str, node: _Node, parent: str) -> str:  # noqa: C901, PLR0911, PLR0912
    """Return the name a person reads for one item inside *parent*."""
    if item_type == "Footprint":
        return _property_value(node, "Reference") or str(
            scalar(field(node, "fp_id")) or "footprint"
        )
    if item_type == "PlacedSymbol":
        return _property_value(node, "Reference") or str(scalar(field(node, "lib_id")) or "symbol")
    if item_type == "Pad":
        return f"{parent} pad {scalar(field(node, 'number'))}"
    if item_type == "Property":
        return f"{parent} field {scalar(field(node, 'name'))}"
    if item_type in ("TrackSegment", "TrackArc"):
        return f"track on {scalar(field(node, 'net')) or 'no net'}"
    if item_type == "Via":
        return f"via on {scalar(field(node, 'net')) or 'no net'}"
    if item_type == "Zone":
        net = scalar(field(node, "net_name")) or scalar(field(node, "net"))
        return f"zone on {net or 'no net'}"
    if item_type == "Label":
        return f"label {scalar(field(node, 'content'))}"
    if item_type == "Line":
        kind = scalar(field(node, "line_type")) or "line"
        return f"{kind} {_point(field(node, 'start'))} to {_point(field(node, 'end'))}"
    if item_type in ("Junction", "NoConnect"):
        noun = "junction" if item_type == "Junction" else "no-connect"
        return f"{noun} at {_point(field(node, 'position'))}"
    if item_type == "Sheet":
        return f"sheet {_property_value(node, 'Sheetname') or '?'}"
    if item_type == "Symbol":
        return f"symbol {scalar(field(node, 'name'))}"
    if item_type == "Pin":
        return f"{parent} pin {scalar(field(node, 'number'))}"
    if item_type == "PlacedSymbolPin":
        return f"{parent} pin {scalar(field(node, 'number'))}"
    return f"{parent} {item_type}" if parent else item_type


def root_name(item_type: str, node: _Node) -> str:
    """Return the name of the document itself."""
    if item_type == "Footprint":
        return str(scalar(field(node, "fp_id")) or "footprint")
    if item_type == "Symbol":
        return f"symbol {scalar(field(node, 'name'))}"
    return {
        "Board": "board",
        "Schematic": "schematic",
        "SymbolLib": "symbol library",
        "ProjectFile": "project",
    }.get(item_type, item_type)


__all__ = [
    "categorize",
    "field",
    "is_item",
    "item_name",
    "root_name",
    "scalar",
    "secondary_key",
]
