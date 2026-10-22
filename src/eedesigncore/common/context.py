"""Serialization-context flag for footprint/symbol writers.

`SaveContext` is the eedesigncore analog of KiCad's ``m_ctl`` bitmask
(`pcb_io_kicad_sexpr.h:217-222`). KiCad runs the SAME ``format(const FOOTPRINT*)``
serializer in every context; the only difference is which ``CTL_*`` bits are set.
Rather than replicate the five independent bits, this enum models the two fixed,
mutually-exclusive presets KiCad actually uses on file-save paths
(``CTL_FOR_LIBRARY`` vs ``CTL_FOR_BOARD``), plus the schematic ``lib_symbols``
context for the sch family.

CTL analogs
-----------
- ``LIBRARY``   -> ``CTL_FOR_LIBRARY``: standalone ``.kicad_mod`` / ``.kicad_sym``.
- ``BOARD``     -> ``CTL_FOR_BOARD``: a footprint embedded in a ``.kicad_pcb``.
- ``SCHEMATIC`` -> the ``.kicad_sch`` ``(lib_symbols)`` symbol context.

Guard table (extract/inject spec section 1.1) - which tokens each context emits.
`LIBRARY` is the byte-identity-preserving default; `BOARD`/`SCHEMATIC` gate the
board/schematic-only tokens that library files omit:

===========================  ==============  =========================================
KiCad bit                    Context        Effect
===========================  ==============  =========================================
CTL_OMIT_LIBNAME             LIBRARY        emit bare footprint name (not ``lib:name``)
CTL_OMIT_FOOTPRINT_VERSION   LIBRARY        emit version/generator block
CTL_OMIT_UUIDS               BOARD          emit footprint-level ``(uuid)``
CTL_OMIT_AT                  BOARD          emit placement ``(at x y [rot])``
CTL_OMIT_PATH                BOARD          emit ``(path)`` when non-empty
CTL_OMIT_PAD_NETS            BOARD          emit pad/zone net/pinfunction/pintype
embedded-files deferral      not BOARD      inline footprint embedded ``(data ...)``
===========================  ==============  =========================================

Scaffold status (iteration 8, T18)
----------------------------------
This iteration is SCAFFOLD ONLY. The parameter is accepted, validated, and
documented, but no writer BEHAVIOR changes: only ``SaveContext.LIBRARY`` is
implemented. Passing ``BOARD`` or ``SCHEMATIC`` raises ``NotImplementedError``
(fail loud, not silently emit library output). Board/schematic emission is
activated in iteration 9/10 alongside the ``.kicad_pcb`` / ``.kicad_sch`` writers
(see ADR-020 and the extract/inject spec section 6 phasing).
"""

from enum import Enum


class SaveContext(Enum):
    """Serialization context - the eedesigncore analog of KiCad's m_ctl mask."""

    LIBRARY = "library"  # standalone .kicad_mod / .kicad_sym  (CTL_FOR_LIBRARY)
    BOARD = "board"  # footprint embedded in .kicad_pcb        (CTL_FOR_BOARD)
    SCHEMATIC = "schematic"  # symbol embedded in .kicad_sch (lib_symbols)
