//! Polars expression plugin exposing `tldextract`-compatible URL parsing.
//!
//! The compiled cdylib serves two masters:
//!
//! * Polars `dlopen`s it and calls the C-ABI symbols emitted by
//!   `#[polars_expr]`. That is the vectorized path.
//! * Python imports it as `polars_tldextract._internal` for the scalar
//!   helpers, which exist so non-DataFrame callers do not have to round-trip
//!   through a one-row frame.
//!
//! Both go through [`extract::with_extracted`], so there is exactly one
//! implementation of the algorithm.

mod extract;
mod netloc;
mod psl;

use polars::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3_polars::derive::polars_expr;
use rayon::prelude::*;
use serde::Deserialize;

use crate::extract::{with_extracted, with_extracted_in, Extracted};
use crate::psl::Lists;

/// Below this many rows, fanning out costs more than it saves.
///
/// It is also deliberately above the streaming engine's morsel size (~50k), so
/// when Polars is already calling this plugin from several of its own threads
/// each call stays single-threaded instead of nesting a rayon fan-out inside
/// it.
const PARALLEL_THRESHOLD: usize = 100_000;

/// Keyword arguments shared by the expressions, pickled across by Polars.
#[derive(Deserialize)]
struct ExtractKwargs {
    /// Mirrors `tldextract`'s `include_psl_private_domains`.
    include_private: bool,
    /// Whether to fan the column out across rayon threads.
    parallel: bool,
}

// =============================================================================
// Parallel fan-out
// =============================================================================

/// Column-shaped output that can be concatenated back together after a
/// parallel split.
trait Concat: Sized {
    fn concat(&mut self, other: &Self) -> PolarsResult<()>;
}

impl Concat for StringChunked {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.append(other)
    }
}

/// Run `build` over the column, fanning out across rayon threads when the
/// column is large enough to be worth it.
///
/// Order is preserved: each thread owns one contiguous index range and the
/// pieces are concatenated back in order.
fn build_column<T, F>(ca: &StringChunked, parallel: bool, build: F) -> PolarsResult<T>
where
    T: Concat + Send,
    F: Fn(&StringChunked) -> T + Sync,
{
    if !parallel || ca.len() < PARALLEL_THRESHOLD {
        return Ok(build(ca));
    }
    let threads = rayon::current_num_threads().max(1);
    let stride = ca.len().div_ceil(threads);
    let mut pieces: Vec<T> = (0..threads)
        .into_par_iter()
        .filter_map(|i| {
            let offset = i * stride;
            (offset < ca.len()).then(|| {
                let len = stride.min(ca.len() - offset);
                build(&ca.slice(offset as i64, len))
            })
        })
        .collect();

    let mut out = pieces.remove(0);
    for piece in &pieces {
        out.concat(piece)?;
    }
    Ok(out)
}

// =============================================================================
// tld_extract: the faithful tldextract result
// =============================================================================

/// `subdomain` / `domain` / `suffix` / `is_private`, exactly as `tldextract`
/// reports them -- missing parts are empty strings, not nulls.
struct ExtractedColumns {
    subdomain: StringChunked,
    domain: StringChunked,
    suffix: StringChunked,
    is_private: BooleanChunked,
}

impl Concat for ExtractedColumns {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.subdomain.append(&other.subdomain)?;
        self.domain.append(&other.domain)?;
        self.suffix.append(&other.suffix)?;
        self.is_private.append(&other.is_private)?;
        Ok(())
    }
}

impl ExtractedColumns {
    fn into_series(self, name: PlSmallStr) -> PolarsResult<Series> {
        let len = self.subdomain.len();
        let fields = [
            self.subdomain.into_series(),
            self.domain.into_series(),
            self.suffix.into_series(),
            self.is_private.into_series(),
        ];
        Ok(StructChunked::from_series(name, len, fields.iter())?.into_series())
    }
}

fn build_extracted(psl: &Lists, ca: &StringChunked, include_private: bool) -> ExtractedColumns {
    let mut subdomain = StringChunkedBuilder::new("subdomain".into(), ca.len());
    let mut domain = StringChunkedBuilder::new("domain".into(), ca.len());
    let mut suffix = StringChunkedBuilder::new("suffix".into(), ca.len());
    let mut is_private = BooleanChunkedBuilder::new("is_private".into(), ca.len());

    for opt in ca.iter() {
        match opt {
            None => {
                subdomain.append_null();
                domain.append_null();
                suffix.append_null();
                is_private.append_null();
            },
            Some(url) => with_extracted_in(psl, url, include_private, |e| {
                subdomain.append_value(e.subdomain);
                domain.append_value(e.domain);
                suffix.append_value(e.suffix);
                is_private.append_value(e.is_private);
            }),
        }
    }

    ExtractedColumns {
        subdomain: subdomain.finish(),
        domain: domain.finish(),
        suffix: suffix.finish(),
        is_private: is_private.finish(),
    }
}

fn extract_output(_: &[Field]) -> PolarsResult<Field> {
    Ok(Field::new(
        "tld_extract".into(),
        DataType::Struct(vec![
            Field::new("subdomain".into(), DataType::String),
            Field::new("domain".into(), DataType::String),
            Field::new("suffix".into(), DataType::String),
            Field::new("is_private".into(), DataType::Boolean),
        ]),
    ))
}

/// Full parse. Null in, null out; empty strings mark parts that do not exist.
#[polars_expr(output_type_func=extract_output)]
fn tld_extract(inputs: &[Series], kwargs: ExtractKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let psl = psl::current();
    let out =
        build_column(ca, kwargs.parallel, |c| build_extracted(&psl, c, kwargs.include_private))?;
    out.into_series(ca.name().clone())
}

// =============================================================================
// Single-field kernels: one part, with null semantics
// =============================================================================

/// Build a Utf8 column from one field of the parse, absences as **nulls**.
///
/// The null semantics are the point, and the reason these are kernels rather
/// than `struct.field` accessors over [`tld_extract`]. `tldextract` reports an
/// absent part as `""`, which in a DataFrame is a *value*: two URLs that both
/// failed to parse would compare equal and join to each other. `tld_extract`
/// keeps the empty strings because fidelity is its job; everything else here
/// uses nulls.
fn build_field<F>(
    psl: &Lists,
    ca: &StringChunked,
    include_private: bool,
    select: F,
) -> StringChunked
where
    F: for<'a> Fn(&Extracted<'a>) -> &'a str,
{
    let mut b = StringChunkedBuilder::new(ca.name().clone(), ca.len());
    for opt in ca.iter() {
        match opt {
            None => b.append_null(),
            Some(url) => with_extracted_in(psl, url, include_private, |e| {
                let value = select(&e);
                if value.is_empty() {
                    b.append_null();
                } else {
                    b.append_value(value);
                }
            }),
        }
    }
    b.finish()
}

/// The subdomain, e.g. `www`, or null when there is none.
#[polars_expr(output_type=String)]
fn tld_subdomain(inputs: &[Series], kwargs: ExtractKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let psl = psl::current();
    let out = build_column(ca, kwargs.parallel, |c| {
        build_field(&psl, c, kwargs.include_private, |e| e.subdomain)
    })?;
    Ok(out.into_series())
}

/// The registrable label, e.g. `bbc`, or null when there is none.
#[polars_expr(output_type=String)]
fn tld_domain(inputs: &[Series], kwargs: ExtractKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let psl = psl::current();
    let out = build_column(ca, kwargs.parallel, |c| {
        build_field(&psl, c, kwargs.include_private, |e| e.domain)
    })?;
    Ok(out.into_series())
}

/// The public suffix, e.g. `co.uk`, or null when no rule matched.
#[polars_expr(output_type=String)]
fn tld_suffix(inputs: &[Series], kwargs: ExtractKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let psl = psl::current();
    let out = build_column(ca, kwargs.parallel, |c| {
        build_field(&psl, c, kwargs.include_private, |e| e.suffix)
    })?;
    Ok(out.into_series())
}

// =============================================================================
// tld_fqdn: the whole hostname
// =============================================================================

/// Rejoin the parse into the hostname it came from, e.g. `www.bbc.co.uk`.
///
/// Deliberately *not* the same strictness as [`tld_registrable_domain`]: a host
/// with no recognized suffix still has a name, so `127.0.0.1` and `localhost`
/// come back as themselves rather than null. Only an input with no host at all
/// (empty, whitespace) yields null. A bracketed IPv6 literal keeps its
/// brackets, matching what `tld_extract` reports as its domain.
///
/// This is the normalized netloc -- scheme, userinfo, port, path, query, and
/// fragment stripped, trailing root labels dropped, and the three non-ASCII
/// IDNA separators folded to `.` -- rebuilt from the parts, so it agrees with
/// the other expressions by construction.
fn build_fqdn(psl: &Lists, ca: &StringChunked, include_private: bool) -> StringChunked {
    let mut b = StringChunkedBuilder::new(ca.name().clone(), ca.len());
    let mut scratch = String::new();
    for opt in ca.iter() {
        match opt {
            None => b.append_null(),
            Some(url) => with_extracted_in(psl, url, include_private, |e| {
                scratch.clear();
                for part in [e.subdomain, e.domain, e.suffix] {
                    if part.is_empty() {
                        continue;
                    }
                    if !scratch.is_empty() {
                        scratch.push('.');
                    }
                    scratch.push_str(part);
                }
                if scratch.is_empty() {
                    b.append_null();
                } else {
                    b.append_value(&scratch);
                }
            }),
        }
    }
    b.finish()
}

/// `subdomain.domain.suffix`, skipping the parts that are absent.
#[polars_expr(output_type=String)]
fn tld_fqdn(inputs: &[Series], kwargs: ExtractKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let psl = psl::current();
    let out = build_column(ca, kwargs.parallel, |c| build_fqdn(&psl, c, kwargs.include_private))?;
    Ok(out.into_series())
}

// =============================================================================
// tld_registrable_domain: the single-column shortcut
// =============================================================================

fn build_registrable(psl: &Lists, ca: &StringChunked, include_private: bool) -> StringChunked {
    let mut b = StringChunkedBuilder::new(ca.name().clone(), ca.len());
    let mut scratch = String::new();
    for opt in ca.iter() {
        match opt {
            None => b.append_null(),
            Some(url) => with_extracted_in(psl, url, include_private, |e| {
                if e.domain.is_empty() || e.suffix.is_empty() {
                    b.append_null();
                } else {
                    scratch.clear();
                    scratch.push_str(e.domain);
                    scratch.push('.');
                    scratch.push_str(e.suffix);
                    b.append_value(&scratch);
                }
            }),
        }
    }
    b.finish()
}

/// `domain.suffix`, or null when either part is missing.
#[polars_expr(output_type=String)]
fn tld_registrable_domain(inputs: &[Series], kwargs: ExtractKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let psl = psl::current();
    let out =
        build_column(ca, kwargs.parallel, |c| build_registrable(&psl, c, kwargs.include_private))?;
    Ok(out.into_series())
}

// =============================================================================
// Scalar Python API
// =============================================================================

/// Parse one URL, for callers that are not holding a DataFrame.
///
/// Returns `(registrable_domain, domain, suffix)` with the same null semantics
/// as the single-field kernels, so scalar and vectorized paths agree by
/// construction.
#[pyfunction]
#[pyo3(signature = (url, *, include_private = false))]
fn extract_scalar(
    url: Option<&str>,
    include_private: bool,
) -> (Option<String>, Option<String>, Option<String>) {
    let Some(url) = url else {
        return (None, None, None);
    };
    with_extracted(url, include_private, |e| {
        let sld = (!e.domain.is_empty()).then(|| e.domain.to_owned());
        let tld = (!e.suffix.is_empty()).then(|| e.suffix.to_owned());
        let full = match (&sld, &tld) {
            (Some(d), Some(s)) => Some(format!("{d}.{s}")),
            _ => None,
        };
        (full, sld, tld)
    })
}

/// Parse one URL into `(subdomain, domain, suffix, is_private)`, the faithful
/// `tldextract` result with empty strings for absent parts.
#[pyfunction]
#[pyo3(signature = (url, *, include_private = false))]
fn extract_scalar_full(url: &str, include_private: bool) -> (String, String, String, bool) {
    with_extracted(url, include_private, |e| {
        (e.subdomain.to_owned(), e.domain.to_owned(), e.suffix.to_owned(), e.is_private)
    })
}

/// The `VERSION:` stamp of the Public Suffix List actually in use.
#[pyfunction]
fn psl_version() -> String {
    psl::psl_version()
}

/// Replace the live Public Suffix List with one parsed from `text`.
///
/// Returns the new list's `VERSION:` stamp. Raises `ValueError` if the text is
/// not a usable list, in which case the process keeps the list it already had.
#[pyfunction]
fn load_psl_text(text: &str) -> PyResult<String> {
    psl::load_from_text(text).map_err(PyValueError::new_err)
}

/// Replace the live Public Suffix List with one read from `path`.
///
/// Returns the new list's `VERSION:` stamp. Raises `ValueError` if the file
/// cannot be read or is not a usable list, in which case the process keeps the
/// list it already had.
#[pyfunction]
fn load_psl_path(path: &str) -> PyResult<String> {
    psl::load_from_path(path).map_err(PyValueError::new_err)
}

#[pymodule]
fn _internal(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_scalar, m)?)?;
    m.add_function(wrap_pyfunction!(extract_scalar_full, m)?)?;
    m.add_function(wrap_pyfunction!(psl_version, m)?)?;
    m.add_function(wrap_pyfunction!(load_psl_text, m)?)?;
    m.add_function(wrap_pyfunction!(load_psl_path, m)?)?;
    m.add("PSL_PATH_ENV", psl::PSL_PATH_ENV)?;
    Ok(())
}
