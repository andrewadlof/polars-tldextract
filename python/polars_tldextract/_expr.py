"""Polars expression wrappers around the compiled plugin.

Every function here is a thin `register_plugin_function` call. The real work
happens in the Rust cdylib that sits next to this file; `plugin_path` points at
the package directory and Polars locates the shared object inside it.

All expressions are elementwise, so Polars is free to reorder, slice, and
parallelize around them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from polars.plugins import register_plugin_function

if TYPE_CHECKING:
    import polars as pl
    from polars._typing import IntoExprColumn

# The compiled plugin lives alongside this module.
_PLUGIN_PATH = Path(__file__).parent

__all__ = [
    "domain",
    "extract",
    "fqdn",
    "registrable_domain",
    "subdomain",
    "suffix",
]


def _plugin(
    function_name: str,
    expr: IntoExprColumn,
    *,
    include_private: bool,
    parallel: bool,
) -> pl.Expr:
    """Register one of the plugin's expressions.

    Parameters
    ----------
    function_name : str
        Symbol exported by the Rust cdylib.
    expr : IntoExprColumn
        String column of URLs (or bare hostnames) to parse.
    include_private : bool
        Whether the PSL's private section counts as suffixes.
    parallel : bool
        Whether the plugin may fan the column out across rayon threads.

    Returns
    -------
    pl.Expr
        The registered expression.
    """
    return register_plugin_function(
        plugin_path=_PLUGIN_PATH,
        function_name=function_name,
        args=[expr],
        kwargs={"include_private": include_private, "parallel": parallel},
        is_elementwise=True,
    )


def extract(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Parse URLs into a struct, faithfully reproducing `tldextract`.

    The output mirrors `tldextract.ExtractResult`: parts that do not exist are
    **empty strings**, not nulls. This is the one expression here that keeps
    that convention -- every single-field accessor uses nulls instead, which is
    what a DataFrame wants. Reach for this when porting `tldextract` code and
    wanting the behavior unchanged.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of URLs (or bare hostnames).
    include_private : bool, default False
        Whether to treat the PSL's private section as suffixes, matching
        `tldextract`'s `include_psl_private_domains`. With the default,
        `pola-rs.github.io` has suffix `io`; with `True` it has suffix
        `github.io`.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.
        Columns below 100k rows run single-threaded regardless.

    Returns
    -------
    pl.Expr
        Struct of `subdomain` / `domain` / `suffix` / `is_private`. Null input
        yields a null struct.
    """
    return _plugin(
        "tld_extract", expr, include_private=include_private, parallel=parallel
    )


def fqdn(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the whole hostname, e.g. `"www.bbc.co.uk"`.

    This is the normalized netloc: scheme, userinfo, port, path, query, and
    fragment stripped, trailing root labels dropped, and the three non-ASCII
    IDNA separators folded to `.`. Casing and punycode spelling are preserved.

    Unlike `registrable_domain`, a host with no recognized suffix still has a
    name, so `"localhost"` and `"127.0.0.1"` come back as themselves rather
    than null. Only input with no host at all yields null. A bracketed IPv6
    literal keeps its brackets, matching what `extract` reports as its domain.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of URLs (or bare hostnames).
    include_private : bool, default False
        Whether to treat the PSL's private section as suffixes. Does not change
        the result -- the same labels are rejoined either way -- but is taken
        so every expression accepts the same keywords.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of hostnames, null where the input held no host.
    """
    return _plugin(
        "tld_fqdn", expr, include_private=include_private, parallel=parallel
    )


def registrable_domain(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract `"domain.suffix"`, or null when either half is missing.

    Computed in one pass, without materializing the other parts. Strict on
    purpose: an IP or an unrecognized suffix has no registrable domain, so it
    yields null rather than a half-formed string. Use `fqdn` when you want the
    hostname regardless.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of URLs (or bare hostnames).
    include_private : bool, default False
        Whether to treat the PSL's private section as suffixes.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of registrable domains, null where none could be parsed.
    """
    return _plugin(
        "tld_registrable_domain",
        expr,
        include_private=include_private,
        parallel=parallel,
    )


def subdomain(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the subdomain, e.g. `"www"` from `"www.bbc.co.uk"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of URLs (or bare hostnames).
    include_private : bool, default False
        Whether to treat the PSL's private section as suffixes.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column, **null** where there is no subdomain. `extract` reports
        the same absence as an empty string.
    """
    return _plugin(
        "tld_subdomain",
        expr,
        include_private=include_private,
        parallel=parallel,
    )


def domain(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the registrable label, e.g. `"bbc"` from `"bbc.co.uk"`.

    This is `tldextract`'s `domain` field -- the label you register, not the
    whole hostname. Use `fqdn` for the hostname and `registrable_domain` for
    `"bbc.co.uk"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of URLs (or bare hostnames).
    include_private : bool, default False
        Whether to treat the PSL's private section as suffixes.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column, **null** where there is no registrable label. `extract`
        reports the same absence as an empty string.
    """
    return _plugin(
        "tld_domain", expr, include_private=include_private, parallel=parallel
    )


def suffix(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the public suffix, e.g. `"co.uk"` from `"bbc.co.uk"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of URLs (or bare hostnames).
    include_private : bool, default False
        Whether to treat the PSL's private section as suffixes.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column, **null** where no suffix rule matched. `extract` reports
        the same absence as an empty string.
    """
    return _plugin(
        "tld_suffix", expr, include_private=include_private, parallel=parallel
    )
