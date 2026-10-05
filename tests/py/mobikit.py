"""Builds small Kindle files for the tests: a Palm database, headers, text and indexes.

`mobi6(...)` and `kf8(...)` each return the records of one book, numbered from
its own header, so the two can be joined into a file that holds both;
`pdb(records)` wraps records into the bytes of a file.
"""

import struct

NULL = 0xFFFFFFFF
RECORD = 4096
HUFFMAN = 17480
END = b"\xe9\x8e\r\n"

NCX_TAGS = ((1, 1, 1), (2, 1, 2), (3, 1, 4), (4, 1, 8), (21, 1, 16), (22, 1, 32), (23, 1, 64),
            (6, 2, 128))
SKELETON_TAGS = ((1, 1, 3), (6, 2, 12))
FRAGMENT_TAGS = ((2, 1, 1), (3, 1, 2), (4, 1, 4), (6, 2, 8))
GUIDE_TAGS = ((1, 1, 1), (6, 2, 2))


def pdb(records, kind=b"BOOKMOBI", name=b"Test_Book", count=None):
    """A Palm database holding `records`; `count` overrides the declared number of them."""
    offset = 78 + 8 * len(records) + 2
    directory = b""
    for number, record in enumerate(records):
        directory += struct.pack(">LL", offset, 2 * number)
        offset += len(record)
    head = name[:31].ljust(32, b"\x00") + bytes(28) + kind + bytes(8)
    head += struct.pack(">H", len(records) if count is None else count)
    return head + directory + b"\x00\x00" + b"".join(records)


def palmdoc(data):
    """PalmDOC-compressed `data`, using literals only."""
    out = bytearray()
    run = bytearray()
    for byte in data:
        if byte == 0 or 0x09 <= byte <= 0x7F:
            if run:
                out += bytes([len(run)]) + run
                run.clear()
            out.append(byte)
        else:
            run.append(byte)
            if len(run) == 8:
                out += b"\x08" + run
                run.clear()
    if run:
        out += bytes([len(run)]) + run
    return bytes(out)


def huffman_tables(phrases=None):
    """A HUFF and a CDIC record in which every byte is its own 8-bit code.

    `phrases` maps a byte to what it stands for instead; a `str` value is
    stored as a phrase that is itself coded and must be unpacked again.
    """
    huff = b"HUFF" + struct.pack(">LLL", 24, 24, 24 + 1024) + bytes(8)
    huff += struct.pack(">L", (255 << 8) | 0x80 | 8) * 256 + bytes(256)
    offsets = b""
    body = b""
    for number in range(256):
        value = (phrases or {}).get(255 - number, bytes([255 - number]))
        final = not isinstance(value, str)
        value = value if final else value.encode()
        offsets += struct.pack(">H", 512 + len(body))
        body += struct.pack(">H", len(value) | (0x8000 if final else 0)) + value
    return [huff, b"CDIC" + struct.pack(">LLL", 16, 256, 8) + offsets + body]


def text_records(text, compression=2, trailers=0):
    """`text` cut into records; `trailers` are the flags of what follows each one's text."""
    records = []
    for start in range(0, len(text), RECORD):
        chunk = text[start:start + RECORD]
        record = palmdoc(chunk) if compression == 2 else chunk
        if trailers & 1:
            record += b"\x00"
        if trailers & 2:
            record += b"\x81"
        records.append(record)
    return records


def header(text_length, records, *, compression=2, encryption=0, codepage=65001, version=6,
           length=232, title="Full Name", exth=(), flags=0, fields=None, locale=9):
    """Record 0: PalmDOC header, MOBI header, EXTH block and the full name.

    `exth` is `[(type, value)]` with int, str or bytes values; `fields` sets
    further 32-bit header fields by offset. A `length` of 0 leaves the MOBI
    header out, as PalmDoc text files do.
    """
    start = struct.pack(">HHLHHHH", compression, 0, text_length, records, RECORD, encryption, 0)
    if not length:
        return start
    block = bytearray(16 + length)
    block[:16] = start
    block[16:20] = b"MOBI"
    values = {0x14: length, 0x18: 2, 0x1C: codepage, 0x20: 7, 0x24: version, 0x5C: locale,
              0x68: version, 0x80: 0x50 if exth else 0x10}
    for offset in (0x28, 0x2C, 0x6C, 0x70, 0xA8, 0xC0, 0xF4, 0xF8, 0xFC, 0x100, 0x104):
        values[offset] = NULL
    values.update(fields or {})
    for offset, value in values.items():
        if offset + 4 <= len(block):
            struct.pack_into(">L", block, offset, value)
    if 0xF4 <= len(block):
        struct.pack_into(">H", block, 0xF2, flags)
    extra = b""
    if exth:
        codec = "utf-8" if codepage == 65001 else "cp1252"
        for kind, value in exth:
            if isinstance(value, int):
                value = struct.pack(">L", value)
            elif isinstance(value, str):
                value = value.encode(codec)
            extra += struct.pack(">LL", kind, 8 + len(value)) + value
        extra = b"EXTH" + struct.pack(">LL", 12 + len(extra), len(exth)) + extra
        extra += bytes(-len(extra) % 4)
    name = title.encode("utf-8" if codepage == 65001 else "cp1252")
    if name:
        struct.pack_into(">LL", block, 0x54, len(block) + len(extra), len(name))
    return bytes(block) + extra + name + bytes(2)


def varint(value):
    out = [value & 0x7F | 0x80]
    value >>= 7
    while value:
        out.insert(0, value & 0x7F)
        value >>= 7
    return bytes(out)


def string_offsets(strings):
    """Where each of an index's strings will lie, which is how entries refer to them."""
    offsets = []
    position = 0
    for string in strings:
        offsets.append(position)
        position += len(varint(len(string))) + len(string)
    return offsets


def index(tags, entries, strings=()):
    """The records of one index: `entries` are `(name, {tag: [values]})`."""
    table = b"".join(bytes((tag, values, mask, 0)) for tag, values, mask in tags)
    table += bytes((0, 0, 0, 1))
    meta = bytearray(192)
    meta[:4] = b"INDX"
    struct.pack_into(">L", meta, 4, 192)
    struct.pack_into(">L", meta, 24, 1)
    struct.pack_into(">L", meta, 28, 65001)
    struct.pack_into(">L", meta, 52, 1 if strings else 0)
    meta += b"TAGX" + struct.pack(">LL", 12 + len(table), 1) + table

    labels = b"".join(varint(len(string)) + string for string in strings)
    body = b""
    starts = []
    for name, found in entries:
        starts.append(192 + len(body))
        control = 0
        sizes = b""
        numbers = b""
        for tag, values, mask in tags:
            if tag not in found:
                continue
            shift = (mask & -mask).bit_length() - 1
            count = (len(found[tag]) // values) << shift
            packed = b"".join(varint(value) for value in found[tag])
            if count == mask and mask & (mask - 1):
                sizes += varint(len(packed))
            control |= count
            numbers += packed
        body += bytes([len(name)]) + name + bytes([control]) + sizes + numbers
    data = bytearray(192)
    data[:4] = b"INDX"
    struct.pack_into(">L", data, 4, 192)
    struct.pack_into(">L", data, 20, 192 + len(body))
    struct.pack_into(">L", data, 24, len(entries))
    data += body + b"IDXT" + b"".join(struct.pack(">H", start) for start in starts)
    return [bytes(meta), bytes(data)] + ([labels] if strings else [])


def mobi6(text, *, compression=2, trailers=0, flags=None, ncx=None, images=(), exth=(),
          tables=None, **options):
    """The records of a MOBI 6 book.

    `ncx` is `[(label, position, depth)]`; `images` are resource records, the
    first of which is resource number 1; `flags` is what the header claims
    about the records' trailers when that should differ from the truth.
    """
    records = text_records(text, compression, trailers)
    fields = dict(options.pop("fields", None) or {})
    after = [b"\x00\x00"]
    if compression == HUFFMAN:
        fields[0x70], fields[0x74] = 1 + len(records) + len(after), 2
        after += tables or huffman_tables()
    if ncx is not None:
        labels = [label.encode() for label, _, _ in ncx]
        entries = [(b"%03d" % number, {1: [position], 3: [offset], 4: [depth]})
                   for number, ((_, position, depth), offset)
                   in enumerate(zip(ncx, string_offsets(labels)))]
        fields[0xF4] = 1 + len(records) + len(after)
        after += index(NCX_TAGS, entries, labels)
    if images:
        fields.setdefault(0x6C, 1 + len(records) + len(after))
    head = header(len(text), len(records), compression=compression,
                  flags=trailers if flags is None else flags, exth=exth, fields=fields, **options)
    return [head] + records + after + list(images)


def kf8(documents, *, flows=(), ncx=None, guide=None, images=(), exth=(), **options):
    """The records of a KF8 book.

    `documents` are `(before, fragments, after)`: a skeleton in two halves and
    the pieces that belong between them. `flows` are stylesheets and SVG
    pages, numbered from 1. `ncx` is `[(label, fragment, offset, depth,
    parent)]` and `guide` `[(kind, fragment, offset)]`, both addressing the
    fragments by their number in the whole book.
    """
    text = b""
    skeletons = []
    fragments = []
    selectors = []
    for number, (before, pieces, after) in enumerate(documents):
        start = len(text)
        skeletons.append((b"SKEL%010d" % number,
                          {1: [len(pieces)], 6: [start, len(before) + len(after)]}))
        text += before + after
        where = start + len(before)
        offset = 0
        for piece in pieces:
            selectors.append(b"P-//*[@aid='%d']" % number)
            fragments.append((b"%010d" % where, {3: [number], 4: [len(fragments)],
                                                 6: [offset, len(piece)]}))
            text += piece
            where += len(piece)
            offset += len(piece)
    spans = [(0, len(text))]
    for flow in flows:
        spans.append((len(text), len(text) + len(flow)))
        text += flow

    records = text_records(text)
    after = [b"\x00\x00"]
    fields = dict(options.pop("fields", None) or {})

    def add(offset, tags, entries, strings=()):
        if fields.setdefault(offset, 1 + len(records) + len(after)) != NULL:
            after.extend(index(tags, entries, strings))

    add(0xFC, SKELETON_TAGS, skeletons)
    for (_, tags), offset in zip(fragments, string_offsets(selectors)):
        tags[2] = [offset]
    add(0xF8, FRAGMENT_TAGS, fragments, selectors)
    if ncx is not None:
        labels = [entry[0].encode() for entry in ncx]
        offsets = string_offsets(labels)
        entries = []
        for number, (_, fragment, offset, depth, parent) in enumerate(ncx):
            tags = {3: [offsets[number]], 4: [depth], 6: [fragment, offset]}
            if parent is not None:
                tags[21] = [parent]
            entries.append((b"%03d" % number, tags))
        add(0xF4, NCX_TAGS, entries, labels)
    if guide is not None:
        add(0x104, GUIDE_TAGS, [(kind.encode(), {6: [fragment, offset]})
                                for kind, fragment, offset in guide])
    if flows:
        fields[0xC0], fields[0xC4] = 1 + len(records) + len(after), len(spans)
        after.append(b"FDST" + struct.pack(">LL", 12, len(spans))
                     + b"".join(struct.pack(">LL", *span) for span in spans))
    if images:
        fields.setdefault(0x6C, 1 + len(records) + len(after))
    head = header(len(text), len(records), version=8, length=264, exth=exth, fields=fields,
                  **options)
    return [head] + records + after + list(images)


def joint(old, new):
    """The records of a file holding a MOBI 6 book and then the KF8 edition of it."""
    return old + [b"BOUNDARY"] + new + [END]
