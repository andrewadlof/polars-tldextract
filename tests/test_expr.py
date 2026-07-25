"""Expression-level behavior: nulls, dtypes, composition, parallelism.

`test_parity.py` covers *what* the algorithm computes. This file covers how it
behaves as a Polars expression -- the parts a parity check against a scalar
Python function cannot see.
"""

from __future__ import annotations

import polars_tldextract as tld

import polars as pl
import pytest

URLS = [
    "https://www.bbc.co.uk/news",
    "github.com",
    "127.0.0.1",
    "com",
    None,
    "",
    "   ",
    "not a url",
]

# Shapes `URLS` does not reach: normalization, casing, IDN, wildcards, and the
# private section.
EXTRA_URLS = [
    "ftp://user:pw@ftp.gnu.org:2121/pub",
    "github.com...",
    "www。github．com｡",
    "WWW.BBC.Co.Uk",
    "http://[2001:db8::1]:80/x",
    "пример.рф",
    "xn--e1afmkfd.xn--p1ai",
    "a.foo.bar.ck",
    "pola-rs.github.io",
    "co.uk",
    ".",
    "@",
]


def test_single_field_expressions_use_nulls() -> None:
    """Every single-field expression is nullable Utf8, null for absences.

    This is the convention `extract` deliberately does not follow: an empty
    string is a *value*, so two rows that both failed to parse would compare
    equal and join to each other.
    """
    out = pl.DataFrame({"u": URLS}).select(
        tld.subdomain("u").alias("sub"),
        tld.domain("u").alias("dom"),
        tld.suffix("u").alias("suf"),
        tld.registrable_domain("u").alias("reg"),
        tld.fqdn("u").alias("fqdn"),
    )
    assert out.dtypes == [pl.String] * 5
    assert out["sub"].to_list() == [
        "www",
        None,  # no subdomain, not ""
        None,
        None,
        None,  # null in, null out
        None,
        None,
        None,
    ]
    assert out["dom"].to_list() == [
        "bbc",
        "github",
        "127.0.0.1",  # an IP is reported as the domain
        None,  # a bare suffix has no registrable label
        None,
        None,
        None,
        "not a url",  # no suffix matched, so the last label is the domain
    ]
    assert out["suf"].to_list() == [
        "co.uk",
        "com",
        None,  # an IP has no suffix
        "com",
        None,
        None,
        None,
        None,
    ]
    assert out["reg"].to_list() == [
        "bbc.co.uk",
        "github.com",
        None,  # an IP has no registrable domain
        None,  # neither does a bare suffix
        None,
        None,
        None,
        None,
    ]


def test_fqdn_keeps_hosts_that_have_no_registrable_domain() -> None:
    """`fqdn` is the hostname, so it is laxer than `registrable_domain`.

    An IP or an unrecognized suffix still names a host; only input with no host
    at all is null. This is the one place the two strictnesses differ, and it
    is the reason `fqdn` exists as its own expression.
    """
    out = pl.DataFrame({"u": URLS}).select(tld.fqdn("u").alias("f"))
    assert out["f"].to_list() == [
        "www.bbc.co.uk",  # scheme and path stripped
        "github.com",
        "127.0.0.1",  # null under registrable_domain
        "com",
        None,  # null in, null out
        None,  # no host at all
        None,
        "not a url",
    ]


def test_fqdn_normalizes_like_the_rest_of_the_parse() -> None:
    """`fqdn` is the normalized netloc, rebuilt from the parts."""
    out = pl.DataFrame({
        "u": [
            "ftp://user:pw@ftp.gnu.org:2121/pub",
            "github.com...",
            "www。github．com｡",
            "WWW.BBC.Co.Uk",
            "http://[2001:db8::1]:80/x",
            "пример.рф",
        ]
    }).select(tld.fqdn("u").alias("f"))
    assert out["f"].to_list() == [
        "ftp.gnu.org",  # userinfo, port and path gone
        "github.com",  # trailing root labels dropped
        "www.github.com",  # ideographic dots folded to "."
        "WWW.BBC.Co.Uk",  # casing preserved, like tldextract
        "[2001:db8::1]",  # brackets kept, matching extract's domain
        "пример.рф",  # punycode spelling preserved
    ]


def test_fqdn_agrees_with_extract() -> None:
    """`fqdn` must be exactly the non-empty parts of `extract`, rejoined."""
    out = (
        pl
        .DataFrame({"u": URLS + EXTRA_URLS})
        .select(tld.extract("u").alias("e"), tld.fqdn("u").alias("fqdn"))
        .unnest("e")
    )
    for row in out.iter_rows(named=True):
        if row["domain"] is None:  # null input: null in, null out
            assert row["fqdn"] is None
            continue
        rejoined = ".".join(
            part
            for part in (row["subdomain"], row["domain"], row["suffix"])
            if part
        )
        assert row["fqdn"] == (rejoined or None)


def test_extract_dtype_and_empty_string_semantics() -> None:
    """`extract` is `tldextract`-faithful: empty strings, not nulls."""
    out = (
        pl
        .DataFrame({"u": URLS})
        .select(tld.extract("u").alias("e"))
        .unnest("e")
    )
    assert out.columns == ["subdomain", "domain", "suffix", "is_private"]
    assert out.dtypes == [pl.String, pl.String, pl.String, pl.Boolean]
    # Row 1 ("github.com") has no subdomain: empty string, not null.
    assert out.row(1) == ("", "github", "com", False)
    # Row 4 is null input, so every field is null.
    assert out.row(4) == (None, None, None, None)


def test_registrable_domain_matches_its_halves() -> None:
    """The one-pass shortcut agrees with `domain` and `suffix` joined."""
    out = pl.DataFrame({"u": URLS + EXTRA_URLS}).select(
        tld.registrable_domain("u").alias("fast"),
        pl.concat_str(tld.domain("u"), tld.suffix("u"), separator=".").alias(
            "assembled"
        ),
    )
    # `concat_str` propagates nulls, which is exactly registrable_domain's
    # "null unless both halves exist" rule.
    assert out["fast"].to_list() == out["assembled"].to_list()


def test_all_null_column() -> None:
    """A column of only nulls round-trips without touching the parser."""
    out = pl.DataFrame({"u": [None, None]}, schema={"u": pl.String}).select(
        tld.registrable_domain("u").alias("d")
    )
    assert out["d"].to_list() == [None, None]


def test_empty_column() -> None:
    """A zero-row column produces a zero-row result, not an error."""
    out = pl.DataFrame({"u": []}, schema={"u": pl.String}).select(
        tld.extract("u").alias("d"), tld.fqdn("u").alias("f")
    )
    assert out.height == 0


@pytest.mark.parametrize(
    "name",
    ("extract", "fqdn", "registrable_domain", "subdomain", "domain", "suffix"),
)
def test_namespace_matches_functions(name: str) -> None:
    """`pl.col(...).tld.*` and the module functions build the same thing.

    Parametrized over the whole surface so a method added to one and not the
    other fails here rather than in a user's pipeline.
    """
    df = pl.DataFrame({"u": URLS})
    # `.tld` is registered with Polars at import time, so a static checker
    # has no way to see it.
    ns = getattr(pl.col("u").tld, name)  # ty: ignore[unresolved-attribute]
    via_ns = df.select(ns().alias("d"))
    via_fn = df.select(getattr(tld, name)("u").alias("d"))
    assert via_ns.equals(via_fn)


def test_deprecated_parts_still_works_and_warns() -> None:
    """The 0.1 `parts` struct is reproduced exactly, under a warning."""
    with pytest.warns(DeprecationWarning, match="registrable_domain"):
        expr = tld.parts("u")
    out = pl.DataFrame({"u": URLS}).select(expr.alias("d")).unnest("d")
    assert out.columns == ["full_domain", "sld", "tld"]
    assert out.dtypes == [pl.String, pl.String, pl.String]
    assert out["full_domain"].to_list()[:4] == [
        "bbc.co.uk",
        "github.com",
        None,
        None,
    ]
    assert out["sld"].to_list()[2] == "127.0.0.1"
    assert out["tld"].to_list()[3] == "com"


def test_deprecated_top_domain_delegates_to_domain() -> None:
    """`top_domain` warns and now carries `domain`'s null semantics."""
    with pytest.warns(DeprecationWarning, match="tld.domain"):
        expr = tld.top_domain("u")
    out = pl.DataFrame({"u": URLS}).select(
        expr.alias("old"), tld.domain("u").alias("new")
    )
    assert out["old"].to_list() == out["new"].to_list()


def test_deprecated_namespace_methods_warn() -> None:
    """The shims warn through the namespace too, not just the functions."""
    col = pl.col("u").tld  # ty: ignore[unresolved-attribute]
    with pytest.warns(DeprecationWarning, match="tld.parts"):
        col.parts()
    with pytest.warns(DeprecationWarning, match="tld.top_domain"):
        col.top_domain()


def test_parallel_and_serial_agree() -> None:
    """The rayon fan-out must not reorder or drop rows.

    Sized past `PARALLEL_THRESHOLD` (100k) so the parallel path is actually
    taken; `parallel=False` gives the single-threaded reference.
    """
    urls: list[str | None] = [
        None
        if i % 1000 == 0
        else f"https://www.host{i}.example{i % 7}.co.uk/p"
        for i in range(250_000)
    ]
    df = pl.DataFrame({"u": urls})

    def run(*, parallel: bool) -> pl.DataFrame:
        return df.select(
            tld.extract("u", parallel=parallel).alias("e"),
            tld.fqdn("u", parallel=parallel).alias("f"),
            tld.domain("u", parallel=parallel).alias("d"),
        ).unnest("e")

    par, ser = run(parallel=True), run(parallel=False)
    assert par.equals(ser)
    # Guard against a fan-out that silently truncates.
    assert par.height == len(urls)


def test_works_inside_list_eval() -> None:
    """The plugin composes inside `list.eval`, over exploded inner series.

    The consuming pipeline needs this to normalize comma-separated domain
    lists without leaving Polars.
    """
    df = pl.DataFrame({
        "x": ["mail.bbc.co.uk,GITHUB.COM,github.com", None, ""]
    })
    out = df.select(
        pl
        .col("x")
        .str.to_lowercase()
        .str.split(",")
        .list.eval(tld.registrable_domain(pl.element()))
        .list.drop_nulls()
        .list.unique(maintain_order=True)
        .list.join(",")
        .alias("out")
    )
    assert out["out"].to_list() == ["bbc.co.uk,github.com", None, ""]


def test_works_in_lazy_and_streaming() -> None:
    """The expression survives the lazy optimizer and the streaming engine."""
    lf = pl.LazyFrame({"u": URLS}).select(
        tld.registrable_domain("u").alias("d")
    )
    assert (
        lf.collect()["d"].to_list()
        == lf.collect(engine="streaming")["d"].to_list()
    )


def test_works_in_group_by_and_filter() -> None:
    """Being elementwise, it works in aggregation and predicate contexts."""
    df = pl.DataFrame({
        "u": ["news.bbc.co.uk", "www.bbc.co.uk", "en.wikipedia.org"],
        "n": [1, 2, 3],
    })
    grouped = (
        df
        .group_by(tld.registrable_domain("u").alias("domain"))
        .agg(pl.col("n").sum())
        .sort("domain")
    )
    assert grouped["domain"].to_list() == ["bbc.co.uk", "wikipedia.org"]
    assert grouped["n"].to_list() == [3, 3]

    filtered = df.filter(tld.suffix("u") == "org")
    assert filtered["n"].to_list() == [3]


def test_non_string_input_is_rejected() -> None:
    """A non-string column fails loudly rather than producing nonsense."""
    df = pl.DataFrame({"u": [1, 2, 3]})
    with pytest.raises(pl.exceptions.PolarsError):
        df.select(tld.registrable_domain("u"))


def test_psl_version_is_reported() -> None:
    """The list in use is identifiable, for provenance in logs."""
    version = tld.psl_version()
    assert version
    assert version != "unknown"
    assert tld.PSL_PATH_ENV == "POLARS_TLDEXTRACT_PSL"
