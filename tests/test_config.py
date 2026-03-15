"""Tests for sitepack.core.config.AppConfig."""

from __future__ import annotations

from pathlib import Path

from sitepack.core.config import AppConfig


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
