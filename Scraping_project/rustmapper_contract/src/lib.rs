//! Six-field rustmapper JSONL record shared with SeedManager (#936).
//!
//! Field order is the on-disk key order of
//! `tests/fixtures/rustmapper_seed/`. An unknown field or a wrong JSON type
//! fails `serde` (`deny_unknown_fields`). This crate does not crawl.

use serde::{Deserialize, Serialize};

/// One public `sitemap.jsonl` line: `url`, `depth`, `status_code`,
/// `content_length`, `title`, `link_count`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RustmapperJsonlRecord {
    pub url: String,
    pub depth: u32,
    pub status_code: u16,
    pub content_length: u64,
    pub title: String,
    pub link_count: u32,
}

impl RustmapperJsonlRecord {
    /// Compact JSON object. Key order matches the struct and the fixtures.
    pub fn emit(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string(self)
    }
}
