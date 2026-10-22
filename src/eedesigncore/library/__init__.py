"""Find a part: list a library, rank what is in it, read a part database.

Four modules, and one line between them and everything else. **This library
decides how part information LANDS in a schematic. It does not decide which
part.** Operator ruling Q26, 2026-09-15, recorded as ADR-100.

- :mod:`~eedesigncore.library.records` — :class:`PartRecord`, the one shape
  every part takes, from a file or from a database row.
- :mod:`~eedesigncore.library.catalog` — list one symbol library, or walk a
  whole merged ``sym-lib-table``.
- :mod:`~eedesigncore.library.search` — rank records against a free-text
  query with KiCad's own weights, and filter them with exact field tests.
- :mod:`~eedesigncore.library.dbl` — read a ``.kicad_dbl`` and report the
  declaration a caller needs to run the read itself.
- :mod:`~eedesigncore.library.database` — turn a supplied row into a symbol
  definition, the way KiCad's own database plugin does.

What this never does, and why, is in ADR-100: it does not connect to anything,
does not generate SQL, does not cache rows on disk, does not hold a credential,
and does not judge a part.
"""

from eedesigncore.library.catalog import (
    iter_library_records,
    iter_symbol_library,
    iter_table_records,
    library_item_names,
)
from eedesigncore.library.database import (
    database_records,
    definition_from_row,
    record_from_row,
)
from eedesigncore.library.dbl import (
    DatabaseCache,
    DatabaseDeclaration,
    DatabaseFieldMapping,
    DatabaseLibrary,
    DatabaseProperties,
    DatabaseSource,
    DatabaseSubLibrary,
    DatabaseTableRead,
    load_kicad_dbl,
    sanitize_database_key,
)
from eedesigncore.library.records import PartRecord, PartSource
from eedesigncore.library.search import (
    DEFAULT_FIELD_WEIGHT,
    FieldOperator,
    FieldTest,
    MatchKind,
    MatchReason,
    PartMatch,
    SearchTerm,
    score_record,
    search_records,
    search_terms,
    split_query,
)

__all__ = [
    "DEFAULT_FIELD_WEIGHT",
    "DatabaseCache",
    "DatabaseDeclaration",
    "DatabaseFieldMapping",
    "DatabaseLibrary",
    "DatabaseProperties",
    "DatabaseSource",
    "DatabaseSubLibrary",
    "DatabaseTableRead",
    "FieldOperator",
    "FieldTest",
    "MatchKind",
    "MatchReason",
    "PartMatch",
    "PartRecord",
    "PartSource",
    "SearchTerm",
    "database_records",
    "definition_from_row",
    "iter_library_records",
    "iter_symbol_library",
    "iter_table_records",
    "library_item_names",
    "load_kicad_dbl",
    "record_from_row",
    "sanitize_database_key",
    "score_record",
    "search_records",
    "search_terms",
    "split_query",
]
