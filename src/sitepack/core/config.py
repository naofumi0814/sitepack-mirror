"""Application configuration management for sitepack."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Self

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_DEFAULT_EXCLUDED_EXTENSIONS: list[str] = [
    ".zip", ".exe", ".dmg", ".iso", ".tar.gz",
]

_DEFAULT_TRACKING_PARAMS: list[str] = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
]


# Characters that are illegal in Windows filenames (also a good superset for
# macOS/Linux). We also treat path separators as illegal inside a project
# name so that nobody can accidentally inject an absolute path or a URL.
_PROJECT_NAME_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows reserved device names (case-insensitive).
_WINDOWS_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
})


def sanitize_project_name(name: str) -> str:
    """Return a filesystem-safe, cross-platform project directory name.

    * Leading/trailing whitespace is stripped.
    * Illegal characters (``/ \\ : * ? " < > |`` and control chars) are
      replaced with ``_``.
    * Trailing dots and spaces are stripped (Windows requirement).
    * Reserved device names (``CON``, ``PRN`` …) get an underscore suffix.
    * An empty result collapses to ``sitepack_project``.
    """
    if name is None:
        return "sitepack_project"

    cleaned = name.strip()
    cleaned = _PROJECT_NAME_ILLEGAL_RE.sub("_", cleaned)
    # Windows disallows trailing dots / spaces in path components
    cleaned = cleaned.rstrip(" .")

    # If everything was illegal (e.g. "///" -> "___") treat as empty
    if not cleaned or cleaned.strip("_") == "":
        return "sitepack_project"
    if cleaned.lower() in _WINDOWS_RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned


@dataclass
class AppConfig:
    """Configuration for a sitepack crawl session."""

    start_url: str = ""
    output_dir: Path = field(default_factory=lambda: Path.cwd())
    project_name: str = ""
    same_host_only: bool = True
    include_subdomains: bool = False
    max_depth: int = 10
    concurrency: int = 4
    request_delay: float = 0.5
    respect_robots: bool = True
    use_renderer: bool = False
    lazy_scroll: bool = True
    fetch_external_assets: bool = True
    asset_filter: str = "all"  # "all", "images", "css_js", "none"
    excluded_extensions: list[str] = field(
        default_factory=lambda: list(_DEFAULT_EXCLUDED_EXTENSIONS)
    )
    excluded_paths: list[str] = field(default_factory=list)
    user_agent: str = _DEFAULT_USER_AGENT
    cookie_file: Path | None = None
    timeout: int = 30
    wait_after_load: float = 1.0
    strip_tracking_params: bool = True
    tracking_params: list[str] = field(
        default_factory=lambda: list(_DEFAULT_TRACKING_PARAMS)
    )

    def __post_init__(self) -> None:
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.cookie_file, str):
            self.cookie_file = Path(self.cookie_file)
        if self.asset_filter not in ("all", "images", "css_js", "none"):
            logger.warning(
                "Invalid asset_filter %r, falling back to 'all'",
                self.asset_filter,
            )
            self.asset_filter = "all"

        # Always sanitize project_name: guarantees we never try to create a
        # path like "06/https://naofumi.org" on Windows (WinError 123).
        safe = sanitize_project_name(self.project_name)
        if safe != self.project_name:
            logger.warning(
                "Project name %r contained illegal characters; "
                "using %r instead.",
                self.project_name,
                safe,
            )
            self.project_name = safe

    def to_dict(self) -> dict:
        """Serialize the configuration to a plain dictionary.

        Path objects are converted to strings and None values are preserved.
        """
        data = asdict(self)
        data["output_dir"] = str(self.output_dir)
        data["cookie_file"] = str(self.cookie_file) if self.cookie_file is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create an AppConfig instance from a dictionary.

        Handles type coercion for Path fields and ignores unknown keys so that
        config files from newer versions do not break older code.
        """
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}

        if "output_dir" in filtered:
            filtered["output_dir"] = Path(filtered["output_dir"])
        if "cookie_file" in filtered and filtered["cookie_file"] is not None:
            filtered["cookie_file"] = Path(filtered["cookie_file"])

        unknown = set(data.keys()) - known_fields
        if unknown:
            logger.debug("Ignoring unknown config keys: %s", unknown)

        return cls(**filtered)

    def save(self, path: Path) -> None:
        """Write the configuration to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Configuration saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load configuration from a JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        logger.info("Configuration loaded from %s", path)
        return cls.from_dict(data)
