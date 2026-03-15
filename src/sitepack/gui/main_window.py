"""Main window for the SitePack GUI application."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGridLayout,
    QTabWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QComboBox,
    QTextEdit,
    QPlainTextEdit,
    QProgressBar,
    QFileDialog,
    QGroupBox,
    QMessageBox,
    QStatusBar,
    QSplitter,
)

from sitepack.core.config import AppConfig
from sitepack.core.crawl_manager import CrawlManager
from sitepack.core.preview_server import PreviewServer

logger = logging.getLogger(__name__)

_ASSET_FILTER_OPTIONS: dict[str, str] = {
    "All": "all",
    "Images only": "images",
    "CSS + JS only": "css_js",
    "None": "none",
}

_ASSET_FILTER_REVERSE: dict[str, str] = {v: k for k, v in _ASSET_FILTER_OPTIONS.items()}


# ---------------------------------------------------------------------------
# Worker that runs the crawl in a background thread
# ---------------------------------------------------------------------------


class CrawlWorker(QObject):
    """Runs :class:`CrawlManager` inside its own asyncio event loop on a
    background thread so the GUI stays responsive."""

    progress = Signal(int, int)
    status = Signal(str)
    url_processing = Signal(str)
    log_message = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, config: AppConfig, retry: bool = False) -> None:
        super().__init__()
        self._config = config
        self._retry = retry
        self._manager: CrawlManager | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- public helpers exposed to the main thread --------------------------

    @property
    def manager(self) -> CrawlManager | None:
        return self._manager

    def request_stop(self) -> None:
        """Thread-safe request to stop the crawl gracefully."""
        if self._manager is not None:
            self._manager.stop()

    # -- slot that actually runs on the worker thread -----------------------

    @Slot()
    def run(self) -> None:
        """Entry point executed on the background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._manager = CrawlManager(self._config)
        self._manager.on_progress = self._on_progress
        self._manager.on_status = self._on_status
        self._manager.on_url_processing = self._on_url
        self._manager.on_log = self._on_log
        self._manager.on_error = self._on_error
        self._manager.on_complete = self._on_complete

        try:
            if self._retry:
                self._loop.run_until_complete(self._manager.retry_failed())
            else:
                self._loop.run_until_complete(self._manager.start())
        except Exception as exc:
            logger.exception("CrawlWorker encountered an error")
            self.error.emit(str(exc))
        finally:
            self._loop.close()
            self.finished.emit()

    # -- callback adapters (called from the async loop, emit Qt signals) ----

    def _on_progress(self, current: int, total: int) -> None:
        self.progress.emit(current, total)

    def _on_status(self, msg: str) -> None:
        self.status.emit(msg)

    def _on_url(self, url: str) -> None:
        self.url_processing.emit(url)

    def _on_log(self, msg: str) -> None:
        self.log_message.emit(msg)

    def _on_error(self, msg: str) -> None:
        self.error.emit(msg)

    def _on_complete(self) -> None:
        # finished signal is emitted in the finally block of run()
        pass


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """SitePack main GUI window."""

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("SitePack \u2014 Website Static Packager")
        self.setMinimumSize(900, 700)

        self._worker: CrawlWorker | None = None
        self._worker_thread: threading.Thread | None = None
        self._preview_server: PreviewServer | None = None

        self._build_ui()
        self._apply_stylesheet()
        self._connect_signals()
        self._set_idle_state()

    # ==================================================================
    # UI construction
    # ==================================================================

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # --- Project settings group ---
        root_layout.addWidget(self._build_project_group())

        # --- Tabs ---
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_basic_settings_tab(), "Basic Settings")
        self._tabs.addTab(self._build_rendering_tab(), "Rendering")
        self._tabs.addTab(self._build_exclusions_tab(), "Exclusions")
        self._tabs.addTab(self._build_log_tab(), "Log")
        root_layout.addWidget(self._tabs, stretch=1)

        # --- Progress section ---
        root_layout.addWidget(self._build_progress_section())

        # --- Button bar ---
        root_layout.addWidget(self._build_button_bar())

        # --- Status bar ---
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

    # -- Project group ------------------------------------------------------

    def _build_project_group(self) -> QGroupBox:
        group = QGroupBox("Project")
        layout = QFormLayout(group)
        layout.setContentsMargins(12, 20, 12, 12)
        layout.setSpacing(8)

        self._start_url_edit = QLineEdit()
        self._start_url_edit.setPlaceholderText("https://example.com")
        layout.addRow("Start URL:", self._start_url_edit)

        self._project_name_edit = QLineEdit()
        self._project_name_edit.setPlaceholderText("my-project")
        layout.addRow("Project name:", self._project_name_edit)

        output_row = QHBoxLayout()
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText(str(Path.home() / "sitepack-output"))
        self._output_dir_edit.setText(str(Path.home() / "sitepack-output"))
        output_row.addWidget(self._output_dir_edit)

        self._browse_btn = QPushButton("Browse\u2026")
        self._browse_btn.setFixedWidth(90)
        self._browse_btn.clicked.connect(self._browse_output_folder)
        output_row.addWidget(self._browse_btn)

        layout.addRow("Output folder:", output_row)

        return group

    # -- Basic Settings tab -------------------------------------------------

    def _build_basic_settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self._same_host_cb = QCheckBox("Only crawl the same host")
        self._same_host_cb.setChecked(True)
        layout.addRow(self._same_host_cb)

        self._include_subdomains_cb = QCheckBox("Include subdomains")
        layout.addRow(self._include_subdomains_cb)

        self._max_depth_spin = QSpinBox()
        self._max_depth_spin.setRange(1, 999)
        self._max_depth_spin.setValue(10)
        layout.addRow("Max depth:", self._max_depth_spin)

        self._concurrency_spin = QSpinBox()
        self._concurrency_spin.setRange(1, 64)
        self._concurrency_spin.setValue(4)
        layout.addRow("Concurrency:", self._concurrency_spin)

        self._request_delay_spin = QDoubleSpinBox()
        self._request_delay_spin.setRange(0.0, 60.0)
        self._request_delay_spin.setSingleStep(0.1)
        self._request_delay_spin.setDecimals(1)
        self._request_delay_spin.setValue(0.5)
        self._request_delay_spin.setSuffix(" s")
        layout.addRow("Request delay:", self._request_delay_spin)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 300)
        self._timeout_spin.setValue(30)
        self._timeout_spin.setSuffix(" s")
        layout.addRow("Timeout:", self._timeout_spin)

        self._respect_robots_cb = QCheckBox("Respect robots.txt")
        self._respect_robots_cb.setChecked(True)
        layout.addRow(self._respect_robots_cb)

        self._fetch_external_cb = QCheckBox("Fetch external assets (CSS, JS, images)")
        self._fetch_external_cb.setChecked(True)
        layout.addRow(self._fetch_external_cb)

        self._asset_filter_combo = QComboBox()
        self._asset_filter_combo.addItems(list(_ASSET_FILTER_OPTIONS.keys()))
        layout.addRow("Asset filter:", self._asset_filter_combo)

        return tab

    # -- Rendering tab ------------------------------------------------------

    def _build_rendering_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self._use_renderer_cb = QCheckBox("Use browser renderer (Playwright)")
        layout.addRow(self._use_renderer_cb)

        self._lazy_scroll_cb = QCheckBox("Lazy-scroll pages to trigger loading")
        self._lazy_scroll_cb.setChecked(True)
        layout.addRow(self._lazy_scroll_cb)

        self._wait_after_load_spin = QDoubleSpinBox()
        self._wait_after_load_spin.setRange(0.0, 30.0)
        self._wait_after_load_spin.setSingleStep(0.5)
        self._wait_after_load_spin.setDecimals(1)
        self._wait_after_load_spin.setValue(1.0)
        self._wait_after_load_spin.setSuffix(" s")
        layout.addRow("Wait after load:", self._wait_after_load_spin)

        self._user_agent_edit = QLineEdit()
        self._user_agent_edit.setText(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        layout.addRow("User-Agent:", self._user_agent_edit)

        return tab

    # -- Exclusions tab -----------------------------------------------------

    def _build_exclusions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Excluded extensions
        ext_label = QLabel("Excluded extensions (one per line):")
        layout.addWidget(ext_label)

        self._excluded_ext_edit = QTextEdit()
        self._excluded_ext_edit.setAcceptRichText(False)
        self._excluded_ext_edit.setPlainText(
            ".zip\n.exe\n.dmg\n.iso\n.tar.gz"
        )
        layout.addWidget(self._excluded_ext_edit)

        # Excluded paths
        path_label = QLabel("Excluded URL paths (one per line):")
        layout.addWidget(path_label)

        self._excluded_paths_edit = QTextEdit()
        self._excluded_paths_edit.setAcceptRichText(False)
        layout.addWidget(self._excluded_paths_edit)

        # Strip tracking params
        self._strip_tracking_cb = QCheckBox("Strip tracking parameters")
        self._strip_tracking_cb.setChecked(True)
        layout.addWidget(self._strip_tracking_cb)

        tracking_label = QLabel("Tracking parameters to strip (one per line):")
        layout.addWidget(tracking_label)

        self._tracking_params_edit = QTextEdit()
        self._tracking_params_edit.setAcceptRichText(False)
        self._tracking_params_edit.setPlainText(
            "utm_source\nutm_medium\nutm_campaign\n"
            "utm_term\nutm_content\nfbclid\ngclid\nmc_cid\nmc_eid"
        )
        layout.addWidget(self._tracking_params_edit)

        return tab

    # -- Log tab ------------------------------------------------------------

    def _build_log_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        mono_font = QFont("Consolas", 9)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self._log_text.setFont(mono_font)
        self._log_text.setMaximumBlockCount(10_000)
        layout.addWidget(self._log_text)

        return tab

    # -- Progress section ---------------------------------------------------

    def _build_progress_section(self) -> QGroupBox:
        group = QGroupBox("Progress")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 20, 12, 12)
        layout.setSpacing(6)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("Idle")
        layout.addWidget(self._status_label)

        self._current_url_label = QLabel("")
        self._current_url_label.setTextFormat(Qt.TextFormat.PlainText)
        self._current_url_label.setWordWrap(False)
        url_font = QFont()
        url_font.setPointSize(8)
        self._current_url_label.setFont(url_font)
        self._current_url_label.setStyleSheet("color: #666;")
        layout.addWidget(self._current_url_label)

        self._summary_label = QLabel("Pages: 0 | Assets: 0 | Failed: 0")
        layout.addWidget(self._summary_label)

        return group

    # -- Button bar ---------------------------------------------------------

    def _build_button_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._start_btn = QPushButton("Start")
        self._start_btn.setObjectName("startBtn")
        self._start_btn.setMinimumWidth(100)
        layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setMinimumWidth(80)
        layout.addWidget(self._stop_btn)

        self._retry_btn = QPushButton("Retry Failed")
        self._retry_btn.setMinimumWidth(100)
        layout.addWidget(self._retry_btn)

        layout.addStretch()

        self._open_folder_btn = QPushButton("Open Output Folder")
        layout.addWidget(self._open_folder_btn)

        self._preview_http_btn = QPushButton("Preview (HTTP)")
        layout.addWidget(self._preview_http_btn)

        self._preview_file_btn = QPushButton("Preview (file://)")
        layout.addWidget(self._preview_file_btn)

        self._export_zip_btn = QPushButton("Export ZIP")
        layout.addWidget(self._export_zip_btn)

        return bar

    # ==================================================================
    # Stylesheet
    # ==================================================================

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QMainWindow { background: #f5f5f5; }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ddd;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-position: top left;
                padding: 4px 8px;
            }
            QTabWidget::pane {
                border: 1px solid #ddd;
                border-radius: 4px;
                background: white;
            }
            QPushButton {
                padding: 6px 16px;
                border-radius: 4px;
                border: 1px solid #ccc;
                background: #fff;
            }
            QPushButton:hover { background: #e8e8e8; }
            QPushButton#startBtn {
                background: #4CAF50;
                color: white;
                font-weight: bold;
                border: none;
            }
            QPushButton#startBtn:hover { background: #45a049; }
            QPushButton#startBtn:disabled { background: #a5d6a7; }
            QPushButton#stopBtn {
                background: #f44336;
                color: white;
                font-weight: bold;
                border: none;
            }
            QPushButton#stopBtn:hover { background: #da190b; }
            QPushButton#stopBtn:disabled { background: #ef9a9a; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                padding: 4px;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #4CAF50;
                border-radius: 3px;
            }
        """)

    # ==================================================================
    # Signal wiring
    # ==================================================================

    def _connect_signals(self) -> None:
        self._start_btn.clicked.connect(self._start_crawl)
        self._stop_btn.clicked.connect(self._stop_crawl)
        self._retry_btn.clicked.connect(self._retry_failed)
        self._open_folder_btn.clicked.connect(self._open_output_folder)
        self._preview_http_btn.clicked.connect(self._start_preview_http)
        self._preview_file_btn.clicked.connect(self._start_preview_file)
        self._export_zip_btn.clicked.connect(self._export_zip)

    # ==================================================================
    # State management
    # ==================================================================

    def _set_idle_state(self) -> None:
        """Configure button enabled/disabled for the idle (not crawling) state."""
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._retry_btn.setEnabled(True)
        self._browse_btn.setEnabled(True)
        self._open_folder_btn.setEnabled(True)
        self._preview_http_btn.setEnabled(True)
        self._preview_file_btn.setEnabled(True)
        self._export_zip_btn.setEnabled(True)

    def _set_crawling_state(self) -> None:
        """Configure button enabled/disabled for the active crawl state."""
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._retry_btn.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._open_folder_btn.setEnabled(False)
        self._preview_http_btn.setEnabled(False)
        self._preview_file_btn.setEnabled(False)
        self._export_zip_btn.setEnabled(False)

    # ==================================================================
    # Config <-> UI
    # ==================================================================

    def _build_config(self) -> AppConfig:
        """Read all UI fields and return a populated :class:`AppConfig`."""
        asset_filter_label = self._asset_filter_combo.currentText()
        asset_filter_value = _ASSET_FILTER_OPTIONS.get(asset_filter_label, "all")

        return AppConfig(
            start_url=self._start_url_edit.text().strip(),
            output_dir=Path(self._output_dir_edit.text().strip()),
            project_name=self._project_name_edit.text().strip(),
            same_host_only=self._same_host_cb.isChecked(),
            include_subdomains=self._include_subdomains_cb.isChecked(),
            max_depth=self._max_depth_spin.value(),
            concurrency=self._concurrency_spin.value(),
            request_delay=self._request_delay_spin.value(),
            respect_robots=self._respect_robots_cb.isChecked(),
            use_renderer=self._use_renderer_cb.isChecked(),
            lazy_scroll=self._lazy_scroll_cb.isChecked(),
            fetch_external_assets=self._fetch_external_cb.isChecked(),
            asset_filter=asset_filter_value,
            excluded_extensions=self._parse_text_list(self._excluded_ext_edit),
            excluded_paths=self._parse_text_list(self._excluded_paths_edit),
            user_agent=self._user_agent_edit.text().strip(),
            timeout=self._timeout_spin.value(),
            wait_after_load=self._wait_after_load_spin.value(),
            strip_tracking_params=self._strip_tracking_cb.isChecked(),
            tracking_params=self._parse_text_list(self._tracking_params_edit),
        )

    def _load_config(self, config: AppConfig) -> None:
        """Populate every UI field from *config*."""
        self._start_url_edit.setText(config.start_url)
        self._project_name_edit.setText(config.project_name)
        self._output_dir_edit.setText(str(config.output_dir))

        self._same_host_cb.setChecked(config.same_host_only)
        self._include_subdomains_cb.setChecked(config.include_subdomains)
        self._max_depth_spin.setValue(config.max_depth)
        self._concurrency_spin.setValue(config.concurrency)
        self._request_delay_spin.setValue(config.request_delay)
        self._timeout_spin.setValue(config.timeout)
        self._respect_robots_cb.setChecked(config.respect_robots)
        self._fetch_external_cb.setChecked(config.fetch_external_assets)

        combo_label = _ASSET_FILTER_REVERSE.get(config.asset_filter, "All")
        idx = self._asset_filter_combo.findText(combo_label)
        if idx >= 0:
            self._asset_filter_combo.setCurrentIndex(idx)

        self._use_renderer_cb.setChecked(config.use_renderer)
        self._lazy_scroll_cb.setChecked(config.lazy_scroll)
        self._wait_after_load_spin.setValue(config.wait_after_load)
        self._user_agent_edit.setText(config.user_agent)

        self._excluded_ext_edit.setPlainText("\n".join(config.excluded_extensions))
        self._excluded_paths_edit.setPlainText("\n".join(config.excluded_paths))
        self._strip_tracking_cb.setChecked(config.strip_tracking_params)
        self._tracking_params_edit.setPlainText("\n".join(config.tracking_params))

    # ==================================================================
    # Crawl lifecycle
    # ==================================================================

    def _start_crawl(self) -> None:
        """Validate inputs, build config, and launch the crawl worker thread."""
        url = self._start_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Validation Error", "Please enter a start URL.")
            return
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(
                self,
                "Validation Error",
                "The start URL must begin with http:// or https://",
            )
            return

        project_name = self._project_name_edit.text().strip()
        if not project_name:
            QMessageBox.warning(self, "Validation Error", "Please enter a project name.")
            return

        output_dir = self._output_dir_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "Validation Error", "Please select an output folder.")
            return

        config = self._build_config()
        self._launch_worker(config, retry=False)

    def _stop_crawl(self) -> None:
        """Request a graceful stop of the running crawl."""
        if self._worker is not None:
            self._worker.request_stop()
            self._append_log("Stop requested \u2014 waiting for current operation to finish\u2026")

    def _retry_failed(self) -> None:
        """Re-queue failed URLs and restart the crawl."""
        url = self._start_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Validation Error", "Please enter a start URL.")
            return

        project_name = self._project_name_edit.text().strip()
        if not project_name:
            QMessageBox.warning(self, "Validation Error", "Please enter a project name.")
            return

        config = self._build_config()
        self._launch_worker(config, retry=True)

    def _launch_worker(self, config: AppConfig, *, retry: bool) -> None:
        """Create a :class:`CrawlWorker`, wire its signals, and start the
        background thread."""
        self._set_crawling_state()
        self._progress_bar.setValue(0)
        self._summary_label.setText("Pages: 0 | Assets: 0 | Failed: 0")

        self._worker = CrawlWorker(config, retry=retry)
        self._worker.progress.connect(self._update_progress)
        self._worker.status.connect(self._update_status)
        self._worker.url_processing.connect(self._update_url)
        self._worker.log_message.connect(self._append_log)
        self._worker.error.connect(self._on_crawl_error)
        self._worker.finished.connect(self._on_crawl_finished)

        self._worker_thread = threading.Thread(
            target=self._worker.run,
            name="crawl-worker",
            daemon=True,
        )
        self._worker_thread.start()
        self._append_log("Crawl started.")

    # ==================================================================
    # Worker signal handlers (called on the main/GUI thread via Qt signals)
    # ==================================================================

    @Slot(int, int)
    def _update_progress(self, current: int, total: int) -> None:
        """Update the progress bar from worker signals."""
        if total > 0:
            pct = int(current / total * 100)
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._progress_bar.setFormat(f"{pct}%  ({current}/{total})")
        else:
            self._progress_bar.setRange(0, 0)  # indeterminate

        # Also update the summary label from the worker's manager state
        self._refresh_summary()

    @Slot(str)
    def _update_status(self, msg: str) -> None:
        """Update the status label."""
        self._status_label.setText(msg)
        self._status_bar.showMessage(msg)

    @Slot(str)
    def _update_url(self, url: str) -> None:
        """Update the current-URL label with an elided version."""
        metrics = self._current_url_label.fontMetrics()
        available_width = self._current_url_label.width() - 8
        if available_width < 50:
            available_width = 600
        elided = metrics.elidedText(url, Qt.TextElideMode.ElideMiddle, available_width)
        self._current_url_label.setText(elided)

    @Slot(str)
    def _append_log(self, msg: str) -> None:
        """Append a line to the log pane and auto-scroll."""
        self._log_text.appendPlainText(msg)
        self._log_text.moveCursor(QTextCursor.MoveOperation.End)

    @Slot(str)
    def _on_crawl_error(self, msg: str) -> None:
        """Handle an error signal from the worker."""
        self._append_log(f"ERROR: {msg}")

    @Slot()
    def _on_crawl_finished(self) -> None:
        """Re-enable buttons and update status when the crawl is done."""
        self._refresh_summary()
        self._set_idle_state()
        self._status_label.setText("Finished")
        self._status_bar.showMessage("Crawl finished")
        self._append_log("Crawl finished.")
        self._worker = None
        self._worker_thread = None

    # ==================================================================
    # Preview / export actions
    # ==================================================================

    def _open_output_folder(self) -> None:
        """Open the project output directory in the system file explorer."""
        project_dir = self._resolve_project_dir()
        if project_dir is None:
            return

        if not project_dir.is_dir():
            QMessageBox.information(
                self,
                "Not Found",
                f"The output folder does not exist yet:\n{project_dir}",
            )
            return

        path_str = str(project_dir)
        if sys.platform == "win32":
            os.startfile(path_str)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path_str])
        else:
            subprocess.Popen(["xdg-open", path_str])

    def _start_preview_http(self) -> None:
        """Launch a local HTTP preview server and open the browser."""
        project_dir = self._resolve_project_dir()
        if project_dir is None:
            return

        if not project_dir.is_dir():
            QMessageBox.information(
                self,
                "Not Found",
                f"The output folder does not exist yet:\n{project_dir}",
            )
            return

        # Stop any previously running preview server
        if self._preview_server is not None and self._preview_server.is_running:
            self._preview_server.stop()

        try:
            self._preview_server = PreviewServer(root_dir=project_dir, port=8080)
            url = self._preview_server.start()
            self._append_log(f"Preview server started at {url}")
            webbrowser.open(url)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Preview Error",
                f"Could not start preview server:\n{exc}",
            )

    def _start_preview_file(self) -> None:
        """Open the project's index.html with a file:// URI in the browser."""
        project_dir = self._resolve_project_dir()
        if project_dir is None:
            return

        index_path = project_dir / "pages" / "index.html"
        if not index_path.is_file():
            # Fall back to just opening the directory
            index_path = project_dir
            if not index_path.is_dir():
                QMessageBox.information(
                    self,
                    "Not Found",
                    f"The output folder does not exist yet:\n{project_dir}",
                )
                return

        webbrowser.open(index_path.resolve().as_uri())

    def _export_zip(self) -> None:
        """Create a ZIP archive of the entire project output folder."""
        project_dir = self._resolve_project_dir()
        if project_dir is None:
            return

        if not project_dir.is_dir():
            QMessageBox.information(
                self,
                "Not Found",
                f"The output folder does not exist yet:\n{project_dir}",
            )
            return

        default_name = project_dir.name + ".zip"
        zip_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export ZIP",
            str(project_dir.parent / default_name),
            "ZIP Archives (*.zip)",
        )
        if not zip_path:
            return

        try:
            self._append_log(f"Exporting to {zip_path}\u2026")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in project_dir.rglob("*"):
                    if file.is_file():
                        arcname = file.relative_to(project_dir)
                        zf.write(file, arcname)
            self._append_log(f"Export complete: {zip_path}")
            QMessageBox.information(
                self,
                "Export Complete",
                f"Project exported to:\n{zip_path}",
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Export Error",
                f"Failed to create ZIP:\n{exc}",
            )

    # ==================================================================
    # Helpers
    # ==================================================================

    def _browse_output_folder(self) -> None:
        """Show a folder-selection dialog for the output directory."""
        current = self._output_dir_edit.text().strip()
        start_dir = current if current and Path(current).is_dir() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Folder", start_dir
        )
        if folder:
            self._output_dir_edit.setText(folder)

    def _resolve_project_dir(self) -> Path | None:
        """Return the full project directory path, or *None* after showing a
        warning if required fields are empty."""
        output_dir = self._output_dir_edit.text().strip()
        project_name = self._project_name_edit.text().strip()
        if not output_dir or not project_name:
            QMessageBox.warning(
                self,
                "Missing Information",
                "Please fill in both the output folder and the project name.",
            )
            return None
        return Path(output_dir) / project_name

    def _refresh_summary(self) -> None:
        """Update the summary label from the worker's manager state."""
        if self._worker is not None and self._worker.manager is not None:
            state = self._worker.manager.state
            if state is not None:
                pages = len(state.visited_urls)
                assets = len(state.asset_hashes)
                failed = len(state.failed_urls)
                self._summary_label.setText(
                    f"Pages: {pages} | Assets: {assets} | Failed: {failed}"
                )

    @staticmethod
    def _parse_text_list(widget: QTextEdit) -> list[str]:
        """Parse the contents of a :class:`QTextEdit` as a list of non-empty,
        stripped lines."""
        raw = widget.toPlainText()
        return [line.strip() for line in raw.splitlines() if line.strip()]

    # ==================================================================
    # Cleanup
    # ==================================================================

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        """Ensure background resources are cleaned up on window close."""
        if self._worker is not None:
            self._worker.request_stop()

        if self._preview_server is not None and self._preview_server.is_running:
            self._preview_server.stop()

        super().closeEvent(event)  # type: ignore[arg-type]
