"""HTML parsing and URL extraction for sitepack.

Uses BeautifulSoup with the lxml parser to extract links, assets, and rewrite
URLs in downloaded HTML documents.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_SKIP_SCHEMES = frozenset({"mailto", "tel", "javascript", "data"})


@dataclass(frozen=True, slots=True)
class AssetRef:
    """A reference to an external asset discovered in HTML or CSS."""

    url: str
    asset_type: str  # image, stylesheet, script, font, icon, video, audio, other
    source_tag: str
    source_attr: str


@dataclass(slots=True)
class ParseResult:
    """Result of parsing an HTML document."""

    links: list[str] = field(default_factory=list)
    assets: list[AssetRef] = field(default_factory=list)
    title: str | None = None


def _should_skip_url(url: str) -> bool:
    """Return True if the URL uses a scheme we should ignore."""
    stripped = url.strip()
    if not stripped or stripped.startswith("#"):
        return True
    try:
        scheme = urlparse(stripped).scheme.lower()
    except ValueError:
        return True
    return scheme in _SKIP_SCHEMES


def _resolve_url(url: str, base_url: str) -> str | None:
    """Resolve *url* against *base_url*, returning None for skippable URLs."""
    stripped = url.strip()
    if _should_skip_url(stripped):
        return None
    try:
        resolved = urljoin(base_url, stripped)
    except ValueError:
        logger.debug("Failed to resolve URL %r against base %r", stripped, base_url)
        return None
    return resolved


def _parse_srcset(srcset: str) -> list[str]:
    """Parse an HTML ``srcset`` attribute and return a list of raw URLs.

    Each entry in a srcset is ``<url> [<width>w | <pixel-density>x]``, separated
    by commas.  We only care about the URL portion.
    """
    urls: list[str] = []
    for entry in srcset.split(","):
        parts = entry.split()
        if parts:
            urls.append(parts[0])
    return urls


def _rewrite_srcset(srcset: str, url_map: dict[str, str], base_url: str) -> str:
    """Rewrite URLs inside a ``srcset`` attribute value."""
    parts: list[str] = []
    for entry in srcset.split(","):
        tokens = entry.split()
        if not tokens:
            parts.append(entry)
            continue
        raw_url = tokens[0]
        resolved = _resolve_url(raw_url, base_url)
        if resolved and resolved in url_map:
            tokens[0] = url_map[resolved]
        parts.append(" ".join(tokens))
    return ", ".join(parts)


# Regex for url(...) references inside inline CSS.
_CSS_URL_RE = re.compile(
    r"""url\(\s*(?P<quote>['"]?)(?P<url>[^)'"]+?)(?P=quote)\s*\)""",
    re.IGNORECASE,
)


class HtmlParser:
    """Extract links and asset references from HTML documents."""

    def __init__(self) -> None:
        # Deferred import to avoid circular dependency at module level.
        from sitepack.core.css_parser import CssAssetParser

        self._css_parser = CssAssetParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, html: str, base_url: str) -> ParseResult:
        """Parse *html* and return discovered links and assets."""
        soup = BeautifulSoup(html, "lxml")
        result = ParseResult()

        # Honour <base href="..."> if present — it overrides the page URL
        # for resolving all relative URLs on the page.
        base_tag = soup.find("base", href=True)
        if base_tag:
            base_url = urljoin(base_url, base_tag["href"])

        # Page title
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            result.title = title_tag.string.strip()

        self._extract_links(soup, base_url, result)
        self._extract_assets(soup, base_url, result)
        self._extract_inline_css(soup, base_url, result)

        logger.debug(
            "Parsed %s: %d links, %d assets, title=%r",
            base_url,
            len(result.links),
            len(result.assets),
            result.title,
        )
        return result

    def rewrite_urls(
        self,
        html: str,
        url_map: dict[str, str],
        base_url: str,
    ) -> str:
        """Return *html* with all asset/link URLs replaced per *url_map*.

        Also updates charset declarations to UTF-8 so that browsers display
        the UTF-8 encoded output correctly, regardless of the source charset.
        """
        soup = BeautifulSoup(html, "lxml")

        # Fix charset declaration first so any <base> handling is correct
        self._fix_charset_to_utf8(soup)

        self._rewrite_a_hrefs(soup, url_map, base_url)
        self._rewrite_img_tags(soup, url_map, base_url)
        self._rewrite_picture_sources(soup, url_map, base_url)
        self._rewrite_script_tags(soup, url_map, base_url)
        self._rewrite_link_tags(soup, url_map, base_url)
        self._rewrite_source_tags(soup, url_map, base_url)
        self._rewrite_video_posters(soup, url_map, base_url)
        self._rewrite_inline_styles(soup, url_map, base_url)
        self._rewrite_frame_tags(soup, url_map, base_url)

        return str(soup)

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_links(
        self,
        soup: BeautifulSoup,
        base_url: str,
        result: ParseResult,
    ) -> None:
        # Standard <a href="..."> links
        for tag in soup.find_all("a", href=True):
            resolved = _resolve_url(tag["href"], base_url)
            if resolved is not None:
                result.links.append(resolved)

        # Image-map <area href="..."> links
        for tag in soup.find_all("area", href=True):
            resolved = _resolve_url(tag["href"], base_url)
            if resolved is not None:
                result.links.append(resolved)

        # <iframe src="..."> and <frame src="..."> — framed sub-pages
        for tag_name in ("iframe", "frame"):
            for tag in soup.find_all(tag_name, src=True):
                resolved = _resolve_url(tag["src"], base_url)
                if resolved is not None:
                    result.links.append(resolved)

        # <meta http-equiv="refresh" content="...;url=..."> redirects
        for tag in soup.find_all("meta", attrs={"http-equiv": re.compile(r"^refresh$", re.IGNORECASE)}):
            content = tag.get("content", "")
            if isinstance(content, str):
                match = re.search(r"url\s*=\s*['\"]?\s*([^'\";\s]+)", content, re.IGNORECASE)
                if match:
                    resolved = _resolve_url(match.group(1), base_url)
                    if resolved is not None:
                        result.links.append(resolved)

    def _extract_assets(
        self,
        soup: BeautifulSoup,
        base_url: str,
        result: ParseResult,
    ) -> None:
        # img[src]
        for tag in soup.find_all("img", src=True):
            self._add_asset(tag["src"], "image", "img", "src", base_url, result)

        # img[srcset]
        for tag in soup.find_all("img", srcset=True):
            for url in _parse_srcset(tag["srcset"]):
                self._add_asset(url, "image", "img", "srcset", base_url, result)

        # picture > source[srcset]
        for picture in soup.find_all("picture"):
            for source in picture.find_all("source", srcset=True):
                for url in _parse_srcset(source["srcset"]):
                    self._add_asset(url, "image", "source", "srcset", base_url, result)

        # script[src]
        for tag in soup.find_all("script", src=True):
            self._add_asset(tag["src"], "script", "script", "src", base_url, result)

        # link[rel=stylesheet][href]
        for tag in soup.find_all("link", rel=lambda v: v and "stylesheet" in v, href=True):
            self._add_asset(tag["href"], "stylesheet", "link", "href", base_url, result)

        # link[rel=icon] / link[rel="shortcut icon"]
        for tag in soup.find_all("link", href=True):
            rels = tag.get("rel", [])
            if "icon" in rels or "shortcut" in rels:
                self._add_asset(tag["href"], "icon", "link", "href", base_url, result)

        # source[src] (outside <picture>)
        for tag in soup.find_all("source", src=True):
            parent = tag.parent
            if isinstance(parent, Tag) and parent.name == "video":
                asset_type = "video"
            elif isinstance(parent, Tag) and parent.name == "audio":
                asset_type = "audio"
            else:
                asset_type = "other"
            self._add_asset(tag["src"], asset_type, "source", "src", base_url, result)

        # video[poster]
        for tag in soup.find_all("video", poster=True):
            self._add_asset(tag["poster"], "image", "video", "poster", base_url, result)

        # link[rel=preload][href]
        for tag in soup.find_all("link", rel=lambda v: v and "preload" in v, href=True):
            self._add_asset(tag["href"], "other", "link", "href", base_url, result)

    def _extract_inline_css(
        self,
        soup: BeautifulSoup,
        base_url: str,
        result: ParseResult,
    ) -> None:
        # <style> blocks
        for style_tag in soup.find_all("style"):
            css_text = style_tag.string
            if not css_text:
                continue
            try:
                css_assets = self._css_parser.parse(css_text, base_url)
                result.assets.extend(css_assets)
            except Exception:
                logger.warning("Failed to parse inline <style> in %s", base_url, exc_info=True)

        # Inline style="..." attributes (e.g. background-image: url(...))
        for tag in soup.find_all(style=True):
            style_val = tag.get("style", "")
            if not isinstance(style_val, str) or "url(" not in style_val:
                continue
            for match in _CSS_URL_RE.finditer(style_val):
                raw_url = match.group("url")
                resolved = _resolve_url(raw_url, base_url)
                if resolved is not None:
                    result.assets.append(
                        AssetRef(
                            url=resolved,
                            asset_type="image",
                            source_tag=tag.name,
                            source_attr="style",
                        )
                    )

    @staticmethod
    def _add_asset(
        raw_url: str,
        asset_type: str,
        source_tag: str,
        source_attr: str,
        base_url: str,
        result: ParseResult,
    ) -> None:
        resolved = _resolve_url(raw_url, base_url)
        if resolved is not None:
            result.assets.append(
                AssetRef(
                    url=resolved,
                    asset_type=asset_type,
                    source_tag=source_tag,
                    source_attr=source_attr,
                )
            )

    # ------------------------------------------------------------------
    # Rewriting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rewrite_attr(
        tag: Tag,
        attr: str,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        raw = tag.get(attr)
        if not raw or not isinstance(raw, str):
            return
        resolved = _resolve_url(raw, base_url)
        if resolved and resolved in url_map:
            tag[attr] = url_map[resolved]

    def _rewrite_a_hrefs(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        for tag in soup.find_all("a", href=True):
            self._rewrite_attr(tag, "href", url_map, base_url)

    def _rewrite_img_tags(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        for tag in soup.find_all("img"):
            self._rewrite_attr(tag, "src", url_map, base_url)
            if tag.get("srcset"):
                tag["srcset"] = _rewrite_srcset(tag["srcset"], url_map, base_url)

    def _rewrite_picture_sources(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        for picture in soup.find_all("picture"):
            for source in picture.find_all("source", srcset=True):
                source["srcset"] = _rewrite_srcset(source["srcset"], url_map, base_url)

    def _rewrite_script_tags(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        for tag in soup.find_all("script", src=True):
            self._rewrite_attr(tag, "src", url_map, base_url)

    def _rewrite_link_tags(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        for tag in soup.find_all("link", href=True):
            self._rewrite_attr(tag, "href", url_map, base_url)

    def _rewrite_source_tags(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        for tag in soup.find_all("source", src=True):
            self._rewrite_attr(tag, "src", url_map, base_url)

    def _rewrite_video_posters(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        for tag in soup.find_all("video", poster=True):
            self._rewrite_attr(tag, "poster", url_map, base_url)

    def _rewrite_inline_styles(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        """Rewrite ``url(...)`` references in ``<style>`` tags and ``style`` attributes."""
        # <style> tags
        for style_tag in soup.find_all("style"):
            css_text = style_tag.string
            if not css_text:
                continue
            rewritten = self._rewrite_css_urls(css_text, url_map, base_url)
            if rewritten != css_text:
                style_tag.string = rewritten

        # Inline style="..." attributes
        for tag in soup.find_all(style=True):
            style_val = tag["style"]
            if isinstance(style_val, str) and "url(" in style_val:
                tag["style"] = self._rewrite_css_urls(style_val, url_map, base_url)

    @staticmethod
    def _fix_charset_to_utf8(soup: BeautifulSoup) -> None:
        """Update any charset declarations in *soup* to UTF-8.

        Called before writing HTML to disk so that the browser reads the
        UTF-8 encoded file with the correct charset, regardless of the
        original encoding of the source page (e.g. Shift-JIS).
        """
        # <meta charset="Shift_JIS"> → <meta charset="UTF-8">
        for tag in soup.find_all("meta", charset=True):
            tag["charset"] = "UTF-8"

        # <meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">
        for tag in soup.find_all(
            "meta",
            attrs={"http-equiv": re.compile(r"^content-type$", re.IGNORECASE)},
        ):
            content_val = tag.get("content", "")
            if isinstance(content_val, str) and "charset" in content_val.lower():
                tag["content"] = re.sub(
                    r"charset\s*=\s*[^\s;\"']+",
                    "charset=UTF-8",
                    content_val,
                    flags=re.IGNORECASE,
                )

    def _rewrite_frame_tags(
        self,
        soup: BeautifulSoup,
        url_map: dict[str, str],
        base_url: str,
    ) -> None:
        """Rewrite ``src`` attributes on ``<frame>`` and ``<iframe>`` elements."""
        for tag in soup.find_all(["frame", "iframe"], src=True):
            self._rewrite_attr(tag, "src", url_map, base_url)

    @staticmethod
    def _rewrite_css_urls(
        css_text: str,
        url_map: dict[str, str],
        base_url: str,
    ) -> str:
        def _replace(match: re.Match[str]) -> str:
            raw_url = match.group("url")
            resolved = _resolve_url(raw_url, base_url)
            if resolved and resolved in url_map:
                quote = match.group("quote")
                return f"url({quote}{url_map[resolved]}{quote})"
            return match.group(0)

        return _CSS_URL_RE.sub(_replace, css_text)
