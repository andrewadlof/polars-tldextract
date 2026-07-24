"""Vectorized, `tldextract`-compatible URL domain parsing for Polars.

A native Polars expression plugin, written in Rust against the Mozilla Public
Suffix List, that reproduces `tldextract`'s algorithm exactly -- so domain
parsing runs at native speed instead of row-by-row through
`Expr.map_elements`.

Examples
--------
>>> import polars as pl
>>> import polars_tldextract as tld
>>> df = pl.DataFrame({"url": ["https://www.bbc.co.uk/news", None]})
>>> df.with_columns(tld.parts("url").alias("d")).unnest("d")  # doctest: +SKIP
>>> df.with_columns(pl.col("url").tld.registrable_domain())  # doctest: +SKIP

The `.tld` expression namespace is registered on import.
"""

from __future__ import annotations

from polars_tldextract._expr import (
    extract,
    parts,
    registrable_domain,
    subdomain,
    suffix,
    top_domain,
)
from polars_tldextract._internal import (
    PSL_PATH_ENV,
    extract_scalar,
    extract_scalar_full,
    psl_version,
)
from polars_tldextract._namespace import TldNamespace

__all__ = [
    "PSL_PATH_ENV",
    "TldNamespace",
    "extract",
    "extract_scalar",
    "extract_scalar_full",
    "parts",
    "psl_version",
    "registrable_domain",
    "subdomain",
    "suffix",
    "top_domain",
]
