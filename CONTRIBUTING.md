# Contributing to AxonOps

Thank you for contributing. This guide covers everything you need to get started.

## Development setup

```bash
git clone https://github.com/yourusername/axonops
cd axonops
python -m venv .venv && source .venv/bin/activate
pip install -e ./axonops_sdk
pip install -e ./plugins/psutil ./plugins/docker ./plugins/prometheus ./plugins/kubernetes ./plugins/aws
pip install -e ./axonops_core
pip install pytest pytest-asyncio ruff mypy
pytest plugins/*/tests/ -v
axonops --log-level DEBUG
```

## Writing a plugin

1. `pip install axonops-sdk`
2. Implement `BaseCollector`, `BaseAction`, or `BaseStrategy`
3. Declare entry points in `pyproject.toml`
4. Write tests in `tests/`

See any existing plugin for a complete example.

## Code style

- Format: `ruff format .`
- Lint: `ruff check .`
- Types: `mypy axonops_sdk/`

## Community plugins

Built a plugin for a new system? Add it to `PLUGINS.md` via PR.
