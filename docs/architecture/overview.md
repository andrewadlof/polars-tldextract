# Architecture

## Why this exists

Splitting a URL into subdomain / registrable domain / public suffix is not a string operation. `example.co.uk` and
`example.com.foo` look identical to a regex; only the [Public Suffix List](https://publicsuffix.org/) (PSL) knows that
`co.uk` is a suffix and `com.foo` is not. In Python that job belongs to
[`tldextract`](https://github.com/john-kurkowski/tldextract) — but `tldextract` is a Python function, so inside Polars it
has to be driven through `Expr.map_elements`, one interpreter round-trip per row. On a 727k-row frame that dominates the
query.

This package moves the same algorithm into Rust and exposes it as ordinary Polars expressions. The design constraint is
not "be fast" — it is **"be fast and produce byte-identical output to `tldextract`"**, so that a pipeline can swap
implementations without its results moving.

## Layout

| Path | Role |
| --- | --- |
| `src/netloc.rs` | Port of `tldextract/remote.py`: `lenient_netloc`, `looks_like_ip`, `looks_like_ipv6` |
| `src/psl.rs` | Loads the suffix list into two `OnceLock`s (ICANN-only and ICANN+private) |
| `src/extract.rs` | The algorithm: `TLDExtract._extract_netloc` + `_PublicSuffixListTLDExtractor.suffix_index` |
| `src/lib.rs` | Polars `#[polars_expr]` kernels, the rayon fan-out, and the scalar `#[pyfunction]`s |
| `src/data/public_suffix_list.dat` | The vendored list, `include_str!`d into the binary |
| `python/polars_tldextract/` | Thin `register_plugin_function` wrappers and the `.tld` namespace |

The compiled cdylib is two things at once: Polars `dlopen`s it and calls the C-ABI symbols the `#[polars_expr]` macro
emits, and Python imports it as `polars_tldextract._internal` for the scalar helpers. Both routes call
`extract::with_extracted`, so there is exactly one implementation of the algorithm and no way for the two to drift.

## Mapping `tldextract` onto the `publicsuffix` crate

`tldextract` builds its own reversed-label trie and reports the **index of the first suffix label**, or "no suffix". The
[`publicsuffix`](https://docs.rs/publicsuffix) crate walks the same shape of trie but reports the **byte length** of the
matched suffix plus a `Type`. Three facts make them equivalent, and each one is load-bearing:

1. **`Info::typ == None` is `tldextract`'s "no suffix".** The PSL spec says an unmatched TLD gets an implicit `*` rule;
   the crate applies it and returns the last label's length with `typ: None`. `tldextract` deliberately does *not* apply
   it and returns `None` from `suffix_index`. Keying on `typ` rather than on `len` reconciles the two — this is why
   `foo.invalidtld` yields domain `invalidtld` and an empty suffix, not suffix `invalidtld`.

2. **`IcannList` is the ICANN-only trie.** `tldextract`'s default (`include_psl_private_domains=False`) uses a trie
   built without the private section. `publicsuffix::IcannList` builds one trie but rejects private leaves at lookup
   time, so it descends one step further into private branches before stopping. Since a private branch never carries an
   ICANN leaf beneath it, both arrive at the same suffix.

3. **Both spellings of an IDN rule are registered.** With the crate's `punycode` feature, `List::from_str` appends both
   the Unicode form of a rule and its IDNA-ASCII form. That means lowercasing the input host is sufficient for lookup —
   `рф` and `xn--p1ai` both resolve — and the plugin never needs an IDNA round-trip on the hot path. `tldextract` gets
   there from the other direction, punycode-*decoding* input labels before lookup; the results agree.

Wildcard (`*.ck`) and exception (`!www.ck`) rules were traced through both implementations by hand. That is not
sufficient evidence, so `tests/test_parity.py` re-derives a corpus from the list itself and checks every rule
mechanically — see [Parity testing](#parity-testing).

## The algorithm

`extract_netloc` mirrors `TLDExtract._extract_netloc` step for step:

1. Normalize: strip scheme, path, query, fragment, userinfo, and port (`lenient_netloc`), then map the three non-ASCII
   IDNA separators (`。．｡`) to `.`.
2. A bracketed IPv6 literal short-circuits: it becomes the whole domain, brackets included.
3. Lowercase for lookup. Hosts are usually already lowercase, so this borrows rather than allocating; case folding never
   adds or removes a `.`, so the lowered form always has the same label count as the original.
4. Look up the reversed labels in the ICANN or full list.
5. No match (`typ: None`): a bare dotted quad becomes an IP (domain = the address, no suffix); anything else keeps its
   last label as the domain.
6. Match: convert the byte length into a **label count** over the lookup labels, then apply that count to the original
   string. Going through the count rather than the byte offset is what keeps non-ASCII hosts correct, since lowercasing
   can change byte lengths.

Output slices borrow from the normalized netloc rather than being rebuilt, which is why `with_extracted` takes a
callback: the strings the caller sees may borrow from a temporary. On the hot path the Polars string builders copy
straight out of those slices, so a typical parse allocates nothing at all.

Output preserves the input's casing and punycode spelling, exactly like `tldextract` — only the *lookup* is normalized.

## Two null conventions, deliberately

`tldextract` uses empty strings for parts that do not exist. That is faithful, but wrong for a DataFrame: an empty
string is a *value*, so two URLs that both failed to parse would compare equal and produce spurious joins.

So there are two views over the same parse:

- `extract()` — `subdomain` / `domain` / `suffix` / `is_private`, empty strings for absences. Use it when you want
  `tldextract`'s answer verbatim.
- `parts()` — `full_domain` / `sld` / `tld`, **nulls** for absences, and `full_domain` populated only when both halves
  exist. Use it for anything that joins, groups, or compares.

`registrable_domain()` is `parts().struct.field("full_domain")` computed in one pass without materializing the other two
fields.

## Parallelism

Columns of at least `PARALLEL_THRESHOLD` (100k) rows are split into contiguous index ranges, one per rayon thread, and
concatenated back in order. Below that, fan-out costs more than it saves.

The threshold is also deliberately **above the streaming engine's morsel size** (~50k). When Polars is already calling
the plugin from several of its own worker threads, each call stays single-threaded rather than nesting a rayon fan-out
inside a Polars thread and oversubscribing the machine.

The plugin uses rayon's global pool rather than Polars' internal one. Polars moved its pool from `polars_core::POOL` to
`polars_core::runtime::THREAD_POOL` between 0.52 and 0.54; binding to an internal that relocates between releases would
buy a little scheduling fairness at the cost of breaking on every upgrade.

## The suffix list

The list is `include_str!`d into the binary, so there is no network access and no cache directory at runtime — both of
which matter inside a Databricks job. The vendored copy is `tldextract`'s own `.tld_set_snapshot`, so the two agree by
construction.

`POLARS_TLDEXTRACT_PSL` overrides it with a path to a `.dat` file, letting the list be refreshed without a rebuild. It
is read once, on first use, and cached for the life of the process — so it must be set before the first extraction. An
unreadable or unparseable override **panics** rather than falling back: silently parsing a different list than the
operator asked for would produce wrong suffixes with no signal.

`scripts/refresh_psl.py` (`just refresh-psl`) re-vendors the list and checks that the ICANN/private section markers
survived, since the crate keys on those to split the two tries.

## Parity testing

`tests/test_parity.py` asserts `(subdomain, domain, suffix)` equals `tldextract`'s answer, for both settings of
`include_private`, over four corpora:

1. **Hand-written edge cases** — one per branch: schemes, userinfo, ports, IPv4, bracketed IPv6, trailing root labels,
   the three ideographic dots, IDN in both spellings, casing, wildcards, exceptions, and degenerate input.
2. **Every rule in the list** — each of the ~9,750 rules turned into three hosts (the rule itself, one label above, two
   labels above), ~29k cases. Wildcards get a concrete label; exception markers are stripped so the exception path is
   actually taken. This is the check that no hand-written suite can substitute for.
3. **200k randomly assembled URLs** — for combinations nobody thought to write down.
4. **The consuming pipeline's own fixtures** — so the migration downstream is covered by the same corpus that guards it
   there.

Both sides are pointed at the **same vendored `.dat`** (via a `file://` URL and an isolated cache directory), so a
disagreement can only be an algorithm difference, never two different snapshots.

## Compatibility

The plugin FFI ABI is `polars-ffi` `(MAJOR=0, MINOR=1)`, unchanged from py-polars 1.37 through 1.42, so one build spans
the whole `polars>=1.37.1` range. If a future Polars bumps it, the plugin fails loudly at load rather than
miscomputing.

`abi3-py312` means a single wheel covers Python 3.12+. Release wheels target `manylinux2014` (glibc 2.17), well below
any current Databricks runtime.
