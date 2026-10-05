"""What a format module hands the library: a book before and after it is opened."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Meta:
    """What the library shows for a book before it is opened."""

    title: str
    authors: list[str]
    language: str
    cover: bytes | None
    error: str = ""


@dataclass
class Book:
    """A converted book: the book.json object without the fields the library adds."""

    title: str
    author: str
    language: str
    sections: list[int]
    toc: list[dict]
    blocks: list[dict]
    authors: list[str] = field(default_factory=list)
