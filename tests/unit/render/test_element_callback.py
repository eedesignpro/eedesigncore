"""Rendered fragments retain their item associations without changing pages."""

import xml.etree.ElementTree as ET

import pytest

from eedesigncore import (
    ALL_CONTENT,
    Board,
    Footprint,
    LayerStyle,
    PcbText,
    RenderItem,
    TextAttributes,
    TrackSegment,
    ViewBox,
    render_board_svg,
    render_footprint_svg,
)

WINDOW = ViewBox(0, 0, 100, 100)
STYLE = LayerStyle({}, default_color="#ffffff")


def test_fragments_preserve_order_identity_and_page_bytes():
    text = PcbText(
        content="AB", position=(2, 3), layer="F.Fab", attributes=TextAttributes(font_size=(1, 1))
    )
    footprint = Footprint(fp_id="Test:Item", drawings=[text])
    track = TrackSegment(start=(0, 0), end=(5, 0), width=0.2, layer="F.Cu", net="GND")
    board = Board(footprints=[footprint], tracks=[track])
    before = board.model_dump()
    layers = ["F.Fab", "F.Cu", "F.Fab", "B.Fab"]
    baseline = render_board_svg(board, layers, WINDOW, style=STYLE, content=ALL_CONTENT)
    calls: list[tuple[RenderItem, str]] = []
    pages = render_board_svg(
        board,
        layers,
        WINDOW,
        style=STYLE,
        content=ALL_CONTENT,
        on_element=lambda item, fragment: calls.append((item, fragment)),
    )
    assert pages == baseline
    assert board.model_dump() == before
    for layer, page in pages.items():
        expected = list(ET.fromstring(page))[0]
        fragments = [ET.fromstring(fragment) for item, fragment in calls if item.layer == layer]
        assert len(fragments) == len(expected)
        for actual, saved in zip(fragments, expected, strict=True):
            assert actual.tag == saved.tag.rsplit("}", 1)[-1]
            assert actual.attrib == saved.attrib
    text_calls = [item for item, _ in calls if item.layer == "F.Fab"]
    assert len(text_calls) > 1
    assert all(item.item is text and item.footprint is footprint for item in text_calls)
    assert all(item.uuid is None for item in text_calls)
    track_calls = [item for item, _ in calls if item.layer == "F.Cu"]
    assert len(track_calls) == 1
    assert track_calls[0].item is track
    assert track_calls[0].item.net == "GND"


def test_callback_exception_is_not_swallowed():
    board = Board(tracks=[TrackSegment(start=(0, 0), end=(1, 0), width=0.2, layer="F.Cu")])

    def fail(item, fragment):
        raise RuntimeError("consumer stopped")

    with pytest.raises(RuntimeError, match="consumer stopped"):
        render_board_svg(
            board, ["F.Cu"], WINDOW, style=STYLE, content=ALL_CONTENT, on_element=fail
        )


def test_style_filtered_items_do_not_emit_callbacks():
    class HiddenStyle:
        emit_ids = False
        point_stroke_width_mm = 0.05

        def style_for(self, item):
            return None

    board = Board(tracks=[TrackSegment(start=(0, 0), end=(1, 0), width=0.2, layer="F.Cu")])
    calls = []
    render_board_svg(
        board,
        ["F.Cu"],
        WINDOW,
        style=HiddenStyle(),
        content=ALL_CONTENT,
        on_element=lambda *args: calls.append(args),
    )
    assert calls == []


def test_footprint_export_forwards_callback():
    footprint = Footprint(
        fp_id="Test:Item",
        drawings=[
            PcbText(
                content="X",
                position=(2, 3),
                layer="F.Fab",
                attributes=TextAttributes(font_size=(1, 1)),
            )
        ],
    )
    calls = []
    pages = render_footprint_svg(
        footprint,
        layers=["F.Fab"],
        view_box=WINDOW,
        style=STYLE,
        on_element=lambda item, fragment: calls.append((item, fragment)),
    )
    assert calls
    assert all(
        item.footprint is footprint and fragment in pages[item.layer] for item, fragment in calls
    )
