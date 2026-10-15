# rustmapper JSONL ↔ SeedManager fixtures

Checked-in shapes for the public rustmapper `sitemap.jsonl` line
(`url`, `depth`, `status_code`, `content_length`, `title`, `link_count`).
Rust-sitemap documents that line in its README. SeedManager does not read
the file itself; `src/stage1/rustmapper_contract.py` checks the shape, then
`SeedManager.add_urls_to_seeds` accepts or rejects `url` with
`source_spider=rustmapper`.

| File | What both sides do |
|---|---|
| [accept.jsonl](accept.jsonl) | Schema-valid http(s) URLs. SeedManager inserts `seed_urls` rows. `cargo test` emits these exact bytes. |
| [reject.jsonl](reject.jsonl) | Same shape, but the URL is not crawlable (bad scheme, empty, no host, whitespace). SeedManager rejects every line and writes nothing. |
| [incompatible.jsonl](incompatible.jsonl) | Unknown key, missing `url`, or a wrong JSON type. Pytest and `cargo test` both fail the line. It never reaches SeedManager. |

`cargo test` lives in `Scraping_project/rustmapper_contract/`. The pytest is
`tests/contract/test_rustmapper_seed_contract.py`. CI runs both in
`.github/workflows/ci-rustmapper-contract.yml`; the pytest also runs in the
default suite.
