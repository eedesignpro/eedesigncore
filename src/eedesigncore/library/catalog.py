"""List what a symbol library holds, and walk a whole merged table.

Before this module the only question a caller could ask a library table was
"does this exact item name exist". KiCad itself lists every part of every
library into its chooser tree (``lib_tree_model_adapter.cpp``), so listing is
KiCad behaviour, not an extension.

**Cost.** Listing parses. Measured on the KiCad 10.0 stock libraries with
``SymbolLib.from_file``: 0.61 s for ``Device.kicad_sym`` (2.59 MB, 538
symbols), 1.31 s for ``Connector.kicad_sym`` (5.44 MB, 401 symbols), so about
0.24 s per megabyte. The whole stock set is 222 files and 244 MB, which is
about a minute. Every in-house folder-form library together is 110 files and
0.9 MB, which is 3 ms each.

So every function here is a generator and every one takes a nickname filter. A
caller that wants one library pays for one library. Nothing is cached: KiCad
keeps its own library cache in memory only and writes no cache file
(``sch_io_database.h:119-124``), and this library holds no cache at all
(operator ruling Q24, 2026-09-15). A caller that needs the list twice keeps
the list.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.exceptions import KicadParseError
from eedesigncore.library.records import PartRecord
from eedesigncore.sym.models import SymbolLib
from eedesigncore.tables.resolver import (
    KICAD_ROW_TYPE,
    ResolvedLibrary,
    ResolvedLibTable,
    symbol_library_files,
)


def iter_symbol_library(
    path: Path | str,
    *,
    strict: bool = False,
) -> Iterator[tuple[Symbol, Path]]:
    """Yield every symbol definition behind one library path, with its file.

    Both library shapes are read, per
    :func:`eedesigncore.tables.resolver.symbol_library_files`. A folder-form
    library yields the definitions of every file in sorted file order, so a
    name defined twice yields twice and the later file is the later yield.
    That matches KiCad, which lets the later file overwrite the earlier in its
    shared name map (``sch_io_kicad_sexpr_lib_cache.cpp:145-170``); a caller
    building a dictionary gets the same winner.

    ``strict`` raises :class:`~eedesigncore.exceptions.KicadParseError` on a
    file that will not parse. Left false, an unparsable file is skipped, which
    is what KiCad does for a folder-form library: it records the error, keeps
    every definition it did read, and only refuses to SAVE (``:176-198``).
    """
    for file in symbol_library_files(Path(path)):
        try:
            library = SymbolLib.from_file(file)
        except (KicadParseError, OSError, UnicodeDecodeError):
            if strict:
                raise
            continue
        for symbol in library.symbols:
            yield symbol, file


def iter_library_records(
    library: ResolvedLibrary | Path | str,
    *,
    nickname: str | None = None,
    strict: bool = False,
) -> Iterator[PartRecord]:
    """Yield one :class:`PartRecord` per definition in one symbol library.

    ``library`` is a resolved table row, which supplies the nickname, or a bare
    path, which does not. A bare path takes ``nickname`` when given.

    A row whose ``type`` is not ``KiCad`` yields nothing. A ``Database`` row
    needs rows a caller supplies, per
    :mod:`eedesigncore.library.database`; this library never connects
    (operator ruling Q24).

    A row outside its own read confinement also yields nothing. The check is
    :attr:`ResolvedLibrary.read_allowed`, which travels with the row, so
    ``library_item_names(table.get(nickname))`` cannot read a file the
    resolver refused.
    """
    if isinstance(library, ResolvedLibrary):
        if library.type != KICAD_ROW_TYPE:
            return
        if not library.read_allowed:
            # The row carries its own confinement, so no caller can forget to
            # ask (security review, iteration 20). A refused row reads as an
            # empty library, which is how a confinement miss is reported
            # everywhere else in this module.
            return
        path = library.path
        resolved_nickname = library.nickname if nickname is None else nickname
    else:
        path = Path(library)
        resolved_nickname = nickname or ""

    for symbol, file in iter_symbol_library(path, strict=strict):
        yield PartRecord.from_symbol(symbol, nickname=resolved_nickname, source_path=str(file))


def iter_table_records(
    table: ResolvedLibTable,
    *,
    nicknames: Iterable[str] | None = None,
    include_disabled: bool = False,
    strict: bool = False,
) -> Iterator[PartRecord]:
    """Yield every part of every readable library in a merged symbol table.

    Libraries are visited in :meth:`ResolvedLibTable.search_order`, so the
    project tier comes before the global tier, which is the order KiCad loads
    plugins in (``library_manager.cpp:1543-1551``).

    Three kinds of row contribute nothing and raise nothing:

    - a row whose ``type`` is not ``KiCad``, such as a ``Database`` row,
    - a row outside the read confinement, which
      :meth:`ResolvedLibTable.scan_allowed` refuses, and
    - a row whose path does not exist.

    ``nicknames`` limits the walk, which is the difference between 3 ms and a
    minute. ``strict`` is passed through to the per-file parse.
    """
    if table.kind != "sym":
        raise ValueError(f"iter_table_records needs a 'sym' table, got {table.kind!r}")

    wanted = None if nicknames is None else set(nicknames)

    for library in table.search_order(include_disabled=include_disabled):
        if library.type != KICAD_ROW_TYPE:
            continue
        if wanted is not None and library.nickname not in wanted:
            continue
        if not table.scan_allowed(library):
            continue
        yield from iter_library_records(library, strict=strict)


def library_item_names(
    library: ResolvedLibrary | Path | str,
    *,
    strict: bool = False,
) -> list[str]:
    """Return the definition names in one symbol library, in read order.

    Duplicates are dropped, keeping the first sighting, so the list is the
    nickname namespace a caller can pick from.
    """
    names: list[str] = []
    seen: set[str] = set()
    for record in iter_library_records(library, strict=strict):
        if record.name in seen:
            continue
        seen.add(record.name)
        names.append(record.name)
    return names


def find_symbol(table: ResolvedLibTable, lib_id: str) -> Symbol | None:
    """Return the definition named by ``<nickname>:<name>``, or ``None``.

    The nickname must resolve in *table*, the row must be a ``KiCad`` row, and
    the library must be inside the read confinement. Anything else returns
    ``None``: a database library cannot be resolved without rows a caller
    supplies, and a confinement miss stays indistinguishable from an absent
    part.

    A folder-form library yields its definitions in sorted file order, so a
    name defined in two files resolves to the LAST one, which is the winner
    KiCad's shared name map also ends up with
    (``sch_io_kicad_sexpr_lib_cache.cpp:145-170``).
    """
    nickname, separator, name = lib_id.partition(":")
    if not separator or not nickname or not name:
        return None

    library = table.get(nickname)
    if library is None or library.type != KICAD_ROW_TYPE:
        return None
    if not table.scan_allowed(library):
        return None

    found: Symbol | None = None
    for symbol, _file in iter_symbol_library(library.path):
        if symbol.name == name:
            found = symbol
    return found


def table_symbol_loader(table: ResolvedLibTable) -> Callable[[str], Symbol | None]:
    """Return a symbol loader bound to *table*, for the database transform.

    This is what :func:`eedesigncore.library.database.definition_from_row`
    calls to resolve a row's symbol reference. It parses the library on every
    call and holds no cache, per operator ruling Q24, so a caller placing many
    parts from the same library should wrap it in its own memo.
    """

    def load(lib_id: str) -> Symbol | None:
        return find_symbol(table, lib_id)

    return load
