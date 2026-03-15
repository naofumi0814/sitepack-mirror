"""Local HTTP preview server for downloaded sites."""

from __future__ import annotations

import http.server
import logging
import threading
import webbrowser
from functools import partial
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_PORT_ATTEMPTS = 10


class PreviewServer:
    """Simple HTTP server for previewing downloaded sites locally.

    Serves static files from a local directory over HTTP so that downloaded
    sites can be previewed in a browser with correct relative-path resolution.

    Usage::

        server = PreviewServer(root_dir=Path("./output"), port=8080)
        url = server.start()
        # ... preview the site ...
        server.stop()

    The server binds to ``127.0.0.1`` only and runs on a daemon thread so it
    does not block interpreter shutdown.
    """

    def __init__(self, root_dir: Path, port: int = 8080) -> None:
        if not root_dir.is_dir():
            raise NotADirectoryError(f"Root directory does not exist: {root_dir}")
        if not (1 <= port <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {port}")

        self._root_dir = root_dir.resolve()
        self._port = port
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> str:
        """Start the preview server and return the URL.

        If the requested port is already in use the server will try up to
        :data:`_MAX_PORT_ATTEMPTS` consecutive ports before raising
        :class:`RuntimeError`.

        Returns:
            The URL where the site is accessible (e.g. ``http://127.0.0.1:8080``).

        Raises:
            RuntimeError: If the server is already running or no available port
                could be found.
        """
        if self._server is not None:
            raise RuntimeError("Preview server is already running")

        handler = partial(
            _SilentRequestHandler,
            directory=str(self._root_dir),
        )

        for attempt in range(_MAX_PORT_ATTEMPTS):
            port = self._port + attempt
            try:
                self._server = http.server.HTTPServer(("127.0.0.1", port), handler)
                self._port = port
                break
            except OSError as exc:
                logger.debug("Port %d unavailable: %s", port, exc)
                continue
        else:
            raise RuntimeError(
                f"Could not find an available port after {_MAX_PORT_ATTEMPTS} "
                f"attempts (starting from {self._port})"
            )

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="preview-server",
            daemon=True,
        )
        self._thread.start()

        url = self.url
        logger.info("Preview server started at %s (serving %s)", url, self._root_dir)
        return url

    def stop(self) -> None:
        """Stop the preview server.

        This method is safe to call even when the server is not running.
        """
        if self._server is None:
            return

        logger.info("Stopping preview server on port %d …", self._port)
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server.server_close()
        self._server = None
        self._thread = None
        logger.info("Preview server stopped")

    # -- properties ----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the server is currently running."""
        return self._server is not None

    @property
    def port(self) -> int:
        """Return the port the server is (or will be) listening on."""
        return self._port

    @property
    def url(self) -> str:
        """Return the base URL for the preview server."""
        return f"http://127.0.0.1:{self._port}"

    @property
    def root_dir(self) -> Path:
        """Return the directory being served."""
        return self._root_dir

    # -- context manager -----------------------------------------------------

    def __enter__(self) -> PreviewServer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.stop()

    # -- convenience helpers -------------------------------------------------

    @staticmethod
    def open_in_browser(url: str) -> None:
        """Open *url* in the default web browser."""
        webbrowser.open(url)

    @staticmethod
    def open_file(path: Path) -> None:
        """Open a local file in the default web browser via a ``file://`` URI."""
        webbrowser.open(path.resolve().as_uri())


class _SilentRequestHandler(http.server.SimpleHTTPRequestHandler):
    """A request handler that logs to the module logger instead of *stderr*."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.debug(format, *args)

    def log_error(self, format: str, *args: object) -> None:  # noqa: A002
        logger.warning(format, *args)
