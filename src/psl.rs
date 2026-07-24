//! Public Suffix List loading.
//!
//! `tldextract` keeps two tries: one built from the ICANN section only, and
//! one that also carries the private section. `include_psl_private_domains`
//! selects between them. The `publicsuffix` crate exposes exactly that split
//! as [`IcannList`] and [`List`], so this module just builds both once.
//!
//! The list text is the vendored snapshot compiled into the binary, unless
//! `POLARS_TLDEXTRACT_PSL` points at a `.dat` file on disk (for example a
//! Databricks Volume), which lets the list be refreshed without a rebuild.
//! Whichever source wins is resolved on first use and cached for the life of
//! the process.

use std::borrow::Cow;
use std::str::FromStr;
use std::sync::OnceLock;

use publicsuffix::{IcannList, List};

/// Environment variable holding a path to a Public Suffix List `.dat` file.
///
/// Must be set before the first extraction, since the parsed list is cached.
pub const PSL_PATH_ENV: &str = "POLARS_TLDEXTRACT_PSL";

/// The snapshot vendored into this crate, taken from `tldextract`'s own
/// `.tld_set_snapshot` so both implementations agree by construction.
const BUNDLED_PSL: &str = include_str!("data/public_suffix_list.dat");

static PSL_TEXT: OnceLock<Cow<'static, str>> = OnceLock::new();
static ICANN_LIST: OnceLock<IcannList> = OnceLock::new();
static FULL_LIST: OnceLock<List> = OnceLock::new();

/// The raw list text, from the environment override or the bundled snapshot.
///
/// An unreadable or unparseable override is a hard error rather than a silent
/// fallback: quietly parsing a different list than the operator asked for
/// would produce wrong suffixes with no signal.
fn psl_text() -> &'static Cow<'static, str> {
    PSL_TEXT.get_or_init(|| match std::env::var(PSL_PATH_ENV) {
        Ok(path) if !path.trim().is_empty() => {
            let text = std::fs::read_to_string(&path).unwrap_or_else(|e| {
                panic!("{PSL_PATH_ENV} is set to {path:?} but it could not be read: {e}")
            });
            Cow::Owned(text)
        },
        _ => Cow::Borrowed(BUNDLED_PSL),
    })
}

/// The ICANN-only list: `tldextract`'s `tlds_excl_private_trie`.
pub fn icann_list() -> &'static IcannList {
    ICANN_LIST.get_or_init(|| {
        IcannList::from_str(psl_text()).expect("Public Suffix List failed to parse")
    })
}

/// The full list, ICANN plus private: `tldextract`'s `tlds_incl_private_trie`.
pub fn full_list() -> &'static List {
    FULL_LIST
        .get_or_init(|| List::from_str(psl_text()).expect("Public Suffix List failed to parse"))
}

/// The `VERSION:` header of the list in use, for logging and provenance.
///
/// Falls back to `"unknown"` when the list carries no such header (the
/// official list always does).
pub fn psl_version() -> String {
    psl_text()
        .lines()
        .take(40)
        .find_map(|line| line.strip_prefix("// VERSION: "))
        .unwrap_or("unknown")
        .trim()
        .to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_list_parses_and_is_versioned() {
        assert!(!icann_list().is_empty());
        assert!(!full_list().is_empty());
        assert_ne!(psl_version(), "unknown");
    }
}
