import support  # noqa: F401  (sets up the import path)

import unittest

from reader.blocks import BookBuilder
from reader.toc import build_toc


def book(*chapters):
    """A builder from `(name, title, [blocks])` chapters, with `#id` anchors on `("id", block)`."""
    builder = BookBuilder()
    for name, title, items in chapters:
        builder.begin_section(name)
        builder.section_title(title)
        for item in items:
            if isinstance(item, tuple):
                builder.anchor(item[0])
                item = item[1]
            builder.add(item)
    builder.finish()
    return builder


def p(text="Some body text."):
    return {"k": "p", "t": text}


def h(level, text):
    return {"k": "h", "l": level, "t": text}


def entry(title, name, fragment="", depth=0):
    return {"t": title, "d": depth, "name": name, "fragment": fragment}


def titles(toc):
    return [(item["t"], item["d"], item["b"]) for item in toc]


class DeclaredTests(unittest.TestCase):
    def setUp(self):
        self.builder = book(
            ("a.xhtml", "Book", [h(1, "One"), p(), ("s1", h(2, "One point one")), p()]),
            ("b.xhtml", "Book", [h(1, "Two"), p(), ("s2", p("Deep in two"))]),
            ("c.xhtml", "Book", [h(1, "Three"), p()]))

    def test_declared_contents_are_resolved(self):
        toc = build_toc([entry("One", "a.xhtml"), entry("1.1", "a.xhtml", "s1", 1),
                         entry("Two", "b.xhtml"), entry("Three", "c.xhtml")], self.builder)
        self.assertEqual(titles(toc), [("One", 0, 0), ("1.1", 1, 2), ("Two", 0, 4), ("Three", 0, 7)])
        self.assertEqual(set(toc[0]), {"t", "d", "b"})

    def test_missing_fragment_resolves_to_the_document(self):
        toc = build_toc([entry("One", "a.xhtml"), entry("Lost", "b.xhtml", "nope"),
                         entry("Three", "c.xhtml")], self.builder)
        self.assertEqual(titles(toc)[1], ("Lost", 0, 4))

    def test_dead_entries_are_dropped_and_children_promoted(self):
        toc = build_toc([entry("One", "a.xhtml"), entry("Gone", "zz.xhtml"),
                         entry("Child", "b.xhtml", "s2", 1), entry("Three", "c.xhtml"),
                         entry("Sub", "c.xhtml", "", 1)], self.builder)
        self.assertEqual(titles(toc), [("One", 0, 0), ("Child", 1, 6), ("Three", 0, 7), ("Sub", 1, 7)])

    def test_titles_are_cleaned(self):
        toc = build_toc([entry("  One\n\t and&shy; o­nly ", "a.xhtml"), entry("", "b.xhtml"),
                         entry(None, "c.xhtml"), entry("x" * 500, "a.xhtml", "s1")], self.builder)
        self.assertEqual(toc[0]["t"], "One and&shy; only")
        self.assertEqual(toc[1]["t"], "Two")
        self.assertEqual(toc[2]["t"], "Three")
        self.assertLessEqual(len(toc[3]["t"]), 121)
        self.assertTrue(toc[3]["t"].endswith("…"))

    def test_untitled_entry_without_a_heading_is_dropped(self):
        toc = build_toc([entry("One", "a.xhtml"), entry("", "b.xhtml", "s2"), entry("Two", "b.xhtml"),
                         entry("Three", "c.xhtml")], self.builder)
        self.assertEqual([item["t"] for item in toc], ["One", "Two", "Three"])

    def test_consecutive_duplicates_are_merged(self):
        toc = build_toc([entry("One", "a.xhtml"), entry("One", "a.xhtml"), entry("Two", "b.xhtml"),
                         entry("Two", "b.xhtml", "s2"), entry("Three", "c.xhtml"),
                         entry("One", "a.xhtml")], self.builder)
        self.assertEqual(titles(toc), [("One", 0, 0), ("Two", 0, 4), ("Two", 0, 6), ("Three", 0, 7),
                                       ("One", 0, 0)])

    def test_depth_is_renormalised(self):
        toc = build_toc([entry("a", "a.xhtml", "", 2), entry("b", "a.xhtml", "s1", 5),
                         entry("c", "b.xhtml", "", 9), entry("d", "b.xhtml", "s2", 5),
                         entry("e", "c.xhtml", "", 2), entry("f", "c.xhtml", "", 1),
                         entry("g", "c.xhtml", "", "x")], self.builder)
        self.assertEqual([item["d"] for item in toc], [0, 1, 2, 1, 0, 0, 0])

    def test_group_label_without_target_opens_at_its_first_child(self):
        toc = build_toc([entry("Part I", ""), entry("One", "a.xhtml", "", 1),
                         entry("Part II", ""), entry("Two", "b.xhtml", "", 1),
                         entry("Empty part", ""), entry("Three", "c.xhtml")], self.builder)
        self.assertEqual(titles(toc), [("Part I", 0, 0), ("One", 1, 0), ("Part II", 0, 4),
                                       ("Two", 1, 4), ("Three", 0, 7)])

    def test_good_contents_are_kept_even_when_headings_offer_more(self):
        toc = build_toc([entry("I", "a.xhtml"), entry("II", "b.xhtml"), entry("III", "c.xhtml")],
                        self.builder)
        self.assertEqual([item["t"] for item in toc], ["I", "II", "III"])

    def test_every_entry_has_a_valid_block(self):
        toc = build_toc([entry("One", "a.xhtml"), entry("Two", "b.xhtml", "s2"),
                         entry("Three", "c.xhtml", "nope")], self.builder)
        self.assertTrue(all(0 <= item["b"] < len(self.builder.blocks) for item in toc))


class SynthesisTests(unittest.TestCase):
    def test_no_contents_uses_headings(self):
        builder = book(("a", "", [h(1, "Book"), p(), h(2, "One"), p(), h(4, "Aside"), p(),
                                  h(2, "Two"), p()]))
        self.assertEqual(titles(build_toc([], builder)),
                         [("Book", 0, 0), ("One", 1, 2), ("Aside", 2, 4), ("Two", 1, 6)])

    def test_thin_contents_lose_to_headings(self):
        builder = book(("a", "", [h(2, "One"), p(), h(2, "Two"), p(), h(2, "Three"), p()]))
        toc = build_toc([entry("Start", "a")], builder)
        self.assertEqual([item["t"] for item in toc], ["One", "Two", "Three"])

    def test_thin_contents_are_kept_when_nothing_is_better(self):
        builder = book(("a", "", [h(1, "Only"), p(), p()]))
        self.assertEqual(titles(build_toc([entry("Start", "a")], builder)), [("Start", 0, 0)])

    def test_entries_sharing_a_target_count_once(self):
        builder = book(("a", "", [h(2, "One"), p(), h(2, "Two"), p(), h(2, "Three"), p()]))
        toc = build_toc([entry("x", "a"), entry("y", "a"), entry("z", "a")], builder)
        self.assertEqual([item["t"] for item in toc], ["One", "Two", "Three"])

    def test_mostly_dead_contents_are_replaced(self):
        builder = book(*[("c%d" % number, "", [h(1, "Chapter %d" % number), p()]) for number in range(8)])
        declared = [entry("Dead %d" % number, "old%d" % number) for number in range(8)]
        declared += [entry("Live %d" % number, "c%d" % number) for number in range(4)]
        toc = build_toc(declared, builder)
        self.assertEqual([item["t"] for item in toc], ["Chapter %d" % number for number in range(8)])

    def test_mostly_dead_contents_survive_without_an_alternative(self):
        builder = book(("a", "", [p(), p()]), ("b", "", [p()]))
        toc = build_toc([entry("Dead", "x"), entry("Dead", "y"), entry("Live", "b")], builder)
        self.assertEqual(titles(toc), [("Live", 0, 2)])

    def test_sections_titled_by_their_documents(self):
        builder = book(("a", "Preface", [p()]), ("b", "Chapter the First", [p()]),
                       ("c", "Chapter the Second", [p()]), ("d", "Unknown", [p()]))
        self.assertEqual(titles(build_toc([], builder)),
                         [("Preface", 0, 0), ("Chapter the First", 0, 1), ("Chapter the Second", 0, 2)])

    def test_export_names_are_not_titles(self):
        builder = book(*[("c%d" % number, "MyBook-ebook-v3_07.31-%d" % number, [p("Body text here."), p()])
                         for number in range(4)])
        self.assertEqual([item["t"] for item in build_toc([], builder)],
                         ["Section 1", "Section 2", "Section 3", "Section 4"])

    def test_shared_document_titles_are_not_used(self):
        builder = book(*[("c%d" % number, "The Book", [p("Paragraph one of a chapter."), p()])
                         for number in range(4)])
        toc = build_toc([], builder)
        self.assertEqual([item["t"] for item in toc], ["Section 1", "Section 2", "Section 3", "Section 4"])
        self.assertEqual([item["b"] for item in toc], [0, 2, 4, 6])

    def test_unstructured_sections_use_a_short_first_line(self):
        builder = book(("a", "", [p("CHAPTER I"), p()]), ("b", "", [p("CHAPTER II"), p()]),
                       ("c", "", [p("It was the best of times, it was the worst of times, it was the age."), p()]))
        self.assertEqual([item["t"] for item in build_toc([], builder)],
                         ["CHAPTER I", "CHAPTER II", "Section 3"])

    def test_first_line_may_end_a_numbered_title_with_a_full_stop(self):
        builder = book(("a", "", [p("CHAPTER I."), p()]), ("b", "", [p("Chapter 2."), p()]),
                       ("c", "", [p("He left."), p()]))
        self.assertEqual([item["t"] for item in build_toc([], builder)],
                         ["CHAPTER I.", "Chapter 2.", "Section 3"])

    def test_picture_only_sections_are_not_numbered(self):
        cover = {"k": "img", "src": "/c.jpg", "w": 600, "h": 800, "alt": ""}
        builder = book(("cover", "", [cover]), ("a", "", [p("PART ONE"), p()]),
                       ("b", "", [p("PART TWO"), p()]), ("c", "", [p("PART THREE"), p()]))
        self.assertEqual(titles(build_toc([], builder)),
                         [("PART ONE", 0, 1), ("PART TWO", 0, 3), ("PART THREE", 0, 5)])

    def test_sections_beat_sparse_headings(self):
        builder = book(("a", "Front", [h(1, "The Book"), p()]), ("b", "First", [p()]),
                       ("c", "Second", [p()]), ("d", "Third", [p()]))
        self.assertEqual([item["t"] for item in build_toc([], builder)],
                         ["The Book", "First", "Second", "Third"])

    def test_heading_like_paragraphs_are_a_last_resort(self):
        def chapter(number):
            return [{"k": "p", "f": 1, "t": "<b>Chapter %d</b>" % number}, p(), p()]

        builder = BookBuilder()
        builder.begin_section("a")
        for number in range(1, 5):
            for number, block in enumerate(chapter(number)):
                builder.add(block, emphatic=number == 0)
        builder.finish()
        self.assertEqual(titles(build_toc([], builder)),
                         [("Chapter 1", 0, 0), ("Chapter 2", 0, 3), ("Chapter 3", 0, 6), ("Chapter 4", 0, 9)])

    def test_heading_like_paragraphs_beat_structure_that_is_too_thin(self):
        builder = BookBuilder()
        for name, title in (("a", "The Book"), ("b", "Document Outline")):
            builder.begin_section(name)
            builder.section_title(title)
            for number in range(3):
                builder.add({"k": "p", "f": 1, "t": "<b>Chapter %s%d</b>" % (name, number)}, emphatic=True)
                builder.add(p())
        declared = [entry("Publisher", "a")]
        self.assertEqual([item["t"] for item in build_toc(declared, builder)],
                         ["Chapter a0", "Chapter a1", "Chapter a2", "Chapter b0", "Chapter b1", "Chapter b2"])

    def test_thin_structure_is_still_better_than_less(self):
        builder = book(("a", "The Book", [p(), p()]), ("b", "Notes", [p()]))
        self.assertEqual([item["t"] for item in build_toc([entry("Publisher", "a")], builder)],
                         ["The Book", "Notes"])

    def test_contents_can_be_built_before_finish(self):
        builder = BookBuilder()
        for number in range(4):
            builder.add({"k": "p", "f": 1, "t": "<b>Chapter %d</b>" % number}, emphatic=True)
            builder.add(p())
        self.assertEqual(len(build_toc([], builder)), 4)

    def test_real_headings_beat_heading_like_paragraphs(self):
        builder = BookBuilder()
        for number in range(3):
            builder.add(h(2, "Real %d" % number))
            builder.add({"k": "p", "f": 1, "t": "<b>Sidebar</b>"}, emphatic=True)
            builder.add(p())
        builder.finish()
        self.assertEqual([item["t"] for item in build_toc([], builder)], ["Real 0", "Real 1", "Real 2"])

    def test_too_many_headings_drop_the_deepest_levels(self):
        builder = BookBuilder()
        for chapter in range(40):
            builder.add(h(1, "Chapter %d" % chapter))
            for verse in range(50):
                builder.add(h(3, "Verse %d" % verse))
        builder.finish()
        toc = build_toc([], builder)
        self.assertEqual(len(toc), 40)
        self.assertEqual({item["d"] for item in toc}, {0})

    def test_empty_book(self):
        builder = BookBuilder()
        builder.finish()
        self.assertEqual(build_toc([], builder), [])
        self.assertEqual(build_toc([entry("Gone", "x")], builder), [])

    def test_single_document_without_structure(self):
        builder = book(("a", "", [p(), p()]))
        self.assertEqual(build_toc([], builder), [])


def bible(*books, front=()):
    """A builder of chapter files `(book, chapters, title pattern)` and the entries listing the books."""
    chapters = list(front)
    declared = [entry(title, name) for name, title, _ in front]
    for name, count, pattern in books:
        declared.append(entry(name, "%s-1" % name))
        for number in range(1, count + 1):
            chapters.append(("%s-%d" % (name, number), pattern.replace("#", str(number)), [p(), p()]))
    return book(*chapters), declared


class RefineTests(unittest.TestCase):
    def test_coarse_entries_get_their_documents_as_children(self):
        builder, declared = bible(("Genesis", 3, "Genesis # KJV"), ("Exodus", 2, "Exodus # KJV"),
                                  ("Psalms", 4, "Psalm # KJV"), front=[("toc", "Contents", [p()])])
        self.assertEqual(titles(build_toc(declared, builder, "King James Bible")), [
            ("Contents", 0, 0),
            ("Genesis", 0, 1), ("Genesis 1", 1, 1), ("Genesis 2", 1, 3), ("Genesis 3", 1, 5),
            ("Exodus", 0, 7), ("Exodus 1", 1, 7), ("Exodus 2", 1, 9),
            ("Psalms", 0, 11), ("Psalm 1", 1, 11), ("Psalm 2", 1, 13), ("Psalm 3", 1, 15),
            ("Psalm 4", 1, 17)])

    def test_words_every_child_ends_with_are_dropped(self):
        from reader.toc import _without_shared_ending
        self.assertEqual(_without_shared_ending(["Genesis 1 KJV", "Genesis 2 KJV"]), ["Genesis 1", "Genesis 2"])
        self.assertEqual(_without_shared_ending(["Psalm 1 - King James", "Psalm 2 - King James"]),
                         ["Psalm 1", "Psalm 2"])
        # Nothing shared, nothing left, or no longer telling the children apart.
        self.assertEqual(_without_shared_ending(["Part One", "Part Two"]), ["Part One", "Part Two"])
        self.assertEqual(_without_shared_ending(["The End", "The End"]), ["The End", "The End"])
        self.assertEqual(_without_shared_ending(["Notes", "End Notes"]), ["Notes", "End Notes"])
        self.assertEqual(_without_shared_ending(["One (cont.) KJV", "Two (cont.) KJV"]), ["One", "Two"])

    def test_an_only_child_would_merely_repeat_its_parent(self):
        builder, declared = bible(("Obadiah", 1, "Obadiah # KJV"), ("Jonah", 4, "Jonah #"),
                                  ("Jude", 1, "Jude #"), ("Micah", 2, "Micah #"))
        found = titles(build_toc(declared, builder))
        self.assertEqual([title for title, depth, _ in found if depth == 0],
                         ["Obadiah", "Jonah", "Jude", "Micah"])
        self.assertEqual([title for title, depth, _ in found if depth == 1],
                         ["Jonah 1", "Jonah 2", "Jonah 3", "Jonah 4", "Micah 1", "Micah 2"])

    def test_untitled_documents_are_titled_by_the_heading_they_open_with(self):
        chapters = []
        for name in ("A", "B", "C"):
            chapters.append((name + "1", "", [h(2, name + " one"), p()]))
            chapters.append((name + "2", "Unknown", [{"k": "img", "src": "/x.png", "w": 9, "h": 9, "alt": ""},
                                                     h(2, name + " two"), p()]))
            chapters.append((name + "3", "", [p(), h(2, name + " later"), p()]))
        declared = [entry(name, name + "1") for name in ("A", "B", "C")]
        found = titles(build_toc(declared, book(*chapters)))
        self.assertEqual(found[:3], [("A", 0, 0), ("A one", 1, 0), ("A two", 1, 2)])
        self.assertEqual(len(found), 9)

    def test_split_files_add_nothing(self):
        for pattern in ("", "The Book", "Unknown", "book_split_00#", "#", "Genesis", "My Bible"):
            with self.subTest(title=pattern):
                builder, declared = bible(("Genesis", 5, pattern), ("Exodus", 5, pattern),
                                          ("Numbers", 5, pattern))
                found = build_toc(declared, builder, "My Bible")
                self.assertEqual([item["t"] for item in found], ["Genesis", "Exodus", "Numbers"])

    def test_titles_equal_to_the_entry_or_the_book_are_not_children(self):
        chapters = [("a1", "Part One", [p()]), ("a2", "Alpha", [p()]), ("a3", "Beta", [p()]),
                    ("b1", "The Book", [p()]), ("b2", "Gamma", [p()]), ("b3", "DELTA", [p()]),
                    ("c1", "Delta", [p()]), ("c2", "Epsilon", [p()]), ("c3", "Zeta", [p()])]
        declared = [entry("Part One", "a1"), entry("Part Two", "b1"), entry("Part Three", "c1")]
        self.assertEqual(titles(build_toc(declared, book(*chapters), "the book")), [
            ("Part One", 0, 0), ("Alpha", 1, 1), ("Beta", 1, 2),
            ("Part Two", 0, 3),
            ("Part Three", 0, 6), ("Epsilon", 1, 7), ("Zeta", 1, 8)])

    def test_contents_that_list_most_documents_are_left_alone(self):
        chapters = [("cover", "Cover", [p()]), ("title", "Title Page", [p()]), ("copy", "Copyright", [p()])]
        chapters += [("c%d" % number, "Chapter %d" % number, [p()]) for number in range(1, 5)]
        declared = [entry("Cover", "cover")] + [entry("Chapter %d" % n, "c%d" % n) for n in range(1, 5)]
        self.assertEqual([item["t"] for item in build_toc(declared, book(*chapters))],
                         ["Cover", "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"])

    def test_entries_with_children_of_their_own_are_left_alone(self):
        builder, declared = bible(("Genesis", 4, "Genesis #"), ("Exodus", 4, "Exodus #"),
                                  ("Numbers", 4, "Numbers #"))
        declared.insert(1, entry("The Fall", "Genesis-3", depth=1))
        found = titles(build_toc(declared, builder))
        self.assertEqual(found[:5], [("Genesis", 0, 0), ("The Fall", 1, 4), ("Genesis 3", 2, 4),
                                     ("Genesis 4", 2, 6), ("Exodus", 0, 8)])

    def test_entries_sharing_a_place_are_refined_once(self):
        builder, declared = bible(("Genesis", 3, "Genesis #"), ("Exodus", 3, "Exodus #"),
                                  ("Numbers", 3, "Numbers #"))
        declared.insert(0, entry("Old Testament", "Genesis-1"))
        found = titles(build_toc(declared, builder))
        self.assertEqual(found[:6], [("Old Testament", 0, 0), ("Genesis", 0, 0), ("Genesis 1", 1, 0),
                                     ("Genesis 2", 1, 2), ("Genesis 3", 1, 4), ("Exodus", 0, 6)])
        self.assertEqual(len(found), 13)

    def test_synthesised_contents_are_not_refined(self):
        builder, _ = bible(("Genesis", 3, "Genesis #"), ("Exodus", 3, "Exodus #"))
        self.assertEqual({item["d"] for item in build_toc([], builder)}, {0})


class OpeningTests(unittest.TestCase):
    def chapters(self, opening):
        return book(("a", "", opening + [p()] * 30 + [("two", h(1, "CHAPTER 2"))] + [p()] * 40
                     + [("three", h(1, "CHAPTER 3"))] + [p()] * 40), ("b", "", [h(1, "LAST"), p()]))

    def declared(self):
        return [entry("CHAPTER 2", "a", "two"), entry("CHAPTER 3", "a", "three"), entry("LAST", "b")]

    def test_unlisted_opening_is_titled_by_its_first_heading(self):
        builder = self.chapters([p(), h(2, "Chapter 1"), p(), h(2, "A later heading")])
        self.assertEqual(titles(build_toc(self.declared(), builder, "The Book"))[:2],
                         [("Chapter 1", 0, 0), ("CHAPTER 2", 0, 34)])

    def test_a_heading_like_paragraph_will_do(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "<b>The Long Road: A Journey</b>", "f": 1}, emphatic=True)
        for _ in range(30):
            builder.add(p())
        for name in ("two", "three", "four"):
            builder.anchor(name)
            builder.add(h(1, name))
            builder.add(p())
        declared = [entry(name, "a", name) for name in ("two", "three", "four")]
        self.assertEqual(titles(build_toc(declared, builder, "The Long Road"))[:2],
                         [("The Long Road: A Journey", 0, 0), ("two", 0, 31)])

    def test_then_the_book_title_then_a_word(self):
        builder = self.chapters([])
        self.assertEqual(build_toc(self.declared(), builder, " The\u00ad Book ")[0],
                         {"t": "The Book", "d": 0, "b": 0})
        self.assertEqual(build_toc(self.declared(), builder)[0], {"t": "Beginning", "d": 0, "b": 0})

    def test_needs_more_than_twenty_blocks_and_a_twentieth_of_the_book(self):
        near = book(("a", "", [p()] * 20 + [("two", h(1, "Two"))] + [p()] * 9), ("b", "", [h(1, "B"), p()]),
                    ("c", "", [h(1, "C"), p()]))
        declared = [entry("Two", "a", "two"), entry("B", "b"), entry("C", "c")]
        self.assertEqual(titles(build_toc(declared, near, "T"))[0], ("Two", 0, 20))
        far = book(("a", "", [p()] * 21 + [("two", h(1, "Two"))] + [p()] * 9), ("b", "", [h(1, "B"), p()]),
                   ("c", "", [h(1, "C"), p()]))
        self.assertEqual(titles(build_toc(declared, far, "T"))[:2], [("T", 0, 0), ("Two", 0, 21)])
        long = book(("a", "", [p()] * 21 + [("two", h(1, "Two"))] + [p()] * 500), ("b", "", [h(1, "B"), p()]),
                    ("c", "", [h(1, "C"), p()]))
        self.assertEqual(titles(build_toc(declared, long, "T"))[0], ("Two", 0, 21))

    def test_the_first_place_named_counts_not_the_first_entry(self):
        builder = self.chapters([])
        declared = [entry("LAST", "b"), entry("Start", "a"), entry("CHAPTER 2", "a", "two")]
        self.assertEqual(titles(build_toc(declared, builder, "T"))[0][0], "LAST")

    def test_where_titles_are_letter_spaced_bold_lines_are_not_chapters(self):
        # A scanned book: bold lines on its copyright page, then chapters
        # whose titles the scan left as capitals set one by one.
        places = ("RIVER", "BRIDGE", "HARBOUR", "STORM", "LETTER", "GARDEN", "WINTER", "SUMMER",
                  "MOUNTAIN", "VALLEY", "FOREST", "ISLAND")
        spaced = [" ".join("THE" + place) for place in places]
        body = {"k": "p", "t": "Body text. " * 20}

        def scanned(bold):
            builder = BookBuilder()
            builder.begin_section("a")
            for line in bold:
                builder.add({"k": "p", "t": "<b>%s</b>" % line, "f": 1}, emphatic=True)
                builder.add(dict(body))
            for line in spaced:
                builder.add({"k": "p", "t": line})
                builder.add(dict(body))
            return [item["t"] for item in build_toc([], builder)]

        self.assertEqual(scanned(("ALSO BY", "First Edition", "Cover design")), spaced)
        # Fewer than ten such titles prove nothing about the book's style.
        del spaced[9:]
        self.assertEqual(scanned(("ALSO BY",)), ["ALSO BY"] + spaced)

    def test_synthesised_contents_get_an_opening_too(self):
        builder = self.chapters([])
        self.assertEqual(titles(build_toc([], builder, "The Book"))[:2],
                         [("The Book", 0, 0), ("CHAPTER 2", 0, 30)])


if __name__ == "__main__":
    unittest.main()
