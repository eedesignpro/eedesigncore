"""Unit tests for constructability wave 2 (iteration-11 T5, plan R6).

Covers GAP-016 (the ``SymText.angle`` unit trap), GAP-017 (``SymbolUnit.name``
derivation), GAP-019 (``ReferenceImage.from_png_bytes``) and GAP-021 (enum
surfaces). Every token set asserted below is quoted from the KiCad writer or
parser in the matching model docstring.
"""

from __future__ import annotations

import base64
import zlib

import pytest
from pydantic import ValidationError

from eedesigncore.common.layers import CANONICAL_LAYER_NAME_SET, is_valid_layer_name
from eedesigncore.common.pcb_family import PadstackLayer, ReferenceImage
from eedesigncore.common.pcb_family.enums import (
    ChamferPosition,
    CustomAnchor,
    CustomShapeClearance,
    PadShape,
)
from eedesigncore.common.sch_family.enums import PowerType
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.common.sch_family.text import SymText
from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.enums import ChamferPosition as ModChamferPosition
from eedesigncore.mod.models import Footprint, Pad
from eedesigncore.mod.writer import write_mod
from eedesigncore.sym.models import SymbolLib
from eedesigncore.sym.writer import write_sym


def _png(width: int = 1) -> bytes:
    """Build a minimal valid PNG byte string."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            len(payload).to_bytes(4, "big")
            + kind
            + payload
            + zlib.crc32(kind + payload).to_bytes(4, "big")
        )

    ihdr = width.to_bytes(4, "big") + b"\x00\x00\x00\x01" + b"\x08\x02\x00\x00\x00"
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# GAP-016 — SymText.angle unit trap
# ---------------------------------------------------------------------------


class TestSymTextAngleUnits:
    def test_angle_stays_raw_tenths(self) -> None:
        """The field is the on-disk value. Byte identity depends on that."""
        assert SymText(angle=900).angle == 900

    def test_angle_degrees_reads_degrees(self) -> None:
        assert SymText(angle=900).angle_degrees == 90.0

    def test_angle_degrees_exposes_the_trap(self) -> None:
        """SymText(angle=90) is NINE degrees, which is the whole point of GAP-016."""
        assert SymText(angle=90).angle_degrees == 9.0

    def test_angle_degrees_writes_tenths(self) -> None:
        text = SymText()
        text.angle_degrees = 90
        assert text.angle == 900

    def test_angle_degrees_rounds_to_an_integer(self) -> None:
        text = SymText()
        text.angle_degrees = 45.55
        assert text.angle == 456

    def test_from_degrees_scales(self) -> None:
        assert SymText.from_degrees(90, content="A").angle == 900

    def test_from_degrees_keeps_the_other_fields(self) -> None:
        text = SymText.from_degrees(180, content="B", position=(1.0, 2.0), private=True)
        assert (text.content, text.position, text.private) == ("B", (1.0, 2.0), True)

    def test_from_degrees_writes_the_scaled_value(self) -> None:
        sym = Symbol.new("T")
        sym.units = [SymbolUnit(name="T_1_1", items=[SymText.from_degrees(90, content="A")])]
        assert " 900)" in write_sym(SymbolLib(symbols=[sym]))


# ---------------------------------------------------------------------------
# GAP-017 — SymbolUnit.name derivation
# ---------------------------------------------------------------------------


class TestSymbolUnitNameDerivation:
    def test_encode_name_joins_name_and_indices(self) -> None:
        assert SymbolUnit.encode_name("R", 1, 1) == "R_1_1"

    def test_encode_name_takes_a_second_unit(self) -> None:
        assert SymbolUnit.encode_name("U", 2, 1) == "U_2_1"

    def test_encode_name_takes_a_demorgan_body_style(self) -> None:
        assert SymbolUnit.encode_name("U", 1, 2) == "U_1_2"

    def test_encode_name_drops_the_library_nickname(self) -> None:
        """KiCad encodes the LIB_ID item name only (lib cache :377)."""
        assert SymbolUnit.encode_name("Device:R", 2, 1) == "R_2_1"

    def test_for_symbol_keeps_the_name_and_indices_consistent(self) -> None:
        unit = SymbolUnit.for_symbol("R", unit_index=2, body_style_index=1)
        assert (unit.name, unit.unit_index, unit.body_style_index) == ("R_2_1", 2, 1)

    def test_for_symbol_defaults_to_unit_one_body_style_one(self) -> None:
        assert SymbolUnit.for_symbol("R").name == "R_1_1"

    def test_for_symbol_carries_items_and_display_name(self) -> None:
        unit = SymbolUnit.for_symbol("R", unit_display_name="Gate A", items=[SymText()])
        assert unit.unit_display_name == "Gate A"
        assert len(unit.items) == 1

    def test_for_symbol_round_trips_through_the_loader(self) -> None:
        sym = Symbol.new("R")
        sym.units = [SymbolUnit.for_symbol("R", unit_index=2)]
        text = write_sym(SymbolLib(symbols=[sym]))
        reloaded = SymbolLib.from_string(text)
        assert reloaded.symbols[0].units[0].unit_index == 2
        assert reloaded.symbols[0].units[0].name == "R_2_1"


# ---------------------------------------------------------------------------
# GAP-019 — ReferenceImage.from_png_bytes
# ---------------------------------------------------------------------------


class TestReferenceImageFromPngBytes:
    def test_data_is_plain_base64_of_the_png(self) -> None:
        """No zstd and no checksum, unlike EmbeddedFile.from_bytes."""
        png = _png()
        image = ReferenceImage.from_png_bytes(png)
        assert base64.b64decode(image.data) == png

    def test_defaults_match_the_model(self) -> None:
        image = ReferenceImage.from_png_bytes(_png())
        assert (image.position, image.layer, image.scale, image.locked) == (
            (0.0, 0.0),
            "F.Cu",
            None,
            False,
        )

    def test_placement_arguments_pass_through(self) -> None:
        image = ReferenceImage.from_png_bytes(
            _png(), position=(1.0, 2.0), layer="Dwgs.User", scale=0.5, locked=True
        )
        assert (image.position, image.layer, image.scale, image.locked) == (
            (1.0, 2.0),
            "Dwgs.User",
            0.5,
            True,
        )

    def test_non_png_bytes_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="needs PNG data"):
            ReferenceImage.from_png_bytes(b"\xff\xd8\xff\xe0 jpeg")

    def test_empty_bytes_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="needs PNG data"):
            ReferenceImage.from_png_bytes(b"")

    def test_the_writer_chunks_the_data(self) -> None:
        fp = Footprint.new("R")
        fp.drawings = [ReferenceImage.from_png_bytes(_png(width=4))]
        text = write_mod(fp)
        assert "(data " in text
        assert "(image" in text


# ---------------------------------------------------------------------------
# GAP-021 — enum surfaces
# ---------------------------------------------------------------------------


class TestEnumSurfaces:
    def test_chamfer_position_valid_set(self) -> None:
        assert {c.value for c in ChamferPosition} == {
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        }

    def test_custom_shape_clearance_valid_set(self) -> None:
        assert {c.value for c in CustomShapeClearance} == {"convexhull", "outline"}

    def test_custom_anchor_valid_set(self) -> None:
        assert {c.value for c in CustomAnchor} == {"circle", "rect"}

    def test_power_type_valid_set(self) -> None:
        assert {p.value for p in PowerType} == {"global", "local"}

    def test_mod_enums_re_exports_the_shared_enum(self) -> None:
        """mod/enums.py is a re-export shim, so one name means one thing."""
        assert ModChamferPosition is ChamferPosition

    def test_pad_coerces_a_chamfer_string(self) -> None:
        pad = Pad(layers=["F.Cu"], chamfer_positions=["top_left"])
        assert pad.chamfer_positions == [ChamferPosition.top_left]

    def test_pad_rejects_an_unknown_chamfer_position(self) -> None:
        with pytest.raises(ValidationError):
            Pad(layers=["F.Cu"], chamfer_positions=["middle"])

    def test_pad_rejects_an_unknown_custom_anchor(self) -> None:
        with pytest.raises(ValidationError):
            Pad(layers=["F.Cu"], custom_anchor="triangle")

    def test_padstack_layer_shape_is_typed(self) -> None:
        layer = PadstackLayer(layer_name="F.Cu", shape="roundrect")
        assert layer.shape is PadShape.roundrect

    def test_padstack_layer_rejects_an_unknown_shape(self) -> None:
        with pytest.raises(ValidationError):
            PadstackLayer(layer_name="F.Cu", shape="chamfrect")

    def test_symbol_power_type_is_typed(self) -> None:
        assert Symbol(name="VCC", power_type="global").power_type is PowerType.global_

    def test_symbol_rejects_an_unknown_power_type(self) -> None:
        with pytest.raises(ValidationError):
            Symbol(name="VCC", power_type="worldwide")

    def test_layer_name_valid_set_is_discoverable(self) -> None:
        """Layer names stay strings, so common/layers.py is the valid set (GAP-021)."""
        assert "F.Cu" in CANONICAL_LAYER_NAME_SET
        assert is_valid_layer_name("In7.Cu")
        assert not is_valid_layer_name("F.Copper")


class TestEnumTokenErrors:
    def test_the_loader_names_the_valid_set_for_a_bad_padstack_shape(self) -> None:
        text = _footprint_with_padstack_shape("chamfrect")
        with pytest.raises(KicadParseError, match="Valid values: circle, rect"):
            Footprint.from_string(text)

    def test_the_loader_accepts_every_padstack_shape_token(self) -> None:
        for shape in PadShape:
            fp = Footprint.from_string(_footprint_with_padstack_shape(shape.value))
            padstack = fp.pads[0].padstack
            assert padstack is not None
            assert padstack.layers[0].shape is shape

    def test_the_loader_names_the_valid_set_for_a_bad_power_type(self) -> None:
        text = (
            '(kicad_symbol_lib (version 20251024) (generator "t") '
            '(generator_version "0") (symbol "VCC" (power worldwide) '
            "(exclude_from_sim no) (in_bom yes) (on_board yes) (in_pos_files yes) "
            "(duplicate_pin_numbers_are_jumpers no) (embedded_fonts no)))"
        )
        with pytest.raises(KicadParseError, match="Valid values: global, local"):
            SymbolLib.from_string(text)


def _footprint_with_padstack_shape(shape: str) -> str:
    return (
        '(footprint "T" (version 20260206) (generator "x") (layer "F.Cu")\n'
        '\t(pad "1" thru_hole rect (at 0 0) (size 2 2) (drill 1.0)\n'
        '\t\t(layers "*.Cu")\n'
        f'\t\t(padstack (mode front_inner_back) (layer "F.Cu" (shape {shape}) (size 2 2)))\n'
        "\t)\n"
        "\t(embedded_fonts no)\n"
        ")\n"
    )
