"""KiCad HTTP symbol libraries, against a local test server (``sch_io_http_lib.cpp``, 10.0)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from eedesigncore import (
    Pin,
    PinElectricalType,
    PinShape,
    Symbol,
    SymbolUnit,
)
from eedesigncore.library import (
    HttpLibraryError,
    HttpLibrarySettings,
    HttpPart,
    find_symbol,
    iter_library_records,
    load_library_symbols,
    symbol_from_http_part,
    table_symbol_loader,
)
from eedesigncore.sym.models import SymbolLib
from eedesigncore.tables.models import LibTable, LibTableRow
from eedesigncore.tables.resolver import LibTableResolver

TOKEN = "test-token"

PARTS = {
    "C1__1": {
        "id": "C1__1",
        "description": "Resistor 1k 0402",
        "keywords": "resistor 1k",
        "symbolIdStr": "Base:R",
        "exclude_from_bom": "false",
        "exclude_from_board": False,
        "exclude_from_sim": "yes",
        "fields": {
            "footprint": {"value": "Lib:R_0402;Lib:R_0603", "visible": "false"},
            "Value": {"value": "1k", "visible": "true"},
            "Datasheet": {"value": "https://example.com/r.pdf", "visible": "False"},
            "MPN": {"value": "RC0402-1K", "visible": "false"},
            "Keywords": {"value": "ignored by the later keywords", "visible": "false"},
        },
    },
    "P7": {
        "id": "P7",
        "name": "Odd:Name",
        "symbolIdStr": "Base:R",
        "fields": {"Value": {"value": "odd", "visible": "true"}},
    },
}
LISTING = {
    "Resistors": [
        PARTS["C1__1"],
        # Listed without fields, so the library asks for the full record.
        {"id": "P7", "name": "Odd:Name", "description": "listed only"},
    ]
}


class _Handler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def log_message(self, *args: object) -> None:  # silence the test output
        pass

    def do_GET(self) -> None:  # noqa: N802
        _Handler.requests.append(self.path)
        if self.headers.get("Authorization") != "Token " + TOKEN:
            self.send_response(401)
            self.end_headers()
            return
        base = "/kicad/T/v1/"
        body: object | None = None
        if self.path == base:
            body = {"categories": base + "categories.json", "parts": base + "parts"}
        elif self.path == base + "categories.json":
            body = [{"id": "Resistors", "name": "Resistors", "description": "R"}]
        elif self.path.startswith(base + "parts/category/"):
            body = LISTING.get(self.path.rsplit("/", 1)[1].removesuffix(".json"))
        elif self.path.startswith(base + "parts/"):
            body = PARTS.get(self.path.rsplit("/", 1)[1].removesuffix(".json"))
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/kicad/T"
    httpd.shutdown()


def _base_library(path: Path) -> Path:
    pins = [
        Pin(
            number=str(i),
            name="~",
            position=(0, 2.54 * i),
            electrical_type=PinElectricalType.passive,
            shape=PinShape.line,
        )
        for i in (1, 2)
    ]
    symbol = Symbol.new("R", reference="R")
    symbol.units = [SymbolUnit.for_symbol("R", items=pins)]
    path.parent.mkdir(parents=True, exist_ok=True)
    SymbolLib(symbols=[symbol]).to_file(path)
    return path


def _http_file(path: Path, url: str, **source: object) -> Path:
    data = {
        "meta": {"version": 1},
        "name": "T",
        "description": "test",
        "source": {
            "type": "REST_API",
            "api_version": "v1",
            "root_url": url,
            "token": TOKEN,
            **source,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _table(tmp_path: Path, server: str):
    settings = tmp_path / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    base = _base_library(tmp_path / "libs" / "Base.kicad_sym")
    http = _http_file(tmp_path / "libs" / "T.kicad_httplib", server)
    LibTable(
        kind="sym",
        version=7,
        rows=[
            LibTableRow(name="Base", type="KiCad", uri=str(base)),
            LibTableRow(name="T", type="HTTP", uri=str(http)),
        ],
    ).to_file(settings / "sym-lib-table", line_ending="lf")
    return LibTableResolver.discover(settings_dir=settings, env={}).resolve("sym")


def _pins(symbol: Symbol) -> list[str]:
    return [item.number for unit in symbol.units for item in unit.items if isinstance(item, Pin)]


class TestSettings:
    def test_the_endpoint_takes_a_slash_and_the_version(self, tmp_path: Path) -> None:
        settings = HttpLibrarySettings.from_file(
            _http_file(tmp_path / "a.kicad_httplib", "http://h/x")
        )
        assert settings.endpoint_url == "http://h/x/v1/"
        assert "test-token" not in repr(settings)

    @pytest.mark.parametrize(
        "source, message",
        [
            ({"api_version": ""}, "missing the API version"),
            ({"api_version": "v2"}, "uses API version v2"),
            ({"root_url": ""}, "missing the root URL"),
            ({"type": "SOAP"}, "invalid library type"),
            ({"root_url": "file:///c:/x"}, "not http or https"),
        ],
    )
    def test_kicad_refuses_bad_settings(self, tmp_path: Path, source: dict, message: str) -> None:
        path = _http_file(tmp_path / "b.kicad_httplib", "http://h/x", **source)
        with pytest.raises(HttpLibraryError, match=message):
            HttpLibrarySettings.from_file(path)


class TestSymbolFromPart:
    def test_kicad_field_rules(self) -> None:
        from eedesigncore.library.http import _part_from_json

        part = _part_from_json(PARTS["C1__1"])
        base = Symbol.new("R", reference="R")
        built = symbol_from_http_part(part, lambda lib_id: base if lib_id == "Base:R" else None)
        symbol = built.definition
        fields = {field.name: field for field in symbol.fields}
        assert symbol.name == "C1__1"
        assert fields["Footprint"].value == "Lib:R_0402"
        assert not fields["Footprint"].visible
        assert symbol.fp_filters == ["Lib:R_0402", "Lib:R_0603"]
        assert fields["Value"].value == "1k" and fields["Value"].visible
        assert not fields["Datasheet"].visible
        assert fields["MPN"].value == "RC0402-1K"
        assert fields["Description"].value == "Resistor 1k 0402"
        assert symbol.keywords == "resistor 1k"
        assert symbol.in_bom and symbol.on_board and symbol.exclude_from_sim
        assert built.base_found

    def test_a_missing_base_gives_fields_and_no_body(self) -> None:
        built = symbol_from_http_part(
            HttpPart(id="X", name="X", symbol_id="Base:None"), lambda _: None
        )
        assert not built.base_found and not built.has_pins
        assert "Base:None" in built.unresolved_reason

    def test_an_illegal_name_character_becomes_an_underscore(self) -> None:
        from eedesigncore.library.http import _part_from_json

        assert _part_from_json(PARTS["P7"]).name == "Odd_Name"


class TestTable:
    def test_find_symbol_reads_the_server_and_the_base(self, tmp_path: Path, server: str) -> None:
        table = _table(tmp_path, server)
        symbol = find_symbol(table, "T:C1__1")
        assert symbol is not None
        assert _pins(symbol) == ["1", "2"]
        assert find_symbol(table, "T:NoSuchPart") is None

    def test_a_name_other_than_the_id_is_found_through_the_listing(
        self, tmp_path: Path, server: str
    ) -> None:
        table = _table(tmp_path, server)
        symbol = find_symbol(table, "T:Odd_Name")
        assert symbol is not None
        assert {f.name: f.value for f in symbol.fields}["Value"] == "odd"

    def test_the_loader_parses_each_base_once(self, tmp_path: Path, server: str) -> None:
        table = _table(tmp_path, server)
        loader = table_symbol_loader(table)
        first, second = loader("T:C1__1"), loader("T:Odd_Name")
        assert first is not None and second is not None
        first.name = "changed"
        assert loader("T:C1__1").name == "C1__1"

    def test_listing_yields_every_part(self, tmp_path: Path, server: str) -> None:
        table = _table(tmp_path, server)
        names = [
            record.name
            for record in iter_library_records(
                table.get("T"), symbol_loader=table_symbol_loader(table)
            )
        ]
        assert names == ["C1__1", "Odd_Name"]

    def test_load_library_symbols_reports_each_miss(self, tmp_path: Path, server: str) -> None:
        table = _table(tmp_path, server)
        load = load_library_symbols(["T:C1__1", "T:Gone", "Nope:R"], table)
        assert list(load.symbols) == ["T:C1__1"]
        assert set(load.missing) == {"T:Gone", "Nope:R"}

    def test_a_wrong_token_raises(self, tmp_path: Path, server: str) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        http = _http_file(tmp_path / "T.kicad_httplib", server)
        http.write_text(http.read_text().replace(TOKEN, "wrong"), encoding="utf-8")
        LibTable(
            kind="sym", version=7, rows=[LibTableRow(name="T", type="HTTP", uri=str(http))]
        ).to_file(settings / "sym-lib-table", line_ending="lf")
        table = LibTableResolver.discover(settings_dir=settings, env={}).resolve("sym")
        with pytest.raises(HttpLibraryError, match="401"):
            find_symbol(table, "T:C1__1")

    def test_a_project_row_may_not_name_a_remote_server(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        project = tmp_path / "project"
        http = _http_file(project / "T.kicad_httplib", "http://parts.example.com/kicad")
        LibTable(
            kind="sym",
            version=7,
            rows=[LibTableRow(name="T", type="HTTP", uri="${KIPRJMOD}/T.kicad_httplib")],
        ).to_file(project / "sym-lib-table", line_ending="lf")
        table = LibTableResolver.discover(
            settings_dir=settings, project_dir=project, env={}
        ).resolve("sym")
        assert table.get("T") is not None and http.exists()
        with pytest.raises(HttpLibraryError, match="only this computer"):
            find_symbol(table, "T:C1__1")
