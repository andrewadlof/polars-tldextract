"""Replacing the Public Suffix List in a running process.

The bundled snapshot is fixed at build time, which is the right default: no
network at import, no cache directory, no first-call latency spike. But the
list changes several times a week, and a long-lived process -- a notebook, a
Databricks cluster, a service -- would otherwise be stuck with whatever list it
read when it parsed its first URL.

`load_psl` swaps in a list you already have. `refresh_psl` downloads the
current one from publicsuffix.org and swaps that in. Neither runs unless
called, so the default path stays offline.

Both validate before swapping: a list that cannot be parsed, or that is missing
the section markers separating ICANN rules from private ones, is rejected and
the process keeps the list it already had. That check matters more than it
looks -- a list missing its markers parses as one undifferentiated section, and
every private suffix would quietly start counting as an ICANN one.
"""

from __future__ import annotations

from polars_tldextract._internal import load_psl_path, load_psl_text

import urllib.request
from pathlib import Path

__all__ = ["PSL_URL", "load_psl", "refresh_psl"]

#: Where `refresh_psl` downloads from by default.
PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"


def load_psl(source: str | Path) -> str:
    """Replace the live Public Suffix List, and return its version.

    Takes effect for every extraction that *starts* after it returns. A query
    already running keeps the list it started with, so no column can be parsed
    against two different lists.

    Parameters
    ----------
    source : str | pathlib.Path
        A path to a `.dat` file, or the list text itself. A `Path` is always
        read as a file; a `str` is treated as list text when it contains a
        newline and as a path otherwise.

    Returns
    -------
    str
        The `VERSION:` stamp of the newly loaded list.

    Raises
    ------
    ValueError
        If the file cannot be read, or the list cannot be parsed, or it is
        missing the ICANN/private section markers. The previously loaded list
        stays in use.

    Examples
    --------
    >>> import polars_tldextract as tld
    >>> path = "/mnt/reference/public_suffix_list.dat"
    >>> tld.load_psl(path)  # doctest: +SKIP
    '2026-07-21_08-00-00_UTC'
    """
    if isinstance(source, Path):
        return load_psl_path(str(source))
    return load_psl_text(source) if "\n" in source else load_psl_path(source)


def refresh_psl(
    url: str = PSL_URL,
    *,
    timeout: float = 60.0,
    save_to: str | Path | None = None,
) -> str:
    """Download the current Public Suffix List and load it.

    This is the only function in the package that touches the network, and only
    when you call it. Call it before the extraction whose results should
    reflect the newer list.

    Parameters
    ----------
    url : str, default `PSL_URL`
        Where to fetch the list from. Point this at an internal mirror if
        outbound access is restricted.
    timeout : float, default 60.0
        Seconds to wait for the download.
    save_to : str | pathlib.Path | None, optional
        If given, also write the downloaded list here, so a later run can
        `load_psl` it (or `POLARS_TLDEXTRACT_PSL` can point at it) without
        going back to the network. Only written once the list has been
        validated and loaded.

    Returns
    -------
    str
        The `VERSION:` stamp of the newly loaded list.

    Raises
    ------
    OSError
        If the download fails. The previously loaded list stays in use.
    ValueError
        If what comes back is not a usable Public Suffix List. The previously
        loaded list stays in use.

    Examples
    --------
    >>> import polars_tldextract as tld
    >>> tld.refresh_psl()  # doctest: +SKIP
    '2026-07-21_08-00-00_UTC'
    """
    with urllib.request.urlopen(url, timeout=timeout) as response:
        text = response.read().decode("utf-8")

    version = load_psl_text(text)

    if save_to is not None:
        Path(save_to).write_text(text, encoding="utf-8")

    return version
