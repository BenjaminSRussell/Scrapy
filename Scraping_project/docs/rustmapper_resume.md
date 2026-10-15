# rustmapper resume data-dir (#937)

`rustmapper resume --data-dir <dir>` continues a crawl from that directory.
Stage 1 does not start a new Scout crawl and does not reseed from the full
snapshot when a resume directory is configured. It validates the artifacts
below and injects only URLs that are new since its own checkpoint.

Configure it with `stage1.discovery.resume_data_dir` or the
`RUSTMAPPER_RESUME_DATA_DIR` environment variable. The orchestrator also
accepts `run_stage1(resume_data_dir=...)`. Null (the shipped default) keeps
the Scout crawl. `write_uconn_urls` stays on for the seed insert.

A missing directory, a failed WAL check, a bad JSONL line, or a corrupt
Stage 1 cursor raises `RustmapperResumeError` and inserts nothing. Stage 1
does not fall back to parsing the site again.

## Artifact layout

| Path | Writer | Stage 1 |
|---|---|---|
| `crawler_state.redb` | rustmapper redb (`nodes`, `hosts`, `metadata` including `last_seqno` and `last_export_ts`) | Presence marks a data-dir. The file is not opened. |
| `wal.log` | rustmapper WAL | Framing and CRC are checked. Payloads stay opaque (rkyv). |
| `wal.offset` | 8-byte little-endian u64 written when the WAL is truncated to a checkpoint | Must be exactly 8 bytes, not past `wal.log`, and on a record boundary. Bytes after it may be a torn tail. |
| `sitemap.jsonl` | Full `SitemapNode` export, rewritten from scratch at the end of a crawl | First handoff parses it once. Later resumes skip it when the file checksum is unchanged, or when `crawl_results_incremental.jsonl` already carried the delta. |
| `crawl_results_incremental.jsonl` | Nodes crawled since rustmapper's last export timestamp. A later export may replace this file with the new batch. | Appended bytes are parsed from the saved offset. A replaced file is parsed and URLs already in the seen-set are not injected again. |
| `sitemap.xml` | Optional XML export | Ignored. XML would be a full re-parse. |
| `logs/` | rustmapper logs | Ignored. |
| `stage1_resume.json` | Stage 1 cursor (not rustmapper) | Byte offset and SHA-256 of the consumed incremental prefix, plus the snapshot checksum. |
| `stage1_resume.seen` | Stage 1 | SHA-256 of each injected URL, so a replaced incremental file does not reseed. |

## WAL frame

Each `wal.log` record is:

```text
[u32 le payload_len][u32 le crc32c][u64 le instance_id][u64 le local_seqno][payload]
```

`crc32c` is CRC-32C (Castagnoli) over the 16 seqno bytes and the payload.
A short read at the end of the file is a torn write and is ignored. A CRC
mismatch, or a torn frame inside the `wal.offset` prefix, fails the resume.

## JSONL lines

A line is either a bare `http://` or `https://` URL, or one JSON object.
The URL is `url`, then `url_normalized`, then `loc`. A finished line that
is not one of those fails the resume. Bytes after the last newline are a
torn tail and stay unparsed until a later resume.

## Checkpoint

Stage 1 writes `stage1_resume.json` only after the new URLs are accepted by
SeedManager (or there were none). A Redis depth throttle does not advance
the cursor, so the next run retries those URLs. Deleting the cursor makes
the next run read the snapshot again; leave the cursor in place.

An unchanged resume does not JSON-parse the consumed incremental prefix or
an unchanged `sitemap.jsonl`.
