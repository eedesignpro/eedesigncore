"""Unit tests for the Prettify writer and _escape_string helper."""

from eedesigncore.sexpr.writer import _escape_string, prettify


class TestTabIndentation:
    def test_depth_0_no_indent(self) -> None:
        result = prettify("(root)")
        assert result.startswith("(root)")

    def test_depth_1_one_tab(self) -> None:
        result = prettify("(root (child x))")
        lines = result.splitlines()
        # second line should start with one tab
        assert lines[1].startswith("\t(child")

    def test_depth_2_two_tabs(self) -> None:
        result = prettify("(root (a (b x)))")
        lines = result.splitlines()
        # depth 2 line should start with two tabs
        depth2 = [ln for ln in lines if ln.startswith("\t\t")]
        assert depth2, f"No depth-2 line found in:\n{result}"

    def test_depth_3_three_tabs(self) -> None:
        result = prettify("(root (a (b (c x))))")
        lines = result.splitlines()
        depth3 = [ln for ln in lines if ln.startswith("\t\t\t")]
        assert depth3, f"No depth-3 line found in:\n{result}"


class TestMultiLineList:
    def test_list_with_sublists_is_multiline(self) -> None:
        result = prettify("(outer (inner1 x) (inner2 y))")
        lines = result.splitlines()
        assert len(lines) > 1

    def test_closing_paren_on_own_line(self) -> None:
        result = prettify("(outer (inner x))")
        lines = result.splitlines()
        # Closing ) of outer should be on its own line at depth 0
        assert lines[-1].rstrip() == ")"


class TestLeafList:
    def test_leaf_list_on_single_line(self) -> None:
        """A list with no sub-lists stays on one line."""
        result = prettify("(outer (leaf a b c))")
        # (leaf a b c) should appear on a single line
        assert any("(leaf a b c)" in line for line in result.splitlines())

    def test_deeply_nested_leaf_on_single_line(self) -> None:
        result = prettify("(root (parent (leaf x y z)))")
        assert any("(leaf x y z)" in line for line in result.splitlines())


class TestCompactShortFormTokens:
    """In COMPACT mode, font/stroke/fill stay on one line even with sub-lists."""

    def test_font_compact_single_line(self) -> None:
        result = prettify("(effects (font (size 1.27 1.27)))", mode="COMPACT")
        # (font (size 1.27 1.27)) must be on one line
        assert any("(font (size 1.27 1.27))" in line for line in result.splitlines())

    def test_stroke_compact_single_line(self) -> None:
        result = prettify("(stroke (width 0) (type default))", mode="COMPACT")
        assert any("(stroke (width 0) (type default))" in line for line in result.splitlines())

    def test_fill_compact_single_line(self) -> None:
        result = prettify("(fill (type none))", mode="COMPACT")
        assert any("(fill (type none))" in line for line in result.splitlines())

    def test_stroke_normal_multiline(self) -> None:
        """In NORMAL mode, stroke expands to multiple lines."""
        result = prettify("(outer (stroke (width 0) (type default)))", mode="NORMAL")
        # stroke should be multi-line in NORMAL mode
        # Each sub-list of stroke should get its own line
        assert "(width 0)" in result
        assert "(type default)" in result
        lines = result.splitlines()
        # width and type should be on separate lines
        width_lines = [ln for ln in lines if "(width 0)" in ln]
        type_lines = [ln for ln in lines if "(type default)" in ln]
        assert width_lines and type_lines


class TestEscapeString:
    """Tests for _escape_string — matches KiCad richio.cpp::Quotes()."""

    def test_backslash_escaped(self) -> None:
        assert _escape_string("a\\b") == "a\\\\b"

    def test_double_quote_escaped(self) -> None:
        assert _escape_string('a"b') == 'a\\"b'

    def test_newline_escaped(self) -> None:
        assert _escape_string("foo\nbar") == "foo\\nbar"

    def test_carriage_return_escaped(self) -> None:
        assert _escape_string("foo\rbar") == "foo\\rbar"

    def test_tab_not_escaped(self) -> None:
        # KiCad richio.cpp does NOT escape tabs — pass through literally
        assert _escape_string("foo\tbar") == "foo\tbar"

    def test_combined_escapes(self) -> None:
        assert _escape_string('a\nb"c\\d') == 'a\\nb\\"c\\\\d'


class TestFileEnding:
    def test_ends_with_exactly_one_newline(self) -> None:
        result = prettify("(root (a 1))")
        assert result.endswith("\n")
        assert not result.endswith("\n\n")

    def test_no_trailing_whitespace_on_last_line(self) -> None:
        # A list with sublists has the closing ) on its own line at depth 0
        result = prettify("(outer (inner x))")
        lines = result.splitlines()
        assert lines[-1] == ")"
