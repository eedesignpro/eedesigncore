"""Resolve mask and paste clearances from the saved board, without changing it.

KiCad: pcbnew/pad.cpp, PAD::GetSolderMaskExpansion/GetSolderPasteMargin.
Custom design rules require the DRC engine and are outside this file-only API.
"""

from __future__ import annotations

from eedesigncore.common.kimath import kiround
from eedesigncore.common.pcb_family.enums import PadShape
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.mod.models import Footprint, Pad
from eedesigncore.mod.pad_geometry import pad_layer_geometry
from eedesigncore.pcb.models import Setup


def pad_aperture_margin(
    pad: Pad,
    layer: str,
    *,
    footprint: Footprint | None = None,
    setup: Setup | None = None,
    version: int | None = None,
) -> tuple[float, float]:
    """Return per-side X/Y clearances in pad axes, in millimeters.

    An absent value inherits. Explicit zero stops inheritance in KiCad 9+.
    Absolute paste clearance and relative paste clearance inherit independently.
    """
    if layer not in {"F.Mask", "B.Mask", "F.Paste", "B.Paste"}:
        return (0.0, 0.0)
    if not any(name.endswith(".Cu") for name in pad.layers):
        return (0.0, 0.0)

    def resolve(name: str, default: float | None) -> float:
        values = (getattr(pad, name), getattr(footprint, name) if footprint else None)
        for value in values:
            if value is not None and not (
                version is not None and version <= 20240201 and value == 0
            ):
                return float(value)
        return default or 0.0

    geometry = pad_layer_geometry(pad, layer)
    size = point_to_iu(geometry.size)
    if layer.endswith(".Mask"):
        margin = kiround(
            resolve("solder_mask_margin", setup.pad_to_mask_clearance if setup else 0) * 1e6
        )
        margin = max(margin, -int(min(size) / 2))
        return (to_mm(margin), to_mm(margin))

    absolute = kiround(
        resolve("solder_paste_margin", setup.pad_to_paste_clearance if setup else 0) * 1e6
    )
    ratio = resolve(
        "solder_paste_margin_ratio", setup.pad_to_paste_clearance_ratio if setup else 0
    )
    margins = (absolute + kiround(size[0] * ratio), absolute + kiround(size[1] * ratio))
    if geometry.shape is not PadShape.custom:
        margins = (max(margins[0], -int(size[0] / 2)), max(margins[1], -int(size[1] / 2)))
    return (to_mm(margins[0]), to_mm(margins[1]))
