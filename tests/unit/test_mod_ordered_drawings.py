"""Ordered GraphicalItems drawings stream tests (GAP-001 / T11).

KiCad emits dimension/table/image/barcode INTERLEAVED with fp_* shapes in one
sorted drawings loop BEFORE pads/zones/groups (format(FOOTPRINT*) :1425-1439),
not trailing after (model). These items have ZERO corpus coverage; synthetic
interleaved strings are the regression net.
"""

from __future__ import annotations

from eedesigncore.common.pcb_family import Barcode, Dimension, ReferenceImage, Table
from eedesigncore.common.pcb_family.shapes import PcbLine, PcbRect
from eedesigncore.mod.models import Footprint

# fp_line, dimension, fp_rect, barcode, image, table interleaved.
_INTERLEAVED = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(fp_line
\t\t(start 0 0)
\t\t(end 1 1)
\t\t(stroke
\t\t\t(width 0.1)
\t\t\t(type solid)
\t\t)
\t\t(layer "F.SilkS")
\t\t(uuid "line-1")
\t)
\t(dimension
\t\t(type aligned)
\t\t(layer "Dwgs.User")
\t\t(uuid "dim-1")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 5 0)
\t\t)
\t\t(height 1)
\t\t(style
\t\t\t(thickness 0.15)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t)
\t)
\t(fp_rect
\t\t(start 0 0)
\t\t(end 2 2)
\t\t(stroke
\t\t\t(width 0.1)
\t\t\t(type solid)
\t\t)
\t\t(fill no)
\t\t(layer "F.SilkS")
\t\t(uuid "rect-1")
\t)
\t(barcode
\t\t(at 3 3)
\t\t(layer "F.SilkS")
\t\t(size 2 2)
\t\t(text "ABC")
\t\t(text_height 1)
\t\t(type code128)
\t\t(hide no)
\t\t(knockout no)
\t\t(uuid "bc-1")
\t)
\t(image
\t\t(at 4 4)
\t\t(layer "F.Cu")
\t\t(data |SGVsbG8=|)
\t\t(uuid "img-1")
\t)
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
)
"""


class TestOrderedStream:
    def test_interleaved_order_preserved_on_load(self) -> None:
        fp = Footprint.from_string(_INTERLEAVED)
        kinds = [type(d).__name__ for d in fp.drawings]
        assert kinds == ["PcbLine", "Dimension", "PcbRect", "Barcode", "ReferenceImage"]

    def test_emitted_order_is_the_kicad_type_rank(self) -> None:
        """The one drawings loop is SORTED, so emission order is the KICAD_T rank.

        Ranks (``include/core/typeinfo.h:86-113``): ``PCB_SHAPE_T`` 5,
        ``PCB_REFERENCE_IMAGE_T`` 6, ``PCB_BARCODE_T`` 18 and
        ``PCB_DIM_ALIGNED_T`` 19. Inside the shapes, both sit on ``F.SilkS`` and
        ``SHAPE_T::SEGMENT`` (0) precedes ``RECTANGLE`` (1).
        """
        out = Footprint.from_string(_INTERLEAVED).to_string()
        positions = [out.index(token) for token in ("(fp_line", "(fp_rect", "(image", "(barcode")]
        positions.append(out.index("(dimension"))
        assert positions == sorted(positions)

    def test_idempotent_byte_round_trip(self) -> None:
        # Canonicalize once (input hand-formatting may differ), then re-emit must
        # be byte-identical: the ordered stream is stable.
        canonical = Footprint.from_string(_INTERLEAVED).to_string()
        assert Footprint.from_string(canonical).to_string() == canonical

    def test_special_items_emit_before_pads_and_embedded(self) -> None:
        out = Footprint.from_string(_INTERLEAVED).to_string()
        # dimension/barcode/image sit in the drawings position, in the one sorted
        # drawings loop and BEFORE (embedded_fonts) — not trailing after (model).
        for token in ("(dimension", "(barcode", "(image", "(fp_line", "(fp_rect"):
            assert out.index(token) < out.index("(embedded_fonts")

    def test_no_trailing_emit_after_models(self) -> None:
        # There is no model here; the special items must not reappear at the tail.
        out = Footprint.from_string(_INTERLEAVED).to_string()
        assert out.count("(dimension") == 1
        assert out.count("(barcode") == 1
        assert out.count("(image") == 1


class TestCompatibilityViews:
    def test_filtered_views_read_from_drawings(self) -> None:
        fp = Footprint.from_string(_INTERLEAVED)
        assert len(fp.dimensions) == 1
        assert len(fp.barcodes) == 1
        assert len(fp.images) == 1
        assert len(fp.tables) == 0
        assert isinstance(fp.dimensions[0], Dimension)
        assert isinstance(fp.barcodes[0], Barcode)
        assert isinstance(fp.images[0], ReferenceImage)

    def test_drawings_is_single_source_of_truth(self) -> None:
        fp = Footprint.from_string(_INTERLEAVED)
        special_kinds = (Dimension, Table, ReferenceImage, Barcode)
        shapes = [d for d in fp.drawings if isinstance(d, PcbLine | PcbRect)]
        specials = [d for d in fp.drawings if isinstance(d, special_kinds)]
        assert len(shapes) == 2
        assert len(specials) == 3
