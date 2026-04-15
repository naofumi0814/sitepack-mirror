# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SitePack.

Build (Windows / macOS / Linux):

    pip install pyinstaller
    pyinstaller --noconfirm sitepack.spec

The result is a single-file executable in ``dist/SitePack`` (or
``dist/SitePack.exe`` on Windows) that can be launched by double-clicking.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH)

block_cipher = None

# --- Hidden imports / data --------------------------------------------------
hiddenimports = []
hiddenimports += collect_submodules("sitepack")
hiddenimports += collect_submodules("PySide6")
hiddenimports += collect_submodules("tinycss2")
hiddenimports += collect_submodules("bs4")
hiddenimports += collect_submodules("httpx")

datas = []
# Bundle README so it can be opened from inside the app if needed
readme = ROOT / "README.md"
if readme.exists():
    datas.append((str(readme), "."))

# --- Analysis ---------------------------------------------------------------
a = Analysis(
    [str(ROOT / "src" / "sitepack" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unused heavy optional deps to keep the binary small
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --- Single-file executable -------------------------------------------------
# console=False = GUI mode (no console window pops up on double-click).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SitePack",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add path to .ico/.icns here when an icon asset exists
)
