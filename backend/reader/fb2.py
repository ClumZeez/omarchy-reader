"""FictionBook 2: one XML file holding the text, its notes and its pictures.

The book is rewritten as plain HTML, one document per top-level section, and
converted by html.py, so every rule about inline markup lives there.
"""

from __future__ import annotations

import re
from html import escape
from xml.etree.ElementTree import Element

from . import html, toc
from .archive import Archive
from .blocks import BookBuilder
from .book import Book, Meta
from .encoding import decode_text
from .errors import ReaderError
from .library import display_author, title_from_filename
from .pictures import Pictures, decode_base64
from .textutil import collapse_space, declared_encoding
from .xmlutil import attr, children, descendants, local, parse_xml, text_of

_DAMAGED = "This book is damaged and can't be opened."
_MISSING = "That book is no longer there."
_UNREADABLE = "This book's file can't be read."
_TOO_BIG = "This book is too large to open."

MAX_FILE = 256 * 1024 * 1024
MAX_DEPTH = 48
MAX_SECTIONS = 20000

_ZIP = b"PK\x03\x04"
_DESCRIPTION_END = re.compile(r"</(?:[\w.-]+:)?description\s*>", re.I)
_BINARY = re.compile(r"<(?:[\w.-]+:)?binary\b([^>]*)>([^<]*)", re.I)
_BINARY_ID = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_BREAK = re.compile(r"(?:[*·•]\s*){1,5}|[—–-]{1,3}")
_NOTES = frozenset(("notes", "comments", "footnotes"))
_EXTERNAL = ("http:", "https:", "mailto:")

_INLINE = {
    "strong": "b", "b": "b", "emphasis": "i", "i": "i", "em": "i", "strikethrough": "s",
    "s": "s", "strike": "s", "del": "s", "u": "u", "sub": "sub", "sup": "sup", "code": "code",
    "tt": "code",
}
_PARAGRAPHS = frozenset(("p", "v", "subtitle", "text-author", "date", "td", "th"))
_CENTRED = ' style="text-align:center;font-weight:bold"'
_RIGHT = ' style="text-align:right"'
_GAP = ' style="margin-top:2em"'


def _tag(el: Element) -> str:
    return local(el.tag).lower()


def _hidden(name: str) -> bool:
    return any(part.startswith(".") or part == "__MACOSX" for part in name.split("/"))


def _source(path: str) -> str:
    """The book's XML as text, taken out of its zip when it is in one."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_FILE + 1)
    except FileNotFoundError:
        raise ReaderError("missing", _MISSING) from None
    except OSError:
        raise ReaderError("corrupt", _UNREADABLE) from None
    if raw.startswith(_ZIP):
        with Archive.open(path) as archive:
            names = [name for name in archive.names() if not _hidden(name)]
            if not names:
                raise ReaderError("corrupt", _DAMAGED)
            # An .fbz often holds a single member with no extension at all.
            name = next((name for name in names if name.lower().endswith(".fb2")),
                        max(names, key=lambda name: archive.member(name).size))
            raw = archive.read(name, MAX_FILE)
    elif len(raw) > MAX_FILE:
        raise ReaderError("corrupt", _TOO_BIG)
    return decode_text(raw, declared=declared_encoding(raw))


class _Description:
    """Title, authors and language, and which picture is the cover."""

    def __init__(self, root: Element, path: str) -> None:
        infos = descendants(root, "title-info") or descendants(root, "src-title-info")
        info = infos[0] if infos else root
        titles = [text_of(el) for el in descendants(info, "book-title")
                  + descendants(root, "book-name")]
        self.title = next((title for title in titles if title), title_from_filename(path))
        self.authors: list[str] = []
        for author in children(info, "author"):
            parts = [text_of(part) for name in ("first-name", "middle-name", "last-name")
                     for part in children(author, name)]
            name = " ".join(part for part in parts if part) or " ".join(
                text_of(part) for part in children(author, "nickname"))
            if name and name not in self.authors:
                self.authors.append(name)
        languages = children(info, "lang")
        self.language = text_of(languages[0]).lower().replace("_", "-") if languages else ""
        covers = [image for page in descendants(info, "coverpage")
                  for image in descendants(page, "image")]
        self.cover = attr(covers[0], "href").strip().lstrip("#") if covers else ""


def read_meta(path: str) -> Meta:
    """Title, authors, language and cover, read without touching the text of the book."""
    text = _source(path)
    end = _DESCRIPTION_END.search(text)
    described = _Description(parse_xml(text[:end.end()] if end else text), path)
    cover = None
    wanted = described.cover.casefold()
    if wanted:
        for found in _BINARY.finditer(text, end.end() if end else 0):
            name = _BINARY_ID.search(found.group(1))
            if name and (name.group(1) or name.group(2) or "").casefold() == wanted:
                cover = decode_base64(found.group(2)) or None
                break
    return Meta(described.title, described.authors, described.language, cover)


class _Resources:
    """Pictures come from the book's binaries; links were resolved while writing the HTML."""

    def __init__(self, root: Element, out_dir: str) -> None:
        self.pictures = Pictures(out_dir)
        self.binaries: dict[str, Element] = {}
        self.folded: dict[str, str] = {}
        self.stored: dict[str, dict | None] = {}
        self.names: set[str] = set()
        for binary in descendants(root, "binary"):
            name = binary.get("id", "")
            if name:
                self.binaries.setdefault(name, binary)
                self.folded.setdefault(name.casefold(), name)

    def stylesheet(self, href: str, base: str) -> str | None:
        return None

    def image(self, href: str, base: str) -> dict | None:
        name = href.lstrip("#")
        name = name if name in self.binaries else self.folded.get(name.casefold())
        if name is None:
            return None
        if name not in self.stored:
            # What the binary says it is does not matter: the bytes are sniffed.
            self.stored[name] = self.pictures.store(decode_base64(self.binaries[name].text or ""))
        return self.stored[name]

    def document(self, href: str, base: str) -> str | None:
        return href if href in self.names else None


class _Writer:
    """Rewrites the bodies of a FictionBook as HTML documents, one per section."""

    def __init__(self, root: Element, cover: str) -> None:
        self.documents: list[tuple[str, list[str]]] = []
        self.contents: list[dict] = []
        self.out: list[str] = []
        self.name = ""
        self.targets: dict[str, tuple[str, str]] = {}
        self.folded: dict[str, tuple[str, str]] = {}
        self.anchors = 0
        self.gap = False
        bodies = children(root, "body") or descendants(root, "body")
        notes = [body for body in bodies[1:] if body.get("name", "").strip().lower() in _NOTES]
        self.units = [unit for body in bodies if body not in notes for unit in self._units(body)]
        self.units += [[body] for body in notes]
        for number, unit in enumerate(self.units):
            for el in unit:
                for inner in el.iter():
                    self._register(inner, "s%04d" % number)
        for number, unit in enumerate(self.units):
            self._begin("s%04d" % number)
            if not number and cover and not (unit and _tag(unit[0]) == "image"):
                self.out.append('<div><img src="%s" alt=""/></div>' % escape(cover))
            if unit and _tag(unit[0]) == "body":
                self._notes(unit[0])
            else:
                self._blocks(unit, 0, 0, 0)

    def _units(self, body: Element) -> list[list[Element]]:
        """A body cut into documents: what precedes its sections, then each section.

        A section that is nothing but a title shares a document with the
        one after it: "Chapter I" and "The Sergeant" are one chapter's heading.
        """
        units: list[list[Element]] = [[]]
        joined = False
        for child in body:
            if _tag(child) == "section" and not joined and len(units) < MAX_SECTIONS:
                units.append([])
            units[-1].append(child)
            joined = _tag(child) == "section" and _title_only(child)
        return [unit for unit in units if unit]

    def _register(self, el: Element, name: str) -> None:
        identifier = el.get("id", "")
        if identifier and identifier not in self.targets:
            self.anchors += 1
            self.targets[identifier] = (name, "a%d" % self.anchors)
            self.folded.setdefault(identifier.casefold(), self.targets[identifier])

    def _begin(self, name: str) -> None:
        self.name = name
        self.out = []
        self.documents.append((name, self.out))

    def _anchor(self, el: Element) -> str:
        target = self.targets.get(el.get("id", ""))
        return '<a id="%s"></a>' % target[1] if target and target[0] == self.name else ""

    def _open(self, tag: str, el: Element | None = None, style: str = "") -> str:
        """An opening tag, carrying the gap an empty line before it asked for."""
        if self.gap:
            self.gap = False
            style = style[:-1] + ";margin-top:2em\"" if style else _GAP
        return (self._anchor(el) if el is not None else "") + "<%s%s>" % (tag, style)

    def _blocks(self, elements: list[Element] | Element, depth: int, titled: int,
                nesting: int) -> None:
        """Write block-level elements. `depth` counts enclosing sections and
        `titled` those among them that have a title."""
        previous: dict | None = None
        for el in elements:
            tag = _tag(el)
            if nesting > MAX_DEPTH:
                self._paragraph(el, text_only=True)
            elif tag == "section":
                previous = self._section(el, depth + 1, titled, nesting, previous)
                continue
            elif tag == "title":
                self._title(el, "h1")
            elif tag in ("p", "v"):
                self._paragraph(el)
            elif tag == "subtitle":
                if _BREAK.fullmatch(text_of(el)):
                    self._rule(el)
                else:
                    self._paragraph(el, _CENTRED)
            elif tag == "empty-line":
                self.gap = True
            elif tag in ("text-author", "date"):
                self._paragraph(el, _RIGHT)
            elif tag == "epigraph":
                self._container(el, "div", ' style="margin-left:2em;font-style:italic"',
                                depth, titled, nesting)
            elif tag == "cite":
                self._container(el, "blockquote", "", depth, titled, nesting)
            elif tag == "annotation":
                self._container(el, "div", ' style="font-size:0.85em"', depth, titled, nesting)
            elif tag == "poem":
                self._poem(el, nesting)
            elif tag == "stanza":
                self._stanza(el)
            elif tag == "image":
                self.gap = False
                self.out.append("<div>%s</div>" % self._image(el))
            elif tag == "table":
                self._table(el)
            elif tag in _INLINE or tag in ("a", "style", "span", "br"):
                self.out.append("<p>%s</p>" % self._inline_element(el, 0))
            elif any(_tag(child) in _PARAGRAPHS or _tag(child) == "section" for child in el):
                self._container(el, "div", "", depth, titled, nesting)
            else:
                self._paragraph(el)
            previous = None
            if el.tail and el.tail.strip():
                self.out.append("<p>%s</p>" % escape(el.tail))

    def _container(self, el: Element, tag: str, style: str, depth: int, titled: int,
                   nesting: int) -> None:
        self.out.append(self._open(tag, el, style))
        if el.text and el.text.strip():
            self.out.append("<p>%s</p>" % escape(el.text))
        self._blocks(el, depth, titled, nesting + 1)
        self.out.append("</%s>" % tag)

    def _section(self, el: Element, depth: int, titled: int, nesting: int,
                 previous: dict | None) -> dict | None:
        """Write a section; the contents entry it made when it was only a title."""
        self.out.append(self._anchor(el))
        titles = children(el, "title")
        label = _label(titles[0]) if titles else ""
        named = any(map(str.isalnum, label))
        entry = None
        if named:
            self.anchors += 1
            fragment = "a%d" % self.anchors
            self.out.append('<a id="%s"></a>' % fragment)
            if previous is not None:
                separator = " " if previous["t"].endswith((".", ":", "!", "?")) else ". "
                previous["t"] += separator + label
            else:
                entry = {"t": label, "d": titled, "name": self.name, "fragment": fragment}
                self.contents.append(entry)
            titled += 1
        if el.text and el.text.strip():
            self.out.append("<p>%s</p>" % escape(el.text))
        for child in el:
            if child in titles:
                if named:
                    self._title(child, "h%d" % min(depth + 1, 6))
                elif label:
                    self._rule(child)
                if child.tail and child.tail.strip():
                    self.out.append("<p>%s</p>" % escape(child.tail))
            else:
                self._blocks([child], depth, titled, nesting + 1)
        return entry if entry is not None and _title_only(el) else None

    def _rule(self, el: Element) -> None:
        """A scene break: a subtitle or a title that is only asterisks or dashes."""
        self.gap = False
        self.out.append(self._anchor(el) + "<hr/>")

    def _title(self, el: Element, tag: str, style: str = "") -> None:
        lines = [self._inline(line, 0) for line in children(el, "p")] or [self._inline(el, 0)]
        lines = [line for line in lines if line.strip()]
        if lines:
            self.out.append("%s%s</%s>" % (self._open(tag, el, style), "<br/>".join(lines), tag))

    def _paragraph(self, el: Element, style: str = "", text_only: bool = False) -> None:
        content = escape(text_of(el)) if text_only else self._inline(el, 0)
        if content.strip():
            self.out.append("%s%s</p>" % (self._open("p", el, style), content))

    def _poem(self, el: Element, nesting: int) -> None:
        self.out.append(self._open("div", el, ' style="margin-left:2em"'))
        for child in el:
            if _tag(child) == "title":
                self._title(child, "p", _CENTRED)
            else:
                self._blocks([child], 0, 0, nesting + 1)
        self.out.append("</div>")

    def _stanza(self, el: Element) -> None:
        """One paragraph for the whole stanza, a line break between its verses."""
        verses: list[str] = []
        self.gap = True

        def flush() -> None:
            if verses:
                self.out.append("%s%s</p>" % (self._open("p", el), "<br/>".join(verses)))
                verses.clear()

        for child in el:
            tag = _tag(child)
            if tag == "v":
                verses.append(self._anchor(child) + self._inline(child, 0))
            elif tag == "title":
                flush()
                self._title(child, "p", _CENTRED)
            else:
                flush()
                self._blocks([child], 0, 0, MAX_DEPTH)
        flush()
        self.gap = False

    def _table(self, el: Element) -> None:
        self.out.append(self._anchor(el) + "<table>")
        for row in descendants(el, "tr"):
            self.out.append("<tr>")
            for cell in row:
                tag = "th" if _tag(cell) == "th" else "td"
                spans = "".join(' %s="%s"' % (name, cell.get(name))
                                for name in ("colspan", "rowspan") if cell.get(name, "").isdigit())
                self.out.append("<%s%s>%s</%s>" % (tag, spans, self._inline(cell, 0), tag))
            self.out.append("</tr>")
        self.out.append("</table>")

    def _image(self, el: Element) -> str:
        alt = el.get("alt") or el.get("title") or ""
        return '%s<img src="%s" alt="%s"/>' % (
            self._anchor(el), escape(attr(el, "href").strip().lstrip("#")), escape(alt))

    def _inline(self, el: Element, nesting: int) -> str:
        """The text of an element with its inline children as HTML."""
        parts = [escape(el.text or "")]
        for child in el:
            parts.append(self._inline_element(child, nesting + 1))
            parts.append(escape(child.tail or ""))
        return "".join(parts)

    def _inline_element(self, el: Element, nesting: int) -> str:
        tag = _tag(el)
        if nesting > MAX_DEPTH:
            return escape(text_of(el))
        if tag == "image":
            return self._image(el)
        if tag == "br":
            return "<br/>"
        if tag == "empty-line":
            return " "
        content = self._anchor(el) + self._inline(el, nesting)
        if tag == "a":
            href = self._href(attr(el, "href").strip())
            return '<a href="%s">%s</a>' % (escape(href), content) if href else content
        if tag in _INLINE:
            return "<%s>%s</%s>" % (_INLINE[tag], content, _INLINE[tag])
        # A paragraph where only text belongs: keep its words apart from the rest.
        return " %s " % content if tag in _PARAGRAPHS else content

    def _href(self, href: str) -> str:
        if href.startswith("#"):
            target = self.targets.get(href[1:]) or self.folded.get(href[1:].casefold())
            return "%s#%s" % target if target else ""
        return href if href.lower().startswith(_EXTERNAL) else ""

    def _notes(self, body: Element) -> None:
        """A body of notes: its title as a chapter heading, each note under its number."""
        titles = children(body, "title")
        label = _label(titles[0]) if titles else ""
        self.anchors += 1
        fragment = "a%d" % self.anchors
        self.contents.append({"t": label or "Notes", "d": 0, "name": self.name,
                              "fragment": fragment})
        self.out.append('%s<a id="%s"></a>' % (self._anchor(body), fragment))
        if titles:
            self._title(titles[0], "h2")
        else:
            self.out.append("<h2>Notes</h2>")
        for child in body:
            if child in titles:
                continue
            if _tag(child) != "section":
                self._blocks([child], 1, 1, 1)
                continue
            self.out.append(self._anchor(child))
            for inner in child:
                if _tag(inner) == "title":
                    self._title(inner, "p", ' style="font-weight:bold"')
                else:
                    self._blocks([inner], 2, 2, 2)


def _label(title: Element) -> str:
    """A title as one line: its paragraphs joined by spaces."""
    return collapse_space(" ".join(text_of(line) for line in children(title, "p"))
                          or text_of(title))


def _title_only(section: Element) -> bool:
    return (not (section.text or "").strip() and bool(children(section, "title"))
            and all(_tag(child) in ("title", "empty-line") and not (child.tail or "").strip()
                    for child in section))


def convert(path: str, out_dir: str) -> Book:
    """Convert the whole book; its pictures are written to `<out_dir>/img/`."""
    root = parse_xml(_source(path))
    described = _Description(root, path)
    writer = _Writer(root, described.cover)
    if not writer.units:
        raise ReaderError("corrupt", _DAMAGED)
    resources = _Resources(root, out_dir)
    resources.names = {name for name, _ in writer.documents}
    builder = BookBuilder()
    for name, parts in writer.documents:
        html.convert_document(builder, "".join(parts), name, resources)
    builder.finish()
    return Book(described.title, display_author(described.authors), described.language,
                list(builder.sections), toc.build_toc(writer.contents, builder, described.title),
                builder.blocks, described.authors)
