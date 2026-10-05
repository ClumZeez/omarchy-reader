"""Tolerant XML for package files (container, OPF, NCX, nav, FB2).

Parsing is namespace-unaware: tag and attribute names stay exactly as written
("dc:title", "opf:role"), so undeclared prefixes never matter, and lookups
go by local name.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from xml.etree.ElementTree import Element
from xml.parsers import expat

from .errors import ReaderError
from .textutil import (
    collapse_space,
    decode_bytes,
    replace_entities,
    strip_declarations,
    strip_illegal_xml,
)

_DAMAGED = "This book is damaged and can't be opened."

_DOCTYPE_START = re.compile(r"<!DOCTYPE\b", re.I)
_DOCTYPE = re.compile(r"<!DOCTYPE\b[^\[>]*(?:\[(.*?)\]\s*)?>", re.I | re.S)
_ENTITY = re.compile(r"<!ENTITY\s+([^\s%\"'>]{1,64})\s+(\"[^\"]{0,4096}\"|'[^']{0,4096}')\s*>")
_PRIVATE_REFERENCE = re.compile(r"&([^\s&;<>]{1,64});")
_OPAQUE_OR_AMPERSAND = re.compile(r"<!\[CDATA\[|<!--|&(?!(?:amp|lt|gt|quot|apos);)")
_OPAQUE_END = {"<![CDATA[": "]]>", "<!--": "-->"}
_NAME = re.compile(r"[A-Za-z_][\w:.-]*")
_WRITTEN_TAG = re.compile(r"<\s*([^\s/>]+)")

_DOCTYPE_WINDOW = 64 * 1024
_MAX_ENTITIES = 1000
_MAX_ENTITY_VALUE = 4096
_MAX_EXPANSION = 1024 * 1024
# A megabyte of zip can hold a file of nothing but tags that asks for
# gigabytes of tree. No book's package file or FictionBook comes near this.
MAX_TAGS = 1_000_000

# Elements that never contain other elements, in HTML and in the package
# vocabularies; when one is left unclosed the next tag is its sibling.
_LEAVES = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
    "item", "itemref", "reference", "content", "rootfile",
})
# A start tag that implies the end of an open element of these kinds.
_IMPLIED_END = {
    "li": ("li",),
    "p": ("p",),
    "dt": ("dt", "dd"),
    "dd": ("dt", "dd"),
    "option": ("option",),
    "tr": ("tr", "td", "th"),
    "td": ("td", "th"),
    "th": ("td", "th"),
}


def parse_xml(data: bytes | str, *, language: str = "") -> Element:
    """Parse a package file, however badly formed, into an element tree.

    Raises ReaderError("corrupt") only when the input holds no element at all.
    """
    if data.count("<" if isinstance(data, str) else b"<") > MAX_TAGS:
        raise ReaderError("corrupt", _DAMAGED)
    if isinstance(data, str):
        text = data.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    else:
        text = decode_bytes(data, kind="package", language=language)
    start = text.find("<")
    if start < 0:
        raise ReaderError("corrupt", _DAMAGED)
    text = _without_doctype(strip_declarations(text[start:]))
    text = strip_illegal_xml(replace_entities(text))
    text = _escape_stray_ampersands(text)
    root = _parse_strict(text)
    if root is None:
        root = _parse_tolerant(text)
    if root is None:
        raise ReaderError("corrupt", _DAMAGED)
    return root


def local(name: str) -> str:
    """The name without its prefix or namespace: "dc:title" → "title"."""
    return name.rpartition("}")[2].rpartition(":")[2]


def children(el: Element, name: str) -> list[Element]:
    """Direct children with this local name, whatever its case or prefix."""
    wanted = name.lower()
    return [child for child in el if local(child.tag).lower() == wanted]


def descendants(el: Element, name: str) -> list[Element]:
    """Every element below `el` with this local name, in document order."""
    wanted = name.lower()
    return [
        found for found in el.iter()
        if found is not el and local(found.tag).lower() == wanted
    ]


def attr(el: Element, name: str, default: str = "") -> str:
    """An attribute by local name, whatever its case or prefix."""
    value = el.get(name)
    if value is not None:
        return value
    wanted = local(name).lower()
    for key, value in el.attrib.items():
        lowered = key.lower()
        if local(lowered) == wanted and not lowered.startswith("xmlns:"):
            return value
    return default


def text_of(el: Element) -> str:
    """All the text inside an element, whitespace-collapsed; a <br> counts as a space."""
    parts = [el.text or ""]
    open_elements = [(el, iter(el))]
    while open_elements:
        parent, remaining = open_elements[-1]
        child = next(remaining, None)
        if child is None:
            open_elements.pop()
            if parent is not el:
                parts.append(parent.tail or "")
            continue
        if local(child.tag).lower() == "br":
            parts.append(" ")
        parts.append(child.text or "")
        open_elements.append((child, iter(child)))
    return collapse_space("".join(parts))


def _without_doctype(text: str) -> str:
    """Drop the DOCTYPE, first applying the entities its internal subset defines.

    With no DTD left the parser has nothing to load or expand, so neither
    external entities nor exponential entity nesting can reach it.
    """
    found = _DOCTYPE_START.search(text, 0, _DOCTYPE_WINDOW)
    doctype = _DOCTYPE.match(text, found.start()) if found else None
    if doctype is None:
        return text
    entities: dict[str, str] = {}
    budget = _MAX_EXPANSION

    def expand(match: re.Match) -> str:
        nonlocal budget
        value = entities.get(match.group(1))
        if value is None:
            return match.group(0)
        budget -= len(value)
        return value if budget >= 0 else ""

    for declared in _ENTITY.finditer(doctype.group(1) or ""):
        if len(entities) >= _MAX_ENTITIES:
            break
        value = _PRIVATE_REFERENCE.sub(expand, declared.group(2)[1:-1])
        if len(value) <= _MAX_ENTITY_VALUE:
            entities.setdefault(declared.group(1), value)
    text = text[:doctype.start()] + text[doctype.end():]
    budget = _MAX_EXPANSION
    return _PRIVATE_REFERENCE.sub(expand, text) if entities else text


def _escape_stray_ampersands(text: str) -> str:
    """Escape each "&" that starts no reference, outside CDATA and comments."""
    parts: list[str] = []
    copied = pos = 0
    while found := _OPAQUE_OR_AMPERSAND.search(text, pos):
        if found.group() == "&":
            parts += (text[copied:found.start()], "&amp;")
            copied = pos = found.end()
        else:
            end = text.find(_OPAQUE_END[found.group()], found.end())
            pos = len(text) if end < 0 else end
    parts.append(text[copied:])
    return "".join(parts)


class _Refused(Exception):
    """The document declares entities, which the strict parser must not expand."""


class _Tree:
    """Builds elements from start, end and text events."""

    def __init__(self) -> None:
        self.root: Element | None = None
        self.stack: list[Element] = []
        self._closed: Element | None = None
        self._text: list[str] = []

    @property
    def complete(self) -> bool:
        return self.root is not None and not self.stack

    def start(self, tag: str, attributes: dict[str, str]) -> None:
        self.flush()
        element = Element(tag, attributes)
        if self.stack:
            self.stack[-1].append(element)
        elif self.root is None:
            self.root = element
        self.stack.append(element)
        self._closed = None

    def end(self, tag: str = "") -> None:
        self.flush()
        self._closed = self.stack.pop()

    def text(self, text: str) -> None:
        self._text.append(text)

    def flush(self) -> None:
        if not self._text:
            return
        text = "".join(self._text)
        self._text.clear()
        if not self.stack:
            return
        if self._closed is not None:
            self._closed.tail = text
        else:
            self.stack[-1].text = text

    def refuse(self, *declaration: object) -> None:
        raise _Refused


def _parse_strict(text: str) -> Element | None:
    """Parse well-formed XML; None when the document is not."""
    tree = _Tree()
    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.EntityDeclHandler = tree.refuse
    parser.StartElementHandler = tree.start
    parser.EndElementHandler = tree.end
    parser.CharacterDataHandler = tree.text
    try:
        parser.Parse(text, True)
    except (expat.ExpatError, _Refused, ValueError):
        # Junk after the closing root tag does not spoil what came before it.
        return tree.root if tree.complete else None
    return tree.root


class _Soup(HTMLParser):
    """Feeds a tree from markup that is not well-formed XML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tree = _Tree()
        self._open: Counter[str] = Counter()

    def set_cdata_mode(self, *args: object, **kwargs: object) -> None:
        # HTML reads <title>, <style> and <script> content as raw text; in
        # FictionBook a <title> holds paragraphs.
        pass

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._start(tag, attrs) and len(self.tree.stack) > 1:
            self._pop()

    def handle_endtag(self, tag: str) -> None:
        if not self._open[tag]:
            return
        while len(self.tree.stack) > 1 and self._pop() != tag:
            pass

    def handle_data(self, data: str) -> None:
        self.tree.text(data)

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            self.tree.text(data[6:])

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if not _NAME.fullmatch(tag):
            return False
        stack = self.tree.stack
        if len(stack) > 1 and self._top() in _LEAVES:
            self._pop()
        implied = _IMPLIED_END.get(tag, ())
        while len(stack) > 1 and self._top() in implied:
            self._pop()
        attributes: dict[str, str] = {}
        for name, value in attrs:
            if _NAME.fullmatch(name):
                attributes.setdefault(name, value or "")
        written = _WRITTEN_TAG.match(self.get_starttag_text() or "")
        name = written.group(1) if written and written.group(1).lower() == tag else tag
        self.tree.start(name, attributes)
        self._open[tag] += 1
        return True

    def _top(self) -> str:
        return self.tree.stack[-1].tag.lower()

    def _pop(self) -> str:
        tag = self._top()
        self.tree.end()
        self._open[tag] -= 1
        return tag


def _parse_tolerant(text: str) -> Element | None:
    """Build the best tree tag soup allows; None when it holds no element.

    The first element becomes the root and is never closed early, so content
    after a misplaced closing tag stays reachable.
    """
    comment = text.rfind("<!--")
    if comment >= 0 and text.find("-->", comment) < 0:
        # An unterminated comment would swallow the rest of the document.
        text = text[:comment] + text[comment + 4:]
    soup = _Soup()
    try:
        soup.feed(text)
        soup.close()
    except Exception:  # keep whatever was built before the parser gave up
        pass
    soup.tree.flush()
    return soup.tree.root
