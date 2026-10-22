"""GAP-010: pad custom-primitive gr_vector / gr_bbox proxies (T11).

Proxy primitives (format(PAD*) :1954 / :1969) have ZERO corpus coverage; these
synthetic fixtures are the regression net. gr_vector is a proxy SEGMENT (no
width, no fill); gr_bbox is a proxy RECTANGLE (no width, fill emitted).
"""

from __future__ import annotations

from eedesigncore.mod.enums import PadShape
from eedesigncore.mod.models import Footprint, PrimBbox, PrimVector


def _fixture() -> str:
    return (
        '(footprint "Test"\n'
        "\t(version 20260206)\n"
        '\t(generator "test")\n'
        '\t(layer "F.Cu")\n'
        "\t(duplicate_pad_numbers_are_jumpers no)\n"
        '\t(pad "1" smd custom\n'
        "\t\t(at 0 0)\n"
        "\t\t(size 0.5 0.5)\n"
        '\t\t(layers "F.Cu")\n'
        "\t\t(options\n"
        "\t\t\t(clearance outline)\n"
        "\t\t\t(anchor circle)\n"
        "\t\t)\n"
        "\t\t(primitives\n"
        "\t\t\t(gr_vector\n"
        "\t\t\t\t(start 0 0)\n"
        "\t\t\t\t(end 1 0)\n"
        "\t\t\t)\n"
        "\t\t\t(gr_bbox\n"
        "\t\t\t\t(start -1 -1)\n"
        "\t\t\t\t(end 1 1)\n"
        "\t\t\t\t(fill no)\n"
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
        "\t(embedded_fonts no)\n"
        ")\n"
    )


class TestProxyPrimitiveLoad:
    def test_gr_vector_and_gr_bbox_parsed(self) -> None:
        fp = Footprint.from_string(_fixture())
        prims = fp.pads[0].primitives
        assert prims is not None
        assert isinstance(prims[0], PrimVector)
        assert prims[0].start == (0.0, 0.0)
        assert prims[0].end == (1.0, 0.0)
        assert isinstance(prims[1], PrimBbox)
        assert prims[1].start == (-1.0, -1.0)
        assert prims[1].end == (1.0, 1.0)
        assert prims[1].fill is False


class TestProxyPrimitiveRoundTrip:
    def test_byte_identical(self) -> None:
        fp = Footprint.from_string(_fixture())
        assert fp.to_string() == _fixture()

    def test_gr_bbox_fill_yes_round_trip(self) -> None:
        text = _fixture().replace("(fill no)", "(fill yes)")
        fp = Footprint.from_string(text)
        assert fp.pads[0].primitives is not None
        assert fp.pads[0].primitives[1].fill is True  # type: ignore[union-attr]
        assert fp.to_string() == text


class TestProxyPrimitiveEmit:
    def test_gr_vector_has_no_width_or_fill(self) -> None:
        fp = Footprint(
            fp_id="T",
            generator="test",
            pads=[
                _custom_pad([PrimVector(start=(0.0, 0.0), end=(1.0, 0.0))]),
            ],
        )
        out = fp.to_string()
        assert "(gr_vector" in out
        assert "(width" not in out.split("(gr_vector", 1)[1].split(")")[0]

    def test_gr_bbox_emits_fill_no_width(self) -> None:
        fp = Footprint(
            fp_id="T",
            generator="test",
            pads=[_custom_pad([PrimBbox(start=(0.0, 0.0), end=(1.0, 1.0), fill=True)])],
        )
        out = fp.to_string()
        assert "(gr_bbox" in out
        bbox = out.split("(gr_bbox", 1)[1]
        assert "(fill yes)" in bbox
        assert "(width" not in bbox.split("(fill", 1)[0]


def _custom_pad(prims: list) -> object:  # noqa: ANN001
    from eedesigncore.mod.models import Pad

    return Pad(
        number="1",
        shape=PadShape.custom,
        layers=["F.Cu"],
        primitives=prims,
    )
