"""Unit tests for ``common/io_mixin.py`` — EOL sniff and preserve mode."""

from __future__ import annotations

from pathlib import Path

from eedesigncore.common.io_mixin import _resolve_line_ending, _sniff_line_ending
from eedesigncore.mod.models import Footprint
from eedesigncore.sym.models import SymbolLib

FIXTURE_MOD = Path(__file__).parent.parent / "fixtures" / "mod" / "R_0402_1005Metric.kicad_mod"
FIXTURE_SYM = Path(__file__).parent.parent / "fixtures" / "sym" / "Device.kicad_sym"


# ---------------------------------------------------------------------------
# _sniff_line_ending
# ---------------------------------------------------------------------------


class TestSniffLineEnding:
    def test_sniff_crlf(self) -> None:
        assert _sniff_line_ending(b"hello\r\nworld") == "crlf"

    def test_sniff_lf(self) -> None:
        assert _sniff_line_ending(b"hello\nworld") == "lf"

    def test_sniff_crlf_beyond_4096(self) -> None:
        # \r\n past 4096 should NOT be detected — sniff only reads 4096 bytes
        data = b"a" * 4096 + b"\r\n"
        assert _sniff_line_ending(data) == "lf"

    def test_sniff_crlf_within_4096(self) -> None:
        data = b"a" * 10 + b"\r\n" + b"b" * 5000
        assert _sniff_line_ending(data) == "crlf"

    def test_sniff_empty(self) -> None:
        assert _sniff_line_ending(b"") == "lf"


# ---------------------------------------------------------------------------
# _resolve_line_ending
# ---------------------------------------------------------------------------


class TestResolveLineEnding:
    def test_explicit_lf(self) -> None:
        assert _resolve_line_ending("lf", "crlf") == "lf"
        assert _resolve_line_ending("lf", None) == "lf"

    def test_explicit_crlf(self) -> None:
        assert _resolve_line_ending("crlf", "lf") == "crlf"
        assert _resolve_line_ending("crlf", None) == "crlf"

    def test_preserve_recorded_lf(self) -> None:
        assert _resolve_line_ending("preserve", "lf") == "lf"

    def test_preserve_recorded_crlf(self) -> None:
        assert _resolve_line_ending("preserve", "crlf") == "crlf"

    def test_preserve_no_recorded_eol_is_crlf(self) -> None:
        # operator-confirmed: in-memory objects → fixed CRLF, NOT os.linesep
        assert _resolve_line_ending("preserve", None) == "crlf"

    def test_native_returns_string(self) -> None:
        result = _resolve_line_ending("native", None)
        assert result in ("lf", "crlf")


# ---------------------------------------------------------------------------
# KicadFileMixin via Footprint
# ---------------------------------------------------------------------------


class TestFootprintMixin:
    def test_from_file_records_lf(self, tmp_path: Path) -> None:
        # Write an LF file; from_file should sniff "lf"
        src = FIXTURE_MOD.read_bytes()
        lf_bytes = src.replace(b"\r\n", b"\n")
        p = tmp_path / "test.kicad_mod"
        p.write_bytes(lf_bytes)
        fp = Footprint.from_file(p)
        assert fp._source_line_ending == "lf"

    def test_from_file_records_crlf(self, tmp_path: Path) -> None:
        src = FIXTURE_MOD.read_bytes()
        lf_bytes = src.replace(b"\r\n", b"\n")
        crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
        p = tmp_path / "test.kicad_mod"
        p.write_bytes(crlf_bytes)
        fp = Footprint.from_file(p)
        assert fp._source_line_ending == "crlf"

    def test_from_string_no_eol_attr(self) -> None:
        text = FIXTURE_MOD.read_text(encoding="utf-8")
        fp = Footprint.from_string(text)
        assert fp._source_line_ending is None

    def test_to_file_preserve_crlf_roundtrip(self, tmp_path: Path) -> None:
        # Source file has CRLF → preserve → output must be CRLF bytes
        src = FIXTURE_MOD.read_bytes()
        lf_bytes = src.replace(b"\r\n", b"\n")
        crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
        src_file = tmp_path / "src.kicad_mod"
        src_file.write_bytes(crlf_bytes)

        fp = Footprint.from_file(src_file)
        out_file = tmp_path / "out.kicad_mod"
        fp.to_file(out_file)  # default "preserve"
        out_bytes = out_file.read_bytes()
        assert b"\r\n" in out_bytes
        assert b"\r\n" not in out_bytes.replace(b"\r\n", b"XX")  # no bare \r

    def test_to_file_preserve_lf_roundtrip(self, tmp_path: Path) -> None:
        # Source file has LF only → preserve → output must be LF bytes
        src = FIXTURE_MOD.read_bytes()
        lf_bytes = src.replace(b"\r\n", b"\n")
        src_file = tmp_path / "src.kicad_mod"
        src_file.write_bytes(lf_bytes)

        fp = Footprint.from_file(src_file)
        out_file = tmp_path / "out.kicad_mod"
        fp.to_file(out_file)
        out_bytes = out_file.read_bytes()
        assert b"\r\n" not in out_bytes

    def test_to_file_explicit_lf(self, tmp_path: Path) -> None:
        # Regardless of source EOL, explicit "lf" → LF only
        src = FIXTURE_MOD.read_bytes()
        crlf_bytes = src.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        src_file = tmp_path / "src.kicad_mod"
        src_file.write_bytes(crlf_bytes)

        fp = Footprint.from_file(src_file)
        out_file = tmp_path / "out.kicad_mod"
        fp.to_file(out_file, line_ending="lf")
        out_bytes = out_file.read_bytes()
        assert b"\r\n" not in out_bytes

    def test_to_file_explicit_crlf(self, tmp_path: Path) -> None:
        # Regardless of source EOL, explicit "crlf" → CRLF
        src = FIXTURE_MOD.read_bytes()
        lf_bytes = src.replace(b"\r\n", b"\n")
        src_file = tmp_path / "src.kicad_mod"
        src_file.write_bytes(lf_bytes)

        fp = Footprint.from_file(src_file)
        out_file = tmp_path / "out.kicad_mod"
        fp.to_file(out_file, line_ending="crlf")
        out_bytes = out_file.read_bytes()
        assert b"\r\n" in out_bytes

    def test_to_file_preserve_no_recorded_eol_writes_crlf(self, tmp_path: Path) -> None:
        # from_string (no recorded EOL) + preserve → CRLF, never raises
        text = FIXTURE_MOD.read_text(encoding="utf-8")
        fp = Footprint.from_string(text)
        assert fp._source_line_ending is None

        out_file = tmp_path / "out.kicad_mod"
        fp.to_file(out_file)  # default "preserve"
        out_bytes = out_file.read_bytes()
        assert b"\r\n" in out_bytes

    def test_to_string_always_lf(self) -> None:
        text = FIXTURE_MOD.read_text(encoding="utf-8")
        fp = Footprint.from_string(text)
        result = fp.to_string()
        assert "\r\n" not in result
        assert "\n" in result


# ---------------------------------------------------------------------------
# KicadFileMixin via SymbolLib
# ---------------------------------------------------------------------------


class TestSymbolLibMixin:
    def test_from_string_no_eol_attr(self) -> None:
        text = FIXTURE_SYM.read_text(encoding="utf-8")
        lib = SymbolLib.from_string(text)
        assert lib._source_line_ending is None

    def test_to_file_preserve_no_recorded_eol_writes_crlf(self, tmp_path: Path) -> None:
        text = FIXTURE_SYM.read_text(encoding="utf-8")
        lib = SymbolLib.from_string(text)
        out_file = tmp_path / "out.kicad_sym"
        lib.to_file(out_file)
        out_bytes = out_file.read_bytes()
        assert b"\r\n" in out_bytes

    def test_from_file_records_lf(self, tmp_path: Path) -> None:
        src = FIXTURE_SYM.read_bytes().replace(b"\r\n", b"\n")
        p = tmp_path / "test.kicad_sym"
        p.write_bytes(src)
        lib = SymbolLib.from_file(p)
        assert lib._source_line_ending == "lf"
