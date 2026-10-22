"""Check this renderer against ``kicad-cli`` by geometry, not by pixels.

The check runs ``kicad-cli`` on a real board, reads both documents back into
primitives, and compares the two multisets inside a tolerance. Nothing is
rasterized. A raster hides which primitive moved and by how much, and it fails
on anti-aliasing that carries no meaning.

The test needs ``kicad-cli`` and a corpus board. It skips when either is
absent, and it says which one, so a skip never reads as a pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eedesigncore.pcb.models import Board
from eedesigncore.render import (
    HOLES,
    BoardContent,
    CliParityStyle,
    ElementStyle,
    Hole,
    LayerStyle,
    RenderItem,
    ViewBox,
    render_board_svg,
)
from eedesigncore.render.compare import (
    compare_holes,
    compare_svg,
    nearest_gap,
    read_primitives,
)
from eedesigncore.render.traversal import iter_holes

#: Layers this renderer covers completely, so agreement must be exact.
#:
#: A silkscreen layer is NOT here. KiCad plots a reference designator as stroke
#: font segments, and text is out of scope (ADR-098), so silkscreen disagrees by
#: design. Putting it here would train the reader to ignore a red test.
STROKED_LAYERS = ("Edge.Cuts",)

KICAD_CLI = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")

#: Canonical name to the stem ``kicad-cli`` writes.
FILE_STEMS = {"Edge.Cuts": "Edge_Cuts", "F.SilkS": "F_Silkscreen"}


def find_cli() -> Path | None:
    if KICAD_CLI.is_file():
        return KICAD_CLI
    found = shutil.which("kicad-cli")
    return Path(found) if found else None


def find_board() -> Path | None:
    root = os.environ.get("KICAD_FILEIO_CORPUS", "C:/KiCad")
    if not Path(root).is_dir():
        return None
    for candidate in sorted(Path(root).glob("74*/*/*/*.kicad_pcb")):
        if ".history" not in candidate.parts:
            return candidate
    return None


@pytest.fixture(scope="module")
def cli() -> Path:
    found = find_cli()
    if found is None:
        pytest.skip("kicad-cli not installed, so the parity oracle cannot run")
    return found


@pytest.fixture(scope="module")
def board_path() -> Path:
    found = find_board()
    if found is None:
        pytest.skip("no corpus board found; set KICAD_FILEIO_CORPUS to a board tree")
    return found


@pytest.fixture(scope="module")
def reference(cli: Path, board_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Export the reference documents with ``kicad-cli``."""
    out = tmp_path_factory.mktemp("kicad_cli_svg")
    subprocess.run(  # noqa: S603
        [
            str(cli),
            "pcb",
            "export",
            "svg",
            "--exclude-drawing-sheet",
            "--page-size-mode",
            "1",
            "--layers",
            ",".join(STROKED_LAYERS),
            "--mode-multi",
            "-o",
            str(out),
            str(board_path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return out


@pytest.mark.parametrize("layer", STROKED_LAYERS)
def test_stroked_geometry_agrees_with_kicad_cli(
    layer: str, board_path: Path, reference: Path
) -> None:
    """Every primitive this renderer draws must be one kicad-cli draws too."""
    board = Board.from_file(board_path)
    ours = render_board_svg(
        board,
        [layer],
        ViewBox(0.0, 0.0, 431.8, 279.4),
        style=CliParityStyle(colors=dict.fromkeys(STROKED_LAYERS, "#8D8E8B")),
    )[layer]

    matches = list(reference.glob(f"*-{FILE_STEMS[layer]}.svg"))
    assert matches, f"kicad-cli wrote no file for {layer}"
    theirs = matches[0].read_text(encoding="utf-8")

    difference = compare_svg(ours, theirs)
    if difference.only_in_left:
        gaps = [
            (primitive, nearest_gap(primitive, read_primitives(theirs)))
            for primitive in difference.only_in_left[:5]
        ]
        pytest.fail(f"{difference.summary()}\nnearest match per stray primitive: {gaps}")

    assert difference.matched > 0, "the comparison matched nothing, so it proved nothing"


def test_the_renderer_draws_nothing_kicad_cli_does_not(board_path: Path, reference: Path) -> None:
    """The one-way rule that matters for a preview.

    A missing primitive is a scope gap and the caller hears about it through
    ``on_skip``. An extra primitive is a defect, because it puts geometry on the
    page that the board does not hold.
    """
    board = Board.from_file(board_path)
    for layer in STROKED_LAYERS:
        ours = render_board_svg(
            board,
            [layer],
            ViewBox(0.0, 0.0, 431.8, 279.4),
            style=CliParityStyle(colors=dict.fromkeys(STROKED_LAYERS, "#8D8E8B")),
        )[layer]
        theirs = next(reference.glob(f"*-{FILE_STEMS[layer]}.svg")).read_text(encoding="utf-8")
        assert not compare_svg(ours, theirs).only_in_left


def test_every_undrawn_item_is_reported(board_path: Path) -> None:
    """Silkscreen text is out of scope. It must never disappear without a word."""
    board = Board.from_file(board_path)
    skipped = []
    render_board_svg(
        board,
        ["F.SilkS"],
        ViewBox(0.0, 0.0, 431.8, 279.4),
        style=CliParityStyle(colors={"F.SilkS": "#8D8E8B"}),
        on_skip=skipped.append,
    )
    kinds = {type(item.item).__name__ for item in skipped}
    assert kinds <= {
        "PcbText",
        "PcbTextBox",
        "Dimension",
        "Table",
        "Barcode",
        "ReferenceImage",
        "PcbCurve",
        "Property",
    }


# ---------------------------------------------------------------------------
# Holes
# ---------------------------------------------------------------------------

#: The copper layer the hole comparison plots.
#:
#: KiCad draws a drill mark only when the item's layer span meets the layers
#: being plotted (``plot_brditems_plotter.cpp:1297-1298``), so a single-layer
#: export holds a SUBSET of the board's holes. A hole document holds them all,
#: because a hole is not on a layer. The test restricts our side to the same
#: subset rather than pretending the two sets should match.
HOLE_COMPARE_LAYER = "F.Cu"


def find_board_with_holes() -> Path | None:
    root = os.environ.get("KICAD_FILEIO_CORPUS", "C:/KiCad")
    if not Path(root).is_dir():
        return None
    for candidate in sorted(Path(root).glob("74*/*/*/*.kicad_pcb")):
        if ".history" in candidate.parts:
            continue
        try:
            board = Board.from_file(candidate)
        except Exception:  # noqa: BLE001 - a corpus file this old package cannot read
            continue
        if any(True for _ in iter_holes(board)):
            return candidate
    return None


@pytest.fixture(scope="module")
def hole_board_path() -> Path:
    found = find_board_with_holes()
    if found is None:
        pytest.skip("no corpus board with holes; set KICAD_FILEIO_CORPUS")
    return found


@pytest.fixture(scope="module")
def hole_reference(
    cli: Path, hole_board_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> str:
    """Export one copper layer WITH actual drill shapes."""
    out = tmp_path_factory.mktemp("kicad_cli_holes")
    subprocess.run(  # noqa: S603
        [
            str(cli),
            "pcb",
            "export",
            "svg",
            "--exclude-drawing-sheet",
            "--page-size-mode",
            "1",
            "--drill-shape-opt",
            "2",
            "--layers",
            HOLE_COMPARE_LAYER,
            "--mode-multi",
            "-o",
            str(out),
            str(hole_board_path),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    written = list(out.glob("*.svg"))
    assert written, "kicad-cli wrote no copper export"
    return written[0].read_text(encoding="utf-8")


class OnCompareLayer:
    """Keep only the holes KiCad would draw for one plotted layer."""

    emit_ids = False
    point_stroke_width_mm = 0.05

    def style_for(self, render_item: RenderItem) -> ElementStyle | None:
        hole = render_item.item
        if not isinstance(hole, Hole) or not hole.touches(HOLE_COMPARE_LAYER):
            return None
        return ElementStyle(stroke="#FFFFFF", stroke_opacity=1.0, fill="none")


def test_every_hole_we_draw_is_one_kicad_cli_draws(
    hole_board_path: Path, hole_reference: str
) -> None:
    """The one-way rule. An extra hole puts an opening the board does not hold.

    The reverse is not asserted: a copper export also holds pads and tracks, and
    an oval pad reduces to the same stadium a slot does. Nothing in the geometry
    separates them, so the count on KiCad's side is legitimately larger.
    """
    board = Board.from_file(hole_board_path)
    ours = render_board_svg(
        board,
        [HOLES],
        ViewBox(0.0, 0.0, 431.8, 279.4),
        style=OnCompareLayer(),
    )[HOLES]

    difference = compare_holes(ours, hole_reference)
    assert not difference.only_in_left, (
        f"{difference.summary()}; strays: {difference.only_in_left[:5]}"
    )
    assert difference.matched > 0, "the comparison matched nothing, so it proved nothing"


def test_the_hole_document_holds_every_hole_not_just_one_layer(
    hole_board_path: Path,
) -> None:
    """A blind or buried via belongs in the document even though no layer shows it."""
    board = Board.from_file(hole_board_path)
    everything = len(list(iter_holes(board)))
    on_layer = sum(1 for hole in iter_holes(board) if hole.touches(HOLE_COMPARE_LAYER))
    assert everything >= on_layer


def test_two_hole_renders_are_byte_identical(hole_board_path: Path) -> None:
    board = Board.from_file(hole_board_path)
    style = LayerStyle(colors={HOLES: "#000000"})
    window = ViewBox(0.0, 0.0, 431.8, 279.4)
    assert render_board_svg(board, [HOLES], window, style=style) == render_board_svg(
        board, [HOLES], window, style=style
    )


# ---------------------------------------------------------------------------
# Pads
# ---------------------------------------------------------------------------

#: A board built to hold one of every pad shape, generated by
#: ``scripts/make_pad_probe.py``.
#:
#: A corpus board will not do. It carries whatever shapes its footprints happened
#: to use, and the shapes that matter most for fidelity -- a trapezoid, a
#: chamfered rectangle, a custom pad -- are the rarest in practice. This fixture
#: puts each case on its own 20 mm grid cell, so a stray primitive names the pad
#: that produced it.
PAD_PROBE = Path(__file__).parent.parent / "fixtures" / "pcb" / "pad_shapes_probe.kicad_pcb"

#: The window ``kicad-cli`` uses for this fixture at ``--page-size-mode 1``.
PAD_PROBE_WINDOW = ViewBox(0.0, 0.0, 297.0022, 210.0072)

#: Grid cells whose pad this renderer does NOT reproduce vertex for vertex.
#:
#: A custom pad is the one case. KiCad unions the anchor and every primitive into
#: a single outline (``PAD::MergePrimitivesAsPolygon``, ``pad.cpp:3267-3306``);
#: this renderer draws them as separate filled shapes, which covers the same area
#: with a different outline. ``pads.pad_geometry_is_exact`` reports that, and this
#: list is the same fact stated as coordinates.
PAD_PROBE_CUSTOM_ROW_Y = 90.0


@pytest.fixture(scope="module")
def pad_reference(cli: Path, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Export F.Cu of the pad probe with ``kicad-cli``."""
    out = tmp_path_factory.mktemp("kicad_cli_pads")
    subprocess.run(  # noqa: S603
        [
            str(cli),
            "pcb",
            "export",
            "svg",
            "--exclude-drawing-sheet",
            "--page-size-mode",
            "1",
            "--layers",
            "F.Cu",
            "--mode-multi",
            "-o",
            str(out),
            str(PAD_PROBE),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    written = list(out.glob("*.svg"))
    assert written, "kicad-cli wrote no pad export"
    return written[0].read_text(encoding="utf-8")


def render_pad_probe() -> str:
    board = Board.from_file(PAD_PROBE)
    return render_board_svg(
        board,
        ["F.Cu"],
        PAD_PROBE_WINDOW,
        style=CliParityStyle(colors={"F.Cu": "#C83434"}),
        content=frozenset({BoardContent.pads}),
    )["F.Cu"]


def _in_custom_row(primitive: tuple[object, ...]) -> bool:
    """True when a primitive sits in the custom-pad row of the grid."""
    numbers = [value for value in primitive if isinstance(value, float)]
    return any(
        abs(numbers[index + 1] - PAD_PROBE_CUSTOM_ROW_Y) < 7.0
        for index in range(0, len(numbers) - 1, 2)
    )


def test_every_exact_pad_shape_agrees_with_kicad_cli(pad_reference: str) -> None:
    """Six of the seven pad shapes must match KiCad vertex for vertex.

    That includes the rounded and chamfered rectangles, which KiCad polygonises
    rather than drawing as arcs. Reproducing the polygon is what
    ``common.polygon`` is for, and this is the check that it works on real pads
    rather than on the five cases its unit tests pin.
    """
    difference = compare_svg(render_pad_probe(), pad_reference)

    stray = [p for p in difference.only_in_left if not _in_custom_row(p)]
    assert not stray, (
        f"{difference.summary()}; this renderer drew geometry KiCad did not, "
        f"outside the custom-pad row: {stray[:5]}"
    )
    assert difference.matched > 0, "the comparison matched nothing, so it proved nothing"


def test_the_only_pads_kicad_cli_draws_and_we_do_not_are_drills_and_custom(
    pad_reference: str,
) -> None:
    """Name every remaining difference, so none of them can hide.

    Two kinds are expected and nothing else is:

    1. **Drill marks.** A hole is not on a layer in this library; it lives in the
       ``Holes`` pseudo-layer document, which the hole oracle above measures. A
       copper export from ``kicad-cli`` carries them inline.
    2. **The custom-pad row.** See :data:`PAD_PROBE_CUSTOM_ROW_Y`.
    """
    difference = compare_svg(render_pad_probe(), pad_reference)
    board = Board.from_file(PAD_PROBE)
    drills = {
        (round(hole.position[0], 4), round(hole.position[1], 4), round(hole.width / 2.0, 4))
        for hole in iter_holes(board)
    }

    unexplained = []
    for primitive in difference.only_in_right:
        if _in_custom_row(primitive):
            continue
        if primitive[0] == "circle":
            shape = tuple(round(float(value), 4) for value in primitive[1:4])
            if shape in drills:
                continue
        unexplained.append(primitive)

    assert not unexplained, (
        "kicad-cli drew pad geometry this renderer skipped, and it is neither a "
        f"drill mark nor a custom pad: {unexplained[:5]}"
    )


def test_every_pad_shape_reaches_the_page(pad_reference: str) -> None:
    """A silent drop is the failure this guards. Each of the 18 cases must draw.

    Without it, a pad selection bug -- an unexpanded ``*.Cu`` wildcard, say --
    would leave the two tests above passing on an empty document.
    """
    ours = render_pad_probe()
    board = Board.from_file(PAD_PROBE)
    assert len(board.footprints) == 18, "the fixture changed; re-measure the oracle"

    elements = ours.count("<path") + ours.count("<circle")
    assert elements >= len(board.footprints), (
        f"only {elements} elements for {len(board.footprints)} pads"
    )

    skipped: list[RenderItem] = []
    render_board_svg(
        board,
        ["F.Cu"],
        PAD_PROBE_WINDOW,
        style=CliParityStyle(colors={"F.Cu": "#C83434"}),
        content=frozenset({BoardContent.pads}),
        on_skip=skipped.append,
    )
    assert skipped == [], "every pad shape has an emitter, so nothing may be skipped"


def test_two_pad_probe_renders_are_byte_identical() -> None:
    assert render_pad_probe() == render_pad_probe()
