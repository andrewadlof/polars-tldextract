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
    "extract",
    "parts",
    "registrable_domain",
    "subdomain",
    "suffix",
    "top_domain",
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
    **empty strings**, not nulls. Use `parts` instead when nulls are wanted.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of URLs (or bare hostnames).
    include_private : bool, default False
        Whether to treat the PSL's private section as suffixes, matching
        `tldextract`'s `include_psl_private_domains`. With the default,
        `waiterrant.blogspot.com` has suffix `com`; with `True` it has suffix
        `blogspot.com`.
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


def parts(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Parse URLs into `full_domain` / `sld` / `tld`, using nulls for absences.

    The null semantics are the point: an empty string is a value that compares
    equal to another empty string, so two URLs that both failed to parse would
    match each other. `full_domain` is populated only when both halves exist.

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
        Struct of `full_domain` (`"sld.tld"`), `sld`, and `tld`.
    """
    return _plugin(
        "tld_parts", expr, include_private=include_private, parallel=parallel
    )


def registrable_domain(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract `"sld.tld"`, or null when either half is missing.

    Equivalent to `parts(...).struct.field("full_domain")` but computed in one
    pass without materializing the other two fields.

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
    """Extract the subdomain, e.g. `"www"` from `"www.example.com"`.

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
        Utf8 column, empty string where there is no subdomain.
    """
    return extract(
        expr, include_private=include_private, parallel=parallel
    ).struct.field("subdomain")


def top_domain(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the registrable label, e.g. `"example"` from `"example.co.uk"`.

    Named `top_domain` rather than `domain` because `domain` reads as the whole
    hostname to most callers; this is `tldextract`'s `domain` field.

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
        Utf8 column, empty string where there is no registrable label.
    """
    return extract(
        expr, include_private=include_private, parallel=parallel
    ).struct.field("domain")


def suffix(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the public suffix, e.g. `"co.uk"` from `"example.co.uk"`.

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
        Utf8 column, empty string where no suffix rule matched.
    """
    return extract(
        expr, include_private=include_private, parallel=parallel
    ).struct.field("suffix")
