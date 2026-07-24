//! The extraction algorithm, mirroring `TLDExtract._extract_netloc` and
//! `_PublicSuffixListTLDExtractor.suffix_index`.
//!
//! # Why `publicsuffix` lines up with `tldextract`
//!
//! `tldextract` walks a reversed-label trie and reports the index of the first
//! suffix label, or "no suffix" when nothing matched. `publicsuffix`'s
//! [`Psl::find`] walks the same shape of trie and reports the *byte length* of
//! the matched suffix plus a [`Type`]. Two properties make them equivalent:
//!
//! * `Info::typ == None` is exactly `tldextract`'s "no suffix". The crate
//!   applies the PSL's implicit `*` rule by returning the last label's length
//!   with no type; `tldextract` deliberately does not apply it and returns
//!   `None` instead. Keying on `typ` rather than `len` reconciles the two.
//! * [`IcannList`](publicsuffix::IcannList) rejects private leaves while still
//!   descending private branches. `tldextract`'s ICANN-only trie has no
//!   private nodes at all, so it stops one step earlier -- but since a private
//!   branch never carries an ICANN leaf below it, both arrive at the same
//!   suffix.
//!
//! Wildcard (`*.ck`) and exception (`!www.ck`) rules were traced through both
//! implementations by hand; `tests/test_parity.py` then checks every rule in
//! the list mechanically.

use std::borrow::Cow;

use publicsuffix::{Psl, Type};

use crate::netloc::{lenient_netloc, looks_like_ip, looks_like_ipv6, with_ascii_dots};
use crate::psl::Lists;

/// A parsed URL, borrowing from the netloc it was parsed out of.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Extracted<'a> {
    /// Everything ahead of the registrable domain, e.g. `www` or `a.b`.
    pub subdomain: &'a str,
    /// The registrable label, e.g. `example`. Empty when there is none.
    pub domain: &'a str,
    /// The public suffix, e.g. `co.uk`. Empty when no rule matched.
    pub suffix: &'a str,
    /// Whether the matched suffix came from the PSL's private section. Always
    /// `false` unless `include_private` was set.
    pub is_private: bool,
}

/// Parse `url` and hand the result to `f`.
///
/// Takes a callback rather than returning, because the result borrows from a
/// normalized form of the input that may be a temporary. Callers that need to
/// keep the strings copy them inside the closure; the hot path (writing into
/// Polars string builders) does not need to copy at all.
///
/// `include_private` mirrors `tldextract`'s `include_psl_private_domains`.
pub fn with_extracted<R>(
    url: &str,
    include_private: bool,
    f: impl FnOnce(Extracted<'_>) -> R,
) -> R {
    with_extracted_in(&crate::psl::current(), url, include_private, f)
}

/// Parse `url` against a specific list snapshot and hand the result to `f`.
///
/// The vectorized paths take one snapshot per expression evaluation and reuse
/// it for every row, so a list swapped in mid-query cannot make one column
/// disagree with itself -- and so the hot path never touches the lock.
pub fn with_extracted_in<R>(
    psl: &Lists,
    url: &str,
    include_private: bool,
    f: impl FnOnce(Extracted<'_>) -> R,
) -> R {
    let netloc = with_ascii_dots(lenient_netloc(url));
    f(extract_netloc(psl, &netloc, include_private))
}

/// Parse an already-normalized netloc (scheme/path/port stripped, ASCII dots).
fn extract_netloc<'a>(psl: &Lists, netloc: &'a str, include_private: bool) -> Extracted<'a> {
    // A bracketed IPv6 literal is reported whole, brackets included, as the
    // domain. `min_num_ipv6_chars` in the Python is 4.
    if netloc.len() >= 4
        && netloc.starts_with('[')
        && netloc.ends_with(']')
        && looks_like_ipv6(&netloc[1..netloc.len() - 1])
    {
        return Extracted { subdomain: "", domain: netloc, suffix: "", is_private: false };
    }

    // The list is all-lowercase, so the lookup needs a lowercased host. Hosts
    // are usually already lowercase, in which case this borrows and the whole
    // parse is allocation-free. Case folding never adds or removes a `.`, so
    // the lowercased form always has the same label count as the original.
    let lookup: Cow<'_, str> =
        if netloc.bytes().any(|b| b.is_ascii_uppercase()) || !netloc.is_ascii() {
            Cow::Owned(netloc.to_lowercase())
        } else {
            Cow::Borrowed(netloc)
        };

    let info = if include_private {
        psl.full().find(lookup.rsplit('.').map(str::as_bytes))
    } else {
        psl.icann().find(lookup.rsplit('.').map(str::as_bytes))
    };

    let Some(typ) = info.typ else {
        // No rule matched. `tldextract` treats a bare dotted quad as an IP
        // (domain = the address, no suffix); anything else keeps its last
        // label as the domain even though that label is not a real suffix.
        const NUM_IPV4_LABELS: usize = 4;
        if netloc.split('.').count() == NUM_IPV4_LABELS && looks_like_ip(netloc) {
            return Extracted { subdomain: "", domain: netloc, suffix: "", is_private: false };
        }
        let (head, last) = split_last_label(netloc);
        return Extracted { subdomain: head, domain: last, suffix: "", is_private: false };
    };

    // `info.len` is a byte length over the *lookup* labels, so count labels
    // there and apply the count to the original, whose labels may differ in
    // length after case folding.
    let mut consumed = 0usize;
    let mut suffix_labels = 0usize;
    for label in lookup.rsplit('.') {
        if consumed >= info.len {
            break;
        }
        consumed += label.len() + usize::from(consumed != 0);
        suffix_labels += 1;
    }

    let (head, suffix) = split_last_n_labels(netloc, suffix_labels);
    let (subdomain, domain) = split_last_label(head);
    Extracted { subdomain, domain, suffix, is_private: typ == Type::Private }
}

/// Split off the last `n` dot-separated labels.
///
/// Returns `(head, tail)` where *tail* holds `n` labels and *head* holds the
/// rest with no trailing dot. `head` is empty when the whole string is taken.
fn split_last_n_labels(s: &str, n: usize) -> (&str, &str) {
    if n == 0 {
        return (s, "");
    }
    let mut seen = 0usize;
    for (i, _) in s.rmatch_indices('.') {
        seen += 1;
        if seen == n {
            return (&s[..i], &s[i + 1..]);
        }
    }
    ("", s)
}

/// Split off the final label: `("a.b", "c")` for `"a.b.c"`, `("", "c")` for
/// `"c"`, and `("", "")` for `""`.
fn split_last_label(s: &str) -> (&str, &str) {
    match s.rfind('.') {
        Some(i) => (&s[..i], &s[i + 1..]),
        None => ("", s),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `(subdomain, domain, suffix)` for a URL, for terse assertions.
    fn parts(url: &str) -> (String, String, String) {
        with_extracted(url, false, |e| {
            (e.subdomain.to_owned(), e.domain.to_owned(), e.suffix.to_owned())
        })
    }

    fn parts_private(url: &str) -> (String, String, String, bool) {
        with_extracted(url, true, |e| {
            (e.subdomain.to_owned(), e.domain.to_owned(), e.suffix.to_owned(), e.is_private)
        })
    }

    fn t(a: &str, b: &str, c: &str) -> (String, String, String) {
        (a.to_owned(), b.to_owned(), c.to_owned())
    }

    #[test]
    fn simple_and_multi_label_suffixes() {
        assert_eq!(parts("http://www.nytimes.com/x"), t("www", "nytimes", "com"));
        assert_eq!(parts("news.bbc.co.uk"), t("news", "bbc", "co.uk"));
        assert_eq!(parts("github.com"), t("", "github", "com"));
        assert_eq!(parts("com"), t("", "", "com"));
    }

    #[test]
    fn unknown_suffix_keeps_last_label_as_domain() {
        assert_eq!(parts("printer.local"), t("printer", "local", ""));
        assert_eq!(parts("localhost"), t("", "localhost", ""));
        assert_eq!(parts(""), t("", "", ""));
        assert_eq!(parts("   "), t("", "", ""));
    }

    #[test]
    fn wildcard_and_exception_rules() {
        // `*.ck` makes any label directly under `ck` part of the suffix, so
        // the registrable domain sits one level further out than usual.
        assert_eq!(parts("bar.ck"), t("", "", "bar.ck"));
        assert_eq!(parts("foo.bar.ck"), t("", "foo", "bar.ck"));
        assert_eq!(parts("a.foo.bar.ck"), t("a", "foo", "bar.ck"));
        // ... except `!www.ck`, whose exception rule pulls `www` back down to
        // being the domain under the plain `ck` suffix.
        assert_eq!(parts("www.ck"), t("", "www", "ck"));
        assert_eq!(parts("foo.www.ck"), t("foo", "www", "ck"));
    }

    #[test]
    fn private_section_is_opt_in() {
        assert_eq!(parts("pola-rs.github.io"), t("pola-rs", "github", "io"));
        assert_eq!(
            parts_private("pola-rs.github.io"),
            ("".to_owned(), "pola-rs".to_owned(), "github.io".to_owned(), true)
        );
    }

    #[test]
    fn ip_addresses() {
        assert_eq!(parts("http://127.0.0.1:8080/x"), t("", "127.0.0.1", ""));
        assert_eq!(parts("http://[2001:db8::1]:80/x"), t("", "[2001:db8::1]", ""));
        // Not a valid octet, so it parses as an ordinary (suffix-less) host.
        assert_eq!(parts("999.0.0.1"), t("999.0.0", "1", ""));
    }

    #[test]
    fn casing_and_idn_are_preserved_in_output() {
        assert_eq!(parts("WWW.GITHUB.COM"), t("WWW", "GITHUB", "COM"));
        // Unicode and punycode spellings of the same suffix both resolve, and
        // the output keeps whichever spelling came in.
        assert_eq!(parts("пример.рф"), t("", "пример", "рф"));
        assert_eq!(parts("xn--e1afmkfd.xn--p1ai"), t("", "xn--e1afmkfd", "xn--p1ai"));
    }

    #[test]
    fn ideographic_dots_are_label_separators() {
        assert_eq!(parts("www\u{3002}github\u{ff0e}com"), t("www", "github", "com"));
    }

    #[test]
    fn label_splitting_helpers() {
        assert_eq!(split_last_n_labels("a.b.c", 2), ("a", "b.c"));
        assert_eq!(split_last_n_labels("a.b.c", 3), ("", "a.b.c"));
        assert_eq!(split_last_n_labels("a.b.c", 9), ("", "a.b.c"));
        assert_eq!(split_last_n_labels("a.b.c", 0), ("a.b.c", ""));
        assert_eq!(split_last_label("a.b.c"), ("a.b", "c"));
        assert_eq!(split_last_label("c"), ("", "c"));
        assert_eq!(split_last_label(""), ("", ""));
    }
}
