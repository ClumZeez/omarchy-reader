"""Builds small EPUBs for the tests, with a knob for every quirk they exercise.

`make(directory)` writes a well-formed two-chapter EPUB 2; each keyword
argument bends one part of it out of shape.
"""

import os
import posixpath
import struct
import zipfile
import zlib

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>%s</rootfiles>
</container>"""

DOCUMENT_TYPE = "application/xhtml+xml"
NCX_TYPE = "application/x-dtbncx+xml"
AES = "http://www.w3.org/2001/04/xmlenc#aes128-cbc"
IDPF_OBFUSCATION = "http://www.idpf.org/2008/embedding"
ADOBE_OBFUSCATION = "http://ns.adobe.com/pdf/enc#RC"

DEFAULT_DOCS = (
    ("ch1.xhtml", '<h1 id="h">One</h1><p>alpha</p>'),
    ("ch2.xhtml", "<h1>Two</h1><p>beta</p>"),
)


def jpeg(width, height):
    """The smallest thing that is recognisably a JPEG of this size."""
    return (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width)
            + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01" + b"\xff\xd9" + b"\x00" * 64)


def png(width, height, shade=120):
    """A real, solid grey PNG."""
    rows = (b"\x00" + bytes([shade]) * width) * height

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def xhtml(body, title="", head=""):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>%s</title>%s</head>'
            "<body>%s</body></html>" % (title, head, body))


def ncx(points):
    """An NCX from `(label, src)` or `(label, src, children)` tuples; None leaves a part out."""
    def render(items):
        out = []
        for item in items:
            label, src = item[0], item[1]
            inner = render(item[2]) if len(item) > 2 else ""
            out.append("<navPoint>%s%s%s</navPoint>" % (
                "" if label is None else "<navLabel><text>%s</text></navLabel>" % label,
                "" if src is None else '<content src="%s"/>' % src, inner))
        return "".join(out)

    return ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            "<docTitle><text>T</text></docTitle><navMap>%s</navMap></ncx>" % render(points))


def nav(inner, kind="toc"):
    return xhtml('<nav epub:type="%s">%s</nav>' % (kind, inner))


def encryption(*entries):
    """META-INF/encryption.xml from `(algorithm, member)` pairs."""
    data = "".join(
        '<enc:EncryptedData><enc:EncryptionMethod Algorithm="%s"/>'
        '<enc:CipherData><enc:CipherReference URI="%s"/></enc:CipherData></enc:EncryptedData>'
        % entry for entry in entries)
    return ('<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">%s</encryption>' % data)


def write_zip(path, members):
    """Write `{name: bytes or str}` as a zip, in order."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
    return path


def make(directory, name="book.epub", *,
         title="T", creators=("A B",), language="en", version="2.0",
         docs=DEFAULT_DOCS, raw_docs=False,
         opf_path="OEBPS/content.opf", opf=None,
         container=None, mimetype=True,
         metadata=None, metadata_extra="",
         manifest=None, manifest_extra="",
         spine=None, spine_attrs=None, guide="",
         ncx_points=None, ncx_text=None, ncx_href="toc.ncx", ncx_type=NCX_TYPE, ncx_id="ncx",
         nav_text=None, nav_href="nav.xhtml", nav_properties="nav",
         files=None, root_files=None, prefix=""):
    """Write an EPUB and return its path.

    docs            `(href, body)` pairs, relative to the package file; ids are c1, c2, …
    raw_docs        the bodies are whole files, not to be wrapped in XHTML
    opf             the whole package file, replacing everything generated
    container       None: a correct container.xml; False: none; a string: the rootfile elements
    metadata        replaces the generated Dublin Core elements; metadata_extra adds to them
    manifest        replaces the generated items; manifest_extra adds to them
    spine           the itemref elements as text, or a list of idrefs
    spine_attrs     attributes of <spine>; by default toc="ncx" when there is an NCX
    ncx_points      see ncx(); None lists the docs by their number names; False leaves the NCX out
    ncx_text        the whole NCX file
    nav_text        a whole nav document, added to the manifest with nav_properties
    files           extra members next to the package file; root_files are relative to the zip root
    prefix          a folder everything is put inside
    """
    folder = os.path.dirname(opf_path)
    numbers = ("One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten")
    has_ncx = ncx_text is not None or ncx_points is not False
    if ncx_text is None and has_ncx:
        if ncx_points is None:
            ncx_points = [(numbers[index % 10], href) for index, (href, _) in enumerate(docs)]
        ncx_text = ncx(ncx_points)

    if metadata is None:
        metadata = "<dc:title>%s</dc:title>%s%s" % (
            title,
            "".join("<dc:creator>%s</dc:creator>" % creator for creator in creators),
            "<dc:language>%s</dc:language>" % language if language is not None else "")
    if manifest is None:
        manifest = "".join('<item id="c%d" href="%s" media-type="%s"/>' % (index, href, DOCUMENT_TYPE)
                           for index, (href, _) in enumerate(docs, 1))
        if has_ncx:
            manifest += '<item id="%s" href="%s" media-type="%s"/>' % (ncx_id, ncx_href, ncx_type)
        if nav_text is not None:
            manifest += '<item id="nav" href="%s" media-type="%s" properties="%s"/>' % (
                nav_href, DOCUMENT_TYPE, nav_properties)
    if spine is None:
        spine = ["c%d" % index for index in range(1, len(docs) + 1)]
    if not isinstance(spine, str):
        spine = "".join('<itemref idref="%s"/>' % idref for idref in spine)
    if spine_attrs is None:
        spine_attrs = ' toc="%s"' % ncx_id if has_ncx else ""
    if opf is None:
        opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<package xmlns="http://www.idpf.org/2007/opf" version="%s" unique-identifier="uid">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
               'xmlns:opf="http://www.idpf.org/2007/opf">'
               '<dc:identifier id="uid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>'
               "%s%s</metadata><manifest>%s%s</manifest><spine%s>%s</spine>%s</package>"
               % (version, metadata, metadata_extra, manifest, manifest_extra, spine_attrs, spine,
                  "<guide>%s</guide>" % guide if guide else ""))

    members = {}
    if mimetype:
        members["mimetype"] = "application/epub+zip"
    if container is not False:
        if container is None:
            container = '<rootfile full-path="%s" media-type="application/oebps-package+xml"/>' % opf_path
        members["META-INF/container.xml"] = CONTAINER % container
    if opf is not False:
        members[opf_path] = opf

    def beside(href):
        return posix_join(folder, href)

    for href, body in docs:
        members[beside(href)] = body if raw_docs or isinstance(body, bytes) else xhtml(body)
    if ncx_text is not None:
        members[beside(ncx_href)] = ncx_text
    if nav_text is not None:
        members[beside(nav_href)] = nav_text
    for href, data in (files or {}).items():
        members[beside(href)] = data
    for href, data in (root_files or {}).items():
        members[href] = data
    if prefix:
        members = {prefix + "/" + member: data for member, data in members.items()}
    return write_zip(os.path.join(directory, name), members)


def posix_join(folder, href):
    return posixpath.normpath(posixpath.join(folder, href))
