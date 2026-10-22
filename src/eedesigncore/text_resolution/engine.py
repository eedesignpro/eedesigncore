"""Nested variable expansion and expression evaluation with explicit context."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePath

from eedesigncore.text_resolution.expressions import Expression
from eedesigncore.text_resolution.models import (
    ResolvedText,
    TextDiagnostic,
    TextResolutionContext,
)

Lookup = Callable[[str], str | None]
_ESCAPES = {"$": "<<<ESC_DOLLAR:", "@": "<<<ESC_AT:"}


def closing(text: str, start: int) -> int | None:
    depth = 1
    for i in range(start, len(text)):
        depth += (text[i] == "{") - (text[i] == "}")
        if depth == 0:
            return i
    return None


class Resolver:
    def __init__(
        self, context: TextResolutionContext, lookup: Lookup | None = None, item_path: str = ""
    ) -> None:
        self.context, self.lookup, self.item_path = context, lookup, item_path
        self.diagnostics: list[TextDiagnostic] = []
        self.operations = 0

    def tick(self) -> None:
        self.operations += 1
        if self.operations > self.context.max_operations:
            raise ValueError("Text resolution exceeds the operation limit.")

    def issue(self, code: str, token: str, message: str) -> None:
        issue = TextDiagnostic.model_validate({"code": code, "token": token, "message": message})
        if issue not in self.diagnostics:
            self.diagnostics.append(issue)

    def variable(self, token: str) -> str | None:
        ctx = self.context
        if token.startswith(("ERC_WARNING", "ERC_ERROR", "DRC_WARNING", "DRC_ERROR")):
            return ""
        if self.lookup:
            value = self.lookup(token)
            if value is not None:
                return value
        for scope in (*ctx.sheet_scopes, *ctx.scopes):
            if token in scope:
                return scope[token]
        builtins = {
            "FILENAME": PurePath(ctx.filename.replace("\\", "/")).name if ctx.filename else None,
            "FILEPATH": ctx.filename,
            "PROJECTNAME": ctx.project_name,
            "SHEETPATH": ctx.sheet_path,
            "SHEETNAME": ctx.sheet_name,
            "#": ctx.page,
            "##": str(ctx.page_count) if ctx.page_count is not None else None,
            "VARIANT": ctx.variant,
            "VARIANTNAME": ctx.variant,
            "VARIANT_DESC": ctx.variant_description,
        }
        if ctx.now is not None:
            builtins.update(
                CURRENT_DATE=ctx.now.strftime("%Y-%m-%d"),
                CURRENT_TIME_HH_MM_SS=ctx.now.strftime("%Hh%Mm%Ss"),
                CURRENT_TIME_LOCALE=ctx.now.strftime("%X"),
            )
        if token in builtins and builtins[token] is not None:
            return builtins[token]
        if ctx.variable_provider:
            value = ctx.variable_provider(self.item_path, token)
            if value is not None:
                return value
        return ctx.variables.get(token)

    def expand(self, text: str, depth: int, chain: tuple[str, ...] = ()) -> str:
        if depth > self.context.max_depth:
            self.issue("resolution_limit", text, "Text resolution reached the recursion limit.")
            return text
        pieces: list[str] = []
        size = 0
        pos = 0
        while pos < len(text):
            self.tick()
            piece = text[pos]
            marker = next((v for v in _ESCAPES.values() if text.startswith(v, pos)), None)
            if marker:
                end = closing(text, pos + len(marker))
                end = len(text) - 1 if end is None else end
                piece, pos = text[pos : end + 1], end + 1
            elif text[pos] == "\\" and text[pos + 1 : pos + 3] in ("${", "@{"):
                end = closing(text, pos + 3)
                if end is None:
                    piece, pos = text[pos:], len(text)
                else:
                    piece = _ESCAPES[text[pos + 1]] + text[pos + 3 : end + 1]
                    pos = end + 1
            elif text[pos : pos + 2] in ("${", "@{"):
                end = closing(text, pos + 2)
                if end is None:
                    end = len(text)
                raw = text[pos : end + 1]
                body = self.expand(text[pos + 2 : end], depth + 1, chain)
                if not body:
                    piece = ""
                elif text[pos] == "$":
                    if body in chain:
                        self.issue(
                            "resolution_limit",
                            raw,
                            "The text variable contains a circular reference.",
                        )
                        piece = raw
                    else:
                        value = self.variable(body)
                        if value is None:
                            self.issue(
                                "unresolved_variable",
                                raw,
                                "No supplied context resolves this text variable.",
                            )
                            piece = "${" + body + "}"
                        else:
                            if value.startswith(
                                ("<Unresolved:", "<Unknown reference:", "<Circular reference>")
                            ):
                                self.issue("unresolved_variable", raw, value)
                            piece = self.expand(value, depth + 1, (*chain, body))
                else:
                    try:
                        piece = Expression(body, self.context, self.tick).evaluate()
                    except (ValueError, ArithmeticError, RecursionError) as error:
                        self.issue("expression_error", raw, str(error))
                        piece = "@{" + body + "}"
                pos = end + 1
            else:
                pos += 1
            size += len(piece)
            if size > self.context.max_length:
                raise ValueError("Text resolution exceeds the output length limit.")
            pieces.append(piece)
        return "".join(pieces)

    def resolve(self, raw: str, *, restore_escapes: bool = True) -> ResolvedText:
        self.diagnostics = []
        self.operations = 0
        if len(raw) > self.context.max_length:
            raise ValueError("Text input exceeds the supported length.")
        try:
            text = self.expand(raw, 0)
        except (ValueError, RecursionError) as error:
            self.issue("resolution_limit", raw, str(error))
            text = raw
        if restore_escapes:
            text = restore_literals(text)
        return ResolvedText(raw=raw, resolved=text, diagnostics=self.diagnostics.copy())


def restore_literals(text: str) -> str:
    for char, marker in _ESCAPES.items():
        text = text.replace(marker, char + "{")
    return text


def resolve_text(
    raw: str, context: TextResolutionContext | None = None, *, item_path: str = ""
) -> ResolvedText:
    """Return raw and shown text without changing a file or reading external state."""
    return Resolver(context or TextResolutionContext(), item_path=item_path).resolve(raw)
