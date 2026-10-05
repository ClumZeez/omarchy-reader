"""Contents: resolve the declared table of contents, judge it, and synthesise
one from the book's headings or sections when it is missing or poor."""

from __future__ import annotations

from bisect import bisect_left

from .blocks import TEXT_KINDS, BookBuilder, clean, ends_like_prose, letter_spaced, plain_text

MIN_ENTRIES = 3
SPACED_TITLES = 10
TITLE_LIMIT = 120
MAX_SYNTHESISED = 1500
FIRST_LINE_LIMIT = 60
OPENING_BLOCKS = 20
OPENING_SHARE = 0.05
# Contents are coarse when the book has this many documents for each place they name.
COARSE = 2

# Titles converters write when they have nothing to say.
_PLACEHOLDERS = frozenset(("", "start", "unknown", "untitled", "cover", "title", "titlepage",
                           "title page"))


def build_toc(declared: list[dict], builder: BookBuilder, title: str = "") -> list[dict]:
    """The book's contents as `[{"t", "d", "b"}]` in listing order.

    `declared` is the book's own table of contents, `[{"t", "d", "name",
    "fragment"}]`. It is kept unless it has fewer than three usable entries
    while the headings or sections offer more, or most of it points nowhere.
    `title` is the book's title.
    """
    # Heading candidates and final link targets exist only once the book is finished.
    builder.finish()
    title = _title(title)
    return _with_opening(_normalise(_choose(declared, builder, title)), builder, title)


def _choose(declared: list[dict], builder: BookBuilder, title: str) -> list[dict]:
    """The declared contents, or the best that can be made of the book's structure."""
    entries, dead, targeted = _resolve(declared, builder)
    usable = _distinct(entries)
    mostly_dead = dead * 2 > targeted
    if usable >= MIN_ENTRIES and not mostly_dead:
        return _refine(entries, builder, title)

    def better(candidate: list[dict]) -> bool:
        count = _distinct(candidate)
        return count > usable if usable < MIN_ENTRIES else count >= MIN_ENTRIES

    headings = _from_headings(builder, 6)
    sections = _from_sections(builder, numbered=False)
    structure = headings if _distinct(headings) >= _distinct(sections) else sections
    # Numbering the documents says nothing about them; it only earns its place
    # when it makes a book navigable that otherwise would not be.
    candidates = (structure, _from_headings(builder, 7), _from_sections(builder, numbered=True))
    for candidate in candidates:
        if _distinct(candidate) >= MIN_ENTRIES and better(candidate):
            return candidate
    # Nothing makes the book navigable: settle for whatever says more than it declares.
    for candidate in candidates[:2]:
        if better(candidate):
            return candidate
    return entries


def _distinct(entries: list[dict]) -> int:
    return len({entry["b"] for entry in entries})


def _title(text: object) -> str:
    title = plain_text(clean(text if isinstance(text, str) else ""))
    if len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return title


def _resolve(declared: list[dict], builder: BookBuilder) -> tuple[list[dict], int, int]:
    """Declared entries with block targets, plus how many of them were dead."""
    headings = {index: text for index, _, text in builder.headings}
    entries: list[dict] = []
    dead = targeted = 0
    for item in declared:
        name = item.get("name") or ""
        fragment = item.get("fragment") or ""
        depth = item.get("d")
        target = None
        if name:
            targeted += 1
            target = builder.anchor_index(name, fragment)
            if target is None and fragment:
                target = builder.anchor_index(name)
            if target is None:
                dead += 1
                continue
        entries.append({"t": _title(item.get("t")), "d": depth if isinstance(depth, int) else 0,
                        "b": target})
    # A label without a target of its own (a grouping row) opens at its first child.
    following: dict | None = None
    for entry in reversed(entries):
        if entry["b"] is None and following is not None and following["d"] > entry["d"]:
            entry["b"] = following["b"]
        if entry["b"] is not None:
            following = entry
    resolved = []
    for entry in entries:
        if entry["b"] is None:
            continue
        if not entry["t"]:
            entry["t"] = _title(headings.get(entry["b"], ""))
        if entry["t"]:
            resolved.append(entry)
    return resolved, dead, targeted


def _refine(entries: list[dict], builder: BookBuilder, book: str) -> list[dict]:
    """Give the entries of coarse contents the titled documents they span as children.

    A Bible lists its 66 books over more than a thousand chapter files, each
    titled "Genesis 1" and so on. Only entries without children of their own
    are refined, and only by documents whose title is theirs alone: files a
    converter split share one title or have none, and add nothing.
    """
    records = builder.section_records()
    if len(records) < COARSE * _distinct(entries):
        return entries
    starts = [start for _, start, _ in records]
    own = _own_titles(builder, records)
    counts: dict[str, int] = {}
    for text in own:
        counts[text.casefold()] = counts.get(text.casefold(), 0) + 1
    targets = sorted({entry["b"] for entry in entries})
    ends = dict(zip(targets, targets[1:] + [len(builder.blocks)]))
    last = {entry["b"]: index for index, entry in enumerate(entries)}
    refined: list[dict] = []
    for index, entry in enumerate(entries):
        refined.append(entry)
        parent = index + 1 < len(entries) and entries[index + 1]["d"] > entry["d"]
        if parent or last[entry["b"]] != index:
            continue
        span = range(bisect_left(starts, entry["b"]), bisect_left(starts, ends[entry["b"]]))
        unwanted = ("", entry["t"].casefold(), book.casefold())
        children = [{"t": own[number], "d": entry["d"] + 1, "b": starts[number]}
                    for number in span
                    if own[number].casefold() not in unwanted
                    and counts[own[number].casefold()] == 1]
        if len(children) > 1:
            for child, text in zip(children, _without_shared_ending([child["t"] for child in children])):
                child["t"] = text
            refined.extend(children)
    return refined


def _without_shared_ending(titles: list[str]) -> list[str]:
    """Drop the words every title ends with.

    Chapter files titled "Genesis 1 KJV", "Genesis 2 KJV" carry the name of
    the edition; under their book's entry only "Genesis 1" says anything.
    """
    words = [title.split(" ") for title in titles]
    shared = 0
    while (all(len(parts) > shared + 1 for parts in words)
           and len({parts[-1 - shared].casefold() for parts in words}) == 1):
        shared += 1
    shorter = [" ".join(parts[:len(parts) - shared]) for parts in words]
    if not shared or len(set(shorter)) != len(shorter):
        return titles
    return shorter


def _own_titles(builder: BookBuilder, records: list[tuple[str, int, str]]) -> list[str]:
    """What each section calls itself: its `<title>`, else the heading it opens with."""
    opening: dict[int, str] = {}
    position = 0
    for index, level, text in builder.headings:
        if level > 6:
            continue
        while position < len(records) and records[position][1] <= index:
            position += 1
        start = records[position - 1][1] if position else index + 1
        # Pictures may come before the heading a document opens with; text may not.
        if start <= index and all(block["k"] == "img" for block in builder.blocks[start:index]):
            opening.setdefault(start, text)
    return [_title(title if _is_title(title) else opening.get(start, ""))
            for _, start, title in records]


def _with_opening(entries: list[dict], builder: BookBuilder, book: str) -> list[dict]:
    """Put an entry for the start of the book before contents that begin well into it."""
    first = min((entry["b"] for entry in entries), default=0)
    if first <= OPENING_BLOCKS or first <= OPENING_SHARE * len(builder.blocks):
        return entries
    heading = next((_title(text) for index, _, text in builder.headings
                    if index < first and _title(text)), "")
    return [{"t": heading or book or "Beginning", "d": 0, "b": 0}] + entries


def _from_headings(builder: BookBuilder, deepest: int) -> list[dict]:
    """One entry per heading down to level `deepest`, unused levels collapsed."""
    found = [(index, level, _title(text)) for index, level, text in builder.headings
             if level <= deepest]
    found = [item for item in found if item[2]]
    likes = [item for item in found if item[1] > 6]
    spaced = [item for item in likes if letter_spaced(item[2])]
    if len(spaced) >= SPACED_TITLES and len(spaced) * 5 >= len(likes) * 3:
        # A scanned book sets its chapter titles one way. Where most of the
        # title-like lines are letter-spaced, the bold lines of its
        # copyright page are not chapters.
        found = [item for item in found if item[1] <= 6 or letter_spaced(item[2])]
    levels = sorted({level for _, level, _ in found})
    while len(found) > MAX_SYNTHESISED and len(levels) > 1:
        levels.pop()
        found = [item for item in found if item[1] in levels]
    rank = {level: depth for depth, level in enumerate(levels)}
    return [{"t": text, "d": rank[level], "b": index} for index, level, text in found]


def _from_sections(builder: BookBuilder, numbered: bool) -> list[dict]:
    """One entry per source document that can be given a title.

    The title is the document's first heading, else its own `<title>` when
    that is not shared with other documents (the book title, usually). With
    `numbered`, documents without either get their short first line or a
    number, so that even an unstructured book can be navigated.
    """
    records = builder.section_records()
    shared: dict[str, int] = {}
    for _, _, title in records:
        shared[title] = shared.get(title, 0) + 1
    first_heading: dict[int, str] = {}
    starts = [start for _, start, _ in records]
    position = 0
    for index, level, text in builder.headings:
        if level > 6 or not text:
            continue
        while position + 1 < len(starts) and starts[position + 1] <= index:
            position += 1
        if starts and starts[position] <= index:
            first_heading.setdefault(starts[position], text)
    entries = []
    ends = starts[1:] + [len(builder.blocks)]
    for number, ((_, start, title), end) in enumerate(zip(records, ends), 1):
        text = first_heading.get(start, "")
        if not text and shared[title] <= 2 and _is_title(title):
            text = title
        if not text and numbered and any(block["k"] in TEXT_KINDS
                                         for block in builder.blocks[start:end]):
            text = _first_line(builder.blocks[start]) or "Section %d" % number
        if text:
            entries.append({"t": _title(text), "d": 0, "b": start})
    return entries


def _is_title(title: str) -> bool:
    """Whether a document's `<title>` was written for readers.

    Converters leave placeholders ("Unknown") and export names
    ("MyBook-v3_07-12") there.
    """
    if title.lower() in _PLACEHOLDERS:
        return False
    exported = " " not in title and any(char.isdigit() for char in title) \
        and any(char in "_-." for char in title)
    return not exported


def _first_line(block: dict) -> str:
    if block.get("k") not in TEXT_KINDS:
        return ""
    text = plain_text(block["t"], block.get("f", 0))
    return text if len(text) <= FIRST_LINE_LIMIT and not ends_like_prose(text) else ""


def _normalise(entries: list[dict]) -> list[dict]:
    """Merge consecutive duplicates; make depth start at 0 and rise one step at a time."""
    merged: list[dict] = []
    for entry in entries:
        if merged and merged[-1]["t"] == entry["t"] and merged[-1]["b"] == entry["b"]:
            continue
        merged.append({"t": entry["t"], "d": entry["d"], "b": entry["b"]})
    open_depths: list[int] = []
    for entry in merged:
        while open_depths and open_depths[-1] >= entry["d"]:
            open_depths.pop()
        open_depths.append(entry["d"])
        entry["d"] = len(open_depths) - 1
    return merged
