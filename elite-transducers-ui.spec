# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['elite_transducers_ui.py'],
    pathex=[],
    binaries=[],
    datas=[('elite_haptics_engine.py', '.'), ('effects.json', '.'), ('triggers.json', '.'),
           ('default_config.json', '.'), ('EFFECTS_GUIDE.md', '.'),
           ('TRIGGERS_GUIDE.md', '.')],
    hiddenimports=['numpy', 'sounddevice', 'watchdog.observers', 'watchdog.events'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='elite-haptics',
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
)
