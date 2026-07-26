{% include-markdown "../README.md" start="<!--usage-start-->" end="<!--usage-end-->" %}

## Migrating from 0.1

`parts()` and `top_domain()` still work in 0.2 but emit a `DeprecationWarning`, and are removed in 0.3.

| 0.1                                          | 0.2                           |
| -------------------------------------------- | ----------------------------- |
| `tld.parts(...).struct.field("full_domain")` | `tld.registrable_domain(...)` |
| `tld.parts(...).struct.field("sld")`         | `tld.domain(...)`             |
| `tld.parts(...).struct.field("tld")`         | `tld.suffix(...)`             |
| `tld.top_domain(...)`                        | `tld.domain(...)`             |

Two things to watch when you migrate:

- **`full_domain` was a misnomer.** It held the *registrable* domain (`bbc.co.uk`). The actual full domain is
  [`fqdn()`][polars_tldextract.fqdn] (`www.bbc.co.uk`), which is new in 0.2.
- **`top_domain()` returned `""` where there was no registrable label; `domain()` returns null.** If you were comparing
  against `""`, compare against null instead — or read the field off `extract()`, which keeps the empty-string form.
