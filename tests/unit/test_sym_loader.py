"""Unit tests for the symbol library loader."""

import pytest

from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sym.enums import PinElectricalType, PinShape
from eedesigncore.sym.models import SymbolLib

_SIMPLE_LIB = """\
(kicad_symbol_lib
    (version 20251024)
    (generator "kicad_symbol_editor")
    (generator_version "10.0")
    (symbol "R"
        (exclude_from_sim no)
        (in_bom yes)
        (on_board yes)
        (in_pos_files yes)
        (duplicate_pin_numbers_are_jumpers no)
        (property "Reference" "R"
            (at 0 0 0)
            (show_name no)
            (do_not_autoplace no)
            (effects
                (font
                    (size 1.27 1.27)
                )
            )
        )
        (property "Value" "R"
            (at 0 0 0)
            (show_name no)
            (do_not_autoplace no)
            (effects
                (font
                    (size 1.27 1.27)
                )
            )
        )
        (symbol "R_0_1"
            (polyline
                (pts
                    (xy -1.016 -0.508)
                    (xy -1.016 0.508)
                )
                (stroke (width 0) (type default))
                (fill (type none))
            )
        )
        (symbol "R_1_1"
            (pin passive line
                (at -2.54 0 0)
                (length 2.54)
                (name "~"
                    (effects (font (size 1.27 1.27)))
                )
                (number "1"
                    (effects (font (size 1.27 1.27)))
                )
            )
        )
        (embedded_fonts no)
    )
)
"""

_DERIVED_LIB = """\
(kicad_symbol_lib
    (version 20251024)
    (generator "kicad_symbol_editor")
    (generator_version "10.0")
    (symbol "R_base"
        (exclude_from_sim no)
        (in_bom yes)
        (on_board yes)
        (in_pos_files yes)
        (duplicate_pin_numbers_are_jumpers no)
        (property "Reference" "R"
            (at 0 0 0)
            (show_name no)
            (do_not_autoplace no)
            (effects (font (size 1.27 1.27)))
        )
        (property "Value" "R_base"
            (at 0 0 0)
            (show_name no)
            (do_not_autoplace no)
            (effects (font (size 1.27 1.27)))
        )
        (symbol "R_base_1_1"
            (pin passive line
                (at -2.54 0 0)
                (length 2.54)
                (name "~" (effects (font (size 1.27 1.27))))
                (number "1" (effects (font (size 1.27 1.27))))
            )
        )
        (embedded_fonts no)
    )
    (symbol "R_derived"
        (extends "R_base")
        (property "Value" "R_derived"
            (at 0 0 0)
            (show_name no)
            (do_not_autoplace no)
            (effects (font (size 1.27 1.27)))
        )
        (embedded_fonts no)
    )
)
"""

_PIN_WITH_ALT = """\
(kicad_symbol_lib
    (version 20251024)
    (generator "kicad_symbol_editor")
    (generator_version "10.0")
    (symbol "MCU"
        (exclude_from_sim no)
        (in_bom yes)
        (on_board yes)
        (in_pos_files yes)
        (duplicate_pin_numbers_are_jumpers no)
        (property "Reference" "U"
            (at 0 0 0)
            (show_name no)
            (do_not_autoplace no)
            (effects (font (size 1.27 1.27)))
        )
        (property "Value" "MCU"
            (at 0 0 0)
            (show_name no)
            (do_not_autoplace no)
            (effects (font (size 1.27 1.27)))
        )
        (symbol "MCU_1_1"
            (pin bidirectional line
                (at 0 0 0)
                (length 2.54)
                (name "PA0"
                    (effects (font (size 1.27 1.27)))
                )
                (number "1"
                    (effects (font (size 1.27 1.27)))
                )
                (alternate "ADC0" input line)
                (alternate "UART_TX" output line)
            )
        )
        (embedded_fonts no)
    )
)
"""


class TestLoadRootSymbol:
    def test_parses_library(self) -> None:
        lib = SymbolLib.from_string(_SIMPLE_LIB)
        assert lib.version == 20251024
        assert lib.generator == "kicad_symbol_editor"

    def test_symbol_name(self) -> None:
        lib = SymbolLib.from_string(_SIMPLE_LIB)
        assert len(lib.symbols) == 1
        assert lib.symbols[0].name == "R"

    def test_symbol_properties(self) -> None:
        lib = SymbolLib.from_string(_SIMPLE_LIB)
        sym = lib.symbols[0]
        assert sym.in_bom is True
        assert sym.on_board is True
        assert sym.embedded_fonts is False

    def test_fields_parsed(self) -> None:
        lib = SymbolLib.from_string(_SIMPLE_LIB)
        fields = lib.symbols[0].fields
        names = [f.name for f in fields]
        assert "Reference" in names
        assert "Value" in names

    def test_units_parsed(self) -> None:
        lib = SymbolLib.from_string(_SIMPLE_LIB)
        sym = lib.symbols[0]
        assert len(sym.units) == 2
        unit_names = [u.name for u in sym.units]
        assert "R_0_1" in unit_names
        assert "R_1_1" in unit_names

    def test_unit_items_parsed(self) -> None:
        lib = SymbolLib.from_string(_SIMPLE_LIB)
        sym = lib.symbols[0]
        unit_01 = next(u for u in sym.units if u.name == "R_0_1")
        assert len(unit_01.items) == 1  # polyline

    def test_pin_parsed(self) -> None:
        lib = SymbolLib.from_string(_SIMPLE_LIB)
        sym = lib.symbols[0]
        unit_11 = next(u for u in sym.units if u.name == "R_1_1")
        pins = [item for item in unit_11.items if isinstance(item, Pin)]
        assert len(pins) == 1
        pin = pins[0]
        assert pin.electrical_type == PinElectricalType.passive
        assert pin.shape == PinShape.line
        assert pin.number == "1"


class TestLoadDerivedSymbol:
    def test_derived_has_extends(self) -> None:
        lib = SymbolLib.from_string(_DERIVED_LIB)
        assert len(lib.symbols) == 2
        derived = next(s for s in lib.symbols if s.name == "R_derived")
        assert derived.extends == "R_base"

    def test_root_has_no_extends(self) -> None:
        lib = SymbolLib.from_string(_DERIVED_LIB)
        root = next(s for s in lib.symbols if s.name == "R_base")
        assert root.extends is None

    def test_derived_has_overridden_field(self) -> None:
        lib = SymbolLib.from_string(_DERIVED_LIB)
        derived = next(s for s in lib.symbols if s.name == "R_derived")
        val_field = next(f for f in derived.fields if f.name == "Value")
        assert val_field.value == "R_derived"


class TestLoadPinWithAlternates:
    def test_pin_alternates_parsed(self) -> None:
        lib = SymbolLib.from_string(_PIN_WITH_ALT)
        sym = lib.symbols[0]
        unit = sym.units[0]
        pins = [item for item in unit.items if isinstance(item, Pin)]
        assert len(pins) == 1
        pin = pins[0]
        assert len(pin.alternates) == 2

    def test_alternate_electrical_types(self) -> None:
        lib = SymbolLib.from_string(_PIN_WITH_ALT)
        sym = lib.symbols[0]
        unit = sym.units[0]
        pins = [item for item in unit.items if isinstance(item, Pin)]
        alts = pins[0].alternates
        assert alts[0].name == "ADC0"
        assert alts[0].electrical_type == PinElectricalType.input
        assert alts[1].name == "UART_TX"
        assert alts[1].electrical_type == PinElectricalType.output


class TestParseErrors:
    def test_wrong_root_token_raises(self) -> None:
        bad = "(not_a_lib (version 1))"
        with pytest.raises(KicadParseError):
            SymbolLib.from_string(bad)

    def test_root_level_embedded_token_rejected(self) -> None:
        """GAP-027: embedded fonts/files are per-symbol; KiCad's root parser
        rejects them at the library root (Expecting symbol/generator/...)."""
        bad = """\
(kicad_symbol_lib
    (version 20251024)
    (generator "kicad_symbol_editor")
    (generator_version "10.0")
    (embedded_fonts no)
)
"""
        with pytest.raises(KicadParseError, match="Unknown token in kicad_symbol_lib"):
            SymbolLib.from_string(bad)

    def test_symbollib_has_no_embedded_fields(self) -> None:
        """GAP-027: the dead SymbolLib.embedded_* fields were removed."""
        lib = SymbolLib()
        assert not hasattr(lib, "embedded_fonts")
        assert not hasattr(lib, "embedded_files")

    def test_unknown_pin_type_raises(self) -> None:
        bad_lib = """\
(kicad_symbol_lib
    (version 1)
    (generator "test")
    (generator_version "1.0")
    (symbol "BadSym"
        (exclude_from_sim no) (in_bom yes) (on_board yes) (in_pos_files yes)
        (duplicate_pin_numbers_are_jumpers no)
        (property "Reference" "U" (at 0 0 0) (show_name no) (do_not_autoplace no)
            (effects (font (size 1.27 1.27))))
        (property "Value" "BadSym" (at 0 0 0) (show_name no) (do_not_autoplace no)
            (effects (font (size 1.27 1.27))))
        (symbol "BadSym_1_1"
            (pin NOT_A_REAL_TYPE line
                (at 0 0 0) (length 2.54)
                (name "X" (effects (font (size 1.27 1.27))))
                (number "1" (effects (font (size 1.27 1.27))))
            )
        )
        (embedded_fonts no)
    )
)
"""
        with pytest.raises(KicadParseError):
            SymbolLib.from_string(bad_lib)
