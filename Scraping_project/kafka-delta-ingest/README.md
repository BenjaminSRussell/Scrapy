# Kafka Delta Ingest

High-performance Kafka to Delta Lake ingestor written in Rust.

## Prerequisites

A fresh clone has **nothing installed**. Before the first `cargo build` you need:

| Tool | Minimum | Why |
|---|---|---|
| Rust toolchain (via rustup) | 1.90 (`rust-version` in Cargo.toml) | compiler |
| C/C++ compiler + make | any recent | `rdkafka` builds librdkafka from source (`cmake-build`) |
| CMake | 3.x+ | same |
| pkg-config | any | locating OpenSSL / SASL |
| OpenSSL dev headers | 3.x | `rdkafka` `ssl` feature |
| Cyrus SASL dev headers | 2.1 | `rdkafka` `sasl` feature |

Platform install commands are under [System Requirements](#-system-requirements).

Tested with (known-good combinations, not something already on your machine):
- macOS (Apple Silicon): Rust 1.90.0, CMake 4.1.2, OpenSSL 3.5.3, Cyrus SASL 2.1.28 (Homebrew)
- Debian 13: Rust 1.99.0, CMake 4.4.4, OpenSSL 3.5.7, Cyrus SASL 2.1.28 (`cargo build` + `cargo test` pass)

## 🚀 Quick Start

After installing the [prerequisites](#prerequisites):

```bash
# Build the project (first build compiles librdkafka and takes several minutes)
cargo build --release

# Run the ingestor
cargo run --release -- ingest <TOPIC> <TABLE_PATH> \
  --kafka localhost:9092 \
  --app-id my-consumer-group
```

## 📦 Dependencies

All dependencies are managed through [Cargo.toml](Cargo.toml):

### Core Dependencies
- **rdkafka 0.39** - Kafka client (features: `cmake-build`, `ssl`, `sasl`, `zstd`)
- **deltalake 0.29** - Delta Lake (feature: `datafusion`; local filesystem tables, see [S3](#s3-table-paths))
- **arrow 56** - Apache Arrow for data processing
- **tokio 1.47** - Async runtime
- **redis 1.2** - URL dedup / state

[Cargo.toml](Cargo.toml) is the source of truth; versions above may lag it.

### Supporting Libraries
- **serde/serde_json** - JSON serialization
- **clap 4.5** - CLI argument parsing
- **tracing** - Structured logging
- **anyhow/thiserror** - Error handling
- **cadence 1.6** - StatsD metrics
- **chrono 0.4** - Time handling

## 🔧 System Requirements

### Debian / Ubuntu
```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config libssl-dev libsasl2-dev

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable
```

### macOS (Apple Silicon)
```bash
# Install Homebrew if not present
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install system dependencies
brew install cmake cyrus-sasl openssl@3

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable
```

### Environment Variables

Create a `.env` file with required variables:

```bash
# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS=localhost:9092

# Delta Lake / S3 Configuration (only relevant once S3 support is enabled, see below)
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_REGION=us-east-1

# StatsD Metrics (optional)
STATSD_HOST=localhost
STATSD_PORT=9125

# Logging
RUST_LOG=info
```

### S3 table paths

The `deltalake` dependency is built **without** its `s3` feature (no `deltalake-aws`
in Cargo.lock), so this build writes to **local filesystem** table paths only. An
`s3://bucket/path` table path fails at startup until the `s3` feature is enabled
and the AWS handlers are registered in `main.rs`.

## 🏗️ Build Instructions

### Development Build
```bash
# macOS only: point the build at Homebrew's SASL (not needed on Debian/Ubuntu)
export LDFLAGS="-L/opt/homebrew/opt/cyrus-sasl/lib"
export CPPFLAGS="-I/opt/homebrew/opt/cyrus-sasl/include"
export PKG_CONFIG_PATH="/opt/homebrew/opt/cyrus-sasl/lib/pkgconfig"

# Build
cargo build
```

### Release Build (Optimized)
```bash
cargo build --release
```

The release build includes:
- LTO (Link Time Optimization)
- Maximum optimization level (opt-level=3)
- Single codegen unit for best performance

## 📖 Usage

### Basic Usage
```bash
kafka-delta-ingest ingest <TOPIC> <TABLE_PATH>
```

### Running the Ingestor

```bash
# Example: Ingest from 'scraped-items' topic to a local Delta table
kafka-delta-ingest ingest scraped-items /app/data/delta_lake/scraped_items \
  --kafka kafka:9092 \
  --app-id scrapy-ingestor \
  --allowed-latency 60
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--kafka` | Kafka bootstrap servers | localhost:9092 |
| `--app-id` | Consumer group ID | kafka-delta-ingest |
| `--auto-offset-reset` | Offset reset strategy | earliest |
| `--allowed-latency` | Max seconds before forcing batch write | 300 |
| `--max-messages-per-batch` | Max messages per batch | 1000 |
| `--max-write-attempts` | Delta write attempts per batch (backoff 1s, 2s, 4s … max 30s) before exiting without committing | 5 |

### Delivery semantics: at-least-once (#282)

- **Manual offset commits.** Auto-commit is **off**. The ingestor records the next offset per partition for every consumed message (including invalid messages it deliberately drops). It commits those offsets synchronously **only after** the batch has been committed to Delta (`flush_and_commit`).
- **Failed writes are retried with backoff.** After `--max-write-attempts` failures the process exits non-zero **without committing**. On restart (or rebalance) the uncommitted messages are consumed again, so a failed lake write can never skip messages.
- **Offset commit failure after a successful Delta write** is logged (`errors.offset_commit_failed`) and retried with the next batch.
- **Duplicates, not loss.** A crash between the Delta commit and the offset commit re-delivers that batch. Downstream readers should dedupe on `url` + `scraped_at_utc` if they need exactly-once views.

## 📊 Schema

The default schema for scraped data:

```rust
{
    url: String (required)
    title: String (optional)
    content: String (optional)
    scraped_at_utc: String (required)
    spider_name: String (required)
    pipeline_version: String (optional)
}
```

**Required fields are rejected, never defaulted (#531).** A message whose `url`, `scraped_at_utc` or `spider_name` is missing, null, empty or not a string is dropped and counted (`errors.schema_validation_failed`, or `errors.missing_required_field` from the typed row check). It is never written with an empty string. Field-name drift such as `spider` instead of `spider_name` is rejected the same way. The list lives in `REQUIRED_INGEST_FIELDS` in `src/main.rs` and in `src/core/ingest_contract.py` on the Python side. `tests/unit/test_ingest_field_contract.py` fails if the two disagree or if the producer's output stops satisfying them.

## 🔍 Monitoring

### Metrics

The application emits StatsD metrics:
- `messages.received` - Total messages consumed
- `records.written` - Records written to Delta Lake
- `batches.written` - Batches committed
- `errors.kafka` - Kafka errors
- `errors.parse_failed` - JSON parsing errors
- `errors.schema_validation_failed` - Messages failing the JSON schema (dropped)
- `errors.missing_required_field` - Messages rejected for a missing/empty required field (#531)
- `errors.write_failed` - Delta Lake write errors

### Logging

Set `RUST_LOG` environment variable:
```bash
RUST_LOG=debug cargo run    # Verbose logging
RUST_LOG=info cargo run     # Normal logging (default)
RUST_LOG=warn cargo run     # Warnings only
```

## 🐛 Troubleshooting

### Build Issues

**Error: `cmake: command not found`**
```bash
brew install cmake                  # macOS
sudo apt-get install -y cmake       # Debian/Ubuntu
```

**Error: `sasl/sasl.h: No such file or directory`**
```bash
sudo apt-get install -y libsasl2-dev   # Debian/Ubuntu
# macOS:
brew install cyrus-sasl
export LDFLAGS="-L/opt/homebrew/opt/cyrus-sasl/lib"
export CPPFLAGS="-I/opt/homebrew/opt/cyrus-sasl/include"
```

**Error: `openssl` not found**
```bash
brew install openssl@3                          # macOS
sudo apt-get install -y libssl-dev pkg-config   # Debian/Ubuntu
```

### Runtime Issues

**Error: Cannot connect to Kafka**
- Verify Kafka is running: `kafka-topics --list --bootstrap-server localhost:9092`
- Check network connectivity
- Verify SSL/SASL configuration if using authentication

**Error: S3 table path fails / unknown scheme `s3`**
- Expected with the current build; see [S3 table paths](#s3-table-paths).

**Error: S3 access denied**
- Verify AWS credentials in `.env`
- Check S3 bucket permissions
- Ensure IAM role has required permissions

## 🔐 Security Best Practices

1. **Never commit `.env` files** - Already in `.gitignore`
2. **Use IAM roles** when running in AWS (no hardcoded credentials)
3. **Enable SSL/TLS** for Kafka connections in production
4. **Rotate credentials** regularly
5. **Use secret management** (AWS Secrets Manager, HashiCorp Vault)

## 📁 Project Structure

```
kafka-delta-ingest/
├── Cargo.toml          # Rust dependencies (master dependency file)
├── src/
│   └── main.rs         # Main application code
├── .env.example        # Example environment variables
├── .gitignore          # Comprehensive gitignore
└── README.md           # This file
```

## 🚀 Performance

Release build optimizations:
- **Binary size**: ~234MB (debug), ~50MB (release)
- **Throughput**: 10,000+ messages/sec (depends on network/disk)
- **Memory**: ~100-500MB (depends on batch size)
- **CPU**: Multi-threaded with Tokio async runtime

## 📝 License

[Add your license here]

## 🤝 Contributing

[Add contribution guidelines here]

## 📞 Support

For issues or questions:
- GitHub Issues: [Link to issues]
- Documentation: [Link to docs]
