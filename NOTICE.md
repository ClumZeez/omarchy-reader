# Notices

Reader's own code is MIT licensed; see `LICENSE`.

## foliate-js

`backend/reader/mobi.py` — the reading of Kindle files — is a Python port of
`mobi.js` from foliate-js: the PalmDB record directory; the PalmDOC, MOBI,
EXTH and KF8 header layouts; the variable-width integers and the removal of
trailing entries; PalmDOC LZ77 and HUFF/CDIC decompression; the INDX, TAGX,
IDXT and CNCX index tables and the NCX tag mapping; MOBI 6 page-break
sections, `filepos` anchors and the guide reference; the KF8 FDST flows,
skeleton and fragment reassembly, `kindle:pos`, `kindle:embed` and
`kindle:flow` addressing and resource numbering; and cover lookup from EXTH.
Encoding detection, the refusals, contents selection, the repair of
insertion points and every limit were written for Reader. The port is used
under foliate-js's licence:

> MIT License
>
> Copyright (c) 2022 John Factotum
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## Omarchy and canon

The bar widget, service and popout follow the patterns of the Omarchy shell
and its first-party panels (MIT), and of the `canon` plugin by Ramen Packet
(MIT), which was the design reference. No code from either is included.

## Preview

The books in `preview.png` are public-domain editions produced by Standard
Ebooks (standardebooks.org), whose own work on them is dedicated to the
public domain (CC0).

## Reference readers

Reader's handling of real-world ebooks was informed by reading how other
open-source readers behave: foliate-js and Readest's fork of it (MIT),
epub.js (BSD), the Readium toolkits (BSD-3-Clause), calibre (GPL-3),
KOReader (AGPL-3) and crengine (GPL-2), MuPDF (AGPL-3), KindleUnpack
(GPL-3) and libmobi (LGPL-3). Apart from the foliate-js port named above,
no code from any of them is included: only what those programs do with
difficult files was studied, and Reader's implementation was written
independently.
