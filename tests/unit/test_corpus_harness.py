"""Unit tests for the corpus harness not-KiCad-written classifier.

Two classifiers are covered. The S-expression side uses the KiCad Prettify
fixed-point test. The JSON side uses the equivalent nlohmann-layout fixed-point
test that ADR-050 defines, plus the structural KiCad 10 marker that replaces
``meta.version`` for project files.

Fixture provenance
------------------
``tests/fixtures/pcb/hand_edited_property_indent.kicad_pcb`` is a reduced
exemplar of a real board in a private corpus. That board carries four
``(property "GIT_*" …)`` rows indented with two tabs, while every sibling
property row uses one tab. KiCad Prettify indents by list
depth alone, so it cannot emit two indentation widths for siblings. The
KiCad-written backup of the same project (2026-07-15) holds one tab and only one
of the GIT rows, which confirms a later hand edit. The exemplar copies the
template board fixture and adds the same defect on two rows.

The template board and project fixtures are named in ``tests/private_inputs.py``.
The public copy does not carry them.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from eedesigncore.sexpr.writer import prettify
from tests.private_inputs import private_path

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pcb"
KICAD_WRITTEN = private_path("template_board", ".kicad_pcb")
HAND_EDITED = FIXTURES / "hand_edited_property_indent.kicad_pcb"

PRO_FIXTURES = Path(__file__).parent.parent / "fixtures" / "pro"
PRO_WRITTEN = private_path("template_project", ".kicad_pro")
PRL_WRITTEN = private_path("template_project_local", ".kicad_prl")
PRO_ASCII_ESCAPED = PRO_FIXTURES / "hand_edited_ascii_escapes.kicad_pro"
PRO_NO_NEWLINE = PRO_FIXTURES / "empty_no_trailing_newline.kicad_pro"


def _load_harness() -> ModuleType:
    """Import ``scripts/corpus_test.py`` by path. It is a script, not a package."""
    path = Path(__file__).parent.parent.parent / "scripts" / "corpus_test.py"
    spec = importlib.util.spec_from_file_location("corpus_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["corpus_test"] = module
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()

# The named exclusion table names private corpus files, so the public copy does
# not carry it. A test that reads an entry skips without it.
requires_named_exclusions = pytest.mark.skipif(
    not HARNESS.NAMED_EXCLUSIONS_FILE.is_file(),
    reason="the private corpus exclusion table is absent",
)


def _lf_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


class TestPrettifyFixedPoint:
    def test_kicad_written_board_is_a_fixed_point(self) -> None:
        text = _lf_text(KICAD_WRITTEN)
        assert prettify(text, mode="NORMAL") == text

    def test_hand_edited_board_is_not_a_fixed_point(self) -> None:
        text = _lf_text(HAND_EDITED)
        assert prettify(text, mode="NORMAL") != text

    def test_prettify_restores_one_tab_on_every_property_row(self) -> None:
        text = _lf_text(HAND_EDITED)
        result = prettify(text, mode="NORMAL")
        rows = [ln for ln in result.splitlines() if '(property "' in ln]
        assert rows, "exemplar must carry board property rows"
        assert all(ln.startswith("\t(property") for ln in rows)

    def test_prettify_changes_only_the_mis_indented_rows(self) -> None:
        text = _lf_text(HAND_EDITED)
        result = prettify(text, mode="NORMAL")
        changed = [
            (a, b) for a, b in zip(text.splitlines(), result.splitlines(), strict=True) if a != b
        ]
        assert len(changed) == 2
        assert all(a == "\t" + b for a, b in changed)

    def test_undecodable_bytes_count_as_a_fixed_point(self) -> None:
        assert HARNESS._is_prettify_fixed_point(b"\xff\xfe(kicad_pcb)") is True

    def test_helper_agrees_with_the_two_fixtures(self) -> None:
        assert HARNESS._is_prettify_fixed_point(KICAD_WRITTEN.read_bytes()) is True
        assert HARNESS._is_prettify_fixed_point(HAND_EDITED.read_bytes()) is False


class TestCheckFileClassification:
    def test_kicad_written_board_is_ok(self) -> None:
        result = HARNESS.check_file(str(KICAD_WRITTEN))
        assert result.status == HARNESS.OK

    def test_hand_edited_board_is_not_kicad_written(self) -> None:
        result = HARNESS.check_file(str(HAND_EDITED))
        assert result.status == HARNESS.NOT_KICAD_WRITTEN
        assert "first_diff@" in result.detail

    def test_not_kicad_written_does_not_count_as_a_failure(self) -> None:
        assert HARNESS.NOT_KICAD_WRITTEN in HARNESS._NON_FAILURES
        assert HARNESS.BYTE_MISMATCH not in HARNESS._NON_FAILURES

    def test_report_lists_the_not_kicad_written_bucket(self) -> None:
        results = [HARNESS.check_file(str(HAND_EDITED))]
        report = HARNESS.render_report(results, 0.1, 1, 3)
        assert "## Not KiCad-written (1 files)" in report
        assert "hand_edited_property_indent.kicad_pcb" in report


class TestGlobalEditorArtifactExclusion:
    """The global rule of ADR-066: editor backup trees never grade."""

    @pytest.mark.parametrize(
        "relative",
        [
            ".history/board.kicad_pcb",
            "kicad/.history/board.kicad_pcb",
            "proj-backups/board.kicad_pcb",
            "proj-backups/nested/board.kicad_pcb",
            "kicad/My_Board-Backups/board.kicad_sch",
            ".HISTORY/lib.kicad_sym",
        ],
        ids=[
            "history-root",
            "history-nested",
            "backups-root",
            "backups-nested",
            "backups-mixed-case",
            "history-upper-case",
        ],
    )
    def test_an_editor_artifact_path_is_excluded(self, tmp_path: Path, relative: str) -> None:
        path = tmp_path / relative
        assert HARNESS._is_editor_artifact(path) is True
        assert HARNESS.exclusion_reason(path) is not None

    @pytest.mark.parametrize(
        "relative",
        [
            "board.kicad_pcb",
            "history/board.kicad_pcb",
            "backups/board.kicad_pcb",
            "my-backups.kicad_pcb",
        ],
        ids=["plain", "history-without-dot", "backups-without-prefix", "suffix-on-the-file"],
    )
    def test_an_ordinary_path_is_not_excluded(self, tmp_path: Path, relative: str) -> None:
        path = tmp_path / relative
        assert HARNESS._is_editor_artifact(path) is False
        assert HARNESS.exclusion_reason(path) is None

    def test_check_file_reports_an_artifact_copy_as_excluded(self, tmp_path: Path) -> None:
        artifact = tmp_path / "proj-backups" / KICAD_WRITTEN.name
        artifact.parent.mkdir()
        artifact.write_bytes(KICAD_WRITTEN.read_bytes())
        result = HARNESS.check_file(str(artifact))
        assert result.status == HARNESS.EXCLUDED
        assert result.ext == "pcb"
        assert "editor-artifact" in result.detail

    def test_the_rule_beats_a_file_that_would_otherwise_round_trip(self, tmp_path: Path) -> None:
        # The same bytes grade as ok outside a backup tree, which proves the
        # exclusion is the reason and not a round-trip failure.
        plain = tmp_path / KICAD_WRITTEN.name
        plain.write_bytes(KICAD_WRITTEN.read_bytes())
        assert HARNESS.check_file(str(plain)).status == HARNESS.OK


@requires_named_exclusions
class TestNamedExclusions:
    """The named rule of ADR-066: five removed files, each with a dated reason.

    Keys are path SUFFIXES, not absolute paths (LOW-11). A suffix drops the drive
    letter and the operator directory tree, so the table is portable and carries
    no path outside the corpus itself.

    Four entries carry the operator ruling of 2026-08-13. The fifth, added in
    iteration 11.8, carries the kicad-cli oracle verdict of 2026-08-18 instead
    (ADR-075): KiCad itself rewrites that file, so it is not a byte-identity
    oracle, and the harness proves it rather than a person asserting it.

    The entries name private corpus files, so the table lives in
    ``scripts/corpus_named_exclusions.json``. The public copy does not carry it,
    and this class then skips.
    """

    _DATED_AUTHORITIES = (
        "operator ruling 2026-08-13",
        "kicad-cli oracle 2026-08-18",
        "operator ruling 2026-09-03",
    )

    def test_every_entry_cites_a_dated_authority(self) -> None:
        assert HARNESS.NAMED_EXCLUSIONS
        for reason in HARNESS.NAMED_EXCLUSIONS.values():
            assert any(authority in reason for authority in self._DATED_AUTHORITIES), reason

    def test_the_removed_files_are_listed(self) -> None:
        names = sorted(Path(key).name for key in HARNESS.NAMED_EXCLUSIONS)
        assert len(set(names)) == 6
        assert sorted(Path(name).suffix for name in names) == [
            ".kicad_pcb",
            ".kicad_pcb",
            ".kicad_sch",
            ".kicad_sch",
            ".kicad_sch",
            ".kicad_sch",
        ]

    def test_no_entry_is_an_absolute_path(self) -> None:
        """LOW-11: no drive letter, no UNC root, no leading slash."""
        for key in HARNESS.NAMED_EXCLUSIONS:
            assert not Path(key).is_absolute(), key
            assert ":" not in key, key
            assert not key.startswith(("/", "\\")), key

    def test_every_entry_still_names_a_parent_directory(self) -> None:
        """A bare file name would over-match. Each key keeps its project directory."""
        for key in HARNESS.NAMED_EXCLUSIONS:
            assert "/" in key, key

    def test_a_named_suffix_is_excluded_with_its_reason(self, tmp_path: Path) -> None:
        for key, reason in HARNESS.NAMED_EXCLUSIONS.items():
            path = tmp_path / "any" / "operator" / "root" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            assert HARNESS.exclusion_reason(path) == reason

    def test_the_same_suffix_matches_under_any_root(self, tmp_path: Path) -> None:
        """Portability: the entry no longer depends on one machine's corpus root."""
        key = next(iter(HARNESS.NAMED_EXCLUSIONS))
        first = tmp_path / "root_a" / key
        second = tmp_path / "root_b" / "deeper" / "still" / key
        assert HARNESS.exclusion_reason(first) == HARNESS.NAMED_EXCLUSIONS[key]
        assert HARNESS.exclusion_reason(second) == HARNESS.NAMED_EXCLUSIONS[key]

    def test_the_match_ignores_slash_direction_and_case(self, tmp_path: Path) -> None:
        key = next(iter(HARNESS.NAMED_EXCLUSIONS))
        assert HARNESS.exclusion_reason(tmp_path / key.replace("/", "\\")) is not None
        assert HARNESS.exclusion_reason(tmp_path / key.upper()) is not None

    def test_the_suffix_anchors_at_a_directory_boundary(self, tmp_path: Path) -> None:
        """A longer directory name that merely ENDS with the key directory must not match."""
        key = next(iter(HARNESS.NAMED_EXCLUSIONS))
        directory, _, name = key.partition("/")
        assert HARNESS.exclusion_reason(tmp_path / f"prefix{directory}" / name) is None

    def test_a_same_named_file_elsewhere_still_grades(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "power.kicad_sch"
        assert HARNESS.exclusion_reason(elsewhere) is None

    def test_a_sibling_file_in_a_named_directory_still_grades(self, tmp_path: Path) -> None:
        key = next(iter(HARNESS.NAMED_EXCLUSIONS))
        directory = key.partition("/")[0]
        assert HARNESS.exclusion_reason(tmp_path / directory / "sibling.kicad_sch") is None


class TestExclusionReporting:
    def test_excluded_does_not_count_as_a_failure(self) -> None:
        assert HARNESS.EXCLUDED in HARNESS._NON_FAILURES
        assert HARNESS.EXCLUDED in HARNESS._STATUSES

    @requires_named_exclusions
    def test_the_report_lists_every_excluded_file_with_its_rule(self, tmp_path: Path) -> None:
        artifact = tmp_path / "proj-backups" / "b.kicad_pcb"
        artifact.parent.mkdir()
        artifact.write_bytes(b"()")
        named = next(iter(HARNESS.NAMED_EXCLUSIONS))
        results = [HARNESS.check_file(str(artifact)), HARNESS.check_file(named)]
        report = HARNESS.render_report(results, 0.1, 1, 3)
        assert "## Excluded (2 files)" in report
        assert "editor-artifact" in report
        assert Path(named).name in report

    def test_the_totals_table_carries_an_excluded_row(self, tmp_path: Path) -> None:
        artifact = tmp_path / ".history" / "b.kicad_pcb"
        artifact.parent.mkdir()
        artifact.write_bytes(b"()")
        report = HARNESS.render_report([HARNESS.check_file(str(artifact))], 0.1, 1, 3)
        assert "| excluded | 1 | 1 |" in report


class TestJsonFixedPoint:
    def test_a_kicad_written_project_file_is_a_fixed_point(self) -> None:
        assert HARNESS._is_json_fixed_point(PRO_WRITTEN.read_bytes()) is True
        assert HARNESS._is_json_fixed_point(PRL_WRITTEN.read_bytes()) is True

    def test_ascii_escaped_text_is_not_a_fixed_point(self) -> None:
        assert HARNESS._is_json_fixed_point(PRO_ASCII_ESCAPED.read_bytes()) is False

    def test_a_missing_trailing_line_feed_is_not_a_fixed_point(self) -> None:
        assert HARNESS._is_json_fixed_point(PRO_NO_NEWLINE.read_bytes()) is False

    def test_a_hand_written_number_is_not_a_fixed_point(self) -> None:
        assert HARNESS._is_json_fixed_point(b'{\n  "a": 1.50\n}\n') is False
        assert HARNESS._is_json_fixed_point(b'{\n  "a": 1.5\n}\n') is True

    def test_unsorted_keys_are_not_a_fixed_point(self) -> None:
        assert HARNESS._is_json_fixed_point(b'{\n  "b": 1,\n  "a": 2\n}\n') is False

    def test_undecodable_bytes_count_as_a_fixed_point(self) -> None:
        assert HARNESS._is_json_fixed_point(b"\xff\xfe{}") is True

    def test_unparseable_text_is_not_a_fixed_point(self) -> None:
        assert HARNESS._is_json_fixed_point(b"{not json}") is False

    def test_valid_json_detection_separates_a_parser_defect(self) -> None:
        assert HARNESS._is_valid_json(b"{}") is True
        assert HARNESS._is_valid_json(b"{") is False
        assert HARNESS._is_valid_json(b"\xff\xfe") is False


class TestJsonVersionMarker:
    def test_the_fixture_project_carries_the_marker(self) -> None:
        assert HARNESS._has_v10_marker(PRO_WRITTEN.read_bytes()) is True

    def test_a_project_without_the_marker_is_reported(self) -> None:
        assert HARNESS._has_v10_marker(b'{"schematic": {}}') is False

    def test_malformed_or_non_object_text_carries_no_marker(self) -> None:
        assert HARNESS._has_v10_marker(b"{") is False
        assert HARNESS._has_v10_marker(b"[]") is False
        assert HARNESS._has_v10_marker(b"\xff\xfe") is False

    def test_local_settings_inherit_the_verdict_of_the_sibling_project(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "p.kicad_pro").write_bytes(PRO_WRITTEN.read_bytes())
        prl = tmp_path / "p.kicad_prl"
        prl.write_bytes(PRL_WRITTEN.read_bytes())
        assert HARNESS._project_is_v10(prl, prl.read_bytes()) is True

    def test_local_settings_without_a_sibling_project_are_not_v10(self, tmp_path: Path) -> None:
        prl = tmp_path / "p.kicad_prl"
        prl.write_bytes(PRL_WRITTEN.read_bytes())
        assert HARNESS._project_is_v10(prl, prl.read_bytes()) is False


class TestJsonCheckFileClassification:
    def test_a_kicad_written_project_file_is_ok(self) -> None:
        assert HARNESS.check_file(str(PRO_WRITTEN)).status == HARNESS.OK
        assert HARNESS.check_file(str(PRL_WRITTEN)).status == HARNESS.OK

    def test_the_two_extensions_get_their_own_buckets(self) -> None:
        assert HARNESS.check_file(str(PRO_WRITTEN)).ext == "pro"
        assert HARNESS.check_file(str(PRL_WRITTEN)).ext == "prl"

    def test_an_edited_project_file_is_not_kicad_written(self) -> None:
        result = HARNESS.check_file(str(PRO_ASCII_ESCAPED))
        assert result.status == HARNESS.NOT_KICAD_WRITTEN
        assert "first_diff@" in result.detail

    def test_a_two_byte_template_is_not_kicad_written(self) -> None:
        assert HARNESS.check_file(str(PRO_NO_NEWLINE)).status == HARNESS.NOT_KICAD_WRITTEN

    def test_invalid_json_is_not_kicad_written(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.kicad_pro"
        path.write_bytes(b'{"a": {')
        result = HARNESS.check_file(str(path))
        assert result.status == HARNESS.NOT_KICAD_WRITTEN
        assert "Invalid JSON" in result.detail

    def test_an_unreadable_path_is_an_other_error(self, tmp_path: Path) -> None:
        result = HARNESS.check_file(str(tmp_path / "absent.kicad_prl"))
        assert result.status == HARNESS.OTHER_ERROR

    def test_a_legacy_project_file_still_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.kicad_pro"
        path.write_bytes(b'{\n  "meta": {\n    "version": 3\n  }\n}\n')
        assert HARNESS.check_file(str(path)).status == HARNESS.OK

    def test_a_mismatch_without_the_marker_is_version_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A KiCad 9 project file that this library fails to reproduce must not
        # fail the gate. The preserve-only writer reaches this branch for no
        # corpus file today, so the mismatch is injected.
        path = tmp_path / "legacy.kicad_pro"
        path.write_bytes(b'{\n  "meta": {\n    "version": 3\n  }\n}\n')
        monkeypatch.setattr(HARNESS, "_expected_bytes", lambda obj: b"different\n")
        assert HARNESS.check_file(str(path)).status == HARNESS.VERSION_SKIPPED

    def test_a_mismatch_with_the_marker_is_a_byte_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "current.kicad_pro"
        path.write_bytes(PRO_WRITTEN.read_bytes())
        monkeypatch.setattr(HARNESS, "_expected_bytes", lambda obj: b"different\n")
        assert HARNESS.check_file(str(path)).status == HARNESS.BYTE_MISMATCH

    def test_a_write_failure_is_an_other_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "current.kicad_pro"
        path.write_bytes(b"{}\n")

        def _boom(obj: object) -> bytes:
            raise RuntimeError("write failed")

        monkeypatch.setattr(HARNESS, "_expected_bytes", _boom)
        result = HARNESS.check_file(str(path))
        assert result.status == HARNESS.OTHER_ERROR
        assert "write RuntimeError" in result.detail

    def test_the_discover_flag_covers_both_project_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "a.kicad_pro").write_bytes(b"{}\n")
        (tmp_path / "a.kicad_prl").write_bytes(b"{}\n")
        (tmp_path / "a.kicad_pcb").write_bytes(b"()")
        found = [Path(p).name for p in HARNESS.discover(tmp_path, "pro")]
        assert found == ["a.kicad_prl", "a.kicad_pro"]

    def test_the_report_splits_the_two_project_columns(self) -> None:
        results = [HARNESS.check_file(str(PRO_WRITTEN)), HARNESS.check_file(str(PRL_WRITTEN))]
        report = HARNESS.render_report(results, 0.1, 1, 3)
        assert "| .kicad_pro | .kicad_prl |" in report
        assert "| ok | 1 | 1 | 2 |" in report
