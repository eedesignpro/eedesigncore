"""Pydantic v2 domain models for KiCad footprint library files (.kicad_mod)."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.common.io_mixin import KicadFileMixin
from eedesigncore.common.models import (
    EmbeddedFile,
    Property,
    PtsElement,
)
from eedesigncore.common.pcb_family import (
    BackdrillInfo,
    Barcode,
    ComponentClasses,
    Dimension,
    FootprintUnitInfo,
    JumperPadGroups,
    Padstack,
    PcbDrawItem,
    PcbGroup,
    PcbPoint,
    PostMachining,
    ReferenceImage,
    Stackup,
    Table,
    Variant,
    Zone,
)
from eedesigncore.mod.enums import (
    ChamferPosition,
    CustomAnchor,
    CustomShapeClearance,
    PadProperty,
    PadShape,
    PadType,
)

# ---------------------------------------------------------------------------
# Pad sub-structures
# ---------------------------------------------------------------------------


class DrillInfo(KicadModel):
    """Drill specification inside a ``drill`` token."""

    oval: bool = False
    width: float = 0.0  # primary diameter
    height: float | None = None  # secondary diameter (oval only)
    offset: tuple[float, float] | None = None


class TeardropParams(KicadModel):
    """Teardrop parameters (``teardrops`` token)."""

    best_length_ratio: float = 0.5
    max_length: float = 1.0
    best_width_ratio: float = 0.5
    max_width: float = 1.0
    curved_edges: bool = False
    filter_ratio: float = 0.75
    enabled: bool = False
    allow_two_segments: bool = True
    prefer_zone_connections: bool = False


class PrimLine(KicadModel):
    """``gr_line`` primitive inside pad primitives."""

    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    width: float = 0.0


class PrimRect(KicadModel):
    """``gr_rect`` primitive inside pad primitives."""

    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    corner_radius: float | None = None
    width: float = 0.0
    fill: bool = False


class PrimArc(KicadModel):
    """``gr_arc`` primitive inside pad primitives."""

    start: tuple[float, float] = (0.0, 0.0)
    mid: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    width: float = 0.0


class PrimCircle(KicadModel):
    """``gr_circle`` primitive inside pad primitives."""

    center: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    width: float = 0.0
    fill: bool = False


class PrimCurve(KicadModel):
    """``gr_curve`` primitive inside pad primitives.

    ``pts`` holds exactly four control points, in the order start, control 1,
    control 2, end. KiCad writes a Bezier primitive as four ``(xy …)`` points and
    reads back the same four (``format(PAD*)`` pcb_io_kicad_sexpr.cpp:1940-1949).
    Any other count is a construct-from-scratch error (GAP-015).
    """

    pts: list[PtsElement] = []  # exactly 4
    width: float = 0.0

    @model_validator(mode="after")
    def _check_four_control_points(self) -> Self:
        if len(self.pts) != 4:
            raise ValueError(
                f"PrimCurve needs exactly 4 control points, got {len(self.pts)}. "
                "Supply start, control 1, control 2 and end."
            )
        return self


class PrimPoly(KicadModel):
    """``gr_poly`` primitive inside pad primitives."""

    pts: list[PtsElement] = []
    width: float = 0.0
    fill: bool = False


class PrimVector(KicadModel):
    """``gr_vector`` proxy primitive (a proxy SEGMENT, GAP-010).

    format(PAD*) :1954: a proxy SEGMENT emits ``(gr_vector (start …) (end …))``
    with NO ``(width …)`` (proxy items skip width, :2023) and no fill.
    """

    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)


class PrimBbox(KicadModel):
    """``gr_bbox`` proxy primitive (a proxy RECTANGLE, GAP-010).

    format(PAD*) :1969: a proxy RECTANGLE emits ``(gr_bbox (start …) (end …)
    (fill …))`` — width is skipped (proxy, :2023) but the fill flag is emitted
    for RECTANGLE shapes regardless (:2028-2033).
    """

    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    fill: bool = False


PadPrimitive = (
    PrimLine | PrimRect | PrimArc | PrimCircle | PrimCurve | PrimPoly | PrimVector | PrimBbox
)


class Pad(KicadModel):
    """A ``pad`` token.

    Construction enforces the KiCad structural invariants (GAP-015, GAP-018).
    ``layers`` must name at least one layer, a through-hole pad must carry a
    drill, a ``roundrect`` pad must carry ``roundrect_rratio``, and
    ``remove_unused_layers`` belongs to a ``thru_hole`` pad only. See
    :meth:`_check_kicad_invariants` for the source citations.
    """

    number: str = ""
    pad_type: PadType = PadType.smd
    shape: PadShape = PadShape.rect
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    size: tuple[float, float] = (1.0, 1.0)
    rect_delta: tuple[float, float] | None = None
    drill: DrillInfo | None = None
    # Backdrill / tertiary drill (§3.2) — at pad top level, NOT inside padstack
    backdrill: BackdrillInfo | None = None
    tertiary_drill: BackdrillInfo | None = None
    # Post-machining (§2.4.2) — modeled but NOT emitted in library context per §2.4
    front_post_machining: PostMachining | None = None
    back_post_machining: PostMachining | None = None
    pad_property: PadProperty | None = None
    # Layer names stay plain strings, because a pad also accepts the wildcard
    # forms KiCad writes ("*.Cu", "*.Mask", formatLayers :1560-1610) alongside
    # canonical names. The discoverable valid set for the canonical names is
    # CANONICAL_LAYER_NAME_SET in common/layers.py, with is_valid_layer_name as
    # the membership test (GAP-021).
    layers: list[str] = []
    # remove_unused_layers is ALWAYS written for thru_hole pads (source line 1785)
    # Two bools on disk — do NOT convert to single enum (ADR-016 correction §3.8)
    remove_unused_layers: bool | None = None  # None = not written; bool = written
    keep_end_layers: bool | None = None
    # zone_layer_connections is board-only (not written in lib/.kicad_mod)
    zone_layer_connections: list[str] | None = None
    roundrect_rratio: float | None = None
    chamfer_ratio: float | None = None
    chamfer_positions: list[ChamferPosition] | None = None
    # net / pinfunction / pintype — OMIT on lib write per §2.4 #17-19
    net_code: int | None = None
    net_name: str | None = None
    pinfunction: str | None = None
    pintype: str | None = None
    die_length: float | None = None
    die_delay: float | None = None
    solder_mask_margin: float | None = None
    solder_paste_margin: float | None = None
    solder_paste_margin_ratio: float | None = None
    clearance: float | None = None
    zone_connect: int | None = None
    thermal_spoke_width: float | None = None
    thermal_spoke_angle: float | None = None
    thermal_gap: float | None = None
    custom_shape_clearance: CustomShapeClearance | None = None
    custom_anchor: CustomAnchor | None = None
    primitives: list[PadPrimitive] | None = None
    teardrop_params: TeardropParams | None = None
    tenting_front: bool | None = None
    tenting_back: bool | None = None
    id: str | None = None
    # Padstack (§3) — only when mode != NORMAL
    padstack: Padstack | None = None

    @model_validator(mode="after")
    def _check_kicad_invariants(self) -> Self:
        """Reject pad states KiCad cannot represent (GAP-015, GAP-018).

        Every rule below mirrors an unconditional emit in
        ``PCB_IO_KICAD_SEXPR::format( const PAD* )``. The load path never trips
        them: a scan of 30006 corpus files found zero violations.
        """
        if not self.layers:
            raise ValueError(
                "Pad needs at least one layer. KiCad writes (layers …) for every "
                "pad (format(PAD*) pcb_io_kicad_sexpr.cpp:1780), so a layerless "
                "pad has no copper, mask or paste."
            )
        if self.pad_type in (PadType.thru_hole, PadType.np_thru_hole) and self.drill is None:
            raise ValueError(
                f"Pad type {self.pad_type.value} needs a drill. Set drill=DrillInfo(width=…)."
            )
        if self.shape is PadShape.roundrect and self.roundrect_rratio is None:
            raise ValueError(
                "Pad shape roundrect needs roundrect_rratio. KiCad always writes "
                "the ratio for a roundrect or chamfered-rect pad "
                "(format(PAD*) pcb_io_kicad_sexpr.cpp:1808-1814)."
            )
        # No custom-shape rule. The audit proposed "custom shape needs
        # primitives", but format(PAD*) :2041-2057 prints a bare `(primitives)`
        # when the list is empty, so KiCad represents a primitive-free custom pad
        # and the padstack corpus exemplar contains one (ADR-048).
        if self.remove_unused_layers is not None and self.pad_type is not PadType.thru_hole:
            raise ValueError(
                "remove_unused_layers applies to a thru_hole pad only. KiCad writes "
                "the token for plated through-hole pads "
                "(format(PAD*) pcb_io_kicad_sexpr.cpp:1782-1785)."
            )
        return self


# ---------------------------------------------------------------------------
# 3D Model
# ---------------------------------------------------------------------------


class Model3D(KicadModel):
    """A ``model`` token for 3D model association."""

    path: str = ""
    visible: bool = True  # False => (hide yes)
    opacity: float | None = None  # written only when != 1.0
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Footprint attributes
# ---------------------------------------------------------------------------


class FootprintAttrs(KicadModel):
    """Footprint attributes (``attr`` token block).

    All flags are optional; the ``attr`` token is only written when
    at least one flag is set.
    """

    smd: bool = False
    through_hole: bool = False
    board_only: bool = False
    exclude_from_pos_files: bool = False
    exclude_from_bom: bool = False
    allow_missing_courtyard: bool = False
    dnp: bool = False
    allow_soldermask_bridges: bool = False

    def has_any(self) -> bool:
        """Return True if any attribute flag is set."""
        return any(
            [
                self.smd,
                self.through_hole,
                self.board_only,
                self.exclude_from_pos_files,
                self.exclude_from_bom,
                self.allow_missing_courtyard,
                self.dnp,
                self.allow_soldermask_bridges,
            ]
        )


# ---------------------------------------------------------------------------
# Footprint root
# ---------------------------------------------------------------------------


class FootprintPlacement(KicadModel):
    """Footprint-level ``(at x y [rot])`` placement (board context, GAP-033).

    Emitted only when present (board context); ``rot`` is suppressed when zero,
    mirroring ``format(FOOTPRINT*)`` :1219-1226.
    """

    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0


class Footprint(KicadFileMixin):
    """The root of a ``.kicad_mod`` footprint file."""

    fp_id: str = ""
    version: int = 20260206
    generator: str = "kicad-footprint-generator"
    generator_version: str | None = None  # None = omit; present in pcbnew-saved files

    locked: bool = False
    placed: bool = False  # (placed yes) — only when IsPlaced() (§2.1 #5)
    layer: str = "F.Cu"
    # Board-context identity/placement (GAP-033) — omitted in library context
    # (CTL_OMIT_UUIDS / CTL_OMIT_AT); present => board-context footprint.
    uuid: str | None = None
    at: FootprintPlacement | None = None
    description: str | None = None  # (descr "...") only when non-empty
    keywords: str | None = None  # (tags "...") only when non-empty

    # Properties (PCB_FIELDs): "Reference", "Value", then others
    properties: list[Property] = []

    # component_classes (§2.13) — only when non-empty
    component_classes: ComponentClasses | None = None

    # Footprint filters (ki_fp_filters) — emitted as property "ki_fp_filters"
    fp_filters: str | None = None

    # Board placement metadata (§2.1 #14-16) — omitted in lib context
    path: str | None = None
    sheetname: str | None = None
    sheetfile: str | None = None

    # Gate-swap unit info (§2.14) — only when non-empty
    unit_info: FootprintUnitInfo | None = None

    # Optional overrides
    solder_mask_margin: float | None = None
    solder_paste_margin: float | None = None
    solder_paste_margin_ratio: float | None = None
    clearance: float | None = None
    zone_connect: int | None = None

    attributes: FootprintAttrs = FootprintAttrs()

    # Stackup (§2.1 #24) — only when mode != EXPAND_INNER_LAYERS
    stackup: Stackup | None = None

    private_layers: list[str] | None = None
    net_tie_pad_groups: list[str] | None = None

    # duplicate_pad_numbers_are_jumpers is ALWAYS written (unconditional)
    duplicate_pad_numbers_are_jumpers: bool = False

    # jumper_pad_groups (§2.1 #28) — only when non-empty
    jumper_pad_groups: JumperPadGroups | None = None

    # Ordered GraphicalItems stream (GAP-001): fp_* shapes, fp_text/textbox AND
    # dimension/table/image/barcode, in a single collection that preserves file
    # order — mirroring KiCad's one sorted drawings loop (format(FOOTPRINT*)
    # :1425-1439). NOT four trailing lists; interleaving is significant.
    drawings: list[PcbDrawItem] = []

    # PCB_POINT items (§2.5)
    points: list[PcbPoint] = []

    # Pads
    pads: list[Pad] = []

    # Zones (§2.7)
    zones: list[Zone] = []

    # Groups
    groups: list[PcbGroup] = []

    # Variants (§2.12)
    variants: list[Variant] = []

    # embedded_fonts is ALWAYS written (unconditional)
    embedded_fonts: bool = False
    embedded_files: list[EmbeddedFile] = []

    # 3D models
    models: list[Model3D] = []

    # ------------------------------------------------------------------
    # Construct-from-scratch factory
    # ------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        fp_id: str,
        *,
        reference: str = "REF**",
        value: str | None = None,
    ) -> Self:
        """Build a footprint that already carries the mandatory Reference and Value.

        A bare ``Footprint()`` holds an empty ``properties`` list, so it writes no
        Reference and no Value field (GAP-020). This factory seeds both, using the
        defaults KiCad itself applies to a new footprint:

        * Reference: text ``REF**`` on ``F.SilkS``, visible
          (``board_design_settings.cpp:75``, ``footprint_libraries_utils.cpp:1270``).
        * Value: text equal to the footprint name on ``F.Fab``, visible
          (``board_design_settings.cpp:77``, ``footprint_libraries_utils.cpp:1310``).

        *value* defaults to the item-name part of *fp_id*, which is the fallback
        KiCad applies at ``footprint_libraries_utils.cpp:1310``. Set every other
        field on the returned model.

        KiCad acceptance of a footprint WITHOUT these fields stays UNVERIFIED, so
        the library adds them here and ships no validator that rejects their
        absence (plan iteration 11, GAP-020).
        """
        item_name = fp_id.split(":")[-1]
        return cls(
            fp_id=fp_id,
            properties=[
                Property(name="Reference", value=reference, layer="F.SilkS", visible=True),
                Property(
                    name="Value",
                    value=item_name if value is None else value,
                    layer="F.Fab",
                    visible=True,
                ),
            ],
        )

    # ------------------------------------------------------------------
    # KicadFileMixin hooks
    # ------------------------------------------------------------------

    @classmethod
    def _from_text(cls, text: str) -> Self:
        from eedesigncore.mod.loader import load_mod
        from eedesigncore.sexpr.parser import parse

        tree = parse(text)
        return load_mod(tree)  # type: ignore[return-value]

    def _to_lf_string(self) -> str:
        from eedesigncore.mod.writer import write_mod

        return write_mod(self)

    # ------------------------------------------------------------------
    # Compatibility read-only views over the ordered drawings stream.
    #
    # GAP-001 merged the former ``tables``/``images``/``barcodes``/
    # ``dimensions`` list fields into the single ordered ``drawings``
    # collection (KiCad's one sorted GraphicalItems loop). These properties
    # are read-only filtered views so callers can still query by kind; the
    # ordered ``drawings`` list remains the single source of truth (append
    # new items there to control interleaved position). See ADR-025.
    # ------------------------------------------------------------------

    @property
    def tables(self) -> list[Table]:
        return [d for d in self.drawings if isinstance(d, Table)]

    @property
    def images(self) -> list[ReferenceImage]:
        return [d for d in self.drawings if isinstance(d, ReferenceImage)]

    @property
    def barcodes(self) -> list[Barcode]:
        return [d for d in self.drawings if isinstance(d, Barcode)]

    @property
    def dimensions(self) -> list[Dimension]:
        return [d for d in self.drawings if isinstance(d, Dimension)]
