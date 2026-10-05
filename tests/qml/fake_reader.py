#!/usr/bin/env python3
"""Stand-in for bin/reader used by the QML scenarios.

Speaks the same command line and JSON as the real backend but invents a
small, deterministic library, so the interface can be exercised without any
real books: covers are generated PNGs, and each book's text is synthetic.

Switches ahead of the command stage a failure: `--empty` (no books),
`--broken` (the helper cannot run), `--slow` (it never answers in time).
"""

import json
import os
import struct
import sys
import time
import zlib

sys.dont_write_bytecode = True

CONV = "test-1"

BOOKS = [
    # key, file, title, author, cover colour (None = no cover), extra
    ("a1", "meditations.epub", "Meditations", "Marcus Aurelius", (124, 58, 42), {}),
    ("a2", "the-odyssey.epub", "The Odyssey", "Homer", (34, 74, 110), {"long_toc": True}),
    ("a3", "walden.epub", "Walden; or, Life in the Woods", "Henry David Thoreau", (52, 98, 60), {}),
    ("a4", "no-cover.epub", "A Book With No Cover Art Whatsoever", "Anonymous", None, {}),
    ("a5", "wide-cover.epub", "Wide Cover", "Landscape Press", (150, 120, 40), {"wide": True}),
    ("a6", "locked.epub", "Locked Book", "Some Publisher", (70, 70, 70), {"drm": True}),
    ("a7", "manual.pdf", "A Fixed-Layout Manual", "", None, {"external": True}),
    ("a8", "essays.epub", "Essays", "Michel de Montaigne", (96, 60, 110), {}),
    ("a9", "zen.epub", "Zen and the Art of Reading on a Status Bar", "R. M. Pirsig", (40, 100, 110), {}),
]


def png(width, height, rgb):
    """A solid-colour PNG with a lighter band, so a cover is recognisable."""
    rows = bytearray()
    light = tuple(min(255, c + 60) for c in rgb)
    for y in range(height):
        rows.append(0)
        band = height // 5 < y < height // 5 + max(2, height // 12)
        rows.extend(bytes(light if band else rgb) * width)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


def transparent_png(width, height):
    """Black strokes on a transparent ground, as publishers ship line art."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            inked = (x + y) % 12 < 3 or y in (0, height - 1)
            rows.extend((0, 0, 0, 255) if inked else (0, 0, 0, 0))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def cache_dir(argv):
    if "--cache" in argv:
        return argv[argv.index("--cache") + 1]
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "omarchy-reader")


def library_dir():
    return os.path.join(os.path.expanduser("~"), "Books")


def entry(cache, spec):
    key, name, title, author, colour, extra = spec
    cover = ""
    size = (0, 0)
    if colour is not None:
        size = (300, 200) if extra.get("wide") else (200, 300)
        cover = os.path.join(cache, "books", key, "cover.png")
        if not os.path.exists(cover):
            write(cover, png(size[0], size[1], colour))
    return {
        "key": key,
        "path": os.path.join(library_dir(), name),
        "format": "pdf" if extra.get("external") else "epub",
        "title": title,
        "author": author,
        "cover": cover,
        "coverW": size[0],
        "coverH": size[1],
        "size": 1000,
        "mtime": 1780000000,
        "external": bool(extra.get("external")),
        "error": "This book is protected by DRM and can't be opened." if extra.get("drm") else "",
    }


PARAGRAPH = ("The quick brown fox jumps over the lazy dog, and having done so once, considers "
             "whether the exercise was worth repeating; the dog, for its part, offers no opinion. ")


def build_book(cache, spec):
    key, name, title, author, colour, extra = spec
    blocks = []
    toc = []
    sections = []
    chapters = 60 if extra.get("long_toc") else 6
    parts = 6 if extra.get("long_toc") else 0

    picture = os.path.join(cache, "books", key, "img", "0001.png")
    write(picture, png(320, 200, colour or (90, 90, 90)))
    line_art = os.path.join(cache, "books", key, "img", "0002.png")
    write(line_art, transparent_png(160, 60))

    for chapter in range(chapters):
        if parts and chapter % (chapters // parts) == 0:
            toc.append({"t": "Book %d" % (chapter // (chapters // parts) + 1), "d": 0, "b": len(blocks)})
        sections.append(len(blocks))
        heading = "Chapter %d" % (chapter + 1)
        toc.append({"t": heading if chapter != 2 else heading + ": In Which a Very Long Chapter Title "
                    "Wraps onto a Second Line of the Contents", "d": 1 if parts else 0, "b": len(blocks)})
        blocks.append({"k": "h", "l": 1, "t": heading, "s": 2})
        for paragraph in range(12):
            text = "%s %d.%d. " % (title, chapter + 1, paragraph + 1) + PARAGRAPH * (1 + paragraph % 3)
            blocks.append({"k": "p", "t": text.strip()})
        if chapter == 0:
            toc.append({"t": "A section inside", "d": (1 if parts else 0) + 1, "b": len(blocks)})
            blocks.append({"k": "h", "l": 2, "t": "A section inside"})
            blocks.append({"k": "p", "f": 1,
                           "t": "Styled text with <i>italics</i>, <b>bold</b>, an escaped &lt;tag&gt; &amp; "
                                "ampersand, a note<a href=\"b:%d\">¹</a> and an "
                                "<a href=\"https://example.org/?a=1&b=2\">external link</a>." % 3})
            blocks.append({"k": "p", "q": 1, "i": 1, "t": "A quoted passage, set apart from the text "
                           "around it. " + PARAGRAPH})
            blocks.append({"k": "hr"})
            blocks.append({"k": "img", "src": picture, "w": 320, "h": 200, "alt": "A picture"})
            blocks.append({"k": "img", "src": line_art, "w": 160, "h": 60, "alt": "Line art", "al": 1})
            blocks.append({"k": "p", "a": "c", "z": -1, "t": "A caption under the picture"})
            blocks.append({"k": "li", "m": "•", "i": 1, "t": "First item of a list"})
            blocks.append({"k": "li", "m": "•", "i": 1, "t": "Second item, long enough to wrap: " + PARAGRAPH})
            blocks.append({"k": "li", "m": "1.", "i": 2, "t": "A nested, numbered item"})
            blocks.append({"k": "pre", "t": "def hello():\n    return 'preformatted'"})
            blocks.append({"k": "tbl", "hdr": 1, "rows": [["Name", "Role", "Notes"],
                                                        ["Ada", "Engineer", "Wrote the first program"],
                                                        ["Grace", "Admiral", "Found the first bug"]]})
            blocks.append({"k": "p", "f": 1, "t": "Roses are red,<br>&#160;&#160;Violets are blue,<br>Verse keeps its lines."})
            blocks.append({"k": "p", "s": 1, "t": "After a scene break. " + PARAGRAPH})
            blocks.append({"k": "p", "c": 1, "t": "A continuation of an over-long paragraph. " + PARAGRAPH})

    return {
        "v": 1, "conv": CONV, "key": key, "format": "epub",
        "path": os.path.join(library_dir(), name), "size": 1000, "mtime": 1780000000,
        "title": title, "author": author, "language": "en",
        "sections": sections, "toc": toc, "blocks": blocks,
    }


def main(argv):
    cache = cache_dir(argv)
    args = [a for a in argv[1:] if a != "--"]
    # Scenario switches, given ahead of the command: an empty library, or a
    # helper that cannot run at all.
    empty = "--empty" in args
    if "--broken" in args:
        return 127
    if "--slow" in args:
        time.sleep(30)
    args = [a for a in args if a != "--empty"]
    command = args[0] if args else ""

    # One line per run, next to the cache, so a scenario can count runs.
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(os.path.join(os.path.dirname(cache), "fake-reader.log"), "a") as log:
        log.write(command + "\n")

    if command == "scan":
        if empty:
            result = {"ok": True, "dirs": [], "books": []}
        else:
            result = {"ok": True, "dirs": [library_dir()], "books": [entry(cache, spec) for spec in BOOKS]}
        write(os.path.join(cache, "index.json"), json.dumps(result).encode())
        print(json.dumps(result))
        return 0

    if command == "open" and len(args) > 1:
        name = os.path.basename(args[1])
        for spec in BOOKS:
            if spec[1] != name:
                continue
            if spec[5].get("drm"):
                print(json.dumps({"ok": False, "error": {
                    "code": "drm", "message": "This book is protected by DRM and can't be opened."}}))
                return 1
            if spec[5].get("external"):
                print(json.dumps({"ok": False, "error": {
                    "code": "unsupported", "message": "PDF files open in your PDF viewer, not in Reader."}}))
                return 1
            out = os.path.join(cache, "books", spec[0], "book.json")
            cached = os.path.exists(out)
            if not cached:
                write(out, json.dumps(build_book(cache, spec)).encode())
            print(json.dumps({"ok": True, "key": spec[0], "book": out, "cached": cached}))
            return 0
        print(json.dumps({"ok": False, "error": {"code": "missing", "message": "That book is no longer there."}}))
        return 1

    print(json.dumps({"ok": False, "error": {"code": "internal", "message": "Unknown command."}}))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
