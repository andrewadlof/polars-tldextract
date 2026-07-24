"""Shared fixtures.

The parity suite compares this plugin against `tldextract` itself. For that
comparison to mean anything, both sides must read the *same* Public Suffix
List -- otherwise a disagreement could just be two different snapshots. So
`tldextract` is pointed at the exact `.dat` file this crate compiles in, via a
`file://` URL (tldextract mounts a `requests_file` adapter for these), with an
isolated cache directory so a stale user cache cannot leak in.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import tldextract

if TYPE_CHECKING:
    from collections.abc import Callable

# repo_root/tests/conftest.py -> repo_root/src/data/public_suffix_list.dat
VENDORED_PSL = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "data"
    / "public_suffix_list.dat"
)


@pytest.fixture(scope="session")
def reference(
    tmp_path_factory: pytest.TempPathFactory,
) -> tldextract.TLDExtract:
    """`tldextract` bound to the same list the plugin compiles in.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Used to give tldextract a cache directory of its own.

    Returns
    -------
    tldextract.TLDExtract
        Extractor reading the vendored snapshot and nothing else.
    """
    assert VENDORED_PSL.is_file(), f"vendored PSL missing at {VENDORED_PSL}"
    return tldextract.TLDExtract(
        cache_dir=str(tmp_path_factory.mktemp("tldextract-cache")),
        suffix_list_urls=(VENDORED_PSL.as_uri(),),
        fallback_to_snapshot=False,
    )


@pytest.fixture(scope="session")
def psl_rules() -> list[str]:
    """Every rule in the vendored Public Suffix List.

    Returns
    -------
    list[str]
        Rule lines, comments and blanks removed, in file order.
    """
    rules: list[str] = []
    for raw in VENDORED_PSL.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        rules.append(line.split()[0])
    return rules


@pytest.fixture(scope="session")
def hosts_from_rules(psl_rules: list[str]) -> Callable[[], list[str]]:
    """Build concrete hostnames exercising every rule in the list.

    Each rule becomes three hosts: the rule itself, one label above it, and two
    labels above it. Wildcards get a concrete label; exception markers are
    stripped so the exception path is actually taken.

    Parameters
    ----------
    psl_rules : list[str]
        Rules from the vendored list.

    Returns
    -------
    Callable[[], list[str]]
        Callable returning the deduplicated host list.
    """

    def build() -> list[str]:
        seen: dict[str, None] = {}
        for rule in psl_rules:
            concrete = rule.lstrip("!").replace("*", "wildcardlabel")
            for host in (
                concrete,
                f"example.{concrete}",
                f"www.example.{concrete}",
            ):
                seen.setdefault(host, None)
        return list(seen)

    return build
