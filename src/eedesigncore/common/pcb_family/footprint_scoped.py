"""Footprint-scoped PCB-family models (Tier B).

Tier B -- footprint-scoped: emitted only by ``format(FOOTPRINT)``, never at
board root; reused by ``pcb/`` only for embedded footprints, NOT board-root. Do
not conflate the footprint ``(stackup)``/``(variant)`` here with the board
``(setup (stackup))``/``formatVariants`` header (those are Tier A board-root and
live in ``pcb/`` at iteration 9). See arch-plan §4.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Self

from pydantic import model_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.common.pcb_family.enums import (
    ChamferPosition,
    CustomAnchor,
    PadShape,
)

# ---------------------------------------------------------------------------
# Enums (§8.2)
# ---------------------------------------------------------------------------


class PadstackMode(str, Enum):
    """Padstack mode enum (padstack.h PADSTACK::MODE).

    NORMAL is never serialized (absence == normal).
    """

    normal = "normal"
    front_inner_back = "front_inner_back"
    custom = "custom"


class PostMachiningMode(str, Enum):
    """Post-machining type (§2.4.2)."""

    counterbore = "counterbore"
    countersink = "countersink"


# ---------------------------------------------------------------------------
# Padstack sub-structures (§3)
# ---------------------------------------------------------------------------


class PostMachining(KicadModel):
    """Front or back post-machining specification (§2.4.2).

    ``angle`` holds the on-disk value scaled by 1/10: the loader divides the disk
    token by 10 and the writer multiplies by 10, an exact inverse pair that
    reproduces KiCad's bytes. KiCad's own disk token is its internal angle / 10
    (FormatDouble2Str(angle/10.0), format(PAD*) :1769), so it is NOT degrees.
    """

    mode: PostMachiningMode
    size: float | None = None
    depth: float | None = None
    angle: float | None = None  # degrees (already divided by 10 here)


class PadstackLayer(KicadModel):
    """Per-layer properties inside a (padstack ...) block (§3.1).

    Preserves the layer name exactly as read from file (ADR §8.3 #10).
    ``layer_name`` stays a plain ``str`` for that reason. The canonical layer-name
    table lives in ``common/layers.py`` (``CANONICAL_LAYER_NAME_SET``,
    ``is_valid_layer_name``), which is the discoverable valid set for GAP-021.
    ``shape``, ``chamfer_positions`` and ``custom_anchor`` became enums in
    iteration 11 (GAP-021); they were bare strings before.
    """

    layer_name: str
    shape: PadShape | None = None
    size: tuple[float, float] | None = None
    offset: tuple[float, float] | None = None
    rect_delta: tuple[float, float] | None = None
    roundrect_rratio: float | None = None
    chamfer_ratio: float | None = None
    chamfer_positions: list[ChamferPosition] | None = None
    primitives: list[Any] | None = None  # PadPrimitive types (avoid circular import)
    custom_anchor: CustomAnchor | None = None
    thermal_bridge_angle: float | None = None
    thermal_gap: float | None = None
    thermal_bridge_width: float | None = None
    clearance: float | None = None
    zone_connect: int | None = None
    tenting_front: bool | None = None
    tenting_back: bool | None = None


class Padstack(KicadModel):
    """Padstack block (§3.1).

    Only emitted when mode != NORMAL. ``layers`` must name at least one layer: a
    padstack exists to override per-layer geometry, so an empty list carries no
    information and writes a bare ``(padstack (mode …))`` KiCad cannot act on
    (GAP-015). Every one of the 542 padstacks in the corpus carries one layer or
    more.
    """

    mode: PadstackMode = PadstackMode.front_inner_back
    layers: list[PadstackLayer] = []

    @model_validator(mode="after")
    def _check_layers_present(self) -> Self:
        if not self.layers:
            raise ValueError(
                "Padstack needs at least one PadstackLayer. A padstack overrides "
                "per-layer pad geometry, so an empty layers list has no effect."
            )
        return self


class BackdrillInfo(KicadModel):
    """Secondary or tertiary drill (backdrill / tertiary_drill, §3.2).

    Emitted at pad top level (items 5-6 in §2.4), NOT inside padstack.
    """

    size: float = 0.0
    start_layer: str = ""
    end_layer: str = ""


# ---------------------------------------------------------------------------
# Variant (§2.12)
# ---------------------------------------------------------------------------


class VariantField(KicadModel):
    """A field override inside a variant."""

    name: str
    value: str


class Variant(KicadModel):
    """A ``(variant ...)`` footprint variant (§2.12).

    Each bool is only written when it differs from the base footprint value;
    each field only when its value differs. For library files, round-trip exactly.
    """

    name: str = ""
    dnp: bool | None = None
    exclude_from_bom: bool | None = None
    exclude_from_pos_files: bool | None = None
    fields: list[VariantField] = []


# ---------------------------------------------------------------------------
# ComponentClasses (§2.13)
# ---------------------------------------------------------------------------


class ComponentClasses(KicadModel):
    """A ``(component_classes ...)`` block (§2.13)."""

    classes: list[str] = []


# ---------------------------------------------------------------------------
# FootprintUnitInfo — gate-swap metadata (§2.14)
# ---------------------------------------------------------------------------


class FootprintGateUnit(KicadModel):
    """One ``(unit ...)`` inside the gate-swap ``(units ...)`` block."""

    name: str
    pins: list[str] = []


class FootprintUnitInfo(KicadModel):
    """Gate-swap unit metadata ``(units ...)`` (§2.14)."""

    units: list[FootprintGateUnit] = []


# ---------------------------------------------------------------------------
# Stackup (§2.1 #24)
# ---------------------------------------------------------------------------


class StackupLayer(KicadModel):
    """One ``(layer ...)`` inside a footprint ``(stackup ...)`` block."""

    name: str


class Stackup(KicadModel):
    """Footprint-level stackup block ``(stackup ...)`` (§2.1 #24)."""

    layers: list[StackupLayer] = []


# ---------------------------------------------------------------------------
# JumperPadGroups helper
# ---------------------------------------------------------------------------

# jumper_pad_groups is a list of lists of pad-number strings
JumperPadGroups = list[list[str]]


__all__ = [
    "PadstackMode",
    "PostMachiningMode",
    "PostMachining",
    "PadstackLayer",
    "Padstack",
    "BackdrillInfo",
    "VariantField",
    "Variant",
    "ComponentClasses",
    "FootprintGateUnit",
    "FootprintUnitInfo",
    "StackupLayer",
    "Stackup",
    "JumperPadGroups",
]
