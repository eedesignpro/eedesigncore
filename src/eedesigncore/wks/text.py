"""KiCad legacy drawing-sheet text conversions, applied without changing source text."""

import re


def legacy_text(text: str, version: int = 20231118) -> str:
    variables = dict(
        D="ISSUE_DATE",
        R="REVISION",
        K="KICAD_VERSION",
        Z="PAPER",
        S="#",
        N="##",
        F="FILENAME",
        L="LAYER",
        P="SHEETPATH",
        Y="COMPANY",
        T="TITLE",
    )

    def replace_token(match: re.Match[str]) -> str:
        key = match[1]
        if key == "%":
            return "%"
        if key.startswith("C"):
            return (
                "${COMMENT" + str(int(key[1]) + 1) + "}"
                if len(key) == 2 and key[1] in "012345678"
                else ""
            )
        return "${" + variables[key] + "}" if key in variables else ""

    text = re.sub(r"%(C.?|.|$)", replace_token, text)
    return _overbars(text) if version < 20210606 else text


def _overbars(text: str) -> str:
    if text == "~":
        return text
    result: list[str] = []
    active = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "~":
            following = text[index + 1 : index + 3]
            if following.startswith("~"):
                if following == "~{":
                    result.append("~~{}")
                else:
                    result.append("~")
                    index += 1
            elif following.startswith("{"):
                return text
            else:
                result.append("}" if active else "~{")
                active = not active
        else:
            if char in " })" and active:
                result.append("}")
                active = False
            result.append(char)
        index += 1
    if active:
        result.append("}")
    return "".join(result)
