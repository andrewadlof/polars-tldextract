"""Throughput comparison against the `map_elements` path this replaces.

Marked `bench` and deselected by default (`just bench` runs it), because it is
a timing measurement, not a correctness check. The one assertion is a floor
generous enough that it fails only on a genuine performance regression, not on
a busy machine.
"""

from __future__ import annotations

import polars_tldextract as tld

import random
import time
from typing import TYPE_CHECKING

import polars as pl
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    import tldextract as tldextract_mod

ROWS = 200_000

# The `map_elements` path must beat this by a wide margin for the rewrite to be
# worth the native dependency. The reference machine measures ~94k rows/s
# through `map_elements` against ~2.05M single-threaded, a 21.7x margin, so the
# floor leaves room for a slower or busier host before it starts crying wolf.
#
# The margin is narrower than it looks against the 0.1 figure of 26.5x: that
# baseline also lowercased and stripped every URL, work the plugin was not
# doing. Removing it made the comparison honest and the baseline faster.
MIN_SPEEDUP = 15.0

pytestmark = pytest.mark.bench


@pytest.fixture(scope="module")
def urls() -> list[str]:
    """Build a realistic mix of URLs to parse.

    Returns
    -------
    list[str]
        `ROWS` synthetic URLs spanning several suffix shapes.
    """
    rng = random.Random(20260724)
    suffixes = [
        "com",
        "co.uk",
        "org",
        "net",
        "io",
        "com.au",
        "de",
        "jp",
        "blogspot.com",
        "invalidtld",
    ]
    return [
        f"https://www.{rng.randrange(50_000)}.example{i % 97}."
        f"{rng.choice(suffixes)}/path?q=1"
        for i in range(ROWS)
    ]


def _timed(label: str, fn: Callable[[], object]) -> float:
    """Run `fn`, print its throughput, and return the elapsed seconds.

    Parameters
    ----------
    label : str
        Name to print alongside the measurement.
    fn : Callable[[], object]
        Zero-argument callable to time.

    Returns
    -------
    float
        Elapsed wall-clock seconds.
    """
    start = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - start
    print(f"  {label:<34} {elapsed:7.3f}s  {ROWS / elapsed:>12,.0f} rows/s")
    return elapsed


def test_throughput_vs_map_elements(
    urls: list[str], reference: tldextract_mod.TLDExtract
) -> None:
    """The plugin must be far faster than driving `tldextract` per row."""
    df = pl.DataFrame({"u": urls})
    dtype = pl.Struct([
        pl.Field("subdomain", pl.String),
        pl.Field("domain", pl.String),
        pl.Field("suffix", pl.String),
    ])

    def via_map_elements() -> None:
        def extract_one(url: str) -> tuple[str, str, str]:
            r = reference(url)
            return (r.subdomain, r.domain, r.suffix)

        df.with_columns(
            pl
            .col("u")
            .map_elements(extract_one, return_dtype=dtype)
            .alias("d")
        )

    print(f"\n{ROWS:,} rows:")
    baseline = _timed("tldextract via map_elements", via_map_elements)
    serial = _timed(
        "plugin (parallel=False)",
        lambda: df.with_columns(tld.extract("u", parallel=False).alias("d")),
    )
    parallel = _timed(
        "plugin (parallel=True)",
        lambda: df.with_columns(tld.extract("u", parallel=True).alias("d")),
    )
    print(
        f"  speedup: {baseline / serial:.1f}x single-threaded, "
        f"{baseline / parallel:.1f}x multi-threaded"
    )

    assert baseline / serial > MIN_SPEEDUP, (
        f"plugin is only {baseline / serial:.1f}x faster than map_elements; "
        f"expected at least {MIN_SPEEDUP}x. If this is far below the floor "
        f"rather than just under it, check that the installed extension is an "
        f"optimized build: `maturin develop` without `--release` is ~15x "
        f"slower here, which measures the profile rather than the code. "
        f"`just bench` builds the right one."
    )
