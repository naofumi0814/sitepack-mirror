"""Tests for sitepack.core.config.AppConfig."""

from __future__ import annotations

from pathlib import Path

from sitepack.core.config import AppConfig, sanitize_project_name


class TestAppConfig:
    """Tests for AppConfig dataclass."""

    def test_default_values(self) -> None:
        cfg = AppConfig()
        assert cfg.same_host_only is True
        assert cfg.include_subdomains is False
        assert cfg.max_depth == 10
        assert cfg.concurrency == 4
        assert cfg.request_delay == 0.5
        assert cfg.respect_robots is True
        assert cfg.fetch_external_assets is True
        assert cfg.asset_filter == "all"
        assert cfg.timeout == 30
        assert cfg.strip_tracking_params is True
        assert len(cfg.tracking_params) > 0
        assert len(cfg.excluded_extensions) > 0

    def test_to_dict_from_dict_roundtrip(self) -> None:
        original = AppConfig(
            start_url="https://example.com",
            output_dir=Path("/tmp/out"),
            project_name="my_project",
            max_depth=5,
            concurrency=8,
        )
        data = original.to_dict()
        restored = AppConfig.from_dict(data)

        assert restored.start_url == original.start_url
        assert restored.output_dir == original.output_dir
        assert restored.project_name == original.project_name
        assert restored.max_depth == original.max_depth
        assert restored.concurrency == original.concurrency

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        cfg = AppConfig(
            start_url="https://example.com",
            output_dir=tmp_path / "output",
            project_name="roundtrip_test",
            max_depth=7,
        )
        config_file = tmp_path / "config.json"
        cfg.save(config_file)

        loaded = AppConfig.load(config_file)
        assert loaded.start_url == cfg.start_url
        assert loaded.project_name == cfg.project_name
        assert loaded.max_depth == cfg.max_depth

    def test_unknown_keys_ignored(self) -> None:
        data = {
            "start_url": "https://example.com",
            "project_name": "test",
            "future_field": True,
            "another_unknown": 42,
        }
        cfg = AppConfig.from_dict(data)
        assert cfg.start_url == "https://example.com"
        assert cfg.project_name == "test"
        # Unknown keys do not raise and are not set as attributes
        assert not hasattr(cfg, "future_field")
        assert not hasattr(cfg, "another_unknown")


class TestSanitizeProjectName:
    """Regression tests for sanitize_project_name().

    Prevents WinError 123 on Windows when the project name accidentally
    contains path separators, URL fragments, or other illegal chars.
    """

    def test_plain_name_unchanged(self) -> None:
        assert sanitize_project_name("my-project") == "my-project"
        assert sanitize_project_name("プロジェクト") == "プロジェクト"
        assert sanitize_project_name("06") == "06"

    def test_strips_whitespace(self) -> None:
        assert sanitize_project_name("  foo  ") == "foo"

    def test_rejects_path_separators(self) -> None:
        # Forward/backward slashes must never appear in a project name
        assert "/" not in sanitize_project_name("a/b/c")
        assert "\\" not in sanitize_project_name("a\\b\\c")

    def test_rejects_windows_illegal_chars(self) -> None:
        for ch in ('<', '>', ':', '"', '|', '?', '*'):
            out = sanitize_project_name(f"name{ch}x")
            assert ch not in out, f"{ch!r} should be stripped, got {out!r}"

    def test_url_like_project_name_becomes_safe(self) -> None:
        # THE regression case that triggered WinError 123
        bad = "06/https://naofumi.org"
        safe = sanitize_project_name(bad)
        # No illegal chars remain
        for ch in ('/', '\\', ':'):
            assert ch not in safe
        # Non-empty and deterministic
        assert safe
        assert sanitize_project_name(bad) == safe

    def test_empty_falls_back(self) -> None:
        assert sanitize_project_name("") == "sitepack_project"
        assert sanitize_project_name("   ") == "sitepack_project"
        assert sanitize_project_name("///") == "sitepack_project"

    def test_windows_reserved_names_suffixed(self) -> None:
        assert sanitize_project_name("CON").endswith("_")
        assert sanitize_project_name("com1").endswith("_")

    def test_trailing_dots_spaces_removed(self) -> None:
        # Windows disallows trailing dots / spaces in path components
        assert not sanitize_project_name("foo.").endswith(".")
        assert not sanitize_project_name("foo ").endswith(" ")

    def test_app_config_post_init_sanitizes(self) -> None:
        # AppConfig must never keep an unsafe project_name
        cfg = AppConfig(
            start_url="https://example.com",
            project_name="06/https://naofumi.org",
        )
        for ch in ('/', '\\', ':'):
            assert ch not in cfg.project_name
