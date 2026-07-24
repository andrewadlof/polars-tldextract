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


def test_parts_dtype_and_null_semantics() -> None:
    """`parts` yields three nullable Utf8 fields, nulls for absent parts."""
    out = (
        pl.DataFrame({"u": URLS}).select(tld.parts("u").alias("d")).unnest("d")
    )
    assert out.columns == ["full_domain", "sld", "tld"]
    assert out.dtypes == [pl.String, pl.String, pl.String]
    assert out["full_domain"].to_list() == [
        "bbc.co.uk",
        "github.com",
        None,  # an IP has no registrable domain
        None,  # a bare suffix has no registrable label
        None,  # null in, null out
        None,
        None,
        None,  # "not a url" has no recognized suffix
    ]
    assert out["sld"].to_list()[2] == "127.0.0.1"
    assert out["tld"].to_list()[3] == "com"


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


def test_registrable_domain_matches_parts_field() -> None:
    """The one-pass shortcut agrees with the struct field it replaces."""
    out = pl.DataFrame({"u": URLS}).select(
        tld.registrable_domain("u").alias("fast"),
        tld.parts("u").struct.field("full_domain").alias("via_struct"),
    )
    assert out["fast"].to_list() == out["via_struct"].to_list()


def test_all_null_column() -> None:
    """A column of only nulls round-trips without touching the parser."""
    out = pl.DataFrame({"u": [None, None]}, schema={"u": pl.String}).select(
        tld.registrable_domain("u").alias("d")
    )
    assert out["d"].to_list() == [None, None]


def test_empty_column() -> None:
    """A zero-row column produces a zero-row result, not an error."""
    out = pl.DataFrame({"u": []}, schema={"u": pl.String}).select(
        tld.parts("u").alias("d")
    )
    assert out.height == 0


def test_namespace_matches_functions() -> None:
    """`pl.col(...).tld.*` and the module functions build the same thing."""
    df = pl.DataFrame({"u": URLS})
    # `.tld` is registered with Polars at import time, so a static checker
    # has no way to see it.
    via_ns = df.select(
        pl.col("u").tld.registrable_domain().alias("d")  # ty: ignore[unresolved-attribute]
    )
    via_fn = df.select(tld.registrable_domain("u").alias("d"))
    assert via_ns.equals(via_fn)


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
    par = df.select(tld.parts("u", parallel=True).alias("d")).unnest("d")
    ser = df.select(tld.parts("u", parallel=False).alias("d")).unnest("d")
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
