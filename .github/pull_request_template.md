<!--
Thanks for contributing! The what is visible in the diff — use this space for the why.
See CONTRIBUTING.md for the development loop and the parity rule.
-->

## Why

<!-- What problem does this solve? Link any related issue (e.g. "Closes #12"). -->

## What changed

<!-- A short summary of the approach, and anything a reviewer would otherwise have to reverse-engineer. -->

## Parity

This package's contract is `tldextract`'s behavior, so parsing changes are held to it.

- [ ] `tests/test_parity.py` passes for both settings of `include_private`
- [ ] Parsing behavior is unchanged, **or** a case covering it was added to `EDGE_CASES`
- [ ] No new normalization of *output* (case, IDNA, trailing dots) — only lookup is normalized

<!-- Not a parsing change? Say so and move on. -->

## Checklist

- [ ] Branched from `main`
- [ ] `just check` is green (fmt, clippy, `cargo test`, ruff, pytest)
- [ ] `just dev` was re-run after any Rust change, so tests exercised the new build
- [ ] Tests added or updated — `tests/test_expr.py` for Polars-level behavior, `tests/test_parity.py` for parsing
- [ ] `CHANGELOG.md` has a bullet under `## [Unreleased]` (skip for internal refactors, test-only changes, CI tweaks)
- [ ] Version **not** bumped — that happens at release

## Notes for the reviewer

<!-- Benchmarks, trade-offs you weighed, or anything you are unsure about. -->
