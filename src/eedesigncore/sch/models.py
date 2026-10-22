"""Pydantic v2 domain models for KiCad schematic files (``.kicad_sch``).

Each ``.kicad_sch`` file is one ``SCH_SHEET``'s screen; there is no separate
whole-schematic formatter (``Format( SCH_SHEET* )``, sch-root-grammar report
Part 1). The embeddable ``Symbol`` sub-grammar used by ``(lib_symbols)`` is
reused verbatim from ``common/sch_family`` (arch-plan section 2c); only the
format-root wrapper ``Schematic`` and the schematic-only item models live here.

Iteration-10 phasing: T1 lands the root skeleton (header / paper / title_block /
empty ``(lib_symbols)`` / embedded blocks). T2 populates ``lib_symbols`` and adds
the placed ``SCH_SYMBOL`` item (``PlacedSymbol``). Higher-frequency items
(wires/labels), sheets and ``(sheet_instances)`` follow in T3/T4.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Self

from pydantic import field_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.common.io_mixin import KicadFileMixin
from eedesigncore.common.models import (
    MAX_EMBEDDED_DATA_SIZE,
    Color,
    EmbeddedFile,
    Fill,
    Property,
    Stroke,
    TextAttributes,
)
from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import Symbol

# A free graphic shape drawn directly on the schematic sheet (saveShape,
# SCH_SHAPE_T). Reuses the shared sch_family shape models verbatim (plan R1);
# only the sch root hosts them as first-class items (ADR-038).
GraphicShape = SchArc | SchCircle | SchRect | SchBezier | SchPolyline

# ---------------------------------------------------------------------------
# Placed SCH_SYMBOL instance (T2)
# ---------------------------------------------------------------------------


class PlacedSymbolPin(KicadModel):
    """A per-instance pin entry: ``(pin "<number>" (uuid …) [(alternate …)])``.

    Instance-only pin uuid + optional alternate-pin selection
    (``saveSymbol`` sch_io_kicad_sexpr.cpp:824-841). ``number`` is the pin's
    number string (the pin identity within the lib symbol).
    """

    number: str
    uuid: str | None = None
    alternate: str | None = None


class InstanceVariantField(KicadModel):
    """One ``(field (name <n>) (value <v>))`` inside a placed-symbol variant (:970-974)."""

    name: str
    value: str


class InstanceVariant(KicadModel):
    """A per-instance ``(variant (name …) …)`` on a placed symbol (saveSymbol :949-977).

    A placed-symbol instance ``(path …)`` may carry zero or more of these (KiCad's
    ``m_Variants`` is a name-keyed map, emitted in map order). Each optional bool is
    written by KiCad ONLY when it DIFFERS from the symbol's own attribute, so each is
    ``None`` when absent on disk and re-emitted verbatim for byte-identity. Unlike the
    sheet-instance variant, the placed-symbol writer DOES emit ``on_board`` and
    ``in_pos_files`` (:964-968). ``in_bom`` is the literal on-disk value (the parser's
    >=20260306 inversion, parser.cpp:3418, is not surfaced in this layer). Emit order:
    name, dnp, exclude_from_sim, in_bom, on_board, in_pos_files, fields.
    """

    name: str
    dnp: bool | None = None
    exclude_from_sim: bool | None = None
    in_bom: bool | None = None
    on_board: bool | None = None
    in_pos_files: bool | None = None
    fields: list[InstanceVariantField] = []


class InstancePath(KicadModel):
    """One ``(path "/uuid…" (reference "R1") (unit N) [(variant …)…])`` entry."""

    path: str
    reference: str
    unit: int
    variants: list[InstanceVariant] = []


class InstanceProject(KicadModel):
    """One ``(project "name" (path …) …)`` group inside ``(instances …)``."""

    name: str
    paths: list[InstancePath] = []


class PlacedSymbol(KicadModel):
    """A placed ``SCH_SYMBOL`` instance node (``saveSymbol``, sch-only).

    Field/emit order mirrors ``sch_io_kicad_sexpr.cpp:706-989`` (embed-context
    deltas Part 2b): optional ``(lib_name …)`` override, ``(lib_id …)``, ``(at …)``,
    optional ``(mirror x|y)``, ``(unit)``/``(body_style)``, the five instance bools,
    optional ``(fields_autoplaced yes)``, ``(uuid)``, fields, per-pin blocks, then
    the ``(instances …)`` block. This node has no ``.kicad_sym`` counterpart.
    """

    lib_name: str | None = None
    lib_id: str
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    mirror: str | None = None  # "x" or "y"
    unit: int = 1
    body_style: int = 1
    exclude_from_sim: bool = False
    in_bom: bool = True
    on_board: bool = True
    in_pos_files: bool = True
    dnp: bool = False
    fields_autoplaced: bool = False
    uuid: str | None = None
    fields: list[Property] = []
    pins: list[PlacedSymbolPin] = []
    instances: list[InstanceProject] = []


# ---------------------------------------------------------------------------
# High-frequency sch items (T3a): connectivity + graphics + text/labels
# ---------------------------------------------------------------------------


class Junction(KicadModel):
    """A wire/bus junction dot (``saveJunction`` :1244-1262).

    ``(junction (at X Y) (diameter D) (color R G B A) (uuid …))`` -- all tokens
    are always emitted, no guards. ``diameter`` 0 means "use theme default";
    ``color`` (0,0,0,0.0) likewise.
    """

    position: tuple[float, float] = (0.0, 0.0)
    diameter: float = 0.0
    color: Color = Color(r=0, g=0, b=0, a=0.0)
    uuid: str | None = None


class NoConnect(KicadModel):
    """A no-connect flag (``saveNoConnect`` :1265-1277): ``(no_connect (at X Y) (uuid …))``."""

    position: tuple[float, float] = (0.0, 0.0)
    uuid: str | None = None


class BusEntry(KicadModel):
    """A wire-to-bus entry (``saveBusEntry`` :1294-1306).

    ``(bus_entry (at X Y) (size W H) <stroke> (uuid …))``. Bus-to-bus entries are
    NOT modeled here: KiCad emits them as ``(bus …)`` lines (ADR-039).
    """

    position: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] = (0.0, 0.0)
    stroke: Stroke = Stroke()
    uuid: str | None = None


class LineType(str, Enum):
    """The layer-derived token for a ``SCH_LINE`` (saveLine :1372-1379)."""

    wire = "wire"
    bus = "bus"
    polyline = "polyline"


class Line(KicadModel):
    """A two-point line segment (``saveLine`` :1364-1395).

    ``(wire|bus|polyline (pts (xy Sx Sy) (xy Ex Ey)) <stroke> (uuid …))``. Always
    exactly two points; N-point ``polyline`` graphics are ``SchPolyline`` shapes.
    """

    line_type: LineType = LineType.wire
    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    stroke: Stroke = Stroke()
    uuid: str | None = None


class SchText(KicadModel):
    """A free text item (``saveText`` for ``SCH_TEXT_T``, :1398-1469).

    ``(text "…" (exclude_from_sim b) (at X Y ANGLE) <effects> (uuid …))``. Plain
    text carries no ``shape`` and no fields (labels only). ``angle`` is stored as
    the raw on-disk degrees value and re-emitted verbatim (KiCad-written files are
    already keep-upright normalized, so no spin-style math is needed for parity).
    """

    content: str = ""
    exclude_from_sim: bool = False
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    attributes: TextAttributes | None = None
    uuid: str | None = None


class LabelKind(str, Enum):
    """The four label tokens ``saveText`` emits (common.cpp:201-212).

    ``netclass_flag`` is the CURRENT on-disk token for a directive label;
    ``directive_label`` is a read-only legacy alias (audit Contradiction 1).
    """

    local = "label"
    global_ = "global_label"
    hierarchical = "hierarchical_label"
    netclass_flag = "netclass_flag"


class Label(KicadModel):
    """A net label of any kind (``saveText`` for the ``SCH_LABEL_BASE`` family).

    Emission order (:1405-1466) is kind-dependent:
    ``(<tok> "text" [(length L) iff netclass_flag] [(shape …) iff global/hier/
    netclass] (at X Y ANGLE) [(fields_autoplaced yes)] <effects> (uuid …)
    <fields…>)``. ``angle`` is the raw on-disk spin-adjusted degrees (stored and
    re-emitted verbatim; ADR-039 rationale applies).
    """

    kind: LabelKind
    content: str = ""
    length: float | None = None
    shape: str | None = None
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    fields_autoplaced: bool = False
    attributes: TextAttributes | None = None
    uuid: str | None = None
    fields: list[Property] = []


class SchTextBox(KicadModel):
    """A schematic text box (``saveTextBox`` for ``SCH_TEXTBOX_T``, :1472-1506).

    Distinct from the ``.kicad_sym`` ``SymTextBox`` grammar: the sch form carries
    ``(exclude_from_sim …)`` after the text and a trailing ``(uuid …)``, and has no
    ``private`` prefix. Order: ``(text_box "…" (exclude_from_sim b) (at X Y ANGLE)
    (size W H) (margins L T R B) <stroke> <fill> <effects> (uuid …))``.
    """

    content: str = ""
    exclude_from_sim: bool = False
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    size: tuple[float, float] = (0.0, 0.0)
    margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    stroke: Stroke = Stroke()
    fill: Fill = Fill()
    attributes: TextAttributes | None = None
    uuid: str | None = None


# ---------------------------------------------------------------------------
# Lower-frequency sch items (T3b): rule_area / image / group / table
# ---------------------------------------------------------------------------


class RuleArea(KicadModel):
    """A schematic rule area (``saveRuleArea`` :1347-1361).

    ``(rule_area (exclude_from_sim b)(in_bom b)(on_board b)(dnp b) <polyline>)``.
    A ``SCH_RULE_AREA`` IS-A ``SCH_SHAPE`` (a closed polygon); the wrapped
    ``SchPolyline`` carries the pts/stroke/fill and the item ``uuid`` (KiCad stores
    the poly's uuid on the rule area, parser.cpp:4500).
    """

    exclude_from_sim: bool = False
    in_bom: bool = True
    on_board: bool = True
    dnp: bool = False
    shape: SchPolyline = SchPolyline()


class Image(KicadModel):
    """A reference bitmap image (``saveBitmap`` :1031-1066): token is ``image``.

    ``(image (at X Y) [(scale g)] (uuid …) (data "…" "…" …))``. ``scale`` is
    emitted (``{:g}``) only when != 1.0. NO layer token (unlike PCB). ``data`` is
    the base64 payload stored as the concatenation of the on-disk 76-char chunks;
    the writer re-chunks it via ``FormatStreamData`` (kicad_io_utils.cpp:55-74).

    ``data`` carries the SAME validator as ``EmbeddedFile.data`` (LOW-3): a 1 GiB
    cap and the base64 character set. The writer wraps each chunk in quotes and
    escapes nothing, so a quote or a backslash inside ``data`` would break the
    token. The character set makes that unreachable.
    """

    position: tuple[float, float] = (0.0, 0.0)
    scale: float | None = None
    uuid: str | None = None
    data: str = ""

    @field_validator("data")
    @classmethod
    def _validate_base64(cls, v: str) -> str:
        if len(v) > MAX_EMBEDDED_DATA_SIZE:
            raise ValueError("Image data exceeds the 1 GiB limit")
        if not re.fullmatch(r"[A-Za-z0-9+/=\n\r\s]*", v):
            raise ValueError("Image data contains invalid base64 characters")
        return v


class Group(KicadModel):
    """A schematic group (``saveGroup`` :1613-1643); empty groups are not written.

    ``(group "name" (uuid …) [(locked yes)] [(lib_id "…")] (members "u" "u" …))``.
    Member uuids are emitted sorted ascending (:1634). ``lib_id`` is the optional
    design-block link.
    """

    name: str = ""
    uuid: str | None = None
    locked: bool = False
    lib_id: str | None = None
    members: list[str] = []


class SchTableCell(KicadModel):
    """A table cell (``saveTextBox`` with ``SCH_TABLECELL_T``, :1476-1505).

    Same grammar as ``SchTextBox`` but the token is ``table_cell``, it carries a
    ``(span cols rows)`` and NO ``<stroke>`` (cells never emit stroke, :1499).
    """

    content: str = ""
    exclude_from_sim: bool = False
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    size: tuple[float, float] = (0.0, 0.0)
    margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    span: tuple[int, int] = (1, 1)
    fill: Fill = Fill()
    attributes: TextAttributes | None = None
    uuid: str | None = None


class SchTable(KicadModel):
    """A schematic table (``saveTable`` :1509-1610).

    ``(table (column_count N) (border (external b)(header b)[<stroke>])
    (separators (rows b)(cols b)[<stroke>]) (column_widths …)(row_heights …)
    (uuid …) (cells <table_cell>…))``. Border/separator strokes are emitted only
    when their governing flags are set (:1564, :1573). Cells use the sch text-box
    grammar (``SchTableCell``), NOT the PCB cell grammar.
    """

    column_count: int = 0
    border_external: bool = False
    border_header: bool = False
    border_stroke: Stroke = Stroke()
    separators_rows: bool = False
    separators_cols: bool = False
    separators_stroke: Stroke = Stroke()
    column_widths: list[float] = []
    row_heights: list[float] = []
    uuid: str | None = None
    cells: list[SchTableCell] = []


# ---------------------------------------------------------------------------
# Sheet node + sheet-instance blocks (T4)
# ---------------------------------------------------------------------------


class SheetPin(KicadModel):
    """A hierarchical sheet pin (``saveSheet`` :1109-1125).

    ``(pin "name" <shapeToken> (at X Y ANGLE) (uuid …) <effects>)``. ``shape`` is
    the sheet-pin electrical token (``input``/``output``/``bidirectional``/
    ``tri_state``/``passive``) stored verbatim; ``angle`` is the side-derived
    on-disk degrees (``getSheetPinAngle``) preserved as read.
    """

    name: str = ""
    shape: str = "input"
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    uuid: str | None = None
    attributes: TextAttributes | None = None


class SheetVariantField(KicadModel):
    """One ``(field (name <n>) (value <v>))`` inside a sheet variant (:1217-1221)."""

    name: str
    value: str


class SheetVariant(KicadModel):
    """A per-sheet-instance variant (``saveSheet`` :1202-1225).

    Emitted per ``m_Variants`` entry (name-sorted map). The writer emits only the
    ``dnp``/``exclude_from_sim``/``in_bom`` bools that DIFFER from the sheet's own
    attributes, so each is optional here (``None`` = not on disk) and re-emitted
    verbatim for byte-identity. ``in_bom`` is the literal on-disk value (positive
    logic; the parser's ``!value`` internal inversion, parser.cpp:3889, is not
    surfaced in this layer). ``on_board``/``in_pos_files`` are parser-accepted but
    NEVER emitted by the 10.0.3 writer (§4c) -- preserved if present on load.
    """

    name: str
    dnp: bool | None = None
    exclude_from_sim: bool | None = None
    in_bom: bool | None = None
    on_board: bool | None = None
    in_pos_files: bool | None = None
    fields: list[SheetVariantField] = []


class SheetInstancePath(KicadModel):
    """One per-sheet ``(path "<full>" (page "<n>") [variant…])`` (:1196-1227).

    The path is the full KIID path (leading root-sheet UUID segment; NO ``"/"``
    empty-path fallback -- that is root-block only). ``page`` is quoted on write.
    """

    path: str
    page: str = ""
    variants: list[SheetVariant] = []


class SheetInstanceProject(KicadModel):
    """One ``(project "name" (path …) …)`` clause in a sheet's ``(instances)`` block.

    Instances are grouped by root-sheet UUID into project clauses; source order is
    preserved verbatim (the writer relies on project-contiguous ordering, A1 §3b /
    open-question 2). Distinct from the placed-symbol ``InstanceProject``.
    """

    name: str
    paths: list[SheetInstancePath] = []


class Sheet(KicadModel):
    """A sub-sheet node (``saveSheet`` :1069-1241, ``SCH_SHEET_T``).

    Field order mirrors the writer: ``(at)``, ``(size)``, the four bools
    ``exclude_from_sim``/``in_bom``/``on_board``/``dnp``, optional
    ``(fields_autoplaced yes)``, ``<stroke>`` (border), ``(fill (color …))``
    (background), ``(uuid)``, fields (``Sheetname``/``Sheetfile``/…), pins, then the
    optional per-sheet ``(instances …)`` block. The ``Sheetfile`` field carries the
    sub-sheet file reference, preserved as data (single-file scope; ADR-040).
    """

    position: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] = (0.0, 0.0)
    exclude_from_sim: bool = False
    in_bom: bool = True
    on_board: bool = True
    dnp: bool = False
    fields_autoplaced: bool = False
    stroke: Stroke = Stroke()
    fill_color: Color = Color(r=0, g=0, b=0, a=0.0)
    uuid: str | None = None
    fields: list[Property] = []
    pins: list[SheetPin] = []
    instances: list[SheetInstanceProject] = []


class RootInstancePath(KicadModel):
    """One flat ``(path "<p>" (page "<n>"))`` entry in the root block (:1659-1661).

    Both values are quoted; the root sheet's own instance carries the empty path
    serialized as the literal ``"/"`` (A1 §1). No project wrapper, no variants.
    """

    path: str = "/"
    page: str = ""


class SheetInstances(KicadModel):
    """The root ``(sheet_instances …)`` block (``saveInstances`` :1646-1666).

    A FLAT list of ``(path (page))`` pairs -- structurally distinct from the
    per-sheet ``(instances)`` block (A1 §1): no ``(project …)`` wrapper, no
    ``(variant …)``. Emitted once per file only when the sheet has a root instance
    (``HasRootInstance()``, :520-526); in practice exactly one entry. The whole
    block is omitted if ``paths`` is empty.
    """

    paths: list[RootInstancePath] = []


# ---------------------------------------------------------------------------
# Schematic root
# ---------------------------------------------------------------------------


class Schematic(KicadFileMixin):
    """The root of a ``.kicad_sch`` schematic file (``Format( SCH_SHEET* )``).

    Field order mirrors the writer's emission order (sch-root-grammar Part 1):
    header wrapper, ``(uuid)`` (the *screen* uuid), ``(paper)``, ``(title_block)``,
    ``(lib_symbols)``, the ordered item stream, ``(sheet_instances)`` (T4), then
    the embedded ``(embedded_fonts)`` / ``(embedded_files)`` tail.

    The item stream is emitted SORTED by ``(KICAD_T type-enum, uuid)`` (a
    ``std::multiset``, Part 3), NOT encounter order -- unlike the board writer.
    The writer re-sorts on emit; model insertion order is irrelevant.
    """

    # Header wrapper
    version: int = 20260306
    generator: str = "eeschema"
    generator_version: str = "10.0"
    uuid: str | None = None

    # Page setup (reused cross-app models; same emitters as the board writer)
    paper: Paper | None = None
    title_block: TitleBlock | None = None

    # (lib_symbols) block: embedded .kicad_sym symbols keyed by full "Lib:Name".
    # The Symbol.name field carries the full lib_id in this context (ADR-037);
    # per-unit child names stay bare (stored verbatim).
    lib_symbols: list[Symbol] = []

    # Item stream (populated incrementally: T2 placed symbols, T3 wires/labels,
    # T4 sheets). Emitted sorted by (KICAD_T type-enum, uuid) per Part 3. Each
    # typed list holds one item category; the writer merges + sorts on emit.
    graphic_shapes: list[GraphicShape] = []  # SCH_SHAPE_T (arc/circle/rect/bezier/poly)
    texts: list[SchText] = []  # SCH_TEXT_T
    text_boxes: list[SchTextBox] = []  # SCH_TEXTBOX_T
    junctions: list[Junction] = []  # SCH_JUNCTION_T
    no_connects: list[NoConnect] = []  # SCH_NO_CONNECT_T
    bus_entries: list[BusEntry] = []  # SCH_BUS_WIRE_ENTRY_T
    lines: list[Line] = []  # SCH_LINE_T (wire/bus/polyline-2pt)
    labels: list[Label] = []  # SCH_LABEL_T / GLOBAL / HIER / DIRECTIVE (by kind)
    images: list[Image] = []  # SCH_BITMAP_T
    tables: list[SchTable] = []  # SCH_TABLE_T
    rule_areas: list[RuleArea] = []  # SCH_RULE_AREA_T
    groups: list[Group] = []  # SCH_GROUP_T
    symbols: list[PlacedSymbol] = []  # SCH_SYMBOL_T
    sheets: list[Sheet] = []  # SCH_SHEET_T (sub-sheet nodes; single-file scope)

    # (sheet_instances) block (Part 1 #7, saveInstances :1646). The modeled root
    # block (T4): a flat list of (path (page)) pairs, emitted only when present.
    # Distinct from each sub-sheet's per-sheet (instances) block (Sheet.instances).
    sheet_instances: SheetInstances | None = None

    # Root tail: (embedded_fonts yes|no) then optional inline (embedded_files …).
    embedded_fonts: bool | None = None
    embedded_files: list[EmbeddedFile] = []

    # ------------------------------------------------------------------
    # KicadFileMixin hooks
    # ------------------------------------------------------------------

    @classmethod
    def _from_text(cls, text: str) -> Self:
        from eedesigncore.sch.loader import load_sch
        from eedesigncore.sexpr.parser import parse

        tree = parse(text)
        return load_sch(tree)  # type: ignore[return-value]

    def _to_lf_string(self) -> str:
        from eedesigncore.sch.writer import write_sch

        return write_sch(self)
