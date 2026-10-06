#!/usr/bin/env python3
"""Stand-in for the clipboard program: nothing reaches a clipboard.

What it was started with and what it was given on standard input are
appended, one JSON object a line, to $HOME/clipboard.log.
"""

import json
import os
import sys

with open(os.path.join(os.environ.get("HOME", "/tmp"), "clipboard.log"), "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "text": sys.stdin.read()}) + "\n")
