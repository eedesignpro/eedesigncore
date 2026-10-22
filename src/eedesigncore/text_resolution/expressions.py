"""Bounded KiCad expression grammar. Never execute expression text as Python."""

from __future__ import annotations

import math
import operator
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

from eedesigncore.text_resolution.models import ExpressionValue, TextResolutionContext

_PREFIX = {
    "a": 1e-18,
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
    "P": 1e15,
    "E": 1e18,
}
_UNITS = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    '"': 25.4,
    "mil": 0.0254,
    "thou": 0.0254,
    "um": 0.001,
    "deg": 1.0,
    "°": 1.0,
    "ps": 1.0,
    "fs": 1.0,
}
_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_PRECEDENCE = {
    "<": 1,
    ">": 1,
    "<=": 1,
    ">=": 1,
    "==": 1,
    "!=": 1,
    "+": 2,
    "-": 2,
    "*": 3,
    "/": 3,
    "%": 3,
    "^": 5,
}
_E24 = (
    100,
    110,
    120,
    130,
    150,
    160,
    180,
    200,
    220,
    240,
    270,
    300,
    330,
    360,
    390,
    430,
    470,
    510,
    560,
    620,
    680,
    750,
    820,
    910,
)
_E192 = tuple(
    int(value)
    for value in [
        "100",
        "101",
        "102",
        "104",
        "105",
        "106",
        "107",
        "109",
        "110",
        "111",
        "113",
        "114",
        "115",
        "117",
        "118",
        "120",
        "121",
        "123",
        "124",
        "126",
        "127",
        "129",
        "130",
        "132",
        "133",
        "135",
        "137",
        "138",
        "140",
        "142",
        "143",
        "145",
        "147",
        "149",
        "150",
        "152",
        "154",
        "156",
        "158",
        "160",
        "162",
        "164",
        "165",
        "167",
        "169",
        "172",
        "174",
        "176",
        "178",
        "180",
        "182",
        "184",
        "187",
        "189",
        "191",
        "193",
        "196",
        "198",
        "200",
        "203",
        "205",
        "208",
        "210",
        "213",
        "215",
        "218",
        "221",
        "223",
        "226",
        "229",
        "232",
        "234",
        "237",
        "240",
        "243",
        "246",
        "249",
        "252",
        "255",
        "258",
        "261",
        "264",
        "267",
        "271",
        "274",
        "277",
        "280",
        "284",
        "287",
        "291",
        "294",
        "298",
        "301",
        "305",
        "309",
        "312",
        "316",
        "320",
        "324",
        "328",
        "332",
        "336",
        "340",
        "344",
        "348",
        "352",
        "357",
        "361",
        "365",
        "370",
        "374",
        "379",
        "383",
        "388",
        "392",
        "397",
        "402",
        "407",
        "412",
        "417",
        "422",
        "427",
        "432",
        "437",
        "442",
        "448",
        "453",
        "459",
        "464",
        "470",
        "475",
        "481",
        "487",
        "493",
        "499",
        "505",
        "511",
        "517",
        "523",
        "530",
        "536",
        "542",
        "549",
        "556",
        "562",
        "569",
        "576",
        "583",
        "590",
        "597",
        "604",
        "612",
        "619",
        "626",
        "634",
        "642",
        "649",
        "657",
        "665",
        "673",
        "681",
        "690",
        "698",
        "706",
        "715",
        "723",
        "732",
        "741",
        "750",
        "759",
        "768",
        "777",
        "787",
        "796",
        "806",
        "816",
        "825",
        "835",
        "845",
        "856",
        "866",
        "876",
        "887",
        "898",
        "909",
        "920",
        "931",
        "942",
        "953",
        "965",
        "976",
        "988",
    ]
)


def shown(value: ExpressionValue) -> str:
    if isinstance(value, str):
        return value
    if not math.isfinite(value):
        raise ValueError("The expression result is not finite.")
    rounded = round(value)
    if abs(value - rounded) < 1e-10 and abs(rounded) < 1e15:
        return str(rounded)
    return repr(float(value))


def number(value: ExpressionValue) -> float:
    if isinstance(value, str) and not re.fullmatch(
        r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value
    ):
        raise ValueError(f"Cannot convert {value!r} to a number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("The expression number is not finite.")
    return result


def _date(days: int, kind: str) -> str:
    value = date(1970, 1, 1) + timedelta(days=days)
    if kind in ("US", "us"):
        return value.strftime("%m/%d/%Y")
    if kind in ("EU", "european"):
        return value.strftime("%d/%m/%Y")
    if kind in ("long", "short"):
        months = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        month = months[value.month - 1]
        return f"{month if kind == 'long' else month[:3]} {value.day}, {value.year}"
    if kind in (
        "Chinese",
        "chinese",
        "CN",
        "cn",
        "中文",
        "Japanese",
        "japanese",
        "JP",
        "jp",
        "日本語",
    ):
        return f"{value.year}年{value.month:02}月{value.day:02}日"
    if kind in ("Korean", "korean", "KR", "kr", "한국어"):
        return f"{value.year}년 {value.month:02}월 {value.day:02}일"
    return value.isoformat()


def _days(text: str) -> float:
    if re.fullmatch(r"\d{8}", text):
        return float((date(int(text[:4]), int(text[4:6]), int(text[6:])) - date(1970, 1, 1)).days)
    if not re.fullmatch(r"[0-9\s./年月日년월일-]+", text):
        raise ValueError("The expression date has invalid characters.")
    parts = re.findall(r"\d+", text)
    if not 1 <= len(parts) <= 3 or not any(c in text for c in "-/.年月日년월일"):
        raise ValueError("The expression date has an invalid format.")
    parts += ["1"] * (3 - len(parts))
    first, second, third = map(int, parts)
    if len(parts[0]) == 4 or any(c in text for c in "年月日년월일"):
        year, month, day = first, second, third
    elif "/" in text and first <= 12 and second <= 31:
        month, day, year = first, second, third
    elif "/" in text and second <= 12:
        day, month, year = first, second, third
    else:
        year, month, day = first, second, third
    return float((date(year, month, day) - date(1970, 1, 1)).days)


def _preferred(value: float, series: str, mode: str) -> float:
    if (
        series
        not in {f"{prefix}{size}" for prefix in ("E", "e") for size in (3, 6, 12, 24, 48, 96, 192)}
        or value <= 0
    ):
        raise ValueError("The E-series or value is invalid.")
    size = int(series[1:])
    base = _E24 if size <= 24 else _E192
    values = base[:: len(base) // size]
    scale = 10.0 ** math.floor(math.log10(value))
    normalized = value / scale * 100
    if mode == "enearest":
        return min(values, key=lambda v: abs(v - normalized)) / 100 * scale
    if mode == "eup":
        return next((v for v in values if v > normalized), values[0] * 10) / 100 * scale
    return next((v for v in reversed(values) if v < normalized), values[-1] / 10) / 100 * scale


_RUNTIME_ARITY = {
    "vcsidentifier": (0, 1),
    "vcsnearestlabel": (0, 2),
    "vcslabeldistance": (0, 2),
    "vcsdirty": (0, 1),
    "vcsdirtysuffix": (0, 2),
    "vcsauthor": (0, 0),
    "vcsauthoremail": (0, 0),
    "vcscommitter": (0, 0),
    "vcscommitteremail": (0, 0),
    "vcsbranch": (0, 0),
    "vcscommitdate": (0, 1),
    "vcsfileidentifier": (1, 2),
    "vcsfileauthor": (1, 1),
    "vcsfileauthoremail": (1, 1),
    "vcsfilecommitter": (1, 1),
    "vcsfilecommitteremail": (1, 1),
    "vcsfilecommitdate": (1, 2),
}


def function(
    name: str, args: list[ExpressionValue], context: TextResolutionContext
) -> ExpressionValue:
    count = len(args)
    if name in _RUNTIME_ARITY:
        low, high = _RUNTIME_ARITY[name]
        if not low <= count <= high:
            raise ValueError(f"Invalid argument count for {name}.")
        result = (
            context.function_provider(name, tuple(args)) if context.function_provider else None
        )
        if result is None:
            raise ValueError(f"Missing runtime context for {name}.")
        return result
    if name in ("today", "now") and not args:
        if context.now is None:
            raise ValueError(f"Missing clock context for {name}.")
        stamp = int(context.now.timestamp())
        return float(int(stamp / 86400) if name == "today" else stamp)
    if name == "random" and not args:
        if context.random is None:
            raise ValueError("Missing random source.")
        value = context.random()
        if not 0 <= value < 1:
            raise ValueError("The random source must return a value from zero up to one.")
        return value
    if name in ("format", "fixed") and args:
        try:
            decimals = int(number(args[1])) if count > 1 else 2
        except ValueError:
            decimals = 2
        if not 0 <= decimals <= 100:
            raise ValueError("Expression decimal places exceed the supported limit.")
        return f"{number(args[0]):.{decimals}f}"
    if name == "currency" and args:
        return f"{shown(args[1]) if count > 1 else '$'}{number(args[0]):.2f}"
    if name == "dateformat" and args:
        return _date(int(number(args[0])), shown(args[1]) if count > 1 else "ISO")
    if name == "datestring" and count == 1:
        return _days(shown(args[0]))
    if name == "weekdayname" and count == 1:
        return (["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])[
            (int(number(args[0])) + 3) % 7
        ]
    if name == "timeformat" and args:
        if context.now is None or context.now.tzinfo is None:
            raise ValueError("Missing explicit timezone for timeformat.")
        moment = datetime.fromtimestamp(int(number(args[0])), context.now.tzinfo)
        kind = shown(args[1]) if count > 1 else "ISO"
        if kind == "12h":
            return (
                f"{moment.hour % 12 or 12}:{moment.minute:02}:{moment.second:02} "
                f"{'PM' if moment.hour >= 12 else 'AM'}"
            )
        return moment.strftime(
            "%Hh%Mm%Ss"
            if kind in ("HH_MM_SS", "filename")
            else "%H:%M"
            if kind == "short"
            else "%H:%M:%S"
        )
    if name in ("upper", "lower") and count == 1:
        lower, upper = "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return shown(args[0]).translate(
            str.maketrans(lower, upper) if name == "upper" else str.maketrans(upper, lower)
        )
    if name == "concat" and count >= 2:
        return "".join(map(shown, args))
    if name in ("beforefirst", "beforelast", "afterfirst", "afterlast") and count == 2:
        text, delimiter = shown(args[0]), (shown(args[1]) or " ")[0]
        pos = text.find(delimiter) if name.endswith("first") else text.rfind(delimiter)
        if pos < 0:
            return "" if name == "afterfirst" else text
        return text[:pos] if name.startswith("before") else text[pos + 1 :]
    if name == "if" and count == 3:
        return args[1] if number(args[0]) else args[2]
    if name in ("enearest", "eup", "edown") and count in (1, 2):
        return _preferred(number(args[0]), shown(args[1]) if count == 2 else "E24", name)
    nums = list(map(number, args))
    unary: dict[str, Callable[[float], float]] = {
        "abs": abs,
        "sqrt": math.sqrt,
        "floor": math.floor,
        "ceil": math.ceil,
        "db": lambda v: 10 * math.log10(v),
        "dbv": lambda v: 20 * math.log10(v),
        "fromdb": lambda v: 10 ** (v / 10),
        "fromdbv": lambda v: 10 ** (v / 20),
    }
    if name in unary and count == 1:
        return float(unary[name](nums[0]))
    if name == "pow" and count == 2:
        return math.pow(*nums)
    if name == "round" and args:
        scale = 10.0 ** (int(nums[1]) if count > 1 else 0)
        v = nums[0] * scale
        return math.copysign(math.floor(abs(v) + 0.5), v) / scale
    if name in ("sum", "min", "max", "avg") and args:
        aggregates: dict[str, Callable[[list[float]], float]] = {
            "sum": sum,
            "min": min,
            "max": max,
            "avg": lambda values: sum(values) / len(values),
        }
        return aggregates[name](nums)
    if name == "shunt" and count == 2:
        return nums[0] * nums[1] / sum(nums) if sum(nums) > 0 else 0.0
    raise ValueError(f"Unknown function or argument count: {name}.")


class Expression:
    def __init__(
        self, text: str, context: TextResolutionContext, tick: Callable[[], None]
    ) -> None:
        self.text, self.context, self.tick = text, context, tick
        self.pos = 0

    def space(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def take(self, token: str) -> bool:
        self.space()
        if self.text.startswith(token, self.pos):
            self.pos += len(token)
            return True
        return False

    def parse(self, precedence: int = 0, depth: int = 0) -> ExpressionValue:
        self.tick()
        if depth > 64:
            raise ValueError("Expression nesting exceeds the supported limit.")
        self.space()
        if self.take("-"):
            left: ExpressionValue = -number(self.parse(4, depth + 1))
        elif self.take("+"):
            left = number(self.parse(4, depth + 1))
        elif self.take("("):
            left = self.parse(0, depth + 1)
            if not self.take(")"):
                raise ValueError("Missing closing parenthesis.")
        elif self.pos < len(self.text) and self.text[self.pos] in "\"'":
            quote = self.text[self.pos]
            self.pos += 1
            result = []
            while self.pos < len(self.text) and self.text[self.pos] != quote:
                char = self.text[self.pos]
                self.pos += 1
                if char == "\\" and self.pos < len(self.text):
                    char = self.text[self.pos]
                    self.pos += 1
                    char = {"n": "\n", "t": "\t", "r": "\r"}.get(char, char)
                result.append(char)
            if not self.take(quote):
                raise ValueError("Unclosed expression string.")
            left = "".join(result)
        elif match := _NUMBER.match(self.text, self.pos):
            token = match.group()
            self.pos = match.end()
            left = float(token)
            unit = next(
                (
                    u
                    for u in sorted(_UNITS, key=len, reverse=True)
                    if self.text.startswith(u, self.pos)
                    and (
                        self.pos + len(u) == len(self.text)
                        or not self.text[self.pos + len(u)].isalpha()
                    )
                ),
                None,
            )
            if unit is None and self.pos < len(self.text) and self.text[self.pos] in _PREFIX:
                multiplier = _PREFIX[self.text[self.pos]]
                self.pos += 1
                fraction = re.match(r"\d+", self.text[self.pos :])
                if fraction and "." not in token and "e" not in token.lower():
                    left = float(token + fraction.group())
                    self.pos += len(fraction.group())
                left *= multiplier
            old = self.pos
            self.space()
            unit = next(
                (
                    u
                    for u in sorted(_UNITS, key=len, reverse=True)
                    if self.text.startswith(u, self.pos)
                    and (
                        self.pos + len(u) == len(self.text)
                        or not self.text[self.pos + len(u)].isalpha()
                    )
                ),
                None,
            )
            if unit:
                left *= _UNITS[unit]
                self.pos += len(unit)
            else:
                self.pos = old
        elif match := _NAME.match(self.text, self.pos):
            name = match.group()
            self.pos = match.end()
            if not self.take("("):
                raise ValueError("Expression names require a function call.")
            args = []
            if not self.take(")"):
                while True:
                    args.append(self.parse(0, depth + 1))
                    if self.take(")"):
                        break
                    if not self.take(","):
                        raise ValueError("Missing function argument separator.")
            left = function(name, args, self.context)
        else:
            raise ValueError("Invalid expression token.")
        while True:
            self.space()
            operation = next(
                (
                    op
                    for op in ("<=", ">=", "==", "!=", "+", "-", "*", "/", "%", "^", "<", ">")
                    if self.text.startswith(op, self.pos)
                ),
                "",
            )
            priority = _PRECEDENCE.get(operation, -1)
            if priority < precedence:
                break
            self.tick()
            self.pos += len(operation)
            right = self.parse(priority if operation == "^" else priority + 1, depth + 1)
            if operation == "+" and (isinstance(left, str) or isinstance(right, str)):
                left = shown(left) + shown(right)
            elif operation in ("==", "!=") and isinstance(left, str) and isinstance(right, str):
                left = float((left == right) if operation == "==" else (left != right))
            else:
                a, b = number(left), number(right)
                operations: dict[str, Callable[[float, float], float]] = {
                    "+": operator.add,
                    "-": operator.sub,
                    "*": operator.mul,
                    "/": operator.truediv,
                    "%": math.fmod,
                    "^": math.pow,
                    "<": operator.lt,
                    ">": operator.gt,
                    "<=": operator.le,
                    ">=": operator.ge,
                    "==": operator.eq,
                    "!=": operator.ne,
                }
                left = float(operations[operation](a, b))
            if len(shown(left)) > self.context.max_length:
                raise ValueError("Expression output exceeds the supported limit.")
        return left

    def evaluate(self) -> str:
        result = self.parse()
        self.space()
        if self.pos != len(self.text):
            raise ValueError("Unexpected text after the expression.")
        return shown(result)
