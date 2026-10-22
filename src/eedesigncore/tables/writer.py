"""Writer for ``fp-lib-table`` / ``sym-lib-table`` files.

Emits a flat S-expression string then reshapes it with the ``LIBRARY_TABLE``
prettify mode (1 TAB indent, each ``(lib ...)`` row forced onto a single line,
root close paren at column 0, single trailing ``\\n``). This mirrors KiCad's
``LIBRARY_TABLE::Save`` -> ``PRETTIFIED_FILE_OUTPUTFORMATTER`` pipeline
(T3 audit §2-§3).

Always emits ``(version 7)`` and all five row keys in fixed order
(name, type, uri, options, descr), matching ``library_table.cpp`` exactly.
"""

from __future__ import annotations

from eedesigncore.common.writers import q
from eedesigncore.sexpr.writer import prettify
from eedesigncore.tables.models import LibTable, LibTableRow

# KiCad hardcodes (version 7) for every table kind (library_table.cpp:190).
_TABLE_VERSION = 7


def _dump_row(row: LibTableRow) -> str:
    """Emit a flat ``(lib ...)`` token; URI backslashes normalised to '/'."""
    uri = row.uri.replace("\\", "/")
    parts = [
        "(lib",
        f"(name {q(row.name)})",
        f"(type {q(row.type)})",
        f"(uri {q(uri)})",
        f"(options {q(row.options)})",
        f"(descr {q(row.descr)})",
    ]
    if row.disabled:
        parts.append("(disabled)")
    if row.hidden:
        parts.append("(hidden)")
    return "".join(parts) + ")"


def dump_lib_table(table: LibTable) -> str:
    """Emit a flat (unindented) S-expression string for *table*."""
    parts = [f"({table.root_token}", f"(version {_TABLE_VERSION})"]
    for row in table.rows:
        parts.append(_dump_row(row))
    return "".join(parts) + ")"


def write_lib_table(table: LibTable) -> str:
    """Return the canonical LF-only serialization of *table*."""
    flat = dump_lib_table(table)
    return prettify(flat, mode="LIBRARY_TABLE")
