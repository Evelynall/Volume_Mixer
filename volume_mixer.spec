from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_dir = Path.cwd()
entry_script = str(project_dir / "volume_mixer.py")
icon_path = project_dir / "app_icon.ico"

datas = collect_data_files("sv_ttk") + [
    (str(project_dir / "app_icon.png"), "."),
    (str(project_dir / "app_icon.ico"), "."),
]
hiddenimports = [
    "PIL._tkinter_finder",
    "pystray._win32",
    "six",
    "six.moves",
    "comtypes.stream",
] + collect_submodules("PIL")

block_cipher = None


a = Analysis(
    [entry_script],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "numpy.typing",
        "pandas",
        "scipy",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "pytest",
        "unittest",
        "tkinter.test",
        "PIL.FpxImagePlugin",
        "PIL.MicImagePlugin",
        "PIL.GribStubImagePlugin",
        "PIL.Hdf5StubImagePlugin",
        "PIL.MpegImagePlugin",
        "PIL.PdfImagePlugin",
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
    name="VolumeMixer",
    icon=str(icon_path),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VolumeMixer",
)
