"""Tolerant (X)HTML to blocks: the conversion core shared by every reflowable format.

A document is read as a stream of parser events over a stack of open
elements; no tree is built. Each element gets a computed style from `css`,
block-level elements delimit blocks, and everything else contributes styled
text to the block being written.

Block boundaries follow the content iterator of the Readium toolkits
(HtmlResourceContentIterator.kt / html_converter.go, BSD-3-Clause, Copyright
Readium Foundation): a block-level element flushes the pending text, `br`
stays inside its block. The code is written fresh.
"""

from __future__ import annotations

import posixpath
import re
from html.parser import HTMLParser
from typing import Callable, Protocol
from urllib.parse import unquote

from . import css
from .blocks import BOLD, TEXT_KINDS, BookBuilder, Inline, clean, escape, plain_text
from .errors import ReaderError

MAX_DEPTH = 256
MAX_BLOCKS = 100_000
MAX_ATTRIBUTES = 64
MAX_HREF = 2048
MAX_ENTITY_GROWTH = 4 << 20
SMALL_IMAGE = 64
FAR_RIGHT = 12.0
CELL_LIMIT = 240
MARKER_LIMIT = 6
MAX_COLUMNS = 12
DEGRADED_BLOCK = 2000
ITEM_SCAN = 4 << 20

_TOO_LARGE = "This book is too large to open."

_SKIP, _INLINE, _LINE, _BLOCK = range(4)

_VOID = frozenset((
    "area", "base", "basefont", "bgsound", "br", "col", "embed", "frame", "hr", "img", "input",
    "keygen", "link", "meta", "param", "source", "track", "wbr",
))
_BLOCK_TAGS = frozenset((
    "html", "body", "address", "article", "aside", "blockquote", "center", "dd", "details",
    "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "legend", "li", "main", "menu",
    "nav", "ol", "p", "pre", "section", "summary", "ul", "table", "caption", "thead", "tbody",
    "tfoot", "tr", "td", "th", "listing", "xmp", "search",
))
# Elements that keep their role whatever `display` the publisher gives them.
_STRUCTURAL = frozenset((
    "html", "body", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "ul", "ol", "dl",
    "pre", "listing", "xmp", "blockquote", "table", "caption", "thead", "tbody", "tfoot", "tr",
    "td", "th", "figure", "figcaption",
))
_CLOSES_P = frozenset((
    "address", "article", "aside", "blockquote", "center", "details", "dialog", "dir", "div",
    "dl", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section", "table",
    "ul", "li", "dd", "dt", "summary", "listing", "xmp",
))
_HEAD_TAGS = frozenset((
    "head", "title", "style", "script", "link", "meta", "base", "basefont", "bgsound",
    "noscript", "template", "noframes",
))
_DROPPED = frozenset((
    "script", "template", "iframe", "audio", "video", "canvas", "select", "textarea", "button",
    "map", "applet", "frameset", "noframes", "noembed", "datalist", "rp",
))
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_TABLE_SECTIONS = frozenset(("thead", "tbody", "tfoot", "caption", "colgroup"))
_TABLE_STRUCTURE = frozenset(("table", "thead", "tbody", "tfoot", "tr"))
_P_SCOPE = frozenset(("table", "td", "th", "caption", "button", "object"))
_ITEM_SCOPE = frozenset(("ul", "ol", "menu", "dir", "dl", "table", "td", "th", "caption"))
_LISTS = frozenset(("ul", "ol", "menu", "dir"))
_MARGINLESS = frozenset(("html", "body", "ul", "ol", "menu", "dir", "li", "table", "thead",
                         "tbody", "tfoot", "tr", "td", "th"))

_DOCTYPE = re.compile(r"<!DOCTYPE\b", re.I)
_ENTITY = re.compile(r"<!ENTITY\s+([\w.:-]+)\s+(?:\"([^\"<]*)\"|'([^'<]*)')\s*>")
_SUBSET_END = re.compile(r"\]\s*>")
_COMMENT_END = re.compile(r"--!?>")
_DANGLING_COMMENT = re.compile(r"<!--[^>]*>?")
# `[^<>]` rather than `[^>]`: a search that fails must fail at the next tag,
# not at the end of the document, or broken markup costs quadratic time.
_RAW_SELF_CLOSED = re.compile(r"<(script|style)\b([^<>]*)/>", re.I)
_RAW_OPEN = re.compile(r"<(script|style)\b[^<>]*>", re.I)
_PLAINTEXT = re.compile(r"<(/?)plaintext\b", re.I)
_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*:")
_WORD_START = re.compile(r"(?<![\w'’\xad])\w")
_URL_BREAKS = re.compile(r"[\t\n\r]")
_IMAGE_NAME = re.compile(r"\.(?:jpe?g|png|gif|svg|webp|bmp|tiff?)$", re.I)
_PLACEHOLDER_ALT = re.compile(
    r"(?:image|img|picture|photo|graphic|figure|illustration|cover|logo|unknown|art|inline|icon"
    r"|spacer|ornament|decoration)?[\s\d._-]*", re.I)
_PI_HREF = re.compile(r"href\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_LIST_TAG = re.compile(r"<(/?)(ol|ul|menu|dir|li)\b", re.I)
_MATH_TOKEN = re.compile(r"[\w.]*")

_GREEK = "αβγδεζηθικλμνξοπρστυφχψω"
_ROMAN = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
_BULLETS = {"disc": "•", "circle": "◦", "square": "▪", "none": ""}


class Resources(Protocol):
    """What a document can ask of the book it belongs to."""

    def stylesheet(self, href: str, base: str) -> str | None:
        """CSS text for a `<link>` or `@import`."""

    def image(self, href: str, base: str) -> dict | None:
        """`{"src": absolute path, "w": int, "h": int, "al": 0 or 1}` for a picture, or None.

        `al` says the picture has transparency.
        """

    def document(self, href: str, base: str) -> str | None:
        """The canonical section name a link points into, or None."""


def convert_document(builder: BookBuilder, markup: str, name: str, resources: Resources) -> None:
    """Append the blocks of one (X)HTML document to `builder`.

    `name` is the document's canonical section name. The section is begun here
    when the caller has not already begun it.
    """
    builder.begin_section(name)
    builder.tags_left -= markup.count("<")
    if builder.tags_left < 0:
        raise ReaderError("corrupt", _TOO_LARGE)
    converter = _Converter(builder, name, resources)
    converter.feed(_prepare(markup))
    converter.close()
    converter.finish()


def _prepare(markup: str) -> str:
    """Repair what would make the parser lose the rest of the document."""
    markup = markup.replace("\x00", "")
    markup = _expand_doctype(markup)
    markup = _close_dangling(markup, "<!--", _COMMENT_END, 2)
    markup = _close_dangling(markup, "<![CDATA[", re.compile(r"\]\]>"), 9)
    if _RAW_OPEN.search(markup):
        markup = _RAW_SELF_CLOSED.sub(r"<\1\2></\1>", markup)
        markup = _close_raw_text(markup)
    if _PLAINTEXT.search(markup):
        markup = _PLAINTEXT.sub(r"<\1pre", markup)
    return markup


def _expand_doctype(markup: str) -> str:
    """Drop the DOCTYPE, expanding the simple entities its internal subset declares."""
    found = _DOCTYPE.search(markup, 0, 4096)
    if found is None:
        return markup
    close = markup.find(">", found.end())
    subset = markup.find("[", found.end())
    entities: dict[str, str] = {}
    if 0 <= subset < close:
        end = _SUBSET_END.search(markup, subset)
        if end is not None:
            close = end.end() - 1
            for declared in _ENTITY.finditer(markup, subset, close):
                value = declared.group(2) if declared.group(2) is not None else declared.group(3)
                if len(entities) < 256 and len(value) <= 1024:
                    entities.setdefault(declared.group(1), value)
    if close < 0:
        return markup
    markup = markup[:found.start()] + markup[close + 1:]
    if not entities:
        return markup
    budget = MAX_ENTITY_GROWTH

    def expand(match: re.Match) -> str:
        nonlocal budget
        value = entities[match.group(1)]
        budget -= len(value)
        return value if budget >= 0 else match.group()

    names = "|".join(re.escape(name) for name in sorted(entities, key=len, reverse=True))
    return re.sub("&(%s);" % names, expand, markup)


def _close_dangling(markup: str, opener: str, closer: re.Pattern, overlap: int) -> str:
    """Neutralise a comment or CDATA section that never ends.

    The parser would treat everything after it as part of the comment, which
    loses the rest of the chapter; dropping the opener loses one stray line.
    """
    position = 0
    while True:
        start = markup.find(opener, position)
        if start < 0:
            return markup
        end = closer.search(markup, start + overlap)
        if end is None:
            break
        position = end.end()
    tail = markup[start:]
    if opener == "<!--":
        tail = _DANGLING_COMMENT.sub("", tail)
    else:
        tail = tail.replace(opener, "")
    return markup[:start] + tail


def _close_raw_text(markup: str) -> str:
    """Remove a `<script>` or `<style>` that is never closed, up to the next tag."""
    for name in ("script", "style"):
        opener = re.compile(r"<%s\b[^<>]*>" % name, re.I)
        closer = re.compile(r"</%s\b" % name, re.I)
        position = 0
        while True:
            opened = opener.search(markup, position)
            if opened is None:
                break
            closed = closer.search(markup, opened.end())
            if closed is None:
                # Nothing closes this one, so nothing closes the ones after it either.
                dangling = re.compile(r"<%s\b[^<>]*>[^<]*" % name, re.I)
                start = opened.start()
                markup = markup[:start] + dangling.sub("", markup[start:])
                break
            position = closed.end()
    return markup


def _meaningful(alt: str) -> bool:
    """Whether alternative text says something a reader would miss."""
    return bool(alt) and not _IMAGE_NAME.search(alt) and not _PLACEHOLDER_ALT.fullmatch(alt)


def _marker(kind: str, number: int) -> str:
    if kind in _BULLETS:
        return _BULLETS[kind]
    if number < 1 or kind in ("decimal", "decimal-leading-zero"):
        return ("%02d." if kind == "decimal-leading-zero" else "%d.") % number
    if kind.endswith("roman"):
        if number >= 4000:
            return "%d." % number
        digits = []
        for value, numeral in _ROMAN:
            while number >= value:
                digits.append(numeral)
                number -= value
        text = "".join(digits)
    elif kind == "lower-greek":
        text = _GREEK[(number - 1) % len(_GREEK)]
    else:
        letters = []
        while number > 0:
            number, digit = divmod(number - 1, 26)
            letters.append(chr(ord("a") + digit))
        text = "".join(reversed(letters))
    return (text.upper() if kind.startswith("upper") else text) + "."


def _operand(text: str) -> str:
    return text if _MATH_TOKEN.fullmatch(text) else "(%s)" % text


def _math(tag: str, text: str, parts: list[str]) -> str:
    """How one MathML element reads on a line, given how its children read.

    Only the layouts that say something by position are rewritten: read as
    their tokens in a row, a half would be "12" and a square "x2".
    """
    if tag == "msqrt":
        return "√" + _operand(" ".join(text.split()))
    if len(parts) == 2 and tag in ("mfrac", "msup", "msub"):
        sign = {"mfrac": "/", "msup": "^", "msub": "_"}[tag]
        return _operand(parts[0]) + sign + _operand(parts[1])
    if len(parts) == 2 and tag == "mroot":
        return "%s^(1/%s)" % (_operand(parts[0]), parts[1])
    if len(parts) == 3 and tag == "msubsup":
        return "%s_%s^%s" % tuple(map(_operand, parts))
    return text


def _integer(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return default


class _Frame:
    """The block context text is written in: one per open block-level element."""

    __slots__ = ("style", "heading", "quote", "left", "depth", "list", "item", "caption")

    def __init__(self) -> None:
        self.style = css.ROOT
        self.heading = 0
        self.quote = 0
        self.left = 0.0
        self.depth = 0
        # [list-style counter, step] of the nearest list (step 0: not numbered), and
        # [marker, already used] of the nearest list item.
        self.list: list | None = None
        self.item: list | None = None
        self.caption = False

    def child(self, style: css.Style) -> "_Frame":
        frame = _Frame()
        frame.style = style
        frame.heading = self.heading
        frame.quote = self.quote
        frame.left = self.left
        frame.depth = self.depth
        frame.list = self.list
        frame.item = self.item
        frame.caption = self.caption
        return frame


class _Node(css.Element):
    """An open element."""

    __slots__ = ("kind", "frame", "outer", "closer", "saved", "count", "last")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "css.Element | None",
                 prev: "css.Element | None" = None, first: bool = True) -> None:
        super().__init__(tag, attrs, parent, prev, first)
        self.kind = _INLINE
        self.frame: _Frame | None = None
        self.outer: _Frame | None = None
        self.closer: Callable[["_Node"], None] | None = None
        self.saved: object = None
        self.count = 0
        self.last: css.Element | None = None


class _Table:
    """Collects a table so that it can become a grid, a list or plain flow."""

    def __init__(self, outer: "BookBuilder | _Table") -> None:
        self.outer = outer
        self.loose: list = []
        self.rows: list[tuple[bool, list]] = []
        self.cell: list | None = None
        self.in_head = False

    def anchor(self, fragment: str) -> None:
        (self.loose if self.cell is None else self.cell).append(fragment)

    def add(self, block: dict, *, size: float = 0.0, emphatic: bool = False) -> int:
        (self.loose if self.cell is None else self.cell).append((block, size, emphatic))
        return 0

    def begin_row(self) -> None:
        self.cell = None
        self.rows.append((self.in_head, []))

    def begin_cell(self, header: bool, columns: int, rows: int) -> None:
        if not self.rows:
            self.begin_row()
        self.cell = []
        self.rows[-1][1].append((self.cell, header, min(max(columns, 1), 20),
                                 min(max(rows, 1), 100)))

    def end_cell(self) -> None:
        self.cell = None

    def finish(self) -> None:
        """Hand the table to the outer sink in the best shape it fits."""
        rows = [row for row in self.rows if row[1]]
        grid = self._grid(rows)
        if grid is not None:
            self._replay(self.loose, anchors_only=False)
            for _, cells in rows:
                for cell in cells:
                    self._replay(cell[0], anchors_only=True)
            header = 0
            for in_head, cells in rows:
                if not (in_head or all(cell[1] for cell in cells)):
                    break
                header += 1
            block = {"k": "tbl", "rows": grid}
            if 0 < header < len(grid):
                block["hdr"] = header
            self.outer.add(block)
            return
        self._replay(self.loose, anchors_only=False)
        marked = self._is_marked(rows)
        for _, cells in rows:
            marker = None
            if marked:
                marker = " ".join(plain_text(item[0]["t"], item[0].get("f", 0))
                                  for item in cells[0][0] if not isinstance(item, str))
                self._replay(cells[0][0], anchors_only=True)
                cells = cells[1:]
            for cell in cells:
                for item in cell[0]:
                    if isinstance(item, str):
                        self.outer.anchor(item)
                        continue
                    block, size, emphatic = item
                    if marker is not None and block["k"] == "p":
                        block = dict(block, k="li", m=marker)
                        marker = None
                    self.outer.add(block, size=size, emphatic=emphatic)

    def _replay(self, items: list, anchors_only: bool) -> None:
        for item in items:
            if isinstance(item, str):
                self.outer.anchor(item)
            elif not anchors_only:
                self.outer.add(item[0], size=item[1], emphatic=item[2])

    def _grid(self, rows: list) -> list[list[str]] | None:
        """Rows of cell markup when this is a real table of short cells."""
        grid: list[list[str]] = []
        carried: dict[int, int] = {}
        filled = 0
        for _, cells in rows:
            line: list[str] = []
            for items, _, columns, spanned in cells:
                while carried.get(len(line), 0) > 0:
                    carried[len(line)] -= 1
                    line.append("")
                blocks = [item[0] for item in items if not isinstance(item, str)]
                if len(blocks) > 1:
                    return None
                text = ""
                if blocks:
                    block = blocks[0]
                    if block["k"] not in ("p", "h") or len(block["t"]) > 4 * CELL_LIMIT:
                        return None
                    markup = block.get("f", 0)
                    if len(plain_text(block["t"], markup)) > CELL_LIMIT:
                        return None
                    text = block["t"] if markup else escape(block["t"]).replace("\n", "<br>")
                    filled += 1
                for column in range(columns):
                    if spanned > 1:
                        carried[len(line)] = spanned - 1
                    line.append("" if column else text)
            if len(line) > MAX_COLUMNS:
                return None
            grid.append(line)
        width = max((len(line) for line in grid), default=0)
        if width < 2 or filled < 2 or not any(sum(1 for cell in line if cell) > 1 for line in grid):
            return None
        for line in grid:
            line.extend([""] * (width - len(line)))
        return grid

    def _is_marked(self, rows: list) -> bool:
        """Two columns where the first only numbers the second: really a list."""
        if not rows:
            return False
        for _, cells in rows:
            if len(cells) != 2:
                return False
            blocks = [item[0] for item in cells[0][0] if not isinstance(item, str)]
            if len(blocks) != 1 or blocks[0]["k"] != "p":
                return False
            if len(plain_text(blocks[0]["t"], blocks[0].get("f", 0))) > MARKER_LIMIT:
                return False
            body = [item[0] for item in cells[1][0] if not isinstance(item, str)]
            if not body or body[0]["k"] != "p":
                return False
        return True


class _Converter(HTMLParser):
    # Only these two hold text that must never be parsed as markup; `title`
    # and `textarea` are read as ordinary elements so that a missing end tag
    # cannot swallow the document.
    CDATA_CONTENT_ELEMENTS = ("script", "style")
    RCDATA_CONTENT_ELEMENTS = ()

    def __init__(self, builder: BookBuilder, name: str, resources: Resources) -> None:
        super().__init__(convert_charrefs=True)
        self.builder = builder
        self.name = name
        self.resources = resources
        self.sink: BookBuilder | _Table = builder
        self.table: _Table | None = None
        root = _Node("", {}, None)
        root.kind = _BLOCK
        root.frame = _Frame()
        self.stack: list[_Node] = [root]
        self.frame: _Frame = root.frame
        self.overflow = 0
        self.skip = 0
        self.capture: list[str] | None = None
        self.head: _Node | None = None
        self.seen_html = False
        self.seen_body = False
        self.title: _Node | None = None
        self.sheets: list[css.Sheet] = []
        self.cascade = css.cascade_for(())
        self.restyle = False
        self.inline = Inline()
        self.atoms: list[dict] = []
        self.inked = False
        self.blank = False
        self.space = 0
        self.ruled = False
        self.count = 0
        self.href = ""
        self.quotes = 0
        self.pre: list[str] | None = None
        self.svg: dict | None = None
        self.math: list[str] | None = None
        self.gap = False
        self.resumed = False
        self.markup = ""
        # The last line `_count_items` looked up and where it starts, and how
        # much markup it may still read.
        self.line = (1, 0)
        self.scan = ITEM_SCAN
        self.links: dict[str, str] = {}
        self.images: dict[str, dict | None] = {}

    def feed(self, data: str) -> None:
        self.markup = data
        super().feed(data)

    def finish(self) -> None:
        while len(self.stack) > 1:
            self._pop()
        self._flush(final=True)

    # Parser events.

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._start(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        overflow = self.overflow
        if self._start(tag, attrs):
            self._pop()
        else:
            self.overflow = overflow

    def handle_endtag(self, tag: str) -> None:
        if ":" in tag:
            tag = tag.rpartition(":")[2]
        if self.overflow:
            self.overflow -= 1
            if tag in _BLOCK_TAGS and not self.skip:
                self._flush()
            return
        if tag in ("html", "body"):
            return
        if tag == "br":
            if not self.skip:
                self._break(self.stack[-1].style)
            return
        stack = self.stack
        for index in range(len(stack) - 1, 0, -1):
            found = stack[index].tag
            if found == tag:
                while len(stack) > index:
                    self._pop()
                return
            # A stray end tag inside a table cell must not close what holds the table.
            if stack[index].kind != _SKIP and (
                    found == "table"
                    or (found in ("td", "th", "caption") and tag not in _TABLE_STRUCTURE)):
                return

    def handle_data(self, data: str) -> None:
        if self.capture is not None:
            self.capture.append(data)
        elif not self.skip:
            self._text(data)

    def unknown_decl(self, data: str) -> None:
        if data.startswith("CDATA["):
            self.handle_data(data[6:])

    def handle_pi(self, data: str) -> None:
        if data.startswith("xml-stylesheet"):
            found = _PI_HREF.search(data)
            if found:
                self._load_sheet(found.group(1) or found.group(2) or "")

    # Opening elements.

    def _start(self, tag: str, pairs: list) -> bool:
        """Handle a start tag; True when the element is now on the stack."""
        raw = tag
        if ":" in tag:
            tag = tag.rpartition(":")[2]
        attrs: dict[str, str] = {}
        for name, value in pairs[:MAX_ATTRIBUTES]:
            attrs.setdefault(name, value or "")
        if self.overflow or len(self.stack) >= MAX_DEPTH:
            return self._start_flattened(tag, attrs)
        if tag == "html":
            if self.seen_html:
                return False
            self.seen_html = True
        elif tag == "body":
            if self.seen_body:
                return False
            self.seen_body = True
            while len(self.stack) > (2 if self.seen_html else 1):
                self._pop()
        if self.head is not None and tag not in _HEAD_TAGS:
            while self.head is not None:
                self._pop()
        # A title holds text only, so any tag means its end tag is missing.
        while self.title is not None:
            self._pop()
        if tag == "link":
            relation = attrs.get("rel", "").lower().split()
            if "stylesheet" in relation and "alternate" not in relation \
                    and "css" in attrs.get("type", "css").lower() \
                    and css.media_applies(attrs.get("media", "")):
                self._load_sheet(attrs.get("href", ""))
            return False
        if self.skip:
            return self._start_skipped(tag, attrs)
        self._imply_end(tag)

        container = self.stack[-1]
        node = _Node(tag, attrs, container if len(self.stack) > 1 else None,
                     container.last, container.count == 0)
        container.count += 1
        if self.restyle:
            self._rebuild_cascade()
        style = node.style = self.cascade.style(node)
        void = tag in _VOID
        kinds = attrs.get("epub:type", "")
        # `mbp:frameset` is what holds the entries of a Kindle dictionary.
        hidden = (style.display == "none" or style.hidden
                  or (tag in _DROPPED and (tag != "frameset" or raw == tag))
                  or raw in ("epub:case", "mbp:pagebreak")
                  or attrs.get("role") == "doc-pagebreak"
                  or (kinds and "pagebreak" in kinds.split()))
        if hidden:
            self._anchor(node)
            if tag == "head":
                self.head = node
                node.closer = self._close_head
            elif tag == "style":
                return self._start_style(node)
            elif tag == "title":
                return self._start_title(node)
            elif raw == "mbp:pagebreak":
                self._flush()
                self.space = 2
            if void:
                self._closed(container, node)
            return self._push_skipped(node, void)
        if void:
            self._void(node)
            self._closed(container, node)
            return False
        display = style.display
        block = tag in _STRUCTURAL or (tag in _BLOCK_TAGS and display != "inline")
        if block:
            self._flush()
        self._anchor(node)
        if tag == "svg":
            self.svg = {"images": [], "text": [], "label": attrs.get("aria-label", "")}
            node.closer = self._close_svg
            return self._push_skipped(node, False)
        if tag == "math":
            node.saved = (self.capture, self.math, attrs.get("alttext", ""), style)
            self.capture, self.math = [], []
            node.closer = self._close_math
            return self._push_skipped(node, False)
        if tag == "rt":
            node.saved = self.capture
            self.capture = []
            node.closer = self._close_annotation
            return self._push_skipped(node, False)
        if tag == "object":
            image = self._resolve_image(attrs.get("data", ""))
            if image is not None:
                self._picture(image, "")
                return self._push_skipped(node, False)
        if block:
            self._open_block(node, style)
        else:
            if display == "block" and not style.floated:
                node.kind = _LINE
                self.inline.line_break(soft=True)
                self.inline.indent(min(16, round((style.left + style.indent) * 2)))
            self._open_inline(node, style)
        self.stack.append(node)
        return True

    def _start_flattened(self, tag: str, attrs: dict[str, str]) -> bool:
        """An element beyond the depth cap: its content joins the deepest open element."""
        if not self.skip:
            if "id" in attrs:
                self.sink.anchor(attrs["id"])
            if tag == "br":
                self._break(self.stack[-1].style)
            elif tag in _BLOCK_TAGS:
                self._flush()
        if tag not in _VOID:
            self.overflow += 1
        return False

    def _start_skipped(self, tag: str, attrs: dict[str, str]) -> bool:
        """A start tag inside content that is not shown."""
        node = _Node(tag, attrs, None)
        self._anchor(node)
        if tag == "style":
            return self._start_style(node)
        if tag == "title" and self.head is not None:
            return self._start_title(node)
        svg = self.svg
        if svg is not None:
            if tag == "image":
                svg["images"].append(attrs.get("xlink:href") or attrs.get("href") or "")
            elif tag == "text":
                node.saved = self.capture
                svg["text"].append(" ")
                self.capture = svg["text"]
                node.closer = self._restore_capture
        elif tag in ("annotation", "annotation-xml"):
            node.saved = (self.capture, self.math)
            self.capture, self.math = [], None
            node.closer = self._restore_math
        elif self.math is not None:
            node.saved = (self.capture, self.math)
            self.capture, self.math = [], []
            node.closer = self._close_math_part
        return self._push_skipped(node, tag in _VOID)

    def _push_skipped(self, node: _Node, void: bool) -> bool:
        if void:
            return False
        node.kind = _SKIP
        self.skip += 1
        self.stack.append(node)
        return True

    def _start_style(self, node: _Node) -> bool:
        kind = node.attrs.get("type", "").lower()
        node.saved = self.capture
        self.capture = []
        if (not kind or "css" in kind) and css.media_applies(node.attrs.get("media", "")):
            node.closer = self._close_style
        else:
            node.closer = self._restore_capture
        return self._push_skipped(node, False)

    def _start_title(self, node: _Node) -> bool:
        if self.seen_body or self.svg is not None:
            return self._push_skipped(node, False)
        self.title = node
        node.saved = self.capture
        self.capture = []
        node.closer = self._close_title
        return self._push_skipped(node, False)

    def _imply_end(self, tag: str) -> None:
        """Close the elements HTML closes implicitly when `tag` starts."""
        if tag in _CLOSES_P:
            self._close_open(("p",), _P_SCOPE)
        if tag == "li":
            self._close_open(("li",), _ITEM_SCOPE)
        elif tag in ("dt", "dd"):
            self._close_open(("dt", "dd"), _ITEM_SCOPE)
        elif tag == "tr":
            self._close_open(("tr",), ("table",))
        elif tag in ("td", "th"):
            self._close_open(("td", "th"), ("tr", "table"))
        elif tag in _TABLE_SECTIONS:
            stack = self.stack
            for index in range(len(stack) - 1, 0, -1):
                if stack[index].tag == "table":
                    while len(stack) > index + 1:
                        self._pop()
                    break
        elif tag in _HEADINGS:
            if self.stack[-1].tag in _HEADINGS:
                self._pop()
        elif tag == "a":
            self._close_open(("a",), _BLOCK_TAGS)

    def _close_open(self, tags: tuple[str, ...], scope: frozenset | tuple) -> None:
        stack = self.stack
        for index in range(len(stack) - 1, 0, -1):
            found = stack[index].tag
            if found in tags:
                while len(stack) > index:
                    self._pop()
                return
            if found in scope:
                return

    def _open_block(self, node: _Node, style: css.Style) -> None:
        tag = node.tag
        node.kind = _BLOCK
        if style.break_before:
            self.space = 2
        elif style.above >= 1.5:
            self.space = max(self.space, 1)
        frame = self.frame.child(style)
        node.outer = self.frame
        node.frame = self.frame = frame
        if tag not in _MARGINLESS:
            frame.left += style.left
        if tag in _HEADINGS:
            frame.heading = _HEADINGS[tag]
        elif tag in _LISTS:
            frame.depth += 1
            frame.item = None
            start, step = 1, 1
            if tag == "ol":
                start = _integer(node.attrs.get("start"), 1)
                if "reversed" in node.attrs:
                    step = -1
                    if "start" not in node.attrs:
                        start = self._count_items()
                        step = -1 if start else 0
            frame.list = [start, step]
        elif tag == "li":
            counter = frame.list
            number = 1
            if counter is not None:
                counter[0] = number = _integer(node.attrs.get("value"), counter[0])
                counter[0] += counter[1]
            kind = style.list_type
            if kind == "none" and style.marker:
                # The publisher draws its own numbers or bullets (`li::before`).
                kind = style.marker
            numbered = counter is None or counter[1] or kind in _BULLETS
            frame.item = [_marker(kind, number) if numbered else "", False]
        elif tag == "blockquote":
            frame.quote += 1
        elif tag in ("figcaption", "caption"):
            frame.caption = True
        elif tag in ("pre", "listing", "xmp"):
            if self.pre is None:
                self.pre = []
                node.closer = self._close_pre
        elif tag == "table":
            node.saved = (self.sink, self.table)
            self.sink = self.table = _Table(self.sink)
            node.closer = self._close_table
        elif self.table is not None:
            if tag == "thead":
                self.table.in_head = True
                node.closer = self._close_table_head
            elif tag == "tr":
                self.table.begin_row()
            elif tag in ("td", "th"):
                self.table.begin_cell(tag == "th", _integer(node.attrs.get("colspan"), 1),
                                      _integer(node.attrs.get("rowspan"), 1))
                node.closer = self._close_cell
        if self.pre is not None and node.closer is None and self.pre:
            self.pre.append("\n")

    def _open_inline(self, node: _Node, style: css.Style) -> None:
        tag = node.tag
        if tag == "a":
            name = node.attrs.get("name")
            if name:
                self.sink.anchor(name)
            href = node.attrs.get("href")
            if href is not None:
                node.saved = self.href
                node.closer = self._close_link
                self.href = self._resolve_link(href)
        elif tag == "q":
            self.quotes += 1
            self._text("“" if self.quotes % 2 else "‘", style)
            node.closer = self._close_quote

    def _void(self, node: _Node) -> None:
        tag = node.tag
        if tag == "hr":
            self._flush()
            self._anchor(node)
            self._emit({"k": "hr"})
        elif tag == "img":
            alt = plain_text(clean(node.attrs.get("alt", "")))
            image = self._resolve_image(node.attrs.get("src", ""))
            if image is not None:
                self._picture(image, alt, node.id)
            else:
                self._anchor(node)
                if _meaningful(alt):
                    self._say(alt, node.style)
        else:
            self._anchor(node)
            if tag == "br":
                self._break(node.style)

    @staticmethod
    def _closed(container: _Node, node: _Node) -> None:
        """Record `node` as the latest finished child of `container`."""
        container.last = node
        # Keep at most two earlier siblings reachable, enough for `a + b + c`.
        node.last = None
        if node.prev is not None:
            node.prev.prev = None

    # Closing elements.

    def _pop(self) -> None:
        node = self.stack.pop()
        kind = node.kind
        if kind == _SKIP:
            self.skip -= 1
        elif kind == _BLOCK:
            self._flush()
            self.frame = node.outer
            style = node.style
            if style.break_after:
                self.space = 2
            elif style.below >= 1.5:
                self.space = max(self.space, 1)
        elif kind == _LINE:
            self.inline.line_break(soft=True)
        if node.closer is not None:
            node.closer(node)
        self._closed(self.stack[-1], node)

    def _close_head(self, node: _Node) -> None:
        self.head = None

    def _restore_capture(self, node: _Node) -> None:
        self.capture = node.saved

    def _restore_math(self, node: _Node) -> None:
        self.capture, self.math = node.saved

    def _close_math_part(self, node: _Node) -> None:
        text = _math(node.tag, "".join(self.capture or ()), self.math or [])
        self.capture, self.math = node.saved
        self.capture.append(text)
        self.math.append(" ".join(text.split()))

    def _close_style(self, node: _Node) -> None:
        text = "".join(self.capture or ())
        self.capture = node.saved
        if text.strip():
            css.collect_sheets(text, self.name, self.resources.stylesheet, self.sheets)
            self.restyle = True

    def _close_title(self, node: _Node) -> None:
        self.builder.section_title("".join(self.capture or ()))
        self.capture = node.saved
        self.title = None

    def _close_svg(self, node: _Node) -> None:
        svg, self.svg = self.svg, None
        images = [image for image in map(self._resolve_image, svg["images"][:16]) if image]
        if images:
            label = plain_text(clean(svg["label"]))
            for image in images:
                self._flush()
                self._emit(dict(image, k="img", alt=label))
            return
        text = svg["label"] or "".join(svg["text"])
        if text.strip():
            self._text(text, node.style)

    def _close_math(self, node: _Node) -> None:
        outer, parts, label, style = node.saved
        text = label or " ".join("".join(self.capture or ()).split())
        self.capture, self.math = outer, parts
        if self.skip:
            return
        image = None if label else self._resolve_image(node.attrs.get("altimg", ""))
        if image is not None:
            self._picture(image, plain_text(clean(text)))
        elif text.strip():
            block = node.attrs.get("display") == "block"
            if block:
                self.inline.line_break(soft=True)
            self._text(text, style)
            if block:
                self.inline.line_break(soft=True)

    def _close_annotation(self, node: _Node) -> None:
        text = " ".join("".join(self.capture or ()).split())
        self.capture = node.saved
        if text and not self.skip:
            self._text("(%s)" % text, node.style)

    def _close_pre(self, node: _Node) -> None:
        text = clean("".join(self.pre or ())).replace("\r\n", "\n").replace("\r", "\n")
        self.pre = None
        if text.startswith("\n"):
            text = text[1:]
        text = "\n".join(line.rstrip() for line in text.expandtabs(4).split("\n")).rstrip()
        if text.strip():
            self._emit({"k": "pre", "t": text})

    def _close_table(self, node: _Node) -> None:
        table = self.table
        self.sink, self.table = node.saved
        if table is not None:
            table.finish()

    def _close_table_head(self, node: _Node) -> None:
        if self.table is not None:
            self.table.in_head = False

    def _close_cell(self, node: _Node) -> None:
        if self.table is not None:
            self.table.end_cell()

    def _close_link(self, node: _Node) -> None:
        self.href = node.saved

    def _close_quote(self, node: _Node) -> None:
        self._text("”" if self.quotes % 2 else "’", node.style)
        self.quotes -= 1

    # Content.

    def _text(self, data: str, style: css.Style | None = None) -> None:
        if self.pre is not None:
            self.pre.append(data)
            return
        if style is None:
            style = self.stack[-1].style
        if self.gap:
            self.gap = False
            if data[:1].isalnum():
                self.inline.text(" ")
        if style.caps or style.transform:
            data = self._transform(data, style)
        flags = style.flags
        if self.frame.heading:
            # Headings are bold by nature; saying so again is only noise.
            flags &= ~BOLD
        if style.white_space == "normal":
            self.inline.text(data, flags, self.href, style.size)
        else:
            self.inline.preformatted(data, flags, self.href, style.size,
                                     keep_spaces=style.white_space == "pre")
        if not self.inked and data.replace("\xad", "").strip():
            self.inked = True

    def _say(self, alt: str, style: css.Style | None = None) -> None:
        """Write alternative text where its picture stood, parted from the words beside it."""
        words = len(alt) > 1
        if words and self.inline.last_char.isalnum():
            self.inline.text(" ")
        self._text(alt, style)
        self.gap = words

    def _transform(self, data: str, style: css.Style) -> str:
        if style.caps or style.transform == "upper":
            return data.upper()
        if style.transform == "lower":
            return data.lower()
        previous = self.inline.last_char
        # A sentinel stands in for the text before this piece, so a word that
        # continues across an inline boundary is not capitalised in the middle.
        sentinel = "x" if previous.isalnum() or previous in ("'", "’", "\xad") else " "
        return _WORD_START.sub(lambda m: m.group().upper(), sentinel + data)[1:]

    def _break(self, style: css.Style) -> None:
        if style.display == "none" or style.hidden:
            return
        if self.pre is not None:
            self.pre.append("\n")
            return
        inline = self.inline
        if not inline:
            self.blank = True
        elif inline.ends_with_break and style.white_space == "normal":
            # Two line breaks in a row are how many books write a paragraph break.
            inline.drop_break()
            self._flush()
        else:
            inline.line_break()

    def _anchor(self, node: _Node) -> None:
        if node.id:
            self.sink.anchor(node.id)

    def _resolve_link(self, href: str) -> str:
        """The link target to give the text: a placeholder, an external URL, or nothing."""
        href = _URL_BREAKS.sub("", href.strip())
        known = self.links.get(href)
        if known is not None:
            return known
        resolved = ""
        if href.startswith("#"):
            if len(href) > 1:
                resolved = self.builder.link(self.name, unquote(href[1:]))
        elif _SCHEME.match(href):
            if href.lower().startswith(("http:", "https:", "mailto:")) and len(href) <= MAX_HREF:
                resolved = clean(href).replace("\xad", "")
                if not resolved.partition(":")[2].strip("/ "):
                    resolved = ""
        elif href:
            path, _, fragment = href.partition("#")
            target = self.resources.document(path, self.name)
            if target is not None:
                resolved = self.builder.link(target, unquote(fragment))
        if len(self.links) < 50000:
            self.links[href] = resolved
        return resolved

    def _resolve_image(self, href: str) -> dict | None:
        href = href.strip()
        if not href:
            return None
        if href in self.images:
            return self.images[href]
        found = self.resources.image(href, self.name)
        image = None
        if found and found.get("src"):
            image = {"src": found["src"], "w": int(found.get("w") or 0),
                     "h": int(found.get("h") or 0)}
            if found.get("al"):
                image["al"] = 1
        if len(self.images) < 10000:
            self.images[href] = image
        return image

    def _picture(self, image: dict, alt: str, fragment: str = "") -> None:
        said = _meaningful(alt)
        block = dict(image, k="img", alt=alt if said else "")
        glyph = 0 < image["w"] <= SMALL_IMAGE and 0 < image["h"] <= SMALL_IMAGE
        initial = said and len(alt) == 1 and alt.isalpha() and self.inline.bare
        if glyph or initial:
            # A glyph-sized picture inside a sentence cannot be drawn inline,
            # and a drop cap of any size is the first letter of its word: each
            # is kept only when it turns out to stand alone in its block.
            if fragment:
                self.sink.anchor(fragment)
            self.atoms.append(block)
            if said:
                inked = self.inked
                self._say(alt)
                self.inked = inked
            return
        resumed = self.inked or self.resumed
        self._flush()
        if fragment:
            self.sink.anchor(fragment)
        self._emit(block)
        # What follows in the same block carries on the sentence the picture cut.
        self.resumed = resumed

    def _count_items(self) -> int:
        """How many items the list whose start tag is being read has; 0 when unknown.

        A list that counts down starts from its length, which a stream of
        parser events cannot know: the markup ahead is searched for it.
        """
        line, column = self.getpos()
        known, position = self.line
        for _ in range(line - known):
            position = self.markup.find("\n", position) + 1
        self.line = (line, position)
        position += column + 1
        end = min(len(self.markup), position + max(0, self.scan))
        count = depth = 0
        for found in _LIST_TAG.finditer(self.markup, position, end):
            closing, name = found.group(1), found.group(2).lower()
            if name == "li":
                if not closing and not depth:
                    count += 1
            elif not closing:
                depth += 1
            elif depth:
                depth -= 1
            else:
                end = found.end()
                break
        else:
            if end < len(self.markup):
                count = 0
        self.scan -= end - position
        return count

    def _load_sheet(self, href: str) -> None:
        if not href or len(self.sheets) >= css.MAX_SHEETS:
            return
        text = self.resources.stylesheet(href, self.name)
        if not text:
            return
        path = href.partition("#")[0].partition("?")[0]
        location = posixpath.normpath(posixpath.join(posixpath.dirname(self.name), path))
        css.collect_sheets(text, location, self.resources.stylesheet, self.sheets)
        self.restyle = True

    def _rebuild_cascade(self) -> None:
        """New sheets arrived: restyle what is open so descendants inherit correctly."""
        self.restyle = False
        self.cascade = css.cascade_for(tuple(self.sheets))
        for node in self.stack[1:]:
            if node.kind != _SKIP:
                node.style = self.cascade.style(node)
                if node.frame is not None:
                    node.frame.style = node.style

    def _flush(self, final: bool = False) -> None:
        """Emit the text written so far as one block."""
        inline = self.inline
        atoms = self.atoms
        resumed, self.resumed = self.resumed, False
        if not inline and not atoms:
            if self.blank:
                # An empty paragraph holding only a line break is a spacer.
                self.space = max(self.space, 1)
                self.blank = False
            return
        if self.count >= MAX_BLOCKS and inline.length < DEGRADED_BLOCK and not final:
            inline.line_break(soft=True)
            return
        inked = self.inked
        self.inline = Inline()
        self.atoms = []
        self.inked = self.blank = self.gap = False
        if atoms and not inked:
            for image in atoms:
                self._emit(image)
            return
        if not inline.visible:
            self.space = max(self.space, 1)
            return
        text, markup = inline.render()
        frame = self.frame
        block: dict = {"k": "p", "t": text}
        if markup:
            block["f"] = 1
        if frame.heading:
            block["k"] = "h"
            block["l"] = frame.heading
        elif frame.item is not None:
            block["k"] = "li"
            block["m"] = "" if frame.item[1] else frame.item[0]
            frame.item[1] = True
        style = frame.style
        if frame.caption and not frame.heading:
            block["a"] = "c"
            block["z"] = -1
        elif style.align:
            block["a"] = style.align
        else:
            left = frame.left
            if style.indent < 0 or style.indent >= 3:
                left += style.indent
            level = (0 if left < 1.5 else 1 + int((left - 1.5) // 2.5)) + max(0, frame.depth - 1)
            if left >= FAR_RIGHT and inline.length <= 120:
                # A short line pushed most of the way across (a signature,
                # an attribution) reads as right-aligned.
                block["a"] = "r"
            elif level:
                block["i"] = min(6, level)
        if frame.quote:
            block["q"] = 1
        if resumed:
            block["c"] = 1
        self._emit(block, inline.dominant_size(), inline.bold)

    def _emit(self, block: dict, size: float = 0.0, emphatic: bool = False) -> None:
        kind = block["k"]
        if kind == "hr":
            if self.ruled:
                return
        elif self.space and kind in TEXT_KINDS:
            block["s"] = self.space
        self.space = 0
        self.ruled = kind == "hr"
        self.count += 1
        self.sink.add(block, size=size, emphatic=emphatic)
