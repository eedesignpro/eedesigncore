"""Read a ``.kicad_dbl`` part-database library and report what it declares.

This module reads the file and nothing else. **It never connects, never
authenticates, never issues a statement, and never holds a credential.**
Operator ruling Q24, 2026-09-15, recorded as ADR-100. There is no KiCad cache
to lean on either: a database library caches rows in memory only and writes no
file (``sch_io_database.h:119-124``).

The handoff to a caller is one object, :class:`DatabaseDeclaration`. It names
the driver, the connection parts with every secret withheld, and per
sub-library the table, the key column, the symbol column, the footprint column
and the full column list. That is everything needed to run the read. KiCad
itself issues only two statement shapes
(``common/database/database_connection.cpp:414-417``, ``:542-544``)::

    SELECT <column list> FROM "<table>"
    SELECT <column list> FROM "<table>" WHERE "<key column>" = ?

The quote character is whatever the driver reports through
``SQL_IDENTIFIER_QUOTE_CHAR`` (``:287-311``), which is one reason this library
refuses to write the statement: the quoting rule belongs to the driver, and
owning the statement would mean owning the dialect and the injection risk for
no gain in KiCad fidelity.

The schema is ``DATABASE_LIB_SETTINGS``
(``common/database/database_lib_settings.cpp:51-181``). Every default here is
KiCad's default from that file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.exceptions import KicadParseError
from eedesigncore.tables.env import read_text_capped

MAX_DBL_BYTES: Final = 16 * 1024 * 1024
"""Read cap for a ``.kicad_dbl`` file. A measured production file is 213 KB."""

WITHHELD: Final = "<withheld>"
"""What a secret reads as in anything this module returns."""

SECRET_KEYS: Final = frozenset(
    {
        "pwd",
        "password",
        "secret",
        "clientsecret",
        "accesstoken",
        "token",
        "apikey",
        "accesskey",
        "privatekey",
        "sharedaccesssignature",
        "sig",
    }
)
"""Connection-string keys whose value is never copied out.

Compared after :func:`_secret_key_form`, which lowercases and drops every
character that is not a letter or a digit. So ``PWD``, ``Password``,
``Client Secret``, ``Client-Secret`` and ``client_secret`` all collapse onto an
entry here. The list grew after the iteration-20 security review found
``Client Secret`` and ``API Key`` passing through unredacted.
"""

MAX_SUB_LIBRARIES: Final = 4096
"""Cap on ``libraries`` entries read from one ``.kicad_dbl``.

A measured production file holds 19. A 16 MB file can hold hundreds of thousands of small
objects, and each one costs a per-row loop later, so the count is capped the
way every other untrusted-input path in this library is (ADR-047 point 2).
"""

MAX_FIELD_MAPPINGS: Final = 4096
"""Cap on ``fields`` entries read from one sub-library. The real files hold 43."""

MAX_JSON_DEPTH: Final = 32
"""Nesting depth a ``.kicad_dbl`` may reach before it is refused.

``json.loads`` recurses once per level, so a file well under
:data:`MAX_DBL_BYTES` made of nested brackets can exhaust the interpreter stack
before the byte cap applies. A ``.kicad_dbl`` has a fixed shallow schema; the
real files reach depth 4.
"""

DEFAULT_TIMEOUT_SECONDS: Final = 2
DEFAULT_CACHE_MAX_SIZE: Final = 256
DEFAULT_CACHE_MAX_AGE: Final = 10
"""KiCad's defaults (``database_lib_settings.cpp:63``, ``:139-142``)."""

_ILLEGAL_ITEM_NAME_CHARS: Final = frozenset(':\t\n\r\\<>"')
"""What ``LIB_ID::FixIllegalChars`` replaces with an underscore.

``LIB_ID::isLegalChar`` (``common/lib_id.cpp:209-234``) refuses the colon, the
three whitespace controls, and the four characters illegal in a file name. A
space is legal.
"""


def sanitize_database_key(raw_key: str) -> str:
    """Port ``LIB_ID::FixIllegalChars`` for an item name (``lib_id.cpp:192-206``).

    Every illegal character becomes one underscore. This is what turns a
    database key into the name half of a placed ``lib_id``, so
    ``PN-01582/116981`` and ``PN-01582:116981`` both land as
    ``PN-01582_116981``.
    """
    return "".join("_" if char in _ILLEGAL_ITEM_NAME_CHARS else char for char in raw_key)


class DatabaseFieldMapping(KicadModel):
    """One column mapped onto one symbol field (``database_lib_settings.cpp:120-133``).

    ``inherit_properties`` means "leave the visibility and the name-shown flag
    as the source symbol has them". Every other flag is applied only when it
    is false, or when the field did not exist yet
    (``sch_io_database.cpp:660-680``).
    """

    column: str = ""
    name: str = ""
    visible_on_add: bool = False
    visible_in_chooser: bool = False
    show_name: bool = False
    inherit_properties: bool = False


class DatabaseProperties(KicadModel):
    """The ``properties`` block: a column name per symbol property.

    Every entry names a COLUMN, not a value (``database_lib_settings.cpp:93-118``).
    An empty entry means the property is not mapped.
    """

    description: str = ""
    keywords: str = ""
    footprint_filters: str = ""
    exclude_from_bom: str = ""
    exclude_from_board: str = ""
    exclude_from_sim: str = ""


class DatabaseSubLibrary(KicadModel):
    """One entry of the ``libraries`` array: one table, mapped.

    ``name`` is the sub-library display name, with every ``/`` removed, which
    KiCad does because it uses the slash as its own separator
    (``database_lib_settings.cpp:91-92``).
    """

    name: str
    table: str
    key: str
    symbols: str
    footprints: str
    properties: DatabaseProperties = Field(default_factory=DatabaseProperties)
    fields: list[DatabaseFieldMapping] = Field(default_factory=list)

    @property
    def columns(self) -> list[str]:
        """Every column this sub-library needs, in declaration order.

        The key, the symbol reference, the footprint reference, every mapped
        property column, then every field column. Duplicates are dropped and
        the first sighting keeps its place. This is the column list a caller
        puts in its ``SELECT``.
        """
        wanted = [self.key, self.symbols, self.footprints]
        wanted.extend(
            column
            for column in (
                self.properties.description,
                self.properties.keywords,
                self.properties.footprint_filters,
                self.properties.exclude_from_bom,
                self.properties.exclude_from_board,
                self.properties.exclude_from_sim,
            )
            if column
        )
        wanted.extend(mapping.column for mapping in self.fields if mapping.column)

        ordered: list[str] = []
        seen: set[str] = set()
        for column in wanted:
            if not column or column in seen:
                continue
            seen.add(column)
            ordered.append(column)
        return ordered

    def display_name(self, raw_key: str, *, globally_unique_keys: bool) -> str:
        """Return the item name KiCad gives a row (``sch_io_database.cpp:257-265``).

        The sanitized key, prefixed with ``<sub-library>/`` UNLESS the file
        sets ``globally_unique_keys`` or the sub-library name is empty. A
        measured production file sets it, so the name is the bare sanitized key, which the
        corpus confirms: ``provisional:PN-01582_116981`` carries no prefix.
        """
        key = sanitize_database_key(raw_key)
        if globally_unique_keys or not self.name:
            return key
        return f"{self.name}/{key}"


class DatabaseSource(KicadModel):
    """The ``source`` block, with every secret withheld.

    ``connection_parts`` is the connection string split on ``;`` into named
    parts, so a caller can read the driver and the server without parsing the
    string again. A part whose key is in :data:`SECRET_KEYS` reads
    :data:`WITHHELD`, and its key is listed in ``withheld_keys``.

    ``password`` from the ``source.password`` setting is never copied out
    either; ``password_present`` reports whether the file carried one.
    """

    type: str = ""
    dsn: str = ""
    username: str = ""
    password_present: bool = False
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    connection_parts: dict[str, str] = Field(default_factory=dict)
    withheld_keys: list[str] = Field(default_factory=list)

    @property
    def driver(self) -> str:
        """The driver the caller must install, or an empty string.

        Read from the ``Driver`` part of the connection string, brackets
        stripped. A measured production file names ``ODBC Driver 18 for SQL Server``.
        """
        for key, value in self.connection_parts.items():
            if key.casefold() == "driver":
                return value.strip().strip("{}")
        return ""

    @property
    def server(self) -> str:
        """The ``Server`` part of the connection string, or an empty string."""
        for key, value in self.connection_parts.items():
            if key.casefold() in {"server", "data source", "addr", "address"}:
                return value.strip()
        return ""

    @property
    def database(self) -> str:
        """The ``Database`` part of the connection string, or an empty string."""
        for key, value in self.connection_parts.items():
            if key.casefold() in {"database", "initial catalog"}:
                return value.strip()
        return ""


class DatabaseCache(KicadModel):
    """The ``cache`` block. KiCad applies it to an in-memory cache only.

    ``max_age`` is seconds and gates an in-memory refresh
    (``sch_io_database.cpp:229``). Nothing is written to disk, by KiCad or by
    this library.
    """

    max_size: int = DEFAULT_CACHE_MAX_SIZE
    max_age: int = DEFAULT_CACHE_MAX_AGE


class DatabaseTableRead(KicadModel):
    """What a caller must read for one sub-library, and how to map it back.

    The outbound half of the handoff. It names the table and the columns; it
    does not name a statement, a dialect or a quoting rule.
    """

    sub_library: str
    table: str
    key_column: str
    symbol_column: str
    footprint_column: str
    columns: list[str] = Field(default_factory=list)
    properties: DatabaseProperties = Field(default_factory=DatabaseProperties)
    fields: list[DatabaseFieldMapping] = Field(default_factory=list)


class DatabaseDeclaration(KicadModel):
    """Everything a caller needs to read a part database, and nothing more.

    Returned by :meth:`DatabaseLibrary.declaration`. Safe to log, print, or
    paste into a report: every secret reads :data:`WITHHELD`.
    """

    name: str = ""
    description: str = ""
    source: DatabaseSource = Field(default_factory=DatabaseSource)
    cache: DatabaseCache = Field(default_factory=DatabaseCache)
    globally_unique_keys: bool = False
    tables: list[DatabaseTableRead] = Field(default_factory=list)

    @property
    def driver(self) -> str:
        """The driver a caller must install to run the read."""
        return self.source.driver

    def table_for(self, sub_library: str) -> DatabaseTableRead | None:
        """Return the read description for one sub-library name, or ``None``."""
        for table in self.tables:
            if table.sub_library == sub_library:
                return table
        return None


class DatabaseLibrary(KicadModel):
    """A parsed ``.kicad_dbl`` file.

    This is the file as read. :meth:`declaration` is the object to hand a
    caller, because it is the one with the secrets already stripped.
    """

    name: str = ""
    description: str = ""
    source: DatabaseSource = Field(default_factory=DatabaseSource)
    cache: DatabaseCache = Field(default_factory=DatabaseCache)
    globally_unique_keys: bool = False
    libraries: list[DatabaseSubLibrary] = Field(default_factory=list)

    def sub_library(self, name: str) -> DatabaseSubLibrary | None:
        """Return one sub-library by display name, or ``None``."""
        for entry in self.libraries:
            if entry.name == name:
                return entry
        return None

    def declaration(self) -> DatabaseDeclaration:
        """Return the caller-facing declaration, secrets withheld."""
        return DatabaseDeclaration(
            name=self.name,
            description=self.description,
            source=self.source,
            cache=self.cache,
            globally_unique_keys=self.globally_unique_keys,
            tables=[
                DatabaseTableRead(
                    sub_library=entry.name,
                    table=entry.table,
                    key_column=entry.key,
                    symbol_column=entry.symbols,
                    footprint_column=entry.footprints,
                    columns=entry.columns,
                    properties=entry.properties,
                    fields=entry.fields,
                )
                for entry in self.libraries
            ],
        )


def _secret_key_form(key: str) -> str:
    """Normalise a connection-string key for the :data:`SECRET_KEYS` test."""
    return "".join(char for char in key if char.isalnum()).casefold()


def _split_on_unquoted_semicolons(text: str) -> list[str]:
    """Split on ``;`` while honouring ``{…}``, ``"…"`` and ``'…'`` quoting.

    An ODBC value may be wrapped so it can CONTAIN a semicolon, which is how a
    password like ``{P@ss;word}`` is written. A blind ``str.split(";")`` cut
    that value in two and the tail survived as a dictionary KEY in the
    declaration — a fragment of the secret, in the object documented as safe to
    log. The iteration-20 security review found it; this is the fix.
    """
    chunks: list[str] = []
    current: list[str] = []
    closing: str | None = None

    for char in text:
        if closing is not None:
            current.append(char)
            if char == closing:
                closing = None
            continue
        if char == "{":
            closing = "}"
            current.append(char)
            continue
        if char in {'"', "'"}:
            closing = char
            current.append(char)
            continue
        if char == ";":
            chunks.append("".join(current))
            current = []
            continue
        current.append(char)

    chunks.append("".join(current))
    return chunks


def split_connection_string(text: str) -> tuple[dict[str, str], list[str]]:
    """Split a connection string into named parts, withholding every secret.

    Returns the parts and the list of keys whose value was withheld. A part
    with no ``=`` is kept under a positional placeholder key with an empty
    value, so nothing is silently dropped AND no unparsed text can carry a
    fragment of a secret into the output.

    Splitting honours ODBC quoting, per :func:`_split_on_unquoted_semicolons`,
    and the key test is the normalised form, per :func:`_secret_key_form`.
    """
    parts: dict[str, str] = {}
    withheld: list[str] = []

    for index, chunk in enumerate(_split_on_unquoted_semicolons(text)):
        piece = chunk.strip()
        if not piece:
            continue
        key, separator, value = piece.partition("=")
        key = key.strip()
        if not separator or not key:
            # Unparsed text. It is NOT copied out: a malformed or exotically
            # quoted string could otherwise leak part of a value as a key.
            parts[f"<unparsed part {index}>"] = WITHHELD
            withheld.append(f"<unparsed part {index}>")
            continue
        if _secret_key_form(key) in SECRET_KEYS:
            parts[key] = WITHHELD
            withheld.append(key)
        else:
            parts[key] = value.strip()

    return parts, withheld


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _as_int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def load_dbl(data: object) -> DatabaseLibrary:
    """Build a :class:`DatabaseLibrary` from already-parsed JSON.

    Every field takes KiCad's default when absent or of the wrong type, which
    is what ``JSON_SETTINGS`` does: a malformed entry is ignored, not fatal
    (``database_lib_settings.cpp:74-135``). A ``libraries`` entry missing one
    of its five required keys is skipped, because KiCad's ``entry["name"]``
    would throw and abort the whole load; skipping keeps the rest readable.
    """
    if not isinstance(data, dict):
        raise KicadParseError("a .kicad_dbl file must hold a JSON object")

    source_obj = data.get("source")
    source_obj = source_obj if isinstance(source_obj, dict) else {}
    connection_parts, withheld = split_connection_string(
        _as_str(source_obj.get("connection_string"))
    )

    cache_obj = data.get("cache")
    cache_obj = cache_obj if isinstance(cache_obj, dict) else {}

    libraries: list[DatabaseSubLibrary] = []
    entries = data.get("libraries")
    if isinstance(entries, list):
        for entry in entries[:MAX_SUB_LIBRARIES]:
            if not isinstance(entry, dict):
                continue
            if not all(key in entry for key in ("name", "table", "key", "symbols", "footprints")):
                continue

            properties_obj = entry.get("properties")
            properties_obj = properties_obj if isinstance(properties_obj, dict) else {}

            fields: list[DatabaseFieldMapping] = []
            field_entries = entry.get("fields")
            if isinstance(field_entries, list):
                for field_entry in field_entries[:MAX_FIELD_MAPPINGS]:
                    if not isinstance(field_entry, dict) or not field_entry:
                        continue
                    fields.append(
                        DatabaseFieldMapping(
                            column=_as_str(field_entry.get("column")),
                            name=_as_str(field_entry.get("name")),
                            visible_on_add=_as_bool(field_entry.get("visible_on_add")),
                            visible_in_chooser=_as_bool(field_entry.get("visible_in_chooser")),
                            show_name=_as_bool(field_entry.get("show_name")),
                            inherit_properties=_as_bool(field_entry.get("inherit_properties")),
                        )
                    )

            libraries.append(
                DatabaseSubLibrary(
                    name=_as_str(entry.get("name")).replace("/", ""),
                    table=_as_str(entry.get("table")),
                    key=_as_str(entry.get("key")),
                    symbols=_as_str(entry.get("symbols")),
                    footprints=_as_str(entry.get("footprints")),
                    properties=DatabaseProperties(
                        description=_as_str(properties_obj.get("description")),
                        keywords=_as_str(properties_obj.get("keywords")),
                        footprint_filters=_as_str(properties_obj.get("footprint_filters")),
                        exclude_from_bom=_as_str(properties_obj.get("exclude_from_bom")),
                        exclude_from_board=_as_str(properties_obj.get("exclude_from_board")),
                        exclude_from_sim=_as_str(properties_obj.get("exclude_from_sim")),
                    ),
                    fields=fields,
                )
            )

    return DatabaseLibrary(
        name=_as_str(data.get("name")),
        description=_as_str(data.get("description")),
        source=DatabaseSource(
            type=_as_str(source_obj.get("type")),
            dsn=_as_str(source_obj.get("dsn")),
            username=_as_str(source_obj.get("username")),
            password_present=bool(_as_str(source_obj.get("password"))),
            timeout_seconds=_as_int(source_obj.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS),
            connection_parts=connection_parts,
            withheld_keys=withheld,
        ),
        cache=DatabaseCache(
            max_size=_as_int(cache_obj.get("max_size"), DEFAULT_CACHE_MAX_SIZE),
            max_age=_as_int(cache_obj.get("max_age"), DEFAULT_CACHE_MAX_AGE),
        ),
        globally_unique_keys=_as_bool(data.get("globally_unique_keys")),
        libraries=libraries,
    )


def _refuse_deep_json(text: str, source: str) -> None:
    """Raise when *text* nests deeper than :data:`MAX_JSON_DEPTH`.

    ``json.loads`` recurses once per nesting level, so a file far under
    :data:`MAX_DBL_BYTES` made of nested brackets can exhaust the interpreter
    stack before the byte cap ever applies. This counts brackets outside string
    literals, which is cheap and needs no parser.
    """
    depth = 0
    in_string = False
    escaped = False

    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise KicadParseError(
                    f"{source} nests deeper than the {MAX_JSON_DEPTH} level cap. A "
                    ".kicad_dbl has a fixed shallow shape; the real files reach depth 4."
                )
        elif char in "]}":
            depth -= 1


def load_kicad_dbl(path: Path | str) -> DatabaseLibrary:
    """Read a ``.kicad_dbl`` file from disk.

    Raises :class:`~eedesigncore.exceptions.KicadParseError` on a file that is
    not JSON, that is over :data:`MAX_DBL_BYTES`, or that nests deeper than
    :data:`MAX_JSON_DEPTH`.
    """
    file = Path(path)
    text = read_text_capped(file, MAX_DBL_BYTES)
    _refuse_deep_json(text, str(file))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise KicadParseError(f"{file} is not valid JSON: {error}") from error
    return load_dbl(data)
