# API reference

Generated from the source, so it cannot drift from the code.

Every expression takes a string column of URLs or bare hostnames, plus the same two keywords:

- `include_private` — whether the Public Suffix List's private section counts as suffixes, mirroring `tldextract`'s
  `include_psl_private_domains`. Off by default, as in `tldextract`.
- `parallel` — whether the plugin may split large columns across rayon threads. Columns below 100k rows run
  single-threaded regardless.

## Expressions

`extract` is the only one that reports an absent part as an empty string; the rest use null. See [Usage](usage.md) for
why.

::: polars_tldextract.extract

::: polars_tldextract.fqdn

::: polars_tldextract.registrable_domain

::: polars_tldextract.subdomain

::: polars_tldextract.domain

::: polars_tldextract.suffix

## The `.tld` namespace

Importing `polars_tldextract` registers this, so `pl.col("url").tld.extract()` works without importing anything else.
Each method mirrors the module-level function of the same name.

::: polars_tldextract.TldNamespace
    options:
      members_order: source

## Scalars

For code that is not holding a DataFrame — the same Rust core, no Polars round-trip.

::: polars_tldextract.extract_scalar

::: polars_tldextract.extract_scalar_full

## The suffix list

::: polars_tldextract.psl_version

::: polars_tldextract.load_psl

::: polars_tldextract.refresh_psl

## Deprecated

Removed in 0.3. See [migrating from 0.1](usage.md#migrating-from-01).

::: polars_tldextract.parts

::: polars_tldextract.top_domain
