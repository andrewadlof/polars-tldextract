"""Type stubs for the compiled Rust extension module."""

PSL_PATH_ENV: str

def extract_scalar(
    url: str | None, *, include_private: bool = False
) -> tuple[str | None, str | None, str | None]:
    """Parse one URL into `(full_domain, sld, tld)`, absences as `None`."""

def extract_scalar_full(
    url: str, *, include_private: bool = False
) -> tuple[str, str, str, bool]:
    """Parse one URL into `(subdomain, domain, suffix, is_private)`."""

def psl_version() -> str:
    """Report the `VERSION:` stamp of the Public Suffix List in use."""
