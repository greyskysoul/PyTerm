# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PyCom.

Build (run from the repository root):
    pyinstaller --noconfirm packaging/pycom.spec
Output: dist/pycom/  — onedir 布局（console app，TUI），体积优化要点：
  - 排除 ssl/网络模块（省 ~6MB libcrypto/libssl）
  - 排除未使用的 Textual 组件与 stdlib 扩展模块
  - 启用 strip + UPX（Windows 上 Python 3.14 因 CFG 自动跳过 UPX）
"""

from pathlib import Path

block_cipher = None
spec_dir = Path(SPECPATH)  # the directory containing this spec file (packaging/)
repo_root = spec_dir.parent

# ship app.tcss so importlib.resources can find it in the frozen bundle
datas = [
    (str(repo_root / "src" / "pycom" / "resources" / "app.tcss"), "pycom/resources"),
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
        "IPython",
        "jupyter",
        "notebook",
        "pandas",
        "scipy",
        "setuptools",
        "distutils",
        # 串口终端不使用网络/SSL，排除可省 ~6MB（libcrypto/libssl）
        "ssl",
        "_ssl",
        "hashlib",
        "_hashlib",
        "email",
        "xmlrpc",
        "ftplib",
        "imaplib",
        "poplib",
        "smtplib",
        "telnetlib",
        # 未被任何依赖使用的 stdlib 扩展模块
        "_decimal",
        "_lzma",
        "_bz2",
        "_zstd",
        "_multiprocessing",
        "_wmi",
        # Textual 未使用、且未被核心/已用组件引用的组件
        "textual.widgets._collapsible",
        "textual.widgets._content_switcher",
        "textual.widgets._digits",
        "textual.widgets._footer",
        "textual.widgets._header",
        "textual.widgets._key_panel",
        "textual.widgets._link",
        "textual.widgets._list_item",
        "textual.widgets._list_view",
        "textual.widgets._log",
        "textual.widgets._markdown",
        "textual.widgets._masked_input",
        "textual.widgets._pretty",
        "textual.widgets._progress_bar",
        "textual.widgets._radio_button",
        "textual.widgets._radio_set",
        "textual.widgets._rich_log",
        "textual.widgets._rule",
        "textual.widgets._selection_list",
        "textual.widgets._sparkline",
        "textual.widgets._switch",
        "textual.widgets._tabbed_content",
        "textual.widgets._tabs",
        "textual.widgets._welcome",
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
    [],
    exclude_binaries=True,
    name="pycom",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# onedir 布局：避免 onefile 每次启动自解压的开销，且便于在嵌入式设备上
# 检查/删除未使用的运行时文件（体积通常也更小）。
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="pycom",
)
