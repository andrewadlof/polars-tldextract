# Contributing to polars-tldextract

Thanks for taking the time. This is a small project with one unusual property worth understanding before you start:
**its contract is another library's behavior.** Almost every design decision here follows from "must match
`tldextract`", so a change that looks like an improvement can be a bug. The [parity requirement](#the-parity-rule)
section explains where that bites.

By contributing you agree that your work is dual licensed under MIT and Apache-2.0, matching the project — see
[LICENSE-MIT](https://github.com/andrewadlof/polars-tldextract/blob/main/LICENSE-MIT),
[LICENSE-APACHE](https://github.com/andrewadlof/polars-tldextract/blob/main/LICENSE-APACHE), and
[NOTICE](https://github.com/andrewadlof/polars-tldextract/blob/main/NOTICE).

## What's most useful

- **Parity bugs.** An input where this package and `tldextract` disagree is the highest-value report there is. Include
  the input string; a failing case added to `EDGE_CASES` in `tests/test_parity.py` is even better.
- **Polars version support.** The plugin FFI ABI has been stable, but each new Polars release should be checked.
- **Performance**, as long as parity holds and the benchmark shows it.

If you are planning something large, open an issue first so nobody duplicates work.

## Getting set up

You need [Rust](https://rustup.rs/) (the pinned toolchain in `rust-toolchain.toml` installs automatically),
[uv](https://docs.astral.sh/uv/), and [just](https://github.com/casey/just) — the last one comes from `uv sync`, so
`uv run just ...` works if you don't have it globally.

```bash
git clone https://github.com/andrewadlof/polars-tldextract
cd polars-tldextract
uv sync          # creates .venv, installs dev tools, builds the extension
just dev         # rebuild the extension in-place after Rust changes
```

`uv sync` builds the Rust extension into the venv. **Any time you touch Rust, re-run `just dev`** or your tests will
silently exercise the previous build.

If a bare `cargo` command fails with `cannot set a minimum Python version 3.10 higher than the interpreter version`, it
found an older `python3` on `PATH`. The `justfile` exports `PYO3_PYTHON=.venv/bin/python`, so `just test` and
`just check` are unaffected; for bare `cargo`, export it yourself:

```bash
export PYO3_PYTHON="$PWD/.venv/bin/python"
```

This deliberately lives in the `justfile` rather than `.cargo/config.toml` — a repo-level cargo config would also apply
inside the release workflow's cross-compilation containers, where `.venv` does not exist and every build would fail with
"failed to run the Python interpreter".

## The development loop

```bash
just test       # cargo test --lib + pytest
just check      # the full gate: fmt, clippy, cargo test, ruff, pytest
just bench      # throughput vs. tldextract through map_elements
just precommit  # every pre-commit hook (ruff, ty, pydoclint, cargo, mdformat, taplo)
just docs-serve # the documentation site, with live reload
just docs       # build the site, failing on broken links
```

`just check` is what CI runs. Run it before opening a PR.

Rust code is formatted with `cargo fmt` (see `rustfmt.toml`) and linted with `cargo clippy -- -D warnings`. Python is
formatted and linted with [ruff](https://docs.astral.sh/ruff/), type-checked with [ty](https://github.com/astral-sh/ty),
and docstrings are checked with [pydoclint](https://github.com/jsh9/pydoclint) in NumPy style. Configuration for all of
it lives in `pyproject.toml`.

## Layout

| Path                              | What lives there                                                                                |
| --------------------------------- | ----------------------------------------------------------------------------------------------- |
| `src/netloc.rs`                   | Port of `tldextract/remote.py` — scheme/path/userinfo/port stripping, IP recognition            |
| `src/psl.rs`                      | Loading the suffix list into the ICANN-only and ICANN+private tries, and swapping it at runtime |
| `src/extract.rs`                  | The algorithm itself                                                                            |
| `src/lib.rs`                      | Polars kernels, the rayon fan-out, the scalar Python functions                                  |
| `src/data/public_suffix_list.dat` | The vendored list (MPL-2.0 — see NOTICE)                                                        |
| `python/polars_tldextract/`       | Expression wrappers, the `.tld` namespace, and the list-refresh helpers                         |
| `tests/test_parity.py`            | The differential suite against `tldextract`                                                     |
| `tests/test_expr.py`              | Polars-level behavior: nulls, dtypes, composition, parallelism                                  |
| `tests/test_psl.py`               | Replacing the list in a running process, and rejecting bad ones                                 |

[`docs/architecture/overview.md`](https://andrewadlof.github.io/polars-tldextract/architecture/overview/) explains *why*
the `publicsuffix` crate's API lines up with `tldextract`'s trie walk. Read it before touching `src/extract.rs` — the
three facts it lists are each load-bearing, and none of them are obvious from the code alone.

## The parity rule

**Any change to parsing behavior must keep `tests/test_parity.py` green**, and that suite is not a formality: it derives
~29,000 hostnames mechanically from every rule in the Public Suffix List and compares against `tldextract` itself, with
both sides reading the same list file.

This means some things you might expect to be welcome are not:

- "Fixing" a case where `tldextract` looks wrong. If `tldextract` returns something surprising, this package returns the
  same surprising thing on purpose. Report it upstream; if upstream changes, we follow.
- Adding IDNA normalization, lowercasing of output, or stripping of trailing dots from results. Output preserves the
  input's spelling and case exactly as `tldextract` does; only the *lookup* is normalized.
- Turning empty-string results into nulls in `extract()`. That distinction is deliberate — `extract()` is the one
  expression that reproduces `tldextract.ExtractResult` verbatim; every other expression already uses nulls.

New behavior that `tldextract` has no opinion on (new convenience expressions, new output shapes) is fine and does not
need a parity case — but it does need tests in `tests/test_expr.py`.

## Refreshing the Public Suffix List

There are two refreshes, and they are not the same thing. This section is about the **vendored snapshot** — the list
compiled into the binary, which changes what a released wheel returns:

```bash
just refresh-psl   # downloads the current list into src/data/
just check         # the parity corpus re-derives itself from the new list
```

Users refreshing the list in *their* running process is a separate mechanism — `tld.refresh_psl()` and `tld.load_psl()`,
implemented in `src/psl.rs` and `python/polars_tldextract/_psl.py`. That path does not touch the vendored file and needs
no rebuild. If you change how lists are parsed or validated, both go through `psl::Lists::from_text`, so the marker and
empty-list checks cover them together.

The script verifies the ICANN and private section markers survived the download, since the parser keys on those to build
the two tries. A list refresh changes what the package returns, so it warrants a CHANGELOG entry and a version bump.

## Documentation

The site is MkDocs + Material, published to <https://andrewadlof.github.io/polars-tldextract/> from `main` by
`.github/workflows/docs.yml`. Pull requests targeting `main` build it without deploying.

```bash
just docs-serve   # live reload at http://127.0.0.1:8000
just docs         # build into site/, --strict
```

Two rules keep it from drifting out of date, and both are worth respecting:

- **Prose is not duplicated.** The narrative pages pull their content out of `README.md` between
  `<!--usage-start-->`-style markers, so the README a PyPI visitor reads and the page a site visitor reads are the same
  bytes. Edit the README; the site follows. If you move a marker, `just docs` fails.
- **The API reference is generated** from the NumPy docstrings pydoclint already enforces, so it cannot describe a
  signature the code does not have. A new public expression needs no reference page — just an entry in
  `docs/reference.md`.

`mkdocstrings` imports the package to read its docstrings, so the compiled extension must be present. `uv sync` builds
it; after a Rust change run `just dev` first or the reference documents the previous build.

`docs/*.md` is **excluded from mdformat** (see `.pre-commit-config.yaml`). mdformat does not understand mkdocstrings'
`options:` blocks or autorefs `[text][target]` links and silently breaks both. `docs/architecture/` is plain Markdown
and stays covered.

Cross-reference API objects with autorefs rather than a hand-written anchor, so the link survives a page being renamed:

```markdown
[`fqdn()`][polars_tldextract.fqdn]
```

Diagrams use Material's built-in mermaid support — a plain \`\`\`mermaid fence. The `mkdocs-mermaid2` plugin is
deliberately not installed; it conflicts with Material's own handling.

## Branching model

Two long-lived branches:

| Branch        | What it is                                                                                                     |
| ------------- | -------------------------------------------------------------------------------------------------------------- |
| `development` | Where work lands. The default branch, and the base for every PR.                                               |
| `main`        | Released state. Only ever receives a promotion PR from `development`, and only tags cut from it are published. |

Everything else is short-lived and branches **from `development`**:

```bash
git switch development && git pull
git switch -c feature/suffix-cache      # or fix/…, docs/…, chore/…
```

Open the PR against `development`. Both branches are covered by a repository ruleset: a pull request is required, so
neither can be pushed to directly, and commits must be **signed** — set up
[commit signing](https://docs.github.com/authentication/managing-commit-signature-verification) before your first PR or
the merge will be blocked no matter how green the tests are.

A release promotes `development` to `main` — see [Releasing](#releasing).

## Pull requests

Opening a PR pre-fills
[the pull request template](https://github.com/andrewadlof/polars-tldextract/blob/main/.github/pull_request_template.md)
— it is the checklist below in long form, including the parity questions. Delete any section that doesn't apply.

- Branch from `development` and target `development`. PRs against `main` are for releases only.
- Add a bullet to the `## [Unreleased]` section of `CHANGELOG.md` for anything user-visible. Skip it for internal
  refactors, test-only changes, and CI tweaks.
- Don't bump the version in your PR — that happens once, at release.
- **Run `just check` and keep it green.** CI does not run on PRs into `development` — it runs at the promotion boundary,
  on PRs into `main` — so your local run is the only thing standing between a broken commit and the release branch. If
  you want a CI tick anyway, dispatch the `CI` workflow against your branch from the Actions tab.
- Explain *why* in the PR description. The what is visible in the diff.

## Releasing

For maintainers. A release is a **promotion of `development` to `main`**, then a tag on `main`:

1. On `development`, `just bump patch` (or `minor` / `major`) and move the `## [Unreleased]` bullets in `CHANGELOG.md`
   under a new dated version heading. That is the only PR that touches the version.
2. Rehearse the wheel matrix: run `release.yml` via `workflow_dispatch`. It builds and smoke-tests every platform and
   **skips publishing**, so the matrix can fail without burning a version number.
3. Open a PR from `development` to `main`, titled for the version. Merging it is the promotion.
4. Tag `main` — `just tag` refuses to run from any other branch — which triggers the publishing workflow.

Tagging is the irreversible step: PyPI releases are immutable, and a broken wheel can only be yanked, never replaced.
Everything before step 4 is reversible, which is why the rehearsal is worth the wait.

The local equivalent, for a wheel you do not intend to publish:

```bash
just bump patch       # or minor / major
just build            # release wheel + sdist into dist/
just publish          # upload to PyPI
```

That builds a wheel for the host platform only. **The real release is cut by CI**: push a `v*` tag and
`.github/workflows/release.yml` builds the full matrix — Linux glibc and musl on x86_64 and aarch64, macOS on Intel and
Apple Silicon, Windows on x64 and arm64, plus an sdist — then uploads it to PyPI.

Run that workflow manually (`workflow_dispatch`) first. It builds and smoke-tests everything but skips publishing, so
the matrix can be rehearsed without burning a version number — PyPI releases are immutable, and a broken wheel can only
be yanked, never replaced.

### Publishing credentials

CI publishes with **Trusted Publishing** (OIDC), so there is no stored token to leak, rotate, or forget to revoke. PyPI
holds a publisher registered against four things, and the upload fails unless all four match:

|                   |                     |
| ----------------- | ------------------- |
| Owner             | `andrewadlof`       |
| Repository        | `polars-tldextract` |
| Workflow filename | `release.yml`       |
| Environment       | `pypi`              |

Register or edit it at <https://pypi.org/manage/project/polars-tldextract/settings/publishing/>. Renaming the repo,
renaming `release.yml`, or renaming the environment breaks the match until the publisher is updated to agree — the
failure surfaces at upload time as `Trusted publishing exchange failure`, after the whole wheel matrix has built.

The `publish` job needs `permissions: id-token: write` to mint the OIDC token, and must **not** pass a `password:` to
`pypa/gh-action-pypi-publish`. An explicit password silently disables both Trusted Publishing and the PEP 740
attestations the action otherwise produces by default — which is what happened through 0.2.0.

Any reviewers or wait timer configured on the `pypi` environment still gate the upload; the environment is part of what
PyPI authenticates, not just a place to keep things.

For a manual `just publish`, `uv publish` reads `UV_PUBLISH_TOKEN` from the environment:

```bash
read -rs UV_PUBLISH_TOKEN && export UV_PUBLISH_TOKEN   # not `export X=...`, again for history
just publish
```

`just build` uses `--zig` to link against an old glibc so the local wheel runs on `manylinux2014` hosts without a
container; CI builds inside the manylinux containers instead and does not need it. Run it through `uv run` (the
`justfile` already does) — maturin locates zig via `python -m ziglang`, which only resolves with the project venv on
`PATH`.

## Code of conduct

Be decent to each other. Assume good faith, keep criticism about the code, and take heated disagreements to a cooling
period rather than another comment. Maintainers may edit, lock, or remove contributions that don't meet that bar.

## Questions

Open a [discussion or issue](https://github.com/andrewadlof/polars-tldextract/issues). Bug reports that include the
exact input string are answered fastest.
