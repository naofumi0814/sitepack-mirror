"""Comprehensive tests for sitepack.core.url_normalizer.UrlNormalizer."""

from __future__ import annotations

from pathlib import Path

from sitepack.core.url_normalizer import UrlNormalizer


class TestNormalize:
    """Tests for UrlNormalizer.normalize()."""

    def test_normalize_basic(self) -> None:
        result = UrlNormalizer.normalize("https://example.com/page")
        assert result == "https://example.com/page"

    def test_normalize_relative_url(self) -> None:
        result = UrlNormalizer.normalize(
            "../other.html",
            base_url="https://example.com/dir/page.html",
        )
        assert result == "https://example.com/other.html"

    def test_normalize_strip_fragment(self) -> None:
        result = UrlNormalizer.normalize("https://example.com/page#section")
        assert "#" not in result
        assert result == "https://example.com/page"

    def test_normalize_trailing_slash(self) -> None:
        # Root URL keeps its slash; normalised path should be consistent.
        a = UrlNormalizer.normalize("https://example.com/")
        b = UrlNormalizer.normalize("https://example.com")
        assert a == b

    def test_normalize_protocol_relative(self) -> None:
        result = UrlNormalizer.normalize(
            "//cdn.example.com/lib.js",
            base_url="https://example.com/",
        )
        assert result.startswith("https://")
        assert "cdn.example.com/lib.js" in result


class TestHostComparison:
    """Tests for is_same_host and is_subdomain."""

    def test_is_same_host(self) -> None:
        assert UrlNormalizer.is_same_host(
            "https://example.com/a",
            "https://example.com/b",
        )

    def test_is_same_host_different(self) -> None:
        assert not UrlNormalizer.is_same_host(
            "https://other.com/a",
            "https://example.com/b",
        )

    def test_is_subdomain(self) -> None:
        assert UrlNormalizer.is_subdomain(
            "https://blog.example.com/post",
            "https://example.com/",
        )

    def test_is_subdomain_exact_match_is_not_subdomain(self) -> None:
        assert not UrlNormalizer.is_subdomain(
            "https://example.com/a",
            "https://example.com/b",
        )


class TestShouldCrawl:
    """Tests for UrlNormalizer.should_crawl()."""

    def test_should_crawl_same_host(self) -> None:
        assert UrlNormalizer.should_crawl(
            "https://example.com/about",
            "https://example.com/",
        )

    def test_should_crawl_external_blocked(self) -> None:
        assert not UrlNormalizer.should_crawl(
            "https://other.com/page",
            "https://example.com/",
        )

    def test_should_crawl_subdomain_off(self) -> None:
        assert not UrlNormalizer.should_crawl(
            "https://blog.example.com/post",
            "https://example.com/",
            include_subdomains=False,
        )

    def test_should_crawl_subdomain_on(self) -> None:
        assert UrlNormalizer.should_crawl(
            "https://blog.example.com/post",
            "https://example.com/",
            include_subdomains=True,
        )

    def test_should_crawl_skips_assets(self) -> None:
        assert not UrlNormalizer.should_crawl(
            "https://example.com/image.png",
            "https://example.com/",
        )


class TestIsExcludedScheme:
    """Tests for UrlNormalizer.is_excluded_scheme()."""

    def test_is_excluded_scheme_mailto(self) -> None:
        assert UrlNormalizer.is_excluded_scheme("mailto:user@example.com")

    def test_is_excluded_scheme_javascript(self) -> None:
        assert UrlNormalizer.is_excluded_scheme("javascript:void(0)")

    def test_is_excluded_scheme_data(self) -> None:
        assert UrlNormalizer.is_excluded_scheme("data:image/png;base64,abc")

    def test_is_excluded_scheme_http(self) -> None:
        assert not UrlNormalizer.is_excluded_scheme("https://example.com")


class TestUrlToFilepath:
    """Tests for UrlNormalizer.url_to_filepath()."""

    def test_url_to_filepath_basic(self) -> None:
        result = UrlNormalizer.url_to_filepath(
            "https://example.com/page.html",
            "example.com",
        )
        assert isinstance(result, Path)
        assert str(result).startswith("pages")
        assert result.name == "page.html"

    def test_url_to_filepath_with_query(self) -> None:
        result = UrlNormalizer.url_to_filepath(
            "https://example.com/page.html?id=42",
            "example.com",
        )
        # Query params encoded as hash suffix
        assert "_q" in result.name
        assert result.suffix == ".html"

    def test_url_to_filepath_no_extension(self) -> None:
        result = UrlNormalizer.url_to_filepath(
            "https://example.com/about",
            "example.com",
        )
        assert result.name == "index.html"

    def test_url_to_filepath_root(self) -> None:
        result = UrlNormalizer.url_to_filepath(
            "https://example.com/",
            "example.com",
        )
        assert result.name == "index.html"

    def test_url_to_filepath_external(self) -> None:
        result = UrlNormalizer.url_to_filepath(
            "https://cdn.other.com/lib.js",
            "example.com",
        )
        assert "external" in str(result)
        assert "cdn.other.com" in str(result)


class TestTrailingSlashPreservation:
    """Trailing slashes on non-root paths must be preserved because they
    change how ``urljoin`` resolves relative URLs.  ``/blog/`` and
    ``/blog`` are distinct resources on many servers."""

    def test_non_root_trailing_slash_preserved(self) -> None:
        result = UrlNormalizer.normalize("https://example.com/blog/")
        assert result.endswith("/blog/")

    def test_non_root_without_trailing_slash(self) -> None:
        result = UrlNormalizer.normalize("https://example.com/blog")
        assert result.endswith("/blog")
        assert not result.endswith("/blog/")

    def test_root_slash_still_consistent(self) -> None:
        a = UrlNormalizer.normalize("https://example.com/")
        b = UrlNormalizer.normalize("https://example.com")
        assert a == b

    def test_deep_path_trailing_slash_preserved(self) -> None:
        result = UrlNormalizer.normalize("https://example.com/a/b/c/")
        assert result.endswith("/a/b/c/")


class TestStripTrackingParams:
    """Tests for UrlNormalizer.strip_tracking_params()."""

    def test_strip_tracking_params(self) -> None:
        url = "https://example.com/page?utm_source=twitter&utm_medium=social&id=42"
        result = UrlNormalizer.strip_tracking_params(
            url, ["utm_source", "utm_medium"],
        )
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=42" in result

    def test_strip_tracking_params_preserves_others(self) -> None:
        url = "https://example.com/page?q=hello&utm_campaign=test&page=2"
        result = UrlNormalizer.strip_tracking_params(url, ["utm_campaign"])
        assert "utm_campaign" not in result
        assert "q=hello" in result
        assert "page=2" in result
