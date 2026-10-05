"""A small CSS parser and cascade for the properties that change what a book says.

Publisher styling is ignored except where dropping it would change the text:
emphasis set through classes, hidden content, alignment, preserved white
space, list markers and a few spacing hints. Anything this module does not
understand (a selector, a value, an at-rule) is skipped rather than guessed.
"""

from __future__ import annotations

import posixpath
import re
from functools import lru_cache
from typing import Callable, Iterable

from .blocks import BOLD, ITALIC, STRIKE, SUB, SUPER, UNDERLINE

MAX_SHEET_CHARS = 1 << 20
MAX_RULES = 20000
MAX_SHEETS = 64
MAX_IMPORT_DEPTH = 8
MAX_STYLE_ATTRIBUTE = 4096
_MATCH_BUDGET = 2000
_MEMO_LIMIT = 20000

# Widths are unknown here, so a percentage is read against a 32 em measure.
_PERCENT = 0.32
_UNITS = {
    "px": 1 / 16, "pt": 1 / 12, "pc": 1.0, "in": 6.0, "cm": 2.36, "mm": 0.236, "q": 0.059,
    "rem": 1.0, "%": _PERCENT, "vw": _PERCENT, "vh": _PERCENT, "vmin": _PERCENT, "vmax": _PERCENT,
}
_FONT_RELATIVE = {"em": 1.0, "ex": 0.5, "ch": 0.5}
_SIZE_KEYWORDS = {
    "xx-small": 0.6, "x-small": 0.75, "small": 0.89, "medium": 1.0,
    "large": 1.2, "x-large": 1.5, "xx-large": 2.0, "xxx-large": 3.0,
}
_LIST_TYPES = frozenset((
    "none", "disc", "circle", "square", "decimal", "decimal-leading-zero",
    "lower-alpha", "upper-alpha", "lower-latin", "upper-latin",
    "lower-roman", "upper-roman", "lower-greek",
))
_LIST_ATTRIBUTE = {"1": "decimal", "a": "lower-alpha", "A": "upper-alpha",
                   "i": "lower-roman", "I": "upper-roman"}
_BLOCK_DISPLAYS = frozenset(("list-item", "table", "flex", "grid", "flow-root", "run-in"))
_MEDIA_TYPES = frozenset(("all", "screen", "amzn-kf8"))
# Media queries are judged as on a phone, whatever the panel measures: the text
# is one column with nothing set side by side, which is the layout publishers
# write their narrow-screen rules for (plays set as tables, for one).
_VIEWPORT_WIDTH = 360.0
_VIEWPORT_UNITS = {"px": 1.0, "em": 16.0, "rem": 16.0, "pt": 4 / 3, "pc": 16.0, "in": 96.0,
                   "cm": 37.8, "mm": 3.78}
# What `<font size>` 1 to 7 stand for.
_FONT_SIZES = ("x-small", "small", "medium", "large", "x-large", "xx-large", "xxx-large")

_LEX = re.compile(
    r"/\*.*?(?:\*/|\Z)|\"(?:[^\"\\\n]|\\.)*\"?|'(?:[^'\\\n]|\\.)*'?"
    r"|url\([^\"'()]*\)|<!--|-->|<!\[CDATA\[|\]\]>", re.S | re.I)
_STRUCTURE = re.compile(r"[{};]")
_BRACES = re.compile(r"[{}]")
_IMPORTANT = re.compile(r"!\s*important\s*$", re.I)
_LENGTH = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))([a-z%]*)$")
_STRING = re.compile("\x00(\\d+)\x00")
_MEDIA_AND = re.compile(r"\band\b")
_MEDIA_FEATURE = re.compile(r"\(\s*([a-z-]+)\s*(?::\s*([^()]*?)\s*)?\)")
_BEFORE = re.compile(r"::?before\s*$", re.I)
_COUNTER = re.compile(
    r"counters?\(\s*[\w-]+\s*(?:,\s*\x00\d+\x00\s*)?(?:,\s*([a-z-]+)\s*)?\)", re.I)
_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?(\x00\d+\x00|[^\s;)]+)\s*\)?\s*(.*)$", re.I | re.S)
_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?|\\(.)", re.S)
_IDENT = r"(?:[\w-]|[^\x00-\x7f]|\\[0-9a-fA-F]{1,6}\s?|\\.)+"
_SELECTOR_TOKEN = re.compile(
    r"(?P<comb>\s*[>+~]\s*|\s+)"
    r"|(?P<ns>(?:[\w-]+|\*)?\|)(?=[\w*])"
    r"|(?P<tag>\*|[a-zA-Z_][\w-]*)"
    r"|#(?P<id>%s)|\.(?P<cls>%s)"
    r"|\[\s*(?:(?P<ans>[\w-]*|\*)\|)?(?P<attr>[\w:-]+)\s*"
    r"(?:(?P<op>[~|^$*]?=)\s*(?P<val>\x00\d+\x00|[^\s\]]+)\s*)?\]"
    r"|:(?P<pseudo>[\w-]+)" % (_IDENT, _IDENT))


class Style:
    """The computed values the converter reads for one element."""

    __slots__ = ("display", "hidden", "floated", "flags", "align", "transform", "caps",
                 "white_space", "size", "left", "indent", "above", "below",
                 "break_before", "break_after", "list_type", "marker")

    def __init__(self) -> None:
        self.display = "block"
        self.hidden = False
        self.floated = False
        self.flags = 0
        self.align = ""
        self.transform = ""
        self.caps = False
        self.white_space = "normal"
        self.size = 1.0
        self.left = 0.0
        self.indent = 0.0
        self.above = 0.0
        self.below = 0.0
        self.break_before = False
        self.break_after = False
        self.list_type = "disc"
        # The list style a `::before` rule draws in place of a marker, if any.
        self.marker = ""


ROOT = Style()
_INHERIT = object()
# `vertical-align: top` raises text only when it is also set smaller inline:
# that is how note and verse numbers are written without `super`.
_TOP = -1


class Element:
    """What selectors can see of an open element."""

    __slots__ = ("tag", "id", "classes", "attrs", "parent", "prev", "first", "style")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Element | None",
                 prev: "Element | None" = None, first: bool = True) -> None:
        self.tag = tag
        self.attrs = attrs
        self.id = attrs.get("id") or ""
        names = attrs.get("class")
        self.classes: tuple[str, ...] = tuple(names.split()[:32]) if names else ()
        self.parent = parent
        self.prev = prev
        self.first = first
        self.style = ROOT


class _Compound:
    __slots__ = ("tag", "id", "classes", "attrs", "first", "root", "link", "comb")

    def __init__(self) -> None:
        self.tag = ""
        self.id = ""
        self.classes: tuple[str, ...] = ()
        self.attrs: tuple[tuple[str, str, str], ...] = ()
        self.first = False
        self.root = False
        self.link = False
        # How this compound relates to the one on its left: " ", ">" or "+".
        self.comb = ""

    def test(self, element: Element) -> bool:
        if self.tag and self.tag != element.tag:
            return False
        if self.id and self.id != element.id:
            return False
        for name in self.classes:
            if name not in element.classes:
                return False
        for name, op, wanted in self.attrs:
            actual = element.attrs.get(name)
            if actual is None or not _attribute_matches(actual, op, wanted):
                return False
        if self.first and not element.first:
            return False
        if self.root and element.parent is not None:
            return False
        if self.link and not (element.tag == "a" and "href" in element.attrs):
            return False
        return True


def _attribute_matches(actual: str, op: str, wanted: str) -> bool:
    if not op:
        return True
    if op == "=":
        return actual == wanted
    if not wanted:
        return False
    if op == "~=":
        return wanted in actual.split()
    if op == "|=":
        return actual == wanted or actual.startswith(wanted + "-")
    if op == "^=":
        return actual.startswith(wanted)
    if op == "$=":
        return actual.endswith(wanted)
    return wanted in actual


class Rule:
    """One selector with its declaration block."""

    __slots__ = ("parts", "specificity", "declarations", "simple")

    def __init__(self, parts: list[_Compound], declarations: tuple) -> None:
        self.parts = parts
        ids = sum(1 for part in parts if part.id)
        classes = sum(len(part.classes) + len(part.attrs) + part.first + part.root + part.link
                      for part in parts)
        tags = sum(1 for part in parts if part.tag)
        self.specificity = (ids, classes, tags)
        self.declarations = declarations
        first = parts[0]
        # Decided by tag, id and classes alone, so the answer can be shared by
        # every element with the same three.
        self.simple = len(parts) == 1 and not (first.attrs or first.first or first.root
                                              or first.link)

    def matches(self, element: Element) -> bool:
        return _match(self.parts, 0, element, [_MATCH_BUDGET])


def _match(parts: list[_Compound], index: int, element: Element, budget: list[int]) -> bool:
    budget[0] -= 1
    if budget[0] < 0:
        return False
    part = parts[index]
    if not part.test(element):
        return False
    if index + 1 == len(parts):
        return True
    if part.comb == ">":
        return element.parent is not None and _match(parts, index + 1, element.parent, budget)
    if part.comb == "+":
        return element.prev is not None and _match(parts, index + 1, element.prev, budget)
    ancestor = element.parent
    while ancestor is not None:
        if _match(parts, index + 1, ancestor, budget):
            return True
        if budget[0] < 0:
            return False
        ancestor = ancestor.parent
    return False


class Sheet:
    """A parsed stylesheet: its rules in source order and the sheets it imports."""

    __slots__ = ("rules", "imports", "user_agent")

    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self.imports: list[str] = []
        self.user_agent = False


def _lex(text: str) -> tuple[str, list[str]]:
    """Remove comments and lift strings out, so structure can be found by character."""
    strings: list[str] = []

    def replace(match: re.Match) -> str:
        token = match.group()
        if token[0] in "\"'":
            body = token[1:-1] if len(token) > 1 and token[-1] == token[0] else token[1:]
            strings.append(_ESCAPE.sub(_unescape, body))
            return "\x00%d\x00" % (len(strings) - 1)
        if token[:4].lower() == "url(":
            strings.append(token[4:-1].strip())
            return "url(\x00%d\x00)" % (len(strings) - 1)
        return " "

    return _LEX.sub(replace, text.replace("\x00", "")), strings


def _unescape(match: re.Match) -> str:
    if match.group(1):
        code = int(match.group(1), 16)
        return chr(code) if 0 < code < 0x110000 and not 0xD800 <= code < 0xE000 else "\ufffd"
    return "" if match.group(2) == "\n" else match.group(2)


def _restore(value: str, strings: list[str]) -> str:
    return _STRING.sub(lambda m: strings[int(m.group(1))], value)


@lru_cache(maxsize=64)
def parse_stylesheet(text: str) -> Sheet:
    """Parse CSS text. Never raises: whatever cannot be understood is left out."""
    sheet = Sheet()
    source, strings = _lex(text[:MAX_SHEET_CHARS])
    _parse_rules(source, strings, sheet, 0)
    return sheet


def _parse_rules(source: str, strings: list[str], sheet: Sheet, depth: int) -> None:
    position = 0
    while len(sheet.rules) < MAX_RULES:
        found = _STRUCTURE.search(source, position)
        if found is None:
            return
        prelude = source[position:found.start()].strip()
        position = found.end()
        if found.group() == ";":
            imported = _IMPORT.match(prelude) if depth == 0 else None
            if imported and media_applies(_restore(imported.group(2), strings)):
                sheet.imports.append(_restore(imported.group(1), strings))
            continue
        if found.group() == "}":
            continue
        end = _block_end(source, position)
        body = source[position:end]
        position = end + 1
        if prelude.startswith("@"):
            if (prelude[:6].lower() == "@media" and depth < MAX_IMPORT_DEPTH
                    and media_applies(prelude[6:])):
                _parse_rules(body, strings, sheet, depth + 1)
            continue
        declarations = _declarations(body, strings)
        generated = _generated(body, strings) if "before" in prelude.lower() else ()
        for selector in _split_selectors(prelude):
            chosen = declarations
            before = _BEFORE.search(selector)
            if before is not None:
                selector, chosen = selector[:before.start()], generated
            parts = _parse_selector(selector, strings) if chosen else None
            if parts and len(sheet.rules) < MAX_RULES:
                sheet.rules.append(Rule(parts, chosen))


def _block_end(source: str, start: int) -> int:
    """Index of the brace closing the block that opens just before `start`."""
    depth = 1
    for found in _BRACES.finditer(source, start):
        depth += 1 if found.group() == "{" else -1
        if depth == 0:
            return found.start()
    return len(source)


def media_applies(query: str) -> bool:
    """Whether a media query list applies to this reader: a narrow colour screen.

    A query with a feature or a syntax not known here never applies, negated
    or not; a media type not known here is simply not this one.
    """
    query = query.strip().lower()
    if not query:
        return True
    return any(_media_member(member.strip()) for member in query.split(","))


def _media_member(member: str) -> bool:
    """Whether one query of a list applies."""
    negated = False
    for word in ("not", "only"):
        if member.startswith(word) and member[len(word):len(word) + 1] in ("", " ", "("):
            negated = word == "not"
            member = member[len(word):]
            break
    applies = True
    for position, part in enumerate(_MEDIA_AND.split(member)):
        part = part.strip()
        if part.startswith("("):
            verdict = _media_feature(part)
        else:
            verdict = part in _MEDIA_TYPES if part and position == 0 else None
        if verdict is None:
            return False
        applies = applies and verdict
    return applies != negated


def _media_feature(text: str) -> bool | None:
    """Whether `(feature: value)` holds here; None when that cannot be said."""
    found = _MEDIA_FEATURE.fullmatch(text)
    if found is None:
        return None
    name, value = found.group(1).replace("device-", ""), found.group(2)
    if value is None:
        return True if name == "color" else None
    if name == "orientation":
        return value == "portrait"
    length = _LENGTH.match(value)
    if name not in ("min-width", "max-width") or length is None:
        return None
    scale = _VIEWPORT_UNITS.get(length.group(2) or "px")
    if scale is None:
        return None
    width = float(length.group(1)) * scale
    return width <= _VIEWPORT_WIDTH if name == "min-width" else width >= _VIEWPORT_WIDTH


def _split_selectors(prelude: str) -> list[str]:
    if "," not in prelude:
        return [prelude] if prelude else []
    if "(" not in prelude and "[" not in prelude:
        return [part.strip() for part in prelude.split(",") if part.strip()]
    parts, depth, start = [], 0, 0
    for index, char in enumerate(prelude):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth <= 0:
            parts.append(prelude[start:index].strip())
            start = index + 1
    parts.append(prelude[start:].strip())
    return [part for part in parts if part]


def _parse_selector(selector: str, strings: list[str]) -> list[_Compound] | None:
    """Compounds from right to left, or None when the selector is not supported."""
    if len(selector) > 2048:
        return None
    parts = [_Compound()]
    empty = True
    position = 0
    while position < len(selector):
        found = _SELECTOR_TOKEN.match(selector, position)
        if found is None or found.end() == position:
            return None
        position = found.end()
        kind = found.lastgroup
        current = parts[-1]
        if kind == "comb":
            comb = found.group().strip() or " "
            if comb == "~" or empty or len(parts) >= 16:
                return None
            parts.append(_Compound())
            parts[-1].comb = comb
            empty = True
            continue
        if kind == "ns":
            return None
        if kind == "tag":
            if not empty:
                return None
            if found.group() != "*":
                current.tag = found.group().lower()
        elif kind == "id":
            current.id = _ESCAPE.sub(_unescape, found.group("id"))
        elif kind == "cls":
            current.classes += (_ESCAPE.sub(_unescape, found.group("cls")),)
        elif kind in ("attr", "op", "val"):
            prefix = found.group("ans")
            if prefix == "*":
                return None
            name = found.group("attr").lower()
            if prefix:
                name = prefix.lower() + ":" + name
            value = found.group("val") or ""
            if value[:1] == "\x00":
                value = _restore(value, strings)
            else:
                value = _ESCAPE.sub(_unescape, value)
            current.attrs += ((name, found.group("op") or "", value),)
        else:
            pseudo = found.group("pseudo").lower()
            if selector[position:position + 1] == "(":
                return None
            if pseudo == "first-child":
                current.first = True
            elif pseudo == "root":
                current.root = True
            elif pseudo in ("link", "any-link"):
                current.link = True
            else:
                return None
        empty = False
    if empty:
        return None
    return parts[::-1]


@lru_cache(maxsize=2048)
def parse_declarations(text: str) -> tuple:
    """The declarations of a `style` attribute, as the cascade stores them."""
    source, strings = _lex(text[:MAX_STYLE_ATTRIBUTE])
    return _declarations(source, strings)


def _declarations(body: str, strings: list[str]) -> tuple:
    """`(property, value, important)` for every declaration this module honours."""
    found = []
    for piece in body.split(";"):
        name, colon, value = piece.partition(":")
        if not colon:
            continue
        name = name.strip().lower()
        if name.startswith("--"):
            continue
        if name.startswith("-"):
            name = name[1:].partition("-")[2]
        reader = _READERS.get(name)
        if reader is None:
            continue
        value = value.strip()
        important = _IMPORTANT.search(value) is not None
        if important:
            value = _IMPORTANT.sub("", value).rstrip()
        value = " ".join(_STRING.sub(" ", value).lower().split())
        if not value:
            continue
        if value == "inherit":
            targets = _INHERITABLE.get(name, ())
            found.extend((target, _INHERIT, important) for target in targets)
            continue
        for target, result in reader(value):
            found.append((target, result, important))
    return tuple(found)


def _generated(body: str, strings: list[str]) -> tuple:
    """What a `::before` rule draws in front of its element, as a declaration.

    Only the kind of marker is kept: the list style of a counter, or a bullet
    for literal text.
    """
    for piece in body.split(";"):
        name, colon, value = piece.partition(":")
        if not colon or name.strip().lower() != "content":
            continue
        important = _IMPORTANT.search(value) is not None
        counter = _COUNTER.search(value)
        if counter is not None:
            kind = (counter.group(1) or "decimal").lower()
            kind = kind if kind in _LIST_TYPES and kind != "none" else "decimal"
        elif "attr(" in value.lower() or any(
                strings[int(number)].strip() for number in _STRING.findall(value)):
            kind = "disc"
        else:
            kind = ""
        return (("marker", kind, important),)
    return ()


def _length(value: str) -> tuple[float, str] | None:
    """A length as (number, unit); unit "" means the number is already in em."""
    if value in ("0", "auto", "normal", "none"):
        return 0.0, ""
    found = _LENGTH.match(value)
    if found is None:
        return None
    number = float(found.group(1))
    unit = found.group(2) or "px"
    if unit in _FONT_RELATIVE:
        return number * _FONT_RELATIVE[unit], "em"
    if unit in _UNITS:
        return number * _UNITS[unit], ""
    return None


def _one_length(target: str) -> Callable[[str], list]:
    def read(value: str) -> list:
        length = _length(value)
        return [] if length is None else [(target, length)]
    return read


def _box(top: str, left: str, bottom: str) -> Callable[[str], list]:
    """Reader for the `margin` / `padding` shorthands."""
    def read(value: str) -> list:
        lengths = [_length(part) for part in value.split()]
        if not 1 <= len(lengths) <= 4 or None in lengths:
            return []
        count = len(lengths)
        return [(top, lengths[0]),
                (left, lengths[3] if count == 4 else lengths[1] if count > 1 else lengths[0]),
                (bottom, lengths[2] if count > 2 else lengths[0])]
    return read


def _keyword(target: str, table: dict) -> Callable[[str], list]:
    def read(value: str) -> list:
        return [(target, table[value])] if value in table else []
    return read


def _font_weight(value: str) -> list:
    if value in ("bold", "bolder"):
        return [("bold", True)]
    if value in ("normal", "lighter", "medium"):
        return [("bold", False)]
    if value.isdigit() and len(value) <= 4:
        return [("bold", int(value) >= 600)]
    return []


def _font_size(value: str) -> list:
    if value in _SIZE_KEYWORDS:
        return [("size", (_SIZE_KEYWORDS[value], False))]
    if value in ("larger", "smaller"):
        return [("size", (1.2 if value == "larger" else 1 / 1.2, True))]
    found = _LENGTH.match(value)
    if found is None or not found.group(2):
        return []
    number, unit = float(found.group(1)), found.group(2)
    if number < 0:
        return []
    if unit in _FONT_RELATIVE:
        return [("size", (number * _FONT_RELATIVE[unit], True))]
    if unit == "%":
        return [("size", (number / 100, True))]
    if unit in _UNITS:
        return [("size", (number * _UNITS[unit], False))]
    return []


def _font(value: str) -> list:
    """The `font` shorthand: everything before the size, and the size."""
    found = {"italic": False, "bold": False, "caps": False}
    for word in value.split():
        if word in ("italic", "oblique"):
            found["italic"] = True
        elif word == "small-caps":
            found["caps"] = True
        elif word == "normal":
            continue
        elif _font_weight(word):
            found["bold"] = _font_weight(word)[0][1]
        else:
            size = _font_size(word.partition("/")[0])
            if not size:
                return []
            return list(found.items()) + size
    return []


def _decoration(value: str) -> list:
    words = value.split()
    bits = (UNDERLINE if "underline" in words else 0) | (STRIKE if "line-through" in words else 0)
    if bits or "none" in words:
        return [("decoration", bits)]
    return []


def _variant(value: str) -> list:
    words = value.split()
    if "small-caps" in words or "all-small-caps" in words:
        return [("caps", True)]
    if "normal" in words or "none" in words:
        return [("caps", False)]
    return []


def _display(value: str) -> list:
    if value in ("none", "inline", "inline-block", "block"):
        return [("display", value)]
    if value in ("contents", "in-line", "ruby", "ruby-base", "ruby-text"):
        return [("display", "inline")]
    if value.startswith("inline-"):
        return [("display", "inline-block")]
    if value in _BLOCK_DISPLAYS or value.startswith("table-"):
        return [("display", "block")]
    return []


def _zero(target: str) -> Callable[[str], list]:
    """Reader for a box dimension: only whether it is a literal zero matters."""
    def read(value: str) -> list:
        found = _LENGTH.match(value)
        return [(target, found is not None and float(found.group(1)) == 0)]
    return read


def _page_break(target: str) -> Callable[[str], list]:
    def read(value: str) -> list:
        if value in ("always", "page", "left", "right", "recto", "verso"):
            return [(target, True)]
        if value in ("auto", "avoid", "avoid-page", "avoid-column", "column"):
            return [(target, False)]
        return []
    return read


def _list_style(value: str) -> list:
    words = value.split()
    for word in words:
        if word in _LIST_TYPES and word != "none":
            return [("list_type", word)]
    if "none" in words:
        return [("list_type", "none")]
    if len(words) == 1 and re.fullmatch(r"[a-z-]+", value) and value not in (
            "inside", "outside", "initial", "unset", "revert"):
        # A counter style this module cannot draw still means "numbered".
        return [("list_type", "decimal")]
    return []


_READERS: dict[str, Callable[[str], list]] = {
    "display": _display,
    "visibility": _keyword("hidden", {"hidden": True, "collapse": True, "visible": False}),
    "float": _keyword("floated", {"left": True, "right": True, "none": False,
                                  "inline-start": True, "inline-end": True}),
    "font-style": _keyword("italic", {"italic": True, "oblique": True, "normal": False}),
    "font-weight": _font_weight,
    "font-size": _font_size,
    "font": _font,
    "font-variant": _variant,
    "font-variant-caps": _variant,
    "text-decoration": _decoration,
    "text-decoration-line": _decoration,
    "vertical-align": _keyword("raised", {"super": SUPER, "sup": SUPER, "sub": SUB,
                                          "baseline": 0, "top": _TOP, "text-top": _TOP}),
    "text-align": _keyword("align", {
        "center": "c", "-webkit-center": "c", "-moz-center": "c", "right": "r", "end": "r",
        "-webkit-right": "r", "left": "", "start": "", "justify": "", "-webkit-left": ""}),
    "text-transform": _keyword("transform", {"uppercase": "upper", "lowercase": "lower",
                                             "capitalize": "capitalize", "none": ""}),
    "white-space": _keyword("white_space", {
        "normal": "normal", "nowrap": "normal", "pre": "pre", "pre-wrap": "pre",
        "break-spaces": "pre", "pre-line": "pre-line"}),
    "margin": _box("margin_top", "margin_left", "margin_bottom"),
    "padding": _box("padding_top", "padding_left", "padding_bottom"),
    "margin-left": _one_length("margin_left"),
    "padding-left": _one_length("padding_left"),
    "margin-top": _one_length("margin_top"),
    "padding-top": _one_length("padding_top"),
    "margin-bottom": _one_length("margin_bottom"),
    "padding-bottom": _one_length("padding_bottom"),
    "text-indent": _one_length("indent"),
    "page-break-before": _page_break("break_before"),
    "page-break-after": _page_break("break_after"),
    "break-before": _page_break("break_before"),
    "break-after": _page_break("break_after"),
    "list-style-type": _list_style,
    "list-style": _list_style,
    "overflow": _keyword("clipped", {"hidden": True, "clip": True, "visible": False,
                                     "auto": False, "scroll": False}),
    "height": _zero("no_height"),
    "max-height": _zero("no_max_height"),
    "width": _zero("no_width"),
    "max-width": _zero("no_max_width"),
}

# What `inherit` sets for each property that can be asked to inherit.
_INHERITABLE = {
    "visibility": ("hidden",), "font-style": ("italic",), "font-weight": ("bold",),
    "font-size": ("size",), "font-variant": ("caps",), "font-variant-caps": ("caps",),
    "text-align": ("align",), "text-transform": ("transform",),
    "white-space": ("white_space",), "text-indent": ("indent",),
    "list-style-type": ("list_type",), "list-style": ("list_type",),
    "font": ("italic", "bold", "caps", "size"),
}

_USER_AGENT = """
html, body, address, article, aside, blockquote, center, dd, details, dialog, dir, div, dl, dt,
fieldset, figcaption, figure, footer, form, h1, h2, h3, h4, h5, h6, header, hgroup, legend, li,
main, menu, nav, ol, p, pre, section, summary, ul, table, caption, thead, tbody, tfoot, tr, td,
th, hr, listing, xmp, search { display: block }
head, script, style, title, template, meta, link, base, param, noembed, noframes, datalist,
area, [hidden] { display: none }
i, em, cite, dfn, var, address { font-style: italic }
b, strong, th, h1, h2, h3, h4, h5, h6 { font-weight: bold }
u, ins { text-decoration: underline }
s, strike, del { text-decoration: line-through }
sup { vertical-align: super }
sub { vertical-align: sub }
center { text-align: center }
pre, listing, xmp { white-space: pre }
blockquote, dd { margin-left: 2.5em }
h1 { font-size: 2em }
h2 { font-size: 1.5em }
h3 { font-size: 1.17em }
h5 { font-size: 0.83em }
h6 { font-size: 0.67em }
big { font-size: larger }
small, sub, sup { font-size: smaller }
ul, menu, dir { list-style-type: disc }
ol { list-style-type: decimal }
ul ul, ol ul { list-style-type: circle }
ul ul ul, ul ol ul, ol ul ul, ol ol ul { list-style-type: square }
"""


def _user_agent_sheet() -> Sheet:
    sheet = Sheet()
    source, strings = _lex(_USER_AGENT)
    _parse_rules(source, strings, sheet, 0)
    sheet.user_agent = True
    return sheet


USER_AGENT = _user_agent_sheet()


def collect_sheets(text: str, base: str, fetch: Callable[[str, str], str | None],
                   sheets: list[Sheet], depth: int = 0, seen: set[str] | None = None) -> None:
    """Append the sheet for `text` to `sheets`, after the sheets it imports.

    `base` is the path of whatever holds `text`; `fetch(href, base)` returns
    the CSS an import refers to, or None.
    """
    if len(sheets) >= MAX_SHEETS:
        return
    seen = set() if seen is None else seen
    sheet = parse_stylesheet(text)
    if depth < MAX_IMPORT_DEPTH:
        for href in sheet.imports:
            path = href.partition("#")[0].partition("?")[0]
            location = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
            if location in seen:
                continue
            seen.add(location)
            imported = fetch(href, base)
            if imported:
                collect_sheets(imported, location, fetch, sheets, depth + 1, seen)
    if len(sheets) < MAX_SHEETS:
        sheets.append(sheet)


class Cascade:
    """Computes element styles from a fixed list of sheets.

    Rules are indexed by the rightmost compound of their selector, so styling
    an element costs in proportion to the rules that could apply to it, and
    every answer that depends only on (tag, id, classes) is remembered.
    """

    def __init__(self, sheets: Iterable[Sheet]) -> None:
        self._buckets: dict[object, list[tuple[tuple, Rule]]] = {}
        order = 0
        for sheet in (USER_AGENT, *sheets):
            origin = 0 if sheet.user_agent else 1
            for rule in sheet.rules:
                order += 1
                first = rule.parts[0]
                if first.id:
                    key: object = ("#", first.id)
                elif first.classes:
                    key = (".", first.classes[0])
                elif first.tag:
                    key = first.tag
                elif first.attrs:
                    key = ("[", first.attrs[0][0])
                else:
                    key = "*"
                self._buckets.setdefault(key, []).append(((origin, *rule.specificity, order), rule))
        self._candidates: dict[tuple, tuple] = {}
        self._specified: dict[tuple, dict] = {}
        self._computed: dict[tuple, Style] = {}

    def style(self, element: Element) -> Style:
        """The computed style of `element`, whose parent is already styled."""
        attrs = element.attrs
        extra = ()
        if len(attrs) > (1 if element.classes else 0) + (1 if element.id else 0):
            extra = tuple(sorted(name for name in attrs if name not in ("class", "id", "style")))
        key = (element.tag, element.id, element.classes, extra)
        entry = self._candidates.get(key)
        if entry is None:
            if len(self._candidates) > _MEMO_LIMIT:
                self._candidates.clear()
                self._specified.clear()
                self._computed.clear()
            entry = self._candidates[key] = self._gather(key)
        specified, conditional, variants = entry
        if conditional:
            matched = tuple(index for index, (_, rule) in enumerate(conditional)
                            if rule.matches(element))
            if matched:
                specified = variants.get(matched)
                if specified is None:
                    specified = dict(entry[0])
                    _specify(specified, [conditional[index] for index in matched])
                    variants[matched] = specified
        inline = attrs.get("style") or ""
        hints = (inline, attrs.get("align") or "", attrs.get("type") or "",
                 attrs.get("size") or "" if element.tag == "font" else "")
        if any(hints):
            hinted_key = (id(specified), hints, element.tag)
            hinted = self._specified.get(hinted_key)
            if hinted is None:
                if len(self._specified) > _MEMO_LIMIT:
                    self._specified.clear()
                    self._computed.clear()
                hinted = (_with_hints(specified, element.tag, hints), specified)
                self._specified[hinted_key] = hinted
            specified = hinted[0]
        parent = element.parent.style if element.parent is not None else ROOT
        computed_key = (id(parent), id(specified))
        entry = self._computed.get(computed_key)
        if entry is None:
            if len(self._computed) > _MEMO_LIMIT:
                self._computed.clear()
            # The key holds ids, so the objects are kept alive beside the result.
            entry = self._computed[computed_key] = (_compute(parent, specified), parent, specified)
        return entry[0]

    def _gather(self, key: tuple) -> tuple:
        tag, element_id, classes, extra = key
        buckets = self._buckets
        found: list[tuple[tuple, Rule]] = []
        found.extend(buckets.get("*", ()))
        found.extend(buckets.get(tag, ()))
        if element_id:
            found.extend(buckets.get(("#", element_id), ()))
        for name in classes:
            found.extend(buckets.get((".", name), ()))
        for name in extra:
            found.extend(buckets.get(("[", name), ()))
        if element_id and "id" not in extra:
            found.extend(buckets.get(("[", "id"), ()))
        if classes:
            found.extend(buckets.get(("[", "class"), ()))
        definite, conditional = [], []
        for item in found:
            first = item[1].parts[0]
            if first.tag and first.tag != tag or first.id and first.id != element_id:
                continue
            if any(name not in classes for name in first.classes):
                continue
            (definite if item[1].simple else conditional).append(item)
        specified: dict = {}
        _specify(specified, definite)
        return specified, conditional, {}


def _specify(specified: dict, rules: list[tuple[tuple, Rule]]) -> None:
    """Merge declarations into `specified`, keeping the winner of each property."""
    for priority, rule in rules:
        for name, value, important in rule.declarations:
            rank = (important, *priority)
            held = specified.get(name)
            if held is None or rank >= held[0]:
                specified[name] = (rank, value)


def _with_hints(specified: dict, tag: str, hints: tuple[str, str, str, str]) -> dict:
    """`specified` plus the element's `style` attribute and presentational attributes."""
    inline, align, list_type, font_size = hints
    merged = dict(specified)
    hinted = []
    if align and tag != "table" and tag != "img":
        align = align.lower()
        hinted.extend(_READERS["text-align"]("center" if align == "middle" else align))
    if list_type and tag in ("ol", "ul", "li"):
        kind = _LIST_ATTRIBUTE.get(list_type) or list_type.lower()
        if kind in _LIST_TYPES:
            hinted.append(("list_type", kind))
    if font_size:
        hinted.extend(_font_attribute(font_size))
    # Presentational attributes rank below every author rule.
    ranked = [(name, value, (False, 1, -1)) for name, value in hinted]
    if inline:
        ranked.extend((name, value, (important, 2))
                      for name, value, important in parse_declarations(inline))
    for name, value, rank in ranked:
        held = merged.get(name)
        if held is None or rank >= held[0]:
            merged[name] = (rank, value)
    return merged


def _font_attribute(value: str) -> list:
    """`<font size>`: 1 to 7, or a signed step from the default of 3."""
    value = value.strip()
    try:
        number = int(value)
    except ValueError:
        return []
    if value[0] in "+-":
        number += 3
    return [("size", (_SIZE_KEYWORDS[_FONT_SIZES[min(7, max(1, number)) - 1]], False))]


@lru_cache(maxsize=16)
def cascade_for(sheets: tuple[Sheet, ...]) -> Cascade:
    """A cascade for these author sheets, shared by documents that use the same ones."""
    return Cascade(sheets)


def _compute(parent: Style, specified: dict) -> Style:
    style = Style()
    style.display = "inline"
    style.hidden = parent.hidden
    style.flags = parent.flags
    style.align = parent.align
    style.transform = parent.transform
    style.caps = parent.caps
    style.white_space = parent.white_space
    style.size = parent.size
    style.indent = parent.indent
    style.list_type = parent.list_type
    if not specified:
        return style
    get = specified.get

    held = get("size")
    if held is not None and held[1] is not _INHERIT:
        number, relative = held[1]
        style.size = min(20.0, max(0.1, number * parent.size if relative else number))
    size = style.size

    def em(name: str) -> float:
        value = get(name)
        if value is None or value[1] is _INHERIT:
            return 0.0
        number, unit = value[1]
        return number * size if unit else number

    for name in ("display", "floated", "break_before", "break_after", "marker"):
        held = get(name)
        if held is not None and held[1] is not _INHERIT:
            setattr(style, name, held[1])

    def given(name: str) -> bool:
        held = get(name)
        return held is not None and held[1] is True

    # A clipped box with no height or no width shows nothing: publishers hide
    # spacer text that way. Padding still shows (the fixed-ratio picture frame).
    if given("clipped") and (
            (given("no_height") or given("no_max_height"))
            and not em("padding_top") and not em("padding_bottom")
            or (given("no_width") or given("no_max_width")) and not em("padding_left")):
        style.display = "none"
    for name in ("hidden", "align", "transform", "caps", "white_space", "list_type"):
        held = get(name)
        if held is not None and held[1] is not _INHERIT:
            setattr(style, name, held[1])
    flags = style.flags
    for name, bit in (("bold", BOLD), ("italic", ITALIC)):
        held = get(name)
        if held is not None and held[1] is not _INHERIT:
            flags = flags | bit if held[1] else flags & ~bit
    held = get("decoration")
    if held is not None:
        flags |= held[1]
    held = get("raised")
    if held is not None:
        raised = held[1]
        if raised == _TOP:
            small = style.display == "inline" and size <= parent.size * 0.85
            raised = SUPER if small else flags & (SUPER | SUB)
        flags = flags & ~(SUPER | SUB) | raised
    style.flags = flags
    held = get("indent")
    if held is not None and held[1] is not _INHERIT:
        style.indent = em("indent")
    style.left = em("margin_left") + em("padding_left")
    style.above = em("margin_top") + em("padding_top")
    style.below = em("margin_bottom") + em("padding_bottom")
    return style
