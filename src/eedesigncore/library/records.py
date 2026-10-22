"""One part, as something a caller can list, rank, and place.

A :class:`PartRecord` is the common shape for every part this library can see,
whatever it came from:

- a symbol definition inside a ``.kicad_sym`` file, single-file or folder-form
  (:func:`eedesigncore.library.catalog.iter_library_records`), and
- a row supplied by a caller that read a part database
  (:func:`eedesigncore.library.database.record_from_row`).

The record carries data, never a judgement. It says what a part's fields hold;
it does not say whether the part is the right one. That line is ADR-100.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.common.sch_family.symbol import Symbol

PartSource = Literal["library", "database"]
"""Where a record came from: an on-disk symbol library, or a database row."""

DESCRIPTION_FIELD: Final = "Description"
FOOTPRINT_FIELD: Final = "Footprint"
VALUE_FIELD: Final = "Value"
DATASHEET_FIELD: Final = "Datasheet"
REFERENCE_FIELD: Final = "Reference"


class PartRecord(KicadModel):
    """One part an agent can find and then place.

    ``name`` is the item name inside its library, which is what a ``lib_id``
    carries after the colon. ``nickname`` is the library nickname, empty when
    the caller has not resolved one.

    ``fields`` holds every field by name, in the order the source gave them.
    For a symbol that is its ``property`` list; for a database row it is the
    mapped columns. ``keywords`` mirrors ``LIB_SYMBOL::m_keyWords``, which
    KiCad keeps off the field list (ADR-073).

    ``source_path`` names the file a library record was parsed from. It is the
    answer to "which file do I edit", which a folder-form library makes a real
    question: KiCad tracks the same thing in ``m_symbolSourceFiles``
    (``sch_io_kicad_sexpr_lib_cache.cpp:135-170``).
    """

    name: str
    nickname: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    keywords: str = ""
    footprint_filters: list[str] = Field(default_factory=list)
    source: PartSource = "library"
    source_path: str = ""
    sub_library: str = ""

    @property
    def lib_id(self) -> str:
        """``<nickname>:<name>``, or the bare name when there is no nickname."""
        return f"{self.nickname}:{self.name}" if self.nickname else self.name

    @property
    def description(self) -> str:
        """The ``Description`` field, empty when absent."""
        return self.fields.get(DESCRIPTION_FIELD, "")

    @property
    def footprint(self) -> str:
        """The ``Footprint`` field, empty when absent."""
        return self.fields.get(FOOTPRINT_FIELD, "")

    @property
    def value(self) -> str:
        """The ``Value`` field, empty when absent."""
        return self.fields.get(VALUE_FIELD, "")

    @classmethod
    def from_symbol(
        cls,
        symbol: Symbol,
        *,
        nickname: str = "",
        source_path: str = "",
    ) -> PartRecord:
        """Build a record from a parsed symbol definition.

        Every ``property`` becomes a field, keyed by name, last one winning.
        ``keywords`` and the footprint filters come off the model members, not
        the field list, which is where KiCad keeps them (ADR-073).
        """
        return cls(
            name=symbol.name,
            nickname=nickname,
            fields={field.name: field.value for field in symbol.fields},
            keywords=symbol.keywords or "",
            footprint_filters=list(symbol.fp_filters),
            source="library",
            source_path=source_path,
        )
