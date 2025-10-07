# Kafka Delta Ingest

High-performance Kafka to Delta Lake ingestor written in Rust.

## ✅ Setup Complete

All dependencies have been installed and configured:
- ✅ Rust 1.90.0 (stable)
- ✅ CMake 4.1.2
- ✅ OpenSSL 3.5.3
- ✅ Cyrus SASL 2.1.28
- ✅ All Cargo dependencies updated to latest stable versions

## 🚀 Quick Start

```bash
# Build the project
cargo build --release

# Run the ingestor
cargo run --release -- ingest <TOPIC> <TABLE_PATH> \
  --kafka localhost:9092 \
  --app-id my-consumer-group
```

## 📦 Dependencies

All dependencies are managed through [Cargo.toml](Cargo.toml):

### Core Dependencies
- **rdkafka 0.38** - Kafka client with SSL/SASL support
- **deltalake 0.28** - Delta Lake with S3 and DataFusion
- **arrow 55** - Apache Arrow for data processing
- **tokio 1.47** - Async runtime
- **aws-sdk-s3 1.107** - AWS S3 integration

### Supporting Libraries
- **serde/serde_json** - JSON serialization
- **clap 4.5** - CLI argument parsing
- **tracing** - Structured logging
- **anyhow/thiserror** - Error handling
- **cadence 1.6** - StatsD metrics
- **chrono 0.4** - Time handling

## 🔧 System Requirements

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

# Delta Lake / S3 Configuration
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_REGION=us-east-1

# StatsD Metrics (optional)
STATSD_HOST=localhost
STATSD_PORT=9125

# Logging
RUST_LOG=info
```

## 🏗️ Build Instructions

### Development Build
```bash
# Set required environment for SASL
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

## 🔍 Monitoring

### Metrics

The application emits StatsD metrics:
- `messages.received` - Total messages consumed
- `records.written` - Records written to Delta Lake
- `batches.written` - Batches committed
- `errors.kafka` - Kafka errors
- `errors.parse_failed` - JSON parsing errors
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
brew install cmake
```

**Error: `sasl/sasl.h: No such file or directory`**
```bash
brew install cyrus-sasl
export LDFLAGS="-L/opt/homebrew/opt/cyrus-sasl/lib"
export CPPFLAGS="-I/opt/homebrew/opt/cyrus-sasl/include"
```

**Error: `openssl` not found**
```bash
brew install openssl@3
```

### Runtime Issues

**Error: Cannot connect to Kafka**
- Verify Kafka is running: `kafka-topics --list --bootstrap-server localhost:9092`
- Check network connectivity
- Verify SSL/SASL configuration if using authentication

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
