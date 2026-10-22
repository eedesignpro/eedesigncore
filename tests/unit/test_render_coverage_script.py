"""The render coverage audit must stay honest, and this is what keeps it so.

The audit's value rests on one property: a KiCad case nobody has classified makes
it fail rather than quietly vanish from the denominator. A property that is never
exercised is a property nobody should trust, so these tests exercise it.

The audit reads the pinned KiCad clone. Without that clone there is nothing to
grade against, so these tests SKIP and say which piece is missing. A skip must
never read as a pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.private_inputs import private_path

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "render_coverage.py"


def load_script() -> ModuleType:
    """Import the audit as a module, since ``scripts/`` is not a package."""
    spec = importlib.util.spec_from_file_location("render_coverage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_coverage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit() -> ModuleType:
    if not SCRIPT.is_file():
        pytest.skip(f"{SCRIPT} is missing, so the audit cannot be graded")
    return load_script()


@pytest.fixture(scope="module")
def kicad_root(audit: ModuleType) -> Path:
    default = audit.kicad_root()
    # The maintainer machine names its clone in tests/private_inputs.py.
    root = default if default.is_dir() else private_path("kicad_source")
    if not root.is_dir():
        pytest.skip(
            f"the pinned KiCad clone is not at {default}; set "
            f"{audit.KICAD_ROOT_ENV} to grade the audit"
        )
    return Path(root)


# ---------------------------------------------------------------------------
# The tables are internally consistent, with no clone needed
# ---------------------------------------------------------------------------


def test_every_gap_carries_a_reason(audit: ModuleType) -> None:
    """A gap with no reason is a gap nobody decided on."""
    gaps = {name for name, model in audit.KICAD_TYPE_TO_MODEL.items() if model is None}
    assert gaps == set(audit.GAP_REASONS), (
        "KICAD_TYPE_TO_MODEL and GAP_REASONS disagree about which types are gaps"
    )


def test_no_reason_is_recorded_for_a_covered_type(audit: ModuleType) -> None:
    """A reason next to a covered type reads as a gap that is not one."""
    for name in audit.GAP_REASONS:
        assert audit.KICAD_TYPE_TO_MODEL.get(name, "") is None, (
            f"{name} has a gap reason but is mapped to a model"
        )


def test_every_stream_has_recorded_coverage(audit: ModuleType) -> None:
    assert set(audit.PLOT_STREAMS) == set(audit.STREAM_COVERAGE)


def test_every_enum_axis_has_a_mapping_table(audit: ModuleType) -> None:
    for _header, _enum_name, key in audit.ENUM_AXES:
        assert key in audit.ENUM_MEMBER_TO_TOKEN, f"no ENUM_MEMBER_TO_TOKEN entry for {key}"


def test_each_known_limit_names_a_file_and_a_citation(audit: ModuleType) -> None:
    for label, relative, citation, reason in audit.KNOWN_LIMITS:
        assert label and relative and citation and reason
        assert relative.endswith((".cpp", ".h")), f"{label}: {relative} is not a source file"


# ---------------------------------------------------------------------------
# Against the clone
# ---------------------------------------------------------------------------


def test_the_audit_is_current(audit: ModuleType, kicad_root: Path) -> None:
    """``--check`` must pass. A failure here is the audit doing its job."""
    assert audit.main(["--check", "--kicad-root", str(kicad_root)]) == 0


def test_the_audit_reads_the_plot_switch_rather_than_a_hard_coded_list(
    audit: ModuleType, kicad_root: Path
) -> None:
    """The expected set comes out of KiCad, which is the whole point."""
    plotted = audit.plotted_item_types(kicad_root)
    assert "PCB_SHAPE_T" in plotted
    assert "PCB_TEXT_T" in plotted
    assert len(plotted) >= 10, "the switch parse returned implausibly few cases"
    assert all(name.startswith("PCB_") and name.endswith("_T") for name in plotted)


def test_an_unclassified_kicad_case_makes_the_audit_fail(
    audit: ModuleType, kicad_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anti-staleness property, exercised rather than asserted in a comment.

    Dropping one entry stands in for KiCad growing a case nobody has looked at.
    The audit must refuse, because the alternative is a coverage percentage that
    silently stops counting the thing it does not know about.
    """
    trimmed = dict(audit.KICAD_TYPE_TO_MODEL)
    removed, _ = trimmed.popitem()
    monkeypatch.setattr(audit, "KICAD_TYPE_TO_MODEL", trimmed)

    axis = audit.audit_item_types(kicad_root)
    assert axis.errors, f"dropping {removed} did not make the audit complain"
    assert any(removed in problem for problem in axis.errors)
    assert audit.main(["--check", "--kicad-root", str(kicad_root)]) == 1


def test_a_broken_citation_makes_the_audit_fail(
    audit: ModuleType, kicad_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim about KiCad's source must fail when the source stops saying it."""
    monkeypatch.setattr(
        audit,
        "PLOT_STREAMS",
        {"pads": ("plot_board_layers.cpp", "for( PAD* pad : footprint->NoSuchAccessor() )")},
    )
    monkeypatch.setattr(audit, "STREAM_COVERAGE", {"pads": "BoardContent.pads"})

    axis = audit.audit_streams(kicad_root)
    assert axis.errors
    assert "no longer appears" in axis.errors[0]


def test_a_claimed_model_the_renderer_does_not_dispatch_on_fails(
    audit: ModuleType, kicad_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table cannot claim coverage the code does not provide."""
    lying = dict(audit.KICAD_TYPE_TO_MODEL)
    lying["PCB_TARGET_T"] = "PcbTargetThatHasNoEmitter"
    monkeypatch.setattr(audit, "KICAD_TYPE_TO_MODEL", lying)

    axis = audit.audit_item_types(kicad_root)
    assert any("PCB_TARGET_T" in problem for problem in axis.errors)


def test_the_enum_parse_finds_the_real_members(audit: ModuleType, kicad_root: Path) -> None:
    """Comments and trailing commas in the header must not leak into the members."""
    members = audit.enum_members(kicad_root, "pcbnew/padstack.h", "PAD_SHAPE")
    assert members == [
        "CIRCLE",
        "RECTANGLE",
        "OVAL",
        "TRAPEZOID",
        "ROUNDRECT",
        "CHAMFERED_RECT",
        "CUSTOM",
    ]


def test_a_missing_function_is_reported_rather_than_guessed(
    audit: ModuleType, kicad_root: Path
) -> None:
    """A rename upstream is itself the finding, so it must not pass silently."""
    source = audit.read_source(kicad_root, "pcbnew/plot_brditems_plotter.cpp")
    with pytest.raises(LookupError):
        audit.function_body(source, "BRDITEMS_PLOTTER::NoSuchFunction")


def test_a_missing_clone_exits_two_rather_than_reporting_full_coverage(
    audit: ModuleType, tmp_path: Path
) -> None:
    """The worst failure mode would be an audit that grades nothing and says 100%."""
    assert audit.main(["--kicad-root", str(tmp_path / "not-here")]) == 2


# ---------------------------------------------------------------------------
# What the audit reads out of this package
# ---------------------------------------------------------------------------


def test_the_dispatch_parse_finds_the_renderer_types(audit: ModuleType) -> None:
    """Read from the AST of ``shapes_for``, not from a list kept beside it."""
    dispatched = audit.dispatched_types()
    assert {"PcbLine", "PcbRect", "PcbArc", "PcbCircle", "PcbPoly", "Pad", "Zone"} <= set(
        dispatched
    )


def test_the_pad_branch_parse_finds_every_shape(audit: ModuleType) -> None:
    branches = set(audit.pad_shape_branches())
    assert {"circle", "rect", "oval", "trapezoid", "roundrect", "custom"} <= branches


def test_the_union_in_an_isinstance_test_counts_both_members(audit: ModuleType) -> None:
    """``isinstance(item, TrackSegment | TrackArc)`` is two covered types, not one."""
    names = audit.dispatched_types()
    assert "TrackSegment" in names
    assert "TrackArc" in names


# ---------------------------------------------------------------------------
# The axes added after the first pass missed them
# ---------------------------------------------------------------------------


def test_every_uncovered_driver_has_a_reason(audit: ModuleType) -> None:
    gaps = {name for name, row in audit.LAYER_DRIVERS.items() if row[2] is None}
    assert gaps == set(audit.DRIVER_GAP_REASONS)


def test_every_uncovered_conditional_item_has_a_reason(audit: ModuleType) -> None:
    gaps = {name for name, row in audit.CONDITIONAL_CONTENT.items() if row[2] is None}
    assert gaps == set(audit.CONDITIONAL_GAP_REASONS)


def test_every_board_content_stream_is_graded(audit: ModuleType) -> None:
    """A stream added to BoardContent with no citation overstates coverage.

    This is the hole the first pass had: `tracks` and `zones` were marked covered
    with no source line behind them.
    """
    import eedesigncore.render.traversal as traversal

    streams = {member.value for member in traversal.BoardContent}
    graded = {"drawings", *audit.PLOT_STREAMS, *audit.BEYOND_KICAD_STREAMS}
    assert streams <= graded, f"ungraded BoardContent streams: {streams - graded}"


def test_the_defect_list_is_empty(audit: ModuleType, kicad_root: Path) -> None:
    """No geometry may reach the page uninvited.

    A missing primitive is scope. An EXTRA primitive is ink the board does not
    hold, which is the one class the oracle treats as a failure rather than a
    limit. A new entry here is a regression, not a record.
    """
    defects = audit.audit_defects(kicad_root)
    assert not defects.gaps, f"new defects: {sorted(defects.gaps)}"
    assert not defects.errors


def test_a_defect_never_counts_inside_the_coverage_total(audit: ModuleType) -> None:
    """A defect and a gap must not share one number.

    Totalling them would let a defect hide inside a coverage percentage. The
    check runs on a synthetic defect, so it holds even while the real list is
    empty.
    """
    axis = audit.Axis("defects", "synthetic")
    axis.gaps["a made-up defect"] = "for the test only"
    report = audit.render_text([axis], audit.kicad_root())
    assert "DEFECTS  1 recorded" in report
    assert "TOTAL  0/0 graded cases covered" in report


def test_a_step_past_kicad_is_recorded_as_such(audit: ModuleType) -> None:
    """Rule areas went from a defect to a named opt-in stream.

    The citation must still name the line where KiCad declines to draw them,
    because that line is what makes the step deliberate.
    """
    assert "rule_areas" in audit.BEYOND_KICAD_STREAMS
    _relative, citation, _note = audit.BEYOND_KICAD_STREAMS["rule_areas"]
    assert "GetIsRuleArea" in citation


def test_a_beyond_kicad_stream_must_not_sit_in_kicad_content(
    audit: ModuleType, kicad_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KICAD_CONTENT is a parity baseline, so a step past KiCad cannot be in it."""
    monkeypatch.setattr(audit, "_kicad_content_names", lambda: {"rule_areas"})
    axis = audit.audit_streams(kicad_root)
    assert any("KICAD_CONTENT holds streams KiCad does not draw" in e for e in axis.errors)
