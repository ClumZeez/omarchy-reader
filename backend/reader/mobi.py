"""Kindle books: MOBI 6, KF8 (AZW3), files that hold both, and PalmDoc text.

The text itself is converted by html.py, one document per KF8 part or per
MOBI 6 page-break section.
"""

# Ported from foliate-js mobi.js, MIT, © John Factotum.

from __future__ import annotations

import bisect
import os
import re
import struct
from dataclasses import dataclass, field
from functools import cached_property
from html import escape, unescape

from . import html, images, toc
from .blocks import BookBuilder
from .errors import ReaderError
from .library import display_author, title_from_filename
from .textutil import collapse_space, write_atomic

_PROTECTED = "This book is protected by DRM and can't be opened."
_DAMAGED = "This book is damaged and can't be opened."
_TOPAZ = "This is a Topaz book, an old Kindle format of scanned pages that Reader can't open."
_KFX = "This is a KFX book, a newer Kindle format that Reader can't open."
_REPLICA = "This is a Print Replica book, a Kindle textbook of fixed pages that Reader can't open."
_PACKED = "This Kindle book is stored in a way Reader can't read."
_MISSING = "That book is no longer there."
_UNREADABLE = "This book's file can't be read."

NULL = 0xFFFFFFFF
STORED, PALMDOC, HUFFMAN = 1, 2, 17480
RECORD_TEXT = 4096
MAX_RECORD = 64 * 1024 * 1024
MAX_PACKED = 64 * 1024
MAX_UNPACKED = 64 * 1024
MAX_TEXT = 32 * 1024 * 1024
# No compression here packs prose more than a few times over; phrase tables built
# to explode can, and would make a small file cost as much as the largest book.
MAX_EXPANSION = 16
MAX_EXTH = 4096
MAX_INDEX_RECORDS = 4096
MAX_STRING_RECORDS = 64
MAX_ENTRIES = 500_000
MAX_TARGETS = 200_000
MAX_SECTIONS = 30_000
MAX_ASSEMBLY = 1 << 28
MAX_HUFFMAN_DEPTH = 32
MAX_PHRASES = 1 << 22
MAX_TOC_DEPTH = 32
MAX_TITLE = 1024
TAG_SPAN = 4096
HEAD_SPAN = 65536
INLINE_SPAN = 8192
MIN_COVER = 64
CONTENTS_PAGES = 8
# A name no section can have: marks a contents entry whose target is not in the book.
_NOWHERE = "\x00"

_GARBLED = (IndexError, KeyError, ValueError, TypeError, OverflowError, RecursionError,
            struct.error)

_BINARY = frozenset((b"INDX", b"FLIS", b"FCIS", b"FDST", b"DATP", b"SRCS", b"CMET", b"RESC",
                     b"FONT", b"HUFF", b"CDIC", b"PAGE", b"AUDI", b"VIDE", b"CRES"))
# No sentence starts like these; "BM", a bitmap, is not on the list because one can.
_MAGICS = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")
_PICTURES = _MAGICS + (b"BM",)
_CONTROLS = bytes(code for code in range(32) if code not in (9, 10, 13))
_HIGH = bytes(range(128, 256))
# The five bytes Windows-1252 leaves undefined.
_UNDEFINED = b"\x81\x8d\x8f\x90\x9d"

_MARKUP = re.compile(rb"<(?:html|head|body|p|div|br|a|b|i|font|h[1-6]|blockquote|span|img"
                     rb"|mbp:pagebreak)\b", re.I)
_PAGEBREAK = re.compile(rb"<\s*/?\s*(?:mbp:)?pagebreak[^<>]*>", re.I)
_BODY = re.compile(rb"<body\b[^<>]*>", re.I)
_HEAD_END = re.compile(rb"</head\s*>", re.I)
_STYLE = re.compile(rb"<style\b[^<>]*>.*?</style\s*>", re.I | re.S)
_REFERENCE = re.compile(rb"&#?[0-9A-Za-z]+;")
_FILEPOS = re.compile(rb"<[^<>]+\bfilepos\s*=\s*[\"']?(\d{1,12})", re.I)
_FILEPOS_LINK = re.compile(rb"(<a\b[^<>]*?)\bfilepos\s*=\s*[\"']?(\d{1,12})[\"']?", re.I)
_IMAGE = re.compile(rb"<img\b[^<>]*>", re.I)
_RECINDEX = re.compile(rb"\b(hi|lo)?recindex\s*=\s*[\"']?(\d{1,9})", re.I)
_GUIDE_TOC = re.compile(rb"<reference\b[^<>]*\btype\s*=\s*[\"']?toc\b[^<>]*>", re.I)
_POSITION = re.compile(rb"kindle:pos:fid:([0-9A-Va-v]{1,8}):off:([0-9A-Va-v]{1,12})")
_EMBED = re.compile(r"kindle:embed:([0-9A-Va-v]{1,8})")
_FLOW = re.compile(r"kindle:flow:([0-9A-Va-v]{1,8})")
_RECORD = re.compile(r"recindex:([\d,]{1,40})")
_SELECTOR = re.compile(rb"^[A-Z]-//\*\[@aid='([0-9A-Va-v]+)'\]$")
_LINK = re.compile(r"<(/?)(?:blockquote|ul|ol)\b[^<>]*>"
                   r"|<a\b[^<>]*?\bhref=[\"'](part\d+)#(pos\d+)[\"'][^<>]*>(.{0,2000}?)</a>",
                   re.I | re.S)
_TAG = re.compile(r"<[^<>]*>")
_UNPRINTABLE = re.compile("[\x00-\x1f\x7f\ufffd]")
_SEVERAL_AUTHORS = re.compile(r"\s*(?:;|&)\s*")
_INLINE = {name: re.compile(r"<dc:%s\b[^<>]*>([^<]{1,%d})<" % (name, MAX_TITLE), re.I)
           for name in ("title", "creator", "language")}

# The Windows code pages a header may name besides UTF-8; anything else is read as 1252.
_CODE_PAGES = frozenset((874, 932, 936, 949, 950, *range(1250, 1259)))
# Windows primary language ids, used when the book does not name its language.
_LANGUAGES = {
    1: "ar", 2: "bg", 3: "ca", 4: "zh", 5: "cs", 6: "da", 7: "de", 8: "el", 9: "en", 10: "es",
    11: "fi", 12: "fr", 13: "he", 14: "hu", 15: "is", 16: "it", 17: "ja", 18: "ko", 19: "nl",
    20: "no", 21: "pl", 22: "pt", 23: "rm", 24: "ro", 25: "ru", 26: "hr", 27: "sk", 28: "sq",
    29: "sv", 30: "th", 31: "tr", 32: "ur", 33: "id", 34: "uk", 35: "be", 36: "sl", 37: "et",
    38: "lv", 39: "lt", 41: "fa", 42: "vi", 43: "hy", 44: "az", 45: "eu", 47: "mk", 54: "af",
    55: "ka", 57: "hi", 62: "ms", 63: "kk", 65: "sw", 67: "uz", 68: "tt", 69: "bn", 70: "pa",
    71: "gu", 73: "ta", 74: "te", 75: "kn", 76: "ml", 78: "mr", 79: "sa", 87: "kok", 97: "ne",
}


@dataclass
class Meta:
    """What the library shows for a book before it is opened.

    `error` is the sentence `convert` would refuse the book with, so that a
    protected book can still be listed under its own title and cover.
    """

    title: str
    authors: list[str]
    language: str
    cover: bytes | None
    error: str = ""


@dataclass
class Book:
    """A converted book: the book.json object without the fields the library adds."""

    title: str
    author: str
    language: str
    sections: list[int]
    toc: list[dict]
    blocks: list[dict]
    authors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Entry:
    """One row of the book's own contents; `target` is a position in its text."""

    label: str
    depth: int
    parent: int | None
    target: int | None


@dataclass(slots=True)
class _Part:
    """One source document of a KF8 book, occupying `[start, end)` of the text."""

    start: int
    end: int
    data: bytes
    skeleton: int | None


@dataclass(slots=True)
class _Fragment:
    insert: int | None
    selector: bytes
    skeleton: int | None
    offset: int
    length: int


def _u16(data: bytes, offset: int, default: int = 0) -> int:
    if offset < 0 or offset + 2 > len(data):
        return default
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int, default: int = 0) -> int:
    if offset < 0 or offset + 4 > len(data):
        return default
    return struct.unpack_from(">L", data, offset)[0]


def _varint(data: bytes, position: int) -> tuple[int, int]:
    """A forward variable-width integer and its length: the last byte has bit 7 set."""
    value = used = 0
    for byte in data[position:position + 5]:
        used += 1
        value = (value << 7) | (byte & 0x7F)
        if byte & 0x80:
            break
    return value, used


def _base32(digits: bytes | str) -> int:
    return int(digits, 32)


def _clean(text: str) -> str:
    """A metadata string as written for readers: entities resolved, no control characters."""
    return collapse_space(_UNPRINTABLE.sub(" ", unescape(text)))


class _Pdb:
    """The Palm database a Kindle book is kept in: numbered records, read on demand."""

    def __init__(self, path: str) -> None:
        self._handle = open(path, "rb")
        try:
            self._read_directory()
        except BaseException:
            self._handle.close()
            raise

    def __enter__(self) -> "_Pdb":
        return self

    def __exit__(self, *exc: object) -> None:
        self._handle.close()

    def _read_directory(self) -> None:
        size = os.fstat(self._handle.fileno()).st_size
        head = self._handle.read(78)
        if head.startswith(b"TPZ"):
            raise ReaderError("unsupported", _TOPAZ)
        if head.startswith((b"\xeaDRMION\xee", b"CONT")):
            raise ReaderError("unsupported", _KFX)
        kind = head[60:68].upper()
        if len(head) < 78 or kind not in (b"BOOKMOBI", b"TEXTREAD", b"TEXTTLDC"):
            raise ReaderError("corrupt", _DAMAGED)
        self.plain = kind != b"BOOKMOBI"
        self.name = head[:32].split(b"\x00", 1)[0]
        count = min(_u16(head, 76), (size - 78) // 8)
        directory = self._handle.read(8 * count)
        count = min(count, len(directory) // 8)
        first = _u32(directory, 0)
        # A count that would put the record list on top of the first record is a lie.
        if count and 78 <= first < 78 + 8 * count:
            count = (first - 78) // 8
        offsets = []
        previous = 78 + 8 * count
        for index in range(count):
            previous = min(max(_u32(directory, 8 * index), previous), size)
            offsets.append(previous)
        self.count = count
        self._offsets = offsets + [size]
        self.text_limit = min(MAX_TEXT, max(MAX_UNPACKED, MAX_EXPANSION * size))

    def size(self, index: int) -> int:
        if not 0 <= index < self.count:
            return 0
        return self._offsets[index + 1] - self._offsets[index]

    def record(self, index: int) -> bytes:
        """A record's bytes; empty when there is no such record."""
        length = min(self.size(index), MAX_RECORD)
        if not length:
            return b""
        self._handle.seek(self._offsets[index])
        return self._handle.read(length)


class _Header:
    """The PalmDOC, MOBI and EXTH headers held by record `base`.

    Every record number in a header counts from the record that holds it. A
    field exists only when the header's declared length reaches it.
    """

    def __init__(self, pdb: _Pdb, base: int = 0) -> None:
        record = pdb.record(base)
        if len(record) < 16:
            raise ReaderError("corrupt", _DAMAGED)
        self.base = base
        self.compression = _u16(record, 0)
        self.text_length = _u32(record, 4)
        self.text_records = _u16(record, 8)
        self.record_size = _u16(record, 10)
        self.encryption = _u16(record, 12)
        self.has_mobi = record[16:20] == b"MOBI" and len(record) >= 24
        length = _u32(record, 20) if self.has_mobi else 0
        end = min(16 + length, len(record))

        def value(offset: int, default: int = NULL) -> int:
            return _u32(record, offset, default) if offset + 4 <= end else default

        self.codec = _codec_for(value(0x1C, 1252))
        version = value(0x24, 0)
        self.locale = value(0x5C, 0)
        self.first_resource = value(0x6C)
        self.huffman_index, self.huffman_count = value(0x70), value(0x74, 0)
        self.flags = 0
        if self.has_mobi and not pdb.plain and 0xE4 <= length <= 500:
            self.flags = _u16(record, 0xF2)
        self.ncx_index = value(0xF4)
        # Old files carry random version numbers, so the length must agree too.
        self.is_kf8 = self.has_mobi and version == 8 and end >= 0x108
        self.flow_index = self.fragment_index = self.skeleton_index = self.guide_index = NULL
        if self.is_kf8:
            # With a single flow the table's record number is not written.
            if value(0xC4, 0) > 1:
                self.flow_index = value(0xC0)
            self.fragment_index, self.skeleton_index = value(0xF8), value(0xFC)
            self.guide_index = value(0x104)
        self.full_name = ""
        offset, size = value(0x54, 0), value(0x58, 0)
        if offset and 0 < size <= MAX_TITLE and offset + size <= len(record):
            self.full_name = _clean(record[offset:offset + size].decode(self.codec, "replace"))
        self.exth: dict[int, list[bytes]] = {}
        if self.has_mobi and value(0x80, 0) & 0x40:
            self._read_exth(record, 16 + length)

    def _read_exth(self, record: bytes, start: int) -> None:
        if record[start:start + 4] != b"EXTH":
            start = record.find(b"EXTH", 16)
        if start < 16:
            return
        position = start + 12
        for _ in range(min(_u32(record, start + 8), MAX_EXTH)):
            kind, size = _u32(record, position), _u32(record, position + 4)
            if size < 8 or position + size > len(record):
                break
            self.exth.setdefault(kind, []).append(record[position + 8:position + size])
            position += size

    def number(self, kind: int) -> int | None:
        """The last value of an integer EXTH record; None when absent or unset."""
        for data in reversed(self.exth.get(kind, ())):
            if 1 <= len(data) <= 4:
                value = int.from_bytes(data, "big")
                return None if value == NULL else value
        return None

    def strings(self, kind: int) -> list[str]:
        values = (_clean(data.decode(self.codec, "replace")) for data in self.exth.get(kind, ()))
        return [value for value in values if value]


def _codec_for(code_page: int) -> str:
    if code_page == 65001:
        return "utf-8"
    return "cp%d" % code_page if code_page in _CODE_PAGES else "cp1252"


def _settle_codec(raw: bytes, declared: str) -> str:
    """The codec the text is really in; headers are known to lie both ways."""
    high = len(raw) - len(raw.translate(None, _HIGH))
    if not high:
        return declared
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        if declared != "utf-8":
            return declared
    # A few stray bytes in real UTF-8 are damage, not a wrong declaration.
    if raw.decode("utf-8", "replace").count("\ufffd") * 20 < high:
        return "utf-8"
    return "cp1252"


def _decode(raw: bytes, codec: str) -> str:
    if codec == "cp1252":
        raw = raw.translate(None, _UNDEFINED)
    return raw.decode(codec, "replace")


def _trailing_size(record: bytes, flags: int) -> int:
    """How many bytes after the text a record carries; -1 when they do not add up."""
    end = len(record)
    entries = flags >> 1
    while entries:
        if entries & 1:
            size = 0
            # A backward variable-width integer: a set high bit restarts it.
            for byte in record[max(0, end - 4):end]:
                if byte & 0x80:
                    size = 0
                size = (size << 7) | (byte & 0x7F)
            if size == 0 or size > end:
                return -1
            end -= size
        entries >>= 1
    if flags & 1:
        if end <= 0:
            return -1
        end -= (record[end - 1] & 3) + 1
        if end < 0:
            return -1
    return len(record) - end


def _strip(record: bytes, flags: int) -> bytes | None:
    """A text record without its trailing entries; None when the flags cannot be right."""
    if not flags:
        return record
    size = _trailing_size(record, flags)
    return None if size < 0 else record[:len(record) - size]


def _unpack_stored(data: bytes) -> bytes:
    return data[:MAX_UNPACKED]


def _unpack_palmdoc(data: bytes) -> bytes:
    """PalmDOC LZ77; each record stands alone."""
    data = data[:MAX_PACKED]
    out = bytearray()
    position, size = 0, len(data)
    while position < size:
        byte = data[position]
        position += 1
        if byte == 0 or 0x09 <= byte <= 0x7F:
            out.append(byte)
        elif byte <= 0x08:
            out += data[position:position + byte]
            position += byte
        elif byte >= 0xC0:
            out.append(0x20)
            out.append(byte ^ 0x80)
        else:
            if position >= size:
                break
            pair = (byte << 8) | data[position]
            position += 1
            distance, length = (pair >> 3) & 0x7FF, (pair & 7) + 3
            if distance == 0 or distance > len(out):
                continue
            start = len(out) - distance
            if distance >= length:
                out += out[start:start + length]
            else:
                # An overlapping copy repeats what it has just written.
                for index in range(start, start + length):
                    out.append(out[index])
    return bytes(out)


class _Huffman:
    """The HUFF/CDIC scheme: Huffman codes for phrases that may be coded themselves."""

    def __init__(self, records: list[bytes]) -> None:
        table = records[0] if records else b""
        codes, limits = _u32(table, 8), _u32(table, 12)
        if table[:4] != b"HUFF" or codes + 1024 > len(table) or limits + 256 > len(table):
            raise ValueError("no code table")
        self.codes = struct.unpack_from(">256L", table, codes)
        pairs = struct.unpack_from(">64L", table, limits)
        self.lowest = (0,) + pairs[0::2]
        self.highest = (0,) + pairs[1::2]
        self.phrases: list[list] = []
        for record in records[1:]:
            start, total, bits = _u32(record, 4), _u32(record, 8), _u32(record, 12)
            if record[:4] != b"CDIC" or bits > 16:
                raise ValueError("no phrase table")
            room = min(total, MAX_PHRASES) - len(self.phrases)
            for number in range(min(1 << bits, room, (len(record) - start) // 2)):
                offset = start + _u16(record, start + 2 * number, len(record))
                size = _u16(record, offset, 0x8000)
                self.phrases.append(
                    [record[offset + 2:offset + 2 + (size & 0x7FFF)], bool(size & 0x8000)])

    def unpack(self, data: bytes, depth: int = 0) -> bytes:
        if depth > MAX_HUFFMAN_DEPTH:
            raise ValueError("phrases nest too deeply")
        codes, lowest, highest, phrases = self.codes, self.lowest, self.highest, self.phrases
        data = data[:MAX_PACKED]
        bits = len(data) * 8
        data += b"\x00" * 8
        out = []
        position = size = 0
        while position < bits and size < MAX_UNPACKED:
            start = position >> 3
            window = (int.from_bytes(data[start:start + 5], "big") >> (8 - (position & 7))) \
                & 0xFFFFFFFF
            entry = codes[window >> 24]
            length, value = entry & 0x1F, entry >> 8
            if length == 0:
                raise ValueError("empty code")
            if not entry & 0x80:
                while length <= 32 and (window >> (32 - length)) < lowest[length]:
                    length += 1
                if length > 32:
                    raise ValueError("unknown code")
                value = highest[length]
            position += length
            if position > bits:
                break
            number = value - (window >> (32 - length))
            if not 0 <= number < len(phrases):
                raise ValueError("unknown phrase")
            phrase = phrases[number]
            if not phrase[1]:
                # Marked done first, so a phrase that names itself cannot loop.
                packed, phrase[0], phrase[1] = phrase[0], b"", True
                phrase[0] = self.unpack(packed, depth + 1)
            out.append(phrase[0])
            size += len(phrase[0])
        return b"".join(out)[:MAX_UNPACKED]


def _unpacker(pdb: _Pdb, header: _Header):
    """The function that inflates one text record of this header."""
    if header.compression == STORED:
        return _unpack_stored
    if header.compression == PALMDOC:
        return _unpack_palmdoc
    if header.compression == HUFFMAN:
        count = min(header.huffman_count, 1024)
        if header.huffman_index == NULL or count < 2:
            raise ValueError("no code tables")
        first = header.base + header.huffman_index
        return _Huffman([pdb.record(first + number) for number in range(count)]).unpack
    raise ReaderError("unsupported", _PACKED)


def _is_binary(record: bytes) -> bool:
    """Whether a record can never be text: only signatures no sentence starts with."""
    return (record in (b"BOUNDARY", b"\xe9\x8e\r\n")
            or (record[:4] in _BINARY and record[4:5] == b"\x00")
            or record.startswith(_MAGICS))


def _is_noise(text: bytes) -> bool:
    """Whether "text" is more than 2 % control characters, as undecrypted text is."""
    return (len(text) - len(text.translate(None, _CONTROLS))) * 50 > len(text)


def _inflate(unpack, record: bytes, flags: int) -> bytes:
    body = _strip(record, flags)
    try:
        return unpack(record if body is None else body)
    except _GARBLED:
        return b""


def _read_text(pdb: _Pdb, header: _Header, unpack, flags: int, end: int) -> bytes:
    """The text records of a header, inflated and joined.

    The header's count of them is a hint: reading stops at the first record
    that cannot be text and goes on while the declared length is not reached.
    """
    first = header.base + 1
    chunks = []
    total = 0
    for index in range(first, end):
        record = pdb.record(index)
        extra = index >= first + header.text_records
        if _is_binary(record) or total >= pdb.text_limit:
            break
        if extra and (total >= header.text_length or len(record) <= 8):
            break
        if header.text_length and total >= header.text_length and len(record) <= 8:
            break
        chunk = _inflate(unpack, record, flags)
        if extra and _is_noise(chunk):
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _flags_fit(pdb: _Pdb, header: _Header, unpack, flags: int, count: int) -> bool:
    """Whether the first records, cut with these flags, hold exactly one record of text each."""
    for index in range(header.base + 1, header.base + 1 + min(4, count - 1)):
        body = _strip(pdb.record(index), flags)
        if body is None:
            return False
        try:
            text = unpack(body)
        except _GARBLED:
            return False
        if len(text) not in (RECORD_TEXT, header.record_size) or _is_noise(text):
            return False
    return True


def _extract_text(pdb: _Pdb, header: _Header, end: int) -> bytes:
    """The book's undecoded text; every position in the format is an offset into it.

    The flags that say what trails each record are checked against the text
    rather than trusted, since wrong ones corrupt every record boundary.
    """
    unpack = _unpacker(pdb, header)
    count = max(0, min(header.text_records, end - header.base - 1))
    candidates = []
    for flags in (header.flags, header.flags & 0xFFFE, header.flags | 1, 0, 2, 3, 1):
        if flags not in candidates:
            candidates.append(flags)
    if header.compression != HUFFMAN and count > 1:
        flags = next((flags for flags in candidates
                      if _flags_fit(pdb, header, unpack, flags, count)), header.flags)
        return _read_text(pdb, header, unpack, flags, end)
    first = None
    for flags in candidates:
        text = _read_text(pdb, header, unpack, flags, end)
        # Text cut off at the cap would come out no better with other flags.
        if len(text) == header.text_length or len(text) >= pdb.text_limit:
            return text
        if first is None:
            first = text
    return first or b""


def _read_index(pdb: _Pdb, index: int) -> tuple[list[tuple[bytes, dict[int, list[int]]]],
                                                dict[int, bytes]]:
    """An index: its entries as `(name, {tag: values})`, and its strings by offset."""
    meta = pdb.record(index)
    count, strings = _u32(meta, 24, NULL), _u32(meta, 52)
    if meta[:4] != b"INDX" or count > MAX_INDEX_RECORDS or strings > MAX_STRING_RECORDS:
        raise ValueError("no index")
    start = _u32(meta, 4)
    if meta[start:start + 4] != b"TAGX":
        start = _u32(meta, 180)
        if meta[start:start + 4] != b"TAGX":
            start = meta.find(b"TAGX")
    if start < 0:
        raise ValueError("no tag table")
    controls = _u32(meta, start + 8)
    stop = min(start + _u32(meta, start + 4), len(meta) - 3)
    tags = [tuple(meta[position:position + 4]) for position in range(start + 12, stop, 4)]
    if controls > 16 or not tags:
        raise ValueError("no tag table")

    labels: dict[int, bytes] = {}
    for number in range(strings):
        record = pdb.record(index + 1 + count + number)
        position = 0
        while position < len(record) and record[position]:
            size, used = _varint(record, position)
            labels[number * 0x10000 + position] = record[position + used:position + used + size]
            position += used + size

    entries: list[tuple[bytes, dict[int, list[int]]]] = []
    for number in range(index + 1, index + 1 + count):
        record = pdb.record(number)
        table = _u32(record, 20)
        if record[:4] != b"INDX" or record[table:table + 4] != b"IDXT":
            continue
        listed = min(_u32(record, 24), (len(record) - table - 4) // 2)
        starts = [_u16(record, table + 4 + 2 * item) for item in range(listed)] + [table]
        for begin, end in zip(starts, starts[1:]):
            if 0 < begin < end <= len(record) and len(entries) < MAX_ENTRIES:
                entries.append(_read_entry(record, begin, end, tags, controls))
    return entries, labels


def _read_entry(record: bytes, begin: int, end: int, tags: list[tuple],
                controls: int) -> tuple[bytes, dict[int, list[int]]]:
    size = record[begin]
    name = record[begin + 1:begin + 1 + size]
    position = begin + 1 + size
    control = record[position:position + controls]
    position += controls
    column = 0
    pending: list[tuple[int, int | None, int | None]] = []
    for tag, values, mask, last in tags:
        if last & 1:
            column += 1
            continue
        if column >= len(control):
            break
        value = control[column] & mask
        if not value:
            continue
        if value == mask and mask & (mask - 1):
            # Every bit of a wide mask set: the size of the values, in bytes, comes first.
            length, used = _varint(record, position)
            position += used
            pending.append((tag, None, length))
        else:
            while not mask & 1:
                mask >>= 1
                value >>= 1
            pending.append((tag, value * values, None))
    found: dict[int, list[int]] = {}
    for tag, wanted, length in pending:
        stop = end if length is None else min(end, position + length)
        numbers = []
        while position < stop and (wanted is None or len(numbers) < wanted):
            number, used = _varint(record, position)
            position += used
            numbers.append(number)
        found[tag] = numbers
    return name, found


def _tag(found: dict[int, list[int]], tag: int, item: int = 0) -> int | None:
    values = found.get(tag, ())
    return values[item] if item < len(values) else None


def _find_kf8(pdb: _Pdb, first: _Header) -> tuple[_Header | None, int]:
    """The KF8 header of the book and the record its older half ends before."""
    if first.is_kf8:
        return first, pdb.count
    candidates = []
    declared = first.number(121)
    if declared is not None and 0 < declared < pdb.count:
        candidates.append(declared)
    candidates.extend(index + 1 for index in range(1, pdb.count - 1) if pdb.size(index) == 8)
    for index in candidates:
        # A second boundary record precedes high-resolution pictures, not a book.
        if pdb.record(index - 1) == b"BOUNDARY":
            try:
                header = _Header(pdb, index)
            except ReaderError:
                continue
            if header.is_kf8:
                return header, index - 1
    return None, pdb.count


class _Volume:
    """An open Kindle file: its headers, and what they say without reading the text."""

    def __init__(self, pdb: _Pdb, path: str) -> None:
        self.pdb = pdb
        self.path = path
        self.first = _Header(pdb)
        self.kf8, self.boundary = _find_kf8(pdb, self.first)
        self.headers = [self.first] if self.kf8 in (None, self.first) else [self.kf8, self.first]

    def refusal(self) -> ReaderError | None:
        """Why the book cannot be opened, when it is whole but not for us."""
        header = self.first
        if self.pdb.record(1).startswith(b"%MOP"):
            return ReaderError("unsupported", _REPLICA)
        if header.compression not in (STORED, PALMDOC, HUFFMAN):
            return ReaderError("unsupported", _PACKED)
        if not self.pdb.plain and header.encryption in (1, 2):
            return ReaderError("drm", _PROTECTED)
        # In plain PalmDoc files the field doubles as a reading position, so
        # only text that does not read as text shows the book is encrypted.
        if header.encryption and _is_noise(self.opening() or b"\x00"):
            return ReaderError("drm", _PROTECTED)
        return None

    def opening(self) -> bytes:
        """The first record of text, inflated; empty when it cannot be."""
        try:
            unpack = _unpacker(self.pdb, self.first)
        except (ReaderError, *_GARBLED):
            return b""
        return _inflate(unpack, self.pdb.record(1), self.first.flags)

    def resource(self, number: int) -> bytes:
        """The record behind a 1-based resource number; empty when there is none.

        Numbers count every record from the first header's first resource,
        pictures or not; in a file with both formats the older half's
        resources come first and are shared.
        """
        index = number - 1
        for start, end in self._resources:
            if 0 <= index < end - start:
                return self.pdb.record(start + index)
            index -= max(0, end - start)
        return b""

    @cached_property
    def _resources(self) -> list[tuple[int, int]]:
        """The runs of records that resource numbers count through."""
        first = self.first
        start = first.base + first.first_resource
        if first.first_resource == NULL or start >= self.boundary:
            # Not stated: the resources begin at the first picture after the text.
            start = next((index for index in range(1 + first.text_records, self.boundary)
                          if self.pdb.record(index).startswith(_PICTURES)), self.boundary)
        ranges = [(start, self.boundary)]
        if self.kf8 not in (None, first) and self.kf8.first_resource != NULL:
            ranges.append((self.kf8.base + self.kf8.first_resource, self.pdb.count))
        return ranges

    def cover(self) -> bytes | None:
        """The cover picture, else the thumbnail the book carries of it."""
        numbers = []
        for header in self.headers:
            offset = header.number(201)
            if offset is not None:
                numbers.append(offset + 1)
        for header in self.headers:
            # Some producers point this at the cover, others at a record of metadata.
            for uri in header.strings(129):
                match = _EMBED.search(uri)
                if match:
                    numbers.append(_base32(match.group(1)))
        for header in self.headers:
            offset = header.number(202)
            if offset is not None:
                numbers.append(offset + 1)
        for number in numbers:
            data = self.resource(number)
            found = images.sniff(data) if data else None
            if found is not None and min(found.width, found.height) >= MIN_COVER:
                return data
        return None

    def describe(self, opening: bytes) -> tuple[str, list[str], str]:
        """Title, authors and language; `opening` is the start of the text."""
        inline = _decode(opening[:INLINE_SPAN], self.first.codec) if not self.first.exth else ""
        title = self._first(503) or next(
            (header.full_name for header in self.headers if header.full_name), "")
        title = title or _inline(inline, "title")
        if not title and len(self.pdb.name) < 31:
            # Longer names were cut to fit the field.
            title = _clean(self.pdb.name.decode("cp1252", "replace").replace("_", " "))
        authors: list[str] = []
        creators = next((names for names in (header.strings(100) for header in self.headers)
                         if names), [_inline(inline, "creator")])
        for creator in creators:
            for name in _SEVERAL_AUTHORS.split(creator):
                # "Unknown" is what converters write when they were told nothing.
                if name and name.lower() != "unknown" and name not in authors:
                    authors.append(name)
        language = (self._first(524) or _inline(inline, "language")).lower().replace("_", "-")
        if not language:
            language = _LANGUAGES.get(self.headers[0].locale & 0xFF, "")
        return title or title_from_filename(self.path), authors, language

    def _first(self, kind: int) -> str:
        for header in self.headers:
            values = header.strings(kind)
            if values:
                return values[0]
        return ""


def _inline(text: str, name: str) -> str:
    """Metadata written into the text's own head, as files without an EXTH block do."""
    match = _INLINE[name].search(text)
    return _clean(match.group(1)) if match else ""


def _snap(raw: bytes, position: int, utf8: bool, floor: int) -> int:
    """The nearest place at or before `position` where markup can be inserted.

    Never inside a tag, a character reference or a UTF-8 sequence, and never
    before `floor`.
    """
    size = len(raw)
    position = min(max(position, floor), size)
    if position == size:
        return position
    opened = raw.rfind(b"<", max(0, position - TAG_SPAN), position)
    if opened != -1 and raw.rfind(b">", opened, position) == -1:
        closed = raw.find(b">", position, opened + TAG_SPAN)
        reopened = raw.find(b"<", position, opened + TAG_SPAN)
        if closed != -1 and (reopened == -1 or closed < reopened):
            return max(opened, floor)
    ampersand = raw.rfind(b"&", max(0, position - 12), position)
    if ampersand != -1:
        semicolon = raw.find(b";", ampersand, ampersand + 14)
        if semicolon >= position and _REFERENCE.fullmatch(raw, ampersand, semicolon + 1):
            return max(ampersand, floor)
    if utf8:
        steps = 0
        while position > floor and raw[position] & 0xC0 == 0x80 and steps < 3:
            position -= 1
            steps += 1
    return position


def _floor(raw: bytes, limit: int) -> int:
    """Where the visible text starts: anchors put in the head would be read as text."""
    found = _BODY.search(raw, 0, limit) or _HEAD_END.search(raw, 0, limit)
    return found.end() if found else 0


def _mark(raw: bytes, marks: dict[int, list[int]], offsets: list[int], start: int, end: int,
          base: int = 0) -> bytes:
    """`raw[start:end]` with an empty anchor `pos<n>` at each marked offset.

    `marks` maps an offset in `raw` to the positions that snapped there and
    `offsets` lists its keys in order; anchors are named by position plus `base`.
    """
    stop = len(offsets) if end == len(raw) else bisect.bisect_left(offsets, end)
    pieces = []
    last = start
    for offset in offsets[bisect.bisect_left(offsets, start):stop]:
        pieces.append(raw[last:offset])
        pieces.extend(b'<a id="pos%d"></a>' % (base + position)
                      for position in sorted(marks[offset]))
        last = offset
    pieces.append(raw[last:end])
    return b"".join(pieces)


def _listing(entries: list[_Entry]) -> list[tuple[_Entry, int]]:
    """Contents entries in reading order, each with its depth.

    The table stores them level by level, so its own order is not the reading
    order: nest by the parent links, or failing those sort by position.
    """
    if not any(entry.parent is not None for entry in entries):
        if all(entry.target is not None for entry in entries):
            entries = sorted(entries, key=lambda entry: entry.target)
        return [(entry, entry.depth) for entry in entries]
    children: dict[int | None, list[int]] = {}
    for number, entry in enumerate(entries):
        parent = entry.parent
        if parent is None or not 0 <= parent < len(entries) or parent == number:
            parent = None
        children.setdefault(parent, []).append(number)
    listed: list[tuple[_Entry, int]] = []
    seen: set[int] = set()
    pending = [(number, 0) for number in reversed(children.get(None, []))]
    while pending:
        number, depth = pending.pop()
        if number in seen:
            continue
        seen.add(number)
        listed.append((entries[number], depth))
        inner = min(depth + 1, MAX_TOC_DEPTH)
        pending.extend((child, inner) for child in reversed(children.get(number, [])))
    # Entries whose parents form a ring are reached from no root.
    listed.extend((entry, 0) for number, entry in enumerate(entries) if number not in seen)
    return listed


def _page_links(markup: str) -> list[tuple[str, str, str, int]]:
    """The internal links of a contents page: `(section, fragment, label, nesting)`."""
    links = []
    seen = set()
    level = 0
    for match in _LINK.finditer(markup):
        if match.group(2) is None:
            level = max(0, level - 1) if match.group(1) else level + 1
            continue
        label = _clean(_TAG.sub(" ", match.group(4)))
        key = (match.group(2), match.group(3), label)
        if label and key not in seen:
            seen.add(key)
            links.append((*key, level))
    return links


def _ranked(links: list[tuple[str, str, str, int]]) -> list[dict]:
    """Contents entries from a page's links, their depth from how far each is nested."""
    rank = {level: depth for depth, level in enumerate(sorted({link[3] for link in links}))}
    return [{"t": label, "d": rank[level], "name": name, "fragment": fragment}
            for name, fragment, label, level in links]


def _name(number: int) -> str:
    return "part%04d" % number


class _Mobi6:
    """The older format: one run of loose HTML, addressed by byte offset."""

    def __init__(self, volume: _Volume) -> None:
        header = volume.first
        self.flows: list[bytes] = []
        self.raw = raw = _extract_text(volume.pdb, header, volume.boundary)
        self.codec = _settle_codec(raw, header.codec)
        self.documents: list[tuple[str, str]] = []
        self.entries: list[_Entry] = []
        self.sections: dict[int, int] = {}
        self._guide: int | None = None
        if not _MARKUP.search(raw):
            lines = _decode(raw, self.codec).splitlines()
            self.documents.append(
                (_name(0), "".join("<p>%s</p>" % escape(line) for line in lines if line.strip())))
            return
        floor = _floor(raw, HEAD_SPAN)
        targets = self._targets(volume, floor)
        utf8 = self.codec == "utf-8"
        marks: dict[int, list[int]] = {}
        for target in targets:
            marks.setdefault(_snap(raw, target, utf8, floor), []).append(target)
        # The head holds metadata that would be read as text; only its styles matter.
        styles = b"".join(_STYLE.findall(raw, 0, floor))
        cuts = sorted({floor, *(match.start() for match in _PAGEBREAK.finditer(raw, floor))})
        del cuts[MAX_SECTIONS:]
        offsets = sorted(marks)
        for offset, positions in marks.items():
            section = bisect.bisect_right(cuts, offset) - 1
            self.sections.update((position, section) for position in positions)
        for number, (start, end) in enumerate(zip(cuts, cuts[1:] + [len(raw)])):
            piece = _mark(raw, marks, offsets, start, end)
            if not number:
                piece = styles + piece
            piece = _FILEPOS_LINK.sub(self._link, piece)
            piece = _IMAGE.sub(_picture_source, piece)
            self.documents.append((_name(number), _decode(piece, self.codec)))

    def _targets(self, volume: _Volume, floor: int) -> set[int]:
        """Every offset of the text that a link or a contents entry points at."""
        raw, header = self.raw, volume.first
        targets = {int(match.group(1)) for match in _FILEPOS.finditer(raw)}
        if header.ncx_index != NULL:
            try:
                entries, labels = _read_index(volume.pdb, header.base + header.ncx_index)
            except _GARBLED:
                entries, labels = [], {}
            for _, found in entries:
                label = labels.get(_tag(found, 3), b"")
                position = _tag(found, 1)
                if position is not None and position <= len(raw):
                    self.entries.append(_Entry(_clean(label.decode(self.codec, "replace")),
                                               _tag(found, 4) or 0, _tag(found, 21), position))
            targets.update(entry.target for entry in self.entries)
        reference = _GUIDE_TOC.search(raw, 0, floor or HEAD_SPAN)
        position = _FILEPOS.match(reference.group()) if reference else None
        if position:
            self._guide = int(position.group(1))
            targets.add(self._guide)
        targets = {target for target in targets if target <= len(raw)}
        if len(targets) > MAX_TARGETS:
            targets = set(sorted(targets)[:MAX_TARGETS])
        return targets

    def _link(self, match: re.Match) -> bytes:
        position = int(match.group(2))
        section = self.sections.get(position)
        if section is None:
            return match.group(1)
        return b'%shref="%s#pos%d"' % (match.group(1), _name(section).encode(), position)

    def _contents_page(self) -> list[tuple[str, str, str, int]]:
        """The links of the contents page the guide points at.

        The page runs from that position to the end of its section, or of the
        first later section that has links when its own has none.
        """
        first = self.sections.get(self._guide) if self._guide is not None else None
        if first is None:
            return []
        marker = '<a id="pos%d"></a>' % self._guide
        for _, markup in self.documents[first:first + CONTENTS_PAGES]:
            links = _page_links(markup[max(0, markup.find(marker)):])
            if links:
                return links
        return []

    def candidates(self) -> list[list[dict]]:
        """The book's declared contents, best first."""
        page = self._contents_page()
        found = []
        if self.entries:
            listed = _listing(self.entries)
            # Some producers write a flat index for a book whose contents page is nested.
            depths: dict[str, int] = {}
            if all(not depth for _, depth in listed):
                depths = {entry["fragment"]: entry["d"] for entry in _ranked(page)}
                known = sum("pos%d" % entry.target in depths for entry, _ in listed)
                if known * 5 < len(listed) * 4 or not any(depths.values()):
                    depths = {}
            found.append([
                {"t": entry.label, "d": depths.get("pos%d" % entry.target, depth),
                 "name": _name(self.sections[entry.target]), "fragment": "pos%d" % entry.target}
                for entry, depth in listed if entry.target in self.sections])
        if len(page) >= 2:
            found.append(_ranked(page))
        return found


def _picture_source(match: re.Match) -> bytes:
    """An `<img>` tag whose record numbers are given as a `src` html.py can ask for."""
    numbers = {(kind or b"").lower(): int(number)
               for kind, number in _RECINDEX.findall(match.group())}
    if not numbers:
        return match.group()
    wanted = ",".join(str(numbers[kind]) for kind in (b"hi", b"", b"lo") if kind in numbers)
    return b'<img src="recindex:%s" %s' % (wanted.encode(), match.group()[4:])


class _Kf8:
    """The newer format: XHTML documents cut into skeletons and fragments."""

    def __init__(self, volume: _Volume) -> None:
        self.pdb = volume.pdb
        self.header = header = volume.kf8
        self.raw = raw = _extract_text(volume.pdb, header, volume.pdb.count)
        self.codec = _settle_codec(raw, header.codec)
        spans = self._flow_spans()
        self.flows = [raw[start:end] for start, end in spans[1:]]
        text = raw[spans[0][0]:spans[0][1]]
        self.fragments = self._read_fragments()
        self.parts = self._assemble(text)
        self._starts = [part.start for part in self.parts]
        self._tops = {part.skeleton: part.start for part in self.parts
                      if part.skeleton is not None}
        entries, labels = self._index(header.ncx_index)
        self.entries = [
            _Entry(_clean(labels.get(_tag(found, 3), b"").decode(self.codec, "replace")),
                   _tag(found, 4) or 0, _tag(found, 21),
                   self._position(_tag(found, 6), _tag(found, 6, 1)))
            for _, found in entries]
        self._guide = next(
            (self._position(_tag(found, 6) if 6 in found else _tag(found, 3), _tag(found, 6, 1))
             for name, found in self._index(header.guide_index)[0]
             if name.strip(b"\x00").lower() == b"toc"), None)
        self.documents = self._documents()

    def _index(self, relative: int) -> tuple[list, dict[int, bytes]]:
        """One of the book's indexes; a missing or damaged one is simply empty."""
        if relative == NULL:
            return [], {}
        try:
            return _read_index(self.pdb, self.header.base + relative)
        except _GARBLED:
            return [], {}

    def _flow_spans(self) -> list[tuple[int, int]]:
        """Where each flow lies in the text: the documents first, then stylesheets and SVG."""
        size = len(self.raw)
        whole = [(0, size)]
        if self.header.flow_index == NULL:
            return whole
        record = self.pdb.record(self.header.base + self.header.flow_index)
        table, count = _u32(record, 4), _u32(record, 8)
        if record[:4] != b"FDST" or not count or table + 8 * count > len(record):
            return whole
        # Only the starts are trusted; each flow ends where the next begins.
        starts = [min(_u32(record, table + 8 * number), size) for number in range(count)]
        spans = []
        for start, following in zip(starts, starts[1:] + [size]):
            spans.append((start, max(start, following)))
        return spans

    def _read_fragments(self) -> list[_Fragment]:
        entries, labels = self._index(self.header.fragment_index)
        return [_Fragment(int(name) if name.isdigit() else None, labels.get(_tag(found, 2), b""),
                          _tag(found, 3), _tag(found, 6) or 0, _tag(found, 6, 1) or 0)
                for name, found in entries]

    def _assemble(self, text: bytes) -> list[_Part]:
        """Put each document back together: its skeleton with its fragments inserted.

        A part is exactly as long as the span of text it was made from, so the
        parts tile the text and a position in one is a position in the other.
        A skeleton that starts inside an earlier part is left out: the parts
        together are then never larger than the text.
        """
        listed = self._index(self.header.skeleton_index)[0][:MAX_SECTIONS]
        if not listed:
            return [_Part(0, len(text), text, None)]
        skeletons = []
        used = 0
        for number, (_, found) in enumerate(listed):
            start, length, count = _tag(found, 6), _tag(found, 6, 1), _tag(found, 1) or 0
            if start is not None and length is not None and start + length <= len(text):
                skeletons.append((start, length, number, self.fragments[used:used + count]))
            used += count
        # The table's order says whose each fragment is; the text's order is the reading order.
        skeletons.sort(key=lambda skeleton: skeleton[0])
        parts = []
        work = source = 0
        for start, length, number, fragments in skeletons:
            if start < source:
                continue
            page = bytearray(text[start:start + length])
            source = start + length
            for fragment in fragments:
                data = text[source:source + fragment.length]
                source += fragment.length
                where, searched = _insertion(page, fragment, start)
                # An insertion costs the bytes searched for its place and the bytes moved
                # aside: next to nothing for fragments that follow one another.
                work += searched + len(page) - where
                if work > MAX_ASSEMBLY:
                    raise ValueError("too much to assemble")
                page[where:where] = data
            parts.append(_Part(start, source, bytes(page), number))
        return parts

    def _position(self, fragment: int | None, offset: int | None) -> int | None:
        """The place in the text that a fragment number and an offset into it name."""
        if fragment is None or not 0 <= fragment < len(self.fragments):
            return None
        found = self.fragments[fragment]
        if found.insert is not None:
            position = found.insert + (offset or 0)
            if self._locate(position) is not None:
                return position
        # Out of every part: settle for the top of the fragment's own document.
        return self._tops.get(found.skeleton)

    def _locate(self, position: int) -> int | None:
        """The part a position falls in."""
        number = bisect.bisect_right(self._starts, position) - 1
        if number < 0 or position >= self.parts[number].end:
            return None
        return number

    def _documents(self) -> list[tuple[str, str]]:
        utf8 = self.codec == "utf-8"
        targets: dict[bytes, int | None] = {}
        marks: list[dict[int, list[int]]] = [{} for _ in self.parts]
        for part in self.parts:
            for match in _POSITION.finditer(part.data):
                if match.group() not in targets and len(targets) < MAX_TARGETS:
                    targets[match.group()] = self._position(_base32(match.group(1)),
                                                            _base32(match.group(2)))
        wanted = {entry.target for entry in self.entries} | set(targets.values()) | {self._guide}
        self.sections: dict[int, int] = {}
        floors = [_floor(part.data, len(part.data)) for part in self.parts]
        for position in wanted - {None}:
            number = self._locate(position)
            if number is None:
                continue
            part = self.parts[number]
            offset = _snap(part.data, position - part.start, utf8, floors[number])
            marks[number].setdefault(offset, []).append(position - part.start)
            self.sections[position] = number

        def link(match: re.Match) -> bytes:
            position = targets.get(match.group())
            if position not in self.sections:
                return match.group()
            return b"%s#pos%d" % (_name(self.sections[position]).encode(), position)

        documents = []
        for number, part in enumerate(self.parts):
            data = _mark(part.data, marks[number], sorted(marks[number]), 0, len(part.data),
                         part.start)
            documents.append((_name(number), _decode(_POSITION.sub(link, data), self.codec)))
        return documents

    def _entry(self, label: str, depth: int, position: int | None) -> dict:
        if position not in self.sections:
            return {"t": label, "d": depth, "name": _NOWHERE, "fragment": ""}
        return {"t": label, "d": depth, "name": _name(self.sections[position]),
                "fragment": "pos%d" % position}

    def candidates(self) -> list[list[dict]]:
        """The book's declared contents, best first."""
        found = []
        if self.entries:
            found.append([self._entry(entry.label, depth, entry.target)
                          for entry, depth in _listing(self.entries)])
        if self._guide in self.sections:
            page = _page_links(self.documents[self.sections[self._guide]][1])
            if len(page) >= 2:
                found.append(_ranked(page))
        return found


def _insertion(page: bytearray, fragment: _Fragment, start: int) -> tuple[int, int]:
    """Where in the growing document a fragment goes, and how many bytes were searched for it.

    A stated position that is missing, outside the document or inside a tag
    is replaced by the element the fragment names, else by the nearest end.
    """
    where = None if fragment.insert is None else fragment.insert - start
    searched = 0
    if where is not None and 0 <= where <= len(page):
        opened, closed = page.rfind(b"<", 0, where), page.rfind(b">", 0, where)
        searched = where - min(opened, closed)
        if opened <= closed:
            return where, searched
    selector = _SELECTOR.match(fragment.selector)
    if selector:
        searched += len(page)
        element = re.search(rb"<[^<>]*\said\s*=\s*[\"']%s[\"'][^<>]*>" % selector.group(1), page)
        if element and element.end() + fragment.offset <= len(page):
            return element.end() + fragment.offset, searched
    return (len(page) if where is None else min(max(where, 0), len(page))), searched


class _Resources:
    """What html.py asks of the book: stylesheets, pictures and link targets."""

    def __init__(self, text: _Mobi6 | _Kf8, volume: _Volume, folder: str,
                 pictures: dict[int, dict]) -> None:
        self.text = text
        self.volume = volume
        self.folder = folder
        self.names = frozenset(name for name, _ in text.documents)
        self.pictures = pictures
        self._absent: set[int] = set()
        # A book may name one flow from every document: each is read once.
        self._sheets: dict[int, str | None] = {}
        self._wrapped: dict[int, int | None] = {}

    def _flow(self, href: str) -> int | None:
        """Which of the book's flows an address names, when it names one that has anything."""
        match = _FLOW.search(href)
        number = _base32(match.group(1)) if match else 0
        if 0 < number <= len(self.text.flows) and self.text.flows[number - 1]:
            return number - 1
        return None

    def stylesheet(self, href: str, base: str) -> str | None:
        flow = self._flow(href)
        if flow is None:
            return None
        if flow not in self._sheets:
            self._sheets[flow] = _decode(self.text.flows[flow], self.text.codec)
        return self._sheets[flow]

    def _embedded(self, href: str) -> int | None:
        """The resource number behind a `kindle:embed` address, or behind a flow that holds one."""
        flow = self._flow(href)
        if flow is None:
            match = _EMBED.search(href)
            return _base32(match.group(1)) if match else None
        if flow not in self._wrapped:
            # A flow shown as a picture is an SVG page wrapped around the real one.
            match = _EMBED.search(self.text.flows[flow].decode("latin-1"))
            self._wrapped[flow] = _base32(match.group(1)) if match else None
        return self._wrapped[flow]

    def image(self, href: str, base: str) -> dict | None:
        match = _RECORD.match(href)
        if match:
            numbers = [int(number) for number in match.group(1).split(",") if number]
        else:
            number = self._embedded(href)
            numbers = [] if number is None else [number]
        for number in numbers:
            picture = self._numbered(number)
            if picture is not None:
                return picture
        return None

    def document(self, href: str, base: str) -> str | None:
        name = href.partition("#")[0]
        return name if name in self.names else None

    def _numbered(self, number: int) -> dict | None:
        """The picture with this resource number, extracted on first use."""
        if number not in self.pictures and number not in self._absent:
            data = self.volume.resource(number)
            found = images.sniff(data) if data else None
            if found is None:
                self._absent.add(number)
            else:
                path = os.path.join(self.folder, "%04d%s" % (len(self.pictures) + 1, found.ext))
                write_atomic(path, data)
                self.pictures[number] = {"src": path, "w": found.width, "h": found.height,
                                         "al": 1 if found.alpha else 0}
        return self.pictures.get(number)


def _contents(candidates: list[list[dict]], builder: BookBuilder) -> list[dict]:
    """The first declared contents that lead to enough places, else the one leading to most."""
    best: list[dict] = []
    most = -1
    for entries in candidates:
        places = set()
        for entry in entries:
            index = builder.anchor_index(entry["name"], entry["fragment"])
            if index is not None:
                places.add(index)
        if len(places) >= toc.MIN_ENTRIES:
            return entries
        if len(places) > most:
            best, most = entries, len(places)
    return best


def _open(path: str) -> _Pdb:
    try:
        return _Pdb(path)
    except FileNotFoundError:
        raise ReaderError("missing", _MISSING) from None
    except OSError:
        raise ReaderError("corrupt", _UNREADABLE) from None


def read_meta(path: str) -> Meta:
    """Title, authors, language and cover, without reading the book's text."""
    try:
        with _open(path) as pdb:
            volume = _Volume(pdb, path)
            refusal = volume.refusal()
            opening = b"" if volume.first.exth or refusal else volume.opening()
            title, authors, language = volume.describe(opening)
            return Meta(title, authors, language, volume.cover(),
                        refusal.message if refusal else "")
    except ReaderError as error:
        if error.code != "unsupported":
            raise
        return Meta(title_from_filename(path), [], "", None, error.message)
    except _GARBLED:
        raise ReaderError("corrupt", _DAMAGED) from None


def convert(path: str, out_dir: str) -> Book:
    """Convert the whole book; its pictures are written to `<out_dir>/img/`."""
    with _open(path) as pdb:
        try:
            volume = _Volume(pdb, path)
            refusal = volume.refusal()
        except _GARBLED:
            raise ReaderError("corrupt", _DAMAGED) from None
        if refusal is not None:
            raise refusal
        readers: list[type] = []
        if volume.kf8 is not None:
            readers.append(_Kf8)
        if volume.kf8 is not volume.first:
            readers.append(_Mobi6)
        # Kept across attempts, so a picture is written once however the book ends up read.
        pictures: dict[int, dict] = {}
        for reader in readers:
            # A file with both formats is read from the newer; the older stands in if that fails.
            try:
                text = reader(volume)
            except (ReaderError, *_GARBLED):
                continue
            builder = BookBuilder()
            resources = _Resources(text, volume, os.path.join(out_dir, "img"), pictures)
            for name, markup in text.documents:
                html.convert_document(builder, markup, name, resources)
            if builder.blocks:
                break
        else:
            raise ReaderError("corrupt", _DAMAGED)
        builder.finish()
        title, authors, language = volume.describe(text.raw)
        contents = toc.build_toc(_contents(text.candidates(), builder), builder, title)
        return Book(title, display_author(authors), language, list(builder.sections), contents,
                    builder.blocks, authors)
