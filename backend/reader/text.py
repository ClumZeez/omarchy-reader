"""Plain text and single web pages.

Text has no markup to go by, so its shape is read from the lines themselves:
how paragraphs are laid out, which lines are chapter headings, what is verse.
A web page goes through html.py like a chapter of any other book.
"""

from __future__ import annotations

import os
import posixpath
import re
from urllib.parse import unquote

from . import html, toc
from .blocks import ITALIC, PARAGRAPH_LIMIT, BookBuilder, Inline, clean
from .book import Book, Meta
from .encoding import decode_text
from .errors import ReaderError
from .library import detect, display_author, title_from_filename
from .pictures import Pictures
from .textutil import collapse_space, declared_encoding, decode_bytes
from .xmlutil import attr, descendants, local, parse_xml, text_of

_MISSING = "That book is no longer there."
_UNREADABLE = "This book's file can't be read."

MAX_TEXT = 64 * 1024 * 1024
MAX_PAGE = 32 * 1024 * 1024
MAX_SHEET = 1024 * 1024
MAX_BLOCKS = 200_000
HEAD = 64 * 1024
HEADING_LIMIT = 100
HEADING_INDENT = 40
SUBTITLE_LIMIT = 70
CAPS_LIMIT = 60
TITLE_LIMIT = 80
LONG_LINE = 150
MARKER_SEARCH = 1000
RELIABLE_LINES = 30
CHAPTERED = 3

_ROMAN = r"(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_UNITS = "one|two|three|four|five|six|seven|eight|nine"
_NUMBER_WORD = (
    r"(?:twenty|thirty|forty|fifty)(?:-(?:%s))?|%s|ten|eleven|twelve|thirteen|fourteen|fifteen"
    r"|sixteen|seventeen|eighteen|nineteen|first|second|third|fourth|fifth|sixth|seventh"
    r"|eighth|ninth|tenth|last|premi[eè]re?|seconde|deuxième|troisième" % (_UNITS, _UNITS))
_PART_WORDS = "book|part|partie|livre|teil|parte|volume|том|книга|часть"
_LOOSE_WORDS = "section|act|scene|letter"
_CHAPTER_WORDS = ("chapter|chapitre|cap[ií]tulo|capitolo|kapitel|hoofdstuk|глава|раздел|canto"
                  "|stave|" + _LOOSE_WORDS)
_NUMBERED = re.compile(
    r"\[?\s*(?:(%s)|(%s))\s+(?:\d{1,4}|%s|%s)(?![\w’'])[.:)\-—–]?\s*(.{0,80}?)\s*\]?"
    % (_PART_WORDS, _CHAPTER_WORDS, _NUMBER_WORD, _ROMAN), re.I)
_LOOSE = re.compile(r"\[?\s*(?:%s|%s)\b" % (_PART_WORDS, _LOOSE_WORDS), re.I)
_NAMED = re.compile(
    r"(?:prologue|epilogue|preface|foreword|introduction|afterword|contents|table of contents"
    r"|appendix(?: \w+)?|пролог|эпилог|предисловие|введение|послесловие|оглавление|содержание"
    r"|vorwort|prólogo|epílogo|table des matières)\s*[.:]?", re.I)
_IDEOGRAPHIC = re.compile(r"第\s*[0-9０-９一二三四五六七八九十百千零〇两]{1,7}\s*[章回节節卷部篇话話]\s*.{0,40}")
_BARE_NUMBER = re.compile(r"(?:\d{1,3}|%s)\.?" % _ROMAN.upper())
_MARKDOWN = re.compile(r"(#{1,6})\s+(.*?)\s*#*")
_UNDERLINE = re.compile(r"(=){3,}|-{3,}")
_RULE = re.compile(r"(?:[*•·~#=_-]\s*){3,}|\*")
_TABULAR = re.compile(r"\S {3,}\S|[│┃║╎]|\|.*\|")
_EMPHASIS = re.compile(r"(?<![\w_])_(?=[^\s_])|(?<=[^\s_])_(?![\w_])")
_PLACEHOLDER = re.compile(r"\[Illustration\]", re.I)
_CLOSINGS = frozenset(("THE END", "END", "FINIS", "FIN"))
_SEPARATORS = ".:)-—–"
_NUMERALS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_SENTENCE_ENDS = ".,;:。，；："
_INDENTED = re.compile(r" {2,8}\S|\u3000")

_START = re.compile(r"\s*\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK", re.I)
_END = re.compile(r"\s*\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK"
                  r"|End of (?:the |this )?Project Gutenberg", re.I)
_FIELD = r"^%s:[ \t]*(\S.*(?:\n {2,}\S.*)*)"
_TITLE_FIELD = re.compile(_FIELD % "Title", re.M)
_AUTHOR_FIELD = re.compile(_FIELD % "Author", re.M)
_LANGUAGE_FIELD = re.compile(_FIELD % "Language", re.M)
_LANGUAGES = {
    "english": "en", "french": "fr", "german": "de", "spanish": "es", "italian": "it",
    "portuguese": "pt", "dutch": "nl", "russian": "ru", "latin": "la", "greek": "el",
    "finnish": "fi", "swedish": "sv", "danish": "da", "polish": "pl", "chinese": "zh",
    "japanese": "ja",
}
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")

PER_LINE, WRAPPED, INDENTED = "line", "wrapped", "indented"


def _read(path: str, limit: int) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(limit)
    except FileNotFoundError:
        raise ReaderError("missing", _MISSING) from None
    except OSError:
        raise ReaderError("corrupt", _UNREADABLE) from None


def _lines(text: str) -> list[str]:
    text = text.replace("\f", "\n\n").replace("\u2028", "\n").replace("\u2029", "\n\n")
    return [line.expandtabs(8).rstrip() if "\t" in line else line.rstrip()
            for line in text.split("\n")]


class _Description:
    """Title, author and language of a text, and where its own words start and end."""

    def __init__(self, lines: list[str], path: str) -> None:
        self.authors: list[str] = []
        self.language = ""
        self.start = 0
        self.end = len(lines)
        search = min(len(lines), MARKER_SEARCH)
        begun = next((index for index in range(search) if _START.match(lines[index])), None)
        if begun is not None:
            self.start = begun + 1
            for index in range(len(lines) - 1, max(begun, len(lines) - MARKER_SEARCH), -1):
                if _END.match(lines[index]):
                    self.end = index
                    break
        header = "\n".join(lines[:begun if begun is not None else 40])
        title = _field(_TITLE_FIELD, header)
        author = _field(_AUTHOR_FIELD, header)
        if author:
            self.authors = [author]
        self.language = _LANGUAGES.get(_field(_LANGUAGE_FIELD, header).lower(), "")
        self.title = title or self._first_line(lines) or title_from_filename(path)

    def _first_line(self, lines: list[str]) -> str:
        """The opening line, when it is short, stands alone and is not a chapter heading."""
        for index in range(self.start, min(self.end, self.start + 200)):
            line = lines[index].strip()
            if not line:
                continue
            if len(line) > TITLE_LIMIT:
                return ""
            marked = _MARKDOWN.fullmatch(line)
            if marked:
                return clean(marked.group(2))
            following = lines[index + 1] if index + 1 < self.end else ""
            if following.strip() or _is_heading(line) or _RULE.fullmatch(line):
                return ""
            return clean(line)
        return ""


def _field(pattern: re.Pattern, header: str) -> str:
    found = pattern.search(header)
    return collapse_space(clean(found.group(1))) if found else ""


def _is_heading(line: str) -> bool:
    return bool(_NUMBERED.fullmatch(line) or _NAMED.fullmatch(line)
                or _IDEOGRAPHIC.fullmatch(line))


def _reads_on(line: str) -> bool:
    """Whether a line that starts like a chapter heading goes on like a sentence."""
    numbered = _NUMBERED.fullmatch(line)
    if not numbered:
        return line[-1] in _SENTENCE_ENDS and bool(_IDEOGRAPHIC.fullmatch(line))
    title = numbered.group(3)
    return bool(title) and (
        bool(_LOOSE.match(line)) or title[0].islower() or (
            title[-1] in _SENTENCE_ENDS
            and line[:numbered.start(3)].rstrip()[-1] not in _SEPARATORS))


def _value(number: str) -> int:
    """The number a bare heading stands for."""
    number = number.rstrip(".")
    if number.isdigit():
        return int(number)
    values = [_NUMERALS[numeral] for numeral in number]
    return sum(-value if value < following else value
               for value, following in zip(values, values[1:] + [0]))


def _is_caps(line: str) -> bool:
    """A short line of capitals: a heading when it stands alone."""
    return (3 <= len(line) <= CAPS_LIMIT and line == line.upper() and line != line.lower()
            and line[0].isalnum() and sum(char.isalpha() for char in line) >= 3
            and not line.endswith((".", ",", ";", ":")) and line not in _CLOSINGS)


def _wide(char: str) -> bool:
    return char >= "\u2e80"


class _Layout:
    """How the lines of a text become paragraphs."""

    def __init__(self, lines: list[str]) -> None:
        lengths = sorted(len(line) for line in lines if line)
        count = len(lengths)
        self.width = lengths[min(count - 1, count * 9 // 10)] if count else 0
        self.reliable = False
        self.mode = WRAPPED
        # A line this long was filled to the margin, so the next one continues it.
        self.full = self.width - 12
        if not count:
            return
        long = sum(1 for length in lengths if length > LONG_LINE) / count
        near = sum(1 for length in lengths if self.width - 12 <= length <= self.width + 2) / count
        blank = 1 - count / len(lines)
        indented = sum(1 for line in lines if _INDENTED.match(line)) / count
        if long >= 0.15 or self.width > 200:
            self.mode = PER_LINE
        elif self.width <= 100 and near >= 0.35:
            self.reliable = count >= RELIABLE_LINES
            if blank < 0.05 and indented >= 0.05:
                self.mode = INDENTED
        elif blank < 0.05 and indented >= 0.10:
            self.mode = INDENTED
        elif blank < 0.10:
            self.mode = PER_LINE


class _Reader:
    """Turns the lines of a text into blocks; headings carry how they were found."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.layout = _Layout(lines)
        self.items: list[tuple[dict, str]] = []

    def read(self) -> list[tuple[dict, str]]:
        lines = self.lines
        mode = self.layout.mode
        index = gap = 0
        fresh = True
        while index < len(lines) and len(self.items) < MAX_BLOCKS:
            if not lines[index]:
                gap += 1
                index += 1
                fresh = True
                continue
            before = len(self.items)
            if fresh:
                used = self._special(index, tight=mode == PER_LINE and not gap and index > 0)
                if used:
                    index += used
                    gap = 0
                    fresh = mode == PER_LINE
                    continue
            end = index + 1
            if mode != PER_LINE:
                while end < len(lines) and lines[end] and not (
                        mode == INDENTED and _INDENTED.match(lines[end])):
                    end += 1
            self._passage(lines[index:end])
            spaced = (gap >= (1 if mode == PER_LINE else 2) and before
                      and self.items[before - 1][0]["k"] == "p")
            if spaced and len(self.items) > before and self.items[before][0]["k"] == "p":
                self.items[before][0]["s"] = 1
            fresh = mode != WRAPPED
            index = end
            gap = 0
        self._fold(index)
        return self._settle()

    def _fold(self, start: int) -> None:
        """Past the bound nothing is dropped: the rest is kept as large paragraphs."""
        mode = self.layout.mode
        held: list[str] = []
        size = 0
        apart = True
        for line in self.lines[start:]:
            words = " ".join(line.split())
            if not words:
                apart = True
                continue
            if held and size + len(words) >= PARAGRAPH_LIMIT:
                self.items.append(({"k": "p", "t": clean("".join(held))}, ""))
                held = []
                size = 0
            if held:
                apart = apart or mode == PER_LINE or (
                    mode == INDENTED and bool(_INDENTED.match(line)))
                held.append("\n" if apart else " ")
            held.append(words)
            size += len(words) + 1
            apart = False
        if held:
            self.items.append(({"k": "p", "t": clean("".join(held))}, ""))

    def _line(self, index: int) -> str:
        return self.lines[index].strip() if 0 <= index < len(self.lines) else ""

    def _special(self, index: int, tight: bool = False) -> int:
        """A rule or a heading starting at this line; the number of lines it took.

        A `tight` line is a paragraph of its own with no blank line above
        it: there only a line that cannot be a sentence is a heading.
        """
        line = self._line(index)
        following = self._line(index + 1)
        # Further right than a centred line would start: a signature, a column head.
        if len(line) > HEADING_LIMIT or len(self.lines[index]) - len(line) > HEADING_INDENT:
            return 0
        if _PLACEHOLDER.fullmatch(line) and not following:
            return 1
        marked = _MARKDOWN.fullmatch(line)
        if marked and marked.group(2):
            return self._head(len(marked.group(1)), marked.group(2), "marked", 1)
        underline = _UNDERLINE.fullmatch(following)
        if underline and not self._line(index + 2) and not _RULE.fullmatch(line):
            return self._head(1 if underline.group(1) else 2, line, "marked", 2)
        if _RULE.fullmatch(line):
            if not self.items or self.items[-1][0]["k"] != "hr":
                self.items.append(({"k": "hr"}, ""))
            return 1
        if tight and _reads_on(line):
            return 0  # "Letter 4 arrived late."
        numbered = _NUMBERED.fullmatch(line)
        if numbered:
            if following and _is_heading(following) and not (tight and _reads_on(following)):
                return 0  # a contents listing
            if _LOOSE.match(line) and len(line) > 40 and following:
                return 0  # "Part of the reason …"
            level = 1 if numbered.group(1) else 2
            label = line.strip("[] ")
            if (following and not numbered.group(3) and len(following) <= SUBTITLE_LIMIT
                    and not following.startswith("[") and not self._line(index + 2)):
                return self._head(level, label + "\n" + following, "worded", 2)
            return self._head(level, label, "worded", 1)
        if _NAMED.fullmatch(line) or _IDEOGRAPHIC.fullmatch(line):
            return self._head(2, line, "worded", 1)
        if not tight and not following and (_BARE_NUMBER.fullmatch(line) or _is_caps(line)):
            return self._head(2, line, "bare", 1)
        return 0

    def _head(self, level: int, text: str, found: str, used: int) -> int:
        text = "\n".join(" ".join(line.split()) for line in clean(text).split("\n"))
        self.items.append(({"k": "h", "l": level, "t": text}, found))
        return used

    def _passage(self, lines: list[str]) -> None:
        """A run of lines with no blank one among them: a paragraph, verse or a figure."""
        indents = [len(line) - len(line.lstrip(" ")) for line in lines]
        margin = min(indents)
        words = [" ".join(line.split()) for line in lines]
        block: dict = {"k": "p"}
        if margin >= 2 and len(lines) > 1:
            block["i"] = 1
        if len(lines) > 1 and self._keeps_lines(words, margin):
            if any(_TABULAR.search(line.strip()) for line in lines):
                figure = "\n".join(line[margin:] for line in lines)
                self.items.append(({"k": "pre", "t": figure}, ""))
                return
            text = "\n".join("\xa0" * (indent - margin) + line
                             for indent, line in zip(indents, words))
        else:
            text = words[0]
            for line in words[1:]:
                # A word broken at a hyphen continues; ideographs take no space.
                glued = (text[-1] == "-" and text[-2:-1].isalpha() and line[0].isalpha()) or (
                    _wide(text[-1]) and _wide(line[0]))
                text += line if glued else " " + line
        block["t"], markup = _emphasised(clean(text))
        if markup:
            block["f"] = 1
        self.items.append((block, ""))

    def _keeps_lines(self, words: list[str], margin: int) -> bool:
        """Whether the line breaks are the author's: verse, an address, a list."""
        if all(_is_heading(line) for line in words):
            return True
        if self.layout.mode == PER_LINE:
            return False
        short = all(len(line) + margin < self.layout.full for line in words[:-1])
        if margin >= 2:
            return short
        return short and self.layout.reliable and all(
            len(line) < 0.75 * self.layout.width for line in words[:-1])

    def _settle(self) -> list[tuple[dict, str]]:
        """Demote headings that turn out to be a title page, a contents listing or a sign."""
        items = self.items
        start = 0
        while start < len(items):
            end = start
            while end < len(items) and items[end][0]["k"] == "h":
                end += 1
            if end - start >= 2:
                run = range(start, end)
                for index in run:
                    if items[index][1] == "bare":
                        items[index] = ({"k": "p", "a": "c", "t": items[index][0]["t"]}, "")
                worded = [index for index in run if items[index][1] == "worded"]
                levels = {items[index][0]["l"] for index in worded}
                if len(worded) >= 3 and len(levels) == 1:
                    for index in worded[:-1]:
                        items[index] = ({"k": "p", "t": items[index][0]["t"]}, "")
            start = max(end, start + 1)
        chapters = [index for index, (block, found) in enumerate(items)
                    if found == "marked" or (found == "worded" and block["l"] == 2
                                             and not _NAMED.fullmatch(block["t"]))]
        if len(chapters) >= CHAPTERED:
            # A book with chapter headings of its own has no use for a lone
            # line of capitals as another; numbers that count on divide it.
            bare = [index for index in range(chapters[0], len(items)) if items[index][1] == "bare"]
            numbers = [(index, _value(items[index][0]["t"])) for index in bare
                       if _BARE_NUMBER.fullmatch(items[index][0]["t"])]
            counted = set()
            for (index, value), (after, following) in zip(numbers, numbers[1:]):
                if following == value + 1:
                    counted.update((index, after))
            for index in bare:
                if index not in counted:
                    items[index] = ({"k": "p", "a": "c", "t": items[index][0]["t"]}, "")
        return items


def _emphasised(text: str) -> tuple[str, int]:
    """`_word_`, the plain-text way of writing italics, as markup."""
    if "_" not in text:
        return text, 0
    pieces: list[list] = []
    position = first = 0
    opened = False
    for found in _EMPHASIS.finditer(text):
        before = text[found.start() - 1:found.start()]
        after = text[found.end():found.end() + 1]
        closes = bool(before.strip("_ \n")) and not (after.isalnum() or after == "_")
        if opened and closes:
            pieces.append([text[position:found.start()], ITALIC])
            opened = False
            first = len(pieces)
        elif not opened and after.strip("_ \n") and not (before.isalnum() or before == "_"):
            pieces.append([text[position:found.start()], 0])
            opened = True
        elif not opened and closes:
            # Closed without having been opened: the passage began in an
            # earlier paragraph, so everything up to here belongs to it.
            pieces.append([text[position:found.start()], ITALIC])
            for piece in pieces[first:]:
                piece[1] = ITALIC
            first = len(pieces)
        else:
            continue
        position = found.end()
    if not pieces:
        return text, 0
    pieces.append([text[position:], ITALIC if opened else 0])
    inline = Inline()
    for piece, flags in pieces:
        inline.preformatted(piece, flags, keep_spaces=False)
    return inline.render()


def _text_book(raw: bytes, path: str) -> Book:
    lines = _lines(decode_text(raw))
    described = _Description(lines, path)
    builder = BookBuilder()
    builder.begin_section("t0")
    for block, _ in _Reader(lines[described.start:described.end]).read():
        if block["k"] == "h" and block["l"] <= 2:
            builder.begin_section("t%d" % len(builder.blocks))
        builder.add(block)
    builder.finish()
    return Book(described.title, display_author(described.authors), described.language,
                list(builder.sections), toc.build_toc([], builder, described.title),
                builder.blocks, described.authors)


class _Page:
    """A web page's own title, author and language, read from its head."""

    def __init__(self, markup: str, path: str) -> None:
        self.title = title_from_filename(path)
        self.authors: list[str] = []
        self.language = ""
        head = markup[:HEAD]
        end = head.lower().find("</head")
        try:
            root = parse_xml(head[:end] if end >= 0 else head)
        except ReaderError:
            return
        if local(root.tag).lower() == "html":
            self.language = attr(root, "lang").strip().lower()
        titles = [text_of(el) for el in root.iter() if local(el.tag).lower() == "title"]
        self.title = next((clean(title) for title in titles if title), self.title)
        for meta in descendants(root, "meta"):
            author = collapse_space(clean(attr(meta, "content")))
            if attr(meta, "name").strip().lower() == "author" and author:
                self.authors.append(author)


class _Files:
    """What a page may load: files beside it or below it, and nothing else."""

    def __init__(self, path: str, name: str, out_dir: str) -> None:
        self.root = os.path.realpath(os.path.dirname(os.path.abspath(path)))
        self.name = name
        self.pictures = Pictures(out_dir)
        self.found: dict[str, dict | None] = {}

    def _locate(self, href: str, base: str) -> str | None:
        href = href.strip().partition("#")[0].partition("?")[0].replace("\\", "/")
        if not href or href.startswith("/") or _SCHEME.match(href):
            return None
        relative = posixpath.join(posixpath.dirname(base), unquote(href))
        if "\x00" in relative:
            return None
        target = os.path.realpath(os.path.join(self.root, relative))
        if os.path.commonpath((target, self.root)) != self.root or not os.path.isfile(target):
            return None
        return target

    def _load(self, href: str, base: str, limit: int) -> bytes | None:
        target = self._locate(href, base)
        try:
            if target is None or os.path.getsize(target) > limit:
                return None
            return _read(target, limit)
        except (OSError, ReaderError):
            return None

    def stylesheet(self, href: str, base: str) -> str | None:
        data = self._load(href, base, MAX_SHEET)
        return None if data is None else decode_bytes(data)

    def image(self, href: str, base: str) -> dict | None:
        href = href.strip()
        inline = href[:5].lower() == "data:"
        key = href if inline else self._locate(href, base)
        if key is None:
            return None
        if key not in self.found:
            if inline:
                self.found[key] = self.pictures.store_data_uri(href)
            else:
                data = self._load(href, base, self.pictures.each)
                self.found[key] = self.pictures.store(data) if data else None
        return self.found[key]

    def document(self, href: str, base: str) -> str | None:
        path = href.partition("#")[0].partition("?")[0]
        return self.name if unquote(path) in ("", self.name) else None


def _page_book(raw: bytes, path: str, out_dir: str) -> Book:
    markup = decode_text(raw, declared=declared_encoding(raw))
    page = _Page(markup, path)
    name = os.path.basename(path)
    builder = BookBuilder()
    html.convert_document(builder, markup, name, _Files(path, name, out_dir))
    builder.finish()
    return Book(page.title, display_author(page.authors), page.language, list(builder.sections),
                toc.build_toc([], builder, page.title), builder.blocks, page.authors)


def read_meta(path: str) -> Meta:
    """Title, author and language from the start of the file; text has no cover."""
    raw = _read(path, HEAD)
    if detect(path) == "html":
        page = _Page(decode_text(raw, declared=declared_encoding(raw)), path)
        return Meta(page.title, page.authors, page.language, None)
    lines = _lines(decode_text(raw))
    if len(raw) == HEAD:
        del lines[-1:]  # the cut may have fallen inside it
    described = _Description(lines, path)
    return Meta(described.title, described.authors, described.language, None)


def convert(path: str, out_dir: str) -> Book:
    """Convert a text file or a web page; a page's pictures go to `<out_dir>/img/`."""
    if detect(path) == "html":
        return _page_book(_read(path, MAX_PAGE), path, out_dir)
    return _text_book(_read(path, MAX_TEXT), path)
