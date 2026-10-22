"""Byte-identity round-trip tests for ``.kicad_pro`` and ``.kicad_prl``.

Fixture provenance
------------------
The template project and local settings files are verbatim copies of an
in-house KiCad 10 project template. ``tests/private_inputs.py`` names them, and
the public copy does not carry them. That project is also the source of the
template board fixture. The project file carries the KiCad 10 marker
``schematic.top_level_sheets``, 25 text variables, and all six nested settings
blocks.

``hand_edited_ascii_escapes.kicad_pro`` is a reduced exemplar of a real project
file in a private corpus. That file writes non-ASCII as ``\\u00b0`` and uses
CRLF line endings. KiCad writes raw UTF-8 and LF, so the file was rewritten by
another tool. It belongs to the same project as the hand-edited board of
ADR-044.

``empty_no_trailing_newline.kicad_pro`` is a verbatim copy of the two-byte
project template found at ``C:/KiCad/KiCad-Templates/Project/default/``. KiCad
always ends a JSON file with one line feed, so those two bytes are not KiCad
output either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.pro import ProjectFile, ProjectLocalSettings, dump_json, parse_json
from tests.private_inputs import private_path

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pro"
KICAD_WRITTEN = [
    (private_path("template_project", ".kicad_pro"), ProjectFile),
    (private_path("template_project_local", ".kicad_prl"), ProjectLocalSettings),
]


class TestByteIdentity:
    @pytest.mark.parametrize(
        ("path", "model"), KICAD_WRITTEN, ids=lambda v: getattr(v, "name", "")
    )
    def test_round_trip_is_byte_identical(
        self, path: Path, model: type[ProjectFile] | type[ProjectLocalSettings], tmp_path: Path
    ) -> None:
        raw = path.read_bytes()
        out = tmp_path / path.name
        model.from_file(path).to_file(out)
        assert out.read_bytes() == raw

    @pytest.mark.parametrize(
        ("path", "model"), KICAD_WRITTEN, ids=lambda v: getattr(v, "name", "")
    )
    def test_to_string_reproduces_the_source_text(
        self, path: Path, model: type[ProjectFile] | type[ProjectLocalSettings]
    ) -> None:
        assert model.from_file(path).to_string() == path.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("path", "model"), KICAD_WRITTEN, ids=lambda v: getattr(v, "name", "")
    )
    def test_a_kicad_written_file_is_a_layout_fixed_point(
        self, path: Path, model: type[ProjectFile] | type[ProjectLocalSettings]
    ) -> None:
        text = path.read_text(encoding="utf-8")
        assert dump_json(parse_json(text), canonical_numbers=True) == text

    def test_the_source_files_use_line_feeds_and_no_byte_order_mark(self) -> None:
        for path, _model in KICAD_WRITTEN:
            raw = path.read_bytes()
            assert b"\r\n" not in raw
            assert not raw.startswith(b"\xef\xbb\xbf")
            assert raw.endswith(b"\n")
            assert not raw.endswith(b"\n\n")


class TestNotKicadWritten:
    def test_the_ascii_escape_exemplar_is_not_a_fixed_point(self) -> None:
        text = (FIXTURES / "hand_edited_ascii_escapes.kicad_pro").read_text(encoding="utf-8")
        assert dump_json(parse_json(text.replace("\r\n", "\n")), canonical_numbers=True) != text

    def test_the_ascii_escape_exemplar_gains_raw_utf8_on_write(self) -> None:
        path = FIXTURES / "hand_edited_ascii_escapes.kicad_pro"
        project = ProjectFile.from_file(path)
        assert project.text_variables["PCB_STACKUP"] == "minimum Tg 170°C."
        assert "\\u00b0" not in project.to_string()
        assert "170°C" in project.to_string()

    def test_the_empty_template_gains_the_missing_line_feed(self) -> None:
        path = FIXTURES / "empty_no_trailing_newline.kicad_pro"
        assert path.read_bytes() == b"{}"
        assert ProjectFile.from_file(path).to_string() == "{}\n"

    def test_crlf_line_endings_are_preserved_on_write(self, tmp_path: Path) -> None:
        path = FIXTURES / "hand_edited_ascii_escapes.kicad_pro"
        out = tmp_path / path.name
        ProjectFile.from_file(path).to_file(out)
        assert b"\r\n" in out.read_bytes()
