"""Unit tests for the S-expression parser."""

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNodeType
from eedesigncore.sexpr.parser import parse


class TestNodeTypes:
    def test_symbol_node(self) -> None:
        node = parse("(foo)")
        assert node.node_type == SExprNodeType.LIST
        children = node.value
        assert isinstance(children, list)
        assert len(children) == 1
        sym = children[0]
        assert sym.node_type == SExprNodeType.SYMBOL
        assert sym.value == "foo"

    def test_string_node(self) -> None:
        node = parse('(x "hello world")')
        assert isinstance(node.value, list)
        assert node.value[1].node_type == SExprNodeType.STRING
        assert node.value[1].value == "hello world"

    def test_integer_node(self) -> None:
        node = parse("(x 42)")
        assert isinstance(node.value, list)
        assert node.value[1].node_type == SExprNodeType.INTEGER
        assert node.value[1].value == 42

    def test_negative_integer(self) -> None:
        node = parse("(x -7)")
        assert isinstance(node.value, list)
        assert node.value[1].node_type == SExprNodeType.INTEGER
        assert node.value[1].value == -7

    def test_double_node(self) -> None:
        node = parse("(x 1.27)")
        assert isinstance(node.value, list)
        n = node.value[1]
        assert n.node_type == SExprNodeType.DOUBLE
        assert n.value == pytest.approx(1.27)

    def test_double_negative(self) -> None:
        node = parse("(x -0.5)")
        assert isinstance(node.value, list)
        n = node.value[1]
        assert n.node_type == SExprNodeType.DOUBLE
        assert n.value == pytest.approx(-0.5)

    def test_list_node(self) -> None:
        node = parse("(outer (inner a b))")
        assert node.node_type == SExprNodeType.LIST
        assert isinstance(node.value, list)
        inner = node.value[1]
        assert inner.node_type == SExprNodeType.LIST


class TestNestedList:
    def test_nested_round_trip(self) -> None:
        node = parse("(a (b (c d)) e)")
        assert isinstance(node.value, list)
        assert node.value[0].value == "a"
        assert node.value[1].node_type == SExprNodeType.LIST
        inner_b = node.value[1]
        assert isinstance(inner_b.value, list)
        inner_c = inner_b.value[1]
        assert inner_c.node_type == SExprNodeType.LIST
        assert isinstance(inner_c.value, list)
        assert inner_c.value[0].value == "c"
        assert inner_c.value[1].value == "d"

    def test_multiple_children(self) -> None:
        node = parse("(root (a 1) (b 2) (c 3))")
        assert isinstance(node.value, list)
        assert len(node.value) == 4  # root + 3 children


class TestCommentStripping:
    def test_comment_stripped(self) -> None:
        text = "# This is a comment\n(symbol foo)"
        node = parse(text)
        assert node.node_type == SExprNodeType.LIST
        assert isinstance(node.value, list)
        assert node.value[0].value == "symbol"

    def test_inline_hash_not_stripped(self) -> None:
        # A '#' inside a token (not at line start) should not be treated as comment
        node = parse("(foo bar#baz)")
        assert isinstance(node.value, list)
        assert node.value[1].value == "bar#baz"

    def test_multiple_comments(self) -> None:
        text = "# comment 1\n# comment 2\n(root x)"
        node = parse(text)
        assert node.node_type == SExprNodeType.LIST


class TestEscapedQuotes:
    def test_escaped_quote_in_string(self) -> None:
        node = parse('(x "say \\"hello\\"")')
        assert isinstance(node.value, list)
        assert node.value[1].value == 'say "hello"'

    def test_backslash_escape(self) -> None:
        node = parse('(x "a\\\\b")')
        assert isinstance(node.value, list)
        assert node.value[1].value == "a\\b"


class TestFloatRaw:
    def test_raw_preserved(self) -> None:
        node = parse("(x 1.27)")
        assert isinstance(node.value, list)
        n = node.value[1]
        assert n.node_type == SExprNodeType.DOUBLE
        assert n.raw == "1.27"

    def test_raw_preserved_negative(self) -> None:
        node = parse("(x -3.81)")
        assert isinstance(node.value, list)
        n = node.value[1]
        assert n.raw == "-3.81"

    def test_raw_zero_decimal(self) -> None:
        node = parse("(x 0.0254)")
        assert isinstance(node.value, list)
        n = node.value[1]
        assert n.raw == "0.0254"


class TestParseErrors:
    def test_unclosed_paren_raises(self) -> None:
        with pytest.raises(KicadParseError, match="(?i)unclosed|paren|expected"):
            parse("(foo bar")

    def test_unexpected_close_paren_raises(self) -> None:
        with pytest.raises(KicadParseError, match="(?i)unexpected"):
            parse("(foo) )")

    def test_empty_input_raises(self) -> None:
        with pytest.raises(KicadParseError, match="(?i)empty"):
            parse("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(KicadParseError, match="(?i)empty"):
            parse("   \n\t  ")

    def test_comment_only_raises(self) -> None:
        with pytest.raises(KicadParseError, match="(?i)empty"):
            parse("# just a comment\n")


class TestDepthLimit:
    def test_max_depth_raises(self) -> None:
        # 101 levels of nesting should raise
        deep = "(" * 101 + "x" + ")" * 101
        with pytest.raises(KicadParseError, match="depth"):
            parse(deep)

    def test_depth_at_limit_ok(self) -> None:
        # exactly 100 levels should parse fine
        deep = "(" * 100 + "x" + ")" * 100
        result = parse(deep)  # should not raise
        assert result is not None


class TestCREscapeSequence:
    def test_cr_escape_parses_to_cr(self) -> None:
        node = parse('(x "foo\\rbar")')
        assert isinstance(node.value, list)
        # value should contain actual CR character (0x0D), not the letter r
        assert node.value[1].value == "foo\rbar"

    def test_crlf_sequence_parses_correctly(self) -> None:
        node = parse('(x "T/R\\r\\nCon")')
        assert isinstance(node.value, list)
        assert node.value[1].value == "T/R\r\nCon"

    def test_cr_roundtrip(self) -> None:
        from eedesigncore.sexpr.writer import emit_flat

        node = parse('(x "foo\\rbar")')
        result = emit_flat(node)
        assert result == '(x "foo\\rbar")'


class TestPipeToken:
    def test_simple_pipe_token(self) -> None:
        """Single-line pipe block produces STRING node with bare base64."""
        node = parse("(data |SGVsbG8gV29ybGQ=|)")
        assert isinstance(node.value, list)
        data_node = node.value[1]
        assert data_node.node_type == SExprNodeType.STRING
        assert data_node.value == "SGVsbG8gV29ybGQ="

    def test_multiline_pipe_token_strips_whitespace(self) -> None:
        """Multi-line pipe block has all whitespace stripped from the content."""
        raw = "(data |AAAA\n\tBBBB\n\tCCCC|)"
        node = parse(raw)
        assert isinstance(node.value, list)
        data_node = node.value[1]
        assert data_node.node_type == SExprNodeType.STRING
        assert data_node.value == "AAAABBBBCCCC"

    def test_pipe_token_coexists_with_quoted_strings(self) -> None:
        """Pipe tokens and quoted strings in the same expression parse correctly."""
        node = parse('(embedded_files (file (name "test.step") (data |SGVs|)))')
        assert isinstance(node.value, list)
        ef_node = node.value[1]
        assert isinstance(ef_node.value, list)
        # name child
        name_node = ef_node.value[1]
        assert isinstance(name_node.value, list)
        assert name_node.value[1].value == "test.step"
        # data child
        data_node = ef_node.value[2]
        assert isinstance(data_node.value, list)
        assert data_node.value[1].node_type == SExprNodeType.STRING
        assert data_node.value[1].value == "SGVs"
