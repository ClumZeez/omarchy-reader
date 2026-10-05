"""The library: finding books, telling formats apart, keys, and the cache."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from types import ModuleType

from . import CONVERTER_VERSION, images
from .archive import Archive
from .blocks import clean, plain_text
from .errors import ReaderError
from .textutil import collapse_space, title_sort_key, write_atomic

BOOK_VERSION = 1
INDEX_VERSION = 1
CACHE_LIMIT = 3 * 512 * 1024 * 1024
# The reader parses a converted book in one go, on the thread that also draws
# the bar. The largest real book met comes to a seventh of this.
BOOK_LIMIT = 48 * 1024 * 1024
# Titles and names are shown on one line of a tile; no real one is longer.
NAME_LIMIT = 300
AUTHOR_LIMIT = 20
MAX_DEPTH = 8
# How often a scan that is reading new books records what it has so far.
CHECKPOINT_SECONDS = 5.0

DEFAULT_DIRS = (
    "~/Books", "~/books", "~/Documents/Books", "~/Documents/books", "~/Documents/EPUB",
    "~/Documents/Ebooks", "~/Documents/ebooks", "~/Documents/eBooks", "~/Calibre Library",
    "~/Documents/Calibre Library",
)

# Longest first, so that "x.fb2.zip" is FictionBook and "x.kepub.epub" loses both parts.
_EXTENSIONS = (
    (".kepub.epub", "epub"), (".fb2.zip", "fb2"), (".kepub", "epub"), (".xhtml", "html"),
    (".epub", "epub"), (".mobi", "mobi"), (".azw3", "mobi"), (".html", "html"),
    (".djvu", "djvu"), (".prc", "mobi"), (".azw", "mobi"), (".kf8", "mobi"), (".fb2", "fb2"),
    (".fbz", "fb2"), (".txt", "text"), (".htm", "html"), (".cbz", "comic"), (".pdf", "pdf"),
)
_MODULES = {"epub": "epub", "mobi": "mobi", "fb2": "fb2", "text": "text", "html": "text",
            "comic": "comic"}
_EXTERNAL = {
    "pdf": "PDF files open in your PDF viewer, not in Reader.",
    "djvu": "DjVu files open in your document viewer, not in Reader.",
}
_NOT_YET = {
    "epub": "EPUB books can't be opened by this version of Reader.",
    "mobi": "Kindle books can't be opened by this version of Reader.",
    "fb2": "FictionBook files can't be opened by this version of Reader.",
    "text": "Text files can't be opened by this version of Reader.",
    "html": "Web pages can't be opened by this version of Reader.",
    "comic": "Comic archives can't be opened by this version of Reader.",
}
_NOT_A_BOOK = "This file isn't a book Reader can open."
_MISSING = "That book is no longer there."
_UNREADABLE = "This book's file can't be read."
_INTERNAL = "Something went wrong while reading this book."
_TOO_LARGE = "This book is too large to open."

_IMAGE_NAMES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
_HTML_START = re.compile(rb"<!doctype\s+html|<html[\s>]")
_SAMPLE_EDGE = 64 * 1024
_SAMPLE = 4 * 1024
_HEAD = 4096

_PARTICLES = frozenset((
    "von", "van", "de", "del", "della", "di", "da", "du", "le", "la", "den", "der", "ter",
    "ten", "st", "st.", "mc", "mac", "al", "el", "bin", "ibn", "dos", "das",
))
_SUFFIXES = frozenset((
    "jr", "sr", "ii", "iii", "iv", "phd", "md", "esq", "dds", "ed", "eds", "inc", "ltd", "llc",
    "translator", "editor",
))


def title_from_filename(path: str) -> str:
    """A presentable title from a file name: no extension, no underscores."""
    name = os.path.basename(path)
    lowered = name.lower()
    for extension, _ in _EXTENSIONS:
        if lowered.endswith(extension) and len(name) > len(extension):
            name = name[:-len(extension)]
            break
    else:
        stem, extension = os.path.splitext(name)
        if stem and len(extension) <= 6:
            name = stem
    return collapse_space(name.replace("_", " ")) or collapse_space(os.path.basename(path))


def _name(text: object) -> str:
    """A title or a person's name as the library shows it: one line, nothing invisible."""
    name = plain_text(clean(str(text or "")[:8 * NAME_LIMIT]))
    if len(name) > NAME_LIMIT:
        name = name[:NAME_LIMIT].rstrip() + "…"
    return name


def display_author(authors: list[str]) -> str:
    """The author line: names in natural order, joined with ", "."""
    names = []
    for author in authors[:8 * AUTHOR_LIMIT]:
        name = _natural_order(_name(author))
        if name and name not in names:
            names.append(name)
    line = ", ".join(names[:AUTHOR_LIMIT])
    if len(line) > NAME_LIMIT:
        line = line[:NAME_LIMIT].rstrip(" ,") + "…"
    return line


def _natural_order(name: str) -> str:
    """"Lewis, C. S." → "C. S. Lewis", when the name cannot be anything else."""
    family, comma, given = name.partition(",")
    family, given = family.strip(), given.strip()
    if not comma or not family or not given or "," in given or any(ch.isdigit() for ch in name):
        return name
    given_words = given.split()
    family_words = family.split()
    if len(given_words) > 3 or len(given) > 30:
        return name
    if any(word.strip(".").casefold() in _SUFFIXES for word in given_words):
        return name
    # Two names in one field ("Jules Verne, Lewis Mercier") also have one comma.
    if not all(word.casefold() in _PARTICLES for word in family_words[:-1]):
        return name
    return given + " " + family


def _extension_format(path: str) -> str | None:
    lowered = os.path.basename(path).lower()
    for extension, kind in _EXTENSIONS:
        if lowered.endswith(extension):
            return kind
    return None


def detect(path: str) -> str | None:
    """The format of a file, from its content first and its name second."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_HEAD)
    except OSError:
        return None
    named = _extension_format(path)
    if head[60:68] in (b"BOOKMOBI", b"TEXtREAd"):
        return "mobi"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"AT&TFORM"):
        return "djvu"
    if head.startswith(b"PK\x03\x04"):
        return _detect_zip(path, named)
    probe = head.replace(b"\x00", b"").lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n").lower()
    if probe.startswith(b"<"):
        if b"<fictionbook" in probe:
            return "fb2"
        if _HTML_START.search(probe[:1024]):
            return "html"
    return named


def _detect_zip(path: str, named: str | None) -> str | None:
    try:
        archive = Archive.open(path)
    except ReaderError:
        return named
    with archive:
        names = [name for name in archive.names() if not _hidden(name)]
        lowered = [name.lower() for name in names]
        if "meta-inf/container.xml" in lowered or any(name.endswith(".opf") for name in lowered):
            return "epub"
        if "mimetype" in lowered:
            try:
                declared = archive.read(names[lowered.index("mimetype")], 256)
                if declared.strip() == b"application/epub+zip":
                    return "epub"
            except ReaderError:
                pass
        if any(name.endswith(".fb2") for name in lowered):
            return "fb2"
        pictures = sum(1 for name in lowered if name.endswith(_IMAGE_NAMES))
        if pictures and pictures * 2 > len(lowered):
            return "comic"
    return named


def _hidden(name: str) -> bool:
    return any(part.startswith(".") or part == "__MACOSX" for part in name.split("/"))


def _load(kind: str | None) -> ModuleType:
    """The module that reads a format; ReaderError("unsupported") when there is none."""
    if kind in _EXTERNAL:
        raise ReaderError("unsupported", _EXTERNAL[kind])
    if kind not in _MODULES:
        raise ReaderError("unsupported", _NOT_A_BOOK)
    name = "%s.%s" % (__package__, _MODULES[kind])
    try:
        return importlib.import_module(name)
    except Exception as error:  # a broken format module must not take the others down
        if not (isinstance(error, ModuleNotFoundError) and error.name == name):
            traceback.print_exc(file=sys.stderr)
        raise ReaderError("unsupported", _NOT_YET[kind]) from None


def _sampled_key(path: str) -> str:
    """A content identity from the size and ten samples of the file."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        digest.update(b"%d\n" % size)
        digest.update(handle.read(_SAMPLE_EDGE))
        handle.seek(max(0, size - _SAMPLE_EDGE))
        digest.update(handle.read(_SAMPLE_EDGE))
        for step in range(1, 9):
            handle.seek(size * step // 9)
            digest.update(handle.read(_SAMPLE))
    return digest.hexdigest()[:20]


def book_key(path: str, module: ModuleType | None = None) -> str:
    """The book's 20-digit identity: the format's own when it has one, else sampled."""
    key = None
    content_key = getattr(module, "content_key", None)
    if content_key is not None:
        try:
            key = content_key(path)
        except ReaderError:
            key = None
    if key:
        return key
    try:
        return _sampled_key(path)
    except OSError:
        return hashlib.sha1(os.fsencode(path)).hexdigest()[:20]


def _field(source: object, name: str, default: object = None) -> object:
    """A field of a Meta or Book, whether the format module made it a dict or an object."""
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _stat(path: str) -> tuple[int, int]:
    try:
        status = os.stat(path)
    except FileNotFoundError:
        raise ReaderError("missing", _MISSING) from None
    except OSError:
        raise ReaderError("corrupt", _UNREADABLE) from None
    if not os.path.isfile(path):
        raise ReaderError("missing", _MISSING)
    return status.st_size, int(status.st_mtime)


def _blank_entry(path: str, size: int, mtime: int) -> dict:
    return {"key": "", "path": path, "format": "", "title": title_from_filename(path),
            "author": "", "cover": "", "coverW": 0, "coverH": 0, "size": size, "mtime": mtime,
            "external": False, "error": ""}


def _describe(path: str, size: int, mtime: int) -> tuple[dict, bytes | None]:
    """The library entry for a file, and its cover bytes; never raises."""
    entry = _blank_entry(path, size, mtime)
    kind = detect(path)
    entry["format"] = kind or ""
    module = None
    meta = None
    try:
        if kind in _EXTERNAL:
            entry["external"] = True
        else:
            module = _load(kind)
            meta = module.read_meta(path)
    except ReaderError as error:
        entry["error"] = error.message
    except Exception:  # whatever a format module trips over, the file is still listed
        traceback.print_exc(file=sys.stderr)
        entry["error"] = _INTERNAL
    # A module may hand over the key with the metadata, saving a second look at the file.
    entry["key"] = str(_field(meta, "key") or "") or book_key(path, module)
    cover = None
    if meta is not None:
        entry["title"] = _name(_field(meta, "title")) or entry["title"]
        entry["author"] = display_author(list(_field(meta, "authors") or []))
        entry["error"] = str(_field(meta, "error") or "")
        cover = _field(meta, "cover") or None
        found = images.sniff(cover) if cover else None
        if found is None:
            cover = None
        else:
            entry["cover"] = "cover" + found.ext
            entry["coverW"], entry["coverH"] = found.width, found.height
    return entry, cover


def _roots(dirs: list[str] | None) -> list[str]:
    roots: list[str] = []
    for directory in DEFAULT_DIRS if dirs is None else dirs:
        root = os.path.abspath(os.path.expanduser(directory))
        if os.path.isdir(root) and root not in roots:
            roots.append(root)
    return roots


def _walk(roots: list[str]) -> list[str]:
    """Every file with a book's extension under the roots, sorted by path."""
    found: set[str] = set()
    pending = [(root, 0) for root in roots]
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as listing:
                entries = list(listing)
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and not entry.name.lower().endswith(".sdr"):
                        pending.append((entry.path, depth + 1))
                elif entry.is_file() and _extension_format(entry.name):
                    found.add(entry.path)
            except OSError:
                continue
    return sorted(found)


def _read_index(cache: str) -> dict[str, dict]:
    """What the last scan recorded, by path."""
    try:
        with open(os.path.join(cache, "index.json"), "rb") as handle:
            index = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(index, dict) or index.get("v") != INDEX_VERSION:
        return {}
    known: dict[str, dict] = {}
    blank = _blank_entry("", 0, 0)
    for listing in (index.get("books"), index.get("duplicates"), index.get("pending")):
        for entry in listing if isinstance(listing, list) else ():
            if (isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    and all(type(entry.get(name)) is type(value) for name, value in blank.items())):
                known[entry["path"]] = entry
    return known


def _has_reader(kind: str) -> bool:
    try:
        return importlib.util.find_spec("%s.%s" % (__package__, _MODULES[kind])) is not None
    except (KeyError, ImportError, ValueError):
        return False


def _still_good(entry: dict, size: int, mtime: int) -> bool:
    """Whether a recorded entry can stand in for reading the file again."""
    if entry["size"] != size or entry["mtime"] != mtime or not entry["key"]:
        return False
    # A format that had no reader last time may have gained one since.
    if entry["error"] in _NOT_YET.values() and _has_reader(entry["format"]):
        return False
    return not entry["cover"] or os.path.isfile(entry["cover"])


def _dump(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace")


def _store_entry(entry: dict, cover: bytes | None, cache: str) -> None:
    """Write the cover and meta.json of a freshly read book into its cache folder."""
    folder = os.path.join(cache, "books", entry["key"])
    if cover is not None:
        target = os.path.join(folder, entry["cover"])
        write_atomic(target, cover)
        entry["cover"] = target
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            stale = os.path.join(folder, name)
            if name.startswith("cover.") and stale != entry["cover"]:
                os.unlink(stale)
    if not entry["external"]:
        source = {name: entry[name] for name in ("path", "size", "mtime")}
        record = dict(entry, v=BOOK_VERSION, conv=CONVERTER_VERSION, source=source)
        write_atomic(os.path.join(folder, "meta.json"), _dump(record))


def scan(dirs: list[str] | None, cache: str) -> dict:
    """Find the books under `dirs` (or the usual places) and describe each one.

    Files whose path, size and modification time match the last scan are not
    opened at all.
    """
    cache = os.path.abspath(os.path.expanduser(cache))
    roots = _roots(dirs)
    known = _read_index(cache)
    books: dict[str, dict] = {}
    duplicates: list[dict] = []
    complaints: list[str] = []

    def unwritable(error: OSError) -> None:
        # A full disk or a cache that cannot be written costs the next scan
        # its head start, not this one its answer. Said once: every write
        # then fails the same way.
        if not complaints:
            print("reader: the cache can't be written: %s" % error, file=sys.stderr)
        complaints.append(str(error))

    def record(pending: list[dict]) -> dict:
        listing = sorted(books.values(),
                         key=lambda entry: (title_sort_key(entry["title"]), entry["path"]))
        result = {"ok": True, "dirs": roots, "books": listing}
        try:
            write_atomic(os.path.join(cache, "index.json"),
                         _dump(dict(result, v=INDEX_VERSION, duplicates=duplicates, pending=pending)))
        except OSError as error:
            unwritable(error)
        return result

    paths = _walk(roots)
    recorded = time.monotonic()
    for position, path in enumerate(paths):
        try:
            size, mtime = _stat(path)
        except ReaderError:
            continue
        entry = known.get(path)
        fresh = entry is None or not _still_good(entry, size, mtime)
        if fresh:
            entry, cover = _describe(path, size, mtime)
            try:
                _store_entry(entry, cover, cache)
            except OSError as error:
                unwritable(error)
                if not os.path.isabs(entry["cover"]):
                    entry.update(cover="", coverW=0, coverH=0)
        if entry["key"] in books:
            duplicates.append(entry)
        else:
            books[entry["key"]] = entry
        if fresh and time.monotonic() - recorded >= CHECKPOINT_SECONDS:
            # The first scan of a large library takes minutes and may be cut
            # short; the next one then starts from here, not from nothing.
            record([known[later] for later in paths[position + 1:] if later in known])
            recorded = time.monotonic()
    return record([])


def _header(key: str, kind: str, path: str, size: int, mtime: int) -> str:
    """How every book.json begins: enough to tell whether it is still current.

    Being the literal start of the file, a cached book is checked by reading
    a few hundred bytes rather than parsing megabytes.
    """
    head = {"v": BOOK_VERSION, "conv": CONVERTER_VERSION, "key": key, "format": kind,
            "size": size, "mtime": mtime, "path": path}
    return json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1] + ","


def _is_current(book_path: str, header: bytes) -> bool:
    try:
        with open(book_path, "rb") as handle:
            return handle.read(len(header)) == header
    except OSError:
        return False


def _book_body(book: object, path: str) -> dict:
    """The part of book.json a format module supplies, in a fixed order."""
    blocks = list(_field(book, "blocks") or [])
    if not blocks:
        raise ReaderError("corrupt", "This book is damaged and can't be opened.")
    author = _field(book, "author")
    if author is None:
        author = display_author(list(_field(book, "authors") or []))
    return {
        "title": _name(_field(book, "title")) or title_from_filename(path),
        "author": _name(author),
        "language": str(_field(book, "language") or ""),
        "sections": list(_field(book, "sections") or [0]),
        "toc": list(_field(book, "toc") or []),
        "blocks": blocks,
    }


def open_book(path: str, cache: str) -> dict:
    """Make sure the book is converted and say where the result is."""
    path = os.path.abspath(os.path.expanduser(path))
    cache = os.path.abspath(os.path.expanduser(cache))
    size, mtime = _stat(path)
    kind = detect(path)
    module = _load(kind)
    key = book_key(path, module)
    folder = os.path.join(cache, "books", key)
    target = os.path.join(folder, "book.json")
    header = _header(key, kind, path, size, mtime)
    cached = _is_current(target, header.encode("utf-8", "replace"))
    if cached:
        try:
            os.utime(target)  # the pruning order is "least recently opened"
        except OSError:
            pass
    else:
        # The old result goes first: a conversion killed half-way must leave
        # nothing that could pass for a finished book.
        if os.path.exists(target):
            os.unlink(target)
        pictures = os.path.join(folder, "img")
        shutil.rmtree(pictures, ignore_errors=True)
        try:
            body = _book_body(module.convert(path, folder), path)
            data = header.encode("utf-8", "replace") + _dump(body)[1:]
            if len(data) > BOOK_LIMIT:
                raise ReaderError("corrupt", _TOO_LARGE)
            write_atomic(target, data)
        except BaseException:
            # Pictures of a book that did not open are no use to anyone.
            shutil.rmtree(pictures, ignore_errors=True)
            raise
        _prune(cache, key)
    return {"ok": True, "key": key, "book": target, "cached": cached}


def _tree_size(folder: str) -> int:
    total = 0
    try:
        with os.scandir(folder) as listing:
            for entry in listing:
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _prune(cache: str, keep: str) -> None:
    """Drop converted books, least recently opened first, down to CACHE_LIMIT."""
    root = os.path.join(cache, "books")
    try:
        keys = os.listdir(root)
    except OSError:
        return
    converted = []
    total = 0
    for key in keys:
        book = os.path.join(root, key, "book.json")
        pictures = os.path.join(root, key, "img")
        try:
            status = os.stat(book)
            opened, size = status.st_mtime, status.st_size
        except OSError:
            opened, size = 0.0, 0  # pictures without a book: an abandoned conversion
        size += _tree_size(pictures)
        if size:
            total += size
            converted.append((opened, key, size, book, pictures))
    for opened, key, size, book, pictures in sorted(converted):
        if total <= CACHE_LIMIT:
            break
        if key == keep:
            continue
        try:
            os.unlink(book)
        except OSError:
            pass
        shutil.rmtree(pictures, ignore_errors=True)
        total -= size


def info(path: str) -> dict:
    """The library entry of one file plus its contents and block count; writes nothing."""
    path = os.path.abspath(os.path.expanduser(path))
    size, mtime = _stat(path)
    kind = detect(path)
    if kind in _EXTERNAL:
        entry = _blank_entry(path, size, mtime)
        entry.update(key=book_key(path), format=kind, external=True)
        return {"ok": True, **entry, "toc": [], "blocks": 0}
    module = _load(kind)
    with tempfile.TemporaryDirectory(prefix="reader-info-") as scratch:
        body = _book_body(module.convert(path, scratch), path)
    entry, _ = _describe(path, size, mtime)
    entry["cover"] = ""
    return {"ok": True, **entry, "toc": body["toc"], "blocks": len(body["blocks"])}
