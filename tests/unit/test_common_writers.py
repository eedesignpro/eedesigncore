"""Unit tests for common/writers.py — shared write helpers (S1)."""

from __future__ import annotations

from eedesigncore.common.models import (
    Color,
    EmbeddedFile,
    Fill,
    FillType,
    JustifyH,
    Stroke,
    StrokeType,
    TextAttributes,
)
from eedesigncore.common.writers import (
    MIME_BASE64_LENGTH,
    _chunk_base64,
    dump_bool,
    dump_color,
    dump_effects,
    dump_embedded_files,
    dump_field_effects,
    dump_fill_pcb,
    dump_fill_sch,
    dump_stroke,
    esc,
    fmt_angle,
    fmt_double,
    fmt_iu,
    q,
)
from eedesigncore.mod.models import Footprint
from eedesigncore.sexpr.writer import prettify


class TestChunkBase64:
    def test_short_data_no_newline(self) -> None:
        result = _chunk_base64("AAAA")
        assert result == "|AAAA|"

    def test_exactly_76_chars(self) -> None:
        data = "A" * MIME_BASE64_LENGTH
        result = _chunk_base64(data)
        assert result == f"|{'A' * 76}|"
        assert "\n" not in result

    def test_76_plus_remainder(self) -> None:
        data = "A" * 76 + "B" * 20
        result = _chunk_base64(data)
        assert result == f"|{'A' * 76}\n{'B' * 20}|"

    def test_three_chunks(self) -> None:
        data = "A" * 76 + "B" * 76 + "C" * 10
        result = _chunk_base64(data)
        parts = result[1:-1].split("\n")
        assert len(parts) == 3
        assert len(parts[0]) == 76
        assert len(parts[1]) == 76
        assert len(parts[2]) == 10


class TestDumpEmbeddedFiles:
    def test_empty_list_returns_empty_string(self) -> None:
        assert dump_embedded_files([]) == ""

    def test_single_file_flat_structure(self) -> None:
        ef = EmbeddedFile(name="foo.step", file_type="model", data="AAAA", checksum="abc123")
        result = dump_embedded_files([ef])
        assert result.startswith("(embedded_files")
        assert "(name" in result
        assert "|AAAA|" in result
        assert '(checksum "abc123")' in result

    def test_multi_chunk_pipe_line_break_at_76(self) -> None:
        """Writer splits base64 data at 76-char boundaries."""
        data = "A" * 76 + "B" * 20
        ef = EmbeddedFile(name="test.step", file_type="model", data=data, checksum="hash")
        result = dump_embedded_files([ef])
        pipe_start = result.index("|")
        pipe_end = result.index("|", pipe_start + 1)
        pipe_content = result[pipe_start + 1 : pipe_end]
        # Should be 76 A's, newline, 20 B's
        assert pipe_content == "A" * 76 + "\n" + "B" * 20

    def test_prettify_indents_continuation_lines(self) -> None:
        """After Prettify, continuation lines are indented with tabs."""
        data = "A" * 76 + "B" * 20
        ef = EmbeddedFile(name="test.step", file_type="model", data=data, checksum="hash")
        flat = dump_embedded_files([ef])
        pretty = prettify(flat)
        lines = pretty.splitlines()
        # Find the line with (data |...
        data_line_idx = next(i for i, ln in enumerate(lines) if "(data |" in ln)
        # The next line should be the continuation chunk indented with tabs
        continuation = lines[data_line_idx + 1]
        assert continuation.startswith("\t")
        assert "B" * 20 in continuation

    def test_type_not_quoted(self) -> None:
        """file_type is emitted unquoted (matches KiCad: (type model) not (type "model"))."""
        ef = EmbeddedFile(name="foo.step", file_type="model", data="AAAA", checksum="abc")
        result = dump_embedded_files([ef])
        assert "(type model)" in result
        assert '(type "model")' not in result


class TestStringHelpers:
    def test_esc_backslash_and_quote(self) -> None:
        assert esc('a\\b"c') == 'a\\\\b\\"c'

    def test_esc_newline_and_cr(self) -> None:
        assert esc("a\nb\rc") == "a\\nb\\rc"

    def test_q_wraps_and_escapes(self) -> None:
        assert q('he said "hi"') == '"he said \\"hi\\""'

    def test_dump_bool(self) -> None:
        assert dump_bool(True) == "yes"
        assert dump_bool(False) == "no"


class TestDumpColorStrokeFill:
    def test_dump_color(self) -> None:
        assert dump_color(Color(r=1, g=2, b=3, a=0.5)) == "(color 1 2 3 0.5)"

    def test_dump_stroke_no_color(self) -> None:
        stroke = Stroke(width=0.1, type=StrokeType.default)
        assert dump_stroke(stroke) == "(stroke (width 0.1) (type default))"

    def test_dump_stroke_with_color(self) -> None:
        stroke = Stroke(width=0.1, type=StrokeType.solid, color=Color(r=10, g=20, b=30, a=1.0))
        assert dump_stroke(stroke) == "(stroke (width 0.1) (type solid) (color 10 20 30 1))"

    def test_dump_fill_pcb_variants(self) -> None:
        assert dump_fill_pcb(Fill(type=FillType.none)) == "(fill no)"
        assert dump_fill_pcb(Fill(type=FillType.outline)) == "(fill yes)"
        assert dump_fill_pcb(Fill(type=FillType.hatch)) == "(fill hatch)"
        assert dump_fill_pcb(Fill(type=FillType.reverse_hatch)) == "(fill reverse_hatch)"
        assert dump_fill_pcb(Fill(type=FillType.cross_hatch)) == "(fill cross_hatch)"

    def test_dump_fill_sch_type_and_color(self) -> None:
        assert dump_fill_sch(Fill(type=FillType.none)) == "(fill (type none))"
        assert (
            dump_fill_sch(Fill(type=FillType.color, color=Color(r=1, g=2, b=3, a=1.0)))
            == "(fill (type color) (color 1 2 3 1))"
        )


class TestDumpEffectsNoHide:
    """dump_effects mirrors EDA_TEXT::Format: font/justify only, NEVER hide (T1)."""

    def test_visible_true_has_no_hide(self) -> None:
        out = dump_effects(TextAttributes())
        assert "hide" not in out
        assert out.startswith("(effects (font (size 1.27 1.27))")

    def test_visible_false_still_has_no_hide(self) -> None:
        # The effects-internal hide branch was dead/incorrect for both families:
        # KiCad emits visibility at item level, never inside (effects ...).
        out = dump_effects(TextAttributes(visible=False))
        assert "hide" not in out

    def test_justify_emitted_when_non_default(self) -> None:
        out = dump_effects(TextAttributes(justify_h=JustifyH.left))
        assert "(justify left)" in out


class TestFieldEffectsDefault:
    """A field ALWAYS carries an effects block (EDA_TEXT::Format, ADR-068)."""

    def test_absent_attributes_emit_the_kicad_default(self) -> None:
        assert dump_field_effects(None) == "(effects (font (size 1.27 1.27)))"

    def test_the_default_matches_an_explicit_default_object(self) -> None:
        assert dump_field_effects(None) == dump_field_effects(TextAttributes())

    def test_present_attributes_pass_straight_through(self) -> None:
        attrs = TextAttributes(bold=True, justify_h=JustifyH.left)
        assert dump_field_effects(attrs) == dump_effects(attrs)

    def test_the_default_is_never_shared_between_calls(self) -> None:
        # A shared module-level model instance would let one caller mutate the
        # default for every later caller.
        assert dump_field_effects(None) == dump_field_effects(None)


class TestItemLevelHideRoundTrip:
    """Hidden fields serialize (hide yes) at item level, before (effects ...)."""

    _FP_HIDDEN_FIELD = (
        '(footprint "T"\n'
        "\t(version 20260206)\n"
        '\t(generator "pcbnew")\n'
        '\t(generator_version "10.0")\n'
        '\t(layer "F.Cu")\n'
        '\t(property "Reference" "REF**"\n'
        "\t\t(at 0 0 0)\n"
        '\t\t(layer "F.SilkS")\n'
        "\t\t(hide yes)\n"
        "\t\t(effects\n"
        "\t\t\t(font\n"
        "\t\t\t\t(size 1 1)\n"
        "\t\t\t\t(thickness 0.15)\n"
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )

    def test_hidden_field_round_trips_idempotent(self) -> None:
        first = Footprint.from_string(self._FP_HIDDEN_FIELD).to_string()
        second = Footprint.from_string(first).to_string()
        assert first == second
        assert "(hide yes)" in first

    def test_item_level_hide_present_effects_hide_absent(self) -> None:
        fp = Footprint.from_string(self._FP_HIDDEN_FIELD)
        out = fp.to_string()
        # exactly one hide, and it precedes the effects block (item level)
        assert out.count("(hide yes)") == 1
        assert out.index("(hide yes)") < out.index("(effects")


class TestFloatFormatters:
    """S2 precision split: fmt_iu (.10f small branch), fmt_double (.16f), fmt_angle (.10g)."""

    def test_fmt_double_small_value_uses_16f(self) -> None:
        # 0.00005 has few significant digits; .10f and .16f coincide after strip
        assert fmt_double(0.00005) == "0.00005"

    def test_fmt_double_distinguishes_from_iu_in_band(self) -> None:
        # A value with >10 fractional digits in the (0, 0.0001] band diverges:
        # fmt_iu (.10f) rounds to 10 fractional digits; fmt_double (.16f) keeps 16.
        assert fmt_iu(0.0000123456789) == "0.0000123457"
        assert fmt_double(0.0000123456789) == "0.0000123456789"

    def test_fmt_iu_unchanged_on_representative_values(self) -> None:
        assert fmt_iu(0.0) == "0"
        assert fmt_iu(1.0) == "1"
        assert fmt_iu(1.27) == "1.27"
        assert fmt_iu(-0.000091) == "-0.000091"  # .10f small branch (FormatInternalUnits)
        assert fmt_iu(0.000001) == "0.000001"

    def test_fmt_double_outside_band_is_10g(self) -> None:
        assert fmt_double(0.5) == "0.5"
        assert fmt_double(1.0) == "1"

    def test_fmt_angle_pure_10g_no_small_branch(self) -> None:
        # FormatAngle has NO fixed small-value branch; small angles go scientific.
        assert fmt_angle(0.00005) == "5e-05"
        assert fmt_angle(90.0) == "90"


class TestFmtDoubleRoundTripSyntheticPad:
    """A sub-0.0001 roundrect_rratio round-trips through fmt_double (.16f)."""

    _FP_ROUNDRECT = (
        '(footprint "T"\n'
        "\t(version 20260206)\n"
        '\t(generator "pcbnew")\n'
        '\t(generator_version "10.0")\n'
        '\t(layer "F.Cu")\n'
        '\t(pad "1" smd roundrect\n'
        "\t\t(at 0 0)\n"
        "\t\t(size 1 1)\n"
        '\t\t(layers "F.Cu")\n'
        "\t\t(roundrect_rratio 0.0000123456789)\n"
        '\t\t(uuid "00000000-0000-0000-0000-000000000001")\n'
        "\t)\n"
        ")\n"
    )

    def test_roundrect_rratio_emitted_with_16f_precision(self) -> None:
        out = Footprint.from_string(self._FP_ROUNDRECT).to_string()
        # .16f keeps all 13 fractional digits; a .10f/fmt_iu path would round to
        # 0.0000123457 and fail this assertion.
        assert "(roundrect_rratio 0.0000123456789)" in out

    def test_roundrect_rratio_round_trips_idempotent(self) -> None:
        first = Footprint.from_string(self._FP_ROUNDRECT).to_string()
        second = Footprint.from_string(first).to_string()
        assert first == second
