"""SCH-family shared enumerations (pin electrical type / shape).

These belong to the ``Symbol`` sub-grammar embedded by both ``.kicad_sym`` and
``.kicad_sch``; they live in ``common/`` (never a format sub-package) so the
shared models stay import-acyclic (ADR-015). ``sym/enums.py`` re-exports them
for backward compatibility.
"""

from enum import Enum


class PowerType(str, Enum):
    """Scope of a power symbol, the value inside ``(power …)`` (GAP-021).

    ``SaveSymbol`` writes ``global`` for ``IsGlobalPower()`` and ``local``
    otherwise, so the set holds exactly these two
    (sch_io_kicad_sexpr_lib_cache.cpp:385-387). Absence of the token means the
    symbol is not a power symbol, which the model spells ``None``.
    """

    global_ = "global"
    local = "local"


class PinElectricalType(str, Enum):
    """Electrical type of a symbol pin.

    Token values match KiCad file format exactly.
    """

    input = "input"
    output = "output"
    bidirectional = "bidirectional"
    tri_state = "tri_state"
    passive = "passive"
    free = "free"
    unspecified = "unspecified"
    power_in = "power_in"
    power_out = "power_out"
    open_collector = "open_collector"
    open_emitter = "open_emitter"
    no_connect = "no_connect"


class PinShape(str, Enum):
    """Graphic shape of a symbol pin.

    Token values match KiCad file format exactly.
    Note: ``falling_edge_clock`` maps to the ``edge_clock_high`` token.
    """

    line = "line"
    inverted = "inverted"
    clock = "clock"
    inverted_clock = "inverted_clock"
    input_low = "input_low"
    clock_low = "clock_low"
    output_low = "output_low"
    # KiCad token is "edge_clock_high" for the falling-edge clock shape
    edge_clock_high = "edge_clock_high"
    non_logic = "non_logic"
