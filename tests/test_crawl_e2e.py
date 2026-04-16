"""End-to-end test that simulates an abehiroshi-style legacy Japanese site.

A lightweight in-process HTTP server serves:
  * a Shift-JIS ``<frameset>`` root page
  * two Shift-JIS frame pages (``menu.htm`` + ``top.htm``) using ``.htm``
    extensions — the exact shape that used to silently break URL rewriting
  * a CSS file referenced by the frames
  * an image referenced from both HTML and CSS

This verifies the full crawl → save → rewrite pipeline produces a usable
offline copy: correct text (no mojibake), correct frame links, and the CSS
file is downloaded and linked.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sitepack.core.config import AppConfig
from sitepack.core.crawl_manager import CrawlManager


# ---------------------------------------------------------------------------
# Fixture content (encoded as Shift-JIS where relevant)
# ---------------------------------------------------------------------------

INDEX_HTML = """<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">
<title>阿部寛のホームページ</title>
</head>
<frameset cols="200,*">
<frame src="menu.htm" name="left">
<frame src="top.htm" name="right">
</frameset>
</html>
"""

MENU_HTML = """<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">
<title>メニュー</title>
<link rel="stylesheet" href="style.css">
</head>
<body bgcolor="#0000FF">
<h1>メニュー</h1>
<ul>
<li><a href="top.htm" target="right">トップ</a></li>
<li><a href="about.htm" target="right">プロフィール</a></li>
</ul>
</body>
</html>
"""

TOP_HTML = """<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">
<title>トップ</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<h1>阿部寛</h1>
<img src="photo.jpg" alt="写真">
<p>俳優、タレント</p>
</body>
</html>
"""

ABOUT_HTML = """<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">
<title>プロフィール</title>
</head>
<body>
<h1>プロフィール</h1>
<p>日本の俳優です。</p>
</body>
</html>
"""

STYLE_CSS = """body { background: url('bg.png'); color: #333; }
h1 { font-family: serif; }
"""

PHOTO_JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 100 + b"\xff\xd9"
BG_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


class _SiteHandler(BaseHTTPRequestHandler):
    """Serve the mock abehiroshi-style site with Shift-JIS pages.

    Critically, the server does **not** emit ``charset`` in the
    Content-Type header — this forces the pipeline to fall back to
    ``<meta>`` detection, the exact scenario that previously broke.
    """

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Silence the default stderr logging.
        return

    def do_GET(self) -> None:  # noqa: N802
        routes = {
            "/": (INDEX_HTML.encode("cp932"), "text/html"),
            "/menu.htm": (MENU_HTML.encode("cp932"), "text/html"),
            "/top.htm": (TOP_HTML.encode("cp932"), "text/html"),
            "/about.htm": (ABOUT_HTML.encode("cp932"), "text/html"),
            "/style.css": (STYLE_CSS.encode("utf-8"), "text/css"),
            "/photo.jpg": (PHOTO_JPG, "image/jpeg"),
            "/bg.png": (BG_PNG, "image/png"),
            "/robots.txt": (b"User-agent: *\nAllow: /\n", "text/plain"),
        }
        body, content_type = routes.get(
            self.path, (b"<html><body>404</body></html>", "text/html")
        )
        status = 200 if self.path in routes else 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_site_url():
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _SiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# The actual end-to-end test
# ---------------------------------------------------------------------------


async def _run_crawl(project_dir: Path, start_url: str) -> None:
    config = AppConfig(
        start_url=start_url,
        output_dir=str(project_dir),
        project_name="mock_site",
        max_depth=5,
        concurrency=2,
        fetch_external_assets=True,
        respect_robots=False,
        asset_filter="all",
        request_delay=0.0,
    )
    manager = CrawlManager(config)
    # Run to completion — this processes everything plus the _rewrite_all pass.
    await manager.start()


def test_crawl_frame_site_shift_jis(mock_site_url: str, tmp_path: Path) -> None:
    """Full pipeline: Shift-JIS frameset site, .htm extensions, CSS + images."""
    asyncio.run(_run_crawl(tmp_path, mock_site_url))

    project = tmp_path / "mock_site"

    # --- Files exist in expected layout ---
    index_path = project / "index.html"
    menu_path = project / "pages" / "menu.htm"
    top_path = project / "pages" / "top.htm"
    css_path = project / "assets" / "style.css"
    photo_path = project / "assets" / "photo.jpg"

    assert index_path.exists(), f"index.html missing in {list(project.iterdir())}"
    assert menu_path.exists(), "pages/menu.htm missing — .htm extension bug regressed"
    assert top_path.exists(), "pages/top.htm missing"
    assert css_path.exists(), "CSS file not downloaded"
    assert photo_path.exists(), "image asset not downloaded"

    # --- No mojibake: Japanese content round-trips as UTF-8 ---
    index_text = index_path.read_text(encoding="utf-8")
    assert "阿部寛のホームページ" in index_text, (
        "Shift-JIS title was mangled — charset detection/fix regressed"
    )
    menu_text = menu_path.read_text(encoding="utf-8")
    assert "メニュー" in menu_text
    assert "プロフィール" in menu_text
    top_text = top_path.read_text(encoding="utf-8")
    assert "俳優、タレント" in top_text

    # --- Charset declarations updated to UTF-8 ---
    # The saved file must tell the browser it's UTF-8 or Japanese will render
    # as mojibake even though the bytes are correct.
    for text in (index_text, menu_text, top_text):
        assert "Shift_JIS" not in text, "Original charset declaration leaked"
        assert "UTF-8" in text or "utf-8" in text.lower(), (
            "UTF-8 charset declaration missing"
        )

    # --- Frame links are rewritten to local relative paths ---
    # Root index → pages/menu.htm, pages/top.htm
    assert "pages/menu.htm" in index_text, (
        f"index.html still references remote URL (frame rewriting broken):\n{index_text}"
    )
    assert "pages/top.htm" in index_text

    # --- .htm files have their anchor + link hrefs rewritten ---
    # The menu page links to top.htm and about.htm, both siblings in pages/
    assert "top.htm" in menu_text  # same-directory link
    # CSS <link href="style.css"> must now point into assets/
    assert "../assets/style.css" in menu_text or "assets/style.css" in menu_text, (
        f"CSS link not rewritten in menu.htm:\n{menu_text}"
    )

    # --- Top page image reference rewritten ---
    assert "../assets/photo.jpg" in top_text or "assets/photo.jpg" in top_text
