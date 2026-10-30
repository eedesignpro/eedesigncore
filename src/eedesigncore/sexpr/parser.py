"""KiCad S-expression parser.

Implements a recursive-descent parser that produces an ``SExprNode`` tree
following the same token-classification rules as KiCad's ``sexpr_parser.cpp``.
"""

from __future__ import annotations

import re

from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNode

# Regex for stripping full-line comments (# at the start of non-whitespace).
_COMMENT_RE = re.compile(r"^[ \t]*#[^\n]*\n?", re.MULTILINE)

# Tokeniser: yields (, ), quoted strings, pipe-delimited blocks, and bare atoms.
_TOKEN_RE = re.compile(
    r"""
    \(                          # open paren
    |
    \)                          # close paren
    |
    "(?:[^"\\]|\\.)*"           # double-quoted string (handles \" and \\)
    |
    \|[^|]*\|                   # pipe-delimited bar token (embedded file data)
    |
    [^\s()"]+                   # bare atom
    """,
    re.VERBOSE | re.DOTALL,
)


def _strip_comments(text: str) -> str:
    """Remove # comment lines from *text* before tokenising."""
    return _COMMENT_RE.sub("", text)


def _classify_atom(token: str) -> SExprNode:
    """Classify a bare atom token into INTEGER, DOUBLE, or SYMBOL."""
    # KiCad rule: only digits, minus sign, and dots → numeric
    # If it contains a dot → DOUBLE; else → INTEGER; else → SYMBOL
    if re.fullmatch(r"-?[0-9]+", token):
        # Preserve -0 as DOUBLE with raw="-0" to round-trip KiCad's -0.0 output.
        if token == "-0":
            return SExprNode.double(-0.0, raw="-0")
        node = SExprNode.integer(int(token))
        if token != str(node.value):
            # "0805" is a number to the lexer and text to a text field: keep
            # the spelling for a reader that takes the atom as text.
            node.raw = token
        return node
    if re.fullmatch(r"-?[0-9]*\.[0-9]*(?:[eE][+-]?[0-9]+)?", token):
        # Store raw text for lossless round-trip
        return SExprNode.double(float(token), raw=token)
    return SExprNode.symbol(token)


def _unescape_string(raw: str) -> str:
    """Remove surrounding quotes and process backslash escapes."""
    # raw includes surrounding double quotes
    inner = raw[1:-1]
    result: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            nch = inner[i + 1]
            if nch == '"':
                result.append('"')
                i += 2
                continue
            elif nch == "\\":
                result.append("\\")
                i += 2
                continue
            elif nch == "n":
                result.append("\n")
                i += 2
                continue
            elif nch == "r":
                result.append("\r")
                i += 2
                continue
            elif nch == "t":
                result.append("\t")
                i += 2
                continue
            else:
                result.append(nch)
                i += 2
                continue
        result.append(ch)
        i += 1
    return "".join(result)


_MAX_DEPTH = 100


def parse(text: str) -> SExprNode:
    """Parse a KiCad S-expression string and return the root ``SExprNode``.

    The root of any valid KiCad file is a single top-level list.

    Raises:
        KicadParseError: on empty input, unclosed parenthesis, or unexpected ``)``,
            or when nesting depth exceeds ``_MAX_DEPTH``.
    """
    cleaned = _strip_comments(text).strip()
    if not cleaned:
        raise KicadParseError("Empty input: no S-expression found")

    tokens = _TOKEN_RE.findall(cleaned)
    if not tokens:
        raise KicadParseError("Empty input: no tokens found")

    pos = 0

    def _parse_one(depth: int = 0) -> SExprNode:
        nonlocal pos

        if depth > _MAX_DEPTH:
            raise KicadParseError(f"S-expression nesting depth exceeds maximum ({_MAX_DEPTH})")

        if pos >= len(tokens):
            raise KicadParseError("Unexpected end of input: unclosed parenthesis")

        tok = tokens[pos]
        pos += 1

        if tok == "(":
            children: list[SExprNode] = []
            while True:
                if pos >= len(tokens):
                    raise KicadParseError("Unclosed parenthesis: expected ')'")
                if tokens[pos] == ")":
                    pos += 1
                    break
                children.append(_parse_one(depth + 1))
            return SExprNode.make_list(children)
        elif tok == ")":
            raise KicadParseError("Unexpected ')' without matching '('")
        elif tok.startswith('"'):
            return SExprNode.string(_unescape_string(tok))
        elif tok.startswith("|"):
            # Pipe-delimited bar token: strip surrounding | and all internal whitespace
            inner = tok[1:-1]
            return SExprNode.string(re.sub(r"\s+", "", inner))
        else:
            return _classify_atom(tok)

    root = _parse_one()

    # Ensure we consumed all tokens (no trailing garbage)
    # Trailing ) after root is an error
    if pos < len(tokens):
        remaining = tokens[pos]
        if remaining == ")":
            raise KicadParseError("Unexpected ')' without matching '('")

    return root
