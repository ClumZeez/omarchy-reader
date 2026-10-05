import support

import collections
import json
import os
import re
import tempfile
import time
import unittest

from reader import images, library

EXPECTATIONS = support.sample("corpus-expect.json")
# Seconds a book may take to convert, unless its expectations allow it more.
BUDGET = 2.0

TEXT_KINDS = ("p", "h", "li")
ALLOWED = {"k", "t", "f", "l", "m", "src", "w", "h", "alt", "al", "rows", "hdr", "a", "i", "q", "z", "s", "c"}
_TOKEN = re.compile(r'<a href="([^"]*)">|<(/?)([a-z]+)>|&([^;\s<>&]{0,12});|([<>&])')
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "#160": " ", "#8239": " "}
_FORBIDDEN = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f﻿]")
_RAW_SPACES = re.compile("[   - ]")
_HREF_UNSAFE = re.compile("[\\s\"'<>\x00-\x1f\x7f]")
_HREF_ESCAPED = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+);")
_EXTERNAL = re.compile(r"(?:https?|mailto):.", re.I)
_HTML_SPACE = re.compile(r"[ \t\n\r\f]+")


def check_markup(text, blocks):
    """The contract §5.2 rules that `f: 1` text breaks, by name; `(problems, visible text)`."""
    problems = set()
    visible = []
    open_tags = []
    position = 0
    marked = False
    first_break = last_break = False
    for match in _TOKEN.finditer(text):
        if match.start() > position:
            visible.append(text[position:match.start()])
            last_break = False
        position = match.end()
        href, closing, tag, entity, stray = match.groups()
        if stray is not None:
            problems.add("unescaped %s" % stray)
            visible.append(stray)
        elif entity is not None:
            if entity in _ENTITIES:
                visible.append(_ENTITIES[entity])
                last_break = False
            else:
                problems.add("entity other than amp/lt/gt/#160/#8239")
        elif href is not None:
            marked = True
            if "a" in open_tags:
                problems.add("nested <a>")
            open_tags.append("a")
            if not href:
                problems.add("empty href")
            elif _HREF_UNSAFE.search(href):
                problems.add("href with a quote, angle bracket, space or control character")
            elif _HREF_ESCAPED.search(href):
                problems.add("href with an entity (Qt does not decode it)")
            elif href.startswith("b:"):
                if not (href[2:].isdigit() and int(href[2:]) < blocks):
                    problems.add("b: link to no block")
            elif not _EXTERNAL.match(href):
                problems.add("href scheme other than b/http/https/mailto")
        elif tag == "br" and not closing:
            if not "".join(visible).strip():
                first_break = True
            last_break = True
        elif tag in ("b", "i", "u", "s", "a"):
            marked = True
            if not closing:
                if tag == "a":
                    problems.add("<a> without href")
                open_tags.append(tag)
            elif open_tags and open_tags[-1] == tag:
                open_tags.pop()
            else:
                problems.add("tags not properly nested")
        else:
            problems.add("tag other than b/i/u/s/a/br")
    if position < len(text):
        visible.append(text[position:])
        last_break = False
    if open_tags:
        problems.add("tag left open")
    if first_break:
        problems.add("<br> at the start")
    if last_break:
        problems.add("<br> at the end")
    if not marked and "<br>" not in text and not any(entity in text for entity in ("&#160;", "&#8239;")):
        problems.add("f:1 without any markup")
    if _RAW_SPACES.search(text):
        problems.add("literal no-break or exotic space in f:1")
    if "\n" in text or "\t" in text:
        problems.add("newline or tab in f:1")
    shown = "".join(visible)
    if _FORBIDDEN.search(shown):
        problems.add("control character or U+FEFF")
    if shown != shown.strip(" ") or "  " in shown:
        problems.add("whitespace not collapsed or trimmed")
    return problems, shown


def check_plain(text):
    """The rules `f: 0` text breaks."""
    problems = set()
    if _FORBIDDEN.search(text) or "\t" in text:
        problems.add("control character or U+FEFF")
    if text.endswith("\n") or text.startswith("\n"):
        problems.add("plain text starting or ending with a newline")
    if any(line != line.strip(" ") or "  " in line for line in text.split("\n")):
        problems.add("whitespace not collapsed or trimmed")
    return problems


def check_block(block, total):
    """Every rule of contract §5.1/§5.2 this block breaks, and its visible text."""
    problems = set()
    kind = block.get("k")
    shown = ""
    if set(block) - ALLOWED:
        problems.add("unknown field")
    if kind in TEXT_KINDS:
        text = block.get("t")
        if not isinstance(text, str):
            return {"text block without t"}, ""
        if block.get("f", 0) == 1:
            found, shown = check_markup(text, total)
            problems |= found
        else:
            if "f" in block:
                problems.add("f present with its default")
            problems |= check_plain(text)
            shown = text
        if not shown.strip(" \n  ­"):
            problems.add("empty text block")
        if kind == "h" and block.get("l") not in (1, 2, 3, 4, 5, 6):
            problems.add("heading level out of range")
        if kind == "li" and not isinstance(block.get("m"), str):
            problems.add("list item without marker")
    elif kind == "pre":
        shown = block.get("t", "")
        if not shown.strip() or "\t" in shown or shown.endswith("\n") or _FORBIDDEN.search(shown.replace("\n", "")):
            problems.add("pre empty, with tabs or a trailing newline")
    elif kind == "img":
        if not (isinstance(block.get("src"), str) and os.path.isabs(block["src"]) and os.path.isfile(block["src"])):
            problems.add("img src not on disk")
        if not all(isinstance(block.get(name), int) and block[name] >= 0 for name in ("w", "h")):
            problems.add("img without size")
        if not isinstance(block.get("alt"), str):
            problems.add("img without alt")
    elif kind == "tbl":
        rows = block.get("rows")
        if not rows or not all(isinstance(row, list) and row for row in rows):
            problems.add("table without rows")
        cells = []
        for row in rows or []:
            for cell in row:
                found, text = check_markup(cell, total)
                problems |= {"table cell: " + problem for problem in found - {"f:1 without any markup"}}
                cells.append(text)
        shown = " ".join(cells)
    elif kind != "hr":
        problems.add("unknown block kind")
    for name, allowed in (("a", ("c", "r")), ("i", (1, 2, 3, 4, 5, 6)), ("q", (1,)), ("z", (-1, 1, 2)),
                          ("s", (1, 2)), ("c", (1,)), ("al", (1,))):
        if name in block and block[name] not in allowed:
            problems.add("optional field %s out of range" % name)
    return problems, shown


def text_chars(shown, kind):
    if kind == "pre":
        return len(shown)
    return len(_HTML_SPACE.sub(" ", shown).strip(" "))


@unittest.skipUnless(support.library_books() and os.path.isfile(EXPECTATIONS),
                     "the real library or its expectations are not on this machine")
class AcceptanceTest(unittest.TestCase):
    """Every book of the real library, converted once and held to the contract."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.cache = os.path.join(cls.scratch.name, "cache")
        with open(EXPECTATIONS, encoding="utf-8") as handle:
            cls.expect = json.load(handle)
        cls.library = library.scan([support.LIBRARY], cls.cache)
        cls.entries = {os.path.basename(entry["path"]): entry for entry in cls.library["books"]}
        cls.books = {}
        cls.seconds = {}
        cls.failures = {}
        for path in support.library_books():
            name = os.path.basename(path)
            started = time.perf_counter()
            try:
                result = library.open_book(path, cls.cache)
            except Exception as error:  # reported per book by test_every_book_converts
                cls.failures[name] = "%s: %s" % (type(error).__name__, error)
                continue
            cls.seconds[name] = time.perf_counter() - started
            with open(result["book"], encoding="utf-8") as handle:
                cls.books[name] = json.load(handle)

    def each_book(self):
        for name in sorted(self.books):
            with self.subTest(book=name):
                yield name, self.books[name]

    def test_scan_finds_exactly_the_books(self):
        self.assertEqual(self.library["dirs"], [os.path.abspath(support.LIBRARY)])
        self.assertEqual(sorted(entry["path"] for entry in self.library["books"]), support.library_books())
        self.assertEqual(set(self.entries), set(self.expect))
        self.assertEqual(len(self.library["books"]), len(self.expect))
        self.assertEqual([entry["error"] for entry in self.library["books"] if entry["error"]], [])
        self.assertEqual(len({entry["key"] for entry in self.library["books"]}), len(self.library["books"]))

    def test_library_entries_meet_expectations(self):
        for name, expected in sorted(self.expect.items()):
            if name not in self.entries:
                continue
            entry = self.entries[name]
            with self.subTest(book=name):
                self.assertEqual(entry["title"], expected["title"])
                self.assertEqual(entry["author"], expected["author"])
                self.assertEqual(entry["format"], "epub")
                if expected["has_cover"]:
                    self.assertEqual([entry["coverW"], entry["coverH"]], expected["cover_px"])
                    with open(entry["cover"], "rb") as handle:
                        found = images.sniff(handle.read())
                    self.assertEqual([found.width, found.height], expected["cover_px"])
                else:
                    self.assertEqual((entry["cover"], entry["coverW"], entry["coverH"]), ("", 0, 0))

    def test_every_book_converts(self):
        self.assertEqual(self.failures, {})
        self.assertEqual(sorted(self.books), [os.path.basename(path) for path in support.library_books()])

    def test_conversion_time(self):
        for name, seconds in sorted(self.seconds.items()):
            with self.subTest(book=name):
                self.assertLess(seconds, self.expect.get(name, {}).get("budget", BUDGET))

    def test_books_meet_expectations(self):
        for name, book in self.each_book():
            expected = self.expect.get(name)
            if expected is None:
                continue
            entry = self.entries[name]
            self.assertEqual((book["title"], book["author"]), (expected["title"], expected["author"]))
            self.assertEqual((book["key"], book["path"], book["size"], book["mtime"]),
                             (entry["key"], entry["path"], entry["size"], entry["mtime"]))
            self.assertGreaterEqual(len(book["toc"]), expected["toc_min_entries"])
            self.assertLessEqual(len(book["sections"]), expected["spine_docs"])
            total = len(book["blocks"])
            chars = sum(text_chars(check_block(block, total)[1], block["k"]) for block in book["blocks"]
                        if block["k"] != "img")
            self.assertGreaterEqual(chars, expected["min_text_chars"])

    def test_structure(self):
        for name, book in self.each_book():
            blocks = book["blocks"]
            total = len(blocks)
            self.assertGreaterEqual(total, 1)
            sections = book["sections"]
            self.assertEqual(sections[0], 0)
            self.assertEqual(sections, sorted(set(sections)))
            self.assertLess(sections[-1], total)
            previous = -1
            for entry in book["toc"]:
                self.assertEqual(sorted(entry), ["b", "d", "t"])
                self.assertTrue(isinstance(entry["b"], int) and 0 <= entry["b"] < total, entry)
                self.assertTrue(entry["t"] and entry["t"] == " ".join(entry["t"].split()), entry)
                self.assertTrue(0 <= entry["d"] <= previous + 1, entry)
                previous = entry["d"]
            folder = os.path.join(self.cache, "books", book["key"], "img")
            for block in blocks:
                if block["k"] == "img":
                    self.assertEqual(os.path.dirname(block["src"]), folder)
                    self.assertTrue(os.path.isfile(block["src"]), block["src"])

    def test_coarse_contents_list_their_chapters(self):
        """Books whose declared contents were refined: the tree their expectations describe."""
        for name, book in self.each_book():
            shape = self.expect.get(name, {}).get("toc")
            if not shape:
                continue
            toc = book["toc"]
            tops = [index for index, entry in enumerate(toc) if entry["d"] == 0]
            self.assertEqual((len(tops), len(toc), max(entry["d"] for entry in toc)),
                             (shape["top"], shape["entries"], shape["depth"]))
            children = {toc[top]["t"]: toc[top + 1:end] for top, end in zip(tops, tops[1:] + [len(toc)])}
            for title, count in shape.get("children", {}).items():
                self.assertEqual(len(children[title]), count, title)
            for title, first in shape.get("first_child", {}).items():
                self.assertEqual(children[title][0]["t"], first)
            for title, last in shape.get("last_child", {}).items():
                self.assertEqual(children[title][-1]["t"], last)
            for top in tops:
                entries = children[toc[top]["t"]]
                self.assertNotEqual(len(entries), 1, toc[top])
                self.assertNotIn(toc[top]["t"], [entry["t"] for entry in entries])
                self.assertEqual([entry["b"] for entry in entries], sorted({entry["b"] for entry in entries}))
                self.assertEqual(entries[0]["b"] if entries else toc[top]["b"], toc[top]["b"])

    def test_no_contents_begin_far_into_the_book(self):
        """An unlisted opening has an entry; `toc_head` says how a book's contents begin."""
        for name, book in self.each_book():
            toc = book["toc"]
            first = min(entry["b"] for entry in toc)
            self.assertFalse(first > 20 and first > 0.05 * len(book["blocks"]), toc[0])
            head = self.expect.get(name, {}).get("toc_head")
            if head:
                self.assertEqual([entry["t"] for entry in toc[:len(head)]], head)
                self.assertEqual((toc[0]["d"], toc[0]["b"]), (0, 0))
                self.assertGreater(toc[1]["b"], 100)

    def test_transparent_pictures_are_marked(self):
        for name, book in self.each_book():
            marked = 0
            for block in book["blocks"]:
                if block["k"] == "img":
                    with open(block["src"], "rb") as handle:
                        self.assertEqual(block.get("al", 0), int(images.sniff(handle.read()).alpha), block)
                    marked += block.get("al", 0)
            self.assertGreaterEqual(marked, self.expect.get(name, {}).get("transparent_pictures_min", 0))

    def test_blocks_obey_the_markup_rules(self):
        broken = collections.Counter()
        examples = {}
        for name, book in sorted(self.books.items()):
            total = len(book["blocks"])
            for index, block in enumerate(book["blocks"]):
                for problem in check_block(block, total)[0]:
                    broken[problem] += 1
                    examples.setdefault(problem, "%s block %d: %.160r" % (name[:24], index, block))
        report = "\n".join("%6d  %s\n        e.g. %s" % (count, problem, examples[problem])
                           for problem, count in broken.most_common())
        self.assertEqual(sum(broken.values()), 0, "contract §5.1/§5.2 rules broken, by number of blocks:\n" + report)


class ValidatorTest(unittest.TestCase):
    """The validator itself: it must refuse what the contract refuses."""

    def problems(self, text, blocks=10):
        return check_markup(text, blocks)[0]

    def test_accepts_the_allowed_subset(self):
        for text in ('plain <i>it</i> <b>bo<i>th</i></b> <u>u</u> <s>s</s>', 'a<br>b <a href="b:3">x</a>',
                     'x &amp; &lt;y&gt; a&#160;b&#8239;c <a href="https://e.org/?a=1&b=2">l</a>',
                     '<a href="mailto:a@b.c"><i>m</i></a>', "one<br>&#160;&#160;two"):
            with self.subTest(text=text):
                self.assertEqual(self.problems(text), set())

    def test_refuses_everything_else(self):
        cases = {
            "<b>open": "tag left open", "<b><i>x</b></i>": "tags not properly nested", "x</i>": "tags not properly nested",
            "<em>x</em>": "tag other than b/i/u/s/a/br", '<img src="x">': "unescaped <", "<B>x</B>": "unescaped <",
            "a &quot;b&quot; <i>c</i>": "entity other than amp/lt/gt/#160/#8239", "a &nbsp; <i>c</i>": "entity other than amp/lt/gt/#160/#8239",
            "fish & <i>chips</i>": "unescaped &", "1 < 2 <i>x</i>": "unescaped <", "<br/>x <i>y</i>": "unescaped <",
            '<a href="b:1">a <a href="b:2">b</a></a>': "nested <a>", '<a href="">x</a>': "empty href",
            '<a href="b:10">x</a>': "b: link to no block", '<a href="b:x">x</a>': "b: link to no block",
            '<a href="javascript:x">x</a>': "href scheme other than b/http/https/mailto",
            '<a href="http://a b">x</a>': "href with a quote, angle bracket, space or control character",
            "<a href='b:1'>x</a>": "unescaped <", "<a>x</a>": "<a> without href",
            '<a href="http://e.org/?a=1&amp;b=2">x</a>': "href with an entity (Qt does not decode it)",
            "<br>x <i>y</i>": "<br> at the start", "<i>x</i><br>": "<br> at the end", "<b><br>x</b>": "<br> at the start",
            "just words": "f:1 without any markup", "a b <i>c</i>": "literal no-break or exotic space in f:1",
            "a b <i>c</i>": "literal no-break or exotic space in f:1", "a\nb <i>c</i>": "newline or tab in f:1",
            "a  b <i>c</i>": "whitespace not collapsed or trimmed", " a <i>c</i>": "whitespace not collapsed or trimmed",
            "a﻿b <i>c</i>": "control character or U+FEFF", "a\x85b <i>c</i>": "control character or U+FEFF",
        }
        for text, problem in cases.items():
            with self.subTest(text=text):
                self.assertIn(problem, self.problems(text))

    def test_blocks(self):
        good = [{"k": "p", "t": "a\nb c"}, {"k": "h", "l": 2, "t": "<i>x</i>", "f": 1, "a": "c", "s": 2},
                {"k": "li", "m": "", "t": "x", "i": 1}, {"k": "pre", "t": "a\n  b"}, {"k": "hr"},
                {"k": "tbl", "rows": [["a", "<b>b</b>"]], "hdr": 1}]
        for block in good:
            with self.subTest(block=block):
                self.assertEqual(check_block(block, 5)[0], set())
        bad = [{"k": "p", "t": ""}, {"k": "p", "t": " "}, {"k": "p", "t": "x\n"}, {"k": "p", "t": "x", "f": 0},
               {"k": "p", "t": "<i></i>", "f": 1}, {"k": "h", "t": "x"}, {"k": "li", "t": "x"}, {"k": "pre", "t": "a\tb"},
               {"k": "img", "src": "/nowhere/x.png", "w": 1, "h": 1, "alt": ""}, {"k": "tbl", "rows": []},
               {"k": "tbl", "rows": [["<em>x</em>"]]}, {"k": "p", "t": "x", "z": 3}, {"k": "p", "t": "x", "bogus": 1},
               {"k": "div"}, {"k": "p", "t": "a\x07b"}]
        for block in bad:
            with self.subTest(block=block):
                self.assertNotEqual(check_block(block, 5)[0], set())


if __name__ == "__main__":
    unittest.main()
