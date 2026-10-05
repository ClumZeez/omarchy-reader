import support

import os
import struct
import unittest
import zlib

from reader.images import ImageInfo, sniff


def png(width, height, first_chunk=b"IHDR"):
    header = struct.pack(">II5B", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(header)) + first_chunk + header
    return b"\x89PNG\r\n\x1a\n" + chunk + struct.pack(">I", zlib.crc32(chunk[4:])) + b"IDAT"


def segment(marker, payload=b""):
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def frame(marker, width, height):
    return segment(marker, struct.pack(">BHHB", 8, height, width, 3) + bytes(9))


def jpeg(width, height, *, marker=0xC0, before=b""):
    return b"\xff\xd8" + before + frame(marker, width, height) + segment(0xDA, bytes(10)) + b"\x00\xff\xd9"


JFIF = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
# The thumbnail's own frame header sits inside the EXIF segment and must not be taken for the image's.
EXIF = segment(0xE1, b"Exif\x00\x00" + b"\xff\xd8" + frame(0xC0, 160, 120) + b"\xff\xd9")


def gif(width, height, version=b"GIF89a"):
    return version + struct.pack("<HH", width, height) + b"\x80\x00\x00"


def webp(chunk, payload):
    body = b"WEBP" + chunk + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_lossy(width, height):
    return webp(b"VP8 ", b"\x30\x01\x00\x9d\x01\x2a" + struct.pack("<HH", width | 0x4000, height) + bytes(8))


def webp_lossless(width, height):
    return webp(b"VP8L", b"\x2f" + struct.pack("<I", (width - 1) | ((height - 1) << 14)) + bytes(4))


def webp_extended(width, height):
    return webp(b"VP8X", b"\x10\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little"))


def bmp(width, height, header=40):
    if header == 12:
        info = struct.pack("<IHHHH", 12, width, height, 1, 24)
    else:
        info = struct.pack("<IiiHH", header, width, height, 1, 24) + bytes(header - 16)
    return b"BM" + struct.pack("<IHHI", 14 + len(info), 0, 0, 14 + len(info)) + info


class TestRaster(unittest.TestCase):
    def assert_image(self, data, kind, width, height, ext):
        info = sniff(data)
        self.assertIsInstance(info, ImageInfo)
        self.assertEqual((info.kind, info.width, info.height, info.ext), (kind, width, height, ext))

    def test_png(self):
        self.assert_image(png(600, 900), "png", 600, 900, ".png")
        self.assertIsNone(sniff(png(600, 900, first_chunk=b"CgBI")))
        self.assertIsNone(sniff(png(0, 900)))

    def test_jpeg_baseline_with_prefixes(self):
        self.assert_image(jpeg(600, 900), "jpeg", 600, 900, ".jpg")
        self.assert_image(jpeg(1200, 1600, before=JFIF), "jpeg", 1200, 1600, ".jpg")
        self.assert_image(jpeg(1200, 1600, before=EXIF + JFIF + segment(0xDB, bytes(65)) + segment(0xC4, bytes(30))),
                          "jpeg", 1200, 1600, ".jpg")

    def test_jpeg_progressive_and_other_loadable_frames(self):
        for marker in (0xC1, 0xC2, 0xC9, 0xCA):
            with self.subTest(hex(marker)):
                self.assert_image(jpeg(640, 480, marker=marker), "jpeg", 640, 480, ".jpg")

    def test_jpeg_restart_markers_and_fill_bytes(self):
        before = segment(0xDD, b"\x00\x08") + b"\xff\xd0\xff\xd7\xff\x01" + b"\xff\xff\xff" + segment(0xFE, b"comment")
        self.assert_image(jpeg(320, 200, before=before), "jpeg", 320, 200, ".jpg")

    def test_jpeg_without_a_usable_frame(self):
        whole = jpeg(600, 900, before=JFIF)
        for cut in range(len(JFIF) + 11):
            with self.subTest(cut=cut):
                self.assertIsNone(sniff(whole[:cut]))
        self.assertIsNone(sniff(jpeg(600, 900, marker=0xC3)))
        self.assertIsNone(sniff(b"\xff\xd8" + segment(0xDA, bytes(10)) + frame(0xC0, 600, 900)))
        self.assertIsNone(sniff(b"\xff\xd8" + JFIF + b"\xff\xd9" + frame(0xC0, 600, 900)))
        self.assertIsNone(sniff(b"\xff\xd8\x00\x00" + frame(0xC0, 600, 900)))
        self.assertIsNone(sniff(b"\xff\xd8" + b"\xff\xe0\x00\x00" * 4000))

    def test_gif(self):
        self.assert_image(gif(120, 80), "gif", 120, 80, ".gif")
        self.assert_image(gif(120, 80, b"GIF87a"), "gif", 120, 80, ".gif")
        self.assertIsNone(sniff(b"GIF89a\x10"))

    def test_webp(self):
        self.assert_image(webp_lossy(800, 1200), "webp", 800, 1200, ".webp")
        self.assert_image(webp_lossless(801, 1201), "webp", 801, 1201, ".webp")
        self.assert_image(webp_extended(4000, 6000), "webp", 4000, 6000, ".webp")
        self.assertIsNone(sniff(webp(b"ALPH", bytes(20))))
        for data in (webp_lossy(800, 1200), webp_lossless(801, 1201), webp_extended(4000, 6000)):
            self.assertIsNone(sniff(data[:22]))

    def test_bmp(self):
        self.assert_image(bmp(64, 48), "bmp", 64, 48, ".bmp")
        self.assert_image(bmp(64, -48), "bmp", 64, 48, ".bmp")
        self.assert_image(bmp(64, 48, header=12), "bmp", 64, 48, ".bmp")
        self.assert_image(bmp(64, 48, header=124), "bmp", 64, 48, ".bmp")
        self.assertIsNone(sniff(b"BMW is a car maker, not a bitmap."))
        self.assertIsNone(sniff(bmp(64, 48)[:20]))

    def test_images_too_large_for_qt(self):
        self.assertIsNone(sniff(png(65535, 65535)))
        self.assertIsNone(sniff(jpeg(65535, 65535)))
        self.assertIsNotNone(sniff(png(8000, 8000)))

    def test_accepts_other_buffers(self):
        self.assertEqual(sniff(bytearray(png(3, 4))).width, 3)
        self.assertEqual(sniff(memoryview(gif(3, 4))).height, 4)


class TestSvg(unittest.TestCase):
    def assert_svg(self, markup, width, height):
        info = sniff(markup.encode("utf-8") if isinstance(markup, str) else markup)
        self.assertIsNotNone(info)
        self.assertEqual((info.kind, info.width, info.height, info.ext), ("svg", width, height, ".svg"))

    def test_sizes(self):
        ns = 'xmlns="http://www.w3.org/2000/svg"'
        self.assert_svg('<svg %s width="600" height="900"/>' % ns, 600, 900)
        self.assert_svg("<svg %s width='600px' height=' 900.4 PX '>" % ns, 600, 900)
        self.assert_svg('<svg %s viewBox="0 0 1200 1600">' % ns, 1200, 1600)
        self.assert_svg('<svg %s width="100%%" height="100%%" viewBox="0,0,300.5,400">' % ns, 300, 400)
        self.assert_svg('<svg %s width="210mm" height="297mm" viewBox="0 0 210 297">' % ns, 210, 297)
        self.assert_svg('<svg %s width="210mm" height="297mm">' % ns, 0, 0)
        self.assert_svg('<svg %s width="600">' % ns, 0, 0)
        self.assert_svg('<svg %s width="-5" height="nan" viewBox="0 0 -1 inf">' % ns, 0, 0)
        self.assert_svg("<svg>", 0, 0)
        self.assert_svg('<svg stroke-width="9" data-height="7" width="20" height="10">', 20, 10)
        self.assert_svg('<svg:svg xmlns:svg="http://www.w3.org/2000/svg" width="5" height="6">', 5, 6)
        self.assert_svg('<SVG WIDTH="5" HEIGHT="6" VIEWBOX="0 0 1 1">', 5, 6)

    def test_prolog(self):
        prolog = ('\ufeff<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<!-- Created with a > tool -->\n'
                  '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" '
                  '[ <!ENTITY ns "http://www.w3.org/2000/svg"> ]>\n')
        self.assert_svg(prolog + '<svg width="10" height="20">', 10, 20)
        self.assert_svg(" " * 4000 + '<svg width="10" height="20">', 10, 20)
        self.assert_svg('<svg data-x="' + "y" * 9000 + '" width="10" height="20">', 10, 20)

    def test_not_svg(self):
        for markup in ("<html><body><svg width='1' height='1'/></body></html>", "<svgx/>", "svg", "<svg",
                       " " * 5000 + "<svg width='1' height='1'>", "<!-- <svg> ", "<?xml version='1.0'?>"):
            with self.subTest(markup[:20]):
                self.assertIsNone(sniff(markup.encode("utf-8")))


def chunk(kind, payload=b""):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png_of_type(colour_type, before_pixels=b""):
    header = struct.pack(">II5B", 6, 9, 8, colour_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + before_pixels + chunk(b"IDAT", bytes(8))


class TestTransparency(unittest.TestCase):
    def test_png_alpha_channel(self):
        self.assertTrue(sniff(png_of_type(6)).alpha)
        self.assertTrue(sniff(png_of_type(4)).alpha)
        self.assertFalse(sniff(png_of_type(2)).alpha)
        self.assertFalse(sniff(png_of_type(0)).alpha)

    def test_png_transparent_palette_entry(self):
        palette = chunk(b"PLTE", bytes(6))
        self.assertTrue(sniff(png_of_type(3, palette + chunk(b"tRNS", b"\x00"))).alpha)
        self.assertFalse(sniff(png_of_type(3, palette)).alpha)
        # Only a tRNS ahead of the pixels counts; the name inside pixel data does not.
        self.assertFalse(sniff(png_of_type(2, chunk(b"tEXt", b"tRNS"))).alpha)

    def test_gif_control_block(self):
        head = b"GIF89a" + struct.pack("<HH", 6, 9) + b"\x80\x00\x00" + bytes(6)
        control = lambda flags: b"\x21\xf9\x04" + bytes([flags]) + bytes(4)
        self.assertTrue(sniff(head + control(1) + b"\x2c").alpha)
        self.assertFalse(sniff(head + control(0) + b"\x2c").alpha)
        self.assertFalse(sniff(head + b"\x2c").alpha)
        comment = b"\x21\xfe\x03abc\x00"
        self.assertTrue(sniff(head + comment + control(1) + b"\x2c").alpha)

    def test_webp(self):
        opaque = webp(b"VP8X", b"\x00\x00\x00\x00" + (5).to_bytes(3, "little") + (8).to_bytes(3, "little"))
        self.assertTrue(sniff(webp_extended(6, 9)).alpha)
        self.assertFalse(sniff(opaque).alpha)
        see_through = webp(b"VP8L", b"\x2f" + struct.pack("<I", 5 | (8 << 14) | (1 << 28)) + bytes(4))
        self.assertTrue(sniff(see_through).alpha)
        self.assertFalse(sniff(webp_lossless(6, 9)).alpha)
        self.assertFalse(sniff(webp_lossy(6, 9)).alpha)

    def test_others(self):
        self.assertTrue(sniff(b'<svg width="6" height="9">').alpha)
        self.assertFalse(sniff(jpeg(6, 9)).alpha)
        self.assertFalse(sniff(bmp(6, 9)).alpha)

    def test_a_cut_header_is_opaque_not_an_error(self):
        whole = png_of_type(6)
        self.assertFalse(sniff(whole[:25]).alpha)
        self.assertFalse(sniff(gif(6, 9)[:10]).alpha)


class TestRefused(unittest.TestCase):
    def test_formats_qt_may_not_load(self):
        samples = {
            "tiff-le": b"II*\x00\x08\x00\x00\x00",
            "tiff-be": b"MM\x00*\x00\x00\x00\x08",
            "jpeg2000": b"\x00\x00\x00\x0cjP  \r\n\x87\n\x00\x00\x00\x14ftypjp2 ",
            "jpeg2000-codestream": b"\xff\x4f\xff\x51\x00\x2f",
            "avif": b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1",
            "heic": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic",
            "jxl": b"\xff\x0a\x00\x00",
            "jxl-container": b"\x00\x00\x00\x0cJXL \r\n\x87\n",
            "ico": b"\x00\x00\x01\x00\x01\x00\x10\x10\x00\x00\x01\x00\x20\x00",
            "pdf": b"%PDF-1.7\n",
            "gzip": b"\x1f\x8b\x08\x00" + bytes(20),
            "text": b"Call me Ishmael.",
        }
        for name, data in samples.items():
            with self.subTest(name):
                self.assertIsNone(sniff(data + bytes(64)))

    def test_never_raises(self):
        whole = (png(6, 9), jpeg(6, 9, before=EXIF), gif(6, 9), webp_lossy(6, 9), webp_lossless(6, 9),
                 webp_extended(6, 9), bmp(6, 9), bmp(6, 9, header=12), b'<svg width="6" height="9">')
        for data in whole:
            for cut in range(len(data)):
                sniff(data[:cut])
        for size in (0, 1, 2, 3, 7, 64, 4096):
            sniff(bytes(size))
            sniff(b"\xff" * size)
            sniff(os.urandom(size))
            for magic in (b"\x89PNG\r\n\x1a\n", b"\xff\xd8", b"GIF89a", b"RIFF\x00\x00\x00\x00WEBP", b"BM", b"<svg"):
                sniff(magic + os.urandom(size))


if __name__ == "__main__":
    unittest.main()
