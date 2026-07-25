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
>>> df.with_columns(tld.extract("url").alias("d"))  # doctest: +SKIP
>>> df.with_columns(pl.col("url").tld.registrable_domain())  # doctest: +SKIP

The `.tld` expression namespace is registered on import.
"""

from __future__ import annotations

from polars_tldextract._deprecated import parts, top_domain
from polars_tldextract._expr import (
    domain,
    extract,
    fqdn,
    registrable_domain,
    subdomain,
    suffix,
)
from polars_tldextract._internal import (
    PSL_PATH_ENV,
    extract_scalar,
    extract_scalar_full,
    psl_version,
)
from polars_tldextract._namespace import TldNamespace
from polars_tldextract._psl import PSL_URL, load_psl, refresh_psl

__all__ = [
    "PSL_PATH_ENV",
    "PSL_URL",
    "TldNamespace",
    "domain",
    "extract",
    "extract_scalar",
    "extract_scalar_full",
    "fqdn",
    "load_psl",
    "parts",
    "psl_version",
    "refresh_psl",
    "registrable_domain",
    "subdomain",
    "suffix",
    "top_domain",
]
