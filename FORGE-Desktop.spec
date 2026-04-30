# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH).resolve()
ENTRYPOINT = ROOT / "forge_desktop.py"
ICON = ROOT / "assets" / "forge-desktop-icon.ico"

HIDDEN_IMPORTS = [
    "forge.providers.registry",
    "forge.providers.groq",
    "forge.providers.gemini",
    "forge.providers.nvidia",
    "forge.providers.cloudflare",
    "forge.providers.deepseek",
    "forge.providers.openrouter",
    "forge.providers.mistral",
    "forge.providers.together",
    "forge.providers.ollama",
    "forge.providers.anthropic",
    "forge.providers.openai",
    "pydantic.deprecated.class_validators",
    "pydantic_core",
    "httpx._transports.default",
]

DATA_FILES = [
    (str(ROOT / "forge" / "skills_catalog"), "forge/skills_catalog"),
    (str(ROOT / "assets"), "assets"),
]

EXCLUDES = [
    "matplotlib",
    "numpy",
    "pandas",
    "torch",
]


a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATA_FILES,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
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
    name="FORGE-Desktop",
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
    icon=[str(ICON)],
)
