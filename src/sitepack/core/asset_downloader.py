"""Async asset downloader with httpx, robots.txt support, and Playwright rendering."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from sitepack.core.config import AppConfig
from sitepack.core.project_state import ProjectState

logger = logging.getLogger(__name__)

# Encoding names that map to Windows Shift-JIS (cp932) — a superset of
# strict Shift-JIS that handles the extra characters used by many Japanese sites.
_SHIFT_JIS_ALIASES: frozenset[str] = frozenset({
    "shiftjis", "shift-jis", "sjis", "xsjis", "mskanji", "csshiftjis",
    "shiftjis2004", "shiftjisx0213",
})


def _decode_html_bytes(content: bytes, content_type_header: str) -> str:
    """Decode raw HTML bytes to a Unicode string using the correct charset.

    Priority:
    1. ``charset`` from the HTTP ``Content-Type`` header.
    2. ``charset`` detected from ``<meta>`` tags in the first 4 KB.
    3. httpx / charset_normalizer auto-detection (fallback).
    """
    # 1. Try charset from Content-Type header
    ct_charset = _charset_from_header(content_type_header)
    if ct_charset:
        enc = _normalize_encoding(ct_charset)
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            logger.debug("Content-Type charset %r failed; trying meta detection.", enc)

    # 2. Try charset from HTML <meta> tags
    meta_charset = _charset_from_html_bytes(content[:4096])
    if meta_charset:
        enc = _normalize_encoding(meta_charset)
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            logger.debug("Meta charset %r failed; falling back to auto.", enc)

    # 3. Fall back: let httpx / charset_normalizer guess
    r = httpx.Response(200, content=content,
                       headers={"content-type": content_type_header or "text/html"})
    return r.text


def _charset_from_header(content_type: str) -> str | None:
    """Extract charset from a Content-Type header string."""
    if not content_type:
        return None
    m = re.search(r"charset\s*=\s*([^\s;,\"']+)", content_type, re.IGNORECASE)
    return m.group(1) if m else None


def _charset_from_html_bytes(data: bytes) -> str | None:
    """Extract charset from HTML <meta> tags encoded in *data* (raw bytes)."""
    # Decode as ASCII so we can read the ASCII parts of any encoding
    head = data.decode("ascii", errors="replace")
    m = re.search(r"charset\s*=\s*[\"' ]?([^\"'\\s>;]+)", head, re.IGNORECASE)
    return m.group(1) if m else None


def _normalize_encoding(enc: str) -> str:
    """Map Shift-JIS variant names to Python's ``cp932`` codec."""
    key = enc.lower().replace("-", "").replace("_", "").replace(" ", "")
    if key in _SHIFT_JIS_ALIASES:
        return "cp932"
    return enc


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Result of fetching a single URL."""

    url: str
    status_code: int
    content_type: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)


class AssetDownloader:
    """Downloads pages and assets using httpx with concurrency control.

    Supports robots.txt checking, content-hash deduplication, and
    optional Playwright-based JavaScript rendering.
    """

    def __init__(self, config: AppConfig, state: ProjectState) -> None:
        self._config = config
        self._state = state
        self._client: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(
            config.concurrency
        )
        self._robots_cache: dict[str, RobotFileParser] = {}
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the shared ``httpx.AsyncClient``."""
        if self._client is not None:
            logger.debug("AssetDownloader already started; skipping.")
            return

        timeout = httpx.Timeout(self._config.timeout, connect=self._config.timeout)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": self._config.user_agent},
            follow_redirects=True,
            http2=True,
        )
        logger.info("AssetDownloader started (concurrency=%s).", self._semaphore._value)

    async def close(self) -> None:
        """Shut down the HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("AssetDownloader closed.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_page(self, url: str) -> FetchResult | None:
        """Fetch an HTML page and return a :class:`FetchResult`, or *None* on failure.

        Respects robots.txt when ``config.respect_robots`` is enabled and
        inserts an inter-request delay of ``config.request_delay`` seconds.
        """
        if self._client is None:
            logger.error("fetch_page called before start(); call start() first.")
            return None

        if getattr(self._config, "respect_robots", True) and not await self.check_robots(url):
            logger.info("Blocked by robots.txt: %s", url)
            return None

        async with self._semaphore:
            await self._enforce_delay()
            try:
                response = await self._client.get(url)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                headers = dict(response.headers)

                logger.debug(
                    "Fetched page %s [%s] content-type=%s",
                    url,
                    response.status_code,
                    content_type,
                )

                # Use smart charset detection so Shift-JIS and other
                # non-UTF-8 pages are decoded correctly even when the
                # server omits the charset from the Content-Type header.
                text = _decode_html_bytes(response.content, content_type)

                return FetchResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    content_type=content_type,
                    text=text,
                    headers=headers,
                )
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "HTTP %s for %s: %s",
                    exc.response.status_code,
                    url,
                    exc,
                )
            except httpx.RequestError as exc:
                logger.warning("Request error for %s: %s", url, exc)
            except Exception:
                logger.exception("Unexpected error fetching page %s", url)

        return None

    async def fetch_asset(self, url: str, save_path: Path) -> bool:
        """Download a binary asset to *save_path* using streaming.

        Performs content-hash deduplication: if an identical file was already
        saved elsewhere, a symlink is created instead of storing a duplicate.

        Returns ``True`` when the file is available at *save_path* (either
        freshly downloaded or symlinked).
        """
        if self._client is None:
            logger.error("fetch_asset called before start(); call start() first.")
            return False

        async with self._semaphore:
            await self._enforce_delay()
            try:
                async with self._client.stream("GET", url) as response:
                    response.raise_for_status()

                    save_path.parent.mkdir(parents=True, exist_ok=True)

                    hasher = hashlib.sha256()
                    tmp_path = save_path.with_suffix(save_path.suffix + ".tmp")

                    try:
                        with tmp_path.open("wb") as fh:
                            async for chunk in response.aiter_bytes(chunk_size=65_536):
                                fh.write(chunk)
                                hasher.update(chunk)
                    except Exception:
                        # Clean up partial download
                        tmp_path.unlink(missing_ok=True)
                        raise

                content_hash = hasher.hexdigest()

                # Deduplication via state's asset hashes
                existing = self._state.asset_hashes.get(content_hash)
                if existing is not None:
                    existing_path = Path(existing)
                    if existing_path.exists() and existing_path != save_path:
                        tmp_path.unlink(missing_ok=True)
                        save_path.unlink(missing_ok=True)
                        try:
                            save_path.symlink_to(existing_path)
                            logger.debug(
                                "Deduplicated %s -> %s (hash=%s)",
                                save_path,
                                existing_path,
                                content_hash[:12],
                            )
                            return True
                        except OSError:
                            logger.debug(
                                "Symlink failed for %s; falling back to copy.",
                                save_path,
                            )
                            # Fall through to rename tmp_path below; re-download
                            # is needed since we deleted tmp_path.
                            return await self._redownload_asset(url, save_path, content_hash)

                # Commit the download
                tmp_path.replace(save_path)
                self._state.asset_hashes[content_hash] = str(save_path)

                logger.debug(
                    "Saved asset %s -> %s (hash=%s)",
                    url,
                    save_path,
                    content_hash[:12],
                )
                return True

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "HTTP %s downloading asset %s: %s",
                    exc.response.status_code,
                    url,
                    exc,
                )
            except httpx.RequestError as exc:
                logger.warning("Request error downloading asset %s: %s", url, exc)
            except Exception:
                logger.exception("Unexpected error downloading asset %s", url)

        return False

    async def check_robots(self, url: str) -> bool:
        """Return ``True`` if *url* is allowed by the host's ``robots.txt``.

        Results are cached per host to avoid redundant fetches.
        """
        parsed = urlparse(url)
        host = parsed.netloc
        if not host:
            return True

        if host not in self._robots_cache:
            robots_url = f"{parsed.scheme}://{host}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)

            try:
                if self._client is None:
                    logger.warning("Cannot fetch robots.txt; client not started.")
                    return True

                response = await self._client.get(robots_url)
                if response.status_code == 200:
                    rp.parse(response.text.splitlines())
                    logger.debug("Loaded robots.txt for %s", host)
                else:
                    # No robots.txt or error -> allow everything
                    rp.allow_all = True  # type: ignore[attr-defined]
                    logger.debug(
                        "No robots.txt for %s (status %s); allowing all.",
                        host,
                        response.status_code,
                    )
            except Exception:
                logger.warning("Failed to fetch robots.txt for %s; allowing by default.", host)
                rp.allow_all = True  # type: ignore[attr-defined]

            self._robots_cache[host] = rp

        rp = self._robots_cache[host]

        # If we flagged allow_all, skip the can_fetch check.
        if getattr(rp, "allow_all", False):
            return True

        allowed = rp.can_fetch(self._config.user_agent, url)
        if not allowed:
            logger.debug("robots.txt disallows %s", url)
        return allowed

    async def fetch_with_renderer(self, url: str) -> str | None:
        """Render *url* in a headless Chromium browser via Playwright.

        Optionally performs lazy-scroll to trigger deferred image loading
        when ``config.lazy_scroll`` is enabled, then waits
        ``config.wait_after_load`` seconds before capturing the DOM.
        """
        # Import lazily so the hard Playwright dependency is only required
        # when JS rendering is actually used.
        from playwright.async_api import async_playwright  # noqa: WPS433

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(user_agent=self._config.user_agent)
                    await page.goto(
                        url,
                        timeout=int(self._config.timeout * 1000),
                        wait_until="networkidle",
                    )

                    if getattr(self._config, "lazy_scroll", False):
                        for _ in range(10):
                            await page.evaluate("window.scrollBy(0, window.innerHeight)")
                            await asyncio.sleep(0.3)
                        await page.evaluate("window.scrollTo(0, 0)")

                    wait_seconds: float = getattr(self._config, "wait_after_load", 0.0)
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)

                    html: str = await page.content()
                    logger.debug("Rendered page %s via Playwright (%d chars).", url, len(html))
                    return html
                finally:
                    await browser.close()
        except Exception:
            logger.exception("Playwright rendering failed for %s", url)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _enforce_delay(self) -> None:
        """Sleep if necessary to respect ``config.request_delay``."""
        delay: float = getattr(self._config, "request_delay", 0.0)
        if delay <= 0:
            return

        loop = asyncio.get_running_loop()
        now = loop.time()
        elapsed = now - self._last_request_time
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = asyncio.get_running_loop().time()

    async def _redownload_asset(self, url: str, save_path: Path, content_hash: str) -> bool:
        """Re-download *url* to *save_path* after a failed symlink attempt."""
        try:
            if self._client is None:
                return False

            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with save_path.open("wb") as fh:
                    async for chunk in response.aiter_bytes(chunk_size=65_536):
                        fh.write(chunk)

            self._state.asset_hashes[content_hash] = str(save_path)
            logger.debug("Re-downloaded asset %s -> %s", url, save_path)
            return True
        except Exception:
            logger.exception("Re-download failed for %s", url)
            return False
