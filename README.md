# polars-tldextract

[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Accurate URL domain parsing for [Polars](https://pola.rs), as a native Rust expression plugin.

Splitting a hostname into subdomain / domain / public suffix is not a string operation. `www.bbc.co.uk` and
`blog.cloudflare.com` look identical to a regex, but the registrable domain is `bbc.co.uk` in one and `cloudflare.com`
in the other — "last two labels" is wrong half the time. Getting it right requires the
[Public Suffix List](https://publicsuffix.org/), and in Python that means
[`tldextract`](https://github.com/john-kurkowski/tldextract) — an excellent library, but a Python function. Inside
Polars it can only be driven through `Expr.map_elements`, one interpreter round-trip per row.

This package implements the same algorithm in Rust and exposes it as ordinary Polars expressions. It is built to produce
**identical output to `tldextract`**, not merely similar output — see [Correctness](#correctness).

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

df.with_columns(
    tld.fqdn("url").alias("fqdn"),
    tld.registrable_domain("url").alias("registrable_domain"),
    tld.suffix("url").alias("suffix"),
)
```

```text
┌─────────────────────────────────────────┬─────────────────────┬────────────────────┬────────┐
│ url                                     ┆ fqdn                ┆ registrable_domain ┆ suffix │
╞═════════════════════════════════════════╪═════════════════════╪════════════════════╪════════╡
│ https://www.bbc.co.uk/news/technology   ┆ www.bbc.co.uk       ┆ bbc.co.uk          ┆ co.uk  │
│ github.com                              ┆ github.com          ┆ github.com         ┆ com    │
│ https://blog.cloudflare.com:443/page/2/ ┆ blog.cloudflare.com ┆ cloudflare.com     ┆ com    │
│ 127.0.0.1                               ┆ 127.0.0.1           ┆ null               ┆ null   │
│ null                                    ┆ null                ┆ null               ┆ null   │
└─────────────────────────────────────────┴─────────────────────┴────────────────────┴────────┘
```

## Install

```bash
pip install polars-tldextract
# or
uv add polars-tldextract
```

Prebuilt wheels cover Linux (glibc and musl, x86_64 and aarch64), macOS (Intel and Apple Silicon), and Windows (x64 and
arm64). There is one wheel per platform rather than one per Python version, because the extension is built against the
stable ABI. An sdist is published too, so anything else builds from source given a Rust toolchain — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

Six expressions, all taking a string column of URLs or bare hostnames:

|                          | `https://www.bbc.co.uk/news`     |                                                        |
| ------------------------ | -------------------------------- | ------------------------------------------------------ |
| `tld.extract`            | `{"www", "bbc", "co.uk", false}` | struct: `subdomain`, `domain`, `suffix`, `is_private`  |
| `tld.fqdn`               | `www.bbc.co.uk`                  | the whole hostname                                     |
| `tld.registrable_domain` | `bbc.co.uk`                      | what you register — `domain.suffix`                    |
| `tld.subdomain`          | `www`                            |                                                        |
| `tld.domain`             | `bbc`                            | the registrable *label*, `tldextract`'s `domain` field |
| `tld.suffix`             | `co.uk`                          | the public suffix                                      |

### Nulls, and the one exception

The five single-value expressions return **null** for a part that does not exist. That matters in a DataFrame: an empty
string is a *value*, so two rows that both failed to parse would compare equal and join to each other.

`tld.extract` is the exception, and deliberately so — it reproduces `tldextract.ExtractResult` verbatim, **empty
strings** and all. Reach for it when porting existing `tldextract` code and you want the behavior unchanged; reach for
anything else when the result is going into a join, a group-by, or a comparison.

### `fqdn` vs. `registrable_domain`

The two differ on hosts that have no registrable domain. `registrable_domain` is strict — no recognized suffix means
null, so an IP or a `.local` name drops out. `fqdn` just gives you the hostname:

```python
tld.fqdn("url")  # "127.0.0.1", "localhost", "printer.local"
tld.registrable_domain("url")  # null,        null,        null
```

`fqdn` is also the normalized netloc — scheme, userinfo, port, path, query and fragment stripped, trailing root labels
dropped, and the non-ASCII IDNA separators folded to `.` — so `ftp://user:pw@ftp.gnu.org:2121/pub` becomes
`ftp.gnu.org`. Casing and punycode spelling are preserved, exactly like `tldextract`.

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
# ('bbc.co.uk', 'bbc', 'co.uk')         (registrable_domain, domain, suffix), nulls for absences

tld.extract_scalar_full("https://www.bbc.co.uk/news")
# ('www', 'bbc', 'co.uk', False)        (subdomain, domain, suffix, is_private), tldextract-faithful
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

200,000 URLs, measured with `just bench`:

|                                       |   throughput | vs. `map_elements` |
| ------------------------------------- | -----------: | -----------------: |
| `tldextract` via `Expr.map_elements`  |   94k rows/s |                  — |
| `polars_tldextract`, `parallel=False` | 2.05M rows/s |              21.7× |
| `polars_tldextract`, `parallel=True`  | 21.9M rows/s |             232.8× |

<sub>AMD Ryzen 9 3950X (16 cores / 32 threads), 32 GB RAM, Linux 6.18 (WSL2), Python 3.12.13, Polars 1.43.</sub>

Measure a **release build**. `just bench` builds one; a plain `maturin develop` is unoptimized and roughly 15× slower on
this workload, which measures the profile rather than the code.

Columns of 100k rows or more are split across [rayon](https://docs.rs/rayon) threads; pass `parallel=False` to force
single-threaded. The threshold sits above the streaming engine's morsel size, so when Polars is already calling the
plugin from several of its own worker threads each call stays single-threaded rather than nesting a fan-out inside it.

The parallel figure scales with core count — 233× reflects 32 threads, and a 4-core laptop will land far below it. It is
also by far the noisiest of the three, swinging ~20% run to run with thread scheduling while the single-threaded number
holds within a couple of percent. The single-threaded number is the one to reason about when Polars is already
saturating your cores.

A caveat worth stating plainly: if your column has far fewer distinct URLs than rows, a `dict` built over
`Series.unique()` plus `replace_strict` can still beat any per-row approach, including this one. This package wins on
columns with high cardinality, and on code you would rather not write.

## Correctness

The point of this package is not "fast domain parsing" — it is "fast domain parsing you can swap in without your results
moving". `tests/test_parity.py` asserts `(subdomain, domain, suffix)` equals `tldextract`'s answer, for both settings of
`include_private`, over four corpora:

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

A snapshot of the Public Suffix List is compiled into the binary, so there is no network access, no cache directory, and
no first-call latency spike. `tld.psl_version()` reports which snapshot is in use.

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

|         |                                                                        |
| ------- | ---------------------------------------------------------------------- |
| Python  | 3.10+ — one `abi3` wheel covers all versions                           |
| Polars  | 1.37+ — the plugin FFI ABI is `(0, 1)` and unchanged across that range |
| Linux   | `manylinux2014` and `musllinux_1_2`, x86_64 and aarch64                |
| macOS   | x86_64 (10.12+) and arm64 (11.0+)                                      |
| Windows | x64 and arm64                                                          |

If a future Polars release bumps the plugin ABI, this package fails loudly at load rather than miscomputing.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the development loop, the parity requirement, and
how to refresh the suffix list. [`docs/architecture/overview.md`](docs/architecture/overview.md) explains how this
implementation maps onto `tldextract`'s, which is worth reading before changing the algorithm.

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or [MIT license](LICENSE-MIT) at your option.

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this work, as defined
in the Apache-2.0 license, shall be dual licensed as above, without any additional terms or conditions.

Two third-party works are included or drawn upon and keep their own terms — the Public Suffix List (MPL-2.0) and the
`tldextract` algorithm (BSD-3-Clause). See [NOTICE](NOTICE).
