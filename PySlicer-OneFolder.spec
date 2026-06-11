# -*- mode: python ; coding: utf-8 -*-
import os
import vispy
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Get paths for VisPy internal folders
vispy_dir = os.path.dirname(vispy.__file__)
vispy_glsl_path = os.path.join(vispy_dir, 'glsl')
vispy_data_path = os.path.join(vispy_dir, 'io', '_data')

# 1. Combine your existing data files with the AI model data
added_datas = [
    (vispy_glsl_path, os.path.join('vispy', 'glsl')),
    (vispy_data_path, os.path.join('vispy', 'io', '_data')),
    ('./UI/cgu.ico', '.'),
    ('plugins', 'plugins')
]
added_datas += collect_data_files('totalsegmentator')
added_datas += collect_data_files('nnunetv2')

# 2. Combine your existing hidden imports with the AI scripts
hidden_imports = ['vispy.app.backends._pyqt6', 'cv2']
hidden_imports += collect_submodules('totalsegmentator')
hidden_imports += collect_submodules('nnunetv2')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=added_datas,             # <--- Now uses the combined list
    hiddenimports=hidden_imports,  # <--- Now uses the combined list
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PySlicer',
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
    icon=['cgu.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PySlicer',
)