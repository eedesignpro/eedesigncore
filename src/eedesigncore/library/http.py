"""Read KiCad HTTP symbol libraries (``.kicad_httplib``), the way KiCad does.

An HTTP library row in a ``sym-lib-table`` names a small JSON settings file. The
file names a REST server, and the server lists categories and parts. Each part
names a base symbol in another library (``symbolIdStr``) and carries fields.
KiCad builds the library symbol from the two.

The port follows KiCad 10.0:

* settings and their checks: ``HTTP_LIB_SETTINGS`` (``http_lib_settings.cpp``)
  and ``SCH_IO_HTTP_LIB::ensureSettings`` (``sch_io_http_lib.cpp:586-657``);
* requests: ``HTTP_LIB_CONNECTION`` (``http_lib_connection.cpp``). The root URL
  takes a ``/`` and the API version; the endpoints are ``categories.json``,
  ``parts/category/<id>.json`` and ``parts/<id>.json``, with the headers
  ``Accept: application/json`` and ``Authorization: Token <token>``;
* the symbol: ``SCH_IO_HTTP_LIB::loadSymbolFromPart`` (``:700-905``).

Operator ruling, 2026-11-07: eedesigncore reads HTTP libraries itself. This is
the one place the library connects to a server. A database library still takes
rows from the caller (:mod:`eedesigncore.library.database`).

A connection error raises :class:`HttpLibraryError`, as KiCad raises
``IO_ERROR``. A part the server does not know returns ``None``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import quote, urlsplit

from eedesigncore.common.base import KicadModel
from eedesigncore.common.sch_family import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.library.database import _field_index, _set_field, _split_footprints
from eedesigncore.library.records import (
    DATASHEET_FIELD,
    DESCRIPTION_FIELD,
    FOOTPRINT_FIELD,
    REFERENCE_FIELD,
    VALUE_FIELD,
)
from eedesigncore.tables.resolver import HTTP_ROW_TYPE

SUPPORTED_API_VERSION: Final = "v1"
"""``HTTP_LIB_SETTINGS::m_api_version``."""

MAX_RESPONSE_BYTES: Final = 512 * 1024 * 1024
"""Cap on one server response. KiCad has no cap.

The largest measured response is one category listing of 76482 parts, 56 MB.
"""

DEFAULT_TIMEOUT_SECONDS: Final = 60.0
"""Network timeout for one request."""

Retriever = Callable[[str, dict[str, str], float], tuple[int, bytes]]
"""Fetch a URL with headers and a timeout; return the status code and the body."""

SymbolLoader = Callable[[str], Symbol | None]


class HttpLibraryError(RuntimeError):
    """An HTTP library could not be read: bad settings, no server, or a bad reply."""


class HttpLibrarySource(KicadModel):
    """The ``source`` block of a ``.kicad_httplib`` file."""

    type: str = ""
    api_version: str = ""
    root_url: str = ""
    token: str = ""
    timeout_parts_seconds: int = 30
    timeout_categories_seconds: int = 600

    def __repr_args__(self):  # type: ignore[no-untyped-def]
        # The token is a credential; keep it out of reprs and logs.
        for key, value in super().__repr_args__():
            yield key, ("<hidden>" if key == "token" and value else value)


class HttpLibrarySettings(KicadModel):
    """One ``.kicad_httplib`` file."""

    name: str = ""
    description: str = ""
    source: HttpLibrarySource = HttpLibrarySource()

    @classmethod
    def from_file(cls, path: Path | str) -> HttpLibrarySettings:
        """Read and check a settings file, as ``ensureSettings`` does.

        Raises:
            HttpLibraryError: when the file is missing or invalid, has no API
                version or another one than ``v1``, has no root URL, or has a
                source type other than ``REST_API``.
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            settings = cls.model_validate(
                {
                    "name": data.get("name", ""),
                    "description": data.get("description", ""),
                    "source": data.get("source", {}),
                }
            )
        except (OSError, ValueError, AttributeError) as error:
            raise HttpLibraryError(
                f"HTTP library settings file {path} missing or invalid."
            ) from error
        source = settings.source
        if not source.api_version:
            raise HttpLibraryError(
                f"HTTP library settings file {path} is missing the API version number."
            )
        if source.api_version != SUPPORTED_API_VERSION:
            raise HttpLibraryError(
                f"HTTP library settings file {path} uses API version {source.api_version}, "
                f"but KiCad requires version {SUPPORTED_API_VERSION}."
            )
        if not source.root_url:
            raise HttpLibraryError(f"HTTP library settings file {path} is missing the root URL.")
        if source.type != "REST_API":
            raise HttpLibraryError(f"HTTP library settings file {path} has invalid library type.")
        if urlsplit(source.root_url).scheme.lower() not in ("http", "https"):
            raise HttpLibraryError(
                f"HTTP library settings file {path} has a root URL that is not http or https."
            )
        return settings

    @property
    def endpoint_url(self) -> str:
        """The root URL with a ``/`` and the API version, as KiCad builds it."""
        root = self.source.root_url
        return (root if root.endswith("/") else root + "/") + self.source.api_version + "/"


@dataclass(frozen=True)
class HttpCategory:
    """``HTTP_LIB_CATEGORY``: one category the server lists."""

    id: str
    name: str
    description: str = ""


@dataclass
class HttpPart:
    """``HTTP_LIB_PART``: one part, from a category listing or its own record."""

    id: str
    name: str
    symbol_id: str = ""
    description: str = ""
    keywords: str = ""
    fp_filters: list[str] = field(default_factory=list)
    exclude_from_bom: bool = False
    exclude_from_board: bool = False
    exclude_from_sim: bool = False
    # (field name, value, visible), in the server's order.
    fields: list[tuple[str, str, bool]] = field(default_factory=list)
    details_loaded: bool = False


def fix_item_name(name: str) -> str:
    """``LIB_ID::FixIllegalChars( name, false )``: replace characters an item name refuses."""
    return "".join("_" if ch in ':\t\n\r\\<>"' else ch for ch in name)


def _bool(value: object, default: bool) -> bool:
    """``jsonBoolField``: a JSON boolean, or a string read by ``boolFromString``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "y", "1"):
            return True
        if text in ("false", "no", "n", "0"):
            return False
    return default


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _part_from_json(data: object) -> HttpPart:
    """``setPartIdNameAndMetadata`` and ``setPartExtendedData``."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str | int):
        raise HttpLibraryError("A part record has no id.")
    part_id = str(data["id"])
    raw_name = data.get("name")
    part = HttpPart(
        id=part_id, name=fix_item_name(str(raw_name) if raw_name is not None else part_id)
    )
    part.description = _text(data.get("description"))
    part.keywords = _text(data.get("keywords"))
    filters = data.get("footprint_filters")
    if isinstance(filters, list):
        part.fp_filters = [str(item) for item in filters]
    elif filters is not None:
        part.fp_filters = [str(filters)]
    if isinstance(data.get("symbolIdStr"), str):
        part.symbol_id = data["symbolIdStr"]
    part.exclude_from_bom = _bool(data.get("exclude_from_bom"), False)
    part.exclude_from_board = _bool(data.get("exclude_from_board"), False)
    part.exclude_from_sim = _bool(data.get("exclude_from_sim"), False)
    fields = data.get("fields")
    if isinstance(fields, dict):
        part.details_loaded = True
        for name, properties in fields.items():
            if not isinstance(properties, dict) or not isinstance(properties.get("value"), str):
                continue
            part.fields.append(
                (str(name), properties["value"], _bool(properties.get("visible"), True))
            )
    return part


def _urllib_retriever(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(MAX_RESPONSE_BYTES + 1)
            return response.status, body
    except urllib.error.HTTPError as error:
        return error.code, b""


class HttpLibraryConnection:
    """``HTTP_LIB_CONNECTION``: the requests to one library server.

    Category and part listings are kept for the life of the object, as KiCad
    keeps them for the life of its plugin. A caller that wants fresh data
    builds a new connection.
    """

    def __init__(
        self,
        settings: HttpLibrarySettings,
        *,
        retriever: Retriever | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings
        self._retriever = retriever or _urllib_retriever
        self._timeout = timeout
        self._categories: list[HttpCategory] | None = None
        self._listings: dict[str, list[HttpPart]] = {}
        self._parts: dict[str, HttpPart] = {}

    def _get(self, url: str) -> object:
        headers = {
            "Accept": "application/json",
            "Authorization": "Token " + self.settings.source.token,
        }
        try:
            status, body = self._retriever(url, headers, self._timeout)
        except OSError as error:
            raise HttpLibraryError(
                f"Could not connect to {self.settings.endpoint_url}: {error}"
            ) from error
        if status != 200:
            raise HttpLibraryError(f"API responded with error code: {status} ({url})")
        if len(body) > MAX_RESPONSE_BYTES:
            raise HttpLibraryError(
                f"The reply from {url} is larger than {MAX_RESPONSE_BYTES} bytes."
            )
        if not body:
            raise HttpLibraryError(f"KiCad received an empty response! ({url})")
        try:
            return json.loads(body)
        except ValueError as error:
            raise HttpLibraryError(f"The reply from {url} is not JSON.") from error

    def validate(self) -> None:
        """``validateHttpLibraryEndpoints``: the root must list both endpoints."""
        reply = self._get(self.settings.endpoint_url)
        if not isinstance(reply, dict) or not reply.get("categories") or not reply.get("parts"):
            raise HttpLibraryError(
                f"Could not connect to {self.settings.endpoint_url}: the server lists no "
                "categories or parts endpoint."
            )

    def categories(self) -> list[HttpCategory]:
        """``syncCategories``."""
        if self._categories is None:
            self.validate()
            reply = self._get(self.settings.endpoint_url + "categories.json")
            if not isinstance(reply, list):
                raise HttpLibraryError("The category list is not a JSON array.")
            self._categories = [
                HttpCategory(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    description=_text(item.get("description")),
                )
                for item in reply
                if isinstance(item, dict) and "id" in item and "name" in item
            ]
        return self._categories

    def category_parts(self, category: HttpCategory) -> list[HttpPart]:
        """``SelectAll``: the parts of one category, as listed."""
        if category.id not in self._listings:
            url = self.settings.endpoint_url + f"parts/category/{quote(category.id, safe='')}.json"
            reply = self._get(url)
            if not isinstance(reply, list):
                raise HttpLibraryError(
                    f"The part list of category {category.name} is not a JSON array."
                )
            parts = [_part_from_json(item) for item in reply]
            self._listings[category.id] = parts
            for part in parts:
                if part.details_loaded:
                    self._parts.setdefault(part.id, part)
        return self._listings[category.id]

    def part(self, part_id: str) -> HttpPart | None:
        """``SelectOne``: the full record of one part, or ``None`` when the server has none."""
        if part_id not in self._parts:
            url = self.settings.endpoint_url + f"parts/{quote(part_id, safe='')}.json"
            try:
                reply = self._get(url)
            except HttpLibraryError as error:
                if "error code: 404" in str(error):
                    return None
                raise
            part = _part_from_json(reply)
            part.details_loaded = True
            self._parts[part_id] = part
        return self._parts[part_id]

    def iter_parts(self) -> Iterator[tuple[HttpPart, HttpCategory]]:
        """Every part with its full record, as ``fetchCategoryParts`` collects them."""
        for category in self.categories():
            for listed in self.category_parts(category):
                part = listed
                if not listed.details_loaded:
                    full = self.part(listed.id)
                    if full is not None:
                        full.description = full.description or listed.description
                        full.keywords = full.keywords or listed.keywords
                        full.id, full.name = listed.id, listed.name
                        part = full
                yield part, category

    def find_part(self, name: str) -> tuple[HttpPart, HttpCategory | None] | None:
        """The part KiCad loads for symbol *name*, or ``None``.

        KiCad maps a symbol name to its part through the category listings
        (``m_partIdMap``), and the last listing wins. The part record of the same
        id is tried first: when it gives the same name, it is the part. Otherwise
        the listings are read.
        """
        direct = self.part(name)
        if direct is not None and direct.name == name:
            return direct, None
        found: tuple[HttpPart, HttpCategory | None] | None = None
        for category in self.categories():
            for listed in self.category_parts(category):
                if listed.name == name:
                    found = (listed, category)
        if found is None:
            return None
        listed, category = found
        if not listed.details_loaded:
            full = self.part(listed.id)
            if full is not None:
                full.id, full.name = listed.id, listed.name
                listed = full
        return listed, category


@dataclass
class HttpPartSymbol:
    """The symbol built from one part, and what is known about its base symbol."""

    definition: Symbol
    base_found: bool
    unresolved_reason: str = ""

    @property
    def has_pins(self) -> bool:
        return any(isinstance(item, Pin) for unit in self.definition.units for item in unit.items)


def symbol_from_http_part(part: HttpPart, symbol_loader: SymbolLoader | None) -> HttpPartSymbol:
    """Build the library symbol for *part*: ``loadSymbolFromPart`` (``:700-905``).

    The base symbol is ``symbolIdStr``, loaded through *symbol_loader*,
    duplicated and renamed to the part name. Without one, the symbol carries the
    part's fields alone and no body, as KiCad's does; ``base_found`` is false.
    """
    symbol: Symbol | None = None
    reason = ""
    if not part.symbol_id:
        reason = "the part names no symbol (symbolIdStr)"
    elif symbol_loader is None:
        reason = f"no symbol loader was given, so {part.symbol_id!r} was never looked up"
    else:
        loaded = symbol_loader(part.symbol_id)
        if loaded is None:
            reason = f"the base symbol {part.symbol_id!r} was not found"
        else:
            symbol = loaded.model_copy(deep=True).renamed(part.name)
    if symbol is None:
        symbol = Symbol.new(part.name)

    symbol.in_bom = not part.exclude_from_bom
    symbol.on_board = not part.exclude_from_board
    symbol.exclude_from_sim = part.exclude_from_sim

    index = _field_index(symbol)
    footprints: list[str] = []
    builtin = {
        "description": DESCRIPTION_FIELD,
        "value": VALUE_FIELD,
        "datasheet": DATASHEET_FIELD,
        "reference": REFERENCE_FIELD,
    }
    for name, value, visible in part.fields:
        lower = name.lower()
        if lower == "footprint":
            footprints = _split_footprints(value)
            if footprints:
                _set_field(index, symbol, FOOTPRINT_FIELD, footprints[0], visible=visible)
            elif FOOTPRINT_FIELD in index:
                index[FOOTPRINT_FIELD].visible = visible
        elif lower in builtin:
            _set_field(index, symbol, builtin[lower], value, visible=visible)
        elif lower == "keywords":
            symbol.keywords = value
        else:
            _set_field(index, symbol, name, value, visible=visible)

    if part.description:
        _set_field(index, symbol, DESCRIPTION_FIELD, part.description)
    if part.keywords:
        symbol.keywords = part.keywords
    symbol.fp_filters = [*footprints, *part.fp_filters]
    return HttpPartSymbol(definition=symbol, base_found=not reason, unresolved_reason=reason)


class HttpSymbolLibrary:
    """One HTTP symbol library: its settings, its connection, and its symbols.

    *symbol_loader* resolves a part's base symbol, for example
    ``table_symbol_loader(resolve_lib_table("sym"))``. Base symbols are kept
    for the life of the object, so a library of 100000 resistors on one base
    symbol parses that symbol once.
    """

    def __init__(
        self,
        settings: HttpLibrarySettings,
        *,
        nickname: str = "",
        symbol_loader: SymbolLoader | None = None,
        connection: HttpLibraryConnection | None = None,
    ) -> None:
        self.settings = settings
        self.nickname = nickname or settings.name
        self.connection = connection or HttpLibraryConnection(settings)
        self._loader = symbol_loader
        self._bases: dict[str, Symbol | None] = {}
        self._loading: set[str] = set()

    @classmethod
    def from_file(cls, path: Path | str, **kwargs: object) -> HttpSymbolLibrary:
        return cls(HttpLibrarySettings.from_file(path), **kwargs)  # type: ignore[arg-type]

    def _base(self, lib_id: str) -> Symbol | None:
        # A base symbol that names this same library would load itself forever
        # (KiCad issue 24249); KiCad skips the recursive load.
        if lib_id in self._loading or self._loader is None:
            return None
        if lib_id not in self._bases:
            self._loading.add(lib_id)
            try:
                self._bases[lib_id] = self._loader(lib_id)
            finally:
                self._loading.discard(lib_id)
        return self._bases[lib_id]

    def build(self, part: HttpPart) -> HttpPartSymbol:
        """The symbol KiCad builds for *part*."""
        return symbol_from_http_part(part, self._base)

    def load(self, name: str) -> HttpPartSymbol | None:
        """``LoadSymbol``: the symbol named *name*, or ``None`` when no part has that name."""
        found = self.connection.find_part(name)
        return None if found is None else self.build(found[0])

    def load_symbol(self, name: str) -> Symbol | None:
        """The definition alone, or ``None``."""
        built = self.load(name)
        return None if built is None else built.definition

    def iter_symbols(self) -> Iterator[tuple[HttpPartSymbol, HttpCategory]]:
        """Every symbol of the library, category by category, as KiCad lists them."""
        for part, category in self.connection.iter_parts():
            yield self.build(part), category


__all__ = [
    "HTTP_ROW_TYPE",
    "HttpCategory",
    "HttpLibraryConnection",
    "HttpLibraryError",
    "HttpLibrarySettings",
    "HttpLibrarySource",
    "HttpPart",
    "HttpPartSymbol",
    "HttpSymbolLibrary",
    "fix_item_name",
    "symbol_from_http_part",
]
