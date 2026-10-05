"""The block list every format converts into, and the inline markup inside it.

`BookBuilder` collects blocks, sections, anchors and links for one book;
`Inline` turns the styled text of one block into plain text or the small
StyledText subset the reader draws (`<b> <i> <u> <s> <a href> <br>`).

The whitespace rules (which characters collapse, which invisible characters
are removed, trimming at block edges) follow the content iterator of the
Readium toolkits (HtmlResourceContentIterator.kt / html_converter.go,
BSD-3-Clause, Copyright Readium Foundation); the code is written fresh.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

PARAGRAPH_LIMIT = 2000
# How many tags of markup one book may have converted. The largest real books
# hold about a twentieth of this; a file of nothing but tags would otherwise
# keep the converter busy for minutes. A book past it is refused, not cut:
# nobody should read to a last page that isn't one.
TAG_BUDGET = 4_000_000
PRE_LIMIT = 4000
TABLE_ROWS = 60
HEADING_LIKE_LIMIT = 120

BOLD, ITALIC, UNDERLINE, STRIKE, SUPER, SUB = 1, 2, 4, 8, 16, 32
_RAISED = SUPER | SUB
_TAGS = ((BOLD, "b"), (ITALIC, "i"), (UNDERLINE, "u"), (STRIKE, "s"))
TEXT_KINDS = ("p", "h", "li")

_SUPERSCRIPT = str.maketrans("0123456789+-−=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱ")
_SUBSCRIPT = str.maketrans("0123456789+-−=()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎")
_SUPER_OK = frozenset("0123456789+-−=()ni  ")
_SUB_OK = frozenset("0123456789+-−=()  ")


def _invisible_table() -> dict[int, str | None]:
    table: dict[int, str | None] = {code: None for code in range(0x20)}
    for code in (0x09, 0x0A, 0x0D):
        del table[code]
    table[0x0B] = table[0x0C] = " "
    table[0x7F] = None
    # A literal C1 control is almost always a windows-1252 character that was
    # decoded as Latin-1, so it is mapped the way browsers map &#146;.
    for code in range(0x80, 0xA0):
        try:
            table[code] = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            table[code] = None
    for code in (0x200B, 0x2060, 0xFEFF, 0xFFFC, 0xFFFE, 0xFFFF):
        table[code] = None
    table[0x2028] = table[0x2029] = " "
    # Fixed-width spaces cannot be drawn as such (see `escape`), so they join
    # the ordinary ones before whitespace is collapsed.
    for code in range(0x2002, 0x200B):
        table[code] = " "
    # Lone surrogates cannot be written as UTF-8.
    for code in range(0xD800, 0xE000):
        table[code] = None
    return table


_INVISIBLE = _invisible_table()
_SPACES = re.compile(r"[ \t\n\r]+")
_INNER_SPACES = re.compile(r" {2,}")
_TAG = re.compile(r"<[^<>]*>")
_NON_BREAK_TAG = re.compile(r"<(?!br>)")
_PLACEHOLDER = re.compile('<a href="\x1f(\\d+)\x1f">|</a>')
_TOKEN = re.compile(r"<[^<>]*>|&#?[a-z0-9]+;|[^<&]+|[<&]")
_ENTITY = re.compile(r"&#?[a-z0-9]+;")
_HREF_UNSAFE = re.compile("[\\s\"'<>\x00-\x1f\x7f-\x9f]")
_LINK = re.compile("\x1f\\d+\x1f")
_BLANK = " \n\xa0\u202f\xad"
_OPENING = _BLANK + "\"'“‘„‚«‹([{¿¡—–-"
_NUMBERED_TITLE = re.compile(
    r"(?:chapter|part|book|volume|section|letter|canto|act|scene|chapitre|livre|kapitel|teil"
    r"|cap[ií]tulo|capitolo)\s+(?:\d+|[ivxlcdm]+)\.", re.I)
_LETTER_SPACED = re.compile(r"\S(?: \S)+")
# Only an ordinary space ends a sentence here: `\s` would also cut at a no-break one.
_SENTENCE = re.compile(r".*?(?:[.!?…][\"'”’»)\]]* +|\Z)", re.S)
_SENTENCE_END = re.compile(r"[.!?…][\"'”’»)\]]* +\Z")


def clean(text: str) -> str:
    """Remove characters that are invisible or unsafe to store."""
    return text.translate(_INVISIBLE)


def escape(text: str) -> str:
    """Literal text as inline markup.

    StyledText turns a literal no-break space into an ordinary one; only the
    numeric entity keeps its meaning.
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("\xa0", "&#160;").replace("\u202f", "&#8239;")


def _escape_href(href: str) -> str:
    """A link target as it is written in `href="…"`; "" when nothing is left of it.

    StyledText does not decode entities there and ends the value at the first
    quote of either kind, so `&` stays raw, quotes, angle brackets and spaces
    are percent-encoded and control characters are dropped.
    """
    if _LINK.fullmatch(href):
        return href

    def encode(match: re.Match) -> str:
        char = match.group()
        return quote(char, safe="") if char in " \"'<>" or ord(char) > 0x9f else ""

    return _HREF_UNSAFE.sub(encode, href.strip())


def _unescape(text: str) -> str:
    return (text.replace("&#160;", "\xa0").replace("&#8239;", "\u202f").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&amp;", "&"))


def plain_text(text: str, markup: int = 0) -> str:
    """The words of a block's `t` on one line, for names and for measuring.

    Whitespace is collapsed and soft hyphens, which only matter where text is
    wrapped, are removed.
    """
    if markup:
        text = _unescape(_TAG.sub("", text.replace("<br>", " ")))
    return " ".join(text.replace("\xad", "").split())


def ends_like_prose(text: str) -> bool:
    """Whether a line ends the way running text does, not the way a title does.

    A full stop also ends titles that are set in capitals or only number a
    chapter ("CHAPTER I.", "Part 2.").
    """
    if text.endswith((",", ";")):
        return True
    return text.endswith(".") and not (text.isupper() or _NUMBERED_TITLE.fullmatch(text))


def letter_spaced(text: str) -> bool:
    """Whether a line is capitals set one by one ("T H E E N D"), as scanned titles are."""
    if len(text) > HEADING_LIKE_LIMIT or not _LETTER_SPACED.fullmatch(text):
        return False
    characters = text[::2]
    letters = [char for char in characters if char.isalpha()]
    return (len(letters) >= 5 and len(letters) * 5 >= len(characters) * 4
            and all(char.isupper() for char in letters))


def _letters(text: str) -> str:
    return "".join(char for char in text if char.isalpha())


def _length(text: str, markup: int) -> int:
    """The length of `t` with each entity counted as the one character it is."""
    if not markup or "&" not in text:
        return len(text)
    return len(_ENTITY.sub("x", text))


def _simplify(text: str) -> tuple[str, int]:
    """Markup that turned out to need no tags becomes plain text."""
    if _NON_BREAK_TAG.search(text):
        return text, 1
    return _unescape(text.replace("<br>", "\n")), 0


class Inline:
    """Accumulates the styled text of one block and renders it."""

    __slots__ = ("_runs", "_line_start", "_space", "_sizes", "_indent",
                 "visible", "bold", "length")

    def __init__(self) -> None:
        # A run is [pieces, flags, href]; a line break is None.
        self._runs: list[list | None] = []
        self._line_start = True
        self._space = False
        self._sizes: dict[float, int] = {}
        self._indent = 0
        self.visible = False
        self.bold = False
        self.length = 0

    def __bool__(self) -> bool:
        return bool(self._runs)

    @property
    def ends_with_break(self) -> bool:
        return bool(self._runs) and self._runs[-1] is None

    @property
    def bare(self) -> bool:
        """Whether nothing but opening punctuation has been written so far."""
        return all(run is not None and not "".join(run[0]).strip(_OPENING)
                   for run in self._runs)

    @property
    def last_char(self) -> str:
        if not self._runs or self._runs[-1] is None:
            return ""
        return self._runs[-1][0][-1][-1]

    def text(self, text: str, flags: int = 0, href: str = "", size: float = 0.0) -> None:
        """Add text in normal flow: whitespace collapses across calls."""
        text = text.translate(_INVISIBLE)
        if not text:
            return
        text = _SPACES.sub(" ", text)
        if (self._line_start or self._space) and text[0] == " ":
            text = text[1:]
            if not text:
                return
        self._append(text, flags, href, size)
        self._space = text[-1] == " "

    def preformatted(self, text: str, flags: int = 0, href: str = "", size: float = 0.0,
                     *, keep_spaces: bool = True) -> None:
        """Add text whose line breaks (and, optionally, spaces) are significant."""
        text = text.translate(_INVISIBLE).replace("\r\n", "\n").replace("\r", "\n")
        for number, line in enumerate(text.split("\n")):
            if number:
                self.line_break()
            if not keep_spaces:
                self.text(line, flags, href, size)
                continue
            line = line.expandtabs(4)
            if not line:
                continue
            if self._line_start:
                body = line.lstrip(" ")
                line = " " * (len(line) - len(body)) + body
            # Runs of spaces would collapse when drawn; all but the last one
            # become no-break spaces so the width survives and lines still wrap.
            line = _INNER_SPACES.sub(lambda m: " " * (len(m.group()) - 1) + " ", line)
            self._append(line, flags, href, size)
            self._space = False

    def indent(self, columns: int) -> None:
        """Indent the line that is about to start (verse); applied with its first text."""
        if self._line_start:
            self._indent = max(0, columns)

    def line_break(self, soft: bool = False) -> None:
        """End the line. A soft break never produces an empty line."""
        self._indent = 0
        if self._line_start and (soft or not self._runs):
            return
        self._trim()
        if not self._runs:
            self._line_start = True
            return
        self._runs.append(None)
        self._line_start = True

    def drop_break(self) -> None:
        """Remove trailing line breaks, so the content ends with text again."""
        while self._runs and self._runs[-1] is None:
            self._runs.pop()
        self._line_start = not self._runs
        self._indent = 0

    def dominant_size(self) -> float:
        """The font size most of the text was set in (0 when unknown)."""
        if not self._sizes:
            return 0.0
        return max(self._sizes.items(), key=lambda item: item[1])[0]

    def render(self) -> tuple[str, int]:
        """Return `(t, f)` for the block; `("", 0)` when there is nothing."""
        self._trim()
        while self._runs and self._runs[-1] is None:
            self._runs.pop()
            self._trim()
        if not self._runs:
            return "", 0
        hrefs = {run[2] for run in self._runs if run is not None and run[2]}
        hrefs = {href: _escape_href(href) for href in hrefs}
        items = [["\n", 0, ""] if run is None else ["".join(run[0]), run[1], hrefs.get(run[2], "")]
                 for run in self._runs]
        _resolve_raised(items)
        _spread_neutral(items)
        items = _merge(items)
        inked = [item for item in items if item[0].strip(_BLANK)]
        self.bold = bool(inked) and all(item[1] & BOLD for item in inked)
        if not any(item[1] or item[2] for item in items):
            return "".join(item[0] for item in items), 0
        return _markup(items), 1

    def _append(self, text: str, flags: int, href: str, size: float) -> None:
        if self._indent:
            text = "\xa0" * self._indent + text
            self._indent = 0
        last = self._runs[-1] if self._runs else None
        if last is not None and last[1] == flags and last[2] == href:
            last[0].append(text)
        else:
            self._runs.append([[text], flags, href])
        self._line_start = False
        self.length += len(text)
        if size:
            self._sizes[size] = self._sizes.get(size, 0) + len(text)
        if not self.visible and text.strip(_BLANK):
            self.visible = True

    def _trim(self) -> None:
        """Drop the collapsible space at the end of the current line."""
        if not self._space:
            return
        self._space = False
        run = self._runs[-1]
        piece = run[0][-1][:-1]
        if piece:
            run[0][-1] = piece
            return
        run[0].pop()
        if not run[0]:
            self._runs.pop()
            self._line_start = not self._runs or self._runs[-1] is None


def _resolve_raised(items: list[list]) -> None:
    """Map super/subscript groups to Unicode forms when every character has one."""
    index = 0
    while index < len(items):
        kind = items[index][1] & _RAISED
        if not kind:
            index += 1
            continue
        end = index
        while end < len(items) and items[end][1] & _RAISED == kind:
            end += 1
        allowed, table = (_SUB_OK, _SUBSCRIPT) if kind == SUB else (_SUPER_OK, _SUPERSCRIPT)
        mappable = kind != _RAISED and all(
            allowed.issuperset(items[n][0]) for n in range(index, end))
        for n in range(index, end):
            if mappable:
                items[n][0] = items[n][0].translate(table)
            items[n][1] &= ~_RAISED
        index = end


def _spread_neutral(items: list[list]) -> None:
    """Give line breaks and bare spaces the style their neighbours share.

    `<i>a</i> <i>b</i>` then renders as `<i>a b</i>` instead of two elements.
    """
    following: list = [None] * len(items)
    nearest = None
    for index in range(len(items) - 1, -1, -1):
        following[index] = nearest
        if items[index][0].strip(" \n"):
            nearest = items[index]
    previous = None
    for index, item in enumerate(items):
        if item[0].strip(" \n"):
            previous = item
            continue
        after = following[index]
        if previous is None or after is None:
            item[1], item[2] = 0, ""
        else:
            item[1] = previous[1] & after[1]
            item[2] = previous[2] if previous[2] == after[2] else ""


def _merge(items: list[list]) -> list[list]:
    merged: list[list] = []
    # The text of the item being grown, joined once it is complete: adding to
    # a string held in a list copies it every time.
    pieces: list[str] = []
    for item in items:
        last = merged[-1] if merged else None
        if last is not None and last[1] == item[1] and last[2] == item[2]:
            pieces.append(item[0])
            continue
        if len(pieces) > 1:
            last[0] = "".join(pieces)
        merged.append(item)
        pieces = [item[0]]
    if len(pieces) > 1:
        merged[-1][0] = "".join(pieces)
    return merged


def _close(slot: int) -> str:
    return "</a>" if slot == 0 else "</%s>" % _TAGS[slot - 1][1]


def _markup(items: list[list]) -> str:
    """Render runs as properly nested tags, opening longer-lived styles first."""
    count = len(items)
    # extent[n][k]: for how many consecutive runs from n style k keeps its value.
    extent = [[0] * 5 for _ in range(count + 1)]
    for index in range(count - 1, -1, -1):
        _, flags, href = items[index]
        row, after = extent[index], extent[index + 1]
        if href:
            same = index + 1 < count and items[index + 1][2] == href
            row[0] = after[0] + 1 if same else 1
        for slot, (bit, _) in enumerate(_TAGS, 1):
            if flags & bit:
                row[slot] = after[slot] + 1
    out: list[str] = []
    open_tags: list[tuple[int, str]] = []
    for index, (text, flags, href) in enumerate(items):
        wanted = {0: href} if href else {}
        for slot, (bit, _) in enumerate(_TAGS, 1):
            if flags & bit:
                wanted[slot] = ""
        keep = 0
        while keep < len(open_tags) and wanted.get(open_tags[keep][0]) == open_tags[keep][1]:
            keep += 1
        while len(open_tags) > keep:
            out.append(_close(open_tags.pop()[0]))
        have = {slot for slot, _ in open_tags}
        row = extent[index]
        for slot in sorted((s for s in wanted if s not in have), key=lambda s: (-row[s], s)):
            out.append('<a href="%s">' % href if slot == 0
                       else "<%s>" % _TAGS[slot - 1][1])
            open_tags.append((slot, wanted[slot]))
        out.append(escape(text).replace("\n", "<br>"))
    while open_tags:
        out.append(_close(open_tags.pop()[0]))
    return "".join(out)


def split_text(text: str, markup: int, limit: int = PARAGRAPH_LIMIT) -> list[tuple[str, int]]:
    """Cut an over-long `t` into `(t, f)` pieces of at most about `limit` characters.

    Cuts fall on a line break if there is one, else after a sentence, else at
    a space, and never inside a tag or an entity; tags open at a cut are
    closed there and reopened in the next piece.
    """
    if _length(text, markup) <= limit:
        return [(text, markup)]
    pieces: list[tuple[str, int]] = []
    current: list[tuple[str, str]] = []
    length = 0
    open_tags: list[str] = []
    # The best place to cut seen so far: (rank, atom index, tags open there, is a break).
    best: tuple[int, int, tuple[str, ...], bool] | None = None

    def cut(rank: int, index: int, opened: tuple[str, ...], at_break: bool) -> None:
        nonlocal current, length, best
        body = "".join(atom for _, atom in current[:index]).rstrip(" ")
        if plain_text(body, markup):
            closers = "".join("</%s>" % tag[1] for tag in reversed(opened))
            pieces.append(_simplify(body + closers) if markup else (body, 0))
        rest = current[index + 1:] if at_break else current[index:]
        while rest and (rest[0][0] == "break"
                        or rest[0][0] == "text" and not rest[0][1].strip(" ")):
            rest.pop(0)
        if rest and rest[0][0] == "text":
            rest[0] = ("text", rest[0][1].lstrip(" "))
        current = [("open", tag) for tag in opened] + rest
        length = sum(_length(atom, markup) for _, atom in current)
        best = None

    for kind, atom in _atoms(text, markup, limit):
        width = _length(atom, markup)
        if kind == "text" and length + width > limit:
            if best is not None:
                cut(*best)
            if length + width > limit and any(k == "text" for k, _ in current):
                cut(0, len(current), tuple(open_tags), False)
        if kind == "text" and pieces and not any(k == "text" for k, _ in current):
            # A piece does not begin with the space that parted it from the last.
            atom = atom.lstrip(" ")
            if not atom:
                continue
            width = _length(atom, markup)
        current.append((kind, atom))
        length += width
        if kind == "open":
            open_tags.append(atom)
        elif kind == "close":
            if open_tags:
                open_tags.pop()
        elif length >= limit * 0.4:
            if kind == "break":
                rank, at = 3, len(current) - 1
            elif _SENTENCE_END.search(atom):
                rank, at = 2, len(current)
            else:
                rank, at = int(atom[-1:] == " "), len(current)
            if rank and (best is None or rank >= best[0]):
                best = (rank, at, tuple(open_tags), kind == "break")
    cut(0, len(current), (), False)
    return pieces


def _atoms(text: str, markup: int, limit: int) -> list[tuple[str, str]]:
    """Tokens of `t` as (kind, source): open, close, break, or text no longer than `limit`."""
    if markup:
        tokens = _TOKEN.findall(text)
    else:
        tokens = []
        for number, line in enumerate(text.split("\n")):
            if number:
                tokens.append("\n")
            if line:
                tokens.append(line)
    atoms: list[tuple[str, str]] = []
    for token in tokens:
        if token in ("<br>", "\n"):
            atoms.append(("break", token))
        elif markup and token[0] == "<" and len(token) > 1:
            atoms.append(("close" if token[1] == "/" else "open", token))
        elif markup and token[0] == "&":
            atoms.append(("text", token))
        else:
            for sentence in filter(None, _SENTENCE.findall(token)):
                if len(sentence) > limit:
                    # Small parts, so that a piece is filled to near the limit
                    # before the cut rather than ending wherever a part did.
                    atoms.extend(("text", part) for part in _chunks(sentence, max(1, limit // 8)))
                    continue
                # Text that stops at a tag or an entity ends inside a word: the
                # last space before it is the nearest place a cut may fall.
                head, space, tail = sentence.rpartition(" ")
                if space and tail:
                    atoms.append(("text", head + space))
                    sentence = tail
                atoms.append(("text", sentence))
    return atoms


def _chunks(text: str, limit: int) -> list[str]:
    """`text` in parts of at most `limit` characters, cut at spaces where possible."""
    parts: list[str] = []
    start = 0
    while len(text) - start > limit:
        end = text.rfind(" ", start + limit // 2, start + limit)
        end = start + limit if end < 0 else end + 1
        parts.append(text[start:end])
        start = end
    parts.append(text[start:])
    return parts


def _split_pre(text: str, limit: int = PRE_LIMIT) -> list[str]:
    """Cut long preformatted text at blank lines where possible, else between lines."""
    if len(text) <= limit:
        return [text] if text.strip() else []
    lines: list[str] = []
    for line in text.split("\n"):
        if len(line) > limit:
            lines.extend(line[n:n + limit] for n in range(0, len(line), limit))
        else:
            lines.append(line)
    pieces: list[str] = []
    current: list[str] = []
    length = 0
    blank = 0
    for line in lines:
        if length + len(line) > limit and current:
            at = blank or len(current)
            pieces.append("\n".join(current[:at]))
            current = current[at:]
            length = sum(len(item) + 1 for item in current)
            blank = 0
        if not line.strip() and length >= limit * 0.4:
            blank = len(current)
        current.append(line)
        length += len(line) + 1
    pieces.append("\n".join(current))
    return [piece.strip("\n") for piece in pieces if piece.strip()]


class BookBuilder:
    """Collects the blocks of one book as its documents are converted.

    Block indexes are final as soon as `add` returns: empty blocks are refused
    and over-long paragraphs are split on the way in, so anchors, sections and
    headings never need renumbering.
    """

    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.sections: list[int] = []
        self.headings: list[tuple[int, int, str]] = []
        self._records: list[list] = []
        self._starts: dict[str, int] = {}
        self._current = ""
        self._new_section = False
        self._anchors: dict[tuple[str, str], int] = {}
        self._folded: dict[tuple[str, str], int] = {}
        self._links: list[tuple[str, str, bool]] = []
        self._link_numbers: dict[tuple[str, str, bool], int] = {}
        self._sizes: dict[int, float] = {}
        self._candidates: list[tuple[int, bool]] = []
        self._finished = False
        self.tags_left = TAG_BUDGET

    def begin_section(self, name: str) -> None:
        """A source document starts. Repeating the current name changes nothing."""
        if self._records and self._current == name:
            return
        self._current = name
        self._new_section = True
        self._starts.setdefault(name, len(self.blocks))
        self._records.append([name, len(self.blocks), ""])

    def section_title(self, title: str) -> None:
        """Record the current document's own title (its `<title>`), once."""
        if self._records and not self._records[-1][2]:
            self._records[-1][2] = plain_text(clean(title))

    def section_records(self) -> list[tuple[str, int, str]]:
        """`(name, first block, title)` for every section that has blocks."""
        records: list[tuple[str, int, str]] = []
        for name, start, title in self._records:
            if start >= len(self.blocks):
                break
            if records and records[-1][1] == start:
                # The earlier document produced nothing; the block is this one's.
                records.pop()
            records.append((name, start, title))
        return records

    def anchor(self, fragment: str) -> None:
        """An id in the current section: it names the next block emitted."""
        if not fragment or len(fragment) > 512:
            return
        key = (self._current, fragment)
        if key not in self._anchors:
            self._anchors[key] = len(self.blocks)
            self._folded.setdefault((self._current, fragment.casefold()), len(self.blocks))

    def add(self, block: dict, *, size: float = 0.0, emphatic: bool = False) -> int:
        """Append a block and return its index.

        An empty block is not stored; the index returned is then the one the
        next block will get. `size` is the computed font size of the text
        relative to the document default and `emphatic` says the whole block
        is bold; `finish` turns them into `z` and heading candidates.
        """
        index = len(self.blocks)
        kind = block.get("k")
        if kind in TEXT_KINDS:
            self._add_text(block, size, emphatic)
        elif kind == "pre":
            for piece in _split_pre(block.get("t") or ""):
                self._store({"k": "pre", "t": piece})
        elif kind == "tbl":
            rows = [row for row in block.get("rows") or [] if row]
            header = block.get("hdr") or 0
            for start in range(0, len(rows), TABLE_ROWS):
                part = {"k": "tbl", "rows": rows[start:start + TABLE_ROWS]}
                if start < header:
                    part["hdr"] = min(header - start, len(part["rows"]))
                self._store(part)
        elif kind == "hr" or (kind == "img" and block.get("src")):
            self._store(block)
        return index

    def link(self, name: str, fragment: str) -> str:
        """An opaque href for an internal link; `finish` resolves or removes it.

        The link is taken to be written in the current section: a fragment
        missing from that same document makes it a dead link.
        """
        key = (name, fragment, name == self._current)
        number = self._link_numbers.get(key)
        if number is None:
            number = self._link_numbers[key] = len(self._links)
            self._links.append(key)
        return "\x1f%d\x1f" % number

    def anchor_index(self, name: str, fragment: str = "") -> int | None:
        """The block a section (or an id inside it) starts at; None when unknown."""
        if not self.blocks:
            return None
        if fragment:
            index = self._anchors.get((name, fragment))
            if index is None:
                index = self._anchors.get((name, unquote(fragment)))
            if index is None:
                index = self._folded.get((name, fragment.casefold()))
        else:
            index = self._starts.get(name)
        if index is None:
            return None
        return min(index, len(self.blocks) - 1)

    def finish(self) -> None:
        """Resolve links, settle `z`, `s` and `i`, find heading candidates; idempotent."""
        if self._finished:
            return
        self._finished = True
        if not self.blocks:
            self._store({"k": "p", "t": "This book has no text."})
        targets = [self._target(*link) for link in self._links]
        for block in self.blocks:
            if block.get("f") == 1 and "\x1f" in block["t"]:
                block["t"], markup = _simplify(_resolve(block["t"], targets))
                if not markup:
                    del block["f"]
            elif block["k"] == "tbl":
                block["rows"] = [[_resolve(cell, targets) if "\x1f" in cell else cell
                                  for cell in row] for row in block["rows"]]
        self._apply_sizes()
        self._calm_spacing()
        self._calm_indent()
        for start in self.sections:
            if self.blocks[start]["k"] in TEXT_KINDS:
                self.blocks[start]["s"] = 2
        self._add_heading_likes()

    def _add_text(self, block: dict, size: float, emphatic: bool) -> None:
        text = block.get("t") or ""
        if not text.strip(_BLANK):
            return
        index = len(self.blocks)
        kind = block["k"]
        markup = 1 if block.get("f") else 0
        if kind == "h" and _length(text, markup) > PARAGRAPH_LIMIT:
            block = {key: value for key, value in block.items() if key != "l"}
            block["k"] = kind = "p"
        pieces = split_text(text, markup)
        for number, (piece, piece_markup) in enumerate(pieces):
            part = dict(block)
            part["t"] = piece
            part.pop("f", None)
            if piece_markup:
                part["f"] = 1
            if number:
                part.pop("s", None)
                part["c"] = 1
                if kind == "li":
                    part["m"] = ""
            self._store(part)
            if size and kind != "h":
                self._sizes[len(self.blocks) - 1] = size
        if len(self.blocks) == index:
            return
        if kind == "h":
            level = block.get("l")
            level = level if isinstance(level, int) and 1 <= level <= 6 else 6
            self.blocks[index]["l"] = level
            self.headings.append((index, level, plain_text(text, markup)))
        elif kind == "p" and len(pieces) == 1 and len(text) <= 4 * HEADING_LIKE_LIMIT:
            self._candidates.append((index, emphatic))

    def _store(self, block: dict) -> None:
        if self._new_section or not self.sections:
            self.sections.append(len(self.blocks))
            self._new_section = False
        self.blocks.append(block)

    def _target(self, name: str, fragment: str, local: bool) -> int | None:
        index = self.anchor_index(name, fragment)
        # A browser asked for a missing fragment shows the top of the document,
        # which moves the reader only when the link leads to another one.
        if index is None and fragment and not local:
            index = self.anchor_index(name)
        return index

    def _apply_sizes(self) -> None:
        """Turn font sizes into `z`, relative to the size most of the book is set in."""
        weight: dict[float, int] = {}
        for index, size in self._sizes.items():
            weight[size] = weight.get(size, 0) + len(self.blocks[index]["t"])
        if not weight:
            return
        base = max(weight.items(), key=lambda item: item[1])[0]
        for index, size in self._sizes.items():
            block = self.blocks[index]
            if "z" in block:
                continue
            ratio = size / base
            if ratio <= 0.86:
                block["z"] = -1
            elif ratio >= 1.55:
                block["z"] = 2
            elif ratio >= 1.15:
                block["z"] = 1

    def _calm_spacing(self) -> None:
        """Drop `s: 1` where a section spaces most of its paragraphs.

        A gap above one paragraph in twenty is a scene break; a gap above
        every other paragraph is just how that book sets its text.
        """
        bounds = self.sections + [len(self.blocks)]
        for start, end in zip(bounds, bounds[1:]):
            texts = [block for block in self.blocks[start:end] if block["k"] in TEXT_KINDS]
            spaced = [block for block in texts if block.get("s") == 1]
            if len(texts) >= 4 and len(spaced) * 3 > len(texts):
                for block in spaced:
                    del block["s"]

    def _calm_indent(self) -> None:
        """Remove the indent a section gives to all of its running text.

        A margin on every paragraph is the page's margin, not an indent.
        """
        bounds = self.sections + [len(self.blocks)]
        for start, end in zip(bounds, bounds[1:]):
            section = [block for block in self.blocks[start:end] if block["k"] in TEXT_KINDS]
            body = [block.get("i", 0) for block in section
                    if block["k"] == "p" and "a" not in block]
            shared = min(body, default=0)
            if len(body) < 4 or not shared:
                continue
            for block in section:
                if block.get("i", 0) > shared:
                    block["i"] -= shared
                else:
                    block.pop("i", None)

    def _add_heading_likes(self) -> None:
        """Record short paragraphs that look like titles as level-7 headings."""
        lines = []
        for index, emphatic in self._candidates:
            block = self.blocks[index]
            lines.append((index, emphatic, plain_text(block["t"], block.get("f", 0))))
        # A scanned book sets its running heads in the same letter-spaced
        # capitals as its chapter titles: only a line met once is a title, and
        # not when it is a piece of a running head or one with its page number
        # (whose 1 and 0 a scan may read as I and O).
        spaced: dict[str, int] = {}
        for _, _, text in lines:
            if letter_spaced(text):
                spaced[text] = spaced.get(text, 0) + 1
        heads = [_letters(text) for text, count in spaced.items() if count > 1]

        def scanned_title(text: str) -> bool:
            if spaced.get(text) != 1:
                return False
            letters = _letters(text)
            return not any(letters in head
                           or head in letters and not letters.replace(head, "", 1).strip("IO")
                           for head in heads)

        # (first block, last block, text) of each candidate.
        found: list[tuple[int, int, str]] = []
        run = False
        for index, emphatic, text in lines:
            block = self.blocks[index]
            joins, run = run, False
            if (len(text) > HEADING_LIKE_LIMIT or block.get("q") or block.get("c")
                    or sum(char.isalpha() for char in text) < 3
                    or block["t"].count("\n") + block["t"].count("<br>") > 1):
                continue
            if scanned_title(text):
                run = True
                # Such a title set on two lines arrives as two paragraphs.
                if joins and found[-1][1] == index - 1 \
                        and len(found[-1][2]) + len(text) < HEADING_LIKE_LIMIT:
                    found[-1] = (found[-1][0], index, found[-1][2] + " " + text)
                else:
                    found.append((index, index, text))
                continue
            shouting = block.get("a") == "c" and text.isupper()
            if (emphatic or block.get("z", 0) >= 1 or shouting) and not ends_like_prose(text):
                found.append((index, index, text))
        # A title is followed by the text it heads; a line followed by another
        # short display line belongs to a title page or a bold passage.
        taken = {first for first, _, _ in found}
        titles = []
        for first, last, text in found:
            following = last + 1
            if following in taken or following >= len(self.blocks):
                continue
            after = self.blocks[following]
            if after["k"] in ("p", "li") and "a" not in after:
                titles.append((first, 7, text))
        if titles:
            self.headings = sorted(self.headings + titles)


def _resolve(text: str, targets: list[int | None]) -> str:
    """Replace link placeholders by block targets; a dead link loses its `<a>`."""
    dead = False

    def replace(match: re.Match) -> str:
        nonlocal dead
        if match.group(1) is None:
            if dead:
                dead = False
                return ""
            return "</a>"
        number = int(match.group(1))
        target = targets[number] if number < len(targets) else None
        if target is None:
            dead = True
            return ""
        return '<a href="b:%d">' % target

    return _PLACEHOLDER.sub(replace, text)
