"""The findings of the iteration-20 QA and security reviews, one class each.

Every expected value here comes from KiCad source or from a measurement over
the corpus, never from this library's own arithmetic. That was the fault the QA
review caught: the first field-position test set its expected value with the
same ``+`` the code used, so it passed while the code was wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eedesigncore import Schematic, Symbol, inject_symbol
from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.exceptions import KicadParseError
from eedesigncore.library.database import (
    MAX_FOOTPRINT_TOKENS,
    MAX_ROW_VALUE_LENGTH,
    definition_from_row,
)
from eedesigncore.library.dbl import (
    MAX_JSON_DEPTH,
    WITHHELD,
    DatabaseFieldMapping,
    DatabaseSubLibrary,
    load_dbl,
    load_kicad_dbl,
    split_connection_string,
)
from eedesigncore.library.search import (
    MAX_REGEX_PATTERN_LENGTH,
    PartRecord,
    regex_is_safe,
    score_record,
)
from eedesigncore.tables.resolver import LibTableResolver, ResolvedLibrary

SYMBOL_TEXT = (
    '(kicad_symbol_lib (version 20251024) (generator "x")\n'
    '\t(symbol "PART"\n'
    '\t\t(property "Reference" "U" (at 0 3.81 0) (effects (font (size 1.27 1.27))))\n'
    '\t\t(property "Value" "PART" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))\n'
    '\t\t(property "IPN" "200-1" (at 2.286 -0.508 0)'
    " (effects (font (size 1.27 1.27)) (hide yes)))\n"
    "\t)\n"
    ")\n"
)


class TestPlacedFieldYSign:
    """QA finding 1: the Y offset is SUBTRACTED, not added.

    KiCad's parser inverts Y for a LIBRARY field and not for a PLACED one
    (``sch_io_kicad_sexpr_parser.h:169-178``; library at
    ``sch_io_kicad_sexpr_parser.cpp:1127``, placed at ``:4726``), so its
    in-memory offset is the negated file value. This model stores the file
    value, so the placement subtracts.

    Measured over 579 corpus schematics, hidden fields with a non-zero library
    Y offset at angle 0: 4095 sit at ``at.y - offset``, 48 at ``at.y + offset``
    and 576 at neither, the last two being fields an engineer moved.
    """

    def _loaded_definition(self, tmp_path: Path) -> Symbol:
        """Load the definition from a FILE, so the loader's convention applies."""
        from eedesigncore.sym.models import SymbolLib

        path = tmp_path / "Parts.kicad_sym"
        path.write_text(SYMBOL_TEXT, encoding="utf-8")
        return SymbolLib.from_file(path).symbols[0]

    def test_the_y_offset_is_subtracted(self, tmp_path: Path) -> None:
        definition = self._loaded_definition(tmp_path)
        placed = inject_symbol(Schematic(), definition, at=(199.39, 111.76), lib_nickname="L")

        # The corpus case the QA review named: provisional:200-00292_103005,
        # placed at (199.39, 111.76), library IPN offset (2.286, -0.508).
        # KiCad wrote (201.676, 112.268).
        ipn = next(f for f in placed.fields if f.name == "IPN")
        assert ipn.position == (201.676, 112.268)

    def test_the_x_offset_is_added(self, tmp_path: Path) -> None:
        definition = self._loaded_definition(tmp_path)
        placed = inject_symbol(Schematic(), definition, at=(100.0, 50.0), lib_nickname="L")

        ipn = next(f for f in placed.fields if f.name == "IPN")
        assert ipn.position[0] == pytest.approx(102.286)

    def test_a_zero_offset_still_lands_on_the_symbol(self, tmp_path: Path) -> None:
        definition = self._loaded_definition(tmp_path)
        next(f for f in definition.fields if f.name == "IPN").position = (0.0, 0.0)
        placed = inject_symbol(Schematic(), definition, at=(120.0, 180.0), lib_nickname="L")

        assert next(f.position for f in placed.fields if f.name == "IPN") == (120.0, 180.0)

    def test_the_library_definition_keeps_the_file_convention(self, tmp_path: Path) -> None:
        definition = self._loaded_definition(tmp_path)
        sch = Schematic()
        placed = inject_symbol(sch, definition, at=(100.0, 50.0), lib_nickname="L")

        registered = next(entry for entry in sch.lib_symbols if entry.name == placed.lib_id)
        assert next(f.position for f in registered.fields if f.name == "IPN") == (2.286, -0.508)


class TestProjectTierIsNotWidened:
    """Security CRITICAL, and QA finding 2. Two leaks into the project tier."""

    def test_a_project_text_variable_is_never_a_read_root(self, tmp_path: Path) -> None:
        """The reported escape: a cloned project names its own read root."""
        resolver = LibTableResolver.discover(
            project_dir=tmp_path / "cloned",
            settings_dir=tmp_path / "settings",
            kicad_root=tmp_path / "KiCad" / "10.0",
            project_text_variables={"ESCAPE": str(tmp_path)},
            env={},
        )

        assert resolver.roots("project") == [tmp_path / "cloned"]
        assert tmp_path not in resolver.roots("global")

    def test_a_path_variable_widens_the_global_tier_only(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        resolver = LibTableResolver.discover(
            settings_dir=tmp_path / "settings",
            project_dir=tmp_path / "proj",
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={"KICAD_SHARED_LIB_DIR": str(shared)},
        )

        assert shared in resolver.default_roots("global")
        assert shared not in resolver.default_roots("project")

    def test_a_chain_root_is_invisible_to_the_project_tier(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        store = tmp_path / "store"
        leaf = store / "Parts.pretty"
        leaf.mkdir(parents=True)
        (leaf / "PART.kicad_mod").write_text("(footprint)", encoding="utf-8")
        nested_uri = (store / "nested").as_posix()
        (store / "nested").write_text(
            f'(fp_lib_table (lib (name "Leaf")(type "KiCad")(uri "{leaf.as_posix()}")'
            '(options "")(descr "")))\n',
            encoding="utf-8",
        )
        (settings / "fp-lib-table").write_text(
            f'(fp_lib_table (lib (name "Chain")(type "Table")(uri "{nested_uri}")'
            '(options "")(descr "")))\n',
            encoding="utf-8",
        )

        resolver = LibTableResolver.discover(
            settings_dir=settings,
            project_dir=tmp_path / "proj",
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={},
        )
        table = resolver.resolve("fp")

        assert table.nicknames == ["Leaf"]
        assert store in resolver.roots("global")
        assert store not in resolver.roots("project")


class TestConfinementTravelsWithTheRow:
    """Security MEDIUM: the check was opt-in per call site."""

    def test_a_refused_row_reads_as_empty(self, tmp_path: Path) -> None:
        from eedesigncore.library import iter_library_records, library_item_names

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "PART.kicad_sym").write_text(SYMBOL_TEXT, encoding="utf-8")
        row = ResolvedLibrary(
            nickname="Outside",
            type="KiCad",
            uri=str(outside),
            raw_uri=str(outside),
            scope="global",
            source_table=tmp_path / "sym-lib-table",
            read_roots=[tmp_path / "inside"],
        )

        assert row.read_allowed is False
        assert list(iter_library_records(row)) == []
        assert library_item_names(row) == []

    def test_a_hand_built_row_is_unconfined(self, tmp_path: Path) -> None:
        from eedesigncore.library import library_item_names

        folder = tmp_path / "libs"
        folder.mkdir()
        (folder / "PART.kicad_sym").write_text(SYMBOL_TEXT, encoding="utf-8")
        row = ResolvedLibrary(
            nickname="Hand",
            type="KiCad",
            uri=str(folder),
            raw_uri=str(folder),
            scope="global",
            source_table=tmp_path / "sym-lib-table",
        )

        assert row.read_allowed is True
        assert library_item_names(row) == ["PART"]

    def test_a_resolved_row_carries_its_tier_roots(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        (settings / "sym-lib-table").write_text(
            '(sym_lib_table (lib (name "X")(type "KiCad")(uri "C:/x.kicad_sym")'
            '(options "")(descr "")))\n',
            encoding="utf-8",
        )
        table = LibTableResolver.discover(
            settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0", env={}
        ).resolve("sym")

        assert table.libraries[0].read_roots is not None
        assert table.libraries[0].read_allowed is False


class TestConnectionStringSecrets:
    """Security HIGH: a quoted value with a semicolon leaked its tail."""

    @pytest.mark.parametrize(
        "connection",
        [
            "Driver={ODBC};Pwd={P@ss;word};Server=db1",
            'Pwd="a;b";Server=db1',
            "Pwd='x;y';Server=db1",
            "Client Secret=abc123;Server=db1",
            "API Key=abc123;Server=db1",
            "Access-Key=abc123;Server=db1",
            "client_secret=abc123;Server=db1",
            "PASSWORD=abc123;Server=db1",
        ],
    )
    def test_no_fragment_of_a_secret_survives(self, connection: str) -> None:
        parts, withheld = split_connection_string(connection)
        rendered = json.dumps({"parts": parts, "withheld": withheld})

        for secret in ("P@ss", "word}", "abc123", "a;b", "x;y"):
            assert secret not in rendered, (secret, rendered)
        assert withheld

    def test_an_unparsed_chunk_is_withheld_rather_than_copied(self) -> None:
        parts, withheld = split_connection_string("nokeyhere;Server=db1")

        assert "nokeyhere" not in json.dumps(parts)
        assert parts["Server"] == "db1"
        assert withheld == ["<unparsed part 0>"]

    def test_a_normal_string_still_reads_through(self) -> None:
        parts, withheld = split_connection_string(
            "Driver={ODBC Driver 18 for SQL Server};Server=sql-server-01;"
            "Database=ECAD;Trusted_Connection=yes"
        )

        assert parts["Server"] == "sql-server-01"
        assert parts["Database"] == "ECAD"
        assert withheld == []

    def test_the_declaration_never_carries_a_secret(self) -> None:
        library = load_dbl(
            {
                "source": {
                    "password": "hunter2",
                    "connection_string": "Pwd={P@ss;word};Client Secret=zzz;Server=db1",
                },
                "libraries": [],
            }
        )
        rendered = library.declaration().model_dump_json()

        for secret in ("hunter2", "P@ss", "word}", "zzz"):
            assert secret not in rendered
        assert WITHHELD in rendered


class TestRegexGuard:
    """Security HIGH: a query word compiled as a regex could hang the process."""

    @pytest.mark.parametrize(
        "pattern",
        ["^(a+)+$", "(a|a)*b", "^(a|aa)+$", "^((a)*)*$", "(a{2}){3}"],
    )
    def test_a_nested_quantifier_is_refused(self, pattern: str) -> None:
        assert regex_is_safe(pattern) is False

    @pytest.mark.parametrize(
        "pattern",
        ["^r_[0-9]+$", "r_.*all", "^gnd$", "[abc]+", "^(abc)$", "a|b"],
    )
    def test_an_ordinary_pattern_is_allowed(self, pattern: str) -> None:
        assert regex_is_safe(pattern) is True

    def test_a_long_pattern_is_refused(self) -> None:
        assert regex_is_safe("a" * (MAX_REGEX_PATTERN_LENGTH + 1)) is False

    def test_a_refused_word_scores_zero_rather_than_hanging(self) -> None:
        record = PartRecord(name="x", fields={"Description": "a" * 60 + "!"})

        assert score_record(record, "^(a+)+$").score == 0

    def test_a_legitimate_regex_query_still_matches(self) -> None:
        assert score_record(PartRecord(name="R_100", nickname="d"), "/^r_[0-9]+$/").score > 0

    def test_a_wildcard_query_still_matches(self) -> None:
        assert score_record(PartRecord(name="R_Small", nickname="d"), "r_*all").score > 0


class TestUntrustedRowAndFileCaps:
    """Security MEDIUM: no caps on a hostile .kicad_dbl or a hostile row."""

    def _sub_library(self, mappings: int) -> DatabaseSubLibrary:
        return DatabaseSubLibrary(
            name="x",
            table="t",
            key="k",
            symbols="sym",
            footprints="fp",
            fields=[DatabaseFieldMapping(column=f"c{i}", name=f"F{i}") for i in range(mappings)],
        )

    def test_a_long_row_value_is_truncated(self) -> None:
        sub = self._sub_library(1)
        row = {"k": "1", "sym": "", "fp": "", "c0": "z" * (MAX_ROW_VALUE_LENGTH + 5000)}

        definition = definition_from_row(row, sub)

        assert len(next(f.value for f in definition.fields if f.name == "F0")) == (
            MAX_ROW_VALUE_LENGTH
        )

    def test_the_footprint_token_count_is_capped(self) -> None:
        sub = self._sub_library(0)
        row = {"k": "1", "sym": "", "fp": ";".join(f"L:F{i}" for i in range(1000))}

        definition = definition_from_row(row, sub)

        assert len(definition.fp_filters) == MAX_FOOTPRINT_TOKENS

    def test_many_mappings_stay_linear(self) -> None:
        """The field lookup is a dict, so this is not quadratic."""
        sub = self._sub_library(5000)
        row = {"k": "1", "sym": "", "fp": "", **{f"c{i}": "v" for i in range(5000)}}

        definition = definition_from_row(row, sub)

        assert len(definition.fields) == 5005

    def test_a_deeply_nested_dbl_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "deep.kicad_dbl"
        depth = MAX_JSON_DEPTH + 5
        path.write_text("[" * depth + "]" * depth, encoding="utf-8")

        with pytest.raises(KicadParseError, match="nests deeper"):
            load_kicad_dbl(path)

    def test_a_real_shaped_dbl_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.kicad_dbl"
        path.write_text(
            json.dumps(
                {
                    "name": "X",
                    "source": {"type": "odbc"},
                    "libraries": [
                        {
                            "name": "R",
                            "table": "R",
                            "key": "k",
                            "symbols": "s",
                            "footprints": "f",
                            "fields": [{"column": "c", "name": "C"}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        assert load_kicad_dbl(path).libraries[0].name == "R"


class TestPlacedFieldEffects:
    """The operator's report: field text carried no style block."""

    def test_a_created_field_carries_the_default_effects(self) -> None:
        sub = DatabaseSubLibrary(
            name="x",
            table="t",
            key="k",
            symbols="sym",
            footprints="fp",
            fields=[DatabaseFieldMapping(column="c", name="NewField")],
        )
        definition = definition_from_row({"k": "1", "sym": "", "fp": "", "c": "v"}, sub)

        created = next(f for f in definition.fields if f.name == "NewField")
        assert created.attributes is not None
        assert created.attributes.font_size == (1.27, 1.27)

    def test_a_field_that_already_had_a_style_keeps_it(self) -> None:
        symbol = Symbol.new("PART")
        existing = next(f for f in symbol.fields if f.name == "Value")
        existing.attributes = TextAttributes(font_size=(2.54, 2.54), bold=True)

        sub = DatabaseSubLibrary(
            name="x",
            table="t",
            key="k",
            symbols="sym",
            footprints="fp",
            fields=[DatabaseFieldMapping(column="c", name="Value")],
        )
        definition = definition_from_row(
            {"k": "1", "sym": "L:PART", "fp": "", "c": "10k"},
            sub,
            symbol_loader=lambda _id: symbol,
        )

        value = next(f for f in definition.fields if f.name == "Value")
        assert value.attributes is not None
        assert value.attributes.font_size == (2.54, 2.54)
        assert value.attributes.bold is True

    def test_a_property_with_no_style_still_writes_none(self) -> None:
        """The WRITER is unchanged: 61 legacy corpus fields carry no block."""
        from eedesigncore.sch.writer import _dump_placed_property

        bare = Property(name="X", value="y", visible=False)
        assert "(effects" not in _dump_placed_property(bare)
