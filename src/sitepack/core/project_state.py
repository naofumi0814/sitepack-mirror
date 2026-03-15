"""Project state management for sitepack crawl sessions."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

from sitepack.core.config import AppConfig

logger = logging.getLogger(__name__)


class ProjectState:
    """Tracks crawl progress and manages the project directory layout.

    Directory structure::

        <project_name>/
            pages/          -- downloaded HTML pages
            assets/         -- local assets (CSS, JS, images, fonts)
            external/       -- assets fetched from external hosts
            _meta/          -- internal bookkeeping
                manifest.json
                url_map.json
                errors.json
                summary.json
                settings.json
                crawl_log.jsonl
    """

    SUBDIRS = ("pages", "assets", "external", "_meta")

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.pages_dir = self.project_dir / "pages"
        self.assets_dir = self.project_dir / "assets"
        self.external_dir = self.project_dir / "external"
        self.meta_dir = self.project_dir / "_meta"

        self.visited_urls: set[str] = set()
        self.pending_urls: deque[tuple[str, int]] = deque()  # (url, depth)
        self.failed_urls: dict[str, str] = {}  # url -> error message
        self.asset_hashes: dict[str, str] = {}  # content hash -> local path
        self.url_map: dict[str, str] = {}  # url -> local relative path

        self._start_time: float = time.monotonic()
        self._config: AppConfig | None = None

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def create_project(
        cls,
        output_dir: Path,
        project_name: str,
        config: AppConfig | None = None,
    ) -> "ProjectState":
        """Create a new project directory and return a fresh state object."""
        output_dir = Path(output_dir)
        project_dir = output_dir / project_name

        if project_dir.exists():
            logger.warning("Project directory already exists: %s", project_dir)
        for subdir in cls.SUBDIRS:
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)

        state = cls(project_dir)
        state._start_time = time.monotonic()
        state._config = config

        # Persist initial settings
        if config is not None:
            state._write_json(state.meta_dir / "settings.json", config.to_dict())

        state._write_json(state.meta_dir / "manifest.json", {
            "project_name": project_name,
            "created_at": time.time(),
            "version": "1",
        })

        logger.info("Created project at %s", project_dir)
        return state

    @classmethod
    def load_project(cls, project_dir: Path) -> "ProjectState":
        """Restore a previously saved project state from disk."""
        project_dir = Path(project_dir)
        meta = project_dir / "_meta"

        if not meta.is_dir():
            raise FileNotFoundError(
                f"Not a valid sitepack project (missing _meta/): {project_dir}"
            )

        state = cls(project_dir)

        # Restore url_map
        url_map_path = meta / "url_map.json"
        if url_map_path.exists():
            state.url_map = state._read_json(url_map_path)
            state.visited_urls = set(state.url_map.keys())
            logger.info("Restored %d visited URLs from url_map", len(state.visited_urls))

        # Restore failed URLs
        errors_path = meta / "errors.json"
        if errors_path.exists():
            state.failed_urls = state._read_json(errors_path)
            logger.info("Restored %d failed URLs", len(state.failed_urls))

        # Restore asset hashes from manifest if available
        manifest_path = meta / "manifest.json"
        if manifest_path.exists():
            manifest = state._read_json(manifest_path)
            state.asset_hashes = manifest.get("asset_hashes", {})

        # Restore settings
        settings_path = meta / "settings.json"
        if settings_path.exists():
            state._config = AppConfig.from_dict(state._read_json(settings_path))

        logger.info("Loaded project from %s", project_dir)
        return state

    # ------------------------------------------------------------------
    # URL tracking
    # ------------------------------------------------------------------

    def add_visited(self, url: str, local_path: str) -> None:
        """Record a successfully downloaded URL and its local path."""
        self.visited_urls.add(url)
        self.url_map[url] = local_path
        self._append_crawl_log({
            "action": "visited",
            "url": url,
            "local_path": local_path,
            "timestamp": time.time(),
        })
        logger.debug("Visited: %s -> %s", url, local_path)

    def add_failed(self, url: str, error: str) -> None:
        """Record a URL that could not be downloaded."""
        self.failed_urls[url] = error
        self._append_crawl_log({
            "action": "failed",
            "url": url,
            "error": error,
            "timestamp": time.time(),
        })
        logger.warning("Failed: %s — %s", url, error)

    def is_visited(self, url: str) -> bool:
        """Check whether a URL has already been downloaded."""
        return url in self.visited_urls

    def get_pending(self) -> tuple[str, int] | None:
        """Pop the next pending URL and its depth, or return None."""
        if not self.pending_urls:
            return None
        return self.pending_urls.popleft()

    def add_pending(self, url: str, depth: int) -> None:
        """Enqueue a URL for future crawling if it has not been seen."""
        if url in self.visited_urls:
            return
        if url in self.failed_urls:
            return
        # Avoid duplicate entries in the queue
        if any(u == url for u, _ in self.pending_urls):
            return
        self.pending_urls.append((url, depth))
        logger.debug("Queued (depth=%d): %s", depth, url)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_state(self) -> None:
        """Persist the current state to the _meta/ directory."""
        self._write_json(self.meta_dir / "url_map.json", self.url_map)
        self._write_json(self.meta_dir / "errors.json", self.failed_urls)
        self._write_json(self.meta_dir / "summary.json", self.get_summary())

        # Update manifest with asset hashes
        manifest_path = self.meta_dir / "manifest.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            manifest = self._read_json(manifest_path)
        manifest["asset_hashes"] = self.asset_hashes
        manifest["last_saved_at"] = time.time()
        self._write_json(manifest_path, manifest)

        if self._config is not None:
            self._write_json(self.meta_dir / "settings.json", self._config.to_dict())

        logger.info("Project state saved to %s", self.meta_dir)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary dict with counts and timing information."""
        elapsed = time.monotonic() - self._start_time
        return {
            "visited_count": len(self.visited_urls),
            "pending_count": len(self.pending_urls),
            "failed_count": len(self.failed_urls),
            "asset_count": len(self.asset_hashes),
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        """Write data as pretty-printed JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> Any:
        """Read and parse a JSON file."""
        text = path.read_text(encoding="utf-8")
        return json.loads(text)

    def _append_crawl_log(self, entry: dict[str, Any]) -> None:
        """Append a single JSON line to crawl_log.jsonl."""
        log_path = self.meta_dir / "crawl_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
