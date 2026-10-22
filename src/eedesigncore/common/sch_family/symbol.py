"""SCH-family ``Symbol`` sub-grammar (the ``SaveSymbol`` payload).

``Symbol``/``SymbolUnit`` are the embeddable symbol definition shared by
``.kicad_sym`` and (from iteration 10) ``.kicad_sch``. The format-root wrapper
``SymbolLib`` stays in ``sym/models.py``; only this embeddable sub-grammar lives
in ``common/`` (arch-plan section 2c).
"""

from __future__ import annotations

import re
from typing import Any, Self

from eedesigncore.common.base import KicadModel
from eedesigncore.common.escape import unescape_string
from eedesigncore.common.models import EmbeddedFile, Property, TextAttributes
from eedesigncore.common.sch_family.enums import PowerType
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.text import SymText, SymTextBox

# Union of all items that can appear inside a unit sub-symbol
SymbolItem = SchArc | SchCircle | SchRect | SchBezier | SchPolyline | Pin | SymText | SymTextBox


# The separator set of ``wxStringTokenizer( value, <space tab CR LF>, wxTOKEN_STRTOK )``
# at ``sch_io_kicad_sexpr_parser.cpp:1186``. ``wxTOKEN_STRTOK`` drops empty tokens.
_FP_FILTER_SEPARATORS = re.compile("[ \t\r\n]+")

_FP_FILTER_SPACE_ESCAPE = "{space}"


def split_fp_filters(value: str) -> list[str]:
    """Split a ``ki_fp_filters`` property value into its filter list.

    This function promises a BYTE-PRESERVING split, NOT KiCad's in-memory value.
    It tokenizes on runs of space, tab, CR and LF, mirroring
    ``sch_io_kicad_sexpr_parser.cpp:1186``, and then reverses ``{space}`` and
    nothing else. Feeding the result back through :func:`join_fp_filters`
    reproduces the source bytes, which is what the round-trip guarantee needs.

    KiCad does more: it passes each token through the general
    ``UnescapeString`` (``sch_io_kicad_sexpr_parser.cpp:1190``), which also
    resolves ``{tab}``, ``{slash}``, ``{colon}``, ``{brace}`` and the rest
    (``common/string_utils.cpp:334-385``). A stored value can carry any of those
    tokens, because the symbol properties dialog stores typed filter text with
    no escaping in either direction
    (``dialog_lib_symbol_properties.cpp:348-349,704``). This function leaves
    every such run intact.

    KiCad also does LESS in one case. Its markup guard
    (``common/string_utils.cpp:364-367``) refuses to resolve a ``{space}``
    preceded by ``$``, ``~``, ``^`` or ``_``, so KiCad reads ``A_{space}B`` as
    ``A_{space}B`` while this function reads it as ``A_ B``.

    Use :func:`semantic_fp_filters` when you need the value KiCad holds in
    memory, and :func:`unescape_string` for a single token. Neither is
    byte-preserving. See ADR-074 and ADR-083, and
    ``context/audit/escape-string-module-2026-08-18.md`` sections 5, 8.3 and 9.
    """
    return [
        token.replace(_FP_FILTER_SPACE_ESCAPE, " ")
        for token in _FP_FILTER_SEPARATORS.split(value)
        if token
    ]


def join_fp_filters(filters: list[str]) -> str:
    """Render a filter list as a ``ki_fp_filters`` property value.

    Port of ``sch_io_kicad_sexpr_lib_cache.cpp:570-589``: escape each filter with
    ``CTX_NO_SPACE``, which maps a space to ``{space}``, then join the results
    with one space. A single-element list emits with no separator.

    This is an exact port of the write side, and it is the inverse of
    :func:`split_fp_filters`. It is NOT the inverse of
    :func:`semantic_fp_filters`, because KiCad's own escape pair is lossy
    (``context/audit/escape-string-module-2026-08-18.md`` section 5).
    """
    return " ".join(item.replace(" ", _FP_FILTER_SPACE_ESCAPE) for item in filters)


def semantic_fp_filters(value: str) -> list[str]:
    """Split a ``ki_fp_filters`` value the way KiCad splits it, in memory.

    This is the complete read path of ``sch_io_kicad_sexpr_parser.cpp:1182-1196``:
    tokenize on runs of space, tab, CR and LF, drop empty tokens, then pass each
    token through the general ``UnescapeString``
    (:func:`unescape_string`). The result is the list KiCad holds after a load,
    which is what its footprint-filter matching compares against
    (``erc.cpp:1881-1889``).

    It is NOT byte-preserving, and it is NOT the inverse of
    :func:`join_fp_filters`. The two functions differ for any value carrying an
    escape token other than a plain ``{space}``, and they differ in the other
    direction for a ``{space}`` preceded by ``$``, ``~``, ``^`` or ``_``. Load
    with :func:`split_fp_filters` and write with :func:`join_fp_filters` when
    the file bytes matter. See ADR-083.

    >>> semantic_fp_filters("A_{space}B DIP{space}8")
    ['A_{space}B', 'DIP 8']
    """
    return [unescape_string(token) for token in _FP_FILTER_SEPARATORS.split(value) if token]


class SymbolUnit(KicadModel):
    """A unit sub-symbol (child ``symbol`` token inside a root symbol).

    ``name`` is the FULL ENCODED name, ``<item name>_<unit>_<body style>``, for
    example ``R_1_1``. The writer emits ``name`` verbatim and ignores
    ``unit_index`` and ``body_style_index``, which the loader derives from the
    name suffix. Setting the two index fields alone therefore changes nothing on
    disk (GAP-017).

    Build the name with :meth:`encode_name`, or build the whole unit with
    :meth:`for_symbol`, which keeps the name and the two indices consistent.
    KiCad composes the same string at
    ``sch_io_kicad_sexpr_lib_cache.cpp:490-498``: it quotes the LIB_ID item name,
    drops the closing quote, then appends ``_<unit>_<body style>"``.
    """

    name: str  # full encoded name, e.g. "R_1_1"
    unit_index: int = 1
    body_style_index: int = 1
    unit_display_name: str | None = None
    items: list[Any] = []  # list[SymbolItem] — Any avoids forward-ref issues

    @staticmethod
    def encode_name(symbol_name: str, unit_index: int, body_style_index: int) -> str:
        """Return the encoded unit name for *symbol_name* and the two indices.

        ``encode_name("R", 1, 1)`` returns ``"R_1_1"``. A library nickname is
        dropped first, because KiCad encodes the LIB_ID ITEM name only
        (``unitId.GetLibItemName()``, sch_io_kicad_sexpr_lib_cache.cpp:377). So
        ``encode_name("Device:R", 2, 1)`` returns ``"R_2_1"``.
        """
        item_name = symbol_name.split(":")[-1]
        return f"{item_name}_{unit_index}_{body_style_index}"

    @classmethod
    def for_symbol(
        cls,
        symbol_name: str,
        *,
        unit_index: int = 1,
        body_style_index: int = 1,
        unit_display_name: str | None = None,
        items: list[Any] | None = None,
    ) -> Self:
        """Build a unit whose encoded name matches the indices (GAP-017).

        This is the constructor to reach for. A hand-typed ``name`` that
        disagrees with ``unit_index`` or ``body_style_index`` writes a
        mislabelled unit, and nothing else in the model catches it.
        """
        return cls(
            name=cls.encode_name(symbol_name, unit_index, body_style_index),
            unit_index=unit_index,
            body_style_index=body_style_index,
            unit_display_name=unit_display_name,
            items=[] if items is None else items,
        )


class Symbol(KicadModel):
    """A top-level symbol definition in a ``.kicad_sym`` file."""

    name: str

    # Root-only fields (None/absent for derived symbols)
    extends: str | None = None  # set → derived symbol

    # Root symbol properties
    power_type: PowerType | None = None  # None = not a power symbol (GAP-021)
    body_styles: list[str] | None = None
    show_pin_numbers: bool = True
    pin_name_offset: float | None = None
    show_pin_names: bool = True
    exclude_from_sim: bool = False
    in_bom: bool = True
    on_board: bool = True
    in_pos_files: bool = True
    duplicate_pin_numbers_are_jumpers: bool = False
    jumper_pin_groups: list[list[str]] | None = None

    # DCM data. KiCad keeps these on LIB_SYMBOL members, not in the field list.
    # The parser consumes the property and returns nullptr
    # (``sch_io_kicad_sexpr_parser.cpp:1170-1203``); the writer builds a throwaway
    # SCH_FIELD from the member on every save
    # (``sch_io_kicad_sexpr_lib_cache.cpp:470-474,562-591``). So ``ki_locked``,
    # ``ki_keywords`` and ``ki_fp_filters`` never appear in ``fields`` (ADR-073).
    units_locked: bool = False  # LIB_SYMBOL::m_unitsLocked
    keywords: str | None = None  # LIB_SYMBOL::m_keyWords
    fp_filters: list[str] = []  # LIB_SYMBOL::m_fpFilters, a wxArrayString

    # Fields (properties) — mandatory + user-defined
    fields: list[Property] = []

    # Unit sub-symbols (root only; derived have none)
    units: list[SymbolUnit] = []

    embedded_fonts: bool = False
    embedded_files: list[EmbeddedFile] = []

    def renamed(self, new_name: str) -> Self:
        """Return a copy named *new_name*, with every unit name re-encoded.

        **Renaming a symbol by hand writes a file KiCad refuses to open.** A
        unit's name is stored data, and KiCad's parser requires it to start with
        the parent's item name and to end in exactly two ``_``-separated numbers
        (``sch_io_kicad_sexpr_parser.cpp:485-530``). Set ``Symbol.name`` alone
        and the units keep the OLD prefix, which fails the check at
        ``:501-505`` — ``Invalid symbol unit name prefix`` — and KiCad then
        rejects the whole schematic, not just that symbol.

        KiCad itself cannot hit this. A unit has no stored name at runtime;
        ``LIB_SYMBOL`` composes ``<item name>_<unit>_<body style>`` on every
        save (``sch_io_kicad_sexpr_lib_cache.cpp:490-498``), so its rename is
        always consistent. This model keeps the name as data, so the rename has
        to propagate it, and every caller that renames a symbol must come
        through here.

        The nickname is dropped from the unit names, because KiCad encodes the
        LIB_ID item name only. So ``renamed("partsdb:PN-00457")`` gives a parent
        ``partsdb:PN-00457`` and units ``PN-00457_1_1``, which is the shape the
        corpus shows KiCad writing.

        A unit name is re-encoded from ``unit_index`` and ``body_style_index``,
        which the loader derives from the name it read, so a round trip through
        this method is stable. A hand-built unit whose name disagrees with its
        indices is normalised to agree with them.
        """
        return self.model_copy(
            update={
                "name": new_name,
                "units": [
                    unit.model_copy(
                        update={
                            "name": SymbolUnit.encode_name(
                                new_name, unit.unit_index, unit.body_style_index
                            )
                        }
                    )
                    for unit in self.units
                ],
            }
        )

    @classmethod
    def new(
        cls,
        name: str,
        *,
        reference: str = "U",
        value: str | None = None,
        footprint: str = "",
        datasheet: str = "",
        description: str = "",
    ) -> Self:
        """Build a symbol that already carries the five mandatory fields.

        A bare ``Symbol(name=…)`` holds an empty ``fields`` list, so it writes no
        Reference and no Value (GAP-020). This factory seeds the mandatory set the
        ``LIB_SYMBOL`` constructor creates, in the same order and with the same
        visibility (``eeschema/lib_symbol.cpp:247-251``):

        ============ ======= =========================================
        Field        Visible Default value
        ============ ======= =========================================
        Reference    yes     *reference*, ``U`` by default
        Value        yes     *value*, the symbol *name* by default
        Footprint    no      *footprint*, empty by default
        Datasheet    no      *datasheet*, empty by default
        Description  no      *description*, empty by default
        ============ ======= =========================================

        The names are the canonical strings KiCad writes and refuses to change
        (``common/template_fieldnames.cpp:33-37``). Every one of the 10521 root
        symbols in the stock KiCad 10.0 libraries carries these five fields in
        this order, and 10515 of them set Value to the symbol name.

        Set every other field on the returned model. This factory ships no
        validator that rejects a symbol lacking the fields, because KiCad
        acceptance of such a symbol stays UNVERIFIED (plan iteration 11, GAP-020).
        """
        return cls(
            name=name,
            fields=[
                Property(
                    name="Reference",
                    value=reference,
                    visible=True,
                    attributes=TextAttributes(),
                ),
                Property(
                    name="Value",
                    value=name if value is None else value,
                    visible=True,
                    attributes=TextAttributes(),
                ),
                Property(
                    name="Footprint", value=footprint, visible=False, attributes=TextAttributes()
                ),
                Property(
                    name="Datasheet", value=datasheet, visible=False, attributes=TextAttributes()
                ),
                Property(
                    name="Description",
                    value=description,
                    visible=False,
                    attributes=TextAttributes(),
                ),
            ],
        )
