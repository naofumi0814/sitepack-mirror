"""Tests for sitepack.core.project_state.ProjectState."""

from __future__ import annotations

from pathlib import Path

from sitepack.core.config import AppConfig
from sitepack.core.project_state import ProjectState


class TestCreateProjectStructure:
    """Tests for ProjectState.create_project()."""

    def test_create_project_structure(self, tmp_path: Path) -> None:
        config = AppConfig(
            start_url="https://example.com",
            output_dir=tmp_path,
            project_name="mysite",
        )
        state = ProjectState.create_project(tmp_path, "mysite", config)

        project_dir = tmp_path / "mysite"
        assert project_dir.is_dir()
        for subdir in ProjectState.SUBDIRS:
            assert (project_dir / subdir).is_dir(), f"Missing subdirectory: {subdir}"

        # Meta files should have been created
        assert (project_dir / "_meta" / "manifest.json").exists()
        assert (project_dir / "_meta" / "settings.json").exists()

        # State object should reference the correct directories
        assert state.project_dir == project_dir
        assert state.pages_dir == project_dir / "pages"
        assert state.assets_dir == project_dir / "assets"
        assert state.external_dir == project_dir / "external"
        assert state.meta_dir == project_dir / "_meta"


class TestVisitedTracking:
    """Tests for add_visited / is_visited."""

    def test_add_visited_and_is_visited(self, tmp_path: Path) -> None:
        state = ProjectState.create_project(tmp_path, "proj")
        url = "https://example.com/page"
        local = "pages/page/index.html"

        assert not state.is_visited(url)
        state.add_visited(url, local)
        assert state.is_visited(url)
        assert state.url_map[url] == local


class TestPendingQueue:
    """Tests for add_pending / get_pending."""

    def test_add_pending_and_get_pending(self, tmp_path: Path) -> None:
        state = ProjectState.create_project(tmp_path, "proj")

        state.add_pending("https://example.com/a", depth=0)
        state.add_pending("https://example.com/b", depth=1)

        first = state.get_pending()
        assert first is not None
        assert first == ("https://example.com/a", 0)

        second = state.get_pending()
        assert second is not None
        assert second == ("https://example.com/b", 1)

        # Queue is now empty
        assert state.get_pending() is None

    def test_add_pending_skips_visited(self, tmp_path: Path) -> None:
        state = ProjectState.create_project(tmp_path, "proj")
        url = "https://example.com/page"

        state.add_visited(url, "pages/page/index.html")
        state.add_pending(url, depth=0)

        # Should not be queued since it is already visited
        assert state.get_pending() is None

    def test_add_pending_skips_duplicates(self, tmp_path: Path) -> None:
        state = ProjectState.create_project(tmp_path, "proj")
        url = "https://example.com/page"

        state.add_pending(url, depth=0)
        state.add_pending(url, depth=1)  # duplicate, should be ignored

        first = state.get_pending()
        assert first == (url, 0)
        assert state.get_pending() is None  # no second entry


class TestAddFailed:
    """Tests for add_failed."""

    def test_add_failed(self, tmp_path: Path) -> None:
        state = ProjectState.create_project(tmp_path, "proj")
        url = "https://example.com/broken"
        error = "404 Not Found"

        state.add_failed(url, error)
        assert url in state.failed_urls
        assert state.failed_urls[url] == error

    def test_add_pending_skips_failed(self, tmp_path: Path) -> None:
        state = ProjectState.create_project(tmp_path, "proj")
        url = "https://example.com/broken"

        state.add_failed(url, "500 Server Error")
        state.add_pending(url, depth=0)

        # Should not be queued since it previously failed
        assert state.get_pending() is None


class TestSaveAndLoad:
    """Tests for save_state / load_project persistence roundtrip."""

    def test_save_and_load_state(self, tmp_path: Path) -> None:
        config = AppConfig(
            start_url="https://example.com",
            output_dir=tmp_path,
            project_name="persist_test",
        )
        state = ProjectState.create_project(tmp_path, "persist_test", config)

        state.add_visited("https://example.com/", "index.html")
        state.add_visited("https://example.com/about", "pages/about/index.html")
        state.add_failed("https://example.com/missing", "404 Not Found")
        state.save_state()

        # Load from disk into a fresh state object
        loaded = ProjectState.load_project(tmp_path / "persist_test")

        assert loaded.is_visited("https://example.com/")
        assert loaded.is_visited("https://example.com/about")
        assert "https://example.com/missing" in loaded.failed_urls
        assert loaded.failed_urls["https://example.com/missing"] == "404 Not Found"
        assert loaded.url_map["https://example.com/"] == "index.html"
        assert loaded.url_map["https://example.com/about"] == "pages/about/index.html"


class TestUrlMap:
    """Tests for URL mapping maintenance."""

    def test_url_map_maintained(self, tmp_path: Path) -> None:
        state = ProjectState.create_project(tmp_path, "proj")

        state.add_visited("https://example.com/", "index.html")
        state.add_visited("https://example.com/about", "pages/about/index.html")
        state.add_visited("https://cdn.other.com/style.css", "external/cdn.other.com/style.css")

        assert len(state.url_map) == 3
        assert state.url_map["https://example.com/"] == "index.html"
        assert state.url_map["https://example.com/about"] == "pages/about/index.html"
        assert state.url_map["https://cdn.other.com/style.css"] == "external/cdn.other.com/style.css"

        # All visited URLs should be tracked
        assert state.is_visited("https://example.com/")
        assert state.is_visited("https://example.com/about")
        assert state.is_visited("https://cdn.other.com/style.css")
        assert not state.is_visited("https://example.com/nonexistent")
