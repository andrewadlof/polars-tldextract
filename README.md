# polars-tldextract

[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Accurate URL domain parsing for [Polars](https://pola.rs), as a native Rust expression plugin.

Splitting a hostname into subdomain / domain / public suffix is not a string operation. `www.bbc.co.uk` and
`blog.cloudflare.com` look identical to a regex, but the registrable domain is `bbc.co.uk` in one and
`cloudflare.com` in the other — "last two labels" is wrong half the time. Getting it right requires
the [Public Suffix List](https://publicsuffix.org/), and in Python that means
[`tldextract`](https://github.com/john-kurkowski/tldextract) — an excellent library, but a Python function. Inside
Polars it can only be driven through `Expr.map_elements`, one interpreter round-trip per row.

This package implements the same algorithm in Rust and exposes it as ordinary Polars expressions. It is built to
produce **identical output to `tldextract`**, not merely similar output — see [Correctness](#correctness).

```python
import polars as pl
import polars_tldextract as tld

df = pl.DataFrame({
    "url": [
        "https://www.bbc.co.uk/news/technology",
        "github.com",
        "https://blog.cloudflare.com:443/page/2/",
        "127.0.0.1",
        None,
    ]
})

df.with_columns(tld.parts("url").alias("d")).unnest("d")
```

```text
┌─────────────────────────────────────────┬────────────────┬────────────┬───────┐
│ url                                     ┆ full_domain    ┆ sld        ┆ tld   │
╞═════════════════════════════════════════╪════════════════╪════════════╪═══════╡
│ https://www.bbc.co.uk/news/technology   ┆ bbc.co.uk      ┆ bbc        ┆ co.uk │
│ github.com                              ┆ github.com     ┆ github     ┆ com   │
│ https://blog.cloudflare.com:443/page/2/ ┆ cloudflare.com ┆ cloudflare ┆ com   │
│ 127.0.0.1                               ┆ null           ┆ 127.0.0.1  ┆ null  │
│ null                                    ┆ null           ┆ null       ┆ null  │
└─────────────────────────────────────────┴────────────────┴────────────┴───────┘
```

## Install

```bash
pip install polars-tldextract
# or
uv add polars-tldextract
```

Prebuilt wheels cover Linux (glibc and musl, x86_64 and aarch64), macOS (Intel and Apple Silicon), and Windows
(x64 and arm64). There is one wheel per platform rather than one per Python version, because the extension is built
against the stable ABI. An sdist is published too, so anything else builds from source given a Rust toolchain — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

### `parts` — nulls for what isn't there

```python
tld.parts("url")  # struct: full_domain, sld, tld
tld.registrable_domain("url")  # just "sld.tld", in one pass
```

Absent parts are **null**, and `full_domain` is populated only when both halves exist. This is usually what you want in
a DataFrame: an empty string is a *value*, so two rows that both failed to parse would compare equal and join to each
other.

### `extract` — exactly what `tldextract` returns

```python
tld.extract("url")  # struct: subdomain, domain, suffix, is_private
tld.subdomain("url")
tld.top_domain("url")  # tldextract's `domain` field: the registrable label
tld.suffix("url")
```

Here absent parts are **empty strings**, faithfully reproducing `tldextract.ExtractResult`. Reach for this when you are
porting existing `tldextract` code and want the behavior unchanged.

### Expression namespace

Importing the package registers a `.tld` namespace:

```python
df.with_columns(pl.col("url").tld.registrable_domain())
df.filter(pl.col("url").tld.suffix() == "org")
```

### Scalars

For code that isn't holding a DataFrame — the same Rust core, no Polars round-trip:

```python
tld.extract_scalar("https://www.bbc.co.uk/news")
# ('bbc.co.uk', 'bbc', 'co.uk')         nulls for absent parts

tld.extract_scalar_full("https://www.bbc.co.uk/news")
# ('www', 'bbc', 'co.uk', False)        tldextract-faithful
```

### Private suffixes

The Public Suffix List has an ICANN section and a private section. Like `tldextract`, the private section is **off** by
default:

```python
tld.extract_scalar("pola-rs.github.io")
# ('github.io', 'github', 'io')

tld.extract_scalar("pola-rs.github.io", include_private=True)
# ('pola-rs.github.io', 'pola-rs', 'github.io')
```

Every expression takes the same `include_private` keyword.

## Performance

200,000 URLs on an 8-core machine (`just bench` reproduces this):

| | throughput |
| --- | ---: |
| `tldextract` via `Expr.map_elements` | 104k rows/s |
| `polars_tldextract`, `parallel=False` | 2.9M rows/s |
| `polars_tldextract`, `parallel=True` | 13.6M rows/s |

Columns of 100k rows or more are split across [rayon](https://docs.rs/rayon) threads; pass `parallel=False` to force
single-threaded. The threshold sits above the streaming engine's morsel size, so when Polars is already calling the
plugin from several of its own worker threads each call stays single-threaded rather than nesting a fan-out inside it.

A caveat worth stating plainly: if your column has far fewer distinct URLs than rows, a `dict` built over
`Series.unique()` plus `replace_strict` can still beat any per-row approach, including this one. This package wins on
columns with high cardinality, and on code you would rather not write.

## Correctness

The point of this package is not "fast domain parsing" — it is "fast domain parsing you can swap in without your
results moving". `tests/test_parity.py` asserts `(subdomain, domain, suffix)` equals `tldextract`'s answer, for both
settings of `include_private`, over four corpora:

1. Hand-written edge cases: schemes, userinfo, ports, IPv4, bracketed IPv6, trailing root labels, the three non-ASCII
   IDNA dot characters, IDN in Unicode and punycode spellings, mixed case, and degenerate input.
2. **Every rule in the Public Suffix List** — each of ~9,750 rules turned into three concrete hosts, ~29,000 cases.
   Wildcard rules (`*.ck`) get a concrete label and exception rules (`!www.ck`) have their marker stripped so the
   exception path is genuinely taken. This is the check that catches divergence no hand-written suite would find.
3. 200,000 randomly assembled URLs.
4. A fixture set drawn from a production pipeline.

Both sides are pointed at the same list file, so a disagreement can only be an algorithm difference — never two
different snapshots.

If you find an input where this package and `tldextract` disagree, that is a bug here. Please
[open an issue](https://github.com/andrewadlof/polars-tldextract/issues) with the input.

## The suffix list

A snapshot of the Public Suffix List is compiled into the binary, so there is no network access, no cache directory,
and no first-call latency spike. `tld.psl_version()` reports which snapshot is in use.

To supply your own list at startup, point `POLARS_TLDEXTRACT_PSL` at a `.dat` file:

```bash
export POLARS_TLDEXTRACT_PSL=/path/to/public_suffix_list.dat
```

It is read once, on first use, so set it before the first extraction.

### Refreshing without a restart

The list changes several times a week. A long-lived process — a notebook, a cluster, a service — would otherwise be
stuck with whatever list it read when it parsed its first URL, so two functions replace it in place:

```python
# Download the current list and load it into this process.
tld.refresh_psl()

# ...and keep a copy, so the next run need not go back to the network.
tld.refresh_psl(save_to="psl.dat")

# Or load one you already have: a path, a Path, or the list text itself.
tld.load_psl("psl.dat")
```

Both return the new `VERSION:` stamp, and take effect for every extraction that *starts* after they return. A query
already in flight keeps the list it began with, so no single column is ever parsed against two different lists.

`refresh_psl` is the only function here that touches the network, and only when you call it — importing the package
still does nothing. Point it at an internal mirror with `tld.refresh_psl(url=...)` if outbound access is restricted.

An unreadable file, an unparseable list, or one missing the `===BEGIN ICANN DOMAINS===` / `===BEGIN PRIVATE DOMAINS===`
markers raises `ValueError` and **leaves the working list untouched**. The marker check matters more than it looks: a
list without them parses as one undifferentiated section, and every private suffix would quietly start counting as an
ICANN one — wrong output, no signal.

## Compatibility

| | |
| --- | --- |
| Python | 3.10+ — one `abi3` wheel covers all versions |
| Polars | 1.37+ — the plugin FFI ABI is `(0, 1)` and unchanged across that range |
| Linux | `manylinux2014` and `musllinux_1_2`, x86_64 and aarch64 |
| macOS | x86_64 (10.12+) and arm64 (11.0+) |
| Windows | x64 and arm64 |

If a future Polars release bumps the plugin ABI, this package fails loudly at load rather than miscomputing.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the development loop, the parity requirement,
and how to refresh the suffix list. [`docs/architecture/overview.md`](docs/architecture/overview.md) explains how this
implementation maps onto `tldextract`'s, which is worth reading before changing the algorithm.

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or [MIT license](LICENSE-MIT) at your option.

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this work, as defined
in the Apache-2.0 license, shall be dual licensed as above, without any additional terms or conditions.

Two third-party works are included or drawn upon and keep their own terms — the Public Suffix List (MPL-2.0) and the
`tldextract` algorithm (BSD-3-Clause). See [NOTICE](NOTICE).
