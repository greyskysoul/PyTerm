# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PyTerm.

Build (run from the repository root):
    pyinstaller --noconfirm packaging/pyterm.spec
Output: dist/pyterm(.exe)  — console app (TUI), so console=True.
"""

from pathlib import Path

block_cipher = None
spec_dir = Path(SPECPATH)  # the directory containing this spec file (packaging/)
repo_root = spec_dir.parent

# ship app.tcss so importlib.resources can find it in the frozen bundle
datas = [
    (str(repo_root / "src" / "pyterm" / "resources" / "app.tcss"), "pyterm/resources"),
]

a = Analysis(
    [str(spec_dir / "launcher.py")],
    pathex=[str(repo_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "serial.tools.list_ports",
        "serial.tools.list_ports_common",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="pyterm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
