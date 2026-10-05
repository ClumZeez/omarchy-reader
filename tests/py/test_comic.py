import support

import base64
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from epubkit import jpeg, png
from reader import comic, images, pictures
from reader.errors import ReaderError

INFO = """<?xml version="1.0" encoding="utf-8"?>
<ComicInfo><Title>The First Issue</Title><Series>Tall Tales</Series><Number>3</Number>
<Writer>Ann Author, Bob Builder</Writer><Penciller>Cy Artist</Penciller><LanguageISO>EN</LanguageISO>
<Pages>%s</Pages></ComicInfo>"""


def encrypt(path):
    """Mark every member of a zip as encrypted, in both of its directories."""
    with open(path, "rb") as handle:
        data = bytearray(handle.read())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = data.find(signature)
        while position >= 0:
            data[position + offset] |= 1
            position = data.find(signature, position + 4)
    with open(path, "wb") as handle:
        handle.write(bytes(data))


class ComicCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = scratch.name
        self.out = os.path.join(self.dir, "out")

    def make(self, members, name="My Comic.cbz"):
        path = os.path.join(self.dir, name)
        with zipfile.ZipFile(path, "w") as archive:
            for member, data in members:
                archive.writestr(member, data)
        return path

    def pages(self, book):
        """The width of each page, which the fixtures use to tell pages apart."""
        return [block["w"] for block in book.blocks]


class PagesTest(ComicCase):
    def test_pages_are_in_natural_order_whatever_the_archive_order(self):
        names = ["10.png", "2.png", "1.png", "9.PNG", "100.png"]
        book = comic.convert(self.make([(name, png(int(name.split(".")[0]), 5)) for name in names]),
                             self.out)
        self.assertEqual(self.pages(book), [1, 2, 9, 10, 100])
        self.assertEqual(book.sections, [0])

    def test_a_folders_own_pages_come_before_its_subfolders(self):
        book = comic.convert(self.make([
            ("Book/extras/a.png", png(4, 5)), ("Book/z.png", png(3, 5)), ("Book/b.png", png(2, 5)),
            ("Another/1.png", png(1, 5)),
        ]), self.out)
        self.assertEqual(self.pages(book), [1, 2, 3, 4])

    def test_only_real_pictures_are_pages(self):
        book = comic.convert(self.make([
            ("page1.png", png(1, 5)), ("__MACOSX/._page1.png", png(50, 5)), (".hidden.png", png(51, 5)),
            ("sub/.DS_Store", b"junk"), ("Thumbs.db", b"junk"), ("notes.txt", b"words"),
            ("empty.png", b""), ("broken.jpg", b"not a picture at all"), ("page2.jpg", jpeg(2, 5)),
            ("ComicInfo.xml", INFO % ""),
        ]), self.out)
        self.assertEqual(self.pages(book), [1, 2])

    def test_pages_are_copied_byte_for_byte_with_their_sizes(self):
        first, second = png(30, 40), jpeg(60, 80)
        book = comic.convert(self.make([("a.png", first), ("b.jpeg", second)]), self.out)
        self.assertEqual([(block["k"], block["w"], block["h"], block["alt"]) for block in book.blocks],
                         [("img", 30, 40, ""), ("img", 60, 80, "")])
        self.assertEqual([os.path.relpath(block["src"], self.out) for block in book.blocks],
                         ["img/0001.png", "img/0002.jpg"])
        for block, data in zip(book.blocks, (first, second)):
            with open(block["src"], "rb") as handle:
                self.assertEqual(handle.read(), data)
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "img"))), ["0001.png", "0002.jpg"])

    def test_a_picture_with_the_wrong_extension_is_named_for_what_it_is(self):
        book = comic.convert(self.make([("a.jpg", png(3, 4))]), self.out)
        self.assertTrue(book.blocks[0]["src"].endswith("0001.png"))


class MetaTest(ComicCase):
    def test_title_and_writers_come_from_comicinfo(self):
        path = self.make([("1.png", png(8, 9)), ("ComicInfo.xml", INFO % "")])
        meta = comic.read_meta(path)
        self.assertEqual((meta.title, meta.authors, meta.language),
                         ("Tall Tales #3", ["Ann Author", "Bob Builder"], "en"))
        book = comic.convert(path, self.out)
        self.assertEqual((book.title, book.author, book.language),
                         ("Tall Tales #3", "Ann Author, Bob Builder", "en"))

    def test_title_alone_is_used_without_a_series_number(self):
        info = "<ComicInfo><Title>One Shot</Title><Series>Loose</Series></ComicInfo>"
        meta = comic.read_meta(self.make([("1.png", png(8, 9)), ("comicinfo.XML", info)]))
        self.assertEqual(meta.title, "One Shot")

    def test_file_name_is_the_title_without_comicinfo_or_with_a_broken_one(self):
        for info in (None, b"\x00\x01 nothing here"):
            members = [("1.png", png(8, 9))] + ([("ComicInfo.xml", info)] if info else [])
            meta = comic.read_meta(self.make(members, "Space_Opera 02.cbz"))
            self.assertEqual((meta.title, meta.authors), ("Space Opera 02", []))

    def test_cover_is_the_first_page_that_is_a_picture(self):
        first = png(20, 30)
        meta = comic.read_meta(self.make([("2.png", png(21, 30)), ("0.png", b"broken"), ("1.png", first)]))
        self.assertEqual(meta.cover, first)
        self.assertEqual(meta.error, "")


class ContentsTest(ComicCase):
    def toc(self, book):
        return [(entry["t"], entry["d"], entry["b"]) for entry in book.toc]

    def test_a_short_comic_has_no_contents(self):
        book = comic.convert(self.make([("%d.png" % n, png(n, 5)) for n in range(1, 21)]), self.out)
        self.assertEqual(book.toc, [])
        self.assertEqual(len(book.blocks), 20)

    def test_a_long_comic_gets_an_entry_every_ten_pages(self):
        book = comic.convert(self.make([("%d.png" % n, png(n, 5)) for n in range(1, 26)]), self.out)
        self.assertEqual(self.toc(book), [("Page 1", 0, 0), ("Page 11", 0, 10), ("Page 21", 0, 20)])

    def test_several_folders_give_one_entry_each(self):
        book = comic.convert(self.make([
            ("Vol 2/1.png", png(3, 5)), ("Vol 1/1.png", png(1, 5)), ("Vol 1/2.png", png(2, 5)),
            ("Vol 10/1.png", png(4, 5)),
        ]), self.out)
        self.assertEqual(self.toc(book), [("Vol 1", 0, 0), ("Vol 2", 0, 2), ("Vol 10", 0, 3)])
        self.assertEqual(book.sections, [0, 2, 3])

    def test_loose_pages_beside_folders_are_listed_under_the_title(self):
        book = comic.convert(self.make([("cover.png", png(1, 5)), ("Part/1.png", png(2, 5))],
                                       "Saga.cbz"), self.out)
        self.assertEqual(self.toc(book), [("Saga", 0, 0), ("Part", 0, 1)])

    def test_the_comics_own_bookmarks_win(self):
        marks = '<Page Image="0" Bookmark="Opening"/><Page Image="1"/><Page Image="2" Bookmark="Finale"/>'
        book = comic.convert(self.make([("a/1.png", png(1, 5)), ("a/2.png", png(2, 5)),
                                        ("b/1.png", png(3, 5)), ("ComicInfo.xml", INFO % marks)]), self.out)
        self.assertEqual(self.toc(book), [("Opening", 0, 0), ("Finale", 0, 2)])

    def test_bookmarks_pointing_nowhere_are_ignored(self):
        marks = '<Page Image="7" Bookmark="Lost"/><Page Image="x" Bookmark="Odd"/>'
        book = comic.convert(self.make([("1.png", png(1, 5)), ("ComicInfo.xml", INFO % marks)]), self.out)
        self.assertEqual(book.toc, [])


class SafetyTest(ComicCase):
    def refusal(self, path, call=None):
        with self.assertRaises(ReaderError) as caught:
            (call or (lambda: comic.convert(path, self.out)))()
        return caught.exception.code, caught.exception.message

    def test_page_count_is_capped(self):
        path = self.make([("%d.png" % n, png(n, 5)) for n in range(1, 9)])
        with mock.patch.object(comic, "MAX_PAGES", 3):
            self.assertEqual(self.pages(comic.convert(path, self.out)), [1, 2, 3])

    def test_an_oversized_page_is_left_out(self):
        big = png(300, 300)
        path = self.make([("1.png", png(1, 5)), ("2.png", big), ("3.png", png(3, 5))])
        with mock.patch.object(comic, "MAX_PICTURE", len(big) - 1):
            self.assertEqual(self.pages(comic.convert(path, self.out)), [1, 3])

    def test_the_total_written_is_capped(self):
        page = png(40, 40)
        path = self.make([("%d.png" % n, page) for n in range(1, 9)])
        with mock.patch.object(comic, "MAX_TOTAL", 3 * len(page) + 1):
            book = comic.convert(path, self.out)
        self.assertEqual(len(book.blocks), 3)
        self.assertEqual(len(os.listdir(os.path.join(self.out, "img"))), 3)

    def test_an_archive_without_pages_is_refused_in_plain_words(self):
        path = self.make([("readme.txt", b"hello"), ("broken.png", b"nope")])
        self.assertEqual(self.refusal(path), ("corrupt", "This comic has no pages that can be shown."))
        self.assertEqual(self.refusal(path, lambda: comic.read_meta(self.make([("a.txt", b"x")])))[0],
                         "corrupt")

    def test_a_file_that_is_not_an_archive_is_corrupt(self):
        path = os.path.join(self.dir, "noise.cbz")
        with open(path, "wb") as handle:
            handle.write(os.urandom(4096))
        self.assertEqual(self.refusal(path)[0], "corrupt")
        self.assertEqual(self.refusal(path, lambda: comic.read_meta(path))[0], "corrupt")

    def test_a_password_protected_archive_says_so(self):
        path = self.make([("1.png", png(1, 5)), ("2.png", png(2, 5))])
        encrypt(path)
        self.assertEqual(self.refusal(path),
                         ("unsupported", "This comic is password-protected and can't be opened."))

    def test_a_missing_file_is_missing(self):
        self.assertEqual(self.refusal(os.path.join(self.dir, "gone.cbz"))[0], "missing")

    def test_member_names_cannot_leave_the_output_folder(self):
        book = comic.convert(self.make([("../../escape.png", png(1, 5)), ("/abs.png", png(2, 5))]),
                             self.out)
        for block in book.blocks:
            self.assertEqual(os.path.dirname(block["src"]), os.path.join(self.out, "img"))
        self.assertEqual(os.listdir(self.dir).count("escape.png"), 0)


class PicturesTest(ComicCase):
    def test_store_numbers_pictures_and_refuses_what_is_not_one(self):
        kept = pictures.Pictures(self.out)
        first = kept.store(png(7, 9))
        self.assertEqual((os.path.relpath(first["src"], self.out), first["w"], first["h"], first["al"]),
                         ("img/0001.png", 7, 9, 0))
        self.assertIsNone(kept.store(b"plain words"))
        self.assertIsNone(kept.store(b""))
        self.assertTrue(kept.store(jpeg(5, 5))["src"].endswith("0002.jpg"))

    def test_store_stops_at_its_bounds(self):
        data = png(20, 20)
        self.assertIsNone(pictures.Pictures(self.out, each=len(data) - 1).store(data))
        counted = pictures.Pictures(self.out, count=1)
        self.assertIsNotNone(counted.store(data))
        self.assertIsNone(counted.store(data))
        totalled = pictures.Pictures(self.out, total=len(data) + 5)
        self.assertIsNotNone(totalled.store(data))
        self.assertIsNone(totalled.store(data))

    def test_data_uris_are_stored(self):
        data = png(6, 6)
        kept = pictures.Pictures(self.out)
        stored = kept.store_data_uri("data:image/png;base64," + base64.b64encode(data).decode())
        with open(stored["src"], "rb") as handle:
            self.assertEqual(handle.read(), data)
        self.assertIsNone(kept.store_data_uri("data:text/plain,hello"))

    def test_base64_is_decoded_however_it_is_wrapped(self):
        data = png(9, 9)
        text = base64.b64encode(data).decode()
        wrapped = "\n".join(text[start:start + 20] for start in range(0, len(text), 20))
        junk = wrapped.replace("\n", "\n!!not-base64!!\n", 1)
        for spelling in (text, wrapped, "  " + wrapped + "\r\n", text.rstrip("="), junk,
                         text.replace("+", "-").replace("/", "_"), text[:8] + "*" + text[8:]):
            with self.subTest(spelling=spelling[:30]):
                self.assertEqual(pictures.decode_base64(spelling), data)
        self.assertEqual(pictures.decode_base64(""), b"")
        self.assertEqual(pictures.decode_base64("A"), b"")


@unittest.skipUnless(os.path.isdir(support.sample("cbz")), "the comic samples are not on this machine")
class SamplesTest(ComicCase):
    def test_every_sample_converts(self):
        names = sorted(os.listdir(support.sample("cbz")))
        self.assertTrue(names)
        for name in names:
            with self.subTest(sample=name), tempfile.TemporaryDirectory() as out:
                book = comic.convert(support.sample("cbz", name), out)
                meta = comic.read_meta(support.sample("cbz", name))
                self.assertEqual((meta.title, meta.authors), (book.title, book.authors))
                self.assertEqual(images.sniff(meta.cover).width, book.blocks[0]["w"])
                self.assertTrue(all(block["k"] == "img" and os.path.isfile(block["src"])
                                    for block in book.blocks))

    def test_the_synthetic_comic(self):
        book = comic.convert(support.sample("cbz", "synthetic.cbz"), self.out)
        self.assertTrue(book.title.endswith("Fixtures #7"), book.title)
        self.assertEqual((book.author, book.language), ("A. Writer, B. Cowriter", "en"))
        self.assertEqual([(block["w"], block["h"]) for block in book.blocks], [(64, 96)] * 6)
        self.assertEqual([(entry["t"], entry["b"]) for entry in book.toc], [("My Comic", 0), ("extras", 5)])

    def test_the_flat_numeric_comic(self):
        book = comic.convert(support.sample("cbz", "synthetic-flat-numeric.cbz"), self.out)
        self.assertEqual((book.title, book.author, len(book.blocks), book.toc),
                         ("synthetic-flat-numeric", "", 7, []))
        with zipfile.ZipFile(support.sample("cbz", "synthetic-flat-numeric.cbz")) as archive:
            expected = [archive.read(name + ".png") for name in ("1", "2", "3", "11", "20", "100", "cover")]
        found = []
        for block in book.blocks:
            with open(block["src"], "rb") as handle:
                found.append(handle.read())
        self.assertEqual(found, expected)


if __name__ == "__main__":
    unittest.main()
