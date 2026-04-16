"""Tests for the charset-detection helpers in ``asset_downloader``.

These regression-test the bugs that caused persistent mojibake on Japanese
legacy sites (abehiroshi.la.coocan.jp and similar) when the server omits
``charset`` from the ``Content-Type`` header.
"""

from __future__ import annotations

from sitepack.core.asset_downloader import (
    _charset_from_header,
    _charset_from_html_bytes,
    _decode_html_bytes,
    _normalize_encoding,
)


class TestCharsetFromHeader:
    def test_basic(self) -> None:
        assert _charset_from_header("text/html; charset=UTF-8") == "UTF-8"

    def test_shift_jis(self) -> None:
        assert _charset_from_header("text/html; charset=Shift_JIS") == "Shift_JIS"

    def test_empty(self) -> None:
        assert _charset_from_header("") is None

    def test_no_charset(self) -> None:
        assert _charset_from_header("text/html") is None


class TestCharsetFromHtmlBytes:
    """Regression: previously the regex ``[^\\"'\\\\s>;]+`` with ``IGNORECASE``
    excluded both lowercase ``s`` and uppercase ``S`` — so every declaration
    starting with ``S`` (Shift_JIS, Shift-JIS) silently returned ``None``.
    """

    def test_meta_charset_shift_jis(self) -> None:
        html = (
            b'<html><head><meta charset="Shift_JIS">'
            b'<title>foo</title></head></html>'
        )
        assert _charset_from_html_bytes(html) == "Shift_JIS"

    def test_meta_charset_lowercase(self) -> None:
        html = b'<html><head><meta charset="shift_jis"></head></html>'
        assert _charset_from_html_bytes(html) == "shift_jis"

    def test_meta_http_equiv_content_type(self) -> None:
        html = (
            b'<html><head>'
            b'<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            b'</head></html>'
        )
        assert _charset_from_html_bytes(html) == "Shift_JIS"

    def test_unquoted_attribute_value(self) -> None:
        html = b'<html><head><meta charset=EUC-JP></head></html>'
        assert _charset_from_html_bytes(html) == "EUC-JP"

    def test_missing_charset(self) -> None:
        html = b'<html><head><title>hi</title></head></html>'
        assert _charset_from_html_bytes(html) is None

    def test_survives_high_bytes(self) -> None:
        """Japanese content after the meta tag must not break detection."""
        prefix = b'<html><head><meta charset="Shift_JIS"><title>'
        # Shift-JIS bytes for 阿部寛
        body = "阿部寛".encode("cp932")
        html = prefix + body + b"</title></head></html>"
        assert _charset_from_html_bytes(html) == "Shift_JIS"


class TestNormalizeEncoding:
    def test_shift_jis_variants_normalize_to_cp932(self) -> None:
        assert _normalize_encoding("Shift_JIS") == "cp932"
        assert _normalize_encoding("shift-jis") == "cp932"
        assert _normalize_encoding("shift_jis") == "cp932"
        assert _normalize_encoding("SJIS") == "cp932"
        assert _normalize_encoding("MS_Kanji") == "cp932"

    def test_utf8_unchanged(self) -> None:
        assert _normalize_encoding("UTF-8") == "UTF-8"

    def test_euc_jp_unchanged(self) -> None:
        assert _normalize_encoding("EUC-JP") == "EUC-JP"


class TestDecodeHtmlBytes:
    """End-to-end: raw Shift-JIS bytes → correctly decoded Unicode string."""

    def test_shift_jis_with_header_charset(self) -> None:
        html = "<html><body>阿部寛のホームページ</body></html>"
        encoded = html.encode("cp932")
        result = _decode_html_bytes(encoded, "text/html; charset=Shift_JIS")
        assert "阿部寛のホームページ" in result

    def test_shift_jis_no_header_charset_uses_meta_fallback(self) -> None:
        """When the server omits charset (common on legacy JP sites), the
        decoder must fall back to the ``<meta>`` tag in the HTML bytes."""
        html = (
            '<html><head>'
            '<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
            '</head><body>阿部寛のホームページ</body></html>'
        )
        encoded = html.encode("cp932")
        result = _decode_html_bytes(encoded, "text/html")
        # Before the regex fix, this returned mojibake ("�������̃z�[���y�[�W")
        assert "阿部寛のホームページ" in result

    def test_utf8_page(self) -> None:
        html = "<html><body>日本語</body></html>"
        encoded = html.encode("utf-8")
        result = _decode_html_bytes(encoded, "text/html; charset=UTF-8")
        assert "日本語" in result
