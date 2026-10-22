"""``fp-lib-table`` / ``sym-lib-table`` loader, writer, and nickname resolver."""

from eedesigncore.tables.env import (
    LibTableEnvironment,
    default_kicad_install_root,
    default_user_settings_dir,
    is_kicad_env_var,
    kicad_env_vars,
)
from eedesigncore.tables.loader import load_lib_table
from eedesigncore.tables.models import LibTable, LibTableKind, LibTableRow
from eedesigncore.tables.resolver import (
    PLACEHOLDER_NICKNAME,
    LibTableResolver,
    LibTableScope,
    ResolvedLibrary,
    ResolvedLibTable,
    SkippedLibTable,
    qualify_item_name,
    resolve_lib_table,
)
from eedesigncore.tables.writer import dump_lib_table, write_lib_table

__all__ = [
    "LibTable",
    "LibTableRow",
    "LibTableKind",
    "LibTableScope",
    "load_lib_table",
    "dump_lib_table",
    "write_lib_table",
    "LibTableEnvironment",
    "LibTableResolver",
    "ResolvedLibTable",
    "ResolvedLibrary",
    "SkippedLibTable",
    "resolve_lib_table",
    "qualify_item_name",
    "default_user_settings_dir",
    "default_kicad_install_root",
    "is_kicad_env_var",
    "kicad_env_vars",
    "PLACEHOLDER_NICKNAME",
]
