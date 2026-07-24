# Define project root
root := justfile_directory()

# Point pyo3 at the project venv's interpreter.
#
# The crate targets abi3-py310, so pyo3-build-config refuses to build against an
# older interpreter -- and a bare `cargo` picks up whatever `python3` is on
# PATH, which can be older still. This lives here rather than in
# .cargo/config.toml on purpose: a repo-level cargo config would also apply
# inside the release workflow's cross-compilation containers, where `.venv`
# does not exist and cargo would fail with "failed to run the Python
# interpreter". CI sets PYO3_PYTHON itself, or lets maturin manage it.
export PYO3_PYTHON := justfile_directory() / ".venv/bin/python"

# List available recipes
default:
    @just --list

# ----------------------------------------------------------------------------
# dev: tests and quality checks
# ----------------------------------------------------------------------------

# Build the extension into the venv in-place (debug), so tests see it
[group('dev')]
dev:
    uv run maturin develop --uv

# Build it optimized instead. Correctness tests do not care, but anything
# measuring throughput does: a debug build is ~15x slower on this workload, so
# benching one compares an unoptimized plugin against an optimized tldextract
# and reports a meaningless ratio.
[group('dev')]
dev-release:
    uv run maturin develop --uv --release

# Run the Rust unit tests and the Python suite
[group('dev')]
test *args: dev
    cargo test --lib
    uv run pytest {{args}}

# Run the test suite with coverage (branch + term-missing report)
[group('dev')]
test_cov *args: dev
    uv run pytest --cov=polars_tldextract --cov-report=term-missing --cov-branch {{args}}

# Throughput: this plugin vs. tldextract through map_elements
#
# Depends on `dev-release`, not `dev`. It leaves an optimized build installed,
# so run `just dev` afterwards to get back to a fast edit-test loop.
[group('dev')]
bench: dev-release
    uv run pytest tests/test_bench.py -s -m bench

# Re-vendor the Public Suffix List from publicsuffix.org
[group('dev')]
refresh-psl:
    uv run python scripts/refresh_psl.py

# Lint with ruff and auto-fix what it safely can
[group('dev')]
lint *args:
    uv run ruff check --fix {{args}}

# Format the codebase with ruff
[group('dev')]
format *args:
    uv run ruff format {{args}}

# Lint (no fixes) + format check + tests -- the full pre-commit gate
[group('dev')]
check: dev
    cargo fmt --check
    cargo clippy --all-targets -- -D warnings
    cargo test --lib
    uv run ruff check
    uv run ruff format --check
    uv run pytest

# Run all pre-commit hooks via prek (ruff, ty, pydoclint, cargo, mdformat, taplo)
[group('dev')]
precommit *args:
    uv run prek run --all-files --show-diff-on-failure {{args}}

# ----------------------------------------------------------------------------
# release: build & publish to PyPI
#
# Releases are normally cut by CI: push a v<version> tag and
# .github/workflows/release.yml builds and publishes via PyPI Trusted
# Publishing. The recipes below are the manual equivalent.
#
# NOTE: published versions are immutable -- run `just bump` before publishing.
# ----------------------------------------------------------------------------

# Print the current package version (from pyproject.toml)
[group('release')]
version:
    uv version --short

# Bump the version: `just bump patch` | minor | major
[group('release')]
bump level="patch":
    uv version --bump {{level}}

# Remove build artifacts
[group('release')]
clean:
    rm -rf {{root}}/dist

# --zig links against an old glibc so the wheel runs on manylinux2014 hosts
# without needing a container. Drop it for a host-native build. Note `uv run`,
# not the bare binary: maturin finds zig via `python -m ziglang`, which only
# resolves with the project venv on PATH.
#
# Build the release wheel + sdist into dist/
[group('release')]
build: clean
    uv run maturin build --release --out {{root}}/dist --zig --compatibility manylinux2014
    uv run maturin sdist --out {{root}}/dist

# Prefer the tag-triggered CI release; this is the manual fallback, and needs
# UV_PUBLISH_TOKEN set to a PyPI API token.
#
# Build and publish to PyPI
[group('release')]
publish: build
    uv publish

# Guard: refuse to tag a release from anywhere but `main`
[private]
_require-main:
    #!/usr/bin/env bash
    set -euo pipefail
    branch="$(git rev-parse --abbrev-ref HEAD)"
    if [ "$branch" != "main" ]; then
        echo "error: release tags are cut from 'main', but you are on '$branch'." >&2
        exit 1
    fi

# Tag the current version and push it, which triggers the release workflow
[group('release')]
tag: _require-main
    #!/usr/bin/env bash
    set -euo pipefail
    version="$(uv version --short)"
    echo "Tagging v$version"
    git tag "v$version"
    git push origin "v$version"
