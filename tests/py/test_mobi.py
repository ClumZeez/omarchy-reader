import support

import os
import random
import re
import struct
import tempfile
import time
import unittest
from unittest import mock

import mobikit
from epubkit import jpeg, png
from reader import images, mobi
from reader.errors import ReaderError

DRM_SENTENCE = "This book is protected by DRM and can't be opened."
SAMPLES = support.sample("mobi")
HAVE_SAMPLES = os.path.isdir(SAMPLES)
PROTECTED = ("libmobi-sample-drm-v1.mobi", "libmobi-sample-drm-v2.mobi")
PAIRS = (("pg11-images.mobi", "pg11-images-kf8.mobi"),
         ("pg1342-images.mobi", "pg1342-images-kf8.mobi"),
         ("pg17489-fr-images.mobi", "pg17489-fr-images-kf8.mobi"))
# file: (title, author, language, sections, contents entries, deepest level, has a cover)
EXPECTED = {
    "libmobi-sample-cp1252.mobi": ("Libmobi test sample", "Bartek Fabiszewski", "en", 2, 2, 0, True),
    "libmobi-sample-dict-infl2.mobi": ("Libmobi sample file", "Bartek Fabiszewski", "pl", 2, 3, 1, False),
    "libmobi-sample-multimedia.mobi": ("Libmobi test sample", "Bartek Fabiszewski", "en-us", 2, 2, 0, True),
    "libmobi-sample-ncx.mobi": ("libmobi ncx test", "", "en", 3, 4, 1, False),
    "libmobi-sample-obfuscated-fonts.mobi": ("font", "", "en", 1, 1, 0, False),
    "libmobi-sample-textread.mobi": ("Libmobi test sample", "Bartek Fabiszewski", "en-us", 1, 3, 1, False),
    "libmobi-sample-unicode-huffdic.mobi": ("Libmobi", "Bartek Fabiszewski", "en-us", 2, 2, 0, True),
    "libmobi-sample-unicode-uncompressed.mobi": ("Libmobi test sample", "Bartek Fabiszewski", "en-us", 2, 2, 0, True),
    "pg11-images-kf8.mobi": ("Alice's Adventures in Wonderland", "Lewis Carroll", "en", 19, 16, 0, True),
    "pg11-images.mobi": ("Alice's Adventures in Wonderland", "Lewis Carroll", "en", 19, 16, 0, True),
    "pg11-noimages.mobi": ("Alice's Adventures in Wonderland", "Lewis Carroll", "en", 18, 17, 0, True),
    "pg1342-images-kf8.mobi": ("Pride and Prejudice", "Jane Austen", "en", 76, 63, 0, True),
    "pg1342-images.mobi": ("Pride and Prejudice", "Jane Austen", "en", 84, 63, 0, True),
    "pg17489-fr-images-kf8.mobi": ("Les misérables Tome I: Fantine", "Victor Hugo", "fr", 84, 153, 1, True),
    "pg17489-fr-images.mobi": ("Les misérables Tome I: Fantine", "Victor Hugo", "fr", 85, 153, 1, True),
    "se-alice.azw3": ("Alice’s Adventures in Wonderland", "Lewis Carroll", "en", 20, 20, 1, True),
}

FILLER = b"".join(b"<p>Paragraph %d of the long chapter, with an accent: caf\xc3\xa9.</p>" % number
                  for number in range(200))


def plain(block):
    text = block.get("t", "")
    if block.get("f"):
        text = re.sub(r"<[^>]*>", "", text.replace("<br>", " "))
        text = text.replace("&#160;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


def base32(number, width):
    digits = ""
    while number or not digits:
        digits = "0123456789ABCDEFGHIJKLMNOPQRSTUV"[number % 32] + digits
        number //= 32
    return digits.rjust(width, "0").encode()


def placed(template, **marks):
    """`template` with each `%(name)010d` filled with the offset `marks[name]` is found at."""
    blank = template % {name.encode(): 0 for name in marks}
    return template % {name.encode(): blank.index(mark) for name, mark in marks.items()}


def six_text(extra=b""):
    return placed(
        b'<html><head><guide><reference type="toc" title="Contents" filepos=%(toc)010d />'
        b'</guide></head><body>'
        b'<p>Opening <i>words</i> and a <a filepos=%(two)010d>link to two</a>, '
        b'a <a filepos=9999999999>dead one</a>.</p>'
        b'<mbp:pagebreak/>'
        b'<h2 id="toc">Contents</h2>'
        b'<p><a filepos=%(one)010d>Chapter One</a></p>'
        b'<blockquote><a filepos=%(inner)010d>Inside One</a></blockquote>'
        b'<p><a filepos=%(two)010d>Chapter Two</a></p>'
        b'<mbp:pagebreak/>'
        b'<h1 id="one">One</h1><p>alpha</p>'
        b'<p><img src="junk" recindex="00001" alt="A white rabbit"/></p>'
        b'<h2 id="inner">Inner</h2><p>inside</p>' + extra +
        b'<mbp:pagebreak></mbp:pagebreak>'
        b'<h1 id="two">Two</h1><p>beta</p></body></html>',
        toc=b'<h2 id="toc"', one=b'<h1 id="one"', inner=b'<h2 id="inner"', two=b'<h1 id="two"')


SIX_EXTH = ((503, "The Title &amp; More"), (100, "Carroll, Lewis"), (100, "Second Author; Third"),
            (524, "en-GB"))


def eight_parts():
    """Two documents in three fragments; returns them with the offset of the inner heading."""
    second = (b'<h1 aid="5">Two</h1><p aid="6">beta</p>'
              b'<h2 aid="7">Two point one</h2><p aid="8">gamma</p>')
    inner = second.index(b"<h2")
    first = (b'<h1 aid="1">One</h1><p class="it" aid="2">alpha and a '
             b'<a href="kindle:pos:fid:0002:off:' + base32(inner, 10) + b'">link</a>, '
             b'a <a href="kindle:pos:fid:00VV:off:0000000000">dead one</a>.</p>')
    more = (b'<h2 aid="3">Inside one</h2>'
            b'<p aid="4"><img src="kindle:embed:0001?mime=image/jpeg" alt="A white rabbit"/></p>'
            b'<div><img src="kindle:flow:0002?mime=image/svg+xml"/></div>')
    head = (b'<?xml version="1.0"?><html><head><title>Part</title><link rel="stylesheet" '
            b'type="text/css" href="kindle:flow:0001?mime=text/css"/></head><body aid="0">')
    documents = [(head, [first, more], b"</body></html>"),
                 (b'<html><head><meta charset="cp-1252"/></head><body aid="9">', [second],
                  b"</body></html>")]
    return documents, inner


EIGHT_FLOWS = (b".it { font-style: italic }",
               b'<svg xmlns:xlink="http://www.w3.org/1999/xlink"><image width="40" height="50" '
               b'xlink:href="kindle:embed:0002?mime=image/png"/></svg>')


def eight(**options):
    documents, inner = eight_parts()
    options.setdefault("ncx", [("One", 0, 0, 0, None), ("Two", 2, 0, 0, None),
                               ("Inside one", 1, 0, 1, 0), ("Two point one", 2, inner, 1, 1)])
    options.setdefault("images", [jpeg(300, 400), png(40, 50), jpeg(600, 900)])
    options.setdefault("exth", ((503, "Eight"), (100, "An Author"), (201, 2)))
    return mobikit.kf8(documents, flows=EIGHT_FLOWS, **options)


class MobiCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = scratch.name
        self.out = os.path.join(self.dir, "out")

    def write(self, data, name="book.mobi"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def convert(self, records, **options):
        return mobi.convert(self.write(mobikit.pdb(records, **options)), self.out)

    def meta(self, records, **options):
        return mobi.read_meta(self.write(mobikit.pdb(records, **options)))

    def refusal(self, data, name="book.mobi"):
        with self.assertRaises(ReaderError) as caught:
            mobi.convert(self.write(data, name), self.out)
        return caught.exception.code, caught.exception.message

    def texts(self, book):
        return [plain(block) for block in book.blocks if "t" in block]

    def toc(self, book):
        return [(entry["t"], entry["d"], plain(book.blocks[entry["b"]])) for entry in book.toc]

    def links(self, book):
        """`(link text, text of the block it leads to)` for every internal link."""
        found = []
        for block in book.blocks:
            if block.get("f"):
                for target, label in re.findall(r'<a href="b:(\d+)">(.*?)</a>', block["t"]):
                    found.append((re.sub(r"<[^>]*>", "", label), plain(book.blocks[int(target)])))
        return found

    def check(self, book, whole=True):
        """What must hold for any converted book; `whole` when its file was not damaged."""
        total = len(book.blocks)
        self.assertGreater(total, 0)
        self.assertEqual(book.sections[0], 0)
        self.assertEqual(book.sections, sorted(set(book.sections)))
        self.assertLess(book.sections[-1], total)
        depth = -1
        for entry in book.toc:
            self.assertTrue(entry["t"])
            self.assertTrue(0 <= entry["b"] < total)
            self.assertLessEqual(entry["d"], depth + 1)
            depth = entry["d"]
        for block in book.blocks:
            if block["k"] == "img":
                self.assertTrue(os.path.isfile(block["src"]), block["src"])
                self.assertEqual(os.path.dirname(block["src"]), os.path.join(self.out, "img"))
            cells = [cell for row in block.get("rows", ()) for cell in row]
            for text in cells + ([block["t"]] if block.get("f") else []):
                if whole:
                    self.assertNotIn("kindle:", text)
                    self.assertNotIn("filepos", text)
                for target in re.findall(r'href="b:(\d+)"', text):
                    self.assertLess(int(target), total)


class Mobi6Test(MobiCase):
    def book(self, **options):
        options.setdefault("exth", SIX_EXTH)
        options.setdefault("images", [jpeg(300, 400)])
        return self.convert(mobikit.mobi6(options.pop("text", six_text()), **options))

    def test_text_emphasis_and_sections(self):
        book = self.book()
        self.check(book)
        self.assertEqual(self.texts(book), [
            "Opening words and a link to two, a dead one.", "Contents", "Chapter One",
            "Inside One", "Chapter Two", "One", "alpha", "Inner", "inside", "Two", "beta"])
        self.assertIn("<i>words</i>", book.blocks[0]["t"])
        self.assertEqual([plain(book.blocks[start]) or book.blocks[start]["k"]
                          for start in book.sections],
                         ["Opening words and a link to two, a dead one.", "Contents", "One", "Two"])
        self.assertEqual(book.blocks[5]["s"], 2)

    def test_filepos_links_lead_to_their_blocks(self):
        book = self.book()
        self.assertEqual(self.links(book), [("link to two", "Two"), ("Chapter One", "One"),
                                            ("Inside One", "Inner"), ("Chapter Two", "Two")])
        self.assertNotIn("dead one</a>", book.blocks[0]["t"])

    def test_recindex_pictures_are_extracted(self):
        book = self.book()
        pictures = [block for block in book.blocks if block["k"] == "img"]
        self.assertEqual([(block["w"], block["h"], block["alt"]) for block in pictures],
                         [(300, 400, "A white rabbit")])
        self.assertEqual(os.path.basename(pictures[0]["src"]), "0001.jpg")
        with open(pictures[0]["src"], "rb") as handle:
            self.assertEqual(handle.read(), jpeg(300, 400))

    def test_picture_that_is_not_one_leaves_its_description(self):
        book = self.book(images=[b"RESC\x00\x00\x00\x10not a picture"])
        self.check(book)
        self.assertFalse([block for block in book.blocks if block["k"] == "img"])
        self.assertIn("A white rabbit", self.texts(book))

    def test_high_resolution_record_is_preferred_when_it_is_a_picture(self):
        text = (b'<html><body><p>a</p><img recindex="1" hirecindex="2"/>'
                b'<img recindex="1" hirecindex="3"/></body></html>')
        book = self.book(text=text, images=[jpeg(100, 120), jpeg(800, 900), b"FONT\x00junk"])
        sizes = [(block["w"], block["h"]) for block in book.blocks if block["k"] == "img"]
        self.assertEqual(sizes, [(800, 900), (100, 120)])

    def test_metadata(self):
        records = mobikit.mobi6(six_text(), exth=SIX_EXTH, images=[jpeg(300, 400)])
        meta = self.meta(records)
        self.assertEqual((meta.title, meta.language, meta.error), ("The Title & More", "en-gb", ""))
        self.assertEqual(meta.authors, ["Carroll, Lewis", "Second Author", "Third"])
        book = self.convert(records)
        self.assertEqual((book.title, book.author), ("The Title & More",
                                                     "Lewis Carroll, Second Author, Third"))

    def test_title_falls_back_to_full_name_then_file_name(self):
        text = b"<html><body><p>text</p></body></html>"
        records = mobikit.mobi6(text, title="The Full Name", exth=((100, "Someone"),))
        self.assertEqual(self.meta(records).title, "The Full Name")
        bare = mobikit.mobi6(text, title="")
        path = self.write(mobikit.pdb(bare, name=b"A_Database_Name_Cut_Off_At_Thirty_One"),
                          "My_Great Book.azw")
        self.assertEqual(mobi.read_meta(path).title, "My Great Book")
        self.assertEqual(mobi.convert(path, self.out).title, "My Great Book")
        self.assertEqual(self.meta(bare, name=b"Short_Name").title, "Short Name")

    def test_language_from_locale_and_unknown_author_dropped(self):
        records = mobikit.mobi6(b"<p>texte</p>", locale=0x040C, exth=((100, "Unknown"),))
        meta = self.meta(records)
        self.assertEqual((meta.language, meta.authors), ("fr", []))

    def test_cover_and_thumbnail_fallback(self):
        pictures = [jpeg(300, 400), jpeg(600, 900), jpeg(120, 180)]
        text = b"<p>text</p>"
        cover = self.meta(mobikit.mobi6(text, images=pictures, exth=((201, 1), (202, 2)))).cover
        self.assertEqual(cover, pictures[1])
        thumbnail = self.meta(mobikit.mobi6(text, images=pictures, exth=((202, 2),))).cover
        self.assertEqual(thumbnail, pictures[2])
        wrong = [b"RESC\x00not a picture", jpeg(600, 900), jpeg(120, 180)]
        fallback = self.meta(mobikit.mobi6(text, images=wrong, exth=((201, 0), (202, 2)))).cover
        self.assertEqual(fallback, wrong[2])
        embedded = self.meta(mobikit.mobi6(text, images=pictures,
                                           exth=((129, "kindle:embed:0002"),))).cover
        self.assertEqual(embedded, pictures[1])
        self.assertIsNone(self.meta(mobikit.mobi6(text, images=pictures)).cover)
        self.assertIsNone(self.meta(mobikit.mobi6(text, exth=((201, 40),))).cover)

    def test_resources_are_found_when_the_header_does_not_say_where(self):
        book = self.book(fields={0x6C: mobikit.NULL}, ncx=[("One", 0, 0)])
        self.assertEqual([block["w"] for block in book.blocks if block["k"] == "img"], [300])

    def test_ncx_contents_in_reading_order(self):
        text = six_text()
        one, inner, two = (text.index(mark) for mark in (b"<h1 id=\"one\"", b"<h2 id=\"inner\"",
                                                           b"<h1 id=\"two\""))
        book = self.book(ncx=[("Chapter 2", two, 0), ("Chapter 1", one, 0), ("Within", inner, 1),
                              ("Nowhere", 10 ** 8, 0)])
        self.assertEqual(self.toc(book), [("Chapter 1", 0, "One"), ("Within", 1, "Inner"),
                                          ("Chapter 2", 0, "Two")])

    def test_flat_ncx_takes_its_depth_from_the_contents_page(self):
        text = six_text()
        places = [text.index(mark) for mark in (b"<h1 id=\"one\"", b"<h2 id=\"inner\"",
                                                b"<h1 id=\"two\"")]
        book = self.book(ncx=[(label, place, 0)
                              for label, place in zip(("First", "Within", "Second"), places)])
        self.assertEqual(self.toc(book), [("First", 0, "One"), ("Within", 1, "Inner"),
                                          ("Second", 0, "Two")])

    def test_contents_from_the_guide_page(self):
        book = self.book()
        self.assertEqual(self.toc(book), [("Chapter One", 0, "One"), ("Inside One", 1, "Inner"),
                                          ("Chapter Two", 0, "Two")])

    def test_contents_synthesised_from_headings(self):
        text = (b"<html><body><h1>First</h1><p>a</p><mbp:pagebreak/><h1>Second</h1><p>b</p>"
                b"<mbp:pagebreak/><h1>Third</h1><p>c</p></body></html>")
        book = self.book(text=text)
        self.assertEqual([entry["t"] for entry in book.toc], ["First", "Second", "Third"])
        self.assertEqual(book.sections, [0, 2, 4])

    def test_empty_sections_add_nothing(self):
        text = (b"<html><body><mbp:pagebreak/><p>a</p><mbp:pagebreak/><mbp:pagebreak/> "
                b"<mbp:pagebreak/><p>b</p><mbp:pagebreak/></body></html>")
        book = self.book(text=text)
        self.check(book)
        self.assertEqual(book.sections, [0, 1])

    def test_head_metadata_is_not_text_but_styles_apply(self):
        text = (b"<html><head><metadata><dc:Title>Hidden</dc:Title></metadata>"
                b"<style>.k { font-weight: bold }</style></head>"
                b'<body><p class="k">shown</p></body></html>')
        book = self.book(text=text)
        self.assertEqual(self.texts(book), ["shown"])
        self.assertEqual(book.blocks[0]["t"], "<b>shown</b>")

    def test_positions_are_offsets_into_the_undecoded_text(self):
        accents = "é".encode() * 500
        text = placed(b"<html><body><p>" + accents + b" <a filepos=%(mark)010d>go</a></p>"
                      b"<mbp:pagebreak/><p>caf\xc3\xa9s</p><p id=m>target</p></body></html>",
                      mark=b"<p id=m>")
        book = self.book(text=text)
        self.assertEqual(self.links(book), [("go", "target")])

    def test_position_inside_a_tag_a_reference_or_a_character(self):
        text = (b"<html><body><p>one</p><p class='x'>two &amp; three</p><p>caf\xc3\xa9</p>"
                b"</body></html>")
        inside_tag = text.index(b"class='x'")
        inside_reference = text.index(b"&amp;") + 2
        inside_character = text.index(b"\xa9")
        links = b"".join(b"<a filepos=%d>to %d</a> " % (place, number) for number, place
                         in enumerate((inside_tag, inside_reference, inside_character)))
        book = self.book(text=text.replace(b"</body>", b"<p>" + links + b"</p></body>"))
        self.assertEqual(self.links(book), [("to 0", "two & three"), ("to 1", "two & three"),
                                            ("to 2", "café")])
        self.assertEqual(self.texts(book)[:3], ["one", "two & three", "café"])

    def test_windows_1252(self):
        text = "<html><body><p>caf\u00e9 \u201cquoted\u201d</p></body></html>".encode("cp1252")
        book = self.book(text=text.replace(b"caf", b"\x81caf"), codepage=1252,
                         exth=((503, "Été"),))
        self.assertEqual(self.texts(book), ["café “quoted”"])
        self.assertEqual(book.title, "Été")

    def test_declared_encoding_is_not_believed_blindly(self):
        utf8 = "<html><body><p>café — thé</p></body></html>".encode()
        self.assertEqual(self.texts(self.book(text=utf8, codepage=1252, exth=())), ["café — thé"])
        latin = "<html><body><p>café thé déjà</p></body></html>".encode("cp1252")
        self.assertEqual(self.texts(self.book(text=latin, codepage=65001, exth=())),
                         ["café thé déjà"])

    def test_stored_text(self):
        book = self.book(compression=1)
        self.assertEqual(self.links(book)[0], ("link to two", "Two"))

    def test_huffman_text(self):
        tables = mobikit.huffman_tables({ord("~"): "<b>bold</b>", ord("^"): b"caret"})
        text = b"<html><body><p>plain ~ and ^</p>" + FILLER + b"</body></html>"
        book = self.book(text=text, compression=mobikit.HUFFMAN, tables=tables)
        self.assertEqual(book.blocks[0]["t"], "plain <b>bold</b> and caret")
        self.assertEqual(len(book.blocks), 201)

    def test_damaged_huffman_tables_do_not_hang(self):
        loop = mobikit.huffman_tables({ord("~"): "~~"})
        book = self.book(text=b"<p>a ~ b</p>", tables=loop, compression=mobikit.HUFFMAN)
        self.assertEqual(self.texts(book), ["a b"])
        huff, cdic = mobikit.huffman_tables()
        for tables in ([huff[:40], cdic], [huff, cdic[:12] + b"\xff\xff\xff\xff" + cdic[16:]],
                       [b"JUNK", cdic]):
            code, _ = self.refusal(mobikit.pdb(mobikit.mobi6(
                b"<p>text</p>", tables=tables, compression=mobikit.HUFFMAN)))
            self.assertEqual(code, "corrupt")

    def test_trailing_entries_are_removed(self):
        text = b"<html><body>" + FILLER + b"</body></html>"
        expected = self.texts(self.book(text=text))
        self.assertEqual(len(expected), 200)
        self.assertEqual(self.texts(self.book(text=text, trailers=3)), expected)

    def test_wrong_trailing_flags_are_corrected(self):
        text = b"<html><body>" + FILLER + b"</body></html>"
        expected = self.texts(self.book(text=text))
        for trailers, flags in ((3, 0), (0, 3), (3, 0xFFFF), (2, 3), (1, 0)):
            with self.subTest(trailers=trailers, flags=flags):
                book = self.book(text=text, trailers=trailers, flags=flags)
                self.assertEqual(self.texts(book), expected)

    def test_header_lies_leave_the_text_unchanged(self):
        text = b"<html><body>" + FILLER + b"<img recindex=1></body></html>"
        records = mobikit.mobi6(text, trailers=3, images=[jpeg(300, 400)], exth=SIX_EXTH)
        expected = self.convert(records).blocks
        start = 78 + 8 * len(records) + 2
        data = mobikit.pdb(records)
        for offset, layout, value in ((8, ">H", len(records)), (8, ">H", 0xFEFE), (8, ">H", 1),
                                      (4, ">L", 12345), (4, ">L", 0), (10, ">H", 7)):
            with self.subTest(offset=offset, value=value):
                lied = bytearray(data)
                struct.pack_into(layout, lied, start + offset, value)
                self.assertEqual(mobi.convert(self.write(bytes(lied)), self.out).blocks, expected)
        with self.subTest("record count"):
            self.assertEqual(self.convert(records, count=5000).blocks, expected)

    def test_palmdoc_decompression(self):
        packed = b"abc" + bytes([0x80, 0x1B]) + b"\xe1" + b"\x02\xc3\xa9" + b"\x00z"
        self.assertEqual(mobi._unpack_palmdoc(packed), b"abcabcabc a\xc3\xa9\x00z")
        self.assertEqual(mobi._unpack_palmdoc(b"ab" + bytes([0x80, 0x0D])), b"ab" + b"b" * 8)
        self.assertEqual(mobi._unpack_palmdoc(b"a" + bytes([0xBF, 0xFF]) + b"b\x80"), b"ab")
        self.assertEqual(mobi._unpack_palmdoc(mobikit.palmdoc(FILLER)), FILLER)

    def test_older_half_is_used_when_there_is_no_newer_one(self):
        records = mobikit.mobi6(six_text(), exth=SIX_EXTH) + [b"BOUNDARY", b"CONT" + bytes(60)]
        self.assertEqual(len(self.convert(records).sections), 4)


class PalmDocTest(MobiCase):
    def records(self, text, **options):
        records = mobikit.text_records(text, options.get("compression", 2))
        return [mobikit.header(len(text), len(records), length=0, **options)] + records

    def test_plain_text_becomes_paragraphs(self):
        text = "First paragraph <not a tag> & more.\n\nSecond: café.\n".encode("cp1252")
        book = self.convert(self.records(text), kind=b"TEXtREAd", name=b"Plain_Notes")
        self.check(book)
        self.assertEqual(self.texts(book), ["First paragraph <not a tag> & more.", "Second: café."])
        self.assertEqual(book.title, "Plain Notes")

    def test_markup_and_metadata_inside_the_text(self):
        text = (b"<HTML><HEAD><metadata><dc-metadata><dc:Title>Inline Title</dc:Title>"
                b"<dc:Creator>Inline Author</dc:Creator><dc:Language>pl</dc:Language>"
                b"</dc-metadata></metadata></HEAD><BODY><H1>Head</H1><P>Body text</BODY></HTML>")
        records = self.records(text, compression=1)
        meta = self.meta(records, kind=b"TEXtREAd", name=b"x" * 31)
        self.assertEqual((meta.title, meta.authors, meta.language),
                         ("Inline Title", ["Inline Author"], "pl"))
        book = self.convert(records, kind=b"TEXtREAd")
        self.assertEqual((book.title, book.author), ("Inline Title", "Inline Author"))
        self.assertEqual(self.texts(book), ["Head", "Body text"])

    def test_reading_position_is_not_mistaken_for_encryption(self):
        text = b"Only a line of text.\n"
        self.assertEqual(self.meta(self.records(text, encryption=77), kind=b"TEXtREAd").error, "")

    def test_encrypted_text_is_refused(self):
        noise = random.Random(7).randbytes(3000)
        records = [mobikit.header(3000, 1, length=0, compression=1, encryption=1), noise]
        data = mobikit.pdb(records, kind=b"TEXtREAd", name=b"Locked_Book")
        self.assertEqual(self.refusal(data), ("drm", DRM_SENTENCE))
        meta = mobi.read_meta(self.write(data))
        self.assertEqual((meta.title, meta.error), ("Locked Book", DRM_SENTENCE))


class Kf8Test(MobiCase):
    def test_parts_fragments_and_stylesheet(self):
        book = self.convert(eight())
        self.check(book)
        self.assertEqual(self.texts(book), ["One", "alpha and a link, a dead one.", "Inside one",
                                            "Two", "beta", "Two point one", "gamma"])
        self.assertEqual(book.blocks[1]["t"],
                         '<i>alpha and a <a href="b:7">link</a>, a dead one.</i>')
        self.assertEqual(plain(book.blocks[7]), "Two point one")
        self.assertEqual(book.sections, [0, 5])
        self.assertEqual((book.title, book.author), ("Eight", "An Author"))

    def test_embedded_and_wrapped_pictures(self):
        book = self.convert(eight())
        pictures = [block for block in book.blocks if block["k"] == "img"]
        self.assertEqual([(block["w"], block["h"], block["alt"]) for block in pictures],
                         [(300, 400, "A white rabbit"), (40, 50, "")])
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "img"))),
                         ["0001.jpg", "0002.png"])

    def test_flow_named_by_every_document_is_read_once(self):
        head = (b'<html><head><link rel="stylesheet" href="kindle:flow:0001?mime=text/css"/>'
                b'</head><body>')
        documents = [(head, [b'<p class="it">text %d</p>'
                             b'<div><img src="kindle:flow:0002?n=%d"/></div>' % (number, number)],
                      b"</body></html>") for number in range(20)]
        records = mobikit.kf8(documents, flows=EIGHT_FLOWS, images=[jpeg(300, 400), png(40, 50)])
        with mock.patch.object(mobi, "_decode", wraps=mobi._decode) as decode:
            book = self.convert(records)
        self.assertEqual([call.args[0] for call in decode.call_args_list].count(EIGHT_FLOWS[0]), 1)
        self.assertEqual([block["t"] for block in book.blocks if "t" in block],
                         ["<i>text %d</i>" % number for number in range(20)])
        self.assertEqual([(block["w"], block["h"]) for block in book.blocks if block["k"] == "img"],
                         [(40, 50)] * 20)
        self.assertEqual(os.listdir(os.path.join(self.out, "img")), ["0001.png"])

    def test_cover(self):
        records = eight()
        cover = images.sniff(self.meta(records).cover)
        self.assertEqual((cover.width, cover.height), (600, 900))

    def test_ncx_hierarchy_in_reading_order(self):
        book = self.convert(eight())
        self.assertEqual(self.toc(book), [("One", 0, "One"), ("Inside one", 1, "Inside one"),
                                          ("Two", 0, "Two"),
                                          ("Two point one", 1, "Two point one")])

    def test_flat_ncx_is_sorted_by_position(self):
        _, inner = eight_parts()
        book = self.convert(eight(ncx=[("Two point one", 2, inner, 1, None), ("Two", 2, 0, 0, None),
                                       ("One", 0, 0, 0, None)]))
        self.assertEqual([(entry["t"], entry["d"]) for entry in book.toc],
                         [("One", 0), ("Two", 0), ("Two point one", 1)])

    def test_ncx_with_a_ring_of_parents_and_dead_entries(self):
        book = self.convert(eight(ncx=[("One", 0, 0, 0, 1), ("Two", 2, 0, 0, 0),
                                       ("Inside one", 1, 0, 0, 77), ("Gone", 500, 0, 0, None)]))
        self.check(book)
        self.assertEqual(sorted(entry["t"] for entry in book.toc), ["Inside one", "One", "Two"])

    def test_position_outside_the_text_leads_to_the_top_of_its_document(self):
        book = self.convert(eight(ncx=[("One", 0, 0, 0, None), ("Inside one", 1, 0, 0, None),
                                       ("Two", 2, 9_999_999, 0, None)]))
        self.assertEqual(self.toc(book), [("One", 0, "One"), ("Inside one", 0, "Inside one"),
                                          ("Two", 0, "Two")])

    def test_contents_from_the_guide_page(self):
        documents, inner = eight_parts()
        page = (b'<h1>Contents</h1><ul><li><a href="kindle:pos:fid:0001:off:0000000000">First'
                b'</a><ul><li><a href="kindle:pos:fid:0001:off:' + base32(len(documents[0][1][0]), 10)
                + b'">Its <b>inner</b> part</a></li></ul></li>'
                b'<li><a href="kindle:pos:fid:0003:off:0000000000">Second</a></li></ul>')
        documents.insert(0, (b"<html><head></head><body>", [page], b"</body></html>"))
        book = self.convert(mobikit.kf8(documents, guide=[("text", 1, 0), ("toc", 0, 0)]))
        self.check(book)
        self.assertEqual(self.toc(book), [("First", 0, "One"), ("Its inner part", 1, "Inside one"),
                                          ("Second", 0, "Two")])

    def test_contents_synthesised_without_ncx_or_guide(self):
        documents, _ = eight_parts()
        book = self.convert(mobikit.kf8(documents))
        self.assertEqual([entry["t"] for entry in book.toc],
                         ["One", "Inside one", "Two", "Two point one"])

    def test_single_flow_and_missing_tables(self):
        documents, _ = eight_parts()
        records = mobikit.kf8(documents, fields={0xFC: mobikit.NULL, 0xF8: mobikit.NULL})
        book = self.convert(records)
        self.check(book)
        self.assertIn("gamma", self.texts(book))
        self.assertEqual(len(book.sections), 1)

    def test_fragment_position_inside_a_tag_is_repaired(self):
        skeleton = b'<html><head></head><body class="main" aid="0"></body></html>'
        fragment = b"<p>inserted</p>"
        text = skeleton + fragment
        selector = b"P-//*[@aid='0']"
        records = mobikit.text_records(text)
        indexes = mobikit.index(mobikit.SKELETON_TAGS,
                                [(b"SKEL0", {1: [1], 6: [0, len(skeleton)]})])
        wrong = skeleton.index(b"class") + 2
        indexes += mobikit.index(mobikit.FRAGMENT_TAGS,
                                 [(b"%010d" % wrong, {2: [0], 3: [0], 4: [0], 6: [0, len(fragment)]})],
                                 [selector])
        head = mobikit.header(len(text), len(records), version=8, length=264,
                              fields={0xFC: 1 + len(records), 0xF8: 3 + len(records)})
        book = self.convert([head] + records + indexes)
        self.assertEqual(self.texts(book), ["inserted"])

    def test_one_long_document_costs_no_more_than_its_fragments(self):
        pieces = [b"<h2>Chapter %d</h2>" % number + b"<p>An ordinary paragraph.</p>" * 150
                  for number in range(120)]
        document = (b'<html><head></head><body aid="0">', pieces, b"</body></html>")
        # Charging the whole growing page for every fragment would come to thirty times this.
        with mock.patch.object(mobi, "MAX_ASSEMBLY", 1 << 20):
            book = self.convert(mobikit.kf8([document]))
        self.check(book)
        self.assertEqual(len(book.blocks), 120 * 151)
        self.assertEqual([entry["t"] for entry in book.toc][::119], ["Chapter 0", "Chapter 119"])

    def test_skeletons_that_overlap_do_not_repeat_the_text(self):
        skeleton = b'<html><head></head><body aid="0"></body></html>'
        fragment = b"<p>inserted</p>"
        text = skeleton + fragment
        records = mobikit.text_records(text)
        claims = [(b"SKEL0", {1: [1], 6: [0, len(skeleton)]})]
        claims += [(b"SKEL%d" % number, {1: [0], 6: [0, len(text)]}) for number in range(1, 40)]
        indexes = mobikit.index(mobikit.SKELETON_TAGS, claims)
        where = skeleton.index(b"</body>")
        indexes += mobikit.index(
            mobikit.FRAGMENT_TAGS,
            [(b"%010d" % where, {2: [0], 3: [0], 4: [0], 6: [0, len(fragment)]})],
            [b"P-//*[@aid='0']"])
        head = mobikit.header(len(text), len(records), version=8, length=264,
                              fields={0xFC: 1 + len(records), 0xF8: 3 + len(records)})
        book = self.convert([head] + records + indexes)
        self.assertEqual(self.texts(book), ["inserted"])

    def test_skeletons_listed_out_of_order_are_read_in_the_order_of_the_text(self):
        skeleton = b'<html><head></head><body aid="0"></body></html>'
        pieces = [b"<p>first</p>", b"<p>second</p>"]
        where = skeleton.index(b"</body>")
        text = b""
        claims = []
        fragments = []
        for number, piece in enumerate(pieces):
            claims.insert(0, (b"SKEL%d" % number, {1: [1], 6: [len(text), len(skeleton)]}))
            fragments.insert(0, (b"%010d" % (len(text) + where),
                                 {2: [0], 3: [1 - number], 4: [1 - number], 6: [0, len(piece)]}))
            text += skeleton + piece
        records = mobikit.text_records(text)
        indexes = mobikit.index(mobikit.SKELETON_TAGS, claims)
        indexes += mobikit.index(mobikit.FRAGMENT_TAGS, fragments, [b"P-//*[@aid='0']"])
        head = mobikit.header(len(text), len(records), version=8, length=264,
                              fields={0xFC: 1 + len(records), 0xF8: 3 + len(records)})
        book = self.convert([head] + records + indexes)
        self.assertEqual(self.texts(book), ["first", "second"])
        self.assertEqual(book.sections, [0, 1])

    def test_position_in_a_fragment_of_no_document_leads_nowhere(self):
        text = b"<html><body><p>all of it</p></body></html>"
        records = mobikit.text_records(text)
        indexes = mobikit.index(mobikit.FRAGMENT_TAGS, [(b"0000000000", {2: [0], 6: [0, 4]})],
                                [b"P-//*[@aid='0']"])
        indexes += mobikit.index(mobikit.NCX_TAGS, [(b"000", {3: [0], 4: [0], 6: [0, 9_999_999]})],
                                 [b"Lost"])
        head = mobikit.header(len(text), len(records), version=8, length=264,
                              fields={0xF8: 1 + len(records), 0xF4: 4 + len(records)})
        with mobi._Pdb(self.write(mobikit.pdb([head] + records + indexes))) as pdb:
            book = mobi._Kf8(mobi._Volume(pdb, ""))
        self.assertEqual([(entry.label, entry.target) for entry in book.entries], [("Lost", None)])

    def test_unlisted_opening_is_titled_after_the_book(self):
        opening = b"".join(b"<p>Sentence %d of a preface that nothing announces.</p>" % number
                           for number in range(30))
        documents = [(b"<html><body>", [opening], b"</body></html>")]
        documents += [(b"<html><body>", [b"<h1>%s</h1><p>text</p>" % name], b"</body></html>")
                      for name in (b"One", b"Two", b"Three")]
        ncx = [("One", 1, 0, 0, None), ("Two", 2, 0, 0, None), ("Three", 3, 0, 0, None)]
        book = self.convert(mobikit.kf8(documents, ncx=ncx, exth=((503, "Eight"),)))
        self.assertEqual([(entry["t"], entry["b"]) for entry in book.toc],
                         [("Eight", 0), ("One", 30), ("Two", 32), ("Three", 34)])

    def test_file_with_both_formats_is_read_from_the_newer(self):
        pictures = [jpeg(300, 400), png(40, 50), jpeg(600, 900)]
        new = eight(images=())
        for exth in ((), ((121, 0),)):
            with self.subTest(exth=exth):
                old = mobikit.mobi6(six_text(), images=pictures, exth=exth)
                if exth:
                    old = mobikit.mobi6(six_text(), images=pictures, exth=((121, len(old) + 1),))
                book = self.convert(mobikit.joint(old, new))
                self.check(book)
                self.assertEqual(book.title, "Eight")
                self.assertEqual(self.texts(book)[0], "One")
                self.assertEqual(len([b for b in book.blocks if b["k"] == "img"]), 2)

    def test_older_half_stands_in_for_a_damaged_newer_one(self):
        old = mobikit.mobi6(six_text(), images=[jpeg(300, 400)], exth=SIX_EXTH)
        new = eight(images=(), compression=mobikit.HUFFMAN)
        book = self.convert(mobikit.joint(old, new))
        self.check(book)
        self.assertEqual(self.texts(book)[0], "Opening words and a link to two, a dead one.")
        self.assertEqual(len(book.sections), 4)

    def test_index_entries_with_a_stated_size(self):
        entries = [(b"A", {1: [5, 6, 7], 6: [10, 20]}), (b"B", {1: [9]})]
        records = mobikit.index(mobikit.SKELETON_TAGS, entries, [b"label", b"other"])
        path = self.write(mobikit.pdb([b"x" * 16] + records))
        with mobi._Pdb(path) as pdb:
            found, labels = mobi._read_index(pdb, 1)
        self.assertEqual(found, entries)
        self.assertEqual(labels, {0: b"label", 6: b"other"})


class RefusalTest(MobiCase):
    def test_drm(self):
        data = mobikit.pdb(mobikit.mobi6(six_text(), encryption=2, images=[jpeg(600, 900)],
                                         exth=SIX_EXTH + ((201, 0),)))
        self.assertEqual(self.refusal(data), ("drm", DRM_SENTENCE))
        meta = mobi.read_meta(self.write(data))
        self.assertEqual((meta.title, meta.error), ("The Title & More", DRM_SENTENCE))
        self.assertEqual(meta.cover, jpeg(600, 900))

    def test_formats_that_are_not_supported(self):
        replica = mobikit.pdb([mobikit.header(100, 1, compression=1), b"%MOP" + bytes(96)])
        packed = mobikit.pdb(mobikit.mobi6(b"<p>text</p>", title="Odd", compression=3))
        sentences = set()
        for name, data in (("topaz.azw", b"TPZ0" + bytes(200)),
                           ("kfx.azw", b"\xeaDRMION\xee" + bytes(200)),
                           ("kfx.kfx", b"CONT" + bytes(200)),
                           ("replica.azw4", replica), ("odd.mobi", packed)):
            with self.subTest(name=name):
                code, message = self.refusal(data, name)
                self.assertEqual(code, "unsupported")
                self.assertTrue(message.endswith(".") and "\n" not in message)
                meta = mobi.read_meta(os.path.join(self.dir, name))
                self.assertEqual(meta.error, message)
                self.assertTrue(meta.title)
                sentences.add(message)
        self.assertEqual(len(sentences), 4)

    def test_missing_file(self):
        for read in (mobi.read_meta, lambda path: mobi.convert(path, self.out)):
            with self.assertRaises(ReaderError) as caught:
                read(os.path.join(self.dir, "gone.mobi"))
            self.assertEqual(caught.exception.code, "missing")

    def test_files_that_are_not_books(self):
        whole = mobikit.pdb(mobikit.mobi6(six_text(), exth=SIX_EXTH))
        for data in (b"", b"short", bytes(4096), whole[:70], whole[:78], whole[:90],
                     whole[:78] + bytes(len(whole) - 78), mobikit.pdb([])):
            with self.subTest(size=len(data)):
                self.assertEqual(self.refusal(data)[0], "corrupt")
                with self.assertRaises(ReaderError) as caught:
                    mobi.read_meta(os.path.join(self.dir, "book.mobi"))
                self.assertEqual(caught.exception.code, "corrupt")

    def test_book_without_text_is_listed_but_does_not_open(self):
        data = mobikit.pdb(mobikit.mobi6(b"", title="Empty"))
        self.assertEqual(self.refusal(data)[0], "corrupt")
        self.assertEqual(mobi.read_meta(os.path.join(self.dir, "book.mobi")).title, "Empty")

    def test_truncation_never_raises_anything_else(self):
        for records in (mobikit.mobi6(six_text() + FILLER, exth=SIX_EXTH, images=[jpeg(30, 40)],
                                      ncx=[("One", 300, 0)]), eight()):
            whole = mobikit.pdb(records)
            for size in range(0, len(whole), 97):
                path = self.write(whole[:size])
                for read in (mobi.read_meta, lambda path: mobi.convert(path, self.out)):
                    try:
                        read(path)
                    except ReaderError as error:
                        self.assertIn(error.code, ("corrupt", "unsupported", "drm"))

    def test_garbled_bytes_never_raise_anything_else(self):
        chance = random.Random(11)
        huffman = mobikit.mobi6(six_text(), compression=mobikit.HUFFMAN, exth=SIX_EXTH)
        joined = mobikit.joint(mobikit.mobi6(six_text(), images=[jpeg(30, 40)]), eight(images=()))
        for records in (mobikit.mobi6(six_text(), exth=SIX_EXTH, images=[jpeg(30, 40)],
                                      ncx=[("One", 300, 0)]), eight(), huffman, joined):
            whole = mobikit.pdb(records)
            for _ in range(150):
                garbled = bytearray(whole)
                for _ in range(chance.choice((1, 2, 8, 64))):
                    garbled[chance.randrange(len(garbled))] = chance.choice(
                        (0, 0xFF, chance.randrange(256)))
                path = self.write(bytes(garbled))
                for read in (mobi.read_meta, lambda path: mobi.convert(path, self.out)):
                    try:
                        book = read(path)
                    except ReaderError as error:
                        self.assertIn(error.code, ("corrupt", "unsupported", "drm"))
                    else:
                        if isinstance(book, mobi.Book):
                            self.check(book, whole=False)

    def test_hostile_sizes_are_bounded(self):
        records = eight()
        head = bytearray(records[0])
        struct.pack_into(">H", head, 8, 0xFFFF)
        struct.pack_into(">L", head, 4, 0xFFFFFFFF)
        index = bytearray(records[struct.unpack_from(">L", head, 0xFC)[0]])
        struct.pack_into(">L", index, 24, 0xFFFFFFFF)
        records[struct.unpack_from(">L", head, 0xFC)[0]] = bytes(index)
        started = time.monotonic()
        book = self.convert([bytes(head)] + records[1:], count=0xFFFF)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("gamma", self.texts(book))

    def test_fragments_that_cost_a_whole_page_each_are_refused(self):
        filler = b"x" * 300_000
        documents = {"moved": (b"", [b"<i>y</i>"] * 1500, b"<p>" + filler + b"</p>"),
                     "searched": (filler, [b"y"] * 1500, b"")}
        for name, document in documents.items():
            with self.subTest(name=name):
                data = mobikit.pdb(mobikit.kf8([document]))
                started = time.monotonic()
                self.assertEqual(self.refusal(data)[0], "corrupt")
                self.assertLess(time.monotonic() - started, 5)

    def test_text_cannot_outgrow_its_file(self):
        tables = mobikit.huffman_tables({ord("A"): b"<p>ab</p>" * 3600})
        head = mobikit.header(0xFFFFFFFF, 4000, compression=mobikit.HUFFMAN,
                              fields={0x70: 4001, 0x74: 2})
        data = mobikit.pdb([head] + [b"A" * 16] * 4000 + tables)
        started = time.monotonic()
        book = mobi.convert(self.write(data), self.out)
        self.assertLess(time.monotonic() - started, 20)
        self.check(book)
        self.assertLess(sum(len(text) for text in self.texts(book)), 16 * len(data))


@unittest.skipUnless(HAVE_SAMPLES, "downloaded Kindle samples are not present")
class SampleTest(MobiCase):
    def sample(self, name):
        return mobi.convert(os.path.join(SAMPLES, name), self.out)

    def chars(self, book):
        return sum(len(plain(block)) for block in book.blocks)

    def test_every_unprotected_sample_converts(self):
        names = sorted(name for name in os.listdir(SAMPLES) if name not in PROTECTED)
        self.assertTrue(names)
        for name in names:
            with self.subTest(name=name):
                self.out = os.path.join(self.dir, name)
                book = self.sample(name)
                self.check(book)
                self.assertTrue(book.title)

    def test_expected_metadata_sections_and_contents(self):
        for name, expected in EXPECTED.items():
            path = os.path.join(SAMPLES, name)
            if not os.path.exists(path):
                continue
            with self.subTest(name=name):
                self.out = os.path.join(self.dir, name)
                title, author, language, sections, entries, deepest, cover = expected
                meta = mobi.read_meta(path)
                book = mobi.convert(path, self.out)
                self.assertEqual((meta.title, meta.language, meta.error), (title, language, ""))
                self.assertEqual((book.title, book.author, book.language), (title, author, language))
                self.assertEqual(len(book.sections), sections)
                self.assertEqual(len(book.toc), entries)
                self.assertEqual(max(entry["d"] for entry in book.toc), deepest)
                self.assertEqual(meta.cover is not None, cover)
                if cover:
                    self.assertGreaterEqual(images.sniff(meta.cover).height, 600)

    def test_both_formats_of_a_book_agree(self):
        for old, new in PAIRS:
            if not all(os.path.exists(os.path.join(SAMPLES, name)) for name in (old, new)):
                continue
            with self.subTest(book=old):
                first, second = self.sample(old), self.sample(new)
                self.assertEqual([entry["t"] for entry in first.toc],
                                 [entry["t"] for entry in second.toc])
                self.assertEqual([entry["d"] for entry in first.toc],
                                 [entry["d"] for entry in second.toc])
                self.assertLess(abs(self.chars(first) - self.chars(second)),
                                0.03 * self.chars(second))

    def test_contents_lead_to_their_chapters(self):
        book = self.sample("se-alice.azw3")
        listed = self.toc(book)
        self.assertEqual([title for title, _, _ in listed[:6]],
                         ["Titlepage", "Imprint", "All in the Golden Afternoon", "Frontispiece",
                          "Alice’s Adventures in Wonderland", "I: Down the Rabbit-Hole"])
        self.assertEqual([depth for _, depth, _ in listed[4:17]], [0] + [1] * 12)
        self.assertEqual(listed[5][2], "I")
        old = self.toc(self.sample("pg11-images.mobi"))
        self.assertEqual(old[3], ("CHAPTER I. Down the Rabbit-Hole", 0, "CHAPTER I."))
        french = self.toc(self.sample("pg17489-fr-images.mobi"))
        self.assertIn(("Monsieur Myriel", 1, "Monsieur Myriel"), french)

    def test_pictures_are_extracted_and_shown(self):
        for name, shown, files in (("pg1342-images.mobi", 164, 164),
                                   ("pg1342-images-kf8.mobi", 107, 164),
                                   ("se-alice.azw3", 45, 44),
                                   ("libmobi-sample-invalid-indx.fail", 36, 12),
                                   ("libmobi-sample-textread.mobi", 1, 1)):
            with self.subTest(name=name):
                self.out = os.path.join(self.dir, name)
                book = self.sample(name)
                self.assertEqual(len([b for b in book.blocks if b["k"] == "img"]), shown)
                self.assertEqual(len(os.listdir(os.path.join(self.out, "img"))), files)

    def test_links_inside_the_book_resolve(self):
        for name, count in (("pg1342-images.mobi", 228), ("pg1342-images-kf8.mobi", 228),
                            ("pg17489-fr-images.mobi", 301), ("se-alice.azw3", 43)):
            with self.subTest(name=name):
                book = self.sample(name)
                found = sum(len(re.findall(r'href="b:\d+"', text)) for block in book.blocks
                            for text in [block.get("t", "")]
                            + [cell for row in block.get("rows", ()) for cell in row])
                self.assertEqual(found, count)

    def test_text_reads_correctly(self):
        texts = self.texts(self.sample("pg17489-fr-images.mobi"))
        self.assertIn("—Quel est ce bonhomme qui me regarde?", texts)
        book = self.sample("libmobi-sample-unicode-huffdic.mobi")
        self.assertEqual(self.texts(book)[0], "This is a sample for testing libmobi project.")
        self.assertEqual(self.texts(book), self.texts(
            self.sample("libmobi-sample-unicode-uncompressed.mobi")))
        book = self.sample("pg11-images-kf8.mobi")
        self.assertIn("There was nothing so <i>very</i> remarkable in that;", book.blocks[19]["t"])

    def test_protected_samples_are_refused_but_listed(self):
        for name in PROTECTED:
            with self.subTest(name=name):
                path = os.path.join(SAMPLES, name)
                with self.assertRaises(ReaderError) as caught:
                    mobi.convert(path, self.out)
                self.assertEqual((caught.exception.code, caught.exception.message),
                                 ("drm", DRM_SENTENCE))
                meta = mobi.read_meta(path)
                self.assertEqual((meta.title, meta.error), ("Libmobi test sample", DRM_SENTENCE))

    def test_large_book_converts_quickly(self):
        started = time.monotonic()
        book = self.sample("pg1342-images-kf8.mobi")
        self.assertLess(time.monotonic() - started, 8)
        self.assertGreater(len(book.blocks), 2000)
        started = time.monotonic()
        mobi.read_meta(os.path.join(SAMPLES, "pg1342-images-kf8.mobi"))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_through_the_library(self):
        from reader import library
        path = os.path.join(SAMPLES, "se-alice.azw3")
        self.assertEqual(library.detect(path), "mobi")
        found = library.info(path)
        self.assertEqual((found["title"], found["author"], found["error"]),
                         ("Alice’s Adventures in Wonderland", "Lewis Carroll", ""))
        self.assertEqual(len(found["toc"]), 20)


if __name__ == "__main__":
    unittest.main()
