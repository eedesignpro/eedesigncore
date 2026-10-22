"""The geometry comparison that stands in for a pixel comparison.

The comparison tool needs its own tests. An oracle nobody checked is not an
oracle. These tests pin what it must see and what it must ignore.
"""

from __future__ import annotations

from eedesigncore.render.compare import compare_svg, nearest_gap, read_primitives


def wrap(body: str) -> str:
    return f'<svg viewBox="0 0 10 10">{body}</svg>'


# ---------------------------------------------------------------------------
# What the comparison must ignore
# ---------------------------------------------------------------------------


def test_a_closed_path_equals_four_separate_segments() -> None:
    """This renderer draws one closed path. KiCad draws four thick segments."""
    closed = wrap('<path d="M0,0 L2,0 L2,1 L0,1 Z"/>')
    separate = wrap(
        '<path d="M0,0 L2,0"/><path d="M2,0 L2,1"/><path d="M2,1 L0,1"/><path d="M0,1 L0,0"/>'
    )
    assert compare_svg(closed, separate).agrees


def test_a_rect_element_equals_the_same_closed_path() -> None:
    rect = wrap('<rect x="0" y="0" width="2" height="1"/>')
    path = wrap('<path d="M0,0 L2,0 L2,1 L0,1 Z"/>')
    assert compare_svg(rect, path).agrees


def test_a_reversed_segment_is_the_same_segment() -> None:
    assert compare_svg(wrap('<path d="M0,0 L2,3"/>'), wrap('<path d="M2,3 L0,0"/>')).agrees


def test_an_arc_folds_to_one_direction() -> None:
    """A to B with sweep 0 draws what B to A with sweep 1 draws."""
    forward = wrap('<path d="M10,0 A10 10 0 0 1 0,10"/>')
    backward = wrap('<path d="M0,10 A10 10 0 0 0 10,0"/>')
    assert compare_svg(forward, backward).agrees


def test_order_does_not_matter() -> None:
    first = wrap('<path d="M0,0 L1,1"/><path d="M5,5 L6,6"/>')
    second = wrap('<path d="M5,5 L6,6"/><path d="M0,0 L1,1"/>')
    assert compare_svg(first, second).agrees


def test_color_and_width_do_not_enter_the_comparison() -> None:
    plain = wrap('<path d="M0,0 L1,1"/>')
    styled = wrap('<path d="M0,0 L1,1" stroke="#FF0000" stroke-width="9"/>')
    assert compare_svg(plain, styled).agrees


def test_an_implicit_lineto_after_a_moveto_counts() -> None:
    """KiCad writes a polygon as one moveto and a list of bare coordinate pairs."""
    kicad_form = wrap('<path d="M 0,0\n2,0\n2,1\nZ"/>')
    explicit = wrap('<path d="M0,0 L2,0 L2,1 Z"/>')
    assert compare_svg(kicad_form, explicit).agrees


def test_a_difference_inside_the_tolerance_still_agrees() -> None:
    assert compare_svg(wrap('<path d="M0,0 L1,1"/>'), wrap('<path d="M0,0 L1.0002,1"/>')).agrees


# ---------------------------------------------------------------------------
# What the comparison must catch
# ---------------------------------------------------------------------------


def test_a_moved_point_is_caught() -> None:
    difference = compare_svg(wrap('<path d="M0,0 L1,1"/>'), wrap('<path d="M0,0 L1,2"/>'))
    assert not difference.agrees
    assert len(difference.only_in_left) == 1
    assert len(difference.only_in_right) == 1


def test_a_missing_shape_is_caught() -> None:
    difference = compare_svg(
        wrap('<path d="M0,0 L1,1"/><path d="M2,2 L3,3"/>'),
        wrap('<path d="M0,0 L1,1"/>'),
    )
    assert len(difference.only_in_left) == 1
    assert difference.matched == 1


def test_a_changed_radius_is_caught() -> None:
    difference = compare_svg(
        wrap('<circle cx="1" cy="1" r="2"/>'), wrap('<circle cx="1" cy="1" r="3"/>')
    )
    assert not difference.agrees


def test_an_arc_is_not_the_same_as_its_chord() -> None:
    difference = compare_svg(
        wrap('<path d="M10,0 A10 10 0 0 1 0,10"/>'), wrap('<path d="M10,0 L0,10"/>')
    )
    assert not difference.agrees


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_the_summary_names_both_sides() -> None:
    difference = compare_svg(wrap('<path d="M0,0 L1,1"/>'), wrap('<path d="M0,0 L1,2"/>'))
    assert "1 only on the left" in difference.summary()
    assert "1 only on the right" in difference.summary()


def test_the_summary_of_agreement_counts_the_matches() -> None:
    difference = compare_svg(wrap('<path d="M0,0 L1,1"/>'), wrap('<path d="M0,0 L1,1"/>'))
    assert difference.summary() == "agree: 1 primitives matched"


def test_nearest_gap_measures_how_far_a_mismatch_sits() -> None:
    """A caller uses this to tell a real disagreement from a tolerance edge."""
    left = read_primitives(wrap('<path d="M0,0 L1,1"/>'))
    (primitive,) = list(read_primitives(wrap('<path d="M0,0 L1,2"/>')))
    assert nearest_gap(primitive, left) == 1.0


def test_nearest_gap_reports_none_when_no_candidate_shares_the_kind() -> None:
    (primitive,) = list(read_primitives(wrap('<circle cx="0" cy="0" r="1"/>')))
    assert nearest_gap(primitive, read_primitives(wrap('<path d="M0,0 L1,1"/>'))) is None


def test_values_astride_the_grid_boundary_still_pair() -> None:
    """A real board hit this: 252.650 against 252.649, agreeing to a thousandth.

    Snapping to a grid put the two on opposite sides. Without the reconcile pass
    the comparison reports a defect that its own rounding created.
    """
    difference = compare_svg(
        wrap('<path d="M0,0 A0.75 0.75 0 0 1 252.6495,131.75"/>'),
        wrap('<path d="M0,0 A0.75 0.75 0 0 1 252.6494,131.75"/>'),
    )
    assert difference.agrees
    assert difference.matched == 1


def test_the_reconcile_pass_does_not_hide_a_real_gap() -> None:
    """Twice the tolerance is the limit. A tenth of a millimeter must still fail."""
    difference = compare_svg(wrap('<path d="M0,0 L1,1"/>'), wrap('<path d="M0,0 L1.1,1"/>'))
    assert not difference.agrees
