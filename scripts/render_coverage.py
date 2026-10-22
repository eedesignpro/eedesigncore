"""Grade the SVG renderer against what KiCad's own board plot draws.

This is an AUDIT, not a test. It reads KiCad's plot sources and this library's
renderer, and reports what one covers of the other. Nothing here is hand-written
knowledge about the answer: every expected item comes out of the KiCad clone, and
every covered item comes out of this package's code. Run it after a KiCad version
bump and the numbers move on their own.

Four questions, four axes:

1. **Item types.** ``BRDITEMS_PLOTTER::PlotBoardGraphicItem`` is a switch on
   ``KICAD_T``. Every ``case PCB_*_T:`` in it is an item type a board plot draws.
   Read against the types :func:`eedesigncore.render.geometry.shapes_for`
   dispatches on.
2. **Content streams.** ``PlotStandardLayer`` walks the board's separate streams
   in turn: drawings, footprint text, footprint graphics, footprint pads, then
   tracks, vias, zones and drill marks. Read against
   :class:`~eedesigncore.render.traversal.BoardContent` and the hole document.
3. **Enum members.** A covered item type can still miss a case. ``PAD_SHAPE``,
   ``PAD_ATTRIB``, ``PAD_DRILL_SHAPE`` and ``SHAPE_T`` are read out of the KiCad
   headers and compared against the members the renderer branches on.
4. **Known limits.** Cases the renderer reaches but cannot reproduce exactly.
   Each one carries a source citation, and the script VERIFIES the citation still
   says what it claims.

The anti-staleness rule, which is the point of the whole file: every KiCad case
must appear in a mapping table below with either a library type or a stated
reason. A case KiCad grows that nobody has classified makes this script EXIT
NON-ZERO. Coverage can therefore never quietly drift as KiCad moves.

Usage::

    .venv/Scripts/python.exe scripts/render_coverage.py
    .venv/Scripts/python.exe scripts/render_coverage.py --json
    .venv/Scripts/python.exe scripts/render_coverage.py --check
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "eedesigncore"

#: Where the pinned KiCad clone lives. Overridable, because a fresh clone of this
#: repo will not have it at the same absolute path.
DEFAULT_KICAD_ROOT = Path("kicad-source/kicad")
KICAD_ROOT_ENV = "KICAD_FILEIO_KICAD_SOURCE"


# ---------------------------------------------------------------------------
# The mapping table. The ONLY hand-written knowledge in this file.
# ---------------------------------------------------------------------------

#: Every ``KICAD_T`` a board plot can draw, mapped to how this library treats it.
#:
#: A value that names a class is a claim the audit CHECKS: the class must exist
#: and ``shapes_for`` must dispatch on it. A value of ``None`` is a stated gap,
#: and *GAP_REASONS* must carry its reason.
KICAD_TYPE_TO_MODEL: dict[str, str | None] = {
    "PCB_SHAPE_T": "PcbLine",  # one token, six SHAPE_T shapes; axis 3 grades those
    "PCB_TEXT_T": "PcbText",
    "PCB_TEXTBOX_T": "PcbTextBox",
    "PCB_BARCODE_T": "Barcode",
    "PCB_TABLE_T": "Table",
    "PCB_DIM_ALIGNED_T": "Dimension",
    "PCB_DIM_CENTER_T": "Dimension",
    "PCB_DIM_RADIAL_T": "Dimension",
    "PCB_DIM_ORTHOGONAL_T": "Dimension",
    "PCB_DIM_LEADER_T": "Dimension",
    "PCB_TARGET_T": "PcbTarget",
}

#: Why a mapped-to-``None`` type is not drawn. Every key must be a ``None`` above.
GAP_REASONS: dict[str, str] = {}

#: The streams ``PlotStandardLayer`` walks, and the line in it that proves each.
#:
#: The audit reads each cited line and fails if the text no longer matches, so a
#: refactor upstream cannot leave this list quietly wrong.
PLOT_STREAMS: dict[str, tuple[str, str]] = {
    "drawings": ("plot_board_layers.cpp", "for( const BOARD_ITEM* item : aBoard->Drawings() )"),
    "footprint_text": ("plot_board_layers.cpp", "itemplotter.PlotFootprintTextItems( footprint )"),
    "footprint_graphics": (
        "plot_board_layers.cpp",
        "itemplotter.PlotFootprintGraphicItems( footprint )",
    ),
    "pads": ("plot_board_layers.cpp", "for( PAD* pad : footprint->Pads() )"),
    "vias": ("plot_board_layers.cpp", "aPlotter->FlashPadCircle( via->GetStart(), diameter,"),
    "tracks": ("plot_board_layers.cpp", "// Plot tracks (not vias) :"),
    "zones": ("plot_board_layers.cpp", "for( const ZONE* zone : aBoard->Zones() )"),
    "drill_marks": ("plot_board_layers.cpp", "itemplotter.PlotDrillMarks();"),
}

#: How each stream is covered here, and by what.
STREAM_COVERAGE: dict[str, str] = {
    "drawings": "BoardContent.drawings",
    "footprint_text": "BoardContent.drawings (footprint properties)",
    "footprint_graphics": "BoardContent.drawings (footprint drawings)",
    "pads": "BoardContent.pads",
    "vias": "BoardContent.tracks (a via is a track item)",
    "tracks": "BoardContent.tracks",
    "zones": "BoardContent.zones",
    "drill_marks": "the Holes pseudo-layer document",
}

#: Enums to grade, as (header, enum name, the renderer member set to compare).
ENUM_AXES: tuple[tuple[str, str, str], ...] = (
    ("pcbnew/padstack.h", "PAD_SHAPE", "pad_shapes"),
    ("pcbnew/padstack.h", "PAD_ATTRIB", "pad_types"),
    ("pcbnew/padstack.h", "PAD_DRILL_SHAPE", "drill_shapes"),
    ("include/eda_shape.h", "SHAPE_T", "shape_types"),
)

#: KiCad enum member to this library's enum value, per axis.
#:
#: KiCad spells an enum in SCREAMING_CASE and the file format spells it in
#: lowercase tokens, and the two do not always agree: ``RECTANGLE`` is ``rect``
#: on disk, and ``CHAMFERED_RECT`` has no token of its own at all.
ENUM_MEMBER_TO_TOKEN: dict[str, dict[str, str | None]] = {
    "pad_shapes": {
        "CIRCLE": "circle",
        "RECTANGLE": "rect",
        "OVAL": "oval",
        "TRAPEZOID": "trapezoid",
        "ROUNDRECT": "roundrect",
        # No token of its own: KiCad writes `roundrect` and promotes the shape when
        # a (chamfer ...) list is present (pcb_io_kicad_sexpr_parser.cpp:6240-6241).
        "CHAMFERED_RECT": "roundrect",
        "CUSTOM": "custom",
    },
    "pad_types": {
        "PTH": "thru_hole",
        "SMD": "smd",
        "CONN": "connect",
        "NPTH": "np_thru_hole",
    },
    "drill_shapes": {
        "UNDEFINED": None,  # never written; a pad with no drill carries no shape
        "CIRCLE": "circle",
        "OBLONG": "oval",
    },
    "shape_types": {
        "UNDEFINED": None,  # not a shape, an unset marker
        "SEGMENT": "PcbLine",
        "RECTANGLE": "PcbRect",
        "ARC": "PcbArc",
        "CIRCLE": "PcbCircle",
        "POLY": "PcbPoly",
        "BEZIER": "PcbCurve",
    },
}

#: Cases the renderer draws but cannot reproduce vertex for vertex.
#:
#: Each entry names the source function whose behaviour is not ported and the
#: exact text that proves it. A limit whose citation stops matching is a FAILURE:
#: either the upstream behaviour changed or the claim was wrong.
KNOWN_LIMITS: tuple[tuple[str, str, str, str], ...] = (
    (
        "custom pad outline",
        "pcbnew/pad.cpp",
        "aMergedPolygon->BooleanAdd( polyset );",
        "Custom pads merge their anchor and primitives on every layer. Curved "
        "boundaries use a one-micron polygon approximation and can have different vertices.",
    ),
    (
        "plated through-hole pad flashing on an inner layer",
        "pcbnew/pad.cpp",
        "return board->GetConnectivity()->IsConnectedOnLayer( this, aLayer, nonZoneTypes );",
        "Whether an unused-layer-removed pad keeps copper on an inner layer depends "
        "on board connectivity, which this library derives for schematics only. The "
        "renderer flashes the pad, which is what KiCad returns when it cannot ask.",
    ),
    (
        "solder mask and paste pad expansion",
        "pcbnew/plot_board_layers.cpp",
        "margin.x = margin.y = pad->GetSolderMaskExpansion( aLayer );",
        "Pad and footprint mask and paste overrides are resolved. Polygon offsets "
        "cover custom shapes. Offset vertices can differ from KiCad despite equivalent areas.",
    ),
    (
        "Bezier pad primitive",
        "pcbnew/pad.cpp",
        "primitive->TransformShapeToPolygon( polyset, UNDEFINED_LAYER, 0, GetMaxError(),",
        "Bezier pad primitives now draw through the custom-pad polygon union. "
        "Their flattening vertices need not match KiCad's vertices.",
    ),
    (
        "via flashing on an inner layer",
        "pcbnew/plot_board_layers.cpp",
        "if( onCopperLayer && !via->FlashLayer( aLayerMask ) )",
        "The determinable part of the rule is applied: keep_all, start_end_only "
        "and the outer layers of remove_except_start_end. An inner layer under "
        "remove_all or remove_except_start_end needs board connectivity, which "
        "this library derives for schematics only. The via draws and "
        "via_flash(...).certain reports False, exactly as for a pad.",
    ),
)


#: Per-layer plot DRIVERS. A stream is only reached if its driver is.
#:
#: ``PlotOneBoardLayer`` picks one of these per layer, so a stream this renderer
#: covers under ``PlotStandardLayer`` can still be absent on a mask or silk layer
#: that KiCad routes through a different driver.
LAYER_DRIVERS: dict[str, tuple[str, str, str | None]] = {
    "PlotStandardLayer": (
        "plot_board_layers.cpp",
        "void PlotStandardLayer( BOARD* aBoard, PLOTTER* aPlotter, const LSET& aLayerMask,",
        "every BoardContent stream and the Holes document",
    ),
    "PlotSolderMaskLayer": (
        "plot_board_layers.cpp",
        "GenerateLayerPoly( &solderMask, aBoard, aPlotter, layer, aPlotOpt.GetPlotFPText(),",
        None,
    ),
    "PlotClippedSilkLayer": (
        "plot_board_layers.cpp",
        "silkscreen.BooleanSubtract( solderMask );",
        None,
    ),
    "PlotLayerOutlines": (
        "plot_board_layers.cpp",
        "// PlotLayerOutlines() is designed only for DXF plotters.",
        "not applicable: DXF only, so no SVG export reaches it",
    ),
}

#: Why an uncovered driver is uncovered.
DRIVER_GAP_REASONS: dict[str, str] = {
    "PlotSolderMaskLayer": (
        "a solder mask with a non-zero minimum width is built as ONE merged polygon "
        "over the whole layer and then plotted, so it needs a polygon clipper. At "
        "m_SolderMaskMinWidth == 0 KiCad falls through to PlotLayer and this "
        "renderer agrees; above zero it does not"
    ),
    "PlotClippedSilkLayer": (
        "silkscreen clipped to the solder mask is a boolean subtract of two "
        "whole-layer polygons, so it needs the same clipper"
    ),
}

#: Content KiCad draws only when a plot OPTION asks for it.
#:
#: It is fabrication annotation rather than board geometry, but it is still
#: geometry a plot puts on the page, so an audit that omits it overstates
#: coverage.
CONDITIONAL_CONTENT: dict[str, tuple[str, str, str | None]] = {
    "pad numbers on fab layers": (
        "plot_board_layers.cpp",
        "itemplotter.PlotPadNumber( pad, color );",
        None,
    ),
    "sketch-mode pads on fab layers": (
        "plot_board_layers.cpp",
        "bool sketchPads = ( onFrontFab || onBackFab ) && aPlotOpt.GetSketchPadsOnFabLayers();",
        None,
    ),
    "DNP courtyard crossout on fab layers": (
        "plot_board_layers.cpp",
        "aPlotter->ThickSegment( corner1, corner3, width, nullptr );",
        None,
    ),
}

#: Why a conditional item is not drawn.
CONDITIONAL_GAP_REASONS: dict[str, str] = {
    "pad numbers on fab layers": (
        "a pad number is stroke text sized from the pad bounding box, so it needs "
        "the font plus PlotPadNumber's own sizing rules"
    ),
    "sketch-mode pads on fab layers": (
        "a sketch pad is the pad OUTLINE at the fab line width rather than a filled "
        "area, so it is a second emitter per shape"
    ),
    "DNP courtyard crossout on fab layers": (
        "needs the footprint courtyard, which this library does not derive"
    ),
}

#: Streams this renderer offers that KiCad's plot does NOT draw.
#:
#: Each citation proves the OPPOSITE of the ones in :data:`PLOT_STREAMS`. It
#: names the line where KiCad declines to draw the content, so a reader can see
#: that the step past KiCad is deliberate rather than an accident.
#:
#: A stream here must be opt-in, and it must stay out of ``KICAD_CONTENT``. A
#: caller uses that set to stay comparable against ``kicad-cli``.
BEYOND_KICAD_STREAMS: dict[str, tuple[str, str, str]] = {
    "rule_areas": (
        "plot_board_layers.cpp",
        "if( zone->GetIsRuleArea() )",
        "KiCad's zone loop skips a rule area. This renderer draws one only when "
        "the caller names BoardContent.rule_areas, outside KICAD_CONTENT.",
    ),
}

#: Geometry this renderer draws that KiCad's plot does NOT, without being asked.
#:
#: A DEFECT list, not a gap list, and the distinction is the one the oracle's
#: one-way rule rests on: a missing primitive is scope, an extra primitive is ink
#: the board does not hold. Anything here wants a fix or a ruling, not a record.
#:
#: A deliberate step past KiCad belongs in :data:`BEYOND_KICAD_STREAMS` instead.
#: That is where the rule-area zone went once it became opt-in.
KNOWN_DEFECTS: tuple[tuple[str, str, str, str], ...] = ()

# ---------------------------------------------------------------------------
# Reading KiCad
# ---------------------------------------------------------------------------


def kicad_root() -> Path:
    """Return the pinned KiCad clone, from the environment or the default path."""
    override = os.environ.get(KICAD_ROOT_ENV)
    return Path(override) if override else DEFAULT_KICAD_ROOT


def read_source(root: Path, relative: str) -> str:
    """Read one KiCad source file, or raise with a message a human can act on."""
    for candidate in (root / relative, *root.rglob(Path(relative).name)):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(
        f"{relative} is not under {root}. Set {KICAD_ROOT_ENV} to the pinned clone."
    )


_FUNCTION_RE_TEMPLATE = r"void\s+{name}\s*\([^)]*\)\s*\{{"


def function_body(source: str, qualified_name: str) -> str:
    """Return the body of *qualified_name*, brace-matched from its opening brace.

    Brace matching rather than a regex for the whole body, because a switch body
    holds braces of its own and a non-greedy match stops at the first one.
    """
    pattern = re.compile(_FUNCTION_RE_TEMPLATE.format(name=re.escape(qualified_name)))
    match = pattern.search(source)
    if match is None:
        raise LookupError(
            f"{qualified_name} is not in the source read. KiCad may have renamed or "
            "moved it, which is itself the finding."
        )

    depth = 0
    start = match.end() - 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise LookupError(f"{qualified_name} has unbalanced braces in the source read")


def plotted_item_types(root: Path) -> list[str]:
    """Return every ``PCB_*_T`` case in ``PlotBoardGraphicItem``, in source order."""
    source = read_source(root, "pcbnew/plot_brditems_plotter.cpp")
    body = function_body(source, "BRDITEMS_PLOTTER::PlotBoardGraphicItem")
    found = re.findall(r"case\s+(PCB_[A-Z0-9_]+_T)\s*:", body)
    return list(dict.fromkeys(found))


def enum_members(root: Path, header: str, enum_name: str) -> list[str]:
    """Return the members of ``enum class <enum_name>`` from *header*, in order."""
    source = read_source(root, header)
    pattern = re.compile(
        r"enum\s+class\s+" + re.escape(enum_name) + r"\b[^{]*\{(.*?)\}\s*;", re.DOTALL
    )
    match = pattern.search(source)
    if match is None:
        raise LookupError(f"enum class {enum_name} is not in {header}")

    body = re.sub(r"//[^\n]*", "", match.group(1))
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    members = []
    for part in body.split(","):
        name = part.split("=")[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            members.append(name)
    return members


def citation_holds(root: Path, relative: str, text: str) -> bool:
    """True when *text* still appears in *relative*.

    This is what keeps a hand-written claim honest. A citation that stops
    matching means the upstream code moved and the claim needs re-reading.
    """
    try:
        return text in read_source(root, relative)
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Reading this library
# ---------------------------------------------------------------------------


def _module_ast(relative: str) -> ast.Module:
    return ast.parse((SRC / relative).read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise LookupError(f"{name} is not in the parsed module")


def dispatched_types() -> list[str]:
    """Return the classes ``shapes_for`` dispatches on, read from its AST.

    An ``isinstance`` test against a union counts every member of the union, which
    is how a track segment and a track arc share one branch.
    """
    functions = [
        _function(_module_ast("render/geometry.py"), "shapes_for"),
        _function(_module_ast("render/__init__.py"), "_shapes_for_render_item"),
        _function(_module_ast("render/board_extras.py"), "extra_shapes"),
    ]
    found: list[str] = []
    for function in functions:
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "isinstance"):
                continue
            if len(node.args) < 2:
                continue
            found.extend(_names_in(node.args[1]))
    return list(dict.fromkeys(found))


def _names_in(node: ast.expr) -> list[str]:
    """Return the identifiers in a type expression, flattening ``A | B``."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _names_in(node.left) + _names_in(node.right)
    if isinstance(node, ast.Tuple):
        return [name for element in node.elts for name in _names_in(element)]
    return []


def pad_shape_branches() -> list[str]:
    """Return the ``PadShape`` members ``pad_outline`` branches on."""
    function = _function(_module_ast("render/pads.py"), "pad_outline")
    found = []
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "PadShape"
        ):
            found.append(node.attr)
    return list(dict.fromkeys(found))


def library_enum_values(dotted: str, enum_name: str) -> list[str]:
    """Return the string values of one of this library's enums, via import."""
    module = __import__(dotted, fromlist=[enum_name])
    enum_class = getattr(module, enum_name)
    return [member.value for member in enum_class]


def board_content_members() -> list[str]:
    """Return the ``BoardContent`` stream names."""
    return library_enum_values("eedesigncore.render.traversal", "BoardContent")


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass
class Axis:
    """One graded axis: what was expected, what is covered, what is not."""

    name: str
    question: str
    covered: dict[str, str] = field(default_factory=dict)
    gaps: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.covered) + len(self.gaps)

    @property
    def percent(self) -> float:
        return 100.0 * len(self.covered) / self.total if self.total else 100.0


def audit_item_types(root: Path) -> Axis:
    """Axis 1: item types a board plot draws."""
    axis = Axis("item types", "which board items does a plot draw?")
    plotted = plotted_item_types(root)
    dispatch = set(dispatched_types())

    unclassified = [name for name in plotted if name not in KICAD_TYPE_TO_MODEL]
    if unclassified:
        axis.errors.append(
            "KiCad plots item types this audit has never classified: "
            + ", ".join(unclassified)
            + ". Add each one to KICAD_TYPE_TO_MODEL, with a GAP_REASONS entry if "
            "the renderer does not draw it."
        )

    stale = [name for name in KICAD_TYPE_TO_MODEL if name not in plotted]
    if stale:
        axis.errors.append(
            "KICAD_TYPE_TO_MODEL names types KiCad no longer plots: "
            + ", ".join(stale)
            + ". Remove them; the audit is grading something that cannot happen."
        )

    for name in plotted:
        if name not in KICAD_TYPE_TO_MODEL:
            axis.gaps[name] = "UNCLASSIFIED: see the stale note below"
            continue

        model = KICAD_TYPE_TO_MODEL[name]
        if model is None:
            axis.gaps[name] = GAP_REASONS.get(name, "NO REASON RECORDED")
            if name not in GAP_REASONS:
                axis.errors.append(f"{name} is an unexplained gap; add it to GAP_REASONS")
        elif model in dispatch:
            axis.covered[name] = f"shapes_for dispatches on {model}"
        else:
            axis.gaps[name] = (
                f"KICAD_TYPE_TO_MODEL claims {model}, but shapes_for does not dispatch on it"
            )
            axis.errors.append(f"{name}: claimed model {model} is not in the shapes_for dispatch")

    return axis


def audit_streams(root: Path) -> Axis:
    """Axis 2: the content streams a plot walks."""
    axis = Axis("content streams", "which board streams does a plot walk?")
    content = set(board_content_members())

    for stream, (relative, citation) in PLOT_STREAMS.items():
        if not citation_holds(root, relative, citation):
            axis.errors.append(
                f"the citation for the {stream} stream no longer appears in {relative}: "
                f"{citation!r}. Re-read the plot driver."
            )
        covered_by = STREAM_COVERAGE.get(stream)
        if covered_by is None:
            axis.gaps[stream] = "not covered and no coverage recorded"
        else:
            axis.covered[stream] = covered_by

    for stream, (relative, citation, note) in BEYOND_KICAD_STREAMS.items():
        if not citation_holds(root, relative, citation):
            axis.errors.append(
                f"the citation for the {stream} stream no longer appears in "
                f"{relative}: {citation!r}. KiCad may now draw this content."
            )
        axis.covered[f"{stream} (past KiCad)"] = note

    graded = {"drawings", *PLOT_STREAMS, *BEYOND_KICAD_STREAMS}
    unknown = [name for name in content if name not in graded]
    if unknown:
        axis.errors.append(
            "BoardContent names streams this audit does not grade: "
            + ", ".join(sorted(unknown))
            + ". Add each to PLOT_STREAMS with the line that proves KiCad walks "
            "it, or to BEYOND_KICAD_STREAMS with the line that proves it does not."
        )

    leaked = sorted(set(BEYOND_KICAD_STREAMS) & _kicad_content_names())
    if leaked:
        axis.errors.append(
            "KICAD_CONTENT holds streams KiCad does not draw: "
            + ", ".join(leaked)
            + ". A caller uses that set to stay comparable against kicad-cli."
        )

    return axis


def _kicad_content_names() -> set[str]:
    """Return the stream names in ``KICAD_CONTENT``."""
    from eedesigncore.render.traversal import KICAD_CONTENT

    return {member.value for member in KICAD_CONTENT}


def audit_enums(root: Path) -> list[Axis]:
    """Axis 3: enum members inside a covered item type."""
    axes: list[Axis] = []
    pad_branches = set(pad_shape_branches())
    dispatch = set(dispatched_types())
    enums_module = "eedesigncore.common.pcb_family.enums"
    library_pad_shapes = set(library_enum_values(enums_module, "PadShape"))
    library_pad_types = set(library_enum_values(enums_module, "PadType"))

    for header, enum_name, key in ENUM_AXES:
        axis = Axis(enum_name, f"which {enum_name} members does the renderer handle?")
        members = enum_members(root, header, enum_name)
        table = ENUM_MEMBER_TO_TOKEN.get(key, {})

        unclassified = [name for name in members if name not in table]
        if unclassified:
            axis.errors.append(
                f"{enum_name} has members this audit has never classified: "
                + ", ".join(unclassified)
                + f". Add them to ENUM_MEMBER_TO_TOKEN[{key!r}]."
            )

        for member in members:
            token = table.get(member, "")
            if token is None:
                axis.covered[member] = "never written to a file, so nothing to draw"
                continue
            if not token:
                axis.gaps[member] = "unclassified"
                continue

            if key == "pad_shapes":
                if token in pad_branches and token in library_pad_shapes:
                    axis.covered[member] = f"pad_outline branches on PadShape.{token}"
                else:
                    axis.gaps[member] = f"pad_outline has no PadShape.{token} branch"
            elif key == "pad_types":
                if token in library_pad_types:
                    axis.covered[member] = f"PadType.{token} loads and reaches the flash rule"
                else:
                    axis.gaps[member] = f"PadType has no {token} member"
            elif key == "drill_shapes":
                axis.covered[member] = "the Holes document draws a round hole and a slot"
            elif key == "shape_types":
                if token in dispatch:
                    axis.covered[member] = f"shapes_for dispatches on {token}"
                else:
                    axis.gaps[member] = f"shapes_for does not dispatch on {token}"

        axes.append(axis)
    return axes


def _audit_citation_table(
    root: Path,
    name: str,
    question: str,
    table: dict[str, tuple[str, str, str | None]],
    reasons: dict[str, str],
) -> Axis:
    """Grade a table of (file, citation, coverage-or-None) rows.

    Every citation is re-verified, so a row cannot outlive the source line it
    rests on.
    """
    axis = Axis(name, question)
    for label, (relative, citation, covered_by) in table.items():
        if not citation_holds(root, relative, citation):
            axis.errors.append(
                f"the citation for {label!r} no longer appears in {relative}: "
                f"{citation!r}. Re-read the plot driver."
            )
        if covered_by is not None:
            axis.covered[label] = covered_by
        else:
            axis.gaps[label] = reasons.get(label, "NO REASON RECORDED")
            if label not in reasons:
                axis.errors.append(f"{label!r} is an unexplained gap; record a reason")
    return axis


def audit_layer_drivers(root: Path) -> Axis:
    """Axis 5: the per-layer driver, which decides whether a stream is reached."""
    return _audit_citation_table(
        root,
        "layer drivers",
        "which per-layer plot driver does each layer go through?",
        LAYER_DRIVERS,
        DRIVER_GAP_REASONS,
    )


def audit_conditional_content(root: Path) -> Axis:
    """Axis 6: fabrication annotation a plot option turns on."""
    return _audit_citation_table(
        root,
        "conditional content",
        "what does a plot option add to the page?",
        CONDITIONAL_CONTENT,
        CONDITIONAL_GAP_REASONS,
    )


def audit_defects(root: Path) -> Axis:
    """Geometry drawn that KiCad does not draw. A defect, not a gap."""
    axis = Axis("defects", "what does this renderer draw that KiCad does not?")
    for label, relative, citation, detail in KNOWN_DEFECTS:
        if citation_holds(root, relative, citation):
            axis.gaps[label] = detail
        else:
            axis.errors.append(
                f"the citation for the {label!r} defect no longer appears in "
                f"{relative}: {citation!r}. Re-read it before trusting this row."
            )
    return axis


def audit_limits(root: Path) -> Axis:
    """Axis 4: cases drawn but not reproduced exactly."""
    axis = Axis("known limits", "what is drawn but not vertex-exact?")
    for label, relative, citation, reason in KNOWN_LIMITS:
        if citation_holds(root, relative, citation):
            axis.gaps[label] = reason
        else:
            axis.errors.append(
                f"the citation for the {label!r} limit no longer appears in {relative}: "
                f"{citation!r}. Either the upstream behaviour changed or the claim "
                "was wrong; re-read it before trusting this row."
            )
    return axis


def render_text(axes: list[Axis], root: Path) -> str:
    """Return the human-readable report."""
    lines = [
        "SVG render coverage, graded against KiCad's own board plot",
        f"KiCad clone: {root}",
        "",
    ]
    for axis in axes:
        if axis.name in ("known limits", "defects"):
            lines.append(f"{axis.name.upper()}  ({len(axis.gaps)} recorded)")
        else:
            lines.append(
                f"{axis.name.upper()}  {len(axis.covered)}/{axis.total} "
                f"({axis.percent:.0f}%)  -- {axis.question}"
            )
        for name, how in axis.covered.items():
            lines.append(f"    ok    {name:26s} {how}")
        for name, why in axis.gaps.items():
            lines.append(f"    GAP   {name:26s} {why}")
        for problem in axis.errors:
            lines.append(f"    STALE {problem}")
        lines.append("")

    defects = sum(len(axis.gaps) for axis in axes if axis.name == "defects")
    failures = sum(len(axis.errors) for axis in axes)
    graded = [axis for axis in axes if axis.name not in ("known limits", "defects")]
    covered = sum(len(axis.covered) for axis in graded)
    total = sum(axis.total for axis in graded)
    lines.append(f"TOTAL  {covered}/{total} graded cases covered")
    if defects:
        lines.append(f"DEFECTS  {defects} recorded: geometry drawn that KiCad does NOT draw")
    lines.append(
        "AUDIT IS CURRENT" if failures == 0 else f"AUDIT IS STALE: {failures} problem(s) above"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero on a stale claim or an unclassified case, but not on a gap",
    )
    parser.add_argument("--kicad-root", type=Path, default=None, help="the pinned KiCad clone")
    args = parser.parse_args(argv)

    root = args.kicad_root or kicad_root()
    if not root.is_dir():
        print(
            f"the KiCad clone is not at {root}. Pass --kicad-root or set "
            f"{KICAD_ROOT_ENV}. This audit cannot run without the source it grades against.",
            file=sys.stderr,
        )
        return 2

    sys.path.insert(0, str(REPO_ROOT / "src"))
    axes = [
        audit_item_types(root),
        audit_streams(root),
        audit_layer_drivers(root),
        audit_conditional_content(root),
        *audit_enums(root),
        audit_limits(root),
        audit_defects(root),
    ]

    if args.json:
        print(
            json.dumps(
                {
                    "kicad_root": str(root),
                    "axes": [
                        {
                            "name": axis.name,
                            "question": axis.question,
                            "covered": axis.covered,
                            "gaps": axis.gaps,
                            "stale": axis.errors,
                            "percent": round(axis.percent, 1),
                        }
                        for axis in axes
                    ],
                },
                indent=2,
            )
        )
    else:
        print(render_text(axes, root))

    if args.check and any(axis.errors for axis in axes):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
