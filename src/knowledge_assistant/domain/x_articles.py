"""Lossless, provider-neutral X Article content."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class XArticleInlineStyle:
    """One Draft.js-compatible inline style range."""

    offset: int
    length: int
    style: str


@dataclass(frozen=True, slots=True)
class XArticleBlock:
    """One ordered Article block validated by a provider adapter."""

    kind: str
    text: str | None = None
    url: str | None = None
    alt_text: str = ""
    width: int | None = None
    height: int | None = None
    inline_styles: tuple[XArticleInlineStyle, ...] = ()
    code_language: str | None = None
    table_rows: tuple[tuple[str, ...], ...] = ()
    table_alignments: tuple[str | None, ...] = ()


@dataclass(frozen=True, slots=True)
class XArticleDocument:
    """An X Article whose block order is safe to persist."""

    title: str
    blocks: tuple[XArticleBlock, ...]
    cover_image_url: str | None = None
    created_at: str | None = None
    author_name: str | None = None
    author_username: str | None = None
