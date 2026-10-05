"""Reader backend: turns ebooks into display blocks for the Omarchy shell plugin."""

# Bump whenever a change can alter block boundaries or order: saved reading
# positions trust a block index only for the version that produced it.
CONVERTER_VERSION = "1"
