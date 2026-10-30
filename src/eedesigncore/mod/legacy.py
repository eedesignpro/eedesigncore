"""Read a footprint written by KiCad 5 to KiCad 9 the way KiCad 10 reads it.

KiCad 10 keeps one footprint parser for every file version and gates the old
syntax on the version number in the file. This module rewrites an old parse
tree into the KiCad 10 token layout before the footprint loader reads it, so
the loader itself stays a reader of the current format.

Every rule cites KiCad 10.0.7 ``pcbnew/pcb_io/kicad_sexpr/
pcb_io_kicad_sexpr_parser.cpp`` (``parser``) unless it names another file.
Version numbers are ``SEXPR_BOARD_FILE_VERSION`` dates
(``pcb_io_kicad_sexpr.h:71-206``).
"""

from __future__ import annotations

from eedesigncore.common.arcs import arc_mid_iu
from eedesigncore.common.kimath import kiround, normalize_angle, rotate_point_about_iu
from eedesigncore.common.layers import (
    CANONICAL_LAYER_NAMES,
    LAYER_NAME_TO_ORDINAL,
    LAYER_WILDCARD_ORDINALS,
)
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType

#: The format version this library reads and writes.
CURRENT_VERSION = 20260206

#: A file without a version token predates 20201116 (``parser`` m_requiredVersion = 0).
_NO_VERSION = 0

_IU_PER_MM = 1_000_000

# ``LEGACY_ARC_FORMATTING`` and ``LEGACY_NET_TIES`` (pcb_io_kicad_sexpr.h:205-206).
_LEGACY_ARC_FORMATTING = 20210925
_LEGACY_NET_TIES = 20220815

# ``DEFAULT_LINE_WIDTH`` (include/board_design_settings.h:42), in mm.
_DEFAULT_LINE_WIDTH = 0.1

# Prior to KiCad 5 a missing text size meant 60 mils (``parseEDA_TEXT``).
_LEGACY_TEXT_SIZE = 1.524

# ``25.4f``: the 3D model offset multiplier is a float literal (``parse3DModel``).
_INCH_TO_MM_FLOAT = 25.399999618530273

_SHAPES = ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve")
_MARGINS = ("solder_mask_margin", "solder_paste_margin", "solder_paste_margin_ratio", "clearance")
_ATTRIBUTE_FLAGS = (
    "smd",
    "through_hole",
    "board_only",
    "exclude_from_pos_files",
    "exclude_from_bom",
    "dnp",
)


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------


def _sym(name: str) -> SExprNode:
    return SExprNode.symbol(name)


def _str(text: str) -> SExprNode:
    return SExprNode.string(text)


def _num(value: float) -> SExprNode:
    if float(value).is_integer():
        return SExprNode.integer(int(value))
    return SExprNode.double(float(value))


def _list(name: str, *items: SExprNode) -> SExprNode:
    return SExprNode.make_list([_sym(name), *items])


def _items(node: SExprNode) -> list[SExprNode]:
    assert isinstance(node.value, list)
    return node.value


def _name(node: SExprNode) -> str | None:
    if node.node_type != SExprNodeType.LIST:
        return None
    items = _items(node)
    if not items or items[0].node_type != SExprNodeType.SYMBOL:
        return None
    return str(items[0].value)


def _is_atom(node: SExprNode, text: str) -> bool:
    return node.node_type == SExprNodeType.SYMBOL and node.value == text


def _text(node: SExprNode) -> str:
    """The text of an atom as the KiCad lexer saw it (``FromUTF8``)."""
    if node.node_type in (SExprNodeType.INTEGER, SExprNodeType.DOUBLE) and node.raw is not None:
        return node.raw
    return str(node.value)


def _as_string(node: SExprNode) -> SExprNode:
    """Turn an unquoted atom that KiCad reads as text into a STRING node."""
    if node.node_type == SExprNodeType.STRING:
        return node
    return _str(_text(node))


def _float(node: SExprNode) -> float:
    return float(_text(node))


def _first_child(node: SExprNode, name: str) -> SExprNode | None:
    for item in _items(node)[1:]:
        if _name(item) == name:
            return item
    return None


def _yes(name: str) -> SExprNode:
    return _list(name, _sym("yes"))


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def convert_overbar(text: str) -> str:
    """``ConvertToNewOverbarNotation`` (common/string_utils.cpp:88-155).

    Version 20210606 changed the overbar notation from ``~...~`` to ``~{...}``.
    A doubled ``~~`` is a literal tilde; a space, ``}`` or ``)`` also ends an
    overbar. A string that already holds ``~{`` is returned unchanged.
    """
    if text == "~":
        return text
    out: list[str] = []
    in_overbar = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "~":
            following = text[index + 1] if index + 1 < len(text) else ""
            if following == "~":
                if index + 2 < len(text) and text[index + 2] == "{":
                    out.append("~~{}")
                    index += 1
                    continue
                out.append("~")
                index += 2
                continue
            if following == "{":
                return text
            out.append("}" if in_overbar else "~{")
            in_overbar = not in_overbar
            index += 1
            continue
        if char in " })" and in_overbar:
            out.append("}")
            in_overbar = False
        out.append(char)
        index += 1
    if in_overbar:
        out.append("}")
    return "".join(out)


def _text_value(node: SExprNode, version: int) -> SExprNode:
    """``parsePCB_TEXT``: %V and %R become text variables; old overbars convert."""
    value = _text(node).replace("%V", "${VALUE}").replace("%R", "${REFERENCE}")
    if version < 20210606:
        value = convert_overbar(value)
    return _str(value)


def _effects(node: SExprNode) -> tuple[SExprNode, bool]:
    """Return the effects node without a bare ``hide``, and whether it hid the text.

    ``parseEDA_TEXT`` reads ``hide`` (bare or ``(hide yes)``) into the text's
    own visibility, and forces a 60 mil size when the file gave none.
    """
    hidden = False
    kept: list[SExprNode] = [_items(node)[0]]
    found_size = False
    for item in _items(node)[1:]:
        if _is_atom(item, "hide"):
            hidden = True
            continue
        if _name(item) == "hide":
            hidden = len(_items(item)) < 2 or _items(item)[1].value == "yes"
            continue
        if _name(item) == "font":
            found_size = found_size or _first_child(item, "size") is not None
        kept.append(item)
    if not found_size:
        size = _list("size", _num(_LEGACY_TEXT_SIZE), _num(_LEGACY_TEXT_SIZE))
        font = next((item for item in kept if _name(item) == "font"), None)
        if font is None:
            kept.insert(1, _list("font", size))
        else:
            _items(font).insert(1, size)
    return SExprNode.make_list(kept), hidden


def _text_tail(items: list[SExprNode], version: int) -> tuple[list[SExprNode], bool]:
    """Rewrite the tokens after a text's value; return them and the hidden flag.

    ``parsePCB_TEXT_effects``: ``unlocked`` inside ``at`` means keep-upright off,
    a bare ``hide`` hides, ``tstamp`` is the uuid.
    """
    out: list[SExprNode] = []
    hidden = False
    for item in items:
        if _is_atom(item, "hide"):
            hidden = True
            continue
        if _is_atom(item, "locked"):
            out.append(_yes("locked"))
            continue
        name = _name(item)
        if name == "at":
            numbers = [x for x in _items(item)[1:] if not _is_atom(x, "unlocked")]
            unlocked = len(numbers) != len(_items(item)) - 1
            if len(numbers) > 2:
                # PCB_TEXT::Rotate normalises the angle to [0, 360) on every
                # parse (pcbnew/pcb_text.cpp:460-469).
                numbers[2] = _num(normalize_angle(_float(numbers[2])))
            out.append(_list("at", *numbers))
            if unlocked:
                out.append(_yes("unlocked"))
        elif name == "hide":
            hidden = len(_items(item)) < 2 or _items(item)[1].value == "yes"
        elif name == "tstamp":
            out.append(_list("uuid", *_items(item)[1:]))
        elif name == "effects":
            effects, effects_hidden = _effects(item)
            hidden = hidden or effects_hidden
            out.append(effects)
        elif name == "layer":
            out.append(_list("layer", *(_as_string_layer(x) for x in _items(item)[1:])))
        else:
            out.append(item)
    if not any(_name(item) == "effects" for item in out):
        out.append(_default_effects())
    return out, hidden


def _default_effects() -> SExprNode:
    """A text with no effects keeps the EDA_TEXT default: 50 mil, no thickness."""
    return _list("effects", _list("font", _list("size", _num(1.27), _num(1.27))))


def _as_string_layer(node: SExprNode) -> SExprNode:
    if _is_atom(node, "knockout"):
        return node
    return _as_string(node)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def _iu(x: SExprNode, y: SExprNode) -> tuple[int, int]:
    return (kiround(_float(x) * _IU_PER_MM), kiround(_float(y) * _IU_PER_MM))


def _mm_point(name: str, point: tuple[int, int]) -> SExprNode:
    return _list(name, _num(point[0] / _IU_PER_MM), _num(point[1] / _IU_PER_MM))


def _legacy_arc(center: tuple[int, int], start: tuple[int, int], angle: float) -> list[SExprNode]:
    """``SetCenter``, ``SetStart``, then ``SetArcAngleAndEnd( angle, true )``.

    ``common/eda_shape.cpp``: the end is the start turned by minus the angle,
    normalised to (-360, 360); a negative angle swaps the ends. The writer then
    calls ``GetArcMid``, which computes the mid from the stored centre.
    """
    normalized = angle
    while normalized < -360.0:
        normalized += 360.0
    while normalized > 360.0:
        normalized -= 360.0
    end = rotate_point_about_iu(start, center, -normalized)
    if angle < 0:
        start, end = end, start
    mid = arc_mid_iu(start, end, center)
    return [_mm_point("start", start), _mm_point("mid", mid), _mm_point("end", end)]


def _upgrade_shape(node: SExprNode, version: int) -> SExprNode:
    """``parsePCB_SHAPE``: legacy width, fill, arc and status tokens."""
    items = _items(node)
    kind = str(items[0].value)
    out: list[SExprNode] = [items[0]]
    width: float | None = None
    stroke: SExprNode | None = None
    fill: SExprNode | None = None
    layer = ""
    rest = items[1:]
    if rest and _is_atom(rest[0], "locked"):
        out.append(_yes("locked"))
        rest = rest[1:]

    if kind == "fp_arc" and version <= _LEGACY_ARC_FORMATTING:
        start_node = _first_child(node, "start")
        end_node = _first_child(node, "end")
        angle_node = _first_child(node, "angle")
        if start_node is not None and end_node is not None and angle_node is not None:
            center = _iu(*_items(start_node)[1:3])
            start = _iu(*_items(end_node)[1:3])
            out.extend(_legacy_arc(center, start, _float(_items(angle_node)[1])))
            rest = [x for x in rest if _name(x) not in ("start", "end", "angle")]

    for item in rest:
        name = _name(item)
        if _is_atom(item, "locked"):
            out.append(_yes("locked"))
        elif name == "width":
            width = _float(_items(item)[1])
        elif name in ("angle", "status"):
            continue
        elif name == "tstamp":
            out.append(_list("uuid", *_items(item)[1:]))
        elif name == "locked" and len(_items(item)) < 2:
            out.append(_yes("locked"))
        elif name == "stroke":
            stroke = item
            out.append(item)
        elif name == "fill":
            fill = _upgrade_fill(item)
            out.append(fill)
        elif name == "layer":
            layer = _text(_items(item)[1])
            out.append(_list("layer", _as_string(_items(item)[1])))
        else:
            out.append(item)

    filled = fill is not None and _text(_items(fill)[1]) not in ("no", "none")
    if fill is None:
        # Legacy versions had no fill flag: a zero width filled a rect or circle,
        # and a polygon off Edge.Cuts was always filled.
        if (
            (width == 0 or (width is None and stroke is None))
            and kind
            in (
                "fp_rect",
                "fp_circle",
            )
            or kind == "fp_poly"
            and layer != "Edge.Cuts"
        ):
            filled = True
        if kind in ("fp_rect", "fp_circle", "fp_poly"):
            out.append(_list("fill", _sym("yes" if filled else "no")))

    if stroke is None:
        # STROKE_PARAMS stroke( 0, LINE_STYLE::SOLID ); only a filled shape may
        # keep a zero width.
        value = width if width is not None else 0.0
        if value <= 0 and not filled:
            value = _DEFAULT_LINE_WIDTH
        out.append(_list("stroke", _list("width", _num(value)), _list("type", _sym("solid"))))
    return SExprNode.make_list(out)


def _upgrade_fill(node: SExprNode) -> SExprNode:
    """``(fill solid)`` was the first filled form; ``(fill none)`` the first empty one."""
    items = _items(node)
    if len(items) < 2:
        return node
    value = _text(items[1])
    mapped = {"solid": "yes", "none": "no"}.get(value, value)
    return _list("fill", _sym(mapped), *items[2:])


# ---------------------------------------------------------------------------
# Pads, zones, models
# ---------------------------------------------------------------------------


def _is_zero(node: SExprNode) -> bool:
    items = _items(node)
    return len(items) > 1 and _float(items[1]) == 0.0


def _has_copper(layers: SExprNode | None) -> bool:
    if layers is None:
        return False
    return any(_text(x).endswith(".Cu") for x in _items(layers)[1:])


_ALL_COPPER = frozenset(LAYER_WILDCARD_ORDINALS["*.Cu"])
_WILDCARD_PAIRS = ("*.Adhes", "*.Paste", "*.SilkS", "*.Mask", "*.CrtYd", "*.Fab")


def format_layers(names: list[str], *, zone: bool = False) -> list[str]:
    """``PCB_IO_KICAD_SEXPR::formatLayers`` with ``aEnumerateLayers`` false.

    Source: ``pcb_io_kicad_sexpr.cpp:1550-1630``. A file older than KiCad 10
    may list a pad's layers in any order and with ``F&B.Cu``; KiCad reads the
    set and writes it back in layer id order with its wildcards. A footprint
    library has no board, so every copper layer counts as enabled.
    """
    mask: set[int] = set()
    unknown: list[str] = []
    for name in names:
        if name in LAYER_WILDCARD_ORDINALS:
            mask.update(LAYER_WILDCARD_ORDINALS[name])
        elif name in LAYER_NAME_TO_ORDINAL:
            mask.add(LAYER_NAME_TO_ORDINAL[name])
        else:
            unknown.append(name)
    out: list[str] = []
    copper = mask & _ALL_COPPER
    if copper == _ALL_COPPER:
        out.append("*.Cu")
        mask -= _ALL_COPPER
    elif copper == {0, 2}:
        out.append("F&B.Cu" if zone else "*.Cu")
        mask -= {0, 2}
    for token in _WILDCARD_PAIRS:
        pair = set(LAYER_WILDCARD_ORDINALS[token])
        if pair <= mask:
            out.append(token)
            mask -= pair
    out.extend(CANONICAL_LAYER_NAMES[ordinal] for ordinal in sorted(mask))
    return out + unknown


def _upgrade_pad(node: SExprNode, version: int) -> SExprNode:
    """``parsePAD``: tstamp, bare locked, pre-9.0 zero margins, legacy thermal width."""
    items = _items(node)
    out: list[SExprNode] = [items[0]]
    if len(items) > 1:
        out.append(_as_string(items[1]))
    out.extend(items[2:4])
    for item in items[4:]:
        name = _name(item)
        if _is_atom(item, "locked") or name == "locked":
            continue  # pad locking is now a session preference
        if name == "tstamp":
            out.append(_list("uuid", *_items(item)[1:]))
        elif name == "thermal_width":
            out.append(_list("thermal_bridge_width", *_items(item)[1:]))
        elif name in _MARGINS and version <= 20240201 and _is_zero(item):
            continue  # in pre-9.0 files "0" meant inherit
        elif name == "layers":
            names = [_text(x) for x in _items(item)[1:]]
            out.append(_list("layers", *(_str(x) for x in format_layers(names))))
        elif name in ("pinfunction", "pintype"):
            out.append(_list(name, _as_string(_items(item)[1])))
        else:
            out.append(item)

    # PAD::CanHaveNumber (pcbnew/pad.cpp): an NPTH or aperture pad has no number.
    pad_type = _text(items[2]) if len(items) > 2 else ""
    aperture = pad_type in ("smd", "connect") and not _has_copper(_first_child(node, "layers"))
    if len(out) > 1 and (pad_type == "np_thru_hole" or aperture):
        out[1] = _str("")
    return SExprNode.make_list(out)


def _uuid_tokens(node: SExprNode) -> SExprNode:
    """Rename every ``tstamp`` in *node* to ``uuid``."""
    if node.node_type != SExprNodeType.LIST:
        return node
    items = _items(node)
    if _name(node) == "tstamp":
        return _list("uuid", *items[1:])
    return SExprNode.make_list([_uuid_tokens(item) for item in items])


def _upgrade_zone(node: SExprNode) -> SExprNode:
    """``parseZONE`` and ``format( ZONE* )`` (pcb_io_kicad_sexpr.cpp:2866-2875).

    KiCad 10 writes a zone net only for a copper zone with a net, and no longer
    writes the v5 stroked-fill flag ``filled_areas_thickness``.
    """
    node = _uuid_tokens(node)
    items = _items(node)
    rule_area = any(_name(item) == "keepout" for item in items)
    out: list[SExprNode] = [items[0]]
    for item in items[1:]:
        name = _name(item)
        if _is_atom(item, "locked"):
            out.append(_yes("locked"))
        elif name in ("net", "net_name"):
            value = _text(_items(item)[1]) if len(_items(item)) > 1 else ""
            if not rule_area and value not in ("", "0"):
                out.append(item)
        elif name == "filled_areas_thickness":
            continue
        elif name == "fill":
            out.append(_upgrade_zone_fill(item))
        else:
            out.append(item)
    if rule_area and not any(_name(item) == "placement" for item in out):
        # A rule area always writes its placement settings, defaulting to off
        # with an empty sheet name (pcb_io_kicad_sexpr.cpp:2965-2985).
        out.append(_list("placement", _list("enabled", _sym("no")), _list("sheetname", _str(""))))
    return SExprNode.make_list(out)


def _upgrade_zone_fill(node: SExprNode) -> SExprNode:
    """``arc_segments`` is read and ignored; a segment fill becomes a polygon fill."""
    kept: list[SExprNode] = [_items(node)[0]]
    for item in _items(node)[1:]:
        name = _name(item)
        if name == "arc_segments":
            continue
        if name == "mode" and len(_items(item)) > 1 and _text(_items(item)[1]) == "segment":
            continue
        kept.append(item)
    return SExprNode.make_list(kept)


def _round_points(node: SExprNode) -> SExprNode:
    """Round every point coordinate to the 1 nm grid KiCad parses into.

    ``parseBoardUnits`` stores an integer nanometre count, so a file written
    with more decimals reads back rounded (``parser`` parseBoardUnits).
    """
    if node.node_type != SExprNodeType.LIST:
        return node
    items = _items(node)
    if _name(node) in ("xy", "start", "end", "mid", "center") and len(items) == 3:
        return _list(
            str(items[0].value),
            *(_num(kiround(_float(x) * _IU_PER_MM) / _IU_PER_MM) for x in items[1:]),
        )
    return SExprNode.make_list([_round_points(item) for item in items])


def _zone_has_corners(node: SExprNode) -> bool:
    for polygon in _items(node)[1:]:
        if _name(polygon) == "polygon":
            pts = _first_child(polygon, "pts")
            if pts is not None and len(_items(pts)) > 1:
                return True
    return False


def _upgrade_model(node: SExprNode) -> SExprNode:
    """``parse3DModel``: before KiCad 5 the offset was ``at`` in inches."""
    items = _items(node)
    out: list[SExprNode] = [items[0], _as_string(items[1])] if len(items) > 1 else [items[0]]
    for item in items[2:]:
        if _is_atom(item, "hide"):
            out.append(_yes("hide"))
        elif _name(item) == "at":
            xyz = _first_child(item, "xyz")
            values = [_float(x) * _INCH_TO_MM_FLOAT for x in _items(xyz)[1:4]] if xyz else []
            out.append(_list("offset", _list("xyz", *(_num(v) for v in values))))
        else:
            out.append(item)
    return SExprNode.make_list(out)


def _upgrade_group(node: SExprNode) -> SExprNode:
    """``parseGROUP``: ``id`` was the uuid token before 20231231."""
    items = _items(node)
    out: list[SExprNode] = [items[0]]
    for item in items[1:]:
        if _name(item) == "id":
            out.append(_list("uuid", *_items(item)[1:]))
        elif _is_atom(item, "locked"):
            out.append(_yes("locked"))
        else:
            out.append(item)
    return SExprNode.make_list(out)


# ---------------------------------------------------------------------------
# Footprint
# ---------------------------------------------------------------------------


def footprint_version(tree: SExprNode) -> int:
    """The ``(version N)`` of a footprint tree, or 0 when it has none."""
    node = _first_child(tree, "version")
    if node is None or len(_items(node)) < 2:
        return _NO_VERSION
    return int(_text(_items(node)[1]))


def needs_upgrade(tree: SExprNode) -> bool:
    """True when *tree* is a library footprint older than the current format.

    A KiCad 5 file has the root ``module`` and no version. Every ``footprint``
    library file carries a version from 20201116 on, so a ``footprint`` with no
    version is a board footprint, which takes its board's version instead.
    """
    name = _name(tree)
    if name == "module":
        return True
    if name != "footprint" or _first_child(tree, "version") is None:
        return False
    return footprint_version(tree) < CURRENT_VERSION


def _field(name: str, items: list[SExprNode]) -> SExprNode:
    return SExprNode.make_list([_sym("property"), _str(name), *items])


def _legacy_property(
    node: SExprNode, version: int, fab_layer: str
) -> tuple[str, SExprNode | str | None]:
    """Classify a ``property`` per ``parseFOOTPRINT_unchecked`` (parser 5256-5341).

    Returns ``("field", node)``, ``("drop", None)``, or a footprint attribute
    name with its value (``description``, ``sheetfile``, ``sheetname``).
    """
    items = _items(node)
    name = _text(items[1])
    value = _text(items[2]) if len(items) > 2 else ""
    if version < 20230620:
        if name in ("ki_keywords", "ki_locked"):
            return ("drop", None)
        if name == "ki_description":
            return ("description", value)
        if name in ("Sheetfile", "Sheet file"):
            return ("sheetfile", value)
        if name in ("Sheetname", "Sheet name"):
            return ("sheetname", value)
    if name == "Footprint":
        # Until V9 footprints carried a Footprint field; KiCad 10 parses and drops it.
        return ("drop", None)
    if name == "ki_fp_filters":
        # Not a field: parsed into the footprint filters (parser 5304-5313).
        return ("filters", node)
    tail, hidden = _text_tail(items[3:], version)
    if not any(_name(item) == "layer" for item in tail):
        tail.insert(0, _list("layer", _str(fab_layer)))
    # A legacy property had no text effects, and hide is a negative effect.
    if version < 20230620 or hidden:
        tail.append(_yes("hide"))
    return ("field", _field(name, [_str(value), *tail]))


def upgrade_footprint_tree(
    tree: SExprNode, *, version: int | None = None, board: bool = False
) -> SExprNode:
    """Return *tree* rewritten into the KiCad 10 footprint token layout.

    Args:
        tree: A parsed ``footprint`` or legacy ``module`` list.
        version: The format version to apply. A library file passes None and
            the tree's own ``(version)`` is used. A footprint inside a board has
            no version of its own and takes the board's.
        board: True for a footprint inside a board. A legacy ``tstamp`` is then
            the footprint uuid; in a library file KiCad drops it on save.
    """
    if version is None:
        version = footprint_version(tree)
    items = _items(tree)
    layer_node = _first_child(tree, "layer")
    back = layer_node is not None and _text(_items(layer_node)[1]) == "B.Cu"
    fab_layer = "B.Fab" if back else "F.Fab"

    out: list[SExprNode] = [_sym("footprint")]
    if len(items) > 1:
        out.append(_as_string(items[1]))
    fields: list[SExprNode] = []
    field_names: list[str] = []
    attributes: dict[str, str] = {}
    keywords = ""
    pad_numbers: list[str] = []
    attr_node: SExprNode | None = None
    has_net_ties = False

    def add_field(field: SExprNode) -> None:
        field_name = _text(_items(field)[1])
        if field_name in field_names:
            index = field_names.index(field_name)
            fields[index] = field
        else:
            field_names.append(field_name)
            fields.append(field)

    for item in items[2:]:
        name = _name(item)
        if item.node_type != SExprNodeType.LIST:
            if _is_atom(item, "placed"):
                out.append(_yes("placed"))
            elif not (_is_atom(item, "locked") and not board):
                out.append(item)
            continue
        if name == "tedit":
            continue
        if not board and (name == "at" or name == "locked"):
            # A library footprint has no position, and "locking a footprint has
            # no meaning outside of a board" (parser 1068-1074).
            continue
        if name == "tstamp":
            if board:
                out.append(_list("uuid", *_items(item)[1:]))
            continue
        if name == "property":
            kind, payload = _legacy_property(item, version, fab_layer)
            if kind == "filters" and isinstance(payload, SExprNode):
                out.append(payload)
            elif kind == "field" and isinstance(payload, SExprNode):
                add_field(payload)
            elif kind != "drop" and isinstance(payload, str):
                attributes[kind] = payload
            continue
        if name == "fp_text":
            converted = _upgrade_text(item, version, len(fields) + 4 - _mandatory(field_names))
            if _name(converted) == "property":
                add_field(converted)
            else:
                out.append(converted)
            continue
        if name in _SHAPES:
            out.append(_upgrade_shape(item, version))
        elif name == "pad":
            pad = _upgrade_pad(item, version)
            out.append(pad)
            pad_numbers.append(_text(_items(pad)[1]))
        elif name == "zone":
            if _zone_has_corners(item):
                out.append(_upgrade_zone(item))
        elif name == "model":
            out.append(_upgrade_model(item))
        elif name == "group":
            out.append(_upgrade_group(item))
        elif name == "solder_paste_ratio":
            node = _list("solder_paste_margin_ratio", *_items(item)[1:])
            if not (version <= 20240201 and _is_zero(node)):
                out.append(node)
        elif name in _MARGINS:
            if not (version <= 20240201 and _is_zero(item)):
                out.append(item)
        elif name == "attr":
            attr_node = _upgrade_attr(item)
            out.append(attr_node)
        elif name == "private_layers":
            # An LSET: written back in layer id order. Before 20220427 Edge.Cuts
            # and Margin could not be private (parser 5452-5456).
            names = [_text(x) for x in _items(item)[1:]]
            if version < 20220427:
                names = [x for x in names if x not in ("Edge.Cuts", "Margin")]
            names.sort(key=lambda x: LAYER_NAME_TO_ORDINAL.get(x, 1 << 16))
            out.append(_list("private_layers", *(_str(x) for x in names)))
        elif name in ("descr", "tags"):
            value = _as_string(_items(item)[1]) if len(_items(item)) > 1 else _str("")
            if name == "tags":
                keywords = str(value.value)
            out.append(_list(name, value))
        elif name == "net_tie_pad_groups":
            has_net_ties = True
            out.append(item)
        elif name in ("fp_text_box", "dimension", "image", "table", "barcode", "point"):
            out.append(_uuid_tokens(item))
        else:
            out.append(item)

    # The writer orders the fields; their place in the tree does not matter.
    if "description" in attributes:
        add_field(
            _field(
                "Description",
                [
                    _str(attributes["description"]),
                    _list("at", _num(0), _num(0), _num(0)),
                    _list("layer", _str(fab_layer)),
                    _yes("hide"),
                    _default_effects(),
                ],
            )
        )
    out.extend(fields)
    for key in ("sheetname", "sheetfile"):
        if key in attributes:
            out.append(_list(key, _str(attributes[key])))

    # In legacy files no attribute meant a through-hole part (parser 5800-5801).
    if version < 20200826:
        flags = {_text(x) for x in _items(attr_node)[1:]} if attr_node is not None else set()
        if not flags & set(_ATTRIBUTE_FLAGS):
            if attr_node is None:
                out.append(_list("attr", _sym("through_hole")))
            else:
                _items(attr_node).insert(1, _sym("through_hole"))

    # Before first-class net ties the keywords marked one (parser 5803-5819).
    if (
        version <= _LEGACY_NET_TIES
        and keywords.startswith("net tie")
        and not has_net_ties
        and pad_numbers
    ):
        out.append(_list("net_tie_pad_groups", _str(", ".join(pad_numbers))))

    return _round_points(SExprNode.make_list(out))


def _mandatory(field_names: list[str]) -> int:
    return sum(
        1 for name in field_names if name in ("Reference", "Value", "Datasheet", "Description")
    )


def _upgrade_attr(node: SExprNode) -> SExprNode:
    """``virtual`` meant excluded from position files and BOM before 20200826."""
    out: list[SExprNode] = [_items(node)[0]]
    for item in _items(node)[1:]:
        if _is_atom(item, "virtual"):
            out.extend([_sym("exclude_from_pos_files"), _sym("exclude_from_bom")])
        else:
            out.append(item)
    return SExprNode.make_list(out)


def _upgrade_text(node: SExprNode, version: int, field_count: int) -> SExprNode:
    """``parsePCB_TEXT`` and the ``T_fp_text`` case of the footprint parser.

    Reference and value texts become the mandatory fields. A hidden user text
    becomes a user field named ``Field<n>``, n being the field count so far
    (``GetUserFieldName``, common/template_fieldnames.cpp).
    """
    items = _items(node)
    kind = _text(items[1]) if len(items) > 1 else "user"
    rest = items[2:]
    locked = False
    if rest and _is_atom(rest[0], "locked"):
        locked = True
        rest = rest[1:]
    value = _text_value(rest[0], version) if rest else _str("")
    tail, hidden = _text_tail(rest[1:], version)
    if hidden:
        tail.append(_yes("hide"))
    if kind == "reference":
        return _field("Reference", [value, *_drop_locked(tail)])
    if kind == "value":
        return _field("Value", [value, *_drop_locked(tail)])
    if hidden:
        return _field(f"Field{field_count}", [value, *_drop_locked(tail)])
    head = [items[0], _sym("user")]
    if locked:
        tail.insert(0, _yes("locked"))
    return SExprNode.make_list([*head, value, *tail])


def _drop_locked(items: list[SExprNode]) -> list[SExprNode]:
    return [item for item in items if _name(item) != "locked"]


__all__ = [
    "CURRENT_VERSION",
    "convert_overbar",
    "footprint_version",
    "needs_upgrade",
    "upgrade_footprint_tree",
]
