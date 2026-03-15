"""Tests for sitepack.core.path_rewriter.PathRewriter."""

from __future__ import annotations

from pathlib import Path

from sitepack.core.config import AppConfig
from sitepack.core.path_rewriter import PathRewriter
from sitepack.core.project_state import ProjectState
from sitepack.core.url_normalizer import UrlNormalizer


def _make_rewriter(tmp_path: Path) -> PathRewriter:
    """Create a PathRewriter with test defaults."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        start_url="https://example.com",
        output_dir=output_dir,
        project_name="test",
    )
    state = ProjectState(tmp_path / "state")
    state.meta_dir.mkdir(parents=True, exist_ok=True)
    return PathRewriter(config, state)


class TestComputeSavePath:
    """Tests for PathRewriter.compute_save_path()."""

    def test_compute_save_path_root(self, tmp_path: Path) -> None:
        rw = _make_rewriter(tmp_path)
        path = rw.compute_save_path("https://example.com/")
        assert path.name == "index.html"
        # Root page lands at the output root, not under pages/
        assert "pages" not in str(path.relative_to(tmp_path / "output"))

    def test_compute_save_path_same_host_page(self, tmp_path: Path) -> None:
        rw = _make_rewriter(tmp_path)
        path = rw.compute_save_path("https://example.com/about")
        assert "pages" in str(path)

    def test_compute_save_path_same_host_asset(self, tmp_path: Path) -> None:
        rw = _make_rewriter(tmp_path)
        path = rw.compute_save_path("https://example.com/css/style.css")
        assert "assets" in str(path)

    def test_compute_save_path_external_asset(self, tmp_path: Path) -> None:
        rw = _make_rewriter(tmp_path)
        path = rw.compute_save_path("https://cdn.other.com/lib.js")
        assert "external" in str(path)
        assert "cdn.other.com" in str(path)


class TestComputeRelativePath:
    """Tests for PathRewriter.compute_relative_path()."""

    def test_compute_relative_path(self, tmp_path: Path) -> None:
        rw = _make_rewriter(tmp_path)
        from_file = tmp_path / "output" / "pages" / "about" / "index.html"
        to_file = tmp_path / "output" / "assets" / "style.css"
        rel = rw.compute_relative_path(from_file, to_file)
        assert ".." in rel
        assert "assets" in rel
        assert "style.css" in rel

    def test_compute_relative_path_same_dir(self, tmp_path: Path) -> None:
        rw = _make_rewriter(tmp_path)
        from_file = tmp_path / "output" / "pages" / "a.html"
        to_file = tmp_path / "output" / "pages" / "b.html"
        rel = rw.compute_relative_path(from_file, to_file)
        assert rel == "b.html"


class TestExternalAssetsDesign:
    """Verify the key architectural property: external assets are saved
    locally but external HTML pages are not followed during crawling."""

    def test_external_assets_saved_but_external_html_not_crawled(
        self, tmp_path: Path,
    ) -> None:
        """External CSS/JS/images get a save path under external/ but
        UrlNormalizer.should_crawl rejects external HTML pages."""
        rw = _make_rewriter(tmp_path)

        # External asset (CSS) gets a valid local save path
        ext_asset_url = "https://cdn.other.com/styles/main.css"
        save_path = rw.compute_save_path(ext_asset_url)
        assert "external" in str(save_path)
        assert save_path.suffix == ".css"

        # External HTML page is NOT crawlable
        ext_page_url = "https://other.com/page"
        assert not UrlNormalizer.should_crawl(
            ext_page_url, "https://example.com/",
        )

        # Same-host HTML page IS crawlable
        same_page = "https://example.com/blog"
        assert UrlNormalizer.should_crawl(
            same_page, "https://example.com/",
        )
