import support

import base64
import functools
import os
import tempfile
import time
import unittest
from unittest import mock

from epubkit import png
from reader import text
from reader.errors import ReaderError
from test_acceptance import check_block

RUSSIAN = ("Глава 1\n\nВ начале июля, в чрезвычайно жаркое время, под вечер, один молодой человек "
           "вышел из своей каморки на улицу и медленно, как бы в нерешимости, отправился к мосту.\n")
GREEK = "Κεφάλαιο\n\nΌλοι οι άνθρωποι γεννιούνται ελεύθεροι και ίσοι στην αξιοπρέπεια και τα δικαιώματα.\n"
JAPANESE = "第一章\n\n吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。何でも薄暗いじめじめした所で泣いていた。\n"
CHINESE = "第一章\n\n这是一个测试文件。我们在这里说的是中国的事情，他们也来了。大家都知道这个道理。\n"
KOREAN = "제1장\n\n모든 인간은 태어날 때부터 자유로우며 그 존엄과 권리에 있어 동등하다. 그리고 이것은 사실이다.\n"
FRENCH = "CHAPITRE PREMIER\n\nC’était — déjà — l’été à Besançon ; « naïveté » coûte 5 €.\n"

PROSE = ("It was a dark and stormy night; the rain fell in torrents, except at",
         "occasional intervals, when it was checked by a violent gust of wind",
         "which swept up the streets and rattled along the house-tops, fiercely",
         "agitating the scanty flame of the lamps that struggled against it.")
JOINED = " ".join(PROSE)

POLISH = ("Rozdział 1\n\nWczoraj wieczorem poszedłem do miasta, żeby kupić chleb i trochę mleka; było już "
          "późno, a księżyc świecił jasno nad łąką. Zażółć gęślą jaźń.\n")
CZECH = ("Kapitola 1\n\nVčera večer jsem šel do města, abych koupil chléb a trochu mléka; bylo už pozdě "
         "a měsíc svítil jasně. Příliš žluťoučký kůň úpěl ďábelské ódy.\n")
HUNGARIAN = ("Tegnap este elmentem a városba, hogy kenyeret és egy kis tejet vegyek; már késő volt, és "
             "a hold fényesen sütött a rét fölött. Árvíztűrő tükörfúrógép.\n")
CROATIAN = ("Jučer navečer otišao sam u grad kupiti kruh i malo mlijeka; već je bilo kasno. Čovjek će "
            "doći, đak također; žena šuti.\n")
TURKISH = ("Dün akşam şehre gittim, ekmek ve biraz süt almak için; artık geç olmuştu ve ay çayırın "
           "üzerinde parlıyordu. Pijamalı hasta yağız şoföre çabucak güvendi.\n")
WESTERN = (
    "Über die Größe der Bäume ließ sich streiten; schön war es, und die Vögel sangen fröhlich.\n",
    "¿Dónde está el niño? Mañana lloverá en la montaña, según él; ¡qué lástima! La cigüeña voló.\n",
    "Não há razão para a ação; o coração da nação está em São Paulo. Você já viu? É difícil, às vezes.\n",
    "Perché la città è così bella? Più tardi andrò in università, ma già so che sarà lì; però è così.\n",
    "De coëfficiënt is één; hij zei dat het reëel was, en de zeeën en de ideeën. Hé, dát is óók zo.\n",
    "Jag går över ån för att äta på kaféet; där är det skönt att sitta på våren, säger hon ändå.\n",
    "Jeg går på gaden og ser på søen; æblerne er røde, og børnene løber. På lørdag køber vi brød.\n",
    "Það er gott veður í dag og ég ætla að fara út. Þú þarft að koma; við sjáum fjörðinn, sagði hún áðan.\n",
    "La seva àvia va dir que això és així; demà anirem, però només si vols. Què és? És difícil, açò és útil.\n",
    "Õhtul läksime üle jõe, et näha päikest. Šokolaad ja žanr on võõrsõnad; öö oli külm.\n",
    "A plain text with one footnote mark¹ and nothing else, at 20° and for 5 £.\n",
)


def novel(chapters=3, paragraphs=6):
    """A hard-wrapped book in the Project Gutenberg manner."""
    lines = []
    for chapter in range(1, chapters + 1):
        lines += ["", "", "CHAPTER %d" % chapter, ""]
        for _ in range(paragraphs):
            lines += [*PROSE, ""]
    return "\n".join(lines) + "\n"


class TextCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = scratch.name
        self.out = os.path.join(self.dir, "out")

    def write(self, content, name="book.txt", encoding="utf-8"):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode(encoding))
        return path

    def convert(self, content, name="book.txt", encoding="utf-8"):
        book = text.convert(self.write(content, name, encoding), self.out)
        total = len(book.blocks)
        for block in book.blocks:
            self.assertEqual(check_block(block, total)[0], set(), block)
        return book

    def shape(self, book):
        return [(block["k"], block.get("t", "")) for block in book.blocks]

    def toc(self, book):
        return [(entry["t"], entry["d"], entry["b"]) for entry in book.toc]


class DecodingTest(TextCase):
    def test_legacy_encodings_are_told_apart_without_any_declaration(self):
        cases = ((RUSSIAN, "cp1251"), (RUSSIAN, "koi8-r"), (RUSSIAN, "cp866"), (GREEK, "cp1253"),
                 (JAPANESE, "shift_jis"), (JAPANESE, "euc_jp"), (CHINESE, "gb18030"),
                 (KOREAN, "euc_kr"), (FRENCH, "cp1252"), (FRENCH, "utf-8"), (RUSSIAN, "utf-8"))
        for content, encoding in cases:
            with self.subTest(encoding=encoding, text=content[:8]):
                self.assertEqual(text.decode_text(content.encode(encoding)), content)

    def test_central_european_and_turkish_are_told_from_western_without_any_declaration(self):
        cases = ((POLISH, "cp1250"), (POLISH, "iso8859-2"), (CZECH, "cp1250"), (CZECH, "iso8859-2"),
                 (HUNGARIAN, "cp1250"), (HUNGARIAN, "iso8859-2"), (CROATIAN, "cp1250"),
                 (CROATIAN, "iso8859-2"), (TURKISH, "cp1254"), (TURKISH, "iso8859-9"),
                 (POLISH + CZECH + HUNGARIAN, "cp1250"), (POLISH + CZECH + HUNGARIAN, "iso8859-2"))
        for content, encoding in cases:
            with self.subTest(encoding=encoding, text=content[:8]):
                self.assertEqual(text.decode_text(content.encode(encoding)), content)

    def test_western_european_languages_stay_western(self):
        for content in (FRENCH, *WESTERN):
            for encoding in ("cp1252", "latin-1"):
                with self.subTest(encoding=encoding, text=content[:8]):
                    try:
                        raw = content.encode(encoding)
                    except UnicodeEncodeError:
                        continue
                    self.assertEqual(text.decode_text(raw), content)

    def test_a_latin_code_page_is_not_guessed_against_a_declaration(self):
        self.assertEqual(text.decode_text(POLISH.encode("cp1250"), declared="windows-1250"), POLISH)
        self.assertEqual(text.decode_text(POLISH.encode("iso8859-2"), declared="iso-8859-2"), POLISH)
        self.assertNotEqual(text.decode_text(POLISH.encode("cp1250"), declared="windows-1252"), POLISH)

    def test_utf16_is_read_with_or_without_a_byte_order_mark(self):
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding):
                self.assertEqual(text.decode_text(FRENCH.encode(encoding)), FRENCH)
        self.assertEqual(text.decode_text(RUSSIAN.encode("utf-16")), RUSSIAN)

    def test_a_utf8_mark_and_windows_line_endings_are_removed(self):
        raw = b"\xef\xbb\xbf" + FRENCH.replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(text.decode_text(raw), FRENCH)

    def test_a_wrong_declaration_does_not_win_over_what_the_text_looks_like(self):
        self.assertEqual(text.decode_text(RUSSIAN.encode("koi8-r"), declared="windows-1251"), RUSSIAN)
        self.assertEqual(text.decode_text(RUSSIAN.encode("cp1251"), declared="koi8-r"), RUSSIAN)
        self.assertEqual(text.decode_text(RUSSIAN.encode("cp1251"), declared="iso-8859-1"), RUSSIAN)
        self.assertEqual(text.decode_text(RUSSIAN.encode("cp1251"), declared="utf-8"), RUSSIAN)

    def test_an_honest_declaration_of_a_latin_code_page_is_believed(self):
        polish = "Zażółć gęślą jaźń i wróć do domu, żeby się wyspać.\n"
        self.assertEqual(text.decode_text(polish.encode("cp1250"), declared="windows-1250"), polish)

    def test_a_stray_byte_does_not_turn_utf8_into_something_else(self):
        raw = RUSSIAN.encode("utf-8") + b"\xff tail"
        self.assertTrue(text.decode_text(raw).startswith(RUSSIAN))

    def test_files_convert_in_their_own_script(self):
        for content, encoding in ((RUSSIAN, "cp1251"), (RUSSIAN, "koi8-r"), (JAPANESE, "shift_jis"),
                                  (CHINESE, "gb18030")):
            with self.subTest(encoding=encoding):
                book = self.convert(content, encoding=encoding)
                heading, paragraph = content.strip().split("\n\n")
                self.assertEqual(self.shape(book), [("h", heading), ("p", paragraph)])


class LayoutTest(TextCase):
    def test_hard_wrapped_paragraphs_are_unwrapped(self):
        book = self.convert(novel(1, 8))
        self.assertEqual(self.shape(book), [("h", "CHAPTER 1")] + [("p", JOINED)] * 8)

    def test_one_paragraph_per_line(self):
        lines = ["Paragraph %d. " % n + "It goes on without a single line break for a long while. " * 4
                 for n in range(12)]
        book = self.convert("\n".join(lines) + "\n")
        self.assertEqual([block["t"] for block in book.blocks], [line.strip() for line in lines])

    def test_one_paragraph_per_line_with_blank_lines_between(self):
        lines = ["Short line %d of a file with a paragraph on every line and gaps." % n for n in range(12)]
        long = "A much longer paragraph that runs far past any margin. " * 5
        book = self.convert("\n\n".join(lines + [long] * 6) + "\n")
        self.assertEqual(len(book.blocks), 18)
        self.assertFalse(any("s" in block for block in book.blocks[1:]))

    def test_indented_first_lines_start_paragraphs_when_no_blank_lines_do(self):
        paragraph = ["    " + PROSE[0], *PROSE[1:]]
        book = self.convert("\n".join(paragraph * 10) + "\n")
        self.assertEqual(self.shape(book), [("p", JOINED)] * 10)

    def test_indented_verse_keeps_its_lines_and_its_shape(self):
        verse = ["    Twinkle, twinkle, little bat!", "      How I wonder what you’re at!",
                 "    Up above the world you fly,", "      Like a tea-tray in the sky."]
        book = self.convert(novel(1, 8) + "\n".join(verse) + "\n\n" + "\n".join(PROSE) + "\n")
        block = book.blocks[9]
        self.assertEqual(block["t"], "Twinkle, twinkle, little bat!\n\xa0\xa0How I wonder what you’re at!\n"
                                     "Up above the world you fly,\n\xa0\xa0Like a tea-tray in the sky.")
        self.assertEqual((block["k"], block["i"]), ("p", 1))
        self.assertEqual(book.blocks[10]["t"], JOINED)

    def test_an_indented_passage_filled_to_the_margin_is_a_quotation_not_verse(self):
        quoted = "\n".join("     " + line[:60].strip() for line in PROSE)
        book = self.convert(novel(1, 8) + quoted + "\n")
        self.assertEqual(book.blocks[9]["t"], " ".join(line[:60].strip() for line in PROSE))
        self.assertEqual(book.blocks[9]["i"], 1)

    def test_short_unindented_lines_stay_apart_in_a_wrapped_book(self):
        book = self.convert(novel(2, 8) + "Yours sincerely,\nJane\n")
        self.assertEqual(book.blocks[-1]["t"], "Yours sincerely,\nJane")

    def test_a_table_drawn_with_spaces_is_preformatted(self):
        table = "    Apples      3     1.20\n    Pears      12     0.80\n"
        book = self.convert(novel(1, 8) + table)
        self.assertEqual(self.shape(book)[-1], ("pre", "Apples      3     1.20\nPears      12     0.80"))

    def test_words_broken_at_a_hyphen_and_ideographs_join_without_a_space(self):
        book = self.convert("A well-\nknown fact -\nor so they say.\n\n吾輩は猫である。\n名前はまだ無い。\n")
        self.assertEqual([block["t"] for block in book.blocks],
                         ["A well-known fact - or so they say.", "吾輩は猫である。名前はまだ無い。"])

    def test_scene_breaks_become_rules_and_runs_of_them_one_rule(self):
        breaks = "\n       *       *       *       *       *\n\n  * * *\n\n"
        book = self.convert("First.\n" + breaks + "Second.\n\n---\n\nThird.\n")
        self.assertEqual([block["k"] for block in book.blocks], ["p", "hr", "p", "hr", "p"])

    def test_extra_blank_lines_are_a_gap(self):
        book = self.convert(novel(1, 8).rstrip("\n") + "\n\n\n\n" + "\n".join(PROSE) + "\n")
        self.assertEqual(book.blocks[-1].get("s"), 1)
        self.assertNotIn("s", book.blocks[-2])

    def test_underscores_become_italics_also_across_paragraphs(self):
        book = self.convert("It was _very_ odd, a snake_case_name and a ____ blank.\n\n"
                            "_A whole passage in italics\n\nthat ends here._ Then plain <text> & more.\n")
        self.assertEqual([(block["t"], block.get("f", 0)) for block in book.blocks], [
            ("It was <i>very</i> odd, a snake_case_name and a ____ blank.", 1),
            ("<i>A whole passage in italics</i>", 1),
            ("<i>that ends here.</i> Then plain &lt;text&gt; &amp; more.", 1),
        ])

    def test_tabs_form_feeds_and_control_characters(self):
        book = self.convert("One\tword.\x00\x07\n\x0cTwo.\n")
        self.assertEqual([block["t"] for block in book.blocks], ["One word.", "Two."])


class HeadingTest(TextCase):
    def headings(self, book):
        return [(block["l"], block["t"]) for block in book.blocks if block["k"] == "h"]

    def test_chapter_lines_in_their_many_spellings(self):
        lines = ("CHAPTER I", "Chapter 12. The Storm", "BOOK TWO", "Part 3", "PROLOGUE", "Epilogue.",
                 "Глава 7", "CHAPITRE PREMIER", "Kapitel 4", "第三章 归来", "[Chapter IX.]", "Letter XX")
        content = "".join("%s\n\n%s\n\n" % (line, JOINED) for line in lines)
        self.assertEqual(self.headings(self.convert(content)), [
            (2, "CHAPTER I"), (2, "Chapter 12. The Storm"), (1, "BOOK TWO"), (1, "Part 3"),
            (2, "PROLOGUE"), (2, "Epilogue."), (2, "Глава 7"), (2, "CHAPITRE PREMIER"), (2, "Kapitel 4"),
            (2, "第三章 归来"), (2, "Chapter IX."), (2, "Letter XX")])

    def test_bare_numbers_standing_alone_are_headings(self):
        content = "".join("%s\n\n%s\n\n" % (line, JOINED) for line in ("XIV", "12."))
        self.assertEqual(self.headings(self.convert(content)), [(2, "XIV"), (2, "12.")])

    def test_chapters_of_a_text_with_no_blank_lines(self):
        for words in ("Chapter %d", "Глава %d", "第%d章 风起", "Chapter %d: The Storm", "Глава %d. Буря.",
                      "# Part %d"):
            with self.subTest(words=words):
                lines = []
                for chapter in range(1, 5):
                    lines += [words % chapter, *[JOINED] * 5]
                book = self.convert("\n".join(lines))
                self.assertEqual([title for title, _, _ in self.toc(book)],
                                 [(words % chapter).lstrip("# ") for chapter in range(1, 5)])
                self.assertEqual(book.sections, [0, 6, 12, 18])

    def test_a_line_between_other_lines_that_reads_as_a_sentence_is_no_heading(self):
        lines = [JOINED, "Introduction"]
        for line in ("Letter 4 arrived late.", "Chapter 3 was the hardest.", "Chapter 3 Nobody came.",
                     "Part 2 of the plan failed", "第三部分的内容很简单。", "NO", "XIV", "THE DEPARTURE"):
            lines += [JOINED, line, JOINED, JOINED]
        lines += ["Chapter 2", "Letter 4 arrived late.", JOINED, "", "THE ARRIVAL", "", JOINED]
        book = self.convert("\n".join(lines))
        self.assertEqual(self.headings(book), [(2, "Introduction"), (2, "Chapter 2"), (2, "THE ARRIVAL")])

    def test_a_title_on_the_line_below_joins_the_heading(self):
        book = self.convert("CHAPTER I.\nDown the Rabbit-Hole\n\n%s\n" % JOINED)
        self.assertEqual(self.shape(book)[0], ("h", "CHAPTER I.\nDown the Rabbit-Hole"))
        self.assertEqual(self.toc(book), [("CHAPTER I. Down the Rabbit-Hole", 0, 0)])

    def test_a_short_line_of_capitals_standing_alone_is_a_heading(self):
        book = self.convert("%s\n\nTHE DEPARTURE\n\n%s\n\nNOT ALONE\n%s\n" % (JOINED, JOINED, JOINED))
        self.assertEqual(self.headings(book), [(2, "THE DEPARTURE")])

    def test_a_lone_line_of_capitals_is_no_chapter_in_a_book_with_chapter_headings(self):
        signs = "\n%s\n\nNO ADMITTANCE\n\n%s\n\nI\n\nam not a number.\n" % (JOINED, JOINED)
        opening = "THE HOUSE ON THE HILL\n\nby A. Writer\n"
        book = self.convert(opening + novel(3, 2) + signs)
        self.assertEqual(self.headings(book), [(2, "THE HOUSE ON THE HILL"), (2, "CHAPTER 1"),
                                               (2, "CHAPTER 2"), (2, "CHAPTER 3")])
        self.assertEqual([(block["t"], block["k"]) for block in book.blocks if block.get("a") == "c"],
                         [("NO ADMITTANCE", "p"), ("I", "p")])
        self.assertEqual(len(book.sections), 4)
        fewer = self.convert(opening + novel(2, 2) + signs)
        self.assertEqual([title for _, title in self.headings(fewer)][3:], ["NO ADMITTANCE", "I"])

    def test_bare_numbers_that_count_on_divide_a_book_with_chapter_headings(self):
        numbers = ("I", "II", "III", "I", "VII.", "5", "6", "I")
        numbered = "".join("\n%s\n\n%s\n" % (number, JOINED) for number in numbers)
        book = self.convert(novel(3, 1) + numbered + "\nA SIGN\n\n%s\n" % JOINED)
        self.assertEqual([title for _, title in self.headings(book)],
                         ["CHAPTER 1", "CHAPTER 2", "CHAPTER 3", "I", "II", "III", "5", "6"])

    def test_shouted_lines_that_are_not_headings(self):
        for line in ("“JANE BENNET.”", "CHORUS.", "THE END", "A, B, AND C,", "OK",
                     " " * 50 + "PAGE", "AN ALL CAPITALS SENTENCE THAT GOES ON MUCH TOO LONG TO BE THE TITLE OF A CHAPTER"):
            with self.subTest(line=line):
                self.assertEqual(self.headings(self.convert("%s\n\n%s\n\n%s\n" % (JOINED, line, JOINED))), [])

    def test_markdown_headings(self):
        book = self.convert("# The Book\n\nIntro.\n\n## One ##\n\nText.\n\nTwo\n---\n\nMore.\n\nTop\n===\n\nEnd.\n")
        self.assertEqual(self.headings(book), [(1, "The Book"), (2, "One"), (2, "Two"), (1, "Top")])
        self.assertEqual(self.toc(book), [("The Book", 0, 0), ("One", 1, 2), ("Two", 1, 4), ("Top", 0, 6)])

    def test_a_contents_listing_is_not_a_run_of_headings(self):
        listing = "CONTENTS\n\nCHAPTER I. The Start\nCHAPTER II. The End\n\n"
        spaced = "Chapter 1\n\nChapter 2\n\nChapter 3\n\n%s\n\n" % JOINED
        book = self.convert(listing + spaced + "Chapter 2\n\n%s\n" % JOINED + novel(1, 8))
        self.assertEqual(self.headings(book),
                         [(2, "CONTENTS"), (2, "Chapter 3"), (2, "Chapter 2"), (2, "CHAPTER 1")])
        self.assertEqual(book.blocks[1]["t"], "CHAPTER I. The Start\nCHAPTER II. The End")
        self.assertEqual(self.shape(book)[2:4], [("p", "Chapter 1"), ("p", "Chapter 2")])

    def test_a_title_page_of_capitals_is_not_headings(self):
        book = self.convert("A TALE\n\nOF TWO FIXTURES\n\nCHAPTER I\n\n%s\n" % JOINED)
        self.assertEqual(self.headings(book), [(2, "CHAPTER I")])
        self.assertEqual([(block["t"], block.get("a")) for block in book.blocks[:2]],
                         [("A TALE", "c"), ("OF TWO FIXTURES", "c")])

    def test_prose_that_starts_like_a_heading(self):
        content = ("%s\n\nPart of the reason, as everyone in the village knew,\nwas the weather.\n\n"
                   "Chapter and verse were quoted at him.\n" % JOINED)
        self.assertEqual(self.headings(self.convert(content)), [])

    def test_headings_start_sections_and_make_the_contents(self):
        book = self.convert("Front matter.\n" + novel(3, 2))
        self.assertEqual(book.sections, [0, 1, 4, 7])
        self.assertEqual(self.toc(book), [("CHAPTER 1", 0, 1), ("CHAPTER 2", 0, 4), ("CHAPTER 3", 0, 7)])
        self.assertEqual([book.blocks[index].get("s") for index in book.sections], [2, 2, 2, 2])

    def test_parts_hold_their_chapters(self):
        book = self.convert("PART ONE\n\nChapter 1\n\nText.\n\nChapter 2\n\nText.\n\nPART TWO\n\nChapter 3\n\nText.\n")
        self.assertEqual([(title, depth) for title, depth, _ in self.toc(book)],
                         [("PART ONE", 0), ("Chapter 1", 1), ("Chapter 2", 1), ("PART TWO", 0),
                          ("Chapter 3", 1)])


GUTENBERG = """The Project Gutenberg eBook of The Long Title

Title: The Long Title
       and Its Second Line

Author: Ann Example

Language: French

*** START OF THE PROJECT GUTENBERG EBOOK THE LONG TITLE ***

CHAPTER I

%s

*** END OF THE PROJECT GUTENBERG EBOOK THE LONG TITLE ***

Licence words that are not part of the book.
"""


class MetaTest(TextCase):
    def meta(self, content, name="book.txt"):
        path = self.write(content, name)
        meta = text.read_meta(path)
        book = text.convert(path, self.out)
        self.assertEqual((meta.title, meta.authors, meta.language), (book.title, book.authors, book.language))
        self.assertIsNone(meta.cover)
        return meta.title, book.author, meta.language

    def test_gutenberg_header_gives_title_author_and_language_and_is_removed(self):
        content = GUTENBERG % JOINED
        self.assertEqual(self.meta(content), ("The Long Title and Its Second Line", "Ann Example", "fr"))
        self.assertEqual(self.shape(self.convert(content)), [("h", "CHAPTER I"), ("p", JOINED)])

    def test_title_and_author_lines_without_the_gutenberg_markers(self):
        self.assertEqual(self.meta("Title: Loose Notes\nAuthor: Lewis, C. S.\n\nText.\n"),
                         ("Loose Notes", "C. S. Lewis", ""))

    def test_first_short_line_standing_alone_is_the_title(self):
        self.assertEqual(self.meta("The Wind in the Reeds\n\n%s\n" % JOINED, "x.txt")[0],
                         "The Wind in the Reeds")
        self.assertEqual(self.meta("# Notes on Rain\n\nText.\n", "x.txt")[0], "Notes on Rain")

    def test_file_name_is_the_title_when_the_text_offers_none(self):
        for content in ("%s\n\nMore.\n" % JOINED, "CHAPTER I\n\nText.\n", "Two lines\nat once.\n", "", "* * *\n\nText.\n"):
            with self.subTest(content=content[:20]):
                self.assertEqual(self.meta(content, "My_Great Book.txt")[0], "My Great Book")

    def test_read_meta_reads_only_the_start_of_the_file(self):
        path = self.write("A Heavy Tome\n\n" + novel(400, 8))
        self.assertGreater(os.path.getsize(path), 2 * text.HEAD)
        with mock.patch.object(text, "_read", wraps=text._read) as read:
            self.assertEqual(text.read_meta(path).title, "A Heavy Tome")
        self.assertEqual([call.args[1] for call in read.call_args_list], [text.HEAD])


class BoundsTest(TextCase):
    def test_a_megabyte_novel_converts_well_within_a_second(self):
        content = novel(64, 60)
        self.assertGreater(len(content), 1_000_000)
        path = self.write(content)
        started = time.perf_counter()
        book = text.convert(path, self.out)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual((len(book.blocks), len(book.toc), len(book.sections)), (64 * 61, 64, 64))

    def test_an_empty_file_still_gives_a_book(self):
        for content in (b"", b"\n\n  \n", b"\xef\xbb\xbf"):
            book = self.convert(content, "Blank.txt")
            self.assertEqual((book.title, len(book.blocks), book.sections, book.toc), ("Blank", 1, [0], []))

    def test_noise_and_endless_lines_do_not_hang(self):
        started = time.perf_counter()
        for content in (os.urandom(200_000), b"# " + b"x #" * 700_000, b"_" * 1_000_000,
                        b"* " * 500_000, b"word " * 600_000, b"\n" * 1_000_000, b"CHAPTER I\n" * 100_000):
            book = text.convert(self.write(content), self.out)
            self.assertGreaterEqual(len(book.blocks), 1)
            text.read_meta(self.write(content))
        self.assertLess(time.perf_counter() - started, 20.0)

    def test_past_the_bound_on_blocks_the_text_is_kept_in_larger_ones(self):
        wrapped = "line\n\n" * 500 + "CHAPTER 9\n\nwrapped\nwords\n\nThe last sentence.\n"
        with mock.patch.object(text, "MAX_BLOCKS", 50):
            book = self.convert(wrapped)
            per_line = self.convert("\n".join([JOINED] * 100 + ["The last sentence."]))
        self.assertEqual(len(book.blocks), 52)
        self.assertEqual([block["t"] for block in book.blocks[:50]], ["line"] * 50)
        self.assertEqual("\n".join(block["t"] for block in book.blocks[50:]),
                         "line\n" * 450 + "CHAPTER 9\nwrapped words\nThe last sentence.")
        self.assertLessEqual(max(len(block["t"]) for block in book.blocks), 2000)
        self.assertEqual(book.toc, [])
        self.assertEqual(len(per_line.blocks), 50 + 8)
        self.assertEqual("\n".join(block["t"] for block in per_line.blocks[50:]),
                         "\n".join([JOINED] * 50 + ["The last sentence."]))

    def test_a_missing_file_is_missing(self):
        for call in (text.read_meta, lambda path: text.convert(path, self.out)):
            with self.assertRaises(ReaderError) as caught:
                call(os.path.join(self.dir, "gone.txt"))
            self.assertEqual(caught.exception.code, "missing")


PAGE = """<!DOCTYPE html>
<html lang="de-AT"><head><meta charset="%s"><title>Ein  Prüfbuch &amp; mehr</title>
<meta content="Jörg Müßig" name="author"><link rel="stylesheet" href="css/style.css">%s</head>
<body><h1 id="top">Anfang</h1><p class="it">Größe</p>%s<h2>Zwei</h2><p><a href="#top">hinauf</a>,
<a href="page.html#top">auch</a> <a href="other.html">fort</a> <a href="https://example.org/">außen</a></p>
</body></html>"""


class PageTest(TextCase):
    def page(self, body="", head="", encoding="utf-8", name="page.html"):
        return self.write(PAGE % (encoding, head, body), os.path.join("site", name), encoding)

    def convert_page(self, path):
        book = text.convert(path, self.out)
        total = len(book.blocks)
        for block in book.blocks:
            self.assertEqual(check_block(block, total)[0], set(), block)
        return book

    def test_title_author_and_language_come_from_the_head(self):
        for encoding in ("utf-8", "iso-8859-1"):
            with self.subTest(encoding=encoding):
                path = self.page(encoding=encoding)
                meta = text.read_meta(path)
                self.assertEqual((meta.title, meta.authors, meta.language, meta.cover),
                                 ("Ein Prüfbuch & mehr", ["Jörg Müßig"], "de-at", None))
                book = self.convert_page(path)
                self.assertEqual((book.title, book.author), (meta.title, "Jörg Müßig"))
                self.assertEqual(book.blocks[1]["t"], "Größe")

    def test_a_page_without_a_title_is_named_after_its_file(self):
        path = self.write("<html><body><p>Only words.</p></body></html>", "Loose_Page.htm")
        self.assertEqual(text.read_meta(path).title, "Loose Page")
        self.assertEqual(self.shape(self.convert_page(path)), [("p", "Only words.")])

    def test_a_page_is_recognised_by_content_whatever_its_name(self):
        path = self.write("<!DOCTYPE html><title>Named Wrongly</title><p>a <b>b</b></p>", "page.txt")
        self.assertEqual(text.read_meta(path).title, "Named Wrongly")
        self.assertEqual(text.convert(path, self.out).blocks[0]["t"], "a <b>b</b>")

    def test_headings_make_the_contents_and_links_resolve(self):
        book = self.convert_page(self.page())
        self.assertEqual(self.toc(book), [("Anfang", 0, 0), ("Zwei", 1, 2)])
        self.assertEqual(book.sections, [0])
        self.assertEqual(book.blocks[3]["t"], '<a href="b:0">hinauf</a>, <a href="b:0">auch</a> fort '
                                              '<a href="https://example.org/">außen</a>')

    def test_stylesheets_beside_the_page_are_applied(self):
        path = self.page()
        self.write(".it { font-style: italic }", os.path.join("site", "css", "style.css"))
        self.assertEqual(self.convert_page(path).blocks[1]["t"], "<i>Größe</i>")

    def test_pictures_beside_the_page_are_copied(self):
        picture = png(100, 80)
        path = self.page('<img src="img/a%20b.png" alt="Bild"><img src="./img/a b.png?v=2#x">')
        self.write(picture, os.path.join("site", "img", "a b.png"))
        book = self.convert_page(path)
        blocks = [block for block in book.blocks if block["k"] == "img"]
        self.assertEqual([(block["w"], block["h"], block["alt"]) for block in blocks],
                         [(100, 80, "Bild"), (100, 80, "")])
        self.assertEqual({os.path.relpath(block["src"], self.out) for block in blocks}, {"img/0001.png"})
        with open(blocks[0]["src"], "rb") as handle:
            self.assertEqual(handle.read(), picture)

    def test_pictures_inside_the_page_are_stored(self):
        uri = "data:image/png;base64," + base64.b64encode(png(90, 70)).decode()
        book = self.convert_page(self.page('<img src="%s">' % uri))
        self.assertEqual([(block["w"], block["h"]) for block in book.blocks if block["k"] == "img"], [(90, 70)])

    def test_nothing_outside_the_pages_folder_is_read(self):
        secret = self.write(png(100, 80), "secret.png")
        self.write(".it { font-style: italic }", "style.css")
        os.makedirs(os.path.join(self.dir, "site"))
        os.symlink(secret, os.path.join(self.dir, "site", "link.png"))
        os.symlink(self.dir, os.path.join(self.dir, "site", "up"))
        sources = ("../secret.png", "..%2Fsecret.png", secret, "file://" + secret, "link.png", "up/secret.png",
                   "https://example.org/a.png", "//example.org/a.png", "img/../../secret.png", "..\\secret.png")
        body = "".join('<img src="%s">' % source for source in sources)
        path = self.page(body, '<link rel="stylesheet" href="../style.css">')
        book = self.convert_page(path)
        self.assertEqual([block["k"] for block in book.blocks], ["h", "p", "h", "p"])
        self.assertEqual(book.blocks[1]["t"], "Größe")
        self.assertFalse(os.path.exists(os.path.join(self.out, "img")))

    def test_oversized_files_are_not_loaded(self):
        path = self.page('<img src="big.png">')
        self.write(png(100, 80), os.path.join("site", "big.png"))
        self.write(".it { font-style: italic }", os.path.join("site", "css", "style.css"))
        small = functools.partial(text.Pictures, each=10)
        with mock.patch.object(text, "MAX_SHEET", 10), mock.patch.object(text, "Pictures", small):
            book = self.convert_page(path)
        self.assertEqual([block["k"] for block in book.blocks], ["h", "p", "h", "p"])
        self.assertEqual(book.blocks[1]["t"], "Größe")

    def test_damaged_pages_degrade(self):
        for content in (b"<html><body><p>cut off <b>here", b"<html>" + os.urandom(5000), b"<html></html>",
                        b"<!DOCTYPE html>" + b"<div>" * 20000 + b"deep"):
            with self.subTest(content=content[:20]):
                path = self.write(content, "damaged.html")
                self.assertGreaterEqual(len(text.convert(path, self.out).blocks), 1)
                self.assertEqual(text.read_meta(path).title, "damaged")


@unittest.skipUnless(os.path.isdir(support.sample("txt")), "the text samples are not on this machine")
class TextSamplesTest(TextCase):
    def book(self, name):
        book = text.convert(support.sample("txt", name), self.out)
        total = len(book.blocks)
        for block in book.blocks:
            self.assertEqual(check_block(block, total)[0], set(), block)
        return book

    def test_every_sample_converts_and_agrees_with_its_metadata(self):
        names = sorted(os.listdir(support.sample("txt")))
        self.assertTrue(names)
        for name in names:
            with self.subTest(sample=name):
                started = time.perf_counter()
                book = self.book(name)
                self.assertLess(time.perf_counter() - started, 1.0)
                meta = text.read_meta(support.sample("txt", name))
                self.assertEqual((meta.title, meta.authors), (book.title, book.authors))
                self.assertGreaterEqual(len(book.toc), 2)
                self.assertNotIn("\ufffd", "".join(block.get("t", "") for block in book.blocks))

    def test_the_small_samples_read_the_same_in_every_encoding(self):
        french = "C’était — déjà — l’été à Besançon ; « naïveté » coûte 5 €. La ligne suivante est " \
                 "enveloppée sur plusieurs lignes courtes."
        expected = {
            "french-cp1252.txt": ["CHAPITRE PREMIER", french, "Chapitre II", "Fin."],
            "french-utf16le-bom.txt": ["CHAPITRE PREMIER", french, "Chapitre II", "Fin."],
            "french-latin1.txt": ["CHAPITRE PREMIER", "C'était - déjà - l'été à Besançon ; « naïveté » coûte "
                                  "5 EUR. La ligne suivante est enveloppée sur plusieurs lignes courtes.",
                                  "Chapitre II", "Fin."],
            "japanese-shiftjis.txt": ["第一章", "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。",
                                      "第二章", "終わり。"],
            "chinese-gb18030.txt": ["第一章 开始", "这是一个测试文件。", "第二章 结束", "完。"],
        }
        for name, texts in expected.items():
            with self.subTest(sample=name):
                book = self.book(name)
                self.assertEqual([block["t"] for block in book.blocks], texts)
                self.assertEqual([block["k"] for block in book.blocks], ["h", "p", "h", "p"])
                self.assertEqual(book.title, name[:-4])

    def test_the_cyrillic_samples_are_one_paragraph_per_line(self):
        books = [self.book("one-paragraph-per-line-%s.txt" % encoding) for encoding in ("cp1251", "koi8r")]
        self.assertEqual(books[0].blocks, books[1].blocks)
        self.assertEqual(self.toc(books[0]), [("Глава 1", 0, 0), ("Глава 2", 0, 9)])
        self.assertEqual(len(books[0].blocks), 15)
        self.assertTrue(books[0].blocks[1]["t"].startswith("Абзац номер 0: длинная строка без переносов"))

    def test_the_gutenberg_fixture(self):
        book = self.book("gutenberg-style-utf8-bom-crlf.txt")
        self.assertEqual((book.title, book.author, book.language),
                         ("A Synthetic Fixture", "Nobody In Particular", "en"))
        self.assertEqual([title for title, _, _ in self.toc(book)],
                         ["A SYNTHETIC FIXTURE", "CONTENTS", "CHAPTER I. The Beginning",
                          "CHAPTER II. The Middle", "CHAPTER III. The End"])
        self.assertEqual(book.blocks[3]["t"], "CHAPTER I. The Beginning\nCHAPTER II. The Middle")
        self.assertEqual(book.blocks[9], {"k": "p", "i": 1, "t": "Indented verse line one\nIndented verse line two"})
        self.assertEqual(book.blocks[10], {"k": "hr"})
        self.assertTrue(book.blocks[5]["t"].endswith("an em—dash and a <i>italic</i> word."))
        self.assertNotIn("Gutenberg", "".join(block.get("t", "") for block in book.blocks))

    def test_alice(self):
        book = self.book("pg11-alice.txt")
        self.assertEqual((book.title, book.author), ("Alice's Adventures in Wonderland", "Lewis Carroll"))
        titles = [title for title, _, _ in self.toc(book)]
        self.assertEqual(len(titles), 13)
        self.assertEqual(titles[:2] + titles[-1:], ["Contents", "CHAPTER I. Down the Rabbit-Hole",
                                                    "CHAPTER XII. Alice’s Evidence"])
        first = book.blocks[book.toc[1]["b"] + 1]["t"]
        self.assertTrue(first.startswith("Alice was beginning to get very tired of sitting by her sister on the bank, and"))
        self.assertNotIn("\n", first)

    def test_pride_and_prejudice(self):
        book = self.book("pg1342-pride.txt")
        self.assertEqual((book.title, book.author), ("Pride and Prejudice", "Jane Austen"))
        titles = [title for title, _, _ in self.toc(book)]
        self.assertEqual(len(titles), 62)
        self.assertEqual(titles[:3] + titles[-1:], ["PREFACE.", "Chapter I.", "CHAPTER II.", "CHAPTER LXI."])
        self.assertGreater(sum(len(block.get("t", "")) for block in book.blocks), 650_000)


@unittest.skipUnless(os.path.isdir(support.sample("html")), "the web page samples are not on this machine")
class PageSamplesTest(TextCase):
    def test_every_page_sample_converts(self):
        names = sorted(name for name in os.listdir(support.sample("html")) if name.endswith((".html", ".htm", ".xhtml")))
        self.assertTrue(names)
        for name in names:
            with self.subTest(sample=name), tempfile.TemporaryDirectory() as out:
                book = text.convert(support.sample("html", name), out)
                meta = text.read_meta(support.sample("html", name))
                self.assertEqual((meta.title, meta.authors), (book.title, book.authors))
                total = len(book.blocks)
                for block in book.blocks:
                    self.assertEqual(check_block(block, total)[0], set(), block)

    def test_the_german_page(self):
        book = text.convert(support.sample("html", "german-latin1-meta.html"), self.out)
        self.assertEqual((book.title, book.author, book.language), ("Ein Prüfbuch", "Jörg Müßig", "de"))
        self.assertEqual([block["k"] for block in book.blocks],
                         ["h", "p", "h", "p", "img", "h", "li", "li", "pre", "p", "p"])
        self.assertEqual(book.blocks[1]["t"], "Größe, Maß & Übermut.")
        self.assertEqual((book.blocks[4]["w"], book.blocks[4]["h"], book.blocks[4]["alt"]), (32, 32, "Bild"))
        self.assertEqual(book.blocks[10]["t"], '<a href="b:0">nach oben</a>')
        self.assertEqual(self.toc(book), [("Ein Prüfbuch", 0, 0), ("Kapitel 1", 1, 2), ("Kapitel 2", 1, 5)])


if __name__ == "__main__":
    unittest.main()
