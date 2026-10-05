"""EPUB: container, package, spine, contents, cover and protection.

The text itself is converted by html.py, one spine document at a time.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import unquote_to_bytes
from xml.etree.ElementTree import Element

from . import html, images, toc
from .archive import MAX_MEMBER, Archive
from .blocks import BookBuilder
from .errors import ReaderError
from .library import display_author, title_from_filename
from .pictures import MAX_PICTURE, Pictures
from .textutil import collapse_space, decode_bytes, natural_key
from .xmlutil import attr, children, descendants, local, parse_xml, text_of

_PROTECTED = "This book is protected by DRM and can't be opened."
_DAMAGED = "This book is damaged and can't be opened."

# Font obfuscation is declared like encryption but protects nothing a reader shows.
_OBFUSCATION = frozenset(("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"))

_DOCUMENT_NAMES = (".xhtml", ".html", ".htm", ".xht")
_IMAGE_NAMES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg")
_OTHER_NAMES = (".ncx", ".css", ".opf", ".xpgt", ".js", ".ttf", ".otf", ".woff", ".woff2", ".smil")
_DOCUMENT_TYPES = frozenset(("application/xhtml+xml", "text/html", "application/x-dtbook+xml",
                             "text/x-oeb1-document"))
_NCX_TYPE = "application/x-dtbncx+xml"
_MARKUP = re.compile(rb"<(?:html|body|head|div|p|h[1-6]|section|dtbook)[\s>/]", re.I)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")
_TAG = re.compile(r"<[^<>]{1,200}>")
_SEVERAL_AUTHORS = re.compile(r"\s*(?:;|&)\s*")
# "cover" as a word of its own: coverimage and front-cover, but not discover or back_cover.
_COVER_WORD = re.compile(r"(?<![a-z])(?<!back[-_. ])(?:front|e?book)?cover")
_TOC_NAV = re.compile(r"<nav\s[^<>]*toc", re.I)

MIN_COVER = 64
COVER_PAGE_TEXT = 200
COVER_PAGE_SIZE = 32 * 1024
COVER_PAGES = 3
MAX_TOC_DEPTH = 32
MAX_TOC_ENTRIES = 20000
MAX_NESTING = 200
MAX_RESOLVED = 200000
NAV_SEARCH = 8
MAX_DATA_URI = 16 * 1024 * 1024
# A name no section can have: marks a contents entry whose target is not in the book.
_NOWHERE = "\x00"


@dataclass
class Meta:
    """What the library shows for a book before it is opened.

    `error` is the sentence `convert` would refuse the book with, so that a
    protected book can still be listed under its own title and cover; `key`
    is what `content_key` would answer, found while the book was open anyway.
    """

    title: str
    authors: list[str]
    language: str
    cover: bytes | None
    error: str = ""
    key: str = ""


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


@dataclass(frozen=True, slots=True)
class _Item:
    id: str
    name: str | None  # the archive member, when the href leads to one
    media: str
    properties: frozenset


@dataclass(frozen=True, slots=True)
class _SpineEntry:
    name: str
    image: bool


def _hidden(name: str) -> bool:
    return any(part.startswith(".") or part == "__MACOSX" for part in name.split("/"))


def _extension(name: str) -> str:
    return posixpath.splitext(name)[1].lower()


class _Package:
    """An open EPUB: its package file read once, and every lookup that needs it."""

    def __init__(self, archive: Archive, path: str) -> None:
        self.archive = archive
        self.opf = ""
        self.items: dict[str, _Item] = {}
        self.listed: list[_Item] = []
        self.by_name: dict[str, _Item] = {}
        self.guide: list[tuple[str, str, str]] = []
        self.spine: list[_SpineEntry] = []
        self.title = title_from_filename(path)
        self.authors: list[str] = []
        self.language = ""
        self._resolved: dict[tuple[str, str], tuple[str | None, str]] = {}
        self._outside: dict[str, bool] = {}
        self._cover_meta = ""
        self._ncx_id = ""
        root = self._find_package()
        if root is not None:
            self._read_manifest(root)
            self._read_metadata(root)
            self.guide = [
                (attr(ref, "type").strip().lower(), *self.resolve(attr(ref, "href"), self.opf))
                for ref in descendants(root, "reference")
            ]
            self._read_spine(root)
        if not self.spine:
            self.spine = self._spine_without_itemrefs()
        self.sections = frozenset(entry.name for entry in self.spine)
        self.encrypted = self._read_encryption()

    def resolve(self, href: str, base: str) -> tuple[str | None, str]:
        """The archive member and fragment a reference written inside `base` points at.

        Tried relative to the referring file, then to the package file, then
        to the archive root; Archive.find forgives case, percent-encoding and
        backslashes at each step.
        """
        href = href.strip()
        if not href or href.startswith("#"):
            return (base or None), href[1:]
        directory = posixpath.dirname(base)
        key = (directory, href)
        known = self._resolved.get(key)
        if known is None:
            known = self._resolve(href, directory)
            if len(self._resolved) < MAX_RESOLVED:
                self._resolved[key] = known
        return known

    def _resolve(self, href: str, directory: str) -> tuple[str | None, str]:
        path, _, fragment = href.partition("#")
        if _SCHEME.match(path):
            return None, fragment
        path = path.replace("\\", "/")
        spellings = [path]
        if "?" in path:
            spellings.append(path.partition("?")[0])
        for spelling in spellings:
            if spelling.startswith("/"):
                candidates = [spelling.lstrip("/")]
            else:
                candidates = [posixpath.join(directory, spelling)]
                package = posixpath.dirname(self.opf)
                if package != directory:
                    candidates.append(posixpath.join(package, spelling))
                if directory and package:
                    candidates.append(spelling)
            for candidate in candidates:
                found = self.archive.find(candidate)
                if found is not None:
                    return found, fragment
        return None, fragment

    def read(self, name: str, limit: int = MAX_MEMBER) -> bytes | None:
        """A member's bytes, or None when it cannot be had."""
        try:
            return self.archive.read(name, limit)
        except (KeyError, ReaderError):
            return None

    def parse(self, name: str) -> Element | None:
        data = self.read(name)
        if not data:
            return None
        try:
            return parse_xml(data, language=self.language)
        except ReaderError:
            return None

    def _find_package(self) -> Element | None:
        """The package document: the container's, else any .opf, shallowest first."""
        candidates: list[str] = []
        container = self.archive.find("META-INF/container.xml")
        root = self.parse(container) if container else None
        if root is not None:
            rootfiles = descendants(root, "rootfile")
            rootfiles.sort(key=lambda el: "package" not in attr(el, "media-type").lower())
            for rootfile in rootfiles:
                found = self.resolve(attr(rootfile, "full-path"), "")[0]
                if found is not None:
                    candidates.append(found)
        loose = [name for name in self.archive.names()
                 if name.lower().endswith(".opf") and not _hidden(name)]
        candidates.extend(sorted(loose, key=lambda name: (name.count("/"), natural_key(name))))
        for name in candidates:
            root = self.parse(name)
            if root is not None and (descendants(root, "item") or descendants(root, "itemref")
                                     or descendants(root, "metadata")):
                self.opf = name
                return root
        return None

    def _read_manifest(self, root: Element) -> None:
        for el in descendants(root, "item"):
            name = self.resolve(attr(el, "href"), self.opf)[0]
            item = _Item(attr(el, "id").strip(), name, attr(el, "media-type").strip().lower(),
                         frozenset(attr(el, "properties").lower().split()))
            self.listed.append(item)
            if item.id:
                self.items.setdefault(item.id, item)
            if name is not None:
                self.by_name.setdefault(name, item)

    def _item(self, identifier: str) -> _Item | None:
        identifier = identifier.strip()
        item = self.items.get(identifier)
        if item is None and identifier:
            folded = identifier.casefold()
            item = next((item for key, item in self.items.items() if key.casefold() == folded),
                        None)
        return item

    def _read_metadata(self, root: Element) -> None:
        scope = next(iter(descendants(root, "metadata")), root)
        refined: dict[str, dict[str, str]] = {}
        for meta in descendants(scope, "meta"):
            target = attr(meta, "refines").strip().lstrip("#")
            if target:
                name = attr(meta, "property").strip().lower()
                refined.setdefault(target, {})[name] = text_of(meta)
            elif attr(meta, "name").strip().lower() == "cover" and not self._cover_meta:
                self._cover_meta = attr(meta, "content").strip()

        titles = [(el, _name(text_of(el))) for el in descendants(scope, "title")]
        titles = [(el, text) for el, text in titles if text]
        main = [text for el, text in titles
                if refined.get(attr(el, "id"), {}).get("title-type", "").lower() == "main"]
        if main or titles:
            self.title = main[0] if main else titles[0][1]

        creators = []
        for el in descendants(scope, "creator"):
            role = attr(el, "role") or refined.get(attr(el, "id"), {}).get("role", "")
            role = role.strip().lower()
            for name in _SEVERAL_AUTHORS.split(_name(text_of(el))):
                if name and (name, role) not in creators:
                    creators.append((name, role))
        chosen = [name for name, role in creators if role in ("", "aut")]
        for name in chosen or [name for name, _ in creators]:
            if name not in self.authors:
                self.authors.append(name)

        for el in descendants(scope, "language"):
            language = text_of(el).lower().replace("_", "-")
            if language and language != "und":
                self.language = language
                break

    def _kind(self, item: _Item) -> str:
        """"document", "image" or "other" — by name, then declared type, then content."""
        name = item.name or ""
        extension = _extension(name)
        if extension in _DOCUMENT_NAMES:
            return "document"
        if extension in _IMAGE_NAMES:
            return "image"
        if extension in _OTHER_NAMES or item.media == _NCX_TYPE:
            return "other"
        if item.media in _DOCUMENT_TYPES:
            return "document"
        if item.media.startswith("image/"):
            return "image"
        if item.media and (item.media.startswith(("font/", "audio/", "video/"))
                           or item.media in ("text/css", "text/javascript")
                           or "font" in item.media or "page-template" in item.media):
            return "other"
        data = self.read(name) or b""
        if images.sniff(data) is not None:
            return "image"
        return "document" if _MARKUP.search(data[:4096]) else "other"

    def _read_spine(self, root: Element) -> None:
        spines = descendants(root, "spine")
        if spines:
            self._ncx_id = attr(spines[0], "toc")
        flow: list[_SpineEntry] = []
        aside: list[_SpineEntry] = []
        seen: set[str] = set()
        for ref in descendants(root, "itemref"):
            item = self._item(attr(ref, "idref"))
            if item is None or item.name is None or item.name in seen:
                continue
            kind = self._kind(item)
            if kind == "other":
                continue
            seen.add(item.name)
            entry = _SpineEntry(item.name, kind == "image")
            linear = attr(ref, "linear").strip().lower() != "no"
            # What the book opens with stays there: cover pages are often marked non-linear.
            if linear or not (flow or aside):
                flow.append(entry)
            else:
                aside.append(entry)
        self.spine = flow + aside

    def _spine_without_itemrefs(self) -> list[_SpineEntry]:
        """The manifest's documents, or failing that the archive's, as the reading order."""
        names = []
        for item in self.listed:
            if (item.name is not None and item.name not in names and "nav" not in item.properties
                    and self._kind(item) == "document"):
                names.append(item.name)
        if not names:
            names = sorted((name for name in self.archive.names()
                            if _extension(name) in _DOCUMENT_NAMES and not _hidden(name)),
                           key=natural_key)
        return [_SpineEntry(name, False) for name in names]

    def _read_encryption(self) -> frozenset:
        """Members that META-INF/encryption.xml says are encrypted for real."""
        declared = "META-INF/encryption.xml"
        name = self.archive.find(declared) or next(
            (name for name in self.archive.names()
             if name.lower().endswith("/" + declared.lower())), None)
        root = self.parse(name) if name else None
        if root is None:
            return frozenset()
        folder = name[:-len(declared)]  # the book may sit inside a folder of the archive
        locked = set()
        for data in descendants(root, "EncryptedData"):
            methods = children(data, "EncryptionMethod") or descendants(data, "EncryptionMethod")
            if methods and attr(methods[0], "Algorithm").strip() in _OBFUSCATION:
                continue
            for reference in descendants(data, "CipherReference"):
                member = self.archive.find(folder + attr(reference, "URI").strip())
                if member is not None:
                    locked.add(member)
        return frozenset(locked)

    def outside(self, name: str | None) -> bool:
        """Whether a document the spine leaves out can still be shown.

        Older books keep their notes in a manifest document without an
        itemref, which only links and the contents lead to.
        """
        if name is None or name in self.sections:
            return False
        known = self._outside.get(name)
        if known is None:
            item = self.by_name.get(name)
            known = self._outside[name] = (
                item is not None and "nav" not in item.properties
                and name not in self.encrypted and not self.archive.member(name).encrypted
                and self._kind(item) == "document")
        return known

    def protected(self) -> bool:
        """Whether text of the book is encrypted.

        A declaration alone is not believed: books stripped of their
        protection often keep the file that announced it.
        """
        for entry in self.spine:
            if entry.image:
                continue
            if self.archive.member(entry.name).encrypted:
                return True
            if entry.name in self.encrypted:
                data = self.read(entry.name)
                if data is None or not _MARKUP.search(data[:65536]):
                    return True
        return False

    def cover(self) -> bytes | None:
        """The cover image's bytes, from the first declaration that holds up."""
        for name, page_only in self._cover_candidates():
            data = self._cover_from(name, page_only)
            if data is not None:
                return data
        return None

    def _cover_candidates(self):
        """`(member, must be a picture page)` in order of trust."""
        for item in self.listed:
            if "cover-image" in item.properties and item.name:
                yield item.name, False
        if self._cover_meta:
            item = self._item(self._cover_meta)
            if item is not None and item.name:
                yield item.name, False
            name = self.resolve(self._cover_meta, self.opf)[0]
            if name:
                yield name, False
        for kind, name, _ in self.guide:
            if _says_cover(kind) and name:
                yield name, False
        name = self._landmark("cover")
        if name:
            yield name, False
        named = []
        for item in self.listed:
            if item.name is None or not (_extension(item.name) in _IMAGE_NAMES
                                         or item.media.startswith("image/")):
                continue
            stem = posixpath.splitext(posixpath.basename(item.name))[0].casefold()
            identifier = item.id.casefold()
            if _says_cover(stem) or _says_cover(identifier):
                exact = stem == "cover" or identifier == "cover"
                size = self.archive.member(item.name).size
                named.append((not exact, -size, len(named), item.name))
        for *_, name in sorted(named):
            yield name, False
        for entry in self.spine[:COVER_PAGES]:
            yield entry.name, True

    def _cover_from(self, name: str, page_only: bool) -> bytes | None:
        data = self.read(name)
        if not data:
            return None
        found = images.sniff(data)
        if found is not None and found.kind != "svg":
            return data if min(found.width, found.height) >= MIN_COVER else None
        # A page (or an SVG) showing the cover: the picture it shows is the cover.
        root = self.parse(name)
        if root is None:
            return None
        if page_only and found is None:
            bodies = descendants(root, "body")
            if len(text_of(bodies[0] if bodies else root)) > COVER_PAGE_TEXT:
                return None
        for href in _pictures(root)[:8]:
            target = self.resolve(href, name)[0]
            picture = self.read(target) if target else None
            inner = images.sniff(picture) if picture else None
            if (inner is not None and inner.kind != "svg"
                    and min(inner.width, inner.height) >= MIN_COVER):
                return picture
        if found is not None and min(found.width, found.height) >= MIN_COVER:
            return data
        return None

    def _landmark(self, kind: str) -> str | None:
        """The member an EPUB 3 landmark of this kind points at."""
        for item in self.listed:
            if "nav" not in item.properties or not item.name:
                continue
            root = self.parse(item.name)
            for nav in descendants(root, "nav") if root is not None else ():
                if "landmarks" not in attr(nav, "type").lower().split():
                    continue
                for link in descendants(nav, "a"):
                    if kind in attr(link, "type").lower().split():
                        return self.resolve(link.get("href", ""), item.name)[0]
        return None

    def cover_page(self) -> str | None:
        """The spine entry the book opens with, when all it does is show the cover.

        Declared by the guide or the landmarks, or recognised by its shape: a
        picture, or a short page around a single one. Whether the picture can
        be read is not asked, so replacing it never changes the answer.
        """
        if not self.spine:
            return None
        first = self.spine[0]
        if first.image:
            return first.name
        if self.archive.member(first.name).size > COVER_PAGE_SIZE:
            return None
        root = self.parse(first.name)
        if root is None:
            return None
        bodies = descendants(root, "body")
        if len(text_of(bodies[0] if bodies else root)) > COVER_PAGE_TEXT:
            return None
        declared = any(_says_cover(kind) and name == first.name for kind, name, _ in self.guide)
        if declared or len(_pictures(root)) == 1 or self._landmark("cover") == first.name:
            return first.name
        return None

    def contents(self, builder: BookBuilder, spotted: list[str]) -> list[dict]:
        """The declared contents to hand to toc.build_toc.

        The nav document is preferred, then the NCX, then a contents `<nav>`
        the package does not declare, then the guide's contents page — unless
        the preferred one resolves to fewer than three places and a later one
        to more. `spotted` are spine documents seen to hold such a `<nav>`.
        """
        best: list[dict] = []
        most = -1
        for entries in self._content_candidates(spotted):
            places = set()
            for entry in entries:
                if entry["name"]:
                    index = builder.anchor_index(entry["name"], entry["fragment"])
                    if index is None and entry["fragment"]:
                        index = builder.anchor_index(entry["name"])
                    if index is not None:
                        places.add(index)
            if len(places) >= toc.MIN_ENTRIES:
                return entries
            if len(places) > most:
                best, most = entries, len(places)
        return best

    def _content_candidates(self, spotted: list[str]):
        navs = [item.name for item in self.listed if "nav" in item.properties and item.name]
        yield from self._first_nav(navs, declared=True)
        name = self._ncx_name()
        root = self.parse(name) if name else None
        if root is not None:
            yield self._ncx_entries(root, name)
        if not navs:
            yield from self._first_nav(spotted or self._loose_navs(), declared=False)
        for kind, name, _ in self.guide:
            if kind == "toc" and name:
                root = self.parse(name)
                if root is not None:
                    yield self._page_entries(root, name)
                break

    def _first_nav(self, names: list[str], declared: bool):
        """The entries of the first of these documents whose `<nav>` has any."""
        for name in names:
            root = self.parse(name)
            entries = self._nav_entries(root, name, declared) if root is not None else []
            if entries:
                yield entries
                break

    def _loose_navs(self) -> list[str]:
        """Documents outside the reading order that may hold a contents `<nav>`, undeclared."""
        loose = [item.name for item in self.listed
                 if item.name and item.name not in self.sections
                 and _extension(item.name) in _DOCUMENT_NAMES]
        return [name for name in loose[:NAV_SEARCH]
                if _holds_toc_nav(decode_bytes(self.read(name) or b"", kind="content",
                                              language=self.language))]

    def _ncx_name(self) -> str | None:
        item = self._item(self._ncx_id) if self._ncx_id else None
        if item is not None and item.name:
            return item.name
        for item in self.listed:
            if item.name and (item.media == _NCX_TYPE or item.name.lower().endswith(".ncx")):
                return item.name
        return next((name for name in self.archive.names()
                     if name.lower().endswith(".ncx") and not _hidden(name)), None)

    def _entry(self, label: str, depth: int, href: str, base: str) -> dict:
        href = href.strip()
        name, fragment = "", ""
        if href and href != "#":
            name, fragment = self.resolve(href, base)
            if name not in self.sections and not self.outside(name):
                name = _NOWHERE
        if "<" in label:
            label = collapse_space(_TAG.sub("", label))
        return {"t": label, "d": depth, "name": name, "fragment": fragment}

    def _ncx_entries(self, root: Element, base: str) -> list[dict]:
        entries: list[dict] = []

        def walk(parent: Element, depth: int, level: int) -> None:
            for point in children(parent, "navPoint"):
                if len(entries) >= MAX_TOC_ENTRIES or level > MAX_NESTING:
                    return
                labels = children(point, "navLabel")
                label = text_of(labels[0]) if labels else ""
                targets = children(point, "content")
                href = attr(targets[0], "src") if targets else ""
                inner = depth
                # A point with neither a label nor a target is only a wrapper.
                if label or href.strip():
                    entries.append(self._entry(label, depth, href, base))
                    inner = min(depth + 1, MAX_TOC_DEPTH)
                walk(point, inner, level + 1)

        maps = descendants(root, "navMap")
        walk(maps[0] if maps else root, 0, 0)
        if not entries:
            for point in descendants(root, "navPoint")[:MAX_TOC_ENTRIES]:
                labels = children(point, "navLabel")
                targets = children(point, "content")
                if targets:
                    entries.append(self._entry(text_of(labels[0]) if labels else "", 0,
                                               attr(targets[0], "src"), base))
        return entries

    def _nav_entries(self, root: Element, base: str, declared: bool) -> list[dict]:
        """The contents `<nav>` of a document.

        Only the declared nav document is trusted to hold one that does not
        say so: elsewhere a bare `<nav>` is a chapter's own section list or a
        strip of previous and next links.
        """
        navs = descendants(root, "nav")
        chosen = next((nav for nav in navs if _is_toc_nav(nav)), None)
        if chosen is None and declared:
            plain = [nav for nav in navs if not attr(nav, "type").strip()]
            chosen = plain[0] if plain else None
        if chosen is None:
            return []
        entries: list[dict] = []

        def walk(parent: Element, depth: int, level: int) -> None:
            for child in parent:
                if len(entries) >= MAX_TOC_ENTRIES or level > MAX_NESTING:
                    return
                tag = local(child.tag).lower()
                if tag == "li":
                    link, label, lists = _list_item(child)
                    inner = depth
                    if link is not None or label:
                        href = link.get("href", "") if link is not None else ""
                        entries.append(self._entry(label, depth, href, base))
                        inner = min(depth + 1, MAX_TOC_DEPTH)
                    for nested in lists:
                        walk(nested, inner, level + 1)
                elif tag == "a":
                    href = child.get("href", "")
                    if href:
                        entries.append(self._entry(_label(child), depth, href, base))
                else:
                    walk(child, depth, level + 1)

        walk(chosen, 0, 0)
        return entries

    def _page_entries(self, root: Element, base: str) -> list[dict]:
        """The links of a contents page inside the book, in order."""
        entries = []
        for link in descendants(root, "a")[:MAX_TOC_ENTRIES]:
            href = link.get("href", "").strip()
            label = _label(link)
            if not href or not label:
                continue
            entry = self._entry(label, 0, href, base)
            if entry["name"] != _NOWHERE:
                entries.append(entry)
        return entries


def _is_toc_nav(nav: Element) -> bool:
    return ("toc" in attr(nav, "type").lower().split()
            or attr(nav, "role").strip().lower() == "doc-toc"
            or nav.get("id", "").strip().lower() == "toc")


def _holds_toc_nav(markup: str) -> bool:
    """Whether a document may hold a `<nav>` that says it is the contents (cheap, not exact)."""
    return _TOC_NAV.search(markup) is not None


def _says_cover(word: str) -> bool:
    """Whether a guide type, file name or id names the front cover."""
    return _COVER_WORD.search(word) is not None


def _label(el: Element) -> str:
    """A link's text, or failing that what it says about itself or its picture."""
    text = text_of(el)
    if not text:
        text = collapse_space(el.get("title", ""))
    if not text:
        text = next((collapse_space(img.get("alt", "")) for img in descendants(el, "img")
                     if img.get("alt", "").strip()), "")
    return text


def _list_item(item: Element) -> tuple[Element | None, str, list[Element]]:
    """A nav `<li>`: its own link, its label, and the lists nested in it."""
    link = None
    lists: list[Element] = []
    words: list[str] = [item.text or ""]
    pending = [iter(item)]
    while pending:
        child = next(pending[-1], None)
        if child is None:
            pending.pop()
            continue
        tag = local(child.tag).lower()
        if tag in ("ol", "ul"):
            lists.append(child)
        elif tag == "a" and link is None:
            link = child
        elif tag != "a":
            words.append(child.text or "")
            if len(pending) < MAX_TOC_DEPTH:
                pending.append(iter(child))
        words.append(child.tail or "")
    label = _label(link) if link is not None else collapse_space(" ".join(words))
    return link, label, lists


def _name(text: str) -> str:
    """A title or a person's name: soft hyphens only matter where text is wrapped."""
    return collapse_space(text.replace("\xad", ""))


def _pictures(root: Element) -> list[str]:
    """The pictures a page shows, in document order."""
    found = []
    for el in root.iter():
        tag = local(el.tag).lower()
        if tag == "img":
            href = el.get("src", "")
        elif tag == "image":
            href = attr(el, "href")
        elif tag == "object":
            href = el.get("data", "")
        else:
            continue
        if href.strip() and not href.strip().lower().startswith("data:"):
            found.append(href)
    return found


class _Resources:
    """What html.py asks of the book: stylesheets, pictures and link targets."""

    def __init__(self, package: _Package, out_dir: str) -> None:
        self.package = package
        self.store = Pictures(out_dir)
        self.sheets: dict[str, str | None] = {}
        self.pictures: dict[str, dict | None] = {}
        self.wanted: list[str] = []

    def stylesheet(self, href: str, base: str) -> str | None:
        name = self.package.resolve(href, base)[0]
        if name is None:
            return None
        if name not in self.sheets:
            data = self.package.read(name)
            self.sheets[name] = None if data is None else decode_bytes(
                data, kind="content", language=self.package.language)
        return self.sheets[name]

    def image(self, href: str, base: str) -> dict | None:
        href = href.strip()
        if href[:5].lower() == "data:":
            return self._inline(href)
        name = self.package.resolve(href, base)[0]
        return self.member(name) if name is not None else None

    def member(self, name: str) -> dict | None:
        """The picture stored as an archive member, extracted on first use."""
        if name not in self.pictures:
            data = self.package.read(name, MAX_PICTURE)
            self.pictures[name] = self.store.store(data) if data else None
        return self.pictures[name]

    def document(self, href: str, base: str) -> str | None:
        name = self.package.resolve(href.partition("#")[0], base)[0]
        if self.package.outside(name):
            self.want(name)
        elif name not in self.package.sections:
            return None
        return name

    def want(self, name: str) -> None:
        """A document outside the spine is led to: it joins the book after the spine."""
        if name not in self.wanted:
            self.wanted.append(name)

    def _inline(self, href: str) -> dict | None:
        if len(href) > MAX_DATA_URI:
            return None
        key = "\x00" + hashlib.sha1(href.encode("utf-8", "replace")).hexdigest()
        if key not in self.pictures:
            header, _, payload = href[5:].partition(",")
            try:
                if header.lower().endswith(";base64"):
                    data = base64.b64decode("".join(payload.split()) + "===")
                else:
                    data = unquote_to_bytes(payload)
            except (binascii.Error, ValueError):
                data = b""
            self.pictures[key] = self.store.store(data) if data else None
        return self.pictures[key]

    def block(self, name: str) -> dict | None:
        """The image block for a picture that is a page of the book in its own right."""
        picture = self.member(name)
        if picture is None:
            return None
        block = {"k": "img", "src": picture["src"], "w": picture["w"], "h": picture["h"], "alt": ""}
        if picture["al"]:
            block["al"] = 1
        return block


def read_meta(path: str) -> Meta:
    """Title, authors, language and cover, without converting anything."""
    with Archive.open(path) as archive:
        package = _Package(archive, path)
        if not package.spine:
            raise ReaderError("corrupt", _DAMAGED)
        return Meta(package.title, list(package.authors), package.language, package.cover(),
                    _PROTECTED if package.protected() else "", _signature(package))


def _signature(package: _Package) -> str:
    """The spine documents' names, checksums and sizes, in order, as 20 hex digits.

    The cover page is left out, since it is rewritten whenever the cover is
    replaced — unless it is all the book has.
    """
    digest = hashlib.sha1()
    cover = package.cover_page()
    for entry in [entry for entry in package.spine if entry.name != cover] or package.spine:
        member = package.archive.member(entry.name)
        digest.update(("%s\x00%08x\x00%d\n" % (member.name, member.crc, member.size))
                      .encode("utf-8", "surrogatepass"))
    return digest.hexdigest()[:20]


def content_key(path: str) -> str | None:
    """The book's identity: its spine documents' names, checksums and sizes.

    Rewriting the metadata or the cover, as library managers do, leaves it
    unchanged. None when no spine can be made out.
    """
    try:
        with Archive.open(path) as archive:
            package = _Package(archive, path)
            return _signature(package) if package.spine else None
    except (ReaderError, OSError):
        return None


def _convert_document(builder: BookBuilder, package: _Package, resources: _Resources,
                      name: str) -> str:
    """Add one document's blocks to the book; its markup is returned."""
    data = package.read(name)
    if data is None:
        return ""
    markup = decode_bytes(data, kind="content", language=package.language)
    html.convert_document(builder, markup, name, resources)
    return markup


def convert(path: str, out_dir: str) -> Book:
    """Convert the whole book; its pictures are written to `<out_dir>/img/`."""
    with Archive.open(path) as archive:
        package = _Package(archive, path)
        if not package.spine:
            raise ReaderError("corrupt", _DAMAGED)
        if package.protected():
            raise ReaderError("drm", _PROTECTED)
        builder = BookBuilder()
        resources = _Resources(package, out_dir)
        spotted: list[str] = []
        for entry in package.spine:
            builder.begin_section(entry.name)
            if entry.image:
                block = resources.block(entry.name)
                if block is not None:
                    builder.add(block)
            elif _holds_toc_nav(_convert_document(builder, package, resources, entry.name)):
                spotted.append(entry.name)
        declared = package.contents(builder, spotted)
        for entry in declared:
            if package.outside(entry["name"]):
                resources.want(entry["name"])
        done = 0
        while done < len(resources.wanted):  # each may lead to more
            builder.begin_section(resources.wanted[done])
            _convert_document(builder, package, resources, resources.wanted[done])
            done += 1
        builder.finish()
        contents = toc.build_toc(declared, builder, package.title)
        return Book(package.title, display_author(package.authors), package.language,
                    list(builder.sections), contents, builder.blocks, list(package.authors))
