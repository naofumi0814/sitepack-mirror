"""Tests for sitepack.core.css_parser.CssAssetParser."""

from __future__ import annotations

from sitepack.core.css_parser import CssAssetParser

BASE_URL = "https://example.com/css/main.css"


def _make_parser() -> CssAssetParser:
    return CssAssetParser()


class TestParseCss:
    """Tests for CssAssetParser.parse()."""

    def test_parse_url_references(self) -> None:
        css = "body { background: url('/images/bg.png') no-repeat; }"
        assets = _make_parser().parse(css, BASE_URL)
        urls = [a.url for a in assets]
        assert any("bg.png" in u for u in urls)

    def test_parse_import(self) -> None:
        css = "@import url('reset.css');"
        assets = _make_parser().parse(css, BASE_URL)
        urls = [a.url for a in assets]
        assert any("reset.css" in u for u in urls)
        types = [a.asset_type for a in assets if "reset.css" in a.url]
        assert "stylesheet" in types

    def test_parse_font_face(self) -> None:
        css = """@font-face {
            font-family: 'MyFont';
            src: url('fonts/my.woff2') format('woff2');
        }"""
        assets = _make_parser().parse(css, BASE_URL)
        urls = [a.url for a in assets]
        assert any("my.woff2" in u for u in urls)
        types = [a.asset_type for a in assets if "my.woff2" in a.url]
        assert "font" in types

    def test_parse_resolves_relative(self) -> None:
        css = "body { background: url('../images/bg.png'); }"
        assets = _make_parser().parse(css, BASE_URL)
        urls = [a.url for a in assets]
        # ../images/bg.png relative to /css/main.css -> /images/bg.png
        assert any("example.com/images/bg.png" in u for u in urls)

    def test_parse_import_string_form(self) -> None:
        css = "@import 'typography.css';"
        assets = _make_parser().parse(css, BASE_URL)
        urls = [a.url for a in assets]
        assert any("typography.css" in u for u in urls)


class TestRewriteCss:
    """Tests for CssAssetParser.rewrite_urls()."""

    def test_rewrite_urls(self) -> None:
        css = "body { background: url('/images/bg.png'); }"
        url_map = {"https://example.com/images/bg.png": "../assets/bg.png"}
        result = _make_parser().rewrite_urls(css, url_map, BASE_URL)
        assert "../assets/bg.png" in result

    def test_rewrite_import(self) -> None:
        css = "@import 'reset.css';"
        resolved = "https://example.com/css/reset.css"
        url_map = {resolved: "../assets/reset.css"}
        result = _make_parser().rewrite_urls(css, url_map, BASE_URL)
        assert "../assets/reset.css" in result

    def test_rewrite_preserves_structure(self) -> None:
        css = """\
body {
    color: red;
    background: url('/images/bg.png');
    font-size: 16px;
}"""
        url_map = {"https://example.com/images/bg.png": "../assets/bg.png"}
        result = _make_parser().rewrite_urls(css, url_map, BASE_URL)
        # CSS structure preserved: color and font-size still present
        assert "color: red" in result
        assert "font-size: 16px" in result
        assert "../assets/bg.png" in result
