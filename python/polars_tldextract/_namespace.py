"""The `.tld` expression namespace.

Importing `polars_tldextract` registers this, so `pl.col("url").tld.extract()`
works without importing anything else.
"""

from __future__ import annotations

from polars_tldextract import _deprecated, _expr

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

    def fqdn(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract the whole hostname, e.g. `"www.bbc.co.uk"`.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of hostnames, null where the input held no host.
        """
        return self._bind(
            _expr.fqdn, include_private=include_private, parallel=parallel
        )

    def registrable_domain(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract `"domain.suffix"`, or null when either half is missing.

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
            Utf8 column of subdomains, null where there is none.
        """
        return self._bind(
            _expr.subdomain, include_private=include_private, parallel=parallel
        )

    def domain(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract the registrable label, e.g. `"bbc"` from `"bbc.co.uk"`.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of registrable labels, null where there is none.
        """
        return self._bind(
            _expr.domain,
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
            Utf8 column of public suffixes, null where no rule matched.
        """
        return self._bind(
            _expr.suffix, include_private=include_private, parallel=parallel
        )

    # ------------------------------------------------------------------
    # Deprecated, removed in 0.3.0. See `polars_tldextract._deprecated`.
    # ------------------------------------------------------------------

    def parts(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Build the 0.1 struct. Use `registrable_domain`/`domain`/`suffix`.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Struct of `full_domain` / `sld` / `tld`, as 0.1 returned it.
        """
        return self._bind(
            _deprecated.parts,
            include_private=include_private,
            parallel=parallel,
        )

    def top_domain(
        self, *, include_private: bool = False, parallel: bool = True
    ) -> pl.Expr:
        """Extract the registrable label. Deprecated alias for `domain`.

        Parameters
        ----------
        include_private : bool, default False
            Whether to treat the PSL's private section as suffixes.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of registrable labels, null where there is none.
        """
        return self._bind(
            _deprecated.top_domain,
            include_private=include_private,
            parallel=parallel,
        )
