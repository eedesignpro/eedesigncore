"""File-version switches between the KiCad 9 and KiCad 10 schematic layouts.

KiCad 10 is the layout this library writes. A file that KiCad 9 wrote carries
an older ``(version …)`` token, and a byte-identical round trip of that file
needs the KiCad 9 spelling of five tokens and one sort order
(``docs/agent/schematics.md § KiCad 9 files``). The two constants are the last
release-version values of KiCad 9 (9.0.9 ``eeschema/sch_file_versions.h:55``
and ``:117``). Every version KiCad 10 wrote, nightlies included, is later.
"""

from __future__ import annotations

KICAD9_SYMBOL_LIB_VERSION = 20241209
KICAD9_SCHEMATIC_VERSION = 20250114


def is_legacy_symbol_lib(version: int) -> bool:
    """True for a ``.kicad_sym`` version that KiCad 9 or older wrote."""
    return version <= KICAD9_SYMBOL_LIB_VERSION


def is_legacy_schematic(version: int) -> bool:
    """True for a ``.kicad_sch`` version that KiCad 9 or older wrote."""
    return version <= KICAD9_SCHEMATIC_VERSION
