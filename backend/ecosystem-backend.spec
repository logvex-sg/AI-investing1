# PyInstaller spec for the ECOSYSTEM backend sidecar.
#
# Produces a single-file executable that the desktop shell starts and stops.
# The Alembic migrations and configuration travel inside the bundle so the
# frozen backend can create and upgrade its own schema.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

ROOT = Path(SPECPATH).resolve()

datas = [
    (str(ROOT / "alembic.ini"), "."),
    (str(ROOT / "alembic"), "alembic"),
]

hiddenimports = [
    "asyncpg",
    "pgvector.sqlalchemy",
    "alembic.ddl.postgresql",
    "ecosystem.desktop.launcher",
    "ecosystem.desktop.postgres",
    "ecosystem.desktop.paths",
]
hiddenimports += collect_submodules("ecosystem")
hiddenimports += collect_submodules("alembic")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("pydantic")

a = Analysis(
    [str(ROOT / "ecosystem" / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "IPython"],
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
    name="ecosystem-backend",
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
