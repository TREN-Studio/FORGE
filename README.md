# FORGE

**Free Open Reasoning and Generation Engine**

FORGE is an English-first, multilingual AI operator that routes across free and local models, chooses the best execution path for the task, and runs through a modular skill system built for real-world work.

[![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-orange.svg)](https://python.org)
[![Website](https://img.shields.io/badge/website-trenstudio.com%2FFORGE-orange.svg)](https://www.trenstudio.com/FORGE)

## Why FORGE

- English-first product surface with multilingual task support.
- Smart model routing across free providers and local runtimes.
- Skill-based execution brain with planning, safety, validation, and recovery.
- Persistent context and memory for long-running operator workflows.
- Safe artifact generation for reports, analysis outputs, and execution traces.

## Core Capabilities

### Universal Model Routing

FORGE connects to multiple providers and local runtimes, scores candidates for the active task, and selects the strongest available path.

### Skill-Based Operator Brain

The operator is split into explicit layers:

1. Intent resolution
2. Structured planning
3. Skill routing
4. Safety guard
5. Execution runtime
6. Validation
7. Recovery and fallback
8. Response composition

### Grounded Local Execution

FORGE can inspect a workspace, read files safely, analyze the codebase with evidence, and write output artifacts without mutating source files.

## Architecture

```text
forge/
  brain/         Core orchestration brain
  core/          Provider routing, quotas, discovery, session runtime
  memory/        Session and persistent context
  providers/     Provider adapters
  recovery/      Retry and fallback handling
  safety/        Risk policy and confirmation logic
  skills/        Skill contracts, registry, loader, router, runtime
  skills_catalog/Pluggable skill folders
  tools/         Safe local execution tools
  validation/    Output and completion validation
```

## Skill System

Every skill is self-contained in its own folder:

```text
skills_catalog/<skill-name>/
  SKILL.md
  schema.json        # optional
  executor.py        # optional
```

This makes new skills pluggable without rewriting the core brain.

## Install

```bash
pip install forge-agent==1.1.5
forge --version  # FORGE 1.1.5
```

## Quick Start

```bash
forge --version
forge status
forge discover
forge operate "Analyze this repository and save a summary file"
forge operate "Read forge/brain/operator.py and explain the execution flow"
```

Current Python package release: `1.1.5` on PyPI.

## Python API

```python
import forge

result = forge.operate("Analyze this project and save a summary file")
print(result.result)
```

## Safety Model

- Untrusted external skills do not run automatically.
- Medium-risk actions can be forced into dry-run mode.
- High-risk actions require confirmation.
- Validation runs before success is reported.
- FORGE never claims execution without evidence.

## Status

Current foundation includes:

- model routing and provider registry
- quota management and model discovery
- skill registry and skill router
- skill governance metadata, precondition checks, and gated Tier 4 routing
- live desktop execution streaming with visible plan and step progress
- safety, validation, and recovery layers
- grounded workspace analysis and file reading skills

## Public Releases

- Current public release line: `1.1.5`.
- The canonical public install path is PyPI: https://pypi.org/project/forge-agent/1.1.5/
- GitHub's latest stable release must also resolve to `v1.1.5`.
- The canonical public release record is the GitHub Release for the matching tag: https://github.com/TREN-Studio/FORGE/releases/tag/v1.1.5
- The PyPI publishing workflow in `.github/workflows/publish-pypi.yml` builds the wheel and source distribution, publishes to PyPI through Trusted Publisher, and attaches `dist/*` to the GitHub Release.
- The Windows release workflow in `.github/workflows/release_forge_windows.yml` is retained for future desktop installer releases. Desktop installer links are not shown on the public downloads page unless those artifacts exist for the current release.
- The supported desktop build entrypoint is `python tools/build_windows_desktop.py`; that script is the source of truth for orchestration and invokes the portable `FORGE-Desktop.spec`.
- Release packaging runs through `python tools/package_release_assets.py`, which writes release assets under `release-assets/` only. It does not publish or sync binary files into `site/downloads/`.
- The public downloads page at `site/downloads/index.html` reads `release-manifest.json` and points users to PyPI plus existing GitHub Release assets only.
- `python tools/verify_release_public_assets.py --manifest release-assets/release-manifest.json --require-mirror` verifies version, size, SHA256, GitHub Release presence, and byte identity for the Hostinger mirror.
- `tools/deploy_hostinger_site.py` deploys the downloads page, portal, and release manifest only; the TREN Studio root page owns `https://www.trenstudio.com/FORGE/`.
- Legacy desktop spec variants were removed. `FORGE-Desktop.spec` is the only supported PyInstaller spec.
- If `WINDOWS_PFX_BASE64` and `WINDOWS_PFX_PASSWORD` are configured in GitHub Secrets, the workflow signs both artifacts before publishing the GitHub Release.
- Until code signing is configured, Windows SmartScreen and local execution reputation checks can still block downloaded installers.

## Roadmap

1. Add guarded file editing and patch execution
2. Add web research and publishing skills
3. Add audit logs and evidence snapshots for every action
4. Add richer test coverage and benchmark suites

## Contributing

FORGE is designed as an open-source operator platform. Contributions should preserve:

- modular contracts
- safety-by-default behavior
- grounded execution
- production-oriented output quality

## License

MIT

## Links

- Website: https://www.trenstudio.com/FORGE
- Downloads: https://www.trenstudio.com/FORGE/downloads/
- Organization: https://github.com/TREN-Studio
- Repository: https://github.com/TREN-Studio/FORGE

## Production Deployment

FORGE keeps its public downloads page and portal bundle inside [`site/`](site). The production page at `https://www.trenstudio.com/FORGE/` remains the TREN Studio project page; the FORGE download interface lives at `https://www.trenstudio.com/FORGE/downloads/` and is deployed from `site/downloads/index.html`.

GitHub Release is the canonical release record. PyPI is the recommended install path for the current public Python package. Hostinger may serve an official mirror under `https://www.trenstudio.com/FORGE/downloads/`, but only when the files are copied from the same CI-built release assets and pass SHA256, file size, version, and byte-identity verification.

The current public download set is:

- `https://pypi.org/project/forge-agent/1.1.5/`
- `https://github.com/TREN-Studio/FORGE/releases/download/v1.1.5/forge_agent-1.1.5-py3-none-any.whl`
- `https://github.com/TREN-Studio/FORGE/releases/download/v1.1.5/forge_agent-1.1.5.tar.gz`

No official-site binary mirror is published for the current release.

### Auto-Deploy Pipeline

GitHub Actions workflow: [`.github/workflows/deploy_forge_site.yml`](.github/workflows/deploy_forge_site.yml)

Deployment script: [`tools/deploy_hostinger_site.py`](tools/deploy_hostinger_site.py). It preserves the remote `/FORGE/index.html` root page unless this repository explicitly adds a root `site/index.html`.

Deploy guard: the script refuses to deploy `site/index.html` to `/FORGE/index.html` unless `--allow-root-index-deploy` or `FORGE_ALLOW_ROOT_INDEX_DEPLOY=1` is provided. Normal downloads/portal deploys also compare the remote `/FORGE/index.html` hash before and after upload and fail if it changes.

Route verification: [`tools/verify_forge_public_routes.py`](tools/verify_forge_public_routes.py) checks that `/FORGE/` is still the original project page, `/FORGE/downloads/` is still the downloads page, and the public `release-manifest.json` matches the expected release manifest.

Required GitHub repository secrets:

- `HOSTINGER_HOST`
- `HOSTINGER_PORT`
- `HOSTINGER_USERNAME`
- `HOSTINGER_PASSWORD`
- `HOSTINGER_REMOTE_ROOT`

Recommended `HOSTINGER_REMOTE_ROOT` value:

```text
domains/trenstudio.com/public_html/FORGE
```

Manual local deploy remains available:

```bash
python tools/deploy_hostinger_site.py
```
