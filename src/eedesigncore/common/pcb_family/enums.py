"""PCB-family shared enumerations (pad type, shape, property and corner options).

These belong to the pad sub-grammar that ``.kicad_mod`` and ``.kicad_pcb`` share,
so they live in ``common/`` rather than a format sub-package, which keeps the
shared models import-acyclic (the ADR-015 pattern, applied to the PCB family).
``mod/enums.py`` re-exports them for backward compatibility.

GAP-021 added ``ChamferPosition``, ``CustomShapeClearance`` and ``CustomAnchor``.
Those three token sets were bare strings before, so a caller had no discoverable
valid set. Every value below is quoted from the KiCad writer.
"""

from enum import Enum


class PadType(str, Enum):
    """Pad attribute / type tokens.

    Values match KiCad token strings exactly (verified against
    ``pcb_io_kicad_sexpr.cpp`` PAD_ATTRIB switch).
    """

    thru_hole = "thru_hole"
    smd = "smd"
    connect = "connect"
    np_thru_hole = "np_thru_hole"


class PadShape(str, Enum):
    """Pad shape tokens.

    Values match KiCad token strings exactly (verified against
    ``pcb_io_kicad_sexpr.cpp`` shapeName lambda).

    Note: ``CHAMFERED_RECT`` and ``ROUNDRECT`` both map to ``"roundrect"``
    in the file format; the loader differentiates by presence of
    ``chamfer_ratio`` / ``chamfer`` tokens.
    """

    circle = "circle"
    rect = "rect"
    oval = "oval"
    trapezoid = "trapezoid"
    roundrect = "roundrect"
    custom = "custom"


class PadProperty(str, Enum):
    """Pad property tokens (``pad_prop_*``).

    Values match KiCad token strings exactly (verified against
    ``pcb_io_kicad_sexpr.cpp`` PAD_PROP switch).
    """

    bga = "pad_prop_bga"
    fiducial_glob = "pad_prop_fiducial_glob"
    fiducial_loc = "pad_prop_fiducial_loc"
    testpoint = "pad_prop_testpoint"
    heatsink = "pad_prop_heatsink"
    castellated = "pad_prop_castellated"
    mechanical = "pad_prop_mechanical"
    pressfit = "pad_prop_pressfit"


class ChamferPosition(str, Enum):
    """Corners listed inside a ``(chamfer …)`` token (GAP-021).

    The complete valid set holds these four tokens, and KiCad writes them in
    this order for a chamfered-rect pad
    (``format(PAD*)`` pcb_io_kicad_sexpr.cpp:1824-1836).
    """

    top_left = "top_left"
    top_right = "top_right"
    bottom_left = "bottom_left"
    bottom_right = "bottom_right"


class CustomShapeClearance(str, Enum):
    """Values of ``(clearance …)`` inside a custom pad ``(options …)`` block (GAP-021).

    KiCad writes ``convexhull`` for ``CUSTOM_SHAPE_ZONE_MODE::CONVEXHULL`` and
    ``outline`` otherwise, so the set holds exactly these two
    (``format(PAD*)`` pcb_io_kicad_sexpr.cpp:2045-2048).
    """

    convexhull = "convexhull"
    outline = "outline"


class CustomAnchor(str, Enum):
    """Values of ``(anchor …)`` inside a custom pad ``(options …)`` block (GAP-021).

    The anchor pad shape is ``rect`` for ``PAD_SHAPE::RECTANGLE`` and ``circle``
    for every other case, so the set holds exactly these two
    (``format(PAD*)`` pcb_io_kicad_sexpr.cpp:2066-2074).
    """

    circle = "circle"
    rect = "rect"


__all__ = [
    "ChamferPosition",
    "CustomAnchor",
    "CustomShapeClearance",
    "PadProperty",
    "PadShape",
    "PadType",
]
