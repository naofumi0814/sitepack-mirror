"""URL normalization and classification utilities for sitepack."""

from __future__ import annotations

import hashlib
import logging
import posixpath
import re
from pathlib import Path, PurePosixPath
from urllib.parse import (
    parse_qs,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

logger = logging.getLogger(__name__)

# Extensions recognised as downloadable assets rather than HTML pages.
_ASSET_EXTENSIONS: frozenset[str] = frozenset({
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    ".avif",
    # Stylesheets
    ".css",
    # Scripts
    ".js", ".mjs",
    # Fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # Media
    ".mp4", ".webm", ".ogg", ".mp3", ".wav", ".flac",
    # Documents / archives (often excluded but still assets)
    ".pdf", ".zip", ".tar", ".gz", ".exe", ".dmg", ".iso",
    # Data
    ".json", ".xml", ".csv", ".wasm",
})

_EXCLUDED_SCHEMES: frozenset[str] = frozenset({
    "mailto", "tel", "javascript", "data", "blob", "ftp",
})

# Characters that are unsafe in filenames across platforms.
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"|?*\x00-\x1f]')


class UrlNormalizer:
    """Stateless helper for URL normalization and classification."""

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    @staticmethod
    def normalize(url: str, base_url: str | None = None) -> str:
        """Return a canonical form of *url*.

        * Resolve against *base_url* when the URL is relative.
        * Strip fragments.
        * Collapse empty-path to ``/``.
        * Collapse consecutive slashes in the path.
        * Normalise scheme and host to lower-case.
        * Sort query parameters for consistency.
        """
        if not url or not url.strip():
            return ""

        url = url.strip()

        # Handle protocol-relative URLs  //cdn.example.com/...
        if url.startswith("//"):
            if base_url:
                scheme = urlparse(base_url).scheme or "https"
            else:
                scheme = "https"
            url = f"{scheme}:{url}"

        # Resolve relative URLs
        if base_url:
            url = urljoin(base_url, url)

        parsed = urlparse(url)

        # Bail out early for non-http(s) schemes
        if parsed.scheme and parsed.scheme.lower() in _EXCLUDED_SCHEMES:
            return url

        scheme = (parsed.scheme or "https").lower()
        hostname = (parsed.hostname or "").lower()

        # Reconstruct port only when it differs from the default
        port = parsed.port
        if port is not None:
            if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
                port = None

        netloc = hostname
        if port is not None:
            netloc = f"{hostname}:{port}"

        # Collapse consecutive slashes and normalize the path
        original_path = parsed.path or "/"
        path = re.sub(r"/{2,}", "/", original_path)
        had_trailing_slash = path.endswith("/") and len(path) > 1
        path = posixpath.normpath(path)
        # posixpath.normpath strips the trailing slash; keep root as "/"
        if path == ".":
            path = "/"
        # Ensure leading slash
        if not path.startswith("/"):
            path = "/" + path
        # Preserve trailing slash — it affects relative URL resolution
        # (urljoin treats /blog/ and /blog differently).
        if had_trailing_slash and not path.endswith("/"):
            path = path + "/"

        # Sort query parameters for consistent comparison
        query = ""
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=True)
            # Sort both keys and individual values within each key
            sorted_qs: list[tuple[str, str]] = []
            for key in sorted(qs):
                for val in sorted(qs[key]):
                    sorted_qs.append((key, val))
            query = urlencode(sorted_qs)

        # Strip fragment
        normalized = urlunparse((scheme, netloc, path, "", query, ""))
        return normalized

    # ------------------------------------------------------------------
    # Host comparison
    # ------------------------------------------------------------------

    @staticmethod
    def is_same_host(url: str, reference_url: str) -> bool:
        """Return True when both URLs share the exact same hostname."""
        host_a = urlparse(url).hostname or ""
        host_b = urlparse(reference_url).hostname or ""
        return host_a.lower() == host_b.lower()

    @staticmethod
    def is_subdomain(url: str, reference_url: str) -> bool:
        """Return True when *url*'s host is a subdomain of *reference_url*'s host.

        ``www.example.com`` is considered a subdomain of ``example.com``.
        An exact match is **not** treated as a subdomain.
        """
        host = (urlparse(url).hostname or "").lower()
        ref_host = (urlparse(reference_url).hostname or "").lower()

        if not host or not ref_host:
            return False
        if host == ref_host:
            return False
        return host.endswith(f".{ref_host}")

    @classmethod
    def should_crawl(
        cls,
        url: str,
        reference_url: str,
        include_subdomains: bool = False,
    ) -> bool:
        """Decide whether *url* should be followed during a crawl.

        Returns True only for HTTP(S) URLs on the same host (or a
        subdomain when *include_subdomains* is set) that look like HTML
        pages rather than binary assets.
        """
        parsed = urlparse(url)

        # Only crawl http(s)
        if parsed.scheme not in ("http", "https", ""):
            return False

        # Must be on allowed host
        same = cls.is_same_host(url, reference_url)
        sub = include_subdomains and cls.is_subdomain(url, reference_url)
        if not (same or sub):
            return False

        # Skip obvious asset URLs
        if cls.is_asset_url(url):
            return False

        return True

    # ------------------------------------------------------------------
    # URL classification
    # ------------------------------------------------------------------

    @staticmethod
    def is_asset_url(url: str) -> bool:
        """Return True if the URL points to a known static asset type."""
        path = urlparse(url).path or ""
        # Handle compound extensions like .tar.gz
        lower = path.lower()
        if lower.endswith(".tar.gz"):
            return True
        suffix = PurePosixPath(lower).suffix
        return suffix in _ASSET_EXTENSIONS

    @staticmethod
    def is_excluded_scheme(url: str) -> bool:
        """Return True for mailto:, tel:, javascript:, data:, etc."""
        if not url:
            return False
        # Fast path for common prefixes before parsing
        lower = url.strip().lower()
        for scheme in _EXCLUDED_SCHEMES:
            if lower.startswith(f"{scheme}:"):
                return True
        parsed = urlparse(url)
        return (parsed.scheme or "").lower() in _EXCLUDED_SCHEMES

    # ------------------------------------------------------------------
    # Filesystem mapping
    # ------------------------------------------------------------------

    @staticmethod
    def url_to_relative_path(url: str) -> Path:
        """Convert a URL's path component to a safe relative filesystem path.

        Returns just the inner path (no ``pages/`` / ``external/<host>/``
        prefix); the caller decides which root directory it belongs in.

        * Query strings are represented as a short hash suffix.
        * Extension-less paths are stored as ``<path>/index.html``.
        * Unsafe characters are replaced with ``_``.
        """
        parsed = urlparse(url)
        path = parsed.path or "/"

        # Clean and split the path
        path = re.sub(r"/{2,}", "/", path)
        path = path.strip("/")

        # Append query-hash when a query string is present
        query_suffix = ""
        if parsed.query:
            query_hash = hashlib.sha256(parsed.query.encode()).hexdigest()[:10]
            query_suffix = f"_q{query_hash}"

        if not path:
            return Path(f"index{query_suffix}.html")

        parts = path.split("/")
        last = parts[-1]

        if "." in last:
            name, ext = last.rsplit(".", 1)
            safe_name = _UNSAFE_FILENAME_RE.sub("_", name)
            filename = f"{safe_name}{query_suffix}.{ext}"
            if len(parts) > 1:
                directories = [_UNSAFE_FILENAME_RE.sub("_", p) for p in parts[:-1]]
                return Path(*directories) / filename
            return Path(filename)

        # No extension — treat as directory and use index.html
        safe_parts = [_UNSAFE_FILENAME_RE.sub("_", p) for p in parts]
        filename = f"index{query_suffix}.html"
        return Path(*safe_parts) / filename

    @classmethod
    def url_to_filepath(cls, url: str, base_host: str) -> Path:
        """Convert a URL to a safe deterministic filesystem path *with* a
        ``pages/`` (same host) or ``external/<host>/`` (other host) root.

        ``base_host`` is matched against the URL's ``netloc`` (case
        insensitive); both sides may include a port.

        Provided for backward compatibility with callers that want the
        full namespaced path.  New code should prefer
        :meth:`url_to_relative_path` and add its own root.
        """
        parsed = urlparse(url)
        netloc = (parsed.netloc or "unknown").lower()
        base = (base_host or "").lower()

        relative = cls.url_to_relative_path(url)

        if netloc == base:
            return Path("pages") / relative
        host_for_dir = parsed.hostname or netloc or "unknown"
        return Path("external") / host_for_dir / relative

    # ------------------------------------------------------------------
    # Tracking-parameter removal
    # ------------------------------------------------------------------

    @staticmethod
    def strip_tracking_params(url: str, params_list: list[str]) -> str:
        """Remove specified tracking query parameters from *url*."""
        if not url or not params_list:
            return url

        parsed = urlparse(url)
        if not parsed.query:
            return url

        params_set = {p.lower() for p in params_list}
        qs = parse_qs(parsed.query, keep_blank_values=True)

        filtered: dict[str, list[str]] = {}
        for key, values in qs.items():
            if key.lower() not in params_set:
                filtered[key] = values

        new_query = urlencode(
            [(k, v) for k in sorted(filtered) for v in sorted(filtered[k])]
        )
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))
