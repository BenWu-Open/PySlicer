# -*- mode: python ; coding: utf-8 -*-
import os
import vispy

# Get paths for VisPy internal folders
vispy_dir = os.path.dirname(vispy.__file__)
vispy_glsl_path = os.path.join(vispy_dir, 'glsl')
vispy_data_path = os.path.join(vispy_dir, 'io', '_data')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        (vispy_glsl_path, os.path.join('vispy', 'glsl')),
        (vispy_data_path, os.path.join('vispy', 'io', '_data')),
        ('./UI/cgu.ico', '.'),
        ('plugins', 'plugins')
    ],
    hiddenimports=['vispy.app.backends._pyqt6', 'cv2'],
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