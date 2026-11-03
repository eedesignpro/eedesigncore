"""Load the footprint definitions a schematic-to-PCB update needs, from a library table.

KiCad loads each footprint named by a symbol through the footprint library table
(``BOARD_NETLIST_UPDATER::replaceFootprint`` and ``addNewFootprint`` call
``LoadFootprint``, ``board_netlist_updater.cpp:148-169, 326``). The update helper
takes the definitions as an argument instead, so a preview is tied to exactly the
definitions it was made with. This module does the loading step, and the result
goes to the helper as ``library_footprints``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from eedesigncore.common.base import KicadModel
from eedesigncore.mod.models import Footprint
from eedesigncore.project._update_common import placed_symbols
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.sch.models import Schematic
from eedesigncore.tables.resolver import (
    KICAD_ROW_TYPE,
    MAX_LIBRARY_SCAN_BYTES,
    ResolvedLibrary,
    ResolvedLibTable,
    path_within_roots,
)


class UpdateFootprintLoad(KicadModel):
    """The definitions found, the reason for each one not found, and notes.

    All three maps use the library identifier from the schematic, for example
    ``Resistor_SMD:R_0603_1608Metric``. Pass ``footprints`` to the update as
    ``library_footprints``. ``notes`` names the library that a search found for
    an identifier with no library name.
    """

    footprints: dict[str, Footprint] = {}
    missing: dict[str, str] = {}
    notes: dict[str, str] = {}


# Characters Windows refuses in a file name. A colon would open an alternate
# data stream of another file.
_FORBIDDEN = re.compile(r'[<>:"|?*\\/\x00-\x1f]')


def _roots(table: ResolvedLibTable, library: ResolvedLibrary) -> list[Path] | None:
    """The folders a read from *library* must stay inside, as ``scan_allowed`` picks them."""
    if library.read_roots is not None:
        return library.read_roots
    return table.global_roots if library.scope == "global" else table.project_roots


def _in_library(table: ResolvedLibTable, library: ResolvedLibrary, name: str) -> Path | str:
    """Return the ``.kicad_mod`` path for *name* in *library*, or the reason it is refused."""
    if library.disabled:
        return f"The library '{library.nickname}' is disabled."
    if library.type != KICAD_ROW_TYPE:
        return f"The library '{library.nickname}' has type '{library.type}', not KiCad."
    if not table.scan_allowed(library):
        return f"The library '{library.nickname}' is outside the allowed folders."
    path = library.path / f"{name}.kicad_mod"
    try:
        # KiCad looks the name up in its own list, where case counts. Windows
        # file names ignore case, so check the stored spelling.
        present = f"{name}.kicad_mod" in os.listdir(library.path)
    except OSError:
        present = False
    if not present or not path.is_file():
        return f"The library '{library.nickname}' has no footprint '{name}'."
    roots = _roots(table, library)
    if roots is not None and not path_within_roots(path, roots):
        return f"The footprint file '{name}' leads outside the allowed folders."
    if path.stat().st_size > MAX_LIBRARY_SCAN_BYTES:
        return f"The footprint file '{name}' is larger than {MAX_LIBRARY_SCAN_BYTES} bytes."
    return path


def _footprint_file(table: ResolvedLibTable, lib_id: str) -> tuple[Path | None, str, str]:
    """Return the path for *lib_id*, the reason when there is none, and a note."""
    nickname, separator, name = lib_id.partition(":") if ":" in lib_id else ("", "", lib_id)
    if separator and not nickname:
        return None, "The identifier has an empty library name.", ""
    if not name or name in (".", "..") or _FORBIDDEN.search(name):
        return None, "The footprint name is not a plain file name.", ""
    if nickname:
        library = table.get(nickname)
        if library is None:
            return None, f"The library table has no library '{nickname}'.", ""
        found = _in_library(table, library, name)
        return (found, "", "") if isinstance(found, Path) else (None, found, "")
    # KiCad searches every library in alphabetical order for a name with no
    # library (footprint_library_adapter.cpp:334-353) and warns.
    for library in sorted(table, key=lambda row: row.nickname.casefold()):
        if library.disabled:
            continue
        found = _in_library(table, library, name)
        if isinstance(found, Path):
            note = f"No library name. A search of every library found it in '{library.nickname}'."
            return found, "", note
    return None, "No library holds a footprint with this name.", ""


def load_update_footprints(
    source: Schematic | SheetHierarchy, lib_table: ResolvedLibTable
) -> UpdateFootprintLoad:
    """Load every footprint that a symbol in *source* names, through *lib_table*.

    *lib_table* is a footprint table from :func:`~eedesigncore.resolve_lib_table`
    with ``kind="fp"``. Symbols with an empty Footprint field are skipped. A file
    that cannot be read is reported in ``missing``, not raised. A name with no
    library name is searched for in every library, as KiCad does, and the
    library found is reported in ``notes``.

    Raises:
        ValueError: when *lib_table* is a symbol table.
    """
    if lib_table.kind != "fp":
        raise ValueError("load_update_footprints needs an 'fp' library table, got a 'sym' one.")
    wanted = sorted(
        {
            field.value
            for _, symbol in placed_symbols(source).values()
            for field in symbol.fields
            if field.name == "Footprint" and field.value
        }
    )
    found: dict[str, Footprint] = {}
    missing: dict[str, str] = {}
    notes: dict[str, str] = {}
    for lib_id in wanted:
        path, reason, note = _footprint_file(lib_table, lib_id)
        if path is None:
            missing[lib_id] = reason
            continue
        if note:
            notes[lib_id] = note
        try:
            found[lib_id] = Footprint.from_file(path)
        except (OSError, ValueError) as error:
            missing[lib_id] = f"The footprint file could not be read: {error}"
    return UpdateFootprintLoad(footprints=found, missing=missing, notes=notes)


__all__ = ["UpdateFootprintLoad", "load_update_footprints"]
