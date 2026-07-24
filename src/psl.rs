//! Public Suffix List loading, and replacing it at runtime.
//!
//! `tldextract` keeps two tries: one built from the ICANN section only, and
//! one that also carries the private section. `include_psl_private_domains`
//! selects between them. The `publicsuffix` crate exposes exactly that split
//! as [`IcannList`] and [`List`], so a [`Lists`] is just both of them plus the
//! version stamp they were built from.
//!
//! The initial list is the vendored snapshot compiled into the binary, unless
//! `POLARS_TLDEXTRACT_PSL` points at a `.dat` file on disk (for example a
//! Databricks Volume). That resolution happens on first use.
//!
//! After that, [`load_from_text`] and [`load_from_path`] replace the list in
//! the running process, which is what a long-lived job needs: the list changes
//! several times a week, and a cluster that has already parsed one URL would
//! otherwise be stuck with whatever it read first.
//!
//! Readers take a snapshot ([`current`]) rather than holding the lock, so a
//! replacement never blocks extraction and an in-flight query keeps the list
//! it started with. A query that starts after the swap sees the new one.

use std::str::FromStr;
use std::sync::{Arc, OnceLock, RwLock};

use publicsuffix::{IcannList, List};

/// Environment variable holding a path to a Public Suffix List `.dat` file.
///
/// Read when the list is first needed. To change lists after that point, call
/// [`load_from_path`] -- the variable is not re-read.
pub const PSL_PATH_ENV: &str = "POLARS_TLDEXTRACT_PSL";

/// The snapshot vendored into this crate, taken from `tldextract`'s own
/// `.tld_set_snapshot` so both implementations agree by construction.
const BUNDLED_PSL: &str = include_str!("data/public_suffix_list.dat");

/// Section markers the `publicsuffix` crate keys on when splitting ICANN rules
/// from private ones.
///
/// A list missing either parses as one undifferentiated section, which would
/// silently break `include_private=False` -- every private suffix would start
/// counting as an ICANN one. Rejecting the load is the only safe answer, since
/// the damage is invisible in the output.
const REQUIRED_MARKERS: [&str; 2] = ["===BEGIN ICANN DOMAINS===", "===BEGIN PRIVATE DOMAINS==="];

/// A parsed Public Suffix List: both tries, plus the version they came from.
pub struct Lists {
    icann: IcannList,
    full: List,
    version: String,
}

impl Lists {
    /// Parse list text into both tries.
    ///
    /// # Errors
    ///
    /// Returns a message describing why the text is not a usable list: a
    /// missing section marker, a parse failure, or a list with no rules.
    pub fn from_text(text: &str) -> Result<Self, String> {
        let missing: Vec<&str> =
            REQUIRED_MARKERS.iter().copied().filter(|m| !text.contains(m)).collect();
        if !missing.is_empty() {
            return Err(format!(
                "not a Public Suffix List: missing section marker(s) {missing:?}. \
                 Without them the private section cannot be told apart from the ICANN one."
            ));
        }

        let icann =
            IcannList::from_str(text).map_err(|e| format!("ICANN rules failed to parse: {e}"))?;
        let full =
            List::from_str(text).map_err(|e| format!("full rule set failed to parse: {e}"))?;
        if icann.is_empty() || full.is_empty() {
            return Err("list parsed but contains no rules".to_owned());
        }

        Ok(Self { icann, full, version: version_of(text) })
    }

    /// The ICANN-only list: `tldextract`'s `tlds_excl_private_trie`.
    pub fn icann(&self) -> &IcannList {
        &self.icann
    }

    /// The full list, ICANN plus private: `tldextract`'s `tlds_incl_private_trie`.
    pub fn full(&self) -> &List {
        &self.full
    }

    /// The `VERSION:` stamp this list was built from.
    pub fn version(&self) -> &str {
        &self.version
    }
}

/// The `VERSION:` header of a list, or `"unknown"` when it carries none.
fn version_of(text: &str) -> String {
    text.lines()
        .take(40)
        .find_map(|line| line.strip_prefix("// VERSION: "))
        .unwrap_or("unknown")
        .trim()
        .to_owned()
}

static CURRENT: OnceLock<RwLock<Arc<Lists>>> = OnceLock::new();

/// The slot holding the live list, initialized from the environment override
/// or the bundled snapshot on first access.
///
/// # Panics
///
/// Panics if `POLARS_TLDEXTRACT_PSL` is set but unreadable, or if the list it
/// points at does not parse. That is deliberate: quietly falling back to the
/// bundled snapshot would produce wrong suffixes with no signal, and there is
/// no caller to return an error to on a lazily-initialized static.
fn slot() -> &'static RwLock<Arc<Lists>> {
    CURRENT.get_or_init(|| {
        let initial = match std::env::var(PSL_PATH_ENV) {
            Ok(path) if !path.trim().is_empty() => {
                let text = std::fs::read_to_string(&path).unwrap_or_else(|e| {
                    panic!("{PSL_PATH_ENV} is set to {path:?} but it could not be read: {e}")
                });
                Lists::from_text(&text).unwrap_or_else(|e| {
                    panic!("{PSL_PATH_ENV} is set to {path:?} but it is not usable: {e}")
                })
            },
            _ => Lists::from_text(BUNDLED_PSL).expect("bundled Public Suffix List failed to parse"),
        };
        RwLock::new(Arc::new(initial))
    })
}

/// A snapshot of the list in use.
///
/// Callers hold the [`Arc`] for the duration of one operation -- an expression
/// evaluation takes it once and shares it across the rayon fan-out -- so every
/// row of one query is parsed against the same list even if another thread
/// swaps it mid-flight.
pub fn current() -> Arc<Lists> {
    Arc::clone(&slot().read().expect("PSL lock poisoned"))
}

/// Replace the live list with one parsed from `text`, returning its version.
///
/// The new text is parsed *before* anything is swapped, so a bad list leaves
/// the process running on the one it already had.
///
/// # Errors
///
/// Returns the parse failure from [`Lists::from_text`], unchanged.
pub fn load_from_text(text: &str) -> Result<String, String> {
    let psl = Lists::from_text(text)?;
    let version = psl.version().to_owned();
    *slot().write().expect("PSL lock poisoned") = Arc::new(psl);
    Ok(version)
}

/// Replace the live list with one read from `path`, returning its version.
///
/// # Errors
///
/// Returns a message if the file cannot be read, or the parse failure from
/// [`Lists::from_text`].
pub fn load_from_path(path: &str) -> Result<String, String> {
    let text =
        std::fs::read_to_string(path).map_err(|e| format!("could not read {path:?}: {e}"))?;
    load_from_text(&text)
}

/// The `VERSION:` stamp of the list in use, for logging and provenance.
pub fn psl_version() -> String {
    current().version().to_owned()
}

#[cfg(test)]
mod tests {
    use publicsuffix::Psl as _;

    use super::*;

    /// A minimal but well-formed list. `frobnicate` is a suffix here and in no
    /// real list, which is what makes a swap observable.
    const FAKE_LIST: &str = "\
// VERSION: 1999-01-01_00-00-00_UTC
// ===BEGIN ICANN DOMAINS===
frobnicate
// ===END ICANN DOMAINS===
// ===BEGIN PRIVATE DOMAINS===
// ===END PRIVATE DOMAINS===
";

    // These tests exercise `Lists::from_text` rather than `load_from_text`.
    // `cargo test` runs a binary's tests on parallel threads and the live list
    // is process-global, so a unit test that swapped it would change what the
    // extract tests see. The swap itself is covered end-to-end in
    // `tests/test_psl.py`, where pytest runs serially and restores the list.

    #[test]
    fn bundled_list_parses_and_is_versioned() {
        let psl = current();
        assert!(!psl.icann().is_empty());
        assert!(!psl.full().is_empty());
        assert_ne!(psl.version(), "unknown");
    }

    #[test]
    fn a_list_missing_its_section_markers_is_rejected() {
        // Rules alone are not enough: without the markers every private
        // suffix would be treated as an ICANN one.
        let Err(err) = Lists::from_text("// VERSION: fake\ncom\nco.uk\n") else {
            panic!("a list with no section markers must be rejected")
        };
        assert!(err.contains("missing section marker"), "{err}");
    }

    #[test]
    fn a_list_with_markers_but_no_rules_is_rejected() {
        let text =
            format!("// VERSION: fake\n// {}\n// {}\n", REQUIRED_MARKERS[0], REQUIRED_MARKERS[1]);
        assert!(Lists::from_text(&text).is_err());
    }

    #[test]
    fn a_well_formed_replacement_parses_and_carries_its_version() {
        let psl = Lists::from_text(FAKE_LIST).expect("well-formed list should parse");
        assert_eq!(psl.version(), "1999-01-01_00-00-00_UTC");
        // The fake rule matches, proving the tries came from this text and not
        // from the bundled snapshot.
        let info = psl.icann().find("host.frobnicate".rsplit('.').map(str::as_bytes));
        assert!(info.typ.is_some());
        assert_eq!(info.len, "frobnicate".len());
    }

    #[test]
    fn a_list_with_no_version_header_still_loads() {
        let text = FAKE_LIST.replace("// VERSION: 1999-01-01_00-00-00_UTC\n", "");
        assert_eq!(Lists::from_text(&text).expect("should parse").version(), "unknown");
    }
}
