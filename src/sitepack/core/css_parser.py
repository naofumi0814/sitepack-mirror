"""CSS parsing and URL extraction for sitepack.

Uses tinycss2 to tokenize CSS and extract ``url()`` references and ``@import``
rules so that external assets can be downloaded and rewritten for offline use.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import tinycss2

from sitepack.core.html_parser import AssetRef

logger = logging.getLogger(__name__)

_SKIP_SCHEMES = frozenset({"data", "mailto", "tel", "javascript"})

# Regex matching url(...) with optional quotes inside CSS text.
_URL_FUNC_RE = re.compile(
    r"""url\(\s*(?P<quote>['"]?)(?P<url>[^)'"]+?)(?P=quote)\s*\)""",
    re.IGNORECASE,
)

# Regex matching @import "..." or @import '...' (without the url() wrapper).
_IMPORT_STRING_RE = re.compile(
    r"""@import\s+(?P<quote>['"])(?P<url>[^'"]+?)(?P=quote)""",
    re.IGNORECASE,
)


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


def _guess_asset_type(url: str, context: str) -> str:
    """Heuristically determine the asset type from a CSS url() reference.

    *context* is one of ``"import"``, ``"font-face"``, or ``"general"`` to help
    guide the decision.
    """
    if context == "import":
        return "stylesheet"
    if context == "font-face":
        return "font"

    lower = url.lower()
    # Common image extensions
    if any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico")):
        return "image"
    # Font extensions
    if any(lower.endswith(ext) for ext in (".woff", ".woff2", ".ttf", ".otf", ".eot")):
        return "font"
    return "other"


class CssAssetParser:
    """Extract and rewrite asset URLs in CSS text."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, css_text: str, base_url: str) -> list[AssetRef]:
        """Parse *css_text* and return all discovered asset references."""
        assets: list[AssetRef] = []

        try:
            rules = tinycss2.parse_stylesheet(
                css_text, skip_comments=True, skip_whitespace=True,
            )
        except Exception:
            logger.warning("tinycss2 failed to parse CSS from %s", base_url, exc_info=True)
            return assets

        self._walk_rules(rules, base_url, assets)

        logger.debug("Parsed CSS at %s: %d asset refs", base_url, len(assets))
        return assets

    def rewrite_urls(
        self,
        css_text: str,
        url_map: dict[str, str],
        base_url: str,
    ) -> str:
        """Return *css_text* with all url() and @import URLs replaced per *url_map*."""
        result = self._rewrite_url_functions(css_text, url_map, base_url)
        result = self._rewrite_import_strings(result, url_map, base_url)
        return result

    # ------------------------------------------------------------------
    # Extraction internals
    # ------------------------------------------------------------------

    def _walk_rules(
        self,
        rules: list[tinycss2.ast.Node],
        base_url: str,
        assets: list[AssetRef],
    ) -> None:
        """Recursively walk tinycss2 AST nodes looking for URL references."""
        for rule in rules:
            rule_type = rule.type

            # @import rule
            if rule_type == "at-rule" and rule.lower_at_keyword == "import":
                self._handle_import(rule, base_url, assets)

            # @font-face rule
            elif rule_type == "at-rule" and rule.lower_at_keyword == "font-face":
                if rule.content is not None:
                    self._extract_urls_from_tokens(rule.content, base_url, assets, context="font-face")

            # Other at-rules with content (e.g. @media)
            elif rule_type == "at-rule" and rule.content is not None:
                # Parse the content block as stylesheet contents to catch nested rules.
                try:
                    nested, _ = tinycss2.parse_stylesheet_contents(
                        tinycss2.serialize(rule.content),
                        skip_comments=True,
                        skip_whitespace=True,
                    )
                    self._walk_rules(nested, base_url, assets)
                except Exception:
                    # Fallback: just scan tokens for url() references.
                    self._extract_urls_from_tokens(rule.content, base_url, assets, context="general")

            # Qualified rule (selectors + declarations)
            elif rule_type == "qualified-rule" and rule.content is not None:
                self._extract_urls_from_tokens(rule.content, base_url, assets, context="general")

    def _handle_import(
        self,
        rule: tinycss2.ast.Node,
        base_url: str,
        assets: list[AssetRef],
    ) -> None:
        """Extract a URL from an ``@import`` rule's prelude."""
        for token in rule.prelude:
            if token.type == "url":
                resolved = _resolve_url(token.value, base_url)
                if resolved is not None:
                    assets.append(
                        AssetRef(
                            url=resolved,
                            asset_type="stylesheet",
                            source_tag="@import",
                            source_attr="url",
                        )
                    )
                return
            if token.type == "string":
                resolved = _resolve_url(token.value, base_url)
                if resolved is not None:
                    assets.append(
                        AssetRef(
                            url=resolved,
                            asset_type="stylesheet",
                            source_tag="@import",
                            source_attr="url",
                        )
                    )
                return
            if token.type == "function" and token.lower_name == "url":
                for arg in token.arguments:
                    if arg.type == "string":
                        resolved = _resolve_url(arg.value, base_url)
                        if resolved is not None:
                            assets.append(
                                AssetRef(
                                    url=resolved,
                                    asset_type="stylesheet",
                                    source_tag="@import",
                                    source_attr="url",
                                )
                            )
                        return

    def _extract_urls_from_tokens(
        self,
        tokens: list[tinycss2.ast.Node],
        base_url: str,
        assets: list[AssetRef],
        *,
        context: str = "general",
    ) -> None:
        """Scan a flat token list for ``url()`` references."""
        for token in tokens:
            if token.type == "url":
                resolved = _resolve_url(token.value, base_url)
                if resolved is not None:
                    assets.append(
                        AssetRef(
                            url=resolved,
                            asset_type=_guess_asset_type(resolved, context),
                            source_tag="style",
                            source_attr="url()",
                        )
                    )
            elif token.type == "function" and token.lower_name == "url":
                for arg in token.arguments:
                    if arg.type == "string":
                        resolved = _resolve_url(arg.value, base_url)
                        if resolved is not None:
                            assets.append(
                                AssetRef(
                                    url=resolved,
                                    asset_type=_guess_asset_type(resolved, context),
                                    source_tag="style",
                                    source_attr="url()",
                                )
                            )
                        break

    # ------------------------------------------------------------------
    # Rewriting internals
    # ------------------------------------------------------------------

    @staticmethod
    def _rewrite_url_functions(
        css_text: str,
        url_map: dict[str, str],
        base_url: str,
    ) -> str:
        """Replace ``url(...)`` references in *css_text*."""

        def _replace(match: re.Match[str]) -> str:
            raw_url = match.group("url")
            resolved = _resolve_url(raw_url, base_url)
            if resolved and resolved in url_map:
                quote = match.group("quote")
                return f"url({quote}{url_map[resolved]}{quote})"
            return match.group(0)

        return _URL_FUNC_RE.sub(_replace, css_text)

    @staticmethod
    def _rewrite_import_strings(
        css_text: str,
        url_map: dict[str, str],
        base_url: str,
    ) -> str:
        """Replace ``@import "..."`` and ``@import '...'`` references."""

        def _replace(match: re.Match[str]) -> str:
            raw_url = match.group("url")
            resolved = _resolve_url(raw_url, base_url)
            if resolved and resolved in url_map:
                quote = match.group("quote")
                return f"@import {quote}{url_map[resolved]}{quote}"
            return match.group(0)

        return _IMPORT_STRING_RE.sub(_replace, css_text)
