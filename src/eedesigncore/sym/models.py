"""Pydantic v2 domain models for KiCad symbol library files (.kicad_sym).

The embeddable ``Symbol`` sub-grammar (``Symbol``/``SymbolUnit``, pins, shapes,
text) lives in ``common/sch_family/`` (arch-plan section 2c). This module retains
only the format-root wrapper ``SymbolLib``.
"""

from __future__ import annotations

from typing import Self

from eedesigncore.common.io_mixin import KicadFileMixin
from eedesigncore.common.sch_family.symbol import Symbol


class SymbolLib(KicadFileMixin):
    """The root of a ``.kicad_sym`` symbol library file."""

    version: int = 20251024
    generator: str = "kicad_symbol_editor"
    generator_version: str = ""
    symbols: list[Symbol] = []
    # Note: embedded fonts/files are per-symbol in .kicad_sym, never at the
    # library root. KiCad's Save writes only the header + symbols (Save/
    # formatLibraryHeader, sch_io_kicad_sexpr_lib_cache.cpp:208-348, :872-878)
    # and its root parser rejects any other token
    # (Expecting("symbol, generator, or generator_version")). The former dead
    # SymbolLib.embedded_fonts/embedded_files fields were removed (GAP-027).

    # ------------------------------------------------------------------
    # KicadFileMixin hooks
    # ------------------------------------------------------------------

    @classmethod
    def _from_text(cls, text: str) -> Self:
        from eedesigncore.sexpr.parser import parse
        from eedesigncore.sym.loader import load_sym

        tree = parse(text)
        return load_sym(tree)  # type: ignore[return-value]

    def _to_lf_string(self) -> str:
        from eedesigncore.sym.writer import write_sym

        return write_sym(self)
