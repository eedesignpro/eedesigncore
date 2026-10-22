"""Reading a ``.kicad_dbl``, and turning a row into a symbol definition.

The field values here are shaped after real production files.
The whole-corpus check lives in ``scripts/database_oracle.py``, which compares
309 KiCad-written definitions against this transform.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eedesigncore.common.models import Property
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.exceptions import KicadParseError
from eedesigncore.library import (
    PartRecord,
    database_records,
    definition_from_row,
    load_kicad_dbl,
    record_from_row,
    sanitize_database_key,
)
from eedesigncore.library.database import bool_from_value, is_valid_lib_id
from eedesigncore.library.dbl import WITHHELD, load_dbl

DBL = {
    "meta": {"version": 1},
    "name": "Example",
    "description": "Example ECAD Component Library",
    "source": {
        "type": "odbc",
        "dsn": "",
        "username": "reader",
        "password": "hunter2",
        "timeout_seconds": 30,
        "connection_string": (
            "Driver={ODBC Driver 18 for SQL Server};Server=a-server;"
            "Database=ECAD;PWD=hunter2;Trusted_Connection=yes"
        ),
    },
    "cache": {"max_size": 100, "max_age": 28800},
    "globally_unique_keys": True,
    "libraries": [
        {
            "name": "Resistors",
            "table": "Resistors",
            "key": "KiCadKey",
            "symbols": "SymbolRef",
            "footprints": "FootprintRef",
            "properties": {
                "description": "AXDescription",
                "keywords": "pair_keywords",
                "exclude_from_bom": "no_bom",
            },
            "fields": [
                {"column": "IPN", "name": "IPN", "visible_on_add": False},
                {
                    "column": "value",
                    "name": "Value",
                    "visible_on_add": True,
                    "show_name": False,
                },
                {"column": "datasheet_url", "name": "Datasheet", "visible_on_add": False},
                {
                    "column": "Tolerance",
                    "name": "Tolerance",
                    "visible_on_add": True,
                    "show_name": True,
                },
                {
                    "column": "Inherited",
                    "name": "Inherited",
                    "visible_on_add": True,
                    "inherit_properties": True,
                },
                {"column": "FootprintRef", "name": "Footprint"},
                {"column": "AbsentColumn", "name": "NeverSet"},
            ],
        }
    ],
}

ROW = {
    "KiCadKey": "PN-00457__1788472695932",
    "IPN": "PN-00457",
    "AXDescription": "RES_10K_1%_0.05W_0201",
    "SymbolRef": "company_Resistors:R__1787704718667",
    "FootprintRef": "company_Resistors:RESC0603X23L;company_Resistors:RESC1005X40L",
    "Tolerance": "1%",
    "value": "10k",
    "datasheet_url": "https://example.invalid/ds",
    "pair_keywords": "aplfc:active aplock:unlocked",
    "no_bom": "false",
    "Inherited": "inherited-value",
}


@pytest.fixture
def library():
    return load_dbl(DBL)


class TestSanitizeKey:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("PN-00457__1788472695932", "PN-00457__1788472695932"),
            ("PN-01582:116981", "PN-01582_116981"),
            ("a\tb\nc\rd", "a_b_c_d"),
            ('back\\slash<gt>quote"', "back_slash_gt_quote_"),
            ("with space", "with space"),
        ],
    )
    def test_only_the_illegal_characters_change(self, raw: str, expected: str) -> None:
        assert sanitize_database_key(raw) == expected


class TestLoadDbl:
    def test_the_file_is_read(self, library) -> None:
        assert library.name == "Example"
        assert library.source.type == "odbc"
        assert library.globally_unique_keys is True
        assert library.cache.max_age == 28800
        assert len(library.libraries) == 1

    def test_the_connection_string_is_split_and_the_secret_withheld(self, library) -> None:
        source = library.source
        assert source.driver == "ODBC Driver 18 for SQL Server"
        assert source.server == "a-server"
        assert source.database == "ECAD"
        assert source.connection_parts["PWD"] == WITHHELD
        assert source.withheld_keys == ["PWD"]

    def test_the_source_password_is_reported_but_never_copied(self, library) -> None:
        assert library.source.password_present is True
        assert "hunter2" not in library.declaration().model_dump_json()

    def test_the_column_list_is_the_select_list(self, library) -> None:
        columns = library.libraries[0].columns
        assert columns[:3] == ["KiCadKey", "SymbolRef", "FootprintRef"]
        assert columns.count("FootprintRef") == 1
        assert "AXDescription" in columns
        assert "Tolerance" in columns

    def test_the_declaration_carries_the_read_description(self, library) -> None:
        table = library.declaration().table_for("Resistors")
        assert table is not None
        assert table.table == "Resistors"
        assert table.key_column == "KiCadKey"
        assert table.symbol_column == "SymbolRef"
        assert table.footprint_column == "FootprintRef"
        assert len(table.fields) == 7

    def test_a_slash_is_removed_from_a_sub_library_name(self) -> None:
        data = json.loads(json.dumps(DBL))
        data["libraries"][0]["name"] = "Resistors/Fixed"
        assert load_dbl(data).libraries[0].name == "ResistorsFixed"

    def test_kicads_defaults_apply_to_an_empty_file(self) -> None:
        empty = load_dbl({})
        assert empty.cache.max_size == 256
        assert empty.cache.max_age == 10
        assert empty.source.timeout_seconds == 2
        assert empty.globally_unique_keys is False
        assert empty.libraries == []

    def test_an_entry_missing_a_required_key_is_skipped(self) -> None:
        data = json.loads(json.dumps(DBL))
        data["libraries"].append({"name": "Broken", "table": "Broken"})
        assert [entry.name for entry in load_dbl(data).libraries] == ["Resistors"]

    def test_a_non_object_file_raises(self) -> None:
        with pytest.raises(KicadParseError):
            load_dbl([1, 2, 3])

    def test_a_file_that_is_not_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.kicad_dbl"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(KicadParseError):
            load_kicad_dbl(path)

    def test_a_file_round_trips_from_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "partsdb.kicad_dbl"
        path.write_text(json.dumps(DBL), encoding="utf-8")
        assert load_kicad_dbl(path).name == "Example"


class TestDisplayName:
    def test_globally_unique_keys_drops_the_prefix(self, library) -> None:
        entry = library.libraries[0]
        assert entry.display_name("PN-01582:116981", globally_unique_keys=True) == (
            "PN-01582_116981"
        )

    def test_without_the_flag_the_sub_library_prefixes(self, library) -> None:
        entry = library.libraries[0]
        assert entry.display_name("K", globally_unique_keys=False) == "Resistors/K"


class TestBoolFromValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("TRUE", True),
            ("yes", True),
            ("Y", True),
            ("1", True),
            ("false", False),
            ("no", False),
            ("n", False),
            ("0", False),
            ("", None),
            ("maybe", None),
            (None, None),
            (1.5, None),
        ],
    )
    def test_the_permissive_cast(self, value, expected) -> None:
        assert bool_from_value(value) is expected


class TestLibIdValidity:
    @pytest.mark.parametrize(
        ("text", "valid"),
        [
            ("lib:name", True),
            ("name", False),
            (":name", False),
            ("lib:", False),
            ("", False),
            ("lib:a:b", False),
            ("lib:has space", True),
            ("lib:bad\\slash", False),
        ],
    )
    def test_both_halves_must_be_present(self, text: str, valid: bool) -> None:
        assert is_valid_lib_id(text) is valid


class TestDefinitionFromRow:
    def _source(self) -> Symbol:
        symbol = Symbol.new("R__1787704718667", reference="R", value="R")
        symbol.keywords = "resistor"
        return symbol

    def _derive(self, row=None, *, with_source: bool = True) -> Symbol:
        library = load_dbl(DBL)
        source = self._source()
        return definition_from_row(
            ROW if row is None else row,
            library.libraries[0],
            globally_unique_keys=library.globally_unique_keys,
            symbol_loader=(lambda _id: source) if with_source else None,
        )

    def test_the_name_is_the_sanitized_key(self) -> None:
        assert self._derive().name == "PN-00457__1788472695932"

    def test_the_first_footprint_token_becomes_the_field(self) -> None:
        derived = self._derive()
        footprint = next(f for f in derived.fields if f.name == "Footprint")
        assert footprint.value == "company_Resistors:RESC0603X23L"

    def test_every_footprint_token_becomes_a_filter(self) -> None:
        assert self._derive().fp_filters == [
            "company_Resistors:RESC0603X23L",
            "company_Resistors:RESC1005X40L",
        ]

    def test_the_properties_block_is_applied(self) -> None:
        derived = self._derive()
        description = next(f for f in derived.fields if f.name == "Description")
        assert description.value == "RES_10K_1%_0.05W_0201"
        assert derived.keywords == "aplfc:active aplock:unlocked"
        assert derived.in_bom is True  # no_bom reads "false"

    def test_an_exclude_flag_that_reads_true_inverts_the_member(self) -> None:
        row = dict(ROW, no_bom="yes")
        assert self._derive(row).in_bom is False

    def test_an_exclude_flag_that_will_not_cast_is_ignored(self) -> None:
        row = dict(ROW, no_bom="perhaps")
        assert self._derive(row).in_bom is True

    def test_the_value_and_datasheet_mappings_write_the_builtin_fields(self) -> None:
        derived = self._derive()
        value = next(f for f in derived.fields if f.name == "Value")
        datasheet = next(f for f in derived.fields if f.name == "Datasheet")
        assert value.value == "10k"
        assert value.visible is True
        assert datasheet.value == "https://example.invalid/ds"
        assert datasheet.visible is False

    def test_a_new_field_takes_its_flags_from_the_mapping(self) -> None:
        tolerance = next(f for f in self._derive().fields if f.name == "Tolerance")
        assert tolerance.value == "1%"
        assert tolerance.visible is True
        assert tolerance.show_name is True

    def test_a_mapping_onto_the_footprint_column_is_skipped(self) -> None:
        # The Footprint mapping names FootprintRef, which step 4 already used,
        # so it must not overwrite the first-token value with the whole string.
        footprint = next(f for f in self._derive().fields if f.name == "Footprint")
        assert ";" not in footprint.value

    def test_a_column_absent_from_the_row_creates_no_field(self) -> None:
        assert all(f.name != "NeverSet" for f in self._derive().fields)

    def test_inherit_properties_leaves_an_existing_field_alone(self) -> None:
        library = load_dbl(DBL)
        source = self._source()
        source.fields.append(Property(name="Inherited", value="old", visible=False))
        derived = definition_from_row(
            ROW,
            library.libraries[0],
            globally_unique_keys=True,
            symbol_loader=lambda _id: source,
        )
        inherited = next(f for f in derived.fields if f.name == "Inherited")
        assert inherited.value == "inherited-value"
        assert inherited.visible is False

    def test_inherit_properties_still_applies_to_a_new_field(self) -> None:
        inherited = next(f for f in self._derive().fields if f.name == "Inherited")
        assert inherited.visible is True

    def test_no_source_symbol_yields_a_placeable_metadata_only_symbol(self) -> None:
        derived = self._derive(with_source=False)
        assert derived.name == "PN-00457__1788472695932"
        assert next(f for f in derived.fields if f.name == "Value").value == "10k"
        assert derived.units == []

    def test_an_invalid_symbol_reference_never_reaches_the_loader(self) -> None:
        asked: list[str] = []

        def loader(lib_id: str) -> Symbol | None:
            asked.append(lib_id)
            return None

        library = load_dbl(DBL)
        definition_from_row(
            dict(ROW, SymbolRef="no-nickname"),
            library.libraries[0],
            globally_unique_keys=True,
            symbol_loader=loader,
        )
        assert asked == []

    def test_the_source_symbol_is_not_mutated(self) -> None:
        library = load_dbl(DBL)
        source = self._source()
        definition_from_row(
            ROW,
            library.libraries[0],
            globally_unique_keys=True,
            symbol_loader=lambda _id: source,
        )
        assert source.name == "R__1787704718667"
        assert source.keywords == "resistor"

    def test_a_row_with_no_key_raises(self) -> None:
        library = load_dbl(DBL)
        with pytest.raises(ValueError, match="key column"):
            definition_from_row({"IPN": "x"}, library.libraries[0])


class TestRecordFromRow:
    def test_a_record_needs_no_symbol_load(self) -> None:
        library = load_dbl(DBL)
        record = record_from_row(
            ROW,
            library.libraries[0],
            nickname="partsdb",
            globally_unique_keys=True,
        )
        assert isinstance(record, PartRecord)
        assert record.lib_id == "partsdb:PN-00457__1788472695932"
        assert record.source == "database"
        assert record.sub_library == "Resistors"
        assert record.fields["IPN"] == "PN-00457"
        assert record.fields["Tolerance"] == "1%"
        assert record.fields["Footprint"] == "company_Resistors:RESC0603X23L"
        assert record.description == "RES_10K_1%_0.05W_0201"
        assert record.keywords == "aplfc:active aplock:unlocked"

    def test_the_records_walk_skips_a_row_with_no_key(self) -> None:
        library = load_dbl(DBL)
        rows = [ROW, {"IPN": "no key here"}]
        records = list(database_records(library, {"Resistors": rows}, nickname="partsdb"))
        assert [r.fields["IPN"] for r in records] == ["PN-00457"]

    def test_an_unknown_sub_library_key_is_ignored(self) -> None:
        library = load_dbl(DBL)
        assert list(database_records(library, {"NotAThing": [ROW]})) == []
