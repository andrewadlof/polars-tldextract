"""Differential tests: the plugin must agree with `tldextract`, everywhere.

This is the load-bearing test file. The Rust implementation is a
reimplementation of someone else's algorithm against a different Public Suffix
List library, and the ways those two can quietly disagree (wildcard rules,
exception rules, the private section, IDN spellings) are exactly the cases
hand-written tests are worst at covering. So the rule corpus below is derived
mechanically from the list itself.
"""

from __future__ import annotations

import polars_tldextract as tld

import random
from typing import TYPE_CHECKING

import polars as pl
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    import tldextract as tldextract_mod

# Hand-picked cases covering each branch of the algorithm.
EDGE_CASES = [
    # Schemes, paths, queries, fragments, ports, userinfo.
    "https://www.nytimes.com/section/world?q=1#frag",
    "http://github.com",
    "//cdn.jsdelivr.net/x",
    "ftp://user:pw@ftp.gnu.org:2121/pub",
    "git+ssh://github.com",
    "github.com:8080",
    "ht_tp://github.com",
    "github.com//not-a-scheme",
    # Multi-label and unknown suffixes.
    "news.bbc.co.uk",
    "qantas.com.au",
    "printer.local",
    "foo.invalidtldthatwillneverexist",
    "localhost",
    "com",
    "co.uk",
    # Wildcard and exception rules.
    "foo.bar.ck",
    "www.ck",
    "foo.www.ck",
    "bar.ck",
    # Private section.
    "pola-rs.github.io",
    "github.io",
    "waiterrant.blogspot.com",
    # IP addresses.
    "127.0.0.1",
    "http://127.0.0.1:8080/x",
    "255.255.255.255",
    "256.0.0.1",
    "01.2.3.4",
    "1.2.3",
    "http://[2001:db8::1]:80/x",
    "[::ffff:1.2.3.4]",
    "[not-an-ipv6]",
    # Dots: trailing root labels and the three non-ASCII separators.
    "github.com.",
    "github.com...",
    "www。github．com｡",
    "github。com",
    # IDN, both spellings.
    "пример.рф",
    "xn--e1afmkfd.xn--p1ai",
    "bücher.de",
    "xn--bcher-kva.de",
    # Casing.
    "WWW.GITHUB.COM",
    "BBC.Co.Uk",
    # Degenerate input.
    "",
    "   ",
    "not a url",
    ".",
    "..",
    "@",
    "http://",
    "://github.com",
]

# Lifted from the consuming pipeline's regression fixtures so the migration is
# covered by the same corpus that guards it downstream.
PIPELINE_FIXTURES = [
    "https://www.cloudflare.com/products",
    "cloudflare.com",
    "CLOUDFLARE.COM",
    "  ",
    "",
    "news.bbc.co.uk",
    "keras.io",
    "not a url",
    "http://mail.google.com",
    "bücher.de",
    "https://en.wikipedia.org",
    "qantas.com.au",
    "www.speedtest.net",
    "t.co",
    "mail.bbc.co.uk",
]


def _reference_parts(
    reference: tldextract_mod.TLDExtract, url: str, *, include_private: bool
) -> tuple[str, str, str]:
    """`(subdomain, domain, suffix)` according to `tldextract`.

    Parameters
    ----------
    reference : tldextract_mod.TLDExtract
        Extractor bound to the vendored suffix list.
    url : str
        URL or bare hostname to parse.
    include_private : bool
        Whether the PSL's private section counts as suffixes.

    Returns
    -------
    tuple[str, str, str]
        The three parts, empty strings where absent.
    """
    r = reference(url, include_psl_private_domains=include_private)
    return (r.subdomain, r.domain, r.suffix)


def _plugin_parts(
    urls: list[str], *, include_private: bool
) -> list[tuple[str, ...]]:
    """`(subdomain, domain, suffix)` for each URL, via the Polars expression.

    Parameters
    ----------
    urls : list[str]
        URLs or bare hostnames to parse.
    include_private : bool
        Whether the PSL's private section counts as suffixes.

    Returns
    -------
    list[tuple[str, ...]]
        One tuple per input, in input order.
    """
    out = (
        pl
        .DataFrame({"url": urls})
        .select(tld.extract("url", include_private=include_private).alias("e"))
        .unnest("e")
    )
    return list(
        zip(
            out["subdomain"].to_list(),
            out["domain"].to_list(),
            out["suffix"].to_list(),
            strict=True,
        )
    )


def _assert_parity(
    reference: tldextract_mod.TLDExtract,
    urls: list[str],
    *,
    include_private: bool = False,
) -> None:
    """Assert the plugin matches `tldextract` for every URL.

    Reports up to ten mismatches rather than just the first, so a systematic
    divergence is visible in one run.

    Parameters
    ----------
    reference : tldextract_mod.TLDExtract
        Extractor bound to the vendored suffix list.
    urls : list[str]
        URLs or bare hostnames to parse.
    include_private : bool, default False
        Whether the PSL's private section counts as suffixes.
    """
    actual = _plugin_parts(urls, include_private=include_private)
    mismatches = [
        (url, exp, got)
        for url, got in zip(urls, actual, strict=True)
        if (
            exp := _reference_parts(
                reference, url, include_private=include_private
            )
        )
        != got
    ]
    if mismatches:
        shown = "\n".join(
            f"  {url!r}: tldextract={exp} plugin={got}"
            for url, exp, got in mismatches[:10]
        )
        pytest.fail(
            f"{len(mismatches)} of {len(urls)} URLs disagree "
            f"(include_private={include_private}):\n{shown}"
        )


@pytest.mark.parametrize("include_private", (False, True))
def test_edge_cases(
    reference: tldextract_mod.TLDExtract, *, include_private: bool
) -> None:
    """Hand-picked cases covering each branch of the algorithm."""
    _assert_parity(reference, EDGE_CASES, include_private=include_private)


@pytest.mark.parametrize("include_private", (False, True))
def test_pipeline_fixtures(
    reference: tldextract_mod.TLDExtract, *, include_private: bool
) -> None:
    """The consuming pipeline's own regression corpus."""
    _assert_parity(
        reference, PIPELINE_FIXTURES, include_private=include_private
    )


@pytest.mark.parametrize("include_private", (False, True))
def test_every_rule_in_the_list(
    reference: tldextract_mod.TLDExtract,
    hosts_from_rules: Callable[[], list[str]],
    *,
    include_private: bool,
) -> None:
    """Every rule in the PSL, as a bare host and with one and two labels above.

    This is what catches wildcard/exception divergence between the crate's
    trie walk and `tldextract`'s -- there is no way to enumerate those
    cases by hand.
    """
    _assert_parity(
        reference, hosts_from_rules(), include_private=include_private
    )


def test_random_urls(reference: tldextract_mod.TLDExtract) -> None:
    """Randomly assembled URLs, to shake out combinations nobody thought of."""
    rng = random.Random(20260724)
    schemes = ["", "http://", "https://", "//", "ftp://"]
    userinfos = ["", "user@", "user:pw@"]
    subs = ["", "www.", "a.b.", "mail.", "почта."]
    slds = ["wikipedia", "github", "bücher", "xn--bcher-kva", "123", "a-b"]
    sufs = [
        "com",
        "co.uk",
        "com.au",
        "org",
        "рф",
        "xn--p1ai",
        "blogspot.com",
        "ck",
        "bar.ck",
        "notarealsuffix",
    ]
    ports = ["", ":80", ":8080"]
    tails = ["", "/", "/a/b", "/a?q=1", "#frag", "/a?q=1#frag", "..", "."]

    urls = [
        f"{rng.choice(schemes)}{rng.choice(userinfos)}{rng.choice(subs)}"
        f"{rng.choice(slds)}.{rng.choice(sufs)}{rng.choice(ports)}{rng.choice(tails)}"
        for _ in range(200_000)
    ]
    _assert_parity(reference, urls)


@pytest.mark.parametrize("include_private", (False, True))
def test_scalar_matches_expression(*, include_private: bool) -> None:
    """The scalar helper and the expression must not drift apart.

    Both call the same Rust core, so this guards the two wrappers around it
    rather than the algorithm.
    """
    expected = _plugin_parts(EDGE_CASES, include_private=include_private)
    actual = [
        tld.extract_scalar_full(url, include_private=include_private)[:3]
        for url in EDGE_CASES
    ]
    assert actual == expected


def test_scalar_null_semantics() -> None:
    """`extract_scalar` collapses absent parts to `None`, like the pipeline."""
    assert tld.extract_scalar("https://www.bbc.co.uk/news") == (
        "bbc.co.uk",
        "bbc",
        "co.uk",
    )
    assert tld.extract_scalar(None) == (None, None, None)
    assert tld.extract_scalar("   ") == (None, None, None)
    # An IP has a "domain" but no suffix, so there is no full domain.
    assert tld.extract_scalar("127.0.0.1") == (None, "127.0.0.1", None)
    # A bare suffix has no registrable label.
    assert tld.extract_scalar("com") == (None, None, "com")


def test_private_section_is_opt_in() -> None:
    """Default behavior matches `TLDExtract()`, not `include_private=True`."""
    assert tld.extract_scalar("pola-rs.github.io") == (
        "github.io",
        "github",
        "io",
    )
    assert tld.extract_scalar("pola-rs.github.io", include_private=True) == (
        "pola-rs.github.io",
        "pola-rs",
        "github.io",
    )
