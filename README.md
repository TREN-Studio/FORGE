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
pip install forge-agent
```

## Quick Start

```bash
forge status
forge discover
forge operate "Analyze this repository and save a summary file"
forge operate "Read forge/brain/operator.py and explain the execution flow"
```

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
- safety, validation, and recovery layers
- grounded workspace analysis and file reading skills

## Windows Releases

- The repository now includes a Windows release workflow in `.github/workflows/release_forge_windows.yml`.
- It builds `FORGE-Desktop.exe` and `FORGE-Setup-<version>.exe` on GitHub Actions.
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
- Organization: https://github.com/TREN-Studio
- Repository: https://github.com/TREN-Studio/FORGE

## Production Deployment

FORGE now keeps the public website bundle inside [`site/`](site), which makes GitHub the source of truth for `https://www.trenstudio.com/FORGE`.

### Auto-Deploy Pipeline

GitHub Actions workflow: [`.github/workflows/deploy_forge_site.yml`](.github/workflows/deploy_forge_site.yml)

Deployment script: [`tools/deploy_hostinger_site.py`](tools/deploy_hostinger_site.py)

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
