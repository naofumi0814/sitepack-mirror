"""Central orchestrator for the sitepack website crawling and packaging process.

CrawlManager coordinates URL discovery, page fetching, asset downloading, and
URL rewriting to produce a fully self-contained offline copy of a website.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from sitepack.core.asset_downloader import AssetDownloader
from sitepack.core.config import AppConfig
from sitepack.core.css_parser import CssAssetParser
from sitepack.core.html_parser import HtmlParser, fix_charset_to_utf8
from sitepack.core.path_rewriter import PathRewriter
from sitepack.core.project_state import ProjectState
from sitepack.core.url_normalizer import UrlNormalizer

logger = logging.getLogger(__name__)

# Interval (in number of processed URLs) between automatic state saves
# during the crawl loop.  Keeps progress durable without excessive I/O.
_STATE_SAVE_INTERVAL: int = 25

# File suffixes that should be treated as HTML during post-crawl URL
# rewriting.  Keeping this aligned with ``PathRewriter._is_html_url`` ensures
# that every file we saved as an HTML page gets its links rewritten.
_HTML_SUFFIXES: frozenset[str] = frozenset({
    ".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".cgi", ".shtml", ".xhtml",
})


class CrawlManager:
    """Orchestrates the website crawling and packaging process.

    Typical usage::

        config = AppConfig(start_url="https://example.com", ...)
        manager = CrawlManager(config)
        manager.on_progress = my_progress_callback
        await manager.start()

    The manager supports graceful stop/resume, retry of failed URLs, and
    fires callbacks suitable for driving a GUI progress display.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._state: ProjectState | None = None
        self._normalizer = UrlNormalizer()
        self._html_parser = HtmlParser()
        self._css_parser = CssAssetParser()
        self._downloader: AssetDownloader | None = None
        self._rewriter: PathRewriter | None = None
        self._running = False
        self._paused = False
        self._stop_event = asyncio.Event()
        self._urls_since_save: int = 0

        # ----- GUI / consumer callbacks -----
        self.on_progress: Callable[[int, int], None] | None = None
        self.on_status: Callable[[str], None] | None = None
        self.on_url_processing: Callable[[str], None] | None = None
        self.on_log: Callable[[str], None] | None = None
        self.on_complete: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the crawl loop is currently active."""
        return self._running

    @property
    def state(self) -> ProjectState | None:
        """The current project state, or ``None`` before :meth:`start`."""
        return self._state

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start or resume crawling.

        If a project directory with an existing manifest is found, the crawl
        is resumed from where it left off.  Otherwise a fresh project is
        created.
        """
        if self._running:
            logger.warning("start() called while already running; ignoring.")
            return

        self._running = True
        self._stop_event.clear()

        project_dir = Path(self._config.output_dir) / self._config.project_name

        # Create or restore project state
        if project_dir.exists() and (project_dir / "_meta" / "manifest.json").exists():
            self._state = ProjectState.load_project(project_dir)
            self._emit_log("Resuming existing project")
        else:
            self._state = ProjectState.create_project(
                Path(self._config.output_dir),
                self._config.project_name,
                config=self._config,
            )
            self._emit_log("Creating new project")

        # Persist current settings
        self._config.save(self._state.project_dir / "_meta" / "settings.json")

        self._downloader = AssetDownloader(self._config, self._state)
        self._rewriter = PathRewriter(self._config, self._state)
        await self._downloader.start()

        # Seed the queue when starting fresh
        if not self._state.visited_urls and not self._state.pending_urls:
            normalized = self._normalizer.normalize(self._config.start_url)
            if normalized:
                self._state.add_pending(normalized, 0)

        start_time = time.monotonic()

        try:
            await self._crawl_loop()
        except Exception as exc:
            logger.exception("Crawl failed")
            self._emit_error(str(exc))
        finally:
            await self._finalize(start_time)

    def stop(self) -> None:
        """Request a graceful stop of the crawl loop.

        The loop will finish processing the current URL, save state, and
        exit.  Already-downloaded files are kept intact.
        """
        self._running = False
        self._stop_event.set()
        self._emit_log("Stopping crawl...")

    async def retry_failed(self) -> None:
        """Move all previously failed URLs back into the pending queue and restart."""
        if self._state is None:
            self._emit_error("No project loaded; cannot retry failed URLs.")
            return

        failed = dict(self._state.failed_urls)
        if not failed:
            self._emit_log("No failed URLs to retry.")
            return

        for url in failed:
            self._state.add_pending(url, 0)
        self._state.failed_urls.clear()
        self._emit_log(f"Re-queued {len(failed)} failed URLs")
        await self.start()

    # ------------------------------------------------------------------
    # Main crawl loop
    # ------------------------------------------------------------------

    async def _crawl_loop(self) -> None:
        """Process pending URLs until the queue is empty or a stop is requested."""
        assert self._state is not None
        assert self._downloader is not None
        assert self._rewriter is not None

        while self._state.pending_urls and self._running:
            if self._stop_event.is_set():
                break

            pending_item = self._state.get_pending()
            if pending_item is None:
                break

            url, depth = pending_item

            if self._state.is_visited(url):
                continue

            if depth > self._config.max_depth:
                logger.debug("Max depth (%d) reached for %s", self._config.max_depth, url)
                continue

            self._emit_url(url)
            self._emit_status(
                f"Fetching ({len(self._state.visited_urls)} done, "
                f"{len(self._state.pending_urls)} pending)"
            )

            try:
                await self._process_url(url, depth)
            except Exception:
                logger.exception("Error processing %s", url)
                self._state.add_failed(url, "Unhandled exception during processing")

            # Periodic state save
            self._urls_since_save += 1
            if self._urls_since_save >= _STATE_SAVE_INTERVAL:
                self._state.save_state()
                self._urls_since_save = 0

        # Final save after loop exits
        self._state.save_state()

    async def _process_url(self, url: str, depth: int) -> None:
        """Fetch a single URL, extract links/assets, and save the result."""
        assert self._state is not None
        assert self._downloader is not None
        assert self._rewriter is not None

        # ----- Fetch -----
        actual_url = url  # may differ from *url* after redirects
        if self._config.use_renderer:
            html = await self._downloader.fetch_with_renderer(url)
            if html is None:
                self._state.add_failed(url, "Renderer returned None")
                return
            result_text = html
            content_type = "text/html"
        else:
            result = await self._downloader.fetch_page(url)
            if result is None:
                self._state.add_failed(url, "Fetch returned None")
                return
            result_text = result.text
            content_type = result.content_type
            actual_url = result.url  # final URL after redirects

        if "text/html" not in content_type:
            logger.debug("Skipping non-HTML response: %s (%s)", url, content_type)
            # Mark as visited so we don't re-fetch on subsequent encounters
            self._state.visited_urls.add(url)
            if actual_url != url:
                self._state.visited_urls.add(actual_url)
            return

        # ----- Parse HTML -----
        # Use *actual_url* (after redirects, with trailing slash intact) as the
        # base for resolving relative links — this is critical for correctness.
        parse_result = self._html_parser.parse(result_text, actual_url)

        # ----- Save raw HTML (will be rewritten in the finalize step) -----
        # Fix charset declarations at save time so the file is viewable as
        # UTF-8 immediately, even if the crawl is interrupted before the
        # final rewrite pass runs.
        save_text = fix_charset_to_utf8(result_text)
        save_path = self._rewriter.compute_save_path(url)
        full_save = self._state.project_dir / save_path
        full_save.parent.mkdir(parents=True, exist_ok=True)
        full_save.write_text(save_text, encoding="utf-8")

        self._state.add_visited(url, str(save_path))

        # If the server redirected, also mark the final URL as visited so that
        # links pointing to the canonical URL don't trigger another fetch.
        if actual_url != url:
            normalized_actual = self._normalizer.normalize(actual_url)
            if normalized_actual and normalized_actual != url:
                self._state.add_visited(normalized_actual, str(save_path))

        # ----- Queue discovered links -----
        self._enqueue_discovered_links(parse_result.links, url, depth)

        # ----- Download referenced assets -----
        if self._config.fetch_external_assets:
            await self._download_assets(parse_result.assets, url)

        # ----- Process CSS files for nested asset references -----
        for asset in parse_result.assets:
            if asset.asset_type == "stylesheet":
                await self._process_css(asset.url)

        self._emit_progress(
            len(self._state.visited_urls),
            len(self._state.visited_urls) + len(self._state.pending_urls),
        )

    # ------------------------------------------------------------------
    # Link discovery
    # ------------------------------------------------------------------

    def _enqueue_discovered_links(
        self, links: list[str], page_url: str, current_depth: int
    ) -> None:
        """Normalize and enqueue same-host links found on a page."""
        assert self._state is not None

        ref_url = self._config.start_url
        for link in links:
            normalized = self._normalizer.normalize(link, page_url)
            if not normalized:
                continue
            if self._normalizer.is_excluded_scheme(normalized):
                continue
            if not self._normalizer.should_crawl(
                normalized, ref_url, self._config.include_subdomains
            ):
                continue
            if self._state.is_visited(normalized):
                continue

            # Check excluded path patterns
            if self._is_excluded_path(normalized):
                continue

            if self._config.strip_tracking_params:
                normalized = self._normalizer.strip_tracking_params(
                    normalized, self._config.tracking_params
                )

            self._state.add_pending(normalized, current_depth + 1)

    def _is_excluded_path(self, url: str) -> bool:
        """Return True if *url* matches any configured exclusion pattern."""
        for pattern in self._config.excluded_paths:
            if pattern in url:
                return True
        return False

    # ------------------------------------------------------------------
    # Asset downloading
    # ------------------------------------------------------------------

    async def _download_assets(self, assets: list, page_url: str) -> None:
        """Download all referenced assets that pass the configured filters."""
        assert self._state is not None
        assert self._downloader is not None
        assert self._rewriter is not None

        for asset in assets:
            url = self._normalizer.normalize(asset.url, page_url)
            if not url or self._normalizer.is_excluded_scheme(url):
                continue
            if self._state.is_visited(url):
                continue
            if not self._should_download_asset(asset.asset_type):
                continue

            # Check excluded extensions
            parsed = urlparse(url)
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in self._config.excluded_extensions):
                continue

            save_path = self._rewriter.compute_save_path(url)
            full_save = self._state.project_dir / save_path
            full_save.parent.mkdir(parents=True, exist_ok=True)

            success = await self._downloader.fetch_asset(url, full_save)
            if success:
                self._state.add_visited(url, str(save_path))
            else:
                self._state.add_failed(url, "Asset download failed")

    async def _process_css(self, css_url: str) -> None:
        """Parse a downloaded CSS file and download any assets it references."""
        assert self._state is not None

        css_url = self._normalizer.normalize(css_url)
        if not css_url:
            return

        save_path_str = self._state.url_map.get(css_url)
        if not save_path_str:
            return

        css_file = self._state.project_dir / save_path_str
        if not css_file.exists():
            return

        try:
            css_text = css_file.read_text(encoding="utf-8", errors="replace")
            css_assets = self._css_parser.parse(css_text, css_url)
            await self._download_assets(css_assets, css_url)
        except Exception as exc:
            logger.warning("Error parsing CSS %s: %s", css_url, exc)

    def _should_download_asset(self, asset_type: str) -> bool:
        """Check whether *asset_type* passes the configured asset filter."""
        filt = self._config.asset_filter
        if filt == "all":
            return True
        if filt == "none":
            return False
        if filt == "images":
            return asset_type in {"image", "icon"}
        if filt == "css_js":
            return asset_type in {"stylesheet", "script"}
        return True

    # ------------------------------------------------------------------
    # URL rewriting (post-crawl)
    # ------------------------------------------------------------------

    async def _rewrite_all(self) -> None:
        """Rewrite URLs in all saved HTML and CSS files to use local paths."""
        assert self._state is not None
        assert self._rewriter is not None

        rewritten_count = 0
        for url, local_path in list(self._state.url_map.items()):
            if not self._running and self._stop_event.is_set():
                break

            full_path = self._state.project_dir / local_path
            if not full_path.exists():
                continue

            # Use pathlib's suffix so we handle every HTML-ish extension
            # (.htm, .php, .asp, …) — abehiroshi-style frame pages use
            # ``.htm`` and were previously skipped entirely.
            suffix = Path(local_path).suffix.lower()

            try:
                if suffix in _HTML_SUFFIXES:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    rewritten = self._rewriter.rewrite_html(content, url, full_path)
                    full_path.write_text(rewritten, encoding="utf-8")
                    rewritten_count += 1
                elif suffix == ".css":
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    rewritten = self._rewriter.rewrite_css(content, url, full_path)
                    full_path.write_text(rewritten, encoding="utf-8")
                    rewritten_count += 1
            except Exception as exc:
                logger.warning("Error rewriting %s: %s", local_path, exc)

        logger.info("Rewrote URLs in %d files.", rewritten_count)

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    async def _finalize(self, start_time: float) -> None:
        """Save state, rewrite URLs, write summary, and shut down."""
        assert self._state is not None

        # Only rewrite if the crawl was not forcefully stopped
        if self._running:
            self._emit_status("Rewriting URLs...")
            await self._rewrite_all()

        elapsed = time.monotonic() - start_time
        self._state.save_state()

        summary = self._state.get_summary()
        summary["elapsed_seconds"] = round(elapsed, 2)

        summary_path = self._state.project_dir / "_meta" / "summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if self._downloader is not None:
            await self._downloader.close()

        self._running = False
        self._emit_log(f"Crawl complete: {summary}")

        if self.on_complete:
            self.on_complete()

    # ------------------------------------------------------------------
    # Callback helpers
    # ------------------------------------------------------------------

    def _emit_progress(self, current: int, total: int) -> None:
        if self.on_progress is not None:
            self.on_progress(current, total)

    def _emit_status(self, msg: str) -> None:
        if self.on_status is not None:
            self.on_status(msg)

    def _emit_url(self, url: str) -> None:
        if self.on_url_processing is not None:
            self.on_url_processing(url)

    def _emit_log(self, msg: str) -> None:
        logger.info(msg)
        if self.on_log is not None:
            self.on_log(msg)

    def _emit_error(self, msg: str) -> None:
        logger.error(msg)
        if self.on_error is not None:
            self.on_error(msg)
