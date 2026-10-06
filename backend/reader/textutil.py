"""Text helpers: byte decoding, entities, whitespace, sort keys, atomic writes."""

from __future__ import annotations

import codecs
import os
import re
import stat
import tempfile
import unicodedata
from html.entities import html5

_SNIFF = 50 * 1024

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),  # before UTF-16: its BOM starts the same way
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

_XML_ENCODING = re.compile(rb"<\?xml[^>]{0,200}?encoding\s*=\s*[\"']\s*([\w.:-]+)", re.I)
_META_CHARSET = re.compile(rb"<meta[^>]{0,400}?charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)

# Declarations that understate what the bytes really are: Word exports GBK
# labelled gb2312, and "latin-1" or "ascii" pages are cp1252 in practice.
_WIDER = {
    "gb2312": "gbk",
    "gb_2312-80": "gbk",
    "ascii": "cp1252",
    "iso8859-1": "cp1252",
    "mac-centraleurope": "mac-latin2",
}
_MULTIBYTE = frozenset({
    "gbk", "gb18030", "hz", "big5", "big5hkscs", "cp950", "shift_jis", "cp932",
    "shift_jis_2004", "shift_jisx0213", "euc_jp", "euc_jis_2004", "euc_jisx0213",
    "euc_kr", "cp949", "johab",
})

_BY_LANGUAGE = {
    # EUC-JP first: Shift-JIS decodes most EUC-JP text without complaint.
    "ja": ("euc_jp", "cp932"),
    "ko": ("cp949",),
    "th": ("cp874",),
    "vi": ("cp1258",),
    "el": ("cp1253",),
    "tr": ("cp1254",),
    "he": ("cp1255",),
    "ar": ("cp1256",),
    "fa": ("cp1256",),
    **dict.fromkeys(("ru", "uk", "bg", "be", "sr", "mk", "kk"), ("cp1251", "koi8_r")),
    **dict.fromkeys(("pl", "cs", "sk", "hu", "sl", "hr", "ro"), ("cp1250",)),
    **dict.fromkeys(("lt", "lv", "et"), ("cp1257",)),
}
_TRADITIONAL_CHINESE = ("tw", "hk", "mo", "hant")

# cp1252 leaves five bytes undefined; they stay as the C1 controls Latin-1
# gives them, so decoding can never fail.
_CP1252 = {
    byte: bytes([byte]).decode("cp1252")
    for byte in range(0x80, 0xA0)
    if byte not in (0x81, 0x8D, 0x8F, 0x90, 0x9D)
}

_XML_DECLARATION = re.compile(r"<\?xml\s[^>]{0,200}?\?>", re.I)
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]")
_REFERENCE = re.compile(r"&(?:#([0-9]{1,10})|#[xX]([0-9a-fA-F]{1,8})|([A-Za-z][A-Za-z0-9]{0,31}));")
_XML_NAMED = frozenset({"amp", "lt", "gt", "quot", "apos"})
_XML_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"})
_MISSPELLED = {"squot": "'", "hellips": "\u2026"}

_SPACE = re.compile(r"[ \t\n\r\f\v]+")
_DIGITS = re.compile(r"([0-9]{1,18})")
_LEADING_PUNCTUATION = re.compile(r"^[\W_]+")
_ARTICLE = re.compile(r"^(?:the|an|a)\s+(?=\S)")


def declared_encoding(raw: bytes) -> str:
    """The encoding a document claims for itself, or "" when it claims none."""
    head = raw[:_SNIFF]
    match = _XML_ENCODING.search(head) or _META_CHARSET.search(head)
    return match.group(1).decode("ascii", "replace") if match else ""


def decode_bytes(raw: bytes, *, kind: str = "content", language: str = "", declared: str = "") -> str:
    """Decode a document of unknown encoding; never fails.

    Valid UTF-8 outranks any declaration. Otherwise `kind` decides how far a
    declaration is trusted: "package" files (metadata) try a declared
    multi-byte encoding first, "content" only after checking that the bytes
    are not UTF-8 with a few stray ones. `declared` is an encoding named
    outside the document; `language` is the book's, used as a last hint.
    The result has LF line endings and no byte-order mark.
    """
    text = _decode(bytes(raw), kind, language, declared)
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode(raw: bytes, kind: str, language: str, declared: str) -> str:
    for mark, encoding in _BOMS:
        if raw.startswith(mark):
            body = raw[len(mark):]
            if encoding != "utf-8":
                return body.decode(encoding, "replace")
            raw = body
            break
    else:
        text = _strict(raw, _unmarked_utf16(raw))
        if text is not None:
            return text

    codec = _codec(declared) or _codec(declared_encoding(raw))
    utf8 = _strict(raw, "utf-8")
    if codec:
        # Pure ASCII reads the same in every codec except the stateful 7-bit
        # ones (ISO-2022-JP, HZ), which only their declaration reveals. A
        # package file that is not UTF-8 is also taken at its word when it
        # names a multi-byte codec, since those reject text that is not
        # theirs; a single-byte codec accepts anything, so it waits its turn.
        believed = raw.isascii() or (kind == "package" and utf8 is None and codec in _MULTIBYTE)
        if believed:
            text = _strict(raw, codec)
            if text is not None:
                return text
    if utf8 is not None:
        return utf8

    # A few stray bytes must not turn a UTF-8 file into mojibake. Real legacy
    # text scores far below this bar: single-byte codecs yield no valid
    # multi-byte sequences at all, East Asian ones about one in five.
    loose = raw.decode("utf-8", "replace")
    bad = loose.count("\ufffd")
    good = len(loose) - len(loose.encode("ascii", "ignore")) - bad
    if good > 0 and good >= 3 * bad:
        return loose

    for candidate in (codec, *_language_codecs(language)):
        text = _strict(raw, candidate)
        if text is not None:
            return text
    return raw.decode("latin-1").translate(_CP1252)


def _strict(raw: bytes, codec: str) -> str | None:
    if not codec:
        return None
    try:
        return raw.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None


def _unmarked_utf16(raw: bytes) -> str:
    """UTF-16 without a byte-order mark shows as "<" followed by a NUL."""
    head = raw[:4]
    if len(head) == 4:
        if head[0] == 0x3C and head[1] == 0 and head[2] and head[3] == 0:
            return "utf-16-le"
        if head[0] == 0 and head[1] == 0x3C and head[2] == 0 and head[3]:
            return "utf-16-be"
    return ""


def _codec(label: str) -> str:
    """The codec to use for a declared encoding; "" when it tells us nothing."""
    label = label.strip().lower()
    if not label:
        return ""
    for spelling in (label, label.removeprefix("x-")):
        spelling = _WIDER.get(spelling, spelling)
        try:
            info = codecs.lookup(spelling)
        except LookupError:
            continue
        name = _WIDER.get(info.name, info.name)
        # UTF-8 is tried regardless, and UTF-16/32 without a byte-order mark
        # or the tell-tale NULs is a false declaration.
        return "" if name.startswith("utf") else name
    return ""


def _language_codecs(language: str) -> tuple[str, ...]:
    tags = language.strip().lower().replace("_", "-").split("-")
    if tags[0] == "zh":
        if any(tag in _TRADITIONAL_CHINESE for tag in tags[1:]):
            return ("big5hkscs", "gb18030")
        return ("gb18030", "big5hkscs")
    return _BY_LANGUAGE.get(tags[0], ())


def strip_declarations(text: str) -> str:
    """Remove XML declarations, whose encoding no longer describes the text."""
    return _XML_DECLARATION.sub("", text[:_SNIFF]) + text[_SNIFF:]


def strip_illegal_xml(text: str) -> str:
    """Remove the characters XML forbids (control characters, surrogates)."""
    return _ILLEGAL_XML.sub("", text)


def replace_entities(text: str) -> str:
    """Turn character references into the characters they name.

    The five XML ones stay escaped, however they were written, so the result
    is still markup. Unknown names are left as written; references to
    characters XML forbids are dropped.
    """
    return _REFERENCE.sub(_resolve, text)


def _resolve(match: re.Match) -> str:
    decimal, hexadecimal, name = match.groups()
    if name is not None:
        if name in _XML_NAMED:
            return match.group(0)
        value = html5.get(name + ";") or _MISSPELLED.get(name)
        return match.group(0) if value is None else value.translate(_XML_ESCAPES)
    number = int(decimal) if decimal is not None else int(hexadecimal, 16)
    if 0x80 <= number <= 0x9F:
        return _CP1252.get(number, "")
    if number > 0x10FFFF:
        return ""
    return _ILLEGAL_XML.sub("", chr(number)).translate(_XML_ESCAPES)


def collapse_space(text: str) -> str:
    """Collapse whitespace as HTML does; no-break spaces are content and stay."""
    return _SPACE.sub(" ", text).strip(" ")


def natural_key(text: str) -> tuple:
    """Sort key ordering digit runs by value: "ch2" before "ch10"."""
    parts = _DIGITS.split(text.casefold())
    return (tuple(int(part) if index % 2 else part for index, part in enumerate(parts)), text)


def title_sort_key(title: str) -> str:
    """Sort key for a title: case, accents, leading articles and punctuation ignored."""
    decomposed = unicodedata.normalize("NFKD", title).casefold()
    plain = collapse_space("".join(ch for ch in decomposed if not unicodedata.combining(ch)))
    key = _LEADING_PUNCTUATION.sub("", plain)
    key = _LEADING_PUNCTUATION.sub("", _ARTICLE.sub("", key))
    return key or plain


def keep_private(directory: str) -> None:
    """Close a directory that exists to everyone but its owner."""
    try:
        if stat.S_IMODE(os.stat(directory).st_mode) & 0o077:
            os.chmod(directory, 0o700)
    except OSError:
        pass


def write_atomic(path: str, data: bytes) -> None:
    """Write a file so that readers only ever see the old or the new content.

    What is written is the text and pictures of a person's books: the file,
    and any directory made for it, is theirs alone.
    """
    directory = os.path.dirname(os.path.abspath(path))
    mask = os.umask(0o077)
    try:
        os.makedirs(directory, exist_ok=True)
    finally:
        os.umask(mask)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
