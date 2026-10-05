"""Comic archives: one picture block per page, in reading order."""

from __future__ import annotations

import posixpath

from . import images
from .archive import Archive
from .blocks import BookBuilder
from .book import Book, Meta
from .errors import ReaderError
from .library import display_author, title_from_filename
from .pictures import Pictures, image_block
from .textutil import collapse_space, natural_key
from .xmlutil import attr, children, descendants, parse_xml, text_of

_NO_PAGES = "This comic has no pages that can be shown."
_LOCKED = "This comic is password-protected and can't be opened."

_PAGE_NAMES = (".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp")
_JUNK_FOLDERS = frozenset(("__macosx", "@eadir", "__thumbs"))

MAX_PAGES = 5000
MAX_PICTURE = 64 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_INFO = 1024 * 1024
COVER_TRIES = 8
MIN_BOOKMARKS = 2
CONTENTS_FROM = 20
CONTENTS_STEP = 10


def _hidden(name: str) -> bool:
    return any(part.startswith(".") or part.lower() in _JUNK_FOLDERS for part in name.split("/"))


def _order(name: str) -> tuple:
    """Folders in natural order, a folder's own pages before those of its subfolders."""
    *folders, page = name.split("/")
    return tuple(natural_key(folder) for folder in folders), natural_key(page)


def _pages(archive: Archive) -> list[str]:
    """The members that claim to be pages, in reading order."""
    names = [name for name in archive.names()
             if name.lower().endswith(_PAGE_NAMES) and not _hidden(name)
             and archive.member(name).size and not archive.member(name).encrypted]
    return sorted(names, key=_order)[:MAX_PAGES]


class _Info:
    """What ComicInfo.xml says about the comic; the file name when it is silent."""

    def __init__(self, archive: Archive, path: str) -> None:
        self.title = title_from_filename(path)
        self.authors: list[str] = []
        self.language = ""
        self.bookmarks: list[tuple[int, str]] = []
        name = next((name for name in archive.names()
                     if posixpath.basename(name).lower() == "comicinfo.xml"
                     and not _hidden(name)), None)
        if name is None:
            return
        try:
            root = parse_xml(archive.read(name, MAX_INFO))
        except ReaderError:
            return

        def said(tag: str) -> str:
            found = children(root, tag)
            return text_of(found[0]) if found else ""

        series, number = said("Series"), said("Number")
        if series and number:
            self.title = "%s #%s" % (series, number)
        else:
            self.title = said("Title") or series or self.title
        self.authors = [writer for writer in map(collapse_space, said("Writer").split(","))
                        if writer]
        self.language = said("LanguageISO").lower()
        for page in descendants(root, "Page")[:MAX_PAGES]:
            label = collapse_space(attr(page, "Bookmark"))
            index = attr(page, "Image").strip()
            if label and index.isdigit() and len(index) < 6:
                self.bookmarks.append((int(index), label))


def read_meta(path: str) -> Meta:
    """Title, writers, language and the first page as the cover."""
    with Archive.open(path) as archive:
        info = _Info(archive, path)
        pages = _pages(archive)
        if not pages:
            _refuse(archive)
        cover = None
        for name in pages[:COVER_TRIES]:
            try:
                data = archive.read(name, MAX_PICTURE)
            except ReaderError:
                continue
            if images.sniff(data) is not None:
                cover = data
                break
        return Meta(info.title, info.authors, info.language, cover)


def _refuse(archive: Archive) -> None:
    locked = any(archive.member(name).encrypted for name in archive.names()
                 if name.lower().endswith(_PAGE_NAMES))
    if locked:
        raise ReaderError("unsupported", _LOCKED)
    raise ReaderError("corrupt", _NO_PAGES)


def convert(path: str, out_dir: str) -> Book:
    """Copy every page to `<out_dir>/img/` and list them as picture blocks."""
    with Archive.open(path, budget=MAX_TOTAL) as archive:
        info = _Info(archive, path)
        names = _pages(archive)
        if not names:
            _refuse(archive)
        builder = BookBuilder()
        pictures = Pictures(out_dir)
        shown: dict[int, int] = {}
        folders: list[tuple[str, int]] = []
        for number, name in enumerate(names):
            try:
                picture = pictures.store(archive.read(name, MAX_PICTURE))
            except ReaderError:
                continue
            if picture is None:
                continue
            folder = posixpath.dirname(name)
            if not folders or folders[-1][0] != folder:
                folders.append((folder, len(builder.blocks)))
                builder.begin_section("/" + folder)
            shown[number] = builder.add(image_block(picture))
    if not builder.blocks:
        raise ReaderError("corrupt", _NO_PAGES)
    builder.finish()
    contents = _contents(info, shown, folders, len(builder.blocks))
    return Book(info.title, display_author(info.authors), info.language, list(builder.sections),
                contents, builder.blocks, list(info.authors))


def _contents(info: _Info, shown: dict[int, int], folders: list[tuple[str, int]],
              count: int) -> list[dict]:
    """The comic's own bookmarks, else its folders, else every tenth page of a long one."""
    marked = sorted({shown[index]: label for index, label in reversed(info.bookmarks)
                     if index in shown}.items())
    if len(marked) >= MIN_BOOKMARKS:
        return [{"t": label, "d": 0, "b": block} for block, label in marked]
    if len(folders) > 1:
        return [{"t": posixpath.basename(folder) or info.title, "d": 0, "b": block}
                for folder, block in folders]
    if count > CONTENTS_FROM:
        return [{"t": "Page %d" % (block + 1), "d": 0, "b": block}
                for block in range(0, count, CONTENTS_STEP)]
    return []
