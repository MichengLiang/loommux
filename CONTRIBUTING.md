# Contributing to loommux

## Development setup

Use Python 3.10 or newer and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/MichengLiang/loommux.git
cd loommux
uv sync --group dev
```

## Before opening a pull request

Keep changes scoped to one user-visible behavior or maintenance concern. Add
or update tests whenever behavior changes, especially for execution identity,
kernel lifecycle, combined-output order, and MCP result-channel behavior.

Run the complete local gate:

```bash
uv run pytest
uv run ruff check src tests
uv run basedpyright src
uv build
uv run twine check dist/*
```

Use clear commit messages and describe the behavioral reason for non-obvious
code or protocol choices. Do not add access tokens, kernel output containing
secrets, local workspace configuration, or generated build artifacts to a
pull request.

## Design changes

The public execution contract is documented under [`docs/`](docs/). Update the
relevant design document when changing a public tool input, result surface,
execution lifecycle rule, or output-reading coordinate.

## Reporting defects

Use the issue templates for reproducible defects and feature proposals. Report
security-sensitive issues through the process in [SECURITY.md](SECURITY.md),
not a public issue.
