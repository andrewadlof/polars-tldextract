# polars-tldextract

{% include-markdown "../README.md" start="<!--intro-start-->" end="<!--intro-end-->" %}

{% include-markdown "../README.md" start="<!--install-start-->" end="<!--install-end-->" %}

{% include-markdown "../README.md" start="<!--compat-start-->" end="<!--compat-end-->" %}

## Where to go next

<div class="grid cards" markdown>

- **[Usage](usage.md)** — the six expressions, null semantics, and the `.tld` namespace.
- **[The suffix list](psl.md)** — the vendored snapshot, overriding it, and swapping it in a running process.
- **[Performance](performance.md)** — measured throughput against `map_elements`, and when *not* to reach for this.
- **[Correctness](correctness.md)** — how parity with `tldextract` is proven.
- **[API reference](reference.md)** — every public expression and helper.
- **[Architecture](architecture/overview.md)** — why the `publicsuffix` crate's trie walk lines up with `tldextract`'s.

</div>
