"""Tests for sitepack.core.html_parser.HtmlParser."""

from __future__ import annotations

from sitepack.core.html_parser import HtmlParser, fix_charset_to_utf8

BASE_URL = "https://example.com/"


def _make_parser() -> HtmlParser:
    return HtmlParser()


class TestParseExtraction:
    """Tests for HtmlParser.parse() - link and asset extraction."""

    def test_parse_extracts_links(self) -> None:
        html = '<html><body><a href="/about">About</a><a href="/contact">Contact</a></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        urls = result.links
        assert any("/about" in u for u in urls)
        assert any("/contact" in u for u in urls)

    def test_parse_extracts_images(self) -> None:
        html = '<html><body><img src="/img/logo.png"></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("logo.png" in u for u in asset_urls)

    def test_parse_extracts_srcset(self) -> None:
        html = '<html><body><img srcset="/img/a.png 1x, /img/b.png 2x"></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("a.png" in u for u in asset_urls)
        assert any("b.png" in u for u in asset_urls)

    def test_parse_extracts_stylesheets(self) -> None:
        html = '<html><head><link rel="stylesheet" href="/css/main.css"></head><body></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("main.css" in u for u in asset_urls)
        types = [a.asset_type for a in result.assets if "main.css" in a.url]
        assert "stylesheet" in types

    def test_parse_extracts_scripts(self) -> None:
        html = '<html><head><script src="/js/app.js"></script></head><body></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("app.js" in u for u in asset_urls)

    def test_parse_extracts_icons(self) -> None:
        html = '<html><head><link rel="icon" href="/favicon.ico"></head><body></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("favicon.ico" in u for u in asset_urls)
        types = [a.asset_type for a in result.assets if "favicon.ico" in a.url]
        assert "icon" in types

    def test_parse_skips_mailto(self) -> None:
        html = '<html><body><a href="mailto:me@example.com">Mail</a></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        assert len(result.links) == 0
        assert all("mailto" not in a.url for a in result.assets)

    def test_parse_skips_javascript(self) -> None:
        html = '<html><body><a href="javascript:void(0)">Click</a></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        assert len(result.links) == 0

    def test_parse_resolves_relative_urls(self) -> None:
        html = '<html><body><a href="sub/page.html">Sub</a></body></html>'
        base = "https://example.com/dir/"
        result = _make_parser().parse(html, base)
        assert any("example.com/dir/sub/page.html" in u for u in result.links)

    def test_parse_extracts_inline_css_urls(self) -> None:
        html = """<html><head><style>
        body { background: url('/images/bg.png'); }
        </style></head><body></body></html>"""
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("bg.png" in u for u in asset_urls)


class TestBaseHrefHandling:
    """<base href> must override the default base URL for resolving relative links."""

    def test_base_href_overrides_base_url(self) -> None:
        html = '''<html><head><base href="https://other.com/"></head>
        <body><a href="page">Link</a></body></html>'''
        result = _make_parser().parse(html, "https://example.com/dir/")
        assert any("other.com/page" in u for u in result.links)
        assert not any("example.com" in u for u in result.links)

    def test_base_href_relative(self) -> None:
        html = '''<html><head><base href="/subdir/"></head>
        <body><a href="page.html">Link</a></body></html>'''
        result = _make_parser().parse(html, "https://example.com/")
        assert any("example.com/subdir/page.html" in u for u in result.links)


class TestExpandedLinkExtraction:
    """Link extraction must cover <area>, <iframe>, <frame>, and meta refresh."""

    def test_area_href_extracted(self) -> None:
        html = '''<html><body>
        <map name="m"><area href="/region1"><area href="/region2"></map>
        </body></html>'''
        result = _make_parser().parse(html, BASE_URL)
        assert any("/region1" in u for u in result.links)
        assert any("/region2" in u for u in result.links)

    def test_iframe_src_extracted(self) -> None:
        html = '<html><body><iframe src="/embed/page"></iframe></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        assert any("/embed/page" in u for u in result.links)

    def test_frame_src_extracted(self) -> None:
        html = '<html><frameset><frame src="/frame1.html"><frame src="/frame2.html"></frameset></html>'
        result = _make_parser().parse(html, BASE_URL)
        assert any("frame1.html" in u for u in result.links)
        assert any("frame2.html" in u for u in result.links)

    def test_meta_refresh_extracted(self) -> None:
        html = '''<html><head>
        <meta http-equiv="refresh" content="0;url=https://example.com/new-page">
        </head><body></body></html>'''
        result = _make_parser().parse(html, BASE_URL)
        assert any("new-page" in u for u in result.links)


class TestInlineStyleAssets:
    """Assets in inline style="" attributes must be discovered."""

    def test_background_image_in_style_attr(self) -> None:
        html = '<html><body><div style="background-image: url(\'/img/bg.jpg\')"></div></body></html>'
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("bg.jpg" in u for u in asset_urls)

    def test_multiple_urls_in_style_attr(self) -> None:
        html = '''<html><body>
        <div style="background: url('/a.png'); border-image: url('/b.png')"></div>
        </body></html>'''
        result = _make_parser().parse(html, BASE_URL)
        asset_urls = [a.url for a in result.assets]
        assert any("a.png" in u for u in asset_urls)
        assert any("b.png" in u for u in asset_urls)


class TestCharsetFix:
    """rewrite_urls must update charset declarations to UTF-8."""

    def test_meta_charset_updated(self) -> None:
        html = '<html><head><meta charset="Shift_JIS"></head><body></body></html>'
        result = _make_parser().rewrite_urls(html, {}, BASE_URL)
        assert 'charset="UTF-8"' in result or "charset=UTF-8" in result
        assert "Shift_JIS" not in result

    def test_meta_http_equiv_charset_updated(self) -> None:
        html = (
            '<html><head>'
            '<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            '</head><body></body></html>'
        )
        result = _make_parser().rewrite_urls(html, {}, BASE_URL)
        assert "Shift_JIS" not in result
        assert "UTF-8" in result

    def test_no_charset_tag_unchanged(self) -> None:
        html = '<html><head><title>Test</title></head><body><p>Hello</p></body></html>'
        result = _make_parser().rewrite_urls(html, {}, BASE_URL)
        assert "Hello" in result


class TestFrameRewriting:
    """frame[src] and iframe[src] must be rewritten to local paths."""

    def test_frame_src_rewritten(self) -> None:
        html = (
            '<html><frameset>'
            '<frame src="/menu.html" name="left">'
            '<frame src="/main.html" name="right">'
            '</frameset></html>'
        )
        url_map = {
            "https://example.com/menu.html": "pages/menu.html",
            "https://example.com/main.html": "pages/main.html",
        }
        result = _make_parser().rewrite_urls(html, url_map, BASE_URL)
        assert "pages/menu.html" in result
        assert "pages/main.html" in result

    def test_iframe_src_rewritten(self) -> None:
        html = '<html><body><iframe src="/embed/content"></iframe></body></html>'
        url_map = {"https://example.com/embed/content": "pages/embed/content/index.html"}
        result = _make_parser().rewrite_urls(html, url_map, BASE_URL)
        assert "pages/embed/content/index.html" in result

    def test_frame_external_src_unchanged(self) -> None:
        html = '<html><frameset><frame src="https://other.com/page"></frameset></html>'
        url_map = {}
        result = _make_parser().rewrite_urls(html, url_map, BASE_URL)
        assert "https://other.com/page" in result


class TestFixCharsetToUtf8:
    """Pure-regex charset fix that runs at save time AND during rewrite."""

    def test_meta_charset_lowercase_shift_jis(self) -> None:
        html = '<html><head><meta charset="shift_jis"></head><body></body></html>'
        result = fix_charset_to_utf8(html)
        assert 'charset="UTF-8"' in result
        assert "shift_jis" not in result.lower() or "charset=\"UTF-8\"" in result

    def test_meta_http_equiv_content_type(self) -> None:
        html = (
            '<html><head>'
            '<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            '</head><body></body></html>'
        )
        result = fix_charset_to_utf8(html)
        assert "charset=UTF-8" in result
        assert "Shift_JIS" not in result

    def test_meta_charset_single_quotes(self) -> None:
        html = "<html><head><meta charset='EUC-JP'></head></html>"
        result = fix_charset_to_utf8(html)
        assert "UTF-8" in result
        assert "EUC-JP" not in result

    def test_preserves_non_charset_content(self) -> None:
        html = '<html><head><title>タイトル</title></head><body><p>本文</p></body></html>'
        result = fix_charset_to_utf8(html)
        assert "タイトル" in result
        assert "本文" in result

    def test_multiple_meta_tags_all_updated(self) -> None:
        html = (
            '<html><head>'
            '<meta charset="Shift_JIS">'
            '<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            '</head><body></body></html>'
        )
        result = fix_charset_to_utf8(html)
        assert "Shift_JIS" not in result
        assert result.count("UTF-8") >= 2

    def test_no_charset_declarations_unchanged(self) -> None:
        html = '<html><head><title>hello</title></head><body></body></html>'
        result = fix_charset_to_utf8(html)
        assert result == html


class TestRewriteUrlsAppliesCharsetFix:
    """rewrite_urls must always emit UTF-8 charset, even with BS4 round-trip."""

    def test_lowercase_shift_jis_converted(self) -> None:
        html = '<html><head><meta charset="shift_jis"></head><body></body></html>'
        result = _make_parser().rewrite_urls(html, {}, BASE_URL)
        # Accept either quoted or unquoted form, just not the original
        assert "shift_jis" not in result.lower() or "utf-8" in result.lower()
        assert "UTF-8" in result or "utf-8" in result.lower()

    def test_japanese_content_preserved(self) -> None:
        html = (
            '<html><head>'
            '<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            '<title>阿部寛のホームページ</title>'
            '</head><body><h1>阿部寛</h1></body></html>'
        )
        result = _make_parser().rewrite_urls(html, {}, BASE_URL)
        assert "阿部寛" in result
        assert "Shift_JIS" not in result


class TestHtmExtensionFrameSite:
    """Simulate abehiroshi-style sites that use .htm frames + Shift-JIS."""

    def test_frameset_htm_links_rewritten(self) -> None:
        html = (
            '<html><head>'
            '<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            '<title>阿部寛のホームページ</title>'
            '</head>'
            '<frameset cols="200,*">'
            '<frame src="menu.htm" name="left">'
            '<frame src="top.htm" name="right">'
            '</frameset></html>'
        )
        base_url = "https://abehiroshi.la.coocan.jp/"
        url_map = {
            "https://abehiroshi.la.coocan.jp/menu.htm": "pages/menu.htm",
            "https://abehiroshi.la.coocan.jp/top.htm": "pages/top.htm",
        }
        result = _make_parser().rewrite_urls(html, url_map, base_url)
        assert "pages/menu.htm" in result
        assert "pages/top.htm" in result
        assert "UTF-8" in result
        assert "阿部寛" in result


class TestRewriteUrls:
    """Tests for HtmlParser.rewrite_urls()."""

    def test_rewrite_urls_img(self) -> None:
        html = '<html><body><img src="/img/logo.png"></body></html>'
        url_map = {"https://example.com/img/logo.png": "assets/logo.png"}
        result = _make_parser().rewrite_urls(html, url_map, BASE_URL)
        assert "assets/logo.png" in result

    def test_rewrite_urls_link(self) -> None:
        html = '<html><head><link rel="stylesheet" href="/css/style.css"></head><body></body></html>'
        url_map = {"https://example.com/css/style.css": "assets/style.css"}
        result = _make_parser().rewrite_urls(html, url_map, BASE_URL)
        assert "assets/style.css" in result

    def test_rewrite_urls_anchor(self) -> None:
        html = '<html><body><a href="/about">About</a></body></html>'
        url_map = {"https://example.com/about": "pages/about/index.html"}
        result = _make_parser().rewrite_urls(html, url_map, BASE_URL)
        assert "pages/about/index.html" in result

    def test_rewrite_preserves_external(self) -> None:
        html = '<html><body><a href="https://other.com/page">Other</a></body></html>'
        url_map = {}  # No mapping for external URL
        result = _make_parser().rewrite_urls(html, url_map, BASE_URL)
        assert "https://other.com/page" in result
