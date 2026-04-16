"""URL-to-local-path conversion and HTML/CSS URL rewriting."""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from sitepack.core.config import AppConfig
from sitepack.core.css_parser import CssAssetParser
from sitepack.core.html_parser import HtmlParser
from sitepack.core.project_state import ProjectState
from sitepack.core.url_normalizer import UrlNormalizer

logger = logging.getLogger(__name__)


class PathRewriter:
    """Maps URLs to local filesystem paths and rewrites references in saved files.

    The rewriter uses the following layout under the project output directory:

    * ``index.html`` -- the site's entry-point page (root URL)
    * ``pages/``     -- other same-host HTML pages
    * ``assets/``    -- same-host static assets (CSS, JS, images, fonts, ...)
    * ``external/<host>/`` -- assets originating from a different host
    """

    def __init__(self, config: AppConfig, state: ProjectState) -> None:
        self._config = config
        self._state = state
        self._normalizer = UrlNormalizer()

    # ------------------------------------------------------------------
    # Path computation
    # ------------------------------------------------------------------

    def compute_save_path(self, url: str) -> Path:
        """Determine the local file path where *url* should be saved.

        The returned path is **relative to the project directory**.

        Rules
        -----
        * **Same-host HTML pages** are stored under ``pages/`` (with the
          root page going to ``index.html`` at the project root).
        * **Same-host assets** go to ``assets/``.
        * **External assets** go to ``external/<host>/``.
        """
        parsed = urlparse(url)
        base_netloc = urlparse(self._config.start_url).netloc.lower()
        url_netloc = parsed.netloc.lower()

        is_same_host = url_netloc == base_netloc or url_netloc == ""
        relative = self._normalizer.url_to_relative_path(url)

        if is_same_host:
            if self._is_html_url(url):
                # Root page -> index.html at project root
                if self._is_root_url(url):
                    return Path("index.html")
                return Path("pages") / relative
            return Path("assets") / relative

        # External asset – namespaced by host
        external_host = parsed.hostname or parsed.netloc or "unknown"
        return Path("external") / external_host / relative

    def compute_relative_path(self, from_file: Path, to_file: Path) -> str:
        """Return a POSIX relative path string from *from_file* to *to_file*.

        Both paths should be absolute or both relative to the same root.
        The result is suitable for use in ``href`` / ``src`` attributes.
        """
        try:
            rel = PurePosixPath(to_file.relative_to(from_file.parent))
            return str(rel)
        except ValueError:
            # Files do not share a common prefix via relative_to; fall back
            # to manual computation using os.path.relpath semantics.
            pass

        from_parts = from_file.parent.parts
        to_parts = to_file.parts

        # Find the common ancestor
        common_length = 0
        for a, b in zip(from_parts, to_parts):
            if a != b:
                break
            common_length += 1

        ups = len(from_parts) - common_length
        downs = to_parts[common_length:]
        segments = [".."] * ups + list(downs)
        return str(PurePosixPath(*segments)) if segments else "."

    # ------------------------------------------------------------------
    # URL map construction
    # ------------------------------------------------------------------

    def build_url_map(self, from_file: Path) -> dict[str, str]:
        """Build a mapping of ``{original_url: relative_local_path}``
        relative to *from_file*'s directory.

        Uses :pyattr:`state.url_map` (``{url: relative_local_path_str}``) as
        the authoritative source of downloaded URLs.  Stored paths are
        relative to ``project_dir`` and resolved against it here.
        """
        url_map: dict[str, str] = {}
        project_dir = self._state.project_dir
        for original_url, local_path_str in self._state.url_map.items():
            local_path = project_dir / local_path_str
            if not local_path.exists():
                logger.debug(
                    "Skipping URL map entry (file missing): %s -> %s",
                    original_url,
                    local_path_str,
                )
                continue
            rel = self.compute_relative_path(from_file, local_path)
            url_map[original_url] = rel
        return url_map

    # ------------------------------------------------------------------
    # Rewriting
    # ------------------------------------------------------------------

    def rewrite_html(self, html: str, page_url: str, save_path: Path) -> str:
        """Rewrite all URLs in *html* to relative local paths.

        Parameters
        ----------
        html:
            Raw HTML content.
        page_url:
            The original URL the HTML was fetched from (used for resolving
            relative links).
        save_path:
            Where the HTML file will be saved on disk.

        Returns
        -------
        str
            The rewritten HTML with local relative paths.
        """
        url_map = self.build_url_map(save_path)
        if not url_map:
            logger.debug("No URL mappings available; returning HTML unchanged for %s", page_url)
            return html

        parser = HtmlParser()
        rewritten = parser.rewrite_urls(html, url_map, base_url=page_url)
        logger.debug(
            "Rewrote HTML for %s (%d URL mappings applied).",
            page_url,
            len(url_map),
        )
        return rewritten

    def rewrite_css(self, css: str, css_url: str, save_path: Path) -> str:
        """Rewrite all ``url()`` references in *css* to relative local paths.

        Parameters
        ----------
        css:
            Raw CSS content.
        css_url:
            The original URL the CSS was fetched from.
        save_path:
            Where the CSS file will be saved on disk.

        Returns
        -------
        str
            The rewritten CSS with local relative paths.
        """
        url_map = self.build_url_map(save_path)
        if not url_map:
            logger.debug("No URL mappings available; returning CSS unchanged for %s", css_url)
            return css

        parser = CssAssetParser()
        rewritten = parser.rewrite_urls(css, url_map, base_url=css_url)
        logger.debug(
            "Rewrote CSS for %s (%d URL mappings applied).",
            css_url,
            len(url_map),
        )
        return rewritten

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_html_url(self, url: str) -> bool:
        """Heuristically decide whether *url* points to an HTML page."""
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        # Explicitly non-HTML extensions
        non_html_extensions = {
            ".css", ".js", ".mjs", ".json", ".xml", ".png", ".jpg", ".jpeg",
            ".gif", ".svg", ".ico", ".webp", ".avif", ".woff", ".woff2",
            ".ttf", ".otf", ".eot", ".mp4", ".webm", ".mp3", ".ogg",
            ".wav", ".pdf", ".zip", ".gz", ".tar", ".br", ".map",
            ".csv", ".wasm", ".bmp", ".tiff",
        }
        suffix = Path(path).suffix.lower()
        if suffix in non_html_extensions:
            return False

        # HTML, server-side page extensions, and extension-less paths
        html_like_extensions = {
            ".html", ".htm", ".php", ".asp", ".aspx", ".jsp",
            ".cgi", ".shtml", ".xhtml", "",
        }
        if suffix in html_like_extensions:
            return True

        return False

    def _is_root_url(self, url: str) -> bool:
        """Return ``True`` when *url* is the site root (``/`` or empty path)."""
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        start_parsed = urlparse(self._config.start_url)

        # Match if the path is empty/root AND host matches the start URL
        if parsed.netloc == start_parsed.netloc and path in {"", start_parsed.path.rstrip("/")}:
            return True
        return False
