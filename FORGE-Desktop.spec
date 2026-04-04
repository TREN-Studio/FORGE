# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\larbi\\My Projects\\FORGE\\forge-agent-v1.0\\forge-agent\\forge_desktop.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\larbi\\My Projects\\FORGE\\forge-agent-v1.0\\forge-agent\\forge\\skills_catalog', 'forge/skills_catalog')],
    hiddenimports=['forge.providers.groq', 'forge.providers.gemini', 'forge.providers.ollama', 'forge.providers.deepseek', 'forge.providers.openrouter'],
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
    name='FORGE-Desktop',
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
    icon=['C:\\Users\\larbi\\My Projects\\FORGE\\forge-agent-v1.0\\forge-agent\\assets\\forge-desktop-icon.ico'],
)
