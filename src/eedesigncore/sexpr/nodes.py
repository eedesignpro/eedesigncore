"""S-expression node types for KiCad file parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SExprNodeType(Enum):
    """Node types matching KiCad's SEXPR_TYPE enum."""

    LIST = "LIST"
    SYMBOL = "SYMBOL"
    STRING = "STRING"
    INTEGER = "INTEGER"
    DOUBLE = "DOUBLE"


@dataclass
class SExprNode:
    """A single node in a KiCad S-expression tree.

    For LIST nodes, ``value`` is a list of child ``SExprNode`` objects.
    For SYMBOL/STRING nodes, ``value`` is a ``str``.
    For INTEGER nodes, ``value`` is an ``int``.
    For DOUBLE nodes, ``value`` is a ``float`` and ``raw`` stores the original
    text representation for lossless round-trip serialisation.
    """

    node_type: SExprNodeType
    value: str | int | float | list[SExprNode]
    raw: str | None = field(default=None)

    # Convenience constructors -----------------------------------------------

    @classmethod
    def symbol(cls, name: str) -> SExprNode:
        """Create a SYMBOL node."""
        return cls(node_type=SExprNodeType.SYMBOL, value=name)

    @classmethod
    def string(cls, text: str) -> SExprNode:
        """Create a STRING node."""
        return cls(node_type=SExprNodeType.STRING, value=text)

    @classmethod
    def integer(cls, n: int) -> SExprNode:
        """Create an INTEGER node."""
        return cls(node_type=SExprNodeType.INTEGER, value=n)

    @classmethod
    def double(cls, f: float, raw: str | None = None) -> SExprNode:
        """Create a DOUBLE node, optionally preserving the original text."""
        return cls(node_type=SExprNodeType.DOUBLE, value=f, raw=raw)

    @classmethod
    def make_list(cls, children: list[SExprNode]) -> SExprNode:
        """Create a LIST node from a list of children."""
        return cls(node_type=SExprNodeType.LIST, value=children)


# Type alias for convenience
SExprList = list[SExprNode]
