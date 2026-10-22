"""Loader for ``fp-lib-table`` / ``sym-lib-table`` S-expression trees.

Strict parser: any unknown top-level token or unknown row member raises
``KicadParseError``. This matches both ADR-017 (hard-error on unknown tokens)
and KiCad's own strict PEG grammar, whose trailing ``must< RPAREN >`` turns any
stray token into a hard syntax error (T3 audit §5).
"""

from __future__ import annotations

from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType
from eedesigncore.tables.models import LibTable, LibTableKind, LibTableRow

_ROOT_KINDS: dict[str, LibTableKind] = {
    "fp_lib_table": "fp",
    "sym_lib_table": "sym",
}

_ROW_KEYS = frozenset({"name", "type", "uri", "options", "descr"})


def _token(node: SExprNode) -> str | None:
    """Return the head symbol of a LIST node, or ``None`` if not a headed list."""
    if node.node_type is not SExprNodeType.LIST:
        return None
    children = node.value
    if not isinstance(children, list) or not children:
        return None
    head = children[0]
    if head.node_type is not SExprNodeType.SYMBOL:
        return None
    assert isinstance(head.value, str)
    return head.value


def _atom_str(node: SExprNode) -> str:
    """Return the string value of an atom node (STRING/SYMBOL/INTEGER/DOUBLE)."""
    if node.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL):
        assert isinstance(node.value, str)
        return node.value
    if node.node_type is SExprNodeType.INTEGER:
        return str(node.value)
    if node.node_type is SExprNodeType.DOUBLE:
        return node.raw if node.raw is not None else str(node.value)
    raise KicadParseError(f"Expected an atom value, got {node.node_type}")


def _single_value(node: SExprNode, key: str) -> str:
    """Extract the single value from a ``(key value)`` list."""
    children = node.value
    assert isinstance(children, list)
    if len(children) != 2:
        raise KicadParseError(f"Expected '({key} value)' with a single value")
    return _atom_str(children[1])


def _load_row(node: SExprNode) -> LibTableRow:
    """Parse one ``(lib ...)`` row; unknown members are a hard error."""
    children = node.value
    assert isinstance(children, list)

    fields: dict[str, str] = {}
    disabled = False
    hidden = False

    for member in children[1:]:
        tok = _token(member)
        if tok in _ROW_KEYS:
            assert tok is not None
            fields[tok] = _single_value(member, tok)
        elif tok == "disabled":
            disabled = True
        elif tok == "hidden":
            hidden = True
        else:
            raise KicadParseError(f"Unknown token in (lib ...) row: {tok!r}")

    missing = {"name", "type", "uri"} - fields.keys()
    if missing:
        raise KicadParseError(
            f"(lib ...) row missing required key(s): {', '.join(sorted(missing))}"
        )

    return LibTableRow(
        name=fields["name"],
        type=fields["type"],
        uri=fields["uri"],
        options=fields.get("options", ""),
        descr=fields.get("descr", ""),
        disabled=disabled,
        hidden=hidden,
    )


def load_lib_table(tree: SExprNode) -> LibTable:
    """Build a :class:`LibTable` from a parsed S-expression tree.

    Accepts both ``fp_lib_table`` and ``sym_lib_table`` roots. ``(version ...)``
    is optional. Raises :class:`KicadParseError` on an unknown root token or any
    unknown top-level / row-level token.
    """
    root_token = _token(tree)
    if root_token not in _ROOT_KINDS:
        raise KicadParseError(
            f"Not a library table: root token {root_token!r} "
            "(expected 'fp_lib_table' or 'sym_lib_table')"
        )
    kind = _ROOT_KINDS[root_token]

    children = tree.value
    assert isinstance(children, list)

    version: int | None = None
    rows: list[LibTableRow] = []

    for child in children[1:]:
        tok = _token(child)
        if tok == "version":
            raw = _single_value(child, "version")
            try:
                version = int(raw)
            except ValueError:
                version = None
        elif tok == "lib":
            rows.append(_load_row(child))
        else:
            raise KicadParseError(f"Unknown top-level token in {root_token}: {tok!r}")

    return LibTable(kind=kind, version=version, rows=rows)
