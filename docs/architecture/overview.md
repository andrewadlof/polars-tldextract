# Architecture

## Why this exists

Splitting a URL into subdomain / registrable domain / public suffix is not a string operation. `www.bbc.co.uk` and
`blog.cloudflare.com` look identical to a regex; only the [Public Suffix List](https://publicsuffix.org/) (PSL) knows
that the registrable domain is `bbc.co.uk` in one and `cloudflare.com` in the other. In Python that job belongs to
[`tldextract`](https://github.com/john-kurkowski/tldextract) — but `tldextract` is a Python function, so inside Polars
it has to be driven through `Expr.map_elements`, one interpreter round-trip per row. On a 727k-row frame that dominates
the query.

This package moves the same algorithm into Rust and exposes it as ordinary Polars expressions. The design constraint is
not "be fast" — it is **"be fast and produce byte-identical output to `tldextract`"**, so that a pipeline can swap
implementations without its results moving.

## Layout

| Path                              | Role                                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| `src/netloc.rs`                   | Port of `tldextract/remote.py`: `lenient_netloc`, `looks_like_ip`, `looks_like_ipv6`       |
| `src/psl.rs`                      | Loads the suffix list into two `OnceLock`s (ICANN-only and ICANN+private)                  |
| `src/extract.rs`                  | The algorithm: `TLDExtract._extract_netloc` + `_PublicSuffixListTLDExtractor.suffix_index` |
| `src/lib.rs`                      | Polars `#[polars_expr]` kernels, the rayon fan-out, and the scalar `#[pyfunction]`s        |
| `src/data/public_suffix_list.dat` | The vendored list, `include_str!`d into the binary                                         |
| `python/polars_tldextract/`       | Thin `register_plugin_function` wrappers and the `.tld` namespace                          |

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
   `printer.local` yields domain `local` and an empty suffix, not suffix `local`.

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

## One struct, one vocabulary

`tldextract` uses empty strings for parts that do not exist. That is faithful, but wrong for a DataFrame: an empty
string is a *value*, so two URLs that both failed to parse would compare equal and produce spurious joins.

The split is drawn along fidelity rather than along shape. `extract()` is the one expression that reproduces
`tldextract.ExtractResult` verbatim — `subdomain` / `domain` / `suffix` / `is_private`, empty strings and all — because
being byte-identical is its entire job. Every other expression yields a single Utf8 column with **nulls** for absences,
which is what a join, a group-by, or a comparison needs.

Each single-field expression is its own kernel (`build_field` in `src/lib.rs`, plus `build_fqdn` and
`build_registrable`) rather than a `struct.field` accessor over `extract()`. That is what lets them emit nulls at all —
the empty string never exists to be converted — and it means asking for one part does not materialize the other three.

Through 0.1 there was a second struct, `parts()`, with the fields `full_domain` / `sld` / `tld`. It carried the null
semantics, but at the cost of a second name for every concept — `sld` and `domain` for the same label, `tld` and
`suffix` for the same suffix — and `full_domain` was a misnomer: it held the *registrable* domain (`bbc.co.uk`), not the
full one (`www.bbc.co.uk`). Its three fields are now `registrable_domain()`, `domain()`, and `suffix()`, and the name it
wanted belongs to `fqdn()`. It is deprecated in 0.2 and removed in 0.3, alongside `top_domain()` → `domain()`; both
shims live in `python/polars_tldextract/_deprecated.py`, so the removal is a file deletion.

### `fqdn()` vs. `registrable_domain()`

The two disagree on hosts with no recognized suffix, and the disagreement is the point:

- `registrable_domain()` is **strict** — `domain.suffix`, or null if either half is missing. An IP, a `.local` name, or
  a bare suffix has nothing you could register, so it yields null rather than a half-formed string.
- `fqdn()` is **lax** — a host with no suffix still has a name, so `127.0.0.1` and `localhost` come back as themselves.
  Only input with no host at all is null.

`fqdn()` rebuilds the hostname from the parse rather than reading the netloc directly, so it agrees with the other
expressions by construction: it is exactly the non-empty `subdomain` / `domain` / `suffix` joined by dots. A bracketed
IPv6 literal therefore keeps its brackets, matching what `extract()` reports as its domain.

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

`POLARS_TLDEXTRACT_PSL` overrides it with a path to a `.dat` file, resolved on first use. An unreadable or unparseable
override **panics** rather than falling back: silently parsing a different list than the operator asked for would
produce wrong suffixes with no signal, and a lazily-initialized static has no caller to return an error to.

`scripts/refresh_psl.py` (`just refresh-psl`) re-vendors the list and checks that the ICANN/private section markers
survived, since the crate keys on those to split the two tries.

### Swapping the list at runtime

The list changes several times a week, and a process that has already parsed one URL would otherwise be stuck with
whatever it read first — untenable for a cluster that stays up for days. `load_psl()` and `refresh_psl()` replace it in
place, so `psl::Lists` (both tries plus the version stamp) lives behind an `RwLock<Arc<Lists>>` rather than a
`OnceLock`.

Two properties make that safe without costing throughput:

- **Readers take a snapshot, not the lock.** Each expression evaluation calls `psl::current()` once, clones the `Arc`,
  and shares it across the whole rayon fan-out. The per-row path never touches the lock — it holds a plain `&Lists` — so
  the swap capability is free on the hot path. This is why `with_extracted_in` takes the list as a parameter and
  `with_extracted` is the thin wrapper that fetches it.
- **A query is never parsed against two lists.** Because the snapshot is taken once and held, a swap that lands
  mid-query affects only the *next* one. Without that, a 200k-row column could have its first half parsed under one list
  and its second half under another — a divergence that would be nearly impossible to diagnose from the output.

Loading parses into a complete `Lists` *before* swapping, so a rejected list leaves the process running on the one it
already had. Rejection covers a missing section marker, a parse failure, and a list with no rules; the marker check is
the important one, since a list without markers parses as one undifferentiated section and every private suffix would
silently be treated as ICANN.

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
the whole `polars>=1.37.1` range. If a future Polars bumps it, the plugin fails loudly at load rather than miscomputing.

`abi3-py312` means a single wheel covers Python 3.12+. Release wheels target `manylinux2014` (glibc 2.17), well below
any current Databricks runtime.
