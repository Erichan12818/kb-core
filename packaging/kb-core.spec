# PyInstaller spec for the kb-core desktop build.
#
# Build:  pyinstaller packaging/kb-core.spec --noconfirm
# Output: dist/kb-core.app (macOS) or dist/kb-core/ (Linux, Windows)
#
# The embedding model is deliberately not bundled: it is ~2.3GB, licensed
# separately, and downloading it on first launch keeps the build small enough
# to publish. kb/desktop.py reports that download rather than looking hung.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

REPO = Path(SPECPATH).parent

# fastembed and qdrant_client both ship data and lazily import submodules that
# static analysis misses, so they are collected wholesale.
datas, binaries, hiddenimports = [], [], []
for package in ("fastembed", "qdrant_client", "tokenizers", "onnxruntime"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# The UI is one file read at runtime.
datas += [(str(REPO / "kb" / "static" / "ui.html"), "kb/static")]
datas += [(str(REPO / "kb" / "eval_queries.yaml"), "kb")]
datas += collect_data_files("kb")

hiddenimports += [
    "kb.api", "kb.add", "kb.apply", "kb.audit", "kb.catalog", "kb.chat",
    "kb.config", "kb.eval", "kb.health", "kb.image", "kb.index",
    "kb.index_update", "kb.ingest", "kb.llm", "kb.mcp", "kb.notify",
    "kb.proposals", "kb.recall", "kb.session_context", "kb.store", "kb.worker",
]

a = Analysis(
    [str(REPO / "packaging" / "launcher.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kb-core",
    debug=False,
    strip=False,
    upx=False,
    console=True,          # first run prints model-download progress
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="kb-core",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="kb-core.app",
        bundle_identifier="app.mybuilt.kbcore",
        info_plist={
            "CFBundleName": "kb-core",
            "CFBundleDisplayName": "kb-core",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # No dock icon: this is a background service with a browser UI.
            "LSUIElement": False,
        },
    )
