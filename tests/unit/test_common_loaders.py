"""Unit tests for common/loaders.py uncovered paths (iter-6 coverage):
- load_stroke with color
- load_fill bare forms (yes/no/hatch/reverse_hatch/cross_hatch)
- load_fill with color
- load_effects with font color, line_spacing, italic, bold via list
- hard-error guards in stroke/fill/effects
- require_sym error paths
- bool_token invalid value
- get_str / get_float / get_int error paths via footprint round-trips
"""

from __future__ import annotations

import pytest

from eedesigncore.common.loaders import (
    bool_token,
    load_effects,
    load_fill,
    load_stroke,
    require_sym,
)
from eedesigncore.common.models import FillType, StrokeType
from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint
from eedesigncore.sexpr.parser import parse

# ---------------------------------------------------------------------------
# Helpers to build minimal nodes for direct function testing
# ---------------------------------------------------------------------------


def _parse_single(text: str):  # type: ignore[return]
    """Parse an S-expression and return the root list node."""
    return parse(text)


# ---------------------------------------------------------------------------
# load_stroke
# ---------------------------------------------------------------------------


class TestLoadStroke:
    def test_stroke_with_color_via_footprint(self) -> None:
        """load_stroke(color ...) path via a full footprint parse."""
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_line
\t\t(start 0 0)
\t\t(end 1 0)
\t\t(stroke (width 0.1) (type solid) (color 255 0 0 1))
\t\t(layer "F.Cu")
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.mod import PcbLine

        lines = [d for d in fp.drawings if isinstance(d, PcbLine)]
        assert len(lines) == 1
        assert lines[0].stroke.color is not None
        assert lines[0].stroke.color.r == 255
        assert lines[0].stroke.type == StrokeType.solid

    def test_stroke_bogus_token_raises_via_footprint(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_line
\t\t(start 0 0)
\t\t(end 1 0)
\t\t(stroke (width 0.1) (type solid) (bogus_stroke 1))
\t\t(layer "F.Cu")
\t)
)
"""
        with pytest.raises(KicadParseError, match="Unknown token in stroke"):
            Footprint.from_string(sexpr)


# ---------------------------------------------------------------------------
# load_fill — bare keyword forms
# ---------------------------------------------------------------------------


class TestLoadFillBareForms:
    def test_fill_yes_via_fp_poly(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_poly
\t\t(pts (xy 0 0) (xy 1 0) (xy 1 1))
\t\t(stroke (width 0) (type default))
\t\t(fill yes)
\t\t(layer "F.Cu")
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.mod import PcbPoly

        polys = [d for d in fp.drawings if isinstance(d, PcbPoly)]
        assert polys[0].fill.type == FillType.outline

    def test_fill_hatch_via_fp_poly(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_poly
\t\t(pts (xy 0 0) (xy 1 0) (xy 1 1))
\t\t(stroke (width 0) (type default))
\t\t(fill hatch)
\t\t(layer "F.Cu")
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.mod import PcbPoly

        polys = [d for d in fp.drawings if isinstance(d, PcbPoly)]
        assert polys[0].fill.type == FillType.hatch

    def test_fill_reverse_hatch_via_fp_poly(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_poly
\t\t(pts (xy 0 0) (xy 1 0) (xy 1 1))
\t\t(stroke (width 0) (type default))
\t\t(fill reverse_hatch)
\t\t(layer "F.Cu")
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.mod import PcbPoly

        polys = [d for d in fp.drawings if isinstance(d, PcbPoly)]
        assert polys[0].fill.type == FillType.reverse_hatch

    def test_fill_cross_hatch_via_fp_poly(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_poly
\t\t(pts (xy 0 0) (xy 1 0) (xy 1 1))
\t\t(stroke (width 0) (type default))
\t\t(fill cross_hatch)
\t\t(layer "F.Cu")
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.mod import PcbPoly

        polys = [d for d in fp.drawings if isinstance(d, PcbPoly)]
        assert polys[0].fill.type == FillType.cross_hatch

    def test_fill_with_color_via_sym(self) -> None:
        """fill (type color) with (color r g b a) child."""
        sexpr = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "S"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "S"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "S"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(symbol "S_0_1"
\t\t\t(circle
\t\t\t\t(center 0 0)
\t\t\t\t(radius 1)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type color) (color 0 128 255 0.5))
\t\t\t)
\t\t)
\t)
)
"""
        from eedesigncore.common.sch_family.shapes import SchCircle as Circle
        from eedesigncore.sym.models import SymbolLib

        lib = SymbolLib.from_string(sexpr)
        circles = [i for i in lib.symbols[0].units[0].items if isinstance(i, Circle)]
        assert circles[0].fill.type == FillType.color
        assert circles[0].fill.color is not None
        assert circles[0].fill.color.b == 255

    def test_fill_bogus_token_raises(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_poly
\t\t(pts (xy 0 0) (xy 1 0) (xy 1 1))
\t\t(stroke (width 0) (type default))
\t\t(fill (type none) (bogus_fill_child 1))
\t\t(layer "F.Cu")
\t)
)
"""
        with pytest.raises(KicadParseError, match="Unknown token in fill"):
            Footprint.from_string(sexpr)


# ---------------------------------------------------------------------------
# load_effects — font color, line_spacing, italic, bold via list
# ---------------------------------------------------------------------------


class TestLoadEffects:
    def test_effects_font_color_via_footprint(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_text user "Label"
\t\t(at 0 0 0)
\t\t(layer "F.SilkS")
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t\t(color 0 255 0 1)
\t\t\t\t(line_spacing 1.5)
\t\t\t\t(bold yes)
\t\t\t\t(italic yes)
\t\t\t)
\t\t)
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.mod import PcbText

        texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        assert len(texts) == 1
        attrs = texts[0].attributes
        assert attrs is not None
        assert attrs.color is not None
        assert attrs.color.g == 255
        assert attrs.line_spacing == pytest.approx(1.5)
        assert attrs.bold is True
        assert attrs.italic is True

    def test_effects_bold_italic_bare_symbols(self) -> None:
        """bold/italic as bare symbols (not list tokens) in font block."""
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_text user "Label"
\t\t(at 0 0 0)
\t\t(layer "F.SilkS")
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t\t bold italic
\t\t\t)
\t\t)
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.mod import PcbText

        texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        attrs = texts[0].attributes
        assert attrs is not None
        assert attrs.bold is True
        assert attrs.italic is True

    def test_effects_justify_right_bottom_mirror(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_text user "Label"
\t\t(at 0 0 0)
\t\t(layer "F.SilkS")
\t\t(effects
\t\t\t(font (size 1.27 1.27))
\t\t\t(justify right bottom mirror)
\t\t)
\t)
)
"""
        fp = Footprint.from_string(sexpr)
        from eedesigncore.common.models import JustifyH, JustifyV
        from eedesigncore.mod import PcbText

        texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        attrs = texts[0].attributes
        assert attrs is not None
        assert attrs.justify_h == JustifyH.right
        assert attrs.justify_v == JustifyV.bottom
        assert attrs.mirror is True

    def test_effects_bogus_token_raises(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_text user "Label"
\t\t(at 0 0 0)
\t\t(layer "F.SilkS")
\t\t(effects
\t\t\t(font (size 1.27 1.27))
\t\t\t(bogus_effects_child 1)
\t\t)
\t)
)
"""
        with pytest.raises(KicadParseError, match="Unknown token in effects"):
            Footprint.from_string(sexpr)

    def test_effects_font_bogus_token_raises(self) -> None:
        sexpr = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
\t(fp_text user "Label"
\t\t(at 0 0 0)
\t\t(layer "F.SilkS")
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t\t(bogus_font_token 1)
\t\t\t)
\t\t)
\t)
)
"""
        with pytest.raises(KicadParseError, match="Unknown token in font"):
            Footprint.from_string(sexpr)


# ---------------------------------------------------------------------------
# require_sym error paths
# ---------------------------------------------------------------------------


class TestRequireSym:
    def test_require_sym_wrong_name_raises(self) -> None:
        node = parse("(footprint)")
        with pytest.raises(KicadParseError, match="Expected token 'stroke'"):
            require_sym(node, "stroke")

    def test_require_sym_non_list_raises(self) -> None:
        """Passing a scalar (SYMBOL) node raises KicadParseError."""
        # A symbol node is not a list; parse gives us a list wrapper
        # We access the inner atom directly
        node = parse("(footprint)")
        assert isinstance(node.value, list)
        atom = node.value[0]  # the SYMBOL "footprint"
        with pytest.raises(KicadParseError, match="Expected list"):
            require_sym(atom, "footprint")


# ---------------------------------------------------------------------------
# bool_token invalid value
# ---------------------------------------------------------------------------


class TestBoolToken:
    def test_invalid_bool_raises(self) -> None:
        node = parse("(hide maybe)")
        with pytest.raises(KicadParseError, match="Expected yes/no boolean"):
            bool_token(node)


# ---------------------------------------------------------------------------
# load_stroke / load_fill direct
# ---------------------------------------------------------------------------


class TestLoadStrokeDirect:
    def test_stroke_with_color_direct(self) -> None:
        node = parse("(stroke (width 0.1) (type solid) (color 128 64 32 0.5))")
        stroke = load_stroke(node)
        assert stroke.width == pytest.approx(0.1)
        assert stroke.type == StrokeType.solid
        assert stroke.color is not None
        assert stroke.color.r == 128
        assert stroke.color.a == pytest.approx(0.5)

    def test_stroke_bogus_type_raises(self) -> None:
        node = parse("(stroke (width 0) (type super_custom_type))")
        with pytest.raises(KicadParseError, match="Unknown stroke type"):
            load_stroke(node)


class TestLoadFillDirect:
    def test_fill_none_via_type(self) -> None:
        node = parse("(fill (type none))")
        fill = load_fill(node)
        assert fill.type == FillType.none

    def test_fill_background_via_type(self) -> None:
        node = parse("(fill (type background))")
        fill = load_fill(node)
        assert fill.type == FillType.background

    def test_fill_outline_via_type(self) -> None:
        node = parse("(fill (type outline))")
        fill = load_fill(node)
        assert fill.type == FillType.outline

    def test_fill_with_color_direct(self) -> None:
        node = parse("(fill (type color) (color 10 20 30 0.9))")
        fill = load_fill(node)
        assert fill.type == FillType.color
        assert fill.color is not None
        assert fill.color.b == 30

    def test_fill_bad_type_raises(self) -> None:
        node = parse("(fill (type lava_lamp))")
        with pytest.raises(KicadParseError, match="Unknown fill type"):
            load_fill(node)


# ---------------------------------------------------------------------------
# load_effects direct
# ---------------------------------------------------------------------------


class TestLoadEffectsDirect:
    def test_effects_face(self) -> None:
        node = parse('(effects (font (face "Courier") (size 1.27 1.27)))')
        attrs = load_effects(node)
        assert attrs.font_face == "Courier"

    def test_effects_thickness(self) -> None:
        node = parse("(effects (font (size 1.27 1.27) (thickness 0.15)))")
        attrs = load_effects(node)
        assert attrs.font_thickness == pytest.approx(0.15)

    def test_effects_left_justify(self) -> None:
        node = parse("(effects (font (size 1.27 1.27)) (justify left))")
        attrs = load_effects(node)
        from eedesigncore.common.models import JustifyH

        assert attrs.justify_h == JustifyH.left

    def test_effects_top_justify(self) -> None:
        node = parse("(effects (font (size 1.27 1.27)) (justify top))")
        attrs = load_effects(node)
        from eedesigncore.common.models import JustifyV

        assert attrs.justify_v == JustifyV.top

    def test_effects_hide(self) -> None:
        node = parse("(effects (font (size 1.27 1.27)) (hide yes))")
        attrs = load_effects(node)
        assert attrs.visible is False


# ---------------------------------------------------------------------------
# get_float rejects a non-finite number (LOW-10)
#
# The tokenizer classifies ``1.0e400`` as a DOUBLE and ``float("1.0e400")``
# returns infinity rather than raising. An infinity then reaches ``math.floor``
# in ``kiround`` at load, or a bare ``int()`` in a save-order comparator, and
# both raise ``OverflowError``. That is outside the ``KicadParseError`` family,
# so a caller guarding a batch job aborts instead of recording one bad file.
# ---------------------------------------------------------------------------


def _footprint_with(body: str) -> str:
    return (
        '(footprint "TEST"\n'
        "\t(version 20260206)\n"
        '\t(generator "test")\n'
        '\t(layer "F.Cu")\n'
        "\t(duplicate_pad_numbers_are_jumpers no)\n"
        "\t(embedded_fonts no)\n"
        f"{body}\n"
        ")"
    )


_ARC = (
    "\t(fp_arc\n\t\t(start {v} 0)\n\t\t(mid 1 1)\n\t\t(end 2 0)\n"
    "\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type solid)\n\t\t)\n"
    '\t\t(layer "F.SilkS")\n\t)'
)
_PAD = (
    '\t(pad "1" smd rect\n\t\t(at {v} 0)\n\t\t(size 1 1)\n'
    '\t\t(layers "F.Cu" "F.Paste" "F.Mask")\n\t)'
)


class TestGetFloatRejectsNonFinite:
    @pytest.mark.parametrize("value", ["1.0e400", "-1.0e400"])
    def test_infinity_in_an_arc_position_raises_kicad_parse_error(self, value: str) -> None:
        with pytest.raises(KicadParseError, match="finite"):
            Footprint.from_string(_footprint_with(_ARC.format(v=value)))

    @pytest.mark.parametrize("value", ["1.0e400", "-1.0e400"])
    def test_infinity_in_a_pad_position_raises_kicad_parse_error(self, value: str) -> None:
        with pytest.raises(KicadParseError, match="finite"):
            Footprint.from_string(_footprint_with(_PAD.format(v=value)))

    def test_the_error_names_the_offending_token(self) -> None:
        with pytest.raises(KicadParseError, match="1.0e400"):
            Footprint.from_string(_footprint_with(_ARC.format(v="1.0e400")))

    @pytest.mark.parametrize("value", ["nan", "NaN", "-nan", "inf"])
    def test_a_nan_shaped_token_raises_kicad_parse_error(self, value: str) -> None:
        """The tokenizer classifies these as SYMBOL, so the type branch rejects them."""
        with pytest.raises(KicadParseError):
            Footprint.from_string(_footprint_with(_ARC.format(v=value)))

    @pytest.mark.parametrize("value", ["nan", "inf"])
    def test_a_nan_shaped_token_in_a_pad_position_raises_kicad_parse_error(
        self, value: str
    ) -> None:
        with pytest.raises(KicadParseError):
            Footprint.from_string(_footprint_with(_PAD.format(v=value)))

    def test_an_integer_atom_too_wide_for_a_float_raises_kicad_parse_error(self) -> None:
        """``float()`` of a 400-digit int raises ``OverflowError``, not a ValueError."""
        with pytest.raises(KicadParseError, match="too large"):
            Footprint.from_string(_footprint_with(_ARC.format(v="9" * 400)))

    def test_a_finite_value_still_loads(self) -> None:
        """The guard rejects only non-finite values. Load-time winding may swap the ends."""
        arc = Footprint.from_string(_footprint_with(_ARC.format(v="1.5"))).drawings[0]
        assert sorted([arc.start[0], arc.end[0]]) == pytest.approx([1.5, 2.0])
