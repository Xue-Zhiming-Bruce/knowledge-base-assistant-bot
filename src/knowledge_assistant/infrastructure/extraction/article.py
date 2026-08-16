"""Source-neutral article extraction backed by Trafilatura."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import trafilatura
from lxml import html as lxml_html  # type: ignore[import-untyped]
from markdownify import markdownify
from trafilatura.metadata import extract_metadata

from knowledge_assistant.domain.documents import SourceProvider
from knowledge_assistant.domain.sources import (
    ExtractedArticle,
    ExtractedImage,
    ExtractionError,
    FetchedContent,
)


class ArticleExtractor:
    """Extract semantic Markdown and reliable source metadata from article HTML."""

    def extract(self, fetched: FetchedContent) -> ExtractedArticle:
        try:
            html = fetched.body.decode("utf-8", errors="replace")
            metadata = extract_metadata(html, default_url=fetched.final_url)
            fallback_markdown = trafilatura.extract(
                html,
                url=fetched.final_url,
                output_format="markdown",
                include_comments=False,
                include_links=True,
                include_tables=True,
                include_images=False,
                favor_precision=True,
            )
            markdown, images = self._extract_markdown_with_images(
                html,
                base_url=fetched.final_url,
            )
            if len(markdown.strip()) < 200:
                markdown = fallback_markdown or ""
                images = ()
            markdown = self._normalize_markdown_links(self._demote_top_level_headings(markdown))
            markdown = self._postprocess_markdown(markdown)
        except Exception as error:
            raise ExtractionError("Article extraction failed.") from error
        title = (metadata.title or "").strip()
        if not title:
            raise ExtractionError("Article title could not be extracted.")
        if markdown is None or len(markdown.strip()) < 200:
            raise ExtractionError("Extracted article content is too short.")

        authors = tuple(
            part.strip()
            for part in (metadata.author or "").replace(";", ",").split(",")
            if part.strip()
        )
        return ExtractedArticle(
            title=title,
            markdown=markdown,
            authors=authors,
            published_at=self._parse_date(metadata.date),
            canonical_url=(metadata.url or fetched.final_url).strip(),
            images=images,
        )

    @staticmethod
    def _extract_markdown_with_images(
        html: str,
        *,
        base_url: str,
    ) -> tuple[str, tuple[ExtractedImage, ...]]:
        tree = lxml_html.fromstring(html)
        candidates = tree.xpath("//article")
        if not candidates:
            candidates = tree.xpath("//main")
        if not candidates:
            return "", ()
        article = max(
            candidates,
            key=lambda node: len(" ".join(node.itertext())),
        )
        ArticleExtractor._normalize_links(article)
        ArticleExtractor._replace_article_cards(article)
        for unwanted in article.xpath(
            ".//script | .//style | .//noscript | .//nav | .//form | .//button | .//svg | .//iframe"
        ):
            unwanted.drop_tree()

        images: list[ExtractedImage] = []
        image_nodes = article.xpath(".//figure//img | .//p//img")
        if image_nodes:
            for decorative_image in article.xpath(
                ".//img[not(ancestor::figure) and not(ancestor::p)]"
            ):
                decorative_image.drop_tree()
        else:
            image_nodes = article.xpath(".//img[not(ancestor::header) and not(ancestor::footer)]")
            for decorative_image in article.xpath(".//header//img | .//footer//img"):
                decorative_image.drop_tree()
        for image in image_nodes:
            source_url = ArticleExtractor._image_url(image, base_url=base_url)
            if source_url is None:
                image.drop_tree()
                continue
            placeholder = f"ka-image://{len(images):04d}"
            alt_text = " ".join((image.get("alt") or "").split())
            images.append(
                ExtractedImage(
                    placeholder=placeholder,
                    original_url=source_url,
                    alt_text=alt_text,
                    expected_width=ArticleExtractor._positive_dimension(image.get("width")),
                    expected_height=ArticleExtractor._positive_dimension(image.get("height")),
                )
            )
            image.attrib.clear()
            image.set("src", placeholder)
            image.set("alt", alt_text)

        converted = markdownify(
            lxml_html.tostring(article, encoding="unicode"),
            heading_style="ATX",
            bullets="-",
            code_language_callback=ArticleExtractor._code_language,
            strip_pre=None,
        )
        return converted.strip(), tuple(images)

    @staticmethod
    def _code_language(element: Any) -> str | None:
        language = element.get("data-code-language")
        if isinstance(language, str) and re.fullmatch(r"[A-Za-z0-9_+.#-]+", language):
            return language
        return None

    @staticmethod
    def _normalize_links(article: object) -> None:
        for link in article.xpath(".//a[@href]"):  # type: ignore[attr-defined]
            href = link.get("href") or ""
            parsed = urlsplit(href)
            if parsed.hostname in {"google.com", "www.google.com"} and parsed.path == "/url":
                query = parse_qs(parsed.query)
                destination = (query.get("q") or query.get("url") or [""])[0]
                destination_parsed = urlsplit(destination)
                if destination_parsed.scheme in {"http", "https"} and destination_parsed.hostname:
                    link.set("href", destination)

    @staticmethod
    def _replace_article_cards(article: object) -> None:
        cards = article.xpath(  # type: ignore[attr-defined]
            ".//div[contains(@class, 'digestPostEmbed')]"
        )
        for card in cards:
            title_nodes = card.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6")
            title = " ".join(title_nodes[0].text_content().split()) if title_nodes else ""
            wrapping_links = list(card.iterancestors("a"))
            target = wrapping_links[0] if wrapping_links else card
            href = target.get("href") or ""
            parsed = urlsplit(href)
            parent = target.getparent()
            if not title or parsed.scheme not in {"http", "https"} or parent is None:
                card.drop_tree()
                continue
            paragraph = lxml_html.Element("p")
            link = lxml_html.Element("a", href=href)
            link.text = title
            paragraph.append(link)
            parent.replace(target, paragraph)

    @staticmethod
    def _demote_top_level_headings(markdown: str) -> str:
        return "\n".join(
            f"#{line}" if line.startswith("# ") else line for line in markdown.splitlines()
        )

    @staticmethod
    def _normalize_markdown_links(markdown: str) -> str:
        duplicate_nested = re.compile(r"\[\[([^\]\n]+)\]\((https?://[^)\s]+)\)\]\(\2\)")
        duplicate_suffix = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)\]\(\2\)")
        redundant_brackets = re.compile(r"\[\[([^\]\n]+)\]\((https?://[^)\s]+)\)\]")
        markdown = duplicate_nested.sub(r"[\1](\2)", markdown)
        markdown = duplicate_suffix.sub(r"[\1](\2)", markdown)
        return redundant_brackets.sub(r"[\1](\2)", markdown)

    @staticmethod
    def _postprocess_markdown(markdown: str) -> str:
        return markdown

    @staticmethod
    def _image_url(image: object, *, base_url: str) -> str | None:
        get = image.get  # type: ignore[attr-defined]
        raw_url = get("data-src") or get("data-original") or get("src")
        srcset = get("data-srcset") or get("srcset")
        if srcset:
            candidates: list[tuple[int, str]] = []
            # Substack CDN transformation URLs contain literal commas. Split
            # only when a comma introduces another URL candidate.
            for entry in re.split(
                r",(?=\s*(?:(?:https?:)?//|/))",
                srcset,
            ):
                parts = entry.strip().split()
                if not parts:
                    continue
                score = 0
                if len(parts) > 1:
                    descriptor = parts[-1].lower()
                    try:
                        score = (
                            int(descriptor[:-1])
                            if descriptor.endswith("w")
                            else int(float(descriptor[:-1]) * 1000)
                            if descriptor.endswith("x")
                            else 0
                        )
                    except ValueError:
                        score = 0
                candidates.append((score, parts[0]))
            if candidates:
                raw_url = max(candidates, key=lambda candidate: candidate[0])[1]
        if not raw_url or raw_url.startswith("data:"):
            return None
        absolute_url = urljoin(base_url, raw_url)
        parsed = urlsplit(absolute_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return str(absolute_url)

    @staticmethod
    def _positive_dimension(value: object) -> int | None:
        if not isinstance(value, str) or not value.isdigit():
            return None
        result = int(value)
        return result if result > 0 else None

    @staticmethod
    def _parse_date(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed


class MediumArticleExtractor(ArticleExtractor):
    """Replaceable Medium adapter backed by the shared article engine."""

    PROVIDER = SourceProvider.MEDIUM


class SubstackArticleExtractor(ArticleExtractor):
    """Replaceable Substack adapter backed by the shared article engine."""

    PROVIDER = SourceProvider.SUBSTACK


class XArticleExtractor(ArticleExtractor):
    """Normalize semantic HTML produced by the strict Xquik Article adapter."""

    PROVIDER = SourceProvider.X

    @staticmethod
    def _postprocess_markdown(markdown: str) -> str:
        """Undo Markdown escapes inside display-math fences for MathJax."""

        display_math = re.compile(r"(?ms)^\$\$[ \t]*\n(?P<body>.*?)(?:\n[ \t]*\$\$[ \t]*$)")

        def restore_latex(match: re.Match[str]) -> str:
            body = "\n".join(
                line[:-2] if line.endswith("  ") else line
                for line in match.group("body").splitlines()
            )
            for escaped in ("_", "*", "#", "+", "-", "."):
                body = body.replace(f"\\{escaped}", escaped)
            return f"$$\n{body}\n$$"

        return display_math.sub(restore_latex, markdown)
