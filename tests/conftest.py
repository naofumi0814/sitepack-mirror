"""Shared pytest fixtures for the sitepack test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from sitepack.core.config import AppConfig


@pytest.fixture()
def sample_html() -> str:
    """Return a realistic HTML string containing many kinds of resource links."""
    return """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Test Page</title>
    <link rel="stylesheet" href="/css/style.css">
    <link rel="icon" href="/favicon.ico">
    <script src="/js/app.js"></script>
    <style>
        body { background: url('/images/bg.png'); }
    </style>
</head>
<body>
    <a href="/about">About</a>
    <a href="https://external.example.com/page">External</a>
    <a href="mailto:user@example.com">Email</a>
    <a href="javascript:void(0)">JS Link</a>
    <img src="/images/logo.png" alt="Logo">
    <img srcset="/images/hero-1x.png 1x, /images/hero-2x.png 2x" alt="Hero">
    <video poster="/images/poster.jpg">
        <source src="/media/video.mp4" type="video/mp4">
    </video>
    <div style="background-image: url('/images/inline-bg.png')"></div>
</body>
</html>"""


@pytest.fixture()
def sample_css() -> str:
    """Return CSS text containing url(), @import, and @font-face references."""
    return """\
@import url('reset.css');
@import 'typography.css';

@font-face {
    font-family: 'CustomFont';
    src: url('fonts/custom.woff2') format('woff2'),
         url('fonts/custom.woff') format('woff');
}

body {
    background: url('/images/bg.png') no-repeat center;
}

.icon {
    background-image: url('../icons/sprite.svg');
}
"""


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary directory with the standard project sub-directories."""
    for subdir in ("pages", "assets", "external", "_meta"):
        (tmp_path / subdir).mkdir()
    return tmp_path


@pytest.fixture()
def default_config(tmp_path: Path) -> AppConfig:
    """Return an AppConfig populated with test-friendly defaults."""
    return AppConfig(
        start_url="https://example.com",
        output_dir=tmp_path / "output",
        project_name="test_project",
        max_depth=3,
        concurrency=1,
        request_delay=0.0,
    )
