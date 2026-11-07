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
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from eedesigncore.common.base import KicadModel
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.exceptions import KicadParseError
from eedesigncore.library.records import PartRecord
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.sch.models import Schematic
from eedesigncore.sym.models import SymbolLib
from eedesigncore.tables.resolver import (
    HTTP_ROW_TYPE,
    KICAD_ROW_TYPE,
    ResolvedLibrary,
    ResolvedLibTable,
    symbol_library_files,
)

if TYPE_CHECKING:
    from eedesigncore.library.http import HttpSymbolLibrary

SymbolLoader = Callable[[str], Symbol | None]


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
    symbol_loader: SymbolLoader | None = None,
) -> Iterator[PartRecord]:
    """Yield one :class:`PartRecord` per definition in one symbol library.

    ``library`` is a resolved table row, which supplies the nickname, or a bare
    path, which does not. A bare path takes ``nickname`` when given.

    An ``HTTP`` row, or a ``.kicad_httplib`` path, reads its server and yields
    every part, as KiCad lists them; *symbol_loader* resolves each part's base
    symbol (see :func:`table_symbol_loader`). A server error raises
    :class:`~eedesigncore.library.http.HttpLibraryError`. Any other row type,
    such as ``Database``, yields nothing: a database library needs rows a
    caller supplies, per :mod:`eedesigncore.library.database`.

    A row outside its own read confinement also yields nothing. The check is
    :attr:`ResolvedLibrary.read_allowed`, which travels with the row, so
    ``library_item_names(table.get(nickname))`` cannot read a file the
    resolver refused.
    """
    if isinstance(library, ResolvedLibrary):
        if library.type not in (KICAD_ROW_TYPE, HTTP_ROW_TYPE):
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

    is_http = (
        library.type == HTTP_ROW_TYPE
        if isinstance(library, ResolvedLibrary)
        else path.suffix == ".kicad_httplib"
    )
    if is_http:
        http = _http_library(path, resolved_nickname, symbol_loader)
        for built, _category in http.iter_symbols():
            yield PartRecord.from_symbol(
                built.definition, nickname=resolved_nickname, source_path=str(path)
            )
        return
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

    ``HTTP`` rows read their servers, so an HTTP library of 100000 parts makes
    the walk read about 75 MB; pass ``nicknames`` to leave it out.

    Three kinds of row contribute nothing and raise nothing:

    - a row whose ``type`` is not ``KiCad`` or ``HTTP``, such as a ``Database`` row,
    - a row outside the read confinement, which
      :meth:`ResolvedLibTable.scan_allowed` refuses, and
    - a row whose path does not exist.

    ``nicknames`` limits the walk, which is the difference between 3 ms and a
    minute. ``strict`` is passed through to the per-file parse.
    """
    if table.kind != "sym":
        raise ValueError(f"iter_table_records needs a 'sym' table, got {table.kind!r}")

    wanted = None if nicknames is None else set(nicknames)

    loader = table_symbol_loader(table)
    for library in table.search_order(include_disabled=include_disabled):
        if library.type not in (KICAD_ROW_TYPE, HTTP_ROW_TYPE):
            continue
        if wanted is not None and library.nickname not in wanted:
            continue
        if not table.scan_allowed(library):
            continue
        if library.type == HTTP_ROW_TYPE:
            _check_http_scope(library)
        yield from iter_library_records(library, strict=strict, symbol_loader=loader)


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

    The nickname must resolve in *table*, the row must be a ``KiCad`` or an
    ``HTTP`` row, and the library must be inside the read confinement. Anything
    else returns ``None``: a database library cannot be resolved without rows a
    caller supplies, and a confinement miss stays indistinguishable from an
    absent part. An HTTP library asks its server, and a server error raises
    :class:`~eedesigncore.library.http.HttpLibraryError`.

    A folder-form library yields its definitions in sorted file order, so a
    name defined in two files resolves to the LAST one, which is the winner
    KiCad's shared name map also ends up with
    (``sch_io_kicad_sexpr_lib_cache.cpp:145-170``).
    """
    return _find_symbol(table, lib_id, None)


def _find_symbol(
    table: ResolvedLibTable, lib_id: str, http: dict[str, HttpSymbolLibrary] | None
) -> Symbol | None:
    nickname, separator, name = lib_id.partition(":")
    if not separator or not nickname or not name:
        return None
    library = table.get(nickname)
    if library is None or library.type not in (KICAD_ROW_TYPE, HTTP_ROW_TYPE):
        return None
    if not table.scan_allowed(library):
        return None
    if library.type == HTTP_ROW_TYPE:
        cache = {} if http is None else http
        if nickname not in cache:
            _check_http_scope(library)
            cache[nickname] = _http_library(
                library.path, nickname, lambda base: _find_symbol(table, base, cache)
            )
        return cache[nickname].load_symbol(name)

    found: Symbol | None = None
    for symbol, _file in iter_symbol_library(library.path):
        if symbol.name == name:
            found = symbol
    return found


def table_symbol_loader(table: ResolvedLibTable) -> Callable[[str], Symbol | None]:
    """Return a symbol loader bound to *table*, for the database transform.

    This is what :func:`eedesigncore.library.database.definition_from_row`
    calls to resolve a row's symbol reference. ``KiCad`` and ``HTTP`` rows are
    read. The loader keeps each result, each HTTP library and each base symbol
    for its own life and returns a copy each time; nothing outlives it. Make a
    new loader to see library changes.
    """

    http: dict[str, HttpSymbolLibrary] = {}
    found: dict[str, Symbol | None] = {}

    def load(lib_id: str) -> Symbol | None:
        # An HTTP library and the base symbols its parts name are kept for the
        # life of the loader: 100 parts on one base symbol parse it once.
        if lib_id not in found:
            found[lib_id] = _find_symbol(table, lib_id, http)
        symbol = found[lib_id]
        return None if symbol is None else symbol.model_copy(deep=True)

    return load


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _check_http_scope(library: ResolvedLibrary) -> None:
    """A project row may name only a server on this computer.

    A global row is the user's own configuration and may name any server. A
    project is often a repository the user did not write, so its HTTP library
    connects only to a loopback address.
    """
    if library.scope == "global":
        return
    from eedesigncore.library.http import HttpLibraryError, HttpLibrarySettings

    host = urlsplit(HttpLibrarySettings.from_file(library.path).source.root_url).hostname or ""
    if host.lower() not in _LOOPBACK:
        raise HttpLibraryError(
            f"The project library '{library.nickname}' names the server {host}. A project "
            "HTTP library may name only this computer; move the row to the global table."
        )


def _http_library(path: Path, nickname: str, loader: SymbolLoader | None) -> HttpSymbolLibrary:
    from eedesigncore.library.http import HttpSymbolLibrary

    return HttpSymbolLibrary.from_file(path, nickname=nickname, symbol_loader=loader)


class LibrarySymbolLoad(KicadModel):
    """The library definitions found, keyed by library id, and the reason for each one not found.

    Pass ``symbols`` to the symbol library update as ``library_symbols``.
    """

    symbols: dict[str, Symbol] = {}
    missing: dict[str, str] = {}


def load_library_symbols(
    source: Schematic | SheetHierarchy | Iterable[str], lib_table: ResolvedLibTable
) -> LibrarySymbolLoad:
    """Load the library definition of every library id, through *lib_table*.

    *source* is a schematic or a hierarchy, whose placed symbols name the ids,
    or the ids themselves. ``KiCad`` and ``HTTP`` rows are read, as KiCad's
    Update Symbols from Library reads them. A server error of an HTTP library
    is reported in ``missing``, not raised.

    Raises:
        ValueError: when *lib_table* is a footprint table.
    """
    from eedesigncore.library.http import HttpLibraryError
    from eedesigncore.project._update_common import placed_symbols

    if lib_table.kind != "sym":
        raise ValueError("load_library_symbols needs a 'sym' library table, got an 'fp' one.")
    if isinstance(source, Schematic | SheetHierarchy):
        wanted = sorted({symbol.lib_id for _, symbol in placed_symbols(source).values()})
    else:
        wanted = sorted(set(source))
    loader = table_symbol_loader(lib_table)
    found: dict[str, Symbol] = {}
    missing: dict[str, str] = {}
    for lib_id in wanted:
        nickname = lib_id.partition(":")[0]
        library = lib_table.get(nickname)
        if library is None:
            missing[lib_id] = f"The library table has no library '{nickname}'."
            continue
        if library.type not in (KICAD_ROW_TYPE, HTTP_ROW_TYPE):
            missing[lib_id] = f"The library '{nickname}' has type '{library.type}'."
            continue
        if not lib_table.scan_allowed(library):
            missing[lib_id] = f"The library '{nickname}' is outside the allowed folders."
            continue
        try:
            symbol = loader(lib_id)
        except HttpLibraryError as error:
            missing[lib_id] = str(error)
            continue
        if symbol is None:
            name = lib_id.partition(":")[2]
            missing[lib_id] = f"The library '{nickname}' has no symbol '{name}'."
        else:
            found[lib_id] = symbol
    return LibrarySymbolLoad(symbols=found, missing=missing)
