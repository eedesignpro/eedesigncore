"""T5 (R8/R9): schematic embedded-files ownership + sym-writer LIBRARY inertness.

Grammar basis (all cited to the 10.0.3 clone):
- Per-symbol embedded data is INLINE in BOTH ``.kicad_sym`` and ``.kicad_sch``
  ``(lib_symbols)`` — ``SaveSymbol(.., aIncludeData=true)`` default
  (``sch_io_kicad_sexpr_lib_cache.h:55-56``; emission ``..._lib_cache.cpp:528``);
  embedded-files-ownership report section 6 (SCH verdict): per-container inline,
  NO consolidation, NO board-style deferral.
- The schematic-root ``(embedded_fonts yes|no)`` then optional ``(embedded_files)``
  is a SEPARATE collection written with data always inline
  (``sch_io_kicad_sexpr.cpp:534`` / ``:536-537`` ``WriteEmbeddedFiles(*m_out, true)``;
  sch-root-grammar report Part 1 rows 8-9).
- The ONLY lib-vs-sch ``SaveSymbol`` delta is the top-level symbol NAME
  (``sch_io_kicad_sexpr_lib_cache.cpp:366`` bare ``GetLibItemName()`` for LIBRARY;
  ``:369-378`` full ``Lib:Name`` substituted only when ``aLibName`` is non-empty,
  i.e. schematic context). Per-unit child names use ``GetLibItemName()`` in BOTH
  contexts. ADR-037 carries ``Lib:Name`` verbatim in ``Symbol.name``, so the sym
  writer needs NO ``SaveContext``/``lib_id`` threading and the LIBRARY path is
  byte-inert.

No corpus schematic carries a PER-SYMBOL ``(embedded_files)`` block (grep over the
135-file C:\\KiCad corpus: 0 files with a lib_symbols-nested block; 15 carry only
the schematic-ROOT block). The per-symbol path is therefore covered here by
source-cited unit tests; the root path additionally has a real corpus fixture
(``tests/fixtures/sch/t5_embedded_root.kicad_sch``).
"""

from __future__ import annotations

import inspect

from eedesigncore.common.models import EmbeddedFile
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.sch.models import Schematic
from eedesigncore.sch.writer import _dump_lib_symbols, dump_sch, write_sch
from eedesigncore.sym.models import SymbolLib
from eedesigncore.sym.writer import _dump_symbol, dump_sym


def _embedded() -> EmbeddedFile:
    return EmbeddedFile.from_bytes("logo.png", b"payload-bytes" * 8, kind="other")


# --- R8: per-symbol inline embedded data inside (lib_symbols) ----------------


class TestPerSymbolEmbeddedInline:
    def test_root_symbol_embeds_data_inline(self) -> None:
        ef = _embedded()
        sym = Symbol(
            name="Logo:LOGO",
            units=[SymbolUnit(name="LOGO_0_1")],
            embedded_fonts=True,
            embedded_files=[ef],
        )
        flat = _dump_lib_symbols(Schematic(lib_symbols=[sym]))
        # Per-symbol block is emitted with data INLINE (aIncludeData default true).
        assert "(embedded_fonts yes)" in flat
        assert "(embedded_files" in flat
        assert '(name "logo.png")' in flat
        assert "(data\n|" in flat  # pipe-chunked inline data, not a checksum stub
        assert f'(checksum "{ef.checksum}")' in flat

    def test_extends_symbol_embeds_data_inline(self) -> None:
        """A derived (extends) symbol also emits its embedded block inline."""
        ef = _embedded()
        sym = Symbol(name="Logo:LOGO_ALT", extends="LOGO", embedded_files=[ef])
        flat = _dump_lib_symbols(Schematic(lib_symbols=[sym]))
        assert "(extends" in flat
        assert "(embedded_files" in flat
        assert "(data\n|" in flat

    def test_per_symbol_and_root_blocks_are_distinct(self) -> None:
        """A symbol block and the schematic-root block coexist (NO consolidation)."""
        sym_ef = EmbeddedFile.from_bytes("sym.png", b"sym", kind="other")
        root_ef = EmbeddedFile.from_bytes("font.ttf", b"font", kind="font")
        sym = Symbol(name="A:B", units=[SymbolUnit(name="B_0_1")], embedded_files=[sym_ef])
        sch = Schematic(
            lib_symbols=[sym],
            embedded_fonts=True,
            embedded_files=[root_ef],
        )
        out = write_sch(sch)
        # Both files are present as distinct entries; the symbol's block lives
        # inside (lib_symbols), the root block after the item stream. The root
        # does NOT absorb the symbol's file (no board-style consolidation).
        assert '(name "sym.png")' in out
        assert '(name "font.ttf")' in out
        # Two separate (embedded_files ...) blocks, not one merged block: the
        # symbol's file precedes the root file, and there is no consolidation.
        assert out.index('(name "sym.png")') < out.index('(name "font.ttf")')
        assert out.count("(embedded_files") == 2

    def test_per_symbol_embedded_round_trips(self) -> None:
        ef = _embedded()
        sch = Schematic(
            lib_symbols=[
                Symbol(
                    name="Logo:LOGO",
                    units=[SymbolUnit(name="LOGO_0_1")],
                    embedded_fonts=True,
                    embedded_files=[ef],
                )
            ],
        )
        text = write_sch(sch)
        reparsed = Schematic.from_string(text)
        assert reparsed.lib_symbols[0].embedded_files[0].name == "logo.png"
        assert reparsed.lib_symbols[0].embedded_files[0].checksum == ef.checksum
        assert reparsed.lib_symbols[0].embedded_fonts is True
        assert write_sch(reparsed) == text


# --- R8: schematic-root (embedded_fonts)/(embedded_files) block --------------


class TestSchematicRootEmbedded:
    def test_root_block_round_trips(self) -> None:
        ef = _embedded()
        sch = Schematic(embedded_fonts=False, embedded_files=[ef])
        text = write_sch(sch)
        assert "(embedded_fonts no)" in text
        assert "(embedded_files" in text
        assert "(data |" in text  # inline data present (not a checksum-only stub)
        reparsed = Schematic.from_string(text)
        assert reparsed.embedded_files[0].checksum == ef.checksum
        assert write_sch(reparsed) == text

    def test_embedded_fonts_emitted_before_files(self) -> None:
        sch = Schematic(embedded_fonts=True, embedded_files=[_embedded()])
        out = dump_sch(sch)
        assert out.index("(embedded_fonts") < out.index("(embedded_files")


# --- R9: sym-writer LIBRARY path is byte-inert (ADR-037; no context threading) -


class TestSymWriterLibraryInert:
    def test_dump_symbol_has_no_context_or_lib_id_param(self) -> None:
        """ADR-037: no ``SaveContext``/``lib_id`` threading was added to the sym writer.

        Iteration 11.8 added ONE argument, the keyword-only ``by_name`` library map
        of ADR-073. It carries no name-qualification duty: ``Symbol.name`` still
        decides bare versus ``Lib:Name`` on its own, and the two tests below prove
        it. ``by_name`` exists only so a derived symbol can materialize its parent's
        keywords and filters, which ``LIB_SYMBOL::GetKeyWords`` and
        ``GetFPFilters`` do at save time (``lib_symbol.h:179-188,211-220``).

        The keyword-only ``legacy`` switch selects the KiCad 9 token layout from
        the file version. It carries no context and no lib id either.
        """
        signature = inspect.signature(_dump_symbol)
        params = list(signature.parameters)
        expected = ["sym", "by_name", "legacy"]
        assert params == expected, f"unexpected sym-writer signature: {params}"
        by_name = signature.parameters["by_name"]
        assert by_name.kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["legacy"].kind is inspect.Parameter.KEYWORD_ONLY
        assert by_name.default is None
        for banned in ("context", "lib_id"):
            assert banned not in params

    def test_name_emitted_verbatim_bare_vs_qualified(self) -> None:
        """LIBRARY (bare) vs SCHEMATIC (Lib:Name) is driven ONLY by ``Symbol.name``."""
        bare = Symbol(name="R", units=[SymbolUnit(name="R_0_1")])
        qualified = Symbol(name="Device:R", units=[SymbolUnit(name="R_0_1")])
        assert _dump_symbol(bare).startswith('(symbol "R"')
        assert _dump_symbol(qualified).startswith('(symbol "Device:R"')
        # Per-unit child name stays bare in both (matches unitName = GetLibItemName()).
        assert '(symbol "R_0_1"' in _dump_symbol(bare)
        assert '(symbol "R_0_1"' in _dump_symbol(qualified)

    def test_same_symbol_serializes_identically_in_both_contexts(self) -> None:
        """The exact bytes for a symbol are context-independent (shared ``_dump_symbol``)."""
        sym = Symbol(
            name="Device:R",
            units=[SymbolUnit(name="R_0_1")],
            embedded_files=[_embedded()],
        )
        lib_fragment = _dump_symbol(sym)
        assert lib_fragment in dump_sym(SymbolLib(symbols=[sym]))
        assert lib_fragment in _dump_lib_symbols(Schematic(lib_symbols=[sym]))
