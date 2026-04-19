# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Hue 1.0.0
# Build with: pyinstaller hue.spec

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('colour_data.py', '.'),
        ('icons/hue_16.png',   'icons'),
        ('icons/hue_32.png',   'icons'),
        ('icons/hue_48.png',   'icons'),
        ('icons/hue_64.png',   'icons'),
        ('icons/hue_128.png',  'icons'),
        ('icons/hue_256.png',  'icons'),
        ('icons/hue_tray_16.png', 'icons'),
        ('icons/hue_tray_22.png', 'icons'),
    ],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtGui',
        'PyQt6.QtCore',
        'colorsys',
        'colour_data',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='hue',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icons/hue_256.png',
)
