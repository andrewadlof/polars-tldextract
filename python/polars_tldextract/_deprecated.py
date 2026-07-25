"""Shims for the 0.1 expression names, kept for one release.

Nothing here is part of the supported surface; each function warns and
delegates to its replacement in `polars_tldextract._expr`. Deleting this file
is the whole of the removal.
"""

from __future__ import annotations

from polars_tldextract import _expr

import warnings
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from polars._typing import IntoExprColumn

__all__ = ["parts", "top_domain"]


def _warn(message: str) -> None:
    """Emit a `DeprecationWarning` attributed to the caller.

    Parameters
    ----------
    message : str
        The warning text.
    """
    # stacklevel 3: this helper, the shim that called it, then user code.
    warnings.warn(message, DeprecationWarning, stacklevel=3)


def parts(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Build the 0.1 `full_domain` / `sld` / `tld` struct. Deprecated.

    The three fields are `registrable_domain`, `domain`, and `suffix`, which
    now carry the null semantics `parts` existed to provide. `full_domain` was
    a misnomer for the *registrable* domain -- `fqdn` is the actual full
    domain.

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
        Struct of `full_domain`, `sld`, and `tld`, as 0.1 returned it.
    """
    _warn(
        "tld.parts() is deprecated and will be removed in 0.3.0. Its fields "
        "are now separate expressions with the same null semantics: "
        "full_domain -> registrable_domain(), sld -> domain(), "
        "tld -> suffix(). For the whole hostname, use fqdn()."
    )
    kwargs = {"include_private": include_private, "parallel": parallel}
    return pl.struct(
        _expr.registrable_domain(expr, **kwargs).alias("full_domain"),
        _expr.domain(expr, **kwargs).alias("sld"),
        _expr.suffix(expr, **kwargs).alias("tld"),
    )


def top_domain(
    expr: IntoExprColumn,
    *,
    include_private: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the registrable label. Deprecated alias for `domain`.

    `top_domain` returned an empty string where there was no registrable label;
    `domain` returns null, like every other single-field expression.

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
        Utf8 column, null where there is no registrable label.
    """
    _warn(
        "tld.top_domain() is deprecated and will be removed in 0.3.0; use "
        "tld.domain(). Note the absent value is now null rather than an "
        "empty string -- extract().struct.field('domain') still gives you "
        "the empty-string form."
    )
    return _expr.domain(
        expr, include_private=include_private, parallel=parallel
    )
