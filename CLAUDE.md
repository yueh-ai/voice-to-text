# Claude Code Project Guidelines

## Virtual Environment

This project uses **uv** for virtual environment and package management.

- `pyproject.toml` - Project configuration and dependencies
- `uv.lock` - Locked dependencies
- `.venv/` - Virtual environment directory (created by uv)

### Common Commands

```bash
# Sync dependencies (install from lock file)
uv sync

# Add a dependency
uv add <package>

# Add a dev dependency
uv add --group dev <package>

# Run a command in the virtual environment
uv run <command>

# Run Python
uv run python <script.py>
```


