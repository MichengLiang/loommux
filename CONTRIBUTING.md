# Contributing to loommux

## Development setup

Use Python 3.13 or newer and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/MichengLiang/loommux.git
cd loommux
uv sync --locked --group dev
```

## Feature freeze until the rebuild

loommux takes no new features until the rebuild described in
[`docs/rebuild-brief.md`](docs/rebuild-brief.md) is done; section 1 there says
why. Defects are still fixed, each with a behavior test, because the tests are
what carries a rule into the new skeleton. A new requirement goes into the
brief's waiting-model or open-questions section, or into an issue
(`docs/issues/`), instead of into the current code.

## Before opening a pull request

Keep changes scoped to one user-visible behavior or maintenance concern. Add
or update tests whenever behavior changes, especially for execution identity,
kernel lifecycle, combined-output order, and MCP result-channel behavior.

Run the complete local gate:

```bash
uv run pytest
uv run ruff check src tests examples scripts
uv run basedpyright src
uv build
uv run twine check dist/*
```

Changes to resource or kernel lifecycle, concurrency, or output recording
also need the load check on Linux:

```bash
uv run python scripts/stress.py --seconds 180
```

It loads a private server with random, cancelled, and disconnected tool calls,
fails if the console stops answering, and then requires every kernel and file
descriptor to be released. Unit tests cannot show either failure: both
appeared only after minutes of mixed load.

## What tests assert

Tests assert behavior: what a tool accepts and returns, which kernel a call
runs in, when a kernel is kept or retired, in what order output arrives. They
do not assert the wording of MCP tool descriptions, docstrings, help text, or
documents. A text is its own authority, written and reviewed by whoever
changes it; a test that copies its phrases holds a second copy of the same
knowledge, turns red on every rewording, and finds no defect.

What a text claims that a machine can check is tested by running it. Every
`%%loommux` header example in the tool descriptions must be accepted by the
real header parser (`tests/mcp/test_server.py`), so the grammar and the
examples an agent copies cannot drift apart. When a test needs a text the code
produces, such as the `%%loommux?` help, it imports the constant
(`HEADER_HELP`) instead of repeating it.

Use clear commit messages and describe the behavioral reason for non-obvious
code or protocol choices. Do not add access tokens, kernel output containing
secrets, local workspace configuration, or generated build artifacts to a
pull request.

## Design changes

The public execution contract is documented under [`docs/`](docs/). Update the
relevant design document when changing a public tool input, result surface,
execution lifecycle rule, or output-reading coordinate.

## Releases

The package version is declared in [`pyproject.toml`](pyproject.toml). A release
must update [CHANGELOG.md](CHANGELOG.md), pass the complete local gate and the
load check, and use
an annotated `v<version>` tag that exactly matches the package version. Pushing
that tag starts the release workflow: it independently verifies the match,
reruns the quality gate, checks the built distribution, uploads to PyPI, and
creates or updates the GitHub Release from the matching changelog entry. A
manual retry requires an explicit release tag.

## Reporting defects

Use the issue templates for reproducible defects and feature proposals. Report
security-sensitive issues through the process in [SECURITY.md](SECURITY.md),
not a public issue.
