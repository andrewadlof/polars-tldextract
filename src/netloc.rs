//! Port of `tldextract/remote.py`.
//!
//! Every function here mirrors its Python counterpart exactly, including the
//! quirks, because the whole point of this crate is drop-in parity with
//! `tldextract`. Where a Python string operation has no direct Rust
//! equivalent, the comment names the operation being emulated.

use std::net::Ipv6Addr;

/// The characters `urllib.parse.scheme_chars` permits in a URL scheme.
///
/// ASCII letters plus digits plus `+`, `-`, `.`.
#[inline]
fn is_scheme_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.')
}

/// Strip a leading `scheme://` if (and only if) one is really there.
///
/// Mirrors `tldextract.remote._schemeless_url`: find the first `//`; if it is
/// at index 0 the URL is protocol-relative and we drop it. Otherwise the two
/// characters before it must be a scheme followed by `:`, and every character
/// of that scheme must be a scheme character -- else the `//` belongs to
/// something else (a path, say) and the URL is returned untouched.
pub fn schemeless_url(url: &str) -> &str {
    let Some(idx) = url.find("//") else {
        return url;
    };
    if idx == 0 {
        return &url[2..];
    }
    // `idx < 2` leaves no room for a one-character scheme plus its colon.
    if idx < 2 || url.as_bytes()[idx - 1] != b':' || !url[..idx - 1].chars().all(is_scheme_char) {
        return url;
    }
    &url[idx + 2..]
}

/// Extract the netloc of a URL-like string, leniently.
///
/// Mirrors `tldextract.remote.lenient_netloc`. Never fails: anything that
/// cannot be understood as a URL is simply returned as-is.
pub fn lenient_netloc(url: &str) -> &str {
    let schemeless = schemeless_url(url);

    // `.partition("/")[0].partition("?")[0].partition("#")[0]`
    let before_path = match schemeless.find(['/', '?', '#']) {
        Some(i) => &schemeless[..i],
        None => schemeless,
    };

    // `.rpartition("@")[-1]` -- drop any userinfo.
    let after_userinfo = match before_path.rfind('@') {
        Some(i) => &before_path[i + 1..],
        None => before_path,
    };

    // A bracketed IPv6 literal is returned with its brackets and without any
    // port, so the caller can recognize it.
    if after_userinfo.starts_with('[') {
        if let Some(i) = after_userinfo.find(']') {
            return &after_userinfo[..=i];
        }
    }

    // `.partition(":")[0].strip()` -- drop the port, then Unicode-trim.
    let hostname = match after_userinfo.find(':') {
        Some(i) => &after_userinfo[..i],
        None => after_userinfo,
    }
    .trim();

    // `.rstrip(".。．｡")` -- drop trailing root labels, written
    // with any of the four dot characters IDNA treats as label separators.
    hostname.trim_end_matches(['.', '\u{3002}', '\u{ff0e}', '\u{ff61}'])
}

/// Replace the three non-ASCII IDNA label separators with `.`.
///
/// Mirrors the `netloc_with_ascii_dots` computation in
/// `TLDExtract._extract_netloc`. Returns the input untouched (no allocation)
/// when it holds none of them, which is the overwhelmingly common case.
pub fn with_ascii_dots(netloc: &str) -> std::borrow::Cow<'_, str> {
    if netloc.contains(['\u{3002}', '\u{ff0e}', '\u{ff61}']) {
        std::borrow::Cow::Owned(netloc.replace(['\u{3002}', '\u{ff0e}', '\u{ff61}'], "."))
    } else {
        std::borrow::Cow::Borrowed(netloc)
    }
}

/// Whether the string looks like a dotted-quad IPv4 address.
///
/// Mirrors `tldextract.remote.looks_like_ip`: the leading character must be a
/// decimal digit, and the whole string must be four `0`-`255` octets. Written
/// out by hand rather than with a regex to keep the dependency surface small;
/// the octet rules match `IP_RE` exactly, including the rejection of leading
/// zeros (`01` is not a valid octet under that pattern).
pub fn looks_like_ip(maybe_ip: &str) -> bool {
    if !maybe_ip.chars().next().is_some_and(|c| c.is_ascii_digit()) {
        return false;
    }
    let mut octets = 0;
    for part in maybe_ip.split('.') {
        octets += 1;
        if octets > 4 || !is_ipv4_octet(part) {
            return false;
        }
    }
    octets == 4
}

/// One `0`-`255` octet, as `IP_RE` spells it: a bare digit, or two digits
/// starting `1`-`9`, or `1xx`, or `2[0-4]x`, or `25[0-5]`.
fn is_ipv4_octet(part: &str) -> bool {
    let b = part.as_bytes();
    if !b.iter().all(u8::is_ascii_digit) {
        return false;
    }
    match b.len() {
        1 => true,
        2 => b[0] != b'0',
        3 => match b[0] {
            b'1' => true,
            b'2' => b[1] < b'5' || (b[1] == b'5' && b[2] <= b'5'),
            _ => false,
        },
        _ => false,
    }
}

/// Whether the string looks like an IPv6 address.
///
/// Mirrors `tldextract.remote.looks_like_ipv6`, which constructs an
/// `ipaddress.IPv6Address` and catches `AddressValueError`. Rust's
/// `Ipv6Addr` parser has the same acceptance set: it takes the full/compressed
/// forms and IPv4-mapped tails, and rejects prefix lengths and `%zone`
/// suffixes -- the two things `IPv6Address` also rejects.
pub fn looks_like_ipv6(maybe_ip: &str) -> bool {
    maybe_ip.parse::<Ipv6Addr>().is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schemeless_strips_only_real_schemes() {
        assert_eq!(schemeless_url("http://example.com/x"), "example.com/x");
        assert_eq!(schemeless_url("//example.com"), "example.com");
        assert_eq!(schemeless_url("git+ssh://example.com"), "example.com");
        // No colon before the `//`, so it is not a scheme.
        assert_eq!(schemeless_url("example.com//x"), "example.com//x");
        // `_` is not a scheme character.
        assert_eq!(schemeless_url("ht_tp://example.com"), "ht_tp://example.com");
        // Too few characters ahead of `//` for a scheme plus colon.
        assert_eq!(schemeless_url(":://example.com"), ":://example.com");
        assert_eq!(schemeless_url("example.com"), "example.com");
    }

    #[test]
    fn lenient_netloc_matches_python() {
        assert_eq!(lenient_netloc("http://www.example.com/path?q=1#f"), "www.example.com");
        assert_eq!(lenient_netloc("http://user:pw@www.example.com:8080/"), "www.example.com");
        assert_eq!(lenient_netloc("http://[2001:db8::1]:80/x"), "[2001:db8::1]");
        assert_eq!(lenient_netloc("example.com..."), "example.com");
        assert_eq!(lenient_netloc("example.com\u{3002}"), "example.com");
        assert_eq!(lenient_netloc("  example.com  "), "example.com");
        assert_eq!(lenient_netloc(""), "");
        assert_eq!(lenient_netloc("not a url"), "not a url");
    }

    #[test]
    fn ipv4_recognition_matches_ip_re() {
        assert!(looks_like_ip("127.0.0.1"));
        assert!(looks_like_ip("255.255.255.255"));
        assert!(looks_like_ip("0.0.0.0"));
        assert!(!looks_like_ip("256.0.0.1"));
        assert!(!looks_like_ip("01.0.0.1"));
        assert!(!looks_like_ip("1.2.3"));
        assert!(!looks_like_ip("1.2.3.4.5"));
        assert!(!looks_like_ip("a.2.3.4"));
        assert!(!looks_like_ip(".1.2.3"));
        assert!(!looks_like_ip(""));
    }

    #[test]
    fn ipv6_recognition_matches_ipaddress() {
        assert!(looks_like_ipv6("2001:db8::1"));
        assert!(looks_like_ipv6("::ffff:1.2.3.4"));
        assert!(!looks_like_ipv6("2001:db8::1%eth0"));
        assert!(!looks_like_ipv6("2001:db8::1/64"));
        assert!(!looks_like_ipv6("gggg::1"));
        assert!(!looks_like_ipv6(""));
    }
}
