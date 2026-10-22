"""Pydantic models for ``fp-lib-table`` and ``sym-lib-table`` files.

KiCad 10 serialises both tables via the single unified ``LIBRARY_TABLE`` class
(``common/libraries/library_table.cpp``); the two file kinds differ only in the
root token (``fp_lib_table`` / ``sym_lib_table``). See the T3 grammar audit
``context/audit/verify-lib-table-grammar-2026-07-13.md`` for the file:line
citations these models honour.

Version note (ADR-004 nuance): KiCad's writer hardcodes ``(version 7)`` for
every table kind (``library_table.cpp:190``) regardless of the version it read.
This library matches that behaviour - :class:`LibTable` records the version read
from disk in :attr:`LibTable.version` (``None`` when absent), but the writer
always emits ``(version 7)``. A version-absent input therefore round-trips with
``(version 7)`` added: byte-identical to what KiCad 10 would write, not
byte-identical to arbitrary input.
"""

from __future__ import annotations

from typing import Literal, Self

from eedesigncore.common.base import KicadModel
from eedesigncore.common.io_mixin import KicadFileMixin

LibTableKind = Literal["fp", "sym"]

_ROOT_TOKENS: dict[LibTableKind, str] = {
    "fp": "fp_lib_table",
    "sym": "sym_lib_table",
}


class LibTableRow(KicadModel):
    """A single ``(lib ...)`` row in a library table.

    All five string fields are always emitted on write (empty ``options``/
    ``descr`` become ``(options "")`` / ``(descr "")``); ``disabled`` and
    ``hidden`` map to inline ``(disabled)`` / ``(hidden)`` markers emitted only
    when set (disabled before hidden). URI backslashes are normalised to forward
    slashes on write, matching ``library_table.cpp:194``.
    """

    name: str
    type: str
    uri: str
    options: str = ""
    descr: str = ""
    disabled: bool = False
    hidden: bool = False


class LibTable(KicadFileMixin):
    """A parsed ``fp-lib-table`` or ``sym-lib-table`` file.

    ``kind`` selects the root token (``fp`` -> ``fp_lib_table``, ``sym`` ->
    ``sym_lib_table``). Rows are a list (not a dict): KiCad preserves duplicate
    nicknames at the file/model layer (``addRowFromIR`` performs no
    uniqueness check), so this model does too.

    ``version`` records the integer version read from disk (``None`` when the
    optional ``(version ...)`` token was absent or non-integer). The writer
    always emits ``(version 7)`` regardless - see the module docstring.
    """

    kind: LibTableKind
    version: int | None = None
    rows: list[LibTableRow] = []

    @property
    def root_token(self) -> str:
        """The S-expression root token for this table kind."""
        return _ROOT_TOKENS[self.kind]

    @classmethod
    def _from_text(cls, text: str) -> Self:
        from eedesigncore.sexpr.parser import parse
        from eedesigncore.tables.loader import load_lib_table

        tree = parse(text)
        return load_lib_table(tree)  # type: ignore[return-value]

    def _to_lf_string(self) -> str:
        from eedesigncore.tables.writer import write_lib_table

        return write_lib_table(self)
