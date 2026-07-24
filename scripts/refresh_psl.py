"""Re-vendor the Public Suffix List from publicsuffix.org.

Overwrites `src/data/public_suffix_list.dat`, which is compiled into the
binary. Rebuild and re-run the parity suite afterwards: a list refresh changes
what the plugin returns, and `tests/test_parity.py` reads the same vendored
file, so it will re-derive its rule corpus from the new list automatically.

Usage
-----
    just refresh-psl
    # then: just check && just bump patch
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

LIST_URL = "https://publicsuffix.org/list/public_suffix_list.dat"
TARGET = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "data"
    / "public_suffix_list.dat"
)

# Markers the `publicsuffix` crate keys on when splitting ICANN from private
# rules. A list missing either would silently parse as one undifferentiated
# section, which would break `include_private=False`.
REQUIRED_MARKERS = ("===BEGIN ICANN DOMAINS===", "===BEGIN PRIVATE DOMAINS===")


def _version_of(text: str) -> str:
    """Read the list's `// VERSION:` header.

    Parameters
    ----------
    text : str
        Contents of a Public Suffix List `.dat` file.

    Returns
    -------
    str
        The version stamp, or `"unknown"` if the header is absent.
    """
    for line in text.splitlines()[:40]:
        if line.startswith("// VERSION: "):
            return line.removeprefix("// VERSION: ").strip()
    return "unknown"


def main() -> int:
    """Download the list, sanity-check it, and write it into the crate.

    Returns
    -------
    int
        Process exit status: 0 on success, 1 if the download looks wrong.
    """
    print(f"Fetching {LIST_URL} ...")
    with urllib.request.urlopen(LIST_URL, timeout=60) as response:
        text = response.read().decode("utf-8")

    missing = [m for m in REQUIRED_MARKERS if m not in text]
    if missing:
        print(f"error: downloaded list is missing {missing}", file=sys.stderr)
        return 1

    old = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    if text == old:
        print(f"Already up to date ({_version_of(old)}).")
        return 0

    TARGET.write_text(text, encoding="utf-8")
    print(f"Updated {TARGET.name}: {_version_of(old)} -> {_version_of(text)}")
    print(
        "Now run: just check   (parity re-derives its corpus from this file)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
