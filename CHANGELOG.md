# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Updating this file.** Add a bullet under `## [Unreleased]` as part of your PR. Do **not** bump the version or add
> a dated `## [X.Y.Z]` section — the version is bumped once, at release time, from everything accumulated under
> `[Unreleased]`. See [CONTRIBUTING.md](CONTRIBUTING.md).
>
> **What warrants an entry.** Record *notable*, user-facing changes — new features, behavior or API changes, bug fixes,
> deprecations, and removals — under the appropriate `Added` / `Changed` / `Fixed` / `Deprecated` / `Removed` heading.
> Changes with no effect on users generally do **not** need an entry: internal refactors, test-only changes, CI config,
> and documentation edits. A refresh of the vendored Public Suffix List always warrants one, since it changes what the
> package returns.

## [Unreleased]

## [0.1.0] - 2026-07-24

### Added

- Initial release. A native Polars expression plugin (Rust) reimplementing `tldextract`'s URL parsing against the
  Mozilla Public Suffix List, so domain extraction runs vectorized instead of row-by-row through
  `Expr.map_elements`.
- Expressions: `extract` (`tldextract`-faithful struct), `parts` (`full_domain` / `sld` / `tld` with null semantics),
  `registrable_domain`, `subdomain`, `top_domain`, `suffix`, plus the `.tld` expression namespace.
- Scalar helpers `extract_scalar` / `extract_scalar_full` for callers that are not holding a DataFrame.
- The Public Suffix List is compiled into the binary (no network at runtime) and can be overridden without a rebuild
  via `POLARS_TLDEXTRACT_PSL`. `psl_version()` reports which list is in use.
- `refresh_psl()` downloads the current Public Suffix List and loads it into the running process, and `load_psl()`
  does the same for a list you already have (a path or the text itself). Both return the new `VERSION:` stamp and
  take effect for extractions that start after they return, so a long-lived process does not have to restart to pick
  up a newer list. A list that fails to parse, or that is missing the ICANN/private section markers, is rejected and
  the working list stays in use. `refresh_psl` is the only function in the package that touches the network, and only
  when called.
- Dual licensed MIT OR Apache-2.0. The vendored Public Suffix List keeps its own MPL-2.0 terms and the `tldextract`
  algorithm its BSD-3-Clause attribution — see [NOTICE](NOTICE).
- Wheels for Linux (`manylinux2014` and `musllinux_1_2`, x86_64 and aarch64), macOS (x86_64 and arm64), and Windows
  (x64 and arm64), plus an sdist. One wheel per platform, since the extension is built against the stable ABI
  (`abi3-py310`), so a single wheel serves Python 3.10 through 3.13+.

[0.1.0]: https://github.com/andrewadlof/polars-tldextract/releases/tag/v0.1.0
[unreleased]: https://github.com/andrewadlof/polars-tldextract/compare/v0.1.0...HEAD
