import support

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import epubkit
from epubkit import jpeg, png
from reader import CONVERTER_VERSION, epub, library
from reader.archive import Archive
from reader.errors import ReaderError

DRM_SENTENCE = "This book is protected by DRM and can't be opened."
COVERED = dict(manifest_extra='<item id="cover" href="cover.jpg" media-type="image/jpeg"/>',
               metadata_extra='<meta name="cover" content="cover"/>')


def fake_backend():
    """The stand-in backend the interface is tested against: the shapes to match."""
    spec = importlib.util.spec_from_file_location(
        "fake_reader", os.path.join(support.ROOT, "tests", "qml", "fake_reader.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shape(value):
    """Key names and value types, for comparing one JSON object's form with another's."""
    return {name: type(item) for name, item in value.items()}


class LibraryCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = scratch.name
        self.books = os.path.join(self.root, "Books")
        self.cache = os.path.join(self.root, "cache")
        os.makedirs(self.books)

    def book(self, name="book.epub", folder=None, **options):
        folder = folder or self.books
        os.makedirs(folder, exist_ok=True)
        options = dict(COVERED, **options)
        options.setdefault("files", {"cover.jpg": jpeg(300, 450)})
        return epubkit.make(folder, name, **options)

    def write(self, name, data, folder=None):
        path = os.path.join(folder or self.books, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def scan(self, *dirs):
        return library.scan(list(dirs) or [self.books], self.cache)

    def leftovers(self, folder):
        return [os.path.join(base, name) for base, _, names in os.walk(folder) for name in names
                if name.startswith(".") or name.endswith(".tmp")]


class NamesTest(unittest.TestCase):
    def test_title_from_filename(self):
        cases = {
            "/x/Some_Book  - A Author.epub": "Some Book - A Author",
            "/x/war_and_peace.kepub.epub": "war and peace",
            "/x/notes.fb2.zip": "notes",
            "/x/Vol. 2 of 3.AZW3": "Vol. 2 of 3",
            "/x/manual.PDF": "manual",
            "/x/.epub": ".epub",
            "/x/no extension": "no extension",
        }
        for path, title in cases.items():
            with self.subTest(path=path):
                self.assertEqual(library.title_from_filename(path), title)

    def test_display_author(self):
        cases = [
            (["Wells, H. G."], "H. G. Wells"),
            (["Carroll, Lewis", "Tenniel, John"], "Lewis Carroll, John Tenniel"),
            (["Stevenson, Robert Louis"], "Robert Louis Stevenson"),
            (["von Arnim, Elizabeth"], "Elizabeth von Arnim"),
            (["Le Guin, Ursula K."], "Ursula K. Le Guin"),
            (["E. E. E. Nesbit"], "E. E. E. Nesbit"),
            (["Jules Verne, Lewis Mercier"], "Jules Verne, Lewis Mercier"),
            (["King, Martin Luther, Jr."], "King, Martin Luther, Jr."),
            (["Martin Luther King, Jr."], "Martin Luther King, Jr."),
            (["Davis, Sammy, Jr"], "Davis, Sammy, Jr"),
            (["Smith, PhD"], "Smith, PhD"),
            (["Agent 47, The"], "Agent 47, The"),
            (["Various"], "Various"),
            (["  Jane   Austen ", "", "Jane Austen"], "Jane Austen"),
            (["Tolstoy, graf Leo Nikolayevich of Yasnaya Polyana"], "Tolstoy, graf Leo Nikolayevich of Yasnaya Polyana"),
            ([], ""),
        ]
        for authors, line in cases:
            with self.subTest(authors=authors):
                self.assertEqual(library.display_author(authors), line)


class NameBoundsTest(unittest.TestCase):
    def test_a_title_is_clipped_to_what_a_tile_can_show(self):
        self.assertEqual(library._name("An Ordinary Title"), "An Ordinary Title")
        self.assertEqual(library._name("x" * library.NAME_LIMIT), "x" * library.NAME_LIMIT)
        clipped = library._name("word " * 1_000_000)
        self.assertEqual(len(clipped), library.NAME_LIMIT)
        self.assertTrue(clipped.endswith("word…"))

    def test_the_author_line_is_clipped_too(self):
        self.assertEqual(library.display_author(["Wells, H. G.", "Jules Verne"]), "H. G. Wells, Jules Verne")
        crowd = library.display_author(["Writer Number %d" % number for number in range(100_000)])
        self.assertLessEqual(len(crowd), library.NAME_LIMIT + 1)
        self.assertTrue(crowd.startswith("Writer Number 0, Writer Number 1, "))
        one = library.display_author(["y" * 4_000_000])
        self.assertEqual(len(one), library.NAME_LIMIT + 1)


class DetectTest(LibraryCase):
    def test_content_decides(self):
        pdb = b"\x00" * 60
        cases = [
            ("kindle.epub", pdb + b"BOOKMOBI" + b"\x00" * 40, "mobi"),
            ("palm.txt", pdb + b"TEXtREAd" + b"\x00" * 40, "mobi"),
            ("doc.epub", b"%PDF-1.7\n...", "pdf"),
            ("scan.txt", b"AT&TFORM\x00\x00\x00\x10DJVU", "djvu"),
            ("novel.epub", b'<?xml version="1.0" encoding="utf-8"?>\n<FictionBook xmlns="x"><body/></FictionBook>', "fb2"),
            ("novel16.txt", "﻿<?xml version='1.0'?><FictionBook>".encode("utf-16"), "fb2"),
            ("page.txt", b"\xef\xbb\xbf\n <!DOCTYPE html><html><body>x</body></html>", "html"),
            ("page.epub", b"<html><head></head><body>x</body></html>", "html"),
            ("page2.mobi", b'<?xml version="1.0"?>\n<html xmlns="http://www.w3.org/1999/xhtml">', "html"),
        ]
        for name, data, kind in cases:
            with self.subTest(name=name):
                self.assertEqual(library.detect(self.write(name, data)), kind)

    def test_extension_decides_the_rest(self):
        cases = {"a.txt": "text", "a.TXT": "text", "a.htm": "html", "a.xhtml": "html", "a.mobi": "mobi",
                 "a.prc": "mobi", "a.azw": "mobi", "a.azw3": "mobi", "a.kf8": "mobi", "a.fb2": "fb2",
                 "a.fbz": "fb2", "a.fb2.zip": "fb2", "a.cbz": "comic", "a.pdf": "pdf", "a.djvu": "djvu",
                 "a.epub": "epub", "a.kepub": "epub", "a.kepub.epub": "epub",
                 "a.doc": None, "a.zip": None, "a": None, "a.jpg": None}
        for name, kind in cases.items():
            with self.subTest(name=name):
                self.assertEqual(library.detect(self.write(name, b"just some words, nothing more")), kind)

    def test_zip_families(self):
        def zipped(name, members):
            return epubkit.write_zip(os.path.join(self.books, name), members)

        self.assertEqual(library.detect(self.book("renamed.cbz")), "epub")
        self.assertEqual(library.detect(self.book("bare.zip", container=False, mimetype=False)), "epub")
        self.assertEqual(library.detect(zipped("typed.zip", {"mimetype": "application/epub+zip\r\n", "a.html": "<p>x</p>"})), "epub")
        self.assertEqual(library.detect(zipped("pages.epub", {"index.html": "<p>x</p>", "ch1.html": "<p>y</p>"})), "epub")
        self.assertEqual(library.detect(zipped("novel.epub", {"novel.fb2": "<FictionBook/>"})), "fb2")
        self.assertEqual(library.detect(zipped("strip.epub", {"1.jpg": jpeg(80, 80), "2.PNG": png(80, 80), "info.xml": "<x/>",
                                                                "__MACOSX/._1.jpg": "junk"})), "comic")
        self.assertEqual(library.detect(zipped("few.cbz", {"1.jpg": jpeg(80, 80), "a.txt": "a", "b.txt": "b"})), "comic")
        self.assertIsNone(library.detect(zipped("misc.zip", {"a.txt": "a", "b.txt": "b"})))

    def test_missing_file(self):
        self.assertIsNone(library.detect(os.path.join(self.books, "nope.epub")))


class ScanTest(LibraryCase):
    def test_entry_shape_matches_the_interface_stand_in(self):
        self.book(title="Shaped", creators=("Wells, H. G.",))
        result = self.scan()
        fake = fake_backend()
        self.assertEqual(list(result), ["ok", "dirs", "books"])
        self.assertEqual(result["dirs"], [self.books])
        entry = result["books"][0]
        self.assertEqual(shape(entry), shape(fake.entry(os.path.join(self.root, "fake"), fake.BOOKS[0])))
        self.assertEqual((entry["title"], entry["author"], entry["format"]), ("Shaped", "H. G. Wells", "epub"))
        self.assertEqual((entry["coverW"], entry["coverH"], entry["external"], entry["error"]), (300, 450, False, ""))
        self.assertEqual(entry["cover"], os.path.join(self.cache, "books", entry["key"], "cover.jpg"))
        with open(entry["cover"], "rb") as handle:
            self.assertEqual(handle.read(), jpeg(300, 450))
        status = os.stat(entry["path"])
        self.assertEqual((entry["size"], entry["mtime"]), (status.st_size, int(status.st_mtime)))
        self.assertRegex(entry["key"], r"^[0-9a-f]{20}$")

    def test_meta_and_index_files(self):
        self.book(title="Recorded")
        result = self.scan()
        entry = result["books"][0]
        with open(os.path.join(self.cache, "books", entry["key"], "meta.json"), encoding="utf-8") as handle:
            meta = json.load(handle)
        self.assertEqual({name: meta[name] for name in entry}, entry)
        self.assertEqual(meta["source"], {"path": entry["path"], "size": entry["size"], "mtime": entry["mtime"]})
        self.assertEqual(meta["conv"], CONVERTER_VERSION)
        with open(os.path.join(self.cache, "index.json"), encoding="utf-8") as handle:
            index = json.load(handle)
        self.assertEqual({name: index[name] for name in result}, result)
        self.assertEqual(self.leftovers(self.cache), [])
        self.assertEqual(sorted(os.listdir(self.books)), ["book.epub"])

    def test_sorted_by_title(self):
        for number, title in enumerate(("The Zebra", "an Apple", "Émile", "A Dog", "the bear", "  (Cat)")):
            self.book("b%d.epub" % number, title=title, docs=(("c.xhtml", "<p>%d</p>" % number),))
        titles = [entry["title"] for entry in self.scan()["books"]]
        self.assertEqual(titles, ["an Apple", "the bear", "(Cat)", "A Dog", "Émile", "The Zebra"])

    def test_walk_rules(self):
        def unique(name, folder):
            return self.book(name, folder, docs=(("c.xhtml", "<p>%s</p>" % os.path.join(folder, name)),))

        deep = self.books
        for level in range(library.MAX_DEPTH):
            deep = os.path.join(deep, "d%d" % level)
        expected = sorted([
            unique("top.EPUB", self.books),
            unique("inner.kepub.epub", os.path.join(self.books, "Author", "Title (12)")),
            unique("deep.epub", deep),
        ])
        unique("too-deep.epub", os.path.join(deep, "one-more"))
        unique("hidden.epub", os.path.join(self.books, ".stash"))
        unique("sidecar.epub", os.path.join(self.books, "Some Book.sdr"))
        unique(".dotfile.epub", self.books)
        outside = os.path.join(self.root, "elsewhere")
        unique("linked.epub", outside)
        os.symlink(outside, os.path.join(self.books, "link"))
        self.write("notes.docx", b"x")
        self.write("metadata.opf", b"<package/>")
        self.write("Title - Author.original_epub", b"x")
        self.write("cover.jpg", jpeg(100, 100))
        found = sorted(entry["path"] for entry in self.scan()["books"])
        self.assertEqual(found, expected)

    def test_default_directories(self):
        home = os.path.join(self.root, "home")
        first = self.book("a.epub", os.path.join(home, "Books"), docs=(("c.xhtml", "<p>a</p>"),))
        second = self.book("b.epub", os.path.join(home, "Documents", "EPUB"), docs=(("c.xhtml", "<p>b</p>"),))
        self.book("c.epub", os.path.join(home, "Downloads"), docs=(("c.xhtml", "<p>c</p>"),))
        with mock.patch.dict(os.environ, {"HOME": home}):
            result = library.scan(None, self.cache)
            tilde = library.scan(["~/Downloads", "~/Nowhere", "~/Downloads/"], self.cache)
        self.assertEqual(result["dirs"], [os.path.join(home, "Books"), os.path.join(home, "Documents", "EPUB")])
        self.assertEqual(sorted(entry["path"] for entry in result["books"]), [first, second])
        self.assertEqual(tilde["dirs"], [os.path.join(home, "Downloads")])
        self.assertEqual(len(tilde["books"]), 1)

    def test_no_directories(self):
        result = library.scan([os.path.join(self.root, "absent")], self.cache)
        self.assertEqual(result, {"ok": True, "dirs": [], "books": []})

    def test_warm_scan_only_stats(self):
        self.book("a.epub", title="A", docs=(("c.xhtml", "<p>a</p>"),))
        self.book("b.epub", title="B", docs=(("c.xhtml", "<p>b</p>"),))
        self.write("manual.pdf", b"%PDF-1.4 ...")
        self.write("garbage.epub", b"never an archive")
        self.write("story.mobi", b"\x00" * 60 + b"BOOKMOBI" + b"\x00" * 200)
        noise = bytes(range(256)) * 8
        self.book("locked.epub", title="Locked", docs=(("ch1.xhtml", noise),), ncx_points=False,
                  root_files={"META-INF/encryption.xml": epubkit.encryption((epubkit.AES, "OEBPS/ch1.xhtml"))})
        with mock.patch.dict(library._MODULES, {"mobi": "no_such_module"}):
            self.warm_scan_reads_nothing()

    def warm_scan_reads_nothing(self):
        cold = self.scan()
        self.assertEqual(len(cold["books"]), 6)
        self.assertEqual(sorted(bool(entry["error"]) for entry in cold["books"]), [False] * 3 + [True] * 3)
        forbidden = AssertionError("a warm scan must not read any book")
        real_open = open

        def guarded_open(path, *args, **kwargs):
            if str(path).startswith(self.books):
                raise forbidden
            return real_open(path, *args, **kwargs)

        with mock.patch.object(Archive, "open", side_effect=forbidden), \
                mock.patch.object(library, "detect", side_effect=forbidden), \
                mock.patch.object(library, "_load", side_effect=forbidden), \
                mock.patch("builtins.open", guarded_open):
            warm = self.scan()
        self.assertEqual(warm, cold)

    def test_each_new_file_is_described_once(self):
        for number in range(5):
            self.book("b%d.epub" % number, title="B%d" % number, docs=(("c.xhtml", "<p>%d</p>" % number),))
        calls = []
        real = epub.read_meta

        def counting(path):
            calls.append(path)
            return real(path)

        with mock.patch.object(epub, "read_meta", counting):
            self.scan()
            self.book("b9.epub", title="B9", docs=(("c.xhtml", "<p>9</p>"),))
            result = self.scan()
        self.assertEqual(len(result["books"]), 6)
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(set(calls)), 6)

    def test_interrupted_scan_resumes_where_it_stopped(self):
        for number in range(6):
            self.book("b%d.epub" % number, title="B%d" % number, docs=(("c.xhtml", "<p>%d</p>" % number),))
        self.scan()
        os.unlink(os.path.join(self.cache, "index.json"))
        calls = []
        real = epub.read_meta

        def interrupted(path):
            if len(calls) == 4:
                raise KeyboardInterrupt  # the shell gave up waiting and killed the scan
            calls.append(path)
            return real(path)

        with mock.patch.object(library, "CHECKPOINT_SECONDS", 0.0):
            with mock.patch.object(epub, "read_meta", interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    self.scan()
            with open(os.path.join(self.cache, "index.json"), "rb") as handle:
                partial = json.load(handle)
            self.assertEqual([entry["title"] for entry in partial["books"]], ["B0", "B1", "B2", "B3"])
            del calls[:]
            with mock.patch.object(epub, "read_meta", lambda path: calls.append(path) or real(path)):
                result = self.scan()
        self.assertEqual(sorted(os.path.basename(path) for path in calls), ["b4.epub", "b5.epub"])
        self.assertEqual([entry["title"] for entry in result["books"]], ["B%d" % number for number in range(6)])

    def test_checkpoint_keeps_what_the_last_scan_knew(self):
        for number in range(4):
            self.book("b%d.epub" % number, title="B%d" % number, docs=(("c.xhtml", "<p>%d</p>" % number),))
        self.scan()
        # The first file changes; a scan cut short right after reading it must
        # not forget the three it has not come to yet.
        self.book("b0.epub", title="B0 again", docs=(("c.xhtml", "<p>changed</p>"),))
        calls = []
        real = epub.read_meta

        def counting(path):
            calls.append(path)
            return real(path)

        real_stat = library._stat

        def cut_short(path):
            if calls and not path.endswith("b0.epub"):
                raise KeyboardInterrupt
            return real_stat(path)

        with mock.patch.object(library, "CHECKPOINT_SECONDS", 0.0), \
                mock.patch.object(epub, "read_meta", counting), \
                mock.patch.object(library, "_stat", cut_short):
            with self.assertRaises(KeyboardInterrupt):
                self.scan()
        del calls[:]
        with mock.patch.object(epub, "read_meta", counting):
            result = self.scan()
        self.assertEqual(calls, [])
        self.assertEqual([entry["title"] for entry in result["books"]], ["B0 again", "B1", "B2", "B3"])

    def test_names_carry_nothing_invisible(self):
        # A soft hyphen belongs in running text, where it lets a word wrap;
        # in a title it only breaks searching and sorting.
        self.book(title="Zu\u00adsam\u00admen\u200bfas\u00adsung", creators=("M\u00fcl\u00adler,  Anna",),
                  docs=(("c.xhtml", "<p>text</p>"),))
        entry = self.scan()["books"][0]
        self.assertEqual((entry["title"], entry["author"]), ("Zusammenfassung", "Anna M\u00fcller"))
        with open(library.open_book(entry["path"], self.cache)["book"], encoding="utf-8") as handle:
            book = json.load(handle)
        self.assertEqual((book["title"], book["author"]), ("Zusammenfassung", "Anna M\u00fcller"))

    def test_new_epub_is_opened_no_more_than_twice(self):
        self.book()
        opened = []
        real = Archive.open.__func__

        def counting(cls, path, **options):
            opened.append(path)
            return real(cls, path, **options)

        with mock.patch.object(Archive, "open", classmethod(counting)):
            entry = self.scan()["books"][0]
        self.assertLessEqual(len(opened), 2)
        self.assertEqual(entry["key"], epub.content_key(entry["path"]))

    def test_changed_file_is_read_again(self):
        path = self.book(title="Before")
        first = self.scan()["books"][0]
        self.book(title="After", files={"cover.jpg": jpeg(310, 460)})
        os.utime(path, (first["mtime"] + 10, first["mtime"] + 10))
        second = self.scan()["books"][0]
        self.assertEqual((second["title"], second["coverW"], second["mtime"]), ("After", 310, first["mtime"] + 10))
        self.assertEqual(second["key"], first["key"])

    def test_cover_type_change_leaves_one_cover(self):
        path = self.book()
        first = self.scan()["books"][0]
        self.book(manifest_extra='<item id="cover" href="cover.png" media-type="image/png"/>',
                  files={"cover.png": png(100, 150)})
        os.utime(path, (first["mtime"] + 10, first["mtime"] + 10))
        second = self.scan()["books"][0]
        folder = os.path.dirname(second["cover"])
        self.assertEqual(sorted(os.listdir(folder)), ["cover.png", "meta.json"])

    def test_lost_cover_file_is_restored(self):
        self.book()
        cover = self.scan()["books"][0]["cover"]
        os.unlink(cover)
        self.assertEqual(self.scan()["books"][0]["cover"], cover)
        self.assertTrue(os.path.isfile(cover))

    def test_removed_file_leaves_the_library(self):
        path = self.book()
        self.assertEqual(len(self.scan()["books"]), 1)
        os.unlink(path)
        self.assertEqual(self.scan()["books"], [])

    def test_duplicates_collapse_to_the_first_path(self):
        first = self.book("copy.epub", os.path.join(self.books, "a"), title="Same")
        second = os.path.join(self.books, "z-renamed.epub")
        shutil.copy(first, second)
        self.book("other.epub", title="Other", docs=(("c.xhtml", "<p>other</p>"),))
        result = self.scan()
        self.assertEqual([entry["title"] for entry in result["books"]], ["Other", "Same"])
        self.assertEqual(result["books"][1]["path"], first)
        with mock.patch.object(library, "_describe", side_effect=AssertionError("duplicates stay cached")):
            self.assertEqual(self.scan(), result)
        os.unlink(first)
        self.assertEqual(self.scan()["books"][1]["path"], second)

    def test_book_without_cover(self):
        self.book(manifest_extra="", metadata_extra="", files={})
        entry = self.scan()["books"][0]
        self.assertEqual((entry["cover"], entry["coverW"], entry["coverH"]), ("", 0, 0))

    def test_unreadable_files_are_listed_with_the_sentence_open_gives(self):
        broken = self.write("Broken_Book.epub", b"this was never an archive")
        empty = self.write("empty.epub", b"")
        noise = bytes(range(256)) * 8
        locked = self.book("locked.epub", title="Locked Title", docs=(("ch1.xhtml", noise),), ncx_points=False,
                           root_files={"META-INF/encryption.xml": epubkit.encryption((epubkit.AES, "OEBPS/ch1.xhtml"))})
        by_path = {entry["path"]: entry for entry in self.scan()["books"]}
        self.assertEqual(len(by_path), 3)
        for path in (broken, empty, locked):
            with self.subTest(path=path):
                with self.assertRaises(ReaderError) as caught:
                    library.open_book(path, self.cache)
                self.assertEqual(by_path[path]["error"], caught.exception.message)
                self.assertRegex(by_path[path]["key"], r"^[0-9a-f]{20}$")
        self.assertEqual(by_path[broken]["title"], "Broken Book")
        self.assertEqual((by_path[locked]["title"], by_path[locked]["error"], by_path[locked]["coverW"]),
                         ("Locked Title", DRM_SENTENCE, 300))

    def test_finished_download_is_read_again(self):
        path = self.write("late.epub", b"half a downl")
        self.assertNotEqual(self.scan()["books"][0]["error"], "")
        status = os.stat(path)
        shutil.copy(self.book("whole.epub", os.path.join(self.root, "aside"), title="Whole"), path)
        os.utime(path, (status.st_mtime, status.st_mtime))
        entry = self.scan()["books"][0]
        self.assertEqual((entry["title"], entry["error"]), ("Whole", ""))

    def test_format_that_gains_a_reader_is_read_again(self):
        self.write("story.txt", b"Once upon a time.")
        with mock.patch.dict(library._MODULES, {"text": "no_such_module"}):
            first = self.scan()["books"][0]
        self.assertIn("Text", first["error"])
        reader = mock.Mock(spec=["read_meta"])
        reader.read_meta.return_value = {"title": "Once", "authors": ["Grimm, Jacob"], "cover": None}
        with mock.patch.object(library, "_has_reader", return_value=True), \
                mock.patch.object(library, "_load", return_value=reader):
            second = self.scan()["books"][0]
        self.assertEqual((second["title"], second["author"], second["error"]), ("Once", "Jacob Grimm", ""))

    def test_external_formats(self):
        self.write("User_Manual.pdf", b"%PDF-1.4 ...")
        self.write("scan.djvu", b"AT&TFORM\x00\x00\x00\x10DJVU")
        entries = self.scan()["books"]
        self.assertEqual([(entry["title"], entry["format"], entry["external"], entry["error"], entry["cover"])
                          for entry in entries],
                         [("scan", "djvu", True, "", ""), ("User Manual", "pdf", True, "", "")])
        for entry in entries:
            with self.assertRaises(ReaderError) as caught:
                library.open_book(entry["path"], self.cache)
            self.assertEqual(caught.exception.code, "unsupported")
        self.assertIn("PDF", library._EXTERNAL["pdf"])
        self.assertIn("DjVu", library._EXTERNAL["djvu"])

    def test_format_without_a_module(self):
        self.write("story.mobi", b"\x00" * 60 + b"BOOKMOBI" + b"\x00" * 200)
        self.book("fine.epub", title="Fine")
        with mock.patch.dict(library._MODULES, {"mobi": "no_such_module"}):
            entries = {entry["format"]: entry for entry in self.scan()["books"]}
            with self.assertRaises(ReaderError) as caught:
                library.open_book(entries["mobi"]["path"], self.cache)
        self.assertEqual(caught.exception.code, "unsupported")
        self.assertIn("Kindle", caught.exception.message)
        self.assertEqual(entries["mobi"]["error"], caught.exception.message)
        self.assertEqual((entries["mobi"]["title"], entries["epub"]["error"]), ("story", ""))

    def test_broken_format_module_does_not_break_the_others(self):
        self.write("story.fb2", b"<FictionBook/>")
        self.book("fine.epub", title="Fine")
        real = library.importlib.import_module

        def importing(name, *args):
            if name.endswith(".fb2"):
                raise SyntaxError("broken module")
            return real(name, *args)

        with mock.patch.object(library.importlib, "import_module", importing), \
                mock.patch.object(library.traceback, "print_exc"):
            entries = {entry["format"]: entry for entry in self.scan()["books"]}
        self.assertIn("FictionBook", entries["fb2"]["error"])
        self.assertEqual(entries["epub"]["title"], "Fine")

    def test_format_module_that_crashes_on_a_file(self):
        self.book("bad.epub")
        with mock.patch.object(epub, "read_meta", side_effect=RuntimeError("boom")), \
                mock.patch.object(library.traceback, "print_exc"):
            entry = self.scan()["books"][0]
        self.assertEqual(entry["title"], "bad")
        self.assertNotIn("boom", entry["error"])
        self.assertNotEqual(entry["error"], "")

    def test_sampled_key(self):
        data = os.urandom(300 * 1024)
        first = self.write("a.pdf", b"%PDF" + data)
        moved = self.write("sub/b.pdf", b"%PDF" + data)
        changed = self.write("c.pdf", b"%PDF" + data[:150 * 1024] + b"!" + data[150 * 1024 + 1:])
        longer = self.write("d.pdf", b"%PDF" + data + b"x")
        keys = [library.book_key(path) for path in (first, moved, changed, longer)]
        self.assertRegex(keys[0], r"^[0-9a-f]{20}$")
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], keys[3])
        self.assertEqual(len(library.book_key(self.write("tiny.pdf", b"%PDF"))), 20)
        del changed

    def test_stale_index_is_ignored(self):
        self.book()
        result = self.scan()
        for content in (b"", b"{not json", b"[]", json.dumps({"v": 0, "books": result["books"]}).encode(),
                        json.dumps({"v": library.INDEX_VERSION, "books": [{"path": 5}, "x", None]}).encode()):
            with self.subTest(content=content[:20]):
                with open(os.path.join(self.cache, "index.json"), "wb") as handle:
                    handle.write(content)
                self.assertEqual(self.scan(), result)


class UnwritableCacheTest(LibraryCase):
    """A full disk or a read-only cache costs the head start, never the answer."""

    def shelf(self):
        self.book("a.epub", title="Alpha", docs=(("a.xhtml", "<p>alpha</p>"),))
        self.book("b.epub", title="Beta", docs=(("b.xhtml", "<p>beta</p>"),))

    def scan(self, *dirs):
        self.said = io.StringIO()
        with contextlib.redirect_stderr(self.said):
            return super().scan(*dirs)

    def test_a_warm_scan_answers_from_what_it_has(self):
        self.shelf()
        first = self.scan()
        with mock.patch.object(library, "write_atomic", side_effect=OSError(28, "No space left on device")):
            again = self.scan()
        self.assertEqual(again, first)
        self.assertEqual(self.said.getvalue().count("\n"), 1)
        self.assertEqual(self.scan(), first)
        self.assertEqual(self.said.getvalue(), "")

    def test_a_cold_scan_lists_the_books_without_their_covers(self):
        self.shelf()
        with mock.patch.object(library, "write_atomic", side_effect=OSError(28, "No space left on device")):
            result = self.scan()
        self.assertTrue(result["ok"])
        self.assertEqual([(entry["title"], entry["cover"], entry["coverW"], entry["error"]) for entry in result["books"]],
                         [("Alpha", "", 0, ""), ("Beta", "", 0, "")])
        self.assertFalse(os.path.exists(os.path.join(self.cache, "index.json")))
        self.assertEqual(self.said.getvalue().count("\n"), 1)  # said once, not once a book
        # With room again, the same scan gives the covers after all.
        self.assertTrue(all(os.path.isfile(entry["cover"]) for entry in self.scan()["books"]))

    @unittest.skipIf(os.geteuid() == 0, "nothing is read-only to root")
    def test_a_cache_folder_that_is_read_only(self):
        self.shelf()
        os.makedirs(self.cache)
        os.chmod(self.cache, 0o500)
        self.addCleanup(os.chmod, self.cache, 0o700)
        result = self.scan()
        self.assertEqual([entry["title"] for entry in result["books"]], ["Alpha", "Beta"])
        self.assertEqual(os.listdir(self.cache), [])


class OpenTest(LibraryCase):
    def test_a_book_that_converts_into_too_much_is_refused(self):
        # The reader parses a converted book in one go, on the thread that draws the bar.
        path = self.book(docs=(("c1.xhtml", '<p>one</p><img src="p.png" alt=""/>'),),
                         files={"cover.jpg": jpeg(300, 450), "p.png": png(40, 40)})
        folder = os.path.dirname(library.open_book(path, self.cache)["book"])
        self.assertEqual(sorted(os.listdir(folder)), ["book.json", "img"])
        os.utime(path, (1, 1))  # a changed file is converted afresh
        with mock.patch.object(library, "BOOK_LIMIT", 200):
            with self.assertRaises(ReaderError) as caught:
                library.open_book(path, self.cache)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("corrupt", "This book is too large to open."))
        self.assertEqual(os.listdir(folder), [])

    def test_a_conversion_that_fails_leaves_no_pictures_behind(self):
        path = self.book(docs=(("c1.xhtml", '<p>one</p><img src="p.png" alt=""/>'),),
                         files={"cover.jpg": jpeg(300, 450), "p.png": png(40, 40)})
        with mock.patch.object(library, "_book_body", side_effect=MemoryError):
            with self.assertRaises(MemoryError):
                library.open_book(path, self.cache)
        folder = os.path.join(self.cache, "books", library.book_key(path, epub))
        self.assertEqual(os.listdir(folder), [])

    def load(self, result):
        with open(result["book"], encoding="utf-8") as handle:
            return json.load(handle)

    def test_open_converts_then_reuses(self):
        path = self.book(title="Opened", creators=("Austen, Jane",),
                         docs=(("ch1.xhtml", '<h1>One</h1><img src="pic.png" alt="a picture"/>'), ("ch2.xhtml", "<h1>Two</h1><p>beta</p>")),
                         files={"cover.jpg": jpeg(300, 450), "pic.png": png(200, 120)})
        first = library.open_book(path, self.cache)
        folder = os.path.join(self.cache, "books", first["key"])
        self.assertEqual(first, {"ok": True, "key": first["key"], "book": os.path.join(folder, "book.json"), "cached": False})
        book = self.load(first)
        fake = fake_backend()
        expected = fake.build_book(os.path.join(self.root, "fake"), fake.BOOKS[0])
        self.assertEqual(shape(book), shape(expected))
        status = os.stat(path)
        self.assertEqual((book["v"], book["conv"], book["key"], book["format"], book["path"], book["size"], book["mtime"]),
                         (1, CONVERTER_VERSION, first["key"], "epub", path, status.st_size, int(status.st_mtime)))
        self.assertEqual((book["title"], book["author"], book["language"]), ("Opened", "Jane Austen", "en"))
        self.assertEqual(book["sections"], [0, 2])
        self.assertEqual(book["toc"], [{"t": "One", "d": 0, "b": 0}, {"t": "Two", "d": 0, "b": 2}])
        self.assertEqual(book["blocks"][1]["src"], os.path.join(folder, "img", "0001.png"))
        self.assertTrue(os.path.isfile(book["blocks"][1]["src"]))
        self.assertEqual(self.leftovers(self.cache), [])

        with mock.patch.object(epub, "convert", side_effect=AssertionError("already converted")):
            second = library.open_book(path, self.cache)
        self.assertEqual(second, dict(first, cached=True))

    def test_same_key_as_scan(self):
        path = self.book()
        self.assertEqual(library.open_book(path, self.cache)["key"], self.scan()["books"][0]["key"])

    def test_path_outside_the_library_and_with_tilde(self):
        home = os.path.join(self.root, "home")
        self.book("far.epub", os.path.join(home, "Desk"), title="Far")
        with mock.patch.dict(os.environ, {"HOME": home}):
            result = library.open_book("~/Desk/far.epub", "~/kept")
        self.assertEqual(result["book"], os.path.join(home, "kept", "books", result["key"], "book.json"))
        self.assertEqual(self.load(result)["path"], os.path.join(home, "Desk", "far.epub"))

    def test_changed_file_is_converted_again(self):
        path = self.book()
        first = library.open_book(path, self.cache)
        status = os.stat(path)
        os.utime(path, (status.st_mtime + 5, status.st_mtime + 5))
        second = library.open_book(path, self.cache)
        self.assertFalse(second["cached"])
        self.assertEqual(self.load(second)["mtime"], int(status.st_mtime) + 5)
        self.assertTrue(library.open_book(path, self.cache)["cached"])
        self.assertEqual(first["key"], second["key"])

    def test_new_converter_version_converts_again(self):
        path = self.book()
        library.open_book(path, self.cache)
        with mock.patch.object(library, "CONVERTER_VERSION", "next"):
            result = library.open_book(path, self.cache)
            self.assertFalse(result["cached"])
            self.assertEqual(self.load(result)["conv"], "next")
            self.assertTrue(library.open_book(path, self.cache)["cached"])
        self.assertFalse(library.open_book(path, self.cache)["cached"])

    def test_moved_book_is_converted_for_its_new_path(self):
        path = self.book()
        first = library.open_book(path, self.cache)
        moved = os.path.join(self.books, "moved.epub")
        os.rename(path, moved)
        second = library.open_book(moved, self.cache)
        self.assertEqual((second["key"], second["cached"]), (first["key"], False))
        self.assertEqual(self.load(second)["path"], moved)

    def test_damaged_cache_is_not_trusted(self):
        path = self.book()
        result = library.open_book(path, self.cache)
        for damage in (b"", b"{", b'{"v":1,"conv":"0"}'):
            with self.subTest(damage=damage):
                with open(result["book"], "wb") as handle:
                    handle.write(damage)
                self.assertFalse(library.open_book(path, self.cache)["cached"])
                self.assertEqual(len(self.load(result)["blocks"]), 4)

    def test_book_json_is_written_last(self):
        path = self.book(docs=(("ch1.xhtml", '<p>a</p><img src="pic.png" alt=""/>'),),
                         files={"cover.jpg": jpeg(300, 450), "pic.png": png(200, 120)})
        result = library.open_book(path, self.cache)
        folder = os.path.dirname(result["book"])
        stale = os.path.join(folder, "img", "9999.png")
        with open(stale, "wb") as handle:
            handle.write(b"left by an older conversion")
        status = os.stat(path)
        os.utime(path, (status.st_mtime + 5, status.st_mtime + 5))
        seen = {}
        real_convert = epub.convert

        def dying(source, out_dir):
            real_convert(source, out_dir)
            seen["book"] = os.path.exists(os.path.join(out_dir, "book.json"))
            seen["stale"] = os.path.exists(stale)
            seen["pictures"] = os.listdir(os.path.join(out_dir, "img"))
            raise KeyboardInterrupt

        with mock.patch.object(epub, "convert", dying):
            with self.assertRaises(KeyboardInterrupt):
                library.open_book(path, self.cache)
        self.assertEqual(seen, {"book": False, "stale": False, "pictures": ["0001.png"]})
        self.assertFalse(os.path.exists(result["book"]))
        again = library.open_book(path, self.cache)
        self.assertFalse(again["cached"])
        self.assertEqual(self.leftovers(self.cache), [])

    def test_failed_write_leaves_no_half_book(self):
        path = self.book()
        with mock.patch.object(library.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                library.open_book(path, self.cache)
        folder = os.path.join(self.cache, "books", library.book_key(path, epub))
        self.assertEqual([name for name in os.listdir(folder) if "book" in name], [])

    def test_errors(self):
        with self.assertRaises(ReaderError) as caught:
            library.open_book(os.path.join(self.books, "gone.epub"), self.cache)
        self.assertEqual(caught.exception.code, "missing")
        with self.assertRaises(ReaderError) as caught:
            library.open_book(self.books, self.cache)
        self.assertEqual(caught.exception.code, "missing")
        with self.assertRaises(ReaderError) as caught:
            library.open_book(self.write("notes.docx", b"words"), self.cache)
        self.assertEqual(caught.exception.code, "unsupported")
        with self.assertRaises(ReaderError) as caught:
            library.open_book(self.write("bad.epub", b"PK\x03\x04 but nothing else"), self.cache)
        self.assertEqual(caught.exception.code, "corrupt")
        self.assertFalse(os.path.exists(os.path.join(self.cache, "index.json")))

    def test_module_returning_plain_dicts(self):
        path = self.write("story.txt", b"Once upon a time.")
        module = mock.Mock(spec=["convert", "read_meta"])
        module.convert.return_value = {"title": "", "authors": ["Grimm, Jacob"], "language": "de",
                                       "sections": [0], "toc": [], "blocks": [{"k": "p", "t": "Once upon a time."}]}
        with mock.patch.object(library, "_load", return_value=module):
            result = library.open_book(path, self.cache)
        book = self.load(result)
        self.assertEqual((book["title"], book["author"], book["format"]), ("story", "Jacob Grimm", "text"))
        self.assertRegex(result["key"], r"^[0-9a-f]{20}$")

    def test_pruning_drops_the_least_recently_opened(self):
        paths = [self.book("b%d.epub" % number, docs=(("c.xhtml", "<p>%d</p><img src='p.png' alt=''/>" % number),),
                           files={"cover.jpg": jpeg(300, 450), "p.png": png(60 + number, 600)})
                 for number in range(4)]
        self.scan()
        opened = []
        with mock.patch.object(library, "CACHE_LIMIT", 1 << 40):
            for number, path in enumerate(paths[:3]):
                result = library.open_book(path, self.cache)
                os.utime(result["book"], (1000 + number, 1000 + number))
                opened.append(result)
            library.open_book(paths[0], self.cache)  # the oldest is read again: now the newest

        def size(result):
            folder = os.path.dirname(result["book"])
            return os.path.getsize(result["book"]) + sum(
                os.path.getsize(os.path.join(folder, "img", name)) for name in os.listdir(os.path.join(folder, "img")))

        abandoned = os.path.join(self.cache, "books", "f" * 20, "img")
        os.makedirs(abandoned)
        with open(os.path.join(abandoned, "0001.png"), "wb") as handle:
            handle.write(b"x" * 100)
        with mock.patch.object(library, "CACHE_LIMIT", size(opened[0]) * 2 + 200):
            last = library.open_book(paths[3], self.cache)

        def converted(result):
            folder = os.path.dirname(result["book"])
            return os.path.exists(result["book"]), os.path.exists(os.path.join(folder, "img"))

        self.assertEqual([converted(result) for result in opened], [(True, True), (False, False), (False, False)])
        self.assertEqual(converted(last), (True, True))
        self.assertFalse(os.path.exists(abandoned))
        for result in opened + [last]:
            folder = os.path.dirname(result["book"])
            self.assertTrue(os.path.isfile(os.path.join(folder, "meta.json")))
            self.assertTrue(os.path.isfile(os.path.join(folder, "cover.jpg")))

    def test_the_book_just_opened_is_never_pruned(self):
        path = self.book()
        with mock.patch.object(library, "CACHE_LIMIT", 1):
            result = library.open_book(path, self.cache)
        self.assertTrue(os.path.isfile(result["book"]))


class InfoTest(LibraryCase):
    def test_info(self):
        path = self.book(title="Informed", creators=("Adams, Scott",),
                         docs=(("ch1.xhtml", '<h1>One</h1><img src="pic.png" alt=""/>'), ("ch2.xhtml", "<h1>Two</h1><p>beta</p>")),
                         files={"cover.jpg": jpeg(300, 450), "pic.png": png(200, 120)})
        result = library.info(path)
        entry = library.scan([self.books], self.cache)["books"][0]
        self.assertEqual({name: result[name] for name in entry if name != "cover"},
                         {name: entry[name] for name in entry if name != "cover"})
        self.assertEqual(result["cover"], "")
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["toc"], [{"t": "One", "d": 0, "b": 0}, {"t": "Two", "d": 0, "b": 2}])
        self.assertEqual(result["blocks"], 4)
        self.assertEqual(sorted(result), sorted(list(entry) + ["ok", "toc", "blocks"]))
        self.assertEqual([name for name in os.listdir(tempfile.gettempdir()) if name.startswith("reader-info-")], [])

    def test_info_writes_nothing(self):
        path = self.book()
        library.info(path)
        self.assertEqual(sorted(os.listdir(self.root)), ["Books"])
        self.assertEqual(os.listdir(self.books), ["book.epub"])

    def test_info_errors(self):
        with self.assertRaises(ReaderError) as caught:
            library.info(os.path.join(self.books, "gone.epub"))
        self.assertEqual(caught.exception.code, "missing")
        noise = bytes(range(256)) * 8
        locked = self.book("locked.epub", docs=(("ch1.xhtml", noise),), ncx_points=False,
                           root_files={"META-INF/encryption.xml": epubkit.encryption((epubkit.AES, "OEBPS/ch1.xhtml"))})
        with self.assertRaises(ReaderError) as caught:
            library.info(locked)
        self.assertEqual(caught.exception.code, "drm")

    def test_info_for_an_external_format(self):
        result = library.info(self.write("manual.pdf", b"%PDF-1.4"))
        self.assertEqual((result["external"], result["format"], result["toc"], result["blocks"]), (True, "pdf", [], 0))


if __name__ == "__main__":
    unittest.main()
