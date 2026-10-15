//! `cargo test` emits the same JSONL bytes the Python contract reads (#936).

use std::fs;
use std::path::PathBuf;

use rustmapper_contract::RustmapperJsonlRecord;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../tests/fixtures/rustmapper_seed")
        .join(name)
}

fn lines(name: &str) -> Vec<String> {
    fs::read_to_string(fixture(name))
        .unwrap_or_else(|err| panic!("read {name}: {err}"))
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(str::to_string)
        .collect()
}

fn accept_records() -> Vec<RustmapperJsonlRecord> {
    vec![
        RustmapperJsonlRecord {
            url: "https://uconn.edu/about".to_string(),
            depth: 0,
            status_code: 200,
            content_length: 1024,
            title: "About UConn".to_string(),
            link_count: 5,
        },
        RustmapperJsonlRecord {
            url: "https://admissions.uconn.edu/apply".to_string(),
            depth: 1,
            status_code: 200,
            content_length: 2048,
            title: "Apply".to_string(),
            link_count: 3,
        },
    ]
}

fn reject_records() -> Vec<RustmapperJsonlRecord> {
    vec![
        RustmapperJsonlRecord {
            url: "ftp://files.uconn.edu/a.pdf".to_string(),
            depth: 0,
            status_code: 200,
            content_length: 10,
            title: "Files".to_string(),
            link_count: 0,
        },
        RustmapperJsonlRecord {
            url: String::new(),
            depth: 1,
            status_code: 200,
            content_length: 0,
            title: String::new(),
            link_count: 0,
        },
        RustmapperJsonlRecord {
            url: "javascript:alert(1)".to_string(),
            depth: 2,
            status_code: 0,
            content_length: 0,
            title: "script".to_string(),
            link_count: 0,
        },
        RustmapperJsonlRecord {
            url: "https://".to_string(),
            depth: 0,
            status_code: 200,
            content_length: 1,
            title: "no host".to_string(),
            link_count: 0,
        },
        RustmapperJsonlRecord {
            url: "https://uconn.edu/a b".to_string(),
            depth: 0,
            status_code: 404,
            content_length: 0,
            title: "space".to_string(),
            link_count: 1,
        },
    ]
}

#[test]
fn cargo_emits_accept_fixture() {
    let emitted: Vec<String> = accept_records()
        .iter()
        .map(|record| record.emit().expect("emit"))
        .collect();
    assert_eq!(emitted, lines("accept.jsonl"));
}

#[test]
fn cargo_emits_reject_fixture() {
    let emitted: Vec<String> = reject_records()
        .iter()
        .map(|record| record.emit().expect("emit"))
        .collect();
    assert_eq!(emitted, lines("reject.jsonl"));
}

#[test]
fn incompatible_field_fails() {
    let rows = lines("incompatible.jsonl");
    assert!(!rows.is_empty());
    for line in rows {
        let parsed = serde_json::from_str::<RustmapperJsonlRecord>(&line);
        assert!(parsed.is_err(), "incompatible field was accepted: {line}");
    }
}
