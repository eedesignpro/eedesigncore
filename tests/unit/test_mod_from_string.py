"""Tests for Footprint.from_string (T3 — N1 fix: embedded-data path)."""

from __future__ import annotations

from pathlib import Path

from eedesigncore.mod.models import Footprint

FIXTURE_EMBEDDED = (
    Path(__file__).parent.parent
    / "fixtures"
    / "mod"
    / "CAP_DISK_5.9MM_TH_Cornell_Dubilier.kicad_mod"
)


class TestFromStringEmbeddedData:
    """Smoke tests ensuring from_string shares one code path with from_file."""

    def test_from_string_embedded_data_no_error(self) -> None:
        """Calling from_string on an embedded-data fixture must not raise."""
        text = FIXTURE_EMBEDDED.read_text(encoding="utf-8")
        fp = Footprint.from_string(text)
        assert fp.fp_id == "CAP_DISK_5.9MM_TH_Cornell_Dubilier"

    def test_from_string_embedded_files_populated(self) -> None:
        text = FIXTURE_EMBEDDED.read_text(encoding="utf-8")
        fp = Footprint.from_string(text)
        assert len(fp.embedded_files) >= 1
        assert fp.embedded_files[0].data  # non-empty

    def test_from_string_matches_from_file(self) -> None:
        """to_string() from from_string must equal to_string() from from_file."""
        text = FIXTURE_EMBEDDED.read_text(encoding="utf-8")
        fp_str = Footprint.from_string(text)
        fp_file = Footprint.from_file(FIXTURE_EMBEDDED)
        assert fp_str.to_string() == fp_file.to_string()
