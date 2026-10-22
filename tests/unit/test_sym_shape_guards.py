"""Unit tests for sym shape loaders: uuid handling, private flag,
hard-error guards, and round-trips for arc/circle/rect/bezier/polyline/text_box
(iter-6 coverage for sym/loader.py and sym/writer.py).
"""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.sym.models import SymbolLib

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LIB_HEAD = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
"""

_SYM_HEAD = """\
\t(symbol "TestSym"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "U"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "TestSym"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
"""

_UNIT_HEAD = """\
\t\t(symbol "TestSym_1_1"
"""


def _make_sym(shape_sexpr: str) -> str:
    """Build a complete kicad_symbol_lib with one unit containing *shape_sexpr*."""
    return _LIB_HEAD + _SYM_HEAD + _UNIT_HEAD + shape_sexpr + "\t\t)\n\t)\n)"


def _round_trip(sexpr: str) -> SymbolLib:
    lib = SymbolLib.from_string(sexpr)
    out = lib.to_string()
    return SymbolLib.from_string(out)


# ---------------------------------------------------------------------------
# Arc uuid
# ---------------------------------------------------------------------------

_ARC_WITH_UUID = _make_sym(
    """\
\t\t\t(arc
\t\t\t\t(start 0 1.27)
\t\t\t\t(mid 1.27 0)
\t\t\t\t(end 0 -1.27)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(uuid "arc-uuid-1")
\t\t\t)
"""
)

_ARC_PRIVATE = _make_sym(
    """\
\t\t\t(arc private
\t\t\t\t(start 0 1.27)
\t\t\t\t(mid 1.27 0)
\t\t\t\t(end 0 -1.27)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t)
"""
)


class TestArcShape:
    def test_arc_uuid_parsed(self) -> None:
        lib = SymbolLib.from_string(_ARC_WITH_UUID)
        unit = lib.symbols[0].units[0]
        from eedesigncore.common.sch_family.shapes import SchArc as Arc

        arcs = [i for i in unit.items if isinstance(i, Arc)]
        assert len(arcs) == 1
        assert arcs[0].id == "arc-uuid-1"

    def test_arc_uuid_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_ARC_WITH_UUID)
        lib2 = _round_trip(_ARC_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchArc as Arc

        a1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, Arc)][0]
        a2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, Arc)][0]
        assert a1.id == a2.id
        assert a1.start == a2.start
        assert a1.end == a2.end

    def test_arc_private_flag(self) -> None:
        lib = SymbolLib.from_string(_ARC_PRIVATE)
        from eedesigncore.common.sch_family.shapes import SchArc as Arc

        arcs = [i for i in lib.symbols[0].units[0].items if isinstance(i, Arc)]
        assert arcs[0].private is True

    def test_arc_private_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_ARC_PRIVATE)
        lib2 = _round_trip(_ARC_PRIVATE)
        from eedesigncore.common.sch_family.shapes import SchArc as Arc

        a1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, Arc)][0]
        a2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, Arc)][0]
        assert a1.private == a2.private

    def test_arc_bogus_token_raises(self) -> None:
        bad = _make_sym(
            """\
\t\t\t(arc
\t\t\t\t(start 0 1)
\t\t\t\t(mid 1 0)
\t\t\t\t(end 0 -1)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(bogus_arc_token 1)
\t\t\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in arc"):
            SymbolLib.from_string(bad)


# ---------------------------------------------------------------------------
# Circle uuid + private
# ---------------------------------------------------------------------------

_CIRCLE_WITH_UUID = _make_sym(
    """\
\t\t\t(circle private
\t\t\t\t(center 0 0)
\t\t\t\t(radius 1.27)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(uuid "circle-uuid-1")
\t\t\t)
"""
)


class TestCircleShape:
    def test_circle_uuid_and_private(self) -> None:
        lib = SymbolLib.from_string(_CIRCLE_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchCircle as Circle

        circles = [i for i in lib.symbols[0].units[0].items if isinstance(i, Circle)]
        assert len(circles) == 1
        assert circles[0].id == "circle-uuid-1"
        assert circles[0].private is True

    def test_circle_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_CIRCLE_WITH_UUID)
        lib2 = _round_trip(_CIRCLE_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchCircle as Circle

        c1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, Circle)][0]
        c2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, Circle)][0]
        assert c1.id == c2.id
        assert c1.private == c2.private
        assert c1.radius == pytest.approx(c2.radius)

    def test_circle_bogus_token_raises(self) -> None:
        bad = _make_sym(
            """\
\t\t\t(circle
\t\t\t\t(center 0 0)
\t\t\t\t(radius 1)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(bogus_circle_token 1)
\t\t\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in circle"):
            SymbolLib.from_string(bad)


# ---------------------------------------------------------------------------
# Rect uuid + corner_radius + private
# ---------------------------------------------------------------------------

_RECT_WITH_UUID = _make_sym(
    """\
\t\t\t(rectangle
\t\t\t\t(start -2.54 1.27)
\t\t\t\t(end 2.54 -1.27)
\t\t\t\t(radius 0.5)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(uuid "rect-uuid-1")
\t\t\t)
"""
)


class TestRectShape:
    def test_rect_uuid(self) -> None:
        lib = SymbolLib.from_string(_RECT_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchRect as Rect

        rects = [i for i in lib.symbols[0].units[0].items if isinstance(i, Rect)]
        assert len(rects) == 1
        assert rects[0].id == "rect-uuid-1"

    def test_rect_corner_radius(self) -> None:
        lib = SymbolLib.from_string(_RECT_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchRect as Rect

        rects = [i for i in lib.symbols[0].units[0].items if isinstance(i, Rect)]
        assert rects[0].corner_radius == pytest.approx(0.5)

    def test_rect_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_RECT_WITH_UUID)
        lib2 = _round_trip(_RECT_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchRect as Rect

        r1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, Rect)][0]
        r2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, Rect)][0]
        assert r1.id == r2.id
        assert r1.corner_radius == pytest.approx(r2.corner_radius)  # type: ignore[arg-type]

    def test_rect_bogus_token_raises(self) -> None:
        bad = _make_sym(
            """\
\t\t\t(rectangle
\t\t\t\t(start 0 0)
\t\t\t\t(end 1 1)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(bogus_rect_token 1)
\t\t\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in rectangle"):
            SymbolLib.from_string(bad)


# ---------------------------------------------------------------------------
# Bezier uuid + private
# ---------------------------------------------------------------------------

_BEZIER_WITH_UUID = _make_sym(
    """\
\t\t\t(bezier private
\t\t\t\t(pts
\t\t\t\t\t(xy 0 0)
\t\t\t\t\t(xy 1 2)
\t\t\t\t\t(xy 2 2)
\t\t\t\t\t(xy 3 0)
\t\t\t\t)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(uuid "bezier-uuid-1")
\t\t\t)
"""
)


class TestBezierShape:
    def test_bezier_uuid_and_private(self) -> None:
        lib = SymbolLib.from_string(_BEZIER_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchBezier as Bezier

        items = [i for i in lib.symbols[0].units[0].items if isinstance(i, Bezier)]
        assert len(items) == 1
        assert items[0].id == "bezier-uuid-1"
        assert items[0].private is True

    def test_bezier_pts(self) -> None:
        lib = SymbolLib.from_string(_BEZIER_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchBezier as Bezier

        items = [i for i in lib.symbols[0].units[0].items if isinstance(i, Bezier)]
        assert len(items[0].pts) == 4

    def test_bezier_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_BEZIER_WITH_UUID)
        lib2 = _round_trip(_BEZIER_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchBezier as Bezier

        b1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, Bezier)][0]
        b2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, Bezier)][0]
        assert b1.id == b2.id
        assert b1.private == b2.private

    def test_bezier_bogus_token_raises(self) -> None:
        bad = _make_sym(
            """\
\t\t\t(bezier
\t\t\t\t(pts (xy 0 0) (xy 1 1))
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(bogus_bezier_token 1)
\t\t\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in bezier"):
            SymbolLib.from_string(bad)


# ---------------------------------------------------------------------------
# Polyline uuid + private (additional coverage)
# ---------------------------------------------------------------------------

_POLYLINE_WITH_UUID = _make_sym(
    """\
\t\t\t(polyline private
\t\t\t\t(pts
\t\t\t\t\t(xy 0 0)
\t\t\t\t\t(xy 1 0)
\t\t\t\t\t(xy 1 1)
\t\t\t\t)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(uuid "poly-uuid-1")
\t\t\t)
"""
)


class TestPolylineShape:
    def test_polyline_uuid_and_private(self) -> None:
        lib = SymbolLib.from_string(_POLYLINE_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchPolyline as Polyline

        items = [i for i in lib.symbols[0].units[0].items if isinstance(i, Polyline)]
        assert len(items) == 1
        assert items[0].id == "poly-uuid-1"
        assert items[0].private is True

    def test_polyline_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_POLYLINE_WITH_UUID)
        lib2 = _round_trip(_POLYLINE_WITH_UUID)
        from eedesigncore.common.sch_family.shapes import SchPolyline as Polyline

        p1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, Polyline)][0]
        p2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, Polyline)][0]
        assert p1.id == p2.id

    def test_polyline_bogus_token_raises(self) -> None:
        bad = _make_sym(
            """\
\t\t\t(polyline
\t\t\t\t(pts (xy 0 0) (xy 1 1))
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(bogus_poly_token 1)
\t\t\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in polyline"):
            SymbolLib.from_string(bad)


# ---------------------------------------------------------------------------
# SymText uuid + private
# ---------------------------------------------------------------------------

_SYM_TEXT = _make_sym(
    """\
\t\t\t(text private "hello"
\t\t\t\t(at 0 0 0)
\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t)
"""
)


class TestSymText:
    def test_sym_text_private(self) -> None:
        lib = SymbolLib.from_string(_SYM_TEXT)
        from eedesigncore.common.sch_family.text import SymText

        texts = [i for i in lib.symbols[0].units[0].items if isinstance(i, SymText)]
        assert len(texts) == 1
        assert texts[0].private is True
        assert texts[0].content == "hello"

    def test_sym_text_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_SYM_TEXT)
        lib2 = _round_trip(_SYM_TEXT)
        from eedesigncore.common.sch_family.text import SymText

        t1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, SymText)][0]
        t2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, SymText)][0]
        assert t1.private == t2.private
        assert t1.content == t2.content

    def test_sym_text_bogus_token_raises(self) -> None:
        bad = _make_sym(
            """\
\t\t\t(text "hello"
\t\t\t\t(at 0 0 0)
\t\t\t\t(bogus_text_token 1)
\t\t\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in sym text"):
            SymbolLib.from_string(bad)


# ---------------------------------------------------------------------------
# SymTextBox private + uuid
# ---------------------------------------------------------------------------

_SYM_TEXT_BOX = _make_sym(
    """\
\t\t\t(text_box private "note"
\t\t\t\t(at 0 0 0)
\t\t\t\t(size 10 5)
\t\t\t\t(margins 0.5 0.5 0.5 0.5)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t)
"""
)


class TestSymTextBox:
    def test_sym_textbox_private(self) -> None:
        lib = SymbolLib.from_string(_SYM_TEXT_BOX)
        from eedesigncore.common.sch_family.text import SymTextBox

        tbs = [i for i in lib.symbols[0].units[0].items if isinstance(i, SymTextBox)]
        assert len(tbs) == 1
        assert tbs[0].private is True
        assert tbs[0].content == "note"

    def test_sym_textbox_attributes(self) -> None:
        lib = SymbolLib.from_string(_SYM_TEXT_BOX)
        from eedesigncore.common.sch_family.text import SymTextBox

        tbs = [i for i in lib.symbols[0].units[0].items if isinstance(i, SymTextBox)]
        assert tbs[0].attributes is not None

    def test_sym_textbox_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_SYM_TEXT_BOX)
        lib2 = _round_trip(_SYM_TEXT_BOX)
        from eedesigncore.common.sch_family.text import SymTextBox

        tb1 = [i for i in lib1.symbols[0].units[0].items if isinstance(i, SymTextBox)][0]
        tb2 = [i for i in lib2.symbols[0].units[0].items if isinstance(i, SymTextBox)][0]
        assert tb1.private == tb2.private
        assert tb1.content == tb2.content
        assert tb1.size == tb2.size

    def test_sym_textbox_bogus_token_raises(self) -> None:
        bad = _make_sym(
            """\
\t\t\t(text_box "note"
\t\t\t\t(at 0 0 0)
\t\t\t\t(size 10 5)
\t\t\t\t(margins 0 0 0 0)
\t\t\t\t(stroke (width 0) (type default))
\t\t\t\t(fill (type none))
\t\t\t\t(bogus_textbox_token 1)
\t\t\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in sym text_box"):
            SymbolLib.from_string(bad)


# ---------------------------------------------------------------------------
# Symbol-level features: power, body_styles, pin_numbers, pin_names,
#                        jumper_pin_groups, extends
# ---------------------------------------------------------------------------

_POWER_SYM = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "PWR_FLAG"
\t\t(power global)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "#PWR"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "PWR_FLAG"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(symbol "PWR_FLAG_0_1"
\t\t\t(pin power_in line
\t\t\t\t(at 0 0 270)
\t\t\t\t(length 0)
\t\t\t\t(name "~"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t\t(number "1"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t)
\t\t)
\t)
)
"""


class TestSymbolLevelFeatures:
    def test_power_type_parsed(self) -> None:
        lib = SymbolLib.from_string(_POWER_SYM)
        sym = lib.symbols[0]
        assert sym.power_type == "global"

    def test_power_round_trip(self) -> None:
        lib1 = SymbolLib.from_string(_POWER_SYM)
        lib2 = _round_trip(_POWER_SYM)
        assert lib1.symbols[0].power_type == lib2.symbols[0].power_type

    def test_body_styles_demorgan(self) -> None:
        sexpr = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "XOR"
\t\t(body_styles demorgan)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "U"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "XOR"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
)
"""
        lib = SymbolLib.from_string(sexpr)
        assert lib.symbols[0].body_styles == ["demorgan"]

    def test_pin_numbers_hidden(self) -> None:
        sexpr = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "R"
\t\t(pin_numbers (hide yes))
\t\t(pin_names (offset 0.25) (hide yes))
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "R"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "R"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
)
"""
        lib = SymbolLib.from_string(sexpr)
        assert lib.symbols[0].show_pin_numbers is False
        assert lib.symbols[0].show_pin_names is False
        assert lib.symbols[0].pin_name_offset == pytest.approx(0.25)

    def test_pin_names_hidden_round_trip(self) -> None:
        sexpr = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "R"
\t\t(pin_names (hide yes))
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "R"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "R"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
)
"""
        lib1 = SymbolLib.from_string(sexpr)
        lib2 = _round_trip(sexpr)
        assert lib1.symbols[0].show_pin_names == lib2.symbols[0].show_pin_names

    def test_extends_symbol(self) -> None:
        sexpr = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "Base"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "U"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "Base"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
\t(symbol "Derived"
\t\t(extends "Base")
\t\t(property "Reference" "U"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "Derived"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
)
"""
        lib = SymbolLib.from_string(sexpr)
        assert len(lib.symbols) == 2
        derived = [s for s in lib.symbols if s.name == "Derived"][0]
        assert derived.extends == "Base"

    def test_extends_round_trip(self) -> None:
        sexpr = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "Base"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "U"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "Base"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
\t(symbol "Derived"
\t\t(extends "Base")
\t\t(property "Reference" "U"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "Derived"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
)
"""
        lib1 = SymbolLib.from_string(sexpr)
        lib2 = _round_trip(sexpr)
        d1 = [s for s in lib1.symbols if s.name == "Derived"][0]
        d2 = [s for s in lib2.symbols if s.name == "Derived"][0]
        assert d1.extends == d2.extends

    def test_pin_alternate(self) -> None:
        sexpr = (
            _LIB_HEAD
            + _SYM_HEAD
            + """\
\t\t(symbol "TestSym_0_1"
\t\t\t(pin bidirectional line
\t\t\t\t(at 0 0 0)
\t\t\t\t(length 2.54)
\t\t\t\t(name "IO"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t\t(number "1"
\t\t\t\t\t(effects (font (size 1.27 1.27)))
\t\t\t\t)
\t\t\t\t(alternate "ALT_NAME" input line)
\t\t\t)
\t\t)
\t)
)
"""
        )
        lib = SymbolLib.from_string(sexpr)
        from eedesigncore.common.sch_family.pins import Pin
        from eedesigncore.sym.enums import PinElectricalType

        pins = [i for i in lib.symbols[0].units[0].items if isinstance(i, Pin)]
        assert len(pins) == 1
        assert len(pins[0].alternates) == 1
        alt = pins[0].alternates[0]
        assert alt.name == "ALT_NAME"
        assert alt.electrical_type == PinElectricalType.input

    def test_unit_name_display(self) -> None:
        sexpr = (
            _LIB_HEAD
            + _SYM_HEAD
            + """\
\t\t(symbol "TestSym_1_1"
\t\t\t(unit_name "Gate A")
\t\t)
\t)
)
"""
        )
        lib = SymbolLib.from_string(sexpr)
        assert lib.symbols[0].units[0].unit_display_name == "Gate A"

    def test_unit_name_round_trip(self) -> None:
        sexpr = (
            _LIB_HEAD
            + _SYM_HEAD
            + """\
\t\t(symbol "TestSym_1_1"
\t\t\t(unit_name "Gate A")
\t\t)
\t)
)
"""
        )
        lib1 = SymbolLib.from_string(sexpr)
        lib2 = _round_trip(sexpr)
        dn1 = lib1.symbols[0].units[0].unit_display_name
        dn2 = lib2.symbols[0].units[0].unit_display_name
        assert dn1 == dn2

    def test_jumper_pin_groups(self) -> None:
        sexpr = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "test")
\t(generator_version "0")
\t(symbol "Jumper"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers yes)
\t\t(jumper_pin_groups
\t\t\t("1" "2" )
\t\t)
\t\t(property "Reference" "JP"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "Jumper"
\t\t\t(at 0 0 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t)
)
"""
        lib = SymbolLib.from_string(sexpr)
        sym = lib.symbols[0]
        assert sym.duplicate_pin_numbers_are_jumpers is True
        assert sym.jumper_pin_groups is not None
        assert len(sym.jumper_pin_groups) == 1
