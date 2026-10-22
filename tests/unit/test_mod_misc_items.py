"""Unit tests for table, image, barcode, point, variant, component_classes
round-trips (iter-6 coverage).
"""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint

_HEAD = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
"""


def _make(extra: str) -> str:
    return _HEAD + extra + "\n)"


def _round_trip(sexpr: str) -> Footprint:
    fp = Footprint.from_string(sexpr)
    out = fp.to_string()
    return Footprint.from_string(out)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

_TABLE = _make(
    """\
\t(table (column_count 2)
\t\t(uuid "tbl-uuid-1")
\t\t(layer "F.Cu")
\t\t(border
\t\t\t(external yes)
\t\t\t(header no)
\t\t)
\t\t(separators
\t\t\t(rows yes)
\t\t\t(cols no)
\t\t)
\t\t(column_widths 10 20)
\t\t(row_heights 5 5)
\t\t(cells
\t\t\t(table_cell "A1"
\t\t\t\t(start 0 0)
\t\t\t\t(end 10 5)
\t\t\t\t(margins 0.5 0.5 0.5 0.5)
\t\t\t\t(span 1 1)
\t\t\t)
\t\t\t(table_cell "B1"
\t\t\t\t(locked yes)
\t\t\t\t(start 10 0)
\t\t\t\t(end 30 5)
\t\t\t\t(margins 0.5 0.5 0.5 0.5)
\t\t\t\t(span 2 1)
\t\t\t)
\t\t)
\t)
"""
)


class TestTable:
    def test_table_parses(self) -> None:
        fp = Footprint.from_string(_TABLE)
        assert len(fp.tables) == 1
        t = fp.tables[0]
        assert t.column_count == 2
        assert t.id == "tbl-uuid-1"
        assert t.layer == "F.Cu"

    def test_table_border(self) -> None:
        fp = Footprint.from_string(_TABLE)
        b = fp.tables[0].border
        assert b.external is True
        assert b.header is False

    def test_table_separators(self) -> None:
        fp = Footprint.from_string(_TABLE)
        s = fp.tables[0].separators
        assert s.rows is True
        assert s.cols is False

    def test_table_widths_heights(self) -> None:
        fp = Footprint.from_string(_TABLE)
        t = fp.tables[0]
        assert t.column_widths == pytest.approx([10.0, 20.0])
        assert t.row_heights == pytest.approx([5.0, 5.0])

    def test_table_cells(self) -> None:
        fp = Footprint.from_string(_TABLE)
        cells = fp.tables[0].cells
        assert len(cells) == 2
        assert cells[0].content == "A1"
        assert cells[0].start == (0.0, 0.0)
        assert cells[0].end == (10.0, 5.0)
        assert cells[1].content == "B1"
        assert cells[1].start == (10.0, 0.0)
        assert cells[1].end == (30.0, 5.0)
        assert cells[1].span == (2, 1)
        assert cells[1].locked is True

    def test_table_cell_emits_start_end_not_at_size(self) -> None:
        fp = Footprint.from_string(_TABLE)
        out = fp.to_string()
        assert "(start 0 0)" in out
        assert "(end 10 5)" in out
        assert "(size" not in out  # cells never emit (size ...)
        # locked precedes start/end for the second cell
        locked_pos = out.index("(locked yes)")
        start_pos = out.index("(start 10 0)")
        assert locked_pos < start_pos

    def test_table_round_trip(self) -> None:
        fp1 = Footprint.from_string(_TABLE)
        fp2 = _round_trip(_TABLE)
        assert fp1.tables[0] == fp2.tables[0]

    def test_table_cell_effects(self) -> None:
        """TableCell with effects (font info)."""
        sexpr = _make(
            """\
\t(table (column_count 1)
\t\t(border
\t\t\t(external yes)
\t\t\t(header no)
\t\t)
\t\t(separators
\t\t\t(rows yes)
\t\t\t(cols yes)
\t\t)
\t\t(cells
\t\t\t(table_cell "X"
\t\t\t\t(start 0 0)
\t\t\t\t(end 10 5)
\t\t\t\t(margins 0 0 0 0)
\t\t\t\t(span 1 1)
\t\t\t\t(effects
\t\t\t\t\t(font
\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
"""
        )
        fp = Footprint.from_string(sexpr)
        assert fp.tables[0].cells[0].attributes is not None

    def test_table_border_with_stroke(self) -> None:
        sexpr = _make(
            """\
\t(table (column_count 1)
\t\t(border
\t\t\t(external yes)
\t\t\t(header no)
\t\t\t(stroke (width 0.1) (type solid))
\t\t)
\t\t(separators
\t\t\t(rows yes)
\t\t\t(cols yes)
\t\t\t(stroke (width 0.05) (type default))
\t\t)
\t\t(cells
\t\t\t(table_cell "X"
\t\t\t\t(start 0 0)
\t\t\t\t(end 10 5)
\t\t\t\t(margins 0 0 0 0)
\t\t\t\t(span 1 1)
\t\t\t)
\t\t)
\t)
"""
        )
        fp = Footprint.from_string(sexpr)
        assert fp.tables[0].border.stroke is not None
        assert fp.tables[0].separators.stroke is not None
        out = fp.to_string()
        fp2 = Footprint.from_string(out)
        assert fp2.tables[0].border.stroke is not None


# ---------------------------------------------------------------------------
# ReferenceImage
# ---------------------------------------------------------------------------

# KiCad 10.0.3 quoted-chunk grammar (FormatStreamData).
_IMAGE = _make(
    """\
\t(image (at 1.0 2.0)
\t\t(layer "F.Cu")
\t\t(scale 2.5)
\t\t(locked yes)
\t\t(data "SGVsbG8=")
\t\t(uuid "img-uuid-1")
\t)
"""
)

# Legacy single pipe-delimited blob, accepted on read for tolerance.
_IMAGE_LEGACY_PIPE = _make(
    """\
\t(image (at 1.0 2.0)
\t\t(layer "F.Cu")
\t\t(data |SGVsbG8=|)
\t\t(uuid "img-uuid-1")
\t)
"""
)

# A payload longer than one 76-char MIME line, to exercise the chunk boundary.
_LONG_B64 = "A" * 76 + "B" * 76 + "C" * 20
_IMAGE_LONG = _make(
    f"""\
\t(image (at 0 0)
\t\t(layer "F.Cu")
\t\t(data "{"A" * 76}" "{"B" * 76}" "{"C" * 20}")
\t)
"""
)


class TestReferenceImage:
    def test_image_parses(self) -> None:
        fp = Footprint.from_string(_IMAGE)
        assert len(fp.images) == 1
        img = fp.images[0]
        assert img.position == (1.0, 2.0)
        assert img.layer == "F.Cu"
        assert img.scale == pytest.approx(2.5)
        assert img.locked is True
        assert img.data == "SGVsbG8="
        assert img.id == "img-uuid-1"

    def test_image_round_trip(self) -> None:
        fp1 = Footprint.from_string(_IMAGE)
        fp2 = _round_trip(_IMAGE)
        assert fp1.images[0] == fp2.images[0]

    def test_image_emits_quoted_chunk_not_pipe(self) -> None:
        fp = Footprint.from_string(_IMAGE)
        out = fp.to_string()
        assert '(data "SGVsbG8=")' in out
        assert "|" not in out

    def test_image_legacy_pipe_accepted_on_read(self) -> None:
        fp = Footprint.from_string(_IMAGE_LEGACY_PIPE)
        assert fp.images[0].data == "SGVsbG8="

    def test_image_legacy_pipe_reemits_quoted_form(self) -> None:
        fp = Footprint.from_string(_IMAGE_LEGACY_PIPE)
        out = fp.to_string()
        assert '(data "SGVsbG8=")' in out
        assert "|" not in out

    def test_image_long_data_chunk_boundary(self) -> None:
        fp = Footprint.from_string(_IMAGE_LONG)
        assert fp.images[0].data == _LONG_B64
        out = fp.to_string()
        # Concatenation of the three quoted chunks reproduces the payload.
        assert f'"{"A" * 76}"' in out
        assert f'"{"C" * 20}"' in out
        fp2 = Footprint.from_string(out)
        assert fp2.images[0].data == _LONG_B64

    def test_image_empty_data(self) -> None:
        sexpr = _make(
            """\
\t(image (at 0 0)
\t\t(layer "F.Cu")
\t\t(data)
\t)
"""
        )
        fp = Footprint.from_string(sexpr)
        assert fp.images[0].data == ""
        out = fp.to_string()
        assert "(data)" in out

    def test_image_scale_in_output(self) -> None:
        fp = Footprint.from_string(_IMAGE)
        out = fp.to_string()
        assert "(scale 2.5)" in out

    def test_image_no_scale(self) -> None:
        sexpr = _make(
            """\
\t(image (at 0 0)
\t\t(layer "F.Cu")
\t\t(data "SGVsbG8=")
\t)
"""
        )
        fp = Footprint.from_string(sexpr)
        assert fp.images[0].scale is None
        out = fp.to_string()
        # scale must NOT appear when None
        assert "(scale" not in out


# ---------------------------------------------------------------------------
# Barcode
# ---------------------------------------------------------------------------

_BARCODE = _make(
    """\
\t(barcode
\t\t(at 5 5 0)
\t\t(layer "F.Cu")
\t\t(size 10 10)
\t\t(text "HELLO")
\t\t(text_height 1.0)
\t\t(type qr)
\t\t(ecc_level H)
\t\t(hide no)
\t\t(knockout no)
\t\t(margins 0.5 0.5)
\t\t(uuid "bc-uuid-1")
\t)
"""
)


class TestBarcode:
    def test_barcode_parses(self) -> None:
        fp = Footprint.from_string(_BARCODE)
        assert len(fp.barcodes) == 1
        bc = fp.barcodes[0]
        assert bc.position == (5.0, 5.0)
        assert bc.angle == 0.0
        assert bc.layer == "F.Cu"
        assert bc.size == (10.0, 10.0)
        assert bc.text == "HELLO"
        assert bc.text_height == pytest.approx(1.0)

    def test_barcode_type(self) -> None:
        fp = Footprint.from_string(_BARCODE)
        from eedesigncore.common.pcb_family.board_items import BarcodeKind

        assert fp.barcodes[0].barcode_type == BarcodeKind.qr

    def test_barcode_ecc(self) -> None:
        fp = Footprint.from_string(_BARCODE)
        from eedesigncore.common.pcb_family.board_items import BarcodeEcc

        assert fp.barcodes[0].ecc_level == BarcodeEcc.H

    def test_barcode_show_text(self) -> None:
        fp = Footprint.from_string(_BARCODE)
        assert fp.barcodes[0].show_text is True

    def test_barcode_margins(self) -> None:
        fp = Footprint.from_string(_BARCODE)
        assert fp.barcodes[0].margins == pytest.approx((0.5, 0.5))

    def test_barcode_uuid(self) -> None:
        fp = Footprint.from_string(_BARCODE)
        assert fp.barcodes[0].id == "bc-uuid-1"

    def test_barcode_round_trip(self) -> None:
        fp1 = Footprint.from_string(_BARCODE)
        fp2 = _round_trip(_BARCODE)
        assert fp1.barcodes[0] == fp2.barcodes[0]

    def test_barcode_code128_type(self) -> None:
        sexpr = _make(
            """\
\t(barcode
\t\t(at 0 0 0)
\t\t(layer "F.Cu")
\t\t(size 5 5)
\t\t(text "ABC")
\t\t(text_height 1.0)
\t\t(type code128)
\t\t(hide no)
\t\t(knockout no)
\t)
"""
        )
        fp = Footprint.from_string(sexpr)
        from eedesigncore.common.pcb_family.board_items import BarcodeKind

        assert fp.barcodes[0].barcode_type == BarcodeKind.code128

    def test_barcode_locked(self) -> None:
        sexpr = _make(
            """\
\t(barcode
\t\t(locked yes)
\t\t(at 0 0 45)
\t\t(layer "F.Cu")
\t\t(size 5 5)
\t\t(text "X")
\t\t(text_height 1.0)
\t\t(type qr)
\t\t(hide yes)
\t\t(knockout yes)
\t)
"""
        )
        fp = Footprint.from_string(sexpr)
        bc = fp.barcodes[0]
        assert bc.locked is True
        assert bc.angle == pytest.approx(45.0)
        assert bc.show_text is False
        assert bc.knockout is True


# ---------------------------------------------------------------------------
# PcbPoint
# ---------------------------------------------------------------------------

_PCB_POINT = _make(
    """\
\t(point (at 3.0 4.0)
\t\t(size 0.5)
\t\t(layer "F.Cu")
\t\t(uuid "pt-uuid-1")
\t)
"""
)


class TestPcbPoint:
    def test_point_parses(self) -> None:
        fp = Footprint.from_string(_PCB_POINT)
        assert len(fp.points) == 1
        pt = fp.points[0]
        assert pt.position == (3.0, 4.0)
        assert pt.size == pytest.approx(0.5)
        assert pt.layer == "F.Cu"
        assert pt.id == "pt-uuid-1"

    def test_point_round_trip(self) -> None:
        fp1 = Footprint.from_string(_PCB_POINT)
        fp2 = _round_trip(_PCB_POINT)
        assert fp1.points[0] == fp2.points[0]

    def test_point_in_output(self) -> None:
        fp = Footprint.from_string(_PCB_POINT)
        out = fp.to_string()
        assert "(point" in out
        assert "(at 3 4)" in out

    def test_bogus_point_token_raises(self) -> None:
        bad = _make(
            """\
\t(point (at 0 0)
\t\t(size 0.5)
\t\t(layer "F.Cu")
\t\t(bogus_point_field 1)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in point"):
            Footprint.from_string(bad)


# ---------------------------------------------------------------------------
# Variant
# ---------------------------------------------------------------------------

_VARIANT = _make(
    """\
\t(variant (name "Variant1")
\t\t(dnp yes)
\t\t(exclude_from_bom no)
\t\t(exclude_from_pos_files yes)
\t\t(field (name "MPN") (value "ABC-123"))
\t\t(field (name "Supplier") (value "Mouser"))
\t)
"""
)


class TestVariant:
    def test_variant_parses(self) -> None:
        fp = Footprint.from_string(_VARIANT)
        assert len(fp.variants) == 1
        v = fp.variants[0]
        assert v.name == "Variant1"
        assert v.dnp is True
        assert v.exclude_from_bom is False
        assert v.exclude_from_pos_files is True

    def test_variant_fields(self) -> None:
        fp = Footprint.from_string(_VARIANT)
        fields = fp.variants[0].fields
        assert len(fields) == 2
        assert fields[0].name == "MPN"
        assert fields[0].value == "ABC-123"
        assert fields[1].name == "Supplier"
        assert fields[1].value == "Mouser"

    def test_variant_round_trip(self) -> None:
        fp1 = Footprint.from_string(_VARIANT)
        fp2 = _round_trip(_VARIANT)
        assert fp1.variants[0] == fp2.variants[0]

    def test_variant_in_output(self) -> None:
        fp = Footprint.from_string(_VARIANT)
        out = fp.to_string()
        assert "(variant" in out
        assert "(dnp yes)" in out
        assert "(exclude_from_pos_files yes)" in out

    def test_bogus_variant_token_raises(self) -> None:
        bad = _make(
            """\
\t(variant (name "V1")
\t\t(bogus_variant_field 1)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in variant"):
            Footprint.from_string(bad)

    def test_bogus_variant_field_token_raises(self) -> None:
        bad = _make(
            """\
\t(variant (name "V1")
\t\t(field (name "X") (bogus_sub 1))
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in variant field"):
            Footprint.from_string(bad)


# ---------------------------------------------------------------------------
# ComponentClasses
# ---------------------------------------------------------------------------

_COMPONENT_CLASSES = _make(
    """\
\t(component_classes
\t\t(class "ClassA")
\t\t(class "ClassB")
\t)
"""
)


class TestComponentClasses:
    def test_component_classes_parses(self) -> None:
        fp = Footprint.from_string(_COMPONENT_CLASSES)
        assert fp.component_classes is not None
        assert len(fp.component_classes.classes) == 2
        assert "ClassA" in fp.component_classes.classes
        assert "ClassB" in fp.component_classes.classes

    def test_component_classes_round_trip(self) -> None:
        fp1 = Footprint.from_string(_COMPONENT_CLASSES)
        fp2 = _round_trip(_COMPONENT_CLASSES)
        assert fp1.component_classes == fp2.component_classes

    def test_component_classes_in_output(self) -> None:
        fp = Footprint.from_string(_COMPONENT_CLASSES)
        out = fp.to_string()
        assert "(component_classes" in out
        assert '(class "ClassA")' in out

    def test_no_component_classes_no_block(self) -> None:
        """When component_classes is None, block should not appear."""
        fp = Footprint.from_string(_HEAD + "\n)")
        out = fp.to_string()
        assert "(component_classes" not in out


# ---------------------------------------------------------------------------
# FootprintUnitInfo (gate-swap units)
# ---------------------------------------------------------------------------

_UNIT_INFO = _make(
    """\
\t(units
\t\t(unit (name "A") (pins "1" "2"))
\t\t(unit (name "B") (pins "3" "4"))
\t)
"""
)


class TestFootprintUnitInfo:
    def test_unit_info_parses(self) -> None:
        fp = Footprint.from_string(_UNIT_INFO)
        assert fp.unit_info is not None
        assert len(fp.unit_info.units) == 2
        assert fp.unit_info.units[0].name == "A"
        assert fp.unit_info.units[0].pins == ["1", "2"]
        assert fp.unit_info.units[1].name == "B"

    def test_unit_info_round_trip(self) -> None:
        fp1 = Footprint.from_string(_UNIT_INFO)
        fp2 = _round_trip(_UNIT_INFO)
        assert fp1.unit_info == fp2.unit_info

    def test_unit_info_in_output(self) -> None:
        fp = Footprint.from_string(_UNIT_INFO)
        out = fp.to_string()
        assert "(units" in out
        assert "(unit" in out


# ---------------------------------------------------------------------------
# Stackup
# ---------------------------------------------------------------------------

_STACKUP = _make(
    """\
\t(stackup
\t\t(layer "F.Cu")
\t\t(layer "B.Cu")
\t)
"""
)


class TestStackup:
    def test_stackup_parses(self) -> None:
        fp = Footprint.from_string(_STACKUP)
        assert fp.stackup is not None
        layers = fp.stackup.layers
        assert len(layers) == 2
        assert layers[0].name == "F.Cu"
        assert layers[1].name == "B.Cu"

    def test_stackup_round_trip(self) -> None:
        fp1 = Footprint.from_string(_STACKUP)
        fp2 = _round_trip(_STACKUP)
        assert fp1.stackup == fp2.stackup

    def test_stackup_in_output(self) -> None:
        fp = Footprint.from_string(_STACKUP)
        out = fp.to_string()
        assert "(stackup" in out
        assert '(layer "F.Cu")' in out

    def test_bogus_stackup_token_raises(self) -> None:
        bad = _make(
            """\
\t(stackup
\t\t(layer "F.Cu")
\t\t(bogus_stackup_token 1)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in stackup"):
            Footprint.from_string(bad)


# ---------------------------------------------------------------------------
# fp_filters round-trip (T9)
# ---------------------------------------------------------------------------

_FP_FILTERS = _make(
    """\
\t(property ki_fp_filters "CAP_* C_*")
"""
)


class TestFpFilters:
    def test_fp_filters_loaded(self) -> None:
        fp = Footprint.from_string(_FP_FILTERS)
        assert fp.fp_filters == "CAP_* C_*"

    def test_fp_filters_not_in_properties(self) -> None:
        fp = Footprint.from_string(_FP_FILTERS)
        for p in fp.properties:
            assert p.name != "ki_fp_filters"

    def test_fp_filters_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_FILTERS)
        fp2 = _round_trip(_FP_FILTERS)
        assert fp1.fp_filters == fp2.fp_filters

    def test_fp_filters_in_output(self) -> None:
        fp = Footprint.from_string(_FP_FILTERS)
        out = fp.to_string()
        assert "ki_fp_filters" in out
        assert "CAP_* C_*" in out
