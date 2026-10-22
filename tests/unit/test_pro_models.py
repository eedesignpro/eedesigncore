"""Unit tests for the ``.kicad_pro`` and ``.kicad_prl`` models and loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pro import (
    PRL_SCHEMA_VERSION,
    PRO_SCHEMA_VERSION,
    V10_MARKER_PATH,
    JsonTree,
    MetaBlock,
    ProjectFile,
    ProjectLocalSettings,
    load_prl,
    load_pro,
    parse_json,
)
from tests.private_inputs import private_path, private_text

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pro"
PRO = private_path("template_project", ".kicad_pro")
PRL = private_path("template_project_local", ".kicad_prl")


class TestLoad:
    def test_load_pro_accepts_a_parsed_tree(self) -> None:
        project = load_pro(parse_json('{"meta": {"version": 3}}'))
        assert isinstance(project, ProjectFile)
        assert project.meta.version == 3

    def test_load_prl_accepts_a_parsed_tree(self) -> None:
        settings = load_prl(parse_json('{"meta": {"version": 5}}'))
        assert isinstance(settings, ProjectLocalSettings)
        assert settings.meta.version == 5

    @pytest.mark.parametrize("text", ["[]", "3"])
    def test_load_pro_rejects_a_non_object_root(self, text: str) -> None:
        with pytest.raises(KicadParseError, match="kicad_pro"):
            load_pro(parse_json(text))

    def test_load_prl_rejects_a_non_object_root(self) -> None:
        with pytest.raises(KicadParseError, match="kicad_prl"):
            load_prl(parse_json("[]"))

    def test_from_string_rejects_malformed_json(self) -> None:
        with pytest.raises(KicadParseError, match="Invalid JSON"):
            ProjectFile.from_string("{not json}")

    def test_from_string_rejects_a_non_object_root(self) -> None:
        with pytest.raises(KicadParseError, match="root is not an object"):
            ProjectLocalSettings.from_string("[]")

    def test_an_incomplete_project_file_still_loads(self) -> None:
        # A project KiCad 10 has not re-saved carries no component_class_settings
        # block at all (audit section 8.8). The loader must not demand one.
        project = ProjectFile.from_string('{"board": {}}')
        assert project.section_names() == ["board"]

    def test_a_default_model_holds_an_empty_tree(self) -> None:
        assert ProjectFile().root == JsonTree()
        assert ProjectFile().to_string() == "{}\n"


class TestSchemaConstants:
    def test_the_two_schema_versions_match_the_kicad_constants(self) -> None:
        assert ProjectFile.schema_version == PRO_SCHEMA_VERSION == 3
        assert ProjectLocalSettings.schema_version == PRL_SCHEMA_VERSION == 5

    def test_each_model_names_its_extension(self) -> None:
        assert ProjectFile.file_extension == "kicad_pro"
        assert ProjectLocalSettings.file_extension == "kicad_prl"


class TestMetaBlock:
    def test_reads_the_fixture_meta_block(self) -> None:
        meta = ProjectFile.from_file(PRO).meta
        assert meta == MetaBlock(filename=PRO.name, version=3)

    def test_reads_the_local_settings_meta_block(self) -> None:
        meta = ProjectLocalSettings.from_file(PRL).meta
        assert meta.filename == PRL.name
        assert meta.version == 5

    def test_absent_keys_read_as_none(self) -> None:
        assert ProjectFile.from_string("{}").meta == MetaBlock()
        assert ProjectFile.from_string('{"meta": {}}').meta == MetaBlock()

    def test_the_view_is_frozen(self) -> None:
        with pytest.raises(ValueError, match="frozen"):
            ProjectFile.from_string("{}").meta.version = 4  # type: ignore[misc]

    def test_a_wrong_typed_version_is_an_error(self) -> None:
        with pytest.raises(KicadParseError, match="integer"):
            _ = ProjectFile.from_string('{"meta": {"version": "3"}}').meta


class TestSections:
    def test_section_names_are_sorted(self) -> None:
        names = ProjectFile.from_file(PRO).section_names()
        assert names == sorted(names)
        assert "net_settings" in names

    def test_section_returns_a_write_through_view(self) -> None:
        project = ProjectFile.from_string('{"board": {"a": 1}}')
        board = project.section("board")
        assert board is not None
        board.set("b", 2)
        assert project.to_string() == '{\n  "board": {\n    "a": 1,\n    "b": 2\n  }\n}\n'

    def test_section_returns_none_when_absent(self) -> None:
        assert ProjectFile.from_string("{}").section("board") is None

    def test_value_at_walks_a_dotted_path(self) -> None:
        project = ProjectFile.from_file(PRO)
        assert project.value_at("meta.version") == 3
        assert project.value_at("pcbnew.page_layout_descr_file") is not None
        assert project.value_at("does.not.exist") is None


class TestProjectFileAccessors:
    def test_the_fixture_carries_the_v10_marker(self) -> None:
        assert ProjectFile.from_file(PRO).has_v10_marker is True

    def test_a_project_without_the_marker_is_reported(self) -> None:
        assert ProjectFile.from_string('{"schematic": {}}').has_v10_marker is False
        assert ProjectFile.from_string("{}").has_v10_marker is False

    def test_the_marker_path_is_the_kicad_10_parameter(self) -> None:
        assert V10_MARKER_PATH == "schematic.top_level_sheets"

    def test_reads_the_top_level_sheets(self) -> None:
        sheets = ProjectFile.from_file(PRO).top_level_sheets
        assert len(sheets) == 1
        assert sheets[0].get_str("filename") == PRO.with_suffix(".kicad_sch").name

    def test_top_level_sheets_is_empty_when_absent(self) -> None:
        assert ProjectFile.from_string("{}").top_level_sheets == []
        not_a_list = ProjectFile.from_string('{"schematic": {"top_level_sheets": 3}}')
        assert not_a_list.top_level_sheets == []

    def test_reads_the_text_variables(self) -> None:
        variables = ProjectFile.from_file(PRO).text_variables
        assert variables["PCBA_REV"] == "01"
        assert variables["PART_ID"] == private_text("template_part_id")

    def test_text_variables_is_empty_when_absent(self) -> None:
        assert ProjectFile.from_string("{}").text_variables == {}

    def test_a_non_string_text_variable_is_an_error(self) -> None:
        project = ProjectFile.from_string('{"text_variables": {"A": 3}}')
        with pytest.raises(KicadParseError, match="text_variables.A"):
            _ = project.text_variables

    def test_reads_the_bus_aliases(self) -> None:
        project = ProjectFile.from_string('{"schematic": {"bus_aliases": {"DATA": ["D0", "D1"]}}}')
        assert project.bus_aliases == {"DATA": ["D0", "D1"]}

    def test_bus_aliases_is_empty_when_absent(self) -> None:
        assert ProjectFile.from_string("{}").bus_aliases == {}
        assert ProjectFile.from_string('{"schematic": {}}').bus_aliases == {}
        assert ProjectFile.from_file(PRO).bus_aliases == {}

    def test_a_non_string_bus_member_is_an_error(self) -> None:
        project = ProjectFile.from_string('{"schematic": {"bus_aliases": {"D": [1]}}}')
        with pytest.raises(KicadParseError, match="bus_aliases.D"):
            _ = project.bus_aliases


class TestPreserveOnlyPolicy:
    def test_an_unknown_key_survives_the_round_trip(self) -> None:
        text = '{\n  "git": {\n    "repo_password": ""\n  }\n}\n'
        assert ProjectFile.from_string(text).to_string() == text

    def test_no_registered_default_is_injected(self) -> None:
        # KiCad's own save would grow this file (audit contradiction C5).
        # This library emits exactly what it read (ADR-049).
        assert ProjectFile.from_string("{}").to_string() == "{}\n"

    def test_an_edit_produces_a_single_line_change(self) -> None:
        project = ProjectFile.from_file(PRO)
        before = project.to_string().splitlines()
        variables = project.section("text_variables")
        assert variables is not None
        variables.set("PCBA_REV", "02")
        after = project.to_string().splitlines()
        changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
        assert changed == [('    "PCBA_REV": "01",', '    "PCBA_REV": "02",')]
