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
use pyo3::prelude::*;
use pyo3_polars::derive::polars_expr;
use rayon::prelude::*;
use serde::Deserialize;

use crate::extract::with_extracted;

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
struct Extracted {
    subdomain: StringChunked,
    domain: StringChunked,
    suffix: StringChunked,
    is_private: BooleanChunked,
}

impl Concat for Extracted {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.subdomain.append(&other.subdomain)?;
        self.domain.append(&other.domain)?;
        self.suffix.append(&other.suffix)?;
        self.is_private.append(&other.is_private)?;
        Ok(())
    }
}

impl Extracted {
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

fn build_extracted(ca: &StringChunked, include_private: bool) -> Extracted {
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
            Some(url) => with_extracted(url, include_private, |e| {
                subdomain.append_value(e.subdomain);
                domain.append_value(e.domain);
                suffix.append_value(e.suffix);
                is_private.append_value(e.is_private);
            }),
        }
    }

    Extracted {
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
    let out = build_column(ca, kwargs.parallel, |c| build_extracted(c, kwargs.include_private))?;
    out.into_series(ca.name().clone())
}

// =============================================================================
// tld_parts: the null-semantics view most pipelines want
// =============================================================================

/// `full_domain` / `sld` / `tld`, with absent parts as nulls rather than empty
/// strings, and `full_domain` populated only when both halves are present.
///
/// This is the shape downstream joins and comparisons need: an empty string is
/// a value that can match another empty string, which would produce spurious
/// equality between two URLs that both failed to parse.
struct Parts {
    full_domain: StringChunked,
    sld: StringChunked,
    tld: StringChunked,
}

impl Concat for Parts {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.full_domain.append(&other.full_domain)?;
        self.sld.append(&other.sld)?;
        self.tld.append(&other.tld)?;
        Ok(())
    }
}

impl Parts {
    fn into_series(self, name: PlSmallStr) -> PolarsResult<Series> {
        let len = self.full_domain.len();
        let fields =
            [self.full_domain.into_series(), self.sld.into_series(), self.tld.into_series()];
        Ok(StructChunked::from_series(name, len, fields.iter())?.into_series())
    }
}

fn build_parts(ca: &StringChunked, include_private: bool) -> Parts {
    let mut full_domain = StringChunkedBuilder::new("full_domain".into(), ca.len());
    let mut sld = StringChunkedBuilder::new("sld".into(), ca.len());
    let mut tld = StringChunkedBuilder::new("tld".into(), ca.len());
    let mut scratch = String::new();

    for opt in ca.iter() {
        match opt {
            None => {
                full_domain.append_null();
                sld.append_null();
                tld.append_null();
            },
            Some(url) => with_extracted(url, include_private, |e| {
                if e.domain.is_empty() {
                    sld.append_null();
                } else {
                    sld.append_value(e.domain);
                }
                if e.suffix.is_empty() {
                    tld.append_null();
                } else {
                    tld.append_value(e.suffix);
                }
                if e.domain.is_empty() || e.suffix.is_empty() {
                    full_domain.append_null();
                } else {
                    scratch.clear();
                    scratch.push_str(e.domain);
                    scratch.push('.');
                    scratch.push_str(e.suffix);
                    full_domain.append_value(&scratch);
                }
            }),
        }
    }

    Parts { full_domain: full_domain.finish(), sld: sld.finish(), tld: tld.finish() }
}

fn parts_output(_: &[Field]) -> PolarsResult<Field> {
    Ok(Field::new(
        "tld_parts".into(),
        DataType::Struct(vec![
            Field::new("full_domain".into(), DataType::String),
            Field::new("sld".into(), DataType::String),
            Field::new("tld".into(), DataType::String),
        ]),
    ))
}

#[polars_expr(output_type_func=parts_output)]
fn tld_parts(inputs: &[Series], kwargs: ExtractKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let out = build_column(ca, kwargs.parallel, |c| build_parts(c, kwargs.include_private))?;
    out.into_series(ca.name().clone())
}

// =============================================================================
// tld_registrable_domain: the single-column shortcut
// =============================================================================

fn build_registrable(ca: &StringChunked, include_private: bool) -> StringChunked {
    let mut b = StringChunkedBuilder::new(ca.name().clone(), ca.len());
    let mut scratch = String::new();
    for opt in ca.iter() {
        match opt {
            None => b.append_null(),
            Some(url) => with_extracted(url, include_private, |e| {
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
    let out = build_column(ca, kwargs.parallel, |c| build_registrable(c, kwargs.include_private))?;
    Ok(out.into_series())
}

// =============================================================================
// Scalar Python API
// =============================================================================

/// Parse one URL, for callers that are not holding a DataFrame.
///
/// Returns `(full_domain, sld, tld)` with the same null semantics as
/// [`tld_parts`], so scalar and vectorized paths agree by construction.
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

#[pymodule]
fn _internal(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_scalar, m)?)?;
    m.add_function(wrap_pyfunction!(extract_scalar_full, m)?)?;
    m.add_function(wrap_pyfunction!(psl_version, m)?)?;
    m.add("PSL_PATH_ENV", psl::PSL_PATH_ENV)?;
    Ok(())
}
