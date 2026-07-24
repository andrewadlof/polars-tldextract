"""Replacing the Public Suffix List in a running process.

Every test here mutates process-global state, so each one restores the list it
started with. `_restore_psl` does that even on failure; without it a single
failing assertion would leave every later test in the session parsing against
a toy list.

The download path in `refresh_psl` is exercised against a local file:// URL
rather than publicsuffix.org, so the suite stays offline and deterministic.
"""

from __future__ import annotations

import polars_tldextract as tld

from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

# A well-formed list whose only ICANN rule is one no real list has. Any host
# ending in `.frobnicate` parses differently under it, which is what makes a
# swap observable rather than merely reported.
FAKE_LIST = """\
// VERSION: 1999-01-01_00-00-00_UTC
// ===BEGIN ICANN DOMAINS===
frobnicate
// ===END ICANN DOMAINS===
// ===BEGIN PRIVATE DOMAINS===
// ===END PRIVATE DOMAINS===
"""

FAKE_VERSION = "1999-01-01_00-00-00_UTC"


# The vendored list, reachable from the source tree. Restoring from this file
# rather than from a captured string keeps the fixture honest: it reloads the
# same bytes the extension was built with.
BUNDLED_PATH = str(
    Path(__file__).resolve().parents[1]
    / "src"
    / "data"
    / "public_suffix_list.dat"
)


@pytest.fixture(autouse=True)
def _restore_psl() -> Iterator[None]:
    """Put the bundled list back after each test, however it ends.

    Yields
    ------
    None
        Control to the test, then restores the list.
    """
    yield
    tld.load_psl(BUNDLED_PATH)


def test_load_psl_from_text_changes_what_extraction_returns() -> None:
    """A swapped-in list takes effect for extractions that start after it."""
    before = tld.extract_scalar("host.frobnicate")
    assert before == (None, "frobnicate", None), before

    version = tld.load_psl(FAKE_LIST)

    assert version == FAKE_VERSION
    assert tld.psl_version() == FAKE_VERSION
    # `frobnicate` is now a suffix, so the registrable domain is one label out.
    assert tld.extract_scalar("host.frobnicate") == (
        "host.frobnicate",
        "host",
        "frobnicate",
    )


def test_the_expression_path_sees_the_new_list_too() -> None:
    """Swapping affects Polars expressions, not just the scalar helpers."""
    df = pl.DataFrame({"u": ["host.frobnicate", "news.bbc.co.uk", None]})

    tld.load_psl(FAKE_LIST)
    got = df.select(tld.registrable_domain("u"))["u"].to_list()

    # Under the toy list `bbc.co.uk` has no known suffix at all, so `co.uk`
    # stops being one and there is no registrable domain.
    assert got == ["host.frobnicate", None, None], got


def test_load_psl_from_a_path() -> None:
    """A `.dat` file on disk loads by path, which is the deployment shape."""
    version = tld.load_psl(BUNDLED_PATH)
    assert version == tld.psl_version()
    assert version != "unknown"


def test_load_psl_accepts_a_path_object(tmp_path: Path) -> None:
    """A `Path` is always read as a file, never mistaken for list text."""
    dat = tmp_path / "psl.dat"
    dat.write_text(FAKE_LIST, encoding="utf-8")
    assert tld.load_psl(dat) == FAKE_VERSION


def test_a_list_missing_section_markers_is_rejected() -> None:
    """Markers are load-bearing: without them private suffixes look ICANN."""
    before = tld.psl_version()
    with pytest.raises(ValueError, match="missing section marker"):
        tld.load_psl("// VERSION: nope\ncom\nco.uk\n")
    # The rejected load must not have disturbed the working list.
    assert tld.psl_version() == before
    assert tld.extract_scalar("news.bbc.co.uk")[0] == "bbc.co.uk"


def test_an_unreadable_path_is_rejected(tmp_path: Path) -> None:
    """A missing file raises rather than silently keeping the old list."""
    before = tld.psl_version()
    with pytest.raises(ValueError, match="could not read"):
        tld.load_psl(str(tmp_path / "does-not-exist.dat"))
    assert tld.psl_version() == before


def test_refresh_psl_downloads_and_loads(tmp_path: Path) -> None:
    """`refresh_psl` fetches, validates, and swaps in one call.

    Pointed at a `file://` URL so the test does not depend on the network or
    on what publicsuffix.org happens to be serving today.
    """
    src = tmp_path / "remote.dat"
    src.write_text(FAKE_LIST, encoding="utf-8")

    version = tld.refresh_psl(src.as_uri())

    assert version == FAKE_VERSION
    assert tld.psl_version() == FAKE_VERSION


def test_refresh_psl_can_save_a_copy(tmp_path: Path) -> None:
    """`save_to` writes the fetched list for later offline reuse."""
    src = tmp_path / "remote.dat"
    src.write_text(FAKE_LIST, encoding="utf-8")
    dest = tmp_path / "cached.dat"

    tld.refresh_psl(src.as_uri(), save_to=dest)

    assert dest.read_text(encoding="utf-8") == FAKE_LIST
    # And the saved copy is loadable on its own.
    assert tld.load_psl(dest) == FAKE_VERSION


def test_refresh_psl_rejects_a_bad_download(tmp_path: Path) -> None:
    """A truncated or wrong download leaves the working list in place."""
    src = tmp_path / "junk.dat"
    src.write_text("<html>404</html>", encoding="utf-8")
    before = tld.psl_version()

    with pytest.raises(ValueError, match="missing section marker"):
        tld.refresh_psl(src.as_uri())

    assert tld.psl_version() == before
    # Nothing was written, since validation happens before the save.
    assert not (tmp_path / "cached.dat").exists()


def test_psl_url_points_at_publicsuffix_org() -> None:
    """The default source is the canonical list, over TLS."""
    assert tld.PSL_URL.startswith("https://")
    assert tld.extract_scalar(tld.PSL_URL)[0] == "publicsuffix.org"
