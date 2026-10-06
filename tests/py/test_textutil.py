import support

import os
import tempfile
import unittest

from reader import textutil
from reader.textutil import (
    collapse_space,
    decode_bytes,
    natural_key,
    replace_entities,
    strip_declarations,
    strip_illegal_xml,
    title_sort_key,
    keep_private,
    write_atomic,
)

FRENCH = "Le cœur a ses raisons — « déjà vu », naïveté, façade. " * 20
CHINESE = "第一章 风雪夜归人。这是一个很长的故事，讲的是很久以前的事情。" * 20
JAPANESE = "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。" * 20
RUSSIAN = "Все счастливые семьи похожи друг на друга, каждая несчастливая семья несчастлива по-своему. " * 20


class TestDecodeMarks(unittest.TestCase):
    def test_byte_order_marks(self):
        for codec, mark in (("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
                            ("utf-32-le", b"\xff\xfe\x00\x00"), ("utf-32-be", b"\x00\x00\xfe\xff")):
            with self.subTest(codec):
                self.assertEqual(decode_bytes(mark + FRENCH.encode(codec)), FRENCH)

    def test_mark_outranks_declaration(self):
        raw = b"\xef\xbb\xbf" + '<?xml version="1.0" encoding="windows-1252"?><p>café 第</p>'.encode("utf-8")
        for kind in ("content", "package"):
            self.assertIn("café 第", decode_bytes(raw, kind=kind))

    def test_utf16_without_mark(self):
        text = '<?xml version="1.0" encoding="UTF-16"?><p>café</p>'
        self.assertEqual(decode_bytes(text.encode("utf-16-le")), text)
        self.assertEqual(decode_bytes(text.encode("utf-16-be")), text)

    def test_truncated_utf16_still_reads(self):
        raw = b"\xff\xfe" + "<p>café</p>".encode("utf-16-le") + b"\x00"
        self.assertTrue(decode_bytes(raw).startswith("<p>café</p>"))

    def test_broken_utf8_after_mark_falls_through(self):
        raw = b"\xef\xbb\xbf" + FRENCH.encode("utf-8") + b"\xe9"
        self.assertTrue(decode_bytes(raw).startswith(FRENCH))

    def test_newlines_and_mark_removed(self):
        self.assertEqual(decode_bytes(b"\xef\xbb\xbfa\r\nb\rc\n"), "a\nb\nc\n")
        self.assertEqual(decode_bytes(b""), "")


class TestDecodeDeclared(unittest.TestCase):
    def test_utf8_beats_a_wrong_declaration(self):
        for label in ("ISO-8859-1", "windows-1252", "gb2312", "shift_jis"):
            raw = ('<?xml version="1.0" encoding="%s"?><p>%s</p>' % (label, FRENCH)).encode("utf-8")
            for kind in ("content", "package"):
                with self.subTest(label, kind=kind):
                    self.assertIn(FRENCH, decode_bytes(raw, kind=kind))

    def test_xml_declaration(self):
        raw = ('<?xml version="1.0" encoding="windows-1251"?><p>%s</p>' % RUSSIAN).encode("cp1251")
        for kind in ("content", "package"):
            self.assertIn(RUSSIAN, decode_bytes(raw, kind=kind))

    def test_meta_charset(self):
        raw = ('<html><head><meta charset="koi8-r"></head><p>%s</p>' % RUSSIAN).encode("koi8-r")
        self.assertIn(RUSSIAN, decode_bytes(raw))

    def test_meta_http_equiv(self):
        raw = ('<html><head><META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=Shift_JIS">'
               "</head><p>%s</p>" % JAPANESE).encode("shift_jis")
        self.assertIn(JAPANESE, decode_bytes(raw))

    def test_declaration_beyond_the_sniffed_head_is_ignored(self):
        self.assertEqual(textutil.declared_encoding(b" " * 60_000 + b'<meta charset="koi8-r">'), "")
        self.assertEqual(textutil.declared_encoding(b'<?xml version="1.0" encoding="UTF-8"?>'), "UTF-8")

    def test_explicit_declaration_argument(self):
        self.assertEqual(decode_bytes(RUSSIAN.encode("cp1251"), declared="cp1251"), RUSSIAN)

    def test_aliases(self):
        gbk_only = "镕基" + CHINESE  # 镕 is in GBK but not in GB2312
        cases = (
            ("gb2312", gbk_only, "gbk"),
            ("GB_2312-80", gbk_only, "gbk"),
            ("x-sjis", JAPANESE, "shift_jis"),
            ("macintosh", FRENCH, "mac-roman"),
            ("x-mac-roman", FRENCH, "mac-roman"),
            ("iso-8859-1", "“quoted” – dash", "cp1252"),
            ("us-ascii", "“quoted” – dash", "cp1252"),
        )
        for label, text, codec in cases:
            with self.subTest(label):
                raw = ('<?xml version="1.0" encoding="%s"?><p>' % label).encode("ascii") + text.encode(codec)
                self.assertIn(text, decode_bytes(raw))

    def test_unknown_or_binary_declaration_is_ignored(self):
        for label in ("x-user-defined", "none", "zlib", "hex", "klingon"):
            with self.subTest(label):
                raw = ('<?xml version="1.0" encoding="%s"?><p>café</p>' % label).encode("cp1252")
                self.assertIn("café", decode_bytes(raw))

    def test_false_utf16_declaration_is_ignored(self):
        raw = b'<?xml version="1.0" encoding="UTF-16"?><p>caf\xe9 society</p>'
        self.assertIn("café society", decode_bytes(raw, kind="package"))

    def test_seven_bit_stateful_encoding(self):
        raw = ('<html><head><meta charset="iso-2022-jp"></head><p>%s</p>' % JAPANESE).encode("iso-2022-jp")
        self.assertTrue(raw.isascii())
        for kind in ("content", "package"):
            self.assertIn(JAPANESE, decode_bytes(raw, kind=kind))

    def test_package_believes_a_multibyte_declaration_first(self):
        # Bytes that are valid GBK as declared, and all but valid UTF-8 too.
        text = "鏁呬簨" * 30 + "中"
        raw = b'<?xml version="1.0" encoding="gbk"?><t>' + text.encode("gbk") + b"</t>"
        self.assertIn(text, decode_bytes(raw, kind="package"))
        self.assertIn("故事" * 30 + "\ufffd", decode_bytes(raw, kind="content"))


class TestDecodeGuesses(unittest.TestCase):
    def test_stray_bytes_do_not_spoil_utf8(self):
        raw = FRENCH.encode("utf-8") + b" stray \x92 byte \xe9 here " + FRENCH.encode("utf-8")
        for declared in ("", "iso-8859-1"):
            for kind in ("content", "package"):
                with self.subTest(declared=declared, kind=kind):
                    text = decode_bytes(raw, kind=kind, declared=declared)
                    self.assertTrue(text.startswith(FRENCH))
                    self.assertEqual(text.count("\ufffd"), 2)

    def test_language_hint(self):
        cases = (("zh", CHINESE, "gbk"), ("zh-TW", "第一章 風雪夜歸人。" * 20, "big5"), ("ja", JAPANESE, "shift_jis"),
                 ("ja-JP", JAPANESE, "euc_jp"), ("ko", "나는 고양이로소이다. 이름은 아직 없다. " * 20, "euc_kr"),
                 ("ru", RUSSIAN, "cp1251"), ("pl_PL", "Zażółć gęślą jaźń. " * 20, "cp1250"))
        for language, text, codec in cases:
            with self.subTest(language, codec=codec):
                self.assertEqual(decode_bytes(text.encode(codec), language=language), text)

    def test_cp1252_fallback_never_fails(self):
        raw = b"\x93smart\x94 quotes \x97 and the undefined \x81\x8d\x8f\x90\x9d"
        self.assertEqual(decode_bytes(raw), "“smart” quotes — and the undefined \x81\x8d\x8f\x90\x9d")
        self.assertEqual(len(decode_bytes(bytes(range(256)))), 256)

    def test_unknown_language_falls_back_to_cp1252(self):
        self.assertEqual(decode_bytes(b"caf\xe9", language="xx"), "café")


class TestCleaning(unittest.TestCase):
    def test_strip_declarations(self):
        self.assertEqual(strip_declarations('<?xml version="1.0" encoding="gbk"?>\n<a/>'), "\n<a/>")
        self.assertEqual(strip_declarations("<?XML version='1.0'?><a/>"), "<a/>")
        kept = '<?xml-stylesheet href="a.css"?><a/>'
        self.assertEqual(strip_declarations(kept), kept)

    def test_strip_illegal_xml(self):
        self.assertEqual(strip_illegal_xml("a\x00b\x01c\x0bd\x0ce\x1ff\x7fg\ufffeh\uffffi\ud800j"), "abcdefghij")
        self.assertEqual(strip_illegal_xml("tab\tnl\ncr\r é 第 \U0001f600"), "tab\tnl\ncr\r é 第 \U0001f600")

    def test_replace_entities(self):
        cases = {
            "a&nbsp;b": "a\u00a0b",
            "&mdash;&eacute;&hellip;": "—é…",
            "&#233;&#xe9;&#XE9;": "ééé",
            "&#146;&#x80;": "’€",
            "&#129;": "",
            "&squot;&hellips;": "&apos;…",
            "&bogus; &nbsp &; & &#; &#xZZ;": "&bogus; &nbsp &; & &#; &#xZZ;",
            "&amp;&lt;&gt;&quot;&apos;": "&amp;&lt;&gt;&quot;&apos;",
            "&#38;&#60;&#x3E;&#34;&#39;": "&amp;&lt;&gt;&quot;&apos;",
            "&AMP;&LT;&GT;&QUOT;": "&amp;&lt;&gt;&quot;",
            "&#0;&#3;&#xD800;&#x110000;&#99999999999;": "&#99999999999;",
            "&#128512;": "\U0001f600",
            '<a title="x&nbsp;y">': '<a title="x\u00a0y">',
        }
        for written, expected in cases.items():
            with self.subTest(written):
                self.assertEqual(replace_entities(written), expected)

    def test_collapse_space(self):
        self.assertEqual(collapse_space("  a \t\r\n b\f\vc  "), "a b c")
        self.assertEqual(collapse_space("\u00a0a\u00a0\u00a0b "), "\u00a0a\u00a0\u00a0b")
        self.assertEqual(collapse_space(" \n "), "")


class TestKeys(unittest.TestCase):
    def test_natural_key(self):
        names = ["ch10.xhtml", "ch2.xhtml", "Ch1.xhtml", "ch2b.xhtml", "appendix", "10", "9", "ch02.xhtml"]
        self.assertEqual(sorted(names, key=natural_key),
                         ["9", "10", "appendix", "Ch1.xhtml", "ch02.xhtml", "ch2.xhtml", "ch2b.xhtml", "ch10.xhtml"])

    def test_natural_key_mixed_shapes_compare(self):
        names = ["a", "1", "a1", "1a", "", "a1b2", "9" * 5000, "٣"]
        self.assertEqual(len(sorted(names, key=natural_key)), len(names))

    def test_title_sort_key(self):
        self.assertEqual(title_sort_key("The Time Machine"), "time machine")
        self.assertEqual(title_sort_key("  A   Tale of Two Cities"), "tale of two cities")
        self.assertEqual(title_sort_key("An Essay"), "essay")
        self.assertEqual(title_sort_key("“The Road”"), "road”")
        self.assertEqual(title_sort_key("…And Then There Were None"), "and then there were none")
        self.assertEqual(title_sort_key("Émile"), "emile")
        self.assertEqual(title_sort_key("Theology"), "theology")
        self.assertEqual(title_sort_key("A"), "a")
        self.assertEqual(title_sort_key("The"), "the")
        self.assertEqual(title_sort_key("!!!"), "!!!")
        self.assertEqual(title_sort_key(""), "")
        titles = ["the Zebra", "An apple", "Émile", "A Banana", "1984"]
        self.assertEqual(sorted(titles, key=title_sort_key), ["1984", "An apple", "A Banana", "Émile", "the Zebra"])


class TestWriteAtomic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_creates_parents_and_replaces(self):
        path = os.path.join(self._tmp.name, "books", "key", "meta.json")
        write_atomic(path, b"one")
        write_atomic(path, b"two")
        with open(path, "rb") as written:
            self.assertEqual(written.read(), b"two")
        self.assertEqual(os.listdir(os.path.dirname(path)), ["meta.json"])

    def test_what_is_written_is_closed_to_other_users(self):
        mask = os.umask(0o022)
        self.addCleanup(os.umask, mask)
        path = os.path.join(self._tmp.name, "books", "key", "meta.json")
        write_atomic(path, b"one")
        write_atomic(path, b"two")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        for directory in (os.path.dirname(path), os.path.join(self._tmp.name, "books")):
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
        self.assertEqual(os.umask(0o022), 0o022)

    def test_an_open_directory_is_closed(self):
        directory = os.path.join(self._tmp.name, "cache")
        os.mkdir(directory)
        os.chmod(directory, 0o755)
        keep_private(directory)
        self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
        keep_private(os.path.join(self._tmp.name, "absent"))

    def test_failure_leaves_nothing_behind(self):
        target = os.path.join(self._tmp.name, "taken")
        os.mkdir(target)
        with self.assertRaises(OSError):
            write_atomic(target, b"data")
        self.assertEqual(os.listdir(self._tmp.name), ["taken"])
        with self.assertRaises(TypeError):
            write_atomic(os.path.join(self._tmp.name, "bad"), "not bytes")
        self.assertEqual(os.listdir(self._tmp.name), ["taken"])


if __name__ == "__main__":
    unittest.main()
