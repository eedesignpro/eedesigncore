"""Unit tests for T4 — hard-error ``else: raise KicadParseError`` guards.

Each test injects a synthetic S-expression with an unknown token and asserts
that ``KicadParseError`` is raised rather than silently discarded.
"""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint
from eedesigncore.sym.models import SymbolLib

# ---------------------------------------------------------------------------
# Helpers — minimal valid S-expression templates
# ---------------------------------------------------------------------------

_MINIMAL_MOD_HEAD = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
"""

_MINIMAL_SYM_HEAD = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
"""


def _make_mod(extra: str) -> str:
    return _MINIMAL_MOD_HEAD + extra + "\n)"


def _make_sym(extra: str) -> str:
    return _MINIMAL_SYM_HEAD + extra + "\n)"


# ---------------------------------------------------------------------------
# load_mod root loop
# ---------------------------------------------------------------------------


class TestLoadModRootUnknownToken:
    def test_unknown_root_token_raises(self) -> None:
        sexpr = _make_mod('\t(BOGUS_TOKEN "data")')
        with pytest.raises(KicadParseError, match="Unknown token in footprint"):
            Footprint.from_string(sexpr)


class TestLoadPadUnknownToken:
    def test_unknown_pad_child_raises(self) -> None:
        sexpr = _make_mod(
            '\t(pad "1" smd rect\n'
            "\t\t(at 0 0)\n"
            "\t\t(size 1 1)\n"
            '\t\t(layers "F.Cu")\n'
            "\t\t(UNKNOWN_PAD_TOKEN yes)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in pad"):
            Footprint.from_string(sexpr)


class TestLoadDrawingUnknownToken:
    def test_unknown_drawing_token_raises(self) -> None:
        """_load_drawing raises on an unrecognised draw-item name."""
        # Inject a known drawing-dispatch name set, but sneak in an unknown
        # token via a direct fp_text wrapper that would call _load_drawing
        # — but the dispatch itself raises if the inner call returns fp_unknown.
        # Easier: just put an unknown item directly in the footprint root
        # through a known drawing slot. We can't inject a fake name through
        # the dispatch; instead call the function directly.
        from eedesigncore.mod.loader import _load_drawing
        from eedesigncore.sexpr.parser import parse

        tree = parse('(fp_unknown "x")')
        with pytest.raises(KicadParseError, match="Unknown drawing item"):
            _load_drawing(tree)


class TestLoadPadPrimitiveUnknownToken:
    def test_unknown_primitive_raises(self) -> None:
        from eedesigncore.mod.loader import _load_pad_primitive
        from eedesigncore.sexpr.parser import parse

        tree = parse("(gr_UNKNOWN (start 0 0) (end 1 1) (width 0.1))")
        with pytest.raises(KicadParseError, match="Unknown pad primitive"):
            _load_pad_primitive(tree)


class TestLoadTeardropsUnknownToken:
    def test_unknown_teardrops_child_raises(self) -> None:
        sexpr = _make_mod(
            '\t(pad "1" smd rect\n'
            "\t\t(at 0 0)\n"
            "\t\t(size 1 1)\n"
            '\t\t(layers "F.Cu")\n'
            "\t\t(teardrops\n"
            "\t\t\t(UNKNOWN_TD yes)\n"
            "\t\t)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in teardrops"):
            Footprint.from_string(sexpr)


class TestLoadGroupUnknownToken:
    def test_unknown_group_child_raises(self) -> None:
        sexpr = _make_mod(
            '\t(group "g"\n'
            '\t\t(uuid "00000000-0000-0000-0000-000000000000")\n'
            "\t\t(BADTOKEN yes)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in group"):
            Footprint.from_string(sexpr)


class TestLoadAttrsUnknownFlag:
    def test_unknown_attr_flag_raises(self) -> None:
        sexpr = _make_mod("\t(attr smd MYSTERY_FLAG)")
        with pytest.raises(KicadParseError, match="Unknown attr flag"):
            Footprint.from_string(sexpr)


# ---------------------------------------------------------------------------
# load_sym root loop
# ---------------------------------------------------------------------------


class TestLoadSymRootUnknownToken:
    def test_unknown_root_token_raises(self) -> None:
        sexpr = _make_sym('\t(BOGUS_SYM_TOKEN "data")')
        with pytest.raises(KicadParseError, match="Unknown token in kicad_symbol_lib"):
            SymbolLib.from_string(sexpr)


class TestLoadSymbolUnknownToken:
    def test_unknown_symbol_child_raises(self) -> None:
        sexpr = _make_sym(
            '\t(symbol "TEST"\n'
            "\t\t(in_bom yes)\n"
            "\t\t(on_board yes)\n"
            "\t\t(UNKNOWN_SYM_FIELD yes)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in symbol"):
            SymbolLib.from_string(sexpr)


class TestLoadUnitUnknownToken:
    def test_unknown_unit_item_raises(self) -> None:
        sexpr = _make_sym(
            '\t(symbol "TEST"\n'
            "\t\t(in_bom yes)\n"
            "\t\t(on_board yes)\n"
            '\t\t(symbol "TEST_1_1"\n'
            "\t\t\t(UNKNOWN_UNIT_ITEM yes)\n"
            "\t\t)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in symbol unit"):
            SymbolLib.from_string(sexpr)


class TestLoadPinUnknownToken:
    def test_unknown_pin_child_raises(self) -> None:
        sexpr = _make_sym(
            '\t(symbol "TEST"\n'
            "\t\t(in_bom yes)\n"
            "\t\t(on_board yes)\n"
            '\t\t(symbol "TEST_1_1"\n'
            "\t\t\t(pin input line\n"
            "\t\t\t\t(at 0 0 0)\n"
            "\t\t\t\t(length 2.54)\n"
            '\t\t\t\t(name "~" (effects (font (size 1.27 1.27))))\n'
            '\t\t\t\t(number "1" (effects (font (size 1.27 1.27))))\n'
            "\t\t\t\t(UNKNOWN_PIN_CHILD yes)\n"
            "\t\t\t)\n"
            "\t\t)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in pin"):
            SymbolLib.from_string(sexpr)


# ---------------------------------------------------------------------------
# common/loaders.py guards
# ---------------------------------------------------------------------------


class TestHardenedTokenCompleteLoops:
    """ADR-017 hard-error hardening of token-complete loader loops (S4 commit H1)."""

    def test_unknown_fp_text_token_raises(self) -> None:
        sexpr = _make_mod(
            '\t(fp_text user "x"\n\t\t(at 0 0)\n\t\t(layer "F.SilkS")\n\t\t(BOGUS_FPTEXT yes)\n\t)'
        )
        with pytest.raises(KicadParseError, match="Unknown token in fp_text"):
            Footprint.from_string(sexpr)

    def test_unknown_fp_text_box_token_raises(self) -> None:
        sexpr = _make_mod(
            '\t(fp_text_box "x"\n'
            "\t\t(start 0 0)\n"
            "\t\t(end 1 1)\n"
            "\t\t(margins 0 0 0 0)\n"
            '\t\t(layer "F.SilkS")\n'
            "\t\t(BOGUS_TEXTBOX yes)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in fp_text_box"):
            Footprint.from_string(sexpr)

    def test_unknown_property_token_raises(self) -> None:
        sexpr = _make_mod(
            '\t(property "Reference" "U1"\n'
            "\t\t(at 0 0)\n"
            '\t\t(layer "F.SilkS")\n'
            "\t\t(BOGUS_PROP yes)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in property"):
            Footprint.from_string(sexpr)

    def test_unknown_render_cache_token_raises(self) -> None:
        from eedesigncore.common.pcb_family.loaders import _load_render_cache
        from eedesigncore.sexpr.parser import parse

        tree = parse('(render_cache "text" 0 (BOGUS_RC (pts)))')
        with pytest.raises(KicadParseError, match="Unknown token in render_cache"):
            _load_render_cache(tree)

    @pytest.mark.parametrize(
        ("prim", "token"),
        [
            ("(gr_line (start 0 0) (end 1 1) (width 0.1) (BOGUS yes))", "gr_line"),
            ("(gr_rect (start 0 0) (end 1 1) (width 0.1) (BOGUS yes))", "gr_rect"),
            ("(gr_arc (start 0 0) (mid 0 1) (end 1 1) (width 0.1) (BOGUS yes))", "gr_arc"),
            ("(gr_circle (center 0 0) (end 1 1) (width 0.1) (BOGUS yes))", "gr_circle"),
            ("(gr_curve (pts (xy 0 0)) (width 0.1) (BOGUS yes))", "gr_curve"),
            ("(gr_poly (pts (xy 0 0)) (width 0.1) (BOGUS yes))", "gr_poly"),
        ],
    )
    def test_unknown_pad_primitive_inner_token_raises(self, prim: str, token: str) -> None:
        from eedesigncore.mod.loader import _load_pad_primitive
        from eedesigncore.sexpr.parser import parse

        tree = parse(prim)
        with pytest.raises(KicadParseError, match=f"Unknown token in {token}"):
            _load_pad_primitive(tree)

    def test_unknown_sym_property_token_raises(self) -> None:
        sexpr = _make_sym(
            '\t(symbol "TEST"\n'
            '\t\t(property "Reference" "U"\n'
            "\t\t\t(at 0 0 0)\n"
            "\t\t\t(BOGUS_SYM_PROP yes)\n"
            "\t\t)\n"
            "\t)"
        )
        with pytest.raises(KicadParseError, match="Unknown token in property"):
            SymbolLib.from_string(sexpr)


class TestLoadStrokeUnknownToken:
    def test_unknown_stroke_child_raises(self) -> None:
        from eedesigncore.common.loaders import load_stroke
        from eedesigncore.sexpr.parser import parse

        tree = parse("(stroke (width 0.1) (type default) (UNKNOWN yes))")
        with pytest.raises(KicadParseError, match="Unknown token in stroke"):
            load_stroke(tree)


class TestLoadFillUnknownToken:
    def test_unknown_fill_child_raises(self) -> None:
        from eedesigncore.common.loaders import load_fill
        from eedesigncore.sexpr.parser import parse

        tree = parse("(fill (type none) (UNKNOWN yes))")
        with pytest.raises(KicadParseError, match="Unknown token in fill"):
            load_fill(tree)


class TestLoadEffectsUnknownToken:
    def test_unknown_effects_child_raises(self) -> None:
        from eedesigncore.common.loaders import load_effects
        from eedesigncore.sexpr.parser import parse

        tree = parse("(effects (font (size 1.27 1.27)) (UNKNOWN_EFFECT yes))")
        with pytest.raises(KicadParseError, match="Unknown token in effects"):
            load_effects(tree)

    def test_unknown_font_child_raises(self) -> None:
        from eedesigncore.common.loaders import load_effects
        from eedesigncore.sexpr.parser import parse

        tree = parse("(effects (font (size 1.27 1.27) (UNKNOWN_FONT_TOKEN yes)))")
        with pytest.raises(KicadParseError, match="Unknown token in font"):
            load_effects(tree)
