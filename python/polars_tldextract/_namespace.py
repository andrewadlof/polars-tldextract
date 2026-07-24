"""The `.tld` expression namespace.

Importing `polars_tldextract` registers this, so `pl.col("url").tld.parts()`
works without importing anything else.
"""

from __future__ import annotations

from polars_tldextract import _expr

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Callable


@pl.api.register_expr_namespace("tld")
class TldNamespace:
    """`tldextract`-compatible URL parsing, as `pl.col(...).tld.*`."""

    def __init__(self, expr: pl.Expr) -> None:
        """Bind the namespace to an expression.

        Parameters
        ----------
        expr : pl.Expr
            The string expression the namespace methods operate on.
        """
        self._expr = expr

    def _bind(self, fn: Callable[..., pl.Expr], **kwargs: bool) -> pl.Expr:
        """Apply a module-level expression builder to the bound expression.

        Parameters
        ----------
        fn : Callable[..., pl.Expr]
            One of the builders in `polars_tldextract._expr`.
        **kwargs : bool
            Passed straight through to *fn*.

        Returns
        -------
        pl.Expr
            The built expression.
        """
        return fn(self._expr, **kwargs)

    def extract(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Parse into a `tldextract`-faithful struct.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Struct of `subdomain` / `domain` / `suffix` / `is_private`.
        """
        return self._bind(
            _expr.extract, include_private=include_private, parallel=parallel
        )

    def parts(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Parse into `full_domain` / `sld` / `tld`, using nulls for absences.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Struct of `full_domain` / `sld` / `tld`.
        """
        return self._bind(
            _expr.parts, include_private=include_private, parallel=parallel
        )

    def registrable_domain(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract `"sld.tld"`, or null when either half is missing.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of registrable domains.
        """
        return self._bind(
            _expr.registrable_domain,
            include_private=include_private,
            parallel=parallel,
        )

    def subdomain(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract the subdomain.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of subdomains.
        """
        return self._bind(
            _expr.subdomain, include_private=include_private, parallel=parallel
        )

    def top_domain(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract the registrable label.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of registrable labels.
        """
        return self._bind(
            _expr.top_domain,
            include_private=include_private,
            parallel=parallel,
        )

    def suffix(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract the public suffix.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of public suffixes.
        """
        return self._bind(
            _expr.suffix, include_private=include_private, parallel=parallel
        )
