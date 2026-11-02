"""Symbol library loader: S-expression tree → ``SymbolLib`` domain model."""

from __future__ import annotations

from eedesigncore.common.loaders import bool_token as _bool_token
from eedesigncore.common.loaders import children as _children
from eedesigncore.common.loaders import elements as _elements
from eedesigncore.common.loaders import find_child as _find_child
from eedesigncore.common.loaders import get_float as _get_float
from eedesigncore.common.loaders import get_int as _get_int
from eedesigncore.common.loaders import get_str as _get_str
from eedesigncore.common.loaders import load_effects as _load_effects
from eedesigncore.common.loaders import load_embedded_files as _load_embedded_files
from eedesigncore.common.loaders import require_sym as _require_sym
from eedesigncore.common.loaders import sym_name as _sym_name
from eedesigncore.common.models import (
    EmbeddedFile,
    Property,
)
from eedesigncore.common.sch_family.enums import PowerType
from eedesigncore.common.sch_family.loaders import _load_unit
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit, split_fp_filters
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType
from eedesigncore.sym.models import SymbolLib

# ---------------------------------------------------------------------------
# Property / Field
# ---------------------------------------------------------------------------


def _load_property(node: SExprNode) -> Property:
    """Parse a ``property`` token into a ``Property`` model."""
    _require_sym(node, "property")
    ch_all = _children(node)

    idx = 0
    private = False

    # Optional 'private' keyword
    if ch_all and ch_all[0].node_type == SExprNodeType.SYMBOL and ch_all[0].value == "private":
        private = True
        idx += 1

    if idx + 1 >= len(ch_all):
        raise KicadParseError("property missing name/value")

    name = _get_str(ch_all[idx])
    value = _get_str(ch_all[idx + 1])
    idx += 2

    position = (0.0, 0.0)
    angle = 0.0
    show_name = False
    do_not_autoplace = False
    visible = True
    attributes = None

    # Remaining children are sub-lists
    for child in ch_all[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "at":
            atch = _children(child)
            position = (_get_float(atch[0]), _get_float(atch[1]))
            if len(atch) > 2:
                angle = _get_float(atch[2])
        elif cname == "show_name":
            show_name = _bool_token(child)
        elif cname == "do_not_autoplace":
            do_not_autoplace = _bool_token(child)
        elif cname == "hide":
            visible = not _bool_token(child)
        elif cname == "effects":
            attributes = _load_effects(child)
        elif cname == "id":
            # Legacy token, read and thrown away. Both property parsers do it:
            # the library one at ``sch_io_kicad_sexpr_parser.cpp:1121-1124`` and
            # the schematic one at ``:2389-2392``, each `parseInt( "field ID" )`
            # then `NeedRIGHT()`. Never written back.
            id_ch = _children(child)
            if not id_ch:
                raise KicadParseError("property id missing its field ID")
            _get_int(id_ch[0])
        else:
            raise KicadParseError(f"Unknown token in property: {cname!r}")

    # KiCad 9 writes a field's ``(hide yes)`` INSIDE ``(effects …)``
    # (``SCH_FIELD::Format``, 9.0.9 ``eeschema/sch_field.cpp:517-586``); KiCad 10
    # moved it to the property level. Both spellings mean the same field flag,
    # so an effects-level hide lands on the property here. The writer alone
    # decides where the token goes back (``legacy`` layout).
    if attributes is not None and not attributes.visible:
        visible = False

    return Property(
        private=private,
        name=name,
        value=value,
        position=position,
        angle=angle,
        show_name=show_name,
        do_not_autoplace=do_not_autoplace,
        visible=visible,
        attributes=attributes,
    )


_DESCRIPTION_FIELD = "Description"
_DESCRIPTION_KEY = _DESCRIPTION_FIELD.casefold()

# Both helpers below take and return the position of the Description property
# inside *fields*, or None while the symbol has none. Carrying the position makes
# each replace O(1); searching for it made a symbol with many properties load in
# time quadratic in the property count (MEDIUM-6 of the 11.8 security review).
# The position never goes stale: a field is only ever appended or replaced in
# place, so no earlier entry can shift.


def _upsert_description(fields: list[Property], value: str, description_index: int | None) -> int:
    """Apply a legacy ``ki_description`` value to the mandatory Description field.

    ``LIB_SYMBOL::SetDescription`` calls ``SetText`` on the mandatory field
    (``lib_symbol.cpp:122-127``, ``lib_symbol.h:349``), so it replaces the TEXT
    only and leaves position, angle, visibility and effects alone. When the
    symbol carries no Description property yet, one is created with the
    ``LIB_SYMBOL`` constructor defaults: ``(at 0 0 0)``, hidden, ``(show_name
    no)``, ``(do_not_autoplace no)`` and the default font
    (``lib_symbol.cpp:239-251``). Audit 15.1.2 and 15.1.5.
    """
    if description_index is not None:
        field = fields[description_index]
        fields[description_index] = field.model_copy(update={"value": value})
        return description_index

    fields.append(Property(name=_DESCRIPTION_FIELD, value=value, visible=False))
    return len(fields) - 1


def _append_field(
    fields: list[Property], prop: Property, description_index: int | None
) -> int | None:
    """Append *prop*, or replace an existing Description property wholesale.

    A mandatory field name reaches ``*existingField = *field;``
    (``sch_io_kicad_sexpr_parser.cpp:1163-1168``), which overwrites the whole
    field. Only Description can collide here in practice, because a legacy
    ``ki_description`` node may already have created one. The name match is
    case-insensitive, matching the ``CmpNoCase`` lookup at
    ``sch_io_kicad_sexpr_parser.cpp:1082-1089``.
    """
    if prop.name.casefold() == _DESCRIPTION_KEY:
        if description_index is not None:
            fields[description_index] = prop
            return description_index
        fields.append(prop)
        return len(fields) - 1

    fields.append(prop)
    return description_index


# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------


def _load_symbol(node: SExprNode) -> Symbol:
    """Parse a top-level ``symbol`` token into a ``Symbol`` model."""
    _require_sym(node, "symbol")
    ch = _children(node)

    if not ch:
        raise KicadParseError("symbol missing name")

    name = _get_str(ch[0])
    idx = 1

    # Check if derived (extends)
    extends = None
    if (
        idx < len(ch)
        and ch[idx].node_type == SExprNodeType.LIST
        and _sym_name(ch[idx]) == "extends"
    ):
        extends = _get_str(_children(ch[idx])[0])
        idx += 1

    power_type = None
    body_styles = None
    show_pin_numbers = True
    pin_name_offset = None
    show_pin_names = True
    exclude_from_sim = False
    in_bom = True
    on_board = True
    in_pos_files = True
    duplicate_pin_numbers_are_jumpers = False
    jumper_pin_groups = None
    units_locked = False
    keywords: str | None = None
    fp_filters: list[str] = []
    fields: list[Property] = []
    description_index: int | None = None
    units: list[SymbolUnit] = []
    embedded_fonts = False
    embedded_files: list[EmbeddedFile] = []

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)

        if cname == "power":
            power_ch = _children(child)
            token = str(power_ch[0].value) if power_ch else "global"
            try:
                power_type = PowerType(token)
            except ValueError as err:
                raise KicadParseError(
                    f"Unknown power type: {token!r}. Valid values: global, local."
                ) from err

        elif cname == "body_styles":
            bsch = _children(child)
            if bsch and bsch[0].node_type == SExprNodeType.SYMBOL and bsch[0].value == "demorgan":
                body_styles = ["demorgan"]
            else:
                body_styles = [_get_str(n) for n in bsch]

        elif cname == "pin_numbers":
            # (pin_numbers (hide yes)) → show_pin_numbers = False
            hide_node = _find_child(child, "hide")
            if hide_node is not None:
                show_pin_numbers = not _bool_token(hide_node)

        elif cname == "pin_names":
            offset_node = _find_child(child, "offset")
            if offset_node is not None:
                pin_name_offset = _get_float(_children(offset_node)[0])
            hide_node = _find_child(child, "hide")
            if hide_node is not None:
                show_pin_names = not _bool_token(hide_node)

        elif cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "in_bom":
            in_bom = _bool_token(child)
        elif cname == "on_board":
            on_board = _bool_token(child)
        elif cname == "in_pos_files":
            in_pos_files = _bool_token(child)
        elif cname == "duplicate_pin_numbers_are_jumpers":
            duplicate_pin_numbers_are_jumpers = _bool_token(child)

        elif cname == "jumper_pin_groups":
            # A group is a bare list of pin numbers, ``("1" "3")``. It carries
            # no keyword, so every element is data. Reading it with the
            # keyword-skipping helper dropped the first pin of every group.
            groups = []
            for grp in _children(child):
                if grp.node_type == SExprNodeType.LIST:
                    groups.append([_get_str(p) for p in _elements(grp)])
            jumper_pin_groups = groups

        elif cname == "property":
            prop = _load_property(child)
            # The four consumed names, matched case-sensitively as the C++
            # `name == "ki_…"` branches are
            # (``sch_io_kicad_sexpr_parser.cpp:1170-1203``). Each returns nullptr
            # there, so none of them can ever become a field. A repeated name
            # wins last, because the token loop runs in file order (``:458-460``).
            if prop.name == "ki_locked":
                units_locked = True  # the value is never read (``:1197-1203``)
            elif prop.name == "ki_keywords":
                keywords = prop.value
            elif prop.name == "ki_fp_filters":
                fp_filters = split_fp_filters(prop.value)
            elif prop.name == "ki_description":
                description_index = _upsert_description(fields, prop.value, description_index)
            else:
                description_index = _append_field(fields, prop, description_index)

        elif cname == "symbol":
            # Unit sub-symbol
            units.append(_load_unit(child))

        elif cname == "embedded_fonts":
            embedded_fonts = _bool_token(child)

        elif cname == "embedded_files":
            embedded_files = _load_embedded_files(child)
        else:
            raise KicadParseError(f"Unknown token in symbol: {cname!r}")

    return Symbol(
        name=name,
        extends=extends,
        power_type=power_type,
        body_styles=body_styles,
        show_pin_numbers=show_pin_numbers,
        pin_name_offset=pin_name_offset,
        show_pin_names=show_pin_names,
        exclude_from_sim=exclude_from_sim,
        in_bom=in_bom,
        on_board=on_board,
        in_pos_files=in_pos_files,
        duplicate_pin_numbers_are_jumpers=duplicate_pin_numbers_are_jumpers,
        jumper_pin_groups=jumper_pin_groups,
        units_locked=units_locked,
        keywords=keywords,
        fp_filters=fp_filters,
        fields=fields,
        units=units,
        embedded_fonts=embedded_fonts,
        embedded_files=embedded_files,
    )


# ---------------------------------------------------------------------------
# Library root
# ---------------------------------------------------------------------------


def load_sym(tree: SExprNode) -> SymbolLib:
    """Load a ``SymbolLib`` from a parsed S-expression tree.

    Args:
        tree: The root ``SExprNode`` as returned by ``parse()``.

    Returns:
        A fully populated ``SymbolLib`` instance.

    Raises:
        KicadParseError: if the tree is not a valid symbol library.
    """
    _require_sym(tree, "kicad_symbol_lib")

    version = 0
    generator = ""
    generator_version = ""
    symbols: list[Symbol] = []

    for child in _children(tree):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)

        if cname == "version":
            version = _get_int(_children(child)[0])
        elif cname == "generator":
            generator = _get_str(_children(child)[0])
        elif cname == "generator_version":
            generator_version = _get_str(_children(child)[0])
        elif cname == "symbol":
            symbols.append(_load_symbol(child))
        else:
            # KiCad's root parser only accepts symbol/generator/generator_version
            # (embedded fonts/files are per-symbol, GAP-027); reject anything else.
            raise KicadParseError(f"Unknown token in kicad_symbol_lib: {cname!r}")

    return SymbolLib(
        version=version,
        generator=generator,
        generator_version=generator_version,
        symbols=symbols,
    )
