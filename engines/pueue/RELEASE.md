# Pueue Engine Release Checklist

Record each command, tool version, exit status, coverage total, and artifact
hash in `docs/pueue-engine/evidence-register.adoc` before release.

- Confirm `pueue --version` and `pueued --version` both report `4.0.4`.
- Run `uv run python scripts/audit_pueue_engine_docs.py` from the repository root.
- Render every file in `docs/pueue-engine/` with Asciidoctor.
- Run the root Python Ruff, basedpyright, pytest coverage, build, and Twine checks.
- Run `just verify` and `just concurrency-20` from `engines/pueue/`.
- Confirm LLVM coverage reaches authored functions 100%, lines at least 95%,
  and regions at least 90%; list only compiler-generated exclusions.
- Run the isolated daemon and stdio black-box suites twice from a clean checkout.
- Confirm no `loommux-pueue`, isolated `pueued`, descendant task, socket, or
  test harness tree under `target/i` remains after each suite; also confirm the
  legacy `target/isolated-pueue` path is absent.
- Run `cargo package --locked` and inspect the package file list.
- Run `cargo build --release --locked`, start the binary without a daemon, and
  confirm the stable startup error is written only to stderr.
- Run `uv build` and `uv run twine check dist/*` for the Python distribution.
- Run `git diff --check`, the documentation stale-claim audit, and a secret scan.
- Compare tool-schema hashes, result-schema hashes, coverage conclusions, and
  packaged artifact hashes across two complete verification runs.
- Inspect the final scoped diff and commit only Pueue engine, shared workspace
  contract, directly affected tests, documentation, and build entrypoints.
