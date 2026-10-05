import support

import mmap
import os
import random
import struct
import tempfile
import time
import unicodedata
import unittest
import zlib
from dataclasses import dataclass
from unittest import mock

from reader import archive
from reader.archive import Archive
from reader.errors import ReaderError


@dataclass
class Entry:
    name: str | bytes
    data: bytes = b""
    method: int = 8
    flags: int = 0
    descriptor: str = ""  # "", "signed" or "bare": sizes follow the data instead
    wrong_crc: bool = False
    local_name: bytes | None = None  # name in the local header, when it differs


def build_zip(entries, *, prefix=b"", directory=True, cut=0):
    """A zip packed by hand so every kind of damage can be dialled in.

    `directory=False` leaves out the central directory; `cut` removes that
    many bytes from the end of the finished archive.
    """
    body = b""
    records = []
    for entry in entries:
        name = entry.name.encode("utf-8") if isinstance(entry.name, str) else entry.name
        flags = entry.flags | (8 if entry.descriptor else 0)
        if entry.method == 8:
            packer = zlib.compressobj(9, zlib.DEFLATED, -15)
            packed = packer.compress(entry.data) + packer.flush()
        else:
            packed = entry.data
        crc = zlib.crc32(entry.data) ^ (0xFFFF if entry.wrong_crc else 0)
        sizes = (crc, len(packed), len(entry.data))
        local_name = name if entry.local_name is None else entry.local_name
        offset = len(body)
        body += struct.pack(
            "<4sHHHHHLLLHH", b"PK\x03\x04", 20, flags, entry.method, 0, 0x21,
            *((0, 0, 0) if entry.descriptor else sizes), len(local_name), 0,
        ) + local_name + packed
        if entry.descriptor:
            body += (b"PK\x07\x08" if entry.descriptor == "signed" else b"") + struct.pack("<LLL", *sizes)
        records.append(struct.pack(
            "<4sHHHHHHLLLHHHHHLL", b"PK\x01\x02", 20, 20, flags, entry.method, 0, 0x21,
            *sizes, len(name), 0, 0, 0, 0, 0, offset,
        ) + name)
    if directory:
        listing = b"".join(records)
        body += listing + struct.pack(
            "<4sHHHHLLH", b"PK\x05\x06", 0, 0, len(records), len(records),
            len(listing), len(body), 0,
        )
    data = prefix + body
    return data[:len(data) - cut] if cut else data


TEXT = b"It was the best of times, it was the worst of times. " * 40


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def path(self, data, name="book.zip"):
        path = os.path.join(self._tmp.name, name)
        with open(path, "wb") as out:
            out.write(data)
        return path

    def open(self, entries, **damage):
        opened = Archive.open(self.path(build_zip(entries, **damage)))
        self.addCleanup(opened.close)
        return opened


class TestIntact(ArchiveCase):
    def test_names_read_and_member(self):
        book = self.open([Entry("mimetype", b"application/epub+zip", method=0), Entry("OEBPS/ch1.xhtml", TEXT)])
        self.assertEqual(book.names(), ["mimetype", "OEBPS/ch1.xhtml"])
        self.assertEqual(book.read("mimetype"), b"application/epub+zip")
        self.assertEqual(book.read("OEBPS/ch1.xhtml"), TEXT)
        member = book.member("OEBPS/ch1.xhtml")
        self.assertEqual((member.name, member.size, member.crc, member.encrypted),
                         ("OEBPS/ch1.xhtml", len(TEXT), zlib.crc32(TEXT), False))

    def test_context_manager_closes(self):
        path = self.path(build_zip([Entry("a.txt", b"a")]))
        with Archive.open(path) as book:
            self.assertEqual(book.read("a.txt"), b"a")
        os.remove(path)

    def test_bytes_input(self):
        with Archive.open(build_zip([Entry("inner.fb2", TEXT)])) as book:
            self.assertEqual(book.read("inner.fb2"), TEXT)
        with Archive.open(build_zip([Entry("inner.fb2", TEXT)], directory=False)) as book:
            self.assertEqual(book.read("inner.fb2"), TEXT)

    def test_directories_are_not_members(self):
        book = self.open([Entry("OEBPS/", method=0), Entry("OEBPS/a.txt", b"a")])
        self.assertEqual(book.names(), ["OEBPS/a.txt"])

    def test_absent_name_is_key_error(self):
        book = self.open([Entry("a.txt", b"a")])
        self.assertIsNone(book.find("b.txt"))
        with self.assertRaises(KeyError):
            book.read("b.txt")
        with self.assertRaises(KeyError):
            book.member("b.txt")

    def test_reading_writes_nothing(self):
        book = self.open([Entry("a.txt", TEXT)], directory=False)
        book.read("a.txt")
        self.assertEqual(os.listdir(self._tmp.name), ["book.zip"])

    def test_junk_files_stay_addressable(self):
        book = self.open([Entry("__MACOSX/._a.txt", b"junk"), Entry("a.txt", b"a")])
        self.assertEqual(book.read("__MACOSX/._a.txt"), b"junk")


class TestDamage(ArchiveCase):
    ENTRIES = [Entry("mimetype", b"application/epub+zip", method=0), Entry("OEBPS/ch1.xhtml", TEXT),
               Entry("OEBPS/ch2.xhtml", TEXT[::-1])]

    def check(self, book):
        self.assertEqual(book.names(), ["mimetype", "OEBPS/ch1.xhtml", "OEBPS/ch2.xhtml"])
        self.assertEqual(book.read("mimetype"), b"application/epub+zip")
        self.assertEqual(book.read("OEBPS/ch2.xhtml"), TEXT[::-1])
        member = book.member("OEBPS/ch1.xhtml")
        self.assertEqual((member.size, member.crc), (len(TEXT), zlib.crc32(TEXT)))

    def test_missing_central_directory(self):
        self.check(self.open(self.ENTRIES, directory=False))

    def test_truncated_central_directory(self):
        self.check(self.open(self.ENTRIES, cut=60))

    def test_junk_before_first_header(self):
        self.check(self.open(self.ENTRIES, prefix=b"\x00junk PK before the archive" * 9))

    def test_junk_prefix_without_directory(self):
        self.check(self.open(self.ENTRIES, prefix=b"<html>not a zip yet</html>", directory=False))

    def test_long_trailing_junk(self):
        data = build_zip(self.ENTRIES) + b"\x00" * 80_000
        book = Archive.open(self.path(data))
        self.addCleanup(book.close)
        self.check(book)

    def test_data_descriptors_with_and_without_signature(self):
        for kind in ("signed", "bare"):
            with self.subTest(kind):
                entries = [Entry(e.name, e.data, e.method, descriptor=kind) for e in self.ENTRIES]
                self.check(self.open(entries, directory=False))
                self.check(self.open(entries))

    def test_empty_member_with_descriptor(self):
        book = self.open([Entry("empty.css", b"", descriptor="signed"), Entry("a.txt", b"a")], directory=False)
        self.assertEqual(book.names(), ["empty.css", "a.txt"])
        self.assertEqual(book.read("empty.css"), b"")

    def test_signature_inside_stored_data(self):
        tricky = b"before PK\x03\x04 not a header PK\x01\x02 after"
        book = self.open([Entry("a.bin", tricky, method=0), Entry("b.txt", b"b")], directory=False)
        self.assertEqual(book.names(), ["a.bin", "b.txt"])
        self.assertEqual(book.read("a.bin"), tricky)

    def test_bad_crc_is_tolerated(self):
        book = self.open([Entry("a.txt", TEXT, wrong_crc=True)])
        self.assertEqual(book.read("a.txt"), TEXT)

    def test_local_name_mismatch_is_tolerated(self):
        book = self.open([Entry("OEBPS/a.txt", TEXT, local_name=b"OEBPS\\a.txt")])
        self.assertEqual(book.read("OEBPS/a.txt"), TEXT)

    def test_broken_stream_keeps_what_inflated(self):
        for seed in range(20):
            with self.subTest(seed=seed):
                content = random.Random(seed).randbytes(30_000)
                data = bytearray(build_zip([Entry("a.bin", content)]))
                data[9000:9040] = b"\xff" * 40
                with Archive.open(bytes(data)) as book:
                    got = book.read("a.bin")
                self.assertGreater(len(got), 8000)
                self.assertEqual(got[:8000], content[:8000])

    def test_file_cut_mid_member(self):
        data = build_zip([Entry("a.txt", TEXT), Entry("b.bin", os.urandom(30_000))], directory=False)
        with Archive.open(data[:-10_000]) as book:
            self.assertEqual(book.names(), ["a.txt", "b.bin"])
            self.assertEqual(book.read("a.txt"), TEXT)
            self.assertTrue(0 < len(book.read("b.bin")) < 30_000)

    def test_member_does_not_inflate(self):
        data = bytearray(build_zip([Entry("a.bin", os.urandom(5000))]))
        data[40:4000] = b"\xff" * 3960
        with Archive.open(bytes(data)) as book:
            self.assertEqual(book.member("a.bin").size, 5000)

    def test_nothing_readable(self):
        for data in (b"", b"plain text, not an archive", b"PK\x03\x04", build_zip([])):
            with self.subTest(data[:12]):
                with self.assertRaises(ReaderError) as caught:
                    Archive.open(self.path(data))
                self.assertEqual(caught.exception.code, "corrupt")
        with self.assertRaises(ReaderError) as caught:
            Archive.open(os.path.join(self._tmp.name, "absent.epub"))
        self.assertEqual(caught.exception.code, "missing")


class TestSearching(ArchiveCase):
    """How a damaged archive is held while it is searched for its members."""

    def test_an_ordinary_damaged_archive_is_read_not_mapped(self):
        # A mapped file that shrinks while it is read kills the process.
        path = self.path(build_zip([Entry("a.txt", TEXT), Entry("b.txt", b"b")], directory=False))
        with mock.patch.object(archive.mmap, "mmap", side_effect=AssertionError("mapped")):
            book = Archive.open(path)
            found = book.read("a.txt"), book.read("b.txt")
        book.close()
        self.assertEqual(found, (TEXT, b"b"))

    def test_only_a_very_large_one_is_mapped(self):
        path = self.path(build_zip([Entry("a.txt", TEXT)], directory=False))
        with mock.patch.object(archive, "_READ_WHOLE", 16):
            with Archive.open(path) as book:
                self.assertIsInstance(book._data(), mmap.mmap)
                self.assertEqual(book.read("a.txt"), TEXT)

    def test_one_too_large_to_map_is_damaged_not_a_crash(self):
        path = self.path(build_zip([Entry("a.txt", TEXT)], directory=False))
        with mock.patch.object(archive, "_READ_WHOLE", 16), \
                mock.patch.object(archive.mmap, "mmap", side_effect=OSError(12, "Cannot allocate memory")):
            with self.assertRaises(ReaderError) as caught:
                Archive.open(path)
        self.assertEqual(caught.exception.code, "corrupt")

    def test_a_file_cut_short_after_it_was_measured(self):
        path = self.path(build_zip([Entry("a.txt", TEXT), Entry("b.txt", TEXT)], directory=False))
        whole = os.path.getsize(path)
        real = os.fstat

        def measured_earlier(handle):
            status = real(handle)
            os.truncate(path, whole - 10)
            return status

        with mock.patch.object(archive.os, "fstat", side_effect=measured_earlier):
            with Archive.open(path) as book:
                self.assertEqual(book.read("a.txt"), TEXT)
                self.assertEqual(book.names(), ["a.txt", "b.txt"])


class TestNames(ArchiveCase):
    def test_backslashes_and_leading_separators(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                book = self.open([Entry("OEBPS\\Text\\ch1.xhtml", b"1"), Entry("/abs/a.txt", b"2"),
                                  Entry("./rel//b.txt", b"3")], directory=directory)
                self.assertEqual(book.names(), ["OEBPS/Text/ch1.xhtml", "abs/a.txt", "rel/b.txt"])
                self.assertEqual(book.read("OEBPS/Text/ch1.xhtml"), b"1")
                self.assertEqual(book.find("./rel/b.txt"), "rel/b.txt")
                self.assertEqual(book.find("OEBPS\\Text\\ch1.xhtml"), "OEBPS/Text/ch1.xhtml")

    def test_names_escaping_the_root_are_rejected(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                book = self.open([Entry("../../etc/passwd", b"x"), Entry("a/../../b", b"y"),
                                  Entry("ok/../fine.txt", b"z")], directory=directory)
                self.assertEqual(book.names(), ["fine.txt"])
                self.assertIsNone(book.find("../../etc/passwd"))

    def test_unflagged_utf8_and_legacy_names(self):
        chinese = "OEBPS/第一章.xhtml"
        for directory in (True, False):
            with self.subTest(directory=directory):
                book = self.open([Entry(chinese.encode("utf-8"), b"1"), Entry(b"caf\xe9.txt", b"2"),
                                  Entry("flagged-ü.txt", b"3", flags=0x800)], directory=directory)
                self.assertEqual(book.names(), [chinese, "café.txt", "flagged-ü.txt"])
                self.assertEqual(book.read("café.txt"), b"2")

    def test_names_are_nfc(self):
        stored = unicodedata.normalize("NFD", "résumé.xhtml")
        book = self.open([Entry(stored, b"1", flags=0x800)])
        composed = unicodedata.normalize("NFC", stored)
        self.assertEqual(book.names(), [composed])
        self.assertEqual(book.find(stored), composed)
        self.assertEqual(book.read(stored), b"1")

    def test_later_duplicates_win(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                book = self.open([Entry("a.txt", b"first"), Entry("b.txt", b"b"), Entry("a.txt", b"second")],
                                 directory=directory)
                self.assertEqual(sorted(book.names()), ["a.txt", "b.txt"])
                self.assertEqual(book.read("a.txt"), b"second")
                self.assertEqual(book.member("a.txt").size, 6)

    def test_find_loose_spellings(self):
        book = self.open([Entry("OEBPS/Text/Chapter One.xhtml", b"1", flags=0x800),
                          Entry("OEBPS/café.xhtml", b"2", flags=0x800),
                          Entry("OEBPS/100%.jpg", b"3"), Entry("OEBPS/a%20b.txt", b"4")])
        cases = {
            "OEBPS/Text/Chapter One.xhtml": "OEBPS/Text/Chapter One.xhtml",
            "OEBPS/Text/Chapter%20One.xhtml": "OEBPS/Text/Chapter One.xhtml",
            "oebps/text/chapter%20one.XHTML": "OEBPS/Text/Chapter One.xhtml",
            "OEBPS/caf%C3%A9.xhtml": "OEBPS/café.xhtml",
            "OEBPS/caf%E9.xhtml": "OEBPS/café.xhtml",
            "OEBPS/CAFÉ.xhtml": "OEBPS/café.xhtml",
            "OEBPS/100%.jpg": "OEBPS/100%.jpg",
            "OEBPS/a%20b.txt": "OEBPS/a%20b.txt",
            "/OEBPS/100%25.jpg": "OEBPS/100%.jpg",
            "OEBPS/missing.xhtml": None,
            "": None,
        }
        for asked, expected in cases.items():
            with self.subTest(asked):
                self.assertEqual(book.find(asked), expected)
        self.assertEqual(book.read("oebps/CAF%C3%A9.xhtml"), b"2")

    def test_exact_name_beats_case_insensitive(self):
        book = self.open([Entry("Cover.jpg", b"upper"), Entry("cover.jpg", b"lower")])
        self.assertEqual(book.find("cover.jpg"), "cover.jpg")
        self.assertEqual(book.find("COVER.JPG"), "Cover.jpg")

    def test_lookups_stay_fast_in_a_large_archive(self):
        book = self.open([Entry("OEBPS/c%d.xhtml" % n, method=0) for n in range(20_000)])
        started = time.monotonic()
        for n in range(20_000):
            self.assertEqual(book.find("oebps/C%d.XHTML" % n), "OEBPS/c%d.xhtml" % n)
            self.assertIsNone(book.find("OEBPS/missing%d.xhtml" % n))
        self.assertLess(time.monotonic() - started, 5)


class TestLimits(ArchiveCase):
    def test_encrypted_member_is_drm(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                book = self.open([Entry("secret.xhtml", TEXT, flags=1), Entry("a.txt", b"a")],
                                 directory=directory)
                self.assertTrue(book.member("secret.xhtml").encrypted)
                self.assertFalse(book.member("a.txt").encrypted)
                with self.assertRaises(ReaderError) as caught:
                    book.read("secret.xhtml")
                self.assertEqual(caught.exception.code, "drm")
                self.assertEqual(book.read("a.txt"), b"a")

    def test_oversize_member_is_refused(self):
        for directory in (True, False):
            with self.subTest(directory=directory):
                book = self.open([Entry("big.txt", TEXT)], directory=directory)
                with self.assertRaises(ReaderError) as caught:
                    book.read("big.txt", limit=100)
                self.assertEqual(caught.exception.code, "corrupt")
                self.assertEqual(book.read("big.txt", limit=len(TEXT)), TEXT)

    def test_oversize_stored_member_is_refused(self):
        book = self.open([Entry("big.txt", TEXT, method=0)], directory=False)
        with self.assertRaises(ReaderError):
            book.read("big.txt", limit=100)

    def test_total_budget(self):
        path = self.path(build_zip([Entry("a.txt", TEXT), Entry("b.txt", TEXT)]))
        with Archive.open(path, budget=len(TEXT) + 10) as book:
            self.assertEqual(book.read("a.txt"), TEXT)
            with self.assertRaises(ReaderError) as caught:
                book.read("b.txt")
            self.assertEqual(caught.exception.code, "corrupt")

    def test_a_refused_read_is_charged_and_a_spent_budget_inflates_nothing(self):
        # A thousand names for one huge member must not each be inflated in turn.
        entries = [Entry("%d.bin" % number, TEXT) for number in range(6)]
        for directory in (True, False):
            with self.subTest(directory=directory):
                path = self.path(build_zip(entries, directory=directory))
                with Archive.open(path, budget=2 * len(TEXT) + 10) as book:
                    self.assertEqual(book.read("0.bin"), TEXT)
                    self.assertEqual(book.read("1.bin"), TEXT)
                    with self.assertRaises(ReaderError):
                        book.read("2.bin")  # ten bytes were left: tried, refused, charged
                    with mock.patch.object(Archive, "_read_listed", side_effect=AssertionError("inflated")), \
                            mock.patch.object(Archive, "_read_raw", side_effect=AssertionError("inflated")):
                        for name in ("3.bin", "4.bin", "5.bin"):
                            with self.assertRaises(ReaderError) as caught:
                                book.read(name)
                            self.assertEqual(caught.exception.code, "corrupt")

    def test_a_damaged_member_is_not_charged(self):
        # Broken pictures must not use up what the chapters after them need.
        data = bytearray(build_zip([Entry("bad.bin", TEXT), Entry("good.txt", TEXT)]))
        data[37:41] = b"\xff\xff\xff\xff"  # where bad.bin's stream begins
        with Archive.open(self.path(bytes(data)), budget=len(TEXT) + 10) as book:
            for _ in range(3):
                with self.assertRaises(ReaderError):
                    book.read("bad.bin")
            self.assertEqual(book.read("good.txt"), TEXT)

    def test_default_caps(self):
        self.assertEqual(archive.MAX_MEMBER, 256 * 1024 * 1024)
        self.assertEqual(archive.MAX_TOTAL, 2 * 1024 * 1024 * 1024)

    def test_deflate_bomb_is_cut_off_while_inflating(self):
        book = self.open([Entry("bomb.txt", bytes(8 * 1024 * 1024))], directory=False)
        with self.assertRaises(ReaderError):
            book.read("bomb.txt", limit=1024)


class TestLibrary(unittest.TestCase):
    def test_every_book_opens(self):
        books = support.library_books()
        if not books:
            self.skipTest("no library on this machine")
        for path in books:
            with self.subTest(os.path.basename(path)):
                with Archive.open(path) as book:
                    names = book.names()
                    self.assertTrue(names)
                    for name in names:
                        member = book.member(name)
                        self.assertEqual(member.name, name)
                        self.assertGreaterEqual(member.size, 0)
                        self.assertIsInstance(member.crc, int)
                        self.assertEqual(book.find(name), name)
                    container = book.find("meta-inf/CONTAINER.xml")
                    self.assertIsNotNone(container)
                    self.assertIn(b"rootfile", book.read(container))


if __name__ == "__main__":
    unittest.main()
