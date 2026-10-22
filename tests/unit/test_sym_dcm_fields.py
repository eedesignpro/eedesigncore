"""Symbol DCM data: ``ki_locked``, ``ki_keywords``, ``ki_fp_filters`` (ADR-073).

KiCad keeps the three on ``LIB_SYMBOL`` members and synthesizes a throwaway
``SCH_FIELD`` for each on every save, after the whole ordinal-sorted property
block (``sch_io_kicad_sexpr_lib_cache.cpp:462-476,562-591``). So the model
carries them as first-class members and the writer emits them last, each behind
its own gate.

Every literal node string below is quoted from audit section 15.2.3 (flat) and
15.2.4 (prettified). None of them is re-derived here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.common.sch_family.symbol import (
    Symbol,
    join_fp_filters,
    semantic_fp_filters,
    split_fp_filters,
)
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sym.models import SymbolLib
from eedesigncore.sym.writer import _dump_symbol, dump_sym

FIXTURES = Path(__file__).parent.parent / "fixtures"
RELAY = FIXTURES / "sym" / "Relay.kicad_sym"
DEVICE = FIXTURES / "sym" / "Device.kicad_sym"
LEGACY = FIXTURES / "sym_legacy" / "Transmission_Line.kicad_sym"

MANDATORY_FIELD_NAMES = ["Reference", "Value", "Footprint", "Datasheet", "Description"]

# Audit 15.2.3, verbatim. The empty value and the MISSING (hide yes) on ki_locked
# are both load-bearing, and so is the hard-coded 1.27 font (audit 15.2.5).
FLAT_KI_LOCKED = (
    '(property "ki_locked" "" (at 0 0 0) (show_name no) (do_not_autoplace no)'
    " (effects (font (size 1.27 1.27))))"
)
FLAT_KI_KEYWORDS = (
    '(property "ki_keywords" "<KEYWORDS>" (at 0 0 0) (show_name no) (do_not_autoplace no)'
    " (hide yes) (effects (font (size 1.27 1.27))))"
)
FLAT_KI_FP_FILTERS = (
    '(property "ki_fp_filters" "<F1> <F2>" (at 0 0 0) (show_name no) (do_not_autoplace no)'
    " (hide yes) (effects (font (size 1.27 1.27))))"
)

# Audit 15.2.4, verbatim, at symbol-child depth (two tabs).
PRETTY_KI_LOCKED = """\
\t\t(property "ki_locked" ""
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
"""
PRETTY_KI_KEYWORDS = """\
\t\t(property "ki_keywords" "<KEYWORDS>"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
"""
PRETTY_KI_FP_FILTERS = """\
\t\t(property "ki_fp_filters" "<F1> <F2>"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
"""

_LIB_HEADER = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "kicad_symbol_editor")
\t(generator_version "10.0")
"""


def _property_node(name: str, value: str, *, extra: str = "") -> str:
    return (
        f'\t\t(property "{name}" "{value}"\n'
        f"\t\t\t(at 0 0 0)\n"
        f"{extra}"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n"
        f"\t\t)\n"
    )


def _library(symbol_body: str, *, name: str = "R") -> str:
    return f'{_LIB_HEADER}\t(symbol "{name}"\n{symbol_body}\t)\n)\n'


def _load_one(symbol_body: str, *, name: str = "R") -> Symbol:
    return SymbolLib.from_string(_library(symbol_body, name=name)).symbols[0]


def _named(lib: SymbolLib, name: str) -> Symbol:
    return next(symbol for symbol in lib.symbols if symbol.name == name)


def _audit_flat(text: str) -> str:
    """Put *text* in the form audit 15.2.3 uses, so the literal can be asserted raw.

    ``_dump_property`` joins its parts with a single space, so the FLAT stream
    closes a property as ``… 1.27))) )`` where the audit writes ``… 1.27))))``.
    This is a whitespace convention of the pre-Prettify stream and nothing else:
    Prettify erases it, and the prettified assertions below carry the audit text
    of 15.2.4 byte for byte. ADR-074.
    """
    return text.replace(" )", ")")


def _emitted_property_names(text: str) -> list[str]:
    """Property names in emission order, symbol-level only (two-tab indent)."""
    return [line.split('"')[1] for line in text.splitlines() if line.startswith('\t\t(property "')]


# ---------------------------------------------------------------------------
# R1 — the model members
# ---------------------------------------------------------------------------


class TestModelMembers:
    def test_defaults_match_the_lib_symbol_constructor(self) -> None:
        symbol = Symbol(name="R")
        assert symbol.units_locked is False
        assert symbol.keywords is None
        assert symbol.fp_filters == []

    def test_each_symbol_gets_its_own_filter_list(self) -> None:
        first = Symbol(name="A")
        second = Symbol(name="B")
        first.fp_filters.append("SOIC*")
        assert second.fp_filters == []

    def test_new_gained_no_dcm_keyword_arguments(self) -> None:
        """Non-goal of the plan: ``Symbol.new`` keeps its old signature."""
        import inspect

        params = list(inspect.signature(Symbol.new).parameters)
        assert params == [
            "name",
            "reference",
            "value",
            "footprint",
            "datasheet",
            "description",
        ]


# ---------------------------------------------------------------------------
# R6 — the {space} transform
# ---------------------------------------------------------------------------


class TestFilterValueTransform:
    def test_a_space_inside_a_filter_escapes(self) -> None:
        assert join_fp_filters(["SOIC*", "DIP 8"]) == "SOIC* DIP{space}8"

    def test_the_escape_round_trips(self) -> None:
        filters = ["SOIC*", "DIP 8"]
        assert split_fp_filters(join_fp_filters(filters)) == filters

    def test_a_single_element_writes_with_no_separator(self) -> None:
        assert join_fp_filters(["*Relay*COTO*3602*"]) == "*Relay*COTO*3602*"

    def test_an_empty_list_renders_empty(self) -> None:
        assert join_fp_filters([]) == ""

    def test_load_splits_on_every_whitespace_kind(self) -> None:
        value = "A\tB\r\nC D"
        assert split_fp_filters(value) == ["A", "B", "C", "D"]

    def test_load_drops_empty_tokens_like_wxTOKEN_STRTOK(self) -> None:
        assert split_fp_filters("   A    B   ") == ["A", "B"]

    def test_an_empty_value_yields_an_empty_list(self) -> None:
        assert split_fp_filters("") == []

    @pytest.mark.parametrize("sequence", ["{tab}", "{return}", "{slash}", "{brace}"])
    def test_any_other_brace_sequence_passes_through_both_ways(self, sequence: str) -> None:
        """ADR-074: this port reverses ``{space}`` only, not the whole UnescapeString."""
        assert split_fp_filters(sequence) == [sequence]
        assert join_fp_filters([sequence]) == sequence


# ---------------------------------------------------------------------------
# R3 (iteration 11.10) - the semantic accessor beside the frozen pair
# ---------------------------------------------------------------------------

# The audit section 5 counterexample table, as filter VALUES. Each row carries
# the byte-preserving split and the semantic split, which differ on four rows.
FILTER_VALUES = [
    "A{space}B",
    "A_{space}B",
    "A_ B",
    "A{tab}B",
    "A{brace}B",
    "A{foo}B",
    "A{B",
    "{{space}}",
    "ab",
    "SOIC* DIP{space}8",
    "A_{space}B DIP{space}8",
    "",
    "   A    B   ",
    "A\tB\r\nC D",
]


class TestSemanticFilterAccessor:
    def test_the_accessor_resolves_what_kicad_resolves(self) -> None:
        assert semantic_fp_filters("A_{space}B DIP{space}8") == ["A_{space}B", "DIP 8"]

    def test_the_accessor_splits_on_every_whitespace_kind(self) -> None:
        assert semantic_fp_filters("A\tB\r\nC D") == ["A", "B", "C", "D"]

    def test_the_accessor_drops_empty_tokens(self) -> None:
        assert semantic_fp_filters("   A    B   ") == ["A", "B"]

    def test_an_empty_value_yields_an_empty_list(self) -> None:
        assert semantic_fp_filters("") == []

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("A{tab}B", ["A\tB"]),
            ("A{brace}B", ["A{B"]),
            ("A{foo}B", ["A{foo}B"]),
            ("A{B", ["A{B"]),
            ("{{space}}", ["{ }"]),
            ("ab", ["ab"]),
        ],
    )
    def test_a_counterexample_row_resolves_the_way_the_audit_states(
        self, value: str, expected: list[str]
    ) -> None:
        assert semantic_fp_filters(value) == expected

    def test_the_accessor_is_not_the_inverse_of_the_writer(self) -> None:
        """Stated so the difference is executable, not only documented."""
        value = "A{tab}B"
        assert join_fp_filters(semantic_fp_filters(value)) == "A\tB"
        assert join_fp_filters(split_fp_filters(value)) == value


class TestTheFrozenPairIsUnchanged:
    """UF-007 freezes ``split_fp_filters`` and ``join_fp_filters``. The new
    accessor runs beside them on the same inputs, so any drift shows up here."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("A{space}B", ["A B"]),
            ("A_{space}B", ["A_ B"]),
            ("A_ B", ["A_", "B"]),
            ("A{tab}B", ["A{tab}B"]),
            ("A{brace}B", ["A{brace}B"]),
            ("A{foo}B", ["A{foo}B"]),
            ("A{B", ["A{B"]),
            ("{{space}}", ["{ }"]),
            ("ab", ["ab"]),
        ],
    )
    def test_the_byte_preserving_split_is_unchanged(self, value: str, expected: list[str]) -> None:
        assert split_fp_filters(value) == expected

    @pytest.mark.parametrize("value", FILTER_VALUES, ids=[repr(v) for v in FILTER_VALUES])
    def test_the_round_trip_still_reproduces_the_source_value(self, value: str) -> None:
        """The whole point of the narrow reverse: the bytes come back."""
        rejoined = join_fp_filters(split_fp_filters(value))
        assert rejoined == " ".join(value.split())

    @pytest.mark.parametrize("value", FILTER_VALUES, ids=[repr(v) for v in FILTER_VALUES])
    def test_the_two_splits_run_on_the_same_input(self, value: str) -> None:
        """Both functions answer, and only the frozen one is byte-preserving."""
        narrow = split_fp_filters(value)
        semantic = semantic_fp_filters(value)
        assert len(narrow) == len(semantic)
        assert join_fp_filters(narrow) == " ".join(value.split())

    def test_the_two_splits_differ_where_the_audit_says_they_differ(self) -> None:
        differing = [v for v in FILTER_VALUES if split_fp_filters(v) != semantic_fp_filters(v)]
        assert differing == [
            "A_{space}B",
            "A{tab}B",
            "A{brace}B",
            "A_{space}B DIP{space}8",
        ]


# ---------------------------------------------------------------------------
# R2 — the loader routes the three names into the members
# ---------------------------------------------------------------------------


class TestLoaderRouting:
    def test_a_root_symbol_with_all_three_gates_on(self) -> None:
        symbol = _named(SymbolLib.from_file(RELAY), "COTO_3602_Split")

        assert symbol.units_locked is True
        assert symbol.keywords == "reed relay low thermal emf"
        assert symbol.fp_filters == ["*Relay*COTO*3602*"]
        assert [field.name for field in symbol.fields] == MANDATORY_FIELD_NAMES

    def test_a_derived_symbol_keeps_its_materialized_values(self) -> None:
        symbol = _named(SymbolLib.from_file(RELAY), "ADW11")

        assert symbol.extends == "Relay_SPST_Latching_1coil"
        assert symbol.units_locked is False
        assert symbol.keywords
        assert symbol.fp_filters
        assert [field.name for field in symbol.fields] == MANDATORY_FIELD_NAMES

    def test_two_gates_off_and_user_fields_ahead_of_the_keywords(self) -> None:
        """``Battery`` is the discriminating stock case (audit 10.2)."""
        symbol = _named(SymbolLib.from_file(DEVICE), "Battery")

        assert symbol.units_locked is False
        assert symbol.fp_filters == []
        assert symbol.keywords == "batt voltage-source cell"
        assert [field.name for field in symbol.fields] == [
            *MANDATORY_FIELD_NAMES,
            "Sim.Device",
            "Sim.Type",
            "Sim.Pins",
        ]

    def test_ki_locked_ignores_its_value(self) -> None:
        symbol = _load_one(_property_node("ki_locked", "anything at all"))
        assert symbol.units_locked is True
        assert symbol.fields == []

    def test_a_repeated_name_wins_last(self) -> None:
        body = _property_node("ki_keywords", "first") + _property_node("ki_keywords", "second")
        assert _load_one(body).keywords == "second"

    def test_a_repeated_filter_list_wins_last(self) -> None:
        body = _property_node("ki_fp_filters", "A B") + _property_node("ki_fp_filters", "C")
        assert _load_one(body).fp_filters == ["C"]

    @pytest.mark.parametrize("name", ["KI_KEYWORDS", "Ki_Locked", "ki_FP_filters"])
    def test_the_name_match_is_case_sensitive(self, name: str) -> None:
        """The C++ branches compare with ``name == "ki_…"``, not ``CmpNoCase``."""
        symbol = _load_one(_property_node(name, "x"))

        assert [field.name for field in symbol.fields] == [name]
        assert symbol.units_locked is False
        assert symbol.keywords is None
        assert symbol.fp_filters == []

    def test_a_filter_value_splits_and_unescapes_on_load(self) -> None:
        symbol = _load_one(_property_node("ki_fp_filters", "SOIC* DIP{space}8"))
        assert symbol.fp_filters == ["SOIC*", "DIP 8"]


# ---------------------------------------------------------------------------
# R3 / R4 — emission, gated, terminal, in the fixed order
# ---------------------------------------------------------------------------


class TestRootEmission:
    def test_all_three_gates_off_emits_nothing(self) -> None:
        text = _dump_symbol(Symbol(name="R"))

        assert "ki_locked" not in text
        assert "ki_keywords" not in text
        assert "ki_fp_filters" not in text

    def test_each_node_matches_the_audit_literal(self) -> None:
        symbol = Symbol(
            name="R",
            units_locked=True,
            keywords="<KEYWORDS>",
            fp_filters=["<F1>", "<F2>"],
        )
        text = _audit_flat(_dump_symbol(symbol))

        assert FLAT_KI_LOCKED in text
        assert FLAT_KI_KEYWORDS in text
        assert FLAT_KI_FP_FILTERS in text

    def test_the_prettified_node_matches_the_audit_literal(self) -> None:
        lib = SymbolLib(
            version=20251024,
            generator="kicad_symbol_editor",
            generator_version="10.0",
            symbols=[
                Symbol(
                    name="R",
                    units_locked=True,
                    keywords="<KEYWORDS>",
                    fp_filters=["<F1>", "<F2>"],
                )
            ],
        )
        text = lib.to_string()

        assert PRETTY_KI_LOCKED in text
        assert PRETTY_KI_KEYWORDS in text
        assert PRETTY_KI_FP_FILTERS in text

    def test_the_order_is_locked_then_keywords_then_filters(self) -> None:
        text = _dump_symbol(Symbol(name="R", units_locked=True, keywords="k", fp_filters=["f"]))
        assert text.index("ki_locked") < text.index("ki_keywords") < text.index("ki_fp_filters")

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"units_locked": True}, ["ki_locked"]),
            ({"keywords": "k"}, ["ki_keywords"]),
            ({"fp_filters": ["f"]}, ["ki_fp_filters"]),
            ({"units_locked": True, "fp_filters": ["f"]}, ["ki_locked", "ki_fp_filters"]),
        ],
    )
    def test_every_gate_is_independent(
        self, kwargs: dict[str, object], expected: list[str]
    ) -> None:
        """All eight subsets occur in the stock corpus (oracle 8.1)."""
        text = _dump_symbol(Symbol(name="R", **kwargs))  # type: ignore[arg-type]
        emitted = [name for name in ("ki_locked", "ki_keywords", "ki_fp_filters") if name in text]
        assert emitted == expected

    def test_an_empty_keyword_string_emits_nothing(self) -> None:
        assert "ki_keywords" not in _dump_symbol(Symbol(name="R", keywords=""))

    def test_the_three_emit_after_the_ordinal_block_and_before_the_units(self) -> None:
        symbol = Symbol.new("R")
        symbol.fields.append(Property(name="MPN", value="X"))
        symbol.units_locked = True
        symbol.keywords = "k"
        symbol.fp_filters = ["f"]

        text = SymbolLib(
            version=20251024,
            generator="kicad_symbol_editor",
            generator_version="10.0",
            symbols=[symbol],
        ).to_string()

        assert _emitted_property_names(text) == [
            *MANDATORY_FIELD_NAMES,
            "MPN",
            "ki_locked",
            "ki_keywords",
            "ki_fp_filters",
        ]

    @pytest.mark.parametrize("position", ["front", "middle"])
    def test_a_scattered_source_position_does_not_survive_the_write(self, position: str) -> None:
        """The stored position must not drive the emitted position (oracle 4 and 6)."""
        dcm = (
            _property_node("ki_locked", "")
            + _property_node("ki_keywords", "k")
            + _property_node("ki_fp_filters", "f")
        )
        mandatory = "".join(_property_node(name, name) for name in MANDATORY_FIELD_NAMES)
        user = _property_node("MPN", "X")
        body = dcm + mandatory + user if position == "front" else mandatory + dcm + user

        lib = SymbolLib.from_string(_library(body))

        assert _emitted_property_names(lib.to_string()) == [
            *MANDATORY_FIELD_NAMES,
            "MPN",
            "ki_locked",
            "ki_keywords",
            "ki_fp_filters",
        ]


class TestDerivedEmission:
    def test_a_derived_symbol_never_emits_ki_locked(self) -> None:
        """The lock block lives inside the IsRoot() branch (lib_cache.cpp:380,470)."""
        symbol = Symbol(name="Child", extends="Parent", units_locked=True, keywords="k")
        text = _dump_symbol(symbol)

        assert "ki_locked" not in text
        assert "ki_keywords" in text

    def test_the_two_survivors_emit_before_embedded_fonts(self) -> None:
        symbol = Symbol(name="Child", extends="Parent", keywords="k", fp_filters=["f"])
        text = _dump_symbol(symbol)

        assert text.index("ki_keywords") < text.index("ki_fp_filters")
        assert text.index("ki_fp_filters") < text.index("(embedded_fonts")

    def test_the_derived_block_emits_after_the_ordinal_block(self) -> None:
        symbol = Symbol.new("Child")
        symbol.extends = "Parent"
        symbol.fields.append(Property(name="MPN", value="X"))
        symbol.keywords = "k"

        lib = SymbolLib(
            version=20251024,
            generator="kicad_symbol_editor",
            generator_version="10.0",
            symbols=[symbol],
        )
        assert _emitted_property_names(lib.to_string()) == [
            *MANDATORY_FIELD_NAMES,
            "MPN",
            "ki_keywords",
        ]


# ---------------------------------------------------------------------------
# R5 — write-time parent fallback
# ---------------------------------------------------------------------------


def _chain(*symbols: Symbol) -> dict[str, Symbol]:
    return {symbol.name: symbol for symbol in symbols}


class TestParentFallback:
    def test_an_empty_child_takes_the_parent_value(self) -> None:
        parent = Symbol(name="Parent", keywords="parent kw", fp_filters=["PF*"])
        child = Symbol(name="Child", extends="Parent")

        text = _dump_symbol(child, by_name=_chain(parent, child))

        assert '"ki_keywords" "parent kw"' in text
        assert '"ki_fp_filters" "PF*"' in text

    def test_a_two_hop_chain_reaches_the_grandparent(self) -> None:
        grandparent = Symbol(name="GP", keywords="gp kw", fp_filters=["GP*"])
        parent = Symbol(name="Parent", extends="GP")
        child = Symbol(name="Child", extends="Parent")

        text = _dump_symbol(child, by_name=_chain(grandparent, parent, child))

        assert '"ki_keywords" "gp kw"' in text
        assert '"ki_fp_filters" "GP*"' in text

    def test_the_child_value_wins_over_the_parent(self) -> None:
        parent = Symbol(name="Parent", keywords="parent kw")
        child = Symbol(name="Child", extends="Parent", keywords="child kw")

        assert '"ki_keywords" "child kw"' in _dump_symbol(child, by_name=_chain(parent, child))

    def test_a_cycle_terminates(self) -> None:
        first = Symbol(name="A", extends="B")
        second = Symbol(name="B", extends="A")

        text = _dump_symbol(first, by_name=_chain(first, second))

        assert "ki_keywords" not in text
        assert "ki_fp_filters" not in text

    def test_a_self_extending_symbol_terminates(self) -> None:
        looped = Symbol(name="A", extends="A")
        assert "ki_keywords" not in _dump_symbol(looped, by_name=_chain(looped))

    def test_a_cycle_still_finds_a_value_on_the_way_round(self) -> None:
        first = Symbol(name="A", extends="B")
        second = Symbol(name="B", extends="A", keywords="b kw")

        assert '"ki_keywords" "b kw"' in _dump_symbol(first, by_name=_chain(first, second))

    def test_an_unresolved_parent_emits_nothing(self) -> None:
        child = Symbol(name="Child", extends="Missing")
        assert "ki_keywords" not in _dump_symbol(child, by_name=_chain(child))

    def test_no_map_means_no_fallback(self) -> None:
        parent = Symbol(name="Parent", keywords="parent kw")
        child = Symbol(name="Child", extends="Parent")

        assert "ki_keywords" not in _dump_symbol(child)
        assert '"ki_keywords" "parent kw"' in _dump_symbol(parent)

    def test_a_root_symbol_never_falls_back(self) -> None:
        """``GetKeyWords`` gates its fallback on ``IsDerived()`` (lib_symbol.h:181)."""
        other = Symbol(name="Other", keywords="other kw")
        root = Symbol(name="Root")

        assert "ki_keywords" not in _dump_symbol(root, by_name=_chain(other, root))

    def test_dump_sym_threads_the_map(self) -> None:
        parent = Symbol(name="Parent", keywords="parent kw")
        child = Symbol(name="Child", extends="Parent")
        lib = SymbolLib(
            version=20251024,
            generator="kicad_symbol_editor",
            generator_version="10.0",
            symbols=[parent, child],
        )
        assert dump_sym(lib).count('"ki_keywords" "parent kw"') == 2


class TestParentNameQualification:
    """The KEY side reduces, the ``extends`` side does not (audit 16.1.7, ADR-078).

    KiCad stores ``m_name`` nickname-stripped (``sch_io_kicad_sexpr_parser.cpp:364``)
    and never parses the ``extends`` value as a ``LIB_ID`` (``:462-482``), so the
    nickname participates on the key side only, by being discarded.
    """

    def test_an_exact_key_match_wins(self) -> None:
        parent = Symbol(name="Device:R", keywords="exact kw")
        decoy = Symbol(name="Other:R", keywords="decoy kw")
        child = Symbol(name="Device:R_Small", extends="Device:R")

        text = _dump_symbol(child, by_name=_chain(parent, decoy, child))
        assert '"ki_keywords" "exact kw"' in text

    def test_a_unique_bare_name_match_wins(self) -> None:
        parent = Symbol(name="Device:R", keywords="bare kw")
        child = Symbol(name="Device:R_Small", extends="R")

        text = _dump_symbol(child, by_name=_chain(parent, child))
        assert '"ki_keywords" "bare kw"' in text

    def test_a_qualified_extends_does_not_reach_a_bare_key(self) -> None:
        """D1: KiCad raises ``THROW_IO_ERROR`` on this file, so nothing is inherited.

        ``(symbol "R")`` keys as ``R`` and ``(extends "Device:R")`` looks up
        ``Device:R`` verbatim, which misses
        (``sch_io_kicad_sexpr_lib_cache.cpp:859-866``).
        """
        parent = Symbol(name="R", keywords="bare key kw")
        child = Symbol(name="R_Small", extends="Device:R")

        text = _dump_symbol(child, by_name=_chain(parent, child))
        assert "ki_keywords" not in text

    def test_a_qualified_extends_does_not_reach_a_differently_qualified_key(self) -> None:
        """D1 again, one step on: the reduced key is ``R``, never ``Other:R``."""
        parent = Symbol(name="Other:R", keywords="other kw")
        child = Symbol(name="R_Small", extends="Device:R")

        assert "ki_keywords" not in _dump_symbol(child, by_name=_chain(parent, child))

    def test_a_bare_extends_reaches_a_qualified_key(self) -> None:
        """D2: KiCad strips the nickname from the key, so this resolves in both."""
        parent = Symbol(name="Foo:Bar", keywords="reduced key kw")
        child = Symbol(name="Foo:Bar_Small", extends="Bar")

        text = _dump_symbol(child, by_name=_chain(parent, child))
        assert '"ki_keywords" "reduced key kw"' in text

    def test_a_slash_escaped_extends_reaches_a_slash_bearing_key(self) -> None:
        """D4: KiCad unescapes ``{slash}`` on both sides before it matches.

        The symbol name at ``sch_io_kicad_sexpr_parser.cpp:343`` and the
        ``extends`` value at ``:477`` take the same replacement. Legacy files are
        the only ones that carry the escape.
        """
        parent = Symbol(name="A/B", keywords="slash kw")
        child = Symbol(name="A/B_Small", extends="A{slash}B")

        text = _dump_symbol(child, by_name=_chain(parent, child))
        assert '"ki_keywords" "slash kw"' in text

    def test_a_slash_escaped_key_is_reached_by_an_unescaped_extends(self) -> None:
        """D4, the mirror: the escape is reversed on the key side too (``:343``)."""
        parent = Symbol(name="A{slash}B", keywords="escaped key kw")
        child = Symbol(name="A/B_Small", extends="A/B")

        text = _dump_symbol(child, by_name=_chain(parent, child))
        assert '"ki_keywords" "escaped key kw"' in text

    def test_a_slash_escaped_name_still_writes_verbatim(self) -> None:
        """The unescape governs matching only; ``Symbol.name`` writes as it loaded."""
        parent = Symbol(name="A{slash}B", keywords="escaped key kw")
        child = Symbol(name="A/B_Small", extends="A/B")

        text = _dump_symbol(parent, by_name=_chain(parent, child))
        assert '(symbol "A{slash}B"' in text

    def test_an_ambiguous_bare_name_applies_no_fallback(self) -> None:
        first = Symbol(name="Device:R", keywords="first kw")
        second = Symbol(name="Other:R", keywords="second kw")
        child = Symbol(name="Device:R_Small", extends="R")

        text = _dump_symbol(child, by_name=_chain(first, second, child))
        assert "ki_keywords" not in text


# ---------------------------------------------------------------------------
# R7 — legacy ki_description upgrades the mandatory Description property
# ---------------------------------------------------------------------------


class TestLegacyDescription:
    def test_it_never_becomes_a_field_of_its_own(self) -> None:
        symbol = _load_one(_property_node("ki_description", "legacy text"))
        assert [field.name for field in symbol.fields] == ["Description"]

    def test_a_missing_description_is_created_with_constructor_defaults(self) -> None:
        symbol = _load_one(_property_node("ki_description", "legacy text"))
        created = symbol.fields[0]

        assert created.value == "legacy text"
        assert created.position == (0.0, 0.0)
        assert created.angle == 0.0
        assert created.visible is False
        assert created.show_name is False
        assert created.do_not_autoplace is False
        assert created.attributes is None

    def test_an_existing_description_keeps_its_geometry_and_effects(self) -> None:
        existing = (
            '\t\t(property "Description" "modern text"\n'
            "\t\t\t(at 1.27 -2.54 90)\n"
            "\t\t\t(show_name yes)\n"
            "\t\t\t(do_not_autoplace no)\n"
            "\t\t\t(hide yes)\n"
            "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 2.54 2.54)\n\t\t\t\t)\n\t\t\t)\n"
            "\t\t)\n"
        )
        symbol = _load_one(existing + _property_node("ki_description", "legacy text"))
        upgraded = symbol.fields[0]

        assert upgraded.value == "legacy text"
        assert upgraded.position == (1.27, -2.54)
        assert upgraded.angle == 90.0
        assert upgraded.show_name is True
        assert upgraded.visible is False
        assert upgraded.attributes == TextAttributes(font_size=(2.54, 2.54))

    def test_a_later_description_node_replaces_the_whole_property(self) -> None:
        body = _property_node("ki_description", "legacy text") + _property_node(
            "Description", "modern text"
        )
        symbol = _load_one(body)

        assert [field.name for field in symbol.fields] == ["Description"]
        assert symbol.fields[0].value == "modern text"

    def test_the_upsert_keeps_file_order(self) -> None:
        body = (
            _property_node("Reference", "R")
            + _property_node("ki_description", "legacy text")
            + _property_node("MPN", "X")
        )
        symbol = _load_one(body)

        assert [field.name for field in symbol.fields] == ["Reference", "Description", "MPN"]

    def test_the_writer_never_emits_it(self) -> None:
        symbol = _load_one(_property_node("ki_description", "legacy text"))
        text = _dump_symbol(symbol)

        assert "ki_description" not in text
        assert '"Description" "legacy text"' in text


# ---------------------------------------------------------------------------
# R8 — the legacy (id N) child token
# ---------------------------------------------------------------------------


class TestLegacyIdToken:
    def test_it_loads_and_is_never_written_back(self) -> None:
        body = _property_node("Reference", "R", extra="\t\t\t(id 5)\n")
        symbol = _load_one(body)

        assert [field.name for field in symbol.fields] == ["Reference"]
        assert "(id " not in _dump_symbol(symbol)

    def test_a_non_integer_payload_is_a_parse_error(self) -> None:
        body = _property_node("Reference", "R", extra="\t\t\t(id x)\n")
        with pytest.raises(KicadParseError, match="Expected integer"):
            _load_one(body)

    def test_an_empty_payload_is_a_parse_error(self) -> None:
        body = _property_node("Reference", "R", extra="\t\t\t(id)\n")
        with pytest.raises(KicadParseError, match="missing its field ID"):
            _load_one(body)


# ---------------------------------------------------------------------------
# R9 — the legacy fixture asserts the UPGRADE, never byte identity
# ---------------------------------------------------------------------------


class TestLegacyFixture:
    """``Transmission_Line.kicad_sym`` is KiCad's own v20220331 qa fixture.

    It carries ``(id N)`` on every property plus ``ki_keywords`` and
    ``ki_description``. A round trip is NOT byte-identical by design: the library
    upgrades the file the way KiCad does (audit 15.1.5). Byte identity for KiCad
    10 input is untouched, and the six fixtures under ``tests/fixtures/sym/``
    still prove it.
    """

    def test_the_fixture_sits_outside_the_byte_identity_glob(self) -> None:
        assert LEGACY.parent.name == "sym_legacy"
        assert not list((FIXTURES / "sym").glob(LEGACY.name))

    def test_ki_description_upgrades_into_the_description_property(self) -> None:
        symbol = SymbolLib.from_file(LEGACY).symbols[0]
        description = next(f for f in symbol.fields if f.name == "Description")

        assert description.value == "Lossless transmission line, for simulation only"
        assert "ki_description" not in [field.name for field in symbol.fields]

    def test_ki_keywords_leaves_the_field_list(self) -> None:
        symbol = SymbolLib.from_file(LEGACY).symbols[0]

        assert symbol.keywords == "lossless transmission line characteristic impedance"
        assert "ki_keywords" not in [field.name for field in symbol.fields]

    def test_the_round_trip_is_an_upgrade_not_a_copy(self) -> None:
        original = LEGACY.read_text(encoding="utf-8")
        written = SymbolLib.from_file(LEGACY).to_string()

        assert written != original
        assert "ki_description" not in written
        assert "(id " not in written
        assert '"Description" "Lossless transmission line, for simulation only"' in written
        assert '"ki_keywords" "lossless transmission line characteristic impedance"' in written

    def test_the_upgraded_output_is_stable_under_a_second_round_trip(self) -> None:
        once = SymbolLib.from_file(LEGACY).to_string()
        twice = SymbolLib.from_string(once).to_string()

        assert once == twice


# ---------------------------------------------------------------------------
# R12 — the placed-symbol side effect of the model change
# ---------------------------------------------------------------------------


class TestInjectSymbolCarriesNoDcmData:
    """``SCH_IO_KICAD_SEXPR::saveSymbol`` synthesizes none of the three (audit 7.1).

    ``inject_symbol`` copies ``incoming.fields`` onto the placed symbol
    (``sch/operations.py``). Before ADR-073 the three names rode along in that
    list and reached a placed node KiCad never writes one on. Now they cannot.
    """

    def test_the_placed_symbol_carries_none_of_the_three(self) -> None:
        from eedesigncore.sch.models import Schematic
        from eedesigncore.sch.operations import inject_symbol

        definition = Symbol.new("R")
        definition.units_locked = True
        definition.keywords = "resistor"
        definition.fp_filters = ["R_*"]

        schematic = Schematic()
        placed = inject_symbol(schematic, definition, at=(0.0, 0.0), lib_nickname="Lib")

        names = [field.name for field in placed.fields]
        assert "ki_locked" not in names
        assert "ki_keywords" not in names
        assert "ki_fp_filters" not in names
        assert names == MANDATORY_FIELD_NAMES

    def test_the_registered_definition_keeps_the_dcm_data_on_its_members(self) -> None:
        from eedesigncore.sch.models import Schematic
        from eedesigncore.sch.operations import inject_symbol
        from eedesigncore.sch.writer import write_sch

        definition = Symbol.new("R")
        definition.units_locked = True
        definition.keywords = "resistor"
        definition.fp_filters = ["R_*"]

        schematic = Schematic()
        inject_symbol(schematic, definition, at=(0.0, 0.0), lib_nickname="Lib")
        registered = schematic.lib_symbols[0]

        assert registered.units_locked is True
        assert registered.keywords == "resistor"
        assert registered.fp_filters == ["R_*"]

        text = write_sch(schematic)
        assert text.count('"ki_keywords"') == 1
        assert text.count('"ki_locked"') == 1
        assert text.count('"ki_fp_filters"') == 1


class TestSchematicLibSymbolsResolveNoParent:
    """D5: a ``.kicad_sch`` ``lib_symbols`` block inherits nothing (audit 16.1.5).

    KiCad parses that block against a throwaway local map
    (``sch_io_kicad_sexpr_parser.cpp:2856-2882``), never calls
    ``updateParentSymbolLinks`` on the path, and rejects a non-root symbol in
    ``SCH_SYMBOL::SetLibSymbol`` (``sch_symbol.cpp:253-255``). Every entry it
    writes is already flattened, so there is no KiCad behaviour for a fallback to
    mirror.
    """

    def _block(self, *symbols: Symbol) -> str:
        from eedesigncore.sch.models import Schematic
        from eedesigncore.sch.writer import _dump_lib_symbols

        schematic = Schematic()
        schematic.lib_symbols = list(symbols)
        return _dump_lib_symbols(schematic)

    def test_a_child_does_not_take_the_parent_keywords(self) -> None:
        parent = Symbol(name="Device:R", keywords="parent kw", fp_filters=["PF*"])
        child = Symbol(name="Device:R_Small", extends="Device:R")

        text = self._block(parent, child)

        assert text.count('"ki_keywords" "parent kw"') == 1
        assert text.count('"ki_fp_filters" "PF*"') == 1

    def test_the_same_pair_does_inherit_on_the_kicad_sym_path(self) -> None:
        """The contrast case: only the library path resolves a parent."""
        parent = Symbol(name="Device:R", keywords="parent kw")
        child = Symbol(name="Device:R_Small", extends="Device:R")
        lib = SymbolLib(
            version=20251024,
            generator="kicad_symbol_editor",
            generator_version="10.0",
            symbols=[parent, child],
        )

        assert dump_sym(lib).count('"ki_keywords" "parent kw"') == 2

    def test_a_childs_own_value_still_emits(self) -> None:
        parent = Symbol(name="Device:R", keywords="parent kw")
        child = Symbol(name="Device:R_Small", extends="Device:R", keywords="child kw")

        text = self._block(parent, child)

        assert '"ki_keywords" "child kw"' in text
        assert '"ki_keywords" "parent kw"' in text

    def test_the_extends_token_is_still_preserved(self) -> None:
        """Audit 16.1.6: KiCad drops it on save, this library keeps it. Not a defect."""
        child = Symbol(name="Device:R_Small", extends="Device:R")

        assert '(extends "Device:R")' in self._block(child)


# ---------------------------------------------------------------------------
# Cost regressions from the iteration-11.8 security review (MEDIUM-5, MEDIUM-6)
# ---------------------------------------------------------------------------


def _qualified_chain(length: int, *, cyclic: bool = False) -> SymbolLib:
    """Build ``s1 → s2 → … → sN`` where every KEY carries a library prefix.

    Every ``extends`` value is bare and every key is qualified, so no exact match
    hits and every hop takes the reduced-key path. That is the shape MEDIUM-5
    named: before the fix each hop rescanned the whole library and the write cost
    grew as the cube of the symbol count.

    The prefix sits on the KEY side, not on the ``extends`` side, because KiCad
    strips a nickname from the key it stores and never parses the ``extends``
    value as a ``LIB_ID`` (audit 16.1.7, divergence D2 against D1). A qualified
    ``extends`` value resolves to nothing here, as it does in KiCad.
    """
    symbols = [Symbol(name=f"Device:s{i}", extends=f"s{i + 1}") for i in range(1, length)]
    tail = Symbol(name=f"Device:s{length}", extends="s1" if cyclic else None)
    if not cyclic:
        tail.keywords = "root kw"
        tail.fp_filters = ["ROOT*"]
    symbols.append(tail)
    return SymbolLib(
        version=20251024,
        generator="kicad_symbol_editor",
        generator_version="10.0",
        symbols=symbols,
    )


class TestParentWalkCost:
    """MEDIUM-5: the walk shares one bare-name index and one memo per write.

    Every assertion here is about the VALUES emitted, never about elapsed time.
    A regression to the per-hop rescan would keep these green but would take the
    cube of the time, which the benchmark in the iteration record measures.
    """

    def test_a_hundred_symbol_qualified_chain_emits_the_root_values(self) -> None:
        text = dump_sym(_qualified_chain(100))

        assert text.count('"ki_keywords" "root kw"') == 100
        assert text.count('"ki_fp_filters" "ROOT*"') == 100

    def test_the_chain_output_is_a_fixed_point_of_a_second_write(self) -> None:
        once = SymbolLib.from_string(_qualified_chain(100).to_string()).to_string()
        twice = SymbolLib.from_string(once).to_string()

        assert once == twice

    def test_a_long_chain_that_closes_into_a_cycle_still_terminates(self) -> None:
        text = dump_sym(_qualified_chain(100, cyclic=True))

        assert "ki_keywords" not in text
        assert "ki_fp_filters" not in text

    def test_a_later_write_sees_a_changed_parent(self) -> None:
        """The memo lives for one write only, so it cannot serve a stale value."""
        lib = _qualified_chain(8)
        first = dump_sym(lib)
        lib.symbols[-1].keywords = "new kw"
        second = dump_sym(lib)

        assert first.count('"ki_keywords" "root kw"') == 8
        assert second.count('"ki_keywords" "new kw"') == 8
        assert "root kw" not in second

    def test_an_ambiguous_bare_name_applies_no_fallback_at_library_scale(self) -> None:
        crowd = [Symbol(name=f"Lib{i}:R", keywords=f"kw {i}") for i in range(50)]
        child = Symbol(name="Device:R_Small", extends="R")

        text = _dump_symbol(child, by_name=_chain(*crowd, child))

        assert "ki_keywords" not in text

    def test_one_exact_key_still_wins_over_fifty_bare_namesakes(self) -> None:
        crowd = [Symbol(name=f"Lib{i}:R", keywords=f"kw {i}") for i in range(50)]
        child = Symbol(name="Device:R_Small", extends="Lib7:R")

        text = _dump_symbol(child, by_name=_chain(*crowd, child))

        assert '"ki_keywords" "kw 7"' in text


class TestDescriptionUpsertCost:
    """MEDIUM-6: the loader carries the Description position instead of searching.

    Each case pins the resulting field list, so the O(1) replace has to land on
    exactly the entry the scan used to find.
    """

    def test_many_user_properties_then_many_descriptions_leave_one_description(self) -> None:
        body = "".join(_property_node(f"u{i}", f"v{i}") for i in range(200))
        body += "".join(_property_node("Description", f"d{i}") for i in range(200))
        symbol = _load_one(body)
        names = [field.name for field in symbol.fields]

        assert len(symbol.fields) == 201
        assert names.count("Description") == 1
        assert names[0] == "u0"
        assert names[-1] == "Description"
        assert symbol.fields[-1].value == "d199"

    def test_a_leading_description_keeps_its_position_under_repeats(self) -> None:
        body = _property_node("Description", "first")
        body += "".join(_property_node(f"u{i}", f"v{i}") for i in range(100))
        body += "".join(_property_node("Description", f"d{i}") for i in range(100))
        symbol = _load_one(body)
        names = [field.name for field in symbol.fields]

        assert len(symbol.fields) == 101
        assert names[0] == "Description"
        assert names.count("Description") == 1
        assert symbol.fields[0].value == "d99"

    def test_a_created_description_is_still_found_after_many_appends(self) -> None:
        body = _property_node("ki_description", "legacy text")
        body += "".join(_property_node(f"u{i}", f"v{i}") for i in range(100))
        body += _property_node("Description", "modern text")
        symbol = _load_one(body)
        names = [field.name for field in symbol.fields]

        assert len(symbol.fields) == 101
        assert names[0] == "Description"
        assert symbol.fields[0].value == "modern text"

    def test_a_case_folded_description_stays_the_upsert_target(self) -> None:
        body = _property_node("description", "lower case name")
        body += "".join(_property_node(f"u{i}", f"v{i}") for i in range(100))
        body += _property_node("ki_description", "legacy text")
        symbol = _load_one(body)
        names = [field.name for field in symbol.fields]

        assert names[0] == "description"
        assert names.count("description") == 1
        assert symbol.fields[0].value == "legacy text"
