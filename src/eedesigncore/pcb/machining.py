"""Resolve saved backdrill depths against caller-supplied physical copper elevations."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from eedesigncore.common.pcb_family.footprint_scoped import BackdrillInfo

BackdrillDepthPolicy = Literal["native", "between-layers"]


@dataclass(frozen=True)
class CopperDepthInterval:
    """One copper layer in millimeters, with Z increasing toward the board front."""

    layer: str
    bottom_mm: float
    top_mm: float


@dataclass(frozen=True)
class BackdrillDepth:
    """A flat-ended opening in the same coordinate frame as the supplied layers."""

    entry_layer: str
    stop_layer: str
    diameter_mm: float
    bottom_mm: float
    top_mm: float
    policy: BackdrillDepthPolicy
    next_layer: str | None


def resolve_backdrill_depth(
    drill: BackdrillInfo,
    copper: Sequence[CopperDepthInterval],
    *,
    policy: BackdrillDepthPolicy,
    surface_bottom_mm: float,
    surface_top_mm: float,
    entry_extension_mm: float,
    axial_margin_mm: float = 0.0,
) -> BackdrillDepth:
    """Resolve depth without reconstructing the stack or changing the source.

    Supply copper front to back in the constructor's physical frame.
    Native depth includes both endpoint layers and the supplied axial margin.
    Between-layer depth ends midway between the stop layer and the next layer.
    Its floor has no axial margin. Surface bounds include any material to cut.
    Entry extension is numerical clearance outside those surfaces, not extra depth.
    """
    if policy not in ("native", "between-layers"):
        raise ValueError("Unknown backdrill depth policy.")
    dimensions = (
        drill.size,
        surface_bottom_mm,
        surface_top_mm,
        entry_extension_mm,
        axial_margin_mm,
    )
    if not all(math.isfinite(value) for value in dimensions):
        raise ValueError("Backdrill dimensions must be finite.")
    if drill.size <= 0 or surface_bottom_mm >= surface_top_mm:
        raise ValueError("Backdrill diameter and surface interval must be positive.")
    if entry_extension_mm < 0 or axial_margin_mm < 0:
        raise ValueError("Backdrill extensions must not be negative.")
    if policy == "between-layers" and axial_margin_mm != 0:
        raise ValueError("Between-layer depth does not accept an axial margin.")
    names = [row.layer for row in copper]
    if (
        len(names) < 2
        or names[0] != "F.Cu"
        or names[-1] != "B.Cu"
        or len(set(names)) != len(names)
    ):
        raise ValueError("Supply distinct copper layers from F.Cu through B.Cu.")
    for row in copper:
        if (
            not row.layer.endswith(".Cu")
            or not all(math.isfinite(value) for value in (row.bottom_mm, row.top_mm))
            or row.bottom_mm >= row.top_mm
            or row.bottom_mm < surface_bottom_mm
            or row.top_mm > surface_top_mm
        ):
            raise ValueError("Copper intervals must be finite and inside the surface bounds.")
    if any(a.bottom_mm < b.top_mm for a, b in zip(copper, copper[1:], strict=False)):
        raise ValueError("Copper intervals must not overlap or reverse order.")
    if drill.start_layer not in names or drill.end_layer not in names:
        raise ValueError("Backdrill names an unknown copper layer.")
    first = names.index(drill.start_layer)
    stop = names.index(drill.end_layer)
    start_row, stop_row = copper[first], copper[stop]
    next_layer = None
    if policy == "native":
        bottom = min(start_row.bottom_mm, stop_row.bottom_mm) - axial_margin_mm
        top = max(start_row.top_mm, stop_row.top_mm) + axial_margin_mm
        if "F.Cu" in (drill.start_layer, drill.end_layer):
            top = max(top, surface_top_mm + entry_extension_mm + axial_margin_mm)
        if "B.Cu" in (drill.start_layer, drill.end_layer):
            bottom = min(bottom, surface_bottom_mm - entry_extension_mm - axial_margin_mm)
    else:
        if drill.start_layer not in ("F.Cu", "B.Cu"):
            raise ValueError("Between-layer backdrills must enter an outer copper side.")
        direction = 1 if drill.start_layer == "F.Cu" else -1
        following = stop + direction
        if not 0 <= following < len(copper):
            raise ValueError("Backdrill has no copper layer beyond its stop layer.")
        next_row = copper[following]
        next_layer = next_row.layer
        if direction == 1:
            bottom = (stop_row.bottom_mm + next_row.top_mm) / 2
            top = surface_top_mm + entry_extension_mm
        else:
            bottom = surface_bottom_mm - entry_extension_mm
            top = (stop_row.top_mm + next_row.bottom_mm) / 2
    return BackdrillDepth(
        drill.start_layer, drill.end_layer, drill.size, bottom, top, policy, next_layer
    )
